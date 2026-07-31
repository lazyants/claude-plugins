# Close the class, not the instance

When an adversarial reviewer returns ~1 finding/round that is a new INSTANCE of the SAME root cause each time, stop patching locations. The tell: finding scope narrows but the root cause is identical every round. This is still a healthy (narrowing) loop, but generalizing at round 3 instead of round 9 saves the rounds in between.

- [Enumerate the set + state the invariant](#enumerate-the-set--state-the-invariant)
- [Enumerate the LAYERS too — a set complete at one layer is not the class](#enumerate-the-layers-too)
- […but enumerate INPUTS, never OUTCOMES of independent conditions](#enumerate-inputs-never-outcomes)
- [Mutating an unguarded rule: read the SPREAD of the reds](#mutating-an-unguarded-rule-read-the-spread-of-the-reds)
- [Format / serialization migrations: enumerate by the shared value](#format--serialization-migrations)
- [Prose-scattered set → a completeness-GREP gate](#prose-scattered-set--a-completeness-grep-gate)
- [Verify the gate itself — it's code](#verify-the-gate-itself)
- [Algorithm-internal dedup: a "claimed" bitmap is the tell](#algorithm-internal-dedup)
- [Swapping a core data structure drops implicit behaviors](#swapping-a-core-data-structure)
- [Symbolic refs, not line numbers — and the wider class: never state a CENSUS in prose](#symbolic-refs-not-line-numbers--and-the-wider-class-never-state-a-census-in-prose)
- [Fixing catastrophic backtracking is not fixing complexity](#fixing-catastrophic-backtracking-is-not-fixing-complexity)

## Enumerate the set + state the invariant

Two moves close a class: (a) ENUMERATE the complete instance set (every fire-and-forget artifact at an unscoped path; every durable commit/consume gate; every validity-gating byte a resume digest must cover), and (b) state the GENERAL PRINCIPLE / invariant IN THE PLAN so the reviewer verifies the CLASS is closed, not just this instance. When enumerating reveals the fix crosses far more surface than a "bugfix" should, splitting it into its own follow-up plan (rather than force-bundling with unrelated small fixes) is the right call — see scope-gating.

## Enumerate the LAYERS too

<a id="enumerate-the-layers-too"></a>

Enumerating the SITES of a pattern closes the class only at the layer you were shown it. When the defect is "an item that could not be processed was silently omitted", the same predicate usually exists at several layers of a pipeline, and each fix looks locally complete because the set really was exhaustive *at that layer*. Ask instead: **at every layer between the source and the consumer, what can drop an item, and does the consumer distinguish "dropped" from "was never there"?**

Verified 2026-07-31 (enduser-handbook 1.12.0): a long unbroken run of review rounds, all of them **one defect** — every round found the previous round's fix incomplete one layer up, and the late ones found it in the error code *next to* the one just guarded, and in a rule the module had already written down for a neighbouring subsystem. (Exact counts live in PR #382, where they are dated and cannot rot; they are deliberately not quoted here — see the census rule below.) Three of the layers:

1. The report loop hashed a chapter's images and kept only the `present` results, so an unreadable image vanished from the comparison and a chapter with one good image and one unreadable one reported `unchanged`. Fixed at that loop; the site set was complete there.
2. The next round found the same predicate in the snapshot that feeds the earlier stage, where a dropped image becomes a *missing key* — and a missing opening key means "brand-new file", which SKIPS the did-it-change check. Same omission, opposite consequence, one layer up.
3. The round after that found the walk itself refuses symlinks before the snapshot can classify anything, so the layer-2 fix could never see them.

**Two tells worth checking directly, both of which produced the rounds above.**

**A shared helper whose callers disagree about what "missing" means.** One `snapshotAssetHashes` served both the opening and the closing observation, and its comment justified dropping an unreadable file: *"a missing expected image fails completeness (rule 3) and the chapter is reported ineligible, never silently trusted."* True of the closing snapshot, which is what that rule reads. Exactly inverted for the opening one, where missing means brand-new and waives the check. The comment was not wrong so much as written for one of two callers, and it read as a completed safety argument. **When one helper feeds two consumers, verify its "absent" case against EACH consumer's meaning, and say in the comment which caller the argument is about.**

**A regression test whose FIXTURE cannot reach the condition its own name claims.** This kept recurring on that branch *after* being written down here as a layer concern — the single most productive question anyone asked of it — which is why it is stated as its own rule now: the question is not only "which layer does this test exercise" but **"can this input physically exhibit the condition I am asserting about?"** A green test and a reachable test look identical.

Two mechanisms produced every instance, and they pull in opposite directions:

- **A real object in a NORMAL state cannot model an ABNORMAL one.** A hard link's dirent is still `isFile()`, so it never reaches the branch that refuses symlinks. A leaf symlink's hazard key *is* an asset key, so it never reaches the containment match that a refused *directory* needs. A real `fs.Dirent` always has a type, so it cannot model the untyped one some filesystems return. A real `fs.Stats` has every predicate, so it cannot model a caller implementing only the declared subset. Each test passed with the path it named entirely broken.
- **A stub built to model the abnormal state encodes MY model of it** — which is half of what is under test. "All predicates false" was my belief about what an untyped dirent looks like; asserting behavior against that proves the code agrees with my belief, not with the platform.

The resolution is to use the real type *constructed in the defective state* wherever the API allows it (`new Dirent(name, 0, path)` — the constructor is reachable off `Object.getPrototypeOf(realDirent).constructor`), and otherwise **to assert the fixture exhibits the condition before asserting any behavior about it**: `assert.equal(fixture.isFile(), false, 'fixture must model X')` costs one line and converts a silently-vacuous test into a failing one. Where a contract has required and optional members, note that a fixture supplying *more* than required cannot prove the optional path — that needs a second fixture supplying exactly the minimum.

Then mutate: change the guard, and check that exactly the intended test fails. Every one of these was caught that way, and a mutant that kills nothing is the same signal as a mutant that kills the wrong test.

**When one guard is applied at SEVERAL call sites, two further vacuity shapes appear, and mutating per site is the only thing that finds either.**

- **An existential assertion over the sites pins none of them.** `assert(calls.some(c => c.askedForX))` passes while any single site still asks, so every per-site mutant survives. Assert the OUTCOME each site is responsible for, or assert per site.
- **A fixture's shape decides which sites execute, so one fixture structurally cannot cover the others.** Measured on a three-site identity read: with a symlinked directory the first `lstat` is consulted only for `isSymbolicLink()` and its identity fields are never touched, so that fixture cannot reach two of the three sites; with a plain directory the third site — the link-target read — never runs at all. Neither fixture is wrong; neither is sufficient. Parameterize over the shapes that select the branch (`for (const topology of [...]) test(...)`) rather than picking the one that looks most interesting.

**A guard that makes a REQUEST needs a responder that behaves differently when it is not asked, or the request is unobservable.** Passing an option that only improves precision is invisible on any input where the imprecise answer happens to be correct — an inode below 2^53 renders identically whether it arrived as a number or a bigint, so dropping the option changed nothing any test could see. The fixture has to be a seam whose ANSWER DEPENDS ON THE REQUEST (exact when asked, deliberately inexact when not); then a site that stops asking fails immediately. Applies to any opt-in precision, consistency, or isolation flag: `{ bigint: true }`, `FOR UPDATE`, `If-Match`, a strict-mode parser option.

Knowing this at round 1 would have collapsed most of that loop: the fix is the same shape at every layer (keep "could not read it" as its own fact, never as an absence), the layers are enumerable by reading the call chain once, and most of the remaining rounds were spent discovering that the previous round's *test*, not its fix, was the incomplete part.

## Mutating an unguarded rule: read the SPREAD of the reds

**When you finally write the missing fixture for a rule that never had one, the mutant's blast radius is a free diagnostic — count the reds and look at WHICH files they are in.** N reds of which exactly one is your new fixture means the rule was already holding up gates nobody designed to guard it: it is *incidentally load-bearing*, and the suite has been resting on an unstated invariant. That changes what you are doing — not adding a nice-to-have fixture, but pinning a mechanism the rest of the suite silently depends on — and the red list names the files whose assertions were riding on it, which is where to look next. (Finding those unguarded rules in the first place is verify-the-fix.md's "Ask which correct behaviours have no gate"; this section is what to do at the moment you mutate one.)

Measured twice on one branch, 2026-07-20 (enduser-handbook 1.6.0, the `_section_contains` awk scanner behind `has_in_section` in `reference-assets.test.sh`). Method both times: delete the rule outright from the shipped helper, run the full documentation-assertion suite, read the failures rather than the count alone.

- Dropping the fence-closer's CHARACTER check (a closing fence must repeat the opener's character) **fails 16 assertions, only one of which is the new fixture** — the other 15 are pre-existing assertions across `static-md.md` and `obsidian-vault.md`. Neither file contains a literal tilde fence, so this is *not* backtick-vs-tilde confusion: without the char check the closer test degrades to "does this line's leading repeated-character run reach the required length", regardless of which character, so some internal line of a real fenced example closes its fence early and cascades wrong section boundaries downstream.
- Two rounds later, the same shape: reinterpreting the literal `index()` needle match as a regex **fails 25 assertions, only one of which is the new fixture**. Most real needles contain parentheses — `validateGroups(entries)`, `specReferencesDir(specText, dir)` — which are regex-special, so under regex reinterpretation they stop matching literally. The rule was load-bearing for two dozen real gates while never being deliberately guarded.

**The corollary is the sharper half: give each AXIS of a multi-part rule its own minimal ISOLATED fixture.** One economical fixture per rule is the instinct, and for a two-axis rule it produces a green suite over a broken axis: a COMBINED fixture can survive a single-axis mutant BY ACCIDENT. Measured on the same helper — "a combined one silently masked the char bug, because dropping the char check causes cascading re-opens that can land the needle live again by accident." The mutant's own cascade restored the very condition the fixture was asserting, so the fixture was not weak, it was un-discriminating.

So: one minimal fixture per axis, each mutation-tested against its own axis and cross-checked to stay green under every other axis's mutant — a **clean diagonal** (full deletion fails all of them, each partial mutant fails exactly its own), reproduced independently against a reference harness that implements each mutant mode rather than read off a single run. For a two-part predicate that means a 2x2 matrix, not one clever case: on the section-boundary check's `<=`, "a same-level fixture does not discriminate an ==-only mutant, and a shallower fixture does not discriminate a <-only one" — each half needs its own fixture, checked rather than assumed. This is the per-AXIS twin of the per-SITE rule above: one fixture cannot discriminate what its own structure does not separate.

## Format / serialization migrations

When a change alters how a SHARED VALUE is serialized/hashed/encoded, the producer and EVERY consumer must migrate atomically. Per-consumer self-consistent tests MASK an un-migrated consumer: each consumer's own fixtures write data whose old-form hash coincidentally equals what that consumer recomputes (writer and reader both stale in the same test) — a **false-green via mutual staleness** that keeps the full suite green while a fail-closed hard gate would spuriously reject in production. Discipline after any shared-format change:

1. GREP every READ and WRITE of the value plus its helper; migrate all atomically. Enumerating by the shared value surfaces instances no single fixer flagged.
2. Keep any byte-identical-DUPLICATE helper genuinely byte-identical across copies — verify the hashing body matches (signature/docstring diffs are fine).
3. Add a NON-canonical-input regression test per consumer (a fixture whose on-disk bytes are deliberately non-canonical) so a future regression fails LOUDLY instead of passing via mutual staleness.

## Prose-scattered set → a completeness-GREP gate

For a fix that must reconcile EVERY occurrence of X across many files (a doc-consistency sweep, a rename, a capability-claim correction, dead-code removal), do NOT enumerate the sites in the plan — the set is too scattered to list reliably, and the reviewer will keep returning "you also missed site N" round after round. Recognize the pattern at round 1 (the first "you missed a site" on a sweep task) and switch immediately to:

1. State the RULE once (e.g. the three-status rule for what each term must say).
2. Give each owner a FILE; have them grep it exhaustively for the pattern and apply the rule — listed sites are illustrative starting points, not the complete set.
3. Prove completeness with a LEAD repo-wide GREP GATE at build time: every hit must be reconciled, on an explicit ALLOWLIST of legitimate non-claims (enum/schema/config forward-spec), or a named deliberate EXCLUSION (append-only CHANGELOG history — correct the NEW entry, don't rewrite the past).

Completeness is then enforced mechanically, not by the plan's enumeration.

## Verify the gate itself

The gate is CODE — verify it before trusting it as proof (a gate can be a silent no-op):

- Use ERE not BRE: `git grep -nEI`. A bare `.{0,20}` interval is LITERAL in BRE → 0 hits. Markdown emphasis splits phrases (`**three** adapters`), so allow `[^A-Za-z0-9]{0,25}` between words rather than a literal space.
- A git pathspec `dir/**/*.md` silently DROPS files directly under `dir/` (matches only nested) — use DIRECTORY pathspecs; git grep recurses.
- If the target token is a HOMONYM swamped by a legitimate high-frequency use (a source-format name vs a same-string block-field name with hundreds of hits), a raw grep is useless — scope the gate to the reader-facing CLAIM surface and DISPOSITION each hit (reconcile / leave-block-field / exclude-history), don't count raw tokens.
- A lexical gate is only a FLOOR: a semantic claim naming no token ("every shipped adapter") slips through and needs an owner's full-file READ.
- RUN the gate, COUNT its output, and eyeball for over/under-match BEFORE banking it as a completeness proof.

## Algorithm-internal dedup

The whack-a-mole also happens INSIDE one algorithm. A per-item "claimed"/"used"/"visited" boolean array keyed by an atomic unit (token/char/index) that a review keeps finding new overlap shapes against is the tell — the bitmap conflates unit-membership with candidate-identity, so patching `any(claimed[...])`→`all(claimed[...])` only fixes one instance and never converges. Convergent rebuild: drop the bitmap; track `seen_spans` as a set of exact accepted-identity keys (e.g. `(name, start, end)` triples already emitted); at each position try candidates longest-first, emit unless its OWN key is already in the set, then advance by exactly ONE unit regardless of match length or outcome. State the invariant as a COMMENT before rebuilding ("suppress only an exact duplicate emission — a token participating in some OTHER candidate's span never blocks a DIFFERENT candidate from also covering it") so the reviewer can verify the class is closed.

## Swapping a core data structure

ANY core-structure swap (bitmap→set, linear-scan→trie, list→heap) can silently drop an IMPLICIT behavior the old structure encoded — a trie walk naturally tracks only the deepest terminal, dropping a longest-first linear scan's "when the longest match is an exact duplicate, fall back to the next-shorter FRESH form at the same position" semantic, a real regression a green suite misses because no test exercised that exact case. Before swapping the core data structure of a matching/selection/dedup algorithm, ENUMERATE every behavior the old structure encoded implicitly — longest-first-fallback, first-match-wins, one-unit-advance, stable order — and pin each with a test BEFORE the swap. An "equivalent rewrite" is only equivalent on the cases a test forces it to be. Watch the sibling perf-class too: an expensive full-scan call nested inside a per-item loop is its own class — sweep every site at once.

## Symbolic refs, not line numbers — and the wider class: never state a CENSUS in prose

Any fix that inserts/removes lines staleizes every doc line-number reference below it, and a green suite never catches it (line-refs live in prose). Don't chase the number (it re-shifts on the next edit) — make the ref SYMBOLIC ("its format gate", no line number). That eliminates the drift CLASS, not the instance. Prefer symbolic/anchor refs over line numbers in any prose that points into an edited file.

**Line numbers are one instance of a bigger class: any MECHANICALLY CHECKABLE fact stated in prose will rot, because nothing gates prose.** Caller lists ("X is this export's only production caller"), consumer enumerations ("every consumer reads `.length`, `.containerTitle` or `.index`"), sync counts ("hand-synced five times"), round counts, needle tallies, and equality claims ("byte-identical in both adapters") all fail the same way — and a green suite is structurally incapable of catching any of them. Verified over five consecutive review rounds on one branch (enduser-handbook 1.11.0), a different file each round; every round's fix changed code that some *other* comment described, which manufactured the next round's finding. Patching instances is a treadmill.

The test to apply, per claim: is it true **by construction** or true **by census**? Leave the first, rewrite the second into the invariant it was trying to express — "callers reach this view through this export rather than re-deriving the expression" survives any number of callers; "the only caller is X" does not. A claim you can verify with one `grep` belongs in a test or nowhere; it is exactly the claim that will be wrong next month. The good comment states DESIGN INTENT ("reached by tests alone", "there must be exactly ONE implementation of this scan") — that stays true because it constrains the future rather than describing the present.

Two refinements that cost a round each to learn:

- **Rot-proof does not mean nameless.** Deleting the caller names entirely ("however many they are") is rot-proof but strictly less navigable than the correct form: *names PLUS an explicit hedge* — "(today: A and B) — however many there are". Keep the illustration, mark it as illustration.
- **Apply the test to the CORRECTIONS, not only the inherited claims.** A fix can be born stale. Same session: a consumer enumeration written *as the fix* for a previous stale claim was already incomplete on the day it landed (it omitted a consumer in another file), and a pin comment written to guard cross-file drift itself asserted "byte-identical" — measured false, and actively misleading, because the two copies deliberately differ in their closing cross-reference. The hardening introduced the stale claim it was hardening against.

### The remediation comment is the single most likely place to write a fresh census

The refinement above was written with an end-of-sweep trigger ("re-run the test over the comments the sweep just wrote") and it did **not** fire — three more instances landed after it, in the same branch, inside the comments whose only purpose was to close this class. The trigger is wrong, not the content. The failure mode has a mechanism, and knowing it is what makes the rule fire:

**Justifying a removed count pulls you toward stating counts.** To explain *why* the old number was wrong you reach for the corrected number, the measurement that disproved it, and the suite total proving the new gate works. Every one of those is a fresh census, written by someone who at that exact moment believes they are the person who understands this failure mode best. Measured instances, all three verified after the rule above already existed:

- a *narrower* replacement for a false "byte-identical" claim, which was also false — it named one cause of divergence when the two paragraphs diverge in mechanism wording throughout (2298 vs 2325 chars, first divergence 300 chars before the end);
- a hard-coded suite total, `581/581`, in a comment documenting a newly added gate — stale on write, because the gate being documented *is* the 582nd test;
- a "mechanically checkable" rule citing `git diff <prev-tag>..HEAD -- "$CPD"` in a repo that has no version tag for that plugin, where `$CPD` is a script-local variable expanding to empty for any reader who pastes the command.

So, at write time rather than at sweep end:

- **The moment a sentence exists to justify a removed number, it is the highest-risk sentence in the diff.** If the justification needs figures to persuade, they go in the commit message — immutable, dated, and never read as a present-tense fact about the file.
- **Never quote a suite total, pass count, or round count in a comment.** These look like evidence and are the fastest-rotting facts you can write; the commit that adds one test invalidates every one of them.
- **A cited command is a claim too — run it from the READER's position.** A command that only works with the author's cwd, script-local variables, or a tag that does not exist is a rule nobody can apply, which is worse than no rule because it reads as enforced.
- **A paragraph explaining why enumeration was removed must not itself enumerate.** "It carried three counts and a two-needle tally" is a census about a census. Say what the invariant now is; the history is the commit's job.

## Enumerate inputs, never outcomes

The enumerate-the-set move above applies to the INSTANCE set (every call site, every artifact,
every gate). It does NOT apply to the OUTCOME set of two or more independent conditions — there,
enumerating is the failure mode, because the outcomes multiply while the generating rule does not.

Tell: a list you keep having to extend, where each extension is discovered by someone hitting a
combination nobody listed.

Verified 2026-07-20 (enduser-handbook CHANGELOG, four revisions of ONE line): a test-count claim
went stale number → machine-specific number → mechanism *plus* a three-item totals list → mechanism
only. The increments (`377 unconditional, +9 when node is on PATH, +1 when esbuild is reachable`)
were correct from the third revision and never changed. Only the totals list kept breaking, because
two independent gates yield four combinations and the list named three. Deleting the list — not
extending it to four — made the claim correct by construction and let any reader derive their own.

Same shape recurred four other ways in that branch: a category named with fewer members than it has
("glossary/Related links" spans two target types with different formulas), a hardcoded test-file
list one behind the directory it mirrors, and a rules table blind to statement-level mutants.
**Enumerations of outcomes are fragile; mechanisms generate.** When the set is generated, ship the
generator and state that the conditions are independent.

## Fixing catastrophic backtracking is not fixing complexity

A reviewer's ReDoS finding names the regex's internal ambiguity (e.g. two adjacent optional
quantifiers) as the root cause; fixing THAT ambiguity only proves the exponential case is gone — it
says nothing about the surrounding MATCH STRATEGY. If that strategy retries one monolithic
pattern at every candidate start position (`matchAll` over a "one-or-more-X-then-Y" regex), removing
the exponential blowup commonly still leaves it quadratic, and a reviewer that already found one
perf bug in a matcher will look harder at the fix, not less — expect (and pre-empt) a second finding
on the very next round. Verified 2026-07-24 (enduser-handbook `citation-audit-lib.mjs`, #258): an
adjacent-`\s*` separator was genuinely exponential (~26 repeats hung 8+s), fixed by collapsing it to
one quantified alternation and confirmed clean by two independent reviewers — then the NEXT review
round found the outer retry-from-every-position shape was still O(n²) (29ms→1.64s from 2k→16k
titles). Closing the class required abandoning the single-regex-retried-everywhere approach for a
genuine single forward pass (find every candidate unit once; group adjacent units into chains once;
check the terminating condition once per chain, never per interior position) — provably lossless
here because any position where the terminator legitimately could appear would already have stopped
chain growth there, so no shorter internal sub-chain is ever missed. After any backtracking fix,
benchmark ACTUAL scaling across at least two widely-separated sizes (not just re-running the
original repro at the size that made it hang) before calling the finding closed — "no longer
exponential" and "linear" are different claims, and only a scaling ratio (not a single absolute
timing) distinguishes them.
