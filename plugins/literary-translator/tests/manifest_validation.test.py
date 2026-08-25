"""tests/manifest_validation.test.py

Targets ``manifest.schema.json``'s validation of ``extract.py.template``'s
output (see the schema's own top-level ``description`` and the build plan's
manifest.schema.json section: "validates ... generation_hashes.
source_extraction_hash (REQUIRED), source_inputs: [string] (REQUIRED,
minItems:1) PLUS generation_hashes.source_input_hash (REQUIRED) ...").

Three things are exercised, all against the REAL, shipped files -- never
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

3. **The zero-body refusal (#761)** -- drives the REAL ``main()`` end to
   end over a throwaway ``${durable_root}``: a spine that classifies no
   file as body must exit 1 naming ``spine_yields_body_files``, never
   print ``ALL PASS``, and the same EPUB with a ``spine_overrides`` entry
   must still clear cleanly. See that block's own header comment for why
   a ``run_self_checks()``-only test cannot cover it.

``extract.py.template`` is loaded by copying it into a throwaway
``${durable_root}`` fixture first (never imported directly from its real
``assets/templates/`` location) -- its module-level
``DURABLE_ROOT = Path(__file__).resolve().parent`` self-anchors off
wherever it is loaded from, and calling ``two_phase_write()``/writing a
real ``manifest.json`` against the plugin's own source tree would be a
real, if narrow, side effect this suite must not risk. For the checks that
call ``run_self_checks()``/``validate_against_schema()`` directly the copy
is defensive, matching the self-anchoring discipline every other test in
this suite already follows for scripts under ``${durable_root}/scripts/``;
for the #761 end-to-end checks it is load-bearing -- those go through
``main()``, whose ``two_phase_write()`` writes a real ``manifest.json``
beside whatever ``extract.py`` was loaded from. The real
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
import json
import shutil
import zipfile
from pathlib import Path

import pytest
import yaml

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_PATH = (
    PLUGIN_ROOT
    / "skills" / "literary-translator" / "assets" / "templates" / "extract.py.template"
)
SCHEMA_PATH = (
    PLUGIN_ROOT
    / "skills" / "literary-translator" / "assets" / "schemas" / "manifest.schema.json"
)
# #210's classification precedence (heading_types vs. a block-mount verse
# claim) lives in assemble.py's _classify_kind, not extract.py.template --
# imported directly, in place (never copied), for the same reason a bare
# `import assemble` from the tests dir won't work: it self-anchors
# SCRIPTS_DIR = Path(__file__).resolve().parent at import time, so loading
# it from its REAL location means that anchor already resolves to the real
# assets/scripts/ directory, where its sibling imports (validate_draft.py,
# output_resolve.py) genuinely live -- no durable_root copy needed, and
# nothing at module level touches the filesystem (see assemble.py's own
# DURABLE_ROOT/SCRIPTS_DIR constants, used only inside functions).
ASSEMBLE_PATH = (
    PLUGIN_ROOT
    / "skills" / "literary-translator" / "assets" / "scripts" / "assemble.py"
)
# #761's end-to-end main() fixture (below) needs the REAL scripts/ directory
# copied whole into its throwaway durable_root: main()'s two_phase_write()
# shells out to scripts/cache_key.py, which itself loads scripts/json_stdout.py
# as an exact-path sibling -- a durable_root missing either exits loud and
# far from this file's own assertions.
SCRIPTS_SRC_DIR = (
    PLUGIN_ROOT
    / "skills" / "literary-translator" / "assets" / "scripts"
)

assert TEMPLATE_PATH.is_file(), f"extract.py.template not found at {TEMPLATE_PATH}"
assert SCHEMA_PATH.is_file(), f"manifest.schema.json not found at {SCHEMA_PATH}"
assert ASSEMBLE_PATH.is_file(), f"assemble.py not found at {ASSEMBLE_PATH}"
assert (SCRIPTS_SRC_DIR / "cache_key.py").is_file(), f"cache_key.py not found under {SCRIPTS_SRC_DIR}"


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


def _load_assemble_module():
    spec = importlib.util.spec_from_file_location("assemble_under_test", ASSEMBLE_PATH)
    assert spec is not None and spec.loader is not None, f"could not load spec for {ASSEMBLE_PATH}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def assemble_mod():
    return _load_assemble_module()


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
        # #83: the body_files_yield_segments self-check reads this. The baseline
        # has exactly one body file (spine's body.xhtml) yielding one body
        # segment (seg01), so the check passes cleanly here.
        "n_body_files": 1,
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


# ---------------------------------------------------------------------------
# #83: body_files_yield_segments -- a body-classified source that produces ZERO
# body segments (the div-wrapped-<h2> collapse) is a FATAL, named failure. Both
# directions locked, isolated from every other check via the clean baseline.
# ---------------------------------------------------------------------------

def test_body_files_yield_segments_fatal_when_body_file_yields_no_segment(extract_mod):
    """n_body_files > 0 but no kind=='body' segment survived -- the whole body
    file collapsed. Must be a FATAL, body_files_yield_segments-named failure,
    with every other check still passing."""
    manifest = _baseline_manifest()
    # drop the sole body segment; the body file (spine body.xhtml, n_body_files=1)
    # now yields nothing.
    manifest["segments"] = [s for s in manifest["segments"] if s["kind"] != "body"]

    checks = extract_mod.run_self_checks(manifest, _baseline_report(), max_segment_words=100)

    assert checks["all_pass"] is False, checks["results"]
    check = _find_check(checks["results"], "body_files_yield_segments")
    assert check["ok"] is False, check["detail"]
    assert "n_body_files=1" in check["detail"], check["detail"]
    assert "n_body_segments=0" in check["detail"], check["detail"]

    for result in checks["results"]:
        if result["name"] != "body_files_yield_segments":
            assert result["ok"] is True, result


def test_body_files_yield_segments_passes_when_source_has_no_body_files(extract_mod):
    """Companion regression-lock, not a legitimacy claim (#761 reversed that):
    #83's body_files_yield_segments is gated on n_body_files>0 and answers
    "body files exist but collapsed to zero body segments" -- it must NOT
    false-fail merely because there are no body files at all (guards against
    an over-eager fix that fires on any empty body-segment list). The two
    checks partition the space: a source with NO body files at all
    (n_body_files==0, this fixture) is a DIFFERENT question, now refused by
    main()'s spine_yields_body_files / the gate's own check of the same name
    -- see extract.py.template's main() and validate_extraction.py's
    run_derivable_checks(). Neither of those is reachable from
    run_self_checks() directly, which is why this test still asserts #83
    passes here rather than asserting the source is accepted overall."""
    manifest = _baseline_manifest()
    manifest["segments"] = [s for s in manifest["segments"] if s["kind"] != "body"]
    report = _baseline_report()
    report["n_body_files"] = 0

    checks = extract_mod.run_self_checks(manifest, report, max_segment_words=100)

    check = _find_check(checks["results"], "body_files_yield_segments")
    assert check["ok"] is True, check["detail"]


# ---------------------------------------------------------------------------
# #84: verse_plain_text_nonempty -- a verse.store entry with empty/whitespace
# plain_text (the bare-<p>-stanza drop) is a FATAL, named failure.
# ---------------------------------------------------------------------------

def _manifest_with_one_verse(plain_text: str) -> dict:
    """Baseline + a single embedded verse.store entry. Every OTHER verse check
    (placeholders unique + mounted, counts reconcile, no uncovered) is
    satisfied so only verse_plain_text_nonempty can attribute a failure."""
    manifest = _baseline_manifest()
    manifest["verse"] = {
        "store": [
            {
                "vid": "V001",
                "placeholder": "⟦VERSE_V001_deadbeef⟧",
                "parent_block": "PARA:seg01:0001",
                "mount": "embedded",
                "plain_text": plain_text,
            }
        ],
        "n_nodes": 1,
        "n_block": 0,
        "n_embedded": 1,
        "by_context": {"body": 1, "footnote": 0, "frontback": 0},
        "total_stanza": 0,
        "total_line": 0,
    }
    return manifest


def test_verse_plain_text_nonempty_fatal_on_whitespace_plain_text(extract_mod):
    manifest = _manifest_with_one_verse("   ")  # whitespace-only -> must FATAL

    checks = extract_mod.run_self_checks(manifest, _baseline_report(), max_segment_words=100)

    assert checks["all_pass"] is False, checks["results"]
    check = _find_check(checks["results"], "verse_plain_text_nonempty")
    assert check["ok"] is False, check["detail"]
    assert "V001" in check["detail"], check["detail"]

    for result in checks["results"]:
        if result["name"] != "verse_plain_text_nonempty":
            assert result["ok"] is True, result


def test_verse_plain_text_nonempty_passes_on_populated_plain_text(extract_mod):
    """Companion regression-lock: a verse entry with real plain_text must pass
    -- isolates whitespace-emptiness as the sole cause of the failure above."""
    manifest = _manifest_with_one_verse("Some real verse text")

    checks = extract_mod.run_self_checks(manifest, _baseline_report(), max_segment_words=100)

    check = _find_check(checks["results"], "verse_plain_text_nonempty")
    assert check["ok"] is True, check["detail"]


# ---------------------------------------------------------------------------
# #210: manifest.heading_types -- schema boundary. Optional top-level array
# of manifest-declared heading block-type tags; top-level is
# additionalProperties:false, so it had to be explicitly declared (never
# added to `required`). Every case below is checked against the REAL,
# shipped manifest.schema.json (via extract.py.template's own
# validate_against_schema()) -- never a hand-rolled restatement of the
# schema's rules.
# ---------------------------------------------------------------------------

def test_heading_types_absent_is_schema_valid(extract_mod):
    """Absent entirely (not even an empty list) -- the schema's own
    back-compat baseline: every pre-#210 manifest, unmodified, must stay
    schema-valid. (Also exercised implicitly by
    test_baseline_manifest_is_schema_valid above -- named explicitly here
    so a future required-ification of heading_types has a dedicated,
    obviously-named regression lock.)"""
    manifest = _baseline_manifest()
    assert "heading_types" not in manifest, "sanity: the baseline fixture must not set it"

    errors = extract_mod.validate_against_schema(manifest)

    assert errors == [], f"an absent heading_types must be schema-valid; got: {errors}"


def test_heading_types_valid_unique_string_array_is_schema_valid(extract_mod):
    manifest = _baseline_manifest()
    manifest["heading_types"] = ["CHAPTER", "SIMAN"]

    errors = extract_mod.validate_against_schema(manifest)

    assert errors == [], f"a valid non-empty unique string array must be schema-valid; got: {errors}"


def test_heading_types_empty_string_item_fails_schema(extract_mod):
    manifest = _baseline_manifest()
    manifest["heading_types"] = ["CHAPTER", ""]

    errors = extract_mod.validate_against_schema(manifest)

    assert errors, "an empty-string item must fail the items.minLength:1 constraint"
    combined = "\n".join(errors)
    assert "heading_types" in combined, combined


def test_heading_types_duplicate_items_fails_schema(extract_mod):
    manifest = _baseline_manifest()
    manifest["heading_types"] = ["CHAPTER", "CHAPTER"]

    errors = extract_mod.validate_against_schema(manifest)

    assert errors, "duplicate items must fail the uniqueItems constraint"
    combined = "\n".join(errors)
    assert "heading_types" in combined, combined
    assert "unique" in combined.lower(), combined


def test_undeclared_top_level_key_still_rejected(extract_mod):
    """Adding heading_types as a new top-level property must not have
    loosened the top-level additionalProperties:false gate for every OTHER
    key -- a genuinely unrelated, undeclared top-level key must still be
    rejected."""
    manifest = _baseline_manifest()
    manifest["totally_unexpected_key"] = "x"

    errors = extract_mod.validate_against_schema(manifest)

    assert errors, "an undeclared top-level key must still fail schema validation"
    combined = "\n".join(errors)
    assert "totally_unexpected_key" in combined, combined


# ---------------------------------------------------------------------------
# #210: declared-heading precedence over a block-mount verse claim.
# assemble.py's _classify_kind checks heading_types ABOVE the block-mount
# verse test, so a block that is BOTH a declared heading type AND carries a
# block-mount verse claim classifies "heading" -- mirroring "HEAD" already
# winning over a block-mount verse claim today. Unit-level (the pure
# classify function itself), imported directly from the real assemble.py
# (see _load_assemble_module above) -- no durable_root fixture needed since
# _classify_kind touches no filesystem state.
# ---------------------------------------------------------------------------

def test_declared_heading_type_wins_over_block_mount_verse_claim(assemble_mod):
    claims = [{"vid": "v1"}]
    verse_store_by_vid = {"v1": {"mount": "block"}}

    kind = assemble_mod._classify_kind(
        "CHAPTER", claims, verse_store_by_vid, heading_types=frozenset({"CHAPTER"}),
    )

    assert kind == "heading", (
        "a declared-heading-type block that ALSO carries a block-mount "
        "verse claim must classify heading, not verse"
    )


def test_same_block_mount_verse_claim_classifies_verse_when_type_not_declared_heading(assemble_mod):
    """Companion regression-lock, isolating heading_types as the sole cause
    of the override above: the identical block-mount verse claim, with
    heading_types either absent or simply not naming this block's type,
    classifies verse -- exactly today's pre-#210 behavior."""
    claims = [{"vid": "v1"}]
    verse_store_by_vid = {"v1": {"mount": "block"}}

    kind = assemble_mod._classify_kind("CHAPTER", claims, verse_store_by_vid)

    assert kind == "verse"


# ---------------------------------------------------------------------------
# #761: main() must refuse, not print ALL PASS and exit 0, when spine
# classification yields ZERO body files. run_self_checks()'s own
# body_files_yield_segments (#83, exercised above) is gated on
# n_body_files>0 by design (see the companion docstring update above) --
# nothing inside run_self_checks() ever asserts a body file exists at all,
# so a helper-only test that stops at run_self_checks() would stay green
# even if the new spine_yields_body_files logic in main() were deleted.
# This drives the REAL main() end-to-end, through a throwaway durable_root
# extract.py.template's own self-anchoring makes possible with no
# monkeypatching (its module-level DURABLE_ROOT = Path(__file__).resolve()
# .parent, with ROOT_MARKER_PATH defined right beside it).
# ---------------------------------------------------------------------------

_E2E_BODY_FILENAME = "body.xhtml"

_E2E_CONTAINER_XML = (
    '<?xml version="1.0"?>\n'
    '<container version="1.0" '
    'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">\n'
    '  <rootfiles>\n'
    '    <rootfile full-path="content.opf" '
    'media-type="application/oebps-package+xml"/>\n'
    '  </rootfiles>\n'
    '</container>\n'
)

_E2E_OPF = (
    '<?xml version="1.0" encoding="utf-8"?>\n'
    '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" '
    'unique-identifier="bookid">\n'
    '  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">\n'
    '    <dc:title>Test Book</dc:title>\n'
    '  </metadata>\n'
    '  <manifest>\n'
    f'    <item id="body" href="{_E2E_BODY_FILENAME}" '
    'media-type="application/xhtml+xml"/>\n'
    '  </manifest>\n'
    '  <spine>\n'
    '    <itemref idref="body"/>\n'
    '  </spine>\n'
    '</package>\n'
)

# No footnote markup anywhere (no FNanchor_N/Footnote_N ids) and, in the
# negative fixture below, no spine_overrides -- classify_spine_item's
# fallback then classifies this file "front-back", exactly issue #761's
# measured instance (a whole book collapsing to front-matter). Matches the
# minimal fixture extract_bodywalk_verse.test.py already confirms produces
# a clean, fully self-check-passing manifest when spine_overrides DOES
# force it to "body" (see that file's h2+comment/whitespace case).
_E2E_BODY_HTML = '<h2>Chapter</h2>\n<!-- editorial note --><p>Real chapter prose.</p>'


def _make_e2e_epub(epub_path: Path, body_inner_html: str) -> None:
    xhtml = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml">\n'
        '<head><title>x</title></head>\n'
        f'<body>\n{body_inner_html}\n</body>\n'
        '</html>\n'
    )
    with zipfile.ZipFile(epub_path, "w") as zf:
        zf.writestr("META-INF/container.xml", _E2E_CONTAINER_XML)
        zf.writestr("content.opf", _E2E_OPF)
        zf.writestr(_E2E_BODY_FILENAME, xhtml)


