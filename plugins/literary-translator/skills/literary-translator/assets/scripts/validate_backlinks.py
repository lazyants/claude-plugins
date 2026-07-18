#!/usr/bin/env python3
"""validate_backlinks.py -- the ADVISORY Mentions-appendix coverage gate for
the source-anchored occurrence index (1.8.0, see the plan's Contract + D4
sections). Runs at W9, after `diff_rendered_output.py`, as the last step of
`assemble.py -> validate_assembled.py -> diff_rendered_output.py ->
validate_backlinks.py`. Unlike the two HARD gates before it in that chain,
this gate's own exit 1 is ADVISORY -- it logs a WARN and does NOT halt W9
(SKILL.md documents this explicitly); only its exit 2 (a genuine hard
error -- unreadable/malformed input) halts.

## What this gate is, and is not

The obsidian adapter's `mentions_section` block
(`output.adapter_config.obsidian.mentions_section`) makes
`render_obsidian.py` emit a source-anchored `## Mentions` occurrence-index
section in each entity note, wrapped in reserved
`<!-- lt:mentions:begin/end -->` boundary markers (D1). ON BY DEFAULT for
`output.target: obsidian` -- an absent `mentions_section` block, an absent
`enabled` key, or `enabled: null` all resolve to enabled; an explicit
`enabled: false` is the only way to opt out. This script does NOT trust
that render produced a complete section -- it independently RE-DERIVES the
expected occurrence universe (via `occurrence_targets.build`, fresh, from
the same manifest/canon/canon_senses/language_config/persisted-NodeStream
inputs assembly used) and compares it against what actually survives in
the rendered vault, so a renderer bug can never "self-hide" by also being
wrong in the report.

## The effective-enabled predicate (codex R8 b1 / R9 b1)

`output.target == "obsidian"` AND
`output.adapter_config.obsidian.mentions_section.enabled is not False` --
the SAME condition `assemble.py` uses to attach `nodestream["mentions"]`
and `render()` uses to gate D1's rendering behavior. Evaluated FIRST,
before loading ANY metric input (manifest/canon/canon_senses/nodestream/
vault): a dormant `obsidian` sub-block that is not explicitly `enabled:
false` under a different `output.target` (e.g. `custom`) must never make
this gate parse a non-Obsidian vault -- the `target != "obsidian"`
short-circuit is unchanged and evaluated first for exactly that reason.
When not effective-enabled (only ever via `target != "obsidian"` or an
explicit `enabled: false`): `mentions_coverage.status` is `"disabled"`, no
metric is computed (collisions/unresolved_homonyms/inline_advisory are all
empty too -- nothing below this point is loaded), `warnings=0`, exit 0.

## The two metrics + two exit-neutral diagnostics (D4)

  1. **Mentions coverage** (the SOLE `warnings` source). Expected =
     `occurrence_targets.build(...)["eligible_by_source_form"]`, called
     FRESH every run (never `nodestream.get("mentions")` -- this gate does
     not even read that field; it re-derives from the same raw inputs).
     Actual = the `[[...]]` wikilink targets found strictly inside the
     SINGLE well-formed `lt:mentions` marker pair of each entity note,
     after stripping exactly one leading YAML frontmatter block and
     matching ONLY exact column-zero whole lines (no `.strip()`, no
     substring `count()`/`index()`, no decode/normalize) -- see
     `parse_mentions_region`. Zero, multiple, nested, or malformed marker
     structure is REJECTED (never "trust the first match"): every segment
     the entity was expected to be linked from then counts as missing. A
     `(source_form, seg)` expected-but-absent pair increments `warnings`
     by one. An entity with zero expected occurrences is never checked
     (not an error either way).
  2. **Native-inline advisory** (exit-neutral). Tallies `[[note_identity|
     target]]`-style inline links -- the pre-existing `_Linker` output --
     found in each SEGMENT note's own body (frontmatter stripped, any
     stray `lt:mentions` region excluded defensively), and reports entities
     whose inline link count falls short of their source-occurrence count
     (the #206 gap made visible: a variant target rendering that never
     matched the one canonical string the inline linker looks for).
     Contributes nothing to `warnings`/exit.
  - **Collisions** (exit-neutral): canon entries grouped by
    `canon_senses.normalize_form(canonical_target_form)`, >=2 owners --
    independent of the rendered vault. Each row also carries
    `renderer_delinked: bool` (#240 gate half): whether
    `render_obsidian.build_entity_index` actually removes this target from
    its link map once collision de-linking engages -- computed by CALLING
    the renderer twice (never re-implementing its rule), so it surfaces
    rather than eliminates the residual disagreement between this gate's
    casefold grouping key and the renderer's own NFC-only,
    sense_translated-aware one. Never routed into `warnings`/exit -- see
    below.
  - **`unresolved_homonyms`** (exit-neutral): `occurrence_targets.build`'s
    own split-form accounting, surfaced verbatim.

## Report shape (one JSON line to stdout)

    { "mentions_coverage": { "status": "enabled" | "disabled",
                              "checked_entities": int,
                              "missing": [ {"source_form", "seg"}, ... ] },
      "unresolved_homonyms": [ {"source_form", "count", "segs": [...]}, ... ],
      "collisions":          [ {"canonical_target_form", "owners": [...],
                                 "renderer_delinked": bool}, ... ],
      "inline_advisory":     { "thin_coverage": [
          {"source_form", "inline_links", "source_occurrences"}, ... ] },
      "warnings": int }

## Exit codes

  0 = feature not effective-enabled, OR `warnings == 0`.
  1 = `warnings > 0` (advisory -- does NOT halt W9; logged as WARN).
  2 = hard error: unreadable/malformed manifest.json, canon.json (when
      present -- an ABSENT canon.json is tolerated as zero entries, exactly
      like `assemble.py`'s own default; a PRESENT `entries` that is not an
      object is still fatal, #236), canon_senses.json, profile.yml, the
      resolved language_config, the persisted assembled NodeStream
      (`${DURABLE_ROOT}/out/.assembled/nodestream.json`, independent of
      `output.destination`/`--vault`) -- including a malformed `book`/
      `seg_order`/`nodes` shape within it (#236, promoted from an uncaught
      exit-1 traceback to a clean, reason-carrying exit 2) -- or
      `occurrence_targets.build()` itself raising.

Usage: python3 validate_backlinks.py [--vault DIR]
"""
import argparse
import json
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from typing import NoReturn

