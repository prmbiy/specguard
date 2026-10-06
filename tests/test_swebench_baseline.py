from __future__ import annotations

import argparse
import json
import subprocess

import pytest

import conflict_certifier.tracks.swebench.baseline as baseline
from conflict_certifier.evaluation import (
    CONFLICT,
    FAIL,
    INCORRECT,
    NO_CONFLICT,
    SUCCESS,
)
from conflict_certifier.llm.call_log import LLMCallLogger


def bash(command, note="Looking for the relevant assertion."):
    return {"role": "assistant", "content": note, "tool_calls": [
        {"id": "call_1", "type": "function",
         "function": {"name": "bash", "arguments": json.dumps({"command": command})}}]}


def verdict(label, reasons="The evidence supports this verdict."):
    return {"role": "assistant", "content": "", "tool_calls": [
        {"id": "call_1", "type": "function",
         "function": {"name": "verdict", "arguments": json.dumps({"label": label, "reasons": reasons})}}]}


class SequenceLLM:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.messages = []

    def complete_tools(self, system, messages, tools):
        self.messages.append((system, list(messages)))
        reply = next(self.responses)
        return reply if isinstance(reply, dict) else {"role": "assistant", "content": reply, "tool_calls": []}


class FakeBox:
    def __init__(self):
        self.commands = []

    def bash(self, command):
        self.commands.append(command)
        return 0, "found relevant test"


class FakeContainer(FakeBox):
    created = []

    def __init__(self, instance_id, test_patch, *, exec_timeout):
        super().__init__()
        self.instance_id = instance_id
        self.test_patch = test_patch
        self.exec_timeout = exec_timeout
        self.closed = False
        self.__class__.created.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.closed = True


def test_baseline_agent_explores_then_returns_exact_label():
    llm = SequenceLLM([
        bash("rg -n expected tests"),
        verdict(baseline.NOT_CONFLICTING, "The expectations agree with the issue."),
    ])
    agent = baseline.SwebenchBaselineAgent(llm, max_turns=3)
    box = FakeBox()

    answer = agent.run("Correct frobnication behavior.", box)

    assert answer.label == baseline.NOT_CONFLICTING
    assert answer.turns == 2
    assert answer.executions == 1
    assert answer.explanation == "The expectations agree with the issue."
    assert box.commands == ["rg -n expected tests"]
    fed_back = llm.messages[1][1][-1]
    assert fed_back["role"] == "tool" and fed_back["tool_call_id"] == "call_1"
    assert "Command exited 0" in fed_back["content"]
    assert [t["function"]["name"] for t in agent.tools] == ["bash", "verdict"]


def test_plain_text_or_unknown_label_is_not_accepted_as_a_verdict():
    llm = SequenceLLM([
        "I think CONFLICTING",                      # no tool call at all
        verdict("MAYBE_CONFLICTING"),               # label outside the allowed set
        verdict(baseline.CONFLICTING, "The test contradicts the documented behavior."),
    ])
    agent = baseline.SwebenchBaselineAgent(llm, max_turns=3)

    answer = agent.run("Issue", FakeBox())

    assert answer.label == baseline.CONFLICTING
    assert answer.turns == 3
    assert "Invalid response" in llm.messages[1][1][-1]["content"]
    assert llm.messages[2][1][-1]["role"] == "tool"
    assert "Invalid response" in llm.messages[2][1][-1]["content"]


def test_turn_exhaustion_is_inconclusive():
    llm = SequenceLLM(["not a valid action", "still invalid"])
    agent = baseline.SwebenchBaselineAgent(llm, max_turns=2)

    answer = agent.run("Issue", FakeBox())

    assert answer.label is None
    assert answer.turns == 2
    assert "exhausted 2 turns" in answer.error


def test_empty_reasons_and_inconclusive_without_opt_in_are_rejected():
    llm = SequenceLLM([
        verdict(baseline.CONFLICTING, reasons=""),
        verdict("INCONCLUSIVE", "Cannot tell."),
    ])
    agent = baseline.SwebenchBaselineAgent(llm, max_turns=2)

    answer = agent.run("Issue", FakeBox())

    assert answer.label is None
    assert all(m[1][-1]["role"] == "tool" and "Invalid response" in m[1][-1]["content"]
               for m in llm.messages[1:])


