#!/usr/bin/env python3
"""SWE-bench Python agent pipeline with task-image-only artifact execution."""

from __future__ import annotations

import argparse
import collections
import contextlib
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from tqdm import tqdm

from conflict_certifier.evaluation import (
    SKIPPED, evaluate_predictions,
    incomplete_result, result_record,
)
from conflict_certifier.reporting import record_progress, write_run_report
from conflict_certifier.llm.config import LLMConfig
from conflict_certifier.llm.call_log import LLMCallLogger
from conflict_certifier.llm.client import LLMRefusalError
from conflict_certifier.tracks.swebench.source import (
    InputUnavailableError, MissingImageError, load_test_inputs, prepare_agent_case,
)
from conflict_certifier.tracks.swebench_py.certifier import PythonCertifier
from conflict_certifier.tracks.swebench_py.connector_agent import SwePythonConnectorAgent
from conflict_certifier.tracks.swebench_py.prompting import prompt_audit
from conflict_certifier.tracks.swebench_py.pyrunner import TaskPythonRunner
from conflict_certifier.tracks.swebench_py.spec_agent import SwePythonSpecAgent
from conflict_certifier.tracks.swebench_py.test_agent import SwePythonTestAgent


REPO = Path(__file__).resolve().parents[4]
SOURCE_DIR = REPO / "data" / "swebench" / "_source"
DEFAULT_MODEL = "claude-opus-4-8"


def _write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")


def load_rows(split: str) -> dict[str, dict]:
    path = SOURCE_DIR / f"impossible_swebench_{split}.jsonl"
    if not path.exists():
        raise FileNotFoundError(f"split not found: {path}")
    rows: dict[str, dict] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            rows[row["instance_id"]] = row
    return rows


def select_instance_ids(rows: dict[str, dict], *, run_all: bool,
                        explicit_ids: list[str] | None) -> list[str]:
    if explicit_ids:
        return list(explicit_ids)
    return sorted(rows) if run_all else []


def llm_cfg(model: str, provider: str, api_key_env: str,
            api_base_env: str, max_tokens: int | None,
            effort: str | None = None) -> dict:
    if not api_key_env:
        api_key_env = "ANTHROPIC_API_KEY" if provider == "anthropic" else "OPENAI_API_KEY"
    return {
        "provider": provider,
        "model": model,
        "api_key_env": api_key_env,
        "api_base_env": api_base_env,
        "max_tokens": max_tokens,
        "effort": effort,
    }


