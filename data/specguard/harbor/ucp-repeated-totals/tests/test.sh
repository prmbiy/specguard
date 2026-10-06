#!/bin/bash
set -uo pipefail
mkdir -p /logs/verifier
cd /testbed
python -m pytest -o addopts= -q business_logic_test.py
status=$?
if [ "$status" -eq 0 ]; then
    echo 1 > /logs/verifier/reward.txt
else
    echo 0 > /logs/verifier/reward.txt
fi
exit "$status"
