"""tests/glossary_snapshot_ordering.test.py -- LT 1.16.0, the snapshot-then-audit
ORDERING in glossary-pass-wf.template.js.

Approval used to bind a PATH. The batch dispatch is
agentType:"codex:codex-rescue", the codex job outlives the awaited call (which is
why the 15-minute wait poll exists at all), and its own prompt instructs an
iterate-until-success rewrite loop against the attempt path -- so repeated atomic
renames over out_{index}_attempt_{n}.json are normal, expected behaviour. With the
reviewer auditing that mutable path, the bytes it approved and the bytes
--merge-batches later fresh-read off disk were only incidentally the same object.

The fix reorders: the reviewer's FIRST act is to run
`--check-batch <attempt> --approve-to <approved_{index}_attempt_{n}.json>`, which
re-validates and copies the exact validated bytes to an immutable path; it then
audits THAT copy, and under live the merge and the disk-independent verify consume
it too. Snapshotting AFTER the audit cannot close the race -- the race is between
the reviewer's read and the copy -- so the ORDER is the guarantee and is what this
file pins.

What this file asserts, and why each one needs the real template under Node rather
than a source grep:
  * the reviewer's READ TARGET is the snapshot, and the snapshot command precedes
    every read/fetch instruction in the rendered prompt (ordering is a property of
    the assembled prompt, not of any one string);
  * the emitted approve command is checkBatchCmd() plus --approve-to APPENDED, so
    the three character-identical --check-batch sites keep issuing that prefix
    verbatim and none of them acquires --approve-to;
  * under LIVE the merge and verify consume approved_{i}_attempt_{n}.json for the
    approved attempt, and no rejected attempt's snapshot is ever named;
  * under OFFLINE they consume the ATTEMPT path and name no snapshot at all --
    offline runs no reviewer, so a global rename to approved_* paths would fail
    every offline merge on a missing file;
  * the resume-skip entry point (which runs neither dispatch nor wait) still
    produces and merges its own snapshot;
  * the emitted snapshot BASENAME is one resume_setup.py's wipe actually matches,
    which is the JS-to-Python seam no per-file suite can see on its own.

Sibling coverage, deliberately not duplicated here: tests/canon_approve_to.test.py
owns --approve-to's byte-exactness and mode refusals, tests/
glossary_fragment_wipe.test.py owns the wipe rule, and tests/
glossary_citation_review.test.py owns the review's verdict/containment/retry-ladder
control flow.
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
ASSETS_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets"
GLOSSARY_TEMPLATE = ASSETS_DIR / "templates" / "glossary-pass-wf.template.js"
RESUME_SETUP = ASSETS_DIR / "scripts" / "resume_setup.py"

assert GLOSSARY_TEMPLATE.is_file(), f"expected plugin template not found: {GLOSSARY_TEMPLATE}"
assert RESUME_SETUP.is_file(), f"expected plugin script not found: {RESUME_SETUP}"

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(
    NODE is None,
    reason="node not found on PATH; this test executes the real glossary-pass "
    "template's snapshot/merge wiring under Node (no hard Node.js dependency "
    "for this plugin otherwise)",
)

FIXTURE_DURABLE_ROOT = "/fixture/project/durable_root"
FIXTURE_RUN_ID = "20260726T000000Z"
FIXTURE_SOURCE_LANG = "French"
FIXTURE_TARGET_LANG = "Russian"

RUN_DIR = f"{FIXTURE_DURABLE_ROOT}/glossary/runs/{FIXTURE_RUN_ID}"


def attempt_path(index: int, attempt: int) -> str:
    """The MUTABLE attempt fragment path -- what codex writes and rewrites."""
    return f"{RUN_DIR}/out_{index}_attempt_{attempt}.json"


def approved_path(index: int, attempt: int) -> str:
    """The IMMUTABLE approved snapshot path -- what the review audits and, under
    live, what merges."""
    return f"{RUN_DIR}/approved_{index}_attempt_{attempt}.json"


def check_cmd_from_wait(out: dict, index: int, attempt: int = 0) -> str:
    """The --check-batch command the WAIT poll for this attempt actually issued,
    lifted back out of its rendered polling loop.

    Deliberately extracted from a rendered prompt rather than transcribed into a
    local f-string. checkBatchCmd() is a pinned contract three sites must issue
    character-identically, and what has to hold is that the approve command is
    THAT string plus an appended flag -- so the comparison has to be against the
    string the template really emitted, not against this file's idea of it. A
    local copy would keep agreeing with itself after a contract change on either
    side.
    """
    prompt = prompts_for(out, f"glossary:wait:{index}")[attempt]
    lines = [ln for ln in prompt.split("\n") if "--check-batch" in ln]
    assert len(lines) == 1, f"expected one --check-batch line in the wait prompt, got {lines}"
    loop = lines[0]
    start = loop.index("do ") + len("do ")
    end = loop.index(" && exit 0")
    cmd = loop[start:end]
    assert attempt_path(index, attempt) in cmd, (
        f"the wait poll for attempt {attempt} does not name that attempt's path: {cmd}"
    )
    return cmd


def check_cmd_from_precheck(out: dict, index: int) -> str:
    """Same, from the PRECHECK -- the one site that still issues the contract on
    the resume-skip path, where neither a dispatch nor a wait ever runs."""
    prompt = prompts_for(out, f"glossary:precheck:{index}")[0]
    lines = [ln.strip() for ln in prompt.split("\n")
             if "--check-batch" in ln and ln.strip().startswith("python3")]
    assert len(lines) == 1, f"expected one bare --check-batch command line, got {lines}"
    return lines[0]


def approve_cmd_for(check_cmd: str, index: int, attempt: int) -> str:
    """--approve-to APPENDED to the pinned contract, never interleaved."""
    return check_cmd + " --approve-to " + approved_path(index, attempt)


def instantiate(*, research_mode: str = "live", batch_agent_cap: int = 10_000) -> str:
    """The exact one-time substitution the template's header documents
    (duplicated, not imported, so this file stays self-contained like every
    sibling harness)."""
    text = GLOSSARY_TEMPLATE.read_text(encoding="utf-8")
    text = text.replace("{{DURABLE_ROOT}}", FIXTURE_DURABLE_ROOT)
    text = text.replace("{{SOURCE_LANG}}", FIXTURE_SOURCE_LANG)
    text = text.replace("{{TARGET_LANG}}", FIXTURE_TARGET_LANG)
    text = text.replace("{{RESEARCH_MODE}}", research_mode)
    text = text.replace("{{RUN_ID}}", FIXTURE_RUN_ID)
    text = text.replace("{{BATCH_AGENT_CAP}}", str(int(batch_agent_cap)))
    text = text.replace("{{EFFORT}}", "high")
    assert "{{" not in text, "fixture instantiation left an unresolved token"
    return text


def _wrap(js_source: str) -> str:
    assert js_source.count("export const meta") == 1
    body = js_source.replace("export const meta", "const meta", 1)
    return "async function __workflowMain__(agent, pipeline, log, args) {\n" + body + "\n}\n"


def make_batch(index: int, names: list) -> dict:
    return {
        "index": index,
        "candidates": [
            {
                "name": n, "freq": 3, "mid_sentence": False, "multiword": False,
                "abbrev": False, "n_segments": 2, "likely_name": True,
            }
            for n in names
        ],
    }


# Records every call's label and rendered prompt IN ORDER, appending per label
# because one label fires once per attempt. PLAN is keyed by the batch's own
# string index; `reviews` is consumed positionally, one entry per attempt.
HARNESS = r"""
'use strict';
__WRAPPED_SOURCE__

