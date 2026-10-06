"""Load and audit reviewable SWE-bench Python prompt templates."""

from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path


PROMPT_DIR = Path(__file__).with_name("prompts")


@lru_cache(maxsize=None)
def load_prompt(filename: str) -> str:
    return (PROMPT_DIR / filename).read_text(encoding="utf-8")


def render_prompt(filename: str, **values: str) -> str:
    return load_prompt(filename).removesuffix("\n").format(**values)


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def prompt_audit(agent: str, system_prompt: str, conversation: list[dict]) -> dict:
    templates = {
        path.name: _sha(path.read_text(encoding="utf-8"))
        for path in sorted(PROMPT_DIR.glob(f"{agent}_*.txt"))
    }
    initial = conversation[0]["content"] if conversation else ""
    return {
        "system_prompt": system_prompt,
        "system_prompt_sha256": _sha(system_prompt),
        "initial_prompt_sha256": _sha(initial),
        "template_sha256": templates,
    }
