"""Track-independent prediction-vector evaluation.

Tracks produce only a Boolean prediction vector or a backend failure.  This
module owns every user-facing result category and is the only place that
compares predictions with private ground truth.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Sequence


SUCCESS = "SUCCESS"
FAIL = "FAIL"
SKIPPED = "SKIPPED"

CONFLICT = "CONFLICT"
NO_CONFLICT = "NO_CONFLICT"
INCORRECT = "INCORRECT"
INCONCLUSIVE = "INCONCLUSIVE"

STATUSES = (SUCCESS, FAIL, SKIPPED)
OUTCOMES = (CONFLICT, NO_CONFLICT, INCORRECT, INCONCLUSIVE)


class EvidenceKind(str, Enum):
    """What a track-specific mechanical backend produced."""

    PREDICTIONS = "predictions"
    INCONCLUSIVE = "inconclusive"
    ERROR = "error"


@dataclass(frozen=True)
class EvaluationEvidence:
    """Label-blind output of a track-specific mechanical checker."""

    kind: EvidenceKind
    predictions: tuple[bool, ...] | None = None
    note: str = ""
    reason: str = ""

    @classmethod
    def complete(cls, predictions: Sequence[bool]) -> "EvaluationEvidence":
        values = tuple(predictions)
        if not values or any(type(value) is not bool for value in values):
            raise ValueError("predictions must be a nonempty Boolean sequence")
        return cls(EvidenceKind.PREDICTIONS, values)

    @classmethod
    def inconclusive(cls, note: str, reason: str = "") -> "EvaluationEvidence":
        return cls(EvidenceKind.INCONCLUSIVE, None, note, reason)

    @classmethod
    def error(cls, note: str, reason: str = "") -> "EvaluationEvidence":
        return cls(EvidenceKind.ERROR, None, note, reason)


@dataclass(frozen=True)
class EvaluationDecision:
    """The complete shared result for one benchmark task."""

    status: str
    outcome: str | None
    predictions: tuple[bool, ...] | None
    ground_truth: tuple[bool, ...] | None
    note: str = ""
    reason: str = ""

    @property
    def exact_match(self) -> bool | None:
        if self.predictions is None or self.ground_truth is None:
            return None
        return self.predictions == self.ground_truth


def evaluate_predictions(
    evidence: EvaluationEvidence,
    ground_truth: Sequence[bool] | None,
) -> EvaluationDecision:
    """Evaluate one backend prediction without track-specific verdict logic."""

    truth = None if ground_truth is None else tuple(ground_truth)
    if truth is not None:
        if not truth or any(type(value) is not bool for value in truth):
            raise ValueError("ground truth must be a nonempty Boolean sequence")

    if evidence.kind is EvidenceKind.ERROR:
        return EvaluationDecision(
            SKIPPED, None, None, truth, evidence.note, evidence.reason)
    if evidence.kind is EvidenceKind.INCONCLUSIVE:
        return EvaluationDecision(
            FAIL, INCONCLUSIVE, None, truth, evidence.note, evidence.reason)

    predictions = evidence.predictions
    if predictions is None:
        raise ValueError("prediction evidence is missing predictions")
    if truth is None:
        raise ValueError("complete predictions require ground truth")
    if len(predictions) != len(truth) or predictions != truth:
        reason = evidence.reason
        if not reason:
            reason = (
                f"prediction {list(predictions)} did not match "
                f"ground truth {list(truth)}"
            )
        return EvaluationDecision(
            FAIL, INCORRECT, predictions, truth,
            evidence.note or "incorrect_prediction", reason)

    outcome = NO_CONFLICT if all(truth) else CONFLICT
    return EvaluationDecision(
        SUCCESS, outcome, predictions, truth, evidence.note, evidence.reason)


def result_record(identifier_key: str, identifier: str,
                  decision: EvaluationDecision, **extra) -> dict:
    """Serialize a decision using the shared task-result schema."""

    result = {
        identifier_key: identifier,
        "status": decision.status,
        "outcome": decision.outcome,
        "predictions": (list(decision.predictions)
                        if decision.predictions is not None else None),
        "ground_truth": (list(decision.ground_truth)
                         if decision.ground_truth is not None else None),
        "exact_match": decision.exact_match,
        "note": decision.note,
        "reason": decision.reason,
    }
    result.update(extra)
    return result


def incomplete_result(identifier_key: str, identifier: str, *, note: str,
                      reason: str = "", skipped: bool = False, **extra) -> dict:
    """Create a shared result when no prediction vector was produced."""

    evidence = (EvaluationEvidence.error(note, reason) if skipped
                else EvaluationEvidence.inconclusive(note, reason))
    return result_record(
        identifier_key, identifier, evaluate_predictions(evidence, None), **extra)