def _make_e2e_durable_root(tmp_path: Path, epub_path: Path, spine_overrides: dict) -> Path:
    """A full ${durable_root} main() can run against with NO monkeypatching:
    the copied extract.py.template, a full copy of the real scripts/
    directory (cache_key.py + its json_stdout.py sibling -- two_phase_write()
    shells out to it), the real manifest.schema.json, a minimal profile.yml
    satisfying load_profile()'s gutenberg_epub check and build()'s own
    source/project/footnotes reads, and the ownership marker
    _resolve_owner_profile_path() requires."""
    durable_root = tmp_path / "durable"
    durable_root.mkdir()
    shutil.copyfile(TEMPLATE_PATH, durable_root / "extract.py")
    shutil.copytree(SCRIPTS_SRC_DIR, durable_root / "scripts")
    (durable_root / "schemas").mkdir()
    shutil.copyfile(SCHEMA_PATH, durable_root / "schemas" / "manifest.schema.json")

    profile = {
        "source": {
            "format": "gutenberg_epub",
            "path": str(epub_path),
            "adapter_config": {"gutenberg_epub": {"spine_overrides": spine_overrides}},
        },
        "project": {"max_segment_words": 100000},
        "footnotes": {"apparatus_policy": "omit_apparatus"},
    }
    profile_path = durable_root / "profile.yml"
    profile_path.write_text(yaml.safe_dump(profile, sort_keys=False), encoding="utf-8")
    (durable_root / ".literary-translator-root.json").write_text(
        json.dumps({"owner_profile_path": str(profile_path)}), encoding="utf-8"
    )
    return durable_root


