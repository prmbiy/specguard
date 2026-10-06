"""Benchmark-specific certification tracks.

Each track is a mostly self-contained pipeline that reuses the shared plumbing
(`lean/`, `llm/`, `config`, `model`) and, where useful, the Gen-A core
(`sources/`, `conflicts/`, `certifiers/`):

  livecodebench/  Lean SpecAgent plus fixed hidden-test evaluation
                  the spec against a corrupted test  (specgen / codegen / prove).
  swebench/       Same idea on real GitHub issues, via a three-agent split.

The Gen-A core (Verina + ImpossibleBench, where a formal spec already exists and
certification is deterministic) stays at the top level, not under tracks/.
"""
