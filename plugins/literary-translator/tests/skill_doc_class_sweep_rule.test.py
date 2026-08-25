"""tests/skill_doc_class_sweep_rule.test.py -- #534: W6 in the shipped SKILL.md
must carry the operator's class-sweep rule, and must carry the constraint that
makes the sweep safe rather than only the instruction to perform one.

## Why a doc pin, and why THIS half of the doc

#534's remedy has two halves. The executable half is one report line in
`fixPrompt` (`tests/fix_prompt_class_concentration.test.py`). The other half is
this paragraph, and it is the half that does the work: the fix turn is
deliberately forbidden to act on a dominant class, and the runtime prompt and
`references/engine-loop.md` both say so by pointing HERE. W6 is where the
procedure itself lives, so if it drifts the pointers lead nowhere.

The paragraph is pinned rather than left to prose drift because its dangerous
failure is a HALF-remembered version. "Enumerate the class and fix it" is worse
than the defect it replaces: the enumerated class runs an order of magnitude
larger than its defect set, and most of the remainder is correct under a
different rule of the same contract (field measurements in CHANGELOG.md's
1.62.0 entry -- not copied here, since nothing can check that two hand-copied
sets of digits agree). So each assertion below pins a constraint whose loss
turns the rule into a sweep, not just the rule's presence.

Deliberately substring pins over exact-paragraph pins: the prose may be
rewritten, and only these properties may not silently vanish. Whitespace is
normalised before matching, because this document hard-wraps and a pin that
breaks when a sentence rewraps is a pin nobody keeps.
"""
import re
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SKILL_MD = PLUGIN_ROOT / "skills" / "literary-translator" / "SKILL.md"
assert SKILL_MD.is_file(), f"SKILL.md not found at {SKILL_MD}"


def _w6_section() -> str:
    """The W6 pass only -- so a phrase appearing anywhere else in this 100 KB
    document cannot satisfy a pin about W6 -- with every whitespace run
    collapsed, so hard-wrapping never breaks a pin."""
    text = SKILL_MD.read_text(encoding="utf-8")
    start = text.find("**W6 Consistency pass**")
    assert start != -1, "W6 Consistency pass heading not found in SKILL.md"
    end = text.find("**W7 ", start)
    assert end != -1 and end > start, "could not delimit the W6 section (no W7 heading after it)"
    section = text[start:end]
    assert len(section) > 500, (
        f"the extracted W6 section is implausibly short ({len(section)} chars) -- "
        "the delimiters moved and these pins would be checking almost nothing"
    )
    # Strip blockquote markers before collapsing: the rule is quoted in the
    # document, and a leading "> " on a continuation line would otherwise land
    # in the middle of a normalised sentence and break every pin over it.
    section = re.sub(r"(?m)^\s*>\s?", "", section)
    return re.sub(r"\s+", " ", section)


def test_w6_states_that_a_dominant_class_is_the_operators_to_sweep():
    w6 = _w6_section()
    assert "#534" in w6, "the W6 sweep rule must cite the issue it discharges"
    assert "<rule>: N of M findings this round" in w6, (
        "W6 must name the signal the operator acts on, in the exact form the fix "
        "turn emits it -- an operator told to watch for 'concentration' with no "
        "idea what it looks like in the reply is told nothing"
    )
    assert "Enumerate the class, then adjudicate every site individually." in w6, (
        "the rule itself must be stated"
    )
    assert "Never close an enumeration by applying the rule across it." in w6, (
        "and the prohibition with it -- the rule without this clause is the "
        "uniform sweep #534 measures as the WORSE failure"
    )


