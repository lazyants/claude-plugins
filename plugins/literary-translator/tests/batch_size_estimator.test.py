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
own "batch_agent_cap preflight" comment, right above the `pipeline()` call):

    const estimatedCalls = 1 + SEGS.length * (6 + 3 * MAXFIX);
    if (estimatedCalls > BATCH_AGENT_CAP) {
      log(...);
      return { converged: [], failed: [], reason: "batch-too-large",
               estimatedCalls: estimatedCalls, cap: BATCH_AGENT_CAP };
    }

The formula comes from enumerating every mutually exclusive per-segment
branch (orchestration-and-batching.md's own derivation, restated here only
to anchor the fixtures below -- this file does not re-derive it):

  - every segment, unconditionally: 3 fixed calls (ledger in_progress write,
    translate call, wait/poll call).
  - timeout branch: +1 ledger write -> 4 total, no review ever happens.
  - blocked branch: up to `max_fix_rounds - 1` completed NORMAL rounds (3
    calls each: review + artifact-check + fix), then ONE terminating round
    whose cost depends on which of three mutually exclusive sub-cases fires
    (`review-null` -> 2, `draft-missing` -> 3, `review-artifact-mismatch`
    -> 4 -- the largest), then +1 ledger write.
  - converged / non-converged-at-cap branch: the full `max_fix_rounds`
    normal rounds (3 calls each, no early clean exit) + 1 final confirming
    review + its own artifact-check (2 calls) + 1 ledger write -- this is
    the TRUE per-segment maximum, `3*max_fix_rounds + 6`, which is exactly
    what `6 + 3*MAXFIX` in the formula above encodes.

This file does not re-implement any of that arithmetic in Python and trust
its own reimplementation -- it extracts the REAL, substituted
`mass-translate-wf.template.js` source, wraps it exactly the way the
Workflow tool that actually executes this file must (the file is
self-contained, uses only the `agent()`/`pipeline()`/`log()`/`args` globals
the Workflow tool supplies, and its top-level `return`/`await` statements
only make sense inside such a wrapper -- confirmed directly: a plain
`node --check` on the raw file fails with "Illegal return statement"), then
drives it with Node.js under a scripted mock `agent()`/`pipeline()` that
counts every real call made and lets each fixture below force one specific
branch. Skipped entirely if Node.js is not on PATH -- this plugin has no
hard Node.js dependency (same stance
`tests/workflow_template_instantiation.test.py` already takes for its own
best-effort `node --check` pass).

