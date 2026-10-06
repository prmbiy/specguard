# ckan-include-users-default

Repository: `ckan/ckan@308ed49062c072ceaba2c6f9f2139f28c2854ab2`. Original selected check: `ckan/tests/controllers/test_organization.py::TestOrganizationMembership::test_member_delete`.

## Status

Packaged; image build status is recorded in `provenance.json`. Reproduction and SpecGuard classification have not been run.

CKAN package installed; application test execution additionally needs the configured PostgreSQL, Solr and Redis services, test INI and initialized database. No external services or credentials are embedded.

The instruction is researcher-authored from the evidence in `provenance.json` and includes the relevant input/output shape. The snapshot's source, tests, skip/xfail markers and license files are unchanged. Research provenance and the fix diff are NOT copied into the image. No credentials are baked in. No newer upstream implementation is substituted for this snapshot.

## Build / run

```bash
docker build -t specguard-ckan-include-users-default:demo data/specguard/harbor/ckan-include-users-default/environment
uv run specguard-bench --config configs/specguard.yaml --task ckan-include-users-default
```

Task-test verifier command (separate from SpecGuard):

```bash
python -m pytest -o addopts= -q ckan/tests/controllers/test_organization.py::TestOrganizationMembership::test_member_delete
```

A failing import, unavailable service, skip or xfail is not evidence of conflict. This verifier is the original test invocation, not a ground-truth evaluator for SpecGuard. Model configuration remains in `configs/specguard.yaml`.
