"""One CLI invocation, with its own deadline, resources and persistent outputs."""
from __future__ import annotations

import json
import signal
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from .layout import artifact_path

from conflict_certifier.llm.call_log import LLMCallLogger
from conflict_certifier.llm.config import LLMConfig

from .agents import connector_agent, spec_agent, test_agent
from .artifacts import GuardError, write_json
from .checker import Checker
from .discovery import selection_loop, test_context
from .workspace import BashWorkspace, Repository
from .settings import execution_settings


class DeadlineExceeded(TimeoutError):
    pass


@contextmanager
def deadline(seconds: float):
    def timed_out(*_):
        raise DeadlineExceeded("Overall time budget exhausted")
    def interrupted(*_):
        raise KeyboardInterrupt()
    old_alarm = signal.signal(signal.SIGALRM, timed_out)
    old_term = signal.signal(signal.SIGTERM, interrupted)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old_alarm)
        signal.signal(signal.SIGTERM, old_term)


def run_task(task: str, root: Path, output: Path, config: dict, *,
             checker_factory=Checker, workspace_factory=BashWorkspace, client_factory=None) -> dict:
    started = time.monotonic()
    config = execution_settings(config)
    output.mkdir(parents=True, exist_ok=False)
    write_json(artifact_path(output, "config.json"), config)
    write_json(artifact_path(output, "result.json"), {"verdict": None, "in_progress": True})
    (artifact_path(output, "task.txt")).write_text(task, encoding="utf8")
    logger = LLMCallLogger(output)
    logger.start_task("trajs")
    llm = LLMConfig.from_dict(config)
    clients = {}
    def client(name):
        if name not in clients:
            clients[name] = (client_factory(name) if client_factory else
                             llm.build(call_logger=logger, agent=name))
        return clients[name]
    checker = None
    box = None
    temp = tempfile.TemporaryDirectory(prefix="specguard-")
    result = {"verdict": "inconclusive", "evidence": None, "reason": "Run did not complete"}
    stage = "preflight"
    def progress(name):
        nonlocal stage
        stage = name
        print(f"[specguard] {name}", file=sys.stderr, flush=True)
        write_json(artifact_path(output, "progress.json"), {"stage": stage, "elapsed_seconds": time.monotonic()-started})
    try:
        with deadline(config["time_budget"]):
            progress("preflight")
            workspace_factory.preflight()
            checker_factory.preflight()
            checker = checker_factory(output)
            checker.timeout = config["lean_timeout"]
            checker.start()
            progress("snapshot")
            repo = Repository(root)
            write_json(artifact_path(output, "inputs.json"), {"repository": str(root.resolve()), "task": task,
                                               "file_hashes": repo.hashes(), "omitted_files": repo.omitted})
            progress("test discovery")
            selection = selection_loop(client("discovery"), repo, task, max_turns=config["discovery_turns"])
            context = test_context(repo, selection)
            write_json(artifact_path(output, "selected_tests.json"), context)
            progress("source selection")
            sources = selection_loop(client("source_selection"), repo, "", source_only=True,
                                     max_turns=config["source_selection_turns"])
            work = Path(temp.name) / "source"
            work.mkdir()
            copied = repo.prepare_source(sources["paths"], set(context["private_files"]), work)
            write_json(artifact_path(output, "selected_source.json"), {"paths": copied})
            progress("isolated workspace")
            box = workspace_factory(work)
            box.prepare()
            # A shell/runtime smoke check before spending on formalization.
            health = box.run("python -c 'import sys; print(sys.version)'", timeout=60)
            if not health.startswith("exit=0\n"):
                raise GuardError("Prepared Python runtime could not start: " + health[:1000])
            progress("specification")
            spec = spec_agent(client("spec"), checker, box, task, output,
                              max_turns=config["spec_turns"], max_submissions=config["spec_submissions"],
                              exec_timeout=config["exec_timeout"])
            progress("test translation")
            tests = test_agent(client("tests"), checker, context, output, max_submissions=config["test_submissions"])
            write_json(artifact_path(output, "test_coverage.json"), {"supported": tests.supported,
                                                      "unsupported": tests.unsupported})
            if not tests.supported:
                result = {"verdict": "inconclusive", "evidence": None,
                          "reason": "No selected demands could be translated", "unsupported_demands": tests.unsupported}
            else:
                progress("connection")
                connector = connector_agent(client("connector"), checker, spec, tests, output, context,
                                            max_submissions=config["connector_submissions"])
                progress("checking")
                result = checker.check(spec, tests, connector)
    except KeyboardInterrupt:
        result = {"verdict": "inconclusive", "evidence": None, "reason": "Interrupted by user", "interrupted": True}
    except Exception as exc:
        # Do not serialize provider exception strings: they can contain credentials.
        reason = str(exc) if isinstance(exc, (GuardError, DeadlineExceeded)) else type(exc).__name__ + " during " + stage
        result = {"verdict": "inconclusive", "evidence": None, "reason": reason, "error_type": type(exc).__name__}
    finally:
        errors = []
        for resource in (box, checker):
            if resource is not None:
                try:
                    resource.close()
                except Exception as exc:
                    errors.append(type(exc).__name__)
        try:
            temp.cleanup()
        except OSError as exc:
            errors.append(type(exc).__name__)
        usage = {}
        for name, c in clients.items():
            if hasattr(c, "usage"):
                usage[name] = c.usage.total()
        write_json(artifact_path(output, "usage.json"), {"agents": usage, "elapsed_seconds": time.monotonic()-started})
        for path in (output / "trajs").glob("*_log.json"):
            if path.name == "checking_log.json":
                continue
            data = json.loads(path.read_text())
            data["in_progress"] = False
            write_json(path, data)
        result.update({"in_progress": False, "stage": stage, "elapsed_seconds": time.monotonic()-started,
                       "output": str(output), "scope": "automatically selected existing tests"})
        if errors:
            result["cleanup_errors"] = errors
        write_json(artifact_path(output, "result.json"), result)
    return result
