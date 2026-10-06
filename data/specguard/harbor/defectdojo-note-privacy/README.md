# defectdojo-note-privacy

Repository: `DefectDojo/django-DefectDojo@2427d34148a94732d7195e148228c886c88192ed`. Original selected check: `unittests/api_v3/test_apiv3_subresources.py::TestApiV3SubresourcesNotePrivacy::test_private_note_visible_to_other_authorized_user_v2_parity`.

## Status

Packaged; image build status is recorded in `provenance.json`. Reproduction and SpecGuard classification have not been run.

Source-inspection image with Django tooling, not a complete DefectDojo deployment. Full pinned application requirements, database migrations, application settings and test fixtures remain necessary for execution.

The instruction is researcher-authored from the evidence in `provenance.json` and includes the relevant input/output shape. The snapshot's source, tests, skip/xfail markers and license files are unchanged. Research provenance and the fix diff are NOT copied into the image. No credentials are baked in. No newer upstream implementation is substituted for this snapshot.

## Build / run

```bash
docker build -t specguard-defectdojo-note-privacy:demo data/specguard/harbor/defectdojo-note-privacy/environment
uv run specguard-bench --config configs/specguard.yaml --task defectdojo-note-privacy
```

Task-test verifier command (separate from SpecGuard):

```bash
python -m pytest -o addopts= -q unittests/api_v3/test_apiv3_subresources.py::TestApiV3SubresourcesNotePrivacy::test_private_note_visible_to_other_authorized_user_v2_parity
```

A failing import, unavailable service, skip or xfail is not evidence of conflict. This verifier is the original test invocation, not a ground-truth evaluator for SpecGuard. Model configuration remains in `configs/specguard.yaml`.
