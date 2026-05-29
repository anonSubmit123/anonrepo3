import random
from typing import List, Dict
from common.util import SegmentTreeWithIndex, VersionedSerializable, QTable
from baseproc.basetask import TaskStatistics
import logging
import math
import time

LOGGER_BASE = "oraplan.plan"
LOG_TAG = {"lgmod": "ORAPLAN"}
LOG_LINE = "\n\t"
logger = logging.getLogger(LOGGER_BASE)

class NestingPolicy:
    def sample(self, qtable: SegmentTreeWithIndex):
        raise NotImplementedError("Policy Not implemented")
    
class EGreedyNestingPolicy(NestingPolicy, VersionedSerializable):
    version = 1
    def __init__(self, epsilon_min=0.00001, epsilon_max=0.5, decay_rate=0.002305):
        self.epsilon_min = epsilon_min
        self.epsilon_max = epsilon_max
        self.decay_rate = decay_rate
        self.time = 0
        self._random = random.Random()

    def _get_epsilon(self, t: int) -> float:
        return self.epsilon_min + (self.epsilon_max - self.epsilon_min) * math.exp(-self.decay_rate * t)

    def sample(self, qtable):
        self.time = self.time + 1
        epsilon = self._get_epsilon(self.time)
        random_value = self._random.random()
        exploit = random_value > epsilon
        if exploit:
            arm = qtable.max_state()
            if logger.isEnabledFor(logging.DEBUG):
                str_qtable = qtable.dump()
                logger.debug(f"Policy: optimal-selection={arm}, Q = {str_qtable}")
        else:
            arm = qtable.sample()
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug(f"Policy: random-selection={arm}")
        return arm
    
    def _custom_getstate(self)->dict:
        excluded_keys = {"_random"}
        ret_dict = {k: v for k, v in self.__dict__.items() if k not in excluded_keys}
        return ret_dict
    
    def _custom_setstate(self, state:dict):
        self.__dict__.update(state)
        self._random = random.Random()
        
class MultiArmedBandit(VersionedSerializable):
    version = 1
    
    def __init__(self, arms, arm_policy: None):  
        self._arms: int = arms      
        self._policy: NestingPolicy = arm_policy if arm_policy is not None else EGreedyNestingPolicy()
        self._armfreq: List[int] = []
        
        self._tpi: float = 0
        self._tpi_update: float = 0 
        self._alpha: float = 0.01
        self._tpi_update_frequency: int = 20
        self._num_plan: int = 0 
        
        self._armfreq: List[int] = [0] * (self._arms)
        self._qtable: QTable = QTable(arms, 0)
        
    def _reward(self, plan_stat: TaskStatistics) -> float:
        return self._tpi - plan_stat.get_total_time()
    
    def reinforce(self, arm: int, plan_stat: TaskStatistics) -> None:
        if arm < 0 or arm > self._arms or plan_stat is None:
            raise ValueError(f"Arms out of bound, max={self._arms}, got={arm}, plan_time={'present' if plan_stat is not None else 'None'}")
        
        self._num_plan = self._num_plan + 1
        self._init_learning(plan_stat)
        reward = self._reward(plan_stat)
        old_estimate = self._qtable.get(arm)
        self._armfreq[arm] = self._armfreq[arm] + 1
        new_estimate = old_estimate + ((reward - old_estimate)/self._armfreq[arm])
        
        self._qtable.set(arm, new_estimate)
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(f"MultiArmedBandit: Arm-{arm} - Reward-{reward} Q = {old_estimate} > {new_estimate}")
        
    def select_arm(self):
        return self._policy.sample(self._qtable)
        
    def _init_learning(self, stats: TaskStatistics):
        total_plan_time = stats.get_total_time()
        is_valid = stats.get_is_valid()
        if not is_valid:
            return
        
        if self._tpi == 0 and is_valid:
            self._tpi = total_plan_time
            self._tpi_update = self._tpi
            return
        
        new_av = total_plan_time
        self._tpi_update = self._alpha * new_av + (1 - self._alpha) * self._tpi_update
        if self._num_plan %  self._tpi_update_frequency == 0:
            self._tpi = self._tpi_update
        
class NestingOptimizer(VersionedSerializable):
    version = 1
    
    def __init__(self, number_children: int, arm_policy: None):
        self._number_children = number_children
        self._common_bandit = MultiArmedBandit(number_children, arm_policy)
        self._cxt_dict: Dict[str, MultiArmedBandit] = {}
        
    def get_bandit(self, arg_context: List[int], arg_list: List[str], create_if_needed: bool = False) -> MultiArmedBandit:
        if arg_context is None:
            return self._common_bandit
        total_args = len(arg_list)
        selected_args = [arg_list[i] for i in arg_context if i >= 0 and i < total_args]
        key = "_".join(selected_args)
        ret = self._cxt_dict.get(key)
        if ret is None and create_if_needed:
            ret = MultiArmedBandit(self._number_children)
            self._cxt_dict[key] = ret
        return ret 
