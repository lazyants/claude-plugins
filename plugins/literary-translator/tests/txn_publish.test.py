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


def _setup(dirs, *, staged=("draft", "review"), canonical=True, round_label=ROUND,
           txn_id="RUN:seg01:1:1", body=None):
    """Build a state that could ACTUALLY OCCUR.

    The first version of this helper recorded placeholder strings ("sd"/"sr")
    as the staged hashes and a bare {"absent": true} review preimage while
    writing a canonical review to disk -- a combination no real transaction
    ever produces. Every test passed anyway, because publish_txn() did not yet
    revalidate. Adding the revalidation turned three of them red, which is the
    fixtures being wrong rather than the fix: a test built on an impossible
    state cannot be evidence about a real one.

    `round_label`/`txn_id`/`body` exist so a SECOND, equally valid transaction
    can be built over the same segment -- the state that proves a publish list
    cannot identify a transaction."""
    paths = DRIVER.staged_paths(dirs["txn"], SEG, round_label)
    for what in staged:
        paths[what].write_text(
            json.dumps(body or {"new": what}) if body else json.dumps({"new": what}),
            encoding="utf-8")

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
        txn_id=txn_id,
        round_label=round_label,
        pre_edit_draft_sha1=authority.draft_content_sha1(draft_path)
        if draft_path.exists() else "none",
        pre_edit_draft_token="RUN:seg01",
        staged_draft_sha256=_h(paths["draft"]) or "absent",
        staged_review_sha256=_h(paths["review"]) or "absent",
        review_preimage={"sha256": _h(review_path)} if review_path.exists()
        else {"absent": True},
    ))
    return paths


def _canonical(dirs, what):
    p = dirs["segments"] / f"{SEG}.{what}.json"
    return json.loads(p.read_text()) if p.exists() else None


def _decide(dirs, module=DRIVER):
    """The decision the REAL classifier produces for the state on disk.

    Every test below goes through this rather than handing publish_txn a dict
    literal. That is not cosmetic: a decision now carries a `binding` -- the
    identity of the transaction the rules were applied to -- and publish_txn
    refuses anything without one. A hand-built decision cannot mint a binding,
    so it cannot publish, which is the point of the mechanism.

    It also removes a whole class of false green. The literal-dict tests
    asserted `is False` and passed both before and after that gate existed,
    because the gate refuses first and a refusal looks the same from outside
    whatever caused it. Driving the authentic decision means a refusal here is
    the refusal the test names."""
    return module.classify_txn_recovery(
        module.gather_txn_observed(SEG, dirs["txn"], dirs["segments"], SCRIPTS_SRC_DIR))


def _decide_but_publish(dirs, publish, module=DRIVER):
    """A real decision with only its `publish` list overridden.

    For guards the classifier cannot reach on its own -- it only ever emits
    (), ("draft",) and ("review","draft"), so it can never name an artifact
    called "canon". Keeping the authentic binding means the test exercises the
    guard it names instead of stopping at the identity gate."""
    return {**_decide(dirs, module), "publish": list(publish)}


# ---------------------------------------------------------------------------
# The happy path, and the ORDER
# ---------------------------------------------------------------------------


def test_publishes_both_in_the_order_the_decision_gives(dirs):
    _setup(dirs)
    decision = _decide(dirs)
    assert decision["publish"] == ["review", "draft"], "fixture must reach the publishing state"
    assert DRIVER.publish_txn(dirs["txn"], SEG, dirs["segments"], decision) is True
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
    module.publish_txn(dirs["txn"], SEG, dirs["segments"], _decide(dirs, module))

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

    decision = _decide(dirs, module)
    monkeypatch.setattr(module.os, "replace", crashing)
    assert module.publish_txn(dirs["txn"], SEG, dirs["segments"], decision) is False

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

    decision = _decide(dirs, module)
    monkeypatch.setattr(module.os, "fsync", fake_fsync)
    assert module.publish_txn(dirs["txn"], SEG, dirs["segments"], decision) is False

    assert _canonical(dirs, "draft") == OLD_DRAFT, (
        "the draft must not be published while the review rename is not durable"
    )