def _load_e2e_extract_module(durable_root: Path):
    extract_copy = durable_root / "extract.py"
    spec = importlib.util.spec_from_file_location("extract_e2e_under_test", extract_copy)
    assert spec is not None and spec.loader is not None, f"could not load spec for {extract_copy}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_main_refuses_zero_body_spine_instead_of_printing_all_pass(tmp_path, capsys):
    """The exact #761 scenario: an EPUB with no footnote markup and NO
    spine_overrides classifies its one spine file front-back, yielding
    n_body_files==0. main() must exit 1, name spine_yields_body_files as a
    FAIL line, and must NOT print ALL PASS -- today (before the main() fix)
    it does the opposite: exit 0 and print ALL PASS.

    Also pins the remedy prose itself, not just the check name -- and pins
    it as ONE CONTIGUOUS sentence, not scattered nouns. A review round
    demonstrated why: token-level assertions (the predicate phrase, the
    dotted path, the JSON example, checked as separate substrings) all pass
    against a message that INVERTS the instruction (e.g. "Never set
    spine_overrides ... delete extractor_path ..."), because every noun the
    scattered asserts were checking for is still present -- token
    membership cannot entail meaning. Asserting the whole remedy sentence
    verbatim, as one literal, pins every clause at once, including ones
    nobody thought to enumerate separately."""
    epub_path = tmp_path / "book.epub"
    _make_e2e_epub(epub_path, _E2E_BODY_HTML)
    durable_root = _make_e2e_durable_root(tmp_path, epub_path, spine_overrides={})
    module = _load_e2e_extract_module(durable_root)

    with pytest.raises(SystemExit) as exc_info:
        module.main()

    assert exc_info.value.code == 1, exc_info.value.code
    out = capsys.readouterr().out
    assert "FAIL  spine_yields_body_files" in out, out
    assert "ALL PASS" not in out, out
    # The complete failing-path remedy, verbatim, as one contiguous expected
    # string -- exactly what extract.py.template appends to
    # spine_yields_body_files_detail when n_body_files == 0. A single
    # membership check on the whole sentence, not separate asserts per noun.
    expected_remedy = (
        "no spine item was classified body. Set "
        "source.adapter_config.gutenberg_epub.spine_overrides "
        "(e.g. {\"content.xhtml\": \"body\"}) for the file(s) that carry the "
        "manuscript."
    )
    assert expected_remedy in out, out


