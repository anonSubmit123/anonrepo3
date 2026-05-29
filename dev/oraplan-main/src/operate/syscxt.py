from typing import Callable, List, Dict, Any, Tuple
import threading
from queue import Queue
from concurrent.futures import ThreadPoolExecutor
from _queue import Empty
from jsonpath_ng import parse as jsonpath_parse
from spcoreutil.jsonCodec import get_first
import traceback
from baseproc.levelmgr import LevelManager, TaskOptimizer, TaskDefinition
from plan.plancompile import PlanRunner, PlanBuilder, PlannerInvokeSubtaskFactory
from _collections import deque
from threading import Condition
from baseproc.ltm import StateNotification, ChildNestingSpec
from baseproc.basetask import TaskRunner, TaskRunContext, StateContext,\
    ProcedureInvokeSubtaskFactory
import logging
from baseproc.proccontext import TaskAction
from plan.planbase import CompositeStateContext
from baseproc.ltmparser import LtmParser

CFG_PLAN_SCHEDULER_WORKERS = jsonpath_parse("$.planSchedulerWorkers")

LOGGER_BASE = "oraplan.plan"
LOG_TAG = {"lgmod": "ORAPLAN"}
LOG_LINE = "\n\t"
logger = logging.getLogger(LOGGER_BASE)

class TaskSelector:
    def start(self):
        pass
    
    def stop(self):
        pass
    
    def select_child(self, action_spec: TaskAction, task_cxt: TaskRunContext, child_spec: ChildNestingSpec) -> str:
        selected_child = child_spec.children[0]
        return (selected_child.child_ns, selected_child.child_taskid)
    
class TaskMonitor:
    def task_launch(self, task_cxt: TaskRunContext) -> None:
        pass
    
    def task_complete(self, task_cxt: TaskRunContext) -> None:
        pass
        
    def action_begin(self, action_spec: TaskAction, task_cxt: TaskRunContext) -> None:
        pass
    
    def action_pause(self, action_spec: TaskAction, task_cxt: TaskRunContext) -> None:
        pass
    
    def action_complete(self, action_spec: TaskAction, task_cxt: TaskRunContext) -> None:
        pass
    
class PlanScheduler:
    def __init__(self, config):
        self.num_workers = get_first(CFG_PLAN_SCHEDULER_WORKERS, config)
        self._task_queue = Queue()
        self.shutdown_flag = False
        self._condition = threading.Condition()
        self._executor = ThreadPoolExecutor(max_workers=self.num_workers)
        self._available_runner: Dict[int, PlanRunner] = {}
        self._daemon_runner: Dict[int, PlanRunner] = {}
        self.top_runner: PlanRunner = None

    def start(self):
        for _ in range(self.num_workers):
            self._executor.submit(self._worker)

    def _worker(self):
        while not self.shutdown_flag:
            with self._condition:
                while self._task_queue.empty() and not self.shutdown_flag:
                    self._condition.wait()
                    
                if self.shutdown_flag:
                    break
                
                try:
                    task = self._task_queue.get(block=False)
                except Empty:
                    continue
            try:
                task()
            except Exception as e:
                print(f"Error executing task: {e}")
                traceback.print_exc()
            finally:
                self._task_queue.task_done()
        
    def register_runner(self, runner: PlanRunner, schedule: bool = True, is_top: bool = False) -> None:
        self._available_runner[runner.plan_cxt.runcxt_id] = runner
        if runner.plan_cxt.daemon:
            self._daemon_runner[runner.plan_cxt.runcxt_id] = runner
        
        if is_top:
            self.top_runner = runner
            
        if schedule:
            self.add_task(runner)
            
    def get_runner(self, runcxt_id) -> PlanRunner:
        return self._available_runner.get(runcxt_id)
    
    def deregister_runner(self, runner: PlanRunner) -> None:
        self._available_runner.pop(runner.plan_cxt.runcxt_id)
        if runner.plan_cxt.daemon:
            self._daemon_runner.pop(runner.plan_cxt.runcxt_id)
            
    def add_task(self, task: Callable[[], None]) -> None:
        with self._condition:
            if not self.shutdown_flag:
                self._task_queue.put(task)
                self._condition.notify()
            else:
                print("Scheduler is shutting down. Cannot add new tasks.")
    
    def wait_shutdown_request(self):
        while not self.shutdown_flag:
            with self._condition:
                while not self.shutdown_flag:
                    self._condition.wait()
                    
                if self.shutdown_flag:
                    break
                
    def shutdown(self, wait=True):
        with self._condition:
            self.shutdown_flag = True
            self._condition.notify_all()
        if wait:
            self._task_queue.join()
        self._executor.shutdown(wait=wait)
        print("Scheduler has been shut down.")

class EventQueue:
    def __init__(self):
        self._queue = deque()
        self._event_lock = threading.Lock()
        self._notify_condition = None
        
    def bind_condition(self, condition: Condition) -> None:
        self._notify_condition = condition
        
    def put_event(self, event: Any):
        with self._event_lock:
            self._queue.append(event)
            if self._notify_condition is not None:
                with self._notify_condition:
                    self._notify_condition.notify()
            
    def has_event(self) -> bool:
        with self._event_lock:
            return len(self._queue) > 0
    
    def poll_event(self) -> Any:
        with self._event_lock:
            if len(self._queue) > 0:
                try:
                    return self._queue.popleft()
                except IndexError:
                    return None
            else:
                return None
    
