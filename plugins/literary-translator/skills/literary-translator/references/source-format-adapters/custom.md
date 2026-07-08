# `custom` — the co-designed escape hatch

`source.format: custom` is what `Step 0c` (see `SKILL.md`) resolves to this
file for. It is **not** a third scaffolded preset alongside
`gutenberg-epub.md`/`plain-text.md` — it is minimally specified on purpose,
and always will be. The source-specific parsing/detection logic is
deliberately undocumented here and co-designed per project, because a truly
custom source can't be pre-documented. What *is* fixed, mandatory, and never
negotiable is the **output contract**: whatever hand-crafted extractor the
co-design session produces must emit a `manifest.json` matching the exact
same shape every other adapter produces, schema-validated against
`manifest.schema.json`, and it must pass the same round-trip self-checks
`extract.py.template` runs (or a documented equivalent covering the same
invariants). A custom source is exempt from *how* block/footnote/verse
detection works, never from *what* the extractor must ultimately produce and
prove.

## Status: experimental/unstable until pilot-proven

This is design decision 5 (plan §19 / inventory §18): `custom` stays in v1's
codebase, with no scope change, but is explicitly labeled
experimental/unstable until it has itself been pilot-proven end-to-end. The
mandatory second-project release-gate pilot (see
`../source-format-adapters/README.md`, "two senses of proven") is scoped to
`gutenberg_epub`/`plain_text` only, since a genuinely custom source can't be
pre-validated the way a shipped preset can. `custom`'s promotion to stable is
a separate, later milestone, not a v1 release blocker; until that milestone
exists, say clearly to the user co-designing one that the adapter is
experimental/unpiloted.

`profile_validate.py` reinforces this at the moment a project actually
selects `custom`: it prints a non-fatal warning naming the choice
experimental/unpiloted and pointing back at this file (Step 0, check 9). This
is a risk disclosure, not a validation failure — `custom` is legitimately
supported.

## Configuring it: `adapter_config.custom.extractor_path`

From `assets/profile.example.yml`:

```yaml
custom:
  extractor_path: null
    # Only meaningful when `source.format: custom`. Path (relative to durable_root) to the hand-crafted Python
    # extractor this project co-designs with the skill (Step 0c, custom.md). `null` means co-design
    # hasn't happened yet -- Step 0c halts and starts that conversation; a non-null value must resolve to an
    # existing file under `${durable_root}/scripts/custom_extractors/<value>` (a bare relative filename, no
    # ".." segment, no absolute path -- e.g. `extractor_path: "my_book_extractor.py"`). The co-designed script
    # must produce a `manifest.json` matching every other adapter's exact output shape and pass the same
    # round-trip self-checks.
```

`profile.schema.json` validates shape only: the `extractor_path` key is
**required** whenever `source.format: custom` (the key itself must never be
absent from the `custom:` sub-block, even to hold `null`), and its value must
be `string | null` — nothing more. This does not prove the value resolves to
a real file or is traversal-free; those are Step 0c's procedural checks,
described next.

### Step 0c's two procedural checks

Step 0c owns exactly the two checks a schema can't express:

- **`extractor_path: null`** — valid, and the *expected* starting state for
  any new `custom` project. Step 0c halts here and starts the co-design
  conversation with the user, informed by `gutenberg-epub.md`/`plain-text.md`
  as starting patterns (see "What to look for in the source" below) — but the
  extractor's output contract is fixed regardless of what the conversation
  decides about parsing. Once the extractor is written, the project sets
  `extractor_path` to point at it and re-runs Step 0.
- **`extractor_path` non-null** — Step 0c FATALLY rejects (before any
  existence check) any value containing `..` or starting with `/`.
  Resolution is against a fixed subtree,
  `${durable_root}/scripts/custom_extractors/<value>`, never an arbitrary
  filesystem location. Only after that rejection passes does Step 0c check
  the resolved path actually exists on disk — FATAL, naming the unresolvable
  path, if not.

