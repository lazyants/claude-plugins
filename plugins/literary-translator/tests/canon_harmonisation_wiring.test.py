"""tests/canon_harmonisation_wiring.test.py

Issue #823: nothing anywhere compares `canonical_target_form` ACROSS canon
entries, so a canon can freeze with one referent under two target spellings
and every shipped gate still reports success. The fix is a new, always-runs,
advisory W-step -- "the canon target-harmonisation read" -- inserted
immediately after the mandatory homonym-split evidence gate (category 5) and
before the skeptic pass / W3a: it serialises the WHOLE of `canon.json`'s
`entries{}` plus a digest of its bytes into one dispatch, fires one
schema-less codex pass asking whether any two target forms denote one
referent, checks the attempt with `canon_harmonisation.py --check
--approve-to`, and (only on that pass) renders it with `--report`.

This is a doc-structural test, not a behavioral one -- the new script's own
logic (schema validation, the canon-anchor digest, byte-exact membership,
anti-fabrication, `--approve-to` publish-on-pass-only) is covered by
`tests/canon_harmonisation.test.py`. What is NOT covered anywhere else is
whether `SKILL.md` and the two reference docs actually tell an operator to
run the step at the right point, with the right shape -- the standalone
script tests can all be green while the step itself is never invoked, or is
invoked with the durable sidecar substituted for the per-attempt path, and
every other gate would still report clean (the exact failure mode
`tests/mandatory_split_audit_wiring.test.py` was built to catch for the
sibling gate; this file mirrors its structure and its two disciplines).

So this file greps the shipped docs directly. Every assertion below is made
inside ONE BOUNDED WINDOW -- the new W-step's own block, delimited by its
own heading and the skeptic-pass heading that follows it -- because a bare
document-order offset check is satisfiable by coincidence (see
`mandatory_split_audit_wiring.test.py`'s own comment on this, and assertion
2 below's docstring for a fresh example specific to this file). The
mandatory gate's own invocation PRECEDES this window and is therefore not
one of its delimiters; the window's start offset is compared against it
separately, in assertion 1.

Assertions:

  1. PLACEMENT: the window opens strictly after the mandatory gate's own
     `--particle-config` invocation and closes strictly before the
     `W3a Segpack generation` heading. Branch coverage needs no assertion
     of its own here: `tests/mandatory_split_audit_wiring.test.py` already
     pins all three W3-rejoin branches to converge on that gate, so
     anything placed after it is reached by every branch by construction.
  2. INPUT: inside the window, the instruction to serialise the WHOLE
     `entries{}` into the dispatch prompt, AND the instruction to supply
     the sha256 of `canon.json`'s bytes beside it.
  3. SEQUENCE: inside the window, in order -- exactly one dispatch, a
     bounded WAIT, then `canon_harmonisation.py --check`, then
     `canon_harmonisation.py --report`.
  4. THE CHECK COMMAND ITSELF, extracted from the window (the one fenced
     code block that itself invokes the marker, never the bare token
     matched anywhere in the window -- see `_check_command_block`'s own
     docstring): it must carry the PER-ATTEMPT path, never the durable
     sidecar, AND the exact `--approve-to
     ${durable_root}/canon_harmonisation.json`.
  5. BOTH QUESTIONS (divergent spelling, divergent transliteration policy)
     are present inside the window.
  6. NON-BLOCKING DISPOSITION: inside the window, the explicit
     continues-to-skeptic-pass/W3a statement, and the ABSENCE of the word
     HALTING from the window.
  7. DOC AGREEMENT: `references/orchestration-and-batching.md` carries the
     equivalent bullet strictly between its "W3 Bootstrap" and
     "W3a Segpack generation" bullets.
  8. SKEPTIC ENTRY POINT: `references/skeptic-pass.md` names the
     harmonisation W-step, not the mandatory homonym-split gate, as what
     the skeptic pass runs immediately after -- otherwise the enabled path
     could freeze `canon.json`'s bytes before a proposal-driven `--correct`
     is applied.

Every one of the eight is mutation-tested against a SCRATCH COPY of the doc
text (never the real file on disk), each asserting the matcher goes red for
the RIGHT reason -- the specific assertion, not an unrelated AttributeError
or a `find()` returning -1 for some other marker.

Collection note: like every ``*.test.py`` file in this suite, pytest's
default "prepend" import mode cannot resolve this dotted module name --
run with
``python3 -m pytest --import-mode=importlib tests/canon_harmonisation_wiring.test.py``.
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
SKEPTIC_PATH = PLUGIN_ROOT / "skills" / "literary-translator" / "references" / "skeptic-pass.md"

assert SKILL_PATH.is_file(), f"SKILL.md not found at {SKILL_PATH}"
assert ORCHESTRATION_PATH.is_file(), f"orchestration-and-batching.md not found at {ORCHESTRATION_PATH}"
assert SKEPTIC_PATH.is_file(), f"skeptic-pass.md not found at {SKEPTIC_PATH}"

# ---------------------------------------------------------------------------
# Markers. Every one of these is verified (by hand, against the shipped
# files) to sit entirely within a SINGLE source line -- this repo hand-wraps
# prose at ~72-78 columns, so a marker spanning a wrap boundary would need
# whitespace-normalization to match, and normalizing would corrupt the
# fenced-code-block extraction used for assertion 4. Keeping every marker
# single-line-safe lets every assertion below use a plain, unnormalized
# substring/offset search throughout.
# ---------------------------------------------------------------------------

HARMONISATION_HEADING = "**Canon target-harmonisation read (always runs, advisory)**"
SKEPTIC_HEADING = "**Skeptic pass (RFC #215 Phase 2, opt-in + advisory)**"
W3A_HEADING_MARKER = "W3a Segpack generation"
PARTICLE_CONFIG_FLAG = "--particle-config"
# The mandatory gate's own heading -- used only by the "moved before the
# gate" mutation below, to compute a splice point that is guaranteed to sit
# before PARTICLE_CONFIG_FLAG's one occurrence.
MANDATORY_GATE_HEADING = "Mandatory homonym-split evidence gate (category 5, always runs)"

SERIALISE_MARKER = "serialises the WHOLE of `canon.json`'s `entries{}`"
DIGEST_MARKER = "the sha256 of `canon.json`'s exact bytes"

DISPATCH_MARKER = "agentType:'codex:codex-rescue'"
WAIT_MARKER = "A bounded **WAIT**"
CHECK_MARKER = "canon_harmonisation.py --check"
REPORT_MARKER = "canon_harmonisation.py --report"
DURABLE_SIDECAR_PATH = "${durable_root}/canon_harmonisation.json"

QUESTION_ONE_MARKER = "which `canonical_target_form` values denote ONE"
QUESTION_TWO_MARKER = "POLICY diverges, an English exonym such as `of Częstochowa`"

CONTINUES_TO_SKEPTIC_MARKER = "the pipeline continues to the skeptic pass when"
CONTINUES_TO_W3A_MARKER = "otherwise straight to W3a below"
END_OF_DISPOSITION_SENTENCE = "already ran."

ORCH_HEADING_MARKER = "Canon target-harmonisation read"

SKEPTIC_ENTRY_NEW = "immediately after the canon target-harmonisation W-step"
SKEPTIC_ENTRY_OLD = "immediately after the mandatory homonym-split gate"

FENCE_RE = re.compile(r"```(.*?)```", re.DOTALL)
# Mirrors mandatory_split_audit_wiring.test.py's _joined_command: folds a
# shell `\`-newline continuation into one logical line, so a multiline
# invocation is checked as the single command it actually is.
CONTINUATION_RE = re.compile(r"\\\s*\n\s*")


def _skill_text() -> str:
    return SKILL_PATH.read_text(encoding="utf-8")


def _orchestration_text() -> str:
    return ORCHESTRATION_PATH.read_text(encoding="utf-8")


def _skeptic_text() -> str:
    return SKEPTIC_PATH.read_text(encoding="utf-8")


def _joined_command(block: str) -> str:
    return CONTINUATION_RE.sub(" ", block)


# ---------------------------------------------------------------------------
# The window, and the eight assertions. Each `_assert_*` function is shared
# verbatim between the positive test (run against the real files) and the
# corresponding mutation self-test (run against a scratch-mutated copy) --
# a single definition of each check cannot drift between the two callers.
# Every raised AssertionError names its own assertion number so a mutation
# test's `pytest.raises(match=...)` pins the RIGHT failure, not merely *a*
# failure.
# ---------------------------------------------------------------------------


def _window(text: str) -> tuple[int, int]:
    start = text.find(HARMONISATION_HEADING)
    assert start != -1, (
        "ASSERTION 1 (placement): SKILL.md no longer has the canon "
        "target-harmonisation W-step's own heading"
    )
    end = text.find(SKEPTIC_HEADING, start)
    assert end != -1, (
        "ASSERTION 1 (placement): no skeptic-pass heading found after the "
        "harmonisation W-step's own heading"
    )
    return start, end


def _assert_placement(text: str) -> tuple[int, int]:
    start, end = _window(text)

    # Branch coverage needs no assertion of its own here: all three
    # W3-rejoin branches are already pinned to converge on the mandatory
    # gate by tests/mandatory_split_audit_wiring.test.py (its assertions 1
    # and its #727 disabled-branch ordering checks). Anything placed after
    # that gate's own invocation is therefore reached by every branch by
    # construction, and this test only needs to pin THAT placement, not
    # re-derive the convergence.
    particle_offset = text.find(PARTICLE_CONFIG_FLAG)
    assert particle_offset != -1, (
        "ASSERTION 1 (placement): the mandatory gate's own --particle-config "
        "invocation is missing from SKILL.md"
    )
    assert particle_offset < start, (
        "ASSERTION 1 (placement): the harmonisation W-step must open "
        "strictly AFTER the mandatory gate's own --particle-config "
        "invocation, not before it"
    )

    w3a_offset = text.find(W3A_HEADING_MARKER)
    assert w3a_offset != -1, (
        "ASSERTION 1 (placement): SKILL.md no longer has a W3a Segpack "
        "generation heading"
    )
    assert end <= w3a_offset, (
        "ASSERTION 1 (placement): the harmonisation W-step's window must "
        "close at or before W3a Segpack generation"
    )
    return start, end


def _assert_input(text: str) -> None:
    start, end = _window(text)
    window = text[start:end]
    assert SERIALISE_MARKER in window, (
        "ASSERTION 2 (input): the window no longer instructs the session "
        "to serialise the WHOLE entries{} into the dispatch prompt -- "
        "without this the step degenerates into a pass that reads whatever "
        "it likes"
    )
    assert DIGEST_MARKER in window, (
        "ASSERTION 2 (input): the window no longer instructs the session "
        "to supply the sha256 of canon.json's bytes beside the serialised "
        "entries -- without this canon_sha256 has nothing trustworthy to "
        "anchor against"
    )


def _assert_sequence(text: str) -> None:
    start, end = _window(text)
    window = text[start:end]
    dispatch_count = window.count(DISPATCH_MARKER)
    assert dispatch_count == 1, (
        "ASSERTION 3 (sequence): expected exactly one codex:codex-rescue "
        f"dispatch marker in the window, found {dispatch_count}"
    )
    dispatch_offset = window.find(DISPATCH_MARKER)
    wait_offset = window.find(WAIT_MARKER)
    check_offset = window.find(CHECK_MARKER)
    report_offset = window.find(REPORT_MARKER)
    assert wait_offset != -1, (
        "ASSERTION 3 (sequence): the bounded WAIT is missing from the "
        "window -- deleting it leaves the check racing an unfinished job"
    )
    assert check_offset != -1, (
        "ASSERTION 3 (sequence): canon_harmonisation.py --check is missing "
        "from the window"
    )
    assert report_offset != -1, (
        "ASSERTION 3 (sequence): canon_harmonisation.py --report is "
        "missing from the window"
    )
    assert dispatch_offset < wait_offset, (
        "ASSERTION 3 (sequence): the dispatch must precede the WAIT"
    )
    assert wait_offset < check_offset, (
        "ASSERTION 3 (sequence): the WAIT must precede --check"
    )
    assert check_offset < report_offset, (
        "ASSERTION 3 (sequence): --check must precede --report -- the "
        "report is only rendered after the check has passed"
    )


def _check_command_block(text: str, start: int, end: int) -> str:
    """Extracts the ONE fenced code block inside text[start:end] that
    itself invokes CHECK_MARKER -- mirrors
    mandatory_split_audit_wiring.test.py's own `_mandatory_command_block`
    and its stated reason: asserting the bare token appears SOMEWHERE in
    the window is satisfiable by an unrelated mention (this window's own
    prose names `canon_harmonisation.py` several times outside any fenced
    block), so a gutted or decoy check command could still leave every
    other assertion here green. Anchoring on "a fenced block that itself
    contains the marker" ties the flags checked below to the one place
    they must actually appear."""
    window = text[start:end]
    candidates = [m.group(1) for m in FENCE_RE.finditer(window) if CHECK_MARKER in m.group(1)]
    assert len(candidates) == 1, (
        "ASSERTION 4 (check command): expected exactly one fenced code "
        f"block invoking {CHECK_MARKER!r} in the window, found {len(candidates)}"
    )
    return candidates[0]


def _assert_check_command(text: str) -> None:
    start, end = _window(text)
    block = _check_command_block(text, start, end)
    joined = _joined_command(block)

    m = re.search(r"--check\s+(\S.*?)\s+--approve-to", joined)
    assert m, (
        "ASSERTION 4 (check command): could not find a --check argument "
        "followed by --approve-to in the check command -- "
        "asserting only the bare token --check would leave the whole "
        "attempt-then-publish design unlocked"
    )
    check_arg = m.group(1)
    assert check_arg != DURABLE_SIDECAR_PATH, (
        "ASSERTION 4 (check command): the --check argument must be the "
        "PER-ATTEMPT path, never the durable sidecar -- checking the "
        "durable sidecar as though it were the freshly dispatched attempt "
        "would let the session validate an attempt and then report an "
        "absent or PREVIOUS run's durable artifact, with every other "
        "assertion and every script test still green"
    )
    assert f"--approve-to {DURABLE_SIDECAR_PATH}" in joined, (
        "ASSERTION 4 (check command): the check command must carry the "
        f"exact --approve-to {DURABLE_SIDECAR_PATH!r}"
    )


def _assert_questions(text: str) -> None:
    start, end = _window(text)
    window = text[start:end]
    assert QUESTION_ONE_MARKER in window, (
        "ASSERTION 5 (questions): the divergent-spelling question (which "
        "canonical_target_form values denote one referent spelled two "
        "ways) is missing from the window"
    )
    assert QUESTION_TWO_MARKER in window, (
        "ASSERTION 5 (questions): the divergent-transliteration-policy "
        "question is missing from the window"
    )


def _assert_non_blocking(text: str) -> None:
    start, end = _window(text)
    window = text[start:end]
    assert CONTINUES_TO_SKEPTIC_MARKER in window, (
        "ASSERTION 6 (non-blocking): the window no longer states that a "
        "failed check continues forward to the skeptic pass"
    )
    assert CONTINUES_TO_W3A_MARKER in window, (
        "ASSERTION 6 (non-blocking): the window no longer names W3a as the "
        "forward continuation when the skeptic pass is disabled"
    )
    assert "HALTING" not in window, (
        "ASSERTION 6 (non-blocking): the word HALTING must never appear in "
        "this advisory, non-blocking W-step's own window -- this step is "
        "explicitly not a gate"
    )


def _assert_orchestration_bullet(text: str) -> None:
    w3_offset = text.find("W3 Bootstrap")
    w3a_offset = text.find(W3A_HEADING_MARKER)
    assert w3_offset != -1, (
        "ASSERTION 7 (doc agreement): orchestration-and-batching.md no "
        "longer has a W3 Bootstrap bullet"
    )
    assert w3a_offset != -1, (
        "ASSERTION 7 (doc agreement): orchestration-and-batching.md no "
        "longer has a W3a Segpack generation bullet"
    )
    assert w3_offset < w3a_offset

    window = text[w3_offset:w3a_offset]
    assert ORCH_HEADING_MARKER in window, (
        "ASSERTION 7 (doc agreement): orchestration-and-batching.md has no "
        "Canon target-harmonisation bullet between W3 Bootstrap and W3a "
        "Segpack generation -- the mandatory step is undocumented at the "
        "orchestration level"
    )
    assert CHECK_MARKER in window, (
        "ASSERTION 7 (doc agreement): the orchestration bullet does not "
        f"name {CHECK_MARKER!r}"
    )


def _assert_skeptic_entry_point(text: str) -> None:
    assert SKEPTIC_ENTRY_NEW in text, (
        "ASSERTION 8 (skeptic entry point): skeptic-pass.md does not say "
        "it runs immediately after the canon target-harmonisation W-step"
    )
    assert SKEPTIC_ENTRY_OLD not in text, (
        "ASSERTION 8 (skeptic entry point): skeptic-pass.md still names "
        "the mandatory homonym-split gate as its entry point -- a "
        "proposal-driven --correct applied between the two W-steps would "
        "then land AFTER the skeptic pass had already frozen canon.json's "
        "bytes"
    )


# ---------------------------------------------------------------------------
# Positive tests -- run each shared assertion against the real, current docs.
# ---------------------------------------------------------------------------


def test_placement():
    _assert_placement(_skill_text())


def test_input():
    _assert_input(_skill_text())


def test_sequence():
    _assert_sequence(_skill_text())


def test_check_command():
    _assert_check_command(_skill_text())


def test_questions():
    _assert_questions(_skill_text())


def test_non_blocking_disposition():
    _assert_non_blocking(_skill_text())


def test_orchestration_bullet_agreement():
    _assert_orchestration_bullet(_orchestration_text())


def test_skeptic_entry_point():
    _assert_skeptic_entry_point(_skeptic_text())


# ---------------------------------------------------------------------------
# Mutation self-tests. Every one perturbs a SCRATCH COPY of the doc text --
# never the real file -- and asserts the matching `_assert_*` function goes
# red FOR THE RIGHT REASON (the named assertion, via `match=`), not an
# unrelated AttributeError or an unindexed find() elsewhere.
# ---------------------------------------------------------------------------


def test_mutation_delete_whole_block_goes_red():
    text = _skill_text()
    start, end = _window(text)
    mutated = text[:start] + text[end:]
    with pytest.raises(AssertionError, match="ASSERTION 1"):
        _assert_placement(mutated)


def test_mutation_move_block_before_mandatory_gate_goes_red():
    """Cuts the whole harmonisation block out of its shipped position and
    splices it back in immediately before the mandatory gate's own
    heading -- i.e. moved to run BEFORE the gate it must actually follow.
    gate_heading_offset is computed against the ORIGINAL text but stays
    valid as an index into `without_block`, since it is < start and only
    content AFTER start was removed."""
    text = _skill_text()
    start, end = _window(text)
    block = text[start:end]
    gate_heading_offset = text.find(MANDATORY_GATE_HEADING)
    assert gate_heading_offset != -1, "could not locate the mandatory gate's own heading to splice against"
    assert gate_heading_offset < start, "the mandatory gate heading must precede the harmonisation window in the real file"

    without_block = text[:start] + text[end:]
    mutated = without_block[:gate_heading_offset] + block + without_block[gate_heading_offset:]

    with pytest.raises(AssertionError, match="ASSERTION 1"):
        _assert_placement(mutated)


def test_mutation_delete_serialise_instruction_goes_red():
    text = _skill_text()
    assert SERIALISE_MARKER in text, "could not locate the serialise-entries marker to mutate"
    mutated = text.replace(SERIALISE_MARKER, "", 1)
    assert SERIALISE_MARKER not in mutated
    with pytest.raises(AssertionError, match="ASSERTION 2"):
        _assert_input(mutated)


def test_mutation_delete_digest_instruction_goes_red():
    text = _skill_text()
    assert DIGEST_MARKER in text, "could not locate the canon-digest marker to mutate"
    mutated = text.replace(DIGEST_MARKER, "", 1)
    assert DIGEST_MARKER not in mutated
    with pytest.raises(AssertionError, match="ASSERTION 2"):
        _assert_input(mutated)


def test_mutation_delete_wait_goes_red():
    text = _skill_text()
    assert WAIT_MARKER in text, "could not locate the WAIT marker to mutate"
    mutated = text.replace(WAIT_MARKER, "", 1)
    assert WAIT_MARKER not in mutated
    with pytest.raises(AssertionError, match="ASSERTION 3"):
        _assert_sequence(mutated)


def test_mutation_swap_check_and_report_goes_red():
    """Swaps the CONTENT of the two fenced code blocks in the window (via
    their FENCE_RE match spans, never a hand-transcribed literal string, so
    this is robust to the blocks' exact whitespace/indentation) so the
    --check invocation now sits where --report used to and vice versa."""
    text = _skill_text()
    start, end = _window(text)
    window = text[start:end]
    matches = list(FENCE_RE.finditer(window))
    assert len(matches) == 2, f"expected exactly 2 fenced blocks in the window, found {len(matches)}"
    check_matches = [m for m in matches if CHECK_MARKER in m.group(1)]
    report_matches = [m for m in matches if REPORT_MARKER in m.group(1)]
    assert len(check_matches) == 1 and len(report_matches) == 1
    m_check, m_report = check_matches[0], report_matches[0]
    assert m_check is not m_report

    first, second = sorted((m_check, m_report), key=lambda m: m.start(1))
    new_window = (
        window[: first.start(1)]
        + second.group(1)
        + window[first.end(1) : second.start(1)]
        + first.group(1)
        + window[second.end(1) :]
    )
    mutated = text[:start] + new_window + text[end:]

    with pytest.raises(AssertionError, match="ASSERTION 3"):
        _assert_sequence(mutated)


def _find_check_fence_span(text: str) -> tuple[int, int, str]:
    """Returns the (absolute_start, absolute_end, content) of the check
    fenced block's inner content, as absolute offsets into `text` -- lets a
    mutation replace exactly that span without needing to hand-transcribe
    its surrounding whitespace/backslash-continuation."""
    start, end = _window(text)
    window = text[start:end]
    candidates = [m for m in FENCE_RE.finditer(window) if CHECK_MARKER in m.group(1)]
    assert len(candidates) == 1
    m = candidates[0]
    return start + m.start(1), start + m.end(1), m.group(1)


def test_mutation_delete_approve_to_goes_red():
    text = _skill_text()
    abs_start, abs_end, _block = _find_check_fence_span(text)
    new_block = "python3 ${durable_root}/scripts/canon_harmonisation.py --check <the attempt path from step 2>"
    mutated = text[:abs_start] + new_block + text[abs_end:]
    assert "--approve-to" not in _joined_command(new_block)
    with pytest.raises(AssertionError, match="ASSERTION 4"):
        _assert_check_command(mutated)


def test_mutation_check_arg_is_durable_sidecar_goes_red():
    text = _skill_text()
    abs_start, abs_end, _block = _find_check_fence_span(text)
    new_block = (
        f"python3 ${{durable_root}}/scripts/canon_harmonisation.py --check {DURABLE_SIDECAR_PATH} "
        f"--approve-to {DURABLE_SIDECAR_PATH}"
    )
    mutated = text[:abs_start] + new_block + text[abs_end:]
    with pytest.raises(AssertionError, match="ASSERTION 4"):
        _assert_check_command(mutated)


def test_mutation_delete_question_one_goes_red():
    text = _skill_text()
    assert QUESTION_ONE_MARKER in text, "could not locate question 1's marker to mutate"
    mutated = text.replace(QUESTION_ONE_MARKER, "", 1)
    with pytest.raises(AssertionError, match="ASSERTION 5"):
        _assert_questions(mutated)


def test_mutation_delete_continues_to_w3a_sentence_goes_red():
    text = _skill_text()
    start_idx = text.find(CONTINUES_TO_SKEPTIC_MARKER)
    assert start_idx != -1, "could not locate the continues-to-skeptic-pass marker to mutate"
    end_idx = text.find(END_OF_DISPOSITION_SENTENCE, start_idx)
    assert end_idx != -1, "could not locate the end of the failure-disposition sentence"
    end_idx += len(END_OF_DISPOSITION_SENTENCE)
    mutated = text[:start_idx] + text[end_idx:]
    assert CONTINUES_TO_SKEPTIC_MARKER not in mutated
    assert CONTINUES_TO_W3A_MARKER not in mutated
    with pytest.raises(AssertionError, match="ASSERTION 6"):
        _assert_non_blocking(mutated)


def test_mutation_delete_orchestration_bullet_goes_red():
    text = _orchestration_text()
    assert ORCH_HEADING_MARKER in text, "could not locate the orchestration bullet's own heading to mutate"
    mutated = text.replace(ORCH_HEADING_MARKER, "", 1)
    with pytest.raises(AssertionError, match="ASSERTION 7"):
        _assert_orchestration_bullet(mutated)


def test_mutation_revert_skeptic_entry_point_goes_red():
    text = _skeptic_text()
    assert SKEPTIC_ENTRY_NEW in text, "could not locate the skeptic-pass entry-point phrase to mutate"
    mutated = text.replace(SKEPTIC_ENTRY_NEW, SKEPTIC_ENTRY_OLD, 1)
    assert SKEPTIC_ENTRY_NEW not in mutated
    assert SKEPTIC_ENTRY_OLD in mutated
    with pytest.raises(AssertionError, match="ASSERTION 8"):
        _assert_skeptic_entry_point(mutated)


if __name__ == "__main__":
    import sys

    sys.exit(pytest.main([__file__, "-v"]))
