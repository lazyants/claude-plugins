"""#409 track B -- the read-only layer that gathers state for the classifier.

Two properties matter here and neither is about the returned values:

  READ-ONLY BY CONSTRUCTION -- gathering must never mutate anything. Asserted
  by snapshotting every path under the root (mtime, size, bytes) around the
  call, not by checking that some expected file failed to appear.

  ABSENT IS NOT UNREADABLE -- a missing intent yields None, which licenses the
  classifier to delete staging; an intent that EXISTS but cannot be decoded
  must yield a non-None sentinel so the classifier refuses WITHOUT deleting
  the only recovery evidence.
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


DRIVER = _load_module(DRIVER_SRC, "segment_dispatch_driver_txn_gather")

SEG = "seg01"
ROUND = 1


@pytest.fixture()
def root(tmp_path):
    txn = tmp_path / "runs" / "R" / "txn"
    segments = tmp_path / "segments"
    txn.mkdir(parents=True)
    segments.mkdir(parents=True)
    return {"txn": txn, "segments": segments, "base": tmp_path}


def _write_draft(segments: Path, token="RUN:seg01", blocks=None):
    payload = {"seg": SEG, "dispatch_token": token,
               "blocks": blocks if blocks is not None else {"b1": "text"}}
    (segments / f"{SEG}.draft.json").write_text(json.dumps(payload), encoding="utf-8")
    return payload


def _snapshot(base: Path):
    out = {}
    for p in sorted(base.rglob("*")):
        if p.is_file():
            st = p.stat()
            out[str(p)] = (st.st_mtime_ns, st.st_size, hashlib.sha256(p.read_bytes()).hexdigest())
    return out


# ---------------------------------------------------------------------------
# Read-only
# ---------------------------------------------------------------------------


def test_gathering_mutates_nothing(root):
    """Snapshot every file under the root -- mtime, size AND bytes -- around
    the call. Checking that "no new file appeared" would miss an in-place
    rewrite, which is the mutation that would actually matter here."""
    _write_draft(root["segments"])
    (root["segments"] / f"{SEG}.review.json").write_text('{"clean": true}', encoding="utf-8")
    (root["txn"] / f"{SEG}.intent.json").write_text(json.dumps({"txn_schema": 1, "round_label": "1"}), encoding="utf-8")
    (root["txn"] / f"{SEG}.{ROUND}.staged.draft.json").write_text("{}", encoding="utf-8")

    before = _snapshot(root["base"])
    DRIVER.gather_txn_observed(SEG, root["txn"], root["segments"], SCRIPTS_SRC_DIR, ROUND)
    after = _snapshot(root["base"])

    assert before == after, "gathering must not create, delete, touch or rewrite anything"


def test_gathering_creates_nothing_when_the_root_is_empty(root):
    before = _snapshot(root["base"])
    obs = DRIVER.gather_txn_observed(SEG, root["txn"], root["segments"], SCRIPTS_SRC_DIR, ROUND)
    assert _snapshot(root["base"]) == before
    assert obs["intent"] is None


# ---------------------------------------------------------------------------
# Absent vs unreadable -- the distinction the classifier depends on
# ---------------------------------------------------------------------------


def test_a_missing_intent_is_None(root):
    obs = DRIVER.gather_txn_observed(SEG, root["txn"], root["segments"], SCRIPTS_SRC_DIR, ROUND)
    assert obs["intent"] is None


@pytest.mark.parametrize("garbage", ["}{", "not json", ""])
def test_an_unreadable_intent_is_NOT_None(root, garbage):
    """None means "absent", and absent licenses deleting staging. An intent
    that exists but cannot be decoded must not borrow that licence."""
    (root["txn"] / f"{SEG}.intent.json").write_text(garbage, encoding="utf-8")
    obs = DRIVER.gather_txn_observed(SEG, root["txn"], root["segments"], SCRIPTS_SRC_DIR, ROUND)
    assert obs["intent"] is not None
    d = DRIVER.classify_txn_recovery(obs)
    assert d["outcome"] == DRIVER.TXN_INTENT_INVALID
    assert d["cleanup"] is False, "an unreadable intent must never authorise deletion"


def test_an_unreadable_intent_with_staging_is_not_aborted_prepare(root):
    """End-to-end through the classifier: the state that would destroy
    evidence if `absent` and `unreadable` were conflated."""
    (root["txn"] / f"{SEG}.intent.json").write_text("}{", encoding="utf-8")
    (root["txn"] / f"{SEG}.{ROUND}.staged.draft.json").write_text("{}", encoding="utf-8")
    obs = DRIVER.gather_txn_observed(SEG, root["txn"], root["segments"], SCRIPTS_SRC_DIR, ROUND)
    d = DRIVER.classify_txn_recovery(obs)
    assert d["outcome"] != DRIVER.TXN_ABORTED_PREPARE
    assert d["cleanup"] is False


# ---------------------------------------------------------------------------
# Hashes
# ---------------------------------------------------------------------------


def test_hashes_are_of_raw_bytes(root):
    body = '{"b": 1, "a": 2}'
    (root["segments"] / f"{SEG}.review.json").write_text(body, encoding="utf-8")
    obs = DRIVER.gather_txn_observed(SEG, root["txn"], root["segments"], SCRIPTS_SRC_DIR, ROUND)
    assert obs["canonical_review_sha256"] == hashlib.sha256(body.encode()).hexdigest()


def test_reformatting_changes_the_review_hash(root):
    """Deliberately order- and format-sensitive: a competing rewrite, even a
    purely cosmetic one, must invalidate a stale transaction exactly like any
    other concurrent publication."""
    p = root["segments"] / f"{SEG}.review.json"
    p.write_text('{"a": 1, "b": 2}', encoding="utf-8")
    first = DRIVER.gather_txn_observed(SEG, root["txn"], root["segments"], SCRIPTS_SRC_DIR, ROUND)
    p.write_text('{"b": 2, "a": 1}', encoding="utf-8")  # same JSON, different bytes
    second = DRIVER.gather_txn_observed(SEG, root["txn"], root["segments"], SCRIPTS_SRC_DIR, ROUND)
    assert first["canonical_review_sha256"] != second["canonical_review_sha256"]


def test_absent_files_hash_to_None(root):
    obs = DRIVER.gather_txn_observed(SEG, root["txn"], root["segments"], SCRIPTS_SRC_DIR, ROUND)
    for key in ("staged_draft_sha256", "staged_review_sha256",
                "canonical_draft_sha256", "canonical_review_sha256"):
        assert obs[key] is None, key


# ---------------------------------------------------------------------------
# The draft's content sha1 comes from the module that OWNS that operation
# ---------------------------------------------------------------------------


def test_content_sha1_matches_the_authoritative_implementation(root):
    """Not "some sha1 was produced" -- the SAME value draft_sha1.py's own
    draft_content_sha1() produces. Reimplementing it here would be the exact
    defect the driver's own comment forbids."""
    _write_draft(root["segments"])
    authority = _load_module(SCRIPTS_SRC_DIR / "draft_sha1.py", "draft_sha1_authority")
    expected = authority.draft_content_sha1(root["segments"] / f"{SEG}.draft.json")

    obs = DRIVER.gather_txn_observed(SEG, root["txn"], root["segments"], SCRIPTS_SRC_DIR, ROUND)
    assert obs["canonical_draft_content_sha1"] == expected


