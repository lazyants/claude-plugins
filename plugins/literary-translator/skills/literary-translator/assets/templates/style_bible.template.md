<!--
  The style_contract span (sections A-F) is wrapped in a pair of
  STYLE_CONTRACT_BEGIN / STYLE_CONTRACT_END HTML-comment markers -- placed
  immediately before section A and immediately after section F, section G
  staying outside them. `cache_key.py`'s `compute_style_contract_hash`
  hashes exactly the bytes strictly between those two markers to produce
  the global `style_contract_hash` cache-key field, and `scaffold_validate.py`
  enforces at W1 that exactly one of each exists, in order. These markers
  are therefore load-bearing: never remove, duplicate, or reorder them, and
  never move section G inside them. (This paragraph deliberately spells the
  marker names without their comment delimiters: nothing parses this file as
  HTML -- `scaffold_validate.py` counts marker occurrences file-wide and
  `cache_key.py` requires each to be unique, so a second literal copy here
  would read as a duplicated marker and FATAL.)

  Section labels A-G below are load-bearing identifiers, not free-form
  headings -- do not renumber or relabel them. Fourteen shipped sites address
  them by label: the glossary worker's own prompt (`glossary_TASK.template.md`),
  `references/canon-and-glossary.md`, `references/language-pair-parameterization.md`,
  and `canon_validate.py`, which prints "style_bible.md section C" into an
  operator-facing refusal. Nothing validates any of those references, so a
  relabelled section leaves every one of them aimed at a heading that no
  longer exists, silently.

  What belongs inside those markers: rule text a translator or reviewer
  must APPLY, plus the boundary case that keeps each rule from being
  over-applied -- nothing else. The reasoning that produced a rule, the
  measurements behind it, and the running log of a campaign belong in a
  sibling file (`consistency_issues.md`), never in here: every byte
  between the markers is hashed into `style_contract_hash`, and every
  byte of this file is re-read by every translator and reviewer call.
  Do not restate a rule `translate_TASK.md`/`review_TASK.md`, the
  segpack, or `profile.yml` already owns -- two statements of one rule
  drift apart, and the freshly resolved surface is the one the pipeline
  actually obeys (verse policy, section E, is the standing example).
  Outside the markers, keep two things: the glossary summary a human
  skimmer needs, and the section-G tables -- those are read in full by
  every translate and review call, and carry what a one-segment reader
  cannot otherwise see. Being outside the markers means they are unhashed,
  not that they are decoration.
-->

# Style bible -- [PROJECT TITLE / AUTHOR / PERIOD -- fill in]

> Living document, read in full by every translator (codex) and reviewer (codex) call, every segment.
> Two parts with different invalidation scope: `style_contract` (A-F, global, changes rarely and on
> purpose -- its bytes are hashed into the `style_contract_hash` cache-key field, so editing it
> invalidates every already-converged segment by itself, with no version bump needed or available: this
> document carries no version field of its own that any script reads) and `glossary` (G, per-term, backed
> by `canon.json`, grows continuously via the codex-glossary-pass).
> `project.pipeline_version` in `profile.yml` is the operator's lever for the other case: an invalidation
> the hashed span cannot express, where the contract text is unchanged but what the pipeline does with it
> is not. Roles: Claude/the human maintain this document's FORM; every accuracy decision (canon name
> basis, established vs. transliterated, an address-register pair) is made by codex, never Claude.

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

This table IS where a recurring common-noun term of art lives -- `canon.json` cannot hold one, by design
(see `references/canon-and-glossary.md`). Nothing checks a rule stated only here, though. Each row worth
enforcing mechanically gets a machine-checkable twin in `profile.yml` under `validation.terms`, one bare
`source_form`/`target_form` pair, and W7 then reports any carrier translating the source form without the
pinned target form. Pin the INVARIANT part of the target form if the target language inflects by suffix.

