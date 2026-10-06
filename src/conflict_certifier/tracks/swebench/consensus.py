"""Generate source fixes once, then privately score them on paired test suites."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import shlex
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import yaml
from tqdm import tqdm

from conflict_certifier.evaluation import EvaluationEvidence, evaluate_predictions, result_record
from conflict_certifier.llm.call_log import LLMCallLogger
from conflict_certifier.llm.config import LLMConfig
from conflict_certifier.reporting import record_progress, write_run_report
from conflict_certifier.tracks.swebench.consensus_eval import (
    TaskContainer, patch_paths, score_candidate, test_files_from_patch,
)
from conflict_certifier.tracks.swebench.prompting import load_prompt, sha256_text


ROOT = Path(__file__).resolve().parents[4]
DATA = ROOT / "data" / "swebench" / "_source"
OUTPUT = ROOT / "output" / "swe_consensus"
LOCALIZE_PROMPT = load_prompt("consensus_localize.txt")
REPAIR_PROMPT = load_prompt("consensus_repair.txt")
USAGE_FIELDS = ("calls", "input_tokens", "output_tokens", "cache_read_input_tokens",
                "cache_creation_input_tokens")


class ResumeCallLogger(LLMCallLogger):
    """Append to task-local API logs when a partially finished task is resumed."""

    def start_task(self, task: str) -> None:
        super().start_task(task)
        for agent in ("localize", "repair"):
            path = self.tasks_root / task / f"{agent}_log.json"
            if path.exists():
                old = json.loads(path.read_text(encoding="utf-8"))
                self._local.state["agents"][agent] = {
                    "calls": old.get("api_calls", []),
                    "conversation": old.get("conversation", []),
                    "system_prompt": old.get("system_prompt", ""),
                }


def _write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, path)


def _read_rows(split: str) -> dict[str, dict]:
    path = DATA / f"impossible_swebench_{split}.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]
    result = {row["instance_id"]: row for row in rows}
    if len(result) != len(rows):
        raise ValueError(f"duplicate task IDs in {path}")
    return result


def paired_rows() -> dict[str, tuple[dict, dict]]:
    original, oneoff = _read_rows("original"), _read_rows("oneoff")
    if original.keys() != oneoff.keys():
        raise ValueError("Original and Oneoff task IDs differ")
    for identifier in original:
        a, b = original[identifier], oneoff[identifier]
        if (a["problem_statement"] != b["problem_statement"]
                or a["test_patch"] != b.get("original_test_patch")
                or a["base_commit"] != b["base_commit"]):
            raise ValueError(f"Original/Oneoff pairing invalid: {identifier}")
    return {key: (original[key], oneoff[key]) for key in sorted(original)}


def _tool_spec() -> list[dict]:
    return [
        {"type": "function", "function": {"name": "bash", "description": "Inspect source without editing it.",
         "parameters": {"type": "object", "properties": {"command": {"type": "string"}},
                        "required": ["command"]}}},
        {"type": "function", "function": {"name": "locations",
         "description": "Finish by naming one to three source excerpts.",
         "parameters": {"type": "object", "properties": {"files": {"type": "array", "items": {
             "type": "object", "properties": {"path": {"type": "string"},
             "start_line": {"type": "integer"}, "end_line": {"type": "integer"}},
             "required": ["path", "start_line", "end_line"]}}}, "required": ["files"]}}},
    ]


def _safe_source_path(path: str) -> bool:
    parts = Path(path).parts
    return (bool(parts) and not Path(path).is_absolute() and ".." not in parts
            and not ({"tests", "test", ".git"} & set(parts))
            and path.endswith(".py") and not Path(path).name.startswith("test_")
            and not Path(path).name.endswith("_test.py"))


def localize(llm, box: TaskContainer, issue: str, turns: int, timeout: int) -> tuple[list[dict], list[dict]]:
    messages = [{"role": "user", "content": f"Issue:\n{issue}\n\nLocate the relevant source."}]
    for _ in range(turns):
        reply = llm.complete_tools(LOCALIZE_PROMPT, messages, _tool_spec())
        messages.append(reply)
        calls = reply.get("tool_calls") or []
        if len(calls) != 1:
            for call in calls:
                messages.append({"role": "tool", "tool_call_id": call["id"],
                                 "content": "Call exactly one tool."})
            if not calls:
                messages.append({"role": "user", "content": "Call exactly one tool."})
            continue
        call = calls[0]
        try:
            args = json.loads(call["function"].get("arguments") or "{}")
        except (ValueError, TypeError):
            args = {}
        name = call["function"].get("name")
        if name == "locations":
            files = args.get("files") if isinstance(args, dict) else None
            if isinstance(files, list) and 1 <= len(files) <= 3 and all(
                isinstance(item, dict) and isinstance(item.get("path"), str)
                and _safe_source_path(item["path"])
                and type(item.get("start_line")) is int
                and type(item.get("end_line")) is int
                and 1 <= item["start_line"] <= item["end_line"] <= item["start_line"] + 300
                for item in files
            ):
                return files, messages
            output = "Invalid source locations."
        elif name == "bash" and isinstance(args, dict) and isinstance(args.get("command"), str):
            command = args["command"]
            # Read-only inspection. Shell commands run in an isolated, test-free
            # container; disallow writes rather than trusting the prompt alone.
            if re.search(r"[;|&><`]|\$\(|\b(?:sed\s+-i|rm|mv|cp|touch|tee|python|perl|pip|git\s+(?:apply|checkout|reset))\b", command):
                output = "Command denied: use a single read-only inspection command."
            else:
                code, data = box.bash(command, timeout=timeout)
                output = f"Exit {code}:\n{data[:12000]}"
        else:
            output = "Invalid tool call."
        messages.append({"role": "tool", "tool_call_id": call["id"], "content": output})
    return [], messages


def excerpts(box: TaskContainer, locations: list[dict]) -> str:
    pieces = []
    for item in locations:
        path = item["path"]
        command = (f"nl -ba -- {shlex.quote(path)} | sed -n "
                   f"{shlex.quote(str(item['start_line']) + ',' + str(item['end_line']) + 'p')}")
        # This command is harness-generated, not agent-generated.
        code, data = box.bash(command, timeout=30)
        if code or not data.strip():
            raise ValueError(f"cannot read localized source: {path}")
        pieces.append(f"### {path}\n{data[:24000]}")
    return "\n\n".join(pieces)


def _extract_patch(reply: str) -> str:
    marker = "diff --git a/"
    offset = reply.find(marker)
    if offset < 0:
        raise ValueError("no Git diff in model response")
    patch = reply[offset:].strip()
    if patch.endswith("```"):
        patch = patch[:-3].rstrip()
    patch_paths(patch)
    return patch + "\n"


def _policy(votes: list[dict], count: int, threshold: str, split: str) -> dict:
    selected = votes[:count]
    valid = [v for v in selected if v.get("valid")]
    minimum = 1 if count == 1 else 2
    truth = [split == "original"]
    if len(valid) < minimum:
        evidence = EvaluationEvidence.inconclusive("insufficient_valid_patches")
    else:
        failures = sum(not v[split]["passed"] for v in valid)
        flagged = (failures > len(valid) / 2 if threshold == "majority"
                   else failures == len(valid))
        evidence = EvaluationEvidence.complete([not flagged])
    decision = evaluate_predictions(evidence, truth)
    return result_record("instance_id", selected[0]["instance_id"], decision,
                         valid_candidates=len(valid), candidates=count,
                         threshold=threshold, split=split)


def _usage_sum(task_dir: Path) -> dict[str, int]:
    return {field: sum(int(json.loads(p.read_text(encoding="utf-8")).get(field, 0))
                       for p in (task_dir / "localize_usage.json", task_dir / "repair_usage.json")
                       if p.exists()) for field in USAGE_FIELDS}


def _save_usage(path: Path, new: dict) -> None:
    old = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    _write_json(path, {field: int(old.get(field, 0)) + int(new.get(field, 0))
                       for field in USAGE_FIELDS})


def _incomplete(identifier: str, cfg: dict, reason: str, *, skipped: bool) -> dict:
    evidence = (EvaluationEvidence.error("infrastructure_error", reason) if skipped
                else EvaluationEvidence.inconclusive("generation_failed", reason))
    policies = {}
    for count in sorted({1, int(cfg["candidates"])}):
        for threshold in ("majority", "all"):
            for split in ("original", "oneoff"):
                key = f"{split}_n{count}_{threshold}"
                policies[key] = result_record(
                    "instance_id", identifier,
                    evaluate_predictions(evidence, [split == "original"]),
                    valid_candidates=0, candidates=count, threshold=threshold, split=split)
    return {"instance_id": identifier, "policies": policies, "valid_candidates": 0,
            "error": reason}


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return round(ordered[lower] + (ordered[upper] - ordered[lower])
                 * (position - lower), 3)


def process_one(pair: tuple[dict, dict], out: Path, cfg: dict,
                localizer, repairer, logger: LLMCallLogger) -> dict:
    task_started = time.monotonic()
    original, oneoff = pair
    identifier = original["instance_id"]
    task_dir = out / identifier
    task_dir.mkdir(parents=True, exist_ok=True)
    final_path = task_dir / "result.json"
    if final_path.exists():
        return json.loads(final_path.read_text(encoding="utf-8"))
    logger.start_task(identifier)
    localizer.usage.start_local()
    repairer.usage.start_local()
    hidden = sorted(set(test_files_from_patch(original["test_patch"])
                        + test_files_from_patch(oneoff["test_patch"])))
    try:
        with TaskContainer(identifier, agent_view=True, test_paths=hidden) as box:
            location_file = task_dir / "locations.json"
            if location_file.exists():
                locations = json.loads(location_file.read_text(encoding="utf-8"))
            else:
                locations, conversation = localize(
                    localizer, box, original["problem_statement"],
                    int(cfg["localize_turns"]), int(cfg["exec_timeout"]))
                _write_json(task_dir / "localization.json", conversation)
                if not locations:
                    raise ValueError("localizer did not identify source files")
                _write_json(location_file, locations)
            source = excerpts(box, locations)
            _write_json(task_dir / "source_excerpts.json", {"locations": locations, "text": source})
            # The identical prompt is used for every independent candidate. No
            # previous candidate or private test result enters this message.
            prompt = (f"Issue:\n{original['problem_statement']}\n\n"
                      f"Buggy source excerpts:\n{source}\n\nReturn one source-code patch.")
            candidates = []
            seen_patches: set[str] = set()
            for index in range(1, int(cfg["candidates"]) + 1):
                directory = task_dir / "candidates" / f"{index:02d}"
                record_file = directory / "candidate.json"
                if record_file.exists():
                    candidate = json.loads(record_file.read_text(encoding="utf-8"))
                else:
                    candidate = {"number": index, "valid_patch": False}
                    generation_started = time.monotonic()
                    conversation = [{"role": "user", "content": prompt}]
                    for attempt in range(1, int(cfg["repair_attempts"]) + 1):
                        reply = repairer.complete_conversation(REPAIR_PROMPT, conversation)
                        _write_json(directory / "attempts" / f"{attempt:02d}.json",
                                    {"text": reply})
                        _write_json(directory / "response.json", {"text": reply,
                                                                    "attempt": attempt})
                        candidate["attempts"] = attempt
                        try:
                            patch = _extract_patch(reply)
                            ok, reason = box.apply(patch, check_only=True)
                            if not ok:
                                raise ValueError(f"patch cannot apply: {reason}")
                            digest = hashlib.sha256(patch.encode("utf-8")).hexdigest()
                            if digest in seen_patches:
                                raise ValueError("duplicate of an earlier candidate; provide a different fix")
                        except ValueError as exc:
                            candidate["reason"] = str(exc)[:1000]
                            if attempt < int(cfg["repair_attempts"]):
                                remaining = int(cfg["repair_attempts"]) - attempt
                                conversation.extend([
                                    {"role": "assistant", "content": reply},
                                    {"role": "user", "content": (
                                        f"Your diff was rejected: {exc}. Repair the diff; "
                                        "return only a complete Git unified diff. "
                                        f"{remaining} attempt(s) remain."
                                        + (" FINAL ATTEMPT: if this next diff is invalid, "
                                           "the candidate will be discarded."
                                           if remaining == 1 else ""))},
                                ])
                            continue
                        (directory / "fix.patch").write_text(patch, encoding="utf-8")
                        candidate["valid_patch"] = True
                        candidate.pop("reason", None)
                        break
                    candidate["generation_seconds"] = round(
                        time.monotonic() - generation_started, 3)
                    _write_json(record_file, candidate)
                if candidate["valid_patch"]:
                    digest = hashlib.sha256((directory / "fix.patch").read_bytes()).hexdigest()
                    if digest in seen_patches:
                        candidate = {**candidate, "valid_patch": False,
                                     "reason": "duplicate of an earlier candidate"}
                    else:
                        seen_patches.add(digest)
                candidates.append(candidate)
    finally:
        _save_usage(task_dir / "localize_usage.json", localizer.usage.take_local())
        _save_usage(task_dir / "repair_usage.json", repairer.usage.take_local())

    votes = []
    for candidate in candidates:
        index = candidate["number"]
        directory = task_dir / "candidates" / f"{index:02d}"
        score_file = directory / "scores.json"
        if score_file.exists():
            score = json.loads(score_file.read_text(encoding="utf-8"))
        elif not candidate["valid_patch"]:
            score = {"instance_id": identifier, "valid": False,
                     "reason": candidate.get("reason", "invalid source patch")}
            _write_json(score_file, score)
        else:
            patch = (directory / "fix.patch").read_text(encoding="utf-8")
            scoring_started = time.monotonic()
            a = score_candidate(identifier, patch, original, timeout=int(cfg["test_timeout"]))
            b = score_candidate(identifier, patch, oneoff, timeout=int(cfg["test_timeout"]))
            score = {"instance_id": identifier, "valid": a.get("valid") and b.get("valid"),
                     "original": a, "oneoff": b,
                     "scoring_seconds": round(time.monotonic() - scoring_started, 3)}
            _write_json(score_file, score)
        votes.append(score)

    policies = {}
    for count in sorted({1, len(votes)}):
        for threshold in ("majority", "all"):
            for split in ("original", "oneoff"):
                key = f"{split}_n{count}_{threshold}"
                policies[key] = _policy(votes, count, threshold, split)
    result = {"instance_id": identifier, "policies": policies,
              "valid_candidates": sum(v.get("valid", False) for v in votes),
              "usage": _usage_sum(task_dir),
              "wall_seconds": round(time.monotonic() - task_started, 3)}
    _write_json(final_path, result)
    return result


def _dotenv() -> None:
    path = ROOT / ".env"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                name, value = line.split("=", 1)
                os.environ.setdefault(name.strip(), value.strip().strip('"').strip("'"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "configs" / "swebench_consensus.yaml")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--ids", nargs="*")
    parser.add_argument("--resume", type=Path)
    args = parser.parse_args(argv)
    cfg = yaml.safe_load(args.config.read_text(encoding="utf-8"))
    if args.limit is not None:
        cfg["limit"] = args.limit
    if args.ids:
        cfg["ids"] = args.ids
    for key in ("localize_turns", "candidates", "repair_attempts", "exec_timeout", "test_timeout", "workers"):
        if int(cfg[key]) < 1:
            parser.error(f"{key} must be positive")
    if cfg["provider"] != "openai_compatible":
        parser.error("localization currently requires openai_compatible native tools")
    if int(cfg["candidates"]) not in (1, 5):
        parser.error("candidates must be 1 (pilot) or 5")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    pairs = paired_rows()
    unknown = set(cfg.get("ids") or []) - pairs.keys()
    if unknown:
        parser.error(f"unknown IDs: {sorted(unknown)}")
    selected = cfg.get("ids") or list(pairs)
    if len(selected) != len(set(selected)):
        parser.error("task IDs must be unique")
    if cfg.get("limit"):
        selected = selected[:int(cfg["limit"])]
    _dotenv()
    llm_cfg = LLMConfig.from_dict(cfg)
    if not os.environ.get(llm_cfg.api_key_env):
        parser.error(f"{llm_cfg.api_key_env} is unset")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = args.resume or OUTPUT / re.sub(r"[^A-Za-z0-9_.-]+", "_", llm_cfg.model) / stamp
    out.mkdir(parents=True, exist_ok=True)
    saved_config = out / "config.json"
    config_record = {"config": cfg, "selected_ids": selected,
                     "localize_prompt_sha256": sha256_text(LOCALIZE_PROMPT),
                     "repair_prompt_sha256": sha256_text(REPAIR_PROMPT),
                     "dataset_sha256": {split: hashlib.sha256(
                         (DATA / f"impossible_swebench_{split}.jsonl").read_bytes()
                     ).hexdigest() for split in ("original", "oneoff")}}
    if saved_config.exists():
        old = json.loads(saved_config.read_text(encoding="utf-8"))
        old_config = {key: value for key, value in old["config"].items() if key != "limit"}
        new_config = {key: value for key, value in cfg.items() if key != "limit"}
        if (old_config != new_config
                or old["selected_ids"] != selected[:len(old["selected_ids"])]
                or old["localize_prompt_sha256"] != config_record["localize_prompt_sha256"]
                or old["repair_prompt_sha256"] != config_record["repair_prompt_sha256"]
                or old["dataset_sha256"] != config_record["dataset_sha256"]):
            parser.error("resume settings, task order, or prompts differ from original run")
        # A five-task pilot may grow to the full 349-task run without regenerating
        # its first five fixes. Retain the original frozen config as the audit record.
    else:
        _write_json(saved_config, config_record)
    _write_json(out / "selection.json", {"selected_ids": selected,
                                          "limit": cfg.get("limit", 0)})
    logger = ResumeCallLogger(out)
    localizer = llm_cfg.build(call_logger=logger, agent="localize")
    repairer = llm_cfg.build(call_logger=logger, agent="repair")
    print(f"[run] {len(selected)} paired tasks, model={llm_cfg.model} -> {out}", flush=True)
    results = []
    counts = Counter()
    with ThreadPoolExecutor(max_workers=int(cfg["workers"])) as pool:
        futures = {pool.submit(process_one, pairs[identifier], out, cfg,
                               localizer, repairer, logger): identifier for identifier in selected}
        with tqdm(total=len(futures), desc="tasks", unit="task") as bar:
            for future in as_completed(futures):
                identifier = futures[future]
                try:
                    result = future.result()
                except Exception as exc:
                    reason = f"{type(exc).__name__}: {exc}"[:1000]
                    skipped = not isinstance(exc, ValueError)
                    result = _incomplete(identifier, cfg, reason, skipped=skipped)
                    result["usage"] = _usage_sum(out / identifier)
                    _write_json(out / identifier / "error.json", result)
                results.append(result)
                primary = result.get("policies", {}).get(f"oneoff_n{cfg['candidates']}_majority")
                if primary:
                    bar.set_postfix(record_progress(counts, primary))
                bar.update()
    usage = {field: sum(int(result.get("usage", {}).get(field, 0)) for result in results)
             for field in USAGE_FIELDS}
    _write_json(out / "usage.json", usage)
    durations = [result["wall_seconds"] for result in results if "wall_seconds" in result]
    _write_json(out / "manifest.json", {"track": "swebench_consensus", "model": llm_cfg.model,
                                       "task_count": len(selected),
                                       "mean_valid_candidates": (
                                           sum(result.get("valid_candidates", 0) for result in results)
                                           / len(results) if results else None),
                                       "wall_seconds_p50": _percentile(durations, .5),
                                       "wall_seconds_p90": _percentile(durations, .9),
                                       "results": results})
    for key in sorted({key for result in results for key in result.get("policies", {})}):
        folder = out / "scores" / key
        folder.mkdir(parents=True, exist_ok=True)
        records = [result["policies"][key] for result in results if key in result.get("policies", {})]
        write_run_report(folder, records, identifier_key="instance_id",
                         metadata={"track": "swebench_consensus", "policy": key,
                                   "model": llm_cfg.model})
    return 0 if all("policies" in result for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
