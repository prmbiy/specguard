"""Execute every generated Python artifact inside a sealed SWE-bench task image."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass

from conflict_certifier.tracks.swebench.source import require_image


_CONTAINER_PATH = "/opt/miniconda3/bin:/usr/local/bin:/usr/bin:/bin"
_CONTAINER_MEMORY = "2g"
_CONTAINER_PIDS_LIMIT = 128


@dataclass(frozen=True)
class ContainerResult:
    ok: bool
    output: str
    timed_out: bool = False
    infrastructure_error: bool = False


class TaskPythonRunner:
    """Run every stage of one task in one networkless container."""

    def __init__(
        self,
        instance_id: str,
        *,
        timeout_seconds: int = 30,
    ):
        self.instance_id = instance_id
        self.image = require_image(instance_id)
        self.timeout = timeout_seconds
        self._cid: str | None = None

    def start(self) -> "TaskPythonRunner":
        if self._cid is not None:
            return self
        command = [
            "docker", "create", "--network=none", "--cap-drop=ALL",
            "--security-opt", "no-new-privileges:true",
            "--pids-limit", str(_CONTAINER_PIDS_LIMIT),
            "--memory", _CONTAINER_MEMORY, "--memory-swap", _CONTAINER_MEMORY,
            "--tmpfs", "/tmp:rw,nosuid,nodev,size=256m",
            "-w", "/testbed", self.image, "sleep", "infinity",
        ]
        result = subprocess.run(
            command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, timeout=120)
        if result.returncode != 0:
            raise RuntimeError(f"docker create failed: {result.stdout[-2000:]}")
        cid = result.stdout.strip().splitlines()[-1]
        started = subprocess.run(
            ["docker", "start", cid], stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, timeout=120)
        if started.returncode != 0:
            subprocess.run(["docker", "rm", "-f", cid], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL)
            raise RuntimeError(f"docker start failed: {started.stdout[-2000:]}")
        sealed = subprocess.run(
            ["docker", "exec", cid, "rm", "-rf", "/testbed/.git"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=60)
        if sealed.returncode != 0:
            subprocess.run(["docker", "rm", "-f", cid], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL)
            raise RuntimeError(f"failed to remove git metadata: {sealed.stdout[-1000:]}")
        self._cid = cid
        return self

    @staticmethod
    def _remove(cid: str) -> None:
        subprocess.run(["docker", "rm", "-f", cid], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL)

    @staticmethod
    def _safe_name(name: str) -> str:
        clean = re.sub(r"[^A-Za-z0-9_.-]+", "_", name)
        if not clean or clean.startswith(".") or "/" in clean:
            raise ValueError(f"invalid artifact filename: {name!r}")
        return clean

    @staticmethod
    def _write(cid: str, name: str, source: str) -> None:
        result = subprocess.run(
            ["docker", "exec", "-i", cid, "sh", "-c",
             f"mkdir -p /tmp/conflict_certifier && cat > /tmp/conflict_certifier/{name}"],
            input=source, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, timeout=30)
        if result.returncode != 0:
            raise RuntimeError(f"failed to place {name} in task container: {result.stdout[-1000:]}")

    def run_files(self, files: dict[str, str], *, entrypoint: str) -> ContainerResult:
        """Execute a bundle without replacing the task's persistent container."""
        safe_files = {self._safe_name(name): source for name, source in files.items()}
        entrypoint = self._safe_name(entrypoint)
        if entrypoint not in safe_files:
            raise ValueError(f"entrypoint {entrypoint!r} is not in the artifact bundle")
        if self._cid is None:
            return ContainerResult(False, "task container is not running",
                                   infrastructure_error=True)
        cid = self._cid
        try:
            reset = subprocess.run(
                ["docker", "exec", cid, "sh", "-c",
                 "rm -rf /tmp/conflict_certifier && mkdir -p /tmp/conflict_certifier"],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, timeout=30)
            if reset.returncode != 0:
                raise RuntimeError(f"failed to reset artifact directory: {reset.stdout[-1000:]}")
            for name, source in safe_files.items():
                self._write(cid, name, source)
            command = [
                "docker", "exec", cid, "env", "-i",
                f"PATH={_CONTAINER_PATH}", "HOME=/tmp", "LANG=C.UTF-8",
                "PYTHONDONTWRITEBYTECODE=1", "PYTHONHASHSEED=0",
                "python", "-I", f"/tmp/conflict_certifier/{entrypoint}",
            ]
            try:
                result = subprocess.run(
                    command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, errors="replace", timeout=self.timeout)
            except subprocess.TimeoutExpired as exc:
                partial = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
                self.close()
                return ContainerResult(
                    False, partial + f"\nTIMEOUT: exceeded {self.timeout}s",
                    timed_out=True)
            output = result.stdout
            if len(output) > 20000:
                output = output[:20000] + "\n... output truncated ..."
            return ContainerResult(result.returncode == 0, output)
        except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
            return ContainerResult(False, str(exc), infrastructure_error=True)

    def bash(self, script: str, timeout: int | None = None) -> tuple[int, str]:
        if self._cid is None:
            raise RuntimeError("task container is not running")
        command = [
            "docker", "exec", "-w", "/testbed", self._cid, "env", "-i",
            f"PATH={_CONTAINER_PATH}", "HOME=/tmp", "LANG=C.UTF-8",
            "PYTHONDONTWRITEBYTECODE=1", "bash", "--noprofile", "--norc", "-c", script,
        ]
        try:
            result = subprocess.run(
                command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, errors="replace", timeout=timeout or self.timeout)
            output = result.stdout
            if len(output) > 20000:
                output = output[:20000] + "\n... output truncated ..."
            return result.returncode, output
        except subprocess.TimeoutExpired:
            self.close()
            return 124, f"command timed out after {timeout or self.timeout}s"

    def close(self) -> None:
        if self._cid is not None:
            self._remove(self._cid)
            self._cid = None

    def __enter__(self) -> "TaskPythonRunner":
        return self.start()

    def __exit__(self, *args) -> None:
        self.close()
