# erpnext-period-counter

Repository: `frappe/erpnext@1dd0b7dc9e7c687c9c051562d45f6fa91d51eaea`. Original selected check: `erpnext/controllers/tests/test_accounts_controller.py::TestAccountsController::test_document_naming_rule_based_on_posting_date`.

## Status

Packaged; image build status is recorded in `provenance.json`. Reproduction and SpecGuard classification have not been run.

ERPNext Python dependencies installed. A compatible Frappe checkout (including the per-prefix naming change), bench site, MariaDB and Redis are still required for execution. These external services are not fabricated or replaced with mocks.

The instruction is researcher-authored from the evidence in `provenance.json` and includes the relevant input/output shape. The snapshot's source, tests, skip/xfail markers and license files are unchanged. Research provenance and the fix diff are NOT copied into the image. No credentials are baked in. No newer upstream implementation is substituted for this snapshot.

## Build / run

```bash
docker build -t specguard-erpnext-period-counter:demo data/specguard/harbor/erpnext-period-counter/environment
uv run specguard-bench --config configs/specguard.yaml --task erpnext-period-counter
```

Task-test verifier command (separate from SpecGuard):

```bash
python -m pytest -o addopts= -q erpnext/controllers/tests/test_accounts_controller.py::TestAccountsController::test_document_naming_rule_based_on_posting_date
```

A failing import, unavailable service, skip or xfail is not evidence of conflict. This verifier is the original test invocation, not a ground-truth evaluator for SpecGuard. Model configuration remains in `configs/specguard.yaml`.
