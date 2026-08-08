"""tests/claim_prompt_contract.test.py -- #438 D9: "a claim is preserved
across a fix round by a PROMPT LINE, not by code" -- plus draft_ready.py's
own claim-aware --expect-token refusal.

## Why this file exists, and why it is split in two parts

D9 (PLAN.md) measured that the DEFAULT `mass-translate-wf.template.js`
dispatch path has exactly ONE deterministic site that ever runs
`draft_ready.py --expect-token` (`translateAcceptCmd`, the translate wait
poll) and it never runs again after a fix round -- `runRound` goes straight
from `callFix()` to the NEXT `getVerifiedReview()`, whose own accept command
is `review_ready.py --expect-token` (the REVIEW artifact's token, not the
DRAFT's). So on that path, the only thing standing between a claimed
segment's token and a fix round that drops it is a sentence in `fixPrompt()`
-- "copy its existing value byte for byte ... never invent, drop, or
recompute it." A prompt line protected by a prompt line.

Part 1 covers what happens when that prompt line is NOT obeyed:
`draft_ready.py --expect-token`'s own refusal must name the lost claim,
not just report a bare token mismatch (this is `draft_ready.py`'s file,
edited here), and a re-claim afterward must be idempotent -- the SAME
authorization reapplied, never a second one (tested directly against the
shipped `claim_record.py`, unedited).

Part 2 covers the prompt line itself and the gap it exists to cover: the
ACTUAL rendered `fixPrompt()` text (never a paraphrase), run through the
real, currently-shipped template under Node -- the SAME instantiate/wrap/
mock-agent technique `tests/mass_translate_driver_smoke.test.py` and
`tests/fix_prompt_self_check_removed.test.py` already use, duplicated here
rather than imported (this plugin's "no shared lib between self-contained
scripts/tests" convention).

Part 3 covers a live, unrelated-to-D9 regression `chokepoint` introduced and
this file's own author found while building the #438 end-to-end test:
`codex_job.py`'s `main()` now FATALs (exit 2) whenever `--run-id` is absent
-- required on EVERY invocation, translate and review alike (D8, #438) --
but `translateDrivePrompt()`/`reviewDrivePrompt()` never forwarded it,
meaning the DEFAULT dispatch path FATALed on every launch. The template fix
lives in this file's own scope (`mass-translate-wf.template.js` is listed
below); `test_default_path_forwards_run_id_to_codex_job` pins it against
the ACTUAL rendered nohup command AND against `codex_job.py`'s own real
argparse flag set (never a hand-typed flag-name string), so the suite fails
NAMING the missing/typo'd flag rather than failing downstream with a bare
exit-2.

## What this file deliberately does NOT do

- It does not add, or test for, a template-side draft-token guard after a
  fix. PLAN.md D9/D8 are explicit that the refusal for an invalid/absent
  claimed draft belongs in `codex_job.py` (the chokepoint layer, a
  different teammate's file) -- a template-side check would retire that
  chokepoint silently. `test_default_path_has_no_deterministic_draft_
  token_recheck_after_a_fix` PINS the absence of such a guard as a
  characterization test; if it ever goes red because someone added one,
  that is a signal to revisit the design, not a bug in this test.
- It does not EDIT `claim_record.py`, `cache_key.py`, `select_segments.py`,
  `segment_dispatch_driver.py`, or `codex_job.py` -- those belong to other
  teammates' scope for this issue. Part 3 LOADS `codex_job.py` in-process
  (read-only introspection of its own argparse parser) to avoid hand-listing
  its flags, which is not the same as editing it.
"""
import importlib.util
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
ASSETS_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets"
SCRIPTS_DIR = ASSETS_DIR / "scripts"
TEMPLATES_DIR = ASSETS_DIR / "templates"

DRAFT_READY_SRC = SCRIPTS_DIR / "draft_ready.py"
CLAIM_RECORD_SRC = SCRIPTS_DIR / "claim_record.py"
CODEX_JOB_SRC = SCRIPTS_DIR / "codex_job.py"
MASS_TRANSLATE_TEMPLATE = TEMPLATES_DIR / "mass-translate-wf.template.js"

assert DRAFT_READY_SRC.is_file(), f"draft_ready.py not found at {DRAFT_READY_SRC}"
assert CLAIM_RECORD_SRC.is_file(), f"claim_record.py not found at {CLAIM_RECORD_SRC}"
assert CODEX_JOB_SRC.is_file(), f"codex_job.py not found at {CODEX_JOB_SRC}"
assert MASS_TRANSLATE_TEMPLATE.is_file(), f"template not found at {MASS_TRANSLATE_TEMPLATE}"

