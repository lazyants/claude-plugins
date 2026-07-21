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


def instantiate(*, max_fix_rounds: int, batch_agent_cap: int) -> str:
    """The exact one-time substitution the template's header documents
    (duplicated, not imported, so this file stays self-contained like every
    sibling)."""
    text = MASS_TRANSLATE_TEMPLATE.read_text(encoding="utf-8")
    text = text.replace("{{DURABLE_ROOT}}", FIXTURE_DURABLE_ROOT)
    text = text.replace("{{RUN_ID}}", FIXTURE_RUN_ID)
    text = text.replace("{{SOURCE_LANG}}", FIXTURE_SOURCE_LANG)
    text = text.replace("{{TARGET_LANG}}", FIXTURE_TARGET_LANG)
    text = text.replace("{{MAX_FIX_ROUNDS}}", str(int(max_fix_rounds)))
    text = text.replace("{{BATCH_AGENT_CAP}}", str(int(batch_agent_cap)))
    text = text.replace("{{VERSE_POLICY_INSTRUCTION_BLOCK}}", json.dumps(FIXTURE_VERSE_POLICY)[1:-1])
    text = text.replace("{{CODEX_COMPANION_PATH_JSON}}", json.dumps(FIXTURE_COMPANION_PATH))
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
  if (label.indexOf("wait:") === 0) return "READY " + seg;
  if (label.indexOf("review-dispatch:") === 0) return DRIVE_RETURNS.review !== null ? DRIVE_RETURNS.review : ("DISPATCHED " + seg + " beef1234");
  if (label.indexOf("review-wait:") === 0) return "READY " + seg;
  if (label.indexOf("review-read:") === 0) return { clean: true, coverage_ok: true, findings: [], draft_sha1: "a" };
  if (label.indexOf("artifact-check:") === 0) return { match: true };
  if (label.indexOf("fix:") === 0) return "FIXED " + seg;
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
        drive_returns: dict | None = None, overrides: dict | None = None, timeout: int = 30) -> dict:
    """Returns {ok, out, stderr}. ok=False (with stderr) when the template
    threw before producing stdout (the SEGS-guard throw path)."""
    drive_returns = drive_returns or {}
    dr = {"translate": drive_returns.get("translate"), "review": drive_returns.get("review")}
    src = instantiate(max_fix_rounds=max_fix_rounds, batch_agent_cap=batch_agent_cap)
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

    # the task-file path carries the runtime DISP.
    assert f'TASKFILE="{FIXTURE_DURABLE_ROOT}/segments/.codex_task.{kind}.seg01.$DISP"' in prompt


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
    poll = extract_poll(out["promptByLabel"][wait_label])

    # (e) ACCEPT runs the full canonical gate directly; no external timeout.
    for s in accept_scripts:
        assert s in poll, f"wait ACCEPT must invoke {s}"
    assert "--expect-token" in poll
    assert "timeout" not in poll and "gtimeout" not in poll, "no external timeout binary"

    # (e) FAIL-FAST: DISP-named sentinel presence check, keyed on the captured
    # DISP, evaluated ONLY AFTER the ACCEPT `exit 0`.
    sentinel = f'[ -f "{FIXTURE_DURABLE_ROOT}/segments/.codex_failed.seg01.{disp}" ] && exit 1'
    assert sentinel in poll, "fail-fast must be a DISP-named sentinel presence check"
    assert poll.index("exit 0") < poll.index(".codex_failed."), (
        "fail-fast must be evaluated AFTER the ACCEPT gate (a valid canonical wins)"
    )

    # (f) elapsed bound = 3450 (>= the 2700 deadline), gate-then-deadline-break,
    # NO separate post-loop gate.
    assert "end=$((SECONDS + 3450))" in poll, "bound = DEADLINE(2700)+FINALIZE(150)+GRACE(600)=3450"
    assert "[ $SECONDS -ge $end ] && break" in poll
    tail = poll.rsplit("done;", 1)[1]
    assert tail.strip() == "exit 1", f"no separate post-loop gate -- tail after done must be `exit 1`, got: {tail!r}"
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
    assert '.codex_failed.seg01.abcDEF01" ] && exit 1' in poll


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


def test_translate_wait_substring_collision_reports_timeout(tmp_path):
    """RED before the #228 exact-match fix at site E (reviewFixLoop's
    "wait:" + seg): the OLD `ready.indexOf("READY") === -1` check falsely
    treated a TIMEOUT reply that merely contains the literal substring
    "READY" inside its own explanatory prose (e.g. "TIMEOUT seg01 (not
    READY)") as ready -- `indexOf` finds "READY" so the negated `=== -1`
    check was false. This is the worst of the five #228 sites: a false pass
    here sends the entire review/fix cycle over a draft that never actually
    finished translating, and no recoverable signal is ever recorded to
    pick it back up."""
    res = run(tmp_path=tmp_path, segs=["seg01"], overrides={"wait:seg01": "TIMEOUT seg01 (not READY)"})
    assert res["ok"], res["stderr"]
    out = res["out"]
    assert out["result"]["failed"] == [{"seg": "seg01", "converged": False, "reason": "translate-timeout"}]
    assert out["result"]["converged"] == []
    labels = [c["label"] for c in out["calls"]]
    # A substring-collision bug proceeds straight into the review/fix cycle
    # on an unfinished draft instead of stopping at the wait.
    assert "review-dispatch:seg01:r1" not in labels
    assert "review-wait:seg01:r1" not in labels


def test_review_wait_substring_collision_reports_review_timeout(tmp_path):
    """RED before the #228 exact-match fix at site C (getVerifiedReview's
    "review-wait:" + seg + ":r" + roundLabel): the OLD
    `ready.indexOf("READY") === -1` check falsely treated a TIMEOUT reply
    containing the literal substring "READY" as ready, letting the code go
    on to read a review artifact that review_ready.py never actually
    confirmed."""
    res = run(tmp_path=tmp_path, segs=["seg01"], overrides={"review-wait:seg01:r1": "TIMEOUT seg01 (not READY)"})
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


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
