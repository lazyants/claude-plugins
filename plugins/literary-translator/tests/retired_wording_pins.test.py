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
SKEPTIC_TRIAGE_SCHEMA = ASSETS / "schemas" / "skeptic-triage.schema.json"

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
# The 1.16.1 release merge (#359): what the 1.16.2 RELEASE retired.
BASELINE_RELEASE = "4343994b9de4f6fe979e6e5af711ed9ab11c4381"
# The 1.16.2 release commit: what the post-review FIX ROUND retired, on top of
# the release. A second generation, and it needs its own baseline for a reason
# worth stating -- a claim INTRODUCED by 1.16.2 and retired by the fix round
# occurs zero times at BASELINE_RELEASE, so pinning it against that baseline
# would fail presence-before while the row was perfectly correct. Each row
# therefore carries the commit it was retired FROM, rather than the file
# carrying one baseline for everything.
BASELINE_FIX_ROUND = "c33d1d8a68348f1edf2b4fdeee5f3874bbb17083"

PIN_BASELINES = (BASELINE_RELEASE, BASELINE_FIX_ROUND)


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
        # encoding is PINNED, not left to text=True's locale default. Without it
        # subprocess decodes with locale.getpreferredencoding(False) while
        # current_text() reads utf-8, so under a non-UTF-8 locale the BASELINE
        # half mis-decodes and the current half does not. These templates carry
        # em dashes and the multiplication sign, so the mismatch is reachable --
        # and it would corrupt exactly the half that makes a row real, turning
        # presence-before red for a reason no needle edit can fix.
        capture_output=True, text=True, encoding="utf-8", timeout=30, check=False,
    )


def baseline_text(path: Path, baseline: str = BASELINE_RELEASE) -> str:
    rel = path.relative_to(REPO_ROOT).as_posix()
    proc = git("show", f"{baseline}:{rel}")
    assert proc.returncode == 0, (
        f"could not read {rel} at the frozen baseline {baseline[:12]}: "
        f"{proc.stderr.strip()}\n"
        f"If the object is simply missing, the branch was rebuilt from a rewritten "
        f"history -- see test_the_pin_baseline_is_frozen_and_still_in_history for "
        f"which of the two this is."
    )
    return normalize(proc.stdout)


def current_text(path: Path) -> str:
    return normalize(path.read_text(encoding="utf-8"))


def _diff_side(path: Path, baseline: str, sign: str) -> str:
    """Normalized text of just the ADDED (`+`) or REMOVED (`-`) lines of the
    diff between `baseline` and the working tree, for one file.

    THIS IS WHAT BINDS A ROW TO THE CHANGE rather than merely to the file, and
    it is the difference between a replacement row that verifies something and
    one that verifies nothing. A literal that happens to exist elsewhere in the
    current file gives a correct file-level count while the intended
    replacement was never installed: retired passes 1 -> 0, replacement's count
    is right, and the pair proves nothing. Requiring the replacement to appear
    among the lines this change ADDED closes that.

    Line prefixes are stripped and the survivors joined with a space, so the
    result normalizes the same way whole-file text does -- including keeping
    the `//` leader that a wrapped comment carries mid-sentence."""
    rel = path.relative_to(REPO_ROOT).as_posix()
    proc = git("diff", "-U0", baseline, "--", rel)
    assert proc.returncode == 0, f"git diff failed for {rel}: {proc.stderr.strip()}"
    marker = "+++" if sign == "+" else "---"
    lines = [
        ln[1:] for ln in proc.stdout.splitlines()
        if ln.startswith(sign) and not ln.startswith(marker)
    ]
    return normalize(" ".join(lines))


def diff_added_text(path: Path, baseline: str) -> str:
    return _diff_side(path, baseline, "+")


def diff_removed_text(path: Path, baseline: str) -> str:
    return _diff_side(path, baseline, "-")


