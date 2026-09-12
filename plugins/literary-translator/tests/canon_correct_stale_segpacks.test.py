"""tests/canon_correct_stale_segpacks.test.py -- coverage for issue #910:
`canon_validate.py --correct` must, in its own success output, name the
segpacks whose frozen `canon_map` no longer agrees with the canon file the
correction just wrote.

WHY THIS EXISTS. Today `--correct` reports `{"success": true, ...}` and
nothing on disk or in any gate tells an operator that a segpack built at W3a
still carries the pre-correction target. Measured live: four corrected
targets, ten segments dispatched afterwards, 23 superseded spans printed
into the drafts. The two freshness mechanisms that DO exist
(`used_terms_hash`, the derivation-state gate) are both silent for a
`not_started` segment -- the ordinary path the reporter's segments took --
because neither is evaluated on that path. See this issue's plan (revision
5) for the full argument; this file is written against its FROZEN payload
contract, not against any particular implementation.

THIS IS ITS OWN FILE, never folded into `canon_correct_entry.test.py`'s
inventory: `senses_fixture_guard.test.py` matches at FILE level on any
`shutil.copy2`/`spec_from_file_location` site sitting beside a
canon_senses-consumer literal, and a fresh file here keeps that guard from
ever having reason to look at it.

THE FROZEN SHAPE, in one paragraph. Every `--correct` success payload,
whatever the disposition, carries `segpacks_scanned` (int),
`segpacks_current` (int), `stale_segpacks` (`[{"seg", "names"}, ...]`,
sorted by seg), `segpacks_unevaluated` (`[{"seg", "error"}, ...]`, sorted by
seg) and `note` (str, always present). `scan_error` (str) is present ONLY
when the scan could not run at all. The bucket balance
`len(stale_segpacks) + len(segpacks_unevaluated) + segpacks_current ==
segpacks_scanned` holds on every payload that reports a scan. Exit code is
always 0 once the correction itself succeeded -- the scan can degrade what
it reports, never the correction's own outcome.

Every test drives the REAL `segpack.py` and `canon_validate.py` as
subprocesses through `_canon_project_fixture`'s `make_project()` /
`run_segpack()` -- never a hand-built segpack dict -- because the whole
point of the check under test is that the shipped PRODUCER and this
CHECKER agree; a hand-built pack could not prove that. Where a test needs a
corrupted or hostile pack, it first builds a real one via `segpack.py --all`
and then mutates the file on disk afterwards.

RED UNTIL THE IMPLEMENTATION LANDS: as of this write, `canon_validate.py`'s
`--correct` payload carries none of `segpacks_scanned` / `segpacks_current`
/ `stale_segpacks` / `segpacks_unevaluated` / `note`, so every test below
that reads one of those keys is expected to fail against today's tree, with
the missing/None value showing up in the printed payload the assertion
message includes.

Covered (numbering follows the issue's plan, section D5):
   1. RED before GREEN: a corrected entry's pre-existing pack is named,
      with the corrected source_form among its reported names.
   2. The vacuity guard: a SAME-TARGET correction (confidence/note/category
      only) on a pack that is genuinely current must NOT be flagged.
   3. Correcting an entry a pack never referenced leaves that pack unlisted.
   4. `disposition:"remove"` is reported exactly like `disposition:"correct"`.
   5. `dismiss`, both directions: a clean dismissal changes nothing, and a
      LATER, unrelated dismissal still names a pack an EARLIER correction
      staled (the report describes current state, not this call's own
      delta).
   6. No `segments/` directory -> N=0, never `scan_error`.
   7. A syntactically corrupt segpack.json is unevaluated, not fatal.
   8. A JSON-valid pack carrying a lone UTF-16 surrogate in `names[]` and as
      a `canon_map` key must not crash the report or corrupt stdout.
   9. `SystemExit` escaping the scan's module load must not fail the
      correction (G1); a merely-missing sibling script (`FileNotFoundError`)
      is the necessary-but-insufficient sibling case, kept alongside it.
  10. Complex, real seg-id shapes (`FRONTBACK:errata_02`,
      `seg05_blocked_regen`) are reported under their EXACT id.
  11. An orphaned `canon_map` key (a name deleted from `names[]` but left in
      `canon_map`) reads as CURRENT -- a disclosed boundary, not a bug:
      `segpack.py` cannot write such a pack, and W3a already fatals on it.
  12. `--canon-path` pointing at a file other than `${durable_root}/canon.json`
      makes no claim about the packs at all, and says why.
"""
import json
import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))
from _canon_project_fixture import (  # noqa: E402
    make_project,
    queued_item,
    read_canon,
    run_canon_init,
    run_canon_validate,
    run_segpack,
    write_fragment,
)

