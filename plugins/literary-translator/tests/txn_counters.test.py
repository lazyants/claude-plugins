"""#409 track B -- the two durable per-segment transaction counters.

These are tested as a unit, apart from any dispatch, because their whole
value is a property that a dispatch-level test cannot observe: that a
failure is charged EXACTLY ONCE across a crash at any point, and that an
attempt id is never handed out twice to a replacement driver.

The identity/terminality split is load-bearing and is asserted here rather
than assumed: `<seg>.attempts` must stay unbounded (bounding it would refuse
legitimate projects whose `engine.max_fix_rounds` exceeds the ceiling on the
NORMAL path, since it advances on successful transactions too), while
`<seg>.txn_failures` is the only counter a ceiling may bound.
"""

import importlib.util
import json
import os
import stat
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


DRIVER = _load_module(DRIVER_SRC, "segment_dispatch_driver_txn_counters")

SEG = "seg01"
CEILING = 3


@pytest.fixture()
def txn_dir(tmp_path):
    d = tmp_path / "runs" / "20260803T000000Z" / "txn"
    d.mkdir(parents=True)
    return d


# ---------------------------------------------------------------------------
# attempt_seq -- identity only, unbounded
# ---------------------------------------------------------------------------


def test_attempt_seq_is_monotonic_and_starts_at_one(txn_dir):
    assert DRIVER.next_attempt_seq(txn_dir, SEG) == 1
    assert DRIVER.next_attempt_seq(txn_dir, SEG) == 2
    assert DRIVER.next_attempt_seq(txn_dir, SEG) == 3


def test_attempt_seq_survives_a_replacement_driver(txn_dir):
    """The allocation source is a FILE, not process memory: a driver that
    replaces a dead one must not reissue an id the dead one already used.
    Re-importing the module models the replacement -- if the counter lived in
    the intent (deleted on refusal and after commit) or in memory, this would
    hand out 1 again."""
    assert DRIVER.next_attempt_seq(txn_dir, SEG) == 1
    replacement = _load_module(DRIVER_SRC, "segment_dispatch_driver_txn_counters_replacement")
    assert replacement.next_attempt_seq(txn_dir, SEG) == 2


def test_attempt_seq_is_per_segment(txn_dir):
    assert DRIVER.next_attempt_seq(txn_dir, "seg01") == 1
    assert DRIVER.next_attempt_seq(txn_dir, "seg02") == 1
    assert DRIVER.next_attempt_seq(txn_dir, "seg01") == 2


def test_attempt_seq_is_not_bounded_by_the_failure_ceiling(txn_dir):
    """Regression guard for the defect the two-counter split exists to fix: a
    single ceiling hung on attempt_seq would refuse a legitimate project with
    max_fix_rounds >= ceiling on its NORMAL path, because attempts advance on
    SUCCESS too. Nothing here may cap it."""
    for expected in range(1, CEILING + 6):
        assert DRIVER.next_attempt_seq(txn_dir, SEG) == expected
    assert not DRIVER.txn_failures_exhausted(txn_dir, SEG, CEILING), (
        "attempts must never feed the failure ceiling"
    )


def test_attempt_seq_reports_failure_as_zero_when_it_cannot_be_made_durable(tmp_path):
    """An id that was not durably reserved may be handed out twice, so the
    caller needs to be able to tell. Zero is that signal."""
    missing = tmp_path / "no-such-dir"
    assert DRIVER.next_attempt_seq(missing, SEG) == 0


# ---------------------------------------------------------------------------
# txn_id
# ---------------------------------------------------------------------------


def test_txn_id_distinguishes_attempts_that_are_otherwise_identical():
    """Two attempts on the same run/segment/round differ ONLY by attempt_seq.
    An id derived from the staged output hashes instead would collide whenever
    two attempts produced identical bytes."""
    a = DRIVER.make_txn_id("RUN", SEG, 2, 1)
    b = DRIVER.make_txn_id("RUN", SEG, 2, 2)
    assert a != b
    assert a == "RUN:seg01:2:1"


