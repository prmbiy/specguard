#!/bin/bash
set -uo pipefail
mkdir -p /logs/verifier
cd /testbed
python -m pytest -o addopts= -q tests/augmentation/test_augmentation_mix.py::TestRandomMixUpV2::test_random_mixup_p0 --device cpu --dtype bfloat16 --xfail-known-failures
status=$?
if [ "$status" -eq 0 ]; then
    echo 1 > /logs/verifier/reward.txt
else
    echo 0 > /logs/verifier/reward.txt
fi
exit "$status"
