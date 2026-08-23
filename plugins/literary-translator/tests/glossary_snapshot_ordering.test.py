"""tests/glossary_snapshot_ordering.test.py -- LT 1.16.x, the
snapshot-then-fetch-then-audit ORDERING in glossary-pass-wf.template.js.

Approval used to bind a PATH. The batch dispatch is
agentType:"codex:codex-rescue", the codex job outlives the awaited call (which is
why the 15-minute wait poll exists at all), and its own prompt instructs an
iterate-until-success rewrite loop against the attempt path -- so repeated atomic
renames over out_{index}_attempt_{n}.json are normal, expected behaviour. With the
reviewer auditing that mutable path, the bytes it approved and the bytes
--merge-batches later fresh-read off disk were only incidentally the same object.

The fix reorders: the FIRST act of the citation-review stage is to run
`--check-batch <attempt> --approve-to <approved_{index}_attempt_{n}.json>`, which
re-validates and copies the exact validated bytes to the approved snapshot;
everything downstream -- the retrieval of the cited URLs, the audit, and under live
the merge and the disk-independent verify -- consumes THAT copy. Snapshotting AFTER
the audit cannot close the race -- the race is between the read and the copy -- so
the ORDER is the guarantee and is what this file pins.

WHY EVERY ANCHOR IN THIS FILE MOVED IN 1.16.1 (#347), given none of the properties
did. The citation review used to be ONE agent that ran the approve command,
fetched every cited URL itself, and judged what came back. Retrieval has moved out
of the judging agent, so the stage is two calls:
  * PREPARE (label glossary:citation-prepare:{index}, effort low) runs the approve
    command as its STEP 1 and scripts/fetch_citation.py as its STEP 2, and ingests
    nothing but the one JSON line each command prints;
  * JUDGE (label glossary:citation-review:{index}, unchanged) reads local files
    only -- the snapshot, the evidence index, and the bodies that index names.
Consequently the approve command is asserted against the PREPARE prompt rather
than the review's; "the snapshot precedes every read and fetch" is now partly an
INTRA-prompt fact (prepare's STEP 1 before its STEP 2) and partly a CROSS-CALL one
(prepare before judge in the recorded call order); there is no "fetch the URL"
INSTRUCTION left anywhere, because retrieval is a script invocation, so the fetch
anchor is fetch_citation.py's own --batch argument; and the failure branch ("stop,
never audit bytes that failed validation") moved into the prepare prompt, where it
routes to EVIDENCE_FAILED instead of to a rejection verdict.

Anchors are structural (a `STEP <n>.` line prefix, an emitted command string, the
recorded call order) rather than phrase-matched wherever there was a choice: the
surrounding prose is edited far more often than the shape is, and a test that
fails on a rewording teaches the next reader to weaken it.

What this file asserts, and why each one needs the real template under Node rather
than a source grep:
  * the JUDGE's read target is the snapshot, and the judge's prompt names no
    fragment path at all; the FETCHER's --batch argument is the snapshot too;
  * the snapshot command precedes both -- prepare's STEP 1 before its STEP 2, and
    prepare before judge (ordering is a property of the assembled prompt and of
    the call sequence, not of any one string);
  * a prepare that reports failure spends no judge call, and its attempt's
    snapshot never reaches the merge;
  * the emitted approve command is checkBatchCmd() plus --approve-to APPENDED, so
    the four character-identical --check-batch sites keep issuing that prefix
    verbatim, and PREPARE is the only call in the file that carries --approve-to;
  * under LIVE the merge and verify consume approved_{i}_attempt_{n}.json for the
    approved attempt, and no rejected attempt's snapshot is ever named;
  * under OFFLINE they consume the ATTEMPT path and name no snapshot at all --
    offline runs neither half of the review, so a global rename to approved_*
    paths would fail every offline merge on a missing file;
  * the resume-skip entry point (which runs neither dispatch nor wait) still
    produces and merges its own snapshot;
  * the emitted snapshot BASENAME is one resume_setup.py's wipe actually matches,
    which is the JS-to-Python seam no per-file suite can see on its own.

Sibling coverage, deliberately not duplicated here: tests/canon_approve_to.test.py
owns --approve-to's byte-exactness and mode refusals, tests/
glossary_fragment_wipe.test.py owns the wipe rule, and tests/
glossary_citation_review.test.py owns the review's verdict/containment/retry-ladder
control flow.

What the snapshot itself guarantees, and the preconditions it rests on, is
stated once in references/canon-and-glossary.md, "What the approved snapshot
guarantees, and the preconditions it rests on" -- this file pins the ORDER only.
"""
from __future__ import annotations

import importlib.util
import json
import re
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
    """The approved snapshot path -- what the fetcher retrieves from, what the
    judge audits and, under live, what merges. Nothing in the pass rewrites it
    after publication, unlike the attempt path the codex loop keeps replacing."""
    return f"{RUN_DIR}/approved_{index}_attempt_{attempt}.json"


