# opencontracts-optional-corpus

Pinned repository: `Open-Source-Legal/OpenContracts@68a1264498a724065c96aa0feee163fec585eff5`.
Selected original test: `opencontractserver/tests/test_llms_typing_behavior_guards.py::TestAddDocumentNoteToolCorpusOptional::test_add_document_note_tool_passes_none_when_corpus_absent`.

Status: Docker image built successfully (image ID and build log in provenance.json). Test execution and SpecGuard verdict **not validated**.

Source-inspection assertion in a Django TestCase, not a simple input/output execution test. Project Django settings/database services still require validation; this is a packaging draft, not an offline-ready reproduction. No assertion or fixture is replaced.

`instruction.md` is researcher-authored and gives the intended behavior and input/output context. The Dockerfile downloads the unmodified repository snapshot, including tests and license notices; no fix diff, metadata, keys, or Git history are copied into the repository.

Build from MATS:

```bash
docker build -t specguard-opencontracts-optional-corpus:demo data/specguard/harbor/opencontracts-optional-corpus/environment
```

Reproduction command inside /testbed:

```bash
python -m pytest -q opencontractserver/tests/test_llms_typing_behavior_guards.py::TestAddDocumentNoteToolCorpusOptional::test_add_document_note_tool_passes_none_when_corpus_absent
```

`tests/test.sh` is a task-test verifier, not a SpecGuard classification evaluator. A nonzero pytest exit must be inspected: import/setup errors are not conflict evidence. Run this task with `uv run specguard-bench --config configs/specguard.yaml --task opencontracts-optional-corpus`, or select tasks in the config and omit `--task`. Model settings remain shared in `configs/specguard.yaml`. No task-local model configuration.
