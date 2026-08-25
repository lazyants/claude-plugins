"""tests/glossary_batch_plan.test.py -- regression suite for
glossary_batch_plan.py (1.3.5, issues #101 W3 resumability filter + #95
batch-cost curation + #91 elision-ambiguity force-inclusion).

Drives the REAL, on-disk script via subprocess (house style: single-line
JSON to stdout, exit 0 success / non-zero fatal). Every load-bearing case
was observed RED against a deliberately-broken build of the script before
this suite was accepted GREEN -- see the dispatch report's red-before-green
section for the exact mutations (review_queue exclusion removed; the #91
force-inclusion re-gated behind likely_name; the co-location closure pull
removed; the stale --retry guard removed -- each turned its own case red and
nothing else).

The property under test, end to end: this script curates bootstrap_names.py
candidates into the glossary Workflow's `args` (and resume_setup.py's
`batches`) by MECHANICAL rules only -- exclude what canon.json already holds,
keep the likely/frequent survivors, and never drop an elision-ambiguous pair
that #91 needs an adjudicator to see -- never making an accuracy call.
"""
import json
import subprocess
import sys
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))
# Shared durable-root builder (#653 producer/consumer regression below):
# glossary_batch_plan.py is one of its STAGED_SCRIPTS, and its
# `run_canon_validate`/`accepted_item`/`queued_item` are the one sanctioned
# way to drive the REAL canon_validate.py --correct/--merge-batches rather
# than hand-rolling a second copy of that write path here.
from _canon_project_fixture import (  # noqa: E402
    accepted_item,
    make_project,
    queued_item,
    read_canon,
    run_canon_init,
    run_canon_validate,
    run_script,
    write_fragment,
)

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (
    PLUGIN_ROOT
    / "skills" / "literary-translator" / "assets" / "scripts"
    / "glossary_batch_plan.py"
)


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def cand(name, freq=5, likely_name=True, mid_sentence=1, **extra):
    """One bootstrap_names.py-shaped candidate row."""
    words = name.split()
    row = {
        "name": name,
        "freq": freq,
        "mid_sentence": mid_sentence,
        "multiword": len(words) > 1,
        "abbrev": len(words) == 1 and len(words[0]) == 1,
        "n_segments": 1,
        "likely_name": likely_name,
    }
    row.update(extra)
    return row


def write_inputs(tmp_path, candidates, entries=None, review_queue=None, corrections=None):
    nc_path = tmp_path / "name_candidates.json"
    nc_path.write_text(
        json.dumps(
            {
                "n_candidates": len(candidates),
                "n_strong": sum(1 for c in candidates if c.get("likely_name")),
                "candidates": candidates,
            }
        ),
        encoding="utf-8",
    )
    canon_path = tmp_path / "canon.json"
    canon_doc = {
        "entries": entries or {},
        "review_queue": review_queue or [],
        "generation_hashes": {
            "particle_config_hash": "pc",
            "derivation_bundle_hash": "db",
        },
    }
    # `corrections` is OPTIONAL (#653) -- omitted entirely, not just empty,
    # whenever the caller passes nothing, so the "absent key" path (a
    # canon.json written before #495/#653) is exercised by every OTHER test
    # in this file rather than only by a dedicated one.
    if corrections is not None:
        canon_doc["corrections"] = corrections
    canon_path.write_text(json.dumps(canon_doc), encoding="utf-8")
    return nc_path, canon_path


def queued(source_form, note="disputed"):
    """A minimal canon-file review_queue[] item (QUEUED shape)."""
    return {
        "source_form": source_form,
        "is_proper_name": True,
        "disposition": "review_queue",
        "note": note,
    }


def dismissal(source_form, old_item=None, reason="not canon-worthy"):
    """A minimal canon-file corrections[] document with disposition:"dismiss"
    (#653) -- the shape canon_validate.py's run_correct dismiss branch
    appends to corrections[] verbatim (plan D2/D3: `old_item`, not
    `old_entry` -- dismiss targets a review_queue[] row, never entries{})."""
    return {
        "source_form": source_form,
        "disposition": "dismiss",
        "old_item": old_item if old_item is not None else source_form,
        "reason": reason,
    }


def run(nc_path, canon_path, *extra):
    argv = [
        sys.executable,
        str(SCRIPT),
        "--name-candidates",
        str(nc_path),
        "--canon",
        str(canon_path),
        *extra,
    ]
    return subprocess.run(argv, capture_output=True, text=True)