def test_main_passes_and_prints_all_pass_when_spine_override_yields_a_body_file(tmp_path, capsys):
    """Positive control for the test above, without which the negative test
    could pass vacuously on an earlier missing-profile/scaffolding failure
    that looks identical to a real red: the SAME epub, with spine_overrides
    forcing the one file to 'body', must clear main() cleanly -- exit 0,
    ALL PASS printed, and no spine_yields_body_files FAIL line. Also pins the
    fix for the defect a review round caught by reading, not testing: the
    remedy prose ("produced no manuscript", "spine_overrides") must NOT
    appear on a healthy PASS line, where it would be false -- only the
    counts are unconditional."""
    epub_path = tmp_path / "book.epub"
    _make_e2e_epub(epub_path, _E2E_BODY_HTML)
    durable_root = _make_e2e_durable_root(
        tmp_path, epub_path, spine_overrides={_E2E_BODY_FILENAME: "body"}
    )
    module = _load_e2e_extract_module(durable_root)

    with pytest.raises(SystemExit) as exc_info:
        module.main()

    assert exc_info.value.code == 0, exc_info.value.code
    out = capsys.readouterr().out
    assert "ALL PASS" in out, out
    assert "FAIL  spine_yields_body_files" not in out, out
    assert "produced no manuscript" not in out, out
    assert "spine_overrides" not in out, out
