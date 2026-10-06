# vllm-precompiled-flags

Pinned repository: `vllm-project/vllm@2131b597b18d051dced4c4a605d362fa37f46ed1`.
Selected original test: `tests/test_envs.py::test_precompiled_install_flags_are_orthogonal`.

Status: Docker image built successfully (image ID and build log in provenance.json). Test execution and SpecGuard verdict **not validated**.

Pure-Python environment checks only; no GPU inference installation. Reproduction bypasses repository-wide conftest and pytest addopts to avoid loading GPU fixtures. Must verify imports in the built image.

`instruction.md` is researcher-authored and gives the intended behavior and input/output context. The Dockerfile downloads the unmodified repository snapshot, including tests and license notices; no fix diff, metadata, keys, or Git history are copied into the repository.

Build from MATS:

```bash
docker build -t specguard-vllm-precompiled-flags:demo data/specguard/harbor/vllm-precompiled-flags/environment
```

Reproduction command inside /testbed:

```bash
python -m pytest --noconftest -o addopts= -q tests/test_envs.py::test_precompiled_install_flags_are_orthogonal
```

`tests/test.sh` is a task-test verifier, not a SpecGuard classification evaluator. A nonzero pytest exit must be inspected: import/setup errors are not conflict evidence. Run this task with `uv run specguard-bench --config configs/specguard.yaml --task vllm-precompiled-flags`, or select tasks in the config and omit `--task`. Model settings remain shared in `configs/specguard.yaml`. No task-local model configuration.
