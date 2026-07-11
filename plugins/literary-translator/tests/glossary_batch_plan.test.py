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


def write_inputs(tmp_path, candidates, entries=None, review_queue=None):
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
    canon_path.write_text(
        json.dumps(
            {
                "entries": entries or {},
                "review_queue": review_queue or [],
                "generation_hashes": {
                    "particle_config_hash": "pc",
                    "derivation_bundle_hash": "db",
                },
            }
        ),
        encoding="utf-8",
    )
    return nc_path, canon_path


def queued(source_form, note="disputed"):
    """A minimal canon-file review_queue[] item (QUEUED shape)."""
    return {
        "source_form": source_form,
        "is_proper_name": True,
        "disposition": "review_queue",
        "note": note,
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