FRENCH_CONFIG = "fr.json"

# Multiword, capitalized, mid-sentence -- clears segpack.py's strong_names
# filter the same way canon_correct_entry.test.py's fixture text does (this
# is the identical text, reused deliberately: it is already proven to
# produce a real candidate under fr.json).
NAME_A = "Jean Valjean"
TEXT_A = "Le matin, Jean Valjean quitta la ville sans rien dire à personne."
NAME_B = "Cosette Fauchelevent"
TEXT_B = "Plus tard, Cosette Fauchelevent revint seule vers la maison basse."


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


def _manifest(segments) -> dict:
    """`segments`: a list of (seg_id, text) pairs, one paragraph block each."""
    return {
        "segments": [
            {
                "seg": seg,
                "title_text": f"Chapitre {i + 1}",
                "kind": "body",
                "word_count": 12,
                "block_ids": [f"p{i}"],
            }
            for i, (seg, _text) in enumerate(segments)
        ],
        "blocks": {
            f"p{i}": {"id": f"p{i}", "seg": seg, "order_index": 0, "plain_text": text}
            for i, (seg, text) in enumerate(segments)
        },
        "footnotes": [],
        "verse": {"store": []},
        "generation_hashes": {
            "source_extraction_hash": "a" * 40,
            "source_input_hash": "b" * 40,
        },
    }


def build_project(tmp_path, segments) -> Path:
    """A Step-0a-shaped, `--init`-bootstrapped durable_root re-pointed at
    `fr.json`, holding exactly the given (seg_id, text) segments. Real
    `--init` gives canon.json a genuine cache_key.py-computed stamp, which
    `--correct`'s `preserve_stamp` path needs a trustworthy prior to carry
    forward."""
    root = make_project(tmp_path, particle_config=FRENCH_CONFIG, manifest=_manifest(segments))
    init = run_canon_init(root)
    assert init.returncode == 0, f"--init failed:\n{init.stdout}\n{init.stderr}"
    return root


def seed_entries(root: Path, entries: dict) -> None:
    """Hand-freezes canon.json's entries{} after --init, the same
    read-mutate-write convention canon_correct_entry.test.py's malformed-row
    fixtures and canon_dismiss_queued.test.py's seed_canon() both use --
    generation_hashes (the real stamp) is left untouched."""
    canon = read_canon(root)
    canon["entries"] = entries
    (root / "canon.json").write_text(json.dumps(canon, ensure_ascii=False), encoding="utf-8")


def seed_review_queue(root: Path, rows) -> None:
    canon = read_canon(root)
    canon["review_queue"] = rows
    (root / "canon.json").write_text(json.dumps(canon, ensure_ascii=False), encoding="utf-8")


def build_packs(root: Path) -> None:
    proc = run_segpack(root, particle_config=FRENCH_CONFIG)
    assert proc.returncode == 0, f"segpack.py --all failed:\n{proc.stdout}\n{proc.stderr}"


def read_segpack(root: Path, seg: str) -> dict:
    return json.loads((root / "segments" / f"segpack_{seg}.json").read_text(encoding="utf-8"))


def write_segpack(root: Path, seg: str, doc: dict, **write_text_kwargs) -> None:
    (root / "segments" / f"segpack_{seg}.json").write_text(
        json.dumps(doc, ensure_ascii=False), encoding="utf-8", **write_text_kwargs
    )


def correction_doc(source_form: str, disposition: str = "correct", **overrides) -> dict:
    doc = {
        "source_form": source_form,
        "disposition": disposition,
        "reason": "test-authored correction for issue #910's stale-segpack report",
    }
    doc.update(overrides)
    return doc


def write_correction(root: Path, doc, name="correction.json") -> Path:
    return write_fragment(root, doc, name=name)


