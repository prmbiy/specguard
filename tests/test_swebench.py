from __future__ import annotations

import json
import hashlib

import pytest
import conflict_certifier.tracks.swebench.spec_agent as spec_agent_module

from conflict_certifier.llm.call_log import LLMCallLogger
from conflict_certifier.tracks.swebench.artifacts import (
    ArtifactError,
    ConnectorArtifact,
    PrivateOriginalTest,
    PrivateTestOrder,
    SpecArtifact,
    TestArtifact as LeanTestsArtifact,
    parse_connector_response,
    parse_spec_response,
    parse_tests_response,
    validate_original_tests_contract,
    validate_safe_lean,
)
from conflict_certifier.tracks.swebench.spec_agent import SweCodebaseSpecAgent
from conflict_certifier.tracks.swebench.connector_agent import (
    DIRECT_CONNECTOR,
    SweConnectorAgent,
)
from conflict_certifier.evaluation import (
    CONFLICT,
    NO_CONFLICT,
    SUCCESS,
    EvaluationEvidence,
    evaluate_predictions,
)
from conflict_certifier.tracks.swebench.certifier import (
    EXECUTION,
    PROOF,
    LeanCertification,
    LeanCertifier,
    render_certificate,
    render_execution,
    render_original_certificate,
)
from conflict_certifier.tracks.swebench.run import process_one, select_instance_ids
from conflict_certifier.tracks.swebench.source import (
    InputUnavailableError,
    load_test_inputs,
    prepare_agent_case,
)
from conflict_certifier.tracks.swebench.test_agent import SweTestAgent
from conflict_certifier.tracks.swebench.prompting import load_prompt, prompt_audit


SPEC_BODY = """\
namespace Spec
abbrev Input := Nat
abbrev Output := Nat
def run (input : Input) : Output := input + 1
end Spec
"""

TEST_MODEL = """\
namespace Tests
structure Case where
  input : Nat
  expected : Nat
end Tests
"""

TEST_CASES = """\
namespace Tests
def test_0 : List Case := [{ input := 1, expected := 2 }]
def test_1 : List Case := [{ input := 1, expected := 3 }]
end Tests
"""

ORIGINAL_TEST_CASES = """\
namespace Tests
def test : List Case := [{ input := 1, expected := 2 }]
end Tests
"""


class FakeRunner:
    def __init__(self, direct_ok: bool = True):
        self.direct_ok = direct_ok
        self.sources: list[str] = []

    def compile(self, source: str, *, name: str = "x", reject_sorry: bool = True):
        self.sources.append(source)
        if name.endswith("_direct"):
            return self.direct_ok, "" if self.direct_ok else "type mismatch"
        return True, ""


class NeverLLM:
    def complete_conversation(self, system, conversation):  # pragma: no cover
        raise AssertionError("LLM should not be called")


class FixedLLM:
    def __init__(self, response: str):
        self.response = response
        self.system = ""
        self.messages = []
        self.calls = 0

    def complete_conversation(self, system, conversation):
        self.calls += 1
        self.system = system
        self.messages = list(conversation)
        return self.response


def _row() -> dict:
    return {
        "instance_id": "repo__repo-1",
        "problem_statement": "issue-only marker",
        "repo": "repo/repo",
        "original_test_patch": "ORIGINAL_VALUE",
        "test_patch": "MODIFIED_VALUE",
        "patch": "GOLD_MUST_NOT_APPEAR",
    }


def _input_record() -> dict:
    return {"status": "OK", "inputs": ["f(1)"], "context": "x = 1"}


def test_dataset_packet_is_reproducibly_shuffled_without_gold_patch():
    case_a, order_a = prepare_agent_case(_row(), _input_record(), order_seed=7)
    case_b, order_b = prepare_agent_case(_row(), _input_record(), order_seed=7)
    assert case_a == case_b and order_a == order_b
    assert {case_a.test_0_patch, case_a.test_1_patch} == {"ORIGINAL_VALUE", "MODIFIED_VALUE"}
    assert "GOLD_MUST_NOT_APPEAR" not in repr(case_a)
    assert {order_a.good_test_number, order_a.bad_test_number} == {0, 1}


