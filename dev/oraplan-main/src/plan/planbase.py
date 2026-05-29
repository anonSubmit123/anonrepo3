from typing import Dict, List, Any, Iterator, Set, Optional, Tuple, Iterable
from collections import defaultdict, deque
from baseproc.proccontext import TaskAction
from baseproc.basetask import StateContext, TaskStatistics, KvpStateContext
from baseproc.basestore import FactAtom
from common.excp import InvalidStateException
import time
import numbers
from common.util import to_valid_list
from baseproc.levelmgr import ParsedDomain, DerivedPredicateSpec

class Cacheable:
    def __init__(self, key):
        self.key = key
    
    def to_cache_key(self):
        return(self.key)
    
    def __hash__(self):
        return hash(self.key)
    
    def __eq__(self, other):
        if self is other:
            return True
        if not isinstance(other, Cacheable):
            return NotImplemented
        return self.key == other.key
    
class ArtifactCache:
    def __init__(self):
        self._cache = {}
        
    def add_item(self, item: Cacheable) -> Cacheable:
        self._cache[item.to_cache_key()] = item
        return item
        
    def get_item(self, key: Any) -> Cacheable:
        return self._cache.get(key)
    
    def check_item(self, key: Any) -> bool:
        return key in self._cache
    
    def __iter__(self) -> Iterator[Any]:
        return iter(self._cache.values())
    
class FactAtomIndexManager:
    def __init__(self):
        self.by_predicate: Dict[str, Set[FactAtom]] = defaultdict(set)
        self.by_position: Dict[str, List[Dict[str, Set[FactAtom]]]] = {}
        self.by_object: Dict[Any, Set[FactAtom]] = defaultdict(set)

    def reset(self):
        self.by_predicate = defaultdict(set)
        self.by_position = {}
        self.by_object = defaultdict(set)
        
    def add_atom(self, atom: FactAtom) -> None:
        pred = atom.get_predicate()
        args = atom.arguments()
        
        self.by_predicate[pred].add(atom)

        if pred not in self.by_position:
            arity = atom.arity()
            self.by_position[pred] = [defaultdict(set) for _ in range(arity)]
            
        for i, val in enumerate(args):
            self.by_position[pred][i][val].add(atom)
         
        for object_arg in args:
            self.by_object[object_arg].add(atom)

    def remove_atom(self, atom: FactAtom) -> None:
        pred = atom.get_predicate()
        args = atom.arguments()
        if atom in self.by_predicate[pred]:
            self.by_predicate[pred].remove(atom)
        for i, val in enumerate(args):
            bucket = self.by_position[pred][i].get(val)
            if bucket and atom in bucket:
                bucket.remove(atom)
                
        for object_arg in args:
            atom_bucket = self.by_object.get(object_arg)
            if atom_bucket and atom in atom_bucket:
                atom_bucket.remove(atom)
                
                if not atom_bucket:
                    del self.by_object[object_arg]
                    
    def fetch_candidates(self, pattern: FactAtom) -> List[FactAtom]:
        pred = pattern.get_predicate()
        if pred not in self.by_predicate:
            return None
        grounded = pattern.grounded_args()
        if not grounded:
            return to_valid_list(list(self.by_predicate[pred]))
        
        candidate_sets: List[Set[FactAtom]] = []
        for i, val in grounded:
            bucket = self.by_position[pred][i].get(val, set())
            candidate_sets.append(bucket)
        if len(candidate_sets) <= 1:
            return to_valid_list(list(candidate_sets[0])) if candidate_sets else None
        
        base_set = min(candidate_sets, key=len)
        results: List[FactAtom] = []
        for atom in base_set:
            match = True
            for i, val in grounded:
                if atom[i+1] != val:
                    match = False
                    break
            if match:
                results.append(atom)
        return to_valid_list(results)
    
    def reachable_facts(self, initial_objs: Set[Any]) -> Tuple[Set[FactAtom], Set[Any]]:
        seen_objs: Set[Any] = set(initial_objs)
        seen_atoms: Set[FactAtom] = set()
        queue = deque(initial_objs)

        while queue:
            obj = queue.popleft()
            for atom in self.by_object.get(obj, ()):
                if atom not in seen_atoms:
                    seen_atoms.add(atom)
                    
                    atom_args = atom.arguments()
                    for arg in atom_args:
                        if isinstance(arg, numbers.Number):
                            continue
                        if arg not in seen_objs:
                            seen_objs.add(arg)
                            queue.append(arg)

        return seen_atoms, seen_objs
 
