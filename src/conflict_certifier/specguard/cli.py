"""specguard 'task description' — no benchmark configuration required."""
from __future__ import annotations

import argparse
import os
import sys
import re
from datetime import datetime, timezone
from pathlib import Path

import yaml

from conflict_certifier.llm.config import LLMConfig

from .artifacts import GuardError
from .run import run_task
from .settings import DEFAULTS, execution_settings


def configuration(path: str | None, budget: float | None) -> dict:
    config_path = Path(path).expanduser() if path else Path.home() / ".config/specguard/config.yaml"
    raw = {}
    if path or config_path.exists():
        raw = yaml.safe_load(config_path.read_text()) or {}
        if not isinstance(raw, dict):
            raise GuardError("SpecGuard config must be a YAML mapping")
    # Task selection belongs to the image launcher, not the model-facing CLI.
    raw.pop("tasks", None)
    allowed = {"provider", "model", "api_key_env", "api_base_env", "max_tokens", "effort", "time_budget"} | DEFAULTS.keys()
    if set(raw) - allowed:
        raise GuardError("Unknown SpecGuard config keys: " + ", ".join(sorted(set(raw)-allowed)))
    model = raw.get("model") or os.environ.get("SPECGUARD_MODEL")
    if not isinstance(model, str) or not model.strip():
        raise GuardError("Set SPECGUARD_MODEL or model in ~/.config/specguard/config.yaml (or --config FILE)")
    provider = raw.get("provider") or ("anthropic" if model.startswith("claude-") and not os.environ.get("LLM_BASE_URL") else "openai_compatible")
    if raw.get("effort") and provider != "anthropic":
        raise GuardError("The shared openai_compatible client does not expose effort; omit it rather than silently ignoring it")
    router = "/" in model and provider == "openai_compatible"
    raw.update(model=model, provider=provider)
    raw.setdefault("api_key_env", "LLM_API_KEY" if os.environ.get("LLM_API_KEY") else
                   "ANTHROPIC_API_KEY" if provider == "anthropic" else
                   "OPENROUTER_API_KEY" if router else "OPENAI_API_KEY")
    if os.environ.get("LLM_BASE_URL"):
        raw.setdefault("api_base_env", "LLM_BASE_URL")
    elif router:
        raw.setdefault("api_base_env", "OPENROUTER_BASE_URL")
    if not isinstance(raw["api_key_env"], str) or not os.environ.get(raw["api_key_env"]):
        raise GuardError(f"Export the credential named by api_key_env ({raw['api_key_env']}); credentials are never passed to agent workspaces")
    if raw.get("api_base_env") and not os.environ.get(raw["api_base_env"]):
        raise GuardError(f"Export {raw['api_base_env']} with your backend URL")
    raw["time_budget"] = budget if budget is not None else raw.get("time_budget", 1800)
    if not isinstance(raw["time_budget"], (float, int)) or not 0 < raw["time_budget"] < float("inf"):
        raise GuardError("time_budget must be a positive finite number of seconds")
    LLMConfig.from_dict(raw)
    return execution_settings(raw)


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "setup":
        from .runtime import setup
        return setup(argv[1:])
    parser = argparse.ArgumentParser(prog="specguard", description="Check an intended Python task against relevant existing tests in the current repository.",
                                     epilog="Use 'specguard setup' to install Lean in Docker, or 'specguard setup --lean-root PATH' to select an existing toolchain.")
    parser.add_argument("task", help="Natural-language task description (quote it as one argument)")
    parser.add_argument("--config", help="Model backend YAML (default: ~/.config/specguard/config.yaml)")
    parser.add_argument("--time-budget", type=float, help="Overall deadline in seconds (default: 1800)")
    parser.add_argument("--task-id", help="Output task identifier (default: repository directory name)")
    args = parser.parse_args(argv)
    if not args.task.strip():
        parser.error("task must not be empty")
    try:
        config = configuration(args.config, args.time_budget)
    except (ValueError, OSError, RuntimeError) as exc:
        print(f"[specguard] {exc}", file=sys.stderr)
        print("inconclusive")
        return 2
    root = Path.cwd()
    task_id = args.task_id or root.name
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", task_id):
        parser.error("task-id must contain only letters, digits, underscores, dots or hyphens and start with a letter or digit")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    model_dir = re.sub(r"[^A-Za-z0-9_.-]", "_", config["model"])
    output = root / "output/specguard" / model_dir / stamp / task_id
    for parent in (root / "output", root / "output/specguard", output.parent.parent):
        if parent.is_symlink():
            print("[specguard] Output directories must not be symlinks", file=sys.stderr)
            print("inconclusive")
            return 2
    result = run_task(args.task, root, output, config)
    print(result["verdict"])
    print(f"[specguard] {result.get('reason') or result.get('evidence') or ''}\n[specguard] output: {output}", file=sys.stderr)
    return {"no-conflict": 0, "conflict": 1, "inconclusive": 2}[result["verdict"]]


if __name__ == "__main__":
    raise SystemExit(main())
