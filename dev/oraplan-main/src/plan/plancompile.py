from typing import List, NewType, Dict, Any, Callable, Tuple
from plan.planbase import ActionSpec, PlanAction, PlanStatistics,\
    PlanStateContext
import logging
import threading
import time, os
from baseproc.ltm import StateNotification
from plan.planner import TaskPlan, PlannerFactory, CustomProblem
from baseproc.basetask import TaskRunContext, StateContext, InvokeSubtaskFactory,\
    SubtaskBuildData
from common.util import UniqueIdGenerator
from baseproc.levelmgr import PlannerTaskDefinition, TaskDefinition
import shutil
from baseproc.proccontext import ProcManage
from threading import Condition

SystemContext = NewType("SystemContext", None)
EventQueue = NewType("EventQueue", None)

LOGGER_BASE = "oraplan.plan"
LOG_TAG = {"lgmod": "ORAPLAN"}
LOG_LINE = "\n\t"
logger = logging.getLogger(LOGGER_BASE)

class PlanRunContext(TaskRunContext):
    ACTION_TAG = "{}-{} {}"
    PLANRUN_TAG = "{}-{}"
    
    ACTIONRESULT_NONE = 0
    ACTIONRESULT_COMPLETE = 1
    ACTIONRESULT_WAIT = 2
    ACTIONRESULT_WAITCHILD = 3
    ACTIONRESULT_WAITCOMPLETE = 4
    ACTIONRESULT_ERROR = 5
    
    PLANST_INIT = 0
    PLANST_ACTIVE = 1
    PLANST_WAIT = 2
    PLANST_COMPLETE = 3
    
    def __init__(self, task_ns: str, task_id: str, parent_cxt: "TaskRunContext" = None):
        super().__init__(task_ns, task_id, parent_cxt)
        
        self.plan_spec = None
        self.lifecycle = self.PLANST_INIT
        self.action_index: int = -1
        self.last_result: int = self.ACTIONRESULT_NONE
        self.runner: PlanRunner = None
        self.task_lock = threading.Lock()
        self.task_process_id = 0
        self.task_dispatch_id = 0
        
    def to_action_tag(self, action: "ActionProcess", tag = "PLAN"):
        super().to_action_tag(action, tag)
        
    def to_action_purpose_tag(self, action: "ActionProcess", tag = "PLAN", purpose: str = None):
        ret = self.to_action_tag(action, tag)
        if purpose is not None:
            return ret + " " + purpose
        return ret
    
    def to_planrun_tag(self, tag = "PLANRUN"):
        return PlanRunContext.PLANRUN_TAG.format(tag, self.runcxt_id)
        
    def get_taskid(self):
        task_id = None
        if self.plan_spec is not None and self.plan_spec.task_definition is not None:
            task_id = self.plan_spec.task_definition.task_id
        return task_id
    
    def get_current_action(self):
        return self.plan_spec.get_action(self.plan_cxt.action_plan_specindex) if self.plan_spec is not None else None
        
    def __repr__(self):
        return "{}: {}@{}".format(id(self), self.get_taskid(), self.runcxt_id)
    
