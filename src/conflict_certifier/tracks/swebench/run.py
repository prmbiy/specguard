#!/usr/bin/env python3
"""SWE-bench Lean: generate agent artifacts, then evaluate deterministically.

The evaluated agent consists of SpecAgent, TestAgent, and an optional neutral
ConnectorAgent. The Lean certifier contains no LLM and computes numbered test
predictions; shared code compares them with private ground truth.
"""

from __future__ import annotations

import argparse
import collections
import contextlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from tqdm import tqdm

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO / "src"))

from conflict_certifier.config import LeanEnv
from conflict_certifier.evaluation import (
    SKIPPED,
    evaluate_predictions,
    incomplete_result,
    result_record,
)
from conflict_certifier.reporting import record_progress, write_run_report
from conflict_certifier.lean.pool import run_many
from conflict_certifier.lean.runner import LeanRunner
from conflict_certifier.llm.config import LLMConfig
from conflict_certifier.llm.call_log import LLMCallLogger
from conflict_certifier.llm.client import LLMRefusalError
from conflict_certifier.tracks.swebench.spec_agent import SweCodebaseSpecAgent
from conflict_certifier.tracks.swebench.prompting import prompt_audit
from conflict_certifier.tracks.swebench.connector_agent import SweConnectorAgent
from conflict_certifier.tracks.swebench.certifier import LeanCertifier
from conflict_certifier.tracks.swebench.source import (
    InputUnavailableError,
    MissingImageError,
    load_test_inputs,
    prepare_agent_case,
)
from conflict_certifier.tracks.swebench.test_agent import SweTestAgent


SOURCE_DIR = REPO / "data" / "swebench" / "_source"
DEFAULT_MODEL = "claude-opus-4-8"
LEAN_PROJECT = os.environ.get("LEAN_PROJECT", "/mnt/data/lean/workspace")
REPL_BIN = os.environ.get("REPL_BIN", "/mnt/data/lean/repl/.lake/build/bin/repl")
ELAN_BIN = os.environ.get("ELAN_BIN", "/mnt/data/lean/elan/bin")
if Path(ELAN_BIN).is_dir() and ELAN_BIN not in os.environ.get("PATH", ""):
    os.environ["PATH"] = ELAN_BIN + os.pathsep + os.environ.get("PATH", "")


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


def select_instance_ids(by_id: dict[str, dict], *, run_all: bool,
                        explicit_ids: list[str] | None) -> list[str]:
    """Select instances, letting explicit CLI ids override config ``all: true``."""
    if explicit_ids:
        return list(explicit_ids)
    return sorted(by_id) if run_all else []


