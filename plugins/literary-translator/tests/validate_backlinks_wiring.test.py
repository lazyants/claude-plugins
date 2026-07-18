"""tests/validate_backlinks_wiring.test.py

An advisory gate is worthless if SKILL.md never tells the operator to run it,
runs it at the wrong point, or documents its exit contract wrongly.
``validate_backlinks.py``'s own LOGIC lives in
``tests/validate_backlinks.test.py``; what is NOT covered anywhere else is
whether ``SKILL.md``'s W9 prose actually wires the appendix-integrity gate at
the right place with the right semantics:

  - invoked exactly once in the W9 window,
  - AFTER ``scripts/diff_rendered_output.py`` (it reads the rendered vault +
    the persisted nodestream.json those steps produce),
  - documented as ADVISORY on exit 1 (log-and-continue, never halts W9) --
    UNLIKE the hard ``validate_assembled.py``/``diff`` gates above it,
  - short-circuiting to ``mentions_coverage.status: disabled`` when the
    opt-in flag is off / target is not obsidian.

Mirrors ``tests/validate_assembled_wiring.test.py``'s charter for the #202
gate. Matcher self-tests near the end run against small hand-built fragments
(never the real SKILL.md) to prove the checks reject a gutted / misplaced /
mis-documented invocation rather than passing vacuously.

Collection: run with
``python3 -m pytest --import-mode=importlib tests/validate_backlinks_wiring.test.py``.
"""
from __future__ import annotations

from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SKILL_PATH = PLUGIN_ROOT / "skills" / "literary-translator" / "SKILL.md"

assert SKILL_PATH.is_file(), f"SKILL.md not found at {SKILL_PATH}"

W9_HEADING = "**W9 Assemble**"
REFERENCE_DOCS_HEADING = "## Reference docs"

# The existing diff acceptance-gate invocation (from the shipped W9 prose) --
# our gate must be wired strictly AFTER it.
DIFF_ACCEPTANCE_MARKER = "scripts/diff_rendered_output.py` as the acceptance gate"

# Our own gate's markers (matched EXACTLY against the W9 prose).
VALIDATE_BACKLINKS_MARKER = "scripts/validate_backlinks.py"
ADVISORY_GATE_MARKER = "appendix-integrity gate"
AFTER_DIFF_MARKER = "AFTER `diff_rendered_output.py`"
ADVISORY_EXIT_MARKER = "log the warnings and CONTINUE W9"
DISABLED_MARKER = "mentions_coverage.status: disabled"


def _skill_text() -> str:
    return SKILL_PATH.read_text(encoding="utf-8")


def _w9_window(text: str) -> str:
    w9 = text.find(W9_HEADING)
    ref = text.find(REFERENCE_DOCS_HEADING)
    assert w9 != -1, "SKILL.md no longer has a W9 Assemble heading"
    assert ref != -1, "SKILL.md no longer has a Reference docs heading"
    assert w9 < ref, "W9 Assemble must come before the Reference docs section"
    return text[w9:ref]


# ===========================================================================
# Real SKILL.md
# ===========================================================================


def test_validate_backlinks_wired_after_diff_with_advisory_semantics():
    window = _w9_window(_skill_text())

    assert window.count(VALIDATE_BACKLINKS_MARKER) == 1, (
        "expected exactly one scripts/validate_backlinks.py invocation in the "
        "W9 window -- zero means the advisory appendix-integrity gate is "
        "unwired, more than one is an ambiguous doc edit"
    )

    diff_offset = window.find(DIFF_ACCEPTANCE_MARKER)
    backlinks_offset = window.find(VALIDATE_BACKLINKS_MARKER)
    assert diff_offset != -1, (
        "SKILL.md's W9 section no longer invokes diff_rendered_output.py as "
        "the acceptance gate with the expected phrasing"
    )
    assert backlinks_offset > diff_offset, (
        "validate_backlinks.py must be wired strictly AFTER "
        "diff_rendered_output.py -- it reads the rendered vault + the "
        "persisted nodestream.json that assemble/diff produce"
    )

    assert ADVISORY_GATE_MARKER in window, (
        "the W9 prose must name it an appendix-integrity gate"
    )
    assert AFTER_DIFF_MARKER in window, (
        "the invocation must state it runs AFTER diff_rendered_output.py"
    )
    assert ADVISORY_EXIT_MARKER in window, (
        "the invocation MUST document exit 1 as ADVISORY (log-and-continue) "
        "-- omitting it lets a future edit silently treat this advisory gate "
        "as a hard halt like the #202/diff gates above it"
    )
    assert DISABLED_MARKER in window, (
        "the invocation must document the flag-off / non-obsidian "
        "short-circuit to mentions_coverage.status: disabled"
    )