# ---------------------------------------------------------------------------
# The rows. Each is (file, needle, what the needle is a claim ABOUT).
#
# The third field is not decoration: an absence assertion whose message only
# says "this string is gone" tells whoever hits it nothing about whether the
# string coming back is a regression or a deliberate quotation.
# ---------------------------------------------------------------------------
# Each row: (baseline, file, retired needle, replacement needle, expected
# replacement count, what the claim is ABOUT).
#
# `replacement` may be None where a claim was DELETED with no successor -- an
# invented replacement would be worse than an absent one. Every row that has a
# real one carries it, and test_rows_without_a_replacement_are_declared below
# makes the count of unpaired rows visible instead of letting it drift.
#
# Every needle here was copied out of normalized text programmatically, never
# retyped. Two of them show why: `is filed // separately` and `this release's
# CHANGELOG // promises that file is untouched` both carry a `//` mid-sentence
# because the comment wrapped there. Typed as a reader sees them, both match
# zero occurrences -- verified.
RETIRED = [
    # -- 1.16.2 RELEASE: skeptic-pass-wf.template.js ---------------------
    (
        BASELINE_RELEASE, SKEPTIC_TEMPLATE,
        "but that coercion is idempotent, so running it // again here is always safe, "
        "never destructive",
        "it is not idempotent", 1,
        "the claim that --validate-fragment is idempotent, which it is not: the "
        "successful run rewrites the fragment in place, so a second run consumes "
        "its own already-pruned output",
    ),
    (
        BASELINE_RELEASE, SKEPTIC_TEMPLATE,
        "safe to run even if it was already run before",
        None, 0,
        "the same non-idempotence claim in its short form; deleted outright rather "
        "than reworded, so it has no one-to-one successor",
    ),
    (
        BASELINE_RELEASE, SKEPTIC_TEMPLATE,
        "per batch: precheck + dispatch + wait == 3",
        "precheck 1 + // dispatch 1 + wait WAIT_CALLS == 2 + WAIT_CALLS", 1,
        "the pre-#352 per-batch ladder, when a wait was ONE agent call",
    ),
    (
        BASELINE_RELEASE, SKEPTIC_TEMPLATE,
        "const estimatedCalls = 3 * BATCHES.length + 2",
        "const estimatedCalls = (2 + WAIT_CALLS) * BATCHES.length + 2", 1,
        "the pre-#352 preflight expression itself",
    ),
    (
        BASELINE_RELEASE, SKEPTIC_TEMPLATE,
        'lines.push("for i in $(seq 1 45); do "',
        'lines.push("end=$((SECONDS + " + waitChunkSec(chunkIndex) + "))', 1,
        "the EMITTED pre-#352 poll: 45 iterations x 20 s == 900 s in a single bash "
        "call, against a measured 600 s clamp. Narrowed to the lines.push() form "
        "because the surviving comments narrate the old poll on purpose (trap 2)",
    ),
    (
        BASELINE_RELEASE, SKEPTIC_TEMPLATE,
        "after the timeout (about 15 minutes), return exactly the line: TIMEOUT",
        "return exactly the line: PENDING", 2,
        "the retired TIMEOUT sentinel and the single-shot timeout it implies; a "
        "chunk now reports PENDING, which means 'this chunk learned nothing', not "
        "'the batch is over'. The replacement occurs TWICE -- the chunk prompt and "
        "the re-check prompt each instruct it",
    ),
    # -- 1.16.2 RELEASE: glossary-pass-wf.template.js --------------------
    (
        BASELINE_RELEASE, GLOSSARY_TEMPLATE,
        "live -- perBatch = 1 + 4*(MAX_CITATION_RETRIES+1)",
        "live -- perBatch = 1 + (3 + WAIT_CALLS)*(MAX_CITATION_RETRIES+1)", 1,
        "the 1.16.1 live ladder (13N+2), before one wait became WAIT_CALLS calls",
    ),
    (
        BASELINE_RELEASE, GLOSSARY_TEMPLATE,
        "The offline branch is therefore EXACTLY the historical 3*BATCHES.length + 2",
        "offline -- perBatch = 1 + (1 + WAIT_CALLS) == 2 + WAIT_CALLS", 1,
        "the claim that offline keeps its historical figure. What survives #352 is "
        "the LADDER-FREE guarantee, not the number: offline pays 5N+2 now, because "
        "the Bash clamp is indifferent to research_mode",
    ),
    (
        BASELINE_RELEASE, GLOSSARY_TEMPLATE,
        'lines.push("for i in $(seq 1 45); do "',
        'lines.push("end=$((SECONDS + " + waitChunkSec(chunkIndex) + "))', 1,
        "the EMITTED pre-#352 poll, glossary's copy",
    ),
    (
        BASELINE_RELEASE, GLOSSARY_TEMPLATE,
        "15-minute wait poll",
        "is why the WAIT_BOUND_SEC wait exists at all -- spent since 1.16.2 across", 1,
        "the prose name for the single-shot 900 s wait",
    ),
    # -- 1.16.2 RELEASE: skeptic_setup.py --------------------------------
    (
        BASELINE_RELEASE, SKEPTIC_SETUP,
        "estimated_calls = 3 * batch_count + 2",
        "estimated_calls = PER_BATCH_CALLS * batch_count + FIXED_RUN_CALLS", 1,
        "the setup script's own copy of the pre-#352 ladder. It refuses a run "
        "BEFORE the Workflow starts, so leaving it behind makes one gate refuse a "
        "batch the other admits, after this run's manifests are already written",
    ),
    # -- POST-REVIEW FIX ROUND (retired from the 1.16.2 commit itself) ---
    (
        BASELINE_FIX_ROUND, SKEPTIC_TEMPLATE,
        "OUT of scope for #352 and is filed // separately",
        "OUT of scope for #352, and it is NOT filed", 1,
        "a claim that the non-idempotence defect was FILED. No issue tracks it -- "
        "writing 'filed' about work nobody filed is the same defect class as any "
        "other unverified claim, and no reviewer lane can see it",
    ),
    (
        BASELINE_FIX_ROUND, GLOSSARY_TEMPLATE,
        "this release's CHANGELOG // promises that file is untouched",
        # Anchored on the ENFORCING TEST the corrected note now cites, not on
        # the note's prose. Deliberate, and learned the hard way: this row first
        # pinned the correction's opening sentence, and a concurrent lane
        # rewrote that sentence within the hour -- correctly, because the
        # replacement note had itself gone stale. A file path is the stable part
        # of a comment that keeps being reworded, and it is also the SUBSTANCE
        # of what changed here: the note stopped asserting a remembered fact
        # about another file and started citing a check that fails loudly when
        # the fact stops holding.
        "tests/rejected_anywhere_parity.test.py", 1,
        "a justification resting on a CHANGELOG promise the same release broke: "
        "1.16.2 changed 234 lines in the very file it claimed was untouched. "
        "TWO successive versions of that note were false in the same way, which is "
        "what makes the SHAPE worth recording rather than either correction: a "
        "comment asserting a fact about a SIBLING file has no local edit that can "
        "invalidate it, so this file's own review can never catch it going stale -- "
        "a reviewer reads one file and the claim is about another. Both versions "
        "read true when written and were false within the hour. The durable form, "
        "and what this row now pins, is a citation of an ENFORCING TEST: "
        "rejected_anywhere_parity.test.py fails loudly when the agreement stops "
        "holding, so the note cannot outlive its own truth",
    ),
    (
        BASELINE_FIX_ROUND, SKEPTIC_TRIAGE_SCHEMA,
        "partial coverage shown as partial.",
        "which labels cited > verified as par", 1,
        "the claim that evidence_coverage durably records partial coverage. It "
        "does not: --validate-fragment rewrites the fragment in place and the "
        "second validation recomputes cited from the already-pruned list",
    ),
]

