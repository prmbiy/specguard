# unsloth-mcp-tool-names

Repository: `unslothai/unsloth@c5faa7fb31a92af7eb996a4de5536025eedb7a63`. Original selected check: `studio/backend/tests/test_mcp_servers.py::test_mcp_specs_skip_invalid_openai_function_names`.

## Status

Packaged; image build status is recorded in `provenance.json`. Reproduction and SpecGuard classification have not been run.

CPU source-inspection image with MCP/API utilities. Full Unsloth inference/GPU stack is not installed; no models are downloaded. Selected MCP test uses local data; remaining Studio import dependencies must be checked before reproduction.

The instruction is researcher-authored from the evidence in `provenance.json` and includes the relevant input/output shape. The snapshot's source, tests, skip/xfail markers and license files are unchanged. Research provenance and the fix diff are NOT copied into the image. No credentials are baked in. No newer upstream implementation is substituted for this snapshot.

## Build / run

```bash
docker build -t specguard-unsloth-mcp-tool-names:demo data/specguard/harbor/unsloth-mcp-tool-names/environment
uv run specguard-bench --config configs/specguard.yaml --task unsloth-mcp-tool-names
```

Task-test verifier command (separate from SpecGuard):

```bash
python -m pytest -o addopts= -q studio/backend/tests/test_mcp_servers.py::test_mcp_specs_skip_invalid_openai_function_names
```

A failing import, unavailable service, skip or xfail is not evidence of conflict. This verifier is the original test invocation, not a ground-truth evaluator for SpecGuard. Model configuration remains in `configs/specguard.yaml`.
