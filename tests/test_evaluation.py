from conflict_certifier.evaluation import (
    CONFLICT,
    FAIL,
    INCORRECT,
    INCONCLUSIVE,
    NO_CONFLICT,
    SKIPPED,
    SUCCESS,
    EvaluationEvidence,
    evaluate_predictions,
    result_record,
)
from conflict_certifier.reporting import summarize


def test_exact_conflicting_vector_is_success_conflict():
    decision = evaluate_predictions(
        EvaluationEvidence.complete([True, True, False]),
        [True, True, False],
    )
    assert (decision.status, decision.outcome) == (SUCCESS, CONFLICT)
    assert decision.exact_match is True


def test_exact_clean_vector_is_success_no_conflict():
    decision = evaluate_predictions(
        EvaluationEvidence.complete([True, True]), [True, True])
    assert (decision.status, decision.outcome) == (SUCCESS, NO_CONFLICT)


def test_exact_all_bad_vector_is_success_conflict():
    decision = evaluate_predictions(
        EvaluationEvidence.complete([False, False]), [False, False])
    assert (decision.status, decision.outcome) == (SUCCESS, CONFLICT)


def test_complete_wrong_vector_is_fail_incorrect():
    decision = evaluate_predictions(
        EvaluationEvidence.complete([True, False, True]),
        [True, True, False],
    )
    assert (decision.status, decision.outcome) == (FAIL, INCORRECT)
    assert decision.exact_match is False


def test_uncertified_backend_is_fail_inconclusive():
    decision = evaluate_predictions(
        EvaluationEvidence.inconclusive("not_certified"), [True, False])
    assert (decision.status, decision.outcome) == (FAIL, INCONCLUSIVE)
    assert decision.predictions is None


def test_backend_error_is_skipped():
    decision = evaluate_predictions(
        EvaluationEvidence.error("error", "backend timeout"), [True, False])
    assert decision.status == SKIPPED
    assert decision.outcome is None
    assert decision.reason == "backend timeout"


def test_shared_summary_uses_only_prediction_result_schema():
    decisions = [
        evaluate_predictions(EvaluationEvidence.complete([True, False]), [True, False]),
        evaluate_predictions(EvaluationEvidence.complete([True, True]), [True, True]),
        evaluate_predictions(EvaluationEvidence.complete([True, False]), [True, True]),
        evaluate_predictions(
            EvaluationEvidence.inconclusive("unsupported"), [True, False]),
        evaluate_predictions(EvaluationEvidence.error("timeout"), [True, False]),
    ]
    results = [result_record("task_id", str(i), decision)
               for i, decision in enumerate(decisions)]
    summary = summarize(results)
    assert summary["status_counts"] == {SUCCESS: 2, FAIL: 2, SKIPPED: 1}
    assert summary["outcome_counts"] == {
        CONFLICT: 1, NO_CONFLICT: 1, INCORRECT: 1, INCONCLUSIVE: 1}
    assert summary["metrics"]["false_positives"] == 1
