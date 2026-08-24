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
install path). ``resolve_codex_companion.py`` (1.4.7) was a fourth until its
stated reason was found false and the exclusion reverted; see
``test_module_docstring_names_three_never_copied_scripts`` below, which pins
that count against the script's own module docstring.
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
EXAMPLE_PATH = PLUGIN_ROOT / "skills" / "literary-translator" / "assets" / "profile.example.yml"


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
    ``main()``'s step 6 does."""
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


def test_module_docstring_names_three_never_copied_scripts():
    """1.4.7's copy-exclusion sweep added resolve_codex_companion.py (the W5
    codex-companion resolver) as a FOURTH plugin-path script never copied to
    durable_root, on a reason since found false and reverted (see SKILL.md's
    Step 0a copy-exclusion list for the disproof) -- profile_validate.py's own
    module docstring must name itself as ONE OF THREE now, not four, and must
    still mention resolve_codex_companion.py by name (as the corrected
    historical note explaining why it is NOT a fourth exception). Also must
    NOT claim "every other script" is copied without qualification --
    scaffold_setup.py is ALSO never copied, for a wholly unrelated reason
    (it is not a bundle member at all), and an unqualified "every other" is
    false regardless of what happens to resolve_codex_companion.py's own
    count. Guards both properties from drifting out of sync with SKILL.md's
    Step 0a copy-exclusion list."""
    source = SCRIPT_PATH.read_text(encoding="utf-8")
    assert "ONE OF THREE PLUGIN-PATH-ONLY SCRIPTS NEVER COPIED" in source
    assert "ONE OF FOUR SCRIPTS NEVER COPIED" not in source
    assert "resolve_codex_companion.py" in source
    assert "scaffold_setup.py" in source, (
        "the docstring's own qualification of \"every other script\" must name "
        "scaffold_setup.py as the OTHER, unrelated exclusion -- omitting it "
        "reintroduces the false 'every other script is copied' claim"
    )


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
# #197 -- engine.effort enum + engine.model pattern
# ---------------------------------------------------------------------------


def test_engine_effort_xhigh_passes_schema():
    profile = make_base_profile()
    profile["engine"]["effort"] = "xhigh"
    assert schema_errors(profile) == []


@pytest.mark.parametrize("bad_effort", ["max", "ultra", "none", "minimal", "HIGH", ""])
def test_engine_effort_out_of_enum_rejected(bad_effort):
    """The enum deliberately excludes 'none'/'minimal' (nonsensical for
    accuracy work) and 'max' ('--effort max' throws in codex-companion) --
    see profile.schema.json's own engine.effort description."""
    profile = make_base_profile()
    profile["engine"]["effort"] = bad_effort
    errors = schema_errors(profile)
    assert errors != []
    assert any("effort" in e for e in errors)


def test_engine_model_well_formed_passes_schema():
    profile = make_base_profile()
    profile["engine"]["model"] = "gpt-5.3-codex-spark"
    assert schema_errors(profile) == []


def test_engine_model_absent_passes_schema():
    # engine.model is optional -- make_base_profile() already omits it.
    profile = make_base_profile()
    assert "model" not in profile["engine"]
    assert schema_errors(profile) == []


