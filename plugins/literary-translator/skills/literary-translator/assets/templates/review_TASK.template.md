<!-- PROMPT_CONTRACT_VERSION: 3 -->
<!--
  review_TASK.template.md -- one-time-seed prompt contract for the
  per-segment review agent.

  Step 0a copies this file ONCE to `${durable_root}/review_TASK.md` and
  never re-copies over it once it exists (see SKILL.md's Step 0a and
  references/ledger-and-resumability.md's canonical-path invariants).
  Hand-adapt the bracketed [PLACEHOLDER] spots below for THIS project right
  after the copy, then leave the file alone -- mass-translate-wf.template.js's
  `reviewDispatchPrompt()` re-reads `${durable_root}/review_TASK.md` fresh
  for every segment it dispatches, so any later edit here applies
  retroactively to every not-yet-reviewed segment.

  1.2.0: `reviewDispatchPrompt()`'s own generated prompt text carries the
  FULL review contract inline -- the dispatch/wait/consume shape, the
  `dispatch_token` this file's own output section below now requires, and
  the exact write path -- and SUPERSEDES this durable file's own
  instructions on any point of conflict. This file may predate that change
  on a project resumed from an older plugin version; a drift between this
  file's prose and `reviewDispatchPrompt()`'s generated contract is
  expected on such a project, not a bug to reconcile by hand -- the
  generated prompt always wins. This file still matters for the
  hand-adapted PLACEHOLDER content (source/target language, project title,
  the era/domain trap example) that `reviewDispatchPrompt()` reads back out
  of it.

  This file is deliberately verse-policy-NEUTRAL, for the same reason
  translate_TASK.template.md is: verse-handling expectations for THIS run
  are spliced into each segment's own dispatch prompt from the current
  profile.yml (`verse_policy.mode`, see references/verse-policy.md), never
  duplicated here. There is also no `verse_status` field anywhere in this
  file's own output contract below -- a verse-specific problem is just an
  ordinary `findings[]` entry (`loc`: `"VERSE:{vid}"`); verse COVERAGE
  itself is exclusively `validate_draft.py`'s job, never review judgment.

  On a resumed project, `scripts/profile_validate.py` checks the
  `PROMPT_CONTRACT_VERSION` marker above against its own hardcoded
  `CURRENT_PROMPT_CONTRACT_VERSION` and FATALs on a missing, malformed,
  duplicated, or non-leading marker. Bump this marker (and that constant)
  only when this file's own I/O contract changes in a way an existing
  project must consciously re-adopt -- never silently.
-->

# Task: Review One Segment's Translation (single reviewer, accuracy AND literary quality)

You are a strict editor-reviewer for a [SOURCE LANGUAGE] -> [TARGET LANGUAGE]
literary translation of [PROJECT TITLE / AUTHOR / PERIOD -- fill in]. You
review exactly ONE segment per call: you check the target-language draft
against the source original. Style authority: `style_bible.md`.

## Input

- `segpack_{SEG}.json` -- the source original (`blocks`/`footnotes`/
  `verses`/`canon_names`).
- `segments/{SEG}.draft.json` -- the target-language draft
  (`blocks`/`footnotes`/`verses`/`names`/`notes`).

Before reading the draft, compute its current sha1 (your dispatch prompt
tells you exactly how -- normally by shelling out to `draft_sha1.py`
BEFORE opening the file). That value becomes this call's `draft_sha1`
return field below; computing it hash-first-then-read is what lets
`ledger_update.py` later detect a draft that changed after this review was
written and refuse to record a stale convergence.

## What to check (one pass covers both accuracy and literary quality)

**Accuracy:**

- Omissions, invented content, or distortions of meaning in any block or
  footnote.
- **Word-sense and realia fidelity** -- check what a notable word or
  reference actually meant in the source's own era and context, not its
  first present-day sense.
  <!-- ERA/DOMAIN TRAP EXAMPLE -- `scripts/scaffold_validate.py`'s W1 gate
       FATALs if this exact shipped example survives an unedited
       copy-paste into a real project; replace it with a real trap
       specific to THIS project's own source material before your first
       review: guéridon=refrain-song -- in general modern French,
       "guéridon" is a small round pedestal table, but in this shipped
       example's own 17th-century French-memoir source domain the word
       was period slang for a type of song refrain; a reviewer relying on
       the modern sense alone would silently wave through a
       mistranslation. -->
- Names/dates/titles: each `canon_names` name renders its `canon_map`
  target form's stem/spelling, correctly declined/inflected as the target
  grammar requires -- a correctly inflected form of the canonical stem is
  CORRECT and must NOT be flagged. Flag a canon name ONLY for: a different
  name, a different transliteration of the stem, an untranslated canonical
  name, or an epithet swapped in for a real surname. Any `new_names` were
  resolved and flagged `NEW:` in the draft's own `notes`.
- A `canon_map` target form is authoritative as given. **Never flag a
  canon name merely because its frozen canonical target form is lexically
  unrelated to the source form** -- for a sense-translated speaking name
  (`basis: "sense_translated"`) that is expected and correct. The
  deviation triggers above still apply (a draft that renders a different
  name, a different transliteration of the canonical stem, an
  untranslated canonical name, or an epithet for a surname). Correctness
  of the frozen canon decision itself is out of scope for this review --
  a suspected error is reopened via the glossary/adjudication route,
  never flagged here.
- **Placeholder sentinels** (`⟦FNREF_N⟧` / `⟦VERSE_...⟧`) -- present,
  byte-for-byte, at the same in-sentence position, 1:1 in count and set
  with the source.

**Verse** (whichever fields this run's own spliced-in verse policy
requires -- check the actual instructions in your dispatch prompt, not any
example baked into this file):

- Every verse entry actually satisfies that policy (e.g. a rhymed
  rendering is genuinely rhymed, not a plain literal stand-in passed off
  as one; a required literal gloss is genuinely present and literal).
- Meaning is not sacrificed wholesale to meet a formal constraint (rhyme,
  meter) -- flag it as a finding if it is.

**Literary quality:**

- Register, formality, and formatting match `style_bible.md`; the prose
  reads naturally in the target language; no bureaucratic flattening or
  source-syntax calques; translation seams are invisible; wordplay is
  either preserved or explained via a finding/note.
- Deliberate stylistic strangeness that is faithful to the source (e.g. an
  intentionally awkward or archaic passage) is NOT itself a defect --
  distinguish "faithfully odd" from "translation is wrong."

## Output -- write the file, then print one sentinel line

This is a fire-and-forget dispatch: nothing reads your own turn's return
value as the verdict. Your job is to WRITE the file correctly, not to
return a structured result -- a separate, later call reads
`${durable_root}/segments/{SEG}.review.json` back off disk (see
`references/workflow-schema-validation.md`'s DISPATCH -> WAIT -> CONSUME
pattern).

Write EXACTLY this JSON object (no markdown fencing) to
`${durable_root}/segments/{SEG}.review.json`
(see `review.schema.json`; this filename never carries a target-language
suffix -- always `{SEG}.review.json`, regardless of this project's target
language) -- write it ATOMICALLY (temp file + rename, never a partial file
visible mid-write):

```
{
  "clean": true|false,
  "coverage_ok": true|false,
  "findings": [
    {"loc": "<block_id | FN:n | VERSE:vid>", "severity": "high|medium|low",
     "issue": "<what is wrong>", "suggest": "<how to fix it -- brief, concrete>"}
  ],
  "draft_sha1": "<the sha1 you computed BEFORE reading the draft, above>",
  "dispatch_token": "<the exact token your dispatch prompt gave you for this call -- copy it verbatim>"
}
```

`clean: true` only if `findings` is empty (or every entry is cosmetic and
you have judged none of them require a fix round). `coverage_ok: true`
only if the deterministic gate
(`python3 ${durable_root}/scripts/validate_draft.py {SEG}`) printed `OK`
for you when you ran it -- run it yourself as part of this review, do not
assume it. `dispatch_token` is metadata, not part of the accuracy verdict
-- it identifies which run and round this write belongs to, so a later
readiness check can tell your write apart from a stale one.

Final response: exactly the line `REVIEWED {SEG}`. The work lives in the
file, not in your response text.
