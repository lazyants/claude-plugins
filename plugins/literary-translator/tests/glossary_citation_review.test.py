"""tests/glossary_citation_review.test.py -- 1.16.0 pre-merge citation review for
glossary-pass-wf.template.js.

WHAT IS BEING GUARDED. Under ``glossary.research_mode: live`` the glossary
dispatch may resolve a candidate with ``basis:"established"``, and that basis
carries a ``source`` URL the agent produced itself. ``canon_validate.py
--check-batch`` asserts only that the URL is PRESENT and URI-SHAPED -- nothing
anywhere checked that it resolves or that it documents the claimed
``canonical_target_form``. A merged canon row is then immutable in practice
(``--verify-merged`` is disk-independent and writes nothing; re-merging a
different resolution for one ``source_form`` is a fatal collision;
``canon_adjudication_audit.py`` only blocks, never repairs), so a fabricated
citation that reached the merge was frozen for the life of the project.

WHY THE OBVIOUS TEST IS THE WRONG TEST. "No bad citation reached the merge" is
satisfied trivially by aborting the pass, and aborting is exactly the failure
mode the shipped template makes easy: ``ready:false`` feeds the
``notReadyBatches`` branch, which refuses the merge for EVERY batch and returns
``reason:"fragment-check-failed"`` for the whole run. So the load-bearing
assertion here is the FULL CYCLE -- reject -> regenerate -> approve -> merge --
proving the gate rejects a citation *without* destroying the pass. Three
further cases cover the structural traps a naive insertion hits:

  * ``test_stale_attempt_verdict_cannot_approve_a_later_attempt`` -- fragments
    are attempt-scoped and the review verdict names its attempt, so a verdict
    produced against attempt N cannot approve attempt N+1. Against one fixed
    ``out_{index}.json`` the post-rejection wait returns READY off the REJECTED
    bytes (a citation-rejected fragment is still structurally valid, which is
    why ``--check-batch`` passed it in the first place).
  * ``test_resume_skipped_fragment_is_still_citation_reviewed`` -- the review is
    reachable from the PRESENT resume-skip path too. A review placed only after
    dispatch/wait is bypassed on precisely the run where a stale, unreviewed
    fragment already sits on disk.
  * ``test_exhaustion_is_distinguishable_from_fragment_failure`` -- exhaustion
    does stop the pass (the merge is all-or-nothing by design), and that is
    accepted; what must not happen is exhaustion being reported as an ordinary
    fragment failure, since the two call for opposite responses (re-run vs. a
    human resolving unsourceable candidates).

SCOPE, vs tests/batch_size_estimator.test.py. That file owns the COST
ESTIMATOR: the live 10*N+2 and offline 3*N+2 preflight formulas, the
exactly-at-cap boundary, and the shape of the over-cap refusal. This file owns
the STATE MACHINE the estimate is a model of -- what the review actually does
to the control flow. The one place the two touch on purpose is formula
TIGHTNESS (below): a real worst-case run measured against the formula, which
is the only assertion here that a refusal test cannot make, plus the one
assertion that ties the two files' ladder constants to the template's own
expression so they cannot drift apart silently. The offline case exists in
both files and is NOT a duplicate: there it is the estimate (3*N+2), here it
is the behaviour (no review call is spent at all), and a template can get
either one right while getting the other wrong.

MECHANISM. Same extract-substitute-wrap-run-under-Node harness as
tests/glossary_pipeline_e2e.test.py and tests/batch_size_estimator.test.py's
glossary section: the REAL, unmodified template is instantiated and executed
with a mocked ``agent()``/``pipeline()``/``log()``, and the assertions are on
the actual control flow (call labels, call order, rendered prompt text) --
never a reimplementation and never a source-string grep. A static grep cannot
tell a review that gates the merge from one that merely runs. Recording the
rendered PROMPTS is what lets this file assert things the estimator's harness
structurally cannot -- which attempt path the merge was handed, that the
reviewer's own findings reached the regeneration, and that a verdict naming a
stale attempt fails to approve a later one.
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
TEMPLATES_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets" / "templates"
GLOSSARY_TEMPLATE = TEMPLATES_DIR / "glossary-pass-wf.template.js"

assert GLOSSARY_TEMPLATE.is_file(), f"expected plugin template not found: {GLOSSARY_TEMPLATE}"

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(
    NODE is None,
    reason="node not found on PATH; this test executes the real glossary-pass "
    "template's citation-review control flow under Node (no hard Node.js "
    "dependency for this plugin otherwise)",
)

FIXTURE_DURABLE_ROOT = "/fixture/project/durable_root"
FIXTURE_RUN_ID = "20260725T000000Z"
FIXTURE_SOURCE_LANG = "French"
FIXTURE_TARGET_LANG = "Russian"

# Mirrors the template's own MAX_CITATION_RETRIES. Asserted against the real
# source in test_max_citation_retries_matches_this_fixture below rather than
# merely assumed, so a change to the constant fails loudly HERE instead of
# silently making these fixtures test a different ladder than the one shipped.
EXPECTED_MAX_CITATION_RETRIES = 2

# This file's copy of the live worst-case per-batch ceiling the preflight
# charges: precheck 1 + one (dispatch + wait + review) triple per attempt.
# tests/batch_size_estimator.test.py keeps its own independent copy
# (GLOSSARY_LIVE_PER_BATCH_CEILING). The two agree today only because both
# happen to evaluate to 10; what makes that agreement an invariant rather than
# a coincidence is
# test_live_per_batch_ceiling_is_pinned_to_the_template_and_the_estimator_file
# at the end of the formula-tightness section below.
LIVE_PER_BATCH_CEILING = 1 + 3 * (EXPECTED_MAX_CITATION_RETRIES + 1)  # 10

RUN_DIR = f"{FIXTURE_DURABLE_ROOT}/glossary/runs/{FIXTURE_RUN_ID}"


def attempt_path(index: int, attempt: int) -> str:
    """The attempt-scoped fragment path the template is expected to use."""
    return f"{RUN_DIR}/out_{index}_attempt_{attempt}.json"


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


# The mock records every call's label and rendered prompt IN ORDER (prompts are
# appended to a list per label, not overwritten, because the whole point here is
# that one label fires more than once -- once per attempt). PLAN is keyed by the
# batch's own string index; `reviews` and `waits` are consumed positionally, one
# entry per attempt, falling back to an ordinary success once exhausted.
HARNESS = r"""
'use strict';
__WRAPPED_SOURCE__

