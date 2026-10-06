"""Native-tool SWE-bench Lean variant.  The evaluator and Lean checks are shared.

Only the model-facing action protocol differs from ``swebench-lean``: Responses
function calls replace fenced bash and Lean submission blocks.  The three
agents retain their original, separate information boundaries.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

from conflict_certifier.llm.call_log import LLMCallLogger, jsonable
from conflict_certifier.llm.client import LLMRefusalError, UsageTracker
from conflict_certifier.llm.config import LLMConfig
from conflict_certifier.tracks.swebench.artifacts import (
    ArtifactError, ConnectorArtifact, SpecArtifact, TestArtifact, assemble_lean,
    parse_connector_response, parse_spec_response, parse_tests_response,
    validate_connector_contract, validate_original_tests_contract,
    validate_spec_contract, validate_tests_contract,
)
from conflict_certifier.tracks.swebench.connector_agent import (
    DIRECT_CONNECTOR, SweConnectorAgent,
)
from conflict_certifier.tracks.swebench.prompting import load_prompt
from conflict_certifier.tracks.swebench.source import RepoContainer
from conflict_certifier.tracks.swebench.spec_agent import (
    _BASH_OUT_CAP, _COMPILE_OUT_CAP, SweCodebaseSpecAgent,
)
from conflict_certifier.tracks.swebench.test_agent import SweTestAgent


def _replace_once(source: str, old: str, new: str) -> str:
    if source.count(old) != 1:
        raise ValueError("SWE-bench prompt protocol changed; review tool prompt")
    return source.replace(old, new, 1)


# Preserve each existing prompt's semantic instructions; replace only how an
# agent acts.  The resulting full strings are saved in every task's agent log.
SPEC_SYSTEM = _replace_once(
    load_prompt("spec_system.txt"),
    "ONE ACTION PER TURN:\n"
    "  * Explore: return one ```bash``` block.\n"
    "  * Submit: return `SUBMIT_SPEC` and one ```lean4``` block.",
    "ONE ACTION PER TURN: call `bash` with one command to explore, or call "
    "`submit_spec` with the complete Lean module. Put bare Lean source in the "
    "tool argument, without fences or SUBMIT_SPEC. No other action is allowed.",
)


def _test_system(filename: str) -> str:
    source = load_prompt(filename)
    source = _replace_once(
        source, "Return exactly this protocol:\n\nSUBMIT_TESTS\nTEST_MODEL\n",
        "Call `submit_tests` with two string arguments: `test_model` and "
        "`test_cases`. Put bare Lean source in each argument, without fences, "
        "labels, or SUBMIT_TESTS. The following sketches define their content.\n\n"
        "test_model:\n",
    )
    return _replace_once(source, "\nTEST_CASES\n", "\ntest_cases:\n")


TEST_SYSTEM = _test_system("test_system.txt")
ORIGINAL_TEST_SYSTEM = _test_system("original_test_system.txt")


def _connector_system(filename: str) -> str:
    source = load_prompt(filename)
    return _replace_once(
        source, "Output exactly `SUBMIT_CONNECTOR` followed by one ```lean4``` block.",
        "Call `submit_connector` with the complete bare Lean module as its "
        "`lean` argument; do not add fences or SUBMIT_CONNECTOR.",
    )


CONNECTOR_SYSTEM = _connector_system("connector_system.txt")
ORIGINAL_CONNECTOR_SYSTEM = _connector_system("original_connector_system.txt")


def _tool(name: str, description: str, properties: dict) -> dict:
    return {
        "type": "function", "name": name, "description": description,
        "parameters": {
            "type": "object", "properties": properties,
            "required": list(properties), "additionalProperties": False,
        },
        "strict": True,
    }


SPEC_TOOLS = [
    _tool("bash", "Run one shell command inside the sealed /testbed checkout.",
          {"command": {"type": "string"}}),
    _tool("submit_spec", "Submit one general executable Lean specification.",
          {"lean": {"type": "string"}}),
]
TEST_TOOLS = [_tool(
    "submit_tests", "Submit the independent Lean test model and concrete demands.",
    {"test_model": {"type": "string"}, "test_cases": {"type": "string"}},
)]
CONNECTOR_TOOLS = [_tool(
    "submit_connector", "Submit the neutral Lean semantic connector.",
    {"lean": {"type": "string"}},
)]


@dataclass(frozen=True)
class Action:
    name: str
    arguments: dict
    call_id: str


def _replay_item(item: dict) -> dict | None:
    """Send only valid Responses input fields, not provider output metadata.

    OpenRouter's output function calls include nullable ``caller`` and
    ``namespace`` fields which its Responses *input* schema rejects.
    """
    kind = item.get("type")
    if kind == "function_call":
        return {key: item[key] for key in
                ("type", "call_id", "name", "arguments")}
    if kind == "reasoning":
        return {key: item[key] for key in
                ("type", "summary", "encrypted_content") if key in item}
    if kind == "message":
        return {key: item[key] for key in
                ("type", "role", "content") if key in item}
    return None


class ToolResponsesClient:
    """Thread-safe shared client; each agent invocation creates a fresh session."""

    def __init__(self, config: LLMConfig, *, call_logger: LLMCallLogger,
                 agent: str, prices: dict[str, float]):
        import httpx
        import openai

        if config.provider != "openai_compatible":
            raise ValueError("specguard_tool requires provider: openai_compatible")
        key = os.environ.get(config.api_key_env, "")
        if not key:
            raise ValueError(f"Missing API key in {config.api_key_env}")
        base = os.environ.get(config.api_base_env, "") if config.api_base_env else ""
        self._client = openai.OpenAI(
            api_key=key, base_url=base or None,
            max_retries=0,
            http_client=httpx.Client(verify=config.ssl_verify),
        )
        self._model = config.model
        self._max_tokens = config.max_tokens
        self._effort = config.effort
        self._logger = call_logger
        self._agent = agent
        self._key = key
        self._prices = prices
        self.usage = UsageTracker()

    def session(self, system: str, tools: list[dict], initial: str) -> "ToolSession":
        return ToolSession(self, system, tools, initial)


class ToolSession:
    def __init__(self, client: ToolResponsesClient, system: str,
                 tools: list[dict], initial: str):
        self.client = client
        self.system = system
        self.tools = tools
        self.history: list[dict] = [{"role": "user", "content": initial}]

    def feedback(self, text: str) -> None:
        self.history.append({"role": "user", "content": text})

    def tool_result(self, action: Action, output: str) -> None:
        self.history.append({
            "type": "function_call_output", "call_id": action.call_id,
            "output": output,
        })

    def step(self) -> Action | None:
        c = self.client
        kwargs = {
            "model": c._model, "instructions": self.system,
            "input": self.history, "tools": self.tools,
            "tool_choice": "required", "parallel_tool_calls": False,
            "store": False, "include": ["reasoning.encrypted_content"],
        }
        if c._max_tokens is not None:
            kwargs["max_output_tokens"] = c._max_tokens
        if c._effort is not None:
            kwargs["reasoning"] = {"effort": c._effort}
        handle = c._logger.begin_call(
            agent=c._agent, provider="openai_responses", model=c._model,
            system=self.system, messages=self.history,
            parameters={key: value for key, value in kwargs.items()
                        if key != "input"},
        )
        for attempt in range(6):
            try:
                response = c._client.responses.create(**kwargs)
                break
            except Exception as exc:
                message = str(exc).replace(c._key, "[REDACTED]")
                c._logger.error(handle, provider_attempt=attempt + 1, error={
                    "type": type(exc).__name__,
                    "status_code": getattr(exc, "status_code", None),
                    "message": message[:4000],
                })
                code = getattr(exc, "status_code", None)
                retryable = code in (408, 429, 500, 502, 503, 504) or type(exc).__name__ in (
                    "APIConnectionError", "APITimeoutError", "InternalServerError",
                    "RateLimitError", "APIResponseValidationError",
                )
                if not retryable or attempt == 5:
                    raise RuntimeError(
                        f"{type(exc).__name__}: {message[:4000]}") from None
                time.sleep(min(80, 5 * 2 ** attempt))
        output = [jsonable(item) for item in (response.output or [])]
        usage = _response_usage(response.usage, c._prices)
        c.usage.record({
            "input_tokens": usage["input_tokens"],
            "output_tokens": usage["output_tokens"],
            "cache_read_input_tokens": usage["cached_input_tokens"],
            "cache_creation_input_tokens": usage["cache_write_input_tokens"],
        })
        c._logger.response(handle, provider_attempt=attempt + 1, response={
            "id": getattr(response, "id", None),
            "status": getattr(response, "status", None),
            "output": output, "usage": usage,
        })
        self.history.extend(replayed for item in output
                            if (replayed := _replay_item(item)) is not None)
        if any(part.get("type") == "refusal" for item in output
               for part in (item.get("content") or []) if isinstance(part, dict)):
            raise LLMRefusalError(f"{c._model} refused the {c._agent} request")
        calls = [item for item in output if item.get("type") == "function_call"]
        if len(calls) != 1:
            for call in calls:
                self.history.append({
                    "type": "function_call_output", "call_id": call["call_id"],
                    "output": "Invalid: exactly one action is allowed per turn.",
                })
            return None
        call = calls[0]
        try:
            arguments = json.loads(call.get("arguments") or "{}")
            if not isinstance(arguments, dict):
                raise ValueError("tool arguments must be an object")
        except (ValueError, TypeError) as exc:
            self.history.append({
                "type": "function_call_output", "call_id": call["call_id"],
                "output": f"Invalid tool arguments: {exc}",
            })
            return None
        return Action(call["name"], arguments, call["call_id"])


def _response_usage(raw, prices: dict[str, float]) -> dict:
    if raw is None:
        return {"available": False, "estimated_cost_usd": None}
    details = getattr(raw, "input_tokens_details", None)
    output_details = getattr(raw, "output_tokens_details", None)
    input_tokens = int(getattr(raw, "input_tokens", 0) or 0)
    output_tokens = int(getattr(raw, "output_tokens", 0) or 0)
    cached = int(getattr(details, "cached_tokens", 0) or 0)
    cache_write = int(getattr(details, "cache_write_tokens", 0) or 0)
    ordinary = max(0, input_tokens - cached - cache_write)
    long_context = input_tokens > 272_000
    multiplier = 2 if long_context else 1
    output_multiplier = 1.5 if long_context else 1
    estimate = (
        ordinary * prices["input"] * multiplier
        + cached * prices["cached_input"] * multiplier
        + cache_write * prices["cache_write_input"] * multiplier
        + output_tokens * prices["output"] * output_multiplier
    ) / 1_000_000
    return {
        "available": True, "input_tokens": input_tokens,
        "cached_input_tokens": cached,
        "cache_write_input_tokens": cache_write,
        "output_tokens": output_tokens,
        "reasoning_tokens": int(getattr(output_details, "reasoning_tokens", 0) or 0),
        "total_tokens": int(getattr(raw, "total_tokens", input_tokens + output_tokens) or 0),
        "estimated_cost_usd": estimate,
        "cost_basis": "configured USD per million tokens; not a provider bill",
    }


class ToolSpecAgent(SweCodebaseSpecAgent):
    system_prompt = SPEC_SYSTEM

    @staticmethod
    def _turn_tag(turn: int, total: int) -> str:
        remaining = total - turn - 1
        if remaining == 0:
            return "\n\nFINAL TURN: call submit_spec with a valid Lean module now."
        if remaining <= 3:
            return f"\n\nTurn {turn + 1}/{total}; {remaining} remain. Finish and submit soon."
        return f"\n\nTurn {turn + 1}/{total}; {remaining} remain."

    def run(self, instance_id: str, issue: str, repo: str,
            inputs: tuple[str, ...] | list[str], context: str,
            *, label: str = "swe_spec") -> SpecArtifact:
        total = self._max_turns
        initial = self._initial_prompt(issue, repo or instance_id, total, inputs, context)
        session = self._llm.session(self.system_prompt, SPEC_TOOLS,
                                    initial + self._turn_tag(0, total))
        last_body = last_output = last_error = ""
        submissions = 0
        with RepoContainer(instance_id, exec_timeout=self._exec_timeout) as box:
            for turn in range(total):
                action = session.step()
                if action is None:
                    session.feedback("Invalid response: call exactly one available tool."
                                     + self._turn_tag(turn + 1, total))
                    continue
                if action.name == "submit_spec":
                    submissions += 1
                    try:
                        body = parse_spec_response(
                            f"SUBMIT_SPEC\n```lean4\n{action.arguments['lean']}\n```")
                        last_body = body
                        ok, last_output = validate_spec_contract(
                            self._runner, body, name=f"{label}_{submissions}")
                        if ok:
                            return SpecArtifact(
                                assemble_lean(body), True, submissions,
                                session.history, last_output)
                        last_error = f"spec contract did not compile:\n{last_output[-_COMPILE_OUT_CAP:]}"
                    except (ArtifactError, KeyError, TypeError) as exc:
                        last_error = str(exc)
                    if submissions >= self._max_submissions:
                        last_error = (f"SpecAgent exhausted its {self._max_submissions} "
                                      f"Lean submissions. Last rejection: {last_error}")
                        break
                    session.tool_result(action, last_error + self._turn_tag(turn + 1, total))
                    continue
                if action.name == "bash" and isinstance(action.arguments.get("command"), str):
                    code, output = box.bash(action.arguments["command"])
                    if len(output) > _BASH_OUT_CAP:
                        output = output[:_BASH_OUT_CAP] + "\n... output truncated ..."
                    session.tool_result(
                        action, f"Command exited {code}:\n```text\n{output}\n```"
                        + self._turn_tag(turn + 1, total))
                else:
                    session.tool_result(action, "Invalid action; use bash or submit_spec."
                                        + self._turn_tag(turn + 1, total))
        return SpecArtifact(
            assemble_lean(last_body) if last_body else "", False, submissions,
            session.history, last_output,
            last_error or "SpecAgent exhausted its turns without a valid submission")


class ToolTestAgent(SweTestAgent):
    def __init__(self, llm, runner, max_submissions: int = 10, *, original: bool = False):
        super().__init__(llm, runner, max_submissions, original=original)
        self.system_prompt = ORIGINAL_TEST_SYSTEM if original else TEST_SYSTEM

    def run(self, inputs: tuple[str, ...] | list[str], context: str,
            test_0_patch: str, test_1_patch: str,
            *, label: str = "swe_tests") -> TestArtifact:
        initial = (self._original_prompt(inputs, context, test_0_patch) if self._original
                   else self._prompt(inputs, context, test_0_patch, test_1_patch))
        session = self._llm.session(self.system_prompt, TEST_TOOLS, initial)
        last_model = last_cases = last_output = last_error = ""
        for attempt in range(self._max_submissions):
            action = session.step()
            if action is None:
                session.feedback("Invalid response: call submit_tests with both Lean modules.")
                continue
            if action.name != "submit_tests":
                session.tool_result(action, "Invalid action: call submit_tests.")
                continue
            try:
                response = ("SUBMIT_TESTS\nTEST_MODEL\n```lean4\n"
                            f"{action.arguments['test_model']}\n```\n"
                            "TEST_CASES\n```lean4\n"
                            f"{action.arguments['test_cases']}\n```")
                model, cases = parse_tests_response(response)
                last_model, last_cases = model, cases
                validator = (validate_original_tests_contract if self._original
                             else validate_tests_contract)
                ok, last_output = validator(
                    self._runner, model, cases, name=f"{label}_{attempt}")
                if ok:
                    return TestArtifact(
                        assemble_lean(model), assemble_lean(cases), True,
                        attempt + 1, session.history, last_output)
                last_error = f"test artifact did not satisfy its contract:\n{last_output[-2500:]}"
            except (ArtifactError, KeyError, TypeError) as exc:
                last_error = str(exc)
            if attempt < self._max_submissions - 1:
                session.tool_result(action, last_error)
        return TestArtifact(
            assemble_lean(last_model) if last_model else "",
            assemble_lean(last_cases) if last_cases else "", False,
            self._max_submissions, session.history, last_output,
            last_error or "TestAgent produced no valid submission")


class ToolConnectorAgent(SweConnectorAgent):
    def __init__(self, llm, runner, max_submissions: int = 6, *, original: bool = False):
        super().__init__(llm, runner, max_submissions, original=original)
        self.system_prompt = ORIGINAL_CONNECTOR_SYSTEM if original else CONNECTOR_SYSTEM

    @staticmethod
    def _prompt(spec: str, test_model: str, test_cases: str,
                inputs: tuple[str, ...] | list[str], context: str) -> str:
        source = SweConnectorAgent._prompt(spec, test_model, test_cases, inputs, context)
        return _replace_once(
            source, "Connect the demands to Spec.run. Return SUBMIT_CONNECTOR and one Lean block.",
            "Connect the demands to Spec.run. Call submit_connector with bare Lean source.")

    def run(self, spec: str, test_model: str, test_cases: str,
            inputs: tuple[str, ...] | list[str], context: str,
            *, label: str) -> ConnectorArtifact:
        ok, direct_output = validate_connector_contract(
            self._runner, spec, test_model, test_cases, DIRECT_CONNECTOR,
            name=f"{label}_direct")
        if ok:
            return ConnectorArtifact(
                assemble_lean(DIRECT_CONNECTOR), True, 0, "direct",
                compile_output=direct_output)
        session = self._llm.session(
            self.system_prompt, CONNECTOR_TOOLS,
            self._prompt(spec, test_model, test_cases, inputs, context))
        last_output, last_error, last_body = direct_output, "", ""
        for attempt in range(self._max_submissions):
            action = session.step()
            if action is None:
                session.feedback("Invalid response: call submit_connector with Lean code.")
                continue
            if action.name != "submit_connector":
                session.tool_result(action, "Invalid action: call submit_connector.")
                continue
            try:
                body = parse_connector_response(
                    f"SUBMIT_CONNECTOR\n```lean4\n{action.arguments['lean']}\n```")
                last_body = body
                ok, last_output = validate_connector_contract(
                    self._runner, spec, test_model, test_cases, body,
                    name=f"{label}_{attempt}")
                if ok:
                    return ConnectorArtifact(
                        assemble_lean(body), True, attempt + 1, "llm",
                        session.history, last_output)
                last_error = f"connector contract did not compile:\n{last_output[-2500:]}"
            except (ArtifactError, KeyError, TypeError) as exc:
                last_error = str(exc)
            if attempt < self._max_submissions - 1:
                session.tool_result(action, last_error)
        return ConnectorArtifact(
            assemble_lean(last_body) if last_body else "", False,
            self._max_submissions, "failure", session.history, last_output,
            last_error or "connector produced no valid submission")


DEFAULT_PRICES = {
    "input": 10.0, "cached_input": 1.0,
    "cache_write_input": 12.5, "output": 50.0,
}


def collate_usage(out_root: Path) -> dict:
    """Rebuild usage from durable task logs, including tasks from earlier resumes."""
    stages = {}
    tasks: dict[str, dict] = {}
    total = {"calls": 0, "input_tokens": 0, "cached_input_tokens": 0,
             "cache_write_input_tokens": 0, "output_tokens": 0,
             "reasoning_tokens": 0, "estimated_cost_usd": 0.0,
             "calls_missing_usage": 0}
    for stage, filename in (("spec", "spec_log.json"),
                            ("tests", "test_log.json"),
                            ("connector", "connector_log.json")):
        counts = dict.fromkeys(total, 0)
        for path in out_root.glob(f"*/{filename}"):
            payload = json.loads(path.read_text(encoding="utf-8"))
            task = tasks.setdefault(path.parent.name, dict.fromkeys(total, 0))
            for call in payload.get("api_calls", []):
                counts["calls"] += 1
                task["calls"] += 1
                usage = (call.get("response") or {}).get("usage") or {}
                if not usage.get("available"):
                    counts["calls_missing_usage"] += 1
                    task["calls_missing_usage"] += 1
                    continue
                for key in counts.keys() - {"calls", "calls_missing_usage"}:
                    value = usage.get(key, 0) or 0
                    counts[key] += value
                    task[key] += value
        stages[stage] = counts
        for key, value in counts.items():
            total[key] += value
    for task_id, counts in tasks.items():
        (out_root / task_id / "usage.json").write_text(
            json.dumps(counts, indent=2), encoding="utf-8")
    return {"by_agent": stages, "total": total,
            "cost_note": "USD values are estimates from configured token prices, not billed charges."}


def main(argv=None) -> int:
    from conflict_certifier.tracks.swebench.run import main as run_main
    return run_main(argv, tool_mode=True)


if __name__ == "__main__":
    raise SystemExit(main())
