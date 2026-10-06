from __future__ import annotations

import subprocess

import pytest

from conflict_certifier.evaluation import EvidenceKind
from conflict_certifier.tracks.swebench_py.artifacts import (
    ArtifactError,
    parse_connector_response,
    parse_spec_response,
    parse_tests_response,
)
from conflict_certifier.tracks.swebench_py.certifier import PythonCertifier, render_certificate
from conflict_certifier.tracks.swebench_py.connector_agent import SwePythonConnectorAgent
from conflict_certifier.tracks.swebench_py.pyrunner import ContainerResult, TaskPythonRunner
from conflict_certifier.tracks.swebench_py.spec_agent import SwePythonSpecAgent
from conflict_certifier.tracks.swebench_py.test_agent import SwePythonTestAgent


PATCH_0 = "+        self.assertEqual(convert(1), 2)\n"
PATCH_1 = "+        self.assertEqual(convert(1), 3)\n"


def _tests_response(source_0: str | None = None) -> str:
    source_0 = source_0 or "+        self.assertEqual(convert(1), 2)"
    return f'''SUBMIT_TESTS
```python
TESTS = [{{
    "input_number": 0,
    "test_0": {{
        "relation": "equals",
        "expected": 2,
        "source_lines": [{source_0!r}],
    }},
    "test_1": {{
        "relation": "equals",
        "expected": 3,
        "source_lines": ["+        self.assertEqual(convert(1), 3)"],
    }},
}}]
```
'''


def test_spec_and_connector_protocols_are_strict():
    assert "def run" in parse_spec_response(
        "SUBMIT_SPEC\n```python\ndef run(spec_input):\n    return spec_input\n```")
    assert "def check" in parse_connector_response(
        "SUBMIT_CONNECTOR\n```python\ndef check(input_number, demand, spec_run, frozen_inputs, input_context):\n"
        "    return None\n```")
    with pytest.raises(ArtifactError, match="forbidden import"):
        parse_spec_response(
            "SUBMIT_SPEC\n```python\nimport subprocess\ndef run(x): return x\n```")
    with pytest.raises(ArtifactError, match="exactly one Python block"):
        parse_connector_response(
            "SUBMIT_CONNECTOR\n```python\ndef check(*x): return None\n```\n```python\nx=1\n```")


def test_test_artifact_is_literal_neutral_and_provenanced():
    code, tests = parse_tests_response(
        _tests_response(), patches=(PATCH_0, PATCH_1), input_count=1)
    assert code.startswith("TESTS =")
    assert tests[0]["test_0"]["expected"] == 2
    assert tests[0]["test_1"]["expected"] == 3


def test_minimal_schema_uses_none_for_an_absent_assertion():
    response = f'''SUBMIT_TESTS
```python
TESTS = [{{
    "input_number": 0,
    "test_0": {{
        "relation": "equals",
        "expected": 2,
        "source_lines": [{PATCH_0.strip()!r}],
    }},
    "test_1": None,
}}]
```
'''
    _, tests = parse_tests_response(
        response, patches=(PATCH_0, ""), input_count=1)
    assert tests == [{
        "input_number": 0,
        "test_0": {
            "relation": "equals", "expected": 2,
            "source_lines": [PATCH_0.strip()],
        },
        "test_1": None,
    }]


def test_test_artifact_rejects_fabricated_source_quote():
    with pytest.raises(ArtifactError, match="absent from test_0"):
        parse_tests_response(
            _tests_response("+        self.assertEqual(convert(1), 999)"),
            patches=(PATCH_0, PATCH_1), input_count=1)


def test_test_artifact_rejects_computation_and_wrong_input_number():
    computed = _tests_response().replace('"expected": 2', '"expected": make_value()')
    with pytest.raises(ArtifactError, match="literals"):
        parse_tests_response(computed, patches=(PATCH_0, PATCH_1), input_count=1)
    wrong_input = _tests_response().replace('"input_number": 0', '"input_number": 7', 1)
    with pytest.raises(ArtifactError, match="frozen input"):
        parse_tests_response(wrong_input, patches=(PATCH_0, PATCH_1), input_count=1)


def test_agent_prompt_boundaries_are_neutral():
    spec_prompt = SwePythonSpecAgent._prompt("issue", "repo", ("convert(1)",), "ctx", 10)
    test_prompt = SwePythonTestAgent._prompt(("convert(1)",), "ctx", PATCH_0, PATCH_1)
    connector_prompt = SwePythonConnectorAgent._prompt(
        "def run(x): return x", "TESTS = {}", ("convert(1)",), "ctx")
    assert PATCH_0 not in spec_prompt and PATCH_1 not in spec_prompt
    assert "issue" not in test_prompt and "def run" not in test_prompt
    assert "good_test" not in connector_prompt and "bad_test" not in connector_prompt
    assert "test_0" in test_prompt and "test_1" in test_prompt


