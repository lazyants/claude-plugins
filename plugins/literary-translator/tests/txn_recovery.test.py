"""#409 track B -- the paired-publication recovery classifier.

The classifier is pure, so every reachable state is exercised directly rather
than by staging real files and killing real processes. Two properties are
asserted structurally rather than trusted:

  TOTALITY  -- every input produces a decision (no state falls through);
  DISJOINTNESS -- the procedure is ORDERED and first-match-wins, so a state
                  matching several descriptions still has exactly one
                  outcome. This is the property a table of independent rows
                  did NOT have, which is why the table was replaced.

The ordering that carries the most weight: CAS refusal sits ABOVE staging
loss, so a diverged preimage can never be read as mere staging loss and
roll-forward can never overwrite an unrelated newer draft.
"""

import importlib.util
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
DRIVER_SRC = HERE.parent / "skills" / "literary-translator" / "assets" / "scripts" / "segment_dispatch_driver.py"
assert DRIVER_SRC.is_file(), f"expected script not found: {DRIVER_SRC}"


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, str(path))
    assert spec is not None and spec.loader is not None, f"cannot load {path}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


DRIVER = _load_module(DRIVER_SRC, "segment_dispatch_driver_txn_recovery")

PRE_SHA1 = "pre-edit-content-sha1"
PRE_TOKEN = "RUN:seg01"
STAGED_DRAFT = "sha256-staged-draft"
STAGED_REVIEW = "sha256-staged-review"
REVIEW_PRE = "sha256-review-preimage"


def _intent(phase="prepared", review_preimage=None):
    return {
        "txn_schema": 1,
        "txn_id": "RUN:seg01:1:1",
        "phase": phase,
        "round_label": "1",
        "pre_edit_draft_sha1": PRE_SHA1,
        "pre_edit_draft_token": PRE_TOKEN,
        "staged_draft_sha256": STAGED_DRAFT,
        "staged_review_sha256": STAGED_REVIEW,
        "review_preimage": review_preimage if review_preimage is not None else {"sha256": REVIEW_PRE},
    }


def _observed(**over):
    base = {
        "intent": _intent(),
        "staged_draft_sha256": STAGED_DRAFT,
        "staged_review_sha256": STAGED_REVIEW,
        "canonical_draft_sha256": "sha256-canonical-old-draft",
        "canonical_review_sha256": REVIEW_PRE,
        "canonical_draft_content_sha1": PRE_SHA1,
        "canonical_draft_token": PRE_TOKEN,
    }
    base.update(over)
    return base


# ---------------------------------------------------------------------------
# Step 0 -- no intent
# ---------------------------------------------------------------------------


def test_no_intent_and_no_staging_proceeds():
    d = DRIVER.classify_txn_recovery(_observed(intent=None, staged_draft_sha256=None,
                                               staged_review_sha256=None))
    assert d["outcome"] == DRIVER.TXN_PROCEED
    assert d["publish"] == [] and d["cleanup"] is False


@pytest.mark.parametrize("sd,sr", [(STAGED_DRAFT, None), (None, STAGED_REVIEW),
                                   (STAGED_DRAFT, STAGED_REVIEW)])
def test_staging_without_an_intent_is_aborted_prepare(sd, sr):
    """Reachable BY CONSTRUCTION: staging is written before the intent is made
    durable, so a crash between them leaves exactly this. An earlier version
    of the procedure had no row for it."""
    d = DRIVER.classify_txn_recovery(_observed(intent=None, staged_draft_sha256=sd,
                                               staged_review_sha256=sr))
    assert d["outcome"] == DRIVER.TXN_ABORTED_PREPARE
    assert d["publish"] == [] and d["cleanup"] is True


