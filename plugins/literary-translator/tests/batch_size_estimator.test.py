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
    unresolved on purpose and simply never asserted against."""
    text = MASS_TRANSLATE_TEMPLATE.read_text(encoding="utf-8")
    text = text.replace("{{DURABLE_ROOT}}", durable_root)
    text = text.replace("{{RUN_ID}}", "fixture-run-id")
    text = text.replace("{{SOURCE_LANG}}", source_lang)
    text = text.replace("{{TARGET_LANG}}", target_lang)
    text = text.replace("{{MAX_FIX_ROUNDS}}", str(int(max_fix_rounds)))
    text = text.replace("{{BATCH_AGENT_CAP}}", str(int(batch_agent_cap)))
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
#   "wait": <translate's own wait:* response, e.g. "READY seg"/"TIMEOUT seg">,
#   "reviewWaits": [<one review-wait:* response per review point, in round
#                     order -- NOT per retry; the shared retry re-runs only
#                     read+check, never dispatch/wait>, ...],
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
  if (label.indexOf("wait:") === 0) return (PLAN[seg] || {}).wait;
  if (label.indexOf("review-dispatch:") === 0) return "DISPATCHED " + seg + " d4e5f6";
  if (label.indexOf("review-wait:") === 0) {
    const q = queues[seg].reviewWaits;
    if (q.length === 0) throw new Error("PLAN reviewWaits queue exhausted for " + seg + " label=" + label);
    return q.shift();
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
# Response-object builders -- shapes matching REVIEW_SCHEMA / REVIEW_ARTIFACT_
# SCHEMA closely enough to drive the real script's own branching
# (`rev.clean`, `rev.coverage_ok`, `rev.findings`, `art.match`,
# `"mismatch_detail" in art`, matching `artifactCheckMatched()`'s exact
# check); this harness never itself validates against the JSON schemas
# (that is the real Workflow tool's job, out of scope here) -- only
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


def converged_worst_case_plan(seg: str, max_fix_rounds: int, *, final_clean: bool) -> dict:
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
    of the template's own comment calling this one combined branch."""
    review_waits: list = []
    reviews: list = []
    artifact_checks: list = []
    fixes: list = []

    for i in range(1, max_fix_rounds + 1):
        review_waits.append(f"READY {seg}")
        reviews.append(review_obj(clean=False))
        artifact_checks.append(match_false(f"round {i} first attempt mismatch"))
        reviews.append(review_obj(clean=False))
        artifact_checks.append(match_true())
        fixes.append(f"FIXED {seg} r{i}")

    # Final confirming round -- also forced through the shared retry (worst
    # case); no fix call follows it regardless of clean/non-clean outcome.
    review_waits.append(f"READY {seg}")
    reviews.append(review_obj(clean=False))
    artifact_checks.append(match_false("final round first attempt mismatch"))
    reviews.append(review_obj(clean=final_clean, coverage_ok=True))
    artifact_checks.append(match_true())

    return {
        "wait": f"READY {seg}",
        "reviewWaits": review_waits,
        "reviews": reviews,
        "artifactChecks": artifact_checks,
        "fixes": fixes,
    }


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
        # getVerifiedReview's own bounded review-wait poll times out on the
        # very first attempt -- 2 calls (dispatch + wait); the read/check/
        # fix machinery is never reached at all. #131 facet B -- NO terminal
        # ledger write (recoverable).
        review_waits.append(f"TIMEOUT {seg}")
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
    """The translator never delivers READY in time -- reviewFixLoop's own
    `ready.indexOf("READY") === -1` check fires on the very first wait call,
    before a single review call is ever made."""
    return {"wait": f"TIMEOUT {seg}", "reviewWaits": [], "reviews": [], "artifactChecks": [], "fixes": []}


def blocked_branch_total(max_fix_rounds: int, terminating_cost: int, *, ledger_write: bool = True) -> int:
    """3 (fixed) + 7*(max_fix_rounds-1) (completed WORST-CASE-RECOVERED
    normal rounds -- review point with a forced shared retry (6) + fix (1)
    == 7 each, matching the estimator's own per-round assumption) +
    terminating_cost + (1 if ledger_write else 0) (terminal ledger write --
    #131 SKIPS this write entirely for every transient/recoverable
    terminating reason: review-null, review-artifact-mismatch,
    review-timeout, review-fabricated-loc, and fix-call-failed. Only
    draft-missing, a genuine anomaly, still writes -- `ledger_write`
    defaults to True/unchanged for callers that don't pass it."""
    return 3 + 7 * (max_fix_rounds - 1) + terminating_cost + (1 if ledger_write else 0)


def converged_branch_total(max_fix_rounds: int) -> int:
    """The converged/non-converged-at-cap branch total: 3 (fixed) +
    7*max_fix_rounds (all MAXFIX normal rounds, each a 6-call worst-case
    review point + 1 fix) + 6 (final confirming review point, worst case,
    no fix) + 1 (terminal ledger write) == 3*... == 10 + 7*max_fix_rounds,
    exactly the `10 + 7*MAXFIX` per-segment term inside estimatedCalls."""
    return 3 + 7 * max_fix_rounds + 6 + 1


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
    estimated = 1 + len(segs) * (10 + 7 * max_fix_rounds)  # 1 + 2*24 = 49

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

    # The real total number of agent() calls made must equal the formula's
    # own estimate exactly at this configuration -- not merely be "close".
    assert len(out["calls"]) == estimated

    per_seg, batch_level = bucket_calls_by_segment(out["calls"])
    assert len(batch_level) == 1, "exactly one mandatory batch-level mergeLedgerPrompt call"
    for seg in segs:
        assert len(per_seg[seg]) == converged_branch_total(max_fix_rounds)
        assert len(per_seg[seg]) == 10 + 7 * max_fix_rounds


