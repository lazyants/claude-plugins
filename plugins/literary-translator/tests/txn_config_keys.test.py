"""tests/txn_config_keys.test.py -- the two new #409 track B `engine.*`
config keys, `max_txn_failures_per_segment` and
`max_rejected_candidates_per_round` (`profile.schema.json`,
`assets/profile.example.yml`).

Both are OPTIONAL, mirroring `engine.max_codex_jobs_per_batch`'s own
established convention: many existing profiles/fixtures predate these knobs
and must stay schema-valid without them. `engine` itself carries
`"additionalProperties": false`, so before this schema change neither key
was even usable -- a profile setting either one was flatly rejected as an
unknown property, which is the whole reason this file exists.

  * `max_txn_failures_per_segment` -- integer, minimum 0, default 3. Bounds
    `<seg>.txn_failures`, the durable count of REFUSED fixreview transactions
    a segment may accumulate (segment_dispatch_driver.py's
    charge_txn_failure/txn_failures_exhausted) before a further transaction
    is refused. Only failures are charged -- a segment that keeps converging
    successfully never advances this counter.
  * `max_rejected_candidates_per_round` -- integer, minimum 0, default 2.
    Bounds how many codex-produced candidates a single numeric fix-review
    round may accumulate as REJECTED before the round gives up.

Scope, mirroring tests/output_knobs_inert.test.py's own stated policy for a
config knob whose consumer lands separately from its schema surface: this
file locks the SHAPE contract (accepted/rejected values, defaults, the
absent-is-still-valid property) via the plugin's own real
`profile_validate.py` validator, not a hand-rolled reimplementation of
jsonschema's type-checking. It does not touch segment_dispatch_driver.py or
codex_job.py, and does not assert anything about how either knob's value is
actually consumed at runtime -- that is a separate change.

RED-before-GREEN for the boolean-is-not-an-integer case (the one case where
`isinstance(True, int) is True` in plain Python makes the naive check look
like it would silently pass): `test_boolean_true_and_false_rejected_for_both_keys`
first proves the REAL schema rejects `True`/`False` for both keys, then
`test_removing_the_type_keyword_would_have_let_booleans_through` proves the
NEGATIVE half by patching a deep-copied schema to drop exactly the `"type":
"integer"` keyword for one key and showing `True`/`False` pass against that
weakened copy -- so the first test's green is against a guard demonstrated to
be load-bearing, not a coincidence of some other rule catching the same
value.
"""
from __future__ import annotations

import copy
import importlib.util
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
ASSETS_ROOT = PLUGIN_ROOT / "skills" / "literary-translator" / "assets"
SCHEMA_PATH = ASSETS_ROOT / "schemas" / "profile.schema.json"
EXAMPLE_PROFILE_PATH = ASSETS_ROOT / "profile.example.yml"
PROFILE_VALIDATE_SCRIPT = ASSETS_ROOT / "scripts" / "profile_validate.py"

assert SCHEMA_PATH.is_file(), f"expected {SCHEMA_PATH} to exist"
assert EXAMPLE_PROFILE_PATH.is_file(), f"expected {EXAMPLE_PROFILE_PATH} to exist"
assert PROFILE_VALIDATE_SCRIPT.is_file(), f"expected {PROFILE_VALIDATE_SCRIPT} to exist"

NEW_KEYS = ("max_txn_failures_per_segment", "max_rejected_candidates_per_round")