const BATCHES_ARGS = __BATCHES_JSON__;
const PLAN = __PLAN_JSON__;
const promptsByLabel = {};
const callsLog = [];
const logLines = [];
const seenCount = {};

function record(label, promptText) {
  if (!promptsByLabel[label]) promptsByLabel[label] = [];
  promptsByLabel[label].push(typeof promptText === "string" ? promptText : String(promptText));
  seenCount[label] = (seenCount[label] || 0) + 1;
  return seenCount[label] - 1;
}

function nth(list, i, fallback) {
  if (!Array.isArray(list)) return fallback;
  return (i < list.length) ? list[i] : fallback;
}

async function agent(promptText, opts) {
  opts = opts || {};
  const label = opts.label || "";
  const ordinal = record(label, promptText);
  callsLog.push({ label: label, ordinal: ordinal, effort: opts.effort || null,
                  agentType: opts.agentType || null });

  if (label === "glossary:merge") return "MERGED (mock)";
  if (label === "glossary:verify") return { verified: true };

  const parts = label.split(":");
  const kind = parts[1];
  const idx = parts[parts.length - 1];
  const p = PLAN[idx] || {};

  if (kind === "precheck") {
    return Object.prototype.hasOwnProperty.call(p, "precheck") ? p.precheck : ("ABSENT " + idx);
  }
  if (kind === "dispatch") return "FRAGMENT " + idx;
  if (kind === "wait") return "READY " + idx;
  if (kind === "citation-review") {
    return nth(p.reviews, ordinal, "CITATIONS_OK " + idx + " ATTEMPT " + ordinal);
  }
  // Deliberately non-throwing: an unrecognized label must surface as a failed
  // ASSERTION with readable context, not an opaque harness crash. RED runs
  // against the pre-fix template rely on this.
  return "UNEXPECTED_LABEL " + label;
}