const BATCHES_ARGS = __BATCHES_JSON__;
const PLAN = __PLAN_JSON__;
const promptsByLabel = {};
const callsLog = [];
const logLines = [];
const seenCount = {};
let pipelineCalled = false;

function record(label, promptText) {
  if (!promptsByLabel[label]) promptsByLabel[label] = [];
  promptsByLabel[label].push(typeof promptText === "string" ? promptText : String(promptText));
  seenCount[label] = (seenCount[label] || 0) + 1;
  return seenCount[label] - 1;   // 0-based ordinal of THIS call for THIS label
}

function planFor(label) {
  const parts = label.split(":");
  return PLAN[parts[parts.length - 1]] || {};
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
    label: label,
    ordinal: ordinal,
    phase: opts.phase || null,
    effort: opts.effort || null,
    agentType: opts.agentType || null,
    hasSchema: !!opts.schema,
  });

  if (label === "glossary:merge") {
    return Object.prototype.hasOwnProperty.call(PLAN, "merge") ? PLAN.merge : "MERGED (mock)";
  }
  if (label === "glossary:verify") {
    return Object.prototype.hasOwnProperty.call(PLAN, "verify") ? PLAN.verify : { verified: true };
  }

  const parts = label.split(":");
  const kind = parts[1];
  const idx = parts[parts.length - 1];
  const p = planFor(label);

  if (kind === "precheck") {
    return Object.prototype.hasOwnProperty.call(p, "precheck") ? p.precheck : ("ABSENT " + idx);
  }
  if (kind === "dispatch") {
    return "FRAGMENT " + idx;
  }
  if (kind === "wait") {
    return nth(p.waits, ordinal, "READY " + idx);
  }
  if (kind === "citation-review") {
    // Default: approve THIS attempt. The attempt number is the ordinal --
    // attempt N's review is the (N+1)th citation-review call for this batch --
    // except on the resume-skip path, where attempt 0's review is still the
    // first call. Tests that care drive `reviews` explicitly.
    return nth(p.reviews, ordinal, "CITATIONS_OK " + idx + " ATTEMPT " + ordinal);
  }
  // Deliberately non-throwing: an unrecognized label must surface as a failed
  // ASSERTION with readable context, not as an opaque harness crash. RED runs
  // against the pre-fix template rely on this.
  return "UNEXPECTED_LABEL " + label;
}

