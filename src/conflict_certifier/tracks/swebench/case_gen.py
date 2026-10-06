"""CaseGen for SWE-bench: invent NOVEL test scenarios (ideas doc 2.3, addon mode).

One plain LLM call — NOT an interactive agent. Given the GitHub issue (and, in the
default "with original" variant, the redacted test: inputs visible, every expected
value hidden), it writes N brand-new input scenarios exercising the same behavior.

Integrity: the input is already value-free and CaseGen doesn't know any answers
(nobody does — these scenarios exist nowhere), so nothing can leak to the SpecAgent.

Output contract: exactly N fenced ```python blocks, each a short self-contained
scenario ending with the probed expression compared to `<?>`. Parsed by regex,
re-asked up to `max_retries` times on a malformed reply. No compilation, no tools.

Downstream (run.py): the first `shown` scenarios go into the SpecAgent's prompt
("cover these too — define novel_1_spec..."); the rest are held out and recorded in
novel_cases.json for later holdout checking (python track) / recordkeeping (lean).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from conflict_certifier.llm.client import LLMClient

_PY_BLOCK_RE = re.compile(r"```(?:python|py)\s*\n(.*?)```", re.DOTALL)

_SYSTEM = """\
You invent NOVEL test scenarios for a behavior described in a GitHub issue.

You are given the issue (and possibly the existing test with every expected value
hidden). Write {n} NEW input scenarios for the SAME behavior — inputs that appear in
no existing test. Someone else will have to derive what each scenario should produce;
your job is only to pick good inputs.

Rules:
- Each scenario: one short self-contained Python-style snippet — minimal setup plus
  the probed call — ending with `== <?>` on the probed expression.
- NEVER write an expected output, not even as a comment or a guess. Only `<?>`.
- Vary meaningfully: 2 ordinary variations (different values/shapes than the existing
  test), 2 edge cases (empty, missing, boundary), 1 structurally different case.
- Stay on the SAME behavior the issue discusses — no drive-by testing of unrelated
  features. Prefer scenarios on behavior the issue's fix would NOT change (the
  surrounding contract), plus 1-2 squarely on the fixed behavior itself.
- Output EXACTLY {n} fenced ```python blocks and nothing else — no prose between them.
"""


@dataclass
class CaseGenResult:
    cases: list[str]
    retries_used: int
    conversation: list[dict] = field(default_factory=list)


class SweCaseGenAgent:
    def __init__(self, llm: LLMClient, n_cases: int = 5, max_retries: int = 3):
        self._llm = llm
        self._n = n_cases
        self._max_retries = max_retries

    def _prompt(self, issue: str, redacted_test: str | None) -> str:
        test_section = ""
        if redacted_test:
            test_section = ("\n\n## Existing test (expected values hidden) — write "
                            "scenarios that do NOT repeat these inputs\n"
                            f"```python\n{redacted_test.strip()}\n```")
        return (f"## GitHub issue\n{issue.strip()}{test_section}\n\n"
                f"Write exactly {self._n} novel scenarios as fenced python blocks.")

    def run(self, issue: str, redacted_test: str | None = None) -> CaseGenResult:
        system = _SYSTEM.format(n=self._n)
        conversation: list[dict] = [{"role": "user",
                                     "content": self._prompt(issue, redacted_test)}]
        for attempt in range(self._max_retries + 1):
            response = self._llm.complete_conversation(system, conversation)
            conversation.append({"role": "assistant", "content": response})
            blocks = [b.strip() for b in _PY_BLOCK_RE.findall(response) if b.strip()]
            # refuse any block that smuggles in an expected value after ==
            clean = [b for b in blocks if "<?>" in b]
            if len(clean) >= self._n:
                return CaseGenResult(cases=clean[:self._n], retries_used=attempt,
                                     conversation=conversation)
            if attempt < self._max_retries:
                conversation.append({"role": "user", "content":
                    f"I found {len(clean)} valid blocks; I need exactly {self._n} fenced "
                    "```python blocks, each ending its probed expression with `== <?>` "
                    "and containing no expected values. Resend all of them."})
        return CaseGenResult(cases=clean, retries_used=self._max_retries,
                             conversation=conversation)
