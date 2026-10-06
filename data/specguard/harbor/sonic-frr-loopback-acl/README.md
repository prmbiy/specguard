# sonic-frr-loopback-acl

Repository: `sonic-net/sonic-mgmt@4da431471daab3ad65234b180d42d21c66abeaeb`. Original selected check: `tests/cacl/test_cacl_application.py::test_cacl_application_nondualtor`.

## Status

Packaged; image build status is recorded in `provenance.json`. Reproduction and SpecGuard classification have not been run.

Requires a SONiC DUT/testbed with the FRR ACL image change and its topology/inventory. Those external systems are not included; image provides the original management/test sources and Python inspection tooling.

The instruction is researcher-authored from the evidence in `provenance.json` and includes the relevant input/output shape. The snapshot's source, tests, skip/xfail markers and license files are unchanged. Research provenance and the fix diff are NOT copied into the image. No credentials are baked in. No newer upstream implementation is substituted for this snapshot.

## Build / run

```bash
docker build -t specguard-sonic-frr-loopback-acl:demo data/specguard/harbor/sonic-frr-loopback-acl/environment
uv run specguard-bench --config configs/specguard.yaml --task sonic-frr-loopback-acl
```

Task-test verifier command (separate from SpecGuard):

```bash
python -m pytest -o addopts= -q tests/cacl/test_cacl_application.py::test_cacl_application_nondualtor
```

A failing import, unavailable service, skip or xfail is not evidence of conflict. This verifier is the original test invocation, not a ground-truth evaluator for SpecGuard. Model configuration remains in `configs/specguard.yaml`.