def test_dataset_packet_requires_ok_input_record():
    with pytest.raises(InputUnavailableError):
        prepare_agent_case(_row(), {"status": "NOT_POSSIBLE"})


def test_original_dataset_packet_has_one_real_suite_without_duplication():
    case, contract = prepare_agent_case(
        _row(), _input_record(), order_seed=7, split="original")
    assert isinstance(contract, PrivateOriginalTest)
    assert contract.ground_truth == (True,)
    assert case.test_0_patch == "ORIGINAL_VALUE"
    assert case.test_1_patch == ""


def test_every_conflicting_task_has_a_frozen_input_record():
    records = load_test_inputs()
    assert len(records) == 349
    assert all(record["status"] == "OK" for record in records.values())
    recovered = records["astropy__astropy-7336"]
    assert len(recovered["inputs"]) == 2
    assert recovered["manual_review"] is True
    assert "assert" not in recovered["context"]
    assert "== 7" not in recovered["context"]


def test_spec_protocol_and_safety():
    assert parse_spec_response(f"SUBMIT_SPEC\n```lean4\n{SPEC_BODY}\n```") == SPEC_BODY.strip()
    with pytest.raises(ArtifactError):
        parse_spec_response("```lean4\ndef x := 1\n```")
    for unsafe in ("axiom h : False", "#eval 1", "unsafe def x := 1",
                   "theorem x : True := by trivial", "native_decide"):
        with pytest.raises(ArtifactError):
            validate_safe_lean(unsafe, label="fixture")
    # Forbidden words inside data do not trigger the token screen.
    validate_safe_lean('def message : String := "unsafe IO sorry"', label="fixture")


def test_test_protocol_has_two_strict_blocks():
    response = (
        "SUBMIT_TESTS\nTEST_MODEL\n```lean4\n" + TEST_MODEL + "\n```\n"
        "TEST_CASES\n```lean4\n" + TEST_CASES + "\n```"
    )
    assert parse_tests_response(response) == (TEST_MODEL.strip(), TEST_CASES.strip())
    with pytest.raises(ArtifactError):
        parse_tests_response(response.replace("TEST_CASES", "CASES"))
    with pytest.raises(ArtifactError):
        parse_tests_response(response.replace(
            "structure Case where", "def leakedValue : Nat := 2\nstructure Case where"))
    with pytest.raises(ArtifactError):
        parse_tests_response(response.replace("test_0", "good_test"))


def test_connector_may_use_concrete_neutral_tests():
    good = "SUBMIT_CONNECTOR\n```lean4\n" + DIRECT_CONNECTOR + "\n```"
    assert "def check" in parse_connector_response(good)
    concrete = good.replace("case.expected", "Tests.test_0.head!.expected")
    assert "Tests.test_0" in parse_connector_response(concrete)


def test_direct_connector_skips_llm():
    runner = FakeRunner(direct_ok=True)
    result = SweConnectorAgent(NeverLLM(), runner).run(
        SPEC_BODY, TEST_MODEL, TEST_CASES, ["f(1)"], "x = 1", label="fixture")
    assert result.compile_success and result.mode == "direct"
    assert len(runner.sources) == 1


def test_llm_connector_sees_concrete_demands_and_input_context():
    response = """SUBMIT_CONNECTOR
```lean4
namespace Connector
def check (case : Tests.Case) : Option Bool :=
  some (Spec.run case.input == case.expected)
end Connector
```"""
    llm = FixedLLM(response)
    runner = FakeRunner(direct_ok=False)
    result = SweConnectorAgent(llm, runner).run(
        SPEC_BODY, TEST_MODEL, TEST_CASES, ["f(1)"], "INPUT_CONTEXT",
        label="fixture")
    assert result.compile_success and result.mode == "llm"
    prompt = llm.messages[0]["content"]
    assert "namespace Tests" in prompt and "namespace Spec" in prompt
    assert "Tests.Case" in llm.system and "Spec.run" in llm.system
    assert "expected := 2" in prompt and "expected := 3" in prompt
    assert "test_0" in prompt and "test_1" in prompt
    assert "f(1)" in prompt and "INPUT_CONTEXT" in prompt
    assert "no corresponding source assertion exists" in llm.system
    assert "vacuously satisfied" in llm.system
    assert "real assertion expecting a missing value" in llm.system


