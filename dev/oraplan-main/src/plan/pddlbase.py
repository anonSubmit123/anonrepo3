from typing import List, Set, Dict, Iterable, Optional, Any
import itertools

from pddl.logic.terms import Variable
from pddl.logic.base import ExistsCondition, ForallCondition, And, Or, Not,\
    BinaryOp, UnaryOp, Formula
from pddl.logic.effects import When, Forall
from pddl.logic.functions import (
    GreaterThan, GreaterEqualThan, EqualTo, LesserThan, LesserEqualThan,
    Increase, Decrease, Assign, ScaleUp, ScaleDown, NumericFunction,
    NumericValue, Plus, Minus, Times, Divide
)
from plan.planbase import ActionProcessor, PlanStateContext, PlanningProcessor,\
    AdjunctContext
from baseproc.basestore import FactAtom
from pddl.logic.predicates import Predicate, DerivedPredicate
from pddl import parse_problem as pddl_parse_problem
from pddl import parse_domain as pddl_parse_domain
from pddl.core import Problem, Domain
from _collections import defaultdict, deque
from pddl.logic.predicates import EqualTo as EqualToPredicate
from baseproc.levelmgr import ParsedDomain, DerivedPredicateSpec
from baseproc.basetask import StateContext
from lark import Lark
from pddl.parser.domain import DomainTransformer
from pddl.logic.terms import Constant
import importlib.resources as resources
import os
import logging

LOGGER_BASE = "oraplan.plan"
LOG_TAG = {"lgmod": "ORAPLAN"}
LOG_LINE = "\n\t"
logger = logging.getLogger(LOGGER_BASE)

def to_fact_atom(pred: Predicate, var_map: Dict[str, str] = None) -> FactAtom:
    args = []
    for term in pred.terms:
        if hasattr(term, 'name'):
            if var_map is not None and term.name in var_map:
                args.append(var_map[term.name])
            elif isinstance(term, Variable):
                args.append(f"?{term.name}")
        else:
            args.append(term.name)
    return FactAtom(pred.name, *args)

class ConditionParser:
    class _LenientTransformer(DomainTransformer):
        def constant(self, args):
            name = args[0]
            return Constant(name, None)

    def __init__(self):
        domain_pkg  = resources.files("pddl.parser")
        domain_path = domain_pkg / "domain.lark"
        grammar_dir = os.path.dirname(domain_path)

        with open(domain_path, encoding="utf-8") as f:
            grammar_text = f.read()

        self.parser = Lark(
            grammar_text,
            start="gd",
            propagate_positions=True,
            import_paths=[grammar_dir]
        )
        
    def parse_condition(self, cond_str) -> Formula:
        transformer = self._LenientTransformer()
        tree = self.parser.parse(cond_str)
        result = transformer.transform(tree)
        return result

