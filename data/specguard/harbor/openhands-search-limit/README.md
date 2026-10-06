# openhands-search-limit

Pinned repository: `OpenHands/software-agent-sdk@29383741587f95edd09295e9c6727c14ac7e8622`.
Selected original test: `tests/agent_server/test_conversation_router.py::test_search_conversations_limit_validation`.

Status: Docker image built successfully (image ID and build log in provenance.json). Test execution and SpecGuard verdict **not validated**.

The original test may PASS against the buggy implementation; a Python failure is not required to establish the task/test contradiction. The full locked workspace has substantial dependencies.

`instruction.md` is researcher-authored and gives the intended behavior and input/output context. The Dockerfile downloads the unmodified repository snapshot, including tests and license notices; no fix diff, metadata, keys, or Git history are copied into the repository.

Build from MATS:

```bash
docker build -t specguard-openhands-search-limit:demo data/specguard/harbor/openhands-search-limit/environment
```

Reproduction command inside /testbed:

```bash
python -m pytest -q tests/agent_server/test_conversation_router.py::test_search_conversations_limit_validation
```

`tests/test.sh` is a task-test verifier, not a SpecGuard classification evaluator. A nonzero pytest exit must be inspected: import/setup errors are not conflict evidence. Run this task with `uv run specguard-bench --config configs/specguard.yaml --task openhands-search-limit`, or select tasks in the config and omit `--task`. Model settings remain shared in `configs/specguard.yaml`. No task-local model configuration.