def test_prompts_obey_information_boundaries():
    spec_prompt = SweCodebaseSpecAgent._initial_prompt(
        "ISSUE_SECRET", "repo", 10, ["f(1)"], "INPUT_CONTEXT")
    assert "ISSUE_SECRET" in spec_prompt and "INPUT_CONTEXT" in spec_prompt
    assert "ORIGINAL_VALUE" not in spec_prompt and "MODIFIED_VALUE" not in spec_prompt

    test_prompt = SweTestAgent._prompt(
        ["f(1)"], "INPUT_CONTEXT", "ORIGINAL_VALUE", "MODIFIED_VALUE")
    assert "INPUT_CONTEXT" in test_prompt
    assert "ORIGINAL_VALUE" in test_prompt and "MODIFIED_VALUE" in test_prompt
    assert "ISSUE_SECRET" not in test_prompt
    assert "good" not in test_prompt.lower() and "bad" not in test_prompt.lower()


def test_test_agent_is_difference_focused_and_uses_neutral_numbered_labels():
    system = SweTestAgent.system_prompt
    assert "whose demanded outcome differs" in system
    assert "assertion_1_test_0" in system and "assertion_1_test_1" in system
    assert "TEST_MODEL contains types only" in system
    assert "exact, verbatim assertion" in system
    assert "Never invent an assertion" in system
    assert "Never duplicate one source assertion" in system
    assert "If two assertions do not correspond, do not pair them" in system
    assert "Case.htmlWrite" not in system and "worldToPixel" not in system
    assert "strongly prefer exact `Rat`" in system
    assert "`Float` is still allowed" in system
    assert "complete tested scenario" in system
    assert "setup or state changes" in system
    assert "when its result is observed" in system
    assert "Never report only an expected value" in system


def test_original_test_agent_has_separate_neutral_translation_prompt():
    agent = SweTestAgent(FixedLLM("unused"), FakeRunner(), original=True)
    assert agent.system_prompt == load_prompt("original_test_system.txt")
    assert agent.system_prompt != SweTestAgent.system_prompt
    assert "Tests.test_0" not in agent.system_prompt
    assert "Tests.test_1" not in agent.system_prompt
    assert "def test : List Case" in agent.system_prompt
    assert "role-bearing names" in agent.system_prompt
    assert "evaluation" not in agent.system_prompt.lower()
    assert "complete tested scenario" in agent.system_prompt
    assert "setup or state changes" in agent.system_prompt
    prompt = agent._original_prompt(["f(1)"], "context", "PATCH_ASSERTION")
    assert "PATCH_ASSERTION" in prompt
    assert "test_0" not in prompt and "test_1" not in prompt


def test_prompt_files_are_reviewable_and_shortcut_language_cannot_return():
    assert SweCodebaseSpecAgent.system_prompt == load_prompt("spec_system.txt")
    assert hashlib.sha256(SweCodebaseSpecAgent.system_prompt.encode()).hexdigest() == (
        "05dc70246e49a620c7fa4a8355ca034a37882f216ab41b799b14b673a5a3a478"
    )
    connector_text = "\n".join((
        load_prompt("connector_system.txt"),
        load_prompt("connector_retry.txt"),
        SweConnectorAgent._prompt(SPEC_BODY, TEST_MODEL, TEST_CASES, ["f(1)"], "context"),
    )).lower()
    for forbidden in (
        "black-box conflict-detection",
        "in any computable way",
        "correctly distinguishing",
        "direct comparisons or value-specific logic",
        "decide the demands",
        "decide each assertion",
    ):
        assert forbidden not in connector_text
    assert "applying spec.run" in connector_text
    assert "do not hardcode" in connector_text
    assert "follow that scenario" in connector_text
    assert "independently of the expected value" in connector_text
    assert "do not infer or alter the tested input" in connector_text


