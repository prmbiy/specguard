# Single-task SpecGuard development demo

This task uses the original pandas snapshot `04a5f9741d754513bbfc26bc2f048ca36bed6725`
and its unchanged `TestOperations.test_assignment_not_inplace` test. The task
description is researcher-written from the `inplace=False` contract. This is an
older defect fixed in 2026, **not** evidence of a newly introduced 2026 defect.

From the MATS repository:

```bash
uv run specguard-bench --task pandas-eval-not-inplace --build
```

After the image is built, omit `--build`. Use `--reproduce-only` to check the
original assertion without LLM calls. Without `--task`, the launcher uses the config's task list. Backend configuration
is `configs/specguard.yaml` at the repository root (override with `--config`);
the default uses Sol through OpenRouter, with a 900-second CLI
deadline. The trusted launcher loads only the selected credentials from the root
`.env` and sends them through stdin. No key is baked into the image.

New evidence is saved under `output/specguard/<model>/<timestamp>/pandas-eval-not-inplace/`:

- `reproduction.log`: real pytest execution of the unchanged test with `--runxfail`.
- `demo.json`: image identity, instruction, and CLI Python source hashes.
- CLI result, agent logs, selected inputs, Lean artifacts and usage are saved directly in that task folder.

The historical test has an xfail marker. `--runxfail` is intentional: this demo
checks its assertion against the stated task, not whether pytest treats an
expected failure as a successful suite exit. No fixed test or gold patch enters
the agent's repository.

The launcher also uses `--noconftest`: this self-contained method has no fixture
arguments, while the repository-wide conftest crashes during an unrelated
datetime fixture initialization in this build. The selected method reproduces
the actual 5x2-versus-5x3 assertion failure. The full suite is **not** validated.

## Runtime boundary

The trusted CLI runs inside the task container and starts the existing Lean REPL
in a separate sibling container with an 8 GiB memory limit (the smaller default
hit memory-reclaim pressure during Mathlib import). For this development launcher only, it receives
the Docker socket and read-only pinned Lean runtime. Agent bash runs in a separate
Bubblewrap filesystem without the socket, credentials, tests, or host checkout.
The trusted outer container has SYS_ADMIN/NET_ADMIN, unmasked system paths, and relaxed seccomp/AppArmor
to allow nested Bubblewrap; agent bash drops all capabilities. This is not a
production-hardened container boundary against kernel exploits.

The `task.toml`, `environment/Dockerfile`, `instruction.md` and `tests/test.sh`
follow Harbor's task layout. The quick demo launcher runs Docker directly rather
than claiming to execute a Harbor job. `tests/test.sh` is a task-test reproduction
verifier; it is not yet a classifier-accuracy evaluator for SpecGuard.

The existing experiment pipelines and results are not modified. Only containers
created by this launcher are removed. Output is retained after completion.
