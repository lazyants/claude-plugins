"""tests/prev_review_archive.test.py -- #541: codex_job.py preserves the review
verdict a promote is about to destroy, at
``segments/.prev_review.<seg>.r<label>.json``, keyed by the OUTGOING verdict's
own round label.

## Why the keying is the thing under test

Round N's canonical ``segments/{seg}.review.json`` is atomically overwritten by
round N+1's promote, so the earlier verdict is gone before the round N+1 fix
turn runs and nothing can compare a finding against the previous round's
finding on the same locus. A single slot, or a slot keyed on the INCOMING
label, does not survive contact with the pipeline: a same-label promote is
reachable (a same-run retranslate invalidates ``review_ready.py``'s
draft-freshness check, ``safe_adopt()`` refuses, and a fresh attempt promotes
at the SAME label), and either keying would let that overwrite the genuine
predecessor with a same-label verdict.

## What this file deliberately does NOT test

- That the fix turn reads the record, or what it does with it: that is prompt
  text, owned by ``tests/fix_prompt_prior_round.test.py``.
- Which round label the driver dispatches: owned by the driver's own suite.
- Any judgement about translation content. Nothing here reads prose; the
  archive is a byte copy of an already-validated artifact.

This file is self-contained (this plugin's "no shared lib between
self-contained scripts/tests" convention) and drives the REAL shipped
``codex_job.py``, never a reimplementation of its path or copy logic.
"""
import ast
import importlib.util
import json
import os
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
DRIVER_SRC = (
    PLUGIN_ROOT / "skills" / "literary-translator" / "assets" / "scripts" / "codex_job.py"
)
assert DRIVER_SRC.is_file(), f"codex_job.py not found at {DRIVER_SRC}"

_spec = importlib.util.spec_from_file_location("codex_job_prev_review_mod", str(DRIVER_SRC))
assert _spec is not None and _spec.loader is not None
codex_job = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(codex_job)

RUN_ID = "20260823T090000Z"
SEG = "seg01"


def _BUDGET():
    """A caller's phase budget that is never exhausted. The archiver takes the
    CALLER's own remaining-seconds callable because it runs under the per-segment
    flock lease and before the authoritative promote; the exhausted case has its
    own test below."""
    return 999.0


def _review(label, marker, seg=SEG):
    """A review verdict at `label`. `marker` is what distinguishes two verdicts
    that share a label -- and therefore share a dispatch_token, which is the
    whole reason a slot's own token cannot identify a verdict instance."""
    return {
        "clean": False,
        "coverage_ok": True,
        "findings": [{"loc": "PARA:%s:0005" % seg, "severity": "high",
                      "issue": marker, "suggest": "s-" + marker}],
        "draft_sha1": "0123456789abcdef",
        "dispatch_token": "%s:%s:r%s" % (RUN_ID, seg, label),
    }


def _job(root, kind="review", label="2", seg=SEG):
    """Every job in this file comes from here, the FRONTBACK one included: a
    hand-built constructor call in one test would keep passing its own fixture
    while the shared shape drifted underneath every other test."""
    segdir = root / "segments"
    segdir.mkdir(parents=True, exist_ok=True)
    tok = ("%s:%s" % (RUN_ID, seg)) if kind == "translate" else (
        "%s:%s:r%s" % (RUN_ID, seg, label))
    return codex_job.CodexJob(
        kind=kind, seg=seg, tok=tok, disp="d1", root=str(root),
        companion="/fake/codex-companion.mjs", prompt_text="⟦JOB_OUT⟧",
        prompt_file="/fake/prompt.txt", deadline_sec=600, poll_sec=5,
        effort="high", node="/usr/bin/node", run_id=RUN_ID,
    )


def _write_canonical(root, obj, seg=SEG):
    (root / "segments").mkdir(parents=True, exist_ok=True)
    p = root / "segments" / ("%s.review.json" % seg)
    p.write_text(json.dumps(obj), encoding="utf-8")
    return p