ROW_IDS = [f"{b[:7]}::{p.name}::{n[:40]}" for b, p, n, _r, _c, _a in RETIRED]


@pytest.mark.parametrize("baseline,path,needle,repl,count,about", RETIRED, ids=ROW_IDS)
def test_retired_needle_occurred_exactly_once_at_the_baseline(
    baseline, path, needle, repl, count, about
):
    """THE HALF THAT MAKES THE ROW REAL. Without it, a mistyped or mis-wrapped
    needle is green forever and pins nothing."""
    hits = baseline_text(path, baseline).count(normalize(needle))
    assert hits == 1, (
        f"the needle for {path.name} matched {hits} time(s) at the frozen baseline "
        f"{baseline[:12]}, not exactly once.\n"
        f"  needle: {needle!r}\n"
        f"  about:  {about}\n"
        f"If this is 0: the needle is WRONG, not the code -- retype nothing, copy "
        f"it programmatically out of normalize(<baseline text>). What does NOT "
        f"cause this: whitespace. Both sides are normalized, so a needle typed "
        f"with a doubled space or a different line wrap still matches (measured). "
        f"What does: a sentence wrapped inside a // comment block carries the "
        f"leader mid-sentence, and any character-level difference survives "
        f"normalization -- a hyphen for an em dash, a straight quote for a curly "
        f"one, one word rewritten. Also check the row is on the right BASELINE: a "
        f"claim introduced by 1.16.2 and retired afterwards occurs zero times at "
        f"BASELINE_RELEASE and once at BASELINE_FIX_ROUND. If this is >1: narrow "
        f"the needle -- the row describes one site and is silently retiring several."
    )