# ---------------------------------------------------------------------------
# Step 0b -- an intent that exists but cannot be trusted
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad", [
    [], "unreadable", 0, 3.5, True,
    {},
    {"txn_schema": 2, "txn_id": "x", "phase": "prepared"},
    {"txn_id": "x", "phase": "prepared"},
])
def test_a_non_mapping_or_unknown_schema_intent_refuses_without_cleanup(bad):
    """ABSENT and INVALID are different states. Treating a non-mapping intent
    as absence returns cleanup=True and DELETES the only recovery evidence;
    an operator can inspect what is left, but not what was removed."""
    d = DRIVER.classify_txn_recovery(_observed(intent=bad))
    assert d["outcome"] == DRIVER.TXN_INTENT_INVALID
    assert d["publish"] == [], "an untrusted intent must never publish"
    assert d["cleanup"] is False, "an untrusted intent must never be deleted"


@pytest.mark.parametrize("schema", [True, False, 1.0, "1", None, 0, 2])
def test_a_boolean_or_wrong_typed_schema_version_never_publishes(schema):
    """`True == 1` in Python, so a bare `!=` comparison accepts
    {"txn_schema": true} as schema 1 and, with matching hashes, authorises
    BOTH publications. The identical trap was already fixed for the counters
    and did not propagate here on its own. `1.0` and `"1"` are the same shape
    from the other direction: JSON can carry either."""
    bad = _intent()
    bad["txn_schema"] = schema
    d = DRIVER.classify_txn_recovery(_observed(intent=bad))
    assert d["outcome"] == DRIVER.TXN_INTENT_INVALID
    assert d["publish"] == [] and d["cleanup"] is False


@pytest.mark.parametrize("phase", ["future-phase", "PREPARED", "", None, 1, "aborted"])
def test_an_unknown_phase_never_reaches_a_publish_decision(phase):
    """The concrete hazard: a mapping whose hashes all match but whose phase
    this code does not understand previously returned publish=[review,draft].
    Publishing on an intent we cannot interpret is the one outcome no recovery
    path may risk."""
    d = DRIVER.classify_txn_recovery(_observed(intent=_intent(phase=phase)))
    assert d["outcome"] == DRIVER.TXN_INTENT_INVALID
    assert d["publish"] == []


@pytest.mark.parametrize("label", [None, "", "0", "01", "final ", "FINAL", "1.0",
                                   1, True, "None", "r1", [], {}])
def test_an_unusable_round_label_refuses_without_cleanup(label):
    """round_label DERIVES the staging paths. An unusable one makes gathering
    hash `<seg>.None.staged.*`, miss the real staging, and answer
    STAGING_LOST -- which licenses cleanup and deletes staging that was there
    all along. This file already closed that exact class once as the `rNone`
    defect."""
    bad = _intent()
    if label is None:
        del bad["round_label"]
    else:
        bad["round_label"] = label
    d = DRIVER.classify_txn_recovery(_observed(intent=bad))
    assert d["outcome"] == DRIVER.TXN_INTENT_INVALID
    assert d["publish"] == [] and d["cleanup"] is False


@pytest.mark.parametrize("slot", ["staged_draft_sha256", "staged_review_sha256",
                                  "canonical_draft_sha256", "canonical_review_sha256"])
def test_an_unreadable_artifact_refuses_without_cleanup(slot):
    """A transient permission/IO failure is the ABSENCE of an observation, not
    an observation of absence. Deciding STAGING_LOST or DIVERGED from a missing
    premise deletes evidence over a blip."""
    d = DRIVER.classify_txn_recovery(_observed(**{slot: DRIVER.TXN_UNREADABLE}))
    assert d["outcome"] == DRIVER.TXN_UNOBSERVABLE
    assert d["publish"] == [] and d["cleanup"] is False and d["commit_intent"] is False


