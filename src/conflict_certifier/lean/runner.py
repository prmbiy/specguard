"""Lean compiler interface — sandboxed REPL, always.

The REPL imports Mathlib once and serves all checks from that warm state at
~0.1s each. One LeanRunner instance = one REPL (or pool) = one Mathlib load,
shared across every compile call on that runner.

Every compile happens inside the `--network=none` container the REPL runs in
(see lean/repl.py). There is deliberately NO host compile path in this module:
model-written Lean runs `#eval`/`IO` at elaboration time, so an unsandboxed
`lake lean` entry point — even an unused one — is an internet/filesystem hole
waiting to be wired up.
"""

from __future__ import annotations

from conflict_certifier.config import ConfigError, LeanEnv, resolve_lean_env


class LeanRunner:
    """Compile interface backed by a single persistent REPL (one Mathlib load)."""

    def __init__(self, env: LeanEnv | None = None):
        self.env = env or resolve_lean_env()
        self.env.validate()
        self._repl = None
        self._start_repl()

    def _start_repl(self) -> None:
        """Start one sandboxed REPL, or a pool of them when repl_processes > 1.

        NO fallback: if the sandboxed REPL fails to start, that exception
        propagates and this runner has no compile backend, on purpose —
        falling back to a host compile would silently drop the
        network/filesystem isolation the sandbox exists for.
        """
        n = max(1, int(getattr(self.env, "repl_processes", 1) or 1))
        # Sweep before booting: a previously hard-killed run may have left
        # containers behind (SIGKILL/OOM skip `--rm`'s normal-exit path),
        # and those compete for this run's RAM budget if not cleared.
        from conflict_certifier.lean.pool import cleanup_orphaned_lean_containers
        cleanup_orphaned_lean_containers()
        if n > 1:
            from conflict_certifier.lean.pool import LeanReplPool
            r = LeanReplPool(self.env, size=n)
        else:
            from conflict_certifier.lean.repl import LeanRepl
            r = LeanRepl(self.env)
        r.start()   # no try/except: let startup failures propagate
        self._repl = r

    @property
    def backend(self) -> str:
        size = getattr(self._repl, "size", 1)
        return "repl" if size == 1 else f"repl-pool({size})"

    def compile(self, lean_source: str, *, name: str = "cert",
                reject_sorry: bool = True) -> tuple[bool, str]:
        """Check lean_source. Returns (ok, output).

        ok is True iff Lean reported no errors AND — unless `reject_sorry` is
        False — the source contains no `sorry`. Rejecting holes here rather than
        in each caller means a new call site cannot accidentally count a
        hole-containing proof as a certificate.

        Raises if the sandboxed REPL is not running (e.g. after close()) —
        there is no unsandboxed compile to fall back to.
        """
        if self._repl is None:
            raise ConfigError(
                "Lean REPL is not running; refusing to compile (no unsandboxed "
                "host fallback exists)."
            )
        return self._repl.compile(lean_source, reject_sorry=reject_sorry)

    def close(self) -> None:
        if self._repl is not None:
            self._repl.close()
            self._repl = None
