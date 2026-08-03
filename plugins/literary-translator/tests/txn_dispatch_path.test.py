"""tests/txn_dispatch_path.test.py -- #409 track B: the DRIVER side of the
merged round. Dispatching --kind fixreview, publishing the validated pair, and
the four bounds that keep the mode's spend inside the number its admission was
checked against.

tests/txn_publish.test.py owns the renames themselves and tests/txn_glue_
plumbing.test.py owns the argv/outcome plumbing. What is new here is everything
BETWEEN them: which kind a round dispatches, the pre-derive recovery phase and
the lease it runs under, minting an intent from real pre-image state, and the
per-round counters.

The bounds get more attention than the happy path on purpose. A bound that is
merely asserted in a docstring and never exercised is indistinguishable from an
unbounded path, and three of the four here exist precisely because the earlier
design named an allowance that nothing counted.
"""
from __future__ import annotations

import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
SCRIPTS_SRC_DIR = HERE.parent / "skills" / "literary-translator" / "assets" / "scripts"
DRIVER_SRC = SCRIPTS_SRC_DIR / "segment_dispatch_driver.py"
assert DRIVER_SRC.is_file(), f"expected script not found: {DRIVER_SRC}"


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, str(path))
    assert spec is not None and spec.loader is not None, f"cannot load {path}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


DRIVER = _load_module(DRIVER_SRC, "segment_dispatch_driver_txn_dispatch")
DRAFT_SHA1 = _load_module(SCRIPTS_SRC_DIR / "draft_sha1.py", "draft_sha1_for_dispatch_path")

SEG = "seg01"
RUN = "20260101T000000Z"
DRAFT_TOKEN = f"{RUN}:{SEG}"


def _ctx(tmp_path, fix_mode=None, **cfg):
    root = tmp_path / "root"
    (root / "segments").mkdir(parents=True, exist_ok=True)
    (root / "runs").mkdir(parents=True, exist_ok=True)
    translate_cfg = {
        "max_fix_rounds": 2, "effort": "high", "model": "",
        "max_rejected_candidates_per_round": 2,
        "max_txn_failures_per_segment": 3,
        # _template_subst() reads all of these before any stub can intercept,
        # so a partial config fails inside the driver rather than in the test.
        "source_lang": "he", "target_lang": "en",
        "batch_agent_cap": 100, "max_codex_jobs_per_batch": 400,
        "verse_policy": {"mode": "literal_only"},
    }
    translate_cfg.update(cfg)
    return DRIVER.DispatchContext(
        dirs={
            "durable_root": root, "runs_dir": root / "runs", "scripts_dir": SCRIPTS_SRC_DIR,
            # derive_next_action() looks these up before it calls anything, so
            # they have to be present even when the gates themselves are stubbed.
            "draft_ready_script": SCRIPTS_SRC_DIR / "draft_ready.py",
            "validate_draft_script": SCRIPTS_SRC_DIR / "validate_draft.py",
            "template_script": SCRIPTS_SRC_DIR.parent / "templates" / "mass-translate-wf.template.js",
        },
        run_id=RUN, translate_cfg=translate_cfg, companion_path="/c.mjs",
        durable_root_str=str(root), plugin_root_str=None, node_bin="node",
        session_id="S", fix_mode=fix_mode or DRIVER.FIX_MODE_CODEX,
    )


def _write_canonical(ctx, *, draft_body="old", review=None):
    draft = {"seg": SEG, "dispatch_token": DRAFT_TOKEN, "blocks": {"b": draft_body}}
    (ctx.segments_dir / f"{SEG}.draft.json").write_text(
        json.dumps(draft, ensure_ascii=False), encoding="utf-8")
    if review is not None:
        (ctx.segments_dir / f"{SEG}.review.json").write_text(
            json.dumps(review, ensure_ascii=False), encoding="utf-8")
    return draft


def _seed_fixreview_inputs(ctx):
    """Writes small, real placeholder content for the FOUR files
    DRIVER.fixreview_context_estimate() sizes -- the segpack, the draft (only
    if no test-specific draft is already there -- never clobbers one
    _write_canonical() already wrote), style_bible.md, and review_TASK.md --
    comfortably within DRIVER.DEFAULT_MAX_FIXREVIEW_CONTEXT_TOKENS.

    #409 track B, the size fallback (R_size). This helper exists because
    that fallback is FAIL CLOSED: a codex-mode ctx whose four inputs are not
    all present now falls back to handoff for every decision
    (DRIVER.effective_fix_mode()), which is correct in PRODUCTION -- a
    segment never reaches a review/needs_fix decision before its segpack and
    the project's style_bible.md/review_TASK.md exist -- but is a real
    behaviour change for every test in this file that drives
    process_segment()/derive_next_action() in --fix-mode=codex through a
    STUBBED derive_next_action/decision_premise without also creating real
    backing files. Call this explicitly wherever a test needs codex mode to
    actually stay in effect; never from _ctx() itself, since several tests
    below (e.g. test_a_genuinely_absent_draft_stays_observable,
    test_an_absent_segpack_or_profile_is_stable_rather_than_flapping) depend
    on _ctx() leaving the draft/segpack genuinely absent."""
    if not (ctx.segments_dir / f"{SEG}.draft.json").exists():
        _write_canonical(ctx)
    segpack = ctx.segments_dir / f"segpack_{SEG}.json"
    if not segpack.exists():
        segpack.write_text('{"blocks": {"b": "source"}}', encoding="utf-8")
    style_bible = ctx.dirs["durable_root"] / "style_bible.md"
    if not style_bible.exists():
        style_bible.write_text("# Style Bible\n\nPlaceholder for tests.\n", encoding="utf-8")
    review_task = ctx.dirs["durable_root"] / "review_TASK.md"
    if not review_task.exists():
        review_task.write_text("# Review Task\n\nPlaceholder for tests.\n", encoding="utf-8")


def _stage_candidates(ctx, *, draft_body="fixed", clean=False, round_label="1"):
    """What a completed --kind fixreview leaves behind: two validated
    candidates at private per-invocation paths, plus the five staged_* fields
    that are the only pointer to them."""
    draft = {"seg": SEG, "dispatch_token": DRAFT_TOKEN, "blocks": {"b": draft_body}}
    review = {"clean": clean, "coverage_ok": True, "findings": [],
              "draft_sha1": "0" * 40,
              "dispatch_token": f"{RUN}:{SEG}:r{round_label}"}
    dpath = ctx.segments_dir / f".att.{SEG}.abcd.draft.json"
    rpath = ctx.segments_dir / f".att.{SEG}.abcd.review.json"
    dpath.write_text(json.dumps(draft, ensure_ascii=False), encoding="utf-8")
    rpath.write_text(json.dumps(review, ensure_ascii=False), encoding="utf-8")
    return {
        "ok": True, "reason": "staged", "error_detail": None, "staged": True,
        "staged_draft_path": str(dpath), "staged_review_path": str(rpath),
        "staged_draft_sha256": hashlib.sha256(dpath.read_bytes()).hexdigest(),
        "staged_review_sha256": hashlib.sha256(rpath.read_bytes()).hexdigest(),
    }


def _capture_intent(monkeypatch):
    """Record the intent as it is written, without preventing the write.

    The intent is deleted by cleanup on the way out of a successful
    transaction, so reading it off disk afterwards observes nothing -- these
    are properties of the record as MINTED."""
    captured = {}
    original = DRIVER.write_txn_intent

    def _capture(txn_dir, seg, intent):
        captured.update(intent)
        return original(txn_dir, seg, intent)

    monkeypatch.setattr(DRIVER, "write_txn_intent", _capture)
    return captured


def _publish(ctx, round_label, result, premise=None):
    """publish_fixreview_pair() with the premise its caller captures under the
    lease. Defaulted to "the state right now" so each test states only what it
    actually varies -- a test that wants a STALE premise passes one."""
    if premise is None:
        premise = DRIVER.decision_premise(ctx, SEG)
    return DRIVER.publish_fixreview_pair(ctx, SEG, round_label, result, premise)


def _canonical(ctx, what):
    path = ctx.segments_dir / f"{SEG}.{what}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def _canonical_present(ctx, what) -> dict:
    """_canonical(), narrowed. Every call site below that reads a specific
    field off the result (as opposed to comparing the whole object, or
    checking `is None` for absence) asserts the file exists first --
    pyright otherwise treats every one of those reads as subscripting
    Optional[dict], since _canonical() itself returns None on a genuinely
    absent file. Kept as a SEPARATE function rather than narrowing
    _canonical() itself: several call sites in this file use _canonical()
    specifically to assert ABSENCE (e.g. "no review was ever published"),
    and this helper's own assertion would make those fail for the wrong
    reason."""
    result = _canonical(ctx, what)
    assert result is not None, f"expected {what}.json to exist for {SEG}"
    return result


# ---------------------------------------------------------------------------
# which kind a round dispatches
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mode,label,expected", [
    (DRIVER.FIX_MODE_HANDOFF, "1", "review"),
    (DRIVER.FIX_MODE_HANDOFF, "final", "review"),
    (DRIVER.FIX_MODE_CODEX, "1", "fixreview"),
    (DRIVER.FIX_MODE_CODEX, "2", "fixreview"),
    (DRIVER.FIX_MODE_CODEX, "final", "review"),
])
def test_the_dispatch_kind_map_is_exactly_this(mode, label, expected):
    """All four cells named, including the two that must NOT change. The final
    round staying a plain review in codex mode is the load-bearing one: it is
    the confirming round that edits nothing, and a merged call there would let
    the run's last word be spoken by the same call that wrote the text."""
    assert DRIVER._dispatch_kind_for_round(mode, label) == expected


def test_a_not_clean_current_review_is_needs_fix_under_handoff_and_a_merged_round_under_codex(
        tmp_path, monkeypatch):
    """ONE state, read by both modes. Asserting them together is the point:
    the branch must not be a blanket replacement, because handoff's needs_fix
    is the default and the whole of the previous release's behaviour."""
    ctx = _ctx(tmp_path, fix_mode=DRIVER.FIX_MODE_HANDOFF)
    draft = _write_canonical(ctx)
    sha1 = DRAFT_SHA1.draft_content_sha1(ctx.segments_dir / f"{SEG}.draft.json")
    (ctx.segments_dir / f"{SEG}.review.json").write_text(json.dumps({
        "clean": False, "coverage_ok": True,
        "findings": [{"loc": "b", "severity": "major", "issue": "x", "suggest": "y"}],
        "draft_sha1": sha1, "dispatch_token": f"{RUN}:{SEG}:r1",
    }), encoding="utf-8")
    assert draft["dispatch_token"] == DRAFT_TOKEN

    monkeypatch.setattr(DRIVER, "_run_gate",
                        lambda script, argv_rest, c, *, supports_plugin_root: True)
    monkeypatch.setattr(DRIVER, "call_template_functions",
                        lambda dirs, subst, calls, node_bin="node": {"verdict": {"status": "ok"}})

    handoff = DRIVER.derive_next_action(SEG, ctx)
    codex_ctx = _ctx(tmp_path, fix_mode=DRIVER.FIX_MODE_CODEX)
    # #409 track B, the size fallback: codex_ctx's own merged_fix branch is
    # only reachable when effective_fix_mode() reads real codex mode, which
    # needs the segpack/style_bible.md/review_TASK.md this shares tmp_path's
    # draft with -- see _seed_fixreview_inputs()'s own docstring.
    _seed_fixreview_inputs(codex_ctx)
    codex = DRIVER.derive_next_action(SEG, codex_ctx)

    assert handoff["action"] == "needs_fix"
    assert handoff["round_label"] == "1"
    assert codex["action"] == "review"
    assert codex["cause"] == "merged_fix"
    # SAME label, not the next one: nothing has applied these findings, so
    # advancing the round would spend one over findings still outstanding.
    assert codex["round_label"] == "1"


# ---------------------------------------------------------------------------
# the lease
# ---------------------------------------------------------------------------


def test_the_lease_is_codex_jobs_own_lock_file_by_exact_path(tmp_path):
    """A second lock file of the driver's own would be an independent lease
    excluding nobody -- the whole property depends on it being THE lock
    codex_job.py takes."""
    ctx = _ctx(tmp_path)
    with DRIVER.segment_lease(ctx.segments_dir, SEG) as leased:
        assert leased is True
        assert (ctx.segments_dir / f".codex_job.{SEG}.lock").exists()


def test_a_lease_held_by_another_process_is_reported_not_waited_for(tmp_path):
    ctx = _ctx(tmp_path)
    lock_path = ctx.segments_dir / f".codex_job.{SEG}.lock"
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        with DRIVER.segment_lease(ctx.segments_dir, SEG) as leased:
            assert leased is False
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def test_the_lease_is_released_on_the_way_out(tmp_path):
    """Otherwise the very first segment would lock out every codex_job.py
    child this driver then launches."""
    ctx = _ctx(tmp_path)
    with DRIVER.segment_lease(ctx.segments_dir, SEG) as leased:
        assert leased is True
    with DRIVER.segment_lease(ctx.segments_dir, SEG) as again:
        assert again is True