class AdjunctContext:
    def __init__(self, owner: "PlanStateContext", derived_predicate_spec: DerivedPredicateSpec, order_index: int) -> None:
        self._derived_predicate_def = derived_predicate_spec.derived_predicate_def
        self._predicate: str = derived_predicate_spec.predicate
        self._depend_preds: Set[str] = derived_predicate_spec.depend_preds
        self._owner = owner
        self.order_index: Optional[int] = order_index
        self._atoms: Set[FactAtom] = set()
        self._index = FactAtomIndexManager()
        self._dirty: bool = True
        self._dirty_depend_set: Set[str] = self._depend_preds.copy()
        
    @property
    def index(self) -> FactAtomIndexManager:
        return self._index
    
    @property
    def predicate(self) -> str:
        return self._predicate
    
    @property
    def depend_predicates(self) -> Set[str]:
        return self._depend_preds
    
    def reset(self):
        self._atoms = set()
        self._index.reset()
        self._dirty_depend_set = set()
        self._dirty = False
    
    def add_fact(self, new_atom: FactAtom) -> bool:
        if new_atom not in self._atoms:
            self._atoms.add(new_atom)
            self._index.add_atom(new_atom)
            return True
        return False
            
    def remove_fact(self, rem_atom: FactAtom) -> bool:
        if rem_atom in self._atoms:
            self._atoms.remove(rem_atom)
            self._index.remove_atom(rem_atom)
            return True
        return False
            
    def _rebuild(self):
        from plan.planner import PlannerFactory
        PlannerFactory.get_instance().get_planning_processor().rebuild_adjunct_context(self._owner.get_affected_adjuncts(self))
        self._dirty = False
        self._dirty_depend_set.clear()
    
    def has_fact(self, atom: FactAtom) -> bool:
        if self._dirty:
            self._rebuild()
        if atom in self._atoms:
            return True
        return False
    
    def fetch_facts(self, pattern: FactAtom) -> List[FactAtom]:
        if self._dirty:
            self._rebuild()
        return self._index.fetch_candidates(pattern)
    
    def reachable_facts(self, initial_objs: Set[Any]) -> Tuple[Set[FactAtom], Set[Any]]:
        if self._dirty:
            self._rebuild()
        return self._index.reachable_facts(initial_objs)
    
    def get_all_facts(self) -> Iterable[FactAtom]:
        if self._dirty:
            self._rebuild()
        return self._atoms
    
    def get_own_facts(self) -> Iterable[FactAtom]:
        if self._dirty:
            self._rebuild()
        return self._atoms
    
    def invalidate(self, dirty_predicate: str):
        self._dirty = True
        self._dirty_depend_set.add(dirty_predicate)
        
    def is_dirty(self, depend_predicate: str = None) -> bool:
        return depend_predicate in self._dirty_depend_set if depend_predicate is not None else self._dirty
    
    def __hash__(self) -> int:
        return id(self)

    def __eq__(self, other: object) -> bool:
        return self is other
           
