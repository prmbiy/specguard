Preserve standalone-document support in add_document_note_tool: when context.corpus is absent, forward None as the corpus identifier instead of rejecting the call for lacking a corpus. When present, forward context.corpus.id. Other tools that require a corpus retain their existing validation. Inputs are authenticated document-note operations with optional corpus context; the observable is the forwarded corpus identifier and whether the operation is rejected. Satisfy opencontractserver/tests/test_llms_typing_behavior_guards.py::TestAddDocumentNoteToolCorpusOptional::test_add_document_note_tool_passes_none_when_corpus_absent while preserving these behaviors.

## Observation context

The following describes setup and observable shapes, not additional behavioral requirements or expected assertion results.

- Setup: resolve the project root and read opencontractserver/llms/agents/pydantic_ai_agents.py as text. The selected check does not invoke add_document_note_tool.
- Observations: substring presence/absence in that complete source file, rather than only a returned corpus identifier or raised exception.
- Output shape: source text as a string with the ability to represent string-membership observations, alongside the optional-corpus runtime behavior described above. Observing source text does not impose a particular Python spelling as an additional intended-behavior requirement.
