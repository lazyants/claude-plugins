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
import shutil
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
DRIVER = (PLUGIN_ROOT / "skills" / "literary-translator" / "assets" / "scripts"
          / "glossary_dispatch_driver.py")


@pytest.fixture
def mod(tmp_path):
    scripts = tmp_path / "durable" / "scripts"
    scripts.mkdir(parents=True)
    target = scripts / "glossary_dispatch_driver.py"
    shutil.copy2(DRIVER, target)
    spec = importlib.util.spec_from_file_location("gdd_repair", target)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _dumps_entry(evidence_path):
    import json
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
    import json as _json
    real_load = _json.load
    try:
        _json.load = lambda fh: {"entries": [entry]}
        mod.read_outcome_pairs(index)
    finally:
        _json.load = real_load
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


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