def run_ok(nc_path, canon_path, *extra):
    proc = run(nc_path, canon_path, *extra)
    assert proc.returncode == 0, f"expected exit 0, got {proc.returncode}; stderr={proc.stderr}"
    return json.loads(proc.stdout)


def names_in_args(result):
    """Flat set of every candidate name in the `args` projection."""
    out = set()
    for batch in result["args"]:
        for cand_row in batch["candidates"]:
            out.add(cand_row["name"])
    return out


# ---------------------------------------------------------------------------
# #101 -- exclusion of already-resolved candidates
# ---------------------------------------------------------------------------


def test_excludes_entries_candidate(tmp_path):
    nc, canon = write_inputs(
        tmp_path,
        [cand("Alice"), cand("Bob")],
        entries={"Bob": {"canonical_target_form": "Боб"}},
    )
    result = run_ok(nc, canon)
    got = names_in_args(result)
    assert "Alice" in got  # control: an un-resolved candidate still flows through
    assert "Bob" not in got  # excluded by entries{} membership


def test_excludes_review_queue_candidate(tmp_path):
    """THE #101 regression -- a review_queue source_form was never excluded
    before this script existed (the old prose only ever excluded entries{})."""
    nc, canon = write_inputs(
        tmp_path,
        [cand("Alice"), cand("Bob")],
        review_queue=[queued("Bob")],
    )
    result = run_ok(nc, canon)
    got = names_in_args(result)
    assert "Alice" in got  # control: proves Bob's absence is the exclusion, not a drop
    assert "Bob" not in got


def test_retry_reincludes_queued_candidate(tmp_path):
    nc, canon = write_inputs(
        tmp_path,
        [cand("Alice"), cand("Bob")],
        review_queue=[queued("Bob")],
    )
    # Without --retry Bob is excluded (previous test); with it, re-included.
    result = run_ok(nc, canon, "--retry", "Bob")
    got = names_in_args(result)
    assert got == {"Alice", "Bob"}


def test_retry_accepts_comma_separated_and_repeated(tmp_path):
    nc, canon = write_inputs(
        tmp_path,
        [cand("Alice"), cand("Bob"), cand("Carol")],
        review_queue=[queued("Bob"), queued("Carol")],
    )
    result = run_ok(nc, canon, "--retry", "Bob,Carol")
    assert names_in_args(result) == {"Alice", "Bob", "Carol"}
    result2 = run_ok(nc, canon, "--retry", "Bob", "--retry", "Carol")
    assert names_in_args(result2) == {"Alice", "Bob", "Carol"}


def test_stale_retry_name_fails_loudly(tmp_path):
    """A --retry name in NEITHER name_candidates.json nor review_queue is a
    stale name from an earlier book -- must fail, never silently no-op."""
    nc, canon = write_inputs(
        tmp_path,
        [cand("Alice")],
        review_queue=[queued("Bob")],
    )
    proc = run(nc, canon, "--retry", "Napoleon")
    assert proc.returncode != 0
    assert "Napoleon" in proc.stderr
    assert proc.stdout.strip() == ""  # stdout stays clean on a fatal error


def test_retry_name_only_in_candidates_is_accepted(tmp_path):
    """A --retry naming a plain (non-queued) candidate is not stale -- it is
    present in name_candidates.json, so it must NOT fail (only absence from
    BOTH inputs is fatal)."""
    nc, canon = write_inputs(tmp_path, [cand("Alice")])
    result = run_ok(nc, canon, "--retry", "Alice")
    assert names_in_args(result) == {"Alice"}


# ---------------------------------------------------------------------------
# #653 -- a dismissed name's exclusion survives the review_queue row being
# dropped. Without this, dismissing a row (canon_validate.py --correct
# disposition:"dismiss") would undo itself on the very next W3 sweep: the
# name is still in the source text, so it is still in name_candidates.json,
# and with no corrections[]-based exclusion it would simply be re-included.
# ---------------------------------------------------------------------------


def test_excludes_dismissed_candidate(tmp_path):
    """A name with NO review_queue row but a corrections[] dismiss record is
    still excluded -- the whole point of #653 (the queue row is gone)."""
    nc, canon = write_inputs(
        tmp_path,
        [cand("Alice"), cand("Bob")],
        corrections=[dismissal("Bob")],
    )
    result = run_ok(nc, canon)
    got = names_in_args(result)
    assert "Alice" in got  # control: proves Bob's absence is the exclusion, not a drop
    assert "Bob" not in got


