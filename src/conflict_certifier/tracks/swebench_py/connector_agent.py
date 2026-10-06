"""Semantic ConnectorAgent for SWE-bench Python."""

from __future__ import annotations

from conflict_certifier.llm.client import LLMClient
from conflict_certifier.tracks.swebench_py.artifacts import ArtifactError, PythonArtifact, parse_connector_response
from conflict_certifier.tracks.swebench_py.contracts import connector_contract
from conflict_certifier.tracks.swebench_py.prompting import load_prompt, render_prompt
from conflict_certifier.tracks.swebench_py.pyrunner import TaskPythonRunner


_SYSTEM = load_prompt("connector_system.txt")


class SwePythonConnectorAgent:
    system_prompt = _SYSTEM

    def __init__(self, llm: LLMClient, runner: TaskPythonRunner,
                 *, max_submissions: int = 6):
        if max_submissions < 1:
            raise ValueError("ConnectorAgent submission limit must be positive")
        self._llm = llm
        self._runner = runner
        self._max_submissions = max_submissions

    @staticmethod
    def _prompt(spec_code: str, tests_code: str, inputs: tuple[str, ...],
                context: str) -> str:
        input_lines = "\n".join(
            f"  - input_{number}: `{value}`" for number, value in enumerate(inputs))
        return (
            "## General specification\n"
            f"```python\n{spec_code.strip()}\n```\n\n"
            "## Frozen input expressions\n" + input_lines + "\n\n"
            "## Supporting input context\n"
            f"```python\n{context.strip()}\n```\n\n"
            "## Neutral concrete test demands\n"
            f"```python\n{tests_code.strip()}\n```\n\n"
            "Connect each demand to run. Return SUBMIT_CONNECTOR and one Python block."
        )

    def run(
        self,
        spec_code: str,
        tests_code: str,
        inputs: tuple[str, ...],
        context: str,
    ) -> PythonArtifact:
        conversation = [{
            "role": "user",
            "content": self._prompt(spec_code, tests_code, inputs, context),
        }]
        last_code = last_output = last_error = ""
        for attempt in range(self._max_submissions):
            response = self._llm.complete_conversation(self.system_prompt, conversation)
            conversation.append({"role": "assistant", "content": response})
            try:
                last_code = parse_connector_response(response)
                result = connector_contract(
                    self._runner, spec_code, tests_code, last_code,
                    inputs=inputs, context=context)
                last_output = result.output
                if result.infrastructure_error or result.timed_out:
                    return PythonArtifact(
                        last_code, False, attempt + 1, conversation, last_output,
                        "execution infrastructure failure", True)
                if result.ok:
                    return PythonArtifact(
                        last_code, True, attempt + 1, conversation, last_output)
                last_error = "connector contract failed:\n" + last_output[-2500:]
            except ArtifactError as exc:
                last_error = str(exc)
            if attempt < self._max_submissions - 1:
                conversation.append({
                    "role": "user",
                    "content": render_prompt("connector_retry.txt", error=last_error),
                })
        return PythonArtifact(
            last_code, False, self._max_submissions, conversation, last_output,
            last_error or "ConnectorAgent produced no valid submission")
