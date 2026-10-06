from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from conflict_certifier.llm.call_log import LLMCallLogger
from conflict_certifier.llm.client import (
    AnthropicClient,
    LLMRefusalError,
    OpenAICompatibleClient,
)


def _log(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_anthropic_persists_thinking_only_response_in_task_test_log(tmp_path):
    logger = LLMCallLogger(tmp_path)
    logger.start_task("task_0")
    responses = [
        SimpleNamespace(
            id="msg_0", model="claude-fable-5", stop_reason="max_tokens",
            stop_sequence=None, content=[SimpleNamespace(type="thinking")],
            usage=SimpleNamespace(input_tokens=10, output_tokens=16000),
        ),
        SimpleNamespace(
            id="msg_1", model="claude-fable-5", stop_reason="end_turn",
            stop_sequence=None,
            content=[SimpleNamespace(type="text", text="SUBMIT_TESTS")],
            usage=SimpleNamespace(input_tokens=12, output_tokens=4),
        ),
    ]

    class Messages:
        def create(self, **kwargs):
            return responses.pop(0)

    client = AnthropicClient(
        "secret", "claude-fable-5", max_tokens=16000,
        call_logger=logger, agent="tests")
    client._client = SimpleNamespace(messages=Messages())
    assert client.complete_conversation(
        "system", [{"role": "user", "content": "first"}]) == ""
    path = tmp_path / "task_0" / "test_log.json"
    first = _log(path)
    assert first["in_progress"] is True
    assert first["conversation"][-1] == {"role": "assistant", "content": ""}
    assert first["api_calls"][0]["response"]["stop_reason"] == "max_tokens"
    assert first["api_calls"][0]["response"]["content_block_types"] == ["thinking"]
    assert first["api_calls"][0]["response"]["usage"]["output_tokens"] == 16000

    assert client.complete_conversation("system", [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": ""},
        {"role": "user", "content": "retry"},
    ]) == "SUBMIT_TESTS"
    final = _log(path)
    assert len(final["api_calls"]) == 2
    assert final["conversation"][-1]["content"] == "SUBMIT_TESTS"


def test_provider_error_is_persisted_and_key_is_redacted(tmp_path):
    logger = LLMCallLogger(tmp_path)
    logger.start_task("task_1")

    class Messages:
        def create(self, **kwargs):
            raise ValueError("request rejected for secret-key")

    client = AnthropicClient(
        "secret-key", "claude-fable-5", call_logger=logger, agent="spec")
    client._client = SimpleNamespace(messages=Messages())
    with pytest.raises(ValueError):
        client.complete("system", "user")

    path = tmp_path / "task_1" / "spec_log.json"
    log = _log(path)
    error = log["api_calls"][0]["provider_attempts"][-1]["error"]
    assert error["message"] == "request rejected for [REDACTED]"
    assert "secret-key" not in path.read_text(encoding="utf-8")


def test_anthropic_refusal_is_terminal_after_one_provider_call(tmp_path):
    logger = LLMCallLogger(tmp_path)
    logger.start_task("task_refused")
    calls = 0

    class Messages:
        def create(self, **kwargs):
            nonlocal calls
            calls += 1
            return SimpleNamespace(
                id="msg_refused", model="claude-fable-5",
                stop_reason="refusal", stop_sequence=None, content=[],
                usage=SimpleNamespace(input_tokens=20, output_tokens=0),
            )

    client = AnthropicClient(
        "secret", "claude-fable-5", call_logger=logger, agent="tests")
    client._client = SimpleNamespace(messages=Messages())
    with pytest.raises(LLMRefusalError, match="refused the tests request"):
        client.complete("system", "user")

    assert calls == 1
    log = _log(tmp_path / "task_refused" / "test_log.json")
    assert log["api_calls"][0]["response"]["stop_reason"] == "refusal"
    assert len(log["api_calls"][0]["provider_attempts"]) == 1


def test_openai_compatible_persists_finish_reason_in_connector_log(tmp_path):
    logger = LLMCallLogger(tmp_path)
    logger.start_task("task_2")
    client = OpenAICompatibleClient(
        "secret", "gpt-test", call_logger=logger, agent="connector")
    usage = SimpleNamespace(prompt_tokens=8, completion_tokens=3,
                            prompt_tokens_details=SimpleNamespace(cached_tokens=6, cache_write_tokens=2))
    response = SimpleNamespace(
        id="chat_0", model="gpt-test", usage=usage,
        choices=[SimpleNamespace(
            finish_reason="stop",
            message=SimpleNamespace(content="SUBMIT_CONNECTOR"))],
    )

    class Completions:
        def create(self, **kwargs):
            return response

    client._client = SimpleNamespace(
        chat=SimpleNamespace(completions=Completions()))
    assert client.complete("system", "user") == "SUBMIT_CONNECTOR"

    log = _log(tmp_path / "task_2" / "connector_log.json")
    response_log = log["api_calls"][0]["response"]
    assert response_log["finish_reason"] == "stop"
    assert response_log["usage"]["completion_tokens"] == 3
    assert client.usage.total()["cache_read_input_tokens"] == 6
    assert client.usage.total()["cache_creation_input_tokens"] == 2


def test_openai_compatible_content_filter_is_terminal(tmp_path):
    logger = LLMCallLogger(tmp_path)
    logger.start_task("task_filtered")
    client = OpenAICompatibleClient(
        "secret", "model", call_logger=logger, agent="spec")
    response = SimpleNamespace(
        id="chat_filtered", model="model", usage=None,
        choices=[SimpleNamespace(
            finish_reason="content_filter",
            message=SimpleNamespace(content=None, refusal=None))],
    )

    class Completions:
        def create(self, **kwargs):
            return response

    client._client = SimpleNamespace(
        chat=SimpleNamespace(completions=Completions()))
    with pytest.raises(LLMRefusalError, match="refused the spec request"):
        client.complete("system", "user")

    log = _log(tmp_path / "task_filtered" / "spec_log.json")
    assert log["api_calls"][0]["response"]["finish_reason"] == "content_filter"