Neither check runs unless `source.format: custom` is the *active* format —
the same format-conditional gating every other per-format `adapter_config`
sub-block gets.

### `${durable_root}/scripts/custom_extractors/` is not scaffolded for you

Unlike `${durable_root}/scripts/`, `languages/`, and `schemas/`, Step 0a does
not create or populate a `custom_extractors/` subdirectory — there is nothing
generic to copy into it. Create the directory and the extractor file
yourself (or have the co-design session do it) as part of writing the
extractor; Step 0c's existence check is the only thing that requires the
path to resolve, and it only runs once `extractor_path` is non-null.

### No `EXTRACTOR_CONTRACT_VERSION` drift check for `custom`

`${durable_root}/extract.py` — the one-time copy of `extract.py.template`
that `gutenberg_epub`/`plain_text` projects hand-adapt — carries a leading
`# EXTRACTOR_CONTRACT_VERSION: N` marker that Step 0 checks on a resumed
project against a hardcoded `CURRENT_EXTRACTOR_CONTRACT_VERSION`, to catch a
project silently running stale extraction logic after a plugin upgrade. A
`custom` extractor lives at a different path
(`${durable_root}/scripts/custom_extractors/<value>`), was never generated
from `extract.py.template`, and has no plugin-shipped template to drift
from — this drift check does not apply to it, and there is currently no
analogous mechanism for it. A `custom` extractor's currency is entirely the
co-designing project's own responsibility.

## The output contract, in full

Whatever the co-designed extractor's own parsing logic looks like
internally, it must produce a final `manifest.json` matching the exact same
shape `gutenberg-epub.md`'s and `plain-text.md`'s own extractors produce,
schema-validated against `manifest.schema.json`:

- The same block-ID/`order_index` model, `spine[]`, `segments[]` (explicitly
  inclusive of translate-decision `FRONTBACK:{id}` units, not just body
  content — if this source has no meaningful front/back-matter distinction,
  `frontback[]` is simply an empty array, still present, never an omitted
  field), `footnotes[]`, and `verse.store` keys every other adapter's output
  carries.
- `generation_hashes.source_extraction_hash` (required).
- `source_inputs: [string]` (required, `minItems: 1`) — **this field is
  where a `custom` extractor is the sole party who can populate it
  correctly.** Every source file path this extractor actually read, in
  read order. `gutenberg_epub`/`plain_text` extractors trivially populate a
  one-entry array (`[source.path]`) since they only ever read the one file
  named in `profile.yml`; a `custom` extractor consuming more than one file
  — a multi-volume scrape, a directory of OCR pages, a source plus a
  separate glossary/errata file — must list every one of them here, in
  order, or `cache_key.py`'s `source_input_hash` computation (below) will be
  wrong.
- `generation_hashes.source_input_hash` (required).
- `frontback: [{id, decision}]` (required array, `minItems: 0`, `id` pattern
  `^FRONTBACK:.+$`, `decision` one of `translate` | `regenerate` | `omit`).

**Cross-reference invariant** (checked procedurally by the self-check suite,
never schema-expressible): every `frontback[]` entry with
`decision:"translate"` must have a matching `id` in `segments[]`; every
`regenerate`/`omit` entry must NOT appear in `segments[]`. A fatal, named
failure either way, same as for the other two adapters.

### `source.path` vs. `manifest.json`'s `source_inputs[]` — don't conflate them

`profile.yml`'s `source.path` stays **required and existence-checked at Step
0 for every format, including `custom`.** For a `custom` source it names the
**primary/representative** input — Step 0's own early sanity anchor, checked
before any co-design or extraction happens — never a claim that it is the
*only* file the extractor reads. `manifest.json`'s own `source_inputs[]`,
populated by the extractor itself, is the separate, authoritative full
file list that `source_input_hash` actually hashes. `source.path`'s own
resolved string is independently folded into that same hash purely to catch
a `profile.yml` repoint even in the freak case where a renamed file's bytes
are unchanged — it does not constrain what the extractor may read.

