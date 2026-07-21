#!/usr/bin/env python3
"""validate_conservation.py -- content-conservation gate (#196/#202).

Two silent holes this closes, and why they need three artifacts that do not
exist by default:

  1. **Wrapper conservation (#196).** When a source is hand-wrapped into
     EPUB from some other pre-wrap form (e.g. `pdftotext -layout` output,
     manually split into chapters), nothing today checks the wrap itself --
     `validate_extraction.py` (W2) re-derives manifest-internal invariants
     and pins `extract.py`'s self-check region, but has no notion of a
     pre-wrap source at all. A hand-wrap can silently drop a paragraph, drop
     a whole page, or truncate a block ("hollow" it) while producing a
     manifest that is perfectly well-formed by every existing check.
  2. **Output coverage (#202, the half `validate_assembled.py` declined).**
     That gate already catches a declared heading that produced NO output at
     all (`missing_heading`). It deliberately does NOT catch a body block
     that produced *some* output that was itself hollowed/truncated during
     translation -- its own docstring rejects a length-BAND check as
     unsound across language pairs. This script's `output-coverage` mode
     ships the FLOOR half of that gap instead of a band (see below).

## Why a naive "block count + volume" comparison is undefined for this book

An early design compared per-block counts/volumes between the pre-wrap text
and the EPUB directly. That is undefined here: the raw pre-wrap text has
neither block ids nor the hand-made chapter boundaries the wrap introduced,
so there is nothing named to compare against; and exact substring
containment fails on legitimate reflow -- the manifest's own `source_html`
already collapses layout whitespace into `plain_text`, and a hand-wrap
routinely re-flows line breaks the same way. The invariant this script
actually enforces is (a) built from three artifacts an operator supplies
when they opt in, and (b) content-conservation at WORD-MULTISET
granularity, never byte-exact and never count-based.

## The three artifacts `wrapper-conservation` mode needs (all opt-in)

Declared under `source.conservation` in profile.yml (see
`profile.schema.json`), all paths resolved durable-root-relative (no
absolute paths, no `..` segment -- same path-safety discipline as
`validate_draft.validate_seg`):

  - `baseline_path` -- the EXACT pre-wrap text the wrap was made from,
    preserved verbatim, read as UTF-8. This is the "sacred original":
    nothing in this script or the wrap process may ever rewrite it.
  - `provenance_path` -- a JSON map built AT WRAP TIME, `{"spans": [
    {"block_id", "baseline_start", "baseline_end"}, ...]}` -- half-open
    character offsets into `baseline_path`, one or more spans per
    `block_id`. This is the one artifact that makes the check well-defined
    at all: without it there is no correspondence between a raw baseline
    span and an EPUB element to compare it against.
  - `allowed_omissions_path` (optional) -- `{"line_patterns": [...],
    "ranges": [{"start","end"}, ...]}`: regexes matched per LINE (running
    heads, page-number-only lines) and half-open CHARACTER-offset ranges
    (front matter never meant to be wrapped at all) that the coverage-gap
    check tolerates as legitimately un-provenanced. Absent -> both empty,
    the strictest default (everything in the baseline must be
    provenance-covered).

  **Every offset in this script -- both `provenance_path`'s spans and
  `allowed_omissions_path`'s ranges -- is a Unicode CODE POINT (character)
  offset into `baseline_path`, read as a Python `str`, NEVER a UTF-8 byte
  offset.** They share one coordinate system on purpose: both feed the same
  `_subtract_ranges()` arithmetic in `check_coverage_gaps()`, so a byte-based
  range mixed with character-based spans would silently corrupt gap
  boundaries for any non-ASCII baseline, not just the omission itself. A
  tool that reports BYTE offsets (`wc -c`, `grep -b`, a raw UTF-8 byte
  count) will silently miscompute an offset for any baseline containing a
  multi-byte character (Hebrew, Cyrillic, accented Latin, ...) -- always
  compute offsets by counting Python `str` characters
  (`len(text[:n])`/`str.index`), never encoded bytes.

No dedicated `*.schema.json` for the provenance/omissions artifacts --
hand-rolled shape validation instead, mirroring `assemble.py`'s own
`nodestream.json` precedent (a small, plugin-internal JSON artifact this
gate is the sole consumer of; adding a schema there would only inflate the
all-schemas resume-digest glob for no independent benefit).

When `source.conservation` is absent (the common case -- most projects are
NOT a hand-wrap and have no baseline to preserve), `wrapper-conservation`
mode is a documented SKIP (prints a NOTE, exits 0) -- the same "opt-out via
absent config, never a forced-on hard gate" shape as
`validate_extraction.py`'s custom-format region-pin skip.

## Five checks, `wrapper-conservation` mode, all HARD (exit 1)

  - `dangling_provenance_block_ref` -- a span cites a `block_id` absent from
    `manifest.blocks{}`.
  - `overlapping_provenance_spans` -- two spans' baseline ranges overlap --
    the "duplicated span" failure: the same baseline content attributed to
    two different blocks (or twice to one), a red flag that the provenance
    map itself is wrong even before any content is compared.
  - `content_dropped_during_wrap` -- a character range of the baseline
    covered by NO provenance span, and not fully consumed by an allowed
    omission, still
    has non-whitespace content after `allowed_omissions`' line/range
    stripping -- content that never made it into the wrap at all (#196).
  - `hollowed_or_truncated_block` -- for a `block_id`, the COMBINED
    word-multiset of every baseline span mapped to it is not a SUBMULTISET
    of that block's own `manifest.blocks[block_id].plain_text` word
    multiset -- content that reached the wrap but was truncated/dropped
    when the block was written (the #202 case #196 doesn't cover). Combined
    per block (not checked per span in isolation) because a block may
    legitimately be covered by more than one non-adjacent baseline range
    (e.g. straddling a running head); checking spans independently risks a
    false PASS from crediting one span's words against a sibling span's own
    portion of the same block.
  - `reading_order_reversal` -- walking every live provenance span in
    baseline PHYSICAL-POSITION order, some span's manifest `order_index` is
    LOWER than the immediately preceding span's -- content the manifest
    places earlier resumes after content it places later already began.
    This is a full span-sequence walk, not a per-block min-anchor
    comparison: an anchor reduction cannot see another block's span
    INTERLEAVED between two spans of the same block (block A starts, block
    B's whole span lands, then A resumes) -- A's anchor is still its first
    span's position, which sorts before B's, so a naive anchor comparison
    misses it. Distinct from `hollowed_or_truncated_block`: a block can be
    internally content-complete (nothing hollowed, nothing dropped) while
    the WRAP still physically shuffled it relative to its neighbors --
    pages or paragraphs swapped or interleaved, each individually faithful.
    None of the other four checks can see this (no dangling ref, no
    overlap, no gap, and the per-block word-multiset still matches its own
    correctly-assigned span); this is the one check that looks at ORDER
    rather than content. See `check_reading_order`'s own docstring for the
    adjacent-pair sufficiency argument.

Normalization is deliberately narrow: NFC + whitespace-run collapse only
(`normalize_words`), no case-folding, no punctuation stripping -- matching
exactly the class of reflow the manifest's own `source_html` ->
`plain_text` collapse already performs, and nothing more aggressive (which
would risk collapsing a genuine drop into a false PASS).

## `output-coverage` mode -- the WARN-first #202 floor, not a band

`validate_assembled.py`'s own docstring rejects a per-block length-BAND
check outright: source/target ratios vary too wildly across language pairs
for a deterministic band to avoid permanent false-rejects. Proposing the
same shape again at WARN level would rename that problem, not solve it.
Instead:

  - v1 (shipped here) is a FLOOR, not a band, and is WARN-ONLY -- it never
    exits 1 for a finding, only for a genuine env/usage precondition (exit
    2). It flags `hollowed_output_block`: a block whose SOURCE side is
    non-trivial (`>= min_source_words`, profile-configurable, default 1 --
    "non-empty") but whose OUTPUT side is empty/near-empty
    (`<= max_output_words`, default 0 -- "empty"). Absolute thresholds, not
    a ratio -- language-pair independent by construction.
  - v2 (a real calibrated band) is deliberately NOT built here -- it needs a
    measured source/target ratio distribution from a real he->en run
    (Step 1 of the SSK vol.2 restart), which does not exist yet.

Population scope: exactly `segments[].block_ids[]`, resolved through
`manifest.blocks{}` -- the SAME iteration `validate_assembled.
collect_source_markers()` performs (imported and reused here, with an
always-true `heading_types` stand-in so EVERY cited block counts, not only
declared headings -- the #202 floor is a body-block concern, not a heading
one). This automatically excludes every non-`translate`-decision frontback
block: `assemble.py`'s own frontback loop (~1179-1230) never emits a
`segments[]` entry for `decision: omit` (dropped before assembly) or
`decision: regenerate` (a synthesized placeholder node, never sourced from
`segments[].block_ids[]` at all) -- both are simply never IN this
population, never merely "passing" it.

Wired at W7 (default `segment_drafts_and_audit` scope, after
`final_audit.py` + `validate_assembled.py`) and W9 (`assembled_book` scope,
after `assemble.py` + `validate_assembled.py`) -- see SKILL.md. Reuses
`validate_assembled.py`'s own reviewed-SHA rebind machinery
(`collect_reviewed_draft_rebind`) in default scope, so a segment that failed
that rebind (a hand edit landing after review) is simply absent from
consideration here too -- already reported once by `validate_assembled.py`
as `stale_review_since_audit`, never re-flagged as a false "hollowed" by
this script reading untrusted bytes.

## Self-anchoring

Like `validate_assembled.py` (and unlike `validate_extraction.py`/
`profile_validate.py`), this script IS a normal bundle-copied durable-root
script: it lives at `${durable_root}/scripts/validate_conservation.py`
(Step 0a's unconditional `assets/scripts/*.py` glob copy) and imports
`validate_assembled` as a SIBLING module from that same directory. Unlike
`validate_extraction.py`, there is no independent-tamper-boundary reason to
keep it plugin-only: it never re-checks a durable extractor's own
self-checks, it only compares durable artifacts the OPERATOR supplies
(`baseline_path`/`provenance_path`, same trust class as `style_bible.md` or
`PLAN.md`) against the durable manifest -- so it gets exactly the same
self-anchoring convention `validate_assembled.py` already uses: no
`--durable-root`/`--manifest` argument, `Path(__file__).resolve().parent`
locates `scripts/`, `.parent` of that is `durable_root`.

Usage:
    python3 validate_conservation.py wrapper-conservation
    python3 validate_conservation.py output-coverage

Exit codes (each subcommand independently): 0 = clean (or, for
`output-coverage`, ran with WARN entries only); 1 = a HARD defect
(`wrapper-conservation` only -- `output-coverage` never exits 1); 2 = a
usage/env precondition (unreadable/malformed manifest, ledger, nodestream,
baseline, provenance, or allowed-omissions artifact; a bad CLI invocation;
a malformed `profile.yml` conservation config).
"""
import argparse
import json
import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path
from typing import NoReturn

