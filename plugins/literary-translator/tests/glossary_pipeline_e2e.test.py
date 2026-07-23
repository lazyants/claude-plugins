"""tests/glossary_pipeline_e2e.test.py -- #228 exact-match sentinel e2e
harness for glossary-pass-wf.template.js (sites A and B).

No harness for this template existed before this file: every one of the 11
existing test files that reference `glossary-pass-wf` does only a STATIC
parse of the source text (grepping/asserting against the raw string) --
none of them ever EXECUTES the template. That is exactly the blind spot
that let #228's substring-collision bug survive in this file specifically
(a static assertion like `"PRESENT" in source` is happy with either the old
`.indexOf(...)` check or the new exact-match one -- it can't tell them
apart). This file closes that gap the same way
tests/mass_translate_driver_smoke.test.py and
tests/skeptic_pipeline_e2e.test.py already do for their own templates: it
runs the REAL, unmodified glossary-pass-wf.template.js under Node, with a
mocked `agent()`/`pipeline()`/`log()`, against constructed batch fixtures,
and asserts on the actual dispatch/wait control flow -- never a
reimplementation and never a source-string grep.

Site A -- batchStep's resume-skip precheck ("glossary:precheck:" + index).
Site B -- batchStep's fragment-ready wait ("glossary:wait:" + index).

Mirrors skeptic_pipeline_e2e.test.py's own precheck/wait substring-collision
tests for skeptic-pass-wf.template.js (the already-exact-match reference
implementation this fix was modelled on).
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
TEMPLATES_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets" / "templates"
GLOSSARY_TEMPLATE = TEMPLATES_DIR / "glossary-pass-wf.template.js"

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(
    NODE is None,
    reason="node not found on PATH; this test executes the real glossary-pass "
    "template's dispatch/wait wiring under Node (no hard Node.js dependency "
    "for this plugin otherwise)",
)

FIXTURE_DURABLE_ROOT = "/fixture/project/durable_root"
FIXTURE_RUN_ID = "20260719T000000Z"
FIXTURE_SOURCE_LANG = "French"
FIXTURE_TARGET_LANG = "Russian"
FIXTURE_RESEARCH_MODE = "offline"


def instantiate(*, batch_agent_cap: int) -> str:
    """The exact one-time substitution the template's header documents
    (duplicated, not imported, so this file stays self-contained like every
    sibling harness)."""
    text = GLOSSARY_TEMPLATE.read_text(encoding="utf-8")
    text = text.replace("{{DURABLE_ROOT}}", FIXTURE_DURABLE_ROOT)
    text = text.replace("{{SOURCE_LANG}}", FIXTURE_SOURCE_LANG)
    text = text.replace("{{TARGET_LANG}}", FIXTURE_TARGET_LANG)
    text = text.replace("{{RESEARCH_MODE}}", FIXTURE_RESEARCH_MODE)
    text = text.replace("{{RUN_ID}}", FIXTURE_RUN_ID)
    text = text.replace("{{BATCH_AGENT_CAP}}", str(int(batch_agent_cap)))
    # #197 -- engine.effort (no {{MODEL}} here -- the glossary pass has no
    # model knob). Not inspected by this file's dispatch/wait assertions; it
    # only needs to resolve.
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


# The mock records the ACTUAL rendered prompt text per label, counts calls,
# and drives a happy-path run to merged:true by default. PLAN, keyed by each
# batch's own string index ("0", "1", ...), overrides that batch's precheck/
# wait reply; "merge"/"verify" keys override the two batch-level calls.
# Every default matches the EXACT sentinel batchPrecheckPrompt/
# batchWaitPrompt actually instruct the agent to return (see the template's
# own comments at :232-233,284-285), so a test overriding only ONE call
# still gets an ordinary happy path for every other call in the sequence.
HARNESS = r"""
'use strict';
__WRAPPED_SOURCE__

const BATCHES_ARGS = __BATCHES_JSON__;
const PLAN = __PLAN_JSON__;
const promptByLabel = {};
const callsLog = [];
let pipelineCalled = false;

function indexFromLabel(label) {
  const parts = label.split(":");
  return parts[parts.length - 1];
}

