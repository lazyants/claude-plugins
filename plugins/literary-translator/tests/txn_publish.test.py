"""#409 track B -- the renames, the only step that touches work the user owns.

Nothing in the shipped driver calls publish_txn() yet; it becomes reachable
only when the dispatch path is wired to `--fix-mode=codex`. These tests are
therefore the entire specification of its behaviour, and they are written
around one property above all others:

  REVIEW BEFORE DRAFT, DURABLY. review_ready.py compares a candidate review
  against the CURRENT canonical draft, so "old draft + new review" is a
  SHA-consistent intermediate state and "new draft + old review" is not.
  Ordering the two renames does not by itself guarantee that -- a crash
  between them can persist the draft rename and lose the review rename,
  materialising exactly the inconsistent pair the order exists to avoid.
"""

import hashlib
import importlib.util
import json
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


DRIVER = _load_module(DRIVER_SRC, "segment_dispatch_driver_txn_publish")

SEG = "seg01"
ROUND = "1"

# What _setup writes as the PRE-publication canonical pair. Named, so an
# assertion cannot drift from the fixture the way the literals first did.
OLD_DRAFT = {"seg": SEG, "dispatch_token": "RUN:seg01", "blocks": {"b": "old"}}
OLD_REVIEW = {"old": "review"}


@pytest.fixture()
def dirs(tmp_path):
    txn = tmp_path / "runs" / "R" / "txn"
    segments = tmp_path / "segments"
    txn.mkdir(parents=True)
    segments.mkdir(parents=True)
    return {"txn": txn, "segments": segments}


def _intent(**over):
    base = {
        "txn_schema": 1, "txn_id": "RUN:seg01:1:1", "phase": "prepared",
        "round_label": ROUND,
        "pre_edit_draft_sha1": "pre", "pre_edit_draft_token": "RUN:seg01",
        "staged_draft_sha256": "sd", "staged_review_sha256": "sr",
        "review_preimage": {"absent": True},
    }
    base.update(over)
    return base


def _setup(dirs, *, staged=("draft", "review"), canonical=True):
    """Build a state that could ACTUALLY OCCUR.

    The first version of this helper recorded placeholder strings ("sd"/"sr")
    as the staged hashes and a bare {"absent": true} review preimage while
    writing a canonical review to disk -- a combination no real transaction
    ever produces. Every test passed anyway, because publish_txn() did not yet
    revalidate. Adding the revalidation turned three of them red, which is the
    fixtures being wrong rather than the fix: a test built on an impossible
    state cannot be evidence about a real one."""
    paths = DRIVER.staged_paths(dirs["txn"], SEG, ROUND)
    for what in staged:
        paths[what].write_text(json.dumps({"new": what}), encoding="utf-8")

    draft_path = dirs["segments"] / f"{SEG}.draft.json"
    review_path = dirs["segments"] / f"{SEG}.review.json"
    if canonical:
        draft_path.write_text(
            json.dumps({"seg": SEG, "dispatch_token": "RUN:seg01", "blocks": {"b": "old"}}),
            encoding="utf-8")
        review_path.write_text('{"old": "review"}', encoding="utf-8")

    def _h(path):
        return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None

    authority = _load_module(SCRIPTS_SRC_DIR / "draft_sha1.py", "draft_sha1_for_publish_fixture")
    DRIVER.write_txn_intent(dirs["txn"], SEG, _intent(
        pre_edit_draft_sha1=authority.draft_content_sha1(draft_path) if canonical else "none",
        pre_edit_draft_token="RUN:seg01",
        staged_draft_sha256=_h(paths["draft"]) or "absent",
        staged_review_sha256=_h(paths["review"]) or "absent",
        review_preimage={"sha256": _h(review_path)} if canonical else {"absent": True},
    ))
    return paths


def _canonical(dirs, what):
    p = dirs["segments"] / f"{SEG}.{what}.json"
    return json.loads(p.read_text()) if p.exists() else None


# ---------------------------------------------------------------------------
# The happy path, and the ORDER
# ---------------------------------------------------------------------------


def test_publishes_both_in_the_order_the_decision_gives(dirs):
    _setup(dirs)
    ok = DRIVER.publish_txn(dirs["txn"], SEG, dirs["segments"],
                            {"publish": ["review", "draft"]})
    assert ok is True
    assert _canonical(dirs, "review") == {"new": "review"}
    assert _canonical(dirs, "draft") == {"new": "draft"}