class PddlPlanningProcessor(PlanningProcessor):
    def __init__(self):
        self._condition_parser = ConditionParser()
        
    def load_domain(self, domain_pddl: str) -> ParsedDomain:
        parsed_domain = ParsedDomain()
        parsed_domain.domain_pddl = domain_pddl
        try:
            domain: Domain = pddl_parse_domain(domain_pddl)
        except Exception as e:
            if logger.isEnabledFor(logging.ERROR):
                logger.error(f"Failed parsing domain from {domain_pddl}:  {type(e).__name__} - {e}", exc_info=True)
                raise
                
        if domain is None:
            raise ValueError(f"Failed parsing domain from: {domain_pddl}: No domain parsed")
        
        parsed_domain.domain = domain 
        parsed_domain.domain_name = domain.name
        if domain.actions:
            for action in domain.actions:
                parsed_domain.actions[action.name] = action
            
        if domain.types:
            for curr_type, parent_type in domain.types.items():
                parsed_domain.type_hierarchy[curr_type] = None if parent_type == "object" else parent_type
                
            parsed_domain.subtypes = PddlPlanningProcessor._subtype_closure(parsed_domain.type_hierarchy)
            
            if domain.constants is not None:
                for constant in domain.constants:
                    constant_type = next(iter(constant.type_tags))
                    parsed_domain.constant_map[constant_type].add(constant.name)  
                    
        if domain.derived_predicates:
            derived_rules = []
            for derived_predicate in domain.derived_predicates:
                derived_rules.append(DerivedRule(derived_predicate))
            ordered_derived_rules = DerivedRule.compute_global_order(derived_rules)
            parsed_domain.derived_predicates = ordered_derived_rules
        return parsed_domain     
    
    def store_domain(self, parsed_domain: ParsedDomain, domain_pddl_path: str) -> None:
        with open(domain_pddl_path, 'w') as f:
            f.write(str(parsed_domain.domain))
    
    def load_problem(self, problem_pddl_path: str, parsed_domain: ParsedDomain, statecxt: StateContext) -> Any:
        problem: Problem = pddl_parse_problem(problem_pddl_path)
        
        if statecxt is None:
            return problem
        
        if parsed_domain.constant_map is not None:
            statecxt.add_objects(parsed_domain.constant_map)
            
        if problem.objects is not None:
            objects_map = defaultdict(set)
            for decl_object in problem.objects:
                decl_object_type = next(iter(decl_object.type_tags))
                objects_map[decl_object_type].add(decl_object.name)
            if len(objects_map) > 0:
                statecxt.add_objects(objects_map)
                
        if problem.init is not None:
            for init_stmt in problem.init:
                if isinstance(init_stmt, EqualTo):
                    numeric_fn, numeric_value = init_stmt.operands
                    if numeric_fn.terms:
                        args = [term.name for term in numeric_fn.terms]
                        fact = FactAtom(numeric_fn.name, *args)
                    else:
                        fact = FactAtom(numeric_fn.name)
                    statecxt.add_function((fact, numeric_value.value))
                else:
                    if init_stmt.terms:
                        args = [term.name for term in init_stmt.terms]
                        fact = FactAtom(init_stmt.name, *args)
                    else:
                        fact = FactAtom(init_stmt.name)
                    statecxt.add_fact(fact)
                    
        if parsed_domain.domain.derived_predicates:
            for derived_predicate in parsed_domain.domain.derived_predicates:
                statecxt.add_adjunct(self._build_adjunt_context(derived_predicate, statecxt))
                
        return problem
    
    def _build_adjunt_context(self, derived_predicate, statecxt: PlanStateContext) -> AdjunctContext:
        predicate_name = derived_predicate.predicate.name 
        dep_names = set()
        self._get_predicates(derived_predicate.condition, dep_names)
        if len(dep_names) <= 0:
            raise ValueError(f"Could not locate dependent predicates for derived predicate rule: {derived_predicate}")
        adjunct_cxt = AdjunctContext(statecxt, derived_predicate, predicate_name, dep_names)
        return adjunct_cxt
    
    def _get_predicates(self, stmt, curr_set: Set[str]):
        if isinstance(stmt, Predicate):
            curr_set.add(stmt.name)
            return
        elif isinstance(stmt, BinaryOp):
            for op in stmt.operands:
                self._get_predicates(op, curr_set)
            return
        elif isinstance(stmt, UnaryOp):
            self._get_predicates(stmt.argument, curr_set)
            return
        
    def store_problem(self, problem: Any, problem_pddl_path: str) -> None:
        with open(problem_pddl_path, 'w') as f:
            f.write(str(problem))
    
    def build_action_processor(self, parsed_domain: ParsedDomain) -> "ActionProcessor":
        return PddlActionProcessor(parsed_domain.subtypes)
    
    @staticmethod
    def _subtype_closure(parent_of: Dict[str, str]) -> Dict[str, Set[str]]:
        direct_children_of = defaultdict(list)
        for object_type, parent_type in parent_of.items():
            if parent_type:
                direct_children_of[parent_type].append(object_type)
                
        subtypes = {}
        for object_type in parent_of:
            seen = set()
            stack = [object_type]
            while stack:
                curr_type = stack.pop()
                if curr_type in seen:
                    continue
                
                stack.extend(direct_children_of.get(curr_type, []))
                seen.add(curr_type)
            subtypes[object_type] = seen
        return subtypes
    
    def rebuild_adjunct_context(self, adjunt_contexts: List[AdjunctContext]):
        for adjunct_cxt in adjunt_contexts:
            adjunct_cxt.reset()
        
        builder = DerivationEngine(adjunt_contexts[0]._owner, adjunt_contexts)
        builder.infer_derived()
        return
    
    def parse_condition(self, cond_str: str) -> Any:
        return self._condition_parser.parse_condition(cond_str)
    
