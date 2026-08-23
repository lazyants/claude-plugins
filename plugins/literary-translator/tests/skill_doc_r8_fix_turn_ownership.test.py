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

What is pinned about the CONSEQUENCE is deliberately narrow, and three review
rounds are the reason. Each round caught this paragraph characterising the
downstream gates, and each characterisation was wrong in a different direction:
that the hash chain rules a late write out; that it does not; that
`validate_assembled.py` never consults the ledger (it does -- its reviewed-SHA
rebind exists precisely to catch a draft edited after the audit). So R8 no longer
describes what any gate guarantees. It states the one property that was verified
and that no gate covers: every gate here proves the reviewer saw the CURRENT
bytes, and none proves a finding already applied to them SURVIVED.

Also pinned: R8 must NOT be readable as forbidding `mass-translate-wf.template.js`'s
`callFix()`, which dispatches one `agent()` per dirty round by design. "Fix turn" is
this plugin's term for that pipeline call too (`references/engine-loop.md`), so
without the scope sentence a reader executing the rule index concludes the shipped
template violates the contract.

Deliberately substring pins over exact-paragraph pins: the prose may be rewritten,
and only these properties may not silently vanish. Whitespace is normalised before
matching, because this document hard-wraps and a pin that breaks when a sentence
rewraps is a pin nobody keeps.

Each pin carries its clause's own SUBJECT and NEGATION, never a bare fragment:
a fragment pins a topic, only the whole clause pins the claim. Each assertion
below carries the inversion that motivated its own pin -- which is the text a
failing run actually prints.
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


def _delimited_block(path: Path, start_marker: str, end_marker: str, min_chars: int) -> str:
    """The delimited passage only -- so a phrase appearing anywhere else in the
    document cannot satisfy a pin about it -- with every whitespace run
    collapsed, so hard-wrapping never breaks a pin.

    Always delimited at the NEXT rule, never by a character window: measured,
    R8's entry in engine-loop.md is 419 normalised characters, so the 700-char
    slice this file first used reached 281 characters into R9/R10 -- the
    clauses could be deleted from R8, restated under R9, and still pass.

    `min_chars` is not decoration. If a marker moves, the slice collapses and
    every pin over it would be checking almost nothing while still reporting a
    clean run -- the exact shape of a check that passes because it ran over
    nothing at all.
    """
    text = path.read_text(encoding="utf-8")
    start = text.find(start_marker)
    assert start != -1, f"{start_marker!r} was not found in {path.name}"
    end = text.find(end_marker, start)
    assert end != -1 and end > start, (
        f"could not delimit the block in {path.name}: no {end_marker!r} after "
        f"{start_marker!r} -- the rule index moved and these pins would be "
        f"checking the wrong text"
    )
    block = re.sub(r"\s+", " ", text[start:end])
    assert len(block) >= min_chars, (
        f"the extracted block from {path.name} is implausibly short "
        f"({len(block)} chars, expected >= {min_chars}) -- the delimiters "
        f"moved and these pins would be checking almost nothing"
    )
    return block


def _r8_block() -> str:
    """R8 in SKILL.md, which carries the rule itself."""
    return _delimited_block(
        SKILL_MD, "- **R8 — The fix turn is applied in-session", "- **R9 — ", 1500
    )


def _r8_index_entry() -> str:
    """R8's one-line entry in engine-loop.md's rule index. Measured at 419
    normalised characters, so 300 is a floor a real entry clears and a
    collapsed slice does not. Re-measure it here if the entry is rewritten:
    a floor above the real size is a permanent RED, one far below it stops
    catching a collapsed slice."""
    return _delimited_block(ENGINE_LOOP_MD, "- **R8**", "- **R9**", 300)


