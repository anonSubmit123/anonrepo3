from typing import List, Tuple, Dict, Any

import os
from jsonpath_ng import parse as jsonpath_parse
from spcoreutil.jsonCodec import get_first, get_first_at

import json
from common.util import resolve_env_variables, to_valid_str, ns_to_filepath
from baseproc.ltm import LevelTransitionMap, ChildNestingSpec, ChildSpec,\
    NotifyStateSpec, AwaitStateSpec, MatchAction, MatchByCriteria,\
    CriteriaMatcherByCondition, CriteriaMatcherByFunction
from baseproc.levelmgr import ScopedLevelManager, LevelManager, ProcedureTaskDefinition,\
    TaskOptimizer
import tempfile
from plan.planner import PlannerFactory

CFG_BASEPATH = jsonpath_parse("$.basepath")
CFG_IMPORT = jsonpath_parse("$.import")
CFG_MODULES = jsonpath_parse("$.modules")
CFG_TASK_TRANSITIONS = jsonpath_parse("$.task_transitions")

class LtmParser:
    def __init__(self, json_filepath, level_mgr: LevelManager, task_optimizer: TaskOptimizer = None):
        self.level_mgr: LevelManager = level_mgr
        self.task_optimizer: TaskOptimizer = task_optimizer
        self.base_path: str = None
        self.ns: str = None
        self.ns_filepath: str = None
        self.import_map: Dict[str, str] = None
        self.module_map: Dict[str, Dict[str, Any]] = None
        self.nslevelmgr: ScopedLevelManager = None
        
        self.procedure_list: List[ProcedureTaskDefinition] = []
        self.std_procedure_list: List[ProcedureTaskDefinition] = []
        self._planning_processor = PlannerFactory.get_instance().get_planning_processor()
        with open(json_filepath, 'r', encoding='utf-8') as f:
            self.config = json.load(f)
            
    def build_ltm(self) -> None:
        self._parse_basepath()
        self.ns = to_valid_str(get_first_at("ns", self.config), "defns")
        self.ns_filepath = ns_to_filepath(self.ns)
        self.nslevelmgr = self.level_mgr.register_namespace(self.ns)
        
        self._parse_import()
        self._parse_modules()
        
        task_transitions = get_first(CFG_TASK_TRANSITIONS, self.config)
        
        ltm_spec_dict = {}
        ltm_taskid_list = []
        for task_spec in task_transitions:
            ltm = LevelTransitionMap()
            taskdef = get_first_at("taskdef", task_spec)
            
            ltm.task_id = self._parse_task_def(taskdef)
            task_definition = self.nslevelmgr.get_task_definition(ltm.task_id)
            if task_definition is None:
                raise ValueError(f"Type definition for task {ltm.task_id} was not successfully parsed/registered")
            
            task_definition.ltm = ltm
            ltm_spec_dict[ltm.task_id] = task_spec
            ltm_taskid_list.append(ltm.task_id)
            
        self._bind_modules()
            
        for task_id in ltm_taskid_list:
            task_spec = ltm_spec_dict[task_id]
            task_definition = self.nslevelmgr.get_task_definition(task_id)
            ltm = task_definition.ltm
            
            nested_specs = get_first_at("nested", task_spec)
            if nested_specs is not None:
                for nested_spec in nested_specs:
                    nested_child = self._parse_nested(task_id, nested_spec)
                    ltm.add_child(nested_child.match_spec.action_name, nested_child)
                    
            await_state_specs = get_first_at("awaitState", task_spec)
            if await_state_specs is not None:
                for await_state_spec in await_state_specs:
                    await_state = self._parse_await_state(await_state_spec)
                    ltm.add_await_state(await_state.match_spec.action_name, await_state)
            
            notify_state_specs = get_first_at("notifyState", task_spec)
            if notify_state_specs is not None:
                for notify_state_spec in notify_state_specs:
                    notify_state = self._parse_notify_state(notify_state_spec)
                    ltm.add_notify_state(notify_state.match_spec.action_name, notify_state)
                    
        if self.task_optimizer:
            for task_id in ltm_taskid_list:
                task_spec = ltm_spec_dict[task_id]
                task_definition = self.nslevelmgr.get_task_definition(task_id)
                self.task_optimizer.optimize_task_definition(task_definition)
                    
    def _bind_modules(self) -> None:
        if len(self.std_procedure_list) > 0:
            code_str = ""
            fn_declare_str = ""
            count = 1
            module_name = "orastdmodule"
            for proc_def in self.std_procedure_list:
                module_id = proc_def.module_id
                code_str = code_str + f"import {module_id}\n"
                new_proc = f"stdinvoke_{count}"
                fn_declare_str = fn_declare_str + f"{new_proc} = {proc_def.procedure_name}"
                proc_def.procedure_name = new_proc
                proc_def.module_id = module_name
            
            code_str = code_str + "\n\n" + fn_declare_str
            with tempfile.NamedTemporaryFile(mode='w+', delete=False, encoding='utf-8', prefix=module_name, suffix=".py") as temp_file:
                temp_filepath = temp_file.name
                temp_file.write(code_str)
                temp_file.flush()
                os.fsync(temp_file.fileno())
            self.nslevelmgr.set_module_definition(module_name, temp_filepath)
            self.procedure_list.extend(self.std_procedure_list)
            
        if len(self.procedure_list) > 0:
            for proc_def in self.procedure_list:
                module_id = proc_def.module_id
                module = self.nslevelmgr.get_module_definition(module_id)
                if module is None:
                    raise ValueError(f"No registered module={module_id} in task={proc_def.task_id}")
                proc_def.bind_exec_module(module)
                
        self.std_procedure_list = None
        self.procedure_list = None
                
    def _parse_basepath(self):
        self.base_path = to_valid_str(get_first(CFG_BASEPATH, self.config), ".")
        self.base_path = resolve_env_variables(self.base_path)
      
    def _parse_import(self):
        self.import_map = {}
        importcfg = get_first(CFG_IMPORT, self.config)
        if importcfg is None:
            return
        for alias, ns in importcfg.items():
            self.import_map[alias] = ns
            
    def _parse_modules(self):
        self.module_map = {}
        modulearr = get_first(CFG_MODULES, self.config)
        if modulearr is None:
            return
        
        for modulecfg in modulearr:
            modid = to_valid_str(get_first_at("modid", modulecfg))
            location = to_valid_str(get_first_at("location", modulecfg))
            if not os.path.isabs(location):
                location = os.path.join(self.base_path, location)
            self.nslevelmgr.set_module_definition(modid, location)
            
    def _resolve_path(self, path):
        if os.path.isabs(path):
            path = resolve_env_variables(path)
        else:
            path = os.path.join(self.base_path, path)
            path = resolve_env_variables(path)
        if not os.path.exists(path):
            raise FileNotFoundError("Cannot access: {}, basepath: {}".format(path, self.base_path))
        return path
         
    def _parse_task_def(self, task_def_spec):
        taskid = get_first_at("taskid", task_def_spec)
        tech = get_first_at("tech", task_def_spec)
        
        if tech == "planner":
            planner_spec = get_first_at("planner", task_def_spec)
            domain = get_first_at("domain", planner_spec)
            domain = self._resolve_path(domain)
            problem = get_first_at("problem", planner_spec)
            problem = self._resolve_path(problem)
            problem_gen = get_first_at("problem_gen", planner_spec)
            if problem_gen is not None:
                problem_gen = self._resolve_path(problem_gen)
            
            task_def =  self.nslevelmgr.set_planner_task_definition(task_id=taskid, 
                domain_pddl=domain, problem_pddl=problem, problem_gen=problem_gen)
            return taskid
            
        elif tech == "procedure":
            procedure_spec = get_first_at("procedure", task_def_spec)
            std = get_first_at("std", procedure_spec)
            self._update_module_resolution(procedure_spec, std is None)
            moduleref = get_first_at("modid", procedure_spec)
            procedure_name = get_first_at("name", procedure_spec)
            
            procedure = self.nslevelmgr.set_procedural_task_definition(module_id=moduleref, task_id=taskid,
                procedure_name=procedure_name)
            if std:
                self.std_procedure_list.append(procedure)
            else:
                self.procedure_list.append(procedure)
            return taskid
        
        else:
            raise ValueError(f"Unsupported tech={tech} for taskid={taskid}")
        
    def _parse_nested(self, parent_taskid, nested_spec) -> ChildNestingSpec:
        nested_child = ChildNestingSpec()
        match_action_spec = get_first_at("match", nested_spec)
        nested_child.match_spec = self._parse_match(match_action_spec)
        
        children_spec = get_first_at("children", nested_spec)
        if children_spec is not None:
            nested_children = []
            for child_spec in children_spec:
                nested_children.append(self._parse_nested_child(child_spec))
            nested_child.children = nested_children
        
        nested_child.arg_context = get_first_at("arg_context", nested_spec)
        return nested_child
            
    def _resolve_ns(self, nsspec):
        if nsspec is None:
            return self.ns
        
        if nsspec in self.import_map:
            nsspec = self.import_map[nsspec]
            
        return nsspec
    
    def _parse_nested_child(self, child_spec) -> ChildSpec:
        child = ChildSpec()
        taskref = to_valid_str(get_first_at("taskref", child_spec))
        ref_cmps = taskref.split(":")
        num_cmps = len(ref_cmps)
        if num_cmps <= 0 or num_cmps > 2:
            raise ValueError(f"Invalid task reference: {taskref}")
        
        nsspec = to_valid_str(ref_cmps[0]) if num_cmps > 1 else None
        child.child_ns = self._resolve_ns(nsspec)
        child.child_taskid = to_valid_str(ref_cmps[1]) if num_cmps > 1 else to_valid_str(ref_cmps[0])
        child.daemon_child = get_first_at("daemon", child_spec)
        child.state_cxtid = get_first_at("state_cxt", child_spec)
        
        child.input_spec= get_first_at("input", child_spec)
        self._update_module_resolution(child.input_spec)
        child.output_spec= get_first_at("output", child_spec)
        self._update_module_resolution(child.output_spec)

        return child
    
    def _update_module_resolution(self, mod_spec, with_ns = True):
        if not mod_spec:
            return mod_spec
        modref = get_first_at("modref", mod_spec)
        if not modref:
            return mod_spec 
        modns = None
        
        if with_ns:
            before, sep, after = modref.partition(':')
            modns = before if sep else None
            modid = after if sep else modref
            modns = self._resolve_ns(modns)
            mod_spec["modns"] = modns
        mod_spec["modid"] = modid
        
    def _parse_idmap(self, idmap_spec) -> List[Tuple[str, str]]:
        if idmap_spec is None:
            return None
        
        own_path = jsonpath_parse("own")
        child_path = jsonpath_parse("child")
        idmap = []
        for idelm_spec in idmap_spec:
            own_object = get_first(own_path, idelm_spec)
            child_object = get_first(child_path, idelm_spec)
            idmap.append(own_object, child_object)
        return idmap

    def _parse_match(self, match_spec):
        purpose = get_first_at("name", match_spec)
        criteria_spec = get_first_at("criteria", match_spec)
        if criteria_spec == None:
            return MatchAction(purpose)
        else:
            criteria_matcher = None
            criteria_type = get_first_at("as", criteria_spec)
            detail_spec = get_first_at("spec", criteria_spec)
            if criteria_type == "condition":
                condition = self._planning_processor.parse_condition(detail_spec)
                criteria_matcher = CriteriaMatcherByCondition(condition)
            elif criteria_type == "function":
                self._update_module_resolution(detail_spec)
                module_ns = get_first_at("modns", detail_spec)
                module_id = get_first_at("modid", detail_spec)
                fnname = get_first_at("fn", detail_spec)
                scoped_levelmgr = self.level_mgr.get_scoped_levelmgr(module_ns)
                if scoped_levelmgr is None:
                    raise ValueError(f"Unknown module namespace: {module_ns}")
                
                module_def = scoped_levelmgr.get_module_definition(module_id)
                if module_def is None:
                    raise ValueError(f"Unknown module identifier: {module_id} in namespace {module_ns}")
                criteria_matcher = CriteriaMatcherByFunction(module_def, fnname)
            else:
                raise ValueError(f"Unsupported criteria type for matching: {criteria_type}")
            return MatchByCriteria(purpose, criteria_matcher)
    
    def _parse_await_state(self, await_state_spec) -> ChildNestingSpec:
        await_state = AwaitStateSpec()
        match_spec = get_first_at("match", await_state_spec)
        await_state.match_spec = self._parse_match(match_spec)
        
        topic_spec = get_first_at("topic", await_state_spec)
        
        await_state.topic_category = get_first_at("category", topic_spec)
        await_state.topic_name = get_first_at("name", topic_spec)
        time_spec = get_first_at("value", topic_spec) 
        await_state.topic_time_spec = AwaitStateSpec.TIMESPEC_AFTER_START if time_spec == "start" else AwaitStateSpec.TIMESPEC_ANY 
        return await_state
    
    def _parse_notify_state(self, notify_state_spec) -> NotifyStateSpec:
        notify_state = NotifyStateSpec()
        match_notifier_spec = get_first_at("match", notify_state_spec)
        notify_state.match_spec = self._parse_match(match_notifier_spec)
        
        topic_spec = get_first_at("topic", notify_state_spec)
        notify_state.topic_category = get_first_at("category", topic_spec)
        notify_state.topic_name = get_first_at("name", topic_spec)
        notify_state.topic_value = get_first_at("value", topic_spec)
        return notify_state