def test_w6_carries_the_four_things_that_decide_a_site():
    """Each of these is a constraint whose loss turns the rule into a sweep."""
    w6 = _w6_section()
    assert "Membership in the enumeration is not a verdict on the site." in w6, (
        "the site's own source and role decide it, not its class membership"
    )
    assert "DIFFERENT rule in the contract already accounts for it as written" in w6, (
        "the competing-rule check: two rules can each be correct while one "
        "rule's enumerated class is mostly instances of the other (66 of 98 "
        "measured sites). Losing this clause is the single largest measured "
        "damage mode"
    )
    assert "never read frequency as guilt" in w6, (
        "a book's strongest convention is indistinguishable, to a rule read "
        "alone, from its most widespread violation"
    )
    assert "The apparatus is load-bearing evidence for a later sweep" in w6, (
        "the draft's own notes[] must be named as EVIDENCE the sweep reads, not "
        "as bookkeeping beside it -- one over-correction was stopped only by a "
        "note written rounds earlier. Pinned as the whole clause rather than a "
        "bare 'notes[]' because the PROPERTY is what may not vanish: a rewrite "
        "to 'check the notes[] too' keeps the token and loses the evidence claim"
    )


def test_w6_says_why_this_is_the_operators_call_and_not_the_loops():
    w6 = _w6_section()
    assert 'a reviewer sees one segment, so *"is this wrong anywhere else?"* is a question it cannot ask' in w6, (
        "W6 must state WHY the loop cannot do this, as the RELATIONSHIP and not "
        "as two words that happen to co-occur: a reviewer sees one segment, so "
        "'is this wrong anywhere else?' is a question it cannot ask. Measured: "
        "'one segment' already occurs twice elsewhere in W6, so pinning the "
        "fragments separately would pin the relationship not at all"
    )
    assert "grants the fix turn no authority over loci no finding named" in w6, (
        "and must state that the report does not license the fix turn to act -- "
        "otherwise a reader assumes the loop already handles it"
    )
    assert "a converged unit goes stale the moment you touch it" in w6, (
        "and the cost that makes acting a deliberate decision. Pinned as the "
        "whole clause: 'stale' alone occurs throughout W6 for unrelated reasons, "
        "so a bare substring pin stays green when this sentence is deleted"
    )


def test_w6_says_that_BUILDING_the_enumeration_is_the_fragile_step():
    """#746. The four bullets above all presuppose that the enumeration is a
    correct list of candidate sites. Nothing said that the list is produced by a
    pattern the OPERATOR writes for that round, and that this is the step most
    likely to be wrong -- measured on a live book, a stem scan for a spelling class
    returned 89 hits of which exactly one was a defect, and a source-side scan that
    stringified a whole block dict reported every figure at exactly 2x.

    Each pin below is a COMPLETE sentence including its terminal period. A pin
    that stops mid-clause stays green under the likeliest careless edit there
    is: a qualifier appended to the end ("... never per count ONLY WHILE there
    are fewer than ten forms"). The period is what makes that mutation red.

    RESIDUAL, stated because a check that oversells itself is the defect class
    this file exists to remove. Substring containment cannot see a NEGATING
    FRAME: quote any pinned sentence verbatim, then write "the superseded policy
    said that, and it is false under the current rule", and every assertion here
    stays green while W6 tells the operator the opposite. That is true of every
    pin this file already shipped, not only of the five added for #746, and it
    is accepted rather than closed. The alternative -- comparing the whole
    block for equality -- is the paragraph pin the docstring above deliberately
    refused: it goes RED on any re-wrap or clarification, so it false-REDs
    ordinary maintenance to guard an edit nobody makes by accident.
    What these pins defend against is DRIFT: a half-remembered rewrite that
    quietly loses a clause. A reader who writes the negation is not drifting.
    """
    w6 = _w6_section()
    assert (
        "The enumeration is produced by a pattern you wrote for this round, and "
        "that pattern is the step most likely to be wrong." in w6
    ), (
        "W6 must say that the enumeration is BUILT by a pattern the OPERATOR "
        "wrote for this round, and that building it is the fragile step. W6 is "
        "the operator's pass and the fix turn has no authority over loci no "
        "finding named, so the actor here is deictic 'you' by design. Pinned as "
        "the whole sentence: 'pattern' and 'enumeration' each occur elsewhere "
        "in W6, so either token alone would pin the relationship not at all"
    )
    assert "It fails silently in both directions and still prints a plausible count." in w6, (
        "and that the failure is SILENT IN BOTH DIRECTIONS and prints a "
        "plausible number either way -- the property that makes the count "
        "untrustworthy. An over-count invites a sweep across sites that were "
        "already correct; an under-count closes a live class. Without this "
        "clause the rule reads as a tidiness note"
    )
    assert (
        "Print the DISTINCT matched surface forms and read them before treating "
        "any hit as a defect, deciding per form and never per count." in w6
    ), (
        "and must give the remedy as an ACTION with its decision rule attached: "
        "print the distinct matched forms, decide per form, never per count. "
        "'per form, never per count' is the half that does the work -- an "
        "instruction to print the forms without it is satisfied by printing "
        "them and then acting on the total anyway"
    )
    assert "Reading the forms you matched can only show an over-count." in w6, (
        "and must say that the remedy is ONE-SIDED. The sentence above claims "
        "the pattern fails silently in BOTH directions, and printing the forms "
        "it matched cannot show a form it never matched -- an omission is "
        "absent from that list exactly like a clean run. Without this the "
        "operator reads the printed forms as having validated the enumeration"
    )
    assert (
        "The check for that direction is a different one: widen the pattern "
        "deliberately, dropping its most restrictive element, and see whether "
        "the set of distinct forms grows." in w6
    ), (
        "and must give the under-count check, which is a different action and "
        "not a harder reading of the same output. Pinned as the whole sentence "
        "including 'widen ... dropping its most restrictive element': an "
        "instruction to 'check for misses' without naming the action is "
        "satisfied by looking harder at the same list, which is the failure"
    )


