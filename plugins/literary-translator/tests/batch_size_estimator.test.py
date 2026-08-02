"""tests/batch_size_estimator.test.py

NAMING NOTE: the build-spec document's own enumeration of test files trails
off mid-sentence as "Test file `tests/`" for this exact mechanism (see
`skills/literary-translator/references/orchestration-and-batching.md`'s
"`batch_agent_cap` -- the worst-case preflight estimator" section, and the
matching derivation inline in
`skills/literary-translator/assets/templates/mass-translate-wf.template.js`
just above its `estimatedCalls` line). This filename is inferred to fit the
`tests/` directory's own naming convention (one test file per mechanism,
`snake_case.test.py`), not copied verbatim from the source document.

Targets: the `batch_agent_cap` preflight estimator inside
`mass-translate-wf.template.js`, i.e. exactly this block (see the template's
own "batch_agent_cap preflight" comment, right above the `pipeline()` call),
as of the 1.2.0 reliability build (CONTRACT-1.2.0-reliability.md sec7 / the
approved plan's "Estimator -- pinned" note). This is the REAL, landed shape
(verified directly against the shipped template, not merely the contract):

    const estimatedCalls = 1 + SEGS.length * (10 + 7 * MAXFIX);
    if (estimatedCalls > BATCH_AGENT_CAP) {
      log(...);
      return { converged: [], failed: [], reason: "batch-too-large",
               estimatedCalls: estimatedCalls, cap: BATCH_AGENT_CAP };
    }

This REPLACES the pre-1.2.0 `1 + N*(6 + 3*MAXFIX)` formula: the review step
was restructured (`getVerifiedReview`) from a single `review:*`/
`artifact-check:*` call pair per round into a four-call review POINT
(`review-dispatch:*` -- since #198 a plain-Claude DRIVE of the detached
`codex_job.py` review job, not a codex fire-and-forget agent call, but still
exactly ONE call that returns immediately -- `review-wait:*` bounded poll of
`review_ready.py`, `review-read:*`, `artifact-check:*`) with a single SHARED
retry budget covering the (read, check) pair together -- never two
independent retries. The #198 driver-dispatch reshape is call-count-neutral:
the drive replaces the old dispatch 1:1 and the wait stays 1 call, so the
`10 + 7*MAXFIX` per-segment term is UNCHANGED. The batch-level term dropped
from N-dependent housekeeping to exactly **1** (the single `merge-ledger`/
`mergeLedgerPrompt` call) now that `{{RUN_ID}}`-scoped `dispatch_token`s make
every driver-promoted artifact fresh-by-construction, removing the old batch
pre-clean call entirely.

Per-segment worst case, re-derived from the real `getVerifiedReview`/
`runRound`/`reviewFixLoop` functions (mirrored in the template's own comment
directly above `estimatedCalls`):

  - every segment, unconditionally: 3 fixed calls (`ledger:in_progress:*`
    write, `translate:*` dispatch, `wait:*` translate-readiness poll).
  - a "review point" -- one call to `getVerifiedReview` -- is:
    `review-dispatch:*` (1, since #198 a plain-Claude DRIVE of the detached
    codex_job.py review job -- its return is parsed only to capture DISP)
    + `review-wait:*` (1, bounded poll; a non-READY result ends the point
    immediately as `blocked/review-timeout`, no read/check ever attempted)
    + `readAndCheck(isRetry=false)`: `review-read:*` (1); if that reads back
    falsy, `artifact-check:*` is **never called** for that attempt
    (`readAndCheck`'s own `if (!rev) return {rev:null, art:null}`
    short-circuit) -- otherwise `artifact-check:*` (1) follows immediately.
    If the first attempt's check matched (`artifactCheckMatched`), the point
    ends there (happy path, 4 calls total: dispatch+wait+read+check). Else
    ONE shared retry of `readAndCheck(isRetry=true)` fires (same
    short-circuit rules): if the retried read is STILL falsy ->
    `blocked/review-null` (no second check call, ever); if the retried
    read succeeds but its check still doesn't match -> `blocked/review-
    artifact-mismatch`; if the retried check DOES match -> the point
    succeeds with the retried verdict. The TRUE worst case -- 6 calls,
    dispatch+wait+read+check+read+check -- only happens when the FIRST
    attempt's read succeeds but its check reports `match:false` (so both
    read and check fire on both attempts); this is the case this file's
    worst-case fixtures force.
  - each of the `max_fix_rounds` NORMAL rounds (`runRound(seg, round,
    isFinal=false)`, every round except the final confirming one) = review
    point (6, worst case) + fix (1, `callFix`) = **7**, provided the
    review point's resulting verdict is NOT `clean && coverage_ok` (a clean
    verdict converges the segment immediately at that round instead,
    cheaper than the worst case and not what these fixtures exercise).
  - the FINAL confirming round (`runRound(seg, MAXFIX+1, isFinal=true)`) is
    one more review point (6, worst case) with NO fix call after it,
    whether it comes back clean (`converged`, via `runRound`'s own
    `ledger:converged:*` write) or not (`non_converged`/`cap`, via
    `reviewFixLoop`'s trailing `ledger:cap:*` write once the for-loop
    exhausts) -- both are the same branch at the same cost, per the
    template's own derivation.
  - +1 terminal per-segment ledger write (`ledger:converged:*` /
    `ledger:blocked:{reason}:*` / `ledger:cap:*` / `ledger:timeout:*`,
    exactly one of these fires per segment).
  - per-segment total: 3 + 7*max_fix_rounds + 6 + 1 == **10 + 7*max_fix_rounds**,
    exactly the `10 + 7*MAXFIX` term inside `estimatedCalls`.
  - batch-level: exactly **1** (`merge-ledger`, colon-free).

Blocked-branch terminating sub-cases (same taxonomy as the pre-1.2.0 file,
re-costed for the new review-point shape and the real `readAndCheck`
short-circuit above -- these fixtures do NOT need to hit the estimator's own
worst-case ceiling per round, only be internally consistent between the PLAN
queues fed to the mock and the assertions below; each fixture's `max_fix_rounds
- 1` completed prior rounds are deliberately modeled at the SAME worst-case-
recovered shape (7 each) the estimator itself assumes, so `test_
review_artifact_mismatch_actual_calls_never_exceed_formula_bound` below
exercises a genuinely maximal blocked branch, not an artificially cheap one):

1.3.6 CHANGE (#131 -- transient/mechanical failures become recoverable, not
terminal): every one of `runRound`'s `getVerifiedReview`-blocked reasons
(`review-null`, `review-artifact-mismatch`, `review-timeout`, and -- #133 --
`review-fabricated-loc`) no longer records a terminal ledger write at all --
the in_progress fragment stays the durable record and select_segments.py
classifies the segment recoverable. Each of those sub-cases below therefore
costs exactly ONE FEWER real agent() call than the pre-1.3.6 file recorded
(no `ledger:blocked:*:*` write). `draft-missing` is UNCHANGED in this
respect (a genuinely absent/invalid draft after translate reported READY
stays terminal, still writes the ledger) but now costs one MORE call than
before -- the new `draft-probe:*` call `runRound`'s fix-call branch makes to
tell a genuine missing draft apart from a merely-transient fix-call failure
on a present, valid draft (the NEW `fix-call-failed` sub-case below).

  - `review-null`: both the first AND the retried read come back falsy --
    `readAndCheck`'s short-circuit means `artifact-check:*` is NEVER called
    for this round -- 4 calls total (dispatch+wait+read+read), no fix, NO
    ledger write (#131 facet B -- recoverable).
  - `review-artifact-mismatch`: the first attempt's read succeeds but its
    check reports `match:false`; the retried read ALSO succeeds but its
    check STILL mismatches -- the true 6-call worst case, no fix, NO ledger
    write (#131 facet B -- recoverable).
  - `review-fabricated-loc` (NEW, #133): the review point succeeds WITHOUT a
    retry (happy path -- first attempt's read+check matches), but the
    verdict's one finding carries a bare, colonless infra-sentinel `loc`
    (e.g. `TASK`) -- `findingsAuthentic()` rejects it right there, before any
    fix ever dispatches. 4 calls total (dispatch+wait+read+check), no fix,
    NO ledger write (#131 facet B makes this reason recoverable too, for
    free -- no extra wiring needed).
  - `draft-missing`: the review point succeeds WITHOUT a retry (happy path,
    4 calls), a fix is dispatched, and the fix call itself reports the
    draft went missing (`fx.indexOf("DRAFT_MISSING") !== -1"`); the new
    `draftPresentAndValid` probe then confirms the draft is genuinely
    absent/invalid (`present:false`) -- 6 calls total
    (dispatch+wait+read+check+fix+probe), and this sub-case STILL writes
    the terminal ledger entry (blocked/draft-missing -> human_escalation,
    a real anomaly worth human attention, unchanged from before).
  - `fix-call-failed` (NEW, #131 facet A): the review point succeeds WITHOUT
    a retry exactly like `draft-missing` above, and the fix call ALSO comes
    back falsy/DRAFT_MISSING -- but this time the probe confirms the draft
    IS present and valid (`present:true`): a transient fix-call failure
    (agent died / output-token ceiling / classifier block), not a genuine
    missing draft. 6 calls total (dispatch+wait+read+check+fix+probe), NO
    ledger write (recoverable, same as the other #131 facets).
  - `review-timeout` (the review restructure gives review its own bounded
    poll, `review-wait:*`, independent of translate's own `wait:*`):
    `review-wait:*` returns non-READY on the very first poll -- 2 calls
    (dispatch+wait), the read/check/fix machinery is never reached, NO
    ledger write (#131 facet B -- recoverable).
  - `timeout` (translate's own, #131 facet C): the translator never delivers
    READY -- `wait:*`'s own `ready.indexOf("READY") === -1` check fires on
    the very first wait call, before any review call is ever made. Cost is
    3 (in_progress ledger + translate + wait), independent of
    `max_fix_rounds` -- NO terminal ledger write anymore (was 4, with a
    `ledger:timeout:*` write, pre-1.3.6; the segment now stays in_progress
    and recoverable instead of writing non_converged/translate-timeout).

This file does not re-implement any of that arithmetic and trust its own
reimplementation -- it extracts the REAL, substituted
`mass-translate-wf.template.js` source, wraps it exactly the way the
Workflow tool that actually executes this file must (self-contained, uses
only the `agent()`/`pipeline()`/`log()`/`args` globals the Workflow tool
supplies; confirmed a plain `node --check` on the raw file fails with
"Illegal return statement"), then drives it with Node.js under a scripted
mock `agent()`/`pipeline()` that counts every real call made and lets each
fixture below force one specific branch. Skipped entirely if Node.js is not
on PATH -- this plugin has no hard Node.js dependency.

Fixtures, one per branch:
  1. `test_estimator_boundary_exactly_at_cap_permits_dispatch_and_converges`
     -- a batch sized so `estimatedCalls` lands EXACTLY at `batch_agent_cap`:
     the gate must NOT trip (`>`, not `>=`), `pipeline()` must actually run,
     and the real total agent-call count made while every segment converges
     on its worst-case-within-branch path (every round's review point forced
     through the full 6-call shared retry) must equal the formula's own
     estimate exactly.
  2. `test_estimator_one_below_boundary_blocks_dispatch_entirely` -- the
     same configuration with `batch_agent_cap` one less: the gate MUST
     trip, `pipeline()` must never run, and zero real agent calls happen.
  3/4/5. One fixture per blocked terminating sub-case: `review-null`,
     `draft-missing`, `review-artifact-mismatch`.
  6. The timeout branch (translate's own).
  7. A dedicated case re-asserting that the `review-artifact-mismatch`
     segment's ACTUAL call count -- built from worst-case-recovered prior
     rounds, matching the estimator's own per-round assumption -- never
     exceeds the per-segment bound (`10 + 7*max_fix_rounds`) the estimator
     itself relies on.
  8. A parametrized, cheap (no `pipeline()` execution at all -- the gate
     trips before it) check that the real script's own `estimatedCalls`
     matches the closed form `1 + N*(10 + 7*maxFixRounds)` across several
     `(segment_count, max_fix_rounds)` pairs.
  9. A bonus (not separately required by the spec, included because it is
     nearly free given the machinery above): the SAME per-segment call
     total applies when the final confirming round ends non-convergent
     rather than convergent -- both are "the cap/converged branch" in the
     formula's own derivation, at the same cost.
  10. NEW `test_blocked_review_timeout_terminating_subcase` -- the review
     restructure's own new terminating sub-case (review's bounded poll,
     independent of translate's), costing exactly 2 calls.
  11. NEW `test_shared_retry_recovers_mid_loop_and_matches_exact_count` -- a
     narrower companion to fixture 1 above: rather than forcing EVERY round
     through the shared-retry worst case, this forces it in exactly ONE
     mid-loop round (the last normal round) while every other round --
     including the final confirming one -- takes the cheap happy path, and
     asserts the resulting total against a hand-computed (not formula-
     derived) expectation. This isolates the shared-retry mechanic itself
     from the estimator's own worst-case ceiling, proving the harness's
     queue machinery counts a PARTIAL-worst-case run correctly too, per the
     CONTRACT's explicit "force a mid-loop read/check->retry->fix max round
     and assert EXACT equality" requirement.
  12. NEW (1.3.6, #131 facet A) `test_blocked_fix_call_failed_terminating_
     subcase` -- the SAME falsy/DRAFT_MISSING `fx` as the `draft-missing`
     sub-case (3/4/5 above), but the new `draftPresentAndValid` probe
     reports the draft IS present and valid: ends `fix-call-failed`, no fix
     forensics needed, and -- unlike `draft-missing` -- NO terminal ledger
     write (recoverable).
  13. NEW (1.3.6, #133) `test_blocked_review_fabricated_loc_terminating_
     subcase` -- a schema-valid, artifact-matched verdict whose one finding
     carries a bare, colonless infra-sentinel `loc`: `findingsAuthentic()`
     rejects it before any fix ever dispatches, ending the segment
     `review-fabricated-loc` with no terminal ledger write.
  14. NEW (1.3.6, #131 facet A review-fix pass -- MAJOR correctness fix)
     `test_blocked_fix_call_failed_probe_itself_fails_terminating_subcase`
     -- the SAME falsy/DRAFT_MISSING `fx` as fixture 12, but this time the
     draft-probe AGENT CALL ITSELF fails (mock returns `null`, simulating a
     correlated outage on both the fix call and the probe call). Locks that
     `draftPresentAndValid` treats a `null` probe result as INCONCLUSIVE,
     never as proof of absence -- before this fix, a null probe result
     collapsed to `false` and wrongly landed on terminal `draft-missing`.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
TEMPLATES_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets" / "templates"
MASS_TRANSLATE_TEMPLATE = TEMPLATES_DIR / "mass-translate-wf.template.js"

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(
    NODE is None,
    reason="node not found on PATH; this test needs Node.js to actually execute "
    "the workflow template's real preflight/dispatch logic (no hard Node.js "
    "dependency for this plugin otherwise)",
)

# ---------------------------------------------------------------------------
# Fixture profile values -- plain resolved values, the same shape
# tests/workflow_template_instantiation.test.py's own fixture profile uses.
# None of these affect the estimator's arithmetic; they only need to be
# valid strings so the prompt-builder functions the real script also calls
# don't choke (this test's mock agent() never reads prompt text, only
# opts.label, so their exact content is otherwise irrelevant here).
# ---------------------------------------------------------------------------
FIXTURE_DURABLE_ROOT = "/fixture/project/durable_root"
FIXTURE_SOURCE_LANG = "fr"
FIXTURE_TARGET_LANG = "ru"
FIXTURE_VERSE_POLICY_INSTRUCTION_BLOCK = "Render every verse literally, line by line."


def instantiate_mass_translate(
    *,
    max_fix_rounds: int,
    batch_agent_cap: int,
    max_codex_jobs_per_batch: int = 1_000_000_000,
    durable_root: str = FIXTURE_DURABLE_ROOT,
    source_lang: str = FIXTURE_SOURCE_LANG,
    target_lang: str = FIXTURE_TARGET_LANG,
    verse_policy_instruction_block: str = FIXTURE_VERSE_POLICY_INSTRUCTION_BLOCK,
) -> str:
    """Re-implements the exact one-time substitution contract the template's
    own header comment documents (same contract
    tests/workflow_template_instantiation.test.py's instantiate helper
    implements -- duplicated here, not imported, so this file stays
    self-contained like every other sibling test file in this directory).
    Deliberately does NOT substitute {{RUN_ID}} -- this file's mock never
    inspects prompt text (only opts.label), so RUN_ID's exact value is
    irrelevant to the call-counting this file cares about; it is left
    unresolved on purpose and simply never asserted against.

    #409 stage 0 -- max_codex_jobs_per_batch defaults to a value no fixture
    in this file could ever reach, so the NEW, independent codex-jobs
    preflight (which runs and can return BEFORE the batch_agent_cap gate
    this whole file exists to exercise) never trips here and never shadows
    what every fixture below is actually testing."""
    text = MASS_TRANSLATE_TEMPLATE.read_text(encoding="utf-8")
    text = text.replace("{{DURABLE_ROOT}}", durable_root)
    text = text.replace("{{RUN_ID}}", "fixture-run-id")
    text = text.replace("{{SOURCE_LANG}}", source_lang)
    text = text.replace("{{TARGET_LANG}}", target_lang)
    text = text.replace("{{MAX_FIX_ROUNDS}}", str(int(max_fix_rounds)))
    text = text.replace("{{BATCH_AGENT_CAP}}", str(int(batch_agent_cap)))
    text = text.replace("{{MAX_CODEX_JOBS_PER_BATCH}}", str(int(max_codex_jobs_per_batch)))
    escaped_verse_block = json.dumps(verse_policy_instruction_block)[1:-1]
    text = text.replace("{{VERSE_POLICY_INSTRUCTION_BLOCK}}", escaped_verse_block)
    # #198 -- CODEX_COMPANION_PATH_JSON: a strict json.dumps JS string literal
    # (quotes included; the token sits OUTSIDE quotes in the template). This
    # test's mock never launches the driver, so the exact value is irrelevant
    # to the call-counting here -- it only needs to resolve so the "{{ not in
    # text" assertion below (no unresolved token) still holds.
    text = text.replace("{{CODEX_COMPANION_PATH_JSON}}", json.dumps("/fixture/codex/codex-companion.mjs"))
    # #197 -- engine.effort/engine.model. Neither is inspected by this file's
    # call-counting assertions; they only need to resolve.
    text = text.replace("{{EFFORT}}", "high")
    # 1.16.1 (#347): empty = fetch_citation.py's shipped default list.
    text = text.replace("{{CITATION_CONTENT_TYPES}}", "")
    text = text.replace("{{MODEL}}", "")
    assert "{{" not in text, "fixture instantiation left an unresolved token -- fix the fixture, not the assertion below"
    return text


def _wrap_for_execution(js_source: str) -> str:
    """Wraps the real, substituted template body in exactly the shape a
    Workflow-tool harness must supply: an async function whose parameters
    ARE the `agent`/`pipeline`/`log`/`args` globals the file's header
    comment documents as its only external dependencies. This is not a
    stylistic choice -- the raw file is not valid standalone JS (confirmed:
    `node --input-type=module --check` on it fails with "Illegal return
    statement", since it both `export`s `meta` and `return`s at its own top
    level, which only typechecks inside a wrapping function body)."""
    assert js_source.count("export const meta") == 1, (
        "expected exactly one 'export const meta' declaration to strip -- "
        "the template's export contract may have changed"
    )
    body = js_source.replace("export const meta", "const meta", 1)
    return "async function __workflowMain__(agent, pipeline, log, args) {\n" + body + "\n}\n"


# ---------------------------------------------------------------------------
# Node harness: mocks `agent()`/`pipeline()`/`log()`, records every real
# agent() call made (label + metadata), and lets a Python-supplied PLAN
# script exactly what each segment's calls should return, in the order the
# real script's own functions (translateStage, reviewFixLoop, runRound,
# getVerifiedReview, readAndCheck, recordLedgerCall, ...) actually issue
# them -- this file never reimplements THEIR logic, only the ambient
# globals they call.
#
# Per-segment PLAN shape: {
#   "wait": <translate's own wait:* response, e.g. "READY seg"/"PENDING seg" --
#             re-read by EVERY chunk call of that wait, see #348 below>,
#   "waitRecheck": <optional wait-recheck:* response; default "PENDING <seg>">,
#   "reviewWaits": [<one review-wait:* response per review POINT, in round
#                     order -- NOT per retry and NOT per chunk; the shared
#                     retry re-runs only read+check, never dispatch/wait>, ...],
#   "reviewWaitRecheck": <optional review-wait-recheck:* response, used at
#                          EVERY review point; default "PENDING <seg>">,
#   "reviews": [<one review-read:* response per read call, in call order --
#                a round with a shared retry contributes TWO entries here>,
#               ...],
#   "artifactChecks": [<one artifact-check:* response per check call, in
#                        call order -- omitted entirely for a read that came
#                        back falsy, per readAndCheck's own short-circuit>,
#                      ...],
#   "fixes": [<one fix:* response per non-final round that reaches a fix
#               call, in round order>, ...],
# }
#
# #348 -- A WAIT IS NO LONGER ONE agent() CALL, and that changes what this
# harness must do, not just what it counts. Each wait site makes up to
# WAIT_CHUNKS bounded chunk calls REUSING its existing label ("wait:<seg>" /
# "review-wait:<seg>:r<round>"), then ONE authoritative non-polling re-check
# under a new label containing "-recheck:".
#
# THE TRAP, spelled out because it fails SILENTLY: `reviewWaits` is a queue
# holding one entry per review POINT. Chunk calls repeat the point's label, so
# a naive shift-per-call would let a single PENDING review point eat the entries
# meant for every LATER point -- every subsequent verdict shifts by one, the
# fixture still "passes", and it exercises a flow it never meant to. Chunk
# answers are therefore MEMOIZED BY LABEL: the queue is shifted once per review
# point (the label carries :r<round>, so each point has its own), and every
# further chunk of that point re-reads the same answer. translate's own `wait`
# was never a queue and needs no memo -- every chunk simply re-reads it.
#
# A re-check the PLAN does not script answers "PENDING <seg>": the fail-safe
# default, so a fixture that forgets it can only make a wait FAIL, never
# falsely converge.
# ---------------------------------------------------------------------------
HARNESS_TEMPLATE = r"""
'use strict';