def test_retry_reincludes_dismissed_candidate(tmp_path):
    """--retry lifts the dismissal exclusion exactly as it lifts the queued
    one -- that flag IS the "operator says so" #653 requires."""
    nc, canon = write_inputs(
        tmp_path,
        [cand("Alice"), cand("Bob")],
        corrections=[dismissal("Bob")],
    )
    result = run_ok(nc, canon, "--retry", "Bob")
    assert names_in_args(result) == {"Alice", "Bob"}


def test_retry_of_dismissed_name_absent_from_candidates_is_recognized_not_fatal(tmp_path):
    """The unknown_retry fatal guard must learn the dismissed set: a --retry
    against a re-extracted source where the dismissed name produced no
    candidate row must NOT abort the run (it is RECOGNIZED, reaching the
    ordinary non-fatal no-dispatch path -- the same treatment a queued name
    gets), and must emit the dismissal-specific note rather than the
    review_queue one (the row is gone, so that note would be false)."""
    nc, canon = write_inputs(
        tmp_path,
        [cand("Alice")],
        corrections=[dismissal("Napoleon")],
    )
    proc = run(nc, canon, "--retry", "Napoleon")
    assert proc.returncode == 0
    result = json.loads(proc.stdout)
    assert names_in_args(result) == {"Alice"}
    assert "note:" in proc.stderr and "Napoleon" in proc.stderr
    assert "dismissed" in proc.stderr
    assert "review_queue" not in proc.stderr


def test_dismissed_name_later_requeued_still_excluded_once(tmp_path):
    """A name dismissed and then RE-QUEUED (an ordinary W3a batch re-proposed
    it and it landed back in review_queue for a fresh reason) must still be
    excluded -- the queued exclusion and the dismissed exclusion are two
    separate sets, and hitting either is sufficient; hitting both must not
    double-exclude or otherwise misbehave."""
    nc, canon = write_inputs(
        tmp_path,
        [cand("Alice"), cand("Bob")],
        review_queue=[queued("Bob")],
        corrections=[dismissal("Bob")],
    )
    result = run_ok(nc, canon)
    assert names_in_args(result) == {"Alice"}
    # --retry lifts BOTH exclusions at once (there is only one flag).
    retried = run_ok(nc, canon, "--retry", "Bob")
    assert names_in_args(retried) == {"Alice", "Bob"}


def test_requeued_after_dismissal_diagnostic_says_review_queue(tmp_path):
    """The diagnostic for a re-queued-after-dismissal name that STILL has no
    candidate row must say "review_queue" -- it is, at this point, literally
    true again -- not the dismissal note, which would be stale."""
    nc, canon = write_inputs(
        tmp_path,
        [cand("Alice")],
        review_queue=[queued("Bob")],
        corrections=[dismissal("Bob")],
    )
    proc = run(nc, canon, "--retry", "Bob")
    assert proc.returncode == 0
    assert "Bob" in proc.stderr and "review_queue" in proc.stderr


def test_no_corrections_key_behaves_as_today(tmp_path):
    """A canon.json with no `corrections` key at all (every canon.json
    written before #495/#653) behaves exactly as before -- OPTIONAL and
    absent must stay valid and mean "no dismissals"."""
    nc, canon = write_inputs(
        tmp_path,
        [cand("Alice"), cand("Bob")],
        review_queue=[queued("Bob")],
    )
    result = run_ok(nc, canon)
    assert names_in_args(result) == {"Alice"}


def test_corrections_with_non_dismiss_disposition_does_not_exclude(tmp_path):
    """A corrections[] document whose disposition is NOT "dismiss" (e.g. an
    unrelated entries{} correction/removal sharing no relation here) must not
    exclude anything -- only disposition:"dismiss" does."""
    nc, canon = write_inputs(
        tmp_path,
        [cand("Alice"), cand("Bob")],
        corrections=[
            {
                "source_form": "Bob",
                "disposition": "remove",
                "old_entry": {"canonical_target_form": "x"},
                "reason": "unrelated entries{} removal",
            }
        ],
    )
    result = run_ok(nc, canon)
    assert names_in_args(result) == {"Alice", "Bob"}


