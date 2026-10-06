"""Project paths and the Lean environment descriptor.

The only machine-specific dependency is a prebuilt Lean 4 (v4.24.0) + Mathlib
environment used to *check* certificates. We never import code from it; every
check runs through a REPL inside a ``--network=none`` container with the Lean
tree mounted read-only (see ``lean/repl.py``).

Benchmark runners carry the ``lean_project`` path in their YAML configuration
or CLI flags. ``LEAN_CONTAINER_MOUNT_ROOT`` relocates the container's read-only
Lean mount. ``resolve_lean_env`` below also supports ``config.local.toml`` for
legacy local tests.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover - fallback for 3.10
    try:
        import tomli as tomllib  # type: ignore
    except ModuleNotFoundError:
        tomllib = None  # type: ignore

# Project roots (this file lives at src/conflict_certifier/config.py).
PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_DIR.parent.parent

VENDOR_DIR = PROJECT_ROOT / "vendor"
VERINA_DIR = VENDOR_DIR / "verina"
VERINA_DATASET_DIR = VERINA_DIR / "datasets" / "verina"

DATA_DIR = PROJECT_ROOT / "data"
GENERATED_SPECS_DIR = DATA_DIR / "verina" / "generated" / "specs"
GENERATED_CODE_DIR = DATA_DIR / "verina" / "generated" / "code"

# Each run writes its own self-describing directory here.
RUNS_DIR = PROJECT_ROOT / "output" / "runs"

_LOCAL_CONFIG = PROJECT_ROOT / "config.local.toml"


class ConfigError(RuntimeError):
    """Raised when required configuration is missing or invalid."""


def expand_path(raw: str | Path) -> Path:
    """Expand ``$VARS`` and ``~`` in a config path, then resolve it.

    Configs carry absolute Lean paths and this tree has moved between machines
    more than once. Writing ``$LEAN_ROOT/workspace`` means one export retargets
    every config; a plain absolute path still works unchanged.
    """
    return Path(os.path.expandvars(str(raw))).expanduser().resolve()


@dataclass(frozen=True)
class LeanEnv:
    """Location of a prebuilt Lean + Mathlib project used to check certificates."""

    project_dir: Path
    timeout_seconds: int = 60
    workers: int = 4               # concurrent agent loops (LLM calls), not Lean
    repl_bin: Path | None = None   # path to the repl binary; enables REPL backend
    # Warm REPL processes to keep. Independent of `workers`: a worker that needs
    # a check borrows whichever REPL is idle, uses it exclusively, returns it.
    # >1 stops one slow `decide` from blocking every other worker. Budget
    # ~2.5GB RAM each.
    repl_processes: int = 1

    # Isolation: every REPL runs inside a `--network=none` container, so a
    # submitted spec's `#eval`/`IO` has no internet and no host filesystem
    # beyond a read-only Mathlib mount. This is a security boundary, NOT a
    # config knob: there is no flag to turn it off and no fallback to host
    # execution if the container fails to start (see LeanRunner._start_repl /
    # LeanRepl.start). Only the container's parameters below are configurable.
    container_image: str = "debian:bookworm-slim"
    # Read-only mount root; must be an ancestor of both project_dir and
    # repl_bin so the same absolute paths resolve inside the container
    # (elan/lake bake absolute paths into manifests).
    container_mount_root: Path = Path("/mnt/data/lean")
    container_memory: str = "3500m"    # must exceed the ~2.5GB Mathlib working set
    container_pids_limit: int = 512    # Lean is heavily threaded; do not set low

    # Flat spellings used by the TOML configs and the swebench CLI, mapped onto
    # the canonical nested-YAML names.
    _ALIASES = {
        "lean_project": "project",
        "lean_timeout_seconds": "timeout",
        "lean_timeout": "timeout",
        "lean_container": "container",
        "lean_container_image": "container_image",
        "lean_container_mount_root": "container_mount_root",
        "lean_container_memory": "container_memory",
        "lean_container_pids_limit": "container_pids_limit",
    }

    @classmethod
    def from_dict(cls, d: dict) -> "LeanEnv":
        """The single parser for every Lean config source.

        Canonical keys: ``project``, ``repl_bin``, ``repl_processes``,
        ``timeout``, ``workers``, ``container_image``,
        ``container_mount_root``, ``container_memory``, ``container_pids_limit``.
        Unknown keys are ignored, so a whole run config can be passed in as-is —
        EXCEPT ``container``/``lean_container``, which is rejected loudly: the
        sandbox is always on and a config claiming to disable it must not be
        silently ignored.

        This exists because the same parsing was previously written out three
        times — in ``resolve_lean_env``, ``RunConfig.lean_env`` and the stage
        loader's ``_lean_env`` — and they drifted: one of them silently dropped
        ``repl_bin``, so those runs fell back to ``lake lean`` (~6s vs ~0.01s
        per check) with nothing in the logs to say so. One parser means a new
        knob is added once, not three times.
        """
        d = {cls._ALIASES.get(k, k): v for k, v in d.items() if v is not None}
        if "container" in d:
            raise ConfigError(
                "`container`/`lean_container` is no longer a config option: the "
                "Lean sandbox (--network=none container) is always on. Remove "
                "the key from your config."
            )
        if not d.get("project"):
            raise ConfigError(
                "Lean config requires `project` (or the flat `lean_project`)."
            )
        repl = d.get("repl_bin")
        defaults = cls.__dataclass_fields__
        mount_root = d.get("container_mount_root") or os.environ.get("LEAN_CONTAINER_MOUNT_ROOT")
        return cls(
            project_dir=expand_path(d["project"]),
            timeout_seconds=int(d.get("timeout", 60)),
            workers=int(d.get("workers", 4)),
            repl_bin=expand_path(repl) if repl else None,
            repl_processes=int(d.get("repl_processes", 1)),
            container_image=str(d.get("container_image", defaults["container_image"].default)),
            container_mount_root=(expand_path(mount_root) if mount_root
                                  else defaults["container_mount_root"].default),
            container_memory=str(d.get("container_memory", defaults["container_memory"].default)),
            container_pids_limit=int(d.get("container_pids_limit",
                                           defaults["container_pids_limit"].default)),
        )

    def validate(self) -> None:
        if not self.project_dir.is_dir():
            raise ConfigError(
                f"Lean project dir does not exist: {self.project_dir}. "
                "Set lean_project in your run config."
            )
        if not (self.project_dir / "lean-toolchain").exists():
            raise ConfigError(
                f"No lean-toolchain found in {self.project_dir}."
            )
        if self.repl_bin is not None and not self.repl_bin.exists():
            raise ConfigError(f"repl_bin does not exist: {self.repl_bin}")
        if self.repl_processes < 1:
            raise ConfigError(f"repl_processes must be >= 1, got {self.repl_processes}")
        # The sandbox is REPL-only (there is no sandboxed `lake lean`), and it
        # is always on, so repl_bin is mandatory.
        if self.repl_bin is None:
            raise ConfigError(
                "repl_bin is required: Lean always compiles inside the sandboxed "
                "REPL container, and no host fallback exists. Set repl_bin (or "
                "the flat `repl_bin` key) in your config."
            )
        if not self.container_mount_root.is_dir():
            raise ConfigError(
                f"container_mount_root does not exist: {self.container_mount_root}"
            )
        for label, p in (("project_dir", self.project_dir), ("repl_bin", self.repl_bin)):
            try:
                p.relative_to(self.container_mount_root)
            except ValueError:
                raise ConfigError(
                    f"{label} ({p}) is not inside container_mount_root "
                    f"({self.container_mount_root}) — the container can only "
                    "see paths under the mount root."
                ) from None


def _read_local_config() -> dict:
    if _LOCAL_CONFIG.exists():
        if tomllib is None:
            raise ConfigError(
                f"{_LOCAL_CONFIG} exists but no TOML parser is available "
                "(Python <3.11 without 'tomli'). Use the conda 'verina' env."
            )
        try:
            with open(_LOCAL_CONFIG, "rb") as f:
                return tomllib.load(f)
        except Exception as exc:
            raise ConfigError(f"Invalid TOML in {_LOCAL_CONFIG}: {exc}") from exc
    return {}


def resolve_lean_env() -> LeanEnv:
    """Convenience resolver for tests, reading config.local.toml.

    Production runs use a per-run config via ``run_config.RunConfig`` instead.
    """
    cfg = _read_local_config()
    raw = cfg.get("lean_project")
    if not raw:
        raise ConfigError(
            "No lean_project configured. Add `lean_project = \"/path/to/lean-project\"` "
            f"to {_LOCAL_CONFIG} (for tests), or use a run config under configs/."
        )
    # The whole local config goes in; from_dict reads the lean keys and ignores
    # the rest (source, limit, certifiers, ...).
    return LeanEnv.from_dict(cfg)
