"""Tests for ``scripts/profile_validate.py`` -- the schema-shape half of Step 0.

Scope (see the build spec's Step 0 section and
``assets/schemas/profile.schema.json``): unknown-top-level-key rejection with
the ``x_*`` namespace carve-out, every conditional/if-then rule in
``profile.schema.json`` (the ``plain_text`` segmentation/verse_detection/
footnotes format-gating chief among them), every procedural path-safety check
(``particle_config`` rejecting ``/``, ``\\``, ``..``, absolute paths;
``smoke_test.report_path`` rejecting any ``..`` substring), placeholder
rejection (one case per placeholder substring, plus a dedicated
"only title unreplaced" case), ``heading_regex`` compilability and
``blank_line_threshold`` negative-path cases, the ``custom``-format
experimental warning (present only for ``custom``), the ``CHOOSE_``-sentinel
placeholder scan and its interaction with schema format-gating, and the
``custom`` adapter's SCHEMA-half cases (``extractor_path: null`` passes;
omitting the whole ``custom:`` sub-block or the ``extractor_path`` key
entirely fails schema validation).

This file deliberately does NOT cover: the three-fixture
missing/verbatim/filled-in profile.example.yml flow (that's
``profile_example_validation.test.py``'s job), the resumed-project
PROMPT_CONTRACT_VERSION/EXTRACTOR_CONTRACT_VERSION drift checks (their own
dedicated test files), or scaffold idempotency. It also does not require the
shipped ``profile.example.yml`` to pass Step 0 verbatim -- it never does, by
design (every placeholder in it is an intentionally invalid sentinel).

The target script is loaded directly from its real location under
``skills/literary-translator/assets/scripts/`` via ``importlib`` (it is not a
package, and it is one of THREE plugin-path scripts in this plugin that are
NEVER copied to a durable_root -- alongside ``validate_extraction.py`` and
``glossary_preflight.py`` (1.4.0) -- always invoked from the plugin's own
install path).
"""

import copy
import importlib.util
import re
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = (
    PLUGIN_ROOT
    / "skills"
    / "literary-translator"
    / "assets"
    / "scripts"
    / "profile_validate.py"
)


