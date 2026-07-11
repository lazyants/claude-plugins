"""tests/glossary_epithet_rule.test.py -- regression-lock for issue #134:
the codex glossary pass had no rule preventing a salon nickname / epithet /
alias from being assigned the REFERENT's real-name `canonical_target_form`
(observed: "Sapho" wrongly clustered onto Scudery's canonical form
"Скюдери" instead of being transliterated on its own as "Сафо"). The fix
adds a three-clause canonicalization rule to `glossary_TASK.template.md`
(authoritative) and duplicates its CORE inline in
`glossary-pass-wf.template.js`'s `batchDispatchPrompt` -- the same
dual-placement precedent this plugin already uses for the `title` basis
rule (present both in the authoritative doc and inline in the dispatch
prompt itself).

This is an honest DROP-detector, not a semantic-equivalence prover: a
prose unit test cannot prove two hand-maintained surfaces stay
non-contradictory forever. What it CAN catch is a future edit that
silently deletes the rule from one surface while leaving the other
intact.

Both files already use "own", "never", "note", and "review_queue"
pervasively for unrelated pre-existing rules (basis/disposition/title),
so a plain whole-file substring check for those tokens alone would be
vacuously green even with the #134 rule entirely absent -- a false-green
that would defeat the point of a red-before-green regression lock. Every
check below instead anchors on "orthographic" -- a token that appears
NOWHERE in either file before this fix -- and requires the other clause
fragments to co-occur within a bounded character window around that
anchor, which only the actual new rule text satisfies. The window is
character-based, not line-based, so it also sidesteps this plugin's docs
hard-wrap (~79 cols): a fragment split across a wrapped line boundary
still matches.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
TEMPLATES_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets" / "templates"
TASK_SRC = TEMPLATES_DIR / "glossary_TASK.template.md"
WF_SRC = TEMPLATES_DIR / "glossary-pass-wf.template.js"

for _p in (TASK_SRC, WF_SRC):
    assert _p.is_file(), f"expected plugin template not found: {_p}"

TASK_TEXT = TASK_SRC.read_text(encoding="utf-8")
WF_TEXT = WF_SRC.read_text(encoding="utf-8")

ANCHOR = "orthographic"


def _window_around(text, needle, before=500, after=2000):
    """A bounded character slice of `text` centered on the first
    occurrence of `needle` (case-insensitive). Character-based (not
    line-based) so it survives hard-wrapped prose; bounded so it proves
    CO-LOCATION with the real rule rather than mere anywhere-in-file
    presence -- both files already use several of the required fragments
    elsewhere, for unrelated pre-existing rules."""
    idx = text.lower().find(needle.lower())
    assert idx != -1, (
        f"anchor {needle!r} not found anywhere in the text -- the #134 "
        "nickname/epithet canonicalization rule appears to be entirely "
        "absent"
    )
    start = max(0, idx - before)
    end = min(len(text), idx + len(needle) + after)
    return text[start:end]


def _assert_fragments_present(window, fragments, *, source_label, clause_label):
    missing = [f for f in fragments if f.lower() not in window.lower()]
    assert not missing, (
        f"{source_label}: the #134 nickname/epithet rule's {clause_label} "
        f"is missing distinctive fragment(s) {missing!r} within the "
        f"{ANCHOR!r}-anchored window -- rule appears dropped or reworded "
        f"away from its distinctive wording. Window:\n\n{window}"
    )


# ---------------------------------------------------------------------------
# glossary_TASK.template.md -- the AUTHORITATIVE surface.
# ---------------------------------------------------------------------------

def test_task_template_carries_clause1_orthographic_sharing_only():
    window = _window_around(TASK_TEXT, ANCHOR)
    _assert_fragments_present(
        window, ["orthographic", "same surface"],
        source_label="glossary_TASK.template.md",
        clause_label="clause 1 (orthographic sharing only)",
    )


def test_task_template_carries_clause2_independent_resolution_never_referent():
    window = _window_around(TASK_TEXT, ANCHOR)
    _assert_fragments_present(
        window, ["epithet", "own", "never", "referent"],
        source_label="glossary_TASK.template.md",
        clause_label="clause 2 (independent resolution, never the referent's form)",
    )


def test_task_template_carries_clause3_identity_link_in_note_only():
    window = _window_around(TASK_TEXT, ANCHOR)
    _assert_fragments_present(
        window, ["note", "review_queue"],
        source_label="glossary_TASK.template.md",
        clause_label="clause 3 (identity link recorded in note only)",
    )


# ---------------------------------------------------------------------------
# glossary-pass-wf.template.js -- inline reinforcement inside
# batchDispatchPrompt (dual-placement precedent: the `title` basis rule is
# likewise duplicated inline there).
# ---------------------------------------------------------------------------

_TOP_LEVEL_FUNC_RE = re.compile(r"^(?:async\s+)?function\s+(\w+)\s*\(", re.MULTILINE)


def _extract_function_body(source, name):
    """Slice one top-level `function name(...) {...}` declaration's full
    text, from its own declaration line up to the next top-level function
    declaration, or EOF (same line-boundary slicing this suite already
    relies on in tests/bounded_poll_present.test.py -- these templates
    deliberately avoid backtick template literals, so a plain LINE slice
    is exact without brace-depth counting)."""
    pattern = re.compile(rf"^(?:async\s+)?function\s+{re.escape(name)}\s*\(", re.MULTILINE)
    m = pattern.search(source)
    assert m is not None, f"function {name!r} not found in glossary-pass-wf.template.js"
    start = m.start()
    m2 = _TOP_LEVEL_FUNC_RE.search(source, m.end())
    end = m2.start() if m2 else len(source)
    return source[start:end]


def test_workflow_template_reinforcement_lives_inside_batch_dispatch_prompt():
    """The anchor must be found INSIDE batchDispatchPrompt's own body, not
    merely somewhere else in the file -- that is the function whose
    generated text a dispatched codex-glossary-pass agent actually reads."""
    body = _extract_function_body(WF_TEXT, "batchDispatchPrompt")
    assert ANCHOR in body.lower(), (
        "the #134 nickname/epithet reinforcement's anchor "
        f"{ANCHOR!r} was not found inside batchDispatchPrompt's own body -- "
        "it must live in the generated prompt text, not merely somewhere "
        "else in the file"
    )


def test_workflow_template_carries_the_orthographic_sharing_reinforcement():
    body = _extract_function_body(WF_TEXT, "batchDispatchPrompt")
    window = _window_around(body, ANCHOR)
    _assert_fragments_present(
        window, ["orthographic", "same surface"],
        source_label="glossary-pass-wf.template.js batchDispatchPrompt",
        clause_label="orthographic-sharing reinforcement",
    )


def test_workflow_template_carries_the_independent_resolution_reinforcement():
    body = _extract_function_body(WF_TEXT, "batchDispatchPrompt")
    window = _window_around(body, ANCHOR)
    _assert_fragments_present(
        window, ["epithet", "own", "never", "referent"],
        source_label="glossary-pass-wf.template.js batchDispatchPrompt",
        clause_label="independent-resolution reinforcement",
    )


def test_workflow_template_carries_the_review_queue_routing_reinforcement():
    body = _extract_function_body(WF_TEXT, "batchDispatchPrompt")
    window = _window_around(body, ANCHOR)
    _assert_fragments_present(
        window, ["review_queue", "note"],
        source_label="glossary-pass-wf.template.js batchDispatchPrompt",
        clause_label="review_queue-routing reinforcement",
    )


# ---------------------------------------------------------------------------
# Proves the window/anchor mechanism itself discriminates, on synthetic
# fixtures, before trusting it against the real templates above.
# ---------------------------------------------------------------------------

def test_window_helper_actually_discriminates():
    present = (
        "prefix filler text ... orthographic spelling variants of the "
        "same surface name ... suffix filler text"
    )
    absent = "no such rule exists anywhere in this text at all"

    window = _window_around(present, ANCHOR)
    assert "same surface" in window.lower()

    with pytest.raises(AssertionError):
        _window_around(absent, ANCHOR)


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
