<!--
  style_bible.template.md -- one-time-seed style-authority scaffold.

  Step 0a copies this file ONCE to `${durable_root}/style_bible.md` and
  never re-copies over it once it exists (see SKILL.md's Step 0a).
  Hand-adapt every `LT_REQUIRED_FILL_BEGIN`/`LT_REQUIRED_FILL_END`
  HTML-comment marker span below for THIS project before W2 (Extract)
  starts -- `scripts/scaffold_validate.py` FATALLY rejects any span that
  still contains the literal sentinel `LT_PLACEHOLDER_UNFILLED`, naming
  the file and the specific marker id. NOTE: this very sentence
  deliberately never writes the marker's own opening/closing HTML-comment
  delimiters back to back -- doing so here, inside this explanatory
  header, would itself parse as a real (accidental) marker span to that
  same regex-based scanner, which does not understand comment nesting.

  Unlike `PLAN.md`, this file stays load-bearing for the whole life of the
  project: `translate_TASK.md`/`review_TASK.md` both name it as the style
  authority every translator (codex) and reviewer (codex) call must read
  in full, and `glossary-pass-wf.template.js`'s own dispatch prompt quotes
  two of its sections literally ("style_bible.md section C-translit").
  Section labels A-G below are therefore load-bearing identifiers, not
  free-form headings -- do not renumber or relabel them.

  Two different roles maintain this document, never conflated: Claude (or
  the human) curates its FORM -- structure, wording, which sections are
  even applicable to this language pair. Every ACCURACY decision it
  eventually records -- a name's canonical target form, whether a basis is
  `established` or `transliterated` -- is made by codex, through the
  codex-glossary-pass, never by Claude directly (see
  `references/canon-and-glossary.md`). style_contract (sections A-F) and
  glossary (section G) also have different invalidation scope: a
  style_contract edit is global and legitimately invalidates every
  segment; a glossary/canon edit invalidates only the segments that
  actually used the changed term (see `references/ledger-and-resumability.md`).

  The style_contract span (sections A-F) is wrapped in a pair of
  STYLE_CONTRACT_BEGIN / STYLE_CONTRACT_END HTML-comment markers -- placed
  immediately before section A and immediately after section F, section G
  staying outside them. `cache_key.py`'s `compute_style_contract_hash`
  hashes exactly the bytes strictly between those two markers to produce
  the global `style_contract_hash` cache-key field, and `scaffold_validate.py`
  enforces at W1 that exactly one of each exists, in order. These markers
  are therefore load-bearing: never remove, duplicate, or reorder them, and
  never move section G inside them. (This paragraph deliberately spells the
  marker names without their comment delimiters, for the same
  regex-nesting reason explained above.)
-->

# Style bible -- [PROJECT TITLE / AUTHOR / PERIOD -- fill in]

> `style_bible_version: v1-draft` -- bump when `style_contract` (sections A-F) changes in a way that must
> invalidate every already-converged segment (see `project.pipeline_version` in `profile.yml`).
> Living document, read in full by every translator (codex) and reviewer (codex) call, every segment.
> Two parts with different invalidation scope: `style_contract` (A-F, global, changes rarely and on
> purpose) and `glossary` (G, per-term, backed by `canon.json`, grows continuously via the
> codex-glossary-pass). Roles: Claude/the human maintain this document's FORM; every accuracy decision
> (canon name basis, established vs. transliterated, an address-register pair) is made by codex, never
> Claude.

---

## style_contract (global rules -- editing this section legitimately invalidates every segment)

<!-- STYLE_CONTRACT_BEGIN -->
### A. Register and voice

<!-- LT_REQUIRED_FILL_BEGIN: voice-and-register -->
LT_PLACEHOLDER_UNFILLED -- describe: the source's own genre/period/tone (what kind of text this is, and
what its narrative voice actually sounds like); the target-language voice this project is aiming for (e.g.
readable modern [TARGET LANGUAGE] with a light period patina -- state this project's own actual target,
don't assume that example); what to preserve from the source (colloquialism, irony, register shifts,
deliberate roughness) versus what is forbidden (bureaucratic flattening, source-syntax calques,
anachronistic modernisms).
<!-- LT_REQUIRED_FILL_END -->

### B. Formal/informal address register matrix (optional -- delete this whole section if [TARGET LANGUAGE] has no T-V-style distinction)

If applicable: state the default address form by relationship type (rank, familiarity, age, master/servant,
...), and how it shifts (intimacy, contempt, addressing a child, a heated exchange). Each new person-pair
whose address form is not yet obvious gets flagged `NEW:` here and resolved once, then held for the whole
book -- see the queue discipline at the bottom of this file and the G-address table below. If not
applicable, delete this section entirely rather than leaving it empty.

### C. Names, titles, realia (rule; the resolved canon itself lives in `canon.json`, section G)

- **Established** (an already-current target-language form exists) -- use it. Confirmed by codex through a
  real reference source, URL recorded in `canon.json`. Never decided from memory alone.
- **Transliterated** (no established form exists) -- apply the single fixed rule in section C-translit
  below, uniformly across the whole book.
- **Title/honorific mapping** -- this project's own fixed mapping from the source language's titles and
  forms of address to the target language (e.g. how to render an honorific placed before a surname, vs. in
  direct address; how to render standard noble/clerical/civic titles).

