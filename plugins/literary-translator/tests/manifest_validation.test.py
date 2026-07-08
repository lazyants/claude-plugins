"""tests/manifest_validation.test.py

Targets ``manifest.schema.json``'s validation of ``extract.py.template``'s
output (see the schema's own top-level ``description`` and the build plan's
manifest.schema.json section: "validates ... generation_hashes.
source_extraction_hash (REQUIRED), source_inputs: [string] (REQUIRED,
minItems:1) PLUS generation_hashes.source_input_hash (REQUIRED) ...").

Two things are exercised, both against the REAL, shipped files -- never
hand-rolled restatements:

1. **Schema validation** -- calls ``extract.py.template``'s own
   ``validate_against_schema()`` function (the real
   ``jsonschema.Draft202012Validator`` + ``FormatChecker`` call the template
   itself makes immediately after extraction, per the build plan: "using the
   REAL jsonschema.Draft202012Validator ... this script runs once per
   project, low-frequency, so the real library is fine here") against the
   REAL ``manifest.schema.json`` file. Covers: a schema-valid baseline
   manifest passes clean; dropping ``generation_hashes.source_extraction_hash``
   or ``.source_input_hash`` each fails; dropping ``generation_hashes``
   entirely fails; dropping ``source_inputs`` entirely fails; an empty
   ``source_inputs: []`` fails the array's ``minItems: 1`` constraint.

2. **The procedural cross-reference invariant** -- checked PROCEDURALLY by
   ``extract.py.template``'s own round-trip self-check suite
   (``run_self_checks()``'s ``frontback_inventory`` check), never
   schema-expressible (manifest.schema.json's own top-level description
   says so explicitly): every ``frontback[]`` entry with
   ``decision:"translate"`` must have a matching id in ``segments[]``, and
   every ``regenerate``/``omit``-decision entry must NOT appear in
   ``segments[]`` at all. Both directions are locked as FATAL, NAMED
   failures (the ``frontback_inventory`` check flips to ``ok: False`` and
   ``run_self_checks()``'s overall ``all_pass`` flips to ``False``) --
   isolated from every other self-check in the suite via a clean baseline
   fixture that passes every other check, so a regression that weakens
   *this specific* check cannot hide behind an unrelated failure.

``extract.py.template`` is loaded by copying it into a throwaway
``${durable_root}`` fixture first (never imported directly from its real
``assets/templates/`` location) -- its module-level
``DURABLE_ROOT = Path(__file__).resolve().parent`` self-anchors off
wherever it is loaded from, and calling ``two_phase_write()``/writing a
real ``manifest.json`` against the plugin's own source tree would be a
real, if narrow, side effect this suite must not risk (this suite only
calls ``run_self_checks()``/``validate_against_schema()`` directly, so no
manifest.json is ever actually written -- the copy is defensive, matching
the self-anchoring discipline every other test in this suite already
follows for scripts under ``${durable_root}/scripts/``). The real
``manifest.schema.json`` is copied alongside it into ``schemas/``, exactly
as Step 0a would, so ``validate_against_schema()`` reads the actual shipped
schema, not a hand-copied stand-in.

Collection note: like every ``*.test.py`` file in this suite, pytest's
default "prepend" import mode cannot resolve the dotted module name -- run
with ``python3 -m pytest --import-mode=importlib
tests/manifest_validation.test.py`` (already configured project-wide via
``pytest.ini``).
"""
import hashlib
import importlib.util
import shutil
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_PATH = (
    PLUGIN_ROOT
    / "skills" / "literary-translator" / "assets" / "templates" / "extract.py.template"
)
SCHEMA_PATH = (
    PLUGIN_ROOT
    / "skills" / "literary-translator" / "assets" / "schemas" / "manifest.schema.json"
)

assert TEMPLATE_PATH.is_file(), f"extract.py.template not found at {TEMPLATE_PATH}"
assert SCHEMA_PATH.is_file(), f"manifest.schema.json not found at {SCHEMA_PATH}"


