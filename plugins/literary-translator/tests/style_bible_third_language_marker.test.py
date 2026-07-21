"""tests/style_bible_third_language_marker.test.py -- #203: without a
required-fill slot, a project could scaffold and pass W1 with the
"embedded third-language text" convention (romanize vs. translate, gloss
format, whether to bracket the original) never actually decided. Section E
of `style_bible.template.md` described the concept but left no fill span at
all, so `scaffold_validate.py`'s marker scan had nothing to check -- a
project physically could start with the convention undefined.

Two layers, same recipe as `tests/required_fill_gates.test.py`:

1. Shipped-template assertion -- reads the REAL, as-shipped
   `style_bible.template.md` and asserts it still carries the
   `embedded-third-language-convention` marker id with the unfilled
   sentinel `LT_PLACEHOLDER_UNFILLED` inside that span, reusing
   `scaffold_validate.py`'s own real `scan_markers()` rather than
   hand-rolling a second marker-span parser.

2. Behavioral CLI run -- copies the REAL `scaffold_validate.py` into a
   throwaway durable root (it self-anchors `DURABLE_ROOT` to
   `Path(__file__).resolve().parents[1]`) and drives it end to end: left
   unfilled -> non-zero exit naming this marker id; filled in -> exit 0.

Also asserts the new span sits inside the `STYLE_CONTRACT_BEGIN`/`_END`
pair (section E already does) -- load-bearing because that span is what
`cache_key.py`'s `compute_style_contract_hash` hashes; a slot placed
outside it would silently never invalidate `style_contract_hash` when
filled in.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
ASSETS_ROOT = PLUGIN_ROOT / "skills" / "literary-translator" / "assets"
TEMPLATES_DIR = ASSETS_ROOT / "templates"
SCAFFOLD_VALIDATE_SCRIPT = ASSETS_ROOT / "scripts" / "scaffold_validate.py"
STYLE_BIBLE_TEMPLATE = TEMPLATES_DIR / "style_bible.template.md"

assert SCAFFOLD_VALIDATE_SCRIPT.is_file(), f"expected {SCAFFOLD_VALIDATE_SCRIPT} to exist"
assert STYLE_BIBLE_TEMPLATE.is_file(), f"expected {STYLE_BIBLE_TEMPLATE} to exist"

MARKER_ID = "embedded-third-language-convention"


def _load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None, f"could not load spec for {path}"
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


scaffold_validate = _load_module("scaffold_validate_under_test_third_language_marker", SCAFFOLD_VALIDATE_SCRIPT)


# ---------------------------------------------------------------------------
# Layer (a) -- shipped-template assertion
# ---------------------------------------------------------------------------


def test_style_bible_template_ships_the_embedded_third_language_marker_unfilled():
    text = STYLE_BIBLE_TEMPLATE.read_text(encoding="utf-8")
    findings = scaffold_validate.scan_markers(STYLE_BIBLE_TEMPLATE, text)
    assert any(MARKER_ID in f for f in findings), (
        f"expected the shipped style_bible.template.md to still carry an "
        f"unfilled '{MARKER_ID}' marker span (findings: {findings})"
    )


def test_marker_sits_inside_the_style_contract_span():
    """A slot outside STYLE_CONTRACT_BEGIN/END would silently never move
    `style_contract_hash` when filled in -- section E already lives inside
    the span, but this pins it so a future reflow can't drift the marker
    out without a test failing."""
    text = STYLE_BIBLE_TEMPLATE.read_text(encoding="utf-8")
    begin_marker = text.index("<!-- LT_REQUIRED_FILL_BEGIN: " + MARKER_ID + " -->")
    contract_begin = text.index("<!-- STYLE_CONTRACT_BEGIN -->")
    contract_end = text.index("<!-- STYLE_CONTRACT_END -->")
    assert contract_begin < begin_marker < contract_end, (
        f"expected the '{MARKER_ID}' marker to sit strictly between "
        f"STYLE_CONTRACT_BEGIN ({contract_begin}) and STYLE_CONTRACT_END "
        f"({contract_end}), found it at {begin_marker}"
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


def _seed_durable_root(tmp_path: Path, *, filled: bool) -> Path:
    """Minimal durable root seeding every sibling scaffold_validate.py scans
    (a missing one is itself a fatal finding), isolating this test to a
    single marker's fill state."""
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "scaffold_validate.py").write_bytes(SCAFFOLD_VALIDATE_SCRIPT.read_bytes())

    body = (
        "Romanize (not translate); gloss inline in parentheses immediately after "
        "first mention; original set off in italics."
        if filled
        else "LT_PLACEHOLDER_UNFILLED"
    )
    style_contract_block = (
        "<!-- STYLE_CONTRACT_BEGIN -->\nSections A-F.\n" + _marker_block(MARKER_ID, body) + "<!-- STYLE_CONTRACT_END -->\n"
    )

    contents = {
        "PLAN.md": "# Project plan\n\nNo markers here.\n",
        "style_bible.md": "# Style bible\n\n" + style_contract_block,
        "consistency_issues.md": "# Consistency issues\n\nNone logged yet.\n",
        "translate_TASK.md": "# Translate task\n\nNo era/domain trap example here.\n",
        "review_TASK.md": "# Review task\n\nNo era/domain trap example here.\n",
        "glossary_TASK.md": "# Glossary task\n\nNo unfilled brackets here.\n",
    }
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


def test_unfilled_marker_fails_validation(tmp_path):
    durable_root = _seed_durable_root(tmp_path, filled=False)

    result = _run_scaffold_validate(durable_root)

    assert result.returncode != 0, (
        f"expected a non-zero exit with '{MARKER_ID}' still unfilled\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert MARKER_ID in result.stdout


def test_filled_marker_passes_clean(tmp_path):
    durable_root = _seed_durable_root(tmp_path, filled=True)

    result = _run_scaffold_validate(durable_root)

    assert result.returncode == 0, (
        f"expected a clean exit once '{MARKER_ID}' is filled in\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
