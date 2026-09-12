#!/usr/bin/env python3
"""The in-place citation repair: what selects the rows, and where they land.

Two properties carry this feature, and both fail SILENTLY if they regress, which
is why they are tested by outcome rather than by log line:

  1. THE SELECTOR IS NEVER MODEL OUTPUT. The failed set comes from
     `fetch_citation.py`'s own `item_index` and `outcome`. A judge-authored list
     would be model output derived from attacker-authored page bodies, so a
     hostile page cited for row A could name valid row B and have B silently
     re-decided. These tests pin the derivation and the two exclusions that keep
     a hostile server from steering it.

  2. THE SPLICE LANDS ON THE ROW IT WAS PRODUCED FOR. Positions and base BOTH come
     from the approved snapshot, never the still-writable attempt fragment. The
     reorder test is the load-bearing one: `canon_validate.py --check-batch`
     compares source-form SETS, never order, so a repair landing on the wrong rows
     produces a fragment that passes every shipped gate while carrying decisions
     attached to the wrong names.
"""

import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
DRIVER = (PLUGIN_ROOT / "skills" / "literary-translator" / "assets" / "scripts"
          / "glossary_dispatch_driver.py")
JSON_STDOUT = DRIVER.parent / "json_stdout.py"


@pytest.fixture
def mod(tmp_path):
    scripts = tmp_path / "durable" / "scripts"
    scripts.mkdir(parents=True)
    target = scripts / "glossary_dispatch_driver.py"
    shutil.copy2(DRIVER, target)
    # json_stdout.py is the driver's one hard sibling dependency: it is loaded
    # by exact path at import time and the driver exits without it, exactly as a
    # deployed copy does. Staging it keeps this fixture a real scripts/ dir.
    shutil.copy2(JSON_STDOUT, target.parent / "json_stdout.py")
    spec = importlib.util.spec_from_file_location("gdd_repair", target)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _dumps_entry(evidence_path):
    return json.dumps({"entries": [{"item_index": 0, "outcome": "fetched",
                                    "evidence_file": evidence_path}]})


def row(form, basis="established", **kw):
    return {"source_form": form, "basis": basis, "disposition": "accepted", **kw}


# ---------------------------------------------------------------------------
# 1. The selector
# ---------------------------------------------------------------------------

def test_only_established_rows_can_be_repaired(mod):
    """`fetch_citation.py` indexes every source-bearing row, but the judge is told
    to ignore every non-established one. An unfiltered set would repair rows no
    judge would ever have objected to -- re-deciding a name for no reason."""
    pairs = [{"item_index": 0, "outcome": "http_error:404"},
             {"item_index": 1, "outcome": "http_error:404"}]
    out = mod.classify_outcomes(pairs, {1})
    assert out["repairable"] == [1], "a non-established row must not be repaired"


def test_established_indices_reads_basis_positionally(mod):
    rows = [row("A"), row("B", basis="transliterated"), row("C")]
    assert mod.established_indices(rows) == {0, 2}


@pytest.mark.parametrize("outcome", sorted({"refused:batch-deadline",
                                            "refused:batch-byte-budget"}))
def test_shared_budget_outcomes_are_never_repaired(mod, outcome):
    """Two independent reasons, either sufficient. They are environment faults --
    a fresh URL cannot fix a run that ran out of time or bytes. And they are the
    one lever by which a HOSTILE server can push a different row into the failure
    set, by consuming the shared budget before that row is reached."""
    pairs = [{"item_index": 0, "outcome": outcome}]
    out = mod.classify_outcomes(pairs, {0})
    assert out["repairable"] == []
    assert out["budget_failed"] == [0]


def test_fetched_rows_are_neither_repaired_nor_failed(mod):
    pairs = [{"item_index": 0, "outcome": "fetched"}]
    out = mod.classify_outcomes(pairs, {0})
    assert out == {"budget_failed": [], "repairable": []}


