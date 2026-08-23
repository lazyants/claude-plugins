"""tests/skill_doc_r8_fix_turn_ownership.test.py -- #507: R8 in the shipped
SKILL.md is the only rule that AUTHORIZES concurrent fix turns, so it must also
carry the constraint that makes that authorization safe, and the scope that keeps
it from reading as a prohibition on shipped code.

## Why a doc pin, and why THIS rule

R8 grants "at most TWO long-lived executors". Nothing in the plugin enforces what
those two may touch: `runs/.driver.lock` covers a competing driver and
`segments/.codex_job.<seg>.lock` covers a codex job in its promoting phase, and
SKILL.md says in its own `--from-stalled` paragraph that neither covers a fix turn.
So the ONLY thing standing between two authorized executors and one canonical draft
is this paragraph. If the ownership sentence is lost in a rewrite, the grant
survives without the constraint -- the rule becomes strictly more dangerous than
having no rule at all, because it now reads as permission.

The dangerous failure is a HALF-remembered version, exactly as in
`skill_doc_class_sweep_rule.test.py`: "use two executors" is the memorable half and
"never on the same segment" is the half that does the work.

Two consequences are pinned rather than one, because they have different shapes and
a rewrite that keeps only the first understates the stakes by a category. The lost
update erases an applied finding; the assembly race puts never-reviewed bytes into
the delivered book. Both were established by opening the code, and the second was
found only in review round 2 -- an earlier draft of this paragraph asserted the
hash chain ruled it out, which is false.

Also pinned: R8 must NOT be readable as forbidding `mass-translate-wf.template.js`'s
`callFix()`, which dispatches one `agent()` per dirty round by design. "Fix turn" is
this plugin's term for that pipeline call too (`references/engine-loop.md`), so
without the scope sentence a reader executing the rule index concludes the shipped
template violates the contract.

Deliberately substring pins over exact-paragraph pins: the prose may be rewritten,
and only these properties may not silently vanish. Whitespace is normalised before
matching, because this document hard-wraps and a pin that breaks when a sentence
rewraps is a pin nobody keeps.

Each pin carries its clause's own SUBJECT and NEGATION, never a bare fragment.
A review round demonstrated why: pinning `callFix()` alone stayed green after
"It is not a statement about callFix()" became "It is explicitly a statement
about callFix()", and pinning "SECOND invocation, concurrent or resumed" stayed
green after "nothing excludes" became "the driver excludes" -- each inversion
asserting exactly the false contract this file exists to keep out. A fragment
pins a topic; only the whole clause pins the claim.
"""
import re
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SKILL_MD = PLUGIN_ROOT / "skills" / "literary-translator" / "SKILL.md"
ENGINE_LOOP_MD = (
    PLUGIN_ROOT / "skills" / "literary-translator" / "references" / "engine-loop.md"
)
assert SKILL_MD.is_file(), f"SKILL.md not found at {SKILL_MD}"
assert ENGINE_LOOP_MD.is_file(), f"engine-loop.md not found at {ENGINE_LOOP_MD}"


def _r8_block() -> str:
    """The R8 rule only -- so a phrase appearing anywhere else in this 100 KB
    document cannot satisfy a pin about R8 -- with every whitespace run
    collapsed, so hard-wrapping never breaks a pin."""
    text = SKILL_MD.read_text(encoding="utf-8")
    start = text.find("- **R8 — The fix turn is applied in-session")
    assert start != -1, "the R8 rule heading was not found in SKILL.md"
    end = text.find("- **R9 — ", start)
    assert end != -1 and end > start, (
        "could not delimit the R8 rule (no R9 heading after it) -- the rule "
        "index moved and these pins would be checking the wrong text"
    )
    block = text[start:end]
    assert len(block) > 1500, (
        f"the extracted R8 block is implausibly short ({len(block)} chars) -- "
        "the delimiters moved and these pins would be checking almost nothing"
    )
    return re.sub(r"\s+", " ", block)


def test_r8_forbids_two_executors_holding_one_segment():
    """The constraint that makes 'at most two' safe. Without it R8 is a bare
    grant of concurrency over an unlocked write target."""
    r8 = _r8_block()
    assert "two executors NEVER hold the same segment" in r8, (
        "R8 authorizes two executors; it must state in the same breath that "
        "they never hold the same segment. Losing this clause leaves the "
        "authorization standing with nothing limiting what it authorizes"
    )
    assert "Split the parcels by segment" in r8, (
        "and it must say HOW -- an operator told 'do not collide' with no "
        "partition rule is told nothing actionable"
    )
    assert "for as long as a round is open" in r8, (
        "the split has a DURATION. A partition released when an executor "
        "reports done, rather than when the round closes, is exactly the "
        "window a still-live fixer races through"
    )