class ActionProcess(PlanAction):
    def __init__(self, action_name: str, arg_list: List[str], action_spec: Any):
        super().__init__(action_name, arg_list)
        self.action_spec: Any = action_spec
        self.check_notify: bool = False
        self.check_receive: bool = False
        self.work_callable: Callable = None
        
    def configure(self, check_receive = False, check_notify = False):
        self.check_notify = check_notify
        self.check_receive = check_receive
        
    def bind_action_worker(self, worker: Callable[["ActionProcess", PlanRunContext], None]) -> None:
        self.work_callable = worker
        
    def process(self, plan_cxt: PlanRunContext) -> None:
        if self.check_receive and self._await_event(plan_cxt):
            return
            
        if not self.is_ready(plan_cxt):
            plan_cxt.last_result = PlanRunContext.ACTIONRESULT_WAIT
            return
        try:
            self.do_work(plan_cxt)
        except:
            if logger.isEnabledFor(logging.ERROR):
                logger.error("{}: Work processing failure".format(plan_cxt.to_action_tag(self)), exc_info=True)
            plan_cxt.last_result = PlanRunContext.ACTIONRESULT_ERROR
            return
            
        self.propagate_effects(plan_cxt)
        plan_cxt.last_result = PlanRunContext.ACTIONRESULT_COMPLETE
       
    def do_work(self, plan_cxt: PlanRunContext):
        if self.work_callable is None:
            print("{} {}".format(format(plan_cxt.to_action_tag(self)), self.print_args()))
        else:
            self.work_callable(self, plan_cxt)
    
    def is_ready(self, plan_cxt: PlanRunContext) -> bool:
        return plan_cxt.plan_spec.action_processor.is_ready(self.arg_list, self.action_spec, plan_cxt.state_cxt)
        
    def propagate_effects(self, plan_cxt: PlanRunContext) -> None:
        plan_cxt.plan_spec.action_processor.propagate_effects(self.arg_list, self.action_spec, plan_cxt.state_cxt)
        self._notify_state(plan_cxt)
                
    def _notify_state(self, plan_cxt: PlanRunContext) -> None:
        if not self.check_notify:
            return
        
        ltm = plan_cxt.plan_spec.task_definition.ltm
        notify_spec = ltm.match_notify_state(self, plan_cxt)
        if notify_spec is not None:
            notification = StateNotification(notify_spec.sync_set, notify_spec.sync_state,
                notify_spec.sync_value, time.time_ns(), plan_cxt.runcxt_id)
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug("{} Notifying event {}.{}={}".format(plan_cxt.to_action_tag(self), 
                    notify_spec.sync_set, notify_spec.sync_state, notify_spec.sync_value))
            plan_cxt.sys_cxt.sync_state_manager.notify_sync_state(notification)
            
    def await_state(self, plan_cxt):
        ltm = plan_cxt.plan_spec.task_definition.ltm
        await_state_spec = ltm.match_await_state_spec(self, plan_cxt)
        if await_state_spec is not None:
            sync_value = plan_cxt.sys_cxt.sync_state_manager.query_synch_state(await_state_spec.sync_set,
               await_state_spec.sync_state, plan_cxt.runner, await_state_spec.sync_at)
            if sync_value is None:
                if logger.isEnabledFor(logging.DEBUG):
                    logger.debug("{}: Awaiting sync-state {}.{}".format(plan_cxt.to_action_tag(self),
                        await_state_spec.sync_set, await_state_spec.sync_state))
                plan_cxt.last_result = PlanRunContext.ACTIONRESULT_WAIT
                return True
            else:
                if logger.isEnabledFor(logging.DEBUG):
                    logger.debug("{}: Received sync-state {}.{}={}".format(plan_cxt.to_action_tag(self),
                        await_state_spec.sync_set, await_state_spec.sync_state, sync_value))
        return False
    
    def print_args(self):
        return  " ".join(map(str, self.arg_list))
    
    def __repr__(self):
        args = " ".join(map(str, self.arg_list))
        ret = f"{self.action_name} {args}"
        return ret
    