__WRAPPED_SOURCE__

const PLAN = __PLAN_JSON__;
const SEGS_ARGS = __SEGS_JSON__;
const callsLog = [];
const logLines = [];
let pipelineCalled = false;

const queues = {};
for (const seg of Object.keys(PLAN)) {
  queues[seg] = {
    reviewWaits: (PLAN[seg].reviewWaits || []).slice(),
    reviews: (PLAN[seg].reviews || []).slice(),
    artifactChecks: (PLAN[seg].artifactChecks || []).slice(),
    fixes: (PLAN[seg].fixes || []).slice(),
  };
}

function segFromLabel(label) {
  const parts = label.split(":");
  return parts[1];
}

// #348 -- one scripted chunk answer per review POINT, keyed by that point's own
// label. See the "THE TRAP" note above the harness: without this, repeated
// chunk calls drain the per-review-point queue.
const reviewWaitByLabel = {};

// A wait call is a RE-CHECK iff its label CONTAINS "-recheck:"; every other
// wait-shaped label is a chunk. Containment, not a prefix test: the review
// site's re-check label is "review-wait-recheck:<seg>:r<round>", which a prefix
// test written for the translate site ("wait-recheck:") would misclassify as a
// chunk -- and the fixture would silently answer the wrong thing.
function waitKind(label) {
  if (label.indexOf("-recheck:") !== -1) return "recheck";
  if (label.indexOf("wait:") === 0 || label.indexOf("review-wait:") === 0) return "chunk";
  return null;
}

async function agent(promptText, opts) {
  opts = opts || {};
  const label = opts.label || "";
  callsLog.push({
    label: label,
    phase: opts.phase || null,
    effort: opts.effort || null,
    agentType: opts.agentType || null,
    hasSchema: !!opts.schema,
  });

  if (label.indexOf("ledger:") === 0) {
    // Handles both the pre-1.2.0 "ledger:{kind}:{seg}" shape and the
    // current "ledger:blocked:{reason}:{seg}" shape (kind is always
    // parts[1]; seg is always the LAST colon-separated part, regardless of
    // how many reason segments sit in between).
    const parts = label.split(":");
    const kind = parts[1];
    const seg = parts[parts.length - 1];
    let status = "unknown";
    if (kind === "in_progress") status = "in_progress";
    else if (kind === "blocked") status = "blocked";
    else if (kind === "converged") status = "converged";
    else if (kind === "timeout") status = "non_converged";
    else if (kind === "cap") status = "non_converged";
    return {
      success: true,
      status: status,
      fragment_path: "/fixture/ledger/" + seg + ".json",
      fragment_sha1: "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef",
    };
  }
  if (label === "merge-ledger") {
    return {
      success: true,
      ledger_path: "/fixture/ledger.json",
      n_segments: SEGS_ARGS.length,
      missing_segments: [],
      stale_segments: [],
    };
  }

  const seg = segFromLabel(label);
  // #198 -- translate/review dispatch are now plain-Claude DRIVES of the
  // detached codex_job.py driver; the dispatcher agent returns
  // `DISPATCHED <seg> <DISP>` (parsed by translateStage/callReviewDispatch
  // via parseDisp). The DISP value does not affect call-counting here -- the
  // mock's wait branches return READY/TIMEOUT directly rather than running
  // the poll bash -- so a fixed hex DISP is sufficient.
  if (label.indexOf("translate:") === 0) return "DISPATCHED " + seg + " a1b2c3";
  if (label.indexOf("review-dispatch:") === 0) return "DISPATCHED " + seg + " d4e5f6";
  const kind = waitKind(label);
  if (kind !== null) {
    const p = PLAN[seg] || {};
    // Both re-check labels start with their site's own prefix, so "review-wait"
    // separates the two sites for chunks AND re-checks alike.
    const isReview = label.indexOf("review-wait") === 0;
    if (kind === "recheck") {
      const scripted = isReview ? p.reviewWaitRecheck : p.waitRecheck;
      return scripted === undefined || scripted === null ? "PENDING " + seg : scripted;
    }
    if (!isReview) return p.wait;
    if (!Object.prototype.hasOwnProperty.call(reviewWaitByLabel, label)) {
      const q = queues[seg].reviewWaits;
      if (q.length === 0) throw new Error("PLAN reviewWaits queue exhausted for " + seg + " label=" + label);
      reviewWaitByLabel[label] = q.shift();
    }
    return reviewWaitByLabel[label];
  }
  if (label.indexOf("review-read:") === 0) {
    const q = queues[seg].reviews;
    if (q.length === 0) throw new Error("PLAN reviews queue exhausted for " + seg + " label=" + label);
    return q.shift();
  }
  if (label.indexOf("artifact-check:") === 0) {
    const q = queues[seg].artifactChecks;
    if (q.length === 0) throw new Error("PLAN artifactChecks queue exhausted for " + seg + " label=" + label);
    return q.shift();
  }
  if (label.indexOf("fix:") === 0) {
    const q = queues[seg].fixes;
    if (q.length === 0) throw new Error("PLAN fixes queue exhausted for " + seg + " label=" + label);
    return q.shift();
  }
  if (label.indexOf("draft-probe:") === 0) {
    // #131 facet A -- a single per-segment value (not a queue), since the
    // probe fires at most once per segment (it only ever runs from
    // runRound's terminal fix-call-failed/draft-missing branch, which ends
    // the segment). Absent PLAN[seg].present defaults to false.
    const p = PLAN[seg] || {};
    // present: null (JSON null, distinct from the key being absent) means
    // this fixture wants to simulate the PROBE CALL ITSELF failing (agent
    // death / output-token ceiling / classifier block on the probe, not
    // just the fix) -- draftPresentAndValid's own null-return path.
    if (p.present === null) return null;
    return { present: p.present === true };
  }
  throw new Error("mock agent(): unrecognized label " + label);
}

async function pipeline(items, stage1, stage2) {
  pipelineCalled = true;
  const out = [];
  for (const item of items) {
    const r1 = await stage1(item);
    const r2 = await stage2(r1, item);
    out.push(r2);
  }
  return out;
}

function log(msg) { logLines.push(String(msg)); }