def test_original_connector_preserves_the_transcribed_scenario():
    text = "\n".join((
        load_prompt("original_connector_system.txt"),
        load_prompt("original_connector_retry.txt"),
    )).lower()
    assert "follow the tested scenario" in text
    assert "independently of the expected value" in text
    assert "never substitute a representative" in text


def test_prompt_audit_records_exact_system_and_template_hashes():
    conversation = [{"role": "user", "content": "rendered initial prompt"}]
    audit = prompt_audit("test", SweTestAgent.system_prompt, conversation)
    assert audit["system_prompt"] == SweTestAgent.system_prompt
    assert audit["system_prompt_sha256"] == hashlib.sha256(
        SweTestAgent.system_prompt.encode()).hexdigest()
    assert audit["initial_prompt_sha256"] == hashlib.sha256(
        conversation[0]["content"].encode()).hexdigest()
    assert set(audit["template_sha256"]) == {"test_retry.txt", "test_system.txt"}


def test_certifier_renders_the_strict_proof_before_execution():
    source = render_certificate(
        SPEC_BODY, TEST_MODEL, TEST_CASES, DIRECT_CONNECTOR, (True, False),
    )
    assert source.count("set_option maxRecDepth 100000 in") == 1
    assert "norm_num" not in source and "native_decide" not in source
    assert "ccTestPasses Tests.test_0 = true" in source
    assert "ccTestPasses Tests.test_1 = false" in source
    assert "theorem evaluation" in source and "by decide" in source
    assert "#print axioms evaluation" in source
    assert "#eval" not in source
    assert "good_test" not in source and "bad_test" not in source

    fallback = render_execution(
        SPEC_BODY, TEST_MODEL, TEST_CASES, DIRECT_CONNECTOR)
    assert "#eval ccEvaluationCode" in fallback
    assert "theorem evaluation" not in fallback


def test_original_certifier_computes_one_result_without_truth_in_lean():
    source = render_original_certificate(
        SPEC_BODY, TEST_MODEL, ORIGINAL_TEST_CASES, DIRECT_CONNECTOR)
    assert "Tests.test.all" in source
    assert "Tests.test_0" not in source and "Tests.test_1" not in source
    assert "ground_truth" not in source and "NO_CONFLICT" not in source

    class ResultRunner:
        def compile(self, source, *, name="x", reject_sorry=True):
            return True, "info: 3\n"

    certification = LeanCertifier(ResultRunner()).certify_original(
        SPEC_BODY, TEST_MODEL, ORIGINAL_TEST_CASES, DIRECT_CONNECTOR,
        label="original")
    assert certification.predictions == (True,)
    assert certification.evidence_tier == PROOF
    assert certification.execution_code == ""
    decision = evaluate_predictions(
        certification.evidence, PrivateOriginalTest().ground_truth)
    assert (decision.status, decision.outcome) == (SUCCESS, NO_CONFLICT)


def test_certifier_falls_back_to_execution_after_proof_failure():
    class TwoStageRunner:
        def __init__(self):
            self.calls = []

        def compile(self, source, *, name="x", reject_sorry=True):
            self.calls.append((name, source))
            if name.endswith("_proof"):
                return False, "error: Tactic `decide` failed"
            return True, "info: 14\n"

    runner = TwoStageRunner()
    certification = LeanCertifier(runner).certify(
        SPEC_BODY, TEST_MODEL, TEST_CASES, DIRECT_CONNECTOR, (True, False),
        label="fallback")
    assert certification.predictions == (True, False)
    assert certification.evidence_tier == EXECUTION
    assert "theorem evaluation" in certification.lean_code
    assert "#eval ccEvaluationCode" in certification.execution_code
    assert [name for name, _ in runner.calls] == [
        "fallback_proof", "fallback_execution"]


