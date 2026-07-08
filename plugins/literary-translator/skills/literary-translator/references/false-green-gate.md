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

Reads `segpack_{seg}.json` (the source) and `draft_path(seg) =
segments/{seg}.draft.json` (no target-language suffix — see
[`ledger-and-resumability.md`](./ledger-and-resumability.md) for why the
draft path is deliberately unsuffixed).

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
   drop/duplicate/mangle of a placeholder token. The mandatory adversarial
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
   every block with a non-empty `body_ref_markers[]` (populated by
   `extract.py.template`/`segpack.py` when `footnotes.apparatus_policy ==
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

## `draft_ready.py` — a separate, narrower job

`draft_ready.py` answers a different, cheaper question: has the async
translator *delivered* a complete file at all — distinct from *is it
good*. It is a separate readiness probe that gates the review step of the
Workflow template, separate from `validate_draft.py`'s structural quality
gate. The split is what prevents a Claude fix-agent from ever ending up
authoring a missing translation from scratch: a fix agent only ever edits
an existing draft that has already passed `draft_ready.py`, never
originates new translated content.

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
  `plugin_bundle_hash`'s inclusion of `validate_draft.py` as one of the six
  scripts that directly shape translate/review/validation content.
- [`engine-loop.md`](./engine-loop.md) — where this gate sits in the
  translate → gate → review → fix loop.