(async () => {
  try {
    const result = await __workflowMain__(agent, pipeline, log, SEGS_ARGS);
    process.stdout.write(JSON.stringify({
      result: result,
      calls: callsLog,
      log: logLines,
      pipelineCalled: pipelineCalled,
    }));
  } catch (err) {
    process.stderr.write("HARNESS_ERROR: " + (err && err.stack || String(err)) + "\n");
    process.exit(1);
  }
})();
"""


def build_harness(js_source: str, segs: list[str], plan: dict) -> str:
    wrapped = _wrap_for_execution(js_source)
    text = HARNESS_TEMPLATE.replace("__WRAPPED_SOURCE__", wrapped)
    text = text.replace("__PLAN_JSON__", json.dumps(plan))
    text = text.replace("__SEGS_JSON__", json.dumps(segs))
    return text


def run_workflow(
    *,
    tmp_path: Path,
    max_fix_rounds: int,
    batch_agent_cap: int,
    segs: list[str],
    plan: dict,
    timeout: int = 30,
) -> dict:
    assert NODE is not None, "node executable not found on PATH -- required to run this test file"
    js_source = instantiate_mass_translate(max_fix_rounds=max_fix_rounds, batch_agent_cap=batch_agent_cap)
    harness_text = build_harness(js_source, segs, plan)
    harness_path = tmp_path / "harness.js"
    harness_path.write_text(harness_text, encoding="utf-8")

    proc = subprocess.run(
        [NODE, str(harness_path)],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if proc.returncode != 0:
        raise AssertionError(
            f"harness execution failed (exit {proc.returncode}):\n"
            f"STDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
        )
    return json.loads(proc.stdout)


# ---------------------------------------------------------------------------
# #348 -- the wait-call ladder, restated as INDEPENDENT literals.
#
# Deriving these from the template would make every arithmetic assertion below
# tautological: the file would agree with whatever the template computed, which
# is precisely what it exists not to do. As literals they are a lock -- a chunk
# count changed in the template without changing the documented cost model
# fails here loudly, which is the point.
#
# WAIT_CHUNKS is Math.ceil(WAIT_BOUND_SEC / WAIT_CHUNK_SEC) = ceil(3450/480) = 8
# in the shipped template; WAIT_CALLS adds the ONE authoritative non-polling
# re-check that runs after the chunk budget is spent (or a chunk reported the
# driver's fail sentinel). tests/wait_chunking.test.py owns the per-chunk
# bound/sum properties; this file owns only what they cost.
WAIT_CHUNKS = 8
WAIT_CALLS = WAIT_CHUNKS + 1  # 9 -- the WORST case for one wait, not its cost


# ---------------------------------------------------------------------------
# Response-object builders -- shapes matching REVIEW_SCHEMA / REVIEW_ARTIFACT_
# SCHEMA closely enough to drive the real script's own branching
# (`rev.clean`, `rev.coverage_ok`, `rev.findings`, `art.match`,
# and `art.mismatch_detail`'s VALUE -- since #289 `artifactCheckMatched()`
# rejects on a non-empty (or wrong-typed) mismatch_detail, never on the key
# merely being present); this harness never itself validates against the
# JSON schemas (that is the real Workflow tool's job, out of scope here) -- only
# exercises the plain JS branching logic that reads these fields directly.
# ---------------------------------------------------------------------------
def review_obj(*, clean: bool, coverage_ok: bool = True) -> dict:
    # loc is a real colon-form structural reference ("PARA:seg01:0001", the
    # shape extract.py.template's own PARA blocks emit) -- NOT a degenerate
    # bare token. #133's authenticity gate (AUTHENTIC_LOC_RE) rejects any
    # colonless loc, so a fixture using a bare "1" here would make every
    # non-clean round in this file blocked/review-fabricated-loc instead of
    # exercising the branch each test actually targets (memory: test a gate
    # against realistic legit content, not a degenerate token that happens
    # to be shaped like what the gate rejects).
    return {
        "clean": clean,
        "coverage_ok": coverage_ok,
        "findings": [] if clean else [{"loc": "PARA:seg01:0001", "severity": "minor", "issue": "x", "suggest": "y"}],
        "draft_sha1": "a" * 40,
    }


def review_obj_fabricated_loc(sentinel: str = "TASK") -> dict:
    """A schema-valid, clean:false verdict whose one finding carries a bare,
    colonless infra-sentinel `loc` (TASK/PROCESS/SYSTEM/RUN) -- the #133
    fabrication shape a codex reviewer killed mid-judgment leaves behind
    after it already computed a real draft_sha1/dispatch_token but before it
    ever inspected actual draft content. AUTHENTIC_LOC_RE rejects this loc
    (no ":") while accepting review_obj's own real colon-form loc above."""
    return {
        "clean": False,
        "coverage_ok": True,
        "findings": [{"loc": sentinel, "severity": "major", "issue": "x", "suggest": "y"}],
        "draft_sha1": "a" * 40,
    }


def match_true() -> dict:
    return {"match": True}


def match_false(detail: str = "artifact mismatch") -> dict:
    return {"match": False, "mismatch_detail": detail}


def converged_worst_case_plan(seg: str, max_fix_rounds: int, *, final_clean: bool,
                              waits_exhaust_every_chunk: bool = False) -> dict:
    """The worst-case-within-the-converged/non-converged-at-cap branch: every
    one of the `max_fix_rounds` normal rounds AND the final confirming round
    forces its review point through the true 6-call shared-retry worst case
    (first read+check attempt fails via a mismatch, the retry succeeds) --
    per getVerifiedReview's own derivation, this is the ONLY path that costs
    exactly 6 per review point. Every normal round's resulting verdict is
    kept non-clean (so a fix always dispatches and the loop never converges
    early); `final_clean` selects between the branch's two possible terminal
    statuses (`converged` vs `non_converged`/`cap`) on the final round --
    both cost exactly the same number of calls, which is the entire point
    of the template's own comment calling this one combined branch.

    #348 -- `waits_exhaust_every_chunk` selects how expensive each WAIT on that
    branch is, which after #348 is a second, independent worst-case axis:

      False (default) -- every wait answers READY on its first chunk, so a wait
        costs 1 call. This is the realistic path and what the pre-#348 fixtures
        implicitly measured.
      True -- every chunk answers PENDING and the artifact lands only at the
        authoritative re-check, so a wait costs the full WAIT_CALLS. This is
        the frozen ssk-w5-smoke-116 shape (#348's actual defect: a clean
        artifact landing after the last poll ended) applied to EVERY wait at
        once, i.e. the true ceiling the estimator budgets for. The run still
        CONVERGES -- that is the fix -- it just pays the maximum."""
    ready = f"PENDING {seg}" if waits_exhaust_every_chunk else f"READY {seg}"
    review_waits: list = []
    reviews: list = []
    artifact_checks: list = []
    fixes: list = []

    for i in range(1, max_fix_rounds + 1):
        review_waits.append(ready)
        reviews.append(review_obj(clean=False))
        artifact_checks.append(match_false(f"round {i} first attempt mismatch"))
        reviews.append(review_obj(clean=False))
        artifact_checks.append(match_true())
        fixes.append(f"FIXED {seg} r{i}")

    # Final confirming round -- also forced through the shared retry (worst
    # case); no fix call follows it regardless of clean/non-clean outcome.
    review_waits.append(ready)
    reviews.append(review_obj(clean=False))
    artifact_checks.append(match_false("final round first attempt mismatch"))
    reviews.append(review_obj(clean=final_clean, coverage_ok=True))
    artifact_checks.append(match_true())

    plan = {
        "wait": ready,
        "reviewWaits": review_waits,
        "reviews": reviews,
        "artifactChecks": artifact_checks,
        "fixes": fixes,
    }
    if waits_exhaust_every_chunk:
        # The artifact lands after the last chunk's poll ended -- the ONE thing
        # #348 fixed. Without these the run would time out at the first wait
        # instead of paying the full ladder, and this fixture would silently
        # measure a 3-call segment.
        plan["waitRecheck"] = f"READY {seg}"
        plan["reviewWaitRecheck"] = f"READY {seg}"
    return plan


# Sentinel distinct from True/False/None: signals that `blocked_plan` should
# simulate the draft-probe AGENT CALL ITSELF failing (agent death/output-
# token ceiling/classifier block ON THE PROBE, not just the fix) -- the mock
# harness's "draft-probe:" branch maps this to a JSON `null` PLAN["present"]
# value, distinct from the field being absent entirely (no probe expected
# for this terminal_kind) and from a real True/False probe result.
_PROBE_ITSELF_FAILS = object()


def blocked_plan(seg: str, max_fix_rounds: int, terminal_kind: str) -> dict:
    """`max_fix_rounds - 1` completed normal rounds, each forced through the
    SAME worst-case-recovered review-point shape `converged_worst_case_plan`
    uses (6-call shared retry + 1 fix == 7 each) -- matching the estimator's
    own per-round worst-case assumption, so a blocked branch built from this
    helper is a genuinely maximal one, not an artificially cheap one -- then
    a terminating round whose shape depends on `terminal_kind` (module
    docstring's own per-kind derivation). `present` is set on the returned
    dict only for the three `callFix`-branch kinds (`draft-missing`,
    `fix-call-failed`, `fix-call-failed-probe-null`) where the mock's
    `draft-probe:*` branch reads it; every other kind never triggers a
    probe call at all, so it is omitted."""
    review_waits: list = []
    reviews: list = []
    artifact_checks: list = []
    fixes: list = []
    present: bool | None | object = None

    for i in range(1, max_fix_rounds):
        review_waits.append(f"READY {seg}")
        reviews.append(review_obj(clean=False))
        artifact_checks.append(match_false(f"round {i} first attempt mismatch"))
        reviews.append(review_obj(clean=False))
        artifact_checks.append(match_true())
        fixes.append(f"FIXED {seg} r{i}")

    if terminal_kind == "review-null":
        # readAndCheck's own "if (!rev) return {rev:null, art:null}"
        # short-circuit means artifact-check:* is NEVER called when the
        # read itself is falsy -- neither on the first attempt nor the
        # retry. 4 calls: dispatch + wait + read + read(retry). #131 facet B
        # -- NO terminal ledger write (recoverable).
        review_waits.append(f"READY {seg}")
        reviews.append(None)
        reviews.append(None)
    elif terminal_kind == "draft-missing":
        # The review point succeeds WITHOUT a retry (happy path) -- draft-
        # missing is a callFix-level failure, unrelated to the read/check
        # retry mechanic. The fix call itself reports DRAFT_MISSING, and the
        # new #131 probe then confirms the draft is genuinely absent/invalid
        # (present:false) -- 6 calls: dispatch+wait+read+check+fix+probe.
        # This kind STILL writes the terminal ledger entry, unchanged.
        review_waits.append(f"READY {seg}")
        reviews.append(review_obj(clean=False))
        artifact_checks.append(match_true())
        fixes.append(f"DRAFT_MISSING {seg}")
        present = False
    elif terminal_kind == "fix-call-failed":
        # #131 facet A (NEW): identical review-point shape to draft-missing
        # above (happy path, no retry) and the SAME falsy/DRAFT_MISSING fx
        # (fx alone can't tell a genuine missing draft apart from a
        # transient agent death/output-token-ceiling/classifier-block on a
        # perfectly fine draft -- that is exactly why the probe exists) --
        # but this time the probe confirms the draft IS present and valid
        # (present:true), so the segment ends fix-call-failed with NO
        # terminal ledger write (recoverable) instead of blocked/draft-
        # missing. 6 calls: dispatch+wait+read+check+fix+probe.
        review_waits.append(f"READY {seg}")
        reviews.append(review_obj(clean=False))
        artifact_checks.append(match_true())
        fixes.append(f"DRAFT_MISSING {seg}")
        present = True
    elif terminal_kind == "fix-call-failed-probe-null":
        # #131 facet A regression test (review-fix pass MAJOR fix): the SAME
        # falsy/DRAFT_MISSING fx as fix-call-failed above, but this time the
        # PROBE CALL ITSELF fails (agent death/output-token ceiling/
        # classifier block on the probe, not just the fix) -- a correlated
        # outage the original `!!(raw && raw.present === true)` return used
        # to conflate with genuine absence, wrongly landing on terminal
        # draft-missing. draftPresentAndValid must return null (inconclusive,
        # never proof of absence), and runRound must route it the SAME
        # recoverable way as present:true. 6 calls: dispatch+wait+read+
        # check+fix+probe, NO ledger write.
        review_waits.append(f"READY {seg}")
        reviews.append(review_obj(clean=False))
        artifact_checks.append(match_true())
        fixes.append(f"DRAFT_MISSING {seg}")
        present = _PROBE_ITSELF_FAILS
    elif terminal_kind == "review-artifact-mismatch":
        # The first attempt's read succeeds but its check reports a
        # mismatch; the retried read ALSO succeeds but its check STILL
        # mismatches -- the true 6-call worst case, no fix ever dispatches.
        # #131 facet B -- NO terminal ledger write (recoverable).
        review_waits.append(f"READY {seg}")
        reviews.append(review_obj(clean=False))
        artifact_checks.append(match_false("first mismatch"))
        reviews.append(review_obj(clean=False))
        artifact_checks.append(match_false("second mismatch"))
    elif terminal_kind == "review-fabricated-loc":
        # #133 (NEW): the review point succeeds WITHOUT a retry (happy path
        # -- first attempt's read+check matches), but the verdict's one
        # finding carries a bare, colonless infra-sentinel loc --
        # findingsAuthentic() rejects it right there, before any fix ever
        # dispatches. 4 calls: dispatch+wait+read+check. #131 facet B makes
        # this reason recoverable too (NO terminal ledger write), for free.
        review_waits.append(f"READY {seg}")
        reviews.append(review_obj_fabricated_loc())
        artifact_checks.append(match_true())
    elif terminal_kind == "review-timeout":
        # getVerifiedReview's own bounded review-wait poll never reports READY;
        # the read/check/fix machinery is never reached at all. #131 facet B --
        # NO terminal ledger write (recoverable).
        #
        # #348 -- this is the one terminating kind whose COST moved. A timing-
        # out wait no longer costs 1 call but the full ladder: every one of the
        # WAIT_CHUNKS chunks answers PENDING, then the authoritative re-check
        # runs (harness default: PENDING) -- 1 + WAIT_CALLS for the point.
        # PENDING, not the retired TIMEOUT: the chunk prompts ask for exactly
        # this sentinel now, and an unrecognized reply would reach the same
        # verdict through waitChunkVerdict's fallthrough instead of through the
        # branch this fixture means to drive.
        review_waits.append(f"PENDING {seg}")
    else:
        raise ValueError(f"unknown terminal_kind {terminal_kind!r}")

    result = {
        "wait": f"READY {seg}",
        "reviewWaits": review_waits,
        "reviews": reviews,
        "artifactChecks": artifact_checks,
        "fixes": fixes,
    }
    if present is _PROBE_ITSELF_FAILS:
        result["present"] = None  # JSON null -> mock's draft-probe branch returns JS null
    elif present is not None:
        result["present"] = present
    return result