def test_malformed_corrections_item_is_skipped_not_fatal(tmp_path):
    """A non-object item in corrections[] is skipped, exactly like the
    review_queue loop's handling of a non-object item -- one bad row must not
    block every other row's exclusion."""
    nc, canon = write_inputs(
        tmp_path,
        [cand("Alice"), cand("Bob")],
        corrections=["not-an-object", dismissal("Bob")],
    )
    result = run_ok(nc, canon)
    assert names_in_args(result) == {"Alice"}


def test_non_array_corrections_fails_loudly(tmp_path):
    """A non-array `corrections` top-level value fails loudly, mirroring the
    existing non-array `review_queue` handling."""
    nc_path = tmp_path / "name_candidates.json"
    nc_path.write_text(
        json.dumps({"n_candidates": 0, "n_strong": 0, "candidates": [cand("Alice")]}),
        encoding="utf-8",
    )
    canon_path = tmp_path / "canon.json"
    canon_path.write_text(
        json.dumps({"entries": {}, "review_queue": [], "corrections": "nope"}),
        encoding="utf-8",
    )
    proc = run(nc_path, canon_path)
    assert proc.returncode != 0
    assert "corrections" in proc.stderr


def test_dismissed_twice_is_simply_dismissed(tmp_path):
    """Two separate corrections[] dismiss documents for the same source_form
    (a hand-edited log, or -- once #653's SKILL.md guidance is in place --
    an operator dismissing, retrying, and dismissing again) is just
    dismissed; the exclusion is a set membership test (ANY dismiss document
    for the name), not an ordering question, so a duplicate collapses with
    no special handling needed."""
    nc, canon = write_inputs(
        tmp_path,
        [cand("Alice"), cand("Bob")],
        corrections=[
            dismissal("Bob", reason="first pass: common word"),
            dismissal("Bob", reason="second pass, independent hand edit: confirmed"),
        ],
    )
    result = run_ok(nc, canon)
    assert names_in_args(result) == {"Alice"}


# ---------------------------------------------------------------------------
# #653 regression (review-loop MAJOR) -- an entries{}-side correction must
# NEVER silently revoke a review_queue{}-side dismissal that shares its
# source_form. `dismiss` and `correct`/`remove` adjudicate two DISJOINT
# structures (review_queue[] vs entries{}); a "most recent corrections[]
# document wins" rule (the earlier, rejected version of this exclusion)
# lets a LATER, unrelated entries{}-side `remove` silently undo an earlier
# review_queue{}-side `dismiss`, through four entirely ordinary sanctioned
# moves:
#   1. dismiss N                         (canon_validate.py --correct)
#   2. an operator --retry's N           (the explicit re-open #653 requires)
#   3. N is researched and accepted      (canon_validate.py --merge-batches)
#   4. N is later `remove`d for an UNRELATED reason (e.g. turns out
#      interpolated, zero real occurrences)
# After step 4, N is in neither entries{} nor review_queue[], and
# corrections[] holds [dismiss(N), remove(N)]. The FIX is deleting the
# "most recent" precedence logic entirely, not adding a condition to it:
# `dismissed` is every source_form carrying ANY dismiss document, full
# stop -- an entries{}-scoped document never touches it. Drives the REAL
# canon_validate.py --correct/--merge-batches AND the REAL
# glossary_batch_plan.py end to end (shared _canon_project_fixture builder,
# same convention as tests/canon_dismiss_queued.test.py's own producer/
# consumer integration test, which this one is deliberately one step
# longer than -- that test stops after the dismissal alone).
# ---------------------------------------------------------------------------


def _fixture_candidate_row(name: str, freq: int = 5) -> dict:
    words = name.split()
    return {
        "name": name,
        "freq": freq,
        "mid_sentence": 1,
        "multiword": len(words) > 1,
        "abbrev": len(words) == 1 and len(words[0]) == 1,
        "n_segments": 1,
        "likely_name": True,
    }


def _run_correct(root, correction_path):
    # allow_durable_sibling=False: --correct never stamps, so it resolves no
    # sibling cache_key.py and must not need either #412 flag (mirrors
    # tests/canon_dismiss_queued.test.py's run_correct()).
    return run_canon_validate(
        root, "--correct", str(correction_path), allow_durable_sibling=False
    )


