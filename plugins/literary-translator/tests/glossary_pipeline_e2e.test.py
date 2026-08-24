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

SITE A NO LONGER EXISTS (#724), and its tests are gone rather than adapted.
The resume-skip is not a reply any more: resume_setup.py re-checks each
attempt-0 fragment before the Workflow starts and the answer is substituted as
{{RESUMED_BATCH_INDICES}}, so there is no prose for a sentinel to be decorated,
glued or quoted inside. Every site-A case below (#228's substring collision,
#308's decorated accept, the fail-priority and non-terminal-quote regressions)
was a property OF THAT PARSE and is retired with it -- not relocated, because
nothing here has the shape they were about. What replaces them is narrower and
about the new mechanism: the array decides, and no precheck call is made.
Site B's own cases are untouched; the two sites never shared code, only shape.
The skeptic template still has both sites, and skeptic_pipeline_e2e.test.py
still exercises them -- so the retired properties are still under test where the
mechanism they describe still runs.

Mirrors skeptic_pipeline_e2e.test.py's own precheck/wait substring-collision
tests for skeptic-pass-wf.template.js (the already-exact-match reference
implementation this fix was modelled on).
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
TEMPLATES_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets" / "templates"
GLOSSARY_TEMPLATE = TEMPLATES_DIR / "glossary-pass-wf.template.js"

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _workflow_instantiation import instantiate_glossary_pass  # noqa: E402

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(
    NODE is None,
    reason="node not found on PATH; this test executes the real glossary-pass "
    "template's dispatch/wait wiring under Node (no hard Node.js dependency "
    "for this plugin otherwise)",
)

FIXTURE_DURABLE_ROOT = "/fixture/project/durable_root"
FIXTURE_RUN_ID = "20260719T000000Z"
FIXTURE_RESEARCH_MODE = "offline"
# #412 -- glossary-pass-wf.template.js's {{PLUGIN_ROOT}} is REQUIRED: unlike
# mass-translate-wf.template.js's own {{PLUGIN_ROOT}} (a pre-#412 token with
# real legacy callers relying on its flagless-default empty-string opt-out),
# this one is brand new, and the template throws at instantiation for an
# empty value -- there is no caller to preserve a silent flagless
# --merge-batches default for, and this is exactly the pass where codex
# holds --write over ${durable_root}/scripts/. So this file's DEFAULT must be
# a real, non-empty, allowlist-legal path -- every test below that does not
# care about PLUGIN_ROOT still gets a working run; the dedicated tests near
# the end of this file override it with either another real path or the
# empty string, to exercise the opt-in / throw explicitly.
FIXTURE_PLUGIN_ROOT = "/fixture/plugin/root/skills/literary-translator"


def instantiate(*, batch_agent_cap: int, plugin_root: str = FIXTURE_PLUGIN_ROOT,
                resumed_batch_indices: list | None = None) -> str:
    """The token map and renderer now live in _workflow_instantiation.py
    (#413); this stays a thin wrapper. FIXTURE_RESEARCH_MODE and plugin_root
    are the two overrides this file needs against the shared module's own
    GLOSSARY_PASS_DEFAULTS -- research_mode because this file's default is
    "offline" (the module default is "live"), and plugin_root because the
    tests below exercise it with special/empty/hostile values that alter
    control flow (see FIXTURE_PLUGIN_ROOT's own comment above: empty is NOT
    a valid value for this template, so the default here is a real path, and
    the PLUGIN_ROOT-specific tests pass a different real value or the empty
    string through this same parameter to exercise the instantiation-time
    throw)."""
    return instantiate_glossary_pass(
        durable_root=FIXTURE_DURABLE_ROOT,
        run_id=FIXTURE_RUN_ID,
        research_mode=FIXTURE_RESEARCH_MODE,
        batch_agent_cap=batch_agent_cap,
        plugin_root=plugin_root,
        resumed_batch_indices=resumed_batch_indices or [],
    )


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
# batch's own string index ("0", "1", ...), overrides that batch's wait reply;
# "merge"/"verify" keys override the two batch-level calls.
# Every default matches the EXACT sentinel batchWaitChunkPrompt actually
# instructs the agent to return, so a test overriding only ONE call
# still gets an ordinary happy path for every other call in the sequence.
# There is no "precheck" key since #724: the resume decision is a substituted
# array, so a run selects ENTRY A through run(resumed_batch_indices=[...]),
# never through a reply.
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
  if (label.indexOf("glossary:dispatch:") === 0) {
    return "FRAGMENT " + idx;
  }
  // 1.16.2 (#352) -- the re-check branch is tested FIRST and by its own
  // distinct prefix. "glossary:wait-recheck:0" does not start with
  // "glossary:wait:", so ordering alone would not have saved this, but naming
  // it explicitly documents that these are two different questions to the mock.
  //
  // `recheck` DEFAULTS TO THE SAME REPLY AS `wait`, which is what keeps every
  // pre-1.16.2 fixture in this file meaning what it meant. Those fixtures say
  // "a wait reply shaped like THIS must not be read as ready"; under a chunked
  // wait that is only observable end-to-end if the authoritative re-check
  // answers the same way. Defaulting it to READY would have turned every one of
  // them green through the re-check while the property under test was broken.
  if (label.indexOf("glossary:wait-recheck:") === 0) {
    if (Object.prototype.hasOwnProperty.call(p, "recheck")) return p.recheck;
    return Object.prototype.hasOwnProperty.call(p, "wait") ? p.wait : ("READY " + idx);
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
        plan: dict | None = None, timeout: int = 30,
        plugin_root: str = FIXTURE_PLUGIN_ROOT,
        resumed_batch_indices: list | None = None) -> dict:
    """Returns {ok, out, stderr}. ok=False (with stderr) when the template
    threw before producing stdout (the batch-index guard throw path).

    `resumed_batch_indices` (#724) selects ENTRY A for the named batches. It is
    a TOKEN, substituted before the run starts, which is the whole shape of the
    change: a fixture can no longer make the resume decision come out differently
    by phrasing a reply, because nothing reads a reply."""
    plan = plan or {}
    src = instantiate(batch_agent_cap=batch_agent_cap, plugin_root=plugin_root,
                      resumed_batch_indices=resumed_batch_indices)
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


def test_a_batch_named_in_resumed_batch_indices_skips_dispatch_and_wait(tmp_path):
    """ENTRY A, and the whole of what #724 left of site A: membership in the
    substituted array -- not a reply -- decides the resume-skip.

    Positive control for the negative one below: without it, "no dispatch call"
    could just as well mean the harness never reaches batchStep at all."""
    res = run(tmp_path=tmp_path, batches=[make_batch(0, ["Jean"])], resumed_batch_indices=[0])
    assert res["ok"], res["stderr"]
    labels = [c["label"] for c in res["out"]["calls"]]
    assert "glossary:dispatch:0" not in labels
    assert "glossary:wait:0" not in labels
    assert res["out"]["result"]["merged"] is True


def test_an_unlisted_batch_dispatches_and_the_resume_decision_costs_no_call(tmp_path):
    """The other direction, plus the reason #724 exists: a batch the array does
    not name takes the ordinary dispatch path, and NOTHING was asked.

    The precheck-label assertion is the load-bearing one and is not redundant
    with the parity suite's structural check: that one proves the template holds
    no PRESENT/ABSENT parse, this one proves no agent call is spent on the
    question under a real run. A reinstated precheck that answered correctly
    would pass every other assertion in this file."""
    res = run(tmp_path=tmp_path, batches=[make_batch(0, ["Jean"])], resumed_batch_indices=[])
    assert res["ok"], res["stderr"]
    labels = [c["label"] for c in res["out"]["calls"]]
    assert "glossary:dispatch:0" in labels
    assert "glossary:wait:0" in labels
    assert not [x for x in labels if x.startswith("glossary:precheck:")], (
        "the resume decision must cost no agent call at all since #724; labels "
        f"this run spent were {labels}"
    )
    assert res["out"]["result"]["merged"] is True


def test_a_partial_resumed_set_is_read_per_batch(tmp_path):
    """Membership is per batch, not a run-wide mode -- the property a Set-based
    read makes easy to get wrong in the direction that is invisible on a
    single-batch fixture."""
    res = run(
        tmp_path=tmp_path,
        batches=[make_batch(0, ["Jean"]), make_batch(1, ["Marie"])],
        resumed_batch_indices=[1],
    )
    assert res["ok"], res["stderr"]
    labels = [c["label"] for c in res["out"]["calls"]]
    assert "glossary:dispatch:0" in labels and "glossary:wait:0" in labels
    assert "glossary:dispatch:1" not in labels and "glossary:wait:1" not in labels
    assert res["out"]["result"]["merged"] is True


def test_a_non_array_resumed_batch_indices_throws_at_startup(tmp_path):
    """The token's own guard. A scalar or string here means the instantiating
    session substituted something other than resume_setup.py's array, and the
    template refuses rather than building a Set whose `.has()` silently answers
    false for every batch -- which would look exactly like a fresh run and cost
    a full re-dispatch of work already done.

    Mutated after instantiation rather than passed through it: the shared
    encoder would coerce the value, which is the point -- this asserts the
    TEMPLATE's own guard, not the fixture helper's."""
    src = instantiate(batch_agent_cap=10_000).replace(
        "const RESUMED_BATCH_INDICES = []", 'const RESUMED_BATCH_INDICES = "0"', 1
    )
    assert 'const RESUMED_BATCH_INDICES = "0"' in src, (
        "the mutation did not apply -- the declaration's spelling moved"
    )
    harness = (
        HARNESS.replace("__WRAPPED_SOURCE__", _wrap(src))
        .replace("__BATCHES_JSON__", json.dumps([make_batch(0, ["Jean"])]))
        .replace("__PLAN_JSON__", json.dumps({}))
    )
    path = tmp_path / "glossary_harness_bad_token.js"
    path.write_text(harness, encoding="utf-8")
    assert NODE is not None
    proc = subprocess.run([NODE, str(path)], capture_output=True, text=True, timeout=30)
    assert proc.returncode != 0, (
        f"a non-array RESUMED_BATCH_INDICES must throw; the run instead exited 0 "
        f"with {proc.stdout[:400]}"
    )
    assert "RESUMED_BATCH_INDICES must be a JSON array" in proc.stderr, proc.stderr


# ---------------------------------------------------------------------------
# #228 P1 fixes: exact-match sentinels (content-matching-sentinel-fragility
# class) at glossary-pass-wf.template.js's batch WAIT site. Site A (the batch
# precheck) was the other half until #724 deleted it -- see the module
# docstring; its cases are retired, not relocated.
# ---------------------------------------------------------------------------

def test_wait_substring_collision_reports_not_ready(tmp_path):
    """RED before the #228 exact-match fix at site B (batchStep's
    "glossary:wait:" + batch.index): the OLD
    `ready.indexOf("READY") === -1` check falsely treated a not-ready reply
    that merely contains the literal substring "READY" inside its own
    explanatory prose (e.g. "PENDING 0 (not READY)") as ready -- `indexOf`
    finds "READY" so the negated `=== -1` check was false, letting an
    unconfirmed fragment reach the merge step.

    1.16.2 (#352) renamed the non-ready sentinel TIMEOUT -> PENDING, and the
    rename is the point rather than cosmetics: under the pre-1.16.2 single-shot
    poll a non-READY reply WAS a timeout and ended the batch, while a chunk that
    reports PENDING only means THIS chunk learned nothing. The #228 property is
    unchanged and is re-pointed at the sentinel the template can actually
    emit -- asserted against PENDING because a fixture still saying TIMEOUT
    would be exercising a string no template site produces or reads, and would
    pass for the wrong reason (an unrecognized sentinel is PENDING by default)."""
    plan = {"0": {"wait": "PENDING 0 (not READY)"}}
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
    plan = {"0": {"wait": "PENDING 0 (not READY)"}}
    res = run(tmp_path=tmp_path, batches=[make_batch(0, ["Jean"]), make_batch(1, ["Marie"])], plan=plan)
    assert res["ok"], res["stderr"]
    out = res["out"]
    assert out["result"]["merged"] is False
    assert out["result"]["reason"] == "fragment-check-failed"
    assert out["result"]["notReady"] == [0]


# ---------------------------------------------------------------------------
# #308 P1 fixes: line-oriented sentinel verdicts (sentinelVerdict()) at
# glossary-pass-wf.template.js's batch WAIT site (site A, the batch precheck,
# is gone since #724). #228 (above) killed the substring false-POSITIVE;
# #308 is the false-NEGATIVE dual #228's own whole-string cure introduced --
# a benign prose-decorated sentinel misclassified as absent/timed-out.
# ---------------------------------------------------------------------------

def test_wait_decorated_ready_is_accepted_not_timeout(tmp_path):
    """Site B accept: a genuine READY reply decorated with a prose preamble
    (the exact #308 evidence reply, journal-verbatim) must be accepted, not
    misclassified as a timeout."""
    plan = {"0": {
        "wait": "The poll confirmed the review artifact is ready (exit 0).\n\nREADY 0",
    }}
    res = run(tmp_path=tmp_path, batches=[make_batch(0, ["Jean"])], plan=plan)
    assert res["ok"], res["stderr"]
    out = res["out"]
    assert out["result"]["merged"] is True
    labels = [c["label"] for c in out["calls"]]
    assert "glossary:merge" in labels
    assert "glossary:verify" in labels


def test_wait_fail_priority_discriminating_order(tmp_path):
    """Same discriminating-order proof at site B: PENDING before a trailing
    READY line must still be read as not-ready.

    This one is NOT sentinelVerdict's own fail-priority scan any more. #352's
    waitChunkVerdict tests the PENDING containment guard FIRST and then calls
    sentinelVerdict with a NULL fail sentinel, so what keeps this reply from
    being read as ready is the guard's raw containment, not a whole-line fail
    scan. Same verdict, different mechanism -- which is why the assertion is
    kept rather than folded into the collision test above: the two now exercise
    different code."""
    plan = {"0": {"wait": "PENDING 0\nREADY 0"}}
    res = run(tmp_path=tmp_path, batches=[make_batch(0, ["Jean"])], plan=plan)
    assert res["ok"], res["stderr"]
    out = res["out"]
    assert out["result"]["merged"] is False
    assert out["result"]["reason"] == "fragment-check-failed"
    assert out["result"]["notReady"] == [0]


def test_wait_non_terminal_quoted_ready_still_times_out(tmp_path):
    """5a non-terminal quoted-success regression at site B (codex's own
    counter-example, reused verbatim): a reply that quotes READY on a
    non-final line, then disavows it, must still report a timeout."""
    plan = {"0": {
        "wait": "The command failed; quoting the requested success form:\nREADY 0\nThat is not my verdict.",
    }}
    res = run(tmp_path=tmp_path, batches=[make_batch(0, ["Jean"])], plan=plan)
    assert res["ok"], res["stderr"]
    out = res["out"]
    assert out["result"]["merged"] is False
    assert out["result"]["reason"] == "fragment-check-failed"
    assert out["result"]["notReady"] == [0]


# ---------------------------------------------------------------------------
# #412 -- PLUGIN_ROOT is threaded into mergeBatchesPrompt()'s --merge-batches
# command ONLY, never checkBatchCmd() (precheck/dispatch/wait) or
# glossaryVerifyPrompt() -- canon_validate.py's own main() forwards
# --plugin-root to run_merge_batches but not to run_check_batch or
# run_verify_merged, so the flag would be silently ignored at either site
# (see the template's own {{PLUGIN_ROOT}} header-comment entry). This file is
# the natural home for that assertion: it is the only harness in this plugin
# that already drives the real template through a full happy-path run and
# captures every agent() prompt by label, so the exact command text each
# builder produced is directly inspectable rather than re-derived from a
# static grep.
# ---------------------------------------------------------------------------

PINNED_PLUGIN_ROOT = "/Users/José García/.claude/plugins/literary-translator/skills/literary-translator"


def test_glossary_template_declares_plugin_root_token():
    raw = GLOSSARY_TEMPLATE.read_text(encoding="utf-8")
    assert "{{PLUGIN_ROOT}}" in raw


def test_merge_batches_command_carries_plugin_root_when_set(tmp_path):
    res = run(
        tmp_path=tmp_path,
        batches=[make_batch(0, ["Jean"])],
        plugin_root=PINNED_PLUGIN_ROOT,
    )
    assert res["ok"], res["stderr"]
    merge_prompt = res["out"]["promptByLabel"]["glossary:merge"]
    assert "--plugin-root '" + PINNED_PLUGIN_ROOT + "'" in merge_prompt, (
        "mergeBatchesPrompt()'s --merge-batches command must carry "
        "--plugin-root when PLUGIN_ROOT is set: " + merge_prompt
    )


def test_check_batch_and_verify_merged_commands_never_carry_plugin_root(tmp_path):
    """canon_validate.py's main() forwards --plugin-root only to
    run_merge_batches (`elif args.merge_batches is not None:`) -- never to
    run_check_batch (`elif args.check_batch is not None:`) or
    run_verify_merged (`elif args.verify_merged:`). Adding it to either site
    would be silent decoration in the CLI and would grow the population of
    the separate open #608 -- this pins the asymmetry so a future
    "consistency" edit that widens it is a RED, not a quiet drift.
    checkBatchCmd() is issued character-identically at all three call sites
    (dispatch self-check, wait chunk poll, wait re-check -- a fourth, the
    precheck, is gone since #724); this asserts on the WAIT CHUNK site, which
    the ordinary happy path already reaches. The precheck used to be the one
    read here, and it was the weakest choice of the four even then: its prompt
    carried the command on a line of its own, so a --plugin-root leaking into
    the poll's own gate line would not have shown up at all."""
    res = run(
        tmp_path=tmp_path,
        batches=[make_batch(0, ["Jean"])],
        plugin_root=PINNED_PLUGIN_ROOT,
    )
    assert res["ok"], res["stderr"]
    wait_prompt = res["out"]["promptByLabel"]["glossary:wait:0"]
    verify_prompt = res["out"]["promptByLabel"]["glossary:verify"]
    assert "--check-batch" in wait_prompt, (
        "the wait prompt must actually carry the --check-batch command for this "
        "assertion to mean anything: " + wait_prompt
    )
    assert "--plugin-root" not in wait_prompt, (
        "checkBatchCmd()'s --check-batch command must never carry "
        "--plugin-root -- canon_validate.py's run_check_batch does not "
        "accept it: " + wait_prompt
    )
    assert "--plugin-root" not in verify_prompt, (
        "glossaryVerifyPrompt()'s --verify-merged command must never carry "
        "--plugin-root -- canon_validate.py's run_verify_merged does not "
        "accept it: " + verify_prompt
    )


# ---------------------------------------------------------------------------
# #109 -- the background routing control, asserted on the prompt a real run
# EMITS rather than on the source that builds it.
#
# tests/bounded_poll_present.test.py pins this line by shape, in the template
# SOURCE, for every codex dispatch this plugin ships -- a claim about text.
# This one closes the gap between that text and the wire by reading the string
# the harness's own agent() mock received under `glossary:dispatch:0`, so a
# refactor that keeps the push but stops RENDERING it first fails here.
# ---------------------------------------------------------------------------


def test_dispatch_prompt_opens_with_the_background_routing_line(tmp_path):
    res = run(tmp_path=tmp_path, batches=[make_batch(0, ["Jean"])])
    assert res["ok"], res["stderr"]
    dispatch_prompt = res["out"]["promptByLabel"]["glossary:dispatch:0"]
    first_line = dispatch_prompt.split("\n")[0]
    assert first_line == "--background", (
        "the codex dispatch prompt's FIRST rendered line must be the bare "
        "routing control --background, so the codex:codex-rescue forwarder is "
        "given an explicit choice instead of picking foreground by its own "
        "heuristic and running the codex turn inside its single Bash call "
        "(#109). First line was instead: " + repr(first_line)
    )


def test_plugin_root_guard_throws_on_unsafe_value(tmp_path):
    """The PLUGIN_ROOT_UNSAFE_RE guard runs at module top level, well before
    this template ever calls agent()/pipeline() -- so a value containing a
    single quote must abort the whole run synchronously, the same
    fail-closed shape as mass-translate-wf.template.js's own PLUGIN_ROOT
    guard (and the same reason run_guard_harness-style tests in
    tests/seg_safety_source_and_workflow.test.py never need a real
    agent()/pipeline() mock to prove a top-level guard throws)."""
    res = run(
        tmp_path=tmp_path,
        batches=[make_batch(0, ["Jean"])],
        plugin_root="/fake/plugin'; touch pwned; echo '",
    )
    assert not res["ok"]
    assert "Unsafe plugin_root value" in res["stderr"], res["stderr"]


def test_plugin_root_empty_value_throws_at_instantiation(tmp_path):
    """#412 follow-up decision: unlike mass-translate-wf.template.js's own
    {{PLUGIN_ROOT}} (a pre-#412 token with real legacy callers relying on a
    flagless-default empty-string opt-out), this template's {{PLUGIN_ROOT}}
    is brand new -- so an empty value throws at instantiation instead of
    silently building a --merge-batches command with no --plugin-root that
    would only fail later, mid-pass, after codex spend on this batch is
    already paid. Same top-level-guard shape as the unsafe-value throw
    above -- runs before agent()/pipeline() are ever called."""
    res = run(
        tmp_path=tmp_path,
        batches=[make_batch(0, ["Jean"])],
        plugin_root="",
    )
    assert not res["ok"]
    assert "plugin_root is required" in res["stderr"], res["stderr"]


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