# ---------------------------------------------------------------------------
# Self-anchoring, and the sole sibling import -- reuses validate_assembled.py's
# own self-anchored constants, _MalformedArtifact, load_json, the manifest
# shape validator, collect_source_markers, and the reviewed-SHA rebind, rather
# than re-implementing any of them (this plugin's established "reuse a
# sibling's own machinery via direct import" convention, see
# validate_assembled.py's own `import validate_draft as vd`).
# ---------------------------------------------------------------------------
SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
try:
    import validate_assembled as va
except ImportError as exc:  # pragma: no cover -- defensive, should be unreachable
    print(
        f"ERROR: validate_conservation.py could not import validate_assembled.py "
        f"from {SCRIPTS_DIR}: {exc}",
        file=sys.stderr,
    )
    sys.exit(2)

DURABLE_ROOT = va.DURABLE_ROOT

DEFAULT_HOLLOW_MIN_SOURCE_WORDS = 1
DEFAULT_HOLLOW_MAX_OUTPUT_WORDS = 0


class ConservationError(Exception):
    """Every env/usage precondition this script can hit -- caught ONCE in
    main() and converted to the exit-2 contract, mirroring
    validate_assembled.py's own _MalformedArtifact / _fatal split (this
    script never needs a SEPARATE "corrupt artifact vs shape defect"
    distinction the way that script does, since neither mode here has an
    analogue to its reviewed-SHA TOCTOU hazard)."""


