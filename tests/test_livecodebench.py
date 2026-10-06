from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from conflict_certifier.evaluation import (
    CONFLICT, INCORRECT, evaluate_predictions,
)
from conflict_certifier.lean.repl import docker_available
from conflict_certifier.lean.runner import LeanRunner
from conflict_certifier.lean.safety import LeanSafetyError, validate_safe_lean
from conflict_certifier.tracks.livecodebench.artifacts import (
    ArtifactError,
    parse_prediction_code,
    parse_spec_response,
    render_certificate,
)
from conflict_certifier.tracks.livecodebench.certifier import LiveCodeBenchCertifier
from conflict_certifier.tracks.livecodebench.source import (
    AgentTask,
    Case,
    DataIntegrityError,
    LeanType,
    Parameter,
    PrivateOrder,
    Signature,
    load_tasks,
    prepare_task,
)
from conflict_certifier.tracks.livecodebench.spec_agent import LiveCodeBenchSpecAgent


DATASET = Path("data/livecodebench/dataset/full_oneoff.json")
CONFLICTING_DATASET = Path("data/livecodebench/sanitized/conflicting.json")


def _task() -> AgentTask:
    return AgentTask(
        task_id="fixture",
        entry_point="add_one",
        description=(
            "def add_one(x: int) -> int:\n"
            "    \"\"\"Return x plus one.\n\n"
            "    >>> add_one(1)\n"
            "    2\n"
            "    \"\"\""
        ),
        signature=Signature((Parameter("x", LeanType("Int")),), LeanType("Int")),
        public_cases=(Case((1,), 2),),
        test_0=(Case((10,), 11),),
        test_1=(Case((10,), 99),),
    )


SPEC_BODY = """namespace Spec

def run (input : Input) : Output := input.x + 1

end Spec"""


def test_config_uses_sanitized_conflicting_dataset():
    from conflict_certifier.tracks.livecodebench.config import LiveCodeBenchConfig

    config = LiveCodeBenchConfig.load("configs/livecodebench.yaml")
    assert config.split == "conflicting"
    assert config.dataset == CONFLICTING_DATASET.resolve()


def test_current_dataset_is_structurally_valid_and_order_is_reproducible():
    rows = load_tasks(DATASET)
    assert len(rows) == 97
    def prepared():
        return [prepare_task(row, order_seed=17)[1] for row in rows]

    first = prepared()
    second = prepared()
    assert first == second
    assert {order.good_test_number for order in first} == {0, 1}


def test_conflicting_dataset_preserves_good_suite_and_adds_one_contradiction():
    rows = load_tasks(CONFLICTING_DATASET)
    assert len(rows) == 95
    for row in rows:
        task, order = prepare_task(row, order_seed=23)
        tests = (task.test_0, task.test_1)
        good = tests[order.good_test_number]
        bad = tests[order.bad_test_number]
        assert len(good) == len(row["original_tests"])
        assert len(bad) == len(good) + 1
        assert all(case in bad for case in good)
        added = [case for case in bad if case not in good]
        assert len(added) == 1
        assert any(case.args == added[0].args and case.expected != added[0].expected
                   for case in good)

def test_dataset_adapter_rejects_a_corruption_not_grounded_in_original_tests():
    row = json.loads(json.dumps(load_tasks(DATASET)[0]))
    row["corrupted_test"]["original_expected"] = "invented"
    with pytest.raises(DataIntegrityError, match="does not match"):
        prepare_task(row, order_seed=0)


def test_spec_prompt_contains_no_hidden_inputs_or_expected_values():
    prompt = LiveCodeBenchSpecAgent.initial_prompt(_task())
    assert "add_one(1)" in prompt  # public example from the statement
    assert "10" not in prompt
    assert "99" not in prompt
    assert "test_0" not in prompt
    assert "test_1" not in prompt


def test_spec_protocol_and_shared_safety_policy():
    assert parse_spec_response(
        f"SUBMIT_SPEC\n```lean4\n{SPEC_BODY}\n```") == SPEC_BODY
    with pytest.raises(ArtifactError, match="forbidden"):
        parse_spec_response(
            "SUBMIT_SPEC\n```lean4\n#eval 1\ndef run := 1\n```")
    with pytest.raises(LeanSafetyError, match="unsafe"):
        validate_safe_lean("unsafe def run := 1", label="fixture")


def test_trusted_harness_routes_outputs_through_spec_run():
    source = render_certificate(_task(), SPEC_BODY)
    assert "def outputs_0 : List Spec.Output := inputs_0.map Spec.run" in source
    assert "def outputs_1 : List Spec.Output := inputs_1.map Spec.run" in source
    assert "99" in source
    assert source.count("#eval") == 1
    assert "test_0" not in LiveCodeBenchSpecAgent.initial_prompt(_task())


@pytest.mark.parametrize(
    ("output", "expected"),
    [("info: 0", (False, False)), ("info: 1", (False, True)),
     ("info: 2", (True, False)), ("info: 3", (True, True))],
)
def test_prediction_code_protocol(output, expected):
    assert parse_prediction_code(output) == expected


class _Runner:
    def __init__(self, output: str):
        self.output = output

    def compile(self, source: str, *, name: str):
        assert "inputs_0.map Spec.run" in source
        assert "inputs_1.map Spec.run" in source
        return True, self.output


def test_certifier_uses_the_shared_prediction_interface():
    correct = LiveCodeBenchCertifier(_Runner("info: 2")).certify(
        _task(), SPEC_BODY, label="fixture")
    assert correct.predictions == (True, False)
    assert evaluate_predictions(
        correct.evidence, PrivateOrder(0, 1).ground_truth).outcome == CONFLICT

    wrong = LiveCodeBenchCertifier(_Runner("info: 3")).certify(
        _task(), SPEC_BODY, label="fixture")
    assert evaluate_predictions(
        wrong.evidence, PrivateOrder(0, 1).ground_truth).outcome == INCORRECT


def _lean_available() -> bool:
    try:
        from conflict_certifier.tracks.livecodebench.config import LiveCodeBenchConfig
        LiveCodeBenchConfig.load("configs/livecodebench.yaml").lean_env.validate()
        return docker_available()
    except Exception:
        return False


@pytest.mark.lean
@pytest.mark.skipif(not _lean_available(), reason="no sandboxed Lean REPL available")
def test_fixed_harness_compiles_and_evaluates_in_lean():
    from conflict_certifier.tracks.livecodebench.config import LiveCodeBenchConfig
    runner = LeanRunner(LiveCodeBenchConfig.load(
        "configs/livecodebench.yaml").lean_env)
    try:
        conflicting = replace(
            _task(),
            test_0=(Case((10,), 11),),
            test_1=(Case((10,), 11), Case((10,), 99)),
        )
        result = LiveCodeBenchCertifier(runner).certify(
            conflicting, SPEC_BODY, label="lcb_fixture")
    finally:
        runner.close()
    assert result.predictions == (True, False), result.compile_output
    assert evaluate_predictions(
        result.evidence, PrivateOrder(0, 1).ground_truth).outcome == CONFLICT
