# W2 gate disclosures — what these gates print, and what to do about it

**Read this when, and only when:** a W2 gate has printed something and you need
the evidence behind the rule, or you are about to write a consumer of one of the
fields these gates emit.

Per section: read "Footnote emphasis (#725)" before writing any consumer of
`footnotes[].source_text`, before proposing markdown `*...*` for this again, or
before touching `segpack.py`'s tag classifiers. Read "Wrapper conservation
(#196)" only when `profile.yml` declares `source.conservation`.

`SKILL.md` keeps the rules these sections justify — the two `source_text`
consumer rules, and the wrapper gate's exit-code and pipeline-advance contract.
What lives here is the evidence: which design was tried and reverted, and why.

Every book runs W2 **once**; every resumed session — W3 onward, a W5 driver batch,
a glossary pass, W6-W9 — does not run W2 at all. That asymmetry is why this
material is here and not in `SKILL.md`.

## Footnote emphasis (#725)

## Footnote emphasis (#725)

- **`source_text` is an UNDECIDABLE UNION of two encodings — do not try to
  fold it back.** A definition whose emphasis was carried is an HTML fragment:
  its entities stay escaped, exactly as `source_html` spells them, so a literal
  `<i>` *inside a carried definition* stays `&lt;i&gt;` and is never confused
  with a real tag. A definition with no emphasis, or one that could not be
  carried, is `plain_text` **verbatim** — and a `plain_text` may itself contain
  a literal `<i>` or a bare `&`. Nothing in the string says which of the two you
  are holding, so a consumer that strips `</?i>` and unescapes gets the carried
  case right and **corrupts the fallback case**, inventing text that was never
  there. Both such folds were written for this change and both were reverted
  for exactly that reason.
  **A consumer that needs the definition's exact, unambiguous text reads
  `manifest.json`'s own `blocks{}` `plain_text`**, and one deciding whether the
  source marks emphasis reads that block's `source_html` — authoritative, one
  encoding each, and where the fix turn is already directed.
  Two report-only checks accept a small, recorded loss rather than fold:
  `final_audit.py`'s term-consistency check (WARN 6) counts no source occurrence of a
  pinned term the source italicises across its own middle
  (`Le pr<i>ésident</i>`), and `verbatim_census.py` splits a source-script run
  at an intra-word span, so it can queue a correct translation for reading.
  Both are pinned as characterizations in
  `tests/segpack_footnote_emphasis.test.py`.
- **It never mangles the text, and never invents emphasis.** Three checks
  decide, and any failure returns `plain_text` unchanged: removing the emphasis
  tags and unescaping must reproduce `plain_text` exactly; every opener must be
  closed by a tag of its **own name** (a numerically balanced
  `<i>a</em>b<em>c</i>` is not balanced at all, and collapsing the names would
  leave `b` roman); and if no `<i>` survives, the definition is returned
  verbatim rather than re-encoded — which is what stops a footnote the source
  never italicised from changing its bytes, and its `note_map_hash`, merely
  because some other tag was dropped out of it. Tag names are matched with
  HTML's own syntax rules, never Python's character rules — never `\s` or `\b`
  for the terminator (`-` and `:` are non-word characters and U+00A0 is `\s`,
  so `<i-foo>` and `<i` + NBSP + `>` would both read as italic), and never a
  bare `re.IGNORECASE` for the name (Python folds `i` with U+0130 and U+0131,
  so `<İ>` and `<ı>` would too — HTML case-folds element names per ASCII, so
  the classifiers carry `re.ASCII`). So emphasis can be *lost* (markup the
  two regexes do not model, a definition whose text spans several block tags, a
  hand-written extractor emitting unbalanced HTML) but never invented or
  reordered.

Markdown `*...*` was the first design and it is **not** what shipped: a
delimiter has flanking rules a tag does not. `<i>a</i><em>b</em>` conserves
every character and still emits `*a**b*`; a source backslash before a span
escapes the delimiter; and CommonMark's punctuation-aware flanking makes
`<i>mot,</i>x` → `*mot,*x` render as literal asterisks, as it does for the
equivalent CJK and Hebrew shapes.

## Wrapper conservation (#196)

This is **opt-in**: it is a no-op (prints a NOTE, exits `0`) unless
`profile.yml` declares `source.conservation` (`baseline_path` +
`provenance_path`, optionally `allowed_omissions_path`) — only relevant when
this project's source was hand-wrapped into its current format from some
other pre-wrap form (e.g. hand-split `pdftotext -layout` output turned into
an EPUB) and the exact pre-wrap text was preserved as an immutable baseline.
When declared, it is HARD: it compares the preserved baseline against
`manifest.json` via the wrap-time provenance map, at word-multiset
granularity (never byte-exact — legitimate reflow, e.g. the same
layout-whitespace collapse `source_html` → `plain_text` already performs,
must never false-RED), catching a hand-wrap that silently dropped baseline
content (#196), a block that reached the wrap but was truncated/hollowed
when written (the #202 case `validate_assembled.py` declines at assembly
time), and a block that was physically shuffled relative to its neighbors
even though its own content survived intact (`reading_order_reversal`,
checked against manifest `order_index`). Exit `1` HARD on any defect — the pipeline
advances to W3 ONLY on exit `0`. See `validate_conservation.py`'s own module
docstring for the full check spec and the three-artifact contract.