class PlanStateContext(StateContext):
    version = 1
    
    def __init__(self, desc, parent = None):
        super().__init__(desc, parent)
        self._atoms: Set[FactAtom] = set()
        self._index = FactAtomIndexManager()
        self._functions: Dict[FactAtom, float] = {}
        self._function_index: FactAtomIndexManager = FactAtomIndexManager()
        self._object_types: Dict[str, Set[str]] = defaultdict(set)
        self._all_objects: Set[str] = None
        self._adjuncts: List[AdjunctContext] = None
        self._depend_adjunct_map: Dict[str, List[AdjunctContext]] = None
        self._result_adjunct_map: Dict[str, List[AdjunctContext]] = None
        
    @property
    def object_types(self):
        return self._object_types
    
    @property
    def all_objects(self):
        return self._all_objects
    
    @property
    def index(self):
        return self._index
        
    def add_objects(self, object_map: Dict[str, Set[str]]) -> None:
        for type_name, object_set in object_map.items():
            self._object_types[type_name].update(object_set)
            
        if self._all_objects is None:
            self._all_objects = set()
            
        for obj_set in self._object_types.values():
            self._all_objects.update(obj_set)
        
    def add_fact(self, new_atom: FactAtom) -> None:
        if new_atom not in self._atoms:
            self._atoms.add(new_atom)
            self._index.add_atom(new_atom)
            if self._adjuncts:
                affected_pred = new_atom.get_predicate() 
                adjuncts = self._depend_adjunct_map.get(affected_pred)
                if adjuncts is not None:
                    for adjunct in adjuncts:
                        adjunct.invalidate(affected_pred)

    def remove_fact(self, rem_atom: FactAtom) -> None:
        if rem_atom in self._atoms:
            self._atoms.remove(rem_atom)
            self._index.remove_atom(rem_atom)
            
            if self._adjuncts:
                affected_pred = rem_atom.get_predicate() 
                adjuncts = self._depend_adjunct_map.get(affected_pred)
                if adjuncts is not None:
                    for adjunct in adjuncts:
                        adjunct.invalidate(affected_pred)
                
    def has_fact(self, atom: FactAtom) -> bool:
        if atom in self._atoms:
            return True
        if self._adjuncts:
            query_pred = atom.get_predicate() 
            adjuncts = self._result_adjunct_map.get(query_pred)
            if adjuncts is not None:
                for adjunct in adjuncts:
                    if adjunct.has_fact(atom):
                        return True
        if self.parent:
            return self.parent.has_fact(atom)
        return False
    
    def fetch_facts(self, pattern: FactAtom) -> List[FactAtom]:
        got_facts = self._index.fetch_candidates(pattern)
        if self._adjuncts:
            query_pred = pattern.get_predicate() 
            adjuncts = self._result_adjunct_map.get(query_pred)
            if adjuncts is not None:
                for adjunct in adjuncts:
                    adjunct_facts = adjunct.fetch_facts(pattern)
                    if adjunct_facts is not None:
                        got_facts = got_facts or []
                        got_facts.append(adjunct_facts)
        if got_facts or self.parent is None: 
            return got_facts
        return  self.parent.fetch_facts(pattern)
        
    def reachable_facts(self, initial_objs: Set[Any]) -> Tuple[Set[FactAtom], Set[Any]]:
        result = self._index.reachable_facts(initial_objs)
        if self._adjuncts:
            curr_facts = set()
            if result[0]:
                curr_facts.update(result[0])
            curr_objects = set()
            if result[1]:
                curr_objects.update(result[1])
            
            for adjunct in self._adjuncts:
                adjunct_facts = adjunct.reachable_facts(curr_objects)
                if adjunct_facts[0]:
                    curr_facts.update(adjunct_facts[0])
                    curr_objects.update(adjunct_facts[1])
            result = (curr_facts, curr_objects)
        if result or self.parent is None: 
            return result
        return  self.parent.reachable_facts(initial_objs)
    
    def get_all_facts(self) -> Iterable[FactAtom]:
        if self.parent is not None:
            parent_set = self.parent.get_all_facts()
            if parent_set:
                ret_set = set()
                ret_set.update(parent_set)
                ret_set.update(self.get_own_facts())
                return ret_set
        return self.get_own_facts()
    
    def get_own_facts(self) -> Iterable[FactAtom]:
        if not self._adjuncts:
            return self._atoms
        
        ret = self._atoms.copy()
        for adjunct in self._adjuncts:
            ret.update(adjunct.get_own_facts()) 
        return ret
    
    def add_function(self, new_fn: Tuple[FactAtom, float]) -> None:
        new_fact = new_fn[0]
        new_value = new_fn[1]
        if new_fact not in self._functions:
            self._functions[new_fact] = new_value
            self._function_index.add_atom(new_fact)
        else:
            self._functions[new_fact] = new_value
    
    def remove_function(self, rmfn_fact: FactAtom) -> None:
        if rmfn_fact in self._functions:
            self._functions.remove(rmfn_fact)
            self._function_index.remove_atom(rmfn_fact)
                
    def get_function(self, getfn_fact: FactAtom) -> float:
        return self._functions.get(getfn_fact)
    
    def has_function(self, fn_fact: FactAtom) -> bool:
        return fn_fact in self._functions
            
    def fetch_functions(self, pattern: FactAtom) -> List[FactAtom]:
        got_facts = self._function_index.fetch_candidates(pattern)
        if got_facts or self.parent is None: 
            return got_facts
        return  self.parent.fetch_functions(pattern)
    
    def reachable_functions(self, initial_objs: Set[Any]) -> Tuple[Set[FactAtom], Set[Any]]:
        result = self._function_index.reachable_facts(initial_objs)
        if result or self.parent is None: 
            return result
        return  self.parent.reachable_functions(initial_objs)
    
    def get_item(self, item_id: str) -> Any:
        return self.parent.get_item(item_id) if self.parent is not None else None
    
    def set_item(self, item_id: str, item_value: Any) -> Any:
        if self.parent is not None:
            return self.parent.set_item(item_id, item_value)
        else:
            raise InvalidStateException("App-Store does not support set_item")
    
    def set_item_default(self, item_id: str, item_value: Any) -> Any:
        if self.parent is not None:
            return self.parent.set_item_default(item_id, item_value)
        else:
            raise InvalidStateException("App-Store does not support set_item_default")
    
    def populate_child_context(self, desc: str, affected_objects: Set[Any],
        child_context: StateContext) -> Tuple['PlanStateContext', Set[FactAtom]]:
        init_set, _ = self._index.reachable_facts(affected_objects)
        for fact_atom in init_set:
            child_context.add_fact(fact_atom)
        return init_set

    def absorb_child_changes(self, child_context: StateContext, init_set: Set[FactAtom]) -> None:
        new_set = child_context.get_own_facts()

        to_remove = init_set - new_set
        to_add    = new_set - init_set

        for fact_atom in to_remove:
            self.remove_fact(fact_atom)
        for fact_atom in to_add:
            self.add_fact(fact_atom)
        
    def add_adjuncts(self, derived_predicates: List[DerivedPredicateSpec]): 
        self._adjuncts = []
        self._result_adjunct_map = defaultdict(list)
        self._depend_adjunct_map = defaultdict(list)
        
        for index, derived_predicate_spec in enumerate(derived_predicates):
            adjunct_cxt = AdjunctContext(self, derived_predicate_spec, index)
            self._adjuncts.append(adjunct_cxt)
            self._result_adjunct_map[adjunct_cxt.predicate].append(adjunct_cxt)
            for dep_pred in adjunct_cxt.depend_predicates:
                self._depend_adjunct_map[dep_pred].append(adjunct_cxt)
                
    def get_affected_adjuncts(self, start: AdjunctContext) -> List[AdjunctContext]:
        affected_predicate_set = set()
        update_affected_predicate_set = set()
        update_affected_predicate_set.add(start.predicate)
        
        while len(update_affected_predicate_set) > 0:
            before_len = len(affected_predicate_set)
            affected_predicate_set.update(update_affected_predicate_set)
            if len(affected_predicate_set) <= before_len:
                break
            
            update_affected_predicate_set.clear()
            for affected_predicate in affected_predicate_set:
                update_affected_predicate_set.update([depend_adjunct.predicate for depend_adjunct in self._depend_adjunct_map[affected_predicate]])
        
        update_affected_predicate_set.clear()
        self._add_dependents(affected_predicate_set, update_affected_predicate_set)
        while len(update_affected_predicate_set) > 0:
            before_len = len(affected_predicate_set)
            
            new_predicate_set =  update_affected_predicate_set - affected_predicate_set
            affected_predicate_set.update(update_affected_predicate_set)
            if len(affected_predicate_set) <= before_len:
                break
            
            update_affected_predicate_set.clear()
            self._add_dependents(new_predicate_set, update_affected_predicate_set)
            
        all_clean = True
        for affected_pedicate in affected_predicate_set:
            affected_adjunct_list = self._result_adjunct_map.get(affected_pedicate)
            for affected_adjunct_cxt in affected_adjunct_list:
                if affected_adjunct_cxt is not None and affected_adjunct_cxt.is_dirty():
                    all_clean = False
                    break
            if not all_clean:
                break
            
        if all_clean:
            return []
                    
        ret = []
        for affected_predicate in affected_predicate_set:
            ret.extend(self._result_adjunct_map[affected_predicate])
            
        return ret if len(ret) == 1 else sorted(ret, key=lambda adj: adj.order_index if adj.order_index is not None else float('inf'))
    
    def _add_dependents(self, affected_predicate_set, update_affected_predicate_set):
        for affected_predicate in affected_predicate_set:
            depend_predicates = [depend_predicate for adjunct_cxt in self._result_adjunct_map[affected_predicate] for depend_predicate in adjunct_cxt.depend_predicates]
            update_affected_predicate_set.update(depend_predicates) 
                        
    def _custom_getstate(self)->dict:
        excluded_keys = {"index"}
        ret_dict = {k: v for k, v in self.__dict__.items() if k not in excluded_keys}
        return ret_dict
        
    def _custom_setstate(self, state:dict):
        self.__dict__.update(state)
        self._index = FactAtomIndexManager()
        for atom in self._atoms:
            self._index.add_atom(atom)
            
