"""CLI agents, independent of the experiment agents and their prompts."""
from __future__ import annotations

import json
import re
from pathlib import Path
from .layout import artifact_path

from .artifacts import (GuardError, assemble, connector_contract, lean_submission,
                        spec_contract, test_submission, tests_contract)
from .discovery import prompt


def spec_agent(client, checker, box, task: str, output: Path, *,
               max_turns=120, max_submissions=10, exec_timeout=120) -> str:
    messages = [{"role": "user", "content": "Task:\n" + task + "\n\nExplore the implementation in /workspace."}]
    submissions = 0
    for turn in range(max_turns):
        response = client.complete_conversation(prompt("spec"), messages)
        messages.append({"role": "assistant", "content": response})
        try:
            if response.strip().startswith("SUBMIT_SPEC"):
                submissions += 1
                code = lean_submission(response, "SUBMIT_SPEC", "Spec")
                (artifact_path(output, "spec_attempt.lean")).write_text(assemble(code))
                ok, result = checker.compile(assemble(code, spec_contract()), name=f"spec_{turn}")
                if ok:
                    (artifact_path(output, "spec.lean")).write_text(assemble(code))
                    return code
            else:
                blocks = re.findall(r"```(?:bash|sh)\s*\n(.*?)```", response, re.S)
                if len(blocks) != 1:
                    raise GuardError("Return one bash block or SUBMIT_SPEC with one lean4 block")
                result = box.run(blocks[0], timeout=exec_timeout)
        except ValueError as exc:
            result = str(exc)
        if submissions >= max_submissions:
            raise GuardError("SpecAgent exhausted its submission budget")
        messages.append({"role": "user", "content": result[-12000:] + f"\nTurns remaining: {max_turns-1-turn}"})
    raise GuardError("SpecAgent exhausted its turn budget")


def test_agent(client, checker, context: dict, output: Path, *, max_submissions=10):
    # Explicitly omit discovery rationale and the natural-language task.
    user = json.dumps({k: context[k] for k in ("locations", "demands", "files")}, ensure_ascii=False)
    messages = [{"role": "user", "content": user}]
    for turn in range(max_submissions):
        response = client.complete_conversation(prompt("test"), messages)
        messages.append({"role": "assistant", "content": response})
        try:
            artifact = test_submission(response, [d["id"] for d in context["demands"]])
            (artifact_path(output, "tests_attempt.lean")).write_text(assemble(artifact.code))
            ok, result = checker.compile(assemble(artifact.code, tests_contract(artifact)), name=f"tests_{turn}")
            if ok:
                (artifact_path(output, "tests.lean")).write_text(assemble(artifact.code))
                return artifact
        except ValueError as exc:
            result = str(exc)
        messages.append({"role": "user", "content": "Invalid submission:\n" + result[-6000:]})
    raise GuardError("TestAgent exhausted its submission budget")


def connector_agent(client, checker, spec, tests, output: Path, context: dict, *, max_submissions=6) -> str:
    # Explicitly omit discovery rationale and the natural-language task.
    source_context = json.dumps({k: context[k] for k in ("demands", "files")}, ensure_ascii=False)[:30000]
    messages = [{"role": "user", "content": "Specification:\n```lean4\n" + spec +
                 "\n```\nTest demands and scenarios:\n```lean4\n" + tests.code +
                 "\n```\n\nTest source context (up to 30000 characters):\n" + source_context}]
    for turn in range(max_submissions):
        response = client.complete_conversation(prompt("connector"), messages)
        messages.append({"role": "assistant", "content": response})
        try:
            code = lean_submission(response, "SUBMIT_CONNECTOR", "Connector")
            (artifact_path(output, "connector_attempt.lean")).write_text(assemble(code))
            ok, result = checker.compile(assemble(spec, tests.code, code, connector_contract()), name=f"connector_{turn}")
            if ok:
                (artifact_path(output, "connector.lean")).write_text(assemble(code))
                return code
        except ValueError as exc:
            result = str(exc)
        messages.append({"role": "user", "content": "Invalid submission:\n" + result[-6000:]})
    raise GuardError("ConnectorAgent exhausted its submission budget")
