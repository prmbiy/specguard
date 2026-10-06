# gardena-discovery-burst

Repository: `cloudless-garden/ha-gardena-smart-local-preview@1ac21261d6333fa9ecf744f28164749c54367318`. Original selected check: `tests/test_coordinator.py::test_schedule_unknown_device_discovery_reschedules_on_burst`.

## Status

Packaged; image build status is recorded in `provenance.json`. Reproduction and SpecGuard classification have not been run.

Home Assistant test dependencies installed. Selected test uses mocked scheduling, not physical Gardena hardware. Dependency versions are recorded in the build freeze; reproduction has not yet been verified.

The instruction is researcher-authored from the evidence in `provenance.json` and includes the relevant input/output shape. The snapshot's source, tests, skip/xfail markers and license files are unchanged. Research provenance and the fix diff are NOT copied into the image. No credentials are baked in. No newer upstream implementation is substituted for this snapshot.

## Build / run

```bash
docker build -t specguard-gardena-discovery-burst:demo data/specguard/harbor/gardena-discovery-burst/environment
uv run specguard-bench --config configs/specguard.yaml --task gardena-discovery-burst
```

Task-test verifier command (separate from SpecGuard):

```bash
python -m pytest -o addopts= -q tests/test_coordinator.py::test_schedule_unknown_device_discovery_reschedules_on_burst
```

A failing import, unavailable service, skip or xfail is not evidence of conflict. This verifier is the original test invocation, not a ground-truth evaluator for SpecGuard. Model configuration remains in `configs/specguard.yaml`.
