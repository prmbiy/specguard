# kornia-bfloat16-mix

Repository: `kornia/kornia@90650596d4de49448ea1359975d3baff71931c84`. Original selected check: `tests/augmentation/test_augmentation_mix.py::TestRandomMixUpV2::test_random_mixup_p0`.

## Status

Packaged; image build status is recorded in `provenance.json`. Reproduction and SpecGuard classification have not been run.

CPU PyTorch installed. Upstream half-precision xfail registry and its pytest hook remain unchanged. The expected-failure policy is part of this candidate; discovery/formalization must include that policy, not just the passing assertions. GPU backends are not provisioned.

The instruction is researcher-authored from the evidence in `provenance.json` and includes the relevant input/output shape. The snapshot's source, tests, skip/xfail markers and license files are unchanged. Research provenance and the fix diff are NOT copied into the image. No credentials are baked in. No newer upstream implementation is substituted for this snapshot.

## Build / run

```bash
docker build -t specguard-kornia-bfloat16-mix:demo data/specguard/harbor/kornia-bfloat16-mix/environment
uv run specguard-bench --config configs/specguard.yaml --task kornia-bfloat16-mix
```

Task-test verifier command (separate from SpecGuard):

```bash
python -m pytest -o addopts= -q tests/augmentation/test_augmentation_mix.py::TestRandomMixUpV2::test_random_mixup_p0 --device cpu --dtype bfloat16 --xfail-known-failures
```

A failing import, unavailable service, skip or xfail is not evidence of conflict. This verifier is the original test invocation, not a ground-truth evaluator for SpecGuard. Model configuration remains in `configs/specguard.yaml`.