class _TattlingEntry(dict):
    """A dict that RECORDS every key anyone looks up.

    Asserting on the returned pairs proves only what came back; an implementation
    could consult `source` or `final_origin`, branch on it, discard it, and return
    an identical shape. The #347 boundary is about what is READ, so the test has
    to watch reads."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.touched = set()

    def get(self, key, default=None):
        self.touched.add(key)
        return super().get(key, default)

    def __getitem__(self, key):
        self.touched.add(key)
        return super().__getitem__(key)

    def __contains__(self, key):
        self.touched.add(key)
        return super().__contains__(key)


def test_outcome_read_takes_only_two_fields(mod, tmp_path):
    """THE #347 BOUNDARY, as an assertion about ACCESS rather than about shape.

    Everything else in an entry is either model-authored (source, source_form,
    basis), server-authored (final_origin, chain, content_type) or the retrieved
    bytes themselves (evidence_file). This process chooses what to fetch next, so
    it must read none of them."""
    index = tmp_path / "index.json"
    index.write_text(
        '{"entries": [{"item_index": 0, "outcome": "fetched",'
        ' "source": "https://evil.test/x", "source_form": "A",'
        ' "final_origin": "https://elsewhere.test", "chain": ["a", "b"],'
        ' "evidence_file": "ev_000.txt", "content_type": "text/html",'
        ' "bytes": 12}]}', encoding="utf-8")
    pairs = mod.read_outcome_pairs(index)
    assert pairs == [{"item_index": 0, "outcome": "fetched"}]

    # Re-run the same parse over a watching entry to see which keys are consulted.
    entry = _TattlingEntry({"item_index": 0, "outcome": "fetched",
                            "source": "https://evil.test/x", "source_form": "A",
                            "final_origin": "https://elsewhere.test",
                            "chain": ["a", "b"], "evidence_file": "ev_000.txt",
                            "content_type": "text/html", "bytes": 12})

    real_load = json.load
    try:
        json.load = lambda fh: {"entries": [entry]}
        mod.read_outcome_pairs(index)
    finally:
        json.load = real_load
    forbidden = entry.touched - {"item_index", "outcome"}
    assert not forbidden, (
        f"read_outcome_pairs consulted {sorted(forbidden)}; the actor that "
        f"chooses what to fetch next must read only fields fetch_citation.py "
        f"itself authored")


def test_no_evidence_body_is_ever_opened(mod, tmp_path):
    """The other half of A4: naming an evidence_file must not lead to opening it."""
    evidence = tmp_path / "ev_000.txt"
    evidence.write_text("attacker-authored page body", encoding="utf-8")
    index = tmp_path / "index.json"
    index.write_text(_dumps_entry(str(evidence)), encoding="utf-8")
    opened = []
    import builtins
    real_open = builtins.open

    def watching_open(file, *a, **kw):
        opened.append(str(file))
        return real_open(file, *a, **kw)

    builtins.open = watching_open
    try:
        mod.read_outcome_pairs(index)
    finally:
        builtins.open = real_open
    assert str(evidence) not in opened, "an evidence body was opened"


# ---------------------------------------------------------------------------
# 2. The repair artifact's shape
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("rows,label", [
    ([row("A")], "a missing row leaves a bad citation in place while a rung burns"),
    ([row("A"), row("C"), row("B")],
     "an extra row rewrites a row whose citation retrieved fine"),
    ([row("C"), row("A")], "a reorder lands each decision on the wrong row"),
    ([row("A"), row("A")], "a duplicate makes the positional splice ambiguous"),
])
def test_repair_shape_refusals(mod, rows, label):
    with pytest.raises(mod.DriverError):
        mod.validate_repair_rows(rows, ["A", "C"])


def test_a_row_without_a_string_source_form_is_refused(mod):
    with pytest.raises(mod.DriverError):
        mod.validate_repair_rows([{"basis": "established"}], ["A"])


def test_the_exact_requested_sequence_is_accepted(mod):
    mod.validate_repair_rows([row("A"), row("C")], ["A", "C"])


# ---------------------------------------------------------------------------
# 3. The splice
# ---------------------------------------------------------------------------

def test_splice_replaces_only_the_failed_positions(mod):
    snapshot = [row("A"), row("B"), row("C")]
    repaired = mod.splice_repair(
        snapshot, [0, 2], [row("A", source="ok-a"), row("C", source="ok-c")])
    assert [r["source_form"] for r in repaired] == ["A", "B", "C"]
    assert repaired[0]["source"] == "ok-a"
    assert repaired[2]["source"] == "ok-c"
    assert repaired[1] == snapshot[1], "an untouched row must be untouched"


def test_splice_does_not_mutate_the_snapshot_it_was_given(mod):
    snapshot = [row("A"), row("B")]
    before = [dict(r) for r in snapshot]
    mod.splice_repair(snapshot, [0], [row("A", source="new")])
    assert snapshot == before


def test_the_snapshot_is_the_base_even_when_the_attempt_fragment_reordered(mod):
    """THE LOAD-BEARING ONE. The attempt fragment is still writable by the codex
    job that produced it, and the template says so. If positions taken from the
    snapshot were applied to a REORDERED attempt file, each repaired decision
    would land on a different name -- and `--check-batch` could not see it,
    because it compares source-form SETS and the set is unchanged.

    Driving splice_repair with the snapshot proves the decision follows the row.
    The reordered attempt list is built here to show it produces a DIFFERENT,
    silently-wrong result had it been used as the base."""
    snapshot = [row("A"), row("B"), row("C")]
    reordered_attempt = [row("C"), row("B"), row("A")]
    failed = [0]                       # position of "A" in the SNAPSHOT
    fix = [row("A", source="verified-a")]

    correct = mod.splice_repair(snapshot, failed, fix)
    assert correct[0]["source_form"] == "A" and correct[0]["source"] == "verified-a"

    wrong = mod.splice_repair(reordered_attempt, failed, fix)
    assert wrong[0]["source_form"] == "A", "the fix row carries its own source_form"
    assert [r["source_form"] for r in wrong] == ["A", "B", "A"], (
        "using the mutable attempt as the base duplicates one name and drops "
        "another -- the corruption this test exists to keep out")
    assert sorted(r["source_form"] for r in correct) == ["A", "B", "C"]


# ---------------------------------------------------------------------------
# 3. #918 -- a body that DID retrieve, and came back identical to another URL's
#
# The retrieval boundary records `unusable:duplicate-body` for it. Two things
# must hold and both fail silently: the row has to reach the repair gate at all
# (it is a failure, even though nothing failed to retrieve), and the repair
# prompt has to be told which rows are which, because the set is mixed more
# often than not and the two facts cannot share one sentence.
# ---------------------------------------------------------------------------

DUPLICATE_BODY = "unusable:duplicate-body"


def test_a_duplicate_body_row_lands_in_repairable_with_no_branch_for_it(mod):
    """PINNED, NOT SPECIAL-CASED. `classify_outcomes()` gets no new case: the
    token is not the success value and not a shared-budget one, so the existing
    catch-all already puts it here. This test exists so that stays true -- a
    later reader adding an explicit branch, or tightening the catch-all into a
    list of known failures, would drop the row into nothing at all and the batch
    would go to a judge with a dead shell as its evidence."""
    pairs = [{"item_index": 0, "outcome": DUPLICATE_BODY}]
    out = mod.classify_outcomes(pairs, {0})
    assert out == {"budget_failed": [], "repairable": [0]}


def test_duplicate_body_indices_names_the_rows_the_boundary_flagged(mod):
    pairs = [{"item_index": 0, "outcome": DUPLICATE_BODY},
             {"item_index": 1, "outcome": "http_error:404"},
             {"item_index": 2, "outcome": DUPLICATE_BODY}]
    assert mod.duplicate_body_indices(pairs, {0, 1, 2}) == [0, 2]


def test_duplicate_body_indices_is_empty_when_nothing_repeated(mod):
    """The empty answer is what keeps the ordinary retrieval-failure path
    byte-identical to before #918: no duplicates, no cause, no new argument."""
    pairs = [{"item_index": 0, "outcome": "http_error:404"},
             {"item_index": 1, "outcome": "refused:content-type-not-allowed"}]
    assert mod.duplicate_body_indices(pairs, {0, 1}) == []


