"""tests/claim_prompt_contract.test.py -- #438 D9: "a claim is preserved
across a fix round by a PROMPT LINE, not by code" -- plus draft_ready.py's
own claim-aware --expect-token refusal.

## Why this file exists, and why it is split into parts

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

Part 2b covers the seam Parts 1 and 2 each only ever saw ONE side of, and
which was BROKEN while both of them were green: the remedy draft_ready.py
prints is only worth printing if running it works. It claims a segment
through the REAL select_segments.py, drops the draft's dispatch_token the
way a fix round would, reads the refusal draft_ready.py produces, and then
runs the recovery THAT MESSAGE ITSELF NAMES -- taking the run id and the
profile out of the printed text rather than out of a constant this file
chose. Before #438's D9 recovery landed in the selector, that second
invocation exited 1 and the advertised remedy did not exist; nothing in
Parts 1 or 2 could go red over it.

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
  its flags, and Part 2b RUNS the shipped `select_segments.py` as a
  subprocess against a fixture root; neither is the same as editing them.
- Part 2b does not re-prove the D1-D6 admission matrix (that is
  `tests/claim_selector.test.py`'s file). It uses exactly one admissible
  population and asserts only what crossing the seam produces.
"""
import importlib.util
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime, timedelta
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


# The FOREIGN profile every four-way fixture below gives its foreign record.
# It must differ from the profile this run's own record carries ("from-cap",
# either written directly or minted by the real selector's --from-cap), and
# every one of those tests asserts that it does: until #453 all four fixtures
# wrote "from-cap" on BOTH records, so no assertion in this file could tell
# whose facts _claim_note() was printing, and a note that reported the foreign
# record's profile and claim time as this run's own passed the whole suite.
FOREIGN_CLAIM_PROFILE = "from-stalled"


def assert_records_are_not_swapped(out, *, seg, run_id, profile, claimed_at,
                                    foreign_run_id, foreign_profile,
                                    foreign_claimed_at):
    """Pins each of the two claim records' facts to the RECORD THEY BELONG TO,
    on a FOREIGN/CLAIM_PRESENT verdict.

    Presence assertions cannot do this. `_claim_note()` prints both records'
    profile and claimed_at, so a swap -- this run's slot filled from the
    foreign payload, or the reverse -- leaves every individual value still
    somewhere in the output and satisfies any "is this string present" check.
    What distinguishes the two is ADJACENCY: each run id sits immediately
    before its own record's `(profile=..., claimed_at=...)`. So each side is
    asserted as ONE CONTIGUOUS substring spanning the run id through that
    closing paren, and nothing between them is left unpinned.

    The fixture self-check comes first on purpose: two records carrying the
    same profile make the pin vacuous again without failing anything, which is
    exactly how this surface came to be unprotected (#453)."""
    assert profile != foreign_profile, (
        f"fixture is vacuous: both claim records carry profile {profile!r}, so "
        f"a swap of the two records' facts cannot be detected by any assertion "
        f"below -- give the foreign record FOREIGN_CLAIM_PROFILE"
    )
    this_facts = (
        f"a claim record for run {run_id!r} IS present for this segment "
        f"(profile={profile!r}, claimed_at={claimed_at!r})"
    )
    foreign_facts = (
        f"a DIFFERENT run, {foreign_run_id!r}, which itself holds a live "
        f"claim record for {seg!r} (profile={foreign_profile!r}, "
        f"claimed_at={foreign_claimed_at!r})"
    )
    assert this_facts in out, (
        f"this run's own claim record must be reported with ITS OWN profile "
        f"and claim time, adjacent to its own run id -- expected "
        f"{this_facts!r} in: {out!r}"
    )
    assert foreign_facts in out, (
        f"the foreign run's claim record must be reported with ITS OWN profile "
        f"and claim time, adjacent to its own run id -- expected "
        f"{foreign_facts!r} in: {out!r}"
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
                      claimed_at="2026-08-08T09:00:00Z", **overrides):
    """Publishes a REAL claim record via the shipped claim_record.py -- the
    exact write path draft_ready.py's own claim lookup (added in this
    change) will later read. Returns (path, payload).

    build_claim_record() is keyword-only with all fourteen fields REQUIRED
    and no defaults, so the fields this fixture has no opinion about are
    supplied as None from CLAIM_RECORD_FIELDS itself rather than spelled out
    here. Part 1's subject is what draft_ready.py PRINTS about a record, and
    it prints exactly two fields (`profile`, `claimed_at`); coupling this
    helper to the full evidence set would make every future field addition
    edit a fixture that never reads one. `**overrides` is the escape hatch
    for a test that does care about one of them."""
    cr = _claim_record_module()
    runs_dir = root / "runs"
    path = cr.claimed_path(run_id, seg, runs_dir)
    payload = cr.build_claim_record(**dict(
        {field: None for field in cr.CLAIM_RECORD_FIELDS},
        seg=seg, profile=profile, run_id=run_id, source_run_id=source_run_id,
        previous_dispatch_token=previous_dispatch_token,
        pre_claim_content_sha1=pre_claim_content_sha1,
        operator_invocation=operator_invocation,
        claimed_at=claimed_at,
        **overrides,
    ))
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