def _slot(root, label, seg=SEG):
    return root / "segments" / (".prev_review.%s.r%s.json" % (seg, label))


# ---------------------------------------------------------------------------
# The positive, and the negatives that are only meaningful paired with it.
# ---------------------------------------------------------------------------

def test_review_promote_preserves_the_outgoing_verdict_under_its_own_label(tmp_path):
    _write_canonical(tmp_path, _review("1", "r1-verdict"))
    _job(tmp_path, label="2")._archive_outgoing_review(_BUDGET)
    kept = json.loads(_slot(tmp_path, "1").read_text(encoding="utf-8"))
    assert kept["findings"][0]["issue"] == "r1-verdict"
    assert kept["dispatch_token"] == "%s:%s:r1" % (RUN_ID, SEG)
    assert not _slot(tmp_path, "2").exists(), (
        "the slot is keyed by the OUTGOING verdict's label, never by the "
        "incoming one -- keying on the incoming label is what a same-label "
        "promote then overwrites"
    )


def test_same_label_promote_leaves_the_genuine_predecessor_intact(tmp_path):
    """The ordering this design exists for. A same-run retranslate makes the
    stored review stale, ``safe_adopt()`` refuses, and a fresh attempt promotes
    at the SAME label -- so an r2 verdict can be the OUTGOING one while slot r1
    must keep holding the real predecessor."""
    _write_canonical(tmp_path, _review("1", "r1-verdict"))
    _job(tmp_path, label="2")._archive_outgoing_review(_BUDGET)

    _write_canonical(tmp_path, _review("2", "first-r2"))
    _job(tmp_path, label="2")._archive_outgoing_review(_BUDGET)      # the same-label promote

    assert json.loads(_slot(tmp_path, "1").read_text())["findings"][0]["issue"] == "r1-verdict", (
        "a same-label promote must not disturb slot r1 -- losing it here is "
        "exactly the defect a single slot has"
    )
    assert json.loads(_slot(tmp_path, "2").read_text())["findings"][0]["issue"] == "first-r2"


def test_a_later_promote_replaces_that_labels_slot_rather_than_accumulating(tmp_path):
    _write_canonical(tmp_path, _review("1", "superseded-r1"))
    _job(tmp_path, label="2")._archive_outgoing_review(_BUDGET)
    _write_canonical(tmp_path, _review("1", "current-r1"))
    _job(tmp_path, label="2")._archive_outgoing_review(_BUDGET)
    assert json.loads(_slot(tmp_path, "1").read_text())["findings"][0]["issue"] == "current-r1"


def test_translate_promote_archives_nothing_and_removes_nothing(tmp_path):
    """Paired with the review positive above: a wholly broken archiver would
    pass this one on its own."""
    _write_canonical(tmp_path, _review("1", "r1-verdict"))
    pre_existing = _slot(tmp_path, "1")
    pre_existing.write_text(json.dumps(_review("1", "left-alone")), encoding="utf-8")
    _job(tmp_path, kind="translate")._archive_outgoing_review(_BUDGET)
    assert json.loads(pre_existing.read_text())["findings"][0]["issue"] == "left-alone"


def test_first_ever_review_promote_has_nothing_to_archive(tmp_path):
    (tmp_path / "segments").mkdir(parents=True, exist_ok=True)
    _job(tmp_path, label="1")._archive_outgoing_review(_BUDGET)
    assert list((tmp_path / "segments").glob(".prev_review.*")) == []


