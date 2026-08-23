"""tests/glossary_trap_routing.test.py -- regression-lock for issue #510:
`glossary_TASK.template.md` told the codex glossary agent to "log a genuine
discovery in `style_bible.md`'s own E-traps section". That is the one place
in this plugin where a codex agent was pointed at an authority file instead
of the run-scoped fragment a deterministic merger validates, and E-traps
sits INSIDE the style_contract span, so the append it invited moves
`style_contract_hash` and flips every already-converged segment to `stale`.
The fix reroutes the discovery into the candidate's own `note` (which
`canon_validate.py` merges into `canon.json` like any other accepted field)
and forbids the write outright; promoting a trap into E-traps stays the
operator's step at a batch boundary.

WHY THE OBVIOUS ASSERTION IS NOT USABLE, stated because it was the first
design and it cannot work. A "the file must not mention `style_bible` near
a write verb" check can never go green here: the replacement paragraph has
to name `style_bible.md` and describe the append in order to FORBID it, so
the check would reject the very text it exists to protect. Every assertion
below therefore pins either the ABSENCE of the old directive's own
distinctive wording or the PRESENCE of the new routing, never a generic
verb/filename co-occurrence.

RED BEFORE GREEN, measured rather than assumed. Counts in the file as it
stood before this fix (`grep -ci`): `operator` 0, `style_contract` 0,
`no other file` 0, `trap-string gate` 1, `section instead` 1. So each
assertion below fails on the pre-fix template -- the two absence checks
because their literals were present, the two presence checks because their
anchors appeared nowhere in the file at all. None of them is vacuous.

WHAT A TOKEN BAG DOES NOT CATCH, measured rather than reasoned about. An
earlier revision of this file checked only that the right tokens occurred
near the anchor. A review round mutated the paragraph to `Write no other
file except style_bible.md: append the discovery to its E-traps section`,
moved ownership from the operator back to this pass, and every assertion
still passed -- the inverted rule contains the same tokens as the rule it
inverts. So the two load-bearing clauses are now pinned as EXACT literals
(`SENTENCE_PINS`) and the sentence that grants the write is guarded against
a trailing carve-out, which is what makes that mutant red.

Like `tests/glossary_epithet_rule.test.py`, this is still an honest
DROP-detector, not a semantic-equivalence prover. Its remaining blind spot
is stated rather than implied: a rewrite that keeps both pinned sentences
verbatim and adds a contradicting directive INSIDE the same window still
passes, and an honest reword of either sentence fails and has to be
re-pinned here deliberately. Every match runs against a whitespace-flattened
copy of the file, so the ~79-column hard wrap can neither split a pinned
sentence (a false red on a pure re-wrap) nor hide a re-wrapped copy of the
old directive from an absence check (a false green, the one that matters).
"""
from __future__ import annotations

from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
TASK_SRC = (
    PLUGIN_ROOT
    / "skills"
    / "literary-translator"
    / "assets"
    / "templates"
    / "glossary_TASK.template.md"
)

assert TASK_SRC.is_file(), f"expected plugin template not found: {TASK_SRC}"

TASK_TEXT = TASK_SRC.read_text(encoding="utf-8")

# Every match below runs against a whitespace-FLATTENED copy, because this
# plugin hard-wraps its prose at ~79 columns and a re-wrap is not a reword. On
# the raw text the failure is two-sided: re-flowing the paragraph without
# changing a word puts a newline inside a pinned sentence and turns this file
# red for nothing, and -- the dangerous direction -- a re-wrapped copy of the
# OLD directive slips past the absence checks, which is a false GREEN on the
# one property this file exists for. The raw text is kept for the excerpts the
# failure messages print, so a human still reads the file as it is written.
TASK_FLAT = " ".join(TASK_TEXT.split())

# The anchor for the new routing paragraph. Absent from this file before the
# #510 fix, so a window built on it cannot be satisfied by pre-existing prose.
ROUTING_ANCHOR = "no other file"

# The old directive's own distinctive wording. Both were present exactly once
# before the fix; neither has any other legitimate use in this file.
OLD_DIRECTIVE_LITERALS = (
    "trap-string gate",
    "E-traps section instead",
)

# The two clauses that carry the rule: the prohibition and the ownership of
# the promotion step. Pinned as exact literals because a token-proximity
# check passes on their own inversion (see the docstring). Reworking either
# sentence is a deliberate act that must be re-pinned here.
SENTENCE_PINS = (
    "Write no other file.",
    "In particular never `style_bible.md`",
    "Promoting a trap into E-traps is the operator's own",
)

# A carve-out appended to the prohibition is the inversion this file exists
# to catch, and it survives every token check because it adds tokens rather
# than removing them.
CARVE_OUT_MARKERS = ("except", "other than", "unless", "apart from")
CARVE_OUT_SCAN_CHARS = 200

WINDOW_BEFORE = 600
WINDOW_AFTER = 600


