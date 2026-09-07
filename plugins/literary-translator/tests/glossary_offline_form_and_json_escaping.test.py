#!/usr/bin/env python3
"""tests/glossary_offline_form_and_json_escaping.test.py -- regression-lock for
issue #891's two shippable findings.

BOTH are prompt-prose rules, and both were reported from a live 96,539-word
Hebrew book, so both fail the same way when a later edit drops them: the pass
still runs, still reports success, and the canon it freezes is simply worse.

RULE 1 -- OFFLINE BANS THE BASIS, NOT THE FORM. `research_mode: offline`
forbids `basis: "established"` because that basis is a CITATION claim. It was
being read as forbidding the established target FORM as well, so names the
target language already spells its own way came back letter-by-letter
(`King Dovid`, `Mitzrayim`, `Avraham our father`) or queued as
`SOURCE_UNAVAILABLE:`. The corrected rule is deliberately CONDITIONAL: it
defers to the project's own `style_bible.md` section C/C-translit rule and
states no preference of its own, because a generic preference inside a prompt
overriding the project's stated convention is exactly the #860 defect. That is
why `style_bible.template.md` is one of the surfaces pinned here -- it is where
the project is now asked to state the preference, and the deference is
worthless if that question is dropped.

RULE 2 -- THE FRAGMENT IS JSON. Hebrew, Yiddish and Aramaic mark an
abbreviation with an ASCII double quote INSIDE the word (`מוהר"ן`, `ז"ל`) and a
single letter with an ASCII apostrophe. `source_form` is copied verbatim into a
JSON string, so the quote must be escaped. Neither prompt said so, and the
reporter measured ~40% of attempts lost to it before adding the sentence by
hand, then 28 of 28 batches passing first try after. It is a COST defect, not a
silent one -- `canon_validate.py --check-batch` refuses a malformed fragment on
its parse and a mark-altered one on its exact source-form coverage -- but every
retried attempt buys a fresh codex dispatch of the whole batch.

WHY FIVE SURFACES, AND WHY THAT IS THE POINT. The offline rule is stated in
four places that must agree (the authoritative task template, the dispatch
prompt the model actually receives, `canon_validate.py`'s refusal text -- which
the model reads during its OWN self-check loop -- and the reference doc), plus
`style_bible.template.md` where the project's own answer is collected. A test
covering only the two templates would stay green if either of the other three
were reverted, which is the drift this file exists to catch. Rule 2 has two
surfaces, because only the two prompts carry it.

RED BEFORE GREEN, MEASURED. Before this change every anchor below occurred
ZERO times in every file pinned here (`widely-used`, `abbreviation mark`,
`citation-backed` -- checked in all five). No assertion here can pass on the
pre-fix bytes.

HONEST SCOPE. Like `tests/glossary_epithet_rule.test.py`, this is a
DROP-detector, not a semantic-equivalence prover: it catches a future edit that
deletes or rewords one surface's copy of a rule while leaving the others
intact. It cannot prove the five surfaces still MEAN the same thing. Matching
runs against a whitespace-flattened copy of each file, so this repo's ~79-column
hard wrap can neither split a required fragment (a false red on a pure re-wrap)
nor hide a re-wrapped copy from a check.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = PLUGIN_ROOT / "skills" / "literary-translator"
TEMPLATES_DIR = SKILL_ROOT / "assets" / "templates"

TASK_SRC = TEMPLATES_DIR / "glossary_TASK.template.md"
WF_SRC = TEMPLATES_DIR / "glossary-pass-wf.template.js"
BIBLE_SRC = TEMPLATES_DIR / "style_bible.template.md"
VALIDATE_SRC = SKILL_ROOT / "assets" / "scripts" / "canon_validate.py"
REF_SRC = SKILL_ROOT / "references" / "canon-and-glossary.md"

for _p in (TASK_SRC, WF_SRC, BIBLE_SRC, VALIDATE_SRC, REF_SRC):
    assert _p.is_file(), f"expected plugin file not found: {_p}"


def _flat(path: Path) -> str:
    """The file's text with every whitespace run collapsed to one space."""
    return re.sub(r"\s+", " ", path.read_text(encoding="utf-8"))


# A bounded window proves CO-LOCATION with the real rule rather than mere
# presence somewhere in a long file: `target-language form`, `C-translit` and
# `citation` all appear elsewhere in these files for unrelated pre-existing
# rules. Character-based, applied to the flattened text.
WINDOW_BEFORE = 500
WINDOW_AFTER = 900

OFFLINE_ANCHOR = "widely-used"
ESCAPING_ANCHOR = "abbreviation mark"

