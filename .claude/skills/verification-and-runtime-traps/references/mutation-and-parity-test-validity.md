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
`replace`. And prefer `git stash push -- <file>` / `git stash pop` over `git checkout -- <file>` when
other uncommitted work is in the same file — `checkout` silently discards it.

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
