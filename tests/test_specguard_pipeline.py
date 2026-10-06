import json
import os
import signal
import time
from pathlib import Path

import pytest

from conflict_certifier.specguard.artifacts import GuardError, TestArtifact as Artifact, lean_submission, test_submission as parse_tests
from conflict_certifier.specguard.checker import Checker, OwnedRepl, lean_environment, proof_axioms_ok
from conflict_certifier.specguard.run import DeadlineExceeded, deadline, run_task


SPEC = "namespace Spec\nabbrev Input := Nat\nabbrev Output := Nat\ndef run (x : Input) : Output := x + 1\nend Spec"
TEST = "namespace Tests\nstructure Case where\n  input : Nat\n  expected : Nat\ndef demand_1 : List Case := [⟨1, 3⟩]\nend Tests"
CONNECTOR = "namespace Connector\ndef check (c : Tests.Case) : Option Bool := some (Spec.run c.input == c.expected)\nend Connector"


class FakeChecker(Checker):
    instances = []
    @staticmethod
    def preflight(): pass
    def __init__(self, output):
        self.output = output
        self.closed = False
        self.instances.append(self)
    def start(self): pass
    def close(self): self.closed = True
    def compile(self, source, *, name, timeout=None):
        if name.startswith("prove_"): return False, "error: decide failed"
        if name == "execute": return True, "info: [1]"
        return True, ""


class FakeWorkspace:
    instances = []
    @staticmethod
    def preflight(): pass
    def __init__(self, root):
        self.root = root
        self.closed = False
        self.instances.append(self)
    def prepare(self):
        assert (self.root / "src/pkg.py").exists()
        assert not (self.root / "tests").exists()
    def run(self, command, timeout=120): return "exit=0\n3.12"
    def close(self): self.closed = True


def test_end_to_end_without_llm_or_docker(tmp_path):
    for folder in ("src", "tests"): (tmp_path / folder).mkdir()
    (tmp_path / "src/pkg.py").write_text("def f(x): return x+1")
    (tmp_path / "tests/test_pkg.py").write_text("def test_f():\n    assert f(1) == 3")
    seen = {}
    class Client:
        def __init__(self, agent): self.agent = agent
        def complete_conversation(self, system, messages):
            seen[self.agent] = str(messages)
            return {
                "discovery": '{"action":"submit","tests":["tests/test_pkg.py::test_f"],"helpers":[]}',
                "source_selection": '{"action":"submit","paths":["src"]}',
                "spec": "SUBMIT_SPEC\n```lean4\n"+SPEC+"\n```",
                "tests": "SUBMIT_TESTS\n```lean4\n"+TEST+'\n```\n```json\n{"supported":["demand_1"],"unsupported":{}}\n```',
                "connector": "SUBMIT_CONNECTOR\n```lean4\n"+CONNECTOR+"\n```",
            }[self.agent]
    output = tmp_path / "output/specguard/test"
    result = run_task("TASK_PRIVATE_DESCRIPTION", tmp_path, output,
                      {"model":"mock", "provider":"openai_compatible", "api_key_env":"UNUSED", "time_budget":10},
                      checker_factory=FakeChecker, workspace_factory=FakeWorkspace, client_factory=Client)
    assert result["verdict"] == "conflict" and result["evidence"] == "execution"
    assert "TASK_PRIVATE_DESCRIPTION" not in seen["tests"]
    assert "TASK_PRIVATE_DESCRIPTION" not in seen["source_selection"]
    assert "assert f(1) == 3" not in seen["spec"]
    assert not (output / "cert.lean").exists()
    assert (output / "intermediates/execution.lean").exists()
    assert FakeChecker.instances[-1].closed and FakeWorkspace.instances[-1].closed
    assert json.loads((output / "result.json").read_text())["in_progress"] is False


@pytest.mark.parametrize("codes,complete,expected", [('[2]',True,'no-conflict'),('[1,0]',False,'conflict'),('[2,0]',True,'inconclusive'),('[2]',False,'inconclusive'),('[]',True,'inconclusive')])
def test_label_blind_execution(tmp_path, codes, complete, expected):
    checker = FakeChecker(tmp_path)
    def compile(source, *, name, **kw):
        assert "ground_truth" not in source
        return (True, "info: "+codes) if name == "execute" else (False,"error")
    checker.compile = compile
    artifact = Artifact(TEST, ("demand_1",), {} if complete else {"demand_2":"missing fixture"})
    assert checker.check(SPEC, artifact, CONNECTOR)["verdict"] == expected


