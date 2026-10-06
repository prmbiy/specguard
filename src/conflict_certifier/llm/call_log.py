"""Durable, task-local logging for LLM API calls."""

from __future__ import annotations

import hashlib
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic
from typing import Any


_LOG_NAMES = {
    "spec": "spec_log.json",
    "tests": "test_log.json",
    "connector": "connector_log.json",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if hasattr(value, "model_dump"):
        return _jsonable(value.model_dump())
    if hasattr(value, "__dict__"):
        return {
            key: _jsonable(item)
            for key, item in vars(value).items()
            if not key.startswith("_")
        }
    return str(value)


class LLMCallLogger:
    """Persist calls inside each task's existing agent log."""

    def __init__(self, tasks_root: Path):
        self.tasks_root = tasks_root
        self._local = threading.local()
        self._write_lock = threading.Lock()

    def start_task(self, task: str) -> None:
        self._local.state = {"task": task, "agents": {}}

    def calls(self, agent: str) -> list[dict]:
        state = getattr(self._local, "state", None)
        if state is None:
            return []
        return _jsonable(state["agents"].get(agent, {}).get("calls", []))

    def begin_call(
        self,
        *,
        agent: str,
        provider: str,
        model: str,
        system: str,
        messages: list[dict],
        parameters: dict,
    ) -> dict:
        state = getattr(self._local, "state", None)
        if state is None:
            raise RuntimeError("LLM call started without a task logging context")
        agent_state = state["agents"].setdefault(agent, {
            "calls": [], "conversation": [], "system_prompt": system,
        })
        entry = {
            "turn": len(agent_state["calls"]) + 1,
            "started_at": _now(),
            "provider": provider,
            "model": model,
            "request": {
                "system_prompt_sha256": hashlib.sha256(
                    system.encode("utf-8")).hexdigest(),
                "message_count": len(messages),
                "parameters": _jsonable(parameters),
            },
            "provider_attempts": [],
            "response": None,
        }
        agent_state["calls"].append(entry)
        agent_state["conversation"] = _jsonable(messages)
        agent_state["system_prompt"] = system
        self._persist(agent)
        return {
            "agent": agent,
            "entry": entry,
            "messages": _jsonable(messages),
            "started": monotonic(),
        }

    def response(self, handle: dict, *, provider_attempt: int, response: dict) -> None:
        response = _jsonable(response)
        text = response.pop("text", "")
        response["latency_seconds"] = round(monotonic() - handle["started"], 3)
        handle["entry"]["provider_attempts"].append({
            "attempt": provider_attempt,
            "outcome": "response",
        })
        handle["entry"]["response"] = response
        state = self._local.state["agents"][handle["agent"]]
        state["conversation"] = handle["messages"] + [
            {"role": "assistant", "content": text}
        ]
        self._persist(handle["agent"])

    def error(self, handle: dict, *, provider_attempt: int, error: dict) -> None:
        handle["entry"]["provider_attempts"].append({
            "attempt": provider_attempt,
            "outcome": "error",
            "latency_seconds": round(monotonic() - handle["started"], 3),
            "error": _jsonable(error),
        })
        self._persist(handle["agent"])

    def _persist(self, agent: str) -> None:
        state = self._local.state
        agent_state = state["agents"][agent]
        filename = _LOG_NAMES.get(agent, f"{agent}_log.json")
        path = self.tasks_root / state["task"] / filename
        value = {
            "in_progress": True,
            "system_prompt": agent_state["system_prompt"],
            "conversation": agent_state["conversation"],
            "api_calls": agent_state["calls"],
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(
            f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        data = json.dumps(value, indent=2, ensure_ascii=False)
        with self._write_lock:
            with temporary.open("w", encoding="utf-8") as output:
                output.write(data)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, path)


def jsonable(value: Any) -> Any:
    """Normalize provider SDK response objects for JSON logging."""
    return _jsonable(value)
