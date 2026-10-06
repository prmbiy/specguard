#!/bin/bash
set -uo pipefail
mkdir -p /logs/verifier
cd /testbed
python -m pytest -o addopts= -q unittests/api_v3/test_apiv3_subresources.py::TestApiV3SubresourcesNotePrivacy::test_private_note_visible_to_other_authorized_user_v2_parity
status=$?
if [ "$status" -eq 0 ]; then
    echo 1 > /logs/verifier/reward.txt
else
    echo 0 > /logs/verifier/reward.txt
fi
exit "$status"
