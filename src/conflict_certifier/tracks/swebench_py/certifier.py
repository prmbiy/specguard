"""Mechanical numbered-prediction certifier for SWE-bench Python artifacts."""

from __future__ import annotations

import json
from dataclasses import dataclass

from conflict_certifier.evaluation import EvaluationEvidence
from conflict_certifier.tracks.swebench_py.contracts import loader_source
from conflict_certifier.tracks.swebench_py.pyrunner import TaskPythonRunner


_RESULT_MARKER = "CONFLICT_CERTIFIER_RESULT="


@dataclass(frozen=True)
class PythonCertification:
    predictions: tuple[bool, bool] | None
    evidence: EvaluationEvidence
    code: str
    execution_output: str


def render_certificate(inputs: tuple[str, ...] | list[str], context: str) -> str:
    """Render label-blind code that computes only test_0/test_1 pass values."""
    return loader_source() + f'''\
import json

spec = cc_load("cc_agent_spec", "spec.py")
tests = cc_load("cc_agent_tests", "tests.py")
connector = cc_load("cc_agent_connector", "connector.py")
frozen_inputs = {tuple(inputs)!r}
input_context = {context!r}

predictions = []
supported = True
for test_name in ("test_0", "test_1"):
    results = []
    for pair in tests.TESTS:
        result = connector.check(
            pair["input_number"], pair[test_name], spec.run,
            frozen_inputs, input_context)
        if result is not None and type(result) is not bool:
            raise TypeError("Connector.check must return True, False, or None")
        if result is None:
            supported = False
        results.append(result)
    predictions.append(all(result is True for result in results))

print({_RESULT_MARKER!r} + json.dumps({{
    "supported": supported,
    "predictions": predictions,
}}, separators=(",", ":")))
'''


class PythonCertifier:
    def __init__(self, runner: TaskPythonRunner):
        self._runner = runner

    def certify(
        self,
        spec_code: str,
        tests_code: str,
        connector_code: str,
        *,
        inputs: tuple[str, ...] | list[str],
        context: str,
    ) -> PythonCertification:
        code = render_certificate(inputs, context)
        result = self._runner.run_files(
            {
                "spec.py": spec_code,
                "tests.py": tests_code,
                "connector.py": connector_code,
                "cert.py": code,
            },
            entrypoint="cert.py",
        )
        if result.infrastructure_error or result.timed_out:
            reason = "container timeout" if result.timed_out else result.output[-1000:]
            return PythonCertification(
                None, EvaluationEvidence.error("error", reason),
                code, result.output)
        if not result.ok:
            return PythonCertification(
                None,
                EvaluationEvidence.inconclusive(
                    "not_certified",
                    "agent artifacts raised an exception during mechanical evaluation"),
                code, result.output)
        lines = [line for line in result.output.splitlines()
                 if line.startswith(_RESULT_MARKER)]
        if len(lines) != 1:
            return PythonCertification(
                None,
                EvaluationEvidence.inconclusive(
                    "invalid_certifier_output",
                    "mechanical evaluation did not emit exactly one result"),
                code, result.output)
        try:
            payload = json.loads(lines[0][len(_RESULT_MARKER):])
            predictions = payload["predictions"]
            if (not isinstance(payload.get("supported"), bool)
                    or not isinstance(predictions, list) or len(predictions) != 2
                    or any(type(value) is not bool for value in predictions)):
                raise ValueError("invalid result shape")
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return PythonCertification(
                None,
                EvaluationEvidence.inconclusive(
                    "invalid_certifier_output",
                    "mechanical evaluation emitted malformed results"),
                code, result.output)
        if not payload["supported"]:
            return PythonCertification(
                None,
                EvaluationEvidence.inconclusive(
                    "unsupported_connection",
                    "Connector.check returned None for at least one case"),
                code, result.output)
        values = (predictions[0], predictions[1])
        return PythonCertification(
            values, EvaluationEvidence.complete(values), code, result.output)