class CompositeStateContext(StateContext):
    def __init__(self, desc: str, parent: StateContext = None):
        self._kvp_context = KvpStateContext(desc, parent)
        self._plan_context = PlanStateContext(desc, parent)
        
    def get_item(self, item_id: str) -> Any:
        return self._kvp_context.get_item(item_id)
    
    def set_item(self, item_id: str, item_value: Any) -> Any:
        return self._kvp_context.set_item(item_id, item_value)
    
    def set_item_default(self, item_id: str, item_value: Any) -> Any:
        return self._kvp_context.set_item_default(item_id, item_value)
    
    def add_objects(self, object_map: Dict[str, Set[str]]) -> None:
        self._plan_context.add_objects(object_map)
        
    def add_fact(self, new_atom: FactAtom) -> None:
        self._plan_context.add_fact(new_atom)

    def remove_fact(self, rem_atom: FactAtom) -> None:
        self._plan_context.remove_fact(rem_atom)
                
    def has_fact(self, atom: FactAtom) -> bool:
        return self._plan_context.has_fact(atom)
    
    def fetch_facts(self, pattern: FactAtom) -> List[FactAtom]:
        return self._plan_context.fetch_facts(pattern)
        
    def reachable_facts(self, initial_objs: Set[Any]) -> Tuple[Set[FactAtom], Set[Any]]:
        return self._plan_context.reachable_facts(initial_objs)
    
    def get_all_facts(self) -> Iterable[FactAtom]:
        if self.parent is None:
            return self._plan_context.get_all_facts()
        
    def get_own_facts(self) -> Iterable[FactAtom]:
        return self._plan_context.get_own_facts()

    def add_function(self, new_fn: Tuple[FactAtom, float]) -> None:
        self._plan_context.add_function(new_fn)
    
    def remove_function(self, rmfn_fact: FactAtom) -> None:
        self._plan_context.remove_function(rmfn_fact)
                
    def get_function(self, getfn_fact: FactAtom) -> float:
        return self._plan_context.get_function(getfn_fact)
            
    def has_function(self, fn_fact: FactAtom) -> bool:
        return self._plan_context.has_function(fn_fact)
        
    def fetch_functions(self, pattern: FactAtom) -> List[FactAtom]:
        return self._plan_context.fetch_functions(pattern)
    
    def reachable_functions(self, initial_objs: Set[Any]) -> Tuple[Set[FactAtom], Set[Any]]:
        return self._plan_context.reachable_functions(initial_objs)
    