# --- Rule 1: the offline BASIS-not-FORM clarification, on all five surfaces.
# Fragments are per-surface on purpose. These are five different voices --
# a prompt template, a JavaScript string, a Python refusal message, a
# reference doc and a fill-block question -- and a fragment list generic
# enough to match all five would be too generic to prove any of them.
OFFLINE_SURFACES = [
    pytest.param(
        TASK_SRC,
        ["citation-backed", "c-translit", "target-language form",
         "not a letter-by-letter obligation"],
        id="glossary_TASK.template.md",
    ),
    pytest.param(
        WF_SRC,
        ["citation-backed basis", "c-translit", "target-language form",
         "not a letter-by-letter obligation"],
        id="glossary-pass-wf.template.js",
    ),
    pytest.param(
        VALIDATE_SRC,
        ["citation-backed basis", "section c-translit rule settles the form",
         "target-language form"],
        id="canon_validate.py",
    ),
    pytest.param(
        REF_SRC,
        ["citation-backed basis", "read as written", "target-language form"],
        id="canon-and-glossary.md",
    ),
    pytest.param(
        BIBLE_SRC,
        ["citation-backed established form", "section c-translit",
         "target-language form"],
        id="style_bible.template.md",
    ),
]

# --- Rule 2: the JSON wire-format rule, on the two prompt surfaces.
ESCAPING_FRAGMENTS = [
    "wire format",
    "ascii double quote inside a word",
    "apostrophe",
    "never double the quote",
    "look-alike unicode mark",
    "for coverage on the third",
]
ESCAPING_SURFACES = [
    pytest.param(TASK_SRC, id="glossary_TASK.template.md"),
    pytest.param(WF_SRC, id="glossary-pass-wf.template.js"),
]

# --- Rule 2, the POSITIVE instruction. The fragment list above pins the
# surrounding terminology and the three prohibitions, and a reviewer proved that
# is not enough: deleting the sentence that actually says HOW to write the quote
# left every case green. These are the sentences that carry the fix, so they are
# pinned as exact clauses. The two differ on purpose -- the task template can
# show the escape literally, while the workflow builder states it in words
# because the sentence lives inside a JavaScript string literal, where a literal
# backslash-quote would have to be escaped twice and reads as a trap.
ESCAPING_INSTRUCTION_PINS = [
    pytest.param(
        TASK_SRC,
        'Inside the JSON string that `"` MUST be written `\\"`, and the `\'` is '
        "written bare",
        id="glossary_TASK.template.md",
    ),
    pytest.param(
        WF_SRC,
        "Inside the JSON string that double quote MUST be written as a backslash "
        "followed by a double quote, and the apostrophe is written bare",
        id="glossary-pass-wf.template.js",
    ),
]

# --- Rule 1's third surface: the QUESTION `style_bible.template.md` now asks.
# The whole offline fix defers to the project's own C-translit rule, so a
# project whose rule never states a preference has nothing to defer to. This
# fill-block question is the only surface that reaches such a project, and the
# same reviewer proved the classification pin above does not cover it: deleting
# the question outright left every case green.
BIBLE_QUESTION_PIN = (
    "Say here, explicitly, whether a widely-used target-language form is "
    "PREFERRED where the target language already spells a place or person its "
    "own way"
)

# --- Rule 1's second half: a row the project's own rule SETTLED is resolved,
# not an unavailable source, so it carries no `SOURCE_UNAVAILABLE:` prefix.
# Pinned as exact clauses rather than by proximity: `SOURCE_UNAVAILABLE:`
# occurs many times in each of these files for the legitimate case, and a
# token-proximity check around it passes on this clause's own inversion.
SETTLED_ROW_PINS = [
    pytest.param(
        TASK_SRC,
        "It is not for a row those rules resolved",
        id="glossary_TASK.template.md",
    ),
    pytest.param(
        WF_SRC,
        "never for a row they resolved",
        id="glossary-pass-wf.template.js",
    ),
    pytest.param(
        REF_SRC,
        "is a resolved row rather than an unavailable source",
        id="canon-and-glossary.md",
    ),
    # The reference is a MANDATORY pre-read for this pass, and it used to say
    # the prefix rides on the `transliterated`/`review_queue` pair. Under the
    # corrected rule `transliterated` is by definition settled, so that pairing
    # is not merely redundant -- it re-authorises the exact spurious marker this
    # release removes, on the one surface an operator is told to read first.
    # The settled-row sentence above does not catch it: both can stand at once.
    pytest.param(
        REF_SRC,
        "a form those rules do NOT settle is routed into `review_queue` "
        "instead, and that is the outcome the prefix marks",
        id="canon-and-glossary.md-prefix-scoped-to-review-queue",
    ),
]


def _window(flat: str, anchor: str, *, path: Path, rule: str) -> str:
    idx = flat.lower().find(anchor.lower())
    assert idx != -1, (
        f"{path.name}: anchor {anchor!r} is absent -- the #891 {rule} rule "
        f"appears to have been dropped from this surface entirely"
    )
    start = max(0, idx - WINDOW_BEFORE)
    end = min(len(flat), idx + len(anchor) + WINDOW_AFTER)
    return flat[start:end]