# ---------------------------------------------------------------------------
# Refusals -- nothing published on any of them
# ---------------------------------------------------------------------------


def test_an_empty_publish_list_is_a_no_op(dirs):
    _setup(dirs)
    assert DRIVER.publish_txn(dirs["txn"], SEG, dirs["segments"],
                              _decide_but_publish(dirs, [])) is True
    assert _canonical(dirs, "draft") == OLD_DRAFT
    assert _canonical(dirs, "review") == OLD_REVIEW


@pytest.mark.parametrize("decision", [{}, {"publish": None}])
def test_a_decision_naming_nothing_publishes_nothing(dirs, decision):
    """Naming nothing wins over carrying no binding: the empty-list exit is
    ABOVE the identity gate, so a decision that asks for no work is a success
    even when it could not have authorised any."""
    _setup(dirs)
    assert DRIVER.publish_txn(dirs["txn"], SEG, dirs["segments"], decision) is True
    assert _canonical(dirs, "draft") == OLD_DRAFT


def test_a_missing_staged_source_refuses_without_touching_canonical(dirs):
    """DEFENSE IN DEPTH, and no longer classifier-reachable -- worth saying so
    rather than letting the name imply otherwise. With the draft staging
    absent the real classifier answers staging-lost with publish=[], so this
    guard can only be reached by removing staging AFTER the decision. That is
    what the override reproduces, on an authentic binding."""
    paths = _setup(dirs)
    decision = _decide_but_publish(dirs, ["draft"])
    paths["draft"].unlink()
    assert DRIVER.publish_txn(dirs["txn"], SEG, dirs["segments"], decision) is False
    assert _canonical(dirs, "draft") == OLD_DRAFT


@pytest.mark.parametrize("body", ["}{", "[]", '{"txn_schema": 1}'])
def test_an_uninterpretable_intent_refuses(dirs, body):
    """Without a trustworthy intent nothing names which staging to publish,
    and guessing would publish the wrong round over the user's text. The
    decision is taken while the intent is still good, so the refusal is caused
    by the corruption under test rather than by a decision that was already
    unusable."""
    _setup(dirs)
    decision = _decide(dirs)
    assert decision["publish"] == ["review", "draft"]
    DRIVER.txn_intent_path(dirs["txn"], SEG).write_text(body, encoding="utf-8")
    assert DRIVER.publish_txn(dirs["txn"], SEG, dirs["segments"], decision) is False
    assert _canonical(dirs, "draft") == OLD_DRAFT
    assert _canonical(dirs, "review") == OLD_REVIEW


def test_an_absent_intent_refuses(dirs):
    _setup(dirs)
    decision = _decide(dirs)
    DRIVER.txn_intent_path(dirs["txn"], SEG).unlink()
    assert DRIVER.publish_txn(dirs["txn"], SEG, dirs["segments"], decision) is False
    assert _canonical(dirs, "review") == OLD_REVIEW


def test_an_unknown_artifact_name_refuses(dirs):
    """Also defense in depth: classify emits only (), ("draft",) and
    ("review","draft"), so it can never name "canon". Reachable only by
    overriding the list on a real decision."""
    _setup(dirs)
    assert DRIVER.publish_txn(dirs["txn"], SEG, dirs["segments"],
                              _decide_but_publish(dirs, ["canon"])) is False
    assert _canonical(dirs, "draft") == OLD_DRAFT


def test_publish_uses_the_DURABLE_round_not_a_guess(dirs):
    """Same rule as gather and cleanup: the intent owns the round.

    This is now STRUCTURAL rather than checked. The round label is read off the
    very intent whose identity the binding check just matched, so there is no
    longer a second read that could name a different round's staging. The state
    below -- an intent for round 7, staging sitting under round 1 -- is exactly
    what the previous version could mis-publish; the classifier sees staging
    that does not exist for the round the intent names and never authorises
    anything."""
    DRIVER.write_txn_intent(dirs["txn"], SEG, _intent(round_label="7"))
    other = DRIVER.staged_paths(dirs["txn"], SEG, "1")
    other["review"].write_text('{"wrong": "round"}', encoding="utf-8")
    (dirs["segments"] / f"{SEG}.review.json").write_text('{"old": "review"}', encoding="utf-8")

    assert _decide(dirs)["publish"] == [], (
        "staging for a round the intent does not name must never be authorised"
    )
    assert DRIVER.publish_txn(dirs["txn"], SEG, dirs["segments"],
                              _decide_but_publish(dirs, ["review"])) is False
    assert _canonical(dirs, "review") == OLD_REVIEW


