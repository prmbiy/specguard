"""Isolated source-patch evaluation against paired SWE-bench test suites."""

from __future__ import annotations

import json
import atexit
import re
import shlex
import subprocess
import threading
from pathlib import PurePosixPath

from conflict_certifier.tracks.swebench.source import require_image


_MEMORY = "4g"
_PIDS = "256"
_DIFF_PATH = re.compile(r"^diff --git a/(.+?) b/(.+?)$", re.MULTILINE)
_PASS = {"PASSED", "XFAIL"}
_FAIL = {"FAILED", "ERROR"}


def patch_paths(patch: str) -> list[str]:
    """Accept a regular, source-only Git diff; never accept test modifications."""
    paths = []
    for old, new in _DIFF_PATH.findall(patch):
        if old != new or old.startswith("/") or "\\" in old:
            raise ValueError("patch contains a renamed or unsafe path")
        path = PurePosixPath(old)
        if any(part in ("", ".", "..") for part in path.parts):
            raise ValueError("patch contains an unsafe path")
        parts = {part.lower() for part in path.parts}
        name = path.name.lower()
        if ({"test", "tests", ".git"} & parts or name.startswith("test_")
                or name.endswith("_test.py") or name == "conftest.py"):
            raise ValueError("patch modifies a test or Git metadata")
        if not name.endswith(".py"):
            raise ValueError("patch must modify Python source files only")
        paths.append(old)
    if not paths or "GIT binary patch" in patch:
        raise ValueError("no applicable text source-code diff")
    return paths