def test_the_lease_is_released_even_when_the_body_raises(tmp_path):
    ctx = _ctx(tmp_path)
    with pytest.raises(RuntimeError):
        with DRIVER.segment_lease(ctx.segments_dir, SEG):
            raise RuntimeError("boom")
    with DRIVER.segment_lease(ctx.segments_dir, SEG) as again:
        assert again is True


# ---------------------------------------------------------------------------
# publishing a validated pair
# ---------------------------------------------------------------------------


def test_a_validated_pair_is_published_and_the_transaction_is_cleaned_up(tmp_path):
    ctx = _ctx(tmp_path)
    _write_canonical(ctx, review={"old": "review"})
    result = _stage_candidates(ctx)

    txn = _publish(ctx, "1", result)

    assert txn["ok"] is True, txn
    assert _canonical_present(ctx, "draft")["blocks"]["b"] == "fixed"
    assert _canonical_present(ctx, "review")["dispatch_token"] == f"{RUN}:{SEG}:r1"
    assert not DRIVER.txn_intent_path(ctx.txn_dir, SEG).exists()
    slots = DRIVER.staged_paths(ctx.txn_dir, SEG, "1")
    assert not slots["draft"].exists() and not slots["review"].exists()


def test_the_attempt_counter_survives_a_successful_transaction(tmp_path):
    """It is the id-uniqueness counter, not the failure counter: cleanup
    removes the intent and the staging and must not touch it, or the next
    transaction reuses the same txn_id."""
    ctx = _ctx(tmp_path)
    _write_canonical(ctx)
    assert _publish(ctx, "1", _stage_candidates(ctx))["ok"]
    assert json.loads((ctx.txn_dir / f"{SEG}.attempts").read_text())["attempt_seq"] == 1

    _stage_candidates(ctx, draft_body="fixed2", round_label="2")
    result2 = _stage_candidates(ctx, draft_body="fixed2", round_label="2")
    assert _publish(ctx, "2", result2)["ok"]
    assert json.loads((ctx.txn_dir / f"{SEG}.attempts").read_text())["attempt_seq"] == 2


def test_a_successful_transaction_charges_no_failure(tmp_path):
    """The counter must count FAILURES, not attempts. Conflating them makes a
    correctly-configured project trip its own ceiling on the ordinary path."""
    ctx = _ctx(tmp_path)
    _write_canonical(ctx)
    assert _publish(ctx, "1", _stage_candidates(ctx))["ok"]
    assert DRIVER.read_txn_failures(ctx.txn_dir, SEG)["count"] == 0


def test_the_review_preimage_records_ABSENT_when_there_is_no_canonical_review(tmp_path, monkeypatch):
    """Round 1 has no review yet, and {"absent": true} has to be spelled --
    recording a hash of nothing would make every round-1 transaction diverge
    against itself."""
    ctx = _ctx(tmp_path)
    _write_canonical(ctx)  # no review
    captured = _capture_intent(monkeypatch)
    assert _publish(ctx, "1", _stage_candidates(ctx))["ok"]
    assert captured["review_preimage"] == {"absent": True}


def test_the_intent_binds_the_draft_TOKEN_as_well_as_its_content(tmp_path, monkeypatch):
    """draft_sha1.py deliberately excludes dispatch_token from the hash, so a
    CAS over content alone waves through a competing write that changed only
    the token -- and derive_next_action() reads the token."""
    ctx = _ctx(tmp_path)
    _write_canonical(ctx)
    captured = _capture_intent(monkeypatch)
    _publish(ctx, "1", _stage_candidates(ctx))
    assert captured["pre_edit_draft_token"] == DRAFT_TOKEN
    assert captured["pre_edit_draft_sha1"] != DRAFT_TOKEN
    assert len(captured["pre_edit_draft_sha1"]) == 40


def test_the_preimage_describes_the_draft_BEFORE_the_edit_not_after(tmp_path, monkeypatch):
    """If the intent recorded the post-edit draft, the CAS would be comparing
    the staged bytes against themselves -- a guard comparing a value to
    itself, which is no guard."""
    ctx = _ctx(tmp_path)
    _write_canonical(ctx, draft_body="old")
    pre = DRAFT_SHA1.draft_content_sha1(ctx.segments_dir / f"{SEG}.draft.json")
    captured = _capture_intent(monkeypatch)
    _publish(ctx, "1", _stage_candidates(ctx, draft_body="fixed"))
    assert captured["pre_edit_draft_sha1"] == pre
    post = DRAFT_SHA1.draft_content_sha1(ctx.segments_dir / f"{SEG}.draft.json")
    assert post != pre, "the fixture must actually change the draft, or this proves nothing"


def test_a_competing_write_between_staging_and_publication_refuses_and_charges(tmp_path, monkeypatch):
    """The CAS the whole intent exists for. Nothing may be published, and the
    refusal must be charged so a segment cannot retry forever."""
    ctx = _ctx(tmp_path)
    _write_canonical(ctx, draft_body="old")
    result = _stage_candidates(ctx)

    original = DRIVER.write_txn_intent

    def _write_then_race(txn_dir, seg, intent):
        ok = original(txn_dir, seg, intent)
        # somebody else edits the canonical draft AFTER the intent is durable
        _write_canonical(ctx, draft_body="somebody-elses-edit")
        return ok

    monkeypatch.setattr(DRIVER, "write_txn_intent", _write_then_race)
    txn = _publish(ctx, "1", result)

    assert txn["ok"] is False
    assert txn["outcome"] == DRIVER.TXN_PREIMAGE_DIVERGED
    assert _canonical_present(ctx, "draft")["blocks"]["b"] == "somebody-elses-edit"
    assert _canonical(ctx, "review") is None
    assert DRIVER.read_txn_failures(ctx.txn_dir, SEG)["count"] == 1


def test_staged_bytes_that_do_not_match_the_validated_digest_never_reach_an_intent(tmp_path):
    """codex_job.py's digest describes the bytes its four gates checked; these
    are the bytes about to be renamed over the user's text. Carrying the
    validated digest into the intent while staging different bytes would make
    the swap invisible to every later check, since they all compare against
    the intent."""
    ctx = _ctx(tmp_path)
    _write_canonical(ctx)
    result = _stage_candidates(ctx)
    Path(result["staged_draft_path"]).write_text('{"tampered": true}', encoding="utf-8")

    txn = _publish(ctx, "1", result)

    assert txn["ok"] is False
    assert txn["reason"] == "txn-staging-copy-failed"
    assert not DRIVER.txn_intent_path(ctx.txn_dir, SEG).exists()
    assert _canonical_present(ctx, "draft")["blocks"]["b"] == "old"


@pytest.mark.parametrize("missing", [
    "staged_draft_path", "staged_review_path",
    "staged_draft_sha256", "staged_review_sha256",
])
def test_an_outcome_missing_any_staged_field_is_refused_before_anything_is_touched(tmp_path, missing):
    ctx = _ctx(tmp_path)
    _write_canonical(ctx)
    result = _stage_candidates(ctx)
    result[missing] = None

    txn = _publish(ctx, "1", result)

    assert txn["ok"] is False
    assert txn["reason"] == "txn-staged-fields-missing"
    assert not ctx.txn_dir.exists() or not any(ctx.txn_dir.iterdir())


def test_an_intent_that_appeared_since_recovery_is_not_published_over(tmp_path):
    """Recovery ran under the lease moments earlier and left nothing behind,
    so an intent here means one appeared since. Starting a second transaction
    over it would overwrite the only durable record of the first -- and the
    first may already have renamed the review, in which case its staged draft
    becomes unreachable and a half-published pair is stranded permanently.

    Found by mutation: without this branch every test still passed."""
    ctx = _ctx(tmp_path)
    _write_canonical(ctx, review={"old": "review"})
    ctx.txn_dir.mkdir(parents=True)
    other = {
        "txn_schema": 1, "txn_id": "SOMEBODY-ELSES-TXN", "phase": "prepared",
        "round_label": "1", "pre_edit_draft_sha1": "a" * 40,
        "pre_edit_draft_token": DRAFT_TOKEN,
        "staged_draft_sha256": "b" * 64, "staged_review_sha256": "c" * 64,
        "review_preimage": {"absent": True},
    }
    assert DRIVER.write_txn_intent(ctx.txn_dir, SEG, other) is True

    txn = _publish(ctx, "1", _stage_candidates(ctx))

    assert txn["ok"] is False
    assert txn["reason"] == "txn-intent-already-present"
    on_disk = json.loads(DRIVER.txn_intent_path(ctx.txn_dir, SEG).read_text(encoding="utf-8"))
    assert on_disk["txn_id"] == "SOMEBODY-ELSES-TXN", "the other transaction's record must survive"
    assert _canonical_present(ctx, "draft")["blocks"]["b"] == "old"
    assert _canonical(ctx, "review") == {"old": "review"}


def test_an_absent_canonical_draft_is_refused_at_the_preimage_not_published_over(tmp_path):
    """The case the preimage check still owns on its own. An ABSENT draft is a
    legal, stable observation, so the premise comparison waves it through --
    but there is nothing to record as pre_edit_draft_sha1, and a transaction
    with no preimage has no CAS."""
    ctx = _ctx(tmp_path)
    (ctx.segments_dir / f"{SEG}.review.json").write_text('{"old": "review"}', encoding="utf-8")
    result = _stage_candidates(ctx)

    txn = _publish(ctx, "1", result)

    assert txn["ok"] is False
    assert txn["reason"] == "txn-preimage-unreadable"
    assert not DRIVER.txn_intent_path(ctx.txn_dir, SEG).exists()


def test_an_unreadable_canonical_review_is_not_recorded_as_ABSENT(tmp_path):
    """A file that exists but cannot be read is the ABSENCE OF AN OBSERVATION,
    never an observation of absence. Recording {"absent": true} here would let
    a later pass confirm a preimage nobody ever saw."""
    ctx = _ctx(tmp_path)
    _write_canonical(ctx, review={"old": "review"})
    review_path = ctx.segments_dir / f"{SEG}.review.json"
    review_path.chmod(0o000)
    try:
        if os.access(str(review_path), os.R_OK):  # running as root -- the chmod bought nothing
            pytest.skip("cannot make a file unreadable as this user")
        txn = _publish(ctx, "1", _stage_candidates(ctx))
    finally:
        review_path.chmod(0o644)

    assert txn["ok"] is False
    # The premise's own observability check fires FIRST for this state --
    # earlier and more general than the preimage check that used to catch it.
    # Both refuse; what matters is that an unreadable review never becomes
    # {"absent": true} in a durable intent, which is what would let a later
    # pass "confirm" a preimage nobody ever saw.
    assert txn["reason"] == "txn-premise-unobservable"
    assert not DRIVER.txn_intent_path(ctx.txn_dir, SEG).exists()


def test_a_write_landing_DURING_the_decision_makes_the_round_re_derive(tmp_path, monkeypatch):
    """THE BRACKET, and the reason a one-sided premise was worthless.

    Capturing the premise only AFTER derive returns records whatever is on disk
    at that moment -- including a write that landed WHILE the decision was
    being read. derive answers from state A, the premise records state B, and
    the publish-time check then compares B against B and passes: the guard
    agreed with itself about exactly the writer it existed to catch.

    Found by codex review, which also pointed out that the test I had written
    for it called decision_premise() directly and never derive_next_action(),
    so it could not have detected this. This one drives the real loop and
    writes from inside derive."""
    ctx = _ctx(tmp_path)
    _write_canonical(ctx)
    _seed_fixreview_inputs(ctx)
    dispatched = []
    writes = []

    def _derive_then_somebody_writes(seg, c):
        # The competing write lands after derive has read, before the premise
        # that is supposed to describe derive's read is taken. It must write
        # something NEW each time: a writer that keeps re-writing identical
        # bytes leaves the state stable from the second iteration on, and a
        # stable state is one the decision may legitimately act on.
        writes.append(len(writes))
        (ctx.segments_dir / f"{SEG}.review.json").write_text(
            json.dumps({"published": "by somebody else", "n": len(writes)}),
            encoding="utf-8")
        return {"action": "review", "round_label": "1"}

    monkeypatch.setattr(DRIVER, "derive_next_action", _derive_then_somebody_writes)
    monkeypatch.setattr(DRIVER, "run_one_codex_job",
                        lambda c, **k: dispatched.append(k) or {"ok": True, "reason": "staged"})

    result = DRIVER.process_segment(SEG, ctx)

    assert dispatched == [], "a decision whose premise moved must dispatch nothing"
    assert len(writes) > 1, "the fixture must have re-derived, not returned after one look"
    # Every iteration re-derives and every one is written under, so the loop
    # ends on its own cap rather than acting on a decision it cannot trust.
    assert result["reason"] == "loop-exhausted-without-terminal-state"