NODE = shutil.which("node")


def _load_module(name, path):
    """Imports a script as an in-process module -- the established pattern
    for direct-function-call unit testing elsewhere in this suite (e.g.
    resume_integrity.test.py's own `_load_module`)."""
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None, f"could not load spec for {path}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# =============================================================================
# Part 1 -- draft_ready.py's claim-aware --expect-token refusal.
# =============================================================================

def make_durable_root(tmp_path, name="durable_root", with_claim_record=True):
    """Builds an isolated durable_root: copies the REAL draft_ready.py (and,
    unless disabled, the REAL claim_record.py) into {root}/scripts/, so
    draft_ready.py's self-anchoring resolves against THIS temp root and its
    `import claim_record` (a same-directory sibling import) resolves to the
    real shipped module. `with_claim_record=False` reproduces the fixture
    contract tests/draft_ready.test.py's own make_durable_root() already
    uses -- draft_ready.py alone, no siblings -- which the enrichment in
    this file must degrade gracefully against rather than crash on."""
    root = tmp_path / name
    scripts_dir = root / "scripts"
    scripts_dir.mkdir(parents=True)
    shutil.copy2(DRAFT_READY_SRC, scripts_dir / "draft_ready.py")
    if with_claim_record:
        shutil.copy2(CLAIM_RECORD_SRC, scripts_dir / "claim_record.py")
    (root / "segments").mkdir()
    return root


def write_segment(root, seg, segpack, draft):
    segments_dir = root / "segments"
    (segments_dir / f"segpack_{seg}.json").write_text(
        json.dumps(segpack, ensure_ascii=False), encoding="utf-8"
    )
    (segments_dir / f"{seg}.draft.json").write_text(
        json.dumps(draft, ensure_ascii=False), encoding="utf-8"
    )


def clean_segpack():
    """Minimal segpack.schema.json-shaped fixture -- mirrors
    tests/draft_ready.test.py's own clean_segpack() exactly (this file's
    "no shared lib" duplication)."""
    return {
        "blocks": [{"id": "p1"}],
        "footnotes": [{"n": 1}],
        "verses": [{"vid": "vA"}],
    }


def clean_draft(seg, dispatch_token=None):
    d = {
        "seg": seg,
        "blocks": {"p1": "translated text"},
        "footnotes": {"1": "translated note"},
        "verses": {"vA": {}},
        "names": [],
        "notes": [],
    }
    if dispatch_token is not None:
        d["dispatch_token"] = dispatch_token
    return d