@pytest.mark.parametrize(
    "bad_model", ["has space", "semi;colon", "", "-leading-dash", 'quote"here']
)
def test_engine_model_malformed_rejected(bad_model):
    profile = make_base_profile()
    profile["engine"]["model"] = bad_model
    errors = schema_errors(profile)
    assert errors != []
    assert any("model" in e for e in errors)


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
    """profile_validate.py's placeholder scan (step 5) walks EVERY string
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


# ---------------------------------------------------------------------------
# output.adapter_config.obsidian.mentions_section SCHEMA-half cases
# (bot review P1 finding 1): the predicate's `is not False` check tolerates
# a `None` value defensively, but that does NOT mean a schema-valid profile
# can actually carry `enabled: null` or `mentions_section: null` -- both
# subschemas declare a single non-nullable `type`, unlike
# `adapter_config.obsidian` itself, which explicitly allows `["object",
# "null"]`. These tests close the doc/schema contradiction the bot flagged:
# the CHANGELOG + reference docs must never claim `null` reaches the
# predicate through the normal, schema-validated Step 0 path.
# ---------------------------------------------------------------------------


def _obsidian_profile(adapter_config_obsidian):
    profile = make_base_profile()
    profile["output"]["target"] = "obsidian"
    profile["output"]["adapter_config"] = {"obsidian": adapter_config_obsidian}
    return profile


def test_mentions_section_enabled_null_is_schema_invalid():
    """RED-proof-worthy claim: `enabled: null` must be REJECTED, never
    silently accepted as a spelling of the default-on behavior -- the
    runtime predicates' own `None is not False` tolerance is a defensive
    fallback, not evidence this shape survives Step 0 validation."""
    profile = _obsidian_profile({"mentions_section": {"enabled": None}})
    errors = schema_errors(profile)
    assert errors != [], (
        "enabled: null must be rejected by profile_validate.py -- if this "
        "starts passing, every doc/CHANGELOG claim that 'null resolves to "
        "enabled' needs re-litigating, not silently trusting"
    )
    assert any("enabled" in e and "boolean" in e for e in errors), errors


def test_mentions_section_null_is_schema_invalid():
    """The second, easy-to-miss unreachable shape (bot review follow-up):
    `mentions_section: null` is ALSO rejected -- its subschema is
    `"type": "object"` only, with no `"null"` alternative (unlike
    `adapter_config.obsidian`'s own `["object", "null"]`, see the paired
    VALID test below)."""
    profile = _obsidian_profile({"mentions_section": None})
    errors = schema_errors(profile)
    assert errors != [], (
        "mentions_section: null must be rejected by profile_validate.py"
    )
    assert any("mentions_section" in e and "object" in e for e in errors), errors


def test_adapter_config_obsidian_null_is_schema_valid():
    """The ONE null shape that genuinely IS schema-valid (B-O1) --
    `adapter_config.obsidian`'s own subschema explicitly allows
    `["object", "null"]`, unlike `mentions_section`/`enabled` above. A
    project with `target: obsidian` and no explicit `obsidian:` sub-block
    at all reaches the default-on predicate through a real, valid
    profile."""
    profile = _obsidian_profile(None)
    assert schema_errors(profile) == []


def test_mentions_section_absent_is_schema_valid():
    """The actually-reachable, supported way to get the default-on
    behavior: omit `mentions_section` entirely (not `null` it)."""
    profile = _obsidian_profile({"folders": {}})
    assert schema_errors(profile) == []


def test_mentions_section_enabled_boolean_values_are_schema_valid():
    for value in (True, False):
        profile = _obsidian_profile({"mentions_section": {"enabled": value}})
        assert schema_errors(profile) == [], (value, schema_errors(profile))


# ---------------------------------------------------------------------------
# validation.forbidden_patterns (#520) -- the Step 0 schema gate.
#
# These belong HERE, not in the W7 suite. final_audit.py reaches profile.yml
# through vd.load_profile(), which only yaml.safe_load()s -- it never
# validates against profile.schema.json. So a W7-side assertion about what the
# schema accepts passes whether or not the schema says so, and would stay
# green if a constraint were reintroduced that Step 0 actually refuses. This
# file runs the REAL validate_against_schema() pass main()'s step 6 runs.
# ---------------------------------------------------------------------------


def _with_patterns(patterns):
    profile = make_base_profile()
    profile["validation"]["forbidden_patterns"] = patterns
    return profile


def test_forbidden_patterns_absent_is_valid():
    """Every project predating #520 has no such key; that must stay valid."""
    assert schema_errors(make_base_profile()) == []


def test_forbidden_patterns_empty_list_is_valid():
    assert schema_errors(_with_patterns([])) == []


def test_forbidden_patterns_well_formed_entry_is_valid():
    assert schema_errors(_with_patterns([
        {"id": "adjacent-asterisks", "pattern": r"\*{2,}", "message": "separate the spans"},
    ])) == []


def test_forbidden_patterns_unknown_property_rejected():
    errors = schema_errors(_with_patterns([
        {"id": "x", "pattern": "y", "message": "z", "severity": "blocker"},
    ]))
    assert len(errors) == 1
    assert "severity" in errors[0]


# "rule\n" is the case a `$`-anchored pattern silently ADMITS: jsonschema
# evaluates `pattern` with Python's `re`, whose `$` also matches before a
# trailing newline. A double-quoted YAML scalar carries that newline through to
# Step 0, and the id is then interpolated into a declaration WARN without the
# whole-line normalization the hit warnings get.
@pytest.mark.parametrize(
    "bad_id", ["Has-Capitals", "-leading-dash", "has space", "", "a" * 65, "rule\n"]
)
def test_forbidden_patterns_bad_id_rejected(bad_id):
    assert schema_errors(_with_patterns([
        {"id": bad_id, "pattern": "y", "message": "z"},
    ])) != []


