# Shared-worktree run validity: a suite number that proves its own trustworthiness

ANY concurrent write in a shared worktree — a deliberately scoped guard mutation, OR simply teammates
implementing in parallel — corrupts a co-running suite pass and produces a false RED (or a false
GREEN) that is **indistinguishable from a real result**. The measurement is of a tree that no longer
exists by the time it is reported.

The central lesson, learned across four occurrences: **scheduling quiet does NOT work.** Do not
negotiate for silence; make the run prove its own validity.

- [Why the false signal looks like the strongest evidence](#why-the-false-signal-looks-like-the-strongest-evidence)
- [The four shapes](#the-four-shapes)
- [Scheduling quiet does NOT work](#scheduling-quiet-does-not-work)
- [Make the measurement prove its own validity](#make-the-measurement-prove-its-own-validity)
- [Reading a contaminated run correctly](#reading-a-contaminated-run-correctly)

## Why the false signal looks like the strongest evidence

The contaminated result arrives wearing every badge of a genuine finding: reproducible across
repeated runs, a coherent and thematically-related failing set, and every cheap alternate explanation
already ruled out. That is exactly the signature of a real bug — and exactly the signature of
measuring a teammate's live mutant, because **the mutation itself is stable while it is on disk.**

This breaks a precondition that the usual verification reflexes silently assume.
[[feedback-convergence-needs-two-sound-methods]] and
[[feedback-verify-my-own-claims-not-just-teammates]] both assume the two measurements are of the SAME
artifact. Here two sound methods run against two DIFFERENT states (one mutated, one not) and agree
with each other while disagreeing with reality. Stability and coherence are not proof against this
class of artifact.

## The four shapes

**1 — A teammate's scoped guard mutation, read by someone else's full-suite run.** Verified
2026-07-26 on the enduser-handbook 1.11.0 team (EH-TESTS + team-lead sharing one worktree). EH-TESTS
ran a scoped guard-mutation (Edit a source line, run the suite, `git checkout` to restore) to
re-demonstrate a RED flip the team-lead had asked to see live. While that mutation was on disk, the
team-lead independently ran the FULL suite on the same shared worktree and got a **stable 660/3,
three runs in a row** — the three failures being exactly the three tests the mutation was designed to
flip. The team-lead checked every "obvious" explanation (guard present in source, the test-file-only
commit, the test file alone giving 297/0), each came back consistent with a genuine cross-file
interference bug, and they were one step from escalating it as a new, confidently-wrong finding.

**2 — The same mechanism from the OTHER direction, in a ~2-second window.** Verified 2026-07-30, same
team's 1.12.0 EH work (team-lead + one teammate sharing the `eh-1.12.0` worktree). The team-lead had
just wired four new SKILL.md-wording pins into `reference-assets.test.sh` and RED-checked each one
solo: decoy the needle out of SKILL.md, run the suite, restore — four times back to back, no heads-up
sent first. A teammate's own full-suite run landed in the ~2-second gap for pin #3 and got
**712/713**, the one failure being exactly that pin's needle. Re-running immediately came back
713/713 and stayed clean twice more. The teammate reproduced the awk matching logic by hand against
the on-disk file, confirmed the needle WAS findable, and correctly refused to just trust the flake
label — but still misattributed the *cause*, calling it a "read-during-write race against the test
file" when the real mechanism was someone else's deliberate scoped mutation.

**3 — The same 712/713 signature again, hit by a different agent, misattributed to a mid-edit save.**
Same session, the round-6 declaration sweep. The observer correctly refused to rule the failure out on
OWNERSHIP grounds (they checked and found the suite really does read `.d.mts`, 26 references, so "not
my files" would have been unsound) and ruled it out on the TIMELINE instead: their own files and the
test script were byte-identical across all 14 runs, so identical content had produced both the failure
and the greens. That method was right and is why the event stayed diagnosable at all. What went wrong
was the last step — reading a 22:10:55 SKILL.md mtime as a teammate's save when it was the team-lead's
*restore* after a scoped RED-check.

**4 — NO mutation testing at all, just three teammates implementing in parallel.** This is the shape
the old, narrower framing ("guard-mutation testing") could never catch — and it is far more common
than deliberate mutation. Symptom: four mutually contradictory suite reports of the SAME branch within
minutes — **152 failed/289 errors**, **4797 passed**, **4796+4**, and "my own files green" — every one
taken in good faith, every one measuring a different half-finished moment. Two runs 2.5 minutes apart
had **ZERO overlap in their failing sets**.

## Scheduling quiet does NOT work

Asking every teammate to freeze failed twice, for two independent reasons, each costing a discarded
run:

- **Teammates answer "stopped" about EDITING** while a suite they launched earlier is still running.
- **A tree byte-stable for 90s is not a tree whose work is DONE.** One lane was paused mid-task with
  1 of 4 files converted, and the stability signal read identical to completion.

## Make the measurement prove its own validity

**Hash the tree BEFORE and AFTER the run and print `RUN INVALID` when they differ.** Then a
contaminated run announces itself instead of being reported as a result. This caught a contaminated
pass on the very first use.

The snapshot needs two ingredients, because either alone is blind:

- `git status --porcelain` — but on its own it is blind to an edit that leaves the same file merely
  still-modified.
- **every source file's mtime** — built with `-print0 | sort -z | xargs -0`.

```sh
# Ingredients, not a drop-in: the stat format string is platform-specific.
git status --porcelain
find <src roots> -type f -print0 | sort -z | xargs -0 stat …    # mtimes

N=$(find <src roots> -type f -print0 | tr -cd '\0' | wc -c)     # NUL records, not lines
[ "$N" -lt 200 ] && exit 3
```

**Why `-print0 | sort -z | xargs -0` is load-bearing, not style.** The obvious
`find … | sort | xargs stat` silently drops every path containing a space, so those files are
invisible to the very check that exists to catch invisible edits — a validity harness with its own
silent blind spot, which then stamps `RUN VALID`. `stat` writes its errors to stderr while the hash
still computes, so a piped run looks fine.

Reproduced in this repo against
`plugins/literary-translator/tests/fixtures/backlink_e2e/expected_vault/`: the naive pipeline turns
the real fixture `003 Chapter Two.md` into **three** non-existent paths (`./003`, `Chapter`,
`Two.md`) and emits nothing but stderr noise, while the `-print0`/`sort -z`/`xargs -0` form lists all
15 files correctly.

**Assert a file-count floor.** Count the records inside the snapshot and refuse an implausibly small
one (`[ "$N" -lt 200 ] && exit 3`); a count you only remember to eyeball is one you forget on the run
that needed it — same failure shape as [[gotcha-zsh-no-word-splitting]]. Count NUL records, not
lines: a path containing a NEWLINE is one record but two lines, and a path containing a SPACE is one
record that `xargs` splits into several ARGUMENTS — exactly the `003 Chapter Two.md` → three
non-existent paths failure above.

**Wait on the COMPLETION criterion, never on stability.** "All N target files contain X" beats
"nothing changed for 90s", which is satisfied by any pause.

**While any other agent is ACTIVE in a shared tree — editing, building, or running a suite it launched
earlier — treat every teammate-reported suite number from that tree as unusable, INCLUDING a green
one.** The danger is symmetric; a green taken mid-edit is just a false all-clear nobody investigates.
The bar is ACTIVITY, never a CONFIRMED concurrent write: a write in flight is not observable (see
"Scheduling quiet does NOT work" above), so demanding proof of one before distrusting a number
restores exactly the failure this file exists to prevent.

**Have teammates verify only their OWN files during the work**, and reserve the full pass for one
self-validated run after everyone stops.

## Reading a contaminated run correctly

**Announce BEFORE mutation-testing in a shared worktree** — don't merely avoid the same file. The cost
of skipping that announcement lands on whoever else is running verification at that moment, not on
the mutator. And once ANY teammate says "I'm about to mutation-test" (or a review round has
snapshotted the branch), every OTHER agent sharing that worktree must not run full-suite verification
passes until told the mutation is reverted — not just avoid EDITING the same file, but avoid RUNNING
anything that reads the shared filesystem while a mutant is live elsewhere in it.

**Mutation-test in an isolated/detached worktree** (its own `git worktree add`, not the team's shared
one) whenever teammates are concurrently active — the same "worktree isolation" principle as
[[gotcha-bash-tool-cwd-persists]] and the standing "always use an isolated working copy" rule,
extended from "don't edit the same file" to "don't even READ the tree while someone else's scoped
mutation is on disk."

**A single-shot failure that vanishes on retry is EQUALLY consistent with a live mutant.** The
signature isn't only "stable across repeated runs" — if the mutation's own replace/run/restore cycle
is fast enough, your re-run simply lands after the restore. Don't let "it went away on retry, so it
must have been a fluke" stand as the explanation; ask WHY it went away.

**Verifying the underlying claim is necessary but not sufficient.** Confirming the needle really
is/was findable tells you the finding isn't a bug in your reproduction — it does not tell you what
caused the transient state you observed. State the cause as "unknown, possibly a concurrent mutation,
ask who's active" rather than reaching for the first plausible-sounding mechanism (a generic "race
condition") and writing that down as fact. "Flake" or "race" is the label that gets written down and
sends the next person hunting a problem that was never in the harness.

**An mtime tells you a file CHANGED, never WHO changed it or WHY** — and a mutation-and-restore cycle
leaves exactly the same mtime signature as an ordinary edit. When a concurrent writer is implicated,
ask who was active rather than inferring intent from timestamps. The honest available answer is
usually "a concurrent writer had it in a transient state," which is correct without naming an actor.

**Before escalating a reproducible + stable + thematically coherent suite failure**, check: is anyone
else active in this same worktree right now, and did the failing set match something someone might be
deliberately mutating?

## Why the first three were hard to diagnose — a harness detail, not a reasoning failure

The enduser-handbook shell suite prints FAIL lines to **stderr** and the TOTAL to **stdout**, so the
usual capture shows the count and hides the name. Capture it with:

```sh
2>&1 | grep -E '^  FAIL |^TOTAL:'
```

See (→skill:enduser-handbook-ops), `references/reference-assets-suite-output.md`. With the failing
test's NAME in hand, all three of these would have been one lookup instead of an inference chain.
