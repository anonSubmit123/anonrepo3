import os, sys
from typing import Dict, List, Set, NewType, Callable, Any, Iterable
from common.util import resolve_env_variables,\
    VersionedSerializable, ns_to_filepath, is_str_valid, to_file_name,\
    find_locks, find_staticmethods
from jsonpath_ng import parse as jsonpath_parse
from spcoreutil.jsonCodec import get_first
from pathlib import Path
import shutil
import logging
import types
import pickle
from plan.problemparser import PddlProblemParser, ProblemTemplateGenerator
from collections import defaultdict
from baseproc.basetask import TaskRunContext
from _ast import Try

LOGGER_BASE = "oraplan.plan"
LOG_TAG = {"lgmod": "ORAPLAN"}
LOG_LINE = "\n\t"
logger = logging.getLogger(LOGGER_BASE)

CFG_DEFINITION_STORE = jsonpath_parse("$.definitionStore")

class DefinitionStore:
    def __init__(self, config):
        base_path = get_first(CFG_DEFINITION_STORE, config)
        self.base_path = resolve_env_variables(base_path)
        
        if not os.access(self.base_path, os.W_OK):
            raise PermissionError("Cannot access {} for for storing oraplan definitions".format(self.base_path))

LevelTransitionMap = NewType("LevelTransitionMap", None)
LevelOptimizer = NewType("LevelOptimizer", None)

class DerivedPredicateSpec:
    def __init__(self, derived_def: Any, predicate: str, depend_preds: Set[str]):
        self.derived_predicate_def: Any = derived_def
        self.predicate: str = predicate
        self.depend_preds: Set[str] = depend_preds

class ParsedDomain:
    def __init__(self):
        self.domain_pddl: str = None
        self.domain_name: str = None
        self.domain: Any = None
        self.actions: Dict[str, Any] = {}
        self.type_hierarchy: Dict[str, str] = {}
        self.subtypes: Dict[str, Set[str]] = {}
        self.constant_map: Dict[str, Set[str]] = defaultdict(set)
        self.derived_predicates: List[DerivedPredicateSpec] = None
        
class TaskDefinition(VersionedSerializable):
    version = 1
    
    def __init__(self, task_ns, task_id, base_path: str, tech: int):
        defbase_location = TaskDefinition._to_path(base_path, task_id)
        self._defbase_path: Path = Path(defbase_location).resolve()
        self._defbase_location: str = str(self._defbase_path)
        self.task_ns: str = task_ns
        self.task_id: str = task_id
        self.tech: int = tech
        self.base_name: str = None
        self.ltm: "LevelTransitionMap" = None
        
        if not os.path.isdir(self._defbase_location):
            self._defbase_path.mkdir(parents=True, exist_ok=True)
        
    def set_ltm(self, ltm: "LevelTransitionMap"):
        self.ltm = ltm
              
    def save(self):
        path = os.path.join(self._defbase_location, "{}.taskdef".format(self.task_id))
        with open(path, "wb") as fd:
            pickle.dump(self, fd)
    
    @staticmethod    
    def load(base_path: str, task_id: str) -> "TaskDefinition":
        defbase_location = TaskDefinition._to_path(base_path, task_id)
        defbase_path = Path(defbase_location).resolve()
        defbase_location = str(defbase_path)
        path = os.path.join(defbase_location, "{}.taskdef".format(task_id))
        if not os.path.isfile(path):
            raise FileNotFoundError(f"No task definition found for {task_id} at {path}")
        
        with open(path, "rb") as fd:
            return pickle.load(fd)
    
    @staticmethod
    def _to_path(base_path, task_id):
        elements = task_id.split('_')
        return os.path.join(base_path, *elements)
    
    def _save_nested_optimizers(self):
        child_optimizer = None
        if self.ltm is not None:
            nested_children_itr = self.ltm.get_nested_children()
            if nested_children_itr is not None:
                optimizer_dict = {}
                for nested_children_list in nested_children_itr:
                    for nested_children in nested_children_list:
                        nesting_spec_id = nested_children.nesting_spec_id
                        if nested_children.optimizer is not None:
                            optimizer_dict[nesting_spec_id] = nested_children.optimizer
                if len(optimizer_dict) > 0:
                    child_optimizer = True
                    path = os.path.join(self._defbase_location, "nesting.optimizer")
                    with open(path, "wb") as fd:
                        try:
                            pickle.dump(optimizer_dict, fd)
                        except Exception as e:
                            find_staticmethods(optimizer_dict)
                            logging.error(f"Unexpected pickling optimizer failure: Error: {e}", exc_info=True)
                else:
                    child_optimizer = False
        return child_optimizer
    
    def _load_nested_optimizers(self):
        if self.ltm is not None:
            path = os.path.join(self._defbase_location, "nesting.optimizer")
            if os.path.exists(path):
                optimizer_dict = None
                with open(path, "rb") as fd:
                    optimizer_dict = pickle.load(fd)
                    
                nested_children_itr = self.ltm.get_nested_children()
                if nested_children_itr is not None:
                    for nested_children_list in nested_children_itr:
                        for nested_children in nested_children_list:
                            optimizer = optimizer_dict.get(nested_children.nesting_spec_id)
                            if optimizer is not None:
                                nested_children.optimizer = optimizer
    