def evidence_dir(index: int, attempt: int) -> str:
    """Where fetch_citation.py writes this attempt's retrieved bodies plus its
    index.json (1.16.1, #347). ATTEMPT-scoped like the snapshot, so attempt n+1's
    judge cannot be handed attempt n's pages."""
    return f"{RUN_DIR}/evidence_{index}_attempt_{attempt}"


def step_line(prompt: str, step: int) -> tuple:
    """The one `STEP <n>.` line of a rendered prompt, with its line index.

    Both halves of the split review are STEP-numbered, and that numbering is the
    anchor this file leans on: the prose around each step is reworded far more
    often than the step structure changes, so matching a phrase would make these
    tests fail on edits that break nothing. Asserting exactly one line per step
    number is itself part of the check -- a duplicated STEP line is an
    ambiguous instruction, and an ordering assertion over an ambiguous
    instruction proves nothing.
    """
    prefix = f"STEP {step}."
    hits = [(i, ln) for i, ln in enumerate(prompt.split("\n")) if ln.startswith(prefix)]
    assert len(hits) == 1, (
        f"expected exactly one line opening with {prefix!r} in this prompt, "
        f"found {len(hits)}"
    )
    return hits[0]


# The chunk poll's ACCEPT gate, as it is actually rendered since 1.16.2 (#352):
#
#   end=$((SECONDS + 480)); while true; do <CMD> >/dev/null 2>&1 && exit 0; ...
#
# The `>/dev/null 2>&1` is load-bearing rather than cosmetic (--check-batch
# prints a JSON line per invocation, and without the redirect the chunk's own
# terminal marker would stop being the last line), which is exactly why it is
# matched here instead of being swept into the extracted command: the approve
# command is the pinned contract plus an APPENDED flag, so a suffix left on the
# extracted string would silently make every comparison below compare the wrong
# thing.
_CHUNK_ACCEPT_RE = re.compile(r"while true; do (.*?) >/dev/null 2>&1 && exit 0;")