def test_a_round_decided_against_state_somebody_else_has_since_replaced_is_refused(tmp_path):
    """THE LEASE HANDOFF WINDOW. The parent decides under the lease, releases
    it to launch the child, and the child takes it -- so a writer can land in
    between. The transaction's own CAS does NOT catch that: it binds the state
    observed AFTER the job returns, so the competitor's publication becomes
    part of the premise instead of invalidating it, and the round would
    overwrite a review it never saw."""
    ctx = _ctx(tmp_path)
    _write_canonical(ctx, review={"old": "review"})
    premise = DRIVER.decision_premise(ctx, SEG)
    result = _stage_candidates(ctx)

    # ... and now somebody else publishes a review for this segment.
    (ctx.segments_dir / f"{SEG}.review.json").write_text(
        '{"somebody": "else"}', encoding="utf-8")

    txn = _publish(ctx, "1", result, premise=premise)

    assert txn["ok"] is False
    assert txn["reason"] == "txn-decision-stale"
    assert _canonical(ctx, "review") == {"somebody": "else"}
    assert _canonical_present(ctx, "draft")["blocks"]["b"] == "old"
    # Refused BEFORE anything durable was allocated: no attempt_seq burned, no
    # intent, no staging, and nothing to charge -- no transaction ever began.
    assert not (ctx.txn_dir / f"{SEG}.attempts").exists()
    assert not DRIVER.txn_intent_path(ctx.txn_dir, SEG).exists()


def test_the_premise_covers_the_draft_TOKEN_and_the_review_not_only_content(tmp_path):
    """A competitor can leave the draft's content identical and change only its
    token, or touch only the review -- and derive_next_action() reads both."""
    ctx = _ctx(tmp_path)
    _write_canonical(ctx, review={"old": "review"})
    premise = DRIVER.decision_premise(ctx, SEG)
    before_content = DRAFT_SHA1.draft_content_sha1(ctx.segments_dir / f"{SEG}.draft.json")

    (ctx.segments_dir / f"{SEG}.draft.json").write_text(json.dumps(
        {"seg": SEG, "dispatch_token": "SOME-OTHER-RUN:seg01", "blocks": {"b": "old"}}),
        encoding="utf-8")
    after = DRIVER.decision_premise(ctx, SEG)

    # Asserted through the OWNING function, not a tuple index: draft_content_sha1
    # is what "content only" means here, and it is deliberately blind to the
    # token. An index would have to be renumbered every time the premise grows,
    # and silently asserts the wrong field when it is not.
    assert DRAFT_SHA1.draft_content_sha1(ctx.segments_dir / f"{SEG}.draft.json") == before_content
    assert after != premise, "the token change must still invalidate the premise"


def test_the_premise_covers_the_SEGPACK_the_gates_judged_against(tmp_path):
    """The segpack is the AUTHORITY, not context: both draft gates read it. A
    content change that keeps the key topology leaves the draft and review
    bytes untouched, so a premise over those two alone still matches and the
    pair publishes as if validated against the new source -- and the resulting
    convergence's cache key is then computed from the new segpack, marking work
    derived from the old one reusable under it.

    Found by codex review; deferring this was not defensible without an
    enforced "the segpack does not change mid-run" contract, and there is none."""
    ctx = _ctx(tmp_path)
    _write_canonical(ctx)
    segpack = ctx.segments_dir / f"segpack_{SEG}.json"
    segpack.write_text('{"blocks": {"b": "source"}}', encoding="utf-8")
    premise = DRIVER.decision_premise(ctx, SEG)

    segpack.write_text('{"blocks": {"b": "corrected source"}}', encoding="utf-8")

    assert DRIVER.decision_premise(ctx, SEG) != premise
    txn = _publish(ctx, "1", _stage_candidates(ctx), premise=premise)
    assert txn["reason"] == "txn-decision-stale"


def test_the_premise_covers_the_PROFILE_the_prompt_was_built_from(tmp_path):
    ctx = _ctx(tmp_path)
    _write_canonical(ctx)
    profile = ctx.dirs["durable_root"] / "profile.yml"
    profile.write_text("engine:\n  max_fix_rounds: 2\n", encoding="utf-8")
    (ctx.dirs["durable_root"] / ".literary-translator-root.json").write_text(
        json.dumps({"owner_profile_path": str(profile)}), encoding="utf-8")
    premise = DRIVER.decision_premise(ctx, SEG)

    profile.write_text("engine:\n  max_fix_rounds: 4\n", encoding="utf-8")

    assert DRIVER.decision_premise(ctx, SEG) != premise


def test_an_absent_segpack_or_profile_is_stable_rather_than_flapping(tmp_path):
    """Absence must compare equal to absence, or every round in a fixture-less
    project would refuse itself. It is UNREADABILITY that must refuse, and that
    is a different observation -- _sha256_of returns a distinct sentinel."""
    ctx = _ctx(tmp_path)
    _write_canonical(ctx)
    premise = DRIVER.decision_premise(ctx, SEG)
    assert premise == DRIVER.decision_premise(ctx, SEG)
    assert DRIVER.premise_is_observable(premise) is True


def test_an_UNREADABLE_component_is_refused_though_it_compares_equal_to_itself(tmp_path):
    """EQUALITY CANNOT EXPRESS THIS, which is why it needs its own check. A
    premise is used by comparing two of them, and TXN_UNREADABLE equals
    TXN_UNREADABLE -- so a segpack whose permissions broke reads as "unchanged"
    on both looks and the round proceeds having verified nothing about the
    authority it was judged against. My own docstring claimed the comparison
    handled it; it does not."""
    ctx = _ctx(tmp_path)
    _write_canonical(ctx)
    segpack = ctx.segments_dir / f"segpack_{SEG}.json"
    segpack.write_text('{"blocks": {}}', encoding="utf-8")
    segpack.chmod(0o000)
    try:
        if os.access(str(segpack), os.R_OK):  # root -- the chmod bought nothing
            pytest.skip("cannot make a file unreadable as this user")
        premise = DRIVER.decision_premise(ctx, SEG)
        assert premise == DRIVER.decision_premise(ctx, SEG), (
            "the two failed reads agree -- that is exactly the problem")
        assert DRIVER.premise_is_observable(premise) is False
        txn = _publish(ctx, "1", _stage_candidates(ctx), premise=premise)
    finally:
        segpack.chmod(0o644)

    assert txn["ok"] is False
    assert txn["reason"] == "txn-premise-unobservable"


def test_an_unreadable_canonical_DRAFT_is_not_mistaken_for_an_absent_one(tmp_path):
    """The sentinel for an unreadable draft arrives in the RAW hash slot;
    _draft_observation()'s two derived slots come back (None, None), which is
    byte-identical to "there is no draft". Dropping the raw value made an
    unreadable draft look absent, so the round proceeded, the gates failed on
    it, derive answered "translate", and the replacement was renamed over
    content nobody had ever read -- directory write permission is all that
    rename needs.

    Found by codex review, in the fix for the previous round's finding."""
    ctx = _ctx(tmp_path)
    _write_canonical(ctx)
    draft_path = ctx.segments_dir / f"{SEG}.draft.json"
    draft_path.chmod(0o000)
    try:
        if os.access(str(draft_path), os.R_OK):
            pytest.skip("cannot make a file unreadable as this user")
        premise = DRIVER.decision_premise(ctx, SEG)
        assert premise == DRIVER.decision_premise(ctx, SEG), "two failed reads agree"
        assert DRIVER.premise_is_observable(premise) is False
    finally:
        draft_path.chmod(0o644)


@pytest.mark.parametrize("mode", [DRIVER.FIX_MODE_HANDOFF, DRIVER.FIX_MODE_CODEX])
def test_an_unreadable_draft_dispatches_NOTHING_in_either_mode(tmp_path, monkeypatch, mode):
    """THE ONE THAT MATTERS ON THE DEFAULT PATH, and the reason it is checked
    in derive_next_action() rather than only in the premise.

    handoff passes premise=None and never reaches decide_under_premise(), so
    every protection built for the merged round is bypassed on the mode that
    is actually the default. The sequence: the draft exists and cannot be read,
    the gates fail on it, derive answers "translate", codex_job.py cannot adopt
    it either, and its fresh attempt is os.replace()d over the top. Replacing
    needs write permission on the DIRECTORY, not the file, so an unreadable
    draft is fully replaceable -- and the only copy of text nobody in this
    pipeline ever read is gone, unnoticed and unrecoverable.

    Found by codex review round 4. NOT a regression from this branch -- handoff
    has always behaved this way -- and fixed anyway, because "no worse than
    before" is a poor answer when the cost is destroyed source text."""
    ctx = _ctx(tmp_path, fix_mode=mode)
    _write_canonical(ctx)
    draft_path = ctx.segments_dir / f"{SEG}.draft.json"
    dispatched = []
    monkeypatch.setattr(DRIVER, "run_one_codex_job",
                        lambda c, **k: dispatched.append(k) or {"ok": True, "reason": "promoted"})
    monkeypatch.setattr(DRIVER, "write_ledger", lambda *a, **k: {"success": True})
    draft_path.chmod(0o000)
    try:
        if os.access(str(draft_path), os.R_OK):
            pytest.skip("cannot make a file unreadable as this user")
        result = DRIVER.process_segment(SEG, ctx)
    finally:
        draft_path.chmod(0o644)

    assert result["reason"] == "unreadable-draft"
    assert result["outcome"] == "failed"
    assert dispatched == [], "a translate here is what destroys the file"
    # No terminal ledger row: the segment stays recoverable and a human looks.
    assert "converged" in result and result["converged"] is False


def test_a_DANGLING_SYMLINK_is_not_hashed_as_absent(tmp_path):
    """ENOENT from the read is not the same observation as "no directory entry".

    Reading through a dangling symlink raises FileNotFoundError, exactly like a
    path with nothing at it -- but os.lstat() on the link succeeds, so an entry
    exists and somebody owns it. Reporting None there reports the ONE
    observation that licenses replacing the path.

    No chmod, so unlike the mode-000 tests in this file this one cannot silently
    skip itself for a user who can read anything."""
    link = tmp_path / "link.json"
    os.symlink(tmp_path / "target-that-does-not-exist.json", link)

    assert os.path.lexists(link), "the entry exists"
    assert not os.path.exists(link), "and resolving it fails, which is the trap"
    assert DRIVER._sha256_of(link) == DRIVER.TXN_UNREADABLE
    # Control: a real absence must still read as absence, or every fresh
    # segment becomes permanently undispatchable.
    assert DRIVER._sha256_of(tmp_path / "nothing-at-all.json") is None


@pytest.mark.parametrize("mode", [DRIVER.FIX_MODE_HANDOFF, DRIVER.FIX_MODE_CODEX])
def test_a_dangling_canonical_draft_dispatches_NOTHING_in_either_mode(
        tmp_path, monkeypatch, mode):
    """The same hazard as the mode-000 draft, reached through a different errno.

    A `<seg>.draft.json` that is a symlink to a target on an unmounted volume
    reads as ENOENT; the old classification called that absence, derived
    `translate`, and renamed a fresh attempt over the LINK the operator owns.

    Found by codex review round 5."""
    ctx = _ctx(tmp_path, fix_mode=mode)
    _write_canonical(ctx)
    draft_path = ctx.segments_dir / f"{SEG}.draft.json"
    draft_path.unlink()
    os.symlink(ctx.segments_dir / f"{SEG}.draft.json.target-is-gone", draft_path)

    dispatched = []
    monkeypatch.setattr(DRIVER, "run_one_codex_job",
                        lambda c, **k: dispatched.append(k) or {"ok": True, "reason": "promoted"})
    monkeypatch.setattr(DRIVER, "write_ledger", lambda *a, **k: {"success": True})

    result = DRIVER.process_segment(SEG, ctx)

    assert result["reason"] == "unreadable-draft"
    assert result["outcome"] == "failed"
    assert dispatched == [], "a translate here is what destroys the operator's link"
    assert os.path.lexists(draft_path), "the entry must still be there afterwards"


def test_an_intent_whose_LOOKUP_FAILS_reads_as_present_not_as_clear(tmp_path):
    """Path.exists() resolves the link, so a self-referential symlink (ELOOP)
    answers False -- byte-identical to "there is no intent". That answer decides
    whether a PAID dispatch may proceed, and the intent it missed then refuses
    every publication with `txn-intent-already-present`, forever, charging
    nothing because the id was already charged once.

    This was introduced by the previous round's own fix, which replaced an
    outcome-name allow-list with a filesystem question and picked the accessor
    that cannot tell a failed lookup from an absence. Found by codex review
    round 5."""
    ctx = _ctx(tmp_path)
    intent_path = DRIVER.txn_intent_path(ctx.txn_dir, SEG)
    intent_path.parent.mkdir(parents=True, exist_ok=True)
    os.symlink(intent_path.name, intent_path)  # points at itself -> ELOOP

    assert os.path.lexists(intent_path), "the entry exists"
    assert not os.path.exists(intent_path), (
        "and exists() reports False for it -- the whole defect in one line")
    assert DRIVER.recovery_left_the_segment_blocked(ctx, SEG, []) is True


