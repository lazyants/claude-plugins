"""tests/profile_example_validation.test.py

Targets ``assets/profile.example.yml`` + ``profile_validate.py``, split into
exactly THREE cases (per the plugin's own test enumeration):

  1. A missing ``profile.yml`` triggers Step 0's auto-copy-then-halt path --
     the shipped example is copied to the target path verbatim, and the run
     halts (non-zero exit) naming the path and instructing the user to fill
     in every placeholder.
  2. The shipped example loaded VERBATIM (placeholders intact) is FATALLY
     rejected. See the NOTE below on why this is checked at *two* levels
     (the real CLI entry point, and ``scan_placeholders()`` directly).
  3. A fixture with every placeholder replaced by a real value, otherwise
     structurally IDENTICAL to the shipped example -- including the
     currently-INACTIVE ``adapter_config.plain_text`` sub-block's
     ``blank_line_threshold: 2`` -- passes Step 0 cleanly. This
     regression-locks the per-field-typed "inactive format sub-block" schema
     loosening: ``adapter_config.plain_text`` keeps its own fields' base
     types (``blank_line_threshold`` stays ``integer|null``, non-null is
     fine) even while ``source.format`` is NOT ``plain_text`` -- only the
     format-specific if/then rules (e.g. "if method==blank_line_run then
     blank_line_threshold is REQUIRED to be a non-null integer") are gated
     off, never the field's own type.

NOTE on case 2's architecture (verified against the real script, not
assumed): Step 0 is fail-fast across steps 1-5 (existence -> dependency
preflight -> parse/profile_version -> unknown-top-level-keys -> whole-file
jsonschema validation) and only becomes a "collect everything, then report
everything" validator across steps 6-13 (the procedural checks), which is
where ``scan_placeholders()`` (step 7) lives. Direct inspection of
``profile.schema.json`` shows exactly ONE placeholder-bearing field with an
*unconditional* schema-level restriction: ``glossary.research_mode``'s
top-level ``enum: ["live", "offline"]`` is not gated behind any
``source.format`` conditional. Every OTHER shipped placeholder (the two
``/ABS/PATH/TO/...`` paths, the book-title placeholder, and the
``adapter_config.plain_text`` CHOOSE_-sentinels) sits inside either a plain
``minLength: 1`` string field or a format-gated sub-block -- so while the
shipped example's active format is ``gutenberg_epub``, those fields are
merely well-typed, non-empty strings from the schema's point of view (this
is the very loosening case 3 above regression-locks) and never independently
fail schema.

The practical consequence, confirmed by running the real script against the
verbatim shipped example: a single end-to-end CLI invocation halts at Step 5
having named only ``glossary.research_mode`` -- it never reaches Step 7's
``scan_placeholders()``, so it does NOT enumerate every placeholder in one
run. That is genuine, documented behavior (see profile_validate.py's own
module docstring: "Only once schema validation passes, run the procedural
checks..."), not a test artifact. So this file asserts BOTH halves honestly:
the real CLI-level outcome (exit 1, exactly the one schema-blocking field
named) via ``test_verbatim_shipped_example_is_fatally_rejected_by_cli``, and
a direct exercise of ``scan_placeholders()`` against the same verbatim,
unmodified parsed document via
``test_verbatim_shipped_example_scan_placeholders_names_every_placeholder``,
which is the actual mechanism responsible for the "names every placeholder"
guarantee and is what would fire for every remaining placeholder once a user
fixes ``glossary.research_mode`` and re-runs.
"""
import importlib.util
from pathlib import Path

import pytest
import yaml

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
ASSETS_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets"
SCRIPT_PATH = ASSETS_DIR / "scripts" / "profile_validate.py"
EXAMPLE_PATH = ASSETS_DIR / "profile.example.yml"


def _load_profile_validate():
    """Imports profile_validate.py fresh from its real, shipped install
    path -- ``assets/scripts/`` is not a package on sys.path, so a plain
    ``import`` won't reach it. A fresh module per call (see the ``pv``
    fixture below) avoids any cross-test state leakage through the
    module-level ``yaml``/``jsonschema`` handles ``dependency_preflight()``
    populates."""
    spec = importlib.util.spec_from_file_location(
        "profile_validate_under_test", SCRIPT_PATH
    )
    assert spec is not None and spec.loader is not None, f"could not load spec for {SCRIPT_PATH}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def pv():
    assert SCRIPT_PATH.is_file(), f"expected profile_validate.py at {SCRIPT_PATH}"
    assert EXAMPLE_PATH.is_file(), f"expected profile.example.yml at {EXAMPLE_PATH}"
    return _load_profile_validate()


