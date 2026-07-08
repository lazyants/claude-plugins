<!-- PROMPT_CONTRACT_VERSION: 1 -->
<!--
  glossary_TASK.template.md -- one-time-seed prompt contract for the
  canon/glossary-pass agent (the codex-glossary-pass).

  Step 0a copies this file ONCE to `${durable_root}/glossary_TASK.md` and
  never re-copies over it once it exists (see SKILL.md's Step 0a and
  references/ledger-and-resumability.md's canonical-path invariants).
  Hand-adapt the bracketed [PLACEHOLDER] spots below for THIS project right
  after the copy, then leave the file alone -- glossary-pass-wf.template.js's
  `glossaryPrompt()` re-reads `${durable_root}/glossary_TASK.md` fresh for
  every batch it dispatches, so any later edit here applies retroactively
  to every not-yet-resolved batch.

  This file holds the canonicalization RULES and PHILOSOPHY -- it is not
  the sole enforcement of the per-item output shape. That shape is
  schema-enforced independently, twice over: the Workflow-level `agent()`
  call passes `schema: CANON_BATCH_SCHEMA` (glossary-pass-wf.template.js),
  and `scripts/canon_validate.py` re-checks every batch item against
  `canon-batch.schema.json` before merging into `canon.json` (see
  references/canon-and-glossary.md). This file never overrides or loosens
  that contract -- it exists to carry THIS project's own naming
  conventions and judgment guidance, which no schema can express, and to
  restate the contract in full so a call reading only this file (plus its
  own dispatch prompt) is self-contained.

  On a resumed project, `scripts/profile_validate.py` checks the
  `PROMPT_CONTRACT_VERSION` marker above against its own hardcoded
  `CURRENT_PROMPT_CONTRACT_VERSION` and FATALs on a missing, malformed,
  duplicated, or non-leading marker. Bump this marker (and that constant)
  only when this file's own I/O contract changes in a way an existing
  project must consciously re-adopt -- never silently.
-->

# Task: Canon and Glossary Resolution ([SOURCE LANGUAGE] -> [TARGET LANGUAGE])

You are resolving proper-name and realia candidates for [PROJECT TITLE /
AUTHOR / PERIOD -- fill in] into `canon.json`, the frozen, cross-segment
name/realia glossary for this whole book. You resolve exactly ONE batch
per call. Style authority: `style_bible.md` -- section C for the
naming/title-mapping rule, section C-translit for this project's own
fixed practical-transcription rule.

## Why this exists (read once)

A name, title, or realia term must translate identically everywhere it
appears in the book, independent of which segment happens to be in
context at the moment a translator drafts it. `canon.json` exists so that
decision is made once, here, validated, and frozen -- never re-decided
inside a segment's own translation pass, and never re-litigated by a
later batch. **Never re-decide or override any `source_form` already
present in `canon.json`'s own `entries{}`** -- this batch resolves only
the new candidates you are handed below, which were already filtered
against the current `canon.json` before you were dispatched.

## Input

Your dispatch prompt (`glossaryPrompt(batch)` in
`glossary-pass-wf.template.js`) hands you, fresh at call time -- never
baked into this file:

- `candidates[]` -- this batch's rows, deterministically extracted by
  `bootstrap_names.py` (`name`, `freq`, `n_segments`, `mid_sentence`,
  `multiword`, `abbrev`, `likely_name`). These are recall-oriented
  heuristics, not a verdict -- deciding whether a candidate is actually a
  proper name at all, and what it should become, is your job, not the
  extractor's.
- `research_mode` (`live` | `offline`) for THIS run -- an explicit,
  human-declared statement of whether you actually have working
  web/research access right now. Governs whether `basis: "established"`
  is available to you at all (see below).

## What to decide, per candidate

For every candidate in the batch, in the same order, decide exactly one
canon-batch item:

- **`source_form`** -- the candidate's own `name` field, copied verbatim.
- **`is_proper_name`** -- `false` when the candidate is not actually a
  proper name at all (a frequent common word, an interjection, a bare
  title, or a sentence-initial capitalization artifact). Such a candidate
  always gets `disposition: "review_queue"` too, never `"accepted"`.
- **`disposition`** -- `"accepted"` once you have a confident resolution;
  `"review_queue"` whenever it still needs a human's later attention -- a
  disputed transcription, several different real people sharing one
  surname, not enough context in this batch alone, a non-name candidate as
  above, or the offline `SOURCE_UNAVAILABLE:` case below.
- **`basis`** (accepted items only) -- exactly one of:
  - **`established`** -- a real, already-current target-language form
    exists for this name. Confirm it through an actual reference source
    (never from memory alone) and record the URL in `source`. Forbidden
    outright when `research_mode: offline` -- see below.
  - **`transliterated`** -- no established form exists; apply this
    project's own fixed practical-transcription rule
    (`style_bible.md` section C-translit) instead.
  - **`title`** -- an honorific/role phrase (e.g. a form meaning
    "Monsieur the Prince" or "the Queen Mother") -- `canonical_target_form`
    holds the unpacked target-language phrase, per this project's own
    title-mapping table (`style_bible.md` section C). If the underlying
    surname is ALSO present as its own separate candidate in this same
    batch, resolve that one on its own merits instead of folding it into
    the title entry.
  - **`not_a_name`** -- paired with `is_proper_name: false`.
- **`confidence`** (accepted items only) -- `high` | `medium` | `low`.
  `low` candidates are still `disposition: "accepted"` if you have a real,
  if tentative, resolution; genuinely unresolved candidates belong in
  `review_queue` instead, not a low-confidence accepted guess dressed up
  as one.
- **`note`** -- required when `disposition` is `"review_queue"`; explain
  briefly why the candidate is queued rather than resolved. Optional
  otherwise, but use it for anything a later human reader would want to
  know (e.g. `NEW:` context copied over from a translator's own draft
  notes, if this batch is resolving a candidate that was already flagged
  `NEW:` mid-segment).

### research_mode policy

- **`offline`** forbids `basis: "established"` outright, no exception --
  use `transliterated` when the fixed rule in `style_bible.md` section
  C-translit is enough on its own, or route the candidate to
  `review_queue` instead, with a `note` starting with the literal prefix
  `SOURCE_UNAVAILABLE:`. Never fabricate a citation to get around this.
- **`live`** allows `established`, but only together with a real, citable
  reference URL -- never an invented one.

Word-sense and realia accuracy applies to names too: a title or place name
can carry a period/domain-specific sense that differs from its modern
one. There is no separate trap-string gate for THIS file the way
`translate_TASK.md`/`review_TASK.md` carry one (`scripts/scaffold_validate.py`'s
trap-string scan only covers those two files) -- log a genuine discovery
in `style_bible.md`'s own E-traps section instead, so future batches and
segments benefit from it too.

## Output -- EXACTLY this JSON (no markdown fencing)

Write a plain JSON array, one item per candidate, in the same order you
were given, to the path your dispatch prompt names (normally
`${durable_root}/glossary/out_{index}.json`) -- no markdown code fence, no
comment, nothing else in the file:

```
[
  {
    "source_form": "<candidate's own name field, copied verbatim>",
    "is_proper_name": true,
    "disposition": "accepted",
    "canonical_target_form": "<resolved target-language form>",
    "basis": "established|transliterated|title|not_a_name",
    "source": "<reference URL -- required and must be a non-empty URI when basis is established>",
    "confidence": "high|medium|low"
  },
  {
    "source_form": "<candidate's own name field, copied verbatim>",
    "is_proper_name": false,
    "disposition": "review_queue",
    "note": "<why this candidate is queued rather than resolved>"
  }
]
```

matching `canon-batch.schema.json` exactly (see
`references/canon-and-glossary.md`) -- a discriminated union over each
item's own `disposition` field, never a bare array of resolved entries.

Self-check before returning: run
`python3 ${durable_root}/scripts/canon_validate.py --research-mode <live|offline> --batch <the file you just wrote>`
and confirm it prints a line with `"success": true`. If it prints a line
with `"success": false`, it names every offending item -- fix each one in
your own array (reassign `basis`/`disposition`/`note` as the rules above
require; never weaken the offline backstop, never fabricate a source URL
to make the check pass), rewrite the file, and repeat until it prints
`"success": true` -- only then does `canon.json` actually hold this
batch's decisions.

Final response: exactly the same validated array you just wrote, in the
same order as the candidates you were given, matching
`canon-batch.schema.json`. The frozen decision lives in `canon.json`, not
in your response text.
