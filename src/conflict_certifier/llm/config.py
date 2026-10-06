"""LLM configuration — provider, model, and credentials.

Provider names, API endpoints, and keys never appear in source code.
- Provider and model name live in the experiment YAML.
- API keys and base URLs live in .env as environment variables.
  The YAML stores only the *name* of the env var to read.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from conflict_certifier.config import ConfigError


@dataclass
class LLMConfig:
    provider: str          # "anthropic" | "openai_compatible"
    model: str
    api_key_env: str       # name of the env var holding the key
    api_base_env: str = "" # optional: name of env var holding custom base URL
    # None = no cap: openai_compatible omits the parameter entirely; anthropic's
    # API refuses requests without one, so the client falls back to 16000 there
    # (the largest value that is safe without streaming).
    max_tokens: int | None = None
    effort: str | None = None
    ssl_verify: bool = True  # set false for self-signed / corporate proxy certs

    @classmethod
    def from_dict(cls, d: dict) -> "LLMConfig":
        if "provider" not in d:
            raise ConfigError("llm config requires 'provider'")
        if "model" not in d:
            raise ConfigError("llm config requires 'model'")
        if "api_key_env" not in d:
            raise ConfigError("llm config requires 'api_key_env'")
        effort = d.get("effort") or None
        if effort not in (None, "low", "medium", "high", "xhigh", "max"):
            raise ConfigError(
                "effort must be one of: low, medium, high, xhigh, max")
        return cls(
            provider=d["provider"],
            model=d["model"],
            api_key_env=d["api_key_env"],
            api_base_env=d.get("api_base_env", ""),
            max_tokens=int(d["max_tokens"]) if d.get("max_tokens") is not None else None,
            effort=effort,
            ssl_verify=bool(d.get("ssl_verify", True)),
        )

    def build(self, *, call_logger=None, agent: str = "llm") -> "LLMClient":
        from conflict_certifier.llm.client import AnthropicClient, OpenAICompatibleClient

        key = os.environ.get(self.api_key_env, "")
        base = os.environ.get(self.api_base_env, "") if self.api_base_env else ""

        if self.provider == "anthropic":
            return AnthropicClient(
                api_key=key, model=self.model, max_tokens=self.max_tokens,
                effort=self.effort, call_logger=call_logger, agent=agent)
        if self.provider == "openai_compatible":
            return OpenAICompatibleClient(
                api_key=key, model=self.model,
                base_url=base or None, max_tokens=self.max_tokens,
                ssl_verify=self.ssl_verify, call_logger=call_logger, agent=agent,
            )
        raise ConfigError(
            f"Unknown LLM provider {self.provider!r}. "
            "Supported: 'anthropic', 'openai_compatible'."
        )


# Re-export so callers import only from llm.config
from conflict_certifier.llm.client import LLMClient  # noqa: E402, F401