async function pipeline(items, stage1) {
  pipelineCalled = true;
  const out = [];
  for (const item of items) {
    out.push(await stage1(item));
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
      promptsByLabel: promptsByLabel,
      log: logLines,
      pipelineCalled: pipelineCalled,
    }));
  } catch (err) {
    process.stderr.write("HARNESS_ERROR: " + (err && err.stack || String(err)) + "\n");
    process.exit(1);
  }
})();
"""


def run(*, tmp_path: Path, batches: list, research_mode: str = "live",
        batch_agent_cap: int = 10_000, plan: dict | None = None,
        timeout: int = 30) -> dict:
    """Returns {ok, out, stderr}. ok=False (with stderr) when the template threw
    before producing stdout (e.g. the batch-index guard's throw path)."""
    plan = plan or {}
    src = instantiate(research_mode=research_mode, batch_agent_cap=batch_agent_cap)
    harness = (
        HARNESS.replace("__WRAPPED_SOURCE__", _wrap(src))
        .replace("__BATCHES_JSON__", json.dumps(batches))
        .replace("__PLAN_JSON__", json.dumps(plan))
    )
    p = tmp_path / "glossary_citation_harness.js"
    p.write_text(harness, encoding="utf-8")
    # NODE is only None when `node` is absent from PATH, in which case
    # pytestmark's skipif already skipped this test before the call is reached.
    assert NODE is not None
    proc = subprocess.run([NODE, str(p)], capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        return {"ok": False, "out": None, "stderr": proc.stderr}
    return {"ok": True, "out": json.loads(proc.stdout), "stderr": proc.stderr}


def labels_of(out: dict) -> list:
    return [c["label"] for c in out["calls"]]


def count_label(out: dict, label: str) -> int:
    return sum(1 for c in out["calls"] if c["label"] == label)


def prompts_for(out: dict, label: str) -> list:
    return out["promptsByLabel"].get(label, [])


# ---------------------------------------------------------------------------
# The constant this file's fixtures are built around.
# ---------------------------------------------------------------------------

def test_max_citation_retries_matches_this_fixture():
    """The retry ladder's depth is a deliberate product decision, and every
    exhaustion fixture below is sized to it. Pinning it here means changing the
    constant fails with a pointed message rather than silently turning the
    exhaustion tests into something that no longer reaches exhaustion."""
    source = GLOSSARY_TEMPLATE.read_text(encoding="utf-8")
    needle = f"const MAX_CITATION_RETRIES = {EXPECTED_MAX_CITATION_RETRIES}"
    assert needle in source, (
        f"expected {needle!r} in glossary-pass-wf.template.js -- if the retry "
        f"ladder changed deliberately, update EXPECTED_MAX_CITATION_RETRIES "
        f"here and re-check every exhaustion fixture in this file"
    )


# ---------------------------------------------------------------------------
# THE test: the full cycle. reject -> regenerate -> approve -> merge.
# ---------------------------------------------------------------------------

def test_rejected_citation_regenerates_then_approves_then_merges(tmp_path):
    """The load-bearing case. A rejected citation must cause the fragment to be
    REGENERATED and the corrected one merged -- not the pass to be aborted.

    Asserting only "no bad citation reached the merge" would pass trivially if
    the template killed the whole run on rejection (the shipped `ready:false`
    ->  notReadyBatches -> reason:"fragment-check-failed" path), which is why
    every leg of the cycle is asserted individually here."""
    plan = {"0": {
        "precheck": "ABSENT 0",
        "reviews": [
            "Item 1: source_form 'Ninon' cites https://example.invalid/nope which 404s.\n"
            "CITATIONS_REJECTED 0 ATTEMPT 0",
            "CITATIONS_OK 0 ATTEMPT 1",
        ],
    }}
    res = run(tmp_path=tmp_path, batches=[make_batch(0, ["Ninon"])], plan=plan)
    assert res["ok"], res["stderr"]
    out = res["out"]

    # 1. REJECT then REGENERATE: a second dispatch actually happened.
    assert count_label(out, "glossary:dispatch:0") == 2, (
        "a rejected citation must trigger a fresh codex dispatch, not a re-poll "
        f"of the same fragment; calls were {labels_of(out)}"
    )
    assert count_label(out, "glossary:wait:0") == 2
    assert count_label(out, "glossary:citation-review:0") == 2

    # 2. Order: the regeneration follows the rejection, it does not precede it.
    order = [c["label"] for c in out["calls"]]
    first_review = order.index("glossary:citation-review:0")
    assert order[first_review + 1] == "glossary:dispatch:0", (
        f"the re-dispatch must directly follow the rejecting review; got {order}"
    )

    # 3. APPROVE then MERGE: the pass completed rather than aborting.
    assert out["result"]["merged"] is True, (
        f"a recoverable citation rejection must NOT abort the pass; got {out['result']}"
    )
    assert "reason" not in out["result"]
    assert "glossary:merge" in order and "glossary:verify" in order

    # 4. The merge received the APPROVED attempt's path -- and only it.
    merge_prompt = prompts_for(out, "glossary:merge")[0]
    assert attempt_path(0, 1) in merge_prompt, (
        f"the merge must be handed the approved attempt-1 fragment; prompt was:\n{merge_prompt}"
    )
    assert attempt_path(0, 0) not in merge_prompt, (
        "the REJECTED attempt-0 fragment must never reach the merge command"
    )

    # 5. The batch result records the approval, not merely readiness.
    batch_result = out["result"]["batches"][0]
    assert batch_result["ready"] is True
    assert batch_result["citationReview"] == "approved"
    assert batch_result["attempt"] == 1
    assert batch_result["fragmentPath"] == attempt_path(0, 1)


def test_rejection_reason_is_carried_into_the_regeneration_prompt(tmp_path):
    """A bare "do it again" would re-run the same reasoning over the same
    candidates and very likely reproduce the same unverifiable URL. The
    reviewer's own findings must reach the regenerating agent."""
    plan = {"0": {
        "precheck": "ABSENT 0",
        "reviews": [
            "source_form 'Ninon' cites https://example.invalid/nope which does not resolve.\n"
            "CITATIONS_REJECTED 0 ATTEMPT 0",
            "CITATIONS_OK 0 ATTEMPT 1",
        ],
    }}
    res = run(tmp_path=tmp_path, batches=[make_batch(0, ["Ninon"])], plan=plan)
    assert res["ok"], res["stderr"]
    out = res["out"]

    dispatches = prompts_for(out, "glossary:dispatch:0")
    assert len(dispatches) == 2

    # The FIRST dispatch is an ordinary one -- it must not claim to be a
    # regeneration (a positive control: proves the assertion below discriminates).
    assert "THIS IS A REGENERATION" not in dispatches[0]

    # The SECOND carries the reviewer's verbatim finding.
    assert "THIS IS A REGENERATION" in dispatches[1]
    assert "https://example.invalid/nope" in dispatches[1], (
        f"the reviewer's own reason must reach the regenerating agent; prompt was:\n{dispatches[1]}"
    )
    # ...and the verdict sentinel itself is stripped rather than echoed into a
    # prompt whose own reply gets sentinel-parsed.
    assert "CITATIONS_REJECTED 0 ATTEMPT 0" not in dispatches[1]


# ---------------------------------------------------------------------------
# Trap: one fixed out_{index}.json lets a stale attempt satisfy a later one.
# ---------------------------------------------------------------------------

def test_each_attempt_uses_its_own_fragment_path(tmp_path):
    """Dispatch, wait and review must all name the SAME attempt-scoped path,
    and a later attempt must name a DIFFERENT one. Against a single fixed
    out_{index}.json the post-rejection wait returns READY off the rejected
    bytes -- the wait only asks whether that path passes --check-batch, and a
    citation-rejected fragment still passes it."""
    plan = {"0": {
        "precheck": "ABSENT 0",
        "reviews": ["CITATIONS_REJECTED 0 ATTEMPT 0", "CITATIONS_OK 0 ATTEMPT 1"],
    }}
    res = run(tmp_path=tmp_path, batches=[make_batch(0, ["Ninon"])], plan=plan)
    assert res["ok"], res["stderr"]
    out = res["out"]

    for attempt in (0, 1):
        expected = attempt_path(0, attempt)
        for label in ("glossary:dispatch:0", "glossary:wait:0", "glossary:citation-review:0"):
            prompt = prompts_for(out, label)[attempt]
            assert expected in prompt, (
                f"{label} attempt {attempt} must name {expected}; prompt was:\n{prompt}"
            )

    # The two attempts are genuinely different files, and the legacy fixed path
    # is gone entirely (its presence anywhere would reopen the stale-bytes hole).
    assert attempt_path(0, 0) != attempt_path(0, 1)
    for label in ("glossary:dispatch:0", "glossary:wait:0", "glossary:citation-review:0"):
        for prompt in prompts_for(out, label):
            assert f"{RUN_DIR}/out_0.json" not in prompt, (
                f"{label} still references the legacy fixed fragment path"
            )


def test_stale_attempt_verdict_cannot_approve_a_later_attempt(tmp_path):
    """DELAYED REGENERATION. The verdict is bound to the attempt it judged, so
    an approval produced against attempt 0 -- arriving late, replayed, or simply
    echoed by a confused agent while attempt 1 is in flight -- cannot approve
    attempt 1's fragment.

    Attempt 1's review here answers with a well-formed, genuine-looking approval
    that names ATTEMPT 0. If verdicts were merely batch-scoped ("CITATIONS_OK
    0") this would approve, and the fragment written for attempt 1 would merge on
    the strength of a judgment made about different bytes."""
    plan = {"0": {
        "precheck": "ABSENT 0",
        "reviews": [
            "CITATIONS_REJECTED 0 ATTEMPT 0",
            "CITATIONS_OK 0 ATTEMPT 0",   # stale: judged attempt 0, not attempt 1
            "CITATIONS_OK 0 ATTEMPT 2",
        ],
    }}
    res = run(tmp_path=tmp_path, batches=[make_batch(0, ["Ninon"])], plan=plan)
    assert res["ok"], res["stderr"]
    out = res["out"]

    # The stale verdict did not approve attempt 1: a third attempt was dispatched.
    assert count_label(out, "glossary:dispatch:0") == 3, (
        "a verdict naming a different attempt must not approve this one; "
        f"calls were {labels_of(out)}"
    )
    # And the run still converged on the attempt whose OWN verdict approved it.
    assert out["result"]["merged"] is True
    assert out["result"]["batches"][0]["attempt"] == 2
    merge_prompt = prompts_for(out, "glossary:merge")[0]
    assert attempt_path(0, 2) in merge_prompt
    assert attempt_path(0, 1) not in merge_prompt


# ---------------------------------------------------------------------------
# Trap: the PRESENT resume-skip path returns early, before dispatch and wait.
# ---------------------------------------------------------------------------

def test_resume_skipped_fragment_is_still_citation_reviewed(tmp_path):
    """A review inserted only after dispatch/wait is bypassed on every resumed
    batch -- exactly the run where a stale, never-reviewed fragment is already
    on disk. The resume-skip must still reach the review."""
    plan = {"0": {"precheck": "PRESENT 0"}}
    res = run(tmp_path=tmp_path, batches=[make_batch(0, ["Ninon"])], plan=plan)
    assert res["ok"], res["stderr"]
    out = res["out"]

    order = labels_of(out)
    # The resume-skip itself still holds: no dispatch, no wait.
    assert "glossary:dispatch:0" not in order
    assert "glossary:wait:0" not in order
    # ...but the review DID run, against attempt 0's fragment.
    assert count_label(out, "glossary:citation-review:0") == 1, (
        f"a resume-skipped fragment must still be citation-reviewed; calls were {order}"
    )
    assert attempt_path(0, 0) in prompts_for(out, "glossary:citation-review:0")[0]
    assert out["result"]["merged"] is True
    assert out["result"]["batches"][0]["citationReview"] == "approved"


def test_resume_skipped_fragment_with_bad_citation_is_regenerated(tmp_path):
    """The stronger half: reviewing the resumed fragment is only worth anything
    if a rejection there actually regenerates it. Proves the resume-skip path
    joins the SAME retry ladder rather than dead-ending in a review whose
    verdict has nowhere to go."""
    plan = {"0": {
        "precheck": "PRESENT 0",
        "reviews": [
            "stale fragment cites https://example.invalid/gone\nCITATIONS_REJECTED 0 ATTEMPT 0",
            "CITATIONS_OK 0 ATTEMPT 1",
        ],
    }}
    res = run(tmp_path=tmp_path, batches=[make_batch(0, ["Ninon"])], plan=plan)
    assert res["ok"], res["stderr"]
    out = res["out"]

    # A real dispatch happened despite the batch having been resume-skipped.
    assert count_label(out, "glossary:dispatch:0") == 1, (
        f"a rejected resume-skipped fragment must be regenerated; calls were {labels_of(out)}"
    )
    assert count_label(out, "glossary:wait:0") == 1
    # The regeneration went to a FRESH path, not back over the resumed bytes.
    assert attempt_path(0, 1) in prompts_for(out, "glossary:dispatch:0")[0]
    assert out["result"]["merged"] is True
    assert out["result"]["batches"][0]["attempt"] == 1
    assert attempt_path(0, 1) in prompts_for(out, "glossary:merge")[0]


# ---------------------------------------------------------------------------
# Trap: ready:false aborts the whole pass -- exhaustion must be its own signal.
# ---------------------------------------------------------------------------

def test_exhaustion_is_distinguishable_from_fragment_failure(tmp_path):
    """Exhaustion DOES stop the pass -- the merge is all-or-nothing by design,
    so a batch that cannot be approved blocks it, and that is expected. What
    must not happen is exhaustion being indistinguishable from an ordinary
    fragment failure: "fragment-check-failed" reads as transient (re-run and it
    will probably work), whereas exhaustion means an agent could not produce a
    verifiable source in any of its attempts and needs a human."""
    rejections = [
        f"CITATIONS_REJECTED 0 ATTEMPT {n}"
        for n in range(EXPECTED_MAX_CITATION_RETRIES + 1)
    ]
    # The FINAL rejection carries reviewer prose. On an exhausted batch there is
    # no further dispatch prompt to carry that finding forward, so
    # `lastRejection` on the returned result is the only place it survives --
    # and it is what the human who now has to resolve these candidates by hand
    # actually reads.
    rejections[-1] = (
        "source_form 'Ninon' cites https://example.invalid/gone which 404s.\n"
        + rejections[-1]
    )
    plan = {"0": {"precheck": "ABSENT 0", "reviews": rejections}}
    res = run(tmp_path=tmp_path, batches=[make_batch(0, ["Ninon"])], plan=plan)
    assert res["ok"], res["stderr"]
    out = res["out"]

    assert out["result"]["merged"] is False
    assert out["result"]["reason"] == "citation-review-exhausted", (
        "exhaustion must NOT be reported as a generic fragment failure; got "
        f"{out['result']}"
    )
    assert out["result"]["reason"] != "fragment-check-failed"
    assert out["result"]["citationExhausted"] == [0]

    # Every attempt in the ladder was actually spent -- no early give-up.
    expected_attempts = EXPECTED_MAX_CITATION_RETRIES + 1
    assert count_label(out, "glossary:dispatch:0") == expected_attempts
    assert count_label(out, "glossary:citation-review:0") == expected_attempts

    # No bad citation reached the merge.
    order = labels_of(out)
    assert "glossary:merge" not in order
    assert "glossary:verify" not in order

    batch_result = out["result"]["batches"][0]
    assert batch_result["reason"] == "citation-review-exhausted"
    assert batch_result["attemptsUsed"] == expected_attempts
    assert "https://example.invalid/gone" in (batch_result.get("lastRejection") or ""), (
        "the final reviewer's findings must survive on the returned result: an "
        "exhausted batch produces no further dispatch prompt to carry them, so "
        f"this is the operator's only copy; got {batch_result.get('lastRejection')!r}"
    )


def test_timeout_still_reports_fragment_check_failed(tmp_path):
    """Discriminating control for the test above: an ordinary wait timeout must
    still report reason:"fragment-check-failed". Without this, a template that
    relabelled EVERY not-ready batch as citation-exhausted would pass the
    exhaustion test while destroying the existing signal."""
    plan = {"0": {"precheck": "ABSENT 0", "waits": ["TIMEOUT 0"]}}
    res = run(tmp_path=tmp_path, batches=[make_batch(0, ["Ninon"])], plan=plan)
    assert res["ok"], res["stderr"]
    out = res["out"]

    assert out["result"]["merged"] is False
    assert out["result"].get("reason") == "fragment-check-failed", (
        f"expected reason:'fragment-check-failed'; got {out['result'].get('reason')!r}"
    )
    assert out["result"].get("notReady") == [0]
    assert "citationExhausted" not in out["result"]
    # A fragment that never materialized is never citation-reviewed.
    assert count_label(out, "glossary:citation-review:0") == 0


def test_one_exhausted_batch_does_not_hide_a_healthy_sibling(tmp_path):
    """Per-batch, not all-or-nothing: a healthy neighbour still runs its own
    full cycle, and the exhausted batch is named precisely."""
    rejections = [
        f"CITATIONS_REJECTED 1 ATTEMPT {n}"
        for n in range(EXPECTED_MAX_CITATION_RETRIES + 1)
    ]
    plan = {
        "0": {"precheck": "ABSENT 0"},
        "1": {"precheck": "ABSENT 1", "reviews": rejections},
    }
    res = run(
        tmp_path=tmp_path,
        batches=[make_batch(0, ["Ninon"]), make_batch(1, ["Scudery"])],
        plan=plan,
    )
    assert res["ok"], res["stderr"]
    out = res["out"]

    # `.get`, not `[...]`: a template that let the exhausted batch through as
    # ready returns a merged result with NO `reason` key at all, and a bare
    # subscript would fail this test with an opaque KeyError instead of naming
    # the regression. Verified by scoped mutation -- the readable form below
    # reports "expected the run to be blocked", the subscript reported
    # "KeyError: 'reason'".
    assert out["result"]["merged"] is False, (
        "an exhausted batch must block the whole (all-or-nothing) merge; got "
        f"{out['result']}"
    )
    assert out["result"].get("reason") == "citation-review-exhausted", (
        f"expected reason:'citation-review-exhausted'; got {out['result'].get('reason')!r}"
    )
    assert out["result"].get("citationExhausted") == [1]
    # The healthy batch was approved on its first attempt.
    assert count_label(out, "glossary:dispatch:0") == 1
    assert count_label(out, "glossary:citation-review:0") == 1
    # ...but the merge is still not attempted, because it is all-or-nothing.
    assert "glossary:merge" not in labels_of(out)


# ---------------------------------------------------------------------------
# research_mode: offline -- the stage must be a cheap no-op.
# ---------------------------------------------------------------------------

def test_offline_mode_spends_no_review_call(tmp_path):
    """offline forbids basis:"established" outright (canon_validate.py's
    merge-time backstop fatally rejects the batch otherwise), so there is no
    citation to review. Spending one agent call per batch to be told so is pure
    waste, and it is what would happen if the stage were mode-blind."""
    res = run(
        tmp_path=tmp_path,
        batches=[make_batch(0, ["Ninon"]), make_batch(1, ["Scudery"])],
        research_mode="offline",
    )
    assert res["ok"], res["stderr"]
    out = res["out"]

    assert [lbl for lbl in labels_of(out) if "citation-review" in lbl] == [], (
        f"offline must spend no citation-review call at all; calls were {labels_of(out)}"
    )
    assert out["result"]["merged"] is True
    assert out["result"]["batches"][0]["citationReview"] == "skipped-offline"
    # Exactly the historical cost: precheck + dispatch + wait per batch, plus
    # the fixed merge + verify pair.
    assert len(out["calls"]) == 3 * 2 + 2

    # Attempt-scoped paths still apply offline -- the naming is not conditional.
    assert attempt_path(0, 0) in prompts_for(out, "glossary:merge")[0]


# ---------------------------------------------------------------------------
# Formula TIGHTNESS -- measured at both ends against real runs.
#
# The preflight REFUSAL tests (does an over-cap run return
# reason:"batch-too-large" without dispatching, and is estimatedCalls exactly
# 10*N+2 live / 3*N+2 offline) live in tests/batch_size_estimator.test.py --
# that file's subject is the cost estimator, so the ladder arithmetic belongs
# there and is not duplicated here.
#
# What stays here is the other question, which needs a real RUN rather than a
# refusal: does the estimate actually bound what the state machine spends? A
# formula can be internally consistent and still be wrong about the code.
#
# Only the WORST case is measured, and only here, because it is the one end of
# the formula the estimator file structurally cannot reach: its harness always
# approves, so it can never drive the ladder to exhaustion. The BEST case (a
# batch approved on attempt 0) is deliberately NOT repeated here -- it is
# first-attempt arithmetic, which the scope note above assigns to the estimator
# file, and that file already asserts the identical one-batch run as a strict
# superset (test_glossary_resume_precheck_absent_falls_through_to_real_dispatch
# asserts the same 6 calls AND the dispatch/wait call labels).
#
# The second test closes the seam BETWEEN the two files, which the measurement
# above cannot: it makes the ceiling this file measures against, the ceiling
# the estimator file charges, and the template's own expression one fact.
# ---------------------------------------------------------------------------

def test_live_worst_case_run_does_not_exceed_its_own_estimate(tmp_path):
    """The estimate is only meaningful if a real worst-case run stays within it.
    Drives one batch all the way to exhaustion -- the most expensive path that
    exists -- and counts the ACTUAL calls against the formula, rather than
    trusting the arithmetic in the comment."""
    per_batch = LIVE_PER_BATCH_CEILING
    rejections = [
        f"CITATIONS_REJECTED 0 ATTEMPT {n}"
        for n in range(EXPECTED_MAX_CITATION_RETRIES + 1)
    ]
    plan = {"0": {"precheck": "ABSENT 0", "reviews": rejections}}
    res = run(tmp_path=tmp_path, batches=[make_batch(0, ["Ninon"])], plan=plan)
    assert res["ok"], res["stderr"]
    out = res["out"]

    # Exhaustion skips merge + verify, so the ceiling for the batch itself is
    # per_batch; the +2 pair is only spent on a run that reaches the merge.
    assert len(out["calls"]) == per_batch, (
        f"a worst-case batch must cost exactly the per-batch term the preflight "
        f"charges for it ({per_batch}); calls were {labels_of(out)}"
    )


def _load_estimator_module():
    """Loads tests/batch_size_estimator.test.py under a private module name.

    Every harness in this directory is deliberately self-contained -- each
    re-implements the template's substitution contract rather than importing a
    sibling. This is the one deliberate exception, and it is the entire point
    of the test below: the two files' ladder constants are exactly the pair
    that must not be allowed to drift apart, so the assertion has to see BOTH
    of them, not a third local copy of the number.

    `batch_size_estimator.test` is not a legal dotted module name (which is why
    pytest.ini runs the suite with --import-mode=importlib), so it is loaded by
    file identity. It is deliberately NOT registered in sys.modules: pytest
    collects the same file separately, and the two loads must not contend for a
    name. Executing it is cheap -- module scope is constants, path literals and
    harness source strings, with no I/O beyond resolving __file__.
    """
    path = Path(__file__).resolve().parent / "batch_size_estimator.test.py"
    assert path.is_file(), f"sibling estimator test not found: {path}"
    spec = importlib.util.spec_from_file_location("_lt_estimator_seam_probe", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_live_per_batch_ceiling_is_pinned_to_the_template_and_the_estimator_file():
    """The one assertion that makes the live ceiling a single fact.

    Three copies of it exist: the template's own `perBatchCalls` expression
    (the only one that is actually EXECUTED, and therefore the only one that is
    by definition right), this file's LIVE_PER_BATCH_CEILING, and
    tests/batch_size_estimator.test.py's GLOSSARY_LIVE_PER_BATCH_CEILING. Until
    this test they were never compared to one another -- they agreed only
    because all three independently evaluated to 10, and each file's own suite
    stayed green against its own copy.

    That is a false-green with teeth, because the two files can only see
    different halves of the problem. The estimator file's formula tests trip
    the refusal gate BEFORE pipeline() ever runs (deliberately -- zero agent
    calls needed), so they verify the preflight arithmetic and structurally
    cannot observe what a real run costs; only
    test_live_worst_case_run_does_not_exceed_its_own_estimate above drives a
    genuine exhaustion run and counts real calls. So if the template's real
    ladder and its preflight expression ever diverge, the red test appears HERE
    -- and "fixing" it by adjusting this file's expected count until it goes
    green would leave the preflight silently UNDER-counting, which is the
    dangerous direction: an estimate that under-counts lets a run start and
    then blow engine.batch_agent_cap mid-flight, whereas one that over-counts
    merely refuses early and loudly. Both suites would stay green throughout.

    The ceiling here is read out of the template's source rather than recomputed
    from either file's constants -- the same way this file's
    test_max_citation_retries_matches_this_fixture and the estimator file's
    test_glossary_citation_retry_bound_is_the_documented_two read the real
    constant -- so changing the ladder in the template, or either file's
    constant, without updating the others is RED.
    """
    source = GLOSSARY_TEMPLATE.read_text(encoding="utf-8")

    retries_match = re.search(r"const\s+MAX_CITATION_RETRIES\s*=\s*(\d+)", source)
    assert retries_match, (
        "could not find `const MAX_CITATION_RETRIES = <n>` in the template; the "
        "ladder constant this whole section is derived from has moved or been "
        "renamed -- re-derive, do not delete this test"
    )

    # The template's own live per-batch expression, executed verbatim by the
    # preflight (glossary-pass-wf.template.js, `const perBatchCalls = ...`).
    # Parsed rather than mirrored, so the SHAPE of the ladder is pinned too and
    # not just the retry count: dropping the review, or the wait, would leave
    # every MAX_CITATION_RETRIES needle test in both files green.
    ladder_match = re.search(
        r"const\s+perBatchCalls\s*=\s*CITATION_REVIEW_ENABLED\s*"
        r"\?\s*(\d+)\s*\+\s*(\d+)\s*\*\s*\(\s*MAX_CITATION_RETRIES\s*\+\s*1\s*\)",
        source,
    )
    assert ladder_match, (
        "the template's live per-batch preflight expression no longer has the "
        "shape `1 + <k>*(MAX_CITATION_RETRIES + 1)` that this seam parses -- "
        "the ladder was restructured, so RE-DERIVE the ceiling in BOTH this "
        "file and tests/batch_size_estimator.test.py from the template's new "
        "expression; do not relax this regex to make it pass"
    )

    retries = int(retries_match.group(1))
    base, per_attempt = int(ladder_match.group(1)), int(ladder_match.group(2))
    ceiling_from_template = base + per_attempt * (retries + 1)

    assert ceiling_from_template == LIVE_PER_BATCH_CEILING, (
        f"the template charges {ceiling_from_template} calls per live batch "
        f"({base} + {per_attempt}*({retries}+1)) but this file measures real "
        f"runs against LIVE_PER_BATCH_CEILING={LIVE_PER_BATCH_CEILING}"
    )

    estimator = _load_estimator_module()
    assert ceiling_from_template == estimator.GLOSSARY_LIVE_PER_BATCH_CEILING, (
        f"the template charges {ceiling_from_template} calls per live batch "
        f"({base} + {per_attempt}*({retries}+1)) but "
        f"tests/batch_size_estimator.test.py's GLOSSARY_LIVE_PER_BATCH_CEILING "
        f"is {estimator.GLOSSARY_LIVE_PER_BATCH_CEILING} -- the preflight "
        f"estimate and the measured cost of a real run have drifted apart"
    )


# ---------------------------------------------------------------------------
# Call-shape invariants for the new stage.
# ---------------------------------------------------------------------------

def test_citation_review_is_a_claude_call_not_a_second_codex_dispatch(tmp_path):
    """The reviewer must not be the same engine that produced the citation --
    an independent opinion, not the same reasoning re-run. Also keeps
    tests/bounded_poll_present.test.py's "exactly one codex work-call in this
    template" pin true, and keeps the stage schema-less like every other
    sentinel-verdict call here (a schema-bearing call can wedge the Workflow if
    the forwarder detaches, #97)."""
    res = run(tmp_path=tmp_path, batches=[make_batch(0, ["Ninon"])])
    assert res["ok"], res["stderr"]
    review_calls = [c for c in res["out"]["calls"] if c["label"] == "glossary:citation-review:0"]
    assert len(review_calls) == 1
    assert review_calls[0]["agentType"] is None, (
        f"the citation review must be a plain Claude call: {review_calls[0]}"
    )
    assert review_calls[0]["hasSchema"] is False
    assert review_calls[0]["phase"] == "GlossaryPass"
    # effort is pinned "high", not the "low" every other Claude step in this
    # template uses. Those are mechanical (run one command, relay one line);
    # this is the only judgment call in the file -- deciding whether a URL
    # actually supports a specific claim -- and it is the last gate before an
    # immutable canon row. Dropping it to "low" would be an invisible quality
    # regression: nothing else in the suite would go red.
    assert review_calls[0]["effort"] == "high", (
        f"the citation review must stay pinned to high effort: {review_calls[0]}"
    )


def test_malformed_review_reply_rejects_rather_than_approves(tmp_path):
    """Fail-safe direction. A verdict that is absent, garbled, or shaped
    unexpectedly must fall to the REJECT side: a wrong reject costs one
    regeneration, a wrong accept freezes a fabricated citation permanently."""
    plan = {"0": {
        "precheck": "ABSENT 0",
        "reviews": ["I was unable to check the sources.", "CITATIONS_OK 0 ATTEMPT 1"],
    }}
    res = run(tmp_path=tmp_path, batches=[make_batch(0, ["Ninon"])], plan=plan)
    assert res["ok"], res["stderr"]
    out = res["out"]
    assert count_label(out, "glossary:dispatch:0") == 2, (
        "an unparseable verdict must regenerate, never approve; calls were "
        f"{labels_of(out)}"
    )
    assert out["result"]["merged"] is True
    assert out["result"]["batches"][0]["attempt"] == 1


def test_decorated_approval_is_accepted(tmp_path):
    """#308's prose-preamble tolerance applies here too: a genuine approval with
    an explanatory preamble must not be misread as a rejection, or every live
    run would burn its whole retry ladder on well-behaved replies."""
    plan = {"0": {
        "precheck": "ABSENT 0",
        "reviews": ["I fetched both cited pages and each attests the claimed form.\n\nCITATIONS_OK 0 ATTEMPT 0"],
    }}
    res = run(tmp_path=tmp_path, batches=[make_batch(0, ["Ninon"])], plan=plan)
    assert res["ok"], res["stderr"]
    out = res["out"]
    assert count_label(out, "glossary:dispatch:0") == 1
    assert out["result"]["merged"] is True
    assert out["result"]["batches"][0]["attempt"] == 0


def test_review_prompt_scopes_itself_to_established_basis(tmp_path):
    """The reviewer must not re-litigate canonicalization choices -- its remit is
    citations only. A reviewer that rejected on style would burn the ladder and
    block merges over judgments that belong to a later human pass."""
    res = run(tmp_path=tmp_path, batches=[make_batch(0, ["Ninon"])])
    assert res["ok"], res["stderr"]
    prompt = prompts_for(res["out"], "glossary:citation-review:0")[0]
    assert '"established"' in prompt
    assert "transliterated" in prompt and "sense_translated" in prompt, (
        "the prompt must name the out-of-scope basis values explicitly"
    )
    assert "CITATIONS_OK 0 ATTEMPT 0" in prompt
    assert "CITATIONS_REJECTED 0 ATTEMPT 0" in prompt


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