def test_r8_says_why_nothing_enforces_it():
    """Pinned as the relationship, not as the two lock names: a reader who
    believes some lock covers this will not keep the partition."""
    r8 = _r8_block()
    assert (
        "**neither covers a fix turn**, which writes "
        "`segments/<seg>.draft.json` directly" in r8
    ), (
        "R8 must state that the plugin's two locks do NOT cover a fix turn. "
        "This is the fact that makes the ownership rule load-bearing rather "
        "than belt-and-braces"
    )
    assert "runs/.driver.lock" in r8 and "segments/.codex_job.<seg>.lock" in r8, (
        "and it must name both locks, so the reader can check the claim "
        "rather than take it on trust"
    )
    assert "not for its own race's outcome" in r8, (
        "the --from-stalled paragraph is cited for ONE fact -- that no lock "
        "covers a fix turn. Its own disclosed outcome belongs to a different "
        "race (a selector claim re-stamping a token) and a rewrite that "
        "borrows it makes R8 assert something false"
    )


def test_r8_carries_both_consequences_and_claims_no_backstop():
    """Two shapes, not one. And no categorical claim about the hash chain --
    two review rounds each falsified a different one."""
    r8 = _r8_block()
    assert "lost update" in r8, (
        "consequence 1: the later whole-draft write erases a finding the "
        "earlier fixer already applied"
    )
    assert "may not rediscover it inside" in r8, (
        "and why that is silent -- the erased finding is not re-reported for "
        "free; the next review may simply miss it within the round budget"
    )
    assert "still live when W9 runs" in r8, (
        "consequence 2, the one that reaches the reader's deliverable: a "
        "fixer that has not stopped by assembly time. Pinned separately "
        "because a rewrite keeping only the lost-update case understates the "
        "stakes by a category"
    )
    assert "reviewed by nobody" in r8, (
        "and it must say plainly that those bytes were never reviewed -- "
        "assemble.py hashes the draft when it loads the ledger and reopens "
        "the file afterwards to build the NodeStream"
    )
    assert (
        "Do not read the hash chain as a backstop for either: it rejects a "
        "late write it happens to observe and proves nothing about quiescence."
        in r8
    ), (
        "R8 must NOT be rewritten into a claim that the hash chain blocks a "
        "late write. It rejects one it happens to observe. An earlier draft "
        "of this paragraph asserted the stronger version and it was false"
    )


def test_r8_does_not_read_as_forbidding_the_shipped_pipeline_fix_call():
    """'Fix turn' names the pipeline's own call too, so an unscoped
    prohibition indicts mass-translate-wf.template.js."""
    r8 = _r8_block()
    assert "Scope: the HAND-DRIVEN fix turn" in r8, (
        "R8's spawn prohibition must be scoped. Unscoped, 'never one spawn "
        "per round, per segment' reads as forbidding callFix(), which "
        "dispatches exactly that by design"
    )
    assert (
        "It is not a statement about `mass-translate-wf.template.js`'s `callFix()`"
        in r8
    ), (
        "and it must name the call it is NOT about, carrying the NEGATION and "
        "the subject in one clause. Pinning a bare 'callFix()' would stay "
        "green after a rewrite to 'It is explicitly a statement about "
        "callFix()' -- the exact inversion this pin exists to stop"
    )
    assert "the call carries no continuation handle" in r8, (
        "with the reason the exclusion is principled rather than an "
        "exemption: that path has no warm executor to keep open, so R8's "
        "cold-start economics do not apply to it"
    )
    assert (
        "#198's SEGS uniqueness guard gives each segment one branch whose fix "
        "calls are serial" in r8
    ), (
        "and the pipeline's own guarantee must be stated at its real "
        "strength. It holds WITHIN one invocation only; 'exclusive by "
        "construction' was written first and is false path-wide"
    )
    assert (
        "nothing excludes a SECOND invocation, concurrent or resumed, holding "
        "the same segment" in r8
    ), (
        "which is the half that matters here -- resume_setup.py selects a run "
        "by input.digest with no liveness or lease check, so a second "
        "invocation can hold a segment the first one is still fixing. Pinned "
        "WITH its 'nothing excludes' subject: the fragment alone survives a "
        "rewrite to 'the driver excludes a SECOND invocation', which asserts "
        "a protection that does not exist"
    )


def test_engine_loop_index_does_not_state_a_weaker_rule_than_r8():
    """The rule index is where a reader meets R8 first. An index line that
    carries the grant but not the constraint is the half-remembered version,
    shipped."""
    index = re.sub(r"\s+", " ", ENGINE_LOOP_MD.read_text(encoding="utf-8"))
    start = index.find("- **R8**")
    assert start != -1, "the R8 index entry was not found in engine-loop.md"
    end = index.find("- **R9**", start)
    assert end != -1 and end > start, (
        "could not delimit the R8 index entry (no R9 entry after it)"
    )
    # Delimited at R9, never a fixed character window: measured, R8 is 436
    # normalised characters and a 700-char slice reaches 264 characters into
    # R9/R10 -- so the clauses could be DELETED from R8, restated under R9,
    # and every pin below would still pass.
    entry = index[start:end]
    assert "those two never hold the same segment" in entry, (
        "engine-loop.md's R8 line grants two executors, so it must carry the "
        "ownership constraint too -- a reader who stops at the index would "
        "otherwise take the grant without it"
    )
    assert "nothing locks a fix turn" in entry, (
        "and the reason, so the constraint does not read as optional care"
    )
    assert "HAND-DRIVEN" in entry, (
        "and the scope, for the same reason SKILL.md carries it"
    )


if __name__ == "__main__":
    import sys

    import pytest

    sys.exit(pytest.main([__file__, "-v"]))