def _run(command: list[str], *, input: str | None = None,
         timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(command, input=input, text=True, errors="replace",
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          timeout=timeout)


class TaskContainer:
    """Fresh, networkless task image; only the scorer installs target tests."""

    _active: set[str] = set()
    _lock = threading.Lock()

    @classmethod
    def close_all(cls) -> None:
        with cls._lock:
            containers = tuple(cls._active)
            cls._active.clear()
        for cid in containers:
            subprocess.run(["docker", "rm", "-f", cid],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def __init__(self, instance_id: str, *, agent_view: bool,
                 test_paths: list[str] | None = None):
        self.image = require_image(instance_id)
        self.agent_view = agent_view
        self.test_paths = test_paths or []
        self.cid: str | None = None

    def __enter__(self) -> "TaskContainer":
        try:
            created = _run([
                "docker", "create", "--network=none", "--cap-drop=ALL",
                "--security-opt", "no-new-privileges:true",
                "--pids-limit", _PIDS, "--memory", _MEMORY,
                "--memory-swap", _MEMORY,
                "--tmpfs", "/tmp:rw,nosuid,nodev,size=512m",
                "--label", "conflict-certifier.track=swebench-consensus",
                "-w", "/testbed", self.image, "sleep", "infinity",
            ])
            if created.returncode:
                raise RuntimeError(f"Docker create failed: {created.stdout[-1000:]}")
            self.cid = created.stdout.strip().splitlines()[-1]
            with self._lock:
                self._active.add(self.cid)
            started = _run(["docker", "start", self.cid])
            if started.returncode:
                raise RuntimeError(f"Docker start failed: {started.stdout[-1000:]}")
            if self.agent_view:
                # The image is the buggy base checkout. Remove even its pre-existing
                # test files; neither Original nor Oneoff patches enter this container.
                program = '''import json, os, shutil
root = "/testbed"
private = set(json.loads(os.environ["PRIVATE_TEST_PATHS"]))
for directory, dirs, files in os.walk(root):
    for d in list(dirs):
        if d in {"__pycache__", ".pytest_cache", ".tox", ".git"}:
            shutil.rmtree(os.path.join(directory, d))
            dirs.remove(d)
    for d in list(dirs):
        if d.lower() in {"test", "tests", "testing"}:
            shutil.rmtree(os.path.join(directory, d))
            dirs.remove(d)
    for name in files:
        low = name.lower()
        if low.startswith("test_") or low.endswith("_test.py") or low == "conftest.py":
            os.unlink(os.path.join(directory, name))
for relative in private:
    path = os.path.abspath(os.path.join(root, relative))
    if path.startswith(root + "/") and os.path.isfile(path):
        os.unlink(path)
'''
                hidden = _run([
                    "docker", "exec", "-i", "-w", "/testbed", self.cid,
                    "env", f"PRIVATE_TEST_PATHS={json.dumps(self.test_paths)}",
                    "python", "-c", program,
                ])
                if hidden.returncode:
                    raise RuntimeError(f"failed to hide tests: {hidden.stdout[-1000:]}")
                sealed = _run(["docker", "exec", self.cid,
                               "rm", "-rf", "/testbed/.git"])
                if sealed.returncode:
                    raise RuntimeError(f"failed to remove Git history: {sealed.stdout[-1000:]}")
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *_):
        if self.cid:
            cid, self.cid = self.cid, None
            with self._lock:
                self._active.discard(cid)
            subprocess.run(["docker", "rm", "-f", cid],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def bash(self, command: str, *, timeout: int = 120) -> tuple[int, str]:
        if not self.cid:
            raise RuntimeError("container is closed")
        try:
            result = _run(["docker", "exec", "-w", "/testbed", self.cid,
                           "env", "-i",
                           "PATH=/opt/miniconda3/bin:/usr/local/bin:/usr/bin:/bin",
                           "HOME=/tmp", "LANG=C.UTF-8",
                           "timeout", "--signal=KILL", str(timeout),
                           "bash", "--noprofile", "--norc", "-c", command],
                          timeout=timeout + 10)
            return result.returncode, result.stdout
        except subprocess.TimeoutExpired:
            self.__exit__(None, None, None)
            return 124, f"command exceeded {timeout}s"

    def apply(self, patch: str, *, check_only: bool = False) -> tuple[bool, str]:
        if not self.cid:
            raise RuntimeError("container is closed")
        args = ["docker", "exec", "-i", "-w", "/testbed", self.cid,
                "git", "apply", "--whitespace=nowarn"]
        if self.agent_view:
            args.append("--no-index")
        if check_only:
            args.append("--check")
        result = _run([*args, "-"], input=patch)
        return result.returncode == 0, result.stdout[-2000:]


def test_files_from_patch(patch: str) -> list[str]:
    return [new for _, new in _DIFF_PATH.findall(patch)]


def test_command(row: dict) -> tuple[str, object]:
    """Use the installed SWE-bench harness's repo/version test command and parser."""
    try:
        from swebench.harness.constants import MAP_REPO_VERSION_TO_SPECS
        from swebench.harness.log_parsers import MAP_REPO_TO_PARSER
        from swebench.harness.test_spec.python import get_test_directives
        from swebench.harness.test_spec.test_spec import make_test_spec
    except ImportError as exc:
        raise RuntimeError("SWE-bench harness is required for consensus scoring") from exc
    spec = MAP_REPO_VERSION_TO_SPECS[row["repo"]][row["version"]]
    command = spec["test_cmd"]
    if isinstance(command, list):
        command = command[-1]
    directives = get_test_directives(row)
    if not directives:
        raise ValueError("test patch contains no runnable test directives")
    setup = "\n".join(spec.get("eval_commands", []))
    script = (
        "source /opt/miniconda3/bin/activate\n"
        "conda activate testbed\n"
        "cd /testbed\n"
        f"{setup}\n"
        f"{command} {' '.join(shlex.quote(d) for d in directives)}"
    )
    return script, (MAP_REPO_TO_PARSER[row["repo"]], make_test_spec(row))


def interpret_test_output(row: dict, output: str, parser_and_spec: object) -> dict:
    parser, test_spec = parser_and_spec
    try:
        statuses = parser(output, test_spec)
    except Exception as exc:
        return {"valid": False, "reason": f"test output parser failed: {exc}",
                "statuses": {}}
    targets = row["FAIL_TO_PASS"]
    missing = [name for name in targets if name not in statuses]
    # A corrupted Oneoff expectation need not be named in the benchmark's
    # original FAIL_TO_PASS list. Inspect every assertion in the patched test
    # file, while still requiring the original target tests to have executed.
    execution_errors = [name for name, value in statuses.items() if value == "ERROR"]
    unknown = {name: status for name, status in statuses.items()
               if status not in _PASS | _FAIL}
    if missing or execution_errors or unknown or not statuses:
        return {"valid": False,
                "reason": f"missing targets={missing}; execution errors={execution_errors}; unknown statuses={unknown}",
                "statuses": statuses}
    return {"valid": True,
            "passed": all(value in _PASS for value in statuses.values()),
            "statuses": statuses}


def score_candidate(instance_id: str, patch: str, row: dict,
                    *, timeout: int) -> dict:
    """One candidate and one presentation; no LLM can see this container."""
    try:
        patch_paths(patch)
        script, parser_and_spec = test_command(row)
        with TaskContainer(instance_id, agent_view=False) as box:
            for label, source in (("source", patch), ("test", row["test_patch"])):
                ok, error = box.apply(source, check_only=True)
                if not ok:
                    return {"valid": False, "reason": f"{label} patch rejected: {error}"}
                ok, error = box.apply(source)
                if not ok:
                    return {"valid": False, "reason": f"{label} patch failed: {error}"}
            code, output = box.bash(script, timeout=timeout)
        if code == 124:
            return {"valid": False, "reason": "test timeout", "exit_code": code,
                    "output": output[-12000:]}
        result = interpret_test_output(row, output, parser_and_spec)
        return {**result, "exit_code": code, "output": output[-12000:]}
    except ValueError as exc:
        return {"valid": False, "reason": f"{type(exc).__name__}: {exc}"[:1000]}


atexit.register(TaskContainer.close_all)
