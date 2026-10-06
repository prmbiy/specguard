"""Repository snapshots and a fail-closed, CLI-owned bash sandbox.

No agent command executes in the original checkout. Bubblewrap sees a constructed
root, not a bind mount of /. This is OS namespace isolation, not a VM or an answer-
leakage oracle: disguised answers in approved implementation/dependencies remain
outside the guarantee.
"""
from __future__ import annotations

import hashlib
import os
import re
import resource
import shutil
import signal
import subprocess
import sys
import sysconfig
import tempfile
from pathlib import Path, PurePosixPath

from .artifacts import GuardError


_PRIVATE = {".git", ".hg", ".svn", ".env", ".venv", "venv", "env", "__pycache__",
            ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox", ".nox", ".ssh",
            ".aws", ".azure", ".config", ".codex", ".claude", "node_modules",
            "output", "outputs", "results", ".specguard", "build", "dist"}
_TEST_DIRS = {"test", "tests", "testing", "testdata", "fixtures", "__tests__"}
_SECRET_SUFFIXES = {".pem", ".key", ".p12", ".pfx", ".pyc", ".pyo", ".log"}
MAX_FILE = 2 * 1024 * 1024


def excluded(path: Path | str, *, tests: bool = False) -> bool:
    parts = PurePosixPath(str(path)).parts
    for part in parts:
        low = part.lower()
        if low in _PRIVATE or low.startswith(".env") or low in {"credentials", "auth.json"}:
            return True
        if tests and (low in _TEST_DIRS or low.startswith("test_") or low.endswith("_test.py")
                      or low == "conftest.py"):
            return True
    return bool(parts and Path(parts[-1]).suffix.lower() in _SECRET_SUFFIXES)


def relative_path(raw: str) -> Path:
    if not isinstance(raw, str) or not raw or "\\" in raw or "\x00" in raw:
        raise GuardError("Invalid repository path")
    p = PurePosixPath(raw)
    if p.is_absolute() or ".." in p.parts:
        raise GuardError("Paths must stay inside the repository")
    return Path(*p.parts)


class Repository:
    """Bounded text-only snapshot; never follows repository symlinks."""

    def __init__(self, root: Path, *, max_bytes: int = 256 * 1024 * 1024):
        self.root = root.resolve()
        self.files: dict[str, str] = {}
        self.omitted: list[str] = []
        size = 0
        for base, dirs, names in os.walk(self.root, followlinks=False):
            dirs[:] = sorted(d for d in dirs if not (Path(base) / d).is_symlink()
                             and not excluded((Path(base) / d).relative_to(self.root)))
            for name in sorted(names):
                path = Path(base) / name
                rel = path.relative_to(self.root).as_posix()
                if path.is_symlink() or excluded(rel) or not path.is_file():
                    continue
                # Open without following a final symlink; verify identity after opening.
                if path.stat().st_size > MAX_FILE:
                    self.omitted.append(rel)
                    continue
                fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                try:
                    import stat
                    if not stat.S_ISREG(os.fstat(fd).st_mode):
                        continue
                    with os.fdopen(fd, "rb", closefd=False) as stream:
                        data = stream.read(MAX_FILE + 1)
                    if len(data) > MAX_FILE or b"\x00" in data:
                        self.omitted.append(rel)
                        continue
                    text = data.decode("utf8")
                except UnicodeDecodeError:
                    self.omitted.append(rel)
                    continue
                finally:
                    os.close(fd)
                size += len(data)
                if size > max_bytes:
                    raise GuardError(f"Repository text snapshot exceeds {max_bytes / (1024 * 1024):g} MiB; narrow the checkout")
                self.files[rel] = text

    def read(self, raw: str) -> str:
        key = relative_path(raw).as_posix()
        if key not in self.files:
            raise GuardError(f"File unavailable in frozen repository: {key}")
        return self.files[key]

    def hashes(self) -> dict[str, str]:
        return {p: hashlib.sha256(s.encode()).hexdigest() for p, s in self.files.items()}

    def prepare_source(self, selected: list[str], private_files: set[str], dest: Path) -> list[str]:
        roots = [relative_path(s).as_posix().rstrip("/") for s in selected]
        if not roots:
            raise GuardError("Source selection returned no implementation paths")
        for p in roots:
            if p == "." or excluded(p, tests=True):
                raise GuardError(f"Disallowed source selection: {p}")
            if not any(f == p or f.startswith(p + "/") for f in self.files):
                raise GuardError(f"Source path unavailable: {p}")
        copied = []
        for path, content in self.files.items():
            if path in private_files or excluded(path, tests=True):
                continue
            if not any(path == p or path.startswith(p + "/") for p in roots):
                continue
            out = dest / path
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(content, encoding="utf8")
            copied.append(path)
        if not copied:
            raise GuardError("No source files remain after test/private-file exclusions")
        return copied