@pytest.mark.parametrize("missing", ["id", "pattern", "message"])
def test_forbidden_patterns_missing_required_key_rejected(missing):
    entry = {"id": "x", "pattern": "y", "message": "z"}
    del entry[missing]
    errors = schema_errors(_with_patterns([entry]))
    assert len(errors) == 1
    assert missing in errors[0]


def test_forbidden_patterns_message_may_carry_newlines():
    """The load-bearing case behind NOT forbidding CR/LF in `message`.

    PyYAML loads a folded `>` scalar with a trailing newline and a literal `|`
    scalar with its breaks intact, so a CR/LF prohibition here would refuse
    ordinary block authoring at Step 0 -- while W7 would never notice, since
    it does not schema-validate. Both scalar styles are parsed for real
    rather than hand-writing the resulting string, so the test fails if
    PyYAML's own behaviour is not what this reasoning assumes."""
    import yaml

    loaded = yaml.safe_load(
        "folded: >\n"
        "  two or more adjacent asterisks reach\n"
        "  the reader verbatim\n"
        "literal: |\n"
        "  first line\n"
        "  second line\n"
    )
    assert loaded["folded"].endswith("\n"), repr(loaded["folded"])
    assert "\n" in loaded["literal"], repr(loaded["literal"])

    for style in ("folded", "literal"):
        assert schema_errors(_with_patterns([
            {"id": "adjacent-asterisks", "pattern": r"\*{2,}", "message": loaded[style]},
        ])) == [], style


def test_forbidden_patterns_must_be_a_list_of_objects():
    assert schema_errors(_with_patterns({"id": "x"})) != []
    assert schema_errors(_with_patterns(["just a string"])) != []




# ---------------------------------------------------------------------------
# validation.terms (#199) -- the Step 0 schema gate.
#
# These belong HERE for the same reason the forbidden_patterns cases above do:
# final_audit.py reaches profile.yml through vd.load_profile(), which only
# yaml.safe_load()s and never validates. A W7-side assertion about what the
# schema accepts would pass whether or not the schema says so.
#
# There is one further reason this set is wider than it looks. #199 chose
# profile.yml over a new ${durable_root}/terms.json precisely BECAUSE
# profile.schema.json settles shape at Step 0 -- so the shapes that argument
# rests on (a null, a mapping where a list belongs, a scalar list item) are
# pinned here rather than assumed. If any of them silently validated, W7 would
# read an invalid declaration as no declaration at all: a run that checked
# nothing, reading exactly like a run whose every term held.
# ---------------------------------------------------------------------------


def _with_terms(terms):
    profile = make_base_profile()
    profile["validation"]["terms"] = terms
    return profile


def test_terms_absent_is_valid():
    """Every project predating #199 has no such key; that must stay valid."""
    assert schema_errors(make_base_profile()) == []


def test_terms_empty_list_is_valid():
    assert schema_errors(_with_terms([])) == []


def test_terms_well_formed_entry_is_valid():
    assert schema_errors(_with_terms([
        {"source_form": "président", "target_form": "президент"},
    ])) == []


@pytest.mark.parametrize("missing", ["source_form", "target_form"])
def test_terms_missing_required_key_rejected(missing):
    entry = {"source_form": "président", "target_form": "президент"}
    del entry[missing]
    errors = schema_errors(_with_terms([entry]))
    assert len(errors) == 1
    assert missing in errors[0]


@pytest.mark.parametrize("stray", ["id", "message", "note", "severity"])
def test_terms_unknown_property_rejected(stray):
    """The declaration is deliberately a bare pair. `id` and `message` are named
    among these because the sibling forbidden_patterns entry HAS both, so an
    operator copying that shape must be told at Step 0, not silently ignored."""
    errors = schema_errors(_with_terms([
        {"source_form": "président", "target_form": "президент", stray: "x"},
    ]))
    assert len(errors) == 1
    assert stray in errors[0]


