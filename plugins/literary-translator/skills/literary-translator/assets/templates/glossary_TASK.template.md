<!-- PROMPT_CONTRACT_VERSION: 3 -->
<!--
  glossary_TASK.template.md -- one-time-seed prompt contract for the
  canon/glossary-pass agent (the codex-glossary-pass).

  Step 0a copies this file ONCE to `${durable_root}/glossary_TASK.md` and
  never re-copies over it once it exists (see SKILL.md's Step 0a and
  references/ledger-and-resumability.md's canonical-path invariants).
  Hand-adapt the bracketed [PLACEHOLDER] spots below for THIS project right
  after the copy, then leave the file alone -- glossary-pass-wf.template.js's
  `batchDispatchPrompt()` re-reads `${durable_root}/glossary_TASK.md` fresh
  for every batch it dispatches, so any later edit here applies
  retroactively to every not-yet-resolved batch.

  This file holds the canonicalization RULES and PHILOSOPHY -- it is not
  the sole enforcement of the per-item output shape. That shape is
  schema-enforced by `scripts/canon_validate.py`, never by an `agent()`
  `schema` param: the batch dispatch call in `glossary-pass-wf.template.js`
  is deliberately schema-less fire-and-forget (see
  references/workflow-schema-validation.md's "shared codex work-call
  pattern"), so the dispatched agent is instead required to self-check its
  own fragment, before returning, by running
  `canon_validate.py --check-batch` against `canon-batch.schema.json` --
  and, later, a separate disk-independent `--verify-merged` call re-checks
  the eventual `canon.json` merge itself (see references/canon-and-glossary.md).
  This file never overrides or loosens that contract -- it exists to carry
  THIS project's own naming conventions and judgment guidance, which no
  schema can express, and to restate the contract in full so a call
  reading only this file (plus its own dispatch prompt) is self-contained.
  The dispatch prompt's OWN self-check command is always authoritative over
  this file's own self-check prose below -- `glossary-pass-wf.template.js`
  is regenerated fresh from the plugin's current copy every run (never a
  one-time-seed file the way this file is), so a resumed project whose
  copy of this file predates a plugin update still gets the current
  command from its dispatch prompt.

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

Your dispatch prompt (`batchDispatchPrompt(batch)` in
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
  - **`sense_translated`** -- the candidate is a **speaking name** whose
    correct rendering is a deliberate sense-translation rather than a
    transcription (`style_bible.md` section C). `canonical_target_form`
    holds the sense-rendering itself; `is_proper_name` must be `true`;
    `note` is required and must explain the sense choice; `source` is
    forbidden -- `sense_translated` is a project-specific editorial
    rendering, never a citable established form. **`established` WINS**
    over `sense_translated` whenever a citable conventional target form
    actually exists -- cite it under `established` instead; reserve
    `sense_translated` for exactly the case where no established-form
    claim can be made at all. Legal under `research_mode: offline` (it
    makes no citation claim -- see below).
  - **`not_a_name`** -- paired with `is_proper_name: false`.
- **Nicknames, epithets, and aliases -- resolved independently, never
  inherited from a referent.** A salon nickname, epithet, sobriquet, or
  alias is its own surface form, never shorthand for its referent's
  `canonical_target_form`. Three rules apply together:
  1. **Orthographic sharing only.** Only true orthographic spelling
     variants of the same surface name (e.g. `Sarrasin` / `Sarrazin`) may
     ever share one `canonical_target_form`. A nickname and the real name
     it refers to are not spelling variants of each other, however
     well-known the identity link.
  2. **Resolve on its own merits.** Decide the nickname's own
     `canonical_target_form` under the `basis` rules above -- usually
     `basis: "transliterated"` (e.g. the classical epithet `Sapho` ->
     `Сафо`), `basis: "established"` if a genuinely established target
     form exists for the nickname itself, or `basis: "sense_translated"`
     when sense clearly carries better than transcription and a clean
     sense-rendering exists (see the `sense_translated` bullet above --
     `established` still wins whenever a citable conventional form
     exists). Never assign it the referent's real-name form instead (e.g.
     never `Скюдери` for `Sapho`). When none of `transliterated`,
     `established`, or `sense_translated` cleanly applies, use
     `disposition: "review_queue"` with a note rather than a fabricated
     basis -- see the nicknames/speaking-names guidance in
     `style_bible.md`.
  3. **Record the identity link in `note` only.** When the nickname's
     referent is known, say so in `note` (or route to
     `disposition: "review_queue"` with a note) -- never by collapsing
     the two entries' `canonical_target_form` into one.
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
  C-translit is enough on its own, use `sense_translated` instead when the
  candidate is a speaking name with a clean sense-rendering (it makes no
  citation claim at all, so it stays legal under offline), or route the
  candidate to `review_queue` instead, with a `note` starting with the
  literal prefix `SOURCE_UNAVAILABLE:`. Never fabricate a citation to get
  around this.
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
`${durable_root}/glossary/runs/<run_id>/out_{index}.json` -- a fresh,
run-scoped path, never a single shared file every batch writes into) --
ATOMICALLY (a temp file in the same directory, then renamed into place),
no markdown code fence, no comment, nothing else in the file:

```
[
  {
    "source_form": "<candidate's own name field, copied verbatim>",
    "is_proper_name": true,
    "disposition": "accepted",
    "canonical_target_form": "<resolved target-language form>",
    "basis": "established|transliterated|title|sense_translated|not_a_name",
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
`python3 ${durable_root}/scripts/canon_validate.py --check-batch <the file you just wrote> --research-mode <live|offline> --expect-source-forms-file <the manifest file your dispatch prompt named>`
and confirm it prints a line with `"success": true`. If it prints a line
with `"success": false`, it names every offending item -- fix each one in
your own array (reassign `basis`/`disposition`/`note` as the rules above
require; never weaken the offline backstop, never fabricate a source URL
to make the check pass, never drop or add a candidate), rewrite the file
the same atomic way, and repeat until it prints `"success": true`. This
command checks only THIS fragment's own shape and its exact candidate
coverage against the manifest -- it never merges into `canon.json` itself;
never run `--batch` (the old, mutating, single-fragment merge mode) here.

Final response: exactly the line your dispatch prompt asks for (e.g.
`FRAGMENT {index}`), once the self-check above prints `"success": true`.
The fragment file you just wrote is not yet the frozen record on its own:
only after every batch's own fragment has independently passed this
self-check does a separate, later, ONE serialized merge step fold every
batch's fragment into `canon.json`, followed by an independent
disk-verify check -- see `references/canon-and-glossary.md`.
