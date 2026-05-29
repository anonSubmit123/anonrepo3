import re
import os
from collections import defaultdict
from typing import Any, Dict, List, Optional, Union
from baseproc.basestore import FactAtom

class PddlProblemParser:
    def __init__(self) -> None:
        self.problem_name: Optional[str] = None
        self.domain_name: Optional[str] = None
        self.objects: Dict[str, List[str]] = {}
        self.init_statements: List[FactAtom] = []
        self.goal_conditions: List[Any] = []
        self.metric: Optional[List[Any]] = None
        self.constraints: Optional[List[Any]] = None

    @staticmethod
    def _parse_sexpr(text: str) -> List[Any]:
        spaced: str = re.sub(r'([\(\)])', r' \1 ', text)
        tokens: List[str] = spaced.split()

        def parse_tokens(tokens_list: List[str]) -> List[Any]:
            stack: List[List[Any]] = [[]]
            for tok in tokens_list:
                if tok == '(':
                    stack.append([])
                elif tok == ')':
                    if len(stack) < 2:
                        raise ValueError("Unbalanced parentheses in PDDL S-expression")
                    node: List[Any] = stack.pop()
                    stack[-1].append(node)
                else:
                    stack[-1].append(tok)
            if len(stack) != 1:
                raise ValueError("Unbalanced parentheses in PDDL S-expression")
            return stack[0]

        return parse_tokens(tokens)

    @staticmethod
    def _parse_typed_objects(obj_tokens: List[str]) -> Dict[str, List[str]]:
        by_type: Dict[str, List[str]] = defaultdict(list)
        current_names: List[str] = []
        i: int = 0

        while i < len(obj_tokens):
            token: str = obj_tokens[i]
            if token == '-':
                if i + 1 >= len(obj_tokens):
                    raise ValueError("Syntax error in :objects: '-' with no type following")
                typ: str = obj_tokens[i + 1]
                for name in current_names:
                    by_type[typ].append(name)
                current_names = []
                i += 2
            else:
                current_names.append(token)
                i += 1

        for name in current_names:
            by_type["object"].append(name)

        return dict(by_type)

    @staticmethod
    def _sexpr_to_string(expr: Union[str, List[Any], FactAtom]) -> str:
        if isinstance(expr, FactAtom):
            inner = " ".join(str(x) for x in expr)
            return f"({inner})"
        if isinstance(expr, str):
            return expr
        if isinstance(expr, list):
            inner: str = " ".join(PddlProblemParser._sexpr_to_string(e) for e in expr)
            return f"({inner})"
        raise ValueError("Unexpected expression type in _sexpr_to_string")

    @staticmethod
    def _convert_to_factatoms(node: Any) -> Any:
        if isinstance(node, list):
            if all(isinstance(elem, str) for elem in node):
                return FactAtom(*node)
            return [PddlProblemParser._convert_to_factatoms(child) for child in node]
        return node

    def parse_text(self, text: str) -> None:
        cleaned = self._remove_comments(text)
        parsed: List[Any] = self._parse_sexpr(cleaned)

        define_list: Optional[List[Any]] = None
        for elem in parsed:
            if isinstance(elem, list) and elem and elem[0] == 'define':
                define_list = elem
                break
        if not define_list:
            raise ValueError("No '(define ...)' block found in the PDDL text.")

        raw_objects: Optional[List[str]] = None
        self.problem_name = None
        self.domain_name = None
        self.objects = {}
        self.init_statements = []
        self.goal_conditions = []
        self.metric = None
        self.constraints = None

        for section in define_list[1:]:
            if not isinstance(section, list) or not section:
                continue
            head = section[0]
            if head == 'problem' and len(section) >= 2:
                self.problem_name = section[1]
            elif head == ':domain' and len(section) >= 2:
                self.domain_name = section[1]
            elif head == ':objects':
                raw_objects = section[1:]
            elif head == ':init':
                for literal in section[1:]:
                    if isinstance(literal, list) and all(isinstance(tok, str) for tok in literal):
                        self.init_statements.append(FactAtom(*literal))
                    else:
                        raise ValueError(f"Init section contains non-atomic entry: {literal}")
            elif head == ':goal':
                converted: List[Any] = []
                for cond in section[1:]:
                    converted.append(self._convert_to_factatoms(cond))
                self.goal_conditions = converted
            elif head == ':metric':
                self.metric = section[1:]
            elif head == ':constraints':
                self.constraints = section[1:]

        if raw_objects is None:
            self.objects = {}
        else:
            self.objects = self._parse_typed_objects(raw_objects)

    def parse_file(self, path: str) -> None:
        with open(path, 'r') as f:
            text: str = f.read()
        self.parse_text(text)

    def __str__(self) -> str:
        if not self.problem_name or not self.domain_name:
            raise ValueError("__str__ called before parsing a valid problem")

        lines: List[str] = []

        lines.append(f"(define (problem {self.problem_name})")
        lines.append(f"  (:domain {self.domain_name})")

        if self.objects:
            lines.append("  (:objects")
            for typ in sorted(self.objects.keys()):
                obj_list: List[str] = self.objects[typ]
                if not obj_list:
                    continue
                names: str = " ".join(obj_list)
                lines.append(f"    {names} - {typ}")
            lines.append("  )")

        if self.init_statements:
            lines.append("  (:init")
            for atom in self.init_statements:
                s: str = self._sexpr_to_string(atom)
                lines.append(f"    {s}")
            lines.append("  )")

        if self.goal_conditions:
            lines.append("  (:goal")
            for condition in self.goal_conditions:
                s: str = self._sexpr_to_string(condition)
                lines.append(f"    {s}")
            lines.append("  )")

        if self.metric is not None:
            s: str = self._sexpr_to_string(self.metric)
            lines.append(f"  (:metric {s})")

        if self.constraints is not None:
            lines.append("  (:constraints")
            for constraint_expr in self.constraints:
                s: str = self._sexpr_to_string(constraint_expr)
                lines.append(f"    {s}")
            lines.append("  )")

        lines.append(")")

        return "\n".join(lines)
    
    @staticmethod
    def _remove_comments(text: str) -> str:
        return re.sub(r';;[^\n]*', '', text)
    
