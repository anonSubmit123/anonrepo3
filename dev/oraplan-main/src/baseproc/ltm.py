from typing import List, Dict, NewType, Any, Iterable
from common.util import VersionedSerializable, UniqueIdGenerator
from baseproc.basetask import TaskRunContext
from baseproc.proccontext import TaskAction

TaskDefinition = NewType("TaskDefinition", None)
PlanRunContext = NewType("PlanRunContext", None)

class MatchAction:
    def __init__(self, action_name: str):
        self.action_name: str = action_name
        
    def prepare(self, task_def: "TaskDefinition"):
        pass
    
    def match_action(self, task_action: TaskAction, task_cxt: TaskRunContext) -> bool:
        return  self.action_name == task_action.action_name  
    
class CriteriaMatcher:
    def match_criteria(self, task_action: TaskAction, task_cxt: TaskRunContext) -> bool:
        raise NotImplementedError("AppContextStore.get_item not implemented") 
        
class CriteriaMatcherByCondition(CriteriaMatcher):
    def __init__(self, cond):
        self.condition = cond
        
    def match_criteria(self, task_action: TaskAction, task_cxt: TaskRunContext) -> bool:
        return task_cxt.plan_spec.action_processor.eval_condition(self.condition, task_cxt.state_cxt)
    
class CriteriaMatcherByFunction(CriteriaMatcher):
    def __init__(self, module, function_name: str):
        self.module = module
        self.function = function_name
        
    def match_criteria(self, task_action: TaskAction, task_cxt: TaskRunContext) -> bool:
        fn_args = {
            "action": task_action,
            "statecxt": task_cxt.state_cxt,
        }
        return self.module.execute_fn(self.function, fn_args)
    
class MatchByCriteria(MatchAction):
    def __init__(self, name: str, criteria_matcher: CriteriaMatcher):
        super().__init__(name)
        self._criteria_matcher: CriteriaMatcher = criteria_matcher
        
    def prepare(self, task_def: "TaskDefinition"):
        pass
        
    def match_action(self, task_action: TaskAction, task_cxt: TaskRunContext):
        return self._criteria_matcher.match_criteria(task_action, task_cxt)

class ChildSpec:
    def __init__(self):
        self.child_ns: str = None
        self.child_taskid: str = None
        self.input_spec: Dict[str, Any] = None
        self.output_spec: Dict[str, Any] = None
        self.daemon_child: bool = False
        self.state_cxtid: str = None
        
class ChildNestingSpec(VersionedSerializable):
    version = 1
    
    nesting_spec_assign_id = UniqueIdGenerator(1)
    
    def __init__(self):
        self.nesting_spec_id = ChildNestingSpec.nesting_spec_assign_id.generate_id()
        self.match_spec: MatchAction = None
        self.children: List[ChildSpec] = None
        self.optimizer: Any = None
        self.arg_context: List[int] = None
        
    def match_action(self, task_action: TaskAction, task_cxt: TaskRunContext) -> bool:
        return self.match_spec.match_action(task_action, task_cxt)
    
    def _custom_getstate(self) -> dict:
        excluded_keys = {"optimizer"}
        ret_dict = {k: v for k, v in self.__dict__.items() if k not in excluded_keys}
        return ret_dict

    def _custom_setstate(self, state: dict):
        self.__dict__.update(state)
        self.optimizer = None
    
class StateNotification:
    def __init__(self, topic_category: str, topic_name: str, topic_value: str, notify_at: int, sender_id: str):
        self.sender_id = sender_id
        self.topic_category: str = topic_category
        self.topic_name: str = topic_name
        self.topic_value: str = topic_value
        self.notify_at: int = notify_at
        
    def __repr__(self):
        return "(Type={}. From={}, To={} )".format(self.event_type, self.sender_id, self.receiver_id)
    
class NotifyStateSpec:
    def __init__(self):
        self.match_spec: MatchAction = None
        self.topic_category: str = None
        self.topic_name: str = None
        self.topic_value: str = None
        
    def match_notifier(self, task_action: TaskAction, task_cxt: TaskRunContext) -> bool:
        return self.match_spec.match_action(task_action, task_cxt)  
    
class AwaitStateSpec:
    TIMESPEC_ANY = 1
    TIMESPEC_AFTER_START = 2
    
    def __init__(self):
        self.match_spec: MatchAction = None
        self.topic_category: str = None
        self.topic_name: str = None
        self.topic_time_spec: int = self.TIMESPEC_ANY
        
    def match_can_receive(self, task_action: TaskAction, task_cxt: TaskRunContext) -> bool:
        return self.match_spec.match_action(task_action, task_cxt) 
    
class LevelTransitionMap:
    def __init__(self):
        self.task_id: str = None
        self._nested_children: Dict[str, List[ChildNestingSpec]] = None
        self._notify_states: Dict[str, List[NotifyStateSpec]] = None
        self._await_states: Dict[str, List[AwaitStateSpec]] = None
        
    def add_child(self, action_name: str, spec: ChildNestingSpec ):
        if self._nested_children is None:
            self._nested_children = {}
        
        list_val = self._nested_children.get(action_name)
        if list_val is None:
            list_val = []
            self._nested_children[action_name] = list_val
        list_val.append(spec)
        
    def add_notify_state(self, action_name: str, spec: NotifyStateSpec):
        if self._notify_states is None:
            self._notify_states = {}
        
        list_val = self._notify_states.get(action_name)
        if list_val is None:
            list_val = []
            self._notify_states[action_name] = list_val
        list_val.append(spec)
        
    def add_await_state(self, action_name: str, spec: AwaitStateSpec):
        if self._await_states is None:
            self._await_states = {}
        self._await_states[action_name] = spec
        
        list_val = self._await_states.get(action_name)
        if list_val is None:
            list_val = []
            self._await_states[action_name] = list_val
        list_val.append(spec)
        
    def can_await_states(self) -> bool:
        return len(self._await_states > 0)
        
    def match_action(self, task_action: TaskAction, task_cxt: TaskRunContext) -> ChildNestingSpec:
        children = None if self._nested_children is None else self._nested_children.get(task_action.action_name)
        if children is None:
            return None
        
        for child_spec in children:
            if child_spec.match_action(task_action, task_cxt):
                return child_spec
        return None
    
    def match_notify_state(self, task_action: TaskAction, task_cxt: TaskRunContext) -> NotifyStateSpec:
        notifiers = None if self._notify_states is None else self._notify_states.get(task_action.action_name)
        if notifiers is None:
            return None
        
        for notifier in notifiers:
            if notifier.match_notifier(task_action, task_cxt):
                return notifier
        return None
        
    def match_await_state(self, task_action: TaskAction, task_cxt: TaskRunContext) -> AwaitStateSpec:
        receivers = None if self._await_states is None else self._await_states.get(task_action.action_name)
        if receivers is None:
            return None
        
        for receiver in receivers:
            if receiver.match_can_receive(task_action, task_cxt):
                return receiver
        return None
    
    def can_nest_action(self, action_name: str) -> bool:
        return self._nested_children.get(action_name) is not None if self._nested_children is not None else False
    
    def can_notify_state(self, action_name: str) -> bool:
        return self._notify_states.get(action_name) is not None if self._notify_states is not None else False
    
    def can_await_state(self, action_name: str) -> bool:
        return self._await_states.get(action_name) is not None if self._await_states is not None else False
        
    def get_nested_children(self) -> Iterable[List[ChildNestingSpec]]:
        return self._nested_children.values() if self._nested_children is not None else None