import os
import re
import itertools
import threading
import _thread
import types
import importlib
from re import compile
from numbers import Number
from typing import List, Callable, Tuple, Any, Type, Dict
import numpy as np
import random

class UniqueIdGenerator:
    def __init__(self, start: int = 0):
        self._counter = itertools.count(start)
        self._lock = threading.Lock()
        self._last = 0

    def generate_id(self) -> int:
        with self._lock:
            gen_id = next(self._counter)
            return gen_id
    
    def last_id(self) -> int:
        return(self._last) 

def resolve_env_variables(input_string: str) -> str:
    def replacer(match):
        var_name = match.group(1)
        return os.getenv(var_name, "")
    
    pattern = compile(r'env\.([A-Za-z_][A-Za-z0-9_]*)')
    return pattern.sub(replacer, input_string)

def clip(value: Number, range_max: Number, range_min: Number) -> Number:
    if value > range_max:
        value = range_max
    if value < range_min:
        value = range_min
    return value

class VersionedSerializable:
    def __getstate__(self) -> dict:
        cls = type(self)
        if "version" not in cls.__dict__:
            raise AttributeError(
                f"{cls.__name__} must explicitly define its own class-level `version`."
            )
            
        state = self._custom_getstate()
        state["_version"] = cls.version
        return state

    def __setstate__(self, state: dict):
        saved_version = state.get("_version", 1)
        self._custom_setstate(state)
        if saved_version < type(self).version:
            self._upgrade_from_version(saved_version)

    def _upgrade_from_version(self, old_version: int):
        raise NotImplementedError(
            f"{type(self).__name__} must implement `_upgrade_from_version()` to support versioned upgrades."
        )
    
    def _custom_getstate(self) -> dict:
        return self.__dict__.copy()

    def _custom_setstate(self, state: dict):
        self.__dict__.update(state)
        
class SegmentTree(VersionedSerializable):
    version = 1
    
    MAX_SPECIFIER = "MAX"
    MIN_SPECIFIER = "MIN"
    SUM_SPECIFIER = "SUM"
    
    MAX_SPEC = (max, float("-inf"), True)
    MIN_SPEC = (min, float("+inf"), True)
    SUM_SPEC = (lambda a, b: a + b, 0, False)
    
    def __init__(self, data: List[Number] = None, start_index: int = 0, count: int = None, default_value: Number = 0,
        type_specifier: str = MIN_SPECIFIER) -> None:
        if count is None and data is None:
            raise ValueError("Both data and count cannot be absent")
        self._type_specifier = type_specifier
        spec: Tuple[Callable[[Number, Number], Number], Number, bool] = \
            self.MAX_SPEC if type_specifier == self.MAX_SPECIFIER else \
            (self.MIN_SPEC if type_specifier == self.MIN_SPECIFIER  else self.SUM_SPEC)
            
        count = count if count is not None else len(data)
        self.n: int = count
        
        self.reduction_fn = spec[0]
        self.identity = spec[1]
        self.same_as_default = spec[2]
        
        if data is None:
            if self.same_as_default:
                self.tree: List[Number] = [default_value] * (2 * self.n)  
                return
            else:
                self.tree: List[Number] = [self.identity] * (2 * self.n)
                for i in range(self.n):
                    self.tree[self.n + i] = default_value
        else:
            self.tree: List[Number] = [self.identity] * (2 * self.n)

            for i in range(self.n):
                self.tree[self.n + i] = data[start_index + i]

        for i in range(self.n - 1, 0, -1):
            self.tree[i] = self.reduction_fn(self.tree[2 * i], self.tree[2 * i + 1])
            

    def get(self, index: int) -> int:
        return self.tree[self.n + index]

    def set(self, index: int, value: Number) -> None:
        pos: int = self.n + index
        self.tree[pos] = value

        while pos > 1:
            pos //= 2
            self.tree[pos] = self.reduction_fn(self.tree[2 * pos], self.tree[2 * pos + 1])
            
    def aggregate(self) -> Number:
        return self.tree[1]

    def range(self, a: int, b: int) -> Number:
        if a == 0 and b == self.n - 1:
            return self.tree[1]
        
        a += self.n
        b += self.n
        result: Number = self.identity
        
        while a <= b:
            if a % 2 == 1:
                result = self.reduction_fn(result, self.tree[a])
                a += 1
            if b % 2 == 0:
                result = self.reduction_fn(result, self.tree[b])
                b -= 1
            a //= 2
            b //= 2

        return result
    
    def __len__(self):
        return self.n
    
    def _custom_getstate(self)->dict:
        excluded_keys = {"reduction_fn", "identity", "same_as_default"}
        ret_dict = {k: v for k, v in self.__dict__.items() if k not in excluded_keys}
        return ret_dict
    
    def _custom_setstate(self, state:dict):
        self.__dict__.update(state)
        
        spec = self.MIN_SPEC if self._type_specifier ==  self.MIN_SPECIFIER else self.MAX_SPEC
        self.reduction_fn = spec[0]
        self.identity = spec[1]
        self.same_as_default = spec[2]
    