@pytest.mark.parametrize("blank", ["", "   ", "\t", "\n"])
@pytest.mark.parametrize("field", ["source_form", "target_form"])
def test_terms_blank_form_rejected(field, blank):
    """`minLength: 1` alone would admit a form that is only whitespace, which
    W7 would then look for in every carrier and find in most of them. The
    `\\S` pattern is what refuses it."""
    entry = {"source_form": "président", "target_form": "президент"}
    entry[field] = blank
    assert schema_errors(_with_terms([entry])) != []


@pytest.mark.parametrize(
    "malformed",
    [
        None,
        {"source_form": "président", "target_form": "президент"},
        ["président"],
        "président",
        42,
    ],
    ids=["null", "mapping-not-list", "list-of-list", "bare-string", "number"],
)
def test_terms_malformed_container_rejected(malformed):
    """The three shapes #199's own argument for profile.yml rests on, plus two
    neighbours. Each must be refused at Step 0, because final_audit.py's reader
    treats anything that is not a list of mappings as NO declaration -- which is
    the correct thing for it to do, and the wrong thing for the run to mean."""
    assert schema_errors(_with_terms(malformed)) != []


def test_terms_scalar_list_item_rejected():
    assert schema_errors(_with_terms(["président"])) != []


# ---------------------------------------------------------------------------
# Step 14 (#726): output.target naming a built-in adapter that has not shipped.
#
# `epub` is in the schema enum and maps to `render_epub`, which does not exist.
# Before this check the profile cleared every gate and the failure landed at W9
# assembly, with the whole book translated, reviewed and converged. The check is
# deliberately NARROW: it fires only under `v1_scope: assembled_book`, only for
# a built-in target, and only when that target's module file is absent.
# ---------------------------------------------------------------------------


_NO_TARGET_KEY = object()


def _output_profile(v1_scope, target=_NO_TARGET_KEY):
    profile = make_base_profile()
    profile["output"]["v1_scope"] = v1_scope
    if target is not _NO_TARGET_KEY:
        profile["output"]["target"] = target
    return profile


def test_assembled_book_with_unshipped_epub_target_is_fatal():
    """Asserts DELEGATION, not a second copy of the message contract. The
    halt text has one home -- output_resolve -- and output_resolve.test.py
    pins its content (the missing filename plus all three ways out); pinning
    the same four strings here too would red two files for one reword."""
    errors = pv.check_output_target_shipped(_output_profile("assembled_book", "epub"))

    assert len(errors) == 1
    with pytest.raises(pv.output_resolve.OutputResolveError) as excinfo:
        pv.output_resolve.assert_builtin_adapter_shipped("epub")
    assert errors[0] == str(excinfo.value)


def test_step_0_main_actually_reports_the_unshipped_epub_halt(tmp_path, capsys):
    """The WIRING, not the checker. Every other case here calls
    `check_output_target_shipped` directly, so deleting its one line in
    `main()`'s procedural block would leave them all green while the real
    Step 0 CLI silently accepted an assembled EPUB -- acceptance criterion 1
    lost with nothing red. This drives the real entry point instead.

    The fixture's `source.path` and `durable_root` are the baseline's
    non-existent placeholders, so main() collects their own fatal lines too;
    that is fine and deliberate (it is a collect-everything validator, and a
    filesystem fixture would add nothing this assertion needs). The line that
    can only come from step 14 is the one asserted."""
    profile = _output_profile("assembled_book", "epub")
    profile_path = tmp_path / "profile.yml"
    profile_path.write_text(pv.yaml.safe_dump(profile), encoding="utf-8")

    with pytest.raises(SystemExit) as excinfo:
        pv.main(["--profile", str(profile_path)])

    stderr = capsys.readouterr().err
    assert excinfo.value.code == 1
    assert "render_epub.py" in stderr
    assert "output.target" in stderr


def test_inert_epub_target_under_the_default_scope_is_accepted():
    """The over-catch guard. Under `segment_drafts_and_audit` nothing ever
    reads output.target -- Step 0d is a no-op and W9 does not run -- so a
    declared `epub` is inert. Refusing it would reject profiles that validate
    today, for a target they never use."""
    profile = _output_profile("segment_drafts_and_audit", "epub")
    assert pv.check_output_target_shipped(profile) == []


def test_absent_output_target_under_the_default_scope_is_accepted():
    """`output.target` is OPTIONAL -- `output`'s own schema `required` list is
    just v1_scope + destination, and this file's own baseline fixture omits the
    key entirely. Reading it with `[...]` instead of `.get` would KeyError on a
    profile Step 0 accepts today."""
    profile = make_base_profile()
    assert "target" not in profile["output"]
    assert pv.check_output_target_shipped(profile) == []