class TaskOptimizer:
    def optimize_task_definition(self, task_definition):
        pass
    
    def start(self, sys_cxt):
        pass
    
    def stop(self, sys_cxt):
        pass
    
class ProcedureTaskDefinition(TaskDefinition):
    version = 1
    
    def __init__(self, task_ns: str, task_id: str, procedure_name: str, base_path: str, module_id: str):
        super().__init__(task_ns, task_id, base_path, TaskRunContext.TECH_PROCEDURE)
        self.module_id = module_id
        self.procedure_name = procedure_name
    
    def reset_definition(self, procedure_name: str, module_id: str) -> None:
        self.module_id = module_id
        self.procedure_name = procedure_name
            
    def bind_exec_module(self, module: "ProcedureModule"):
        self.module = module
    
    def execute(self, args: Dict[str, Any] = None) -> Any:
        return self.module.execute_task(self.procedure_name, args)
    
    def _custom_getstate(self)->dict:
        excluded_keys = {"module"}
        ret_dict = {k: v for k, v in self.__dict__.items() if k not in excluded_keys}
        
        child_optimizer = self._save_nested_optimizers()
        if child_optimizer is not None:
            ret_dict["_child_optimizer"] = child_optimizer
        return ret_dict
    
    def _custom_setstate(self, state:dict):
        self.__dict__.update(state)
        self.module = None
        
        if hasattr(self, "_child_optimizer") and self._child_optimizer and self.ltm is not None:
            self._load_nested_optimizers()
            del self._child_optimizer 
        
    def __repr__(self)->str:
        return f"{self.task_ns}:{self.task_id} - {self.module_id}.{self.procedure_name}"
        
