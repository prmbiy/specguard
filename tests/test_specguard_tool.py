"""No-provider checks for the native-tool SWE-bench Lean entry point."""

from __future__ import annotations

import json
from types import SimpleNamespace

from conflict_certifier.tracks.swebench import run, specguard_tool as tool


class FakeLogger:
    def __init__(self):
        self.entries = []

    def begin_call(self, **kwargs):
        self.entries.append(kwargs)
        return {"started": 0}

    def response(self, handle, **kwargs):
        self.entries[-1]["response"] = kwargs["response"]

    def error(self, handle, **kwargs):
        raise AssertionError(kwargs)


def test_tool_prompts_preserve_boundaries_and_use_native_actions():
    assert "call `bash`" in tool.SPEC_SYSTEM
    assert "submit_spec" in tool.SPEC_SYSTEM
    assert "one ```bash``` block" not in tool.SPEC_SYSTEM
    assert "submit_tests" in tool.TEST_SYSTEM
    assert "submit_tests" in tool.ORIGINAL_TEST_SYSTEM
    assert "submit_connector" in tool.CONNECTOR_SYSTEM
    assert "submit_connector" in tool.ORIGINAL_CONNECTOR_SYSTEM
    assert "## Issue" in tool.ToolSpecAgent._initial_prompt(
        "issue text", "repo", 4, ["f(1)"], "context")
    for system in (tool.TEST_SYSTEM, tool.ORIGINAL_TEST_SYSTEM):
        assert "issue text" not in system
    assert "SUBMIT_CONNECTOR" not in tool.ToolConnectorAgent._prompt(
        "spec", "model", "cases", ["f(1)"], "context")
    assert "call submit_spec" in tool.ToolSpecAgent._turn_tag(1, 2)


def test_native_call_continues_with_matching_tool_output():
    logger = FakeLogger()
    response = SimpleNamespace(
        id="resp_1", status="completed",
        output=[SimpleNamespace(model_dump=lambda: {
            "type": "function_call", "name": "bash", "call_id": "call_1",
            "arguments": '{"command":"pwd"}', "id": "fc_tmp_1",
            "caller": None, "namespace": None, "status": "completed",
        })],
        usage=SimpleNamespace(input_tokens=100, output_tokens=20,
                              total_tokens=120,
                              input_tokens_details=SimpleNamespace(cached_tokens=40),
                              output_tokens_details=SimpleNamespace(reasoning_tokens=5)),
    )
    requests = []
    client = object.__new__(tool.ToolResponsesClient)
    client._client = SimpleNamespace(responses=SimpleNamespace(
        create=lambda **kw: (requests.append(kw), response)[1]))
    client._model = "test-model"
    client._max_tokens = 1000
    client._effort = "medium"
    client._logger = logger
    client._agent = "spec"
    client._key = "not-a-real-key"
    client._prices = tool.DEFAULT_PRICES
    client.usage = tool.UsageTracker()
    session = client.session("system", tool.SPEC_TOOLS, "initial")
    action = session.step()
    assert action == tool.Action("bash", {"command": "pwd"}, "call_1")
    session.tool_result(action, "/testbed")
    assert session.history[1] == {
        "type": "function_call", "call_id": "call_1", "name": "bash",
        "arguments": '{"command":"pwd"}',
    }
    assert session.history[-1] == {
        "type": "function_call_output", "call_id": "call_1", "output": "/testbed"}
    assert requests[0]["tool_choice"] == "required"
    assert requests[0]["parallel_tool_calls"] is False
    assert requests[0]["store"] is False
    assert requests[0]["reasoning"] == {"effort": "medium"}
    assert logger.entries[0]["response"]["usage"]["cached_input_tokens"] == 40
    session.step()
    assert requests[1]["input"][1] == session.history[1]
    assert "caller" not in requests[1]["input"][1]


def test_usage_collation_rebuilds_task_and_run_totals(tmp_path):
    task_dir = tmp_path / "task-a"
    task_dir.mkdir()
    (task_dir / "spec_log.json").write_text(json.dumps({"api_calls": [
        {"response": {"usage": {"available": True, "input_tokens": 10,
                                  "cached_input_tokens": 2,
                                  "output_tokens": 3,
                                  "estimated_cost_usd": 0.0002}}},
    ]}), encoding="utf-8")
    result = tool.collate_usage(tmp_path)
    assert result["total"]["calls"] == 1
    assert result["total"]["input_tokens"] == 10
    assert result["by_agent"]["spec"]["output_tokens"] == 3
    assert json.loads((task_dir / "usage.json").read_text())["estimated_cost_usd"] == 0.0002


def test_legacy_parser_and_tool_parser_are_separate():
    legacy = {action.dest for action in run._parser()._actions}
    native = {action.dest for action in run._parser(tool_mode=True)._actions}
    assert "input_price" not in legacy
    assert "input_price" in native


def test_spec_agent_executes_one_bash_call_then_submits(monkeypatch):
    class FakeBox:
        def __init__(self, instance_id, exec_timeout):
            assert instance_id == "task-a"

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def bash(self, command):
            assert command == "pwd"
            return 0, "/testbed"

    class FakeSession:
        def __init__(self):
            self.history = []
            self.actions = iter([
                tool.Action("bash", {"command": "pwd"}, "call-1"),
                tool.Action("submit_spec", {"lean": "namespace Spec\nend Spec"}, "call-2"),
            ])

        def step(self):
            return next(self.actions)

        def tool_result(self, action, output):
            self.history.append((action.name, output))

    session = FakeSession()
    llm = SimpleNamespace(session=lambda *args: session)
    monkeypatch.setattr(tool, "RepoContainer", FakeBox)
    monkeypatch.setattr(tool, "validate_spec_contract", lambda *a, **kw: (True, "ok"))
    agent = tool.ToolSpecAgent(llm, object(), max_turns=3)
    result = agent.run("task-a", "issue", "repo", ["f(1)"], "context")
    assert result.compile_success
    assert result.attempts_used == 1
    assert "Command exited 0" in session.history[0][1]
    assert "/testbed" in session.history[0][1]
