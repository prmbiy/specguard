# borg-preopen-atime

Repository: `borgbackup/borg@a6904402ef300eaf95fb757ecbdea245979549d7`. Original selected check: `src/borg/testsuite/archiver/extract_cmd_test.py::test_timestamps_win32`.

## Status

Packaged; image build status is recorded in `provenance.json`. Reproduction and SpecGuard classification have not been run.

The selected test is Windows/NTFS-specific and cannot be reproduced in this Linux container. Native Borg extensions are not compiled. Source and Windows-specific test are retained unchanged; this is a source-inspection image, not a Windows emulator.

The instruction is researcher-authored from the evidence in `provenance.json` and includes the relevant input/output shape. The snapshot's source, tests, skip/xfail markers and license files are unchanged. Research provenance and the fix diff are NOT copied into the image. No credentials are baked in. No newer upstream implementation is substituted for this snapshot.

## Build / run

```bash
docker build -t specguard-borg-preopen-atime:demo data/specguard/harbor/borg-preopen-atime/environment
uv run specguard-bench --config configs/specguard.yaml --task borg-preopen-atime
```

Task-test verifier command (separate from SpecGuard):

```bash
python -m pytest -o addopts= -q src/borg/testsuite/archiver/extract_cmd_test.py::test_timestamps_win32
```

A failing import, unavailable service, skip or xfail is not evidence of conflict. This verifier is the original test invocation, not a ground-truth evaluator for SpecGuard. Model configuration remains in `configs/specguard.yaml`.