def _load_dotenv() -> None:
    path = REPO / ".env"
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def process_one(
    row: dict,
    *,
    spec_llm,
    test_llm,
    connector_llm,
    runner: TaskPythonRunner,
    out_root: Path,
    call_log: LLMCallLogger,
    input_records: dict[str, dict],
    order_seed: int,
    spec_turns: int,
    spec_submissions: int,
    test_submissions: int,
    connector_submissions: int,
    exec_timeout: int,
) -> dict:
    instance_id = row["instance_id"]
    out = out_root / instance_id
    out.mkdir(parents=True, exist_ok=True)
    try:
        case, order = prepare_agent_case(
            row, input_records.get(instance_id), order_seed=order_seed)
    except InputUnavailableError as exc:
        result = incomplete_result(
            "instance_id", instance_id, note="input_unavailable", reason=str(exc))
        _write_json(out / "result.json", result)
        return result

    (out / "input_context.txt").write_text(
        "## Inputs\n" + "\n".join(
            f"input_{number}: {value}" for number, value in enumerate(case.inputs))
        + "\n\n## Context\n" + case.input_context + "\n",
        encoding="utf-8")
    _write_json(out / "test_order.json", {
        "seed": order_seed,
        "good_test": order.good_test_number,
        "bad_test": order.bad_test_number,
    })

    spec_agent = SwePythonSpecAgent(
        spec_llm, runner, max_turns=spec_turns,
        max_submissions=spec_submissions, exec_timeout=exec_timeout)
    test_agent = SwePythonTestAgent(
        test_llm, runner, max_submissions=test_submissions)
    connector_agent = SwePythonConnectorAgent(
        connector_llm, runner, max_submissions=connector_submissions)

    spec = spec_agent.run(
        instance_id, case.description, case.repo, case.inputs, case.input_context)
    if spec.code:
        (out / "spec.py").write_text(spec.code + "\n", encoding="utf-8")
    _write_json(out / "spec_log.json", {
        "success": spec.success,
        "attempts": spec.attempts_used,
        "error": spec.error,
        "execution_output": spec.execution_output[-4000:],
        "conversation": spec.conversation,
        "api_calls": call_log.calls("spec"),
        "prompts": prompt_audit("spec", spec_agent.system_prompt, spec.conversation),
    })
    if not spec.success:
        note = "error" if spec.infrastructure_error else "spec_agent_failure"
        result = incomplete_result(
            "instance_id", instance_id, note=note, reason=spec.error,
            skipped=spec.infrastructure_error, spec_attempts=spec.attempts_used)
        _write_json(out / "result.json", result)
        return result

    tests = test_agent.run(
        case.inputs, case.input_context, case.test_0_patch, case.test_1_patch)
    if tests.code:
        (out / "tests.py").write_text(tests.code + "\n", encoding="utf-8")
    _write_json(out / "test_log.json", {
        "success": tests.success,
        "attempts": tests.attempts_used,
        "error": tests.error,
        "execution_output": tests.execution_output[-4000:],
        "conversation": tests.conversation,
        "api_calls": call_log.calls("tests"),
        "prompts": prompt_audit("test", test_agent.system_prompt, tests.conversation),
    })
    if not tests.success:
        note = "error" if tests.infrastructure_error else "test_agent_failure"
        result = incomplete_result(
            "instance_id", instance_id, note=note, reason=tests.error,
            skipped=tests.infrastructure_error,
            spec_attempts=spec.attempts_used,
            test_attempts=tests.attempts_used)
        _write_json(out / "result.json", result)
        return result

    connector = connector_agent.run(
        spec.code, tests.code, case.inputs, case.input_context)
    if connector.code:
        (out / "connector.py").write_text(connector.code + "\n", encoding="utf-8")
    _write_json(out / "connector_log.json", {
        "success": connector.success,
        "attempts": connector.attempts_used,
        "error": connector.error,
        "execution_output": connector.execution_output[-4000:],
        "conversation": connector.conversation,
        "api_calls": call_log.calls("connector"),
        "prompts": prompt_audit(
            "connector", connector_agent.system_prompt, connector.conversation),
    })
    if not connector.success:
        note = "error" if connector.infrastructure_error else "connector_failure"
        result = incomplete_result(
            "instance_id", instance_id, note=note, reason=connector.error,
            skipped=connector.infrastructure_error,
            spec_attempts=spec.attempts_used,
            test_attempts=tests.attempts_used,
            connector_attempts=connector.attempts_used)
        _write_json(out / "result.json", result)
        return result

    certification = PythonCertifier(runner).certify(
        spec.code, tests.code, connector.code,
        inputs=case.inputs, context=case.input_context)
    (out / "cert.py").write_text(certification.code, encoding="utf-8")
    decision = evaluate_predictions(certification.evidence, order.ground_truth)
    _write_json(out / "evaluation_log.json", {
        "predictions": decision.predictions,
        "ground_truth": decision.ground_truth,
        "status": decision.status,
        "outcome": decision.outcome,
        "exact_match": decision.exact_match,
        "note": decision.note,
        "reason": decision.reason,
        "execution_output": certification.execution_output[-8000:],
    })
    result = result_record(
        "instance_id", instance_id, decision,
        spec_attempts=spec.attempts_used, test_attempts=tests.attempts_used,
        connector_attempts=connector.attempts_used)
    _write_json(out / "result.json", result)
    return result


def _apply_config(parser: argparse.ArgumentParser, argv) -> argparse.Namespace:
    args = parser.parse_args(argv)
    if not args.config:
        return args
    import yaml
    config = yaml.safe_load(Path(args.config).read_text(encoding="utf-8")) or {}
    valid = {action.dest for action in parser._actions}
    unknown = sorted(set(config) - valid)
    if unknown:
        parser.error(f"unknown config keys in {args.config}: {unknown}")
    parser.set_defaults(**config)
    return parser.parse_args(argv)


def _record_config(out_root: Path, args: argparse.Namespace, stamp: str,
                   filename: str = "config.yaml") -> None:
    import yaml
    resolved = {key: value for key, value in sorted(vars(args).items()) if key != "config"}
    payload = {
        "track": "python", "stamp": stamp, "config_file": args.config, **resolved,
    }
    (out_root / filename).write_text(
        yaml.safe_dump(payload, sort_keys=False, allow_unicode=True), encoding="utf-8")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run the SWE-bench Python SpecAgent, TestAgent, ConnectorAgent, "
            "task-container certifier, and deterministic evaluation."))
    parser.add_argument("--config")
    parser.add_argument("--ids", nargs="*")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--resume", metavar="RUN_DIR")
    parser.add_argument("--retry-errors", action="store_true")
    parser.add_argument("--collate", metavar="RUN_DIR")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--split", default="conflicting",
                        choices=["oneoff", "conflicting", "original"])
    parser.add_argument("--spec-turns", type=int, default=120)
    parser.add_argument("--spec-submissions", type=int, default=10)
    parser.add_argument("--test-submissions", type=int, default=10)
    parser.add_argument("--connector-submissions", type=int, default=6)
    parser.add_argument("--test-order-seed", type=int, default=0)
    parser.add_argument("--exec-timeout", type=int, default=120)
    parser.add_argument("--py-timeout", type=int, default=30)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--provider", default="anthropic",
                        choices=["anthropic", "openai_compatible"])
    parser.add_argument("--api-key-env", default="")
    parser.add_argument("--api-base-env", default="")
    parser.add_argument("--max-tokens", type=int, default=None)
    parser.add_argument("--effort", choices=["low", "medium", "high", "xhigh", "max"])
    parser.add_argument("--workers", type=int, default=5)
    return parser