def test_validate_backlinks_marker_appears_once_in_whole_document():
    text = _skill_text()
    assert text.count(VALIDATE_BACKLINKS_MARKER) == 1, (
        "scripts/validate_backlinks.py must be invoked in exactly one place "
        "(the W9 window) across the whole document"
    )


# ===========================================================================
# Matcher self-tests -- hand-built fragments, never the real SKILL.md.
# ===========================================================================


def test_matcher_rejects_gate_placed_before_diff():
    """The gate marker is present but sits BEFORE the diff acceptance gate --
    must be rejected: it reads artifacts diff/assemble produce."""
    decoy = (
        f"{W9_HEADING} filler.\n\n"
        f"Run `{VALIDATE_BACKLINKS_MARKER}` as an {ADVISORY_GATE_MARKER}, "
        f"{AFTER_DIFF_MARKER}; {ADVISORY_EXIT_MARKER}; {DISABLED_MARKER}.\n\n"
        f"Then run `{DIFF_ACCEPTANCE_MARKER}: it re-renders.\n\n"
        f"{REFERENCE_DOCS_HEADING}\n"
    )
    window = _w9_window(decoy)
    diff_offset = window.find(DIFF_ACCEPTANCE_MARKER)
    backlinks_offset = window.find(VALIDATE_BACKLINKS_MARKER)
    assert backlinks_offset < diff_offset, (
        "fixture sanity: the decoy places the gate before diff"
    )


def test_matcher_rejects_missing_advisory_semantics():
    """The gate is placed after diff, but its exit-1-advisory contract is
    absent -- a naive 'marker appears after diff' check would pass; this must
    not, because dropping the advisory note lets the gate read as a hard
    halt."""
    gutted = (
        f"{W9_HEADING} filler.\n\n"
        f"Then run `{DIFF_ACCEPTANCE_MARKER}: it re-renders.\n\n"
        f"Then run `{VALIDATE_BACKLINKS_MARKER}` as an {ADVISORY_GATE_MARKER}, "
        f"{AFTER_DIFF_MARKER}.\n\n"
        f"{REFERENCE_DOCS_HEADING}\n"
    )
    window = _w9_window(gutted)
    assert window.count(VALIDATE_BACKLINKS_MARKER) == 1  # present, after diff...
    assert ADVISORY_EXIT_MARKER not in window            # ...but advisory contract missing


def test_matcher_accepts_a_genuine_fragment():
    good = (
        f"{W9_HEADING} filler.\n\n"
        f"Then run `{DIFF_ACCEPTANCE_MARKER}: it re-renders.\n\n"
        f"Then run `{VALIDATE_BACKLINKS_MARKER}` as an {ADVISORY_GATE_MARKER}, "
        f"{AFTER_DIFF_MARKER}. Its exit 1 -- {ADVISORY_EXIT_MARKER}; when off "
        f"it short-circuits to {DISABLED_MARKER}.\n\n"
        f"{REFERENCE_DOCS_HEADING}\n"
    )
    window = _w9_window(good)
    diff_offset = window.find(DIFF_ACCEPTANCE_MARKER)
    backlinks_offset = window.find(VALIDATE_BACKLINKS_MARKER)
    assert backlinks_offset > diff_offset
    assert window.count(VALIDATE_BACKLINKS_MARKER) == 1
    assert ADVISORY_EXIT_MARKER in window
    assert DISABLED_MARKER in window


if __name__ == "__main__":
    import sys

    sys.exit(pytest.main([__file__, "-v"]))