def test_content_sha1_ignores_the_dispatch_token(root):
    """pre_edit_draft_sha1 deliberately EXCLUDES the token, which is why the
    CAS has to bind the token separately. Pinned here so a change to that
    property is visible at this seam too."""
    _write_draft(root["segments"], token="RUN:seg01")
    a = DRIVER.gather_txn_observed(SEG, root["txn"], root["segments"], SCRIPTS_SRC_DIR, ROUND)
    _write_draft(root["segments"], token="A-COMPLETELY-DIFFERENT-TOKEN")
    b = DRIVER.gather_txn_observed(SEG, root["txn"], root["segments"], SCRIPTS_SRC_DIR, ROUND)

    assert a["canonical_draft_content_sha1"] == b["canonical_draft_content_sha1"]
    assert a["canonical_draft_token"] != b["canonical_draft_token"], (
        "the token must still be reported separately, or the CAS cannot bind it"
    )


def test_content_sha1_changes_with_content(root):
    _write_draft(root["segments"], blocks={"b1": "one"})
    a = DRIVER.gather_txn_observed(SEG, root["txn"], root["segments"], SCRIPTS_SRC_DIR, ROUND)
    _write_draft(root["segments"], blocks={"b1": "two"})
    b = DRIVER.gather_txn_observed(SEG, root["txn"], root["segments"], SCRIPTS_SRC_DIR, ROUND)
    assert a["canonical_draft_content_sha1"] != b["canonical_draft_content_sha1"]


