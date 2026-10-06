"""Read-only automatic selection; no repository imports or test execution."""
from __future__ import annotations

import ast
import json
from pathlib import Path

from .artifacts import GuardError, json_response
from .workspace import Repository, excluded


def prompt(name: str) -> str:
    return (Path(__file__).parent / "prompts" / f"{name}.txt").read_text()


def functions(source: str) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise GuardError(f"Python source could not be parsed: {exc.msg}") from exc
    found = {}
    def walk(nodes, prefix=""):
        for node in nodes:
            if isinstance(node, ast.ClassDef):
                walk(node.body, prefix + node.name + "::")
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                found[prefix + node.name] = node
    walk(tree.body)
    return found


def selection_loop(client, repo: Repository, task: str, *, source_only: bool = False, max_turns=40) -> dict:
    system = prompt("source_selection" if source_only else "discovery")
    messages = [{"role": "user", "content": (
        "Identify implementation paths in this repository."
        if source_only else "Task description:\n" + task
    )}]
    metadata = {"pyproject.toml", "setup.cfg", "setup.py", "MANIFEST.in"}
    for _ in range(max_turns):
        response = client.complete_conversation(system, messages)
        messages.append({"role": "assistant", "content": response})
        try:
            data = json_response(response)
            action = data.get("action")
            if action == "submit":
                key = "paths" if source_only else "tests"
                values = data.get(key)
                if not isinstance(values, list) or not all(isinstance(s, str) for s in values):
                    raise GuardError(f"{key} must be an array of strings")
                if len(values) > 200:
                    raise GuardError("At most 200 selected paths/test functions per invocation")
                if not source_only:
                    for selector in values:
                        path, sep, name = selector.partition("::")
                        if not sep or name not in functions(repo.read(path)):
                            raise GuardError(f"Selected test function does not exist: {selector}. Read the file and correct the selector.")
                    helpers = data.get("helpers", [])
                    if not isinstance(helpers, list) or not all(isinstance(s, str) for s in helpers):
                        raise GuardError("helpers must be an array of repository file paths")
                    for helper in helpers:
                        repo.read(helper)
                    if values:
                        # Return invalid/oversized selections to discovery for
                        # repair, not an unrecoverable post-discovery exception.
                        test_context(repo, data)
                return data
            offset = data.get("offset", 0)
            if not isinstance(offset, int) or offset < 0:
                raise GuardError("offset must be a nonnegative integer")
            if action == "list":
                prefix = data.get("prefix", "")
                if not isinstance(prefix, str):
                    raise GuardError("prefix must be a string")
                names = sorted(p for p in repo.files if p.startswith(prefix))
                answer = {"files": names[offset:offset+200], "total": len(names), "next_offset": offset+200}
            elif action == "read":
                path = data.get("path", "")
                if source_only and path not in metadata:
                    raise GuardError("Source selection can read packaging metadata only")
                lines = repo.read(path).splitlines()
                answer = {"path": path, "lines": lines[offset:offset+160], "total_lines": len(lines),
                          "next_offset": offset+160}
            elif action == "search" and not source_only:
                query = data.get("query")
                if not isinstance(query, str) or not query:
                    raise GuardError("search requires a nonempty literal query")
                hits = [{"path": p, "line": i+1, "text": line[:350]}
                        for p, text in repo.files.items() for i, line in enumerate(text.splitlines())
                        if query.lower() in line.lower()]
                answer = {"matches": hits[offset:offset+60], "total": len(hits), "next_offset": offset+60}
            else:
                raise GuardError("Unknown action")
        except (GuardError, TypeError) as exc:
            answer = {"error": str(exc)}
        messages.append({"role": "user", "content": json.dumps(answer, ensure_ascii=False)[:22000]})
    raise GuardError("Discovery exhausted its turn budget")


def test_context(repo: Repository, selection: dict) -> dict:
    selected = list(dict.fromkeys(selection["tests"]))
    if not selected:
        raise GuardError("No relevant tests were found")
    sources = {}
    demands = []
    locations = []
    for selector in selected:
        path, sep, name = selector.partition("::")
        if not sep:
            raise GuardError("Select individual functions using path.py::Class::test_name or path.py::test_name")
        source = repo.read(path)
        node = functions(source).get(name)
        if node is None:
            raise GuardError(f"Selected test function does not exist: {selector}")
        sources[path] = source
        locations.append({"selector": selector, "line": node.lineno, "end_line": node.end_lineno})
        assertions = []
        for n in ast.walk(node):
            if isinstance(n, ast.Assert):
                assertions.append(n)
            elif isinstance(n, ast.Call):
                f = n.func
                called = f.attr if isinstance(f, ast.Attribute) else f.id if isinstance(f, ast.Name) else ""
                if called.startswith("assert") or called in {"raises", "warns"}:
                    assertions.append(n)
        # A helper-based/dynamic test is still an obligation, not silently empty.
        if not assertions:
            assertions = [node]
        for n in sorted(assertions, key=lambda a: (a.lineno, a.col_offset)):
            demands.append({"id": f"demand_{len(demands)+1}", "selector": selector,
                            "line": n.lineno, "source": ast.get_source_segment(source, n),
                            "whole_function": n is node,
                            "instruction": "Include all parameter cases, setup, and helper demands associated with this source location."})
        # Include conftest along the directory chain automatically.
        for parent in (Path(path).parent, *Path(path).parent.parents):
            conf = (parent / "conftest.py").as_posix()
            if conf in repo.files:
                sources[conf] = repo.files[conf]
    for helper in selection.get("helpers", []):
        sources[helper] = repo.read(helper)
    if sum(len(s) for s in sources.values()) > 180000:
        raise GuardError("Selected test context is too large (180000 characters); cannot check it faithfully within this invocation")
    return {"locations": locations, "demands": demands, "files": sources,
            "private_files": sorted(sources)}


def fallback_source_paths(repo: Repository) -> list[str]:
    """Deterministic candidates displayed as hints, never an unrestricted fallback."""
    return sorted({p.split('/')[0] for p in repo.files
                   if p.endswith('.py') and not excluded(p, tests=True)})
