"""tests/retired_wording_pins.test.py -- the 1.16.2 (#352) retired-wording pins.

WHAT A PIN IS FOR. Some of what #352 changed is not behaviour but CLAIMS: a
comment saying a wait costs three calls per batch, an estimator expression that
renders the old ladder, a prompt line that instructs the pre-1.16.2 poll. Those
survive a rewrite easily -- nothing executes a stale comment -- and the way they
come back is a copy-paste from a neighbouring block or a merge that resurrects a
hunk. A pin is a needle asserted GONE from the file it was retired from.

WHY EVERY ROW IS ASSERTED IN BOTH DIRECTIONS, and why the cheap half is the
useless one.

    absence-after  -- the needle no longer occurs in the current file.
    presence-before -- the needle occurred EXACTLY ONCE at the baseline.

Absence-after alone is worthless, and worse than worthless because it is green.
A needle that never matched anything -- mistyped, mis-wrapped, or simply
invented -- satisfies it forever while proving nothing at all, and from inside a
passing run that is indistinguishable from a pin that is doing its job.
Presence-before is what makes the needle REAL. Exactly-once rather than
at-least-once, because a needle matching several places retires more than the
row claims to be about, and the row's own description then names only one of
them.

This is the same defect shape as an assertion written against a helper's
arithmetic instead of against the bytes a template emits: it passes without ever
exercising the thing it names. See tests/wait_chunking_batch_passes.test.py's
header for that one.

TWO TRAPS, both measured on this change rather than imagined. Each has a
self-test below, because a rule nobody has watched fire is a comment.

  1. A NEEDLE THAT SPANS A LINE BREAK INSIDE A COMMENT BLOCK CONTAINS THE
     LEADER. Whitespace-normalizing a JS comment does not remove the `//` that
     begins each wrapped line, so a sentence a reader sees as

         "...so running it again here is always safe..."

     is really, in normalized text,

         "...so running it // again here is always safe..."

     Typing the needle the way the sentence READS matches zero occurrences --
     measured: 0 at the baseline -- and gives a permanently green row.

  2. A NEEDLE CAN MATCH ITS OWN HISTORICAL QUOTATION. The retired poll is
     narrated in the surviving comments of skeptic-pass-wf.template.js, so a
     bare `for i in $(seq 1 45)` still occurs there ON PURPOSE -- measured: 1 in
     the current file. Pinned bare it would fail absence-after: a FALSE RED
     reporting a regression that is really a deliberate description of one. The
     row is narrowed to the emitted `lines.push(...)` form instead. Same shape
     in the idempotence fix, where the old wording is kept as a quoted
     refutation: `always safe` and `never destructive` are still in the file
     deliberately and must never be pinned bare.

THE AUTHORING RULE THAT FOLLOWS: copy a needle programmatically out of the
NORMALIZED text, never retype it from the rendered comment. The presence-before
assertion is what enforces it -- a retyped needle that lost a `//`, a hyphen or
a doubled space fails loudly instead of passing quietly.

THE BASELINE IS FROZEN. Every presence-before assertion reads a hard-coded
pre-release commit SHA, never HEAD or any other moving ref -- see the comment on
PIN_BASELINE_SHA for why a ref that moves with the thing under test is not a
baseline at all, and why advancing that SHA is how you RETIRE this whole file
rather than how you maintain it.

SCOPE. Templates and scripts only. Lane C's documentation rows are not here:
those files were still being edited when this file was written, and a pin
authored against a moving file measures nothing. They belong in a follow-up row
set, added the same way.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PLUGIN_ROOT.parents[1]
ASSETS = PLUGIN_ROOT / "skills" / "literary-translator" / "assets"

SKEPTIC_TEMPLATE = ASSETS / "templates" / "skeptic-pass-wf.template.js"
GLOSSARY_TEMPLATE = ASSETS / "templates" / "glossary-pass-wf.template.js"
SKEPTIC_SETUP = ASSETS / "scripts" / "skeptic_setup.py"

GIT = shutil.which("git")
pytestmark = pytest.mark.skipif(
    GIT is None,
    reason="git not found on PATH; the presence-before half of every pin reads "
    "the baseline out of git's object store",
)

# ---------------------------------------------------------------------------
# THE BASELINE IS A FROZEN SHA, NEVER A MOVING REF, and that is load-bearing.
#
# `4343994` is the 1.16.1 release merge (#359) -- the last commit BEFORE 1.16.2.
# The retired wording below is what that tree contained.
#
# An earlier draft of this file used "HEAD", which was correct only for as long
# as 1.16.2 stayed uncommitted. That is the defect this comment exists to stop
# anyone reintroducing: the moment the release commits, HEAD becomes the
# POST-change commit, baseline_text() starts returning the NEW text, every
# needle occurs zero times in it, and all eleven presence-before rows go red at
# once -- permanently, and for a purely structural reason. A gate that is red
# for a reason nobody can fix by fixing the code is a gate the next person
# deletes. Measured, not argued: simulating a post-change baseline turns 11/11
# rows red, and the frozen SHA leaves 11/11 green.
#
# The general shape is a check whose expectation is reached through a reference
# that MOVES WITH THE THING UNDER TEST. A baseline that tracks HEAD is not a
# baseline. If you find yourself wanting a symbolic ref here, that is the
# feeling this paragraph is about.
#
# ADVANCING THIS SHA RETIRES THE ENTIRE PIN SET. It is a deliberate act -- the
# statement "these strings are no longer worth pinning" -- and never routine
# maintenance, never a way to make a red row green. A row that has genuinely
# outlived its usefulness is DELETED, with its reason, one row at a time.
PIN_BASELINE_SHA = "4343994b9de4f6fe979e6e5af711ed9ab11c4381"


def normalize(text: str) -> str:
    """Whole-file text with every whitespace run collapsed to one space.

    Wrap-safe: a needle can then be matched across the line break the source
    happens to wrap at, which a line-oriented grep cannot do. It does NOT strip
    comment leaders -- see trap 1 in this module's docstring."""
    return " ".join(text.split())