# ---------------------------------------------------------------------------
# Scope
# ---------------------------------------------------------------------------


def test_publish_does_not_clean_up(dirs):
    """It renames and nothing else. Cleanup is a separate step so that a
    partial publish leaves the intent and any unconsumed staging in place for
    recovery to classify."""
    _setup(dirs)
    assert DRIVER.publish_txn(dirs["txn"], SEG, dirs["segments"], _decide(dirs)) is True, (
        "the publication must SUCCEED, or this asserts nothing about cleanup"
    )
    assert DRIVER.txn_intent_path(dirs["txn"], SEG).exists(), (
        "the intent survives publication -- removing it is cleanup_txn's job, and "
        "keeping the two separate is what lets recovery classify a partial publish"
    )


def test_a_partial_publish_leaves_the_unconsumed_staging(dirs, monkeypatch):
    """The other half of the same separation, and the half that actually needs
    a partial publish to exist. Truncating a publication after the review must
    leave the draft staging on disk for recovery to roll forward from.

    Split out of test_publish_does_not_clean_up, which used to fake a partial
    publish by asking for a shorter list. It cannot any more -- a truncated
    list is not the tail the classifier authorised, so the tail check refuses
    it, and the test would have asserted about a publication that never
    happened."""
    module = _load_module(DRIVER_SRC, "driver_publish_partial")
    _setup(dirs)
    decision = _decide(dirs, module)
    real_replace = module.os.replace
    state = {"n": 0}

    def crashing(src, dst):
        state["n"] += 1
        if state["n"] == 2:
            raise OSError("simulated crash before the draft rename")
        return real_replace(src, dst)

    monkeypatch.setattr(module.os, "replace", crashing)
    assert module.publish_txn(dirs["txn"], SEG, dirs["segments"], decision) is False

    assert DRIVER.txn_intent_path(dirs["txn"], SEG).exists()
    assert DRIVER.staged_paths(dirs["txn"], SEG, ROUND)["draft"].exists(), (
        "the unconsumed draft staging must survive for recovery to publish later"
    )


def test_publish_leaves_another_segment_alone(dirs):
    _setup(dirs)
    other = dirs["segments"] / "seg02.draft.json"
    other.write_text('{"other": true}', encoding="utf-8")
    assert DRIVER.publish_txn(dirs["txn"], SEG, dirs["segments"], _decide(dirs)) is True, (
        "a no-op publication leaves every other segment alone trivially"
    )
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
    assert _canonical(dirs, "review") == {"new": "review"}


# ---------------------------------------------------------------------------
# ONE REVALIDATION PER RENAME, and the identity a publish list cannot carry
# ---------------------------------------------------------------------------


def test_an_edit_landing_BETWEEN_the_two_renames_is_not_overwritten(dirs, monkeypatch):
    """The window a single revalidation could never close, and the widest one
    in the old code: it spans the directory fsync between the renames, so the
    canonical draft's observation was already stale by the time the draft was
    replaced.

    This is the shape the shipped reproduction probe uses. Before the
    per-rename revalidation it published both and returned True, destroying the
    edit."""
    module = _load_module(DRIVER_SRC, "driver_publish_between")
    paths = _setup(dirs)
    decision = _decide(dirs, module)
    assert decision["publish"] == ["review", "draft"]

    draft_path = dirs["segments"] / f"{SEG}.draft.json"
    newer = {"seg": SEG, "dispatch_token": "RUN:seg01",
             "blocks": {"b": "SAVED BY A PERSON BETWEEN THE RENAMES"}}
    real_replace = module.os.replace
    state = {"n": 0}

    def editing(src, dst):
        state["n"] += 1
        result = real_replace(src, dst)
        if state["n"] == 1:          # right after the review rename
            draft_path.write_text(json.dumps(newer), encoding="utf-8")
        return result

    monkeypatch.setattr(module.os, "replace", editing)
    assert module.publish_txn(dirs["txn"], SEG, dirs["segments"], decision) is False
    assert state["n"] == 1, "the draft rename must never have been attempted"

    assert json.loads(draft_path.read_text()) == newer, "the newer edit must survive"
    assert paths["draft"].exists(), "and its staging must be left for recovery"