class SegmentTreeWithIndex(VersionedSerializable):
    version = 1
    
    @staticmethod
    def max_with_index(a: Tuple[Number, int], b: Tuple[Number, int]) -> Tuple[Number, int]:
        if a[0] > b[0]:
            return a
        elif b[0] > a[0]:
            return b
        else:
            return a if a[1] < b[1] else b
    
    @staticmethod
    def min_with_index(a: Tuple[Number, int], b: Tuple[Number, int]) -> Tuple[Number, int]:
        if a[0] < b[0]:
            return a
        elif b[0] < a[0]:
            return b
        else:
            return a if a[1] < b[1] else b
    
    MIN_SPECIFIER: str = "MIN"
    MAX_SPECIFIER: str = "MAX"
    MAX_SPEC = (max_with_index, (float("-inf") -1), True)
    MIN_SPEC = (min_with_index, (float("+inf"), -1), True)
    
    def __init__(self, data: List[Number] = None, start_index: int = 0, count: int = None, default_value: Number = 0,
        type_specifier: str = MIN_SPECIFIER) -> None:
        if count is None and data is None:
            raise ValueError("Both data and count cannot be absent")
        
        self._type_specifier = type_specifier
        spec: Tuple[Callable[[Tuple[Number, int], Tuple[Number, int]], Tuple[Number, int]], bool] = \
            self.MIN_SPEC if type_specifier ==  self.MIN_SPECIFIER else self.MAX_SPEC
        count = count if count is not None else len(data)
        self.n: int = count
        
        self.reduction_fn = spec[0]
        self.identity = spec[1]
        self.same_as_default = spec[2]
        self.tree: List[Tuple[Number, int]] = None
        if data is None:
            if self.same_as_default:
                self.tree = [(default_value, -1)] * (2 * self.n)  
                for i in range(self.n):
                    self.tree[self.n + i] = (default_value, i)
                return
            else:
                self.tree = [self.identity] * (2 * self.n)
                for i in range(self.n):
                    self.tree[self.n + i] = (default_value, i)
        else:
            self.tree = [self.identity] * (2 * self.n)

            for i in range(self.n):
                self.tree[self.n + i] = (data[start_index + i], i)

        for i in range(self.n - 1, 0, -1):
            self.tree[i] = self.reduction_fn(self.tree[2 * i], self.tree[2 * i + 1])
            

    def get(self, index: int) -> Number:
        return self.tree[self.n + index][0]

    def set(self, index: int, value: Number) -> None:
        pos: int = self.n + index
        self.tree[pos] = (value, index)

        while pos > 1:
            pos //= 2
            self.tree[pos] = self.reduction_fn(self.tree[2 * pos], self.tree[2 * pos + 1])
            
    def aggregate(self) -> Tuple[Number, int]:
        return self.tree[1]

    def range(self, a: int, b: int) -> Tuple[Number, int]:
        if a == 0 and b == self.n - 1:
            return self.tree[1]
        
        a += self.n
        b += self.n
        result: Tuple[Number, int] = self.identity
        
        while a <= b:
            if a % 2 == 1:
                result = self.reduction_fn(result, self.tree[a])
                a += 1
            if b % 2 == 0:
                result = self.reduction_fn(result, self.tree[b])
                b -= 1
            a //= 2
            b //= 2

        return result
    
    def __len__(self):
        return self.n
    
    def _custom_getstate(self)->dict:
        excluded_keys = {"reduction_fn", "identity", "same_as_default"}
        ret_dict = {k: v for k, v in self.__dict__.items() if k not in excluded_keys}
        return ret_dict
    
    def _custom_setstate(self, state:dict):
        self.__dict__.update(state)
        
        spec = self.MIN_SPEC if self._type_specifier ==  self.MIN_SPECIFIER else self.MAX_SPEC
        self.reduction_fn = spec[0]
        self.identity = spec[1]
        self.same_as_default = spec[2]
        