def test_w6_separates_a_class_claim_from_the_sites_it_names():
    """#746, second half. A finding of the form 'X is applied inconsistently
    throughout' followed by a list of sites makes TWO claims that fail
    independently: measured on the same round, 427 italic spans in the source
    against 831 in the drafts refuted the class claim outright while three of
    the sites that finding named were real defects. An operator who measures the
    class, finds it refuted, and closes the finding drops those sites.

    The CONVERSE -- confirming sites and sweeping the class -- is NOT
    restated here. It is already carried by the quoted rule
    (test_w6_states_that_a_dominant_class_is_the_operators_to_sweep), by the
    first of the four site bullets
    (test_w6_carries_the_four_things_that_decide_a_site), by
    references/engine-loop.md, and by the runtime fix prompt itself in
    assets/templates/mass-translate-wf.template.js. The bullet points back at
    the quoted rule instead, and the deictic opener is pinned below so that a
    later edit cannot quietly turn the pointer into a fifth paraphrase.
    """
    w6 = _w6_section()
    assert (
        'Under the rule above, a finding that says a rule is applied '
        'inconsistently "throughout" and then lists sites has made two claims, '
        'and they fail independently.' in w6
    ), (
        "W6 must state the two-claims separation, and must open it by POINTING "
        "at the quoted rule rather than restating the no-sweep converse a fifth "
        "time. Pinned as the whole sentence including 'Under the rule above' "
        "for exactly that reason: drop the opener and the natural rewrite is a "
        "fresh paraphrase of a rule that already has four copies"
    )
    assert (
        "Measure the class AND open the named sites, because refuting the "
        "class claim discharges no named site." in w6
    ), (
        "and must say which direction is NEW: a refuted class claim discharges "
        "no named site. Pinned as the COMPLETE sentence, opening imperative "
        "included: a pin starting at the lowercase 'refuting' survives 'It is "
        "false that refuting the class claim discharges no named site.' -- an "
        "outright negation of the property, measured green"
    )


if __name__ == "__main__":
    import sys

    import pytest

    sys.exit(pytest.main([__file__, "-v"]))