def _load_results(out_root: Path) -> list[dict]:
    if not out_root.is_dir():
        return []
    return sorted(
        (json.loads(path.read_text(encoding="utf-8"))
         for path in out_root.glob("*/result.json")),
        key=lambda result: result.get("instance_id", ""))


def _collate(out_root: Path, results: list[dict], stamp: str, model: str) -> int:
    write_run_report(out_root, results, identifier_key="instance_id", metadata={
        "stamp": stamp, "model": model,
        "notes": dict(collections.Counter(result.get("note", "") for result in results)),
    }, print_tasks=True)
    return 0


def main(argv=None) -> int:
    parser = _parser()
    args = _apply_config(parser, argv)
    if args.collate:
        directory = Path(args.collate).resolve()
        results = _load_results(directory)
        if not results:
            print(f"no result.json files under {directory}")
            return 2
        return _collate(directory, results, directory.name, directory.parent.name)

    _load_dotenv()
    rows_by_id = load_rows(args.split)
    ids = select_instance_ids(rows_by_id, run_all=args.all, explicit_ids=args.ids)
    if not ids:
        print("no ids (use --ids ... or --all)")
        return 2
    missing = [instance_id for instance_id in ids if instance_id not in rows_by_id]
    if missing:
        print(f"not in split={args.split!r}: {missing}")
        return 2
    if args.limit:
        ids = ids[:args.limit]
    rows = [rows_by_id[instance_id] for instance_id in ids]

    if args.resume:
        out_root = Path(args.resume).resolve()
        stamp = out_root.name
        _record_config(out_root, args, stamp, "config.resume.yaml")
    else:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        model_dir = re.sub(r"[^A-Za-z0-9._-]+", "_", args.model)
        out_root = REPO / "output" / "swe_py_runs" / model_dir / stamp
        out_root.mkdir(parents=True, exist_ok=True)
        _record_config(out_root, args, stamp)

    if args.resume:
        pending = []
        for row in rows:
            path = out_root / row["instance_id"] / "result.json"
            if not path.exists():
                pending.append(row)
            elif args.retry_errors:
                saved = json.loads(path.read_text(encoding="utf-8"))
                if saved.get("status", saved.get("cert")) == SKIPPED:
                    pending.append(row)
        rows = pending
        if not rows:
            return _collate(out_root, _load_results(out_root), stamp, args.model)

    input_records = load_test_inputs()
    common = llm_cfg(args.model, args.provider, args.api_key_env,
                     args.api_base_env, args.max_tokens, args.effort)
    call_log = LLMCallLogger(out_root)
    spec_llm = LLMConfig.from_dict(common).build(
        call_logger=call_log, agent="spec")
    test_llm = LLMConfig.from_dict(common).build(
        call_logger=call_log, agent="tests")
    connector_llm = LLMConfig.from_dict(common).build(
        call_logger=call_log, agent="connector")
    print(f"[run] {len(rows)} instances, model={args.model}, seed={args.test_order_seed} "
          f"-> {out_root}")
    live: collections.Counter = collections.Counter()

    def one(row: dict) -> dict:
        instance_id = row["instance_id"]
        call_log.start_task(instance_id)
        try:
            with TaskPythonRunner(
                instance_id, timeout_seconds=args.py_timeout) as runner:
                result = process_one(
                    row, spec_llm=spec_llm, test_llm=test_llm,
                    connector_llm=connector_llm, runner=runner, out_root=out_root,
                    call_log=call_log,
                    input_records=input_records, order_seed=args.test_order_seed,
                    spec_turns=args.spec_turns, spec_submissions=args.spec_submissions,
                    test_submissions=args.test_submissions,
                    connector_submissions=args.connector_submissions,
                    exec_timeout=args.exec_timeout)
        except MissingImageError as exc:
            result = incomplete_result(
                "instance_id", instance_id, note="error",
                reason=str(exc).splitlines()[0], skipped=True)
        except LLMRefusalError as exc:
            result = incomplete_result(
                "instance_id", instance_id, note="provider_refusal",
                reason=str(exc))
        except Exception as exc:
            result = incomplete_result(
                "instance_id", instance_id, note="error",
                reason=f"{type(exc).__name__}: {exc}"[:500], skipped=True)
        task_out = out_root / instance_id
        task_out.mkdir(parents=True, exist_ok=True)
        _write_json(task_out / "result.json", result)
        progress.set_postfix(record_progress(live, result), refresh=False)
        progress.update(1)
        return result

    log_path = out_root / "run.log"
    with tqdm(total=len(rows), unit="task", desc="instances", dynamic_ncols=True) as progress, \
         open(log_path, "a" if args.resume else "w", encoding="utf-8", buffering=1) as log, \
         contextlib.redirect_stdout(log), \
         ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        list(executor.map(one, rows))
    _write_json(out_root / "usage.json", {
        "spec": spec_llm.usage.total(), "tests": test_llm.usage.total(),
        "connector": connector_llm.usage.total(),
    })
    return _collate(out_root, _load_results(out_root), stamp, args.model)


if __name__ == "__main__":
    raise SystemExit(main())
