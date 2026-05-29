from __future__ import annotations
from common.util import UniqueIdGenerator, VersionedSerializable
from typing import List, Any, Dict, Tuple, Set, Iterable

from typing import TYPE_CHECKING

from baseproc.proccontext import ProcManage, TaskAction
import time
from common.excp import InvalidStateException
from baseproc.basestore import AppContextStore, FactAtom
import logging

LOGGER_BASE = "oraplan.plan"
LOG_TAG = {"lgmod": "ORAPLAN"}
LOG_LINE = "\n\t"
logger = logging.getLogger(LOGGER_BASE)

if TYPE_CHECKING:
    from operate.syscxt import SystemContext
    from baseproc.ltm import ChildSpec, ChildNestingSpec    
    from baseproc.levelmgr import TaskDefinition
    
class TaskStatistics:
    def get_total_time(self) -> float:
        raise NotImplementedError("TaskStatistics.get_total_time not implemented")
    
    def get_is_valid(self) -> bool:
        raise NotImplementedError("TaskStatistics.get_is_valid not implemented")
    
    def start_task(self):
        raise NotImplementedError("TaskStatistics.start_task not implemented")
    
    def complete_task(self):
        raise NotImplementedError("TaskStatistics.complete_task not implemented")
    
class ProcedureStatistics(TaskStatistics):
    def __init__(self):
        self._task_start: float = -1
        self._task_end: float = -1
        self._total_time = -1
    
    def _reset_stats(self):
        self._task_start: float = -1
        self._task_end: float = -1
        self._total_time = -1
        
    def start_task(self):
        self._task_start = time.perf_counter()
    
    def complete_task(self):
        self._task_end = time.perf_counter()
        self._total_time = self._task_end - self._task_start
        
    def get_total_time(self) -> float:
        return self._total_time
    
    def get_is_valid(self) -> bool:
        return self._total_time >= 0
        
class StateContext(AppContextStore, VersionedSerializable):
    version = 1
    _cxtid_assigner = UniqueIdGenerator()
    
    def __init__(self, desc: str, parent: "StateContext" = None):
        self.cxt_id: int = StateContext._cxtid_assigner.generate_id()
        self.desc: str = desc
        self.parent: "StateContext" = parent
        