def test_dismiss_then_retry_accept_then_unrelated_remove_still_excludes(tmp_path):
    root = make_project(tmp_path)
    assert run_canon_init(root).returncode == 0

    row = queued_item("Fantine", note="candidate under dispute")
    canon = read_canon(root)
    canon["review_queue"] = [row]
    (root / "canon.json").write_text(json.dumps(canon, ensure_ascii=False), encoding="utf-8")

    # Step 1: dismiss Fantine.
    doc = dismissal("Fantine", row, "a common word the detector mis-flagged")
    dismissed = _run_correct(root, write_fragment(root, doc, name="dismiss.json"))
    assert dismissed.returncode == 0, f"{dismissed.stdout}\n{dismissed.stderr}"
    assert read_canon(root)["review_queue"] == []

    # Fantine is still IN THE TEXT throughout this whole sequence (a
    # re-extraction would surface it again), so it is a name_candidates.json
    # row before AND after every step below -- the same file is read by
    # every glossary_batch_plan.py invocation in this test.
    candidates = [_fixture_candidate_row("Fantine"), _fixture_candidate_row("Marius")]
    name_candidates_path = root / "name_candidates.json"
    name_candidates_path.write_text(
        json.dumps(
            {
                "n_candidates": len(candidates),
                "n_strong": sum(1 for c in candidates if c["likely_name"]),
                "candidates": candidates,
            }
        ),
        encoding="utf-8",
    )

    def dispatched_names(*extra_args):
        proc = run_script(
            root, "glossary_batch_plan.py",
            "--name-candidates", str(name_candidates_path),
            *extra_args,
        )
        assert proc.returncode == 0, f"glossary_batch_plan.py failed:\n{proc.stdout}\n{proc.stderr}"
        result = json.loads(proc.stdout)
        return {c["name"] for batch in result["args"] for c in batch["candidates"]}

    # Step 2: the operator's explicit re-open. This is the assertion this
    # test exists to make -- that --retry is what makes step 3 legitimate,
    # not merely that the final state comes out excluded. Confirms Fantine
    # really IS dispatched under --retry right after a dismissal, which is
    # the load-bearing premise of the whole "operator-typed re-open"
    # argument in the module docstring.
    reretried = dispatched_names("--retry", "Fantine")
    assert "Marius" in reretried  # control
    assert "Fantine" in reretried, (
        "--retry did not re-include a dismissed name -- the operator-typed "
        "re-open path itself is broken, not just the later exclusion"
    )

    # Step 3: the research from that re-open lands via an ordinary
    # --merge-batches accept, unchanged by #653 -- Fantine is frozen into
    # entries{}.
    fragment = write_fragment(root, [accepted_item("Fantine", "Fantine")])
    accepted = run_canon_validate(root, "--merge-batches", str(fragment))
    assert accepted.returncode == 0, f"{accepted.stdout}\n{accepted.stderr}"
    assert "Fantine" in read_canon(root)["entries"]

    # Step 4: LATER, an entries{}-side correction removes the frozen record
    # for a reason that has NOTHING to do with the earlier dismissal.
    remove_doc = {
        "source_form": "Fantine",
        "disposition": "remove",
        "old_entry": read_canon(root)["entries"]["Fantine"],
        "reason": "turns out interpolated -- zero real occurrences in the source",
    }
    removed = _run_correct(root, write_fragment(root, remove_doc, name="remove.json"))
    assert removed.returncode == 0, f"{removed.stdout}\n{removed.stderr}"
    after = read_canon(root)
    assert "Fantine" not in after["entries"]
    assert after["review_queue"] == []
    assert [c["disposition"] for c in after["corrections"]] == ["dismiss", "remove"]

    # Final assertion: WITHOUT --retry (no operator said so this run),
    # Fantine is excluded again -- exactly the state that would silently
    # re-open it if the exclusion looked only at the LAST corrections[]
    # document.
    final = dispatched_names()
    assert "Marius" in final  # control: proves the exclusion, not a drop of everything
    assert "Fantine" not in final, (
        "an entries{}-side `remove` correction silently revoked an earlier "
        "review_queue{}-side `dismiss` decision -- #653 forbids automated "
        "re-research reopening a dismissed name with no operator saying so"
    )


# ---------------------------------------------------------------------------
# --retry non-dispatch diagnostics (stderr note; stdout/exit unchanged).
# A --retry name that passes the neither-input fatal guard but still resolves
# to no dispatched candidate must not be SILENTLY swallowed -- it undercuts
# #101's explicit-human-retry intent. Non-fatal: exit stays 0, stdout stays
# the clean JSON line, the explanation goes to stderr only.
# ---------------------------------------------------------------------------