class PlannerTaskDefinition(TaskDefinition):
    version = 1
    TASK_ID_SEPARATOR = "."
    ACTION_ARG_SEPARATOR = "_"
    PLAN_DIR = "plan"
    TMP_PLAN_DIR = "tmpplan"
    PROBLEM_DIR = "problem"
    OPTIMIZER_DIR = "optimizer"
    LEVEL_OPTIMIZER_DIR = "level"
    NESTING_OPTIMIZER_DIR = "nesting"
    NONCXT = "0"
    
    def __init__(self, task_ns, task_id, base_path: str, domain_pddl: str, problem_pddl: str, problem_gen: str = None):
        super().__init__(task_ns, task_id, base_path, TaskRunContext.TECH_PLANNER)
        self.domain_pddl: str = None
        self.problem_pddl: str = None
        self.problem_gen: str = None
        self.planner: str = None
        self.domain: ParsedDomain = None
        
        self.action_work_callable: Dict[str, Callable] = None
        
        plan_path = self._defbase_path / PlannerTaskDefinition.PLAN_DIR
        if not plan_path.exists():
            plan_path.mkdir(parents=True, exist_ok=True)
            
            plan_path = self._defbase_path / PlannerTaskDefinition.TMP_PLAN_DIR
            plan_path.mkdir(parents=True, exist_ok=True)
            
            plan_path = plan_path / PlannerTaskDefinition.NONCXT 
            plan_path.mkdir(parents=True, exist_ok=True)
            
            problem_path = self._defbase_path / PlannerTaskDefinition.PROBLEM_DIR
            problem_path.mkdir(parents=True, exist_ok=True)
        
        self.optimizer: Any = None
        self._save_specs(domain_pddl, problem_pddl, problem_gen)
        self.problem_custom_callable = None
        
    def bind_action_work(self, action_work_callable: Dict[str, Callable]) -> None:
        self.action_work_callable = action_work_callable
        
    def bind_custom_problem(self, problem_custom_callable):
        self.problem_custom_callable = problem_custom_callable
        
    def prepare_task(self) -> None:
        self.planner = "BfwsFD"
        
    def get_domain(self) -> ParsedDomain:
        if self.domain is not None:
            return self.domain
        
        from plan.planner import PlannerFactory
        self.domain = PlannerFactory.get_instance().get_planning_processor().load_domain(self.domain_pddl)
        return self.domain
    
    def reset_definition(self, domain_pddl: str, problem_pddl: str, problem_gen: str = None) -> None:
        prepared = self.planner is not None
        self._save_specs(domain_pddl, problem_pddl, problem_gen)
        if prepared:
            self.prepare_task()
            
    def get_artifact_path(self, artifact_type: str = PLAN_DIR) -> str:
        return os.path.join(self._defbase_location, artifact_type)
        
    def _save_specs(self, domain_pddl: str, problem_pddl: str, problem_gen: str = None) -> None:
        spec_path = self._check_and_save(domain_pddl)
        self.domain_pddl = str(spec_path)
        spec_path = self._check_and_save(problem_pddl)
        self.problem_pddl = str(spec_path)
        self.base_name = spec_path.name
        self.base_name, _ = os.path.splitext(spec_path.name)
        
        if problem_gen is not None:
            spec_path = self._check_and_save(problem_gen)
            self.problem_gen = str(spec_path)
        else:
            self.problem_gen = os.path.join(self._defbase_path, f"{self.base_name}.pddl.j2")
            problem_parser = PddlProblemParser()
            problem_parser.parse_file(self.problem_pddl)
            gen = ProblemTemplateGenerator(problem_parser)
            gen.write_template(self.problem_gen)
                    
    def _check_and_save(self, file_location):
        file_path = Path(file_location).resolve()

        if file_path.is_file() and file_path.parent == self._defbase_path:
            return file_path
        
        destination_path = self._defbase_path / file_path.name
        shutil.copy2(file_path, destination_path)
        return destination_path
    
    def get_problem_pddlpath(self, custom_problem) -> str:
        if custom_problem is None:
            return self.problem_pddl
        else:
            problem_basepath = self.get_artifact_path(PlannerTaskDefinition.PROBLEM_DIR)
            custom_id = custom_problem.generate_id()
            problem_pddl_path = os.path.join(problem_basepath, "{}.pddl".format(custom_id))
            return problem_pddl_path
    
    def _custom_getstate(self)->dict:
        excluded_keys = {"optimizer", "domain"}
        ret_dict = {k: v for k, v in self.__dict__.items() if k not in excluded_keys}
        
        if self.optimizer is not None:
            ret_dict["_optimizer"] = True
            path = os.path.join(self._defbase_location, "level.optimizer")
            with open(path, "wb") as fd:
                try:
                    pickle.dump(self.optimizer, fd)
                except Exception as e:
                    find_locks(self.optimizer)
                    logging.error(f"Unexpected pickling optimizer failure: Error: {e}", exc_info=True)
                    
        else:
            ret_dict["_optimizer"] = False
            
        if self.ltm is not None:
            child_optimizer = self._save_nested_optimizers()
            if child_optimizer is not None:
                ret_dict["_child_optimizer"] = child_optimizer
        return ret_dict
    
    def _custom_setstate(self, state:dict):
        self.__dict__.update(state)
        self.domain = None
        
        self.optimizer = None
        if self._optimizer:
            path = os.path.join(self._defbase_location, "level.optimizer")
            with open(path, "rb") as fd:
                self.optimizer = pickle.load(fd)
        del self._optimizer
        
        if hasattr(self, "_child_optimizer") and self._child_optimizer and self.ltm is not None:
            self._load_nested_optimizers()
            del self._child_optimizer 
            
    def __repr__(self)->str:
        return f"{self.task_ns}:{self.task_id} - {self.domain_pddl}"
    
