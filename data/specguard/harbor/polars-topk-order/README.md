# polars-topk-order

Repository: `pola-rs/polars@c6e05d169d48c28f860884c314d5126d101dabda`. Original selected check: `py-polars/tests/unit/operations/test_top_k.py::test_top_k`.

## Status

Packaged; image build status is recorded in `provenance.json`. Reproduction and SpecGuard classification have not been run.

Source-inspection image. The pinned Rust/Polars extension is not built; no newer PyPI implementation is substituted. Executing the selected test requires building that extension.

The instruction is researcher-authored from the evidence in `provenance.json` and includes the relevant input/output shape. The snapshot's source, tests, skip/xfail markers and license files are unchanged. Research provenance and the fix diff are NOT copied into the image. No credentials are baked in. No newer upstream implementation is substituted for this snapshot.

## Build / run

```bash
docker build -t specguard-polars-topk-order:demo data/specguard/harbor/polars-topk-order/environment
uv run specguard-bench --config configs/specguard.yaml --task polars-topk-order
```

Task-test verifier command (separate from SpecGuard):

```bash
python -m pytest -o addopts= -q py-polars/tests/unit/operations/test_top_k.py::test_top_k
```

A failing import, unavailable service, skip or xfail is not evidence of conflict. This verifier is the original test invocation, not a ground-truth evaluator for SpecGuard. Model configuration remains in `configs/specguard.yaml`.