@pytest.mark.parametrize("token", [
    "%s:%s:rfinal" % (RUN_ID, SEG),           # no fix turn can ever consume it
    "%s:%s:r2x" % (RUN_ID, SEG),              # not a numeric round
    "%s:%s:r0" % (RUN_ID, SEG),               # no round 0 is ever minted
    "%s:%s:r01" % (RUN_ID, SEG),              # zero-padded: keys a slot nothing asks for
    # SUPERSCRIPT TWO, written via chr() so no non-ASCII byte sits in this file:
    # str.isdigit() answers True for it, so a naive digit check would place the
    # slot under a label no consumer will ever ask for.
    "%s:%s:r%s" % (RUN_ID, SEG, chr(0x00B2)),
    "%s:segOTHER:r1" % RUN_ID,                # names another segment
    ":%s:r1" % SEG,                           # no run minted it: the seg and the
                                              # label both parse, and only the
                                              # empty run piece rejects it
    "%s:%s" % (RUN_ID, SEG),                  # a translate-shaped token
    "",
])
def test_an_outgoing_verdict_this_archiver_cannot_place_is_not_archived(tmp_path, token):
    obj = _review("1", "x")
    obj["dispatch_token"] = token
    _write_canonical(tmp_path, obj)
    _job(tmp_path, label="2")._archive_outgoing_review(_BUDGET)
    assert list((tmp_path / "segments").glob(".prev_review.*")) == []


def test_an_unparseable_or_tokenless_outgoing_canonical_is_not_archived(tmp_path):
    (tmp_path / "segments").mkdir(parents=True, exist_ok=True)
    (tmp_path / "segments" / ("%s.review.json" % SEG)).write_text("{not json", encoding="utf-8")
    _job(tmp_path, label="2")._archive_outgoing_review(_BUDGET)
    assert list((tmp_path / "segments").glob(".prev_review.*")) == []

    _write_canonical(tmp_path, {"clean": False, "findings": []})
    _job(tmp_path, label="2")._archive_outgoing_review(_BUDGET)
    assert list((tmp_path / "segments").glob(".prev_review.*")) == []


def test_a_symlinked_canonical_is_refused_rather_than_followed(tmp_path):
    (tmp_path / "segments").mkdir(parents=True, exist_ok=True)
    elsewhere = tmp_path / "elsewhere.json"
    elsewhere.write_text(json.dumps(_review("1", "off-tree")), encoding="utf-8")
    os.symlink(str(elsewhere), str(tmp_path / "segments" / ("%s.review.json" % SEG)))
    _job(tmp_path, label="2")._archive_outgoing_review(_BUDGET)
    assert list((tmp_path / "segments").glob(".prev_review.*")) == []


# ---------------------------------------------------------------------------
# Remove-first: a failed write degrades to ABSENCE, never to a stale body.
# ---------------------------------------------------------------------------

def test_a_failed_slot_write_leaves_absence_not_the_superseded_body(tmp_path, monkeypatch):
    """Two verdicts at one label share a dispatch_token, so a surviving stale
    body is indistinguishable from the real predecessor by any check the
    consumer can run. Unlinking before writing is what makes every ordinary
    write failure degrade to absence instead."""
    _write_canonical(tmp_path, _review("1", "superseded-r1"))
    _job(tmp_path, label="2")._archive_outgoing_review(_BUDGET)
    assert json.loads(_slot(tmp_path, "1").read_text())["findings"][0]["issue"] == "superseded-r1"

    _write_canonical(tmp_path, _review("1", "current-r1"))
    real_open = os.open

    def refuse_the_temp(path, flags, *a, **kw):
        if ".prev_review_tmp." in str(path):
            raise OSError(28, "No space left on device")
        return real_open(path, flags, *a, **kw)

    monkeypatch.setattr(codex_job.os, "open", refuse_the_temp)
    _job(tmp_path, label="2")._archive_outgoing_review(_BUDGET)
    monkeypatch.undo()

    assert not _slot(tmp_path, "1").exists(), (
        "a failed refresh must leave NO record rather than the superseded one "
        "-- absence is the case the fix prompt already handles"
    )
    assert list((tmp_path / "segments").glob(".prev_review_tmp.*")) == [], (
        "the temp file must not survive a failed write"
    )