async function agent(promptText, opts) {
  opts = opts || {};
  const label = opts.label || "";
  promptByLabel[label] = promptText;
  callsLog.push({ label: label, agentType: opts.agentType || null, hasSchema: !!opts.schema });

  if (label === "glossary:merge") {
    return Object.prototype.hasOwnProperty.call(PLAN, "merge") ? PLAN.merge : "MERGED";
  }
  if (label === "glossary:verify") {
    return Object.prototype.hasOwnProperty.call(PLAN, "verify") ? PLAN.verify : { verified: true };
  }

  const idx = indexFromLabel(label);
  const p = PLAN[idx] || {};
  if (label.indexOf("glossary:precheck:") === 0) {
    return Object.prototype.hasOwnProperty.call(p, "precheck") ? p.precheck : ("ABSENT " + idx);
  }
  if (label.indexOf("glossary:dispatch:") === 0) {
    return "FRAGMENT " + idx;
  }
  if (label.indexOf("glossary:wait:") === 0) {
    return Object.prototype.hasOwnProperty.call(p, "wait") ? p.wait : ("READY " + idx);
  }
  throw new Error("mock agent(): unrecognized label " + label);
}

async function pipeline(items, stage1) {
  pipelineCalled = true;
  const out = [];
  for (const item of items) {
    out.push(await stage1(item));
  }
  return out;
}
function log() {}

