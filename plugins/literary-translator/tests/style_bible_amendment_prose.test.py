"""tests/style_bible_amendment_prose.test.py -- #916: pins two prose
amendments to the shipped `style_bible.template.md`, both closing gaps an
operator hit on a live 73-unit book.

(1) The E-traps "Timing, not content" paragraph priced only the
    ALREADY-CONVERGED population a contract append flips to `stale`, and
    named only that population's mitigation (`validation.
    admit_contract_only_stale`). It said nothing about the NOT-YET-
    CONVERGED population an append also reaches -- there the cost is not
    an admissible `stale` flip but a fresh `RUN_ID` that orphans those
    drafts' dispatch tokens, which `segment_dispatch_driver.py` REFUSES by
    name (#742) rather than retranslating over them. This test pins that
    the paragraph now names that population, says the admit-only-stale
    escape hatch does not reach it (it requires an `.ever_converged.<seg>`
    sentinel a never-converged unit does not have), and points at
    `references/ledger-and-resumability.md` rather than restating that
    file's own mechanics -- plus the one economic fact that was in no
    shipped text: applying the same decision directly to those drafts
    costs only the review they already owe, so deferring it to a contract
    append buys that population nothing.

(2) The top header comment already drew a load-bearing line -- retained on
    purpose since #778 / `b3ac4f6f` -- between must-apply rule text
    (belongs inside the STYLE_CONTRACT markers, pays the hash-invalidation
    price) and the unhashed region outside the markers (glossary summary
    plus the section-G tables, settled cross-segment context, never a
    place for a rule). An earlier draft of this same amendment was
    rejected as a MAJOR for blurring exactly that line. What the header was
    missing is narrower: an instruction telling a reviewer not to REPORT a
    finding is suppression, not a style rule, and does not bind a reviewer
    -- three of four reviewers raised a finding a prior draft of this
    project tried to suppress that way. Tests 5 and 6 below are the
    regression catchers for the rejected MAJOR: they pin that the header
    STILL states the original taxonomy unchanged, so a future edit cannot
    quietly re-open the hole a book could ship under two style standards
    through.

Flattened-text matching throughout: this template is hard-wrapped at
~110 columns, so a phrase can straddle a line break. Every assertion joins
the file's lines and collapses whitespace runs to single spaces before
matching, rather than matching against raw (wrapped) lines.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
TEMPLATES_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets" / "templates"
STYLE_BIBLE_TEMPLATE = TEMPLATES_DIR / "style_bible.template.md"

assert STYLE_BIBLE_TEMPLATE.is_file(), f"expected {STYLE_BIBLE_TEMPLATE} to exist"


def _flatten(text: str) -> str:
    """Join line breaks and collapse whitespace runs to a single space, so a
    phrase this template hard-wraps across two lines still matches."""
    return re.sub(r"\s+", " ", text).strip()


def _header_comment(text: str) -> str:
    """The top `<!-- ... -->` header comment (lines 1-N), which is scope C's
    home -- isolated so a header-only assertion can't accidentally match
    text from the E-traps section below it."""
    match = re.search(r"\A<!--(.*?)-->", text, re.DOTALL)
    assert match is not None, "expected the template to open with a <!-- ... --> header comment"
    return match.group(1)


def _timing_paragraph(text: str) -> str:
    """The E-traps 'Timing, not content' block: from that heading up to the
    next '### F.' heading, isolated so a Timing-only assertion can't
    accidentally match the header comment or section F."""
    match = re.search(
        r"Timing, not content, is the constraint on appending here\.(.*?)### F\.",
        text,
        re.DOTALL,
    )
    assert match is not None, (
        "expected to find the 'Timing, not content' paragraph followed by a "
        "'### F.' heading -- has the paragraph or the heading been renamed?"
    )
    return match.group(1)


RAW_TEXT = STYLE_BIBLE_TEMPLATE.read_text(encoding="utf-8")
FLAT_HEADER = _flatten(_header_comment(RAW_TEXT))
FLAT_TIMING = _flatten(_timing_paragraph(RAW_TEXT))


# ---------------------------------------------------------------------------
# Scope B -- the E-traps "Timing, not content" paragraph
# ---------------------------------------------------------------------------


def test_timing_paragraph_names_the_not_yet_converged_population():
    assert "not-yet-converged" in FLAT_TIMING, (
        "expected the Timing paragraph to name the not-yet-converged population "
        "an append also reaches (previously it priced only the already-converged "
        "'flips to stale' population)"
    )
    assert "#742" in FLAT_TIMING, (
        "expected the Timing paragraph to cite #742 -- the dispatch driver's "
        "refusal-by-name for a not-yet-converged draft orphaned by a fresh RUN_ID"
    )
    assert "RUN_ID" in FLAT_TIMING, (
        "expected the Timing paragraph to say a fresh RUN_ID is what orphans a "
        "not-yet-converged draft's dispatch token"
    )


def test_timing_paragraph_says_admit_contract_only_stale_does_not_reach_it():
    assert "validation.admit_contract_only_stale" in FLAT_TIMING
    assert "does not reach that population" in FLAT_TIMING, (
        "expected the Timing paragraph to say explicitly that "
        "validation.admit_contract_only_stale does not reach the not-yet-converged "
        "population -- silence here reads as 'the existing mitigation covers "
        "everything', which is the gap this amendment closes"
    )
    assert ".ever_converged" in FLAT_TIMING, (
        "expected the paragraph to name the .ever_converged.<seg> sentinel as the "
        "reason admit_contract_only_stale cannot reach a never-converged unit"
    )


def test_timing_paragraph_carries_the_already_owed_review_claim_and_its_pointer():
    assert "references/ledger-and-resumability.md" in FLAT_TIMING, (
        "expected a pointer to references/ledger-and-resumability.md for the "
        "mechanics and recovery routes -- restating #742's own recovery routes "
        "here would be a second copy free to drift from the string the operator "
        "actually sees at the refusal"
    )
    assert "already owe" in FLAT_TIMING, (
        "expected the Timing paragraph to state the economic fact that applying "
        "a mid-run decision directly to the not-yet-converged drafts costs only "
        "the review those units already owe -- this is the fact verified absent "
        "from shipped text: it is what makes deferring the decision to a "
        "contract append pointless for that population"
    )
    assert "buys that population nothing" in FLAT_TIMING, (
        "expected the Timing paragraph to conclude that deferring the decision "
        "to a contract append buys the not-yet-converged population nothing"
    )


def test_timing_paragraph_does_not_claim_the_drafts_retranslate():
    """Since #742 a not-yet-converged draft orphaned by a fresh RUN_ID is
    REFUSED, not retranslated. Regression catcher for the wrong verb: the
    paragraph's ONE mention of retranslation has to be the negated form
    ('rather than retranslating'). Pinned by enumerating every mention
    rather than by listing affirmative spellings to forbid, so ANY
    affirmative rewording is caught and not only the spellings this test
    happened to think of."""
    assert "REFUSES" in FLAT_TIMING, (
        "expected the Timing paragraph to say the driver REFUSES the dispatch "
        "over the not-yet-converged drafts, by name, per #742"
    )
    mentions = re.findall(r"re-?translat\w*", FLAT_TIMING)
    assert mentions == ["retranslating"], (
        f"the Timing paragraph mentions retranslation as {mentions} -- since #742 "
        "a not-yet-converged draft is REFUSED, not retranslated, so its only "
        "permitted mention is the negated 'rather than retranslating'"
    )
    assert "rather than retranslating" in FLAT_TIMING, (
        "found a mention of retranslation that is not the negated form -- since "
        "#742 the driver refuses over these drafts instead of retranslating them"
    )


# ---------------------------------------------------------------------------
# Scope C -- the top header comment
# ---------------------------------------------------------------------------


def test_header_says_a_suppression_instruction_does_not_bind_a_reviewer():
    assert "suppression" in FLAT_HEADER, (
        "expected the header to say an instruction telling a reviewer not to "
        "report a finding is suppression, not a style rule"
    )
    assert "does not bind a reviewer" in FLAT_HEADER, (
        "expected the header to say explicitly that such an instruction does "
        "not bind a reviewer -- three of four reviewers raised a finding a "
        "prior draft tried to suppress this way, so an unstated rule here is "
        "not a hypothetical gap"
    )


def test_header_still_says_must_apply_rule_text_belongs_inside_the_markers():
    """Regression catcher for the MAJOR an earlier draft of this amendment
    drew: it said the unhashed region outside the markers 'is a place for
    rules', reversing the taxonomy #778 / b3ac4f6f retained on purpose. If
    this assertion goes red, a future edit has re-opened that hole: an
    operator could put a prose-affecting rule outside the markers, no hash
    would move, already-converged segments would never be re-judged against
    it, and the book would ship under two style standards -- early segments
    held to one, later ones to another."""
    assert "must APPLY" in FLAT_HEADER, (
        "expected the header to still say must-apply rule text belongs inside "
        "the STYLE_CONTRACT markers -- its absence means the taxonomy #778 / "
        "b3ac4f6f retained on purpose has been lost, and nothing stops a "
        "prose-affecting rule from being placed outside the hashed span, where "
        "it silently never invalidates already-converged segments"
    )
    assert "nothing else" in FLAT_HEADER, (
        "expected the header to still say must-apply rule text (plus the "
        "boundary case that keeps it from being over-applied) is ALL that "
        "belongs inside the markers -- 'nothing else' is the boundary that "
        "keeps rule text from leaking into the unhashed region"
    )


def test_header_still_limits_the_unhashed_region_to_glossary_summary_and_g_tables():
    """Second regression catcher for the same MAJOR: pins that the header
    still names exactly two things as legitimate outside the markers -- the
    glossary summary and the section-G tables read by both translate and
    review calls -- rather than a general 'place for rules'. Losing this
    line is the other half of the same failure mode: a rule placed outside
    the markers moves no hash, so an already-converged segment is never
    re-dispatched against it, and the book ships under two style standards."""
    assert "glossary summary" in FLAT_HEADER
    assert "section-G tables" in FLAT_HEADER
    assert "read in full by every translate and review call" in FLAT_HEADER, (
        "expected the header to still say the section-G tables are read in "
        "full by every translate AND review call -- that is why filling one in "
        "mid-run is safe (it binds calls still to come) while a rule there "
        "would not be (it can't reach a segment that already converged)"
    )


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
