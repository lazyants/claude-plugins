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

ONE EXCEPTION, and it is the #772 block at the end of this section. That block
is an INSTRUCTION, and an instruction is reversed by appending a negating frame
beside it rather than by deleting it, so a substring pin would stay green
through its own reversal. It is therefore pinned WHOLE, with `endswith` over
the normalised section, which is also why it is authored last: there is no room
after it for a frame. The mirror residual is not closed and is not closeable by
this shape -- a negating frame planted HIGHER inside W6 leaves the `endswith`
green, exactly as one planted above the heading does for the intake pin in
skill_prose_present.test.py. Pinning all of W6 exactly is not worth its cost;
the check that would close the class is a whole-file denylist of reversal
phrasings, which reports clean for every phrasing its author did not think of.
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

    Each pin below runs through a COMPLETE sentence's terminal period; where one
    sentence carries two separable rules it is pinned in halves, and the closing
    half carries the period. A pin that stops mid-clause stays green under the
    likeliest careless edit there is: a qualifier appended to the end ("... never
    per count ONLY WHILE there are fewer than ten forms"). The period is what
    makes that mutation red.

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
    assert (
        "That check is one-directional in its turn — an already over-broad "
        "pattern only grows further under it, which reads as confirmation" in w6
    ), (
        "#775. And must say that the widening check is itself ONE-SIDED. The "
        "sentence above prescribes widening as THE check for the direction the "
        "printed forms cannot show, and an operator reads it as closing the "
        "'both directions' warning earlier in the bullet. It does not: widening "
        "an already over-broad pattern grows it further, and the larger number "
        "reads as the check confirming the miss. Without this clause the "
        "paragraph warns about both directions and prescribes a remedy for one"
    )
    assert (
        "tighten by one defensible distinction and see whether the count "
        "collapses, and treat a count that moves by an order of magnitude under "
        "a small, defensible change of pattern as the signal to stop and read "
        "rather than to sweep." in w6
    ), (
        "#775. And must give the other direction as an ACTION with its decision "
        "rule attached, for the same reason the widening pin above is a whole "
        "sentence: 'check both directions' without naming the move is satisfied "
        "by re-reading the same output. The decision rule is the half that does "
        "the work and is pinned with it -- the operator is told what a moving "
        "count MEANS (stop and read), because tightening does not prove the "
        "broad pattern over-matched, only that two defensible predicates "
        "disagree materially. A pin ending at 'collapses' would survive a "
        "rewrite that keeps the action and drops the verdict, which is how a "
        "measurement becomes sweep authority again"
    )


def test_w6_says_source_anchoring_does_not_fix_the_pattern():
    """#775, second half. 1.72.0 (#760) added the paragraph telling the operator
    to enumerate the population from the SOURCE rather than from a draft-side
    matcher, and that rule is correct: a draft-side pattern defines its own
    residue. But it fixes WHICH POPULATION is counted, not whether the pattern
    counting it is right, and a reader who has just been given a rule that
    solves one enumeration failure reads it as solving enumeration.

    The measurement behind this is a source-side sweep -- run exactly as that
    paragraph prescribes -- where two defensible patterns for one class returned
    688 sites and 0 minutes apart. Anchoring the denominator on the source did
    nothing about it, because the source-side pattern is also one the operator
    wrote.

    Pinned separately from the trap bullet, and not merged into it, because the
    two live in different paragraphs and the loss modes differ: the bullet can
    lose the two-directional check while this paragraph keeps its rule intact,
    and this paragraph can be read as sufficient while the bullet is word-perfect.

    Deliberately NOT pinned: the 688/0 digits. This file's module docstring puts
    field measurements in CHANGELOG.md, since nothing here can check that two
    hand-copied sets of digits agree.
    """
    w6 = _w6_section()
    assert "Enumerate the population from the SOURCE, not from your own matcher." in w6, (
        "the source-anchoring rule this qualification attaches to must still be "
        "here -- if 1.72.0's paragraph is ever cut, the qualification below is "
        "left pointing at nothing and should be cut with it, not silently kept"
    )
    assert (
        "Anchoring on the source fixes the DENOMINATOR, not the pattern: the "
        "source-side pattern is one you wrote too, and the trap above applies "
        "to it unchanged." in w6
    ), (
        "and W6 must say that source-anchoring is BETTER than a draft-side "
        "sweep, not SUFFICIENT. Pinned as the complete sentence including its "
        "terminal period: a pin stopping at 'not the pattern' survives a "
        "qualifier appended to the end, and the second half is the half that "
        "does the work -- it points the reader back at the pattern trap rather "
        "than restating it, which is what keeps this from being a fifth copy "
        "of the no-sweep rule"
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


# --- #772: ending the class sweep is the HUMAN's decision --------------------
#
# W6 already told the operator that a converged unit goes stale the moment it is
# touched and that acting is a deliberate call. What it never said is that the
# call RECURS: the sweep's own edits re-open converged units, whose re-review
# yields fresh class findings, whose sweep re-opens the next set. That loop has
# no terminating condition anywhere in this pipeline -- no gate reports it and no
# status names it -- so an operator waiting for a signal waits for one that never
# arrives. Measured before this change, whitespace-collapsed over the whole of
# SKILL.md: "no terminating condition" and "does not terminate" each occurred
# ZERO times, as did every one of fifteen ask-the-user phrasings.
#
# WHY THIS PIN IS AN `endswith` AND NOT A MEMBERSHIP ASSERTION. Every needle
# above asserts containment, which is the right shape for a FACT. This passage
# is an INSTRUCTION, and an instruction survives its own needle: leave every
# pinned character in place, append "That was the old policy; the orchestrator
# now closes the loop itself", and containment stays green while the meaning
# inverts. `endswith` over the normalized section is a bounded window with no
# room after it, so anything appended inside W6 below this block -- a negating
# frame included -- moves the tail and goes red. That is also why the block is
# authored at the END of the section rather than beside the sentence it extends.
W6_SWEEP_HAND_BACK_EXPECTED = (
    "**Ending this sweep is a decision, and it is the human's.** Sweeping "
    "a class edits sites inside units that have already converged; their "
    "drafts drift off the hash their review was taken against, they land "
    "in `stale`, and re-reviewing them yields fresh class findings whose "
    "sweep drifts the next set. That loop has no terminating condition in "
    "this pipeline — no gate reports it and no status names it — so where "
    "to stop is not a state you can wait for. Report the population the "
    "last round re-opened, what each route costs, and a recommendation, "
    "and let the human say whether another sweep round opens. What they "
    "decide is the sweep BOUNDARY, not the fate of a draft. Every unit "
    "already touched still takes one of the two routes above — "
    "`--from-converged` for a confirming re-review, or a restore — "
    "because nothing here delivers a draft the reviewer never saw: "
    "`assemble.py` refuses one outright (\"a hand-edit the reviewer never "
    "saw must not be assembled\") and W7's hard check 2 reports the same "
    "mismatch independently. Record why the sweep stopped in "
    "`consistency_issues.md`, where a record that authorizes nothing "
    "belongs; never as a key on a ledger fragment or record, which "
    "`ledger_update.py` will not write, which both schemas refuse, and "
    "which the next ordinary write erases anyway. One completed book "
    "ended this loop by hand-writing ledger records the supported writer "
    "cannot produce — that is what happened on it, not a route to copy. "
    "Note what was decided there: every finding was applied, and only the "
    "bookkeeping was accepted."
)

def test_w6_says_the_sweep_loop_has_no_terminating_condition_and_the_human_ends_it():
    w6 = _w6_section().rstrip()
    # Diagnosis needles first -- they name WHICH clause moved, instead of
    # leaving a 1500-character diff to read.
    assert "That loop has no terminating condition in this pipeline" in w6, (
        "W6 must state the non-termination as a property of the PIPELINE, not "
        "as a hazard the operator might happen to notice: no gate reports it "
        "and no status names it, so it is not a state anyone can wait for"
    )
    assert "What they decide is the sweep BOUNDARY, not the fate of a draft" in w6, (
        "and must scope the human's decision correctly. No supported route "
        "delivers a draft the reviewer never saw -- assemble.py refuses one "
        "outright and W7's hard check 2 reports the same mismatch -- so an "
        "instruction to 'accept the delta' would leave the operator holding a "
        "record and an undeliverable book"
    )
    assert "never as a key on a ledger fragment or record" in w6, (
        "and must forbid the ledger destination explicitly. ledger_update.py's "
        "payload schema rejects a seventh key, the fragment and the "
        "materialized record both close with unevaluatedProperties:false so an "
        "ordinary merge refuses, and a supported write rebuilds the fragment "
        "fresh -- three independent reasons a stamp there is silently lost"
    )
    assert w6.endswith(W6_SWEEP_HAND_BACK_EXPECTED), (
        "the sweep hand-back must be the LAST thing in W6 and must match its "
        "pinned text exactly. A membership assertion here would stay green "
        "under a negating frame appended beside it; the whole point of this "
        "shape is that there is no room after the block for one"
    )
