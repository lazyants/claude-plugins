# Source prep — adapter gap, clean text, EPUB-wrap, segmentation

## Contents
- The adapter gap (why you must EPUB-wrap)
- Get clean source text (pdftotext)
- EPUB-wrap mechanics
- Segmentation (marker-snapped)
- Structure attestation gate (siman lists)

## The adapter gap — `plain_text`/`custom` are documented but UNIMPLEMENTED

The shipped `assets/templates/extract.py.template` is the **gutenberg_epub** extractor and only that: `load_profile()` hard-`die()`s if `source.format != "gutenberg_epub"` (the format gate), `build()` unconditionally opens `source.path` as a ZIP/EPUB, and the two-phase hash hardcodes the gutenberg adapter config. Yet `references/source-format-adapters/plain-text.md` claims the same template gets its `# ADAPT-POINT:` sections "filled in for plain-text" — a docs↔executable MISMATCH. Trust the executable: there is no plain_text branch to adapt into.

Consequence: you CANNOT translate a `.txt`/OCR/scraped source through the shipped code. Fastest workaround = wrap the text as a **minimal EPUB** and run the proven `gutenberg_epub` adapter. The extractor's self-check region (`extract.py.template:1108–1277`) is genuinely GENERIC (grep confirms zero project literals), so a 0-footnote / 0-verse / 0-frontmatter Hebrew EPUB passes it. That region is hash-pinned by `validate_extraction.py`, so only the four `# ADAPT-POINT:` regions are editable.

## Get clean source text

Prefer `pdftotext -layout` on a PDF with a real text layer:
- Embedded Yiddish stays linear/correct under `-layout`; the **default mode reverses it** and `-raw` **fully reverses** it.
- Vocalized Hebrew comes out word-fragmented (intra-word spaces, e.g. `רב נו א מ ר`) in EVERY extractor including PyMuPDF — the model reconstructs it during translation (proven: an existing RU translation was made from the same fragmented dump). Do not try to de-fragment it yourself.
- Degraded per-line OCR dumps (garbled glyphs) are worse than `pdftotext` on a real text layer — prefer the text layer. Some source books ship only a finished translation and no clean original; there is nothing to extract from those.

**No source-fidelity gate exists** — a splitter that drops or duplicates text passes W2 green. This is a real gap; you are the only check on completeness.

## EPUB-wrap mechanics

Build a 4-file EPUB:
1. `mimetype` — STORED (uncompressed) and FIRST in the archive
2. `META-INF/container.xml`
3. `content.opf` — single spine
4. `content.xhtml`

Rules for `content.xhtml`:
- The FIRST `<body>` child MUST be `<h2>` (anything before the first `<h2>` is classified as frontmatter).
- One `<h2>` per chapter; one `<p>` per paragraph.
- NO verse or footnote markup.
- Headings in the uncased script only — a Latin heading becomes a cased name candidate and breaks `--no-names-confirmed`.
- Force body classification with `adapter_config.gutenberg_epub.spine_overrides: {"content.xhtml":"body"}` (the heuristic otherwise wants a footnote-anchor signal a plain doc lacks).

## Segmentation — marker-snapped, NOT per-page and NOT a full siman ledger

For a book of numbered sections (simanim) where raw extraction yields far more bare marker-lines than real sections (embedded other-volume markers, inline cross-refs, stray front-matter, dups, out-of-order), a full gematria backbone is fragile — a greedy build broke at siman 164. Robust rule: a chapter boundary = the next bare-marker line AFTER each ~2600-word mark → ~40 chapters, every one starting at a real marker (no mid-section or mid-name split), all < 6000 words.

## Structure attestation gate (siman lists)

The model HALLUCINATES STRUCTURE, not just prose — e.g. it emitted a `## ב-תרצח` heading for a section that appears in NO source block (a plausible in-range gematria number the source simply lacks). Therefore:
- Gate every model-produced heading against SOURCE ATTESTATION — the marker must lead an actual source block — never against "it falls in the chapter's gematria window." In-range plausibility ≠ existence.
- Build the authoritative per-chapter siman list from the EPUB `<h2>` gematria WINDOWS `[chapter_start, next_chapter_start)`, deduped by gematria. This excludes inline cross-refs in BOTH directions — a monotonic-only filter WRONGLY drops a real siman when a forward cross-ref inflates the running max.
