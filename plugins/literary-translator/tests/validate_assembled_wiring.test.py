"""tests/validate_assembled_wiring.test.py

#202: a mandatory gate is worthless if it's never actually invoked. This is
a doc-structural test, not a behavioral one -- ``validate_assembled.py``'s
own LOGIC is ``tests/validate_assembled.test.py``'s job; what is NOT covered
anywhere else is whether ``SKILL.md`` actually tells an operator to run it
at the two right points in the pipeline (default scope at W7/W8, AFTER
``final_audit.py`` succeeds and BEFORE W8 Deliver; ``assembled_book`` scope
at W9, AFTER ``assemble.py`` writes ``out/.assembled/nodestream.json`` and
BEFORE ``scripts/diff_rendered_output.py``) -- the standalone gate's own
test suite could stay fully green while the gate itself is never run in
either scope (mirrors ``tests/mandatory_split_audit_wiring.test.py``'s own
charter for a different gate).

Marker strings below are matched EXACTLY against the SKILL.md prose teammate
D pastes for #202's wiring -- see the #202 build's own hand-off note for the
two bullets these markers are drawn from. A handful of "matcher" tests near
the end exercise the window-extraction helpers against small, self-contained
hand-built fragments (never the real SKILL.md) to prove the matchers
themselves reject a gutted/misplaced invocation rather than passing
vacuously on "the marker appears SOMEWHERE in the file" -- the same rigor
concern ``mandatory_split_audit_wiring.test.py``'s own ``_mandatory_command_
block`` docstring calls out.

Collection note: like every ``*.test.py`` file in this suite, pytest's
default "prepend" import mode cannot resolve this dotted module name --
run with
``python3 -m pytest --import-mode=importlib tests/validate_assembled_wiring.test.py``.
"""
from __future__ import annotations

from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SKILL_PATH = PLUGIN_ROOT / "skills" / "literary-translator" / "SKILL.md"

assert SKILL_PATH.is_file(), f"SKILL.md not found at {SKILL_PATH}"

W7_HEADING = "**W7 Final audit**"
W8_HEADING = "**W8 Deliver**"
W9_HEADING = "**W9 Assemble**"
REFERENCE_DOCS_HEADING = "## Reference docs"

VALIDATE_ASSEMBLED_MARKER = "scripts/validate_assembled.py"

# -- default (segment_drafts_and_audit) scope, W7/W8 --
DEFAULT_SCOPE_GATE_MARKER = "Structural-completeness gate (`scripts/validate_assembled.py`, #202)"
DEFAULT_SCOPE_AFTER_MARKER = "AFTER `final_audit.py` succeeds"
DEFAULT_SCOPE_REVIEWED_SHA_MARKER = "reviewed_draft_sha1"

# -- assembled_book scope, W9 --
ASSEMBLE_INVOKE_MARKER = "Run `scripts/assemble.py`, which reconstructs the whole-book reading order"
DIFF_RENDERED_MARKER = "scripts/diff_rendered_output.py` as the acceptance gate"
ASSEMBLED_SCOPE_NODESTREAM_MARKER = "out/.assembled/nodestream.json"
ASSEMBLED_SCOPE_HEADING_KIND_MARKER = 'kind:"heading"'


def _skill_text() -> str:
    return SKILL_PATH.read_text(encoding="utf-8")


def _default_scope_window(text: str) -> str:
    w7 = text.find(W7_HEADING)
    w8 = text.find(W8_HEADING)
    assert w7 != -1, "SKILL.md no longer has a W7 Final audit heading"
    assert w8 != -1, "SKILL.md no longer has a W8 Deliver heading"
    assert w7 < w8, "W7 Final audit must come before W8 Deliver"
    return text[w7:w8]


def _assembled_scope_window(text: str) -> str:
    w9 = text.find(W9_HEADING)
    ref = text.find(REFERENCE_DOCS_HEADING)
    assert w9 != -1, "SKILL.md no longer has a W9 Assemble heading"
    assert ref != -1, "SKILL.md no longer has a Reference docs heading"
    assert w9 < ref, "W9 Assemble must come before the Reference docs section"
    return text[w9:ref]


# ===========================================================================
# Real SKILL.md -- default scope (W7/W8)
# ===========================================================================