@pytest.mark.parametrize("field", ["commit_failed", "cleanup_failed"])
def test_a_DISCARDED_durability_failure_blocks_the_segment(tmp_path, field):
    """commit_txn_intent() and cleanup_txn() both report durability failure by
    returning False, and both returns were discarded. Asking the filesystem
    answers "will an intent refuse my publication" but cannot answer "did the
    steps this decision required actually land", so those are carried
    positively."""
    ctx = _ctx(tmp_path)
    ctx.txn_dir.mkdir(parents=True, exist_ok=True)

    # Control first: with no intent on disk and nothing failed, the segment is
    # clear. Without this the test below could pass for the wrong reason.
    assert DRIVER.recovery_left_the_segment_blocked(
        ctx, SEG, [{"outcome": "whatever", "published": True}]) is False

    assert DRIVER.recovery_left_the_segment_blocked(
        ctx, SEG, [{"outcome": "whatever", "published": True, field: True}]) is True


def test_a_genuinely_absent_draft_still_translates(tmp_path, monkeypatch):
    """The other direction, and it has to be exercised through the real loop:
    refusing absence would make every fresh segment permanently undispatchable,
    which is a far bigger failure than the one being prevented."""
    ctx = _ctx(tmp_path, fix_mode=DRIVER.FIX_MODE_HANDOFF)
    dispatched = []
    monkeypatch.setattr(DRIVER, "_run_gate",
                        lambda script, argv_rest, c, *, supports_plugin_root: False)
    monkeypatch.setattr(DRIVER, "write_ledger", lambda *a, **k: {"success": True})
    monkeypatch.setattr(DRIVER, "run_one_codex_job",
                        lambda c, **k: dispatched.append(k) or {"ok": False, "reason": "timed-out",
                                                                "error_detail": None})

    DRIVER.process_segment(SEG, ctx)

    assert [d["kind"] for d in dispatched] == ["translate"]


def test_a_genuinely_absent_draft_stays_observable(tmp_path):
    """The other half: a segment before its translate has no draft, and that is
    a real state the loop must be able to act on. Refusing it would make every
    fresh segment unrunnable."""
    ctx = _ctx(tmp_path)
    premise = DRIVER.decision_premise(ctx, SEG)
    assert DRIVER.premise_is_observable(premise) is True


def test_the_private_candidates_SURVIVE_until_the_intent_is_durable(tmp_path, monkeypatch):
    """They may not be removed until something DURABLE owns the bytes, and
    transaction staging alone is not that: a failed intent write takes the
    staging with it, and a crash before the intent lands leaves staging that
    the next recovery classifies as an aborted prepare and deletes. Unlinked
    at either moment, no copy of a validated pair would survive anywhere.

    Found by codex review: the happy-path test could not tell deletion before
    the intent from deletion after it."""
    ctx = _ctx(tmp_path)
    _write_canonical(ctx)
    result = _stage_candidates(ctx)
    monkeypatch.setattr(DRIVER, "write_txn_intent", lambda *a, **k: False)

    txn = _publish(ctx, "1", result)

    assert txn["reason"] == "txn-intent-write-failed"
    assert Path(result["staged_draft_path"]).exists(), (
        "the intent never became durable, so these are still the only copy")
    assert Path(result["staged_review_path"]).exists()


def test_candidates_abandoned_on_lease_contention_are_KEPT(tmp_path, monkeypatch):
    """A reversal of an earlier revision, which deleted them on the argument
    that their random per-invocation paths make them unreachable. They are not:
    the dispatch journal records both paths, so deleting destroyed validated,
    paid-for work a human could still recover, in order to avoid litter."""
    ctx = _ctx(tmp_path)
    _write_canonical(ctx)
    _seed_fixreview_inputs(ctx)
    held = {}

    def _job_that_keeps_the_lease(c, *, kind, seg, round_label=None):
        path = ctx.segments_dir / f".codex_job.{seg}.lock"
        held["fd"] = os.open(str(path), os.O_CREAT | os.O_RDWR, 0o600)
        fcntl.flock(held["fd"], fcntl.LOCK_EX | fcntl.LOCK_NB)
        return dict(_stage_candidates(ctx), kind=kind, seg=seg, round_label=round_label)

    monkeypatch.setattr(DRIVER, "derive_next_action",
                        lambda seg, c: {"action": "review", "round_label": "1"})
    monkeypatch.setattr(DRIVER, "run_one_codex_job", _job_that_keeps_the_lease)
    try:
        result = DRIVER.process_segment(SEG, ctx)
    finally:
        fcntl.flock(held["fd"], fcntl.LOCK_UN)
        os.close(held["fd"])

    assert result["reason"] == "segment-busy"
    assert (ctx.segments_dir / f".att.{SEG}.abcd.draft.json").exists()
    assert (ctx.segments_dir / f".att.{SEG}.abcd.review.json").exists()


def test_an_intent_recovery_could_not_resolve_stops_the_segment_before_it_pays(
        tmp_path, monkeypatch):
    """Recovery deliberately RETAINS an intent it cannot resolve -- invalid,
    unobservable, or one whose failure charge would not go durable. Ignoring
    that was not a tidiness point: the loop went on to derive, dispatch and PAY
    for a full fixreview round whose publication then refused
    `txn-intent-already-present`, and the same retained intent reproduced that
    wasted round on every later invocation without ever charging the counter
    that is supposed to bound exactly this.

    Found by codex review, which reproduced it with a pure probe."""
    ctx = _ctx(tmp_path)
    _write_canonical(ctx)
    _seed_fixreview_inputs(ctx)
    ctx.txn_dir.mkdir(parents=True)
    # An intent this module's own validator refuses to understand: retained,
    # never cleaned up, and publication will refuse to publish over it.
    DRIVER.txn_intent_path(ctx.txn_dir, SEG).write_text(
        json.dumps({"txn_schema": 99, "txn_id": "FROM-THE-FUTURE"}), encoding="utf-8")
    dispatched = []
    monkeypatch.setattr(DRIVER, "derive_next_action",
                        lambda seg, c: {"action": "review", "round_label": "1"})
    monkeypatch.setattr(DRIVER, "run_one_codex_job",
                        lambda c, **k: dispatched.append(k) or {"ok": True, "reason": "staged"})

    result = DRIVER.process_segment(SEG, ctx)

    assert result["reason"] == "recovery-blocked"
    assert dispatched == [], "a segment recovery could not clear must cost nothing"
    assert DRIVER.txn_intent_path(ctx.txn_dir, SEG).exists(), "and the record stays"


def test_a_cleanly_recovered_segment_is_NOT_blocked(tmp_path, monkeypatch):
    """The other direction, or `recovery-blocked` would fire on every ordinary
    round and the mode would never dispatch anything at all."""
    ctx = _ctx(tmp_path)
    _write_canonical(ctx)
    dispatched = []
    monkeypatch.setattr(DRIVER, "derive_next_action",
                        lambda seg, c: {"action": "review", "round_label": "final"})
    monkeypatch.setattr(DRIVER, "run_one_codex_job",
                        lambda c, **k: dispatched.append(k) or {"ok": False, "reason": "timed-out",
                                                                "error_detail": None})

    DRIVER.process_segment(SEG, ctx)

    assert [d["kind"] for d in dispatched] == ["review"]


# An OUTCOME NAME describes what the classifier decided, never what happened
# afterwards -- a roll-forward whose publication then failed reports a
# roll-forward name and leaves the intent in place. So these assert the
# OBSERVATION, and deliberately pair each case with the outcome name that would
# have given the opposite answer under the old name-matching version.
@pytest.mark.parametrize("outcome", [
    DRIVER.TXN_ROLL_FORWARD_BOTH, DRIVER.TXN_ROLLED_FORWARD_TAIL,
    DRIVER.TXN_COMMITTED_CLEANED, DRIVER.TXN_PREIMAGE_DIVERGED,
])
def test_an_intent_still_on_disk_blocks_whatever_outcome_was_reported(tmp_path, outcome):
    ctx = _ctx(tmp_path)
    ctx.txn_dir.mkdir(parents=True)
    DRIVER.txn_intent_path(ctx.txn_dir, SEG).write_text("{}", encoding="utf-8")

    assert DRIVER.recovery_left_the_segment_blocked(
        ctx, SEG, [{"outcome": outcome, "published": True}]) is True


@pytest.mark.parametrize("outcome", [
    DRIVER.TXN_PROCEED, DRIVER.TXN_INTENT_INVALID, DRIVER.TXN_UNOBSERVABLE,
])
def test_no_intent_on_disk_clears_whatever_outcome_was_reported(tmp_path, outcome):
    """Including the two that USED to block by name: if the record is gone,
    nothing will refuse the next publication, and refusing to dispatch would
    strand the segment forever over a name."""
    ctx = _ctx(tmp_path)
    assert DRIVER.recovery_left_the_segment_blocked(
        ctx, SEG, [{"outcome": outcome}]) is False


def test_a_failed_publication_under_a_success_shaped_outcome_still_blocks(tmp_path):
    """The exact sequence the name-matching version got wrong: recovery
    classifies a roll-forward, publication fails, the charge lands, and the
    intent stays. Reported as a roll-forward, it read as cleared -- and the
    loop then paid for a round whose publication refused
    txn-intent-already-present, every invocation, charging nothing more
    because that transaction id had already been charged once."""
    ctx = _ctx(tmp_path)
    ctx.txn_dir.mkdir(parents=True)
    DRIVER.txn_intent_path(ctx.txn_dir, SEG).write_text("{}", encoding="utf-8")

    assert DRIVER.recovery_left_the_segment_blocked(
        ctx, SEG,
        [{"outcome": DRIVER.TXN_ROLL_FORWARD_BOTH, "published": False,
          "charge_lost": False}]) is True


def test_a_recovery_whose_charge_was_lost_also_blocks(tmp_path):
    """The one thing the filesystem cannot show: a failure whose charge did not
    go durable must block even in the window where the intent is already gone."""
    ctx = _ctx(tmp_path)
    assert DRIVER.recovery_left_the_segment_blocked(
        ctx, SEG, [{"outcome": DRIVER.TXN_PREIMAGE_DIVERGED, "charge_lost": True}]) is True


def test_an_empty_recovery_result_clears(tmp_path):
    ctx = _ctx(tmp_path)
    assert DRIVER.recovery_left_the_segment_blocked(ctx, SEG, []) is False
    assert DRIVER.recovery_left_the_segment_blocked(ctx, SEG, None) is False


def test_an_unobservable_premise_stops_the_round_before_it_dispatches(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path)
    _write_canonical(ctx)
    _seed_fixreview_inputs(ctx)
    dispatched = []
    monkeypatch.setattr(DRIVER, "derive_next_action",
                        lambda seg, c: {"action": "review", "round_label": "1"})
    monkeypatch.setattr(DRIVER, "run_one_codex_job",
                        lambda c, **k: dispatched.append(k) or {"ok": True, "reason": "staged"})
    monkeypatch.setattr(DRIVER, "decision_premise",
                        lambda c, seg: ("a", "b", None, DRIVER.TXN_UNREADABLE, None))

    result = DRIVER.process_segment(SEG, ctx)

    assert dispatched == []
    assert result["reason"] == "loop-exhausted-without-terminal-state"


def test_the_private_candidates_are_removed_once_the_transaction_owns_copies(tmp_path):
    """codex_job.py deliberately KEEPS its two per-invocation candidates when
    it reports `staged` -- they are the only pointer to the work and it cannot
    know whether the driver consumed them. Nobody else ever deletes them, so
    without this they accumulate one draft-sized pair per round, forever, in
    the segments directory."""
    ctx = _ctx(tmp_path)
    _write_canonical(ctx)
    result = _stage_candidates(ctx)
    assert Path(result["staged_draft_path"]).exists()

    assert _publish(ctx, "1", result)["ok"] is True

    assert not Path(result["staged_draft_path"]).exists()
    assert not Path(result["staged_review_path"]).exists()


def test_the_private_candidates_SURVIVE_a_refusal_before_they_are_copied(tmp_path):
    """Deleting them on a path that published nothing would destroy the only
    copy of a validated pair over a transient refusal."""
    ctx = _ctx(tmp_path)
    _write_canonical(ctx)
    result = _stage_candidates(ctx)
    (ctx.segments_dir / f"{SEG}.review.json").write_text('{"raced": true}', encoding="utf-8")

    txn = _publish(ctx, "1", result, premise=("stale", "stale", None))

    assert txn["reason"] == "txn-decision-stale"
    assert Path(result["staged_draft_path"]).exists()
    assert Path(result["staged_review_path"]).exists()


# ---------------------------------------------------------------------------
# recovery
# ---------------------------------------------------------------------------


def test_orphaned_staging_is_found_by_GLOB_not_by_a_guessed_label(tmp_path):
    """The one state with no intent to read the label from is exactly the
    state step 0 exists for -- staging written, crash before the intent was
    durable. Guessing a label answers "nothing in flight" for real files."""
    ctx = _ctx(tmp_path)
    ctx.txn_dir.mkdir(parents=True)
    DRIVER.staged_paths(ctx.txn_dir, SEG, "2")["draft"].write_text("{}", encoding="utf-8")
    DRIVER.staged_paths(ctx.txn_dir, SEG, "final")["review"].write_text("{}", encoding="utf-8")

    assert DRIVER.orphaned_staging_labels(ctx.txn_dir, SEG) == ["2", "final"]


