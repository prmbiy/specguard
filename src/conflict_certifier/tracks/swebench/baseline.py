#!/usr/bin/env python3
"""Task-level LLM baseline for SWE-bench original and oneoff splits.

The baseline sees an issue and a sealed checkout containing that row's installed tests.
It returns one task-level label. It does not generate a formal artifact and is kept
separate from the SWE-bench Lean and Python certification pipelines.
"""

from __future__ import annotations

import argparse
import atexit
import collections
import contextlib
import hashlib
import json
import os
import re
import subprocess
import sys
import threading
from concurrent.futures import CancelledError, Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from tqdm import tqdm

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO / "src"))

from conflict_certifier.evaluation import (  # noqa: E402
    EvaluationEvidence,
    SKIPPED,
    evaluate_predictions,
    incomplete_result,
    result_record,
)
from conflict_certifier.tracks.swebench.baseline_votes import aggregate, policy_metrics  # noqa: E402
from conflict_certifier.llm.call_log import LLMCallLogger  # noqa: E402
from conflict_certifier.llm.client import LLMClient, LLMRefusalError  # noqa: E402
from conflict_certifier.llm.config import LLMConfig  # noqa: E402
from conflict_certifier.reporting import record_progress, write_run_report  # noqa: E402
from conflict_certifier.tracks.swebench.prompting import (  # noqa: E402
    load_prompt,
    sha256_text,
)
from conflict_certifier.tracks.swebench.source import (  # noqa: E402
    MissingImageError,
    require_image,
)


SOURCE_DIR = REPO / "data" / "swebench" / "_source"
OUTPUT_DIR = REPO / "output" / "swe_baseline"
SYSTEM_PROMPT = load_prompt("baseline_system.txt")

CONFLICTING = "CONFLICTING"
NOT_CONFLICTING = "NOT_CONFLICTING"
LABELS = (CONFLICTING, NOT_CONFLICTING)


def system_prompt(allow_inconclusive=False):
    return load_prompt('baseline_abstention.txt') if allow_inconclusive else SYSTEM_PROMPT


def _run_folder_name(stamp: str, split: str, votes: int, allow_inconclusive: bool) -> str:
    return f"{stamp}_{split}_k{votes}" + ('_incl' if allow_inconclusive else '')

def _tools(labels: tuple[str, ...]) -> list[dict]:
    """The judge's two actions as native tool schemas; the provider enforces one call per turn."""
    return [
        {"type": "function", "function": {
            "name": "bash",
            "description": "Run one bash command in the repository at /testbed and return its output.",
            "parameters": {"type": "object", "properties": {"command": {"type": "string"}},
                           "required": ["command"]}}},
        {"type": "function", "function": {
            "name": "verdict",
            "description": "Finish with the task-level classification and the evidence for it.",
            "parameters": {"type": "object", "properties": {
                "label": {"type": "string", "enum": list(labels)},
                "reasons": {"type": "string"}}, "required": ["label", "reasons"]}}},
    ]
_COMMAND_OUTPUT_CAP = 6000
_CONTAINER_MEMORY = "4g"
_CONTAINER_PIDS = 256
_CONTAINER_PATH = "/opt/miniconda3/bin:/usr/local/bin:/usr/bin:/bin"
_USAGE_FIELDS = (
    "calls",
    "input_tokens",
    "output_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
)


def _write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    data = json.dumps(value, indent=2, ensure_ascii=False)
    with temporary.open("w", encoding="utf-8") as output:
        output.write(data)
        output.flush()
        os.fsync(output.fileno())
    os.replace(temporary, path)


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


def _dataset_path(split: str) -> Path:
    return SOURCE_DIR / f"impossible_swebench_{split}.jsonl"


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_rows(split: str) -> dict[str, dict]:
    path = _dataset_path(split)
    if not path.exists():
        raise FileNotFoundError(f"split not found: {path}")
    rows: dict[str, dict] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        identifier = row["instance_id"]
        if identifier in rows:
            raise ValueError(f"duplicate instance_id in {path}: {identifier}")
        rows[identifier] = row
    return rows


