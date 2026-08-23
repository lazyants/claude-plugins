"""tests/canon_correct_entry.test.py -- coverage for issue #495: a frozen
canon.json entry that is simply WRONG had no sanctioned correction route.

`_merge_batch` refuses an accepted item whose source_form already carries a
DIFFERENT resolution, `--init` is create-only, `--restamp-derivation` touches
provenance only, and validate-only writes nothing -- so the only available move
was a hand-edit of the exact artifact the whole gate chain treats as frozen,
performed outside every validation the tool owns and recorded nowhere. #495 adds
`--correct PATH`: out-of-band, one entry per call, stating the OLD value (refused
naming both when it does not match disk), carrying a required reason, and
appending the correction document verbatim to canon.json's `corrections[]`.

WHY THIS SUITE STAGES FRENCH, when every sibling canon suite stages Hebrew.
`_canon_project_fixture` exists for #290/#291, whose whole subject is the
zero-candidate path: he.json ships no `name_inventory`, so `bootstrap_names.py`'s
`Lu`-gated detector finds nothing in an uncased source and `segpack.py` builds
`canon_names: []` for every segment no matter what is in `entries{}`. This
suite's central assertion is a COUNT over the segments that reference a
corrected entry, and over that fixture the counted set is empty BY
CONSTRUCTION -- "exactly one moved, and no others" would pass vacuously, which
is precisely the shape a re-stale assertion must not have. So it swaps the
particle config to `fr.json` and writes its own three-segment French manifest,
arranged so that

  seg01 references ONLY the entry under correction,
  seg02 references a DIFFERENT frozen entry,
  seg03 references none.

seg02 is what makes "and no others" a real assertion rather than "the empty set
did not change": without it, an implementation that re-staled everything and one
that re-staled exactly the right segment would be indistinguishable.

Every hash figure here is read from the AUTHORITATIVE implementation --
`cache_key.py --seg <id> --field used_terms_hash`, the same call
`resume_setup.py` shells out to -- never re-derived locally. Every behavioural
assertion drives the real scripts as subprocesses.

Covered:
  1. Blast radius as a COUNT: correcting one entry moves exactly one segment's
     used_terms_hash and leaves the other two byte-identical.
  2. The blind-use interlock: a wrong `old_entry` is refused naming BOTH values,
     and canon.json is left byte-identical.
  3. The guard #495 must NOT widen: an ordinary `--merge-batches` carrying a
     differing resolution still raises the collision error. This test passes
     before the change too -- it is the pin, not a new capability.
  4. Round-trip: validate-only still passes, generation_hashes are byte-identical
     (a correction never restamps -- #291), the record is in `corrections[]`, and
     nothing outside canon.json was written.
  5. `remove`, with the same count discipline.
  6. The recollapse ASYMMETRY: `correct` on an adjudicated homonym split is
     refused, `remove` on the same form is allowed -- removal is the only repair
     `canon_adjudication_audit.py`'s BLOCKING `collapsed_split` can accept.
  7. The rest of the refusal battery, each leaving canon.json byte-identical.
  7b. The content controls a SECOND write path into entries{} must not skip --
     #347's citation-source boundary and the offline basis:"established"
     backstop -- with their two scopes pinned in both directions: the citation
     check fires on the absolute value, the backstop on any correction that
     STATES an established claim it did not already carry verbatim (the claim
     being the canonical_target_form/source pair, not the basis label), and
     `remove` is exempt from both.
  8. A correction can repair an entry that fails its OWN schema -- the case
     `old_entry` is deliberately not $ref-validated for.
"""
import json
import shutil
import sys
from pathlib import Path

import pytest
import yaml

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))
from _canon_project_fixture import (  # noqa: E402
    LANGUAGES_SRC,
    make_project,
    read_canon,
    run_canon_init,
    run_canon_validate,
    run_script,
    stamp_of,
    write_fragment,
)

FRENCH_CONFIG = "fr.json"
SEGS = ("seg01", "seg02", "seg03")

# seg01 carries the name under correction, seg02 a different frozen one, seg03
# none. Both names are multiword AND mid-sentence, so they clear
# segpack.py's `strong_names` filter on either leg.
FRENCH_BLOCKS = {
    "p1": ("seg01", "Le matin, Jean Valjean quitta la ville sans rien dire à personne."),
    "p2": ("seg02", "Plus tard, Cosette Fauchelevent revint seule vers la maison basse."),
    "p3": ("seg03", "Le vieux jardinier ne parla plus jamais de cette longue nuit."),
}
CORRECTED = "Jean Valjean"
UNTOUCHED = "Cosette Fauchelevent"


def _entry(source_form: str, target_form: str, **overrides) -> dict:
    entry = {
        "source_form": source_form,
        "is_proper_name": True,
        "canonical_target_form": target_form,
        "basis": "transliterated",
        "confidence": "high",
    }
    entry.update(overrides)
    return entry


def _accepted(source_form: str, target_form: str) -> dict:
    return dict(_entry(source_form, target_form), disposition="accepted")


def _sense(sense_id: str, disambiguator: str) -> dict:
    return {
        "sense_id": sense_id,
        "disambiguator": disambiguator,
        "index_scope": "narrative",
        "evidence": {
            "block": "PARA:seg01:0001",
            "seg": "seg01",
            "char_start": 0,
            "char_end": 4,
            "context_start": 0,
            "context_end": 10,
            "sha256": "0" * 64,
        },
    }


