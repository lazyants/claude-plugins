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


def _manifest_with_extra_heading_block(raw_type: str, *, heading_types=None) -> dict:
    """Baseline manifest plus one extra schema-valid block of type
    ``raw_type``, cited by seg01's own ``block_ids`` (so it participates in
    the manifest like a real heading block would, without disturbing any
    other derivable check). Passing ``heading_types`` sets the manifest's own
    ``heading_types`` key to that value; leaving it ``None`` leaves the key
    OUT of the manifest entirely -- the #210 D2 trigger condition is the
    key's outright ABSENCE, never an empty/falsy value, so tests must be able
    to construct "key absent" and "key present but empty" as two distinct
    shapes."""
    m = _baseline_manifest()
    block_id = f"{raw_type}:seg01:0001"
    m["blocks"][block_id] = {
        "id": block_id, "type": raw_type, "order_index": 5,
        "source_file": "body.xhtml", "plain_text": "A sub-heading",
        "sha1": _sha1("A sub-heading"),
    }
    m["segments"][0]["block_ids"] = m["segments"][0]["block_ids"] + [block_id]
    if heading_types is not None:
        m["heading_types"] = heading_types
    return m


def _manifest_with_heading_levels(levels: dict) -> dict:
    """Baseline manifest (whose only heading block is HEAD, never itself
    heading-shaped per BROAD_HEADING_LIKE_RE) plus a ``heading_levels`` map --
    isolates schema/cross-field assertions about ``heading_levels`` from the
    #210 D2 undeclared-heading-type check."""
    m = _baseline_manifest()
    m["heading_levels"] = levels
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


def _write_profile(tmp_path: Path, apparatus_policy="omit_apparatus", max_segment_words=700,
                    source_format=None) -> Path:
    """``source_format`` defaults to omitted entirely (mirrors every
    pre-existing caller: no ``source`` key at all, exercising
    ``load_profile_values``'s ``profile.get("source")`` None-tolerance) --
    pass e.g. ``"custom"``/``"gutenberg_epub"`` for #180's region-pin
    format-gate tests below."""
    text = (
        "project:\n"
        f"  max_segment_words: {max_segment_words}\n"
        "footnotes:\n"
        f"  apparatus_policy: {apparatus_policy}\n"
    )
    if source_format is not None:
        text += f"source:\n  format: {source_format}\n"
    p = tmp_path / "profile.yml"
    p.write_text(text, encoding="utf-8")
    return p


def _write_extract(tmp_path: Path, body="    x = 1\n") -> tuple:
    text = _extract_text(body)
    p = tmp_path / "extract.py"
    p.write_text(text, encoding="utf-8")
    return p, text


def _run_gate(tmp_path, monkeypatch, manifest, *, apparatus_policy="omit_apparatus",
              max_segment_words=700, pinned_hash=None, extract_body="    x = 1\n",
              source_format=None):
    """Writes manifest/profile/extract to tmp_path, monkeypatches the pinned
    hash (defaulting to the fixture region's OWN hash so the pin passes
    independently of the LEAD's fill), runs main(), and returns the exit code.
    ``source_format`` threads through to ``_write_profile`` -- see #180's
    region-pin format-gate tests below."""
    manifest_path = _write_manifest(tmp_path, manifest)
    profile_path = _write_profile(tmp_path, apparatus_policy, max_segment_words, source_format)
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


# --- #180: source.format: custom skips the region pin entirely ---------------

def test_region_pin_skipped_for_custom_format(tmp_path, monkeypatch, capsys):
    """For source.format: custom, extract.py is Step 0a's unadapted
    extract.py.template copy -- never the real custom extractor
    (scripts/custom_extractors/<value>) that actually produced this
    manifest.json. Pinning the copy would only ever vacuously pass, so #180's
    fix SKIPS the pin outright: a deliberately MISMATCHED region hash must
    NOT fail the gate. Parts (a) schema validation and (b) derivable
    re-derivation still run against a clean, schema-valid, derivable-passing
    manifest -- exit 0 here proves those two ran and passed, not that the
    whole gate went inert. Red proof: before the format-gate this is the
    exact fixture shape of test_tampered_region_fails, which exits 1."""
    code = _run_gate(
        tmp_path, monkeypatch, _baseline_manifest(),
        source_format="custom",
        pinned_hash="deadbeefdeadbeefdeadbeefdeadbeefdeadbeef",  # deliberately mismatched
    )
    out, _err = capsys.readouterr()
    assert code == 0, out
    assert "skipped for source.format: custom" in out.lower(), out