def _fatal(msg) -> NoReturn:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(2)


class _AlwaysContains:
    """Stand-in for `heading_types` when calling
    `va.collect_source_markers()` -- makes EVERY cited block count, not only
    declared headings, without forking that function's own dangling-ref
    fatal check and iteration shape (see module docstring's population-scope
    section)."""

    def __contains__(self, item):
        return True


_ALL_TYPES = _AlwaysContains()


# ---------------------------------------------------------------------------
# Durable-root-relative path resolution -- SECURITY (path-traversal): these
# values come straight from profile.yml, an operator-edited file, but the
# same "never trust a config-supplied path to escape durable_root" discipline
# validate_draft.validate_seg already applies to segment ids applies here too.
# ---------------------------------------------------------------------------


def _resolve_durable_relative(value, field_name):
    if not isinstance(value, str) or not value:
        raise ConservationError(
            f"profile.yml source.conservation.{field_name} must be a non-empty "
            f"string, got {value!r}"
        )
    p = Path(value)
    if p.is_absolute() or ".." in p.parts:
        raise ConservationError(
            f"profile.yml source.conservation.{field_name}={value!r} must be a "
            f"durable-root-relative path with no '..' segment and not absolute"
        )
    return DURABLE_ROOT / p


# ---------------------------------------------------------------------------
# Normalization -- shared by the coverage-gap check and the per-block
# conservation check. See module docstring: NFC + whitespace-run collapse
# ONLY, nothing more aggressive.
# ---------------------------------------------------------------------------


def normalize_words(text):
    """NFC-normalize then split on whitespace runs -- a list of content
    words, tolerant of the layout-whitespace reflow a hand-wrap legitimately
    introduces (the same class of transform manifest.json's own
    source_html -> plain_text collapse already performs) without touching
    word content itself."""
    if not isinstance(text, str):
        return []
    return unicodedata.normalize("NFC", text).split()