def is_str_valid(value: Any) -> None:
    return value is not None and isinstance(value, str) and value.strip()
    
def to_valid_str(value: str, default_value: str = None):
    value = value.strip() if value is not None else None
    return value if value else default_value

def to_valid_list(list_value: List[Any]) -> List[Any]:
    return list_value if len(list_value) > 0 else None

def is_obj_valid(value, class_type: Type[Any]):
    return value is not None and isinstance(value, class_type)

def to_file_name(simple_name: str, use_pascal_case: bool = False) -> str:
    if not is_str_valid(simple_name):
        raise ValueError("Absent/invalid input: " + simple_name)

    cleaned_name = re.sub(r'[^a-zA-Z0-9\s]', '', simple_name)
    cleaned_name = re.sub(r'_+', '_', cleaned_name)
    cleaned_name = cleaned_name.strip('_')
    words = cleaned_name.split()

    if not words:
        raise ValueError("Absent/invalid input: " + simple_name)

    camel_cased_name = "".join(word.capitalize() for word in words)
    if camel_cased_name[0].isdigit():
        camel_cased_name = "d" + camel_cased_name
        
    if not use_pascal_case:
        camel_cased_name =  camel_cased_name[0].lower() + camel_cased_name[1:]
    return camel_cased_name

def ns_to_filepath(ns: str) -> str:
    if not is_str_valid(ns):
        raise ValueError("Absent/invalid namespace: " + ns)
    
    protocol_regex = r"^(http|https|ftp|sftp|file|ssh):\/\/"
    cleaned_string = re.sub(protocol_regex, "", ns, flags=re.IGNORECASE)
    
    tokens = [token.strip() for token in re.split(r'[:./]+', cleaned_string)
        if token.strip()
    ]

    if not tokens:
        raise ValueError("Absent/invalid namespace: " + ns)
    
    sanitized_tokens = []
    for token in tokens:
        safe_token = re.sub(r'[^a-zA-Z0-9_]', '_', token)
        safe_token = re.sub(r'_+', '_', safe_token)
        safe_token = safe_token.strip('_')
        if safe_token:
            sanitized_tokens.append(safe_token)
    if not sanitized_tokens:
        raise ValueError("Absent/invalid namespace: " + ns)
    
    camel_cased_name = "".join(word.capitalize() for word in sanitized_tokens)
    if camel_cased_name[0].isdigit():
        camel_cased_name = "d" + camel_cased_name
    camel_cased_name =  camel_cased_name[0].lower() + camel_cased_name[1:]
    return camel_cased_name

def file_to_str(filepath: str) -> str:
    with open(filepath, 'r', encoding='utf-8') as file:
        content = file.read()
    return content

def str_to_file(content: str, filepath: str) -> None:
    with open(filepath, 'w', encoding='utf-8') as file:
        file.write(content)
        
