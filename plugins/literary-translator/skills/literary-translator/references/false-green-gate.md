# The false-green gate (`validate_draft.py`)

This generalizes the real, source-proven `validate_draft.py` from
`historiettes-t3` (battle-tested at ~75-segment scale) plus the
`adversarial-false-green-gate` methodology almost directly.

**False-green**, for this domain: a validator reports OK while a defect —
dropped footnote content, a swapped verse, an empty translation, a stray
untranslated sentinel — actually shipped. `validate_draft.py` exists
specifically to make that impossible for the defect classes below. It is
deterministic (no LLM judgment call anywhere inside it) and is the gate a
draft must clear *before* it is ever handed to the codex reviewer — the
reviewer's job is literary/accuracy judgment; this script's job is "did
anything mechanically get corrupted."

There is a second false-green move this discipline names explicitly:
**editing a self-check to make it pass.** A deterministic check protects you
only while it stays honest — silencing, weakening, or faking one to reach
green manufactures exactly the false-green it existed to prevent, and is never
acceptable. A check that fires wrongly on a legitimately-different input is a
plugin issue to file; a genuine coverage gap is a new check to add and
regression-lock — never a line to quietly delete. The post-extraction gate
below (`validate_extraction.py`) enforces this structurally, pinning the
extractor's own self-check region by hash so a locally-weakened check is
caught rather than trusted.

Reads `segpack_{seg}.json` (the source) and `draft_path(seg) =
segments/{seg}.draft.json` (no target-language suffix — see
[`ledger-and-resumability.md`](./ledger-and-resumability.md) for why the
draft path is deliberately unsuffixed).

**Candidate-file mode (1.4.7, #198).** `validate_draft.py` (and its siblings
`draft_ready.py`/`review_ready.py`) accept an optional `--candidate-file <attempt>`
that overrides `draft_path(seg)`/`review_path(seg)` while still reading the segpack
from the canonical location. The shipped `codex_job.py` driver uses it to run this
full gate on an ISOLATED attempt artifact and only atomically promote it to the
canonical path once it passes (validate-before-promote). Independently, #198 means
this on-disk gate is now reliably REACHED at all: before the driver, the
`codex:codex-rescue` forwarder backgrounded the codex job and returned a stub, so no
`draft_path(seg)` was ever written and the gate ran on nothing (every segment
timed out) — the driver owns the launch deterministically, so a real draft reaches
this gate.

## The six checks

Stated as invariants — not fr/ru-specific, not tied to any one language
pair:

1. **Block/footnote/verse key sets are exact 1:1 with the source segpack.**
   No silent omission, no silent extra. This is an exact key-set invariant,
   not a size-only comparison — a dropped key and a substituted key of the
   same count must both surface.

