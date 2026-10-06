"""Shared run collation and metrics for every prediction-vector track."""

from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path

from conflict_certifier.evaluation import (
    CONFLICT,
    FAIL,
    INCORRECT,
    INCONCLUSIVE,
    NO_CONFLICT,
    OUTCOMES,
    SKIPPED,
    STATUSES,
    SUCCESS,
)


def _write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")


def normalize_result(result: dict) -> dict:
    """Read historical three-verdict records without changing old files."""

    if "status" in result:
        return result
    legacy = result.get("cert")
    normalized = dict(result)
    if legacy == CONFLICT:
        normalized.update(status=SUCCESS, outcome=CONFLICT, exact_match=True)
    elif legacy == INCONCLUSIVE:
        normalized.update(status=FAIL, outcome=INCONCLUSIVE, exact_match=None)
    elif legacy == SKIPPED:
        normalized.update(status=SKIPPED, outcome=None, exact_match=None)
    return normalized


def summarize(results: list[dict]) -> dict:
    """Compute metrics solely from the shared result schema."""

    status_counts = Counter(result.get("status") for result in results)
    outcome_counts = Counter(result.get("outcome") for result in results)
    evidence_tier_counts = Counter(
        result.get("evidence_tier") for result in results
        if result.get("evidence_tier")
    )
    total = len(results)
    incorrect = [result for result in results if result.get("outcome") == INCORRECT]
    false_positives = sum(
        bool(result.get("ground_truth"))
        and all(result["ground_truth"])
        and result.get("predictions") != result.get("ground_truth")
        for result in incorrect
    )
    false_negatives = sum(
        bool(result.get("ground_truth"))
        and not all(result["ground_truth"])
        and bool(result.get("predictions"))
        and all(result["predictions"])
        for result in incorrect
    )
    return {
        "n": total,
        "status_counts": {value: status_counts[value] for value in STATUSES},
        "outcome_counts": {value: outcome_counts[value] for value in OUTCOMES},
        "evidence_tier_counts": dict(sorted(evidence_tier_counts.items())),
        "metrics": {
            "exact_vector_accuracy": (
                (outcome_counts[CONFLICT] + outcome_counts[NO_CONFLICT]) / total
                if total else 0.0
            ),
            "coverage": ((status_counts[SUCCESS] + outcome_counts[INCORRECT]) / total
                         if total else 0.0),
            "false_positives": false_positives,
            "false_negatives": false_negatives,
            "inconclusive": outcome_counts[INCONCLUSIVE],
            "skipped": status_counts[SKIPPED],
        },
    }


def record_progress(counts: Counter, result: dict) -> dict[str, int]:
    """Record one result and return the universal progress-bar fields."""

    normalized = normalize_result(result)
    counts[normalized.get("outcome") or normalized["status"]] += 1
    return {
        "conflict": counts[CONFLICT],
        "no_conflict": counts[NO_CONFLICT],
        "incorrect": counts[INCORRECT],
        "inconcl": counts[INCONCLUSIVE],
        "skipped": counts[SKIPPED],
    }


def write_run_report(
    out: Path,
    results: list[dict],
    *,
    identifier_key: str,
    metadata: dict,
    print_tasks: bool = False,
) -> dict:
    """Write the universal manifest/TSV and print the universal summary."""

    results = [normalize_result(result) for result in results]
    summary = summarize(results)
    manifest = {**metadata, **summary, "tasks": results}
    _write_json(out / "manifest.json", manifest)

    columns = [
        identifier_key, "status", "outcome", "evidence_tier", "note", "reason"]
    with (out / "summary.tsv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, delimiter="\t")
        writer.writerow(columns)
        for result in results:
            writer.writerow([result.get(column, "") for column in columns])

    print("\n==================== RESULTS ====================")
    if print_tasks:
        for result in results:
            detail = result.get("note", "")
            if result.get("reason"):
                detail += (": " if detail else "") + result["reason"]
            label = result.get("outcome") or result.get("status")
            print(f"  {result[identifier_key]:32} {label:14} {detail}")
        print("-------------------------------------------------")
    for value in (CONFLICT, NO_CONFLICT, INCORRECT, INCONCLUSIVE):
        count = summary["outcome_counts"][value]
        print(f"  {value:14} = {count}/{summary['n']}")
    if summary["evidence_tier_counts"]:
        for tier, count in summary["evidence_tier_counts"].items():
            print(f"    {tier:12} = {count}/{summary['n']}")
    print(f"  {SKIPPED:14} = {summary['status_counts'][SKIPPED]}/{summary['n']}")
    print(f"  output: {out}")
    return manifest