def test_default_scope_gate_wired_between_w7_and_w8():
    window = _default_scope_window(_skill_text())
    assert window.count(VALIDATE_ASSEMBLED_MARKER) == 1, (
        "expected exactly one scripts/validate_assembled.py mention between "
        "W7 Final audit and W8 Deliver -- zero means the default-scope gate "
        "is unwired, more than one is an ambiguous doc edit"
    )
    assert DEFAULT_SCOPE_GATE_MARKER in window, (
        "SKILL.md's W7/W8 section must name the #202 structural-completeness "
        "gate by its own invocation marker"
    )
    assert DEFAULT_SCOPE_AFTER_MARKER in window, (
        "the default-scope invocation must say it runs AFTER final_audit.py "
        "succeeds -- not merely mention final_audit.py in passing"
    )
    assert DEFAULT_SCOPE_REVIEWED_SHA_MARKER in window, (
        "the default-scope invocation must describe the reviewed_draft_sha1 "
        "rebind (codex R2 MINOR-2) -- omitting it silently drops the "
        "hand-edit-after-review protection from the documented contract"
    )


def test_default_scope_gate_marker_does_not_leak_outside_its_window():
    text = _skill_text()
    w7 = text.find(W7_HEADING)
    assert w7 != -1
    assert DEFAULT_SCOPE_GATE_MARKER not in text[:w7], (
        "the default-scope gate bullet must not appear before its own W7 "
        "heading"
    )
    assert text.count(DEFAULT_SCOPE_GATE_MARKER) == 1, (
        "the default-scope gate bullet's own marker must appear exactly "
        "once in the whole document"
    )


# ===========================================================================
# Real SKILL.md -- assembled_book scope (W9)
# ===========================================================================


def test_assembled_scope_gate_wired_between_assemble_and_diff():
    window = _assembled_scope_window(_skill_text())
    assemble_offset = window.find(ASSEMBLE_INVOKE_MARKER)
    diff_offset = window.find(DIFF_RENDERED_MARKER)
    assert assemble_offset != -1, (
        "SKILL.md's W9 section no longer invokes assemble.py with the "
        "expected phrasing -- update this test's ASSEMBLE_INVOKE_MARKER to "
        "match the current prose"
    )
    assert diff_offset != -1, (
        "SKILL.md's W9 section no longer invokes diff_rendered_output.py "
        "with the expected phrasing -- update this test's "
        "DIFF_RENDERED_MARKER to match the current prose"
    )
    assert assemble_offset < diff_offset, (
        "assemble.py's own invocation must be described before "
        "diff_rendered_output.py's"
    )

    between = window[assemble_offset:diff_offset]
    assert between.count(VALIDATE_ASSEMBLED_MARKER) == 1, (
        "expected exactly one scripts/validate_assembled.py invocation "
        "strictly between the assemble.py and diff_rendered_output.py "
        "invocations -- zero means the assembled_book-scope gate is "
        "unwired, more than one is an ambiguous doc edit"
    )
    assert ASSEMBLED_SCOPE_NODESTREAM_MARKER in between, (
        "the assembled_book-scope invocation must say it runs AFTER "
        "assemble.py writes out/.assembled/nodestream.json"
    )
    assert ASSEMBLED_SCOPE_HEADING_KIND_MARKER in between, (
        "the assembled_book-scope invocation must describe checking the "
        "assembled NodeStream's own kind:\"heading\" nodes -- the "
        "incremental value this scope's gate provides over the default one"
    )


def test_assembled_scope_gate_not_mentioned_before_assemble_invocation():
    window = _assembled_scope_window(_skill_text())
    assemble_offset = window.find(ASSEMBLE_INVOKE_MARKER)
    assert assemble_offset != -1
    assert VALIDATE_ASSEMBLED_MARKER not in window[:assemble_offset], (
        "the assembled_book-scope gate must not be described before "
        "assemble.py's own invocation -- it depends on assemble.py's "
        "nodestream.json output existing on disk first"
    )


# ===========================================================================
# Matcher self-tests -- hand-built fragments, never the real SKILL.md,
# proving the window-extraction + marker checks above reject a gutted or
# misplaced invocation rather than passing on "the marker is SOMEWHERE in
# the file".
# ===========================================================================