(async () => {
  try {
    const result = await __workflowMain__(agent, pipeline, log, BATCHES_ARGS);
    process.stdout.write(JSON.stringify({ result: result, calls: callsLog, promptByLabel: promptByLabel, pipelineCalled: pipelineCalled }));
  } catch (err) {
    process.stderr.write("HARNESS_ERROR: " + (err && err.message || String(err)) + "\n");
    process.exit(1);
  }
})();
"""


def run(*, tmp_path: Path, batches: list, batch_agent_cap: int = 10_000,
        plan: dict | None = None, timeout: int = 30) -> dict:
    """Returns {ok, out, stderr}. ok=False (with stderr) when the template
    threw before producing stdout (the batch-index guard throw path)."""
    plan = plan or {}
    src = instantiate(batch_agent_cap=batch_agent_cap)
    harness = (
        HARNESS.replace("__WRAPPED_SOURCE__", _wrap(src))
        .replace("__BATCHES_JSON__", json.dumps(batches))
        .replace("__PLAN_JSON__", json.dumps(plan))
    )
    p = tmp_path / "glossary_harness.js"
    p.write_text(harness, encoding="utf-8")
    # NODE is only None when `node` is absent from PATH, in which case
    # pytestmark's skipif already skips every test in this file before this
    # call is ever reached -- this assert just narrows that for the type
    # checker rather than casting it away (a real None here would be a
    # genuine bug, not a typing false-positive).
    assert NODE is not None
    proc = subprocess.run([NODE, str(p)], capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        return {"ok": False, "out": None, "stderr": proc.stderr}
    return {"ok": True, "out": json.loads(proc.stdout), "stderr": proc.stderr}


# ---------------------------------------------------------------------------
# Positive controls -- a genuinely current run still behaves correctly. Run
# first / referenced implicitly by every collision test below: if these
# fail, the harness's own fixture-construction approach is unsound.
# ---------------------------------------------------------------------------

def test_happy_path_merges(tmp_path):
    res = run(tmp_path=tmp_path, batches=[make_batch(0, ["Jean"]), make_batch(1, ["Marie"])])
    assert res["ok"], res["stderr"]
    assert res["out"]["result"] == {"batches": res["out"]["result"]["batches"], "merged": True}
    assert res["out"]["pipelineCalled"] is True


def test_precheck_exact_present_resume_skips(tmp_path):
    """Positive control paired with the substring-collision test below: a
    genuine, EXACT "PRESENT <index>" reply DOES resume-skip -- proves the
    collision test below is catching a real false negative, not asserting
    against a mock that never resume-skips at all."""
    res = run(tmp_path=tmp_path, batches=[make_batch(0, ["Jean"])], plan={"0": {"precheck": "PRESENT 0"}})
    assert res["ok"], res["stderr"]
    labels = [c["label"] for c in res["out"]["calls"]]
    assert "glossary:dispatch:0" not in labels
    assert "glossary:wait:0" not in labels
    assert res["out"]["result"]["merged"] is True


# ---------------------------------------------------------------------------
# #228 P1 fixes: exact-match sentinels (content-matching-sentinel-fragility
# class) at glossary-pass-wf.template.js's two sentinel sites -- A (batch
# precheck) and B (batch wait).
# ---------------------------------------------------------------------------

def test_precheck_substring_collision_does_not_falsely_resume_skip(tmp_path):
    """RED before the #228 exact-match fix at site A (batchStep's
    "glossary:precheck:" + batch.index): the OLD
    `precheck.indexOf("PRESENT") !== -1` check falsely matched a FAILURE
    reply that merely contains the literal substring "PRESENT" inside its
    own explanatory prose (e.g. "ABSENT 0 (fragment missing; not
    PRESENT)"), resume-skipping WITHOUT dispatching -- so a recoverable
    missing/corrupt fragment would silently never be repaired on resume."""
    plan = {"0": {"precheck": "ABSENT 0 (fragment missing; not PRESENT)"}}
    res = run(tmp_path=tmp_path, batches=[make_batch(0, ["Jean"])], plan=plan)
    assert res["ok"], res["stderr"]
    labels = [c["label"] for c in res["out"]["calls"]]
    # A substring-collision bug would resume-skip straight from precheck to
    # merge/verify, never calling dispatch/wait at all.
    assert "glossary:dispatch:0" in labels
    assert "glossary:wait:0" in labels
    assert res["out"]["result"]["merged"] is True


def test_wait_substring_collision_reports_not_ready(tmp_path):
    """RED before the #228 exact-match fix at site B (batchStep's
    "glossary:wait:" + batch.index): the OLD
    `ready.indexOf("READY") === -1` check falsely treated a TIMEOUT reply
    that merely contains the literal substring "READY" inside its own
    explanatory prose (e.g. "TIMEOUT 0 (not READY)") as ready -- `indexOf`
    finds "READY" so the negated `=== -1` check was false, letting an
    unconfirmed fragment reach the merge step."""
    plan = {"0": {"precheck": "ABSENT 0", "wait": "TIMEOUT 0 (not READY)"}}
    res = run(tmp_path=tmp_path, batches=[make_batch(0, ["Jean"])], plan=plan)
    assert res["ok"], res["stderr"]
    out = res["out"]
    assert out["result"]["merged"] is False
    assert out["result"]["reason"] == "fragment-check-failed"
    assert out["result"]["notReady"] == [0]
    labels = [c["label"] for c in out["calls"]]
    assert "glossary:merge" not in labels
    assert "glossary:verify" not in labels


def test_wait_substring_collision_in_one_of_two_batches(tmp_path):
    """Same as above but with a second, healthy batch alongside it -- proves
    the collision is caught per-batch, not just in a single-batch fixture,
    and that a healthy sibling batch does not mask the sick one."""
    plan = {"0": {"precheck": "ABSENT 0", "wait": "TIMEOUT 0 (not READY)"}}
    res = run(tmp_path=tmp_path, batches=[make_batch(0, ["Jean"]), make_batch(1, ["Marie"])], plan=plan)
    assert res["ok"], res["stderr"]
    out = res["out"]
    assert out["result"]["merged"] is False
    assert out["result"]["reason"] == "fragment-check-failed"
    assert out["result"]["notReady"] == [0]


# ---------------------------------------------------------------------------
# #308 P1 fixes: line-oriented sentinel verdicts (sentinelVerdict()) at
# glossary-pass-wf.template.js's two sentinel sites -- A (batch precheck)
# and B (batch wait). #228 (above) killed the substring false-POSITIVE;
# #308 is the false-NEGATIVE dual #228's own whole-string cure introduced --
# a benign prose-decorated sentinel misclassified as absent/timed-out.
# ---------------------------------------------------------------------------

def test_precheck_decorated_present_still_resume_skips(tmp_path):
    """Site A accept: a genuine PRESENT reply decorated with a prose
    preamble (the observed real #308 shape) must still resume-skip, not
    fall through to a full dispatch."""
    plan = {"0": {"precheck": "The precheck command exited 0, confirming the existing fragment is already valid.\n\nPRESENT 0"}}
    res = run(tmp_path=tmp_path, batches=[make_batch(0, ["Jean"])], plan=plan)
    assert res["ok"], res["stderr"]
    labels = [c["label"] for c in res["out"]["calls"]]
    assert "glossary:dispatch:0" not in labels
    assert "glossary:wait:0" not in labels
    assert res["out"]["result"]["merged"] is True


def test_wait_decorated_ready_is_accepted_not_timeout(tmp_path):
    """Site B accept: a genuine READY reply decorated with a prose preamble
    (the exact #308 evidence reply, journal-verbatim) must be accepted, not
    misclassified as a timeout."""
    plan = {"0": {
        "precheck": "ABSENT 0",
        "wait": "The poll confirmed the review artifact is ready (exit 0).\n\nREADY 0",
    }}
    res = run(tmp_path=tmp_path, batches=[make_batch(0, ["Jean"])], plan=plan)
    assert res["ok"], res["stderr"]
    out = res["out"]
    assert out["result"]["merged"] is True
    labels = [c["label"] for c in out["calls"]]
    assert "glossary:merge" in labels
    assert "glossary:verify" in labels


def test_precheck_fail_priority_discriminating_order(tmp_path):
    """Fail-priority, discriminating order (PLAN-308 sec3 item 3's round-3
    codex finding): ABSENT before a trailing PRESENT line must still
    regenerate -- proves the fail-sentinel scan runs over every line, not
    just the last one (a last-line-only reader would wrongly accept this,
    since PRESENT is the reply's own final line)."""
    plan = {"0": {"precheck": "ABSENT 0\nPRESENT 0"}}
    res = run(tmp_path=tmp_path, batches=[make_batch(0, ["Jean"])], plan=plan)
    assert res["ok"], res["stderr"]
    labels = [c["label"] for c in res["out"]["calls"]]
    assert "glossary:dispatch:0" in labels
    assert "glossary:wait:0" in labels
    assert res["out"]["result"]["merged"] is True


def test_wait_fail_priority_discriminating_order(tmp_path):
    """Same discriminating-order proof at site B: TIMEOUT before a trailing
    READY line must still time out."""
    plan = {"0": {"precheck": "ABSENT 0", "wait": "TIMEOUT 0\nREADY 0"}}
    res = run(tmp_path=tmp_path, batches=[make_batch(0, ["Jean"])], plan=plan)
    assert res["ok"], res["stderr"]
    out = res["out"]
    assert out["result"]["merged"] is False
    assert out["result"]["reason"] == "fragment-check-failed"
    assert out["result"]["notReady"] == [0]


def test_precheck_non_terminal_quoted_present_still_regenerates(tmp_path):
    """5a non-terminal quoted-success regression (required, not optional):
    a reply that quotes the PRESENT sentinel on a non-final line, then
    disavows it in later prose, must NOT resume-skip -- the sentinel must be
    the reply's own final non-empty line, not merely present anywhere."""
    plan = {"0": {"precheck": "The command failed; quoting the requested success form:\nPRESENT 0\nThat is not my verdict."}}
    res = run(tmp_path=tmp_path, batches=[make_batch(0, ["Jean"])], plan=plan)
    assert res["ok"], res["stderr"]
    labels = [c["label"] for c in res["out"]["calls"]]
    assert "glossary:dispatch:0" in labels
    assert "glossary:wait:0" in labels
    assert res["out"]["result"]["merged"] is True


def test_wait_non_terminal_quoted_ready_still_times_out(tmp_path):
    """5a non-terminal quoted-success regression at site B (codex's own
    counter-example, reused verbatim): a reply that quotes READY on a
    non-final line, then disavows it, must still report a timeout."""
    plan = {"0": {
        "precheck": "ABSENT 0",
        "wait": "The command failed; quoting the requested success form:\nREADY 0\nThat is not my verdict.",
    }}
    res = run(tmp_path=tmp_path, batches=[make_batch(0, ["Jean"])], plan=plan)
    assert res["ok"], res["stderr"]
    out = res["out"]
    assert out["result"]["merged"] is False
    assert out["result"]["reason"] == "fragment-check-failed"
    assert out["result"]["notReady"] == [0]


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