def test_duplicate_body_indices_answers_only_about_the_set_it_was_given(mod):
    """The second argument is `eligible_indices`, NOT the established set, and
    the repair gate really does hand it something else: the REPAIRABLE set, by
    which point `classify_outcomes()` has already applied the established
    restriction once.

    The two sets give the same answer for this token -- a duplicate-body row is
    never the success value and never a shared-budget outcome, so it is in
    `repairable` exactly when it is established -- so no test can tell them
    apart by their result. What this one pins instead is the property that
    matters either way: a row outside the set it was handed is not reported."""
    pairs = [{"item_index": 0, "outcome": DUPLICATE_BODY},
             {"item_index": 1, "outcome": DUPLICATE_BODY}]
    assert mod.duplicate_body_indices(pairs, {1}) == [1]


def test_the_token_is_matched_whole_and_never_by_prefix(mod):
    """The outcome vocabulary is closed. A neighbouring `unusable:` token this
    driver has not been taught is NOT a duplicate body, and must not borrow the
    paragraph that says the bytes repeated another URL's."""
    pairs = [{"item_index": 0, "outcome": "unusable:duplicate-body-ish"},
             {"item_index": 1, "outcome": "unusable:something-else"}]
    assert mod.duplicate_body_indices(pairs, {0, 1}) == []
    assert mod.classify_outcomes(pairs, {0, 1})["repairable"] == [0, 1], (
        "an unknown failure token still has to reach the repair gate -- only "
        "the CAUSE it is given may differ")