# ---------------------------------------------------------------------------
# failure charging -- exactly once, across crashes
# ---------------------------------------------------------------------------


def test_charging_the_same_transaction_twice_charges_once(txn_dir):
    txn = DRIVER.make_txn_id("RUN", SEG, 1, 1)
    assert DRIVER.charge_txn_failure(txn_dir, SEG, txn, CEILING)["count"] == 1
    assert DRIVER.charge_txn_failure(txn_dir, SEG, txn, CEILING)["count"] == 1
    assert DRIVER.charge_txn_failure(txn_dir, SEG, txn, CEILING)["count"] == 1


def test_distinct_transactions_each_charge(txn_dir):
    for i in (1, 2, 3):
        DRIVER.charge_txn_failure(txn_dir, SEG, DRIVER.make_txn_id("RUN", SEG, 1, i), CEILING)
    assert DRIVER.read_txn_failures(txn_dir, SEG)["count"] == 3


@pytest.mark.parametrize(
    "crash_point",
    ["before_charge", "after_charge_intent_alive", "after_staging_removed_intent_alive", "after_cleanup"],
)
def test_exactly_once_across_a_crash_at_every_boundary(txn_dir, crash_point):
    """Cleanup performs TWO removals (staging, then intent), so the crash
    boundary between them is a distinct filesystem state and gets its own
    case -- a generic "between charge and delete" case would not exercise it.

    In every case the transaction is retried by a replacement driver that
    replays the same txn_id, and the count must be 1."""
    txn = DRIVER.make_txn_id("RUN", SEG, 1, 1)
    staging = txn_dir / f"{SEG}.1.staged.draft.json"
    intent = txn_dir / f"{SEG}.intent.json"
    staging.write_text("{}", encoding="utf-8")
    intent.write_text(json.dumps({"txn_id": txn, "phase": "prepared"}), encoding="utf-8")

    if crash_point != "before_charge":
        DRIVER.charge_txn_failure(txn_dir, SEG, txn, CEILING)
    if crash_point in ("after_staging_removed_intent_alive", "after_cleanup"):
        staging.unlink()
    if crash_point == "after_cleanup":
        intent.unlink()

    # Replacement driver recovers the same transaction and charges again.
    replacement = _load_module(DRIVER_SRC, f"driver_replay_{crash_point}")
    replacement.charge_txn_failure(txn_dir, SEG, txn, CEILING)

    assert replacement.read_txn_failures(txn_dir, SEG)["count"] == 1, (
        f"crash at {crash_point} must still charge exactly once"
    )


def test_charged_history_is_truncated_to_ceiling_plus_one(txn_dir):
    for i in range(1, 10):
        DRIVER.charge_txn_failure(txn_dir, SEG, DRIVER.make_txn_id("RUN", SEG, 1, i), CEILING)
    state = DRIVER.read_txn_failures(txn_dir, SEG)
    assert len(state["charged"]) == CEILING + 1
    assert state["count"] == 9, "truncating the history must not lose the COUNT"


def test_truncation_keeps_the_most_recent_ids(txn_dir):
    """Eviction must drop the OLDEST. An evicted id cannot regain a live
    intent (a segment has at most one, and no new transaction starts once the
    ceiling is reached), so only recency matters."""
    ids = [DRIVER.make_txn_id("RUN", SEG, 1, i) for i in range(1, 8)]
    for t in ids:
        DRIVER.charge_txn_failure(txn_dir, SEG, t, CEILING)
    assert DRIVER.read_txn_failures(txn_dir, SEG)["charged"] == ids[-(CEILING + 1):]


def test_truncation_does_not_let_a_recent_transaction_be_charged_twice(txn_dir):
    """The CONSEQUENCE of truncating in the wrong direction, which asserting
    the list contents alone does not catch: if eviction dropped the most
    recent ids instead of the oldest, replaying the newest transaction after
    a crash would charge it a second time. That is the double-charge the
    idempotence exists to prevent, reappearing through the history bound."""
    ids = [DRIVER.make_txn_id("RUN", SEG, 1, i) for i in range(1, 8)]
    for t in ids:
        DRIVER.charge_txn_failure(txn_dir, SEG, t, CEILING)
    before = DRIVER.read_txn_failures(txn_dir, SEG)["count"]

    DRIVER.charge_txn_failure(txn_dir, SEG, ids[-1], CEILING)

    assert DRIVER.read_txn_failures(txn_dir, SEG)["count"] == before, (
        "replaying the most recent transaction must stay a no-op after truncation"
    )