def run_correct(root: Path, correction_path: Path, canon_path: Path = None, research_mode="offline"):
    # allow_durable_sibling=False on purpose (mirrors canon_correct_entry.
    # test.py's own run_correct): --correct is a NON-stamping mode and must
    # not need either #412 flag.
    args = ["--correct", str(correction_path)]
    if canon_path is not None:
        args += ["--canon-path", str(canon_path)]
    return run_canon_validate(root, *args, research_mode=research_mode, allow_durable_sibling=False)


def payload_of(proc) -> dict:
    """Parses the single stdout JSON line, per the frozen contract's own
    instruction to the test author. Asserting there is exactly one
    non-blank line (rather than a bare `json.loads(proc.stdout)`) turns a
    scan that accidentally prints a SECOND line into a named test failure
    instead of a `json.JSONDecodeError` pointing nowhere useful."""
    lines = [line for line in proc.stdout.splitlines() if line.strip()]
    assert len(lines) == 1, (
        f"expected exactly one JSON line on stdout, got {len(lines)}:\n"
        f"{proc.stdout!r}\nstderr:\n{proc.stderr}"
    )
    return json.loads(lines[0])


def bucket_balance_holds(payload: dict) -> bool:
    return (
        len(payload.get("stale_segpacks", []))
        + len(payload.get("segpacks_unevaluated", []))
        + payload.get("segpacks_current", 0)
    ) == payload.get("segpacks_scanned", 0)


# ---------------------------------------------------------------------------
# D5.1 -- RED before GREEN
# ---------------------------------------------------------------------------


def test_correction_reports_the_pack_it_leaves_stale(tmp_path):
    """A pack built before the correction still carries the pre-correction
    target; the payload must name it, with the corrected source_form among
    its reported names."""
    root = build_project(tmp_path, [("seg01", TEXT_A)])
    seed_entries(root, {NAME_A: _entry(NAME_A, NAME_A)})
    build_packs(root)

    doc = correction_doc(
        NAME_A, old_entry=_entry(NAME_A, NAME_A), new_entry=_entry(NAME_A, "Jean Valljean")
    )
    proc = run_correct(root, write_correction(root, doc))
    assert proc.returncode == 0, f"--correct failed:\n{proc.stdout}\n{proc.stderr}"

    payload = payload_of(proc)
    assert payload.get("segpacks_scanned") == 1, payload
    assert payload.get("segpacks_current") == 0, payload
    assert payload.get("segpacks_unevaluated") == [], payload
    stale = payload.get("stale_segpacks")
    assert stale and [item["seg"] for item in stale] == ["seg01"], payload
    assert NAME_A in stale[0]["names"], payload
    note = payload.get("note", "")
    assert "segpack.py --all" in note, ("the note must name the remedy: " + repr(payload))
    # The changelog claims the note disclaims a validity verdict. Assert the
    # SENTENCE, not merely that a note exists: a non-empty check would pass
    # for a note reading "all packs are valid", which is the exact claim this
    # report must never make.
    assert "not a validity check on the segpacks" in note, (
        "the stale note must disclaim a validity verdict: " + repr(payload)
    )


# ---------------------------------------------------------------------------
# D5.2 -- the vacuity guard
# ---------------------------------------------------------------------------


def test_same_target_correction_is_not_flagged_stale(tmp_path):
    """A pack that is genuinely CURRENT for the corrected name must not be
    flagged just because it carries that name. The interlock compares the
    whole new_entry against old_entry, so a same-target correction
    (confidence-only, here) is legal -- canon_correct_entry.test.py:767-771
    already proves that. Without this test, an implementation that flags
    every pack containing the corrected source_form (rather than one whose
    canon_map target actually disagrees) would pass every other test here
    and cause endless needless `segpack.py --all` reruns."""
    root = build_project(tmp_path, [("seg01", TEXT_A)])
    seed_entries(root, {NAME_A: _entry(NAME_A, NAME_A)})
    build_packs(root)

    first_new = _entry(NAME_A, "Jean Valljean")
    first = correction_doc(NAME_A, old_entry=_entry(NAME_A, NAME_A), new_entry=first_new)
    proc1 = run_correct(root, write_correction(root, first, name="c1.json"))
    assert proc1.returncode == 0, f"--correct failed:\n{proc1.stdout}\n{proc1.stderr}"

    # Rebuild -- the pack now agrees with the corrected target.
    build_packs(root)

    same_target = correction_doc(
        NAME_A, old_entry=first_new, new_entry=dict(first_new, confidence="medium")
    )
    proc2 = run_correct(root, write_correction(root, same_target, name="c2.json"))
    assert proc2.returncode == 0, f"--correct failed:\n{proc2.stdout}\n{proc2.stderr}"

    payload = payload_of(proc2)
    assert payload.get("segpacks_scanned") == 1, payload
    assert payload.get("stale_segpacks") == [], payload
    assert payload.get("segpacks_current") == 1, payload


