# ucp-repeated-totals

Repository: `Universal-Commerce-Protocol/conformance@9e6201c1c86128b0357b2f1f4c84aa9c7457502d`. Original selected check: `business_logic_test.py::BusinessLogicTest::assert_totals_consistent`.

## Status

Packaged; image build status is recorded in `provenance.json`. Reproduction and SpecGuard classification have not been run.

Selected assertion helper belongs to an integration suite needing a merchant reference server. Packaging preserves the helper and original call sites; it does not invent a new failing example. Full integration reproduction needs server configuration.

The instruction is researcher-authored from the evidence in `provenance.json` and includes the relevant input/output shape. The snapshot's source, tests, skip/xfail markers and license files are unchanged. Research provenance and the fix diff are NOT copied into the image. No credentials are baked in. No newer upstream implementation is substituted for this snapshot.

## Build / run

```bash
docker build -t specguard-ucp-repeated-totals:demo data/specguard/harbor/ucp-repeated-totals/environment
uv run specguard-bench --config configs/specguard.yaml --task ucp-repeated-totals
```

Task-test verifier command (separate from SpecGuard):

```bash
python -m pytest -o addopts= -q business_logic_test.py
```

A failing import, unavailable service, skip or xfail is not evidence of conflict. This verifier is the original test invocation, not a ground-truth evaluator for SpecGuard. Model configuration remains in `configs/specguard.yaml`.