async function pipeline(items, stage1) {
  const out = [];
  for (const item of items) out.push(await stage1(item));
  return out;
}

function log(msg) { logLines.push(String(msg)); }

(async () => {
  try {
    const result = await __workflowMain__(agent, pipeline, log, BATCHES_ARGS);
    process.stdout.write(JSON.stringify({
      result: result, calls: callsLog, promptsByLabel: promptsByLabel, log: logLines,
    }));
  } catch (err) {
    process.stderr.write("HARNESS_ERROR: " + (err && err.stack || String(err)) + "\n");
    process.exit(1);
  }
})();
"""


def run(*, tmp_path: Path, batches: list, research_mode: str = "live",
        plan: dict | None = None, timeout: int = 30) -> dict:
    plan = plan or {}
    harness = (
        HARNESS.replace("__WRAPPED_SOURCE__", _wrap(instantiate(research_mode=research_mode)))
        .replace("__BATCHES_JSON__", json.dumps(batches))
        .replace("__PLAN_JSON__", json.dumps(plan))
    )
    p = tmp_path / "glossary_snapshot_harness.js"
    p.write_text(harness, encoding="utf-8")
    # NODE is only None when `node` is absent from PATH, in which case
    # pytestmark's skipif already skipped this test before the call is reached.
    assert NODE is not None
    proc = subprocess.run([NODE, str(p)], capture_output=True, text=True, timeout=timeout)
    assert proc.returncode == 0, f"template threw under Node: {proc.stderr}"
    return json.loads(proc.stdout)


def prompts_for(out: dict, label: str) -> list:
    return out["promptsByLabel"].get(label, [])


def labels_of(out: dict) -> list:
    return [c["label"] for c in out["calls"]]


def one_batch_run(tmp_path: Path, **kwargs) -> dict:
    return run(tmp_path=tmp_path, batches=[make_batch(0, ["Sarrasin", "Enclos"])], **kwargs)


# ---------------------------------------------------------------------------
# 1. The reviewer's READ TARGET, and the ordering that makes it meaningful.
# ---------------------------------------------------------------------------

def test_the_review_reads_the_snapshot_never_the_mutable_attempt_path(tmp_path):
    """The whole reorder, stated as the one assertion that fails on the old code.

    The attempt path legitimately still APPEARS in this prompt -- inside the
    approve command, and in the prose forbidding a later read of it -- so
    "attempt path absent" is the wrong assertion and would be un-RED-able. What
    must hold is that the line naming the file to READ names the snapshot.
    """
    out = one_batch_run(tmp_path)
    review = prompts_for(out, "glossary:citation-review:0")[0]

    read_lines = [ln for ln in review.split("\n") if ln.startswith("STEP 2.")]
    assert len(read_lines) == 1, (
        "expected exactly one STEP 2 read instruction in the citation-review "
        f"prompt, found {len(read_lines)}"
    )
    read_line = read_lines[0]
    assert approved_path(0, 0) in read_line, (
        "the citation reviewer must be pointed at the immutable approved "
        f"snapshot {approved_path(0, 0)}; its read instruction was: {read_line}"
    )
    assert attempt_path(0, 0) not in read_line, (
        "the citation reviewer must NOT be pointed at the mutable attempt path "
        f"{attempt_path(0, 0)} -- a still-running codex job rewrites it. Its "
        f"read instruction was: {read_line}"
    )


def test_the_snapshot_command_precedes_every_read_and_fetch_instruction(tmp_path):
    """Ordering, asserted on the assembled prompt rather than on any one string.

    Snapshotting after the audit does not close the race -- the race is between
    the reviewer's read and the copy -- so the approve command has to come first,
    and "first" is a fact about line positions in the rendered prompt.
    """
    out = one_batch_run(tmp_path)
    review = prompts_for(out, "glossary:citation-review:0")[0]
    approve_cmd = approve_cmd_for(check_cmd_from_wait(out, 0), 0, 0)
    lines = review.split("\n")

    approve_idx = [i for i, ln in enumerate(lines) if approve_cmd in ln]
    assert len(approve_idx) == 1, (
        f"expected exactly one approve-command line, found {len(approve_idx)}"
    )
    read_idx = [i for i, ln in enumerate(lines) if ln.startswith("STEP 2.")]
    fetch_idx = [i for i, ln in enumerate(lines) if "Actually fetch the URL" in ln]
    assert read_idx and fetch_idx, "prompt lost its read or its fetch instruction"

    assert approve_idx[0] < read_idx[0], (
        "the snapshot command must precede the read instruction: approve at line "
        f"{approve_idx[0]}, read at line {read_idx[0]}"
    )
    assert approve_idx[0] < fetch_idx[0], (
        "the snapshot command must precede the fetch instruction: approve at line "
        f"{approve_idx[0]}, fetch at line {fetch_idx[0]}"
    )


def test_the_reviewer_is_told_to_reject_when_the_snapshot_command_fails(tmp_path):
    """A fragment that no longer passes --check-batch was rewritten underneath
    the reviewer. The correct answer is a fresh attempt, never an audit of bytes
    that failed validation -- so the failure branch must route to the rejection
    sentinel, not to a best-effort audit."""
    out = one_batch_run(tmp_path)
    review = prompts_for(out, "glossary:citation-review:0")[0]
    fail_lines = [ln for ln in review.split("\n") if "exits non-zero" in ln]
    assert len(fail_lines) == 1, (
        f"expected one snapshot-command failure instruction, found {len(fail_lines)}"
    )
    assert "reject" in fail_lines[0].lower(), (
        "a failed snapshot command must reject the batch rather than proceed to "
        f"an audit; the instruction was: {fail_lines[0]}"
    )


# ---------------------------------------------------------------------------
# 2. The --check-batch contract prefix survived, and did not leak --approve-to.
# ---------------------------------------------------------------------------

def test_the_approve_command_is_the_check_batch_contract_plus_approve_to(tmp_path):
    """--approve-to is APPENDED, never interleaved. The dispatch prompt tells
    codex to re-run "exactly the command above", so the --check-batch prefix has
    to stay reproducible from the dispatch side, with --research-mode still ahead
    of --expect-source-forms-file."""
    out = one_batch_run(tmp_path)
    review = prompts_for(out, "glossary:citation-review:0")[0]
    expected = approve_cmd_for(check_cmd_from_wait(out, 0), 0, 0)
    assert expected in review, (
        "the citation-review prompt must issue the wait poll's own --check-batch "
        f"command with --approve-to appended:\n  expected: {expected}\n"
        f"  review prompt was:\n{review}"
    )


@pytest.mark.parametrize("label", [
    "glossary:precheck:0", "glossary:dispatch:0", "glossary:wait:0",
])
def test_no_plain_check_batch_site_ever_issues_approve_to(tmp_path, label):
    """The three sites that must issue checkBatchCmd() character-identically keep
    issuing it bare. A precheck or a wait poll that snapshotted would write an
    approved copy of bytes nobody has reviewed; a dispatch self-check that did it
    would let the producer approve its own output."""
    out = one_batch_run(tmp_path)
    prompts = prompts_for(out, label)
    assert prompts, f"no prompt recorded for {label}"
    for prompt in prompts:
        assert "--approve-to" not in prompt, (
            f"{label} must not carry --approve-to -- only the citation review "
            "snapshots"
        )


# ---------------------------------------------------------------------------
# 3. What the merge and the disk-independent verify actually consume.
# ---------------------------------------------------------------------------

def test_live_merge_and_verify_consume_the_approved_snapshot(tmp_path):
    out = one_batch_run(tmp_path)
    merge = prompts_for(out, "glossary:merge")[0]
    verify = prompts_for(out, "glossary:verify")[0]

    for name, prompt in (("merge", merge), ("verify", verify)):
        assert approved_path(0, 0) in prompt, (
            f"the {name} call must consume the approved snapshot "
            f"{approved_path(0, 0)}"
        )
        assert attempt_path(0, 0) not in prompt, (
            f"the {name} call must NOT name the mutable attempt path "
            f"{attempt_path(0, 0)} -- a fresh read of it can return bytes that "
            "were never reviewed"
        )

    result = out["result"]
    assert result["merged"] is True
    batch_result = result["batches"][0]
    assert batch_result["mergePath"] == approved_path(0, 0)
    # fragmentPath stays as the diagnostic record of which attempt produced the
    # bytes, and is deliberately not what merges.
    assert batch_result["fragmentPath"] == attempt_path(0, 0)


def test_a_rejected_attempts_snapshot_never_reaches_the_merge(tmp_path):
    """Attempt-scoping the snapshot is what buys this. With one
    approved_{index} per batch, a rejected earlier attempt's snapshot would sit
    at exactly the path a later attempt's merge names."""
    out = one_batch_run(tmp_path, plan={"0": {"reviews": [
        "item Sarrasin: source 404s\nCITATIONS_REJECTED 0 ATTEMPT 0",
        "CITATIONS_OK 0 ATTEMPT 1",
    ]}})
    merge = prompts_for(out, "glossary:merge")[0]

    assert approved_path(0, 1) in merge, "the APPROVED attempt's snapshot must merge"
    assert approved_path(0, 0) not in merge, (
        f"the rejected attempt's snapshot {approved_path(0, 0)} must never be "
        "handed to the merge"
    )
    assert attempt_path(0, 1) not in merge, (
        "the approved attempt's MUTABLE path must not merge either"
    )
    assert out["result"]["batches"][0]["mergePath"] == approved_path(0, 1)