def test_r8_forbids_two_executors_holding_one_segment():
    """The constraint that makes 'at most two' safe. Without it R8 is a bare
    grant of concurrency over an unlocked write target."""
    r8 = _r8_block()
    assert "two executors NEVER hold the same segment" in r8, (
        "R8 authorizes two executors; it must state in the same breath that "
        "they never hold the same segment. Losing this clause leaves the "
        "authorization standing with nothing limiting what it authorizes"
    )
    assert (
        "Split the parcels by segment and keep that split for as long as a "
        "round is open" in r8
    ), (
        "and it must say HOW, with the duration attached to the split itself: "
        "an operator told 'do not collide' with no partition rule is told "
        "nothing actionable, and a partition released when an executor reports "
        "done rather than when the round closes is exactly the window a "
        "still-live fixer races through. Pinned as one clause because a bare "
        "'for as long as a round is open' is the subject-less fragment this "
        "file's own rule forbids -- it survives the duration being re-attached "
        "to something else entirely"
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


def test_r8_states_the_lost_update_consequence():
    """The consequence that was actually verified, and the reason it is
    silent."""
    r8 = _r8_block()
    assert "lost update" in r8, (
        "the later whole-draft write erases a finding the earlier fixer "
        "already applied -- name the failure, so a reader can recognise it"
    )
    assert (
        "two fixers off the same predecessor end with the later one's text" in r8
    ), (
        "and the MECHANISM, with its subject: a bare 'lost update' names a "
        "category and pins nothing about how this one happens"
    )
    assert "may simply not be found again inside" in r8, (
        "and why it is silent -- the erased finding is not re-reported for "
        "free; the fresh review is a new model pass over the surviving text "
        "and may miss it within the round budget"
    )


def test_r8_does_not_claim_a_gate_recovers_the_lost_fix():
    """The ABSENCE of a false claim -- a different property from the
    consequence above, and the one three review rounds kept falsifying. Split
    out so a red run names which class broke."""
    r8 = _r8_block()
    assert "**No gate recovers it, and do not expect one to.**" in r8, (
        "R8 must say plainly that no gate recovers the erased fix. Three "
        "rounds each caught a DIFFERENT wrong characterisation of the "
        "downstream gates here; this sentence is what replaced them"
    )
    assert (
        "Every gate here proves the reviewer saw the current bytes. None "
        "proves that a finding already applied to them survived." in r8
    ), (
        "and it must draw the line at the exact place it falls, as one "
        "clause carrying both halves. The rebind gates are REAL -- "
        "validate_assembled.py's exists precisely to catch a draft edited "
        "after the audit -- so a rewrite that says they do nothing is as "
        "false as one that says they cover this. What they do not cover is "
        "SURVIVAL of an applied finding, and that is the whole claim"
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
    entry = _r8_index_entry()
    assert "those two never hold the same segment" in entry, (
        "engine-loop.md's R8 line grants two executors, so it must carry the "
        "ownership constraint too -- a reader who stops at the index would "
        "otherwise take the grant without it"
    )
    assert "nothing locks a fix turn" in entry, (
        "and the reason, so the constraint does not read as optional care"
    )
    assert "the SPAWN rule is hand-driven only" in entry, (
        "and the scope -- but attached to the SPAWN rule specifically, which "
        "is the only half it applies to"
    )
    assert "while segment ownership binds BOTH" in entry, (
        "R8's two halves scope DIFFERENTLY, and the index is where a reader "
        "meets them first. An index that scopes the whole rule to the "
        "hand-driven path lets a reader exempt two concurrent pipeline() "
        "invocations from segment ownership -- recreating the lost update "
        "this rule exists to prevent, which is exactly what the authoritative "
        "R8 paragraph says DOES bind that path"
    )
    assert (
        "a second `pipeline()` invocation can hold a segment the first one is "
        "still fixing" in entry
    ), (
        "with the reason, so the binding does not read as an arbitrary "
        "asymmetry a reader may talk themselves out of"
    )


if __name__ == "__main__":
    import sys

    import pytest

    sys.exit(pytest.main([__file__, "-v"]))