@pytest.mark.parametrize("preimage", [
    {}, {"absent": False}, {"absent": "yes"}, {"absent": 1},
    {"sha256": True}, {"sha256": 1}, {"sha256": ""}, {"sha256": None},
    {"absent": True, "sha256": "x"},          # both tags at once
    {"unknown": "tag"}, [], "absent", None, True,
])
def test_a_malformed_review_preimage_is_INVALID_not_DIVERGED(preimage):
    """The distinction that matters: an unrecognised nested shape must refuse
    and KEEP everything, never refuse and DELETE. Divergence licenses cleanup,
    so classifying a shape we do not understand as divergence destroys the only
    durable recovery evidence for it."""
    bad = _intent()
    bad["review_preimage"] = preimage
    d = DRIVER.classify_txn_recovery(_observed(intent=bad))
    assert d["outcome"] == DRIVER.TXN_INTENT_INVALID, (
        "a shape the schema does not recognise must not be reported as divergence"
    )
    assert d["publish"] == []
    assert d["cleanup"] is False, "an unrecognised intent must never be deleted"


@pytest.mark.parametrize("field", ["pre_edit_draft_sha1", "pre_edit_draft_token",
                                  "staged_draft_sha256", "staged_review_sha256"])
@pytest.mark.parametrize("value", [True, 1, None, "", [], {}])
def test_a_wrong_typed_comparison_field_refuses(field, value):
    """These are matched against values derived from real files, so a non-string
    is the same class as the boolean schema version: it cannot legitimately
    equal a hash or a token. Refuse at the boundary rather than relying on a
    comparison to happen to fail."""
    bad = _intent()
    bad[field] = value
    d = DRIVER.classify_txn_recovery(_observed(intent=bad))
    assert d["outcome"] == DRIVER.TXN_INTENT_INVALID
    assert d["publish"] == [] and d["cleanup"] is False


@pytest.mark.parametrize("missing", ["txn_id", "pre_edit_draft_sha1", "pre_edit_draft_token",
                                     "staged_draft_sha256", "staged_review_sha256",
                                     "review_preimage"])
def test_a_missing_required_field_refuses(missing):
    bad = _intent()
    del bad[missing]
    d = DRIVER.classify_txn_recovery(_observed(intent=bad))
    assert d["outcome"] == DRIVER.TXN_INTENT_INVALID
    assert d["publish"] == [] and d["cleanup"] is False


def test_staging_with_an_INVALID_intent_is_not_aborted_prepare(_=None):
    """aborted-prepare means "no intent was ever made durable" and licenses
    deleting staging. An intent that EXISTS but is unreadable is a different
    story and must not license that deletion."""
    d = DRIVER.classify_txn_recovery(_observed(intent="unreadable"))
    assert d["outcome"] != DRIVER.TXN_ABORTED_PREPARE
    assert d["cleanup"] is False


# ---------------------------------------------------------------------------
# Step 1 -- committed
# ---------------------------------------------------------------------------


def test_committed_intent_is_cleanup_only():
    """Idempotence: a crash after marking committed but before cleanup must
    not republish anything."""
    d = DRIVER.classify_txn_recovery(_observed(intent=_intent(phase="committed")))
    assert d["outcome"] == DRIVER.TXN_COMMITTED_CLEANED
    assert d["publish"] == [] and d["cleanup"] is True


# ---------------------------------------------------------------------------
# Step 2 -- both renamed, intent still prepared
# ---------------------------------------------------------------------------


def test_both_renamed_is_a_tail_commit_not_staging_loss():
    """Staging is legitimately GONE here because it was consumed. Reading that
    as staging-loss would refuse a transaction that in fact succeeded -- the
    concrete defect an earlier table had."""
    d = DRIVER.classify_txn_recovery(_observed(
        canonical_draft_sha256=STAGED_DRAFT, canonical_review_sha256=STAGED_REVIEW,
        staged_draft_sha256=None, staged_review_sha256=None,
        canonical_draft_content_sha1="post-edit-content", canonical_draft_token=PRE_TOKEN,
    ))
    assert d["outcome"] == DRIVER.TXN_ROLLED_FORWARD_TAIL
    assert d["publish"] == [] and d["commit_intent"] is True and d["cleanup"] is True


# ---------------------------------------------------------------------------
# Step 3 -- review renamed, draft still to go
# ---------------------------------------------------------------------------