def timeout_plan(seg: str) -> dict:
    """The translator never delivers READY in time -- reviewFixLoop's wait
    never reaches a READY verdict, so not a single review call is ever made.

    #348 -- "never" now means the whole ladder: every chunk answers PENDING and
    the authoritative re-check (harness default, also PENDING) confirms nothing
    landed. The branch is unchanged; what it costs is not."""
    return {"wait": f"PENDING {seg}", "reviewWaits": [], "reviews": [], "artifactChecks": [], "fixes": []}


def blocked_branch_total(max_fix_rounds: int, terminating_cost: int, *, ledger_write: bool = True,
                         wait_calls: int = 1) -> int:
    """(2 + wait_calls) (fixed: in_progress ledger + translate dispatch +
    translate's own wait) + (6 + wait_calls)*(max_fix_rounds-1) (completed
    WORST-CASE-RECOVERED normal rounds -- review point with a forced shared
    retry (5 + its wait) + fix (1), matching the estimator's own per-round
    assumption) + terminating_cost + (1 if ledger_write else 0) (terminal
    ledger write -- #131 SKIPS this write entirely for every transient/
    recoverable terminating reason: review-null, review-artifact-mismatch,
    review-timeout, review-fabricated-loc, and fix-call-failed. Only
    draft-missing, a genuine anomaly, still writes -- `ledger_write`
    defaults to True/unchanged for callers that don't pass it.

    #348 -- `wait_calls` is what a wait COSTS on the path a fixture actually
    drives, which is NOT the same number as the estimator's WAIT_CALLS
    ceiling: a wait whose first chunk answers READY costs 1. It defaults to 1
    because every fixture below scripts READY-on-the-first-chunk waits for the
    rounds LEADING UP TO the terminating one. The terminating round's own wait
    cost lives in `terminating_cost`, which its caller computes -- a fixture
    that times a wait OUT there pays 1 + WAIT_CALLS for that review point (all
    chunks plus the re-check), not 2."""
    return ((2 + wait_calls) + (6 + wait_calls) * (max_fix_rounds - 1)
            + terminating_cost + (1 if ledger_write else 0))


def converged_branch_total(max_fix_rounds: int, *, wait_calls: int = 1) -> int:
    """The converged/non-converged-at-cap branch total: (2 + wait_calls)
    (fixed: in_progress ledger + translate dispatch + translate's wait) +
    max_fix_rounds*(6 + wait_calls) (all MAXFIX normal rounds, each a
    worst-case review point -- dispatch + wait + read + check + read + check
    -- plus 1 fix) + (5 + wait_calls) (final confirming review point, worst
    case, no fix) + 1 (terminal ledger write)
    == 8 + 2*wait_calls + max_fix_rounds*(6 + wait_calls).

    That is the template's own per-segment term verbatim. At wait_calls=1 it
    collapses to the pre-#348 `10 + 7*max_fix_rounds`, which is the arithmetic
    proof that #348 generalised the estimator rather than rewriting it -- and
    at wait_calls=WAIT_CALLS it IS the estimator's per-segment ceiling. The two
    uses are deliberately the same function: a ceiling that could drift from
    the observed-cost formula is the bug this file exists to prevent."""
    return (2 + wait_calls) + max_fix_rounds * (6 + wait_calls) + (5 + wait_calls) + 1


def bucket_calls_by_segment(calls: list[dict]) -> tuple[dict[str, list[dict]], list[dict]]:
    """Splits the harness's flat call log into per-segment buckets plus the
    batch-level bucket (the single mandatory mergeLedgerPrompt call)."""
    per_seg: dict[str, list[dict]] = {}
    batch_level: list[dict] = []
    for call in calls:
        label = call["label"]
        if label == "merge-ledger":
            batch_level.append(call)
            continue
        parts = label.split(":")
        seg = parts[-1] if parts[0] == "ledger" else parts[1]
        per_seg.setdefault(seg, []).append(call)
    return per_seg, batch_level


# ---------------------------------------------------------------------------
# 1/2: the boundary itself -- `estimatedCalls > BATCH_AGENT_CAP`, not `>=`.
# ---------------------------------------------------------------------------


def test_estimator_boundary_exactly_at_cap_permits_dispatch_and_converges(tmp_path):
    max_fix_rounds = 2
    segs = ["seg01", "seg02"]
    # 1 + 2*(8 + 2*9 + 2*(6+9)) = 1 + 2*56 = 113
    estimated = 1 + len(segs) * converged_branch_total(max_fix_rounds, wait_calls=WAIT_CALLS)

    plan = {seg: converged_worst_case_plan(seg, max_fix_rounds, final_clean=True) for seg in segs}
    out = run_workflow(
        tmp_path=tmp_path,
        max_fix_rounds=max_fix_rounds,
        batch_agent_cap=estimated,
        segs=segs,
        plan=plan,
    )

    assert out["pipelineCalled"] is True, "estimatedCalls == cap must NOT trip the gate (the check is '>', not '>=')"

    result = out["result"]
    assert result["batchComplete"] is True
    assert sorted(r["seg"] for r in result["converged"]) == segs
    assert result["failed"] == []

    # #348 -- THE ESTIMATE IS A CEILING, AND THIS ASSERTION SAYS SO. Before the
    # chunked wait a wait was exactly one call, so the worst-case run's real
    # total EQUALLED the estimate and this line read `==`. It cannot now: this
    # plan answers READY on every wait's FIRST chunk, so each of its waits costs
    # 1 of the 9 the estimator budgets. Restating that as `<=` is not a
    # weakening -- "never exceeds the preflight bound" is the property the cap
    # actually needs, and it is asserted here on a converging run and again,
    # as strict equality, against the true worst case in
    # test_worst_case_wait_ladder_costs_exactly_the_estimate below.
    assert len(out["calls"]) <= estimated, (
        f"a converging worst-case-review batch made {len(out['calls'])} calls, above the "
        f"preflight estimate of {estimated} -- the cap this gate enforces would be unsound"
    )

    per_seg, batch_level = bucket_calls_by_segment(out["calls"])
    assert len(batch_level) == 1, "exactly one mandatory batch-level mergeLedgerPrompt call"
    for seg in segs:
        # Exact, on the path this plan actually drives: every wait READY on its
        # first chunk. At wait_calls=1 the term is the pre-#348 10 + 7*MAXFIX
        # verbatim, which is what makes this a regression lock and not a
        # re-baselining -- the observed cost of this branch never moved.
        assert len(per_seg[seg]) == converged_branch_total(max_fix_rounds, wait_calls=1)
        assert len(per_seg[seg]) == 10 + 7 * max_fix_rounds


def test_estimator_one_below_boundary_blocks_dispatch_entirely(tmp_path):
    max_fix_rounds = 2
    segs = ["seg01", "seg02"]
    estimated = 1 + len(segs) * converged_branch_total(max_fix_rounds, wait_calls=WAIT_CALLS)  # 113

    # Same configuration as the boundary-permits test above, but the cap is
    # one less -- deliberately reuse a plan that WOULD converge if pipeline()
    # ever ran, so a false negative (gate fails to trip) surfaces as a
    # queue-exhaustion/mismatch error rather than silently "passing".
    plan = {seg: converged_worst_case_plan(seg, max_fix_rounds, final_clean=True) for seg in segs}
    out = run_workflow(
        tmp_path=tmp_path,
        max_fix_rounds=max_fix_rounds,
        batch_agent_cap=estimated - 1,
        segs=segs,
        plan=plan,
    )

    assert out["pipelineCalled"] is False, "pipeline() must never run once the batch is judged too large"
    assert out["calls"] == [], "zero real agent() calls once the gate trips -- it must return before any dispatch"

    result = out["result"]
    assert result == {
        "converged": [],
        "failed": [],
        "reason": "batch-too-large",
        "estimatedCalls": estimated,
        "cap": estimated - 1,
    }
    assert any("Batch too large" in line and str(estimated) in line for line in out["log"])


# ---------------------------------------------------------------------------
# 1b (NEW, #348): the strict-equality half the boundary fixture above gave up.
#
# Once a wait can cost anywhere from 1 to WAIT_CALLS calls, "real == estimate"
# stops being true of ANY single run -- the estimate became a ceiling. Asserting
# only `<=` would leave the ceiling unpinned from BELOW: an estimator that
# budgeted ten times too much would satisfy every `<=` in this file, and the
# batch_agent_cap gate's whole job is to be tight enough to still permit real
# batches. So this fixture builds the run that actually costs the maximum --
# every chunk PENDING at every wait, the artifact landing only at the
# authoritative re-check, all MAXFIX rounds plus the final confirming one, each
# review point through the 6-call shared retry -- and demands EXACT equality.
#
# It doubles as the end-to-end proof of #348's fix at full scale: this is the
# frozen ssk-w5-smoke-116 shape (a clean artifact landing after the last poll
# ended) at EVERY wait in the run, and it CONVERGES. On the unfixed template
# the same plan reports translate-timeout at the very first wait.
# ---------------------------------------------------------------------------


def test_worst_case_wait_ladder_costs_exactly_the_estimate(tmp_path):
    max_fix_rounds = 2
    segs = ["seg01", "seg02"]
    estimated = 1 + len(segs) * converged_branch_total(max_fix_rounds, wait_calls=WAIT_CALLS)

    plan = {
        seg: converged_worst_case_plan(
            seg, max_fix_rounds, final_clean=True, waits_exhaust_every_chunk=True,
        )
        for seg in segs
    }
    out = run_workflow(
        tmp_path=tmp_path,
        max_fix_rounds=max_fix_rounds,
        batch_agent_cap=estimated,
        segs=segs,
        plan=plan,
    )

    assert out["pipelineCalled"] is True
    result = out["result"]
    assert sorted(r["seg"] for r in result["converged"]) == segs, (
        "a run whose artifacts all land at the re-check must still converge -- "
        f"that is what #348 fixed; got {result}"
    )
    assert result["failed"] == []

    # The whole point: at the true worst case the estimate is not merely an
    # upper bound, it is EXACTLY the cost. A ceiling that can never be reached
    # would be silently over-budgeting every batch.
    assert len(out["calls"]) == estimated

    per_seg, batch_level = bucket_calls_by_segment(out["calls"])
    assert len(batch_level) == 1
    for seg in segs:
        assert len(per_seg[seg]) == converged_branch_total(max_fix_rounds, wait_calls=WAIT_CALLS)

        # ...and the cost is where the formula says it is. Without this, a
        # segment could hit the same total for the wrong reason (e.g. extra
        # review rounds paired with cheap waits) and the equality above would
        # still hold. One wait per translate + one per review point (MAXFIX
        # normal rounds + the final confirming one), each spending its full
        # ladder of WAIT_CHUNKS chunks plus exactly one re-check.
        n_waits = 1 + (max_fix_rounds + 1)
        wait_calls = [c for c in per_seg[seg] if "wait" in c["label"]]
        rechecks = [c for c in wait_calls if "-recheck:" in c["label"]]
        assert len(wait_calls) == n_waits * WAIT_CALLS
        assert len(rechecks) == n_waits, (
            "exactly one authoritative re-check per wait -- a re-check that polled "
            "would be a ninth chunk and could itself hit the Bash per-call cap"
        )


# ---------------------------------------------------------------------------
# 3/4/5: one fixture per blocked terminating sub-case.
# ---------------------------------------------------------------------------


def test_blocked_review_null_terminating_subcase(tmp_path):
    max_fix_rounds = 3
    seg = "segA"
    out = run_workflow(
        tmp_path=tmp_path,
        max_fix_rounds=max_fix_rounds,
        batch_agent_cap=10_000,
        segs=[seg],
        plan={seg: blocked_plan(seg, max_fix_rounds, "review-null")},
    )

    result = out["result"]
    assert result["converged"] == []
    assert len(result["failed"]) == 1
    failed = result["failed"][0]
    assert failed["seg"] == seg
    assert failed["converged"] is False
    assert failed["reason"] == "review-null"

    per_seg, batch_level = bucket_calls_by_segment(out["calls"])
    assert len(batch_level) == 1
    assert len(per_seg[seg]) == blocked_branch_total(max_fix_rounds, terminating_cost=4, ledger_write=False)


def test_blocked_draft_missing_terminating_subcase(tmp_path):
    max_fix_rounds = 3
    seg = "segB"
    out = run_workflow(
        tmp_path=tmp_path,
        max_fix_rounds=max_fix_rounds,
        batch_agent_cap=10_000,
        segs=[seg],
        plan={seg: blocked_plan(seg, max_fix_rounds, "draft-missing")},
    )

    result = out["result"]
    assert result["converged"] == []
    assert len(result["failed"]) == 1
    failed = result["failed"][0]
    assert failed["seg"] == seg
    assert failed["converged"] is False
    assert failed["reason"] == "draft-missing"

    per_seg, batch_level = bucket_calls_by_segment(out["calls"])
    assert len(batch_level) == 1
    # terminating_cost=6, not 5: the new #131 draft-probe call
    # (dispatch+wait+read+check+fix+probe) fires whenever fx comes back
    # falsy/DRAFT_MISSING, and this kind still writes the terminal ledger
    # entry (ledger_write defaults True) -- a real anomaly worth human
    # attention, unchanged.
    assert len(per_seg[seg]) == blocked_branch_total(max_fix_rounds, terminating_cost=6)


def test_blocked_fix_call_failed_terminating_subcase(tmp_path):
    """#131 facet A (NEW): the SAME falsy/DRAFT_MISSING fx as draft-missing
    above, but the probe confirms the draft IS present and valid -- a
    transient fix-call failure (agent died / output-token ceiling /
    classifier block), not a genuine missing draft. Ends the segment as
    fix-call-failed with NO terminal ledger write (recoverable)."""
    max_fix_rounds = 3
    seg = "segI"
    out = run_workflow(
        tmp_path=tmp_path,
        max_fix_rounds=max_fix_rounds,
        batch_agent_cap=10_000,
        segs=[seg],
        plan={seg: blocked_plan(seg, max_fix_rounds, "fix-call-failed")},
    )

    result = out["result"]
    assert result["converged"] == []
    assert len(result["failed"]) == 1
    failed = result["failed"][0]
    assert failed["seg"] == seg
    assert failed["converged"] is False
    assert failed["reason"] == "fix-call-failed"

    per_seg, batch_level = bucket_calls_by_segment(out["calls"])
    assert len(batch_level) == 1
    labels = [c["label"] for c in per_seg[seg]]
    assert any(label.startswith("draft-probe:") for label in labels), (
        "fix-call-failed must be reached via the draftPresentAndValid probe"
    )
    # "ledger:in_progress:*" (translateStage's own unconditional write) is
    # still present -- only the TERMINAL "ledger:blocked:*" write must be
    # absent (that is the one #131 facet A skips).
    assert not any(label.startswith("ledger:blocked:") for label in labels), (
        "fix-call-failed must NOT write a terminal ledger entry -- it stays "
        "in_progress and recoverable, exactly like the other #131 facets"
    )
    assert len(per_seg[seg]) == blocked_branch_total(max_fix_rounds, terminating_cost=6, ledger_write=False)