def test_retry_in_queue_but_not_a_candidate_emits_note(tmp_path):
    """Case (a): the retry name is in review_queue but no longer appears as a
    candidate row (source re-extracted) -> nothing to dispatch, plus a note."""
    nc, canon = write_inputs(
        tmp_path,
        [cand("Alice")],
        review_queue=[queued("Bob")],
    )
    proc = run(nc, canon, "--retry", "Bob")
    assert proc.returncode == 0
    result = json.loads(proc.stdout)  # stdout stays the expected clean JSON
    assert names_in_args(result) == {"Alice"}
    assert "note:" in proc.stderr and "Bob" in proc.stderr
    assert "review_queue" in proc.stderr


def test_retry_dropped_by_curation_emits_note(tmp_path):
    """Case (b): the retry name IS a current candidate and survives the
    review_queue exclusion, but step-2 curation still drops it (below the
    freq floor / not likely_name) -> a note, not silence."""
    nc, canon = write_inputs(
        tmp_path,
        [cand("Alice"), cand("Bob", freq=1, likely_name=False, mid_sentence=0)],
        review_queue=[queued("Bob")],
    )
    proc = run(nc, canon, "--retry", "Bob")
    assert proc.returncode == 0
    result = json.loads(proc.stdout)
    assert names_in_args(result) == {"Alice"}
    assert "Bob" in proc.stderr
    assert "not dispatched" in proc.stderr


def test_retry_of_resolved_entry_emits_note(tmp_path):
    """A --retry name already resolved in entries{} is not dispatched (retry
    overrides only the review_queue exclusion, never a resolved entry) -> a
    note rather than silence."""
    nc, canon = write_inputs(
        tmp_path,
        [cand("Alice"), cand("Bob")],
        entries={"Bob": {}},
    )
    proc = run(nc, canon, "--retry", "Bob")
    assert proc.returncode == 0
    assert names_in_args(json.loads(proc.stdout)) == {"Alice"}
    assert "Bob" in proc.stderr and "already resolved" in proc.stderr


def test_retry_diagnostic_prefers_entries_note_over_dismissal_note(tmp_path):
    """#653 bot review P2 (PR #703, glossary_batch_plan.py:532-536): a name can
    legitimately be BOTH a resolved entries{} key AND carry a `dismiss`
    document in corrections[] -- the sanctioned dismiss -> --retry ->
    accepted-merge sequence produces exactly that overlap. If it is later
    absent from name_candidates.json (an ordinary source re-extraction) and
    --retried again, the entries{} note must fire, NOT the dismissal note:
    the dismissal note says "the source may have been re-extracted since it
    was dismissed", which implies a future re-extraction would let --retry
    dispatch it -- FALSE, since step (1) excludes a resolved entries{} key
    UNCONDITIONALLY and --retry never overrides that exclusion. Before the
    fix, `entry_keys` was checked AFTER candidate-name absence, so this
    name hit the candidate-absence branch first and got the (now
    inaccurate) dismissal note; entries{} is checked FIRST now."""
    nc, canon = write_inputs(
        tmp_path,
        [cand("Alice")],  # Bob is NOT a current candidate (re-extracted away)
        entries={"Bob": {"canonical_target_form": "Боб"}},
        corrections=[dismissal("Bob")],
    )
    proc = run(nc, canon, "--retry", "Bob")
    assert proc.returncode == 0
    assert names_in_args(json.loads(proc.stdout)) == {"Alice"}
    assert "already resolved in canon.json's entries{}" in proc.stderr
    assert "was dismissed in canon.json's corrections[]" not in proc.stderr


def test_dispatched_retry_emits_no_note(tmp_path):
    """No false positive: a --retry name that IS dispatched gets no note."""
    nc, canon = write_inputs(
        tmp_path,
        [cand("Alice"), cand("Bob")],
        review_queue=[queued("Bob")],
    )
    proc = run(nc, canon, "--retry", "Bob")
    assert proc.returncode == 0
    assert names_in_args(json.loads(proc.stdout)) == {"Alice", "Bob"}
    assert "note: --retry" not in proc.stderr


# ---------------------------------------------------------------------------
# Zero-candidate short-circuit
# ---------------------------------------------------------------------------


