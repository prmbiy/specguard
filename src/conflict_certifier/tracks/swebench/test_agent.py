"""TestAgent for SWE-bench Lean.

The agent sees two shuffled, neutrally numbered test variants and the same
answer-free input slice given to SpecAgent. It never sees the issue, repository,
specification, connector, or the private good/bad ordering.
"""

from __future__ import annotations

from conflict_certifier.lean.runner import LeanRunner
from conflict_certifier.llm.client import LLMClient
from conflict_certifier.tracks.swebench.artifacts import (
    ArtifactError,
    TestArtifact,
    assemble_lean,
    parse_tests_response,
    validate_original_tests_contract,
    validate_tests_contract,
)
from conflict_certifier.tracks.swebench.prompting import load_prompt, render_prompt


_SYSTEM = load_prompt("test_system.txt")
_ORIGINAL_SYSTEM = load_prompt("original_test_system.txt")


class SweTestAgent:
    system_prompt = _SYSTEM

    def __init__(self, llm: LLMClient, runner: LeanRunner, max_submissions: int = 10,
                 *, original: bool = False):
        if max_submissions < 1:
            raise ValueError("TestAgent submission limit must be positive")
        self._llm = llm
        self._runner = runner
        self._max_submissions = max_submissions
        self._original = original
        self.system_prompt = _ORIGINAL_SYSTEM if original else _SYSTEM

    @staticmethod
    def _prompt(inputs: tuple[str, ...] | list[str], context: str,
                test_0_patch: str, test_1_patch: str) -> str:
        input_lines = "\n".join(f"  - input_{i}: `{value}`"
                                for i, value in enumerate(inputs)) or "  (none)"
        return (
            "## Pre-decided input expressions\n"
            f"{input_lines}\n\n"
            "## Supporting input context\n"
            f"```python\n{context.strip()}\n```\n\n"
            "## test_0\n"
            f"```diff\n{test_0_patch.strip()}\n```\n\n"
            "## test_1\n"
            f"```diff\n{test_1_patch.strip()}\n```\n\n"
            "Transcribe only the assertions whose demanded outcomes differ between "
            "these versions. Preserve each complete assertion in its comment and both "
            "demanded values faithfully."
        )

    @staticmethod
    def _original_prompt(inputs: tuple[str, ...] | list[str], context: str,
                         test_patch: str) -> str:
        input_lines = "\n".join(f"  - input_{i}: `{value}`"
                                for i, value in enumerate(inputs)) or "  (none)"
        return (
            "## Pre-decided input expressions (supporting context only)\n"
            f"{input_lines}\n\n"
            "## Supporting input context\n"
            f"```python\n{context.strip()}\n```\n\n"
            "## Test patch\n"
            f"```diff\n{test_patch.strip()}\n```\n\n"
            "Faithfully transcribe the assertions introduced or updated by this patch. "
            "The patch itself determines which assertions to translate; do not use the "
            "input examples to omit assertions."
        )
    def run(self, inputs: tuple[str, ...] | list[str], context: str,
            test_0_patch: str, test_1_patch: str,
            *, label: str = "swe_tests") -> TestArtifact:
        total = self._max_submissions
        conversation: list[dict] = [{
            "role": "user",
            "content": (
                self._original_prompt(inputs, context, test_0_patch)
                if self._original else
                self._prompt(inputs, context, test_0_patch, test_1_patch)
            ),
        }]
        last_model = last_cases = last_output = last_error = ""
        for attempt in range(total):
            response = self._llm.complete_conversation(self.system_prompt, conversation)
            conversation.append({"role": "assistant", "content": response})
            try:
                model, cases = parse_tests_response(response)
                last_model, last_cases = model, cases
                validator = (validate_original_tests_contract if self._original
                             else validate_tests_contract)
                ok, last_output = validator(
                    self._runner, model, cases, name=f"{label}_{attempt}")
                if ok:
                    return TestArtifact(
                        model_code=assemble_lean(model),
                        cases_code=assemble_lean(cases),
                        compile_success=True,
                        attempts_used=attempt + 1,
                        conversation=conversation,
                        compile_output=last_output,
                    )
                last_error = f"test artifact did not satisfy its contract:\n{last_output[-2500:]}"
            except ArtifactError as exc:
                last_error = str(exc)
            if attempt < total - 1:
                conversation.append({
                    "role": "user",
                    "content": render_prompt(
                        "original_test_retry.txt" if self._original else "test_retry.txt",
                        error=last_error),
                })
        return TestArtifact(
            model_code=assemble_lean(last_model) if last_model else "",
            cases_code=assemble_lean(last_cases) if last_cases else "",
            compile_success=False,
            attempts_used=total,
            conversation=conversation,
            compile_output=last_output,
            error=last_error or "TestAgent produced no valid submission",
        )