def test_blocked_fix_call_failed_probe_itself_fails_terminating_subcase(tmp_path):
    """#131 facet A regression test (review-fix pass MAJOR fix): the probe
    AGENT CALL ITSELF fails (mock returns null, simulating agent death /
    output-token ceiling / classifier block on the PROBE, not just the fix
    -- a correlated outage). draftPresentAndValid must return null
    (inconclusive), and runRound must route it the SAME recoverable way as
    present:true -- NOT fall through to a terminal draft-missing write.
    Before the MAJOR fix, `!!(raw && raw.present === true)` collapsed a null
    probe result to `false`, wrongly landing on terminal draft-missing;
    this is the regression lock for that."""
    max_fix_rounds = 3
    seg = "segJ"
    out = run_workflow(
        tmp_path=tmp_path,
        max_fix_rounds=max_fix_rounds,
        batch_agent_cap=10_000,
        segs=[seg],
        plan={seg: blocked_plan(seg, max_fix_rounds, "fix-call-failed-probe-null")},
    )

    result = out["result"]
    assert result["converged"] == []
    assert len(result["failed"]) == 1
    failed = result["failed"][0]
    assert failed["seg"] == seg
    assert failed["converged"] is False
    assert failed["reason"] == "fix-call-failed", (
        "a probe call that itself fails (null) must be treated as INCONCLUSIVE, "
        "never as proof of absence -- it must end fix-call-failed, not draft-missing"
    )

    per_seg, batch_level = bucket_calls_by_segment(out["calls"])
    assert len(batch_level) == 1
    labels = [c["label"] for c in per_seg[seg]]
    assert any(label.startswith("draft-probe:") for label in labels)
    assert not any(label.startswith("ledger:blocked:") for label in labels), (
        "a probe-call-itself-failed outcome must NOT write a terminal ledger "
        "entry -- it stays in_progress and recoverable, exactly like a "
        "confirmed-present probe result"
    )
    assert len(per_seg[seg]) == blocked_branch_total(max_fix_rounds, terminating_cost=6, ledger_write=False)


def test_blocked_review_fabricated_loc_terminating_subcase(tmp_path):
    """#133 (NEW): a schema-valid, artifact-matched verdict whose one
    finding carries a bare, colonless infra-sentinel loc -- the shape a
    codex reviewer killed mid-judgment (after obtaining a real
    draft_sha1/dispatch_token but before inspecting real content) leaves
    behind. findingsAuthentic() must reject it BEFORE any fix dispatches,
    routing to blocked/review-fabricated-loc, which #131 facet B already
    makes recoverable (no extra ledger-skip wiring needed for this reason)."""
    max_fix_rounds = 3
    seg = "segH"
    out = run_workflow(
        tmp_path=tmp_path,
        max_fix_rounds=max_fix_rounds,
        batch_agent_cap=10_000,
        segs=[seg],
        plan={seg: blocked_plan(seg, max_fix_rounds, "review-fabricated-loc")},
    )

    result = out["result"]
    assert result["converged"] == []
    assert len(result["failed"]) == 1
    failed = result["failed"][0]
    assert failed["seg"] == seg
    assert failed["converged"] is False
    assert failed["reason"] == "review-fabricated-loc"

    per_seg, batch_level = bucket_calls_by_segment(out["calls"])
    assert len(batch_level) == 1
    # The terminal round itself (round max_fix_rounds, where the fabricated
    # loc appears) must never reach a fix call -- the `max_fix_rounds - 1`
    # PRIOR completed rounds each legitimately have their own "fix:*:r{i}"
    # call, so this checks the terminal round specifically, not "no fix
    # calls at all".
    labels = [c["label"] for c in per_seg[seg]]
    assert f"fix:{seg}:r{max_fix_rounds}" not in labels, (
        "a fabricated-loc verdict must never reach the fix call"
    )
    # "ledger:in_progress:*" (translateStage's own unconditional write) is
    # still present -- only the TERMINAL "ledger:blocked:*" write must be
    # absent (that is the one #131 facet B skips for this reason too).
    assert not any(label.startswith("ledger:blocked:") for label in labels), (
        "review-fabricated-loc must NOT write a terminal ledger entry"
    )
    assert len(per_seg[seg]) == blocked_branch_total(max_fix_rounds, terminating_cost=4, ledger_write=False)


def test_blocked_review_artifact_mismatch_terminating_subcase(tmp_path):
    max_fix_rounds = 3
    seg = "segC"
    out = run_workflow(
        tmp_path=tmp_path,
        max_fix_rounds=max_fix_rounds,
        batch_agent_cap=10_000,
        segs=[seg],
        plan={seg: blocked_plan(seg, max_fix_rounds, "review-artifact-mismatch")},
    )

    result = out["result"]
    assert result["converged"] == []
    assert len(result["failed"]) == 1
    failed = result["failed"][0]
    assert failed["seg"] == seg
    assert failed["converged"] is False
    assert failed["reason"] == "review-artifact-mismatch"

    per_seg, batch_level = bucket_calls_by_segment(out["calls"])
    assert len(batch_level) == 1
    assert len(per_seg[seg]) == blocked_branch_total(max_fix_rounds, terminating_cost=6, ledger_write=False)


# ---------------------------------------------------------------------------
# 10 (NEW): review's own bounded-poll timeout -- distinct from translate's,
# and from the pre-1.2.0 file's three sub-cases, since the review restructure
# gave review its own independent readiness gate.
# ---------------------------------------------------------------------------


def test_blocked_review_timeout_terminating_subcase(tmp_path):
    max_fix_rounds = 3
    seg = "segG"
    out = run_workflow(
        tmp_path=tmp_path,
        max_fix_rounds=max_fix_rounds,
        batch_agent_cap=10_000,
        segs=[seg],
        plan={seg: blocked_plan(seg, max_fix_rounds, "review-timeout")},
    )

    result = out["result"]
    assert result["converged"] == []
    assert len(result["failed"]) == 1
    failed = result["failed"][0]
    assert failed["seg"] == seg
    assert failed["converged"] is False
    assert failed["reason"] == "review-timeout"

    per_seg, batch_level = bucket_calls_by_segment(out["calls"])
    assert len(batch_level) == 1
    # #348 -- the terminating review point now costs dispatch + the FULL wait
    # ladder (WAIT_CHUNKS chunks, all PENDING, then the authoritative re-check),
    # where it used to cost dispatch + 1. The branch and its reason string are
    # unchanged; only the wait's own price moved.
    assert len(per_seg[seg]) == blocked_branch_total(
        max_fix_rounds, terminating_cost=1 + WAIT_CALLS, ledger_write=False,
    )
    n_review_wait_calls = sum(
        1 for c in per_seg[seg] if c["label"].startswith("review-wait")
    )
    assert n_review_wait_calls == max_fix_rounds - 1 + WAIT_CALLS, (
        "a timing-out review point must spend its whole chunk budget AND the "
        "re-check; the prior rounds' waits each answer on chunk 1"
    )


# ---------------------------------------------------------------------------
# 6: the timeout branch (translate's own).
# ---------------------------------------------------------------------------


def test_timeout_branch(tmp_path):
    max_fix_rounds = 5  # deliberately irrelevant: the loop never reaches round 1
    seg = "segD"
    out = run_workflow(
        tmp_path=tmp_path,
        max_fix_rounds=max_fix_rounds,
        batch_agent_cap=10_000,
        segs=[seg],
        plan={seg: timeout_plan(seg)},
    )

    result = out["result"]
    assert result["converged"] == []
    assert len(result["failed"]) == 1
    failed = result["failed"][0]
    assert failed["seg"] == seg
    assert failed["converged"] is False
    assert failed["reason"] == "translate-timeout"

    per_seg, batch_level = bucket_calls_by_segment(out["calls"])
    assert len(batch_level) == 1
    # 1 in_progress ledger write + 1 translate call + the translate wait,
    # independent of max_fix_rounds. #131 facet C: NO terminal
    # "ledger:timeout:*" write anymore -- the segment stays in_progress and
    # recoverable instead of a terminal non_converged/translate-timeout.
    # #348 -- the wait is the full ladder here (WAIT_CHUNKS PENDING chunks +
    # the authoritative re-check), so 2 + WAIT_CALLS where it used to be 3.
    assert len(per_seg[seg]) == 2 + WAIT_CALLS
    n_wait_calls = sum(1 for c in per_seg[seg] if c["label"].startswith("wait"))
    assert n_wait_calls == WAIT_CALLS, (
        f"a translate wait that never lands must spend all {WAIT_CHUNKS} chunks and then "
        f"the authoritative re-check, got {n_wait_calls} wait calls"
    )
    assert sum(1 for c in per_seg[seg] if c["label"] == f"wait-recheck:{seg}") == 1, (
        "the re-check runs exactly once, after the chunk budget is spent (#348)"
    )
    assert not any(c["label"].startswith("ledger:timeout:") for c in per_seg[seg]), (
        "translate-timeout must NOT write a terminal ledger entry (#131 facet C) "
        "-- only the in_progress write from translateStage should appear"
    )


# ---------------------------------------------------------------------------
# 7: dedicated case -- a review-artifact-mismatch segment's ACTUAL call
# count, built from worst-case-recovered prior rounds (matching the
# estimator's own per-round assumption), never exceeds the formula's own
# per-segment bound (10 + 7*MAXFIX).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("max_fix_rounds", [1, 2, 3, 6])
def test_review_artifact_mismatch_actual_calls_never_exceed_formula_bound(tmp_path, max_fix_rounds):
    seg = "segE"
    out = run_workflow(
        tmp_path=tmp_path,
        max_fix_rounds=max_fix_rounds,
        batch_agent_cap=10_000,
        segs=[seg],
        plan={seg: blocked_plan(seg, max_fix_rounds, "review-artifact-mismatch")},
    )

    result = out["result"]
    assert len(result["failed"]) == 1
    assert result["failed"][0]["reason"] == "review-artifact-mismatch"

    per_seg, _ = bucket_calls_by_segment(out["calls"])
    actual_calls = len(per_seg[seg])
    # the exact term estimatedCalls sizes per segment (#348: 8 + 2*WAIT_CALLS
    # + MAXFIX*(6 + WAIT_CALLS), which at WAIT_CALLS=1 is the old 10 + 7*MAXFIX)
    per_segment_bound = converged_branch_total(max_fix_rounds, wait_calls=WAIT_CALLS)

    assert actual_calls == blocked_branch_total(max_fix_rounds, terminating_cost=6, ledger_write=False)
    assert actual_calls <= per_segment_bound, (
        f"a review-artifact-mismatch segment made {actual_calls} real agent() calls, "
        f"exceeding the estimator's own per-segment bound of {per_segment_bound} "
        f"(max_fix_rounds={max_fix_rounds}) -- the preflight estimate would have been unsound"
    )


# ---------------------------------------------------------------------------
# 8: the closed-form formula itself, cheaply, across several (N, maxFix)
# pairs -- forcing the gate to trip every time (cap = estimate - 1) means
# pipeline() never runs and agent() is never called, so this needs no PLAN
# at all; it reads the real script's own computed estimatedCalls back out of
# its batch-too-large return value.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "n_segs,max_fix_rounds",
    [(1, 1), (3, 2), (5, 4), (10, 6), (37, 1)],
)
def test_estimator_formula_matches_closed_form(tmp_path, n_segs, max_fix_rounds):
    segs = [f"seg{idx:03d}" for idx in range(n_segs)]
    # Written out rather than routed through converged_branch_total: this row
    # is the one place the closed form is restated INDEPENDENTLY of the helper
    # every other assertion shares, so a wrong helper cannot agree with itself.
    expected = 1 + n_segs * (8 + 2 * WAIT_CALLS + max_fix_rounds * (6 + WAIT_CALLS))

    out = run_workflow(
        tmp_path=tmp_path,
        max_fix_rounds=max_fix_rounds,
        batch_agent_cap=expected - 1,
        segs=segs,
        plan={},
    )

    assert out["pipelineCalled"] is False
    assert out["calls"] == []
    result = out["result"]
    assert result["reason"] == "batch-too-large"
    assert result["estimatedCalls"] == expected
    assert result["cap"] == expected - 1
    assert result["converged"] == []
    assert result["failed"] == []


# ---------------------------------------------------------------------------
# 9 (bonus, not separately spec-mandated but nearly free given the harness
# above): the SAME per-segment call total applies whether the final
# confirming round ends convergent or non-convergent-at-cap -- both are "the
# cap/converged branch" in the formula's own derivation, at the same cost.
# ---------------------------------------------------------------------------


def test_non_converged_at_cap_costs_the_same_as_converged(tmp_path):
    max_fix_rounds = 2
    seg = "segF"
    out = run_workflow(
        tmp_path=tmp_path,
        max_fix_rounds=max_fix_rounds,
        batch_agent_cap=10_000,
        segs=[seg],
        plan={seg: converged_worst_case_plan(seg, max_fix_rounds, final_clean=False)},
    )

    result = out["result"]
    assert result["converged"] == []
    assert len(result["failed"]) == 1
    failed = result["failed"][0]
    assert failed["seg"] == seg
    assert failed["converged"] is False
    assert failed["reason"] == "cap"
    assert failed["rounds"] == max_fix_rounds + 1

    per_seg, _ = bucket_calls_by_segment(out["calls"])
    assert len(per_seg[seg]) == converged_branch_total(max_fix_rounds)