def _strip_omission_lines(text, line_patterns):
    """Drops every line of `text` (split on '\\n') that re.search-matches
    ANY compiled pattern in `line_patterns` -- front-matter running heads /
    page-number-only lines. Returns the remaining lines rejoined with '\\n'."""
    if not line_patterns:
        return text
    kept = [ln for ln in text.split("\n") if not any(p.search(ln) for p in line_patterns)]
    return "\n".join(kept)


# ---------------------------------------------------------------------------
# allowed_omissions artifact
# ---------------------------------------------------------------------------


def load_allowed_omissions(path):
    """Returns (line_patterns: list[re.Pattern], ranges: list[(start, end)]).
    `ranges` are CHARACTER (code point) offsets, the same coordinate system
    `load_provenance()`'s spans use -- see the module docstring's "Every
    offset in this script..." note. `path is None` (the field was absent
    from profile.yml) -> both empty -- the strictest, safest default:
    nothing is tolerated as an omission, so every character of the baseline
    must be provenance-covered."""
    if path is None:
        return [], []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConservationError(f"could not read allowed-omissions file {path}: {exc}")
    try:
        doc = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ConservationError(f"allowed-omissions file {path} is not valid JSON: {exc}")
    if not isinstance(doc, dict):
        raise ConservationError(f"allowed-omissions file {path} must be a JSON object")

    raw_patterns = doc.get("line_patterns", [])
    if not isinstance(raw_patterns, list) or not all(isinstance(x, str) for x in raw_patterns):
        raise ConservationError(
            f"allowed-omissions file {path}: 'line_patterns' must be an array of strings"
        )
    try:
        patterns = [re.compile(p) for p in raw_patterns]
    except re.error as exc:
        raise ConservationError(
            f"allowed-omissions file {path}: invalid regex in 'line_patterns': {exc}"
        )

    raw_ranges = doc.get("ranges", [])
    if not isinstance(raw_ranges, list):
        raise ConservationError(f"allowed-omissions file {path}: 'ranges' must be an array")
    ranges = []
    for i, r in enumerate(raw_ranges):
        if not isinstance(r, dict):
            raise ConservationError(f"allowed-omissions file {path}: ranges[{i}] must be an object")
        start, end = r.get("start"), r.get("end")
        if (
            not isinstance(start, int) or isinstance(start, bool)
            or not isinstance(end, int) or isinstance(end, bool)
            or start < 0 or end <= start
        ):
            raise ConservationError(
                f"allowed-omissions file {path}: ranges[{i}] must have integer "
                f"'start' < 'end', both >= 0, got start={start!r} end={end!r}"
            )
        ranges.append((start, end))
    return patterns, ranges


def _subtract_ranges(span, omit_ranges):
    """span=(start, end), CHARACTER offsets (see module docstring); returns
    the list of sub-spans of `span` remaining after removing every overlap
    with `omit_ranges` -- shrinks a provenance gap by the declared
    front-matter/omitted character ranges before the emptiness check."""
    start, end = span
    pieces = [(start, end)]
    for (os_, oe) in omit_ranges:
        next_pieces = []
        for (ps, pe) in pieces:
            if oe <= ps or os_ >= pe:
                next_pieces.append((ps, pe))
                continue
            if ps < os_:
                next_pieces.append((ps, os_))
            if oe < pe:
                next_pieces.append((oe, pe))
        pieces = next_pieces
    return pieces


# ---------------------------------------------------------------------------
# provenance map artifact
# ---------------------------------------------------------------------------