def git(*args: str) -> subprocess.CompletedProcess:
    # Narrowing, not decoration: pytestmark skips this module when git is
    # absent, but a helper that would call subprocess.run([None, ...]) raises an
    # unreadable TypeError instead of the explanatory failure every other check
    # in this file gives. Fail in this file's own voice.
    assert GIT is not None, (
        "git is not on PATH, so the baseline cannot be read; the presence-before "
        "half of every pin is what makes a row real, and without it this file "
        "would report success while proving nothing"
    )
    return subprocess.run(
        [GIT, "-C", str(REPO_ROOT), *args],
        capture_output=True, text=True, timeout=30, check=False,
    )


def baseline_text(path: Path) -> str:
    rel = path.relative_to(REPO_ROOT).as_posix()
    proc = git("show", f"{PIN_BASELINE_SHA}:{rel}")
    assert proc.returncode == 0, (
        f"could not read {rel} at the frozen baseline {PIN_BASELINE_SHA[:12]}: "
        f"{proc.stderr.strip()}\n"
        f"If the object is simply missing, the branch was rebuilt from a rewritten "
        f"history -- see test_the_pin_baseline_is_frozen_and_still_in_history for "
        f"which of the two this is."
    )
    return normalize(proc.stdout)


def current_text(path: Path) -> str:
    return normalize(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# The rows. Each is (file, needle, what the needle is a claim ABOUT).
#
# The third field is not decoration: an absence assertion whose message only
# says "this string is gone" tells whoever hits it nothing about whether the
# string coming back is a regression or a deliberate quotation.
# ---------------------------------------------------------------------------
RETIRED = [
    # -- skeptic-pass-wf.template.js -------------------------------------
    (
        SKEPTIC_TEMPLATE,
        "but that coercion is idempotent, so running it // again here is always safe, "
        "never destructive",
        "the claim that --validate-fragment is idempotent, which it is not: the "
        "successful run rewrites the fragment in place, so a second run consumes "
        "its own already-pruned output",
    ),
    (
        SKEPTIC_TEMPLATE,
        "safe to run even if it was already run before",
        "the same non-idempotence claim in its short form",
    ),
    (
        SKEPTIC_TEMPLATE,
        "per batch: precheck + dispatch + wait == 3",
        "the pre-#352 per-batch ladder, when a wait was ONE agent call",
    ),
    (
        SKEPTIC_TEMPLATE,
        "const estimatedCalls = 3 * BATCHES.length + 2",
        "the pre-#352 preflight expression itself, now (2 + WAIT_CALLS)*N + 2",
    ),
    (
        SKEPTIC_TEMPLATE,
        'lines.push("for i in $(seq 1 45); do "',
        "the EMITTED pre-#352 poll: 45 iterations x 20 s == 900 s in a single bash "
        "call, against a measured 600 s clamp. Narrowed to the lines.push() form "
        "because the surviving comments narrate the old poll on purpose (trap 2)",
    ),
    (
        SKEPTIC_TEMPLATE,
        "after the timeout (about 15 minutes), return exactly the line: TIMEOUT",
        "the retired TIMEOUT sentinel and the single-shot timeout it implies; a "
        "chunk now reports PENDING, which means 'this chunk learned nothing', not "
        "'the batch is over'",
    ),
    # -- glossary-pass-wf.template.js ------------------------------------
    (
        GLOSSARY_TEMPLATE,
        "live -- perBatch = 1 + 4*(MAX_CITATION_RETRIES+1)",
        "the 1.16.1 live ladder (13N+2), before one wait became WAIT_CALLS calls",
    ),
    (
        GLOSSARY_TEMPLATE,
        "The offline branch is therefore EXACTLY the historical 3*BATCHES.length + 2",
        "the claim that offline keeps its historical figure. What survives #352 is "
        "the LADDER-FREE guarantee, not the number: offline pays 5N+2 now, because "
        "the Bash clamp is indifferent to research_mode",
    ),
    (
        GLOSSARY_TEMPLATE,
        'lines.push("for i in $(seq 1 45); do "',
        "the EMITTED pre-#352 poll, glossary's copy",
    ),
    (
        GLOSSARY_TEMPLATE,
        "15-minute wait poll",
        "the prose name for the single-shot 900 s wait",
    ),
    # -- skeptic_setup.py ------------------------------------------------
    (
        SKEPTIC_SETUP,
        "estimated_calls = 3 * batch_count + 2",
        "the setup script's own copy of the pre-#352 ladder. It refuses a run "
        "BEFORE the Workflow starts, so leaving it behind makes one gate refuse a "
        "batch the other admits, after this run's manifests are already written",
    ),
]

ROW_IDS = [f"{p.name}::{n[:44]}" for p, n, _ in RETIRED]


@pytest.mark.parametrize("path,needle,about", RETIRED, ids=ROW_IDS)
def test_retired_needle_occurred_exactly_once_at_the_baseline(path, needle, about):
    """THE HALF THAT MAKES THE ROW REAL. Without it, a mistyped or mis-wrapped
    needle is green forever and pins nothing."""
    hits = baseline_text(path).count(normalize(needle))
    assert hits == 1, (
        f"the needle for {path.name} matched {hits} time(s) at "
        f"the frozen baseline {PIN_BASELINE_SHA[:12]}, not exactly once.\n"
        f"  needle: {needle!r}\n"
        f"  about:  {about}\n"
        f"If this is 0: the needle is WRONG, not the code -- retype nothing, copy "
        f"it programmatically out of normalize(<baseline text>). What does NOT "
        f"cause this: whitespace. Both sides are normalized, so a needle typed "
        f"with a doubled space or a different line wrap still matches (measured). "
        f"What does: a sentence wrapped inside a // comment block carries the "
        f"leader mid-sentence, and any character-level difference survives "
        f"normalization -- a hyphen for an em dash, a straight quote for a curly "
        f"one, one word rewritten. If this is >1: narrow the needle -- the row "
        f"describes one site and is silently retiring several.\n"
        f"If EVERY row in this file went to 0 at once, suspect the BASELINE rather "
        f"than the needles -- but note that PIN_BASELINE_SHA is a frozen commit id "
        f"and cannot move on its own, so the cause is a history rewrite, not the "
        f"release landing. test_the_pin_baseline_is_frozen_and_still_in_history "
        f"tells you which."
    )


@pytest.mark.parametrize("path,needle,about", RETIRED, ids=ROW_IDS)
def test_retired_needle_is_gone_from_the_current_file(path, needle, about):
    """The half that actually pins. Cheap, and worth nothing on its own -- see
    the test above."""
    hits = current_text(path).count(normalize(needle))
    assert hits == 0, (
        f"retired wording came back in {path.name} ({hits} occurrence(s)).\n"
        f"  needle: {needle!r}\n"
        f"  about:  {about}\n"
        f"If this is a deliberate QUOTATION of the old wording rather than a "
        f"regression -- a comment narrating what the code used to do, or a "
        f"refutation that restates the claim it is refuting -- then the needle is "
        f"too broad: narrow it to the form only the retired code emits, the way "
        f"the seq-loop rows are narrowed to their lines.push() call."
    )


@pytest.mark.parametrize("path,needle,about", RETIRED, ids=ROW_IDS)
def test_every_needle_is_stored_in_its_own_normalized_form(path, needle, about):
    """A row authored with a raw newline, a tab, or a doubled space in it would
    match the normalized text only by accident of where the source happened to
    wrap. Storing needles pre-normalized makes that unrepresentable rather than
    merely discouraged -- and keeps the failure messages above quoting the same
    bytes the comparison used."""
    assert needle == normalize(needle), (
        f"needle for {path.name} is not in normalized form; store "
        f"{normalize(needle)!r}"
    )
    assert needle.strip(), "empty needle"


def test_the_pin_baseline_is_frozen_and_still_in_history():
    """One place to look when the whole file misbehaves at once, and the guard
    that keeps the frozen SHA honest.

    Three separate things, each with its own failure, because they call for
    different repairs:

      * the SHA is a full 40-hex literal, not an abbreviation and not a symbolic
        ref. An abbreviation can become ambiguous as the repo grows; a symbolic
        ref is the defect this whole design exists to prevent;
      * it still RESOLVES to a commit here;
      * it is an ANCESTOR of the current commit. This is the one a rebase
        breaks: a rewritten history leaves the old object resolvable for a while
        (so the check above still passes) while the branch no longer descends
        from it, which means the baseline is no longer "the tree this change was
        made against" and every row's provenance is void.
    """
    assert re.fullmatch(r"[0-9a-f]{40}", PIN_BASELINE_SHA), (
        f"PIN_BASELINE_SHA must be a full 40-hex commit id, got "
        f"{PIN_BASELINE_SHA!r}. An abbreviation can go ambiguous later, and a "
        f"symbolic ref (HEAD, a branch, a tag) MOVES -- which is exactly the "
        f"defect the comment above this constant describes"
    )

    resolved = git("rev-parse", "--verify", f"{PIN_BASELINE_SHA}^{{commit}}")
    assert resolved.returncode == 0, (
        f"the frozen baseline {PIN_BASELINE_SHA[:12]} does not resolve to a commit "
        f"in {REPO_ROOT}: {resolved.stderr.strip()}\n"
        f"Either this is a shallow or partial clone, or the commit was garbage "
        f"collected after a history rewrite. Do NOT advance the SHA to make this "
        f"pass -- that silently retires all {len(RETIRED)} rows. Re-point it at "
        f"whatever commit now holds the pre-1.16.2 tree, and re-verify every row's "
        f"presence-before against it."
    )

    ancestor = git("merge-base", "--is-ancestor", PIN_BASELINE_SHA, "HEAD")
    assert ancestor.returncode == 0, (
        f"the frozen baseline {PIN_BASELINE_SHA[:12]} is no longer an ancestor of "
        f"HEAD. The branch was rebased or its history rewritten, so this SHA is no "
        f"longer the tree these pins were authored against and their provenance is "
        f"void. Re-derive the baseline from the new history and re-verify every "
        f"row -- advancing the SHA blindly turns all {len(RETIRED)} presence-before "
        f"rows into assertions about a tree nobody chose."
    )


# ===========================================================================
# TRAP SELF-TESTS. Each measures the trap on this repo's real files, so the
# rule in the docstring is a recorded fact rather than a warning.
# ===========================================================================

# The idempotence sentence as a READER sees it -- the same words, minus the `//`
# that the comment's own line wrap put in the middle. This is what a needle
# typed from the rendered comment looks like.
RETYPED_WITHOUT_COMMENT_LEADER = (
    "but that coercion is idempotent, so running it again here is always safe, "
    "never destructive"
)


def test_a_needle_retyped_without_its_wrapped_comment_leader_matches_nothing():
    """TRAP 1, measured. The retyped form matches zero occurrences at the
    baseline -- so as an absence-after row it would have been green forever.
    This is the exact row that presence-before catches, and the reason that
    assertion is not optional."""
    assert baseline_text(SKEPTIC_TEMPLATE).count(normalize(RETYPED_WITHOUT_COMMENT_LEADER)) == 0, (
        "the retyped, leader-less form now matches at the baseline -- the comment "
        "was rewrapped, so trap 1 no longer reproduces here and this self-test "
        "should be re-pointed at whatever wrapped needle the row set now uses"
    )
    # ...while the form the row set actually stores does match, exactly once.
    stored = next(n for p, n, _ in RETIRED if p is SKEPTIC_TEMPLATE and "idempotent" in n)
    assert baseline_text(SKEPTIC_TEMPLATE).count(normalize(stored)) == 1
    assert " // " in stored, (
        "the stored needle no longer carries the wrapped comment leader, so this "
        "self-test is no longer demonstrating trap 1"
    )


def test_a_bare_needle_would_match_its_own_historical_quotation():
    """TRAP 2, measured, and the measurement is finer than the rule.

    The retired poll is narrated in the SURVIVING comments of the skeptic
    template, so a bare needle would fail absence-after against a deliberate
    description -- a false RED. The glossary template carries no such surviving
    narration, so the same bare needle would be harmless there. Both are
    asserted: the row set narrows BOTH to the emitted lines.push() form anyway,
    because "harmless today" is not a property worth depending on."""
    bare = normalize("for i in $(seq 1 45)")
    assert current_text(SKEPTIC_TEMPLATE).count(bare) >= 1, (
        "the skeptic template no longer narrates its retired poll, so trap 2 no "
        "longer reproduces there"
    )
    assert current_text(GLOSSARY_TEMPLATE).count(bare) == 0, (
        "the glossary template now narrates its retired poll too -- which is fine, "
        "but this self-test recorded that it did not"
    )
    # The narrowed forms the row set really stores are gone from both.
    for path in (SKEPTIC_TEMPLATE, GLOSSARY_TEMPLATE):
        stored = next(n for p, n, _ in RETIRED if p is path and "lines.push" in n)
        assert current_text(path).count(normalize(stored)) == 0


@pytest.mark.parametrize("kept", ["always safe", "never destructive"])
def test_deliberately_kept_refutation_wording_is_never_pinned_bare(kept):
    """The other face of trap 2. The idempotence fix keeps the old claim as a
    QUOTED REFUTATION -- it restates the wrong thing in order to correct it -- so
    these fragments are in the current file on purpose.

    Two assertions, and the second is the one with teeth: the wording is still
    there, AND no row in this file pins it in a form that would match it. A row
    set that grew such a row would be a false red on a correction."""
    assert current_text(SKEPTIC_TEMPLATE).count(normalize(kept)) >= 1, (
        f"{kept!r} is no longer in the skeptic template; if the refutation was "
        f"dropped, this self-test no longer describes the file"
    )
    for path, needle, _about in RETIRED:
        if path is not SKEPTIC_TEMPLATE:
            continue
        assert normalize(needle) != normalize(kept), (
            f"{kept!r} is pinned bare, but it survives deliberately as part of a "
            f"refutation -- that row is a false red waiting to happen"
        )


def test_no_two_rows_pin_the_same_needle_in_the_same_file():
    """A duplicated row double-counts a single retirement and makes the row
    total a misleading measure of how much this file actually covers."""
    seen = set()
    for path, needle, _about in RETIRED:
        key = (path, normalize(needle))
        assert key not in seen, f"duplicate pin row for {path.name}: {needle!r}"
        seen.add(key)


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