def _load_profile_validate_module():
    spec = importlib.util.spec_from_file_location("profile_validate", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None, f"could not load spec for {SCRIPT_PATH}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


pv = _load_profile_validate_module()
# Populate the module-level `yaml`/`jsonschema` handles the same way main()
# would (step 2) -- both are real, installed dependencies in this
# environment, so this exercises the script's real dependency-preflight path,
# not a mock.
pv.dependency_preflight()

SCHEMA = pv.load_profile_schema()


def schema_errors(profile):
    """Runs the real Draft202012Validator + FormatChecker pass exactly as
    ``main()``'s step 5 does."""
    return pv.validate_against_schema(profile, SCHEMA)


def make_base_profile():
    """A fully schema-valid profile (default format: gutenberg_epub) with no
    placeholders and no path-safety violations anywhere. Every inactive
    format's own adapter_config sub-block is populated with harmless,
    basic-shape-valid values (mirroring the shipped profile.example.yml's own
    convention of leaving inactive sub-blocks populated for illustration).
    Individual tests deep-copy this and mutate exactly the field(s) under
    test."""
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


# ---------------------------------------------------------------------------
# Sanity: the fixture itself is schema-valid (harness self-check -- every
# mutation test below deep-copies this and should isolate exactly one
# violation, so the baseline must be clean).
# ---------------------------------------------------------------------------


def test_base_profile_is_schema_valid():
    assert schema_errors(make_base_profile()) == []


def test_base_profile_has_no_placeholders():
    assert pv.scan_placeholders(make_base_profile()) == []


# ---------------------------------------------------------------------------
# Unknown-top-level-key rejection + the x_* carve-out
# ---------------------------------------------------------------------------


def test_unknown_top_level_key_rejected():
    profile = make_base_profile()
    profile["totally_unknown_key"] = "value"
    errors = pv.check_unknown_top_level_keys(profile)
    assert len(errors) == 1
    assert "totally_unknown_key" in errors[0]


def test_unknown_top_level_key_also_rejected_by_schema():
    # Defense in depth: the schema's own additionalProperties:false (with the
    # x_* patternProperties carve-out) independently rejects it too.
    profile = make_base_profile()
    profile["totally_unknown_key"] = "value"
    errors = schema_errors(profile)
    assert len(errors) == 1
    assert "totally_unknown_key" in errors[0]


def test_x_prefixed_top_level_key_is_allowed():
    profile = make_base_profile()
    profile["x_custom_extension"] = {"anything": True, "goes": [1, 2, 3]}
    assert pv.check_unknown_top_level_keys(profile) == []
    assert schema_errors(profile) == []


def test_x_prefix_alone_is_allowed():
    profile = make_base_profile()
    profile["x_"] = "bare prefix, no suffix"
    assert pv.check_unknown_top_level_keys(profile) == []


def test_key_starting_with_x_but_not_x_underscore_is_rejected():
    # "xtra_field" starts with "x" but not the reserved "x_" prefix -- must
    # not be silently carved out.
    profile = make_base_profile()
    profile["xtra_field"] = 1
    errors = pv.check_unknown_top_level_keys(profile)
    assert len(errors) == 1
    assert "xtra_field" in errors[0]


# ---------------------------------------------------------------------------
# Schema conditional/if-then rules: gutenberg_epub format gating
# ---------------------------------------------------------------------------


def test_gutenberg_epub_active_requires_its_adapter_config_object():
    profile = make_base_profile()
    profile["source"]["adapter_config"]["gutenberg_epub"] = None
    errors = schema_errors(profile)
    assert errors != []
    assert any("gutenberg_epub" in e for e in errors)


def test_gutenberg_id_must_be_null_when_format_is_not_gutenberg_epub():
    profile = make_base_profile()
    profile["source"]["format"] = "plain_text"
    profile["source"]["gutenberg_id"] = 42
    errors = schema_errors(profile)
    assert errors != []
    assert any("gutenberg_id" in e for e in errors)


def test_gutenberg_id_may_be_non_null_when_format_is_gutenberg_epub():
    # The null-restriction is format-gated -- it does NOT apply to the
    # active gutenberg_epub format itself.
    profile = make_base_profile()
    profile["source"]["gutenberg_id"] = 42
    assert schema_errors(profile) == []


# ---------------------------------------------------------------------------
# Schema conditional/if-then rules: plain_text format gating
# ---------------------------------------------------------------------------


def test_plain_text_active_requires_its_adapter_config_object():
    profile = make_base_profile()
    profile["source"]["format"] = "plain_text"
    profile["source"]["adapter_config"]["plain_text"] = None
    errors = schema_errors(profile)
    assert errors != []
    assert any("plain_text" in e for e in errors)


def test_plain_text_segmentation_blank_line_run_requires_positive_threshold():
    """Negative path: method=blank_line_run with blank_line_threshold=None
    is fatal once plain_text is the active format."""
    profile = make_base_profile()
    profile["source"]["format"] = "plain_text"
    profile["source"]["adapter_config"]["plain_text"]["segmentation"][
        "blank_line_threshold"
    ] = None
    errors = schema_errors(profile)
    assert errors != []
    assert any("blank_line_threshold" in e for e in errors)


def test_plain_text_segmentation_blank_line_threshold_zero_rejected():
    """Negative path: blank_line_threshold=0 violates minimum:1 (both the
    base type constraint and the active-format conditional)."""
    profile = make_base_profile()
    profile["source"]["format"] = "plain_text"
    profile["source"]["adapter_config"]["plain_text"]["segmentation"][
        "blank_line_threshold"
    ] = 0
    errors = schema_errors(profile)
    assert errors != []
    assert any("blank_line_threshold" in e and "minimum" in e for e in errors)


def test_plain_text_segmentation_blank_line_run_valid_threshold_passes():
    profile = make_base_profile()
    profile["source"]["format"] = "plain_text"
    profile["source"]["adapter_config"]["plain_text"]["segmentation"][
        "blank_line_threshold"
    ] = 3
    assert schema_errors(profile) == []


def test_plain_text_segmentation_heading_regex_method_requires_string():
    """Negative path: method=heading_regex with heading_regex=None is fatal
    once plain_text is the active format."""
    profile = make_base_profile()
    profile["source"]["format"] = "plain_text"
    seg = profile["source"]["adapter_config"]["plain_text"]["segmentation"]
    seg["method"] = "heading_regex"
    seg["heading_regex"] = None
    errors = schema_errors(profile)
    assert errors != []
    assert any("heading_regex" in e for e in errors)


def test_plain_text_segmentation_heading_regex_method_valid_passes():
    profile = make_base_profile()
    profile["source"]["format"] = "plain_text"
    seg = profile["source"]["adapter_config"]["plain_text"]["segmentation"]
    seg["method"] = "heading_regex"
    seg["heading_regex"] = "^Chapter \\d+"
    assert schema_errors(profile) == []


def test_plain_text_verse_detection_enum_is_format_gated():
    """The verse_detection enum restriction (none_confirmed|regex) is only
    enforced while plain_text is the ACTIVE format -- while inactive, the
    field is just a plain non-empty string."""
    active = make_base_profile()
    active["source"]["format"] = "plain_text"
    active["source"]["adapter_config"]["plain_text"]["verse_detection"] = "not_a_real_choice"
    active_errors = schema_errors(active)
    assert active_errors != []
    assert any("verse_detection" in e for e in active_errors)

    inactive = make_base_profile()  # format stays gutenberg_epub
    inactive["source"]["adapter_config"]["plain_text"]["verse_detection"] = "not_a_real_choice"
    assert schema_errors(inactive) == []


def test_plain_text_footnotes_enum_is_format_gated():
    """Mirrors verse_detection's format gating for the footnotes enum
    (none_confirmed|markdown_ref|custom_regex)."""
    active = make_base_profile()
    active["source"]["format"] = "plain_text"
    active["source"]["adapter_config"]["plain_text"]["footnotes"] = "not_a_real_choice"
    active_errors = schema_errors(active)
    assert active_errors != []
    assert any("footnotes" in e for e in active_errors)

    inactive = make_base_profile()
    inactive["source"]["adapter_config"]["plain_text"]["footnotes"] = "not_a_real_choice"
    assert schema_errors(inactive) == []


def test_plain_text_verse_detection_regex_requires_verse_regex():
    profile = make_base_profile()
    profile["source"]["format"] = "plain_text"
    pt = profile["source"]["adapter_config"]["plain_text"]
    pt["verse_detection"] = "regex"
    pt["verse_regex"] = None
    errors = schema_errors(profile)
    assert errors != []
    assert any("verse_regex" in e for e in errors)


def test_plain_text_verse_detection_regex_with_valid_verse_regex_passes():
    profile = make_base_profile()
    profile["source"]["format"] = "plain_text"
    pt = profile["source"]["adapter_config"]["plain_text"]
    pt["verse_detection"] = "regex"
    pt["verse_regex"] = "^V\\."
    assert schema_errors(profile) == []


def test_plain_text_verse_detection_none_confirmed_requires_null_verse_regex():
    profile = make_base_profile()
    profile["source"]["format"] = "plain_text"
    pt = profile["source"]["adapter_config"]["plain_text"]
    pt["verse_detection"] = "none_confirmed"
    pt["verse_regex"] = "somepattern"  # dead config left lying around -- fatal
    errors = schema_errors(profile)
    assert errors != []
    assert any("verse_regex" in e for e in errors)


def test_plain_text_footnotes_custom_regex_requires_both_regexes():
    profile = make_base_profile()
    profile["source"]["format"] = "plain_text"
    pt = profile["source"]["adapter_config"]["plain_text"]
    pt["footnotes"] = "custom_regex"
    pt["footnote_anchor_regex"] = None
    pt["footnote_def_regex"] = None
    errors = schema_errors(profile)
    assert errors != []
    assert any("footnote_anchor_regex" in e for e in errors)
    assert any("footnote_def_regex" in e for e in errors)


def test_plain_text_footnotes_custom_regex_with_valid_regexes_passes():
    profile = make_base_profile()
    profile["source"]["format"] = "plain_text"
    pt = profile["source"]["adapter_config"]["plain_text"]
    pt["footnotes"] = "custom_regex"
    pt["footnote_anchor_regex"] = r"\[\^(\d+)\]"
    pt["footnote_def_regex"] = r"^\[\^(\d+)\]:"
    assert schema_errors(profile) == []


def test_plain_text_footnotes_non_custom_regex_requires_null_regexes():
    profile = make_base_profile()
    profile["source"]["format"] = "plain_text"
    pt = profile["source"]["adapter_config"]["plain_text"]
    pt["footnotes"] = "markdown_ref"
    pt["footnote_anchor_regex"] = "something"  # dead config -- fatal
    errors = schema_errors(profile)
    assert errors != []
    assert any("footnote_anchor_regex" in e for e in errors)


# ---------------------------------------------------------------------------
# Schema conditional/if-then rules: custom format gating
# ---------------------------------------------------------------------------


def test_custom_active_requires_its_adapter_config_object():
    profile = make_base_profile()
    profile["source"]["format"] = "custom"
    profile["source"]["adapter_config"]["custom"] = None
    errors = schema_errors(profile)
    assert errors != []
    assert any("custom" in e for e in errors)


# ---------------------------------------------------------------------------
# Schema conditional/if-then rules: verse_policy.mode <-> threshold_lines
# ---------------------------------------------------------------------------


def test_verse_policy_mixed_by_length_requires_threshold_lines():
    profile = make_base_profile()
    profile["verse_policy"] = {"mode": "mixed_by_length", "threshold_lines": None}
    errors = schema_errors(profile)
    assert errors != []
    assert any("threshold_lines" in e for e in errors)


def test_verse_policy_mixed_by_length_with_threshold_lines_passes():
    profile = make_base_profile()
    profile["verse_policy"] = {"mode": "mixed_by_length", "threshold_lines": 5}
    assert schema_errors(profile) == []


def test_verse_policy_non_mixed_mode_requires_null_threshold_lines():
    profile = make_base_profile()
    profile["verse_policy"] = {"mode": "literal_only", "threshold_lines": 5}
    errors = schema_errors(profile)
    assert errors != []
    assert any("threshold_lines" in e for e in errors)


# ---------------------------------------------------------------------------
# Procedural path-safety check: source.language.particle_config
# ---------------------------------------------------------------------------


def _particle_profile(value):
    return {"source": {"language": {"particle_config": value}}}


def test_particle_config_rejects_forward_slash():
    errors = pv.check_particle_config(_particle_profile("/abs/fr.json"))
    assert len(errors) == 1
    assert "forward slash" in errors[0]


def test_particle_config_rejects_backslash():
    errors = pv.check_particle_config(_particle_profile("sub\\fr.json"))
    assert len(errors) == 1
    assert "backslash" in errors[0]


def test_particle_config_rejects_dot_dot_segment():
    errors = pv.check_particle_config(_particle_profile("..fr.json"))
    assert len(errors) == 1
    assert "'..'" in errors[0] or ".." in errors[0]


def test_particle_config_rejects_absolute_path_prefix():
    errors = pv.check_particle_config(_particle_profile("~fr.json"))
    assert len(errors) == 1
    assert "absolute path" in errors[0]


def test_particle_config_bare_filename_passes():
    assert pv.check_particle_config(_particle_profile("fr.json")) == []


def test_particle_config_non_string_value_deferred_to_schema():
    # A non-string value (e.g. schema violation handled elsewhere) is not
    # this procedural check's job -- it must not raise or false-positive.
    assert pv.check_particle_config(_particle_profile(None)) == []


# ---------------------------------------------------------------------------
# Procedural path-safety check: source.language.smoke_test.report_path
# ---------------------------------------------------------------------------


def _smoke_profile(value):
    return {"source": {"language": {"smoke_test": {"report_path": value}}}}


def test_smoke_test_report_path_rejects_dot_dot_mid_path():
    errors = pv.check_smoke_test_report_path(_smoke_profile("runs/../secret.json"))
    assert len(errors) == 1
    assert ".." in errors[0]


def test_smoke_test_report_path_rejects_dot_dot_anywhere_in_a_segment():
    errors = pv.check_smoke_test_report_path(_smoke_profile("..hidden.json"))
    assert len(errors) == 1
    assert ".." in errors[0]


def test_smoke_test_report_path_valid_relative_path_passes():
    assert pv.check_smoke_test_report_path(_smoke_profile("runs/report.json")) == []


def test_smoke_test_report_path_null_passes():
    assert pv.check_smoke_test_report_path(_smoke_profile(None)) == []


# ---------------------------------------------------------------------------
# Placeholder rejection: one case per substring, plus a dedicated
# "only title unreplaced" isolation case
# ---------------------------------------------------------------------------


def test_placeholder_book_title_rejected():
    profile = make_base_profile()
    profile["project"]["title"] = "YOUR BOOK TITLE HERE"
    errors = pv.scan_placeholders(profile)
    assert len(errors) == 1
    assert "YOUR BOOK TITLE HERE" in errors[0]
    assert "project.title" in errors[0]


def test_placeholder_durable_root_rejected():
    profile = make_base_profile()
    profile["project"]["durable_root"] = "/ABS/PATH/TO/YOUR_PROJECT"
    errors = pv.scan_placeholders(profile)
    assert len(errors) == 1
    assert "/ABS/PATH/TO/YOUR_PROJECT" in errors[0]
    assert "project.durable_root" in errors[0]


def test_placeholder_source_path_rejected():
    profile = make_base_profile()
    profile["source"]["path"] = "/ABS/PATH/TO/YOUR_SOURCE.epub"
    errors = pv.scan_placeholders(profile)
    assert len(errors) == 1
    assert "/ABS/PATH/TO/YOUR_SOURCE" in errors[0]
    assert "source.path" in errors[0]


def test_only_title_placeholder_remains_isolates_a_single_error():
    """Dedicated case: every OTHER field holds a real value; only the title
    still carries its shipped placeholder. The scan must report exactly one
    violation, naming exactly the title field -- never spuriously fire on
    the (already-real) durable_root/source.path/CHOOSE_ fields alongside
    it."""
    profile = make_base_profile()
    profile["project"]["title"] = "YOUR BOOK TITLE HERE"
    errors = pv.scan_placeholders(profile)
    assert len(errors) == 1
    assert "project.title" in errors[0]
    assert "YOUR BOOK TITLE HERE" in errors[0]


def test_all_three_placeholders_together_report_three_distinct_errors():
    profile = make_base_profile()
    profile["project"]["title"] = "YOUR BOOK TITLE HERE"
    profile["project"]["durable_root"] = "/ABS/PATH/TO/YOUR_PROJECT"
    profile["source"]["path"] = "/ABS/PATH/TO/YOUR_SOURCE.epub"
    errors = pv.scan_placeholders(profile)
    assert len(errors) == 3
    joined = "\n".join(errors)
    assert "project.title" in joined
    assert "project.durable_root" in joined
    assert "source.path" in joined


# ---------------------------------------------------------------------------
# check_durable_root: tmp/scratchpad rejection, and the
# LT_PROFILE_VALIDATE_ALLOW_TMP_ROOT override that lets an ephemeral/CI/
# test environment opt a durable_root genuinely under /tmp back in, without
# weakening the default (no env var -> still rejected). durable_root's
# parent is "/tmp" itself here -- always present and writable on macOS and
# Linux -- so both cases below isolate the tmp/scratchpad check alone, with
# no parent-exists/writable noise mixed in.
# ---------------------------------------------------------------------------


def test_durable_root_under_tmp_is_rejected_by_default(monkeypatch):
    monkeypatch.delenv(pv.ALLOW_TMP_ROOT_ENV_VAR, raising=False)
    profile = make_base_profile()
    profile["project"]["durable_root"] = "/tmp/lt-profile-validate-test-durable-root"

    errors = pv.check_durable_root(profile)

    assert len(errors) == 1, errors
    assert "project.durable_root" in errors[0]
    assert "must not resolve under a tmp/temp/scratchpad directory" in errors[0]


def test_durable_root_under_tmp_is_accepted_with_override(monkeypatch):
    monkeypatch.setenv(pv.ALLOW_TMP_ROOT_ENV_VAR, "1")
    profile = make_base_profile()
    profile["project"]["durable_root"] = "/tmp/lt-profile-validate-test-durable-root"

    assert pv.check_durable_root(profile) == []


# ---------------------------------------------------------------------------
# heading_regex compilability (procedural)
# ---------------------------------------------------------------------------


def _segmentation_profile(method, heading_regex, blank_line_threshold):
    return {
        "source": {
            "adapter_config": {
                "plain_text": {
                    "segmentation": {
                        "method": method,
                        "heading_regex": heading_regex,
                        "blank_line_threshold": blank_line_threshold,
                    }
                }
            }
        }
    }


def test_heading_regex_invalid_regex_is_fatal():
    profile = _segmentation_profile("heading_regex", "(unclosed", None)
    errors, warnings = pv.check_plain_text_segmentation(profile)
    assert len(errors) == 1
    assert "does not compile" in errors[0]
    assert warnings == []


def test_heading_regex_valid_regex_passes():
    profile = _segmentation_profile("heading_regex", "^Chapter \\d+", None)
    errors, warnings = pv.check_plain_text_segmentation(profile)
    assert errors == []


def test_heading_regex_cross_field_warning_when_inactive_and_set():
    """method=blank_line_run but heading_regex is still non-null: a
    non-fatal cross-field WARNING (dead configuration), never a fatal
    error."""
    profile = _segmentation_profile("blank_line_run", "somepattern", 2)
    errors, warnings = pv.check_plain_text_segmentation(profile)
    assert errors == []
    assert len(warnings) == 1
    assert "blank_line_run" in warnings[0]


def test_blank_line_threshold_cross_field_warning_when_inactive_and_set():
    """method=heading_regex but blank_line_threshold is still non-null: a
    non-fatal cross-field WARNING."""
    profile = _segmentation_profile("heading_regex", "^X", 3)
    errors, warnings = pv.check_plain_text_segmentation(profile)
    assert errors == []
    assert len(warnings) == 1
    assert "heading_regex" in warnings[0]


def test_plain_text_falsy_short_circuits_with_no_findings():
    profile = {"source": {"adapter_config": {"plain_text": None}}}
    errors, warnings = pv.check_plain_text_segmentation(profile)
    assert errors == []
    assert warnings == []


# ---------------------------------------------------------------------------
# custom-format experimental warning: present only for custom
# ---------------------------------------------------------------------------


def test_custom_format_warning_present_for_custom():
    warnings = pv.check_custom_format_warning({"source": {"format": "custom"}})
    assert len(warnings) == 1
    assert "experimental" in warnings[0]


def test_custom_format_warning_absent_for_gutenberg_epub():
    warnings = pv.check_custom_format_warning({"source": {"format": "gutenberg_epub"}})
    assert warnings == []


def test_custom_format_warning_absent_for_plain_text():
    warnings = pv.check_custom_format_warning({"source": {"format": "plain_text"}})
    assert warnings == []


# ---------------------------------------------------------------------------
# CHOOSE_-sentinel rejection + its interaction with schema format-gating
# ---------------------------------------------------------------------------


def test_choose_sentinel_rejected_in_an_unconditionally_enforced_field():
    profile = make_base_profile()
    profile["glossary"]["research_mode"] = "CHOOSE_live_or_offline"
    errors = pv.scan_placeholders(profile)
    assert len(errors) == 1
    assert "CHOOSE_live_or_offline" in errors[0]
    assert "glossary.research_mode" in errors[0]
    # And the schema itself unconditionally rejects it too (research_mode's
    # enum is not format-gated at all -- always live|offline).
    assert schema_errors(profile) != []


def test_choose_sentinel_in_inactive_format_block_still_fatally_scanned():
    """profile_validate.py's placeholder scan (step 7) walks EVERY string
    leaf regardless of which source.format is active -- a CHOOSE_ sentinel
    left in the currently-INACTIVE plain_text sub-block still fails Step 0,
    even though the schema's own enum restriction for that field is
    format-gated and would NOT catch it on its own."""
    profile = make_base_profile()  # format stays gutenberg_epub (inactive plain_text)
    profile["source"]["adapter_config"]["plain_text"][
        "verse_detection"
    ] = "CHOOSE_none_confirmed_or_regex"

    # The schema alone does NOT catch this while plain_text is inactive.
    assert schema_errors(profile) == []

    # But the placeholder scan catches it regardless of active format.
    errors = pv.scan_placeholders(profile)
    assert len(errors) == 1
    assert "CHOOSE_none_confirmed_or_regex" in errors[0]


def test_choose_sentinel_when_format_active_is_caught_by_both_layers():
    """When plain_text IS the active format, a CHOOSE_ sentinel in
    verse_detection is caught by the schema's enum conditional AND the
    placeholder scan (defense in depth, not mutually exclusive)."""
    profile = make_base_profile()
    profile["source"]["format"] = "plain_text"
    profile["source"]["adapter_config"]["plain_text"][
        "verse_detection"
    ] = "CHOOSE_none_confirmed_or_regex"

    schema_errs = schema_errors(profile)
    assert schema_errs != []
    assert any("verse_detection" in e for e in schema_errs)

    placeholder_errs = pv.scan_placeholders(profile)
    assert len(placeholder_errs) == 1
    assert "CHOOSE_none_confirmed_or_regex" in placeholder_errs[0]


def test_choose_sentinel_for_footnotes_in_inactive_block_still_scanned():
    profile = make_base_profile()
    profile["source"]["adapter_config"]["plain_text"][
        "footnotes"
    ] = "CHOOSE_none_confirmed_or_markdown_ref_or_custom_regex"

    assert schema_errors(profile) == []  # format-gated, inactive -> schema is silent

    errors = pv.scan_placeholders(profile)
    assert len(errors) == 1
    assert "CHOOSE_none_confirmed_or_markdown_ref_or_custom_regex" in errors[0]


# ---------------------------------------------------------------------------
# custom-adapter SCHEMA-half cases (Step 0c, schema half -- procedural half
# lives in the orchestrating-session test, not here)
# ---------------------------------------------------------------------------


def test_custom_extractor_path_null_passes_schema():
    profile = make_base_profile()
    profile["source"]["format"] = "custom"
    profile["source"]["adapter_config"]["custom"] = {"extractor_path": None}
    assert schema_errors(profile) == []


def test_custom_extractor_path_non_null_string_also_passes_schema():
    profile = make_base_profile()
    profile["source"]["format"] = "custom"
    profile["source"]["adapter_config"]["custom"] = {
        "extractor_path": "my_book_extractor.py"
    }
    assert schema_errors(profile) == []


def test_custom_sub_block_entirely_omitted_fails_schema():
    """Omitting the WHOLE `custom:` sub-block (not merely nulling it) --
    adapter_config's own unconditional `required` list names it missing."""
    profile = make_base_profile()
    profile["source"]["format"] = "custom"
    del profile["source"]["adapter_config"]["custom"]
    errors = schema_errors(profile)
    assert errors != []
    assert any("custom" in e and "required" in e for e in errors)


def test_custom_extractor_path_key_omitted_fails_schema():
    """The `custom:` sub-block is present but empty -- `extractor_path` the
    key itself is missing, not merely null. Fails schema validation (the key
    is required whenever the custom object is present, never merely
    optional)."""
    profile = make_base_profile()
    profile["source"]["format"] = "custom"
    profile["source"]["adapter_config"]["custom"] = {}
    errors = schema_errors(profile)
    assert errors != []
    assert any("extractor_path" in e and "required" in e for e in errors)


if __name__ == "__main__":
    import sys

    sys.exit(pytest.main([__file__, "-v"]))
