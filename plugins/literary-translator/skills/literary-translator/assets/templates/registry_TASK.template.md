<!--
  registry_TASK.template.md -- the prompt contract for W9r, the opt-in
  person-registry pass (#550).

  Copied to `${durable_root}/registry_TASK.md` at W9r time -- NOT at Step 0a.
  W9r is opt-in by virtue of the operator running it, so a project that never
  runs it never needs this file; and keeping it out of Step 0a keeps it out of
  `scaffold_validate.py`'s closed six-file required-fill list and out of the
  PROMPT_CONTRACT_VERSION regime, neither of which it belongs to.

  Fill the bracketed [PLACEHOLDER] spots for THIS project right after the copy.

  TWO SECTIONS, TWO SEPARATE CALLS. Pass A builds the registry; Pass B
  adjudicates what Pass A produced. Pass B MUST be a FRESH dispatch that has
  never seen Pass A's conversation and never reads `registry_verdicts.json` --
  its only semantic inputs are its own section below and
  `registry/registry_claims.json`. A reader who already holds the narrative
  that produced a judgement is not an independent check on it, and the failure
  Pass B exists to catch is precisely a judgement that is plausible and wrong.
-->

# Task: Person Registry ([SOURCE LANGUAGE] -> [TARGET LANGUAGE])

You are consolidating the name forms of [PROJECT TITLE / AUTHOR / PERIOD --
fill in] into a person-keyed registry. This book was translated **for
genealogy**: the deliverable is not the translation but the answer to "how
many distinct people are in this book, which name forms are the same person,
what does the book print them as, and how are they related."

## The rule that governs everything below

**A shared surface form is ambiguous by construction, not by accident.** In a
naming system that fixes identity by patronymic or by place, a given name plus
an honorific denotes a CLASS of people, not a person. On the corpus this pass
was generalized from, one spelling of a single given name denotes six distinct
men. There is no spelling rule, no surname heuristic, and no similarity measure
that can tell them apart, and every such rule fails in the direction that looks
like success: it merges, confidently and silently.

So: **refusal is the safe direction, and it is cheap.** Two records for one man
is untidy and a reader fixes it in a minute. One record for two men is a
fabricated person, it is invisible in the output, and it corrupts every
conclusion drawn from the registry. When the evidence you were given does not
establish that two forms denote the same person, do not merge them. Say so, and
say why.

---

# Pass A — build the registry

## Input

`registry/registry_input.json`, produced by `scripts/person_registry.py --prep`.
Its `units[]` are the whole cast, each one a `(source_form, sense_id)` pair
drawn from three populations:

- `canon_entry` — a frozen canon entry, with its `canonical_target_form` and
  its source-anchored occurrence records;
- `canon_senses` — ONE SENSE of an adjudicated homonym split. These forms are
  deliberately absent from `canon.json` and each sense carries a
  `disambiguator` written by whoever adjudicated the split. **Two senses of one
  form are two people until proven otherwise** — that is what the split
  recorded;
- `canon_review_queue` — a candidate the project itself recorded as
  unresolved, with the reason. You may describe it; you may not resolve it.
  It belongs in `refusals[]`.

Each unit carries up to `--max-contexts-per-form` contexts, chosen as an even
spread across the whole book rather than the first few, plus
`contexts_truncated`. When that flag is true you are not seeing every
occurrence: judge on what the spread shows, never on what the unseen contexts
might have contained. Each context is one PHYSICAL occurrence — two mentions in
one paragraph are two contexts, each windowed on its own.

Every context carries `text` (the SOURCE) and `target_text` (what the book
PRINTS in that same container). Read both. `target_text` is where the printed
renderings actually are, and `target_window_centred_on_canonical_form: false`
is a signal rather than a defect: the canonical form was not found verbatim
there, which usually means the translator inflected or declined it — exactly
what `printed_surfaces[]` is for.

## Output

Write `registry/registry_verdicts.json` against
`assets/schemas/registry/registry-verdicts.schema.json`. Copy `input_sha256`
from the input verbatim.

**Every unit must appear exactly once**, across `people[].units`,
`non_person_forms[]` and `refusals[]`. A unit you leave out is a person the
registry silently loses; a unit in two places is a merge conflict. Both are
refused by the build gate, so neither is a way to avoid a decision — the way to
avoid a decision is `refusals[]`, with a reason.

- **`people[]`** — one record per human being. `units[]` lists every form you
  judge to be that person. `display_name` is how you would name them in an
  index. `identity_note` says who they are in one sentence — it reaches the
  reader verbatim, and it is adjudicated with the identity itself, so put
  nothing in it the contexts do not support.
- **`printed_surfaces[]`** — the target-language strings the book actually
  prints for this person, as strings only, **read off each context's
  `target_text`** rather than derived from the canonical form. The translator
  was free to inflect or decline it, so this is a claim about the delivered
  text, not a copy of `canonical_target_form` (which is counted for you
  anyway). **Never a number** — every count in the registry is computed from
  the text. List each surface once; an exact duplicate is refused.
- **`relations[]` / `places[]` / `dates[]`** — only what the book STATES, each
  with a quote copied VERBATIM from the container its locator names, and the
  locator taken from that unit's own contexts. A relation you inferred from a
  name ("X of Tulchin must be the son of Y of Tulchin") is not a stated
  relation. Point at another person with `to_person_id`; point outside this
  book's cast with `to_unregistered` and the prose's own wording — that is a
  real edge, not a defect.
- **`identity_status`** — `confirmed` only when the identity is genuinely
  settled; `contested` when it is not. This is a fact about the IDENTITY, not
  about how often the person appears: a person mentioned once whose identity is
  obvious is `confirmed`, and a person mentioned two hundred times who cannot
  be told apart from a namesake is `contested`. Mention counts are computed
  separately and are not your business — so **"only one mention" is never a
  reason for either status**, and `identity_status_reason` saying so is the one
  mistake this field exists to prevent. Both values are adjudicated, reason and
  all.
- **`non_person_forms[]`** — a place, a work, a group, anything that is not a
  human being. `category` is optional on a canon entry and absent on most, so
  this judgement is yours — and it is adjudicated like every other, because
  removing a real person from the cast is exactly as silent as inventing one.
  An unaffirmed classification puts the unit in `refusals[]`, not in the cast.

---

# Pass B — adjudicate, seeing only the claims

**Read this section only. Do not read `registry_verdicts.json`, and do not
carry anything over from the call that produced it.** You are a second reader,
and your independence is the only thing standing between a plausible mistake
and a published registry.

## Input

`registry/registry_claims.json`. Each entry is one judgement, isolated, with
its own `claim_id`, its `question`, and everything needed to answer it — for a
person claim, every unit's contexts; for a typed claim, the quote and the full
text of the container it was taken from. Nothing else about the person reaches
you, deliberately.

## Output

Write `registry/registry_adjudications.json` against
`assets/schemas/registry/registry-adjudications.schema.json`, copying
`input_sha256` and `claims_sha256` verbatim. Emit **exactly one entry per
`claim_id`** — no omissions, no duplicates, nothing invented. Each entry is
`affirmed` plus a reason.

Affirm only what the material in front of you establishes:

- **`person`** — do these forms denote a person at all (rather than a place, a
  work, a group, a common noun); when there is more than one, do they denote
  the SAME person; and is the `identity_note` supported by the contexts shown?
  Affirm only if all three hold. If the contexts are equally consistent with
  two distinct people, that is a refusal, and a refusal is the correct answer,
  not a failure to decide.
- **`relation` / `place` / `date`** — does the quoted sentence STATE this exact
  typed claim about these exact parties? Both parties are named for you: a
  relation claim carries an identity card for its subject AND its object, with
  every form and target rendering, so "these exact parties" is checkable rather
  than an opaque id. A sentence in which both names appear is not a statement
  of a relation between them. "David visited Isaac in Warsaw" does not state
  that David is Isaac's son, that David was born in Warsaw, or anything about a
  date.
- **`non_person`** — the record says this form denotes a place, a work, a group
  or a common noun rather than a person. Do the contexts support that? Refuse
  if the form could denote a human being.
- **`printed_surface`** — does the book print this string as a name for this
  person? The claim tells you how many times that exact string appears in the
  delivered text and shows you those passages — all of them when there are few,
  an even spread over them when there are many, and it says which. Judge on what
  you are shown. An empty list means the book does not print it at all. A string
  that appears only inside a longer word, or only as another person's name, is
  not this person's printed form.
- **`identity_status`** — the record states a status and a reason. Is the
  status right on the evidence, and is the reason about the IDENTITY rather
  than about how often the person appears? "Only one mention" is a statement
  about scarcity and is never a reason for either status. This applies to
  `contested` as much as to `confirmed`: `contested` is the safe status, but
  its reason still reaches the reader.

Not affirming costs a line in the registry's `refuted_claims[]` with your
reason, and the operator can act on it. Affirming wrongly costs a fabricated
person or a fabricated kinship edge that nothing downstream will ever catch.
When in doubt, do not affirm.