def test_region_pin_still_enforced_for_gutenberg(tmp_path, monkeypatch):
    """CONVERSE: the skip applies ONLY to source.format: custom -- the SAME
    mismatched region hash under gutenberg_epub must still fail the gate.
    (An absent/unrecognized source_format -- every OTHER test in this file,
    which omits the source key entirely -- is already covered by
    test_tampered_region_fails; this test locks the explicit gutenberg_epub
    case specifically, alongside the new custom carve-out.)"""
    assert _run_gate(
        tmp_path, monkeypatch, _baseline_manifest(),
        source_format="gutenberg_epub",
        pinned_hash="deadbeefdeadbeefdeadbeefdeadbeefdeadbeef",
    ) == 1


def test_region_pin_skip_does_not_bypass_derivable_checks_for_custom(tmp_path, monkeypatch, capsys):
    """Guards against an over-broad fix that skips ALL checks for custom, not
    just the region pin: a manifest that fails a manifest-derivable invariant
    must still exit 1 for source.format: custom, even with a mismatched
    (would-be-skipped) region hash."""
    m = _baseline_manifest()
    m["spine"] = list(reversed(m["spine"]))
    code = _run_gate(
        tmp_path, monkeypatch, m,
        source_format="custom",
        pinned_hash="deadbeefdeadbeefdeadbeefdeadbeefdeadbeef",
    )
    out, _err = capsys.readouterr()
    assert code == 1, out


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


# ---------------------------------------------------------------------------
# #210 D2: fail-loud when heading_types is wholly ABSENT and a heading-shaped
# block type exists (heading_types_declared_when_heading_shaped_blocks_exist)
# ---------------------------------------------------------------------------

def test_undeclared_heading_shaped_block_fails_naming_type_and_both_remedies(tmp_path, monkeypatch, capsys):
    m = _manifest_with_extra_heading_block("CHAPTER")  # heading_types key absent
    code = _run_gate(tmp_path, monkeypatch, m)
    _out, err = capsys.readouterr()
    assert code == 1
    assert "CHAPTER" in err
    assert "heading_types" in err
    assert "[]" in err  # names the opt-out remedy, not just the declare-it remedy


def test_undeclared_heading_shaped_block_isolated_via_run_derivable_checks():
    m = _manifest_with_extra_heading_block("CHAPTER")
    results = ve.run_derivable_checks(m, "omit_apparatus", 700)
    assert _ok(results, "heading_types_declared_when_heading_shaped_blocks_exist") is False


def test_explicit_empty_heading_types_is_a_positive_optout_and_passes(tmp_path, monkeypatch):
    # An explicit [] is a DECLARATION ("this source has no heading blocks"),
    # not an absence -- must NOT trigger the check, even with a CHAPTER block.
    m = _manifest_with_extra_heading_block("CHAPTER", heading_types=[])
    assert _run_gate(tmp_path, monkeypatch, m) == 0


def test_declared_heading_type_passes(tmp_path, monkeypatch):
    m = _manifest_with_extra_heading_block("CHAPTER", heading_types=["CHAPTER"])
    assert _run_gate(tmp_path, monkeypatch, m) == 0


def test_gutenberg_shaped_manifest_head_only_no_heading_types_key_passes(tmp_path, monkeypatch):
    """The false-RED regression that matters most: every shipped
    gutenberg_epub/plain_text project is exactly this shape -- its only
    heading block is HEAD, and it never sets heading_types at all. "HEAD"
    does not match BROAD_HEADING_LIKE_RE (HEADING != HEAD, H[1-6] != HEAD),
    so this must stay green. If this test ever goes red, every shipped
    adapter project breaks."""
    m = _baseline_manifest()
    assert "heading_types" not in m
    assert _run_gate(tmp_path, monkeypatch, m) == 0


