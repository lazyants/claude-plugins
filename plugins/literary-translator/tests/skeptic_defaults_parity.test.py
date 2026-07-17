"""Parity gate: the JSON-Schema ``default:`` values under profile.schema.json
``glossary.skeptic_pass`` MUST equal skeptic_constants.py's authoritative
``*_DEFAULT`` constants.

Why this test exists: ``profile_validate.py`` does NOT materialize JSON-Schema
defaults (it validates shape only), so skeptic_constants.py is the real runtime
default source; the schema ``default:`` values are documentation for a human
reading the profile schema. If the two drift, a maintainer reading the schema
would believe a default the code does not actually use. This test fails LOUD on
any drift.

Standalone-script loader mirrors tests/occ_index.test.py:1-45.
"""
import importlib.util
import json
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
ASSETS_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets"
SCRIPTS_DIR = ASSETS_DIR / "scripts"
SCHEMAS_DIR = ASSETS_DIR / "schemas"
CONSTANTS_SCRIPT = SCRIPTS_DIR / "skeptic_constants.py"
PROFILE_SCHEMA = SCHEMAS_DIR / "profile.schema.json"

assert CONSTANTS_SCRIPT.is_file(), f"skeptic_constants.py not found at {CONSTANTS_SCRIPT}"
assert PROFILE_SCHEMA.is_file(), f"profile.schema.json not found at {PROFILE_SCHEMA}"


def _load_module(name: str, path: Path, extra_sys_path: Path):
    sys.path.insert(0, str(extra_sys_path))
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(extra_sys_path))


CONST = _load_module("skeptic_constants", CONSTANTS_SCRIPT, SCRIPTS_DIR)


def _skeptic_pass_props():
    schema = json.loads(PROFILE_SCHEMA.read_text(encoding="utf-8"))
    return schema["properties"]["glossary"]["properties"]["skeptic_pass"]["properties"]


# Named mutation: change any schema `default:` (e.g. dispersion_threshold default
# 12 -> 10) OR any skeptic_constants *_DEFAULT and this test goes RED.
def test_schema_defaults_mirror_constants():
    props = _skeptic_pass_props()
    expected = {
        "enabled": False,
        "windows_per_entity": CONST.WINDOWS_PER_ENTITY_DEFAULT,
        "sample_cap": CONST.SAMPLE_CAP_DEFAULT,
        "dispersion_threshold": CONST.DISPERSION_THRESHOLD_DEFAULT,
        "near_threshold": CONST.NEAR_THRESHOLD_DEFAULT,
        "near_cap": CONST.NEAR_CAP_DEFAULT,
        "near_pair_budget": CONST.NEAR_PAIR_BUDGET_DEFAULT,
    }
    for field, want in expected.items():
        assert field in props, f"skeptic_pass.{field} missing from profile schema"
        assert "default" in props[field], f"skeptic_pass.{field} has no schema default"
        got = props[field]["default"]
        assert got == want, f"skeptic_pass.{field} default {got!r} != constant {want!r}"


def test_enabled_defaults_false_opt_in():
    # The pass MUST be opt-in: a maintainer reading the schema sees enabled:false.
    props = _skeptic_pass_props()
    assert props["enabled"]["default"] is False


def test_citation_block_types_has_no_hardcoded_default():
    # citation_block_types is adapter-derived (per source.format) when omitted,
    # so it deliberately carries NO schema default. A default here would wrongly
    # suggest a single fixed citation set across all adapters.
    props = _skeptic_pass_props()
    assert "default" not in props["citation_block_types"]