# ---------------------------------------------------------------------------
# Self-anchoring: this script always lives at {durable_root}/scripts/<name>.py.
# It never assumes cwd == durable_root, and never takes a --durable-root flag.
# ---------------------------------------------------------------------------
SCRIPTS_DIR = Path(__file__).resolve().parent
DURABLE_ROOT = SCRIPTS_DIR.parent
MANIFEST_PATH = DURABLE_ROOT / "manifest.json"
CANON_PATH = DURABLE_ROOT / "canon.json"
# Sibling of CANON_PATH -- self-anchored the same way, deliberately
# NOT imported from canon_senses.py (see that module's own docstring on why
# each consumer computes its own copy of this durable-root-relative default).
CANON_SENSES_PATH = DURABLE_ROOT / "canon_senses.json"
ASSEMBLED_DIR = DURABLE_ROOT / "out" / ".assembled"
NODESTREAM_PATH = ASSEMBLED_DIR / "nodestream.json"

# validate_draft.py (profile loading), output_resolve.py (out_dir
# resolution), bootstrap_names.py (language_config), canon_senses.py
# (canon_senses.json loading), occurrence_targets.py (the eligibility
# engine, A1) and render_obsidian.py (shared filename/marker-writing
# helpers, reused rather than reimplemented) all live next to this script --
# imported directly, matching this plugin's own established
# `import validate_draft as vd` sibling-import pattern.
sys.path.insert(0, str(SCRIPTS_DIR))


def _import_fatal(modname, exc) -> NoReturn:
    print(
        f"ERROR: validate_backlinks.py could not import {modname}.py from "
        f"{SCRIPTS_DIR}: {exc}",
        file=sys.stderr,
    )
    sys.exit(2)


def _import_dependency_halt_fatal(modname) -> NoReturn:
    # A sibling module's own module-level dependency preflight (e.g.
    # validate_draft.py's PyYAML guard, canon_senses.py's/render_obsidian.py's
    # own PyYAML/jsonschema guards) can sys.exit() DURING this very import --
    # before this script's own try/except ever runs. Re-surface it as this
    # script's own exit-2 dependency-precondition contract rather than
    # letting a bare stderr-only exit (possibly at a different exit code)
    # escape untouched -- mirrors assemble.py's/validate_assembled.py's own
    # handling of validate_draft.py's identical import-time guard.
    print(
        f"ERROR: validate_backlinks.py could not import {modname}.py from "
        f"{SCRIPTS_DIR} -- it halted during its own module-level dependency "
        f"preflight (see stderr for the specific reason)",
        file=sys.stderr,
    )
    sys.exit(2)


try:
    import validate_draft as vd
except ImportError as exc:  # pragma: no cover -- defensive, should be unreachable
    _import_fatal("validate_draft", exc)
except SystemExit:
    _import_dependency_halt_fatal("validate_draft")

try:
    import output_resolve
except ImportError as exc:  # pragma: no cover -- defensive, should be unreachable
    _import_fatal("output_resolve", exc)

try:
    import bootstrap_names
except ImportError as exc:  # pragma: no cover -- defensive, should be unreachable
    _import_fatal("bootstrap_names", exc)

try:
    import canon_senses
except ImportError as exc:  # pragma: no cover -- defensive, should be unreachable
    _import_fatal("canon_senses", exc)
except SystemExit:
    _import_dependency_halt_fatal("canon_senses")

try:
    import occurrence_targets
except ImportError as exc:  # pragma: no cover -- disjoint teammate module (A1)
    _import_fatal("occurrence_targets", exc)