class KvpStateContext(StateContext):
    version = 1
    
    def __init__(self, desc: str, parent: StateContext):
        super().__init__(desc, parent)
        self.kvp_map: Dict[str, Any] = {}
        
    def _is_present(self, item_id: str):
        if item_id in self.kvp_map:
            return self
        if self.parent is not None:
            return self.parent._is_present(item_id)
        return None
        
    def get_item(self, item_id: str) -> Any:
        if item_id in self.kvp_map:
            return self.kvp_map[item_id]
        if self.parent is not None:
            return self.parent.get_item(item_id)
        return None
    
    def set_item(self, item_id: str, item_value: Any) -> Any:
        current_value = self.kvp_map.get(item_id)
        self.kvp_map[item_id] = item_value
        return current_value
    
    def set_item_default(self, item_id: str, item_value: Any) -> Any:
        owning_cxt = self._is_present(item_id)
        if owning_cxt is not None:
            return owning_cxt.get_item(item_id)
        self.kvp_map[item_id] = item_value
        return item_value
    
    def add_objects(self, object_map: Dict[str, Set[str]]) -> None:
        if self.parent is not None:
            self.parent.add_objects(object_map)
        else:
            raise InvalidStateException("App-Store does not support add_objects")
        
    def add_fact((self, new_atom: FactAtom) -> None:
        if self.parent is not None:
            self.parent.add_fact(new_atom)
        else:
            raise InvalidStateException("App-Store does not support add_fact")

    def remove_fact(self, rem_atom: FactAtom) -> None:
        if self.parent is not None:
            self.parent.remove_fact(rem_atom)
        else:
            raise InvalidStateException("App-Store does not support remove_fact")
                
    def has_fact(self, atom: FactAtom) -> bool:
        return self.parent.has_fact(atom) if self.parent is not None else False
            
    def fetch_facts(self, pattern: FactAtom) -> List[FactAtom]:
        return self.parent.fetch_facts(pattern) if self.parent is not None else []
    
    def reachable_facts(self, initial_objs: Set[Any]) -> Tuple[Set[FactAtom], Set[Any]]:
        return self.parent.reachable_facts(initial_objs) if self.parent is not None else None
    
    def get_all_facts(self) -> Iterable[FactAtom]:
        if self.parent is not None:
            return self.parent.get_all_facts()
        return None
    
    def get_own_facts(self) -> Iterable[FactAtom]:
        return None
    
    def add_function(self, new_fn: Tuple[FactAtom, float]) -> None:
        if self.parent is not None:
            self.parent.add_function(new_fn)
        else:
            raise InvalidStateException("App-Store does not support add_function")
    
    def remove_function(self, rmfn_fact: FactAtom) -> None:
        if self.parent is not None:
            self.parent.remove_function(rmfn_fact)
        else:
            raise InvalidStateException("App-Store does not support remove_function")
                
    def get_function(self, getfn_fact: FactAtom) -> float:
        if self.parent is not None:
            return self.parent.get_function(getfn_fact)
        else:
            raise InvalidStateException("App-Store does not support get_function")
            
    def has_function(self, fn_fact: FactAtom) -> bool:
        if self.parent is not None:
            return self.parent.has_function(fn_fact)
        else:
            raise InvalidStateException("App-Store does not support has_function")
        
    def fetch_functions(self, pattern: FactAtom) -> List[FactAtom]:
        return self.parent.fetch_functions(pattern) if self.parent is not None else []
    
    def reachable_functions(self, initial_objs: Set[Any]) -> Tuple[Set[FactAtom], Set[Any]]:
        return self.parent.reachable_functions(initial_objs) if self.parent is not None else None
        
class TaskRunContext:
    ACTION_TAG = "{}-{} {}"
    _runcxt_id_generator: UniqueIdGenerator = UniqueIdGenerator(0)
    TECH_PLANNER = 1
    TECH_PROCEDURE = 2
    
    def __init__(self, task_ns: str, task_id: str, parent_cxt: "TaskRunContext" = None):
        self.runcxt_id: int = TaskRunContext._runcxt_id_generator.generate_id()
        self.task_ns: str = task_ns
        self.task_id: str = task_id
        self.task_tech: int = None  
        self.task_def: None
        self.state_cxt: AppContextStore = None
        self.parent_cxt: "TaskRunContext" = parent_cxt
        self.children: List["TaskRunContext"] = None
        self.sys_cxt: SystemContext = None
        self.daemon: bool = False
        self.statistics = None
        self.app_spec: Dict[str, Any] = None
        self.in_parent_nesting_spec: None
        self.logger = None
        
        if parent_cxt is not None:
            parent_cxt._add_child(self)
        
    def _add_child(self, child_cxt: "TaskRunContext") -> None:
        child_cxt.parent_cxt = self
        if self.children is None:
            self.children = []
        self.children.append(child_cxt)
        
    def get_taskref(self):
        return (self.task_ns if self.task_ns is not None else "") + ":" + self.task_id 
    
    def to_action_tag(self, action: TaskAction, tag = "TASK"):
        action_name = action.action_name if action is not None else ""
        return TaskRunContext.ACTION_TAG.format(tag, self.runcxt_id, action_name)
    
class SubtaskBuildData:
    def __init__(self):
        self.task_def: TaskDefinition = None
        self.child_spec: ChildSpec = None
        self.subtask_args: Dict[str, Any] = None
        self.child_nesting_spec: ChildNestingSpec = None, 
        self.invoker_action: TaskAction = None
        self.parent_task_def: TaskDefinition = None
        self.parent_task_cxt: TaskRunContext = None
        self.sys_cxt: SystemContext = None
        self.wait_completion: bool = False
        self.task_cxt: TaskRunContext = None
        self.initial_statecxt = None
        
class InvokeSubtaskFactory:
    def build_task_cxt(self, builder: SubtaskBuildData) -> TaskRunContext:
        raise NotImplementedError("InvokeSubtaskFactory.build_task_cxt  not implemented")
    
    def invoke_subtask(self, builder: SubtaskBuildData) -> Tuple[Any, bool]:
        raise NotImplementedError("InvokeSubtaskFactory.InvokeSubtaskFactory  not implemented")
    
    def accept_output(self, builder: SubtaskBuildData, fn_result: Any) -> Any:
        raise NotImplementedError("InvokeSubtaskFactory.accept_output  not implemented")
    
class ProcedureInvokeSubtaskFactory:
    def build_task_cxt(self, builder: SubtaskBuildData) -> TaskRunContext:
        task_cxt = TaskRunContext(builder.task_def.task_ns, builder.task_def.task_id, builder.parent_task_cxt)
        task_cxt.logger = logging.getLogger("oraplan.app")
        task_cxt.task_tech = TaskRunContext.TECH_PROCEDURE
        task_cxt.task_def = builder.task_def
        task_cxt.statistics = ProcedureStatistics()
        if builder.parent_task_cxt is not None:
            task_cxt.app_spec = builder.parent_task_cxt.app_spec
            task_cxt.sys_cxt = builder.parent_task_cxt.sys_cxt
        else:
            task_cxt.sys_cxt = builder.sys_cxt
        return task_cxt
        
    def invoke_subtask(self, builder: SubtaskBuildData):
        child_cxt = builder.task_cxt
        parent_state_cxt = builder.parent_task_cxt.state_cxt if builder.parent_task_cxt is not None \
            else builder.initial_statecxt 
        child_cxt.state_cxt = KvpStateContext(f"{child_cxt.task_id}", parent_state_cxt)
        prev_cxt = ProcManage.set_task_cxt(child_cxt)
        try:
            child_cxt.sys_cxt.task_monitor.task_launch(child_cxt)
            invoke_args = self._compute_args(builder)
            
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug("{}: Invoking nested task {}, using taskdef={}".
                    format(builder.task_cxt.to_action_tag(builder.invoker_action), builder.task_def, builder.task_def.task_id))
            fn_result = builder.task_def.execute(invoke_args)
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug("{}: Returned from nested task {}, using taskdef={}".
                    format(builder.task_cxt.to_action_tag(builder.invoker_action), builder.task_def, builder.task_def.task_id))
                
            child_cxt.sys_cxt.task_monitor.task_complete(child_cxt)
            return (fn_result, True)
        finally:
            ProcManage.set_task_cxt(prev_cxt)
            
    def accept_output(self, builder: SubtaskBuildData, fn_result: Any) -> Any:
        if builder.child_spec is None or \
            builder.child_spec.output_spec is None or \
            len(builder.child_spec.output_spec) == 0 or \
            builder.parent_task_cxt is None or \
            builder.parent_task_cxt.state_cxt is None:
            return fn_result
            
        sys_cxt = builder.task_cxt.sys_cxt
        output_spec = builder.child_spec.output_spec
        state_cxt = builder.task_cxt.state_cxt
        if output_spec.spec_type is not None and output_spec.spec_type != "argfn":
            raise ValueError(f"Unsupported input-argument specification type: {output_spec.spec_type}")
        
        scoped_levelmgr = sys_cxt.level_manager.get_scoped_levelmgr(output_spec.modns)
        module = scoped_levelmgr.get_module_definition(output_spec.modid)
        fn_args = {
            "statecxt": state_cxt,
            "parent_ns": builder.parent_task_def.task_ns,
            "parent_id": builder.parent_task_def.task_id,
            "subtask_ns": builder.task_def.task_ns,
            "subtask_id": builder.task_def.task_id,
            "result": fn_result
        }
        return module.execute_fn(output_spec.argfn, fn_args)
            
    def _compute_args(self, builder: SubtaskBuildData) -> Dict[str, Any]: 
        if builder.child_spec is None or \
            builder.child_spec.input_spec is None or \
            len(builder.child_spec.input_spec) == 0 or \
            builder.parent_task_cxt is None or \
            builder.parent_task_cxt.state_cxt is None:
            return builder.subtask_args
            
        sys_cxt = builder.task_cxt.sys_cxt
        input_spec = builder.child_spec.input_spec
        state_cxt = builder.parent_task_cxt.state_cxt
        if input_spec.spec_type is not None and input_spec.spec_type != "argfn":
            raise ValueError(f"Unsupported input-argument specification type: {input_spec.spec_type}")
        
        scoped_levelmgr = sys_cxt.level_manager.get_scoped_levelmgr(input_spec.modns)
        module = scoped_levelmgr.get_module_definition(input_spec.modid)
        fn_args = {
            "statecxt": state_cxt,
            "parent_ns": builder.parent_task_def.task_ns,
            "parent_id": builder.parent_task_def.task_id,
            "subtask_ns": builder.task_def.task_ns,
            "subtask_id": builder.task_def.task_id,
            "subtask_args": builder.task_def.subtask_args
        }
        return module.execute_fn(input_spec.argfn, fn_args)
    
class TaskRunner:
    _app_id_generator = UniqueIdGenerator()
    
    def __init__(self):
        self._invoke_factory_map: Dict[int, InvokeSubtaskFactory] = {}
        
    def register_invoke_subtask_factory(self, tech, factory):
        self._invoke_factory_map[tech] = factory
    
    def invoke_subtask(self, task_cxt: TaskRunContext, target_subtask: TaskAction, args: Dict[str, Any] = None,
        wait_completion = False) -> Tuple[Any, bool]:
        if task_cxt is None or target_subtask is None:
            raise ValueError("Missing invoke_subtask args")
        
        builder = SubtaskBuildData()
        builder.parent_task_cxt = task_cxt
        builder.invoker_action = target_subtask
        builder.subtask_args = args
        builder.wait_completion = wait_completion
        
        sys_cxt = builder.sys_cxt or task_cxt.sys_cxt
        
        scoped_level_manager = sys_cxt.level_manager.get_scoped_levelmgr(task_cxt.task_ns)
        if scoped_level_manager is None:
            raise ValueError(f"Unknown invoking parent's namespace {task_cxt.task_ns} for task {task_cxt.task_id} executing subtask {target_subtask.action_name}")
        
        builder.parent_task_def = scoped_level_manager.get_task_definition(task_cxt.task_id)
        if builder.parent_task_def is None:
            raise ValueError(f"No task definition in namespace {task_cxt.task_ns} for parent task {task_cxt.task_id} executing subtask {target_subtask.action_name}")
        
        builder.child_nesting_spec = builder.parent_task_def.ltm.match_action(target_subtask, task_cxt)
        if builder.child_nesting_spec is None:
            raise InvalidStateException(f"No matching child-nesting for specified subtask {target_subtask.action_name}")
        
        child_ns, child_taskid = sys_cxt.task_selector.select_child(target_subtask, task_cxt, builder.child_nesting_spec)
        builder.child_spec = next((child_item for child_item in builder.child_nesting_spec.children if \
            child_item.child_taskid == child_taskid and child_item.child_ns == child_ns), None)
        if builder.child_spec is None:
            raise InvalidStateException("No valid nested plan for action {}".format(self))
            
        scoped_level_manager = sys_cxt.level_manager.get_scoped_levelmgr(child_ns)
        if scoped_level_manager is None:
            raise ValueError(f"Unknown child task's namespace {child_ns} for task {child_taskid} executing subtask {target_subtask.action_name}")
        builder.task_def = scoped_level_manager.get_task_definition(child_taskid)
        if builder.task_def is None:
            raise ValueError(f"No task definition in namespace {task_cxt.task_ns} for child task {child_taskid} executing subtask {target_subtask.action_name}")
        
        return self._do_invoke_subtask(builder)
    
    def _do_invoke_subtask(self, builder: SubtaskBuildData, app_spec: Dict[str, Any] = None) -> Tuple[Any, bool]:
        invoker = self._invoke_factory_map.get(builder.task_def.tech)
        if invoker is None:
            raise ValueError(f"Cannot support subtasks for tech={builder.task_def.tech}")
        
        builder.task_cxt = invoker.build_task_cxt(builder)
        if app_spec is not None:
            builder.task_cxt.app_spec = app_spec
        builder.task_cxt.in_parent_nesting_spec = builder.child_nesting_spec
        
        fn_result, subtask_completed = invoker.invoke_subtask(builder)
        
        if builder.parent_task_def is not None:
            output_acceptor = self._invoke_factory_map.get(builder.parent_task_def.tech)
            fn_result = output_acceptor.accept_output(builder, fn_result)
        
        return (fn_result, subtask_completed)
        
    def launch_app(self, sys_cxt: SystemContext, app_ns: str, task_id: str, args: Dict[str, Any] = None, initial_statecxt: StateContext = None) -> Any:
        app_id = TaskRunner._app_id_generator.generate_id()
        app_spec = {
            "app_id": app_id,
            "app_ns": app_ns
        }
        
        builder = SubtaskBuildData()
        builder.initial_statecxt = initial_statecxt or sys_cxt.build_default_app_context(f"{app_ns}.{app_id}")
        scoped_level_manager = sys_cxt.level_manager.get_scoped_levelmgr(app_ns)
        if scoped_level_manager is None:
            raise ValueError(f"Unknown top app's namespace {app_ns} for task {task_id}")
        builder.task_def = scoped_level_manager.get_task_definition(task_id)
        if builder.task_def is None:
            raise ValueError(f"No task definition in namespace {app_ns} for top app task {task_id}")
        
        from baseproc.ltm import ChildSpec
        builder.child_spec = ChildSpec()
        builder.child_spec.child_ns = app_ns
        builder.child_spec.child_taskid = task_id
        
        builder.sys_cxt = sys_cxt
        builder.subtask_args = args
        
        result = self._do_invoke_subtask(builder, app_spec = app_spec)
        return result[0] if result is not None else None