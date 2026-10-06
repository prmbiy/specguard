"""Validated, label-private adapter for the structured LiveCodeBench data."""

from __future__ import annotations

import ast
import hashlib
import json
import random
import re
from dataclasses import dataclass
from pathlib import Path


class DataIntegrityError(ValueError):
    pass


@dataclass(frozen=True)
class LeanType:
    kind: str
    args: tuple["LeanType", ...] = ()

    @property
    def source(self) -> str:
        if self.kind == "List":
            return f"List {self._wrapped(self.args[0])}"
        if self.kind == "Option":
            return f"Option {self._wrapped(self.args[0])}"
        if self.kind == "Prod":
            return "(" + " × ".join(arg.source for arg in self.args) + ")"
        return self.kind

    @staticmethod
    def _wrapped(value: "LeanType") -> str:
        return value.source if value.kind in {"Int", "String", "Bool"} else f"({value.source})"

    def accepts(self, value) -> bool:
        if self.kind == "Int":
            return isinstance(value, int) and not isinstance(value, bool)
        if self.kind == "String":
            return isinstance(value, str)
        if self.kind == "Bool":
            return isinstance(value, bool)
        if self.kind == "List":
            return isinstance(value, list) and all(self.args[0].accepts(v) for v in value)
        if self.kind == "Option":
            return value is None or self.args[0].accepts(value)
        if self.kind == "Prod":
            return (isinstance(value, (list, tuple)) and len(value) == len(self.args)
                    and all(t.accepts(v) for t, v in zip(self.args, value)))
        return False

    def render(self, value) -> str:
        if not self.accepts(value):
            raise DataIntegrityError(f"value {value!r} does not have Lean type {self.source}")
        if self.kind == "Int":
            return str(value)
        if self.kind == "Bool":
            return "true" if value else "false"
        if self.kind == "String":
            return _lean_string(value)
        if self.kind == "List":
            return "[" + ", ".join(self.args[0].render(v) for v in value) + "]"
        if self.kind == "Option":
            return "none" if value is None else f"some ({self.args[0].render(value)})"
        if self.kind == "Prod":
            return "(" + ", ".join(t.render(v) for t, v in zip(self.args, value)) + ")"
        raise DataIntegrityError(f"cannot render {self.source}")


@dataclass(frozen=True)
class Parameter:
    name: str
    lean_type: LeanType


@dataclass(frozen=True)
class Signature:
    parameters: tuple[Parameter, ...]
    output: LeanType

    @property
    def declarations(self) -> str:
        fields = "\n".join(
            f"  {parameter.name} : {parameter.lean_type.source}"
            for parameter in self.parameters
        )
        return (
            "namespace Spec\n\n"
            f"structure Input where\n{fields}\n"
            "deriving Repr\n\n"
            f"abbrev Output := {self.output.source}\n\n"
            "end Spec"
        )

    def render_input(self, values: list) -> str:
        if len(values) != len(self.parameters):
            raise DataIntegrityError(
                f"expected {len(self.parameters)} arguments, received {len(values)}")
        fields = ", ".join(
            f"{parameter.name} := {parameter.lean_type.render(value)}"
            for parameter, value in zip(self.parameters, values)
        )
        return f"{{ {fields} }}"


@dataclass(frozen=True)
class Case:
    args: tuple
    expected: object


@dataclass(frozen=True)
class PrivateOrder:
    good_test_number: int
    bad_test_number: int

    @property
    def ground_truth(self) -> tuple[bool, bool]:
        return tuple(i == self.good_test_number for i in range(2))  # type: ignore[return-value]


@dataclass(frozen=True)
class AgentTask:
    task_id: str
    entry_point: str
    description: str
    signature: Signature
    public_cases: tuple[Case, ...]
    test_0: tuple[Case, ...]
    test_1: tuple[Case, ...]


def _lean_string(value: str) -> str:
    escaped = []
    for ch in value:
        escaped.append({
            "\\": "\\\\", '"': '\\"', "\n": "\\n", "\r": "\\r", "\t": "\\t",
        }.get(ch, ch if ord(ch) >= 32 else f"\\u{{{ord(ch):x}}}"))
    return '"' + "".join(escaped) + '"'


