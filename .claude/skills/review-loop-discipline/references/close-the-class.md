# Close the class, not the instance

When an adversarial reviewer returns ~1 finding/round that is a new INSTANCE of the SAME root cause each time, stop patching locations. The tell: finding scope narrows but the root cause is identical every round. This is still a healthy (narrowing) loop, but generalizing at round 3 instead of round 9 saves the rounds in between.

- [Enumerate the set + state the invariant](#enumerate-the-set--state-the-invariant)
- […but enumerate INPUTS, never OUTCOMES of independent conditions](#enumerate-inputs-never-outcomes)
- [Format / serialization migrations: enumerate by the shared value](#format--serialization-migrations)
- [Prose-scattered set → a completeness-GREP gate](#prose-scattered-set--a-completeness-grep-gate)
- [Verify the gate itself — it's code](#verify-the-gate-itself)
- [Algorithm-internal dedup: a "claimed" bitmap is the tell](#algorithm-internal-dedup)
- [Swapping a core data structure drops implicit behaviors](#swapping-a-core-data-structure)
- [Symbolic refs, not line numbers](#symbolic-refs-not-line-numbers)

## Enumerate the set + state the invariant

Two moves close a class: (a) ENUMERATE the complete instance set (every fire-and-forget artifact at an unscoped path; every durable commit/consume gate; every validity-gating byte a resume digest must cover), and (b) state the GENERAL PRINCIPLE / invariant IN THE PLAN so the reviewer verifies the CLASS is closed, not just this instance. When enumerating reveals the fix crosses far more surface than a "bugfix" should, splitting it into its own follow-up plan (rather than force-bundling with unrelated small fixes) is the right call — see scope-gating.

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

## Symbolic refs, not line numbers

Any fix that inserts/removes lines staleizes every doc line-number reference below it, and a green suite never catches it (line-refs live in prose). Don't chase the number (it re-shifts on the next edit) — make the ref SYMBOLIC ("its format gate", no line number). That eliminates the drift CLASS, not the instance. Prefer symbolic/anchor refs over line numbers in any prose that points into an edited file.

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
