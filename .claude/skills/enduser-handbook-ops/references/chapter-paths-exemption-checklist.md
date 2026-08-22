# `chapter-paths.mjs` hardening facts: exemption checklist, halt rationale, title-refusal asymmetry, needle collisions

Supplements SKILL.md §6–§8 and §14 (which cover the hub-file status, canon-drift and gate-tracing
traps in general terms) with the specific measured facts from the rounds that actually hardened
`findStaleChapterRows` and the flat-append title-refusal logic.

## `findStaleChapterRows`'s five exemption shapes — run this checklist on any change to the function

`findStaleChapterRows` decides "this row is fine, do not name it as stale." 1.17.0 (#357/#349, PR #583)
took five admitted findings across four review rounds (3 codex + the `lazy-ants-reviewer` bot) on this
function, and **every one of the five was in an exemption**, never in the main match logic:

1. own-row by containment
2. own-row exact vs CRLF
3. own-row exact vs any non-bullet row shape
4. the reference-definition test matching a definition-looking PREFIX
5. the destination match searching the RAW target instead of the module's own `foldTargetForMatch`,
   which hid one of the two accepted wikilink spellings

Codex found the first four across three rounds and missed the fifth entirely; the ped-ant bot found
the fifth on its first pass — the two review lanes are genuinely disjoint here, not redundant. When
touching this function again, check all five shapes explicitly rather than re-deriving a checklist
from scratch.

**Why they were expensive: the scan's result is a HARD HALT.** A wrong exemption either stops a
publish outright, or — in the non-bullet-row case specifically — the operator could NOT clear it:
deleting the row only for the next publish to re-append it, so the next scan halts again with no
operator-side escape. Filed as **#585** with the halt-vs-advisory decision, plus a third option that
was costed and left open: keep the halt, but let its text state the escape, so an operator's own
judgement can clear a false positive instead of an edit. This is the reasoning behind #585's decision
to KEEP the halt — without it, that decision reads as arbitrary rather than as a considered tradeoff
against the alternative's dead-end.

**What actually ended the four-round loop** was not a better single predicate — it was accepting an
ASYMMETRY between two populations with opposite safe failure modes: exact string comparison on the
`-`/`*`/`+` bullet the writer itself emits (sound against the nested-bracket attack, and it agrees with
`wireNestedListChapter`'s membership guard), and CONTAINMENT everywhere else (silent rather than
halting on an index form the scan does not recognize). A uniform rule across both populations was the
wrong instinct each of the four rounds it was tried.

## #574: a `]` in a title is NOT uniformly refused — pin both directions of any asymmetry claim

The 1.18.0 release copy (obsidian-vault.md, CHANGELOG.md, README.md, and the PR body — four write
sites) shipped the claim *"a `]` anywhere in the title is refused in both modes."* Measured, it is
wrong in **both** directions:

- `Items]` — **accepted** under wikilinks mode.
- `Items \] esc` — **accepted** in path mode, but **refused** under wikilinks.

Neither mode is uniformly stricter than the other, and no single character rule summarises the
refused set in either mode. Re-derive with `locateChapterLine` / `findStaleChapterRows` in both link
modes rather than trusting a character-class description.

**How the false universal survived:** the author measured three probes (`Items ]v1`, `Items]v1`,
`A [b] c`) that all happened to agree, and generalised from a sample rather than a proof. **A pin
covering only ONE direction of an asymmetry claim is what lets the false half survive** — the
regression pin said "refused in path mode and accepted in wikilinks" and nothing pinned the reverse
case, so the false half went unguarded through review. When pinning any asymmetry claim between two
modes, write a pin for EACH direction, not one pin per mode.

A cautionary sentence "worth noting: nobody attacks a sentence that states what a change COSTS" — it
reads as safe because it sounds conservative, so it draws no scrutiny even though it is exactly where
the false universal above lived.

## A needle that was unique can stop being unique when a sibling branch grows a same-spelled call

`tests/md-structure.test.mjs`'s uniqueness guard caught a real collision during the #574 work:
`expectedTarget)` stopped being unique to step 0's call once the flat-append branch grew a call
spelled the same way. Both mirrors of the guard (the bash `line_of` witness and the node-side list)
now carry step 0's outcome explicitly in the needle text, rather than relying on the bare call
expression staying unique across branches. When adding a new branch that calls an existing helper the
same way an existing needle already pins, check whether that needle's uniqueness assumption still
holds before trusting a green run.

## Related

- SKILL.md §6, §7, §8, §14 — the general traps (multi-write-site canon drift, gate-tracing, EOF-swallow
  fixtures, hub-partitioning) this file's specific measured facts sit inside.
- `references/wikilink-resolution-ground-truth.md` — the underlying resolution-tier facts the wikilinks
  write mode depends on; this file's title-refusal asymmetry is a write-time validation concern, not a
  resolution-tier one.
