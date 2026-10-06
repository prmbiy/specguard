# anyllm-multiturn-tools

Repository: `mozilla-ai/any-llm@9b3448ff8ef7953877758f6b618720cee59a74e8`. Original selected check: `tests/integration/test_agent_loop.py::test_agent_loop_parallel_tool_calls`.

## Status

Packaged; image build status is recorded in `provenance.json`. Reproduction and SpecGuard classification have not been run.

The selected upstream integration test needs live provider credentials/network. Neither is baked into this image and no provider requests are made by packaging or validation. Test source remains unchanged.

The instruction is researcher-authored from the evidence in `provenance.json` and includes the relevant input/output shape. The snapshot's source, tests, skip/xfail markers and license files are unchanged. Research provenance and the fix diff are NOT copied into the image. No credentials are baked in. No newer upstream implementation is substituted for this snapshot.

## Build / run

```bash
docker build -t specguard-anyllm-multiturn-tools:demo data/specguard/harbor/anyllm-multiturn-tools/environment
uv run specguard-bench --config configs/specguard.yaml --task anyllm-multiturn-tools
```

Task-test verifier command (separate from SpecGuard):

```bash
python -m pytest -o addopts= -q tests/integration/test_agent_loop.py::test_agent_loop_parallel_tool_calls
```

A failing import, unavailable service, skip or xfail is not evidence of conflict. This verifier is the original test invocation, not a ground-truth evaluator for SpecGuard. Model configuration remains in `configs/specguard.yaml`.
