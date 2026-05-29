import os
import re
import logging
from typing import List, Dict, NewType, Any
import subprocess
from jsonpath_ng import parse as jsonpath_parse
from spcoreutil.jsonCodec import get_first
from common.util import resolve_env_variables, dynamic_load_instance
from plan.planbase import PlanAction, PlanningProcessor
from io import StringIO
import hashlib
from jinja2 import Template
import pickle

LOGGER_BASE = "oraplan.plan"
LOG_TAG = {"lgmod": "ORAPLAN"}
LOG_LINE = "\n\t"

DEFAULT_LOG_FILE = "/tmp/planner.log"

CFG_PLANNERS = jsonpath_parse("$.planners")
CFG_FDR_TRANSLATORS = jsonpath_parse("$.fdrTranslators")
CFGPLANNER_BASE = jsonpath_parse("base")
CFGPLANNER_PM = jsonpath_parse("pm")
CFGPLANNER_XLATE = jsonpath_parse("xlate")
CFGPM_NAME = jsonpath_parse("name")
CFGPM_COMMAND = jsonpath_parse("command")
CFGPM_INSERT = jsonpath_parse("insert")
CFGPM_DEFAULT = jsonpath_parse("default")
CFG_PLANNING_PROCESSOR = jsonpath_parse("planning_processor")

LtmParser = NewType("LtmParser", None)

class TaskPlan:
    def __init__(self, actions: List[PlanAction]):
        self.actions = actions

    @classmethod
    def from_string(cls, plan_string: str) -> 'TaskPlan':
        non_comment_lines = [
            line for line in plan_string.splitlines()
            if not line.strip().startswith(';')
        ]
        filtered_string = "\n".join(non_comment_lines)
        
        raw_actions = re.findall(r"\([^()]*\)", filtered_string)
        
        actions: List[PlanAction] = []
        for raw in raw_actions:
            content = raw.strip()[1:-1].strip()
            tokens = content.split()
            if not tokens:
                continue
            
            action = tokens[0].lower()
            arguments = [arg.lower() for arg in tokens[1:]]
            
            actions.append(PlanAction(action, arguments))

        return cls(actions)
    
    @classmethod
    def from_file(cls, filepath: str) -> 'TaskPlan':
        with open(filepath, 'r', encoding='utf-8') as f:
            plan_string = f.read()
        return cls.from_string(plan_string)

    def write(self) -> str:
        return "\n".join(str(action) for action in self.actions)

    def write_to_file(self, filepath: str) -> None:
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(self.write() + "\n")

    def __repr__(self) -> str:
        return f"TaskPlan(actions={self.actions})"
    
# Keep this definition
PlannerFactory = NewType("PlannerFactory", None)

class ToolInvoker:
    def __init__(self, name: str, spec: List[str], dynamic_arg_indices: List[int]):
        self._name = name
        self._invoke_spec = spec
        self._dynamic_arg_indices = dynamic_arg_indices
        
    def invoke_command(self, dynamic_args: List[str], purpose: str, dynamic_args_desc: List[str], 
        output_file: str = DEFAULT_LOG_FILE, error_file: str = DEFAULT_LOG_FILE):
        command_arr = list(self._invoke_spec)
        
        if self._dynamic_arg_indices is not None and len(self._dynamic_arg_indices) > 0:
            for index in range(len(self._dynamic_arg_indices)):
                command_arr[self._dynamic_arg_indices[index]] = dynamic_args[index]
            
        if PlannerFactory.logger.isEnabledFor(logging.DEBUG):
            msg = "COMMAND: invoking {} {} for ".format(purpose, command_arr[0])
            if self._dynamic_arg_indices is not None and len(self._dynamic_arg_indices) > 0:
                for index in range(len(self._dynamic_arg_indices)):
                    msg = msg + ", {}={}".format(dynamic_args_desc[index], dynamic_args[index])
            PlannerFactory.logger.debug(msg)
                
        try:
            if output_file == error_file:   
                with open(output_file, "w") as outfile:
                    process = subprocess.Popen(command_arr, stdout=outfile, stderr=subprocess.STDOUT)
                    return_code = process.wait()
                    return return_code
            else:
                with open(output_file, "w") as outfile, open(error_file, "w") as errfile:
                    process = subprocess.Popen(command_arr, stdout=outfile, stderr=errfile)
                    return_code = process.wait()
                    return return_code
        except FileNotFoundError as e:
            print(f"Error for {purpose}: {e}")
            return -1
        except subprocess.CalledProcessError as e:
            print(f"Error invoking {purpose}: {e}")
            return -1
        except Exception as e:
            print(f"An unexpected error occurred invoking {purpose}: {e}")
            return -1
        
    def __repr__(self):
        return self._name
    