class SyncSetStore:
    def __init__(self, sync_set: str, condition: threading.Condition):
        self.sync_set: str = sync_set
        self._sync_state_vars: Dict[str, Tuple[str, int]] = {}
        
    def get_sync_state_var(self, sync_state: str, sync_time: int = None):
        ret = self._sync_state_vars.get(sync_state)
        if sync_time is not None and ret[1] < sync_time:
            return None
        else:
            return ret[0]
    
    def set_sync_state_var(self, sync_state: str, sync_value: Any, sync_time: int):
        self._sync_state_vars[sync_state] = (sync_value, sync_time)
        
class SyncStateManager:
    def __init__(self):
        self.pending_runner: Dict[str, List[PlanRunner]] = {}
        self.sync_sets: Dict[str, SyncSetStore] = {}
        self._dispatch_condition = threading.Condition()
        self.event_queue = EventQueue()
        self.event_queue.bind_condition(self._dispatch_condition)
        self.shutdown_requested = False
        self._pending_lock = threading.Lock()
        
    def query_synch_state(self, sync_set: str, sync_state: str, plan_runner: PlanRunner, sync_time: int = None):
        with self._pending_lock:
            sync_set_store = self.sync_sets[sync_set]
            sync_value = None
            if sync_set_store is not None:
                sync_value = sync_set_store.get_sync_state_var(sync_state, sync_time)
                
            if sync_value is not None:
                return sync_value
            
            pending_key = "{}.{}".format(sync_set, sync_state)
            if sync_set_store is not None:
                self.sync_sets[sync_set] = SyncSetStore(sync_set)
                
            pend_list = self.pending_runner.get(pending_key)
            if pend_list is None:
                pend_list = []
                self.pending_runner[pending_key] = pend_list
            pend_list.append(plan_runner)
            return None
    
    def _update_synch_state(self, notification: StateNotification) -> None:
        with self._pending_lock:
            sync_set_store = self.sync_sets[notification.sync_set]
            if sync_set_store is not None:
                sync_set_store = SyncSetStore(notification.sync_set)
                self.sync_sets[notification.sync_set] = sync_set_store
            
            sync_set_store.set_sync_state_var(notification.sync_state, notification.sync_value, notification.sync_time)
            
            pending_key = "{}.{}".format(notification.sync_set, notification.sync_state)
            pend_list = self.pending_runner.get(pending_key)
            if pend_list is None:
                return
            self.pending_runner.pop(pending_key)
        
        for plan_runner in pend_list:
            plan_runner.check_dispatch()

    def notify_sync_state(self, notification: StateNotification):
        self.event_queue.put_event(notification)
    
    def __call__(self):
        self.run()
        
    def run(self):
        if logger.isEnabledFor(logging.INFO):
            logger.info("SyncStateManager Dispatch Thread Starting to monitor notification")
            
        while True:
            with self._dispatch_condition:
                self._dispatch_condition.wait()
            
            if self.shutdown_requested:
                if logger.isEnabledFor(logging.INFO):
                    logger.info("SyncStateManager Dispatch Thread Shutting Down gracefully")
                return
            
            if self.event_queue.has_event():
                notification = self.event_queue.poll_event()
                self._update_synch_state(notification)
      
    def start(self):
        pass
              
    def shutdown(self):
        self.shutdown_requested = True
        with self._dispatch_condition:
            self._dispatch_condition.notify()
    
class SystemContext:
    def __init__(self, level_manager: LevelManager, 
            scheduler: PlanScheduler,
            sync_state_manager: SyncStateManager = None,
            selector: TaskSelector = None,
            monitor: TaskMonitor = None,
            plan_builder: PlanBuilder = None,
            task_optimizer: TaskOptimizer = None,
            task_runner: TaskRunner = None):
        self.level_manager: LevelManager = level_manager
        self.scheduler: PlanScheduler = scheduler
        self.task_optimizer = task_optimizer
        self.plan_builder: PlanBuilder =  plan_builder if plan_builder is not None else PlanBuilder(level_manager)
        self.task_selector: TaskSelector = selector if selector is not None else TaskSelector()
        self.task_monitor: TaskMonitor = monitor if monitor is not None else TaskMonitor()
        self.task_runner: TaskRunner = task_runner if task_runner is not None else TaskRunner()
        self.task_runner.register_invoke_subtask_factory(TaskRunContext.TECH_PROCEDURE, ProcedureInvokeSubtaskFactory())
        self.task_runner.register_invoke_subtask_factory(TaskRunContext.TECH_PLANNER, PlannerInvokeSubtaskFactory())
        self._state_context_map: Dict[str, StateContext] = {}
        self.sync_state_manager = sync_state_manager if sync_state_manager is not None else SyncStateManager()
        
    def start(self):
        self.level_manager.start()
        self.plan_builder.start()
        self.task_selector.start()
        self.scheduler.start()
        self.sync_state_manager.start()
        self.scheduler.add_task(self.sync_state_manager)
        
        if self.task_optimizer is not None:
            self.task_optimizer.start(self)
    
    def stop(self, wait = True):
        self.scheduler.shutdown(wait)
        self.sync_state_manager.shutdown()
        
        if self.task_optimizer is not None:
            self.task_optimizer.stop(self)
            
        self.task_selector.stop()
        self.plan_builder.stop()
        self.level_manager.stop()
        
    def await_shutdown(self):
        self.scheduler.wait_shutdown_request()
        
    def get_persistent_state_context(self, state_cxtid: str, create_if_needed: bool = True) ->StateContext:
        state_cxt = self._state_context_map.get(state_cxtid)
        if state_cxt is None and create_if_needed:
            state_cxt = StateContext()
            self._state_context_map[state_cxtid] = state_cxt
        return state_cxt
    
    def build_default_app_context(self, desc: str):
        return CompositeStateContext(desc)
    
    def register_app(self, app_config: str) -> None:
        ltm_parser = LtmParser(app_config, self.level_manager, self.task_optimizer)
        ltm_parser.build_ltm()