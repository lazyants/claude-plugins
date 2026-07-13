# Source-format adapters

`profile.yml`'s `source.format` is always explicit, never sniffed, and resolves
(Step 0c) to exactly one file in this directory. Filename resolution lowercases
the value, maps underscore to hyphen, appends `.md`, and halts naming the
available files if missing. v1 scopes to exactly **three** named source
formats — no more, no generic parser framework sitting above them — but only
one of the three currently works: **one working built-in adapter
(`gutenberg_epub`) plus a supported expert-mode `custom`-extractor path;
`plain_text` is specified but not yet implemented (#62)**. That three-way
split is a deliberate design decision (see "Why only three" below), not an
oversight.

| `source.format` | Adapter doc | Input shape | Status |
|---|---|---|---|
| `gutenberg_epub` | [`gutenberg-epub.md`](./gutenberg-epub.md) | Project Gutenberg-style EPUB | Extraction/translation fidelity is **source-proven** against `historiettes-t3` specifically; see caveat below |
| `plain_text` | [`plain-text.md`](./plain-text.md) | `.txt` transcription, scraped web novel, OCR output | **Specified but NOT YET IMPLEMENTED** — `source.format: plain_text` is rejected FATALLY by `extract.py.template`'s format gate; tracked by #62 |
| `custom` | [`custom.md`](./custom.md) | Anything else | **Experimental/unstable until a real project exercises it end-to-end** — parsing is co-designed per project, output contract is fixed |

## Adapter-specific load-bearing rules

- `gutenberg_epub`: files are not chapters; spine items are classified by
  content, not filename, with
  `source.adapter_config.gutenberg_epub.spine_overrides` available as a
  per-file override; front/back elements are classified by
  `classify_frontback_block`, with
  `source.adapter_config.gutenberg_epub.frontback_overrides` available as a
  per-`FRONTBACK:{id}` decision override; footnote anchor/definition bijection
  is a cheap validity check; footnote definitions are grouped over the
  concatenated stream, not per file; verse markup is the union of
  container-class and bare-line/stanza fallback; extraction uses a custom
  block-boundary-only text extractor, never `get_text(" ")`. This is the
  only adapter path that uses `beautifulsoup4`/`lxml`: `extract.py.template`
  must wrap missing imports with an actionable `pip install -r requirements.txt`
  message naming the specific missing package, and must also preflight the
  parser backends by parsing `BeautifulSoup("<a>x</a>", "lxml")` and
  `BeautifulSoup("<a>x</a>", "xml")` in separate try/except blocks.
- `plain_text`: segmentation is `adapter_config.plain_text.segmentation` with
  `method: blank_line_run` plus required positive `blank_line_threshold`, or
  `method: heading_regex` plus required compilable `heading_regex`. Verse
  handling is the required enum
  `source.adapter_config.plain_text.verse_detection`: `none_confirmed` |
  `regex`; `regex` requires `verse_regex`. There is no `manual` option in v1;
  a plain-text source needing hand-marked verse must use `source.format:
  custom`. The plain-text path reads its segmentation, `verse_regex`, and
  footnote regex settings directly, with no content-based fallback heuristic,
  and must not import `bs4` or depend on `beautifulsoup4`/`lxml`.
- `custom`: `source.adapter_config.custom.extractor_path` is required whenever
  `source.format: custom` and its value is `string | null`. If it is `null`,
  Step 0c halts for co-design; if non-null, it is rejected before existence
  checking when it contains `..` or starts with `/`; the non-null schema shape
  is additionally constrained by `pattern: "^[A-Za-z0-9._/-]+$"`. The value is
  then resolved under `${durable_root}/scripts/custom_extractors/<value>` and
  checked for existence. `source.path` remains required and existence-checked
  for every format; for `custom`, it names the primary/representative input,
  not the full set of files read.

## Why only three, no generic framework

Design decision 1 (plan §19 / inventory §18): keep
`references/source-format-adapters/` as a directory, but scope it to exactly
these three named formats — one working built-in adapter (`gutenberg_epub`),
a supported expert-mode `custom`-extractor path, and `plain_text` (specified
but not yet implemented, #62) — never a generic parser framework above them.
`custom`'s source-specific parsing logic stays deliberately undocumented and
co-designed per project — a truly custom source can't be pre-documented —
but its **output contract is fixed, mandatory, and schema-validated**
(`manifest.schema.json`) exactly like `gutenberg_epub`'s output (and
`plain_text`'s output, once #62 lands). There is no generic parser framework
behind these three, and none is planned for v1: building one now would be
premature abstraction over a sample size of two (`gutenberg_epub` and
`plain_text` are the only two real, specified extraction strategies this
plugin has ever had to generalize from).

## The shared output contract

Regardless of which adapter runs, extraction must produce a `manifest.json`
matching the same shape, validated by `manifest.schema.json` immediately
after extraction (alongside, never instead of, the round-trip self-check
suite `extract.py.template` runs): the block-ID/`order_index` model,
`spine[]`, `segments[]` (explicitly inclusive of translate-decision
`FRONTBACK:{id}` units, not just body content), `footnotes[]`, `verse.store`,
`generation_hashes.source_extraction_hash` (required), `source_inputs: [string]`
(required, `minItems: 1`) plus `generation_hashes.source_input_hash`
(required), and `frontback: [{id, decision}]` (required array, `minItems: 0`,
`id` pattern `^FRONTBACK:.+$`, `decision` one of `translate` | `regenerate` |
`omit`). A cross-reference invariant — every `frontback[]` entry with
`decision:"translate"` must have a matching `id` in `segments[]`; every
`regenerate`/`omit` entry must NOT appear in `segments[]` — is checked
procedurally by the self-check suite, never schema-expressible, and is a
fatal named failure either way.

Hash stamping is part of the adapter contract. `source_extraction_hash` is the
sha1 of canonical JSON `{format: source.format, adapter_config: <ONLY the ONE
sub-block matching the resolved format, never the whole adapter_config
object>}` concatenated with the resolved extractor file's raw bytes
(`${durable_root}/extract.py` for `gutenberg_epub` — and for `plain_text`,
once #62 implements it — or the resolved `adapter_config.custom.extractor_path`
file for `custom`). `source_input_hash` is the sha1 of canonical JSON
`{source_path: <resolved source.path STRING itself>, source_bytes_sha1: <see
below>}`. For `gutenberg_epub` (and `plain_text`, once #62 implements it),
`source_bytes_sha1` is the sha1 of the source file's raw bytes and the
adapter also emits `source_inputs: [source.path]`. For
`custom`, the extractor must emit `source_inputs: [string]` in read order, and
`source_bytes_sha1` is the sha1 of canonical JSON `[{filename, sha1: <sha1 of
THAT file's raw bytes>}]`, one entry per file, sorted by filename, hashing
`{filename, sha1(bytes)}` pairs — never bare sorted-and-concatenated bytes.
Extraction writes a draft manifest first (`source_inputs[]` populated,
`generation_hashes.source_extraction_hash`/`.source_input_hash` absent and not
yet schema-valid), computes both hashes via `cache_key.py --field ...`, merges
them into memory, then performs one final validated atomic write.

The schema is the machine-checkable half of the contract; the self-check
suite (bijection, uniqueness, coverage-no-holes, spine-order,
segmentation-nonempty, sentinel-uniqueness, front-back inventory,
verse-structure, plus the blocking `no_segment_exceeds_max_words` check, which
fails extraction if any segment's `word_count` exceeds
`project.max_segment_words`, naming every offender) is the semantic half.
Neither substitutes for the other. A `custom` adapter's
extractor must pass the same self-check suite (or a documented equivalent
covering the same invariants) — a custom source is exempt from *how*
block/footnote/verse detection works, never from *what* it must ultimately
produce and prove.

`extract.py.template`'s format-independent core — block-ID assignment, sha1
hashing, `order_index` re-ranking, the self-check suite, hash stamping, and
schema validation — stays intact across adapters. Source-specific work belongs
only in the marked `# ADAPT-POINT:` areas around `classify_spine_item`,
`classify_frontback_block`, the footnote-grouping loop, and the verse-detection
loop. The Gutenberg points consult
`source.adapter_config.gutenberg_epub.spine_overrides` and
`source.adapter_config.gutenberg_epub.frontback_overrides` before adapter
defaults. (Once `plain_text` is implemented — #62 — its adapt-points will
read `source.adapter_config.plain_text.segmentation`, `.verse_regex`,
`.footnote_anchor_regex`, and `.footnote_def_regex` directly; the shipped
`extract.py.template` currently fills only the `gutenberg_epub` adapt-points
and FATALs on any other `source.format`.)

`footnotes.apparatus_policy` (four enum values: `translate_all` |
`preserve_source` | `omit_apparatus` | `body_refs_only`) is also defined
per-adapter, orthogonal to footnote *detection*. See each adapter's own page
for how detection resolves for that source shape, and
[`../false-green-gate.md`](../false-green-gate.md) for the `body_refs_only`
sentinel-lite marker-survival check.

For `plain_text`, once implemented (#62), footnote detection is specified as
the required enum `source.adapter_config.plain_text.footnotes`:
`none_confirmed` | `markdown_ref` | `custom_regex`; `custom_regex` is paired
with `footnote_anchor_regex`/`footnote_def_regex`. Under `none_confirmed`, all
four `apparatus_policy` values are no-ops because there is no detected
apparatus to apply them to. Its extraction loop is specified to have exactly
three apparatus branches, identically to `gutenberg_epub`'s already-shipped
loop: `translate_all`/`preserve_source` build the `FN:{N}` table plus body
`⟦FNREF_N⟧` sentinel; `body_refs_only` builds no apparatus, keeps a literal
body marker, and records it in `body_ref_markers[]`; `omit_apparatus` builds no
apparatus, strips the anchor, and records no marker. `validate_draft.py`
already runs zero footnote-content checks under
`body_refs_only`/`omit_apparatus` for `gutenberg_epub`, and will for
`plain_text` too once it exists — `body_refs_only` specifically still runs the
sentinel-lite marker-survival check.

## Two different senses of "proven" — do not conflate them

This distinction is design decision 5 (plan §19 / inventory §18) and it
governs how to read every adapter's status line above:

- **`gutenberg_epub`'s extraction/translation fidelity** is source-proven —
  generalized from the real, proven `historiettes-t3` project's own
  `extract.py` and cross-checked against it. That claim is older and
  narrower than the rest of this plugin: it predates the ledger machinery
  entirely, and it is proven against **that one book**, not against EPUBs
  in general.
- **This plugin's v1 stability** — the ledger-fragment/cache-key/
  derivation-state machinery, `profile_semantics_hash`, and `gutenberg_epub`'s
  adapter path — is proven only once a real second-project pilot run actually
  exercises it, and that is unknown until the pilot runs. `gutenberg_epub` has
  **not** already cleared that bar merely because of its older, narrower
  extraction-fidelity claim. (`plain_text` isn't pilot-eligible at all yet —
  it is specified but not implemented, #62; once it lands, it inherits this
  same not-yet-piloted status.)

The mandatory release-gate pilot (a genuine run against a second real book,
not `historiettes-t3` again) is currently scoped to `gutenberg_epub` only —
the one working, shipped adapter. `plain_text` is specified but not yet
implemented (`extract.py.template` FATALs on it, #62) and cannot be the
pilot's exercised adapter until #62 lands; once it does, `plain_text`
inherits the same "experimental/unstable, not yet pilot-proven with the new
ledger machinery" label `custom` already carries everywhere (this doc,
`custom.md`, the marketplace listing) until its own pilot run clears it.
That is a labeling distinction, not a scope change — nothing about
`plain_text`'s spec is deferred or removed, only its implementation (#62).
`custom` can't be pre-validated the way a shipped preset can either, so its
promotion to stable is its own separate, later milestone, independent of the
`gutenberg_epub` pilot.

One further status note specific to `FRONTBACK:{id}` handling in
`gutenberg_epub`: routing front/back-matter elements through the same
segment/ledger/review pipeline as body content is **new plugin hardening**,
generalizing an intent the real `historiettes-t3` project's own plan
document stated but never actually built (its real, shipped code handles
front/back matter via a separate, hand-maintained `frontmatter_ru.json` file
entirely outside the ledger and review pipeline). Treat this specific
mechanism — not the rest of `gutenberg_epub`'s spine/footnote/verse
extraction — with the same "carefully designed, not yet run at scale"
confidence as the ledger subsystem itself, regardless of the adapter's
overall proven status above.

## See also

- [`gutenberg-epub.md`](./gutenberg-epub.md) — the proven EPUB adapter, spine
  classification, footnote grouping, verse detection, `FRONTBACK:{id}`
  handling.
- [`plain-text.md`](./plain-text.md) — segmentation heuristics, the
  `verse_detection` and `footnotes` enums, why there is no `manual` verse
  option.
- [`custom.md`](./custom.md) — the co-design process, the mandatory output
  contract, `source_inputs[]`, and the two-phase manifest write.
- [`../false-green-gate.md`](../false-green-gate.md) — placeholder-fidelity
  checks referenced by the `apparatus_policy` table above.
- [`../ledger-and-resumability.md`](../ledger-and-resumability.md) — the
  cache-key/ledger machinery whose v1 stability is adapter-agnostic and
  pilot-gated, per the "two senses of proven" section above.
