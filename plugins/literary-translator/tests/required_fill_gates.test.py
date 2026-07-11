"""tests/required_fill_gates.test.py -- regression-catcher for the
"2-day names-in-parentheses redo" bug class: a required-fill scaffold
marker that either (a) silently stops shipping in the real templates, or
(b) ships but scaffold_validate.py's own gate stops actually catching an
unfilled span.

Two independent layers, neither alone sufficient:

1. Shipped-template assertion -- reads the REAL, as-shipped
   `assets/templates/PLAN.template.md` / `style_bible.template.md` and
   asserts each still carries its required-fill marker id
   (`intake-proportionality-agreement` / `name-display-parentheses`) with
   the unfilled sentinel `LT_PLACEHOLDER_UNFILLED` still inside that span
   -- proves the gate exists in the actual shipped files, not just in a
   throwaway fixture. Reuses scaffold_validate.py's own real
   `scan_markers()` scanner rather than hand-rolling a second marker-span
   parser (same recipe as tests/scaffold_idempotency.test.py).

2. Behavioral CLI run -- builds a throwaway durable root, copies the REAL
   `scaffold_validate.py` into `<tmp>/scripts/` (it self-anchors its
   `DURABLE_ROOT` to `Path(__file__).resolve().parents[1]`, so this is the
   only way to exercise it against a fixture durable root at all), and
   populates every file the real script scans (`MARKER_SCAN_FILES` +
   `BRACKET_SCAN_FILES` + `TRAP_STRING_SCAN_FILES` -- a missing sibling is
   itself a fatal finding, so all must exist) with minimal, otherwise-clean
   content. Drives the real script as a subprocess end to end: unfilled ->
   non-zero exit naming the still-unfilled marker id(s); filled -> exit 0.

The #94 hardening adds two more layers over the same two mechanisms:

3. Bracket-placeholder scan -- unit assertions that `scan_bracket_placeholders`
   catches every real placeholder across all six shipped `*.template.md`
   files (including the line-wrapped one in `glossary_TASK.template.md`,
   which only whitespace normalization catches) while never flagging a
   hand-adapter's own legitimate editorial brackets (`[NOTE]`, `[CHAPTER I]`,
   ...); plus behavioral CLI runs proving an unfilled bracket fails, all-six
   filled passes, and mixed findings isolate per file.

4. Era/domain trap residue -- unit assertions proving the exact-substring
   `scan_trap_string` MISSES a separator-mangled / line-deleted trap while
   the co-occurrence `scan_era_domain_trap_residue` catches it, and that a
   fully replaced or fully removed callout passes both clean.
"""
from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
import unicodedata
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
ASSETS_ROOT = PLUGIN_ROOT / "skills" / "literary-translator" / "assets"
TEMPLATES_DIR = ASSETS_ROOT / "templates"
SCAFFOLD_VALIDATE_SCRIPT = ASSETS_ROOT / "scripts" / "scaffold_validate.py"
PLAN_TEMPLATE = TEMPLATES_DIR / "PLAN.template.md"
STYLE_BIBLE_TEMPLATE = TEMPLATES_DIR / "style_bible.template.md"

assert SCAFFOLD_VALIDATE_SCRIPT.is_file(), f"expected {SCAFFOLD_VALIDATE_SCRIPT} to exist"
assert PLAN_TEMPLATE.is_file(), f"expected {PLAN_TEMPLATE} to exist"
assert STYLE_BIBLE_TEMPLATE.is_file(), f"expected {STYLE_BIBLE_TEMPLATE} to exist"

INTAKE_MARKER_ID = "intake-proportionality-agreement"
NAME_DISPLAY_MARKER_ID = "name-display-parentheses"

# The six Step-0a-copied durable-root filenames mapped to their shipped
# `*.template.md` source. Keyed by the .md name scaffold_validate.py scans.
BRACKET_TEMPLATE_SOURCES = {
    "PLAN.md": "PLAN.template.md",
    "style_bible.md": "style_bible.template.md",
    "consistency_issues.md": "consistency_issues.template.md",
    "translate_TASK.md": "translate_TASK.template.md",
    "review_TASK.md": "review_TASK.template.md",
    "glossary_TASK.md": "glossary_TASK.template.md",
}