def test_absent_output_target_under_assembled_book_is_not_this_checks_business():
    """Pins the non-expansion: a missing target under `assembled_book` IS a
    real problem, but it is `resolve_output_adapter`'s ("no output.target set")
    at Step 0d, and this check must not quietly grow into it."""
    assert pv.check_output_target_shipped(_output_profile("assembled_book")) == []


def test_shipped_obsidian_target_under_assembled_book_is_accepted():
    assert pv.check_output_target_shipped(_output_profile("assembled_book", "obsidian")) == []


def test_custom_target_is_untouched_at_step_0():
    """`custom` with a null renderer_path is the documented co-design STARTING
    state, and its HALT belongs to Step 0d/W9 -- a project may translate and
    converge its whole book while the renderer is still being designed. Pulling
    that halt forward to Step 0 would block the project outright."""
    profile = _output_profile("assembled_book", "custom")
    profile["output"]["adapter_config"] = {
        "obsidian": None,
        "epub": None,
        "custom": {"renderer_path": None},
    }
    assert pv.check_output_target_shipped(profile) == []
# ===========================================================================
# #727 -- glossary.enabled: boolean master switch, schema-level cases
# ===========================================================================


def test_glossary_enabled_boolean_true_passes_schema():
    profile = make_base_profile()
    profile["glossary"]["enabled"] = True
    assert schema_errors(profile) == []


def test_glossary_enabled_boolean_false_passes_schema():
    profile = make_base_profile()
    profile["glossary"]["enabled"] = False
    assert schema_errors(profile) == []


def test_glossary_enabled_string_rejected_by_schema():
    profile = make_base_profile()
    profile["glossary"]["enabled"] = "true"  # a string, not the boolean the schema demands
    errors = schema_errors(profile)
    assert errors != []
    assert any("enabled" in e and "boolean" in e for e in errors), errors


def test_glossary_enabled_absent_still_valid():
    # make_base_profile() already omits glossary.enabled -- absent means
    # true (glossary.enabled's own schema description), so every profile
    # predating #727 keeps validating unchanged.
    profile = make_base_profile()
    assert "enabled" not in profile["glossary"]
    assert schema_errors(profile) == []


# ===========================================================================
# #727 -- the placeholder scan now runs BEFORE jsonschema (main()'s step 5,
# ahead of step 6): a profile carrying BOTH an unanswered CHOOSE_ sentinel
# AND an unrelated, genuine schema violation must report the questionnaire,
# never the schema error -- schema validation must never even run.
# ===========================================================================


def _write_profile_yaml(path, profile: dict) -> None:
    import yaml as real_yaml

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(real_yaml.safe_dump(profile, sort_keys=False), encoding="utf-8")


def test_sentinel_and_schema_violation_together_report_questionnaire_not_schema_error(tmp_path, capsys):
    """The naive ("schema-first") ordering would halt on
    verse_policy.mode's own enum violation and never mention the sentinel at
    all -- or, depending on which error jsonschema's own validator happens
    to sort first, might report both mixed together. #727's fix makes the
    placeholder/sentinel scan its own fail-fast step, strictly before
    schema validation, so this profile must halt naming ONLY the sentinel,
    with no trace of the schema violation anywhere in the output."""
    profile = make_base_profile()
    profile["glossary"]["research_mode"] = "CHOOSE_live_or_offline"
    # An unrelated, genuine, UNCONDITIONAL schema violation -- nothing to do
    # with any placeholder or sentinel.
    profile["verse_policy"] = {"mode": "not_a_real_verse_policy_mode", "threshold_lines": None}

    profile_path = tmp_path / ".claude" / "literary-translator" / "profile.yml"
    _write_profile_yaml(profile_path, profile)

    with pytest.raises(SystemExit) as exc_info:
        pv.main(["--profile", str(profile_path)])
    captured = capsys.readouterr()

    assert exc_info.value.code == 1
    assert "Step 0 needs these intake decisions answered" in captured.err, captured.err
    assert "glossary.research_mode" in captured.err
    assert "CHOOSE_live_or_offline" in captured.err
    assert "not_a_real_verse_policy_mode" not in captured.err, (
        f"schema validation must never run once a placeholder sentinel "
        f"survives the earlier scan:\n{captured.err}"
    )
    assert "verse_policy" not in captured.err, captured.err


