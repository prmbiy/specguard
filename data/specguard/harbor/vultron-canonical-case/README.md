# vultron-canonical-case

Repository: `CERTCC/Vultron@0a1ea477411d52306d0ca0a15e34748a1ff73a66`. Original selected check: `test/demo/test_initialize_participant_demo.py::test_demo`.

## Status

Packaged; image build status is recorded in `provenance.json`. Reproduction and SpecGuard classification have not been run.

Application dependencies installed. The original demo test retains its xfail marker. Its service/data-layer setup is not proven operational; an xfail result alone is not conflict reproduction.

The instruction is researcher-authored from the evidence in `provenance.json` and includes the relevant input/output shape. The snapshot's source, tests, skip/xfail markers and license files are unchanged. Research provenance and the fix diff are NOT copied into the image. No credentials are baked in. No newer upstream implementation is substituted for this snapshot.

## Build / run

```bash
docker build -t specguard-vultron-canonical-case:demo data/specguard/harbor/vultron-canonical-case/environment
uv run specguard-bench --config configs/specguard.yaml --task vultron-canonical-case
```

Task-test verifier command (separate from SpecGuard):

```bash
python -m pytest -o addopts= -q test/demo/test_initialize_participant_demo.py::test_demo
```

A failing import, unavailable service, skip or xfail is not evidence of conflict. This verifier is the original test invocation, not a ground-truth evaluator for SpecGuard. Model configuration remains in `configs/specguard.yaml`.