@pytest.mark.parametrize("body", ["}{", "[]", "null", '"a string"'])
def test_a_draft_the_AUTHORITY_rejects_yields_None_rather_than_raising(root, body):
    """None cannot match any recorded preimage, so the classifier refuses.
    This layer must not decide anything, and must not crash the batch.

    Which drafts are rejected is draft_sha1.py's call, not this test's: it
    raises on any non-object and accepts an object. The cases here are exactly
    the ones the authority refuses -- verified against it in the sibling test
    below rather than assumed."""
    (root["segments"] / f"{SEG}.draft.json").write_text(body, encoding="utf-8")
    obs = DRIVER.gather_txn_observed(SEG, root["txn"], root["segments"], SCRIPTS_SRC_DIR, ROUND)
    assert obs["canonical_draft_content_sha1"] is None


def test_this_layer_defers_to_the_authority_on_what_counts_as_a_draft(root):
    """The delegation itself, pinned. A blockless object is ACCEPTED by
    draft_sha1.py, so this layer must report its hash rather than substitute
    its own idea of validity -- the first version of this test asserted None
    here, encoding my expectation instead of the authority's contract."""
    authority = _load_module(SCRIPTS_SRC_DIR / "draft_sha1.py", "draft_sha1_contract")
    draft = root["segments"] / f"{SEG}.draft.json"
    draft.write_text('{"seg": "seg01"}', encoding="utf-8")

    expected = authority.draft_content_sha1(draft)
    obs = DRIVER.gather_txn_observed(SEG, root["txn"], root["segments"], SCRIPTS_SRC_DIR, ROUND)

    assert obs["canonical_draft_content_sha1"] == expected
    assert expected is not None


@pytest.mark.parametrize("token", [None, 1, True, [], {}])
def test_a_non_string_dispatch_token_is_reported_as_None(root, token):
    payload = {"seg": SEG, "blocks": {"b1": "t"}}
    if token is not None:
        payload["dispatch_token"] = token
    (root["segments"] / f"{SEG}.draft.json").write_text(json.dumps(payload), encoding="utf-8")
    obs = DRIVER.gather_txn_observed(SEG, root["txn"], root["segments"], SCRIPTS_SRC_DIR, ROUND)
    assert obs["canonical_draft_token"] is None


# ---------------------------------------------------------------------------
# The round label used to find staging
# ---------------------------------------------------------------------------


def test_the_round_label_comes_from_the_intent_when_not_supplied(root):
    """A replacement driver recovering a transaction it did not start does not
    know the round; it must read it from the intent, exactly as it reads the
    txn_id from there."""
    (root["txn"] / f"{SEG}.intent.json").write_text(
        json.dumps({"txn_schema": 1, "round_label": "7"}), encoding="utf-8")
    staged = root["txn"] / f"{SEG}.7.staged.draft.json"
    staged.write_text("{}", encoding="utf-8")

    obs = DRIVER.gather_txn_observed(SEG, root["txn"], root["segments"], SCRIPTS_SRC_DIR)
    assert obs["staged_draft_sha256"] == hashlib.sha256(b"{}").hexdigest()


# ---------------------------------------------------------------------------
# A read failure is not an absence
# ---------------------------------------------------------------------------


def test_an_unreadable_artifact_is_not_reported_as_absent(root, monkeypatch):
    """Both would be None otherwise, and None means absent -- which the
    classifier may act on destructively. A present-but-unreadable file must be
    distinguishable."""
    module = _load_module(DRIVER_SRC, "driver_gather_readfail")
    target = root["txn"] / f"{SEG}.{ROUND}.staged.draft.json"
    target.write_text("{}", encoding="utf-8")
    real_read = Path.read_bytes

    def fake_read(self):
        if self == target:
            raise OSError("simulated read failure")
        return real_read(self)

    monkeypatch.setattr(Path, "read_bytes", fake_read)
    obs = module.gather_txn_observed(SEG, root["txn"], root["segments"], SCRIPTS_SRC_DIR, ROUND)

    assert obs["staged_draft_sha256"] == module.TXN_UNREADABLE
    d = module.classify_txn_recovery(obs)
    assert d["outcome"] == module.TXN_UNOBSERVABLE
    assert d["cleanup"] is False, "a read failure must never authorise deletion"


