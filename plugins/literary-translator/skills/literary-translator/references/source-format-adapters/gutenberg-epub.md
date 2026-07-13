# The `gutenberg_epub` adapter

`source.format: gutenberg_epub` selects this adapter for a Project
Gutenberg-style EPUB — the input shape `historiettes-t3` was actually built
against. This is the one adapter in
[`README.md`](./README.md)'s three-way table whose **extraction/translation
fidelity is source-proven** — read that claim narrowly, see "What
'source-proven' actually means" below before trusting any part of this file
as more broadly validated than it is.

## What "source-proven" actually means here — and what it does not

The claim is specific, not general:

- **Proven**: the spine-classification heuristic, the custom block-boundary
  text extractor, the footnote anchor↔definition bijection, the
  cross-notes-file footnote grouping, and the verse-container detection
  below are generalized directly from `historiettes-t3`'s real, executed
  `extract.py` — read verbatim in this file where it matters — and that
  script ran successfully, with its checks passing, against one real book
  (`Les Historiettes de Tallemant des Réaux`, tome 3, Project Gutenberg
  ebook 39314, 76 segments, 423 footnotes).
- **Proven against**: *that one EPUB's* specific markup conventions — its
  particular `x-ebookmaker-pageno` class, its particular
  `FNanchor_N`/`Footnote_N` id convention, its particular
  `.poetry-container`/`.stanza`/`.line` verse markup, its particular
  Project-Gutenberg-generated front/back boilerplate. It is **not** proven
  against Gutenberg EPUBs in general — a different Gutenberg-sourced book
  can and often will use different id conventions, different front-matter
  markup, or no verse markup at all. Any new project using this adapter
  against a *different* source still needs the same design discipline this
  file documents (classify by content, never by filename; verify the
  bijection; never use `get_text(" ")`), but treat every literal
  regex/id-convention/class-name below as a **starting template to adapt**,
  not a guarantee that it will match a different book's markup unchanged.
- **Not proven at all — regardless of the extraction claim above**: the
  ledger/cache-key/derivation-state machinery this plugin wraps around
  extraction (`ledger-and-resumability.md`), and specifically the
  `FRONTBACK:{id}` → `segments[]` → ledger → review pipeline described
  below. Both are new for this plugin. `historiettes-t3`'s real, shipped
  code never ran front/back matter through anything like a ledger at all —
  see "`FRONTBACK:{id}` handling" below for the direct-inspection evidence.
  Treat that specific mechanism with the same "carefully designed, not yet
  run at scale" confidence as the rest of the ledger subsystem, independent
  of this adapter's older and narrower extraction-fidelity claim.

## Files ≠ chapters

An EPUB's spine is a flat, mechanically-paginated list of XHTML files — a
publisher-side pagination artifact, not a content boundary. A single
novella/chapter can span multiple spine files; a single spine file's
`<body>` can contain multiple `<h2>`-headed segments. `extract.py` never
segments by file — it walks the concatenated stream of each spine file's
top-level body children and starts a new segment only on an `<h2>`, exactly
as described below under "Body segmentation." Any adapter (or per-project
override) that tries to treat one spine file as one segment will silently
misclassify convergent or split chapters.

## `classify_spine_item` — classify by content, never by filename

Source-plan contract, generalized from `reference_gutenberg_epub_structure.md`
and the real `extract.py` logic: classify each spine item by content signals,
not by filename.

- **`body`** — body-content structure: heading signal plus footnote-anchor
  signal. Do not reduce this to "any XHTML file" or to filename patterns, and
  do not reduce it to headings alone.
- **`footnote-defs`** — notes apparatus: many footnote-definition ids and no
  body headings.
- **`front-back`** — wrapper/cover/TOC/colophon/transcriber's-note material,
  including files that are neither body content nor notes apparatus.

**Watch for a stray heading misclassifying a front/back file as body** — this
is the explicit edge case in the source plan. A decorative or wrapper heading
inside cover/title/TOC material is not enough reason to route the whole spine
item through body segmentation; check it against the other content signals and
use `spine_overrides` when one source file needs a per-project correction.

**Per-project override**: `source.adapter_config.gutenberg_epub.spine_overrides`
(see "adapter_config fields," below) lets a project force a specific file's
classification when the content-signal heuristic gets one file wrong, without
touching the heuristic itself.

## The custom block-boundary text extractor — never `get_text(" ")`

