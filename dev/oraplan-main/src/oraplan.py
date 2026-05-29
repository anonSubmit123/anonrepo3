import json
import logging
import sys

from spcoreutil.tcconfig import tc_runtime_configure, tc_closest_filepath
from spcoreutil.coreutil import ArgumentParserThrowing, ArgumentParserError
from plan.planner import PlannerFactory
from spcoreutil.dldefine import DLDefine
from baseproc.levelmgr import DefinitionStore, LevelManager
from operate.syscxt import PlanScheduler

LOGGER_BASE = "oraplan"
LOG_TAG = {"lgmod": "ORAPLAN"}
LOG_LINE = "\n\t"


class OraPlan:
    _singleton = None
    logger = None
    
    @classmethod
    def get(cls):
        return cls._singleton
        
    def __init__(self, config):
        OraPlan._singleton = self
        OraPlan.logger = logging.getLogger(LOGGER_BASE)
        self.planner_factory = PlannerFactory(config)
        self.definition_store = DefinitionStore(config)
        self.level_manager = LevelManager.build_level_manager(self.definition_store)
        self.plan_scheduler = PlanScheduler(config)
        
    def launch(self):
        self.plan_scheduler.start()
        pass
    
def run_oraplan(argv):    
    ap = ArgumentParserThrowing();
    try:
        lOpt = DLDefine.to_option
        lSOpt = DLDefine.to_short_option
        lDesc = DLDefine.to_desc
        
        opt = DLDefine.OPTION_APP_CONFIG
        ap.add_argument(lSOpt(opt), lOpt(opt), type=str, required=False, help=lDesc(opt),
            default=(None))
        
        opt = DLDefine.OPTION_LOG_CONFIG
        ap.add_argument(lSOpt(opt), lOpt(opt), type=str, required=False, help=lDesc(opt),
            default=(None))
                
        args = vars(ap.parse_args(argv[1:]))
        
    except ArgumentParserError as excp:
        print("ERROR: " + str(excp))
        ap.print_help();    
        sys.exit(2)
    
    log_cfg_file = args.get(DLDefine.OPTION_LOG_CONFIG)
    tc_runtime_configure({
        "logConfig": log_cfg_file if log_cfg_file is not None else tc_closest_filepath(__file__, "logConfig.json")
    })
    tc_closest_filepath
    config = args.get(DLDefine.OPTION_APP_CONFIG)
    config = config if config is not None else tc_closest_filepath(__file__, "oraconfig.json")
    with open(config, 'r', encoding='utf-8') as f:
        config = json.load(f)
    system = OraPlan(config)
    system.launch()