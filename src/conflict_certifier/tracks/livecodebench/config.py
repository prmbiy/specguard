"""Small, flat configuration for the LiveCodeBench pipeline."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from conflict_certifier.config import ConfigError, LeanEnv, PROJECT_ROOT
from conflict_certifier.llm.config import LLMConfig


_DATASETS = {
    "conflicting": PROJECT_ROOT / "data/livecodebench/sanitized/conflicting.json",
    "oneoff": PROJECT_ROOT / "data/livecodebench/dataset/full_oneoff.json",
}
_ALLOWED = {
    "provider", "model", "api_key_env", "api_base_env", "max_tokens", "effort",
    "ssl_verify", "split", "limit", "spec_submissions", "test_order_seed",
    "workers", "repl_processes", "lean_timeout", "lean_project", "repl_bin",
}


@dataclass(frozen=True)
class LiveCodeBenchConfig:
    split: str
    dataset: Path
    lean_env: LeanEnv
    llm: LLMConfig
    spec_submissions: int
    test_order_seed: int
    limit: int

    @classmethod
    def load(cls, path: str | Path, *, model: str | None = None,
             limit: int | None = None) -> "LiveCodeBenchConfig":
        path = Path(path).expanduser().resolve()
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        unknown = sorted(set(raw) - _ALLOWED)
        if unknown:
            raise ConfigError(f"unknown LiveCodeBench config keys: {unknown}")

        split = str(raw.get("split", "conflicting"))
        if split not in _DATASETS:
            raise ConfigError(
                f"split must be one of {sorted(_DATASETS)}, received {split!r}")
        dataset = _DATASETS[split].resolve()
        if not dataset.is_file():
            raise ConfigError(f"normalized {split} dataset is missing: {dataset}")

        llm_raw = dict(raw)
        if model:
            llm_raw["model"] = model
        llm = LLMConfig.from_dict(llm_raw)

        submissions = int(raw.get("spec_submissions", 10))
        if submissions < 1:
            raise ConfigError("spec_submissions must be at least 1")
        resolved_limit = int(raw.get("limit", 0) if limit is None else limit)
        if resolved_limit < 0:
            raise ConfigError("limit cannot be negative")

        lean_env = LeanEnv.from_dict({
            "project": raw.get("lean_project", os.environ.get(
                "LEAN_PROJECT", "/mnt/data/lean/workspace")),
            "repl_bin": raw.get("repl_bin", os.environ.get(
                "REPL_BIN", "/mnt/data/lean/repl/.lake/build/bin/repl")),
            "timeout": raw.get("lean_timeout", 120),
            "workers": raw.get("workers", 8),
            "repl_processes": raw.get("repl_processes", 2),
        })
        lean_env.validate()
        return cls(
            split=split,
            dataset=dataset,
            lean_env=lean_env,
            llm=llm,
            spec_submissions=submissions,
            test_order_seed=int(raw.get("test_order_seed", 0)),
            limit=resolved_limit,
        )

    def run_dir(self, stamp: str) -> Path:
        model = re.sub(r"[^A-Za-z0-9._-]+", "_", self.llm.model)
        return (PROJECT_ROOT / "output/livecodebench" / model / stamp).resolve()
