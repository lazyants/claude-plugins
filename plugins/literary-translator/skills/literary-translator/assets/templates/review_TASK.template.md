<!-- PROMPT_CONTRACT_VERSION: 3 -->

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
  name, or an epithet swapped in for a real surname. **The draft's own
  `names[]` entries and any `NEW:`-prefixed note are the translator's
  unratified proposals, written in the same turn as the prose you are
  reviewing -- never a standard.** A finding that prescribes a particular
  canonical target form -- change to it, restore it, revert to it -- must
  quote the `canon_map` entry it rests on; a form with no `canon_map`
  entry has no frozen canon and you may not assert one. Findings grounded
  in the source rather than in a canonical target form are untouched.
- `split_names` -- a source form adjudicated as a **homonym split** (one
  spelling, two or more distinct referents), listed with a `disambiguator`
  per sense. Such a form carries **no frozen canon**: it is absent from
  `canon_map` and from `canon.json` by design, because one frozen form is
  what a homonym cannot have. So do **not** flag it as an uncanonized name,
  and do **not** prescribe a canonical target form for it -- the rule above
  (a form with no `canon_map` entry has no frozen canon you may assert)
  applies here with no exception. What you MAY flag, quoting the
  `disambiguator` it rests on, is the draft rendering the **wrong sense**
  for the passage: the referent the source means is a source-grounded
  question, and settling it is squarely in scope.
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

**Book-scoped style rules:**

- `style_bible.md` carries rules whose predicate spans the WHOLE book -- gloss a
  realia at its **first occurrence only**, identify a person on **first
  mention**, give a source-calendar year its Common Era equivalent at its
  **first mention**, render an original-script name in parentheses on **first
  mention**. You hold ONE segment, and a term's first occurrence in the book is
  normally in a segment you will never see, so **a finding may not rest on the
  assertion that an occurrence in this segment is, or is not, the book's
  first.** Do not demand that a first-mention treatment be ADDED here, and do
  not demand that a treatment already present be REMOVED here as a redundant
  repeat -- both directions are unevaluable from your inputs, and the remove
  direction deletes correct text.
- Where `style_bible.md` itself records where a term first occurs -- its
  motif table's first-occurrence column, or a written note IN THAT FILE naming the
  block that holds the first mention -- that record settles the question in BOTH
  directions, and you report normally: a missing first-mention treatment at the
  recorded place when that place is in this segment, and a redundant repeat at
  any occurrence the record puts elsewhere.
- A second place the evidence is in your hands is THIS SEGMENT ITSELF: where an
  occurrence here is preceded by another occurrence of the same term in this same
  segment, the later one is provably not the book's first whatever lies in the
  segments you cannot see, so a redundant repeat there is reported normally. That
  reasoning runs in the REMOVE direction only -- an earlier occurrence here proves
  a later one is not the first, and proves nothing about whether that earlier one
  is.
- Everything else about such a rule stays in scope: where the treatment IS
  present, whether it is correctly FORMED -- the right script, the
  transliteration system `style_bible.md` names, correct era arithmetic -- is
  fully evaluable here, and a finding grounded in the source rather than in a
  whole-book predicate is untouched.

## Output -- write the file, then print one sentinel line

This is a detached-driver dispatch (the `codex_job.py` driver launches you and
never reads your turn's return): nothing reads your own turn's return value as
the verdict. Your job is to WRITE the file correctly, not to return a
structured result -- a separate, later call reads
`${durable_root}/segments/{SEG}.review.json` back off disk (see
`references/workflow-schema-validation.md`'s DISPATCH -> WAIT -> CONSUME
pattern).

Write EXACTLY this JSON object (no markdown fencing) to the write target the
dispatch prompt gives you. Under W5 mass-translate, `reviewDispatchPrompt()`
SUPERSEDES this section and supplies that path -- the `codex_job.py` driver's
own isolated attempt file, NOT the canonical path -- and the driver validates
that attempt and only then atomically promotes it to the canonical
`${durable_root}/segments/{SEG}.review.json` (see `review.schema.json`; this
filename never carries a target-language suffix -- always `{SEG}.review.json`,
regardless of this project's target language). When this contract is followed
directly, OUTSIDE the W5 driver, write that canonical path ATOMICALLY (temp
file + rename, never a partial file visible mid-write):

```
{
  "clean": true|false,
  "coverage_ok": true|false,
  "findings": [
    {"loc": "<block_id | FN:n | VERSE:vid | NOTE:n>", "severity": "high|medium|low",
     "issue": "<what is wrong>", "suggest": "<how to fix it -- brief, concrete>"}
  ],
  "draft_sha1": "<the sha1 you computed BEFORE reading the draft, above>",
  "dispatch_token": "<the exact token your dispatch prompt gave you for this call -- copy it verbatim>"
}
```

Every `loc` must be colon-delimited. A bare, holistic token (`overall`,
`NOTES`, `TASK`) is refused outright and discards the entire review, valid
findings included. `NOTE:n` addresses one entry of the draft's own `notes[]`
array and is a **0-based index** into it (the first note is `NOTE:0`), whereas
`FN:n` is the footnote's own **number**, not an index.

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
