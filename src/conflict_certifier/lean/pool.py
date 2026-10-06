"""Parallel Lean compilation across independent sources.

``LeanReplPool`` — N warm sandboxed REPL processes behind one ``compile()``.
A single REPL serializes every check behind one lock, so one slow ``decide``
blocks every agent worker waiting to compile, and a watchdog kill costs all
of them a Mathlib reload. N processes turn that into N independent lanes.

``run_many`` is unrelated to Lean — it is the thread pool that runs independent
per-task agent loops (LLM calls) concurrently.

Sizing the pool is a RAM question, not a CPU one: budget ~3.5GB per REPL
container — see ``LeanEnv.container_memory``.
"""

from __future__ import annotations

import queue
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from conflict_certifier.config import LeanEnv
from conflict_certifier.lean.repl import CONTAINER_LABEL, LeanRepl


def cleanup_orphaned_lean_containers() -> None:
    """Force-remove any lean-repl containers left over from a prior run that
    was hard-killed before its own teardown ran (SIGKILL, OOM, crash).

    `--rm` handles the normal-exit case; this is the backstop for the
    abnormal one, so a killed run can't leak containers that then compete for
    the RAM budget of the next run. Safe to call with no orphans present.
    """
    try:
        r = subprocess.run(
            ["docker", "ps", "-aq", "--filter", f"label={CONTAINER_LABEL}"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=15,
        )
    except FileNotFoundError:
        return
    ids = r.stdout.split()
    if ids:
        subprocess.run(["docker", "rm", "-f", *ids],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)


class LeanReplPool:
    """N persistent REPLs sharing one queue. Drop-in for :class:`LeanRepl`.

    Each ``LeanRepl`` already serializes its own access and lazily restarts
    itself after a watchdog kill, so the pool only has to hand out an idle
    process. A ``queue.Queue`` of instances is the entire scheduler: ``get()``
    blocks until one frees up, and the ``finally`` returns it even when the
    check timed out — that instance simply re-imports Mathlib on its next use,
    while the other lanes keep serving.

    Checks stay independent: every process branches from its *own* base env, so
    which lane a check lands on cannot change its result.
    """

    def __init__(self, env: LeanEnv, size: int = 2):
        self.env = env
        self._repls = [LeanRepl(env) for _ in range(max(1, int(size)))]
        self._idle: queue.Queue = queue.Queue()

    @property
    def size(self) -> int:
        return len(self._repls)

    def start(self) -> None:
        """Boot every REPL concurrently — serial starts would cost N × ~5s."""
        errors: list[BaseException] = []

        def _boot(r: LeanRepl) -> None:
            try:
                r.start()
                self._idle.put(r)
            except BaseException as e:  # noqa: BLE001 - re-raised below
                errors.append(e)

        threads = [threading.Thread(target=_boot, args=(r,)) for r in self._repls]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        if errors:
            self.close()
            raise errors[0]

    def compile(self, lean_source: str, *, timeout: int | None = None,
                reject_sorry: bool = True) -> tuple[bool, str]:
        repl = self._idle.get()
        try:
            return repl.compile(lean_source, timeout=timeout,
                                reject_sorry=reject_sorry)
        finally:
            self._idle.put(repl)

    def close(self) -> None:
        for r in self._repls:
            try:
                r.close()
            except Exception:
                pass

    def __enter__(self) -> "LeanReplPool":
        self.start()
        return self

    def __exit__(self, *_) -> None:
        self.close()


def run_many(
    tasks: list,
    fn: Callable,
    workers: int = 4,
) -> list:
    """Run fn(task) over tasks in a thread pool. Order preserved.

    Used to run independent per-task agent loops concurrently (each task does its
    own LLM calls + serial isolated compiles).
    """
    results: list = [None] * len(tasks)

    def _work(idx_task):
        idx, task = idx_task
        return idx, fn(task)

    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        for idx, res in ex.map(_work, enumerate(tasks)):
            results[idx] = res
    return results