# ---------------------------------------------------------------------------
# the ceiling
# ---------------------------------------------------------------------------


def test_exhausted_uses_greater_or_equal_not_greater(txn_dir):
    """With `>` the knob named max_txn_failures_per_segment would permit
    failure number ceiling+1. Reaching the ceiling must already refuse."""
    for i in range(1, CEILING + 1):
        DRIVER.charge_txn_failure(txn_dir, SEG, DRIVER.make_txn_id("RUN", SEG, 1, i), CEILING)
        count = DRIVER.read_txn_failures(txn_dir, SEG)["count"]
        assert DRIVER.txn_failures_exhausted(txn_dir, SEG, CEILING) is (count >= CEILING)
    assert DRIVER.read_txn_failures(txn_dir, SEG)["count"] == CEILING
    assert DRIVER.txn_failures_exhausted(txn_dir, SEG, CEILING) is True


def test_ceiling_is_per_segment(txn_dir):
    for i in range(1, CEILING + 1):
        DRIVER.charge_txn_failure(txn_dir, SEG, DRIVER.make_txn_id("RUN", SEG, 1, i), CEILING)
    assert DRIVER.txn_failures_exhausted(txn_dir, SEG, CEILING) is True
    assert DRIVER.txn_failures_exhausted(txn_dir, "seg02", CEILING) is False


# ---------------------------------------------------------------------------
# hostile / degraded on-disk state
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("garbage", ["not json at all", "[]", '"a string"', '{"count": -4}', "null"])
def test_corrupt_failure_counter_fails_closed(txn_dir, garbage):
    """An EXISTING counter that cannot be decoded must not read as zero.
    Reading it as empty forgets the idempotence history and re-authorises a
    segment whose refusals were already exhausted -- so `read` reports None
    and `exhausted` answers True."""
    (txn_dir / f"{SEG}.txn_failures").write_text(garbage, encoding="utf-8")
    assert DRIVER.read_txn_failures(txn_dir, SEG) is None
    assert DRIVER.txn_failures_exhausted(txn_dir, SEG, CEILING) is True


def test_corrupt_counter_does_not_resurrect_an_exhausted_segment(txn_dir):
    """The concrete regression: reach the ceiling, then corrupt the file. The
    segment must stay refused."""
    for i in range(1, CEILING + 1):
        DRIVER.charge_txn_failure(txn_dir, SEG, DRIVER.make_txn_id("RUN", SEG, 1, i), CEILING)
    assert DRIVER.txn_failures_exhausted(txn_dir, SEG, CEILING) is True
    (txn_dir / f"{SEG}.txn_failures").write_text("}{", encoding="utf-8")
    assert DRIVER.txn_failures_exhausted(txn_dir, SEG, CEILING) is True


def test_charging_against_a_corrupt_counter_refuses(txn_dir):
    (txn_dir / f"{SEG}.txn_failures").write_text("}{", encoding="utf-8")
    assert DRIVER.charge_txn_failure(txn_dir, SEG, "RUN:seg01:1:1", CEILING) is None


def test_absent_counter_is_not_corrupt(txn_dir):
    """Absence may safely initialise an empty counter -- nothing has been
    promised yet. Only an EXISTING undecodable file fails closed."""
    assert DRIVER.read_txn_failures(txn_dir, SEG) == {"count": 0, "charged": []}
    assert DRIVER.txn_failures_exhausted(txn_dir, SEG, CEILING) is False