class NestedActionProcess(ActionProcess):
    def __init__(self, action_name: str, arg_list: List[str], action_spec: ActionSpec):
        super().__init__(action_name, arg_list, action_spec)
    
    def process(self, plan_cxt: PlanRunContext) -> None:
        if plan_cxt.last_result == PlanRunContext.ACTIONRESULT_NONE:
            result = plan_cxt.sys_cxt.task_runner.invoke_subtask(plan_cxt, self, wait_completion=False)
            if not result[1]:
                plan_cxt.last_result = PlanRunContext.ACTIONRESULT_WAITCHILD
                return
            else:
                self.propagate_effects(plan_cxt)
                plan_cxt.last_result = PlanRunContext.ACTIONRESULT_COMPLETE
                print("{} {}".format(format(plan_cxt.to_action_tag(self)), self.print_args()))
                return
        elif plan_cxt.last_result == PlanRunContext.ACTIONRESULT_WAITCOMPLETE:
            self.propagate_effects(plan_cxt)
            plan_cxt.last_result = PlanRunContext.ACTIONRESULT_COMPLETE
            print("{} {}".format(format(plan_cxt.to_action_tag(self)), self.print_args()))
        else:
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug("{}: Invalid nested action state {}".
                    format(plan_cxt.to_action_tag(self), plan_cxt.last_result))
              
    @staticmethod
    def launchby_nonplanner_parent(builder: SubtaskBuildData, termination_handler: Callable, custom_problem: CustomProblem):
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(f"Preparing to build for planner subtask={builder.task_def.task_ns}.{builder.task_def.task_id}")
                
        sys_cxt = builder.task_cxt.sys_cxt
        child_plan_spec = sys_cxt.plan_builder.build_plan(builder.task_def.task_ns, builder.task_def.task_id, custom_problem)
        
        state_cxt = None
        if builder.child_spec.state_cxtid is not None:
            state_cxt = sys_cxt.get_persistent_state_context(builder.child_spec.state_cxtid)
        
        parent_state_cxt = builder.parent_task_cxt.state_cxt if builder.parent_task_cxt is not None else  builder.initial_statecxt
        if state_cxt is None:
            state_cxt = PlanStateContext(f"{builder.task_def.task_ns}:{builder.task_def.task_id}", parent_state_cxt)
        else:
            state_cxt.parent = parent_state_cxt
            
        problem_pddlpath = child_plan_spec.task_definition.get_problem_pddlpath(custom_problem)
        PlannerFactory.get_instance().get_planning_processor().load_problem(problem_pddlpath, 
            child_plan_spec.task_definition.get_domain(), state_cxt)
        
        runner = PlanRunner(child_plan_spec, plan_cxt=builder.task_cxt, 
            state_cxt=state_cxt,
            on_termination=termination_handler, 
            is_daemon=builder.child_spec.daemon_child)
        sys_cxt.scheduler.register_runner(runner, schedule=True)   
         
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug("{}: Invoking nested plan {}, nested plan-id using taskdef={}".
                format(builder.task_cxt.to_action_tag(builder.invoker_action), runner.plan_cxt.runcxt_id, builder.task_def.task_id))
        return (None, runner)    
        
    @staticmethod
    def launchby_planner_parent(builder: SubtaskBuildData, termination_handler: Callable, custom_problem: CustomProblem):
        _, runner = NestedActionProcess.launchby_nonplanner_parent(builder, termination_handler, custom_problem)
        nested_cxt = {}
        nested_cxt["parent"] = builder.parent_task_cxt
        nested_cxt["child_runcxtid"] = builder.task_cxt.runcxt_id
        def on_termination_handler():
            builder.invoker_action._on_child_complete(nested_cxt)
        return (on_termination_handler, runner)
        
    def _on_child_complete(self, nested_cxt: Dict[str, Any]):
        plan_cxt = nested_cxt["parent"]
        child_runcxtid = nested_cxt["child_runcxtid"]
        
        plan_cxt.last_result = PlanRunContext.ACTIONRESULT_WAITCOMPLETE
        plan_cxt.sys_cxt.scheduler.add_task(plan_cxt.runner)
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug("{}: Finished nested action child {}".
                format(plan_cxt.to_action_tag(self), child_runcxtid))

