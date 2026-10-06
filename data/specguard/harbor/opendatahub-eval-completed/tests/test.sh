#!/bin/bash
set -uo pipefail
mkdir -p /logs/verifier
cd /testbed
python -m pytest -o addopts= -q tests/ai_safety/evalhub/k8s_lifecycle_signals/test_evalhub_lifecycle_job_label_phase.py::TestLblJobLabelPhase::test_lbl_002_job_label_set_to_succeeded_on_completion
status=$?
if [ "$status" -eq 0 ]; then
    echo 1 > /logs/verifier/reward.txt
else
    echo 0 > /logs/verifier/reward.txt
fi
exit "$status"
