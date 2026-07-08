# `plain_text` adapter

**Status: fully specified, tests planned but not yet run.** This is design
decision 5 (plan §19 / inventory §18): the
mandatory release-gate pilot run may exercise `gutenberg_epub` or
`plain_text`, operator's choice — but whichever one it does *not* exercise
carries this same "experimental/unstable, not yet pilot-proven with the new
ledger machinery" label everywhere (this doc, the README table, the
marketplace listing) until a real second-project pilot run actually clears
it. Nothing about the spec below is deferred or half-built because of this —
it is a labeling distinction, not a scope gap. See
[`README.md`](./README.md#two-different-senses-of-proven--do-not-conflate-them)
for the full "two senses of proven" framing this status inherits.

A lighter adapter for non-EPUB sources: a `.txt` transcription, a scraped web
novel, OCR output. Selected via `source.format: plain_text` in `profile.yml`;
its own knobs live under `source.adapter_config.plain_text` (see
`assets/profile.example.yml`'s shipped block for the exact shape; the same
field shape and enum values are shown below).

This adapter never parses markup. It needs neither `beautifulsoup4` nor `lxml`,
and the plain-text path must not import `bs4`; those dependencies belong only
to `gutenberg_epub`.

## `adapter_config.plain_text` — exact shipped shape

```yaml
plain_text:
  segmentation:
    method: blank_line_run  # blank_line_run | heading_regex
    blank_line_threshold: 2 # blank_line_run only -- REQUIRED, a positive integer, when this method is active
    heading_regex: null     # heading_regex only -- REQUIRED, a compilable regex, when this method is active
  verse_detection: CHOOSE_none_confirmed_or_regex
    # none_confirmed | regex -- REQUIRED, no default, no `manual` option (see below)
  verse_regex: null
    # Only meaningful, and then REQUIRED, when verse_detection: regex; ignored
    # (must stay null) for none_confirmed.
  footnotes: CHOOSE_none_confirmed_or_markdown_ref_or_custom_regex
    # none_confirmed | markdown_ref | custom_regex -- REQUIRED, no default
  footnote_anchor_regex: null   # required (and only meaningful) when footnotes: custom_regex
  footnote_def_regex: null      # required (and only meaningful) when footnotes: custom_regex
```

The active method's requirements are validated FATALLY at Step 0, before
extraction ever runs — the same discipline `verse_detection`/`verse_regex` and
`footnotes`/`footnote_*_regex` already get. A malformed `segmentation` value
(a missing `heading_regex` under `method: heading_regex`, a non-positive
`blank_line_threshold` under `method: blank_line_run`) is rejected at Step 0,
never left to fail — or silently misbehave — mid-extraction. If the sibling
field belonging to the unselected method is non-null, `profile_validate.py`
prints a named non-fatal warning that the value is present but ignored. This
block is validated for basic shape only while a different `source.format` is
active; its specialized rules become load-bearing only once `plain_text` is
genuinely selected.

## Segmentation

Two mutually exclusive methods, chosen by `segmentation.method`:

- **`blank_line_run`** — a run of `blank_line_threshold` or more consecutive
  blank lines marks a segment boundary. `blank_line_threshold` is REQUIRED
  and must be a positive integer under this method. A non-null `heading_regex`
  is warned as ignored, not fatal.
- **`heading_regex`** — a line matching the REQUIRED, compilable
  `heading_regex` starts a new segment. Under this method,
  `blank_line_threshold` is not consulted; if non-null, it is warned as
  ignored, not fatal.

There is no content-based classification heuristic to fall back on the way
`gutenberg-epub.md`'s `classify_spine_item` has (spine items are already
discrete files with their own signals) — the plain-text path reads
`segmentation`/`verse_regex` directly, with no default fallback, which is
exactly why the active segmentation field is fatally validated at Step 0
rather than allowed to misbehave silently.

## Verse detection — `verse_detection` enum

REQUIRED, two values, no default:

- **`none_confirmed`** — a deliberate, visible "this source has no verse"
  choice, not a silent default.
- **`regex`** — paired with a REQUIRED `verse_regex`; lines matching it are
  registered as verse.