class PlanSpec:
    def __init__(self, plan_id: int, task_definition: TaskDefinition, custom_problem: CustomProblem,
        uncommitted_planpath) -> None:
        self.plan_id = plan_id
        self.task_definition = task_definition
        self.action_processor = None
        self.custom_problem = custom_problem
        self.uncommitted_planpath = uncommitted_planpath
        self.plan: List[ActionProcess] = []
        
    def build_plan(self, parsed_plan: TaskPlan) -> "PlanSpec":
        self.plan = []
        
        log_buf = None
        if logger.isEnabledFor(logging.DEBUG):
            log_buf = ""
            
        action_work_callable = self.task_definition.action_work_callable
        domain = self.task_definition.get_domain()
        self.action_processor = PlannerFactory.get_instance().get_planning_processor().build_action_processor(domain)
        for action_instance in parsed_plan.actions:
            action_name = action_instance.action_name
            arg_list = action_instance.arg_list
            
            action_def = domain.actions.get(action_name)
            
            notify = False
            nested = False
            receive = False
            ltm = self.task_definition.ltm
            if ltm is not None:
                if ltm.can_nest_action(action_name):
                    nested = True
                if ltm.can_notify_state(action_name):
                    notify = True
                if ltm.can_await_state(action_name):
                    receive = True
            
            action_spec = ActionProcess(action_name, arg_list, action_def) if not nested else \
                NestedActionProcess(action_name, arg_list, action_def)
            if notify or receive:
                action_spec.configure(notify, receive)
            if action_work_callable is not None:
                work_callable = action_work_callable.get(action_name)
                if work_callable is not None:
                    action_spec.bind_action_worker(work_callable)
                
            self.plan.append(action_spec)
            
            if log_buf is not None:
                log_buf = log_buf + "\n{}{}{}{}".format(action_name, str([item for item in arg_list]), ", Nested" if nested else "", ", Notify" if notify else "")
            
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug("Compiled plan for plan_id={}".format(self.plan_id) + log_buf)
        return self
    
    def get_action(self, at_index):
        return self.plan[at_index] if at_index < len(self.plan) else None
            
    def complete(self, sys_cxt):
        if self.uncommitted_planpath is not None:
            sys_cxt.plan_builder.dispose_uncommitted_plan(self)
            
    def to_print(self) -> str:
        buf = ""
        for action in self.plan:
            buf = buf + ", " + str(action)
       
class PlanErrorHandler:
    def handle_error(self, action, plan_cxt):
        message = "{}: Failed processing for {} - aborting".format(plan_cxt.to_planrun_tag(), action)
        if logger.isEnabledFor(logging.ERROR):
            logger.error(message)
        print("ERROR: {}".format(message))
        return False

class ActionCallable:
    def __call__(self, action_spec: ActionProcess, plan_cxt: PlanRunContext) -> None:
        pass
    