def llm_cfg(model: str, provider: str = "anthropic", api_key_env: str = "",
            api_base_env: str = "", max_tokens: int | None = None,
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
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")


def process_one(row: dict, spec_agent: SweCodebaseSpecAgent,
                test_agent: SweTestAgent, connector_agent: SweConnectorAgent,
                certifier: LeanCertifier, out_root: Path,
                call_log: LLMCallLogger,
                input_records: dict[str, dict], order_seed: int,
                split: str = "conflicting") -> dict:
    iid = row["instance_id"]
    out = out_root / iid
    out.mkdir(parents=True, exist_ok=True)

    try:
        case, order = prepare_agent_case(
            row, input_records.get(iid), order_seed=order_seed, split=split)
    except InputUnavailableError as exc:
        result = incomplete_result(
            "instance_id", iid, note="input_unavailable", reason=str(exc))
        _write_json(out / "result.json", result)
        return result

    (out / "input_context.txt").write_text(
        "## Inputs\n" + "\n".join(
            f"input_{i}: {value}" for i, value in enumerate(case.inputs)
        ) + "\n\n## Context\n" + case.input_context + "\n",
        encoding="utf-8",
    )
    if split in ("original", "oneoff"):
        _write_json(out / "test_order.json", {
            "suite": "test",
            "ground_truth": list(order.ground_truth),
        })
    else:
        _write_json(out / "test_order.json", {
            "seed": order_seed,
            "good_test": order.good_test_number,
            "bad_test": order.bad_test_number,
        })

    print(f"[{iid}] SpecAgent", flush=True)
    spec = spec_agent.run(
        iid, case.description, case.repo, case.inputs, case.input_context,
        label=f"{iid}_spec",
    )
    if spec.lean_code:
        (out / "spec.lean").write_text(spec.lean_code, encoding="utf-8")
    _write_json(out / "spec_log.json", {
        "compile_success": spec.compile_success,
        "attempts": spec.attempts_used,
        "error": spec.error,
        "compile_output": spec.compile_output[-4000:],
        "conversation": spec.conversation,
        "api_calls": call_log.calls("spec"),
        "prompts": prompt_audit(
            "spec", getattr(spec_agent, "system_prompt", ""), spec.conversation),
    })
    if not spec.compile_success:
        result = incomplete_result(
            "instance_id", iid, note="spec_agent_failure", reason=spec.error,
            spec_attempts=spec.attempts_used)
        _write_json(out / "result.json", result)
        return result

    print(f"[{iid}] TestAgent", flush=True)
    tests = test_agent.run(
        case.inputs, case.input_context, case.test_0_patch, case.test_1_patch,
        label=f"{iid}_tests",
    )
    if tests.model_code:
        (out / "tests_model.lean").write_text(tests.model_code, encoding="utf-8")
    if tests.cases_code:
        (out / "tests_cases.lean").write_text(tests.cases_code, encoding="utf-8")
    if tests.model_code and tests.cases_code:
        (out / "tests.lean").write_text(tests.lean_code, encoding="utf-8")
    _write_json(out / "test_log.json", {
        "compile_success": tests.compile_success,
        "attempts": tests.attempts_used,
        "error": tests.error,
        "compile_output": tests.compile_output[-4000:],
        "conversation": tests.conversation,
        "api_calls": call_log.calls("tests"),
        "prompts": prompt_audit(
            "original_test" if split in ("original", "oneoff") else "test",
            getattr(test_agent, "system_prompt", ""), tests.conversation),
    })
    if not tests.compile_success:
        result = incomplete_result(
            "instance_id", iid, note="test_agent_failure", reason=tests.error,
            spec_attempts=spec.attempts_used,
            test_attempts=tests.attempts_used)
        _write_json(out / "result.json", result)
        return result

    print(f"[{iid}] Connector", flush=True)
    connector = connector_agent.run(
        spec.lean_code, tests.model_code, tests.cases_code,
        case.inputs, case.input_context, label=f"{iid}_connector")
    if connector.lean_code:
        (out / "connector.lean").write_text(connector.lean_code, encoding="utf-8")
    _write_json(out / "connector_log.json", {
        "compile_success": connector.compile_success,
        "mode": connector.mode,
        "attempts": connector.attempts_used,
        "error": connector.error,
        "compile_output": connector.compile_output[-4000:],
        "conversation": connector.conversation,
        "api_calls": call_log.calls("connector"),
        "prompts": prompt_audit(
            "original_connector" if split in ("original", "oneoff") else "connector",
            getattr(connector_agent, "system_prompt", ""),
            connector.conversation),
    })
    if not connector.compile_success:
        result = incomplete_result(
            "instance_id", iid, note="connector_failure", reason=connector.error,
            spec_attempts=spec.attempts_used,
            test_attempts=tests.attempts_used,
            connector_attempts=connector.attempts_used)
        _write_json(out / "result.json", result)
        return result

    print(f"[{iid}] Certifier", flush=True)
    certify = (certifier.certify_original if split in ("original", "oneoff")
               else certifier.certify)
    certification = certify(
        spec.lean_code, tests.model_code, tests.cases_code, connector.lean_code,
        order.ground_truth,
        label=f"{iid}_evaluation")
    evaluation = evaluate_predictions(certification.evidence, order.ground_truth)
    (out / "cert.lean").write_text(certification.lean_code, encoding="utf-8")
    if certification.execution_code:
        (out / "execution.lean").write_text(
            certification.execution_code, encoding="utf-8")
    _write_json(out / "evaluation_log.json", {
        "status": evaluation.status,
        "outcome": evaluation.outcome,
        "evidence_tier": certification.evidence_tier or None,
        "predictions": evaluation.predictions,
        "ground_truth": evaluation.ground_truth,
        "exact_match": evaluation.exact_match,
        "note": evaluation.note,
        "reason": evaluation.reason,
        "proof_compile_output": certification.compile_output[-8000:],
        "execution_compile_output": certification.execution_output[-8000:],
    })
    result = result_record(
        "instance_id", iid, evaluation,
        spec_attempts=spec.attempts_used,
        test_attempts=tests.attempts_used,
        connector_mode=connector.mode,
        connector_attempts=connector.attempts_used,
        evidence_tier=certification.evidence_tier or None,
    )
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
                   filename: str = "config.yaml", *, track: str = "lean") -> None:
    import yaml
    resolved = {key: value for key, value in sorted(vars(args).items()) if key != "config"}
    payload = {
        "track": track,
        "stamp": stamp,
        "config_file": args.config,
        **resolved,
    }
    (out_root / filename).write_text(
        yaml.safe_dump(payload, sort_keys=False, allow_unicode=True), encoding="utf-8")


