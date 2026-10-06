"""SpecAgent for the SWE-bench Lean track."""

from __future__ import annotations

import re

from conflict_certifier.lean.runner import LeanRunner
from conflict_certifier.llm.client import LLMClient
from conflict_certifier.tracks.swebench.artifacts import (
    ArtifactError,
    SpecArtifact,
    assemble_lean,
    parse_spec_response,
    validate_spec_contract,
)
from conflict_certifier.tracks.swebench.source import RepoContainer
from conflict_certifier.tracks.swebench.prompting import load_prompt, render_prompt


_BASH_BLOCK_RE = re.compile(r"```(?:bash|sh|shell)\s*\n(.*?)```", re.DOTALL)
_BASH_OUT_CAP = 6000
_COMPILE_OUT_CAP = 2500


_SYSTEM = load_prompt("spec_system.txt")


class SweCodebaseSpecAgent:
    system_prompt = _SYSTEM

    def __init__(self, llm: LLMClient, runner: LeanRunner, max_turns: int = 120,
                 max_submissions: int = 10, exec_timeout: int = 120):
        if max_turns < 1 or max_submissions < 1:
            raise ValueError("SpecAgent turn and submission limits must be positive")
        self._llm = llm
        self._runner = runner
        self._max_turns = max_turns
        self._max_submissions = max_submissions
        self._exec_timeout = exec_timeout

    @staticmethod
    def _initial_prompt(issue: str, repo: str, total_turns: int,
                        inputs: tuple[str, ...] | list[str], context: str) -> str:
        input_lines = "\n".join(f"  - input_{i}: `{value}`"
                                for i, value in enumerate(inputs)) or "  (none)"
        return (
            f"You are in `/testbed`, the sealed `{repo}` repository at its buggy commit. "
            f"You have at most {total_turns} turns.\n\n"
            f"## Issue\n{issue.strip()}\n\n"
            "## Input examples (grounding only; outputs are not provided)\n"
            f"{input_lines}\n\n"
            "## Supporting input context\n"
            f"```python\n{context.strip()}\n```\n\n"
            "Explore the implementation, infer the intended general rule, and submit the "
            "single `Spec.run` model when ready."
        )
    @staticmethod
    def _turn_tag(turn: int, total: int) -> str:
        remaining = total - turn - 1
        if remaining == 0:
            return "\n\nFINAL TURN: submit SUBMIT_SPEC and one valid Lean block now."
        if remaining <= 3:
            return f"\n\nTurn {turn + 1}/{total}; {remaining} remain. Finish and submit soon."
        return f"\n\nTurn {turn + 1}/{total}; {remaining} remain."

    @staticmethod
    def _bash(text: str) -> str | None:
        match = _BASH_BLOCK_RE.search(text)
        return match.group(1).strip() if match and match.group(1).strip() else None

    def run(self, instance_id: str, issue: str, repo: str,
            inputs: tuple[str, ...] | list[str], context: str,
            *, label: str = "swe_spec") -> SpecArtifact:
        total = self._max_turns
        conversation: list[dict] = [{
            "role": "user",
            "content": self._initial_prompt(issue, repo or instance_id, total, inputs, context)
                       + self._turn_tag(0, total),
        }]
        last_body = last_output = last_error = ""
        submissions = 0
        with RepoContainer(instance_id, exec_timeout=self._exec_timeout) as box:
            for turn in range(total):
                response = self._llm.complete_conversation(self.system_prompt, conversation)
                conversation.append({"role": "assistant", "content": response})

                if "SUBMIT_SPEC" in response:
                    submissions += 1
                    try:
                        body = parse_spec_response(response)
                        last_body = body
                        ok, last_output = validate_spec_contract(
                            self._runner, body, name=f"{label}_{submissions}")
                        if ok:
                            return SpecArtifact(
                                lean_code=assemble_lean(body),
                                compile_success=True,
                                attempts_used=submissions,
                                conversation=conversation,
                                compile_output=last_output,
                            )
                        last_error = f"spec contract did not compile:\n{last_output[-_COMPILE_OUT_CAP:]}"
                    except ArtifactError as exc:
                        last_error = str(exc)
                    if submissions >= self._max_submissions:
                        last_error = (
                            f"SpecAgent exhausted its {self._max_submissions} Lean "
                            f"submissions. Last rejection: {last_error}"
                        )
                        break
                    conversation.append({
                        "role": "user",
                        "content": render_prompt(
                            "spec_retry_submission.txt", error=last_error,
                            turn_tag=self._turn_tag(turn + 1, total)),
                    })
                    continue

                command = self._bash(response)
                if command is not None:
                    code, output = box.bash(command)
                    if len(output) > _BASH_OUT_CAP:
                        output = output[:_BASH_OUT_CAP] + "\n... output truncated ..."
                    conversation.append({
                        "role": "user",
                        "content": f"Command exited {code}:\n```text\n{output}\n```"
                                   + self._turn_tag(turn + 1, total),
                    })
                else:
                    conversation.append({
                        "role": "user",
                        "content": render_prompt(
                            "spec_retry_invalid_action.txt",
                            turn_tag=self._turn_tag(turn + 1, total)),
                    })

        return SpecArtifact(
            lean_code=assemble_lean(last_body) if last_body else "",
            compile_success=False,
            attempts_used=submissions,
            conversation=conversation,
            compile_output=last_output,
            error=last_error or "SpecAgent exhausted its turns without a valid submission",
        )