def load_provenance(path, baseline_len):
    """Returns a list of (block_id, start, end) triples, sorted by
    (start, end). `start`/`end` are CHARACTER (code point) offsets into
    `baseline_path`, NEVER UTF-8 bytes -- see the module docstring's "Every
    offset in this script..." note; `baseline_len` is `len()` of the
    already-decoded baseline `str` for the same reason. Hand-rolled shape
    validation -- see module docstring on why there is no dedicated
    schema.json for this artifact."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConservationError(f"could not read provenance map {path}: {exc}")
    try:
        doc = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ConservationError(f"provenance map {path} is not valid JSON: {exc}")
    if not isinstance(doc, dict):
        raise ConservationError(f"provenance map {path} must be a JSON object")

    raw_spans = doc.get("spans")
    if not isinstance(raw_spans, list) or not raw_spans:
        raise ConservationError(f"provenance map {path}: 'spans' must be a non-empty array")

    spans = []
    for i, s in enumerate(raw_spans):
        if not isinstance(s, dict):
            raise ConservationError(f"provenance map {path}: spans[{i}] must be an object")
        block_id = s.get("block_id")
        start, end = s.get("baseline_start"), s.get("baseline_end")
        if not isinstance(block_id, str) or not block_id:
            raise ConservationError(
                f"provenance map {path}: spans[{i}].block_id must be a non-empty string"
            )
        if (
            not isinstance(start, int) or isinstance(start, bool)
            or not isinstance(end, int) or isinstance(end, bool)
            or start < 0 or end <= start
        ):
            raise ConservationError(
                f"provenance map {path}: spans[{i}] must have integer "
                f"'baseline_start' < 'baseline_end', both >= 0, got "
                f"start={start!r} end={end!r}"
            )
        if end > baseline_len:
            raise ConservationError(
                f"provenance map {path}: spans[{i}] baseline_end={end} exceeds "
                f"the baseline file's own length ({baseline_len} characters)"
            )
        spans.append((block_id, start, end))
    spans.sort(key=lambda t: (t[1], t[2]))
    return spans


# ---------------------------------------------------------------------------
# wrapper-conservation: the five checks
# ---------------------------------------------------------------------------


def check_dangling_block_refs(spans, manifest_blocks):
    dangling = sorted({bid for bid, _s, _e in spans if bid not in manifest_blocks})
    return [
        {
            "kind": "dangling_provenance_block_ref",
            "block_ids": [bid],
            "detail": f"spans cite block_id {bid!r}, absent from manifest.blocks{{}}",
        }
        for bid in dangling
    ]


def check_overlaps(spans):
    """Pairwise-ADJACENT overlap check over spans already sorted by start --
    catches a baseline character range double-mapped to two block_ids (or
    twice to one), i.e. the 'duplicated span' failure mode. Sufficient because any
    overlap among N sorted-by-start spans manifests as an adjacent-pair
    overlap for at least one pair."""
    defects = []
    for i in range(1, len(spans)):
        prev_id, _prev_s, prev_e = spans[i - 1]
        cur_id, cur_s, cur_e = spans[i]
        if cur_s < prev_e:
            defects.append(
                {
                    "kind": "overlapping_provenance_spans",
                    "block_ids": sorted({prev_id, cur_id}),
                    "detail": (
                        f"span for {prev_id!r} ending at {prev_e} overlaps span for "
                        f"{cur_id!r} starting at {cur_s}"
                    ),
                }
            )
    return defects


def check_coverage_gaps(spans, baseline_text, omit_ranges, omit_line_patterns):
    """Every character of `baseline_text` not covered by any provenance span
    must, after subtracting the declared omission ranges and stripping
    omission-matched lines, contain no remaining non-whitespace -- otherwise
    real baseline content was silently dropped during the hand-wrap (#196)."""
    defects = []
    if spans:
        boundaries = [(0, spans[0][1])]
        for i in range(1, len(spans)):
            boundaries.append((spans[i - 1][2], spans[i][1]))
        boundaries.append((spans[-1][2], len(baseline_text)))
    else:
        boundaries = [(0, len(baseline_text))]

    for (gap_start, gap_end) in boundaries:
        if gap_end <= gap_start:
            continue
        for (sub_s, sub_e) in _subtract_ranges((gap_start, gap_end), omit_ranges):
            if sub_e <= sub_s:
                continue
            chunk = baseline_text[sub_s:sub_e]
            remainder = _strip_omission_lines(chunk, omit_line_patterns)
            if remainder.strip():
                defects.append(
                    {
                        "kind": "content_dropped_during_wrap",
                        "block_ids": [],
                        "detail": (
                            f"baseline[{sub_s}:{sub_e}) has no provenance span and is "
                            f"not an allowed omission: {remainder.strip()[:120]!r}"
                        ),
                    }
                )
    return defects


def check_hollowed_or_truncated(live_spans, baseline_text, manifest_blocks, omit_line_patterns):
    """Groups spans by block_id -- a block may legitimately be covered by
    more than one non-adjacent baseline range (e.g. straddling a running
    head) -- then checks the COMBINED per-block baseline word-multiset is a
    SUBMULTISET of that block's own manifest plain_text word-multiset.
    `live_spans` excludes dangling refs (already reported by
    check_dangling_block_refs; indexing manifest_blocks for one would
    KeyError)."""
    by_block = {}
    for block_id, start, end in live_spans:
        chunk = baseline_text[start:end]
        chunk = _strip_omission_lines(chunk, omit_line_patterns)
        by_block.setdefault(block_id, []).extend(normalize_words(chunk))

    defects = []
    for block_id in sorted(by_block):
        source_counter = Counter(by_block[block_id])
        block_text = manifest_blocks[block_id].get("plain_text")
        block_counter = Counter(normalize_words(block_text))
        missing = source_counter - block_counter
        if missing:
            sample = ", ".join(f"{w!r}x{c}" for w, c in list(missing.items())[:8])
            defects.append(
                {
                    "kind": "hollowed_or_truncated_block",
                    "block_ids": [block_id],
                    "detail": (
                        f"{sum(missing.values())} word-occurrence(s) present in the "
                        f"baseline span(s) mapped to block {block_id!r} are absent "
                        f"from its manifest plain_text: {sample}"
                    ),
                }
            )
    return defects


def check_reading_order(live_spans, manifest_blocks):
    """A block can survive `check_hollowed_or_truncated` with every one of
    its own words intact and STILL have been physically shuffled by the
    hand-wrap -- pages or paragraphs swapped, each one internally faithful.
    None of the other three checks can see that: no dangling ref, no
    overlap, no coverage gap, and the per-block word-multiset still matches
    its OWN correctly-assigned span. This check is the one that looks at
    ORDER, not content.

    A per-block MIN-anchor reduction (take each block's earliest span start,
    compare those anchors across blocks) is NOT sufficient: it collapses a
    multi-span block down to one position and so cannot see another block's
    span physically INTERLEAVED between two spans of the same block (block A
    starts, block B's whole span lands, then block A resumes) -- A's anchor
    is still its first span's position, which sorts before B's, so the
    anchor comparison reports no defect even though the wrap shuffled
    content between them. Instead this check walks the FULL span sequence:

    1. Build `(start, end, order_index, block_id)` for every live
       (non-dangling) span whose block's own `order_index` is a real
       (non-bool) int -- manifest.schema.json already requires that on every
       block; a manifest that violates it is a schema-shape defect for
       `validate_extraction.py` to catch, not this check's concern.
    2. Sort that list by `(start, end)` -- i.e. baseline physical position.
    3. Walk ADJACENT pairs of the sorted span list and flag any pair whose
       `order_index` DECREASES: the span immediately following (by baseline
       position) a span of order_index N belongs to a block whose manifest
       order_index is less than N, meaning content the manifest places
       EARLIER physically resumes AFTER content it places LATER already
       began -- a reading-order reversal or interleave.

    Only adjacent pairs of the sorted SPAN sequence are checked (the same
    sufficiency argument `check_overlaps` already uses): a sequence of
    values is non-decreasing iff every adjacent pair in it is non-decreasing,
    so a full pairwise scan across the whole span list is unnecessary. This
    is strictly stronger than a per-block anchor comparison: two spans of
    the SAME block always share one order_index, so `cur < prev` is never
    true between them -- legitimate multi-span blocks (e.g. straddling a
    running head) never false-flag -- while an inversion or an interleave
    between two DIFFERENT blocks that carry DISTINCT `order_index` values
    always surfaces as a decrease somewhere in the walk, because it is then a
    decrease in the underlying order_index sequence and any such decrease
    appears between at least one adjacent pair once sorted. (Two DIFFERENT
    blocks sharing ONE `order_index` could interleave without producing a
    decrease, but that collision is a FATAL `duplicate_order_index` manifest
    defect `assemble.py` raises on before assembly -- order_index is the
    single global reading-order axis and must be unique per block; gaps in
    the sequence are fine, only collisions are fatal -- so it never reaches a
    real book.)"""
    ordered = []
    for block_id, start, end in live_spans:
        oi = manifest_blocks[block_id].get("order_index")
        if not isinstance(oi, int) or isinstance(oi, bool):
            continue
        ordered.append((start, end, oi, block_id))
    ordered.sort(key=lambda t: (t[0], t[1]))

    defects = []
    for i in range(1, len(ordered)):
        prev_start, _prev_end, prev_oi, prev_id = ordered[i - 1]
        cur_start, _cur_end, cur_oi, cur_id = ordered[i]
        if cur_oi < prev_oi:
            defects.append(
                {
                    "kind": "reading_order_reversal",
                    "block_ids": [prev_id, cur_id],
                    "detail": (
                        f"walking the baseline in position order, a span of "
                        f"{cur_id!r} (manifest order_index {cur_oi}) at baseline "
                        f"position {cur_start} appears immediately after a span of "
                        f"{prev_id!r} (manifest order_index {prev_oi}) at baseline "
                        f"position {prev_start}, but manifest order places "
                        f"{cur_id!r} BEFORE {prev_id!r} -- the wrap physically "
                        f"shuffled or interleaved their content"
                    ),
                }
            )
    return defects


def run_wrapper_conservation(manifest):
    """Returns True (clean) / False (HARD defects) -- raises
    ConservationError on any env/usage precondition."""
    profile = va.vd.load_profile()
    source = profile.get("source") if isinstance(profile, dict) else None
    conservation_cfg = source.get("conservation") if isinstance(source, dict) else None

    if not conservation_cfg:
        print(
            "NOTE wrapper_conservation: SKIPPED -- profile.yml has no "
            "source.conservation block declared. This check is opt-in: it "
            "only applies to a source hand-wrapped from some other pre-wrap "
            "form with a preserved baseline + provenance map. See this "
            "script's own module docstring for the three artifacts needed "
            "to enable it.",
            file=sys.stderr,
        )
        print(json.dumps({"defects": [], "skipped": True}, ensure_ascii=False))
        return True

    if not isinstance(conservation_cfg, dict):
        raise ConservationError("profile.yml source.conservation must be an object when present")

    baseline_path = _resolve_durable_relative(conservation_cfg.get("baseline_path"), "baseline_path")
    provenance_path = _resolve_durable_relative(conservation_cfg.get("provenance_path"), "provenance_path")
    omissions_value = conservation_cfg.get("allowed_omissions_path")
    omissions_path = (
        _resolve_durable_relative(omissions_value, "allowed_omissions_path")
        if omissions_value is not None
        else None
    )

    try:
        baseline_text = baseline_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConservationError(f"could not read conservation baseline {baseline_path}: {exc}")

    line_patterns, omit_ranges = load_allowed_omissions(omissions_path)

    manifest_blocks = manifest.get("blocks")
    if not isinstance(manifest_blocks, dict):
        raise ConservationError("manifest.json 'blocks' must be an object")

    spans = load_provenance(provenance_path, len(baseline_text))
    live_spans = [(bid, s, e) for (bid, s, e) in spans if bid in manifest_blocks]

    defects = []
    defects.extend(check_dangling_block_refs(spans, manifest_blocks))
    defects.extend(check_overlaps(spans))
    defects.extend(check_coverage_gaps(spans, baseline_text, omit_ranges, line_patterns))
    defects.extend(check_hollowed_or_truncated(live_spans, baseline_text, manifest_blocks, line_patterns))
    defects.extend(check_reading_order(live_spans, manifest_blocks))

    print("=" * 70, file=sys.stderr)
    print(f"WRAPPER CONSERVATION -- baseline={baseline_path}", file=sys.stderr)
    print("=" * 70, file=sys.stderr)
    print(
        f"\nHARD ({len(defects)}): {'CLEAN' if not defects else str(len(defects)) + ' DEFECTS'}",
        file=sys.stderr,
    )
    for d in defects:
        ids = ",".join(d["block_ids"]) or "-"
        print(f"  x [{ids}] {d['kind']}: {d['detail']}", file=sys.stderr)
    print(json.dumps({"defects": defects}, ensure_ascii=False))
    return not defects


# ---------------------------------------------------------------------------
# output-coverage: the v1 floor
# ---------------------------------------------------------------------------


def collect_default_output_word_counts(trusted_drafts):
    counts = {}
    for seg, draft in trusted_drafts.items():
        blocks = draft.get("blocks")
        if not isinstance(blocks, dict):
            continue
        for bid, text in blocks.items():
            counts[(seg, bid)] = len(normalize_words(text))
    return counts


def collect_nodestream_output_word_counts(nodestream):
    """Word count per (seg, block_id) node key -- takes the MAX across any
    duplicate key (fail-safe direction: prefer not to warn if ANY surviving
    occurrence of a key is non-hollow)."""
    counts = {}
    nodes = nodestream.get("nodes")
    if not isinstance(nodes, list):
        return counts
    for node in nodes:
        if not isinstance(node, dict):
            continue
        node_seg, node_id, text = node.get("seg"), node.get("id"), node.get("text")
        if not isinstance(node_seg, str) or not isinstance(node_id, str):
            continue
        key = (node_seg, node_id)
        wc = len(normalize_words(text))
        counts[key] = max(wc, counts.get(key, 0))
    return counts


def run_output_coverage():
    """Returns True always (WARN-first v1 never HARD-gates) -- raises
    ConservationError on any env/usage precondition."""
    profile = va.vd.load_profile()
    try:
        v1_scope = profile["output"]["v1_scope"]
    except (KeyError, TypeError) as exc:
        raise ConservationError(f"profile.yml is missing required field 'output.v1_scope' ({exc})")

    floor_cfg = (profile.get("validation") or {}).get("conservation_hollow_floor")
    floor_cfg = floor_cfg if floor_cfg is not None else {}
    if not isinstance(floor_cfg, dict):
        raise ConservationError(
            "profile.yml validation.conservation_hollow_floor must be an object when present"
        )
    min_source_words = floor_cfg.get("min_source_words", DEFAULT_HOLLOW_MIN_SOURCE_WORDS)
    max_output_words = floor_cfg.get("max_output_words", DEFAULT_HOLLOW_MAX_OUTPUT_WORDS)
    if not isinstance(min_source_words, int) or isinstance(min_source_words, bool) or min_source_words < 1:
        raise ConservationError(
            f"validation.conservation_hollow_floor.min_source_words must be an "
            f"integer >= 1, got {min_source_words!r}"
        )
    if not isinstance(max_output_words, int) or isinstance(max_output_words, bool) or max_output_words < 0:
        raise ConservationError(
            f"validation.conservation_hollow_floor.max_output_words must be an "
            f"integer >= 0, got {max_output_words!r}"
        )

    manifest, err = va.load_json(va.MANIFEST_PATH, "manifest.json")
    if err:
        raise ConservationError(err)
    if not isinstance(manifest, dict):
        raise ConservationError(f"manifest.json at {va.MANIFEST_PATH} did not parse to an object")
    manifest_blocks = manifest.get("blocks")
    manifest_segments = manifest.get("segments")
    if not isinstance(manifest_blocks, dict):
        raise ConservationError("manifest.json 'blocks' must be an object")
    if not isinstance(manifest_segments, list):
        raise ConservationError("manifest.json 'segments' must be an array")

    try:
        va._validate_manifest_shape(manifest, manifest_blocks, manifest_segments)
        source_marker_counter = va.collect_source_markers(manifest_segments, manifest_blocks, _ALL_TYPES)
    except va._MalformedArtifact as exc:
        raise ConservationError(str(exc))

    if v1_scope == "assembled_book":
        nodestream, err = va.load_json(va.NODESTREAM_PATH, "assembled nodestream")
        if err:
            raise ConservationError(
                f"{err} -- run assemble.py before validate_conservation.py "
                f"output-coverage in assembled_book scope"
            )
        if not isinstance(nodestream, dict):
            raise ConservationError(f"nodestream at {va.NODESTREAM_PATH} did not parse to an object")
        output_words = collect_nodestream_output_word_counts(nodestream)
        eligible_keys = set(source_marker_counter)
    elif v1_scope == "segment_drafts_and_audit":
        ledger, err = va.load_json(va.LEDGER_PATH, "runs/ledger.json")
        if err:
            raise ConservationError(
                f"{err} -- run final_audit.py before validate_conservation.py "
                f"output-coverage in default scope"
            )
        ledger_segments = ledger.get("segments") if isinstance(ledger, dict) else None
        if not isinstance(ledger_segments, dict):
            raise ConservationError("runs/ledger.json is missing its 'segments' object")
        try:
            trusted_drafts, _stale_segs = va.collect_reviewed_draft_rebind(ledger_segments)
        except va._MalformedArtifact as exc:
            raise ConservationError(str(exc))
        output_words = collect_default_output_word_counts(trusted_drafts)
        # Only a CONVERGED, reviewed-SHA-trusted segment is eligible -- a
        # segment not yet converged is "not done", not "hollowed"; a segment
        # that failed the rebind was already reported once by
        # validate_assembled.py as stale_review_since_audit and is never
        # re-evaluated here on untrusted bytes.
        eligible_keys = {(seg, bid) for (seg, bid) in source_marker_counter if seg in trusted_drafts}
    else:
        raise ConservationError(
            f"profile.yml output.v1_scope={v1_scope!r} is not one of "
            f"'segment_drafts_and_audit'/'assembled_book'"
        )

    warnings = []
    for (seg, bid) in sorted(eligible_keys):
        mb = manifest_blocks.get(bid) or {}
        source_words = len(normalize_words(mb.get("plain_text")))
        if source_words < min_source_words:
            continue
        out_words = output_words.get((seg, bid), 0)
        if out_words <= max_output_words:
            warnings.append(
                {
                    "seg": seg,
                    "block_id": bid,
                    "kind": "hollowed_output_block",
                    "source_words": source_words,
                    "output_words": out_words,
                }
            )

    print("=" * 70, file=sys.stderr)
    print(f"OUTPUT COVERAGE (v1 floor, WARN-only) -- scope={v1_scope}", file=sys.stderr)
    print("=" * 70, file=sys.stderr)
    print(f"\nWARN ({len(warnings)}):", file=sys.stderr)
    for w in warnings:
        print(
            f"  * [{w['seg']}/{w['block_id']}] source_words={w['source_words']} "
            f"output_words={w['output_words']}",
            file=sys.stderr,
        )
    print(json.dumps({"warnings": warnings}, ensure_ascii=False))
    return True


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def build_arg_parser():
    parser = argparse.ArgumentParser(
        description="Content-conservation gate (#196/#202): wrapper-conservation "
        "(HARD, opt-in, run after W2) and output-coverage (WARN-only v1 floor, "
        "run at W7/W9)."
    )
    sub = parser.add_subparsers(dest="mode", required=True)
    sub.add_parser(
        "wrapper-conservation",
        help="HARD gate: compare the wrapped EPUB manifest against the preserved "
        "pre-wrap baseline via its provenance map. SKIPPED (exit 0) when "
        "profile.yml has no source.conservation block.",
    )
    sub.add_parser(
        "output-coverage",
        help="WARN-only v1 floor: flag a hollowed (empty/near-empty) translated "
        "block against a non-empty source. Never exits 1 for a finding.",
    )
    return parser


def main(argv=None):
    args = build_arg_parser().parse_args(argv)
    try:
        if args.mode == "wrapper-conservation":
            manifest, err = va.load_json(va.MANIFEST_PATH, "manifest.json")
            if err:
                raise ConservationError(err)
            if not isinstance(manifest, dict):
                raise ConservationError(
                    f"manifest.json at {va.MANIFEST_PATH} did not parse to an object"
                )
            ok = run_wrapper_conservation(manifest)
        else:
            ok = run_output_coverage()
    except ConservationError as exc:
        _fatal(str(exc))

    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
