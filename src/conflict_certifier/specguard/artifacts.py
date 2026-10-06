"""CLI-owned protocols, persistent output, and formalization contracts."""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from conflict_certifier.lean.safety import code_only, validate_safe_lean


class GuardError(ValueError):
    pass


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    tmp.replace(path)


def json_response(text: str) -> dict:
    text = text.strip()
    if text.startswith("```json") and text.endswith("```"):
        text = text[7:-3].strip()
    try:
        value = json.loads(text)
    except (ValueError, TypeError) as exc:
        raise GuardError("Return one JSON object, with no surrounding prose") from exc
    if not isinstance(value, dict):
        raise GuardError("Expected a JSON object")
    return value


def assemble(*parts: str) -> str:
    return "import Mathlib\n\n" + "\n\n".join(parts) + "\n"


def lean_submission(text: str, marker: str, namespace: str) -> str:
    if not text.strip().startswith(marker):
        raise GuardError(f"Expected {marker}")
    blocks = re.findall(r"```lean4?\s*\n(.*?)```", text, re.S)
    if len(blocks) != 1:
        raise GuardError("Expected exactly one Lean code block")
    body = blocks[0].strip()
    validate_safe_lean(body, label=namespace)
    # Keep agent definitions out of other agents' namespaces and the trusted harness.
    code = code_only(body).strip()
    if not code.startswith(f"namespace {namespace}\n") or not re.search(
        rf"\bend\s+{namespace}\s*$", code
    ):
        raise GuardError(f"Wrap the entire artifact in namespace {namespace} ... end {namespace}")
    for token in ("_root_", "SGHarness", "attribute", "initialize", "builtin_initialize"):
        if re.search(rf"\b{token}\b", code):
            raise GuardError(f"Forbidden artifact token: {token}")
    # A closed outer namespace followed by another declaration could redefine the harness.
    if len(re.findall(r"\bnamespace\b", code)) != 1 or re.search(
        r"^\s*end\b", code[:-len(f"end {namespace}")], re.M
    ):
        raise GuardError("Nested namespaces/sections are not part of the artifact protocol")
    return body


@dataclass(frozen=True)
class TestArtifact:
    code: str
    supported: tuple[str, ...]
    unsupported: dict[str, str]

    def harness(self) -> str:
        lists = ", ".join(f"Tests.{name}" for name in self.supported)
        return f"def sgDemands : List Tests.Case := ([{lists}] : List (List Tests.Case)).flatten"


def test_submission(text: str, demand_ids: list[str]) -> TestArtifact:
    # Metadata is outside Lean so unsupported source demands cannot disappear silently.
    match = re.search(r"```json\s*\n(.*?)```", text, re.S)
    if not match:
        raise GuardError("Include JSON with supported IDs and unsupported ID-to-reason mapping")
    data = json_response(match[1])
    yes, no = data.get("supported"), data.get("unsupported")
    if not isinstance(yes, list) or not all(isinstance(x, str) for x in yes):
        raise GuardError("supported must be an array of demand IDs")
    if not isinstance(no, dict) or not all(isinstance(x, str) and x.strip() for x in no.values()):
        raise GuardError("unsupported must map IDs to nonempty reasons")
    if len(set(yes)) != len(yes) or set(yes) & set(no) or set(yes) | set(no) != set(demand_ids):
        raise GuardError("Account for every supplied demand ID exactly once")
    return TestArtifact(lean_submission(text, "SUBMIT_TESTS", "Tests"), tuple(yes), no)


def spec_contract() -> str:
    return "def sgSpecContract (input : Spec.Input) : Spec.Output := Spec.run input\n#synth BEq Spec.Output"


def tests_contract(artifact: TestArtifact) -> str:
    return "\n".join(
        f"def sgContract_{name} : List Tests.Case := Tests.{name}\n#guard !Tests.{name}.isEmpty"
        for name in artifact.supported
    )


def connector_contract() -> str:
    return "def sgConnectorContract (c : Tests.Case) : Option Bool := Connector.check c"