def _parser(*, tool_mode: bool = False) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run the complete SWE-bench Lean pipeline: SpecAgent, TestAgent, "
            "Connector, and deterministic evaluation. One progress bar advances "
            "once per fully processed task."
        ),
        epilog=(
            "Task results use the shared prediction-vector evaluator: successful "
            "CONFLICT/NO_CONFLICT, failed INCORRECT/INCONCLUSIVE, or SKIPPED."
        ),
    )
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
    parser.add_argument("--exec-timeout", type=int, default=120)
    parser.add_argument("--test-submissions", type=int, default=10)
    parser.add_argument("--connector-submissions", type=int, default=6)
    parser.add_argument("--test-order-seed", type=int, default=0)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--provider", default="anthropic",
                        choices=["anthropic", "openai_compatible"])
    parser.add_argument("--api-key-env", default="")
    parser.add_argument("--api-base-env", default="")
    parser.add_argument("--max-tokens", type=int, default=None)
    parser.add_argument("--effort", choices=["low", "medium", "high", "xhigh", "max"])
    parser.add_argument("--workers", type=int, default=5)
    parser.add_argument("--repl-processes", type=int, default=2)
    parser.add_argument("--lean-project", default=LEAN_PROJECT)
    parser.add_argument("--repl-bin", default=REPL_BIN)
    parser.add_argument("--lean-timeout", type=int, default=120)
    if tool_mode:
        from conflict_certifier.tracks.swebench.specguard_tool import DEFAULT_PRICES
        for key, price in DEFAULT_PRICES.items():
            parser.add_argument(f"--{key.replace('_', '-')}-price", type=float,
                                default=price)
    return parser


