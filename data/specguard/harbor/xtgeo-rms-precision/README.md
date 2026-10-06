# xtgeo-rms-precision

Repository: `equinor/xtgeo@ba88852b57bbe02cfa5548d3b89ca79c36fe5dce`. Original selected check: `tests/test_roxarapi/test_roxarapi_reek.py::test_blocked_well_roxar_to_from_file`.

## Status

Packaged; image build status is recorded in `provenance.json`. Reproduction and SpecGuard classification have not been run.

Source-inspection image. Running the selected test requires the proprietary Roxar/RMS Python API, its project fixtures, and a compiled XTGeo extension; those are not redistributed or emulated.

The instruction is researcher-authored from the evidence in `provenance.json` and includes the relevant input/output shape. The snapshot's source, tests, skip/xfail markers and license files are unchanged. Research provenance and the fix diff are NOT copied into the image. No credentials are baked in. No newer upstream implementation is substituted for this snapshot.

## Build / run

```bash
docker build -t specguard-xtgeo-rms-precision:demo data/specguard/harbor/xtgeo-rms-precision/environment
uv run specguard-bench --config configs/specguard.yaml --task xtgeo-rms-precision
```

Task-test verifier command (separate from SpecGuard):

```bash
python -m pytest -o addopts= -q tests/test_roxarapi/test_roxarapi_reek.py::test_blocked_well_roxar_to_from_file
```

A failing import, unavailable service, skip or xfail is not evidence of conflict. This verifier is the original test invocation, not a ground-truth evaluator for SpecGuard. Model configuration remains in `configs/specguard.yaml`.