def test_a_label_shaped_thing_that_is_not_a_round_label_is_ignored(tmp_path):
    ctx = _ctx(tmp_path)
    ctx.txn_dir.mkdir(parents=True)
    (ctx.txn_dir / f"{SEG}.../..evil.staged.draft.json".replace("/", "_")).write_text(
        "{}", encoding="utf-8")
    (ctx.txn_dir / f"{SEG}.0.staged.draft.json").write_text("{}", encoding="utf-8")

    assert DRIVER.orphaned_staging_labels(ctx.txn_dir, SEG) == []


def test_recovery_deletes_orphaned_staging_left_by_a_crash_before_the_intent(tmp_path):
    ctx = _ctx(tmp_path)
    _write_canonical(ctx)
    ctx.txn_dir.mkdir(parents=True)
    slots = DRIVER.staged_paths(ctx.txn_dir, SEG, "1")
    slots["draft"].write_text('{"orphan": true}', encoding="utf-8")
    slots["review"].write_text('{"orphan": true}', encoding="utf-8")

    outcomes = [r["outcome"] for r in DRIVER.recover_segment_txns(ctx, SEG)]

    assert DRIVER.TXN_ABORTED_PREPARE in outcomes
    assert not slots["draft"].exists() and not slots["review"].exists()
    assert _canonical_present(ctx, "draft")["blocks"]["b"] == "old"


def test_recovery_over_a_quiet_segment_touches_nothing(tmp_path):
    ctx = _ctx(tmp_path)
    _write_canonical(ctx, review={"old": "review"})

    results = DRIVER.recover_segment_txns(ctx, SEG)

    assert [r["outcome"] for r in results] == [DRIVER.TXN_PROCEED]
    assert _canonical_present(ctx, "draft")["blocks"]["b"] == "old"
    assert _canonical(ctx, "review") == {"old": "review"}


def test_a_hand_built_partial_publish_list_is_refused_by_the_tail_check(tmp_path, monkeypatch):
    """Discovered while writing the crash test below, and worth keeping: a
    caller cannot ask publish_txn for HALF of what the classifier authorised.
    Each rename re-observes and compares the remaining list against a freshly
    minted decision, so "publish only the review" from a state where both are
    still pending is refused rather than performed."""
    ctx = _ctx(tmp_path)
    _write_canonical(ctx, review={"old": "review"})
    result = _stage_candidates(ctx)
    seen = {}

    real_publish = DRIVER.publish_txn

    def _ask_for_half(txn_dir, seg, segments_dir, decision, scripts_dir=DRIVER.SCRIPTS_DIR):
        seen["asked"] = list(decision["publish"])
        return real_publish(txn_dir, seg, segments_dir, dict(decision, publish=["review"]),
                            scripts_dir)

    monkeypatch.setattr(DRIVER, "publish_txn", _ask_for_half)
    txn = _publish(ctx, "1", result)

    assert seen["asked"] == ["review", "draft"]
    assert txn["ok"] is False
    assert _canonical(ctx, "review") == {"old": "review"}, "nothing may have been renamed"


def test_a_crash_between_the_two_renames_is_rolled_FORWARD_not_back(tmp_path, monkeypatch):
    """The state the whole intent exists for: the review is published and
    names a draft that has not been. Treating it as a failure would discard an
    edit that is already valid and already half-visible.

    The intermediate state is produced by renaming the review and then dying,
    which is what a crash after step 4 leaves -- not by asking publish_txn for
    a partial list, which it refuses (see the test above)."""
    ctx = _ctx(tmp_path)
    _write_canonical(ctx, review={"old": "review"})
    result = _stage_candidates(ctx)

    def _rename_the_review_then_die(txn_dir, seg, segments_dir, decision,
                                    scripts_dir=DRIVER.SCRIPTS_DIR):
        src = DRIVER.staged_paths(txn_dir, seg, "1")["review"]
        os.replace(str(src), str(segments_dir / f"{seg}.review.json"))
        return False

    monkeypatch.setattr(DRIVER, "publish_txn", _rename_the_review_then_die)
    first = _publish(ctx, "1", result)
    monkeypatch.undo()

    assert first["ok"] is False
    assert _canonical_present(ctx, "review")["dispatch_token"] == f"{RUN}:{SEG}:r1"
    assert _canonical_present(ctx, "draft")["blocks"]["b"] == "old", "draft must not be published yet"
    # The refusal is charged, even though the very next pass completes the
    # transaction. That is the conservative direction on purpose: the counter
    # bounds ATTEMPTS at a transaction that has already failed once, and a
    # roll-forward that then succeeds does not retroactively un-fail it.
    assert DRIVER.read_txn_failures(ctx.txn_dir, SEG)["count"] == 1

    # A replacement driver arrives and runs recovery before reading anything.
    outcomes = [r["outcome"] for r in DRIVER.recover_segment_txns(ctx, SEG)]

    assert DRIVER.TXN_ROLL_FORWARD_DRAFT in outcomes
    assert _canonical_present(ctx, "draft")["blocks"]["b"] == "fixed"
    assert not DRIVER.txn_intent_path(ctx.txn_dir, SEG).exists()
    assert DRIVER.read_txn_failures(ctx.txn_dir, SEG)["count"] == 1, "not charged twice"


def test_one_failed_transaction_is_charged_ONCE_however_often_recovery_runs(tmp_path, monkeypatch):
    """Charge-then-clean is only safe because the counter records the ids it
    has charged. Re-running recovery over the same txn_id must not advance
    it -- and the id is read from the intent, never recomputed."""
    ctx = _ctx(tmp_path)
    _write_canonical(ctx, draft_body="old")
    result = _stage_candidates(ctx)

    monkeypatch.setattr(DRIVER, "publish_txn", lambda *a, **k: False)
    _publish(ctx, "1", result)
    monkeypatch.undo()
    assert DRIVER.read_txn_failures(ctx.txn_dir, SEG)["count"] == 1

    # The intent is still there (a failed publication is never cleaned up), so
    # recovery sees the SAME txn_id again.
    for _ in range(3):
        DRIVER.advance_txn(ctx, SEG)
    failures = DRIVER.read_txn_failures(ctx.txn_dir, SEG)
    assert failures["count"] == 1, failures


def test_a_refusal_whose_charge_could_not_be_made_durable_keeps_its_own_evidence(
        tmp_path, monkeypatch):
    """"Charge first, then clean" is idempotent only while the charge lands.
    The counter is keyed by txn_id, so a second pass over the same intent does
    not double-count -- but if the charge could not be made durable and cleanup
    runs anyway, the intent carrying that id is gone and NO later pass can ever
    charge it. The segment then fails transactions without limit across
    invocations while its ceiling reads zero.

    Found by codex review: on a CAS refusal `published` stays True (nothing was
    asked to be renamed), so the cleanup branch fired regardless of the charge."""
    ctx = _ctx(tmp_path)
    _write_canonical(ctx, draft_body="old")
    result = _stage_candidates(ctx)

    original = DRIVER.write_txn_intent

    def _write_then_race(txn_dir, seg, intent):
        ok = original(txn_dir, seg, intent)
        _write_canonical(ctx, draft_body="somebody-elses-edit")
        return ok

    monkeypatch.setattr(DRIVER, "write_txn_intent", _write_then_race)
    # ...and the charge cannot be made durable.
    monkeypatch.setattr(DRIVER, "charge_txn_failure", lambda *a, **k: None)

    txn = _publish(ctx, "1", result)

    assert txn["ok"] is False
    assert txn["outcome"] == DRIVER.TXN_PREIMAGE_DIVERGED
    intent_path = DRIVER.txn_intent_path(ctx.txn_dir, SEG)
    assert intent_path.exists(), (
        "an uncharged refusal must keep the intent -- it is the only record of "
        "the txn_id a later pass could charge")
    slots = DRIVER.staged_paths(ctx.txn_dir, SEG, "1")
    assert slots["draft"].exists() and slots["review"].exists()


def test_a_refusal_whose_charge_DID_land_is_cleaned_up_normally(tmp_path, monkeypatch):
    """The other half, so the fix above is a condition rather than a blanket
    'never clean up after a refusal' -- which would leave every CAS refusal's
    staging on disk forever."""
    ctx = _ctx(tmp_path)
    _write_canonical(ctx, draft_body="old")
    result = _stage_candidates(ctx)
    original = DRIVER.write_txn_intent

    def _write_then_race(txn_dir, seg, intent):
        ok = original(txn_dir, seg, intent)
        _write_canonical(ctx, draft_body="somebody-elses-edit")
        return ok

    monkeypatch.setattr(DRIVER, "write_txn_intent", _write_then_race)
    txn = _publish(ctx, "1", result)

    assert txn["outcome"] == DRIVER.TXN_PREIMAGE_DIVERGED
    assert txn["charge_lost"] is False
    assert DRIVER.read_txn_failures(ctx.txn_dir, SEG)["count"] == 1
    assert not DRIVER.txn_intent_path(ctx.txn_dir, SEG).exists()
    slots = DRIVER.staged_paths(ctx.txn_dir, SEG, "1")
    assert not slots["draft"].exists() and not slots["review"].exists()


def test_an_aborted_prepare_is_still_cleaned_up_though_it_charges_nothing(tmp_path):
    """The charge-durability gate must not block cleanup on a path with no
    transaction to charge: orphaned staging has no intent and therefore no
    txn_id, and leaving it would make the fix above a permanent leak."""
    ctx = _ctx(tmp_path)
    _write_canonical(ctx)
    ctx.txn_dir.mkdir(parents=True)
    slots = DRIVER.staged_paths(ctx.txn_dir, SEG, "1")
    slots["draft"].write_text("{}", encoding="utf-8")

    outcomes = [r["outcome"] for r in DRIVER.recover_segment_txns(ctx, SEG)]

    assert DRIVER.TXN_ABORTED_PREPARE in outcomes
    assert not slots["draft"].exists()


# ---------------------------------------------------------------------------
# the bounds -- each one asserted at the DISPATCH, not after it
# ---------------------------------------------------------------------------


def _loop(monkeypatch, ctx, actions, job_results=None, publish=None):
    """Drive process_segment() over a scripted sequence of actions, recording
    every dispatch. The two collaborators are replaced rather than simulated
    end to end, so what is under test is the loop's own control flow.

    Seeds the four fixreview inputs (see _seed_fixreview_inputs()) before
    driving anything: every caller here is, by construction, simulating a
    segment already past translate (a scripted "review" or "needs_fix"
    action), so a real draft/segpack/style_bible.md/review_TASK.md already
    existing is the correct baseline -- without it, #409 track B's size
    fallback would silently downgrade every one of these codex-mode
    scenarios to handoff for reasons the test never intended to exercise."""
    _seed_fixreview_inputs(ctx)
    dispatched = []
    remaining = list(actions)

    def _derive(seg, c):
        return remaining.pop(0) if remaining else {"action": "cap_reached", "findings": []}

    def _job(c, *, kind, seg, round_label=None):
        dispatched.append({"kind": kind, "round_label": round_label})
        if job_results:
            return dict({"kind": kind, "seg": seg, "round_label": round_label},
                        **job_results.pop(0))
        return {"kind": kind, "seg": seg, "round_label": round_label, "ok": True,
                "reason": "staged", "error_detail": None, "staged": True}

    monkeypatch.setattr(DRIVER, "derive_next_action", _derive)
    monkeypatch.setattr(DRIVER, "run_one_codex_job", _job)
    monkeypatch.setattr(DRIVER, "publish_fixreview_pair",
                        publish or (lambda *a, **k: {"ok": True, "reason": None,
                                                     "outcome": None, "charged": None}))
    return dispatched


REJECTED = {"ok": False, "reason": "validate-failed", "error_detail": "gate 4", "staged": False}
INFRA_FAILED = {"ok": False, "reason": "timed-out", "error_detail": None}


def test_a_rejected_candidate_is_retried_within_the_round_then_terminates(tmp_path, monkeypatch):
    """A gate rejection is not an infrastructure failure: nothing was
    published and both candidates are quarantined, so another attempt is
    meaningful. Bounded, because "the model keeps emitting a fabricated loc"
    reproduces rather than resolves."""
    ctx = _ctx(tmp_path, max_rejected_candidates_per_round=2)
    action = {"action": "review", "round_label": "1"}
    dispatched = _loop(monkeypatch, ctx, [action] * 6, job_results=[REJECTED] * 6)

    result = DRIVER.process_segment(SEG, ctx)

    assert result["reason"] == "rejected-candidates-exhausted"
    assert result["outcome"] == "failed"
    assert "converged" in result and result["converged"] is False
    assert len(dispatched) == 2, dispatched