@pytest.mark.parametrize("baseline,path,needle,repl,count,about", RETIRED, ids=ROW_IDS)
def test_retired_needle_is_gone_from_the_current_file(
    baseline, path, needle, repl, count, about
):
    """The half that actually pins. Cheap, and worth nothing on its own."""
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


@pytest.mark.parametrize("baseline,path,needle,repl,count,about", RETIRED, ids=ROW_IDS)
def test_retired_needle_is_bound_to_the_hunk_that_removed_it(
    baseline, path, needle, repl, count, about
):
    """Provenance for the retirement: the needle must appear among the lines this
    change REMOVED, not merely be absent from the file now.

    A needle can go absent for reasons that have nothing to do with this change
    -- a rename, an unrelated rewrite, a file that never contained it in the form
    the row claims. Binding it to the removed side makes the row a statement
    about THIS change."""
    removed = diff_removed_text(path, baseline)
    assert normalize(needle) in removed, (
        f"the retired needle for {path.name} is absent from the file (so the pin "
        f"passes) but does NOT appear among the lines this change removed against "
        f"{baseline[:12]}. The row is not describing this change.\n"
        f"  needle: {needle!r}\n  about:  {about}"
    )


@pytest.mark.parametrize("baseline,path,needle,repl,count,about", RETIRED, ids=ROW_IDS)
def test_replacement_is_installed_and_bound_to_the_hunk_that_added_it(
    baseline, path, needle, repl, count, about
):
    """The other row kind, and the half a retirement alone cannot give.

    A claim can be deleted and its correction never written; absence-after is
    perfectly happy either way. Three things are required, and the third is what
    the review round asked for:

      * the replacement occurs in the current file the expected number of times;
      * it did NOT occur at the baseline, so it is genuinely new rather than
        something that was always there;
      * it appears among the lines this change ADDED. Without that a literal
        copied from an unrelated part of the current file carries a correct
        file-level count while the intended replacement was never installed --
        retired passes 1 -> 0, the count is right, and nothing is verified.
    """
    if repl is None:
        pytest.skip("row declares no one-to-one replacement (claim deleted outright)")
    r = normalize(repl)
    hits = current_text(path).count(r)
    assert hits == count, (
        f"the replacement for {path.name} occurs {hits} time(s), expected {count}.\n"
        f"  replacement: {repl!r}\n  about: {about}"
    )
    assert baseline_text(path, baseline).count(r) == 0, (
        f"the replacement for {path.name} already existed at {baseline[:12]}, so "
        f"this row proves nothing about what the change installed.\n"
        f"  replacement: {repl!r}"
    )
    assert r in diff_added_text(path, baseline), (
        f"the replacement for {path.name} is present with the right count but does "
        f"NOT appear among the lines this change ADDED against {baseline[:12]} -- "
        f"it is an unrelated literal that happens to be in the file, and the pair "
        f"verifies nothing.\n  replacement: {repl!r}"
    )