def test_multiple_tool_calls_in_one_turn_are_all_answered_and_rejected():
    two = bash("ls")
    two["tool_calls"] = two["tool_calls"] + [{"id": "call_2", "type": "function",
        "function": {"name": "bash", "arguments": json.dumps({"command": "pwd"})}}]
    llm = SequenceLLM([two, verdict(baseline.CONFLICTING)])
    agent = baseline.SwebenchBaselineAgent(llm, max_turns=2)
    box = FakeBox()

    answer = agent.run("Issue", box)

    assert box.commands == []
    assert answer.label == baseline.CONFLICTING
    tail = llm.messages[1][1][-2:]
    assert [m["tool_call_id"] for m in tail] == ["call_1", "call_2"]
    assert all("Invalid response" in m["content"] for m in tail)


def test_label_without_any_executed_command_is_not_scored(tmp_path):
    llm = SequenceLLM([verdict(baseline.CONFLICTING, "I remember this repository.")])
    agent = baseline.SwebenchBaselineAgent(llm, max_turns=1)
    logger = LLMCallLogger(tmp_path)
    logger.start_task("task")

    result = baseline.process_one(
        dict(instance_id="task", problem_statement="Issue", test_patch="patch"),
        "oneoff", agent, tmp_path, logger, container_factory=FakeContainer)

    assert result["outcome"] == "INCONCLUSIVE"
    assert result["note"] == "no_evidence"
    assert result["baseline_label"] == baseline.CONFLICTING
    assert result["baseline_executions"] == 0


@pytest.mark.parametrize(
    ("split", "label", "status", "outcome"),
    [
        ("original", baseline.NOT_CONFLICTING, SUCCESS, NO_CONFLICT),
        ("oneoff", baseline.CONFLICTING, SUCCESS, CONFLICT),
        ("original", baseline.CONFLICTING, FAIL, INCORRECT),
        ("oneoff", baseline.NOT_CONFLICTING, FAIL, INCORRECT),
    ],
)
def test_task_labels_use_shared_evaluator(split, label, status, outcome):
    decision = baseline.decision_for_label(label, split)
    assert decision.status == status
    assert decision.outcome == outcome


def test_original_and_oneoff_have_identical_model_prompt():
    agent = baseline.SwebenchBaselineAgent(SequenceLLM([]), max_turns=120)
    issue = "A shared issue description"
    original = agent.initial_prompt(issue)
    oneoff = agent.initial_prompt(issue)

    assert original == oneoff
    combined = (baseline.SYSTEM_PROMPT + original).lower()
    assert "oneoff" not in combined
    assert "original" not in combined
    assert "test_patch" not in combined
    assert "gold patch" not in combined


def test_container_applies_patch_before_removing_git(monkeypatch):
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs.get("input")))
        output = "container-id\n" if command[:2] == ["docker", "create"] else ""
        return subprocess.CompletedProcess(command, 0, output)

    monkeypatch.setattr(baseline, "require_image", lambda _: "local/task:image")
    monkeypatch.setattr(baseline.subprocess, "run", fake_run)
    baseline.BaselineContainer._active.clear()

    box = baseline.BaselineContainer("repo__repo-1", "PATCH BODY", exec_timeout=30)
    box.start()
    box.close()

    commands = [call[0] for call in calls]
    create = commands[0]
    assert "--network=none" in create
    assert "--cap-drop=ALL" in create
    assert "no-new-privileges:true" in create
    assert not any(item.startswith("--volume") or item == "-v" for item in create)

    apply_indices = [i for i, command in enumerate(commands) if "apply" in command]
    seal_index = next(
        i for i, command in enumerate(commands)
        if command[-2:] == ["-rf", "/testbed/.git"]
    )
    assert len(apply_indices) == 2
    assert max(apply_indices) < seal_index
    assert all(calls[i][1] == "PATCH BODY" for i in apply_indices)
    assert commands[-1][:3] == ["docker", "rm", "-f"]
    assert not baseline.BaselineContainer._active


def test_process_uses_only_issue_and_installed_patch_and_writes_task_log(tmp_path):
    FakeContainer.created.clear()
    call_log = LLMCallLogger(tmp_path)
    call_log.start_task("repo__repo-1")
    agent = baseline.SwebenchBaselineAgent(
        SequenceLLM([
            bash("sed -n '1,80p' tests/test_frob.py"),
            verdict(baseline.CONFLICTING, "The installed expectation contradicts the issue."),
        ]),
        max_turns=2,
        exec_timeout=17,
    )
    row = {
        "instance_id": "repo__repo-1",
        "problem_statement": "PUBLIC ISSUE",
        "test_patch": "INSTALLED TEST PATCH",
        "patch": "SECRET GOLD PATCH",
        "impossible_type": "oneoff",
    }

    result = baseline.process_one(
        row, "oneoff", agent, tmp_path, call_log,
        container_factory=FakeContainer,
    )

    assert result["status"] == SUCCESS
    assert result["outcome"] == CONFLICT
    assert len(FakeContainer.created) == 1
    container = FakeContainer.created[0]
    assert container.test_patch == "INSTALLED TEST PATCH"
    assert container.exec_timeout == 17
    assert container.closed

    log = json.loads((tmp_path / "repo__repo-1" / "baseline_log.json").read_text())
    assert log["in_progress"] is False
    assert log["label"] == baseline.CONFLICTING
    assert result["baseline_executions"] == 1
    rendered = json.dumps(log)
    assert "PUBLIC ISSUE" in rendered
    assert "SECRET GOLD PATCH" not in rendered
    assert "INSTALLED TEST PATCH" not in rendered
    assert "oneoff" not in rendered


