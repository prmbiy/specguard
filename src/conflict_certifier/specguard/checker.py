"""Label-independent CLI checking, using the existing Lean REPL runtime."""
from __future__ import annotations

import contextlib
import json
import os
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path
from .layout import artifact_path

from conflict_certifier.config import ConfigError, LeanEnv
from conflict_certifier.lean.repl import LeanRepl, _image_present, docker_available

from .artifacts import GuardError, TestArtifact, assemble, write_json
from .runtime import ImageLeanEnv, lean_environment


def _lake_env_vars(project: str, elan_bin: str) -> dict[str, str]:
    # Resolve the pinned runtime, not whichever toolchain the caller's HOME uses.
    env = dict(os.environ)
    env.update(ELAN_HOME=str(Path(elan_bin).parent), PATH=f"{elan_bin}:{env.get('PATH', '')}")
    proc = subprocess.run([str(Path(elan_bin) / "lake"), "env", "printenv"],
                          cwd=project, env=env, capture_output=True, text=True, timeout=60)
    values = dict(line.split("=", 1) for line in proc.stdout.splitlines()
                  if line.startswith(("LEAN_PATH=", "LEAN_SYSROOT=")))
    if proc.returncode or set(values) != {"LEAN_PATH", "LEAN_SYSROOT"}:
        raise GuardError("Cannot resolve the pinned Lean runtime environment")
    return values


class OwnedRepl(LeanRepl):
    """Same REPL/image/toolchain, separate ownership label; no orphan sweeps."""

    def _ensure_alive_unsafe(self):
        # Same restart behavior, without interleaving chatter with tqdm.
        if self._proc is None or self._proc.poll() is not None:
            self.start()

    def start(self):
        # A REPL can return an environment ID even when an import produced errors.
        # Check both; never let agents work against a silently empty environment.
        self._proc = self._start_container()
        base, messages = self._send_unsafe("import Plausible\nimport Mathlib", env=None, timeout=180)
        errors = [m.get("data", "") for m in messages if m.get("severity") == "error"]
        if base is None or errors:
            self.close()
            raise GuardError("Lean runtime import failed: " + "; ".join(errors))
        self._base_env = base
        _, probe = self._send_unsafe("#check Nat\n#check Rat", env=base, timeout=15)
        errors = [m.get("data", "") for m in probe if m.get("severity") == "error"]
        if errors:
            self.close()
            raise GuardError("Lean runtime validation failed: " + "; ".join(errors))

    def _start_container(self):
        env = self.env
        managed = isinstance(env, ImageLeanEnv)
        values = {} if managed else _lake_env_vars(str(env.project_dir), str(env.container_mount_root / "elan/bin"))
        self._cname = f"specguard-lean-{os.getpid()}-{uuid.uuid4().hex[:12]}"
        argv = ["docker", "run", "-i", "--rm", "--name", self._cname,
                "--network=none", "--cap-drop=ALL", "--security-opt=no-new-privileges",
                "--memory", env.container_memory, "--memory-swap", env.container_memory,
                "--pids-limit", str(env.container_pids_limit),
                "--label", "specguard.owned=true", "--label", f"specguard.pid={os.getpid()}",
                "--tmpfs", "/tmp:rw,size=256m,mode=1777",
                *(["--read-only"] if managed else ["-v", f"{env.container_mount_root}:{env.container_mount_root}:ro"]),
                "-w", str(env.project_dir), "-e", "HOME=/tmp"]
        if managed:
            argv += [env.container_image]
        else:
            argv += [
                "-e", f"LEAN_SYSROOT={values['LEAN_SYSROOT']}",
                "-e", f"LEAN_PATH={values['LEAN_PATH']}",
                "-e", f"PATH={values['LEAN_SYSROOT']}/bin:/usr/bin:/bin",
                env.container_image, str(env.repl_bin)]
        return subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, start_new_session=True)

    def close(self):
        # Also handles interrupted startup before _proc is assigned.
        name = self._cname
        try:
            super().close()
        finally:
            if name:
                self._docker_rm(name)