- **Original-script parenthetical** -- whether this project renders the source-form / original-script name
  in parentheses on first mention, and if so, in which form.

<!-- LT_REQUIRED_FILL_BEGIN: name-display-parentheses -->
LT_PLACEHOLDER_UNFILLED -- on first mention, render the source-form / original-script name in parentheses?
YES/NO. If YES: which form -- original script, transliteration, or both? If transliteration (alone or
alongside the original script), which transliteration system (cite it by name -- a standard national /
academic romanization scheme, not an ad hoc one)?
<!-- LT_REQUIRED_FILL_END -->

- Nicknames / speaking names -- translate the sense where the source clearly intends one; keep the
  original alongside it when it matters and the sense doesn't carry over cleanly.

### C-translit. Practical source -> target transcription rule (the fixed rule for names with no established form)

<!-- LT_REQUIRED_FILL_BEGIN: translit-rule -->
LT_PLACEHOLDER_UNFILLED -- state your book's fixed source -> target practical transcription rule here: the
sound-by-sound (or letter-by-letter) mapping this project applies uniformly to every name that doesn't
have an established target-language form. Base it on this language pair's own standard practical-
transcription practice where one exists. Cases the rule doesn't cleanly resolve go to the `REVIEW:` queue
(section G) for manual confirmation before the mass-translate batches run at scale.
<!-- LT_REQUIRED_FILL_END -->

### D. Formatting

Footnote numbering and marker style are NOT yours to set, whatever you choose below: the apparatus is
the source's own, and there is no translator-note namespace beside it. What section D fixes is the FORM the
gloss takes, and the unit it goes in.

<!-- LT_REQUIRED_FILL_BEGIN: formatting-conventions -->
LT_PLACEHOLDER_UNFILLED -- state this project's own conventions: dialogue/direct-speech punctuation;
quotation-mark style (and how it differs for nested/embedded quotes or titles); italics (foreign-language
insertions, titles of works, the source's own emphasis); how numbers, dates, and money/measurement realia
are rendered; and the FORM of the gloss for a realia term at its first appearance -- parenthetical, an
em-dash aside, italics: pick one, apply it consistently, and put it in the translated unit that already
carries the term. Section E's embedded-third-language span refers back to the form fixed here.
<!-- LT_REQUIRED_FILL_END -->

### E. Techniques and hard cases

- **Verse** -- if this book has verse, the actual per-verse handling (literal only, rhymed, mixed by
  length, ...) is `profile.yml`'s `verse_policy.mode`, resolved fresh into every dispatch prompt (see
  `references/verse-policy.md`) -- never hardcoded here or in `translate_TASK.md`/`review_TASK.md`.
- **Embedded third-language text** (a language other than both the source and target -- e.g. a classical
  language, an older stage of the source language, or a foreign-to-both aside) -- ALWAYS glossed in-text:
  keep the original AND give the target-language translation immediately alongside it. Never bury the
  gloss only in a translator's internal notes.

<!-- LT_REQUIRED_FILL_BEGIN: embedded-third-language-convention -->
LT_PLACEHOLDER_UNFILLED -- state this project's own fixed convention for embedded third-language text (a
language other than both source and target appearing inline in the source -- e.g. Yiddish inside a Hebrew
source, Latin inside an English source, an older stage of the source language, ...): romanize it or
translate it outright; the gloss format (the same form section D fixes for its realia gloss -- pick one and
apply it uniformly); and whether the kept original is set off from the surrounding target-language prose in
a particular way (parentheses, italics, a distinct marker).
If this project's source has no embedded third-language text at all, state that explicitly here rather
than leaving this unfilled.
<!-- LT_REQUIRED_FILL_END -->

