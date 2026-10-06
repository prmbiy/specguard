"""Typed artifacts and strict text protocols for the SWE-bench Lean track.

LLM providers in this project expose text completions, so every generative stage
has a deliberately small fenced-code protocol.  This module is the trust boundary:
it parses responses, rejects dangerous Lean, renders imports mechanically, and
checks each public interface before another stage may consume it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from conflict_certifier.lean.runner import LeanRunner
from conflict_certifier.lean.safety import (
    LeanSafetyError,
    code_only as _code_only,
    validate_safe_lean as _validate_safe_lean,
)


_LEAN_BLOCK_RE = re.compile(r"```(?:lean4?|lean)\s*\n(.*?)```", re.DOTALL)


class ArtifactError(ValueError):
    """An LLM response violates its artifact protocol or Lean safety policy."""


@dataclass(frozen=True)
class AgentCase:
    instance_id: str
    description: str
    repo: str
    inputs: tuple[str, ...]
    input_context: str
    test_0_patch: str
    test_1_patch: str


@dataclass(frozen=True)
class PrivateTestOrder:
    good_test_number: int
    bad_test_number: int

    def __post_init__(self) -> None:
        if {self.good_test_number, self.bad_test_number} != {0, 1}:
            raise ValueError("test order must be a permutation of 0 and 1")

    @property
    def ground_truth(self) -> tuple[bool, bool]:
        return tuple(number == self.good_test_number for number in range(2))


@dataclass(frozen=True)
class PrivateOriginalTest:
    """Private evaluator contract for a single original or oneoff suite."""

    expected_pass: bool = True

    @property
    def ground_truth(self) -> tuple[bool]:
        return (self.expected_pass,)


@dataclass
class SpecArtifact:
    lean_code: str
    compile_success: bool
    attempts_used: int
    conversation: list[dict] = field(default_factory=list)
    compile_output: str = ""
    error: str = ""


@dataclass
class TestArtifact:
    model_code: str
    cases_code: str
    compile_success: bool
    attempts_used: int
    conversation: list[dict] = field(default_factory=list)
    compile_output: str = ""
    error: str = ""

    @property
    def lean_code(self) -> str:
        return assemble_lean(self.model_code, self.cases_code)


@dataclass
class ConnectorArtifact:
    lean_code: str
    compile_success: bool
    attempts_used: int
    mode: str  # direct | llm | unsupported | failure
    conversation: list[dict] = field(default_factory=list)
    compile_output: str = ""
    error: str = ""


def validate_safe_lean(source: str, *, label: str) -> None:
    """Compatibility wrapper preserving SWE-bench's ArtifactError API."""
    try:
        _validate_safe_lean(source, label=label)
    except LeanSafetyError as exc:
        raise ArtifactError(str(exc)) from exc


def strip_mathlib_import(source: str) -> str:
    return re.sub(r"^\s*import\s+Mathlib\s*\n", "", source, count=1).strip()


def assemble_lean(*parts: str) -> str:
    bodies = [strip_mathlib_import(p) for p in parts if p and p.strip()]
    return "import Mathlib\n\n" + "\n\n".join(bodies).rstrip() + "\n"


def parse_spec_response(response: str) -> str:
    if "SUBMIT_SPEC" not in response:
        raise ArtifactError("missing SUBMIT_SPEC marker")
    blocks = _LEAN_BLOCK_RE.findall(response)
    if len(blocks) != 1:
        raise ArtifactError(f"expected exactly one Lean block, found {len(blocks)}")
    body = blocks[0].strip()
    validate_safe_lean(body, label="spec")
    return body


def parse_tests_response(response: str) -> tuple[str, str]:
    if "SUBMIT_TESTS" not in response:
        raise ArtifactError("missing SUBMIT_TESTS marker")
    model_pos, cases_pos = response.find("TEST_MODEL"), response.find("TEST_CASES")
    if model_pos < 0 or cases_pos < 0 or model_pos >= cases_pos:
        raise ArtifactError("expected TEST_MODEL followed by TEST_CASES")
    model_blocks = _LEAN_BLOCK_RE.findall(response[model_pos:cases_pos])
    cases_blocks = _LEAN_BLOCK_RE.findall(response[cases_pos:])
    if len(model_blocks) != 1 or len(cases_blocks) != 1:
        raise ArtifactError("TEST_MODEL and TEST_CASES must each contain one Lean block")
    model, cases = model_blocks[0].strip(), cases_blocks[0].strip()
    validate_safe_lean(model, label="test model")
    validate_safe_lean(cases, label="test cases")
    model_code = _code_only(model)
    if re.search(r"^\s*def\b", model_code, re.MULTILINE):
        raise ArtifactError(
            "TEST_MODEL may contain only general type declarations; put all values "
            "and helper definitions in TEST_CASES"
        )
    if re.search(r"\btest_[01]\b", model_code):
        raise ArtifactError("TEST_MODEL may not refer to concrete test_0/test_1 values")
    for label, source in (("test model", model), ("test cases", cases)):
        if re.search(
            r"(?i)(?:\b(?:good|bad|original|corrupted|gold)\b|"
            r"\b\w+_(?:good|bad|original|corrupted|gold)\b|"
            r"\b(?:good|bad|original|corrupted|gold)_\w+\b)",
            _code_only(source),
        ):
            raise ArtifactError(f"{label}: use only neutral test_0/test_1 naming")
    return model, cases


def parse_connector_response(response: str) -> str:
    if "SUBMIT_CONNECTOR" not in response:
        raise ArtifactError("missing SUBMIT_CONNECTOR marker")
    blocks = _LEAN_BLOCK_RE.findall(response)
    if len(blocks) != 1:
        raise ArtifactError(f"expected exactly one Lean block, found {len(blocks)}")
    body = blocks[0].strip()
    validate_safe_lean(body, label="connector")
    return body


def validate_spec_contract(runner: LeanRunner, body: str, *, name: str) -> tuple[bool, str]:
    harness = """
#check Spec.Input
#check Spec.Output
#check Spec.run
#synth BEq Spec.Output
def ccSpecContract (input : Spec.Input) : Spec.Output := Spec.run input
"""
    return runner.compile(assemble_lean(body, harness), name=name)


def validate_tests_contract(runner: LeanRunner, model: str, cases: str,
                            *, name: str) -> tuple[bool, str]:
    harness = """
#check Tests.Case
def ccTest0Contract : List Tests.Case := Tests.test_0
def ccTest1Contract : List Tests.Case := Tests.test_1
#guard !Tests.test_0.isEmpty
#guard !Tests.test_1.isEmpty
"""
    return runner.compile(assemble_lean(model, cases, harness), name=name)


def validate_original_tests_contract(runner: LeanRunner, model: str, cases: str,
                                     *, name: str) -> tuple[bool, str]:
    """Validate the one-suite artifact used only by the original split."""
    harness = """
#check Tests.Case
def ccTestContract : List Tests.Case := Tests.test
#guard !Tests.test.isEmpty
"""
    return runner.compile(assemble_lean(model, cases, harness), name=name)


def validate_connector_contract(runner: LeanRunner, spec: str, test_model: str,
                                test_cases: str, connector: str,
                                *, name: str) -> tuple[bool, str]:
    harness = """
def ccConnectorContract (case : Tests.Case) : Option Bool := Connector.check case
"""
    return runner.compile(
        assemble_lean(spec, test_model, test_cases, connector, harness), name=name)
