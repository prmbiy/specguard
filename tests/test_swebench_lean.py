from __future__ import annotations

import pytest

from conflict_certifier import config
from conflict_certifier.lean.runner import LeanRunner
from conflict_certifier.tracks.swebench.artifacts import PrivateTestOrder
from conflict_certifier.tracks.swebench.connector_agent import DIRECT_CONNECTOR
from conflict_certifier.evaluation import CONFLICT, NO_CONFLICT, evaluate_predictions
from conflict_certifier.tracks.swebench.certifier import PROOF, LeanCertifier


SPEC_BODY = """
namespace Spec
abbrev Input := Nat
abbrev Output := Nat
def run (input : Input) : Output := input + 1
end Spec
"""
TEST_MODEL = """
namespace Tests
structure Case where
  input : Nat
  expected : Nat
end Tests
"""
TEST_CASES = """
namespace Tests
def test_0 : List Case := [{ input := 1, expected := 2 }]
def test_1 : List Case := [{ input := 1, expected := 3 }]
end Tests
"""
ORIGINAL_TEST_CASES = """
namespace Tests
def test : List Case := [{ input := 1, expected := 2 }]
end Tests
"""

TRANSCRIPTION_MODEL = """
namespace Tests
inductive Case where
  | assertion_1 : Nat → Case
end Tests
"""
TRANSCRIPTION_CASES = """
namespace Tests
def assertion_1_test_0 : Nat := 2
def assertion_1_test_1 : Nat := 3
def test_0 : List Case := [.assertion_1 assertion_1_test_0]
def test_1 : List Case := [.assertion_1 assertion_1_test_1]
end Tests
"""
TRANSCRIPTION_CONNECTOR = """
namespace Connector
def check : Tests.Case → Option Bool
  | .assertion_1 demanded => some (Spec.run 1 == demanded)
end Connector
"""


pytestmark = pytest.mark.lean


def _lean_available() -> bool:
    try:
        config.resolve_lean_env().validate()
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _lean_available(), reason="no Lean environment configured")
def test_direct_connector_certificate():
    runner = LeanRunner()
    try:
        certification = LeanCertifier(runner).certify(
            SPEC_BODY, TEST_MODEL, TEST_CASES, DIRECT_CONNECTOR,
            PrivateTestOrder(0, 1).ground_truth,
            label="swe_smoke")
    finally:
        runner.close()
    result = evaluate_predictions(
        certification.evidence, PrivateTestOrder(0, 1).ground_truth)
    assert result.outcome == CONFLICT, certification.compile_output[-2000:]
    assert certification.evidence_tier == PROOF
    assert "theorem evaluation" in certification.lean_code
    assert "by decide" in certification.lean_code
    assert certification.execution_code == ""


@pytest.mark.skipif(not _lean_available(), reason="no Lean environment configured")
def test_original_direct_connector_certificate():
    runner = LeanRunner()
    try:
        certification = LeanCertifier(runner).certify_original(
            SPEC_BODY, TEST_MODEL, ORIGINAL_TEST_CASES, DIRECT_CONNECTOR,
            (True,),
            label="swe_original_smoke")
    finally:
        runner.close()
    result = evaluate_predictions(certification.evidence, (True,))
    assert result.outcome == NO_CONFLICT, certification.compile_output[-2000:]
    assert certification.predictions == (True,)


@pytest.mark.skipif(not _lean_available(), reason="no Lean environment configured")
def test_neutral_transcription_and_smart_connector_compile():
    runner = LeanRunner()
    try:
        certification = LeanCertifier(runner).certify(
            SPEC_BODY, TRANSCRIPTION_MODEL, TRANSCRIPTION_CASES,
            TRANSCRIPTION_CONNECTOR,
            PrivateTestOrder(0, 1).ground_truth,
            label="neutral_transcription",
        )
    finally:
        runner.close()
    result = evaluate_predictions(
        certification.evidence, PrivateTestOrder(0, 1).ground_truth)
    assert result.outcome == CONFLICT, certification.compile_output[-2000:]
