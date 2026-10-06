"""Fast, no-API tests for the paired execution-consensus baseline."""

from __future__ import annotations

import json

import pytest

from conflict_certifier.tracks.swebench.consensus import _policy, paired_rows, process_one
from conflict_certifier.tracks.swebench.consensus_eval import interpret_test_output, patch_paths


def test_pairs_are_same_issue_and_original_patch():
    pairs = paired_rows()
    assert len(pairs) == 349
    for original, oneoff in pairs.values():
        assert original["problem_statement"] == oneoff["problem_statement"]
        assert original["test_patch"] == oneoff["original_test_patch"]


@pytest.mark.parametrize("path", [
    "tests/test_api.py", "package/test_api.py", "package/api_test.py",
    "package/conftest.py", "../escape.py", "package/.git/config.py",
])
def test_rejects_test_or_unsafe_patch(path):
    diff = f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n"
    with pytest.raises(ValueError):
        patch_paths(diff)


def test_thresholds_and_independent_n1_n5():
    votes = [
        {"instance_id": "x", "valid": True, "original": {"passed": True},
         "oneoff": {"passed": False}},
        {"instance_id": "x", "valid": True, "original": {"passed": True},
         "oneoff": {"passed": False}},
        {"instance_id": "x", "valid": True, "original": {"passed": True},
         "oneoff": {"passed": False}},
        {"instance_id": "x", "valid": True, "original": {"passed": True},
         "oneoff": {"passed": True}},
        {"instance_id": "x", "valid": False},
    ]
    assert _policy(votes, 1, "majority", "oneoff")["outcome"] == "CONFLICT"
    assert _policy(votes, 5, "majority", "oneoff")["outcome"] == "CONFLICT"
    assert _policy(votes, 5, "all", "oneoff")["outcome"] == "INCORRECT"
    assert _policy(votes, 5, "majority", "original")["outcome"] == "NO_CONFLICT"
    for vote in votes[:4]:
        vote["valid"] = False
    assert _policy(votes, 5, "majority", "oneoff")["outcome"] == "INCONCLUSIVE"


def test_mutated_failure_outside_original_target_list_is_counted():
    row = {"FAIL_TO_PASS": ["tests/test_api.py::test_fix"]}
    statuses = {"tests/test_api.py::test_fix": "PASSED",
                "tests/test_api.py::test_mutated_expectation": "FAILED"}
    result = interpret_test_output(row, "", (lambda output, spec: statuses, None))
    assert result["valid"] is True
    assert result["passed"] is False


def test_one_generated_patch_scored_against_both_private_suites(tmp_path, monkeypatch):
    from conflict_certifier.tracks.swebench import consensus

    original = {"instance_id": "example__repo-1", "problem_statement": "Fix API.",
                "test_patch": "diff --git a/tests/test_api.py b/tests/test_api.py\n",
                "base_commit": "abc"}
    oneoff = {**original, "test_patch": "diff --git a/tests/test_api.py b/tests/test_api.py\nmutated"}
    calls = []
    patch = "diff --git a/pkg/api.py b/pkg/api.py\n--- a/pkg/api.py\n+++ b/pkg/api.py\n@@ -1 +1 @@\n-a\n+b\n"

    class Box:
        def __init__(self, task, agent_view, test_paths):
            assert agent_view and test_paths == ["tests/test_api.py"]

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def bash(self, command, timeout):
            return 0, "1\ta\n"

        def apply(self, text, check_only):
            assert check_only and text == patch
            return True, ""

    class Client:
        def __init__(self):
            self.prompts = []
            self.usage = type("Usage", (), {"start_local": lambda self: None,
                                            "take_local": lambda self: {}})()

        def complete_conversation(self, system, messages):
            self.prompts.append(messages[0]["content"])
            return patch

    class Logger:
        def start_task(self, task):
            pass

    monkeypatch.setattr(consensus, "TaskContainer", Box)
    monkeypatch.setattr(consensus, "localize", lambda *a: (
        [{"path": "pkg/api.py", "start_line": 1, "end_line": 1}], []))

    def fake_score(identifier, source_patch, row, timeout):
        calls.append((source_patch, row["test_patch"]))
        return {"valid": True, "passed": row is original}

    monkeypatch.setattr(consensus, "score_candidate", fake_score)
    repair = Client()
    result = process_one((original, oneoff), tmp_path,
                         {"localize_turns": 1, "exec_timeout": 1,
                          "test_timeout": 1, "candidates": 1, "repair_attempts": 3},
                         Client(), repair, Logger())
    assert calls == [(patch, original["test_patch"]), (patch, oneoff["test_patch"])]
    assert len(repair.prompts) == 1
    assert "mutated" not in repair.prompts[0]
    assert result["policies"]["oneoff_n1_majority"]["outcome"] == "CONFLICT"
    assert result["policies"]["original_n1_majority"]["outcome"] == "NO_CONFLICT"
    # Resume does not buy a second model call or rerun either test suite.
    assert process_one((original, oneoff), tmp_path,
                       {"localize_turns": 1, "exec_timeout": 1,
                        "test_timeout": 1, "candidates": 1, "repair_attempts": 3},
                       Client(), repair, Logger()) == result
    assert len(calls) == 2