def _routing_window() -> str:
    """A bounded character slice centered on the first occurrence of
    `ROUTING_ANCHOR` (case-insensitive). Bounded so a match proves
    CO-LOCATION with the routing paragraph rather than mere presence
    somewhere in a 300-line file -- `note`, `canon.json` and
    `style_bible.md` all appear elsewhere for unrelated pre-existing
    rules."""
    idx = TASK_FLAT.lower().find(ROUTING_ANCHOR.lower())
    assert idx != -1, (
        f"anchor {ROUTING_ANCHOR!r} not found anywhere in {TASK_SRC.name} -- "
        "the #510 trap-routing paragraph appears to be entirely absent"
    )
    start = max(0, idx - WINDOW_BEFORE)
    end = min(len(TASK_FLAT), idx + len(ROUTING_ANCHOR) + WINDOW_AFTER)
    return TASK_FLAT[start:end]


def _assert_fragments_present(window: str, fragments, *, clause_label: str) -> None:
    missing = [f for f in fragments if f.lower() not in window.lower()]
    assert not missing, (
        f"{TASK_SRC.name}: the #510 {clause_label} is missing distinctive "
        f"fragment(s) {missing!r} within the {ROUTING_ANCHOR!r}-anchored "
        f"window -- the rule appears dropped or reworded away from its "
        f"distinctive wording. Window:\n\n{window}"
    )


@pytest.mark.parametrize("literal", OLD_DIRECTIVE_LITERALS)
def test_old_write_directive_is_gone(literal):
    """The pre-#510 sentence and the rationale that produced it are both
    deleted, not merely surrounded by a correction. Its rationale -- "there
    is no separate trap-string gate for THIS file" -- is exactly the
    reasoning that redirected the write to the file with LESS checking, so
    it goes with the directive."""
    assert literal.lower() not in TASK_FLAT.lower(), (
        f"{TASK_SRC.name} still carries {literal!r} -- the pre-#510 "
        "instruction to log a discovery in style_bible.md's E-traps section "
        "(or the rationale that produced it) is back."
    )


def test_prohibition_present():
    """The paragraph forbids the write and says what the write would cost,
    so a later reader cannot restore it as a harmless convenience."""
    _assert_fragments_present(
        _routing_window(),
        ["style_bible.md", "style_contract", "stale"],
        clause_label="prohibition on writing the authority file",
    )


def test_note_routing_present():
    """The sanctioned destination is named in the same breath as the
    prohibition: the candidate's own `note`, in the run-scoped output
    fragment, carried into `canon.json` by the merge."""
    _assert_fragments_present(
        _routing_window(),
        ["note", "run-scoped", "canon.json"],
        clause_label="note-routing clause",
    )


@pytest.mark.parametrize("sentence", SENTENCE_PINS)
def test_load_bearing_sentence_is_verbatim(sentence):
    """The prohibition and the ownership clause are pinned exactly. A
    proximity check cannot separate `never style_bible.md` from `style_bible.md
    is fine`, so these two are compared byte for byte."""
    assert sentence in TASK_FLAT, (
        f"{TASK_SRC.name} no longer carries the exact clause {sentence!r} -- "
        "the #510 prohibition or the operator-ownership of the E-traps "
        "promotion has been reworded or removed. If the reword is deliberate, "
        "re-pin it here in the same commit."
    )


def test_prohibition_carries_no_carve_out():
    """`Write no other file, except ...` is the mutation that keeps every
    token this file checks while restoring exactly the write #510 removed."""
    idx = TASK_FLAT.find("Write no other file")
    assert idx != -1, f"{TASK_SRC.name}: the prohibition sentence is gone"
    tail = TASK_FLAT[idx : idx + CARVE_OUT_SCAN_CHARS].lower()
    found = [m for m in CARVE_OUT_MARKERS if m in tail]
    assert not found, (
        f"{TASK_SRC.name}: the prohibition on writing any other file is "
        f"followed by carve-out wording {found!r} within "
        f"{CARVE_OUT_SCAN_CHARS} characters -- an exception here is the whole "
        f"defect #510 closed. Text:\n\n{TASK_FLAT[idx : idx + CARVE_OUT_SCAN_CHARS]}"
    )


def test_every_etraps_mention_is_operator_scoped():
    """This file may mention E-traps only as somebody ELSE's step. Any
    occurrence whose window does not name the `operator` is an agent-facing
    E-traps directive again, whatever wording it arrives in -- which is the
    class this lock exists for, not just the one deleted sentence. Before
    the fix the token `operator` appeared nowhere in the file, so the single
    pre-fix occurrence failed this."""
    lowered = TASK_FLAT.lower()
    occurrences = []
    idx = lowered.find("e-traps")
    while idx != -1:
        occurrences.append(idx)
        idx = lowered.find("e-traps", idx + 1)

    for pos in occurrences:
        start = max(0, pos - WINDOW_BEFORE)
        end = min(len(TASK_FLAT), pos + WINDOW_AFTER)
        window = TASK_FLAT[start:end]
        assert "the operator's own" in window.lower(), (
            f"{TASK_SRC.name}: an `E-traps` mention at offset {pos} has no "
            "`the operator's own` within its window -- this file may name "
            "E-traps only as the operator's own batch-boundary step, never as "
            "something this pass does. Window:\n\n" + window
        )
