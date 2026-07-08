"""tests/output_adapter_schema_shape.test.py -- the Phase 0 profile.schema.json
additions under `output.adapter_config` (see
references/output-target-adapters/README.md): `custom.renderer_path`'s ACTUALLY-WIRED
pattern (unlike the source side's documented-but-unwired
`source.adapter_config.custom.extractor_path` drift -- see that field's own
schema entry, which carries no `pattern` at all) and `obsidian.folders`'s
open category->folder catalog shape.

This file deliberately does NOT re-test anything tests/output_knobs_inert.test.py
already covers (the target/v1_scope/name_display/index knobs, the general
three-sub-block adapter_config shape) -- scope here is strictly the two NEW
per-target sub-fields Phase 0 adds underneath `adapter_config.custom` and
`adapter_config.obsidian`.

Half of this file's job is documenting a DELIBERATE split: the schema layer
only ever enforces CHARACTER-CLASS shape (a `pattern` regex), never path
semantics like ".."-segment rejection or leading-"/" rejection -- those are
RUNTIME checks in output_resolve.py (contract §9), consistent with this
whole schema's own "shape/type/enum/conditional validation only" design
note. See tests/output_resolve.test.py for the runtime half.
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
        "profile_validate_under_test_adapter_shape", PROFILE_VALIDATE_SCRIPT
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


pv = _load_profile_validate_module()
pv.dependency_preflight()
SCHEMA = pv.load_profile_schema()


def schema_errors(profile):
    return pv.validate_against_schema(profile, SCHEMA)


def make_base_profile():
    """Mirrors tests/output_knobs_inert.test.py's own make_base_profile()
    (kept local/self-contained per this codebase's per-file-fixture
    convention) -- a fully schema-valid profile with output.target left at
    'obsidian' and adapter_config all-null, so individual tests only need
    to mutate the one adapter_config sub-block under test."""
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
        "target": {"language": {"code": "ru", "register_notes": "informal"}},
        "verse_policy": {"mode": "literal_only", "threshold_lines": None},
        "engine": {"effort": "high", "max_fix_rounds": 4, "batch_agent_cap": 1000},
        "footnotes": {"apparatus_policy": "translate_all"},
        "glossary": {"research_mode": "live"},
        "validation": {"untranslated_sentinel": "no translation"},
        "output": {
            "v1_scope": "assembled_book",
            "destination": "/some/real/project/out/",
            "target": "obsidian",
            "adapter_config": {"obsidian": None, "epub": None, "custom": None},
        },
    }


def test_base_profile_is_schema_valid():
    assert schema_errors(make_base_profile()) == []


# ---------------------------------------------------------------------------
# Schema-shape assertions (read profile.schema.json directly).
# ---------------------------------------------------------------------------


def _adapter_config_schema():
    raw = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    return raw["properties"]["output"]["properties"]["adapter_config"]


def test_custom_renderer_path_declares_the_documented_pattern():
    custom = _adapter_config_schema()["properties"]["custom"]
    renderer_path = custom["properties"]["renderer_path"]
    assert set(renderer_path["type"]) == {"string", "null"}
    assert renderer_path["pattern"] == r"^[A-Za-z0-9._/-]+$"


def test_obsidian_folders_declares_an_open_string_valued_map():
    obsidian = _adapter_config_schema()["properties"]["obsidian"]
    folders = obsidian["properties"]["folders"]
    assert folders["type"] == "object"
    assert folders["additionalProperties"]["type"] == "string"


# ---------------------------------------------------------------------------
# Behavioral, via the real validator (profile_validate.py's validate_against_schema).
# ---------------------------------------------------------------------------


def test_custom_renderer_path_accepts_a_plain_relative_filename():
    profile = make_base_profile()
    profile["output"]["target"] = "custom"
    profile["output"]["adapter_config"] = {
        "obsidian": None, "epub": None,
        "custom": {"renderer_path": "my_renderer.py"},
    }
    assert schema_errors(profile) == []


def test_custom_renderer_path_accepts_null():
    profile = make_base_profile()
    profile["output"]["adapter_config"] = {
        "obsidian": None, "epub": None, "custom": {"renderer_path": None},
    }
    assert schema_errors(profile) == []


def test_custom_renderer_path_rejects_a_shell_metacharacter():
    profile = make_base_profile()
    profile["output"]["target"] = "custom"
    profile["output"]["adapter_config"] = {
        "obsidian": None, "epub": None,
        "custom": {"renderer_path": "evil; rm -rf.py"},
    }
    errors = schema_errors(profile)
    assert errors != [], "a semicolon/space is outside the pattern's allowed character class"


def test_custom_renderer_path_schema_does_not_catch_path_traversal():
    """Deliberate split: '..' is composed entirely of characters the
    pattern DOES allow (dots and slashes are both in the character class),
    so this passes SCHEMA validation cleanly -- the '..'-segment rejection
    is output_resolve.py's own runtime job (contract §9), not this
    schema's. See tests/output_resolve.test.py for the runtime-side lock."""
    profile = make_base_profile()
    profile["output"]["target"] = "custom"
    profile["output"]["adapter_config"] = {
        "obsidian": None, "epub": None,
        "custom": {"renderer_path": "../escape.py"},
    }
    assert schema_errors(profile) == [], (
        "schema stays shape-only by design -- '..' rejection belongs to "
        "output_resolve.py's runtime check, not the schema"
    )


def test_obsidian_folders_accepts_a_populated_category_map():
    profile = make_base_profile()
    profile["output"]["adapter_config"] = {
        "obsidian": {"folders": {"person": "people", "place": "places", "divine-name": "divine"}},
        "epub": None, "custom": None,
    }
    assert schema_errors(profile) == []


def test_obsidian_folders_rejects_a_non_string_value():
    profile = make_base_profile()
    profile["output"]["adapter_config"] = {
        "obsidian": {"folders": {"person": 123}},
        "epub": None, "custom": None,
    }
    errors = schema_errors(profile)
    assert errors != [], "folders values must be strings, not arbitrary JSON"


if __name__ == "__main__":
    import sys

    import pytest

    sys.exit(pytest.main([__file__, "-v"]))