@pytest.mark.parametrize("fail_every_time", [False, True])
def test_repair_gets_three_attempts_and_final_warning(tmp_path, monkeypatch,
                                                     fail_every_time):
    from conflict_certifier.tracks.swebench import consensus

    original = {"instance_id": "example__repo-2", "problem_statement": "Fix API.",
                "test_patch": "diff --git a/tests/test_api.py b/tests/test_api.py\n"}
    oneoff = {**original, "test_patch": original["test_patch"] + "mutated"}
    patch = "diff --git a/pkg/api.py b/pkg/api.py\n--- a/pkg/api.py\n+++ b/pkg/api.py\n@@ -1 +1 @@\n-a\n+b\n"

    class Box:
        attempts = 0

        def __init__(self, task, agent_view, test_paths):
            assert agent_view

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def bash(self, command, timeout):
            return 0, "1\ta\n"

        def apply(self, text, check_only):
            self.attempts += 1
            return (False, "corrupt hunk") if fail_every_time or self.attempts < 3 else (True, "")

    class Client:
        def __init__(self):
            self.messages = []
            self.usage = type("Usage", (), {"start_local": lambda self: None,
                                            "take_local": lambda self: {}})()

        def complete_conversation(self, system, messages):
            self.messages.append(list(messages))
            return patch

    class Logger:
        def start_task(self, task):
            pass

    monkeypatch.setattr(consensus, "TaskContainer", Box)
    monkeypatch.setattr(consensus, "localize", lambda *a: (
        [{"path": "pkg/api.py", "start_line": 1, "end_line": 1}], []))
    monkeypatch.setattr(consensus, "score_candidate", lambda *a, **k: {
        "valid": True, "passed": False})
    repair = Client()
    result = process_one((original, oneoff), tmp_path,
                         {"localize_turns": 1, "exec_timeout": 1,
                          "test_timeout": 1, "candidates": 1, "repair_attempts": 3},
                         Client(), repair, Logger())
    assert len(repair.messages) == 3
    assert "corrupt hunk" in repair.messages[1][-1]["content"]
    assert "FINAL ATTEMPT" in repair.messages[2][-1]["content"]
    assert "mutated" not in str(repair.messages)
    candidate = json.loads((tmp_path / original["instance_id"] / "candidates"
                            / "01" / "candidate.json").read_text())
    assert candidate["attempts"] == 3
    assert len(list((tmp_path / original["instance_id"] / "candidates"
                     / "01" / "attempts").glob("*.json"))) == 3
    assert candidate["valid_patch"] is not fail_every_time
    assert result["valid_candidates"] == (0 if fail_every_time else 1)