Fixtures, one per branch (per the build spec's own enumeration):
  1. `test_estimator_boundary_exactly_at_cap_permits_dispatch_and_converges`
     -- a batch sized so `estimatedCalls` lands EXACTLY at `batch_agent_cap`
     for the cap/converged branch: the gate must NOT trip (`>`, not `>=`),
     `pipeline()` must actually run, and the real total agent-call count
     made while every segment converges on its worst-case-within-branch
     path (never clean until the final confirming round) must equal the
     formula's own estimate exactly.
  2. `test_estimator_one_below_boundary_blocks_dispatch_entirely` -- the
     same configuration with `batch_agent_cap` one less: the gate MUST
     trip, `pipeline()` must never run, and zero real agent calls happen.
  3/4/5. One fixture per blocked terminating sub-case: `review-null`,
     `draft-missing`, `review-artifact-mismatch`.
  6. The timeout branch.
  7. A dedicated case re-asserting that the `review-artifact-mismatch`
     segment's ACTUAL call count never exceeds the per-segment bound
     (`6 + 3*max_fix_rounds`) the estimator itself relies on.
  8. A parametrized, cheap (no `pipeline()` execution at all -- the gate
     trips before it) check that the real script's own `estimatedCalls`
     matches the closed form `1 + N*(6 + 3*maxFixRounds)` across several
     `(segment_count, max_fix_rounds)` pairs.
  9. A bonus (not separately required by the spec, included because it is
     nearly free given the machinery above): the SAME per-segment call
     total applies when the final confirming round ends non-convergent
     rather than convergent -- both are "the cap/converged branch" in the
     formula's own derivation, at the same cost.
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
    self-contained like every other sibling test file in this directory)."""
    text = MASS_TRANSLATE_TEMPLATE.read_text(encoding="utf-8")
    text = text.replace("{{DURABLE_ROOT}}", durable_root)
    text = text.replace("{{SOURCE_LANG}}", source_lang)
    text = text.replace("{{TARGET_LANG}}", target_lang)
    text = text.replace("{{MAX_FIX_ROUNDS}}", str(int(max_fix_rounds)))
    text = text.replace("{{BATCH_AGENT_CAP}}", str(int(batch_agent_cap)))
    escaped_verse_block = json.dumps(verse_policy_instruction_block)[1:-1]
    text = text.replace("{{VERSE_POLICY_INSTRUCTION_BLOCK}}", escaped_verse_block)
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
# getVerifiedReview, recordLedgerCall, ...) actually issue them -- this file
# never reimplements THEIR logic, only the ambient globals they call.
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
  if (label.indexOf("translate:") === 0) return "DONE " + seg;
  if (label.indexOf("wait:") === 0) return (PLAN[seg] || {}).wait;
  if (label.indexOf("review:") === 0) {
    const q = queues[seg].reviews;
    if (q.length === 0) throw new Error("PLAN review queue exhausted for " + seg + " label=" + label);
    return q.shift();
  }
  if (label.indexOf("artifact-check:") === 0) {
    const q = queues[seg].artifactChecks;
    if (q.length === 0) throw new Error("PLAN artifact-check queue exhausted for " + seg + " label=" + label);
    return q.shift();
  }
  if (label.indexOf("fix:") === 0) {
    const q = queues[seg].fixes;
    if (q.length === 0) throw new Error("PLAN fix queue exhausted for " + seg + " label=" + label);
    return q.shift();
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
# Response-object builders -- shapes matching REVIEW_SCHEMA /
# REVIEW_ARTIFACT_SCHEMA closely enough to drive the real script's own
# branching (`rev.clean`, `rev.coverage_ok`, `rev.findings`, `art.match`);
# this harness never itself validates against the JSON schemas (that is the
# real Workflow tool's job, out of scope here) -- only exercises the plain
# JS branching logic that reads these fields directly.
# ---------------------------------------------------------------------------
def review_obj(*, clean: bool, coverage_ok: bool = True) -> dict:
    return {
        "clean": clean,
        "coverage_ok": coverage_ok,
        "findings": [] if clean else [{"loc": "1", "severity": "minor", "issue": "x", "suggest": "y"}],
        "draft_sha1": "a" * 40,
    }


def match_true() -> dict:
    return {"match": True}


def match_false(detail: str = "artifact mismatch") -> dict:
    return {"match": False, "mismatch_detail": detail}


def converged_worst_case_plan(seg: str, max_fix_rounds: int, *, final_clean: bool) -> dict:
    """The worst-case-within-the-converged/non-converged-at-cap branch: every
    one of the `max_fix_rounds` normal rounds comes back non-null,
    artifact-matching, but NOT clean (so none of them exit early), then the
    final confirming round is queried once more. `final_clean` selects
    between the branch's two possible terminal statuses (`converged` vs
    `non_converged`/`cap`) -- both cost exactly the same number of calls,
    which is the entire point of the doc calling this one combined branch."""
    reviews = [review_obj(clean=False) for _ in range(max_fix_rounds)]
    reviews.append(review_obj(clean=final_clean, coverage_ok=True))
    artifact_checks = [match_true() for _ in range(max_fix_rounds + 1)]
    fixes = [f"FIXED {seg} r{i}" for i in range(1, max_fix_rounds + 1)]
    return {"wait": f"READY {seg}", "reviews": reviews, "artifactChecks": artifact_checks, "fixes": fixes}


def blocked_plan(seg: str, max_fix_rounds: int, terminal_kind: str) -> dict:
    """`max_fix_rounds - 1` completed normal rounds (non-null, matching, not
    clean -- so each costs exactly 3: review + artifact-check + fix), then a
    terminating round whose shape depends on `terminal_kind`, matching
    orchestration-and-batching.md's own three mutually exclusive sub-cases
    exactly, each scripted to fire at the LATEST possible round (round
    `max_fix_rounds` itself) -- the worst case the branch total assumes."""
    reviews: list = []
    artifact_checks: list = []
    fixes: list = []
    for i in range(1, max_fix_rounds):
        reviews.append(review_obj(clean=False))
        artifact_checks.append(match_true())
        fixes.append(f"FIXED {seg} r{i}")

    if terminal_kind == "review-null":
        # Original call null, single retry ALSO null -> blocked immediately,
        # neither call ever reaches the artifact-check gate. 2 calls.
        reviews.append(None)
        reviews.append(None)
    elif terminal_kind == "draft-missing":
        # A normal, non-null, artifact-matching review, but the fix call
        # itself reports DRAFT_MISSING. 3 calls.
        reviews.append(review_obj(clean=False))
        artifact_checks.append(match_true())
        fixes.append(f"DRAFT_MISSING {seg}")
    elif terminal_kind == "review-artifact-mismatch":
        # Non-null review, artifact-check reports a mismatch; the retry
        # review is ALSO non-null, and its own artifact-check STILL
        # mismatches -- no fix call ever dispatches. 4 calls, the largest
        # terminating sub-case.
        reviews.append(review_obj(clean=False))
        artifact_checks.append(match_false("first mismatch"))
        reviews.append(review_obj(clean=False))
        artifact_checks.append(match_false("second mismatch"))
    else:
        raise ValueError(f"unknown terminal_kind {terminal_kind!r}")

    return {"wait": f"READY {seg}", "reviews": reviews, "artifactChecks": artifact_checks, "fixes": fixes}


def timeout_plan(seg: str) -> dict:
    """The translator never delivers READY in time -- reviewFixLoop's own
    `ready.indexOf("READY") === -1` check fires on the very first wait call,
    before a single review/fix call is ever made."""
    return {"wait": f"TIMEOUT {seg}", "reviews": [], "artifactChecks": [], "fixes": []}


def blocked_branch_total(max_fix_rounds: int, terminating_cost: int) -> int:
    """orchestration-and-batching.md's own blocked-branch derivation:
    3 (fixed) + 3*(max_fix_rounds-1) (completed normal rounds) +
    terminating_cost + 1 (ledger write)."""
    return 3 + 3 * (max_fix_rounds - 1) + terminating_cost + 1


def converged_branch_total(max_fix_rounds: int) -> int:
    """orchestration-and-batching.md's own converged/non-converged-at-cap
    branch total: 3 (fixed) + 3*max_fix_rounds (all normal rounds
    completed) + 2 (final confirming review + artifact-check) + 1 (ledger
    write) == 3*max_fix_rounds + 6, exactly the `6 + 3*MAXFIX` per-segment
    term inside estimatedCalls."""
    return 3 + 3 * max_fix_rounds + 2 + 1


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
    estimated = 1 + len(segs) * (6 + 3 * max_fix_rounds)  # 1 + 2*12 = 25

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
        assert len(per_seg[seg]) == 6 + 3 * max_fix_rounds


def test_estimator_one_below_boundary_blocks_dispatch_entirely(tmp_path):
    max_fix_rounds = 2
    segs = ["seg01", "seg02"]
    estimated = 1 + len(segs) * (6 + 3 * max_fix_rounds)  # 25

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
    assert len(per_seg[seg]) == blocked_branch_total(max_fix_rounds, terminating_cost=2)


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
    assert len(per_seg[seg]) == blocked_branch_total(max_fix_rounds, terminating_cost=3)


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
    assert len(per_seg[seg]) == blocked_branch_total(max_fix_rounds, terminating_cost=4)


# ---------------------------------------------------------------------------
# 6: the timeout branch.
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
    # 1 in_progress ledger write + 1 translate call + 1 wait call + 1 timeout
    # ledger write == 4, independent of max_fix_rounds.
    assert len(per_seg[seg]) == 4


# ---------------------------------------------------------------------------
# 7: dedicated case -- a review-artifact-mismatch segment's ACTUAL call
# count never exceeds the formula's own per-segment bound (6 + 3*MAXFIX),
# even though it is the largest of the three blocked terminating sub-cases.
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
    per_segment_bound = 6 + 3 * max_fix_rounds  # the exact term estimatedCalls sizes per segment

    assert actual_calls == blocked_branch_total(max_fix_rounds, terminating_cost=4)
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
    expected = 1 + n_segs * (6 + 3 * max_fix_rounds)

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