# ===========================================================================
# #727 -- KNOB_QUESTIONS: per-knob question appended to the sentinel's own
# error line, a path with no entry keeps the OLD message verbatim, and the
# shipped-sentinel-set <-> KNOB_QUESTIONS-key-set drift guard (both
# directions, derived by walking the parsed example -- never a hand-typed
# list, which would freeze whatever the two sets happened to be at authoring
# time and stop detecting drift).
# ===========================================================================


def _set_dotted(d: dict, dotted_path: str, value) -> dict:
    parts = dotted_path.split(".")
    cursor = d
    for part in parts[:-1]:
        cursor = cursor.setdefault(part, {})
    cursor[parts[-1]] = value
    return d


@pytest.mark.parametrize("dotted_path", sorted(pv.KNOB_QUESTIONS.keys()))
def test_knob_question_appears_on_its_own_sentinel_error_line(dotted_path):
    """Every dotted path with a KNOB_QUESTIONS entry gets its plain-language
    question appended to THAT field's own sentinel error line -- not merely
    present somewhere in the scan's output, which a per-field reader would
    never see if it were attached to some OTHER field's line instead."""
    profile = _set_dotted({}, dotted_path, f"CHOOSE_{dotted_path.replace('.', '_')}_placeholder")
    errors = pv.scan_placeholders(profile)
    matching = [e for e in errors if e.startswith(f"{dotted_path}:")]
    assert len(matching) == 1, f"expected exactly one error for {dotted_path!r}; got:\n{errors}"
    assert pv.KNOB_QUESTIONS[dotted_path] in matching[0], (
        f"expected the KNOB_QUESTIONS text for {dotted_path!r} to appear on "
        f"its own sentinel error line; got:\n{matching[0]}"
    )


def test_knob_questions_carry_the_shared_contracts_frozen_substance():
    """The shared #727 plan-review contract froze specific substantive
    clauses per knob (wording may be reflowed, but the substance must
    survive) -- pins them directly against KNOB_QUESTIONS's own values,
    independent of whether the generic per-path test above would catch a
    later edit that reworded away the substance while leaving the mapping's
    keys and mechanics intact."""
    assertions = {
        "glossary.enabled": ("W3 glossary pass", "EMPTY canon", "NEW:"),
        "glossary.research_mode": ('basis:"established"', "glossary.enabled` is false"),
        "footnotes.apparatus_policy": (
            "translate_all", "preserve_source", "omit_apparatus", "body_refs_only",
        ),
        "output.v1_scope": ("segment_drafts_and_audit", "assembled_book"),
        "output.target": (
            "obsidian", "epub", "custom", "assembled_book",
            # Codex review follow-up: pins the substantive availability
            # clause itself, not merely the three enum names -- a test that
            # only pinned the names would stay green if this cost/caveat
            # disappeared from the question entirely.
            "has no renderer yet", "co-designing a renderer",
            # #726 rebase: "has no renderer yet" alone survived BOTH the
            # pre-#726 wording (which only claimed epub "halts at assembly",
            # i.e. at W9) and the corrected post-#726 wording -- it would not
            # by itself catch a regression back to the stale W9-timing claim.
            # Pin the corrected TIMING specifically: #726 moved this refusal
            # all the way to Step 0 (check_output_target_shipped), never W9.
            "refused at Step 0",
        ),
        "source.adapter_config.plain_text.verse_detection": (
            "none_confirmed", "regex", "source.format: plain_text",
        ),
        "source.adapter_config.plain_text.footnotes": (
            "none_confirmed", "markdown_ref", "custom_regex",
        ),
    }
    assert set(assertions.keys()) == set(pv.KNOB_QUESTIONS.keys()), (
        "this pinning test itself has drifted out of sync with KNOB_QUESTIONS's "
        "own key set -- update the assertions table above, not just the code"
    )
    for dotted_path, phrases in assertions.items():
        question = pv.KNOB_QUESTIONS[dotted_path]
        for phrase in phrases:
            assert phrase in question, (
                f"KNOB_QUESTIONS[{dotted_path!r}] dropped the frozen contract "
                f"clause {phrase!r}; got:\n{question}"
            )


