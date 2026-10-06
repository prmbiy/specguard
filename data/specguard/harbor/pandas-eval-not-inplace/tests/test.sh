#!/bin/bash
set -uo pipefail
mkdir -p /logs/verifier
cd /testbed
# The historical test is marked xfail; --runxfail exposes its real assertion.
python -m pytest --noconftest pandas/tests/computation/test_eval.py::TestOperations::test_assignment_not_inplace --runxfail -q
status=$?
if [ "$status" -eq 0 ]; then
    echo 1 > /logs/verifier/reward.txt
else
    echo 0 > /logs/verifier/reward.txt
fi
exit "$status"
