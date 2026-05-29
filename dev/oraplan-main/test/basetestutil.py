import json
import logging
import os
import threading
from plan.planner import PlannerFactory
from spcoreutil.tcconfig import tc_closest_filepath, tc_runtime_configure
from pathlib import Path
from baseproc.levelmgr import DefinitionStore, LevelManager
from plan.plancompile import PlanBuilder
from operate.syscxt import PlanScheduler, SystemContext
from jsonpath_ng import parse as jsonpath_parse
from plan.planbase import CompositeStateContext
from spcoreutil.jsonCodec import get_first
import csv
from operate.planaddoncomp import BlockingCallable

LOGGER_BASE = "test.oraplan"
LOG_TAG = {"lgmod": "ORAPLAN"}
LOG_LINE = "\n\t"

class BaseHelper:
    test_module = None
    planner_factory = None
    level_manager = None
    plan_builder = None
    config = None
    own_dir = None
    resources_dir = None
    sys_cxt = None
    logger = logging.getLogger(LOGGER_BASE)

def base_test_setup(test_module, logger = None, load_sys_cxt = True, launch_sys_cxt = True):
    BaseHelper.test_module = test_module 
    message = f"TEST: Setting up  test environment and resources for test module: {test_module}"
    print(message)
    
    if logger is not None:
        BaseHelper.logger = logger
        
    tc_runtime_configure({
        "logConfig": tc_closest_filepath(__file__, "logConfig.json")
    })
    if BaseHelper.logger.isEnabledFor(logging.INFO):
        BaseHelper.logger.info(message, extra=LOG_TAG)
        
    config = tc_closest_filepath(__file__, "oraconfig.json")
    with open(config, 'r', encoding='utf-8') as f:
        config = json.load(f)
      
    BaseHelper.config = config  
    BaseHelper.own_dir = os.path.dirname(os.path.abspath(__file__))
    BaseHelper.resources_dir = str(Path(BaseHelper.own_dir).joinpath("..", "test_resources").resolve())
    
    BaseHelper.planner_factory = PlannerFactory(config)
    definition_store = DefinitionStore(config)
    BaseHelper.level_manager = LevelManager.build_level_manager(definition_store)
    BaseHelper.plan_builder = PlanBuilder(BaseHelper.level_manager)
    plan_scheduler = PlanScheduler(BaseHelper.config)
    BaseHelper.sys_cxt = None
    
    if load_sys_cxt:
        BaseHelper.sys_cxt = SystemContext(BaseHelper.level_manager, plan_scheduler)
        
        if launch_sys_cxt:
            BaseHelper.sys_cxt.start()
            
    message = f"TEST: Setup completed for test module: {test_module}"
    print(message)
    
    if BaseHelper.logger.isEnabledFor(logging.INFO):
        BaseHelper.logger.info(message, extra=LOG_TAG)
               
def base_test_teardown(test_module, launched_sys_cxt = True):
    message = f"TEST: Tearing down test environment and resources for test module: {test_module}"
    if BaseHelper.logger.isEnabledFor(logging.INFO):
        BaseHelper.logger.info(message, extra=LOG_TAG)
    print(message)
    
    if BaseHelper.sys_cxt is not None and launched_sys_cxt:
        BaseHelper.sys_cxt.stop(False)
        BaseHelper.sys_cxt.await_shutdown()
        
    message = f"TEST: Completed Teardown for test module: {test_module}"
    print(message)
    if BaseHelper.logger.isEnabledFor(logging.INFO):
        BaseHelper.logger.info(message, extra=LOG_TAG)
    
def load_test_sys_cxt():
    pass

class CoordinatorHelper:
    def __init__(self, test_case = None):
        self._condition = threading.Condition()
        self.test_case = test_case if not None else "default"
        self._terminated = False
        
    def notify_event(self):
        with self._condition:
            self._terminated = True
            if BaseHelper.logger.isEnabledFor(logging.DEBUG):
                BaseHelper.logger.debug(f"{BaseHelper.test_module}.{self.test_case}: Notify completion", extra=LOG_TAG)
            self._condition.notify_all()
    
    def await_event(self):
        with self._condition:
            while not self._terminated:
                if BaseHelper.logger.isEnabledFor(logging.DEBUG):
                    BaseHelper.logger.debug(f"{BaseHelper.test_module}.{self.test_case}: Awaiting completion", extra=LOG_TAG)
                self._condition.wait()

def get_json_attrs(json_file_path, json_paths):
    ret_list = []
    with open(json_file_path, 'r', encoding='utf-8') as f:
        config = json.load(f)
        for json_path in json_paths:
            path  = jsonpath_parse(json_path)
            ret_list.append(get_first(path, config))
    return ret_list
        
def load_initial_context(problem_file: str, domain_ns: str, domain_taskid: str, desc: str):
    slm = BaseHelper.level_manager.get_scoped_levelmgr(domain_ns)
    task_definition = slm.get_task_definition(domain_taskid)
    parsed_domain = task_definition.get_domain()

    state_cxt = CompositeStateContext(desc)
    PlannerFactory.get_instance().get_planning_processor().load_problem(problem_file, parsed_domain, state_cxt)
    return state_cxt  

def loadOpTime(base_dir: str, task_ns = str, task_id = str, domain_path = str, ops_path: str = None):
    scoped_level_mgr = BaseHelper.level_manager.get_scoped_levelmgr(task_ns)
    task_definition = scoped_level_mgr.get_task_definition(task_id)
    if ops_path is None:
        ops_path = get_ops_path(base_dir, domain_path)
    elif not os.path.isabs(ops_path):
        ops_path = os.path.join(base_dir, ops_path)
    if not os.path.exists(ops_path):
        return False    
        
    ops_dict = {}
    with open(ops_path, newline='') as f:
        reader = csv.reader(f)
        next(reader, None)
        for row in reader:
            ops_dict[row[0]] = BlockingCallable(row[0], float(row[1]), float(row[2]))
    task_definition.bind_action_work(ops_dict)
    return True
    
def get_ops_path(base_dir: str, domain_path: str) -> str:
    rel_dir = os.path.dirname(domain_path)

    base_fname = os.path.basename(domain_path)
    stem, _     = os.path.splitext(base_fname)
    if stem.endswith("_domain"):
        stem = stem[: -len("_domain")]
    else:
        stem = stem

    ops_fname = f"{stem}_ops.csv"

    return os.path.join(base_dir, rel_dir, ops_fname)