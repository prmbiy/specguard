"""Interactive, codebase-grounded SpecAgent for SWE-bench Python."""

from __future__ import annotations

import re

from conflict_certifier.llm.client import LLMClient
from conflict_certifier.tracks.swebench_py.artifacts import ArtifactError, PythonArtifact, parse_spec_response
from conflict_certifier.tracks.swebench_py.contracts import spec_contract
from conflict_certifier.tracks.swebench_py.prompting import load_prompt, render_prompt
from conflict_certifier.tracks.swebench_py.pyrunner import TaskPythonRunner


_BASH_BLOCK = re.compile(r"```(?:bash|sh|shell)\s*\n(.*?)```", re.DOTALL)
_BASH_OUTPUT_CAP = 6000
_SYSTEM = load_prompt("spec_system.txt")


class SwePythonSpecAgent:
    system_prompt = _SYSTEM

    def __init__(
        self,
        llm: LLMClient,
        runner: TaskPythonRunner,
        *,
        max_turns: int = 120,
        max_submissions: int = 10,
        exec_timeout: int = 120,
    ):
        if max_turns < 1 or max_submissions < 1:
            raise ValueError("SpecAgent limits must be positive")
        self._llm = llm
        self._runner = runner
        self._max_turns = max_turns
        self._max_submissions = max_submissions
        self._exec_timeout = exec_timeout

    @staticmethod
    def _prompt(issue: str, repo: str, inputs: tuple[str, ...], context: str,
                total_turns: int) -> str:
        input_lines = "\n".join(
            f"  - input_{number}: `{value}`" for number, value in enumerate(inputs))
        return (
            f"You are in `/testbed`, the sealed `{repo}` repository at its buggy commit. "
            f"You have at most {total_turns} turns.\n\n"
            f"## Issue\n{issue.strip()}\n\n"
            "## Frozen input expressions (grounding only; outputs are not provided)\n"
            f"{input_lines}\n\n"
            "## Supporting input context\n"
            f"```python\n{context.strip()}\n```\n\n"
            "Explore the intended behavior and submit one general run(spec_input) model."
        )

    @staticmethod
    def _turn_tag(turn: int, total: int) -> str:
        remaining = total - turn - 1
        if remaining == 0:
            return "FINAL TURN: submit a valid SUBMIT_SPEC artifact now."
        return f"Turn {turn + 1}/{total}; {remaining} remain."

    @staticmethod
    def _bash(response: str) -> str | None:
        match = _BASH_BLOCK.search(response)
        return match.group(1).strip() if match and match.group(1).strip() else None

    def run(
        self,
        instance_id: str,
        issue: str,
        repo: str,
        inputs: tuple[str, ...],
        context: str,
    ) -> PythonArtifact:
        conversation = [{
            "role": "user",
            "content": self._prompt(issue, repo or instance_id, inputs, context,
                                    self._max_turns)
                       + "\n\n" + self._turn_tag(0, self._max_turns),
        }]
        submissions = 0
        last_code = last_output = last_error = ""
        for turn in range(self._max_turns):
            response = self._llm.complete_conversation(self.system_prompt, conversation)
            conversation.append({"role": "assistant", "content": response})
            if "SUBMIT_SPEC" in response:
                submissions += 1
                try:
                    last_code = parse_spec_response(response)
                    result = spec_contract(self._runner, last_code)
                    last_output = result.output
                    if result.infrastructure_error or result.timed_out:
                        return PythonArtifact(
                            last_code, False, submissions, conversation,
                            last_output, "execution infrastructure failure", True)
                    if result.ok:
                        return PythonArtifact(
                            last_code, True, submissions, conversation, last_output)
                    last_error = "spec contract failed:\n" + last_output[-2500:]
                except ArtifactError as exc:
                    last_error = str(exc)
                if submissions >= self._max_submissions:
                    break
                conversation.append({
                    "role": "user",
                    "content": render_prompt(
                        "spec_retry_submission.txt", error=last_error,
                        turn_tag=self._turn_tag(turn + 1, self._max_turns)),
                })
                continue
            command = self._bash(response)
            if command is None:
                conversation.append({
                    "role": "user",
                    "content": render_prompt(
                        "spec_retry_invalid_action.txt",
                        turn_tag=self._turn_tag(turn + 1, self._max_turns)),
                })
                continue
            exit_code, output = self._runner.bash(command, timeout=self._exec_timeout)
            if len(output) > _BASH_OUTPUT_CAP:
                output = output[:_BASH_OUTPUT_CAP] + "\n... output truncated ..."
            conversation.append({
                "role": "user",
                "content": f"Command exited {exit_code}:\n```text\n{output}\n```\n\n"
                           + self._turn_tag(turn + 1, self._max_turns),
            })
        return PythonArtifact(
            last_code, False, submissions, conversation, last_output,
            last_error or "SpecAgent ended without a valid submission")