def test_draft_ready_treats_a_foreign_token_with_no_claim_record_as_recoverable_not_refused(tmp_path):
    """The FOREIGN-shaped mismatch has a twin the original fixture for the
    SUPERSEDED test (above, in Part 2b) used to conflate with the live-record
    case: the draft's CURRENT token names some OTHER run T, but T holds NO
    claim record at all. Nobody actually claimed the segment out from under
    this run -- the token was rewritten some other way (by hand, or by a fix
    round that invented rather than preserved it) -- so this is closer to the
    LOST case in REMEDY even though `_claim_note()` reaches it through the
    FOREIGN branch (T's parsed run id != this run's own): re-claiming under
    THIS run's own --run-id is the correct recovery, and the refusal must not
    tell the operator it "will be refused", because select_segments.py's own
    reclaim guard only refuses when the foreign run's claim record classifies
    non-ABSENT, and T's classifies CLAIM_ABSENT here.

    THE MUTATION THAT MAKES THIS FAIL: delete the `if foreign_state ==
    claim_record.CLAIM_ABSENT:` branch from draft_ready.py's `_claim_note()`
    (i.e. let a foreign CLAIM_ABSENT record fall through to the CLAIM_AMBIGUOUS
    wording below it, which reports T's ownership as merely UNREADABLE/
    undetermined rather than saying outright that T holds no record at all --
    a milder but still wrong understatement). Measured: this drops "NO claim
    record" from the message and fails the assertion for it below; the
    SUPERSEDED/"will be refused" assertions do not by themselves catch this
    particular mutation (the AMBIGUOUS wording says neither), which is why
    the "NO claim record" assertion is this test's load-bearing one.

    This run's own claim record IS real (write_real_claim(), the shipped
    claim_record.py); the foreign token is a direct edit with no record ever
    published behind it -- the shape this fixture is FOR, and the one the
    SUPERSEDED test's own fixture used to produce by mistake."""
    root = make_durable_root(tmp_path)
    run_id = "20260808T000000Z"
    seg = "seg01"
    expect_token = f"{run_id}:{seg}"
    foreign_run_id = "20260813T000000Z"
    _, payload = write_real_claim(root, run_id, seg, profile="from-cap")
    draft = clean_draft(seg, dispatch_token=f"{foreign_run_id}:{seg}")
    write_segment(root, seg, clean_segpack(), draft)
    assert not (root / "runs" / foreign_run_id).exists(), (
        "fixture precondition: the foreign run must hold NO claim record at all"
    )

    result = run_draft_ready(root, seg, "--expect-token", expect_token)

    assert result.returncode == 1, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    out = result.stdout
    assert foreign_run_id in out, f"the refusal must name the foreign run on the token: {out!r}"
    assert "NO claim record" in out or "no claim record" in out.lower(), (
        f"the refusal must say nobody actually holds the segment: {out!r}"
    )
    assert "SUPERSEDED" not in out, (
        f"nobody holds a live claim, so this must not be reported as a "
        f"superseded authorization: {out!r}"
    )
    assert "will be refused" not in out and "it will be refused" not in out, (
        f"the selector does not refuse this reclaim (the foreign run holds no "
        f"record), so the message must not promise a refusal that will not "
        f"happen: {out!r}"
    )
    assert "the claim was LOST" not in out, (
        f"this is reached through the FOREIGN branch, not the plain LOST "
        f"branch, even though the remedy is the same: {out!r}"
    )
    assert payload["profile"] in out, "the refusal should still name this run's own claim's profile"


# =============================================================================
# Part 2 -- claim_record.py's own re-claim idempotency (D9 rule 2: "a
# re-claim after a lost token is the same authorization being reapplied,
# not a new one, and it must not consume or extend anything"). Exercises
# the shipped, UNEDITED claim_record.py directly -- this file does not
# modify it.
# =============================================================================

def test_write_claim_record_is_exclusive_so_a_reclaim_cannot_rewrite_the_baseline(tmp_path):
    """Calls the REAL write_claim_record() twice for the identical
    (run, seg) -- the second call (an operator re-claiming after watching a
    fix round drop the token) must be refused as "already claimed by this
    run", and the FIRST claim's own payload on disk -- especially
    previous_dispatch_token/pre_claim_content_sha1, the only durable record
    of what the draft looked like BEFORE this run ever touched it -- must
    stay byte-for-byte unchanged. A second payload built from DIFFERENT
    facts is used deliberately (not a copy of the first): if the write were
    NOT exclusive, this is the shape that would silently corrupt the
    pre-claim baseline the whole claim mechanism rests on.

    WHAT THIS TEST DOES NOT COVER, stated because it used to be named as
    though it did: it never drops a dispatch_token and it never invokes the
    selector, so it cannot fail when the ADVERTISED recovery is unreachable
    -- and for the whole of #438's first draft it was, while this test
    stayed green. Calling write_claim_record() twice exercises one property
    of claim_record.py (exclusivity), not the operator-visible remedy
    draft_ready.py prints. That remedy is covered by
    test_lost_token_recovery_runs_the_command_draft_ready_advertises() in
    Part 2b, which drives the real selector for real; this test is the unit
    that Part 2b's idempotency assertion rests on."""
    cr = _claim_record_module()
    root = tmp_path / "durable_root"
    runs_dir = root / "runs"
    run_id = "20260808T000000Z"
    seg = "seg01"
    path = cr.claimed_path(run_id, seg, runs_dir)

    first_payload = cr.build_claim_record(**dict(
        {field: None for field in cr.CLAIM_RECORD_FIELDS},
        seg=seg, profile="from-cap", run_id=run_id, source_run_id="20260801T132418Z",
        previous_dispatch_token="20260801T132418Z:seg01", pre_claim_content_sha1="a" * 40,
        operator_invocation="claim.py --profile from-cap --seg seg01",
        claimed_at="2026-08-08T09:00:00Z",
    ))
    ok1, detail1 = cr.write_claim_record(path, first_payload)
    assert ok1, detail1
    before = path.read_text(encoding="utf-8")

    second_payload = cr.build_claim_record(**dict(
        {field: None for field in cr.CLAIM_RECORD_FIELDS},
        seg=seg, profile="from-cap", run_id=run_id, source_run_id="20260801T132418Z",
        previous_dispatch_token="A-DIFFERENT-VALUE-A-BROKEN-RECLAIM-MIGHT-SUPPLY",
        pre_claim_content_sha1="b" * 40,
        operator_invocation="claim.py --profile from-cap --seg seg01 (retry)",
        claimed_at="2026-08-08T09:05:00Z",
    ))
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

    payload = cr.build_claim_record(**dict(
        {field: None for field in cr.CLAIM_RECORD_FIELDS},
        seg=seg, profile="from-cap", run_id=run_id, source_run_id=run_id,
        previous_dispatch_token=f"{run_id}:{seg}", pre_claim_content_sha1="c" * 40,
        operator_invocation="claim.py --profile from-cap --seg FRONTBACK:errata_02",
        claimed_at="2026-08-08T09:10:00Z",
    ))
    ok1, _ = cr.write_claim_record(path, payload)
    assert ok1

    ok2, detail2 = cr.write_claim_record(path, payload)
    assert ok2 is False
    assert detail2 == "already claimed by this run"


