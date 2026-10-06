"""LLM client implementations.

All clients expose a single method: complete(system, user) → str.
Provider-specific SDK calls are isolated here; the rest of the codebase
only touches LLMClient and never imports from anthropic/openai directly.

Token usage is accumulated on the client (see UsageTracker). One client is
shared across worker threads, so the tracker keeps both a process-wide total
and a per-thread tally — tasks run one-per-thread, so the thread tally is how
a stage attributes tokens to the task it just ran.
"""

from __future__ import annotations

import threading
import re
from abc import ABC, abstractmethod

from conflict_certifier.llm.call_log import LLMCallLogger, jsonable

_USAGE_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
)


class LLMRefusalError(RuntimeError):
    """The provider declined to answer; repeating the same request cannot help."""


class UsageTracker:
    """Thread-safe token accounting: process-wide total + per-thread tally."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._total = {f: 0 for f in _USAGE_FIELDS}
        self._total["calls"] = 0
        self._local = threading.local()

    def record(self, usage) -> None:
        """Add one response's usage. Accepts an SDK object or a plain dict."""
        get = usage.get if isinstance(usage, dict) else lambda k, d=0: getattr(usage, k, d)
        counts = {f: int(get(f, 0) or 0) for f in _USAGE_FIELDS}
        counts["calls"] = 1
        with self._lock:
            for k, v in counts.items():
                self._total[k] += v
        local = getattr(self._local, "counts", None)
        if local is not None:
            for k, v in counts.items():
                local[k] = local.get(k, 0) + v

    def start_local(self) -> None:
        """Begin a fresh per-thread tally. Threads are reused across tasks, so
        this must be called at the start of each task rather than relying on a
        thread being new."""
        self._local.counts = {f: 0 for f in _USAGE_FIELDS}
        self._local.counts["calls"] = 0

    def take_local(self) -> dict:
        """Return the current thread's tally and stop tallying."""
        counts = getattr(self._local, "counts", None)
        self._local.counts = None
        return counts or {f: 0 for f in (*_USAGE_FIELDS, "calls")}

    def total(self) -> dict:
        with self._lock:
            return dict(self._total)


class LLMClient(ABC):
    def __init__(self, *, call_logger: LLMCallLogger | None = None,
                 agent: str = "llm") -> None:
        self.usage = UsageTracker()
        self._call_logger = call_logger
        self._agent = agent

    def _begin_log(self, *, provider: str, model: str, system: str,
                   messages: list[dict], parameters: dict) -> dict | None:
        if self._call_logger is None:
            return None
        return self._call_logger.begin_call(
            agent=self._agent, provider=provider, model=model, system=system,
            messages=messages, parameters=parameters)

    def _log_response(self, handle: dict | None, attempt: int, response: dict) -> None:
        if self._call_logger is not None and handle is not None:
            self._call_logger.response(
                handle, provider_attempt=attempt, response=response)

    def _log_error(self, handle: dict | None, attempt: int, exc: Exception,
                   api_key: str) -> None:
        if self._call_logger is None or handle is None:
            return
        message = str(exc).replace(api_key, "[REDACTED]") if api_key else str(exc)
        message = re.sub(r"\b(?:sk|or)-[A-Za-z0-9_-]+", "[REDACTED]", message)
        self._call_logger.error(handle, provider_attempt=attempt, error={
            "type": type(exc).__name__,
            "status_code": getattr(exc, "status_code", None)
                           or getattr(exc, "code", None),
            "message": message[:4000],
        })

    @abstractmethod
    def complete(self, system: str, user: str) -> str:
        """Send a single system+user turn and return the assistant text."""

    @abstractmethod
    def complete_conversation(self, system: str, messages: list[dict]) -> str:
        """Send a multi-turn conversation (list of {role, content} dicts)."""