# ---------------------------------------------------------------------------
# 3b. The cause the gate hands the repair prompt
# ---------------------------------------------------------------------------

class _FakeCtx:
    """Only the surface `prepare_and_hand_back()` actually touches.

    `build()` answers with the paths this test wrote, so the template is never
    executed here: what is under test is which cause the gate NAMES, and the
    prompt those causes render is pinned separately, against the real template,
    in tests/glossary_dispatch_driver.test.py."""

    def __init__(self, approved, index, durable_root):
        self.durable_root = durable_root
        self.subst = {"run_id": "runX"}
        self._paths = {"approve": "true", "approved": str(approved),
                       "fetch": "true", "index": str(index), "judge": "JUDGE"}

    def build(self, calls):
        return {c["key"]: self._paths[c["key"]] for c in calls}


def _gate(mod, monkeypatch, tmp_path, rows, outcomes):
    """Drives the REAL gate over a real snapshot and a real evidence index.

    `outcomes[i]` is the outcome recorded for snapshot position i. Nothing is
    stubbed but the two shell commands -- the snapshot is read by load_rows()
    and the index by read_outcome_pairs(), both unmodified."""
    approved = tmp_path / "approved.json"
    approved.write_text(json.dumps(rows), encoding="utf-8")
    index = tmp_path / "index.json"
    index.write_text(json.dumps({"entries": [
        {"item_index": i, "outcome": o} for i, o in enumerate(outcomes)]}),
        encoding="utf-8")
    monkeypatch.setattr(mod, "run_template_cmd",
                        lambda cmd, timeout=600: (0, "", ""))
    ctx = _FakeCtx(approved, index, tmp_path)
    return mod.prepare_and_hand_back(ctx, {"index": 0}, 0, tmp_path / "frag.json")


def test_every_repairable_row_duplicate_names_the_duplicate_body_cause(mod, monkeypatch, tmp_path):
    out = _gate(mod, monkeypatch, tmp_path, [row("A"), row("B")],
                [DUPLICATE_BODY, DUPLICATE_BODY])
    assert out["state"] == "needs_repair"
    assert out["failedPositions"] == [0, 1]
    assert out["cause"] == "duplicate-body"
    assert out["duplicatePositions"] == [0, 1]


def test_no_duplicate_row_leaves_the_result_exactly_as_it_was_before(mod, monkeypatch, tmp_path):
    """The unchanged path, asserted as a WHOLE dict. run_repair() defaults to
    "unretrievable" on a missing key, so an extra key here -- a cause of
    "unretrievable" spelled out, an empty duplicatePositions list -- would be a
    silent change to the one path #918 promised to leave alone."""
    out = _gate(mod, monkeypatch, tmp_path, [row("A"), row("B")],
                ["http_error:404", "fetched"])
    assert out == {"state": "needs_repair", "batchIndex": 0, "attempt": 0,
                   "failedPositions": [0],
                   "snapshotPath": str(tmp_path / "approved.json")}


def test_a_mixed_repair_set_still_names_the_duplicate_body_cause(mod, monkeypatch, tmp_path):
    """WHENEVER ANY, not only when all -- and the measured majority of flagged
    batches are mixed. One list goes to the gate and one prompt goes to the
    agent, so an all-or-nothing cause would give most of these batches a
    paragraph that is false about half their rows. The positions travel with the
    cause precisely so the prompt can split them."""
    out = _gate(mod, monkeypatch, tmp_path,
                [row("A"), row("B"), row("C")],
                [DUPLICATE_BODY, "http_error:404", "fetched"])
    assert out["cause"] == "duplicate-body"
    assert out["failedPositions"] == [0, 1]
    assert out["duplicatePositions"] == [0]


def test_a_duplicate_body_row_never_reaches_a_judge(mod, monkeypatch, tmp_path):
    """The whole point of catching this at the boundary: a judge spent on a dead
    application shell rejects for want of content, and that rejection is then
    read as a content rejection of the fragment."""
    out = _gate(mod, monkeypatch, tmp_path, [row("A")], [DUPLICATE_BODY])
    assert out["state"] == "needs_repair"
    assert "judgePrompt" not in out


def test_a_shared_budget_row_still_wins_over_a_duplicate_body_one(mod, monkeypatch, tmp_path):
    """Step 6a returns first, deliberately and unchanged: a run that ran out of
    time or bytes is an environment fault, and a hostile server able to spend
    that budget must not be able to steer which rows get repaired."""
    out = _gate(mod, monkeypatch, tmp_path, [row("A"), row("B")],
                [DUPLICATE_BODY, "refused:batch-deadline"])
    assert out["state"] == "evidence_failed"
    assert out["reason"] == "fetch-budget-exhausted"