class DerivedRule(DerivedPredicateSpec):
    def __init__(self, derived: DerivedPredicate):
        super().__init__(derived, derived.predicate.name, DerivedRule.to_dep_preds(derived.condition, set()))

    def __hash__(self):
        return id(self)

    def __eq__(self, other: object) -> bool:
        return self is other

    @staticmethod
    def to_dep_preds(cond, dep_preds):
        if isinstance(cond, Predicate):
            dep_preds.add(cond.name)
        elif isinstance(cond, And):
            for op in cond.operands:
                DerivedRule.to_dep_preds(op, dep_preds)
        return dep_preds
    
    @staticmethod
    def compute_global_order(rules: List["DerivedRule"]) -> List["DerivedRule"]:
        graph: Dict[DerivedRule, List[DerivedRule]] = defaultdict(list)
        indegree: Dict[DerivedRule, int] = {rule: 0 for rule in rules}

        head_map: Dict[str, List[DerivedRule]] = defaultdict(list)
        for rule in rules:
            head_map[rule.predicate].append(rule)

        for rule in rules:
            for dep in rule.depend_preds:
                for parent in head_map.get(dep, []):
                    if parent is rule:
                        continue
                    graph[parent].append(rule)
                    indegree[rule] += 1

        queue = deque([r for r, deg in indegree.items() if deg == 0])
        ordered: List[DerivedRule] = []

        while queue:
            curr = queue.popleft()
            ordered.append(curr)
            for child in graph[curr]:
                indegree[child] -= 1
                if indegree[child] == 0:
                    queue.append(child)

        remaining = [r for r, deg in indegree.items() if deg > 0 and r not in ordered]
        ordered.extend(remaining)

        return ordered
    
    def __repr__(self):
        return self.predicate
    