# ---------------------------------------------------------------------------
# D5.3 -- an entry the pack never referenced
# ---------------------------------------------------------------------------


def test_correcting_an_entry_absent_from_a_pack_leaves_it_unlisted(tmp_path):
    """Two entries are frozen; the one pack in this project references only
    the FIRST. Correcting the SECOND (which the pack carries neither the old
    nor the new target for) must not list that pack."""
    root = build_project(tmp_path, [("seg01", TEXT_A)])
    seed_entries(
        root,
        {NAME_A: _entry(NAME_A, NAME_A), NAME_B: _entry(NAME_B, NAME_B)},
    )
    build_packs(root)
    pack = read_segpack(root, "seg01")
    assert NAME_B not in pack.get("names", []), (
        "fixture premise: this project's one pack must not carry NAME_B, "
        f"else this test cannot distinguish 'absent from the pack' from "
        f"'present but unaffected': {pack}"
    )

    doc = correction_doc(
        NAME_B, old_entry=_entry(NAME_B, NAME_B), new_entry=_entry(NAME_B, "Cosette Fauchelevaunt")
    )
    proc = run_correct(root, write_correction(root, doc))
    assert proc.returncode == 0, f"--correct failed:\n{proc.stdout}\n{proc.stderr}"

    payload = payload_of(proc)
    assert payload.get("segpacks_scanned") == 1, payload
    assert payload.get("stale_segpacks") == [], payload


# ---------------------------------------------------------------------------
# D5.4 -- disposition:"remove"
# ---------------------------------------------------------------------------


def test_remove_disposition_reports_the_pack_it_leaves_stale(tmp_path):
    """Removing a frozen entry a pack references makes that pack's stored
    target disagree with the (now absent) current canon target -- exactly
    as a `correct` would, just with a target of None on the canon side."""
    root = build_project(tmp_path, [("seg01", TEXT_A)])
    seed_entries(root, {NAME_A: _entry(NAME_A, NAME_A)})
    build_packs(root)

    doc = correction_doc(NAME_A, disposition="remove", old_entry=_entry(NAME_A, NAME_A))
    proc = run_correct(root, write_correction(root, doc))
    assert proc.returncode == 0, f"--correct remove failed:\n{proc.stdout}\n{proc.stderr}"

    payload = payload_of(proc)
    stale = payload.get("stale_segpacks")
    assert stale and [item["seg"] for item in stale] == ["seg01"], payload
    assert NAME_A in stale[0]["names"], payload


# ---------------------------------------------------------------------------
# D5.5 -- dismiss, both directions
# ---------------------------------------------------------------------------


def test_dismiss_on_a_fresh_tree_leaves_no_stale_pack(tmp_path):
    """D5.5(a). A dismissal drops a review_queue[] row and touches no
    canon_map target; on an otherwise-current tree it must report the pack
    current, not stale."""
    root = build_project(tmp_path, [("seg01", TEXT_A)])
    seed_entries(root, {NAME_A: _entry(NAME_A, NAME_A)})
    build_packs(root)

    queued = queued_item("Marius Pontmercy")
    seed_review_queue(root, [queued])
    dismiss = {
        "source_form": "Marius Pontmercy",
        "disposition": "dismiss",
        "old_item": queued,
        "reason": "common noun, mis-flagged by the detector",
    }
    proc = run_correct(root, write_correction(root, dismiss))
    assert proc.returncode == 0, f"--correct dismiss failed:\n{proc.stdout}\n{proc.stderr}"

    payload = payload_of(proc)
    assert payload.get("segpacks_scanned") == 1, payload
    assert payload.get("stale_segpacks") == [], payload


