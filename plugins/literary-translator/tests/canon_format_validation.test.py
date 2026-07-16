"""tests/canon_format_validation.test.py -- format/conditional validation
tests for scripts/canon_validate.py, exercised against the REAL
canon-entry.schema.json / canon-batch.schema.json / canon-file.schema.json
files (see references/canon-and-glossary.md, "Research preflight /
offline-fallback policy for basis:'established'", and §14/§15 of the build
plan for the authoritative spec).

Three things this file locks down:

  1. canon-entry.schema.json's `basis:"established"` conditional: `source`
     becomes REQUIRED and must be a non-empty (`minLength:1`) `format:"uri"`
     string. Exercised both via VALIDATE-ONLY mode (against an existing
     canon.json's entries{}) and via MERGE mode's Pass 1 (the SAME
     conditional inlined into canon-batch.schema.json's ACCEPTED branch).

  2. canon-batch.schema.json's `disposition` discriminated union:
     `"accepted"` requires the full canon-entry shape
     (canonical_target_form/basis/confidence, established->URI conditional
     still applies); `"review_queue"` requires ONLY `note` --
     canonical_target_form/basis/source/confidence are all optional/absent.

  3. `canon_validate.py`'s merge-time offline backstop: `--research-mode
     offline` FATALLY rejects the WHOLE batch merge -- naming every
     offending item's source_form, accepted or queued alike -- if ANY item
     claims `basis:"established"`. Run in BOTH `--research-mode` directions
     against the SAME batch composition, proving the backstop fires under
     `offline` and does NOT fire under `live`.

Following this plugin's established convention for scripts that self-anchor
their durable_root via `Path(__file__).resolve().parents[1]`
(`validate_draft.test.py`'s/`ledger_merge.test.py`'s `make_durable_root`
pattern): every test copies the REAL `canon_validate.py` and the REAL
`assets/schemas/canon-{entry,batch,file}.schema.json` files into an isolated
`tmp_path` fixture root and invokes it exactly as it is invoked in
production -- `python3 {durable_root}/scripts/canon_validate.py
--research-mode <mode> [--batch PATH]` -- so its self-anchoring resolves
against the fixture, never this repo's real assets tree.

`cache_key.py` (only ever shelled out to via `--field particle_config_hash`/
`--field derivation_bundle_hash`, for stamping `generation_hashes` at merge
time) is stubbed out with a tiny fixture script that just echoes a
deterministic string per field -- the real 15-field hashing algorithm has
its own dedicated test file (`ledger_composite_key.test.py`); this keeps
this file scoped to canon_validate.py's OWN two-pass validation, disposition
routing, and offline-backstop logic.
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


# A fixture stand-in for the real cache_key.py -- canon_validate.py's MERGE
# mode only ever shells out to it as `cache_key.py --field <name>` (never
# `--seg`), so the stub only needs to support that one interface: print a
# deterministic, non-empty string per requested field.
FAKE_CACHE_KEY_PY = """#!/usr/bin/env python3
import argparse
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--field")
    parser.add_argument("--seg", default=None)
    args = parser.parse_args()
    if not args.field:
        sys.stderr.write("fake cache_key.py: test stub requires --field\\n")
        return 1
    print(f"fixture-{args.field}-hash")
    return 0


if __name__ == "__main__":
    sys.exit(main())