# ---------------------------------------------------------------------------
# 11 (NEW): a dedicated, narrower companion to fixture 1 -- rather than
# forcing EVERY round through the shared-retry worst case, this forces it in
# exactly ONE mid-loop round (the last normal round, i.e. round max_fix_
# rounds itself) while every other round -- including the final confirming
# one -- takes the cheap happy path (no retry). This isolates the shared-
# retry mechanic in getVerifiedReview from the estimator's own worst-case
# ceiling, and the expected total below is hand-computed per-round, not
# read back off the closed-form formula -- a genuinely independent check
# that the harness's queue machinery counts a PARTIAL-worst-case run
# correctly, not just the all-worst-case boundary fixture above. Directly
# exercises the CONTRACT's explicit "force a mid-loop read/check->retry->fix
# max round and assert EXACT equality" requirement.
# ---------------------------------------------------------------------------


def test_shared_retry_recovers_mid_loop_and_matches_exact_count(tmp_path):
    max_fix_rounds = 3
    seg = "segRetry"

    review_waits = [f"READY {seg}"] * (max_fix_rounds + 1)
    reviews = [
        review_obj(clean=False),  # round 1 -- happy-path read (no retry)
        review_obj(clean=False),  # round 2 -- happy-path read (no retry)
        review_obj(clean=False),  # round 3 (the max round) -- first attempt
        review_obj(clean=False),  # round 3 -- shared-retry attempt, succeeds
        review_obj(clean=True, coverage_ok=True),  # final round -- happy-path read
    ]
    artifact_checks = [
        match_true(),                                    # round 1
        match_true(),                                     # round 2
        match_false("round 3 first attempt mismatch"),     # round 3, first attempt fails
        match_true(),                                      # round 3, retry succeeds
        match_true(),                                       # final round
    ]
    fixes = [f"FIXED {seg} r1", f"FIXED {seg} r2", f"FIXED {seg} r3"]

    plan = {
        seg: {
            "wait": f"READY {seg}",
            "reviewWaits": review_waits,
            "reviews": reviews,
            "artifactChecks": artifact_checks,
            "fixes": fixes,
        }
    }

    out = run_workflow(
        tmp_path=tmp_path,
        max_fix_rounds=max_fix_rounds,
        batch_agent_cap=10_000,
        segs=[seg],
        plan=plan,
    )

    result = out["result"]
    assert result["batchComplete"] is True
    assert [r["seg"] for r in result["converged"]] == [seg]
    assert result["failed"] == []

    # round1 (happy, 4+1fix=5) + round2 (happy, 5) + round3 (shared retry,
    # 6+1fix=7) + final review (happy, 4, no fix) + 3 fixed + 1 terminal
    # ledger -- hand-computed independently of converged_branch_total (which
    # assumes EVERY round hits the 6-call worst case, not just one).
    expected_total = 3 + (5 + 5 + 7) + 4 + 1
    assert expected_total == 25

    per_seg, batch_level = bucket_calls_by_segment(out["calls"])
    assert len(batch_level) == 1
    assert len(per_seg[seg]) == expected_total
    assert len(out["calls"]) == expected_total + 1


# ===========================================================================
# GLOSSARY-PASS TEMPLATE (issues #101, #95, plus 1.16.0's pre-merge citation
# review) -- same extract-substitute-wrap-run-under-node harness mechanism as
# the mass-translate section above, but for glossary-pass-wf.template.js's
# own, distinct control flow:
#
#   * PREFLIGHT COST CAP (#95, re-derived for 1.16.0's citation review, again
#     for 1.16.1's prepare/judge split, #347, and again for 1.16.2's chunked
#     wait, #352). The template's own preflight comment block documents the
#     per-call ladder this whole section derives its expected counts from --
#     never the other way round:
#
#         1 precheck                                (always, exactly one)
#       + (dispatch + wait)          per attempt    (1 + WAIT_CALLS each)
#       + (citation prepare + judge) per attempt    (2 each, LIVE ONLY)
#       + merge + verify                            (2, fixed, per run)
#
#     with attempts == MAX_CITATION_RETRIES + 1 == 3 in the worst case, so:
#
#         live    -- perBatch = 1 + (3 + WAIT_CALLS)*(MAX_CITATION_RETRIES+1)
#                            = 1 + 6*3 = 19
#         offline -- perBatch = 1 + (1 + WAIT_CALLS) == 2 + WAIT_CALLS == 5
#         estimatedCalls = perBatch * BATCHES.length + 2
#
#     The live term went 10 -> 13 in 1.16.1 and NOT because the pass does more
#     work: #347 split the single fetch-and-judge reviewer into a prepare call
#     that runs the validated fetcher while ingesting no page content, and a
#     judge call that reads only local files. One review point, two calls.
#
#     It went 13 -> 19 in 1.16.2, for a reason that has nothing to do with
#     citations: ONE WAIT STOPPED BEING ONE AGENT CALL. The Bash tool clamps a
#     single call at 600 s, so #352 spends the 900 s wait across WAIT_CHUNKS
#     bounded chunks plus one authoritative non-polling re-check -- WAIT_CALLS
#     == 3 in the worst case -- and the retry ladder multiplies that.
#
#     THE OFFLINE TERM MOVED THIS TIME, 3 -> 5, and that is the mode-awareness
#     principle being APPLIED rather than abandoned. The rule was never "offline
#     keeps its historical figure"; it is "charge offline only for work an
#     offline run can actually perform". A retry ladder is work offline can NEVER
#     perform (there is no reviewer to reject an attempt), so charging for it was
#     always a false refusal -- and the offline branch still does not. The extra
#     wait calls are the opposite case: every offline run must be
#     preflight-charged for them on every batch, as a worst-case CEILING -- not
#     a claim that every batch actually spends them (see below: a wait whose
#     first chunk finds the fragment pays 1 call, not WAIT_CALLS, offline
#     exactly as live) -- because the Bash clamp is indifferent to
#     research_mode. Leaving offline at 3*N+2 would be an UNDER-count, which is
#     the dangerous direction -- it lets a run start and then blow
#     engine.batch_agent_cap mid-flight
#     instead of refusing it early and loudly.
#
#     If that exceeds engine.batch_agent_cap the whole run is refused WITHOUT
#     calling pipeline(), mirroring the mass template's
#     `{merged:false, reason:"batch-too-large", ...}` shape.
#
#     The OFFLINE branch is pinned here on its own precisely because it is NOT
#     mode-blind: it charges 1 dispatch + 1 wait for exactly one attempt, never
#     a ladder. Under live the estimate is a worst-case CEILING, not the
#     observed cost -- a run whose reviews approve on attempt 0, and whose very
#     first wait chunk finds the fragment, pays 5 per batch, not 19 -- so the
#     live tests assert the observed count from the ladder AND that it stays
#     under the ceiling. 1.16.2 WIDENS that gap rather than narrowing it, which
#     is why the observed constants below did not move while the ceiling did.
#   * RESUME-SKIP PRECHECK (#101): batchStep runs one single-shot precheck
#     agent() call first; if it reports the fragment is already present and
#     valid (PRESENT), the codex dispatch + wait are SKIPPED. Any other
#     answer (ABSENT -- a missing OR corrupt fragment, since both fail the
#     same `--check-batch` command) falls THROUGH to a normal dispatch +
#     wait. Both halves are asserted by the mocked agent() CALL LABELS
#     directly, not merely the final result.
#   * PRE-MERGE CITATION REVIEW (1.16.0): under research_mode:live, every
#     attempt's fragment is audited by an independent reviewer BEFORE it
#     counts as ready; under offline the stage is a total no-op, because
#     canon_validate.py makes basis:"established" FATAL there -- so there is
#     provably no citation in existence to review.
#
#     SCOPE SPLIT -- this file owns the review only as a term in the COST
#     ARITHMETIC: since 1.16.1 it adds TWO calls per attempt (prepare + judge),
#     it is what makes the live ceiling 19 rather than 5, and it is why a
#     resume-skipped batch saves the dispatch + wait and never the review, so
#     the split widened the gap between what a resume saves and what a batch
#     costs rather than the saving itself. The review's BEHAVIOUR -- rejection regenerating to a
#     fresh attempt-scoped path, the resume-skip path reaching the gate at
#     all, a stale attempt-bound verdict failing to approve a later attempt,
#     exhaustion reporting its own reason instead of falling into the merge,
#     the reviewer's findings reaching the regeneration prompt -- is
#     tests/glossary_citation_review.test.py's subject, and is asserted there
#     against rendered prompt text this harness does not record.
#
# The glossary template drives a SINGLE-stage `pipeline(BATCHES, batchStep)`
# (not the mass template's two-stage pipeline), and uses its own agent()
# call labels (glossary:precheck:N / glossary:dispatch:N / glossary:wait:N /
# glossary:citation-prepare:N / glossary:citation-review:N, plus the
# batch-level glossary:merge / glossary:verify), so it needs its own
# instantiate helper + mock harness
# below; only `_wrap_for_execution` (owner-agnostic) is reused verbatim.
# ===========================================================================

GLOSSARY_PASS_TEMPLATE = TEMPLATES_DIR / "glossary-pass-wf.template.js"


def instantiate_glossary_pass(
    *,
    batch_agent_cap: int,
    durable_root: str = FIXTURE_DURABLE_ROOT,
    source_lang: str = FIXTURE_SOURCE_LANG,
    target_lang: str = FIXTURE_TARGET_LANG,
    research_mode: str = "live",
    run_id: str = "fixture-run-id",
) -> str:
    """Re-implements glossary-pass-wf.template.js's own one-time substitution
    contract (its header comment's token list), the glossary twin of
    instantiate_mass_translate above. Substitutes {{BATCH_AGENT_CAP}} as a
    BARE integer (feeding the preflight cost cap). The mock never inspects
    prompt text (only opts.label), so the exact string values are irrelevant
    beyond being syntactically valid."""
    text = GLOSSARY_PASS_TEMPLATE.read_text(encoding="utf-8")
    text = text.replace("{{DURABLE_ROOT}}", durable_root)
    text = text.replace("{{RUN_ID}}", run_id)
    text = text.replace("{{SOURCE_LANG}}", source_lang)
    text = text.replace("{{TARGET_LANG}}", target_lang)
    text = text.replace("{{RESEARCH_MODE}}", research_mode)
    text = text.replace("{{BATCH_AGENT_CAP}}", str(int(batch_agent_cap)))
    # #197 -- engine.effort. Not inspected by this file's call-counting
    # assertions; it only needs to resolve.
    text = text.replace("{{EFFORT}}", "high")
    # 1.16.1 (#347): empty = fetch_citation.py's shipped default list.
    text = text.replace("{{CITATION_CONTENT_TYPES}}", "")
    assert "{{" not in text, (
        "glossary fixture instantiation left an unresolved token -- fix the "
        "fixture, not the assertion"
    )
    return text


# ---------------------------------------------------------------------------
# Glossary node harness: single-stage pipeline, and a mock agent() driven by
# a per-batch PLAN keyed by str(index):
#   { "0": {"precheck": "PRESENT 0"},                       # -> resume-skip
#     "1": {"precheck": "ABSENT 1", "wait": "READY 1"} }    # -> dispatch
# Absent keys default to precheck "ABSENT <idx>" (fall through) and wait
# "READY <idx>" (fragment becomes ready). The batch-level glossary:merge /
# glossary:verify calls always succeed (verify returns {verified:true}).
#
# The citation prepare (1.16.1) ALWAYS reports its evidence ready and the
# citation review (1.16.0) ALWAYS approves, and there is deliberately no way to
# script a failure of either in this file. Every count below is therefore a
# first-attempt cost, which is what the cost estimator's own tests need; the
# retry ladder appears here only as the CEILING the preflight charges for,
# never as an executed path. Rejection, regeneration, exhaustion, the
# stale-verdict case, and (1.16.1) a prepare failure short-circuiting before the
# judge are glossary_citation_review.test.py's subject -- its harness records
# rendered prompt text, so it can assert what a regeneration actually carries
# forward, which this harness structurally cannot. That file also owns the one
# MEASURED exhaustion run (its test_live_worst_case_run_does_not_exceed_its_own_
# estimate), which is what pins the ceiling below from BELOW -- this file's
# harness always approves and so structurally cannot reach it. The two constants
# are made one fact by that file's own cross-file seam assertion,
# test_live_per_batch_ceiling_is_pinned_to_the_template_and_the_estimator_file,
# which reads GLOSSARY_LIVE_PER_BATCH_CEILING below out of this module.
# ---------------------------------------------------------------------------
GLOSSARY_HARNESS_TEMPLATE = r"""
'use strict';

__WRAPPED_SOURCE__

const PLAN = __PLAN_JSON__;
const BATCHES_ARGS = __BATCHES_JSON__;
const callsLog = [];
const logLines = [];
let pipelineCalled = false;
// Per-batch citation-review call counter. The review runs exactly once per
// attempt, so this doubles as the attempt number the verdict sentinel must
// name -- a verdict is a statement about ONE attempt path, not about a batch.
const reviewCounts = {};
// #347/1.16.1 -- the prepare step gets its OWN counter rather than sharing the
// review's. They run once each per attempt and so hold the same value on every
// path this file drives, but only because prepare always succeeds here: a
// prepare failure short-circuits before the judge, and a shared counter would
// then have the judge's sentinel naming the wrong attempt. Two counters keep
// the harness honest about a path it does not itself script.
const prepareCounts = {};

async function agent(promptText, opts) {
  opts = opts || {};
  const label = opts.label || "";
  callsLog.push({
    label: label,
    phase: opts.phase || null,
    effort: opts.effort || null,
    agentType: opts.agentType || null,
    hasSchema: !!opts.schema,
  });

  if (label === "glossary:merge") return "MERGED (mock)";
  if (label === "glossary:verify") return { verified: true };

  const parts = label.split(":");
  const kind = parts[1];
  const idx = parts[parts.length - 1];
  const p = PLAN[idx] || {};
  if (kind === "precheck") return (p.precheck !== undefined) ? p.precheck : ("ABSENT " + idx);
  if (kind === "dispatch") return "FRAGMENT " + idx;
  if (kind === "wait") return (p.wait !== undefined) ? p.wait : ("READY " + idx);
  if (kind === "citation-prepare") {
    // #347/1.16.1 -- the fetch step, split out of the reviewer so the agent that
    // decides what to fetch never reads a retrieved byte. It ALWAYS reports its
    // evidence ready here: an attempt whose prepare FAILS short-circuits before
    // the judge and spends 3 calls rather than 4, so scripting a failure would
    // measure a cheaper path than any count in this section is about. That path
    // is glossary_citation_review.test.py's
    // test_prepare_failure_can_exhaust_the_ladder_under_its_own_reason.
    const attempt = prepareCounts[idx] || 0;
    prepareCounts[idx] = attempt + 1;
    return "EVIDENCE_READY " + idx + " ATTEMPT " + attempt;
  }
  if (kind === "citation-review") {
    // ALWAYS approves this attempt. Scripting a rejection -- and with it the
    // retry ladder, exhaustion, and the stale-verdict case -- belongs to
    // glossary_citation_review.test.py, whose harness records rendered prompt
    // text and can therefore assert what a regeneration actually carries. This
    // file only needs the review to happen so the call ARITHMETIC is right.
    // The ordinal is tracked rather than hardcoded to 0 so the sentinel stays
    // attempt-correct by construction.
    const attempt = reviewCounts[idx] || 0;
    reviewCounts[idx] = attempt + 1;
    return "CITATIONS_OK " + idx + " ATTEMPT " + attempt;
  }
  throw new Error("glossary mock agent(): unrecognized label " + label);
}

async function pipeline(items, stage) {
  pipelineCalled = true;
  const out = [];
  for (const item of items) {
    out.push(await stage(item));
  }
  return out;
}

function log(msg) { logLines.push(String(msg)); }

(async () => {
  try {
    const result = await __workflowMain__(agent, pipeline, log, BATCHES_ARGS);
    process.stdout.write(JSON.stringify({
      result: result,
      calls: callsLog,
      log: logLines,
      pipelineCalled: pipelineCalled,
    }));
  } catch (err) {
    process.stderr.write("HARNESS_ERROR: " + (err && err.stack || String(err)) + "\n");
    process.exit(1);
  }
})();
"""


