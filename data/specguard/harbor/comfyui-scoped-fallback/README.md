# comfyui-scoped-fallback

Pinned repository: `Comfy-Org/ComfyUI-Manager@2b40deba7d04afeee29ee70c88f6c336e43dc9ca`.
Selected original test: `tests/test_install_flags_structural.py::JsCopyStructuralTest::test_generic_fallback_and_frozen_callers_unchanged`.

Status: Docker image built successfully (image ID and build log in provenance.json). Test execution and SpecGuard verdict **not validated**.

Python unittest inspects JavaScript text. Mixed-language structural task, not Python function input/output semantics. Selected test is dependency-light; full ComfyUI runtime is intentionally not installed.

`instruction.md` is researcher-authored and gives the intended behavior and input/output context. The Dockerfile downloads the unmodified repository snapshot, including tests and license notices; no fix diff, metadata, keys, or Git history are copied into the repository.

Build from MATS:

```bash
docker build -t specguard-comfyui-scoped-fallback:demo data/specguard/harbor/comfyui-scoped-fallback/environment
```

Reproduction command inside /testbed:

```bash
python -m pytest --noconftest -o addopts= -q tests/test_install_flags_structural.py::JsCopyStructuralTest::test_generic_fallback_and_frozen_callers_unchanged
```

`tests/test.sh` is a task-test verifier, not a SpecGuard classification evaluator. A nonzero pytest exit must be inspected: import/setup errors are not conflict evidence. Run this task with `uv run specguard-bench --config configs/specguard.yaml --task comfyui-scoped-fallback`, or select tasks in the config and omit `--task`. Model settings remain shared in `configs/specguard.yaml`. No task-local model configuration.