For serialized translation text, `extract.py` never uses BeautifulSoup's
`get_text(" ")`. It walks the tree itself, inserting a separator **only** at
block-tag boundaries, never between inline children:

```python
BLOCK_TAGS = {"p", "div", "br", "h1", "h2", "h3", "h4", "h5", "h6",
              "blockquote", "li", "ul", "ol", "table", "tr", "td", "hr"}

def block_text(node) -> str:
    """get_text() that inserts a separator only at block boundaries, not inline."""
    parts = []
    def rec(n):
        if isinstance(n, NavigableString):
            parts.append(str(n)); return
        if not isinstance(n, Tag):
            return
        block = n.name in BLOCK_TAGS
        if block: parts.append("\n")
        for c in n.children: rec(c)
        if block: parts.append("\n")
    rec(node)
    return "".join(parts)
```

`get_text(" ")` would inject a space at *every* tag boundary, including
inline ones — corrupting two things this project actually hit:

1. **Drop-cap-split inline markup** — a word opening with `<b>C</b>ertes`
   (a stylized drop-cap on the first letter) must serialize as `Certes`,
   not `C ertes`. The real self-check `verse_fidelity_no_dropcap_split`
   exists specifically because an earlier version of this extractor
   introduced exactly this corruption in verse text: a regex
   `^[BCDFGHJKLMNPQRSTVWXZ] ` (capital consonant + space at a line start)
   flags it as an artifact, since legitimate French one-letter words are
   vowels (`À`, `Ô`, `Y`, `A`), never consonants.
2. **Anchor-sentinel spacing** — a footnote anchor replaced in place by the
   `⟦FNREF_N⟧` sentinel must sit glued to its neighboring punctuation exactly
   as the source had it (`LE MARÉCHAL DE BASSOMPIERRE⟦FNREF_1⟧.`, no
   injected space before the period). The real self-check
   `prose_sentinel_adjacency` asserts both that no `"⟧ ."`/`"⟧ ,"` pattern
   exists anywhere and that `HEAD:seg02`'s exact literal text matches —
   a regression test written because French never has a space before a
   period or comma, so any adjacency in the extracted text is a serialization
   bug, not source content.