def test_corrupt_attempts_file_refuses_allocation(txn_dir):
    """Restarting the sequence at 1 would hand out a transaction id already
    issued, so an undecodable attempts file must refuse (0), not reset."""
    assert DRIVER.next_attempt_seq(txn_dir, SEG) == 1
    (txn_dir / f"{SEG}.attempts").write_text("}{", encoding="utf-8")
    assert DRIVER.next_attempt_seq(txn_dir, SEG) == 0


@pytest.mark.parametrize("shape", ["{}", "[]", "null", '{"attempt_seq": "bad"}',
                                   '{"attempt_seq": -1}', '{"attempt_seq": true}',
                                   '{"attempt_seq": 1.5}'])
def test_parseable_but_invalid_attempts_shape_refuses(txn_dir, shape):
    """Valid JSON is not a valid counter. `{}`, `[]`, `null` and a non-integer
    attempt_seq all parse cleanly, so keying the refusal on json.loads()
    succeeding would let them fall through to a fresh sequence and reissue an
    id the file had already allocated -- the same hazard as malformed bytes."""
    assert DRIVER.next_attempt_seq(txn_dir, SEG) == 1
    (txn_dir / f"{SEG}.attempts").write_text(shape, encoding="utf-8")
    assert DRIVER.next_attempt_seq(txn_dir, SEG) == 0


@pytest.mark.parametrize("shape", ['{"count": false, "charged": []}',
                                   '{"count": true, "charged": []}'])
def test_boolean_count_is_not_a_valid_counter(txn_dir, shape):
    """`isinstance(False, int)` is True in Python and `False >= 0` holds, so a
    boolean count would read as a legitimate zero and re-authorise a segment
    whose refusals were exhausted."""
    (txn_dir / f"{SEG}.txn_failures").write_text(shape, encoding="utf-8")
    assert DRIVER.read_txn_failures(txn_dir, SEG) is None
    assert DRIVER.txn_failures_exhausted(txn_dir, SEG, CEILING) is True


def test_parseable_invalid_shape_does_not_resurrect_an_exhausted_segment(txn_dir):
    for i in range(1, CEILING + 1):
        DRIVER.charge_txn_failure(txn_dir, SEG, DRIVER.make_txn_id("RUN", SEG, 1, i), CEILING)
    (txn_dir / f"{SEG}.txn_failures").write_text('{"count": false, "charged": []}', encoding="utf-8")
    assert DRIVER.txn_failures_exhausted(txn_dir, SEG, CEILING) is True


def test_charge_reports_failure_when_it_cannot_be_persisted(tmp_path):
    """_atomic_write_json reports durability failure; that result must reach
    the caller. Returning the incremented in-memory state would tell a caller
    following the charge-first/cleanup-second contract that the refusal was
    recorded, and it would then delete the intent and lose it."""
    missing = tmp_path / "no-such-dir"
    assert DRIVER.charge_txn_failure(missing, SEG, "RUN:seg01:1:1", CEILING) is None