class PlanBuilder:
    def __init__(self, level_manager):
        self._level_manager = level_manager
        self._plan_id_generator: UniqueIdGenerator = UniqueIdGenerator(0)
        
    def start(self):
        pass
    
    def stop(self):
        pass
    
    def build_plan(self, ns: str, task_id: str, custom_problem: CustomProblem = None) -> PlanSpec:
        task_definition = self._level_manager.get_scoped_levelmgr(ns).get_task_definition(task_id)
        
        problem_pddl_path = task_definition.problem_pddl
        if task_definition.problem_custom_callable is not None:
            initial_custom_state = custom_problem.pddlgen_render_dict if custom_problem is not None else {}
            task_definition.problem_custom_callable(task_definition, initial_custom_state)
            if custom_problem is None:
                custom_problem = CustomProblem(initial_custom_state)
        
        if custom_problem is not None and len(custom_problem.pddlgen_render_dict) == 0:
            custom_problem = None
        
        uncommitted_planpath = None
        plan_path, plan_key = self._load_optimized_plan(task_definition, custom_problem)
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(f"Loaded optimized plan {plan_path} key={plan_key}")
        planner_factory = PlannerFactory.get_instance()
        if plan_path is None:
            if custom_problem is not None:
                problem_pddl_path = task_definition.get_problem_pddlpath(custom_problem)
                if not os.path.exists(problem_pddl_path):
                    custom_problem.build_problem_pddl(task_definition.problem_gen, problem_pddl_path)
            
            plan_basepath = task_definition.get_artifact_path(PlannerTaskDefinition.TMP_PLAN_DIR)
            plan_key = self._plan_id_generator.generate_id()
            plan_path = os.path.join(plan_basepath, "{}.plan".format(plan_key))
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug(f"Building new plan at {plan_path}, key={plan_key}")
            rc = planner_factory.get_planner(task_definition.planner). \
                invoke_planner(task_definition.domain_pddl, problem_pddl_path, plan_path)
            if rc < 0 or not os.path.exists(plan_path) or os.path.getsize(plan_path) == 0:
                raise ValueError(f"Failed generating plan for {task_definition.domain_pddl}, problem {problem_pddl_path}: {rc}")
            uncommitted_planpath = plan_path
                
        parsed_plan = planner_factory.parse_plan(plan_path)
        runnable_plan = PlanSpec(plan_key, task_definition, custom_problem, uncommitted_planpath).\
            build_plan(parsed_plan)
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug("LEVELMGR: Built plan {} for task {} at {}".\
                format(plan_key, task_id, plan_path))
        return runnable_plan
    
    def commit_optimized_plan(self, plan_spec: PlanSpec, key:str) -> None:
        if plan_spec.uncommitted_planpath is None:
            return
        
        plan_dir = self._plan_path(plan_spec.task_definition, plan_spec.custom_problem)
        commit_file_path = os.path.join(plan_dir, "{}.plan".format(key))
        if not os.path.isfile(commit_file_path):
            shutil.move(plan_spec.uncommitted_planpath, commit_file_path)
        else:
            os.remove(plan_spec.uncommitted_planpath)
        plan_spec.uncommitted_planpath = None
    
    def dispose_uncommitted_plan(self, plan_spec: PlanSpec):
        if plan_spec.uncommitted_planpath is None:
            return
        os.remove(plan_spec.uncommitted_planpath)
        plan_spec.uncommitted_planpath = None
        
    def _plan_path(self, task_definition: TaskDefinition, custom_problem: CustomProblem) -> None:
        plan_basepath = task_definition.get_artifact_path(PlannerTaskDefinition.PLAN_DIR)
        plan_cxtdir = PlannerTaskDefinition.NONCXT if custom_problem is None else \
            custom_problem.generate_id()
        plan_dir = os.path.join(plan_basepath, plan_cxtdir)
        if not os.path.isdir(plan_dir):
            os.makedirs(plan_dir, exist_ok=True)
        return plan_dir
        
    def _load_optimized_plan(self, task_definition, custom_problem) -> Tuple[str, str]:
        optimizer = task_definition.optimizer
        if optimizer is None or optimizer.generate_plan(custom_problem):
            return (None, None)
        
        graph = optimizer.get_graph(custom_problem)
        if graph is None:
            return (None, None)
        
        path = graph.select_plan()
        if path is None:
            return (None, None)
        
        plan_key = graph.path_to_key(path)
        plan_dir = self._plan_path(task_definition, custom_problem)
        plan_path = os.path.join(plan_dir, "{}.plan".format(plan_key))
        if os.path.isfile(plan_path):
            return (plan_path, plan_key)
        return (None, None)
        