def test_case_insensitive_lowercase_chapter_still_trips_the_check():
    m = _manifest_with_extra_heading_block("chapter")
    results = ve.run_derivable_checks(m, "omit_apparatus", 700)
    assert _ok(results, "heading_types_declared_when_heading_shaped_blocks_exist") is False


def test_fullmatch_not_substring_particle_does_not_trip_part():
    m = _manifest_with_extra_heading_block("PARTICLE")
    results = ve.run_derivable_checks(m, "omit_apparatus", 700)
    assert _ok(results, "heading_types_declared_when_heading_shaped_blocks_exist") is True


def test_undeclared_heading_shaped_check_still_fires_for_custom_format(tmp_path, monkeypatch):
    """The self-check REGION PIN is skipped for source.format: custom
    (#180), but this manifest-derivable check is NOT a region-pin check --
    it must still fire, catching the defect immediately after extraction even
    for a co-designed custom extractor."""
    m = _manifest_with_extra_heading_block("CHAPTER")
    assert _run_gate(tmp_path, monkeypatch, m, source_format="custom") == 1


# ---------------------------------------------------------------------------
# #210 D1 -- heading_levels: schema acceptance/rejection driven through the
# real jsonschema.validate (validate_manifest_schema, called from main()),
# plus the cross-field guard that is NOT expressible in JSON Schema
# (heading_levels_keys_are_declared_heading_types).
# ---------------------------------------------------------------------------

def test_heading_levels_valid_map_is_schema_valid_and_gate_passes(tmp_path, monkeypatch):
    # "HEAD" is always a member of heading_types ∪ {"HEAD"}, regardless of
    # whether heading_types itself is set -- so this is valid on both axes.
    m = _manifest_with_heading_levels({"HEAD": 3})
    assert ve.validate_manifest_schema(m) == []
    assert _run_gate(tmp_path, monkeypatch, m) == 0


@pytest.mark.parametrize("bad_level", [0, 7, "2", True, 3.5])
def test_heading_levels_value_rejected_by_schema(tmp_path, monkeypatch, bad_level):
    m = _manifest_with_heading_levels({"HEAD": bad_level})
    assert ve.validate_manifest_schema(m) != []
    assert _run_gate(tmp_path, monkeypatch, m) == 1


def test_heading_levels_empty_string_key_rejected_by_schema(tmp_path, monkeypatch):
    m = _manifest_with_heading_levels({"": 2})
    assert ve.validate_manifest_schema(m) != []
    assert _run_gate(tmp_path, monkeypatch, m) == 1


def test_heading_levels_key_not_a_declared_heading_type_fails_cross_field_guard(tmp_path, monkeypatch):
    # "CHAPTER" is schema-valid (any non-empty string key, level in range) but
    # is not a member of heading_types ∪ {"HEAD"} here -- a typo that would
    # otherwise silently no-op in assemble.py. Only the cross-field guard,
    # not the schema, can reject it.
    m = _manifest_with_heading_levels({"CHAPTER": 2})
    assert ve.validate_manifest_schema(m) == []
    results = ve.run_derivable_checks(m, "omit_apparatus", 700)
    assert _ok(results, "heading_levels_keys_are_declared_heading_types") is False
    assert _run_gate(tmp_path, monkeypatch, m) == 1


def test_heading_levels_key_in_declared_heading_types_passes(tmp_path, monkeypatch):
    m = _manifest_with_heading_levels({"CHAPTER": 3})
    m["heading_types"] = ["CHAPTER"]
    results = ve.run_derivable_checks(m, "omit_apparatus", 700)
    assert _ok(results, "heading_levels_keys_are_declared_heading_types") is True
    assert _run_gate(tmp_path, monkeypatch, m) == 0