def test_a_write_that_makes_no_progress_abandons_rather_than_spinning(tmp_path, monkeypatch):
    """A zero-length write is not progress, and it reaches the same short-write
    guard a partial write does: nothing is published and the temp file goes.
    Deleting that guard publishes an EMPTY slot here, which is the far worse
    outcome -- absence is a case the fix prompt handles, a zero-byte record is
    an unparseable one it does not."""
    _write_canonical(tmp_path, _review("1", "r1-verdict"))
    monkeypatch.setattr(codex_job.os, "write", lambda fd, data: 0)
    _job(tmp_path, label="2")._archive_outgoing_review(_BUDGET)
    monkeypatch.undo()
    assert list((tmp_path / "segments").glob(".prev_review.*")) == [], (
        "a stalled write must leave neither a slot nor its temp file"
    )


def test_the_archiver_never_raises_whatever_it_hits(tmp_path, monkeypatch):
    _write_canonical(tmp_path, _review("1", "r1-verdict"))

    def explode(*a, **kw):
        raise RuntimeError("filesystem gremlin")

    monkeypatch.setattr(codex_job.os, "replace", explode)
    _job(tmp_path, label="2")._archive_outgoing_review(_BUDGET)      # must not raise
    monkeypatch.undo()


def test_the_archiver_touches_no_field_finalize_reads(tmp_path):
    """The whole-set assertion: an archive failure must be invisible in every
    field the job's own outcome is built from."""
    _write_canonical(tmp_path, _review("1", "r1-verdict"))
    watched = ("promoted", "adopted", "ok", "reason", "canonical_unreadable",
               "error_detail", "job_status")
    job = _job(tmp_path, label="2")
    before = {k: getattr(job, k, None) for k in watched}
    job._archive_outgoing_review(_BUDGET)
    assert {k: getattr(job, k, None) for k in watched} == before


# ---------------------------------------------------------------------------
# Wiring: a correct helper called from nowhere leaves the suite green.
# ---------------------------------------------------------------------------

def test_every_promote_into_the_canonical_archives_first(tmp_path):
    """Both ``os.replace(..., self.canonical)`` sites -- ``adopt_pending()``'s
    and ``run()``'s post-launch promote -- must be immediately preceded by the
    archive call. Reverting either single call line would otherwise leave every
    test above green."""
    tree = ast.parse(DRIVER_SRC.read_text(encoding="utf-8"))

    def is_canonical_promote(node):
        if not (isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)):
            return False
        call = node.value
        func = call.func
        if not (isinstance(func, ast.Attribute) and func.attr == "replace"):
            return False
        if not (isinstance(func.value, ast.Name) and func.value.id == "os"):
            return False
        return any(
            isinstance(arg, ast.Attribute) and arg.attr == "canonical"
            for arg in call.args
        )

    def is_archive_call(node):
        return (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Attribute)
            and node.value.func.attr == "_archive_outgoing_review"
        )

    promotes = 0
    for parent in ast.walk(tree):
        # Every statement list, discovered rather than named: a hand-kept
        # ("body", "orelse", ...) tuple silently stops covering a promote that
        # moves into a field it does not list.
        for _field, block in ast.iter_fields(parent):
            if not (isinstance(block, list) and block
                    and all(isinstance(n, ast.stmt) for n in block)):
                continue
            for i, stmt in enumerate(block):
                if not is_canonical_promote(stmt):
                    continue
                promotes += 1
                assert i > 0 and is_archive_call(block[i - 1]), (
                    "os.replace(..., self.canonical) at line %d is not "
                    "immediately preceded by self._archive_outgoing_review(...) "
                    "-- the predecessor verdict it destroys would be lost"
                    % stmt.lineno
                )
    archive_args = {}
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.FunctionDef):
            continue
        for node in ast.walk(fn):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "_archive_outgoing_review"):
                assert len(node.args) == 1 and isinstance(node.args[0], ast.Attribute), (
                    "the archive call in %s must pass exactly one budget attribute"
                    % fn.name
                )
                archive_args[fn.name] = node.args[0].attr
    assert archive_args == {"adopt_pending": "poll_remaining", "run": "finalize_timeout"}, (
        "each call site must pass its OWN phase budget, not the other's and not "
        "the job-wide ceiling: a poll-window caller spending finalize_timeout eats "
        "the reserve finalize() needs, and the post-launch promote given "
        "poll_remaining would skip the archive on every ordinary run. Got %r"
        % (archive_args,)
    )

    assert promotes == 2, (
        "expected exactly the two known promote-into-canonical sites "
        "(adopt_pending and run); found %d -- a new one needs the archive "
        "call too" % promotes
    )