def test_dismiss_still_reports_a_pack_staled_by_an_earlier_correction(tmp_path):
    """D5.5(b). The report describes the state the correction LEAVES
    BEHIND, not what THIS call changed. A dismissal that introduces no
    canon_map mismatch of its own must still name a pack an EARLIER,
    unrelated correction staled -- otherwise an operator reading only the
    dismissal's output is told a stale pack is current."""
    root = build_project(tmp_path, [("seg01", TEXT_A)])
    seed_entries(root, {NAME_A: _entry(NAME_A, NAME_A)})
    build_packs(root)

    correct = correction_doc(
        NAME_A, old_entry=_entry(NAME_A, NAME_A), new_entry=_entry(NAME_A, "Jean Valljean")
    )
    proc_correct = run_correct(root, write_correction(root, correct, name="correct.json"))
    assert proc_correct.returncode == 0, proc_correct.stdout
    # The pack is deliberately NOT rebuilt -- it is now stale.
    correct_payload = payload_of(proc_correct)
    assert [item["seg"] for item in correct_payload.get("stale_segpacks", [])] == ["seg01"], (
        f"fixture premise: the correction above must already stale seg01: {correct_payload}"
    )

    queued = queued_item("Fantine")
    seed_review_queue(root, [queued])
    dismiss = {
        "source_form": "Fantine",
        "disposition": "dismiss",
        "old_item": queued,
        "reason": "duplicate spelling of an already-queued form",
    }
    proc_dismiss = run_correct(root, write_correction(root, dismiss, name="dismiss.json"))
    assert proc_dismiss.returncode == 0, f"--correct dismiss failed:\n{proc_dismiss.stdout}\n{proc_dismiss.stderr}"

    payload = payload_of(proc_dismiss)
    stale = payload.get("stale_segpacks")
    assert stale and [item["seg"] for item in stale] == ["seg01"], (
        f"a dismissal's own payload must still name a pack staled by an "
        f"EARLIER correction: {payload}"
    )


# ---------------------------------------------------------------------------
# D5.6 -- no segments/ directory
# ---------------------------------------------------------------------------


def test_missing_segments_directory_is_zero_not_scan_error(tmp_path):
    """No `segments/` directory means N=0 -- this is a normal, un-failed
    scan (nothing to look at yet, e.g. before the first `segpack.py --all`
    run), never `scan_error`."""
    root = build_project(tmp_path, [("seg01", TEXT_A)])
    seed_entries(root, {NAME_A: _entry(NAME_A, NAME_A)})
    assert not (root / "segments").exists(), "fixture premise: segpack.py must never have run"

    doc = correction_doc(
        NAME_A, old_entry=_entry(NAME_A, NAME_A), new_entry=_entry(NAME_A, "Jean Valljean")
    )
    proc = run_correct(root, write_correction(root, doc))
    assert proc.returncode == 0, f"--correct failed:\n{proc.stdout}\n{proc.stderr}"

    payload = payload_of(proc)
    assert payload.get("segpacks_scanned") == 0, payload
    assert payload.get("stale_segpacks") == [], payload
    assert payload.get("segpacks_unevaluated") == [], payload
    assert "scan_error" not in payload, (
        f"a merely-absent segments/ directory is not a scan failure: {payload}"
    )
    assert payload.get("note"), payload


# ---------------------------------------------------------------------------
# D5.7 -- a syntactically corrupt segpack
# ---------------------------------------------------------------------------


def test_corrupt_segpack_json_is_unevaluated_not_fatal(tmp_path):
    """A per-pack read/parse failure must land in segpacks_unevaluated
    without aborting the sweep or the correction. This is the case D5.8
    (the lone surrogate) deliberately does NOT reach -- this pack never
    parses at all, so it never gets near the serializer."""
    root = build_project(tmp_path, [("seg01", TEXT_A)])
    seed_entries(root, {NAME_A: _entry(NAME_A, NAME_A)})
    build_packs(root)
    (root / "segments" / "segpack_seg01.json").write_text("{not valid json", encoding="utf-8")

    doc = correction_doc(
        NAME_A, old_entry=_entry(NAME_A, NAME_A), new_entry=_entry(NAME_A, "Jean Valljean")
    )
    proc = run_correct(root, write_correction(root, doc))
    assert proc.returncode == 0, (
        f"a corrupt segpack must not fail the correction:\n{proc.stdout}\n{proc.stderr}"
    )

    payload = payload_of(proc)
    assert payload.get("segpacks_scanned") == 1, payload
    unevaluated = payload.get("segpacks_unevaluated")
    assert unevaluated and [item["seg"] for item in unevaluated] == ["seg01"], payload
    assert unevaluated[0].get("error"), payload
    assert payload.get("stale_segpacks") == [], payload
    assert bucket_balance_holds(payload), payload

    assert read_canon(root)["entries"][NAME_A]["canonical_target_form"] == "Jean Valljean", (
        "the correction itself must still be on disk despite the corrupt pack"
    )