# =============================================================================
# Part 2b -- the ADVERTISED recovery, run for real.
#
# Part 1 proves draft_ready.py PRINTS a remedy. Part 2 proves claim_record.py
# refuses to overwrite a record. Neither one ever ran the remedy, and for the
# whole of #438's first draft the remedy did not work: select_segments.py's S3
# refused a token-less draft before the claim block ever consulted an existing
# record, so "re-run select_segments.py's claim step ... it re-stamps the
# token" named a command that exits 1. Both files were green throughout. That
# is the producer/consumer trap in its purest form -- the producer of the
# instruction and the consumer of it were tested against separate fixtures and
# nothing ever fed one into the other.
#
# So this part drives the REAL select_segments.py, twice, over a REAL
# durable_root, and takes the second invocation's arguments FROM THE MESSAGE
# draft_ready.py actually printed rather than from a constant this file
# chose. The fixture is duplicated from tests/claim_selector.test.py's own
# P2 (--from-cap) population rather than imported, per this plugin's "no
# shared lib between self-contained scripts/tests" convention; --from-cap is
# chosen over --from-converged for the same reason that file gives (no
# .ever_converged bookkeeping, so the fixture's moving parts stay minimal).
# =============================================================================

SELECT_SEGMENTS_SRC = SCRIPTS_DIR / "select_segments.py"
LEDGER_MERGE_SRC = SCRIPTS_DIR / "ledger_merge.py"
VALIDATE_DRAFT_SRC = SCRIPTS_DIR / "validate_draft.py"
SCHEMAS_SRC = ASSETS_DIR / "schemas"

for _src in (SELECT_SEGMENTS_SRC, LEDGER_MERGE_SRC, VALIDATE_DRAFT_SRC):
    assert _src.is_file(), f"required sibling script not found at {_src}"
assert SCHEMAS_SRC.is_dir(), f"schemas dir not found at {SCHEMAS_SRC}"

# The same fixture stand-in for cache_key.py every other selector-driving test
# file in this suite uses -- its own 15-field hashing algorithm has a dedicated
# test file, and re-proving it here would only make this fixture heavier.
FAKE_CACHE_KEY_PY = """#!/usr/bin/env python3
import argparse
import json
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seg")
    parser.add_argument("--field")
    parser.add_argument("--durable-root", default=None)
    args = parser.parse_args()
    if args.durable_root:
        durable_root = Path(args.durable_root).resolve()
    else:
        durable_root = Path(__file__).resolve().parent.parent
    keys_path = durable_root / "test_fixture_cache_keys.json"
    if not args.seg:
        sys.stderr.write("fake cache_key.py: test stub requires --seg\\n")
        return 1
    data = json.loads(keys_path.read_text(encoding="utf-8"))
    if args.seg not in data:
        sys.stderr.write(f"fake cache_key.py: no fixture key for {args.seg}\\n")
        return 1
    print(json.dumps(data[args.seg]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
"""

# validate_draft.py's own load_profile() requires all three sections. Written
# as literal YAML rather than yaml.safe_dump()'d so this file keeps its
# stdlib-only import set (Parts 1-3 need no third-party module at all).
CLAIM_PROFILE_YAML = (
    "verse_policy:\n"
    "  mode: full_rhymed_plus_literal\n"
    "  threshold_lines: null\n"
    "footnotes:\n"
    "  apparatus_policy: translate_all\n"
    "validation:\n"
    '  untranslated_sentinel: "[TODO-UNTRANSLATED]"\n'
)

CACHE_KEY_FIELDS = [
    "input_sha1", "style_contract_hash", "used_terms_hash", "pipeline_version",
    "schema_hash", "prompt_hash", "agent_config_hash", "profile_semantics_hash",
    "particle_config_hash", "source_extraction_hash", "source_input_hash",
    "derivation_bundle_hash", "verse_map_hash", "note_map_hash", "plugin_bundle_hash",
]

FN_PH = "\u27e6FNREF_1\u27e7"
V_PH_A = "\u27e6VERSE_vA\u27e7"

CLAIM_RUN_ID = "20260812T000000Z"
CLAIM_SOURCE_RUN_ID = "20260801T090000Z"


def claim_segpack(seg):
    return {
        "seg": seg,
        "blocks": [
            {"id": "p1", "order_index": 0,
             "source_html": f"<p>Some prose with a note {FN_PH} attached.</p>"},
            {"id": "vblockA", "order_index": 1,
             "source_html": "<p>Premiere ligne<br/>Deuxieme ligne</p>"},
        ],
        "footnotes": [{"n": 1, "source_text": "Une note en francais."}],
        "verses": [{"vid": "vA", "placeholder": V_PH_A, "parent_block": "vblockA"}],
        "names": [],
        "canon_names": [],
        "new_names": [],
        "canon_map": {},
        "generation_hashes": {
            "source_extraction_hash": "sxh-0",
            "source_input_hash": "sih-0",
            "particle_config_hash": "pch-0",
            "derivation_bundle_hash": "dbh-0",
        },
    }


def claim_draft(seg, dispatch_token):
    return {
        "seg": seg,
        "blocks": {
            "p1": f"Some translated prose with a note {FN_PH} attached. Hand-fixed after the cap.",
            "vblockA": V_PH_A,
        },
        "footnotes": {"1": "A translated note in English."},
        "verses": {
            "vA": {
                "rendered": "First line rendered so\nSecond line rendered so",
                "literal_gloss": "The first line means one thing, the second means another",
            },
        },
        "names": [],
        "notes": [],
        "dispatch_token": dispatch_token,
    }