def test_each_attempt_snapshots_to_its_own_path(tmp_path):
    """Every attempt's review issues its own attempt-scoped approve command, so a
    later attempt can never re-approve an earlier attempt's snapshot."""
    out = one_batch_run(tmp_path, plan={"0": {"reviews": [
        "bad source\nCITATIONS_REJECTED 0 ATTEMPT 0",
        "bad source again\nCITATIONS_REJECTED 0 ATTEMPT 1",
        "CITATIONS_OK 0 ATTEMPT 2",
    ]}})
    reviews = prompts_for(out, "glossary:citation-review:0")
    assert len(reviews) == 3, f"expected three review calls, got {len(reviews)}"
    for attempt, prompt in enumerate(reviews):
        expected = approve_cmd_for(check_cmd_from_wait(out, 0, attempt), 0, attempt)
        assert expected in prompt, (
            f"attempt {attempt}'s review must snapshot that attempt's own "
            f"fragment to {approved_path(0, attempt)}:\n  expected: {expected}"
        )
        for other in range(3):
            if other == attempt:
                continue
            assert approved_path(0, other) not in prompt, (
                f"attempt {attempt}'s review names another attempt's snapshot "
                f"{approved_path(0, other)}"
            )


def test_offline_merge_consumes_the_attempt_path_and_names_no_snapshot(tmp_path):
    """The explicit live/offline branch, not a global rename.

    Offline forbids basis:"established" outright, so no reviewer runs and nothing
    ever issues an approve command. A merge that always consumed approved_* paths
    would name a file that cannot exist and every offline run would die at the
    merge on a missing file.
    """
    out = one_batch_run(tmp_path, research_mode="offline")
    assert "glossary:citation-review:0" not in labels_of(out), (
        "offline must spend no citation-review call"
    )
    merge = prompts_for(out, "glossary:merge")[0]
    verify = prompts_for(out, "glossary:verify")[0]

    for name, prompt in (("merge", merge), ("verify", verify)):
        assert attempt_path(0, 0) in prompt, (
            f"the offline {name} call must consume the attempt path"
        )
        assert "approved_" not in prompt, (
            f"the offline {name} call must name no snapshot at all -- offline "
            f"writes none. Prompt was: {prompt}"
        )

    assert out["result"]["merged"] is True
    batch_result = out["result"]["batches"][0]
    assert batch_result["mergePath"] == attempt_path(0, 0)
    assert batch_result["citationReview"] == "skipped-offline"