def test_proof_only_written_on_success(tmp_path):
    checker = FakeChecker(tmp_path)
    checker.compile = lambda *a, **k: (True, "info: 'evaluation' does not depend on any axioms")
    result = checker.check(SPEC, Artifact(TEST,("demand_1",),{}), CONNECTOR)
    assert result["evidence"] == "proof"
    assert (tmp_path / "cert.lean").exists()
    assert not proof_axioms_ok("depends on axioms: [sorryAx]")
    assert not proof_axioms_ok("")


def test_missing_demands_and_namespace_escape_rejected():
    with pytest.raises(GuardError):
        parse_tests('SUBMIT_TESTS\n```lean4\n'+TEST+'\n```\n```json\n{"supported":[],"unsupported":{}}\n```', ['demand_1'])
    for bad in [SPEC+'\nnamespace Bad\ndef x := 1\nend Bad', SPEC.replace('x + 1','sorry'), SPEC.replace('def run','def _root_.run')]:
        with pytest.raises(ValueError):
            lean_submission('SUBMIT_SPEC\n```lean4\n'+bad+'\n```', 'SUBMIT_SPEC', 'Spec')


def test_deadline_interrupts_blocking_work():
    with pytest.raises(DeadlineExceeded):
        with deadline(0.02): time.sleep(1)
    assert signal.getitimer(signal.ITIMER_REAL)[0] == 0


def test_preflight_failure_never_calls_a_provider(tmp_path):
    class Unavailable(FakeWorkspace):
        @staticmethod
        def preflight(): raise GuardError("sandbox unavailable")
    output = tmp_path / "output/specguard/preflight"
    result = run_task("task", tmp_path, output,
                      {"model":"mock", "provider":"openai_compatible", "api_key_env":"UNUSED", "time_budget":10},
                      checker_factory=FakeChecker, workspace_factory=Unavailable,
                      client_factory=lambda _: pytest.fail("paid client created before preflight"))
    assert result["verdict"] == "inconclusive"
    assert result["reason"] == "sandbox unavailable"
    assert (output / "intermediates/usage.json").exists()


def test_failed_lean_import_is_rejected_and_closed(monkeypatch):
    repl = OwnedRepl(lean_environment())
    monkeypatch.setattr(repl, "_start_container", lambda: object())
    monkeypatch.setattr(repl, "_send_unsafe", lambda *a, **kw: (0, [{"severity":"error", "data":"bad import"}]))
    closed = []
    monkeypatch.setattr(repl, "close", lambda: closed.append(True))
    with pytest.raises(GuardError, match="bad import"):
        repl.start()
    assert closed


def test_interrupted_startup_keeps_result_and_cleans_own_checker(tmp_path):
    class Interrupted(FakeChecker):
        def start(self): raise KeyboardInterrupt()
    result = run_task("task", tmp_path, tmp_path / "output/specguard/interrupted",
                      {"model":"mock", "provider":"openai_compatible", "api_key_env":"UNUSED", "time_budget":10},
                      checker_factory=Interrupted, workspace_factory=FakeWorkspace,
                      client_factory=lambda _: pytest.fail("no model needed"))
    assert result["interrupted"] and not result["in_progress"]
    assert Interrupted.instances[-1].closed


def test_runtime_uses_its_own_elan_home(monkeypatch):
    from conflict_certifier.specguard.checker import _lake_env_vars
    from types import SimpleNamespace
    def run(argv, **kw):
        assert argv[0] == "/pinned/elan/bin/lake"
        assert kw["env"]["ELAN_HOME"] == "/pinned/elan"
        return SimpleNamespace(returncode=0, stdout="LEAN_PATH=/pinned/lib\nLEAN_SYSROOT=/pinned/toolchain\n")
    monkeypatch.setattr("conflict_certifier.specguard.checker.subprocess.run", run)
    assert _lake_env_vars("/pinned/workspace", "/pinned/elan/bin")["LEAN_SYSROOT"] == "/pinned/toolchain"


def test_repl_ownership_does_not_match_experiment_sweep(monkeypatch, tmp_path):
    from conflict_certifier.config import LeanEnv
    from conflict_certifier.lean.repl import CONTAINER_LABEL
    monkeypatch.setattr("conflict_certifier.specguard.checker._lake_env_vars", lambda *a: {"LEAN_PATH":"p", "LEAN_SYSROOT":"/lean"})
    calls = []
    monkeypatch.setattr("conflict_certifier.specguard.checker.subprocess.Popen", lambda argv, **kw: calls.append(argv))
    repl = OwnedRepl(LeanEnv(project_dir=tmp_path, repl_bin=tmp_path / "repl", container_mount_root=tmp_path))
    repl._start_container()
    argv = calls[0]
    assert CONTAINER_LABEL not in argv
    assert "specguard.owned=true" in argv
    assert "--network=none" in argv
    assert repl._cname.startswith("specguard-lean-")