- **Word-sense / realia accuracy** -- a notable word or reference may have meant something different in
  the source's own era/domain than its first present-day sense. This is a first-class, explicitly named
  review dimension (see `references/engine-loop.md`'s R6), not folded into generic accuracy.
- **Deliberate ambiguity is content, and reproducing it is the job** -- this is a literary TRANSLATION,
  not a commentary on the source. Where the author leaves something open on purpose -- hearsay framing
  (`they say that ...`), a period euphemism the passage then undercuts, a word the narrator declines to
  make explicit -- the target text reproduces the opening. **A rendering that is unambiguous where the
  source is not has changed what the author asserted**, and that is a defect no matter how well the
  stronger reading is attested.
  The discriminator matters, because the rule above pulls the other way and a reviewer cannot tell the
  two apart by feel: a genuinely missed idiom leaves the target thinner at that WORD; a deliberate
  ambiguity is thin at the word and load-bearing in the PASSAGE. So before calling a rendering weak,
  read what the surrounding sentences do with it -- an ambiguity the narrator goes on to exploit,
  attribute to gossip, or dismantle is the author's device, not the translator's omission. The stronger
  reading never enters the narrator's voice.
- **A repetition the author uses as a link** -- when the source repeats a word about one referent AND the
  passage leans on that repetition (the later occurrence answers, echoes or reinterprets the earlier one),
  the link is content: keep ONE target lexeme across the pair wherever the target language permits it.
  Not every repeated source word is such a link, and this rule does not make a differing rendering a
  defect by itself -- sense, collocation, register and grammar routinely require different target words,
  and target inflection can rule out literal repetition outright. Establish that the passage uses the
  repetition before treating a difference as a defect; where it does and the target cannot repeat, carry
  the link by other means rather than silently dropping it. Worth naming because of how the real case
  presents: it surfaces as a word-sense finding against ONE of the two sites, and repairing that site
  alone strengthens one occurrence, drops the link, and can resolve an ambiguity the source kept open.

#### E-traps. Known traps discovered during this project (living, append-only -- starts empty)

Not pre-fillable at scaffold time -- a real trap can only be discovered once translation is actually under
way. Append one bullet per trap as it's found: the source term, the wrong (modern/first-sense) reading, the
right (period/domain-specific) reading, and which segment surfaced it. This is the project's own running
defense against the same mistake recurring in a later segment.

**A measured claim carries its own scope.** A bullet that reports a count names, in the same sentence, the
universe it was counted over ("all 7 title strings enumerated in `consistency_issues.md`", never a bare
"205 of 205 title sites") -- a total whose scope did not travel with it reads as a closed class when the
real class is still open. Where the class is open-ended, say so instead of reporting a total, and prefer
recording the enumeration, or a path to it, over the count.

**Timing, not content, is the constraint on appending here.** These lines sit inside the style_contract
span, so one more bullet moves `style_contract_hash` and flips every already-converged segment to `stale`.
That flip is bookkeeping, not an order to re-review anything (SKILL.md's R9). What it costs is that every
unit converged BEFORE the append is then refused by the W7 completeness gate and W9 assembly until it
converges again -- whenever the append lands, not only at the end. Two things make that affordable, in
this order: collect traps in `consistency_issues.md` as they surface and promote them here in one batch at
a batch boundary, so segments converging afterwards carry the new hash and are never flipped; and, if a
promotion has already flipped work you do not intend to re-review, set
`validation.admit_contract_only_stale: true` in `profile.yml`, which lets both gates admit a flipped unit
whose draft is unchanged since review and name it in their output (SKILL.md's R9, `#533`). That
declaration is the wrong answer when the promotion REVERSED an earlier rule rather than adding to it: the
segments converged under the old rule were told to do the thing you have just forbidden, and no hash can
tell the two cases apart.

### F. Reference samples (voice anchor -- fill in AFTER the W4 stress gate converges, not at scaffold time)

Not a required-fill span: this content doesn't exist yet when the project is first scaffolded (the stress
gate hasn't run). Once the W4 stress-gate segment converges, cite/quote it here as the prose voice anchor
every subsequent batch is told to match. If this project has an early landmark passage (e.g. a
particularly hard verse or set-piece) that converged cleanly and is worth citing as its own anchor, add it
here too. Filling this in at W4 is cheap by construction: only the stress-gate segment has converged by
then, so the `style_contract_hash` move it causes costs one segment's stamp, not a book's. Adding a later
anchor is a style_contract edit like any other -- the timing rule under E-traps above applies to it too.

<!-- STYLE_CONTRACT_END -->

---

## G. glossary -- per-term canons (populated by the codex-glossary-pass; not filled at scaffold time)

Not a required-fill span: the actual canon is built by `bootstrap_names.py` plus the codex-glossary-pass
(see `references/canon-and-glossary.md`), which runs after W2 extraction, not at W1 scaffold time. The full
canon lives in `canon.json` -- do not inline hundreds of entries into this always-loaded file. Once the
canon exists, record here: the frozen-as-of summary -- entry count by basis (`established` /
`transliterated` / `title` / `sense_translated` / `not_a_name`), plus how many entries still sit in
`review_queue` -- and a short table of the established forms most worth calling out for a human skimming
this document.

`segpack.py` injects `canon_names[]` (locked forms a translator must use verbatim), `new_names[]` (not
yet canonized -- the translator resolves by context and flags `NEW:` in its own notes) and `split_names{}`
(adjudicated homonym splits from `canon_senses.json`: one spelling, two or more distinct referents, each
sense carrying a `disambiguator`; the translator picks the sense per occurrence and flags `NEW:`, since a
homonym has no single frozen target form) into every segment.

Three of this section's sub-sections -- `G-cast`, `G-voices` and `G-motifs` -- are optional and ship
empty; an empty one is a legitimate final state for a book that doesn't need it. Like the rest of section
G they sit outside the style_contract markers, so filling one in mid-run moves no cache-key field and
binds the segments still to come without re-dispatching one that already converged (any later dispatch of
such a segment, for whatever other reason, reads this file in full and does see the addition).

| source form | canonical target form | basis |
|----|--------------|-------|

### G-address. Address-register matrix by person-pair (only if section B applies; PENDING -- fills in as `NEW:` pairs are resolved)

| person A | person B | A -> B | B -> A | basis |
|--------|--------|-----|-----|-----------|

### G-cast. Dramatis personae + one-paragraph synopsis (fills in once the recurring cast is known)

A translate or review call is given one segment and nothing else -- no neighbouring segment, no synopsis,
no cast list. Put that context here if this book needs it: one paragraph on what the book is about, then
one row per recurring person.

| person | who they are | relation to the others | what a translator must not get wrong |
|--------|--------------|------------------------|--------------------------------------|

### G-voices. Per-character voice (fills in as a character's own voice settles)

Section A fixes the narrator's voice book-wide and section B the address register between persons; neither
says how one character sounds. Record that here once it settles, so a reviewer holding one segment can
check a voice it cannot otherwise see.

| person | register | tics / habitual constructions | forbidden for this person |
|--------|----------|-------------------------------|---------------------------|

### G-motifs. Recurring phrases held to one rendering (fills in as a motif is recognized)

A phrase the author repeats on purpose is a link across segments (section E's repetition rule), and a
reviewer holding one segment cannot see the other end of it. Fix the rendering once here and cite where it
first occurs.

| source phrase | fixed target rendering | first occurrence |
|---------------|------------------------|------------------|

### Queues (discipline)

- `NEW:` -- a term/pair not yet in this document: the translator marks it, the codex-glossary-pass resolves
  it before the next batch starts.
- `REVIEW:` -- a `confidence: low` or disputed entry (see `canon.json`'s `review_queue`) -- needs manual
  confirmation against a second source before this project is treated as final.
- This document freezes at each batch boundary; the W6 consistency pass (`consistency_issues.md`) runs
  after every batch, before the next one starts.
