# Verse policy

`verse_policy` is a **required** `profile.yml` block with a fixed `mode`
enum, nested with `threshold_lines`:

```yaml
verse_policy:
  mode: full_rhymed_plus_literal
    # full_rhymed_plus_literal | full_rhymed_only | rhythmic_approximation |
    # mixed_by_length | literal_only | skip   -- see references/verse-policy.md
  threshold_lines: null
    # REQUIRED (fatal at Step 0 if null/absent) only when mode: mixed_by_length;
    # ignored (must stay null) for every other mode -- nested under verse_policy so
    # the mode<->threshold coupling is structurally visible, not two independent
    # top-level keys a profile author could set inconsistently.
```

No component may hardcode a verse-handling behavior — every one of them looks
it up from the single table below, but each through a **different, deliberately
distinct channel**:

- The translator and reviewer receive the resolved instruction **text** through
  the generated per-run workflow script's `{{VERSE_POLICY_INSTRUCTION_BLOCK}}`
  substitution — read fresh from the *current* `profile.yml` every time a new
  run is scaffolded (mass-translate, glossary-pass). It is **never** spliced
  into the hand-adapted `translate_TASK.md` / `review_TASK.md` files, which
  stay verse-policy-neutral end to end (project-general instructions,
  era/domain-trap callouts) exactly like every other one-time Step-0a copy. A
  live splice into those files would either silently overwrite a project's own
  hand edits on every run, or — if genuinely one-time — leave a later
  `verse_policy.mode` change never reflected in them at all.
- The validator (`validate_draft.py`) branches on `verse_policy.mode` directly,
  at check time.
- Step 0b's own job is narrower than "splice the instruction into a file": it
  is the fatal precondition check (`mixed_by_length` requires
  `threshold_lines`) plus resolving which row of the table below applies — a
  resolution the workflow-script substitution and the validator each consume
  independently, never through a shared mutated file.

## The six modes

| `verse_policy.mode` value | Translator instruction | Required draft fields | Validator behavior |
|---|---|---|---|
| `full_rhymed_plus_literal` | Every verse (long or short, incl. epigrams) gets a full rhymed literary rendering AND a mandatory literal gloss. | `rendered` (rhymed) + `literal_gloss`, both non-stub, provably distinct after whitespace normalization | Both fields required; distinctness check; multi-line-source → non-1-line-rendering check; key-set/bijection checks always apply |
| `full_rhymed_only` | Full rhymed rendering; no forced literal safety-net copy. | `rendered` only | `rendered` required; no distinctness check needed; key-set/bijection checks always apply |
| `rhythmic_approximation` | Meter/rhythm preserved but full end-rhyme not required — a lighter-weight option for volume/cost-sensitive projects. | `rendered` only | `rendered` required; no rhyme-specific check; key-set/bijection checks always apply |
| `mixed_by_length` | Verses at or over `verse_policy.threshold_lines` get `full_rhymed_plus_literal`; verses under it get `rhythmic_approximation`. `threshold_lines` is REQUIRED whenever this mode is chosen (fatal at Step 0 if missing). | Conditional on line count vs. `threshold_lines` | Conditional on the same threshold; key-set/bijection checks always apply |
| `literal_only` | No rhyme/meter attempt; a faithful prose gloss only — for projects prioritizing informational accuracy over literary verse craft. | `literal_gloss` only | `literal_gloss` required; no rhyme fields expected (presence would be flagged as unexpected extra); key-set/bijection checks always apply |
| `skip` | Verses are left untranslated / passed through as-is (e.g. a project translating prose commentary only, quoting verse in the original), OR rendered with an explicit passthrough marker. | none (content-wise) | **Content requirement only is exempted — coverage is NOT.** Every verse's key must still be present and its placeholder must still resolve via the same per-block bijection check as every other mode. |

## The one invariant that must never regress