def test_the_actual_rename_order_is_review_then_draft(dirs, monkeypatch):
    """Observed, not assumed: record the os.replace destinations."""
    module = _load_module(DRIVER_SRC, "driver_publish_order")
    _setup(dirs)
    order = []
    real_replace = module.os.replace

    def recording(src, dst):
        order.append(Path(dst).name)
        return real_replace(src, dst)

    monkeypatch.setattr(module.os, "replace", recording)
    module.publish_txn(dirs["txn"], SEG, dirs["segments"], {"publish": ["review", "draft"]})

    assert order == [f"{SEG}.review.json", f"{SEG}.draft.json"]


def test_a_crash_after_the_review_rename_leaves_the_CONSISTENT_pair(dirs, monkeypatch):
    """The property the order exists for: old draft + new review is
    SHA-consistent, because review_ready.py compares a candidate review
    against the CURRENT canonical draft. The reverse pair is not."""
    module = _load_module(DRIVER_SRC, "driver_publish_crash")
    _setup(dirs)
    real_replace = module.os.replace
    state = {"n": 0}

    def crashing(src, dst):
        state["n"] += 1
        if state["n"] == 2:
            raise OSError("simulated crash before the second rename")
        return real_replace(src, dst)

    monkeypatch.setattr(module.os, "replace", crashing)
    assert module.publish_txn(dirs["txn"], SEG, dirs["segments"],
                              {"publish": ["review", "draft"]}) is False

    assert _canonical(dirs, "review") == {"new": "review"}
    assert _canonical(dirs, "draft") == OLD_DRAFT, (
        "the surviving pair must be old-draft + new-review, never the reverse"
    )


def test_the_barrier_sits_BETWEEN_the_renames(dirs, monkeypatch):
    """Ordering alone does not survive a crash: without a flush between them,
    the draft rename can persist while the review rename is lost, which is the
    inconsistent pair. Failing the first directory fsync must stop the second
    rename from happening at all."""
    module = _load_module(DRIVER_SRC, "driver_publish_barrier")
    _setup(dirs)
    import os as _os
    import stat as _stat
    real_fsync = _os.fsync
    state = {"left": 1}

    def fake_fsync(fd):
        if _stat.S_ISDIR(_os.fstat(fd).st_mode) and state["left"] > 0:
            state["left"] -= 1
            raise OSError("simulated directory fsync failure")
        return real_fsync(fd)

    monkeypatch.setattr(module.os, "fsync", fake_fsync)
    assert module.publish_txn(dirs["txn"], SEG, dirs["segments"],
                              {"publish": ["review", "draft"]}) is False

    assert _canonical(dirs, "draft") == OLD_DRAFT, (
        "the draft must not be published while the review rename is not durable"
    )


# ---------------------------------------------------------------------------
# Refusals -- nothing published on any of them
# ---------------------------------------------------------------------------


def test_an_empty_publish_list_is_a_no_op(dirs):
    _setup(dirs)
    assert DRIVER.publish_txn(dirs["txn"], SEG, dirs["segments"], {"publish": []}) is True
    assert _canonical(dirs, "draft") == OLD_DRAFT
    assert _canonical(dirs, "review") == OLD_REVIEW


@pytest.mark.parametrize("decision", [{}, {"publish": None}])
def test_a_decision_naming_nothing_publishes_nothing(dirs, decision):
    _setup(dirs)
    assert DRIVER.publish_txn(dirs["txn"], SEG, dirs["segments"], decision) is True
    assert _canonical(dirs, "draft") == OLD_DRAFT


def test_a_missing_staged_source_refuses_without_touching_canonical(dirs):
    _setup(dirs, staged=("review",))
    assert DRIVER.publish_txn(dirs["txn"], SEG, dirs["segments"],
                              {"publish": ["draft"]}) is False
    assert _canonical(dirs, "draft") == OLD_DRAFT


@pytest.mark.parametrize("body", ["}{", "[]", '{"txn_schema": 1}'])
def test_an_uninterpretable_intent_refuses(dirs, body):
    """Without a trustworthy intent nothing names which staging to publish,
    and guessing would publish the wrong round over the user's text."""
    _setup(dirs)
    DRIVER.txn_intent_path(dirs["txn"], SEG).write_text(body, encoding="utf-8")
    assert DRIVER.publish_txn(dirs["txn"], SEG, dirs["segments"],
                              {"publish": ["review", "draft"]}) is False
    assert _canonical(dirs, "draft") == OLD_DRAFT
    assert _canonical(dirs, "review") == OLD_REVIEW


def test_an_absent_intent_refuses(dirs):
    _setup(dirs)
    DRIVER.txn_intent_path(dirs["txn"], SEG).unlink()
    assert DRIVER.publish_txn(dirs["txn"], SEG, dirs["segments"],
                              {"publish": ["review"]}) is False
    assert _canonical(dirs, "review") == OLD_REVIEW


