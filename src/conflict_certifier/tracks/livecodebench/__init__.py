"""Lean-only LiveCodeBench conflict-certification track.

The LLM writes one general ``Spec.run``. Hidden structured tests are rendered
into a fixed Lean harness, and the shared evaluator compares the resulting
prediction vector with the private test order.
"""
