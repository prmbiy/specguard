"""Persistent Lean REPL backend.

Starts one warm `repl` process, imports Mathlib once (~25s), then runs every
compile check against that warm base environment. Each check is independent —
it sends `{"cmd": ..., "env": base_env_id}` which branches from the base env
without mutating it, so task A's definitions never affect task B.

Protocol (from the REPL readme):
- Send: one JSON object per command, terminated by a blank line.
- Receive: one JSON response object, also terminated by a blank line.
- The `env` field in the response is a numeric label for the resulting environment.
- Using `env: base_env_id` on every check branches from the same clean state.

Framing: the REPL's pretty-printed error messages can contain blank lines inside
the `data` string, so we cannot use a blank line as the response terminator alone;
we accumulate output and stop when we have read a complete blank-line-terminated
message.

Robustness (timeout + self-heal): a runaway tactic (e.g. a diverging `decide`)
would otherwise block the read forever while holding the send lock, freezing the
whole run. To prevent that, every request arms a watchdog timer that kills the
REPL process if it does not respond within `timeout` seconds. Killing it unblocks
the read (EOF), the request returns a `TIMEOUT` error, and the process is marked
dead. The next check lazily restarts the REPL (re-importing Mathlib) so the run
self-heals instead of stalling.

Isolation (always on, not configurable): a submitted spec compiles at
elaboration time, so `#eval`/`IO` in it can otherwise open sockets or read
host files — the compiler is an allowed tool that would silently become a way
out of the sandbox. The REPL therefore always runs inside
`docker run --network=none` with a READ-ONLY mount of the Lean tree and no
other host path visible, so `#eval` has nothing to reach. There is no host
execution path.

The container does NOT run `lake env <repl_bin>` — `lake` revalidates each
package's remote URL on start, which needs write + network + git, none of
which the sandbox may have. Instead we ask `lake env printenv` on the HOST
(once, cached) for the two environment variables (`LEAN_PATH`, `LEAN_SYSROOT`)
the `repl` binary actually needs, and exec `repl` directly inside the
container with those baked in as `-e` flags. This is a one-time path lookup
against the already-resolved project manifest, not code execution, and it
removes `lake`'s own network-touching check from the sandboxed path as a
side benefit.

There is NO fallback to host execution if the container fails to start: see
`LeanRunner._start_repl` in runner.py.
"""

from __future__ import annotations

import functools
import json
import os
import subprocess
import threading
import uuid
from pathlib import Path

from conflict_certifier.config import ConfigError, LeanEnv

# Mathlib import can take ~25s cold; give startup a generous floor regardless of
# the per-check timeout.
_IMPORT_TIMEOUT_FLOOR = 180

# Docker label on every container this module starts, so a crashed/hard-killed
# run's containers can be found and swept (see lean/pool.py).
CONTAINER_LABEL = "conflict_certifier_lean_repl"
_CONTAINER_HOME = "/scratch"
_CONTAINER_TMPFS_SIZE = "512m"


def docker_available() -> bool:
    try:
        r = subprocess.run(["docker", "version"], stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL, timeout=15)
        return r.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _image_present(image: str) -> bool:
    try:
        r = subprocess.run(["docker", "image", "inspect", image],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
        return r.returncode == 0
    except FileNotFoundError:
        return False


@functools.lru_cache(maxsize=None)
def _lake_env_vars(project_dir: str, elan_bin_dir: str) -> dict[str, str]:
    """Ask `lake env` on the HOST which LEAN_PATH/LEAN_SYSROOT `repl` needs.

    Cached per (project_dir, elan_bin_dir) so every pool worker triggers this
    at most once. This runs against the already-resolved project manifest to
    read off two paths — it is a lookup, not an invocation of untrusted code —
    and it deliberately avoids `lake env <repl_bin>` running INSIDE the
    container (see module docstring for why that path fails under isolation).
    """
    env = dict(os.environ)
    env["PATH"] = f"{elan_bin_dir}:{env.get('PATH', '')}"
    try:
        r = subprocess.run(["lake", "env", "printenv"], cwd=project_dir,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True, timeout=60, env=env)
    except FileNotFoundError as e:
        raise ConfigError(f"`lake` not found on PATH (looked under {elan_bin_dir}): {e}") from e
    if r.returncode != 0:
        raise ConfigError(f"`lake env printenv` failed in {project_dir}: {r.stderr.strip()}")
    out: dict[str, str] = {}
    for line in r.stdout.splitlines():
        k, sep, v = line.partition("=")
        if sep and k in ("LEAN_PATH", "LEAN_SYSROOT"):
            out[k] = v
    missing = {"LEAN_PATH", "LEAN_SYSROOT"} - out.keys()
    if missing:
        raise ConfigError(f"`lake env printenv` did not export {sorted(missing)}")
    return out


def mentions_sorry(output: str) -> bool:
    """True if a compile output shows the source used ``sorry``.

    ``sorry`` is warning-only: a file containing it produces no error-severity
    message and ``lake lean`` still exits 0. Anything that reads "compiled" as
    "proved" must screen for it separately, which is what ``reject_sorry`` on
    the compile entry points does.
    """
    return "sorry" in output.lower()


def _terminate_tree(proc: subprocess.Popen) -> None:
    """Kill a process AND its children.

    `proc` is the `docker run` client; the container itself is removed
    separately (`_docker_rm`). Reaping the tree covers any client-side
    children. On Windows, `taskkill /T` reaps the tree.
    """
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10,
            )
        else:
            proc.kill()
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