def test_usage_is_rebuilt_from_all_task_results():
    results = [
        {"usage": {"calls": 2, "input_tokens": 10}},
        {"usage": {"calls": 3, "input_tokens": 7, "output_tokens": 4}},
        {},
    ]
    assert baseline._usage_total(results) == {
        "calls": 5,
        "input_tokens": 17,
        "output_tokens": 4,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
    }


def test_resume_rejects_a_different_model(tmp_path, monkeypatch):
    dataset = tmp_path / "dataset.jsonl"
    dataset.write_text("{}\n")
    monkeypatch.setattr(baseline, "_dataset_path", lambda _: dataset)
    (tmp_path / "config.yaml").write_text(
        "track: swebench_baseline\n"
        "split: original\n"
        "provider: openai_compatible\n"
        "model: first-model\n"
        f"dataset_sha256: {baseline._file_sha256(dataset)}\n"
        f"system_prompt_sha256: {baseline.sha256_text(baseline.SYSTEM_PROMPT)}\n"
    )
    args = argparse.Namespace(
        split="original", provider="openai_compatible", model="second-model")

    with pytest.raises(ValueError, match="model"):
        baseline._validate_resume(tmp_path, args)


@pytest.mark.parametrize('enabled', [False, True])
def test_abstention_is_opt_in_and_reason_is_saved(tmp_path, enabled):
    llm = SequenceLLM([bash("python -c 'import Y'"),
                       verdict('INCONCLUSIVE', 'Cannot determine X: dependency Y is unavailable after attempting import.')])
    agent = baseline.SwebenchBaselineAgent(llm, max_turns=2, allow_inconclusive=enabled)
    logger = LLMCallLogger(tmp_path)
    logger.start_task('task')
    result = baseline.process_one(dict(instance_id='task', problem_statement='Issue',test_patch='patch'),
        'original', agent,tmp_path,logger,container_factory=FakeContainer)
    assert result['outcome'] == 'INCONCLUSIVE'
    assert result.get('baseline_label') == ('INCONCLUSIVE' if enabled else None)
    if enabled:
        assert 'dependency Y' in result['reason']
    log=json.loads((tmp_path/'task/baseline_log.json').read_text())
    assert log['system_prompt'] == agent.system_prompt
    if not enabled:
        assert agent.system_prompt == baseline.SYSTEM_PROMPT


def test_votes_majority_counts_abstentions_and_keeps_score():
    from conflict_certifier.tracks.swebench.baseline_votes import aggregate, policy_metrics
    votes=[dict(baseline_label=x) for x in ['CONFLICTING','NOT_CONFLICTING','INCONCLUSIVE','CONFLICTING','CONFLICTING']]
    assert aggregate('t',votes[:1],'oneoff')['outcome']=='CONFLICT'
    three=aggregate('t',votes[:3],'oneoff')
    assert three['outcome']=='INCONCLUSIVE'
    assert three['conflict_score']==1/3
    five=aggregate('t',votes,'oneoff')
    assert five['outcome']=='CONFLICT'
    assert five['vote_counts']['inconclusive']==1
    assert five['conflict_score']==3/5
    # A label reached without executing any command is audit metadata, not a vote.
    votes[3]['note']='no_evidence'
    guarded=aggregate('t',votes,'oneoff')
    assert guarded['outcome']=='INCONCLUSIVE'
    assert guarded['vote_counts']=={'conflicting':2,'not_conflicting':1,'inconclusive':2,'skipped':0}
    metrics=policy_metrics([three], 'oneoff')
    assert metrics['percent']['coverage']==0
    assert metrics['percent']['false_negative']==0
    assert metrics['percent']['fail_open_miss']==100
    clean=policy_metrics([three], 'original')
    assert clean['percent']['false_positive']==0
    assert clean['percent']['fail_closed_false_block']==100