def test_a_different_transaction_of_the_SAME_SHAPE_is_refused(dirs):
    """What the publish list cannot express. classify emits only three lists,
    so shape alone lets an entirely different, entirely valid transaction --
    different round, different staged bytes -- pass a list comparison and get
    published under this decision's authority."""
    _setup(dirs)
    decision = _decide(dirs)
    assert decision["publish"] == ["review", "draft"]

    # Replace the whole transaction with a valid one for another round, over
    # the SAME untouched canonical pair -- so the only thing that differs is
    # the transaction's own identity.
    DRIVER.txn_intent_path(dirs["txn"], SEG).unlink()
    for p in DRIVER.staged_paths(dirs["txn"], SEG, ROUND).values():
        p.unlink()
    _setup(dirs, round_label="9", txn_id="RUN:seg01:9:1", canonical=False)

    replacement = _decide(dirs)
    assert replacement["publish"] == decision["publish"], (
        "the premise of this test: the two transactions are indistinguishable by list"
    )
    assert replacement["binding"] != decision["binding"], "but not by binding"

    assert DRIVER.publish_txn(dirs["txn"], SEG, dirs["segments"], decision) is False
    assert _canonical(dirs, "draft") == OLD_DRAFT
    assert _canonical(dirs, "review") == OLD_REVIEW


def test_a_decision_that_did_not_come_from_the_classifier_cannot_publish(dirs):
    """A binding can only be minted by classify_txn_recovery, so a hand-built
    decision is refused before anything is touched. This makes 'publish
    something the classifier never authorised' unspellable rather than merely
    discouraged.

    THE EARLY GATE IS PROVABLY REDUNDANT, and mutation is what showed it:
    disabling `if expected is None` turns nothing red, because a None `expected`
    then meets the identity check, and there the on-disk binding is either
    non-None (mismatch, refused) or None -- which happens only when
    _is_valid_intent is false, and the assert below the identity check refuses
    that. There is no state the early gate alone rejects.

    It is kept anyway, deliberately, for two reasons that are not coverage:
    it refuses before any I/O, and it names the actual mistake ("this decision
    did not come from the classifier") instead of the downstream symptom. What
    is NOT claimed is that a test holds it -- this docstring is the record that
    one cannot, rather than an implication that one does."""
    _setup(dirs)
    assert DRIVER.publish_txn(dirs["txn"], SEG, dirs["segments"],
                              {"publish": ["review", "draft"]}) is False
    assert _canonical(dirs, "draft") == OLD_DRAFT
    assert _canonical(dirs, "review") == OLD_REVIEW


def test_a_fabricated_binding_is_refused(dirs):
    """And it cannot be forged either -- the check compares against a binding
    re-derived from disk, not against anything the caller supplies."""
    _setup(dirs)
    forged = {**_decide(dirs), "binding": ("RUN:seg01:1:1", "1", "prepared", "x", "y")}
    assert DRIVER.publish_txn(dirs["txn"], SEG, dirs["segments"], forged) is False
    assert _canonical(dirs, "draft") == OLD_DRAFT


def test_every_classifier_outcome_carries_a_binding_key(dirs):
    """Including the unobservable path, which used to be a dict literal and so
    would have been the one decision able to skip the identity gate."""
    _setup(dirs)
    observed = DRIVER.gather_txn_observed(SEG, dirs["txn"], dirs["segments"], SCRIPTS_SRC_DIR)
    assert "binding" in DRIVER.classify_txn_recovery(observed)

    unreadable = {**observed, "canonical_draft_sha256": DRIVER.TXN_UNREADABLE}
    unobservable = DRIVER.classify_txn_recovery(unreadable)
    assert unobservable["outcome"] == DRIVER.TXN_UNOBSERVABLE
    assert "binding" in unobservable, (
        "the unobservable early return must not be the one path that omits it"
    )