def _annotation_name(node: ast.AST) -> str:
    if isinstance(node, ast.Subscript):
        return _annotation_name(node.value)
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _infer_value_type(values: list) -> LeanType:
    nonempty = [value for value in values if value not in (None, [])]
    if not nonempty:
        raise DataIntegrityError("cannot infer an unannotated list type from only empty values")
    sample = nonempty[0]
    if isinstance(sample, list):
        if sample and all(isinstance(v, (list, tuple)) for v in sample):
            widths = {len(v) for value in nonempty for v in value}
            if len(widths) == 1:
                width = widths.pop()
                parts = tuple(_infer_value_type(
                    [item[i] for value in nonempty for item in value]) for i in range(width))
                return LeanType("List", (LeanType("Prod", parts),))
        elements = [item for value in nonempty for item in value]
        return LeanType("List", (_infer_value_type(elements),))
    if isinstance(sample, bool) and all(isinstance(v, bool) for v in nonempty):
        return LeanType("Bool")
    if isinstance(sample, int) and all(isinstance(v, int) and not isinstance(v, bool)
                                       for v in nonempty):
        return LeanType("Int")
    if isinstance(sample, str) and all(isinstance(v, str) for v in nonempty):
        return LeanType("String")
    raise DataIntegrityError(f"cannot infer Lean type from values like {sample!r}")


def _parse_type(node: ast.AST | None, observed: list) -> LeanType:
    if node is None:
        return _infer_value_type(observed)
    name = _annotation_name(node)
    if name == "int":
        return LeanType("Int")
    if name == "str":
        return LeanType("String")
    if name == "bool":
        return LeanType("Bool")
    if name in {"List", "list"}:
        if not isinstance(node, ast.Subscript):
            return _infer_value_type(observed)
        return LeanType("List", (_parse_type(node.slice, [x for v in observed for x in v]),))
    if name in {"Tuple", "tuple"} and isinstance(node, ast.Subscript):
        elements = node.slice.elts if isinstance(node.slice, ast.Tuple) else [node.slice]
        return LeanType("Prod", tuple(_parse_type(element, [v[i] for v in observed])
                                      for i, element in enumerate(elements)))
    if name == "Optional" and isinstance(node, ast.Subscript):
        return LeanType("Option", (_parse_type(
            node.slice, [v for v in observed if v is not None]),))
    raise DataIntegrityError(f"unsupported Python annotation: {ast.unparse(node)}")


def _function(task: dict) -> ast.FunctionDef:
    try:
        module = ast.parse(task["description"])
    except (SyntaxError, TypeError) as exc:
        raise DataIntegrityError(f"invalid problem description: {exc}") from exc
    matches = [node for node in module.body if isinstance(node, ast.FunctionDef)
               and node.name == task.get("entry_point")]
    if len(matches) != 1:
        raise DataIntegrityError("description must define the entry point exactly once")
    return matches[0]


def _signature(task: dict, tests: list[dict]) -> Signature:
    function = _function(task)
    if any(arg.annotation is None for arg in function.args.args) or function.returns is None:
        raise DataIntegrityError("entry point must have complete Python type annotations")
    parameters = []
    for index, argument in enumerate(function.args.args):
        observed = [test["args"][index] for test in tests]
        parameters.append(Parameter(argument.arg, _parse_type(argument.annotation, observed)))
    output = _parse_type(function.returns, [test["expected"] for test in tests])
    return Signature(tuple(parameters), output)


def _public_cases(task: dict, signature: Signature) -> tuple[Case, ...]:
    pattern = re.compile(
        rf"^\s*>>>\s*{re.escape(task['entry_point'])}\((.*)\)\s*\n\s*(.+?)\s*$",
        re.MULTILINE,
    )
    cases = []
    for args_text, expected_text in pattern.findall(task["description"]):
        try:
            call = ast.parse(f"f({args_text})", mode="eval").body
            assert isinstance(call, ast.Call) and not call.keywords
            args = [ast.literal_eval(arg) for arg in call.args]
            expected = ast.literal_eval(expected_text)
            signature.render_input(args)
            signature.output.render(expected)
        except Exception as exc:
            raise DataIntegrityError(f"invalid public example: {args_text!r}: {exc}") from exc
        cases.append(Case(tuple(args), expected))
    return tuple(cases)


