"""tests/skill_prose_present.test.py -- presence test for hand-authored
plugin prose that no schema or script enforces: an accidental deletion
during a later edit would otherwise go unnoticed until a human happened to
reread the file.

Split by file, and every needle below is a fragment verified (by line
number, see the inline comments) to sit fully within one physical source
line at authoring time -- this codebase's docs hard-wrap at a fixed column
width, so a needle spanning a wrap point would silently miss even though
the content is fully intact. Never extend a passing needle into a longer
phrase without re-checking it still lands on one line.

Covers:
1. SKILL.md's "Intake & proportionality" step existing at all.
2. SKILL.md's pipeline-role-assignment prompt within that same step
   (LESSONS item 18: who translates/reviews/fixes/orchestrates, reviewer
   independence, pointing at the constellation doc).
3. SKILL.md's F3 adjudication-fence sentence (gating the opt-in
   canon-adjudication-audit machinery to its justifying deliverable).
4. references/operating-constellation.md existing and actually carrying
   the Part-6 review-orchestration content (LESSONS items 20-24), not just
   an empty stub.
"""
from __future__ import annotations

from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SKILL_MD = PLUGIN_ROOT / "skills" / "literary-translator" / "SKILL.md"
OPERATING_CONSTELLATION = (
    PLUGIN_ROOT / "skills" / "literary-translator" / "references" / "operating-constellation.md"
)

assert SKILL_MD.is_file(), f"expected {SKILL_MD} to exist"


def _skill_text() -> str:
    return SKILL_MD.read_text(encoding="utf-8")


def test_intake_and_proportionality_step_present():
    # Heading fragment, fully on one line as of this writing:
    # "## Intake & proportionality (do this first)".
    assert "Intake & proportionality" in _skill_text()


def test_pipeline_role_assignment_prompt_present():
    # "Agree pipeline role assignment." sits fully on its own line as of
    # this writing. Deliberately NOT extended to "...independent of the
    # translator" -- that phrase itself wraps across two lines here, the
    # exact hard-wrap trap this file's docstring warns about.
    text = _skill_text()
    assert "Agree pipeline role assignment" in text
    # The prompt must actually point at the constellation doc, not merely
    # use the word "independent" in passing elsewhere in the file.
    assert "operating-constellation.md" in text


def test_f3_adjudication_fence_sentence_present():
    # Fully on one line as of this writing: "identity itself. Enable ONLY
    # when a per-person index, per-person bios, or".
    assert "Enable ONLY when a per-person index" in _skill_text()


def test_operating_constellation_reference_exists_and_has_review_orchestration_content():
    assert OPERATING_CONSTELLATION.is_file(), f"expected {OPERATING_CONSTELLATION} to exist"
    text = OPERATING_CONSTELLATION.read_text(encoding="utf-8")
    # "Independent reviewer" sits fully on one line as of this writing
    # ("- **Independent reviewer, always.** Whatever checks a piece of
    # work must run"); matched case-insensitively since it's a bold
    # heading phrase, not a fixed-case identifier.
    assert "independent reviewer" in text.lower()


if __name__ == "__main__":
    import sys

    import pytest

    sys.exit(pytest.main([__file__, "-v"]))