class ProcedureModule(VersionedSerializable):
    version = 1
    
    def __init__(self, module_id: str, module_name: str, code_file: str):
        self.module_id: str = module_id
        self.module_name: str = module_name
        self.code_file: str = code_file
        self.execmod: types.ModuleType = None
        
    def init_module(self):
        if self.execmod is not None:
            return
        
        with open(self.code_file, "r") as fhan:
            code_str = fhan.read()
            
        code_obj = compile(code_str, self.code_file, "exec")
            
        mod = types.ModuleType(self.module_name)
        
        from baseproc.shim_import import add_thread_shim
        add_thread_shim(mod)
        
        from baseproc.proccontext import op_get_appcxt_store, op_invoke_subtask
        mod.__dict__['op_get_appcxt_store'] = op_get_appcxt_store
        mod.__dict__['op_invoke_subtask']   = op_invoke_subtask
        
        exec(code_obj, mod.__dict__)
        
        self.execmod = mod
        
    def execute_task(self, task_name: str, args: Dict[str, Any] = None) -> Any:
        if self.execmod is None:
            self.init_module()
            
        fn = getattr(self.execmod, task_name)
        if fn is None:
            raise ValueError(f"Module for {self.module_id}.{task_name} does not define a {task_name} function")
        
        result = fn(**args) if args is not None else fn()
        return result
          
    def execute_fn(self, fn_name, args: Dict[str, Any] = None) -> Any:
        return self.execute_task(fn_name, args)
    
    def _custom_getstate(self)->dict:
        excluded_keys = {"execmod"}
        ret_dict = {k: v for k, v in self.__dict__.items() if k not in excluded_keys}
        return ret_dict
    
    def _custom_setstate(self, state:dict):
        self.__dict__.update(state)
        self.execmod = None
        self.init_module()
        