# ---------------------------------------------------------------------------
# D5.8 -- the lone surrogate
# ---------------------------------------------------------------------------


def test_lone_surrogate_in_pack_names_does_not_break_the_report(tmp_path):
    """A JSON-VALID pack (json.loads accepts an escaped lone surrogate) can
    still carry `"\\ud800"` in names[] and as a canon_map key. Serializing
    the report with plain json.dumps(..., ensure_ascii=False) and then
    .encode("utf-8") raises UnicodeEncodeError on exactly this input -- G2
    exists to catch that BEFORE it reaches stdout. Asserts the correction
    still succeeds and the printed line is genuinely decodable UTF-8."""
    root = build_project(tmp_path, [("seg01", TEXT_A)])
    seed_entries(root, {NAME_A: _entry(NAME_A, NAME_A)})
    build_packs(root)

    pack = read_segpack(root, "seg01")
    surrogate = "\ud800"
    pack["names"] = list(pack.get("names", [])) + [surrogate]
    canon_map = dict(pack.get("canon_map", {}))
    canon_map[surrogate] = "Some Target"
    pack["canon_map"] = canon_map
    # ensure_ascii=True IS THE POINT, and getting this wrong makes the test
    # vacuous. Written with ensure_ascii=False the surrogate goes to disk as
    # the raw bytes ED A0 80, which are not valid UTF-8 at all: the pack then
    # fails at READ time inside select_segments.py's own reader and is
    # classified unevaluated, so the hostile string never reaches the
    # serializer and G2 is never exercised. Written as the ASCII escape the
    # file is valid UTF-8, json.loads hands back the lone surrogate IN MEMORY,
    # it flows into the report, and dumps_line(...).encode("utf-8") is what
    # raises -- which is the path this test exists for.
    raw = json.dumps(pack, ensure_ascii=True)
    assert b"\\ud800" in raw.encode("utf-8"), "fixture premise: the escape, not raw bytes"
    assert json.loads(raw)["names"][-1] == surrogate, "fixture premise: pack must stay JSON-valid"
    (root / "segments" / "segpack_seg01.json").write_text(raw, encoding="utf-8")

    doc = correction_doc(
        NAME_A, old_entry=_entry(NAME_A, NAME_A), new_entry=_entry(NAME_A, "Jean Valljean")
    )
    proc = run_correct(root, write_correction(root, doc))
    assert proc.returncode == 0, (
        f"a lone surrogate in a pack must not crash the report:\n{proc.stdout!r}\n{proc.stderr}"
    )
    # The printed line must be genuinely decodable UTF-8 -- this is the
    # assertion the corrupt-JSON test (D5.7) never reaches, since that pack
    # never parses far enough to put a hostile string in the payload.
    proc.stdout.encode("utf-8")

    payload = payload_of(proc)
    # G2 fires: the fragment cannot be serialised, so the whole scan degrades
    # to the scan_error shape rather than printing a half-encodable line. The
    # counts are zeroed and the bucket balance holds trivially.
    assert "scan_error" in payload, payload
    assert payload.get("segpacks_scanned") == 0, payload
    assert payload.get("segpacks_current") == 0, payload
    assert payload.get("stale_segpacks") == [], payload
    assert payload.get("segpacks_unevaluated") == [], payload
    assert "segpack.py --all" in payload.get("note", ""), payload
    assert read_canon(root)["entries"][NAME_A]["canonical_target_form"] == "Jean Valljean", (
        "the correction must still be on disk"
    )


# ---------------------------------------------------------------------------
# D5.9 -- SystemExit during the scan's module load
# ---------------------------------------------------------------------------


