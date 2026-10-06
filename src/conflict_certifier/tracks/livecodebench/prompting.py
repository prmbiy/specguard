"""Reviewable prompt loading and exact prompt audit records."""

from __future__ import annotations

import hashlib
from pathlib import Path


PROMPT_DIR = Path(__file__).with_name("prompts")


def load_prompt(name: str) -> str:
    return (PROMPT_DIR / name).read_text(encoding="utf-8").strip()


def prompt_audit(system_prompt: str, conversation: list[dict]) -> dict:
    initial = conversation[0]["content"] if conversation else ""
    return {
        "system_prompt": system_prompt,
        "system_prompt_sha256": hashlib.sha256(system_prompt.encode()).hexdigest(),
        "initial_prompt_sha256": hashlib.sha256(initial.encode()).hexdigest(),
        "template_sha256": {
            name: hashlib.sha256(load_prompt(name).encode()).hexdigest()
            for name in ("spec_retry.txt", "spec_system.txt")
        },
    }