def run_draft_ready(root, seg, *extra_args, timeout=30):
    return subprocess.run(
        [sys.executable, str(root / "scripts" / "draft_ready.py"), seg, *extra_args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _claim_record_module():
    """The REAL, shipped claim_record.py, imported in-process -- never a
    hand-typed reimplementation of its record shape or its exclusivity
    semantics (this plugin's established convention for fixtures that need
    the production module's own behavior, not a stand-in for it)."""
    return _load_module("claim_record_under_test_prompt_contract", CLAIM_RECORD_SRC)


def write_real_claim(root, run_id, seg, *, profile="from-cap",
                      source_run_id="20260801T132418Z",
                      previous_dispatch_token="20260801T132418Z:seg01",
                      pre_claim_content_sha1="d" * 40,
                      operator_invocation="claim.py --profile from-cap --seg seg01",
                      cache_key=None, claimed_at="2026-08-08T09:00:00Z"):
    """Publishes a REAL claim record via the shipped claim_record.py -- the
    exact write path draft_ready.py's own claim lookup (added in this
    change) will later read. Returns (path, payload)."""
    cr = _claim_record_module()
    runs_dir = root / "runs"
    path = cr.claimed_path(run_id, seg, runs_dir)
    payload = cr.build_claim_record(
        seg=seg, profile=profile, run_id=run_id, source_run_id=source_run_id,
        previous_dispatch_token=previous_dispatch_token,
        pre_claim_content_sha1=pre_claim_content_sha1,
        operator_invocation=operator_invocation,
        cache_key=cache_key if cache_key is not None else {},
        claimed_at=claimed_at,
    )
    ok, detail = cr.write_claim_record(path, payload)
    assert ok, f"fixture setup: real claim record write failed: {detail}"
    return path, payload


def test_fix_round_dropping_claimed_token_is_refused_and_names_the_lost_claim(tmp_path):
    """D9 item 1 (PLAN.md, "Two things follow that the implementation must
    not get wrong" -- point 1): a fix round that drops the claimed token
    must be REFUSED, and the refusal must NAME the lost claim, not merely
    report a token mismatch. Setup: this run genuinely claimed seg01 (a
    REAL claim record on disk, written via claim_record.py), but the
    on-disk draft's dispatch_token no longer equals what the claim
    established -- exactly the shape a fix round that failed to preserve
    dispatch_token byte for byte (see Part 2 below) would produce."""
    root = make_durable_root(tmp_path)
    run_id = "20260808T000000Z"
    seg = "seg01"
    expect_token = f"{run_id}:{seg}"
    _, payload = write_real_claim(root, run_id, seg, profile="from-cap")
    draft = clean_draft(seg, dispatch_token="SOME-OTHER-VALUE-A-FIX-ROUND-INVENTED")
    write_segment(root, seg, clean_segpack(), draft)

    result = run_draft_ready(root, seg, "--expect-token", expect_token)

    assert result.returncode == 1, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    # Non-regression: tests/draft_ready.test.py's own
    # test_expect_token_mismatch_is_not_ready pins this exact substring and
    # the echoed expected token -- both must survive this change untouched.
    assert "dispatch_token mismatch" in result.stdout
    assert expect_token in result.stdout
    assert "claim" in result.stdout.lower(), (
        f"the refusal must name the lost claim: {result.stdout!r}"
    )
    assert payload["profile"] in result.stdout, "the refusal should name the claim's own profile"
    assert "lost" in result.stdout.lower()
    assert "idempotent" in result.stdout.lower() and "not a second authorization" in result.stdout.lower(), (
        "the refusal should tell the operator a re-claim is the same "
        "authorization reapplied, not a new one (D9 rule 2)"
    )
    assert "Traceback" not in result.stderr


def test_expect_token_mismatch_without_a_claim_stays_the_plain_message(tmp_path):
    """Control for the test above: when NO claim record exists for this
    run/seg, the refusal must NOT claim a lost claim -- this is a genuine
    stale/straggler draft from a different run, the original 1.2.0 meaning
    of this refusal, and conflating the two would send an operator to
    re-claim a segment nobody ever claimed. Same fixture shape as
    tests/draft_ready.test.py::test_expect_token_mismatch_is_not_ready."""
    root = make_durable_root(tmp_path)
    write_segment(root, "seg01", clean_segpack(), clean_draft("seg01"))

    result = run_draft_ready(root, "seg01", "--expect-token", "RUN1:seg01")

    assert result.returncode == 1
    assert "dispatch_token mismatch" in result.stdout
    assert "RUN1:seg01" in result.stdout
    assert "claim" not in result.stdout.lower(), (
        f"no claim record exists; the message must not mention one: {result.stdout!r}"
    )


def test_claim_record_present_does_not_affect_the_ready_path(tmp_path):
    """Non-regression control: a claim record existing for this run/seg
    must have ZERO effect on the healthy, token-matching path -- the
    enrichment only ever fires inside the mismatch branch."""
    root = make_durable_root(tmp_path)
    run_id = "20260808T000000Z"
    seg = "seg01"
    expect_token = f"{run_id}:{seg}"
    write_real_claim(root, run_id, seg)
    draft = clean_draft(seg, dispatch_token=expect_token)
    write_segment(root, seg, clean_segpack(), draft)

    result = run_draft_ready(root, seg, "--expect-token", expect_token)

    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert "[seg01] READY" in result.stdout


def test_unreadable_claim_record_is_treated_as_unclaimed_but_flagged(tmp_path):
    """claim_record.py's own documented discipline: an AMBIGUOUS claim
    record (here, a DIRECTORY occupying the claim path instead of a
    regular file) must map to "not claimed", never to "claimed" -- but
    silently dropping a genuine on-disk anomaly would hide it from the one
    operator positioned to investigate, so the refusal still flags it as
    unreadable rather than staying generic."""
    root = make_durable_root(tmp_path)
    run_id = "20260808T000000Z"
    seg = "seg01"
    expect_token = f"{run_id}:{seg}"
    claim_dir = root / "runs" / run_id / f".claimed.{seg}"
    claim_dir.mkdir(parents=True)  # a directory, not a regular file -> AMBIGUOUS
    draft = clean_draft(seg, dispatch_token="SOME-OTHER-VALUE")
    write_segment(root, seg, clean_segpack(), draft)

    result = run_draft_ready(root, seg, "--expect-token", expect_token)

    assert result.returncode == 1
    assert "dispatch_token mismatch" in result.stdout
    assert "claim" in result.stdout.lower()
    assert "unreadable" in result.stdout.lower(), (
        f"an ambiguous claim record must be flagged as unreadable, not "
        f"silently dropped or asserted as a claim: {result.stdout!r}"
    )
    # Must NOT read as an actual claim: the "LOST"/idempotent language
    # belongs only to the CLAIM_PRESENT case.
    assert "idempotent" not in result.stdout.lower()


def test_without_claim_record_module_present_degrades_to_the_plain_message(tmp_path):
    """draft_ready.py's claim lookup is BEST-EFFORT and must never crash
    the readiness probe: a caller that copies only draft_ready.py itself --
    the pre-#438 fixture/deployment contract tests/draft_ready.test.py's
    own make_durable_root() still uses -- must get the exact pre-#438
    message, not an ImportError traceback."""
    root = make_durable_root(tmp_path, with_claim_record=False)
    assert not (root / "scripts" / "claim_record.py").exists()
    write_segment(root, "seg01", clean_segpack(), clean_draft("seg01"))

    result = run_draft_ready(root, "seg01", "--expect-token", "RUN1:seg01")

    assert result.returncode == 1
    assert "dispatch_token mismatch" in result.stdout
    assert "Traceback" not in result.stderr
    assert result.stderr == ""


def test_fix_round_dropping_claimed_token_names_the_lost_claim_for_a_colon_bearing_id(tmp_path):
    """Same shape as the primary D9 test above, for a colon-bearing segment
    id -- FRONTBACK:errata_02 is a real, shipped id shape (#438 P3), and it
    reaches a real on-disk claim-record filename
    (runs/<run>/.claimed.FRONTBACK:errata_02) that must round-trip through
    claimed_path()/read_claim_record() exactly like an ordinary id."""
    root = make_durable_root(tmp_path)
    run_id = "20260808T000000Z"
    seg = "FRONTBACK:errata_02"
    expect_token = f"{run_id}:{seg}"
    _, payload = write_real_claim(root, run_id, seg, profile="from-cap")
    draft = clean_draft(seg, dispatch_token="SOME-OTHER-VALUE")
    write_segment(root, seg, clean_segpack(), draft)

    result = run_draft_ready(root, seg, "--expect-token", expect_token)

    assert result.returncode == 1, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert seg in result.stdout
    assert "claim" in result.stdout.lower()
    assert payload["profile"] in result.stdout
    assert "idempotent" in result.stdout.lower()


# =============================================================================
# Part 2 -- claim_record.py's own re-claim idempotency (D9 rule 2: "a
# re-claim after a lost token is the same authorization being reapplied,
# not a new one, and it must not consume or extend anything"). Exercises
# the shipped, UNEDITED claim_record.py directly -- this file does not
# modify it.
# =============================================================================

def test_reclaim_after_a_lost_token_is_idempotent_not_a_second_authorization(tmp_path):
    """Calls the REAL write_claim_record() twice for the identical
    (run, seg) -- the second call (an operator re-claiming after watching a
    fix round drop the token) must be refused as "already claimed by this
    run", and the FIRST claim's own payload on disk -- especially
    previous_dispatch_token/pre_claim_content_sha1, the only durable record
    of what the draft looked like BEFORE this run ever touched it -- must
    stay byte-for-byte unchanged. A second payload built from DIFFERENT
    facts is used deliberately (not a copy of the first): if the write were
    NOT exclusive, this is the shape that would silently corrupt the
    pre-claim baseline the whole claim mechanism rests on."""
    cr = _claim_record_module()
    root = tmp_path / "durable_root"
    runs_dir = root / "runs"
    run_id = "20260808T000000Z"
    seg = "seg01"
    path = cr.claimed_path(run_id, seg, runs_dir)

    first_payload = cr.build_claim_record(
        seg=seg, profile="from-cap", run_id=run_id, source_run_id="20260801T132418Z",
        previous_dispatch_token="20260801T132418Z:seg01", pre_claim_content_sha1="a" * 40,
        operator_invocation="claim.py --profile from-cap --seg seg01",
        cache_key={}, claimed_at="2026-08-08T09:00:00Z",
    )
    ok1, detail1 = cr.write_claim_record(path, first_payload)
    assert ok1, detail1
    before = path.read_text(encoding="utf-8")

    second_payload = cr.build_claim_record(
        seg=seg, profile="from-cap", run_id=run_id, source_run_id="20260801T132418Z",
        previous_dispatch_token="A-DIFFERENT-VALUE-A-BROKEN-RECLAIM-MIGHT-SUPPLY",
        pre_claim_content_sha1="b" * 40,
        operator_invocation="claim.py --profile from-cap --seg seg01 (retry)",
        cache_key={}, claimed_at="2026-08-08T09:05:00Z",
    )
    ok2, detail2 = cr.write_claim_record(path, second_payload)

    assert ok2 is False, "a re-claim of an already-claimed segment must not report a fresh success"
    assert detail2 == "already claimed by this run", detail2
    after = path.read_text(encoding="utf-8")
    assert after == before, (
        "the re-claim must not have touched the on-disk record at all -- a "
        "re-claim is the SAME authorization being reapplied, never a "
        "second one, and overwriting would destroy the only record of the "
        "true pre-claim baseline"
    )


def test_reclaim_for_a_colon_bearing_id_is_also_idempotent(tmp_path):
    """Same property as above, for FRONTBACK:errata_02 -- the colon must
    survive claimed_path()'s filename construction identically on both the
    first write and the exclusivity check that refuses the second."""
    cr = _claim_record_module()
    root = tmp_path / "durable_root"
    runs_dir = root / "runs"
    run_id = "20260801T132418Z"
    seg = "FRONTBACK:errata_02"
    path = cr.claimed_path(run_id, seg, runs_dir)
    assert path.name == ".claimed.FRONTBACK:errata_02"

    payload = cr.build_claim_record(
        seg=seg, profile="from-cap", run_id=run_id, source_run_id=run_id,
        previous_dispatch_token=f"{run_id}:{seg}", pre_claim_content_sha1="c" * 40,
        operator_invocation="claim.py --profile from-cap --seg FRONTBACK:errata_02",
        cache_key={}, claimed_at="2026-08-08T09:10:00Z",
    )
    ok1, _ = cr.write_claim_record(path, payload)
    assert ok1

    ok2, detail2 = cr.write_claim_record(path, payload)
    assert ok2 is False
    assert detail2 == "already claimed by this run"


# =============================================================================
# Part 3 -- the DEFAULT path's ACTUAL rendered fix prompt (D9's real
# protection) and the characterization test pinning the gap it covers.
# =============================================================================

# Deliberately NOT a module-level `pytestmark`: that would also skip Parts 1
# and 2 above, which are pure Python (subprocess + in-process import) and
# have no Node dependency at all. Applied per-function instead, below.
_needs_node = pytest.mark.skipif(
    NODE is None,
    reason="node not found on PATH; Part 3 executes the real workflow "
    "template's fixPrompt()/runRound() wiring under Node (no hard Node.js "
    "dependency for this plugin otherwise)",
)

FIXTURE_DURABLE_ROOT = "/fixture/project/durable_root"
FIXTURE_RUN_ID = "20260808T000000Z"
FIXTURE_SOURCE_LANG = "he"
FIXTURE_TARGET_LANG = "en"
FIXTURE_VERSE_POLICY = "Render every verse literally, line by line."
FIXTURE_COMPANION_PATH = "/opt/codex/1.0.10/codex-companion.mjs"
FIXTURE_EFFORT = "high"
FIXTURE_MODEL = ""
FIXTURE_PLUGIN_ROOT = ""


def instantiate(*, max_fix_rounds: int, batch_agent_cap: int = 100000,
                 max_codex_jobs_per_batch: int = 100000) -> str:
    """The exact one-time substitution the template's own header documents
    -- duplicated, not imported, so this file stays self-contained like
    every sibling test file (mass_translate_driver_smoke.test.py's own
    instantiate() is the source of this pattern)."""
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
    text = text.replace("{{EFFORT}}", FIXTURE_EFFORT)
    text = text.replace("{{MODEL}}", FIXTURE_MODEL)
    text = text.replace("{{PLUGIN_ROOT}}", json.dumps(FIXTURE_PLUGIN_ROOT))
    assert "{{" not in text, "fixture instantiation left an unresolved token"
    return text


def _wrap(js_source: str) -> str:
    assert js_source.count("export const meta") == 1
    body = js_source.replace("export const meta", "const meta", 1)
    return "async function __workflowMain__(agent, pipeline, log, args) {\n" + body + "\n}\n"


# A minimal, self-contained harness: SEG is baked in directly (never
# re-derived from a label string, unlike mass_translate_driver_smoke.test.py's
# shared HARNESS, whose `label.split(":")[1]` derivation is wrong for a
# colon-bearing id -- FRONTBACK:errata_02 IS this file's required coverage,
# so that shortcut is not reused here). Drives exactly ONE fix round: round
# 1's review comes back non-clean, round 2's comes back clean -- the
# minimal shape that exercises callFix() AND the following getVerifiedReview()
# in the same run, which is what both Part 3 tests below need.
HARNESS = r"""
'use strict';
__WRAPPED_SOURCE__

const SEG = __SEG_JSON__;
const SEGS_ARGS = [SEG];
const NON_CLEAN_REVIEW = {
  clean: false, coverage_ok: true,
  findings: [{ loc: "VERSE:1", severity: "minor", issue: "i", suggest: "s" }],
  draft_sha1: "a".repeat(40),
};
const CLEAN_REVIEW = { clean: true, coverage_ok: true, findings: [], draft_sha1: "a".repeat(40) };
const callsLog = [];
const promptByLabel = {};

async function agent(promptText, opts) {
  opts = opts || {};
  const label = opts.label || "";
  promptByLabel[label] = promptText;
  callsLog.push({ label: label });

  if (label.indexOf("ledger:") === 0) {
    let status = "blocked";
    if (label.indexOf(":in_progress:") !== -1) status = "in_progress";
    else if (label.indexOf(":converged:") !== -1) status = "converged";
    else if (label.indexOf(":cap:") !== -1) status = "non_converged";
    return { success: true, status: status, fragment_path: "/x/" + SEG + ".json", fragment_sha1: "d" };
  }
  if (label === "merge-ledger") {
    return { success: true, ledger_path: "/x/l.json", n_segments: 1, missing_segments: [], stale_segments: [] };
  }
  if (label === "translate:" + SEG) return "DISPATCHED " + SEG + " a1b2c3d4";
  if (label === "review-dispatch:" + SEG + ":r1" || label === "review-dispatch:" + SEG + ":r2") {
    return "DISPATCHED " + SEG + " beef1234";
  }
  if (label === "wait:" + SEG) return "READY " + SEG;
  if (label === "review-wait:" + SEG + ":r1" || label === "review-wait:" + SEG + ":r2") return "READY " + SEG;
  if (label === "review-read:" + SEG + ":r1") return NON_CLEAN_REVIEW;
  if (label === "review-read:" + SEG + ":r2") return CLEAN_REVIEW;
  if (label === "artifact-check:" + SEG + ":r1" || label === "artifact-check:" + SEG + ":r2") return { match: true };
  if (label === "fix:" + SEG + ":r1") return "FIXED " + SEG;
  if (label === "draft-probe:" + SEG) return { present: true };
  throw new Error("mock agent(): unrecognized label " + JSON.stringify(label));
}

async function pipeline(items, stage1, stage2) {
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
    process.stdout.write(JSON.stringify({ result: result, calls: callsLog, promptByLabel: promptByLabel }));
  } catch (err) {
    process.stderr.write("HARNESS_ERROR: " + (err && err.message || String(err)) + "\n");
    process.exit(1);
  }
})();
"""


def run_pipeline(tmp_path, seg, *, max_fix_rounds=2, timeout=30) -> dict:
    src = instantiate(max_fix_rounds=max_fix_rounds)
    harness = (
        HARNESS.replace("__WRAPPED_SOURCE__", _wrap(src))
        .replace("__SEG_JSON__", json.dumps(seg))
    )
    p = tmp_path / "claim_prompt_harness.js"
    p.write_text(harness, encoding="utf-8")
    assert NODE is not None
    proc = subprocess.run([NODE, str(p)], capture_output=True, text=True, timeout=timeout)
    assert proc.returncode == 0, (
        f"harness run failed for seg={seg!r} (rc={proc.returncode}):\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    return json.loads(proc.stdout)


@_needs_node
@pytest.mark.parametrize("seg", ["seg01", "FRONTBACK:errata_02"])
def test_fix_prompt_actual_rendered_text_preserves_the_claimed_token_byte_for_byte(tmp_path, seg):
    """D9's actual protection on the DEFAULT path (PLAN.md D9,
    mass-translate-wf.template.js:1284): the claim survives a fix round
    ONLY because fixPrompt()'s own rendered text instructs the fixer to
    copy the draft's existing dispatch_token byte for byte, unchanged. This
    is exercised against the ACTUAL rendered fixPrompt() output produced by
    the real, currently-shipped template running under Node -- never a
    paraphrase of the instruction -- and against a colon-bearing id
    (FRONTBACK:errata_02 is a real, shipped #438 P3 shape)."""
    out = run_pipeline(tmp_path, seg, max_fix_rounds=2)
    fix_prompt = out["promptByLabel"]["fix:" + seg + ":r1"]
    assert (
        "The draft also carries a dispatch_token top-level field -- copy "
        "its existing value byte for byte into your rewritten draft, "
        "unchanged; never invent, drop, or recompute it."
    ) in fix_prompt, f"fixPrompt must still carry the byte-for-byte preserve instruction:\n{fix_prompt}"


@_needs_node
def test_default_path_has_no_deterministic_draft_token_recheck_after_a_fix(tmp_path):
    """Characterization test (PLAN.md D9 and the Tests entry "D9's default-
    path shape"): pins the actual protection GAP the byte-for-byte prompt
    line above is compensating for. On the DEFAULT template path there is
    exactly one code site that ever runs `draft_ready.py --expect-token`
    (translateAcceptCmd, the translate wait poll) and it never runs again
    after a fix round -- `runRound` goes straight from `callFix()` to the
    NEXT round's `getVerifiedReview()`, whose own accept command is
    `review_ready.py --expect-token` (the REVIEW artifact's token, not the
    DRAFT's).

    If a future change adds a template-side draft-token re-check between a
    fix and the next review, THIS TEST GOES RED. That is the correct signal
    to revisit D9's design (PLAN.md explicitly warns against a template-
    side guard here, because the refusal for an invalid/absent claimed
    draft already belongs to codex_job.py -- a template-side check would
    silently retire that chokepoint) -- not a bug in this test to be fixed
    by loosening the assertion."""
    seg = "seg01"
    out = run_pipeline(tmp_path, seg, max_fix_rounds=2)

    labels = [c["label"] for c in out["calls"]]
    fix_idx = labels.index("fix:" + seg + ":r1")
    assert labels[fix_idx + 1] == "review-dispatch:" + seg + ":r2", (
        f"the call immediately after the fix must be the next round's "
        f"review dispatch, with nothing in between; got: {labels}"
    )
    assert "wait:" + seg not in labels[fix_idx + 1:], (
        "the draft's own wait poll -- the only site that ever runs "
        "draft_ready.py --expect-token -- must not run again after a fix"
    )

    round2_wait_prompt = out["promptByLabel"]["review-wait:" + seg + ":r2"]
    assert "review_ready.py" in round2_wait_prompt
    assert "draft_ready.py" not in round2_wait_prompt, (
        "the post-fix accept command must be review_ready.py (the "
        "REVIEW's token), never draft_ready.py (the DRAFT's) -- there is "
        "no deterministic draft-token re-check after a fix on this path"
    )

    # Sweep every prompt captured from the fix call onward, not just the
    # one inspected above -- draft_ready.py must not appear ANYWHERE
    # downstream of the fix.
    for label in labels[fix_idx + 1:]:
        prompt = out["promptByLabel"].get(label, "")
        assert "draft_ready.py" not in prompt, (
            f"draft_ready.py must not appear in the {label!r} prompt, "
            f"which runs after the fix -- see this test's docstring"
        )


@_needs_node
def test_default_path_ledger_write_precedes_the_translate_dispatch(tmp_path):
    """PLAN.md D8's residual, pinned so it stays visible in the suite
    rather than only in prose: `recordLedgerCall(seg, {status:
    "in_progress"})` (mass-translate-wf.template.js:1782) completes BEFORE
    `agent(translateDrivePrompt(seg))` (:1785) -- so any refusal inside
    codex_job.py (the chokepoint layer, a different teammate's file, out of
    this file's scope) fires strictly AFTER the ledger has already been
    full-replaced to a two-key in_progress row. This is why that chokepoint
    refusal saves the draft BYTES for a healthy claimed segment
    (codex_job.py's own safe_adopt() never reaches launch()) but does NOT
    save the ledger RECORD -- it is already overwritten by the time
    codex_job.py is even invoked. Exercised on the colon-bearing id, since
    that is the shape most likely to be hand-typed wrong in a future
    change to this ordering."""
    seg = "FRONTBACK:errata_02"
    out = run_pipeline(tmp_path, seg, max_fix_rounds=2)
    labels = [c["label"] for c in out["calls"]]
    assert labels[0] == "ledger:in_progress:" + seg, (
        f"the in_progress ledger write must be the very first call; got: {labels[:3]}"
    )
    assert labels[1] == "translate:" + seg, (
        f"the translate dispatch must be the second call, strictly after "
        f"the ledger write; got: {labels[:3]}"
    )


def _codex_job_flags() -> set:
    """Every long-form flag `codex_job.py`'s OWN, real, currently-shipped
    argparse parser recognizes -- introspected, never hand-listed, so a
    future rename on codex_job.py's side is what breaks this, not a stale
    copy-pasted list drifting out of sync with it."""
    codex_job = _load_module("codex_job_flag_introspection", CODEX_JOB_SRC)
    parser = codex_job._build_parser()
    flags = set()
    for action in parser._actions:
        for opt in action.option_strings:
            if opt.startswith("--"):
                flags.add(opt)
    return flags


def _extract_nohup_flag_value(prompt_text: str, flag: str) -> "str | None":
    """The value immediately following `flag` on the ONE `nohup ...
    codex_job.py ...` line in `prompt_text`, or None if the line or the flag
    is absent. A targeted regex, not a full shell parse -- sufficient since
    every flag value this test checks is a single non-whitespace token
    (an id, never free text)."""
    nohup_lines = [ln for ln in prompt_text.splitlines() if "nohup" in ln and "codex_job.py" in ln]
    assert len(nohup_lines) == 1, (
        f"expected exactly one nohup codex_job.py line, found {len(nohup_lines)}:\n{prompt_text}"
    )
    m = re.search(re.escape(flag) + r"\s+(\S+)", nohup_lines[0])
    return m.group(1) if m else None


@_needs_node
def test_default_path_forwards_run_id_to_codex_job(tmp_path):
    """Regression pin for a live defect this file's own author found while
    building the #438 end-to-end test: `codex_job.py:main()` FATALs (exit 2)
    whenever `--run-id` is absent -- required on EVERY invocation it
    launches, translate and review alike (D8, #438) -- but
    `translateDrivePrompt()`/`reviewDrivePrompt()` never forwarded it, so
    every default-path dispatch FATALed immediately. Fixed in this same
    file's scope (`mass-translate-wf.template.js`).

    Checked against the ACTUAL rendered nohup command (never a paraphrase)
    for BOTH dispatch sites, and cross-checked against `codex_job.py`'s own
    real parser so a future rename of the flag fails here by name rather
    than downstream as a bare exit 2 -- exactly mirroring the guard
    `chokepoint` built on the driver's own side (introspecting the real
    parser rather than hand-listing flags)."""
    assert "--run-id" in _codex_job_flags(), (
        "codex_job.py's own parser no longer recognizes --run-id -- this "
        "test's own premise has changed; update BOTH the template and this "
        "test together, not just one"
    )

    seg = "seg01"
    out = run_pipeline(tmp_path, seg, max_fix_rounds=2)

    translate_prompt = out["promptByLabel"]["translate:" + seg]
    translate_run_id = _extract_nohup_flag_value(translate_prompt, "--run-id")
    assert translate_run_id == FIXTURE_RUN_ID, (
        f"translateDrivePrompt()'s rendered nohup command must forward "
        f"--run-id {FIXTURE_RUN_ID!r} to codex_job.py -- got {translate_run_id!r}. "
        f"Prompt:\n{translate_prompt}"
    )

    review_prompt = out["promptByLabel"]["review-dispatch:" + seg + ":r1"]
    review_run_id = _extract_nohup_flag_value(review_prompt, "--run-id")
    assert review_run_id == FIXTURE_RUN_ID, (
        f"reviewDrivePrompt()'s rendered nohup command must forward "
        f"--run-id {FIXTURE_RUN_ID!r} to codex_job.py -- got {review_run_id!r}. "
        f"Prompt:\n{review_prompt}"
    )


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
