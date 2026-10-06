#!/bin/bash
set -uo pipefail
mkdir -p /logs/verifier
cd /testbed
python -m pytest -o addopts= -q tests/cacl/test_cacl_application.py::test_cacl_application_nondualtor
status=$?
if [ "$status" -eq 0 ]; then
    echo 1 > /logs/verifier/reward.txt
else
    echo 0 > /logs/verifier/reward.txt
fi
exit "$status"