def test_a_verdict_an_operator_rejected_is_archived_exactly_like_any_other(tmp_path):
    """A numbered-round rejection routes to a fresh review at the NEXT label, and
    that review's promote archives the rejected verdict. The archiver must not
    special-case it: `reject_review.py`'s own
    ``segments/{seg}.review_rejected.json`` stays the authority on what does not
    bind, and giving this copy a rejection-aware branch would make the archive a
    second authority beside it. What keeps the rejected remedy from being
    re-applied is the fix prompt's context-not-authority clause, pinned in
    tests/fix_prompt_prior_round.test.py -- not an omission here."""
    _write_canonical(tmp_path, _review("1", "rejected-r1"))
    (tmp_path / "segments" / ("%s.review_rejected.json" % SEG)).write_text(
        json.dumps({"seg": SEG, "dispatch_token": "%s:%s:r1" % (RUN_ID, SEG)}), encoding="utf-8")
    _job(tmp_path, label="2")._archive_outgoing_review(_BUDGET)
    assert json.loads(_slot(tmp_path, "1").read_text())["findings"][0]["issue"] == "rejected-r1"


def test_a_frontback_segment_archives_like_any_other(tmp_path):
    """A seg id may be a ``FRONTBACK:{id}`` unit -- the module's own segment-id
    contract says so -- and its dispatch token therefore carries FOUR
    colon-separated pieces, not three. Counting colons silently excluded that
    whole segment class while every ``seg01`` assertion above stayed green."""
    seg = "FRONTBACK:fm01"
    _write_canonical(tmp_path, _review("1", "fb-r1", seg=seg), seg=seg)
    _job(tmp_path, label="2", seg=seg)._archive_outgoing_review(_BUDGET)
    kept = _slot(tmp_path, "1", seg=seg)
    assert kept.is_file(), (
        "a FRONTBACK unit must get a predecessor record like any other segment"
    )
    assert json.loads(kept.read_text())["dispatch_token"] == "%s:%s:r1" % (RUN_ID, seg)


def test_an_exhausted_caller_budget_abandons_the_copy(tmp_path):
    """The archiver runs under the per-segment flock lease and BEFORE the
    authoritative os.replace, so it takes the caller's own phase budget for the
    same reason every other read loop in this module does. An exhausted budget
    must abandon to absence, and must not stop the promote it precedes."""
    _write_canonical(tmp_path, _review("1", "r1-verdict"))
    _job(tmp_path, label="2")._archive_outgoing_review(lambda: 0)
    assert list((tmp_path / "segments").glob(".prev_review.*")) == []