# Exact set of unfilled bracket placeholders each shipped template is
# expected to still carry (verified against the current corpus). A tight
# equality regression-catcher: it fails loudly if a template stops shipping
# a placeholder OR the scan starts over/under-matching.
EXPECTED_TEMPLATE_PLACEHOLDERS = {
    "PLAN.template.md": {"[PROJECT TITLE / AUTHOR / PERIOD -- fill in]"},
    "style_bible.template.md": {
        "[TARGET LANGUAGE]",
        "[PROJECT TITLE / AUTHOR / PERIOD -- fill in]",
    },
    "consistency_issues.template.md": {"[PROJECT TITLE / AUTHOR / PERIOD -- fill in]"},
    "translate_TASK.template.md": {
        "[SOURCE LANGUAGE]",
        "[TARGET LANGUAGE]",
        "[PROJECT TITLE / AUTHOR / PERIOD -- fill in]",
    },
    "review_TASK.template.md": {
        "[SOURCE LANGUAGE]",
        "[TARGET LANGUAGE]",
        "[PROJECT TITLE / AUTHOR / PERIOD -- fill in]",
    },
    "glossary_TASK.template.md": {
        "[SOURCE LANGUAGE]",
        "[TARGET LANGUAGE]",
        "[PROJECT TITLE / AUTHOR / PERIOD -- fill in]",
    },
}

# Editorial brackets a translator might legitimately write into a correctly
# hand-adapted file -- the closed-list scan must never flag these.
LEGITIMATE_HAND_ADAPTED_BRACKETS = [
    "See [NOTE] below.",
    "[CHAPTER I]",
    "[TRANSLATOR'S NOTE] -- kept archaic spelling.",
    "[SIC]",
    "[ILLEGIBLE PASSAGE]",
]

# The rejected round-1/2 design: a generic uppercase-bracket shape regex.
# Kept here only to PROVE (red-before-green) that it would have wrongly
# flagged the legitimate content above, which the closed list fixes.
OLD_BROAD_BRACKET_RE = re.compile(r"\[[A-Z][^\]]{2,}\]")


def _load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None, f"could not load spec for {path}"
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


scaffold_validate = _load_module("scaffold_validate_under_test_required_fill", SCAFFOLD_VALIDATE_SCRIPT)


# ---------------------------------------------------------------------------
# Layer (a) -- shipped-template assertion
# ---------------------------------------------------------------------------


def test_plan_template_ships_the_intake_proportionality_marker_unfilled():
    text = PLAN_TEMPLATE.read_text(encoding="utf-8")
    findings = scaffold_validate.scan_markers(PLAN_TEMPLATE, text)
    assert any(INTAKE_MARKER_ID in f for f in findings), (
        f"expected the shipped PLAN.template.md to still carry an unfilled "
        f"'{INTAKE_MARKER_ID}' marker span (findings: {findings})"
    )


def test_style_bible_template_ships_the_name_display_marker_unfilled():
    text = STYLE_BIBLE_TEMPLATE.read_text(encoding="utf-8")
    findings = scaffold_validate.scan_markers(STYLE_BIBLE_TEMPLATE, text)
    assert any(NAME_DISPLAY_MARKER_ID in f for f in findings), (
        f"expected the shipped style_bible.template.md to still carry an "
        f"unfilled '{NAME_DISPLAY_MARKER_ID}' marker span (findings: {findings})"
    )


# ---------------------------------------------------------------------------
# Layer (b) -- behavioral CLI run against a throwaway durable root
# ---------------------------------------------------------------------------


def _marker_block(marker_id: str, body: str) -> str:
    return (
        f"<!-- LT_REQUIRED_FILL_BEGIN: {marker_id} -->\n"
        f"{body}\n"
        f"<!-- LT_REQUIRED_FILL_END -->\n"
    )