def test_original_test_contract_requires_one_nonempty_suite():
    runner = FakeRunner()
    ok, _ = validate_original_tests_contract(
        runner, TEST_MODEL, ORIGINAL_TEST_CASES, name="original")
    assert ok


def test_one_task_command_pipeline_runs_every_agent_then_evaluator(tmp_path):
    calls: list[str] = []

    class Spec:
        def run(self, *args, **kwargs):
            calls.append("spec")
            return SpecArtifact(SPEC_BODY, True, 1, [], "")

    class Tests:
        def run(self, *args, **kwargs):
            calls.append("tests")
            return LeanTestsArtifact(TEST_MODEL, TEST_CASES, True, 1, [], "")

    class Connector:
        def run(self, *args, **kwargs):
            calls.append("connector")
            return ConnectorArtifact(
                lean_code=DIRECT_CONNECTOR,
                compile_success=True,
                attempts_used=0,
                mode="direct",
            )

    class Certifier:
        def certify(self, *args, **kwargs):
            calls.append("certifier")
            return LeanCertification(
                (True, False),
                EvaluationEvidence.complete((True, False)),
                "#eval ccEvaluationCode",
                "",
            )

    result = process_one(
        _row(), Spec(), Tests(), Connector(), Certifier(), tmp_path,
        LLMCallLogger(tmp_path),
        {"repo__repo-1": _input_record()}, 7,
    )

    assert calls == ["spec", "tests", "connector", "certifier"]
    assert result["status"] == SUCCESS
    assert result["outcome"] == CONFLICT
    persisted = json.loads((tmp_path / "repo__repo-1" / "result.json").read_text())
    assert persisted["status"] == SUCCESS
    assert persisted["outcome"] == CONFLICT
    for filename in ("spec_log.json", "test_log.json", "connector_log.json"):
        log = json.loads((tmp_path / "repo__repo-1" / filename).read_text())
        assert "system_prompt" in log["prompts"]
        assert "system_prompt_sha256" in log["prompts"]
        assert "initial_prompt_sha256" in log["prompts"]
        assert log["prompts"]["template_sha256"]


@pytest.mark.parametrize("split, expected", [("original", True), ("oneoff", False)])
def test_single_patch_pipeline_uses_one_suite(tmp_path, split, expected):
    seen: dict[str, object] = {}

    class Spec:
        def run(self, *args, **kwargs):
            return SpecArtifact(SPEC_BODY, True, 1, [], "")

    class Tests:
        system_prompt = "original test prompt"

        def run(self, inputs, context, patch, second_patch, **kwargs):
            seen["patch"] = patch
            seen["second_patch"] = second_patch
            return LeanTestsArtifact(
                TEST_MODEL, ORIGINAL_TEST_CASES, True, 1, [], "")

    class Connector:
        system_prompt = "original connector prompt"

        def run(self, *args, **kwargs):
            return ConnectorArtifact(DIRECT_CONNECTOR, True, 0, "direct")

    class Certifier:
        def certify(self, *args, **kwargs):  # pragma: no cover
            raise AssertionError("conflicting certifier path must not run")

        def certify_original(self, *args, **kwargs):
            assert args[4] == (expected,)
            return LeanCertification(
                (expected,), EvaluationEvidence.complete((expected,)),
                "#eval ccEvaluationCode", "")

    result = process_one(
        _row(), Spec(), Tests(), Connector(), Certifier(), tmp_path,
        LLMCallLogger(tmp_path),
        {"repo__repo-1": _input_record()}, 7, split)

    patch = _row()["original_test_patch" if expected else "test_patch"]
    assert seen == {"patch": patch, "second_patch": ""}
    assert result["status"] == SUCCESS
    assert result["outcome"] == (NO_CONFLICT if expected else CONFLICT)
    assert result["predictions"] == [expected]
    assert result["ground_truth"] == [expected]
    private = json.loads(
        (tmp_path / "repo__repo-1" / "test_order.json").read_text())
    assert private == {"suite": "test", "ground_truth": [expected]}