try:
    import render_obsidian
except ImportError as exc:  # pragma: no cover -- defensive, should be unreachable
    _import_fatal("render_obsidian", exc)
except SystemExit:
    _import_dependency_halt_fatal("render_obsidian")


# ---------------------------------------------------------------------------
# Reserved marker lines (D1) -- must match render_obsidian.py's own
# `<!-- lt:mentions:begin/end -->` constants byte for byte. Matched as
# EXACT, column-zero, WHOLE lines only -- never a substring, never
# .strip()ed, never decode/Unicode-normalized -- so a marker smuggled into
# an unrestricted YAML frontmatter scalar (`category`/`source`) or embedded
# mid-line can never forge a region (codex R7 b1/R8).
# ---------------------------------------------------------------------------
_MENTIONS_BEGIN_LINE = "<!-- lt:mentions:begin -->"
_MENTIONS_END_LINE = "<!-- lt:mentions:end -->"

# Obsidian wikilink target: the text before an optional "|alias". Target
# text can't itself contain "[", "]", or "|".
_WIKILINK_RE = re.compile(r"\[\[([^\[\]|]+)(?:\|[^\[\]]*)?\]\]")

# #236 fence-awareness -- a ``` / ~~~ fenced code block delimiter line
# (leading/trailing whitespace tolerated, matching common Markdown
# renderers). Group 2 captures whatever follows the delimiter run (an
# OPENING fence's optional info string, e.g. "python" in "```python"; a
# CLOSING fence may carry nothing but trailing whitespace there -- see
# `_fenced_line_mask`, which is the only thing that actually enforces that
# distinction). Used to keep a marker line or a wikilink that only exists
# INSIDE an author's own illustrative code fence from ever forging gate
# signal (a fake begin/end pair, or a fake counted-as-coverage link).
_FENCE_DELIM_RE = re.compile(r"^(```+|~~~+)(.*)$")

# A run of one or more backticks -- the CommonMark inline-code-span
# delimiter unit `_strip_inline_code` scans with, below.
_BACKTICK_RUN_RE = re.compile(r"`+")


def _strip_inline_code(line):
    """Removes every CommonMark-style inline code span from `line`: a run
    of N backticks OPENS a span, and the NEXT run of EXACTLY N backticks
    (never merely >= N, never merely a single backtick regardless of N)
    CLOSES it -- content between them, however many single/shorter-run
    backticks it contains, is consumed as code and dropped whole, never
    scanned for a wikilink. This is what keeps `` ``[[001 real]]`` `` --
    a wikilink an author is merely QUOTING as literal text via a
    DOUBLE-backtick span, not emitting -- from ever counting as coverage
    (#236, bot review P1 finding 3). A run with no matching same-length
    closer anywhere later in the line is not a code span at all (CommonMark:
    an unmatched backtick run is literal text) and is left untouched.

    A pure regex can't express "the next run of exactly N backticks"
    cleanly for unbounded N (a fixed-N pattern only ever handles one N),
    so this is a small left-to-right scan instead -- mirrors
    `_fenced_line_mask`'s own "small scan, not a regex, for a rule regex
    can't express" precedent just above."""
    out = []
    i, n = 0, len(line)
    while i < n:
        m = _BACKTICK_RUN_RE.match(line, i)
        if not m:
            out.append(line[i])
            i += 1
            continue
        run_len = len(m.group(0))
        j = m.end()
        close_end = None
        while j < n:
            m2 = _BACKTICK_RUN_RE.match(line, j)
            if not m2:
                j += 1
                continue
            if len(m2.group(0)) == run_len:
                close_end = m2.end()
                break
            j = m2.end()  # a different-length run is literal CODE CONTENT, keep scanning for the real closer
        if close_end is None:
            out.append(m.group(0))  # unmatched run -- literal text, not a code-span opener
            i = m.end()
        else:
            i = close_end  # whole span (opener + content + closer) dropped -- never re-scanned for a wikilink
    return "".join(out)


def _fatal(msg) -> NoReturn:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(2)


def _load_json(path, label):
    """Returns (obj, error_message_or_None) -- mirrors validate_draft.py's
    own `_load_json` / validate_assembled.py's `load_json` exactly,
    including the R8-1 lesson that `ValueError` alone (a shared parent of
    `json.JSONDecodeError` AND `UnicodeDecodeError`) is the right catch at
    a black-box parse boundary, rather than enumerating subclasses."""
    if not path.is_file():
        return None, f"{label} missing: {path}"
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except (OSError, ValueError) as exc:
        return None, f"{label} at {path} is not valid JSON: {exc}"
    except RecursionError as exc:
        return None, f"{label} at {path} is nested too deeply to parse: {exc}"


def _require_json(path, label):
    obj, err = _load_json(path, label)
    if err:
        _fatal(err)
    return obj


