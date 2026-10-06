"""Strict Lean artifact protocol and trusted LiveCodeBench renderers."""

from __future__ import annotations

import re

from conflict_certifier.lean.safety import validate_safe_lean
from conflict_certifier.tracks.livecodebench.source import AgentTask, Case


_SPEC_RESPONSE_RE = re.compile(
    r"\s*SUBMIT_SPEC\s*\n```(?:lean4?|lean)\s*\n(.*?)```\s*", re.DOTALL)


class ArtifactError(ValueError):
    pass


def parse_spec_response(response: str) -> str:
    match = _SPEC_RESPONSE_RE.fullmatch(response)
    if match is None:
        raise ArtifactError("expected only SUBMIT_SPEC followed by one Lean block")
    body = match.group(1).strip()
    try:
        validate_safe_lean(body, label="LiveCodeBench spec")
    except ValueError as exc:
        raise ArtifactError(str(exc)) from exc
    if not re.search(r"\bdef\s+run\b", body):
        raise ArtifactError("spec must define run")
    return body


def assemble_spec(task: AgentTask, body: str) -> str:
    return f"import Mathlib\n\n{task.signature.declarations}\n\n{body.strip()}\n"


def render_contract(task: AgentTask, body: str) -> str:
    return assemble_spec(task, body) + """
#check Spec.Input
#check Spec.Output
#check Spec.run
#synth BEq Spec.Output
def ccSpecContract (input : Spec.Input) : Spec.Output := Spec.run input
"""


def _render_case_input(task: AgentTask, case: Case) -> str:
    return task.signature.render_input(list(case.args))


def render_public_validation(task: AgentTask, body: str) -> str:
    lines = [render_contract(task, body)]
    for number, case in enumerate(task.public_cases):
        value = task.signature.output.render(case.expected)
        lines.append(
            f"#guard Spec.run ({_render_case_input(task, case)}) == ({value}) "
            f"-- public example {number}"
        )
    return "\n".join(lines).rstrip() + "\n"


def render_certificate(task: AgentTask, body: str) -> str:
    inputs_0 = ",\n    ".join(
        _render_case_input(task, case) for case in task.test_0
    )
    inputs_1 = ",\n    ".join(
        _render_case_input(task, case) for case in task.test_1
    )
    expected_0 = ",\n    ".join(
        task.signature.output.render(case.expected) for case in task.test_0
    )
    expected_1 = ",\n    ".join(
        task.signature.output.render(case.expected) for case in task.test_1
    )
    return assemble_spec(task, body) + f"""
namespace Tests

def inputs_0 : List Spec.Input := [
    {inputs_0}
]

def inputs_1 : List Spec.Input := [
    {inputs_1}
]

def expected_0 : List Spec.Output := [
    {expected_0}
]

def expected_1 : List Spec.Output := [
    {expected_1}
]

def outputs_0 : List Spec.Output := inputs_0.map Spec.run

def outputs_1 : List Spec.Output := inputs_1.map Spec.run

def testPasses (outputs expected : List Spec.Output) : Bool :=
  outputs.length == expected.length &&
    (List.zipWith (fun actual wanted => actual == wanted) outputs expected).all id

def predictionCode : Nat :=
  (if testPasses outputs_0 expected_0 then 2 else 0) +
  (if testPasses outputs_1 expected_1 then 1 else 0)

end Tests

#eval Tests.predictionCode
"""


def parse_prediction_code(output: str) -> tuple[bool, bool]:
    values = [int(match.group(1)) for match in re.finditer(
        r"(?m)^\s*(?:info:\s*)?([0-3])\s*$", output)]
    if not values:
        raise ArtifactError("Lean evaluation produced no prediction code")
    code = values[-1]
    return bool(code & 2), bool(code & 1)