def _inject_after_classification(module, monkeypatch, write, *, on_call=1):
    """Run `write` in the gap between one classification and its confirms.

    `on_call` selects WHICH iteration, and it is load-bearing rather than a
    convenience. publish_txn revalidates once per rename, so a write injected
    after iteration 1 is seen by iteration 2's tail check -- which refuses, and
    the confirm under test never speaks. Mutation is what exposed that: with the
    destination confirm disabled the suite stayed green, because a test that
    looked like it targeted the confirm was actually being caught one guard
    upstream. To isolate a confirm the write must land in the SAME iteration as
    the rename it guards."""
    real = module.classify_txn_recovery
    state = {"calls": 0, "done": False}

    def classify_then_write(observed):
        result = real(observed)
        state["calls"] += 1
        if state["calls"] == on_call:
            state["done"] = True
            write()
        return result

    monkeypatch.setattr(module, "classify_txn_recovery", classify_then_write)
    return state


@pytest.mark.parametrize("what,on_call", [("review", 1), ("draft", 2)])
def test_the_destination_is_confirmed_at_the_LAST_moment_before_it_is_destroyed(
        dirs, monkeypatch, what, on_call):
    """gather reads five artifacts in a fixed order, so by the time an artifact
    is renamed its observation is already the oldest thing in the decision --
    worst of all for the canonical draft, read FIRST and renamed LAST. The
    confirm asserts that the premise the rules just consumed is still true for
    the one file about to be overwritten.

    Both artifacts, because the two differ in exactly the way that matters: the
    review is renamed in the iteration that classified it, the draft one whole
    iteration later."""
    module = _load_module(DRIVER_SRC, f"driver_publish_dest_confirm_{what}")
    _setup(dirs)
    decision = _decide(dirs, module)
    target = dirs["segments"] / f"{SEG}.{what}.json"
    newer = {"seg": SEG, "late": "EDIT LANDING AFTER CLASSIFICATION"}

    state = _inject_after_classification(
        module, monkeypatch,
        lambda: target.write_text(json.dumps(newer), encoding="utf-8"),
        on_call=on_call)

    assert module.publish_txn(dirs["txn"], SEG, dirs["segments"], decision) is False
    assert state["done"], "the injection never ran -- this test proved nothing"
    assert json.loads(target.read_text()) == newer, "the late edit must survive"


def test_staged_bytes_that_change_after_validation_never_reach_a_canonical_name(
        dirs, monkeypatch):
    """The same gap from the source side. Bytes nothing validated must not be
    published under a decision that validated different ones."""
    module = _load_module(DRIVER_SRC, "driver_publish_src_confirm")
    paths = _setup(dirs)
    decision = _decide(dirs, module)

    state = _inject_after_classification(
        module, monkeypatch,
        lambda: paths["review"].write_text('{"new": "TAMPERED"}', encoding="utf-8"))

    assert module.publish_txn(dirs["txn"], SEG, dirs["segments"], decision) is False
    assert state["done"], "the injection never ran -- this test proved nothing"
    assert _canonical(dirs, "review") == OLD_REVIEW
    assert _canonical(dirs, "draft") == OLD_DRAFT


def test_a_symlinked_canonical_destination_is_refused(dirs):
    """os.replace replaces the LINK; hashing the path reads the TARGET. Without
    this the confirm would approve one inode's bytes and the rename would
    destroy a different one."""
    _setup(dirs)
    decision = _decide(dirs)
    draft_path = dirs["segments"] / f"{SEG}.draft.json"
    real_target = dirs["segments"] / "elsewhere.draft.json"
    draft_path.rename(real_target)
    draft_path.symlink_to(real_target)

    assert DRIVER.publish_txn(dirs["txn"], SEG, dirs["segments"], decision) is False
    assert draft_path.is_symlink(), "the link itself must be left alone"
    assert json.loads(real_target.read_text()) == OLD_DRAFT