def test_oneoff_packet_excludes_original_patch_and_gold():
    case, contract = prepare_agent_case(_row(), _input_record(), split="oneoff")
    assert contract.ground_truth == (False,)
    assert case.test_0_patch == _row()["test_patch"]
    assert case.test_1_patch == ""
    prompt = SweTestAgent._original_prompt(case.inputs, case.input_context, case.test_0_patch)
    assert "ORIGINAL_VALUE" not in prompt
    assert "GOLD_MUST_NOT_APPEAR" not in repr(case)
    row = _row()
    row["test_patch"] = ""
    with pytest.raises(InputUnavailableError):
        prepare_agent_case(row, _input_record(), split="oneoff")


@pytest.mark.parametrize("code, outcome", [(2, CONFLICT), (3, "INCORRECT"), (0, "INCONCLUSIVE")])
def test_oneoff_execution_fallback_scoring(code, outcome):
    class Runner:
        def compile(self, source, *, name="x", **kwargs):
            if name.endswith("_proof"):
                assert "ccTestPasses = false" in source
                assert "by decide" in source
                return False, "proof failed"
            return True, f"info: {code}\n"

    certification = LeanCertifier(Runner()).certify_original(
        SPEC_BODY, TEST_MODEL, ORIGINAL_TEST_CASES, DIRECT_CONNECTOR,
        (False,), label="oneoff")
    assert evaluate_predictions(certification.evidence, (False,)).outcome == outcome


def test_explicit_ids_override_config_all_for_pilot_runs():
    rows = {"task-b": {}, "task-a": {}}
    assert select_instance_ids(rows, run_all=True, explicit_ids=["task-b"]) == ["task-b"]
    assert select_instance_ids(rows, run_all=True, explicit_ids=None) == ["task-a", "task-b"]


def test_submission_limits_cap_lean_compiles_at_28(monkeypatch):
    class RejectingRunner:
        def __init__(self):
            self.sources: list[str] = []

        def compile(self, source, *, name="x", reject_sorry=True):
            self.sources.append(source)
            return False, "fixture rejection"

    class Box:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def bash(self, command):  # pragma: no cover - responses always submit
            raise AssertionError("unexpected bash turn")

    monkeypatch.setattr(spec_agent_module, "RepoContainer", Box)

    spec_llm = FixedLLM(f"SUBMIT_SPEC\n```lean4\n{SPEC_BODY}\n```")
    spec_runner = RejectingRunner()
    spec = SweCodebaseSpecAgent(
        spec_llm, spec_runner, max_turns=120, max_submissions=10,
    ).run("repo__repo-1", "issue", "repo/repo", ["f(1)"], "context")
    assert not spec.compile_success
    assert spec.attempts_used == spec_llm.calls == len(spec_runner.sources) == 10

    tests_response = (
        "SUBMIT_TESTS\nTEST_MODEL\n```lean4\n" + TEST_MODEL + "\n```\n"
        "TEST_CASES\n```lean4\n" + TEST_CASES + "\n```"
    )
    test_llm = FixedLLM(tests_response)
    test_runner = RejectingRunner()
    tests = SweTestAgent(
        test_llm, test_runner, max_submissions=10,
    ).run(["f(1)"], "context", "patch 0", "patch 1")
    assert not tests.compile_success
    assert tests.attempts_used == test_llm.calls == len(test_runner.sources) == 10

    connector_response = (
        "SUBMIT_CONNECTOR\n```lean4\n" + DIRECT_CONNECTOR + "\n```"
    )
    connector_llm = FixedLLM(connector_response)
    connector_runner = RejectingRunner()
    connector = SweConnectorAgent(
        connector_llm, connector_runner, max_submissions=6,
    ).run(SPEC_BODY, TEST_MODEL, TEST_CASES, ["f(1)"], "context", label="limit")
    assert not connector.compile_success
    assert connector.attempts_used == connector_llm.calls == 6
    assert len(connector_runner.sources) == 7  # one direct check + six submissions

    assert 10 + 10 + 7 + 1 == 28