def test_the_resume_skip_entry_point_still_produces_its_own_snapshot(tmp_path):
    """ENTRY A runs neither the dispatch nor the wait, which is exactly why the
    snapshot is taken inside the reviewer's turn rather than in the wait step: a
    wait-side snapshot would be skipped on every resumed batch, and a resumed,
    never-reviewed fragment is the case this whole stage exists for."""
    out = one_batch_run(tmp_path, plan={"0": {"precheck": "PRESENT 0"}})
    order = labels_of(out)
    assert "glossary:dispatch:0" not in order, "fixture did not take the resume-skip path"
    assert "glossary:wait:0" not in order, "fixture did not take the resume-skip path"

    review = prompts_for(out, "glossary:citation-review:0")[0]
    expected = approve_cmd_for(check_cmd_from_precheck(out, 0), 0, 0)
    assert expected in review, (
        "a resume-skipped batch must still snapshot its own fragment:\n"
        f"  expected: {expected}"
    )
    merge = prompts_for(out, "glossary:merge")[0]
    assert approved_path(0, 0) in merge
    assert attempt_path(0, 0) not in merge


def test_a_healthy_sibling_batch_merges_its_own_snapshot(tmp_path):
    """Two batches, different winning attempts: each merges the snapshot of its
    OWN approved attempt, in ascending batch-index order."""
    out = run(tmp_path=tmp_path, batches=[make_batch(0, ["Sarrasin"]), make_batch(1, ["Enclos"])],
              plan={"1": {"reviews": ["bad\nCITATIONS_REJECTED 1 ATTEMPT 0",
                                      "CITATIONS_OK 1 ATTEMPT 1"]}})
    merge = prompts_for(out, "glossary:merge")[0]
    assert approved_path(0, 0) in merge
    assert approved_path(1, 1) in merge
    assert approved_path(1, 0) not in merge
    assert merge.index(approved_path(0, 0)) < merge.index(approved_path(1, 1)), (
        "fragments must reach --merge-batches in ascending batch-index order"
    )