def test_review_renamed_rolls_the_draft_forward():
    d = DRIVER.classify_txn_recovery(_observed(canonical_review_sha256=STAGED_REVIEW))
    assert d["outcome"] == DRIVER.TXN_ROLL_FORWARD_DRAFT
    assert d["publish"] == ["draft"]
    assert d["commit_intent"] is True


def test_review_renamed_but_draft_staging_lost_is_staging_loss_not_divergence():
    """The ordering subtlety this procedure exists for: the review differs
    from its preimage because THIS transaction renamed it. Without the
    `review_is_postimage` disjunct in step 5, that self-produced difference
    is reported as somebody else's divergence."""
    d = DRIVER.classify_txn_recovery(_observed(canonical_review_sha256=STAGED_REVIEW,
                                               staged_draft_sha256=None))
    assert d["outcome"] == DRIVER.TXN_STAGING_LOST
    assert d["publish"] == []


# ---------------------------------------------------------------------------
# Step 4 -- nothing renamed yet
# ---------------------------------------------------------------------------


def test_nothing_renamed_publishes_review_first_then_draft():
    """Review first is not stylistic. review_ready.py compares a candidate
    review against the CURRENT canonical draft, so old-draft + new-review is
    SHA-consistent and new-draft + old-review is not."""
    d = DRIVER.classify_txn_recovery(_observed())
    assert d["outcome"] == DRIVER.TXN_ROLL_FORWARD_BOTH
    assert d["publish"] == ["review", "draft"], "review must be renamed before the draft"


def test_absent_review_preimage_is_honoured():
    d = DRIVER.classify_txn_recovery(_observed(
        intent=_intent(review_preimage={"absent": True}), canonical_review_sha256=None))
    assert d["outcome"] == DRIVER.TXN_ROLL_FORWARD_BOTH


def test_absent_preimage_does_not_match_a_present_review():
    d = DRIVER.classify_txn_recovery(_observed(
        intent=_intent(review_preimage={"absent": True}),
        canonical_review_sha256="something-else-entirely"))
    assert d["outcome"] == DRIVER.TXN_PREIMAGE_DIVERGED


# ---------------------------------------------------------------------------
# Step 5 -- CAS refusal, and its ordering above step 6
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("field,value", [
    ("canonical_draft_content_sha1", "somebody-elses-content"),
    ("canonical_draft_token", "RUN:seg01-but-a-different-token"),
    ("canonical_draft_token", None),
])
def test_a_diverged_draft_preimage_refuses(field, value):
    """`pre_edit_draft_sha1` deliberately EXCLUDES dispatch_token, so a
    concurrent writer can leave content identical and the token different.
    The CAS must bind both."""
    d = DRIVER.classify_txn_recovery(_observed(**{field: value}))
    assert d["outcome"] == DRIVER.TXN_PREIMAGE_DIVERGED
    assert d["publish"] == [], "a refusal must never publish"


def test_divergence_wins_over_missing_staging():
    """Step 5 above step 6, asserted directly: both conditions hold at once,
    and the answer must be the refusal. If staging-loss won, a later
    roll-forward could overwrite an unrelated newer draft."""
    d = DRIVER.classify_txn_recovery(_observed(
        canonical_draft_content_sha1="somebody-elses-content",
        staged_draft_sha256=None, staged_review_sha256=None))
    assert d["outcome"] == DRIVER.TXN_PREIMAGE_DIVERGED


def test_a_review_matching_neither_preimage_nor_postimage_refuses():
    d = DRIVER.classify_txn_recovery(_observed(canonical_review_sha256="a-third-thing"))
    assert d["outcome"] == DRIVER.TXN_PREIMAGE_DIVERGED


# ---------------------------------------------------------------------------
# Step 6 -- staging unusable
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("over", [
    {"staged_draft_sha256": None},
    {"staged_review_sha256": None},
    {"staged_draft_sha256": "hash-does-not-match"},
    {"staged_review_sha256": "hash-does-not-match"},
])
def test_unusable_staging_publishes_nothing(over):
    d = DRIVER.classify_txn_recovery(_observed(**over))
    assert d["outcome"] == DRIVER.TXN_STAGING_LOST
    assert d["publish"] == [] and d["cleanup"] is True


