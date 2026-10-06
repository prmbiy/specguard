"""Proof-first mechanical certifier for SWE-bench Lean artifacts."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence

from conflict_certifier.evaluation import EvaluationEvidence
from conflict_certifier.lean.runner import LeanRunner
from conflict_certifier.tracks.swebench.artifacts import assemble_lean


PROOF = "proof"
EXECUTION = "execution"
_ALLOWED_AXIOMS = {"propext", "Classical.choice", "Quot.sound"}


@dataclass(frozen=True)
class LeanCertification:
    predictions: tuple[bool, ...] | None
    evidence: EvaluationEvidence
    lean_code: str
    compile_output: str
    execution_code: str = ""
    execution_output: str = ""
    evidence_tier: str = ""


def _truth(values: Sequence[bool], *, expected_length: int) -> tuple[bool, ...]:
    truth = tuple(values)
    if len(truth) != expected_length or any(type(value) is not bool for value in truth):
        raise ValueError(
            f"ground truth must contain exactly {expected_length} Boolean values")
    return truth


def _bool(value: bool) -> str:
    return "true" if value else "false"


def _common_harness() -> str:
    return """\
def ccCaseSupported (case : Tests.Case) : Bool :=
  match Connector.check case with
  | some _ => true
  | none => false

def ccCasePasses (case : Tests.Case) : Bool :=
  match Connector.check case with
  | some result => result
  | none => false

def ccTestSupported (cases : List Tests.Case) : Bool :=
  cases.all ccCaseSupported

def ccTestPasses (cases : List Tests.Case) : Bool :=
  cases.all ccCasePasses
"""


def render_certificate(
    spec: str,
    test_model: str,
    test_cases: str,
    connector: str,
    ground_truth: Sequence[bool],
) -> str:
    """Render the kernel-checked two-suite certificate attempted first."""

    truth = _truth(ground_truth, expected_length=2)
    harness = _common_harness() + f"""\

set_option maxRecDepth 100000 in
theorem evaluation :
    ccTestSupported Tests.test_0 = true ∧
    ccTestSupported Tests.test_1 = true ∧
    ccTestPasses Tests.test_0 = {_bool(truth[0])} ∧
    ccTestPasses Tests.test_1 = {_bool(truth[1])} := by decide

#print axioms evaluation
"""
    return assemble_lean(spec, test_model, test_cases, connector, harness)


def render_execution(
    spec: str,
    test_model: str,
    test_cases: str,
    connector: str,
) -> str:
    """Render the label-blind execution fallback for two suites."""

    harness = _common_harness() + """\

def ccEvaluationCode : Nat :=
  (if ccTestSupported Tests.test_0 then 8 else 0) +
  (if ccTestSupported Tests.test_1 then 4 else 0) +
  (if ccTestPasses Tests.test_0 then 2 else 0) +
  (if ccTestPasses Tests.test_1 then 1 else 0)

set_option maxRecDepth 100000 in
#eval ccEvaluationCode
"""
    return assemble_lean(spec, test_model, test_cases, connector, harness)


def _original_harness() -> str:
    return """\
def ccCaseSupported (case : Tests.Case) : Bool :=
  match Connector.check case with
  | some _ => true
  | none => false

def ccCasePasses (case : Tests.Case) : Bool :=
  match Connector.check case with
  | some result => result
  | none => false

def ccTestSupported : Bool := Tests.test.all ccCaseSupported
def ccTestPasses : Bool := Tests.test.all ccCasePasses
"""


def render_original_certificate(
    spec: str,
    test_model: str,
    test_cases: str,
    connector: str,
    ground_truth: Sequence[bool] = (True,),
) -> str:
    """Render the kernel-checked one-suite certificate attempted first."""

    truth = _truth(ground_truth, expected_length=1)
    harness = _original_harness() + f"""\

set_option maxRecDepth 100000 in
theorem evaluation :
    ccTestSupported = true ∧
    ccTestPasses = {_bool(truth[0])} := by decide

#print axioms evaluation
"""
    return assemble_lean(spec, test_model, test_cases, connector, harness)


def render_original_execution(
    spec: str,
    test_model: str,
    test_cases: str,
    connector: str,
) -> str:
    """Render the label-blind execution fallback for one suite."""

    harness = _original_harness() + """\

def ccEvaluationCode : Nat :=
  (if ccTestSupported then 2 else 0) +
  (if ccTestPasses then 1 else 0)

