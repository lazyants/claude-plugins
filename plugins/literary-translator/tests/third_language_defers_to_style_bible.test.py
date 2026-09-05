"""tests/third_language_defers_to_style_bible.test.py -- #860: the generic
"ALWAYS gloss embedded third-language text in-text" instruction reached the
translator on every dispatch and contradicted the project's OWN convention,
which `style_bible.template.md` has had a required-fill block for since #203.

A project whose convention is "no source script in the target text at all"
therefore shipped two contradictory rules, and the generic one was the one
inside the prompt the translator actually follows. It did what the prompt said.
Measured on a Hebrew->English series: 62 of 97 first-round findings on one
volume were this single class, and 234 / 755 non-Latin runs stand in the
visible English of two already-delivered volumes.

FOUR carriers state that rule, not the two the issue named, and a fix that
misses any one of them leaves the class open:

  1. `translate_TASK.template.md`         -- read by the translator (dispatch
                                             prompt's own read list).
  2. `style_bible.template.md`            -- read by translator AND reviewer;
                                             sits directly above the fill block
                                             that is supposed to override it.
  3. `mass-translate-wf.template.js`      -- the strongest one: spliced
                                             VERBATIM into every per-segment
                                             dispatch prompt, and not
                                             project-customizable at all. A
                                             fill block in a scaffolded file
                                             would never have reached it.
  4. `references/engine-loop.md`          -- a MANDATORY pre-read (`SKILL.md`'s
                                             pre-read mandate) held by the
                                             session that applies the R8
                                             hand-driven fix turn. Left
                                             unqualified, that session restores
                                             by hand the gloss the project's
                                             convention removed.

What this file pins, per carrier: the clause still states the default AND, in
that SAME clause, names `embedded-third-language-convention` and says that
block governs. A file-level "the style bible is mentioned somewhere" assertion
would pass on a file whose third-language clause was never touched, so every
match is made against the containing clause only.

It also pins the DECISION not to fix this by giving `translate_TASK.template.md`
its own `LT_REQUIRED_FILL` block: that file is not in `scaffold_validate.py`'s
`MARKER_SCAN_FILES`, so an unwired block would ship `LT_PLACEHOLDER_UNFILLED`
into a live dispatch prompt with every W1 gate green -- strictly worse than the
defect. Restating the convention in a second file would also re-create, one file
later, the very drift this issue is about.

The carrier list is a fixed literal and its length is asserted: a loop that
silently iterates zero times prints exactly what a passing one prints.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = PLUGIN_ROOT / "skills" / "literary-translator"
TEMPLATES_DIR = SKILL_ROOT / "assets" / "templates"
REFERENCES_DIR = SKILL_ROOT / "references"

TRANSLATE_TASK_TEMPLATE = TEMPLATES_DIR / "translate_TASK.template.md"
STYLE_BIBLE_TEMPLATE = TEMPLATES_DIR / "style_bible.template.md"
WORKFLOW_TEMPLATE = TEMPLATES_DIR / "mass-translate-wf.template.js"
ENGINE_LOOP_REFERENCE = REFERENCES_DIR / "engine-loop.md"

# The fill-block id `style_bible.template.md` carries for this decision (#203).
# Every carrier must name it, so the reader is sent to ONE governing place.
MARKER_ID = "embedded-third-language-convention"


def _markdown_bullet(text, opener):
    """The bullet that starts with `opener`, up to the start of the next
    top-level list item, ANY heading, or an HTML comment.

    Every boundary here was put in by a demonstrated false green. Stopping only
    at bold bullets (`- **`) let a plain sibling `- ` bullet supply the
    deference wording while the third-language bullet itself carried none; the
    narrower fix, stopping at a column-0 `- `, then let the same wording in at
    one space of indent. So the boundary is any list item at any indent.
    Clause scoping is the entire point of this helper: an over-capturing
    extractor is indistinguishable from no extractor at all.
    """
    start = text.index(opener)
    rest = text[start + len(opener) :]
    end = len(rest)
    for pattern in (r"\n *[-*+] ", r"\n#{1,6} ", r"\n<!-- "):
        found = re.search(pattern, rest)
        if found is not None:
            end = min(end, found.start())
    return opener + rest[:end]


def _markdown_section(text, heading):
    """One section, up to the next heading of the SAME OR HIGHER level.

    Same lesson: stopping only at `\n## ` let the next heading be demoted to
    `# ` and the section swallow it, so wording living under a sibling section
    satisfied the assertion for this one.
    """
    start = text.index(heading)
    level = len(heading) - len(heading.lstrip("#"))
    rest = text[start + len(heading) :]
    found = re.search(r"\n#{1,%d} " % level, rest)
    return heading + (rest[: found.start()] if found is not None else rest)


def _js_prompt_string(text, needle):
    """The emitted PROMPT STRING carrying `needle` -- the `lines.push("...")`
    string literal, decoded, not the physical source line.

    Reading the raw line let a trailing `// ...` comment carrying the deleted
    wording satisfy every assertion while the prompt the translator actually
    receives had lost it entirely. Only what `translatePrompt()` emits counts,
    so every literal on the line is parsed and unescaped, and the one actually
    carrying the anchor is returned -- taking the FIRST literal instead failed
    the carrier spuriously as soon as an unrelated `lines.push()` shared the
    line.
    """
    source_lines = [line for line in text.splitlines() if needle in line]
    assert len(source_lines) == 1, (
        f"expected exactly 1 line carrying {needle!r}, got {len(source_lines)}"
    )
    literals = [
        json.loads('"' + m.group(1) + '"')
        for m in re.finditer(r'lines\.push\(\s*"((?:[^"\\]|\\.)*)"', source_lines[0])
    ]
    assert literals, (
        "expected the third-language sentence to sit inside a lines.push(\"...\") "
        "string literal; it no longer does, so this test cannot see the emitted prompt"
    )
    carrying = [literal for literal in literals if needle in literal]
    assert carrying, (
        f"{needle!r} is on the source line but NOT inside any pushed string -- "
        f"it is in a comment or other non-emitted text, so it never reaches the "
        f"translator. Emitted strings on that line: {literals!r}"
    )
    assert len(carrying) == 1, f"expected 1 pushed string carrying {needle!r}, got {len(carrying)}"
    return carrying[0]


# (label, path, clause extractor, clause anchor, the notes-only prohibition
# this carrier must still state UNCONDITIONALLY). The prohibition is pinned
# per carrier and phrase-exact on purpose: asserting only that the word
# "notes" appears somewhere in the clause passed with the whole prohibition
# deleted, because a neighbouring sentence tells the translator to "flag it
# in notes as NEW:".
#
# The pin is phrase-exact by design, and the cost is accepted: a meaning-
# preserving rewording ("never confine a translation to notes") fails here and
# must update its pin. That is the same trade this repo makes in
# `retired_wording_pins.test.py`, and it is the only version of this assertion
# that does not go vacuous -- a looser one was tried and passed with the whole
# prohibition deleted.
CARRIERS = (
    (
        "translate_TASK.template.md",
        TRANSLATE_TASK_TEMPLATE,
        _markdown_bullet,
        "- **Embedded third-language text**",
        r"never\s+buried\s+only\s+in\s+`notes`",
    ),
    (
        "style_bible.template.md",
        STYLE_BIBLE_TEMPLATE,
        _markdown_bullet,
        "- **Embedded third-language text**",
        r"never\s+buried\s+only\s+in\s+a\s+translator's\s+internal\s+notes",
    ),
    (
        "mass-translate-wf.template.js",
        WORKFLOW_TEMPLATE,
        _js_prompt_string,
        "Any embedded third-language text",
        r"never\s+a\s+notes-only\s+translation,\s+because\s+notes\s+never\s+reach\s+the\s+reader",
    ),
    (
        "engine-loop.md",
        ENGINE_LOOP_REFERENCE,
        _markdown_section,
        "## Foreign-language insertions",
        r"never\s+a\s+notes-only\s+translation",
    ),
)

# Asserted, not printed. Every parametrized case below is driven off this
# tuple, so a truncated or emptied list must fail rather than read as a pass.
EXPECTED_CARRIER_COUNT = 4

# Both per-carrier tests run over the same tuple under the same ids. Sharing one
# decorator is what keeps them from drifting onto different carrier lists.
for_each_carrier = pytest.mark.parametrize(
    ("label", "path", "extractor", "anchor", "prohibition"),
    CARRIERS,
    ids=[label for label, *_ in CARRIERS],
)


def _clause(label, path, extractor, anchor):
    assert path.is_file(), f"{label}: expected {path} to exist"
    text = path.read_text(encoding="utf-8")
    occurrences = text.count(anchor)
    assert occurrences == 1, (
        f"{label}: expected exactly 1 occurrence of the clause anchor {anchor!r}, "
        f"got {occurrences} -- this test can no longer locate the clause"
    )
    return extractor(text, anchor)


def test_carrier_list_is_complete():
    """The count is the guard: a carrier dropped from the list takes its whole
    parametrized case with it, silently, and every remaining case still passes."""
    assert len(CARRIERS) == EXPECTED_CARRIER_COUNT
    assert len({label for label, *_ in CARRIERS}) == EXPECTED_CARRIER_COUNT


@for_each_carrier
def test_third_language_clause_defers_to_the_style_bible(label, path, extractor, anchor, prohibition):
    del prohibition  # this test checks the deference; the sibling test checks the prohibition
    clause = _clause(label, path, extractor, anchor)

    assert MARKER_ID in clause, (
        f"{label}: the embedded-third-language clause does not name the "
        f"{MARKER_ID!r} block, so a translator following it has no way to know "
        f"the project decided this question elsewhere (#860)"
    )
    assert re.search(r"\bgovern", clause, re.IGNORECASE), (
        f"{label}: the clause names the fill block but never says that block "
        f"GOVERNS -- naming both rules without ranking them is the #860 defect"
    )
    assert re.search(r"\bdefault\b", clause, re.IGNORECASE), (
        f"{label}: the in-text gloss must be stated as the DEFAULT; an "
        f"unqualified rule is what overrode the project's convention (#860)"
    )
    assert "ALWAYS gloss" not in clause and "ALWAYS glossed" not in clause, (
        f"{label}: the clause still asserts the gloss unconditionally (#860)"
    )


@for_each_carrier
def test_notes_only_prohibition_survives_unconditionally(label, path, extractor, anchor, prohibition):
    """The one half that is NOT the project's to override: a translation living
    only in `notes[]` never reaches the reader whatever the convention says.
    Deferring the gloss must not have deferred this with it."""
    clause = _clause(label, path, extractor, anchor)
    assert re.search(prohibition, clause, re.IGNORECASE | re.DOTALL), (
        f"{label}: the notes-only prohibition is gone from the clause -- it is "
        f"convention-independent and must survive the #860 deference. Expected "
        f"the clause to still state {prohibition!r}"
    )


def test_translate_task_template_has_no_required_fill_block():
    """#860 proposed fixing this with a fill block in `translate_TASK.template.md`.
    Refused on purpose: that file is absent from `scaffold_validate.py`'s
    `MARKER_SCAN_FILES`, so nothing would reject an unfilled one and the
    `LT_PLACEHOLDER_UNFILLED` sentinel would reach a live dispatch prompt with
    every W1 gate green. Deference to the ONE block that IS gated is the fix."""
    text = TRANSLATE_TASK_TEMPLATE.read_text(encoding="utf-8")
    assert "LT_REQUIRED_FILL" not in text
    assert "LT_PLACEHOLDER_UNFILLED" not in text

    scaffold_validate = (SKILL_ROOT / "assets" / "scripts" / "scaffold_validate.py").read_text(
        encoding="utf-8"
    )
    marker_scan = re.search(r"^MARKER_SCAN_FILES = \[(?P<body>[^\]]*)\]", scaffold_validate, re.MULTILINE)
    assert marker_scan is not None, "could not locate MARKER_SCAN_FILES in scaffold_validate.py"
    assert "translate_TASK.md" not in marker_scan.group("body"), (
        "translate_TASK.md is now marker-scanned; a required-fill block there is "
        "gated after all, so revisit the #860 decision recorded above"
    )


def test_style_bible_keeps_the_block_the_others_defer_to():
    """Every other carrier now points at this block. If it were removed, three
    files would send the reader to a place that no longer exists."""
    text = STYLE_BIBLE_TEMPLATE.read_text(encoding="utf-8")
    assert f"<!-- LT_REQUIRED_FILL_BEGIN: {MARKER_ID} -->" in text
