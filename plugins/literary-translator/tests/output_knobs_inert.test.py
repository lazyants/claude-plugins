"""tests/output_knobs_inert.test.py -- the new Phase-A `output.*` profile
knobs (`output.target`, `output.v1_scope: assembled_book`,
`output.name_display.parenthetical_originals`, `output.index.*`,
`output.adapter_config`) are declared and shape-validated NOW, but the
assembler/adapters that would actually act on them don't exist until later
increments (Phase 0/1). This suite locks two things so that gap never
silently drifts:

1. Schema-shape assertions read `profile.schema.json` directly and assert
   every new knob's declared shape (enum members, boolean defaults,
   sub-object presence) -- these knobs are inert only in the sense that
   nothing downstream reads them yet; their SHAPE is real, enforced
   contract from day one.
2. The one place a new knob genuinely already changes validation behavior:
   `output.index.person_grouping: true` requires `output.index.enabled:
   true` (a grouped index with no index to group into is a validation
   error, not merely a documentation note). Driven through the plugin's
   own `profile_validate.py` validator (`validate_against_schema`), not
   raw jsonschema against a bare schema fragment, so this proves real
   Step-0 behavior -- exactly the same call
   tests/profile_validate.test.py's own `schema_errors()` helper makes.

This file deliberately does NOT re-test anything already covered by
tests/profile_validate.test.py (unknown-key rejection, placeholder
scanning, source/verse_policy conditionals, ...) -- scope here is strictly
the new `output.*` knob set.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
ASSETS_ROOT = PLUGIN_ROOT / "skills" / "literary-translator" / "assets"
SCHEMA_PATH = ASSETS_ROOT / "schemas" / "profile.schema.json"
PROFILE_VALIDATE_SCRIPT = ASSETS_ROOT / "scripts" / "profile_validate.py"

assert SCHEMA_PATH.is_file(), f"expected {SCHEMA_PATH} to exist"
assert PROFILE_VALIDATE_SCRIPT.is_file(), f"expected {PROFILE_VALIDATE_SCRIPT} to exist"


def _load_profile_validate_module():
    spec = importlib.util.spec_from_file_location(
        "profile_validate_under_test_output_knobs", PROFILE_VALIDATE_SCRIPT
    )
    assert spec is not None and spec.loader is not None, f"could not load spec for {PROFILE_VALIDATE_SCRIPT}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


pv = _load_profile_validate_module()
# Same recipe as tests/profile_validate.test.py: populate the module-level
# yaml/jsonschema handles the way main()'s step 2 would, against the real,
# installed dependencies -- not a mock.
pv.dependency_preflight()
SCHEMA = pv.load_profile_schema()


def schema_errors(profile):
    return pv.validate_against_schema(profile, SCHEMA)


def make_base_profile():
    """A fully schema-valid profile with no `output.*` knobs beyond the
    two that were always required (`v1_scope`, `destination`) -- mirrors
    tests/profile_validate.test.py's own fixture shape (kept local/
    self-contained per this codebase's convention of every test file
    owning its own fixture builder, rather than importing another test
    file). Individual tests deep-copy this and mutate exactly the
    `output.*` field(s) under test."""
    return {
        "profile_version": 1,
        "project": {
            "title": "A Real Book Title",
            "durable_root": "/some/real/project",
            "pipeline_version": "v1",
            "max_segment_words": 15000,
        },
        "source": {
            "format": "gutenberg_epub",
            "path": "/some/real/project/source.epub",
            "gutenberg_id": None,
            "language": {
                "code": "fr",
                "particle_config": "fr.json",
                "smoke_test": {"report_path": None},
            },
            "adapter_config": {
                "gutenberg_epub": {"spine_overrides": {}, "frontback_overrides": {}},
                "plain_text": {
                    "segmentation": {
                        "method": "blank_line_run",
                        "blank_line_threshold": 2,
                        "heading_regex": None,
                    },
                    "verse_detection": "none_confirmed",
                    "verse_regex": None,
                    "footnotes": "none_confirmed",
                    "footnote_anchor_regex": None,
                    "footnote_def_regex": None,
                },
                "custom": {"extractor_path": None},
            },
        },
        "target": {
            "language": {"code": "ru", "register_notes": "informal"},
        },
        "verse_policy": {"mode": "literal_only", "threshold_lines": None},
        "engine": {"effort": "high", "max_fix_rounds": 4, "batch_agent_cap": 1000},
        "footnotes": {"apparatus_policy": "translate_all"},
        "glossary": {"research_mode": "live"},
        "validation": {"untranslated_sentinel": "no translation"},
        "output": {
            "v1_scope": "segment_drafts_and_audit",
            "destination": "/some/real/project/out/",
        },
    }


def test_base_profile_is_schema_valid():
    """Harness self-check: every mutation test below deep-copies this, so
    the baseline itself must be clean or a "fails" assertion downstream
    could be hiding an unrelated pre-existing violation."""
    assert schema_errors(make_base_profile()) == []


# ---------------------------------------------------------------------------
# Schema-shape assertions (read profile.schema.json directly)
# ---------------------------------------------------------------------------


def _output_schema():
    raw = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    return raw["properties"]["output"]


def test_target_enum_declares_all_three_targets():
    target = _output_schema()["properties"]["target"]
    assert target["type"] == "string"
    assert set(target["enum"]) == {"obsidian", "epub", "custom"}


def test_v1_scope_enum_gains_assembled_book_alongside_the_original_value():
    v1_scope = _output_schema()["properties"]["v1_scope"]
    assert set(v1_scope["enum"]) == {"segment_drafts_and_audit", "assembled_book"}


def test_name_display_parenthetical_originals_enum():
    name_display = _output_schema()["properties"]["name_display"]
    parenthetical = name_display["properties"]["parenthetical_originals"]
    assert set(parenthetical["enum"]) == {"never", "first_occurrence"}


def test_index_enabled_and_person_grouping_are_booleans_defaulting_false():
    index_props = _output_schema()["properties"]["index"]["properties"]
    assert index_props["enabled"]["type"] == "boolean"
    assert index_props["enabled"]["default"] is False
    assert index_props["person_grouping"]["type"] == "boolean"
    assert index_props["person_grouping"]["default"] is False


def test_adapter_config_declares_all_three_target_sub_blocks():
    adapter_config = _output_schema()["properties"]["adapter_config"]
    assert set(adapter_config["properties"].keys()) == {"obsidian", "epub", "custom"}


# ---------------------------------------------------------------------------
# Behavioral: the person_grouping => enabled coupling, driven through the
# plugin's own real validator, not a bare schema fragment.
# ---------------------------------------------------------------------------


def test_person_grouping_true_with_enabled_false_fails_validation():
    profile = make_base_profile()
    profile["output"]["index"] = {"enabled": False, "person_grouping": True}
    errors = schema_errors(profile)
    assert errors != [], "person_grouping:true with enabled:false must be a validation error"
    assert any("enabled" in e for e in errors)


def test_person_grouping_true_with_enabled_true_passes():
    profile = make_base_profile()
    profile["output"]["index"] = {"enabled": True, "person_grouping": True}
    assert schema_errors(profile) == []


def test_person_grouping_false_with_enabled_true_passes():
    profile = make_base_profile()
    profile["output"]["index"] = {"enabled": True, "person_grouping": False}
    assert schema_errors(profile) == []


def test_all_default_case_with_no_index_block_at_all_passes():
    """The all-default case: a profile that never even mentions `output.index`
    (the base fixture) must pass -- the coupling rule must not force the
    block to be present."""
    assert schema_errors(make_base_profile()) == []


# ---------------------------------------------------------------------------
# Behavioral: the other new knobs validate for shape without requiring any
# further apparatus -- proving they are accepted now (inert) rather than
# rejected as "not yet supported".
# ---------------------------------------------------------------------------


def test_each_declared_target_value_passes_on_its_own():
    for target in ("obsidian", "epub", "custom"):
        profile = make_base_profile()
        profile["output"]["target"] = target
        assert schema_errors(profile) == [], f"output.target: {target!r} should validate cleanly"


def test_undeclared_target_value_fails():
    profile = make_base_profile()
    profile["output"]["target"] = "pdf"
    errors = schema_errors(profile)
    assert errors != []
    assert any("target" in e for e in errors)


def test_v1_scope_assembled_book_passes():
    profile = make_base_profile()
    profile["output"]["v1_scope"] = "assembled_book"
    assert schema_errors(profile) == []


def test_name_display_never_and_first_occurrence_both_pass():
    for value in ("never", "first_occurrence"):
        profile = make_base_profile()
        profile["output"]["name_display"] = {"parenthetical_originals": value}
        assert schema_errors(profile) == [], f"parenthetical_originals: {value!r} should validate cleanly"


def test_name_display_undeclared_value_fails():
    profile = make_base_profile()
    profile["output"]["name_display"] = {"parenthetical_originals": "always"}
    errors = schema_errors(profile)
    assert errors != []
    assert any("parenthetical_originals" in e for e in errors)


def test_adapter_config_with_all_three_sub_blocks_null_passes():
    profile = make_base_profile()
    profile["output"]["adapter_config"] = {"obsidian": None, "epub": None, "custom": None}
    assert schema_errors(profile) == []


def test_adapter_config_active_target_sub_block_may_be_populated_as_object():
    profile = make_base_profile()
    profile["output"]["target"] = "obsidian"
    profile["output"]["adapter_config"] = {
        "obsidian": {"folders": {"person": "people"}},
        "epub": None,
        "custom": None,
    }
    assert schema_errors(profile) == []


def test_adapter_config_rejects_an_unknown_target_key():
    profile = make_base_profile()
    profile["output"]["adapter_config"] = {"not_a_real_target": {}}
    errors = schema_errors(profile)
    assert errors != []
    assert any("not_a_real_target" in e for e in errors)


if __name__ == "__main__":
    import sys

    import pytest

    sys.exit(pytest.main([__file__, "-v"]))