def _load_profile_validate_module():
    spec = importlib.util.spec_from_file_location(
        "profile_validate_under_test_txn_config_keys", PROFILE_VALIDATE_SCRIPT
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


def schema_errors(profile, schema=None):
    return pv.validate_against_schema(profile, schema if schema is not None else SCHEMA)


def make_base_profile():
    """A fully schema-valid profile with neither new `engine.*` knob set --
    mirrors tests/profile_validate.test.py's own fixture shape (kept
    local/self-contained per this codebase's convention of every test file
    owning its own fixture builder, rather than importing another test
    file). Individual tests deep-copy this and mutate exactly the field(s)
    under test."""
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
# additionalProperties:false regression lock -- BEFORE this change, setting
# either key was flatly rejected as an unknown property. This is the actual
# bug the schema change fixes; a passing suite that never checked "absent"
# separately from "present-and-valid" could not tell the two states apart.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("key", NEW_KEYS)
def test_absent_still_valid(key):
    """A profile that never mentions the new key (the base fixture, and by
    extension every pre-existing profile/fixture in the repo) must remain
    valid -- both keys are OPTIONAL."""
    profile = make_base_profile()
    assert key not in profile["engine"]
    assert schema_errors(profile) == []


def test_both_absent_together_still_valid():
    profile = make_base_profile()
    assert schema_errors(profile) == []


# ---------------------------------------------------------------------------
# Accepted values
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("key,value", [("max_txn_failures_per_segment", 3), ("max_rejected_candidates_per_round", 2)])
def test_shipped_default_value_accepted(key, value):
    """The value this file's sibling profile.example.yml actually ships."""
    profile = make_base_profile()
    profile["engine"][key] = value
    assert schema_errors(profile) == []


@pytest.mark.parametrize("key", NEW_KEYS)
def test_zero_accepted(key):
    """minimum: 0 is inclusive -- a project that wants zero tolerance (refuse
    on the very first failure/rejection) must be expressible."""
    profile = make_base_profile()
    profile["engine"][key] = 0
    assert schema_errors(profile) == []


@pytest.mark.parametrize("key", NEW_KEYS)
def test_large_positive_value_accepted(key):
    profile = make_base_profile()
    profile["engine"][key] = 1000
    assert schema_errors(profile) == []


def test_both_keys_present_and_valid_together():
    profile = make_base_profile()
    profile["engine"]["max_txn_failures_per_segment"] = 5
    profile["engine"]["max_rejected_candidates_per_round"] = 1
    assert schema_errors(profile) == []


# ---------------------------------------------------------------------------
# Rejected: negative values (minimum: 0)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("key", NEW_KEYS)
def test_negative_one_rejected(key):
    profile = make_base_profile()
    profile["engine"][key] = -1
    errors = schema_errors(profile)
    assert errors != []
    assert any(key in e for e in errors)


@pytest.mark.parametrize("key", NEW_KEYS)
def test_large_negative_value_rejected(key):
    profile = make_base_profile()
    profile["engine"][key] = -1000
    errors = schema_errors(profile)
    assert errors != []
    assert any(key in e for e in errors)


# ---------------------------------------------------------------------------
# Rejected: wrong types, INCLUDING booleans. `isinstance(True, int)` is True
# in plain Python -- a hand-rolled "is this an int" check would wrongly admit
# a boolean here, exactly the class of bug segment_dispatch_driver.py's own
# _is_counter_int() helper (assets/scripts/segment_dispatch_driver.py) was
# written to guard against for the runtime counters these two knobs bound.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("key", NEW_KEYS)
@pytest.mark.parametrize("bad_value", [True, False])
def test_boolean_rejected_for_both_keys(key, bad_value):
    profile = make_base_profile()
    profile["engine"][key] = bad_value
    errors = schema_errors(profile)
    assert errors != [], f"{key}={bad_value!r} (a bool) must be rejected, not silently accepted as an int"
    assert any(key in e for e in errors)


@pytest.mark.parametrize("key", NEW_KEYS)
@pytest.mark.parametrize("bad_value", ["3", 3.5, None, [3], {"count": 3}])
def test_other_wrong_types_rejected(key, bad_value):
    profile = make_base_profile()
    profile["engine"][key] = bad_value
    errors = schema_errors(profile)
    assert errors != [], f"{key}={bad_value!r} must be rejected"
    assert any(key in e for e in errors)


# ---------------------------------------------------------------------------
# RED-before-GREEN: prove the boolean rejection above is actually pinned on
# the schema's own `"type": "integer"` keyword, not an accident of some
# other rule (e.g. `minimum`) happening to also catch it. A deep-copied,
# deliberately weakened schema with that one keyword removed must let the
# same boolean value through -- if it did NOT, the real schema's rejection
# above would not be proven to depend on the keyword this change actually
# adds.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("key", NEW_KEYS)
def test_removing_the_type_keyword_would_have_let_booleans_through(key):
    weakened = copy.deepcopy(SCHEMA)
    prop = weakened["properties"]["engine"]["properties"][key]
    assert prop["type"] == "integer", f"unexpected pre-existing shape for engine.{key}"
    del prop["type"]  # the guard this test is proving is load-bearing

    profile = make_base_profile()
    profile["engine"][key] = True
    # Against the REAL schema this same assignment is rejected (see
    # test_boolean_rejected_for_both_keys above). Against the WEAKENED copy
    # it must now pass, demonstrating the removed keyword was the guard.
    assert schema_errors(profile, schema=weakened) == [], (
        f"expected the weakened schema (no 'type' on engine.{key}) to accept "
        f"a boolean -- if it still rejects it, this test is not exercising "
        f"the keyword it claims to"
    )


# ---------------------------------------------------------------------------
# Defaults declared in the schema match what profile.example.yml actually
# ships -- a drift here would mean the "effective limit" an operator sees
# when the key is absent (per max_codex_jobs_per_batch's own precedent of
# substituting the schema's documented default) no longer matches the
# shipped example's own illustrative value.
# ---------------------------------------------------------------------------


def test_schema_declared_defaults_match_shipped_example():
    engine_schema = SCHEMA["properties"]["engine"]["properties"]
    assert engine_schema["max_txn_failures_per_segment"]["default"] == 3
    assert engine_schema["max_rejected_candidates_per_round"]["default"] == 2


def test_both_new_keys_are_optional_not_required():
    required = SCHEMA["properties"]["engine"].get("required", [])
    for key in NEW_KEYS:
        assert key not in required, f"engine.{key} must stay OPTIONAL (not in engine.required)"


def test_shipped_example_yml_sets_both_keys_to_their_documented_defaults():
    """assets/profile.example.yml is a human-facing illustration -- it should
    demonstrate the knob at its own documented default, not silently omit it
    or drift from it."""
    import yaml

    profile = yaml.safe_load(EXAMPLE_PROFILE_PATH.read_text(encoding="utf-8"))
    assert profile["engine"]["max_txn_failures_per_segment"] == 3
    assert profile["engine"]["max_rejected_candidates_per_round"] == 2


if __name__ == "__main__":
    import sys

    sys.exit(pytest.main([__file__, "-v"]))