def _trap_residue_block(pairing: str = "guéridon = refrain-song") -> str:
    """An ERA/DOMAIN TRAP EXAMPLE callout whose pairing has been separator-
    mangled away from the exact `guéridon=refrain-song` literal (spaces by
    default) -- bypasses scan_trap_string but not scan_era_domain_trap_residue."""
    return (
        "\n<!-- ERA/DOMAIN TRAP EXAMPLE -- replace before your first segment: "
        f"{pairing} -- in general modern French, \"guéridon\" is a small round "
        "pedestal table, but here it was period slang for a type of song -->\n"
    )


def _seed_durable_root(
    tmp_path: Path,
    *,
    intake_filled: bool,
    name_display_filled: bool,
    bracket_unfilled_files: tuple[str, ...] = (),
    trap_residue_files: tuple[str, ...] = (),
) -> Path:
    """Builds a throwaway durable root under tmp_path with the real
    scaffold_validate.py copied to <tmp>/scripts/ (self-anchoring means
    parents[1] of that copy resolves to tmp_path itself), plus every file
    the real script scans present as minimal, otherwise-clean content --
    a missing sibling is itself a fatal finding the real script reports,
    so every one of MARKER_SCAN_FILES/BRACKET_SCAN_FILES/TRAP_STRING_SCAN_FILES
    must exist even though only some carry the markers under test here.

    `bracket_unfilled_files` appends a still-unfilled `[SOURCE LANGUAGE]`
    placeholder to each named file; `trap_residue_files` appends a
    separator-mangled era/domain trap callout. Both default to none, so
    existing callers get a fully clean, all-filled root."""
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "scaffold_validate.py").write_bytes(SCAFFOLD_VALIDATE_SCRIPT.read_bytes())

    intake_body = (
        "LT_PLACEHOLDER_UNFILLED"
        if not intake_filled
        else "Fast tier chosen: offline research, lightest apparatus, index off; "
        "worth it because this is a plain translate+gloss job."
    )
    name_display_body = (
        "LT_PLACEHOLDER_UNFILLED"
        if not name_display_filled
        else "YES, original script only, no transliteration system needed."
    )

    # A well-formed STYLE_CONTRACT_BEGIN/END pair, unrelated to the
    # LT_REQUIRED_FILL marker under test here, so scaffold_validate.py's
    # independent STYLE_CONTRACT check (#129) always passes in this fixture
    # and the LT_REQUIRED_FILL assertions below stay isolated to their own
    # single finding.
    style_contract_block = "<!-- STYLE_CONTRACT_BEGIN -->\nSections A-F.\n<!-- STYLE_CONTRACT_END -->\n"

    contents = {
        "PLAN.md": "# Project plan\n\n" + _marker_block(INTAKE_MARKER_ID, intake_body),
        "style_bible.md": (
            "# Style bible\n\n"
            + style_contract_block
            + _marker_block(NAME_DISPLAY_MARKER_ID, name_display_body)
        ),
        # consistency_issues.md legitimately ships with zero marker spans (see
        # scaffold_validate.py's own docstring) -- a passing case, not a gap.
        "consistency_issues.md": "# Consistency issues\n\nNone logged yet.\n",
        "translate_TASK.md": "# Translate task\n\nNo era/domain trap example here.\n",
        "review_TASK.md": "# Review task\n\nNo era/domain trap example here.\n",
        # glossary_TASK.md is scanned by BRACKET_SCAN_FILES; a missing sibling
        # is itself a fatal finding, so it must be seeded bracket-free here.
        "glossary_TASK.md": "# Glossary task\n\nNo unfilled brackets here.\n",
    }

    for name in bracket_unfilled_files:
        contents[name] += "\nSource language: [SOURCE LANGUAGE] -- to be filled.\n"
    for name in trap_residue_files:
        contents[name] += _trap_residue_block()

    for name, text in contents.items():
        (tmp_path / name).write_text(text, encoding="utf-8")
    return tmp_path