def _run_main(pv_module, profile_path: Path, capsys):
    """Invokes the real CLI entry point exactly as the plugin does
    (``profile_validate.py --profile <path>``) and returns
    ``(exit_code, stdout, stderr)``. ``main()`` always ends in
    ``sys.exit()``, never a bare return, so every call site must catch
    ``SystemExit``."""
    with pytest.raises(SystemExit) as exc_info:
        pv_module.main(["--profile", str(profile_path)])
    captured = capsys.readouterr()
    return exc_info.value.code, captured.out, captured.err


def _build_filled_profile(durable_root: Path, source_path: Path) -> dict:
    """Builds a profile dict that is structurally IDENTICAL to
    ``assets/profile.example.yml`` (same keys, same nesting, same shape),
    with every shipped placeholder replaced by a real, valid value --
    EXCEPT ``adapter_config.plain_text.segmentation.blank_line_threshold``,
    which was already a real value (``2``) in the shipped example, not a
    placeholder, and is kept exactly as-is per the spec's explicit call-out
    (this is the field that regression-locks the inactive-format schema
    loosening: ``plain_text`` sits inert because ``source.format`` is
    ``gutenberg_epub`` here, yet its own ``blank_line_threshold`` field still
    validates as a plain, correctly-typed integer)."""
    return {
        "profile_version": 1,
        "project": {
            "title": "Les Historiettes de Tallemant des Reaux, tome 3",
            "durable_root": str(durable_root),
            "durable_root_adopt_existing": False,
            "pipeline_version": "v1",
            "max_segment_words": 15000,
        },
        "source": {
            "format": "gutenberg_epub",
            "path": str(source_path),
            "gutenberg_id": None,
            "language": {
                "code": "fr",
                "particle_config": "fr.json",
                "smoke_test": {"report_path": None},
            },
            "adapter_config": {
                "gutenberg_epub": {
                    "spine_overrides": {},
                    "frontback_overrides": {},
                },
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
            "language": {
                "code": "ru",
                "register_notes": "ty/vy politeness distinction -- see style_bible.md section B",
            },
        },
        "verse_policy": {
            "mode": "full_rhymed_plus_literal",
            "threshold_lines": None,
        },
        "engine": {
            "effort": "high",
            "max_fix_rounds": 4,
            "batch_agent_cap": 1000,
        },
        "footnotes": {"apparatus_policy": "translate_all"},
        "glossary": {"research_mode": "offline"},
        "validation": {"untranslated_sentinel": "нет перевода"},
        "output": {
            "v1_scope": "segment_drafts_and_audit",
            "destination": str(durable_root / "out"),
        },
    }


# ---------------------------------------------------------------------------
# Case 1: missing profile.yml -> Step 0 auto-copies the shipped example
# verbatim to the target path, then halts (never runs dependency preflight
# or schema validation in this branch at all).
# ---------------------------------------------------------------------------

def test_missing_profile_triggers_autocopy_and_halt(pv, tmp_path, capsys):
    profile_path = tmp_path / ".claude" / "literary-translator" / "profile.yml"
    assert not profile_path.exists()

    exit_code, _out, err = _run_main(pv, profile_path, capsys)

    assert exit_code != 0, "a freshly auto-copied profile must halt, not proceed"
    assert profile_path.exists(), "Step 0 must auto-copy the shipped example on absence"
    assert profile_path.read_bytes() == EXAMPLE_PATH.read_bytes(), (
        "the auto-copied profile must be byte-identical to assets/profile.example.yml"
    )
    assert str(profile_path) in err, err
    assert "placeholder" in err.lower(), err


def test_missing_profile_autocopy_does_not_touch_an_existing_profile(pv, tmp_path, capsys):
    """Companion sanity check for case 1's own guard: an EXISTING profile.yml
    (however malformed its content) must never be silently overwritten by the
    auto-copy branch -- existence is checked fresh, not "does it look
    filled-in". This is what makes the halt on a genuinely-absent file safe
    to rely on elsewhere (e.g. scaffold_idempotency.test.py)."""
    profile_path = tmp_path / ".claude" / "literary-translator" / "profile.yml"
    profile_path.parent.mkdir(parents=True)
    sentinel_content = b"not a real profile, just a sentinel the auto-copy must not clobber\n"
    profile_path.write_bytes(sentinel_content)

    # Whatever happens next (this content will fail YAML/schema validation
    # further down the pipeline), the auto-copy branch itself must not fire.
    with pytest.raises(SystemExit):
        pv.main(["--profile", str(profile_path)])

    assert profile_path.read_bytes() == sentinel_content


# ---------------------------------------------------------------------------
# Case 2: the shipped example loaded VERBATIM (placeholders intact) is
# fatally rejected. See the module docstring's NOTE for why this is split
# into a CLI-level assertion and a scan_placeholders()-level assertion.
# ---------------------------------------------------------------------------

def test_verbatim_shipped_example_is_fatally_rejected_by_cli(pv, tmp_path, capsys):
    profile_path = tmp_path / ".claude" / "literary-translator" / "profile.yml"
    profile_path.parent.mkdir(parents=True)
    profile_path.write_bytes(EXAMPLE_PATH.read_bytes())

    exit_code, _out, err = _run_main(pv, profile_path, capsys)

    assert exit_code != 0, "the verbatim shipped example must never pass Step 0"
    # glossary.research_mode is the ONE placeholder-bearing field with an
    # unconditional (non-format-gated) schema enum restriction, confirmed by
    # direct inspection of profile.schema.json -- Step 5's schema validation
    # halts on it before Step 7 (scan_placeholders) ever runs.
    assert "glossary.research_mode" in err, err
    assert "CHOOSE_live_or_offline" in err, err


def test_verbatim_shipped_example_scan_placeholders_names_every_placeholder(pv):
    """Exercises scan_placeholders() -- the actual Step 7 mechanism behind
    the "names every placeholder" guarantee -- directly against the
    verbatim, unmodified shipped example. This is what fires in full once a
    user has fixed the one schema-blocking field (glossary.research_mode)
    and re-runs Step 0; regression-locks that the mechanism itself still
    correctly names EVERY remaining placeholder in one pass, not just the
    first one it encounters."""
    profile = yaml.safe_load(EXAMPLE_PATH.read_text(encoding="utf-8"))
    errors = pv.scan_placeholders(profile)
    joined = "\n".join(errors)

    # Every literal placeholder substring the module itself declares...
    for placeholder in pv.PLACEHOLDER_SUBSTRINGS:
        assert placeholder in joined, (
            f"expected placeholder {placeholder!r} to be named; got:\n{joined}"
        )
    # ...plus every CHOOSE_-prefixed sentinel actually shipped in the example.
    for sentinel in (
        "CHOOSE_none_confirmed_or_regex",
        "CHOOSE_none_confirmed_or_markdown_ref_or_custom_regex",
        "CHOOSE_live_or_offline",
    ):
        assert sentinel in joined, f"expected sentinel {sentinel!r} to be named; got:\n{joined}"

    # ...and each violation is attributed to its own field, by dotted path.
    for field in (
        "project.title",
        "project.durable_root",
        "source.path",
        "source.adapter_config.plain_text.verse_detection",
        "source.adapter_config.plain_text.footnotes",
        "glossary.research_mode",
        "output.destination",
    ):
        assert any(err.startswith(f"{field}:") for err in errors), (
            f"expected an error attributed to {field!r}; got:\n{joined}"
        )


# ---------------------------------------------------------------------------
# Case 3: every placeholder replaced with a real value, otherwise
# structurally identical (including the inactive plain_text sub-block's
# blank_line_threshold: 2) -> clean pass.
# ---------------------------------------------------------------------------

def test_fully_filled_fixture_structurally_identical_passes_cleanly(pv, tmp_path, capsys):
    durable_root = tmp_path / "book-project"
    source_path = tmp_path / "source.epub"
    source_path.write_bytes(b"fake epub bytes for the fixture")

    profile_data = _build_filled_profile(durable_root, source_path)
    # Sanity: the field this case exists to regression-lock is genuinely
    # present and unchanged from the shipped example's own value.
    assert (
        profile_data["source"]["adapter_config"]["plain_text"]["segmentation"]["blank_line_threshold"] == 2
    )
    assert profile_data["source"]["format"] == "gutenberg_epub", (
        "plain_text must stay the INACTIVE sub-block for this regression lock to mean anything"
    )

    profile_path = tmp_path / ".claude" / "literary-translator" / "profile.yml"
    profile_path.parent.mkdir(parents=True)
    profile_path.write_text(yaml.safe_dump(profile_data, sort_keys=False), encoding="utf-8")

    exit_code, out, err = _run_main(pv, profile_path, capsys)

    assert exit_code == 0, f"expected a clean Step 0 pass; stdout:\n{out}\nstderr:\n{err}"
    assert "OK -- Step 0 validation passed" in out, out


def test_fully_filled_fixture_no_placeholders_survive(pv, tmp_path):
    """Companion unit-level check: the case-3 fixture builder must not
    accidentally leave a placeholder substring or CHOOSE_ sentinel behind --
    if it did, the clean-pass assertion above would be vacuous (it could
    pass for the wrong reason, e.g. a schema bug that stopped enforcing the
    scan_placeholders step)."""
    durable_root = tmp_path / "book-project"
    source_path = tmp_path / "source.epub"
    profile_data = _build_filled_profile(durable_root, source_path)

    errors = pv.scan_placeholders(profile_data)

    assert errors == [], f"the case-3 fixture must be placeholder-free; got:\n{errors}"