def _read_text_or_none(path):
    """`path`'s UTF-8 text, or None if it isn't a readable regular file --
    the is_file()/read-or-None pattern both metric passes use to load a
    vault note without letting a missing/unreadable file become a hard
    error."""
    if not path.is_file():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


# ---------------------------------------------------------------------------
# Effective-enabled predicate (codex R8 b1 / R9 b1) -- the SAME condition
# assemble.py/render() use, so a dormant obsidian sub-block never activates
# this gate under a different output.target.
# ---------------------------------------------------------------------------

def _effective_enabled(profile):
    output_cfg = (profile or {}).get("output") or {}
    if output_cfg.get("target") != "obsidian":
        return False
    mentions_cfg = (
        (output_cfg.get("adapter_config") or {}).get("obsidian") or {}
    ).get("mentions_section") or {}
    return mentions_cfg.get("enabled") is not False


def _disabled_report():
    return {
        "mentions_coverage": {"status": "disabled", "checked_entities": 0, "missing": []},
        "unresolved_homonyms": [],
        "collisions": [],
        "inline_advisory": {"thin_coverage": []},
        "warnings": 0,
    }


# ---------------------------------------------------------------------------
# Marker-region parsing (metric 1's "actual" side).
# ---------------------------------------------------------------------------

def _strip_frontmatter(text):
    """Removes exactly one leading YAML frontmatter block (a first line
    that is exactly "---", up to the next line that is exactly "---") --
    the WHOLE block, never partially -- so any marker-shaped text an
    author smuggled into a frontmatter scalar (`category:`, `source:`,
    ...) is removed along with it, before any marker search ever runs
    (codex R8: the frontmatter-scalar-injection spoof). No frontmatter
    (first line isn't "---") -> returned unchanged. An UNTERMINATED
    frontmatter (opening "---" with no closing "---") has no body at all
    to search -- returns "" rather than guessing."""
    lines = render_obsidian._split_lf_lines(text or "")
    if not lines or lines[0] != "---":
        return text or ""
    for i in range(1, len(lines)):
        if lines[i] == "---":
            return "\n".join(lines[i + 1:])
    return ""


def _fenced_line_mask(lines):
    """Returns a list of bool, same length as `lines`: True iff that line
    sits STRICTLY INSIDE an open ``` / ~~~ fenced code block (the fence
    delimiter lines themselves are False, matching CommonMark's own
    "fence lines are not part of the code block's content" rule). A fence
    is closed by a later delimiter line using the SAME fence character
    (backtick vs tilde), a run length >= the opening one, AND NOTHING
    AFTER THE RUN but optional trailing whitespace -- close enough to
    CommonMark's real nesting rule for this defensive purpose; exact
    fenced-code-block semantics are not the point, only "don't let a
    marker/wikilink hiding inside an author's own example fence forge gate
    signal" (#236).

    The "nothing but trailing whitespace" clause matters (bot review P1
    finding 2): an INFO-BEARING delimiter line (e.g. "```python", opening a
    NESTED illustrative fence inside the outer example) must never be
    mistaken for the outer fence's closer while that outer fence is still
    open -- CommonMark reserves an info string for OPENING fences only; a
    closing fence carries no info string. Without this check, a line like
    "```python" appearing while a fence is open would wrongly close it
    (same char, run length >= open), un-masking everything after it --
    including a real marker pair the author only meant to ILLUSTRATE inside
    the still-open outer fence."""
    mask = []
    open_char = None
    open_len = 0
    for ln in lines:
        m = _FENCE_DELIM_RE.match(ln.strip())
        if m:
            token, rest = m.group(1), m.group(2)
            char, length = token[0], len(token)
            if open_char is None:
                # Opening fence -- an info string (`rest`) is allowed here,
                # CommonMark-style, and never inspected.
                open_char = char
                open_len = length
                mask.append(False)  # the opening delimiter itself
            elif char == open_char and length >= open_len and not rest.strip():
                open_char = None
                open_len = 0
                mask.append(False)  # the closing delimiter itself
            else:
                # Either a shorter/differently-fenced delimiter-shaped line,
                # or a same-char run >= open_len that carries trailing
                # content (an info string) -- neither closes the fence;
                # both are ordinary content strictly inside it.
                mask.append(True)
            continue
        mask.append(open_char is not None)
    return mask


def _single_marker_pair(lines):
    """(begin_index, end_index) of the SINGLE well-formed lt:mentions
    marker pair in `lines`, or None if zero, multiple, nested, or
    otherwise malformed marker structure is present -- "trust the first
    match" is never done here (codex R7: multiple/malformed marker pairs
    must be rejected outright, not silently resolved to the first). A
    marker line that sits INSIDE a ``` / ~~~ fenced code block is never
    counted as a real marker (#236: a forged example fence must not spoof
    a begin/end pair)."""
    mask = _fenced_line_mask(lines)
    begins = [i for i, ln in enumerate(lines) if ln == _MENTIONS_BEGIN_LINE and not mask[i]]
    ends = [i for i, ln in enumerate(lines) if ln == _MENTIONS_END_LINE and not mask[i]]
    if len(begins) != 1 or len(ends) != 1:
        return None
    b, e = begins[0], ends[0]
    if e <= b:
        return None
    return b, e