class PlannerInvokeSubtaskFactory(InvokeSubtaskFactory):
    def build_task_cxt(self, builder: SubtaskBuildData) -> TaskRunContext:
        task_cxt = PlanRunContext(builder.task_def.task_ns, builder.task_def.task_id, builder.parent_task_cxt)
        task_cxt.statistics = PlanStatistics()
        task_cxt.task_tech = TaskRunContext.TECH_PLANNER
        task_cxt.task_def = builder.task_def
        if builder.parent_task_cxt is not None:
            task_cxt.app_spec = builder.parent_task_cxt.app_spec
            task_cxt.sys_cxt = builder.parent_task_cxt.sys_cxt
        else:
            task_cxt.sys_cxt = builder.sys_cxt
        return task_cxt
    
    def invoke_subtask(self, builder: SubtaskBuildData) -> Tuple[Any, bool]:
        completion_cxt = {
            "term_han": None,
            "completed": False,
            "cond": None
        }
        def termination_handler():
            self._on_complete_subtask(completion_cxt)
            
        custom_problem = self._compute_args(builder)
        if builder.parent_task_def is not None and builder.parent_task_def.tech == TaskRunContext.TECH_PLANNER:
            term_han, _ = NestedActionProcess.launchby_planner_parent(builder, termination_handler, custom_problem)
            completion_cxt["term_han"] = term_han
            result = (None, False)
        else:
            term_han, _ = NestedActionProcess.launchby_nonplanner_parent(builder, termination_handler, custom_problem)
            completion_cxt["term_han"] = term_han
            
            complete_cond = Condition()
            completion_cxt["cond"] = complete_cond
            with complete_cond:
                while not completion_cxt["completed"]:
                    complete_cond.wait()
            result = (None, True)
        return result
                
    def _on_complete_subtask(self, completion_cxt):
        term_han = completion_cxt["term_han"]
        if term_han is not None:
            term_han()
            
        if completion_cxt["cond"] is not None:
            with completion_cxt["cond"]:
                completion_cxt["completed"] = True
                completion_cxt["cond"].notify()
            
    def _compute_args(self, builder: SubtaskBuildData) -> Dict[str, Any]: 
        if builder.child_spec is None or \
            builder.child_spec.input_spec is None or \
            len(builder.child_spec.input_spec) == 0 or \
            builder.parent_task_cxt is None or \
            builder.parent_task_cxt.state_cxt is None:
            return None
            
        sys_cxt = builder.task_cxt.sys_cxt
        input_spec = builder.child_spec.input_spec
        state_cxt = builder.parent_task_cxt.state_cxt
        spec_type = input_spec.get("spec_type")
        if spec_type is not None and spec_type != "argfn":
            raise ValueError(f"Unsupported input-argument specification type: {spec_type}")
        
        scoped_levelmgr = sys_cxt.level_manager.get_scoped_levelmgr(input_spec.get("modns"))
        module = scoped_levelmgr.get_module_definition(input_spec.get("modid"))
        fn_args = {
            "statecxt": state_cxt,
            "parent_ns": builder.parent_task_def.task_ns,
            "parent_id": builder.parent_task_def.task_id,
            "subtask_ns": builder.task_def.task_ns,
            "subtask_id": builder.task_def.task_id,
            "subtask_args": builder.subtask_args
        }
        pddl_gen_reder_dict = module.execute_fn(input_spec.get("argfn"), fn_args)
        
        return CustomProblem(pddl_gen_reder_dict) if pddl_gen_reder_dict is not None else None
    
    def accept_output(self, builder: SubtaskBuildData, fn_result: Any) -> Any:
        if builder.child_spec is None or \
            builder.child_spec.output_spec is None or \
            len(builder.child_spec.output_spec) == 0 or \
            builder.parent_task_cxt is None or \
            builder.parent_task_cxt.state_cxt is None:
            return None
            
        sys_cxt = builder.task_cxt.sys_cxt
        output_spec = builder.child_spec.output_spec
        state_cxt = builder.task_cxt.state_cxt
        spec_type = output_spec.get("spec_type")
        if spec_type is not None and spec_type != "argfn":
            raise ValueError(f"Unsupported input-argument specification type: {spec_type}")
        
        scoped_levelmgr = sys_cxt.level_manager.get_scoped_levelmgr(output_spec.get("modns"))
        module = scoped_levelmgr.get_module_definition(output_spec.get("modid"))
        fn_args = {
            "statecxt": state_cxt,
            "parent_ns": builder.parent_task_def.task_ns,
            "parent_id": builder.parent_task_def.task_id,
            "subtask_ns": builder.task_def.task_ns,
            "subtask_id": builder.task_def.task_id,
            "result": fn_result
        }
        module.execute_fn(output_spec.get("argfn"), fn_args)
        return None
            