class ProblemTemplateGenerator:
    OVERWRITE = "overwrite"
    CUSTOM_OBJECTS = "custom_objects"
    CUSTOM_INIT = "custom_init"
    CUSTOM_GOAL = "custom_goal"
    
    def __init__(self, parser: PddlProblemParser) -> None:
        self.parser = parser

    def _render_original_objects(self) -> str:
        lines: List[str] = []
        for typ, names in self.parser.objects.items():
            if not names:
                continue
            joined_names = " ".join(names)
            lines.append(f"    {joined_names} - {typ}")
        return "\n".join(lines)

    def _render_original_init(self) -> str:
        lines: List[str] = []
        for atom in self.parser.init_statements:
            lines.append(f"    {atom}")
        return "\n".join(lines)

    def _render_original_goal(self) -> str:
        lines: List[str] = []
        if self.parser.goal_conditions:
            top = self.parser.goal_conditions[0]
            for sub in top[1:]:
                if isinstance(sub, FactAtom):
                    lines.append(f"      {sub}")
                else:
                    lines.append(f"      {sub}")
        return "\n".join(lines)

    def write_template(self, output_path: str) -> None:
        orig_objects = self._render_original_objects()
        orig_init = self._render_original_init()
        orig_goal = self._render_original_goal()

        template_lines: List[str] = []

        template_lines.append(f"(define (problem {self.parser.problem_name})")
        template_lines.append(f"  (:domain {self.parser.domain_name})")
        template_lines.append("")
        template_lines.append("  (:objects")
        template_lines.append("{%- if overwrite and custom_objects %}")
        template_lines.append("    {{ custom_objects }}")
        template_lines.append("{%- else %}")
        if orig_objects:
            template_lines.append(orig_objects)
        template_lines.append("{%- if custom_objects %}")
        template_lines.append("    {{ custom_objects }}")
        template_lines.append("{%- endif %}")
        template_lines.append("{%- endif %}")
        template_lines.append("  )")
        template_lines.append("")
        template_lines.append("  (:init")
        template_lines.append("{%- if overwrite and custom_init %}")
        template_lines.append("    {{ custom_init }}")
        template_lines.append("{%- else %}")
        if orig_init:
            template_lines.append(orig_init)
        template_lines.append("{%- if custom_init %}")
        template_lines.append("    {{ custom_init }}")
        template_lines.append("{%- endif %}") 
        template_lines.append("{%- endif %}")
        template_lines.append("  )")
        template_lines.append("")
        template_lines.append("  (:goal")
        template_lines.append("{%- if overwrite and custom_goal %}")
        template_lines.append("    (and")
        template_lines.append("    {{ custom_goal }}")
        template_lines.append("    )")
        template_lines.append("{%- else %}")
        template_lines.append("    (and")
        if orig_goal:
            template_lines.append(orig_goal)
        template_lines.append("{%- if custom_goal %}")
        template_lines.append("      {{ custom_goal }}")
        template_lines.append("{%- endif %}")
        template_lines.append("    )")
        template_lines.append("{%- endif %}")
        template_lines.append("  )")
        template_lines.append(")")

        template_str = "\n".join(template_lines)

        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, "w") as f:
            f.write(template_str)