**No `manual` option in v1.** An earlier design considered a mode where a
project flags verse spans by hand, but it has no actual machine-readable
contract — no profile fields for markers/ranges, no way for
`extract.py.template` to read the convention structurally. A plain-text
source whose verse can't be found by regex is, for v1, not a `plain_text`
source at all — use `source.format: custom` and co-design the extraction
with the skill instead (see [`custom.md`](./custom.md)).

`verse_detection` is orthogonal to `verse_policy.mode` (the profile-level
enum governing what happens to verse once detected — see
[`../verse-policy.md`](../verse-policy.md)), exactly the same
detection-vs-disposition split `footnotes` has against
`footnotes.apparatus_policy` below. The one invariant that never regresses
regardless of adapter — per-block verse-placeholder bijection enforced
structurally under every `verse_policy.mode` including `skip` — applies here
identically; `verse_detection` only decides whether a block is registered as
verse at all, never whether its placeholder-fidelity check runs once it is.

## Footnote detection — `footnotes` enum

Footnote DETECTION is a separate, orthogonal setting from
`footnotes.apparatus_policy` (detection mechanism vs. translate/preserve/omit
decision — the same relationship `verse_detection` has to `verse_policy.mode`
above). `source.adapter_config.plain_text.footnotes` (REQUIRED enum):

- **`none_confirmed`** — no footnotes to detect, a stated choice.
- **`markdown_ref`** — a fixed parser for the standard `[^N]` anchor +
  trailing numbered-endnote-list convention.
- **`custom_regex`** — `footnote_anchor_regex`/`footnote_def_regex` define
  this source's own convention; both REQUIRED under this value, ignored
  otherwise.

## `footnotes.apparatus_policy` — the four enum values, this adapter's behavior

Given `footnotes` resolves to `markdown_ref` or `custom_regex` — under
`none_confirmed`, all four `apparatus_policy` values are simply a no-op,
there is nothing detected to apply a policy to:

| `apparatus_policy` | Behavior for `plain_text` |
|---|---|
| `translate_all` | Every detected footnote reference/definition pair is translated. |
| `preserve_source` | Every detected footnote definition is carried through UNTRANSLATED (original-language text kept verbatim in the output). |
| `omit_apparatus` | Every detected footnote definition is dropped entirely; the anchor in body text is also removed, not left dangling. |
| `body_refs_only` | No `FN:{N}` definition block is extracted or carried AT ALL. The original anchor position is kept as an ordinary literal marker baked directly into the block's plain text (e.g. a literal `[N]` character sequence) — NOT the `⟦FNREF_N⟧` sentinel, since that sentinel's contract presumes a matching `FN:{N}` target to check placeholder fidelity against, and there is nothing to point at under this policy. Net effect: a reader sees "there was a note here" without the apparatus being extracted, translated, or checked. **Marker survival IS checked:** `extract.py.template` records each block's literal marker string(s) verbatim in `body_ref_markers: [string]`; `validate_draft.py` runs the sentinel-lite check (see [`../false-green-gate.md`](../false-green-gate.md)) confirming every recorded marker still appears, at the same multiset count, in the translated text — a best-effort substring/count check, weaker than the other three policies' full placeholder-fidelity guarantee, but real. |

`extract.py.template`'s footnote-grouping loop does exactly one of three
things based on the resolved policy, identically to the `gutenberg_epub`
adapter: (a) `translate_all`/`preserve_source` — builds the `FN:{N}` block
table + body `⟦FNREF_N⟧` sentinel normally (these two differ ONLY at
translation stage); (b) `body_refs_only` — builds no apparatus, replaces the
anchor with a plain literal marker, DOES record it in `body_ref_markers[]`;
(c) `omit_apparatus` — skips the table AND strips the anchor entirely,
nothing left behind, no `body_ref_markers` entry either. `segpack.py`, the
task prompt templates, and `validate_draft.py` all branch on this same
three-way distinction consistently. `validate_draft.py` runs ZERO
footnote-content checks under `body_refs_only`/`omit_apparatus` (no apparatus
to check coverage against) — but under `body_refs_only` specifically still
runs the sentinel-lite marker-survival check.

## The shared `extract.py.template` core (unchanged from every other adapter)

This adapter's extractor is the same `extract.py.template` file every other
adapter starts from, with its `# ADAPT-POINT:` sections filled in for
plain-text's own segmentation/verse-detection/footnote-grouping logic — never
a separate script. The generic, format-independent core stays intact
regardless of adapter:

- Block-ID assignment, sha1 hashing, the `order_index` re-ranking pass.
- The full round-trip self-check suite: bijection, uniqueness,
  coverage-no-holes, spine-order, segmentation-nonempty, sentinel-uniqueness,
  front-back inventory, verse-structure.
- `no_segment_exceeds_max_words` — blocking; fails the whole extraction if
  any segment's `word_count` exceeds `project.max_segment_words`, naming
  every offending segment. This is v1's sub-chunking-cut mitigation, and it
  applies to `plain_text` exactly as it does to every other adapter — a
  plain-text source with a genuinely long natural chapter is out of scope
  for v1 the same way an EPUB with one is (see the non-goals list).
- Stamps `manifest.json`'s `generation_hashes.source_extraction_hash` and
  `.source_input_hash` the moment extraction completes (via `cache_key.py`,
  two-phase write — draft manifest with hashes not yet stamped, hashes
  computed against that draft, one final write with both hashes merged in).
- Validates the resulting `manifest.json` against `manifest.schema.json`
  immediately after extraction, alongside (never instead of) the round-trip
  self-check suite.

For `plain_text` specifically, the `# ADAPT-POINT:` sections consult
`adapter_config.plain_text.segmentation`/`.verse_regex`/`.footnotes`/
`.footnote_anchor_regex`/`.footnote_def_regex` directly — there is no
content-based classification heuristic to fall back to first (unlike
`gutenberg_epub`'s `classify_spine_item`/`classify_frontback_block`, which
consult their own `adapter_config` overrides only before falling back to a
default heuristic). Because this path has no fallback, the active/load-bearing
fields are fatally validated at Step 0 rather than left to fail — or silently
misbehave — mid-extraction; inactive segmentation sibling values are warned as
ignored, not treated as fatal.

There is no `FRONTBACK:{id}` mechanism for this adapter — that concept is
specific to `gutenberg_epub`'s spine-item classification (front/back-matter
spine files are an EPUB-specific structural signal a plain-text source
doesn't have). A plain-text source's `manifest.json` still carries the
REQUIRED `frontback: [{id, decision}]` array from the shared
`manifest.schema.json` contract, unconditionally present as an empty array
(`minItems: 0`) — never an omitted field.

## Output contract

Extraction under this adapter produces the exact same `manifest.json` shape
every other adapter produces, validated by the same `manifest.schema.json` —
see [`README.md`](./README.md#the-shared-output-contract) for the complete
shared shape (block-ID/`order_index` model, `spine[]`, `segments[]`,
`footnotes[]`, `verse.store`, `generation_hashes.source_extraction_hash`,
`source_inputs: [string]`, `generation_hashes.source_input_hash`,
`frontback: [{id, decision}]`). There is nothing plain-text-specific about
the output shape itself — only the extraction logic that produces it
differs from `gutenberg_epub`.

For `plain_text`, `source_inputs` is the one-entry array `[source.path]`.
`source_extraction_hash` is the sha1 of canonical JSON `{format:
source.format, adapter_config: <ONLY source.adapter_config.plain_text>}`,
concatenated with `${durable_root}/extract.py`'s raw bytes. `source_input_hash`
is the sha1 of canonical JSON `{source_path: <resolved source.path STRING
itself>, source_bytes_sha1: <sha1 of the source file's raw bytes>}`.

## See also

- [`README.md`](./README.md) — the shared output contract, the two senses of
  "proven," and why v1 ships exactly three adapters with no generic parser
  framework above them.
- [`gutenberg-epub.md`](./gutenberg-epub.md) — the proven EPUB adapter, for
  contrast: spine-item classification instead of segmentation heuristics,
  `FRONTBACK:{id}` handling this adapter has no equivalent of.
- [`custom.md`](./custom.md) — the escape hatch for a plain-text source
  whose verse or footnote convention can't be captured by `verse_regex`/
  `footnote_anchor_regex`/`footnote_def_regex`.
- [`../false-green-gate.md`](../false-green-gate.md) — the full 6-check
  `validate_draft.py` spec, including the sentinel-lite marker-survival
  check `body_refs_only` relies on.
- [`../verse-policy.md`](../verse-policy.md) — the `verse_policy.mode` enum
  and the bijection invariant that holds regardless of `verse_detection`.