class Checker:
    def __init__(self, output: Path, env: LeanEnv | None = None):
        self.output = output
        self.env = env or lean_environment()
        from .repl_service import RemoteRepl
        self.repl = RemoteRepl() if os.environ.get("SPECGUARD_REPL_SOCKET") else OwnedRepl(self.env)
        self.timeout = 120
        self.records: list[dict] = []

    @staticmethod
    def preflight(env: LeanEnv | None = None) -> None:
        if os.environ.get("SPECGUARD_REPL_SOCKET"):
            from .repl_service import RemoteRepl
            RemoteRepl().start()
            return
        env = env or lean_environment()
        try:
            env.validate()
        except ConfigError as exc:
            raise GuardError(f"{exc} Run specguard setup to install Lean in Docker, or set SPECGUARD_LEAN_ROOT to an existing installation.") from exc
        if not docker_available() or not _image_present(env.container_image):
            raise GuardError(f"Lean runtime unavailable: Docker and local image {env.container_image} are required. Run specguard setup or set SPECGUARD_LEAN_ROOT.")

    def start(self) -> None:
        with contextlib.redirect_stdout(sys.stderr):
            self.repl.start()
        runtime = {"lean_project": str(self.env.project_dir), "repl_bin": str(self.env.repl_bin),
                   "backend": "image" if isinstance(self.env, ImageLeanEnv) else "host",
                   "container_image": self.env.container_image, "container_mount_root": str(self.env.container_mount_root),
                   "recheck_command": "lake env lean cert.lean (inside this pinned Lean/Mathlib project)",
                   "files": {}}
        for name in ("lean-toolchain", "lake-manifest.json", "lakefile.lean", "lakefile.toml"):
            path = self.env.project_dir / name
            if isinstance(self.env, ImageLeanEnv):
                p = subprocess.run(["docker", "run", "--rm", "--network=none", "--read-only",
                                    "--cap-drop=ALL", "--security-opt=no-new-privileges",
                                    "--entrypoint", "cat", self.env.container_image, str(path)],
                                   capture_output=True, text=True, timeout=15)
                if p.returncode == 0:
                    runtime["files"][name] = p.stdout
            elif path.is_file():
                runtime["files"][name] = path.read_text()
        p = subprocess.run(["docker", "image", "inspect", "--format", "{{.Id}}", self.env.container_image],
                           capture_output=True, text=True, timeout=10)
        runtime["container_image_id"] = p.stdout.strip()
        write_json(artifact_path(self.output, "verification.json"), runtime)

    def compile(self, source: str, *, name: str, timeout: int | None = None) -> tuple[bool, str]:
        start = time.monotonic()
        with contextlib.redirect_stdout(sys.stderr):
            ok, message = self.repl.compile(source, timeout=timeout or self.timeout, reject_sorry=True)
        self.records.append({"name": name, "ok": ok, "seconds": time.monotonic()-start, "output": message})
        write_json(artifact_path(self.output, "checking_log.json"), self.records)
        return ok, message

    def close(self):
        self.repl.close()

    def check(self, spec: str, tests: TestArtifact, connector: str) -> dict:
        complete = not tests.unsupported
        harness = tests.harness() + "\n" + """
def sgResults : List (Option Bool) := sgDemands.map Connector.check
def sgConflict : Bool := sgResults.any (fun x => match x with | some false => true | _ => false)
def sgAllPass : Bool := !sgResults.isEmpty && sgResults.all (fun x => match x with | some true => true | _ => false)
"""
        parts = (spec, tests.code, connector, harness)
        for verdict, expression in [("conflict", "sgConflict"), ("no-conflict", "sgAllPass")]:
            if verdict == "no-conflict" and not complete:
                continue
            cert = assemble(*parts, f"set_option maxRecDepth 100000 in\ntheorem evaluation : {expression} = true := by decide\n#print axioms evaluation")
            (artifact_path(self.output, f"proof_attempt_{verdict}.lean")).write_text(cert)
            ok, message = self.compile(cert, name=f"prove_{verdict}")
            if ok and proof_axioms_ok(message):
                (artifact_path(self.output, "cert.lean")).write_text(cert)
                return {"verdict": verdict, "evidence": "proof", "reason": "",
                        "unsupported_demands": tests.unsupported}
        execution = assemble(*parts, "#eval sgResults.map (fun x => match x with | none => (0 : Nat) | some false => 1 | some true => 2)")
        (artifact_path(self.output, "execution.lean")).write_text(execution)
        ok, message = self.compile(execution, name="execute")
        matches = re.findall(r"^info:\s*\[([0-2,\s]*)\]\s*$", message, re.M)
        if not ok or len(matches) != 1:
            return {"verdict": "inconclusive", "evidence": None,
                    "reason": "Lean evaluation failed or returned no readable result",
                    "unsupported_demands": tests.unsupported}
        codes = [int(x.strip()) for x in matches[0].split(",") if x.strip()]
        verdict = "conflict" if 1 in codes else "no-conflict" if complete and codes and all(x == 2 for x in codes) else "inconclusive"
        return {"verdict": verdict, "evidence": "execution" if verdict != "inconclusive" else None,
                "reason": "" if verdict != "inconclusive" else "One or more demands could not be represented or connected",
                "predictions": [None if x == 0 else x == 2 for x in codes],
                "unsupported_demands": tests.unsupported}


def proof_axioms_ok(output: str) -> bool:
    if "sorryAx" in output:
        return False
    if "does not depend on any axioms" in output:
        return True
    matches = re.findall(r"depends on axioms:\s*\[([^]]*)\]", output)
    return bool(matches) and all(set(s.strip() for s in x.split(",") if s.strip()) <=
                                 {"propext", "Quot.sound", "Classical.choice"} for x in matches)