def test_scan_module_load_failure_does_not_fail_the_correction(tmp_path):
    """G1's whole point: the scan's module load runs inside a boundary that
    catches BOTH Exception and SystemExit. Two escalating cases:

    (a) select_segments.py deleted outright -> FileNotFoundError, which a
    plain `except Exception` already catches. Necessary, but NOT sufficient
    alone -- it passes against an implementation that still lets SystemExit
    escape.

    (b) select_segments.py present but its MODULE BODY raises SystemExit --
    mirroring select_segments.py's own real json_stdout.py loader, which
    calls sys.exit() when ITS sibling is missing
    (select_segments.py:316-322). Only this case actually discriminates:
    `except Exception` alone
    does NOT catch SystemExit, since SystemExit is a BaseException sibling,
    not an Exception subclass.
    """
    root = build_project(tmp_path, [("seg01", TEXT_A)])
    seed_entries(root, {NAME_A: _entry(NAME_A, NAME_A)})
    build_packs(root)
    select_segments_path = root / "scripts" / "select_segments.py"

    # (a) necessary but insufficient: outright deletion.
    select_segments_path.unlink()
    doc1 = correction_doc(
        NAME_A, old_entry=_entry(NAME_A, NAME_A), new_entry=_entry(NAME_A, "Jean Valljean")
    )
    proc_missing = run_correct(root, write_correction(root, doc1, name="missing.json"))
    assert proc_missing.returncode == 0, (
        f"a missing select_segments.py must not fail the correction:\n"
        f"{proc_missing.stdout}\n{proc_missing.stderr}"
    )
    payload_missing = payload_of(proc_missing)
    assert "scan_error" in payload_missing, payload_missing
    assert payload_missing.get("segpacks_scanned") == 0, payload_missing

    # (b) the discriminating case: present, but its module body exits.
    select_segments_path.write_text(
        "raise SystemExit('fixture: staged select_segments.py refuses to load')\n",
        encoding="utf-8",
    )
    doc2 = correction_doc(
        NAME_A, old_entry=doc1["new_entry"], new_entry=dict(doc1["new_entry"], confidence="medium")
    )
    proc_exit = run_correct(root, write_correction(root, doc2, name="systemexit.json"))
    assert proc_exit.returncode == 0, (
        f"SystemExit escaping the scan's module load must not fail the "
        f"correction:\n{proc_exit.stdout}\n{proc_exit.stderr}"
    )
    payload_exit = payload_of(proc_exit)
    assert "scan_error" in payload_exit, payload_exit
    assert payload_exit.get("segpacks_scanned") == 0, payload_exit
    assert payload_exit.get("stale_segpacks") == [], payload_exit
    assert payload_exit.get("segpacks_unevaluated") == [], payload_exit

    entry = read_canon(root)["entries"][NAME_A]
    assert entry["canonical_target_form"] == "Jean Valljean"
    assert entry["confidence"] == "medium", "both corrections must still be on disk"


# ---------------------------------------------------------------------------
# D5.10 -- complex, real seg-id shapes
# ---------------------------------------------------------------------------


def test_complex_seg_ids_are_reported_verbatim(tmp_path):
    """An id-recovery that splits the filename on '_' would corrupt
    'seg05_blocked_regen' and would not even round-trip
    'FRONTBACK:errata_02' -- both are real, shipped id shapes
    (segpack.py's own id contract: (FRONTBACK:)?[A-Za-z0-9_]+). The seg id
    must be recovered by literal affix slicing (removing the constant
    'segpack_' prefix and '.json' suffix and nothing else) and reported
    exactly."""
    root = build_project(
        tmp_path, [("FRONTBACK:errata_02", TEXT_A), ("seg05_blocked_regen", TEXT_A)]
    )
    seed_entries(root, {NAME_A: _entry(NAME_A, NAME_A)})
    build_packs(root)

    doc = correction_doc(
        NAME_A, old_entry=_entry(NAME_A, NAME_A), new_entry=_entry(NAME_A, "Jean Valljean")
    )
    proc = run_correct(root, write_correction(root, doc))
    assert proc.returncode == 0, f"--correct failed:\n{proc.stdout}\n{proc.stderr}"

    payload = payload_of(proc)
    assert payload.get("segpacks_scanned") == 2, payload
    reported = sorted(item["seg"] for item in payload.get("stale_segpacks", []))
    assert reported == sorted(["FRONTBACK:errata_02", "seg05_blocked_regen"]), payload


# ---------------------------------------------------------------------------
# D5.11 -- the orphaned canon_map key (disclosed boundary)
# ---------------------------------------------------------------------------