Substitution = Dict[str, str]
class DerivationEngine:
    def __init__(self, base_state: PlanStateContext, affected_adjunct_contexts: List[AdjunctContext]):
        self.base: PlanStateContext = base_state
        self.affected: List[AdjunctContext] = affected_adjunct_contexts
        self._current_adjunct_cxt: AdjunctContext = None
        self._partial_bindings_list: List[Substitution] = None
        
    def infer_derived(self):
        updated = True
        while updated:
            updated = False
            for adjunct_cxt in self.affected:
                self._current_adjunct_cxt = adjunct_cxt
                updated = self._update_adjunct_context(adjunct_cxt) or updated
    
    def _update_adjunct_context(self, adjunct_cxt: AdjunctContext) -> bool:
        derived_pred_rule: DerivedPredicate = adjunct_cxt._derived_predicate_def
        cond = derived_pred_rule.condition
        head_atom = to_fact_atom(derived_pred_rule.predicate)
        self._partial_bindings_list: List[Substitution] = [ {} ]
        
        if isinstance(cond, And):
            for body_literal in cond.operands:
                self._process_literal(body_literal)
        elif isinstance(cond, Predicate):
            self._process_literal(cond)
        else:
            raise ValueError("Unsupported condition for ")
        
        if not self._partial_bindings_list:
            return False
    
        updated = False
        for complete_bindings in self._partial_bindings_list:
            new_atom = self.ground(head_atom, complete_bindings)
            updated = adjunct_cxt.add_fact(new_atom) or updated
    
        return updated
    
    def _process_literal(self, body_literal: Formula):
        if isinstance(body_literal, Predicate):
            body_literal = to_fact_atom(body_literal)
            
        next_bindings_list: List[Substitution] = []
        for existing_bindings in self._partial_bindings_list:
            grounded_pattern = self.ground(body_literal, existing_bindings)
            candidate_atoms = self._get_candidate_atoms(grounded_pattern)

            for candidate_atom in candidate_atoms:
                new_bindings = self.unify(grounded_pattern, candidate_atom)
                
                if new_bindings is None:
                    continue            

                merged_bindings = existing_bindings.copy()
                merged_bindings.update(new_bindings)
                next_bindings_list.append(merged_bindings)

        self._partial_bindings_list = next_bindings_list
    
    @staticmethod
    def unify(pattern: FactAtom, other: FactAtom, subs: Substitution = None) -> Optional[Substitution]:
        subs = {} if subs is None else subs
        for patternElm, otherElm in zip(pattern, other):
            if patternElm.startswith('?'):
                if patternElm in subs and subs[patternElm] != otherElm:
                    return None
                subs[patternElm] = otherElm
            elif patternElm != otherElm:
                return None
        return subs

    @staticmethod
    def ground(atom: FactAtom, subs: Substitution) -> FactAtom:
        grounded: List[str] = []
        for part in atom:
            grounded.append(subs.get(part, part))
        return FactAtom(*grounded)
    
    def _get_candidate_atoms(self, literal_pattern: FactAtom) -> List[FactAtom]:
        base_candidates    = self.base.index.fetch_candidates(literal_pattern)
        ret_candidates = []
        if base_candidates:
            ret_candidates.extend(base_candidates)
            
        for adjunct_cxt in self.affected:
            derived_candidates = adjunct_cxt.index.fetch_candidates(literal_pattern)
            if derived_candidates:
                ret_candidates.extend(derived_candidates)
        return ret_candidates
    
