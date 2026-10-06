# ray-actor-order

Repository: `ray-project/ray@9fbfdcd7b076c6e9cbf376b0fd387b1393f98321`. Original selected check: `python/ray/data/tests/test_map_batches.py::test_map_batches_actors_preserves_order`.

## Status

Packaged; image build status is recorded in `provenance.json`. Reproduction and SpecGuard classification have not been run.

Source-inspection image. Ray native binaries are not compiled; executing the selected distributed test additionally requires a matching Ray build. Ordering is nondeterministic, so one passing execution does not establish the contract.

The instruction is researcher-authored from the evidence in `provenance.json` and includes the relevant input/output shape. The snapshot's source, tests, skip/xfail markers and license files are unchanged. Research provenance and the fix diff are NOT copied into the image. No credentials are baked in. No newer upstream implementation is substituted for this snapshot.

## Build / run

```bash
docker build -t specguard-ray-actor-order:demo data/specguard/harbor/ray-actor-order/environment
uv run specguard-bench --config configs/specguard.yaml --task ray-actor-order
```

Task-test verifier command (separate from SpecGuard):

```bash
python -m pytest -o addopts= -q python/ray/data/tests/test_map_batches.py::test_map_batches_actors_preserves_order
```

A failing import, unavailable service, skip or xfail is not evidence of conflict. This verifier is the original test invocation, not a ground-truth evaluator for SpecGuard. Model configuration remains in `configs/specguard.yaml`.