def _mentions_region_lines(body_text):
    """The lines strictly between a SINGLE well-formed lt:mentions marker
    pair in `body_text` (frontmatter already stripped by the caller), or
    None if that marker structure is absent or malformed (see
    `_single_marker_pair`)."""
    lines = render_obsidian._split_lf_lines(body_text)
    pair = _single_marker_pair(lines)
    if pair is None:
        return None
    b, e = pair
    return lines[b + 1:e]


def _wikilink_targets(lines):
    targets = set()
    for ln in lines:
        # #236: an inline-code-quoted wikilink (e.g. `` `[[001 real]]` `` or
        # `` ``[[001 real]]`` ``) is literal text an author is QUOTING, not
        # a real link -- strip inline code spans of EVERY backtick-run
        # length before matching so it can never count as coverage.
        for m in _WIKILINK_RE.finditer(_strip_inline_code(ln)):
            targets.add(m.group(1))
    return frozenset(targets)


def parse_mentions_region(note_text):
    """None if `note_text` has no single well-formed lt:mentions marker
    region; otherwise the frozenset of wikilink target strings found
    strictly inside it. `note_text` may be None (note file missing/
    unreadable) -- treated the same as "no region found"."""
    if note_text is None:
        return None
    region_lines = _mentions_region_lines(_strip_frontmatter(note_text))
    if region_lines is None:
        return None
    return _wikilink_targets(region_lines)


def _body_excluding_mentions_region(text):
    """`text` (frontmatter stripped) with any single well-formed
    lt:mentions marker region -- markers included -- removed. Defense in
    depth for metric 2 (native-inline advisory), which must never count a
    link found inside a Mentions region as inline coverage (D4: "Metric 2
    excludes Mentions links"). Segment notes never carry a legitimate
    Mentions region (D1: only entity notes do), so in normal operation
    this is a no-op; it exists so a malformed/hand-edited segment note
    can't inflate the inline-advisory count via a smuggled marker block."""
    lines = render_obsidian._split_lf_lines(_strip_frontmatter(text))
    pair = _single_marker_pair(lines)
    if pair is not None:
        b, e = pair
        lines = lines[:b] + lines[e + 1:]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Filename/identity reconstruction -- mirrors render_obsidian.render()'s OWN
# algorithm exactly (never a re-derivation that could drift), reusing its
# shared helpers directly rather than reimplementing them (the same
# established pattern occurrence_targets.py uses for `_verse_texts`).
# ---------------------------------------------------------------------------

def _seg_filename_map(nodestream):
    """seg -> the wikilink target text ("NNN slug", no ".md") D1's
    render() writes each segment note under -- reconstructed against the
    SAME persisted nodestream.json render() was actually given
    (assemble.py persists it BEFORE dispatch_adapter() runs), using
    render()'s own segment-ordering/slugging algorithm (same seg_order +
    sorted-extra-segs fallback, same _segment_title/
    sanitize_filename_component/_stable_fallback_name calls) -- so this
    map is byte-identical to what render() actually wrote."""
    nodes = nodestream.get("nodes")
    if nodes is not None and not isinstance(nodes, list):
        # #236: promoted from an uncaught downstream error to a clean,
        # named exit 2 -- see this function's own callers' docstrings for
        # why exit 1 (advisory) is never acceptable for a structurally
        # malformed persisted artifact.
        _fatal(f"assembled nodestream: 'nodes' must be a list, got {type(nodes).__name__}")
    nodes_by_seg = {}
    for node in nodes or []:
        # A malformed node (not an object, no string "seg", no int
        # "order_index") must be a clean exit-2 hard error, never an
        # uncaught KeyError escaping as exit 1 with a raw traceback --
        # validated HERE, once, so the sort below can trust every node's
        # "order_index" unconditionally.
        if not isinstance(node, dict):
            _fatal(f"assembled nodestream: a 'nodes' entry is not an object, got {type(node).__name__}")
        seg = node.get("seg")
        if not isinstance(seg, str):
            _fatal(f"assembled nodestream: a node is missing a string 'seg' field, got {seg!r}")
        order_index = node.get("order_index")
        if not isinstance(order_index, int) or isinstance(order_index, bool):
            _fatal(f"assembled nodestream: node {seg!r} is missing an int 'order_index' field, got {order_index!r}")
        nodes_by_seg.setdefault(seg, []).append(node)

    book = nodestream.get("book")
    if book is not None and not isinstance(book, dict):
        # #236: `book = ["x"]` previously reached `.get("seg_order")` on a
        # list a few lines below and raised an uncaught AttributeError
        # (exit 1, advisory -- W9 would silently walk past it).
        _fatal(f"assembled nodestream: 'book' must be an object, got {type(book).__name__}")
    seg_order = (book or {}).get("seg_order") or []
    if not isinstance(seg_order, list) or not all(isinstance(s, str) for s in seg_order):
        # #236: a non-list (e.g. the string "abc") previously iterated as
        # CHARACTERS with no error at all -- a silently wrong answer, not
        # a crash; a list with a non-string element (e.g. `[1, 2]`)
        # previously reached a downstream `.encode()` call and raised an
        # uncaught AttributeError. Both are now this one named exit 2.
        _fatal(f"assembled nodestream: book.seg_order must be a list of strings, got {seg_order!r}")
    extra_segs = sorted(set(nodes_by_seg) - set(seg_order))
    full_order = list(seg_order) + extra_segs
    mapping = {}
    for idx, seg in enumerate(full_order, start=1):
        seg_nodes = sorted(nodes_by_seg.get(seg, []), key=lambda n: n["order_index"])
        title = render_obsidian._segment_title(seg_nodes, seg)
        slug = render_obsidian.sanitize_filename_component(
            title, render_obsidian._stable_fallback_name(seg or str(idx), "segment")
        )
        mapping[seg] = f"{idx:03d} {slug}"
    return mapping


