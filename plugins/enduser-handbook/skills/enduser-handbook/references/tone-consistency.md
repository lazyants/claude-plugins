# Tone consistency over time

A handbook lives for years and is touched by many sessions. Without a re-anchoring
discipline at the start of each session, prose drifts: address form softens, sentence
rhythm wanders, terminology forks, headings stop rhyming with each other. You fix
this by re-reading two sources before you write a single line of chapter prose:

1. The **style guide** the project owns.
2. The **latest published chapter** as a working *exemplar* of that style applied.

Skip either and you will ship drift. The user will not flag it in one chapter; they
will flag it in chapter twelve, when the handbook reads like five different writers.

## Why drift is the default

Every session starts fresh. You do not remember the last chapter's cadence, the
project's exact register, or the small lexical choices that have already calcified
into house style (which connector word, which heading verb form, whether an
imperative gets a trailing period, whether UI labels are bolded or quoted). Even
when the profile pins `language.register` and `style_guide.source`, those settings
describe rules. The exemplar shows the *applied result* — and the applied result is
what the next chapter has to match.

Drift compounds: a chapter written half a step off the exemplar becomes itself the
next session's exemplar, and the next chapter drifts half a step further off the
original. Re-anchoring to the **style guide** (authoritative) plus the **latest
published chapter** (currently-applied reality) breaks the chain.

## Mandate: read the style guide before drafting

Before you write any prose for a chapter — before the lede, before the first H2,
before the first imperative — you must read the project's style guide.

- If `style_guide.source` is set in the profile, read that file in full. It is the
  authoritative voice contract for the project: address form, sentence shape, UI
  label discipline, heading grammar, alt-text discipline, numbers and units,
  do/don't list. Treat it the way you treat the profile itself: load it, internalize
  it, then write.
- If `style_guide.source` is `null` and only `style_guide.inline` is set, read the
  inline keys. Inline is a minimal fallback — it covers sentence style, address
  form examples, the UI-label rule, and a do/don't list, and not much else. If
  inline is all you have, write more conservatively: shorter sentences, fewer
  stylistic flourishes, no clever connectors that the project has not yet sanctioned.
- If `style_guide.source` is set but the file does not exist, halt. Do not silently
  fall back to inline. The halt message is the one defined in the base skill's
  startup checks.

You re-read the style guide every session. Not "once per project". Every session.
The cost is small; the cost of not re-reading is drift.

## Mandate: read the latest published chapter as exemplar

After the style guide, open the most recent chapter under `publish.chapters_dir`.
Pick by modification time, not by alphabetical order — the most recently edited
chapter reflects the current applied voice.

Read it whole. Notice:

- The cadence of imperatives. How long is a typical step? Does it end in a period?
- How UI labels are presented (bold, quoted, both, neither).
- How screenshots are introduced — is there a lead-in sentence, or does the image
  follow the step directly?
- The grammar of H1 and H2. Noun phrase? `<Object> <Verb>`? Imperative?
- How section labels resolved from `publish.section_labels` (e.g.
  `publish.section_labels.prerequisites`, `publish.section_labels.related`) read in
  practice in this project's language.
- How cross-references are written — wikilinks if `publish.wikilinks` is true,
  markdown links otherwise — and where they appear (inline, or in the "related"
  section).
- Glossary terms. Which terms are linked on first mention; which are linked every
  time. The exemplar tells you which discipline the project actually applies.

Write your new chapter so that, dropped next to the exemplar, the two read like the
same author wrote them on the same afternoon. Even when the topic is different.
Especially when the topic is different — same voice, different content is exactly
the consistency contract.

If `publish.chapters_dir` contains no chapter yet (first chapter of the handbook),
you have no exemplar. In that case the style guide is the only anchor; write the
first chapter deliberately, knowing it will become the exemplar for every chapter
after it. Lean conservative.

## When the exemplar drifts from the style guide

Sometimes the latest chapter and the style guide disagree. A previous session
wrote a chapter that wandered: an imperative form the style guide forbids, a UI
label paraphrased instead of quoted verbatim, a heading grammar the guide does not
sanction, a register that softened from formal to casual.

The rule is simple: **trust the style guide**. It is authoritative. The exemplar
is evidence of how the style guide has been applied, not a license to keep applying
it wrong.

When you spot a discrepancy:

1. Write the new chapter to the **style guide**, not to the drifted exemplar.
2. Flag the exemplar discrepancy to the user, concretely. Name the file, name the
   rule the exemplar broke, quote the offending line, quote the style-guide rule it
   conflicts with. Do not editorialize; just surface the conflict.
3. Offer to fix the drifted exemplar in a follow-up pass, but do not silently
   rewrite it in the current session — that is scope creep and it hides the drift
   from the user.

If multiple recent chapters show the same drift, the drift is no longer a one-off:
either the style guide is out of date and needs an update, or the drift is a
systematic regression that needs a sweep. Surface both possibilities to the user
and let them decide. Do not pick one yourself.

## What "consistent voice" actually means

It is not "every chapter sounds the same word-for-word". It is: a reader moving
from chapter to chapter never notices the seam. The address form does not shift.
Step verbs come from the same small set. Headings rhyme. UI labels are presented
the same way every time. Numbers, dates, and units follow the same conventions
(`language.date_format`, `language.decimal_separator`, `language.currency_symbol`).
Glossary terms resolve to a single canonical form per concept
(`glossary.canonical_term_language`).

You achieve this by re-anchoring every session, not by hoping you remember.
