"""tests/max_codex_jobs_per_batch_preflight.test.py

Targets: the NEW `max_codex_jobs_per_batch` preflight inside
`mass-translate-wf.template.js` (#409 stage 0, issue #402) -- the
"max_codex_jobs_per_batch preflight" block, sitting directly above the
pre-existing "batch_agent_cap preflight" block this file does NOT touch or
re-test (see `tests/batch_size_estimator.test.py`, which already owns that
one exhaustively).

This is a SECOND, INDEPENDENT preflight gate. `batch_agent_cap` estimates
Workflow `agent()` calls; this gate estimates the resource an operator
actually spends -- real codex dispatches (one detached `codex_job.py`
launch per translate, per review, and per fix round). Both gates run,
in sequence, before `pipeline()` is ever called; either one tripping
refuses the batch before any work starts.

Formula (derived by enumerating this template's ACTUAL codex_job.py launch
sites, not from any plan/brief and not from the template's prose comment --
an earlier version of this file re-derived the arithmetic "independently"
and still reproduced the template's own error, because both counted the
same wrong thing):

  Per segment, worst case (every round non-clean, so every fix round
  actually fires):
    1 translate job
  + (max_fix_rounds + 1) review jobs   -- one per normal round, plus the
                                           one mandatory final confirming
                                           review (runRound's isFinal=true
                                           call, which never dispatches a
                                           fix regardless of its verdict)
  = 1 + (max_fix_rounds + 1) = max_fix_rounds + 2.

  The max_fix_rounds fix rounds are NOT codex jobs: callFix() is a plain
  Workflow agent() call (the Claude fix step). The template has exactly two
  codex_job.py launch sites -- the dispatch shells in translateDrivePrompt
  and reviewDrivePrompt -- and a review round's retry path (readAndCheck)
  re-reads the artifact codex already wrote rather than starting a second
  job, so no round can launch more than one.

  At max_fix_rounds=3: 3+2 = 5 jobs/segment. At the shipped
  max_fix_rounds=4 (profile.example.yml): 4+2 = 6 jobs/segment.

This file does NOT reimplement or trust its own reimplementation of that
arithmetic -- like its sibling `batch_size_estimator.test.py`, it extracts
the REAL, substituted template source and drives it with Node.js under a
mock `agent()`/`pipeline()`, then asserts against the same closed form
independently, so a drift between the template's real formula and this
file's expectation surfaces as a test failure rather than an agreeing
duplicate. Skipped entirely if Node.js is not on PATH (no hard Node.js
dependency for this plugin otherwise, matching every sibling test file's
own skip policy).

Deliberately lighter-weight than `batch_size_estimator.test.py`: this file
never needs to drive a segment to actual convergence (translate/review/fix
round-tripping), because the preflight gate this file targets returns
BEFORE `pipeline()` is ever called on a refusal, and on a PERMITTED batch
this file only needs to prove `pipeline()` actually ran -- not that every
segment converged. So the mock `pipeline()` here is a no-op stub (records
that it was called, returns `[]` immediately, never invokes stage1/stage2)
and the mock `agent()` only needs to answer the one unconditional
post-pipeline call every real run makes regardless of `results` content:
`merge-ledger` (mergeLedgerPrompt). This is a real simplification a fixture
author must knowingly choose, not an oversight: it is exactly why this file
does not attempt to duplicate `batch_size_estimator.test.py`'s much larger
per-segment queue-driven harness.

Fixtures:
  1. `test_refusal_fires_before_any_work_and_names_all_four_things` -- the
     RED case. A batch whose `estimatedCodexJobs` is one over
     `max_codex_jobs_per_batch`: the gate must trip, `pipeline()` must never
     run, zero real `agent()` calls happen, and the logged refusal message
     names all four required things (the knob, the computed need, the
     effective limit, the segment count) -- issue #402's own complaint.
     Also locks the MESSAGE-HONESTY property with an exact-string match: the
     substituted cap value is byte-identical whether it came from an
     explicit profile.yml setting or the schema's own documented default
     (the template tracks no provenance for it, deliberately -- "effective
     ... limit" phrasing, never a claim that the operator set anything), so
     the message stays true under either origin.
  2. `test_boundary_exactly_at_cap_permits_dispatch` -- `estimatedCodexJobs
     == max_codex_jobs_per_batch` must NOT trip the gate (the check is `>`,
     not `>=`); `pipeline()` must actually run and the batch completes.
  3. `test_legitimate_small_batch_is_never_refused` -- HARD CONSTRAINT 2: a
     gate that refuses everything would pass a bare "does it refuse?" test
     just as well as a correct one, so this fixture proves the OPPOSITE
     property on a batch comfortably under the cap.
  4. `test_estimated_codex_jobs_matches_closed_form_across_cases` -- a
     parametrized-by-hand (not `pytest.mark.parametrize`, so an empty case
     list cannot silently vanish into zero collected tests -- the loop
     itself asserts `len(CASES) > 0` before iterating), cheap check (no
     `pipeline()` execution -- the gate trips before it, `max_codex_jobs_
     per_batch` pinned to 1 so every case refuses) that the real script's
     own `estimatedCodexJobs` equals the closed form `N * (maxFixRounds +
     2)` across several `(segment_count, max_fix_rounds)` pairs, including
     the max_fix_rounds=3 -> 5 jobs/segment case.
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
    "the workflow template's real preflight logic (no hard Node.js dependency "
    "for this plugin otherwise)",
)

FIXTURE_DURABLE_ROOT = "/fixture/project/durable_root"
FIXTURE_SOURCE_LANG = "fr"
FIXTURE_TARGET_LANG = "ru"
FIXTURE_VERSE_POLICY_INSTRUCTION_BLOCK = "Render every verse literally, line by line."


def instantiate_mass_translate(
    *,
    max_fix_rounds: int,
    batch_agent_cap: int,
    max_codex_jobs_per_batch: int,
    durable_root: str = FIXTURE_DURABLE_ROOT,
    source_lang: str = FIXTURE_SOURCE_LANG,
    target_lang: str = FIXTURE_TARGET_LANG,
    verse_policy_instruction_block: str = FIXTURE_VERSE_POLICY_INSTRUCTION_BLOCK,
) -> str:
    """Re-implements the exact one-time substitution contract the template's
    own header comment documents (same contract every sibling test file's
    own `instantiate_mass_translate` implements -- duplicated here, not
    imported, so this file stays self-contained like every other file in
    this directory). Deliberately does NOT substitute {{RUN_ID}} -- this
    file's mock never inspects prompt text (only opts.label), so RUN_ID's
    exact value is irrelevant here and is left unresolved on purpose."""
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
    text = text.replace("{{CODEX_COMPANION_PATH_JSON}}", json.dumps("/fixture/codex/codex-companion.mjs"))
    text = text.replace("{{EFFORT}}", "high")
    text = text.replace("{{MODEL}}", "")
    assert "{{" not in text, "fixture instantiation left an unresolved token -- fix the fixture, not the assertion below"
    return text


def _wrap_for_execution(js_source: str) -> str:
    """Same wrapping contract as `batch_size_estimator.test.py`'s own
    helper: the raw file is not valid standalone JS (it both `export`s
    `meta` and `return`s at its own top level), so it must be wrapped in an
    async function whose parameters ARE the `agent`/`pipeline`/`log`/`args`
    globals the Workflow tool supplies."""
    assert js_source.count("export const meta") == 1, (
        "expected exactly one 'export const meta' declaration to strip -- "
        "the template's export contract may have changed"
    )
    body = js_source.replace("export const meta", "const meta", 1)
    return "async function __workflowMain__(agent, pipeline, log, args) {\n" + body + "\n}\n"


# ---------------------------------------------------------------------------
# Minimal harness: a NO-OP pipeline() (records that it ran, returns []
# immediately -- never drives translateStage/reviewFixLoop), and an agent()
# mock that answers only the one unconditional post-pipeline call every real
# run makes regardless of results content: "merge-ledger". Deliberately does
# NOT reimplement the per-segment queue machinery batch_size_estimator.
# test.py's harness needs -- this file's gate returns before pipeline() on
# a refusal, and never needs a segment to actually converge on a permit.
# ---------------------------------------------------------------------------
HARNESS_TEMPLATE = r"""
'use strict';