def _entity_maps(canon, profile):
    """(entries, relpath_by_source_form, note_identity_by_source_form) --
    the exact canon entries dict and note-filename resolution
    render_obsidian.render() itself computes, reused directly
    (`_resolve_entity_notes`) so an entity note's expected path/identity
    can never drift from what the renderer actually wrote it under."""
    entries = render_obsidian._canon_entries(canon)
    output_cfg = (profile or {}).get("output") or {}
    folders_map = ((output_cfg.get("adapter_config") or {}).get("obsidian") or {}).get("folders") or {}
    relpath_by_source_form = render_obsidian._resolve_entity_notes(entries, folders_map)
    note_identity_by_source_form = {
        sf: (relpath[: -len(".md")] if relpath.endswith(".md") else relpath)
        for sf, relpath in relpath_by_source_form.items()
    }
    return entries, relpath_by_source_form, note_identity_by_source_form


# ---------------------------------------------------------------------------
# Metric 1 -- Mentions coverage.
# ---------------------------------------------------------------------------

def _compute_missing(aggregate, seg_filename_map, relpath_by_source_form, out_dir):
    """Returns (missing, checked_entities). `missing` is a sorted-by-
    (source_form, seg) list of {"source_form", "seg"} pairs the FRESH
    `occurrence_targets.build()` aggregate expected but the rendered vault
    does not actually link from within a valid Mentions region. An entity
    with zero expected occurrences is never checked (not an error either
    way, D4)."""
    missing = []
    checked_entities = 0
    eligible = aggregate.get("eligible_by_source_form") or {}
    for sf in sorted(eligible):
        records = eligible[sf] or []
        segs_expected = sorted({r["seg"] for r in records})
        if not segs_expected:
            continue
        checked_entities += 1
        relpath = relpath_by_source_form.get(sf)
        note_text = None
        if relpath is not None:
            note_text = _read_text_or_none(out_dir / relpath)
        parsed = parse_mentions_region(note_text)
        for seg in segs_expected:
            target = seg_filename_map.get(seg)
            if parsed is None or target is None or target not in parsed:
                missing.append({"source_form": sf, "seg": seg})
    return missing, checked_entities


# ---------------------------------------------------------------------------
# Metric 2 -- native-inline advisory (exit-neutral).
# ---------------------------------------------------------------------------

def _compute_inline_advisory(aggregate, note_identity_by_source_form, seg_filename_map, out_dir):
    """Tallies inline `[[note_identity|target]]`-style links (the
    pre-existing `_Linker` output) found in each SEGMENT note's own body,
    per entity, and reports entities whose inline count falls short of
    their fresh source-occurrence count -- the #206 gap made visible.
    Exit-neutral: never contributes to `warnings`."""
    identity_to_source_form = {v: k for k, v in note_identity_by_source_form.items()}
    inline_counts = Counter()
    for target in seg_filename_map.values():
        text = _read_text_or_none(out_dir / f"{target}.md")
        if text is None:
            continue
        body = _body_excluding_mentions_region(text)
        for ln in body.split("\n"):
            # #236: same inline-code-stripping discipline as metric 1's
            # `_wikilink_targets` -- a quoted `` `[[...]]` `` or
            # `` ``[[...]]`` `` must not count toward this metric either.
            for m in _WIKILINK_RE.finditer(_strip_inline_code(ln)):
                sf = identity_to_source_form.get(m.group(1))
                if sf is not None:
                    inline_counts[sf] += 1

    eligible = aggregate.get("eligible_by_source_form") or {}
    thin = []
    for sf in sorted(eligible):
        source_occurrences = len(eligible[sf] or [])
        if source_occurrences == 0:
            continue
        inline_links = inline_counts.get(sf, 0)
        if inline_links < source_occurrences:
            thin.append({
                "source_form": sf,
                "inline_links": inline_links,
                "source_occurrences": source_occurrences,
            })
    return thin