class PddlActionProcessor(ActionProcessor):
    def __init__(self, subtypes: Dict[str, Set[str]]):
        self.subtypes = subtypes
    
    def is_ready(
        self,
        action_args: List[str], action_def: Any,
        state_cxt: PlanStateContext
    ) -> bool:
        var_map = {p.name: a for p, a in zip(action_def.parameters, action_args)}
        
        if hasattr(action_def, 'conditions'): # Durative Action check
            for op in action_def.conditions:
                if op.modifier.name in ("AT_START", "OVER_ALL"):
                    if not self._eval(op.formula, var_map, state_cxt):
                        return False
            return True
        return self._eval(action_def.precondition, var_map, state_cxt)

    def propagate_effects(
        self,
        action_args: List[str],
        action_def: Any,
        state_cxt: PlanStateContext,
        stage: str = "at end"
    ):
        var_map = {p.name: a for p, a in zip(action_def.parameters, action_args)}
        if hasattr(action_def, 'effects'): # Durative Action check
            if stage == "at start":
                for op in action_def.effects:
                    if op.modifier.name in ("AT_START", "OVER_ALL"):
                        self._apply(op.formula, var_map, state_cxt)
            elif stage == "at end":
                for op in action_def.effects:
                    if op.modifier.name in ("AT_END", "OVER_ALL"):
                        self._apply(op.formula, var_map, state_cxt)
            return
        
        self._apply(action_def.effect, var_map, state_cxt)
        
    def eval_condition(self, cond_def: Any, state_cxt: StateContext, var_map: Dict[str, str] = None) -> bool:
        return self._eval(cond_def, var_map or {}, state_cxt)
        
    def _all_objects_of(self, type_name: str, ctxt: PlanStateContext) -> List[str]:
        if type_name is not None and type_name != "object":
            objs = set()
            for subtype in self.subtypes.get(type_name, {type_name}):
                objs.update(ctxt.object_types.get(subtype, []))
            return objs
        else:
            return ctxt.all_objects

    def _all_objects_for_types(self, type_iter: Iterable[str], ctxt: PlanStateContext) -> List[str]:
        ret = set()
        for type_name in type_iter:
            if type_name is not None and type_name != "object":
                for subtype in self.subtypes.get(type_name, {type_name}):
                    ret.update(ctxt.object_types.get(subtype, []))
            else:
                return ctxt.all_objects
        return ret
        
    def _make_atom(self, pred: Predicate, var_map: Dict[str, str]) -> FactAtom:
        args = []
        for a in pred.terms:
            if hasattr(a, 'name') and a.name in var_map:
                args.append(var_map[a.name])
            else:
                args.append(a.name)
        return FactAtom(pred.name, *args)
    
    def _eval(self, cond, var_map, ctxt) -> bool:
        if isinstance(cond, And):
            return all(self._eval(c, var_map, ctxt) for c in cond.operands)
        if isinstance(cond, Or):
            return any(self._eval(c, var_map, ctxt) for c in cond.operands)
        if isinstance(cond, Not):
            return not self._eval(cond.argument, var_map, ctxt)

        if isinstance(cond, ForallCondition):
            return self._eval_forall(cond, var_map, ctxt)
        if isinstance(cond, ExistsCondition):
            return self._eval_exists(cond, var_map, ctxt)

        if isinstance(cond, Predicate):
            return ctxt.has_fact(self._make_atom(cond, var_map))
        if isinstance(cond, (GreaterThan, GreaterEqualThan,
            LesserThan, LesserEqualThan, EqualTo)):
            return self._eval_comparison(cond, var_map, ctxt)
        if isinstance(cond, EqualToPredicate):
            return self._eval_equalto(cond, var_map, ctxt)
        
        raise NotImplementedError(f"Unknown condition: {type(cond)}")

    def _eval_forall(self, forall_cond: ForallCondition, var_map: Dict[str,str], ctxt) -> bool:
        src = {}
        for v in forall_cond.variables:
            objs = self._all_objects_for_types(iter(v.type_tags), ctxt)
            if not objs:
                return True
            src[v.name] = objs

        for combo in itertools.product(*src.values()):
            new_map = {**var_map, **dict(zip(src.keys(), combo))}
            if not self._eval(forall_cond.condition, new_map, ctxt):
                return False
        return True

    def _eval_exists(self, exists_cond: ExistsCondition, var_map: Dict[str,str], ctxt) -> bool:
        src = {}
        for v in exists_cond.variables:
            objs = self._all_objects_for_types(iter(v.type_tags), ctxt)
            if not objs:
                return False
            src[v.name] = objs

        for combo in itertools.product(*src.values()):
            new_map = {**var_map, **dict(zip(src.keys(), combo))}
            if self._eval(exists_cond.condition, new_map, ctxt):
                return True
        return False

    def _eval_comparison(self, cond, var_map: Dict[str,str], ctxt) -> bool:
        left_expr, right_expr = cond.operands
        lv = self._eval_numeric(left_expr, var_map, ctxt)
        rv = self._eval_numeric(right_expr, var_map, ctxt)

        if isinstance(cond, GreaterThan):      return lv >  rv
        if isinstance(cond, GreaterEqualThan): return lv >= rv
        if isinstance(cond, LesserThan):       return lv <  rv
        if isinstance(cond, LesserEqualThan):  return lv <= rv
        return lv == rv
    
    def _eval_equalto(self, cond, var_map: Dict[str,str], ctxt) -> bool:
        left_val = self._eval_var_or_numeric(cond.left, var_map, ctxt)
        right_val = self._eval_var_or_numeric(cond.right, var_map, ctxt)
        return left_val == right_val

    def _eval_var_or_numeric(self, expr, var_map, ctxt) -> float | str:
        if isinstance(expr, Variable):
            return var_map[expr.name]
        else:
            return self._eval_numeric(expr, var_map, ctxt)
        
    def _eval_numeric(self, expr, var_map, ctxt) -> float:
        if isinstance(expr, NumericValue):
            return expr.value

        if isinstance(expr, NumericFunction):
            atom = self._make_fn_atom(expr, var_map)
            return ctxt.get_function(atom) or 0.0

        if isinstance(expr, Plus):
            a, b = expr.operands
            return self._eval_numeric(a, var_map, ctxt) + self._eval_numeric(b, var_map, ctxt)
        if isinstance(expr, Minus):
            a, b = expr.operands
            return self._eval_numeric(a, var_map, ctxt) - self._eval_numeric(b, var_map, ctxt)
        if isinstance(expr, Times):
            a, b = expr.operands
            return self._eval_numeric(a, var_map, ctxt) * self._eval_numeric(b, var_map, ctxt)
        if isinstance(expr, Divide):
            a, b = expr.operands
            return self._eval_numeric(a, var_map, ctxt) / self._eval_numeric(b, var_map, ctxt)

        raise NotImplementedError(f"Cannot evaluate numeric expression: {expr}")
    
    def _apply(self, eff, var_map: Dict[str, str], ctxt: PlanStateContext):
        if isinstance(eff, And):
            for operand in eff.operands:
                self._apply(operand, var_map, ctxt)
            return
        if isinstance(eff, Not):
            if not isinstance(eff.argument, Predicate):
                raise ValueError(f"Illegal effect: A Not formula cannot remove non-atomic argument: {eff.argument}")
            atom = self._make_atom(eff.argument, var_map)
            ctxt.remove_fact(atom)
            return
        if isinstance(eff, When):
            if self._eval(eff.condition, var_map, ctxt):
                self._apply(eff.effect, var_map, ctxt)
            return
        if isinstance(eff, Forall):
            self._apply_forall(eff, var_map, ctxt)
            return
        if isinstance(eff, (Increase, Decrease, Assign, ScaleUp, ScaleDown)):
            fn_expr, val_expr = eff.operands
            atom = self._make_fn_atom(fn_expr, var_map)
            old  = ctxt.get_function(atom) or 0.0
            delta = self._eval_numeric(val_expr, var_map, ctxt)

            if isinstance(eff, Increase):    new = old + delta
            elif isinstance(eff, Decrease):  new = old - delta
            elif isinstance(eff, ScaleUp):   new = old * delta
            elif isinstance(eff, ScaleDown): new = old / delta
            else:                            new = delta

            ctxt.add_function((atom, new))
            return
        if isinstance(eff, Predicate):
            atom = self._make_atom(eff, var_map)
            ctxt.add_fact(atom)
            return
        raise NotImplementedError(f"Unknown effect type: {type(eff)}")
    
    def _make_fn_atom(self, fn: NumericFunction, var_map: Dict[str, str]) -> FactAtom:
        args = []
        for term in fn.terms:
            if hasattr(term, "name") and term.name in var_map:
                args.append(var_map[term.name])
            else:
                args.append(term)
        return FactAtom(fn.name, *args)
    
    def _apply_forall(self, forall, var_map, ctxt) -> None:
        variable: Variable = None
        src_dict = {}
        for variable  in forall.variables:
            var_name = variable.name
            objs = self._all_objects_for_types(iter(variable.type_tags), ctxt)
            if objs is None or len(objs) == 0:
                return
            src_dict[var_name] = objs
        
        keys = list(src_dict)
        value_lists = [src_dict[k] for k in keys]
        for values in itertools.product(*value_lists): 
            comb_dict = dict(zip(keys, values))
            comb_dict.update(var_map)
            self._apply(forall.effect, comb_dict, ctxt)
        return
