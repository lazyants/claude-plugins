"""Tests for ``scripts/validate_extraction.py`` -- the managed post-extraction
gate (issue #86).

The gate's whole reason to exist is that ``extract.py`` runs its own round-trip
self-checks, so a hand-adapted extractor could weaken a check and still print
"all self-checks passed". These tests prove the gate closes that hole:

  * the DERIVABLE half is INDEPENDENTLY re-derived from ``manifest.json``, so a
    manifest that violates any invariant fails EVEN WHEN the self-check region
    hash matches (the bypass-closure proof -- the core of the file);
  * the region-hash PIN catches a tampered/absent self-check region;
  * usage/env problems exit 2, validation failures exit 1, a clean run exits 0.

The derivable-check tests do NOT depend on the LEAD's hash fill: they either
call ``run_derivable_checks`` directly, or monkeypatch
``CURRENT_EXTRACTOR_SELFCHECK_HASH`` to the hash of the fixture extract.py's own
region, so they pass regardless of the shipped placeholder.

The target script is loaded directly from its real location under
``skills/literary-translator/assets/scripts/`` via ``importlib`` -- like
``profile_validate.py`` it is never copied to a durable_root.
"""

import copy
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


def _sha1(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = (
    PLUGIN_ROOT
    / "skills"
    / "literary-translator"
    / "assets"
    / "scripts"
    / "validate_extraction.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location("validate_extraction", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None, f"could not load spec for {SCRIPT_PATH}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ve = _load_module()


# ---------------------------------------------------------------------------
# Manifest fixtures -- a schema-valid, self-check-clean baseline plus focused
# variants; every violation test deepcopies + perturbs exactly ONE thing.
# ---------------------------------------------------------------------------

def _baseline_manifest() -> dict:
    """Clean, all-derivable-checks-pass manifest under apparatus_policy
    ``omit_apparatus`` (no footnote apparatus, no verse)."""
    return {
        "blocks": {
            "HEAD:seg01": {
                "id": "HEAD:seg01", "type": "HEAD", "order_index": 0,
                "source_file": "body.xhtml", "plain_text": "Chapter One",
                "sha1": _sha1("Chapter One"),
            },
            "PARA:seg01:0001": {
                "id": "PARA:seg01:0001", "type": "PARA", "order_index": 1,
                "source_file": "body.xhtml", "plain_text": "Some body prose.",
                "sha1": _sha1("Some body prose."),
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
        },
        "source_inputs": ["book.epub"],
        "generation_hashes": {
            "source_extraction_hash": _sha1("source_extraction_hash fixture"),
            "source_input_hash": _sha1("source_input_hash fixture"),
        },
    }


def _manifest_translate_all() -> dict:
    """Clean manifest for the translate_all / preserve_source footnote branch:
    one footnote with a matching anchor+def, one FNREF sentinel in exactly one
    block."""
    m = _baseline_manifest()
    # A real FN definition block so def_block resolves under block_graph_integrity.
    m["blocks"]["Footnote_1"] = {
        "id": "Footnote_1", "type": "FN", "order_index": 4,
        "source_file": "notes.xhtml", "plain_text": "1. A footnote.",
        "sha1": _sha1("1. A footnote."),
    }
    m["footnotes"] = [{
        "n": 1, "anchor_block": "PARA:seg01:0001", "anchor_seg": "seg01",
        "def_block": "Footnote_1",
    }]
    m["blocks"]["PARA:seg01:0001"]["plain_text"] = "Some body prose ⟦FNREF_1⟧."
    m["blocks"]["PARA:seg01:0001"]["sha1"] = _sha1("Some body prose ⟦FNREF_1⟧.")
    return m


def _manifest_body_refs() -> dict:
    """Clean manifest for the body_refs_only branch: one well-formed body-ref
    marker."""
    m = _baseline_manifest()
    m["blocks"]["PARA:seg01:0001"]["plain_text"] = "Some body prose [1]."
    m["blocks"]["PARA:seg01:0001"]["sha1"] = _sha1("Some body prose [1].")
    return m


def _manifest_with_verse() -> dict:
    """Clean manifest carrying one block-level verse node -- reconciles."""
    m = _baseline_manifest()
    m["blocks"]["VERSE:seg01:0001"] = {
        "id": "VERSE:seg01:0001", "type": "VERSE", "order_index": 4,
        "source_file": "body.xhtml", "plain_text": "A line of verse",
        "sha1": _sha1("A line of verse"),
    }
    m["verse"] = {
        "store": [{
            "vid": "V1", "placeholder": "⟦VERSE_V1_deadbeef⟧", "context": "body",
            "parent_block": "PARA:seg01:0001", "plain_text": "A line of verse",
            "sha1": _sha1("A line of verse"), "mount": "body",
        }],
        "n_nodes": 1, "n_block": 1, "n_embedded": 0,
        "by_context": {"body": 1, "footnote": 0, "frontback": 0},
    }
    return m


def _ok(results, name):
    for n, ok, _ in results:
        if n == name:
            return ok
    raise AssertionError(f"check {name!r} was not run; ran {[n for n, _, _ in results]}")


def _names(results):
    return {n for n, _, _ in results}


# ---------------------------------------------------------------------------
# selfcheck_region_hash -- the normalization contract
# ---------------------------------------------------------------------------

def _extract_text(body: str) -> str:
    """A minimal extract.py string with a well-formed self-check region wrapping
    ``body`` (which must already end in a newline)."""
    return (
        "#!/usr/bin/env python3\n"
        "# EXTRACTOR_CONTRACT_VERSION: 2\n"
        f"{ve.BEGIN_SENTINEL_PREFIX} -- DO NOT EDIT (false-green anti-pattern)\n"
        f"{body}"
        f"{ve.END_SENTINEL_PREFIX}\n"
        "print('done')\n"
    )


def test_region_hash_ignores_trailing_whitespace_and_cr():
    a = ve.selfcheck_region_hash(_extract_text("    x = 1\n    y = 2\n"))
    b = ve.selfcheck_region_hash(_extract_text("    x = 1   \n    y = 2\t\r\n"))
    assert a is not None and a == b


def test_region_hash_changes_when_a_check_is_edited():
    a = ve.selfcheck_region_hash(_extract_text("    x = 1\n"))
    b = ve.selfcheck_region_hash(_extract_text("    x = 2\n"))
    assert a != b


def test_region_hash_none_when_sentinels_absent():
    assert ve.selfcheck_region_hash("print('no region')\n") is None


def test_region_hash_none_on_duplicate_begin():
    text = (
        f"{ve.BEGIN_SENTINEL_PREFIX} a\n    x = 1\n"
        f"{ve.BEGIN_SENTINEL_PREFIX} b\n"
        f"{ve.END_SENTINEL_PREFIX}\n"
    )
    assert ve.selfcheck_region_hash(text) is None


def test_region_hash_none_when_end_precedes_begin():
    text = (
        f"{ve.END_SENTINEL_PREFIX}\n    x = 1\n{ve.BEGIN_SENTINEL_PREFIX}\n"
    )
    assert ve.selfcheck_region_hash(text) is None


# ---------------------------------------------------------------------------
# run_derivable_checks -- clean baselines pass; apparatus branches route right
# ---------------------------------------------------------------------------

def test_clean_omit_apparatus_all_pass():
    failed = [(n, d) for n, ok, d in ve.run_derivable_checks(_baseline_manifest(), "omit_apparatus", 700) if not ok]
    assert failed == [], failed


def test_clean_translate_all_pass():
    failed = [(n, d) for n, ok, d in ve.run_derivable_checks(_manifest_translate_all(), "translate_all", 700) if not ok]
    assert failed == [], failed


def test_clean_preserve_source_pass():
    failed = [(n, d) for n, ok, d in ve.run_derivable_checks(_manifest_translate_all(), "preserve_source", 700) if not ok]
    assert failed == [], failed


def test_clean_body_refs_only_pass():
    failed = [(n, d) for n, ok, d in ve.run_derivable_checks(_manifest_body_refs(), "body_refs_only", 700) if not ok]
    assert failed == [], failed


def test_clean_verse_pass():
    failed = [(n, d) for n, ok, d in ve.run_derivable_checks(_manifest_with_verse(), "omit_apparatus", 700) if not ok]
    assert failed == [], failed


def test_translate_all_branch_runs_footnote_checks():
    names = _names(ve.run_derivable_checks(_manifest_translate_all(), "translate_all", 700))
    assert {"fn_bijection", "fnref_sentinel_unique"} <= names
    assert "body_ref_markers_well_formed_and_unique" not in names
    assert "footnote_checks_not_applicable" not in names


def test_preserve_source_branch_runs_footnote_checks():
    names = _names(ve.run_derivable_checks(_manifest_translate_all(), "preserve_source", 700))
    assert {"fn_bijection", "fnref_sentinel_unique"} <= names


def test_body_refs_only_branch_runs_marker_check():
    names = _names(ve.run_derivable_checks(_manifest_body_refs(), "body_refs_only", 700))
    assert "body_ref_markers_well_formed_and_unique" in names
    assert "fn_bijection" not in names and "fnref_sentinel_unique" not in names


def test_omit_apparatus_branch_marks_not_applicable():
    results = ve.run_derivable_checks(_baseline_manifest(), "omit_apparatus", 700)
    assert _ok(results, "footnote_checks_not_applicable") is True
    names = _names(results)
    assert "fn_bijection" not in names
    assert "body_ref_markers_well_formed_and_unique" not in names


# ---------------------------------------------------------------------------
# run_derivable_checks -- each derivable invariant individually violated
# ---------------------------------------------------------------------------

def test_block_ids_unique_violation():
    m = _baseline_manifest()
    # second dict key, same inner "id" -> len(blocks) > distinct ids. NOTE: a
    # duplicate id can only arise when some id != its key, so block_graph_
    # integrity necessarily co-fires -- block_ids_unique cannot be isolated (it
    # is subsumed by integrity). We assert only its own target here.
    m["blocks"]["PARA:seg01:0002"] = copy.deepcopy(m["blocks"]["PARA:seg01:0001"])
    results = ve.run_derivable_checks(m, "omit_apparatus", 700)
    assert _ok(results, "block_ids_unique") is False
    assert _ok(results, "block_graph_integrity") is False


def test_block_graph_integrity_id_key_mismatch():
    # inner id != dict key; the key is still unique, so block_ids_unique STAYS
    # green -- isolating block_graph_integrity's id-vs-key clause.
    m = _baseline_manifest()
    m["blocks"]["PARA:seg01:0001"]["id"] = "PARA:mismatch"
    results = ve.run_derivable_checks(m, "omit_apparatus", 700)
    assert _ok(results, "block_graph_integrity") is False
    assert _ok(results, "block_ids_unique") is True


def test_block_graph_integrity_dangling_segment_ref():
    m = _baseline_manifest()
    m["segments"][0]["block_ids"] = m["segments"][0]["block_ids"] + ["PARA:does-not-exist"]
    assert _ok(ve.run_derivable_checks(m, "omit_apparatus", 700), "block_graph_integrity") is False


def test_block_graph_integrity_dangling_footnote_def():
    m = _manifest_translate_all()
    m["footnotes"][0]["def_block"] = "Footnote_DOES_NOT_EXIST"
    results = ve.run_derivable_checks(m, "translate_all", 700)
    assert _ok(results, "block_graph_integrity") is False
    # fn_bijection still passes (a truthy, non-resolving def_block still counts)
    assert _ok(results, "fn_bijection") is True


def test_block_graph_integrity_dangling_verse_parent():
    m = _manifest_with_verse()
    m["verse"]["store"][0]["parent_block"] = "PARA:ghost"
    assert _ok(ve.run_derivable_checks(m, "omit_apparatus", 700), "block_graph_integrity") is False


def test_block_graph_integrity_empty_sentinel_refs_pass():
    # the "" sentinel (dangling footnote def / never-mounted verse) must NOT be
    # treated as a dangling block reference.
    m = _manifest_with_verse()
    m["footnotes"] = [{"n": 1, "anchor_block": "PARA:seg01:0001", "anchor_seg": "seg01", "def_block": ""}]
    m["verse"]["store"][0]["parent_block"] = ""
    assert _ok(ve.run_derivable_checks(m, "translate_all", 700), "block_graph_integrity") is True


def test_spine_order_violation():
    m = _baseline_manifest()
    m["spine"] = list(reversed(m["spine"]))
    assert _ok(ve.run_derivable_checks(m, "omit_apparatus", 700), "spine_order_preserved") is False


def test_segmentation_nonempty_violation():
    m = _baseline_manifest()
    # a heading-only body segment (real, resolvable block; zero para/verse/quote)
    m["blocks"]["HEAD:seg02"] = {
        "id": "HEAD:seg02", "type": "HEAD", "order_index": 4,
        "source_file": "body.xhtml", "plain_text": "Empty Chapter",
        "sha1": _sha1("Empty Chapter"),
    }
    m["segments"].append({
        "seg": "seg02", "kind": "body", "block_ids": ["HEAD:seg02"], "word_count": 0,
        "n_para": 0, "n_verse": 0, "n_quote": 0, "source_files": ["body.xhtml"],
    })
    assert _ok(ve.run_derivable_checks(m, "omit_apparatus", 700), "segmentation_nonempty") is False


def test_body_files_yield_segments_violation():
    m = _baseline_manifest()
    # spine still has a body file, but drop every body segment
    m["segments"] = [s for s in m["segments"] if s["kind"] != "body"]
    assert _ok(ve.run_derivable_checks(m, "omit_apparatus", 700), "body_files_yield_segments") is False


def _add_pseudo_notes_segment(m: dict) -> dict:
    """A schema-valid body segment sourced ENTIRELY from a footnote-defs spine
    file, with a real resolvable block -- triggers ONLY no_pseudo_segments_from_
    notes (not schema minItems, not block_graph_integrity)."""
    m["spine"].append({"pos": 2, "file": "notes.xhtml", "klass": "footnote-defs"})
    m["blocks"]["PARA:seg99:0001"] = {
        "id": "PARA:seg99:0001", "type": "PARA", "order_index": 4,
        "source_file": "notes.xhtml", "plain_text": "note prose",
        "sha1": _sha1("note prose"),
    }
    m["segments"].append({
        "seg": "seg99", "kind": "body", "block_ids": ["PARA:seg99:0001"], "word_count": 2,
        "n_para": 1, "n_verse": 0, "n_quote": 0, "source_files": ["notes.xhtml"],
    })
    return m


def test_no_pseudo_segments_from_notes_violation():
    m = _add_pseudo_notes_segment(_baseline_manifest())
    assert _ok(ve.run_derivable_checks(m, "omit_apparatus", 700), "no_pseudo_segments_from_notes") is False


def test_fn_bijection_violation():
    m = _manifest_translate_all()
    # def_block "" is the schema-valid representation of a footnote with no
    # definition (dangling anchor) -- falsy, so it drops out of `defs`.
    m["footnotes"] = [{"n": 1, "anchor_block": "PARA:seg01:0001", "anchor_seg": "seg01", "def_block": ""}]
    results = ve.run_derivable_checks(m, "translate_all", 700)
    assert _ok(results, "fn_bijection") is False
    # the sibling fnref check must remain green -- the violation is isolated
    assert _ok(results, "fnref_sentinel_unique") is True


def test_fnref_sentinel_unique_violation():
    m = _manifest_translate_all()
    m["blocks"]["PARA:seg01:0001"]["plain_text"] = "prose ⟦FNREF_1⟧ ⟦FNREF_1⟧"
    m["blocks"]["PARA:seg01:0001"]["sha1"] = _sha1("prose ⟦FNREF_1⟧ ⟦FNREF_1⟧")
    results = ve.run_derivable_checks(m, "translate_all", 700)
    assert _ok(results, "fnref_sentinel_unique") is False
    assert _ok(results, "fn_bijection") is True


def test_body_ref_markers_violation():
    m = _manifest_body_refs()
    m["blocks"]["PARA:seg01:0001"]["plain_text"] = "prose [1] and again [1]."
    assert _ok(ve.run_derivable_checks(m, "body_refs_only", 700), "body_ref_markers_well_formed_and_unique") is False


def test_frontback_inventory_missing_reason_violation():
    m = _baseline_manifest()
    m["frontback"][1]["reason"] = ""
    assert _ok(ve.run_derivable_checks(m, "omit_apparatus", 700), "frontback_inventory") is False


def test_frontback_inventory_leaked_into_segments_violation():
    m = _baseline_manifest()
    # an omit-decision frontback id leaked into the segment list
    m["segments"].append({
        "seg": "FRONTBACK:fm02", "kind": "frontback",
        "block_ids": ["FRONTBACK:fm02"], "word_count": 3, "source_files": ["front.xhtml"],
    })
    assert _ok(ve.run_derivable_checks(m, "omit_apparatus", 700), "frontback_inventory") is False


def test_verse_unmounted_violation():
    m = _manifest_with_verse()
    m["verse"]["store"][0]["parent_block"] = ""  # schema-valid falsy = never mounted
    assert _ok(ve.run_derivable_checks(m, "omit_apparatus", 700), "verse_placeholders_unique_and_mounted") is False


def test_verse_plain_text_empty_violation():
    m = _manifest_with_verse()
    m["verse"]["store"][0]["plain_text"] = "   "
    assert _ok(ve.run_derivable_checks(m, "omit_apparatus", 700), "verse_plain_text_nonempty") is False


def test_no_segment_exceeds_max_words_violation():
    # baseline seg01 has word_count 4; a max of 2 makes it an offender
    assert _ok(ve.run_derivable_checks(_baseline_manifest(), "omit_apparatus", 2), "no_segment_exceeds_max_words") is False


def test_verse_counts_reconcile_sum_violation():
    m = _manifest_with_verse()
    m["verse"]["n_nodes"] = 2  # n_block + n_embedded (1) no longer equals n_nodes
    assert _ok(ve.run_derivable_checks(m, "omit_apparatus", 700), "verse_counts_reconcile") is False


def test_verse_counts_reconcile_rederived_block_count_violation():
    # The persisted n_block claims 1 and n_nodes agrees (1) -- but there is NO
    # VERSE-typed block, so the RE-DERIVED count is 0. A gate that trusted the
    # scalar would pass; independent re-derivation must fail.
    m = _manifest_with_verse()
    del m["blocks"]["VERSE:seg01:0001"]
    assert _ok(ve.run_derivable_checks(m, "omit_apparatus", 700), "verse_counts_reconcile") is False


# ---------------------------------------------------------------------------
# #93 Fix B gate mirror -- an embedded verse's own FNREF/body-ref sentinel
# lives ONLY on verse.store[].plain_text (the carrier block's own plain_text
# holds just the ⟦VERSE_...⟧ placeholder), so fnref_sentinel_unique /
# body_ref_markers_well_formed_and_unique must scan verse_store too -- byte-
# mirroring extract.py.template's in-region #93 Fix B scan.
# ---------------------------------------------------------------------------

def _manifest_embedded_verse_fnref_translate_all() -> dict:
    """Clean manifest: one embedded verse (mount=="embedded") whose OWN
    plain_text carries the footnote's sole FNREF citation -- the carrier
    block's plain_text holds only the VERSE placeholder (mirrors #93 Fix A
    mounting; the carrier's own fnrefs never see the verse's FNREF)."""
    m = _manifest_translate_all()
    m["blocks"]["PARA:seg01:0001"]["plain_text"] = "Some body prose ⟦VERSE_V1_deadbeef⟧."
    m["blocks"]["PARA:seg01:0001"]["sha1"] = _sha1(m["blocks"]["PARA:seg01:0001"]["plain_text"])
    m["verse"] = {
        "store": [{
            "vid": "V1", "placeholder": "⟦VERSE_V1_deadbeef⟧", "context": "body",
            "mount": "embedded", "parent_block": "PARA:seg01:0001",
            "plain_text": "A line of verse ⟦FNREF_1⟧",
            "sha1": _sha1("A line of verse ⟦FNREF_1⟧"),
        }],
        "n_nodes": 1, "n_block": 0, "n_embedded": 1,
        "by_context": {"body": 1, "footnote": 0, "frontback": 0},
    }
    return m


def test_embedded_verse_fnref_counted_translate_all():
    m = _manifest_embedded_verse_fnref_translate_all()
    results = ve.run_derivable_checks(m, "translate_all", 700)
    assert _ok(results, "fn_bijection") is True
    assert _ok(results, "fnref_sentinel_unique") is True


def test_embedded_verse_fnref_duplicate_across_blocks_is_caught():
    """Negative twin: the SAME n also cited in the carrier block's OWN text
    (alongside the verse placeholder) must trip the cross-block-duplicate
    detection -- proving the embedded occurrence is really aggregated into the
    SAME ref_count/ref_block as the block scan, not tracked separately."""
    m = _manifest_embedded_verse_fnref_translate_all()
    m["blocks"]["PARA:seg01:0001"]["plain_text"] = "Some body prose ⟦VERSE_V1_deadbeef⟧ ⟦FNREF_1⟧."
    m["blocks"]["PARA:seg01:0001"]["sha1"] = _sha1(m["blocks"]["PARA:seg01:0001"]["plain_text"])
    results = ve.run_derivable_checks(m, "translate_all", 700)
    assert _ok(results, "fnref_sentinel_unique") is False


def _manifest_embedded_verse_body_ref() -> dict:
    """Clean manifest for the body_refs_only branch: one embedded verse whose
    OWN plain_text carries a literal `[1]` body-ref marker."""
    m = _manifest_body_refs()
    m["blocks"]["PARA:seg01:0001"]["plain_text"] = "Some body prose ⟦VERSE_V1_deadbeef⟧."
    m["blocks"]["PARA:seg01:0001"]["sha1"] = _sha1(m["blocks"]["PARA:seg01:0001"]["plain_text"])
    m["verse"] = {
        "store": [{
            "vid": "V1", "placeholder": "⟦VERSE_V1_deadbeef⟧", "context": "body",
            "mount": "embedded", "parent_block": "PARA:seg01:0001",
            "plain_text": "A line citing [1]", "sha1": _sha1("A line citing [1]"),
        }],
        "n_nodes": 1, "n_block": 0, "n_embedded": 1,
        "by_context": {"body": 1, "footnote": 0, "frontback": 0},
    }
    return m


def test_embedded_verse_body_ref_marker_counted_body_refs_only():
    m = _manifest_embedded_verse_body_ref()
    assert _ok(
        ve.run_derivable_checks(m, "body_refs_only", 700), "body_ref_markers_well_formed_and_unique"
    ) is True


def test_embedded_verse_body_ref_marker_duplicate_across_blocks_is_caught():
    m = _manifest_embedded_verse_body_ref()
    m["blocks"]["PARA:seg01:0001"]["plain_text"] = "Some body prose [1]."
    m["blocks"]["PARA:seg01:0001"]["sha1"] = _sha1(m["blocks"]["PARA:seg01:0001"]["plain_text"])
    assert _ok(
        ve.run_derivable_checks(m, "body_refs_only", 700), "body_ref_markers_well_formed_and_unique"
    ) is False


def test_unmounted_embedded_fnref_surfaces_as_unmounted_not_typeerror():
    """#93 Fix B None-guard (finding-3 methodology -- an INTERMEDIATE-MUTATION
    checkpoint, NOT RED-on-pristine): an UNMOUNTED embedded verse entry
    (parent_block=None) carrying an FNREF, alongside a real block citing the
    SAME n directly, must surface as verse_placeholders_unique_and_mounted
    failing -- run_derivable_checks must RETURN (never raise a TypeError from
    sorted({str, None})), and fnref_sentinel_unique (which never sees the
    unmounted entry, guarded at the source) stays green."""
    m = _manifest_embedded_verse_fnref_translate_all()
    m["verse"]["store"][0]["parent_block"] = None
    m["blocks"]["PARA:seg01:0001"]["plain_text"] = "Some body prose ⟦FNREF_1⟧."
    m["blocks"]["PARA:seg01:0001"]["sha1"] = _sha1(m["blocks"]["PARA:seg01:0001"]["plain_text"])
    results = ve.run_derivable_checks(m, "translate_all", 700)
    assert _ok(results, "verse_placeholders_unique_and_mounted") is False
    assert _ok(results, "fnref_sentinel_unique") is True


# ---------------------------------------------------------------------------
# End-to-end through main(): exit codes + the bypass-closure proof
# ---------------------------------------------------------------------------

def _write_manifest(tmp_path: Path, manifest: dict) -> Path:
    p = tmp_path / "manifest.json"
    p.write_text(json.dumps(manifest), encoding="utf-8")
    return p


def _write_profile(tmp_path: Path, apparatus_policy="omit_apparatus", max_segment_words=700) -> Path:
    p = tmp_path / "profile.yml"
    p.write_text(
        "project:\n"
        f"  max_segment_words: {max_segment_words}\n"
        "footnotes:\n"
        f"  apparatus_policy: {apparatus_policy}\n",
        encoding="utf-8",
    )
    return p


def _write_extract(tmp_path: Path, body="    x = 1\n") -> tuple:
    text = _extract_text(body)
    p = tmp_path / "extract.py"
    p.write_text(text, encoding="utf-8")
    return p, text


def _run_gate(tmp_path, monkeypatch, manifest, *, apparatus_policy="omit_apparatus",
              max_segment_words=700, pinned_hash=None, extract_body="    x = 1\n"):
    """Writes manifest/profile/extract to tmp_path, monkeypatches the pinned
    hash (defaulting to the fixture region's OWN hash so the pin passes
    independently of the LEAD's fill), runs main(), and returns the exit code."""
    manifest_path = _write_manifest(tmp_path, manifest)
    profile_path = _write_profile(tmp_path, apparatus_policy, max_segment_words)
    extract_path, text = _write_extract(tmp_path, extract_body)
    monkeypatch.setattr(
        ve, "CURRENT_EXTRACTOR_SELFCHECK_HASH",
        pinned_hash if pinned_hash is not None else ve.selfcheck_region_hash(text),
    )
    with pytest.raises(SystemExit) as exc:
        ve.main([
            "--manifest", str(manifest_path),
            "--extract", str(extract_path),
            "--profile", str(profile_path),
        ])
    return exc.value.code


def test_gate_clean_manifest_matching_region_exits_zero(tmp_path, monkeypatch):
    assert _run_gate(tmp_path, monkeypatch, _baseline_manifest()) == 0


# --- bypass-closure: a violating manifest fails EVEN with a matching region ---

def test_bypass_spine_order(tmp_path, monkeypatch):
    m = _baseline_manifest()
    m["spine"] = list(reversed(m["spine"]))
    assert _run_gate(tmp_path, monkeypatch, m) == 1


def test_bypass_no_pseudo_segments_from_notes(tmp_path, monkeypatch):
    # schema-valid (resolvable block_ids >=1) so the gate's exit 1 proves
    # no_pseudo_segments_from_notes fired, not schema validation (#4 was vacuous).
    m = _add_pseudo_notes_segment(_baseline_manifest())
    assert _run_gate(tmp_path, monkeypatch, m) == 1


def test_bypass_block_graph_integrity_dangling_seg_ref(tmp_path, monkeypatch):
    # schema-valid (block_ids is array-of-string, minItems satisfied) but the id
    # does not resolve -- only block_graph_integrity can reject it.
    m = _baseline_manifest()
    m["segments"][0]["block_ids"] = m["segments"][0]["block_ids"] + ["PARA:does-not-exist"]
    assert _run_gate(tmp_path, monkeypatch, m) == 1


def test_bypass_body_ref_markers_under_body_refs_only(tmp_path, monkeypatch):
    m = _manifest_body_refs()
    m["blocks"]["PARA:seg01:0001"]["plain_text"] = "prose [1] and again [1]."
    assert _run_gate(tmp_path, monkeypatch, m, apparatus_policy="body_refs_only") == 1


def test_bypass_verse_counts_reconcile(tmp_path, monkeypatch):
    m = _manifest_with_verse()
    del m["blocks"]["VERSE:seg01:0001"]  # re-derived VERSE-block count now 0, scalar says 1
    assert _run_gate(tmp_path, monkeypatch, m) == 1


def test_bypass_fn_bijection_under_translate_all(tmp_path, monkeypatch):
    m = _manifest_translate_all()
    m["footnotes"] = [{"n": 1, "anchor_block": "PARA:seg01:0001", "anchor_seg": "seg01", "def_block": ""}]
    assert _run_gate(tmp_path, monkeypatch, m, apparatus_policy="translate_all") == 1


def test_bypass_no_segment_exceeds_max_words(tmp_path, monkeypatch):
    assert _run_gate(tmp_path, monkeypatch, _baseline_manifest(), max_segment_words=2) == 1


# --- region-pin: a tampered / absent / drifted region fails -------------------

def test_tampered_region_fails(tmp_path, monkeypatch):
    # pin to the hash of an UNEDITED region, then ship an edited one
    original = ve.selfcheck_region_hash(_extract_text("    x = 1\n    y = 2\n"))
    assert _run_gate(
        tmp_path, monkeypatch, _baseline_manifest(),
        pinned_hash=original, extract_body="    x = 1\n    y = 999  # weakened\n",
    ) == 1


def test_absent_region_fails(tmp_path, monkeypatch):
    manifest_path = _write_manifest(tmp_path, _baseline_manifest())
    profile_path = _write_profile(tmp_path)
    extract_path = tmp_path / "extract.py"
    extract_path.write_text("#!/usr/bin/env python3\nprint('no region here')\n", encoding="utf-8")
    monkeypatch.setattr(ve, "CURRENT_EXTRACTOR_SELFCHECK_HASH", "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef")
    with pytest.raises(SystemExit) as exc:
        ve.main([
            "--manifest", str(manifest_path),
            "--extract", str(extract_path),
            "--profile", str(profile_path),
        ])
    assert exc.value.code == 1


def test_pending_lead_fill_placeholder_fails_closed(tmp_path, monkeypatch):
    # With the shipped un-provisioned placeholder, even a clean build cannot
    # certify green -- the gate fails closed until the LEAD fills the hash.
    manifest_path = _write_manifest(tmp_path, _baseline_manifest())
    profile_path = _write_profile(tmp_path)
    extract_path, _ = _write_extract(tmp_path)
    monkeypatch.setattr(ve, "CURRENT_EXTRACTOR_SELFCHECK_HASH", "PENDING_LEAD_FILL")
    with pytest.raises(SystemExit) as exc:
        ve.main([
            "--manifest", str(manifest_path),
            "--extract", str(extract_path),
            "--profile", str(profile_path),
        ])
    assert exc.value.code == 1


# --- usage / environment errors -> exit 2 -------------------------------------

def test_no_args_exits_two():
    with pytest.raises(SystemExit) as exc:
        ve.main([])
    assert exc.value.code == 2


def test_unreadable_manifest_exits_two(tmp_path):
    with pytest.raises(SystemExit) as exc:
        ve.main([
            "--manifest", str(tmp_path / "nope.json"),
            "--extract", str(tmp_path / "extract.py"),
            "--profile", str(tmp_path / "profile.yml"),
        ])
    assert exc.value.code == 2


def test_malformed_json_manifest_exits_two(tmp_path):
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text("{ not json", encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        ve.main([
            "--manifest", str(manifest_path),
            "--extract", str(tmp_path / "extract.py"),
            "--profile", str(tmp_path / "profile.yml"),
        ])
    assert exc.value.code == 2


def test_unreadable_extract_exits_two(tmp_path):
    manifest_path = _write_manifest(tmp_path, _baseline_manifest())
    profile_path = _write_profile(tmp_path)
    with pytest.raises(SystemExit) as exc:
        ve.main([
            "--manifest", str(manifest_path),
            "--extract", str(tmp_path / "nope.py"),
            "--profile", str(profile_path),
        ])
    assert exc.value.code == 2


def test_profile_missing_key_exits_two(tmp_path):
    manifest_path = _write_manifest(tmp_path, _baseline_manifest())
    extract_path, _ = _write_extract(tmp_path)
    profile_path = tmp_path / "profile.yml"
    profile_path.write_text("project:\n  max_segment_words: 700\n", encoding="utf-8")  # no footnotes
    with pytest.raises(SystemExit) as exc:
        ve.main([
            "--manifest", str(manifest_path),
            "--extract", str(extract_path),
            "--profile", str(profile_path),
        ])
    assert exc.value.code == 2


def test_profile_unknown_apparatus_policy_exits_two(tmp_path):
    manifest_path = _write_manifest(tmp_path, _baseline_manifest())
    extract_path, _ = _write_extract(tmp_path)
    profile_path = _write_profile(tmp_path, apparatus_policy="translate_everything")
    with pytest.raises(SystemExit) as exc:
        ve.main([
            "--manifest", str(manifest_path),
            "--extract", str(extract_path),
            "--profile", str(profile_path),
        ])
    assert exc.value.code == 2


# --- independent manifest schema validation (#3): a structurally-invalid ------
# manifest is a validation failure (exit 1), NOT a false-green exit 0 ----------

def test_baseline_fixtures_are_schema_valid():
    """Guards the invariant every bypass test relies on: the fixtures are
    schema-valid, so a bypass test that reaches exit 1 is proving the DERIVABLE
    check fired, not that schema validation tripped on a malformed fixture."""
    ve._dependency_preflight()
    for m in (_baseline_manifest(), _manifest_translate_all(), _manifest_body_refs(), _manifest_with_verse()):
        assert ve.validate_manifest_schema(m) == []


def test_broken_bundled_schema_exits_two(monkeypatch):
    # A structurally-invalid BUNDLED schema is a broken install (env error, exit
    # 2), not a manifest failure and not a traceback.
    ve._dependency_preflight()
    monkeypatch.setattr(ve, "_load_manifest_schema", lambda: {"type": "bogus-type"})
    with pytest.raises(SystemExit) as exc:
        ve.validate_manifest_schema(_baseline_manifest())
    assert exc.value.code == 2


def test_manifest_missing_source_inputs_exits_one(tmp_path, monkeypatch):
    # `source_inputs` is a required top-level key that NO derivable check reads
    # -- without schema validation this manifest would pass every check and exit
    # 0 (the exact false-green #3 closes). With it, the gate exits 1.
    m = _baseline_manifest()
    del m["source_inputs"]
    assert _run_gate(tmp_path, monkeypatch, m) == 1


def test_manifest_missing_generation_hashes_exits_one(tmp_path, monkeypatch):
    m = _baseline_manifest()
    del m["generation_hashes"]
    assert _run_gate(tmp_path, monkeypatch, m) == 1


def test_manifest_stray_top_level_key_exits_one(tmp_path, monkeypatch):
    # additionalProperties:false -- a stray top-level key is schema-invalid
    m = _baseline_manifest()
    m["surprise"] = {"unexpected": True}
    assert _run_gate(tmp_path, monkeypatch, m) == 1


def test_manifest_missing_required_top_level_key_exits_one(tmp_path, monkeypatch):
    m = _baseline_manifest()
    del m["verse"]  # a required top-level key -> schema-invalid
    assert _run_gate(tmp_path, monkeypatch, m) == 1


def test_schema_valid_but_check_field_missing_exits_one(tmp_path, monkeypatch):
    # A body segment lacking n_para is schema-VALID (that field is optional) but
    # the segmentation check reads it -- the structural-guard reports a single
    # FATAL validation failure (exit 1) rather than crashing.
    m = _baseline_manifest()
    del m["segments"][0]["n_para"]
    assert _run_gate(tmp_path, monkeypatch, m) == 1
