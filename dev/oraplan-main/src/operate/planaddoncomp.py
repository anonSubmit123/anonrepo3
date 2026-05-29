from typing import Callable
import random
import logging
import time
from plan.plancompile import PlanRunContext, ActionProcess
from operate.syscxt import TaskSelector, TaskMonitor
from baseproc.ltm import ChildNestingSpec
from baseproc.basetask import TaskRunContext
from baseproc.proccontext import TaskAction

LOGGER_BASE = "oraplan.plan"
LOG_TAG = {"lgmod": "ORAPLAN"}
LOG_LINE = "\n\t"
logger = logging.getLogger(LOGGER_BASE)

class NoArgProxyCallable:
    def __init__(self, delegate: Callable[[], None]):
        self.delegate = delegate
        
    def __call__(self, action_spec: ActionProcess, plan_cxt: PlanRunContext) -> None:
        self.delegate()
            
class BlockingCallable:
    def __init__(self, purpose: str = "", min_time: float = 1.0, max_time: float = 5.0, 
        custom_logger: logging.Logger = None):
        self.min_time = min_time
        self.max_time = max_time
        self.purpose = purpose
        self.logger = custom_logger if custom_logger is not None else logger
        
    def block(self):
        block_time = self.min_time if self.min_time == self.max_time else random.uniform(self.min_time, self.max_time)
        time.sleep(block_time)
        return block_time
    
    def __call__(self, action_spec: ActionProcess, plan_cxt: PlanRunContext) -> None:
        blocked_time = self.block()
        print("{}".format(format(plan_cxt.to_action_tag(action_spec)), self))
        if self.logger.isEnabledFor(logging.DEBUG):
            self.logger.debug("{}: {} _completed work in {} time".
                format(plan_cxt.to_action_purpose_tag(action_spec, "ACTION-WORK"), self.purpose, blocked_time),
                extra=LOG_TAG)

class TaskMonitorComposite(TaskMonitor):
    def __init__(self):
        self._monitors = []
        
    def add_monitor(self, monitor: TaskMonitor) -> None:
        self._monitors.append(monitor)
    
    def remove_monitor(self, monitor: TaskMonitor) -> None:
        self._monitors.remove(monitor)
    
    def task_launch(self, task_cxt: TaskRunContext) -> None:
        for monitor in self._monitors:
            monitor.task_launch(task_cxt)
    
    def task_complete(self, task_cxt: TaskRunContext) -> None:
        for monitor in self._monitors:
            monitor.task_complete(task_cxt)
        
    def action_begin(self, action_spec: TaskAction, task_cxt: TaskRunContext) -> None:
        for monitor in self._monitors:
            monitor.action_begin(action_spec, task_cxt)
    
    def action_pause(self, action_spec: TaskAction, task_cxt: TaskRunContext) -> None:
        for monitor in self._monitors:
            monitor.action_pause(action_spec, task_cxt)
    
    def action_complete(self, action_spec: TaskAction, task_cxt: TaskRunContext) -> None:
        for monitor in self._monitors:
            monitor.action_complete(action_spec, task_cxt)

class TaskTimeMonitor(TaskMonitor):
    def task_launch(self, task_cxt: TaskRunContext) -> None:
        task_cxt.statistics.start_task()
    
    def task_complete(self, task_cxt: TaskRunContext) -> None:
        task_cxt.statistics.complete_task()
        
    def action_begin(self, action_spec: TaskAction, task_cxt: TaskRunContext) -> None:
        task_cxt.statistics.start_action(action_spec)
    
    def action_pause(self, action_spec: TaskAction, task_cxt: TaskRunContext) -> None:
        task_cxt.statistics.pause_action(action_spec)
    
    def action_complete(self, action_spec: TaskAction, task_cxt: TaskRunContext) -> None:
        task_cxt.statistics.complete_action(action_spec)
    
class TaskPerformanceSelector(TaskSelector):
    def select_child(self, action_spec: TaskAction, task_cxt: TaskRunContext, child_spec: ChildNestingSpec) -> str:
        if child_spec.optimizer is None:
            selected_child = child_spec.children[0]
            return (selected_child.child_ns, selected_child.child_taskid)
        
        bandit = child_spec.optimizer.get_bandit(child_spec.arg_context, action_spec.arg_list)
        child = bandit.select_arm()
        if child >= 0 and child < len(child_spec.children):
            selected_child = child_spec.children[child]
            return (selected_child.child_ns, selected_child.child_taskid)
        else:
            selected_child = child_spec.children[0]
            return (selected_child.child_ns, selected_child.child_taskid)