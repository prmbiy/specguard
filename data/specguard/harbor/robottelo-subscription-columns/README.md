# robottelo-subscription-columns

Repository: `SatelliteQE/robottelo@9f45edc6705604c2682a18f95da8eaa58a47f507`. Original selected check: `tests/foreman/ui/test_subscription.py::test_select_customizable_columns_uncheck_and_checks_all_checkboxes`.

## Status

Packaged; image build status is recorded in `provenance.json`. Reproduction and SpecGuard classification have not been run.

Requires a Foreman/Katello deployment, browser and matching Airgun client. The image contains original tests and Python inspection tools, not that deployment. No testbed credentials are supplied.

The instruction is researcher-authored from the evidence in `provenance.json` and includes the relevant input/output shape. The snapshot's source, tests, skip/xfail markers and license files are unchanged. Research provenance and the fix diff are NOT copied into the image. No credentials are baked in. No newer upstream implementation is substituted for this snapshot.

## Build / run

```bash
docker build -t specguard-robottelo-subscription-columns:demo data/specguard/harbor/robottelo-subscription-columns/environment
uv run specguard-bench --config configs/specguard.yaml --task robottelo-subscription-columns
```

Task-test verifier command (separate from SpecGuard):

```bash
python -m pytest -o addopts= -q tests/foreman/ui/test_subscription.py::test_select_customizable_columns_uncheck_and_checks_all_checkboxes
```

A failing import, unavailable service, skip or xfail is not evidence of conflict. This verifier is the original test invocation, not a ground-truth evaluator for SpecGuard. Model configuration remains in `configs/specguard.yaml`.