class AnthropicClient(LLMClient):
    """Anthropic Messages client. Uses the `anthropic` SDK if installed, else falls
    back to a dependency-free stdlib (`urllib`) call to the public Messages API."""

    _API_URL = "https://api.anthropic.com/v1/messages"
    _API_VERSION = "2023-06-01"

    def __init__(self, api_key: str, model: str, max_tokens: int | None = None,
                 thinking_budget_tokens: int = 0, effort: str | None = None,
                 *, call_logger: LLMCallLogger | None = None,
                 agent: str = "llm"):
        super().__init__(call_logger=call_logger, agent=agent)
        self._api_key = api_key
        self._model = model
        self._max_tokens = max_tokens
        self._thinking_budget = thinking_budget_tokens
        self._effort = effort
        try:
            import anthropic  # lazy; optional
            self._client = anthropic.Anthropic(api_key=api_key)
        except ImportError:
            self._client = None  # use urllib fallback

    def complete(self, system: str, user: str) -> str:
        return self.complete_conversation(system, [{"role": "user", "content": user}])

    def complete_conversation(self, system: str, messages: list[dict]) -> str:
        kwargs = dict(
            model=self._model,
            # The Messages API requires an explicit cap. When the config sets
            # none, use 16000 — the largest value safe without streaming.
            max_tokens=self._max_tokens or 16000,
            system=system,
            messages=messages,
            # Anthropic caching is opt-in. Automatic caching advances the
            # breakpoint as a retry conversation grows, which is exactly the
            # access pattern used by all three agents.
            cache_control={"type": "ephemeral"},
        )
        if self._thinking_budget > 0:
            kwargs["thinking"] = {"type": "enabled", "budget_tokens": self._thinking_budget}
        if self._effort:
            kwargs["output_config"] = {"effort": self._effort}

        handle = self._begin_log(
            provider="anthropic", model=self._model, system=system,
            messages=messages,
            parameters={
                "endpoint": self._API_URL,
                **{key: value for key, value in kwargs.items()
                   if key not in ("system", "messages")},
            },
        )

        # transient API failures (429 rate limit, 5xx, 529 overloaded) are NORMAL at
        # high concurrency — retry with exponential backoff instead of killing the run
        import time
        import urllib.error
        last_exc: Exception | None = None
        for attempt in range(6):
            try:
                if self._client is not None:
                    response = self._client.messages.create(**kwargs)
                    if getattr(response, "usage", None) is not None:
                        self.usage.record(response.usage)
                    text_blocks = [b.text for b in response.content if hasattr(b, "text")]
                    text = "\n".join(text_blocks)
                    self._log_response(handle, attempt + 1, {
                        "id": getattr(response, "id", None),
                        "model": getattr(response, "model", None),
                        "text": text,
                        "stop_reason": getattr(response, "stop_reason", None),
                        "stop_sequence": getattr(response, "stop_sequence", None),
                        "content_block_types": [
                            getattr(block, "type", type(block).__name__)
                            for block in getattr(response, "content", [])
                        ],
                        "usage": jsonable(getattr(response, "usage", None)),
                    })
                    if getattr(response, "stop_reason", None) == "refusal":
                        raise LLMRefusalError(
                            f"{self._model} refused the {self._agent} request")
                    return text
                text, body = self._complete_via_urllib(kwargs)
                self._log_response(handle, attempt + 1, {
                    "id": body.get("id"),
                    "model": body.get("model"),
                    "text": text,
                    "stop_reason": body.get("stop_reason"),
                    "stop_sequence": body.get("stop_sequence"),
                    "content_block_types": [
                        block.get("type") for block in body.get("content", [])
                    ],
                    "usage": body.get("usage"),
                })
                if body.get("stop_reason") == "refusal":
                    raise LLMRefusalError(
                        f"{self._model} refused the {self._agent} request")
                return text
            except LLMRefusalError:
                raise
            except urllib.error.HTTPError as e:
                self._log_error(handle, attempt + 1, e, self._api_key)
                if e.code not in (408, 429, 500, 502, 503, 504, 529):
                    raise
                last_exc = e
            except Exception as e:  # SDK errors: retry the same transient statuses
                self._log_error(handle, attempt + 1, e, self._api_key)
                code = getattr(e, "status_code", None)
                if code not in (408, 429, 500, 502, 503, 504, 529) and \
                        type(e).__name__ not in ("APIConnectionError", "APITimeoutError",
                                                 "OverloadedError", "InternalServerError",
                                                 "RateLimitError"):
                    raise
                last_exc = e
            if attempt < 5:
                time.sleep(min(120, 5 * 2 ** attempt))  # 5s..80s, capped
        raise last_exc  # type: ignore[misc]

    def _complete_via_urllib(self, payload: dict) -> tuple[str, dict]:
        import json
        import urllib.request

        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self._API_URL, data=data, method="POST",
            headers={
                "x-api-key": self._api_key,
                "anthropic-version": self._API_VERSION,
                "content-type": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=600) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        if isinstance(body.get("usage"), dict):
            self.usage.record(body["usage"])
        # content is a list of blocks; keep text blocks only (mirrors SDK path)
        text = "\n".join(b.get("text", "") for b in body.get("content", [])
                         if b.get("type") == "text")
        return text, body


