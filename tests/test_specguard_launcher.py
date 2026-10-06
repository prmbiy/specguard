from unittest.mock import Mock, MagicMock

import pytest
import yaml


from conflict_certifier.specguard import bench as launcher


@pytest.fixture(autouse=True)
def mock_repl_service(monkeypatch):
    service = MagicMock()
    monkeypatch.setattr(launcher, "ReplService", service)
    return service


def test_config_tasks_resolve_to_built_image_names():
    config = yaml.safe_load((launcher.ROOT / "configs/specguard.yaml").read_text())
    tasks = launcher.selected_tasks(config)
    assert len(tasks) == 22
    assert all(image == yaml.safe_load((task / "provenance.json").read_text()).get("image_tag", f"specguard-{task.name}:demo")
               for task, image in tasks)
    assert launcher.selected_tasks(config, ["pandas-eval-not-inplace"])[0][1] == "specguard-pandas-eval:demo"


def test_lean_mount_uses_configured_root(monkeypatch, tmp_path):
    monkeypatch.setenv("SPECGUARD_LEAN_ROOT", str(tmp_path))
    assert launcher.lean_runtime_mount() == (str(tmp_path), f"{tmp_path}:{tmp_path}:ro")


def test_benchmark_lean_mount_root_can_be_relocated(monkeypatch, tmp_path):
    from conflict_certifier.config import LeanEnv

    monkeypatch.setenv("LEAN_CONTAINER_MOUNT_ROOT", str(tmp_path))
    env = LeanEnv.from_dict({"lean_project": str(tmp_path / "workspace"),
                             "repl_bin": str(tmp_path / "repl")})
    assert env.container_mount_root == tmp_path


@pytest.mark.parametrize("names", [[], "pandas", ["../escape"], ["missing"],
                                  ["pandas-eval-not-inplace"] * 2])
def test_bad_task_selection(names):
    with pytest.raises(ValueError):
        launcher.selected_tasks({"tasks": names})


def test_artifact_directories_rejects_empty_and_container_paths():
    assert launcher.artifact_directories("\n", "task-id") == []
    assert launcher.artifact_directories("/\n/testbed\n", "task-id") == []
    assert launcher.artifact_directories(
        "/testbed/output/specguard/model/stamp/task-id\n", "task-id"
    ) == ["/testbed/output/specguard/model/stamp/task-id"]
    assert launcher.artifact_directories(
        "/testbed/output/specguard/model/stamp/another-task\n", "task-id"
    ) == []


def test_main_uses_one_pool_and_does_not_build_by_default(monkeypatch, mock_repl_service):
    monkeypatch.setattr("sys.argv", ["specguard-bench"])
    mocked_run = Mock()
    mocked_task = Mock()
    monkeypatch.setattr(launcher, "run", mocked_run)
    monkeypatch.setattr(launcher, "run_one", mocked_task)
    monkeypatch.setattr(launcher.signal, "signal", Mock())
    launcher.main()
    assert mocked_run.call_count == 22
    assert all(c.args[:3] == ("docker", "image", "inspect") for c in mocked_run.call_args_list)
    assert mocked_task.call_count == 22
    assert len({c.args[4] for c in mocked_task.call_args_list}) == 1
    mock_repl_service.assert_called_once_with(4, 120)
    assert all(c.kwargs["service"] is mock_repl_service.return_value.__enter__.return_value
               for c in mocked_task.call_args_list)


def test_tasks_really_overlap(monkeypatch):
    import threading
    barrier = threading.Barrier(3)
    seen = set()
    lock = threading.Lock()
    def task(*args, **kwargs):
        with lock:
            seen.add(threading.get_ident())
        if args[0].name in {"comfyui-scoped-fallback", "vllm-precompiled-flags", "haystack-stream-usage"}:
            barrier.wait(timeout=3)
        return {"verdict": "conflict"}
    monkeypatch.setattr("sys.argv", ["specguard-bench"])
    monkeypatch.setattr(launcher, "run", Mock())
    monkeypatch.setattr(launcher, "run_one", task)
    launcher.main()
    assert len(seen) >= 3


def test_override_selects_one_task(monkeypatch):
    monkeypatch.setattr("sys.argv", ["specguard-bench", "--task", "comfyui-scoped-fallback"])
    monkeypatch.setattr(launcher, "run", Mock())
    task = Mock()
    monkeypatch.setattr(launcher, "run_one", task)
    monkeypatch.setattr(launcher.signal, "signal", Mock())
    launcher.main()
    assert task.call_count == 1
    assert task.call_args.args[0].name == "comfyui-scoped-fallback"


def test_verdict_summary(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", [
        "specguard-bench", "--task", "pandas-eval-not-inplace",
        "--task", "comfyui-scoped-fallback", "--task", "vllm-precompiled-flags",
        "--task", "haystack-stream-usage", "--task", "openhands-search-limit",
    ])
    monkeypatch.setattr(launcher, "run", Mock())
    monkeypatch.setattr(launcher.signal, "signal", Mock())
    monkeypatch.setattr(launcher, "run_one", Mock(side_effect=[
        {"verdict": "conflict"}, {"verdict": "no-conflict"},
        {"verdict": "inconclusive"}, RuntimeError("failure"), {"verdict": "conflict"},
    ]))
    launcher.main()
    out = capsys.readouterr().out
    assert "CONFLICT       = 2/5" in out
    assert "NO-CONFLICT    = 1/5" in out
    assert "INCONCLUSIVE   = 2/5" in out
