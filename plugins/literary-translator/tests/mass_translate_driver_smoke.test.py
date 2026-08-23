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
from pathlib import Path

import pytest

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
FIXTURE_SOURCE_LANG = "fr"
FIXTURE_TARGET_LANG = "ru"
FIXTURE_VERSE_POLICY = "Render every verse literally, line by line."
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
    """The exact one-time substitution the template's header documents
    (duplicated, not imported, so this file stays self-contained like every
    sibling). #409 stage 0 -- max_codex_jobs_per_batch defaults generously
    (matching batch_agent_cap's own default below), same reasoning: this
    file's smoke tests exercise the driver/convergence machinery, not either
    preflight gate, so the new gate must never trip here."""
    text = MASS_TRANSLATE_TEMPLATE.read_text(encoding="utf-8")
    text = text.replace("{{DURABLE_ROOT}}", FIXTURE_DURABLE_ROOT)
    text = text.replace("{{RUN_ID}}", FIXTURE_RUN_ID)
    text = text.replace("{{SOURCE_LANG}}", FIXTURE_SOURCE_LANG)
    text = text.replace("{{TARGET_LANG}}", FIXTURE_TARGET_LANG)
    text = text.replace("{{MAX_FIX_ROUNDS}}", str(int(max_fix_rounds)))
    text = text.replace("{{BATCH_AGENT_CAP}}", str(int(batch_agent_cap)))
    text = text.replace("{{MAX_CODEX_JOBS_PER_BATCH}}", str(int(max_codex_jobs_per_batch)))
    text = text.replace("{{VERSE_POLICY_INSTRUCTION_BLOCK}}", json.dumps(FIXTURE_VERSE_POLICY)[1:-1])
    text = text.replace("{{CODEX_COMPANION_PATH_JSON}}", json.dumps(FIXTURE_COMPANION_PATH))
    text = text.replace("{{EFFORT}}", effort)
    text = text.replace("{{MODEL}}", model)
    # #412/#607 -- PLUGIN_ROOT: same json.dumps JS string literal contract as
    # CODEX_COMPANION_PATH_JSON above (token sits OUTSIDE quotes in
    # `const PLUGIN_ROOT = {{PLUGIN_ROOT}};`). Empty used to be the documented
    # "not opted into the redirect" sentinel; since #607 it is a REFUSAL --
    # the fix-scope audit has no trusted copy to run without a plugin root --
    # so this fixture's default is a real path.
    text = text.replace("{{PLUGIN_ROOT}}", json.dumps(plugin_root))
    assert "{{" not in text, "fixture instantiation left an unresolved token"
    return text


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
  if (label.indexOf("fix-scope:") === 0) return { ok: true, n_checked: 79 };
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
function log() {}

(async () => {
  try {
    const result = await __workflowMain__(agent, pipeline, log, SEGS_ARGS);
    process.stdout.write(JSON.stringify({ result: result, calls: callsLog, promptByLabel: promptByLabel, pipelineCalled: pipelineCalled }));
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
    assert out["result"]["failed"] == [{"seg": "seg01", "converged": False, "reason": "translate-timeout"}]
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
    assert out["result"]["failed"] == [
        {"seg": "seg01", "converged": False, "reason": "review-timeout", "rounds": 1}
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
    assert out["result"]["failed"] == [
        {"seg": "seg01", "converged": False, "reason": "fix-call-failed", "rounds": 1}
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
    assert out["result"]["failed"] == [
        {"seg": "seg01", "converged": False, "reason": "review-timeout", "rounds": 1}
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
    assert out["result"]["failed"] == [{"seg": "seg01", "converged": False, "reason": "translate-timeout"}]
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
    assert out["result"]["failed"] == [
        {"seg": "seg01", "converged": False, "reason": "fix-call-failed", "rounds": 1}
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


def _assert_report_reached_the_draft_probe(out: dict, shape_desc: str) -> None:
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
    assert out["result"]["failed"] == [
        {"seg": "seg01", "converged": False, "reason": "fix-call-failed", "rounds": 1}
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
        out, f"prose on the sentinel's own line, glued by {glue_name}"
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
        out, f"the sentinel alone on its line behind {glue_name}, which trim() does not strip"
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
    assert out["result"]["failed"] == [
        {"seg": "seg01", "converged": False, "reason": "review-timeout", "rounds": 1}
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
    assert out["result"]["failed"] == [{"seg": "seg01", "converged": False, "reason": "translate-timeout"}]
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
    "art",
    [
        pytest.param({"match": True, "mismatch_detail": "expected sha1 a.. got b.."}, id="real-mismatch-detail"),
        pytest.param({"match": True, "mismatch_detail": None}, id="wrong-typed-mismatch-detail"),
        pytest.param({"match": False, "mismatch_detail": "artifact differs"}, id="honest-mismatch"),
        pytest.param({"match": True, "verified": True}, id="undeclared-key"),
    ],
)
def test_artifact_check_still_rejects(tmp_path, art):
    """The anti-false-green half at the third site. A real mismatch_detail is
    still fatal even next to match:true; an unreadable (wrong-typed) one
    fails closed; an honest match:false is unchanged; and a key
    REVIEW_ARTIFACT_SCHEMA never declared is now rejected too -- the guard
    gained the allowed-key check its two ledger siblings always had."""
    res = run(tmp_path=tmp_path, segs=["seg01"], overrides=_artifact_overrides(art))
    assert res["ok"], res["stderr"]
    out = res["out"]
    assert out["result"]["converged"] == []
    assert out["result"]["failed"] == [
        {"seg": "seg01", "converged": False, "reason": "review-artifact-mismatch", "rounds": 1}
    ]


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