2. **Per prose block: the *multiset* of placeholder sentinels
   (footnote-anchor / embedded-verse tokens) matches the source's
   multiset.** Multiset, not set — order-independent, so it catches
   drop/duplicate/mangle of a placeholder token. What counts as a
   placeholder sentinel here is an EXACT MAP, not a shape/prefix pattern: a
   `⟦…⟧` span is a placeholder iff it is a `⟦FNREF_N⟧` footnote anchor or
   one of this segpack's own declared `verses[].placeholder` strings
   (any adapter's own naming, e.g. `⟦POEM_1⟧`) — any other bracketed span
   is literal source prose, not a fidelity token, so a translation that
   renders it away is not falsely rejected. The mandatory adversarial
   pass below is what decides whether any newly discovered marker-order hole
   means this check must become stricter.

3. **Per standalone verse block: the translation equals THAT block's own
   placeholder, via a `parent_block` bijection — not flat set
   membership.** This closes a real false-negative an earlier version of
   the proven validator had: with only set-membership, two verses could
   swap or share a placeholder and both blocks would "pass." The bijection
   check is by `parent_block`, so the validator must bind each verse to the
   specific placeholder it belongs to rather than accepting "some known verse
   placeholder is present somewhere."

4. **Per footnote: non-empty, no untranslated-sentinel string, placeholder
   fidelity.** Content is compared, not just key presence — this is the
   other concrete false-negative the adversarial pass on the proven
   validator found: an emptied-out footnote passed when only keys were
   checked. Placeholder fidelity is part of this check, not merely a prose
   block concern.

5. **Per verse: the exact required-content fields are derived from the
   resolved `verse_policy.mode`** (see
   [`verse-policy.md`](./verse-policy.md) for the full six-mode table),
   e.g.:
   - `full_rhymed_plus_literal` requires both `rendered` and
     `literal_gloss`, each non-stub, and provably distinct from each other
     after whitespace normalization (collapse all whitespace runs to a
     single space before comparing — a mere rewrap of the literal gloss
     must not pass as a distinct rhymed rendering).
   - `literal_only` requires only `literal_gloss`.
   - **`skip` exempts translated CONTENT only, never coverage.** An
     earlier draft of this rule was flagged for silently weakening the
     gate ("verse section not checked at all" under `skip`) relative to
     the proven `validate_draft.py`, which always enforces the verse
     key-set/placeholder-bijection checks (checks 1–3 above) regardless of
     policy. A `skip`ped verse must still appear with its correct key, and
     its placeholder must still resolve to exactly the right
     `parent_block`; only the requirement that its *value* be a translated
     rendering is dropped (untouched/pass-through source text, or an
     explicit passthrough marker, satisfies it instead).

6. **Sentinel-lite marker survival — `body_refs_only` policy ONLY.** For
   every block with a non-empty `body_ref_markers[]` (populated by the
   producing extractor — `extract.py.template` for `gutenberg_epub`/
   `plain_text`, the custom extractor for `custom` — and by `segpack.py`,
   when `footnotes.apparatus_policy ==
   body_refs_only` — see
   [`source-format-adapters/README.md`](./source-format-adapters/README.md)),
   confirm each recorded marker STRING still appears in that block's
   translated text, at the SAME multiset count as recorded. This is a
   cheap substring/count check, deliberately *not* the same
   placeholder-fidelity machinery checks 1–4 use — there is no `FN:{N}`
   apparatus target to check fidelity against under this policy, by
   design (the anchor was extracted as a bare literal marker, e.g. `[N]`,
   never as an `⟦FNREF_N⟧` sentinel, because nothing exists on the other
   end for it to point at). It runs under `body_refs_only` specifically
   and never under `omit_apparatus` (which promises nothing survives, so
   there is genuinely nothing to check). An earlier draft ran *zero*
   footnote-related checks under `body_refs_only`, meaning a translator
   silently dropping the sole literal marker mid-sentence passed the gate
   completely undetected — contradicting the policy's own promise that a
   reader still sees a marker where a note used to be. This check closes
   that gap; it is a real, automated, best-effort guarantee — weaker than
   what `translate_all`/`preserve_source` get from checks 1–4, but a
   genuine check where before there was none.

## Exit codes are a CONTRACT (#398)

This gate's exit code is consumed as a VERDICT, not merely as pass/fail, so
what lands on each code is load-bearing:

- **0** -- the candidate passes every check.
- **1** -- the CANDIDATE's own content is defective: any of the six checks,
  the structural self-check below, a `SOURCE DEFECT` finding, or a
  missing/malformed candidate draft. Every exit-1 condition is one that
  re-running the identical translation cannot clear.
- **2** -- usage, environment, or source availability: a malformed `seg`,
  PyYAML absent, an unreadable/malformed ownership marker or `profile.yml`,
  a missing or unreadable SEGPACK, and any otherwise-uncaught exception.

`codex_job.py` reads exit 1 -- and only exit 1 -- as a permanent content
rejection, and writes a TERMINAL `blocked`/`translate-rejected` ledger
fragment on the strength of it, which takes the segment out of automatic
re-dispatch on both dispatch paths. So widening what exits 1 terminally
blocks segments whose real problem was mechanical, and narrowing it puts a
permanently-failing segment back into an unbounded paid retry loop. A
`SOURCE DEFECT` stays exit 1 deliberately: it is permanent until the source
is repaired, which is the property that matters to that consumer.

Existing consumers are unaffected by the split: `codex_job.py`'s gate helper
tests `returncode == 0`, and `final_audit.py` calls `validate()` in process
and never reads an exit code.

## Structural self-check against `draft.schema.json`

`validate_draft.py` also runs a structural self-check of the draft file
against `draft.schema.json` (hand-rolled, no external `jsonschema`
dependency for this one script). The ownership split here is deliberate and
must not be blurred:

- **`draft.schema.json`** is a MODE-NEUTRAL structural superset only —
  container shapes (`{seg, blocks{}, footnotes{}, verses{}, names[],
  notes[]}`), the same across every `verse_policy.mode` including `skip`.
  There is no `if/then` in the schema keyed on `verse_policy.mode` — six-way
  branching in a schema was judged more complexity than the value
  warrants, so the verse-policy table is documented as plain-English
  rules, not a formal discriminated union in JSON Schema.
- **Within draft validation, `validate_draft.py` is the sole authority for
  anything that varies BY `verse_policy.mode`** — which of
  `rendered`/`literal_gloss` are required, forbidden, or conditional
  (check 5 above). It also owns the draft-validation checks that branch on
  `footnotes.apparatus_policy` (checks 4 and 6 above) and the
  untranslated-sentinel scan (read from `validation.untranslated_sentinel`
  in `profile.yml`, never a hardcoded literal — see the shipped
  `profile.example.yml`'s `"нет перевода"` example value).

A draft can be fully `draft.schema.json`-valid while still failing
`validate_draft.py` outright (a missing verse key, a duplicated
placeholder) — or being semantically wrong for the active mode (a missing
`literal_gloss` under `full_rhymed_plus_literal`). Every one of those
failures is caught only by `validate_draft.py`, never by the schema alone.
There is no standalone `verse.schema.json` file.

## Adapt points (what a generalizing implementation branches on)

- Verse-section content checks (check 5) branch on `verse_policy.mode`.
- Footnote checks (checks 4 and 6) branch on `footnotes.apparatus_policy`.
- The untranslated-sentinel string is read from
  `validation.untranslated_sentinel` in `profile.yml`, never hardcoded.
- Reads/writes exclusively `draft_path(seg) = segments/{seg}.draft.json` —
  no language suffix. The real source script hardcodes
  `segments/{seg}.ru.draft.json`; a generalized port that keeps that
  suffix is a bug, not a faithful port.

## Mandatory adversarial pass before trusting the gate on real data

Before relying on this validator for a real project, prompt a reviewer
explicitly to hunt **false-negatives** — "what malformed input makes this
gate pass wrongly": drop/duplicate/reorder/empty/type-coerce a key,
set-membership where a bijection is actually needed, multiset where
sequence is actually needed, byte-compare where whitespace-normalization is
actually needed. This is the same process that found the two concrete
false-negatives (checks 3 and 4) baked into the six checks above; treat any
newly-found hole the same way.

## Regression-lock every hole found

`tests/validate_draft.test.py` (shipped, generic) has one injected-defect
test per known failure class — an empty footnote, a swapped verse
placeholder, a dropped sentinel, a whitespace-only "distinct" verse, and a
dropped `body_refs_only` marker — each of which MUST fail the gate.
Reverting the fix for any one of them must break its test. Any new hole
found by the adversarial pass gets its own new injected-defect test before
the fix is considered done.

**1.4.1:** Draft `seg`-identity: `validate_draft.py` and `draft_ready.py`
now require the draft's top-level `seg` to equal the requested segment id
(a mislabeled/cross-wired `seg01.draft.json` carrying `seg:seg02` previously
passed both gates). Regression-locked by
`tests/seg_identity_enforced.test.py`, a dedicated fixture (not folded into
`validate_draft.test.py`'s injected-defect suite because the hole spans both
scripts).

## `draft_ready.py` — a separate, narrower job

`draft_ready.py` answers a different, cheaper question: has the async
translator *delivered* a complete file at all — distinct from *is it
good*. It is a separate readiness probe that gates the review step of the
Workflow template, separate from `validate_draft.py`'s structural quality
gate. The split is what prevents a Claude fix-agent from ever ending up
authoring a missing translation from scratch: a fix agent only ever edits
an existing draft that has already passed `draft_ready.py`, never
originates new translated content.

**1.2.0:** `draft_ready.py` gains `--expect-token TOK` — READY only when
the on-disk draft's `dispatch_token` also equals `TOK` (backward-compatible
when the flag is omitted), so a straggler draft from an interrupted OLD run
can never be accepted as this run's own delivery. `review_ready.py` is the
new, sibling readiness probe for the review point — see
`references/ledger-and-resumability.md`'s `dispatch_token`/commit-gate
chain and `references/orchestration-and-batching.md`'s shared
DISPATCH → WAIT → CONSUME pattern for the full mechanics; both scripts
share the byte-identical `_SEG_ID_RE`/`validate_seg()` and the same
self-anchoring discipline this file's §3 "canonical path invariants"
sibling doc (`references/ledger-and-resumability.md`) already documents.

## The post-extraction gate (`validate_extraction.py`)

`validate_extraction.py` is the false-green gate for the *extraction* stage —
the earlier sibling of `validate_draft.py`, run once at W2 the moment the
adapter's extractor produces `manifest.json`, before any draft exists. The
pipeline advances only on its exit 0. For `gutenberg_epub`/`plain_text`,
`extract.py` (adapted from `extract.py.template`) IS that producing
extractor. For a `custom` source, the *co-designed* extractor at
`scripts/custom_extractors/<value>` produces `manifest.json` instead —
`extract.py` on disk is only Step 0a's unadapted template copy, never run;
see [`source-format-adapters/custom.md`](./source-format-adapters/custom.md).
The two ways this gate closes the false-green hole below are described in
terms of `extract.py` because that's the shipped, template-based case
(`gutenberg_epub`/`plain_text`); §2 (the region-hash pin) does **not** apply
to a `custom` source — see "The honest residual" below.

It exists to make the "editing a self-check to make it pass" anti-pattern
above structurally harmless. `extract.py` runs its own in-file self-check
suite (the sentinel-delimited `# BEGIN SELF-CHECK REGION` …
`# END SELF-CHECK REGION` block — see
[`source-format-adapters/gutenberg-epub.md`](./source-format-adapters/gutenberg-epub.md)),
but that suite lives in a file each project is expected to hand-adapt. A
hand-edited `extract.py` that skips, weakens, or fakes its own enforcement
could otherwise manufacture a green `manifest.json`. `validate_extraction.py`
closes that hole two ways (for `gutenberg_epub`/`plain_text`; see below for
`custom`):

1. **Independent re-derivation of the manifest-derivable invariants.** It
   self-anchors to the plugin's own install path (like `profile_validate.py`),
   is **never copied to the durable root**, and is **not** a bundle member —
   so it cannot itself be hand-edited as part of a project's `extract.py`
   adaptation. It loads the produced `manifest.json` plus the profile and
   **re-derives every manifest-derivable invariant from scratch**, ignoring
   whatever result `extract.py`'s own checks claimed: block-id uniqueness,
   spine order, segmentation-nonempty, body-files-yield-segments, — as of
   #761 — spine-yields-body-files (the spine classifies ZERO items as `body`,
   so the manifest carries no manuscript at all; this is exactly the case
   body-files-yield-segments is deliberately gated off for, and it is a
   GATE-ONLY check with no counterpart in the extractor's own suite),
   no-pseudo-segments-from-notes, the footnote bijection + sentinel-uniqueness
   (or, under `body_refs_only`, body-ref-marker well-formedness/uniqueness —
   branching on `footnotes.apparatus_policy` exactly as the extractor does),
   frontback inventory, verse-placeholder uniqueness/mounting, verse
   plain-text non-emptiness, the per-segment word cap, the full
   verse-count reconciliation, and — as of #397 — that no content unit is
   empty for the translator (`no_untranslatable_empty_blocks` for blocks a
   segment cites, `no_empty_footnote_definitions` for footnote definitions
   under a footnote-carrying `apparatus_policy`). Any failure is FATAL. A
   green manifest a
   tampered extractor produced still fails here, because this gate never trusts
   the extractor's self-report — it recomputes each invariant from the
   manifest itself.
2. **Self-check region hash pin.** It computes a normalized hash of
   `extract.py`'s self-check region (the text strictly between the two
   sentinel lines) and compares it against the shipped
   `CURRENT_EXTRACTOR_SELFCHECK_HASH`. A missing/malformed region, or a hash
   mismatch, is FATAL — naming the "editing a self-check to reach green"
   anti-pattern and pointing genuine gaps at a plugin issue.

