#!/usr/bin/env python3
"""Standalone downloader for SWE-bench eval Docker images.

Kept DELIBERATELY separate from the certification pipeline: run.py never pulls
images (disk is finite; each image is ~1-2 GB). Fetch exactly the instances you
need, when you need them, with this tool.

  # list what a split needs and whether each image is already local (no download)
  python -m conflict_certifier.tracks.swebench.pull_images --split oneoff --check

  # pull specific instances
  python -m conflict_certifier.tracks.swebench.pull_images --ids astropy__astropy-14309 sympy__sympy-13480

  # pull every image in a split (BIG — hundreds of GB; asks first)
  python -m conflict_certifier.tracks.swebench.pull_images --split oneoff --all

The image name for an instance is `swebench/sweb.eval.x86_64.<id with __ -> _1776_>:latest`,
pulled from Docker Hub (https://hub.docker.com/u/swebench). Requires a running Docker.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO / "src"))

from conflict_certifier.tracks.swebench import source as swe

SOURCE_DIR = REPO / "data" / "swebench" / "_source"


def _split_ids(split: str) -> list[str]:
    path = SOURCE_DIR / f"impossible_swebench_{split}.jsonl"
    if not path.exists():
        raise FileNotFoundError(f"split not found: {path}")
    ids: list[str] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                ids.append(json.loads(line)["instance_id"])
    return ids


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ids", nargs="*", help="instance ids to pull")
    ap.add_argument("--split", default="oneoff",
                    choices=["oneoff", "conflicting", "original"],
                    help="split to read ids from when using --all/--check")
    ap.add_argument("--all", action="store_true",
                    help="pull every image in the split (large!)")
    ap.add_argument("--check", action="store_true",
                    help="only report which images are present locally; download nothing")
    ap.add_argument("--yes", action="store_true",
                    help="skip the confirmation prompt for large --all pulls")
    args = ap.parse_args(argv)

    if args.ids:
        ids = args.ids
    elif args.all or args.check:
        ids = _split_ids(args.split)
    else:
        print("nothing to do (use --ids ..., or --split ... with --all/--check)")
        return 2

    # --check: report presence only, never download
    if args.check:
        present = missing = 0
        for iid in ids:
            image = swe.docker_image_for(iid)
            here = swe._image_present(image)
            present += here
            missing += not here
            print(f"  [{'local ' if here else 'MISSING'}] {iid}  ({image})")
        print(f"\n  {present} local, {missing} missing of {len(ids)}")
        return 0

    to_pull = [i for i in ids if not swe._image_present(swe.docker_image_for(i))]
    if not to_pull:
        print(f"all {len(ids)} image(s) already present locally; nothing to pull")
        return 0

    # guard the big case: a whole split is hundreds of GB
    if len(to_pull) > 20 and not args.yes:
        print(f"about to pull {len(to_pull)} images (~1-2 GB each; "
              f"roughly {len(to_pull) * 1.5:.0f} GB). Re-run with --yes to proceed.")
        return 2

    failed: list[str] = []
    for n, iid in enumerate(to_pull, 1):
        print(f"[{n}/{len(to_pull)}] {iid}", flush=True)
        try:
            swe.pull_image(iid)
        except Exception as e:  # noqa: BLE001 - report and continue
            print(f"  FAILED: {e}", flush=True)
            failed.append(iid)

    ok = len(to_pull) - len(failed)
    print(f"\n  pulled {ok}/{len(to_pull)}" + (f", failed: {failed}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