# ---------------------------------------------------------------------------
# NON-DESTRUCTIVE, NOT ATOMIC -- the os.link pin at the check/replace boundary
#
# POSIX has no compare-and-rename, so the confirm above and the os.replace
# below still resolve the same NAME twice with a gap between them. What the
# pin changes is what happens INSIDE that gap: instead of "the newer bytes
# are gone", a writer landing there gets "the newer bytes are on disk under
# another name and the transaction refused". These tests are the entire
# specification of that mechanism -- nothing above exercises os.link at all,
# so a publish_txn that never called it passed every test above already.
# ---------------------------------------------------------------------------


def test_the_happy_path_leaves_no_superseded_litter(dirs):
    """Every rename in the loop links, hashes the pin, and -- when the pin
    still holds the preimage it was taken to protect -- discards it. If the
    discard branch ever regressed, no test above would notice: they only ever
    look at return values and canonical content, never at what ELSE is sitting
    in the segments directory afterward. Glob for the pattern rather than one
    predicted name, since either artifact's pin would be litter."""
    _setup(dirs)
    decision = _decide(dirs)
    assert decision["publish"] == ["review", "draft"]
    assert DRIVER.publish_txn(dirs["txn"], SEG, dirs["segments"], decision) is True
    litter = list(dirs["segments"].glob("*.superseded-*"))
    assert litter == [], f"an ordinary publish must leave no pin behind, found {litter}"


def test_a_write_landing_between_the_check_and_the_link_is_preserved_and_refused(
        dirs, monkeypatch):
    """The race the pin exists for. `_sha256_of(destination) != expected` was
    just confirmed true (the destination still holds the preimage), so between
    that confirm and os.link a writer -- the fix step, an editor, a sync
    daemon -- rewrites the canonical review IN PLACE, on the same inode. Only
    os.link, called immediately after, can still see it: it pins whatever is
    at that name RIGHT NOW, which is the racer's bytes, not the ones the
    confirm approved a moment earlier.

    Driven by monkeypatching os.link on the module publish_txn actually calls
    through -- the driver imports `os` at module scope, so `module.os.link` is
    the same name space the function reads from -- with a wrapper that writes
    the racer's content into the link's source path (the canonical
    destination) before delegating to the real os.link. That is the shape of
    the race, reproduced without needing two real threads."""
    module = _load_module(DRIVER_SRC, "driver_publish_race_link")
    _setup(dirs)
    decision = _decide(dirs, module)
    assert decision["publish"] == ["review", "draft"]

    injected = {"seg": SEG, "raced": "WRITER LANDED BETWEEN THE CHECK AND THE LINK"}
    real_link = module.os.link
    state = {"calls": 0}

    def racing_link(src, dst, *args, **kwargs):
        state["calls"] += 1
        Path(src).write_text(json.dumps(injected), encoding="utf-8")
        return real_link(src, dst, *args, **kwargs)

    monkeypatch.setattr(module.os, "link", racing_link)

    assert module.publish_txn(dirs["txn"], SEG, dirs["segments"], decision) is False
    assert state["calls"] == 1, (
        "os.link was never called -- either this publish_txn does not pin at all, or the "
        "race never landed, and this test proved nothing either way"
    )

    pins = list(dirs["segments"].glob(f"{SEG}.review.json.superseded-*"))
    assert len(pins) == 1, f"expected exactly one preserved pin, found {pins}"
    assert json.loads(pins[0].read_text()) == injected, (
        "the preserved bytes must be the racer's, not the preimage the confirm approved -- "
        "a design that merely refused without capturing them would pass the two assertions "
        "above and lose the data anyway"
    )


def test_a_preexisting_pin_holding_the_same_preimage_does_not_block_publication(dirs):
    """The FileExistsError branch's "ours, carries nothing new" case: a pin
    already sits at this transaction's exact name (same destination, same
    txn_id) and holds the SAME bytes the confirm just approved. That can only
    be this transaction's own earlier attempt, so it must not stop the
    publish, and once the rename lands it is a duplicate of a preimage the
    intent already records by digest -- discarded like any other pin that
    turned out to match."""
    _setup(dirs)
    review_path = dirs["segments"] / f"{SEG}.review.json"
    pin_path = review_path.with_name(f"{review_path.name}.superseded-RUN:seg01:1:1")
    pin_path.write_bytes(review_path.read_bytes())

    decision = _decide(dirs)
    assert decision["publish"] == ["review", "draft"]
    assert DRIVER.publish_txn(dirs["txn"], SEG, dirs["segments"], decision) is True

    assert _canonical(dirs, "review") == {"new": "review"}
    assert _canonical(dirs, "draft") == {"new": "draft"}
    assert list(dirs["segments"].glob("*.superseded-*")) == [], (
        "a pin that turned out to hold the same preimage is a duplicate, not evidence, and "
        "must be discarded rather than left behind"
    )