# ---------------------------------------------------------------------------
# 3c. The ordinals run_repair() computes out of those positions
# ---------------------------------------------------------------------------

class _FakeSandbox:
    """Stands in for DispatchSandbox, whose confinement probe and broker
    teardown are tested on their own and are not what this section is about."""

    def __init__(self, label):
        self.label = label
        self.path = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def artifact(self, name):
        return Path("/private/tmp/ltgd.fake") / name


class _RecordingCtx:
    """Records the arguments every builder call is made with."""

    def __init__(self, tmp_path):
        self.calls = []
        self.companion = "companion.mjs"
        self.node_bin = "node"
        self.effort = "high"
        self.tmpdir = str(tmp_path)
        self._paths = {
            "repairpath": str(tmp_path / "repair.json"),
            "nextfragment": str(tmp_path / "next.json"),
            "nextcheck": "true",
        }

    def build(self, calls):
        self.calls.extend(calls)
        out = {}
        for c in calls:
            out[c["key"]] = self._paths.get(c["key"], "--background\nPROMPT")
        return out

    def repair_args(self):
        for c in self.calls:
            if c["fn"] == "batchRepairPrompt":
                return c["args"]
        raise AssertionError("batchRepairPrompt was never built")


def _run_repair(mod, monkeypatch, tmp_path, snapshot_rows, failed_positions,
                cause, duplicate_positions):
    snapshot = tmp_path / "snap.json"
    snapshot.write_text(json.dumps(snapshot_rows), encoding="utf-8")
    monkeypatch.setattr(mod, "DispatchSandbox", _FakeSandbox)
    monkeypatch.setattr(mod, "launch_codex", lambda **kw: "job-1")
    monkeypatch.setattr(mod, "wait_for_artifact", lambda *a, **kw: {
        "ready": False, "jobStatus": "completed", "jobDetail": None})
    ctx = _RecordingCtx(tmp_path)
    out = mod.run_repair(ctx, {"index": 0}, 0, failed_positions, snapshot,
                         cause=cause, duplicate_positions=duplicate_positions)
    assert out["state"] == "repair_invalid", out
    return ctx.repair_args()


def test_the_ordinals_index_the_list_the_agent_is_shown_not_the_snapshot(mod, monkeypatch, tmp_path):
    """THE WHOLE REASON ORDINALS EXIST. `failed_rows` is a SUBSET of the
    snapshot, in snapshot order, and it is the only list the agent ever sees. A
    snapshot position handed straight to the prompt would name a row that is not
    in that list at all -- here, snapshot position 3 is the SECOND item shown."""
    rows = [row("A"), row("B"), row("C"), row("D")]
    args = _run_repair(mod, monkeypatch, tmp_path, rows, [1, 3],
                       "duplicate-body", [3])
    assert args[4] == "duplicate-body"
    assert args[6] == [2], "1-based ordinal of snapshot position 3 within [1, 3]"


def test_the_ordinals_are_positions_and_never_source_forms(mod, monkeypatch, tmp_path):
    """A MEASURED HAZARD, not a hypothetical. canon-batch.schema.json permits two
    queued established rows to carry ONE source_form with different sources, and
    canon_validate.py's coverage check compares source-form SETS. So a prompt
    that named the duplicate rows by form would point the agent at a row whose
    URL retrieved perfectly well -- and at a repair rung, a row named is a row
    replaced or downgraded."""
    rows = [row("Twin", source="https://shell.test/a"),
            row("Twin", source="https://real.test/b")]
    args = _run_repair(mod, monkeypatch, tmp_path, rows, [0, 1],
                       "duplicate-body", [0])
    assert args[6] == [1]
    assert [r["source"] for r in args[2]] == ["https://shell.test/a",
                                              "https://real.test/b"], (
        "both rows share one source_form, so only their POSITION separates them")


def test_an_unretrievable_repair_passes_an_empty_ordinal_list(mod, monkeypatch, tmp_path):
    """The default path carries no duplicate rows, so there is nothing to name.
    The builder renders byte-identically either way -- pinned as a complete
    string in tests/glossary_dispatch_driver.test.py -- and this asserts the
    driver's own half of that: it invents no ordinals it was not given."""
    args = _run_repair(mod, monkeypatch, tmp_path, [row("A")], [0],
                       "unretrievable", None)
    assert args[4] == "unretrievable"
    assert args[6] == []


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