def test_an_unknown_artifact_name_refuses(dirs):
    _setup(dirs)
    assert DRIVER.publish_txn(dirs["txn"], SEG, dirs["segments"],
                              {"publish": ["canon"]}) is False
    assert _canonical(dirs, "draft") == OLD_DRAFT


def test_publish_uses_the_DURABLE_round_not_a_guess(dirs):
    """Same rule as gather and cleanup: the intent owns the round. Staging for
    another round must not be publishable."""
    DRIVER.write_txn_intent(dirs["txn"], SEG, _intent(round_label="7"))
    other = DRIVER.staged_paths(dirs["txn"], SEG, "1")
    other["review"].write_text('{"wrong": "round"}', encoding="utf-8")
    (dirs["segments"] / f"{SEG}.review.json").write_text('{"old": "review"}', encoding="utf-8")

    assert DRIVER.publish_txn(dirs["txn"], SEG, dirs["segments"],
                              {"publish": ["review"]}) is False
    assert _canonical(dirs, "review") == OLD_REVIEW


# ---------------------------------------------------------------------------
# Scope
# ---------------------------------------------------------------------------


def test_publish_does_not_clean_up(dirs):
    """It renames and nothing else. Cleanup is a separate step so that a
    partial publish leaves the intent and any unconsumed staging in place for
    recovery to classify."""
    _setup(dirs)
    DRIVER.publish_txn(dirs["txn"], SEG, dirs["segments"], {"publish": ["review"]})
    assert DRIVER.txn_intent_path(dirs["txn"], SEG).exists()
    assert DRIVER.staged_paths(dirs["txn"], SEG, ROUND)["draft"].exists()


def test_publish_leaves_another_segment_alone(dirs):
    _setup(dirs)
    other = dirs["segments"] / "seg02.draft.json"
    other.write_text('{"other": true}', encoding="utf-8")
    DRIVER.publish_txn(dirs["txn"], SEG, dirs["segments"], {"publish": ["review", "draft"]})
    assert json.loads(other.read_text()) == {"other": True}


# ---------------------------------------------------------------------------
# The CAS at the publication boundary
# ---------------------------------------------------------------------------


def test_a_canonical_edit_after_classification_is_not_overwritten(dirs):
    """THE reason this boundary revalidates. The decision was computed from an
    earlier snapshot; if the canonical draft is edited between then and the
    rename, acting on the stale decision destroys that newer text -- the exact
    loss the CAS exists to prevent, arriving through the gap between the check
    and the use rather than through a missing check."""
    _setup(dirs)
    decision = DRIVER.classify_txn_recovery(
        DRIVER.gather_txn_observed(SEG, dirs["txn"], dirs["segments"], SCRIPTS_SRC_DIR))
    assert decision["publish"] == ["review", "draft"]

    newer = {"seg": SEG, "dispatch_token": "RUN:seg01", "blocks": {"b": "EDITED BY SOMEONE ELSE"}}
    (dirs["segments"] / f"{SEG}.draft.json").write_text(json.dumps(newer), encoding="utf-8")

    assert DRIVER.publish_txn(dirs["txn"], SEG, dirs["segments"], decision) is False
    assert _canonical(dirs, "draft") == newer, "the newer edit must survive"
    assert _canonical(dirs, "review") == OLD_REVIEW, "and nothing else may be published either"


def test_a_staged_file_changed_after_gathering_is_not_published(dirs):
    """The same gap from the other side: staging whose bytes no longer match
    the hash the intent recorded must not reach the canonical tree."""
    paths = _setup(dirs)
    decision = DRIVER.classify_txn_recovery(
        DRIVER.gather_txn_observed(SEG, dirs["txn"], dirs["segments"], SCRIPTS_SRC_DIR))

    paths["draft"].write_text(json.dumps({"new": "TAMPERED"}), encoding="utf-8")

    assert DRIVER.publish_txn(dirs["txn"], SEG, dirs["segments"], decision) is False
    assert _canonical(dirs, "draft") == OLD_DRAFT
    assert _canonical(dirs, "review") == OLD_REVIEW


def test_an_unchanged_state_still_publishes(dirs):
    """The revalidation must not be so strict that the normal path stops
    working -- a refusal that fires on everything is not a guard."""
    _setup(dirs)
    decision = DRIVER.classify_txn_recovery(
        DRIVER.gather_txn_observed(SEG, dirs["txn"], dirs["segments"], SCRIPTS_SRC_DIR))
    assert DRIVER.publish_txn(dirs["txn"], SEG, dirs["segments"], decision) is True
    assert _canonical(dirs, "draft") == {"new": "draft"}