def _load_extract_module(tmp_path: Path):
    """Copies extract.py.template + the real manifest.schema.json into a
    throwaway ${durable_root} fixture and imports the copy fresh -- see the
    module docstring for why this is a copy, never the real installed path.
    """
    durable_root = tmp_path / "durable"
    (durable_root / "schemas").mkdir(parents=True)
    extract_copy = durable_root / "extract.py"
    shutil.copyfile(TEMPLATE_PATH, extract_copy)
    shutil.copyfile(SCHEMA_PATH, durable_root / "schemas" / "manifest.schema.json")

    spec = importlib.util.spec_from_file_location("extract_under_test", extract_copy)
    assert spec is not None and spec.loader is not None, f"could not load spec for {extract_copy}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def extract_mod(tmp_path):
    return _load_extract_module(tmp_path)


def _sha1(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# A schema-valid, self-check-clean baseline manifest -- every test below
# either uses this verbatim or deepcopies + perturbs exactly ONE thing, so a
# failure can only be attributed to the thing that was actually changed.
# ---------------------------------------------------------------------------

def _baseline_manifest() -> dict:
    return {
        "blocks": {
            "HEAD:seg01": {
                "id": "HEAD:seg01", "type": "HEAD", "order_index": 0,
                "source_file": "body.xhtml", "plain_text": "Chapter One",
                "sha1": _sha1("Chapter One"),
            },
            "PARA:seg01:0001": {
                "id": "PARA:seg01:0001", "type": "PARA", "order_index": 1,
                "seg": "seg01", "source_file": "body.xhtml",
                "plain_text": "Some body prose.", "sha1": _sha1("Some body prose."),
            },
            "FRONTBACK:fm01": {
                "id": "FRONTBACK:fm01", "type": "FRONTBACK", "order_index": 2,
                "source_file": "front.xhtml", "plain_text": "Title page text",
                "sha1": _sha1("Title page text"),
                "decision": "translate", "reason": "title-page text worth keeping",
            },
            "FRONTBACK:fm02": {
                "id": "FRONTBACK:fm02", "type": "FRONTBACK", "order_index": 3,
                "source_file": "front.xhtml", "plain_text": "Project Gutenberg boilerplate",
                "sha1": _sha1("Project Gutenberg boilerplate"),
                "decision": "omit", "reason": "Project Gutenberg boilerplate header",
            },
        },
        "spine": [
            {"pos": 0, "file": "body.xhtml", "klass": "body"},
            {"pos": 1, "file": "front.xhtml", "klass": "front-back"},
        ],
        "segments": [
            {
                "seg": "seg01", "kind": "body",
                "block_ids": ["HEAD:seg01", "PARA:seg01:0001"],
                "word_count": 4, "n_para": 1, "n_verse": 0, "n_quote": 0,
                "source_files": ["body.xhtml"],
            },
            {
                "seg": "FRONTBACK:fm01", "kind": "frontback",
                "block_ids": ["FRONTBACK:fm01"], "word_count": 3,
                "source_files": ["front.xhtml"],
            },
        ],
        "footnotes": [],
        "frontback": [
            {"id": "FRONTBACK:fm01", "decision": "translate", "reason": "title-page text worth keeping"},
            {"id": "FRONTBACK:fm02", "decision": "omit", "reason": "Project Gutenberg boilerplate header"},
        ],
        "verse": {
            "store": [], "n_nodes": 0, "n_block": 0, "n_embedded": 0,
            "by_context": {"body": 0, "footnote": 0, "frontback": 0},
            "total_stanza": 0, "total_line": 0,
        },
        "source_inputs": ["book.epub"],
        "generation_hashes": {
            "source_extraction_hash": _sha1("source_extraction_hash fixture"),
            "source_input_hash": _sha1("source_input_hash fixture"),
        },
    }


def _baseline_report() -> dict:
    return {
        "body_toplevel_total": 1,
        "body_toplevel_classified": 1,
        "unclassified": [],
        "apparatus_policy": "omit_apparatus",
        "orphan_fn": [],
        "uncovered_verse_lines": [],
        "n_verse_blocks": 0,
    }


def _find_check(results, name):
    matches = [r for r in results if r["name"] == name]
    assert len(matches) == 1, f"expected exactly one {name!r} check, found {len(matches)}"
    return matches[0]


# ---------------------------------------------------------------------------
# Baseline sanity: proves the fixture itself, not the checks under test, is
# innocent before any perturbation is asserted to fail below.
# ---------------------------------------------------------------------------

def test_baseline_manifest_is_schema_valid(extract_mod):
    errors = extract_mod.validate_against_schema(_baseline_manifest())
    assert errors == [], f"baseline fixture must be schema-valid; got: {errors}"


def test_baseline_manifest_passes_round_trip_self_checks(extract_mod):
    manifest = _baseline_manifest()
    checks = extract_mod.run_self_checks(manifest, _baseline_report(), max_segment_words=100)

    assert checks["all_pass"] is True, checks["results"]
    frontback_check = _find_check(checks["results"], "frontback_inventory")
    assert frontback_check["ok"] is True, frontback_check["detail"]


# ---------------------------------------------------------------------------
# Schema: generation_hashes.source_extraction_hash / .source_input_hash
# required.
# ---------------------------------------------------------------------------

def test_missing_source_extraction_hash_fails_schema(extract_mod):
    manifest = _baseline_manifest()
    del manifest["generation_hashes"]["source_extraction_hash"]

    errors = extract_mod.validate_against_schema(manifest)

    assert errors, "dropping generation_hashes.source_extraction_hash must fail schema validation"
    combined = "\n".join(errors)
    assert "generation_hashes" in combined and "source_extraction_hash" in combined, combined
    assert "required" in combined, combined


def test_missing_source_input_hash_fails_schema(extract_mod):
    manifest = _baseline_manifest()
    del manifest["generation_hashes"]["source_input_hash"]

    errors = extract_mod.validate_against_schema(manifest)

    assert errors, "dropping generation_hashes.source_input_hash must fail schema validation"
    combined = "\n".join(errors)
    assert "generation_hashes" in combined and "source_input_hash" in combined, combined
    assert "required" in combined, combined


def test_missing_generation_hashes_block_entirely_fails_schema(extract_mod):
    manifest = _baseline_manifest()
    del manifest["generation_hashes"]

    errors = extract_mod.validate_against_schema(manifest)

    assert errors, "dropping generation_hashes entirely must fail schema validation"
    combined = "\n".join(errors)
    assert "generation_hashes" in combined, combined
    assert "required" in combined, combined


# ---------------------------------------------------------------------------
# Schema: source_inputs is a REQUIRED array with minItems:1.
# ---------------------------------------------------------------------------

def test_missing_source_inputs_key_fails_schema(extract_mod):
    manifest = _baseline_manifest()
    del manifest["source_inputs"]

    errors = extract_mod.validate_against_schema(manifest)

    assert errors, "dropping source_inputs entirely must fail schema validation"
    combined = "\n".join(errors)
    assert "source_inputs" in combined, combined
    assert "required" in combined, combined


def test_empty_source_inputs_array_fails_minitems(extract_mod):
    manifest = _baseline_manifest()
    manifest["source_inputs"] = []

    errors = extract_mod.validate_against_schema(manifest)

    assert errors, "an empty source_inputs: [] must fail the minItems:1 constraint"
    combined = "\n".join(errors)
    assert "source_inputs" in combined, combined


def test_populated_source_inputs_array_is_schema_valid(extract_mod):
    """Regression-lock companion to the empty-array case above: a
    source_inputs array satisfying minItems:1 (the baseline's own
    ["book.epub"]) must NOT itself be rejected -- isolates minItems as the
    actual, sole cause of the empty-array failure above, not some other
    latent defect in how this suite constructs source_inputs."""
    manifest = _baseline_manifest()
    manifest["source_inputs"] = ["book.epub", "book-notes.epub"]

    errors = extract_mod.validate_against_schema(manifest)

    assert errors == [], f"a populated source_inputs array must be schema-valid; got: {errors}"


# ---------------------------------------------------------------------------
# The procedural cross-reference invariant (never schema-expressible, per
# manifest.schema.json's own top-level description): checked by
# run_self_checks()'s "frontback_inventory" check, both directions.
# ---------------------------------------------------------------------------

def test_translate_frontback_entry_missing_from_segments_is_fatal(extract_mod):
    """frontback[] declares FRONTBACK:fm01 as decision:'translate', but no
    matching segments[] entry exists for it -- must be a FATAL,
    frontback_inventory-named failure, isolated from every other check."""
    manifest = _baseline_manifest()
    manifest["segments"] = [s for s in manifest["segments"] if s["seg"] != "FRONTBACK:fm01"]

    checks = extract_mod.run_self_checks(manifest, _baseline_report(), max_segment_words=100)

    assert checks["all_pass"] is False, checks["results"]
    frontback_check = _find_check(checks["results"], "frontback_inventory")
    assert frontback_check["ok"] is False, frontback_check["detail"]
    assert "FRONTBACK:fm01" in frontback_check["detail"], frontback_check["detail"]
    assert "missing_from_segments" in frontback_check["detail"], frontback_check["detail"]

    # every other check must still pass -- the ONLY thing perturbed above
    # was the frontback-owning segments[] entry.
    for result in checks["results"]:
        if result["name"] != "frontback_inventory":
            assert result["ok"] is True, result


def test_omit_frontback_entry_leaked_into_segments_is_fatal(extract_mod):
    """frontback[] declares FRONTBACK:fm02 as decision:'omit', but it leaked
    into segments[] anyway (as if it were a real translatable unit) -- must
    be a FATAL, frontback_inventory-named failure in the OTHER direction."""
    manifest = _baseline_manifest()
    manifest["segments"].append({
        "seg": "FRONTBACK:fm02", "kind": "frontback",
        "block_ids": ["FRONTBACK:fm02"], "word_count": 3,
        "source_files": ["front.xhtml"],
    })

    checks = extract_mod.run_self_checks(manifest, _baseline_report(), max_segment_words=100)

    assert checks["all_pass"] is False, checks["results"]
    frontback_check = _find_check(checks["results"], "frontback_inventory")
    assert frontback_check["ok"] is False, frontback_check["detail"]
    assert "FRONTBACK:fm02" in frontback_check["detail"], frontback_check["detail"]
    assert "leaked_into_segments" in frontback_check["detail"], frontback_check["detail"]

    for result in checks["results"]:
        if result["name"] != "frontback_inventory":
            assert result["ok"] is True, result


def test_regenerate_frontback_entry_leaked_into_segments_is_fatal(extract_mod):
    """Same leaked-into-segments direction as above, but for a
    decision:'regenerate' entry rather than 'omit' -- the self-check groups
    both non-'translate' decisions identically (fb_other_ids), so this locks
    down that 'regenerate' is not silently exempted."""
    manifest = _baseline_manifest()
    manifest["frontback"][1]["decision"] = "regenerate"
    manifest["frontback"][1]["reason"] = "back-cover advertising, to be regenerated"
    manifest["blocks"]["FRONTBACK:fm02"]["decision"] = "regenerate"
    manifest["segments"].append({
        "seg": "FRONTBACK:fm02", "kind": "frontback",
        "block_ids": ["FRONTBACK:fm02"], "word_count": 3,
        "source_files": ["front.xhtml"],
    })

    checks = extract_mod.run_self_checks(manifest, _baseline_report(), max_segment_words=100)

    assert checks["all_pass"] is False, checks["results"]
    frontback_check = _find_check(checks["results"], "frontback_inventory")
    assert frontback_check["ok"] is False, frontback_check["detail"]
    assert "FRONTBACK:fm02" in frontback_check["detail"], frontback_check["detail"]
    assert "leaked_into_segments" in frontback_check["detail"], frontback_check["detail"]


def test_correct_frontback_disposition_does_not_trip_either_direction(extract_mod):
    """Companion regression-lock to the two failure cases above: the
    baseline's OWN frontback disposition -- one 'translate' entry correctly
    present in segments[], one 'omit' entry correctly absent -- must pass
    cleanly. Guards against an over-eager fix to either failure case above
    turning into a false positive on the legitimate, matching case."""
    manifest = _baseline_manifest()

    checks = extract_mod.run_self_checks(manifest, _baseline_report(), max_segment_words=100)

    frontback_check = _find_check(checks["results"], "frontback_inventory")
    assert frontback_check["ok"] is True, frontback_check["detail"]
    assert "missing_from_segments=[]" in frontback_check["detail"], frontback_check["detail"]
    assert "leaked_into_segments=[]" in frontback_check["detail"], frontback_check["detail"]
