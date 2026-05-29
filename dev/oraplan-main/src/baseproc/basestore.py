from typing import Dict, List, Any, Tuple, Set, Iterable

class FactAtom(tuple):

    def __new__(cls, *elements: Any) -> "FactAtom":
        return super(FactAtom, cls).__new__(cls, elements)

    def get_predicate(self) -> Any:
        return self[0]
    
    def atom_at(self, predicate: str = None, arg_map: Dict[int, Any] = None) -> "FactAtom":
        new_elements = list(self)

        if predicate is not None:
            new_elements[0] = predicate
            
        if arg_map is not None:
            for i, value in arg_map.items():
                if i < 0:
                    raise IndexError(f"Element cannot have negative index: {i}")
                new_elements[i + 1] = value
        return FactAtom(*new_elements)

    def get_argument_at(self, index: int) -> Any:
        return self[index + 1]

    def arity(self) -> int:
        return len(self) - 1

    def arguments(self) -> Tuple[Any, ...]:
        return tuple(self[1:])
    
    def var_args(self) -> List[Tuple[int, str]]:
        var_args = []
        for i, val in enumerate(self[1:]):
            if isinstance(val, str) and val.startswith('?'):
                var_args.append((i, val))
        return self._var_args
    
    def grounded_args(self) -> List[Tuple[int, str]]:
        ground_args = []
        for i, val in enumerate(self[1:]):
            if not isinstance(val, str) or not val.startswith('?'):
                ground_args.append((i, val))
        return ground_args

    def is_grounded(self) -> bool:
        result = self.var_args()
        return len(result) == 0
        
    def __repr__(self) -> str:
        inner = " ".join(str(x) for x in self)
        return f"({inner})"

    def __eq__(self, other: Any) -> bool:
        return super().__eq__(other)

    def __hash__(self) -> int:
        return super().__hash__()
    
class AppContextStore:
        def get_item(self, item_id: str) -> Any:
            raise NotImplementedError("AppContextStore.get_item not implemented")
        
        def set_item(self, item_id: str, item_value: Any) -> Any:
            raise NotImplementedError("AppContextStore.set_item not implemented")
        
        def set_item_default(self, item_id: str, item_value: Any) -> Any:
            raise NotImplementedError("AppContextStore.set_item not implemented")
        
        def add_objects(self, object_map: Dict[str, Set[str]]) -> None:
            raise NotImplementedError("AppContextStore.add_objects not implemented")
            
        def add_fact(self, new_atom: FactAtom) -> None:
            raise NotImplementedError("AppContextStore.add_facts not implemented")
    
        def remove_fact(self, rem_atom: FactAtom) -> None:
            raise NotImplementedError("AppContextStore.remove_fact not implemented")
                    
        def has_fact(self, atom: FactAtom) -> bool:
            raise NotImplementedError("AppContextStore.has_fact not implemented")
        
        def fetch_facts(self, pattern: FactAtom) -> List[FactAtom]:
            raise NotImplementedError("AppContextStore.fetch_facts not implemented")
        
        def reachable_facts(self, initial_objs: Set[Any]) -> Tuple[Set[FactAtom], Set[Any]]:
            raise NotImplementedError("AppContextStore.reachable_facts not implemented")
        
        def get_all_facts(self) -> Iterable[FactAtom]:
            raise NotImplementedError("AppContextStore.fetch_facts not implemented")
        
        def get_own_facts(self) -> Iterable[FactAtom]:
            raise NotImplementedError("AppContextStore.fetch_facts not implemented")
        
        def add_function(self, new_fn: Tuple[FactAtom, float]) -> None:
            raise NotImplementedError("AppContextStore.add_function not implemented")
        
        def remove_function(self, rmfn_fact: FactAtom) -> None:
            raise NotImplementedError("AppContextStore.remove_function not implemented")
                    
        def get_function(self, getfn_fact: FactAtom) -> float:
            raise NotImplementedError("AppContextStore.get_function not implemented")
        
        def has_function(self, fn_fact: FactAtom) -> bool:
            raise NotImplementedError("AppContextStore.has_function not implemented")
                
        def fetch_functions(self, pattern: FactAtom) -> List[FactAtom]:
            raise NotImplementedError("AppContextStore.fetch_functions not implemented")
        
        def reachable_functions(self, initial_objs: Set[Any]) -> Tuple[Set[FactAtom], Set[Any]]:
            raise NotImplementedError("AppContextStore.reachable_functions not implemented")