def _assert_fragments(window: str, fragments, *, path: Path, rule: str,
                      anchor: str) -> None:
    missing = [f for f in fragments if f.lower() not in window.lower()]
    assert not missing, (
        f"{path.name}: the #891 {rule} rule is missing fragment(s) {missing!r} "
        f"within the {WINDOW_BEFORE}+{WINDOW_AFTER}-character window around "
        f"{anchor!r} -- the rule looks dropped, or reworded away from its "
        f"distinctive wording. If the reword is deliberate, re-pin it here in "
        f"the same commit. Window:\n\n{window}"
    )


# ---------------------------------------------------------------------------
# The tables are non-empty. A parametrized file whose table silently emptied
# collects zero cases and reports exactly what a passing one reports.
# ---------------------------------------------------------------------------

def test_every_surface_table_is_populated():
    assert len(OFFLINE_SURFACES) == 5, "the offline rule is stated on five surfaces"
    assert len(ESCAPING_SURFACES) == 2, "the escaping rule is stated on two prompts"
    assert len(SETTLED_ROW_PINS) == 4
    assert len(ESCAPING_INSTRUCTION_PINS) == 2
    assert len(ESCAPING_FRAGMENTS) == 6


@pytest.mark.parametrize("path,fragments", OFFLINE_SURFACES)
def test_offline_bans_the_basis_not_the_form(path: Path, fragments):
    """Every surface that states the offline rule says the ban is on the
    citation-backed BASIS and defers the FORM to the project's own rule."""
    flat = _flat(path)
    window = _window(flat, OFFLINE_ANCHOR, path=path, rule="offline-form")
    _assert_fragments(window, fragments, path=path, rule="offline-form",
                      anchor=OFFLINE_ANCHOR)


@pytest.mark.parametrize("path", ESCAPING_SURFACES)
def test_the_fragment_is_json_and_the_prompt_says_so(path: Path):
    """Both prompts tell the model how a source form's own ASCII quote and
    apostrophe are written inside the JSON string it produces."""
    flat = _flat(path)
    window = _window(flat, ESCAPING_ANCHOR, path=path, rule="JSON-escaping")
    _assert_fragments(window, ESCAPING_FRAGMENTS, path=path,
                      rule="JSON-escaping", anchor=ESCAPING_ANCHOR)


@pytest.mark.parametrize("path,clause", SETTLED_ROW_PINS)
def test_a_settled_row_carries_no_source_unavailable_prefix(path: Path, clause: str):
    """The prefix means a human still has to research this name. Putting it on
    a row the project's own rule already settled fills `canon.json` with work
    nobody needs to do, which is the half of #891's finding 1 that survives
    into the merged canon rather than only into one fragment."""
    assert clause in _flat(path), (
        f"{path.name} no longer carries the exact clause {clause!r} -- the "
        f"#891 split between a settled row and an unavailable source has been "
        f"reworded or removed. If the reword is deliberate, re-pin it here in "
        f"the same commit."
    )


@pytest.mark.parametrize("path,clause", ESCAPING_INSTRUCTION_PINS)
def test_each_prompt_states_how_the_quote_is_written(path: Path, clause: str):
    """The sentence that does the work, pinned exactly. Everything else about
    this rule -- the abbreviation-mark context, the three prohibitions, the
    coverage consequence -- can survive an edit that quietly drops the one
    instruction telling the model what to type."""
    assert clause in _flat(path), (
        f"{path.name} no longer carries the exact clause {clause!r} -- the #891 "
        f"JSON-escaping instruction has been reworded or removed. If the reword "
        f"is deliberate, re-pin it here in the same commit."
    )


def test_the_style_bible_asks_the_project_for_its_own_answer():
    """`style_bible.template.md`'s C-translit fill block asks whether a
    widely-used target-language form is preferred. Both prompts DEFER to the
    project's rule rather than stating a preference of their own -- so dropping
    the question leaves a project with nothing to defer to, and the deference is
    the whole shape of this fix. Scoped to the C-translit section, because
    `widely-used` also appears in section C's classification just above it."""
    flat = _flat(BIBLE_SRC)
    start = flat.find("### C-translit.")
    assert start != -1, "style_bible.template.md: the C-translit heading is gone"
    section = flat[start:]
    assert BIBLE_QUESTION_PIN in section, (
        "style_bible.template.md's C-translit fill block no longer asks whether "
        f"a widely-used target-language form is preferred (looked for "
        f"{BIBLE_QUESTION_PIN!r} after the heading) -- the #891 fix defers to "
        "this answer, so removing the question removes what it defers to."
    )