# ---------------------------------------------------------------------------
# One snapshot, and one authority on the round
# ---------------------------------------------------------------------------


def test_a_draft_edited_mid_observation_never_yields_a_composite(root, monkeypatch):
    """The CAS's whole job is to refuse publishing over a concurrent edit. All
    three draft-derived fields feed it, so reading the file more than once lets
    a rewrite between reads synthesise an observation that never existed --
    old content hash beside newer bytes -- and the classifier would then
    authorise publication over exactly that edit."""
    module = _load_module(DRIVER_SRC, "driver_gather_toctou")
    _write_draft(root["segments"], blocks={"b1": "ORIGINAL"})
    draft = root["segments"] / f"{SEG}.draft.json"
    real_read = Path.read_bytes
    state = {"n": 0}

    def racing_read(self):
        if self == draft:
            state["n"] += 1
            if state["n"] == 1:
                data = real_read(self)
                # a competitor rewrites the draft immediately after the first read
                _write_draft(root["segments"], blocks={"b1": "NEWER-CONTENT"})
                return data
        return real_read(self)

    monkeypatch.setattr(Path, "read_bytes", racing_read)
    obs = module.gather_txn_observed(SEG, root["txn"], root["segments"], SCRIPTS_SRC_DIR, "1")

    assert obs["canonical_draft_sha256"] == module.TXN_UNREADABLE
    d = module.classify_txn_recovery(obs)
    assert d["publish"] == [], "a composite observation must never authorise publication"
    assert d["cleanup"] is False


def _valid_intent(round_label="7", **over):
    """A FULLY VALID intent. The first version of the label-authority test
    below used a stub carrying only txn_schema and round_label -- so it was
    rejected for MISSING FIELDS and passed no matter what the label logic did.
    It survived the mutant that removes the fix, which is how the vacuity
    surfaced."""
    intent = {
        "txn_schema": 1, "txn_id": "RUN:seg01:7:1", "phase": "prepared",
        "round_label": round_label,
        "pre_edit_draft_sha1": "pre", "pre_edit_draft_token": "RUN:seg01",
        "staged_draft_sha256": "sd", "staged_review_sha256": "sr",
        "review_preimage": {"absent": True},
    }
    intent.update(over)
    return intent


def test_the_durable_intent_owns_the_round_label(root):
    """A caller label that disagrees with the durable intent would hash one
    round's staging while validation blesses another's -- real staging then
    reads as missing and STAGING_LOST licenses deleting it."""
    (root["txn"] / f"{SEG}.intent.json").write_text(
        json.dumps(_valid_intent("7")), encoding="utf-8")
    (root["txn"] / f"{SEG}.7.staged.draft.json").write_text("{}", encoding="utf-8")
    (root["txn"] / f"{SEG}.7.staged.review.json").write_text("{}", encoding="utf-8")

    obs = DRIVER.gather_txn_observed(SEG, root["txn"], root["segments"], SCRIPTS_SRC_DIR, "1")
    d = DRIVER.classify_txn_recovery(obs)

    assert d["outcome"] != DRIVER.TXN_STAGING_LOST, (
        "the round-7 staging is present and correct; answering staging-lost would delete it"
    )
    assert d["cleanup"] is False, "valid staging must not be deleted over a caller/intent mismatch"


def test_an_agreeing_caller_label_reaches_the_right_staging(root):
    (root["txn"] / f"{SEG}.intent.json").write_text(
        json.dumps(_valid_intent("7")), encoding="utf-8")
    (root["txn"] / f"{SEG}.7.staged.draft.json").write_text("{}", encoding="utf-8")
    obs = DRIVER.gather_txn_observed(SEG, root["txn"], root["segments"], SCRIPTS_SRC_DIR, "7")
    assert obs["staged_draft_sha256"] == hashlib.sha256(b"{}").hexdigest()


def test_no_caller_label_uses_the_durable_one(root):
    (root["txn"] / f"{SEG}.intent.json").write_text(
        json.dumps(_valid_intent("7")), encoding="utf-8")
    (root["txn"] / f"{SEG}.7.staged.draft.json").write_text("{}", encoding="utf-8")
    obs = DRIVER.gather_txn_observed(SEG, root["txn"], root["segments"], SCRIPTS_SRC_DIR)
    assert obs["staged_draft_sha256"] == hashlib.sha256(b"{}").hexdigest()
