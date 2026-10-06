import threading
import time
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

import pytest

from conflict_certifier.specguard import checker, agents
from conflict_certifier.specguard.artifacts import GuardError
from conflict_certifier.specguard.settings import execution_settings, DEFAULTS
from conflict_certifier.specguard.repl_service import ReplService, RemoteRepl


@pytest.mark.parametrize("key", DEFAULTS)
@pytest.mark.parametrize("value", [0, -1, True, "3", 1.5])
def test_invalid_controls(key, value):
    with pytest.raises(GuardError):
        execution_settings({key: value})


def test_shared_pool_bounds_compiles_and_closes(monkeypatch):
    state = {"active": 0, "peak": 0, "starts": 0, "closes": 0}
    lock = threading.Lock()
    class Repl:
        def __init__(self, env): pass
        def start(self): state["starts"] += 1
        def close(self): state["closes"] += 1
        def compile(self, source, *, timeout, reject_sorry):
            assert timeout == 7 and reject_sorry
            with lock:
                state["active"] += 1
                state["peak"] = max(state["peak"], state["active"])
            time.sleep(0.04)
            with lock:
                state["active"] -= 1
            return True, source
    monkeypatch.setattr(checker, "OwnedRepl", Repl)
    with ReplService(2, 7) as service:
        monkeypatch.setenv("SPECGUARD_REPL_SOCKET", service.directory.name + "/repl.sock")
        monkeypatch.setenv("SPECGUARD_REPL_TOKEN", service.token)
        client = RemoteRepl()
        client.start()
        with ThreadPoolExecutor(max_workers=6) as executor:
            results = list(executor.map(lambda n: client.compile(str(n), timeout=99), range(6)))
        assert results == [(True, str(n)) for n in range(6)]
        client.close()
        assert state["closes"] == 0
        monkeypatch.setenv("SPECGUARD_REPL_TOKEN", "wrong")
        with pytest.raises(GuardError, match="Unauthorized"):
            client.start()
    assert state == {"active": 0, "peak": 2, "starts": 2, "closes": 2}


def test_checker_timeout_is_used(tmp_path, monkeypatch):
    monkeypatch.delenv("SPECGUARD_REPL_SOCKET", raising=False)
    instance = checker.Checker(tmp_path)
    instance.repl = Mock()
    instance.repl.compile.return_value = True, "ok"
    instance.timeout = 17
    instance.compile("hello", name="probe")
    instance.repl.compile.assert_called_once_with("hello", timeout=17, reject_sorry=True)


def test_spec_submission_limit(tmp_path):
    client = Mock()
    client.complete_conversation.return_value = "SUBMIT_SPEC\n```lean4\ninvalid\n```"
    with pytest.raises(GuardError):
        agents.spec_agent(client, Mock(), Mock(), "task", tmp_path, max_turns=10, max_submissions=2)
    assert client.complete_conversation.call_count == 2


def test_spec_bash_timeout_and_turn_limit(tmp_path):
    client = Mock()
    client.complete_conversation.return_value = "```bash\npwd\n```"
    box = Mock()
    box.run.return_value = "ok"
    with pytest.raises(GuardError, match="turn budget"):
        agents.spec_agent(client, Mock(), box, "task", tmp_path, max_turns=2, exec_timeout=19)
    assert box.run.call_count == 2
    box.run.assert_called_with("pwd\n", timeout=19)


def test_test_and_connector_submission_limits(tmp_path):
    from conflict_certifier.specguard.artifacts import TestArtifact
    context = {"locations": [], "demands": [], "files": {}}
    client = Mock()
    client.complete_conversation.return_value = "invalid"
    with pytest.raises(GuardError, match="TestAgent exhausted"):
        agents.test_agent(client, Mock(), context, tmp_path, max_submissions=2)
    assert client.complete_conversation.call_count == 2
    client.reset_mock()
    with pytest.raises(GuardError, match="ConnectorAgent exhausted"):
        agents.connector_agent(client, Mock(), "", TestArtifact("", (), {}), tmp_path, context,
                               max_submissions=3)
    assert client.complete_conversation.call_count == 3


def test_selection_turn_limit():
    from conflict_certifier.specguard.discovery import selection_loop
    client = Mock()
    client.complete_conversation.return_value = '{"action":"invalid"}'
    with pytest.raises(GuardError, match="turn budget"):
        selection_loop(client, Mock(), "task", max_turns=2)
    assert client.complete_conversation.call_count == 2


def test_task_cancellation_only_targets_owned_containers(monkeypatch):
    from conflict_certifier.specguard import bench
    command = Mock()
    monkeypatch.setattr(bench.subprocess, "run", command)
    control = bench.TaskControl()
    launch = Mock()
    control.start("owned-task", launch)
    control.stop()
    launch.assert_called_once()
    assert command.call_args.args[0][:3] == ["docker", "exec", "owned-task"]
    with pytest.raises(RuntimeError, match="interrupted"):
        control.start("not-started", launch)
    launch.assert_called_once()