def test_a_preexisting_pin_holding_different_content_refuses_and_is_left_untouched(dirs):
    """The other half of the FileExistsError branch: a pin at this exact name
    that does NOT hold the recorded preimage can only be somebody else's
    evidence -- a previous refusal that already parked a race's bytes there.
    Overwriting it would destroy the one thing this mechanism exists to
    protect, so publication refuses before touching anything, including the
    pin itself."""
    _setup(dirs)
    review_path = dirs["segments"] / f"{SEG}.review.json"
    pin_path = review_path.with_name(f"{review_path.name}.superseded-RUN:seg01:1:1")
    evidence = b'{"somebody_elses_evidence": true}'
    pin_path.write_bytes(evidence)

    decision = _decide(dirs)
    assert decision["publish"] == ["review", "draft"]
    assert DRIVER.publish_txn(dirs["txn"], SEG, dirs["segments"], decision) is False

    assert pin_path.read_bytes() == evidence, "somebody else's evidence must be left exactly as found"
    assert _canonical(dirs, "review") == OLD_REVIEW
    assert _canonical(dirs, "draft") == OLD_DRAFT


def test_a_destination_that_does_not_exist_is_not_pinned(dirs, monkeypatch):
    """The guard is `if expected_destination is not None:` -- when a canonical
    review has never existed for this segment, there is nothing to preserve
    and os.link must never even be attempted for it. The canonical draft in
    this same fixture DOES exist, so the two renames in one publication take
    different branches: draft still pins (and, matching, discards), review
    never does. Recording every os.link call's SOURCE name is what tells the
    two apart -- a "no litter afterward" assertion alone cannot, because a
    pin that was created and then discarded looks identical to one that was
    never created."""
    module = _load_module(DRIVER_SRC, "driver_publish_absent_dest")
    paths = module.staged_paths(dirs["txn"], SEG, ROUND)
    paths["draft"].write_text(json.dumps({"new": "draft"}), encoding="utf-8")
    paths["review"].write_text(json.dumps({"new": "review"}), encoding="utf-8")

    draft_path = dirs["segments"] / f"{SEG}.draft.json"
    draft_path.write_text(json.dumps(OLD_DRAFT), encoding="utf-8")
    review_path = dirs["segments"] / f"{SEG}.review.json"
    assert not review_path.exists(), "the fixture's premise: no review has ever been published"

    def _h(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    authority = _load_module(SCRIPTS_SRC_DIR / "draft_sha1.py", "draft_sha1_for_no_dest_fixture")
    module.write_txn_intent(dirs["txn"], SEG, _intent(
        pre_edit_draft_sha1=authority.draft_content_sha1(draft_path),
        pre_edit_draft_token="RUN:seg01",
        staged_draft_sha256=_h(paths["draft"]),
        staged_review_sha256=_h(paths["review"]),
        review_preimage={"absent": True},
    ))

    decision = _decide(dirs, module)
    assert decision["publish"] == ["review", "draft"], "fixture must reach the publishing state"

    real_link = module.os.link
    calls = []

    def recording_link(src, dst, *args, **kwargs):
        calls.append(Path(src).name)
        return real_link(src, dst, *args, **kwargs)

    monkeypatch.setattr(module.os, "link", recording_link)

    assert module.publish_txn(dirs["txn"], SEG, dirs["segments"], decision) is True
    assert calls == [f"{SEG}.draft.json"], (
        "os.link must be attempted for the existing draft destination and never for the "
        f"review destination, which never existed; calls were {calls}"
    )
    assert _canonical(dirs, "review") == {"new": "review"}
    assert _canonical(dirs, "draft") == {"new": "draft"}
    assert list(dirs["segments"].glob("*.superseded-*")) == []