### Hashing detail (what `cache_key.py` needs from a `custom` extractor)

- **`source_extraction_hash`** — sha1 of canonical JSON `{format:
  source.format, adapter_config: <only the custom sub-block>}`, concatenated
  with the resolved `custom.extractor_path` file's own raw bytes (in place
  of `${durable_root}/extract.py`, which `gutenberg_epub`/`plain_text` use
  instead).
- **`source_input_hash`** — sha1 of canonical JSON `{source_path: <resolved
  source.path string>, source_bytes_sha1: <see below>}`. For `custom`
  (which may consume multiple files): `source_bytes_sha1` = sha1 of
  canonical JSON `[{filename, sha1: <sha1 of that file's raw bytes>}]`, one
  entry per file listed in `manifest.json`'s `source_inputs[]`, **sorted by
  filename**, hashing `{filename, sha1(bytes)}` pairs — never bare
  sorted-and-concatenated bytes (concatenated-bytes-only would let a
  secondary file get silently repointed at a byte-identical different file
  with no hash change; filename must be part of what's hashed, not just the
  sort key).

Full field-by-field derivation, including the other thirteen `cache_key.py`
fields shared by every adapter, lives in
[`../ledger-and-resumability.md`](../ledger-and-resumability.md) — do not
re-derive it here.

### The two-phase write (mandatory for `custom` too)

`manifest.schema.json` requires `generation_hashes.source_extraction_hash`/
`.source_input_hash` to be present, but those hashes can only be computed
from a `manifest.json` that already has `source_inputs[]`/`format`/
`adapter_config` populated — a chicken-and-egg problem every extractor, not
just `extract.py.template`, must resolve the same way:

1. Write a **draft** `manifest.json` — `source_inputs[]` populated,
   `generation_hashes.source_extraction_hash`/`.source_input_hash`
   deliberately absent, not yet schema-valid, never validated at this point.
2. Run `cache_key.py --field source_input_hash` / `--field
   source_extraction_hash`, which read the draft's own `source_inputs[]`/
   `format`/`adapter_config`.
3. Merge both computed hashes into the in-memory manifest object.
4. Write once, final — tmp-write-then-`os.replace()`, the same atomic
   pattern `ledger_update.py` uses. `manifest.schema.json` validation runs
   only against this final write, never the draft.

A `custom` extractor that skips straight to a single write with the hashes
guessed or hand-filled has not followed the contract, even if the resulting
file happens to validate — the two-phase sequence is what keeps the hashes
actually derived from the content they claim to describe.

### The self-check suite

The extractor must pass the same round-trip self-checks
`extract.py.template` runs, or a documented equivalent covering the same
invariants: bijection, uniqueness, coverage-no-holes, spine-order,
segmentation-nonempty, sentinel-uniqueness, front-back inventory,
verse-structure, plus the blocking `no_segment_exceeds_max_words` check
(fails the whole extraction, naming every offending segment, if any
segment's `word_count` exceeds `project.max_segment_words` — v1 has no
sub-chunking, see `../engine-loop.md`). Schema validation against
`manifest.schema.json` is part of this suite, not a substitute for it — the
schema is the machine-checkable half of the output contract, the self-check
suite is the semantic half, neither substitutes for the other. If a custom
extractor uses a documented equivalent instead of the exact suite, that
equivalent must cover the same invariants.

## `footnotes.apparatus_policy` for `custom`

Detection is entirely co-designed — there is no `footnotes` enum for
`custom` the way `plain_text` has one, because there is no single detection
mechanism to enumerate in advance. Whatever the extractor decides counts as
a footnote, `footnotes.apparatus_policy`'s four values still apply and mean
the same thing they mean for the other two adapters:

| `apparatus_policy` | `custom` |
|---|---|
| `translate_all` | Co-designed |
| `preserve_source` | Co-designed |
| `omit_apparatus` | Co-designed |
| `body_refs_only` | Co-designed |

If the co-designed extractor implements `body_refs_only`, it must follow
the same contract the other adapters do: no `FN:{N}` block extracted at
all, the original anchor kept as an ordinary literal marker baked into the
block's plain text (never the `⟦FNREF_N⟧` sentinel — nothing exists for it
to point at), and that marker's exact string recorded in the owning block's
`body_ref_markers: [string]` so `validate_draft.py`'s sentinel-lite
marker-survival check (see `../false-green-gate.md`) has something to
verify. Under `omit_apparatus`, nothing is recorded and nothing is checked —
there is genuinely nothing to check either way.

## What to look for in the source, before co-designing

`custom` has no detection algorithm to hand you, but the questions the other
two adapters already had to answer are the right ones to ask about a new
source, even though the answers will differ:

- **What is a segment, here?** `gutenberg-epub.md` classifies spine items by
  content, not filename, and treats files as mechanical pagination rather
  than as chapter boundaries. `plain-text.md` segments on a heading heuristic
  or a blank-line-run threshold. A `custom` source needs its own answer to
  "where does one segment end and the next begin" — and every segment must
  still respect `max_segment_words` (no sub-chunking in v1).
- **Is there verse?** If yes, the extractor needs a UNION-style detection
  strategy (container markup, bare-line/stanza fallback, or whatever this
  source actually uses) and must register verse blocks into `verse.store`
  the same way the other adapters do, so `verse_policy.mode`'s bijection
  check (`../verse-policy.md`) has something structurally consistent to
  check against.
- **Are there footnotes, and what convention do they use?** Anchor↔definition
  bijection is a cheap validity check regardless of the source's own
  footnote convention; if definitions can spill across a file/section
  boundary, group over the concatenated stream, not per-file, the same
  lesson `gutenberg-epub.md` already learned the hard way.
- **Is there front/back matter that needs a translate/regenerate/omit
  decision?** If the source has no such distinction at all, `frontback[]`
  is legitimately empty — that is a correct, complete answer, not a gap.
- **How many files does this source actually consist of?** If more than
  one, `source_inputs[]` and the multi-file `source_input_hash` derivation
  above are load-bearing from the very first extraction, not an
  edge case to add later.

None of this is a checklist to satisfy in the abstract — it is the same set
of design questions `gutenberg-epub.md` and `plain-text.md` already had to
answer for their own source shapes, offered here as a starting point for the
co-design conversation, not as a spec this file is pre-committing to on the
new source's behalf.

## See also

- [`README.md`](./README.md) — why v1 ships exactly three adapters and no
  generic parser framework, and the "two senses of proven" distinction that
  keeps `custom`'s experimental status from being confused with a
  not-yet-piloted-but-provable status.
- [`gutenberg-epub.md`](./gutenberg-epub.md) / [`plain-text.md`](./plain-text.md)
  — the two shipped presets, useful as starting patterns for a co-design
  session, not as templates `custom` extends.
- [`../ledger-and-resumability.md`](../ledger-and-resumability.md) — full
  `cache_key.py` field derivation, including every hash a `custom`
  extractor's output feeds into.
- [`../false-green-gate.md`](../false-green-gate.md) — `validate_draft.py`'s
  checks, including the `body_refs_only` sentinel-lite marker-survival
  check a `custom` extractor's footnote handling must feed correctly.
- [`../verse-policy.md`](../verse-policy.md) — the verse-placeholder
  bijection invariant that applies under every `verse_policy.mode`,
  including for a `custom`-extracted source.
- `SKILL.md`, Step 0c — the orchestrating-session procedure that resolves
  `source.format` to this file and runs the two procedural checks described
  above.