def test_empty_eligible_set_emits_no_new_candidates(tmp_path):
    """Everything already resolved -> the exact schema-shaped marker, so the
    orchestrator skips resume_setup.py + the Workflow entirely."""
    nc, canon = write_inputs(
        tmp_path,
        [cand("Alice"), cand("Bob")],
        entries={"Alice": {}, "Bob": {}},
    )
    result = run_ok(nc, canon)
    assert result == {"no_new_candidates": True, "batches": []}


def test_all_below_floor_emits_no_new_candidates(tmp_path):
    """No entries/queue at all, but every survivor fails the step-2 predicate
    -> still the empty marker (not an empty `batches` payload)."""
    nc, canon = write_inputs(
        tmp_path,
        [cand("Alice", freq=1, likely_name=False, mid_sentence=0)],
    )
    result = run_ok(nc, canon, "--min-candidate-freq", "2")
    assert result == {"no_new_candidates": True, "batches": []}


# ---------------------------------------------------------------------------
# #95 -- frequency curation
# ---------------------------------------------------------------------------


def test_min_candidate_freq_shrinks_batch(tmp_path):
    """Assert the candidate COUNT shrinks (not just a tag): raising the floor
    from 2 to 5 must drop the freq<5 rows."""
    candidates = [
        cand("Alice", freq=10),
        cand("Bob", freq=3),
        cand("Carol", freq=2),
    ]
    nc, canon = write_inputs(tmp_path, candidates)

    low = run_ok(nc, canon, "--min-candidate-freq", "2")
    assert names_in_args(low) == {"Alice", "Bob", "Carol"}

    high = run_ok(nc, canon, "--min-candidate-freq", "5")
    assert names_in_args(high) == {"Alice"}


def test_not_likely_name_excluded_even_above_floor(tmp_path):
    """likely_name=False is excluded even at high freq (unless force-included
    by the #91 bypass, tested separately)."""
    nc, canon = write_inputs(
        tmp_path,
        [cand("Alice", freq=10, likely_name=True), cand("Bonjour", freq=10, likely_name=False)],
    )
    result = run_ok(nc, canon, "--min-candidate-freq", "2")
    assert names_in_args(result) == {"Alice"}


# ---------------------------------------------------------------------------
# #91 -- elision-ambiguous force-inclusion (the interaction with #95)
# ---------------------------------------------------------------------------


def test_elision_pair_bypasses_full_step2_predicate(tmp_path):
    """The dominant #91 case: a sentence-initial capitalized elision yields a
    single-word, mid_sentence=0, freq=1, likely_name=FALSE ambiguous row plus
    an equally weak stripped-form target. Both are below the freq floor AND
    fail likely_name -- a bypass that only skipped the floor (still requiring
    likely_name) would silently kill this case. A plain equally-weak row that
    is neither ambiguous nor a target stays excluded, proving the bypass is
    specific, not a blanket force-include."""
    candidates = [
        cand(
            "L'Enclos", freq=1, likely_name=False, mid_sentence=0,
            elision_ambiguous=True, elision_stripped_form="Enclos",
        ),
        cand("Enclos", freq=1, likely_name=False, mid_sentence=0),
        cand("Xyz", freq=1, likely_name=False, mid_sentence=0),  # weak control
    ]
    nc, canon = write_inputs(tmp_path, candidates)
    result = run_ok(nc, canon, "--min-candidate-freq", "2")
    got = names_in_args(result)
    assert "L'Enclos" in got  # ambiguous row force-included
    assert "Enclos" in got    # its stripped-form target force-included
    assert "Xyz" not in got   # an equally-weak non-elision row stays excluded


def test_elision_ambiguous_row_forced_even_when_target_excluded_at_step1(tmp_path):
    """If the stripped-form target is already in entries{} (excluded at step
    1), it STAYS excluded -- but the ambiguous row alone is still
    force-included (carrying its elision_stripped_form as adjudicator
    context)."""
    candidates = [
        cand(
            "L'Enclos", freq=1, likely_name=False, mid_sentence=0,
            elision_ambiguous=True, elision_stripped_form="Enclos",
        ),
        cand("Enclos", freq=1, likely_name=False, mid_sentence=0),
    ]
    nc, canon = write_inputs(tmp_path, candidates, entries={"Enclos": {}})
    result = run_ok(nc, canon, "--min-candidate-freq", "2")
    got = names_in_args(result)
    assert "L'Enclos" in got
    assert "Enclos" not in got