Every serialized block (`serialize_prose`) first clones the tag, decomposes
any page-number anchors (`x-ebookmaker-pageno` class — Project
Gutenberg-generated pagination markers, pure noise for translation), replaces
each footnote-anchor `<a id="FNanchor_N">` with the literal string
`⟦FNREF_N⟧`, then runs `block_text()` over the clone and collapses
whitespace runs (`normalize_text`) into a single space. `html` (the cleaned
clone's `str()`) and `plain_text` (the normalized string) are both recorded
per block; `fnrefs` is the list of `N`s found in the sentinel-scan of the
resulting text.

## Body segmentation

Walking each `body`-classified spine file's top-level `<body>` children in
document order:

- An `<h2>` starts a new segment: `start_segment` allocates the next
  `segN` id (`seg{counter:02d}`), serializes the heading itself as block
  `HEAD:{seg}`, and resets the per-segment paragraph-order counter.
- Before the *first* `<h2>` in the whole body stream, every top-level child
  is front-matter, not segment content — routed to `classify_frontback_block`
  and recorded as `FRONTBACK:fm{n:02d}` (see below), never silently
  attached to whatever segment happens to follow.
- After the first `<h2>`, each subsequent top-level child that the
  verse-detection loop recognizes (the Historiettes-derived default includes
  `.poetry-container` and the bare `.line`/`.stanza` fallback) becomes its own
  `VERSE:{seg}:{ord}` block; a `blockquote` becomes `QUOTE:{seg}:{ord}`;
  anything else becomes `PARA:{seg}:{ord}` (`{ord}` is a 4-digit, per-segment
  paragraph-order counter, reset at each new `<h2>`). An empty `<p>` (pure
  whitespace) is a structural spacer, silently skipped — not a defect, not a
  block.
- Every ordinary segment emitted from a body-classified spine item is exported
  as `kind: "body"` in `manifest.json`/`segpack.schema.json`; `kind:
  "frontback"` is reserved for translate-decision `FRONTBACK:{id}` units.

`order_index` is assigned twice: once in raw emission order as blocks are
built (body stream, then footnote stream, then front/back stream — not
global source order), then **re-ranked** in a second pass, sorted by
`(spine position of the source file, the raw emission order within that
file/stream)` — this second pass is what actually produces true
spine/reading order across all three streams. The real regression check
`order_index_spine_order` asserts this monotonicity holds against spine
position for every block.

## `classify_frontback_block` — decision + reason, never silent

Real, proven code (`extract.py`):

```python
def classify_frontback_block(tag):
    cls = tag.get("class") or []
    txt = normalize_text(tag.get_text(" "))
    if tag.name == "div" and ("pgheader" in cls or "pgmonospaced" in cls):
        return "omit", "Project Gutenberg boilerplate header"
    if tag.name == "div" and "box" in cls and txt.lower().startswith("note sur la transcription"):
        return "omit", "transcriber note"
    low = txt.strip().upper().rstrip(":")
    if tag.name == "hr":
        return "regenerate", "rule (structural)"
    if "TABLE DES MATI" in txt.upper():
        return "regenerate", "table of contents rebuilt in Russian from segments"
    if low == "NOTES":
        return "regenerate", "notes-section divider rebuilt in Russian"
    if tag.find("img"):
        return "regenerate", "decorative image kept as-is in assembly"
    if not txt:
        return "regenerate", "empty structural block"
    return "translate", "title-page / front-matter text"
```

Every front/back top-level child gets exactly one of three decisions plus a
human-readable `reason` string — never an unclassified pass-through. In
order: Gutenberg's own boilerplate header divs and the transcriber's note
are `omit` (pure noise, never worth translating or regenerating); a
horizontal rule, a "TABLE DES MATIÈRES" block, and a bare "NOTES" divider
are `regenerate` (structural — rebuilt from the segment/footnote data at
assembly time rather than translated verbatim, since a TOC's page numbers
and a notes-divider's position are derived, not authored, text); a
decorative image is `regenerate` (kept as-is, nothing to translate); an
empty block is `regenerate` (nothing there); anything else — actual
title-page or front-matter *prose* — is `translate`.

**The real self-check `frontback_inventory`** asserts every element has a
`decision` in `{translate, regenerate, omit}` and a non-empty `reason`, and
additionally that every `omit` decision's reason string actually mentions
`"boilerplate"` or `"transcriber"` — a deliberate guard against silently
widening `omit` to swallow real content over time.

`extract.py` calls this both for pre-first-`<h2>` front-matter blocks inside
body-classified files (ids `FRONTBACK:fm{n:02d}`) and for every top-level
child of every `front-back`-classified spine file (ids
`FRONTBACK:{slug}_{n:02d}`, `slug` derived from the source filename). One
extra real-code nuance: if a block's own text is empty but it *contains* an
`<h1>`/`<h2>` (a wrapper div around a heading), the classifier re-runs on
that inner heading tag instead of accepting "empty structural block" at face
value — otherwise a cover-page wrapper's actual heading text would be lost
to the empty-block branch.

**Per-project override**:
`source.adapter_config.gutenberg_epub.frontback_overrides` lets a project
force a specific `FRONTBACK:{id}`'s decision when the heuristic gets one
element wrong (see "adapter_config fields," below).

### `FRONTBACK:{id}` handling — new plugin hardening, not a port of proven mechanics

**This is the one part of this adapter that is *not* a generalization of
something `historiettes-t3` actually ran at scale — say so plainly whenever
this section is referenced.** Direct inspection of the real project's own
files: `manifest.json` keeps `frontback` as a separate top-level key (22
entries) from `segments` (76 entries), with zero frontback-shaped entries
inside `segments[]`; `ledger.json` has exactly 75 keys, every one a plain
`segNN` id, zero `FRONTBACK:*` keys anywhere; the real assembly script reads
a wholly separate, hand-maintained `frontmatter_ru.json` file for all
front/back content — never a segment draft file, never through the
translate → review → fix convergence loop, never ledgered. The real
project's own plan document *describes* routing translate-decision
front/back elements through the same pipeline as body content, but the
actually-shipped code never built it that way.

This plugin closes that gap deliberately, as new hardening: each front/back
spine item is decomposed into `FRONTBACK:{id}` elements by
`classify_frontback_block` as above, each resolved to
`translate`/`regenerate`/`omit` (overridable per-element via
`frontback_overrides`). A `translate`-decision element gets its **own**
entry in `manifest.json`'s `segments[]` — `segments[]` is explicitly defined
to include every translatable unit, body and frontback alike — and from
that point on is segpacked, dispatched, reviewed, ledgered, and audited
through the exact same generic pipeline as an ordinary body segment, with no
special-casing anywhere downstream. `regenerate`/`omit`-decision elements do
**not** join `segments[]` — they are recorded only in `manifest.json`'s
`frontback[]` inventory and accounted for in a separate coverage report at
W7/W8, never in the translation dispatch set.

Practical consequence for anyone using this adapter: expect this specific
mechanism to need its own careful watching on a real second-project pilot,
independent of how settled the rest of the adapter is.

## The footnote-grouping loop — group over the concatenated notes stream

Real, proven mechanics: a single footnote's definition can **spill across a
notes-file boundary** — its anchor `<p>` ends file N, its continuation opens
file N+1. `extract.py` processes every `footnote-defs`-classified spine file
as **one concatenated stream**, not per-file, so an in-progress group
(`cur`) stays active across that boundary:

```python
cur = None
for soup, file_label in notes_soups:
    for foot in soup.find_all("div", class_="footnote"):
        for node in foot.children:
            if isinstance(node, Tag):
                a = node.find(id=FN_DEF_RE)
                if a is not None:
                    n = int(FN_DEF_RE.match(a.get("id")).group(1))
                    cur = {"n": n, "file": file_label, "files": [file_label], "nodes": [node]}
                    fn_groups.append(cur)
                    continue
                if cur is not None:
                    cur["nodes"].append(node)
                    if file_label not in cur["files"]:
                        cur["files"].append(file_label)
                elif normalize_text(node.get_text(" ")):
                    orphan_fn.append((file_label, normalize_text(node.get_text(" "))[:40]))
            # ... NavigableString continuation nodes follow the same cur-append/orphan-report split
```

A node whose id matches `Footnote_N` starts a new group (`cur`); any
subsequent sibling — Tag or NavigableString — with no matching id is a
**continuation** of the currently-open group and is appended to it,
regardless of which file it physically lives in; a continuation-shaped node
encountered with `cur is None` (no group open yet) is a genuine orphan,
recorded rather than silently dropped, and fails the real self-check
`no_orphan_footnote_continuation`.

Each finished group is serialized into one `FN:{N}` block: every Tag node
in the group runs through the same `serialize_prose` extractor used for body
blocks (with `strip_labels=True`, additionally decomposing `span.label`
elements — the printed footnote-number label itself, redundant once the
block carries its own `n` field), and every NavigableString node's stripped
text is appended directly. `source_files` on the resulting block lists every
file the group actually spanned (length > 1 for a spilled footnote).

**Bijection as the cheap validity check**: after grouping, `footnotes[]` is
built by joining `anchor_index` (populated at body-serialization time,
keyed by the `N` found in each `⟦FNREF_N⟧` sentinel) with `def_index`
(populated here, keyed by `N`). The real self-check `fn_bijection` asserts
the anchor-`N` set and definition-`N` set are identical (`== 423` for this
project specifically — a project-specific count, not something to copy
verbatim) and separately reports any dangling anchor or dangling definition
by id. `fnref_sentinel_unique` additionally asserts every anchor `N` appears
**exactly once**, in **exactly one** block — catching a duplicated or
misplaced sentinel that a naive bijection-only check would miss.

### `footnotes.apparatus_policy` — the four enum values, as this adapter defines them

`apparatus_policy` is orthogonal to footnote *detection* — for this
adapter, detection is unconditional (the `FNanchor_N`/`Footnote_N` id
convention above; there is no `none_confirmed`/`custom_regex` choice the way
`plain-text.md` has one). `apparatus_policy` governs only what happens to
whatever this adapter's grouping loop already found:

| `apparatus_policy` | Meaning for `gutenberg_epub` |
|---|---|
| `translate_all` | Every `FN:{N}` definition (the full critical apparatus) is translated. This project's actual choice. |
| `preserve_source` | Footnote definitions are carried through **untranslated** — original-language text kept verbatim in the output, for a project that wants the apparatus visible but not re-authored. |
| `omit_apparatus` | Footnote definitions are dropped entirely from output. Anchors in body text are **also removed**, not left dangling — nothing left behind, no trace a footnote ever existed. |
| `body_refs_only` | **No `FN:{N}` block is extracted or carried at all.** The original anchor position is kept as an ordinary **literal** marker baked directly into the block's plain text (e.g. a literal `[N]` sequence) — **not** the `⟦FNREF_N⟧` sentinel, since that sentinel's whole contract presumes a matching `FN:{N}` target to check placeholder fidelity against, and under this policy nothing exists on the other end. Net effect: a reader sees "there was a note here" without the apparatus being extracted, translated, or checked as an apparatus at all. |

The footnote-grouping loop does exactly one of three things based on the
resolved policy (never a translation-stage-only distinction):

1. **`translate_all` / `preserve_source`** — builds the `FN:{N}` block table
   and the body's `⟦FNREF_N⟧` sentinel normally; these two policies differ
   *only* at the translation stage (whether the extracted `FN:{N}` text
   gets translated or is kept verbatim), never in what extraction produces.
2. **`body_refs_only`** — builds no `FN:{N}` table and no sentinel; replaces
   the body anchor with a plain literal marker instead, and **does** record
   that marker's exact string into the owning block's `body_ref_markers[]`
   list (consumed later by `segpack.py` and the sentinel-lite check in
   [`../false-green-gate.md`](../false-green-gate.md)).
3. **`omit_apparatus`** — skips the `FN:{N}` table entirely **and** strips
   the anchor from the body outright, leaving nothing behind — including no
   `body_ref_markers` entry, since there is genuinely nothing to check.

`segpack.py`, the task-prompt templates, and `validate_draft.py` all branch
on this same three-way distinction, consistently — `validate_draft.py` runs
zero footnote-content checks under `body_refs_only`/`omit_apparatus` (no
apparatus to check coverage against either way), but under `body_refs_only`
specifically still runs the sentinel-lite marker-survival check against
`body_ref_markers[]`.

## The verse-detection loop

The source-plan summary is explicit about the detection contract at the level
this adapter document can safely standardize: verse markup is registered by
the **union** of the container-class path and the bare-line/stanza-without-
container fallback. Do not narrow the Gutenberg default to only
`.poetry-container` detection.

For the Historiettes-derived default, that means the verse-detection adapt
point must recognize both:

- container-class verse markup, the Project-Gutenberg/Historiettes convention
  this adapter was proven against;
- bare `.line` / `.stanza` verse markup even when those elements are not
  wrapped in that container class.

That fallback is a real detection fallback, not merely a text-extraction
fallback inside an already-detected container and not merely a fatal
"uncovered line" check. If a new Gutenberg-sourced book has bare stanza/line
markup without the Historiettes container class, the default to copy is:
register it as verse (or adapt the detection loop deliberately), preserve the
same `verse.store`/`VERSE:{seg}:{ord}` output contract, and let the generic
round-trip self-check suite verify the resulting verse structure.

This loop is one of the four `# ADAPT-POINT:` regions in `extract.py.template`.
Adjust the selector set and verse-registration mapping only inside that region
for a new book, and keep the format-independent core unchanged:
block-id assignment, sha1 hashing, `order_index` re-ranking, the structural
self-check suite, `no_segment_exceeds_max_words`, and the final
`generation_hashes`/`manifest.schema.json` validation.

## `extract.py.template`'s generic core — stays intact across all adapters

The following mechanics are format-independent and ship unchanged regardless
of which adapter's `# ADAPT-POINT:`-marked functions are in use:

- **Block-ID assignment** — the `HEAD:{seg}` / `PARA:{seg}:{ord}` /
  `QUOTE:{seg}:{ord}` / `VERSE:{seg}:{ord}` / `FN:{N}` / `FRONTBACK:{id}`
  model itself, and the `add_block` helper's fatal duplicate-id check.
- **sha1 hashing** — every block and every verse-store entry records
  `sha1(plain_text)`, computed identically regardless of source format.
- **The `order_index` re-ranking pass** — raw emission order (body stream,
  then footnote stream, then front/back stream) is re-sorted by
  `(spine position, raw order within stream)` into true reading order, as
  described under "Body segmentation" above.
- **The full round-trip self-check suite** — the structural invariants
  generalize; the literal project-specific assertions do not (see next
  section). The invariants that generalize: block-id bijection (footnote
  anchor↔definition), block-id uniqueness, body-coverage-without-holes,
  spine-order-and-classification-sanity, segmentation-nonempty,
  sentinel-uniqueness (`fnref_sentinel_unique`), front-back inventory
  completeness (`frontback_inventory`), and verse-structure
  reconciliation/no-uncovered-lines.
- **`no_segment_exceeds_max_words`** — **new for this plugin, not present in
  the real `extract.py` at all.** Blocking: fails the whole extraction,
  naming every offending segment, if any segment's `word_count` exceeds
  `project.max_segment_words` (`profile.yml`). This is the v1 mitigation
  for cutting sub-chunking entirely — a segment too long to translate
  whole is a fatal precondition failure, not something the pipeline
  silently splits.
- **`generation_hashes` stamping and schema validation** — the moment
  extraction completes, `cache_key.py --field source_extraction_hash` /
  `--field source_input_hash` stamp `manifest.json`'s
  `generation_hashes.source_extraction_hash`/`.source_input_hash`.
  For `gutenberg_epub`, `source_extraction_hash` is the sha1 of canonical JSON
  `{format: source.format, adapter_config: <the ONE sub-block of source.adapter_config matching the resolved source.format, here source.adapter_config.gutenberg_epub>}`
  concatenated with `${durable_root}/extract.py`'s raw bytes.
  `source_input_hash` is the sha1 of canonical JSON
  `{source_path: <resolved source.path STRING itself>, source_bytes_sha1: <sha1 of the EPUB source file's raw bytes>}`;
  the manifest also populates `source_inputs: [source.path]` for consistency.
  The two-phase write is exact: first write a draft manifest with
  `source_inputs[]` populated and both generation hashes absent, deliberately
  not yet schema-valid and never schema-validated at this draft point; run
  `cache_key.py --field source_input_hash` and
  `cache_key.py --field source_extraction_hash` against that draft's own
  `source_inputs[]`/format/adapter config; merge both hashes into the in-memory
  manifest; then perform one final validated write. `manifest.json` is then
  validated against `manifest.schema.json` immediately afterward,
  **alongside, never instead of**, the round-trip self-check suite above — the
  schema catches a malformed shape, the self-check suite catches a malformed
  semantic invariant; neither substitutes for the other. See
  [`../ledger-and-resumability.md`](../ledger-and-resumability.md) for the
  full two-phase-write / cache-key mechanics.

### What does *not* generalize — project-specific regression pins in the real file

The real `extract.py` also carries several checks that are correct
*evidence this mechanism was proven*, but are Historiettes-specific literal
assertions, not structural invariants a template should ship verbatim:
`fn_bijection`'s `== 423` count, `rouen_has_sonnet`'s named-segment lookup,
`no_pseudo_novellas_from_notes`'s notes-file-set check, the drop-cap
regression's specific "Certes, le trait..." literal string, and
`prose_sentinel_adjacency`'s `HEAD:seg02` literal comparison. The *pattern*
these embody — pin a real, previously-hit defect as a permanent regression
test once you find it — is exactly what a new project should do for its own
source; the specific literals themselves are not portable.

## Per-project adapt points

`# ADAPT-POINT:` marked comments in `extract.py.template` surround exactly
four functions/loops — these are what a new project actually edits when
adapting this adapter to a different Gutenberg-style EPUB, and each one
consults its matching `adapter_config.gutenberg_epub` sub-block before
falling back to the default heuristic documented above:

1. **`classify_spine_item`** — the spine content-signal heuristic. Consults
   `adapter_config.gutenberg_epub.spine_overrides`
   first, per-file, before running the heuristic. Adapt this if a new
   book's markup uses different footnote-anchor/definition id conventions
   than `FNanchor_N`/`Footnote_N`, or classifies its front-matter files
   differently.
2. **`classify_frontback_block`** — the omit/regenerate/translate decision
   tree. Consults `adapter_config.gutenberg_epub.frontback_overrides` first,
   per-element, before running the heuristic. Adapt this if a new book's
   front/back matter uses different boilerplate classes, a different TOC
   heading string, or additional structural-divider conventions this
   heuristic doesn't already recognize.
3. **The footnote-grouping loop** — the cross-file streaming group-builder.
   Adapt this if a new book's footnote markup does not use a `div.footnote`
   wrapper with a `Footnote_N`-id anchor child, or groups continuations
   differently.
4. **The verse-detection loop** — the `.poetry-container`/`.stanza`/`.line`
   selector set and the standalone/embedded decision. Adapt this if a new
   book's verse uses different class names, or if "standalone" should be
   decided by a different structural test than "direct child of `<body>` in
   a body-context file."

Every other part of `extract.py.template`'s generic core (block-ID
assignment, hashing, `order_index` re-ranking, the structural self-checks,
`no_segment_exceeds_max_words`, `generation_hashes` stamping,
`manifest.schema.json` validation) is untouched by adapting this file to a
new project — only these four adapt points, and only after consulting the
matching `adapter_config.gutenberg_epub` overrides, should change.

### The self-check region is off-limits — and the pre-green handoff diff

The full round-trip structural self-check suite ships wrapped in a
sentinel-delimited region inside `extract.py.template`:

```python
# BEGIN SELF-CHECK REGION -- DO NOT EDIT (editing a check to reach green is a false-green anti-pattern; take genuine gaps to a plugin issue)
def run_self_checks(...):
    ...
# END SELF-CHECK REGION
```

Per-project adaptation touches **only** the four `# ADAPT-POINT:` regions
above. Everything between `# BEGIN SELF-CHECK REGION` and
`# END SELF-CHECK REGION` — every invariant in the suite — is off-limits: it
is the very machinery that tells you your four edits didn't corrupt the
extraction. **Editing a self-check to make it pass is a false-green
anti-pattern.** It manufactures a green result while the defect the check
existed to catch ships silently — exactly the failure this whole methodology
is built to prevent. If a check fires on a legitimately-different book and you
believe the *check* is at fault (too Historiettes-specific, a false positive
on valid markup, or a genuine structural gap it doesn't cover), that is a
**plugin issue to file, not a line to edit locally**. Adapt the four adapt
points until the shipped checks pass honestly; never move the goalposts to
reach green.

The managed post-extraction gate `validate_extraction.py` (see
[`../false-green-gate.md`](../false-green-gate.md)) backstops this: it runs
from the plugin path, independently re-derives the manifest-derivable
invariants from `manifest.json`, and pins this region by hash — so a
locally-weakened self-check is caught rather than trusted. But the discipline
is yours to keep first; the gate is the safety net, not the license.

**Recommended pre-green handoff diff.** Before treating an adapted
`extract.py` as ready, diff it against the shipped template and read every
change that lands **outside** the four `# ADAPT-POINT:` regions — diff the
adapted `${durable_root}/extract.py` against
`assets/templates/extract.py.template`. A faithful adaptation shows hunks
*only* inside the four adapt points. **Any** hunk inside the
`# BEGIN SELF-CHECK REGION`/`# END SELF-CHECK REGION` span — or anywhere else
in the generic core — is a red flag to explain or revert before proceeding,
and the single most important thing to eyeball in that diff.

## `adapter_config.gutenberg_epub` fields (verbatim, `profile.example.yml`)

```yaml
    gutenberg_epub:
      spine_overrides: {}       # optional: {"<file>": "body|footnote-defs|front-back"} per-file classification override
      frontback_overrides: {}  # optional: {"<FRONTBACK id>": "translate|regenerate|omit"} per-element decision override
```

Both default to an empty mapping (no overrides). Only this sub-block is
meaningful while `source.format: gutenberg_epub` is the active format; the
`plain_text`/`custom` sub-blocks sit inertly alongside it and are validated
for shape only, never their specialized rules, until one of those formats is
actually selected. `source.gutenberg_id` (top-level, optional, this format
only) records the real Project Gutenberg ebook id when known — informational
only, not consumed by any check.

## See also

- [`README.md`](./README.md) — the shared `manifest.json` output contract
  every adapter (including this one) must satisfy, and the "two different
  senses of proven" distinction this file's own status line depends on.
- [`plain-text.md`](./plain-text.md) — the lighter adapter specified for
  non-EPUB sources (not yet implemented, #62); contrast its
  `verse_detection`/`footnotes` enum-based detection with this adapter's
  unconditional, markup-driven detection.
- [`custom.md`](./custom.md) — the escape hatch for a source whose
  structure this adapter's heuristics genuinely can't classify.
- [`../false-green-gate.md`](../false-green-gate.md) — the sentinel-lite
  `body_ref_markers[]` survival check consumed under `apparatus_policy:
  body_refs_only`.
- [`../ledger-and-resumability.md`](../ledger-and-resumability.md) — the
  two-phase manifest write and `generation_hashes` mechanics this adapter's
  extraction completes into.