class LeanRepl:
    """One persistent REPL process with Mathlib pre-loaded.

    Thread-safe via a lock: sends are serialized so multiple worker threads can
    share one process without interleaving pipe reads/writes. A per-request
    watchdog kills a hung process; the next request restarts it automatically.
    """

    def __init__(self, env: LeanEnv):
        self.env = env
        self._proc: subprocess.Popen | None = None
        self._base_env: int | None = None
        self._lock = threading.Lock()
        self._timeout = int(getattr(env, "timeout_seconds", 60) or 60)
        self._cname: str | None = None   # set iff this REPL is a container

    # ---- lifecycle -------------------------------------------------------
    def start(self) -> None:
        if self.env.repl_bin is None:
            raise ConfigError(
                "repl_bin not set in LeanEnv. Add `repl_bin` to the lean: block in your YAML."
            )
        self._proc = self._start_container()
        import_timeout = max(self._timeout, _IMPORT_TIMEOUT_FLOOR)
        base_env, msgs = self._send_unsafe("import Plausible\nimport Mathlib",
                                           env=None, timeout=import_timeout)
        if base_env is None:
            errs = [m.get("data", "") for m in msgs if m.get("severity") == "error"]
            if self._cname is not None:
                self._docker_rm(self._cname)
                self._cname = None
            self._proc = None
            raise ConfigError(f"REPL failed to import Mathlib: {'; '.join(errs)}")
        self._base_env = base_env

    def _start_container(self) -> subprocess.Popen:
        """Launch `repl` inside a `--network=none` container. No fallback: any
        failure here (Docker missing, image missing, `lake env printenv`
        failing) raises and the caller does not get a REPL."""
        env = self.env
        if not docker_available():
            raise ConfigError(
                "Docker is not available, and Lean is only ever compiled inside "
                "a sandboxed container (no host fallback exists). Install/start "
                "Docker to run."
            )
        if not _image_present(env.container_image):
            raise ConfigError(
                f"Lean container image '{env.container_image}' not found locally. "
                f"Pull it with: docker pull {env.container_image}"
            )
        elan_bin = str(env.container_mount_root / "elan" / "bin")
        lake_vars = _lake_env_vars(str(env.project_dir), elan_bin)
        self._cname = f"lean-repl-{os.getpid()}-{uuid.uuid4().hex[:12]}"
        mount = str(env.container_mount_root)
        path = (f"{lake_vars['LEAN_SYSROOT']}/bin:"
                "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin")
        argv = [
            "docker", "run", "-i", "--rm", "--name", self._cname,
            "--network=none",
            "--memory", env.container_memory, "--memory-swap", env.container_memory,
            "--pids-limit", str(env.container_pids_limit),
            "--label", CONTAINER_LABEL,
            "--tmpfs", f"{_CONTAINER_HOME}:rw,size={_CONTAINER_TMPFS_SIZE},mode=1777",
            "-v", f"{mount}:{mount}:ro",
            "-w", str(env.project_dir),
            "-e", f"HOME={_CONTAINER_HOME}",
            "-e", f"LEAN_SYSROOT={lake_vars['LEAN_SYSROOT']}",
            "-e", f"LEAN_PATH={lake_vars['LEAN_PATH']}",
            "-e", f"PATH={path}",
            env.container_image,
            str(env.repl_bin),
        ]
        return subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL)

    @staticmethod
    def _docker_rm(cname: str) -> None:
        """Force-remove a container by name. Idempotent: safe to call on a
        name that already exited/was removed (`--rm` beat us to it)."""
        try:
            subprocess.run(["docker", "rm", "-f", cname],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
        except Exception:
            pass

    def _teardown(self, proc: subprocess.Popen) -> None:
        """Stop `proc`, and in container mode remove its container.

        Stronger than the host path: `docker rm -f` drops the container's
        whole PID namespace, so there is no orphaned-`repl.exe`-child problem
        the way killing bare `lake` on the host has (see `_terminate_tree`).
        """
        if self._cname is not None:
            cname, self._cname = self._cname, None
            self._docker_rm(cname)
        _terminate_tree(proc)

    def _ensure_alive_unsafe(self) -> None:
        """Restart the REPL if it has never started or was killed after a timeout.

        Called with the lock held. Restarting re-imports Mathlib (~25s) and
        establishes a fresh base env; it happens at most once per death.
        """
        if self._proc is None or self._proc.poll() is not None:
            print("[LeanRepl] (re)starting REPL + re-importing Mathlib ...", flush=True)
            self.start()

    def close(self) -> None:
        if self._proc is not None:
            try:
                if self._proc.stdin:
                    self._proc.stdin.close()
                self._proc.wait(timeout=5)
                # Clean exit: `--rm` already removed the container. Still
                # clear the name so a stale one can't be double-removed later.
                self._cname = None
            except Exception:
                self._teardown(self._proc)
            self._proc = None
        self._cname = None

    def __enter__(self) -> "LeanRepl":
        self.start()
        return self

    def __exit__(self, *_) -> None:
        self.close()

    # ---- protocol --------------------------------------------------------
    def _send_unsafe(self, cmd: str, env: int | None,
                     timeout: int) -> tuple[int | None, list[dict]]:
        """Send one command and read one response. NOT lock-protected.

        Arms a watchdog that kills the process after `timeout` seconds so a
        diverging check cannot block forever. On timeout/death, `self._proc` is
        cleared so the next call restarts the REPL.
        """
        assert self._proc and self._proc.stdin and self._proc.stdout
        proc = self._proc
        payload: dict = {"cmd": cmd}
        if env is not None:
            payload["env"] = env
        proc.stdin.write((json.dumps(payload) + "\n\n").encode())
        proc.stdin.flush()

        timed_out = threading.Event()

        def _kill() -> None:
            timed_out.set()
            self._teardown(proc)

        timer = threading.Timer(timeout, _kill)
        timer.start()
        try:
            raw = b""
            while True:
                chunk = proc.stdout.readline()
                if chunk == b"\n" and raw.strip():
                    break
                raw += chunk
                if not chunk and proc.poll() is not None:
                    # process ended — killed by the watchdog or crashed
                    self._proc = None
                    if timed_out.is_set():
                        return None, [{"severity": "error",
                                       "data": f"TIMEOUT after {timeout}s"}]
                    return None, [{"severity": "error", "data": "REPL process died"}]
        finally:
            timer.cancel()

        try:
            resp = json.loads(raw.decode())
        except json.JSONDecodeError as e:
            return None, [{"severity": "error", "data": f"REPL parse error: {e}"}]
        return resp.get("env"), resp.get("messages", [])

    # ---- compile ---------------------------------------------------------
    def compile(self, lean_source: str, *, timeout: int | None = None,
                reject_sorry: bool = True) -> tuple[bool, str]:
        """Run lean_source against the warm Mathlib base env. Returns (ok, output).

        Serialized across threads; restarts the REPL first if it died. A check
        that exceeds `timeout` (default: env.timeout_seconds) returns
        `ok=False` with a `TIMEOUT` message instead of hanging the run.

        `reject_sorry` (default True) fails a compile that Lean accepted only
        because the source contains a hole. Pass False where `sorry` is
        deliberate and provably harmless — specifically the
        `#guard decide (postcond .. (by sorry))` idiom, where the precondition
        proof is proof-irrelevant under decidable evaluation and so cannot
        change the guard's verdict.
        """
        # Strip import lines — Mathlib/Plausible are already in the base env
        body = "\n".join(
            line for line in lean_source.splitlines()
            if not line.strip().startswith("import ")
        )
        t = int(timeout or self._timeout)
        with self._lock:
            self._ensure_alive_unsafe()
            _, messages = self._send_unsafe(body, env=self._base_env, timeout=t)
        ok = not any(m.get("severity") == "error" for m in messages)
        output = "\n".join(
            f"{m.get('severity', '?')}: {m.get('data', '')}" for m in messages
        )
        if ok and reject_sorry and mentions_sorry(output):
            return False, output + "\nerror: rejected — declaration uses 'sorry'"
        return ok, output