def dynamic_load_instance(module_name: str, class_name: str, arg_map: Dict[str, Any] = []) -> Any:
    module = importlib.import_module(module_name)
    if module is None:
        raise ValueError(f"Could not load dynamic module: {module_name}")
    
    gen_class = getattr(module, class_name)
    
    if gen_class is None:
        raise ValueError(f"Could not load dynamic class: {class_name} from module: {module_name}")
    
    instance = gen_class(**arg_map)
    return instance
    
_LOCK_TYPE = type(_thread.allocate_lock())

def find_locks(obj, path="root", visited=None):
    if visited is None:
        visited = set()

    obj_id = id(obj)
    if obj_id in visited:
        return
    visited.add(obj_id)

    if isinstance(obj, (str, bytes, bytearray,
                        int, float, complex,
                        bool, type(None))):
        return

    if isinstance(obj, _LOCK_TYPE):
        print(f"Found lock at: {path}")
        return

    if isinstance(obj, dict):
        for key, val in obj.items():
            find_locks(val, f"{path}[{key!r}]", visited)

    elif isinstance(obj, (list, tuple, set, frozenset)):
        for idx, item in enumerate(obj):
            find_locks(item, f"{path}[{idx}]", visited)

    else:
        if hasattr(obj, "__dict__"):
            for attr, val in vars(obj).items():
                find_locks(val, f"{path}.{attr}", visited)

        for attr in dir(obj):
            if attr.startswith("__") and attr.endswith("__"):
                continue
            if hasattr(obj, "__dict__") and attr in obj.__dict__:
                continue
            try:
                val = getattr(obj, attr)
            except Exception:
                continue
            if isinstance(val, (types.FunctionType,
                                types.MethodType,
                                types.BuiltinFunctionType)):
                continue
            find_locks(val, f"{path}.{attr}", visited)

def find_staticmethods(obj, path="root", visited=None):
    if visited is None:
        visited = set()

    obj_id = id(obj)
    if obj_id in visited:
        return
    visited.add(obj_id)

    if isinstance(obj, staticmethod):
        print(f"Found staticmethod at: {path}")
        return

    if isinstance(obj, (str, bytes, bytearray,
                        int, float, complex,
                        bool, type(None))):
        return

    if isinstance(obj, dict):
        for key, val in obj.items():
            find_staticmethods(val,     f"{path}[{key!r}]", visited)

    elif isinstance(obj, (list, tuple, set, frozenset)):
        for idx, item in enumerate(obj):
            find_staticmethods(item,    f"{path}[{idx}]",     visited)

    else:
        if hasattr(obj, "__dict__"):
            for attr, val in vars(obj).items():
                find_staticmethods(val, f"{path}.{attr}",      visited)

        for attr in dir(obj):
            if attr.startswith("__") and attr.endswith("__"):
                continue
            if hasattr(obj, "__dict__") and attr in obj.__dict__:
                continue
            try:
                val = getattr(obj, attr)
            except Exception:
                continue
            if isinstance(val, (types.FunctionType,
                                types.MethodType,
                                types.BuiltinFunctionType,
                                classmethod)):
                continue
            find_staticmethods(val,     f"{path}.{attr}",      visited)
    
class QTable:
    def __init__(self, num_state, init_value: float = 0.0):
        self._qtable = np.full(num_state, float(init_value))
        self._num_state = num_state
        
    def set(self, state, q_value):
        self._qtable[state] = q_value
        
    def get(self, state):
        return self._qtable[state]
    
    def max_state(self):
        max_index = int(np.argmax(self._qtable))
        return max_index
    
    def sample(self):
        return random.randint(0, self._num_state - 1)
        
    def dump(self, decimals: int = 3) -> str:
        with np.printoptions(precision=decimals, suppress=True, floatmode='fixed'):
            return np.array2string(self._qtable, separator=', ')
    
    def __len__(self):
        return(self._num_state) 

def getNameFromFunction(functionText):
    pattern = re.compile(r'def\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(', re.MULTILINE)
    match = pattern.search(functionText)
    if match is not None:
        return match.group(1)
    else:
        return None