def test_orphaned_canon_map_key_reads_as_current_a_disclosed_boundary(tmp_path):
    """PINNED as a DISCLOSED BOUNDARY, not a bug to fix. `segpack.py` itself
    can never write a pack whose canon_map key is missing from names[]:
    build_pack() derives canon_names/canon_map by iterating strong_names and
    writes that SAME list as names, so canon_map is always a subset of
    names for anything the real producer can emit -- and W3a's own
    preflight fatals on a hand-corrupted pack that violates this. The
    predicate (evaluate_fresh_segpack_precondition) walks the pack's OWN
    names[] partition, so a canon_map key that names[] no longer carries is
    never visited, and the pack reads as current.

    Pinning this here stops a later round re-deriving the same argument and
    re-growing the machinery revision 4 of this issue's plan already tried
    and removed (a second validate_segpack module load) to chase a state
    the real producer cannot create."""
    root = build_project(tmp_path, [("seg01", TEXT_A)])
    seed_entries(root, {NAME_A: _entry(NAME_A, NAME_A)})
    build_packs(root)

    pack = read_segpack(root, "seg01")
    assert NAME_A in pack["names"], (
        f"fixture premise: the real pack must carry NAME_A before it is "
        f"hand-orphaned: {pack}"
    )
    pack["names"] = [name for name in pack["names"] if name != NAME_A]
    write_segpack(root, "seg01", pack)

    doc = correction_doc(
        NAME_A, old_entry=_entry(NAME_A, NAME_A), new_entry=_entry(NAME_A, "Jean Valljean")
    )
    proc = run_correct(root, write_correction(root, doc))
    assert proc.returncode == 0, f"--correct failed:\n{proc.stdout}\n{proc.stderr}"

    payload = payload_of(proc)
    assert payload.get("stale_segpacks") == [], (
        f"this is the accepted boundary: an orphaned canon_map key that "
        f"names[] no longer carries is invisible to the check: {payload}"
    )
    assert payload.get("segpacks_unevaluated") == [], payload
    assert payload.get("segpacks_current") == 1, payload
    assert payload.get("segpacks_scanned") == 1, payload
    assert bucket_balance_holds(payload), payload
    assert "not a validity check on the segpacks" in payload.get("note", ""), (
        "the note must name the scope of the check, so a reader does not "
        f"take 'current' as a validity claim on the pack: {payload}"
    )


# ---------------------------------------------------------------------------
# D5.12 -- --canon-path pointing elsewhere
# ---------------------------------------------------------------------------


def test_canon_path_override_makes_no_claim_about_the_packs(tmp_path):
    """segpack.py hard-codes canon_path = DURABLE_ROOT / "canon.json" and
    segments_dir = DURABLE_ROOT / "segments", so a report computed against
    a DIFFERENT corrected file would name packs `segpack.py --all` cannot
    repair, and miss packs genuinely stale against the real canon.json.
    When --canon-path points elsewhere, the scan must not run, and the note
    must name BOTH paths and say why. Asserting a *stale* or *current*
    verdict here would pin exactly the wrong behaviour."""
    root = build_project(tmp_path, [("seg01", TEXT_A)])
    seed_entries(root, {NAME_A: _entry(NAME_A, NAME_A)})
    build_packs(root)

    candidate_path = root / "candidate.json"
    candidate_path.write_text((root / "canon.json").read_text(encoding="utf-8"), encoding="utf-8")

    doc = correction_doc(
        NAME_A, old_entry=_entry(NAME_A, NAME_A), new_entry=_entry(NAME_A, "Jean Valljean")
    )
    proc = run_correct(root, write_correction(root, doc), canon_path=candidate_path)
    assert proc.returncode == 0, f"--correct failed:\n{proc.stdout}\n{proc.stderr}"

    payload = payload_of(proc)
    assert payload.get("segpacks_scanned") == 0, payload
    assert payload.get("stale_segpacks") == [], payload
    assert payload.get("segpacks_unevaluated") == [], payload
    assert "scan_error" not in payload, payload
    note = payload.get("note", "")
    assert str(candidate_path) in note or candidate_path.name in note, note
    assert "canon.json" in note, note

    # root/canon.json itself is untouched; only the override path moved.
    assert read_canon(root)["entries"][NAME_A]["canonical_target_form"] == NAME_A
    candidate_canon = json.loads(candidate_path.read_text(encoding="utf-8"))
    assert candidate_canon["entries"][NAME_A]["canonical_target_form"] == "Jean Valljean"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