def test_elision_pair_colocated_same_batch(tmp_path):
    """Even when freq-sort would separate them (target freq=50 sorts first,
    ambiguous row freq=1 sorts last) and --batch-size=1 would put each in its
    own batch, the co-location pull keeps the pair in ONE batch."""
    candidates = [
        cand(
            "L'Enclos", freq=1, likely_name=False, mid_sentence=0,
            elision_ambiguous=True, elision_stripped_form="Enclos",
        ),
        cand("Enclos", freq=50, likely_name=True),
        cand("Filler1", freq=40, likely_name=True),
        cand("Filler2", freq=30, likely_name=True),
    ]
    nc, canon = write_inputs(tmp_path, candidates)
    result = run_ok(nc, canon, "--batch-size", "1")

    batch_of = {}
    for batch in result["args"]:
        for cand_row in batch["candidates"]:
            batch_of[cand_row["name"]] = batch["index"]
    assert batch_of["Enclos"] == batch_of["L'Enclos"], (
        f"elision pair split across batches: {batch_of}"
    )


# ---------------------------------------------------------------------------
# Output-shape invariants
# ---------------------------------------------------------------------------


def test_projections_have_identical_name_sets(tmp_path):
    """The `args` candidates and the `batches` names-only projection must
    carry identical name sets, batch for batch (the one drift channel between
    the Workflow input and resume_setup.py's manifest)."""
    candidates = [cand(f"Name{i:02d}", freq=100 - i) for i in range(25)]
    nc, canon = write_inputs(tmp_path, candidates)
    result = run_ok(nc, canon, "--batch-size", "10")

    assert len(result["args"]) == len(result["batches"])
    for arg_batch, name_batch in zip(result["args"], result["batches"]):
        assert arg_batch["index"] == name_batch["index"]
        args_names = {c["name"] for c in arg_batch["candidates"]}
        assert args_names == set(name_batch["names"])
    # And the union across batches equals the full eligible set.
    assert names_in_args(result) == {c["name"] for c in candidates}


def test_candidates_passed_through_verbatim(tmp_path):
    """Each `args` candidate is the bootstrap row VERBATIM -- including the
    #91 elision fields, which the adjudicator prompt relies on."""
    row = cand(
        "L'Enclos", freq=1, likely_name=False, mid_sentence=0,
        elision_ambiguous=True, elision_stripped_form="Enclos",
    )
    nc, canon = write_inputs(tmp_path, [row, cand("Enclos", freq=1, likely_name=False, mid_sentence=0)])
    result = run_ok(nc, canon, "--min-candidate-freq", "2")
    emitted = None
    for batch in result["args"]:
        for cand_row in batch["candidates"]:
            if cand_row["name"] == "L'Enclos":
                emitted = cand_row
    assert emitted == row  # byte-for-byte the same object, extra fields intact


def test_batch_size_chunks(tmp_path):
    candidates = [cand(f"Name{i:02d}", freq=100 - i) for i in range(23)]
    nc, canon = write_inputs(tmp_path, candidates)
    result = run_ok(nc, canon, "--batch-size", "10")
    sizes = [len(b["candidates"]) for b in result["args"]]
    assert sizes == [10, 10, 3]


# ---------------------------------------------------------------------------
# Fatal-input handling
# ---------------------------------------------------------------------------


def test_missing_name_candidates_fails(tmp_path):
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--name-candidates", str(tmp_path / "nope.json")],
        capture_output=True,
        text=True,
    )
    assert proc.returncode != 0
    assert "name_candidates.json not found" in proc.stderr


def test_explicit_missing_canon_fails(tmp_path):
    nc, _ = write_inputs(tmp_path, [cand("Alice")])
    proc = run(nc, tmp_path / "nope.json")
    assert proc.returncode != 0
    assert "--canon path not found" in proc.stderr


def test_min_freq_below_one_is_rejected(tmp_path):
    nc, canon = write_inputs(tmp_path, [cand("Alice")])
    proc = run(nc, canon, "--min-candidate-freq", "0")
    assert proc.returncode != 0


def test_batch_size_below_one_is_rejected(tmp_path):
    nc, canon = write_inputs(tmp_path, [cand("Alice")])
    proc = run(nc, canon, "--batch-size", "0")
    assert proc.returncode != 0
