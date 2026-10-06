"""Neutral semantic connector for the SWE-bench Lean track."""

from __future__ import annotations

from conflict_certifier.lean.runner import LeanRunner
from conflict_certifier.llm.client import LLMClient
from conflict_certifier.tracks.swebench.artifacts import (
    ArtifactError,
    ConnectorArtifact,
    assemble_lean,
    parse_connector_response,
    strip_mathlib_import,
    validate_connector_contract,
)
from conflict_certifier.tracks.swebench.prompting import load_prompt, render_prompt


DIRECT_CONNECTOR = """\
namespace Connector

def check (case : Tests.Case) : Option Bool :=
  some (Spec.run case.input == case.expected)

end Connector
"""


_SYSTEM = load_prompt("connector_system.txt")
_ORIGINAL_SYSTEM = load_prompt("original_connector_system.txt")


class SweConnectorAgent:
    system_prompt = _SYSTEM

    def __init__(self, llm: LLMClient, runner: LeanRunner, max_submissions: int = 6,
                 *, original: bool = False):
        if max_submissions < 1:
            raise ValueError("ConnectorAgent submission limit must be positive")
        self._llm = llm
        self._runner = runner
        self._max_submissions = max_submissions
        self._original = original
        self.system_prompt = _ORIGINAL_SYSTEM if original else _SYSTEM

    @staticmethod
    def _prompt(spec: str, test_model: str, test_cases: str,
                inputs: tuple[str, ...] | list[str], context: str) -> str:
        input_lines = "\n".join(
            f"  - input_{i}: `{value}`" for i, value in enumerate(inputs)
        ) or "  (none)"
        return (
            "## General specification\n"
            f"```lean4\n{strip_mathlib_import(spec)}\n```\n\n"
            "## Frozen input expressions\n"
            f"{input_lines}\n\n"
            "## Supporting input context\n"
            f"```python\n{context.strip()}\n```\n\n"
            "## Neutral test model\n"
            f"```lean4\n{strip_mathlib_import(test_model)}\n```\n\n"
            "## Neutral concrete test demands\n"
            f"```lean4\n{strip_mathlib_import(test_cases)}\n```\n\n"
            "Connect the demands to Spec.run. Return SUBMIT_CONNECTOR and one Lean block."
        )

    def run(self, spec: str, test_model: str, test_cases: str,
            inputs: tuple[str, ...] | list[str], context: str,
            *, label: str) -> ConnectorArtifact:
        # The one mechanical fast path. Failure only means the representations are
        # not definitionally identical; it is not an evaluation verdict.
        ok, direct_output = validate_connector_contract(
            self._runner, spec, test_model, test_cases, DIRECT_CONNECTOR,
            name=f"{label}_direct",
        )
        if ok:
            return ConnectorArtifact(
                lean_code=assemble_lean(DIRECT_CONNECTOR),
                compile_success=True,
                attempts_used=0,
                mode="direct",
                compile_output=direct_output,
            )

        conversation: list[dict] = [{
            "role": "user",
            "content": self._prompt(spec, test_model, test_cases, inputs, context),
        }]
        last_output, last_error, last_body = direct_output, "", ""
        total = self._max_submissions
        for attempt in range(total):
            response = self._llm.complete_conversation(self.system_prompt, conversation)
            conversation.append({"role": "assistant", "content": response})
            try:
                body = parse_connector_response(response)
                last_body = body
                ok, last_output = validate_connector_contract(
                    self._runner, spec, test_model, test_cases, body,
                    name=f"{label}_{attempt}",
                )
                if ok:
                    return ConnectorArtifact(
                        lean_code=assemble_lean(body),
                        compile_success=True,
                        attempts_used=attempt + 1,
                        mode="llm",
                        conversation=conversation,
                        compile_output=last_output,
                    )
                last_error = f"connector contract did not compile:\n{last_output[-2500:]}"
            except ArtifactError as exc:
                last_error = str(exc)
            if attempt < total - 1:
                conversation.append({
                    "role": "user",
                    "content": render_prompt(
                        "original_connector_retry.txt" if self._original
                        else "connector_retry.txt", error=last_error),
                })
        return ConnectorArtifact(
            lean_code=assemble_lean(last_body) if last_body else "",
            compile_success=False,
            attempts_used=total,
            mode="failure",
            conversation=conversation,
            compile_output=last_output,
            error=last_error or "connector produced no valid submission",
        )
