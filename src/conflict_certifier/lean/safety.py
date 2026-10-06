"""Shared safety policy for Lean source written by language models."""

from __future__ import annotations

import re


class LeanSafetyError(ValueError):
    """Lean source contains a capability unavailable to generated artifacts."""


def code_only(source: str) -> str:
    """Mask comments and literals before screening Lean tokens."""
    out: list[str] = []
    i, block_depth = 0, 0
    in_line = in_string = in_char = False
    while i < len(source):
        two = source[i:i + 2]
        ch = source[i]
        if in_line:
            if ch == "\n":
                in_line = False
                out.append(ch)
            else:
                out.append(" ")
            i += 1
            continue
        if block_depth:
            if two == "/-":
                block_depth += 1
                out.extend("  ")
                i += 2
            elif two == "-/":
                block_depth -= 1
                out.extend("  ")
                i += 2
            else:
                out.append("\n" if ch == "\n" else " ")
                i += 1
            continue
        if in_string or in_char:
            quote = '"' if in_string else "'"
            if ch == "\\" and i + 1 < len(source):
                out.extend("  ")
                i += 2
            elif ch == quote:
                in_string = in_char = False
                out.append(" ")
                i += 1
            else:
                out.append("\n" if ch == "\n" else " ")
                i += 1
            continue
        if two == "--":
            in_line = True
            out.extend("  ")
            i += 2
        elif two == "/-":
            block_depth = 1
            out.extend("  ")
            i += 2
        elif ch == '"':
            in_string = True
            out.append(" ")
            i += 1
        elif ch == "'":
            # Lean identifiers may end in apostrophes. Treat this as a
            # character literal only when a nearby closing quote exists.
            if source.find("'", i + 1, min(len(source), i + 8)) != -1:
                in_char = True
            out.append(" ")
            i += 1
        else:
            out.append(ch)
            i += 1
    return "".join(out)


_FORBIDDEN = re.compile(
    r"(?:"
    r"\b(?:import|sorry|axiom|opaque|unsafe|partial|extern|native_decide|"
    r"run_tac|macro|syntax|elab|notation|theorem|lemma|IO|System|Process)\b|"
    r"implemented_by|set_option|^\s*#"
    r")",
    re.MULTILINE,
)


def validate_safe_lean(source: str, *, label: str) -> None:
    """Reject dangerous or proof-bypassing constructs in generated Lean."""
    if not source.strip():
        raise LeanSafetyError(f"{label}: empty Lean artifact")
    match = _FORBIDDEN.search(code_only(source))
    if match:
        token = match.group(0).strip()
        raise LeanSafetyError(f"{label}: forbidden Lean construct {token!r}")
