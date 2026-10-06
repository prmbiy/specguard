"""Clean LiveCodeBench pipeline: Lean SpecAgent, mechanical tests, generic eval."""

from __future__ import annotations

import collections
import contextlib
import hashlib
import json
import os
import threading
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

from tqdm import tqdm

from conflict_certifier.config import ConfigError, PROJECT_ROOT
from conflict_certifier.evaluation import (
    SKIPPED,
    evaluate_predictions,
    incomplete_result,
    result_record,
)
from conflict_certifier.lean.pool import run_many
from conflict_certifier.lean.runner import LeanRunner
from conflict_certifier.llm.call_log import LLMCallLogger
from conflict_certifier.llm.client import LLMRefusalError
from conflict_certifier.reporting import record_progress, write_run_report
from conflict_certifier.tracks.livecodebench.artifacts import assemble_spec
from conflict_certifier.tracks.livecodebench.certifier import LiveCodeBenchCertifier
from conflict_certifier.tracks.livecodebench.config import LiveCodeBenchConfig
from conflict_certifier.tracks.livecodebench.prompting import prompt_audit
from conflict_certifier.tracks.livecodebench.source import (
    DataIntegrityError,
    load_tasks,
    prepare_task,
)
from conflict_certifier.tracks.livecodebench.spec_agent import LiveCodeBenchSpecAgent


def _write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _billing_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(marker in text for marker in (
        "insufficient credits", "usage limit", "usage limits", "key limit exceeded",
        "billing", "payment required", "credit balance",
    ))


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_dotenv() -> None:
    path = PROJECT_ROOT / ".env"
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _clean_retry_dir(path: Path) -> None:
    for name in (
        "cert.lean", "error.txt", "evaluation_log.json", "result.json", "spec.lean",
        "spec_log.json", "test_order.json",
    ):
        target = path / name
        if target.is_file():
            target.unlink()