class PlannerInvoker(ToolInvoker):
    ARGS_DESC = ["Domain", "Problem", "Plan"]
    def __init__(self, name: str, spec: List[str], dynamic_arg_indices: List[int]):
        super().__init__(name, spec, dynamic_arg_indices)
        
    def invoke_planner(self, domain_file, problem_file, plan_file, output_file: str = DEFAULT_LOG_FILE, error_file: str = DEFAULT_LOG_FILE):
        return super().invoke_command([domain_file, problem_file, plan_file], "Planner", self.ARGS_DESC, output_file, error_file)

class TranslatorInvoker(ToolInvoker):
    ARGS_DESC = ["Domain", "Problem", "Output"]
    def __init__(self, name: str, spec: List[str], dynamic_arg_indices: List[int]):
        super().__init__(name, spec, dynamic_arg_indices)
        
    def translate(self, domain_file, problem_file, json_file, output_file: str = DEFAULT_LOG_FILE, error_file: str = DEFAULT_LOG_FILE):
        super().invoke_command([domain_file, problem_file, json_file], "Translator", self.ARGS_DESC, output_file, error_file)
            
class PlannerFactory:
    DEFAULT_INSERT = [1, 2, 3]
    logger = None
    _singleton = None
    
    @staticmethod
    def get_instance() -> "PlannerFactory":
        return PlannerFactory._singleton
    
    def __init__(self, config):
        PlannerFactory.logger = logging.getLogger(LOGGER_BASE)
        self._planner_map: Dict[str, PlannerInvoker] = {}
        self._translator_map: Dict[str, TranslatorInvoker] = {}
        self._default_planner = None
        self._default_translator = None
        self._planning_processor: PlanningProcessor = None
        
        planners_spec = get_first(CFG_PLANNERS, config)
        for planner_spec in planners_spec:
            base_path = self._parse_base_path(planner_spec)
            pm = get_first(CFGPLANNER_PM, planner_spec)
            
            for pm_elm in pm:
                name, command, insert, is_default = self._parse_invoker_specelm(base_path, pm_elm)
                invoker = PlannerInvoker(name, command, insert)
                self._planner_map[name] = invoker
                if is_default:
                    self._default_planner = invoker
                    
        translators_spec = get_first(CFG_FDR_TRANSLATORS, config)
        for translator_spec in translators_spec:
            base_path = self._parse_base_path(translator_spec)
            xlate = get_first(CFGPLANNER_XLATE, translator_spec)
            
            for pm_elm in xlate:
                name, command, insert, is_default = self._parse_invoker_specelm(base_path, pm_elm)
                invoker = TranslatorInvoker(name, command, insert)
                self._translator_map[name] = invoker
                if is_default:
                    self._default_translator = invoker
                    
        planning_processor_cfg = get_first(CFG_PLANNING_PROCESSOR, config)
        if planning_processor_cfg:
            module_name = planning_processor_cfg.get("module_name")
            class_name = planning_processor_cfg.get("class_name")
            args = planning_processor_cfg.get("args", {})
            self._planning_processor = dynamic_load_instance(module_name, class_name, args)
            
        if PlannerFactory._singleton is None:
            PlannerFactory._singleton = self
    
    def parse_ltm(self, json_filepath, level_mgr):
        parser = LtmParser(json_filepath, level_mgr)
        parser.build_ltm()
        
    def parse_plan(self, plan_file) -> TaskPlan:
        return TaskPlan.from_file(plan_file)
        
    def _parse_base_path(self, config_spec):
        base_path = get_first(CFGPLANNER_BASE, config_spec)
        if base_path is not None:
            base_path = base_path.strip()
            base_path = resolve_env_variables(base_path)
        return base_path
    
    def _parse_invoker_specelm(self, base_path, pm_elm):
        name = get_first(CFGPM_NAME, pm_elm)
        command = get_first(CFGPM_COMMAND, pm_elm)
        insert = get_first(CFGPM_INSERT, pm_elm)
        insert = insert if insert is not None else list(self.DEFAULT_INSERT)
        is_default = get_first(CFGPM_DEFAULT, pm_elm, False)
        is_default = bool(is_default)
        if not command[0].startswith("/"):
            command[0] = base_path + os.sep + command[0]
        
        return(name, command, insert, is_default)
        
    def get_planner(self, purpose = None):
        planner = None if purpose is None else self._planner_map.get(purpose)
        if planner is None:
            planner = self._default_planner
        return planner
    
    def get_translator(self, purpose = None):
        translator = None if purpose is None else self._translator_map.get(purpose)
        if translator is None:
            translator = self._default_translator
        return translator
    
    def total_planner(self):
        return len(self._planner_map)
    
    def total_translator(self):
        return len(self._translator_map)
    
    def get_default_planner(self):
        return self._default_planner
    
    def get_default_translator(self):
        return self._default_translator
    
    def get_planning_processor(self, preference: str = None) -> PlanningProcessor:
        return self._planning_processor