def check_cmd_from_wait(out: dict, index: int, attempt: int = 0) -> str:
    """The --check-batch command the WAIT poll for this attempt actually issued,
    lifted back out of its rendered polling loop.

    Deliberately extracted from a rendered prompt rather than transcribed into a
    local f-string. checkBatchCmd() is a pinned contract four sites must issue
    character-identically, and what has to hold is that the approve command is
    THAT string plus an appended flag -- so the comparison has to be against the
    string the template really emitted, not against this file's idea of it. A
    local copy would keep agreeing with itself after a contract change on either
    side.

    1.16.2 (#352): one wait renders up to WAIT_CHUNKS chunk prompts, all under
    the same `glossary:wait:<index>` label, so the prompt list is no longer
    one-entry-per-attempt and positional indexing by `attempt` would silently
    read another attempt's chunk. Selected by the attempt's own fragment PATH
    instead, which is the thing that actually distinguishes them.
    """
    wanted = attempt_path(index, attempt)
    hits = []
    for prompt in prompts_for(out, f"glossary:wait:{index}"):
        for line in prompt.split("\n"):
            m = _CHUNK_ACCEPT_RE.search(line)
            if m and "--check-batch" in m.group(1) and wanted in m.group(1):
                hits.append(m.group(1))
    assert hits, (
        f"no wait chunk for batch {index} attempt {attempt} issued a --check-batch "
        f"gate naming {wanted}"
    )
    # Every chunk of one attempt's wait splices the SAME builder, so they must
    # all be character-identical; asserting that here is what lets the callers
    # treat "the command this attempt's wait issued" as a single well-defined
    # string at all.
    assert len(set(hits)) == 1, (
        f"the wait chunks for batch {index} attempt {attempt} issued DIFFERENT "
        f"--check-batch commands: {sorted(set(hits))}"
    )
    return hits[0]


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
    # 1.16.1 (#347): empty = fetch_citation.py's shipped default list.
    text = text.replace("{{CITATION_CONTENT_TYPES}}", "")
    # #412 -- json.dumps JS string literal, token OUTSIDE quotes. Empty is NOT
    # a valid value for the GLOSSARY template's {{PLUGIN_ROOT}}; the canonical
    # explanation of why (and of why mass-translate-wf.template.js's own
    # empty-string opt-out is deliberately NOT harmonised with it) lives once,
    # on FIXTURE_GLOSSARY_PLUGIN_ROOT in workflow_template_instantiation
    # .test.py and in the template's own header token entry -- not restated
    # here. The real plugin skill root resolves a genuine cache_key.py, so the
    # guard accepts it; this file's snapshot-ordering assertions inspect
    # nothing else about it.
    text = text.replace("{{PLUGIN_ROOT}}", json.dumps(str(PLUGIN_ROOT / "skills" / "literary-translator")))
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
# string index; `prepares` and `reviews` are each consumed positionally, one
# entry per call of that label (see the citation-review branch below for the one
# case where a review's position is NOT its attempt number).
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
  callsLog.push({
    label: label, ordinal: ordinal, effort: opts.effort || null,
    agentType: opts.agentType || null,
    // Round-8: captures the REAL schema literal's own additionalProperties
    // flag at the moment of each call, so a test can assert directly on the
    // template's own schema declaration (e.g. that CANON_VERIFY_SCHEMA
    // forbids extra fields) without this mock needing to simulate the real
    // Workflow engine's schema-validation/retry-until-valid enforcement.
    hasSchema: !!opts.schema,
    schemaAdditionalProperties: opts.schema ? opts.schema.additionalProperties : null,
  });

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
  // 1.16.2 (#352): the wait poll is chunked, and every chunk of every attempt
  // shares this one label, so a plan-supplied reply is consumed POSITIONALLY --
  // one entry per call, not one per attempt. The default keeps every prior
  // caller's behaviour (READY on the first chunk, so the re-check below never
  // fires) while letting a plan force PENDING to reach it.
  if (kind === "wait") return nth(p.waits, ordinal, "READY " + idx);
  // The authoritative re-check (#352) -- reached only when every chunk of an
  // attempt's wait answered something other than READY. Same default as
  // "wait" above: a plan that says nothing gets a READY re-check, so a run
  // that never forces PENDING waits never has to know this branch exists.
  if (kind === "wait-recheck") return nth(p.rechecks, ordinal, "READY " + idx);
  // 1.16.1 (#347) -- the citation review became TWO calls, and this branch is
  // what the whole file's live path now hangs on: the JUDGE runs only if PREPARE
  // reported EVIDENCE_READY, so a harness that leaves this label unanswered
  // sends every live batch up the retry ladder to exhaustion, records no review
  // prompt at all, and merges nothing. That is exactly how these tests failed
  // before this branch existed -- not on the property each one guards.
  if (kind === "citation-prepare") {
    return nth(p.prepares, ordinal, "EVIDENCE_READY " + idx + " ATTEMPT " + ordinal);
  }
  if (kind === "citation-review") {
    // The judge's own ordinal counts JUDGED attempts, which stops being the
    // attempt NUMBER as soon as a prepare fails -- a failed prepare spends no
    // judge call, so attempt 1's judge is still ordinal 0. The verdict sentinel
    // carries the attempt and a stale one is rejected by design, so the DEFAULT
    // verdict derives the attempt from how many prepares this batch has had:
    // exactly one per attempt, always issued before that attempt's judge. A
    // PLAN-supplied review is still taken by ordinal and spells its own
    // sentinel out, so a plan that exercises a prepare failure must count
    // judged attempts, not attempts.
    const prepared = seenCount["glossary:citation-prepare:" + idx] || 1;
    return nth(p.reviews, ordinal, "CITATIONS_OK " + idx + " ATTEMPT " + (prepared - 1));
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


def sole_prompt(out: dict, label: str) -> str:
    """The one prompt recorded for a label that must fire exactly once.

    Worth a helper rather than a bare [0]: the failure mode this file most needs
    to read clearly is a call that stopped happening at all -- a review bypassed
    on the resume path, a prepare deleted -- and `prompts_for(...)[0]` reports
    that as `IndexError: list index out of range`, which reads like a broken
    harness rather than a missing gate. Measured while mutation-testing this
    file: exempting resumed batches from the review produced exactly that opaque
    error until this helper existed.
    """
    prompts = prompts_for(out, label)
    assert len(prompts) == 1, (
        f"expected exactly one {label} call, got {len(prompts)}; the calls this "
        f"run actually made were {labels_of(out)}"
    )
    return prompts[0]


def labels_of(out: dict) -> list:
    return [c["label"] for c in out["calls"]]


def one_batch_run(tmp_path: Path, **kwargs) -> dict:
    return run(tmp_path=tmp_path, batches=[make_batch(0, ["Sarrasin", "Enclos"])], **kwargs)


def pending_wait_run(tmp_path: Path, **kwargs) -> dict:
    """A run whose chunked wait poll never answers READY, so the fallthrough to
    the authoritative re-check (glossary:wait-recheck:0, #352) actually fires.

    one_batch_run's wait answers READY on its first chunk by default, which is
    exactly why the re-check never rendered a prompt anywhere in this file
    before it got its own roster entry: nothing ever drove the harness past the
    chunk loop. WAIT_CHUNKS is 2 in the shipped template, so two PENDING
    replies exhaust the chunk budget and hand control to the re-check, which
    then defaults to READY so the rest of the run (prepare, judge, merge)
    completes exactly like every other fixture here.
    """
    plan = {"0": {"waits": ["PENDING 0", "PENDING 0"]}}
    return run(tmp_path=tmp_path, batches=[make_batch(0, ["Sarrasin", "Enclos"])],
               plan=plan, **kwargs)


# ---------------------------------------------------------------------------
# 1. What the stage READS AND RETRIEVES FROM -- one target per half since the
#    1.16.1 split -- and the ordering that makes both meaningful.
# ---------------------------------------------------------------------------

def test_the_review_reads_the_snapshot_never_the_mutable_attempt_path(tmp_path):
    """The whole reorder, stated as the one assertion that fails on the old code.

    ANCHOR MOVED IN 1.16.1, and the property got STRONGER rather than weaker.
    Before the split this had to be written narrowly -- only the READ line could
    be checked -- because the attempt path legitimately appeared elsewhere in
    this same prompt, inside the approve command and in the prose forbidding a
    later read of it, so "attempt path absent" was the wrong assertion and would
    have been un-RED-able. The judge now runs no command and is handed no
    fragment path at all, deliberately (see citationJudgePrompt(): a
    prompt-injected judge should have to GUESS that string rather than be given
    it). So the read-line check is KEPT -- it is what fails if the read is
    repointed -- and the whole-prompt absence check is ADDED on top, which is
    what fails if the fragment path leaks back into the judge's prompt at all.
    """
    out = one_batch_run(tmp_path)
    review = sole_prompt(out, "glossary:citation-review:0")

    _, read_line = step_line(review, 2)
    assert approved_path(0, 0) in read_line, (
        "the citation judge must be pointed at the approved "
        f"snapshot {approved_path(0, 0)}; its read instruction was: {read_line}"
    )
    assert attempt_path(0, 0) not in read_line, (
        "the citation judge must NOT be pointed at the mutable attempt path "
        f"{attempt_path(0, 0)} -- a still-running codex job rewrites it. Its "
        f"read instruction was: {read_line}"
    )
    # The stem rather than one attempt's full path: repointing the judge at a
    # DIFFERENT attempt's fragment is the same defect and must fail here too.
    assert "out_0_attempt_" not in review, (
        "the citation judge's prompt must not name any attempt fragment path, "
        "not even inside prose forbidding a read of it -- it is given the "
        f"snapshot and nothing else. Prompt was:\n{review}"
    )


def test_the_retrieval_reads_the_snapshot_never_the_mutable_attempt_path(tmp_path):
    """The other half of the same invariant, which 1.16.1 moved into its own call.

    Until the split, one agent both fetched and judged, so "the review consumes
    the snapshot" was one assertion over one prompt. Retrieval is now
    fetch_citation.py, launched from PREPARE, so the invariant needs two
    assertions over two prompts. Dropping this half would leave the fetcher free
    to retrieve URLs off the mutable attempt path -- the same race the snapshot
    closes, one layer out: the URLs fetched would be ones no judge ever approved
    and no merge ever sees.
    """
    out = one_batch_run(tmp_path)
    prepare = sole_prompt(out, "glossary:citation-prepare:0")
    _, fetch_line = step_line(prepare, 2)

    assert "fetch_citation.py" in fetch_line, (
        f"prepare's STEP 2 is no longer the retrieval boundary: {fetch_line}"
    )
    assert " --batch " + approved_path(0, 0) in fetch_line, (
        "the fetcher must be pointed at the approved snapshot "
        f"{approved_path(0, 0)}; its invocation was: {fetch_line}"
    )
    assert attempt_path(0, 0) not in fetch_line, (
        "the fetcher must NOT retrieve from the mutable attempt path "
        f"{attempt_path(0, 0)}; its invocation was: {fetch_line}"
    )
    assert " --out-dir " + evidence_dir(0, 0) in fetch_line, (
        "the evidence directory must be attempt-scoped, so a later attempt's "
        f"judge cannot read an earlier attempt's pages: {fetch_line}"
    )


def test_the_snapshot_command_precedes_every_read_and_fetch_instruction(tmp_path):
    """Ordering, which since 1.16.1 spans a prompt AND a call boundary.

    Snapshotting after the audit does not close the race -- the race is between
    the read and the copy -- so the approve command has to come first. "First"
    used to be entirely a fact about line positions in one rendered prompt; it is
    now two facts, and BOTH have to hold: prepare's STEP 1 (the approve command)
    precedes its STEP 2 (the fetch), and prepare precedes the judge in the
    recorded call order. Checking only the first would leave a judge that runs
    before any snapshot exists looking perfectly healthy.
    """
    out = one_batch_run(tmp_path)
    prepare = sole_prompt(out, "glossary:citation-prepare:0")
    approve_cmd = approve_cmd_for(check_cmd_from_wait(out, 0), 0, 0)
    lines = prepare.split("\n")

    approve_idx = [i for i, ln in enumerate(lines) if approve_cmd in ln]
    assert len(approve_idx) == 1, (
        f"expected exactly one approve-command line, found {len(approve_idx)}"
    )
    step1_idx, _ = step_line(prepare, 1)
    fetch_idx, _ = step_line(prepare, 2)
    assert approve_idx[0] == step1_idx, (
        "the approve command must BE prepare's STEP 1, not merely appear "
        f"somewhere in its prompt: approve at line {approve_idx[0]}, STEP 1 at "
        f"line {step1_idx}"
    )
    assert step1_idx < fetch_idx, (
        "the snapshot command must precede the fetch: approve at line "
        f"{step1_idx}, fetch at line {fetch_idx}"
    )

    # The cross-call half. Walked over the recorded order rather than looked up
    # by index so that it stays meaningful up the retry ladder, where several of
    # each label fire: at no point may a judge have run that its own attempt's
    # prepare had not already preceded.
    seen_prepare = 0
    for i, label in enumerate(labels_of(out)):
        if label == "glossary:citation-prepare:0":
            seen_prepare += 1
        elif label == "glossary:citation-review:0":
            assert seen_prepare > 0, (
                "a citation judge ran before any prepare had taken the snapshot "
                f"it reads (call {i} of {labels_of(out)})"
            )


def test_the_prepare_step_is_told_to_stop_when_the_snapshot_command_fails(tmp_path):
    """A fragment that no longer passes --check-batch was rewritten underneath
    us. The correct answer is a fresh attempt, never an audit of bytes that
    failed validation -- so the failure branch must stop the stage dead rather
    than carry on to a best-effort audit.

    ANCHOR MOVED IN 1.16.1: the approve command is run by PREPARE now, so this
    instruction lives in the prepare prompt and routes to EVIDENCE_FAILED rather
    than to a rejection verdict. (Named test_the_reviewer_is_told_to_reject_...
    until then; renamed because the agent that runs the command is no longer the
    one that reviews. The property is unchanged.) The control-flow consequence --
    that a failed prepare really does spend no judge call -- is asserted
    separately below, because prose alone cannot prove it.
    """
    out = one_batch_run(tmp_path)
    prepare = sole_prompt(out, "glossary:citation-prepare:0")
    lines = prepare.split("\n")

    fail_lines = [(i, ln) for i, ln in enumerate(lines) if "exits non-zero" in ln]
    assert len(fail_lines) == 1, (
        f"expected one snapshot-command failure instruction, found {len(fail_lines)}"
    )
    fail_idx, fail_line = fail_lines[0]
    step1_idx, _ = step_line(prepare, 1)
    step2_idx, _ = step_line(prepare, 2)
    assert step1_idx < fail_idx < step2_idx, (
        "the failure branch must be stated with STEP 1, before STEP 2 is even "
        f"introduced: STEP 1 at {step1_idx}, failure branch at {fail_idx}, "
        f"STEP 2 at {step2_idx}"
    )

    low = fail_line.lower()
    assert "stop" in low and "step 2" in low, (
        "a failed snapshot command must stop the stage before the fetch, not "
        f"proceed; the instruction was: {fail_line}"
    )
    assert "EVIDENCE_FAILED 0 ATTEMPT 0" in prepare, (
        "prepare must have an attempt-scoped failure sentinel to route that stop "
        f"into; its prompt was:\n{prepare}"
    )


# ---------------------------------------------------------------------------
# 2. The --check-batch contract prefix survived, and did not leak --approve-to.
# ---------------------------------------------------------------------------

def test_the_approve_command_is_the_check_batch_contract_plus_approve_to(tmp_path):
    """--approve-to is APPENDED, never interleaved. The dispatch prompt tells
    codex to re-run "exactly the command above", so the --check-batch prefix has
    to stay reproducible from the dispatch side, with --research-mode still ahead
    of --expect-source-forms-file.

    ANCHOR MOVED IN 1.16.1: the command is issued by PREPARE now. The comparison
    is unchanged -- still against the string the wait poll really emitted, never
    against a local transcription of it.
    """
    out = one_batch_run(tmp_path)
    prepare = sole_prompt(out, "glossary:citation-prepare:0")
    expected = approve_cmd_for(check_cmd_from_wait(out, 0), 0, 0)
    assert expected in prepare, (
        "the citation-prepare prompt must issue the wait poll's own --check-batch "
        f"command with --approve-to appended:\n  expected: {expected}\n"
        f"  prepare prompt was:\n{prepare}"
    )


# Round-8 sweep finding: PRECHECK and WAIT (chunk and re-check alike) are each
# told "do nothing else" beyond their one read-only check -- unpinned, in this
# file that already pins the --check-batch CONTRACT those same calls issue.
# PRESENCE-ONLY: this file's mocked agent() cannot simulate an LLM doing
# something extra with its bash tool, so this proves the instruction is still
# WRITTEN, not that it is OBEYED -- see glossary_citation_review.test.py's
# DISPATCH_NO_ACTION_CLAUSE pin for the same caveat spelled out at more length.
PRECHECK_NOTHING_ELSE_CLAUSE = (
    "do not create, modify, dispatch, or resolve any candidates yourself"
)
WAIT_NOTHING_ELSE_CLAUSE = (
    "do not touch any files, and do not resolve any candidates yourself"
)


def test_precheck_and_wait_are_told_to_do_nothing_beyond_their_own_check(tmp_path):
    """PRECHECK holds a bash tool to run its one read-only --check-batch probe;
    WAIT holds one to run its bounded poll (and the re-check that backs it).
    Neither is restricted by any tool-level sandbox -- confirmed in the round-8
    sweep that NO agent() call anywhere in this plugin's templates carries a
    tool-restriction option -- so the prompt's own "do nothing else" sentence
    is the ONLY thing standing between "ran the one suggested command" and
    "did whatever else its bash tool allows", for both of these mechanical,
    supposedly read-only steps.

    All three call sites share one property (the SAME sentence, verbatim, at
    the wait chunk and the wait re-check) but need two different runs: the
    chunk fires under one_batch_run's default; the re-check only fires when
    the chunk budget is exhausted (see pending_wait_run(), used above by this
    file's own --check-batch roster test for the identical reason)."""
    out = one_batch_run(tmp_path)
    precheck = sole_prompt(out, "glossary:precheck:0")
    assert PRECHECK_NOTHING_ELSE_CLAUSE in precheck, (
        "the precheck prompt must forbid the agent from doing anything beyond "
        f"its one read-only check; prompt was:\n{precheck}"
    )
    for prompt in prompts_for(out, "glossary:wait:0"):
        assert WAIT_NOTHING_ELSE_CLAUSE in prompt, (
            "every wait chunk prompt must forbid the agent from touching "
            f"files or resolving candidates itself; prompt was:\n{prompt}"
        )

    recheck_out = pending_wait_run(tmp_path)
    recheck_prompts = prompts_for(recheck_out, "glossary:wait-recheck:0")
    assert recheck_prompts, "pending_wait_run() must reach the wait re-check"
    for prompt in recheck_prompts:
        assert WAIT_NOTHING_ELSE_CLAUSE in prompt, (
            "the wait re-check prompt must forbid the agent from touching "
            f"files or resolving candidates itself; prompt was:\n{prompt}"
        )


@pytest.mark.parametrize("label", [
    "glossary:precheck:0", "glossary:dispatch:0", "glossary:wait:0",
    "glossary:wait-recheck:0", "glossary:citation-review:0",
])
def test_only_the_prepare_call_ever_issues_the_approve_command(tmp_path, label):
    """PREPARE is the one call in the file that may snapshot, and the reasons
    differ per label rather than being one rule repeated.

    The four --check-batch sites (precheck, dispatch self-check, wait chunk
    poll, wait re-check -- the last added in 1.16.2, #352) must issue
    checkBatchCmd() character-identically, so none of them may acquire the
    flag: a precheck, wait chunk poll or re-check that snapshotted would write
    an approved copy of bytes nobody has reviewed, and a dispatch self-check
    that did it would let the producer approve its own output.

    The JUDGE is here for a different reason and was added in 1.16.1 (the test was
    named test_no_plain_check_batch_site_ever_issues_approve_to when it covered
    only the first three). It is not a --check-batch site at all; what it must not
    do is re-take the snapshot AFTER the evidence was retrieved from the first
    one, which would leave the audited bytes and the fetched-from bytes as two
    different objects -- the very split this file exists to prevent.

    The re-check needs a different run from the other four labels: the default
    fixture's wait answers READY on its very first chunk, so the re-check never
    renders a prompt at all under it -- see pending_wait_run()'s docstring for
    why a plan has to force the chunk budget to exhaust before this label ever
    fires.
    """
    out = (
        pending_wait_run(tmp_path) if label == "glossary:wait-recheck:0"
        else one_batch_run(tmp_path)
    )
    prompts = prompts_for(out, label)
    assert prompts, f"no prompt recorded for {label}"
    for prompt in prompts:
        assert "--approve-to" not in prompt, (
            f"{label} must not carry --approve-to -- only the citation prepare "
            "call snapshots"
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


# Round-8 sweep finding: the verify call is what the template's own comments
# elsewhere call "what this run actually trusts" (glossaryVerifyPrompt() is
# disk-independent and re-derives its own verdict rather than trusting the
# merge call above), so a verify call that lied about its own result would go
# completely unnoticed -- nothing re-runs --verify-merged independently.
GLOSSARY_VERIFY_NO_JUDGE_CLAUSE = "do not judge the comparison yourself"
GLOSSARY_VERIFY_NO_ALTER_CLAUSE = (
    "Do not add, omit, or alter any value the command printed"
)


def test_verify_prompt_forbids_judging_or_altering_the_command_result(tmp_path):
    """The verify call's whole safety property is that it RELAYS
    --verify-merged's own output rather than deciding anything itself --
    the template's own comment calls --verify-merged "never trusting the
    merge call above's own claim", and that guarantee is worthless if the
    RELAY step is then free to trust itself instead.

    Two things are pinned, at two different strengths:

    1. PRESENCE-ONLY (cannot be made behavioural with this mocked harness):
       both clause texts must be in the rendered prompt. The mock agent()
       here returns a canned Python dict for this label -- it never runs an
       LLM that could ignore or obey these words -- so this half proves the
       instruction is still WRITTEN, not that it is OBEYED.
    2. STRUCTURAL / behavioural: CANON_VERIFY_SCHEMA's own
       additionalProperties must be false. This is the one part of "do not
       add ... any value" that IS independently enforced beyond the prompt
       sentence -- the real Workflow engine's schema validation would reject
       a reply carrying an extra field, regardless of what the prompt says.
       It does NOT cover "omit" or "alter": a schema-conformant reply that
       reports verified:true when the command actually printed false is
       accepted by the schema and by isVerifiedResult() alike, exactly as
       measured in the round-8 sweep -- for THAT half, the prompt clause
       above is still the only defense that exists anywhere in this system.
    """
    out = one_batch_run(tmp_path)
    verify = prompts_for(out, "glossary:verify")[0]

    assert GLOSSARY_VERIFY_NO_JUDGE_CLAUSE in verify, (
        "the verify prompt must tell the agent not to judge the comparison "
        f"itself; prompt was:\n{verify}"
    )
    assert GLOSSARY_VERIFY_NO_ALTER_CLAUSE in verify, (
        "the verify prompt must forbid adding, omitting, or altering any "
        f"value the command printed; prompt was:\n{verify}"
    )

    verify_calls = [c for c in out["calls"] if c["label"] == "glossary:verify"]
    assert len(verify_calls) == 1, (
        f"expected exactly one glossary:verify call, got {len(verify_calls)}"
    )
    assert verify_calls[0]["hasSchema"] is True, (
        "the glossary:verify call must be schema-carrying at all -- without a "
        "schema, not even the 'add an extra field' half has any structural "
        "backstop"
    )
    assert verify_calls[0]["schemaAdditionalProperties"] is False, (
        "CANON_VERIFY_SCHEMA must set additionalProperties: false -- this is "
        "the actual code-level enforcement of the 'do not add' half of the "
        "prompt clause above, independent of whether the prompt sentence "
        f"survives; got {verify_calls[0]['schemaAdditionalProperties']!r}"
    )


def test_verify_result_trust_rests_on_shape_alone_not_independent_corroboration(tmp_path):
    """The STRONG form of the property the test above can only pin weakly.

    Not "the schema forbids an extra field" (already pinned above) but: does
    anything downstream treat a shape-valid reply as evidence the command
    was actually run, or compare it against what the command itself
    printed? Answered two ways, deliberately different in kind:

    1. SOURCE-STRUCTURAL: isVerifiedResult() -- the one function standing
       between the agent's reply and merged:true -- is read directly out of
       the real template file and asserted to contain no subprocess call, no
       second agent() call, and no reference to canon_validate.py. It is a
       pure shape/value check over the reply OBJECT, nothing else. If a
       future change adds real corroboration (re-running --verify-merged,
       hashing something, anything that inspects reality instead of the
       reply's shape), this is the assertion that would force a conscious
       update here rather than staying silently true.
    2. BEHAVIOURAL, over this file's own fixture: a MOCKED "glossary:verify"
       reply that never invokes any subprocess at all -- one_batch_run's
       canned `{ verified: true }` -- still makes the run report
       merged:true. This is not new behaviour created by this test; it is
       the precondition every other fixture in this file already depends on,
       made an explicit, named assertion instead of an implicit one nobody
       has to notice.
    """
    template_source = GLOSSARY_TEMPLATE.read_text(encoding="utf-8")
    m = re.search(r"function isVerifiedResult\(v\) \{(.*?)\n\}", template_source, re.DOTALL)
    assert m, (
        "isVerifiedResult() not found in glossary-pass-wf.template.js -- has "
        "it been renamed or restructured? This test's whole premise is that "
        "function's own body."
    )
    body = m.group(1)
    for marker in ("execFileSync", "spawnSync", "require(", "subprocess", "agent(", "canon_validate"):
        assert marker not in body, (
            f"isVerifiedResult() now contains {marker!r} -- it used to be a "
            "pure shape/value check over the reply object with no "
            "independent corroboration of anything; if that changed on "
            "purpose, this assertion (and the docstring above citing it as "
            f"the sole trust point) needs to be revisited, not silenced. "
            f"Body was:\n{body}"
        )

    out = one_batch_run(tmp_path)
    assert out["result"]["merged"] is True, (
        "a schema-valid, canned verify reply that never invoked the real "
        "--verify-merged command is still trusted -- confirms there is no "
        f"independent corroboration step; result was {out['result']}"
    )


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
    """Every attempt snapshots its own fragment to its own path, so a later
    attempt can never re-approve an earlier attempt's snapshot.

    ANCHOR MOVED IN 1.16.1: the approve command is prepare's, so the per-attempt
    scoping is asserted over the PREPARE prompts. The judge is checked in the
    same loop rather than dropped -- it is still the call that must not be
    handed a neighbouring attempt's snapshot or evidence, and asserting only the
    prepare side would leave a judge reading attempt 0's evidence while attempt 2
    merges.
    """
    out = one_batch_run(tmp_path, plan={"0": {"reviews": [
        "bad source\nCITATIONS_REJECTED 0 ATTEMPT 0",
        "bad source again\nCITATIONS_REJECTED 0 ATTEMPT 1",
        "CITATIONS_OK 0 ATTEMPT 2",
    ]}})
    prepares = prompts_for(out, "glossary:citation-prepare:0")
    reviews = prompts_for(out, "glossary:citation-review:0")
    assert len(prepares) == 3, f"expected three prepare calls, got {len(prepares)}"
    assert len(reviews) == 3, f"expected three review calls, got {len(reviews)}"

    for attempt, prompt in enumerate(prepares):
        expected = approve_cmd_for(check_cmd_from_wait(out, 0, attempt), 0, attempt)
        assert expected in prompt, (
            f"attempt {attempt}'s prepare must snapshot that attempt's own "
            f"fragment to {approved_path(0, attempt)}:\n  expected: {expected}"
        )
        assert evidence_dir(0, attempt) in prompt, (
            f"attempt {attempt}'s prepare must retrieve into its own evidence "
            f"directory {evidence_dir(0, attempt)}"
        )

    for attempt, prompt in enumerate(reviews):
        assert approved_path(0, attempt) in prompt, (
            f"attempt {attempt}'s judge must audit that attempt's own snapshot "
            f"{approved_path(0, attempt)}"
        )
        assert evidence_dir(0, attempt) in prompt, (
            f"attempt {attempt}'s judge must read that attempt's own evidence "
            f"directory {evidence_dir(0, attempt)}"
        )

    for kind, prompts in (("prepare", prepares), ("judge", reviews)):
        for attempt, prompt in enumerate(prompts):
            for other in range(3):
                if other == attempt:
                    continue
                assert approved_path(0, other) not in prompt, (
                    f"attempt {attempt}'s {kind} names another attempt's "
                    f"snapshot {approved_path(0, other)}"
                )
                assert evidence_dir(0, other) not in prompt, (
                    f"attempt {attempt}'s {kind} names another attempt's "
                    f"evidence directory {evidence_dir(0, other)}"
                )


def test_a_failed_prepare_spends_no_judge_call_and_never_merges_its_attempt(tmp_path):
    """The rejection cause 1.16.1 added, and the one no prose assertion reaches.

    A prepare that reports EVIDENCE_FAILED means there is either no trustworthy
    snapshot or no evidence, so there is nothing to judge: spending the judge call
    anyway would ask an agent to audit files that may not exist. It joins the same
    retry ladder a citation rejection does, which makes this the same invariant as
    "a rejected attempt's snapshot never reaches the merge" -- through the second
    door, and the door that did not exist before the split.
    """
    out = one_batch_run(tmp_path, plan={"0": {"prepares": [
        "step 1 exited 2: the fragment failed its coverage check\n"
        "EVIDENCE_FAILED 0 ATTEMPT 0",
    ]}})
    prepares = prompts_for(out, "glossary:citation-prepare:0")
    reviews = prompts_for(out, "glossary:citation-review:0")
    assert len(prepares) == 2, f"expected a second attempt to be prepared, got {len(prepares)}"
    assert len(reviews) == 1, (
        "a failed prepare must spend no judge call -- exactly one judge call "
        f"should have run, for attempt 1; got {len(reviews)}"
    )
    assert approved_path(0, 1) in reviews[0], (
        "the one judge call must be attempt 1's, the attempt whose evidence was "
        "actually prepared"
    )

    merge = prompts_for(out, "glossary:merge")[0]
    assert approved_path(0, 1) in merge
    assert approved_path(0, 0) not in merge, (
        f"the unprepared attempt's snapshot {approved_path(0, 0)} must never be "
        "handed to the merge -- nothing ever audited it"
    )
    assert out["result"]["batches"][0]["mergePath"] == approved_path(0, 1)


def test_offline_merge_consumes_the_attempt_path_and_names_no_snapshot(tmp_path):
    """The explicit live/offline branch, not a global rename.

    Offline forbids basis:"established" outright, so neither half of the citation
    stage runs and nothing ever issues an approve command. A merge that always
    consumed approved_* paths would name a file that cannot exist and every
    offline run would die at the merge on a missing file.
    """
    out = one_batch_run(tmp_path, research_mode="offline")
    # BOTH halves of the split stage, since 1.16.1. Checking only the judge would
    # miss an offline run that still ran the prepare -- which would take a
    # snapshot and, worse, hit the network through the fetcher, in the one mode
    # whose whole point is that it makes no external claim at all.
    for label in ("glossary:citation-prepare:0", "glossary:citation-review:0"):
        assert label not in labels_of(out), f"offline must spend no {label} call"
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
    snapshot is taken inside the prepare call's own turn rather than in the wait
    step: a wait-side snapshot would be skipped on every resumed batch, and a
    resumed, never-reviewed fragment is the case this whole stage exists for.

    ANCHOR MOVED IN 1.16.1: the snapshot is prepare's STEP 1, so PREPARE is what
    has to sit at the convergence point of the two entry points. The judge is
    asserted alongside it because the resumed batch must be REVIEWED, not merely
    snapshotted -- a prepare that ran while the judge was skipped would satisfy
    the snapshot half of this test and still merge unreviewed bytes.
    """
    out = one_batch_run(tmp_path, plan={"0": {"precheck": "PRESENT 0"}})
    order = labels_of(out)
    assert "glossary:dispatch:0" not in order, "fixture did not take the resume-skip path"
    assert "glossary:wait:0" not in order, "fixture did not take the resume-skip path"

    prepare = sole_prompt(out, "glossary:citation-prepare:0")
    expected = approve_cmd_for(check_cmd_from_precheck(out, 0), 0, 0)
    assert expected in prepare, (
        "a resume-skipped batch must still snapshot its own fragment:\n"
        f"  expected: {expected}"
    )
    review = prompts_for(out, "glossary:citation-review:0")
    assert len(review) == 1, (
        "a resume-skipped batch must still be judged, exactly once, over the "
        f"snapshot its prepare took; got {len(review)} judge call(s)"
    )
    assert approved_path(0, 0) in review[0]
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