def prepare_task(task: dict, *, order_seed: int) -> tuple[AgentTask, PrivateOrder]:
    required = {"task_id", "entry_point", "description", "original_tests"}
    missing = sorted(required - task.keys())
    if missing:
        raise DataIntegrityError(f"missing fields: {missing}")
    original = task["original_tests"]
    if not isinstance(original, list):
        raise DataIntegrityError("tests must be structured data")

    # Preserve the suite exactly as stored, but reject contradictory assertions
    # for the same input.
    by_args: dict[str, dict] = {}
    for test in original:
        if not isinstance(test, dict) or not isinstance(test.get("args"), list) \
                or "expected" not in test:
            raise DataIntegrityError("malformed original test")
        key = json.dumps(test["args"], sort_keys=True, ensure_ascii=False)
        previous = by_args.get(key)
        if previous is not None and previous["expected"] != test["expected"]:
            raise DataIntegrityError("original tests contradict each other")
        by_args[key] = test
    validated_original = list(original)

    if "modified_tests" in task:
        modified = task["modified_tests"]
        if not isinstance(modified, list) or not modified:
            raise DataIntegrityError("modified_tests must be a nonempty list")
        for test in modified:
            if not isinstance(test, dict) or not isinstance(test.get("args"), list) \
                    or "expected" not in test:
                raise DataIntegrityError("malformed modified test")
        original_counts = {}
        for test in validated_original:
            key = json.dumps([test["args"], test["expected"]],
                             sort_keys=True, ensure_ascii=False)
            original_counts[key] = original_counts.get(key, 0) + 1
        extra = []
        for test in modified:
            key = json.dumps([test["args"], test["expected"]],
                             sort_keys=True, ensure_ascii=False)
            if original_counts.get(key, 0):
                original_counts[key] -= 1
            else:
                extra.append(test)
        if any(original_counts.values()) or len(extra) != 1:
            raise DataIntegrityError(
                "modified suite must contain the original suite plus one assertion")
        added = extra[0]
        input_key = json.dumps(added["args"], sort_keys=True, ensure_ascii=False)
        matching = by_args.get(input_key)
        if matching is None or matching["expected"] == added["expected"]:
            raise DataIntegrityError(
                "added assertion must contradict an existing input")
        signature = _signature(task, validated_original + modified)
        mutation = added
    else:
        corrupted = task.get("corrupted_test")
        if not isinstance(corrupted, dict) or not isinstance(corrupted.get("args"), list) \
                or "expected" not in corrupted or "original_expected" not in corrupted:
            raise DataIntegrityError("malformed corrupted test")
        key = json.dumps(corrupted["args"], sort_keys=True, ensure_ascii=False)
        matching = by_args.get(key)
        if matching is None or matching["expected"] != corrupted["original_expected"]:
            raise DataIntegrityError("corrupted test does not match its original assertion")
        if corrupted["expected"] == corrupted["original_expected"]:
            raise DataIntegrityError("corrupted expected value did not change")
        signature = _signature(task, validated_original + [corrupted])
        mutation = corrupted

    good = tuple(Case(tuple(test["args"]), test["expected"])
                 for test in validated_original)
    if "modified_tests" in task:
        bad = good + (Case(tuple(mutation["args"]), mutation["expected"]),)
    else:
        bad = tuple(Case(case.args, mutation["expected"]
                         if list(case.args) == mutation["args"] else case.expected)
                    for case in good)
    for case in (*good, *bad):
        signature.render_input(list(case.args))
        signature.output.render(case.expected)

    seed_bytes = hashlib.sha256(f"{order_seed}:{task['task_id']}".encode()).digest()
    good_number = random.Random(seed_bytes).randrange(2)
    order = PrivateOrder(good_number, 1 - good_number)
    tests = (good, bad) if good_number == 0 else (bad, good)
    agent_task = AgentTask(
        task_id=task["task_id"], entry_point=task["entry_point"],
        description=task["description"], signature=signature,
        public_cases=_public_cases(task, signature), test_0=tests[0], test_1=tests[1],
    )
    return agent_task, order


def load_tasks(path: Path) -> list[dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    tasks = payload.get("tasks") if isinstance(payload, dict) else None
    if not isinstance(tasks, list):
        raise DataIntegrityError("dataset must contain a tasks list")
    ids = [task.get("task_id") for task in tasks if isinstance(task, dict)]
    if len(ids) != len(tasks) or len(set(ids)) != len(ids):
        raise DataIntegrityError("dataset task IDs must be present and unique")
    return tasks