class CustomProblem:
    OVERWRITE = "overwrite"
    CUSTOM_OBJECTS = "custom_objects"
    CUSTOM_INIT = "custom_init"
    CUSTOM_GOAL = "custom_goal"
    SEPARATOR = "\n\t"
    FIRST_SEPARATOR = ""  
    
    DEFAULT_RENDER = (CUSTOM_OBJECTS, CUSTOM_INIT, CUSTOM_GOAL, OVERWRITE)
    def __init__(self, pddlgen_render_dict: Dict[str, Any]):
        self.pddlgen_render_dict = pddlgen_render_dict
        self.generated_id = None
        
    def generate_id(self) -> str:
        if self.generated_id is not None:
            return self.generated_id
        gendata = pickle.dumps(self.pddlgen_render_dict, protocol=pickle.HIGHEST_PROTOCOL)
        digest = hashlib.sha256(gendata).hexdigest()
        self.generated_id = digest
        return self.generated_id
        
    def build_problem_pddl(self, base_problem_path, output_problem_path):
        with open(base_problem_path, "r") as han:
            template_source = han.read()
        tmpl = Template(template_source)
        self._normalize_standard()
        pddl_file_content = tmpl.render(self.pddlgen_render_dict)
        if pddl_file_content is None or len(pddl_file_content) == 0:
            raise ValueError(f"Failed PDDL generation for {base_problem_path}")
        
        with open(output_problem_path, "w") as han:
            han.write(pddl_file_content)
            
    def _normalize_standard(self):
        custom_objects = self.pddlgen_render_dict.get(self.CUSTOM_OBJECTS)
        if isinstance(custom_objects, dict):
            norm_val = StringIO()
            separator = self.FIRST_SEPARATOR
            for type_name, type_list in custom_objects.items():
                norm_val.write(separator)
                norm_val.write(' '.join(type_list))
                norm_val.write(f" - {type_name}")
                separator = self.SEPARATOR
            self.pddlgen_render_dict[self.CUSTOM_OBJECTS] = norm_val.getvalue()
            
        custom_init = self.pddlgen_render_dict.get(self.CUSTOM_INIT)
        if isinstance(custom_init, set):
            norm_val = StringIO()
            separator = self.FIRST_SEPARATOR
            for fact in custom_init:
                norm_val.write(separator)
                norm_val.write(str(fact))
                separator = self.SEPARATOR
            self.pddlgen_render_dict[self.CUSTOM_INIT] = norm_val.getvalue()
            
        custom_goals = self.pddlgen_render_dict.get(self.CUSTOM_GOAL)
        if isinstance(custom_goals, set):
            norm_val = StringIO()
            separator = self.FIRST_SEPARATOR
            for fact in custom_goals:
                norm_val.write(separator)
                norm_val.write(str(fact))
                separator = self.SEPARATOR
            self.pddlgen_render_dict[self.CUSTOM_GOAL] = norm_val.getvalue()
            
class CustomProblemParser(CustomProblem):
    def __init__(self, init_list: List[str], goal_list: List[str]):
        self.init_list = init_list
        self.goal_list = goal_list

    def build_problem_pddl(self, base_problem_path: str, output_problem_path: str) -> None:
        with open(base_problem_path, 'r', encoding='utf-8') as f:
            content = f.read()
            
        if self.init_list is not None:
            content = self._inject_into_init(content, self.init_list)

        if self.goal_list is not None:
            content = self._inject_into_goal(content, self.goal_list)

        with open(output_problem_path, 'w', encoding='utf-8') as f:
            f.write(content)

    def _inject_into_init(self, content: str, new_facts: List[str]) -> str:
        pattern = r"(:init\s*)([\s\S]*?)(?=\)\s*:goal)"
        match = re.search(pattern, content, re.IGNORECASE)
        if not match:
            raise ValueError("Could not find a valid :init section.")

        init_start, init_body = match.groups()
        updated_init_body = init_body.strip() + "\n  " + "\n  ".join(new_facts) + "\n"
        return re.sub(pattern, f"{init_start}{updated_init_body}", content, flags=re.IGNORECASE)

    def _inject_into_goal(self, content: str, new_goals: List[str]) -> str:
        pattern = r"(:goal\s*\()([\s\S]*?)\)\s*\)"
        match = re.search(pattern, content, re.IGNORECASE)
        if not match:
            raise ValueError("Could not find a valid :goal section.")

        goal_start, goal_body = match.groups()
        goal_body = goal_body.strip()

        if goal_body.lower().startswith("and"):
            updated_goal_body = goal_body + "\n  " + "\n  ".join(new_goals)
        else:
            updated_goal_body = "and\n  " + goal_body + "\n  " + "\n  ".join(new_goals)

        return re.sub(pattern, f"{goal_start}{updated_goal_body})", content, flags=re.IGNORECASE)