"""tests/seed_template_presence.test.py

Regression-catcher for the peer-review finding that the literary-translator
plugin's own SKILL.md documents a Step 0a one-time-seed copy pass for
`PLAN.template.md`, `style_bible.template.md`, `consistency_issues.template.md`,
and `glossary_TASK.template.md` (see SKILL.md's Step 0a, ~lines 168-176) --
but none of those four files actually shipped in `assets/templates/`, so a
fresh project's scaffold step would FATAL the moment it tried to copy a
file that doesn't exist. This test locks the fix two ways so the same
class of defect can never ship silently again:

1. Parses SKILL.md itself for every `*.template.md` / `*.template.js` /
   `*.template` filename it names, and asserts each one actually exists in
   `assets/templates/` -- a generic, self-updating regression-catcher: any
   FUTURE template SKILL.md starts documenting is covered automatically,
   not just the four this test was written to fix.
2. Explicitly names the four templates this fix adds, so a future edit
   that silently deletes one of them (without SKILL.md itself losing the
   reference) still fails loudly.

Also asserts `glossary_TASK.template.md` carries a well-formed, current
`PROMPT_CONTRACT_VERSION` marker -- reusing `profile_validate.py`'s own
`check_contract_marker()` rather than hand-rolling a second parser, so this
test and the real Step 12 gate can never quietly disagree about what
"well-formed" means.
"""
from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SKILL_MD = PLUGIN_ROOT / "skills" / "literary-translator" / "SKILL.md"
TEMPLATES_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets" / "templates"
PROFILE_VALIDATE_PATH = (
    PLUGIN_ROOT / "skills" / "literary-translator" / "assets" / "scripts" / "profile_validate.py"
)

# Matches both `<name>.template.<ext>` (e.g. `PLAN.template.md`,
# `mass-translate-wf.template.js`) and the bare `<name>.template` shape
# `extract.py.template` uses (no further extension after `.template`).
TEMPLATE_FILENAME_RE = re.compile(r"\b[A-Za-z0-9_.-]*\.template(?:\.[A-Za-z0-9]+)?\b")

# The four templates this fix adds -- named explicitly so a future silent
# deletion fails even if SKILL.md's own reference to it also disappeared in
# the same change (the SKILL.md-driven scan above wouldn't catch that).
NEWLY_ADDED_TEMPLATES = (
    "PLAN.template.md",
    "style_bible.template.md",
    "consistency_issues.template.md",
    "glossary_TASK.template.md",
)


def _load_profile_validate():
    """Imports profile_validate.py fresh from its real, shipped install
    path -- ``assets/scripts/`` is not a package on sys.path, so a plain
    ``import`` won't reach it (same recipe as
    tests/prompt_contract_drift.test.py's own ``pv`` fixture)."""
    spec = importlib.util.spec_from_file_location(
        "profile_validate_under_test_seed_presence", PROFILE_VALIDATE_PATH
    )
    assert spec is not None and spec.loader is not None, (
        f"could not load spec for {PROFILE_VALIDATE_PATH}"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _template_filenames_referenced_in_skill_md() -> set[str]:
    assert SKILL_MD.is_file(), f"expected {SKILL_MD} to exist"
    text = SKILL_MD.read_text(encoding="utf-8")
    return set(TEMPLATE_FILENAME_RE.findall(text))


def test_skill_md_actually_references_the_expected_templates():
    """Sanity check on the scan itself: SKILL.md must still name every
    template this test was written to protect, and at least the two
    pre-existing sibling task templates -- guards against the regex
    silently matching nothing, which would make the presence check below
    pass vacuously."""
    referenced = _template_filenames_referenced_in_skill_md()
    for name in NEWLY_ADDED_TEMPLATES:
        assert name in referenced, (
            f"expected SKILL.md to still reference {name!r} -- if this "
            f"assertion fails because SKILL.md's own wording changed, "
            f"update this list to match, don't just delete the assertion"
        )
    assert "translate_TASK.template.md" in referenced
    assert "review_TASK.template.md" in referenced


def test_every_template_skill_md_references_exists_on_disk():
    """The actual regression-catcher: every `*.template.*` filename SKILL.md
    names must exist under assets/templates/ -- this is exactly the defect
    class the peer review found (SKILL.md documents a Step 0a copy of a
    file that was never shipped)."""
    referenced = _template_filenames_referenced_in_skill_md()
    assert referenced, "expected the SKILL.md template-filename scan to find at least one match"

    missing = sorted(name for name in referenced if not (TEMPLATES_DIR / name).is_file())
    assert missing == [], (
        f"SKILL.md references template file(s) that don't exist in "
        f"{TEMPLATES_DIR}: {missing}"
    )


@pytest.mark.parametrize("filename", NEWLY_ADDED_TEMPLATES)
def test_newly_added_template_exists(filename):
    path = TEMPLATES_DIR / filename
    assert path.is_file(), f"expected {path} to exist"
    assert path.stat().st_size > 0, f"expected {path} to be non-empty"


def test_glossary_task_template_starts_with_prompt_contract_version_marker():
    """glossary_TASK.template.md must carry a well-formed, leading, CURRENT
    PROMPT_CONTRACT_VERSION marker -- reuses profile_validate.py's own
    check_contract_marker() (the real Step 12 gate) against the shipped
    template directly, so this test and that gate can never quietly
    disagree about what "well-formed" means."""
    path = TEMPLATES_DIR / "glossary_TASK.template.md"
    text = path.read_text(encoding="utf-8")
    assert text.lstrip().startswith("<!-- PROMPT_CONTRACT_VERSION:"), (
        f"expected {path} to start with a leading PROMPT_CONTRACT_VERSION marker"
    )

    pv = _load_profile_validate()
    findings = pv.check_contract_marker(
        path,
        "PROMPT_CONTRACT_VERSION",
        pv.PROMPT_CONTRACT_MARKER_RE,
        pv.CURRENT_PROMPT_CONTRACT_VERSION,
    )
    assert findings == [], (
        f"expected the shipped template's own marker to already match the "
        f"current contract version; got findings: {findings}"
    )
