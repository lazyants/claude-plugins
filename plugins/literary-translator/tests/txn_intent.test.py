"""#409 track B -- the intent lifecycle: write, commit, clean up.

Everything here writes ONLY inside the transaction directory. The renames
that publish into `segments/` are deliberately not part of this layer, and the
central test below asserts that boundary rather than assuming it: no function
here may touch a canonical draft or review.

Round-trip with the classifier is asserted too. An intent that this module
writes must be one the recovery procedure can interpret -- writing a record
its own reader would reject is strictly worse than not starting, because an
invalid intent is deliberately never cleaned up and would block the segment
until a human removed it.
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


DRIVER = _load_module(DRIVER_SRC, "segment_dispatch_driver_txn_intent")

SEG = "seg01"
ROUND = "1"


@pytest.fixture()
def dirs(tmp_path):
    txn = tmp_path / "runs" / "R" / "txn"
    segments = tmp_path / "segments"
    txn.mkdir(parents=True)
    segments.mkdir(parents=True)
    return {"txn": txn, "segments": segments, "base": tmp_path}


def _intent(**over):
    base = {
        "txn_schema": 1, "txn_id": "RUN:seg01:1:1", "phase": "prepared",
        "round_label": ROUND,
        "pre_edit_draft_sha1": "pre-sha1", "pre_edit_draft_token": "RUN:seg01",
        "staged_draft_sha256": "sd", "staged_review_sha256": "sr",
        "review_preimage": {"absent": True},
    }
    base.update(over)
    return base


def _snapshot(base: Path):
    out = {}
    for p in sorted(base.rglob("*")):
        if p.is_file():
            out[str(p)] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


# ---------------------------------------------------------------------------
# The boundary: nothing here may touch canonical artifacts
# ---------------------------------------------------------------------------


def test_no_lifecycle_operation_touches_a_canonical_artifact(dirs):
    """The renames that publish into segments/ are a DIFFERENT layer. Asserted
    over the whole lifecycle rather than per function, because the property
    that matters is that none of them, in any order, reaches user work."""
    draft = dirs["segments"] / f"{SEG}.draft.json"
    review = dirs["segments"] / f"{SEG}.review.json"
    draft.write_text('{"seg": "seg01", "blocks": {"b": "t"}}', encoding="utf-8")
    review.write_text('{"clean": true}', encoding="utf-8")
    before = _snapshot(dirs["segments"])

    DRIVER.write_txn_intent(dirs["txn"], SEG, _intent())
    DRIVER.commit_txn_intent(dirs["txn"], SEG)
    DRIVER.cleanup_txn(dirs["txn"], SEG, ROUND)

    assert _snapshot(dirs["segments"]) == before, (
        "the intent lifecycle must never touch a canonical draft or review"
    )


# ---------------------------------------------------------------------------
# write
# ---------------------------------------------------------------------------


def test_a_written_intent_round_trips_through_the_recovery_reader(dirs):
    """The round trip is the contract: what this writes, recovery must be able
    to interpret."""
    assert DRIVER.write_txn_intent(dirs["txn"], SEG, _intent()) is True
    obs = DRIVER.gather_txn_observed(SEG, dirs["txn"], dirs["segments"], SCRIPTS_SRC_DIR)
    assert isinstance(obs["intent"], dict)
    d = DRIVER.classify_txn_recovery(obs)
    assert d["outcome"] != DRIVER.TXN_INTENT_INVALID


@pytest.mark.parametrize("over", [
    {"txn_schema": 2}, {"txn_schema": True}, {"round_label": None},
    {"round_label": "r1"}, {"txn_id": ""}, {"review_preimage": {}},
    {"pre_edit_draft_sha1": 1},
])
def test_writing_an_intent_its_own_reader_would_reject_is_refused(dirs, over):
    """Strictly worse than not starting: recovery would find a record it must
    classify as invalid, and an invalid intent is deliberately never cleaned
    up, so it would block the segment until a human removed it."""
    bad = _intent(**over)
    assert DRIVER.write_txn_intent(dirs["txn"], SEG, bad) is False
    assert not DRIVER.txn_intent_path(dirs["txn"], SEG).exists(), (
        "a refused intent must leave nothing behind"
    )


@pytest.mark.parametrize("phase", ["committed", "prepared "])
def test_an_intent_is_always_published_as_prepared(dirs, phase):
    assert DRIVER.write_txn_intent(dirs["txn"], SEG, _intent(phase=phase)) is False
    assert not DRIVER.txn_intent_path(dirs["txn"], SEG).exists()


def test_the_intent_path_is_one_per_segment(dirs):
    """Discovery must be deterministic: a replacement driver finds the intent
    by path and READS its txn_id, which is why the id need not be derivable."""
    a = DRIVER.txn_intent_path(dirs["txn"], "seg01")
    b = DRIVER.txn_intent_path(dirs["txn"], "seg02")
    assert a != b and a.parent == dirs["txn"]
    DRIVER.write_txn_intent(dirs["txn"], "seg01", _intent())
    DRIVER.write_txn_intent(dirs["txn"], "seg02", _intent(txn_id="RUN:seg02:1:1"))
    assert json.loads(a.read_text())["txn_id"] == "RUN:seg01:1:1"
    assert json.loads(b.read_text())["txn_id"] == "RUN:seg02:1:1"


# ---------------------------------------------------------------------------
# commit
# ---------------------------------------------------------------------------


def test_commit_flips_the_phase_on_disk(dirs):
    DRIVER.write_txn_intent(dirs["txn"], SEG, _intent())
    assert DRIVER.commit_txn_intent(dirs["txn"], SEG) is True
    on_disk = json.loads(DRIVER.txn_intent_path(dirs["txn"], SEG).read_text())
    assert on_disk["phase"] == "committed"
    assert on_disk["txn_id"] == "RUN:seg01:1:1", "commit must preserve the rest of the record"


def test_commit_is_idempotent(dirs):
    """Recovery replays this, so a second commit is a no-op success rather
    than a failure."""
    DRIVER.write_txn_intent(dirs["txn"], SEG, _intent())
    assert DRIVER.commit_txn_intent(dirs["txn"], SEG) is True
    assert DRIVER.commit_txn_intent(dirs["txn"], SEG) is True


def test_commit_reads_the_DISK_not_the_callers_copy(dirs):
    """The phase flip describes the state the disk is in, so a caller's stale
    mapping must not be able to smuggle different fields into the record."""
    DRIVER.write_txn_intent(dirs["txn"], SEG, _intent())
    path = DRIVER.txn_intent_path(dirs["txn"], SEG)
    current = json.loads(path.read_text())
    current["pre_edit_draft_sha1"] = "rewritten-out-of-band"
    path.write_text(json.dumps(current), encoding="utf-8")

    DRIVER.commit_txn_intent(dirs["txn"], SEG)

    assert json.loads(path.read_text())["pre_edit_draft_sha1"] == "rewritten-out-of-band"


def test_commit_refuses_an_absent_intent(dirs):
    assert DRIVER.commit_txn_intent(dirs["txn"], SEG) is False


@pytest.mark.parametrize("body", ["}{", "[]", "null", '{"txn_schema": 1}'])
def test_commit_refuses_an_uninterpretable_intent(dirs, body):
    DRIVER.txn_intent_path(dirs["txn"], SEG).write_text(body, encoding="utf-8")
    assert DRIVER.commit_txn_intent(dirs["txn"], SEG) is False
    assert DRIVER.txn_intent_path(dirs["txn"], SEG).read_text() == body, (
        "refusing must not rewrite what it could not interpret"
    )


# ---------------------------------------------------------------------------
# cleanup
# ---------------------------------------------------------------------------


def test_cleanup_removes_staging_and_intent_only(dirs):
    paths = DRIVER.staged_paths(dirs["txn"], SEG, ROUND)
    paths["draft"].write_text("{}", encoding="utf-8")
    paths["review"].write_text("{}", encoding="utf-8")
    DRIVER.write_txn_intent(dirs["txn"], SEG, _intent())
    other = dirs["txn"] / "seg02.intent.json"
    other.write_text("{}", encoding="utf-8")

    assert DRIVER.cleanup_txn(dirs["txn"], SEG, ROUND) is True

    assert not paths["draft"].exists() and not paths["review"].exists()
    assert not DRIVER.txn_intent_path(dirs["txn"], SEG).exists()
    assert other.exists(), "cleanup must be scoped to its own segment"


def test_cleanup_is_idempotent_on_an_empty_directory(dirs):
    assert DRIVER.cleanup_txn(dirs["txn"], SEG, ROUND) is True


def test_cleanup_removes_the_intent_LAST(dirs, monkeypatch):
    """Observe the ACTUAL removal order, by recording the calls. An earlier
    version of this test unlinked the staging by hand and then asked the
    classifier what it saw -- which asserts a property of the classifier and
    exercises cleanup's ordering not at all. It passed with the order
    reversed, which is how the vacuity surfaced."""
    module = _load_module(DRIVER_SRC, "driver_intent_order")
    paths = module.staged_paths(dirs["txn"], SEG, ROUND)
    paths["draft"].write_text("{}", encoding="utf-8")
    paths["review"].write_text("{}", encoding="utf-8")
    module.write_txn_intent(dirs["txn"], SEG, _intent())

    order = []
    real_unlink = Path.unlink

    def recording_unlink(self, *a, **kw):
        order.append(self.name)
        return real_unlink(self, *a, **kw)

    monkeypatch.setattr(Path, "unlink", recording_unlink)
    module.cleanup_txn(dirs["txn"], SEG, ROUND)

    assert order, "cleanup must actually remove something"
    assert order[-1] == f"{SEG}.intent.json", (
        f"the intent must be removed LAST, got order {order}"
    )


def test_a_crash_between_the_two_removals_leaves_a_state_recovery_recognises(dirs):
    """The consequence of that ordering: staging gone with the intent still
    present is a state the procedure has a step for."""
    paths = DRIVER.staged_paths(dirs["txn"], SEG, ROUND)
    paths["draft"].write_text("{}", encoding="utf-8")
    paths["review"].write_text("{}", encoding="utf-8")
    DRIVER.write_txn_intent(dirs["txn"], SEG, _intent())
    DRIVER.commit_txn_intent(dirs["txn"], SEG)

    paths["draft"].unlink()
    paths["review"].unlink()

    obs = DRIVER.gather_txn_observed(SEG, dirs["txn"], dirs["segments"], SCRIPTS_SRC_DIR)
    d = DRIVER.classify_txn_recovery(obs)
    assert d["outcome"] == DRIVER.TXN_COMMITTED_CLEANED
    assert d["cleanup"] is True and d["publish"] == []


def test_full_lifecycle_ends_with_nothing_in_flight(dirs):
    paths = DRIVER.staged_paths(dirs["txn"], SEG, ROUND)
    paths["draft"].write_text("{}", encoding="utf-8")
    paths["review"].write_text("{}", encoding="utf-8")
    DRIVER.write_txn_intent(dirs["txn"], SEG, _intent())
    DRIVER.commit_txn_intent(dirs["txn"], SEG)
    DRIVER.cleanup_txn(dirs["txn"], SEG, ROUND)

    obs = DRIVER.gather_txn_observed(SEG, dirs["txn"], dirs["segments"], SCRIPTS_SRC_DIR, ROUND)
    assert DRIVER.classify_txn_recovery(obs)["outcome"] == DRIVER.TXN_PROCEED


@pytest.mark.parametrize("failing", ["draft", "review"])
def test_a_failed_staging_removal_preserves_the_intent(dirs, monkeypatch, failing):
    """LAST is not the same as ONLY-IF, and only the second gives the
    invariant. Deleting the intent when staging could not be removed leaves
    staging on disk with no durable record explaining it -- the orphan state
    the ordering exists to avoid, reached through the failure path rather than
    a crash."""
    module = _load_module(DRIVER_SRC, f"driver_cleanup_fail_{failing}")
    paths = module.staged_paths(dirs["txn"], SEG, ROUND)
    paths["draft"].write_text("{}", encoding="utf-8")
    paths["review"].write_text("{}", encoding="utf-8")
    module.write_txn_intent(dirs["txn"], SEG, _intent())
    target = paths[failing]
    real_unlink = Path.unlink

    def failing_unlink(self, *a, **kw):
        if self == target:
            raise PermissionError("simulated unlink failure")
        return real_unlink(self, *a, **kw)

    monkeypatch.setattr(Path, "unlink", failing_unlink)

    assert module.cleanup_txn(dirs["txn"], SEG, ROUND) is False
    assert module.txn_intent_path(dirs["txn"], SEG).exists(), (
        "the intent must survive so recovery can still explain the leftover staging"
    )
    assert target.exists()


def test_a_failed_staging_removal_still_leaves_a_classifiable_state(dirs, monkeypatch):
    """The consequence: what recovery sees afterwards must be a state the
    procedure has a step for, not orphan staging."""
    module = _load_module(DRIVER_SRC, "driver_cleanup_fail_classify")
    paths = module.staged_paths(dirs["txn"], SEG, ROUND)
    paths["draft"].write_text("{}", encoding="utf-8")
    module.write_txn_intent(dirs["txn"], SEG, _intent())
    real_unlink = Path.unlink

    def failing_unlink(self, *a, **kw):
        if self == paths["draft"]:
            raise PermissionError("simulated unlink failure")
        return real_unlink(self, *a, **kw)

    monkeypatch.setattr(Path, "unlink", failing_unlink)
    module.cleanup_txn(dirs["txn"], SEG, ROUND)
    monkeypatch.undo()

    obs = module.gather_txn_observed(SEG, dirs["txn"], dirs["segments"], SCRIPTS_SRC_DIR)
    d = module.classify_txn_recovery(obs)
    assert d["outcome"] != module.TXN_ABORTED_PREPARE, (
        "staging must never be left without the intent that explains it"
    )


# ---------------------------------------------------------------------------
# The durable intent owns the round for cleanup too
# ---------------------------------------------------------------------------


def test_cleanup_uses_the_durable_round_not_the_callers(dirs):
    """A replacement driver on a different round is exactly the case that
    produces the orphan state: with round-1 staging and a round-1 intent, a
    caller passing "2" removes nothing (absent files count as removed) and
    would then delete the intent, leaving the real staging behind."""
    paths = DRIVER.staged_paths(dirs["txn"], SEG, "1")
    paths["draft"].write_text("{}", encoding="utf-8")
    paths["review"].write_text("{}", encoding="utf-8")
    DRIVER.write_txn_intent(dirs["txn"], SEG, _intent(round_label="1"))

    DRIVER.cleanup_txn(dirs["txn"], SEG, "2")

    assert not paths["draft"].exists() and not paths["review"].exists(), (
        "cleanup must remove the staging the DURABLE intent names, not the caller's round"
    )
    assert not DRIVER.txn_intent_path(dirs["txn"], SEG).exists()


def test_cleanup_never_orphans_staging_via_a_stale_caller_round(dirs):
    """The consequence, stated as the invariant: staging must never outlive
    the intent that explains it."""
    paths = DRIVER.staged_paths(dirs["txn"], SEG, "1")
    paths["draft"].write_text("{}", encoding="utf-8")
    DRIVER.write_txn_intent(dirs["txn"], SEG, _intent(round_label="1"))

    DRIVER.cleanup_txn(dirs["txn"], SEG, "2")

    intent_gone = not DRIVER.txn_intent_path(dirs["txn"], SEG).exists()
    staging_left = paths["draft"].exists()
    assert not (intent_gone and staging_left), "staging must never outlive its intent"


@pytest.mark.parametrize("body", ["}{", "[]", '{"txn_schema": 1}'])
def test_cleanup_refuses_when_the_intent_cannot_be_interpreted(dirs, body):
    """Its staging cannot be identified, so deleting anything would be
    guesswork -- and an uninterpretable intent is deliberately never removed."""
    paths = DRIVER.staged_paths(dirs["txn"], SEG, ROUND)
    paths["draft"].write_text("{}", encoding="utf-8")
    DRIVER.txn_intent_path(dirs["txn"], SEG).write_text(body, encoding="utf-8")

    assert DRIVER.cleanup_txn(dirs["txn"], SEG, ROUND) is False
    assert DRIVER.txn_intent_path(dirs["txn"], SEG).exists()
    assert paths["draft"].exists()


def test_cleanup_uses_the_caller_round_when_there_is_no_intent(dirs):
    """The aborted-prepare case: staging exists, no intent was ever made
    durable, so there is nothing to disagree with."""
    paths = DRIVER.staged_paths(dirs["txn"], SEG, ROUND)
    paths["draft"].write_text("{}", encoding="utf-8")
    assert DRIVER.cleanup_txn(dirs["txn"], SEG, ROUND) is True
    assert not paths["draft"].exists()


def test_an_already_committed_retry_reestablishes_durability(dirs, monkeypatch):
    """Same post-os.replace() hazard as the failure counter: a first commit can
    publish `committed` and still report failure because the directory fsync
    failed, so a retry must not return True on sight of the phase alone."""
    module = _load_module(DRIVER_SRC, "driver_commit_fsync")
    module.write_txn_intent(dirs["txn"], SEG, _intent())
    state = {"left": 1, "dir_fsyncs": 0}
    real_fsync = __import__("os").fsync
    import os as _os
    import stat as _stat

    def fake_fsync(fd):
        if _stat.S_ISDIR(_os.fstat(fd).st_mode):
            state["dir_fsyncs"] += 1
            if state["left"] > 0:
                state["left"] -= 1
                raise OSError("simulated directory fsync failure")
            return None
        return real_fsync(fd)

    monkeypatch.setattr(module.os, "fsync", fake_fsync)

    assert module.commit_txn_intent(dirs["txn"], SEG) is False
    assert json.loads(DRIVER.txn_intent_path(dirs["txn"], SEG).read_text())["phase"] == "committed"
    before = state["dir_fsyncs"]

    assert module.commit_txn_intent(dirs["txn"], SEG) is True
    assert state["dir_fsyncs"] > before, "the retry must re-issue the durability barrier"


def test_the_intent_survives_when_the_staging_removal_cannot_be_persisted(dirs, monkeypatch):
    """ONLY-IF is not DURABLY-BEFORE. Without a barrier between the two
    phases the ordering is process-visible but not crash-durable: a crash can
    preserve the intent deletion while losing the staging deletions, which is
    orphan staging again, reached through the durability layer."""
    module = _load_module(DRIVER_SRC, "driver_cleanup_barrier")
    paths = module.staged_paths(dirs["txn"], SEG, ROUND)
    paths["draft"].write_text("{}", encoding="utf-8")
    paths["review"].write_text("{}", encoding="utf-8")
    module.write_txn_intent(dirs["txn"], SEG, _intent())

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

    assert module.cleanup_txn(dirs["txn"], SEG, ROUND) is False
    assert module.txn_intent_path(dirs["txn"], SEG).exists(), (
        "the intent must remain until the staging removals are durable"
    )