"""


# ---------------------------------------------------------------------------
# Fixture harness
# ---------------------------------------------------------------------------


def make_durable_root(tmp_path):
    """Builds an isolated durable_root: copies the REAL canon_validate.py
    into {root}/scripts/ (so its self-anchored SCHEMAS_DIR/DEFAULT_CANON_PATH
    resolve against THIS fixture, never this repo's real assets tree),
    installs the fake cache_key.py stub alongside it, and copies the REAL
    three canon-*.schema.json files into {root}/schemas/. Also stages the
    REAL canon_senses.py (canon_validate.py's sibling import, RFC #215 1d)
    and canon-senses.schema.json via the sanctioned tests/_senses_fixture.py
    helper, so `from canon_senses import ...` resolves inside this isolated
    fixture too.
    """
    root = tmp_path / "durable_root"
    stage_consumer(root, "canon_validate.py")
    scripts_dir = root / "scripts"
    (scripts_dir / "cache_key.py").write_text(FAKE_CACHE_KEY_PY, encoding="utf-8")

    schemas_dir = root / "schemas"
    for name in CANON_SCHEMA_FILES:
        shutil.copy2(SCHEMAS_SRC / name, schemas_dir / name)

    return root


def write_canon(root, doc):
    canon_path = root / "canon.json"
    canon_path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return canon_path


def write_batch(root, batch, name="batch.json"):
    batch_path = root / name
    batch_path.write_text(json.dumps(batch, ensure_ascii=False), encoding="utf-8")
    return batch_path


def run_canon_validate(root, research_mode, batch_path=None, canon_path=None, timeout=30):
    cmd = [
        sys.executable,
        str(root / "scripts" / "canon_validate.py"),
        "--research-mode",
        research_mode,
    ]
    if batch_path is not None:
        cmd += ["--batch", str(batch_path)]
    if canon_path is not None:
        cmd += ["--canon-path", str(canon_path)]
    return subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout, cwd=str(root)
    )


def run_canon_validate_cli(root, args, timeout=30):
    """Generic CLI invocation -- `args` is the full flag list AFTER the
    script path. run_canon_validate() above only covers the legacy
    --research-mode [--batch]/[--canon-path] shape; #138's TP-1 lifecycle
    test (below) also needs --check-batch, --merge-batches, and
    --verify-merged, which that helper does not build."""
    cmd = [sys.executable, str(root / "scripts" / "canon_validate.py")] + list(args)
    return subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout, cwd=str(root)
    )


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
    the 'disposition' discriminator MERGE mode routes on."""
    item = canon_entry(source_form, **kwargs)
    item["disposition"] = "accepted"
    return item


def queued_batch_item(source_form, note="needs review", is_proper_name=True, **extra):
    """A canon-batch.schema.json QUEUED-branch item -- only source_form,
    is_proper_name, disposition, and note are required; every other field
    (including basis) is optional and unconstrained by any conditional."""
    item = {
        "source_form": source_form,
        "is_proper_name": is_proper_name,
        "disposition": "review_queue",
        "note": note,
    }
    item.update(extra)
    return item


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
# 1. canon-entry.schema.json's basis:"established" -> source (uri, minLength
#    1) conditional, exercised in VALIDATE-ONLY mode against entries{}.
# ===========================================================================


def test_validate_only_rejects_established_entry_missing_source(tmp_path):
    root = make_durable_root(tmp_path)
    write_canon(
        root,
        canon_file_doc(
            entries={
                "Amerique": canon_entry(
                    "Amerique",
                    canonical_target_form="America",
                    basis="established",
                    confidence="high",
                )
            }
        ),
    )

    proc = run_canon_validate(root, "live")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    payload = parse_stdout(proc)
    assert payload["success"] is False
    assert "Amerique" in payload["error"]


def test_validate_only_rejects_established_entry_with_empty_source(tmp_path):
    root = make_durable_root(tmp_path)
    write_canon(
        root,
        canon_file_doc(
            entries={
                "Amerique": canon_entry(
                    "Amerique",
                    canonical_target_form="America",
                    basis="established",
                    confidence="high",
                    source="",
                )
            }
        ),
    )

    proc = run_canon_validate(root, "live")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    payload = parse_stdout(proc)
    assert payload["success"] is False
    assert "Amerique" in payload["error"]


def test_validate_only_rejects_established_entry_with_malformed_uri_source(tmp_path):
    root = make_durable_root(tmp_path)
    write_canon(
        root,
        canon_file_doc(
            entries={
                "Amerique": canon_entry(
                    "Amerique",
                    canonical_target_form="America",
                    basis="established",
                    confidence="high",
                    # No scheme -- fails jsonschema's 'uri' (RFC 3987 URI,
                    # not URI-reference) format assertion.
                    source="not-a-uri-no-scheme",
                )
            }
        ),
    )

    proc = run_canon_validate(root, "live")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    payload = parse_stdout(proc)
    assert payload["success"] is False
    assert "Amerique" in payload["error"]


def test_validate_only_accepts_established_entry_with_valid_uri_source(tmp_path):
    root = make_durable_root(tmp_path)
    write_canon(
        root,
        canon_file_doc(
            entries={
                "Amerique": canon_entry(
                    "Amerique",
                    canonical_target_form="America",
                    basis="established",
                    confidence="high",
                    source=VALID_URI,
                )
            }
        ),
    )

    proc = run_canon_validate(root, "live")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    payload = parse_stdout(proc)
    assert payload["success"] is True
    assert payload["entries_count"] == 1


def test_validate_only_accepts_non_established_entry_without_source(tmp_path):
    # The established->source conditional is basis-gated -- a
    # "transliterated" entry with no 'source' field at all must pass clean.
    root = make_durable_root(tmp_path)
    write_canon(
        root,
        canon_file_doc(
            entries={
                "Guerin": canon_entry(
                    "Guerin",
                    canonical_target_form="Gerin",
                    basis="transliterated",
                    confidence="medium",
                )
            }
        ),
    )

    proc = run_canon_validate(root, "live")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    payload = parse_stdout(proc)
    assert payload["success"] is True


# ===========================================================================
# 2. canon-batch.schema.json's disposition-discriminated conditional,
#    exercised in MERGE mode's Pass 1 (per-batch-item).
# ===========================================================================


def test_merge_rejects_accepted_item_missing_required_fields(tmp_path):
    # 'accepted' requires canonical_target_form/basis/confidence -- an item
    # with none of them must fail Pass 1 and never reach a write.
    root = make_durable_root(tmp_path)
    batch_path = write_batch(
        root,
        [{"source_form": "Rome", "is_proper_name": True, "disposition": "accepted"}],
    )

    proc = run_canon_validate(root, "live", batch_path=batch_path)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    payload = parse_stdout(proc)
    assert payload["success"] is False
    assert "Rome" in payload["error"]
    assert not (root / "canon.json").exists()


def test_merge_rejects_established_accepted_item_missing_source(tmp_path):
    # The established->URI conditional is inlined into canon-batch.schema.json
    # too -- a fresh incoming batch item must be caught the same way an
    # existing entries{} value is.
    root = make_durable_root(tmp_path)
    batch_path = write_batch(
        root,
        [
            accepted_batch_item(
                "Roi Soleil",
                canonical_target_form="Sun King",
                basis="established",
                confidence="high",
                # source omitted
            )
        ],
    )

    proc = run_canon_validate(root, "live", batch_path=batch_path)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    payload = parse_stdout(proc)
    assert payload["success"] is False
    assert "Roi Soleil" in payload["error"]
    assert not (root / "canon.json").exists()


def test_merge_accepts_accepted_item_transliterated_without_source(tmp_path):
    # 'source' is only required when basis=="established" -- a fully
    # populated 'accepted' item with basis:"transliterated" and no source
    # must merge cleanly.
    root = make_durable_root(tmp_path)
    batch_path = write_batch(
        root,
        [
            accepted_batch_item(
                "Guerin",
                canonical_target_form="Gerin",
                basis="transliterated",
                confidence="medium",
            )
        ],
    )

    proc = run_canon_validate(root, "live", batch_path=batch_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    payload = parse_stdout(proc)
    assert payload["success"] is True
    assert payload["merged_accepted"] == 1
    assert payload["merged_queued"] == 0

    on_disk = json.loads((root / "canon.json").read_text(encoding="utf-8"))
    assert "Guerin" in on_disk["entries"]
    # 'disposition' must be stripped -- entries{} values are the bare
    # canon-entry.schema.json shape, which is additionalProperties:false.
    assert "disposition" not in on_disk["entries"]["Guerin"]
    assert on_disk["entries"]["Guerin"]["basis"] == "transliterated"


def test_merge_accepts_and_persists_category_field(tmp_path):
    # Regression lock (THOROUGH build, per-work category taxonomy): 'category'
    # is an OPTIONAL, open-vocabulary string wired through THREE surfaces --
    # canon-entry.schema.json, canon-batch.schema.json's ACCEPTED branch (an
    # additionalProperties:false object that would otherwise REJECT it at
    # intake), and canon_validate.py's own CANON_ENTRY_FIELDS allow-list
    # (which otherwise STRIPS any key not listed there when merging an
    # accepted item into entries{}). A lone edit to any one of the three
    # silently breaks this round-trip -- this test proves all three agree.
    root = make_durable_root(tmp_path)
    batch_path = write_batch(
        root,
        [
            accepted_batch_item(
                "Yerushalayim",
                canonical_target_form="Jerusalem",
                basis="transliterated",
                confidence="high",
                category="place",
            )
        ],
    )

    proc = run_canon_validate(root, "live", batch_path=batch_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    payload = parse_stdout(proc)
    assert payload["success"] is True
    assert payload["merged_accepted"] == 1

    on_disk = json.loads((root / "canon.json").read_text(encoding="utf-8"))
    assert on_disk["entries"]["Yerushalayim"]["category"] == "place"
    # 'disposition' must still be stripped alongside everything else that is
    # NOT a canon-entry field -- category surviving must not mean the
    # allow-list stopped filtering altogether.
    assert "disposition" not in on_disk["entries"]["Yerushalayim"]


def test_validate_only_accepts_entry_with_category_field(tmp_path):
    # Companion VALIDATE-ONLY case: an existing entries{} value already
    # carrying 'category' (e.g. from a prior merge, or hand-edited) must
    # pass canon-entry.schema.json cleanly -- it's optional, not merely
    # merge-time-tolerated.
    root = make_durable_root(tmp_path)
    write_canon(
        root,
        canon_file_doc(
            entries={
                "Yerushalayim": canon_entry(
                    "Yerushalayim",
                    canonical_target_form="Jerusalem",
                    basis="transliterated",
                    confidence="high",
                    category="place",
                )
            }
        ),
    )

    proc = run_canon_validate(root, "live")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    payload = parse_stdout(proc)
    assert payload["success"] is True
    assert payload["entries_count"] == 1


def test_merge_rejects_review_queue_item_missing_note(tmp_path):
    root = make_durable_root(tmp_path)
    batch_path = write_batch(
        root,
        [{"source_form": "Provence", "is_proper_name": True, "disposition": "review_queue"}],
    )

    proc = run_canon_validate(root, "live", batch_path=batch_path)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    payload = parse_stdout(proc)
    assert payload["success"] is False
    assert "Provence" in payload["error"]
    assert not (root / "canon.json").exists()


def test_merge_accepts_review_queue_item_with_only_note(tmp_path):
    # 'review_queue' requires ONLY note -- canonical_target_form/basis/
    # source/confidence are all absent here and must not be required.
    root = make_durable_root(tmp_path)
    batch_path = write_batch(
        root,
        [queued_batch_item("Provence", note="disputed rendering, needs a human call")],
    )

    proc = run_canon_validate(root, "live", batch_path=batch_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    payload = parse_stdout(proc)
    assert payload["success"] is True
    assert payload["merged_accepted"] == 0
    assert payload["merged_queued"] == 1

    on_disk = json.loads((root / "canon.json").read_text(encoding="utf-8"))
    assert on_disk["entries"] == {}
    assert len(on_disk["review_queue"]) == 1
    assert on_disk["review_queue"][0]["source_form"] == "Provence"
    assert on_disk["review_queue"][0]["note"] == "disputed rendering, needs a human call"


# ===========================================================================
# 3. The merge-time offline backstop -- run in BOTH research_mode directions
#    against the same/similar batch compositions.
# ===========================================================================


def test_merge_offline_backstop_rejects_established_accepted_item(tmp_path):
    root = make_durable_root(tmp_path)
    batch_path = write_batch(
        root,
        [
            accepted_batch_item(
                "Roi Soleil",
                canonical_target_form="Sun King",
                basis="established",
                confidence="high",
                source=VALID_URI,
            )
        ],
    )

    proc = run_canon_validate(root, "offline", batch_path=batch_path)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    payload = parse_stdout(proc)
    assert payload["success"] is False
    assert "offline" in payload["error"]
    assert "Roi Soleil" in payload["error"]
    assert payload["offending"] == ["Roi Soleil"]
    # Nothing is ever written when the backstop fires.
    assert not (root / "canon.json").exists()


def test_merge_offline_backstop_fires_for_queued_item_too(tmp_path):
    # The authoritative spec's own wording is "ANY entry", not "any accepted
    # entry" -- a QUEUED item claiming basis:"established" (even though the
    # QUEUED shape never requires/validates a source for it) must ALSO
    # trigger the backstop.
    root = make_durable_root(tmp_path)
    batch_path = write_batch(
        root,
        [
            queued_batch_item(
                "Duc de Guise",
                note="SOURCE_UNAVAILABLE: no citation reachable offline",
                basis="established",
            )
        ],
    )

    proc = run_canon_validate(root, "offline", batch_path=batch_path)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    payload = parse_stdout(proc)
    assert payload["success"] is False
    assert payload["offending"] == ["Duc de Guise"]
    assert not (root / "canon.json").exists()


def test_merge_offline_backstop_names_every_offending_entry(tmp_path):
    root = make_durable_root(tmp_path)
    batch_path = write_batch(
        root,
        [
            accepted_batch_item(
                "Roi Soleil",
                canonical_target_form="Sun King",
                basis="established",
                confidence="high",
                source=VALID_URI,
            ),
            queued_batch_item(
                "Duc de Guise",
                note="SOURCE_UNAVAILABLE: no citation reachable offline",
                basis="established",
            ),
            # Not offending -- basis is not "established".
            accepted_batch_item(
                "Guerin", canonical_target_form="Gerin", basis="transliterated", confidence="medium"
            ),
        ],
    )

    proc = run_canon_validate(root, "offline", batch_path=batch_path)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    payload = parse_stdout(proc)
    assert payload["success"] is False
    assert set(payload["offending"]) == {"Roi Soleil", "Duc de Guise"}
    assert "Guerin" not in payload["offending"]
    assert not (root / "canon.json").exists()


def test_merge_live_mode_allows_established_item_through_backstop(tmp_path):
    # The SAME item that test_merge_offline_backstop_rejects_established_
    # accepted_item rejects under offline must merge cleanly under live --
    # proving the backstop is genuinely conditional on research_mode, not a
    # blanket rejection of basis:"established".
    root = make_durable_root(tmp_path)
    batch_path = write_batch(
        root,
        [
            accepted_batch_item(
                "Roi Soleil",
                canonical_target_form="Sun King",
                basis="established",
                confidence="high",
                source=VALID_URI,
            )
        ],
    )

    proc = run_canon_validate(root, "live", batch_path=batch_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    payload = parse_stdout(proc)
    assert payload["success"] is True
    assert payload["merged_accepted"] == 1

    on_disk = json.loads((root / "canon.json").read_text(encoding="utf-8"))
    assert on_disk["entries"]["Roi Soleil"]["basis"] == "established"
    assert on_disk["entries"]["Roi Soleil"]["source"] == VALID_URI
    # generation_hashes was stamped via the (fake) cache_key.py -- proves
    # the merge actually ran to completion, not just past the backstop.
    assert on_disk["generation_hashes"]["particle_config_hash"] == "fixture-particle_config_hash-hash"
    assert on_disk["generation_hashes"]["derivation_bundle_hash"] == "fixture-derivation_bundle_hash-hash"


def test_merge_offline_mode_allows_non_established_items_through(tmp_path):
    # Offline mode must not be an overzealous blanket rejection -- a batch
    # with no basis:"established" claims at all merges cleanly under offline
    # exactly as it would under live.
    root = make_durable_root(tmp_path)
    batch_path = write_batch(
        root,
        [
            accepted_batch_item(
                "Guerin", canonical_target_form="Gerin", basis="transliterated", confidence="medium"
            ),
            queued_batch_item("Provence", note="disputed, needs a human call"),
        ],
    )

    proc = run_canon_validate(root, "offline", batch_path=batch_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    payload = parse_stdout(proc)
    assert payload["success"] is True
    assert payload["merged_accepted"] == 1
    assert payload["merged_queued"] == 1


# ===========================================================================
# 4. canon-file.schema.json's whole-file Pass 2 -- generation_hashes'
#    presence is unconditionally required, not just per-item shape.
# ===========================================================================


def test_validate_only_missing_canon_json_is_a_hard_error(tmp_path):
    root = make_durable_root(tmp_path)
    # canon.json deliberately never written.

    proc = run_canon_validate(root, "live")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    payload = parse_stdout(proc)
    assert payload["success"] is False
    assert "canon.json" in payload["error"]


def test_validate_only_rejects_canon_file_missing_generation_hashes(tmp_path):
    root = make_durable_root(tmp_path)
    write_canon(root, canon_file_doc(generation_hashes=None))

    proc = run_canon_validate(root, "live")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    payload = parse_stdout(proc)
    assert payload["success"] is False
    assert "generation_hashes" in payload["error"]


def test_validate_only_rejects_canon_file_with_partial_generation_hashes(tmp_path):
    root = make_durable_root(tmp_path)
    write_canon(
        root,
        canon_file_doc(generation_hashes={"particle_config_hash": "abc123"}),
    )

    proc = run_canon_validate(root, "live")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    payload = parse_stdout(proc)
    assert payload["success"] is False
    assert "derivation_bundle_hash" in payload["error"]


def test_validate_only_accepts_a_fully_well_formed_canon_file(tmp_path):
    # Sanity check for the harness itself: a fully valid canon.json (one
    # established entry with a real URI, one transliterated entry, one
    # queued item, both generation_hashes fields present) clears both
    # passes with exit 0 -- proves every negative test above is isolating a
    # single genuine defect, not a harness-wide misconfiguration.
    root = make_durable_root(tmp_path)
    write_canon(
        root,
        canon_file_doc(
            entries={
                "Amerique": canon_entry(
                    "Amerique",
                    canonical_target_form="America",
                    basis="established",
                    confidence="high",
                    source=VALID_URI,
                ),
                "Guerin": canon_entry(
                    "Guerin",
                    canonical_target_form="Gerin",
                    basis="transliterated",
                    confidence="medium",
                ),
            },
            review_queue=[queued_batch_item("Provence", note="disputed rendering")],
        ),
    )

    proc = run_canon_validate(root, "live")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    payload = parse_stdout(proc)
    assert payload["success"] is True
    assert payload["entries_count"] == 2
    assert payload["review_queue_count"] == 1


def test_validate_only_rejects_source_form_present_in_both_entries_and_review_queue(tmp_path):
    # Issue #102 hardening gap (the original bug -- `_merge_batch` re-
    # queuing an already-accepted source_form -- is already fixed; this is
    # the remaining schema-cross-constraint gap). canon-file.schema.json has
    # no cross-key constraint linking entries{} and review_queue[], so a
    # hand-corrupted (or otherwise not-merged-through-`_merge_batch`)
    # canon.json with the SAME source_form in BOTH collections previously
    # passed whole-file (Pass 2) validation silently. Written directly here
    # via write_canon(), bypassing `_merge_batch` entirely, to prove the
    # NEW `_validate_whole_file` invariant check (not schema validation)
    # catches it.
    root = make_durable_root(tmp_path)
    write_canon(
        root,
        canon_file_doc(
            entries={
                "Ivan": canon_entry(
                    "Ivan", canonical_target_form="Ivan", basis="transliterated", confidence="high"
                )
            },
            review_queue=[queued_batch_item("Ivan", note="needs a human call")],
        ),
    )

    proc = run_canon_validate(root, "live")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    payload = parse_stdout(proc)
    assert payload["success"] is False
    assert "Ivan" in payload["error"]


# ===========================================================================
# 5. #138 -- basis:"sense_translated" (TP-1): acceptance at all THREE enum
#    sites through the real canon_validate.py -- canon-batch.schema.json's
#    ACCEPTED branch, its QUEUED branch (via canon-file.schema.json's
#    POSITIONAL $ref, #/items/oneOf/1, which bypasses ACCEPTED entirely),
#    and canon-entry.schema.json directly once an item lives in entries{}.
# ===========================================================================


def test_sense_translated_accepted_item_full_lifecycle_check_merge_verify(tmp_path):
    # TP-1(a) -- the full real pipeline an operator actually drives:
    # --check-batch (Pass 1, NO write) -> --merge-batches (Pass 1 again +
    # offline backstop + merge + generation_hashes stamp + atomic write +
    # in-memory Pass 2 + disk-re-read Pass 2, all internal to this one call)
    # -> our own disk re-read -> --verify-merged (a SEPARATE,
    # disk-independent re-verification against the CURRENT canon.json).
    root = make_durable_root(tmp_path)
    batch_path = write_batch(
        root,
        [
            accepted_batch_item(
                "le Loup Solitaire",
                canonical_target_form="the Lone Wolf",
                basis="sense_translated",
                confidence="high",
                is_proper_name=True,
                note="deliberate sense-rendering of the epithet; no established form claimed",
            )
        ],
    )

    check_proc = run_canon_validate_cli(
        root, ["--research-mode", "live", "--check-batch", str(batch_path)]
    )
    assert check_proc.returncode == 0, check_proc.stdout + check_proc.stderr
    check_payload = parse_stdout(check_proc)
    assert check_payload["success"] is True
    assert not (root / "canon.json").exists()  # --check-batch never writes

    merge_proc = run_canon_validate_cli(
        root, ["--research-mode", "live", "--merge-batches", str(batch_path)]
    )
    assert merge_proc.returncode == 0, merge_proc.stdout + merge_proc.stderr
    merge_payload = parse_stdout(merge_proc)
    assert merge_payload["success"] is True
    assert merge_payload["merged_accepted"] == 1

    on_disk = json.loads((root / "canon.json").read_text(encoding="utf-8"))
    assert on_disk["entries"]["le Loup Solitaire"]["basis"] == "sense_translated"
    assert on_disk["entries"]["le Loup Solitaire"]["canonical_target_form"] == "the Lone Wolf"
    assert "source" not in on_disk["entries"]["le Loup Solitaire"]

    verify_proc = run_canon_validate_cli(
        root, ["--research-mode", "live", "--verify-merged", "--batch", str(batch_path)]
    )
    assert verify_proc.returncode == 0, verify_proc.stdout + verify_proc.stderr
    verify_payload = parse_stdout(verify_proc)
    assert verify_payload["verified"] is True
    assert verify_payload["missing"] == []


def test_sense_translated_queued_item_validates_via_positional_ref(tmp_path):
    # TP-1(b) -- basis:"sense_translated" is legal on a QUEUED item too (the
    # field is optional/unconstrained there -- no sense_translated
    # conditional applies to the QUEUED branch, only the enum widens).
    # canon-file.schema.json's review_queue[] uses a POSITIONAL $ref
    # (#/items/oneOf/1) straight at the QUEUED branch, bypassing
    # oneOf[0]/ACCEPTED entirely -- a partial 2-of-3 edit that widened
    # canon-entry.schema.json and canon-batch.schema.json's ACCEPTED branch
    # but forgot the QUEUED branch's own enum would pass every other test in
    # this file yet fail exactly this one.
    root = make_durable_root(tmp_path)
    write_canon(
        root,
        canon_file_doc(
            review_queue=[
                queued_batch_item(
                    "le Renard",
                    note="candidate sense-rendering, not yet confirmed",
                    basis="sense_translated",
                )
            ]
        ),
    )

    proc = run_canon_validate(root, "live")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    payload = parse_stdout(proc)
    assert payload["success"] is True
    assert payload["review_queue_count"] == 1


def test_sense_translated_entries_value_passes_validate_only(tmp_path):
    # TP-1(c) -- the third enum site: canon-entry.schema.json validated
    # directly against an existing entries{} value (VALIDATE-ONLY mode, no
    # --batch), independent of canon-batch.schema.json's discriminated
    # union entirely.
    root = make_durable_root(tmp_path)
    write_canon(
        root,
        canon_file_doc(
            entries={
                "le Loup Solitaire": canon_entry(
                    "le Loup Solitaire",
                    canonical_target_form="the Lone Wolf",
                    basis="sense_translated",
                    confidence="high",
                    is_proper_name=True,
                    note="deliberate sense-rendering; no established form claimed",
                )
            }
        ),
    )

    proc = run_canon_validate(root, "live")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    payload = parse_stdout(proc)
    assert payload["success"] is True
    assert payload["entries_count"] == 1


# ===========================================================================
# 6. #138 -- TP-4 (must STAY green, not a red-before-green test): the
#    established -> source (URI, non-empty) conditional, restructured from a
#    bare if/then into allOf:[{if,then},{if,then}] (S1.3) to make room for
#    the new sense_translated conditional alongside it, must still reject a
#    malformed/empty source on canon-batch.schema.json's ACCEPTED branch --
#    not just on canon-entry.schema.json (already covered in section 1
#    above). A botched restructure is a DUPLICATE JSON key that silently
#    drops one of the two `if`/`then` conditionals; this is the regression
#    guard for that specific failure mode on the second file.
# ===========================================================================


def test_merge_rejects_established_accepted_item_with_empty_source(tmp_path):
    root = make_durable_root(tmp_path)
    batch_path = write_batch(
        root,
        [
            accepted_batch_item(
                "Roi Soleil",
                canonical_target_form="Sun King",
                basis="established",
                confidence="high",
                source="",
            )
        ],
    )

    proc = run_canon_validate(root, "live", batch_path=batch_path)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    payload = parse_stdout(proc)
    assert payload["success"] is False
    assert "Roi Soleil" in payload["error"]
    assert not (root / "canon.json").exists()


def test_merge_rejects_established_accepted_item_with_malformed_uri_source(tmp_path):
    root = make_durable_root(tmp_path)
    batch_path = write_batch(
        root,
        [
            accepted_batch_item(
                "Roi Soleil",
                canonical_target_form="Sun King",
                basis="established",
                confidence="high",
                source="not-a-uri-no-scheme",
            )
        ],
    )

    proc = run_canon_validate(root, "live", batch_path=batch_path)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    payload = parse_stdout(proc)
    assert payload["success"] is False
    assert "Roi Soleil" in payload["error"]
    assert not (root / "canon.json").exists()


# ===========================================================================
# 7. #138 -- TP-5: the sense_translated conditional itself (identical in
#    canon-entry.schema.json and canon-batch.schema.json's ACCEPTED branch):
#    note+is_proper_name required, is_proper_name pinned to `true`,
#    note/canonical_target_form must carry VISIBLE (non-whitespace-only)
#    content -- pattern:"\S", not minLength:1 (D4) -- and source forbidden
#    outright.
#
#    Vacuously green pre-#138 (basis:"sense_translated" itself is rejected
#    by the bare 4-value enum, so every REJECT assertion below trivially
#    passes for the WRONG reason); genuinely RED if the enum were ever
#    widened without its conditional (nothing would enforce
#    note/is_proper_name/pattern/source, so every case here would wrongly
#    validate); GREEN only with the conditional actually in place, as it now
#    is. The positive control at the end proves the "then" clause is
#    actually satisfiable, not merely restrictive.
# ===========================================================================


def test_sense_translated_rejects_is_proper_name_false(tmp_path):
    root = make_durable_root(tmp_path)
    write_canon(
        root,
        canon_file_doc(
            entries={
                "Wolf": canon_entry(
                    "Wolf",
                    canonical_target_form="the Wolf",
                    basis="sense_translated",
                    confidence="high",
                    is_proper_name=False,
                    note="a sense-rendering",
                )
            }
        ),
    )

    proc = run_canon_validate(root, "live")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    payload = parse_stdout(proc)
    assert payload["success"] is False


def test_sense_translated_rejects_missing_note(tmp_path):
    root = make_durable_root(tmp_path)
    write_canon(
        root,
        canon_file_doc(
            entries={
                "Wolf": canon_entry(
                    "Wolf",
                    canonical_target_form="the Wolf",
                    basis="sense_translated",
                    confidence="high",
                    is_proper_name=True,
                )
            }
        ),
    )

    proc = run_canon_validate(root, "live")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    payload = parse_stdout(proc)
    assert payload["success"] is False


def test_sense_translated_rejects_whitespace_only_note(tmp_path):
    # D4 -- pattern:"\S", not minLength:1, is the whole point of this case:
    # a whitespace-only note passes minLength:1 but must fail \S.
    root = make_durable_root(tmp_path)
    write_canon(
        root,
        canon_file_doc(
            entries={
                "Wolf": canon_entry(
                    "Wolf",
                    canonical_target_form="the Wolf",
                    basis="sense_translated",
                    confidence="high",
                    is_proper_name=True,
                    note="   ",
                )
            }
        ),
    )

    proc = run_canon_validate(root, "live")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    payload = parse_stdout(proc)
    assert payload["success"] is False


def test_sense_translated_rejects_whitespace_only_canonical_target_form(tmp_path):
    root = make_durable_root(tmp_path)
    write_canon(
        root,
        canon_file_doc(
            entries={
                "Wolf": canon_entry(
                    "Wolf",
                    canonical_target_form="   ",
                    basis="sense_translated",
                    confidence="high",
                    is_proper_name=True,
                    note="a sense-rendering",
                )
            }
        ),
    )

    proc = run_canon_validate(root, "live")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    payload = parse_stdout(proc)
    assert payload["success"] is False


def test_sense_translated_rejects_source_present(tmp_path):
    root = make_durable_root(tmp_path)
    write_canon(
        root,
        canon_file_doc(
            entries={
                "Wolf": canon_entry(
                    "Wolf",
                    canonical_target_form="the Wolf",
                    basis="sense_translated",
                    confidence="high",
                    is_proper_name=True,
                    note="a sense-rendering",
                    source=VALID_URI,
                )
            }
        ),
    )

    proc = run_canon_validate(root, "live")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    payload = parse_stdout(proc)
    assert payload["success"] is False


def test_check_batch_rejects_accepted_sense_translated_missing_note(tmp_path):
    # Same conditional, exercised through canon-batch.schema.json's ACCEPTED
    # branch specifically (Pass 1, --check-batch, NO write) -- not just
    # canon-entry.schema.json via VALIDATE-ONLY above -- proving Pass 1
    # itself (not only the later whole-file Pass 2) enforces it.
    root = make_durable_root(tmp_path)
    batch_path = write_batch(
        root,
        [
            accepted_batch_item(
                "Wolf",
                canonical_target_form="the Wolf",
                basis="sense_translated",
                confidence="high",
                is_proper_name=True,
            )
        ],
    )

    proc = run_canon_validate_cli(
        root, ["--research-mode", "live", "--check-batch", str(batch_path)]
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr
    payload = parse_stdout(proc)
    assert payload["success"] is False
    assert not (root / "canon.json").exists()


def test_sense_translated_accepts_well_formed_entry(tmp_path):
    # Positive control -- proves the conditional's "then" clause is
    # satisfiable, not merely restrictive.
    root = make_durable_root(tmp_path)
    write_canon(
        root,
        canon_file_doc(
            entries={
                "Wolf": canon_entry(
                    "Wolf",
                    canonical_target_form="the Wolf",
                    basis="sense_translated",
                    confidence="high",
                    is_proper_name=True,
                    note="deliberate sense-rendering of the epithet",
                )
            }
        ),
    )

    proc = run_canon_validate(root, "live")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    payload = parse_stdout(proc)
    assert payload["success"] is True
    assert payload["entries_count"] == 1


# ===========================================================================
# 8. #138 -- TP-6: basis:"sense_translated" is offline-legal (D5), exactly
#    like "transliterated"/"title"/"not_a_name" -- it never claims an
#    external citation, so the offline backstop (which forbids ONLY
#    basis:"established") must not fire for it. The established twin in the
#    SAME batch composition must still FATAL, proving the exemption is
#    scoped to the new basis, not a regression that widens the backstop's
#    own exemption list.
# ===========================================================================


def test_merge_offline_mode_allows_sense_translated_accepted_item(tmp_path):
    root = make_durable_root(tmp_path)
    batch_path = write_batch(
        root,
        [
            accepted_batch_item(
                "Wolf",
                canonical_target_form="the Wolf",
                basis="sense_translated",
                confidence="high",
                is_proper_name=True,
                note="deliberate sense-rendering of the epithet",
            )
        ],
    )

    proc = run_canon_validate(root, "offline", batch_path=batch_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    payload = parse_stdout(proc)
    assert payload["success"] is True
    assert payload["merged_accepted"] == 1

    on_disk = json.loads((root / "canon.json").read_text(encoding="utf-8"))
    assert on_disk["entries"]["Wolf"]["basis"] == "sense_translated"


def test_merge_offline_mode_still_fatally_rejects_established_twin(tmp_path):
    root = make_durable_root(tmp_path)
    batch_path = write_batch(
        root,
        [
            accepted_batch_item(
                "Wolf",
                canonical_target_form="the Wolf",
                basis="sense_translated",
                confidence="high",
                is_proper_name=True,
                note="deliberate sense-rendering of the epithet",
            ),
            accepted_batch_item(
                "Roi Soleil",
                canonical_target_form="Sun King",
                basis="established",
                confidence="high",
                source=VALID_URI,
            ),
        ],
    )

    proc = run_canon_validate(root, "offline", batch_path=batch_path)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    payload = parse_stdout(proc)
    assert payload["success"] is False
    assert payload["offending"] == ["Roi Soleil"]
    assert "Wolf" not in payload["offending"]
    assert not (root / "canon.json").exists()


# ===========================================================================
# 9. #138 -- TP-2b/TP-2d: regression locks for canon_validate.py's landed
#    _format_errors/_format_oneof_error/_forbidden_keys_for_schema
#    formatter (D17/S2). Through canon-batch.schema.json's `oneOf`, a
#    boolean-`false` property rejection (`"source": false`) reports at the
#    PARENT with the offending key STRIPPED -- jsonschema's own message is
#    a bare "False schema does not allow '<value>'", never naming the key
#    (verified empirically at D17/S2). Without the branch-select +
#    key-recovery formatter, the retry agent sees only the raw
#    whole-instance `oneOf` dump and could mutate the `source` value
#    forever instead of removing the field -- the T-14 hang by another
#    road. These two tests lock that formatter's behavior against
#    regression; they were flagged as a real coverage gap (PLAN-138 S2
#    defines TP-2b/TP-2d but neither owner had claimed them).
# ===========================================================================


def test_check_batch_sense_translated_source_forbidden_error_names_the_property(tmp_path):
    # TP-2b -- an ACCEPTED sense_translated item carrying a forbidden
    # `source` must yield a message naming the property, the branch label,
    # and the offending basis -- never the raw whole-instance dump.
    root = make_durable_root(tmp_path)
    batch_path = write_batch(
        root,
        [
            accepted_batch_item(
                "Loup",
                canonical_target_form="the Wolf",
                basis="sense_translated",
                confidence="high",
                is_proper_name=True,
                note="a sense-rendering",
                source=VALID_URI,
            )
        ],
    )

    proc = run_canon_validate_cli(
        root, ["--research-mode", "live", "--check-batch", str(batch_path)]
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr
    payload = parse_stdout(proc)
    assert payload["success"] is False
    error = payload["error"]
    assert "source" in error
    assert "sense_translated" in error
    assert "forbidden" in error
    assert "accepted" in error
    # NOT the raw whole-instance oneOf dump -- that's the exact failure
    # mode D17/S2 fixes (a bare "is not valid under any of the given
    # schemas" gives the retry agent nothing actionable to act on).
    assert "is not valid under any of the given schemas" not in error


def test_check_batch_missing_disposition_still_yields_actionable_message(tmp_path):
    # TP-2d -- D17(c): an item whose 'disposition' matches NEITHER oneOf
    # branch (missing here -- both branches require it) must still produce
    # a NON-EMPTY, actionable message via jsonschema's own best_match
    # fallback across every branch's sub-errors -- never an empty string,
    # never a crash/traceback.
    root = make_durable_root(tmp_path)
    batch_path = write_batch(
        root,
        [
            {
                "source_form": "Ambiguous",
                "is_proper_name": True,
                "canonical_target_form": "Something",
                "basis": "transliterated",
                "confidence": "high",
                # 'disposition' deliberately omitted.
            }
        ],
    )

    proc = run_canon_validate_cli(
        root, ["--research-mode", "live", "--check-batch", str(batch_path)]
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr
    payload = parse_stdout(proc)
    assert payload["success"] is False
    error = payload["error"]
    assert error.strip() != ""
    assert "Ambiguous" in error


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