def _run_scaffold_validate(durable_root: Path):
    return subprocess.run(
        [sys.executable, str(durable_root / "scripts" / "scaffold_validate.py")],
        cwd=durable_root,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_both_markers_unfilled_fails_naming_both_ids(tmp_path):
    durable_root = _seed_durable_root(tmp_path, intake_filled=False, name_display_filled=False)

    result = _run_scaffold_validate(durable_root)

    assert result.returncode != 0, (
        f"expected a non-zero exit with both markers still unfilled\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert INTAKE_MARKER_ID in result.stdout
    assert NAME_DISPLAY_MARKER_ID in result.stdout


def test_only_intake_unfilled_isolates_that_single_finding(tmp_path):
    durable_root = _seed_durable_root(tmp_path, intake_filled=False, name_display_filled=True)

    result = _run_scaffold_validate(durable_root)

    assert result.returncode != 0
    assert INTAKE_MARKER_ID in result.stdout
    assert NAME_DISPLAY_MARKER_ID not in result.stdout


def test_only_name_display_unfilled_isolates_that_single_finding(tmp_path):
    durable_root = _seed_durable_root(tmp_path, intake_filled=True, name_display_filled=False)

    result = _run_scaffold_validate(durable_root)

    assert result.returncode != 0
    assert NAME_DISPLAY_MARKER_ID in result.stdout
    assert INTAKE_MARKER_ID not in result.stdout


def test_both_markers_filled_passes_clean(tmp_path):
    durable_root = _seed_durable_root(tmp_path, intake_filled=True, name_display_filled=True)

    result = _run_scaffold_validate(durable_root)

    assert result.returncode == 0, (
        f"expected a clean exit once both markers are filled in\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )


# ---------------------------------------------------------------------------
# Layer (c) -- bracket-placeholder scan (#94), unit-level against real templates
# ---------------------------------------------------------------------------


def test_bracket_template_sources_matches_production_bracket_scan_files():
    """Drift-protection: this test module's own file inventory must equal
    scaffold_validate.py's real BRACKET_SCAN_FILES, or a future edit that
    shrinks the production list (removing a file from being scanned at all)
    would leave every per-template test below green -- they only ever
    iterate over THIS module's hardcoded dict, never the production list."""
    assert set(BRACKET_TEMPLATE_SOURCES) == set(scaffold_validate.BRACKET_SCAN_FILES), (
        f"test inventory {sorted(BRACKET_TEMPLATE_SOURCES)} has drifted from "
        f"production BRACKET_SCAN_FILES {sorted(scaffold_validate.BRACKET_SCAN_FILES)}"
    )


@pytest.mark.parametrize("md_name, template_name", sorted(BRACKET_TEMPLATE_SOURCES.items()))
def test_bracket_scan_catches_every_real_placeholder_per_template(md_name, template_name):
    text = (TEMPLATES_DIR / template_name).read_text(encoding="utf-8")
    findings = scaffold_validate.scan_bracket_placeholders(Path(md_name), text)
    expected = EXPECTED_TEMPLATE_PLACEHOLDERS[template_name]
    for placeholder in expected:
        assert any(placeholder in f for f in findings), (
            f"expected {template_name} to still carry unfilled placeholder "
            f"{placeholder!r} (findings: {findings})"
        )
    assert len(findings) == len(expected), (
        f"{template_name}: expected exactly {len(expected)} placeholder findings "
        f"({sorted(expected)}), got {findings}"
    )


def test_bracket_scan_catches_line_wrapped_glossary_placeholder():
    """The glossary placeholder wraps across a real newline in the shipped
    template, so a plain substring check misses it -- this is the exact case
    that motivates WHITESPACE_RE normalization. Asserting the raw text does
    NOT contain the flat string is load-bearing: if a future edit un-wraps
    it, this guard fails loudly rather than silently going vacuous."""
    wrapped = "[PROJECT TITLE / AUTHOR / PERIOD -- fill in]"
    text = (TEMPLATES_DIR / "glossary_TASK.template.md").read_text(encoding="utf-8")
    assert wrapped not in text, (
        "expected the glossary placeholder to still be line-wrapped in the "
        "shipped template -- this test's normalization proof depends on it"
    )
    findings = scaffold_validate.scan_bracket_placeholders(Path("glossary_TASK.md"), text)
    assert any(wrapped in f for f in findings), (
        "whitespace normalization must catch the line-wrapped glossary placeholder"
    )


def test_bracket_scan_ignores_doc_comment_and_json_array_examples():
    for text in [
        "Hand-adapt the bracketed [PLACEHOLDER] spots below for THIS project",
        "- `candidates[]` -- this batch's rows, deterministically extracted",
        '[ {"source_form": "<candidate\'s own name field>"} ]',
        "An empty list is written as [].",
    ]:
        assert scaffold_validate.scan_bracket_placeholders(Path("x.md"), text) == [], (
            f"non-placeholder brackets must not be flagged: {text!r}"
        )


@pytest.mark.parametrize("fixture", LEGITIMATE_HAND_ADAPTED_BRACKETS)
def test_bracket_scan_does_not_flag_legitimate_hand_adapted_brackets(fixture):
    """The actual fix for what the round-3 ExitPlanMode review rejected: a
    hand-adapter's own editorial brackets in a correctly filled-in file must
    never trip the gate."""
    assert scaffold_validate.scan_bracket_placeholders(Path("hand_adapted.md"), fixture) == []


def test_old_broad_bracket_regex_would_have_regressed_on_hand_adapted_brackets():
    """Red-before-green documentation. The rejected round-1/2 shape regex
    WOULD have wrongly flagged every legitimate editorial bracket above; the
    shipped closed-list design flags none. If this ever stops holding, the
    false-positive guard above has quietly lost its meaning."""
    regressed = [b for b in LEGITIMATE_HAND_ADAPTED_BRACKETS if OLD_BROAD_BRACKET_RE.search(b)]
    assert regressed == LEGITIMATE_HAND_ADAPTED_BRACKETS, (
        f"expected the old broad regex to flag ALL legitimate brackets, got {regressed}"
    )
    for b in LEGITIMATE_HAND_ADAPTED_BRACKETS:
        assert scaffold_validate.scan_bracket_placeholders(Path("x.md"), b) == []


# ---------------------------------------------------------------------------
# Layer (c) -- bracket-placeholder scan, behavioral CLI runs
# ---------------------------------------------------------------------------


def test_unfilled_bracket_in_one_file_fails_naming_only_that_file(tmp_path):
    durable_root = _seed_durable_root(
        tmp_path,
        intake_filled=True,
        name_display_filled=True,
        bracket_unfilled_files=("translate_TASK.md",),
    )

    result = _run_scaffold_validate(durable_root)

    assert result.returncode != 0, (
        f"expected a non-zero exit with an unfilled bracket placeholder\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "translate_TASK.md" in result.stdout
    assert "[SOURCE LANGUAGE]" in result.stdout
    # No other file tripped the bracket check.
    assert "glossary_TASK.md" not in result.stdout
    assert "PLAN.md" not in result.stdout


def test_all_six_files_bracket_free_passes_clean(tmp_path):
    durable_root = _seed_durable_root(tmp_path, intake_filled=True, name_display_filled=True)

    result = _run_scaffold_validate(durable_root)

    assert result.returncode == 0, (
        f"expected a clean exit with all six files bracket-free\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )


def test_mixed_bracket_findings_isolate_per_file(tmp_path):
    durable_root = _seed_durable_root(
        tmp_path,
        intake_filled=True,
        name_display_filled=True,
        bracket_unfilled_files=("translate_TASK.md", "glossary_TASK.md"),
    )

    result = _run_scaffold_validate(durable_root)

    assert result.returncode != 0
    assert "translate_TASK.md" in result.stdout
    assert "glossary_TASK.md" in result.stdout
    assert "PLAN.md" not in result.stdout


# ---------------------------------------------------------------------------
# Layer (d) -- era/domain trap residue hardening (#94)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("pairing", ["guéridon = refrain-song", "guéridon -> refrain-song"])
def test_separator_mangled_trap_bypasses_exact_but_caught_by_residue(pairing):
    text = "# Translate task\n\n" + _trap_residue_block(pairing)
    assert scaffold_validate.scan_trap_string(Path("translate_TASK.md"), text) == [], (
        "the exact-substring check is expected to MISS a separator-mangled trap"
    )
    assert scaffold_validate.scan_era_domain_trap_residue(Path("translate_TASK.md"), text), (
        "the co-occurrence check must catch a separator-mangled trap"
    )


def test_deleted_trap_line_but_surrounding_sentence_survives_caught_by_residue():
    # The exact `guéridon=refrain-song` pairing was deleted, but the callout's
    # explanatory sentence still names 'guéridon' inside the labeled block.
    text = (
        "# Translate task\n\n"
        "<!-- ERA/DOMAIN TRAP EXAMPLE -- replace before your first segment: "
        'in general modern French, "guéridon" is a small round pedestal table, '
        "but here it was period slang for a type of song -->\n"
    )
    assert scaffold_validate.scan_trap_string(Path("translate_TASK.md"), text) == []
    assert scaffold_validate.scan_era_domain_trap_residue(Path("translate_TASK.md"), text)


def test_fully_replaced_callout_with_own_trap_passes_clean():
    # Label kept, but the example replaced with this project's own trap word
    # (no 'guéridon') -- a correctly hand-adapted file must pass.
    text = (
        "# Translate task\n\n"
        "<!-- ERA/DOMAIN TRAP EXAMPLE -- replace before your first segment: "
        "Kutsche=stagecoach -- in this project's 19th-century German domain the "
        "word is a specific horse-drawn carriage, not a generic car -->\n"
    )
    assert scaffold_validate.scan_trap_string(Path("translate_TASK.md"), text) == []
    assert scaffold_validate.scan_era_domain_trap_residue(Path("translate_TASK.md"), text) == []


def test_fully_removed_callout_passes_clean():
    text = "# Translate task\n\nThis project has no era/domain trap yet.\n"
    assert scaffold_validate.scan_trap_string(Path("translate_TASK.md"), text) == []
    assert scaffold_validate.scan_era_domain_trap_residue(Path("translate_TASK.md"), text) == []


def test_correctly_replaced_callout_not_rejected_by_unrelated_later_guéridon_mention():
    """The residue check must be scoped to the callout's own HTML comment
    span, not the whole file -- 'guéridon' is an ordinary French word
    (a pedestal table) a real project's own hand-adapted content can
    legitimately mention elsewhere. A file-wide check would wrongly reject
    a correctly replaced callout forever, the exact over-broad-gate failure
    class this plugin already got bitten by once (see
    gotcha-overbroad-freetext-gate-regex in project memory)."""
    text = (
        "# Translate task\n\n"
        "<!-- ERA/DOMAIN TRAP EXAMPLE -- replace before your first segment: "
        "Kutsche=stagecoach -- in this project's 19th-century German domain the "
        "word is a specific horse-drawn carriage, not a generic car -->\n\n"
        "Translator's note: the source describes a guéridon in the drawing-room "
        "scene; render it as a small round side table, not a generic 'table'.\n"
    )
    assert scaffold_validate.scan_trap_string(Path("translate_TASK.md"), text) == []
    assert scaffold_validate.scan_era_domain_trap_residue(Path("translate_TASK.md"), text) == []


def test_recased_callout_label_still_caught_by_residue():
    text = (
        "# Translate task\n\n"
        "<!-- era/domain trap example -- replace before your first segment: "
        "guéridon = refrain-song -- in general modern French, \"guéridon\" is a "
        "small round pedestal table, but here it was period slang for a type "
        "of song -->\n"
    )
    assert scaffold_validate.scan_era_domain_trap_residue(Path("translate_TASK.md"), text)


def test_nfd_encoded_guéridon_still_caught_by_residue():
    nfd_block = unicodedata.normalize("NFD", _trap_residue_block())
    text = "# Translate task\n\n" + nfd_block
    assert scaffold_validate.scan_era_domain_trap_residue(Path("translate_TASK.md"), text)


def test_era_domain_residue_fails_at_cli(tmp_path):
    durable_root = _seed_durable_root(
        tmp_path,
        intake_filled=True,
        name_display_filled=True,
        trap_residue_files=("translate_TASK.md",),
    )

    result = _run_scaffold_validate(durable_root)

    assert result.returncode != 0, (
        f"expected a non-zero exit with a separator-mangled trap residue\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "translate_TASK.md" in result.stdout
    assert "guéridon" in result.stdout


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