def write_split_sidecar(root: Path, source_form: str) -> None:
    """An adjudicated homonym split for `source_form` at the DEFAULT sidecar
    path, so every mode picks it up without a --senses-path override."""
    (root / "canon_senses.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "entries_by_source_form": {
                    source_form: {
                        "senses": [_sense("s1", "the convict"), _sense("s2", "the mayor")]
                    }
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def french_manifest() -> dict:
    return {
        "segments": [
            {
                "seg": seg,
                "title_text": f"Chapitre {i + 1}",
                "kind": "body",
                "word_count": 12,
                "block_ids": [block],
            }
            for i, (block, (seg, _)) in enumerate(FRENCH_BLOCKS.items())
        ],
        "blocks": {
            block: {"id": block, "seg": seg, "order_index": i, "plain_text": text}
            for i, (block, (seg, text)) in enumerate(FRENCH_BLOCKS.items())
        },
        "footnotes": [],
        "verse": {"store": []},
        "generation_hashes": {
            "source_extraction_hash": "a" * 40,
            "source_input_hash": "b" * 40,
        },
    }


def make_french_project(tmp_path, merge=True) -> Path:
    """The shared Step-0a-shaped durable_root, re-pointed at fr.json with a
    three-segment French manifest, canon bootstrapped, and (by default) two
    entries frozen through the REAL merge path."""
    root = make_project(tmp_path)
    shutil.copy2(LANGUAGES_SRC / FRENCH_CONFIG, root / "languages" / FRENCH_CONFIG)
    (root / "profile.yml").write_text(
        yaml.safe_dump(
            {"source": {"language": {"particle_config": FRENCH_CONFIG}}}, sort_keys=False
        ),
        encoding="utf-8",
    )
    (root / "manifest.json").write_text(
        json.dumps(french_manifest(), ensure_ascii=False), encoding="utf-8"
    )

    init = run_canon_init(root)
    assert init.returncode == 0, f"--init failed:\n{init.stdout}\n{init.stderr}"

    if merge:
        fragment = write_fragment(
            root,
            [
                _accepted(CORRECTED, "Jean Valjean"),
                _accepted(UNTOUCHED, "Cosette Fauchelevent"),
            ],
        )
        merged = run_canon_validate(root, "--merge-batches", str(fragment))
        assert merged.returncode == 0, (
            f"--merge-batches failed:\n{merged.stdout}\n{merged.stderr}"
        )
    return root


def run_segpack_fr(root: Path):
    proc = run_script(
        root,
        "segpack.py",
        "--all",
        "--particle-config",
        FRENCH_CONFIG,
        "--apparatus-policy",
        "omit_apparatus",
    )
    assert proc.returncode == 0, f"segpack.py failed:\n{proc.stdout}\n{proc.stderr}"
    return proc


def used_terms_hashes(root: Path) -> dict:
    """Per-segment used_terms_hash, read from the script that OWNS the
    computation. A local re-derivation would be a second implementation of the
    very thing under test."""
    values = {}
    for seg in SEGS:
        proc = run_script(root, "cache_key.py", "--seg", seg, "--field", "used_terms_hash")
        assert proc.returncode == 0, (
            f"cache_key.py --seg {seg} --field used_terms_hash failed:\n"
            f"{proc.stdout}\n{proc.stderr}"
        )
        values[seg] = proc.stdout.strip()
        assert values[seg], f"empty used_terms_hash for {seg}"
    return values


def write_correction(root: Path, doc, name="correction.json") -> Path:
    """Delegates to the fixture's writer rather than restating it. Kept as its
    own name because `write_fragment` says "fragment", and a correction
    document is precisely NOT one -- the intent belongs at the call site."""
    return write_fragment(root, doc, name=name)


def correction_doc(disposition="correct", **overrides) -> dict:
    doc = {
        "source_form": CORRECTED,
        "disposition": disposition,
        "old_entry": _entry(CORRECTED, "Jean Valjean"),
        "reason": "the source spells this name with a doubled l throughout",
    }
    if disposition == "correct":
        doc["new_entry"] = _entry(CORRECTED, "Jean Valljean")
    doc.update(overrides)
    return doc


def run_correct(root: Path, correction_path: Path, research_mode="offline"):
    # allow_durable_sibling=False on purpose: --correct is a NON-stamping mode,
    # so it resolves no sibling cache_key.py and must not need either #412 flag.
    # Letting the fixture append one would hide exactly that property.
    return run_canon_validate(
        root,
        "--correct",
        str(correction_path),
        research_mode=research_mode,
        allow_durable_sibling=False,
    )


def canon_bytes(root: Path) -> bytes:
    return (root / "canon.json").read_bytes()


def payload_of(proc) -> dict:
    return json.loads(proc.stdout)


def assert_refused(proc, root: Path, before: bytes, *needles):
    """Every refusal in this mode has the same three obligations: exit 1, a JSON
    failure payload naming what it saw, and a canon.json that did not move a
    single byte."""
    assert proc.returncode == 1, (
        f"expected a refusal (exit 1), got {proc.returncode}:\n{proc.stdout}\n{proc.stderr}"
    )
    payload = payload_of(proc)
    assert payload["success"] is False, payload
    for needle in needles:
        assert needle in payload["error"], (
            f"refusal does not name {needle!r}: {payload['error']!r}"
        )
    assert canon_bytes(root) == before, "a refused correction still modified canon.json"


# ---------------------------------------------------------------------------
# 1. Blast radius, as a COUNT
# ---------------------------------------------------------------------------


def test_correction_restales_exactly_the_segments_referencing_that_entry(tmp_path):
    """#495's first acceptance criterion, verbatim: "correcting one entry
    re-stales exactly the segments referencing it, and no others (assert the
    count, not just 'some')".

    The mechanism is `cache_key.compute_used_terms_hash`, which projects
    `entries{}` onto `segpack.canon_names | new_names` -- so the guarantee is
    per-segment, not whole-corpus, and that is what makes a correction cost
    bounded re-review rather than a re-translation of the book."""
    root = make_french_project(tmp_path)
    run_segpack_fr(root)

    packs = {
        seg: json.loads((root / "segments" / f"segpack_{seg}.json").read_text(encoding="utf-8"))
        for seg in SEGS
    }
    # The fixture's own premise, asserted rather than assumed: if the detector
    # found nothing, every count below would compare empty sets and pass for a
    # reason that has nothing to do with the correction path.
    assert packs["seg01"]["canon_names"] == [CORRECTED], packs["seg01"]["canon_names"]
    assert packs["seg02"]["canon_names"] == [UNTOUCHED], packs["seg02"]["canon_names"]
    assert packs["seg03"]["canon_names"] == [], packs["seg03"]["canon_names"]

    before = used_terms_hashes(root)
    assert len(set(before.values())) == 3, (
        f"the three segments must start from three DISTINCT digests, else "
        f"'exactly one moved' cannot be observed: {before}"
    )

    proc = run_correct(root, write_correction(root, correction_doc()))
    assert proc.returncode == 0, f"--correct failed:\n{proc.stdout}\n{proc.stderr}"

    run_segpack_fr(root)
    after = used_terms_hashes(root)
    moved = sorted(seg for seg in SEGS if before[seg] != after[seg])
    assert moved == ["seg01"], (
        f"expected exactly the one segment referencing {CORRECTED!r} to re-stale, "
        f"got {moved}\n  before={before}\n  after={after}"
    )


# ---------------------------------------------------------------------------
# 2. The blind-use interlock
# ---------------------------------------------------------------------------


def test_wrong_old_entry_is_refused_naming_both_values(tmp_path):
    """#495's second acceptance criterion. `old_entry` exists to make the mode
    unusable against a canon.json that moved since it was read, so the refusal
    must show the operator BOTH what is on disk and what they claimed."""
    root = make_french_project(tmp_path)
    before = canon_bytes(root)

    stale = correction_doc(old_entry=_entry(CORRECTED, "Jean Valjeanne"))
    proc = run_correct(root, write_correction(root, stale))

    assert_refused(
        proc,
        root,
        before,
        "Jean Valjeanne",  # the value the caller stated
        "'Jean Valjean'",  # and the one actually frozen
        "refusing to correct blind",
    )


# ---------------------------------------------------------------------------
# 3. The guard this change must NOT widen
# ---------------------------------------------------------------------------


def test_merge_batches_still_collides_on_a_differing_resolution(tmp_path):
    """#495's third acceptance criterion, and the reason correction is
    out-of-band rather than a relaxed merge: an ordinary re-adjudication batch
    carrying a DIFFERENT resolution for a frozen source_form must still be
    rejected. This test passes on the unfixed tree too -- it is a pin against
    the fix being implemented by loosening `_merge_batch`, not a new
    capability."""
    root = make_french_project(tmp_path)
    before = canon_bytes(root)

    fragment = write_fragment(
        root, [_accepted(CORRECTED, "Jean Valljean")], name="recollide.json"
    )
    proc = run_canon_validate(root, "--merge-batches", str(fragment))

    assert proc.returncode == 1, f"the collision guard did not fire:\n{proc.stdout}"
    payload = payload_of(proc)
    assert "collision" in payload["error"], payload["error"]
    # Not a needle on the NEW value: `_bounded_message` caps each collision
    # line, and the frozen entry's repr already exhausts that budget, so the
    # incoming resolution is truncated away. Assert what the message actually
    # guarantees -- which source_form collided, and that the guard's own
    # wording is intact.
    assert f"{CORRECTED!r}: existing entry" in payload["error"], payload["error"]
    assert "conflicts with newly" in payload["error"], payload["error"]
    assert canon_bytes(root) == before, "a rejected batch still modified canon.json"


# ---------------------------------------------------------------------------
# 4. Round-trip
# ---------------------------------------------------------------------------


def test_round_trip_validate_only_passes_and_provenance_is_preserved(tmp_path):
    """#495's fourth acceptance criterion.

    The stamp half is the load-bearing one: `_content_view` excludes only
    generation_hashes, so a corrected entry reads as a CHANGED document and the
    #291 rule would restamp it. That must not happen -- segpack.py copies these
    two hashes verbatim into every pack and select_segments.py's
    derivation-state gate compares that copy against a fresh cache_key.py value,
    so restamping here would clear `blocked_needs_regeneration` with nothing
    regenerated.

    The criterion's other half -- "the affected units are admissible via
    --from-converged" -- is asserted as the property that MAKES them admissible:
    the correction writes no draft, no ledger record and no `.ever_converged`
    sentinel, so nothing about a unit's convergence state moved.
    --from-converged's own admission rules are covered by select_segments.py's
    suite; a second copy of them here would be a weaker restatement of that
    gate."""
    root = make_french_project(tmp_path)
    stamp_before = stamp_of(root)
    doc = correction_doc()

    proc = run_correct(root, write_correction(root, doc))
    assert proc.returncode == 0, f"--correct failed:\n{proc.stdout}\n{proc.stderr}"

    payload = payload_of(proc)
    assert payload["mode"] == "correct"
    assert payload["source_form"] == CORRECTED
    assert payload["disposition"] == "correct"
    assert payload["entries_count"] == 2
    assert payload["corrections_count"] == 1
    assert payload["generation_hashes_restamped"] is False, payload

    canon = read_canon(root)
    assert canon["entries"][CORRECTED] == doc["new_entry"]
    assert canon["entries"][UNTOUCHED] == _entry(UNTOUCHED, "Cosette Fauchelevent")
    assert canon["corrections"] == [doc], (
        "the correction document must be recorded VERBATIM -- the record is the "
        "only thing that tells the next operator this was adjudicated"
    )
    assert stamp_of(root) == stamp_before, (
        "a correction restamped generation_hashes -- that clears "
        "select_segments.py's derivation-state gate with nothing regenerated (#291)"
    )

    health = run_canon_validate(root)
    assert health.returncode == 0, (
        f"validate-only rejects the corrected canon.json:\n{health.stdout}\n{health.stderr}"
    )

    assert not (root / "runs").exists() or not list((root / "runs").glob("**/*.draft.json")), (
        "a correction wrote a draft"
    )
    assert not list(root.glob("**/.ever_converged*")), "a correction wrote a sentinel"
    assert not (root / "runs" / "ledger.json").exists(), "a correction wrote a ledger record"


# ---------------------------------------------------------------------------
# 5. remove
# ---------------------------------------------------------------------------


def test_remove_deletes_the_entry_and_restales_only_its_segments(tmp_path):
    """The disposition #495's own cases need twice over: an interpolated name
    with zero source occurrences must go AWAY, not change. Removal is also the
    only route that clears canon_adjudication_audit.py's BLOCKING
    `collapsed_split` (see the split test below)."""
    root = make_french_project(tmp_path)
    run_segpack_fr(root)
    before = used_terms_hashes(root)
    stamp_before = stamp_of(root)

    doc = correction_doc(
        disposition="remove", reason="interpolated; zero occurrences in the source"
    )
    proc = run_correct(root, write_correction(root, doc))
    assert proc.returncode == 0, f"--correct remove failed:\n{proc.stdout}\n{proc.stderr}"

    payload = payload_of(proc)
    assert payload["disposition"] == "remove"
    assert payload["entries_count"] == 1
    assert payload["generation_hashes_restamped"] is False

    canon = read_canon(root)
    assert CORRECTED not in canon["entries"]
    assert UNTOUCHED in canon["entries"]
    assert canon["corrections"] == [doc]
    assert stamp_of(root) == stamp_before

    run_segpack_fr(root)
    after = used_terms_hashes(root)
    moved = sorted(seg for seg in SEGS if before[seg] != after[seg])
    assert moved == ["seg01"], f"expected only seg01 to re-stale, got {moved}"


# ---------------------------------------------------------------------------
# 6. The recollapse asymmetry
# ---------------------------------------------------------------------------


def test_split_form_refuses_correct_but_allows_remove(tmp_path):
    """The asymmetry is the whole point, and getting it backwards in either
    direction is a defect.

    `correct` must refuse: substituting one bare entry for another is still ONE
    bare entry for a form with two referents -- the same recollapse
    `_merge_batch` refuses, through the same `is_split` predicate.

    `remove` must be ALLOWED: `canon_adjudication_audit.py`'s `collapsed_split`
    is BLOCKING and "never satisfied by an adjudication record -- the underlying
    canon.json entry must actually be corrected". No substituted value can
    satisfy it, so refusing removal here would leave that finding with no route
    at all -- which is the very shape of defect #495 exists to close."""
    root = make_french_project(tmp_path)
    write_split_sidecar(root, CORRECTED)
    before = canon_bytes(root)

    refused = run_correct(root, write_correction(root, correction_doc()))
    assert_refused(refused, root, before, "adjudicated homonym split", "2 senses")

    allowed = run_correct(
        root,
        write_correction(
            root,
            correction_doc(disposition="remove", reason="collapsed split; clearing the bare entry"),
            name="removal.json",
        ),
    )
    assert allowed.returncode == 0, (
        f"--correct remove was refused on a split form -- that leaves "
        f"collapsed_split with no repair route:\n{allowed.stdout}\n{allowed.stderr}"
    )
    assert CORRECTED not in read_canon(root)["entries"]


# ---------------------------------------------------------------------------
# 7. The rest of the refusal battery
# ---------------------------------------------------------------------------


def test_absent_source_form_is_refused_rather_than_inserted(tmp_path):
    """A correction never inserts blind -- otherwise it would be a second,
    unvalidated route into entries{} beside the glossary merge."""
    root = make_french_project(tmp_path)
    before = canon_bytes(root)

    doc = correction_doc(source_form="Marius Pontmercy")
    doc["new_entry"] = _entry("Marius Pontmercy", "Marius Pontmercy")
    doc["old_entry"] = _entry("Marius Pontmercy", "Marius")
    proc = run_correct(root, write_correction(root, doc))

    assert_refused(proc, root, before, "Marius Pontmercy", "--merge-batches")


def test_identical_new_entry_is_refused(tmp_path):
    """The merge path treats an identical resubmission as a silent no-op. A
    correction must not inherit that: a mis-authored one would report success
    having changed nothing, and the operator would believe the entry was
    repaired."""
    root = make_french_project(tmp_path)
    before = canon_bytes(root)

    doc = correction_doc(new_entry=_entry(CORRECTED, "Jean Valjean"))
    proc = run_correct(root, write_correction(root, doc))

    assert_refused(proc, root, before, "identical to old_entry", "nothing to correct")


def test_new_entry_source_form_must_equal_the_map_key(tmp_path):
    """canon_adjudication_audit.py reads each entry RECORD's own authoritative
    `source_form` field, not the entries{} map key, precisely because a record
    filed under an unrelated key is a defect it must still catch. A correction
    must not be able to create one."""
    root = make_french_project(tmp_path)
    before = canon_bytes(root)

    doc = correction_doc(new_entry=_entry("Jean Valljean", "Jean Valljean"))
    proc = run_correct(root, write_correction(root, doc))

    assert_refused(proc, root, before, "map key", "--merge-batches")


def test_schema_invalid_correction_document_is_refused(tmp_path):
    """`reason` is required and must be non-blank: a correction whose rationale
    is absent is exactly the unexplained diff #495 is about."""
    root = make_french_project(tmp_path)
    before = canon_bytes(root)

    doc = correction_doc(reason="   ")
    proc = run_correct(root, write_correction(root, doc))

    assert_refused(proc, root, before, "schema validation")


def test_remove_may_not_carry_a_new_entry(tmp_path):
    """The two dispositions are exclusive by schema, not by convention -- a
    `remove` that quietly carried a replacement would be a `correct` wearing the
    disposition that skips the recollapse guard."""
    root = make_french_project(tmp_path)
    before = canon_bytes(root)

    doc = correction_doc(disposition="remove")
    doc["new_entry"] = _entry(CORRECTED, "Jean Valljean")
    proc = run_correct(root, write_correction(root, doc))

    assert_refused(proc, root, before, "schema validation")


def test_correction_file_must_be_a_json_object(tmp_path):
    root = make_french_project(tmp_path)
    before = canon_bytes(root)

    proc = run_correct(root, write_correction(root, [correction_doc()]))

    assert_refused(proc, root, before, "does not contain a JSON object")


def test_absent_canon_is_refused_and_points_at_init(tmp_path):
    root = make_french_project(tmp_path, merge=False)
    (root / "canon.json").unlink()

    proc = run_correct(root, write_correction(root, correction_doc()))
    assert proc.returncode == 1, proc.stdout
    payload = payload_of(proc)
    assert "canon.json not found" in payload["error"]
    assert "--init" in payload["error"]
    assert not (root / "canon.json").exists(), "a refused correction created canon.json"


def test_unpreservable_stamp_is_refused_and_points_at_restamp(tmp_path):
    """A correction carries the existing stamp forward verbatim, so it needs one
    to carry. Stamping a fresh value instead would be exactly the silent
    provenance advance `preserve_stamp` exists to prevent -- so this refuses and
    names the one mode that MAY advance it, deliberately and by name."""
    root = make_french_project(tmp_path)
    canon = read_canon(root)
    canon.pop("generation_hashes")
    (root / "canon.json").write_text(json.dumps(canon, ensure_ascii=False), encoding="utf-8")
    before = canon_bytes(root)

    proc = run_correct(root, write_correction(root, correction_doc()))

    assert_refused(proc, root, before, "no trustworthy generation_hashes", "--restamp-derivation")


def test_correct_needs_neither_plugin_root_nor_allow_durable_sibling(tmp_path):
    """--correct WRITES canon.json but does not STAMP it, so it resolves no
    sibling cache_key.py and #412's trusted-sibling precondition has nothing to
    guard. Pinned because "writing" and "stamping" named the same four modes
    until this one, and the guard is a comprehension over MODE_SPECS'
    `stamps_generation_hashes` column -- a future row that sets it True by
    reflex would start demanding a flag this mode has no use for."""
    root = make_french_project(tmp_path)
    proc = run_correct(root, write_correction(root, correction_doc()))
    assert proc.returncode == 0, (
        f"--correct demanded a #412 flag it has no sibling to resolve:\n"
        f"{proc.stdout}\n{proc.stderr}"
    )


@pytest.mark.parametrize("flag", ["--expect-source-forms-file", "--approve-to"])
def test_correct_refuses_the_fragment_only_flags(tmp_path, flag):
    """Both refusals come from MODE_SPECS rows, not hand-written per-flag
    checks. Silently accepting either would report a coverage or snapshot
    guarantee this mode never provides -- it reads a correction DOCUMENT, never
    a batch fragment."""
    root = make_french_project(tmp_path)
    path = write_correction(root, correction_doc())
    proc = run_canon_validate(
        root, "--correct", str(path), flag, str(root / "unused.json"),
        allow_durable_sibling=False,
    )
    assert proc.returncode == 2, f"expected an argparse refusal:\n{proc.stdout}\n{proc.stderr}"
    assert "--correct does not accept" in proc.stderr, proc.stderr


# ---------------------------------------------------------------------------
# 7b. The content controls a SECOND write path must not skip
# ---------------------------------------------------------------------------


def test_correct_refuses_an_unsafe_citation_source_exactly_as_the_merge_path_does(tmp_path):
    """#347's `_enforce_citation_source_safety` calls itself "the only place
    such a `source` can be stopped before it is frozen into canon.json". A
    second route into entries{} that skipped it would make that claim false.

    Measured before the guard existed: a `source` of "http://127.0.0.1:8080/x"
    is refused by --check-batch AND --merge-batches as `loopback-address`, and
    was ACCEPTED by --correct, landing in entries{}. Asserted here against BOTH
    paths in one test, so the two can never drift into disagreeing about the
    same URL -- which is the only way this hole could quietly reopen."""
    root = make_french_project(tmp_path)
    before = canon_bytes(root)
    unsafe = _entry(
        CORRECTED, "Jean Valljean", basis="established", source="http://127.0.0.1:8080/x"
    )

    corrected = run_correct(
        root, write_correction(root, correction_doc(new_entry=unsafe)), research_mode="live"
    )
    assert_refused(corrected, root, before, "unsafe citation source", "loopback-address")

    # The merge path's verdict on the SAME url, as the comparison that makes
    # "exactly as the merge path does" a measurement rather than a claim.
    fragment = write_fragment(
        root,
        [dict(_accepted("Marius Pontmercy", "Marius Pontmercy"),
              basis="established", source="http://127.0.0.1:8080/x")],
        name="unsafe_frag.json",
    )
    merged = run_canon_validate(root, "--merge-batches", str(fragment), research_mode="live")
    assert merged.returncode == 1, merged.stdout
    assert "loopback-address" in payload_of(merged)["error"], payload_of(merged)["error"]


def test_correct_refuses_established_basis_under_offline_research_mode(tmp_path):
    """The offline backstop forbids basis:"established" for every NEW entry,
    because an established rendering is a claim about the world that offline
    research cannot have checked. A correction freezes a new entry, so it is
    subject to the same rule -- and the operator's escape is to declare
    --research-mode live, which is exactly the declaration that flag exists to
    force."""
    root = make_french_project(tmp_path)
    before = canon_bytes(root)
    established = _entry(
        CORRECTED, "Jean Valljean", basis="established", source="https://example.org/valjean"
    )

    offline = run_correct(
        root, write_correction(root, correction_doc(new_entry=established)),
        research_mode="offline",
    )
    assert_refused(offline, root, before, "offline", "established")

    # Same document, honest declaration -> allowed. Without this leg the test
    # above would also pass if --correct refused basis:"established" outright.
    live = run_correct(
        root,
        write_correction(root, correction_doc(new_entry=established), name="live.json"),
        research_mode="live",
    )
    assert live.returncode == 0, (
        f"--correct refuses a cited established rendering even under "
        f"--research-mode live:\n{live.stdout}\n{live.stderr}"
    )


ESTABLISHED = _entry(
    CORRECTED, "Jean Valjean", basis="established", source="https://example.org/valjean"
)


def freeze_established(root: Path) -> dict:
    canon = read_canon(root)
    canon["entries"][CORRECTED] = ESTABLISHED
    (root / "canon.json").write_text(json.dumps(canon, ensure_ascii=False), encoding="utf-8")
    return ESTABLISHED


@pytest.mark.parametrize(
    "field,value",
    [("confidence", "medium"), ("note", "register checked against the 1892 edition"),
     ("category", "person")],
    ids=["confidence", "note", "category"],
)
def test_offline_correction_of_a_non_claim_field_is_allowed(tmp_path, field, value):
    """The backstop is scoped to the DELTA, and this is the case that forced
    it. An established entry asserts a `canonical_target_form` and cites a
    `source`; correcting anything ELSE on the row restates no claim about the
    world, so it must stay legal offline.

    Measured, not assumed: 488 of the 999 frozen entries across the four live
    books are basis:"established". An unscoped call would have put half the
    corpus out of reach of the very mode built to repair it."""
    root = make_french_project(tmp_path)
    established = freeze_established(root)

    doc = correction_doc(
        old_entry=established,
        new_entry=dict(established, **{field: value}),
        reason=f"correcting {field}; the established claim itself is untouched",
    )
    proc = run_correct(root, write_correction(root, doc), research_mode="offline")
    assert proc.returncode == 0, (
        f"the offline backstop fired on a correction that restated no claim:\n"
        f"{proc.stdout}\n{proc.stderr}"
    )
    assert read_canon(root)["entries"][CORRECTED][field] == value


@pytest.mark.parametrize(
    "changed",
    [{"canonical_target_form": "Entirely Different"},
     {"source": "https://example.org/unrelated"},
     {"canonical_target_form": "Entirely Different", "source": "https://example.org/unrelated"}],
    ids=["target-form", "source", "both"],
)
def test_offline_correction_that_RESTATES_the_established_claim_is_refused(tmp_path, changed):
    """The correction to the delta scoping, found on PR review and reproduced
    before fixing: keying the exemption on `basis` alone let a correction
    replace the target form AND its citation offline and keep the exemption
    purely because the OLD row also said "established".

    An established entry's claim is the (canonical_target_form, source) pair —
    "this rendering is the conventional one, and here is the citation" — not the
    basis label. Restating either half is a new claim about the world, and
    offline research cannot have checked it. The operator's move is the same
    command with `--research-mode live`, which is exactly the declaration that
    flag exists to force."""
    root = make_french_project(tmp_path)
    established = freeze_established(root)
    before = canon_bytes(root)

    doc = correction_doc(
        old_entry=established,
        new_entry=dict(established, **changed),
        reason="restating the established claim",
    )
    refused = run_correct(root, write_correction(root, doc), research_mode="offline")
    assert_refused(refused, root, before, "offline", "established")

    live = run_correct(
        root,
        write_correction(root, doc, name="live.json"),
        research_mode="live",
    )
    assert live.returncode == 0, (
        f"the same correction is refused even under --research-mode live, so the "
        f"refusal above is not about the declaration:\n{live.stdout}\n{live.stderr}"
    )


def test_offline_correction_may_still_DEMOTE_an_uncitable_established_entry(tmp_path):
    """The counterexample the backstop must NOT catch, and #495's own third
    case: an entry frozen as basis:"established" with no citable source is
    repaired by demoting it to "transliterated". The backstop reads the NEW
    basis, so this stays legal offline -- if it read the OLD one, the repair
    would need a live research run to fix a defect that needs no research at
    all."""
    root = make_french_project(tmp_path)
    canon = read_canon(root)
    canon["entries"][CORRECTED] = _entry(
        CORRECTED, "Jean Valjean", basis="established", source="https://example.org/valjean"
    )
    (root / "canon.json").write_text(json.dumps(canon, ensure_ascii=False), encoding="utf-8")

    doc = correction_doc(
        old_entry=canon["entries"][CORRECTED],
        new_entry=_entry(CORRECTED, "Jean Valjean"),
        reason="no citable source backs the established claim; demoted",
    )
    proc = run_correct(root, write_correction(root, doc), research_mode="offline")
    assert proc.returncode == 0, (
        f"the offline backstop caught a DEMOTION away from established -- it "
        f"must read the new basis, not the old:\n{proc.stdout}\n{proc.stderr}"
    )
    assert read_canon(root)["entries"][CORRECTED]["basis"] == "transliterated"


def test_remove_is_exempt_from_both_content_controls(tmp_path):
    """Both controls constrain what may be FROZEN, and a removal freezes
    nothing. Applying them to `remove` would trap exactly the record most worth
    deleting: an entry whose own frozen `source` is unsafe could never be taken
    out, under any research mode."""
    root = make_french_project(tmp_path)
    canon = read_canon(root)
    canon["entries"][CORRECTED] = _entry(
        CORRECTED, "Jean Valjean", basis="established", source="http://127.0.0.1:8080/x"
    )
    (root / "canon.json").write_text(json.dumps(canon, ensure_ascii=False), encoding="utf-8")

    doc = correction_doc(
        disposition="remove",
        old_entry=canon["entries"][CORRECTED],
        reason="entry cites an unreachable private address; removing it",
    )
    proc = run_correct(root, write_correction(root, doc), research_mode="offline")
    assert proc.returncode == 0, (
        f"--correct remove was refused over the OUTGOING entry's own content:\n"
        f"{proc.stdout}\n{proc.stderr}"
    )
    assert CORRECTED not in read_canon(root)["entries"]


# ---------------------------------------------------------------------------
# 8. The case old_entry is deliberately not $ref-validated for
# ---------------------------------------------------------------------------


def test_correction_can_repair_an_entry_that_fails_its_own_schema(tmp_path):
    """`old_entry` is typed as a plain object, not a $ref to
    canon-entry.schema.json, and this is the case that decides it.

    An entry can be schema-INVALID on disk: canon_validate.py refuses to write
    one, but the hand-edit that was #495's only repair route before this mode
    can produce one, and validate-only exists to find it. Requiring the OLD
    value to validate would leave precisely those entries -- the ones most in
    need of correcting -- with no route at all. `new_entry` IS $ref-validated,
    because it is what gets frozen."""
    root = make_french_project(tmp_path)
    canon = read_canon(root)
    # basis:"established" requires a `source` URI; this record has none, so it
    # fails canon-entry.schema.json's own conditional.
    broken = {
        "source_form": CORRECTED,
        "is_proper_name": True,
        "canonical_target_form": "Jean Valjean",
        "basis": "established",
        "confidence": "high",
    }
    canon["entries"][CORRECTED] = broken
    (root / "canon.json").write_text(json.dumps(canon, ensure_ascii=False), encoding="utf-8")

    unhealthy = run_canon_validate(root)
    assert unhealthy.returncode == 1, (
        "the fixture's 'broken' entry validates after all -- this test would "
        "then prove nothing about repairing an invalid one"
    )

    doc = correction_doc(
        old_entry=broken,
        new_entry=_entry(CORRECTED, "Jean Valjean"),
        reason="basis was established with no citable source; demoted to transliterated",
    )
    proc = run_correct(root, write_correction(root, doc))
    assert proc.returncode == 0, (
        f"--correct cannot repair a schema-invalid entry:\n{proc.stdout}\n{proc.stderr}"
    )

    healthy = run_canon_validate(root)
    assert healthy.returncode == 0, (
        f"canon.json still fails validate-only after the repair:\n{healthy.stdout}"
    )


@pytest.mark.parametrize(
    "broken", ["not-an-object", ["a", "list"], None, 17], ids=["str", "list", "null", "int"]
)
def test_a_primitive_valued_entries_row_can_be_removed(tmp_path, broken):
    """The conclusion of the `old_entry` design call, and the case that made it
    a bug rather than a preference (PR review, ped-ant P2).

    `_load_canon` type-checks `entries` itself but never its VALUES, so a hand
    edit -- the very repair route this mode replaces -- can leave any JSON value
    under an entries{} key. `--validate-only` reports it (exit 1). While
    `old_entry` was `type: "object"`, the correction document naming that value
    was refused by its own schema BEFORE the equality interlock could run, so
    the row could be diagnosed and not repaired, and the operator was sent back
    to the hand edit. `old_entry` is therefore unconstrained."""
    root = make_french_project(tmp_path)
    canon = read_canon(root)
    canon["entries"]["broken"] = broken
    (root / "canon.json").write_text(json.dumps(canon, ensure_ascii=False), encoding="utf-8")

    assert run_canon_validate(root).returncode == 1, (
        "the malformed row does not fail validate-only, so this fixture is not "
        "the state the repair route exists for"
    )

    doc = {
        "source_form": "broken",
        "disposition": "remove",
        "old_entry": broken,
        "reason": "row is not a canon entry at all; removing it",
    }
    proc = run_correct(root, write_correction(root, doc))
    assert proc.returncode == 0, (
        f"a malformed entries{{}} row cannot be removed:\n{proc.stdout}\n{proc.stderr}"
    )
    assert "broken" not in read_canon(root)["entries"]
    assert run_canon_validate(root).returncode == 0, "canon.json is still unhealthy after the repair"


def test_a_primitive_valued_entries_row_can_be_replaced_offline(tmp_path):
    """The `correct` half of the same case, and the one that reaches the
    delta-scoped backstop with a non-mapping `old_entry`. Reading `basis` off it
    naively would raise AttributeError, so the repair would fail as an
    "unexpected error" rather than succeed."""
    root = make_french_project(tmp_path)
    canon = read_canon(root)
    canon["entries"][CORRECTED] = "not-an-object"
    (root / "canon.json").write_text(json.dumps(canon, ensure_ascii=False), encoding="utf-8")

    doc = correction_doc(
        old_entry="not-an-object",
        new_entry=_entry(CORRECTED, "Jean Valjean"),
        reason="row was corrupted to a bare string; restoring the real entry",
    )
    proc = run_correct(root, write_correction(root, doc), research_mode="offline")
    assert proc.returncode == 0, (
        f"a malformed entries{{}} row cannot be replaced:\n{proc.stdout}\n{proc.stderr}"
    )
    assert read_canon(root)["entries"][CORRECTED] == doc["new_entry"]
    assert run_canon_validate(root).returncode == 0


def test_a_primitive_old_entry_still_faces_the_interlock(tmp_path):
    """Unconstraining `old_entry` must not weaken what it is FOR. A correction
    naming the wrong primitive is still refused, naming both values."""
    root = make_french_project(tmp_path)
    canon = read_canon(root)
    canon["entries"]["broken"] = "not-an-object"
    (root / "canon.json").write_text(json.dumps(canon, ensure_ascii=False), encoding="utf-8")
    before = canon_bytes(root)

    doc = {
        "source_form": "broken",
        "disposition": "remove",
        "old_entry": "a-different-string",
        "reason": "stale read",
    }
    proc = run_correct(root, write_correction(root, doc))
    assert_refused(proc, root, before, "a-different-string", "not-an-object")


@pytest.mark.parametrize(
    "on_disk,stated",
    [(True, 1), (False, 0), (1, True), (0, False), (1, 1.0)],
    ids=["true-vs-1", "false-vs-0", "1-vs-true", "0-vs-false", "int-vs-float"],
)
def test_the_interlock_is_type_exact_not_python_equal(tmp_path, on_disk, stated):
    """A consequence of unconstraining `old_entry`, found on PR review.

    Python's `==` collapses the boolean/number boundary (`True == 1`), so a
    correction could state `1` for an on-disk `true`, pass the interlock, and be
    RECORDED with the wrong value. The record is this mode's whole deliverable —
    a record that misstates what was on disk defeats it even when the edit
    itself was the one the operator wanted. Reproduced exactly that way before
    the fix: rc=0, row removed, `corrections[0].old_entry == 1`.

    `1` vs `1.0` rides along because it is the same class of collapse and the
    canonical-JSON comparison settles it for free."""
    root = make_french_project(tmp_path)
    canon = read_canon(root)
    canon["entries"]["broken"] = on_disk
    (root / "canon.json").write_text(json.dumps(canon, ensure_ascii=False), encoding="utf-8")
    before = canon_bytes(root)

    doc = {
        "source_form": "broken",
        "disposition": "remove",
        "old_entry": stated,
        "reason": "stating a Python-equal but JSON-different value",
    }
    proc = run_correct(root, write_correction(root, doc))
    assert_refused(proc, root, before, "refusing to correct blind")


def test_the_type_exact_comparison_still_admits_a_reordered_object(tmp_path):
    """The other side of that fix: object key ORDER is not part of a JSON
    value, so a correction whose `old_entry` states the same fields in a
    different order must still be admitted. Comparing serialized text WITHOUT
    `sort_keys` would have broken every ordinary correction — the one authored
    by hand rather than copy-pasted out of the file."""
    root = make_french_project(tmp_path)
    frozen = read_canon(root)["entries"][CORRECTED]
    reordered = dict(reversed(list(frozen.items())))
    assert list(reordered) != list(frozen), "fixture did not actually reorder the keys"

    doc = correction_doc(old_entry=reordered)
    proc = run_correct(root, write_correction(root, doc))
    assert proc.returncode == 0, (
        f"a key-reordered old_entry was refused -- key order is not part of the "
        f"value:\n{proc.stdout}\n{proc.stderr}"
    )


def test_more_than_one_malformed_row_blocks_every_writing_mode_alike(tmp_path):
    """CHARACTERIZATION, not a supported repair path — pinned so nobody reads
    `--correct` as a general canon.json repair tool (PR review raised it as a
    deadlock; this test records what the boundary actually is).

    `_stamp_write_verify` Pass-2-validates the whole document BEFORE touching
    disk. So with TWO malformed rows, repairing one still leaves a file that
    fails whole-file validation, and the write is refused. That reads like a
    deadlock in `--correct` specifically, and it is not: this test drives
    `--merge-batches` and `--restamp-derivation` over the SAME file and asserts
    they fail the same way, on the same row. The behaviour belongs to the shared
    write path, predates this mode, and is the gate that stops a corrupt
    document from being written at all.

    What `--correct` DOES repair is one malformed row in an otherwise valid file
    — covered above, and the reachable case. A file with several is file
    corruption rather than a wrong adjudication, which is a different problem
    with a different owner; measured population in the four live books is 0
    malformed rows out of 999 entries. Widening the write path's validation for
    one mode would weaken the single gate every mode depends on, to serve that
    zero."""
    root = make_french_project(tmp_path)
    canon = read_canon(root)
    canon["entries"][CORRECTED] = "broken-one"
    canon["entries"][UNTOUCHED] = ["broken-two"]
    (root / "canon.json").write_text(json.dumps(canon, ensure_ascii=False), encoding="utf-8")
    before = canon_bytes(root)

    doc = {
        "source_form": CORRECTED,
        "disposition": "remove",
        "old_entry": "broken-one",
        "reason": "removing the first malformed row",
    }
    corrected = run_correct(root, write_correction(root, doc))
    assert_refused(corrected, root, before, UNTOUCHED, "is not of type 'object'")

    # The load-bearing half: the SAME file, the SAME row, through the other two
    # writing modes. Without this, the assertion above would read as a defect
    # in --correct rather than as the shared write path's contract.
    fragment = write_fragment(root, [_accepted("Marius Pontmercy", "Marius")], name="mm.json")
    merged = run_canon_validate(root, "--merge-batches", str(fragment))
    assert merged.returncode == 1, merged.stdout
    assert UNTOUCHED in payload_of(merged)["error"], payload_of(merged)["error"]

    restamped = run_canon_validate(root, "--restamp-derivation")
    assert restamped.returncode == 1, restamped.stdout
    assert UNTOUCHED in payload_of(restamped)["error"], payload_of(restamped)["error"]

    assert canon_bytes(root) == before, "a refused write still modified canon.json"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
