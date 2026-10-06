# SpecGuard: local Python task checking

From an already-set-up Python repository:

```bash
specguard "The parser should accept dates in YYYY-MM-DD format."
```

The CLI discovers relevant existing test functions, prepares a source-only view for
SpecAgent, translates the tests independently, connects the Lean artifacts and checks
them. It never modifies the repository's implementation or tests. Output is saved in
`output/specguard/<model>/<timestamp>/<repository-name>/` in the current repository.

`conflict` means a supported translated demand fails against the generated model.
`no-conflict` requires all selected demands to be supported and pass. `inconclusive`
means the pipeline could not establish either. This is about the automatically selected
tests and generated formalization, not a guarantee about every possible repository test
or implementation. Automatic discovery and formalization can be wrong. Review
`selected_tests.json`, `test_coverage.json`, and the Lean artifacts.

## One-time configuration

Installing this project also installs the `specguard` entry point. There is no separate
Python package. When working on this checkout, `python -m
conflict_certifier.specguard.cli "task"` is equivalent.

Use `~/.config/specguard/config.yaml` (or explicitly `--config FILE`):

```yaml
provider: openai_compatible
model: your-model-id
api_key_env: OPENAI_API_KEY
time_budget: 1800
```

Credentials must be exported in the trusted CLI environment. The CLI does not source a
repository's `.env`, shell startup files, or experiment configs. As a shortcut,
`SPECGUARD_MODEL` supplies the model without YAML. A model starting `claude-` selects
Anthropic and `ANTHROPIC_API_KEY`; slash-qualified models default to OpenRouter and
require `OPENROUTER_API_KEY` and `OPENROUTER_BASE_URL`. Backend fields `provider`,
`api_key_env`, `api_base_env`, and `max_tokens` can be explicit in YAML; `effort` is
accepted for Anthropic only because the shared compatible client does not expose it.
Optional `--time-budget SECONDS` overrides the overall deadline. Provider clients and
their caching/retry behavior are reused unchanged from this project.

## Prepared runtime requirements

- Linux with Bubblewrap (`bwrap`) and usable namespaces. On Debian/Ubuntu an administrator
  can install the `bubblewrap` package. The CLI does not install it or change host
  security settings. Inside Docker, namespace creation must be permitted separately.
- Docker and a Lean/Mathlib/REPL runtime. Existing-directory mode is the default:
  `/mnt/data/lean`, or `SPECGUARD_LEAN_ROOT`, containing `workspace`, `repl`, and
  `elan`, with the local `debian:bookworm-slim` image. Alternatively run
  `specguard setup` once: it downloads pinned Lean 4.24.0, Mathlib and REPL into
  `specguard-lean:4.24.0`, checks a proof with networking disabled, and saves the
  selection in `~/.config/specguard/runtime.json`. This image needs no host Lean
  files. First setup downloads several GB; failed setup leaves the previous choice
  intact. `specguard setup --lean-root /path/to/lean` selects an existing directory
  instead. `SPECGUARD_LEAN_ROOT` overrides saved settings; `SPECGUARD_LEAN_IMAGE`
  selects a built image when no root override exists. No downloads occur during checks.
- Python dependencies available in the environment running the CLI. Runtime packages
  are copied into a restricted view; editable-install hooks, caches, tests and installed
  copies of the selected project are excluded. Projects requiring unusual system data,
  binaries, external services or runtime assets may be inconclusive.

SpecAgent's bash gets a constructed filesystem, not the original checkout or host home.
It has no network or credentials. Source selection sees only file names and packaging
metadata; it cannot forward test contents or prose to SpecAgent. The selected tests and
their helper files are excluded from SpecAgent's source view, even with unconventional
file names. If a file serves both as implementation and required test context it is
excluded from SpecAgent, which may make the task unsupported.

The three formalization agents share one **CLI-owned instance of the existing Lean
REPL setup**, with a separate ownership label. The CLI never sweeps experiment
containers. Namespace/Docker isolation is not a claim against kernel exploits; filtering
cannot recognize arbitrary disguised answers inside approved code or dependencies.

## Evidence and output

`result.json` contains the verdict, evidence (`proof` or `execution`), reason, elapsed
time and selected-test scope. `cert.lean` exists only after successful proof checking.
Execution-only detection saves `execution.lean`; failed proof attempts have separate
names. `verification.json` records toolchain/project files and the container image ID.
Re-check a certificate with `lake env lean /path/to/cert.lean` **in that pinned,
isolated Lean/Mathlib environment**. This checks the formal theorem, not the fidelity
of the natural-language/Python translation.

For the setup-built image, re-check an exported certificate with:

```bash
docker run --rm --network=none --read-only --cap-drop=ALL \
  --security-opt=no-new-privileges \
  -v /absolute/path/to/cert.lean:/tmp/cert.lean:ro \
  specguard-lean:4.24.0 lean /tmp/cert.lean
```

Each agent has its own persistent `*_log.json`, including provider calls. `usage.json`
records completed-call token usage; interrupted provider calls may still incur charges
not returned by the provider. Input hashes, selected source paths and test context are
saved. Do not publish logs without reviewing repository-sensitive content.

Exit codes: 0 no-conflict, 1 conflict, 2 inconclusive/setup failure. stdout contains the
verdict; progress goes to stderr. Timeouts and Ctrl+C preserve partial artifacts and
remove only this invocation's workspace and REPL. SIGKILL or machine failure cannot
run cleanup; such leftovers retain their distinct `specguard.owned` label.

## Verification without paid model calls

```bash
python -m pytest tests/test_specguard_*.py -q
SPECGUARD_LEAN_TEST=1 python -m pytest tests/test_specguard_pipeline.py -m lean -q
SPECGUARD_SANDBOX_TEST=1 python -m pytest tests/test_specguard_workspace.py -q
```

The last two commands explicitly require their prepared local runtimes. Ordinary unit
tests use mocked provider responses and do not start experiments or make paid calls.