class ActionSpec:
    def __init__(self):
        pass
       
PlanAction = TaskAction             

class PlanStatistics(TaskStatistics):
    def __init__(self):
        self._action_times: List[float] = None
        self._total_time: float  = 0
        self._total_nopause_time: float  = None
        self._action_id: int = -1
        self._action_start: float = -1
        self._action_time: float = 0
        self._cumulative_action_time = 0
        self._plan_start: float = -1
        self._completed = False
    
    def get_total_elapsed(self) -> float:
        return self._total_time if self._completed else None
    
    def get_nopause_total_elapsed(self) -> float:
        if self._total_nopause_time is None and self._completed:
            _total_nopause_time = 0
            for _action_time in self._action_times:
                _total_nopause_time = _total_nopause_time + _action_time
            self._total_nopause_time = _total_nopause_time
        return(self._total_nopause_time)
    
    def get_action_time(self, at_index) -> float:
        return self._action_times[at_index] if len(self._action_times) > at_index else None
    
    def get_action_times(self) -> List[float]:
        return self._action_times if self._completed else None 
            
    def _reset_stats(self):
        self._action_times = []
        self._total_time = 0
        self._total_nopause_time = None
        self._plan_start = -1
        self._completed = False
        self._reset_action()
        
    def _reset_action(self):
        self._action_id = -1
        self._action_start = -1
        self._action_time = 0
        
    def start_task(self):
        self._reset_stats()
        self._plan_start = time.perf_counter()
    
    def complete_task(self):
        end_time = time.perf_counter()
        self._total_time = self._total_time + (end_time - self._plan_start)
        self._cumulative_action_time = 0
        for action_time in self._action_times:
            self._cumulative_action_time = self._cumulative_action_time + action_time
        self._completed = True
    
    def get_cumulative_action_time(self):
        return self._cumulative_action_time
    
    def get_number_action(self):
        return len(self._action_times)
        
    def start_action(self, action_spec):
        _action_id = id(action_spec)
        if _action_id != self._action_id:
            self._reset_action()
            
        self._action_start = time.perf_counter()
        self._action_id = _action_id
    
    def pause_action(self, action_spec):
        if action_spec.id() == self._action_id:
            end_time = time.perf_counter()
            self._action_time = self._action_time + (end_time - self._action_start)
         
    def complete_action(self, action_spec):
        if id(action_spec) == self._action_id:
            end_time = time.perf_counter()
            self._action_time = self._action_time + (end_time - self._action_start)
            self._action_times.append(self._action_time)
            self._action_id = -1 
            
    def get_total_time(self):
        return self._cumulative_action_time
    
    def get_is_valid(self):
        return len(self._action_times) > 0

