import os
from pathlib import Path

import pytest

from conflict_certifier.specguard.artifacts import GuardError
from conflict_certifier.specguard.discovery import selection_loop, test_context as make_context
from conflict_certifier.specguard.workspace import Repository, BashWorkspace, _copy_runtime_tree


def put(root, path, text="content"):
    p = root / path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p


def test_snapshot_selection_and_private_files(tmp_path):
    root = tmp_path / "repo"
    put(root, "src/pkg/code.py", "answer = 1")
    put(root, "src/pkg/tests/hidden.py", "SECRET_EXPECTED = 42")
    put(root, "src/pkg/unusual.py", "assert 3 == 9")
    put(root, ".env", "API_KEY=secret")
    put(root, ".git/config", "history")
    put(root, "output/specguard/old/spec.lean", "secret")
    put(root, "pyproject.toml", "[project]\nname='pkg'")
    host = put(tmp_path, "outside", "host secret")
    (root / "src/escape").symlink_to(host)
    repo = Repository(root)
    assert ".env" not in repo.files
    assert "src/escape" not in repo.files
    assert not any(p.startswith("output/") for p in repo.files)
    put(root, "src/pkg/code.py", "changed after snapshot")
    dest = tmp_path / "view"
    copied = repo.prepare_source(["src"], {"src/pkg/unusual.py"}, dest)
    assert copied == ["src/pkg/code.py"]
    assert (dest / copied[0]).read_text() == "answer = 1"
    for path in ["../outside", str(host), ".", ".git", "src/escape"]:
        with pytest.raises(GuardError):
            repo.prepare_source([path], set(), tmp_path / "bad")


def test_runtime_strips_hooks_tests_secrets_and_symlinks(tmp_path):
    src = tmp_path / "packages"
    put(src, "pkg/code.py", "pass")
    put(src, "pkg/tests/test_a.py", "secret")
    put(src, "evil.pth", "import subprocess")
    put(src, "sitecustomize.py", "evil")
    put(src, "pkg/.env", "secret")
    (src / "link.py").symlink_to(src / "pkg/code.py")
    dest = tmp_path / "sanitized"
    _copy_runtime_tree(src, dest)
    assert [p.relative_to(dest).as_posix() for p in dest.rglob("*") if p.is_file()] == ["pkg/code.py"]


def test_discovery_repairs_nonexistent_selector(tmp_path):
    put(tmp_path, "tests/test_x.py", "def test_real():\n    assert 1 == 2\n")
    class Client:
        calls = 0
        def complete_conversation(self, system, messages):
            self.calls += 1
            if self.calls == 1:
                return '{"action":"submit","tests":["tests/test_x.py::test_invented"]}'
            assert "does not exist" in messages[-1]["content"]
            return '{"action":"submit","tests":["tests/test_x.py::test_real"]}'
    client = Client()
    result = selection_loop(client, Repository(tmp_path), "Check test_real")
    assert result["tests"] == ["tests/test_x.py::test_real"]
    assert client.calls == 2


def test_source_agent_cannot_read_tests(tmp_path):
    put(tmp_path, "tests/test_x.py", "assert 1 == 2")
    class Client:
        def __init__(self): self.n = 0
        def complete_conversation(self, system, messages):
            assert "TASK_SECRET" not in str(messages)
            assert "assert 1 == 2" not in str(messages)
            self.n += 1
            if self.n == 1: return '{"action":"read","path":"tests/test_x.py"}'
            assert "metadata only" in messages[-1]["content"]
            return '{"action":"submit","paths":["src"]}'
    assert selection_loop(Client(), Repository(tmp_path), "TASK_SECRET", source_only=True)["paths"] == ["src"]


def test_test_function_obligations_and_helpers(tmp_path):
    put(tmp_path, "tests/test_x.py", "import pytest\n@pytest.mark.parametrize('x', [1,2])\ndef test_x(x):\n    assert x > 0\n    with pytest.raises(ValueError):\n        int('x')\n")
    put(tmp_path, "tests/conftest.py", "fixture_context = 1")
    context = make_context(Repository(tmp_path), {"tests": ["tests/test_x.py::test_x"], "helpers": []})
    assert len(context["demands"]) == 2
    assert "tests/conftest.py" in context["files"]
    with pytest.raises(GuardError):
        make_context(Repository(tmp_path), {"tests": ["tests/test_x.py::not_a_test"]})


def test_bwrap_missing_is_not_unrestricted_fallback(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda _: None)
    with pytest.raises(GuardError, match="bubblewrap"):
        BashWorkspace.preflight()


@pytest.mark.skipif(os.environ.get("SPECGUARD_SANDBOX_TEST") != "1", reason="explicit prepared bubblewrap runtime required")
def test_real_bash_cannot_access_host(tmp_path):
    secret = put(tmp_path, "host-secret.txt", "private")
    source = tmp_path / "source"
    source.mkdir()
    put(source, "main.py", "print('hello')")
    BashWorkspace.preflight()
    box = BashWorkspace(source)
    try:
        box.prepare()
        assert box.run("python main.py").startswith("exit=0\nhello")
        assert box.run(f"cat {secret}").startswith("exit=1\n")
        assert box.run("ls /.git /var/run/docker.sock").startswith("exit=2\n")
        assert "HOST_SECRET" not in box.run("env")
        assert box.run("python -c 'import socket; socket.create_connection((\"1.1.1.1\",443),1)'").startswith("exit=1\n")
    finally:
        box.close()