def test_sentinel_with_no_knob_questions_entry_still_errors_without_a_question():
    """A dotted path with NO KNOB_QUESTIONS entry must still FATAL on a
    surviving CHOOSE_ sentinel -- it just loses the appended question, never
    the error itself. Pins the OLD message verbatim (scan_placeholders() /
    the CHOOSE_-sentinel half keeps its pre-#727 wording exactly when there
    is no question to append)."""
    dotted_path = "target.language.register_notes"
    assert dotted_path not in pv.KNOB_QUESTIONS, (
        f"{dotted_path!r} is now a real knob -- pick a different unmapped "
        f"path for this negative control"
    )
    profile = _set_dotted({}, dotted_path, "CHOOSE_some_hypothetical_choice")
    errors = pv.scan_placeholders(profile)
    assert errors == [
        f"{dotted_path}: still has the shipped placeholder sentinel "
        f"'CHOOSE_some_hypothetical_choice' -- consciously choose one of its "
        f"documented real values before proceeding."
    ], errors


def test_recursive_yaml_alias_errors_instead_of_recursion_error():
    """#727: `_walk_strings()` gained a per-walk RECURSION-STACK guard (an
    `id()` added right before descending into a dict/list and removed again
    in a `finally` on unwind -- not a whole-walk visited SET, see the
    shared-alias test below for why that distinction matters) because the
    placeholder scan now runs ahead of jsonschema's own shape check, which
    would otherwise have rejected a cyclic document on TYPE grounds before
    this scan ever saw it. `title: &loop [*loop]` is exactly the shape a
    hand-authored profile.yml cannot construct on its own but a YAML
    anchor/alias can -- PyYAML parses it into a list that contains itself.

    Verified red against the pre-#727 profile_validate.py (git rev b9b20d6,
    before this branch's own changes): the unguarded `_walk_strings()`
    recurses forever and dies with a raw RecursionError, never an ordinary
    Step 0 ERROR line -- confirmed by loading that revision's module
    directly and driving this exact fixture through it."""
    import yaml as real_yaml

    profile = real_yaml.safe_load("title: &loop [*loop]\n")
    assert isinstance(profile["title"], list)
    assert profile["title"][0] is profile["title"], (
        "fixture is not genuinely self-referential -- PyYAML's alias "
        "resolution did not produce the cycle this test needs"
    )

    # Must not raise RecursionError -- the whole point of the guard.
    errors = pv.scan_placeholders(profile)
    assert errors == [], (
        "a cyclic alias carries no string leaves of its own -- scan_placeholders "
        "must return cleanly, not raise, and must not report a phantom "
        "violation for a structure with no actual string content"
    )


def test_shared_non_cyclic_alias_is_reported_at_every_path_it_occurs():
    """Companion to the cyclic-alias test above, pinning the actual reason
    #727 moved from a whole-walk visited SET to a per-walk recursion STACK: a
    shared, non-cyclic YAML alias reached via two SIBLING paths (never an
    ancestor-descendant chain) is not a cycle at all, and a whole-walk set
    would have silently under-reported it -- the alias's id() would already
    be marked visited by the time the SECOND path reached it, so that path's
    own sentinel would lose its questionnaire line even though nothing about
    it is cyclic.

    Codex's own fixture: `x_alias: &g {enabled: CHOOSE_true_or_false,
    research_mode: offline}` aliased a second time as `glossary: *g` --
    `glossary.enabled` and `x_alias.enabled` are the SAME underlying dict
    entry, reached via two unrelated top-level keys. Both dotted paths must
    be reported, each with its own KNOB_QUESTIONS text intact (glossary.enabled
    has a KNOB_QUESTIONS entry; x_alias.enabled does not, so that path keeps
    the plain, question-less message -- see the no-entry negative control
    above)."""
    import yaml as real_yaml

    profile = real_yaml.safe_load(
        "x_alias: &g {enabled: CHOOSE_true_or_false, research_mode: offline}\n"
        "glossary: *g\n"
    )
    assert profile["glossary"] is profile["x_alias"], (
        "fixture is not genuinely a shared alias -- PyYAML did not resolve "
        "both keys to the SAME underlying mapping object"
    )

    errors = pv.scan_placeholders(profile)
    matching_glossary = [e for e in errors if e.startswith("glossary.enabled:")]
    matching_alias = [e for e in errors if e.startswith("x_alias.enabled:")]
    assert len(matching_glossary) == 1, (
        f"expected exactly one error for glossary.enabled (reached via the "
        f"SECOND sibling path to the shared alias); got:\n{errors}"
    )
    assert len(matching_alias) == 1, (
        f"expected exactly one error for x_alias.enabled (reached via the "
        f"FIRST sibling path to the shared alias); got:\n{errors}"
    )
    assert pv.KNOB_QUESTIONS["glossary.enabled"] in matching_glossary[0], (
        f"glossary.enabled has a KNOB_QUESTIONS entry -- its own error line "
        f"must carry the question; got:\n{matching_glossary[0]}"
    )
    assert "x_alias.enabled" not in pv.KNOB_QUESTIONS, (
        "x_alias.enabled is now a real knob -- pick a different unmapped "
        "alias name for this negative half of the fixture"
    )
    assert matching_alias[0] == (
        "x_alias.enabled: still has the shipped placeholder sentinel "
        "'CHOOSE_true_or_false' -- consciously choose one of its documented "
        "real values before proceeding."
    ), matching_alias[0]