def test_the_rejected_candidate_allowance_is_PER_ROUND_not_per_segment(tmp_path, monkeypatch):
    """Pooling them across rounds lets one bad round spend every other round's
    allowance and still be 'within' the bound."""
    ctx = _ctx(tmp_path, max_rejected_candidates_per_round=2)
    dispatched = _loop(
        monkeypatch, ctx,
        [{"action": "review", "round_label": "1"},
         {"action": "review", "round_label": "2"},
         {"action": "review", "round_label": "2"},
         {"action": "already_converged", "round_label": "2"}],
        job_results=[REJECTED, REJECTED, REJECTED])

    # round 1 rejects once (under its own allowance), round 2 rejects twice.
    original_write = DRIVER.write_ledger
    monkeypatch.setattr(DRIVER, "write_ledger", lambda *a, **k: {"success": True})
    try:
        result = DRIVER.process_segment(SEG, ctx)
    finally:
        monkeypatch.setattr(DRIVER, "write_ledger", original_write)

    assert result["reason"] == "rejected-candidates-exhausted"
    assert result["round_label"] == "2"
    assert [d["round_label"] for d in dispatched] == ["1", "2", "2"]


def test_an_infrastructure_failure_is_NOT_charged_to_the_candidate_allowance(tmp_path, monkeypatch):
    """Retrying a dead companion is not meaningful the way retrying a rejected
    candidate is, and spending the round's allowance on it would refuse a
    round that never produced a bad candidate at all."""
    ctx = _ctx(tmp_path)
    dispatched = _loop(monkeypatch, ctx, [{"action": "review", "round_label": "1"}],
                       job_results=[INFRA_FAILED])

    result = DRIVER.process_segment(SEG, ctx)

    assert result["reason"] == "timed-out"
    assert result["stage"] == "fixreview"
    assert len(dispatched) == 1


def test_a_rejected_candidate_under_HANDOFF_is_still_terminal_at_once(tmp_path, monkeypatch):
    """The retry belongs to the merged path. handoff's control flow is
    deliberately untouched by this release, and a plain review job cannot
    report validate-failed for a fixreview gate anyway."""
    ctx = _ctx(tmp_path, fix_mode=DRIVER.FIX_MODE_HANDOFF)
    dispatched = _loop(monkeypatch, ctx, [{"action": "review", "round_label": "1"}] * 4,
                       job_results=[REJECTED] * 4)

    result = DRIVER.process_segment(SEG, ctx)

    assert result["reason"] == "validate-failed"
    assert len(dispatched) == 1


def test_a_persistently_stale_round_terminates_on_its_OWN_counter(tmp_path, monkeypatch):
    """This path has no bound of its own under handoff -- the loop cap is the
    bound there, and the driver says so. codex mode's per-segment number
    claims a SPECIFIC per-round allowance for staleness, and a claimed
    allowance nothing counts is the old unbounded path with a number written
    next to it."""
    ctx = _ctx(tmp_path)
    stale = {"action": "review", "round_label": "1", "cause": "clean_stale"}
    dispatched = _loop(monkeypatch, ctx, [stale] * 10)

    result = DRIVER.process_segment(SEG, ctx)

    assert result["reason"] == "stale-redispatch-exhausted"
    assert result["round_label"] == "1"
    assert len(dispatched) == DRIVER.MAX_STALE_REDISPATCHES_PER_ROUND


def test_the_stale_bound_does_not_fire_on_the_final_round(tmp_path, monkeypatch):
    """The final round is a plain review in both modes, so it is outside the
    fixreview path the per-round allowance describes -- it is bounded by the
    loop cap, and the plan says so rather than pretending the additive model
    enumerates everything."""
    ctx = _ctx(tmp_path)
    stale = {"action": "review", "round_label": "final", "cause": "clean_stale"}
    dispatched = _loop(monkeypatch, ctx, [stale] * 20)

    result = DRIVER.process_segment(SEG, ctx)

    assert result["reason"] == "loop-exhausted-without-terminal-state"
    assert all(d["kind"] == "review" for d in dispatched)
    assert len(dispatched) > DRIVER.MAX_STALE_REDISPATCHES_PER_ROUND


def test_an_exhausted_failure_counter_refuses_BEFORE_the_job_is_launched(tmp_path, monkeypatch):
    """Checking afterwards still spends the job whose spend the bound exists
    to refuse. The assertion that matters is that NOTHING was dispatched."""
    ctx = _ctx(tmp_path, max_txn_failures_per_segment=2)
    ctx.txn_dir.mkdir(parents=True)
    for n, txn_id in enumerate(("RUN:seg01:1:1", "RUN:seg01:1:2"), start=1):
        DRIVER.charge_txn_failure(ctx.txn_dir, SEG, txn_id, 2)
    assert DRIVER.read_txn_failures(ctx.txn_dir, SEG)["count"] == 2

    dispatched = _loop(monkeypatch, ctx, [{"action": "review", "round_label": "1"}] * 4)
    result = DRIVER.process_segment(SEG, ctx)

    assert result["reason"] == "txn-failures-exhausted"
    assert dispatched == [], dispatched


def test_the_exhausted_counter_does_NOT_block_the_final_round_or_a_translate(tmp_path, monkeypatch):
    """Neither is transactional. Refusing them over transaction failures they
    cannot cause would strand a segment that only needs its confirming review."""
    ctx = _ctx(tmp_path, max_txn_failures_per_segment=1)
    ctx.txn_dir.mkdir(parents=True)
    DRIVER.charge_txn_failure(ctx.txn_dir, SEG, "RUN:seg01:1:1", 1)

    dispatched = _loop(monkeypatch, ctx,
                       [{"action": "translate"},
                        {"action": "review", "round_label": "final"},
                        {"action": "cap_reached", "findings": []}])
    monkeypatch.setattr(DRIVER, "write_ledger", lambda *a, **k: {"success": True})
    result = DRIVER.process_segment(SEG, ctx)

    assert result["reason"] == "cap"
    assert [d["kind"] for d in dispatched] == ["translate", "review"]


def test_a_busy_segment_is_reported_recoverable_and_dispatches_nothing(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path)
    lock_path = ctx.segments_dir / f".codex_job.{SEG}.lock"
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    dispatched = _loop(monkeypatch, ctx, [{"action": "review", "round_label": "1"}])
    try:
        result = DRIVER.process_segment(SEG, ctx)
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)

    assert result == {"seg": SEG, "converged": False, "outcome": "failed",
                      "reason": "segment-busy"}
    assert dispatched == []


def test_publication_happens_UNDER_the_lease_not_merely_after_it(tmp_path, monkeypatch):
    """The renames cannot be made atomic -- POSIX has no compare-and-rename --
    but holding the lease across them at least excludes every writer that
    honours it, which is every codex_job.py in the system. Publishing outside
    it would leave the driver's own renames racing the very children it
    launches.

    The lease is free when derive runs and taken by the time publication does,
    which is exactly the shape of a child that has not let go."""
    ctx = _ctx(tmp_path)
    _write_canonical(ctx)
    _seed_fixreview_inputs(ctx)
    held = {}

    def _job_that_keeps_the_lease(c, *, kind, seg, round_label=None):
        path = ctx.segments_dir / f".codex_job.{seg}.lock"
        held["fd"] = os.open(str(path), os.O_CREAT | os.O_RDWR, 0o600)
        fcntl.flock(held["fd"], fcntl.LOCK_EX | fcntl.LOCK_NB)
        return dict(_stage_candidates(ctx), kind=kind, seg=seg, round_label=round_label)

    monkeypatch.setattr(DRIVER, "derive_next_action",
                        lambda seg, c: {"action": "review", "round_label": "1"})
    monkeypatch.setattr(DRIVER, "run_one_codex_job", _job_that_keeps_the_lease)
    try:
        result = DRIVER.process_segment(SEG, ctx)
    finally:
        fcntl.flock(held["fd"], fcntl.LOCK_UN)
        os.close(held["fd"])

    assert result["stage"] == "publish"
    assert result["reason"] == "segment-busy"
    assert _canonical_present(ctx, "draft")["blocks"]["b"] == "old", "nothing may have been published"
    # What happens to the validated pair here is asserted by
    # test_candidates_abandoned_on_lease_contention_are_KEPT, which owns that
    # question; this test owns only "the lease is taken before publishing".


def test_the_renames_themselves_run_while_the_lease_is_HELD(tmp_path, monkeypatch):
    """The test above proves the lease is ACQUIRED before publishing. It cannot
    tell that apart from acquiring it, releasing it, and then publishing --
    mutation confirmed exactly that: moving the publish call one line out of
    the `with` left it green.

    This one observes the lock at the moment of the rename. flock is per
    OPEN FILE DESCRIPTION, so a second open() in this same process contends
    with the driver's own hold just as another process would."""
    ctx = _ctx(tmp_path)
    _write_canonical(ctx)
    _seed_fixreview_inputs(ctx)
    observed = {}
    real_publish = DRIVER.publish_txn

    def _publish_and_look(txn_dir, seg, segments_dir, decision, scripts_dir=DRIVER.SCRIPTS_DIR):
        fd = os.open(str(segments_dir / f".codex_job.{seg}.lock"),
                     os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            observed["lease_was_free"] = True
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            observed["lease_was_free"] = False
        finally:
            os.close(fd)
        return real_publish(txn_dir, seg, segments_dir, decision, scripts_dir)

    monkeypatch.setattr(DRIVER, "publish_txn", _publish_and_look)
    monkeypatch.setattr(DRIVER, "derive_next_action",
                        lambda seg, c: {"action": "review", "round_label": "1"})
    monkeypatch.setattr(
        DRIVER, "run_one_codex_job",
        lambda c, **k: dict(_stage_candidates(ctx), **k))

    DRIVER.process_segment(SEG, ctx)

    assert observed.get("lease_was_free") is False, (
        "the canonical renames ran with the per-segment lease NOT held -- the "
        "driver's own publication was racing the children it launches")
    assert _canonical_present(ctx, "draft")["blocks"]["b"] == "fixed"


def test_handoff_takes_no_lease_at_all(tmp_path, monkeypatch):
    """A lease handoff never needed is a behaviour change on the default path,
    and one that would make a driver and its own predecessor's child fight."""
    ctx = _ctx(tmp_path, fix_mode=DRIVER.FIX_MODE_HANDOFF)
    lock_path = ctx.segments_dir / f".codex_job.{SEG}.lock"
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    dispatched = _loop(monkeypatch, ctx, [{"action": "review", "round_label": "1"}],
                       job_results=[{"ok": True, "reason": "promoted", "error_detail": None}])
    try:
        DRIVER.process_segment(SEG, ctx)
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)

    assert [d["kind"] for d in dispatched] == ["review"]


def test_a_publication_refusal_stops_the_round_instead_of_looping(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path)
    dispatched = _loop(
        monkeypatch, ctx, [{"action": "review", "round_label": "1"}] * 6,
        publish=lambda *a, **k: {"ok": False, "reason": DRIVER.TXN_STAGING_LOST,
                                 "outcome": DRIVER.TXN_STAGING_LOST, "charged": None})

    result = DRIVER.process_segment(SEG, ctx)

    assert result["stage"] == "publish"
    assert result["reason"] == DRIVER.TXN_STAGING_LOST
    assert len(dispatched) == 1


# ---------------------------------------------------------------------------
# the two engine knobs, now actually read
# ---------------------------------------------------------------------------


def _profile_root(tmp_path, engine_extra=""):
    root = tmp_path / "proot"
    (root / "segments").mkdir(parents=True)
    profile = root / "profile.yml"
    profile.write_text(
        "engine:\n  max_fix_rounds: 2\n  batch_agent_cap: 100\n  effort: high\n"
        + engine_extra
        + "source:\n  language:\n    code: he\n"
          "target:\n  language:\n    code: en\n"
          "verse_policy:\n  mode: literal_only\n",
        encoding="utf-8")
    (root / ".literary-translator-root.json").write_text(
        json.dumps({"owner_profile_path": str(profile)}), encoding="utf-8")
    return root


def test_two_profile_reads_that_disagree_refuse_the_run():
    """The admission and the loop each open profile.yml, separately and at
    different moments. An edit landing between them lets the SMALLER number
    admit the batch while the loop runs on the LARGER one -- which defeats
    exactly the "admission and cap are one number" property, and defeats it
    invisibly, because both reads are individually valid.

    Found by codex review."""
    engine = {"max_fix_rounds": 2, "max_codex_jobs_per_batch": 400,
              "max_rejected_candidates_per_round": 2, "max_txn_failures_per_segment": 3}
    assert DRIVER.profile_snapshots_disagree(engine, dict(engine)) is None

    grown = dict(engine, max_fix_rounds=9)
    refusal = DRIVER.profile_snapshots_disagree(engine, grown)
    assert refusal is not None
    assert refusal["key"] == "max_fix_rounds"
    assert refusal["admissionValue"] == 2 and refusal["dispatchValue"] == 9


# HARD-CODED, never derived from the production tuple. Parametrising over
# DRIVER._SHARED_PROFILE_KEYS made the test self-fulfilling: deleting a key
# from the contract deleted its own test case along with it, so the coverage
# and the thing covered could only ever agree.
EXPECTED_SHARED_PROFILE_KEYS = (
    "max_fix_rounds", "max_codex_jobs_per_batch",
    "max_rejected_candidates_per_round", "max_txn_failures_per_segment",
    "max_fixreview_context_tokens",
)