def test_default_scope_matcher_rejects_a_gutted_decoy():
    """The marker string appears, but only as a passing mention -- the real
    invocation prose (AFTER final_audit.py succeeds / reviewed_draft_sha1)
    is absent. A naive "does the marker appear anywhere in this window"
    check would be fooled; this one must not be."""
    gutted = (
        f"{W7_HEADING} filler filler.\n\n"
        "- Some other bullet mentions `scripts/validate_assembled.py` in "
        "passing, but never actually invokes it after final_audit.py.\n\n"
        f"{W8_HEADING} filler.\n"
    )
    window = _default_scope_window(gutted)
    assert window.count(VALIDATE_ASSEMBLED_MARKER) == 1  # the bare marker is present...
    assert DEFAULT_SCOPE_AFTER_MARKER not in window        # ...but the real invocation prose is not
    assert DEFAULT_SCOPE_REVIEWED_SHA_MARKER not in window


def test_default_scope_matcher_accepts_a_genuine_fragment():
    good = (
        f"{W7_HEADING} filler filler.\n\n"
        f"- **{DEFAULT_SCOPE_GATE_MARKER}:** runs immediately "
        f"{DEFAULT_SCOPE_AFTER_MARKER}, over the converged drafts, "
        f"checking each draft's own {DEFAULT_SCOPE_REVIEWED_SHA_MARKER}.\n\n"
        f"{W8_HEADING} filler.\n"
    )
    window = _default_scope_window(good)
    assert window.count(VALIDATE_ASSEMBLED_MARKER) == 1
    assert DEFAULT_SCOPE_GATE_MARKER in window
    assert DEFAULT_SCOPE_AFTER_MARKER in window
    assert DEFAULT_SCOPE_REVIEWED_SHA_MARKER in window


def test_assembled_scope_matcher_rejects_a_decoy_placed_before_assemble():
    """The gate marker is present, and even carries the right content
    markers, but sits BEFORE assemble.py's own invocation -- must be
    rejected: the assembled_book-scope gate cannot run before the artifact
    it reads (nodestream.json) exists."""
    decoy = (
        f"{W9_HEADING} filler.\n\n"
        f"Run `scripts/validate_assembled.py` checking "
        f"{ASSEMBLED_SCOPE_NODESTREAM_MARKER} and every "
        f"{ASSEMBLED_SCOPE_HEADING_KIND_MARKER} node -- misplaced, BEFORE "
        f"assemble.py itself runs.\n\n"
        f"{ASSEMBLE_INVOKE_MARKER} filler filler.\n\n"
        f"Then run `{DIFF_RENDERED_MARKER} filler.\n\n"
        f"{REFERENCE_DOCS_HEADING}\n"
    )
    window = _assembled_scope_window(decoy)
    assemble_offset = window.find(ASSEMBLE_INVOKE_MARKER)
    assert VALIDATE_ASSEMBLED_MARKER in window[:assemble_offset], "fixture sanity: the decoy must precede assemble.py's own invocation"


def test_assembled_scope_matcher_accepts_a_genuine_fragment():
    good = (
        f"{W9_HEADING} filler.\n\n"
        f"{ASSEMBLE_INVOKE_MARKER} filler filler.\n\n"
        f"Then run `scripts/validate_assembled.py` -- AFTER assemble.py "
        f"writes `{ASSEMBLED_SCOPE_NODESTREAM_MARKER}`, checking every "
        f"{ASSEMBLED_SCOPE_HEADING_KIND_MARKER} node -- BEFORE the next "
        f"step.\n\n"
        f"Then run `{DIFF_RENDERED_MARKER} filler.\n\n"
        f"{REFERENCE_DOCS_HEADING}\n"
    )
    window = _assembled_scope_window(good)
    assemble_offset = window.find(ASSEMBLE_INVOKE_MARKER)
    diff_offset = window.find(DIFF_RENDERED_MARKER)
    assert assemble_offset < diff_offset
    between = window[assemble_offset:diff_offset]
    assert between.count(VALIDATE_ASSEMBLED_MARKER) == 1
    assert ASSEMBLED_SCOPE_NODESTREAM_MARKER in between
    assert ASSEMBLED_SCOPE_HEADING_KIND_MARKER in between


if __name__ == "__main__":
    import sys

    sys.exit(pytest.main([__file__, "-v"]))