def _fail_dir_fsync_times(monkeypatch, module, times):
    """Make the FIRST `times` directory fsyncs fail, counting every one.

    Failing only directory fsyncs models the real hazard exactly:
    `os.replace()` has already published the bytes by then, so the counter is
    VISIBLE while the write reports failure."""
    real_fsync = os.fsync
    state = {"dir_fsyncs": 0, "left": times}

    def fake_fsync(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            state["dir_fsyncs"] += 1
            if state["left"] > 0:
                state["left"] -= 1
                raise OSError("simulated directory fsync failure")
            return None
        return real_fsync(fd)

    monkeypatch.setattr(module.os, "fsync", fake_fsync)
    return state


def test_a_visible_but_unconfirmed_charge_is_reported_as_failure(txn_dir, monkeypatch):
    """os.replace() publishes before the directory fsync, so a failed fsync
    leaves the counter VISIBLE while the write reports failure. That must be
    reported as failure, not success."""
    module = _load_module(DRIVER_SRC, "driver_fsync_probe_a")
    _fail_dir_fsync_times(monkeypatch, module, 1)
    txn = module.make_txn_id("RUN", SEG, 1, 1)

    assert module.charge_txn_failure(txn_dir, SEG, txn, CEILING) is None
    # ...and yet it IS on disk, which is exactly what makes the replay risky.
    assert txn in module.read_txn_failures(txn_dir, SEG)["charged"]


def test_replaying_an_unconfirmed_charge_reestablishes_durability(txn_dir, monkeypatch):
    """The replay path must not shortcut on "already in charged". It has to
    retry the durability barrier, or a caller deletes the live intent over a
    rename that was never confirmed and a later crash loses the refusal."""
    module = _load_module(DRIVER_SRC, "driver_fsync_probe_b")
    counters = _fail_dir_fsync_times(monkeypatch, module, 1)
    txn = module.make_txn_id("RUN", SEG, 1, 1)

    assert module.charge_txn_failure(txn_dir, SEG, txn, CEILING) is None
    before = counters["dir_fsyncs"]

    replay = module.charge_txn_failure(txn_dir, SEG, txn, CEILING)

    assert counters["dir_fsyncs"] > before, (
        "the replay must attempt a directory fsync, not return on `already charged`"
    )
    assert replay is not None and replay["count"] == 1


def test_replay_still_reports_failure_while_durability_cannot_be_established(txn_dir, monkeypatch):
    module = _load_module(DRIVER_SRC, "driver_fsync_probe_c")
    _fail_dir_fsync_times(monkeypatch, module, 99)
    txn = module.make_txn_id("RUN", SEG, 1, 1)

    assert module.charge_txn_failure(txn_dir, SEG, txn, CEILING) is None
    assert module.charge_txn_failure(txn_dir, SEG, txn, CEILING) is None


def test_a_close_failure_refuses_rather_than_raising(txn_dir, monkeypatch):
    """close(2) can report errors. A close in `finally` outside the handler
    escapes as an exception -- and the replay path calls _fsync_dir() outside
    _atomic_write_json()'s own try, so that would crash the driver mid-batch
    instead of refusing the round."""
    module = _load_module(DRIVER_SRC, "driver_close_probe")
    real_close = os.close

    def fake_close(fd):
        is_dir = stat.S_ISDIR(os.fstat(fd).st_mode)
        real_close(fd)
        if is_dir:
            raise OSError("simulated close failure")

    monkeypatch.setattr(module.os, "close", fake_close)
    txn = module.make_txn_id("RUN", SEG, 1, 1)

    # Must return, not raise -- on the first charge and on the replay alike.
    assert module.charge_txn_failure(txn_dir, SEG, txn, CEILING) is None
    assert module.charge_txn_failure(txn_dir, SEG, txn, CEILING) is None


def test_reported_count_always_matches_what_is_on_disk(txn_dir):
    """The returned state and a fresh read must never disagree -- that
    divergence is what made an unpersisted charge look charged."""
    txn = DRIVER.make_txn_id("RUN", SEG, 1, 1)
    returned = DRIVER.charge_txn_failure(txn_dir, SEG, txn, CEILING)
    assert returned is not None
    assert returned == DRIVER.read_txn_failures(txn_dir, SEG)


def test_no_temp_file_is_left_behind(txn_dir):
    DRIVER.next_attempt_seq(txn_dir, SEG)
    DRIVER.charge_txn_failure(txn_dir, SEG, DRIVER.make_txn_id("RUN", SEG, 1, 1), CEILING)
    assert [p.name for p in txn_dir.iterdir() if p.name.endswith(".tmp")] == []


def test_counters_are_separate_files(txn_dir):
    """If they shared a file, deleting or rewriting one would move the other,
    which is exactly the coupling the split exists to prevent."""
    DRIVER.next_attempt_seq(txn_dir, SEG)
    DRIVER.charge_txn_failure(txn_dir, SEG, DRIVER.make_txn_id("RUN", SEG, 1, 1), CEILING)
    assert (txn_dir / f"{SEG}.attempts").is_file()
    assert (txn_dir / f"{SEG}.txn_failures").is_file()
    os.remove(txn_dir / f"{SEG}.txn_failures")
    assert DRIVER.next_attempt_seq(txn_dir, SEG) == 2, "attempts must not reset when failures are cleared"