def test_knob_questions_matches_shipped_sentinels_set_equality():
    """Drift guard: the set of dotted paths carrying a CHOOSE_ sentinel in
    the REAL shipped assets/profile.example.yml must equal KNOB_QUESTIONS's
    own key set, in BOTH directions -- derived by WALKING the parsed
    example, never a hand-typed list (which would freeze whatever the two
    sets happened to be at authoring time and stop detecting drift). A
    KNOB_QUESTIONS entry for a sentinel the example no longer ships is dead
    weight; a shipped sentinel with no KNOB_QUESTIONS entry silently loses
    its question (still errors, per the no-entry negative control above,
    but the operator gets no guidance for that ONE decision)."""
    import yaml as real_yaml

    example = real_yaml.safe_load(EXAMPLE_PATH.read_text(encoding="utf-8"))
    shipped_sentinel_paths = {
        location
        for location, value in pv._walk_strings(example)
        if value.startswith(pv.CHOOSE_PREFIX)
    }
    knob_question_paths = set(pv.KNOB_QUESTIONS.keys())
    assert shipped_sentinel_paths == knob_question_paths, (
        f"shipped-but-unmapped: {shipped_sentinel_paths - knob_question_paths}\n"
        f"mapped-but-not-shipped: {knob_question_paths - shipped_sentinel_paths}"
    )


# ===========================================================================
# #727 -- glossary.enabled: false vs glossary.skeptic_pass.enabled: true
# cross-field contradiction, plus its two negative controls.
# ===========================================================================


def test_glossary_disabled_conflicts_with_active_skeptic_pass_is_fatal():
    profile = {"glossary": {"enabled": False, "skeptic_pass": {"enabled": True}}}
    errors = pv.check_glossary_disabled_conflicts_with_skeptic_pass(profile)
    assert len(errors) == 1
    assert "glossary.enabled" in errors[0]
    assert "glossary.skeptic_pass.enabled" in errors[0]


def test_glossary_enabled_absent_with_skeptic_pass_enabled_stays_valid():
    """Negative control (the point of the finding that produced it): an
    ABSENT glossary.enabled means true (the schema's own default) -- every
    profile written before this key existed, including one with
    skeptic_pass.enabled: true, must keep validating unchanged. A falsy
    `.get()` implementation (treating absence the same as an explicit
    False) would break this and every existing skeptic-enabled profile in
    the wild."""
    profile = {"glossary": {"skeptic_pass": {"enabled": True}}}
    assert pv.check_glossary_disabled_conflicts_with_skeptic_pass(profile) == []


def test_glossary_enabled_explicit_true_with_skeptic_pass_enabled_stays_valid():
    """Second negative control: an EXPLICIT glossary.enabled: true beside
    skeptic_pass.enabled: true is the ordinary, fully-enabled configuration
    and must never be flagged."""
    profile = {"glossary": {"enabled": True, "skeptic_pass": {"enabled": True}}}
    assert pv.check_glossary_disabled_conflicts_with_skeptic_pass(profile) == []


def test_glossary_disabled_with_skeptic_pass_absent_is_not_a_conflict():
    profile = {"glossary": {"enabled": False}}
    assert pv.check_glossary_disabled_conflicts_with_skeptic_pass(profile) == []


def test_glossary_disabled_with_skeptic_pass_explicitly_false_is_not_a_conflict():
    profile = {"glossary": {"enabled": False, "skeptic_pass": {"enabled": False}}}
    assert pv.check_glossary_disabled_conflicts_with_skeptic_pass(profile) == []


if __name__ == "__main__":
    import sys

    sys.exit(pytest.main([__file__, "-v"]))