def build_glossary_harness(js_source: str, batches: list, plan: dict) -> str:
    wrapped = _wrap_for_execution(js_source)
    text = GLOSSARY_HARNESS_TEMPLATE.replace("__WRAPPED_SOURCE__", wrapped)
    text = text.replace("__PLAN_JSON__", json.dumps(plan))
    text = text.replace("__BATCHES_JSON__", json.dumps(batches))
    return text


def _glossary_batches(n: int) -> list:
    """n minimal, index-guard-legal glossary batches -- candidate content is
    irrelevant (the mock never reads prompt text)."""
    return [
        {"index": i, "candidates": [{"name": f"Cand{i}", "freq": 3, "likely_name": True}]}
        for i in range(n)
    ]


def run_glossary_workflow(
    *,
    tmp_path: Path,
    batch_agent_cap: int,
    batches: list,
    plan: dict,
    research_mode: str = "live",
    timeout: int = 30,
) -> dict:
    """`research_mode` is threaded straight into the template's own
    {{RESEARCH_MODE}} token, because it is no longer inert as of 1.16.0: it drives
    CITATION_REVIEW_ENABLED, and through it BOTH the preflight cost formula and
    whether the citation-review agent call happens at all. The default stays
    "live" -- the mode that pays the full ladder -- so a test that says nothing
    about research_mode is exercising the more expensive branch, never the
    cheaper one by accident."""
    assert NODE is not None, "node executable not found on PATH -- required to run this test file"
    js_source = instantiate_glossary_pass(
        batch_agent_cap=batch_agent_cap, research_mode=research_mode
    )
    harness_text = build_glossary_harness(js_source, batches, plan)
    harness_path = tmp_path / "glossary_harness.js"
    harness_path.write_text(harness_text, encoding="utf-8")

    proc = subprocess.run(
        [NODE, str(harness_path)],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if proc.returncode != 0:
        raise AssertionError(
            f"glossary harness execution failed (exit {proc.returncode}):\n"
            f"STDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
        )
    return json.loads(proc.stdout)


# ---------------------------------------------------------------------------
# Per-call ladder constants. These are DERIVED from the template's own
# preflight comment block (quoted in this section's banner above), never
# copied out of an observed run -- a count read back from the implementation's
# actual behaviour would agree with the implementation by construction and so
# test nothing. test_glossary_citation_retry_bound_is_the_documented_two below
# pins the one template knob every number here hangs off, so bumping it in the
# template fails loudly with a message that says to re-derive, instead of
# scattering unexplained off-by-N count failures across this section.
# ---------------------------------------------------------------------------
GLOSSARY_MAX_CITATION_RETRIES = 2          # template: const MAX_CITATION_RETRIES
GLOSSARY_MAX_ATTEMPTS = GLOSSARY_MAX_CITATION_RETRIES + 1          # 3
# 1.16.2 (#352): what ONE wait costs in agent calls, worst case. The template
# derives it as WAIT_CHUNKS + 1 == ceil(WAIT_BOUND_SEC / WAIT_CHUNK_SEC) + 1 ==
# ceil(900/480) + 1 == 3: two bounded poll chunks, then one authoritative
# non-polling re-check. Restated here as an independent literal, like every
# other constant in this block -- re-deriving it from the template would make
# the ceiling agree with the template for any self-consistent pair of wait
# constants. test_glossary_wait_calls_term_is_the_template_own_chunk_count
# below is the tripwire that ties this literal back to the shipped template.
GLOSSARY_WAIT_CALLS = 3
# per batch, worst case: precheck 1 + attempts * (dispatch 1 + wait WAIT_CALLS +
# citation prepare 1 + citation judge 1). The per-attempt term became 4 in
# 1.16.1 (#347 split the reviewer into prepare + judge) and 6 in 1.16.2 (#352
# made one wait WAIT_CALLS agent calls instead of one).
GLOSSARY_LIVE_PER_ATTEMPT = 3 + GLOSSARY_WAIT_CALLS                # 6
GLOSSARY_LIVE_PER_BATCH_CEILING = (
    1 + GLOSSARY_LIVE_PER_ATTEMPT * GLOSSARY_MAX_ATTEMPTS
)                                                                  # 19
# per batch, offline: precheck 1 + the single dispatch 1 + wait WAIT_CALLS. The
# review is a no-op there, which ALSO removes the only thing that can reject an
# attempt -- so the ladder can never advance past attempt 0, and this term stays
# ladder-free. It is a separate constant because it moves for DIFFERENT reasons
# than the live one: #347's reviewer split did not touch it (there is no
# reviewer to split under offline), while #352's chunked wait did (the Bash
# per-call clamp is indifferent to research_mode).
GLOSSARY_OFFLINE_PER_BATCH = 2 + GLOSSARY_WAIT_CALLS               # 5
# per RUN, either mode: the serialized merge call + the disk-independent verify
GLOSSARY_FIXED_MERGE_VERIFY = 2
# per batch, live, when the review approves on the first attempt AND the very
# first wait chunk finds the fragment (the happy path every plan in this file
# takes unless it scripts a REJECT or a PENDING chunk):
#   precheck 1 + attempt0 (dispatch 1 + wait 1 + prepare 1 + judge 1) 4
# UNCHANGED by #352, and that is the point rather than an oversight: a wait that
# is answered by its first chunk still costs ONE call. WAIT_CALLS is a ceiling,
# paid only by a wait that exhausts every chunk and still needs the re-check.
GLOSSARY_LIVE_PER_BATCH_APPROVED_FIRST = 1 + 4                     # 5
# per batch, live, when the precheck resume-skips a valid fragment: precheck 1 +
# (prepare + judge) 2. The skip drops the dispatch and the wait; it is NOT
# exempt from the review, which is exactly why the saving is 2 and not 4.
GLOSSARY_LIVE_PER_BATCH_RESUME_SKIPPED = 1 + 2                     # 3


def test_glossary_citation_retry_bound_is_the_documented_two():
    """Every expected call count in this section is derived from
    MAX_CITATION_RETRIES, so it must not drift silently. Read straight out of
    the template text rather than inferred from a run."""
    text = GLOSSARY_PASS_TEMPLATE.read_text(encoding="utf-8")
    assert f"const MAX_CITATION_RETRIES = {GLOSSARY_MAX_CITATION_RETRIES}" in text, (
        "the template's MAX_CITATION_RETRIES no longer matches this section's "
        "GLOSSARY_MAX_CITATION_RETRIES -- RE-DERIVE every count in this "
        "section from the template's own preflight ladder comment, do not "
        "just patch the numbers until the suite goes green"
    )


def test_glossary_live_per_attempt_term_is_the_template_own_multiplier():
    """The OTHER knob every live count here hangs off: how many calls one
    attempt costs. #347 moved it from 3 to 4 (the reviewer split into prepare +
    judge), #352 moved it from 4 to 6 (one wait became WAIT_CALLS calls), and
    nothing in this file would have noticed either time -- the ceiling is a
    Python arithmetic expression, so a template that went back to 4, or on to 7,
    would leave every assertion below internally consistent and wrong.

    The authoritative comparison of the three copies (template expression, this
    file's constant, glossary_citation_review.test.py's) is that file's
    test_live_per_batch_ceiling_is_pinned_to_the_template_and_the_estimator_file,
    which imports this module and reads the constant out of it. This assertion
    is the local tripwire, and it is not redundant with the seam: it names the
    per-attempt MULTIPLIER specifically, so a drift reports as "the ladder
    changed shape" here rather than only as "two totals disagree" there.

    Matched against the template's SYMBOLIC expression (`3 + WAIT_CALLS`), not
    against a rendered `6`, because the template deliberately writes the ladder
    in terms of its own wait constants -- pinning a literal here would fail on
    the shipped source while the arithmetic was perfectly correct."""
    text = GLOSSARY_PASS_TEMPLATE.read_text(encoding="utf-8")
    per_attempt, remainder = divmod(
        GLOSSARY_LIVE_PER_BATCH_CEILING - 1, GLOSSARY_MAX_ATTEMPTS
    )
    # Carries its own message rather than being a bare assert: this fires FIRST,
    # so a bare one would report a stale ceiling as an unexplained AssertionError
    # and bury the very diagnostic this test exists to give.
    assert remainder == 0 and per_attempt == 6, (
        f"GLOSSARY_LIVE_PER_BATCH_CEILING ({GLOSSARY_LIVE_PER_BATCH_CEILING}) is no "
        f"longer 1 precheck + {GLOSSARY_MAX_ATTEMPTS} attempts * 6 calls. Either the "
        f"ladder changed and every count in this section needs re-deriving from the "
        f"template's preflight comment and from the labels a real run emits, or the "
        f"constant was patched to silence a failure -- which is the move this test "
        f"exists to stop"
    )
    assert " ".join("1 + (3 + WAIT_CALLS) * (MAX_CITATION_RETRIES + 1)".split()) in " ".join(text.split()), (
        f"the template's live perBatchCalls expression is no longer "
        f"`1 + (3 + WAIT_CALLS) * (MAX_CITATION_RETRIES + 1)`. An attempt now costs a "
        f"different number of calls than the {per_attempt} (dispatch 1 + wait "
        f"{GLOSSARY_WAIT_CALLS} + citation prepare 1 + citation judge 1) this "
        f"section's GLOSSARY_LIVE_PER_BATCH_CEILING is built from -- RE-DERIVE the "
        f"ladder from the template's own preflight comment and from the labels a "
        f"real run emits, do not patch the constant until the suite goes green"
    )


def test_glossary_wait_calls_term_is_the_template_own_chunk_count():
    """1.16.2 (#352): the third knob, new in this release. GLOSSARY_WAIT_CALLS
    feeds BOTH the live ceiling and the offline term, and it is the one number
    in this section that is not visible anywhere in the template's rendered
    output -- it is computed from two constants and a ceil-div. A template that
    raised WAIT_CHUNK_SEC to 900 (one chunk, WAIT_CALLS 2) or dropped it to 300
    (three chunks, WAIT_CALLS 4) would leave every count in this section
    internally consistent and wrong in both directions.

    Re-derives WAIT_CALLS from the template's OWN declared constants, so it
    fails on the drift rather than on the total. That is a different question
    from the one wait_chunking_batch_passes.test.py asks: this one is
    "does the estimator's input still match the template's declaration", that
    one is "does the template's declaration still match what it EMITS"."""
    text = GLOSSARY_PASS_TEMPLATE.read_text(encoding="utf-8")
    declared = {}
    for name in ("WAIT_BOUND_SEC", "WAIT_CHUNK_SEC"):
        m = re.search(rf"^const {name} = (\d+)", text, re.MULTILINE)
        assert m is not None, f"the template no longer declares a const {name}"
        declared[name] = int(m.group(1))

    chunks = -(-declared["WAIT_BOUND_SEC"] // declared["WAIT_CHUNK_SEC"])  # ceil-div
    assert chunks + 1 == GLOSSARY_WAIT_CALLS, (
        f"the template's wait constants (WAIT_BOUND_SEC={declared['WAIT_BOUND_SEC']}, "
        f"WAIT_CHUNK_SEC={declared['WAIT_CHUNK_SEC']}) now imply {chunks} chunk(s) + 1 "
        f"re-check == {chunks + 1} calls per wait, not this section's "
        f"GLOSSARY_WAIT_CALLS ({GLOSSARY_WAIT_CALLS}). Both the live ceiling and the "
        f"offline term are built on it -- RE-DERIVE them, do not patch the constant"
    )
    # The template must still compute it the same way, not merely happen to
    # agree at these values: a hard-coded `const WAIT_CALLS = 3` would satisfy
    # the arithmetic above and then silently stop tracking WAIT_CHUNK_SEC.
    assert " ".join("const WAIT_CHUNKS = Math.ceil(WAIT_BOUND_SEC / WAIT_CHUNK_SEC)".split()) in " ".join(text.split()), (
        "the template no longer derives WAIT_CHUNKS from its own bound and chunk size"
    )
    assert " ".join("const WAIT_CALLS = WAIT_CHUNKS + 1".split()) in " ".join(text.split()), (
        "the template no longer derives WAIT_CALLS as WAIT_CHUNKS + 1 (the chunks "
        "plus the one authoritative re-check)"
    )


# ---------------------------------------------------------------------------
# Preflight cost cap (#95, re-derived for 1.16.0's citation-review ladder,
# again for 1.16.1's prepare/judge split, #347, and again for 1.16.2's chunked
# wait, #352).
#
#   live    -- perBatch = 1 + (3 + WAIT_CALLS)*(MAX_CITATION_RETRIES+1) = 19
#   offline -- perBatch = 2 + WAIT_CALLS = 5
#   estimatedCalls = perBatch * N + 2
# ---------------------------------------------------------------------------


def test_glossary_preflight_boundary_exactly_at_cap_permits_dispatch(tmp_path):
    batches = _glossary_batches(2)
    # Derivation (live): perBatch = precheck 1 + 3 attempts * (dispatch 1 +
    # wait 3 + citation prepare 1 + citation judge 1) 6 = 19; 2 batches = 38;
    # + merge/verify 2 = 40.
    estimated = GLOSSARY_LIVE_PER_BATCH_CEILING * len(batches) + GLOSSARY_FIXED_MERGE_VERIFY
    assert estimated == 40

    out = run_glossary_workflow(
        tmp_path=tmp_path,
        batch_agent_cap=estimated,
        batches=batches,
        plan={},  # every batch default: precheck ABSENT, wait READY, review OK
    )

    assert out["pipelineCalled"] is True, (
        "estimatedCalls == cap must NOT trip the gate (the check is '>', not '>=')"
    )
    assert out["result"]["merged"] is True
    # OBSERVED cost, which is no longer the estimate: as of 1.16.0 the live
    # estimate is a worst-case CEILING (every review rejects until the ladder
    # is exhausted), while this plan's reviews all approve on attempt 0. As of
    # 1.16.2 it is also a ceiling on the WAIT: this plan's first wait chunk
    # answers READY, so the wait costs 1 call rather than WAIT_CALLS.
    # Derivation: per batch precheck 1 + attempt0 dispatch/wait/prepare/judge
    # 4 = 5; 2 batches = 10; + merge/verify 2 = 12.
    expected_observed = (
        GLOSSARY_LIVE_PER_BATCH_APPROVED_FIRST * len(batches) + GLOSSARY_FIXED_MERGE_VERIFY
    )
    assert expected_observed == 12
    assert len(out["calls"]) == expected_observed
    # ...and the ceiling really does bound it, which is the whole point of the
    # preflight refusing on the ceiling rather than on the happy-path cost.
    assert len(out["calls"]) <= estimated


def test_glossary_preflight_one_below_boundary_blocks_dispatch_entirely(tmp_path):
    batches = _glossary_batches(2)
    # Same live derivation as above: 19*2 + 2 = 40.
    estimated = GLOSSARY_LIVE_PER_BATCH_CEILING * len(batches) + GLOSSARY_FIXED_MERGE_VERIFY
    assert estimated == 40

    out = run_glossary_workflow(
        tmp_path=tmp_path,
        batch_agent_cap=estimated - 1,
        batches=batches,
        plan={},
    )

    assert out["pipelineCalled"] is False, "pipeline() must never run once the batch is judged too large"
    assert out["calls"] == [], "zero real agent() calls once the gate trips -- it must return before any dispatch"
    assert out["result"] == {
        "merged": False,
        "reason": "batch-too-large",
        "estimatedCalls": estimated,
        "cap": estimated - 1,
    }
    assert any("Batch too large" in line and str(estimated) in line for line in out["log"])


@pytest.mark.parametrize("n_batches", [1, 2, 5, 13])
def test_glossary_preflight_live_formula_is_19_batches_plus_2(tmp_path, n_batches):
    """Locks the LIVE formula (1 + (3+WAIT_CALLS)*(MAX_CITATION_RETRIES+1))*N + 2
    == 19*N + 2.

    Six wrong variants this discriminates against, each a plausible partial
    implementation, with the per-batch term spelled out so the arithmetic can be
    checked rather than taken on faith:
      * the historical pre-1.16.0 estimate, review and ladder both uncharged:
        1 precheck + 1 dispatch + 1 wait                      = 3  -> 3*N + 2
      * the retry ladder charged but the review itself not:
        1 + (MAX_CITATION_RETRIES+1) * (dispatch + wait) = 1 + 3*2 = 7  -> 7*N + 2
      * the review charged ONCE rather than per attempt:
        1 + 3*2 + 1                                           = 8  -> 8*N + 2
      * the 1.16.0 figure -- the full ladder, but the reviewer still counted as
        ONE call per attempt rather than #347's prepare + judge pair:
        1 + 3*(dispatch + wait + review) = 1 + 3*3            = 10 -> 10*N + 2
      * the 1.16.1 figure -- the split reviewer, but the wait still counted as
        ONE call rather than #352's WAIT_CALLS:
        1 + 3*(dispatch + wait + prepare + judge) = 1 + 3*4   = 13 -> 13*N + 2
      * #352's chunks charged but its authoritative re-check not (WAIT_CALLS
        read as WAIT_CHUNKS): 1 + 3*(1 + 2 + 2) = 1 + 3*5     = 16 -> 16*N + 2
    Only the chunked ladder with its re-check gives 19. The two the release can
    actually regress into are the last two: 13 is what this file asserted before
    #352, so any count left un-re-derived lands exactly there, and 16 is what
    dropping the re-check from the wait term gives -- which is the term the
    whole fix turns on. Cheap: the gate trips before pipeline() ever runs, so no
    PLAN and zero agent calls are needed."""
    batches = _glossary_batches(n_batches)
    # Derivation: perBatch = precheck 1 + 3 attempts * (dispatch 1 + wait 3 +
    # citation prepare 1 + citation judge 1) 6 = 19, plus the fixed merge +
    # verify pair 2.
    expected = GLOSSARY_LIVE_PER_BATCH_CEILING * n_batches + GLOSSARY_FIXED_MERGE_VERIFY
    assert expected == {1: 21, 2: 40, 5: 97, 13: 249}[n_batches]

    out = run_glossary_workflow(
        tmp_path=tmp_path,
        batch_agent_cap=expected - 1,
        batches=batches,
        plan={},
    )

    assert out["pipelineCalled"] is False
    assert out["calls"] == []
    assert out["result"]["reason"] == "batch-too-large"
    assert out["result"]["estimatedCalls"] == expected
    assert out["result"]["cap"] == expected - 1


@pytest.mark.parametrize("n_batches", [1, 2, 5, 13])
def test_glossary_preflight_offline_formula_is_5_batches_plus_2(tmp_path, n_batches):
    """THE LADDER-FREE GUARANTEE for offline projects, which is what survives
    1.16.2 -- not the historical NUMBER, which does not.

    The citation review is a provable no-op under offline (canon_validate.py
    makes basis:"established" FATAL there, so there is no citation in existence
    to review), and with no reviewer there is nothing that can reject an
    attempt. Charging offline for a retry ladder it can never execute would
    start refusing runs whose real cost did not change at all -- a preflight
    that refuses runs it should permit is a worse failure than one that is
    slightly loose. That is the invariant, and the formula below still holds it:
    exactly ONE dispatch and ONE wait, never (MAX_CITATION_RETRIES+1) of them.

    What DID move is the wait term, 1 -> WAIT_CALLS, so the per-batch total went
    3 -> 5. That is not the principle being abandoned, it is the same principle
    applied: the extra wait calls are cost every offline run must be
    preflight-charged for on every batch, as a worst-case CEILING, not a claim
    that every batch actually spends them -- a wait whose first chunk finds the
    fragment pays 1 call, not WAIT_CALLS, offline exactly as live -- because
    #352's Bash per-call clamp is indifferent to research_mode. Leaving offline
    at 3*N+2 would be an UNDER-count -- the dangerous direction, since it
    admits a run that then blows engine.batch_agent_cap mid-flight instead of
    refusing it early and loudly.

    So this test discriminates against two opposite regressions at once: an
    offline branch that grew a ladder (which would give 1 + 3*4 = 13 or worse),
    and an offline branch left at the stale flat 3. Without it nothing in the
    suite would notice either: every other preflight test here runs under
    live."""
    batches = _glossary_batches(n_batches)
    # Derivation (offline): perBatch = precheck 1 + the single dispatch 1 +
    # wait WAIT_CALLS 3 = 5 (no review, and therefore no retry ladder), plus
    # the fixed merge + verify pair 2.
    expected = GLOSSARY_OFFLINE_PER_BATCH * n_batches + GLOSSARY_FIXED_MERGE_VERIFY
    assert expected == 5 * n_batches + 2
    assert expected == {1: 7, 2: 12, 5: 27, 13: 67}[n_batches]
    # The ladder-free half, stated as its own fact rather than left implicit in
    # the total: offline must charge for exactly ONE attempt. A ladder here
    # would multiply the same per-attempt term the live branch uses.
    assert GLOSSARY_OFFLINE_PER_BATCH == 1 + (1 + GLOSSARY_WAIT_CALLS), (
        "the offline term must stay precheck + ONE (dispatch + wait), never a "
        "ladder -- an offline run has no reviewer and so can never reach attempt 1"
    )

    out = run_glossary_workflow(
        tmp_path=tmp_path,
        batch_agent_cap=expected - 1,
        batches=batches,
        plan={},
        research_mode="offline",
    )

    assert out["pipelineCalled"] is False
    assert out["calls"] == []
    assert out["result"]["reason"] == "batch-too-large"
    assert out["result"]["estimatedCalls"] == expected
    assert out["result"]["cap"] == expected - 1


# The BEHAVIOUR half of the offline guarantee -- that an offline run spends no
# citation-review call at all -- lives in
# tests/glossary_citation_review.test.py::test_offline_mode_spends_no_review_call.
# The two are genuinely different assertions, not one test written twice, and
# each catches a bug the other misses: an estimate left at the old flat term
# while the stage still runs passes THIS test and fails that one; a mode-blind
# estimate over a stage that correctly no-ops fails THIS test and passes that
# one. Both were confirmed by scoped template mutation, in both directions.


# ---------------------------------------------------------------------------
# Resume-skip precheck (#101): a valid pre-existing fragment is TRUSTED
# (dispatch + wait skipped); a missing/corrupt fragment falls THROUGH.
# ---------------------------------------------------------------------------


def test_glossary_resume_skip_trusts_valid_fragment_and_skips_dispatch(tmp_path):
    batches = _glossary_batches(1)
    out = run_glossary_workflow(
        tmp_path=tmp_path,
        batch_agent_cap=10_000,
        batches=batches,
        plan={"0": {"precheck": "PRESENT 0"}},
    )

    labels = [c["label"] for c in out["calls"]]
    assert "glossary:precheck:0" in labels
    assert "glossary:dispatch:0" not in labels, (
        "a valid pre-existing fragment must skip the (expensive) codex dispatch"
    )
    assert "glossary:wait:0" not in labels, (
        "a valid pre-existing fragment must skip the wait poll too"
    )
    assert out["result"]["merged"] is True
    assert out["result"]["batches"][0]["ready"] is True
    assert out["result"]["batches"][0]["batchIndex"] == 0
    # Derivation (live, 1.16.1): precheck 1 + merge 1 + verify 1 = 3, PLUS the
    # citation review's TWO calls (prepare + judge) -- the resume-skip saves the
    # dispatch and the wait, but it is NOT exempt from the review. That the
    # review is REACHABLE from this path at all is load-bearing rather than
    # incidental (a stale, unreviewed fragment already on disk is precisely the
    # run a review placed only after dispatch/wait would bypass), and it is
    # asserted in tests/glossary_citation_review.test.py, by
    # test_resume_skipped_fragment_is_still_citation_reviewed; here the review
    # is only the +2 in the count. = 5.
    assert len(out["calls"]) == GLOSSARY_LIVE_PER_BATCH_RESUME_SKIPPED + GLOSSARY_FIXED_MERGE_VERIFY
    assert len(out["calls"]) == 5
    # The saving is stated as a saving, not just as a total: #347 raised what a
    # fresh batch costs, and a resume that quietly stopped skipping anything
    # would still satisfy the equality above once the constants moved with it.
    # OBSERVED saving, so it is 2 (dispatch + one answered wait chunk) and not
    # 1 + WAIT_CALLS: the ceiling's version of this saving is 4, and the two are
    # different facts about the same skip -- see the ladder constants above.
    assert (
        GLOSSARY_LIVE_PER_BATCH_APPROVED_FIRST - GLOSSARY_LIVE_PER_BATCH_RESUME_SKIPPED == 2
    ), "the resume-skip must save exactly the dispatch + wait pair, and nothing else"


def test_glossary_resume_precheck_absent_falls_through_to_real_dispatch(tmp_path):
    """A missing OR corrupt fragment both fail the precheck's own
    `--check-batch` command (the Python half of that rejection is
    glossary_fragment_merge.test.py's malformed-JSON case), so the template
    sees ABSENT and must dispatch for real."""
    batches = _glossary_batches(1)
    out = run_glossary_workflow(
        tmp_path=tmp_path,
        batch_agent_cap=10_000,
        batches=batches,
        plan={"0": {"precheck": "ABSENT 0", "wait": "READY 0"}},
    )

    labels = [c["label"] for c in out["calls"]]
    assert "glossary:precheck:0" in labels
    assert "glossary:dispatch:0" in labels, (
        "a missing/corrupt fragment must fall through to a real codex dispatch"
    )
    assert "glossary:wait:0" in labels
    assert out["result"]["merged"] is True
    # Derivation (live, 1.16.1): precheck 1 + attempt0
    # dispatch/wait/prepare/judge 4 = 5, + merge/verify 2 = 7.
    assert len(out["calls"]) == (
        GLOSSARY_LIVE_PER_BATCH_APPROVED_FIRST + GLOSSARY_FIXED_MERGE_VERIFY
    )
    assert len(out["calls"]) == 7


def test_glossary_resume_skip_is_decided_per_batch(tmp_path):
    """One batch resume-skipped, its neighbour freshly dispatched -- the skip
    decision is per-batch, never all-or-nothing."""
    batches = _glossary_batches(2)
    out = run_glossary_workflow(
        tmp_path=tmp_path,
        batch_agent_cap=10_000,
        batches=batches,
        plan={
            "0": {"precheck": "PRESENT 0"},                    # skip
            "1": {"precheck": "ABSENT 1", "wait": "READY 1"},  # dispatch
        },
    )

    labels = [c["label"] for c in out["calls"]]
    assert "glossary:dispatch:0" not in labels
    assert "glossary:wait:0" not in labels
    assert "glossary:dispatch:1" in labels
    assert "glossary:wait:1" in labels
    assert out["result"]["merged"] is True
    # Derivation (live, 1.16.1): batch0 precheck 1 + review 2 = 3 (skipped its
    # dispatch + wait, but not its review); batch1 precheck 1 + attempt0
    # dispatch/wait/prepare/judge 4 = 5; + merge/verify 2 == 10.
    assert len(out["calls"]) == (
        GLOSSARY_LIVE_PER_BATCH_RESUME_SKIPPED
        + GLOSSARY_LIVE_PER_BATCH_APPROVED_FIRST
        + GLOSSARY_FIXED_MERGE_VERIFY
    )
    assert len(out["calls"]) == 10