class BaselineContainer:
    """A test-patched, Git-free, networkless SWE-bench task container."""

    _active: set[str] = set()
    _active_lock = threading.Lock()

    def __init__(self, instance_id: str, test_patch: str, *, exec_timeout: int = 120):
        if not test_patch.strip():
            raise ValueError("test_patch is empty")
        self.instance_id = instance_id
        self.test_patch = test_patch
        self.exec_timeout = exec_timeout
        self.image = require_image(instance_id)
        self._cid: str | None = None

    @classmethod
    def _track(cls, cid: str) -> None:
        with cls._active_lock:
            cls._active.add(cid)

    @classmethod
    def _untrack(cls, cid: str) -> None:
        with cls._active_lock:
            cls._active.discard(cid)

    @classmethod
    def close_all(cls) -> None:
        with cls._active_lock:
            containers = tuple(cls._active)
            cls._active.clear()
        for cid in containers:
            subprocess.run(
                ["docker", "rm", "-f", cid],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

    @staticmethod
    def _remove(cid: str) -> None:
        subprocess.run(
            ["docker", "rm", "-f", cid],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        BaselineContainer._untrack(cid)

    def _run(self, command: list[str], **kwargs) -> subprocess.CompletedProcess:
        return subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            **kwargs,
        )

    def start(self) -> "BaselineContainer":
        if self._cid is not None:
            return self
        safe_task = re.sub(r"[^A-Za-z0-9_.-]+", "_", self.instance_id)
        created = self._run([
            "docker", "create",
            "--network=none",
            "--cap-drop=ALL",
            "--security-opt", "no-new-privileges:true",
            "--pids-limit", str(_CONTAINER_PIDS),
            "--memory", _CONTAINER_MEMORY,
            "--memory-swap", _CONTAINER_MEMORY,
            "--tmpfs", "/tmp:rw,nosuid,nodev,size=512m",
            "--label", "conflict-certifier.track=swebench-baseline",
            "--label", f"conflict-certifier.task={safe_task}",
            "-w", "/testbed",
            self.image,
            "sleep", "infinity",
        ], timeout=120)
        if created.returncode != 0:
            raise RuntimeError(f"docker create failed: {created.stdout[-2000:]}")
        cid = created.stdout.strip().splitlines()[-1].strip()
        if not cid:
            raise RuntimeError("docker create returned no container id")
        self._track(cid)
        self._cid = cid
        try:
            started = self._run(["docker", "start", cid], timeout=120)
            if started.returncode != 0:
                raise RuntimeError(f"docker start failed: {started.stdout[-2000:]}")

            # The patch is never written to the host or exposed as a model message.
            # Apply it while Git metadata exists, then permanently remove that metadata
            # before the first agent command can run.
            for action in ("--check", "--apply"):
                git_args = ["git", "apply", "--whitespace=nowarn"]
                if action == "--check":
                    git_args.append("--check")
                git_args.append("-")
                applied = self._run(
                    ["docker", "exec", "-i", "-w", "/testbed", cid, *git_args],
                    input=self.test_patch,
                    timeout=120,
                )
                if applied.returncode != 0:
                    raise RuntimeError(
                        f"test patch {action.removeprefix('--')} failed: "
                        f"{applied.stdout[-2000:]}"
                    )

            sealed = self._run(
                ["docker", "exec", cid, "rm", "-rf", "/testbed/.git"],
                timeout=60,
            )
            if sealed.returncode != 0:
                raise RuntimeError(
                    f"failed to remove Git metadata: {sealed.stdout[-1000:]}"
                )
        except Exception:
            self.close()
            raise
        return self

    def bash(self, script: str) -> tuple[int, str]:
        if self._cid is None:
            raise RuntimeError("task container is not running")
        command = [
            "docker", "exec", "-w", "/testbed", self._cid,
            "env", "-i",
            f"PATH={_CONTAINER_PATH}",
            "HOME=/tmp",
            "LANG=C.UTF-8",
            "PYTHONDONTWRITEBYTECODE=1",
            "timeout", "--signal=KILL", str(self.exec_timeout),
            "bash", "--noprofile", "--norc", "-c", script,
        ]
        try:
            result = self._run(command, timeout=self.exec_timeout + 10)
            return result.returncode, result.stdout
        except subprocess.TimeoutExpired:
            # An outer timeout means the in-container timeout mechanism failed. Close
            # the container so an agent command can never remain running invisibly.
            self.close()
            return 124, f"<command timed out after {self.exec_timeout}s>"

    def close(self) -> None:
        if self._cid is not None:
            cid, self._cid = self._cid, None
            self._remove(cid)

    def __enter__(self) -> "BaselineContainer":
        return self.start()

    def __exit__(self, *_) -> None:
        self.close()


atexit.register(BaselineContainer.close_all)


@dataclass(frozen=True)
class BaselineAnswer:
    label: str | None
    turns: int
    conversation: list[dict]
    error: str = ""
    explanation: str = ""
    executions: int = 0


class SwebenchBaselineAgent:
    system_prompt = SYSTEM_PROMPT

    def __init__(self, llm: LLMClient, *, max_turns: int = 120,
                 exec_timeout: int = 120, allow_inconclusive: bool = False):
        if max_turns < 1 or exec_timeout < 1:
            raise ValueError("max_turns and exec_timeout must be positive")
        self._llm = llm
        self._max_turns = max_turns
        self.exec_timeout = exec_timeout
        self.system_prompt = system_prompt(allow_inconclusive)
        self.labels = (*LABELS, 'INCONCLUSIVE') if allow_inconclusive else LABELS
        self.tools = _tools(self.labels)
        self.stop_event = threading.Event()

    def initial_prompt(self, issue: str) -> str:
        return (
            f"You have at most {self._max_turns} turns.\n\n"
            f"## Issue\n{issue.strip()}\n\n"
            "Inspect the repository and return your task-level verdict when ready."
        )

    def run(self, issue: str, box: BaselineContainer) -> BaselineAnswer:
        conversation: list[dict] = [
            {"role": "user", "content": self.initial_prompt(issue)}
        ]
        executions = 0
        for turn in range(1, self._max_turns + 1):
            if self.stop_event.is_set():
                raise CancelledError('Run interrupted')
            reply = self._llm.complete_tools(self.system_prompt, conversation, self.tools)
            conversation.append(reply)
            calls = reply.get("tool_calls") or []
            remaining = self._max_turns - turn
            suffix = f"\n\n{remaining} turns remain." if remaining else ""
            invalid = ("Invalid response. Call exactly one tool: bash with a nonempty command, "
                       "or verdict with label in " + ' or '.join(self.labels)
                       + " and nonempty reasons.")
            if not calls:
                conversation.append({"role": "user", "content": invalid + suffix})
                continue
            if len(calls) > 1:
                # Every tool call must be answered for the transcript to stay valid.
                for call in calls:
                    conversation.append({"role": "tool", "tool_call_id": call["id"],
                                         "content": invalid + suffix})
                continue
            call = calls[0]
            try:
                args = json.loads(call["function"].get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            if not isinstance(args, dict):
                args = {}
            name = call["function"].get("name")
            label = args.get("label")
            reasons = str(args.get("reasons") or "").strip()
            command = str(args.get("command") or "").strip()
            if name == "verdict" and label in self.labels and reasons:
                return BaselineAnswer(label, turn, conversation,
                                      explanation=reasons, executions=executions)
            if name == "bash" and command:
                code, output = box.bash(command)
                executions += 1
                if len(output) > _COMMAND_OUTPUT_CAP:
                    output = output[:_COMMAND_OUTPUT_CAP] + "\n... output truncated ..."
                content = f"Command exited {code}:\n```text\n{output}\n```"
            else:
                content = invalid
            conversation.append({"role": "tool", "tool_call_id": call["id"],
                                 "content": content + suffix})

        return BaselineAnswer(
            None,
            self._max_turns,
            conversation,
            f"baseline exhausted {self._max_turns} turns without a valid label",
            executions=executions,
        )

def decision_for_label(label: str, split: str):
    if label not in (*LABELS, 'INCONCLUSIVE'):
        raise ValueError(f"invalid baseline label: {label!r}")
    if split not in ("original", "oneoff"):
        raise ValueError(f"unsupported baseline split: {split!r}")
    if label == 'INCONCLUSIVE':
        return evaluate_predictions(EvaluationEvidence.inconclusive('baseline_inconclusive'), [split == 'original'])
    prediction = label == NOT_CONFLICTING
    ground_truth = (split == "original",)
    return evaluate_predictions(EvaluationEvidence.complete([prediction]), ground_truth)


def _finalize_baseline_log(
    task_dir: Path,
    call_log: LLMCallLogger,
    answer: BaselineAnswer,
    prompt: str = SYSTEM_PROMPT,
) -> None:
    # The durable call logger may contain a more recent conversation than
    # ``answer`` when an API call raised (including a provider refusal).
    existing = {}
    path = task_dir / "baseline_log.json"
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            existing = {}
    conversation = answer.conversation or existing.get("conversation", [])
    _write_json(task_dir / "baseline_log.json", {
        "in_progress": False,
        "system_prompt": prompt,
        "system_prompt_sha256": sha256_text(prompt),
        "initial_prompt_sha256": (
            sha256_text(conversation[0]["content"])
            if conversation else None
        ),
        "conversation": conversation,
        "api_calls": call_log.calls("baseline"),
        "label": answer.label,
        "turns": answer.turns,
        "error": answer.error,
    })


def process_one(
    row: dict,
    split: str,
    agent: SwebenchBaselineAgent,
    out_root: Path,
    call_log: LLMCallLogger,
    *,
    container_factory: Callable[..., BaselineContainer] = BaselineContainer,
    task_dir: Path | None = None,
) -> dict:
    identifier = row["instance_id"]
    task_dir = task_dir or out_root / identifier
    task_dir.mkdir(parents=True, exist_ok=True)
    patch = row.get("test_patch", "")
    if not patch.strip():
        return incomplete_result(
            "instance_id", identifier, note="setup_error",
            reason="dataset row has an empty test_patch", skipped=True)

    answer = BaselineAnswer(None, 0, [], "baseline did not start")
    try:
        with container_factory(
            identifier, patch, exec_timeout=agent.exec_timeout
        ) as box:
            answer = agent.run(row["problem_statement"], box)
    finally:
        _finalize_baseline_log(task_dir, call_log, answer, agent.system_prompt)

    explanation = answer.explanation
    if answer.label is None or answer.label == 'INCONCLUSIVE':
        return incomplete_result(
            "instance_id", identifier, note="baseline_inconclusive",
            reason=explanation or answer.error, baseline_turns=answer.turns,
            baseline_label=answer.label, baseline_executions=answer.executions,
            ground_truth=[split == 'original'])
    if answer.executions == 0:
        # A label reached without any executed command is not evidence-based; it is
        # recorded (label kept for audit) but never scored as a decision.
        return incomplete_result(
            "instance_id", identifier, note="no_evidence",
            reason="verdict returned without executing any command: " + explanation,
            baseline_turns=answer.turns, baseline_label=answer.label,
            baseline_executions=0, ground_truth=[split == 'original'])
    decision = decision_for_label(answer.label, split)
    return result_record(
        "instance_id", identifier, decision,
        baseline_label=answer.label,
        baseline_turns=answer.turns,
        baseline_executions=answer.executions,
        reason_summary=explanation,
        evidence_tier="llm_baseline",
    )

def _usage_total(results: list[dict]) -> dict[str, int]:
    return {
        field: sum(int(result.get("usage", {}).get(field, 0) or 0)
                   for result in results)
        for field in _USAGE_FIELDS
    }


def _load_results(out_root: Path) -> list[dict]:
    return sorted(
        (json.loads(path.read_text(encoding="utf-8"))
         for path in out_root.glob("*/result.json")),
        key=lambda result: result.get("instance_id", ""),
    )


def _collate(out_root: Path, results: list[dict], *, model: str, split: str,
             votes: int = 1, allow_inconclusive: bool = False) -> int:
    _write_json(out_root / "usage.json", _usage_total(results))
    notes = collections.Counter(result.get("note", "") for result in results)
    write_run_report(out_root, results, identifier_key="instance_id", metadata={
        "track": "swebench_baseline",
        "model": model,
        "split": split,
        "system_prompt_sha256": sha256_text(system_prompt(allow_inconclusive)),
        "votes": votes, "allow_inconclusive": allow_inconclusive,
        "notes": dict(notes),
    }, print_tasks=True)
    by_k = {}
    for k in (1, 3, 5):
        if k > votes:
            continue
        prefix_results = []
        for r in results:
            if votes == 1:
                prefix_results.append(r)
            else:
                paths = [out_root/r['instance_id']/'votes'/str(i)/'result.json' for i in range(1,k+1)]
                if all(p.exists() for p in paths):
                    prefix_results.append(aggregate(r['instance_id'],
                        [json.loads(p.read_text()) for p in paths], split))
        by_k[str(k)] = {'metrics': policy_metrics(prefix_results, split), 'tasks': prefix_results}
    _write_json(out_root/'vote_summary.json', {
        'split': split, 'allow_inconclusive': allow_inconclusive, 'by_k': by_k,
        'aggregation': 'Strict majority of all k votes; otherwise inconclusive. Fixed vote-index prefixes.',
    })
    audit = []
    for result in results:
        task = out_root/result['instance_id']
        paths = ([task/'result.json'] if votes == 1 else
                 [task/'votes'/str(i)/'result.json' for i in range(1, votes+1)])
        for path in paths:
            if path.exists():
                record = json.loads(path.read_text())
                if record.get('baseline_label') == 'INCONCLUSIVE':
                    audit.append({'instance_id': result['instance_id'],
                                  'result_file': str(path.relative_to(out_root)),
                                  'reason': record.get('reason', ''), 'audit_verdict': None})
    _write_json(out_root/'abstention_audit_candidates.json', {
        'note': 'Candidates for manual review, not automatic judgments of reason validity. No audit calls were made.',
        'candidates': audit,
    })
    return 0


def _apply_config(parser: argparse.ArgumentParser, argv) -> argparse.Namespace:
    initial = parser.parse_args(argv)
    if not initial.config:
        return initial
    import yaml
    config = yaml.safe_load(Path(initial.config).read_text(encoding="utf-8")) or {}
    valid = {action.dest for action in parser._actions}
    unknown = sorted(set(config) - valid)
    if unknown:
        parser.error(f"unknown config keys in {initial.config}: {unknown}")
    parser.set_defaults(**config)
    return parser.parse_args(argv)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the isolated task-level SWE-bench LLM baseline.")
    parser.add_argument("--config")
    parser.add_argument("--ids", nargs="*")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--split", choices=["original", "oneoff"], default="original")
    parser.add_argument("--resume", metavar="RUN_DIR")
    parser.add_argument("--retry-errors", action="store_true")
    parser.add_argument("--collate", metavar="RUN_DIR")
    parser.add_argument("--turns", type=int, default=120)
    parser.add_argument("--exec-timeout", type=int, default=120)
    parser.add_argument("--workers", type=int, default=5)
    parser.add_argument('--votes', type=int, choices=[1, 3, 5], default=1)
    parser.add_argument('--allow-inconclusive', action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--provider", choices=["anthropic", "openai_compatible"],
                        default="openai_compatible")
    parser.add_argument("--model", default="gpt-5.6-sol")
    parser.add_argument("--api-key-env", default="OPENAI_API_KEY")
    parser.add_argument("--api-base-env", default="")
    parser.add_argument("--max-tokens", type=int, default=None)
    parser.add_argument("--effort", choices=["low", "medium", "high", "xhigh", "max"])
    return parser


def _record_config(out_root: Path, args: argparse.Namespace, filename: str) -> dict:
    import yaml
    dataset = _dataset_path(args.split)
    payload = {
        "track": "swebench_baseline",
        **{key: value for key, value in sorted(vars(args).items())
           if key not in ("config", "collate")},
        "config_file": args.config,
        "dataset_sha256": _file_sha256(dataset),
        "system_prompt_sha256": sha256_text(system_prompt(args.allow_inconclusive)),
    }
    (out_root / filename).write_text(
        yaml.safe_dump(payload, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return payload


def _validate_resume(out_root: Path, args: argparse.Namespace) -> None:
    import yaml
    path = out_root / "config.yaml"
    if not path.exists():
        raise ValueError(f"resume directory has no config.yaml: {out_root}")
    saved = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    current = {
        "track": "swebench_baseline",
        "split": args.split,
        "provider": args.provider,
        "model": args.model,
        "dataset_sha256": _file_sha256(_dataset_path(args.split)),
        "system_prompt_sha256": sha256_text(system_prompt(getattr(args, 'allow_inconclusive', False))),
        "allow_inconclusive": getattr(args, 'allow_inconclusive', False),
        "votes": getattr(args, 'votes', 1),
    }
    mismatches = [
        key for key, value in current.items()
        if saved.get(key, {'votes': 1, 'allow_inconclusive': False}.get(key)) != value
    ]
    if mismatches:
        details = ", ".join(
            f"{key}: saved={saved.get(key)!r}, current={current[key]!r}"
            for key in mismatches)
        raise ValueError(f"resume settings do not match ({details})")


def main(argv=None) -> int:
    parser = _parser()
    args = _apply_config(parser, argv)
    if args.votes not in (1,3,5) or not isinstance(args.allow_inconclusive, bool):
        parser.error('votes must be 1, 3, or 5; allow_inconclusive must be a YAML boolean')
    if args.turns < 1 or args.exec_timeout < 1 or args.workers < 1 or args.limit < 0:
        parser.error("turns, exec_timeout, and workers must be positive; limit cannot be negative")

    if args.collate:
        out_root = Path(args.collate).resolve()
        config_path = out_root / "config.yaml"
        if not config_path.exists():
            parser.error(f"no config.yaml under {out_root}")
        import yaml
        saved = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        return _collate(
            out_root, _load_results(out_root),
            model=saved.get("model", out_root.parent.name),
            split=saved.get("split", "unknown"),
            votes=saved.get('votes', 1), allow_inconclusive=saved.get('allow_inconclusive', False),
        )

    _load_dotenv()
    rows_by_id = load_rows(args.split)
    identifiers = list(args.ids or (sorted(rows_by_id) if args.all else []))
    if not identifiers:
        parser.error("no tasks selected (use --all or --ids ...)")
    missing = [identifier for identifier in identifiers if identifier not in rows_by_id]
    if missing:
        parser.error(f"tasks not found in split {args.split!r}: {missing}")
    if args.limit:
        identifiers = identifiers[:args.limit]

    if args.resume:
        out_root = Path(args.resume).resolve()
        _validate_resume(out_root, args)
        _record_config(out_root, args, "config.resume.yaml")
    else:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        model_dir = re.sub(r"[^A-Za-z0-9._-]+", "_", args.model)
        out_root = OUTPUT_DIR / model_dir / _run_folder_name(
            stamp, args.split, args.votes, args.allow_inconclusive)
        out_root.mkdir(parents=True, exist_ok=False)
        _record_config(out_root, args, "config.yaml")

    pending: list[dict] = []
    for identifier in identifiers:
        result_path = out_root / identifier / "result.json"
        if not result_path.exists() or (args.votes > 1 and any(
            not (out_root/identifier/'votes'/str(i)/'result.json').exists() for i in range(1,args.votes+1))):
            pending.append(rows_by_id[identifier])
            continue
        saved = json.loads(result_path.read_text(encoding="utf-8"))
        if args.retry_errors and saved.get("status") == SKIPPED:
            pending.append(rows_by_id[identifier])
        elif args.retry_errors and args.votes > 1 and any(
            json.loads((out_root/identifier/'votes'/str(i)/'result.json').read_text()).get('status') == SKIPPED
            for i in range(1,args.votes+1)):
            pending.append(rows_by_id[identifier])

    if not pending:
        return _collate(
            out_root, _load_results(out_root), model=args.model, split=args.split,
            votes=args.votes, allow_inconclusive=args.allow_inconclusive)

    call_log = LLMCallLogger(out_root)
    llm = LLMConfig.from_dict({
        "provider": args.provider,
        "model": args.model,
        "api_key_env": args.api_key_env,
        "api_base_env": args.api_base_env,
        "max_tokens": args.max_tokens,
        "effort": args.effort,
    }).build(call_logger=call_log, agent="baseline")
    agent = SwebenchBaselineAgent(
        llm, max_turns=args.turns, exec_timeout=args.exec_timeout,
        allow_inconclusive=args.allow_inconclusive)

    counts: collections.Counter = collections.Counter()

    def run_vote(row: dict, task_dir: Path) -> dict:
        identifier = row["instance_id"]
        call_log.start_task(str(task_dir.relative_to(out_root)))
        llm.usage.start_local()
        try:
            result = process_one(row, args.split, agent, out_root, call_log, task_dir=task_dir)
        except CancelledError:
            raise
        except LLMRefusalError as exc:
            result = incomplete_result(
                "instance_id", identifier, note="provider_refusal", reason=str(exc))
        except MissingImageError as exc:
            result = incomplete_result(
                "instance_id", identifier, note="missing_image",
                reason=str(exc).splitlines()[0], skipped=True)
        except Exception as exc:
            result = incomplete_result(
                "instance_id", identifier, note="error",
                reason=f"{type(exc).__name__}: {exc}"[:1000], skipped=True)
        finally:
            usage = llm.usage.take_local()
        result["usage"] = usage
        _write_json(task_dir / "result.json", result)
        return result

    def run_task(row: dict) -> dict:
        identifier = row['instance_id']
        if args.votes == 1:
            result = run_vote(row, out_root/identifier)
            details = aggregate(identifier, [result], args.split)
            result.update({k: details[k] for k in ('k', 'conflict_score', 'vote_counts')})
        else:
            records = []
            for i in range(1,args.votes+1):
                if agent.stop_event.is_set():
                    raise CancelledError('Run interrupted')
                task_dir = out_root/identifier/'votes'/str(i)
                path = task_dir/'result.json'
                saved = json.loads(path.read_text()) if path.exists() else None
                if saved is None or (args.retry_errors and saved.get('status') == SKIPPED):
                    saved = run_vote(row, task_dir)
                records.append(saved)
            result = aggregate(identifier, records, args.split)
            result['usage'] = _usage_total(records)
        _write_json(out_root/identifier/'result.json', result)
        return result

    print(
        f"[baseline] {len(pending)} tasks, split={args.split}, model={args.model} -> {out_root}")
    executor = ThreadPoolExecutor(max_workers=args.workers)
    futures: dict[Future, str] = {}
    try:
        with tqdm(total=len(pending), unit="task", desc="tasks", dynamic_ncols=True) as progress, \
             (out_root / "run.log").open(
                 "a" if args.resume else "w", encoding="utf-8", buffering=1) as log, \
             contextlib.redirect_stdout(log):
            futures = {
                executor.submit(run_task, row): row["instance_id"] for row in pending
            }
            for future in as_completed(futures):
                result = future.result()
                progress.set_postfix(record_progress(counts, result), refresh=False)
                progress.update(1)
    except KeyboardInterrupt:
        agent.stop_event.set()
        for future in futures:
            future.cancel()
        BaselineContainer.close_all()
        executor.shutdown(wait=False, cancel_futures=True)
        print("\nInterrupted; active baseline containers were removed.", file=sys.stderr)
        return 130
    else:
        executor.shutdown(wait=True)
    finally:
        BaselineContainer.close_all()

    return _collate(
            out_root, _load_results(out_root), model=args.model, split=args.split,
            votes=args.votes, allow_inconclusive=args.allow_inconclusive)


if __name__ == "__main__":
    raise SystemExit(main())