Regardless of mode, the **per-block verse-placeholder bijection check** —
every verse key present exactly once on both sides, each placeholder
resolving to exactly one verse block — is enforced **structurally,
deterministically, under every mode, `skip` included**. Only the *literary
content requirement* (what each mode demands the translated value actually
contain) varies per mode.

`skip` exempts **only** the translated-content requirement, never key
coverage or the bijection check. An earlier draft of this plan made `skip` a
full no-op on the verse section — that would have been *stricter-in-reverse*
of the proven `validate_draft.py`, which always enforces verse
key-set/placeholder-bijection regardless of translated content. `skip` never
disables that; it only stops requiring a translated **value**. This must
never become mode-conditional in any future change.

`profile_semantics_hash` is defined as the sha1 of canonical JSON
`{source_lang: source.language.code, target_lang: target.language.code,
verse_policy_mode: verse_policy.mode, verse_policy_threshold_lines:
verse_policy.threshold_lines, apparatus_policy: footnotes.apparatus_policy,
untranslated_sentinel: validation.untranslated_sentinel}`. Because the exact
hash keys include `verse_policy_mode` and `verse_policy_threshold_lines`, any
edit to either source profile field flips every affected segment to `stale` —
closing the "configurable but silently uncached" risk a configurable enum
would otherwise create.

## Ownership split (critical, don't get this wrong)

`draft.schema.json` is a **mode-neutral structural superset only** — local
container shapes (`{seg, blocks{}, footnotes{}, verses{}, names[], notes[]}`),
identical across every mode, including `skip`. There is **no `if/then` in the
schema keyed on `verse_policy.mode`**, by design: six-way branching in a
schema is more complexity than the value would add, so the table above is
documented as plain-English rules, not a formal discriminated union. There is
no separate standalone `verse.schema.json` file.

`validate_draft.py` is the **sole authority** for:

- coverage/bijection — which needs the separate segpack file to check at all
  (the schema alone cannot do a cross-document check), and
- anything that actually varies **by** `verse_policy.mode` — the table above,
  in full (which of `rendered` / `literal_gloss` are required, forbidden, or
  conditional).

A draft can be fully `draft.schema.json`-valid while still failing
`validate_draft.py` outright (missing verse key, duplicated placeholder) OR
being semantically wrong for the active mode (missing `literal_gloss` under
`full_rhymed_plus_literal`) — every one of those failures is caught **only**
by `validate_draft.py`.

## Why an enum, not booleans or a `manual` mode

Design decision 3 (kept `verse_policy` as an enum): the source project's own
plan document shows it went through three prior verse policies before
locking `full_rhymed_plus_literal` — a future project translating, say, a
prose-only philosophical text with two decorative epigraphs has no reason to
pay for `full_rhymed_plus_literal`'s cost on 50 poems, and a future project
centered entirely on a verse epic has no reason to accept
`rhythmic_approximation`'s lighter bar. That is why this is a table keyed by
one enum field, not a compositional set of independent boolean flags a
profile author could combine into an unspecified or contradictory
combination.

There is **no `manual` mode** in v1 — cut for lacking any machine-readable
marker/range contract a validator could actually check. A verse-handling case
a regex genuinely can't express uses `source.format: custom` instead
(co-designed with the skill rather than half-specified as a verse-policy
value), while the verse-placeholder bijection check still applies exactly as
it does for every shipped adapter.

## See also

- [`../false-green-gate.md`](../false-green-gate.md) — check 3 (per-standalone-verse
  `parent_block` bijection) and check 5 (per-verse required-content fields
  derived from the resolved verse policy), the two checks this table feeds.
- [`../ledger-and-resumability.md`](../ledger-and-resumability.md) —
  `profile_semantics_hash`'s exact `verse_policy_mode` and
  `verse_policy_threshold_lines` entries, and how a policy edit flips affected
  segments to `stale`.
- [`source-format-adapters/README.md`](./source-format-adapters/README.md) —
  the adjacent design decision on why there is no generic parser framework,
  and the two different senses of "proven" that also apply to language
  configs and adapters.
