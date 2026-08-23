"""tests/mass_translate_driver_smoke.test.py -- #198 Workflow-level smoke.

Two DETERMINISTIC automated layers for the #198 driver-dispatch reshape of
`mass-translate-wf.template.js` (per PLAN-198 §4). They do NOT (and do not
claim to) exercise a live plain-Claude Workflow agent turn -- that is the
MANUAL pre-ship procedure (asciinema-screencast a real 1-segment W5 run,
observe on-disk convergence AND that the canonical appears AFTER the
dispatcher agent returned = proof of detach). There is no in-repo Workflow
runner/API, so like batch_size_estimator.test.py this file runs the REAL
substituted template under Node with a mocked `agent()`/`pipeline()`/`log()`
and asserts against the ACTUAL rendered prompt strings + the real dispatch/
wait/consume wiring.

Layer 1 -- Contract (rendered prompt shape). Instantiate the template, run
the default happy-path flow, capture the ACTUAL translateDrivePrompt /
reviewDrivePrompt / waitPrompt / reviewWaitPrompt strings, and assert
PLAN §4 (a)-(f):
  (a) the drive prompt generates a per-dispatch DISP, writes the codex
      task-file, launches codex_job.py DETACHED (nohup ... </dev/null
      >/dev/null 2>&1 & -- NO setsid, NO timeout), returns DISPATCHED <seg>
      <DISP>, writes NO .codex_disp sidecar;
  (b) it invokes codex_job.py --kind ... --companion '...' --cwd ...
      --expect-token ... --disp ... (COMPANION single-quoted);
  (c) the codex TASK TEXT carries the durable-TASK SUPERSEDE clause (forbids
      the CANONICAL path, names ⟦JOB_OUT⟧ as the sole segments/ write) +
      EXACTLY ONE ⟦JOB_OUT⟧ placeholder, and DISP is NOT in the task text;
  (d) NO agentType:"codex...";
  (e) the WAIT ACCEPT runs the FULL canonical gate directly (translate:
      draft_ready.py --expect-token AND validate_draft.py; review:
      review_ready.py), NO external timeout binary, its FAIL-FAST is a
      `[ -f .codex_failed.<seg>.<disp> ]` presence check keyed on the DISP,
      evaluated ONLY AFTER ACCEPT;
  (f) the WAIT loop is gate -> [ $SECONDS -ge $end ] && break -> clamped
      sleep, with NO separate post-loop gate (exactly one gate straddles the
      deadline).

Layer 2 -- Execution wiring (mocked agent()/pipeline()). Assert PLAN §4 (g)
+ the SEGS uniqueness guard + safe degradation:
  * the SEGS uniqueness guard THROWS before pipeline() on a duplicate seg id
    (a duplicate `args`/manifest-derived SEGS -- SEGS == args, so both reduce
    to a duplicate in the dispatch array), and a UNIQUE SEGS dispatches
    normally to convergence;
  * a valid `DISPATCHED <seg> <DISP>` return threads that DISP into the wait
    poll's fail-fast sentinel path;
  * an INJECTION-y / wrong-seg / extra-text drive return yields disp="" (the
    HIGH-3 anchored grammar), so the wait command contains NO injected token
    and NO fail-fast clause (safe degradation -- polls to the bound).
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from _workflow_instantiation import instantiate_mass_translate  # noqa: E402

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
TEMPLATES_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets" / "templates"
MASS_TRANSLATE_TEMPLATE = TEMPLATES_DIR / "mass-translate-wf.template.js"

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(
    NODE is None,
    reason="node not found on PATH; this test executes the real workflow "
    "template's dispatch/wait wiring under Node (no hard Node.js dependency "
    "for this plugin otherwise)",
)

FIXTURE_DURABLE_ROOT = "/fixture/project/durable_root"
FIXTURE_RUN_ID = "20260716T000000Z"
FIXTURE_COMPANION_PATH = "/opt/codex/1.0.10/codex-companion.mjs"
# #197 -- a non-default enum value (never the shipped "high" default) so a
# template that silently dropped --effort (the exact "profile effort never
# reaches codex" regression this file's docstring calls out) would be caught.
FIXTURE_EFFORT = "xhigh"
# Empty string = engine.model unset -- the mass template's own documented
# sentinel for "no --model flag threaded to codex_job.py".
FIXTURE_MODEL = ""
# #412 -- empty string = not opted into the --plugin-root redirect (the
# mass template's own documented sentinel, mirroring FIXTURE_MODEL above).
# #607 -- was "" ("not opted into the redirect"). The W5 template now REFUSES
# to start without a plugin root, because the fix-scope audit runs only from
# the plugin install tree, so every fixture that executes the workflow needs a
# real value. Tests that specifically exercise the opt-out/absent-redirect
# shape pass plugin_root="" explicitly at their own call site.
FIXTURE_PLUGIN_ROOT = "/fixture/plugin/literary-translator"


def instantiate(*, max_fix_rounds: int, batch_agent_cap: int, max_codex_jobs_per_batch: int = 100000,
                 effort: str = FIXTURE_EFFORT, model: str = FIXTURE_MODEL,
                 plugin_root: str = FIXTURE_PLUGIN_ROOT) -> str:
    """The token map and its encoding now live in _workflow_instantiation.py
    (#413). `RUN_ID` and `CODEX_COMPANION_PATH_JSON` are this file's own
    values; `EFFORT` defaults to `FIXTURE_EFFORT` ("xhigh", never the shipped
    "high" default) so a template that silently dropped `--effort` would be
    caught -- an assertion below checks it. `DURABLE_ROOT`, `SOURCE_LANG`,
    `TARGET_LANG` and `VERSE_POLICY_INSTRUCTION_BLOCK` already equal the
    shared module's defaults and are left unset. `MODEL` and `PLUGIN_ROOT`
    stay explicit passthroughs of this function's own parameters -- callers
    exercising the opt-out/absent-redirect shape pass `plugin_root=""` at
    their own call site (#412/#607), and a plain default here would mask
    that override silently going nowhere."""
    return instantiate_mass_translate(
        run_id=FIXTURE_RUN_ID,
        max_fix_rounds=max_fix_rounds,
        batch_agent_cap=batch_agent_cap,
        max_codex_jobs_per_batch=max_codex_jobs_per_batch,
        codex_companion_path_json=FIXTURE_COMPANION_PATH,
        effort=effort,
        model=model,
        plugin_root=plugin_root,
    )


def _wrap(js_source: str) -> str:
    assert js_source.count("export const meta") == 1
    body = js_source.replace("export const meta", "const meta", 1)
    return "async function __workflowMain__(agent, pipeline, log, args) {\n" + body + "\n}\n"


# The mock records the ACTUAL rendered prompt text per label, counts calls,
# and drives a one-round happy path to convergence. DRIVE_RETURNS lets a test
# override the translate/review dispatcher return (default: a valid
# DISPATCHED <seg> <DISP>). OVERRIDES is a more general escape hatch, keyed
# by the EXACT label string, that short-circuits every other branch below --
# used by the #228 exact-match sentinel tests to inject a substring-collision
# or falsy/null reply at review-wait:*, wait:*, or fix:* without having to
# thread a new bespoke parameter through this harness for each site.
#
# #348 -- A WAIT IS NO LONGER ONE CALL. Each wait site now makes up to
# WAIT_CHUNKS bounded chunk calls that REUSE that site's existing label
# ("wait:<seg>" / "review-wait:<seg>:r<round>"), then ONE authoritative
# non-polling re-check under a NEW label containing "-recheck:". Two
# consequences this harness has to carry deliberately:
#
#   * promptByLabel keeps ONE prompt per label, so for a chunked wait it holds
#     the LAST chunk's prompt, not the first. Every Contract-layer test below
#     inspects a wait prompt from the happy path, where the first chunk answers
#     READY and the loop stops -- so exactly one chunk ran and the recorded
#     prompt IS chunk 1's. test_wait_poll_shape_accept_failfast_deadline
#     asserts that call count rather than assuming it, so a future change that
#     made the happy path poll twice fails loudly instead of silently swapping
#     which chunk's prompt is under assertion.
#   * a re-check label must be classified by CONTAINMENT of "-recheck:", never
#     a prefix test: the review site's re-check label is
#     "review-wait-recheck:<seg>:r<round>", which a prefix test against
#     "wait-recheck:" would miss, and neither re-check label contains its
#     site's chunk label as a prefix -- so without an explicit branch every
#     test in this file dies on "unrecognized label".
#
# The re-check's default answer is PENDING (fail-safe: a wait can only FAIL by
# default, never falsely converge). OVERRIDES is consulted first, so a test
# that wants a landing-after-the-last-chunk artifact scripts the re-check label
# directly; tests/wait_chunking.test.py owns that direction end to end.
HARNESS = r"""
'use strict';
__WRAPPED_SOURCE__

const SEGS_ARGS = __SEGS_JSON__;
const DRIVE_RETURNS = __DRIVE_RETURNS_JSON__;
const OVERRIDES = __OVERRIDES_JSON__;
const promptByLabel = {};
const callsLog = [];
let pipelineCalled = false;

async function agent(promptText, opts) {
  opts = opts || {};
  const label = opts.label || "";
  promptByLabel[label] = promptText;
  callsLog.push({ label: label, agentType: opts.agentType || null, hasSchema: !!opts.schema });

  if (Object.prototype.hasOwnProperty.call(OVERRIDES, label)) {
    return OVERRIDES[label];
  }

  if (label.indexOf("ledger:") === 0) {
    const parts = label.split(":");
    const kind = parts[1];
    const seg = parts[parts.length - 1];
    let status = "converged";
    if (kind === "in_progress") status = "in_progress";
    else if (kind === "blocked") status = "blocked";
    else if (kind === "cap") status = "non_converged";
    return { success: true, status: status, fragment_path: "/x/" + seg + ".json", fragment_sha1: "d" };
  }
  if (label === "merge-ledger") {
    return { success: true, ledger_path: "/x/l.json", n_segments: SEGS_ARGS.length, missing_segments: [], stale_segments: [] };
  }
  const seg = label.split(":")[1];
  if (label.indexOf("translate:") === 0) return DRIVE_RETURNS.translate !== null ? DRIVE_RETURNS.translate : ("DISPATCHED " + seg + " a1b2c3d4");
  if (label.indexOf("review-dispatch:") === 0) return DRIVE_RETURNS.review !== null ? DRIVE_RETURNS.review : ("DISPATCHED " + seg + " beef1234");
  // #348 -- both wait sites, chunk and re-check. Containment of "-recheck:"
  // classifies first, because "review-wait-recheck:<seg>:r<round>" would fall
  // to the chunk branch under any prefix test written for the translate site.
  if (label.indexOf("-recheck:") !== -1) return "PENDING " + seg;
  if (label.indexOf("wait:") === 0 || label.indexOf("review-wait:") === 0) return "READY " + seg;
  if (label.indexOf("review-read:") === 0) return { clean: true, coverage_ok: true, findings: [], draft_sha1: "a" };
  if (label.indexOf("artifact-check:") === 0) return { match: true };
  if (label.indexOf("fix:") === 0) return "FIXED " + seg;
  // #607 -- the fix-scope audit relay. This file's fixtures are about prompt
  // TEXT and branch reachability, never about the audit's own verdict, so a
  // clean pass is the right constant here; batch_size_estimator.test.py and
  // fix_scope_gate.test.py own the mismatch and relay-failure paths.
  if (label.indexOf("fix-scope:") === 0) return { ok: true, n_checked: 79, n_expected: 79 };
  if (label.indexOf("draft-probe:") === 0) return { present: true };
  throw new Error("mock agent(): unrecognized label " + label);
}

async function pipeline(items, stage1, stage2) {
  pipelineCalled = true;
  const out = [];
  for (const item of items) {
    const r1 = await stage1(item);
    out.push(await stage2(r1, item));
  }
  return out;
}
const logLines = [];
function log(line) { logLines.push(line); }

(async () => {
  try {
    const result = await __workflowMain__(agent, pipeline, log, SEGS_ARGS);
    process.stdout.write(JSON.stringify({
      result: result, calls: callsLog, promptByLabel: promptByLabel,
      pipelineCalled: pipelineCalled, logLines: logLines,
    }));
  } catch (err) {
    process.stderr.write("HARNESS_ERROR: " + (err && err.message || String(err)) + "\n");
    process.exit(1);
  }
})();
"""


def run(*, tmp_path: Path, segs: list, max_fix_rounds: int = 1, batch_agent_cap: int = 100000,
        drive_returns: dict | None = None, overrides: dict | None = None, timeout: int = 30,
        effort: str = FIXTURE_EFFORT, model: str = FIXTURE_MODEL,
        plugin_root: str = FIXTURE_PLUGIN_ROOT) -> dict:
    """Returns {ok, out, stderr}. ok=False (with stderr) when the template
    threw before producing stdout (the SEGS-guard throw path)."""
    drive_returns = drive_returns or {}
    dr = {"translate": drive_returns.get("translate"), "review": drive_returns.get("review")}
    src = instantiate(max_fix_rounds=max_fix_rounds, batch_agent_cap=batch_agent_cap,
                       effort=effort, model=model, plugin_root=plugin_root)
    harness = (
        HARNESS.replace("__WRAPPED_SOURCE__", _wrap(src))
        .replace("__SEGS_JSON__", json.dumps(segs))
        .replace("__DRIVE_RETURNS_JSON__", json.dumps(dr))
        .replace("__OVERRIDES_JSON__", json.dumps(overrides or {}))
    )
    p = tmp_path / "smoke_harness.js"
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
# Rendered-string helpers
# ---------------------------------------------------------------------------

def extract_codex_task(drive_prompt: str) -> str:
    """The codex TASK TEXT embedded in the drive prompt's quoted heredoc."""
    m = re.search(r"<<'LT_CODEX_TASK_EOF'\n(.*?)\nLT_CODEX_TASK_EOF", drive_prompt, re.DOTALL)
    assert m is not None, f"no LT_CODEX_TASK_EOF heredoc found in drive prompt:\n{drive_prompt[:400]}"
    return m.group(1)


def extract_poll(wait_prompt: str) -> str:
    """The single bash poll command line (starts with `end=$((SECONDS +`)."""
    hits = [ln for ln in wait_prompt.splitlines() if ln.startswith("end=$((SECONDS +")]
    assert len(hits) == 1, f"expected exactly one poll command line, got {len(hits)}"
    return hits[0]


# A convergent happy-path run (default valid DISPATCHED returns) whose
# rendered prompts the Contract-layer tests inspect.
def _happy_run(tmp_path) -> dict:
    res = run(tmp_path=tmp_path, segs=["seg01"])
    assert res["ok"], f"happy-path run unexpectedly threw: {res['stderr']}"
    return res["out"]


# ---------------------------------------------------------------------------
# Layer 1 -- Contract (rendered prompt shape).
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "drive_label,launch_needle,kind",
    [("translate:seg01", "codex_job.py --kind translate", "translate"),
     ("review-dispatch:seg01:r1", "codex_job.py --kind review", "review")],
)
def test_drive_prompt_launches_detached_codex_job(tmp_path, drive_label, launch_needle, kind):
    out = _happy_run(tmp_path)
    prompt = out["promptByLabel"][drive_label]

    # (a) DISP nonce + detached launch + DISPATCHED return; no sidecar.
    assert "DISP=$(uuidgen 2>/dev/null || echo $RANDOM$RANDOM$RANDOM)" in prompt
    assert 'echo "DISPATCHED seg01 $DISP"' in prompt
    assert ".codex_disp" not in prompt, "no .codex_disp sidecar is written"

    launch = [ln for ln in prompt.splitlines() if launch_needle in ln]
    assert len(launch) == 1, f"expected exactly one codex_job.py launch line for {kind}"
    launch = launch[0]
    assert "nohup " in launch
    assert "</dev/null >/dev/null 2>&1 &" in launch
    assert "setsid" not in launch, "no setsid"
    assert "timeout" not in launch and "gtimeout" not in launch, "no external timeout binary"

    # (b) exact frozen CLI surface, COMPANION single-quoted.
    assert f"--companion '{FIXTURE_COMPANION_PATH}'" in launch
    assert f"--cwd {FIXTURE_DURABLE_ROOT}" in launch
    assert "--seg seg01" in launch
    assert '--prompt-file "$TASKFILE"' in launch
    assert "--disp \"$DISP\"" in launch
    assert "--deadline-sec 2700" in launch
    if kind == "translate":
        assert f"--expect-token {FIXTURE_RUN_ID}:seg01 " in launch
    else:
        assert f"--expect-token {FIXTURE_RUN_ID}:seg01:r1 " in launch

    # #197 regression-catcher: the profile's engine.effort must reach the
    # dispatched codex_job.py launch as a real --effort flag (THE assertion
    # that would have caught "profile effort never reaches codex" -- a
    # template that silently dropped it would leave codex_job.py's own
    # argparse default of "high" in charge instead of FIXTURE_EFFORT).
    assert f"--effort {FIXTURE_EFFORT}" in launch
    # engine.model is unset by default (FIXTURE_MODEL == "") -- no --model
    # flag on the launch line at all.
    assert "--model" not in launch
    # #412/#607 -- FIXTURE_PLUGIN_ROOT is now a real path, because #607 makes
    # an empty plugin root refuse the batch outright. So the launch line
    # carries the flag; the "omits it when empty" shape it used to assert is
    # no longer reachable through this template at all (see
    # test_empty_plugin_root_refuses_the_batch below).
    assert "--plugin-root '" + FIXTURE_PLUGIN_ROOT + "'" in launch

    # the task-file path carries the runtime DISP.
    assert f'TASKFILE="{FIXTURE_DURABLE_ROOT}/segments/.codex_task.{kind}.seg01.$DISP"' in prompt


# Round-8 sweep finding: the DISPATCHING agent (this prompt, NOT codex) holds
# a Bash tool and is told it must do nothing but launch the detached job --
# unpinned. PRESENCE-ONLY, and a residual gap is named rather than implied
# away: this file's own `test_drive_dispatch_call_sites_have_no_codex_
# agenttype` below already confirms the dispatcher itself carries no
# agentType (it is a plain Claude call, not codex), which is exactly what
# makes this prompt's own restraint the only thing standing between it and
# writing the canonical draft/review file directly. Traced closely rather
# than assumed: if this agent fabricated a structurally-valid
# segments/{seg}.draft.json with the CORRECT dispatch_token (RUN_ID + ":" +
# seg -- a value visible to it in this SAME prompt's own embedded codex task
# text, not a secret), draft_ready.py's token check would pass it, and
# validate_draft.py's six checks (verified in
# assets/scripts/validate_draft.py -- placeholder/coverage/key-set/verse-
# line-count structure only, explicitly NOT semantic fidelity, which its own
# comments say is "deliberately left for the semantic codex review to
# catch") cannot distinguish a genuine codex translation from a plausible
# fabrication with the right shape. This is a real, currently unclosable gap
# given this system has no tool-restriction mechanism (round-8 sweep,
# confirmed across all 27 `await agent(` call sites in the three templates --
# an earlier draft said 30, counting `agent(` occurrences that were comment
# and label-string text rather than call sites) -- named
# here rather than left implicit behind a presence check that would read as
# coverage it does not have.
DISPATCH_NO_SELF_ACTION_CLAUSES = {
    "translate:seg01": (
        "you do NOT translate anything yourself, and you do NOT wait for "
        "the job to finish"
    ),
    "review-dispatch:seg01:r1": (
        "you do NOT review anything yourself, and you do NOT wait for the "
        "job to finish"
    ),
}
DISPATCH_NO_POLL_OR_READ_CLAUSE = (
    "Do not poll the job, do not read any file, and add no other text"
)


@pytest.mark.parametrize("drive_label", ["translate:seg01", "review-dispatch:seg01:r1"])
def test_drive_prompt_forbids_the_dispatcher_doing_the_work_itself(tmp_path, drive_label):
    """See the module-level comment just above for the full argument,
    including the unclosable residual gap this pin cannot cover. This is a
    PRESENCE check only: the mocked agent() here returns a canned DISPATCHED
    string for this label -- it never runs an LLM that could ignore these
    words -- so this proves the instructions are still WRITTEN, not that a
    dispatching agent is actually restrained by them."""
    out = _happy_run(tmp_path)
    prompt = out["promptByLabel"][drive_label]

    assert DISPATCH_NO_SELF_ACTION_CLAUSES[drive_label] in prompt, (
        f"the {drive_label} dispatch prompt must forbid the agent from "
        f"doing the translate/review work itself; prompt was:\n{prompt}"
    )
    assert DISPATCH_NO_POLL_OR_READ_CLAUSE in prompt, (
        f"the {drive_label} dispatch prompt must forbid polling the job or "
        f"reading any file; prompt was:\n{prompt}"
    )


@pytest.mark.parametrize(
    "drive_label,launch_needle",
    [("translate:seg01", "codex_job.py --kind translate"),
     ("review-dispatch:seg01:r1", "codex_job.py --kind review")],
)
def test_drive_prompt_launch_carries_model_when_pinned(tmp_path, drive_label, launch_needle):
    """#197 -- a pinned engine.model threads to both codex_job.py launches
    as a single-quoted --model flag, same convention as --companion."""
    res = run(tmp_path=tmp_path, segs=["seg01"], model="gpt-5.3-codex")
    assert res["ok"], res["stderr"]
    prompt = res["out"]["promptByLabel"][drive_label]
    launch = [ln for ln in prompt.splitlines() if launch_needle in ln][0]
    assert "--model 'gpt-5.3-codex'" in launch


@pytest.mark.parametrize(
    "drive_label,launch_needle",
    [("translate:seg01", "codex_job.py --kind translate"),
     ("review-dispatch:seg01:r1", "codex_job.py --kind review")],
)
def test_drive_prompt_launch_omits_model_when_unset(tmp_path, drive_label, launch_needle):
    """#197 -- positive control paired with the pinned-model case above: an
    empty engine.model (the common, unset case) produces NO --model flag at
    all on either codex_job.py launch line."""
    res = run(tmp_path=tmp_path, segs=["seg01"], model="")
    assert res["ok"], res["stderr"]
    prompt = res["out"]["promptByLabel"][drive_label]
    launch = [ln for ln in prompt.splitlines() if launch_needle in ln][0]
    assert "--model" not in launch


@pytest.mark.parametrize(
    "drive_label,launch_needle",
    [("translate:seg01", "codex_job.py --kind translate"),
     ("review-dispatch:seg01:r1", "codex_job.py --kind review")],
)
def test_drive_prompt_launch_carries_plugin_root_when_opted_in(tmp_path, drive_label, launch_needle):
    """#412 -- a resolved plugin_root threads to both codex_job.py launches
    as a single-quoted --plugin-root flag, same convention as --companion/
    --model. This is the ONLY test in the suite that exercises the template
    with a non-empty plugin_root and inspects the actual dispatch argv --
    #412's entire security claim (a tampered durable-root gate copy is
    caught because codex_job.py is told where the TRUSTED plugin copy
    lives) rests on this flag actually being emitted, which is exactly what
    this asserts."""
    res = run(tmp_path=tmp_path, segs=["seg01"], plugin_root="/opt/claude/plugins/literary-translator")
    assert res["ok"], res["stderr"]
    prompt = res["out"]["promptByLabel"][drive_label]
    launch = [ln for ln in prompt.splitlines() if launch_needle in ln][0]
    assert "--plugin-root '/opt/claude/plugins/literary-translator'" in launch


def test_empty_plugin_root_refuses_the_batch(tmp_path):
    """#607 -- replaces test_drive_prompt_launch_omits_plugin_root_when_not_
    opted_in, whose premise this release removed.

    That test asserted the #412 positive control: an empty plugin_root ("not
    opted into the redirect") produced NO --plugin-root flag on either
    codex_job.py launch. The assertion was true and is now UNREACHABLE
    through this template -- with no plugin root there is no trusted copy of
    fix_scope_audit.py to run, so the batch refuses before any dispatch
    happens and no launch line is ever built. Asserting the old shape would
    mean asserting over a prompt the run never produces.

    The template's PLUGIN_ROOT_ARG still has its empty branch, which is now
    dead in THIS template; it is left in place because the same constant
    shape is shared with the sibling workflow templates, which are unchanged.
    """
    res = run(tmp_path=tmp_path, segs=["seg01"], plugin_root="")
    assert res["ok"], res["stderr"]
    result = res["out"]["result"]
    assert result["reason"] == "fix-scope-plugin-root-missing"
    assert result["converged"] == []
    assert result["failed"] == []
    assert res["out"]["promptByLabel"] == {}, (
        "the batch must refuse BEFORE any agent call -- an unaudited fix turn "
        "is exactly what this refusal exists to prevent"
    )


@pytest.mark.parametrize(
    "drive_label,canonical_suffix",
    [("translate:seg01", "segments/seg01.draft.json"),
     ("review-dispatch:seg01:r1", "segments/seg01.review.json")],
)
def test_codex_task_has_exactly_one_job_out_and_supersede_clause(tmp_path, drive_label, canonical_suffix):
    out = _happy_run(tmp_path)
    task = extract_codex_task(out["promptByLabel"][drive_label])

    # (c) EXACTLY ONE ⟦JOB_OUT⟧ placeholder (the driver rejects 0 or 2).
    assert task.count("⟦JOB_OUT⟧") == 1, "codex task text must carry exactly one JOB_OUT placeholder"

    # SUPERSEDE clause: forbids the canonical path + names JOB_OUT the sole write.
    canonical = f"{FIXTURE_DURABLE_ROOT}/{canonical_suffix}"
    assert canonical in task, "the codex task must name the forbidden canonical path"
    assert "SUPERSEDES" in task
    assert "the only segments-area file you may write" in task

    # DISP must NOT leak into the codex task text (only the driver knows it).
    assert "$DISP" not in task and "DISP" not in task, "DISP must not appear in the codex task text"


def test_drive_dispatch_call_sites_have_no_codex_agenttype(tmp_path):
    # (d) both dispatcher call sites are plain-Claude (no agentType), effort low.
    out = _happy_run(tmp_path)
    by = {c["label"]: c for c in out["calls"]}
    for lbl in ("translate:seg01", "review-dispatch:seg01:r1"):
        assert by[lbl]["agentType"] is None, f"{lbl} must have no agentType (plain-Claude drive)"
        assert by[lbl]["hasSchema"] is False, f"{lbl} must be schema-less"


@pytest.mark.parametrize(
    "wait_label,accept_scripts,disp",
    [("wait:seg01", ["draft_ready.py", "validate_draft.py"], "a1b2c3d4"),
     ("review-wait:seg01:r1", ["review_ready.py"], "beef1234")],
)
def test_wait_poll_shape_accept_failfast_deadline(tmp_path, wait_label, accept_scripts, disp):
    out = _happy_run(tmp_path)
    # The happy path answers READY on the first chunk, so exactly one call was
    # made at this label and promptByLabel's single slot holds CHUNK 1. Asserted,
    # not assumed: chunk calls reuse the label (#348), so a change that made the
    # happy path poll twice would silently move these assertions onto chunk 2.
    n_wait_calls = sum(1 for c in out["calls"] if c["label"] == wait_label)
    assert n_wait_calls == 1, (
        f"expected the happy path to answer READY on chunk 1, got {n_wait_calls} calls "
        f"at {wait_label} -- the prompt asserted below is no longer chunk 1's"
    )
    prompt = out["promptByLabel"][wait_label]
    poll = extract_poll(prompt)

    # (e) ACCEPT runs the full canonical gate directly; no external timeout.
    for s in accept_scripts:
        assert s in poll, f"wait ACCEPT must invoke {s}"
    assert "--expect-token" in poll
    assert "timeout" not in poll and "gtimeout" not in poll, "no external timeout binary"

    # (e) FAIL-FAST: DISP-named sentinel presence check, keyed on the captured
    # DISP, evaluated ONLY AFTER the ACCEPT `exit 0`. Since #348 the sentinel
    # branch also PRINTS its marker before exiting (the reply grammar's FAILED
    # verdict is derived from that marker, not from the exit status alone) --
    # the presence check itself, and its position after ACCEPT, are unchanged.
    sentinel = (
        f'[ -f "{FIXTURE_DURABLE_ROOT}/segments/.codex_failed.seg01.{disp}" ]'
        " && { echo LT_FAIL_SENTINEL; exit 1; }"
    )
    assert sentinel in poll, "fail-fast must be a DISP-named sentinel presence check"
    assert poll.index("exit 0") < poll.index(".codex_failed."), (
        "fail-fast must be evaluated AFTER the ACCEPT gate (a valid canonical wins)"
    )

    # (f) #348 -- the elapsed bound is now this CHUNK's slice of the total, not
    # the whole 3450 s wait: the Bash tool clamps any single call at 600 s, so a
    # one-call poll of 3450 s was killed every time. Both halves are asserted --
    # the chunk's own bound stays clear of the clamp, AND the prompt still
    # declares the unchanged total bound of 3450 s (DEADLINE 2700 + FINALIZE 150
    # + GRACE 600), which is the contract every downstream doc quotes.
    # tests/wait_chunking.test.py owns the sum-of-chunks property.
    chunk_sec = int(re.match(r"^end=\$\(\(SECONDS \+ (\d+)\)\);", poll).group(1))
    assert 0 < chunk_sec < 600, (
        f"chunk 1 declares {chunk_sec}s; the Bash tool clamps a single call at 600s (#348)"
    )
    assert "3450" in prompt, (
        "the chunk prompt must still declare the total wait bound "
        "DEADLINE(2700)+FINALIZE(150)+GRACE(600)=3450"
    )
    assert "[ $SECONDS -ge $end ] && break" in poll
    tail = poll.rsplit("done;", 1)[1]
    # The tail gained the chunk's own budget-exhausted MARKER; what it must
    # still not gain is a second gate. `echo`-plus-`exit 1` keeps a TOOL-KILLED
    # chunk (exit 143, no marker) indistinguishable from a merely-exhausted one,
    # which is the safe reading: not ready yet, keep polling.
    assert tail.strip() == "echo LT_CHUNK_BOUND; exit 1", (
        f"no separate post-loop gate -- tail after done must be the chunk marker "
        f"plus `exit 1`, got: {tail!r}"
    )
    for s in accept_scripts:
        assert s not in tail, f"a gate ({s}) must NOT run after the loop (no post-loop gate)"


# ---------------------------------------------------------------------------
# Layer 2 -- Execution wiring (SEGS guard + DISP threading + safe degradation).
# ---------------------------------------------------------------------------

def test_segs_uniqueness_guard_throws_on_duplicate(tmp_path):
    res = run(tmp_path=tmp_path, segs=["seg01", "seg01"])
    assert res["ok"] is False, "a duplicate seg id must THROW before pipeline()"
    assert "duplicate segment id" in res["stderr"], res["stderr"]
    assert '"seg01"' in res["stderr"], "the throw must name the offending id"


def test_segs_uniqueness_guard_throws_on_duplicate_deeper_in_list(tmp_path):
    # SEGS == args, so a "manifest-derived" duplicate is just a duplicate in
    # the dispatch array -- exercise a dup that is not the adjacent pair.
    res = run(tmp_path=tmp_path, segs=["seg01", "seg02", "seg03", "seg02"])
    assert res["ok"] is False
    assert "duplicate segment id" in res["stderr"] and '"seg02"' in res["stderr"]


def test_unique_segs_dispatch_normally_to_convergence(tmp_path):
    res = run(tmp_path=tmp_path, segs=["seg01", "seg02"])
    assert res["ok"] is True, res["stderr"]
    out = res["out"]
    assert out["pipelineCalled"] is True
    assert sorted(r["seg"] for r in out["result"]["converged"]) == ["seg01", "seg02"]
    assert out["result"]["failed"] == []
    assert out["result"]["batchComplete"] is True


def test_valid_disp_threads_into_both_wait_polls(tmp_path):
    """A valid DISPATCHED <seg> <DISP> return from each dispatcher must place
    that exact DISP into its wait poll's fail-fast sentinel path."""
    res = run(
        tmp_path=tmp_path, segs=["seg01"],
        drive_returns={"translate": "DISPATCHED seg01 CAFE-01", "review": "DISPATCHED seg01 F00D02"},
    )
    assert res["ok"], res["stderr"]
    by = res["out"]["promptByLabel"]
    assert ".codex_failed.seg01.CAFE-01" in by["wait:seg01"]
    assert ".codex_failed.seg01.F00D02" in by["review-wait:seg01:r1"]


@pytest.mark.parametrize(
    "bad_return",
    [
        "DISPATCHED seg01 ;rm -rf /tmp/x",     # shell injection attempt
        "DISPATCHED seg01 a1b2 extra tokens",  # trailing text past the DISP
        "DISPATCHED wrongseg a1b2c3",          # wrong seg
        "DISPATCHED seg01 g1h2i3",             # chars outside [0-9A-Fa-f-]
        "DISPATCHED seg01\nDISPATCHED seg01 dead",  # multi-line
    ],
)
def test_unparseable_drive_return_disables_failfast_safely(tmp_path, bad_return):
    """PLAN §4 (g): any mismatch -> disp="" (HIGH-3 anchored grammar), so the
    wait command carries NO injected token and NO fail-fast clause (safe
    degradation: it simply polls to the bound)."""
    res = run(tmp_path=tmp_path, segs=["seg01"], drive_returns={"translate": bad_return})
    assert res["ok"], res["stderr"]
    poll = extract_poll(res["out"]["promptByLabel"]["wait:seg01"])
    assert ".codex_failed." not in poll, "an empty DISP must DISABLE fail-fast (no sentinel clause)"
    assert "rm -rf" not in poll, "no unsafe token from the drive return may reach the wait bash"
    # the poll still runs to the bound (the ACCEPT gate + deadline break remain)
    assert "draft_ready.py" in poll and "[ $SECONDS -ge $end ] && break" in poll


def test_valid_disp_still_produces_failfast_control(tmp_path):
    """Positive control paired with the safe-degradation cases above: a WELL-
    FORMED DISP DOES produce the fail-fast clause (so the assertions above are
    catching real disabling, not a perpetually-absent clause)."""
    res = run(tmp_path=tmp_path, segs=["seg01"], drive_returns={"translate": "DISPATCHED seg01 abcDEF01"})
    assert res["ok"], res["stderr"]
    poll = extract_poll(res["out"]["promptByLabel"]["wait:seg01"])
    assert '.codex_failed.seg01.abcDEF01" ] && { echo LT_FAIL_SENTINEL; exit 1; }' in poll


# ---------------------------------------------------------------------------
# #228 exact-match sentinels (content-matching-sentinel-fragility class) at
# this template's three remaining sentinel sites -- C (getVerifiedReview's
# "review-wait:"), D (runRound's "fix:"), E (reviewFixLoop's "wait:").
# Mirrors skeptic_pipeline_e2e.test.py's own precheck/wait substring-
# collision tests for skeptic-pass-wf.template.js. OVERRIDES (see HARNESS
# above) injects the colliding/falsy reply at the exact label under test;
# every other call in the sequence keeps its ordinary happy-path default.
# ---------------------------------------------------------------------------

def _non_clean_review():
    return {
        "clean": False, "coverage_ok": True,
        "findings": [{"loc": "VERSE:1", "severity": "minor", "issue": "i", "suggest": "s"}],
        "draft_sha1": "a" * 40,
    }


# Both rows below are run against TWO replies. "TIMEOUT ..." is the #228
# evidence verbatim -- kept because it is the reply that actually broke, and
# because under #348's grammar it is ALSO the unrecognized-reply shape, whose
# fail-safe fallthrough to PENDING is worth pinning. "PENDING ..." is the same
# collision written in the grammar the chunk prompts now ask for. A reply
# carrying the literal substring "READY" inside its own prose must be rejected
# in both.
COLLIDING_NOT_READY_REPLIES = [
    "TIMEOUT seg01 (not READY)",
    "PENDING seg01 (not READY)",
]


@pytest.mark.parametrize("reply", COLLIDING_NOT_READY_REPLIES)
def test_translate_wait_substring_collision_reports_timeout(tmp_path, reply):
    """RED before the #228 exact-match fix at site E (reviewFixLoop's
    "wait:" + seg): the OLD `ready.indexOf("READY") === -1` check falsely
    treated a not-ready reply that merely contains the literal substring
    "READY" inside its own explanatory prose (e.g. "PENDING seg01 (not
    READY)") as ready -- `indexOf` finds "READY" so the negated `=== -1`
    check was false. This is the worst of the five #228 sites: a false pass
    here sends the entire review/fix cycle over a draft that never actually
    finished translating, and no recoverable signal is ever recorded to
    pick it back up."""
    res = run(tmp_path=tmp_path, segs=["seg01"], overrides={"wait:seg01": reply})
    assert res["ok"], res["stderr"]
    out = res["out"]
    # #400 -- the colliding reply never reaches the recorded detail: it loses
    # every chunk (never READY), so the chunk loop exhausts and the ONE
    # non-polling re-check runs last, unoverridden, answering this harness's
    # own default "PENDING seg01" -- that becomes lastWaitReply, not the
    # collision text itself.
    assert out["result"]["failed"] == [
        {"seg": "seg01", "converged": False, "reason": "translate-timeout", "detail": "PENDING seg01"}
    ]
    assert out["result"]["converged"] == []
    labels = [c["label"] for c in out["calls"]]
    # A substring-collision bug proceeds straight into the review/fix cycle
    # on an unfinished draft instead of stopping at the wait.
    assert "review-dispatch:seg01:r1" not in labels
    assert "review-wait:seg01:r1" not in labels


@pytest.mark.parametrize("reply", COLLIDING_NOT_READY_REPLIES)
def test_review_wait_substring_collision_reports_review_timeout(tmp_path, reply):
    """RED before the #228 exact-match fix at site C (getVerifiedReview's
    "review-wait:" + seg + ":r" + roundLabel): the OLD
    `ready.indexOf("READY") === -1` check falsely treated a not-ready reply
    containing the literal substring "READY" as ready, letting the code go
    on to read a review artifact that review_ready.py never actually
    confirmed."""
    res = run(tmp_path=tmp_path, segs=["seg01"], overrides={"review-wait:seg01:r1": reply})
    assert res["ok"], res["stderr"]
    out = res["out"]
    # #400 -- same reasoning as the translate-site twin above: the collision
    # text never survives to the recorded detail, since it loses every chunk
    # and the unoverridden re-check's own default "PENDING seg01" is the
    # last reply seen.
    assert out["result"]["failed"] == [
        {"seg": "seg01", "converged": False, "reason": "review-timeout", "rounds": 1, "detail": "PENDING seg01"}
    ]
    assert out["result"]["converged"] == []
    labels = [c["label"] for c in out["calls"]]
    # A substring-collision bug proceeds to read the (never-ready) review
    # artifact instead of stopping at the wait.
    assert "review-read:seg01:r1" not in labels
    assert "artifact-check:seg01:r1" not in labels


def test_fix_substring_collision_does_not_falsely_trigger_probe(tmp_path):
    """RED before the #228 exact-match fix at site D (runRound's "fix:" + seg
    + ":r" + round): the OLD `fx.indexOf("DRAFT_MISSING") !== -1` check
    falsely matched a genuine, successful fix reply that merely mentions the
    literal substring "DRAFT_MISSING" in its own prose (e.g. explaining what
    it fixed) -- wrongly routing a perfectly healthy segment through the
    #131 draft-probe and, on this harness's default present:true probe
    result, into a needless fix-call-failed non-convergence instead of
    accepting the fix and moving on."""
    res = run(
        tmp_path=tmp_path, segs=["seg01"],
        overrides={
            "review-read:seg01:r1": _non_clean_review(),
            "artifact-check:seg01:r1": {"match": True},
            "fix:seg01:r1": "FIXED seg01 (previously printed DRAFT_MISSING due to a timing race; now translated cleanly)",
        },
    )
    assert res["ok"], res["stderr"]
    out = res["out"]
    assert out["result"]["converged"] == [{"seg": "seg01", "converged": True, "rounds": 2}]
    assert out["result"]["failed"] == []
    labels = [c["label"] for c in out["calls"]]
    assert "draft-probe:seg01" not in labels, "a substring collision must NOT trigger the #131 draft probe"


def test_fix_null_return_still_triggers_probe(tmp_path):
    """Mandatory regression guard for site D's permissive-falsy branch
    (`!fx || ...`): a literal falsy `fx` (agent death / output-token ceiling
    / classifier block on the fix call itself -- #131 facet A) MUST still
    route through the draftPresentAndValid probe, exactly like an exact
    DRAFT_MISSING reply does. This is deliberately NOT redundant with the
    exact-match check above it: `null` is not the string "DRAFT_MISSING
    seg01", so a version of the fix that dropped the `!fx ||` disjunct (kept
    only the bare `String(fx).trim() === ...` exact match) would let a dead
    fix call fall through as an ordinary review round -- silently skipping
    the probe that exists precisely to disambiguate that case."""
    res = run(
        tmp_path=tmp_path, segs=["seg01"],
        overrides={
            "review-read:seg01:r1": _non_clean_review(),
            "artifact-check:seg01:r1": {"match": True},
            "fix:seg01:r1": None,
        },
    )
    assert res["ok"], res["stderr"]
    out = res["out"]
    labels = [c["label"] for c in out["calls"]]
    assert "draft-probe:seg01" in labels, "a falsy fix-call return must still trigger the #131 draft probe"
    # #400 -- the probe answered present:true (this harness's default), so the
    # detail names the FIX call that actually died, not the probe.
    assert out["result"]["failed"] == [
        {
            "seg": "seg01", "converged": False, "reason": "fix-call-failed", "rounds": 1,
            "detail": "fix call: agent call returned null",
        }
    ]
    assert out["result"]["converged"] == []


# ---------------------------------------------------------------------------
# #308 -- sentinelVerdict() line-oriented match at the same three sites (C,
# D, E). #228 (above) converted these sites from indexOf substring checks to
# whole-string exact match to kill substring false-POSITIVES; #308 is the
# false-NEGATIVE dual that whole-string cure introduced: a low-effort wait
# agent's benign prose preamble (real evidence: 2-in-6 review-waits in the
# 1.15.0 W5 smoke) made the exact match fail and mislabeled a **completed**
# review/translate as a timeout. sentinelVerdict tolerates the preamble while
# keeping BOTH #228's and #308's directions closed. The two decorated-READY
# replies below are the REAL production replies from the #308 evidence
# (journal-verbatim), not invented paraphrases.
# ---------------------------------------------------------------------------

DECORATED_READY_A = "The poll confirmed the review artifact is ready (exit 0).\n\nREADY "
DECORATED_READY_B = 'The command exited 0 (final line shows `{"ready": true}`).\n\nREADY '

QUOTED_SUCCESS_DISAVOWED = (
    "The command failed; quoting the requested success form:\n"
    "READY seg01\n"
    "That is not my verdict."
)


def test_review_wait_decorated_ready_still_converges(tmp_path):
    """Site C accept (#308): a prose-decorated READY reply (the real #308
    evidence shape) at review-wait:seg01:r1 must NOT be misread as a
    review-timeout -- the segment converges and the read/check calls run."""
    res = run(
        tmp_path=tmp_path, segs=["seg01"],
        overrides={"review-wait:seg01:r1": DECORATED_READY_A + "seg01"},
    )
    assert res["ok"], res["stderr"]
    out = res["out"]
    assert out["result"]["converged"] == [{"seg": "seg01", "converged": True, "rounds": 1}]
    assert out["result"]["failed"] == []
    labels = [c["label"] for c in out["calls"]]
    assert "review-read:seg01:r1" in labels, "a decorated READY must still let the read/check pair run"
    assert "artifact-check:seg01:r1" in labels


def test_translate_wait_decorated_ready_still_converges(tmp_path):
    """Site E accept (#308): a prose-decorated READY reply at wait:seg01 must
    NOT be misread as a translate-timeout -- the run proceeds into the
    review/fix cycle and converges."""
    res = run(
        tmp_path=tmp_path, segs=["seg01"],
        overrides={"wait:seg01": DECORATED_READY_B + "seg01"},
    )
    assert res["ok"], res["stderr"]
    out = res["out"]
    assert out["result"]["converged"] == [{"seg": "seg01", "converged": True, "rounds": 1}]
    assert out["result"]["failed"] == []
    labels = [c["label"] for c in out["calls"]]
    assert "review-dispatch:seg01:r1" in labels, "a decorated READY must still enter the review cycle"


# #348 re-pointed these two rows. The sentinel whose priority they assert used
# to be the single "TIMEOUT <seg>"; the chunked wait replaced it with TWO
# non-success sentinels -- "FAILED <seg>" (the driver's fail sentinel appeared)
# and "PENDING <seg>" (this chunk spent its budget, or was cut short) -- and
# waitChunkVerdict tests both by containment BEFORE the whole-line READY test.
# The INVARIANT is unchanged and is what is parametrized here: a non-success
# sentinel anywhere in the reply outranks a READY on the reply's own final
# line. Dropping the rows because the literal "TIMEOUT" is gone would delete
# the property; re-pointing them at the sentinels that replaced it keeps it,
# and now covers two sentinels where there was one.
NON_SUCCESS_SENTINELS = ["FAILED", "PENDING"]


@pytest.mark.parametrize("fail_sentinel", NON_SUCCESS_SENTINELS)
def test_review_wait_fail_sentinel_wins_when_not_last_line(tmp_path, fail_sentinel):
    """Fail-priority, discriminating order (round-3 codex finding): the
    non-success sentinel appearing BEFORE the success sentinel -- "FAILED
    seg01\\nREADY seg01" -- must still block. READY is the reply's own FINAL
    line, so a last-line-only reader would wrongly ACCEPT it; the correct
    full-scan-for-failSentinel-then-check-last-line algorithm still rejects
    it because the failure-sentinel scan runs over every line, not just the
    last. The non-discriminating order ("READY seg01\\nFAILED seg01",
    covered by the #228 substring-collision tests' sibling cases above)
    would not tell a correct implementation apart from a broken
    last-line-only one.

    The observable stays `review-timeout`: a FAILED chunk stops the chunk
    loop early but NOT the segment (#348's re-check runs on that path too,
    since a valid canonical outranks any sentinel), and this harness answers
    that re-check PENDING -- so the artifact genuinely never landed."""
    res = run(
        tmp_path=tmp_path, segs=["seg01"],
        overrides={"review-wait:seg01:r1": f"{fail_sentinel} seg01\nREADY seg01"},
    )
    assert res["ok"], res["stderr"]
    out = res["out"]
    # #400 -- every chunk repeats this same non-ready reply, so the loop
    # exhausts and the unoverridden re-check's own default "PENDING seg01"
    # is the last reply seen -- same reasoning as the #228 collision tests.
    assert out["result"]["failed"] == [
        {"seg": "seg01", "converged": False, "reason": "review-timeout", "rounds": 1, "detail": "PENDING seg01"}
    ]
    assert out["result"]["converged"] == []
    labels = [c["label"] for c in out["calls"]]
    assert "review-read:seg01:r1" not in labels


@pytest.mark.parametrize("fail_sentinel", NON_SUCCESS_SENTINELS)
def test_translate_wait_fail_sentinel_wins_when_not_last_line(tmp_path, fail_sentinel):
    """Same discriminating-order fail-priority case as above, at site E."""
    res = run(
        tmp_path=tmp_path, segs=["seg01"],
        overrides={"wait:seg01": f"{fail_sentinel} seg01\nREADY seg01"},
    )
    assert res["ok"], res["stderr"]
    out = res["out"]
    # #400 -- same reasoning as the review-site twin above.
    assert out["result"]["failed"] == [
        {"seg": "seg01", "converged": False, "reason": "translate-timeout", "detail": "PENDING seg01"}
    ]
    assert out["result"]["converged"] == []
    labels = [c["label"] for c in out["calls"]]
    assert "review-dispatch:seg01:r1" not in labels


def test_fix_decorated_draft_missing_still_triggers_probe(tmp_path):
    """Site D probe-routing (#308): a decorated DRAFT_MISSING reply (prose
    preamble, sentinel as the final line) at fix:seg01:r1 -- the label
    callFix() actually emits is "fix:" + seg + ":r" + round, i.e.
    "fix:seg01:r1" for round 1, NOT the bare "fix:seg01" -- must still route
    through the #131 draftPresentAndValid probe instead of silently
    continuing as an ordinary review round. With the probe's default
    {present: true}, the segment ends transient fix-call-failed."""
    res = run(
        tmp_path=tmp_path, segs=["seg01"],
        overrides={
            "review-read:seg01:r1": _non_clean_review(),
            "artifact-check:seg01:r1": {"match": True},
            "fix:seg01:r1": "I attempted the fix but could not locate the draft.\nDRAFT_MISSING seg01",
        },
    )
    assert res["ok"], res["stderr"]
    out = res["out"]
    labels = [c["label"] for c in out["calls"]]
    assert "draft-probe:seg01" in labels, "a decorated DRAFT_MISSING must still trigger the #131 draft probe"
    # #400 -- the flattened (LF -> space) fix reply itself is the detail: the
    # probe answered present:true, so it never displaces the fix call's own.
    assert out["result"]["failed"] == [
        {
            "seg": "seg01", "converged": False, "reason": "fix-call-failed", "rounds": 1,
            "detail": "fix call: I attempted the fix but could not locate the draft. DRAFT_MISSING seg01",
        }
    ]
    assert out["result"]["converged"] == []


# ---------------------------------------------------------------------------
# 1.16.0 -- site D (runRound's "fix:" + seg + ":r" + round) in its GLUED
# shapes: the BEHAVIOURAL half of the containment reversal.
#
# The five READY/TIMEOUT sites take a FAIL sentinel, so gluing one fakes a
# PASS. Site D runs the other way round: "DRAFT_MISSING <seg>" is its OK
# sentinel, so glue cannot fake a pass -- it makes a GENUINE report go
# UNRECOGNIZED. Under whole-trimmed-line equality the branch is skipped,
# runRound returns terminal:false, the loop carries on to the mandatory final
# review, and this harness answers that review CLEAN -- so the segment is
# reported CONVERGED over a draft the fix agent just said was missing. That
# false GREEN is the observable this file asserts against.
#
# WHY THESE CASES EXIST. Site D's other locks are TEXTUAL, and a rewrite can
# satisfy every one of them while reopening the gap:
# tests/bounded_poll_present.test.py pins that the branch is keyed on
# mentionedAnywhere() and that no sentinelVerdict call survives in runRound;
# tests/rejected_anywhere_parity.test.py pins that mentionedAnywhere() is a
# delegation rather than a second containment implementation;
# tests/transient_failure_recoverable.test.py pins what the branch DOES once
# entered. All of them hold for a version that keeps a mentionedAnywhere() call
# in the source and then decides the branch with a hand-rolled whole-line
# comparison. Until these cases, nothing EXECUTED site D's decision on a glued
# reply -- the other five sites each had such a case
# (tests/mass_translate_sentinel_containment.test.py), this one did not.
#
# NO COUNT IS PUBLISHED HERE, deliberately, and none should be added: the
# exhaustive per-character sweeps and their numbers belong to their own named
# populations -- ALL_GLUES (15 items,
# tests/mass_translate_sentinel_containment.test.py) and GLUE_CHARS (16 items,
# tests/glossary_citation_review.test.py). The six characters below are a
# deliberate SUBSET, three from each side of the trim() partition, chosen so
# both shapes' opposite mechanisms are exercised; they are not a third
# population for anyone to quote a ratio over.
# ---------------------------------------------------------------------------

LF = chr(0x0A)

FIX_PROSE = "I ran draft_ready.py and it reports the canonical is absent."
DRAFT_MISSING_SEG01 = "DRAFT_MISSING seg01"

# Built with chr() -- never typed as the character itself and never as a
# backslash-u escape, which a careless paste silently replaces with the
# character, invisible in every later diff of this file.
#
# Partitioned by whether JS trim() strips the character, because that split is
# exactly what decides the sentinel-alone shape below. MEASURED, not eyeballed:
# U+2028 IS stripped, while U+0085 NEL -- the character one would most
# naturally reach for as a line boundary -- is NOT in the JS WhiteSpace set.
FIX_GLUE_TRIM_STRIPPED = [
    ("space", chr(0x20)),
    ("nbsp_u00a0", chr(0xA0)),
    ("lsep_u2028", chr(0x2028)),
]
FIX_GLUE_TRIM_PRESERVED = [
    ("nel_u0085", chr(0x85)),
    ("zwsp_u200b", chr(0x200B)),
    ("letter_x", "x"),
]
FIX_GLUE_BOTH = FIX_GLUE_TRIM_STRIPPED + FIX_GLUE_TRIM_PRESERVED

# #400 -- the exact `detail` each glued reply folds down to through
# replyDetail()'s DETAIL_BREAKS regex, keyed by glue_name and confirmed
# against the REAL template's output (this file's own run() harness), not
# hand-simulated. DETAIL_BREAKS collapses a CONTIGUOUS run of break
# characters (LF, U+2028, U+0085 among them) into exactly one ascii space; a
# glue that is itself a break character folds together with the LF in front
# of it in the "alone on its line" shape, while every other glue in this
# file's set is not a break character and rides through verbatim. "fix
# call: " is sourcedDetail()'s own label prefix.
GLUED_TO_PROSE_DETAIL = {
    "space": "fix call: " + FIX_PROSE + " " + DRAFT_MISSING_SEG01,
    "nbsp_u00a0": "fix call: " + FIX_PROSE + chr(0xA0) + DRAFT_MISSING_SEG01,
    "lsep_u2028": "fix call: " + FIX_PROSE + " " + DRAFT_MISSING_SEG01,
    "nel_u0085": "fix call: " + FIX_PROSE + " " + DRAFT_MISSING_SEG01,
    "zwsp_u200b": "fix call: " + FIX_PROSE + chr(0x200B) + DRAFT_MISSING_SEG01,
    "letter_x": "fix call: " + FIX_PROSE + "x" + DRAFT_MISSING_SEG01,
}
ALONE_ON_LINE_DETAIL = {
    "space": "fix call: " + FIX_PROSE + "  " + DRAFT_MISSING_SEG01,
    "nbsp_u00a0": "fix call: " + FIX_PROSE + " " + chr(0xA0) + DRAFT_MISSING_SEG01,
    "lsep_u2028": "fix call: " + FIX_PROSE + " " + DRAFT_MISSING_SEG01,
    "nel_u0085": "fix call: " + FIX_PROSE + " " + DRAFT_MISSING_SEG01,
    "zwsp_u200b": "fix call: " + FIX_PROSE + " " + chr(0x200B) + DRAFT_MISSING_SEG01,
    "letter_x": "fix call: " + FIX_PROSE + " x" + DRAFT_MISSING_SEG01,
}


def _prose_shares_the_sentinels_line(glue: str) -> str:
    """prose + GLUE + sentinel -- the everyday shape. trim() only reaches a
    line's two ends, so it never gets at glue sitting BETWEEN the prose and the
    sentinel: the line equals nothing and only containment sees the report."""
    return FIX_PROSE + glue + DRAFT_MISSING_SEG01


def _sentinel_alone_on_its_line(glue: str) -> str:
    """prose + LF + GLUE + sentinel -- the sentinel is alone on its own line, so
    the outcome turns entirely on whether trim() can strip GLUE."""
    return FIX_PROSE + LF + glue + DRAFT_MISSING_SEG01


def _run_with_fix_reply(tmp_path, reply) -> dict:
    """Round 1's fix call answered with `reply`; every other call keeps its
    happy-path default -- including the mandatory final review, which answers
    CLEAN. That default is load-bearing: it is what turns an unrecognised
    report into a visible false CONVERGENCE rather than a silent no-op."""
    res = run(
        tmp_path=tmp_path, segs=["seg01"],
        overrides={
            "review-read:seg01:r1": _non_clean_review(),
            "artifact-check:seg01:r1": {"match": True},
            "fix:seg01:r1": reply,
        },
    )
    assert res["ok"], res["stderr"]
    return res["out"]


def _assert_report_reached_the_draft_probe(out: dict, shape_desc: str, expected_detail: str) -> None:
    labels = [c["label"] for c in out["calls"]]
    assert "fix:seg01:r1" in labels, (
        f"the run never reached round 1's fix call, so this case says nothing "
        f"about site D -- the overrides or the label shape moved. Calls: {labels}"
    )
    assert "draft-probe:seg01" in labels, (
        f"a genuine DRAFT_MISSING report with {shape_desc} went UNRECOGNIZED: the "
        f"branch never probed draftPresentAndValid, so runRound returned "
        f"terminal:false and the loop carried on as an ordinary review round over "
        f"a draft the fix agent had just said was missing. Calls: {labels}"
    )
    assert out["result"]["converged"] == [], (
        f"the segment must NOT be reported converged. With the report "
        f"unrecognised the round falls through, the mandatory final review "
        f"answers clean, and the batch banks a draft that was never there. "
        f"Result: {out['result']}"
    )
    # #400 -- the probe answers present:true (this harness's default), so the
    # detail must name the FIX reply itself, flattened by replyDetail().
    assert out["result"]["failed"] == [
        {
            "seg": "seg01", "converged": False, "reason": "fix-call-failed", "rounds": 1,
            "detail": expected_detail,
        }
    ], (
        f"expected the transient fix-call-failed end for round 1 (this harness's "
        f"probe answers present:true, so a recognised report routes there and "
        f"auto-redispatches next run); got {out['result']}"
    )


@pytest.mark.parametrize("glue_name,glue", FIX_GLUE_BOTH, ids=[n for n, _ in FIX_GLUE_BOTH])
def test_fix_draft_missing_glued_to_prose_still_reaches_the_probe(tmp_path, glue_name, glue):
    """Site D, prose sharing the sentinel's line -- CONTAINMENT-ONLY.

    A plain SPACE is enough. split("\\n") breaks on LF and nothing else, so any
    character between the prose and the sentinel keeps them on one line, and
    trim() cannot reach it there: whole-line equality misses the report at every
    one of these characters, on both sides of the trim() partition. Only
    mentionedAnywhere()'s containment test sees it.

    This is the case codex's mutant reopens -- a mentionedAnywhere() call left
    in the source to satisfy the structural locks, with the branch actually
    decided by a hand-rolled whole-line comparison. Every other test in the
    plugin stays green under it."""
    out = _run_with_fix_reply(tmp_path, _prose_shares_the_sentinels_line(glue))
    _assert_report_reached_the_draft_probe(
        out, f"prose on the sentinel's own line, glued by {glue_name}",
        GLUED_TO_PROSE_DETAIL[glue_name],
    )


@pytest.mark.parametrize(
    "glue_name,glue", FIX_GLUE_TRIM_PRESERVED, ids=[n for n, _ in FIX_GLUE_TRIM_PRESERVED]
)
def test_fix_draft_missing_alone_behind_unstrippable_glue_still_reaches_the_probe(
    tmp_path, glue_name, glue
):
    """Site D, sentinel alone on its line -- the half that STILL needs
    containment. The sentinel has its own line, but one character trim() does
    not strip sits in front of it, so the trimmed line still equals nothing."""
    out = _run_with_fix_reply(tmp_path, _sentinel_alone_on_its_line(glue))
    _assert_report_reached_the_draft_probe(
        out, f"the sentinel alone on its line behind {glue_name}, which trim() does not strip",
        ALONE_ON_LINE_DETAIL[glue_name],
    )


@pytest.mark.parametrize(
    "glue_name,glue", FIX_GLUE_TRIM_STRIPPED, ids=[n for n, _ in FIX_GLUE_TRIM_STRIPPED]
)
def test_fix_draft_missing_alone_behind_trimmable_glue_reaches_the_probe_unaided(
    tmp_path, glue_name, glue
):
    """THE NEGATIVE CONTROL for the two tests above -- the same shape, the other
    side of the trim() partition.

    trim() strips this glue, so the line genuinely EQUALS the sentinel and
    whole-line equality recognises the report on its own: these rows must be
    green with the containment call AND without it. They are what shows the
    cases above track the real mechanism -- whole-line equality modulo trim() --
    rather than merely detecting that a containment call exists. If a future
    edit makes these rows depend on containment, the mechanism has changed and
    the partition this section is built on is no longer true."""
    out = _run_with_fix_reply(tmp_path, _sentinel_alone_on_its_line(glue))
    _assert_report_reached_the_draft_probe(
        out,
        f"the sentinel alone on its line behind {glue_name} -- which trim() DOES "
        f"strip, so this row must hold with no containment involved at all",
        ALONE_ON_LINE_DETAIL[glue_name],
    )


def test_review_wait_non_terminal_quoted_ready_still_times_out(tmp_path):
    """5a (round-2 codex finding, MAJOR): READY appearing on a NON-final line
    while later prose explicitly disavows it must NOT be accepted. This is
    codex's own counter-example, reused verbatim so the regression is
    grounded in the exact reply that broke the round-1 "any line" draft --
    proving the shipped whole-string check's rejection of this reply is
    preserved by the round-2 last-line design, not reintroduced as a false
    accept."""
    res = run(
        tmp_path=tmp_path, segs=["seg01"],
        overrides={"review-wait:seg01:r1": QUOTED_SUCCESS_DISAVOWED},
    )
    assert res["ok"], res["stderr"]
    out = res["out"]
    # #400 -- QUOTED_SUCCESS_DISAVOWED never reaches READY on any chunk, so the
    # loop exhausts and the unoverridden re-check's own default "PENDING
    # seg01" is the last reply seen -- same reasoning as the collision tests.
    assert out["result"]["failed"] == [
        {"seg": "seg01", "converged": False, "reason": "review-timeout", "rounds": 1, "detail": "PENDING seg01"}
    ]
    assert out["result"]["converged"] == []


def test_translate_wait_non_terminal_quoted_ready_still_times_out(tmp_path):
    """5a at site E -- same disavowed-quote regression as above."""
    res = run(
        tmp_path=tmp_path, segs=["seg01"],
        overrides={"wait:seg01": QUOTED_SUCCESS_DISAVOWED},
    )
    assert res["ok"], res["stderr"]
    out = res["out"]
    assert out["result"]["failed"] == [
        {"seg": "seg01", "converged": False, "reason": "translate-timeout", "detail": "PENDING seg01"}
    ]
    assert out["result"]["converged"] == []


# Round-8 sweep finding: readReviewPrompt's "do not judge or second-guess the
# reviewer's verdict" was unpinned. PRESENCE-ONLY, same caveat as the
# dispatch pin above. Partial mitigation named rather than assumed: a
# misreported verdict here is not the only line of defense -- getVerifiedReview
# later runs verifyReviewArtifactPrompt/review_artifact_check.py to
# independently re-derive the SAME four fields from the on-disk review.json
# and compare, which is where this file's own artifactCheckMatched() pin
# below (STRUCTURAL, not presence) actually lives. This test only covers the
# read step's own prompt text.
REVIEW_READ_NO_JUDGE_CLAUSE = "do not judge or second-guess the reviewer's verdict"


def test_review_read_prompt_forbids_judging_the_verdict(tmp_path):
    out = _happy_run(tmp_path)
    prompt = out["promptByLabel"]["review-read:seg01:r1"]
    assert REVIEW_READ_NO_JUDGE_CLAUSE in prompt, (
        "the review-read prompt must forbid the agent from judging or "
        f"second-guessing the reviewer's verdict; prompt was:\n{prompt}"
    )


# Round-8 sweep finding: draftProbePrompt's "do not translate, fix, or judge
# anything" was unpinned. PRESENCE-ONLY. Reached via the same fixture
# test_fix_null_return_still_triggers_probe uses above, since the default
# happy path never dispatches a fix round and so never reaches this label.
DRAFT_PROBE_NO_ACTION_CLAUSE = "do not translate, fix, or judge anything"


def test_draft_probe_prompt_forbids_acting_on_the_draft(tmp_path):
    res = run(
        tmp_path=tmp_path, segs=["seg01"],
        overrides={
            "review-read:seg01:r1": _non_clean_review(),
            "artifact-check:seg01:r1": {"match": True},
            "fix:seg01:r1": None,
        },
    )
    assert res["ok"], res["stderr"]
    out = res["out"]
    labels = [c["label"] for c in out["calls"]]
    assert "draft-probe:seg01" in labels, (
        f"expected the fix-call failure to reach the draft probe; calls were {labels}"
    )
    prompt = out["promptByLabel"]["draft-probe:seg01"]
    assert DRAFT_PROBE_NO_ACTION_CLAUSE in prompt, (
        "the draft-probe prompt must forbid the agent from translating, "
        f"fixing, or judging anything itself; prompt was:\n{prompt}"
    )


# ---------------------------------------------------------------------------
# #289 -- the two ledger guards (ledgerWriteSucceeded/ledgerMergeSucceeded)
# must judge failure EVIDENCE, never failure-key PRESENCE.
#
# LEDGER_WRITE_SCHEMA/LEDGER_MERGE_SCHEMA are flat unions of both branches
# (CONTRACT section 1), so they ADVERTISE error/exit_code/stderr as fillable
# fields on every ledger call. In the first live end-to-end W5 run two of
# three agents took that invitation and truthfully relayed `exit_code: 0`
# alongside an otherwise perfect success return; the guards' old
# `FAILURE_ONLY_KEYS.some((k) => k in raw)` presence test read that PROOF OF
# SUCCESS as proof of failure and reported ledger-write-failed /
# ledger-merge-failed for segments whose fragments were already correctly on
# disk. Whether an agent volunteers the field is model discretion, so the
# old gate's verdict was non-deterministic across identical prompts.
#
# These cases run the REAL template under Node and assert the OBSERVABLE
# workflow result, not the predicate in isolation: OVERRIDES injects the
# return at the exact ledger label under test, every other call keeps its
# happy-path default.
# ---------------------------------------------------------------------------

def _write_success(seg: str, status: str = "converged", **extra) -> dict:
    """The exact success return recordLedgerPrompt asks for, plus whatever
    extra fields a test wants the agent to have volunteered."""
    raw = {
        "success": True, "status": status,
        "fragment_path": "/x/" + seg + ".json", "fragment_sha1": "d",
    }
    raw.update(extra)
    return raw


def _merge_success(n_segments: int = 1, **extra) -> dict:
    """The merge-ledger counterpart of _write_success."""
    raw = {
        "success": True, "ledger_path": "/x/l.json", "n_segments": n_segments,
        "missing_segments": [], "stale_segments": [],
    }
    raw.update(extra)
    return raw


WRITE_FAILED_DEFAULT_DETAIL = "ledger_update.py write did not report success"
MERGE_FAILED_DEFAULT_DETAIL = "ledger_merge.py completeness check did not report success"


def test_ledger_write_accepts_truthful_exit_code_zero(tmp_path):
    """#289 core case, reproducing the live W5 run: BOTH per-segment ledger
    writes come back with a truthful `exit_code: 0` riding along. The script
    really did exit 0, so the segment must converge -- the run that motivated
    this issue instead reported ledger-write-failed for a fragment that was
    already correct on disk."""
    res = run(
        tmp_path=tmp_path, segs=["seg01"],
        overrides={
            "ledger:in_progress:seg01": _write_success("seg01", status="in_progress", exit_code=0),
            "ledger:converged:seg01": _write_success("seg01", exit_code=0),
        },
    )
    assert res["ok"], res["stderr"]
    out = res["out"]
    assert out["result"]["converged"] == [{"seg": "seg01", "converged": True, "rounds": 1}]
    assert out["result"]["failed"] == []
    assert out["result"]["batchComplete"] is True


# Round-8 sweep finding: recordLedgerPrompt's scratch-file naming
# instruction, "never reuse an existing scratch file" -- unpinned.
# PRESENCE-ONLY: a collision-avoidance instruction, not a capability
# boundary (the residual harm is a corrupted/racing write between two
# ledger-write calls sharing a scratch path, not network reach or a trusted
# verdict), so this is deliberately the lowest-priority pin in this file.
LEDGER_SCRATCH_FILE_NO_REUSE_CLAUSE = "never reuse an existing scratch file"


def test_ledger_write_prompt_forbids_reusing_a_scratch_file(tmp_path):
    out = _happy_run(tmp_path)
    prompt = out["promptByLabel"]["ledger:converged:seg01"]
    assert LEDGER_SCRATCH_FILE_NO_REUSE_CLAUSE in prompt, (
        "the ledger-write prompt must forbid reusing an existing scratch "
        f"file; prompt was:\n{prompt}"
    )


# Round-8 sweep item singled out for the strongest treatment: "Do not trust
# the command's own fragment_sha1 claim without this independent check" is
# not a scope reminder -- it IS the only independent cross-check that
# exists anywhere for ledger_update.py's reported hash. If it goes, nothing
# re-derives the hash. Same shape and same two-part structure as
# glossary_snapshot_ordering.test.py's/skeptic_pipeline_e2e.test.py's
# test_verify_result_trust_rests_on_shape_alone_not_independent_corroboration.
LEDGER_INDEPENDENT_SHA1_CHECK_CLAUSE = (
    "Do not trust the command's own fragment_sha1 claim without this "
    "independent check"
)


def test_ledger_write_result_trust_rests_on_shape_alone_not_independent_corroboration(tmp_path):
    """The STRONG form of the sha1-independence property.

    1. SOURCE-STRUCTURAL: ledgerWriteSucceeded() -- the one function
       standing between the agent's reply and a converged ledger entry -- is
       read directly out of the real template and asserted to contain none
       of createHash/readFileSync/execFileSync/spawnSync/require(/
       subprocess/agent(: it checks only that fragment_sha1 is a non-empty
       STRING (isNonEmptyString), never its VALUE, and never independently
       recomputes anything itself.
    2. BEHAVIOURAL: this file's OWN happy-path mock -- _write_success(),
       which every ledger-write override in this file builds on -- already
       returns the trivially fake `fragment_sha1: "d"`, one character,
       obviously never computed by hashlib over any real file, and the
       workflow converges the segment anyway. That is not new behaviour
       created by this test: it is the precondition this entire file's
       fixtures already depend on, made an explicit, named assertion here
       instead of an implicit byproduct nobody has to notice. Reinforced
       with a second, differently-fake value below to show it is not
       specifically the string "d" that slips through.
    """
    template_source = MASS_TRANSLATE_TEMPLATE.read_text(encoding="utf-8")
    m = re.search(r"function ledgerWriteSucceeded\(raw\) \{(.*?)\n\}", template_source, re.DOTALL)
    assert m, (
        "ledgerWriteSucceeded() not found in mass-translate-wf.template.js "
        "-- has it been renamed or restructured? This test's whole premise "
        "is that function's own body."
    )
    body = m.group(1)
    for marker in ("createHash", "readFileSync", "execFileSync", "spawnSync", "require(", "subprocess", "agent("):
        assert marker not in body, (
            f"ledgerWriteSucceeded() now contains {marker!r} -- it used to "
            "be a pure shape/value check with no independent recomputation "
            "of the hash; if that changed on purpose, this assertion needs "
            f"to be revisited, not silenced. Body was:\n{body}"
        )

    res = run(
        tmp_path=tmp_path, segs=["seg01"],
        overrides={"ledger:converged:seg01": _write_success("seg01", fragment_sha1="not-a-real-sha1-either")},
    )
    assert res["ok"], res["stderr"]
    assert res["out"]["result"]["converged"] == [{"seg": "seg01", "converged": True, "rounds": 1}], (
        "a ledger-write reply carrying an obviously-fake fragment_sha1 "
        "value (never independently computed by anything the harness ran) "
        "still converges the segment -- confirms nothing downstream "
        "corroborates the agent's independent-check claim; result was "
        f"{res['out']['result']}"
    )


def test_ledger_merge_accepts_truthful_exit_code_zero(tmp_path):
    """The merge-ledger half of the same defect: a truthful `exit_code: 0` on
    the batch-final completeness check must not flip an otherwise complete
    batch to ledger-merge-failed."""
    res = run(
        tmp_path=tmp_path, segs=["seg01"],
        overrides={"merge-ledger": _merge_success(exit_code=0)},
    )
    assert res["ok"], res["stderr"]
    out = res["out"]
    assert out["result"]["batchComplete"] is True
    assert out["result"]["ledgerPath"] == "/x/l.json"
    assert "reason" not in out["result"]


@pytest.mark.parametrize(
    "evidence,expected_detail",
    [
        pytest.param({"exit_code": 3}, WRITE_FAILED_DEFAULT_DETAIL, id="nonzero-exit-code"),
        pytest.param({"exit_code": "0"}, WRITE_FAILED_DEFAULT_DETAIL, id="wrong-typed-exit-code"),
        pytest.param(
            {"stderr": "Traceback (most recent call last):\n  RuntimeError"},
            WRITE_FAILED_DEFAULT_DETAIL, id="nonempty-stderr",
        ),
        pytest.param(
            {"error": "runs/ledger.d is not writable"},
            "runs/ledger.d is not writable", id="nonempty-error",
        ),
    ],
)
def test_ledger_write_still_rejects_real_failure_evidence(tmp_path, evidence, expected_detail):
    """The anti-false-green half of #289: relaxing the guard to accept
    `exit_code: 0` must not weaken it for anything that is genuine evidence
    of failure -- a non-zero (or unreadable, wrong-typed) exit code, a
    traceback on stderr, or an error message -- even when `success: true` and
    every success field is present and plausible."""
    res = run(
        tmp_path=tmp_path, segs=["seg01"],
        overrides={"ledger:converged:seg01": _write_success("seg01", **evidence)},
    )
    assert res["ok"], res["stderr"]
    out = res["out"]
    assert out["result"]["converged"] == []
    assert out["result"]["failed"] == [
        {"seg": "seg01", "converged": False, "reason": "ledger-write-failed", "detail": expected_detail}
    ]


def test_ledger_write_still_rejects_success_false(tmp_path):
    """`success: false` remains fatal on its own, with the returned error
    relayed as the failure detail."""
    res = run(
        tmp_path=tmp_path, segs=["seg01"],
        overrides={"ledger:converged:seg01": {"success": False, "error": "boom"}},
    )
    assert res["ok"], res["stderr"]
    assert res["out"]["result"]["failed"] == [
        {"seg": "seg01", "converged": False, "reason": "ledger-write-failed", "detail": "boom"}
    ]


@pytest.mark.parametrize(
    "raw,expected_detail",
    [
        pytest.param(
            _write_success("seg01", fragment_sha1="", exit_code=0),
            WRITE_FAILED_DEFAULT_DETAIL, id="empty-success-field-alongside-exit-code-zero",
        ),
        pytest.param(
            _write_success("seg01", ledger_path="/x/l.json", exit_code=0),
            WRITE_FAILED_DEFAULT_DETAIL, id="undeclared-key-alongside-exit-code-zero",
        ),
    ],
)
def test_exit_code_zero_does_not_bypass_the_other_write_guards(tmp_path, raw, expected_detail):
    """A benign `exit_code: 0` buys nothing beyond itself: the success-field
    completeness check and the allowed-key-set check still run and still
    reject. Locks in that the #289 relaxation was scoped to the three
    failure-evidence fields and did not turn into a blanket
    `additionalProperties` amnesty."""
    res = run(tmp_path=tmp_path, segs=["seg01"], overrides={"ledger:converged:seg01": raw})
    assert res["ok"], res["stderr"]
    assert res["out"]["result"]["failed"] == [
        {"seg": "seg01", "converged": False, "reason": "ledger-write-failed", "detail": expected_detail}
    ]


@pytest.mark.parametrize(
    "raw,expected_detail",
    [
        pytest.param(_merge_success(exit_code=2), MERGE_FAILED_DEFAULT_DETAIL, id="nonzero-exit-code"),
        pytest.param(_merge_success(stderr="cache_key.py died"), MERGE_FAILED_DEFAULT_DETAIL, id="nonempty-stderr"),
        pytest.param(_merge_success(error="fragment dir missing"), "fragment dir missing", id="nonempty-error"),
        pytest.param(
            _merge_success(missing_segments=["seg09"], exit_code=0),
            MERGE_FAILED_DEFAULT_DETAIL, id="incomplete-batch-alongside-exit-code-zero",
        ),
        pytest.param({"success": False, "error": "boom"}, "boom", id="success-false"),
    ],
)
def test_ledger_merge_still_rejects_real_failure_evidence(tmp_path, raw, expected_detail):
    """The merge-side anti-false-green cases, including the guarantee that a
    benign `exit_code: 0` never excuses a non-empty missing_segments -- the
    completeness check is the whole point of this call."""
    res = run(tmp_path=tmp_path, segs=["seg01"], overrides={"merge-ledger": raw})
    assert res["ok"], res["stderr"]
    out = res["out"]
    assert out["result"]["batchComplete"] is False
    assert out["result"]["reason"] == "ledger-merge-failed"
    assert out["result"]["detail"] == expected_detail


# ---------------------------------------------------------------------------
# #289 third site -- artifactCheckMatched(), same class as the two ledger
# guards above. REVIEW_ARTIFACT_SCHEMA declares `mismatch_detail` fillable
# alongside `match`, and review_artifact_check.py itself NEVER emits it on a
# match (`emit_match` prints a bare `{"match": true}`), so any
# mismatch_detail riding on a match:true return is agent-added -- exactly
# how `exit_code` got onto the ledger returns. Rejecting on presence turned
# a benign fill into `blocked/review-artifact-mismatch`, which is terminal
# human escalation rather than the ledger path's recoverable redispatch.
#
# The retried read+check pair carries its own ":retry" label suffix, so a
# case that must survive BOTH attempts overrides both.
# ---------------------------------------------------------------------------

ARTIFACT_LABELS = ("artifact-check:seg01:r1", "artifact-check:seg01:r1:retry")


def _artifact_overrides(art: dict) -> dict:
    """Same artifact-check return on the first attempt and on the one shared
    retry -- getVerifiedReview only blocks after both have failed."""
    return {label: art for label in ARTIFACT_LABELS}


def test_artifact_check_accepts_benign_empty_mismatch_detail(tmp_path):
    """#289 at the third site: a genuine match whose relay volunteered an
    EMPTY mismatch_detail must still be a match. The pre-fix guard rejected
    it on presence alone and escalated a perfectly good review artifact."""
    res = run(
        tmp_path=tmp_path, segs=["seg01"],
        overrides=_artifact_overrides({"match": True, "mismatch_detail": ""}),
    )
    assert res["ok"], res["stderr"]
    out = res["out"]
    assert out["result"]["converged"] == [{"seg": "seg01", "converged": True, "rounds": 1}]
    assert out["result"]["failed"] == []
    labels = [c["label"] for c in out["calls"]]
    assert ARTIFACT_LABELS[1] not in labels, (
        "an accepted first check must not trigger the shared read+check retry"
    )


# Round-8 sweep item, second one singled out for the strongest treatment.
# verifyReviewArtifactPrompt's "do not judge the comparison yourself" /
# "do not re-judge it" and artifactCheckMatched() -- the JS-side trust
# function directly above this test's own section -- are the SAME shape as
# the ledger sha1 case: a pure shape check over the reply object, with
# nothing downstream re-running review_artifact_check.py or re-deriving
# `match` independently. This is the DEEPEST layer in the review-verdict
# chain (see the review-read pin's comment above, which names this as its
# own partial mitigation) -- past this point, nothing else corroborates.
ARTIFACT_CHECK_NO_JUDGE_CLAUSE = "do not judge the comparison yourself"
ARTIFACT_CHECK_NO_REJUDGE_CLAUSE = "do not re-judge it"


def test_artifact_check_prompt_forbids_judging_the_comparison(tmp_path):
    """PRESENCE half: both clause texts must be in the rendered prompt."""
    out = _happy_run(tmp_path)
    prompt = out["promptByLabel"]["artifact-check:seg01:r1"]
    assert ARTIFACT_CHECK_NO_JUDGE_CLAUSE in prompt, (
        "the artifact-check prompt must forbid the agent from judging the "
        f"comparison itself; prompt was:\n{prompt}"
    )
    assert ARTIFACT_CHECK_NO_REJUDGE_CLAUSE in prompt, (
        "the artifact-check prompt must forbid re-judging the script's own "
        f"comparison; prompt was:\n{prompt}"
    )


def test_artifact_check_result_trust_rests_on_shape_alone_not_independent_corroboration(tmp_path):
    """STRUCTURAL + BEHAVIOURAL half, same two-part shape as the ledger
    sha1 pin above and the glossary/skeptic verify-trust pins.

    1. SOURCE-STRUCTURAL: artifactCheckMatched() is read directly out of the
       real template and asserted to contain none of
       execFileSync/spawnSync/require(/subprocess/agent(/review_artifact_
       check -- it checks only that `match` is exactly `true`, that no
       failure evidence is present, and that no undeclared key rides along;
       it never re-runs review_artifact_check.py or re-derives the
       comparison itself.
    2. BEHAVIOURAL: a bare `{"match": true}` reply -- which every other test
       in this ARTIFACT_LABELS section already uses as ITS happy path,
       without any review_artifact_check.py subprocess ever actually
       running inside this harness -- still converges the segment.
    """
    template_source = MASS_TRANSLATE_TEMPLATE.read_text(encoding="utf-8")
    m = re.search(r"function artifactCheckMatched\(art\) \{(.*?)\n\}", template_source, re.DOTALL)
    assert m, (
        "artifactCheckMatched() not found in mass-translate-wf.template.js "
        "-- has it been renamed or restructured? This test's whole premise "
        "is that function's own body."
    )
    body = m.group(1)
    for marker in ("execFileSync", "spawnSync", "require(", "subprocess", "agent(", "review_artifact_check"):
        assert marker not in body, (
            f"artifactCheckMatched() now contains {marker!r} -- it used to "
            "be a pure shape check over the reply object with no "
            "independent re-derivation of the comparison; if that changed "
            f"on purpose, this assertion needs to be revisited, not "
            f"silenced. Body was:\n{body}"
        )

    res = run(
        tmp_path=tmp_path, segs=["seg01"],
        overrides=_artifact_overrides({"match": True}),
    )
    assert res["ok"], res["stderr"]
    assert res["out"]["result"]["converged"] == [{"seg": "seg01", "converged": True, "rounds": 1}], (
        "a bare match:true reply, never independently corroborated against "
        "review_artifact_check.py's own real comparison, still converges "
        f"the segment; result was {res['out']['result']}"
    )


@pytest.mark.parametrize(
    "art,expected_detail",
    [
        pytest.param(
            {"match": True, "mismatch_detail": "expected sha1 a.. got b.."},
            "expected sha1 a.. got b..", id="real-mismatch-detail",
        ),
        pytest.param(
            {"match": True, "mismatch_detail": None},
            "agent call returned no usable object", id="wrong-typed-mismatch-detail",
        ),
        pytest.param(
            {"match": False, "mismatch_detail": "artifact differs"},
            "artifact differs", id="honest-mismatch",
        ),
        pytest.param(
            {"match": True, "verified": True},
            "agent call returned no usable object", id="undeclared-key",
        ),
    ],
)
def test_artifact_check_still_rejects(tmp_path, art, expected_detail):
    """The anti-false-green half at the third site. A real mismatch_detail is
    still fatal even next to match:true; an unreadable (wrong-typed) one
    fails closed; an honest match:false is unchanged; and a key
    REVIEW_ARTIFACT_SCHEMA never declared is now rejected too -- the guard
    gained the allowed-key check its two ledger siblings always had.

    #400 -- the retry's own art object is where the detail comes from
    (getVerifiedReview never trusts the first attempt's evidence once a
    retry runs): a real STRING mismatch_detail is relayed verbatim even next
    to match:true, while a wrong-typed or absent one falls through to
    replyDetail() on the whole art object, which is truthy and not a string,
    hence "agent call returned no usable object" for both the wrong-typed
    and undeclared-key cases."""
    res = run(tmp_path=tmp_path, segs=["seg01"], overrides=_artifact_overrides(art))
    assert res["ok"], res["stderr"]
    out = res["out"]
    assert out["result"]["converged"] == []
    assert out["result"]["failed"] == [
        {
            "seg": "seg01", "converged": False, "reason": "review-artifact-mismatch", "rounds": 1,
            "detail": expected_detail,
        }
    ]


# ---------------------------------------------------------------------------
# #400 -- direct coverage of the detail/waitDetail threading and the batch
# failureDetailTally, beyond the strict-equality repairs above (which only
# pin what the shipped template already produces, not the property that
# makes each value correct). Every test below was watched RED against a
# targeted /tmp mutation of the real template before being written here --
# never claimed from reading the source alone; each docstring names its own
# mutation and what went wrong under it.
# ---------------------------------------------------------------------------

def test_translate_wait_null_every_call_reports_null_detail(tmp_path):
    """Every chunk AND the re-check at site E return null. RED before #400
    against a mutant that reverts timeoutVerdict() to
    `return { status: "blocked", reason: reason };` (no detail field at
    all): the failed row then carries no "detail" key, and the equality
    assertion below fails."""
    res = run(
        tmp_path=tmp_path, segs=["seg01"],
        overrides={"wait:seg01": None, "wait-recheck:seg01": None},
    )
    assert res["ok"], res["stderr"]
    assert res["out"]["result"]["failed"] == [
        {"seg": "seg01", "converged": False, "reason": "translate-timeout", "detail": "agent call returned null"}
    ]


def test_review_wait_null_every_call_reports_null_detail(tmp_path):
    """Site C's twin of the test above -- same mutant, same failure shape."""
    res = run(
        tmp_path=tmp_path, segs=["seg01"],
        overrides={"review-wait:seg01:r1": None, "review-wait-recheck:seg01:r1": None},
    )
    assert res["ok"], res["stderr"]
    assert res["out"]["result"]["failed"] == [
        {
            "seg": "seg01", "converged": False, "reason": "review-timeout", "rounds": 1,
            "detail": "agent call returned null",
        }
    ]


def test_translate_wait_timeout_detail_is_the_last_reply_not_the_first(tmp_path):
    """The chunk (first reply seen) and the re-check (last reply seen)
    answer DIFFERENTLY -- null, then an empty string -- so the two are
    distinguishable in replyDetail()'s own output ("agent call returned
    null" vs "agent call returned an empty reply"). RED before #400 against
    a mutant that drops the `lastWaitReply = recheck;` assignment (freezing
    lastWaitReply at whatever the chunk loop last set it to): the detail
    then stays the chunk's null instead of the re-check's empty-string
    value, and the assertion below fails."""
    res = run(
        tmp_path=tmp_path, segs=["seg01"],
        overrides={"wait:seg01": None, "wait-recheck:seg01": ""},
    )
    assert res["ok"], res["stderr"]
    assert res["out"]["result"]["failed"] == [
        {
            "seg": "seg01", "converged": False, "reason": "translate-timeout",
            "detail": "agent call returned an empty reply",
        }
    ]


def test_review_wait_timeout_detail_is_the_last_reply_not_the_first(tmp_path):
    """Site C's twin of the test above -- same mutant shape, applied to
    getVerifiedReview's own `lastWaitReply = recheck;` assignment."""
    res = run(
        tmp_path=tmp_path, segs=["seg01"],
        overrides={"review-wait:seg01:r1": None, "review-wait-recheck:seg01:r1": ""},
    )
    assert res["ok"], res["stderr"]
    assert res["out"]["result"]["failed"] == [
        {
            "seg": "seg01", "converged": False, "reason": "review-timeout", "rounds": 1,
            "detail": "agent call returned an empty reply",
        }
    ]


def test_translate_dispatch_null_detail_survives_a_healthy_looking_wait(tmp_path):
    """The DISPATCH reply died (null) but the waits that follow answer a
    healthy-LOOKING per-segment PENDING -- the dispatch detail must still
    be what is reported, with the wait's own text preserved as waitDetail
    (timeoutVerdict()'s documented priority: dispatchDetail outranks
    waitDetail only when the dispatch reply was falsy, and the wait's own
    text is never thrown away). RED before #400 against a mutant that
    hardcodes translateStage's own dispatchDetail to always null: the
    detail then collapses to the wait's own "PENDING seg01" with no
    waitDetail key at all, and the assertion below fails."""
    res = run(
        tmp_path=tmp_path, segs=["seg01"],
        overrides={"translate:seg01": None, "wait:seg01": "PENDING seg01", "wait-recheck:seg01": "PENDING seg01"},
    )
    assert res["ok"], res["stderr"]
    assert res["out"]["result"]["failed"] == [
        {
            "seg": "seg01", "converged": False, "reason": "translate-timeout",
            "detail": "translate dispatch: agent call returned null", "waitDetail": "PENDING seg01",
        }
    ]


def test_review_dispatch_null_detail_survives_a_healthy_looking_wait(tmp_path):
    """Site C's twin of the test above -- same mutant shape, applied to
    callReviewDispatch's own dispatchDetail."""
    res = run(
        tmp_path=tmp_path, segs=["seg01"],
        overrides={
            "review-dispatch:seg01:r1": None,
            "review-wait:seg01:r1": "PENDING seg01", "review-wait-recheck:seg01:r1": "PENDING seg01",
        },
    )
    assert res["ok"], res["stderr"]
    assert res["out"]["result"]["failed"] == [
        {
            "seg": "seg01", "converged": False, "reason": "review-timeout", "rounds": 1,
            "detail": "review dispatch: agent call returned null", "waitDetail": "PENDING seg01",
        }
    ]


def test_translate_dispatch_null_alone_still_converges_when_wait_answers_ready(tmp_path):
    """The counterexample that keeps the two tests above honest: a null
    DISPATCH reply on its own, with the wait left at this harness's own
    default READY, must still CONVERGE -- the dispatch command launches the
    detached codex job BEFORE relaying its own acknowledgement, so the
    launch can succeed while only the ack is lost (see timeoutVerdict()'s
    own comment). Without this case, an implementation that times out on
    ANY falsy dispatch reply -- never even polling the wait -- would pass
    every other #400 test in this file. RED before #400 against exactly
    that implementation, wired in as a mutant at reviewFixLoop's own wait
    entry (an immediate timeoutVerdict() return whenever dispatchDetail is
    non-null, before the chunk loop ever runs): the segment reports
    translate-timeout instead of converging, and the assertion below
    fails."""
    res = run(tmp_path=tmp_path, segs=["seg01"], overrides={"translate:seg01": None})
    assert res["ok"], res["stderr"]
    assert res["out"]["result"]["converged"] == [{"seg": "seg01", "converged": True, "rounds": 1}]
    assert res["out"]["result"]["failed"] == []


def test_review_dispatch_null_alone_still_converges_when_wait_answers_ready(tmp_path):
    """Site C's twin of the test above -- same mutant shape, applied at
    getVerifiedReview's own wait entry."""
    res = run(tmp_path=tmp_path, segs=["seg01"], overrides={"review-dispatch:seg01:r1": None})
    assert res["ok"], res["stderr"]
    assert res["out"]["result"]["converged"] == [{"seg": "seg01", "converged": True, "rounds": 1}]
    assert res["out"]["result"]["failed"] == []


# The four tests above only exercise a NULL dispatch reply, which is one
# instance of parseDisp() rejecting it, not the general case: a dispatcher
# that answers a truthy, fully-formed failure SENTENCE -- what an actual
# outage produces, and the MR bot's own reproduction for this fix -- is
# rejected by parseDisp() identically, and unlike null it is the SAME string
# on every segment. dispatchDetail is keyed on `disp === ""` (the reply
# rejected) rather than on `raw` being falsy specifically so this case is
# also caught; before the fix, `raw` here is truthy, so dispatchDetail stayed
# null, each segment fell back to its own per-segment "PENDING <seg>" wait
# text, and failureDetailTally came back empty on the exact outage this PR
# exists to surface.
DISPATCH_REJECTED_SHARED_DETAIL = "Dispatcher could not launch the codex job: service unavailable"


def test_failure_detail_tally_buckets_a_shared_translate_dispatch_rejection(tmp_path):
    """Two segments' TRANSLATE dispatcher both answer the same truthy,
    unparseable failure sentence; both waits then answer their own
    per-segment PENDING. Both rows must carry the SAME "translate dispatch:
    ..." detail with their own waitDetail preserved, and the tally must
    bucket them as one entry of 2 with the matching log line. RED before
    this fix against a mutant reverting both dispatchDetail sites to
    `raw ? null : sourcedDetail(...)` (the pre-fix `raw`-falsy keying):
    observed red was an EMPTY failureDetailTally and two per-segment
    "PENDING seg0N" details instead of the shared dispatch sentence -- the
    exact operator-facing failure this fix closes, confirmed by running both
    the fixed and the reverted template through this harness."""
    overrides = {}
    for seg in ("seg01", "seg02"):
        overrides["translate:" + seg] = DISPATCH_REJECTED_SHARED_DETAIL
        overrides["wait:" + seg] = "PENDING " + seg
        overrides["wait-recheck:" + seg] = "PENDING " + seg
    res = run(tmp_path=tmp_path, segs=["seg01", "seg02"], overrides=overrides)
    assert res["ok"], res["stderr"]
    out = res["out"]
    expected_detail = "translate dispatch: " + DISPATCH_REJECTED_SHARED_DETAIL
    assert out["result"]["failed"] == [
        {
            "seg": "seg01", "converged": False, "reason": "translate-timeout",
            "detail": expected_detail, "waitDetail": "PENDING seg01",
        },
        {
            "seg": "seg02", "converged": False, "reason": "translate-timeout",
            "detail": expected_detail, "waitDetail": "PENDING seg02",
        },
    ]
    assert out["result"]["failureDetailTally"] == [{"detail": expected_detail, "count": 2}]
    assert f"Repeated failure detail (2/2 failed): {expected_detail}" in out["logLines"]


def test_failure_detail_tally_buckets_a_shared_review_dispatch_rejection(tmp_path):
    """Site C's twin of the test above -- same overrides and mutant shape,
    applied at callReviewDispatch/getVerifiedReview."""
    overrides = {}
    for seg in ("seg01", "seg02"):
        overrides["review-dispatch:" + seg + ":r1"] = DISPATCH_REJECTED_SHARED_DETAIL
        overrides["review-wait:" + seg + ":r1"] = "PENDING " + seg
        overrides["review-wait-recheck:" + seg + ":r1"] = "PENDING " + seg
    res = run(tmp_path=tmp_path, segs=["seg01", "seg02"], overrides=overrides)
    assert res["ok"], res["stderr"]
    out = res["out"]
    expected_detail = "review dispatch: " + DISPATCH_REJECTED_SHARED_DETAIL
    assert out["result"]["failed"] == [
        {
            "seg": "seg01", "converged": False, "reason": "review-timeout", "rounds": 1,
            "detail": expected_detail, "waitDetail": "PENDING seg01",
        },
        {
            "seg": "seg02", "converged": False, "reason": "review-timeout", "rounds": 1,
            "detail": expected_detail, "waitDetail": "PENDING seg02",
        },
    ]
    assert out["result"]["failureDetailTally"] == [{"detail": expected_detail, "count": 2}]
    assert f"Repeated failure detail (2/2 failed): {expected_detail}" in out["logLines"]


def test_translate_dispatch_rejected_reply_still_converges_when_wait_answers_ready(tmp_path):
    """The counterexample that keeps the two tests above honest, mirroring
    test_translate_dispatch_null_alone_still_converges_when_wait_answers_ready:
    a truthy-but-unparseable dispatch reply, with the wait left at this
    harness's own default READY, must still CONVERGE. A rejected DISP is
    safe degradation -- the dispatch command launches the detached codex job
    BEFORE relaying its own acknowledgement, so the launch can succeed while
    only the ack comes back unparseable -- and must not become a timeout on
    its own. Without this case, an implementation that widened the #400 fix
    into short-circuiting to translate-timeout whenever dispatchDetail is
    non-null (rather than merely recording it for a wait that actually times
    out) would pass both tally tests above while breaking every healthy
    dispatch whose acknowledgement merely came back garbled. RED against
    exactly that mutant, wired in at reviewFixLoop's own wait entry right
    after dispatchDetail is computed (an immediate return whenever it is
    non-null, before the chunk loop ever runs): the segment reported
    translate-timeout with converged: [] instead of converging."""
    res = run(tmp_path=tmp_path, segs=["seg01"], overrides={"translate:seg01": DISPATCH_REJECTED_SHARED_DETAIL})
    assert res["ok"], res["stderr"]
    assert res["out"]["result"]["converged"] == [{"seg": "seg01", "converged": True, "rounds": 1}]
    assert res["out"]["result"]["failed"] == []


def test_review_dispatch_rejected_reply_still_converges_when_wait_answers_ready(tmp_path):
    """Site C's twin of the test above -- same mutant shape (the immediate
    return wired in at getVerifiedReview's own entry, right after the
    dispatch call, whenever dispatch.dispatchDetail is non-null)."""
    res = run(
        tmp_path=tmp_path, segs=["seg01"],
        overrides={"review-dispatch:seg01:r1": DISPATCH_REJECTED_SHARED_DETAIL},
    )
    assert res["ok"], res["stderr"]
    assert res["out"]["result"]["converged"] == [{"seg": "seg01", "converged": True, "rounds": 1}]
    assert res["out"]["result"]["failed"] == []


def test_fix_call_failed_detail_is_the_fix_reply_when_probe_present_true(tmp_path):
    """The probe genuinely ran and answered present:true -- the detail must
    name the FIX call's own (flattened) reply, not the probe. RED before
    #400 against a mutant that swaps the two branches of runRound's
    `present === null ? PROBE_NULL_DETAIL : sourcedDetail("fix call", fx)`
    ternary: the detail becomes the frozen PROBE_NULL_DETAIL constant even
    though the probe answered present:true, and the assertion below
    fails."""
    res = run(
        tmp_path=tmp_path, segs=["seg01"],
        overrides={
            "review-read:seg01:r1": _non_clean_review(),
            "artifact-check:seg01:r1": {"match": True},
            "fix:seg01:r1": "The fix could not complete because the draft file vanished mid-run: DRAFT_MISSING seg01",
            "draft-probe:seg01": {"present": True},
        },
    )
    assert res["ok"], res["stderr"]
    assert res["out"]["result"]["failed"] == [
        {
            "seg": "seg01", "converged": False, "reason": "fix-call-failed", "rounds": 1,
            "detail": (
                "fix call: The fix could not complete because the draft file "
                "vanished mid-run: DRAFT_MISSING seg01"
            ),
        }
    ]


def test_fix_call_failed_detail_is_the_probe_reply_when_probe_itself_died(tmp_path):
    """The distinguishing counterpart: the PROBE call itself died (null),
    which is inconclusive rather than proof the fix call's own (also null)
    reply was the failure -- the detail must be the frozen PROBE_NULL_DETAIL
    constant, never a re-derivation from the fix reply. RED before #400
    against the same swapped-ternary mutant as the test above: the detail
    becomes "fix call: agent call returned null" instead, and the assertion
    below fails."""
    res = run(
        tmp_path=tmp_path, segs=["seg01"],
        overrides={
            "review-read:seg01:r1": _non_clean_review(),
            "artifact-check:seg01:r1": {"match": True},
            "fix:seg01:r1": None,
            "draft-probe:seg01": None,
        },
    )
    assert res["ok"], res["stderr"]
    assert res["out"]["result"]["failed"] == [
        {
            "seg": "seg01", "converged": False, "reason": "fix-call-failed", "rounds": 1,
            "detail": "draft probe: agent call returned null",
        }
    ]


def test_failure_detail_tally_buckets_a_shared_translate_timeout_detail(tmp_path):
    """Two segments failing on the SAME detail -- both wait sites, at seg01
    and seg02, returning null on every call -- must land in one bucket with
    count 2, and the batch log must report that bucket. RED before #400
    against the same reverted timeoutVerdict() mutant as the null-detail
    tests above: with no "detail" field on either failed row, the tally
    loop's `typeof row.detail === "string"` guard admits neither row, and
    failureDetailTally comes back empty instead of one bucket of 2."""
    res = run(
        tmp_path=tmp_path, segs=["seg01", "seg02"],
        overrides={
            "wait:seg01": None, "wait-recheck:seg01": None,
            "wait:seg02": None, "wait-recheck:seg02": None,
        },
    )
    assert res["ok"], res["stderr"]
    out = res["out"]
    assert out["result"]["failureDetailTally"] == [{"detail": "agent call returned null", "count": 2}]
    assert "Repeated failure detail (2/2 failed): agent call returned null" in out["logLines"]


def test_failure_detail_tally_orders_buckets_by_count_descending(tmp_path):
    """Three segments share one detail, two share a different one -- the
    3-count bucket must sort first. RED before #400 against a mutant that
    reverses the primary sort comparator (`detailCounts.get(a) -
    detailCounts.get(b)` in place of the shipped `get(b) - get(a)`): the
    2-count bucket sorts first instead, and the assertion below fails."""
    overrides = {}
    for seg in ("seg01", "seg02", "seg03"):
        overrides[f"wait:{seg}"] = None
        overrides[f"wait-recheck:{seg}"] = None
    for seg in ("seg04", "seg05"):
        overrides[f"wait:{seg}"] = ""
        overrides[f"wait-recheck:{seg}"] = ""
    res = run(tmp_path=tmp_path, segs=["seg01", "seg02", "seg03", "seg04", "seg05"], overrides=overrides)
    assert res["ok"], res["stderr"]
    assert res["out"]["result"]["failureDetailTally"] == [
        {"detail": "agent call returned null", "count": 3},
        {"detail": "agent call returned an empty reply", "count": 2},
    ]


def test_failure_detail_tally_breaks_equal_counts_by_detail_ascending(tmp_path):
    """Two buckets of equal size (2 and 2) must break the tie by the detail
    STRING, ascending -- "agent call returned an empty reply" sorts before
    "agent call returned null" (the 'e' in "an empty" precedes the 'n' in
    "null" at the first differing character). RED before #400 against a
    mutant that reverses the tie-break comparator (`a < b ? 1 : (a > b ?
    -1 : 0)` in place of the shipped ascending form): the null bucket sorts
    first instead, and the assertion below fails."""
    overrides = {}
    for seg in ("seg01", "seg02"):
        overrides[f"wait:{seg}"] = None
        overrides[f"wait-recheck:{seg}"] = None
    for seg in ("seg03", "seg04"):
        overrides[f"wait:{seg}"] = ""
        overrides[f"wait-recheck:{seg}"] = ""
    res = run(tmp_path=tmp_path, segs=["seg01", "seg02", "seg03", "seg04"], overrides=overrides)
    assert res["ok"], res["stderr"]
    assert res["out"]["result"]["failureDetailTally"] == [
        {"detail": "agent call returned an empty reply", "count": 2},
        {"detail": "agent call returned null", "count": 2},
    ]


def test_failure_detail_tally_ignores_detail_less_rows_but_counts_them_in_the_denominator(tmp_path):
    """A run mixing two DETAIL-LESS reasons (draft-missing, cap -- neither
    ever sets a `detail` field) with two rows sharing one detail: no
    "undefined" bucket may appear for the detail-less rows, and the logged
    numerator's DENOMINATOR is the FULL failed length (4), not just the 2
    detail-carrying rows. Two mutants, both RED before #400 against this
    same fixture: (1) counting every row via `String(row.detail)` instead
    of gating on `typeof row.detail === "string"` produces a spurious
    {"detail": "undefined", "count": 2} bucket, failing the tally equality
    below; (2) tracking a separate detail-row-only counter and logging
    against IT instead of `failed.length` prints "(2/2 failed)" in place of
    "(2/4 failed)", failing the logLines assertion below."""
    res = run(
        tmp_path=tmp_path, segs=["seg01", "seg02", "seg03", "seg04"], max_fix_rounds=1,
        overrides={
            "wait:seg01": None, "wait-recheck:seg01": None,
            "wait:seg02": None, "wait-recheck:seg02": None,
            "review-read:seg03:r1": _non_clean_review(), "artifact-check:seg03:r1": {"match": True},
            "fix:seg03:r1": "DRAFT_MISSING seg03", "draft-probe:seg03": {"present": False},
            "review-read:seg04:r1": _non_clean_review(), "artifact-check:seg04:r1": {"match": True},
            "review-read:seg04:rfinal": _non_clean_review(), "artifact-check:seg04:rfinal": {"match": True},
        },
    )
    assert res["ok"], res["stderr"]
    out = res["out"]
    assert out["result"]["failed"] == [
        {"seg": "seg01", "converged": False, "reason": "translate-timeout", "detail": "agent call returned null"},
        {"seg": "seg02", "converged": False, "reason": "translate-timeout", "detail": "agent call returned null"},
        {"seg": "seg03", "converged": False, "reason": "draft-missing", "rounds": 1},
        {
            "seg": "seg04", "converged": False, "reason": "cap", "rounds": 2,
            "lastFindings": _non_clean_review()["findings"],
        },
    ], f"the overrides no longer land on the intended reasons/shapes: {out['result']['failed']}"
    assert out["result"]["failureDetailTally"] == [{"detail": "agent call returned null", "count": 2}]
    assert "Repeated failure detail (2/4 failed): agent call returned null" in out["logLines"]


def test_ledger_in_progress_null_call_carries_its_own_null_detail(tmp_path):
    """A falsy ledger:in_progress reply must carry replyDetail(raw), not the
    "ledger_update.py write did not report success" constant -- that
    constant accuses the script of answering and being rejected, and the
    script never answered at all. RED before #400 against a mutant that
    drops recordLedgerCall's `raw ?` branch and always uses the constant:
    the detail becomes the constant string even though raw is null, and
    the assertion below fails."""
    res = run(tmp_path=tmp_path, segs=["seg01"], overrides={"ledger:in_progress:seg01": None})
    assert res["ok"], res["stderr"]
    assert res["out"]["result"]["failed"] == [
        {
            "seg": "seg01", "converged": False, "reason": "ledger-write-failed",
            "detail": "agent call returned null",
        }
    ]


def test_ledger_merge_null_call_carries_its_own_null_detail(tmp_path):
    """The merge-ledger twin of the test above -- same mutant shape, applied
    to the batch-final completeness check's own `mergeResult ?` branch."""
    res = run(tmp_path=tmp_path, segs=["seg01"], overrides={"merge-ledger": None})
    assert res["ok"], res["stderr"]
    out = res["out"]
    assert out["result"]["batchComplete"] is False
    assert out["result"]["reason"] == "ledger-merge-failed"
    assert out["result"]["detail"] == "agent call returned null"


# DETAIL_CAP read directly out of the real template rather than hand-copied,
# so the *derived* uses below stay correct if that constant is ever retuned
# -- but a change to BOTH the constant AND the truncation behaviour together
# would then sail through every test that only ever compares the value
# against itself. test_detail_cap_constant_is_160 below is the belt: it pins
# the LITERAL 160 as its own assertion, so retuning the constant fails
# loudly there even though every derived-use test would stay green.
_DETAIL_CAP_MATCH = re.search(r"const DETAIL_CAP = (\d+);", MASS_TRANSLATE_TEMPLATE.read_text(encoding="utf-8"))
assert _DETAIL_CAP_MATCH, "DETAIL_CAP constant not found in mass-translate-wf.template.js"
DETAIL_CAP = int(_DETAIL_CAP_MATCH.group(1))


def test_detail_cap_constant_is_160():
    """The literal pin: every other test in this file uses the DERIVED
    DETAIL_CAP (read out of the template's own source), which stays green
    even if a future change retunes the constant -- correctly, for those
    tests' own purpose. This one exists solely so a retuning is a visible,
    deliberate act: it fails the moment DETAIL_CAP stops being 160,
    independent of whatever the template's truncation code does with it."""
    assert DETAIL_CAP == 160


# Every break character DETAIL_BREAKS matches, plus CRLF (two of them back to
# back, which still collapses to ONE space -- the regex is greedy). Built
# with chr(), never pasted as the literal character, same discipline as
# FIX_GLUE_TRIM_STRIPPED/FIX_GLUE_TRIM_PRESERVED above. TAB/VT/FF added
# alongside the original six: DETAIL_BREAKS has always matched them --
# its own class is r"[\n\r\t\v\f\u0085\u2028\u2029]+" -- but this matrix
# did not exercise them until now.
DETAIL_BREAK_CASES = [
    ("LF", chr(0x0A)),
    ("CR", chr(0x0D)),
    ("CRLF", chr(0x0D) + chr(0x0A)),
    ("TAB", chr(0x09)),
    ("VT", chr(0x0B)),
    ("FF", chr(0x0C)),
    ("lsep_u2028", chr(0x2028)),
    ("psep_u2029", chr(0x2029)),
    ("nel_u0085", chr(0x85)),
]


@pytest.mark.parametrize("break_name,break_chars", DETAIL_BREAK_CASES, ids=[n for n, _ in DETAIL_BREAK_CASES])
def test_wait_timeout_detail_flattens_line_breaks_without_truncation(tmp_path, break_name, break_chars):
    """A short reply carrying one break character comes back single-line,
    with the break collapsed to exactly one ascii space -- and, being well
    under DETAIL_CAP, WITHOUT the " [...]" truncation marker. RED before
    #400 against a mutant that neuters DETAIL_BREAKS to an unmatchable
    regex (`/$^/g`): the raw break character survives verbatim into the
    detail, and the `break_chars not in detail` assertion below fails."""
    reply = "before the break" + break_chars + "after the break"
    res = run(
        tmp_path=tmp_path, segs=["seg01"],
        overrides={"wait:seg01": "PENDING seg01", "wait-recheck:seg01": reply},
    )
    assert res["ok"], res["stderr"]
    detail = res["out"]["result"]["failed"][0]["detail"]
    assert break_chars not in detail, f"raw break character survived flattening into: {detail!r}"
    assert detail == "before the break after the break"
    assert not detail.endswith(" [...]")


@pytest.mark.parametrize("break_name,break_chars", DETAIL_BREAK_CASES, ids=[n for n, _ in DETAIL_BREAK_CASES])
def test_wait_timeout_detail_truncates_long_replies_at_detail_cap(tmp_path, break_name, break_chars):
    """A reply well over DETAIL_CAP even after flattening comes back at
    EXACTLY DETAIL_CAP characters, ending with the " [...]" marker. RED
    before #400 against a mutant that drops replyDetail()'s truncation
    branch entirely (returning the flattened string unbounded): the detail
    comes back at its full flattened length (321 for the shipped
    DETAIL_CAP=160) instead of 160, and the `len(detail) == DETAIL_CAP`
    assertion below fails."""
    reply = "A" * DETAIL_CAP + break_chars + "B" * DETAIL_CAP
    res = run(
        tmp_path=tmp_path, segs=["seg01"],
        overrides={"wait:seg01": "PENDING seg01", "wait-recheck:seg01": reply},
    )
    assert res["ok"], res["stderr"]
    detail = res["out"]["result"]["failed"][0]["detail"]
    assert len(detail) == DETAIL_CAP, f"expected exactly {DETAIL_CAP} chars, got {len(detail)}: {detail!r}"
    assert detail.endswith(" [...]")
    assert detail.startswith("A" * (DETAIL_CAP - 6))


def test_failure_detail_tally_empty_on_a_fully_converged_run(tmp_path):
    """THE NEGATIVE CONTROL for the tally tests above -- NOT a revert-red
    case: a run with no failures at all logs no "Repeated failure detail"
    line and returns an empty failureDetailTally. This is what proves the
    tally tests above are asserting something real: a tally-emitting
    implementation that fired unconditionally would still pass every OTHER
    test in this file (none of them inspect logLines on a clean run). RED
    before #400 against a mutant that appends one unconditional tally log
    line right after the real (empty, correctly-guarded) loop: a "Repeated
    failure detail" line then appears even though nothing failed, and the
    assertion below fails."""
    out = _happy_run(tmp_path)
    assert out["result"]["failureDetailTally"] == []
    assert not any(line.startswith("Repeated failure detail") for line in out["logLines"])


# ---------------------------------------------------------------------------
# #400 follow-up (codex MAJOR) -- flattenDetail() is the single normalizer
# every STRING that reaches a detail goes through now, not just an agent's
# raw reply. Before this fix, only replyDetail()'s own argument was
# flattened/capped; three OTHER strings reached a detail verbatim: the
# schema-validated `mismatch_detail` on the artifact check, the relayed
# `error` on a ledger write and on the merge check, and sourcedDetail()'s own
# label was appended AFTER the cap rather than counted against it. A schema
# field is still model-authored text under no length or charset restriction,
# so an oversized, multiline mismatch_detail/error used to reach the
# operator log verbatim, falsifying the "one line, capped at DETAIL_CAP"
# property this file and the docs both promise. Every fixture below is
# deliberately BOTH oversized (over DETAIL_CAP even before the break
# collapses) AND line-break-carrying, so a fix that restored only one half
# would still show up; every test was watched RED against a /tmp mutant that
# restores the pre-fix verbatim path at its own site.
# ---------------------------------------------------------------------------

# Built from already-chr()-safe pieces (LF from above) rather than a pasted
# character. Deliberately not re-deriving flattenDetail()'s own transform in
# Python: the expected values below are sliced directly from the SAME raw
# strings the template flattens, using only "collapse one run of breaks to
# one space" and "cut at DETAIL_CAP-6 chars plus ' [...]'" -- confirmed
# against the real template's actual output, not assumed.
LONG_BREAK_MISMATCH_DETAIL = "expected sha1 " + "a" * 90 + LF + "got sha1 " + "b" * 90
_FLAT_MISMATCH_DETAIL = "expected sha1 " + "a" * 90 + " " + "got sha1 " + "b" * 90
EXPECTED_FLATTENED_MISMATCH_DETAIL = _FLAT_MISMATCH_DETAIL[: DETAIL_CAP - 6] + " [...]"

LONG_BREAK_LEDGER_ERROR = "ledger write failed: " + "x" * 90 + LF + "cause: " + "y" * 90
_FLAT_LEDGER_ERROR = "ledger write failed: " + "x" * 90 + " " + "cause: " + "y" * 90
EXPECTED_FLATTENED_LEDGER_ERROR = _FLAT_LEDGER_ERROR[: DETAIL_CAP - 6] + " [...]"


def test_review_artifact_mismatch_detail_is_flattened_and_capped_across_the_batch(tmp_path):
    """The artifact-check `mismatch_detail` is schema-validated shape, never
    content -- an oversized, multiline one used to reach a failed row (and
    the tally, and the log) verbatim. TWO segments share the identical
    oversized reply so the property is pinned through the tally and the log
    line too, reproducing where the reviewer actually found it. RED before
    this fix against a mutant that restores getVerifiedReview's old
    `typeof retry.art.mismatch_detail === "string" ? retry.art.
    mismatch_detail : replyDetail(retry.art)` (mismatch_detail relayed
    verbatim, no flattenDetail() call at all): the detail then carries the
    raw LF and its full 203-character length, and the equality assertion
    below fails."""
    overrides = {}
    for seg in ("seg01", "seg02"):
        for suffix in ("r1", "r1:retry"):
            overrides[f"artifact-check:{seg}:{suffix}"] = {
                "match": False, "mismatch_detail": LONG_BREAK_MISMATCH_DETAIL,
            }
    res = run(tmp_path=tmp_path, segs=["seg01", "seg02"], overrides=overrides)
    assert res["ok"], res["stderr"]
    out = res["out"]
    assert "\n" not in EXPECTED_FLATTENED_MISMATCH_DETAIL and len(EXPECTED_FLATTENED_MISMATCH_DETAIL) <= DETAIL_CAP
    assert out["result"]["failed"] == [
        {
            "seg": "seg01", "converged": False, "reason": "review-artifact-mismatch", "rounds": 1,
            "detail": EXPECTED_FLATTENED_MISMATCH_DETAIL,
        },
        {
            "seg": "seg02", "converged": False, "reason": "review-artifact-mismatch", "rounds": 1,
            "detail": EXPECTED_FLATTENED_MISMATCH_DETAIL,
        },
    ]
    assert out["result"]["failureDetailTally"] == [
        {"detail": EXPECTED_FLATTENED_MISMATCH_DETAIL, "count": 2}
    ]
    assert f"Repeated failure detail (2/2 failed): {EXPECTED_FLATTENED_MISMATCH_DETAIL}" in out["logLines"]
    assert all("\n" not in line for line in out["logLines"]), (
        f"a raw line break survived into the operator log: {out['logLines']}"
    )


def test_ledger_write_error_detail_is_flattened_and_capped_across_the_batch(tmp_path):
    """The ledger-write site's twin of the test above: a relayed script
    `error` is still arbitrary text, not a validated constant. RED before
    this fix against a mutant that drops the `flattenDetail(raw.error)` call
    in recordLedgerCall (reverted to bare `raw.error`): the detail carries
    the raw LF and its full 209-character length, and the equality assertion
    below fails."""
    overrides = {}
    for seg in ("seg01", "seg02"):
        overrides[f"ledger:converged:{seg}"] = {"success": False, "error": LONG_BREAK_LEDGER_ERROR}
    res = run(tmp_path=tmp_path, segs=["seg01", "seg02"], overrides=overrides)
    assert res["ok"], res["stderr"]
    out = res["out"]
    assert out["result"]["failed"] == [
        {
            "seg": "seg01", "converged": False, "reason": "ledger-write-failed",
            "detail": EXPECTED_FLATTENED_LEDGER_ERROR,
        },
        {
            "seg": "seg02", "converged": False, "reason": "ledger-write-failed",
            "detail": EXPECTED_FLATTENED_LEDGER_ERROR,
        },
    ]
    assert out["result"]["failureDetailTally"] == [
        {"detail": EXPECTED_FLATTENED_LEDGER_ERROR, "count": 2}
    ]
    assert f"Repeated failure detail (2/2 failed): {EXPECTED_FLATTENED_LEDGER_ERROR}" in out["logLines"]


def test_ledger_merge_error_detail_is_flattened_and_capped(tmp_path):
    """The merge-ledger twin -- a single batch-level call (one merge-ledger
    check per run, never per-segment), so no tally/log angle here, just the
    top-level `detail` field. RED before this fix against a mutant that
    drops the `flattenDetail(mergeResult.error)` call at the batch-final
    completeness check (reverted to bare `mergeResult.error`): the detail
    carries the raw LF and its full 209-character length, and the equality
    assertion below fails."""
    res = run(tmp_path=tmp_path, segs=["seg01"], overrides={
        "merge-ledger": {"success": False, "error": LONG_BREAK_LEDGER_ERROR},
    })
    assert res["ok"], res["stderr"]
    out = res["out"]
    assert out["result"]["batchComplete"] is False
    assert out["result"]["reason"] == "ledger-merge-failed"
    assert out["result"]["detail"] == EXPECTED_FLATTENED_LEDGER_ERROR


# Round 2 of the same #400 review: a schema-accepted but WHITESPACE-ONLY
# `error` string flattens to "" (DETAIL_BREAKS only matches break characters,
# never plain spaces, but a break collapses to one space and the surrounding
# spaces then vanish under flattenDetail()'s own trim()) -- which used to
# become detail:"" and form an empty-string bucket in the batch tally. Both
# ledger sites now compute the flattened relayed error first and fall back to
# the existing WRITE_FAILED_DEFAULT_DETAIL/MERGE_FAILED_DEFAULT_DETAIL
# constant when it comes back empty, the same non-empty guard the
# artifact-check site already carried. Built with chr(), never pasted glyphs:
# space, tab, LF, tab, space -- whitespace-only, and the tab-LF-tab run is
# one contiguous break-character sequence, not merely spaces.
WHITESPACE_ONLY_ERROR = chr(0x20) + chr(0x09) + chr(0x0A) + chr(0x09) + chr(0x20)


def test_ledger_write_whitespace_only_error_falls_back_to_the_constant(tmp_path):
    """TWO segments share the identical whitespace-only error so the "no
    empty-string bucket" property is pinned through the tally, not just the
    per-row detail. RED before this fix against a mutant that drops the
    `relayed !== ""` guard in recordLedgerCall (reverted to using
    `flattenDetail(raw.error)` directly, unconditionally): the detail comes
    back as "" on both rows, and failureDetailTally reports a bucket of
    {"detail": "", "count": 2} instead of the constant, failing the
    assertions below."""
    overrides = {}
    for seg in ("seg01", "seg02"):
        overrides[f"ledger:converged:{seg}"] = {"success": False, "error": WHITESPACE_ONLY_ERROR}
    res = run(tmp_path=tmp_path, segs=["seg01", "seg02"], overrides=overrides)
    assert res["ok"], res["stderr"]
    out = res["out"]
    assert out["result"]["failed"] == [
        {"seg": "seg01", "converged": False, "reason": "ledger-write-failed", "detail": WRITE_FAILED_DEFAULT_DETAIL},
        {"seg": "seg02", "converged": False, "reason": "ledger-write-failed", "detail": WRITE_FAILED_DEFAULT_DETAIL},
    ]
    assert out["result"]["failureDetailTally"] == [{"detail": WRITE_FAILED_DEFAULT_DETAIL, "count": 2}]


def test_ledger_merge_whitespace_only_error_falls_back_to_the_constant(tmp_path):
    """The merge-ledger twin -- single batch-level call, so just the
    top-level `detail` field. RED before this fix against a mutant that
    drops the equivalent `relayedMergeError !== ""` guard: detail comes back
    as "" instead of MERGE_FAILED_DEFAULT_DETAIL, failing the assertion
    below."""
    res = run(tmp_path=tmp_path, segs=["seg01"], overrides={
        "merge-ledger": {"success": False, "error": WHITESPACE_ONLY_ERROR},
    })
    assert res["ok"], res["stderr"]
    out = res["out"]
    assert out["result"]["batchComplete"] is False
    assert out["result"]["reason"] == "ledger-merge-failed"
    assert out["result"]["detail"] == MERGE_FAILED_DEFAULT_DETAIL


# A fix reply long enough that replyDetail() alone already caps it at
# DETAIL_CAP (160): naively appending "fix call: " (10 chars) on top would
# land at 170, over budget. Confirmed against the real template: the
# combined "fix call: " + replyDetail(fx) is exactly 170 chars before
# sourcedDetail()'s own re-flatten, and exactly DETAIL_CAP after it.
LONG_FIX_REPLY_OVER_CAP = "A" * 150 + " DRAFT_MISSING seg01 " + "B" * 150


def test_sourced_detail_keeps_the_source_label_inside_the_cap(tmp_path):
    """sourcedDetail() re-flattens its own "source: " + replyDetail(reply)
    concatenation, so the label counts against DETAIL_CAP rather than being
    appended past it. RED before this fix against a mutant that reverts
    sourcedDetail() to `return source + ": " + replyDetail(reply);` (no
    re-flatten): the detail comes back at 170 characters instead of 160, and
    the `len(detail) == DETAIL_CAP` assertion below fails."""
    res = run(
        tmp_path=tmp_path, segs=["seg01"],
        overrides={
            "review-read:seg01:r1": _non_clean_review(),
            "artifact-check:seg01:r1": {"match": True},
            "fix:seg01:r1": LONG_FIX_REPLY_OVER_CAP,
            "draft-probe:seg01": {"present": True},
        },
    )
    assert res["ok"], res["stderr"]
    detail = res["out"]["result"]["failed"][0]["detail"]
    assert len(detail) == DETAIL_CAP, f"expected exactly {DETAIL_CAP} chars, got {len(detail)}: {detail!r}"
    assert detail.endswith(" [...]")
    assert detail.startswith("fix call: " + "A" * (DETAIL_CAP - 6 - len("fix call: ")))


# Existing exact-detail pins for SHORT errors are unaffected by
# flattenDetail() -- confirmed by re-running the whole file, not asserted
# again here (duplicating them would just be a second copy to drift):
# test_ledger_write_still_rejects_success_false ("boom") and the
# "nonempty-error" case of test_ledger_write_still_rejects_real_failure_evidence
# / test_ledger_merge_still_rejects_real_failure_evidence
# ("runs/ledger.d is not writable" / "fragment dir missing") all still pass
# untouched: a short, single-line string is already <= DETAIL_CAP and has
# nothing for DETAIL_BREAKS to collapse, so flattenDetail() is a no-op on it.


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
