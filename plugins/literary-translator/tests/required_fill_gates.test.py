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
   `TRAP_STRING_SCAN_FILES` -- a missing sibling is itself a fatal finding,
   so all must exist) with minimal, otherwise-clean content. Drives the
   real script as a subprocess end to end: unfilled -> non-zero exit
   naming the still-unfilled marker id(s); filled -> exit 0.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

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


def _seed_durable_root(tmp_path: Path, *, intake_filled: bool, name_display_filled: bool) -> Path:
    """Builds a throwaway durable root under tmp_path with the real
    scaffold_validate.py copied to <tmp>/scripts/ (self-anchoring means
    parents[1] of that copy resolves to tmp_path itself), plus every file
    the real script scans present as minimal, otherwise-clean content --
    a missing sibling is itself a fatal finding the real script reports,
    so every one of MARKER_SCAN_FILES/TRAP_STRING_SCAN_FILES must exist
    even though only two of them carry the markers under test here."""
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

    (tmp_path / "PLAN.md").write_text(
        "# Project plan\n\n" + _marker_block(INTAKE_MARKER_ID, intake_body),
        encoding="utf-8",
    )
    (tmp_path / "style_bible.md").write_text(
        "# Style bible\n\n" + _marker_block(NAME_DISPLAY_MARKER_ID, name_display_body),
        encoding="utf-8",
    )
    # consistency_issues.md legitimately ships with zero marker spans (see
    # scaffold_validate.py's own docstring) -- a passing case, not a gap.
    (tmp_path / "consistency_issues.md").write_text(
        "# Consistency issues\n\nNone logged yet.\n", encoding="utf-8"
    )
    (tmp_path / "translate_TASK.md").write_text(
        "# Translate task\n\nNo era/domain trap example here.\n", encoding="utf-8"
    )
    (tmp_path / "review_TASK.md").write_text(
        "# Review task\n\nNo era/domain trap example here.\n", encoding="utf-8"
    )
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


if __name__ == "__main__":
    import pytest

    sys.exit(pytest.main([__file__, "-v"]))