class LiveCodeBenchRun:
    def __init__(self, config: LiveCodeBenchConfig):
        self.config = config

    def run(self, *, resume: Path | None = None, retry_errors: bool = False) -> dict:
        cfg = self.config
        rows = load_tasks(cfg.dataset)
        if cfg.limit:
            rows = rows[:cfg.limit]
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        out = Path(resume).resolve() if resume else cfg.run_dir(stamp)
        dataset_sha256 = _file_sha256(cfg.dataset)
        if resume:
            original = out / "config.json"
            if not original.is_file():
                raise ConfigError(f"resume directory has no config.json: {out}")
            saved = json.loads(original.read_text(encoding="utf-8"))
            expected = {
                "dataset": str(cfg.dataset), "model": cfg.llm.model,
                "provider": cfg.llm.provider,
                "test_order_seed": cfg.test_order_seed,
                "dataset_sha256": dataset_sha256,
            }
            mismatches = [key for key, value in expected.items()
                          if saved.get(key) != value]
            if mismatches:
                raise ConfigError(
                    "resume configuration differs for: " + ", ".join(mismatches))
        tasks_dir = out / "tasks"
        tasks_dir.mkdir(parents=True, exist_ok=True)
        _write_json(out / ("config.resume.json" if resume else "config.json"), {
            "name": "livecodebench",
            "split": cfg.split,
            "started_at": _now(),
            "dataset": str(cfg.dataset),
            "dataset_sha256": dataset_sha256,
            "model": cfg.llm.model,
            "provider": cfg.llm.provider,
            "effort": cfg.llm.effort,
            "max_tokens": cfg.llm.max_tokens,
            "spec_submissions": cfg.spec_submissions,
            "test_order_seed": cfg.test_order_seed,
            "workers": cfg.lean_env.workers,
            "repl_processes": cfg.lean_env.repl_processes,
            "limit": cfg.limit,
        })

        pending = []
        for row in rows:
            result_path = tasks_dir / row["task_id"] / "result.json"
            if not result_path.exists():
                pending.append(row)
                continue
            saved = json.loads(result_path.read_text(encoding="utf-8"))
            if retry_errors and saved.get("status", saved.get("cert")) == SKIPPED:
                _clean_retry_dir(result_path.parent)
                pending.append(row)

        if not pending:
            return self._finalize(out, rows, [])

        _load_dotenv()
        if not os.environ.get(cfg.llm.api_key_env):
            raise ConfigError(
                f"{cfg.llm.api_key_env} is not set in the environment or .env")
        if cfg.llm.api_base_env and not os.environ.get(cfg.llm.api_base_env):
            raise ConfigError(
                f"{cfg.llm.api_base_env} is not set in the environment or .env")

        call_log = LLMCallLogger(tasks_dir)
        llm = cfg.llm.build(call_logger=call_log, agent="spec")
        runner = LeanRunner(cfg.lean_env)
        agent = LiveCodeBenchSpecAgent(
            llm, runner, max_submissions=cfg.spec_submissions)
        certifier = LiveCodeBenchCertifier(runner)
        stop_billing = threading.Event()
        counts: collections.Counter = collections.Counter()
        results: list[dict] = []
        progress = tqdm(total=len(pending), desc="tasks", unit="task", dynamic_ncols=True)

        def one(row: dict) -> dict:
            task_id = row["task_id"]
            call_log.start_task(task_id)
            task_out = tasks_dir / task_id
            task_out.mkdir(parents=True, exist_ok=True)
            started = time.time()
            llm.usage.start_local()
            result = incomplete_result(
                "task_id", task_id, note="error", skipped=True)
            try:
                if stop_billing.is_set():
                    result = incomplete_result(
                        "task_id", task_id, note="billing_limit",
                        reason="provider billing limit reached earlier in this run",
                        skipped=True)
                    return result
                task, order = prepare_task(row, order_seed=cfg.test_order_seed)
                _write_json(task_out / "input.json", row)
                _write_json(task_out / "test_order.json", {
                    "seed": cfg.test_order_seed,
                    "good_test": order.good_test_number,
                    "bad_test": order.bad_test_number,
                })
                spec = agent.run(task, label=f"{task_id}_spec")
                if spec.body:
                    (task_out / "spec.lean").write_text(
                        assemble_spec(task, spec.body), encoding="utf-8")
                _write_json(task_out / "spec_log.json", {
                    "compile_success": spec.compile_success,
                    "submissions": spec.submissions,
                    "error": spec.error,
                    "compile_output": spec.compile_output[-8000:],
                    "conversation": spec.conversation,
                    "api_calls": call_log.calls("spec"),
                    "prompts": prompt_audit(agent.system_prompt, spec.conversation),
                })
                if not spec.compile_success:
                    result = incomplete_result(
                        "task_id", task_id, note="spec_agent_failure",
                        reason=spec.error)
                    return result

                certification = certifier.certify(
                    task, spec.body, label=f"{task_id}_evaluation")
                decision = evaluate_predictions(
                    certification.evidence, order.ground_truth)
                (task_out / "cert.lean").write_text(
                    certification.source, encoding="utf-8")
                _write_json(task_out / "evaluation_log.json", {
                    "predictions": decision.predictions,
                    "ground_truth": decision.ground_truth,
                    "evidence": certification.evidence.kind.value,
                    "status": decision.status,
                    "outcome": decision.outcome,
                    "exact_match": decision.exact_match,
                    "note": decision.note,
                    "reason": decision.reason,
                    "compile_output": certification.compile_output[-8000:],
                })
                result = result_record("task_id", task_id, decision)
                return result
            except DataIntegrityError as exc:
                result = incomplete_result(
                    "task_id", task_id, note="invalid_task", reason=str(exc))
                return result
            except LLMRefusalError as exc:
                result = incomplete_result(
                    "task_id", task_id, note="provider_refusal", reason=str(exc))
                return result
            except Exception as exc:
                if _billing_error(exc):
                    stop_billing.set()
                    note = "billing_limit"
                else:
                    note = "error"
                result = incomplete_result(
                    "task_id", task_id, note=note,
                    reason=f"{type(exc).__name__}: {exc}", skipped=True)
                (task_out / "error.txt").write_text(
                    traceback.format_exc(), encoding="utf-8")
                return result
            finally:
                result["elapsed_s"] = round(time.time() - started, 2)
                result["tokens"] = llm.usage.take_local()
                _write_json(task_out / "result.json", result)
                progress.set_postfix(record_progress(counts, result), refresh=False)
                progress.update(1)

        log_path = out / "run.log"
        try:
            with open(log_path, "a" if resume else "w", encoding="utf-8", buffering=1) as log, \
                    contextlib.redirect_stdout(log):
                if pending:
                    results = run_many(pending, one, workers=cfg.lean_env.workers)
        finally:
            progress.close()
            runner.close()
        _write_json(out / "usage.json", llm.usage.total())
        return self._finalize(out, rows, results)

    def _finalize(self, out: Path, rows: list[dict], current: list[dict]) -> dict:
        del current
        results = []
        for row in rows:
            path = out / "tasks" / row["task_id"] / "result.json"
            if path.exists():
                results.append(json.loads(path.read_text(encoding="utf-8")))
            else:
                results.append(incomplete_result(
                    "task_id", row["task_id"], note="not_reached",
                    reason="run ended before task started", skipped=True))
        return write_run_report(out, results, identifier_key="task_id", metadata={
            "name": "livecodebench",
            "split": self.config.split,
            "model": self.config.llm.model,
            "finished_at": _now(),
        })
