# Mutation/RED validity, and parity tests that construct the wrong collaborator

## A RED that proves nothing looks exactly like one that proves everything

Red-before-green and mutation testing both rest on: *break the thing, watch the check fail, restore.*
The failure is only evidence if the mutant is **otherwise valid** — if the ONLY difference is the
behaviour under test.

Three ways the RED becomes worthless, all measured in one session:

- **A mutant that does not parse.** Swapping a string via a sloppy `str.replace` produced a syntax
  error; the test file failed to import, the runner printed `not ok 1 — <file>`, and at the summary
  line that is indistinguishable from the assertion firing. It proved the file was broken, not that
  the assertion worked.
- **A sandboxed runner failing on the environment.** An agent's `mkdtemp` hit `EPERM`, producing 19
  fixture failures out of 46 that had nothing to do with the code under review.
- **A RED from the wrong test.** Read WHICH test failed and WHY, never the count.

**How to apply:** after mutating, assert the mutant is still valid before reading the result — parse
it (`node --input-type=module -e 'import(...)'`), or confirm the failure message names the assertion
rather than the loader. Restore from a byte-identical copy and verify the digest, not from a second
`replace`. And when other uncommitted work is in the same file, restore from a backup taken
outside the repo — `git checkout -- <file>` silently discards that work and `git stash` is
repo-wide, so it takes every other session's too. See *Restoring the file after a mutation*
below for the full sequence.

## The dual: a GREEN that proves nothing, because the mutant never landed

A mutation harness fails silently in four ways, all of which read as "the code already handles it":

- the fixture LOSES the character it is testing (a U+00A0 written through a shell heredoc arriving
  as a plain space) — assert the character is in the file before trusting the exit code;
- the revert between cases takes the ARTIFACT UNDER TEST with it (`git checkout -- .`), so every
  case after the first runs against the pre-fix file;
- the patch that was supposed to introduce the fix found no anchor and changed nothing
  (`str.replace` returns the string unchanged and prints whatever you told it to print);