def test_real_task_local_api_logger_persists(monkeypatch, tmp_path):
    from conflict_certifier.llm.call_log import LLMCallLogger
    from conflict_certifier.llm.client import LLMClient
    logger = LLMCallLogger(tmp_path)
    logger.start_task("run")
    class LoggedClient(LLMClient):
        def complete(self, system, user): return self.complete_conversation(system,[{"role":"user","content":user}])
        def complete_conversation(self, system, messages):
            h = self._begin_log(provider="mock", model="mock", system=system, messages=messages, parameters={})
            self._log_response(h, 1, {"text":"answer", "usage":{"input_tokens":2,"output_tokens":1}})
            return "answer"
    for name in ["discovery", "source_selection", "spec", "tests", "connector"]:
        LoggedClient(call_logger=logger, agent=name).complete("system", "input")
    assert {p.name for p in (tmp_path / "run").iterdir()} == {
        "discovery_log.json", "source_selection_log.json", "spec_log.json", "test_log.json", "connector_log.json"}
    log = json.loads((tmp_path / "run/spec_log.json").read_text())
    assert log["api_calls"][0]["response"]["usage"]["output_tokens"] == 1
    assert log["conversation"][-1]["content"] == "answer"


@pytest.mark.lean
@pytest.mark.skipif(os.environ.get("SPECGUARD_LEAN_TEST") != "1", reason="explicit existing Lean runtime required")
def test_real_lean_certificate(tmp_path):
    checker = Checker(tmp_path)
    checker.preflight()
    try:
        checker.start()
        result = checker.check(SPEC, Artifact(TEST,("demand_1",),{}), CONNECTOR)
        assert result["verdict"] == "conflict" and result["evidence"] == "proof"
        cert = (tmp_path / "cert.lean").read_text()
        assert checker.compile(cert, name="independent_recheck")[0]
        clean = checker.check(SPEC, Artifact(TEST.replace('⟨1, 3⟩', '⟨1, 2⟩'),("demand_1",),{}), CONNECTOR)
        assert clean["verdict"] == "no-conflict" and clean["evidence"] == "proof"
        unsupported = CONNECTOR.replace('some (Spec.run c.input == c.expected)', 'none')
        assert checker.check(SPEC, Artifact(TEST,("demand_1",),{}), unsupported)["verdict"] == "inconclusive"
    finally:
        checker.close()


@pytest.mark.lean
@pytest.mark.skipif(os.environ.get("SPECGUARD_FULL_TEST") != "1", reason="explicit prepared sandbox and Lean runtime required")
def test_real_runtime_pipeline_with_scripted_models(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "src/pkg.py").write_text("def f(x): return x + 1")
    (tmp_path / "tests/test_pkg.py").write_text("from pkg import f\ndef test_f():\n    assert f(1) == 3")
    class Scripted:
        def __init__(self, name): self.name = name
        def complete_conversation(self, system, messages):
            if len(messages) > 1:
                pytest.fail("Unexpected agent retry: " + str(messages[-1]))
            return {
                "discovery": '{"action":"submit","tests":["tests/test_pkg.py::test_f"],"helpers":[]}',
                "source_selection": '{"action":"submit","paths":["src"]}',
                "spec": 'SUBMIT_SPEC\n```lean4\n'+SPEC+'\n```',
                "tests": 'SUBMIT_TESTS\n```lean4\n'+TEST+'\n```\n```json\n{"supported":["demand_1"],"unsupported":{}}\n```',
                "connector": 'SUBMIT_CONNECTOR\n```lean4\n'+CONNECTOR+'\n```',
            }[self.name]
    output = tmp_path / "output/specguard/full"
    result = run_task("Return the input plus one", tmp_path, output,
                      {"model":"scripted", "provider":"openai_compatible", "api_key_env":"UNUSED", "time_budget":300},
                      client_factory=Scripted)
    assert result["verdict"] == "conflict", result
    assert result["evidence"] == "proof"
    assert (output / "cert.lean").exists()
    assert (output / "config/verification.json").exists()
