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

There is a second false-green move this discipline names explicitly:
**editing a self-check to make it pass.** A deterministic check protects you
only while it stays honest — silencing, weakening, or faking one to reach
green manufactures exactly the false-green it existed to prevent, and is never
acceptable. A check that fires wrongly on a legitimately-different input is a
plugin issue to file; a genuine coverage gap is a new check to add and
regression-lock — never a line to quietly delete. The post-extraction gate
below (`validate_extraction.py`) enforces this structurally, pinning the
extractor's own self-check region by hash so a locally-weakened check is
caught rather than trusted.

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

**1.4.1:** Draft `seg`-identity: `validate_draft.py` and `draft_ready.py`
now require the draft's top-level `seg` to equal the requested segment id
(a mislabeled/cross-wired `seg01.draft.json` carrying `seg:seg02` previously
passed both gates). Regression-locked by
`tests/seg_identity_enforced.test.py`, a dedicated fixture (not folded into
`validate_draft.test.py`'s injected-defect suite because the hole spans both
scripts).

## `draft_ready.py` — a separate, narrower job

`draft_ready.py` answers a different, cheaper question: has the async
translator *delivered* a complete file at all — distinct from *is it
good*. It is a separate readiness probe that gates the review step of the
Workflow template, separate from `validate_draft.py`'s structural quality
gate. The split is what prevents a Claude fix-agent from ever ending up
authoring a missing translation from scratch: a fix agent only ever edits
an existing draft that has already passed `draft_ready.py`, never
originates new translated content.

**1.2.0:** `draft_ready.py` gains `--expect-token TOK` — READY only when
the on-disk draft's `dispatch_token` also equals `TOK` (backward-compatible
when the flag is omitted), so a straggler draft from an interrupted OLD run
can never be accepted as this run's own delivery. `review_ready.py` is the
new, sibling readiness probe for the review point — see
`references/ledger-and-resumability.md`'s `dispatch_token`/commit-gate
chain and `references/orchestration-and-batching.md`'s shared
DISPATCH → WAIT → CONSUME pattern for the full mechanics; both scripts
share the byte-identical `_SEG_ID_RE`/`validate_seg()` and the same
self-anchoring discipline this file's §3 "canonical path invariants"
sibling doc (`references/ledger-and-resumability.md`) already documents.

## The post-extraction gate (`validate_extraction.py`)

`validate_extraction.py` is the false-green gate for the *extraction* stage —
the earlier sibling of `validate_draft.py`, run once at W2 the moment
`extract.py` produces `manifest.json`, before any draft exists. The pipeline
advances only on its exit 0.

It exists to make the "editing a self-check to make it pass" anti-pattern
above structurally harmless. `extract.py` runs its own in-file self-check
suite (the sentinel-delimited `# BEGIN SELF-CHECK REGION` …
`# END SELF-CHECK REGION` block — see
[`source-format-adapters/gutenberg-epub.md`](./source-format-adapters/gutenberg-epub.md)),
but that suite lives in a file each project is expected to hand-adapt. A
hand-edited `extract.py` that skips, weakens, or fakes its own enforcement
could otherwise manufacture a green `manifest.json`. `validate_extraction.py`
closes that hole two ways:

1. **Independent re-derivation of the manifest-derivable invariants.** It
   self-anchors to the plugin's own install path (like `profile_validate.py`),
   is **never copied to the durable root**, and is **not** a bundle member —
   so it cannot itself be hand-edited as part of a project's `extract.py`
   adaptation. It loads the produced `manifest.json` plus the profile and
   **re-derives every manifest-derivable invariant from scratch**, ignoring
   whatever result `extract.py`'s own checks claimed: block-id uniqueness,
   spine order, segmentation-nonempty, body-files-yield-segments,
   no-pseudo-segments-from-notes, the footnote bijection + sentinel-uniqueness
   (or, under `body_refs_only`, body-ref-marker well-formedness/uniqueness —
   branching on `footnotes.apparatus_policy` exactly as the extractor does),
   frontback inventory, verse-placeholder uniqueness/mounting, verse
   plain-text non-emptiness, the per-segment word cap, and the full
   verse-count reconciliation. Any failure is FATAL. A green manifest a
   tampered extractor produced still fails here, because this gate never trusts
   the extractor's self-report — it recomputes each invariant from the
   manifest itself.
2. **Self-check region hash pin.** It computes a normalized hash of
   `extract.py`'s self-check region (the text strictly between the two
   sentinel lines) and compares it against the shipped
   `CURRENT_EXTRACTOR_SELFCHECK_HASH`. A missing/malformed region, or a hash
   mismatch, is FATAL — naming the "editing a self-check to reach green"
   anti-pattern and pointing genuine gaps at a plugin issue.

**The honest residual.** Three of the extractor's self-checks —
`body_coverage_no_holes`, `no_orphan_footnote_continuation`, and
`verse_no_uncovered` — depend on intermediate parse state that is **not**
recorded in `manifest.json`, so `validate_extraction.py` cannot independently
re-derive them. They are covered by the **region hash pin only**: if the
self-check region is byte-for-byte the shipped implementation (hash matches),
these three are trusted to have run as shipped; the gate does not re-prove
them from the manifest. This is a deliberate, documented limit — stated
plainly so nobody mistakes the hash pin for a full independent re-derivation
of these three.

Invocation mirrors `profile_validate.py`'s exit-code discipline (exit `0` =
every check passed, `1` = any check or the hash pin failed, `2` = usage/env
error such as bad args or an unreadable file):

```
python3 {{PLUGIN_ROOT}}/assets/scripts/validate_extraction.py \
  --manifest <durable manifest.json path> \
  --extract  <durable extract.py path> \
  --profile  <profile.yml path>
```

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
