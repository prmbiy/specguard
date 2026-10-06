"""Neutral, provenance-checked TestAgent for SWE-bench Python."""

from __future__ import annotations

from conflict_certifier.llm.client import LLMClient
from conflict_certifier.tracks.swebench_py.artifacts import ArtifactError, TestArtifact, parse_tests_response
from conflict_certifier.tracks.swebench_py.contracts import tests_contract
from conflict_certifier.tracks.swebench_py.prompting import load_prompt, render_prompt
from conflict_certifier.tracks.swebench_py.pyrunner import TaskPythonRunner


_SYSTEM = load_prompt("test_system.txt")


class SwePythonTestAgent:
    system_prompt = _SYSTEM

    def __init__(self, llm: LLMClient, runner: TaskPythonRunner,
                 *, max_submissions: int = 10):
        if max_submissions < 1:
            raise ValueError("TestAgent submission limit must be positive")
        self._llm = llm
        self._runner = runner
        self._max_submissions = max_submissions

    @staticmethod
    def _prompt(inputs: tuple[str, ...], context: str,
                test_0_patch: str, test_1_patch: str) -> str:
        input_lines = "\n".join(
            f"  - input_{number}: `{value}`" for number, value in enumerate(inputs))
        return (
            "## Frozen input expressions\n" + input_lines + "\n\n"
            "## Supporting input context\n"
            f"```python\n{context.strip()}\n```\n\n"
            "## test_0\n"
            f"```diff\n{test_0_patch.strip()}\n```\n\n"
            "## test_1\n"
            f"```diff\n{test_1_patch.strip()}\n```\n\n"
            "Return SUBMIT_TESTS and the single literal TESTS artifact."
        )

    def run(
        self,
        inputs: tuple[str, ...],
        context: str,
        test_0_patch: str,
        test_1_patch: str,
    ) -> TestArtifact:
        patches = (test_0_patch, test_1_patch)
        conversation = [{
            "role": "user",
            "content": self._prompt(inputs, context, *patches),
        }]
        last_code = last_output = last_error = ""
        last_tests: list[dict] = []
        for attempt in range(self._max_submissions):
            response = self._llm.complete_conversation(self.system_prompt, conversation)
            conversation.append({"role": "assistant", "content": response})
            try:
                last_code, last_tests = parse_tests_response(
                    response, patches=patches, input_count=len(inputs))
                result = tests_contract(self._runner, last_code)
                last_output = result.output
                if result.infrastructure_error or result.timed_out:
                    return TestArtifact(
                        last_code, False, attempt + 1, conversation, last_output,
                        "execution infrastructure failure", True, last_tests)
                if result.ok:
                    return TestArtifact(
                        last_code, True, attempt + 1, conversation,
                        last_output, "", False, last_tests)
                last_error = "test contract failed:\n" + last_output[-2500:]
            except ArtifactError as exc:
                last_error = str(exc)
            if attempt < self._max_submissions - 1:
                conversation.append({
                    "role": "user",
                    "content": render_prompt("test_retry.txt", error=last_error),
                })
        return TestArtifact(
            last_code, False, self._max_submissions, conversation, last_output,
            last_error or "TestAgent produced no valid submission", False, last_tests)