__WRAPPED_SOURCE__

const SEGS_ARGS = __SEGS_JSON__;
const callsLog = [];
const logLines = [];
let pipelineCalled = false;

async function agent(promptText, opts) {
  opts = opts || {};
  const label = opts.label || "";
  callsLog.push({ label: label });
  if (label === "merge-ledger") {
    return {
      success: true,
      ledger_path: "/fixture/ledger.json",
      n_segments: SEGS_ARGS.length,
      missing_segments: [],
      stale_segments: [],
    };
  }
  throw new Error("mock agent(): unexpected call in a no-op-pipeline harness, label=" + label);
}

async function pipeline(items, stage1, stage2) {
  pipelineCalled = true;
  return [];
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


def build_harness(js_source: str, segs: list[str]) -> str:
    wrapped = _wrap_for_execution(js_source)
    text = HARNESS_TEMPLATE.replace("__WRAPPED_SOURCE__", wrapped)
    text = text.replace("__SEGS_JSON__", json.dumps(segs))
    return text


def run_workflow(
    *,
    tmp_path: Path,
    max_fix_rounds: int,
    batch_agent_cap: int,
    max_codex_jobs_per_batch: int,
    segs: list[str],
    timeout: int = 30,
) -> dict:
    assert NODE is not None, "node executable not found on PATH -- required to run this test file"
    js_source = instantiate_mass_translate(
        max_fix_rounds=max_fix_rounds,
        batch_agent_cap=batch_agent_cap,
        max_codex_jobs_per_batch=max_codex_jobs_per_batch,
    )
    harness_text = build_harness(js_source, segs)
    tmp_path.mkdir(parents=True, exist_ok=True)
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


def _segs(n: int) -> list[str]:
    return [f"seg{i:02d}" for i in range(1, n + 1)]


# A batch_agent_cap value generous enough that the PRE-EXISTING agent-call
# preflight (tests/batch_size_estimator.test.py's own gate, sitting right
# below this file's gate in the template) never trips in any fixture below
# -- every fixture here is sized to test ONLY the max_codex_jobs_per_batch
# gate in isolation, never the older one.
GENEROUS_BATCH_AGENT_CAP = 10_000_000


# ---------------------------------------------------------------------------
# 1: RED -- the refusal fires before any work, and names all four things.
# ---------------------------------------------------------------------------


def test_refusal_fires_before_any_work_and_names_all_four_things(tmp_path):
    max_fix_rounds = 3
    segs = _segs(5)
    # 3 + 2 = 5 jobs/segment * 5 = 25. Only the 1 translate and the
    # (max_fix_rounds + 1) reviews launch codex_job.py; the fix rounds are
    # plain Claude agent() calls and are deliberately not counted.
    codex_jobs_per_seg = max_fix_rounds + 2
    assert codex_jobs_per_seg == 5, "sanity: max_fix_rounds=3 must cost 5 jobs/segment"
    estimated = len(segs) * codex_jobs_per_seg
    cap = estimated - 1  # one below the true need -> must refuse

    out = run_workflow(
        tmp_path=tmp_path,
        max_fix_rounds=max_fix_rounds,
        batch_agent_cap=GENEROUS_BATCH_AGENT_CAP,
        max_codex_jobs_per_batch=cap,
        segs=segs,
    )

    assert out["pipelineCalled"] is False, "pipeline() must never run once the codex-job budget is judged too large"
    assert out["calls"] == [], "zero real agent() calls once the gate trips -- it must return before any dispatch"

    result = out["result"]
    assert result == {
        "converged": [], "failed": [], "reason": "batch-too-large-codex-jobs",
        "estimatedCodexJobs": estimated, "codexJobsCap": cap,
    }

    # The refusal message must name all four things issue #402 complained
    # today's message does not: the knob, the computed need, the effective
    # limit, and the segment count.
    log_text = "\n".join(out["log"])
    assert "engine.max_codex_jobs_per_batch" in log_text, "refusal must name the KNOB"
    assert f"limit of {cap}" in log_text, "refusal must name the EFFECTIVE LIMIT"
    assert f"estimatedCodexJobs={estimated}" in log_text, "refusal must name the COMPUTED NEED"
    assert f"{len(segs)} segment(s)" in log_text, "refusal must name the SEGMENT COUNT"

    # MESSAGE-HONESTY property (team lead review requirement): the value
    # substituted into MAX_CODEX_JOBS_PER_BATCH is byte-identical whether it
    # came from an explicit profile.yml setting or from the schema's own
    # documented default -- this harness has no way to tell the two apart,
    # by design, because the template no longer tracks or claims a source at
    # all. So the message must describe the number as the EFFECTIVE limit
    # (a fact the template can always compute) and never assert it was "set"
    # by the operator (a fact the template cannot know). Exact string match
    # locks this in precisely, rather than merely checking for a substring
    # that a future edit could satisfy while still smuggling in a false
    # claim elsewhere in the sentence. This is a STRONGER fix than a
    # provenance-aware wording would be: it removes the ABILITY to make a
    # wrong claim, not just the claim itself, so it survives future edits
    # that a "be careful with the wording" fix would not.
    assert log_text == (
        f"Batch too large: this batch needs estimatedCodexJobs={estimated} for {len(segs)} "
        f"segment(s) at max_fix_rounds={max_fix_rounds}, over the effective "
        f"engine.max_codex_jobs_per_batch limit of {cap}. Raise it in profile.yml under "
        f"engine: to allow a larger batch."
    )


# ---------------------------------------------------------------------------
# 2: the boundary itself -- `estimatedCodexJobs > MAX_CODEX_JOBS_PER_BATCH`,
# not `>=`.
# ---------------------------------------------------------------------------


def test_boundary_exactly_at_cap_permits_dispatch(tmp_path):
    max_fix_rounds = 3
    segs = _segs(5)
    estimated = len(segs) * (max_fix_rounds + 2)  # 25

    out = run_workflow(
        tmp_path=tmp_path,
        max_fix_rounds=max_fix_rounds,
        batch_agent_cap=GENEROUS_BATCH_AGENT_CAP,
        max_codex_jobs_per_batch=estimated,  # exactly at the boundary
        segs=segs,
    )

    assert out["pipelineCalled"] is True, "estimatedCodexJobs == cap must NOT trip the gate (the check is '>', not '>=')"
    result = out["result"]
    assert result["batchComplete"] is True
    assert result["converged"] == []
    assert result["failed"] == []


# ---------------------------------------------------------------------------
# 3: HARD CONSTRAINT 2 -- prove the refusal does NOT fire on a legitimate
# batch. A gate that refuses everything passes a bare "does it refuse?"
# test just as well as a correct one; this fixture is the other half.
# ---------------------------------------------------------------------------


def test_legitimate_small_batch_is_never_refused(tmp_path):
    max_fix_rounds = 4  # profile.example.yml's own shipped default
    segs = _segs(2)
    # 4 + 2 = 6 jobs/segment * 2 = 12, comfortably under any real cap
    # (profile.example.yml's own shipped default of 400).
    estimated = len(segs) * (max_fix_rounds + 2)
    assert estimated == 12

    out = run_workflow(
        tmp_path=tmp_path,
        max_fix_rounds=max_fix_rounds,
        batch_agent_cap=GENEROUS_BATCH_AGENT_CAP,
        max_codex_jobs_per_batch=400,  # profile.example.yml's shipped default
        segs=segs,
    )

    assert out["pipelineCalled"] is True, "a batch comfortably under the cap must be permitted to dispatch"
    # A successful result carries no "reason" key at all (see the template's
    # own final `return { converged, failed, batchComplete: true, ... }` --
    # "reason" only appears on a refusal/failure branch), so `batchComplete
    # is True` alone is the stronger, correct proof of "never refused" here;
    # indexing a "reason" key that a success legitimately omits would raise
    # KeyError rather than prove anything.
    assert out["result"].get("reason") != "batch-too-large-codex-jobs"
    assert out["result"]["batchComplete"] is True


# ---------------------------------------------------------------------------
# 4: the closed form itself, across several (segment_count, max_fix_rounds)
# pairs -- cheap (max_codex_jobs_per_batch pinned to 1, so every case
# refuses before pipeline() ever runs). Hand-looped, not
# pytest.mark.parametrize, so an accidentally-empty case list cannot vanish
# into zero collected tests without this file noticing (NO SILENT ZERO).
# ---------------------------------------------------------------------------


def test_estimated_codex_jobs_matches_closed_form_across_cases(tmp_path):
    CASES = [
        (1, 1),   # 1 * (2*1+2) = 4
        (3, 2),   # 3 * (2*2+2) = 18
        (5, 3),   # 5 * (2*3+2) = 40 -- the brief's own worked example
        (10, 4),  # 10 * (2*4+2) = 100
        (40, 4),  # 40 * (2*4+2) = 400 -- profile.example.yml's own documented ceiling
    ]
    assert len(CASES) > 0, "CASES must not be silently empty -- this test would otherwise vacuously pass"

    checked = 0
    for segment_count, max_fix_rounds in CASES:
        segs = _segs(segment_count)
        expected = segment_count * (max_fix_rounds + 2)

        out = run_workflow(
            tmp_path=tmp_path / f"case-{segment_count}-{max_fix_rounds}",
            max_fix_rounds=max_fix_rounds,
            batch_agent_cap=GENEROUS_BATCH_AGENT_CAP,
            max_codex_jobs_per_batch=1,  # always below any real need -> always refuses
            segs=segs,
        )
        assert out["pipelineCalled"] is False
        assert out["result"]["estimatedCodexJobs"] == expected, (
            f"segment_count={segment_count} max_fix_rounds={max_fix_rounds}: "
            f"expected {expected}, got {out['result']['estimatedCodexJobs']}"
        )
        checked += 1

    assert checked == len(CASES), "every case in the table must actually run -- a silently-skipped case is a silent zero"