<!-- LT_REQUIRED_FILL_BEGIN: title-mapping -->
LT_PLACEHOLDER_UNFILLED -- list this project's own title/honorific mapping, one row per source-language
form: source form -> target-language rendering, plus any register note (e.g. "before a surname" vs. "in
direct address"). If the source language has no honorific/title system worth a fixed mapping, state that
explicitly here rather than leaving this unfilled.
<!-- LT_REQUIRED_FILL_END -->

- **Original-script parenthetical** -- whether this project renders the source-form / original-script name
  in parentheses on first mention, and if so, in which form.

<!-- LT_REQUIRED_FILL_BEGIN: name-display-parentheses -->
LT_PLACEHOLDER_UNFILLED -- on first mention, render the source-form / original-script name in parentheses?
YES/NO. If YES: which form -- original script, transliteration, or both? If transliteration (alone or
alongside the original script), which transliteration system (cite it by name -- a standard national /
academic romanization scheme, not an ad hoc one)?
<!-- LT_REQUIRED_FILL_END -->

- Nicknames / speaking names -- translate the sense where the source clearly intends one; keep the
  original in a translator's note when it matters and the sense doesn't carry over cleanly.

### C-translit. Practical source -> target transcription rule (the fixed rule for names with no established form)

<!-- LT_REQUIRED_FILL_BEGIN: translit-rule -->
LT_PLACEHOLDER_UNFILLED -- state your book's fixed source -> target practical transcription rule here: the
sound-by-sound (or letter-by-letter) mapping this project applies uniformly to every name that doesn't
have an established target-language form. Base it on this language pair's own standard practical-
transcription practice where one exists. Cases the rule doesn't cleanly resolve go to the `REVIEW:` queue
(section G) for manual confirmation before the mass-translate batches run at scale.
<!-- LT_REQUIRED_FILL_END -->

### D. Formatting

Fill in this project's own conventions: dialogue/direct-speech punctuation; quotation-mark style (and how
it differs for nested/embedded quotes or titles); italics (foreign-language insertions, titles of works,
the source's own emphasis); footnote numbering/marker style (this project's apparatus keeps the source's
own numbering; a translator's own added notes, if any, use a visibly distinct marker/namespace so the two
are never confused); how numbers, dates, and money/measurement realia are rendered, including the gloss
mechanism for a realia term at its first appearance (inline parenthetical vs. its own note -- pick one and
apply it consistently; a segment's own schema may not have a slot for a brand-new footnote, in which case
an inline gloss is usually the only mechanism actually available).

### E. Techniques and hard cases

- **Verse** -- if this book has verse, the actual per-verse handling (literal only, rhymed, mixed by
  length, ...) is `profile.yml`'s `verse_policy.mode`, resolved fresh into every dispatch prompt (see
  `references/verse-policy.md`) -- never hardcoded here or in `translate_TASK.md`/`review_TASK.md`.
- **Embedded third-language text** (a language other than both the source and target -- e.g. a classical
  language, an older stage of the source language, or a foreign-to-both aside) -- ALWAYS glossed in-text:
  keep the original AND give the target-language translation immediately alongside it. Never bury the
  gloss only in a translator's internal notes.
- **Word-sense / realia accuracy** -- a notable word or reference may have meant something different in
  the source's own era/domain than its first present-day sense. This is a first-class, explicitly named
  review dimension (see `references/engine-loop.md`'s R6), not folded into generic accuracy.

#### E-traps. Known traps discovered during this project (living, append-only -- starts empty)

Not pre-fillable at scaffold time -- a real trap can only be discovered once translation is actually under
way. Append one bullet per trap as it's found: the source term, the wrong (modern/first-sense) reading, the
right (period/domain-specific) reading, and which segment surfaced it. This is the project's own running
defense against the same mistake recurring in a later segment.

### F. Reference samples (voice anchor -- fill in AFTER the W4 stress gate converges, not at scaffold time)

Not a required-fill span: this content doesn't exist yet when the project is first scaffolded (the stress
gate hasn't run). Once the W4 stress-gate segment converges, cite/quote it here as the prose voice anchor
every subsequent batch is told to match. If this project has an early landmark passage (e.g. a
particularly hard verse or set-piece) that converged cleanly and is worth citing as its own anchor, add it
here too.

<!-- STYLE_CONTRACT_END -->

---

## G. glossary -- per-term canons (populated by the codex-glossary-pass; not filled at scaffold time)

Not a required-fill span: the actual canon is built by `bootstrap_names.py` plus the codex-glossary-pass
(see `references/canon-and-glossary.md`), which runs after W2 extraction, not at W1 scaffold time. The full
canon lives in `canon.json` -- do not inline hundreds of entries into this always-loaded file. Once the
canon exists, record here: the frozen-as-of summary (entry count, how many `established` /
`transliterated` / `title` / `not_a_name`, how many still in `review_queue`), and a short table of the
established forms most worth calling out for a human skimming this document.

`segpack.py` injects `canon_names[]` (locked forms a translator must use verbatim) and `new_names[]` (not
yet canonized -- the translator resolves by context and flags `NEW:` in its own notes) into every segment.

| source form | canonical target form | basis |
|----|--------------|-------|
| _(populated once the glossary-pass has run)_ | | |

### G-address. Address-register matrix by person-pair (only if section B applies; PENDING -- fills in as `NEW:` pairs are resolved)

| person A | person B | A -> B | B -> A | basis |
|--------|--------|-----|-----|-----------|
| _(populated as pairs come up, if section B applies to this language pair)_ | | | | |

### Queues (discipline)

- `NEW:` -- a term/pair not yet in this document: the translator marks it, the codex-glossary-pass resolves
  it before the next batch starts.
- `REVIEW:` -- a `confidence: low` or disputed entry (see `canon.json`'s `review_queue`) -- needs manual
  confirmation against a second source before this project is treated as final.
- This document freezes at each batch boundary; the W6 consistency pass (`consistency_issues.md`) runs
  after every batch, before the next one starts.