# ---------------------------------------------------------------------------
# Collisions + unresolved_homonyms (exit-neutral diagnostics).
# ---------------------------------------------------------------------------

def _renderer_delinked_targets(entries, note_identity_by_source_form):
    """#240 gate half: the set of NFC-normalized, case-SENSITIVE target
    strings `render_obsidian.build_entity_index` actually removes from its
    link map once collision de-linking engages -- i.e. present in the
    `collision_delink=False` map but ABSENT from the `collision_delink=True`
    one. Calls the renderer's OWN function twice rather than
    re-implementing its collision rule, so this can never drift from
    `render()`'s real behavior.

    `build_entity_index` returns a 2-TUPLE `(pattern, target_to_entity)` --
    a bare `set(build_entity_index(...))` would iterate that tuple and
    raise `TypeError: unhashable type: 'dict'`; take `[1]` (the
    target->entity dict) explicitly. Its KEYS are NFC-normalized
    (render_obsidian.py's own NFC-normalize step), case-SENSITIVE target
    strings.

    ⚠️ Correctness precondition, verified true against `render_obsidian.py`
    at time of writing: this set-diff is correct ONLY while a de-linked
    target is fully ABSENT from `target_to_entity` (never left present
    under some sentinel). If `build_entity_index` ever changes to signal
    de-linking a different way, this silently degrades to "nothing was
    de-linked" rather than erroring -- both files are gate-owned, so this
    is a note-to-self, not a cross-session negotiation, but it is written
    down here so a future edit to one side cannot break the other
    invisibly."""
    _, no_delink = render_obsidian.build_entity_index(
        entries, note_identity_by_source_form, collision_delink=False
    )
    _, delinked = render_obsidian.build_entity_index(
        entries, note_identity_by_source_form, collision_delink=True
    )
    return set(no_delink) - set(delinked)


def _compute_collisions(entries, renderer_delinked_targets):
    """Groups canon entries by `canon_senses.normalize_form
    (canonical_target_form)`, reporting every group with >=2 owners.
    Independent of the rendered vault -- computed from canon alone. The
    GROUPING key is normalized (NFC + casefold + whitespace-collapse); the
    reported `canonical_target_form` is the ORIGINAL (display) value of
    the first owner in sorted-source_form order -- membership key folded,
    display stays original, mirroring `_dedupe_path`'s own discipline in
    render_obsidian.py.

    Each row also carries `renderer_delinked: bool` (#240 gate half):
    whether `render_obsidian.build_entity_index` actually de-links THIS
    collision's target under `collision_delink=True` -- surfacing, rather
    than eliminating, the residual disagreement between this gate's own
    casefold+whitespace-collapse grouping key and the renderer's NFC-only,
    case-SENSITIVE, sense_translated-aware one (lead-decision B-C2; a
    case-variant pair is a real canon data-quality problem even though the
    renderer treats the two strings as distinct targets). The membership
    check NFC-normalizes the DISPLAY value before comparing against
    `renderer_delinked_targets` -- which holds NFC-normalized keys -- since
    this function's own `display` map deliberately keeps the ORIGINAL
    (possibly NFD) form; skipping that normalization would silently report
    `renderer_delinked: False` for any collision stored in decomposed
    form. NFC-exact, never casefolded here -- the casefold-vs-NFC
    disagreement is exactly the thing being surfaced, not reconciled."""
    groups = defaultdict(list)
    display = {}
    for sf in sorted(entries):
        entry = entries[sf]
        if not isinstance(entry, dict):
            continue
        target = entry.get("canonical_target_form")
        if not isinstance(target, str) or not target:
            continue
        key = canon_senses.normalize_form(target)
        groups[key].append(sf)
        display.setdefault(key, target)
    collisions = []
    for key, owners in groups.items():
        if len(owners) < 2:
            continue
        canonical_target_form = display[key]
        renderer_delinked = (
            unicodedata.normalize("NFC", canonical_target_form) in renderer_delinked_targets
        )
        collisions.append({
            "canonical_target_form": canonical_target_form,
            "owners": sorted(owners),
            "renderer_delinked": renderer_delinked,
        })
    return sorted(collisions, key=lambda d: d["canonical_target_form"])


def _unresolved_homonyms_list(aggregate):
    out = []
    for sf, info in sorted((aggregate.get("unresolved_homonyms") or {}).items()):
        info = info or {}
        out.append({
            "source_form": sf,
            "count": info.get("count", 0),
            "segs": sorted(info.get("segs") or []),
        })
    return out


# ---------------------------------------------------------------------------
# CLI entry point.
# ---------------------------------------------------------------------------

def build_arg_parser():
    parser = argparse.ArgumentParser(
        description=(
            "Advisory two-metric Mentions-appendix coverage gate -- see "
            "this file's own module docstring."
        ),
    )
    parser.add_argument(
        "--vault",
        default=None,
        metavar="DIR",
        help=(
            "Override the rendered vault directory to scan (default: "
            "resolved the same way assemble.py resolves output.destination, "
            "via output_resolve.resolve_out_dir)."
        ),
    )
    return parser