@pytest.mark.parametrize("baseline,path,needle,repl,count,about", RETIRED, ids=ROW_IDS)
def test_every_needle_is_stored_in_its_own_normalized_form(
    baseline, path, needle, repl, count, about
):
    """A row authored with a raw newline, a tab, or a doubled space in it would
    match the normalized text only by accident of where the source happened to
    wrap. Storing needles pre-normalized makes that unrepresentable rather than
    merely discouraged -- and keeps the failure messages above quoting the same
    bytes the comparison used."""
    for label, value in (("retired", needle), ("replacement", repl)):
        if value is None:
            continue
        assert value == normalize(value), (
            f"{label} needle for {path.name} is not in normalized form; store "
            f"{normalize(value)!r}"
        )
        assert value.strip(), f"empty {label} needle"
    assert baseline in PIN_BASELINES, (
        f"row for {path.name} names baseline {baseline!r}, which is not one of the "
        f"declared PIN_BASELINES"
    )


def test_rows_without_a_replacement_are_declared():
    """Makes the unpaired rows COUNTABLE rather than invisible.

    A replacement row is the stronger of the two kinds, so the number of rows
    that lack one is a real measure of how much this file leaves unverified. It
    is pinned rather than merely observable, so adding an unpaired row is a
    deliberate act with a number attached -- and inventing a replacement to make
    the number look better is worse than the gap it hides."""
    unpaired = [
        (path.name, needle)
        for _b, path, needle, repl, _c, _a in RETIRED
        if repl is None
    ]
    assert len(unpaired) == 1, (
        f"expected exactly 1 row with no one-to-one replacement, found "
        f"{len(unpaired)}: {unpaired}. If a claim really was deleted without a "
        f"successor, raise this count and say so in the row's `about`; do NOT "
        f"invent a replacement to pair it with."
    )


@pytest.mark.parametrize("PIN_BASELINE_SHA", PIN_BASELINES, ids=[b[:7] for b in PIN_BASELINES])
def test_the_pin_baseline_is_frozen_and_still_in_history(PIN_BASELINE_SHA):
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
        f"a PIN_BASELINES entry must be a full 40-hex commit id, got "
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
    assert baseline_text(SKEPTIC_TEMPLATE, BASELINE_RELEASE).count(
            normalize(RETYPED_WITHOUT_COMMENT_LEADER)) == 0, (
        "the retyped, leader-less form now matches at the baseline -- the comment "
        "was rewrapped, so trap 1 no longer reproduces here and this self-test "
        "should be re-pointed at whatever wrapped needle the row set now uses"
    )
    # ...while the form the row set actually stores does match, exactly once.
    stored = next(n for _b, p, n, _r, _c, _a in RETIRED
                  if p is SKEPTIC_TEMPLATE and "idempotent" in n)
    assert baseline_text(SKEPTIC_TEMPLATE, BASELINE_RELEASE).count(normalize(stored)) == 1
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
        stored = next(n for _b, p, n, _r, _c, _a in RETIRED
                      if p is path and "lines.push" in n)
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
    for _b, path, needle, _r, _c, _a in RETIRED:
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
    for _b, path, needle, _r, _c, _a in RETIRED:
        key = (path, normalize(needle))
        assert key not in seen, f"duplicate pin row for {path.name}: {needle!r}"
        seen.add(key)


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