def make_claim_capable_root(tmp_path, seg="seg01", name="claim_root"):
    """A durable_root the REAL select_segments.py can admit a --from-cap
    claim in: every sibling it shells out to is the real shipped script
    (ledger_merge.py, draft_ready.py, validate_draft.py, claim_record.py)
    except cache_key.py, plus a single segment in the P2
    population -- materialized ledger non_converged/reason=cap, NO
    .ever_converged sentinel, a stored review that is clean:false WITH
    findings, and a draft whose bytes were hand-edited after the cap while
    its dispatch_token still names the run it was dispatched under."""
    root = tmp_path / name
    scripts_dir = root / "scripts"
    scripts_dir.mkdir(parents=True)
    for src in (SELECT_SEGMENTS_SRC, LEDGER_MERGE_SRC,
                DRAFT_READY_SRC, VALIDATE_DRAFT_SRC, CLAIM_RECORD_SRC):
        shutil.copy2(src, scripts_dir / src.name)
    (scripts_dir / "cache_key.py").write_text(FAKE_CACHE_KEY_PY, encoding="utf-8")
    shutil.copytree(SCHEMAS_SRC, root / "schemas")

    (root / "runs" / "ledger.d").mkdir(parents=True)
    (root / "segments").mkdir()
    profile_path = root / "profile.yml"
    profile_path.write_text(CLAIM_PROFILE_YAML, encoding="utf-8")
    (root / ".literary-translator-root.json").write_text(
        json.dumps({"owner_profile_path": str(profile_path)}), encoding="utf-8"
    )
    (root / "canon.json").write_text(json.dumps({"entries": {}}), encoding="utf-8")
    (root / "manifest.json").write_text(
        json.dumps({"segments": [{"seg": seg}]}, ensure_ascii=False), encoding="utf-8"
    )
    (root / "test_fixture_cache_keys.json").write_text(
        json.dumps({seg: {f: f"{f}-{seg}" for f in CACHE_KEY_FIELDS}}), encoding="utf-8"
    )

    (root / "segments" / f"segpack_{seg}.json").write_text(
        json.dumps(claim_segpack(seg), ensure_ascii=False), encoding="utf-8"
    )
    write_draft_doc(root, seg, claim_draft(seg, f"{CLAIM_SOURCE_RUN_ID}:{seg}"))
    (root / "segments" / f"{seg}.review.json").write_text(
        json.dumps({
            "clean": False, "coverage_ok": True,
            "findings": [{"loc": "p1", "severity": "medium",
                          "issue": "awkward phrasing", "suggest": "rephrase"}],
            "draft_sha1": "0" * 40,
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    (root / "runs" / "ledger.d" / f"{seg}.json").write_text(
        json.dumps({"timestamp": "2026-01-01T00:00:00Z", "status": "non_converged",
                    "reason": "cap", "rounds": 4}, sort_keys=True),
        encoding="utf-8",
    )

    # S3 requires runs/<source_run_id>/ to exist; #409 Step 3 separately
    # requires an input.digest for any id that carries dispatch evidence, and
    # the draft's own dispatch_token IS dispatch evidence for the source run.
    for run_id in (CLAIM_SOURCE_RUN_ID, CLAIM_RUN_ID):
        run_dir = root / "runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "input.digest").write_text(
            json.dumps({"digest": f"stub-{run_id}"}), encoding="utf-8"
        )
    return root


def write_draft_doc(root, seg, doc):
    (root / "segments" / f"{seg}.draft.json").write_text(
        json.dumps(doc, ensure_ascii=False), encoding="utf-8"
    )


def read_draft_doc(root, seg):
    return json.loads((root / "segments" / f"{seg}.draft.json").read_text(encoding="utf-8"))


def run_select(root, *extra_args, timeout=60):
    return subprocess.run(
        [sys.executable, str(root / "scripts" / "select_segments.py"), *extra_args],
        capture_output=True, text=True, timeout=timeout, cwd=str(root),
    )


def test_lost_token_recovery_runs_the_command_draft_ready_advertises(tmp_path):
    """D9 end to end, across the seam Part 1 and Part 2 each only saw one
    side of: CLAIM (real selector) -> a fix round DROPS dispatch_token ->
    draft_ready.py REFUSES and prints a remedy -> that exact remedy is run
    (real selector again) -> the token is restored and the segment is READY.

    The second invocation's run id and profile are PARSED OUT OF
    draft_ready.py's own stdout, never taken from this file's constants.
    That is the whole point: the assertion is not "a re-claim works", it is
    "the command this tool tells an operator to run is the command that
    works". A message that named a different run id, or a profile the
    selector would refuse, passes every test in Part 1 and fails here.

    THE MUTATIONS THAT MAKE THIS FAIL, one per hop:
      * delete select_segments.py's evaluate_lost_token_recovery() call from
        its S3 branch (i.e. restore the pre-#438 "refuse a token-less draft
        outright") -- the second run_select() exits 1 and the token is never
        restored. This is the state the whole feature shipped in until this
        release, with Parts 1 and 2 green;
      * make that recovery accept a DIFFERENT profile than the record's --
        caught by the profile assertion below only because the profile fed
        back in comes from the message;
      * drop `--run-id {run_id!r}` from draft_ready.py's remedy sentence --
        the regex below finds nothing and the test fails at the parse, which
        is the correct place to fail: an operator would have had nothing to
        run either;
      * let the re-claim overwrite the durable record instead of taking
        write_claim_record()'s "already claimed by this run" branch -- the
        byte-comparison of the record fails.

    Nothing is mocked. Node is not involved (Part 3's dependency, not
    this one's)."""
    seg = "seg01"
    root = make_claim_capable_root(tmp_path, seg=seg)
    token = f"{CLAIM_RUN_ID}:{seg}"

    # ---- hop 1: the real claim ------------------------------------------
    # --run-resume false: CLAIM_RUN_ID is a freshly minted id here (its
    # input.digest is seeded exactly as resume_setup.py would leave one), and
    # select_segments.py requires the --run-id/--run-resume pair together.
    claim = run_select(root, "--only-segs", seg, "--from-cap", seg,
                       "--run-id", CLAIM_RUN_ID, "--run-resume", "false")
    assert claim.returncode == 0, f"stdout={claim.stdout!r} stderr={claim.stderr!r}"
    assert read_draft_doc(root, seg)["dispatch_token"] == token, (
        "the claim must re-stamp the draft before this test can lose the token"
    )
    cr = _claim_record_module()
    record_path = cr.claimed_path(CLAIM_RUN_ID, seg, root / "runs")
    assert record_path.is_file(), f"no claim record at {record_path}"
    record_before = record_path.read_bytes()

    # ---- hop 2: a fix round drops the token ------------------------------
    # draft.schema.json makes dispatch_token OPTIONAL, so a re-emitted draft
    # simply loses the field -- exactly what Part 3's prompt line exists to
    # prevent and what this recovery exists to repair. Every other byte is
    # left alone, so draft_content_sha1() (which projects dispatch_token out)
    # is unchanged, which is what lets the re-claim's own staged-file
    # identity check pass.
    fixed = read_draft_doc(root, seg)
    del fixed["dispatch_token"]
    write_draft_doc(root, seg, fixed)

    # ---- hop 3: draft_ready.py refuses, and advertises the remedy --------
    refusal = run_draft_ready(root, seg, "--expect-token", token)
    assert refusal.returncode == 1, f"stdout={refusal.stdout!r}"
    assert "the claim was LOST" in refusal.stdout, refusal.stdout
    advertised_run_id = re.search(r"--run-id '([^']+)'", refusal.stdout)
    advertised_profile = re.search(r"profile='([^']+)'", refusal.stdout)
    assert advertised_run_id is not None, (
        f"the remedy must name the run id to re-claim under: {refusal.stdout!r}"
    )
    assert advertised_profile is not None, (
        f"the remedy must name the profile to re-claim under: {refusal.stdout!r}"
    )
    assert "select_segments.py" in refusal.stdout, (
        f"the remedy must name the script that performs it: {refusal.stdout!r}"
    )

    # ---- hop 4: run EXACTLY what the message said ------------------------
    # The profile string in the message is the record's own value; it maps to
    # the selector flag of the same name. Looked up rather than hardcoded so
    # a record carrying an unknown profile fails HERE, naming it, instead of
    # silently exercising --from-cap regardless of what was advertised.
    profile_flag = {
        "from-cap": "--from-cap",
        "from-converged": "--from-converged",
        "from-stalled": "--from-stalled",
    }
    flag = profile_flag.get(advertised_profile.group(1))
    assert flag is not None, (
        f"draft_ready.py advertised profile {advertised_profile.group(1)!r}, which is not "
        f"a profile select_segments.py offers a flag for -- the remedy is unrunnable"
    )
    # --run-resume true: this id is no longer fresh. resume_setup.py would
    # find runs/<id>/input.digest and report a RESUME, and the driver forwards
    # that verdict verbatim -- so 'true' is what a real re-claim of an
    # existing run carries, not a convenience.
    recovery = run_select(root, "--only-segs", seg, flag, seg,
                          "--run-id", advertised_run_id.group(1), "--run-resume", "true")
    assert recovery.returncode == 0, (
        f"the recovery draft_ready.py itself advertises must WORK. "
        f"stdout={recovery.stdout!r} stderr={recovery.stderr!r}"
    )

    # ---- hop 5: what the operator was promised, on disk -------------------
    assert read_draft_doc(root, seg)["dispatch_token"] == token, (
        "the advertised recovery promises 'it re-stamps the token' -- the draft must "
        "carry this run's token again"
    )
    assert "lost-token recovery" in recovery.stderr, (
        f"a recovery must never be silent: {recovery.stderr!r}"
    )
    assert record_path.read_bytes() == record_before, (
        "the re-claim is the SAME authorization reapplied, not a second one -- the "
        "durable record, including its pre-claim baseline, must be byte-identical"
    )
    ready = run_draft_ready(root, seg, "--expect-token", token)
    assert ready.returncode == 0, (
        f"after the advertised recovery the segment must actually be READY: "
        f"stdout={ready.stdout!r} stderr={ready.stderr!r}"
    )
    assert f"[{seg}] READY" in ready.stdout


def test_draft_ready_reports_a_FOREIGN_claim_as_superseded_not_lost(tmp_path):
    """A segment claimed by run A, then claimed OUT FROM UNDER it by run B --
    where B itself holds a LIVE claim record -- must be reported to the
    operator as SUPERSEDED -- never as the "claim was LOST" case, whose
    advertised remedy is to re-claim under run A.

    This is the operator-facing half of the cross-run ownership defect a third
    review round found. Run A's own claim record survives run B's claim (nothing
    releases a claim), and the draft's token no longer matches A's, so the state
    is byte-for-byte the one the LOST branch was written to explain. It read that
    state as "a fix round dropped my token" and told the operator to re-claim
    under A -- which is precisely the reclaim the selector refuses when B's own
    claim outranks A's. Following that advice could only ever produce a second,
    more confusing failure.

    A LATER review round found this fixture asserting SUPERSEDED/"NOT the fix"
    while never actually publishing a claim record for B -- i.e. asserting the
    live-record branch while building the record-absent one. `_claim_note()`
    now looks B's OWN record up before saying anything (see its docstring), so
    the fixture has to make that record real or it exercises a different branch
    entirely (covered by test_draft_ready_treats_a_foreign_token_with_no_
    claim_record_as_recoverable_not_refused below). B's claim record is
    published directly through the same real, shipped claim_record.py Part 1
    already drives via write_real_claim() -- not by re-running the real
    selector a second time, which would need a second, independently satisfied
    #409 evidence-scan precondition this test has no stake in establishing.

    THE MUTATION THAT MAKES THIS FAIL: delete the `actual_run_id != run_id`
    branch from draft_ready.py's `_claim_note()` CLAIM_PRESENT arm, i.e. restore
    the single LOST message for every present record. The LOST assertion below
    then finds its string and the test fails on it. (A narrower mutation --
    deleting just the `foreign_state == claim_record.CLAIM_PRESENT` return and
    letting it fall through to the CLAIM_ABSENT wording -- also fails this
    test's SUPERSEDED/"NOT the fix" assertions, while leaving the sibling test
    below green, which is exactly the boundary between the two tests.)

    Run A's claim is minted by the real select_segments.py; run B's claim
    record is published by the real claim_record.py directly. The real
    draft_ready.py is driven as a subprocess."""
    seg = "seg01"
    root = make_claim_capable_root(tmp_path, seg=seg)
    token_a = f"{CLAIM_RUN_ID}:{seg}"
    run_b = "20260813T000000Z"
    token_b = f"{run_b}:{seg}"

    # ---- run A takes the segment, for real -------------------------------
    claim = run_select(root, "--only-segs", seg, "--from-cap", seg,
                       "--run-id", CLAIM_RUN_ID, "--run-resume", "false")
    assert claim.returncode == 0, f"stdout={claim.stdout!r} stderr={claim.stderr!r}"
    assert read_draft_doc(root, seg)["dispatch_token"] == token_a
    cr = _claim_record_module()
    assert cr.claimed_path(CLAIM_RUN_ID, seg, root / "runs").is_file()

    # ---- run B claims it out from under A, for real -----------------------
    # The TOKEN move is a direct edit (draft_content_sha1() projects
    # dispatch_token out, so this transition is invisible to every content
    # hash in the system, and A's record is left exactly where it was) --
    # but B's own CLAIM RECORD is published for real, through the identical
    # write_claim_record() path the selector itself uses. Skipping this used
    # to be the fixture's own bug: it asserted B "has since claimed this
    # segment legitimately" while never writing evidence that B claimed
    # anything at all.
    doc = read_draft_doc(root, seg)
    doc["dispatch_token"] = token_b
    write_draft_doc(root, seg, doc)
    # B's claimed_at is derived from A's ACTUAL record rather than pinned to a
    # literal date. A's claim is minted by the real selector, so its claimed_at
    # is the REAL CLOCK -- and a hardcoded literal on B's side is therefore a
    # dated time bomb, not a fixed fixture: it holds only until the wall clock
    # passes it, after which A becomes the later claim and this test silently
    # exercises the RECOVERY branch while still asserting SUPERSEDED. (It was
    # written as "2026-08-13T00:00:00Z" and had four days left when a review
    # caught it.) Reading A's own record and adding one second makes B provably
    # later than A whenever the test runs, which is the property the test needs
    # -- and it keeps A minted by the real write path rather than trading the
    # bomb away for a weaker fixture.
    a_state, a_payload, a_detail = cr.read_claim_record(
        cr.claimed_path(CLAIM_RUN_ID, seg, root / "runs"))
    assert a_state == cr.CLAIM_PRESENT, (
        f"fixture setup: A's own record unreadable: {a_state} {a_detail}")
    a_at = datetime.strptime(a_payload["claimed_at"], "%Y-%m-%dT%H:%M:%SZ")
    b_at = (a_at + timedelta(seconds=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    write_real_claim(root, run_b, seg, profile=FOREIGN_CLAIM_PROFILE,
                      source_run_id=CLAIM_RUN_ID, previous_dispatch_token=token_a,
                      claimed_at=b_at)

    # ---- A's readiness probe must not call this "lost" --------------------
    refusal = run_draft_ready(root, seg, "--expect-token", token_a)
    assert refusal.returncode == 1, f"stdout={refusal.stdout!r}"
    out = refusal.stdout

    # The load-bearing assertion: this is the exact state the LOST branch used
    # to claim, so its ABSENCE is what proves the two cases are now told apart.
    assert "the claim was LOST" not in out, (
        "run B owns this segment; reporting it as a LOST claim sends the "
        f"operator to re-claim under run A, which the selector refuses: {out!r}"
    )
    assert run_b in out, f"the refusal must name the run that owns it now: {out!r}"
    assert "SUPERSEDED" in out, f"A's record must be called superseded: {out!r}"
    assert "NOT the fix" in out, (
        f"re-claiming under A must be named as NOT the remedy: {out!r}"
    )
    # #438 round 6 REVERSED round 5's contract here, and the old assertion
    # survived the reversal only because it was VACUOUS -- it searched for a
    # lowercase "it will be refused" while the message says "the reclaim WILL
    # be refused", so it passed no matter what the note said. Round 5's rule
    # was that the note must not promise a verdict, because it could not see
    # the evidence the selector decides on. Round 6 removed that premise: the
    # note now reads BOTH claim records and performs the identical comparison
    # the guard performs, so on this branch the verdict IS established here,
    # and hedging it would understate what the operator can rely on. Asserted
    # POSITIVELY, in the message's own casing, so it cannot go vacuous again.
    assert "WILL be refused" in out, (
        f"both claim ages are known here, so the note must state the "
        f"selector's verdict rather than hedge it: {out!r}"
    )
    assert "neither a guaranteed refusal" not in out, (
        f"that hedge belongs to the UNDETERMINED branch, where the ages "
        f"cannot be compared -- not here, where they can: {out!r}"
    )
    # A's profile and claimed_at are read off A's OWN record rather than
    # restated here: A's claim is minted by the real selector, so both are
    # whatever it actually wrote.
    assert_records_are_not_swapped(
        out, seg=seg, run_id=CLAIM_RUN_ID, profile=a_payload["profile"],
        claimed_at=a_payload["claimed_at"], foreign_run_id=run_b,
        foreign_profile=FOREIGN_CLAIM_PROFILE, foreign_claimed_at=b_at,
    )


def test_draft_ready_reports_this_runs_later_claim_as_the_remedy_not_superseded(tmp_path):
    """The case round 5's FOREIGN/CLAIM_PRESENT wording got wrong: it is
    unconditional, but the selector's own reclaim guard
    (rewrite_draft_dispatch_token(), round 5) does not unconditionally
    refuse a foreign-looking token any more -- it ADMITS a retry whenever
    THIS run's own claimed_at is provably later than the foreign run's,
    which is exactly the D9 crash-between-claim-and-stamp recovery: this
    run claims the segment (its own record is written FIRST, by design),
    then crashes before the rewrite ever stamps the draft's dispatch_token,
    leaving the draft still naming whichever run held it before. Run B's
    own claim record here is real and OLDER than run A's -- B is not who
    the segment belongs to any more, A is -- so telling A's retry it is
    "superseded" and "NOT the fix" is exactly backwards: the selector will
    ADMIT that retry, not refuse it.

    THE MUTATION THAT MAKES THIS FAIL, OBSERVED: reverting _claim_note()'s
    foreign/CLAIM_PRESENT branch to the pre-round-6 unconditional SUPERSEDED
    return (deleting the this_instant/foreign_instant comparison entirely)
    makes this test's SUPERSEDED/"NOT the fix" absence-assertions find their
    strings and fail -- confirmed by temporarily applying that revert and
    running this test alone; it failed on the SUPERSEDED assertion. The
    sibling SUPERSEDED test above stays green either way, since its own
    fixture's foreign claim really is the later one."""
    root = make_durable_root(tmp_path)
    run_id = "20260808T000000Z"
    seg = "seg01"
    expect_token = f"{run_id}:{seg}"
    foreign_run_id = "20260805T000000Z"
    # This run's own claim is the LATER of the two -- the selector's own
    # guard admits a retry on exactly this fact.
    this_claimed_at = "2026-08-08T10:00:00Z"
    foreign_claimed_at = "2026-08-08T09:00:00Z"
    _, payload = write_real_claim(root, run_id, seg, profile="from-cap",
                                   claimed_at=this_claimed_at)
    write_real_claim(root, foreign_run_id, seg, profile=FOREIGN_CLAIM_PROFILE,
                      claimed_at=foreign_claimed_at)
    draft = clean_draft(seg, dispatch_token=f"{foreign_run_id}:{seg}")
    write_segment(root, seg, clean_segpack(), draft)

    result = run_draft_ready(root, seg, "--expect-token", expect_token)

    assert result.returncode == 1, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    out = result.stdout
    assert foreign_run_id in out, f"the refusal must name the foreign run on the token: {out!r}"
    assert "SUPERSEDED" not in out, (
        f"this run's own claim is the LATER one -- it must not be called "
        f"superseded: {out!r}"
    )
    assert "NOT the fix" not in out, (
        f"the retry IS the remedy the selector will admit here, not a "
        f"refused reclaim: {out!r}"
    )
    assert "the claim was LOST" not in out, (
        f"this is reached through the FOREIGN branch and characterised by "
        f"claim age, not the plain LOST branch: {out!r}"
    )
    assert "crashed before stamping" in out, (
        f"the refusal should name the crash-recovery shape this run is in: {out!r}"
    )
    assert "remedy" in out, f"the refusal must point at the retry as the remedy: {out!r}"
    # Was `assert payload["profile"] in out`, which proved nothing here: the
    # foreign record carried the same profile, so that string was in the output
    # whichever record it came from. Both records' facts are now pinned to
    # their own run id.
    assert_records_are_not_swapped(
        out, seg=seg, run_id=run_id, profile=payload["profile"],
        claimed_at=this_claimed_at, foreign_run_id=foreign_run_id,
        foreign_profile=FOREIGN_CLAIM_PROFILE,
        foreign_claimed_at=foreign_claimed_at,
    )


def test_draft_ready_reports_a_tie_in_claim_age_as_permanent_not_a_retry(tmp_path):
    """A claimed_at TIE is the one outcome a same-run retry can NEVER fix:
    claim_record.py's write is exclusive (see
    test_write_claim_record_is_exclusive_so_a_reclaim_cannot_rewrite_the_
    baseline above), so re-running the claim step reuses THIS run's
    EXISTING record untouched -- its claimed_at cannot change no matter how
    many times it is retried. The refusal must say so explicitly rather
    than folding a tie into the ordinary SUPERSEDED wording (which at least
    implies retrying is pointless for a DIFFERENT reason) or, worse, into
    wording that could read as "try again".

    THE MUTATION THAT MAKES THIS FAIL, OBSERVED: changing draft_ready.py's
    tie-branch guard from `this_instant < foreign_instant` (strict) to
    `this_instant <= foreign_instant` folds an equal claimed_at into the
    SUPERSEDED branch instead of the dedicated TIE branch. Applying that
    one-character edit and running this test alone failed the "TIE" /
    "PERMANENT" presence assertions below (the SUPERSEDED wording appeared
    instead)."""
    root = make_durable_root(tmp_path)
    run_id = "20260808T000000Z"
    seg = "seg01"
    expect_token = f"{run_id}:{seg}"
    foreign_run_id = "20260813T000000Z"
    tied_claimed_at = "2026-08-08T09:00:00Z"
    write_real_claim(root, run_id, seg, profile="from-cap", claimed_at=tied_claimed_at)
    # The tie fixture is the one where claimed_at CANNOT discriminate the two
    # records -- it is identical by construction -- so the profile is the only
    # thing that can, and it has to differ.
    write_real_claim(root, foreign_run_id, seg, profile=FOREIGN_CLAIM_PROFILE,
                      claimed_at=tied_claimed_at)
    draft = clean_draft(seg, dispatch_token=f"{foreign_run_id}:{seg}")
    write_segment(root, seg, clean_segpack(), draft)

    result = run_draft_ready(root, seg, "--expect-token", expect_token)

    assert result.returncode == 1, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    out = result.stdout
    assert "TIE" in out, f"the refusal must name this as a tie: {out!r}"
    assert "PERMANENT" in out, f"the refusal must say the tie cannot be broken by retrying: {out!r}"
    assert "SUPERSEDED" not in out, f"a tie is not the same as being outranked: {out!r}"
    assert "Do NOT re-run the claim step" in out, (
        f"the refusal must not read as advice to wait and retry -- a tied "
        f"claimed_at cannot change on a same-run retry: {out!r}"
    )
    assert "different --run-id" in out or "manual" in out, (
        f"the refusal should point at an actual way out of a tie (a "
        f"different run identity or a manual decision), not silence: {out!r}"
    )
    assert_records_are_not_swapped(
        out, seg=seg, run_id=run_id, profile="from-cap",
        claimed_at=tied_claimed_at, foreign_run_id=foreign_run_id,
        foreign_profile=FOREIGN_CLAIM_PROFILE,
        foreign_claimed_at=tied_claimed_at,
    )


def test_draft_ready_reports_an_unparseable_claim_age_as_undetermined(tmp_path):
    """A hand-edited or otherwise malformed claimed_at on either side makes
    the age comparison impossible to run at all. The refusal must say so
    (UNDETERMINED) rather than defaulting to either the SUPERSEDED or the
    later-claim-is-the-remedy wording -- the same safe direction
    claim_record.py's own three-state discipline uses everywhere else in
    this file, and the same direction select_segments.py's own
    _claim_record_claimed_at() takes (None on anything that does not parse,
    which this run's _claim_instant() mirrors).

    THE MUTATION THAT MAKES THIS FAIL, OBSERVED: changing _claim_instant()'s
    `except ValueError: return None` to instead `return value` (a
    fall-through meant to compare the raw, unparsed strings lexically
    instead of refusing to compare at all). Applying that edit and running
    this test alone did NOT produce the lexical-comparison outcome
    predicted in an earlier draft of this comment -- `this_instant >
    foreign_instant` then compares a bare `str` (this run's unparsed
    value) against a `datetime` (the foreign run's, which parsed cleanly),
    and Python's `>` refuses mixed str/datetime with an uncaught
    TypeError. That escapes _claim_note() uncaught (nothing downstream of
    the CLAIM_PRESENT age comparison is wrapped in a try/except), crashing
    draft_ready.py before any line is printed: stdout comes back empty and
    the UNDETERMINED assertion below fails against ''. Both the predicted
    and the actual failure are the SAME underlying point -- a lexical
    fallback here is unsound, whether it silently picks a wrong winner or
    crashes outright -- but only the actual, observed one is asserted."""
    root = make_durable_root(tmp_path)
    run_id = "20260808T000000Z"
    seg = "seg01"
    expect_token = f"{run_id}:{seg}"
    foreign_run_id = "20260813T000000Z"
    this_claimed_at = "not-a-real-timestamp"
    foreign_claimed_at = "2026-08-13T00:00:00Z"
    write_real_claim(root, run_id, seg, profile="from-cap",
                      claimed_at=this_claimed_at)
    write_real_claim(root, foreign_run_id, seg, profile=FOREIGN_CLAIM_PROFILE,
                      claimed_at=foreign_claimed_at)
    draft = clean_draft(seg, dispatch_token=f"{foreign_run_id}:{seg}")
    write_segment(root, seg, clean_segpack(), draft)

    result = run_draft_ready(root, seg, "--expect-token", expect_token)

    assert result.returncode == 1, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    out = result.stdout
    assert "UNDETERMINED" in out, f"the refusal must say ownership could not be established: {out!r}"
    assert "does not parse as a timestamp" in out, f"the refusal must name the malformed side: {out!r}"
    assert "this run's own claim record's claimed_at" in out, (
        f"the refusal must name WHICH side is unparseable: {out!r}"
    )
    assert "SUPERSEDED" not in out, (
        f"a malformed claimed_at must not be silently treated as a definite "
        f"outcome: {out!r}"
    )
    assert "NOT the fix" not in out, (
        f"neither definite outcome may be asserted when the comparison "
        f"cannot run at all: {out!r}"
    )
    assert_records_are_not_swapped(
        out, seg=seg, run_id=run_id, profile="from-cap",
        claimed_at=this_claimed_at, foreign_run_id=foreign_run_id,
        foreign_profile=FOREIGN_CLAIM_PROFILE,
        foreign_claimed_at=foreign_claimed_at,
    )


def test_a_token_less_draft_with_no_claim_record_is_still_refused(tmp_path):
    """The control the recovery test needs, and the reason the recovery is
    not a general hole: the SAME token-less draft, with no claim record for
    the run, must still be refused by the selector. If this ever passes, D9's
    recovery has stopped being "this run's own record authorizes restoring
    the token it wrote" and become "a missing token is fine"."""
    seg = "seg01"
    root = make_claim_capable_root(tmp_path, seg=seg)
    fixed = read_draft_doc(root, seg)
    del fixed["dispatch_token"]
    write_draft_doc(root, seg, fixed)

    proc = run_select(root, "--only-segs", seg, "--from-cap", seg,
                      "--run-id", CLAIM_RUN_ID, "--run-resume", "false")

    assert proc.returncode == 1, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert "no dispatch_token" in proc.stdout, proc.stdout
    assert not (root / "runs" / CLAIM_RUN_ID / f".claimed.{seg}").exists(), (
        "a refused admission must not leave a claim record behind"
    )


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
# #607 -- was "" ("not opted into the redirect"). The W5 template now REFUSES
# to start without a plugin root, because the fix-scope audit runs only from
# the plugin install tree, so every fixture that executes the workflow needs a
# real value. Tests that specifically exercise the opt-out/absent-redirect
# shape pass plugin_root="" explicitly at their own call site.
FIXTURE_PLUGIN_ROOT = "/fixture/plugin/literary-translator"


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
  // #607 -- the fix-scope audit relay. This file is about the fix prompt's
  // TEXT (the claimed dispatch_token must survive byte for byte), never about
  // the audit's verdict, so a clean pass is the right constant; the mismatch
  // and relay-failure branches belong to fix_scope_gate.test.py.
  if (label === "fix-scope:" + SEG + ":r1") return { ok: true, n_checked: 79, n_expected: 79 };
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
    mass-translate-wf.template.js:1288): the claim survives a fix round
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
    # #607 inserted ONE call here, and this assertion was widened rather than
    # loosened. The docstring above forbids fixing a RED by relaxing the
    # check, so the question it poses was answered instead: does the new call
    # retire codex_job.py's chokepoint? It does not. The fix-scope audit runs
    # `fix_scope_audit.py`, which compares the durable root's PLUGIN-INSTALLED
    # copies against the plugin install tree. It never runs
    # `draft_ready.py --expect-token`, never reads the draft's dispatch_token,
    # and renders no verdict on whether a claimed draft is valid or present --
    # which is the thing D9 says must stay codex_job.py's alone. The two
    # assertions that encode the actual protection (no `wait:` re-poll, and
    # the token-carrying prompt line pinned byte for byte above) are
    # unchanged and still pass.
    assert labels[fix_idx + 1] == "fix-scope:" + seg + ":r1", (
        f"#607: the fix-scope audit must be the FIRST thing after the fix -- "
        f"before the reply is even inspected; got: {labels}"
    )
    assert labels[fix_idx + 2] == "review-dispatch:" + seg + ":r2", (
        f"the next round's review dispatch must follow the audit, with "
        f"nothing else in between; got: {labels}"
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
    "in_progress"})` (mass-translate-wf.template.js:1786) completes BEFORE
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