class OpenAICompatibleClient(LLMClient):
    def __init__(self, api_key: str, model: str,
                 base_url: str | None = None, max_tokens: int | None = None,
                 ssl_verify: bool = True, *,
                 call_logger: LLMCallLogger | None = None,
                 agent: str = "llm"):
        super().__init__(call_logger=call_logger, agent=agent)
        import httpx
        import openai  # lazy
        self._api_key = api_key
        self._client = openai.OpenAI(
            api_key=api_key,
            base_url=base_url,
            http_client=httpx.Client(verify=ssl_verify),
        )
        self._model = model
        self._max_tokens = max_tokens
        self._base_url = base_url or "https://api.openai.com/v1"

    def _create(self, kwargs: dict, handle: dict | None):
        """One chat.completions request with the shared retry policy; records usage."""
        # Transient failures (429/5xx, gateway hiccups returning non-JSON
        # bodies, dropped connections) are normal at high concurrency —
        # retry with backoff instead of killing the task, mirroring the
        # Anthropic client's behavior.
        import time
        last_exc: Exception | None = None
        for attempt in range(6):
            try:
                response = self._client.chat.completions.create(**kwargs)
                break
            except Exception as e:
                self._log_error(handle, attempt + 1, e, self._api_key)
                # Newer OpenAI models reject max_tokens in favor of
                # max_completion_tokens; rename once and retry immediately.
                if "max_tokens" in kwargs and "max_completion_tokens" in str(e):
                    kwargs["max_completion_tokens"] = kwargs.pop("max_tokens")
                    continue
                code = getattr(e, "status_code", None)
                retryable = code in (408, 429, 500, 502, 503, 504) or type(e).__name__ in (
                    "APIConnectionError", "APITimeoutError", "InternalServerError",
                    "RateLimitError", "APIResponseValidationError", "JSONDecodeError",
                )
                if not retryable:
                    raise
                last_exc = e
            if attempt < 5:
                time.sleep(min(120, 5 * 2 ** attempt))  # 5s..80s, capped
        else:
            raise last_exc  # type: ignore[misc]
        u = getattr(response, "usage", None)
        if u is not None:  # OpenAI names these differently than Anthropic
            details = getattr(u, "prompt_tokens_details", None)
            self.usage.record({
                "input_tokens": getattr(u, "prompt_tokens", 0) or 0,
                "output_tokens": getattr(u, "completion_tokens", 0) or 0,
                "cache_read_input_tokens": getattr(details, "cached_tokens", 0) or 0,
                "cache_creation_input_tokens": getattr(details, "cache_write_tokens", 0) or 0,
            })
        return response, attempt, u

    def complete_tools(self, system: str, messages: list[dict], tools: list[dict]) -> dict:
        """One turn with native tool calling; exactly one call per turn is requested from
        the provider (parallel_tool_calls=False, tool_choice=required).

        Returns {"content": str, "tool_calls": [{"id", "type", "function": {"name",
        "arguments"}}]} — the assistant message in OpenAI wire format, so it can be
        appended to `messages` as-is. Messages may include role=tool entries.
        """
        all_msgs = [{"role": "system", "content": system}] + messages
        kwargs = dict(model=self._model, messages=all_msgs, tools=tools,
                      tool_choice="required", parallel_tool_calls=False)
        if self._max_tokens is not None:
            kwargs["max_tokens"] = self._max_tokens
        handle = self._begin_log(
            provider="openai_compatible", model=self._model, system=system,
            messages=messages,
            parameters={"endpoint": self._base_url,
                        **{k: v for k, v in kwargs.items() if k not in ("messages", "tools")},
                        "tools": [t["function"]["name"] for t in tools]},
        )
        response, attempt, u = self._create(kwargs, handle)
        choice = response.choices[0]
        message = choice.message
        calls = [{"id": c.id, "type": "function",
                  "function": {"name": c.function.name, "arguments": c.function.arguments or "{}"}}
                 for c in (message.tool_calls or [])]
        text = message.content or ""
        refusal = getattr(message, "refusal", None)
        finish_reason = getattr(choice, "finish_reason", None)
        self._log_response(handle, attempt + 1, {
            "id": getattr(response, "id", None),
            "model": getattr(response, "model", None),
            "text": text,
            "tool_calls": calls,
            "finish_reason": finish_reason,
            "refusal": refusal,
            "content_block_types": (["text"] if text else []) + ["tool_use"] * len(calls),
            "usage": jsonable(u),
        })
        if refusal or finish_reason in {"content_filter", "refusal", "safety"}:
            raise LLMRefusalError(
                f"{self._model} refused the {self._agent} request")
        return {"role": "assistant", "content": text, "tool_calls": calls}

    def complete(self, system: str, user: str) -> str:
        return self.complete_conversation(system, [{"role": "user", "content": user}])

    def complete_conversation(self, system: str, messages: list[dict]) -> str:
        all_msgs = [{"role": "system", "content": system}] + messages
        kwargs = dict(model=self._model, messages=all_msgs)
        if self._max_tokens is not None:  # no config cap = don't send one
            kwargs["max_tokens"] = self._max_tokens
        handle = self._begin_log(
            provider="openai_compatible", model=self._model, system=system,
            messages=messages,
            parameters={
                "endpoint": self._base_url,
                **{key: value for key, value in kwargs.items()
                   if key != "messages"},
            },
        )

        response, attempt, u = self._create(kwargs, handle)
        text = response.choices[0].message.content or ""
        choice = response.choices[0]
        refusal = getattr(choice.message, "refusal", None)
        finish_reason = getattr(choice, "finish_reason", None)
        self._log_response(handle, attempt + 1, {
            "id": getattr(response, "id", None),
            "model": getattr(response, "model", None),
            "text": text,
            "finish_reason": finish_reason,
            "refusal": refusal,
            "content_block_types": ["text"] if text else [],
            "usage": jsonable(u),
        })
        if refusal or finish_reason in {"content_filter", "refusal", "safety"}:
            raise LLMRefusalError(
                f"{self._model} refused the {self._agent} request")
        # Reasoning models can spend the whole max_tokens budget thinking and
        # come back with content=None. Return "" instead — the agent loop
        # treats that as "no code block" and retries with feedback, rather
        # than crashing the task on a regex over None.
        return text
