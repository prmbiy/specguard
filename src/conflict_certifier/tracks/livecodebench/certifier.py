"""Deterministic Lean backend for the generic prediction evaluator."""

from __future__ import annotations

from dataclasses import dataclass

from conflict_certifier.evaluation import (
    EvaluationEvidence,
)
from conflict_certifier.tracks.livecodebench.artifacts import (
    ArtifactError,
    parse_prediction_code,
    render_certificate,
)
from conflict_certifier.tracks.livecodebench.source import AgentTask


@dataclass(frozen=True)
class Certification:
    source: str
    predictions: tuple[bool, bool] | None
    evidence: EvaluationEvidence
    compile_output: str


class LiveCodeBenchCertifier:
    def __init__(self, runner):
        self.runner = runner

    def certify(self, task: AgentTask, spec_body: str,
                *, label: str) -> Certification:
        source = render_certificate(task, spec_body)
        try:
            ok, output = self.runner.compile(source, name=label)
        except Exception as exc:
            evidence = EvaluationEvidence.error(
                "infrastructure_error", f"{type(exc).__name__}: {exc}")
            return Certification(source, None, evidence, "")
        if not ok:
            timed_out = "TIMEOUT" in output
            evidence = (
                EvaluationEvidence.error(
                    "evaluation_timeout", "Lean evaluation timed out")
                if timed_out else EvaluationEvidence.inconclusive(
                    "not_evaluable",
                    "the submitted specification could not evaluate the hidden inputs")
            )
            return Certification(source, None, evidence, output)
        try:
            predictions = parse_prediction_code(output)
        except ArtifactError as exc:
            evidence = EvaluationEvidence.inconclusive(
                "prediction_parse_error", str(exc))
            return Certification(source, None, evidence, output)
        evidence = EvaluationEvidence.complete(predictions)
        return Certification(source, predictions, evidence, output)
