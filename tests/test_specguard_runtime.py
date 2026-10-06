import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from conflict_certifier.specguard import checker, runtime
from conflict_certifier.specguard.artifacts import GuardError
from conflict_certifier.specguard.cli import main


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    for name in ("SPECGUARD_LEAN_ROOT", "SPECGUARD_LEAN_IMAGE", "SPECGUARD_REPL_SOCKET"):
        monkeypatch.delenv(name, raising=False)


def save_selection(value):
    path = runtime.runtime_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def test_default_and_explicit_host_override(monkeypatch, tmp_path):
    assert runtime.lean_environment().container_mount_root == Path("/mnt/data/lean")
    save_selection({"backend": "image", "image": "managed:1"})
    assert isinstance(runtime.lean_environment(), runtime.ImageLeanEnv)
    monkeypatch.setenv("SPECGUARD_LEAN_IMAGE", "explicit:1")
    assert runtime.lean_environment().container_image == "explicit:1"
    monkeypatch.setenv("SPECGUARD_LEAN_ROOT", str(tmp_path / "existing"))
    env = runtime.lean_environment()
    assert not isinstance(env, runtime.ImageLeanEnv)
    assert env.container_mount_root == tmp_path / "existing"


def test_saved_host_selection(tmp_path):
    save_selection({"backend": "host", "root": str(tmp_path / "existing")})
    assert runtime.lean_environment().project_dir == tmp_path / "existing/workspace"


def test_malformed_saved_selection_is_actionable():
    save_selection([])
    with pytest.raises(GuardError, match="Invalid Lean runtime configuration"):
        runtime.lean_environment()


def test_image_backend_never_resolves_or_mounts_host_toolchain(monkeypatch):
    monkeypatch.setenv("SPECGUARD_LEAN_IMAGE", "managed:1")
    monkeypatch.setattr(checker, "_lake_env_vars", lambda *a: pytest.fail("host Lean invoked"))
    popen = Mock()
    monkeypatch.setattr(checker.subprocess, "Popen", popen)
    env = runtime.lean_environment()
    env.validate()  # Does not require /opt/specguard-lean on the host.
    checker.OwnedRepl(env)._start_container()
    argv = popen.call_args.args[0]
    assert "-v" not in argv
    assert "--network=none" in argv and "--read-only" in argv
    assert "--cap-drop=ALL" in argv and "--security-opt=no-new-privileges" in argv
    assert argv[-1] == "managed:1"


def test_setup_verifies_before_selecting_and_needs_no_model(monkeypatch):
    monkeypatch.setattr("conflict_certifier.lean.repl.docker_available", lambda: True)
    run = Mock()
    monkeypatch.setattr(runtime.subprocess, "run", run)
    repl = Mock()
    repl.compile.return_value = (True, "ok")
    monkeypatch.setattr(checker, "OwnedRepl", Mock(return_value=repl))
    monkeypatch.setattr("conflict_certifier.specguard.cli.configuration",
                        lambda *a: pytest.fail("requested model credentials"))
    assert main(["setup"]) == 0
    assert run.call_args.args[0][:4] == ["docker", "build", "--tag", runtime.IMAGE]
    repl.start.assert_called_once()
    repl.compile.assert_called_once()
    repl.close.assert_called_once()
    assert json.loads(runtime.runtime_file().read_text()) == {"backend": "image", "image": runtime.IMAGE}


def test_failed_proof_keeps_previous_runtime_and_cleans_repl(monkeypatch):
    save_selection({"backend": "image", "image": "previous:1"})
    monkeypatch.setattr("conflict_certifier.lean.repl.docker_available", lambda: True)
    monkeypatch.setattr(runtime.subprocess, "run", Mock())
    repl = Mock()
    repl.compile.return_value = (False, "invalid toolchain")
    monkeypatch.setattr(checker, "OwnedRepl", Mock(return_value=repl))
    assert runtime.setup([]) == 2
    assert json.loads(runtime.runtime_file().read_text())["image"] == "previous:1"
    repl.close.assert_called_once()


def test_missing_host_directory_does_not_download_or_select(monkeypatch):
    monkeypatch.setattr("conflict_certifier.lean.repl.docker_available", lambda: True)
    run = Mock()
    monkeypatch.setattr(runtime.subprocess, "run", run)
    assert runtime.setup(["--lean-root", "/nonexistent/lean"]) == 2
    run.assert_not_called()
    assert not runtime.runtime_file().exists()


def test_select_existing_runtime_does_not_build_image(monkeypatch, tmp_path):
    root = tmp_path / "lean"
    (root / "workspace").mkdir(parents=True)
    (root / "workspace/lean-toolchain").write_text("leanprover/lean4:v4.24.0")
    (root / "repl/.lake/build/bin").mkdir(parents=True)
    (root / "repl/.lake/build/bin/repl").touch()
    monkeypatch.setattr("conflict_certifier.lean.repl.docker_available", lambda: True)
    monkeypatch.setattr("conflict_certifier.lean.repl._image_present", lambda _: True)
    run = Mock()
    monkeypatch.setattr(runtime.subprocess, "run", run)
    repl = Mock()
    repl.compile.return_value = (True, "ok")
    factory = Mock(return_value=repl)
    monkeypatch.setattr(checker, "OwnedRepl", factory)
    assert runtime.setup(["--lean-root", str(root)]) == 0
    run.assert_not_called()
    assert factory.call_args.args[0].container_mount_root == root
    assert json.loads(runtime.runtime_file().read_text()) == {"backend": "host", "root": str(root)}


def test_managed_benchmark_has_no_lean_host_mount(monkeypatch):
    from conflict_certifier.specguard.bench import lean_runtime_mount
    monkeypatch.setenv("SPECGUARD_LEAN_IMAGE", "managed:1")
    assert lean_runtime_mount() == (str(runtime.IMAGE_ROOT), None)


def test_missing_runtime_points_to_setup(monkeypatch, tmp_path):
    monkeypatch.setenv("SPECGUARD_LEAN_ROOT", str(tmp_path / "missing"))
    with pytest.raises(GuardError, match="Run specguard setup"):
        checker.Checker.preflight()


def test_shared_runtime_preflight_uses_socket_not_local_install(monkeypatch):
    monkeypatch.setenv("SPECGUARD_REPL_SOCKET", "/pool/repl.sock")
    remote = Mock()
    monkeypatch.setattr("conflict_certifier.specguard.repl_service.RemoteRepl", Mock(return_value=remote))
    monkeypatch.setattr(checker, "lean_environment", lambda: pytest.fail("local toolchain required"))
    checker.Checker.preflight()
    remote.start.assert_called_once()