class PlanRunner:
    def __init__(self, 
        plan_spec: "PlanSpec",
        sys_cxt: SystemContext = None,
        state_cxt: StateContext = None, 
        plan_cxt: TaskRunContext = None,
        error_handler: PlanErrorHandler = None,
        on_termination: Callable = None, 
        is_daemon: bool = False):
        
        self.plan_spec = plan_spec
        self.plan_cxt = plan_cxt
        self.plan_cxt.plan_spec = plan_spec
        self.plan_cxt.daemon = is_daemon
        self.plan_cxt.runner = self
        self.plan_cxt.sys_cxt = sys_cxt or plan_cxt.sys_cxt
        self.plan_cxt.state_cxt = state_cxt if state_cxt is not None else PlanStateContext(f"{plan_cxt.task_ns}:{plan_cxt.task_id}", None)
        if self.plan_cxt.state_cxt.parent is None and self.plan_cxt.parent_cxt is not None:
            self.plan_cxt.state_cxt.parent = self.plan_cxt.parent_cxt.state_cxt
        self.error_handler = error_handler
        self.on_termination = on_termination
    
    def is_ready(self):
        action = self.plan_spec.get_action(self.plan_cxt.action_index)
        if action is not None:
            return action.is_ready()
        return False
    
    def __call__(self):
        self.run()
        
    def _plan_state_transit(self, new_state):
        with self.plan_cxt.task_lock:
            self.plan_cxt.lifecycle = new_state
        
    def check_dispatch(self):
        with self.plan_cxt.task_lock:
            if self.plan_cxt.lifecycle == PlanRunContext.PLANST_WAIT and \
                self.plan_cxt.task_dispatch_id < self.plan_cxt.task_process_id:
                self.plan_cxt.task_dispatch_id = self.plan_cxt.task_process_id
                self.plan_cxt.sys_cxt.scheduler.add_task(self)
    
    def run(self):
        with self.plan_cxt.task_lock:
            if self.plan_cxt.lifecycle == PlanRunContext.PLANST_ACTIVE:
                return
            
            if self.plan_cxt.lifecycle == PlanRunContext.PLANST_INIT:
                self._set_init_state()
            
            self.plan_cxt.task_process_id = self.plan_cxt.task_process_id + 1
            self.plan_cxt.lifecycle = PlanRunContext.PLANST_ACTIVE
            
        task_monitor = self.plan_cxt.sys_cxt.task_monitor
        
        prev_cxt = ProcManage.set_task_cxt(self.plan_cxt)
        try:
            while True:
                action = self.plan_spec.get_action(self.plan_cxt.action_index)
                if action is None:
                    self._on_complete()
                    return
                
                task_monitor.action_begin(action, self.plan_cxt)
                action.process(self.plan_cxt)
                if self.plan_cxt.last_result == PlanRunContext.ACTIONRESULT_ERROR and \
                    not self.error_handler(action, self.plan_cxt):
                    return
                elif self.plan_cxt.last_result == PlanRunContext.ACTIONRESULT_WAIT or \
                    self.plan_cxt.last_result == PlanRunContext.ACTIONRESULT_WAITCHILD:
                    task_monitor.action_pause(action, self.plan_cxt)
                    self._plan_state_transit(PlanRunContext.PLANST_WAIT)
                    return
                else:
                    task_monitor.action_complete(action, self.plan_cxt)
                    if logger.isEnabledFor(logging.DEBUG):
                        logger.debug("{}: Completed Action {}".format(self.plan_cxt.to_planrun_tag(), action))
                    self.plan_cxt.last_result = PlanRunContext.ACTIONRESULT_NONE
                    self.plan_cxt.action_index = self.plan_cxt.action_index + 1
        finally:
            ProcManage.set_task_cxt(prev_cxt)

    def _set_init_state(self):
        self.plan_cxt.action_index = 0
        self.plan_cxt.sys_cxt.task_monitor.task_launch(self.plan_cxt)
        
        message = "{}: Start {}".format(self.plan_cxt.to_planrun_tag(), self.plan_cxt.get_taskid())
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(message)
        print(message)
        
    def _on_complete(self):
        self._plan_state_transit(PlanRunContext.PLANST_COMPLETE)
        self.plan_cxt.sys_cxt.task_monitor.task_complete(self.plan_cxt)
        
        message = "{}: Complete {}".format(self.plan_cxt.to_planrun_tag(), self.plan_cxt.get_taskid())
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(message)
        print(message)
        
        self.plan_spec.complete(self.plan_cxt.sys_cxt)
        self.plan_cxt.runner = None
        self.plan_cxt.sys_cxt.scheduler.deregister_runner(self)
        if self.on_termination:
            self.on_termination()