def test_a_budget_spent_by_the_EOF_read_leaves_the_existing_record_untouched(tmp_path):
    """The read that returns EOF can itself spend the last of the caller's phase
    budget. Returning the payload anyway would let the caller decode it, unlink
    the slot and publish a replacement -- every one of those steps under the
    per-segment flock lease the budget exists to bound, and every one after this
    advisory copy stopped being affordable.

    What makes this observable at all is the UNLINK: the archiver removes the
    slot before writing, so a caller that proceeds past a spent budget destroys
    the genuine predecessor on its way to a write it can no longer afford. Not
    proceeding leaves that record in place, which is why this asserts the OLD
    body is still there rather than merely asserting absence -- absence is what
    every other failure in this file produces, so an absence assertion here
    would pass against the unfixed code too.
    """
    _write_canonical(tmp_path, _review("1", "genuine-r1"))
    _job(tmp_path, label="2")._archive_outgoing_review(_BUDGET)
    assert json.loads(_slot(tmp_path, "1").read_text())["findings"][0]["issue"] == "genuine-r1"

    # A newer verdict at the same label, archived under a budget that runs out
    # exactly on the EOF read: two positive answers (the data read and the EOF
    # read), then nothing left.
    _write_canonical(tmp_path, _review("1", "newer-r1"))
    answers = [999.0, 999.0]

    def budget_spent_by_eof():
        return answers.pop(0) if answers else 0.0

    _job(tmp_path, label="2")._archive_outgoing_review(budget_spent_by_eof)

    kept = _slot(tmp_path, "1")
    assert kept.is_file(), (
        "the genuine predecessor must survive an archive that ran out of budget "
        "before it unlinked anything -- destroying it to attempt an unaffordable "
        "write is strictly worse than not trying"
    )
    assert json.loads(kept.read_text())["findings"][0]["issue"] == "genuine-r1"
    assert list((tmp_path / "segments").glob(".prev_review_tmp.*")) == []


def test_the_read_half_consults_the_budget_before_reading(tmp_path):
    """Pins the READ check independently of the write one.

    Both halves fail CLOSED to absence, so the final on-disk state is identical
    whether the budget is checked before the read or only before the write --
    which is exactly why an outcome assertion cannot tell them apart. What
    differs is how much work runs under the flock lease before anything notices,
    and the observable for that is how often the caller's budget is consulted:
    the read loop asks before every read INCLUDING the one that returns EOF, so
    a small canonical produces two consultations. The write half is a single
    bounded os.write and asks nothing, so every count here is the read loop's.
    Drop the read check and it falls to zero."""
    _write_canonical(tmp_path, _review("1", "r1-verdict"))
    calls = []

    def counting_budget():
        calls.append(1)
        return 999.0

    _job(tmp_path, label="2")._archive_outgoing_review(counting_budget)
    assert _slot(tmp_path, "1").is_file(), "the control path must still archive"
    assert len(calls) >= 2, (
        "expected the read loop to consult the budget before each read -- two "
        "for a small file, including the read that returns EOF; got %d, which is "
        "what a budget snapshotted once, or not read at all, looks like" % len(calls)
    )


def test_the_slot_name_cannot_be_seen_by_a_draft_enumeration(tmp_path):
    """Both dispatch-evidence scans over ``segments/`` skip the dot-prefixed
    namespace before any suffix test (#428), and a canonical ``{seg}.draft.json``
    can never be dot-prefixed. Pinning BOTH properties of this name -- the
    leading dot and the suffix -- is what keeps a future rename from walking
    back into either scan; the dot is what actually carries it, and the suffix
    check is the belt for a scan that ever drops the dot filter again."""
    name = os.path.basename(_job(tmp_path, label="2")._prev_review_slot("1"))
    assert name == ".prev_review.%s.r1.json" % SEG, (
        "the name must keep its leading dot -- what both scans filter on -- and "
        "must not end in .draft.json"
    )

    # Drive the real filters rather than restating them: both are one-liners and
    # a hand-typed copy here would agree with a broken scan by construction.
    for src in ("select_segments.py", "backfill_resume_gate_ack.py"):
        text = (PLUGIN_ROOT / "skills" / "literary-translator" / "assets"
                / "scripts" / src).read_text(encoding="utf-8")
        assert 'startswith(".")' in text, (
            "%s no longer drops dot-prefixed entries before its suffix test, so "
            "this slot file would be read as a draft" % src
        )


if __name__ == "__main__":
    import sys

    sys.exit(pytest.main([__file__, "-v"]))