set_option maxRecDepth 100000 in
#eval ccEvaluationCode
"""
    return assemble_lean(spec, test_model, test_cases, connector, harness)


def _unexpected_axioms(output: str) -> set[str]:
    if "sorryAx" in output:
        return {"sorryAx"}
    found: set[str] = set()
    for raw in re.findall(r"depends on axioms:\s*\[([^]]*)\]", output):
        found.update(value.strip() for value in raw.split(",") if value.strip())
    return found - _ALLOWED_AXIOMS


def _parse_code(output: str) -> int | None:
    values = [int(match.group(1)) for match in re.finditer(
        r"(?m)^\s*(?:info:\s*)?([0-9]|1[0-5])\s*$", output)]
    return values[-1] if values else None


class LeanCertifier:
    def __init__(self, runner: LeanRunner):
        self._runner = runner

    def _compile(self, source: str, *, name: str) -> tuple[bool, str, Exception | None]:
        try:
            ok, output = self._runner.compile(source, name=name)
            return ok, output, None
        except Exception as exc:  # infrastructure failures are represented, not raised
            return False, "", exc

    def certify(
        self,
        spec: str,
        test_model: str,
        test_cases: str,
        connector: str,
        ground_truth: Sequence[bool],
        *,
        label: str,
    ) -> LeanCertification:
        truth = _truth(ground_truth, expected_length=2)
        proof = render_certificate(
            spec, test_model, test_cases, connector, truth)
        proof_ok, proof_output, proof_error = self._compile(
            proof, name=f"{label}_proof")
        if proof_ok and not _unexpected_axioms(proof_output):
            return LeanCertification(
                truth, EvaluationEvidence.complete(truth), proof, proof_output,
                evidence_tier=PROOF)

        execution = render_execution(spec, test_model, test_cases, connector)
        exec_ok, exec_output, exec_error = self._compile(
            execution, name=f"{label}_execution")
        if exec_error is not None:
            evidence = EvaluationEvidence.error(
                "infrastructure_error", f"{type(exec_error).__name__}: {exec_error}")
            return LeanCertification(
                None, evidence, proof, proof_output, execution, exec_output)
        if not exec_ok:
            evidence = (
                EvaluationEvidence.error("evaluation_timeout", "lean timeout")
                if "TIMEOUT" in exec_output
                else EvaluationEvidence.inconclusive(
                    "not_evaluable",
                    "agent artifacts could not compute the numbered test predictions",
                )
            )
            return LeanCertification(
                None, evidence, proof, proof_output, execution, exec_output)

        code = _parse_code(exec_output)
        if code is None:
            evidence = EvaluationEvidence.inconclusive(
                "prediction_parse_error", "Lean emitted no evaluation code")
            return LeanCertification(
                None, evidence, proof, proof_output, execution, exec_output)
        if not (code & 8) or not (code & 4):
            evidence = EvaluationEvidence.inconclusive(
                "unsupported_connection",
                "Connector.check returned none for at least one evaluated case",
            )
            return LeanCertification(
                None, evidence, proof, proof_output, execution, exec_output)

        predictions = (bool(code & 2), bool(code & 1))
        return LeanCertification(
            predictions, EvaluationEvidence.complete(predictions),
            proof, proof_output, execution, exec_output, EXECUTION)

    def certify_original(
        self,
        spec: str,
        test_model: str,
        test_cases: str,
        connector: str,
        ground_truth: Sequence[bool] = (True,),
        *,
        label: str,
    ) -> LeanCertification:
        truth = _truth(ground_truth, expected_length=1)
        proof = render_original_certificate(
            spec, test_model, test_cases, connector, truth)
        proof_ok, proof_output, proof_error = self._compile(
            proof, name=f"{label}_proof")
        if proof_ok and not _unexpected_axioms(proof_output):
            return LeanCertification(
                truth, EvaluationEvidence.complete(truth), proof, proof_output,
                evidence_tier=PROOF)

        execution = render_original_execution(
            spec, test_model, test_cases, connector)
        exec_ok, exec_output, exec_error = self._compile(
            execution, name=f"{label}_execution")
        if exec_error is not None:
            evidence = EvaluationEvidence.error(
                "infrastructure_error", f"{type(exec_error).__name__}: {exec_error}")
            return LeanCertification(
                None, evidence, proof, proof_output, execution, exec_output)
        if not exec_ok:
            evidence = (
                EvaluationEvidence.error("evaluation_timeout", "lean timeout")
                if "TIMEOUT" in exec_output
                else EvaluationEvidence.inconclusive(
                    "not_evaluable",
                    "agent artifacts could not compute the original test prediction",
                )
            )
            return LeanCertification(
                None, evidence, proof, proof_output, execution, exec_output)

        code = _parse_code(exec_output)
        if code is None or code > 3:
            evidence = EvaluationEvidence.inconclusive(
                "prediction_parse_error", "Lean emitted no original-suite result")
            return LeanCertification(
                None, evidence, proof, proof_output, execution, exec_output)
        if not (code & 2):
            evidence = EvaluationEvidence.inconclusive(
                "unsupported_connection",
                "Connector.check returned none for an evaluated case",
            )
            return LeanCertification(
                None, evidence, proof, proof_output, execution, exec_output)

        predictions = (bool(code & 1),)
        return LeanCertification(
            predictions, EvaluationEvidence.complete(predictions),
            proof, proof_output, execution, exec_output, EXECUTION)
