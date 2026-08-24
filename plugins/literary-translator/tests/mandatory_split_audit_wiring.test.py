"""tests/mandatory_split_audit_wiring.test.py

RFC #215, item 1e: the homonym-split evidence gate (category 5 of
``canon_adjudication_audit.py --check``) must be wired as a MANDATORY
W-step, distinct from the categories-1-4 gate that stays an opt-in,
Deliver-time (W7/W8) invocation of the same script. Since #244 that opt-in
covers categories 2-4 and category 1's identical-surface shape only: a
category-1 surface-variant finding is never masked by ``--advisory`` and so
halts this mandatory W-step too.

This is a doc-structural test, not a behavioral one -- the audit script's
own logic (categories, ``--advisory`` narrowing, ``--particle-config``
plumbing) is covered by ``canon_adjudication_audit.test.py`` and
``sense_translated_behaviour.test.py``. What is NOT covered anywhere else
is whether ``SKILL.md`` and ``references/orchestration-and-batching.md``
actually tell an operator to run the gate at the right point in the
pipeline -- the standalone-audit tests can all be green while the gate
itself is never invoked (the exact R2 BLOCKER the shared contract calls
out). So this file greps the shipped docs directly and asserts:

  1. In ``SKILL.md``, both W3-rejoin branches -- the
     ``{"no_new_candidates": true, "batches": []}`` SKIP path and the
     "Otherwise run the codex-glossary-pass" path -- are described BEFORE
     the mandatory gate's literal invocation, which itself appears BEFORE
     the "W3a Segpack generation" heading. Both branches converge on one
     unconditional next step; the ordering proves the gate sits at that
     rejoin point rather than being tucked away at Deliver.
  2. That invocation names ``canon_adjudication_audit.py --check``, an
     explicit ``--particle-config`` (the profile's literal
     ``source.language.particle_config`` value -- never reconstructed from
     ``source.language.code``), and ``--advisory`` -- dropping any of the
     three would either silently skip the gate, break language-config
     threading (evidence verification cannot run without it), or
     (dropping ``--advisory``) reintroduce Deliver-time-only categories-2-4
     blocking this early in the pipeline, none of which the contract calls
     for here. Keeping ``--advisory`` does NOT make this invocation
     non-blocking for category 1's surface-variant shape (#244), which it
     cannot mask.
  3. The stale "not yet wired as a mandatory W-step" claim is gone from
     SKILL.md -- a regression guard against silently reverting the wiring
     while leaving the old opt-in-only prose in place.
  4. ``references/orchestration-and-batching.md`` carries an equivalent
     bullet strictly between its "W3 Bootstrap" and "W3a Segpack
     generation" bullets, naming the same script + flag.

Collection note: like every ``*.test.py`` file in this suite, pytest's
default "prepend" import mode cannot resolve this dotted module name --
run with
``python3 -m pytest --import-mode=importlib tests/mandatory_split_audit_wiring.test.py``.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SKILL_PATH = PLUGIN_ROOT / "skills" / "literary-translator" / "SKILL.md"
ORCHESTRATION_PATH = (
    PLUGIN_ROOT / "skills" / "literary-translator" / "references" / "orchestration-and-batching.md"
)

assert SKILL_PATH.is_file(), f"SKILL.md not found at {SKILL_PATH}"
assert ORCHESTRATION_PATH.is_file(), f"orchestration-and-batching.md not found at {ORCHESTRATION_PATH}"

NO_NEW_CANDIDATES_MARKER = '{"no_new_candidates": true, "batches": []}'
GLOSSARY_PASS_BRANCH_MARKER = "Otherwise run the codex-glossary-pass"
W3A_HEADING_MARKER = "W3a Segpack generation"
MANDATORY_COMMAND_MARKER = "canon_adjudication_audit.py --check"
PARTICLE_CONFIG_FLAG = "--particle-config"
ADVISORY_FLAG = "--advisory"
STALE_NOT_MANDATORY_CLAIM = "not yet wired as a mandatory W-step"

# #727: the third W3-rejoin branch (glossary.enabled: false -- the project
# opted out of the researched name/realia canon entirely). LANGUAGE_SMOKE_
# TEST_MARKER anchors the mandatory language smoke test's own heading
# (text.find() returns the FIRST occurrence, i.e. the heading itself, not
# this branch's own later back-reference to it) -- name detection stays
# load-bearing on this branch, so it must never be documented as skippable
# alongside the smoke test.
LANGUAGE_SMOKE_TEST_MARKER = "language smoke test"
DISABLED_BRANCH_MARKER = "the project declared it wants no researched"
REJOIN_PHRASE_MARKER = "rejoin at the mandatory homonym-split evidence gate"
# The mandatory gate's own heading, i.e. the start of the authoritative
# summary paragraph that enumerates which W3-rejoin branches it runs after.
MANDATORY_GATE_HEADING = "Mandatory homonym-split evidence gate (category 5, always runs)"

FENCE_RE = re.compile(r"```(.*?)```", re.DOTALL)


def _skill_text() -> str:
    return SKILL_PATH.read_text(encoding="utf-8")


def _orchestration_text() -> str:
    return ORCHESTRATION_PATH.read_text(encoding="utf-8")


def _mandatory_command_block(text: str, start: int, end: int) -> str:
    """Returns the content of the SINGLE fenced (triple-backtick) code block
    inside text[start:end] that invokes MANDATORY_COMMAND_MARKER.

    Finding 6 (codex round-4): a naive "does PARTICLE_CONFIG_FLAG/
    ADVISORY_FLAG/MANDATORY_COMMAND_MARKER each appear SOMEWHERE in this
    window" check is fooled by SKILL.md's own opt-in categories-1-4
    paragraph, which mentions the same script name (as an inline
    single-backtick code span, never a fenced block) a few lines above the
    real mandatory invocation -- so all three tokens could appear in the
    window from TWO DIFFERENT, unrelated places even if the actual mandatory
    command were gutted. Anchoring on "a fenced code block that itself
    contains the command marker" ties the flags to the one place they must
    actually appear: the real invocation. Asserts there is EXACTLY one such
    block -- zero means the gate was gutted/de-fenced, more than one means
    an ambiguous doc edit neither of which should pass silently."""
    window = text[start:end]
    candidates = [m.group(1) for m in FENCE_RE.finditer(window) if MANDATORY_COMMAND_MARKER in m.group(1)]
    assert len(candidates) == 1, (
        f"expected exactly one fenced code block invoking {MANDATORY_COMMAND_MARKER!r} "
        f"in this window, found {len(candidates)}"
    )
    return candidates[0]


def _joined_command(block: str) -> str:
    """Joins a shell `\\`-newline line continuation into one logical command
    string, so a multiline invocation (script line + flags line) is checked
    as the single command it actually is, not as two independent facts."""
    return re.sub(r"\\\s*\n\s*", " ", block)


def test_skill_md_mandatory_gate_sits_after_both_w3_branches_before_w3a():
    text = _skill_text()

    no_new_candidates_offset = text.find(NO_NEW_CANDIDATES_MARKER)
    glossary_branch_offset = text.find(GLOSSARY_PASS_BRANCH_MARKER)
    w3a_offset = text.find(W3A_HEADING_MARKER)

    assert no_new_candidates_offset != -1, "SKILL.md no longer describes the no_new_candidates SKIP branch"
    assert glossary_branch_offset != -1, "SKILL.md no longer describes the codex-glossary-pass branch"
    assert w3a_offset != -1, "SKILL.md no longer has a W3a Segpack generation heading"

    # The mandatory gate must be invoked AFTER a point strictly following
    # BOTH rejoin branches. Anchor on `--particle-config`, not the bare
    # `canon_adjudication_audit.py --check` substring -- that substring
    # ALREADY appears in this window pre-change (the categories-1-4
    # opt-in-gate paragraph also lives between the W3 branches and W3a, it
    # is just semantically meant for Deliver-time), so it would pass even
    # unwired. `--particle-config` is new to this window: the pre-existing
    # opt-in-gate invocation never threads a language config.
    mandatory_offset = text.find(PARTICLE_CONFIG_FLAG, glossary_branch_offset)
    assert mandatory_offset != -1, (
        "SKILL.md never invokes canon_adjudication_audit.py --check with "
        "--particle-config between the W3-rejoin branches and W3a -- the "
        "mandatory gate is unwired"
    )

    assert no_new_candidates_offset < mandatory_offset, (
        "the mandatory gate's invocation must be described after the "
        "no_new_candidates SKIP branch"
    )
    assert glossary_branch_offset < mandatory_offset, (
        "the mandatory gate's invocation must be described after the "
        "codex-glossary-pass branch"
    )
    assert mandatory_offset < w3a_offset, (
        "the mandatory gate's invocation must be described strictly before "
        "W3a Segpack generation -- a split must never reach segpack unverified"
    )


def test_skill_md_mandatory_gate_command_carries_particle_config_and_advisory():
    text = _skill_text()

    glossary_branch_offset = text.find(GLOSSARY_PASS_BRANCH_MARKER)
    w3a_offset = text.find(W3A_HEADING_MARKER)
    assert glossary_branch_offset != -1 and w3a_offset != -1

    # Finding 6: checking the three tokens independently ANYWHERE in this
    # window is vacuously satisfiable -- the opt-in categories-1-4 paragraph
    # (which also lives in this window, see the ordering test above) already
    # mentions MANDATORY_COMMAND_MARKER as an inline code span, so a gutted
    # mandatory command (its script line replaced by a decoy, e.g. `true \`,
    # while the `--particle-config ... --advisory` continuation line
    # survives) would still pass. Extract the ONE fenced code block that
    # actually invokes the marker and assert the flags belong to that SAME
    # joined command.
    block = _mandatory_command_block(text, glossary_branch_offset, w3a_offset)
    joined = _joined_command(block)
    assert PARTICLE_CONFIG_FLAG in joined, (
        "the mandatory gate's own invocation must pass --particle-config -- "
        "evidence verification cannot run without a resolved language config"
    )
    assert ADVISORY_FLAG in joined, (
        "the mandatory gate's own invocation must pass --advisory -- "
        "otherwise it would also start hard-blocking on the still-opt-in "
        "categories 2-4 this early in the pipeline, which the contract does "
        "not call for"
    )


def test_mandatory_command_matcher_rejects_gutted_decoy():
    """RED-witness for the fix above: proves _mandatory_command_block is not
    fooled by a decoy where the mandatory command's own script token is
    replaced (e.g. by `true \\`) while the --particle-config/--advisory
    continuation line survives untouched -- the exact failure mode the old
    "each token appears somewhere in the window" check would have missed."""
    gutted_fragment = (
        "Otherwise run the codex-glossary-pass, filler filler filler.\n\n"
        "```\n"
        "true \\\n"
        "  --particle-config <particle_config's literal value> --advisory\n"
        "```\n\n"
        "**W3a Segpack generation** filler filler.\n"
    )
    with pytest.raises(AssertionError, match="found 0"):
        _mandatory_command_block(gutted_fragment, 0, len(gutted_fragment))


def test_mandatory_command_matcher_accepts_a_genuine_joined_invocation():
    """Positive control for the matcher itself (isolated from the real
    SKILL.md prose): a well-formed fenced block naming the mandatory command
    together with both flags on a continuation line is accepted and its
    flags are found in the joined command string."""
    good_fragment = (
        "Otherwise run the codex-glossary-pass, filler filler filler.\n\n"
        "```\n"
        f"python3 ${{durable_root}}/scripts/{MANDATORY_COMMAND_MARKER} \\\n"
        "  --particle-config <particle_config's literal value> --advisory\n"
        "```\n\n"
        "**W3a Segpack generation** filler filler.\n"
    )
    block = _mandatory_command_block(good_fragment, 0, len(good_fragment))
    joined = _joined_command(block)
    assert PARTICLE_CONFIG_FLAG in joined
    assert ADVISORY_FLAG in joined


def test_skill_md_no_longer_claims_gate_is_opt_in_only():
    text = _skill_text()
    assert STALE_NOT_MANDATORY_CLAIM not in text, (
        "SKILL.md still claims the split-evidence gate is 'not yet wired as "
        "a mandatory W-step' -- stale prose left over from before the "
        "mandatory wiring landed"
    )


def test_orchestration_doc_has_mandatory_gate_bullet_between_w3_and_w3a():
    text = _orchestration_text()

    w3_bootstrap_offset = text.find("W3 Bootstrap")
    w3a_offset = text.find(W3A_HEADING_MARKER)
    assert w3_bootstrap_offset != -1, "orchestration-and-batching.md no longer has a W3 Bootstrap bullet"
    assert w3a_offset != -1, "orchestration-and-batching.md no longer has a W3a Segpack generation bullet"
    assert w3_bootstrap_offset < w3a_offset

    window = text[w3_bootstrap_offset:w3a_offset]
    assert MANDATORY_COMMAND_MARKER in window, (
        "orchestration-and-batching.md has no bullet between W3 Bootstrap and "
        "W3a Segpack generation invoking canon_adjudication_audit.py --check "
        "-- the mandatory gate is undocumented at the orchestration level"
    )
    assert PARTICLE_CONFIG_FLAG in window, (
        "orchestration-and-batching.md's mandatory-gate bullet must name "
        "--particle-config"
    )


# ---------------------------------------------------------------------------
# #727 -- the THIRD W3-rejoin branch (glossary.enabled: false), alongside the
# no_new_candidates SKIP and codex-glossary-pass branches above: must sit
# strictly after the mandatory language smoke test (name detection stays
# load-bearing on this branch too, so it can never be documented as
# skippable alongside the glossary itself) and strictly before the mandatory
# gate's own invocation, which itself must still precede W3a.
# ---------------------------------------------------------------------------


def _assert_disabled_branch_ordering(text: str) -> None:
    """Shared assertion logic, factored out so the mutation-check test below
    can exercise the SAME check against a scratch-mutated copy of SKILL.md's
    text -- never the real file on disk."""
    smoke_test_offset = text.find(LANGUAGE_SMOKE_TEST_MARKER)
    disabled_branch_offset = text.find(DISABLED_BRANCH_MARKER)
    w3a_offset = text.find(W3A_HEADING_MARKER)

    assert smoke_test_offset != -1, "SKILL.md no longer describes the mandatory language smoke test"
    assert disabled_branch_offset != -1, "SKILL.md no longer documents the glossary.enabled: false branch"
    assert w3a_offset != -1, "SKILL.md no longer has a W3a Segpack generation heading"

    mandatory_offset = text.find(PARTICLE_CONFIG_FLAG, disabled_branch_offset)
    assert mandatory_offset != -1, (
        "SKILL.md never invokes canon_adjudication_audit.py --check with "
        "--particle-config after the glossary.enabled: false branch"
    )

    assert smoke_test_offset < disabled_branch_offset, (
        "the glossary.enabled: false branch must be documented AFTER the "
        "mandatory language smoke test -- name detection must stay "
        "load-bearing on this branch, never skippable alongside the glossary"
    )
    assert disabled_branch_offset < mandatory_offset, (
        "the glossary.enabled: false branch must be documented BEFORE the "
        "mandatory homonym-split evidence gate's own invocation"
    )
    assert mandatory_offset < w3a_offset, (
        "the mandatory gate's invocation must be described strictly before "
        "W3a Segpack generation -- a split must never reach segpack unverified"
    )

    # The ordering checks above are satisfiable by COINCIDENCE: --particle-config
    # appears exactly once in the whole document (the mandatory gate's own
    # invocation, further below), so it always sits after any earlier offset
    # regardless of whether THIS branch's own prose actually says it rejoins
    # there. Pin that the branch's own paragraph explicitly says so.
    window = text[disabled_branch_offset:mandatory_offset]
    assert REJOIN_PHRASE_MARKER in window, (
        "the glossary.enabled: false branch's own prose must explicitly say "
        f"it must {REJOIN_PHRASE_MARKER} -- otherwise the ordering checks "
        "above could pass by coincidence, since the gate's one invocation "
        "sits further down in the document regardless of whether this "
        "branch actually points to it"
    )


def test_skill_md_disabled_branch_sits_between_smoke_test_and_mandatory_gate():
    _assert_disabled_branch_ordering(_skill_text())


def test_disabled_branch_ordering_check_goes_red_when_rejoin_sentence_deleted():
    """Mutation check (never touches the real file, per the shared #727
    contract's own instruction): deletes the disabled branch's own rejoin
    sentence from a SCRATCH COPY of SKILL.md's text and confirms
    ``_assert_disabled_branch_ordering`` goes red for the RIGHT reason -- the
    rejoin-phrase assertion above, not an unrelated AttributeError or a
    find() returning -1 for some other marker entirely."""
    text = _skill_text()
    rejoin_sentence = (
        "then rejoin at the mandatory homonym-split evidence gate below, "
        "exactly\nlike every other branch."
    )
    assert rejoin_sentence in text, (
        "could not locate the disabled branch's own rejoin sentence to "
        "mutate -- update this test's literal transcription to match "
        "SKILL.md's current wording before trusting this mutation check"
    )
    mutated = text.replace(rejoin_sentence, "", 1)
    assert rejoin_sentence not in mutated  # sanity: the mutation actually took

    with pytest.raises(AssertionError, match=re.escape(REJOIN_PHRASE_MARKER)):
        _assert_disabled_branch_ordering(mutated)


# ---------------------------------------------------------------------------
# #727 codex review round -- the ordering/rejoin pins above cannot fail on
# three further things they were never built to catch: whether the branch's
# own prose actually names its CONDITION (rather than merely being findable
# by a marker substring an editor could detach from any real condition), or
# names what it forbids INSIDE its own block, or whether the mandatory
# gate's own authoritative summary paragraph enumerates all three branches
# rather than the pre-existing two.
# ---------------------------------------------------------------------------

# The branch's own heading, which doubles as its start-of-block anchor -- so
# a bounded-window search below can never be satisfied by unrelated prose
# elsewhere in the document.
DISABLED_BRANCH_HEADING_MARKER = "glossary.enabled: false` — the project declared it wants no researched"
# The very next branch's own opening clause -- the disabled branch's block
# ends exactly here.
NEXT_BRANCH_START_MARKER = "Otherwise (`glossary.enabled` not false, the default)"

FORBIDDEN_ON_DISABLED_BRANCH = (
    "glossary_batch_plan.py",
    "resume_setup.py",
    "glossary Workflow",
    "canon merge",
    "skeptic pass",
)


def test_skill_md_disabled_branch_names_its_condition_and_forbidden_operations():
    """(a) the branch's own prose must literally name `glossary.enabled:
    false` as the CONDITION that selects it -- not merely be a block a test
    can locate by an arbitrary marker while the actual prose never states
    what selects it. (b) all FIVE operations the branch forbids
    (`glossary_batch_plan.py`, `resume_setup.py`, the glossary Workflow, the
    canon merge, and the skeptic pass) must be named INSIDE the branch's own
    bounded block -- bounded between this branch's own heading and the NEXT
    branch's opening clause, never the whole document, so an unrelated
    mention of e.g. "skeptic pass" in the skeptic-pass section further below
    cannot satisfy this pin."""
    text = _skill_text()
    block_start = text.find(DISABLED_BRANCH_HEADING_MARKER)
    assert block_start != -1, "SKILL.md no longer has the glossary.enabled: false branch's own heading"
    block_end = text.find(NEXT_BRANCH_START_MARKER, block_start)
    assert block_end != -1, (
        "could not find the next W3 branch's own opening clause after the "
        "disabled branch -- cannot bound the disabled branch's own block"
    )
    block = " ".join(text[block_start:block_end].split())

    assert "glossary.enabled: false" in block, (
        "the glossary.enabled: false branch's own prose must literally name "
        f"its condition, not merely describe an unnamed branch; got:\n{block}"
    )
    for forbidden_op in FORBIDDEN_ON_DISABLED_BRANCH:
        assert forbidden_op in block, (
            f"the glossary.enabled: false branch's own block must name "
            f"{forbidden_op!r} among what it forbids -- checked INSIDE this "
            f"branch's own bounded block, never the whole document, so "
            f"unrelated prose elsewhere cannot satisfy this pin; got:\n{block}"
        )


def _assert_mandatory_gate_paragraph_enumerates_all_three_branches(text: str) -> None:
    """Shared assertion, factored out so the SAME check can be driven against
    an arbitrary SKILL.md revision's text, not only the current file. The
    one caller below drives it against the current file; the pre-#727 red
    (git rev b9b20d6) was verified by hand, driving this same function
    against that revision's text directly -- see that test's own docstring
    for the exact command and output. Not worth a permanent test of its own:
    a test that pins a historical git revision's text is not worth its
    maintenance."""
    heading_offset = text.find(MANDATORY_GATE_HEADING)
    assert heading_offset != -1, "SKILL.md no longer has the mandatory homonym-split gate heading"
    fence_offset = text.find("```", heading_offset)
    assert fence_offset != -1, "could not find the mandatory gate's own fenced command block"

    paragraph = " ".join(text[heading_offset:fence_offset].split())

    assert "glossary.enabled: false" in paragraph, (
        "the mandatory gate's own enumeration paragraph must name the "
        "glossary.enabled: false disabled branch as one of the THREE "
        f"W3-rejoin branches it runs after; got:\n{paragraph}"
    )
    assert '{"no_new_candidates": true, "batches": []}' in paragraph, (
        "the mandatory gate's own enumeration paragraph must still name the "
        f"no_new_candidates SKIP branch; got:\n{paragraph}"
    )
    assert "codex-glossary-pass" in paragraph, (
        "the mandatory gate's own enumeration paragraph must still name the "
        f"codex-glossary-pass branch; got:\n{paragraph}"
    )
    assert "three" in paragraph.lower(), (
        "the paragraph must say it runs after all THREE branches, not still "
        f"count 'both' (two) while merely listing three names; got:\n{paragraph}"
    )


def test_mandatory_gate_paragraph_enumerates_all_three_rejoin_branches():
    """#727 codex round MAJOR: this paragraph is the mandatory gate's own
    authoritative summary of when it runs -- before the fix it still said
    "immediately after **both** W3-rejoin branches", naming only the
    pre-existing SKIP and codex-glossary-pass paths, even once the third
    (`glossary.enabled: false`) branch was wired in above to rejoin here too.
    An operator reading only this summary (not the individual branches
    above it) would believe the gate runs after just two of the three ways
    W3 can reach it.

    Verified RED against the pre-#727 SKILL.md (git rev b9b20d6, before this
    branch's own changes): that revision's paragraph reads "immediately
    after **both** W3-rejoin branches above -- the `{"no_new_candidates":
    true, "batches": []}` SKIP path and the "Otherwise run the
    codex-glossary-pass" path alike" -- which fails this exact assertion
    for the right reason (the disabled-branch marker and the word "three"
    are both genuinely absent, not a fixture typo), confirmed by loading
    that revision's text directly and driving it through this same
    function."""
    _assert_mandatory_gate_paragraph_enumerates_all_three_branches(_skill_text())


if __name__ == "__main__":
    import sys

    sys.exit(pytest.main([__file__, "-v"]))
