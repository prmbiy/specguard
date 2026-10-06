# haystack-stream-usage

Pinned repository: `deepset-ai/haystack@de412b9b8817a577d160d33c3da27834fcb7c6a0`.
Selected original test: `test/components/generators/chat/test_openai.py::TestChatCompletionChunkConversion::test_handle_stream_response`.

Status: Docker image built successfully (image ID and build log in provenance.json). Test execution and SpecGuard verdict **not validated**.

Dependency-sensitive case: the OpenAI SDK version determines the serialized fields. This recipe does NOT pin a verified historical SDK version yet; do not report it as reproduced until the field mismatch is checked. Test uses mocked chunks, not paid API requests.

`instruction.md` is researcher-authored and gives the intended behavior and input/output context. The Dockerfile downloads the unmodified repository snapshot, including tests and license notices; no fix diff, metadata, keys, or Git history are copied into the repository.

Build from MATS:

```bash
docker build -t specguard-haystack-stream-usage:demo data/specguard/harbor/haystack-stream-usage/environment
```

Reproduction command inside /testbed:

```bash
python -m pytest -q test/components/generators/chat/test_openai.py::TestChatCompletionChunkConversion::test_handle_stream_response
```

`tests/test.sh` is a task-test verifier, not a SpecGuard classification evaluator. A nonzero pytest exit must be inspected: import/setup errors are not conflict evidence. Run this task with `uv run specguard-bench --config configs/specguard.yaml --task haystack-stream-usage`, or select tasks in the config and omit `--task`. Model settings remain shared in `configs/specguard.yaml`. No task-local model configuration.