class FakeRunner:
    def __init__(self, result: ContainerResult):
        self.result = result
        self.files = None

    def run_files(self, files, *, entrypoint):
        self.files = (files, entrypoint)
        return self.result


class FakeLLM:
    def __init__(self, responses):
        self.responses = iter(responses)

    def complete_conversation(self, system_prompt, conversation):
        return next(self.responses)


def test_test_and_connector_agents_accept_compiling_artifacts():
    runner = FakeRunner(ContainerResult(True, ""))
    tests = SwePythonTestAgent(
        FakeLLM([_tests_response()]), runner, max_submissions=1).run(
            ("convert(1)",), "ctx", PATCH_0, PATCH_1)
    assert tests.success and tests.tests[0]["test_0"]["expected"] == 2

    connector_response = '''SUBMIT_CONNECTOR
```python
def check(input_number, demand, spec_run, frozen_inputs, input_context):
    return None
```
'''
    connector = SwePythonConnectorAgent(
        FakeLLM([connector_response]), runner, max_submissions=1).run(
            "def run(x): return x", tests.code, ("convert(1)",), "ctx")
    assert connector.success and connector.attempts_used == 1


def test_certifier_returns_numbered_predictions_without_private_labels():
    output = 'CONFLICT_CERTIFIER_RESULT={"supported":true,"predictions":[true,false]}\n'
    runner = FakeRunner(ContainerResult(True, output))
    certification = PythonCertifier(runner).certify(
        "def run(x): return x", "TESTS = []",
        "def check(*args): return True", inputs=("x",), context="ctx")
    assert certification.predictions == (True, False)
    assert certification.evidence.kind is EvidenceKind.PREDICTIONS
    assert certification.evidence.predictions == (True, False)
    assert "good_test" not in certification.code
    assert "bad_test" not in certification.code
    assert runner.files[1] == "cert.py"


def test_certifier_treats_none_as_unsupported():
    output = 'CONFLICT_CERTIFIER_RESULT={"supported":false,"predictions":[true,false]}\n'
    certification = PythonCertifier(FakeRunner(ContainerResult(True, output))).certify(
        "", "", "", inputs=("x",), context="")
    assert certification.predictions is None
    assert certification.evidence.kind is EvidenceKind.INCONCLUSIVE
    assert certification.evidence.note == "unsupported_connection"


def test_certificate_source_has_no_private_order():
    source = render_certificate(("call(1)",), "context")
    assert "test_0" in source and "test_1" in source
    assert "good" not in source.lower()
    assert "bad" not in source.lower()


@pytest.mark.skipif(
    subprocess.run(
        ["docker", "image", "inspect",
         "swebench/sweb.eval.x86_64.astropy_1776_astropy-14182:latest"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode != 0,
    reason="SWE-bench smoke-test image is not installed",
)
def test_real_task_image_certification_smoke():
    spec = "def run(spec_input):\n    return spec_input + 1\n"
    tests = '''TESTS = [{
    "input_number": 0,
    "test_0": {"relation": "equals", "expected": 2, "source_lines": ["x"]},
    "test_1": {"relation": "equals", "expected": 3, "source_lines": ["y"]},
}]
'''
    connector = '''def check(input_number, demand, spec_run, frozen_inputs, input_context):
    if demand is None:
        return True
    actual = spec_run(int(frozen_inputs[input_number]))
    return actual == demand["expected"]
'''
    with TaskPythonRunner("astropy__astropy-14182", timeout_seconds=20) as runner:
        certification = PythonCertifier(runner).certify(
            spec, tests, connector, inputs=("1",), context="")
    assert certification.predictions == (True, False), certification.execution_output


@pytest.mark.skipif(
    subprocess.run(
        ["docker", "image", "inspect",
         "swebench/sweb.eval.x86_64.astropy_1776_astropy-14182:latest"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode != 0,
    reason="SWE-bench smoke-test image is not installed",
)
def test_shell_changes_persist_into_python_execution():
    with TaskPythonRunner("astropy__astropy-14182", timeout_seconds=20) as runner:
        code, output = runner.bash(
            "printf same-container > /testbed/.cc_same_container_test")
        assert code == 0, output
        result = runner.run_files({
            "check.py": (
                "from pathlib import Path\n"
                "assert Path('/testbed/.cc_same_container_test').read_text() "
                "== 'same-container'\n")
        }, entrypoint="check.py")
        assert result.ok, result.output