def _copy_runtime_tree(source: Path, dest: Path, *, package: bool = False) -> None:
    """Copy runtime dependencies, stripping test trees, caches and startup hooks."""
    if not source.is_dir():
        return
    for base, dirs, names in os.walk(source, followlinks=False):
        relbase = Path(base).relative_to(source)
        dirs[:] = [d for d in dirs if not (Path(base) / d).is_symlink()
                   and not excluded(relbase / d, tests=True)
                   and d not in {"site-packages", "dist-packages"}]
        for name in names:
            src = Path(base) / name
            rel = relbase / name
            if src.is_symlink() or not src.is_file() or excluded(rel, tests=True):
                continue
            if name.endswith((".pth", ".egg-link")) or name in {
                "sitecustomize.py", "usercustomize.py", "direct_url.json"
            } or name.startswith("__editable__"):
                continue
            out = dest / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, out)
            out.chmod(src.stat().st_mode & 0o777)


class BashWorkspace:
    def __init__(self, root: Path, *, executable: str | None = None):
        self.root = root
        self.bwrap = executable or shutil.which("bwrap")
        self.process: subprocess.Popen | None = None
        self.runtime = root.parent / "runtime"
        self._argv: list[str] = []

    @staticmethod
    def preflight() -> None:
        binary = shutil.which("bwrap")
        if not binary:
            raise GuardError("SpecGuard requires bubblewrap (bwrap) for isolated bash; install it in the prepared environment")
        check = subprocess.run([binary, "--unshare-all", "--die-with-parent", "--ro-bind", "/", "/",
                                "--", "/bin/true"], capture_output=True, timeout=10)
        if check.returncode:
            raise GuardError("Bubblewrap isolation is unavailable (user namespaces may be disabled): "
                             + check.stderr.decode(errors="replace")[-500:])

    def prepare(self) -> None:
        if not self.bwrap:
            raise GuardError("Bubblewrap is required")
        self.root.mkdir(parents=True, exist_ok=True)
        self.runtime.mkdir(parents=True, exist_ok=True)
        argv = [self.bwrap, "--unshare-all", "--die-with-parent", "--new-session",
                "--cap-drop", "ALL", "--clearenv", "--proc", "/proc", "--dev", "/dev",
                "--tmpfs", "/tmp", "--dir", "/home", "--dir", "/home/agent"]
        # Exact executables, not /bin or /usr: those may contain host administration tools.
        binaries = {"bash", "ls", "cat", "find", "grep", "sed", "head", "tail", "wc",
                    "sort", "cut", "tr", "pwd", "env", "mkdir", "cp", "mv", "rm", "touch", "rg"}
        native = []
        for name in sorted(binaries):
            found = shutil.which(name)
            if found:
                src = Path(found).resolve()
                native.append(src)
                argv += ["--ro-bind", str(src), f"/usr/bin/{name}"]
        python = Path(sys.executable).resolve()
        native.append(python)
        argv += ["--ro-bind", str(python), "/usr/bin/python", "--symlink", "python", "/usr/bin/python3",
                 "--symlink", "usr/bin", "/bin"]
        pyroot = self.runtime / "python"
        lib = pyroot / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}"
        _copy_runtime_tree(Path(sysconfig.get_path("stdlib")), lib)
        site = lib / "site-packages"
        for source in {sysconfig.get_path("purelib"), sysconfig.get_path("platlib")}:
            _copy_runtime_tree(Path(source), site, package=True)
        # Do not expose a second, installed copy of the project that bypasses the
        # source selection (including tests with nonstandard names).
        module_roots = {p.stem for p in self.root.glob("*.py")}
        for base in (self.root, self.root / "src"):
            if base.is_dir():
                module_roots.update(p.name for p in base.iterdir() if p.is_dir())
                module_roots.update(p.stem for p in base.glob("*.py"))
        for name in module_roots:
            for installed in (site / name, site / (name + ".py")):
                if installed.is_dir():
                    shutil.rmtree(installed)
                elif installed.is_file():
                    installed.unlink()
        # Shared libraries are runtime inputs; copy only native libraries, never their
        # containing host directories. Resolve symlinks before copying each library.
        libpaths = set()
        for binary in native:
            p = subprocess.run(["ldd", str(binary)], capture_output=True, text=True, timeout=10)
            libpaths.update(re.findall(r"(/[^\s()]+)", p.stdout))
        for folder in (Path("/lib"), Path("/usr/lib"), Path("/usr/local/lib")):
            for p in folder.glob("*.so*"):
                if p.is_file():
                    libpaths.add(str(p))
            for sub in folder.glob("*-linux-gnu"):
                for p in sub.glob("*.so*"):
                    if p.is_file():
                        libpaths.add(str(p))
        for path in sorted(libpaths):
            src = Path(path)
            if not src.is_file():
                continue
            dst = self.runtime / "native" / path.lstrip("/")
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src.resolve(), dst)
            dst.chmod(src.resolve().stat().st_mode & 0o777)
            argv += ["--ro-bind", str(dst), path]
        argv += ["--ro-bind", str(pyroot), "/runtime/python", "--bind", str(self.root), "/workspace",
                 "--setenv", "PATH", "/usr/bin", "--setenv", "HOME", "/home/agent",
                 # The constructed root deliberately has no host ld.so.cache.
                 # Python builds installed in /usr/local/lib need an explicit
                 # search path to the native libraries copied above.
                 "--setenv", "LD_LIBRARY_PATH", ":".join(sorted({str(Path(p).parent) for p in libpaths})),
                 "--setenv", "PYTHONHOME", "/runtime/python", "--setenv", "PYTHONPATH", "/workspace:/workspace/src",
                 "--setenv", "PYTHONDONTWRITEBYTECODE", "1", "--setenv", "PYTHONUTF8", "1",
                 "--setenv", "LC_ALL", "C",
                 "--chdir", "/workspace", "--", "/usr/bin/bash", "--noprofile", "--norc", "-c"]
        self._argv = argv

    def run(self, command: str, timeout: float = 120) -> str:
        if not self._argv:
            raise GuardError("Workspace has not been prepared")
        def limits():
            resource.setrlimit(resource.RLIMIT_FSIZE, (16 * 1024 * 1024,) * 2)
            resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
            resource.setrlimit(resource.RLIMIT_AS, (4 * 1024**3,) * 2)
            resource.setrlimit(resource.RLIMIT_NPROC, (512, 512))
        with tempfile.TemporaryFile() as output:
            self.process = subprocess.Popen(self._argv + [command], stdout=output, stderr=output,
                                            stdin=subprocess.DEVNULL, start_new_session=True, preexec_fn=limits)
            try:
                code = self.process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                self.close()
                code = "timeout"
            except BaseException:
                self.close()
                raise
            finally:
                # A shell may leave background processes: kill its owned namespace/group.
                self.close()
            output.seek(0)
            data = output.read(12000).decode(errors="replace")
            return f"exit={code}\n{data}\n[output capped at 12000 bytes]"

    def close(self) -> None:
        if self.process is not None:
            try:
                os.killpg(self.process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            self.process.wait(timeout=5)
            self.process = None
