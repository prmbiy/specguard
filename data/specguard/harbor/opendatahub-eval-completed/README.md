# opendatahub-eval-completed

Repository: `opendatahub-io/opendatahub-tests@67564156721beb03560e538befea44e4a7a14ff8`. Original selected check: `tests/ai_safety/evalhub/k8s_lifecycle_signals/test_evalhub_lifecycle_job_label_phase.py::TestLblJobLabelPhase::test_lbl_002_job_label_set_to_succeeded_on_completion`.

## Status

Packaged; image build status is recorded in `provenance.json`. Reproduction and SpecGuard classification have not been run.

Requires an OpenShift/Kubernetes cluster with EvalHub, a known-good model/dataset, routes and test fixtures. Source-inspection image only; no cluster or external services are provisioned.

The instruction is researcher-authored from the evidence in `provenance.json` and includes the relevant input/output shape. The snapshot's source, tests, skip/xfail markers and license files are unchanged. Research provenance and the fix diff are NOT copied into the image. No credentials are baked in. No newer upstream implementation is substituted for this snapshot.

## Build / run

```bash
docker build -t specguard-opendatahub-eval-completed:demo data/specguard/harbor/opendatahub-eval-completed/environment
uv run specguard-bench --config configs/specguard.yaml --task opendatahub-eval-completed
```

Task-test verifier command (separate from SpecGuard):

```bash
python -m pytest -o addopts= -q tests/ai_safety/evalhub/k8s_lifecycle_signals/test_evalhub_lifecycle_job_label_phase.py::TestLblJobLabelPhase::test_lbl_002_job_label_set_to_succeeded_on_completion
```

A failing import, unavailable service, skip or xfail is not evidence of conflict. This verifier is the original test invocation, not a ground-truth evaluator for SpecGuard. Model configuration remains in `configs/specguard.yaml`.