**The honest residual.** Three of the extractor's self-checks —
`body_coverage_no_holes`, `no_orphan_footnote_continuation`, and
`verse_no_uncovered` — depend on intermediate parse state that is **not**
recorded in `manifest.json`, so `validate_extraction.py` cannot independently
re-derive them. For `gutenberg_epub`/`plain_text` they are covered by the
**region hash pin only**: if the self-check region is byte-for-byte the
shipped implementation (hash matches), these three are trusted to have run
as shipped; the gate does not re-prove them from the manifest. This is a
deliberate, documented limit — stated plainly so nobody mistakes the hash
pin for a full independent re-derivation of these three.

**For a `custom` source, the region pin is SKIPPED outright** (not merely
"trivially passes") — `validate_extraction.py` detects `source.format:
custom` and never even reads `extract.py`'s self-check region for the pin.
Pinning it would be worse than a documented limit: `extract.py` there is
Step 0a's unadapted template copy, so the pin would vacuously match
`CURRENT_EXTRACTOR_SELFCHECK_HASH` every time and *certify nothing* about
the extractor that actually ran (`scripts/custom_extractors/<value>`). The
gate still runs re-derivation (§1 above) against the custom-produced
`manifest.json` in full — only the region pin is skipped — so a `custom`
run's exit code depends solely on that re-derivation. The custom extractor's
own equivalent of these three residual checks is the co-designing project's
own responsibility; see
[`source-format-adapters/custom.md`](./source-format-adapters/custom.md).

