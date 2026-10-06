"""Strict artifact protocols for the SWE-bench Python track."""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from typing import Any


_PYTHON_BLOCK = re.compile(r"```(?:python|py)\s*\n(.*?)```", re.DOTALL)
_FORBIDDEN_IMPORTS = {
    "aiohttp", "asyncio", "ctypes", "ftplib", "http", "imaplib",
    "atexit", "importlib", "inspect", "io", "multiprocessing", "os", "paramiko", "pathlib", "poplib",
    "requests", "shutil", "signal", "smtplib", "socket", "subprocess",
    "sys", "tempfile", "threading", "urllib", "webbrowser", "xmlrpc",
}
_FORBIDDEN_CALLS = {
    "breakpoint", "compile", "eval", "exec", "getattr", "globals", "input",
    "locals", "open", "setattr", "vars", "__import__",
}


class ArtifactError(ValueError):
    """An LLM response violates its Python artifact contract."""


@dataclass
class PythonArtifact:
    code: str
    success: bool
    attempts_used: int
    conversation: list[dict] = field(default_factory=list)
    execution_output: str = ""
    error: str = ""
    infrastructure_error: bool = False


@dataclass
class TestArtifact(PythonArtifact):
    tests: list[dict[str, Any]] = field(default_factory=list)


def _one_block(response: str, marker: str) -> str:
    if marker not in response:
        raise ArtifactError(f"missing {marker} marker")
    blocks = _PYTHON_BLOCK.findall(response)
    if len(blocks) != 1:
        raise ArtifactError(f"expected exactly one Python block, found {len(blocks)}")
    body = blocks[0].strip()
    if not body:
        raise ArtifactError("empty Python artifact")
    return body


def _tree(source: str, label: str) -> ast.Module:
    try:
        return ast.parse(source)
    except SyntaxError as exc:
        raise ArtifactError(f"{label}: invalid Python: {exc.msg} at line {exc.lineno}") from exc


def validate_safe_python(source: str, *, label: str) -> ast.Module:
    """Reject direct escape hatches; Docker isolation remains the hard boundary."""
    tree = _tree(source, label)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = ([alias.name for alias in node.names] if isinstance(node, ast.Import)
                     else [node.module or ""])
            for name in names:
                root = name.split(".", 1)[0]
                if root in _FORBIDDEN_IMPORTS:
                    raise ArtifactError(f"{label}: forbidden import {root!r}")
        if isinstance(node, ast.Call):
            name = node.func.id if isinstance(node.func, ast.Name) else None
            if name in _FORBIDDEN_CALLS:
                raise ArtifactError(f"{label}: forbidden call {name!r}")
        if isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            raise ArtifactError(f"{label}: dunder attribute access is forbidden")
        if isinstance(node, ast.Name) and node.id.startswith("__") and node.id != "__name__":
            raise ArtifactError(f"{label}: dunder name access is forbidden")
    return tree


def parse_spec_response(response: str) -> str:
    body = _one_block(response, "SUBMIT_SPEC")
    tree = validate_safe_python(body, label="spec")
    runs = [node for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "run"]
    if len(runs) != 1 or isinstance(runs[0], ast.AsyncFunctionDef):
        raise ArtifactError("spec must define exactly one synchronous function named run")
    return body


def _literal_tests(tree: ast.Module) -> list[dict[str, Any]]:
    meaningful = [node for node in tree.body
                  if not (isinstance(node, ast.Expr)
                          and isinstance(node.value, ast.Constant)
                          and isinstance(node.value.value, str))]
    if len(meaningful) != 1 or not isinstance(meaningful[0], (ast.Assign, ast.AnnAssign)):
        raise ArtifactError("tests must contain only one literal TESTS assignment")
    node = meaningful[0]
    if isinstance(node, ast.Assign):
        if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
            raise ArtifactError("tests assignment target must be TESTS")
        name, value = node.targets[0].id, node.value
    else:
        if not isinstance(node.target, ast.Name) or node.value is None:
            raise ArtifactError("tests assignment target must be TESTS")
        name, value = node.target.id, node.value
    if name != "TESTS":
        raise ArtifactError("tests artifact must assign the literal named TESTS")
    try:
        result = ast.literal_eval(value)
    except (ValueError, TypeError, SyntaxError) as exc:
        raise ArtifactError("TESTS must be composed entirely of Python literals") from exc
    if not isinstance(result, list):
        raise ArtifactError("TESTS must be a list")
    return result


_PAIR_KEYS = {"input_number", "test_0", "test_1"}
_DEMAND_KEYS = {"relation", "expected", "source_lines"}


def validate_tests_schema(
    tests: object,
    *,
    patches: tuple[str, str],
    input_count: int,
) -> list[dict[str, Any]]:
    if not isinstance(tests, list) or not tests:
        raise ArtifactError("TESTS must be a nonempty list")
    normalized: list[dict[str, Any]] = []
    for index, pair in enumerate(tests):
        where = f"TESTS[{index}]"
        if not isinstance(pair, dict) or set(pair) != _PAIR_KEYS:
            raise ArtifactError(f"{where} must have exactly {sorted(_PAIR_KEYS)}")
        input_number = pair["input_number"]
        if type(input_number) is not int or not (0 <= input_number < input_count):
            raise ArtifactError(f"{where}: input_number must reference a frozen input")
        meanings = []
        for test_number in (0, 1):
            name = f"test_{test_number}"
            demand = pair[name]
            if demand is None:
                meanings.append(None)
                continue
            if not isinstance(demand, dict) or set(demand) != _DEMAND_KEYS:
                raise ArtifactError(
                    f"{where}.{name} must be None or have exactly {sorted(_DEMAND_KEYS)}")
            relation, source_lines = demand["relation"], demand["source_lines"]
            if not isinstance(relation, str) or not relation.strip():
                raise ArtifactError(f"{where}.{name}: relation must be a nonempty string")
            if (not isinstance(source_lines, list) or not source_lines
                    or any(not isinstance(line, str) or not line.strip()
                           for line in source_lines)):
                raise ArtifactError(
                    f"{where}.{name}: source_lines must quote nonempty patch lines")
            missing = [line for line in source_lines if line not in patches[test_number]]
            if missing:
                raise ArtifactError(
                    f"{where}.{name}: quoted source line is absent from {name}: {missing[0]!r}")
            meanings.append((relation, demand["expected"]))
        if meanings[0] is None and meanings[1] is None:
            raise ArtifactError(f"{where}: at least one test must contain the assertion")
        if meanings[0] == meanings[1]:
            raise ArtifactError(f"{where}: test_0 and test_1 make the same demand")
        normalized.append(pair)
    return normalized


def parse_tests_response(
    response: str,
    *,
    patches: tuple[str, str],
    input_count: int,
) -> tuple[str, list[dict[str, Any]]]:
    body = _one_block(response, "SUBMIT_TESTS")
    tree = _tree(body, "tests")
    tests = validate_tests_schema(
        _literal_tests(tree), patches=patches, input_count=input_count)
    return body, tests


def parse_connector_response(response: str) -> str:
    body = _one_block(response, "SUBMIT_CONNECTOR")
    tree = validate_safe_python(body, label="connector")
    checks = [node for node in tree.body
              if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "check"]
    if len(checks) != 1 or isinstance(checks[0], ast.AsyncFunctionDef):
        raise ArtifactError("connector must define exactly one synchronous function named check")
    return body
