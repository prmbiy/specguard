"""Load and audit the human-reviewable SWE-bench prompt templates."""

from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path


PROMPT_DIR = Path(__file__).with_name("prompts")


@lru_cache(maxsize=None)
def load_prompt(filename: str) -> str:
    return (PROMPT_DIR / filename).read_text(encoding="utf-8")


def render_prompt(filename: str, **values: str) -> str:
    # Text files conventionally end in a newline; runtime retry messages did not.
    return load_prompt(filename).removesuffix("\n").format(**values)


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def prompt_audit(agent: str, system_prompt: str, conversation: list[dict]) -> dict:
    """Return the exact system prompt and hashes for every template in a stage."""
    templates = {
        path.name: sha256_text(path.read_text(encoding="utf-8"))
        for path in sorted(PROMPT_DIR.glob(f"{agent}_*.txt"))
    }
    initial = conversation[0]["content"] if conversation else ""
    return {
        "system_prompt": system_prompt,
        "system_prompt_sha256": sha256_text(system_prompt),
        "initial_prompt_sha256": sha256_text(initial),
        "template_sha256": templates,
    }