- **the harness cannot SEE the failure it is waiting for — it captures stdout while the failing
  path writes to stderr.** Measured 2026-08-18 (enduser-handbook #574): the harness collected only
  stdout while the gate's `bad()` writes to stderr, so two pins reported GREEN under mutation while
  they were in fact load-bearing. **A verification harness that cannot observe failure reports
  exactly what a passing one reports** — capture both streams, or assert on the exit status, before
  reading any mutation result.

And for a defect that fails OPEN, WHERE the fixture goes decides whether the failure is visible:
the same mutation placed before every real surface hides everything and exits nonzero, which looks
like the check working. Place it after the last real surface.

**And the exit code itself can be somebody else's.** `python3 check.py | tail -2; echo $?` reports
TAIL's status, so a guard that exited 2 reads as 0 — which, for a guard, looks exactly like the
polarity bug where it reports a problem and returns success. Measured twice on the same artifact in
one day, by two sessions independently: both nearly filed a fail-open finding against a check that
fails closed. Redirect to a file and read `$?` from the command itself.

Why this one in particular slips through: a broken pipe that yields a false GREEN reads as noise and
gets re-run, while "reports a problem, exits 0" reads as a FINDING — and a finding-shaped error is
the one nobody verifies before acting on it.

## Restoring the file after a mutation — the step that loses the work

`mutate → run → restore` reads as three steps of equal risk. It is not: **the restore is the only
one that can destroy work, and every one of its failure modes prints what success prints.**

**The rule: commit before mutating.** Once the change under test is in a commit, the restore cannot
cost anything. Everything below is what goes wrong when it is not — and one thing that goes wrong
precisely *because* it is.

### The two restores, and why "commit first" does not settle which to use

Committing protects the work but **changes what a restore restores**, so the two rules must be
applied together:

| State of the change under test | Restore with | Failure if you use the other |
| --- | --- | --- |
| Uncommitted | a backup taken immediately before the mutation, outside the repo | `git checkout --` reverts the mutation *and the change*, exit 0, tree clean |
| Committed | `git checkout <pre-change rev> -- <file>`, e.g. `origin/main` | a bare `git checkout -- <file>` restores the change, so the "mutation" is a **no-op** |

The second row is the counter-intuitive one. **A mutation run after the change is committed is
vacuous:** `git checkout -- <file>` brings the guard back, the mutant never lands, and the suite
prints exactly the all-green a real restore prints. Measured (#370): the first backout printed
`34 passed`, read as "the suite survives losing the guards"; re-running against `origin/main`
produced the real `25 failed`. **Assert the mutation happened** — `grep -c GUARD_SYMBOL` must print
`0` — before believing any result.

### `git checkout -- <path>` restores from the INDEX, not from HEAD and not from what you wrote

Verified directly: with HEAD at `v1`, `git add` staging `v2`, and `v3` in the working tree,
`git checkout -- f.txt` yields **`v2`**. So any edit made after the last `git add` is silently
discarded — no warning, no conflict, exit 0 — and `git diff` then shows nothing for the file,
because the working tree now matches the index exactly.

Two aggravations: `git checkout <tree-ish> -- <path>` (from a stash, say) **also updates the index**,
so a later bare `git checkout -- <path>` restores *that* revision rather than yours; and when the
lost edits were non-behavioural (a docstring fix, a hoisted call) **the suite stays green and
nothing points at the loss.**

**`git stash` is not the safe alternative here.** It is repo-wide: in a shared working tree it takes
every other session's and teammate's uncommitted work with it, the reverted files read as CLEAN, and
a failed `pop` restores nothing. Prefer it only in a checkout you are certain you are alone in.

### `cp` is interactive in this environment — the restore hangs and leaves the mutant

A loop doing `cp file backup` → mutate → run → `cp backup file` hits `overwrite <file>? (y/n [n])`
on the restore and blocks until the Bash-tool timeout kills it — **with the mutant still in the
tree** and the first mutant's RED already printed, so the output reads like a completed run.

Restore with something that cannot prompt: `/bin/cp -f` (bypasses the alias), or a Python
read/write that asserts its own result. Never bare `cp`, never `mv`.

```python
python3 -c "
import pathlib, filecmp
pathlib.Path(T).write_bytes(pathlib.Path(BACKUP).read_bytes())
print('restored byte-identical:', filecmp.cmp(T, BACKUP, shallow=False))"
```

### A successful restore can still leave the mutant executing — `__pycache__`

Python validates a cached `.pyc` against the source's **size and mtime in whole seconds** (the pyc
header stores `int(st_mtime)` and the size — verified). A mutation that (a) does not change the
file's byte length and (b) is restored inside the same second therefore leaves the **mutated**
bytecode cached and valid. Every later run executes the mutation while `git diff --stat`, a
byte-for-byte `diff` against the saved copy, and `grep` all show correct source.

Byte-length-identical mutations are common precisely because they are the smallest: `r([1-9]...)` →
`(r[1-9]...)`, `<` → `<=`, one renamed identifier.

**Apply:** run every mutation cycle under `PYTHONDONTWRITEBYTECODE=1`, and when a test reds against
source you have just verified correct, run
`find . -name __pycache__ -prune -exec rm -rf {} +` **before forming any theory about the code.**

### Confirming a restore — two reads, neither of which is the exit code

A partially restored tree and a clean one print identically, so verify by content:

- **Grep for a token that exists ONLY in your edit** — a marker you inserted. Grepping a string the
  original file also contains returns a pre-existing line and reads as your work surviving.
- **Read the COLLECTED total, not the pass line.** `41 deselected` becoming `39 deselected` is the
  file having lost two tests. A pass count cannot show this.

`git status --short` is not this check: clean means clean relative to the index, which is what you
want only once HEAD already carries your work.

### Both routes in — the vacuous one and the successful one

- **Vacuous:** the harness asserts its anchor is present before writing, the assert fails, no
  mutation is applied — and an `&&`-free script runs the test and the restore anyway. The test
  passes vacuously (`1 passed, 41 deselected`) and the restore wipes the work. Nothing in the
  output says so.
- **Successful:** the mutation lands, the test goes RED for exactly the right reason and names the
  right cases, and the trailing restore reverts every uncommitted edit in the file. **Nothing about
  that transcript looks wrong — the finished RED is what stops you reading further.** So the
  read-back above is not optional after a *successful* mutation either.

### A hard-wrapped target blames the anchor for a tree the last restore already wiped

Pinning prose in a hard-wrapped doc (a `SKILL.md`, a reference `.md`), the anchor phrase spans a
newline in the raw bytes while the test that pins it normalizes whitespace first. The phrase reads
as present, the raw-text mutation still fails `ANCHOR MISSING` — and that message accuses the
anchor, which is exactly the wrong place to look. Build the matcher against wraps, then **re-read
the tree before rewriting the anchor**:

```python
rx = re.compile(r"\s+".join(re.escape(w) for w in anchor.split()))
```

### Someone else's restore takes your edit, and its proof reads as success

A teammate that mutates a shared file and restores it from **its own** backup reverts every edit
anyone else made to that file while the mutation was in flight. The teammate compares the restored
file against its own pre-mutation snapshot, so a byte-identical match is exactly what a clobber
produces. Two-sided guard mutation is the right technique and worth asking for — but it makes the
mutated file a WRITE target for the duration, even when the brief said "do not edit this file", and
the lead's own contract file is the likeliest victim because that is the file worth mutating.

**Apply:** the lead must not touch that file until the teammate reports; if it already did,
re-verify the specific edit with a `grep` for the exact string — `git status` shows the file as
modified either way. Prefer having the teammate mutate a COPY, or make the mutation the last thing
anyone does to that file.

## A parity/differential test must construct the collaborator exactly as production does

A test that measures agreement with another module by **constructing that module's object itself**
is only as faithful as the constructor call. When the other module later adds optional parameters,
the old call keeps working and silently selects the pre-change behaviour — the test stays green
while measuring something production no longer does.

Measured instance: `render_obsidian._Linker` gained `delinked_targets=` and `diagnostic_pattern=` in
LT 1.32.0. Built as `_Linker(pattern, target_to_entity, mode)` a de-linked target's span is NOT
consumed and a shorter name inside it still links; built the way `render()` builds it — both kwargs
passed — the span is consumed. A parity test using the short form asserted agreement with behaviour
that had been replaced.

**Rule: a parity/differential test constructs the collaborator exactly as production does, and says
so in the test.** Copy the real call site's arguments; if that is awkward, that awkwardness is the
finding. A peer's report of the new behaviour is not enough either — the peer described `render()`'s
composed behaviour and the direct-construction path contradicted it.

The tell that this class is in play: the other module's function grew keyword arguments with
defaults chosen for backward compatibility. Those defaults are precisely the old semantics, so every
hand-built collaborator freezes there.

## A hand-typed membership list inside a drift test freezes the drift it exists to catch

This is a THIRD shape, distinct from both self-derived-expected and the self-satisfying anti-masking
gate above: neither of those reaches it. Self-derived-expected is a fixture whose expected value is
produced by the code under test; the anti-masking gate is a substring-presence check that is
vacuously true. Here the test's own body embeds a **static, hand-typed copy** of the thing it is
supposed to be diffing against a live source — so the test's sense of "drift" is inverted: making the
real artifact CORRECT is what turns the check RED, not what turns it GREEN.

Measured (`literary-translator` #591, PR #593): `schema_literal_drift.test.py` checked two of three
bundles against the tuples that actually own them, and hard-coded the third as a literal membership
list. A wrong sentence in shipped docs survived **nine releases** because the one test that could
have caught it would have gone RED the moment someone corrected the doc to match the hard-coded list,
not the other way around — nobody reads a new RED as "the doc just got fixed."

**Fix:** read the owning tuple (`scaffold_setup.py`'s) via `ast`, not by re-typing its members and not
by importing the module — an `import` ties the result to test-module load order via its sibling
`import cache_key`, reintroducing an ordering dependency for no reason. `ast`-parsing the source file
gets the live tuple without executing it.

**Apply:** when a "drift" test's expected side is anything other than a live read of the artifact that
owns the fact (a parsed tuple, a parsed schema, a parsed enum), ask which direction wrong-to-right
moves the test — if fixing the artifact can turn the test red, the test is pinning the artifact's
CURRENT state, not detecting its drift from truth.

## An exhaustive/whole-set assertion catches what per-item assertions miss — and needs its own count check

**A whole-key-set assertion caught a textually clean merge that every per-key assertion missed.**
Measured (`literary-translator` #588/#589, PR #599, LT 1.32.0→1.33.0): a rebase merged
`validate_backlinks.py` and its test file without conflict, then the suite went red —
`test_default_enabled_report_keeps_exactly_its_documented_keys` asserts the WHOLE key set of the
default-enabled report, and #588 had added `delink_cost` to that report without updating the
enumeration. Every per-key assertion in the same file stayed green; only the whole-set assertion
caught the drift. Worth keeping that shape for any report a gate emits: pair per-key checks with one
assertion of the complete key/shape set.

**An exhaustive sweep needs a COUNT assertion as well as its property assertions.** Measured
(`literary-translator` #586, PR #594): a 30 940-string sweep over a 13-char adversarial alphabet
caught eleven real mutants — except an always-`fallback` mutant, which passed the sweep because every
property assertion is *skipped* on the fallback path, and nothing was asserting how many cases had
actually been exercised. The iteration-count assertion and the per-case property assertion catch
DIFFERENT mutants — a mutant that silently short-circuits every case onto a no-op path is invisible to
property assertions alone, because there is nothing left for them to check. Add a `assert cases_run ==
expected_total` (or equivalent) beside the property loop, not instead of it.
