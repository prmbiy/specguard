#!/bin/bash
set -uo pipefail
mkdir -p /logs/verifier
cd /testbed
python -m pytest -q opencontractserver/tests/test_llms_typing_behavior_guards.py::TestAddDocumentNoteToolCorpusOptional::test_add_document_note_tool_passes_none_when_corpus_absent
status=$?
if [ "$status" -eq 0 ]; then
    echo 1 > /logs/verifier/reward.txt
else
    echo 0 > /logs/verifier/reward.txt
fi
exit "$status"
