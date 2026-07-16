<!-- PROMPT_CONTRACT_VERSION: 3 -->
<!--
  translate_TASK.template.md -- one-time-seed prompt contract for the
  per-segment translator agent.

  Step 0a copies this file ONCE to `${durable_root}/translate_TASK.md` and
  never re-copies over it once it exists (see SKILL.md's Step 0a and
  references/ledger-and-resumability.md's canonical-path invariants).
  Hand-adapt the bracketed [PLACEHOLDER] spots below for THIS project right
  after the copy, then leave the file alone -- mass-translate-wf.template.js's
  `translatePrompt()` re-reads `${durable_root}/translate_TASK.md` fresh for
  every segment it dispatches, so any later edit here applies retroactively
  to every not-yet-drafted segment.

  This file is deliberately verse-policy-NEUTRAL: it never hardcodes which
  verses[] fields a draft must contain. Those instructions are spliced into
  each segment's own dispatch prompt at run time from the current
  profile.yml (`verse_policy.mode`, see references/verse-policy.md) --
  never duplicated or pre-baked here, so this file never goes stale when
  that setting changes later.

  On a resumed project, `scripts/profile_validate.py` checks the
  `PROMPT_CONTRACT_VERSION` marker above against its own hardcoded
  `CURRENT_PROMPT_CONTRACT_VERSION` and FATALs on a missing, malformed,
  duplicated, or non-leading marker. Bump this marker (and that constant)
  only when this file's own I/O contract changes in a way an existing
  project must consciously re-adopt -- never silently.
-->

# Task: Literary Translation of One Segment ([SOURCE LANGUAGE] -> [TARGET LANGUAGE])

You are translating literary prose from [SOURCE LANGUAGE] into
[TARGET LANGUAGE] for [PROJECT TITLE / AUTHOR / PERIOD -- fill in]. You
translate exactly ONE segment per call. Input: `segpack_{SEG}.json`. Style
authority: `style_bible.md` -- read it in full before your first segment,
and re-read it whenever it changes.

## Input (segpack_{SEG}.json)

- `blocks[]` -- reading-order blocks; each carries an `id`, a `type`
  (e.g. HEAD/PARA/QUOTE/VERSE -- see this project's own block-id model),
  source text with placeholder sentinels inline, and a source HTML-ish
  rendering for context only.
- `footnotes[]` -- source-apparatus footnotes to translate (`n`, source
  text) -- whether they apply at all depends on this project's
  `apparatus_policy` (check `style_bible.md` / `profile.yml` if unsure).
- `verses[]` -- embedded verse (`vid`, source text, placement metadata).
  How to render each verse for THIS run arrives in your own dispatch
  prompt, spliced fresh from the current verse policy -- follow that
  instruction, not any example baked into this file.
- `canon_names[]` -- the source-form names/realia already canonized for
  this segment. `canon_map` (source form -> frozen `canonical_target_form`)
  carries the actual target forms -- render each canonized name using its
  `canon_map` target form's **stem/spelling, declined as the target
  grammar requires**: a correctly inflected/case form of the canonical
  stem is CORRECT; do not copy the citation form verbatim where grammar
  needs another case.
- `new_names[]` -- names/realia not yet canonized: choose a reasoned
  rendering (see `style_bible.md`'s naming-convention section) and record
  your choice in `notes` as `NEW: ...` so a later glossary pass can review
  and canonize it.

## Placeholder sentinels -- INVIOLABLE

The source text carries tokens such as `⟦FNREF_N⟧` (a footnote anchor's
exact position) and `⟦VERSE_Vddd_hash⟧` (an embedded verse's position).
**Copy every sentinel byte for byte, at its correct in-sentence position,
in every block you translate.** Never translate, reword, drop, or invent
one. The set and count of sentinels in your translated block must exactly
match the source block.

## What to translate, and how

- **Every block** in `blocks[]` -> target-language text. Register, tone,
  formality, and formatting follow `style_bible.md` exactly. Preserve any
  deliberate colloquialism, irony, or register shift present in the
  source; flattening it into bureaucratic prose, or calquing the source
  language's own syntax, is never acceptable.
- **Every footnote** in `footnotes[]` -> translate the whole apparatus,
  unless this project's `apparatus_policy` says otherwise.
- **Every verse** in `verses[]` -> follow this run's own spliced-in verse
  policy instructions; write exactly the fields that policy requires (see
  `draft.schema.json`'s mode-neutral `verses{}` shape -- `validate_draft.py`
  is the sole authority on which fields a given mode actually requires).
- **Word-sense and realia fidelity** -- check what a notable word or
  reference actually meant in the source's own era and context, not its
  first present-day sense.
  <!-- ERA/DOMAIN TRAP EXAMPLE -- `scripts/scaffold_validate.py`'s W1 gate
       FATALs if this exact shipped example survives an unedited
       copy-paste into a real project; replace it with a real trap
       specific to THIS project's own source material before your first
       segment: guéridon=refrain-song -- in general modern French,
       "guéridon" is a small round pedestal table, but in this shipped
       example's own 17th-century French-memoir source domain the word
       was period slang for a type of song refrain; a translator relying
       on the modern sense alone would silently mistranslate every
       occurrence. -->
- **Embedded third-language text** (e.g. Latin, an older stage of the
  source language, or a similar aside) -- ALWAYS gloss it in-text: keep
  the original AND give the target-language translation immediately
  alongside it (inline in parentheses, or as its own footnote). Never bury
  the gloss only in `notes` -- a reader of the draft alone would never see
  it there, which is a reviewable defect, not a stylistic choice.

## Output -- EXACTLY this JSON (no markdown fencing)

**Write target:** under W5 mass-translate the per-segment dispatch prompt
(`mass-translate-wf.template.js`'s `translatePrompt()`, which re-reads this
file) SUPERSEDES this section and supplies the exact path to write to -- the
`codex_job.py` driver's own isolated attempt file, NOT the canonical path --
so write wherever that generated dispatch prompt tells you, never guess a
path. The driver validates that attempt and only then atomically promotes it
to the canonical `${durable_root}/segments/{SEG}.draft.json` (see
`draft.schema.json`; this filename never carries a target-language suffix --
always `{SEG}.draft.json`, regardless of this project's target language). When
this contract is followed directly, OUTSIDE the W5 driver (no dispatch prompt
overriding the target), write that canonical path ATOMICALLY -- temp file +
rename, never a partial file visible mid-write.

```
{
  "seg": "{SEG}",
  "blocks": { "<block_id>": "<target text with sentinels>", ... },
  "footnotes": { "<n>": "<target text>", ... },
  "verses": { "<vid>": { "...": "per this run's own spliced-in verse-policy instructions" }, ... },
  "names": [ {"source_form": "...", "canonical_target_form": "...", "basis": "established|transliterated|title|sense_translated|not_a_name", "confidence": "high|medium|low"}, ... ],
  "notes": [ "NEW: ...", "..." ]
}
```

`blocks`/`footnotes`/`verses` keys must be EXACTLY the ids/n/vids present in
the input segpack -- 1:1, nothing skipped, nothing invented.

Self-check before returning (direct/fallback use only -- under the W5 driver
the generated dispatch prompt supersedes this step, because the driver's own
validate-before-promote plus the Workflow's on-disk gate are the acceptance
authority there): run
`python3 ${durable_root}/scripts/validate_draft.py {SEG}` and confirm it
prints `OK`. If it prints `FAIL`, fix the draft, rewrite the file, and
repeat until it prints `OK`.

Final response: exactly the line `DONE {SEG}`. The work lives in the file,
not in your response text.