def main():
    args = build_arg_parser().parse_args()

    try:
        profile = vd.load_profile()
    except SystemExit:
        # validate_draft.py's own load_profile() already halted via
        # sys.exit(2) on a profile/environment precondition, printing to
        # stderr -- that IS this script's own exit-2 contract, so just let
        # it propagate rather than double-report.
        raise
    except Exception as exc:  # noqa: BLE001 -- black-box loader boundary,
        # see validate_assembled.py's own identical R6-1/R7-1 lesson: an
        # exhaustive named-exception list at a black-box config-load
        # boundary is whack-a-mole; ANY exception here means "can't load
        # the profile, can't evaluate" -- exit 2.
        _fatal(
            "could not load profile.yml (via validate_draft.py's own "
            f"profile loader): {type(exc).__name__}: {exc}"
        )

    if not _effective_enabled(profile):
        print(json.dumps(_disabled_report()))
        sys.exit(0)

    manifest = _require_json(MANIFEST_PATH, "manifest.json")
    if not isinstance(manifest, dict):
        _fatal(f"manifest.json at {MANIFEST_PATH} did not parse to an object")

    # canon.json absent is tolerated as zero entries -- mirrors assemble.py's
    # own default (`canon = {"entries": {}, ...}` when CANON_PATH doesn't
    # exist); a PRESENT-but-malformed canon.json is still a hard error.
    canon = {"entries": {}}
    if CANON_PATH.is_file():
        canon = _require_json(CANON_PATH, "canon.json")
        if not isinstance(canon, dict):
            _fatal(f"canon.json at {CANON_PATH} did not parse to an object")
    if "entries" in canon and not isinstance(canon["entries"], dict):
        # #236: previously silently absorbed by render_obsidian._canon_entries
        # (tolerant by design for the RENDERER, since it accepts either the
        # whole canon.json OR a bare entries{} mapping) -- but that same
        # tolerance let this GATE fall back to zero entities checked and
        # exit 0, a green-but-vacuous report. The gate is stricter than the
        # renderer on purpose here: a malformed 'entries' is a real defect
        # worth a clean exit 2, never a silent "nothing to check".
        _fatal(f"canon.json at {CANON_PATH}: 'entries' is present but not an object")

    try:
        senses_result = canon_senses.load_senses(CANON_SENSES_PATH, allow_absent=True)
    except canon_senses.CanonSensesLoadError as exc:
        _fatal(f"canon_senses.json failed to load: {exc}")

    try:
        particle_config = profile["source"]["language"]["particle_config"]
    except (KeyError, TypeError) as exc:
        _fatal(f"profile.yml missing required field source.language.{exc}")
    try:
        language_config = bootstrap_names.load_language_config(particle_config)
    except bootstrap_names.BootstrapNamesError as exc:
        _fatal(f"could not resolve source.language.particle_config: {exc}")

    nodestream = _require_json(NODESTREAM_PATH, "assembled nodestream")
    if not isinstance(nodestream, dict):
        _fatal(f"assembled nodestream at {NODESTREAM_PATH} did not parse to an object")

    try:
        aggregate = occurrence_targets.build(
            manifest, canon, senses_result, language_config, nodestream
        )
    except Exception as exc:  # noqa: BLE001 -- A1's own module is a
        # trusted-but-independently-owned dependency; any failure it
        # raises means the gate cannot compute an expected universe at
        # all -- a hard error (exit 2), never a silently empty report.
        _fatal(f"occurrence_targets.build() failed: {type(exc).__name__}: {exc}")

    if args.vault:
        out_dir = Path(args.vault)
    else:
        try:
            out_dir = output_resolve.resolve_out_dir(profile, DURABLE_ROOT)
        except output_resolve.OutputResolveError as exc:
            _fatal(str(exc))

    entries, relpath_by_source_form, note_identity_by_source_form = _entity_maps(canon, profile)
    seg_map = _seg_filename_map(nodestream)

    missing, checked_entities = _compute_missing(aggregate, seg_map, relpath_by_source_form, out_dir)
    thin_coverage = _compute_inline_advisory(aggregate, note_identity_by_source_form, seg_map, out_dir)
    renderer_delinked_targets = _renderer_delinked_targets(entries, note_identity_by_source_form)
    collisions = _compute_collisions(entries, renderer_delinked_targets)
    unresolved = _unresolved_homonyms_list(aggregate)

    report = {
        "mentions_coverage": {
            "status": "enabled",
            "checked_entities": checked_entities,
            "missing": missing,
        },
        "unresolved_homonyms": unresolved,
        "collisions": collisions,
        "inline_advisory": {"thin_coverage": thin_coverage},
        "warnings": len(missing),
    }
    print(json.dumps(report, ensure_ascii=False))
    sys.exit(1 if report["warnings"] > 0 else 0)


if __name__ == "__main__":
    main()