Invocation mirrors `profile_validate.py`'s exit-code discipline (exit `0` =
every check passed, `1` = any check or the hash pin failed, `2` = usage/env
error such as bad args or an unreadable file):

```
python3 {{PLUGIN_ROOT}}/assets/scripts/validate_extraction.py \
  --manifest <durable manifest.json path> \
  --extract  <durable extract.py path> \
  --profile  <profile.yml path>
```

## What this gate does NOT check: reproduced source text

The six checks compare KEY SETS, placeholder multisets and per-mode required
fields. **No text of any source span a draft reproduces is ever compared to
the segpack's `plain_text`.** A quoted Hebrew phrase inside an English draft
can lose a letter and every check above stays green — measured on a real book
at 206 letter-level differences across 4040 reproduced runs (#502).

`scripts/verbatim_census.py` (1.42.0) covers that population, and it sits
deliberately OUTSIDE the six checks rather than becoming a seventh:

- **It is not a gate.** Exit `0` whenever the census ran, however long the
  queue; exit `2` only for usage, environment or a malformed artifact. Nothing
  in the plugin dispatches it — not the W5 template's
  `draft_ready.py && validate_draft.py` accept condition, not
  `segment_dispatch_driver.py`'s sibling allowlist, not `final_audit.py`.
  It is an operator diagnostic, run by hand.
- **It never corrects.** The output is a reading queue, not a patch, and the
  script writes no file at all. The reason is measured, not cautious: on the
  population that was read word by word there were more cases where the DRAFT
  was right and the SOURCE was corrupt than cases where the draft was wrong,
  and no deterministic comparison separates those two.
- **It never suppresses.** Every non-verbatim run is listed; the class is a
  rank (tier 1 `letter_diff`/`no_source_run` → tier 4 `verbatim_other_unit`),
  and the tier is a likelihood heuristic rather than a consequence ordering.
- **Hebrew only**, and it refuses — exit 2, naming what it refused — rather
  than reporting an empty census, both for a unit whose block carries no
  `plain_text` and for a project whose source contains no Hebrew.

Because it joins no hashed bundle, adding it moved neither `plugin_bundle_hash`
nor `orchestration_bundle_hash`: no converged segment went stale.

## The visual-order advisory (`visual_order_scan`, 1.46.0, #489)

The same gate also carries one REPORT-ONLY scan, printed as a `WARN
visual_order_scan:` line on stderr and named in the final status line as
`(N ADVISORY)`. **It is not a check.** It never touches `derivable_ok` or
`region_ok`, so it can neither refuse an ingestion nor rescue a failing one, and
it is deliberately kept out of `run_derivable_checks()`'s results so no existing
check tuple or exit-code contract moves.

**What it is for.** A source EPUB converted from a PDF can carry RTL text in
VISUAL order. Extraction is byte-faithful — measured byte-identical against the
raw EPUB, and `segpack.py` is a pass-through by construction — so the mangling
is upstream and correct to preserve. But no deterministic gate in this pipeline
can see it: token counts, digests, schema validation and `validate_draft` never
read what a fragment MEANS. The victims are the LLM turns. On a live book this
produced both false reviewer findings against correct drafts and one
mistranslation, with subject and object swapped, that reached a converged draft
a full review round had already called clean. That is the gap this advisory
exists to make audible — it is the one class this document's gates cannot cover
by construction.

**What it detects, named honestly.** It is a *leading-terminal-punctuation
screen*: a token whose first character is terminal punctuation followed —
across any combining marks or bidi format controls — by an RTL letter. Logical
order cannot produce that, because a stop or comma is stored AFTER the word it
ends. It therefore detects visual-order *handling*, **not** word *reordering*,
even though reordering is what actually tears tokens.

Three false-negative classes, stated rather than implied away:

- an **unpunctuated** reversed run, which carries no signature at all;
- word **reordering** generally, for the same reason;
- any mangling whose punctuation is **not adjacent** to an RTL letter.

Do not "improve" this by swapping in a reversal-scoring detector. Two were
MEASURED against the known positive control and both returned clean: whole-block
reversal scoring and tail-window reversal scoring. A clean sweep from either
would have read as *no visual-order input found* — a false all-clear, which is
the exact failure this document exists to prevent.

**Measured behaviour, both directions.** On the positive control book: 921 hits
across 476 of 1212 RTL-bearing text units, 41 of 42 units flagged (the miss is a
front-matter unit whose RTL content is a single block), and both named control
blocks hit. Against known logical-order corpora — Hebrew pointed with 267
sof-pasuq occurrences and unpointed, Arabic, Persian, Urdu — **0 hits in 46 762
RTL tokens**. The ellipsis is excluded from the terminal set because a
sentence-initial elision is legitimate logical order; it contributed zero of the
921 hits, so the exclusion costs nothing measured.

**Scan population.** `blocks[*].plain_text` **plus** `verse.store[*].plain_text`
where `mount == "embedded"`. An embedded verse's text is lifted OUT of its
carrier block and replaced by a `⟦VERSE_…⟧` placeholder, so a blocks-only scan
would be blind to it; a standalone verse (`mount: "block"`) is already a
`blocks[]` entry and is not scanned twice. Those are the extractor's only
independent source-text stores.

**Evidence is codepoints, never glyphs.** Sampled tokens and histogram keys are
emitted `\uXXXX` / `U+XXXX`. A bidi-reordering terminal renders a corrupted RTL
token identically to an intact one — a finding on this very book was filed and
then retracted for exactly that reason — so evidence meant to be ADJUDICATED
cannot be rendered text.

**The verdict is not the scan's.** The WARN routes an operator to an
adjudication turn that reads the sampled units against the source and decides;
on a positive, the condition is recorded in the project's own `style_bible.md`
under `### E-traps`, which is the one artifact the translate, review and fix
turns all read. SKILL.md's W2 section carries the procedure and the clause, and
states the `style_contract_hash` cost of pasting it.

## The block-size census (`block_size_census`, #504)

The same gate also prints one REPORT-ONLY census for every schema-valid run: a
`NOTE block_size_census:` line on stdout, always, and — only when at least one
block crosses its threshold — a `WARN block_size_census:` line on stderr that
is also named in the final status line's `(N ADVISORY)` count. **It is not a
check.** Neither line touches `derivable_ok` or `region_ok`, so the census can
neither refuse an ingestion nor rescue a failing one, and it sits, like
`visual_order_scan`, outside `run_derivable_checks()`'s results so no existing
check tuple or exit-code contract moves.

A census that cannot be BUILT prints no NOTE and is named as a `scan
unavailable` advisory instead; one that cannot be PRINTED stays silent. Neither
touches the exit decision.

**What it is for.** A source block — a member of some segment's `block_ids` —
can be a wrap/extraction artifact: a converter joining a whole narrative, or
several paragraphs, into one block. Extraction preserves whatever the converter
produced, faithfully; the artifact is upstream. No check in this gate is
size-aware at the block level to catch it — the only existing size check is the
per-segment word count, and a block many times the size of this book's other
blocks passes it exactly as an ordinary one does.

**What it detects, named honestly.** It is a *relative size outlier screen*: a
block whose character count is at least 10x this book's own p90 block size,
over the population described below. A genuinely long paragraph produces
exactly the same signal as an artifact — the census has no way to tell them
apart, and does not attempt to.

False-negative classes, stated rather than implied away:

- characters, not words;
- blocks only — an embedded verse's text is lifted OUT of its carrier block
  and replaced by a placeholder, so it is not in this population;
- silent below 30 blocks (the NOTE still prints the count, so the silence is
  visible, never withheld);
- blind once more than roughly 10% of a book's blocks are themselves
  artifacts, because the p90 reference then sits on them;
- blind to a book whose blocks are uniformly chapter-sized — a relative
  measure has no reference there;
- the threshold is calibrated on five manifests, of which only one is
  positive.

**Measured behaviour.** Over the five manifests measured (max block size over its
own p90): 21.03 on the book carrying the known artifact (`ssk-he-en/vol2`, block
`PARA:seg21:0001`, 17 896 characters), and 4.97 / 4.86 / 5.78 / 6.04 on four
clean books. 10 clears the noisiest clean book (6.04) by a 1.66x margin, and
the true positive (21.03) clears 10 by a 2.10x margin — measured as the widest
two-sided margin among p90/p95/p99 reference choices. The median is not used as
the reference for the same reason it cannot serve as a general baseline: on the
positive book the median block is six characters (1170 short dialogue
paragraphs), and 600 of that book's 1212 blocks sit at or above 10x it — a
threshold keyed to the median would flag half the book.

**Scan population.** The distinct blocks named by some segment's `block_ids`
whose `plain_text` is non-empty — never a `kind` filter, since a real manifest
on disk carries `kind` values outside the schema enum and filtering on it can
silently empty the population. This excludes `FN:` footnote-definition blocks
and unattached front/back matter, including the ~18 800-character Project
Gutenberg licence block present in every Gutenberg book. A repeated block id
inside `block_ids` is counted once: nothing in the schema forbids a repeat and
no derivable check rejects one -- measured, by feeding a manifest whose
`block_ids` repeats an id through `run_derivable_checks()` and watching all
fourteen pass -- so counting occurrences would move both the population and the
reference with no block behind the difference.

**The verdict is not the census's.** A fired WARN routes an operator to an
adjudication turn that reads the named block(s) against the printed source and
decides whether it is a genuinely long paragraph or an artifact; on the latter,
the finding is recorded in that segment's own draft `notes[]` array, not the
manifest — no draft exists yet at W2, and a manifest segment object is
`additionalProperties: false` with no `notes` field. SKILL.md's W2 section,
'Oversized source block', carries the procedure. Nothing is re-paragraphed
automatically: re-cutting a block changes the segpack's block-key set, which
`validate_draft.py` locks 1:1 against the draft.


## See also

- [`verse-policy.md`](./verse-policy.md) — the full six-mode
  `verse_policy.mode` table (translator instruction, required draft
  fields, validator behavior per mode) that check 5 and the schema
  ownership split both depend on.
- [`source-format-adapters/README.md`](./source-format-adapters/README.md)
  — the `footnotes.apparatus_policy` four-value table (`translate_all` |
  `preserve_source` | `omit_apparatus` | `body_refs_only`) that checks 4
  and 6 branch on, and where `body_ref_markers[]` is populated.
- [`ledger-and-resumability.md`](./ledger-and-resumability.md) — the
  `draft_path(seg)`/`review_path(seg)` canonical-path invariants, and
  `plugin_bundle_hash`'s inclusion of `validate_draft.py` as one of the
  scripts that directly shape translate/review/validation content.
- [`engine-loop.md`](./engine-loop.md) — where this gate sits in the
  translate → gate → review → fix loop.