def main(argv=None, *, tool_mode: bool = False) -> int:
    parser = _parser(tool_mode=tool_mode)
    args = _apply_config(parser, argv)
    if args.collate:
        directory = Path(args.collate).resolve()
        results = _load_results(directory)
        if not results:
            print(f"no result.json files under {directory}")
            return 2
        if tool_mode:
            from conflict_certifier.tracks.swebench.specguard_tool import collate_usage
            _write_json(directory / "usage.json", collate_usage(directory))
        return _collate(directory, results, directory.name, directory.parent.name)

    _load_dotenv()
    by_id = load_rows(args.split)
    ids = select_instance_ids(by_id, run_all=args.all, explicit_ids=args.ids)
    if not ids:
        print("no ids (use --ids ... or --all)")
        return 2
    missing = [iid for iid in ids if iid not in by_id]
    if missing:
        print(f"not in split={args.split!r}: {missing}")
        return 2
    if args.limit:
        ids = ids[:args.limit]
    rows = [by_id[iid] for iid in ids]

    if args.resume:
        out_root = Path(args.resume).resolve()
        if tool_mode:
            import yaml
            saved_config = out_root / "config.yaml"
            if not saved_config.is_file():
                parser.error(f"native-tool resume has no config.yaml: {out_root}")
            saved = yaml.safe_load(saved_config.read_text(encoding="utf-8")) or {}
            if saved.get("track") != "lean_tool":
                parser.error(f"not a specguard_tool run: {out_root}")
            if saved.get("model") != args.model or saved.get("split") != args.split:
                parser.error("resume model and split must match the original run")
        stamp = out_root.name
        _record_config(out_root, args, stamp, "config.resume.yaml",
                       track="lean_tool" if tool_mode else "lean")
    else:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        model_dir = re.sub(r"[^A-Za-z0-9._-]+", "_", args.model)
        out_root = REPO / "output" / ("specguard_tool" if tool_mode else "swe_runs") / model_dir / stamp
        out_root.mkdir(parents=True, exist_ok=True)
        _record_config(out_root, args, stamp,
                       track="lean_tool" if tool_mode else "lean")

    if args.resume:
        todo = []
        for row in rows:
            result_file = out_root / row["instance_id"] / "result.json"
            if not result_file.exists():
                todo.append(row)
                continue
            saved = json.loads(result_file.read_text(encoding="utf-8"))
            if args.retry_errors and saved.get("status", saved.get("cert")) == SKIPPED:
                todo.append(row)
        rows = todo
        if not rows:
            if tool_mode:
                from conflict_certifier.tracks.swebench.specguard_tool import collate_usage
                _write_json(out_root / "usage.json", collate_usage(out_root))
            return _collate(out_root, _load_results(out_root), stamp, args.model)

    input_records = load_test_inputs()
    lean_env = LeanEnv.from_dict({
        "project": args.lean_project,
        "repl_bin": args.repl_bin,
        "timeout": args.lean_timeout,
        "workers": args.workers,
        "repl_processes": args.repl_processes,
    })
    lean_env.validate()
    runner = LeanRunner(lean_env)
    common_llm = llm_cfg(args.model, args.provider, args.api_key_env,
                         args.api_base_env, args.max_tokens, args.effort)
    # Separate clients make the three context boundaries explicit even when all
    # stages use the same configured provider/model.
    call_log = LLMCallLogger(out_root)
    if tool_mode:
        from conflict_certifier.tracks.swebench.specguard_tool import (
            ToolConnectorAgent, ToolResponsesClient, ToolSpecAgent, ToolTestAgent,
        )
        prices = {key: getattr(args, f"{key}_price")
                  for key in ("input", "cached_input", "cache_write_input", "output")}
        if any(value < 0 for value in prices.values()):
            parser.error("token prices must be nonnegative")
        if args.provider != "openai_compatible":
            parser.error("specguard_tool requires provider: openai_compatible")
        spec_llm, test_llm, connector_llm = (
            ToolResponsesClient(LLMConfig.from_dict(common_llm),
                                call_logger=call_log, agent=agent, prices=prices)
            for agent in ("spec", "tests", "connector")
        )
        spec_type, test_type, connector_type = (
            ToolSpecAgent, ToolTestAgent, ToolConnectorAgent)
    else:
        spec_llm = LLMConfig.from_dict(common_llm).build(
            call_logger=call_log, agent="spec")
        test_llm = LLMConfig.from_dict(common_llm).build(
            call_logger=call_log, agent="tests")
        connector_llm = LLMConfig.from_dict(common_llm).build(
            call_logger=call_log, agent="connector")
        spec_type, test_type, connector_type = (
            SweCodebaseSpecAgent, SweTestAgent, SweConnectorAgent)
    spec_agent = spec_type(
        spec_llm, runner, max_turns=args.spec_turns,
        max_submissions=args.spec_submissions, exec_timeout=args.exec_timeout)
    test_agent = test_type(
        test_llm, runner, max_submissions=args.test_submissions,
        original=args.split in ("original", "oneoff"))
    connector_agent = connector_type(
        connector_llm, runner, max_submissions=args.connector_submissions,
        original=args.split in ("original", "oneoff"))
    certifier = LeanCertifier(runner)

    print(f"[run] {len(rows)} instances, model={args.model}, seed={args.test_order_seed} "
          f"-> {out_root}")
    live: collections.Counter = collections.Counter()

    def one(row: dict) -> dict:
        iid = row["instance_id"]
        call_log.start_task(iid)
        try:
            result = process_one(
                row, spec_agent, test_agent, connector_agent, certifier, out_root,
                call_log, input_records, args.test_order_seed, args.split)
        except MissingImageError as exc:
            result = incomplete_result(
                "instance_id", iid, note="error",
                reason=str(exc).splitlines()[0], skipped=True)
            task_out = out_root / iid
            task_out.mkdir(parents=True, exist_ok=True)
            _write_json(task_out / "result.json", result)
        except LLMRefusalError as exc:
            result = incomplete_result(
                "instance_id", iid, note="provider_refusal", reason=str(exc))
            task_out = out_root / iid
            task_out.mkdir(parents=True, exist_ok=True)
            _write_json(task_out / "result.json", result)
        except Exception as exc:
            result = incomplete_result(
                "instance_id", iid, note="error",
                reason=f"{type(exc).__name__}: {exc}"[:400], skipped=True)
            task_out = out_root / iid
            task_out.mkdir(parents=True, exist_ok=True)
            _write_json(task_out / "result.json", result)
        progress.set_postfix(record_progress(live, result), refresh=False)
        progress.update(1)
        return result

    log_path = out_root / "run.log"
    try:
        with tqdm(total=len(rows), unit="task", desc="instances", dynamic_ncols=True) as progress, \
             open(log_path, "a" if args.resume else "w", encoding="utf-8", buffering=1) as log, \
             contextlib.redirect_stdout(log):
            run_many(rows, one, workers=args.workers)
    finally:
        runner.close()
        if tool_mode:
            from conflict_certifier.tracks.swebench.specguard_tool import collate_usage
            _write_json(out_root / "usage.json", collate_usage(out_root))
    if not tool_mode:
        _write_json(out_root / "usage.json", {
            "spec": spec_llm.usage.total(),
            "tests": test_llm.usage.total(),
            "connector": connector_llm.usage.total(),
        })
    return _collate(out_root, _load_results(out_root), stamp, args.model)


def _load_results(out_root: Path) -> list[dict]:
    if not out_root.is_dir():
        return []
    return sorted(
        (json.loads(path.read_text(encoding="utf-8"))
         for path in out_root.rglob("result.json")),
        key=lambda result: result.get("instance_id", ""),
    )


def _collate(out_root: Path, results: list[dict], stamp: str, model: str) -> int:
    note_counts = collections.Counter(result.get("note", "") for result in results)
    write_run_report(out_root, results, identifier_key="instance_id", metadata={
        "stamp": stamp,
        "model": model,
        "notes": dict(note_counts),
    }, print_tasks=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