def test_the_shared_profile_key_contract_is_exactly_this():
    assert tuple(DRIVER._SHARED_PROFILE_KEYS) == EXPECTED_SHARED_PROFILE_KEYS


@pytest.mark.parametrize("key", list(EXPECTED_SHARED_PROFILE_KEYS))
def test_every_shared_profile_key_is_actually_compared(key):
    """Named one at a time. A key silently dropped from the comparison is the
    one an edit would then be free to split."""
    engine = {k: 1 for k in EXPECTED_SHARED_PROFILE_KEYS}
    assert DRIVER.profile_snapshots_disagree(engine, dict(engine, **{key: 2})) is not None


def test_a_key_only_one_loader_returns_is_not_a_disagreement():
    """The two loaders return overlapping-but-different shapes; a key absent
    from one is not evidence the file moved."""
    assert DRIVER.profile_snapshots_disagree({"max_fix_rounds": 2}, {}) is None


@pytest.mark.parametrize("loader", ["load_engine_config", "load_translate_config"])
def test_both_loaders_read_the_two_new_knobs(tmp_path, loader):
    """BOTH, from the same key with the same default. One honouring the
    profile while the other defaults would make the admission check and the
    loop cap disagree about the number that is supposed to be one number."""
    root = _profile_root(tmp_path,
                         "  max_rejected_candidates_per_round: 5\n"
                         "  max_txn_failures_per_segment: 7\n")
    cfg = getattr(DRIVER, loader)(root)
    assert cfg["max_rejected_candidates_per_round"] == 5
    assert cfg["max_txn_failures_per_segment"] == 7


@pytest.mark.parametrize("loader", ["load_engine_config", "load_translate_config"])
def test_both_loaders_apply_the_schema_documented_defaults(tmp_path, loader):
    """profile.schema.json's `default` is documentation-only -- nothing fills
    it in at validation time -- so every consumer applies it itself."""
    cfg = getattr(DRIVER, loader)(_profile_root(tmp_path))
    assert cfg["max_rejected_candidates_per_round"] == DRIVER.DEFAULT_MAX_REJECTED_CANDIDATES_PER_ROUND
    assert cfg["max_txn_failures_per_segment"] == DRIVER.DEFAULT_MAX_TXN_FAILURES_PER_SEGMENT


@pytest.mark.parametrize("value", ["true", "-1", "'2'", "2.5"])
def test_a_non_integer_knob_is_refused_not_coerced(tmp_path, value):
    """`true` is the one that matters: bool IS an int subclass in Python, so
    without the explicit rejection it would silently resolve to 1."""
    root = _profile_root(tmp_path, f"  max_rejected_candidates_per_round: {value}\n")
    with pytest.raises(DRIVER.DriverError):
        DRIVER.load_engine_config(root)


# ---------------------------------------------------------------------------
# #409 track B -- the size fallback (R_size). effective_fix_mode()/
# fixreview_context_estimate() -- see segment_dispatch_driver.py's own
# module-level comment block above FIXREVIEW_CONTEXT_WINDOW_TOKENS for the
# formula and the provenance of every constant it sums.
# ---------------------------------------------------------------------------


def test_a_normal_sized_segment_still_takes_the_codex_path(tmp_path):
    """THE CONTROL. Without this, "an oversized segment falls back" could be
    passing because effective_fix_mode() falls back UNCONDITIONALLY, size be
    damned -- this proves the ordinary, comfortably-within-budget case is
    unaffected: a --fix-mode=codex ctx with real, modestly sized inputs
    stays codex."""
    ctx = _ctx(tmp_path)
    _seed_fixreview_inputs(ctx)
    assert ctx.fix_mode == DRIVER.FIX_MODE_CODEX
    assert DRIVER.effective_fix_mode(ctx, SEG) == DRIVER.FIX_MODE_CODEX


def test_an_oversized_segment_falls_back_to_handoff_while_configured_mode_stays_codex(tmp_path):
    """The RUN's own configured mode is never touched -- only THIS segment's
    decision changes. engine.max_fixreview_context_tokens=1 is smaller than
    even the FIXED per-call overhead (the dispatch prompt + agent preamble +
    worst-case review output, none of which depend on any file's content),
    so the fallback is guaranteed regardless of how small the four real
    files are -- the point here is the MODE distinction, not a close
    numeric call (test_the_budget_boundary_is_exercised_on_both_sides below
    owns that)."""
    ctx = _ctx(tmp_path, max_fixreview_context_tokens=1)
    _seed_fixreview_inputs(ctx)

    assert ctx.fix_mode == DRIVER.FIX_MODE_CODEX, "the RUN stays configured codex"
    assert DRIVER.effective_fix_mode(ctx, SEG) == DRIVER.FIX_MODE_HANDOFF


def test_an_unobservable_size_fails_closed_rather_than_proceeding(tmp_path):
    """No draft has been written at all -- one of the four sizes
    (fixreview_context_estimate()'s own contract) is unobservable.
    Proceeding as if an unobservable size were small would be exactly the
    silent under-estimate this mechanism exists to prevent; the contract is
    fall back, never guess "probably fine"."""
    ctx = _ctx(tmp_path)
    assert DRIVER.fixreview_context_estimate(ctx, SEG) is None
    assert DRIVER.effective_fix_mode(ctx, SEG) == DRIVER.FIX_MODE_HANDOFF


def test_effective_fix_mode_is_inert_under_handoff(tmp_path):
    """No estimate, no budget check, no file reads at all -- ctx.fix_mode is
    returned verbatim. A run never configured codex has nothing to fall
    back FROM."""
    ctx = _ctx(tmp_path, fix_mode=DRIVER.FIX_MODE_HANDOFF)
    assert DRIVER.effective_fix_mode(ctx, SEG) == DRIVER.FIX_MODE_HANDOFF


def test_the_budget_boundary_is_exercised_on_both_sides(tmp_path):
    """Built from REAL file sizes, never by mocking fixreview_context_
    estimate() itself -- a test that mocks the thing under test proves
    nothing about the comparison it performs. segpack_<seg>.json/
    style_bible.md/review_TASK.md are held at the SAME tiny size on both
    sides; only the draft's own byte count is computed (from the driver's
    own published ratios, used here only to size an INPUT file -- the
    assertions below are a plain mode comparison, never a numeric equality
    against a production constant) to land the estimate just under, then
    just over, one fixed budget."""
    ctx = _ctx(tmp_path, max_fixreview_context_tokens=10_000)
    _seed_fixreview_inputs(ctx)
    fixed_overhead = (DRIVER.FIXREVIEW_DISPATCH_PROMPT_TOKENS
                      + DRIVER.FIXREVIEW_BASE_INSTRUCTIONS_TOKENS
                      + DRIVER.FIXREVIEW_WORST_CASE_REVIEW_OUTPUT_TOKENS)
    other_tokens = (
        (ctx.segments_dir / f"segpack_{SEG}.json").stat().st_size
        / DRIVER.FIXREVIEW_SEGPACK_BYTES_PER_TOKEN
        + ((ctx.dirs["durable_root"] / "style_bible.md").stat().st_size
           + (ctx.dirs["durable_root"] / "review_TASK.md").stat().st_size)
          / DRIVER.FIXREVIEW_PROSE_BYTES_PER_TOKEN
    )
    draft_budget_tokens = 10_000 - fixed_overhead - other_tokens
    # estimate's draft term is 2 * draft_bytes / RATIO -- solved for the
    # byte count that alone would spend exactly the remaining budget.
    draft_bytes_at_boundary = int(draft_budget_tokens * DRIVER.FIXREVIEW_DRAFT_BYTES_PER_TOKEN / 2)
    draft_path = ctx.segments_dir / f"{SEG}.draft.json"

    draft_path.write_bytes(b"x" * max(draft_bytes_at_boundary - 200, 0))
    assert DRIVER.effective_fix_mode(ctx, SEG) == DRIVER.FIX_MODE_CODEX, "just UNDER the budget"

    draft_path.write_bytes(b"x" * (draft_bytes_at_boundary + 200))
    assert DRIVER.effective_fix_mode(ctx, SEG) == DRIVER.FIX_MODE_HANDOFF, "just OVER the budget"


def test_a_realistically_oversized_segment_fails_unambiguously(tmp_path):
    """THE CORPUS-SHAPED CASE, kept deliberately SEPARATE from the knife-edge
    boundary test above, and deliberately NOT knife-edge itself. Measured on
    the real 207-segment corpus this budget was set against: exactly ONE
    segment falls back, and it fails by 39.2% over budget -- not a marginal
    call. The next-largest segment clears by +17.0%, the one after that by
    +50.5%: a genuine gap between the single failure and everything else,
    not a threshold sitting in the middle of a cluster. This test reproduces
    that SHAPE (a real segment sized well past the budget) rather than the
    exact corpus bytes, which are not available in this sandbox -- if this
    test ever needed knife-edge byte tuning to pass, that would itself be a
    sign it was measuring the arithmetic rather than the behaviour."""
    ctx = _ctx(tmp_path)
    _seed_fixreview_inputs(ctx)
    budget = DRIVER.DEFAULT_MAX_FIXREVIEW_CONTEXT_TOKENS
    assert ctx.translate_cfg.get("max_fixreview_context_tokens", budget) == budget

    # Size the draft alone so the estimate lands at ~1.392x budget, the same
    # margin the one real failing corpus segment fails by -- comfortably,
    # unambiguously over, never adjacent to the line.
    fixed_overhead = (DRIVER.FIXREVIEW_DISPATCH_PROMPT_TOKENS
                      + DRIVER.FIXREVIEW_BASE_INSTRUCTIONS_TOKENS
                      + DRIVER.FIXREVIEW_WORST_CASE_REVIEW_OUTPUT_TOKENS)
    target_tokens = budget * 1.392
    draft_tokens_needed = target_tokens - fixed_overhead
    draft_bytes = int(draft_tokens_needed * DRIVER.FIXREVIEW_DRAFT_BYTES_PER_TOKEN / 2)
    (ctx.segments_dir / f"{SEG}.draft.json").write_bytes(b"x" * draft_bytes)

    estimate = DRIVER.fixreview_context_estimate(ctx, SEG)
    assert estimate > budget * 1.3, (
        f"expected an unambiguous ~39% overage, got estimate={estimate} budget={budget}")
    assert DRIVER.effective_fix_mode(ctx, SEG) == DRIVER.FIX_MODE_HANDOFF


def test_gate_b_differential_check_against_the_real_corpus_measurement(tmp_path):
    """Ties the gate to S6-TOKEN-MARGIN-RESULTS.md's own independently
    tokenized ground truth (real tiktoken counts against real, on-disk
    files -- a SEPARATE measurement pass, not this formula run twice) rather
    than only to itself. This is the check that catches a constant that is
    individually plausible but collectively wrong: with an earlier, too
    generous budget (204,000) seg66's verdict came out right for the wrong
    reason -- only the margin figures this test checks exposed that.

    Byte sizes below are PINNED AS LITERALS, read once (read-only, via
    `stat -f %z`) from the real seg66/seg27 files at
    /Users/moi/lazy-ants/development/claude-plugins-runs/lt-409-stepB/
    s6-measure-root (an APFS copy-on-write clone of the live tome1 book) --
    confirmed to match the doc's own §10.1/§10.2/§10.4 tables exactly. This
    test does NOT depend on that clone existing at test time; nothing here
    reads from it.

    seg66 (doc §10.3): the corpus's LARGEST real segment -- gate_b_tokens=
    269,781 against the doc's EXACT tokenization, 39.2% OVER the 193,800
    budget, the single outlier of 207 real eligible segments. MUST fall
    back.

    seg27 (doc §10.4): the corpus's SECOND-largest real segment, same book
    (same style_bible.md/review_TASK.md as seg66) -- gate_b_tokens=160,799
    against the doc's exact tokenization, margin +17.0% (33,001 tokens of
    headroom). MUST stay codex -- comfortable, not a near-miss.

    THIS FORMULA'S OWN ESTIMATE IS DELIBERATELY NOT EXPECTED TO MATCH THOSE
    TWO NUMBERS EXACTLY, and asserting equality would be the wrong check.
    fixreview_context_estimate() applies ONE conservative prose ratio
    (FIXREVIEW_PROSE_BYTES_PER_TOKEN, the WORST of eight real per-file
    measurements) uniformly to style_bible.md + review_TASK.md, precisely
    so the driver never needs a tiktoken dependency at runtime (doc §10.5's
    own defended trade-off) -- the doc's §10 figures instead tokenize EACH
    file exactly (tome1's style_bible.md at its own real ~3.6 B/token,
    review_TASK.md at its own real ~4.1 B/token), which is why they read
    LOWER. What must match is the VERDICT on both segments, and this
    formula's estimate must never read BELOW the doc's exact figure --
    over-reserving is the only direction it is allowed to be wrong in."""
    ctx = _ctx(tmp_path)
    budget = ctx.translate_cfg.get(
        "max_fixreview_context_tokens", DRIVER.DEFAULT_MAX_FIXREVIEW_CONTEXT_TOKENS)
    assert budget == 193_800, "this check is calibrated against the FINAL budget; update it if that moves"

    # tome1's own style_bible.md/review_TASK.md -- SAME for both segments below,
    # since seg66 and seg27 are both tome1 segments (doc §10.1/§10.2).
    style_bible_bytes = 29_684   # tome1/style_bible.md, real, read-only clone
    review_task_bytes = 9_158    # tome1/review_TASK.md, real, read-only clone
    (ctx.dirs["durable_root"] / "style_bible.md").write_bytes(b"x" * style_bible_bytes)
    (ctx.dirs["durable_root"] / "review_TASK.md").write_bytes(b"x" * review_task_bytes)

    # seg66 -- tome1's largest real segment (doc §10.1, §3 Case A). Written
    # under this file's own fixed test SEG id ("seg01"), not the real
    # segment name -- only the BYTE SIZES are the real corpus's own.
    (ctx.segments_dir / f"segpack_{SEG}.json").write_bytes(b"x" * 297_469)
    (ctx.segments_dir / f"{SEG}.draft.json").write_bytes(b"x" * 262_817)
    seg66_estimate = DRIVER.fixreview_context_estimate(ctx, SEG)
    assert seg66_estimate is not None
    assert seg66_estimate >= 269_781, (
        f"this formula's conservative prose ratio must never read BELOW the "
        f"doc's exact tokenization (269,781) -- got {seg66_estimate}, which "
        f"would mean this formula can under-reserve budget")
    assert DRIVER.effective_fix_mode(ctx, SEG) == DRIVER.FIX_MODE_HANDOFF, (
        "seg66 is the corpus's one real, 39.2%-over outlier -- must fall back")

    # seg27 -- tome1's second-largest real segment, same book (doc §10.4).
    (ctx.segments_dir / f"segpack_{SEG}.json").write_bytes(b"x" * 157_383)
    (ctx.segments_dir / f"{SEG}.draft.json").write_bytes(b"x" * 155_459)
    seg27_estimate = DRIVER.fixreview_context_estimate(ctx, SEG)
    assert seg27_estimate is not None
    assert seg27_estimate >= 160_799, (
        f"this formula must never read BELOW the doc's exact tokenization "
        f"(160,799) -- got {seg27_estimate}")
    assert DRIVER.effective_fix_mode(ctx, SEG) == DRIVER.FIX_MODE_CODEX, (
        "seg27 has +17.0% real margin (33,001 tokens headroom) -- must stay codex, "
        "not a near-miss")


