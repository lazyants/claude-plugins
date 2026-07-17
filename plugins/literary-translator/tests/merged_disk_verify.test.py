"""tests/merged_disk_verify.test.py -- regression coverage for
scripts/canon_validate.py's `--verify-merged` mode, per
CONTRACT-1.2.0-reliability.md §3 (issue #88: glossary "banks the codex
agent() return with no disk verification -> false-green").

`--verify-merged --batch f1 --batch f2 ... --research-mode X
[--expect-source-forms-file M.json]` is deliberately DISK-INDEPENDENT: it
fresh-reads canon.json plus each named fragment file directly and compares
them -- it never replays `_merge_batch`'s accumulator/threading logic, so
fragment argument order must never change the verdict. Per fragment item,
by disposition:

    accepted      -> canon["entries"][sf] == _entry_from_accepted_item(item)
    review_queue  -> the exact queued object is in canon["review_queue"]
                     OR its source_form is already a key in canon["entries"]
                     (accept-supersedes -- do NOT report missing)

This file locks down:

  1. Present-correct passes (`verified: true`, with `missing` either absent
     or empty -- CONTRACT §5's JS guard explicitly tolerates both shapes,
     and the landed script always emits `missing: []` even on success).
  2. Four distinct fail-RED shapes: a dropped field on an accepted entry, a
     divergent/clobbered entry, a candidate the fragment claims but canon.json
     has nowhere at all, and a genuinely STALE queued object (same
     source_form, different queued content, not superseded by an accept).
  3. THE #88 regression itself: a fragment's QUEUED claim for a source_form
     that canon.json has SINCE resolved into entries{} (e.g. a later
     human/codex adjudication) must PASS, not fail-RED -- proven in BOTH
     `--batch` argument orders, since this mode is disk-independent.
  4. `--expect-source-forms-file` union coverage across multiple `--batch`
     fragments, using multiword + apostrophe candidate names (manifests are
     files, never argv, per CONTRACT §2).
  5. The script's own stdout is exactly the CANON_VERIFY_SCHEMA relay shape
     (`additionalProperties:false`, `required:["verified"]`,
     `properties:{verified:boolean, missing:array<string>}`) -- never a key
     outside `{"verified","missing"}`, in either the pass or fail direction.
  6. `--research-mode` is required syntactically (uniform precondition
     declaration, CONTRACT §3) but its VALUE is never read by this
     disk-independent mode -- proven by getting an identical verdict for an
     established-basis item under both "live" and "offline" (no offline
     backstop ever fires here, unlike `--check-batch`/`--merge-batches`).

Follows this plugin's established self-anchoring test convention
(`canon_format_validation.test.py`'s own `make_durable_root` pattern): every
test copies the REAL `canon_validate.py` and the REAL
`assets/schemas/canon-{entry,batch,file}.schema.json` files into an isolated
`tmp_path` fixture root and invokes the script exactly as production does.
No `cache_key.py` stub is needed here (unlike `glossary_fragment_merge.test.py`)
-- `--verify-merged` never writes canon.json and never stamps
generation_hashes.

STATUS (owner-build coordination): at the time this file was written,
`--verify-merged` does not exist yet on `scripts/canon_validate.py` -- every
test below is written to CONTRACT-1.2.0-reliability.md §3's documented
shape, not to what is currently on disk. Tests fail with an argparse
"unrecognized arguments" error until Owner C lands the flag; that is
expected, not a bug in this file.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))
from _senses_fixture import stage_consumer  # noqa: E402

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
ASSETS_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets"
SCRIPT_SRC = ASSETS_DIR / "scripts" / "canon_validate.py"
SCHEMAS_SRC = ASSETS_DIR / "schemas"

CANON_SCHEMA_FILES = (
    "canon-entry.schema.json",
    "canon-batch.schema.json",
    "canon-file.schema.json",
)

assert SCRIPT_SRC.is_file(), f"canon_validate.py not found at {SCRIPT_SRC}"
for _name in CANON_SCHEMA_FILES:
    assert (SCHEMAS_SRC / _name).is_file(), f"{_name} not found under {SCHEMAS_SRC}"


# ---------------------------------------------------------------------------
# Fixture harness
# ---------------------------------------------------------------------------


def make_durable_root(tmp_path):
    """Builds an isolated durable_root: copies the REAL canon_validate.py
    into {root}/scripts/ and the REAL three canon-*.schema.json files into
    {root}/schemas/ -- no cache_key.py stub, since --verify-merged never
    shells out to it (disk-independent read+compare only, no hashing).
    Also stages the REAL canon_senses.py (canon_validate.py's sibling
    import, RFC #215 1d) and canon-senses.schema.json via the sanctioned
    tests/_senses_fixture.py helper, so `from canon_senses import ...`
    resolves inside this isolated fixture too."""
    root = tmp_path / "durable_root"
    stage_consumer(root, "canon_validate.py")

    schemas_dir = root / "schemas"
    for name in CANON_SCHEMA_FILES:
        shutil.copy2(SCHEMAS_SRC / name, schemas_dir / name)

    return root


def write_canon(root, doc):
    canon_path = root / "canon.json"
    canon_path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return canon_path


def write_fragment(root, items, name="fragment.json"):
    frag_path = root / name
    frag_path.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
    return frag_path


def write_manifest(root, source_forms, name="manifest.json"):
    manifest_path = root / name
    manifest_path.write_text(
        json.dumps(list(source_forms), ensure_ascii=False), encoding="utf-8"
    )
    return manifest_path


def run_verify_merged(root, research_mode, fragment_paths, manifest_path=None, timeout=30):
    cmd = [
        sys.executable,
        str(root / "scripts" / "canon_validate.py"),
        "--research-mode",
        research_mode,
        "--verify-merged",
    ]
    for p in fragment_paths:
        cmd += ["--batch", str(p)]
    if manifest_path is not None:
        cmd += ["--expect-source-forms-file", str(manifest_path)]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=str(root))


def parse_stdout(proc):
    assert proc.stdout.strip(), (
        f"expected one JSON line on stdout, got none. stderr:\n{proc.stderr}"
    )
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    assert len(lines) == 1, f"expected exactly one JSON line, got:\n{proc.stdout}"
    return json.loads(lines[0])


def canon_entry(
    source_form,
    canonical_target_form="Placeholder Target",
    basis="transliterated",
    confidence="high",
    source=None,
    is_proper_name=True,
    note=None,
    category=None,
):
    """A canon-entry.schema.json-shaped ACCEPTED entry (entries{} value)."""
    entry = {
        "source_form": source_form,
        "is_proper_name": is_proper_name,
        "canonical_target_form": canonical_target_form,
        "basis": basis,
        "confidence": confidence,
    }
    if source is not None:
        entry["source"] = source
    if note is not None:
        entry["note"] = note
    if category is not None:
        entry["category"] = category
    return entry


def accepted_batch_item(source_form, **kwargs):
    """A canon-batch.schema.json ACCEPTED-branch item -- canon_entry() plus
    the 'disposition' discriminator."""
    item = canon_entry(source_form, **kwargs)
    item["disposition"] = "accepted"
    return item


def queued_batch_item(source_form, note="needs review", is_proper_name=True, **extra):
    """A canon-batch.schema.json QUEUED-branch item."""
    item = {
        "source_form": source_form,
        "is_proper_name": is_proper_name,
        "disposition": "review_queue",
        "note": note,
    }
    item.update(extra)
    return item


def entry_for_accepted(item):
    """Projects a fragment ACCEPTED item to the exact shape canon.json's
    entries{} stores it as: every field except 'disposition'. This mirrors
    canon_validate.py's own `_entry_from_accepted_item` (strip
    'disposition', keep the declared canon-entry field set) WITHOUT
    reimplementing its logic -- it is a plain fixture-construction helper
    (an accepted item's fields ARE exactly the canon-entry field set plus
    'disposition'; nothing here re-derives a decision the SUT makes)."""
    return {k: v for k, v in item.items() if k != "disposition"}


def assert_verify_passed(payload):
    """CANON_VERIFY_SCHEMA's own JS guard (CONTRACT §5: 'verify only if:
    verified===true AND (missing absent OR empty array)') explicitly
    tolerates BOTH 'missing' being absent AND present-but-empty on a
    passing verdict -- assert the tolerant shape (no key outside
    {verified,missing}, verified is True, missing is absent or empty),
    not one single literal rendering."""
    assert set(payload.keys()) <= {"verified", "missing"}, payload
    assert payload["verified"] is True
    assert payload.get("missing", []) == []


def canon_file_doc(
    entries: dict | None = None,
    review_queue: list | None = None,
    generation_hashes: str | dict | None = "default",
):
    doc = {
        "entries": entries if entries is not None else {},
        "review_queue": review_queue if review_queue is not None else [],
    }
    if generation_hashes == "default":
        doc["generation_hashes"] = {
            "particle_config_hash": "abc123",
            "derivation_bundle_hash": "def456",
        }
    elif generation_hashes is not None:
        doc["generation_hashes"] = generation_hashes
    return doc


VALID_URI = "https://example.org/wiki/established_name"


# ===========================================================================
# 1. Present-correct.
# ===========================================================================


def test_verify_merged_present_correct_passes(tmp_path):
    root = make_durable_root(tmp_path)
    accepted_item = accepted_batch_item(
        "Guerin", canonical_target_form="Gerin", basis="transliterated", confidence="medium"
    )
    queued_item = queued_batch_item("Provence", note="disputed rendering")
    write_canon(
        root,
        canon_file_doc(
            entries={"Guerin": entry_for_accepted(accepted_item)},
            review_queue=[queued_item],
        ),
    )
    frag = write_fragment(root, [accepted_item, queued_item])

    proc = run_verify_merged(root, "live", [frag])
    assert proc.returncode == 0, proc.stdout + proc.stderr
    payload = parse_stdout(proc)
    assert_verify_passed(payload)


# ===========================================================================
# 2. Fail-RED shapes.
# ===========================================================================


def test_verify_merged_dropped_key_fails_red(tmp_path):
    accepted_item = accepted_batch_item(
        "Guerin",
        canonical_target_form="Gerin",
        basis="transliterated",
        confidence="medium",
        note="a note the fragment carries",
    )
    on_disk_entry = entry_for_accepted(accepted_item)
    del on_disk_entry["note"]  # canon.json is missing a field the fragment claims

    root = make_durable_root(tmp_path)
    write_canon(root, canon_file_doc(entries={"Guerin": on_disk_entry}))
    frag = write_fragment(root, [accepted_item])

    proc = run_verify_merged(root, "live", [frag])
    assert proc.returncode == 1, proc.stdout + proc.stderr
    payload = parse_stdout(proc)
    assert payload["verified"] is False
    assert payload["missing"] == ["Guerin"]


def test_verify_merged_divergent_clobber_fails_red(tmp_path):
    accepted_item = accepted_batch_item(
        "Roi Soleil", canonical_target_form="Sun King", basis="transliterated", confidence="high"
    )
    clobbered_entry = entry_for_accepted(accepted_item)
    # Someone else wrote a conflicting entry between merge and verify.
    clobbered_entry["canonical_target_form"] = "The Sun King"

    root = make_durable_root(tmp_path)
    write_canon(root, canon_file_doc(entries={"Roi Soleil": clobbered_entry}))
    frag = write_fragment(root, [accepted_item])

    proc = run_verify_merged(root, "live", [frag])
    assert proc.returncode == 1, proc.stdout + proc.stderr
    payload = parse_stdout(proc)
    assert payload["verified"] is False
    assert payload["missing"] == ["Roi Soleil"]


def test_verify_merged_omitted_candidate_fails_red(tmp_path):
    root = make_durable_root(tmp_path)
    write_canon(root, canon_file_doc())  # entries AND review_queue both empty
    accepted_item = accepted_batch_item(
        "Guerin", canonical_target_form="Gerin", basis="transliterated", confidence="medium"
    )
    frag = write_fragment(root, [accepted_item])

    proc = run_verify_merged(root, "live", [frag])
    assert proc.returncode == 1, proc.stdout + proc.stderr
    payload = parse_stdout(proc)
    assert payload["verified"] is False
    assert payload["missing"] == ["Guerin"]


def test_verify_merged_stale_queued_object_not_superseded_fails_red(tmp_path):
    # canon.json's review_queue has a DIFFERENT queued object for the same
    # source_form (e.g. the note text changed) AND that source_form is NOT
    # in entries either -- a genuine staleness, distinct from the
    # accept-supersession case below, which must fail-RED.
    fragment_queued_item = queued_batch_item(
        "Provence", note="disputed rendering, needs a human call"
    )
    stale_on_disk_item = queued_batch_item("Provence", note="an OLDER, now-stale note")

    root = make_durable_root(tmp_path)
    write_canon(root, canon_file_doc(review_queue=[stale_on_disk_item]))
    frag = write_fragment(root, [fragment_queued_item])

    proc = run_verify_merged(root, "live", [frag])
    assert proc.returncode == 1, proc.stdout + proc.stderr
    payload = parse_stdout(proc)
    assert payload["verified"] is False
    assert payload["missing"] == ["Provence"]


# ===========================================================================
# 3. THE #88 regression: queued-then-accepted supersession PASSES, both
#    fragment orders (proves disk-independence).
# ===========================================================================


@pytest.mark.parametrize("order", ["queued_first", "accepted_first"])
def test_verify_merged_queued_then_accepted_supersession_passes_both_orders(tmp_path, order):
    # The fragment claims "Duc de Guise" as QUEUED, but canon.json's CURRENT
    # state has it already resolved into entries{} (accepted later, e.g. by
    # a subsequent human/codex adjudication). CONTRACT's explicit "do NOT
    # report missing" rule means this must PASS.
    superseded_queued_item = queued_batch_item("Duc de Guise", note="needs a human call")
    resolved_entry = canon_entry(
        "Duc de Guise", canonical_target_form="Duke of Guise", basis="transliterated", confidence="high"
    )
    # A second, unrelated accepted candidate -- present identically in BOTH
    # canon.json and its own fragment -- used only so there are TWO
    # fragments whose --batch argument order can be flipped, proving order
    # never changes the verdict (this mode is disk-independent).
    other_accepted_item = accepted_batch_item(
        "Guerin", canonical_target_form="Gerin", basis="transliterated", confidence="medium"
    )

    root = make_durable_root(tmp_path)
    write_canon(
        root,
        canon_file_doc(
            entries={
                "Duc de Guise": resolved_entry,
                "Guerin": entry_for_accepted(other_accepted_item),
            },
            review_queue=[],
        ),
    )
    frag_queued = write_fragment(root, [superseded_queued_item], name="frag_queued.json")
    frag_accepted = write_fragment(root, [other_accepted_item], name="frag_accepted.json")

    fragments = (
        [frag_queued, frag_accepted] if order == "queued_first" else [frag_accepted, frag_queued]
    )

    proc = run_verify_merged(root, "live", fragments)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    payload = parse_stdout(proc)
    assert payload["verified"] is True and payload.get("missing", []) == [], (
        f"order={order}: a queued claim superseded by a later accept must "
        "PASS --verify-merged (CONTRACT's 'do NOT report missing' rule), "
        "regardless of --batch argument order"
    )


# ===========================================================================
# 4. --expect-source-forms-file union coverage, multiword + apostrophe.
# ===========================================================================


def test_verify_merged_manifest_union_coverage_with_multiword_apostrophe_names(tmp_path):
    item_a = accepted_batch_item(
        "Notre-Dame de Paris", canonical_target_form="Notre-Dame Cathedral", basis="transliterated", confidence="high"
    )
    item_b = accepted_batch_item(
        "D'Artagnan", canonical_target_form="D'Artagnan", basis="transliterated", confidence="high"
    )

    root = make_durable_root(tmp_path)
    write_canon(
        root,
        canon_file_doc(
            entries={
                "Notre-Dame de Paris": entry_for_accepted(item_a),
                "D'Artagnan": entry_for_accepted(item_b),
            }
        ),
    )
    frag1 = write_fragment(root, [item_a], name="out_0.json")
    frag2 = write_fragment(root, [item_b], name="out_1.json")
    manifest = write_manifest(root, ["Notre-Dame de Paris", "D'Artagnan"], name="manifest_all.json")

    proc = run_verify_merged(root, "live", [frag1, frag2], manifest_path=manifest)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    payload = parse_stdout(proc)
    assert_verify_passed(payload)


def test_verify_merged_manifest_union_coverage_rejects_omission(tmp_path):
    item_a = accepted_batch_item(
        "Notre-Dame de Paris", canonical_target_form="Notre-Dame Cathedral", basis="transliterated", confidence="high"
    )

    root = make_durable_root(tmp_path)
    write_canon(root, canon_file_doc(entries={"Notre-Dame de Paris": entry_for_accepted(item_a)}))
    frag1 = write_fragment(root, [item_a], name="out_0.json")
    # The manifest names a SECOND candidate that no given fragment addresses.
    manifest = write_manifest(root, ["Notre-Dame de Paris", "D'Artagnan"], name="manifest_all.json")

    proc = run_verify_merged(root, "live", [frag1], manifest_path=manifest)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    payload = parse_stdout(proc)
    assert payload["verified"] is False
    assert "D'Artagnan" in payload["missing"]


# ===========================================================================
# 5. Stdout is exactly the CANON_VERIFY_SCHEMA relay shape.
# ===========================================================================


def test_verify_merged_stdout_shape_matches_canon_verify_schema_relay_exactly(tmp_path):
    """CONTRACT §1's CANON_VERIFY_SCHEMA is
    {additionalProperties:false, required:["verified"],
     properties:{verified:boolean, missing:array<string>}} -- the glossary
    template relays this script's raw stdout through that exact schema, so
    canon_validate.py's own stdout must never carry a key outside
    {"verified","missing"}, in EITHER direction (pass or fail)."""
    root = make_durable_root(tmp_path)
    accepted_item = accepted_batch_item(
        "Guerin", canonical_target_form="Gerin", basis="transliterated", confidence="medium"
    )

    # Pass case.
    write_canon(root, canon_file_doc(entries={"Guerin": entry_for_accepted(accepted_item)}))
    frag = write_fragment(root, [accepted_item])
    pass_payload = parse_stdout(run_verify_merged(root, "live", [frag]))
    assert set(pass_payload.keys()) <= {"verified", "missing"}
    assert pass_payload["verified"] is True

    # Fail case (a fresh, separate root -- an omitted candidate).
    root2 = make_durable_root(tmp_path / "second")
    write_canon(root2, canon_file_doc())
    frag2 = write_fragment(root2, [accepted_item])
    fail_payload = parse_stdout(run_verify_merged(root2, "live", [frag2]))
    assert set(fail_payload.keys()) <= {"verified", "missing"}
    assert fail_payload["verified"] is False
    assert isinstance(fail_payload["missing"], list)
    assert all(isinstance(x, str) for x in fail_payload["missing"])


# ===========================================================================
# 6. --research-mode required syntactically, value ignored semantically.
# ===========================================================================


def test_verify_merged_requires_research_mode(tmp_path):
    root = make_durable_root(tmp_path)
    accepted_item = accepted_batch_item(
        "Guerin", canonical_target_form="Gerin", basis="transliterated", confidence="medium"
    )
    write_canon(root, canon_file_doc(entries={"Guerin": entry_for_accepted(accepted_item)}))
    frag = write_fragment(root, [accepted_item])

    cmd = [
        sys.executable,
        str(root / "scripts" / "canon_validate.py"),
        "--verify-merged",
        "--batch",
        str(frag),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30, cwd=str(root))
    assert proc.returncode == 2, proc.stdout + proc.stderr


def test_verify_merged_research_mode_value_is_ignored(tmp_path):
    """--research-mode is required syntactically for EVERY mode (CONTRACT
    §3's uniform 'never defaulted' precondition-declaration discipline),
    but --verify-merged's own verdict logic never reads it (disk-
    independent fresh-read + comparison only -- unlike --check-batch/
    --merge-batches, it never runs the offline research-mode backstop).
    Proven with an established-basis item (which WOULD be fatally rejected
    by --check-batch/--merge-batches under offline) getting the identical
    'verified: true' verdict under both values."""
    accepted_item = accepted_batch_item(
        "Roi Soleil",
        canonical_target_form="Sun King",
        basis="established",
        confidence="high",
        source=VALID_URI,
    )
    root = make_durable_root(tmp_path)
    write_canon(root, canon_file_doc(entries={"Roi Soleil": entry_for_accepted(accepted_item)}))
    frag = write_fragment(root, [accepted_item])

    live_payload = parse_stdout(run_verify_merged(root, "live", [frag]))
    offline_payload = parse_stdout(run_verify_merged(root, "offline", [frag]))
    assert_verify_passed(live_payload)
    assert_verify_passed(offline_payload)


# ===========================================================================
# 7. Issue #102 hardening: --verify-merged is the Workflow's actual trusted
#    final gate, so it must run Pass 2 (`_validate_whole_file` -- whole-file
#    SCHEMA validation, e.g. canon-file.schema.json's unconditionally
#    required generation_hashes, PLUS the entries{}/review_queue[] overlap
#    invariant) against the freshly re-read canon.json itself, not just
#    compare fragments against it item-by-item.
# ===========================================================================


def test_verify_merged_rejects_source_form_present_in_both_entries_and_review_queue(tmp_path):
    # run_verify_merged calls the full `_validate_whole_file` (schema +
    # overlap invariant) against the freshly re-read canon.json. The
    # fragment here claims "Ivan" as ACCEPTED with a shape that matches
    # canon.json's entries{} value EXACTLY, so `_verify_merged_item`'s own
    # per-item check passes clean for it -- isolating that the failure
    # below comes only from `_validate_whole_file`'s overlap invariant, not
    # the pre-existing per-item comparison logic.
    accepted_item = accepted_batch_item(
        "Ivan", canonical_target_form="Ivan", basis="transliterated", confidence="high"
    )
    root = make_durable_root(tmp_path)
    write_canon(
        root,
        canon_file_doc(
            entries={"Ivan": entry_for_accepted(accepted_item)},
            review_queue=[queued_batch_item("Ivan", note="needs a human call")],
        ),
    )
    frag = write_fragment(root, [accepted_item])

    proc = run_verify_merged(root, "live", [frag])
    assert proc.returncode == 1, proc.stdout + proc.stderr
    payload = parse_stdout(proc)
    assert payload["verified"] is False
    assert any("Ivan" in m for m in payload["missing"]), payload["missing"]


def test_verify_merged_rejects_canon_json_missing_required_generation_hashes(tmp_path):
    # Codex-rescue follow-up regression (#102): before this fix,
    # run_verify_merged only ran the narrower entries{}/review_queue[]
    # overlap check, never the full canon-file.schema.json Pass 2 -- so a
    # hand-corrupted canon.json missing the unconditionally-required
    # generation_hashes field (or either of its two required sub-fields)
    # still returned {"verified": true, "missing": []} as long as every
    # fragment's own item-by-item comparison happened to pass. The fragment
    # here matches entries{} EXACTLY (same isolation technique as the test
    # above), so the only thing that can catch the missing generation_hashes
    # is the whole-file schema validation half of `_validate_whole_file`.
    accepted_item = accepted_batch_item(
        "Guerin", canonical_target_form="Gerin", basis="transliterated", confidence="medium"
    )
    root = make_durable_root(tmp_path)
    write_canon(
        root,
        canon_file_doc(
            entries={"Guerin": entry_for_accepted(accepted_item)},
            generation_hashes=None,  # canon-file.schema.json requires this key
        ),
    )
    frag = write_fragment(root, [accepted_item])

    proc = run_verify_merged(root, "live", [frag])
    assert proc.returncode == 1, proc.stdout + proc.stderr
    payload = parse_stdout(proc)
    assert payload["verified"] is False
    assert any("generation_hashes" in m for m in payload["missing"]), payload["missing"]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