# ---------------------------------------------------------------------------
# Structural properties
# ---------------------------------------------------------------------------


_ALL_OUTCOMES = {
    DRIVER.TXN_PROCEED, DRIVER.TXN_ABORTED_PREPARE, DRIVER.TXN_COMMITTED_CLEANED,
    DRIVER.TXN_ROLLED_FORWARD_TAIL, DRIVER.TXN_ROLL_FORWARD_DRAFT,
    DRIVER.TXN_PREIMAGE_DIVERGED, DRIVER.TXN_STAGING_LOST,
    DRIVER.TXN_INTENT_INVALID, DRIVER.TXN_UNOBSERVABLE,
}


def test_totality_over_the_cross_product():
    """No combination may fall through without a decision. Enumerated rather
    than sampled: a state with no outcome is exactly the kind of hole a
    hand-written table hides."""
    values = {
        "staged_draft_sha256": [None, STAGED_DRAFT, "wrong"],
        "staged_review_sha256": [None, STAGED_REVIEW, "wrong"],
        "canonical_draft_sha256": [None, STAGED_DRAFT, "old"],
        "canonical_review_sha256": [None, STAGED_REVIEW, REVIEW_PRE, "third"],
        "canonical_draft_content_sha1": [PRE_SHA1, "other"],
        "canonical_draft_token": [PRE_TOKEN, "other", None],
    }
    # The INTENT shape is varied too. An earlier version of this sweep held it
    # fixed at a fully valid `prepared`/`committed` record, so it enumerated
    # 1296 states and still missed the entire invalid-intent boundary -- an
    # enumeration is only as total as its widest-varying axis, and "1296 cases"
    # read as exhaustive while one axis was pinned.
    _bool_schema = _intent()
    _bool_schema["txn_schema"] = True
    _bad_preimage = _intent()
    _bad_preimage["review_preimage"] = {"absent": False}
    intents = [None, _intent(phase="prepared"), _intent(phase="committed"),
               _intent(phase="future-phase"), [], "unreadable", {}, _bool_schema,
               _bad_preimage]
    seen = set()
    checked = 0
    import itertools
    keys = list(values)
    for combo in itertools.product(*(values[k] for k in keys)):
        for intent in intents:
            obs = _observed(intent=intent, **dict(zip(keys, combo)))
            d = DRIVER.classify_txn_recovery(obs)
            assert d["outcome"] in _ALL_OUTCOMES, f"undecided state: {obs}"
            assert isinstance(d["publish"], list)
            if d["outcome"] in (DRIVER.TXN_INTENT_INVALID,):
                assert d["publish"] == [] and d["cleanup"] is False
            seen.add(d["outcome"])
            checked += 1
    assert checked == len(intents) * 3 * 3 * 3 * 4 * 2 * 3, "the enumeration must actually run"
    assert {DRIVER.TXN_PREIMAGE_DIVERGED, DRIVER.TXN_STAGING_LOST,
            DRIVER.TXN_INTENT_INVALID, DRIVER.TXN_PROCEED} <= seen


def test_a_refusal_never_publishes():
    """The one invariant worth stating over the whole space: no outcome that
    means 'do not trust this transaction' may carry anything to publish."""
    refusals = {DRIVER.TXN_PREIMAGE_DIVERGED, DRIVER.TXN_STAGING_LOST,
                DRIVER.TXN_ABORTED_PREPARE}

    for cr in (None, STAGED_REVIEW, REVIEW_PRE, "third"):
        for sd in (None, STAGED_DRAFT, "wrong"):
            for content in (PRE_SHA1, "other"):
                d = DRIVER.classify_txn_recovery(_observed(
                    canonical_review_sha256=cr, staged_draft_sha256=sd,
                    canonical_draft_content_sha1=content))
                if d["outcome"] in refusals:
                    assert d["publish"] == [], f"{d['outcome']} must publish nothing"