def test_the_fallback_is_recorded_in_the_journal(tmp_path, monkeypatch):
    """An operator watching a --fix-mode=codex project who sees a plain
    `review` dispatch where a merged `fixreview` was expected (or a
    `needs_fix` handoff) has no other way to learn WHY -- see
    _journal_fixreview_size_fallback()'s own docstring in the driver."""
    ctx = _ctx(tmp_path, max_fixreview_context_tokens=1)
    monkeypatch.setattr(DRIVER, "write_ledger", lambda *a, **k: {"success": True})
    dispatched = _loop(monkeypatch, ctx,
                       [{"action": "review", "round_label": "1"},
                        {"action": "cap_reached", "findings": []}])

    DRIVER.process_segment(SEG, ctx)

    assert [d["kind"] for d in dispatched] == ["review"], (
        "a budget of 1 token must have downgraded fixreview -> review")
    journal = DRIVER.journal_path(ctx.dirs["durable_root"], ctx.session_id)
    events = [json.loads(ln) for ln in journal.read_text(encoding="utf-8").splitlines()]
    fallbacks = [e for e in events if e.get("type") == "fixreview_size_fallback"]
    assert len(fallbacks) == 1, fallbacks
    assert fallbacks[0]["seg"] == SEG
    assert fallbacks[0]["round_label"] == "1"
    assert fallbacks[0]["configured_fix_mode"] == DRIVER.FIX_MODE_CODEX
    assert fallbacks[0]["budget_tokens"] == 1


def test_a_large_style_bible_pushes_an_otherwise_fine_segment_over_budget(tmp_path):
    """THE TEST THAT PROVES THE OVERHEAD IS MEASURED PER PROJECT, NOT BAKED
    IN. Same ctx, same segpack, same draft, same review_TASK.md throughout
    -- the ONLY thing that changes is style_bible.md's own size, read fresh
    at runtime by fixreview_context_estimate() every call. A module
    constant could never have known this project's own style_bible.md size
    in advance; only measuring it, per project, at the moment of the
    decision, catches this."""
    ctx = _ctx(tmp_path)
    _seed_fixreview_inputs(ctx)
    assert DRIVER.effective_fix_mode(ctx, SEG) == DRIVER.FIX_MODE_CODEX, (
        "control: a small style_bible.md leaves this otherwise-tiny segment in budget")

    (ctx.dirs["durable_root"] / "style_bible.md").write_bytes(b"x" * 900_000)

    assert DRIVER.effective_fix_mode(ctx, SEG) == DRIVER.FIX_MODE_HANDOFF, (
        "a 900 KB style_bible.md alone must push this segment over the default budget")


def test_both_loaders_read_max_fixreview_context_tokens(tmp_path):
    """#409 track B, the size fallback. Mirrors test_both_loaders_read_the_
    two_new_knobs above: BOTH loaders resolve this key from the same
    profile with the same default -- see load_engine_config()'s own comment
    on this key for why it is read there too even though nothing in
    admission math consumes it."""
    root = _profile_root(tmp_path, "  max_fixreview_context_tokens: 12345\n")
    for loader in ("load_engine_config", "load_translate_config"):
        cfg = getattr(DRIVER, loader)(root)
        assert cfg["max_fixreview_context_tokens"] == 12345


def test_both_loaders_apply_the_max_fixreview_context_tokens_default(tmp_path):
    """profile.schema.json documents 193800 as the default: the pinned codex
    model's own context_window (272,000) times effective_context_window_
    percent (95 -- easy to miss next to the first field, and both matter)
    gives the 258,400-token window this mode may actually plan against,
    times 0.75 (the 25% margin bar this mode must clear) gives 193,800.
    Written here literally, not imported from the production constant, so
    this test would notice either side drifting from the other."""
    root = _profile_root(tmp_path)
    for loader in ("load_engine_config", "load_translate_config"):
        cfg = getattr(DRIVER, loader)(root)
        assert cfg["max_fixreview_context_tokens"] == 193800


# ---------------------------------------------------------------------------
# The publication P1 -- reproduced against the REAL publish_txn(), not the
# probe's stand-ins (claude-plugins-runs/lt-409-stepB/
# probe_nondestructive_publish.py), which fix-codexjob found model the
# shipped code badly in at least two ways. Everything below is built from
# reading publish_txn() itself: the `expected_destination is not None`
# branch (segment_dispatch_driver.py, os.link()/os.replace() pair) and the
# `except FileExistsError` reconciliation right after it -- verified at the
# source, not taken on relay.
# ---------------------------------------------------------------------------

_P1_RACER_BYTES = b'{"racer": "content a user typed and would lose"}'


def _p1_setup(ctx, *, round_label="1"):
    """A state publish_txn() could ACTUALLY be handed: a real canonical
    draft+review pair, real staged "new" content at publish_txn()'s own
    staged_paths(), and a real, _is_valid_intent()-passing intent -- the
    same shape tests/txn_publish.test.py's own _setup()/_intent() build,
    reproduced here because that file is not mine to touch for this test
    and publish_txn() must be driven by an authentic intent, never a
    hand-built decision (classify_txn_recovery() is what mints a `binding`;
    publish_txn() refuses anything without one)."""
    _write_canonical(ctx, draft_body="old", review={"old": "review"})
    draft_path = ctx.segments_dir / f"{SEG}.draft.json"
    review_path = ctx.segments_dir / f"{SEG}.review.json"
    ctx.txn_dir.mkdir(parents=True, exist_ok=True)

    paths = DRIVER.staged_paths(ctx.txn_dir, SEG, round_label)
    paths["draft"].write_text(json.dumps({"seg": SEG, "dispatch_token": DRAFT_TOKEN,
                                          "blocks": {"b": "new"}}), encoding="utf-8")
    paths["review"].write_text(json.dumps({"new": "review"}), encoding="utf-8")

    def _h(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    ok = DRIVER.write_txn_intent(ctx.txn_dir, SEG, {
        "txn_schema": DRIVER.TXN_SCHEMA_VERSION,
        "txn_id": f"{RUN}:{SEG}:{round_label}:1",
        "phase": "prepared",
        "round_label": round_label,
        "pre_edit_draft_sha1": DRAFT_SHA1.draft_content_sha1(draft_path),
        "pre_edit_draft_token": DRAFT_TOKEN,
        "staged_draft_sha256": _h(paths["draft"]),
        "staged_review_sha256": _h(paths["review"]),
        "review_preimage": {"sha256": _h(review_path)},
    })
    assert ok, "fixture's own intent must be valid, or nothing below tests publish_txn() at all"
    return paths


def _p1_decide(ctx):
    """The REAL decision, from the REAL classifier -- never a literal dict.
    Matches tests/txn_publish.test.py's own _decide(): a hand-built decision
    cannot mint the `binding` publish_txn() requires, so it would refuse
    for a reason that has nothing to do with the race under test."""
    observed = DRIVER.gather_txn_observed(SEG, ctx.txn_dir, ctx.segments_dir,
                                          ctx.dirs["scripts_dir"])
    return DRIVER.classify_txn_recovery(observed)


@pytest.mark.xfail(
    strict=True,
    reason=(
        "#409 track B, the publication P1. os.link()'s pin captures whatever inode is "
        "CURRENT at the destination -- it cannot capture bytes a rename-based racer "
        "installs AFTER the pin, and os.replace() a few lines later destroys them with "
        "no trace. strict=True so this flips to an unexpected PASS (and the suite says "
        "so loudly) the moment the publish-boundary redesign lands, instead of going "
        "quietly green."
    ),
)
def test_a_rename_based_racer_between_the_link_pin_and_the_replace_survives_or_refuses(
        tmp_path, monkeypatch):
    """THE RACE: publish_txn() pins whatever inode is CURRENTLY at the
    canonical destination with os.link(destination, pinned), then a few
    lines later replaces it with os.replace(source, destination). A
    RENAME-BASED writer (stage a temp file, os.rename() it over the
    canonical name -- exactly what the handoff fix step, a sync daemon, or
    any editor that saves via rename would do) that lands in that window
    swaps a NEW inode onto `destination` AFTER the pin already captured the
    OLD one. os.replace() then destroys whatever the racer just installed;
    the post-replace check (`_sha256_of(pinned) != expected_destination`)
    compares the OLD content against itself, matches, and nothing refuses.
    publish_txn() returns True.

    ACCEPTANCE CRITERION, matching the probe's own verdict shape exactly
    (`ok is False and survived` in probe_nondestructive_publish.py's own
    run_case()) rather than one invented for this test: the racer's bytes
    must EITHER stop the publish (a refusal) OR survive somewhere findable
    on disk. Today's shipped publish_txn() satisfies NEITHER half -- this
    is the P1, reproduced against the real function."""
    ctx = _ctx(tmp_path)
    paths = _p1_setup(ctx)
    decision = _p1_decide(ctx)
    assert decision["publish"] == ["review", "draft"], "fixture must reach the publishing state"

    real_link = DRIVER.os.link

    def _racing_link(src, dst):
        # THE PIN, unmodified -- captures whatever is at `src` RIGHT NOW.
        real_link(src, dst)
        # THE RACE, injected immediately after: only on the DRAFT's own pin
        # (review is renamed first, per the "review before draft" order
        # publish_txn()'s own docstring states, and is already durably
        # published by the time this fires) -- a rename-based writer swaps
        # a NEW inode onto the canonical name between the pin above and the
        # os.replace() a few lines later in publish_txn() itself.
        if str(src) == str(paths_dst := ctx.segments_dir / f"{SEG}.draft.json"):
            racer_tmp = paths_dst.with_name(paths_dst.name + ".racer-tmp")
            racer_tmp.write_bytes(_P1_RACER_BYTES)
            DRIVER.os.rename(str(racer_tmp), str(paths_dst))

    monkeypatch.setattr(DRIVER.os, "link", _racing_link)

    result = DRIVER.publish_txn(ctx.txn_dir, SEG, ctx.segments_dir, decision,
                                ctx.dirs["scripts_dir"])

    racer_survived = any(
        p.is_file() and p.read_bytes() == _P1_RACER_BYTES
        for p in ctx.segments_dir.iterdir()
    )
    assert result is False and racer_survived, (
        f"publish_txn() returned {result!r}; racer bytes survived on disk: {racer_survived} -- "
        f"today's os.link-pin design cannot catch a rename-based racer landing between the "
        f"link and the replace, and it is not supposed to be able to (see this test's own "
        f"docstring); expected to stay xfail until the publish-boundary redesign lands"
    )


if __name__ == "__main__":
    import sys

    sys.exit(pytest.main([__file__, "-v"]))
