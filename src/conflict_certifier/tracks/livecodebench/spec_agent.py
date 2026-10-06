"""Compile-guided Lean SpecAgent with no hidden-test feedback."""

from __future__ import annotations

from dataclasses import dataclass, field

from conflict_certifier.tracks.livecodebench.artifacts import (
    ArtifactError,
    parse_spec_response,
    render_public_validation,
)
from conflict_certifier.tracks.livecodebench.prompting import load_prompt
from conflict_certifier.tracks.livecodebench.source import AgentTask


@dataclass
class SpecResult:
    body: str = ""
    compile_success: bool = False
    submissions: int = 0
    error: str = ""
    compile_output: str = ""
    conversation: list[dict] = field(default_factory=list)


class LiveCodeBenchSpecAgent:
    system_prompt = load_prompt("spec_system.txt")
    retry_template = load_prompt("spec_retry.txt")

    def __init__(self, llm, runner, *, max_submissions: int = 10):
        self.llm = llm
        self.runner = runner
        self.max_submissions = max(1, max_submissions)

    @staticmethod
    def initial_prompt(task: AgentTask) -> str:
        fields = "\n".join(
            f"- `{parameter.name}`: `{parameter.lean_type.source}`"
            for parameter in task.signature.parameters
        )
        return (
            f"## Problem\n\n{task.description.strip()}\n\n"
            f"## Fixed Lean types\n\n```lean4\n{task.signature.declarations}\n```\n\n"
            f"Input fields:\n{fields}\n\n"
            "Submit the implementation of `Spec.run`."
        )

    def run(self, task: AgentTask, *, label: str) -> SpecResult:
        conversation = [{"role": "user", "content": self.initial_prompt(task)}]
        last = SpecResult(conversation=conversation)
        for submission in range(1, self.max_submissions + 1):
            response = self.llm.complete_conversation(self.system_prompt, conversation)
            conversation.append({"role": "assistant", "content": response})
            try:
                body = parse_spec_response(response)
                source = render_public_validation(task, body)
                ok, output = self.runner.compile(source, name=f"{label}_{submission}")
                if ok:
                    return SpecResult(body, True, submission, "", output, conversation)
                error = output.strip()[-4000:] or "Lean compilation failed"
            except ArtifactError as exc:
                body, output, error = "", "", str(exc)
            last = SpecResult(body, False, submission, error, output, conversation)
            if submission < self.max_submissions:
                conversation.append({
                    "role": "user",
                    "content": self.retry_template.format(error=error),
                })
        return last