class PlanningProcessor:
    def load_domain(self, domain_pddl_path: str) -> ParsedDomain:
        raise NotImplementedError("PlanningProcessor.load_domain not implemented")
    
    def store_domain(self, parsed_domain: ParsedDomain, domain_pddl_path: str) -> None:
        raise NotImplementedError("PlanningProcessor.store_domain not implemented")
        
    def load_problem(self, problem_pddl_path: str, parsed_domain: ParsedDomain, statecxt: StateContext) -> Any:
        raise NotImplementedError("PlanningProcessor.load_problem not implemented")
    
    def store_problem(self, problem: Any, problem_pddl_path: str) -> None:
        raise NotImplementedError("PlanningProcessor.store_problem not implemented")
    
    def build_action_processor(self, parsed_domain: ParsedDomain) -> "ActionProcessor":
        raise NotImplementedError("PlanningProcessor.build_action_processor not implemented")
        
    def rebuild_adjunct_context(self, adjunt_contexts: List[AdjunctContext]):
        raise NotImplementedError("PlanningProcessor.build_action_processor not implemented")
    
    def parse_condition(self, cond_str: str) -> Any:
        raise NotImplementedError("PlanningProcessor.parse_condition not implemented")
    
class ActionProcessor:
    def is_ready(self, action_args: List[str], action_def: Any, state_cxt: StateContext) -> bool:
        raise NotImplementedError("ActionProcessor.is_ready not implemented")
    
    def propagate_effects(self, action_args: List[str], action_def: Any, state_cxt: StateContext, stage: str = None):
        raise NotImplementedError("ActionProcessor.propagate_effects not implemented")
    
    def eval_condition(self, cond_def: Any, state_cxt: StateContext, var_map: Dict[str, str] = None) -> bool:
        raise NotImplementedError("ActionProcessor.is_ready not implemented")