def test_policy_metrics_includes_infrastructure_failure():
    from conflict_certifier.tracks.swebench.baseline_votes import policy_metrics
    m=policy_metrics([dict(status='SKIPPED',predictions=None)],'original')
    assert m['counts']['skipped']==1
    assert m['policies']['fail_closed']['percent']['false_positive']==100
    assert m['policies']['fail_open']['percent']['false_positive']==0


def test_multivote_runner_independent_logs_resume_and_usage(tmp_path, monkeypatch):
    import threading
    class Usage:
        def __init__(self): self.local=threading.local()
        def start_local(self): self.local.started=True
        def take_local(self): return {'calls':1,'input_tokens':10,'output_tokens':2}
    class LLM(SequenceLLM):
        def __init__(self):
            super().__init__([bash("ls tests"), verdict("CONFLICTING", "The assertion contradicts the issue.")]*5)
            self.usage=Usage()
    llm=LLM()
    dataset=tmp_path/'data.jsonl';dataset.write_text('{}')
    monkeypatch.setattr(baseline, '_dataset_path', lambda _:dataset)
    monkeypatch.setattr(baseline, '_load_dotenv', lambda:None)
    monkeypatch.setattr(baseline, 'OUTPUT_DIR', tmp_path/'runs')
    monkeypatch.setattr(baseline, 'load_rows', lambda _: {'task':dict(instance_id='task',test_patch='PATCH',problem_statement='ISSUE')})
    monkeypatch.setattr(baseline.LLMConfig,'build', lambda *a,**kw:llm)
    original_process=baseline.process_one
    def process(*args, **kwargs):
        return original_process(*args,**kwargs,container_factory=FakeContainer)
    monkeypatch.setattr(baseline, 'process_one',process)
    FakeContainer.created.clear()
    argv=['--all','--model','fake','--split','oneoff','--votes','5','--allow-inconclusive','--workers','1']
    assert baseline.main(argv)==0
    run=next((tmp_path/'runs/fake').iterdir())
    assert run.name.endswith('_oneoff_k5_incl')
    assert len(FakeContainer.created)==5
    assert all(box.closed for box in FakeContainer.created)
    assert all(len(messages) in (1, 3) for _,messages in llm.messages)
    assert len(list(run.glob('task/votes/*/baseline_log.json')))==5
    usage=json.loads((run/'usage.json').read_text())
    assert usage['calls']==5 and usage['input_tokens']==50   # take_local() is stubbed per vote
    summary=json.loads((run/'vote_summary.json').read_text())
    assert set(summary['by_k'])=={'1','3','5'}
    # Simulate interruption after vote five was saved but parent result was not.
    (run/'task/result.json').unlink()
    assert baseline.main(argv+['--resume',str(run)])==0
    assert len(llm.messages)==10
    assert json.loads((run/'usage.json').read_text())==usage
    # Completed prefix votes are retained when resuming a partial batch.
    (run/'task/result.json').unlink()
    (run/'task/votes/5/result.json').unlink()
    llm.responses=iter([bash("ls"), verdict("NOT_CONFLICTING", "Evidence is compatible.")])
    assert baseline.main(argv+['--resume',str(run)])==0
    assert len(llm.messages)==12
    assert json.loads((run/'usage.json').read_text())==usage
    with pytest.raises(ValueError,match='allow_inconclusive|system_prompt'):
        baseline.main(argv+['--resume',str(run),'--no-allow-inconclusive'])


@pytest.mark.parametrize('split', ['original', 'oneoff'])
@pytest.mark.parametrize('votes', [1, 3, 5])
@pytest.mark.parametrize('enabled', [False, True])
def test_run_folder_naming(split, votes, enabled):
    expected = f'20260917T120000Z_{split}_k{votes}' + ('_incl' if enabled else '')
    assert baseline._run_folder_name('20260917T120000Z', split, votes, enabled) == expected


def test_prompt_modes_use_one_complete_file():
    binary = baseline.load_prompt('baseline_system.txt')
    abstaining = baseline.load_prompt('baseline_abstention.txt')
    assert baseline.system_prompt(False) == binary
    assert baseline.system_prompt(True) == abstaining
    assert abstaining.count('You are classifying') == 1
    assert 'bash access' in abstaining
    assert 'buggy commit' in abstaining
    assert 'call exactly one tool' in binary and 'call exactly one tool' in abstaining
    assert '<thoughts>' not in binary and '<thoughts>' not in abstaining
    assert 'INCONCLUSIVE' not in binary
    assert 'INCONCLUSIVE' in abstaining
