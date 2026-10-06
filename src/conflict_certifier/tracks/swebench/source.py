"""SWE-bench dataset and sealed-codebase adapter.

The Lean track builds a public :class:`AgentCase` from the issue, frozen
answer-free inputs, and neutrally numbered test variants while retaining the
good/bad ordering in a separate :class:`PrivateTestOrder`. Legacy mutation
extraction helpers remain here for dataset inspection and the Python track.

Unlike Verina/LiveCodeBench, SWE-bench tasks are repository-level: the "function
under test" operates on rich framework objects we cannot formalize wholesale.
We therefore do NOT load these as fully-populated ``Task`` objects for the
automated pipeline. Instead this module extracts the raw material a human (or,
later, a slicing agent) needs to write a *slice spec*:

    1. problem_statement   — the natural-language intent
    2. gold patch          — the reference fix (confirms correct behavior)
    3. the mutation        — (test_input, original_expected, corrupted_expected)
                             recovered by diffing test_patch vs original_test_patch

The mutation parser is deliberately conservative: SWE-bench test patches are
arbitrary Python, so we extract the changed assertion lines verbatim and, when
the shape is a simple ``assert <lhs> == <rhs>`` / ``assertEqual``/``assertIs``,
split out lhs/rhs. Anything we cannot parse is still surfaced as raw diff lines
so a human can read it.

No Docker, no repo clone: certification is by Lean proof, not execution.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import re
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path

from conflict_certifier.config import DATA_DIR
from conflict_certifier.tracks.swebench.artifacts import (
    AgentCase,
    PrivateOriginalTest,
    PrivateTestOrder,
)

HF_DATASET = "fjzzq2002/impossible_swebench"
SWEBENCH_DIR = DATA_DIR / "swebench"
MANUAL_DIR = SWEBENCH_DIR / "manual"       # hand-built certified tasks (<id>/instance.json ...)
# Frozen, human-reviewed test-inputs file (built once by
# scripts/llm_test_input_filter.py; see data/swebench/test_json/).
TEST_INPUTS_FILE = SWEBENCH_DIR / "test_json" / "test_inputs.jsonl"


class InputUnavailableError(ValueError):
    """The frozen answer-free input projection cannot support this instance."""


def prepare_agent_case(row: dict, input_record: dict | None,
                       *, order_seed: int = 0, split: str = "conflicting") -> tuple[
                           AgentCase, PrivateTestOrder | PrivateOriginalTest]:
    """Create the public agent packet and private suite-level truth.

    Conflicting data retains its established shuffled two-patch behavior. Original
    and oneoff expose one patch, with private truth ``[true]`` and ``[false]``
    respectively.
    """
    if input_record is None or input_record.get("status") != "OK":
        status = "missing" if input_record is None else input_record.get("status", "invalid")
        raise InputUnavailableError(f"frozen input record is {status}")
    iid = row["instance_id"]
    if split in ("original", "oneoff"):
        patch = ((row.get("original_test_patch", "") or row.get("test_patch", ""))
                 if split == "original" else row.get("test_patch", ""))
        if not patch.strip():
            raise InputUnavailableError(f"{split} test patch is empty")
        case = AgentCase(
            instance_id=iid,
            description=row["problem_statement"],
            repo=row.get("repo", ""),
            inputs=tuple(str(value) for value in input_record.get("inputs", [])),
            input_context=str(input_record.get("context", "")),
            test_0_patch=patch,
            test_1_patch="",
        )
        return case, PrivateOriginalTest(expected_pass=split == "original")

    digest = hashlib.sha256(f"{order_seed}:{iid}".encode("utf-8")).digest()
    good_number = digest[0] & 1
    bad_number = 1 - good_number
    patches = ["", ""]
    patches[good_number] = row.get("original_test_patch", "")
    patches[bad_number] = row.get("test_patch", "")
    case = AgentCase(
        instance_id=iid,
        description=row["problem_statement"],
        repo=row.get("repo", ""),
        inputs=tuple(str(value) for value in input_record.get("inputs", [])),
        input_context=str(input_record.get("context", "")),
        test_0_patch=patches[0],
        test_1_patch=patches[1],
    )
    return case, PrivateTestOrder(good_number, bad_number)


def load_test_inputs() -> dict[str, dict]:
    """Load the frozen test-inputs file, keyed by instance_id.

    This is the ONLY source of test-derived material a SpecAgent may see (both
    tracks). The live redaction path it replaced (spec_agent_test_inputs,
    since deleted) leaked expected values three ways — audit Finding 5 — and
    must not come back. A missing file is a hard error: silently running
    inputs-off would change the experiment without saying so.
    """
    if not TEST_INPUTS_FILE.exists():
        raise FileNotFoundError(
            f"test inputs are enabled but the test-inputs file is missing: "
            f"{TEST_INPUTS_FILE}. Build it with scripts/llm_test_input_filter.py "
            f"or run with --no-test-inputs.")
    recs: dict[str, dict] = {}
    for line in TEST_INPUTS_FILE.read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            recs[r["instance_id"]] = r
    return recs

# --- Docker (buggy-repo access) ---------------------------------------------
# Each SWE-bench instance has an eval image with the repo checked out at the
# buggy base commit under /testbed. Image name maps "__" -> "_1776_".
# e.g. astropy__astropy-14309 -> swebench/sweb.eval.x86_64.astropy_1776_astropy-14309
#
# IMPORTANT: the pipeline NEVER pulls images. Images are large (~1-2 GB each) and
# disk is finite, so downloading is a deliberate, separate step (see pull_images.py
# / `python -m conflict_certifier.tracks.swebench.pull_images`). At run time we only
# ever *check* for a local image and raise MissingImageError if it is absent.


class MissingImageError(RuntimeError):
    """Raised when an instance's eval image is not present locally.

    The pipeline does not download images; carry the exact pull command so the
    caller can surface it and the user can fetch the image on their own terms.
    """

    def __init__(self, instance_id: str, image: str, reason: str | None = None):
        self.instance_id = instance_id
        self.image = image
        head = (f"{reason}; " if reason else "") + \
            f"eval image not present locally for {instance_id}: {image}"
        super().__init__(
            head + "\n"
            f"  pull it manually (large, ~1-2 GB):  docker pull {image}\n"
            f"  or:  python -m conflict_certifier.tracks.swebench.pull_images "
            f"--ids {instance_id}"
        )


def docker_image_for(instance_id: str) -> str:
    """Return the eval Docker image name for an instance id."""
    return f"swebench/sweb.eval.x86_64.{instance_id.replace('__', '_1776_')}:latest"


def docker_available() -> bool:
    """True if a `docker` CLI is on PATH and responds."""
    try:
        return subprocess.run(
            ["docker", "version"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        ).returncode == 0
    except FileNotFoundError:
        return False


def _image_present(image: str) -> bool:
    try:
        return subprocess.run(
            ["docker", "image", "inspect", image],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        ).returncode == 0
    except FileNotFoundError:
        # no docker CLI at all -> the image certainly isn't available locally
        return False


def require_image(instance_id: str) -> str:
    """Return the local eval image name, or raise MissingImageError. Never pulls."""
    image = docker_image_for(instance_id)
    if not docker_available():
        raise MissingImageError(
            instance_id, image,
            reason="docker CLI not found on PATH (install/start Docker to use --codebase)",
        )
    if not _image_present(image):
        raise MissingImageError(instance_id, image)
    return image


def pull_image(instance_id: str) -> str:
    """Explicitly pull one eval image from Docker Hub. Used ONLY by the separate
    downloader CLI (pull_images.py) — never by the certification pipeline."""
    image = docker_image_for(instance_id)
    if _image_present(image):
        return image
    print(f"[swebench] pulling {image} ...", flush=True)
    r = subprocess.run(["docker", "pull", image], stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"docker pull failed for {image}:\n{r.stdout[-800:]}")
    return image


class RepoContainer:
    """A live eval container the SpecAgent drives with a real bash shell.

    The instance's repo is checked out at the BUGGY base commit under /testbed.
    We start the image detached (`sleep infinity`), run agent-issued commands via
    `docker exec`, and force-remove it on close. Never pulls (require_image).

    Integrity: the repo here is the buggy tree — the gold fix is NOT applied. The
    agent has full shell access (as requested); it localizes the code itself from
    the issue, exactly as in real SWE-bench.

    SEALED: the container runs with --network=none and its .git directory removed.
    Transcript audits showed spec agents fetching the real fix from GitHub, pip-
    installing fixed library versions, and finding fix commits via git history —
    all of which leak the answer the agent is supposed to derive independently.
    No network + no git history closes every one of those channels.
    """

    def __init__(self, instance_id: str, workdir: str = "/testbed",
                 exec_timeout: int = 120):
        self.instance_id = instance_id
        self.image = require_image(instance_id)  # raises MissingImageError; never pulls
        self.workdir = workdir
        self.exec_timeout = exec_timeout
        self._cid: str | None = None

    def start(self) -> "RepoContainer":
        r = subprocess.run(
            ["docker", "run", "-d", "--network=none", "-w", self.workdir, self.image,
             "sleep", "infinity"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=120,
        )
        if r.returncode != 0:
            raise RuntimeError(f"failed to start container for {self.instance_id}:\n{r.stdout}")
        self._cid = r.stdout.strip().splitlines()[-1].strip()
        # Strip git metadata: history can contain (or fetch refs to) the actual fix
        # commit and the corrected tests — the answer key the SpecAgent must derive.
        code, out = self.bash(f"rm -rf {self.workdir}/.git")
        if code != 0:
            self.close()
            raise RuntimeError(f"failed to seal container for {self.instance_id}:\n{out}")
        return self

    def bash(self, script: str, timeout: int | None = None) -> tuple[int, str]:
        """Run a shell script in /testbed. Returns (exit_code, combined_output)."""
        if self._cid is None:
            raise RuntimeError("container not started")
        try:
            r = subprocess.run(
                ["docker", "exec", "-w", self.workdir, self._cid, "bash", "-lc", script],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                encoding="utf-8", errors="replace",
                timeout=timeout or self.exec_timeout,
            )
            return r.returncode, r.stdout
        except subprocess.TimeoutExpired:
            return 124, f"<command timed out after {timeout or self.exec_timeout}s>"

    def close(self) -> None:
        if self._cid is not None:
            subprocess.run(["docker", "rm", "-f", self._cid],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self._cid = None

    def __enter__(self) -> "RepoContainer":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.close()


_DIFF_FILE_RE = re.compile(r"^diff --git a/(\S+) b/(\S+)", re.MULTILINE)


def files_from_patch(patch: str, include_tests: bool = False) -> list[str]:
    """Return the source-file paths a patch touches (test files excluded by default)."""
    paths: list[str] = []
    for m in _DIFF_FILE_RE.finditer(patch or ""):
        p = m.group(2)  # b/<path>
        low = p.lower()
        is_test = "test" in low or low.endswith("conftest.py")
        if is_test and not include_tests:
            continue
        if p not in paths:
            paths.append(p)
    return paths


@dataclass
class Mutation:
    """One changed assertion recovered from the test_patch diff.

    ``original_line`` / ``corrupted_line`` are the verbatim assertion lines (as
    they appear after stripping the leading diff +/- marker). lhs/rhs/op are
    best-effort parses; any may be None when the assertion is not a simple
    binary comparison.
    """

    original_line: str | None
    corrupted_line: str
    lhs: str | None = None
    op: str | None = None
    original_rhs: str | None = None
    corrupted_rhs: str | None = None
    kind: str = "unknown"  # "value_change" | "added_conflict" | "unknown"


@dataclass
class SweInstance:
    instance_id: str
    repo: str
    base_commit: str
    version: str
    difficulty: str
    impossible_type: str
    problem_statement: str
    gold_patch: str
    fail_to_pass: list[str]
    first_fail_to_pass: str
    mutations: list[Mutation] = field(default_factory=list)
    raw_added_lines: list[str] = field(default_factory=list)
    raw_removed_lines: list[str] = field(default_factory=list)


@dataclass
class SweTask:
    """A SWE-bench task ready for slice-spec generation.

    Unlike Verina/LCB, we don't formalize the repo-level function wholesale.
    Instead, the slice agent uses these fields to write a Lean spec for the
    behavior the test exercises.
    """

    instance_id: str
    repo: str
    problem_statement: str
    gold_patch: str
    original_expected: str  # from official test_patch (correct value)
    corrupted_expected: str  # from impossible_swebench mutation
    mutated_call: str  # the lhs of the assertion (e.g. "is_separable(cm7)")
    metadata: dict = field(default_factory=dict)  # repo, version, difficulty, etc.


# --- assertion parsing -------------------------------------------------------

# assert <lhs> == <rhs>     /     assert <lhs> != <rhs>
_ASSERT_CMP = re.compile(r"^\s*assert\s+(?P<lhs>.+?)\s*(?P<op>==|!=|is not|is)\s+(?P<rhs>.+?)\s*$")
# self.assertEqual(<lhs>, <rhs>)  /  assertIs / assertNotIn etc.
_ASSERT_METHOD = re.compile(
    r"^\s*self\.(?P<method>assert\w+)\((?P<args>.*)\)\s*$"
)


def _split_top_level_comma(s: str) -> list[str]:
    """Split on commas not nested in (), [], {}, or strings. Best-effort."""
    parts, depth, buf, quote = [], 0, [], None
    for ch in s:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in "\"'":
            quote = ch
            buf.append(ch)
        elif ch in "([{":
            depth += 1
            buf.append(ch)
        elif ch in ")]}":
            depth -= 1
            buf.append(ch)
        elif ch == "," and depth == 0:
            parts.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
    if buf:
        parts.append("".join(buf).strip())
    return parts


def _parse_assertion(line: str) -> tuple[str | None, str | None, str | None]:
    """Return (lhs, op, rhs) for a comparison-style assertion, else (None,)*3."""
    m = _ASSERT_CMP.match(line)
    if m:
        return m.group("lhs"), m.group("op"), m.group("rhs")
    m = _ASSERT_METHOD.match(line)
    if m:
        method, args = m.group("method"), m.group("args")
        parts = _split_top_level_comma(args)
        if method in ("assertEqual", "assertIs") and len(parts) >= 2:
            return parts[0], "==", parts[1]
        if method in ("assertNotEqual", "assertIsNot") and len(parts) >= 2:
            return parts[0], "!=", parts[1]
        if method == "assertTrue" and len(parts) >= 1:
            return parts[0], "==", "True"
        if method == "assertFalse" and len(parts) >= 1:
            return parts[0], "==", "False"
    return None, None, None


def _diff_assertion_lines(original_patch: str, corrupted_patch: str) -> tuple[list[str], list[str]]:
    """Return (added, removed) content lines that differ between the two patches.

    We diff the two *patch texts* line-by-line and keep only lines that carry
    assertion-ish content, with the diff's own +/- markers stripped.
    """
    a = original_patch.splitlines()
    b = corrupted_patch.splitlines()
    added, removed = [], []
    for line in difflib.unified_diff(a, b, lineterm="", n=0):
        if line.startswith(("+++", "---", "+@@", "-@@", "+index", "-index", "@@")):
            continue
        if line.startswith("+"):
            content = line[1:].lstrip("+- ")
            if content.strip():
                added.append(content.rstrip())
        elif line.startswith("-"):
            content = line[1:].lstrip("+- ")
            if content.strip():
                removed.append(content.rstrip())
    return added, removed


def _build_mutations(added: list[str], removed: list[str]) -> list[Mutation]:
    """Pair up changed assertions into Mutation records.

    Two shapes:
      * value_change  — a removed `assert lhs == X` paired with an added
        `assert lhs == Y` on the SAME lhs (one-off mutation).
      * added_conflict — an added assertion whose lhs matches an existing
        (unremoved) assertion, with a different rhs (conflicting mutation).
    """
    muts: list[Mutation] = []

    def is_assert(s: str) -> bool:
        return "assert" in s.lower()

    added_asserts = [s for s in added if is_assert(s)]
    removed_asserts = [s for s in removed if is_assert(s)]

    matched_added: set[int] = set()
    # value_change: same lhs in a removed and an added line
    for rline in removed_asserts:
        r_lhs, r_op, r_rhs = _parse_assertion(rline)
        for i, aline in enumerate(added_asserts):
            if i in matched_added:
                continue
            a_lhs, a_op, a_rhs = _parse_assertion(aline)
            if r_lhs is not None and a_lhs is not None and r_lhs == a_lhs and r_rhs != a_rhs:
                muts.append(Mutation(
                    original_line=rline.strip(), corrupted_line=aline.strip(),
                    lhs=a_lhs, op=a_op, original_rhs=r_rhs, corrupted_rhs=a_rhs,
                    kind="value_change",
                ))
                matched_added.add(i)
                break

    # leftover added asserts = added_conflict (or unpaired)
    for i, aline in enumerate(added_asserts):
        if i in matched_added:
            continue
        a_lhs, a_op, a_rhs = _parse_assertion(aline)
        muts.append(Mutation(
            original_line=None, corrupted_line=aline.strip(),
            lhs=a_lhs, op=a_op, original_rhs=None, corrupted_rhs=a_rhs,
            kind="added_conflict",
        ))
    return muts


# --- spec-agent test INPUTS (answers stripped) ------------------------------
# (The old mechanical redaction path — reconstruct_post_patch /
# redact_expected_values / spec_agent_test_inputs — was removed: it leaked
# expected values (audit Finding 5). Test-input material for the SpecAgent
# comes exclusively from load_test_inputs() above.)


def extract_instance(row: dict) -> SweInstance:
    """Build a SweInstance from a raw HF dataset row."""
    f2p = row["FAIL_TO_PASS"]
    if isinstance(f2p, str):
        f2p = json.loads(f2p)
    added, removed = _diff_assertion_lines(
        row.get("original_test_patch", ""), row.get("test_patch", "")
    )
    return SweInstance(
        instance_id=row["instance_id"],
        repo=row["repo"],
        base_commit=row["base_commit"],
        version=str(row.get("version", "")),
        difficulty=row.get("difficulty", ""),
        impossible_type=row.get("impossible_type", ""),
        problem_statement=row["problem_statement"],
        gold_patch=row["patch"],
        fail_to_pass=f2p,
        first_fail_to_pass=row.get("first_fail_to_pass_test", ""),
        mutations=_build_mutations(added, removed),
        raw_added_lines=added,
        raw_removed_lines=removed,
    )


def load_instances(instance_ids: list[str], split: str = "oneoff") -> list[SweInstance]:
    """Load+extract specific instances from the LOCAL impossible_swebench mirror.

    Reads data/swebench/_source/impossible_swebench_<split>.jsonl (a verbatim dump of
    the HF dataset ``fjzzq2002/impossible_swebench``, see HF_DATASET). Uses only stdlib
    ``json`` — no ``datasets``/HF dependency at runtime. To refresh/add splits, re-run
    the download+convert step that produced these JSONL files.
    """
    want = set(instance_ids)
    path = SWEBENCH_DIR / "_source" / f"impossible_swebench_{split}.jsonl"
    if not path.exists():
        raise FileNotFoundError(
            f"local split not found: {path}. Available splits: "
            "oneoff, conflicting, original (under data/swebench/_source/)."
        )
    by_id: dict[str, dict] = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            if r["instance_id"] in want:
                by_id[r["instance_id"]] = r
    missing = want - set(by_id)
    if missing:
        raise KeyError(f"instances not found in split={split}: {sorted(missing)}")
    return [extract_instance(by_id[i]) for i in instance_ids]


def dump_instances(instances: list[SweInstance], out_dir: Path = MANUAL_DIR) -> None:
    """Write one <instance_id>/instance.json per instance under data/swebench/manual/."""
    out_dir.mkdir(parents=True, exist_ok=True)
    for inst in instances:
        d = out_dir / inst.instance_id
        d.mkdir(parents=True, exist_ok=True)
        (d / "instance.json").write_text(json.dumps(asdict(inst), indent=2, ensure_ascii=False))
        print(f"[swebench] wrote {d / 'instance.json'}")


def load_instances_from_disk(instance_ids: list[str], data_dir: Path = MANUAL_DIR) -> list[SweInstance]:
    """Load instances from disk (data/swebench/manual/<instance_id>/instance.json)."""
    instances = []
    for iid in instance_ids:
        path = data_dir / iid / "instance.json"
        if not path.exists():
            raise FileNotFoundError(f"instance not found: {path}")
        data = json.loads(path.read_text())
        inst = SweInstance(
            instance_id=data["instance_id"],
            repo=data["repo"],
            base_commit=data["base_commit"],
            version=data["version"],
            difficulty=data["difficulty"],
            impossible_type=data["impossible_type"],
            problem_statement=data["problem_statement"],
            gold_patch=data["gold_patch"],
            fail_to_pass=data["fail_to_pass"],
            first_fail_to_pass=data["first_fail_to_pass"],
            mutations=[
                Mutation(
                    original_line=m.get("original_line"),
                    corrupted_line=m.get("corrupted_line"),
                    lhs=m.get("lhs"),
                    op=m.get("op"),
                    original_rhs=m.get("original_rhs"),
                    corrupted_rhs=m.get("corrupted_rhs"),
                    kind=m.get("kind"),
                )
                for m in data.get("mutations", [])
            ],
            raw_added_lines=data.get("raw_added_lines", []),
            raw_removed_lines=data.get("raw_removed_lines", []),
        )
        instances.append(inst)
    return instances
