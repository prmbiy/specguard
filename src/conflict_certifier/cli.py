"""Compatibility entry point for the LiveCodeBench experiment."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from conflict_certifier.config import ConfigError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m conflict_certifier.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    lcb = commands.add_parser("lcb", help="run the LiveCodeBench Lean track")
    lcb.add_argument("--config", required=True)
    lcb.add_argument("--model", default=None)
    lcb.add_argument("--limit", type=int, default=None)
    lcb.add_argument("--resume", default=None, metavar="RUN_DIR")
    lcb.add_argument("--retry-errors", action="store_true")
    args = parser.parse_args(argv)

    from conflict_certifier.tracks.livecodebench.config import LiveCodeBenchConfig
    from conflict_certifier.tracks.livecodebench.run import LiveCodeBenchRun

    try:
        config = LiveCodeBenchConfig.load(args.config, model=args.model, limit=args.limit)
        LiveCodeBenchRun(config).run(
            resume=Path(args.resume) if args.resume else None,
            retry_errors=args.retry_errors,
        )
    except ConfigError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