# ---------------------------------------------------------------------------
# 4. The JS-to-Python seam: the snapshot name the template emits has to be a
#    name resume_setup.py's wipe actually matches. Each side's own suite is
#    green either way -- only this comparison can fail.
# ---------------------------------------------------------------------------

def _load_resume_setup():
    """Loads resume_setup.py by file identity under a private module name.

    Every harness here is otherwise self-contained; this is a deliberate
    exception, and it is the point of the test below -- the assertion has to see
    the REAL wipe regex, not a third local transcription of it. The private name
    keeps this load from contending with tests/glossary_fragment_wipe.test.py's
    own load of the same file.
    """
    spec = importlib.util.spec_from_file_location(
        "_lt_resume_setup_snapshot_seam_probe", RESUME_SETUP
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_emitted_snapshot_basename_is_one_the_wipe_matches(tmp_path):
    """The producer/consumer seam this release's fan-out most easily breaks.

    The template emits snapshot paths; resume_setup.py deletes them by regex on
    the basename. A rename on either side alone leaves both files' suites green
    and silently strands stale snapshots in the run directory, where a later
    merge that was handed a path whose snapshot was never written this run would
    find a previous run's file waiting for it.
    """
    resume_setup = _load_resume_setup()
    pattern = resume_setup._GLOSSARY_FRAGMENT_RE

    out = one_batch_run(tmp_path, plan={"0": {"reviews": [
        "bad\nCITATIONS_REJECTED 0 ATTEMPT 0", "CITATIONS_OK 0 ATTEMPT 1",
    ]}})
    emitted = out["result"]["batches"][0]["mergePath"]
    assert emitted == approved_path(0, 1), "fixture self-check on the merged path"

    basename = emitted.rsplit("/", 1)[-1]
    match = pattern.match(basename)
    assert match is not None, (
        f"the template emits snapshot basename {basename!r}, which "
        "resume_setup.py's _GLOSSARY_FRAGMENT_RE does not match -- the wipe "
        "would leave it on disk across runs"
    )
    assert match.group(1) == "approved", (
        f"expected the wipe to classify {basename!r} as an approved snapshot, "
        f"got kind {match.group(1)!r}"
    )
    assert (match.group(2), match.group(3)) == ("0", "1"), (
        "the wipe must read this snapshot's batch index and attempt number back "
        f"out of its name; got {match.groups()!r} for {basename!r}"
    )


def test_the_emitted_attempt_basename_is_one_the_wipe_matches(tmp_path):
    """The same seam for the offline merge path, which is an out_* fragment.

    Asserted separately because the resume rule treats the two kinds
    differently -- attempt 0 of out_* survives a resume, every approved_* does
    not -- so the wipe has to be able to tell them apart by name.
    """
    resume_setup = _load_resume_setup()
    out = one_batch_run(tmp_path, research_mode="offline")
    emitted = out["result"]["batches"][0]["mergePath"]
    assert emitted == attempt_path(0, 0), "fixture self-check on the merged path"

    match = resume_setup._GLOSSARY_FRAGMENT_RE.match(emitted.rsplit("/", 1)[-1])
    assert match is not None, (
        f"the template emits attempt basename {emitted.rsplit('/', 1)[-1]!r}, "
        "which resume_setup.py's _GLOSSARY_FRAGMENT_RE does not match"
    )
    assert match.group(1) == "out"
