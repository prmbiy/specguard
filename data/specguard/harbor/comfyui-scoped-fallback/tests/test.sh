#!/bin/bash
set -uo pipefail
mkdir -p /logs/verifier
cd /testbed
python -m pytest --noconftest -o addopts= -q tests/test_install_flags_structural.py::JsCopyStructuralTest::test_generic_fallback_and_frozen_callers_unchanged
status=$?
if [ "$status" -eq 0 ]; then
    echo 1 > /logs/verifier/reward.txt
else
    echo 0 > /logs/verifier/reward.txt
fi
exit "$status"