def test_estimator_one_below_boundary_blocks_dispatch_entirely(tmp_path):
    max_fix_rounds = 2
    segs = ["seg01", "seg02"]
    estimated = 1 + len(segs) * (10 + 7 * max_fix_rounds)  # 49

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
    assert len(per_seg[seg]) == blocked_branch_total(max_fix_rounds, terminating_cost=2, ledger_write=False)


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
    # 1 in_progress ledger write + 1 translate call + 1 wait call == 3,
    # independent of max_fix_rounds. #131 facet C: NO terminal
    # "ledger:timeout:*" write anymore -- the segment stays in_progress and
    # recoverable instead of a terminal non_converged/translate-timeout.
    assert len(per_seg[seg]) == 3
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
    per_segment_bound = 10 + 7 * max_fix_rounds  # the exact term estimatedCalls sizes per segment

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
    expected = 1 + n_segs * (10 + 7 * max_fix_rounds)

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
# GLOSSARY-PASS TEMPLATE (issues #101, #95) -- same extract-substitute-wrap-
# run-under-node harness mechanism as the mass-translate section above, but
# for glossary-pass-wf.template.js's own, distinct control flow:
#
#   * PREFLIGHT COST CAP (#95): `estimatedCalls = 3*BATCHES.length + 2`
#     (per batch: precheck + dispatch + wait == 3 worst case, plus the fixed
#     merge + verify pair == 2). If it exceeds engine.batch_agent_cap the
#     whole run is refused WITHOUT calling pipeline(), mirroring the mass
#     template's `{merged:false, reason:"batch-too-large", ...}` shape. The
#     `3*` (not the round-1 `2*`) is the corrected worst case -- a fresh run
#     with no fragments on disk pays the precheck AND the dispatch AND the
#     wait for every batch.
#   * RESUME-SKIP PRECHECK (#101): batchStep runs one single-shot precheck
#     agent() call first; if it reports the fragment is already present and
#     valid (PRESENT), the codex dispatch + wait are SKIPPED and the batch
#     is returned ready straight away. Any other answer (ABSENT -- a missing
#     OR corrupt fragment, since both fail the same `--check-batch` command)
#     falls THROUGH to a normal dispatch + wait. Both halves are asserted by
#     the mocked agent() CALL LABELS directly, not merely the final result.
#
# The glossary template drives a SINGLE-stage `pipeline(BATCHES, batchStep)`
# (not the mass template's two-stage pipeline), and uses its own agent()
# call labels (glossary:precheck:N / glossary:dispatch:N / glossary:wait:N,
# plus the batch-level glossary:merge / glossary:verify), so it needs its
# own instantiate helper + mock harness below; only `_wrap_for_execution`
# (owner-agnostic) is reused verbatim.
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
# ---------------------------------------------------------------------------
GLOSSARY_HARNESS_TEMPLATE = r"""
'use strict';

__WRAPPED_SOURCE__

const PLAN = __PLAN_JSON__;
const BATCHES_ARGS = __BATCHES_JSON__;
const callsLog = [];
const logLines = [];
let pipelineCalled = false;

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
    timeout: int = 30,
) -> dict:
    assert NODE is not None, "node executable not found on PATH -- required to run this test file"
    js_source = instantiate_glossary_pass(batch_agent_cap=batch_agent_cap)
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
# Preflight cost cap (#95): the corrected 3*BATCHES.length + 2 boundary.
# ---------------------------------------------------------------------------


def test_glossary_preflight_boundary_exactly_at_cap_permits_dispatch(tmp_path):
    batches = _glossary_batches(2)
    estimated = 3 * len(batches) + 2  # 8

    out = run_glossary_workflow(
        tmp_path=tmp_path,
        batch_agent_cap=estimated,
        batches=batches,
        plan={},  # every batch default: precheck ABSENT, wait READY
    )

    assert out["pipelineCalled"] is True, (
        "estimatedCalls == cap must NOT trip the gate (the check is '>', not '>=')"
    )
    assert out["result"]["merged"] is True
    # A fresh run (no fragment present) pays precheck+dispatch+wait (3) per
    # batch plus the fixed merge+verify pair (2) -- the estimate EXACTLY.
    assert len(out["calls"]) == estimated


def test_glossary_preflight_one_below_boundary_blocks_dispatch_entirely(tmp_path):
    batches = _glossary_batches(2)
    estimated = 3 * len(batches) + 2  # 8

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
def test_glossary_preflight_formula_is_3_batches_plus_2(tmp_path, n_batches):
    """Locks the CORRECTED formula 3*N + 2 (round-1's 2*N + 2 would compute a
    different estimatedCalls and fail here). Cheap: the gate trips before
    pipeline() ever runs, so no PLAN and zero agent calls are needed."""
    batches = _glossary_batches(n_batches)
    expected = 3 * n_batches + 2

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
    # precheck (1) + merge (1) + verify (1) -- no dispatch, no wait.
    assert len(out["calls"]) == 3


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
    # precheck (1) + dispatch (1) + wait (1) + merge (1) + verify (1).
    assert len(out["calls"]) == 5


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
    # batch0 precheck (1) + batch1 precheck+dispatch+wait (3) + merge+verify (2).
    assert len(out["calls"]) == 6