class ScopedLevelManager(VersionedSerializable):
    version = 1
    
    def __init__(self, ns, base_name: str, base_path: str) -> None:
        self.ns = ns
        self._base_name = base_name
        self._base_path = base_path
        self._level_task_defmap: Dict[str, TaskDefinition] = {}
        self._mod_defmap: Dict[str, ProcedureModule] = {}

    def set_module_definition(self, module_id: str, src_path: str) -> ProcedureModule:
        src_base_path = os.path.join(self._base_path, "src")
        if not os.path.exists(src_base_path):
            os.makedirs(src_base_path, exist_ok=True)
            if src_base_path not in sys.path:
                sys.path.insert(0, src_base_path)
            
        base_file_name = to_file_name(module_id)
        dest_file = os.path.join(src_base_path, f"{base_file_name}.py")
        currentDirectory = os.getcwd()
        print(src_path, dest_file, currentDirectory)
        shutil.copyfile(src_path, dest_file)
        
        module_name = f"sb_{self._base_name}_{base_file_name}"
        modreg =  ProcedureModule(module_id, module_name, dest_file)
        self._mod_defmap[module_id] = modreg
        return modreg
    
    def get_module_definition(self, module_id: str) -> ProcedureModule:
        return self._mod_defmap.get(module_id)
    
    def set_procedural_task_definition(self, module_id: str, task_id: str, procedure_name: str = None) -> TaskDefinition:
        procedure_name = procedure_name or task_id
        current_def = self._level_task_defmap.get(task_id)
        if current_def is not None:
            current_def.reset_definition(procedure_name, module_id)
            return current_def
        else:
            task_def = ProcedureTaskDefinition(self.ns, task_id, procedure_name, self._base_path, module_id)
            self._level_task_defmap[task_id] = task_def
            return task_def
        
    def set_planner_task_definition(self, task_id: str, domain_pddl: str, problem_pddl: str, problem_gen: str = None) -> TaskDefinition:
        if not is_str_valid(task_id):
            raise ValueError(f"Invalid task_id: {task_id}")
        
        current_def = self._level_task_defmap.get(task_id)
        if current_def is not None:
            current_def.reset_definition(domain_pddl, problem_pddl, problem_gen)
        else:
            current_def = PlannerTaskDefinition(self.ns, task_id, self._base_path, domain_pddl, problem_pddl)
            current_def.prepare_task()
            self._level_task_defmap[task_id] = current_def
            
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug("LEVELMGR: Task Define {} with Domain={}, Problem={}".\
                format(task_id, current_def.domain_pddl, current_def.problem_pddl))
            
        return current_def
             
    def get_task_definition(self, task_id: str = None) -> TaskDefinition:
        return self._level_task_defmap.get(task_id)
    
    def get_task_definitions(self) -> Iterable[TaskDefinition]:
        return self._level_task_defmap.value()
    
    def _custom_getstate(self)->dict:
        excluded_keys = {"_level_task_defmap"}
        ret_dict = {k: v for k, v in self.__dict__.items() if k not in excluded_keys}
        
        task_id_list = list(self._level_task_defmap.keys())
        ret_dict["_task_ids"] = task_id_list
        
        for task_definition in self._level_task_defmap.values():
            task_definition.save()
        
        return ret_dict
        
    def _custom_setstate(self, state:dict):
        self.__dict__.update(state)
        
        self._level_task_defmap = {}
        if self._task_ids is not None:
            for task_id in self._task_ids:
                task_def = TaskDefinition.load(self._base_path, task_id)
                self._level_task_defmap[task_id] = task_def
                
                if isinstance(task_def, ProcedureTaskDefinition):
                    module = self.get_module_definition(task_def.module_id)
                    task_def.bind_exec_module(module)
        del self._task_ids
    
class LevelManager(VersionedSerializable):
    version = 1
    
    TOP_LEVEL = "top"
    
    @staticmethod
    def build_level_manager(definition_store) -> "LevelManager":
        level_manager = LevelManager._load(definition_store.base_path)
        if level_manager is not None:
            return level_manager
        
        level_manager = LevelManager(definition_store)
        return level_manager
    
    def __init__(self, definition_store):
        self._definition_store: DefinitionStore = definition_store
        self._scoped_manager_map = {}
        
    def get_scoped_levelmgr(self, ns: str) -> ScopedLevelManager:
        return self._scoped_manager_map.get(ns)
        
    def register_namespace(self, ns: str) -> ScopedLevelManager:
        curr_mgr = self.get_scoped_levelmgr(ns)
        if curr_mgr is not None:
            return curr_mgr
        
        nspath = ns_to_filepath(ns)
        scope_base_path = os.path.join(self._definition_store.base_path, nspath)
        if not os.path.exists(scope_base_path):
            os.makedirs(scope_base_path, exist_ok=True)
            
        ret = ScopedLevelManager(ns, nspath, scope_base_path)
        self._scoped_manager_map[ns] = ret
        return ret
        
    def start(self):
        pass
    
    def stop(self):
        self._save()
        
    def _save(self):
        path = os.path.join(self._definition_store.base_path, "levelmgr.pickle")
        with open(path, "wb") as fd:
            pickle.dump(self, fd)
    
    @staticmethod
    def _load(base_path):
        levelmgr_path = os.path.join(base_path, "levelmgr.pickle")
        if not os.path.isfile(levelmgr_path):
            return None
        
        with open(levelmgr_path, "rb") as fd:
            return pickle.load(fd)
