#!/usr/bin/env python3
"""render_obsidian.py -- NodeStream -> Obsidian vault (W9 Assemble, Phase 1).

The shipped, primary `output.target: obsidian` renderer. Authoritative spec:
`references/output-target-adapters/obsidian.md` (vault layout, entity-note
frontmatter, the wikilink rule, the category->folder catalog, the security
posture on `category`/folder values and note filenames) and
`references/assembly-and-output.md` (the NodeStream contract this script
consumes and the render+diff acceptance gate that checks this script's own
output). Read those docs first if anything below is unclear -- they are the
ground truth this script implements, not the other way around.

## Entry point

    def render(nodestream: dict, canon: dict, profile: dict, out_dir: Path) -> dict

Every built-in output-target adapter exposes this exact signature (see
`references/output-target-adapters/README.md`). `assemble.py` imports this
module as a flat sibling (`sys.path.insert(0, SCRIPTS_DIR); import
render_obsidian`) and calls `render_obsidian.render(...)` directly -- this
script has no other coupling to assemble.py's own internals, and is built
and tested against a hand-authored fixture NodeStream, not assemble.py's
real output.

## What gets written, under `out_dir`

- One **narrative page** per `manifest.segments[]` entry (a NodeStream
  `seg`), in `book.seg_order` reading order, named `"{NNN} {title}.md"` at
  the vault root (`NNN` a stable zero-padded position, `title` the
  segment's own first heading-kind node text -- with that heading's own
  KNOWN sentinels (footnote anchors, declared verse placeholders) resolved
  out to plain text first, see `_heading_plain_text` -- or the raw `seg` id
  if the segment carries no heading). Sentinels are resolved here: `⟦FNREF_N⟧`
  becomes an Obsidian native footnote reference (`[^N]`, definitions
  appended at the foot of the page), and each verse placeholder becomes
  either a full blockquote (a dedicated verse block, `kind: "verse"`) or a
  compact inline rendering (a verse embedded inside a prose/heading block,
  `kind` something else but still carrying that verse in its own
  `verses[]`) -- or nothing at all under `verse_policy.mode: skip`, per the
  shared assembler contract (an empty verse `content` is not an error).
- One **entity note** per `canon.json` `entries{}` entry (keyed by
  `source_form`), routed into `<folder>/` per the category->folder catalog
  (`output.adapter_config.obsidian.folders`; absent/unsafe -> `other`).
- One **markup note** per declared-entity identity `canon.json` has no
  entry for, but ONLY when `output.entity_markup.index_from: markup` is in
  effect (#795). Absent that knob nothing below it runs, and this adapter's
  output matches 1.73.0 except in two deliberate places, both documented
  under obsidian.md's "Editorial brackets": the outer editorial `[`/`]` around
  ANY emitted wikilink (canon links included) is now escaped, and a heading
  whose source text literally contains an `⟦ENT_n⟧`-shaped token has those
  tokens removed and its internal whitespace collapsed. See the
  "Declared entity markup"
  section further down for the whole feature: assemble.py records what the
  translator marked, and one pre-pass here turns every recorded span into a
  wikilink to either the canon note or a minted markup note.

Canon terms occurring in rendered text (narrative prose/headings, verse
content, and footnote definitions alike) are wikilinked -- see
`build_entity_index`/`_Linker` below for the exact longest-first,
first-occurrence-per-block matching rule from obsidian.md.

## Security

`category`/folder values and note filenames both reach a filesystem path --
see `_resolve_folder`/`sanitize_filename_component` below, and obsidian.md's
own "Security" section, for the two *different* positive allow-lists this
script applies (folders: a small, project-declared, ASCII vocabulary;
filenames: a Unicode-aware allow-list, since `source_form` is often
non-ASCII source-script text by design) and why a denylist would not be
sufficient for either.
"""

import argparse
import copy
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

# Importing a sibling module writes scripts/__pycache__/*.pyc. Several
# entrypoints here promise not to write anything (cache_key.py) or promise ZERO
# filesystem writes in dry-run (backfill_resume_gate_ack.py), so the whole set
# opts out uniformly rather than case by case.
sys.dont_write_bytecode = True


# --- the shared one-line JSON serialiser (#369) -----------------------------
# Loaded by EXACT PATH, never `import json_stdout`. A bare sibling import
# resolves through the global sys.modules cache regardless of which staged copy
# the CALLER intended, so one process that stages several durable roots would
# bind the FIRST root's copy for all of them. exec_module() opens this file's
# own sibling or raises -- the loud failure the staging discipline depends on,
# and it needs no cache eviction to get there. `Path(__file__).absolute()`
# rather than `.resolve()`: the unresolved form is what lets a caller's own
# no-follow symlink logic still see the path it was handed.
import importlib.util as _importlib_util

_JSON_STDOUT_PATH = Path(__file__).absolute().parent / "json_stdout.py"
try:
    _json_stdout_spec = _importlib_util.spec_from_file_location(
        "json_stdout", _JSON_STDOUT_PATH
    )
    if _json_stdout_spec is None or _json_stdout_spec.loader is None:
        raise ImportError(f"no loader for {_JSON_STDOUT_PATH}")
    _json_stdout = _importlib_util.module_from_spec(_json_stdout_spec)
    # OSError, not ImportError alone: spec_from_file_location() happily builds a
    # spec for a file that is not there, and it is exec_module() that raises
    # FileNotFoundError when it opens the source.
    _json_stdout_spec.loader.exec_module(_json_stdout)
except (ImportError, OSError) as _json_stdout_exc:  # pragma: no cover - staging error path
    sys.exit(
        f"render_obsidian.py: cannot load json_stdout.py from {_JSON_STDOUT_PATH} "
        f"({_json_stdout_exc}).\n"
        "json_stdout.py must be installed alongside render_obsidian.py under "
        "${durable_root}/scripts/ -- Step 0a's copy pass places it there."
    )

dumps_line = _json_stdout.dumps_line

try:
    import yaml
except ImportError:
    print(
        "ERROR: render_obsidian.py requires the 'PyYAML' package to write "
        "Obsidian note frontmatter (YAML front matter for entity and "
        "segment notes). Install with: pip install PyYAML (or: pip install "
        "-r requirements.txt from the literary-translator plugin's own "
        "directory).",
        file=sys.stderr,
    )
    sys.exit(2)

# ---------------------------------------------------------------------------
# Self-anchoring: this script always lives at {durable_root}/scripts/<name>.py.
# It never assumes cwd == durable_root, and never takes a --durable-root flag.
# ---------------------------------------------------------------------------
SCRIPTS_DIR = Path(__file__).resolve().parent
DURABLE_ROOT = SCRIPTS_DIR.parent
CANON_PATH = DURABLE_ROOT / "canon.json"
NODESTREAM_PATH = DURABLE_ROOT / "out" / ".assembled" / "nodestream.json"

# Format-neutral placeholder sentinel for footnote anchors -- same literal
# convention as validate_draft.py/final_audit.py's own FNREF_RE (⟦FNREF_N⟧);
# verse placeholders have no fixed naming convention of their own (they are
# free-form per segpack.schema.json's `placeholder` field), so those are
# always taken verbatim from each BlockNode's own `verses[].placeholder`,
# never reconstructed from a vid.
_FNREF_SENTINEL_FMT = "⟦FNREF_{n}⟧"

_TITLE_FN_MARKUP_RE = re.compile(r"\[\^\d+\]")       # rendered markdown footnote ref -- unwanted in a title/slug
_TITLE_FNREF_ANCHOR_RE = re.compile(r"⟦FNREF_\d+⟧")  # machine footnote-anchor sentinel -- never legitimate prose

# LABEL PROTECTION (inline-verse gloss label) --
# The renderer-authored inline-verse gloss label (`_render_verse_inline`'s
# literal " (lit.: " prefix) must never itself be swept into a wikilink. Since
# #105c links the WHOLE composed block text in one pass, and _Linker.pattern is
# an unanchored literal alternation (no word boundary), a canon entry whose
# canonical_target_form is/contains "lit" would otherwise match INSIDE this
# renderer-authored label and steal the block's single first-occurrence slot
# from the real gloss content.
#
# Protection is done by POSITION, never by string content. `_render_verse_inline`
# emits the label as ordinary literal text and returns the (start, end) char
# offsets of that label within its own output; `_render_block` maps those to
# ABSOLUTE offsets in the composed block text (tracked exactly through the
# verse/fnref substitution pass) and hands them to `_Linker.link(extra_protected=)`,
# which merges them into the same protected-span machinery a _PROTECTED_SPAN_RE
# match uses -- never matched into, never counted as "seen", carried through the
# NFC reconstruction verbatim. The literal label is ALREADY its final,
# human-readable form, so nothing is ever restored or rewritten afterwards.
#
# CORRECTNESS INVARIANT -- protection MUST stay position-based. Rounds 2-4 used a
# fixed sentinel string as a stand-in for the label, then found/restored it by
# CONTENT MATCHING; three separate real collisions followed (round 3: a free-form
# verse placeholder literally EQUAL to the sentinel; round 4: a canon `source_form`
# CONTAINING it as a substring; round 5: a block's OWN prose/document text
# containing it verbatim). Any content-matching restore is structurally unable to
# tell "the occurrence I inserted" from "an identical string from any other
# source, anywhere in the pipeline", so a 4th collision vector was always
# inevitable. Position tracking has no such failure mode: only the exact rendered
# label span is protected, and nothing is rewritten.

DEFAULT_FOLDER = "other"

# Ownership marker (review round 2, item C1): a dotfile stamped into out_dir
# on every successful render, so _clean_vault_content can tell "a vault this
# adapter has already rendered into" (safe to clean) apart from "some other
# directory that happens to already have content" (refuse to touch). A
# dotfile so the existing dot-preserving clean keeps it across re-renders.
VAULT_MARKER_FILENAME = ".literary-translator-vault.json"

# D1/D4 opt-in Mentions-section feature (RFC lt-appendix-backlink-
# integrity; D3 collision de-linking is a SEPARATE concern not gated by
# this opt-in `enabled` flag at all -- see build_entity_index -- though it
# still gates, like this feature, on output.target == "obsidian"): the
# reserved boundary-comment markers render() wraps a generated
# "## Mentions" section in, and the token a canon field is forbidden from
# containing once the feature is active -- see
# `_effective_mentions_enabled`/`_validate_mentions_safe_canon` below.
# HTML comments so they stay invisible in Obsidian's rendered preview.
MENTIONS_SECTION_MARKER_BEGIN = "<!-- lt:mentions:begin -->"
MENTIONS_SECTION_MARKER_END = "<!-- lt:mentions:end -->"
_MENTIONS_RESERVED_TOKEN = "lt:mentions:"

# The full str.splitlines() line-boundary codepoint set (see
# `_split_lf_lines`'s own docstring above) -- a canon `source_form`/
# `canonical_target_form` containing any of these could inject a forged
# extra line (e.g. a spoofed marker) into the raw Markdown heading it
# renders into.
_MENTIONS_LINE_BREAK_CHARS = frozenset(
    "\n\r\v\f\x1c\x1d\x1e\x85" + chr(0x2028) + chr(0x2029)
)


def _is_obsidian_target(profile):
    """`True` iff `output.target` is EXACTLY "obsidian" -- the single
    source of truth for that check, shared by `_effective_mentions_enabled`
    (D1/D4) and render()'s own D3 collision-de-link call site, so the
    magic string lives in exactly one place. This is what keeps the
    standalone CLI (`main()` below, whose profile can carry a dormant
    `obsidian` sub-block while `--out-dir`/`output.target` actually point
    somewhere else, e.g. `target: "custom"`) from ever activating D1, D3,
    or D4: those must fire only when this adapter is genuinely the one in
    effect for real assembly."""
    output_cfg = (profile or {}).get("output") or {}
    return output_cfg.get("target") == "obsidian"


def _effective_mentions_enabled(profile):
    """The ONE predicate D1 (this file) and D4 (`validate_backlinks.py`,
    computed independently there) both gate on -- `_is_obsidian_target(
    profile)` must hold AND
    `output.adapter_config.obsidian.mentions_section.enabled` must not be
    boolean `False`. ON BY DEFAULT (1.10.0+): an absent `mentions_section`
    block, an absent `enabled` key, or `enabled: null` all resolve to
    enabled -- an explicit `enabled: false` is the only way to opt out.
    Computed fresh from render()'s own `profile` argument every call, never
    cached/inherited -- see `_is_obsidian_target`'s own docstring for why
    the target check alone (never this flag) is what gates the standalone
    CLI's `target: "custom"` path out of the Mentions section and the
    reserved-field rejections. D3 (collision de-linking,
    `build_entity_index`) does NOT gate on THIS predicate (the `enabled`
    flag) at all (#206/#207) -- a homonym collision is de-linked on every
    real obsidian render regardless of the appendix flag. D3 STILL gates
    on `_is_obsidian_target(profile)` though, via its own call in render():
    the standalone CLI's dormant-`obsidian`-under-`target:"custom"` path
    continues to activate none of D1/D3/D4. See build_entity_index's own
    docstring for why."""
    if not _is_obsidian_target(profile):
        return False
    output_cfg = (profile or {}).get("output") or {}
    obsidian_cfg = (output_cfg.get("adapter_config") or {}).get("obsidian") or {}
    mentions_cfg = obsidian_cfg.get("mentions_section") or {}
    return mentions_cfg.get("enabled") is not False


class RenderError(Exception):
    """Raised for a fail-closed render() precondition (an unsafe or
    unmanaged out_dir) that must surface as a one-JSON-line, reason-
    carrying failure to whichever caller invoked render() -- assemble.py
    in the real pipeline, or this module's own CLI (`main()` below).
    Carries `.reason` (a short machine-readable string) alongside the
    human-readable message, mirroring output_resolve.py's own
    `OutputResolveError`."""

    def __init__(self, reason, message):
        super().__init__(message)
        self.reason = reason

# Category/folder allow-list: a small, project-DECLARED, ASCII vocabulary
# (obsidian.md's "Security" section) -- a positive allow-list, not a
# denylist (a denylist rejecting "/"/".." alone would still pass shell/path
# metacharacters it didn't anticipate; see the repo's identifier->path
# allow-list precedent). re.fullmatch, never re.match(...+"$"), since
# Python's "$" also matches just before a trailing newline.
_FOLDER_ALLOW_RE = re.compile(r"^[A-Za-z0-9 _-]+$")

# Note-filename allow-list: unlike category/folder, `source_form` is often
# non-ASCII source-script text (Cyrillic, etc.) BY DESIGN -- see SKILL.md's
# English-only-identifiers rule, which governs code identifiers, not this
# kind of data-derived filename. Still a positive allow-list (any Unicode
# alphanumeric, str.isalnum(), plus the two legs below) rather than a
# denylist: everything else -- including every path separator, control/NUL
# bytes, and every format character (Cf: ZWJ/ZWNJ/RLM) -- is replaced,
# never merely blocked after the fact.
#
# Leg 2, by Unicode CATEGORY rather than by enumeration: every combining
# mark. A mark is combining BY DEFINITION, so it can be neither a path
# separator nor an extension -- admitting the category wholesale therefore
# cannot weaken the TRAVERSAL and EXTENSION guarantees, which is exactly
# why this leg is a category test and not a character list. It does change
# one thing, and the claim is deliberately narrow about it: a run of marks
# no longer collapses into a single "_", so a stem can now be far LONGER
# than the same input produced before -- see _FILENAME_MAX_BYTES below. Before it existed, a
# fully pointed Hebrew name became one "_" per niqqud/cantillation mark:
# a stem no reader can type (#586, measured over a delivered vault).
_FILENAME_MARK_CATEGORIES = ("Mn", "Mc", "Me")

# Leg 3, curated and deliberately short. Characters are spelled as escapes,
# never as literals: an RTL or combining character pasted into this line
# would be unreviewable in a diff.
#   "." ","    printed names ("Mrs. Adil", "Miriam, daughter of our Rebbe")
#   U+2019     RIGHT SINGLE QUOTATION MARK -- the typographic apostrophe
#              a printed name carries, parity with the ASCII "'" this
#              set already admitted
#   U+05BE     HEBREW PUNCTUATION MAQAF -- the Hebrew hyphen
#   U+05F3     HEBREW PUNCTUATION GERESH
#   U+05F4     HEBREW PUNCTUATION GERSHAYIM
# The last three are letter-level orthography in Hebrew/Yiddish names, not
# decoration. Admitting "." is what forces sanitize_filename_component's
# normalization tail to enforce the traversal/extension properties in code
# rather than inherit them from this set's silence -- see its docstring.
_FILENAME_EXTRA_CHARS = " _-()'.,\u2019\u05be\u05f3\u05f4"

# Byte cap on a sanitized stem. NAME_MAX is 255 on every filesystem this is
# driven from, counted in BYTES on ext4 and in characters on APFS -- a BYTE
# budget is therefore conservative for both. 240 leaves 15 bytes for the
# ".md" this script appends AND for _dedupe_path's "-<n>" collision suffix,
# which is applied AFTER this function returns.
#
# This cap is not decoration: an over-long stem makes _write_note raise
# ENAMETOOLONG *after* _clean_vault_content has already emptied the managed
# vault, so one hostile name aborts the whole render halfway. That was
# already reachable before #586 with a long enough alphanumeric name (300
# "A"s sanitized to a 300-character stem; measured), and #586's mark leg
# made it reachable a second way, since a run of marks no longer collapses
# into a single "_" (review round 1). Truncation rather than the fallback
# name, because a long heading truncated is still a heading a reader
# recognizes; the collisions truncation can create are exactly what
# _dedupe_path already resolves deterministically.
_FILENAME_MAX_BYTES = 240

# Cap on CONSECUTIVE combining marks, a different limit from the byte cap
# and not implied by it. The number defends ONE measured filesystem
# predicate and claims nothing else: macOS refuses, with EILSEQ, to create a
# filename carrying 32 or more marks on a single base, whatever its byte
# length -- measured on this project's filesystem, where 31 marks writes and
# 32 does not, while the same marks spread over separate bases are fine. 30
# leaves a margin under that. The kernel applies that limit to the name it
# has CANONICALLY DECOMPOSED, so the loop below counts NFD marks rather
# than written characters -- see it for the two ways those counts diverge. (It is NOT Unicode's Stream-Safe Text Format
# bound, which an earlier version of this comment cited: UAX #15 counts
# non-starters after NFKD and treats U+034F CGJ as a break, and this loop
# does neither.)
#
# Where that makes it over-catch, deliberately: measured, macOS accepts
# "A" + 30 marks + CGJ + 30 marks, and this cap truncates it anyway, because
# CGJ is itself Mn and counts toward the run. Under-catching is what would
# abort a render; over-catching costs a name no orthography produces -- a
# fully pointed Hebrew letter carries a vowel, a dagesh and a cantillation
# mark, not thirty-one. Marks past the cap become "_" (and then collapse), so
# a pathological run degrades to a writable name instead of aborting a render
# that has already emptied the vault.
_MAX_MARKS_PER_BASE = 30

# Win32 reserved device basenames. A device name stays reserved when an
# extension follows it, so "AUX.txt" and its emitted "AUX.txt.md" are both
# device paths and neither can be created -- and the failure lands in
# _write_note, after _clean_vault_content has emptied the managed vault.
# Bare "CON"/"NUL"/"AUX" reached that state before #586 too; admitting "."
# widened the class to "<device>.<anything>", which is what made this the
# same defect as the two caps above rather than a separate wish, and why it
# is fixed here instead of deferred (#592). The stem gets one "_" appended
# to its device basename -- "AUX.txt" -> "AUX_.txt", "CON" -> "CON_" --
# rather than falling back to the sha1 name, so the reader still recognises
# the note. Enforced on every platform, not just Windows: a vault is copied
# and synced between machines, and a name that is unwritable THERE is a
# defect wherever it was rendered.
# The superscripts are Microsoft's own list, not a guess: "Windows recognizes
# the 8-bit ISO/IEC 8859-1 superscript digits U+00B9, U+00B2 and U+00B3 as
# digits and treats them as valid parts of COM# and LPT# device names" --
# learn.microsoft.com/en-us/windows/win32/fileio/naming-a-file, read at
# source. They matter here because `str.isalnum()` is True for all three, so
# "COM\u00b9" and "COM\u00b2.txt" sail through the allow-list untouched (MR bot,
# second round). Spelled as escapes for the same reason as
# _FILENAME_EXTRA_CHARS: a Latin-1 character pasted into a list of ASCII
# identifiers is easy to misread and impossible to review.
_WIN32_RESERVED_STEMS = frozenset(
    ["CON", "PRN", "AUX", "NUL"]
    + [f"COM{d}" for d in "123456789\u00b9\u00b2\u00b3"]
    + [f"LPT{d}" for d in "123456789\u00b9\u00b2\u00b3"]
)

_RTL_LANGUAGE_CODES = {
    "ar", "he", "fa", "ur", "yi", "ps", "sd", "ug", "dv", "arc", "ckb",
}

# Syntax-aware wikilinker guard (review round 1 finding): a canon target must
# never be wrapped when it falls inside an ALREADY-EMITTED wikilink
# (`[[...]]`), an Obsidian footnote reference (`[^N]`), or a raw,
# not-yet-substituted sentinel token (`⟦...⟧`) -- e.g. target "Alice" inside
# "[[Manual|Alice]]" would otherwise nest, and target "1" would corrupt
# "[^1]" into "[^[[One|1]]]". Protected spans are recomputed once per
# `link()` call over that call's own input text; any matcher hit overlapping
# one is left untouched -- and NOT counted as "seen" for the first-
# occurrence bookkeeping, since it was never actually re-rendered there.
#
# Known limitation (accepted, not fixed): this regex's non-greedy match
# plus non-overlapping `finditer()` only protects through the FIRST closer
# of a NESTED span using the same delimiter pair, e.g.
# "⟦outer ⟦Alice⟧ tail Alice⟧ after Alice" only protects up to the inner
# "⟧", so the second "Alice" (still lexically inside the outer sentinel)
# is incorrectly treated as matchable. None of this plugin's actual inputs
# nest same-delimiter spans today (FNREF sentinels are flat, never
# self-nesting; a literal "[[" inside translated prose is pathological) --
# a correct fix needs a balanced-delimiter/stack-based scan, not a regex,
# which is disproportionate for a currently-untriggerable edge case.
_PROTECTED_SPAN_RE = re.compile(r"\[\[.*?\]\]|\[\^\d+\]|⟦[^⟧]+⟧")


def _merge_spans(spans):
    r"""Coalesce a list of (start, end) char-offset spans into the minimal set
    of disjoint, ascending spans covering the same offsets -- overlapping or
    directly touching spans collapse into their union. `_Linker.link`'s
    NFC-reconstruction loop requires disjoint, ascending spans; a bare
    `sorted()` only orders by start and would let an overlap through, causing
    the loop to re-copy the already-emitted inner span and regress its cursor.
    Standard interval-merge; pure, order-independent of the caller.

    Known limitation (accepted, not fixed): a zero-length span (start == end)
    would survive this merge as an empty normalization boundary and could
    block NFC composition across that position in the reconstruction loop.
    Not reachable via either of this function's real inputs today:
    `_PROTECTED_SPAN_RE`'s three alternatives (`\[\[.*?\]\]`, `\[\^\d+\]`,
    `⟦[^⟧]+⟧`) each require at least one/four characters and can never match
    zero-length; `_render_verse_inline`'s label span is always exactly
    `len(" (lit.: ")` (a hardcoded source literal, not data-driven) wide.
    If a future caller could ever supply a zero-length span, filter it out
    here rather than relying on this argument staying true."""
    if not spans:
        return []
    spans = sorted(spans)
    merged = [spans[0]]
    for start, end in spans[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end:  # overlapping or touching -- fuse into one span
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return merged


# ---------------------------------------------------------------------------
# Canon -> entity-linking index (obsidian.md's "wikilink rule")
# ---------------------------------------------------------------------------

def _canon_entries(canon):
    """`canon` is expected to be the whole parsed canon.json (`{"entries":
    {...}, "review_queue": [...], ...}`), per canon-file.schema.json -- but
    tolerate being handed just the entries{} mapping directly too, since the
    render() signature only names the parameter `canon: dict` without
    pinning which shape the caller passes."""
    if isinstance(canon, dict):
        entries = canon.get("entries")
        if isinstance(entries, dict):
            return entries
        if "entries" not in canon:
            return canon
    return {}


def _reject_reserved_mentions_token(value, field_label, source_form):
    if isinstance(value, str) and _MENTIONS_RESERVED_TOKEN in value:
        raise RenderError(
            "mentions_reserved_token_in_canon_field",
            f"canon entry {source_form!r}'s {field_label} contains the "
            f"reserved Mentions-marker token {_MENTIONS_RESERVED_TOKEN!r} -- "
            f"once mentions_section.enabled is true this field can reach raw "
            f"rendered Markdown and could forge a fake '## Mentions' section "
            f"boundary, spoofing validate_backlinks.py's coverage gate. "
            f"Rename it in canon.json.",
        )


def _reject_line_break_in_mentions_field(value, field_label, source_form):
    if isinstance(value, str) and any(ch in _MENTIONS_LINE_BREAK_CHARS for ch in value):
        raise RenderError(
            "mentions_field_line_break",
            f"canon entry {source_form!r}'s {field_label} contains a "
            f"line-break character -- once mentions_section.enabled is true "
            f"this field can become a raw Markdown heading, and a newline "
            f"there could inject a forged extra line (e.g. a spoofed "
            f"Mentions marker) disguised as a fresh heading. Rename it in "
            f"canon.json.",
        )


def _validate_mentions_safe_canon(entries):
    """Called by `render()` ONLY when `_effective_mentions_enabled(profile)`
    holds (D1). No canon field that can reach raw rendered Markdown --
    `canonical_target_form`, the `source_form` heading fallback (both feed
    `_render_entity_note`'s `# {heading}` line and `_Linker`'s emitted
    `[[note|target]]`/`(source_form)` inline text), and `note` -- may
    contain the reserved boundary-marker token `_MENTIONS_RESERVED_TOKEN`
    (codex R5/R6: an authored value containing it could forge a fake `##
    Mentions` region and spoof `validate_backlinks.py`, which trusts ONLY
    the exact marker pair `render()` itself emits). `source_form`/
    `canonical_target_form` are ALSO rejected if they contain any
    line-break character (codex R6: the newline-injected-heading forgery
    -- a newline there could inject an entirely new forged line disguised
    as a fresh Markdown heading; `note` is exempted from this second check
    since it is free-form authored prose, not itself renderable as a
    heading). Iterates `sorted(entries)` for a deterministic
    first-violation report; raises `RenderError` and halts before any note
    is written -- fail-closed, never a partial/best-effort render."""
    for source_form in sorted(entries):
        entry = entries[source_form]
        if not isinstance(entry, dict):
            continue
        target = entry.get("canonical_target_form") or ""
        note = entry.get("note") or ""
        _reject_reserved_mentions_token(target, "canonical_target_form", source_form)
        _reject_reserved_mentions_token(source_form, "source_form", source_form)
        _reject_reserved_mentions_token(note, "note", source_form)
        _reject_line_break_in_mentions_field(target, "canonical_target_form", source_form)
        _reject_line_break_in_mentions_field(source_form, "source_form", source_form)


def _owners_by_target(entries):
    """`{NFC canonical_target_form: [(source_form, basis), ...]}` -- every
    owner of every target, UNREDUCED. Order within a list follows `entries`
    iteration order (immaterial: both the tiebreak and the delink check in
    `_link_decision` are order-independent). Factored out of
    `build_entity_index` (#588) so `delinked_owners_by_target` groups owners
    by exactly the same rule instead of re-implementing it."""
    owners_by_target = defaultdict(list)
    for source_form, entry in (entries or {}).items():
        if not isinstance(entry, dict):
            continue
        target = entry.get("canonical_target_form")
        if not target or not target.strip():
            continue
        # NFC-normalize so a canon entry stored in decomposed (NFD) form
        # collapses onto the same target as an NFC one, and so the pattern
        # built below matches consistently against `_Linker.link`'s own
        # NFC-normalized scan text (block text can carry either form,
        # spliced in from different upstream sources).
        target = unicodedata.normalize("NFC", target)
        # `basis` carried alongside (#240) so the sense_translated exclusion
        # can be applied AFTER the collision tally below, not before it --
        # see build_entity_index's own docstring.
        owners_by_target[target].append((source_form, entry.get("basis")))
    return owners_by_target


def _link_decision(owners, collision_delink, primary_by_source_form=None):
    """`(winner_source_form_or_None, delinked)` for ONE target's owner list.

    THE one implementation of the inline-link rule, called by both
    `build_entity_index` (which needs the winner) and
    `delinked_owners_by_target` (which needs `delinked`), so the two can
    never drift.

    `delinked` is True only when the >=2-owner rule removed a target that
    the tiebreak would OTHERWISE have linked -- i.e. de-linking actually
    cost something. A target whose owners are ALL `sense_translated` is
    dropped either way, so it is never reported as a cost. That makes this
    flag equal, by construction, to `validate_backlinks.py`'s own
    "present in the collision_delink=False map, absent from the True one"
    two-call diff (#240).

    `primary_by_source_form` (#588, optional, VALIDATED by the caller --
    see `_validate_link_groups`) is the `{member: primary}` projection of
    the `canon_link_groups.json` sidecar: an upstream-established statement
    that N canon forms denote ONE referent. It is consulted ONLY inside the
    de-link branch, so it can move nothing else: a single-owner target keeps
    its own link, no string becomes newly matchable, and an absent/empty map
    reproduces 1.29.0 behavior exactly. When every owner of a colliding
    target reduces to the SAME group primary -- only reachable when they are
    all members of one group -- and no owner is `sense_translated`, the
    shared target links to that primary's note instead of being de-linked.
    A `sense_translated` owner still suppresses the link entirely: that
    target string is an ordinary word by construction, and the anti-flood
    invariant (#138) outranks a group's routing preference."""
    groups = primary_by_source_form or {}
    survivors = [
        source_form for source_form, basis in owners
        if basis != "sense_translated"
    ]
    if collision_delink and len(owners) >= 2:
        identities = {groups.get(source_form, source_form) for source_form, _basis in owners}
        if len(identities) == 1 and len(survivors) == len(owners):
            return next(iter(identities)), False  # one established entity -- re-linked to its primary
        # >=2 distinct entities (or a group plus an outsider, or a
        # sense_translated owner): delinked entirely -- no inline link for
        # this string at all. It COST an link only if some owner could have
        # won the tiebreak in the first place.
        return None, bool(survivors)
    if not survivors:
        return None, False  # every owner is sense_translated -- never auto-linked, drop the target entirely
    # The documented, fixed tiebreak: shortest source_form, then
    # lexicographic -- sense_translated owners already excluded above.
    return min(survivors, key=lambda sf: (len(sf), sf)), False


def delinked_owners_by_target(entries, primary_by_source_form=None):
    """`{canonical_target_form: [source_form, ...]}` for exactly the targets
    collision de-linking removes from the link map that the tiebreak would
    otherwise have linked (#588) -- the population whose occurrences carry
    no inline link in the rendered vault, and the set
    `_Linker`'s diagnostic counter charges occurrences to.

    Owners are the target's FULL owner list (sorted), including any
    `sense_translated` owner that contributed to the collision tally
    (#240), so the operator sees every canon form implicated in the
    suppression, not only the ones that could have won."""
    out = {}
    for target, owners in _owners_by_target(entries).items():
        _winner, delinked = _link_decision(owners, True, primary_by_source_form)
        if delinked:
            out[target] = sorted(source_form for source_form, _basis in owners)
    return out


def _longest_first_pattern(targets):
    """The ONE way this module compiles a set of `canonical_target_form`
    strings into a scanning alternation, shared by `build_entity_index` and
    `build_diagnostic_pattern` so the two can never drift.

    LONGEST FIRST, then lexicographic: Python's `re` alternation tries
    alternatives in order at a given start position, so ordering longest-first
    is what stops a shorter name shadowing a longer one that contains it as a
    substring. Each alternative is `re.escape`d -- a `canonical_target_form` is
    free-form text and may legally contain regex metacharacters.

    Returns None when there is nothing to scan for."""
    if not targets:
        return None
    ordered = sorted(targets, key=lambda t: (-len(t), t))
    return re.compile("|".join(re.escape(t) for t in ordered))


def build_diagnostic_pattern(linkable_targets, delinked_targets):
    """The single compiled alternation `_Linker` scans for #588's cost
    metric: every LINKABLE and every DE-LINKED target, LONGEST FIRST, each
    alternative `re.escape`d (a `canonical_target_form` is free-form text
    and may legally contain regex metacharacters).

    The union -- rather than the de-linked targets alone -- is what makes
    attribution correct. The wikilink rule links only the FIRST occurrence
    of a target per block, so a linked name's later occurrences sit in the
    prose as plain text; a de-linked short name nested inside one of them
    ("Reb Noson" inside "Reb Noson of Nemirov") must be charged to the
    longer name, not counted as a silenced mention of the shorter one.
    Longest-first, non-overlapping matching gives each physical occurrence
    exactly one owner -- the same guarantee `build_entity_index`'s own
    pattern relies on, and with the same documented consequence for two
    targets that OVERLAP without nesting ("AB" linked, "BC" de-linked,
    text "ABC"): the first match wins and the second is not seen.

    Returns None when there is nothing to scan for."""
    return _longest_first_pattern(set(linkable_targets or ()) | set(delinked_targets or ()))


def _validate_link_groups(primary_by_source_form, note_identity_by_source_form):
    """Fail-closed check of the `{member: primary}` map at its CONSUMPTION
    point, raising `RenderError("link_groups_invalid")`.

    `assemble.py` loads the sidecar through `canon_link_groups.load_link_groups`,
    which already validates canon membership -- but `render()` consumes
    `nodestream["link_groups"]`, and the standalone CLI (or a hand-edited
    persisted NodeStream) can hand it a map that never passed through that
    loader. An unvalidated primary reaches
    `note_identity_by_source_form.get(source_form, source_form)` inside
    `build_entity_index`, whose fallback would emit a wikilink to a note
    this render never writes -- a broken link in the delivered vault.

    Two conditions, both mechanical (never an identity judgement):
      - every key AND every value is a `source_form` this render actually
        emits an entity note for;
      - every value maps to ITSELF (`m[primary] == primary`), i.e. the
        primary is a member of its own group, which is what makes the flat
        projection a well-formed one-hop map rather than a chain.
    Non-string keys/values and a non-mapping argument are rejected here too,
    so a malformed persisted map surfaces as this named failure rather than
    a TypeError from a downstream lookup.

    Called BEFORE `_clean_vault_content` (see `render()`): a rejected input
    must never cost the operator the vault that is already on disk."""
    if primary_by_source_form is None:
        return {}
    if not isinstance(primary_by_source_form, dict):
        raise RenderError(
            "link_groups_invalid",
            "link_groups must be an object mapping each grouped source_form "
            f"to its group's primary source_form, got "
            f"{type(primary_by_source_form).__name__}",
        )
    for member, primary in primary_by_source_form.items():
        if not isinstance(member, str) or not isinstance(primary, str):
            raise RenderError(
                "link_groups_invalid",
                f"link_groups entry {member!r} -> {primary!r} is not a "
                "string-to-string mapping",
            )
        for role, value in (("member", member), ("primary", primary)):
            if value not in note_identity_by_source_form:
                raise RenderError(
                    "link_groups_invalid",
                    f"link_groups {role} {value!r} is not a canon entry this "
                    "render emits a note for -- a group may only name "
                    "canon.json entries{} keys, byte-exact",
                )
        if primary_by_source_form.get(primary) != primary:
            raise RenderError(
                "link_groups_invalid",
                f"link_groups primary {primary!r} is not a member of its own "
                "group (it must map to itself)",
            )
    return primary_by_source_form


def build_entity_index(entries, note_identity_by_source_form, collision_delink=False,
                       primary_by_source_form=None):
    """Returns (compiled_pattern, target_to_entity) for every
    canon entry carrying a non-degenerate `canonical_target_form` -- the
    substring that actually appears in TRANSLATED body text (obsidian.md's
    asymmetry: never `source_form`, which is the original-script identity,
    not what shows up in the rendered prose).

    `target_to_entity[target] = (note_identity, source_form)`:
      - `note_identity` is the SANITIZED, already collision-deduped,
        FOLDER-QUALIFIED note path (e.g. "People/Ivan" -- from
        `note_identity_by_source_form`, itself derived from
        `_resolve_entity_notes`'s own relpath resolution, the SAME
        resolution the entity-note-writing loop uses for the actual
        filename). This is what the wikilink TARGET must be: a raw
        `source_form` (e.g. containing "../x") would make a path-like link
        that never resolves to the emitted note (review round 1), and a
        bare (non-folder-qualified) stem is not guaranteed unique across
        different folders (review round 2) -- the link identity and the
        filename identity must be the exact same string, 1:1.
      - `source_form` is kept alongside, unchanged, for the OPTIONAL
        parenthetical original-script gloss (`name_display.
        parenthetical_originals`) -- a reading aid, not a link, which
        legitimately wants the raw original-script text rather than a
        sanitized filename stem.

    `canonical_target_form` is not guaranteed unique across entries.
    Default (`collision_delink=False`, unchanged from 1.7.0): the
    documented, fixed tiebreak -- prefer the entry with the shortest
    `source_form`, then break ties lexicographically by `source_form` --
    silently picks ONE winner and the rest simply never get an inline
    link. `render()` passes `collision_delink=_is_obsidian_target(profile)`
    (D3, #206/#207): `True` -- de-linking a >=2-owner target entirely --
    on EVERY real obsidian render, regardless of
    `_effective_mentions_enabled(profile)`/the `## Mentions` appendix
    `enabled` flag (it used to be gated on that predicate too; see the
    CHANGELOG for the decoupling); `False` -- the old tiebreak -- only on
    the standalone CLI's dormant-`obsidian`-under-`target:"custom"` path
    (`_is_obsidian_target` false), where D1/D3/D4 must all stay inert (see
    `_is_obsidian_target`'s own docstring). `validate_backlinks.py`'s gate
    also calls this function directly with both `True` and `False` to
    compute its own diagnostics, and the existing unit tests exercise the
    tiebreak directly, independent of render()'s call site.
    `collision_delink=True`: a target with >=2 owners is instead REMOVED
    from the map entirely -- no owner gets an inline link for that string,
    closing the silent "wrong entity's page" misattribution the tiebreak
    otherwise causes -- and the compiled pattern is built from the map
    AFTER that removal, so `_Linker`'s mandatory
    `target_to_entity[matched]` lookup can never `KeyError` on a delinked
    target. The invariant this establishes: on every real obsidian render,
    a `canonical_target_form` with >=2 owners is not inline-linked -- with
    exactly one exception, and it is never one this code INFERS: a
    `primary_by_source_form` group (#588) in which EVERY owner is a member
    and none is `sense_translated` is an identity call the operator already
    recorded, and that target links to the group's primary. Absent such a
    group the rule is absolute, because a misattributed inline link actively
    misleads (a reader clicks
    through to the WRONG entity's note), which is
    strictly worse than a missing one (recoverable via the `## Mentions`
    appendix or a manual search), so ambiguity always resolves toward the
    safer failure. Source-anchored `## Mentions` (D1) is the
    collapse-free, authoritative index regardless of this parameter --
    inline auto-linking is a reading affordance, never the sole source of
    truth. (The set of colliding targets is used only to drive that
    removal; the operator-facing collision diagnostic is surfaced
    independently by `validate_backlinks.py`'s own report, computed there
    from canon directly, so this function does not also return it.)
    `primary_by_source_form` (#588, default `None` = 1.29.0 behavior
    exactly) is the VALIDATED `{member: primary}` projection of the
    `canon_link_groups.json` sidecar. It is consulted ONLY inside the
    de-link branch (see `_link_decision`, which owns the rule): when every
    owner of a colliding target belongs to ONE established group and none
    is `sense_translated`, the shared target links to that group's primary
    note instead of being de-linked. Nothing else moves -- a single-owner
    target is untouched, and no string becomes newly matchable, because the
    alternation is still built from the same `canonical_target_form` values.
    Callers must pass a map already checked by `_validate_link_groups`;
    an unvalidated primary would reach the
    `note_identity_by_source_form.get(source_form, source_form)` fallback
    below and emit a link to a note that was never written.

    Degenerate values (empty or whitespace-only) are skipped entirely --
    otherwise a blank/whitespace target would become a matcher that wraps
    the first space (or nothing) in every block (review round 1 finding).

    `basis: "sense_translated"` entries (#138) never WIN the tiebreak and
    never get an inline auto-link -- deliberately, and unlike every other
    basis. A sense-rendering is an ordinary word BY CONSTRUCTION ("Hope",
    "Wolf"), so the alternation below would otherwise wikilink every
    incidental occurrence of that word in the prose, not just the entity's
    own mentions. `_Linker.link`'s #587 boundary guard does NOT cover this
    and never could: it refuses a match that is only part of a longer word,
    while a sense-rendering matches as a WHOLE word -- exclusion is the only
    thing that answers it. The entity note itself is
    still emitted and still carries its `basis` in frontmatter
    (`_render_entity_note` never branches on `basis`) -- only the body
    auto-linking is suppressed, erring toward the recoverable failure (a
    missing auto-link) over a false-link flood.

    A `sense_translated` entry STILL CONTRIBUTES to the collision tally,
    though (#240/#207-a): it is filtered out only at the tiebreak-selection
    step below, AFTER `owners_by_target` has already counted it as an
    owner. A sense_translated entry sharing a `canonical_target_form` with
    a narrative entry is therefore still a real >=2-owner collision under
    `collision_delink=True` -- both entries are de-linked, not just the
    narrative one silently winning as if the sense_translated owner never
    existed. If EVERY owner of a target turns out to be sense_translated,
    there is no eligible winner at all and the target is dropped from
    `by_target` entirely (never `min()` over an empty sequence).

    The compiled pattern alternates every distinct target string LONGEST
    FIRST, so a shorter name can never shadow a longer one that contains it
    as a substring -- see `_longest_first_pattern`, which owns that rule for
    this module and for the #588 diagnostic scan alike. That guarantee is
    about targets and ONLY about targets: it cannot help when the longer
    string is ordinary prose rather than another target, which is what
    `_Linker.link`'s per-match boundary guard (#587) is for.
    """
    owners_by_target = _owners_by_target(entries)

    by_target = {}
    for target, owners in owners_by_target.items():
        winner_source_form, _delinked = _link_decision(
            owners, collision_delink, primary_by_source_form
        )
        if winner_source_form is None:
            continue
        by_target[target] = winner_source_form

    target_to_entity = {
        target: (note_identity_by_source_form.get(source_form, source_form), source_form)
        for target, source_form in by_target.items()
    }
    # #206: this is a conservative verbatim same-surface affordance --
    # case-sensitive, no morphology, no identity call -- never the
    # authoritative occurrence index; that is the default-on
    # source-anchored `## Mentions` appendix (see obsidian.md).
    #
    # The pattern itself carries no boundary assertion, on purpose: `\b` would
    # be defined against each alternative's own edge characters, and a target
    # may begin or end with punctuation. The boundary is applied per MATCH, in
    # `_Linker.link`'s `_boundary_ok` (#587), which reads the adjacent
    # characters of the scanned text instead.
    #
    # `_longest_first_pattern` yields None for an empty map, which is exactly
    # the `(None, {})` an all-collision book must return.
    return _longest_first_pattern(target_to_entity), target_to_entity


class _Linker:
    """Bundles the compiled entity pattern with the BOOK-WIDE first-
    occurrence tracking `output.name_display.parenthetical_originals`
    needs, so callers don't have to thread multiple values through every
    render helper.

    Two distinct "first occurrence" scopes exist side by side here, on
    purpose (obsidian.md / assembly-and-output.md's `name_display`
    semantics differ from the wikilink rule's own scope):
      - the wikilink itself resets PER BLOCK (`seen_in_block`, local to one
        `link()` call) -- a name repeated three times in one block gets
        exactly one wikilink;
      - the parenthetical original-script gloss (only ever added when
        `parenthetical_originals: first_occurrence`) tracks the first
        occurrence ACROSS THE WHOLE BOOK (`self.global_seen`, persisting
        across every `link()` call this render makes) -- shown once, ever,
        the very first time a given canonical_target_form appears anywhere,
        never repeated even in a later block's own first occurrence.

    It also carries #588's cost diagnostic, for the same reason: this is the
    ONE place that sees the exact text the wikilink rule is applied to.
    `diagnostic_pattern` (the longest-first union of linkable AND de-linked
    targets, `build_diagnostic_pattern`) is scanned on the same
    NFC-normalized string, with the same merged protected spans, as the
    linking scan itself -- so `delinked_counts` is a count of occurrences the
    linker really saw and really left unlinked, not an after-the-fact guess
    from the rendered markdown. Scanning the FINAL note text instead would be
    both over- and under-inclusive: `_render_verse_block` links the gloss
    BEFORE wrapping it as `> *Literal: …*`, the segment title is duplicated
    into YAML frontmatter, and `_render_verse_inline`'s label is protected by
    POSITION (`extra_protected`), not by `_PROTECTED_SPAN_RE`.
    `links_emitted` is incremented at the actual insertion site below, so it
    counts links this render wrote -- never a `[[…]]` that was already in the
    translated source text and merely preserved as a protected span.
    """

    def __init__(self, pattern, target_to_entity, parenthetical_mode,
                 diagnostic_pattern=None, delinked_targets=None):
        self.pattern = pattern
        self.target_to_entity = target_to_entity  # target -> (note_identity, source_form)
        self.parenthetical_mode = parenthetical_mode
        self.global_seen = set()
        # #588 cost diagnostic -- purely observational: nothing below reads
        # these back, and no linking decision depends on them.
        self.diagnostic_pattern = diagnostic_pattern
        self.delinked_targets = frozenset(delinked_targets or ())
        self.delinked_counts = Counter()
        self.links_emitted = 0
        # #795 §7: how many emitted wikilinks had an editorial bracket pair
        # escaped around them. Counted on this object rather than returned,
        # because #795's OTHER emission site (the entity-markup pre-pass,
        # `_apply_entity_markup`) folds its own escapes into the same counter
        # -- the manifest reports one number for both sites, which is what
        # makes it a count of what the render actually did rather than of one
        # code path. Purely observational: nothing reads it back.
        self.brackets_escaped = 0

    def link(self, text, seen_in_block=None, extra_protected=None):
        # `extra_protected` (optional): a list of (start, end) char-offset spans
        # in the SAME coordinate space as `text` (the original, pre-NFC input),
        # each protected EXACTLY like a _PROTECTED_SPAN_RE match -- never matched
        # into, never counted as "seen", carried through the NFC reconstruction
        # verbatim. _render_block passes the absolute positions of each
        # inline-verse " (lit.: " label here so the linker won't wikilink into
        # that renderer-authored text. The label is already its final literal
        # form, so protection alone suffices -- there is nothing to restore
        # afterwards (see the LABEL PROTECTION comment block above).
        # ONE scan drives both linking and #588's cost count, over the UNION
        # of linkable and de-linked targets (`diagnostic_pattern`). Two
        # independent scans is what the first cut did, and it was wrong in a
        # way only a shared scan can fix -- see the loop below.
        #
        # The early return must NOT fire when only the LINKABLE pattern is
        # absent: `build_entity_index` returns `(None, {})` when nothing
        # survives collision de-linking, which is exactly the all-collision
        # book this metric exists for, and returning here would report zero
        # for a book where every single name is suppressed.
        scan_pattern = (
            self.diagnostic_pattern if self.diagnostic_pattern is not None else self.pattern
        )
        if not text or scan_pattern is None:
            return text
        # The NFC reassembly below is a MATCHING aid, never an output
        # transform on a no-link path: when nothing is linkable this call
        # must still return the caller's own bytes, exactly as the single
        # `self.pattern is None` early return used to.
        original_text = text

        # Protected spans (review round 1): never wrap a target that falls
        # inside an already-emitted [[...]], a [^N] footnote ref, or a raw
        # ⟦...⟧ sentinel -- computed FIRST, over the ORIGINAL un-normalized
        # text. The syntax characters these spans are delimited by ("[[",
        # "]]", "[^", digits, "⟦", "⟧") are not subject to NFC/NFD
        # decomposition, so their boundaries are identical whichever form
        # the text is in -- safe to locate before normalizing anything.
        # Merge _PROTECTED_SPAN_RE matches with the caller's position-tracked
        # spans into disjoint, ascending intervals. The NFC-reconstruction loop
        # below assumes ascending, NON-OVERLAPPING spans, so the two sets MUST be
        # coalesced first: they can and do overlap. _render_block tracks a verse
        # " (lit.: " label's absolute position, and that label can land NESTED
        # INSIDE a _PROTECTED_SPAN_RE span -- e.g. when the verse placeholder sat
        # between the brackets of a pre-existing [[...]] wikilink, the label ends
        # up wholly contained in that wikilink span. A bare sort would leave the
        # nested label as a second, overlapping interval, and the loop would
        # re-copy that already-emitted substring and regress its cursor,
        # duplicating/corrupting the output. `_merge_spans` fuses any overlapping
        # (or touching) spans into their union, which is exactly right here: an
        # already-emitted wikilink must be preserved byte-for-byte in full, label
        # included -- once it encloses the label there is nothing to treat
        # specially. This also handles any other overlap shape (partial, exact
        # duplicate, adjacent) robustly, without assuming a single scenario.
        orig_protected = _merge_spans(
            [(m.start(), m.end()) for m in _PROTECTED_SPAN_RE.finditer(text)]
            + list(extra_protected or [])
        )

        # NFC-normalize only the MATCHABLE (non-protected) portions -- the
        # compiled pattern's alternatives are themselves NFC
        # (build_entity_index), so an entity spelled in NFD form (decomposed
        # combining marks) would otherwise byte-mismatch the pattern and go
        # unmatched. A protected span's own bytes must NOT be touched: doing
        # so would silently rewrite e.g. a pre-existing literal [[...]]
        # wikilink's target bytes, desyncing it from the actual
        # (non-normalized) filename `_dedupe_path` wrote to disk -- a
        # protected span is supposed to survive byte-for-byte untouched.
        # Reassemble piece by piece, tracking each protected span's new
        # position in the rebuilt string (NFC-normalizing a preceding
        # non-protected piece can shift it, since NFD forms have more
        # codepoints than their NFC equivalent) so every offset used below
        # stays aligned to this same reassembled string.
        pieces = []
        protected = []
        last = 0
        offset = 0
        for p_start, p_end in orig_protected:
            if p_start > last:
                normalized = unicodedata.normalize("NFC", text[last:p_start])
                pieces.append(normalized)
                offset += len(normalized)
            span = text[p_start:p_end]
            pieces.append(span)
            protected.append((offset, offset + len(span)))
            offset += len(span)
            last = p_end
        if last < len(text):
            pieces.append(unicodedata.normalize("NFC", text[last:]))
        text = "".join(pieces)

        def _is_protected(start, end):
            return any(start < p_end and end > p_start for p_start, p_end in protected)

        def _boundary_ok(start, end):
            # #587: refuse a match that is only PART of a longer written run --
            # "Teplik" inside the Yiddish demonym "Tepliker", which shipped into
            # a delivered book as "[[...|Teplik]]er", the target wrapped and the
            # "er" left dangling outside the link. `targets_sorted` being
            # longest-first stops a shorter TARGET shadowing a longer one; it
            # cannot help when the longer string is ordinary prose.
            #
            # The test is `str.isalnum()` on the ADJACENT CHARACTER -- the
            # FIRST leg of `sanitize_filename_component`'s filename allow-list
            # (since #586 that allow-list has two further legs, the combining-
            # mark categories and a curated punctuation set; this boundary test
            # deliberately keeps neither, for the reason in the next sentence
            # and the one about marks below) -- and it is deliberately
            # alphanumeric rather than non-space:
            # an apostrophe, quote, comma or period after a name is the common,
            # correct case ("[[...|Reb Noson]]'s"), and only a letter or digit
            # means the target is a fragment of a longer word. It also needs no
            # per-script branch: LETTERS are `isalnum()` in Hebrew and Cyrillic
            # alike, so an uncased script behaves like a cased one. Combining
            # MARKS are not (a Devanagari matra, a Hebrew point), so a word
            # continued by one is still cut -- the known gap, filed as #590.
            #
            # Deliberately NOT `\b`/`\w`. `\b`'s assertion is defined relative to
            # the PATTERN's own edge characters, so for a canonical_target_form
            # beginning or ending with punctuation ("R.", an apostrophised form)
            # it is wrong in BOTH directions: `re.escape("R.") + r"\b"` matches
            # "R.Smith", which this rule refuses, and does NOT match "R. Noson",
            # which this rule links -- after "R." the `\b` position has a
            # non-word character on each side, so it never fires. Looking only
            # at the neighbouring character means a target's own edges can never
            # change what the guard means.
            return not (
                (start > 0 and text[start - 1].isalnum())
                or (end < len(text) and text[end].isalnum())
            )

        # `seen_in_block` is normally SHARED across every `link()` call made
        # while rendering one block (#105c) -- passed down from
        # `_render_block` through the verse renderers, so a name already
        # linked inside a verse (or in its gloss) doesn't link again in the
        # surrounding prose. Callers with no natural "one block" scope of
        # their own (e.g. the footnote-definition line) omit the argument
        # and get an independent fresh set, correct for their own use --
        # each footnote definition is its own block for this rule.
        if seen_in_block is None:
            seen_in_block = set()
        out = []
        last = 0
        for m in scan_pattern.finditer(text):
            if _is_protected(m.start(), m.end()):
                continue  # inside a protected span -- leave untouched, don't count as "seen"
            if not _boundary_ok(m.start(), m.end()):
                # #587. Refused BEFORE `seen_in_block` (and before
                # `global_seen`), so a fragment match never spends the block's
                # single first-occurrence slot -- a properly bounded occurrence
                # later in the same block still gets its wikilink, and its
                # first-occurrence parenthetical.
                #
                # `finditer` is non-overlapping, so the refused span is still
                # consumed: a DIFFERENT, shorter target starting inside it gets
                # no turn here. That is deliberate. Re-scanning from one
                # character on would recover the odd legitimate short mention,
                # but it would also link a different entity inside a full name
                # -- targets "Ann Marie" and "Marie" over the prose "JoAnn
                # Marie" yield "JoAnn [[...|Marie]]" under a rescan and are left
                # untouched here. A missing link is recoverable via the
                # source-anchored `## Mentions` appendix; a wrong one, sitting
                # in the delivered book, is not.
                continue
            target = m.group(0)
            if target in self.delinked_targets:
                # #588 COST, and the reason this is ONE scan. A de-linked
                # target CONSUMES its span and emits nothing. `finditer` is
                # non-overlapping, so a shorter SURVIVING target starting
                # inside it gets no turn -- which is the whole point: with
                # two independent scans, canon holding a colliding
                # "John Smith" and a single-owner "John" rendered
                # "[[…|John]] Smith", a link landing on the wrong man inside
                # the very span de-linking had just suppressed, while the
                # cost report simultaneously called that occurrence unlinked.
                # A false link is worse than a missing one (#207) -- and a
                # metric that contradicts the vault it describes is worse
                # than either.
                #
                # Counted BEFORE `seen_in_block` is consulted: the question
                # is how many occurrences of a de-linked name a reader meets
                # unlinked, so every occurrence counts, not one per block.
                # Counted AFTER `_boundary_ok`, because what this metric
                # means is occurrences that carry no link BECAUSE OF THE
                # COLLISION: a match the #587 boundary refuses ("Teplik"
                # inside "Tepliker") would carry no link with a single owner
                # either, so charging it would inflate the number with
                # occurrences a link group could never recover.
                self.delinked_counts[target] += 1
                continue
            if self.pattern is None or target not in self.target_to_entity:
                # Nothing linkable at all this render, or a scan-pattern
                # alternative that is in neither map (unreachable while the
                # union is built from exactly these two sets -- defensive,
                # so a future caller cannot turn a mismatch into a KeyError).
                continue
            if target in seen_in_block:
                continue
            seen_in_block.add(target)
            note_identity, source_form = self.target_to_entity[target]
            piece = f"[[{note_identity}|{target}]]"
            if self.parenthetical_mode == "first_occurrence" and target not in self.global_seen:
                piece += f" ({source_form})"
            self.global_seen.add(target)
            # #795 §7, emission site 1 of 2 (the other is
            # `_apply_entity_markup`). An editorial bracket the translator
            # put around the name collides with the wikilink placed inside
            # it: "[" + "[[People/Reb Noson|Reb Noson]]" + "]" reads to
            # Obsidian as the target "[People/Reb Noson" plus a stray "]".
            # Escaping the OUTER pair keeps what the reader sees ("[…]")
            # and gives the parser an unambiguous link. The parenthetical
            # gloss is inside the escaped pair on purpose -- the outer "]"
            # sits after it in the source text, so the pair being escaped is
            # the one that actually encloses the whole emitted piece.
            chunks, last, escaped = _editorial_bracket_emit(
                text, last, m.start(), m.end(), piece
            )
            out.extend(chunks)
            self.brackets_escaped += escaped
            self.links_emitted += 1  # #588: counted where the link is actually inserted
        out.append(text[last:])
        if self.pattern is None:
            # Nothing was linkable this render, so nothing was rewritten --
            # the counting pass above still ran. Return the caller's OWN
            # bytes rather than the NFC reassembly, exactly as the single
            # `self.pattern is None` early return used to (see
            # `original_text`).
            return original_text
        return "".join(out)


# ---------------------------------------------------------------------------
# Declared entity markup (#795) -- the `output.entity_markup` knob's RENDER
# half. `assemble.py` owns the SCAN half: in `index` mode it has already
# replaced each declared element in every text-bearing string with the
# three-part sequence `⟦ENT_{n}⟧{payload}⟦/ENT_{n}⟧` and recorded
# `nodestream["entity_markup"] = {"spans": {"<n>": {"tag","payload","ref"}}}`.
# Nothing below re-parses the operator's `<person …>` grammar -- that grammar
# lives in assemble.py alone, and this file consumes only the sentinel form.
# Spec: references/assembly-and-output.md (the NodeStream contract) and
# references/output-target-adapters/obsidian.md (how a markup note is named,
# foldered and composed with canon).
# ---------------------------------------------------------------------------

# The sentinel pair, and the token form on its own. `⟦[^⟧]+⟧` already makes
# both a `_PROTECTED_SPAN_RE` span for free, which is why a sentinel (rather
# than a character offset) is what assemble.py records: `_render_block`
# splices verse/fnref substitutions into the text before linking and
# `_heading_plain_text` rebuilds title text from scratch -- an offset survives
# neither. DOTALL because a payload may not contain CR/LF (assemble.py refuses
# those) but the surrounding text between two different spans may.
_ENT_SPAN_RE = re.compile(r"⟦ENT_(\d+)⟧(.*?)⟦/ENT_\1⟧", re.DOTALL)
# BOTH forms. A lone closer reaches a reader exactly as much as a lone opener,
# so every residual check below looks for either.
_ENT_TOKEN_RE = re.compile(r"⟦/?ENT_\d+⟧")

# The two shapes `_heading_plain_text(flatten_wikilinks=True)` reduces, plus
# the escaped-bracket pair §7 may have wrapped one in.
_WIKILINK_ALIASED_RE = re.compile(r"\[\[([^\[\]|]*)\|([^\[\]]*)\]\]")
_WIKILINK_BARE_RE = re.compile(r"\[\[([^\[\]|]*)\]\]")
# Cheap "is there anything here to flatten at all" probe, so the plain-heading
# fast path in `_heading_plain_text` still returns byte-identical text for
# every heading that carries neither a wikilink nor an escaped bracket.
_HEADING_FLATTEN_PROBE_RE = re.compile(r"\[\[|\\[\[\]]")


def _entity_markup_mode(profile):
    """`"off"` / `"strip"` / `"index"` / `"index_unsupported_target"` for
    this profile's `output.entity_markup` block (#795 §4).

    INDEPENDENT RECOMPUTATION of `assemble.py`'s function of the same name,
    from the SAME two profile fields, never imported from it -- the same
    discipline the three `mentions_section` predicates already follow.
    Change one and change the other.

    The two do NOT have identical text, and cannot: assemble.py's copy
    delegates to its own `_entity_markup_config` runtime validator and
    RAISES `AssembleError` on the unsupported-target row, and this file has
    no such failure vocabulary (a renderer must stay inert there, not
    fatal). So that row is a fourth RETURN VALUE here. It also inlines the
    `output.target == "obsidian"` test rather than calling
    `_is_obsidian_target`, which exists only in this file.

    A SECOND, OPEN-ENDED divergence, deliberate and confined to profiles
    assemble.py has already refused: this copy does NOT validate the block. It
    reads exactly two fields and never looks at `tags`, so anything
    `_entity_markup_config` rejects is a hard `entity_markup_config_invalid`
    there while resolving here to whatever those two reads produce -- `"off"`
    for a non-mapping block, and for a structurally-bad-but-mapping one (an
    empty `tags`, a bare string `tags`, an unknown key) whatever `index_from`
    and `output.target` say, `"index"` included. Do not read the pair as
    "two named exceptions": the rule is that assemble.py REFUSES wherever
    this copy resolves, and the set of such profiles is whatever its validator
    rejects.

    `assemble.py` runs first on every real path -- it is what writes the
    spans this adapter reads -- so this function's own answer is only ever
    reached standalone, and staying inert on a block it cannot parse is the
    same fail-quiet posture as the `"off"` branch itself. The fail-CLOSED
    half lives downstream, in the preflight that proves the span table and
    the text agree, and are well-typed, before the vault is cleaned.

    `index_unsupported_target` is `index_from: markup` asked for on a target
    that consumes no spans. `assemble.py` REFUSES there
    (`entity_markup_index_unsupported_target`), because silently degrading to
    `strip` would hand an operator an index they asked for and did not get;
    this adapter treats it as not-index and stays inert, which is the same
    posture D1/D3/D4 take on the standalone CLI's dormant
    `obsidian`-under-`target: "custom"` path."""
    output_cfg = (profile or {}).get("output") or {}
    cfg = output_cfg.get("entity_markup")
    if not isinstance(cfg, dict):
        return "off"
    if cfg.get("index_from") != "markup":
        return "strip"
    if output_cfg.get("target") != "obsidian":
        return "index_unsupported_target"
    return "index"


def _entity_markup_spans(nodestream):
    """The RAW `{"<n>": {"tag","payload","ref"}}` table off the NodeStream,
    or `{}` when the book carries none.

    Deliberately UNFILTERED. An earlier version dropped every non-mapping
    record here and claimed that turned a malformed record into a named
    refusal downstream -- true only for a record some `⟦ENT_n⟧` pair actually
    cites, because the preflight's inverse check (condition 4) can only see
    what this function returned. An UNUSED garbage record was therefore
    dropped in silence. Everything malformed now reaches
    `_entity_markup_preflight`'s condition 0, which is the one place that
    decides what a valid record is.

    Only non-string KEYS are dropped, and they cannot be reached anyway: JSON
    object keys are strings by construction, and the pair regex captures
    digits."""
    block = nodestream.get("entity_markup")
    if not isinstance(block, dict):
        return {}
    spans = block.get("spans")
    if not isinstance(spans, dict):
        return {}
    return {key: span for key, span in spans.items() if isinstance(key, str)}


def _entity_markup_identity(span):
    """`(tag, label)` -- #795 §6.2's ONE identity rule.

    `label = NFC(ref or payload)`; the NFC normalization is what makes a
    label match `build_entity_index`'s own NFC-normalized target keys, and
    what keeps two spellings of one decomposed name from minting two notes.

    The TAG is part of the identity, not merely the folder: `<person>Jordan`
    and `<place>Jordan` stay two notes. Collapsing them would be the
    entity-merge judgement #795's non-goals exclude, and one note cannot
    truthfully carry two categories anyway."""
    tag = span.get("tag") or ""
    ref = span.get("ref")
    label = ref if isinstance(ref, str) and ref else (span.get("payload") or "")
    return tag, unicodedata.normalize("NFC", label)


def _entity_markup_string_slots(nodestream):
    """Yields `(container, key)` for EVERY string the resolution pre-pass
    rewrites -- node `text`, each verse `content.rendered`/`literal_gloss`,
    each footnote `text`.

    ONE enumeration, used by both the preflight (§6.4) and the rewrite
    (§6.3), so the two can never disagree about which strings are rewritable.
    That agreement is the whole basis of the preflight's completeness claim:
    it refuses a token found ANYWHERE in the NodeStream that is not in one of
    these slots, which is only sound while "these slots" means the same thing
    to both.

    Walk order is the RENDERER's own reconstructed reading order -- group by
    `seg`, follow `book.seg_order` with unlisted segs appended sorted, sort
    each segment's nodes by `order_index`, footnotes last by `n` -- NOT the
    raw `nodes` list order. `parenthetical_originals: first_occurrence`
    appends its gloss to the FIRST span the pre-pass resolves, so a
    hand-authored or post-processed NodeStream must order the same way the
    vault does."""
    nodes_by_seg = {}
    for node in nodestream.get("nodes") or []:
        if isinstance(node, dict):
            nodes_by_seg.setdefault(node["seg"], []).append(node)
    seg_order = (nodestream.get("book") or {}).get("seg_order") or []
    full_order = list(seg_order) + sorted(set(nodes_by_seg) - set(seg_order))
    for seg in full_order:
        for node in sorted(nodes_by_seg.get(seg, []), key=lambda n: n["order_index"]):
            if isinstance(node.get("text"), str):
                yield node, "text"
            for verse in node.get("verses") or []:
                content = verse.get("content") if isinstance(verse, dict) else None
                if not isinstance(content, dict):
                    continue
                for field in ("rendered", "literal_gloss"):
                    if isinstance(content.get(field), str):
                        yield content, field
    footnotes = [fn for fn in (nodestream.get("footnotes") or []) if isinstance(fn, dict)]
    for fn in sorted(footnotes, key=lambda f: f.get("n") or 0):
        if isinstance(fn.get("text"), str):
            yield fn, "text"


def _walk_json_strings(value):
    """Every string anywhere in a parsed-JSON value, dict KEYS included.
    The preflight's totality rests on this being the WHOLE value: a token
    hiding in a field the pre-pass does not rewrite (a node's `raw_type`, a
    verse `placeholder`) is exactly the case a scan of the rewritten strings
    alone would miss, and it would ship that sentinel to a reader."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            if isinstance(key, str):
                yield key
            yield from _walk_json_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_json_strings(item)


# The characters a span record's `tag`/`payload`/`ref` may never contain.
# BYTE-FOR-BYTE the set `assemble.py`'s `_ENTITY_UNSAFE_CHARS` refuses at
# recording time, plus the sentinel delimiters: `⟦…⟧` is a fixed machine
# shape, and one inside a payload (`⟦FNREF_1⟧`, a verse placeholder) would be
# lifted into a note heading or collide with the wikilink closer. Duplicated
# per this repo's no-shared-util convention; change both copies together.
_ENTITY_UNSAFE_IN_RECORD = ("[", "]", "|", "\r", "\n", "⟦", "⟧")


def _entity_markup_preflight(nodestream, spans, canon):
    """#795 §6.4. Called BEFORE `out_dir.mkdir`/`_clean_vault_content`, for
    the same reason `_validate_link_groups` is: the clean empties the
    existing vault, so a failure discovered while WRITING would leave the
    operator with neither the old vault nor a complete new one.

    SIX conditions, all of them fail-closed. Their shape is deliberate: the
    span table and the text that cites it must be proved MUTUALLY CONSISTENT
    AND WELL-TYPED here, because every consumer downstream of the clean --
    `_entity_markup_identity`, `_canon_composition`, `_markup_note_records`,
    the rewriter -- reads those fields without re-checking them, and the
    clean is irreversible. Three separate review rounds each found one more
    malformed shape that reached `_clean_vault_content`; conditions 0 and 3b
    are the answer to that class rather than to the last instance of it.

      0. every span record this render will USE is well-typed: `tag` and
         `payload` non-empty strings, `ref` a non-empty string when present.
         Nothing downstream re-checks, and a non-string payload reaches
         `unicodedata.normalize` as a TypeError -- after the clean;
      1. every `⟦ENT_n⟧`/`⟦/ENT_n⟧` token in the whole NodeStream sits in a
         slot the pre-pass actually rewrites (counted, not located -- a
         count over `_walk_json_strings` compared against the same count over
         `_entity_markup_string_slots`, which is total by construction);
      2. within those slots every token belongs to a well-formed
         `⟦ENT_n⟧…⟦/ENT_n⟧` pair;
      3. every such pair's id has a span record (3a) AND the text between the
         tokens equals that record's own `payload` (3b). Assembly emits the
         two from one string, so disagreement means the table and the text
         come from different runs -- and the rewriter takes the DISPLAYED
         alias from the text while taking the NOTE from the record, so a
         stale table writes `[[People/Pyotr|Ivan]]`: a name linked to
         somebody else, exit 0, counts balanced;
      4. the INVERSE of 3a -- every span record is used by exactly one
         pair;
      5. and no reserved token sits anywhere in `canon.json`, the OTHER
         source of text this render writes to disk.
    Anything else raises `RenderError("entity_markup_unresolvable")`.

    Condition 5 is condition 4's sibling, closed in the same place for the
    same reason. An entity note's frontmatter and heading are built straight
    from the canon entry and never pass through the pre-pass, so a reserved
    token sitting there is caught only by `_reject_residual_entity_tokens`
    at WRITE time -- after the clean. Checking canon HERE is what keeps that
    post-condition a pure resolver assertion rather than a second, later,
    vault-destroying input gate.

    Condition 4 is not symmetry for its own sake: render()'s coverage
    identity (`replaced == links == len(spans)`, §6.5) is checked AFTER
    `_clean_vault_content` has already emptied the managed vault, so a
    NodeStream carrying records 1 and 2 but only pair 1 -- or pair 1 twice --
    used to pass this preflight, lose the operator their existing vault, and
    only then fail. Checking it HERE is what makes the CHANGELOG's "a failure
    leaves the existing vault untouched" true; the post-render comparison
    stays as a cheap postcondition on the resolver itself."""
    # Condition 0 -- see this function's own docstring. Checked over the RAW
    # table (`_entity_markup_spans` filters nothing) and over the WHOLE of it
    # rather than only the ids the text cites: condition 4 already requires
    # every record to be used, so an UNUSED garbage record must be refused
    # here or it is refused nowhere.
    for key in sorted(spans, key=lambda k: (len(k), k)):
        record = spans[key]
        if not isinstance(record, dict):
            raise RenderError(
                "entity_markup_unresolvable",
                f"nodestream.entity_markup.spans[{key!r}] is {record!r}, not "
                "a mapping of tag/payload/ref. Re-run assemble.py rather than "
                "hand-editing nodestream.json.",
            )
        for field, required in (("tag", True), ("payload", True), ("ref", False)):
            if not required and field not in record:
                continue
            value = record.get(field)
            if not isinstance(value, str) or not value:
                raise RenderError(
                    "entity_markup_unresolvable",
                    f"nodestream.entity_markup.spans[{key!r}].{field} is "
                    f"{value!r}, not a non-empty string. Every field of a span "
                    "record is interpolated into a note name, a wikilink or a "
                    "heading without being re-checked. Re-run assemble.py "
                    "rather than hand-editing nodestream.json.",
                )
            # The producer's OWN constraint, re-applied to the persisted
            # artifact. `assemble.py` refuses these characters at the moment
            # the span is recorded (`_entity_markup_check_span_text`) precisely
            # because this adapter interpolates the value into a wikilink alias
            # and a note name; a hand-edited `Iv|an` renders as the malformed
            # `[[People/Iv_an|Iv|an]]`. Duplicated rather than imported: this
            # repo keeps cross-cutting helpers duplicated per script, and the
            # two copies must be changed together.
            bad = next((c for c in _ENTITY_UNSAFE_IN_RECORD if c in value), None)
            if bad is not None:
                raise RenderError(
                    "entity_markup_unresolvable",
                    f"nodestream.entity_markup.spans[{key!r}].{field} contains "
                    f"{bad!r}, which cannot survive interpolation into a "
                    "wikilink alias or a note name. assemble.py refuses it when "
                    "the span is recorded; this NodeStream was hand-edited or "
                    "written by a different version. Re-run assemble.py.",
                )

    slots = list(_entity_markup_string_slots(nodestream))
    slot_tokens = sum(len(_ENT_TOKEN_RE.findall(container[key])) for container, key in slots)
    all_tokens = sum(len(_ENT_TOKEN_RE.findall(s)) for s in _walk_json_strings(nodestream))
    if all_tokens != slot_tokens:
        raise RenderError(
            "entity_markup_unresolvable",
            f"the NodeStream carries {all_tokens} entity-markup sentinel "
            f"token(s) but only {slot_tokens} of them sit in a field this "
            "adapter rewrites (node text, verse rendered/literal_gloss, "
            "footnote text) -- the rest would be delivered verbatim to a "
            "reader. Re-run assemble.py rather than hand-editing "
            "nodestream.json.",
        )
    pair_uses = Counter()
    for container, key in slots:
        text = container[key]
        if "ENT_" not in text:
            continue
        for match in _ENT_SPAN_RE.finditer(text):
            pair_uses[match.group(1)] += 1
            if match.group(1) not in spans:
                raise RenderError(
                    "entity_markup_unresolvable",
                    f"entity-markup sentinel {match.group(0)!r} has no record "
                    f"in nodestream.entity_markup.spans -- this adapter cannot "
                    f"resolve it to a note, and it would ship verbatim. "
                    f"Offending text: {text[:200]!r}",
                )
            # Condition 3b -- see this function's own docstring.
            recorded = spans[match.group(1)]["payload"]
            if match.group(2) != recorded:
                raise RenderError(
                    "entity_markup_unresolvable",
                    f"entity-markup span {match.group(1)!r} reads "
                    f"{match.group(2)!r} in the text but "
                    f"nodestream.entity_markup.spans records {recorded!r}. "
                    "Assembly writes both from one string, so this NodeStream's "
                    "text and span table are from different runs -- the printed "
                    "name would be linked to whichever entity the stale record "
                    "names. Re-run assemble.py rather than hand-editing "
                    "nodestream.json.",
                )
        residue = _ENT_SPAN_RE.sub("", text)
        stray = _ENT_TOKEN_RE.search(residue)
        if stray:
            raise RenderError(
                "entity_markup_unresolvable",
                f"unpaired entity-markup sentinel {stray.group(0)!r} -- every "
                "token must be part of a well-formed ⟦ENT_n⟧…⟦/ENT_n⟧ pair. "
                f"Offending text: {text[:200]!r}",
            )

    # Condition 4 -- the inverse of 3, checked here rather than after the
    # vault has already been cleaned (see this function's own docstring).
    unused = sorted(set(spans) - set(pair_uses), key=lambda k: (len(k), k))
    if unused:
        raise RenderError(
            "entity_markup_unresolvable",
            f"nodestream.entity_markup.spans records {len(spans)} span(s) but "
            f"{len(unused)} of them appear nowhere in the text this adapter "
            f"rewrites (first: {unused[:5]}) -- the recorded index would claim "
            "coverage this render cannot deliver. Re-run assemble.py rather "
            "than hand-editing nodestream.json.",
        )

    # Condition 5 -- see this function's own docstring. `canon` is walked
    # whole rather than field by field: every string in it is a candidate
    # for an entity note's aliases, name, category or ref.
    canon_stray = next(
        (
            match.group(0)
            for text in _walk_json_strings(canon)
            for match in [_ENT_TOKEN_RE.search(text)]
            if match
        ),
        None,
    )
    if canon_stray is not None:
        raise RenderError(
            "entity_markup_unresolvable",
            f"canon.json contains the reserved entity-markup sentinel "
            f"{canon_stray!r}. Entity notes are built straight from canon "
            "and never pass through this adapter's resolution pre-pass, so "
            "that token would ship verbatim to a reader. Remove it from "
            "canon.json.",
        )
    repeated = sorted((k for k, n in pair_uses.items() if n > 1), key=lambda k: (len(k), k))
    if repeated:
        raise RenderError(
            "entity_markup_unresolvable",
            f"entity-markup span id(s) {repeated[:5]} appear in more than one "
            "⟦ENT_n⟧…⟦/ENT_n⟧ pair -- each id names ONE marked run, so a reused "
            "id means the NodeStream no longer describes the book assemble.py "
            "built. Re-run assemble.py rather than hand-editing nodestream.json.",
        )


def _canon_composition(spans, target_to_entity, entries):
    """`{(tag, label): (note_identity, source_form)}` for every span identity
    that COMPOSES with canon -- links canon's own note and mints nothing --
    computed ONCE so `_markup_note_records` and `_apply_entity_markup` can
    never disagree about which identities canon owns.

    An identity composes when its label is a linkable canon target AND that
    canon entry does not CONTRADICT the declared tag. The category test is
    the whole point: composing on the label alone made `<person>Jordan</person>`
    link a canon note for a PLACE named Jordan -- one canon entry silently
    absorbing a second, differently-categorized entity the operator had
    explicitly marked, which is both the entity-merge judgement this plugin
    never makes and a silent index shortfall (no person note, coverage counts
    still balanced, exit 0).

    A canon entry with NO category composes with any tag, and that is
    deliberate rather than lax: the shipped glossary pass never asks for
    `category`, so on a typical project the field is empty everywhere, and
    requiring a positive match would stop composition entirely -- every marked
    name would mint a duplicate beside its own canon note, which is exactly
    the "two indexes competing" this feature exists to avoid. Canon speaks
    only where it has actually spoken."""
    composed = {}
    for span in spans.values():
        tag, label = _entity_markup_identity(span)
        if (tag, label) in composed:
            continue
        resolved = target_to_entity.get(label)
        if resolved is None:
            continue
        category = (entries.get(resolved[1]) or {}).get("category")
        if isinstance(category, str) and category.strip() and category.strip() != tag:
            continue
        composed[(tag, label)] = resolved
    return composed


def _markup_note_records(spans, canon_composition):
    """`{(tag, label): {"aliases": [...], "ref": label or None}}` for every
    span identity that needs a note of its OWN -- i.e. every one absent from
    `_canon_composition` (those link the canon note and mint nothing, so canon
    stays the authority wherever it has actually spoken).

    `aliases` is every DISTINCT PRINTED payload seen for this identity,
    sorted and deduped: two spans `<person ref="B">Reb Noson</person>` and
    `<person ref="B">R. Noson</person>` are one man with two printed forms.
    `ref` is present only when the label came from a `ref` attribute -- in
    which case it IS the label, since the identity is NFC-keyed and the raw
    pre-normalization spelling is not what any consumer resolves against.
    Nothing else goes in the frontmatter: `basis`, `confidence` and `source`
    are canon fields, and inventing them here would be a fabrication."""
    records = {}
    for span in spans.values():
        tag, label = _entity_markup_identity(span)
        if (tag, label) in canon_composition:
            continue
        record = records.setdefault((tag, label), {"aliases": set(), "ref": None})
        record["aliases"].add(span.get("payload") or "")
        ref = span.get("ref")
        if isinstance(ref, str) and ref:
            record["ref"] = label
    return {
        identity: {"aliases": sorted(record["aliases"]), "ref": record["ref"]}
        for identity, record in records.items()
    }


def _resolve_markup_notes(identities, folders_map, used_paths):
    """`{(tag, label): relpath}`, resolved through the SAME `used_paths` set
    the canon notes were already resolved through (#795 §6.1).

    Canon resolves FIRST and this runs second, on purpose: every canon
    relpath then stays byte-identical to what it was before this feature
    existed, so `validate_backlinks.py`'s own INDEPENDENT re-derivation
    (`_resolve_entity_notes(entries, folders_map)`, two arguments, its own
    fresh set) still matches the vault. A markup note can therefore never
    take or overwrite a canon note's path -- only ever get deduped away from
    one.

    The fallback stem is prefixed `markup-` rather than `entity-` so a
    sanitized-to-nothing markup label and a sanitized-to-nothing canon
    `source_form` do not silently resolve to the same name; `_dedupe_path`
    would separate them anyway, but the reader can see which is which."""
    relpath_by_identity = {}
    for identity in sorted(identities):
        tag, label = identity
        folder = _resolve_folder(tag, folders_map)
        stem = sanitize_filename_component(
            label, _stable_fallback_name(f"{tag}/{label}", "markup")
        )
        relpath_by_identity[identity] = _dedupe_path(f"{folder}/{stem}.md", used_paths)
    return relpath_by_identity


def _backslash_run_is_even(text, index):
    """Is the backslash run ending immediately before `text[index]` of EVEN
    length -- i.e. is `text[index]` a LITERAL character rather than an
    escaped one?

    Parity, never presence: in `\\\\[` the two backslashes are themselves an
    escaped backslash, so the `[` after them is literal and does collide with
    a wikilink put inside it. Testing only `text[index - 1] == "\\\\"` reports
    every even run as escaped, which is the original bug with one more
    backslash in front of it."""
    run = 0
    i = index - 1
    while i >= 0 and text[i] == "\\":
        run += 1
        i -= 1
    return run % 2 == 0


def _editorial_bracket_sides(text, start, end):
    """#795 §7: `(escape_open, escape_close)` for an emitted link that
    replaces `text[start:end]`. An editorial bracket the translator put
    around a name collides with the wikilink emitted inside it --
    `[[[People/X|X]]` reads to Obsidian as the target `[People/X` plus a
    stray `]`.

    The PAIR is what makes it an editorial bracket, so both sides must be
    present -- either literal or already escaped -- or nothing is touched and
    an unmatched bracket stays the literal source text the renderer's
    unresolved-bracket contract promises. But the two sides are decided
    SEPARATELY, because they can disagree: in `[Name\\]` the operator escaped
    only the closer, and requiring both to be literal left the opener bare and
    the link target broken. Escaping only what is literal also means an
    operator's own escape is never doubled -- a reader must never be shown a
    backslash.

    PARITY ON BOTH SIDES, and the third return value is what makes the
    closing side possible. The opening side's backslash run sits BEFORE its
    `[`, so it stays in the prefix untouched and the caller consumes one
    character. The closing side's run sits BETWEEN the span and its `]`, so
    the caller has to consume the run TOO and re-emit it -- `\\\\]` is an
    escaped backslash followed by a LITERAL `]`, which must become
    `\\\\` + `\\]`, not be mistaken for an escaped closer and skipped.
    Returns `(escape_open, escape_close, close_run_len)`."""
    if start < 1 or text[start - 1] != "[":
        return False, False, 0
    # `\[` needs no branch of its own: the run before the `[` is odd there,
    # so parity already reports it escaped.
    open_literal = _backslash_run_is_even(text, start - 1)
    run = 0
    while end + run < len(text) and text[end + run] == "\\":
        run += 1
    if end + run >= len(text) or text[end + run] != "]":
        return False, False, 0
    return open_literal, run % 2 == 0, run


def _editorial_bracket_emit(text, last, start, end, piece):
    """The SPLICE half of §7, shared by both emission sites: `(chunks,
    new_last, escaped)` for replacing `text[start:end]` with `piece` while
    honouring an editorial bracket pair around it.

    `chunks` is appended to the caller's output list and `escaped` (0 or 1)
    folded into the caller's own `brackets_escaped` counter -- the two sites
    differ only in how they FIND the span and which counter they own, and
    `_editorial_bracket_sides` alone left the consume-and-re-emit arithmetic
    written twice, where a fix applied to one copy stays invisible until a
    book hits that site's own mixed case."""
    open_lit, close_lit, close_run = _editorial_bracket_sides(text, start, end)
    if not (open_lit or close_lit):
        return [text[last:start], piece], end, 0
    # Consume the LITERAL bracket on each side and re-emit it escaped; an
    # already-escaped one is left exactly as the operator wrote it (see
    # `_editorial_bracket_sides`). On the closing side the backslash run
    # between the span and its `]` is consumed with it and re-emitted verbatim.
    chunks = [
        text[last:start - (1 if open_lit else 0)],
        f"\\[{piece}" if open_lit else piece,
    ]
    if close_lit:
        chunks.append(text[end:end + close_run] + "\\]")
    return chunks, end + (close_run + 1 if close_lit else 0), 1


def _apply_entity_markup(nodestream, spans, canon_composition, markup_note_identity, linker):
    """#795 §6.3 -- THE single resolution site. Rewrites every string
    `_entity_markup_string_slots` names, in place, replacing each
    `⟦ENT_n⟧payload⟦/ENT_n⟧` with a wikilink. Returns
    `{"replaced": int, "links": int}` for render()'s coverage check.

    ONE pre-pass rather than a resolution point inside each renderer:
    `_render_block`, `_render_verse_block`, `_render_verse_inline`,
    `_render_segment_note` and `_heading_plain_text` are all untouched by
    resolution, so there is no site to forget. `_render_verse_inline` in
    particular splices its output into the composed block AFTER any
    per-function resolution point would have run, which is exactly how a
    per-site design leaks.

    EVERY span emits a wikilink -- every occurrence, every node kind,
    headings included. No marked span is ever left as bare text, and that is
    the single property the whole design rests on: an emitted `[[…]]` is a
    `_PROTECTED_SPAN_RE` span, so the canon scan cannot see inside it and the
    two mechanisms share no state at all. Leaving even one class of marked
    span bare would silently re-open three separate defects, because the
    canon scan matches over the RECOMPOSED text rather than entity
    boundaries: longest-first matching (`_longest_first_pattern`) would link
    a marked `John` into a wikilink for a canon `John Smith`; `seen_in_block`
    would swallow the second and later marked occurrences in a block; and
    `_boundary_ok` would refuse `<person>Ann</person>ette` outright. Each
    produces a rendered index covering less than what was marked, or covering
    it wrongly, while the run exits 0.

    `parenthetical_originals: first_occurrence` is HONOURED here, not
    bypassed: this consults and updates the same `linker.global_seen` set the
    canon linker uses (the linker is constructed before this runs -- "before
    the scan" is not "before construction"), so the gloss appears exactly
    once book-wide. `linker.links_emitted` is likewise incremented here, so
    `delink_cost.inline_links_emitted` keeps meaning every inline link this
    render inserted rather than quietly narrowing to the canon ones."""
    counts = {"replaced": 0, "links": 0}

    def _rewrite(text):
        if "ENT_" not in text:
            return text
        out = []
        last = 0
        for match in _ENT_SPAN_RE.finditer(text):
            payload = match.group(2)
            span = spans[match.group(1)]  # preflight already proved the record exists
            tag, label = _entity_markup_identity(span)
            if (tag, label) in canon_composition:
                note_identity, source_form = canon_composition[(tag, label)]
                piece = f"[[{note_identity}|{payload}]]"
                if (linker.parenthetical_mode == "first_occurrence"
                        and label not in linker.global_seen):
                    piece += f" ({source_form})"
                linker.global_seen.add(label)
            else:
                note_identity = markup_note_identity[(tag, label)]
                piece = f"[[{note_identity}|{payload}]]"
            counts["replaced"] += 1
            counts["links"] += 1
            linker.links_emitted += 1
            # §7, emission site 2 of 2 (the other is `_Linker.link`).
            chunks, last, escaped = _editorial_bracket_emit(
                text, last, match.start(), match.end(), piece
            )
            out.extend(chunks)
            linker.brackets_escaped += escaped
        out.append(text[last:])
        return "".join(out)

    for container, key in _entity_markup_string_slots(nodestream):
        container[key] = _rewrite(container[key])
    return counts


def _flatten_wikilinks(text):
    """`[[target|display]]` -> `display`, `[[target]]` -> `target`, and
    `\\[`/`\\]` -> `[`/`]` (#795 §6.3).

    Used ONLY for a heading's frontmatter `title:` and filename slug, and
    only in `index` mode. A heading's marked spans link like every other
    span -- resolving them to bare payload instead is what would reopen the
    wrong-note problem inside a heading -- but a wikilink must not survive
    into a YAML title or a filename. The unescape is not incidental: §7 turns
    an editorial-bracketed marked name in a heading into `\\[[[…]]\\]`, and
    flattening only the inner wikilink would leave `title: \\[John\\]` and
    the backslashes in the slug."""
    text = _WIKILINK_ALIASED_RE.sub(lambda m: m.group(2), text)
    text = _WIKILINK_BARE_RE.sub(lambda m: m.group(1), text)
    return text.replace("\\[", "[").replace("\\]", "]")


def _render_markup_note(tag, label, aliases, ref, is_rtl):
    """#795 §6.6. Frontmatter carries only what is TRUE of a marked entity:
    the printed forms seen, the label, the tag as `category`, the `ref` when
    the label came from one, and `direction`. No `basis`/`confidence`/
    `source` -- those are canon's, and a markup note has no canon entry
    behind it by construction.

    No `## Mentions` section either: that index is source-anchored and
    canon-keyed, and `validate_backlinks.py` derives the notes it parses from
    canon alone -- markup notes are invisible to it, which is why that gate
    needs no change."""
    frontmatter = {"aliases": aliases, "name": label, "category": tag}
    if ref is not None:
        frontmatter["ref"] = ref
    frontmatter["direction"] = "rtl" if is_rtl else "ltr"
    return "\n".join([_yaml_frontmatter(frontmatter), "", f"# {label}"]) + "\n"


def _reject_residual_entity_tokens(rel_path, note_text):
    """#795 §6.5, post-condition 2: the LAST thing between a machine
    sentinel and a reader, checked on each note's final text immediately
    before `_write_note`.

    With the §6.4 preflight in place this can only fire on a resolver bug:
    the preflight covers BOTH inputs a note's text is built from -- the
    NodeStream and canon.json -- so anything reaching here was resolvable
    and was not resolved. That is the division of labour: the preflight
    proves the INPUT was resolvable and refuses BEFORE the vault is cleaned,
    this proves the OUTPUT was actually resolved. Both token forms, because
    a lone closer ships just as visibly as a lone opener.

    Gated on markup being active at all three call sites, so an undeclared
    project never runs it."""
    match = _ENT_TOKEN_RE.search(note_text)
    if match:
        raise RenderError(
            "entity_markup_residual_sentinel",
            f"refusing to write {rel_path!r}: it still contains the "
            f"entity-markup sentinel {match.group(0)!r} after resolution -- "
            "a machine token must never reach a reader.",
        )


# ---------------------------------------------------------------------------
# Verse content -> markdown (verse_policy.mode's two content fields, per
# validate_draft.py's own _verse_required_fields -- read directly there, not
# hardcoded blindly: `rendered` and/or `literal_gloss`, mode-dependent, both
# absent under mode: skip)
# ---------------------------------------------------------------------------

_VERSE_FNREF_RE = re.compile(r"⟦FNREF_(\d+)⟧")


def _convert_verse_fnrefs(text):
    # A footnote cited inside a verse (⟦FNREF_N⟧ baked into the source poem)
    # becomes an Obsidian [^N]. Prose/heading FNREFs are converted in
    # _render_block via node.fnrefs; verse content is not on that path (the
    # block-verse branch returns early, and an embedded verse's FNREF lives in
    # the verse content, not the carrier text), so the verse renderers convert
    # their own. The [^N]: definition line is emitted by _render_segment_note
    # from node.fnrefs, which assemble now populates from verse content.
    return _VERSE_FNREF_RE.sub(lambda m: f"[^{m.group(1)}]", text)


def _verse_texts(content):
    content = content or {}
    rendered = _convert_verse_fnrefs((content.get("rendered") or "").strip())
    gloss = _convert_verse_fnrefs((content.get("literal_gloss") or "").strip())
    return rendered, gloss


def _normalize_newlines(s):
    # CRLF first, then lone CR -> LF (order matters). Deliberately LF-specific
    # afterward -- NOT str.splitlines(), which also splits U+2028/U+2029/NEL/VT/FF.
    return s.replace("\r\n", "\n").replace("\r", "\n")


def _split_lf_lines(s):
    """Line-split on LF ONLY, mirroring str.splitlines()'s "a trailing line
    terminator yields no empty trailing element" -- but WITHOUT treating the
    exotic Unicode boundaries splitlines() also breaks on (U+2028/U+2029/NEL/
    VT/FF/U+001C-1E) as line breaks. #183: a verse's rendered/gloss text must
    line-split the same way whether the verse is a block or an inline mount;
    realistic translator input uses \n, and #172 already made the block
    gloss/footnote paths LF-specific for the same reason."""
    normalized = _normalize_newlines(s)
    lines = normalized.split("\n")
    if lines and lines[-1] == "":
        lines.pop()          # mirror splitlines(): a single trailing "\n" adds no empty tail
    return lines


def _flatten_gloss(s):
    """Flatten a multi-line literal gloss to a single line, LF-specific (NOT
    str.splitlines()). Shared by the block (`_render_verse_block`) and inline
    (`_render_verse_inline`) gloss paths so both flatten an exotic-Unicode-
    containing gloss identically (#183)."""
    return _normalize_newlines(s).replace("\n", " ")


def _render_verse_block(content, linker, seen_in_block=None):
    """A whole dedicated verse block (`kind: "verse"`, `mount: "block"`) --
    rendered as its own blockquote. Empty content (verse_policy.mode: skip)
    resolves to nothing at all, not an error and not a placeholder marker,
    per the shared assembler contract. `rendered` and `gloss` share one
    `seen_in_block` (#105c) -- a name appearing in both must link only once,
    not once per field."""
    rendered, gloss = _verse_texts(content)
    rendered = linker.link(rendered, seen_in_block)
    gloss = linker.link(gloss, seen_in_block)
    body = rendered or gloss
    if not body:
        return ""
    lines = [f"> {line}".rstrip() for line in _split_lf_lines(body)]
    if rendered and gloss:
        lines.append(">")
        flat_gloss = _flatten_gloss(gloss)
        lines.append(f"> *Literal: {flat_gloss}*")
    return "\n".join(lines)


def _render_verse_inline(content):
    """A verse embedded inside a prose/heading block's own text (`mount`
    something other than "block", e.g. a footnote-embedded or
    quote-embedded verse) -- rendered as a compact single-line italic
    substitution in place of the placeholder, since a real blockquote
    cannot sit mid-paragraph in markdown. Deliberately does NOT link its own
    text (#105c-ordering): entity linking must happen once, over the fully
    spliced block text, in true document order -- see `_render_block`.

    Returns `(text, label_span)`. `label_span` is None when there is no gloss
    label, else the (start, end) char offsets of the literal " (lit.: " label
    WITHIN the returned `text`, so _render_block can protect exactly that span
    from the linker BY POSITION -- no sentinel, no content matching (see the
    LABEL PROTECTION comment block above)."""
    rendered, gloss = _verse_texts(content)
    body = rendered or gloss
    if not body:
        return "", None
    single = " / ".join(line.strip() for line in _split_lf_lines(body) if line.strip())
    out = f"*{single}*"
    label_span = None
    if rendered and gloss:
        # Emit the label as ordinary literal text and record its exact offsets,
        # so _render_block can protect it from the single-pass linker by
        # position (a canon target of "lit" would otherwise match inside it).
        label = " (lit.: "
        label_start = len(out)
        out += label
        label_span = (label_start, label_start + len(label))
        out += f"{_flatten_gloss(gloss)})"
    return out, label_span


# ---------------------------------------------------------------------------
# BlockNode -> markdown
# ---------------------------------------------------------------------------

def _heading_level(node):
    """Renderer fail-safe CLAMP for a heading node's markdown level -- not a
    validation gate (a gate, when one exists, lives upstream in assemble.py/
    validate_extraction.py; this function's only job is to never let a
    malformed `level` reach an invalid or silently-degrading `#` run).
    Returns `node["level"]` when it is an `int` (explicitly NOT `bool` --
    Python's `bool` is an `int` subclass, so `isinstance(True, int)` is
    `True`, and a stray `level: true` must not sail through as if it were
    `1`) in the range 1..6; anything else -- absent, `None`, a `str` like
    `"3"`, a `bool`, or an out-of-range int (`0`, `7`) -- falls back to
    **2**, the pre-1.12.0 hardcoded value. The absent case matters most:
    every heading node built before this feature existed (and any
    hand-built/legacy nodestream) has no `level` key at all, so the
    byte-identical-output invariant for a project without `heading_levels`
    depends on this fallback. Clamping is mandatory, not merely tidy: a
    `level` of `0` would emit `f"{'#' * 0} {text}"` == `" {text}"`, silently
    demoting a heading to plain prose with a stray leading space -- no
    error, no heading, just vanished structure. A `level` above `6` would
    emit `#######...`, which is not a valid ATX heading past level 6."""
    level = node.get("level")
    if isinstance(level, int) and not isinstance(level, bool) and 1 <= level <= 6:
        return level
    return 2


def _render_block(node, linker):
    # One shared `seen_in_block` for the WHOLE rendered block (#105c) --
    # created once here. The prose branch below splices UNLINKED inline-verse
    # text into `text` first and links everything in one pass at the end, so
    # "first occurrence" follows true DISPLAY (document) order rather than
    # processing order. The verse-block branch (kind == "verse") still threads
    # `seen_in_block` through its own two `link()` calls (rendered, then
    # gloss) directly, since there `rendered` always displays before `gloss`
    # (blockquote body, then "Literal: gloss" beneath it) -- processing order
    # already matches display order there, so no splice-then-link-once step
    # is needed.
    seen_in_block = set()
    kind = node.get("kind")
    verses = node.get("verses") or []

    if kind == "verse":
        # A dedicated verse block IS its own verse (assemble.py's own
        # classification, contract's reconstruction algorithm step 3) --
        # render EVERY claim on the node straight from its own verse entry,
        # ignoring the raw surrounding text (expected to be little more than
        # the placeholders themselves). #119: render ALL entries, never just
        # verses[0] -- a 2+-entry list must not be silently truncated (whole
        # verse content, rendered+gloss, would otherwise be lost). One shared
        # `seen_in_block` across all entries (#105c: one wikilink per rendered
        # block). Empty entries (verse_policy.mode: skip -> "") are skipped;
        # the rest join with a blank line so each renders as its own distinct
        # blockquote, exactly as _render_segment_note joins sibling blocks.
        rendered_blocks = [
            _render_verse_block(v.get("content") or {}, linker, seen_in_block)
            for v in verses
        ]
        return "\n\n".join(block for block in rendered_blocks if block)

    text = node.get("text", "")

    # #118 item 3 (Fix D): when an embedded verse is the ENTIRE content of a
    # PROSE block (nothing else shares the line), there is no real
    # mid-paragraph constraint, so render it as a full blockquote -- matching
    # a mount:"block" verse's own presentation -- instead of the compact
    # inline italic. Scoped as narrowly as is sound: prose only (NEVER a
    # heading -- a heading whose whole text is a verse placeholder must keep
    # its "## " semantics, handled below), exactly one verse claim, and the
    # ORIGINAL block text must be nothing but that verse's placeholder. A
    # verse genuinely embedded mid-sentence keeps the compact-italic path
    # (see _render_verse_inline's "blockquote can't sit mid-paragraph"
    # docstring). Detected pre-substitution against the raw block text -- far
    # cheaper and more obviously correct than comparing the post-substitution
    # composed string.
    if kind == "prose" and len(verses) == 1:
        only_placeholder = verses[0].get("placeholder")
        if only_placeholder and text.strip() == only_placeholder:
            return _render_verse_block(verses[0].get("content") or {}, linker, seen_in_block)

    # Resolve verse placeholders AND fnref sentinels in ONE pass over the
    # ORIGINAL text, never N chained str.replace() calls: a placeholder value is
    # free-form (segpack.schema.json does not constrain it), so one substitution's
    # rendered OUTPUT could be re-matched and corrupted by a later replacement
    # whose search key happens to equal that output. We match ONLY against the
    # original `text` (never re-scanning inserted text), exactly as re.sub would,
    # but reconstruct the output manually via finditer so we can ALSO track the
    # absolute position each inline-verse " (lit.: " label lands at in the final
    # composed string -- for position-based linker protection (no sentinel).
    # `substitutions`: token -> (replacement, label_span_or_None). The span is the
    # (start, end) offsets of a verse's " (lit.: " label WITHIN its replacement
    # text; None for fnref tokens and gloss-less verses (nothing to protect).
    substitutions = {}
    for v in verses:
        placeholder = v.get("placeholder")
        if placeholder and placeholder not in substitutions:
            substitutions[placeholder] = _render_verse_inline(v.get("content") or {})
    for n in node.get("fnrefs") or []:
        substitutions.setdefault(_FNREF_SENTINEL_FMT.format(n=n), (f"[^{n}]", None))

    label_ranges = []
    if substitutions:
        # Longest key first so a token that is a prefix of another still matches.
        combined_re = re.compile(
            "|".join(re.escape(k) for k in sorted(substitutions, key=len, reverse=True))
        )
        out_parts = []
        cursor = 0  # length of output emitted so far == absolute offset in final text
        last = 0
        for m in combined_re.finditer(text):
            gap = text[last:m.start()]
            out_parts.append(gap)
            cursor += len(gap)
            repl, span = substitutions[m.group(0)]
            if span is not None:
                label_ranges.append((cursor + span[0], cursor + span[1]))
            out_parts.append(repl)
            cursor += len(repl)
            last = m.end()
        out_parts.append(text[last:])
        text = "".join(out_parts)

    # Link the whole composed block text in one pass (#105c document order),
    # protecting each inline-verse " (lit.: " label BY POSITION. The label text
    # is already final, so protection (not restoration) is all that is needed,
    # and it can never be confused with an identical string arriving from prose,
    # canon data, or a placeholder (see the LABEL PROTECTION comment block).
    text = linker.link(text, seen_in_block, extra_protected=label_ranges).strip()
    if not text:
        return ""
    if kind == "heading":
        return f"{'#' * _heading_level(node)} {text}"
    return text


def _heading_plain_text(node, flatten_wikilinks=False):
    """Resolve a heading node's KNOWN sentinels to PLAIN title text for the
    frontmatter `title` and filename slug: declared verse placeholders -> their
    flattened rendered verse text (footnote refs [^N] stripped -- a footnote
    marker does not belong in a title; no italic, no "(lit.: …)" label, no entity
    linking), this node's footnote anchors -> removed. Only KNOWN sentinels are
    touched: any OTHER bracketed span is literal source text and is preserved
    verbatim (the renderer's unresolved-bracket contract). A stray raw footnote
    anchor (fixed ⟦FNREF_N⟧ machine shape, never prose) is scrubbed as
    defense-in-depth. The "plain heading" fast path is gated on WHETHER THERE
    WAS ANY KNOWN SENTINEL TO RESOLVE (a declared verse placeholder or this
    node's own footnote anchor), never on "did the text change" -- a
    degenerate/malformed verse whose rendered content happens to equal its own
    placeholder sentinel would otherwise make the substitution a no-op and let
    the raw sentinel through unstripped. When there is nothing to resolve, the
    ORIGINAL text is returned with only .strip() -- byte-identical to the prior
    _segment_title, so plain-heading titles/slugs never change (no internal
    whitespace collapse).

    `flatten_wikilinks` (#795 §6.3, passed True ONLY in `index` mode) also
    reduces `[[target|display]]`/`[[target]]` and unescapes `\\[`/`\\]` --
    see `_flatten_wikilinks`. It is mode-confined on purpose: that keeps a
    literal `[[…]]` an operator wrote into a source heading behaving exactly
    as it does today on every other project. The fast path above has to know
    about it too, or a heading carrying a wikilink but NO verse placeholder
    and NO footnote anchor would take that path and ship the raw `[[…]]`
    into `title:` and the filename slug."""
    original = node.get("text") or ""
    substitutions = {}
    for v in node.get("verses") or []:
        ph = v.get("placeholder")
        if ph and ph not in substitutions:
            rendered, gloss = _verse_texts(v.get("content") or {})
            body = _TITLE_FN_MARKUP_RE.sub("", rendered or gloss)   # drop [^N] refs
            substitutions[ph] = " ".join(body.split())              # flatten multi-line verse to one title line
    for n in node.get("fnrefs") or []:
        substitutions.setdefault(_FNREF_SENTINEL_FMT.format(n=n), "")
    # Plain heading (no known sentinel to resolve): preserve prior behavior EXACTLY
    # -- .strip() only, no whitespace collapse. A literal ⟦variant⟧ that is neither
    # a declared placeholder nor a footnote anchor stays verbatim here.
    needs_flatten = bool(flatten_wikilinks and _HEADING_FLATTEN_PROBE_RE.search(original))
    if (not substitutions and not _TITLE_FNREF_ANCHOR_RE.search(original)
            and not _ENT_TOKEN_RE.search(original) and not needs_flatten):
        return original.strip()
    text = original
    if substitutions:
        combined_re = re.compile(
            "|".join(re.escape(k) for k in sorted(substitutions, key=len, reverse=True))
        )
        text = combined_re.sub(lambda m: substitutions[m.group(0)], text)   # resolve
        # A well-formed verse never renders to its own sentinel; if a malformed
        # content field re-introduced a known placeholder via its replacement
        # value, blank it so a raw ⟦…⟧ can never reach the title (#171 invariant).
        text = combined_re.sub("", text)
    text = _TITLE_FNREF_ANCHOR_RE.sub("", text)   # scrub stray anchors not in this node's fnrefs
    # #795: strip entity-markup sentinels, keeping the payload between them
    # -- the same defense-in-depth the FNREF scrub above is (⟦ENT_n⟧ is a
    # fixed machine shape, never prose), and load-bearing for ONE caller in
    # particular. UNCONDITIONAL, and every matching token individually: a
    # LONE opener or closer is removed too, because a half-pair ships to a
    # reader just as visibly as a whole one. That is also the one way this
    # function's output can change for a project declaring no markup -- a
    # heading whose SOURCE text literally contains an ⟦ENT_n⟧-shaped token
    # now leaves the byte-identical fast path (whose guard tests for it),
    # so it loses those tokens AND has its internal whitespace collapsed.
    # Prose says so at `references/output-target-adapters/obsidian.md`
    # "Editorial brackets". `validate_backlinks.py:780` reconstructs each
    # segment note's filename by calling `_segment_title` against the
    # PERSISTED nodestream.json -- which assemble.py wrote BEFORE render()'s
    # resolution pre-pass ran, so its heading text still carries raw
    # sentinels. Without this scrub that gate would derive
    # "001 _ENT_1_John_ENT_1_" for a segment render() actually wrote as
    # "001 John", and every Mentions link into that segment would be reported
    # missing. Scrubbing the tokens yields exactly render()'s own answer,
    # because a resolved span's wikilink DISPLAY text is the payload and
    # `_flatten_wikilinks` reduces it back to that same payload.
    text = _ENT_TOKEN_RE.sub("", text)
    if flatten_wikilinks:
        # LAST, after every sentinel resolution: a declared verse placeholder
        # resolves to verse text that the #795 pre-pass may itself have
        # wikilinked, so flattening earlier would miss it.
        text = _flatten_wikilinks(text)
    return re.sub(r"\s+", " ", text).strip()


def _segment_title(seg_nodes, seg, flatten_wikilinks=False):
    """`flatten_wikilinks` is threaded straight through to
    `_heading_plain_text` (#795 §6.3). OPTIONAL and defaulting to today's
    behaviour: `validate_backlinks.py` calls this with two arguments while
    re-deriving each segment note's own expected filename, and that call must
    keep resolving to the byte-identical slug this adapter wrote."""
    for node in seg_nodes:
        if node.get("kind") == "heading":
            text = _heading_plain_text(node, flatten_wikilinks=flatten_wikilinks)
            if text:
                return text
    return seg


def _render_segment_note(seg, seg_nodes, footnote_text_by_n, linker, is_rtl,
                         flatten_wikilinks=False):
    title = _segment_title(seg_nodes, seg, flatten_wikilinks=flatten_wikilinks)
    frontmatter = {
        "seg": seg,
        "title": title,
        "direction": "rtl" if is_rtl else "ltr",
    }

    body_blocks = []
    used_fnrefs = set()
    for node in seg_nodes:
        block_md = _render_block(node, linker)
        if block_md:
            body_blocks.append(block_md)
        used_fnrefs.update(node.get("fnrefs") or [])

    fn_lines = []
    for n in sorted(used_fnrefs):
        linked = linker.link(footnote_text_by_n.get(n, ""))
        indented = _normalize_newlines(linked).replace("\n", "\n    ")
        fn_lines.append(f"[^{n}]: {indented}")

    parts = [_yaml_frontmatter(frontmatter), "\n\n".join(body_blocks)]
    if fn_lines:
        parts.append("\n".join(fn_lines))
    return "\n\n".join(p for p in parts if p) + "\n"


# ---------------------------------------------------------------------------
# Entity notes (canon.json -> vault/<folder>/<name>.md)
# ---------------------------------------------------------------------------

def _is_safe_path_segment(value):
    if not isinstance(value, str) or not value:
        return False
    if value in (".", ".."):
        return False
    if value.startswith("/") or value.startswith("\\"):
        return False
    return bool(_FOLDER_ALLOW_RE.fullmatch(value))


def _resolve_folder(category, folders_map):
    """category -> folder, per obsidian.md's category->folder catalog:
    "a category absent from that map, or blank/absent on the entry itself,
    routes to vault/other/". `folders_map` (the profile's own
    `output.adapter_config.obsidian.folders`) is the WHOLE catalog -- an
    out-of-catalog category is an expected, valid state (open vocabulary,
    no enum), routed to `other`, never rejected outright. Note what this
    means for the security posture: `category` itself is used ONLY as a
    dict lookup key here, never as a path segment -- the only string that
    ever reaches the filesystem path is a `folders_map` VALUE the project
    itself declared in profile.yml, which is what the allow-list below
    actually guards (a profile-author typo/unsafe value, not an
    attacker-controlled category)."""
    folders_map = folders_map or {}
    if not isinstance(category, str) or category not in folders_map:
        return DEFAULT_FOLDER
    candidate = folders_map[category]
    if _is_safe_path_segment(candidate):
        return candidate
    return DEFAULT_FOLDER


def sanitize_filename_component(value, fallback):
    """POSITIVE allow-list sanitizer for a note filename derived from
    content that may legitimately be in any script (canon.json's
    `source_form`, or a segment's own heading text) -- unlike the strict
    ASCII category/folder allow-list (a small, curated, project-declared
    vocabulary), a name has no such constraint. Still a positive allow-list,
    never a denylist: every character that is not `str.isalnum()`, not a
    combining mark (`_FILENAME_MARK_CATEGORIES`) and not in the small
    curated punctuation set (`_FILENAME_EXTRA_CHARS`) is replaced with "_",
    not merely rejected after the fact via a blocklist of "dangerous"
    characters.

    TWO PROPERTIES THIS FUNCTION OWNS. Until #586 both were inherited from
    the allow-list's silence -- "." was simply excluded, so neither could
    be violated. "." is admitted now (a printed name carries it), so the
    normalization tail below enforces both in code instead:

      (a) NO TRAVERSAL. A run of "." collapses to a single "." and "." is
          stripped at both ends, so the returned stem can neither BE nor
          CONTAIN a ".." segment. The leading-dot strip does a second job:
          diff_rendered_output.py's recursive walker skips dot-entries, so
          a dot-named note would be invisible to the render+diff gate that
          is supposed to be watching this adapter's output.
      (b) NO EXTENSION OF ITS OWN. A trailing ".md" -- the extension this
          script appends itself -- has its "." turned into "_" (case
          preserved: "x.MD" -> "x_MD"). That loop runs at most ONCE: its
          guard needs a "." three characters from the end and its body
          writes "_" at exactly that index, leaving a "_md" tail that
          cannot re-match -- so only the name's OWN trailing extension is
          neutralized ("x.md.md" -> "x.md_md"). It is spelled as a loop
          rather than an `if` because the condition, not the count, is the
          property. Necessary because otherwise the wikilink identity (the
          relpath minus the ONE appended ".md") would name a file that does
          not exist: "x.md" would be written as "x.md.md" and linked as
          "[[.../x.md]]".
      (c) WRITABLE AT ALL. A stem is capped at _FILENAME_MAX_BYTES, a run
          of marks at _MAX_MARKS_PER_BASE, and a Win32 device basename gets
          a "_" (_WIN32_RESERVED_STEMS) -- because _write_note runs AFTER
          _clean_vault_content has emptied the managed vault: a name the
          filesystem refuses does not lose one note, it aborts the render
          over a half-rebuilt vault. All three are the review's finding
          rather than the issue's, and each constant records what was
          measured. Note this property is about the WHOLE fleet of
          filesystems a vault travels across, not the one it was rendered
          on: the device-name rule is enforced on macOS too.

    What it deliberately does NOT do, so the next reader does not mistake
    silence for coverage: a trailing extension that is
    NOT ".md" (".png") is left alone, since a general trailing-extension
    rule would damage legitimate names like "J.R.R". And two source forms
    that differ only by an invisible mark now yield two filenames a reader
    cannot tell apart -- the price of admitting the mark categories, which
    is what makes a pointed Hebrew name typable at all."""
    if not isinstance(value, str) or not value:
        return fallback
    out = []
    marks_in_run = 0
    for ch in value:
        # The run is counted in DECOMPOSED marks, never in written
        # characters: the filesystem canonically decomposes a name before
        # applying its own limit, and the two counts differ in both
        # directions. 59 code points that are themselves Mn/Mc/Me expand to
        # two or three marks under NFD (U+0344, U+0F73, U+0CCB...), and a
        # precomposed BASE contributes marks of its own (U+1EBF decomposes
        # to a letter plus two). Counting as-written let "A" + 16 x U+0344
        # -- a run of 16 by that count, 32 after NFD -- walk straight past a
        # cap of 30 into the EILSEQ this guard exists to prevent, which is
        # #586's own guard failing on its own terms (found post-merge).
        weight = sum(
            1 for c in unicodedata.normalize("NFD", ch)
            if unicodedata.category(c) in _FILENAME_MARK_CATEGORIES
        )
        if unicodedata.category(ch) in _FILENAME_MARK_CATEGORIES:
            marks_in_run += weight
            out.append(ch if marks_in_run <= _MAX_MARKS_PER_BASE else "_")
            continue
        # A non-mark starts a fresh run -- but not necessarily at zero, since
        # a precomposed base carries its own marks into it.
        marks_in_run = weight
        out.append(ch if (ch.isalnum() or ch in _FILENAME_EXTRA_CHARS) else "_")
    kept = "".join(out)
    # Capped BEFORE the tail, never after: every step below is length
    # non-increasing (a collapse and a strip only shorten; the ".md" loop
    # substitutes in place), so the tail's guarantees still hold on the
    # truncated string. `errors="ignore"` drops the partial character a
    # byte-slice can cut in half.
    kept = kept.encode("utf-8")[:_FILENAME_MAX_BYTES].decode("utf-8", "ignore")
    kept = re.sub(r"\.{2,}", ".", kept).strip("_ .")
    while kept.lower().endswith(".md"):
        kept = kept[:-3] + "_" + kept[-2:]
    # Collapsed LAST, so the "_" the loop above just substituted for a dot
    # cannot leave a "__" run behind it ("x_.md" -> "x__md" -> "x_md").
    kept = re.sub(r"_+", "_", kept)
    device, dot, extension = kept.partition(".")
    if device.upper() in _WIN32_RESERVED_STEMS:
        kept = f"{device}_{dot}{extension}"
    if not kept or kept in (".", ".."):
        return fallback
    if all(unicodedata.category(ch) in _FILENAME_MARK_CATEGORIES for ch in kept):
        # A stem of nothing but combining marks is an INVISIBLE filename
        # (a lone U+0301, or the U+FE0F left behind by an emoji whose base
        # character was replaced) -- worse for the reader than the stable
        # `entity-<sha1>`/`segment-<sha1>` fallback this replaces it with,
        # and the same unusable-name class #586 exists to fix.
        return fallback
    return kept


def _stable_fallback_name(value, prefix):
    """Deterministic (never Python's randomized str hash()) fallback name,
    so a filename collision-avoidance/empty-sanitization fallback stays
    identical across runs -- required for the render+diff acceptance gate
    to ever reach a stable baseline."""
    digest = hashlib.sha1((value or "").encode("utf-8", errors="replace")).hexdigest()[:8]
    return f"{prefix}-{digest}"


def _dedupe_path(base_path, used_paths):
    """Two different source_forms can sanitize to the same filename (e.g.
    'Jean!' and 'Jean?' both -> 'Jean_'). Disambiguate deterministically by
    a numeric suffix -- deterministic because callers always iterate
    `sorted(entries)`, so collision order is stable across runs.

    The `used_paths` MEMBERSHIP KEY is folded (NFC-normalized + casefolded)
    so two exact-string-distinct paths that only differ by case (e.g.
    'People/IVAN.md' vs 'People/Ivan.md') still collide here and get a `-2`
    suffix applied -- on a case-insensitive filesystem (APFS default,
    Windows) they would otherwise resolve to the same inode, and the second
    `write_text` would silently clobber the first. The RETURNED/STORED path
    stays case-preserving -- only the membership key is folded, never the
    path itself."""
    def _fold(p):
        return unicodedata.normalize("NFC", p).casefold()

    key = _fold(base_path)
    if key not in used_paths:
        used_paths.add(key)
        return base_path
    stem = base_path[: -len(".md")] if base_path.endswith(".md") else base_path
    n = 2
    while True:
        candidate = f"{stem}-{n}.md"
        candidate_key = _fold(candidate)
        if candidate_key not in used_paths:
            used_paths.add(candidate_key)
            return candidate
        n += 1


def _entity_note_relpath(source_form, entry, folders_map, used_paths):
    folder = _resolve_folder(entry.get("category"), folders_map)
    stem = sanitize_filename_component(source_form, _stable_fallback_name(source_form, "entity"))
    return _dedupe_path(f"{folder}/{stem}.md", used_paths)


def _resolve_entity_notes(entries, folders_map, used_paths=None):
    """Resolves every entry's note relpath (folder/stem.md) UP FRONT, in
    the same `sorted(entries)` order the entity-note-writing loop uses --
    so the wikilink identity used while rendering narrative pages (via
    `build_entity_index`/`_Linker`) is guaranteed IDENTICAL to the actual
    filename the writing loop emits later, collision-dedup included
    (review round 1: the link target and the emitted filename must be the
    same string, or the link never resolves to the note). Returns
    {source_form: relpath}; the writing loop reuses this same mapping
    rather than re-resolving (and re-deduping) a second time.

    `used_paths` (#795 §6.1) is an OPTIONAL, caller-owned collision set, so
    `render()` can resolve the entity-markup notes afterwards through the
    SAME set and a markup note can never take or overwrite a canon note's
    path. Absent -- which is how `validate_backlinks.py:860` calls this, with
    two arguments, immediately `.items()`-ing the dict it returns -- a fresh
    set is used and every canon relpath is byte-identical to what it was
    before #795 existed. Canon MUST be resolved first for that to hold; see
    `_resolve_markup_notes`."""
    if used_paths is None:
        used_paths = set()
    relpath_by_source_form = {}
    for source_form in sorted(entries):
        entry = entries[source_form]
        if not isinstance(entry, dict):
            continue
        relpath_by_source_form[source_form] = _entity_note_relpath(source_form, entry, folders_map, used_paths)
    return relpath_by_source_form


def _mentions_note_identities(mention_records, segment_note_by_seg, seg_position):
    """D1: one entity's `nodestream["mentions"][source_form]` list (each a
    `{source_form, seg, origin, ...}` Record per the occurrence_targets.py
    contract -- only `seg` matters here, the renderer is origin-agnostic)
    reduced to the ordered, DEDUPED list of note identities its `##
    Mentions` section links to. Deduped per note (a seg contributing
    multiple Records collapses to one link, via the `set` below) and
    sorted into READING order (`seg_position`, this render's own
    `full_order` index -- NOT the Record list's own, unspecified, order).
    A `seg` absent from `segment_note_by_seg` (no rendered segment note --
    should not happen for a `build()`-derived aggregate, since eligibility
    is keyed off the very same NodeStream, but defensive rather than a
    KeyError on a malformed/hand-authored `nodestream["mentions"]`) is
    silently skipped, never a phantom link."""
    segs = {
        r.get("seg") for r in (mention_records or [])
        if isinstance(r, dict) and r.get("seg") in segment_note_by_seg
    }
    ordered_segs = sorted(segs, key=lambda seg: seg_position.get(seg, len(seg_position)))
    identities = []
    for seg in ordered_segs:
        rel_path = segment_note_by_seg[seg]
        identity = rel_path[: -len(".md")] if rel_path.endswith(".md") else rel_path
        identities.append(identity)
    return identities


def _render_mentions_section(note_identities):
    """D1: the opt-in, source-anchored occurrence index -- a `## Mentions`
    heading listing every rendered segment note this entity was found in,
    wrapped in the reserved boundary markers `validate_backlinks.py`
    parses to find ONLY this generated region (never an authored `note`
    body, however similar it looks -- codex R5/R6/R7's spoof-resistance
    chain, see `_validate_mentions_safe_canon`)."""
    lines = [MENTIONS_SECTION_MARKER_BEGIN, "", "## Mentions", ""]
    for identity in note_identities:
        lines.append(f"- [[{identity}]]")
    lines.append(MENTIONS_SECTION_MARKER_END)
    return "\n".join(lines)


def _render_entity_note(source_form, entry, is_rtl, mentions_section=None):
    """Frontmatter mirrors canon-entry.schema.json exactly, in the field
    order obsidian.md documents, plus two adapter-computed fields:
    `aliases` (the raw `source_form`, so a reader/search can still find
    this note by its original-script identity even though the wikilink
    TARGET is now the sanitized note name -- round-trip per review round
    1) and `direction`. `note` is deliberately singular -- it mirrors
    canon-entry.schema.json's own field name, not a pluralized `notes`
    list. Entries with `basis: not_a_name` / `is_proper_name: false`
    (realia, not names) get the identical treatment -- this frontmatter
    never branches on `is_proper_name`.

    `mentions_section` (D1, optional -- `None` unless
    `_effective_mentions_enabled(profile)` holds AND this entity has >=1
    eligible mention): a pre-rendered `_render_mentions_section(...)`
    string appended after any authored `note` body. `None` (the default,
    and the ONLY value ever passed when the feature is not
    effective-enabled) means this function's output is byte-identical to
    every 1.7.0 render -- no section, no marker, nothing new."""
    frontmatter = {
        "aliases": [source_form],
        "source_form": source_form,
        "canonical_target_form": entry.get("canonical_target_form", ""),
        "category": entry.get("category") or "",
        "is_proper_name": bool(entry.get("is_proper_name", False)),
        "basis": entry.get("basis", ""),
        "confidence": entry.get("confidence", ""),
    }
    if entry.get("source"):
        frontmatter["source"] = entry["source"]
    frontmatter["note"] = entry.get("note", "")
    frontmatter["direction"] = "rtl" if is_rtl else "ltr"

    heading = entry.get("canonical_target_form") or source_form
    lines = [_yaml_frontmatter(frontmatter), "", f"# {heading}"]
    note_text = entry.get("note")
    if note_text:
        lines.append("")
        lines.append(note_text)
    if mentions_section:
        lines.append("")
        lines.append(mentions_section)
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _yaml_frontmatter(mapping):
    dumped = yaml.safe_dump(
        mapping, allow_unicode=True, sort_keys=False, default_flow_style=False
    ).rstrip("\n")
    return f"---\n{dumped}\n---"


def _is_rtl_language(code):
    if not isinstance(code, str) or not code:
        return False
    return code.split("-")[0].lower() in _RTL_LANGUAGE_CODES


DELINK_COST_KEY = "delink_cost"


def _build_delink_cost(delinked_owners, linker):
    """#588's report block: what collision de-linking cost THIS render.

        {"delinked_targets": [{"canonical_target_form", "owners",
                                "unlinked_occurrences"}, ...],
         "unlinked_occurrences_total": int,
         "inline_links_emitted": int}

    `unlinked_occurrences` counts every occurrence of that target string in
    the text the linker actually scanned and left unlinked -- not one per
    block, and not a re-scan of the rendered markdown (see `_Linker`'s own
    docstring for why the latter cannot be correct). `inline_links_emitted`
    is what this render actually inserted. The two are DIFFERENT
    cardinalities on purpose -- occurrences versus links, and the wikilink
    rule emits at most one link per target per block -- so both are reported
    under names that say which is which and nothing is claimed about their
    ratio. Rows are sorted by cost, then by target, so the head of the list
    is the operator's worklist.

    A target with zero occurrences is still listed: the de-linked SET is
    itself the diagnostic (it names every canon form implicated), and its
    cost being zero is the useful, non-obvious part."""
    counts = linker.delinked_counts
    rows = [
        {
            "canonical_target_form": target,
            "owners": owners,
            "unlinked_occurrences": counts.get(target, 0),
        }
        for target, owners in delinked_owners.items()
    ]
    rows.sort(key=lambda row: (-row["unlinked_occurrences"], row["canonical_target_form"]))
    return {
        "delinked_targets": rows,
        "unlinked_occurrences_total": sum(row["unlinked_occurrences"] for row in rows),
        "inline_links_emitted": linker.links_emitted,
    }


def _warn_delink_cost(delink_cost, stream=None):
    """One stderr WARN naming the number whenever de-linking cost this book
    anything at all (#588). Deliberately not gated on a ratio: the issue's
    "the de-linked population dwarfs the linked one" case is a subset of
    "> 0", and a book whose most-named figures are silenced should not
    depend on a threshold to be told so. The remedy it names is QUALIFIED:
    a group cannot help a target whose owners include a `sense_translated`
    entry, or one where some owner is outside the group, so the text sends
    the operator to the reported `owners` first rather than into an edit +
    re-render cycle that cannot change the result. The renderer warns rather than the
    W9 gate because collision de-linking runs on EVERY obsidian render,
    while `validate_backlinks.py` short-circuits when the `## Mentions`
    appendix is disabled."""
    total = delink_cost.get("unlinked_occurrences_total", 0)
    if not total:
        return
    # Rows arrive sorted by descending cost, so the charged ones lead and
    # `charged[:3]` is the largest three.
    charged = [
        row for row in (delink_cost.get("delinked_targets") or [])
        if row["unlinked_occurrences"]
    ]
    top = ", ".join(
        f"{row['canonical_target_form']!r} x{row['unlinked_occurrences']}"
        for row in charged[:3]
    )
    print(
        f"WARN: collision de-linking left {total} occurrence(s) of "
        f"{len(charged)} shared target(s) with no inline link "
        f"(this render emitted {delink_cost.get('inline_links_emitted', 0)} "
        f"inline link(s) in total). Largest: {top}. Each of these targets is "
        "owned by >=2 canon entries -- read the reported `owners` first. A "
        "canon_link_groups.json group re-links a target only when EVERY one "
        "of its owners is in that group and none is sense_translated; a "
        "group plus an outsider, or any sense_translated owner, stays "
        "de-linked by design, so grouping those would change nothing. "
        "See references/output-target-adapters/obsidian.md.",
        file=stream if stream is not None else sys.stderr,
    )


def _marker_payload(delink_cost=None):
    """The vault ownership marker's content. `managed_by`/`target` are the
    identity `_is_valid_vault_marker` checks; `delink_cost` (#588) rides
    alongside as this render's own record of what de-linking cost, which
    `validate_backlinks.py` republishes rather than re-deriving. Its ABSENCE
    is meaningful: a marker without it describes a vault whose render did
    not complete (or one written by an older version), and the gate reports
    `null` rather than a stale number."""
    payload = {"managed_by": "literary-translator", "target": "obsidian"}
    if delink_cost is not None:
        payload[DELINK_COST_KEY] = delink_cost
    return payload


def _is_valid_vault_marker(marker_path):
    """True only if marker_path is a REAL, regular file (review round 3,
    [BLOCKER]: `Path.is_file()` alone FOLLOWS a symlink, so a planted
    `.literary-translator-vault.json -> /some/real/file` symlink would
    otherwise satisfy the ownership gate below) whose content parses as
    THIS adapter's own marker JSON -- the FULL identity `_marker_payload()`
    actually stamps (`managed_by` AND `target == "obsidian"`, review round
    5: checking `managed_by` alone would let a partial marker
    `{"managed_by": "literary-translator"}`, or a cross-adapter marker
    stamped by some OTHER output-target adapter (`"target": "docusaurus"`),
    pass this gate -- and this adapter's clean-render would then delete a
    vault it does not actually own). A symlink, or a foreign/garbage file
    that merely shares the name, never satisfies the gate -- content
    validation closes the "some unrelated dotfile happens to have this
    exact name" case too."""
    if marker_path.is_symlink() or not marker_path.is_file():
        return False
    try:
        data = json.loads(marker_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # ValueError, not just json.JSONDecodeError -- it's the common
        # parent of JSONDecodeError AND UnicodeDecodeError (review round
        # 4: a REAL marker file with non-UTF-8 bytes raised
        # UnicodeDecodeError straight through read_text(encoding="utf-8"),
        # escaping as a bare traceback instead of returning False here).
        return False
    return (
        isinstance(data, dict)
        and data.get("managed_by") == "literary-translator"
        and data.get("target") == "obsidian"
    )


def _stamp_vault_marker(out_dir, delink_cost=None):
    """Writes/refreshes the ownership marker WITHOUT ever following an
    existing symlink at the FINAL marker path (review round 3, [BLOCKER]):
    if something is already a symlink there, unlink it first (never write
    through it); the actual write always goes through a TEMP file first,
    then `os.replace(tmp, marker_path)` -- which always replaces whatever
    directory entry currently sits at the destination, symlink or regular
    file, rather than following it.

    The temp file itself must ALSO never be reachable via a planted
    symlink (review round 4, [BLOCKER]): a predictable dotfile temp name
    (e.g. ".literary-translator-vault.json.tmp-<pid>") is preserved across
    clean-render (it starts with "."), so an attacker could plant a
    symlink at that exact path pointing at an external file -- a plain
    `Path.write_text()` to that path would FOLLOW the symlink and clobber
    the external target, even though the final os.replace is itself safe.
    `tempfile.mkstemp` closes this: it creates the temp file with
    O_CREAT|O_EXCL (fails instead of following/reusing anything already at
    that path) under a securely-randomized, non-predictable name -- and a
    NON-dot prefix ("lt-vault-tmp-") so a stray leftover from a crashed
    prior run (before os.replace ran) is swept by the next clean-render's
    ordinary non-dot-entry deletion, rather than surviving forever like a
    dotfile would."""
    marker_path = out_dir / VAULT_MARKER_FILENAME
    if marker_path.is_symlink():
        marker_path.unlink()
    fd, tmp_name = tempfile.mkstemp(dir=str(out_dir), prefix="lt-vault-tmp-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(_marker_payload(delink_cost), ensure_ascii=False) + "\n")
        os.replace(tmp_name, marker_path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _clean_vault_content(out_dir):
    """Clears every top-level entry this adapter itself manages (segment
    note files directly under out_dir, and category folders) before a
    fresh render -- otherwise a re-render into an existing out_dir leaves
    STALE notes behind (e.g. a canon entry since removed keeps its old
    note, and the render+diff acceptance gate wrongly PASSES against a
    baseline that also still had it -- review round 1). Deterministic,
    from-scratch rebuild is the whole point of that gate.

    Hidden top-level entries (any name starting with ".") are explicitly
    PRESERVED -- `.baseline/` (diff_rendered_output.py's frozen snapshot),
    `.assembled/` (assemble.py's own NodeStream/anchor-map artifacts), and
    this adapter's own `VAULT_MARKER_FILENAME` all live as siblings inside
    this same out_dir and are not deleted here.

    OWNERSHIP GATE (review round 2, [BLOCKER]; hardened round 3, [BLOCKER]):
    if out_dir already has any non-dot entry AND its `VAULT_MARKER_FILENAME`
    does not pass `_is_valid_vault_marker` (a REAL regular file, never a
    symlink, whose content is genuinely this adapter's own marker JSON --
    NOT the looser `.is_file()` check the round-2 fix originally used,
    which a planted `.literary-translator-vault.json -> /some/real/file`
    symlink would satisfy, bypassing the gate entirely), this is NOT a
    vault this adapter has ever rendered into -- it could be an arbitrary
    directory a caller pointed `out_dir` at (e.g. a misconfigured
    `output.destination`), and blindly deleting its contents would destroy
    files this adapter doesn't own. Refuse instead (`RenderError`,
    reason `out_dir_not_managed`). A genuinely fresh/empty out_dir (no
    non-dot entries at all) has nothing to refuse and proceeds -- `render()`
    stamps the marker (via `_stamp_vault_marker`, itself symlink-safe) at
    the end of a successful run, so the SECOND render into the same
    out_dir sees a valid marker and cleans normally.

    NO-FOLLOW DELETION: a symlink entry is `unlink()`-ed directly, checked
    BEFORE the `is_dir()` branch -- `Path.is_dir()` follows a symlink, so
    testing that first would route a symlink-to-directory into
    `shutil.rmtree(entry)`, which either raises (rmtree refuses a bare
    symlink argument) or, worse, recurses into the LINK TARGET's own
    contents when the symlink is nested rather than the top-level entry
    itself. Unlinking the symlink entry is always the intended, contained
    action: the link vanishes, its target is untouched either way."""
    if not out_dir.is_dir():
        return

    entries = list(out_dir.iterdir())
    non_dot_entries = [e for e in entries if not e.name.startswith(".")]
    if non_dot_entries and not _is_valid_vault_marker(out_dir / VAULT_MARKER_FILENAME):
        raise RenderError(
            "out_dir_not_managed",
            f"refusing to clean {out_dir}: it already contains content but "
            f"no valid {VAULT_MARKER_FILENAME} ownership marker was found "
            "(a real, regular file this adapter itself wrote) -- this "
            "adapter will not delete files it doesn't own. If this really "
            "is a vault this adapter should manage, remove its stale "
            "content by hand once, or point output.destination at an empty "
            "directory -- the marker is stamped automatically from then on.",
        )

    for entry in non_dot_entries:
        if entry.is_symlink():
            entry.unlink()
        elif entry.is_dir():
            shutil.rmtree(entry)
        else:
            entry.unlink()


def _write_note(out_dir, rel_path, content):
    """Joins + writes under out_dir, with a realpath-containment check as
    defense in depth on top of sanitize_filename_component/_resolve_folder
    already structurally preventing "/"/".."  from ever reaching this join
    -- guard the sink as well as the source (repo identifier->path
    allow-list precedent)."""
    out_dir_resolved = out_dir.resolve()
    full_path = (out_dir / rel_path).resolve()
    if full_path != out_dir_resolved and out_dir_resolved not in full_path.parents:
        raise RuntimeError(f"refusing to write outside the vault root: {rel_path!r}")
    full_path.parent.mkdir(parents=True, exist_ok=True)
    full_path.write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# The adapter entry point
# ---------------------------------------------------------------------------

def render(nodestream: dict, canon: dict, profile: dict, out_dir: Path) -> dict:
    """Writes the assembled NodeStream as an Obsidian vault under out_dir.
    Returns {"written": [relative_path, ...], "kind": "vault"} for
    diff_rendered_output.py -- see references/output-target-adapters/
    obsidian.md for the full vault-layout spec this implements.

    Raises `RenderError` (carrying `.reason`) for a fail-closed out_dir
    precondition -- see `_clean_vault_content` (`out_dir_not_managed`) and
    the symlink guard immediately below (`out_dir_is_symlink`) -- and for
    #795's three entity-markup failures: `entity_markup_unresolvable` (§6.4,
    raised BEFORE the existing vault is touched),
    `entity_markup_coverage_mismatch` (§6.5) and
    `entity_markup_residual_sentinel` (§6.5)."""
    out_dir = Path(out_dir)
    if out_dir.is_symlink():
        # Checked BEFORE mkdir(exist_ok=True) -- that call would otherwise
        # silently succeed against a symlinked out_dir (the directory
        # "exists" via the link), and _clean_vault_content would then
        # delete through it into the LINK TARGET, which may not be a vault
        # this adapter owns at all (review round 2, [BLOCKER]).
        raise RenderError(
            "out_dir_is_symlink",
            f"refusing to render into a symlinked out_dir ({out_dir}) -- "
            "writing/cleaning through a symlink could affect the link "
            "TARGET's own contents rather than a vault this adapter owns; "
            "point output.destination at a real directory instead",
        )
    # #795: the ONE place this render decides whether declared entity markup
    # is in effect. In every other mode -- `off`, `strip`, and the
    # `index_unsupported_target` state assemble.py itself refuses -- every
    # path below is inert, exactly the discipline `nodestream["mentions"]`
    # already gets under a non-effective-enabled profile. The deepcopy is
    # taken only here, because the resolution pre-pass rewrites
    # node/verse/footnote text in place and assemble.py's own nodestream
    # object (already persisted to nodestream.json) must not change under it.
    entity_markup_active = _entity_markup_mode(profile) == "index"
    if not entity_markup_active:
        # ...with ONE exception, and it is the whole point of the feature.
        # Being inert about the KEY is right; being inert about a book whose
        # TEXT carries `⟦ENT_n⟧` is how machine markup reaches a reader, which
        # is the failure #795 exists to close. A non-empty span table can only
        # come from an `index`-mode assemble -- `strip` and `off` write no key,
        # and `index_from: markup` under another target is refused there -- so
        # this pairing means the NodeStream and the profile are from different
        # runs: someone removed `index_from: markup` (or the whole block) and
        # re-rendered without re-assembling. There is no legitimate path to it
        # and no resolution this mode can perform, so it refuses instead of
        # delivering the sentinels verbatim.
        stale = _entity_markup_spans(nodestream)
        if stale:
            raise RenderError(
                "entity_markup_stale_nodestream",
                f"this NodeStream records {len(stale)} entity-markup span(s), "
                "so it was assembled under `output.entity_markup.index_from: "
                "markup`, but this profile no longer resolves to that mode -- "
                "its text still carries ⟦ENT_n⟧ sentinels and nothing here can "
                "resolve them, so they would ship verbatim to a reader. Re-run "
                "assemble.py against the current profile.",
            )
    entity_spans = _entity_markup_spans(nodestream) if entity_markup_active else {}
    if entity_markup_active:
        nodestream = copy.deepcopy(nodestream)

    meta = nodestream.get("meta") or {}
    is_rtl = _is_rtl_language(meta.get("target"))

    output_cfg = (profile or {}).get("output") or {}
    parenthetical_mode = (output_cfg.get("name_display") or {}).get("parenthetical_originals") or "never"
    folders_map = ((output_cfg.get("adapter_config") or {}).get("obsidian") or {}).get("folders") or {}

    # D1/D4: computed ONCE, fresh, from this call's own `profile` -- gates
    # the Mentions section and the canon reserved-field rejections below.
    # D3 (collision de-linking, just below) no longer gates on THIS
    # predicate -- the `## Mentions` appendix `enabled` flag -- at all
    # (#206/#207 -- see build_entity_index's own docstring); it still
    # gates on `_is_obsidian_target(profile)` via its own call site. See
    # `_effective_mentions_enabled`'s own docstring for why THIS predicate
    # is profile-derived and not simply "the flag", so the standalone CLI's
    # `target: "custom"` path (`main()` below) can never activate D1/D4.
    mentions_enabled = _effective_mentions_enabled(profile)
    # D3: the SAME predicate gates link-group validation, collision
    # de-linking and the de-linked owner list below -- read once, from this
    # call's own `profile`, so the three can never disagree about whether
    # this render is a real obsidian one.
    collision_delink = _is_obsidian_target(profile)

    entries = _canon_entries(canon)
    # Resolve every entity note's actual (collision-deduped) filename UP
    # FRONT, so the wikilinker below points at the SAME identity the
    # entity-note-writing loop later emits as a filename -- never the raw
    # source_form (review round 1: a raw source_form target doesn't
    # resolve to the emitted note, and could itself be path-like).
    # #795 §6.1: canon resolves FIRST, through a set this function now owns,
    # so `_resolve_markup_notes` can dedupe against it afterwards. Passing an
    # empty set in is behaviourally identical to the fresh one the helper
    # used to make for itself -- every canon relpath stays byte-identical to
    # a pre-#795 render, which is what keeps `validate_backlinks.py`'s own
    # independent re-derivation correct.
    used_note_paths = set()
    relpath_by_source_form = _resolve_entity_notes(entries, folders_map, used_note_paths)
    # The wikilink identity is the FOLDER-QUALIFIED relpath (minus ".md"),
    # e.g. "People/Ivan" -- NOT the bare stem (review round 2, [important]).
    # `_dedupe_path`'s `used_paths` set is shared across ALL entities for
    # this render regardless of folder, so the full relpath is already
    # globally unique; a bare stem is not (two entities in different
    # folders can share one stem, e.g. "People/Ivan.md" and
    # "Places/Ivan.md", and Obsidian's own `[[Ivan]]` resolution would then
    # be ambiguous). Obsidian wikilinks natively support a folder-qualified
    # target for exactly this disambiguation.
    note_identity_by_source_form = {
        source_form: relpath[: -len(".md")] if relpath.endswith(".md") else relpath
        for source_form, relpath in relpath_by_source_form.items()
    }

    # #588: the one-entity link groups this render was handed, validated
    # HERE -- after note resolution (so membership is checked against the
    # identities this render will actually emit) and BEFORE
    # `_clean_vault_content` below deletes anything. A rejected input must
    # never cost the operator the vault that is already on disk. Read from
    # arg 1 exactly like `nodestream["mentions"]` (assemble.py attaches both
    # before persisting, so the adapter's 4-positional-arg contract never
    # changes), and gated on the same `_is_obsidian_target` check as
    # collision de-linking itself, so the dormant
    # `obsidian`-under-`target:"custom"` CLI path stays inert.
    primary_by_source_form = _validate_link_groups(
        nodestream.get("link_groups") if collision_delink else None,
        note_identity_by_source_form,
    )

    # #795 §6.4, and here for the same reason `_validate_link_groups` is: the
    # clean below empties the existing vault, so an unresolvable sentinel
    # discovered while WRITING would leave the operator with neither the old
    # vault nor a complete new one. This walks the WHOLE NodeStream value,
    # not just the strings the pre-pass rewrites.
    if entity_markup_active:
        _entity_markup_preflight(nodestream, entity_spans, canon)

    out_dir.mkdir(parents=True, exist_ok=True)
    _clean_vault_content(out_dir)  # marker-gated; raises RenderError if unmanaged -- review round 1+2
    # #588: re-stamp the ownership marker WITHOUT a measurement the moment
    # the old vault is gone. The marker is a preserved dotfile, so a render
    # that is killed (or fails) partway would otherwise leave the PREVIOUS
    # render's `delink_cost` standing over notes it no longer describes --
    # and `validate_backlinks.py` republishes that block as the vault's own
    # number. An unmeasured marker is honest about an incomplete vault; the
    # measured one is stamped LAST, only on success.
    _stamp_vault_marker(out_dir)
    written = []

    if mentions_enabled:
        # Fail-closed, before any note is written: no canon field may
        # already carry the reserved marker token or an unsafe line-break
        # (D1, codex R5/R6 -- see the function's own docstring).
        _validate_mentions_safe_canon(entries)

    # D3 (#206/#207): collision de-linking is de-coupled from the `##
    # Mentions` appendix `enabled` flag -- a >=2-owner canonical_target_form
    # is not inline-linked on ANY real obsidian render, appendix on or off,
    # unless an operator-recorded link group covers every one of its owners
    # (#588 -- see `_link_decision`). It still gates on `_is_obsidian_target(profile)` (the same
    # target check `_effective_mentions_enabled` itself starts with), so
    # the standalone CLI's dormant-`obsidian`-under-`target:"custom"` path
    # keeps the OLD tiebreak behavior, unchanged -- D3 must stay inert
    # there exactly like D1/D4. A misattributed inline link actively
    # misleads (a click lands on the WRONG entity's note); a missing one is
    # merely recoverable (via the `## Mentions` appendix or a manual
    # search), so ambiguity always resolves toward the safer failure. See
    # build_entity_index's own docstring.
    pattern, target_to_entity = build_entity_index(
        entries, note_identity_by_source_form,
        collision_delink=collision_delink,
        primary_by_source_form=primary_by_source_form,
    )
    # #588: what de-linking actually costs this book. The owner lists come
    # from the same `_link_decision` the index above used; the OCCURRENCE
    # counts are gathered by `_Linker` itself, inside the one pass that sees
    # the real matchable text.
    delinked_owners = (
        delinked_owners_by_target(entries, primary_by_source_form)
        if collision_delink else {}
    )
    linker = _Linker(
        pattern, target_to_entity, parenthetical_mode,
        diagnostic_pattern=build_diagnostic_pattern(target_to_entity, delinked_owners),
        delinked_targets=set(delinked_owners),
    )

    # #795 §6.2/§6.3: resolve every recorded span to a wikilink, in ONE
    # pre-pass, BEFORE any renderer helper reads the text. Ordered here and
    # not earlier because it needs two things that only exist by now:
    # `target_to_entity` (which `_canon_composition` reduces to the identities
    # canon actually owns -- those link the canon note and mint nothing) and
    # `linker` (whose `global_seen` the
    # pre-pass shares, so `parenthetical_originals: first_occurrence` shows
    # its gloss once book-wide across BOTH mechanisms, and whose
    # `links_emitted` keeps counting every inline link this render inserted).
    markup_records = {}
    markup_note_relpath = {}
    entity_markup_report = None
    if entity_markup_active:
        canon_composition = _canon_composition(entity_spans, target_to_entity, entries)
        markup_records = _markup_note_records(entity_spans, canon_composition)
        markup_note_relpath = _resolve_markup_notes(
            markup_records, folders_map, used_note_paths
        )
        markup_note_identity = {
            identity: relpath[: -len(".md")] if relpath.endswith(".md") else relpath
            for identity, relpath in markup_note_relpath.items()
        }
        markup_counts = _apply_entity_markup(
            nodestream, entity_spans, canon_composition, markup_note_identity, linker
        )
        # §6.5, post-condition 1. With no bare-text branch the identity is
        # exact -- spans resolved == wikilinks emitted == len(spans) -- so it
        # is ONE comparison rather than an argument. A resolver that quietly
        # drops spans is otherwise indistinguishable from one that resolves
        # them all: the counts look plausible and every spot-check passes.
        # It is deliberately a claim about RESOLUTION, not about delivery --
        # §9's vault assertions are what prove a link reached a reader.
        if (markup_counts["replaced"] != len(entity_spans)
                or markup_counts["links"] != len(entity_spans)):
            raise RenderError(
                "entity_markup_coverage_mismatch",
                f"entity-markup resolution covered {markup_counts['replaced']} "
                f"span(s) and emitted {markup_counts['links']} wikilink(s), but "
                f"nodestream.entity_markup.spans records {len(entity_spans)} -- "
                "every recorded span must resolve to exactly one wikilink.",
            )
        entity_markup_report = {
            "spans": len(entity_spans),
            "notes": len(markup_note_relpath),
            "links": markup_counts["links"],
        }

    footnote_text_by_n = {fn["n"]: fn.get("text", "") for fn in (nodestream.get("footnotes") or [])}

    nodes_by_seg = {}
    for node in nodestream.get("nodes") or []:
        nodes_by_seg.setdefault(node["seg"], []).append(node)

    seg_order = (nodestream.get("book") or {}).get("seg_order") or []
    # Defensive: render every segment the NodeStream actually carries nodes
    # for, even one book.seg_order somehow omitted -- appended after the
    # declared order, in a stable (sorted) order of their own, rather than
    # silently dropped.
    extra_segs = sorted(set(nodes_by_seg) - set(seg_order))
    full_order = list(seg_order) + extra_segs
    # D1: this book's own reading-order position for every seg -- the
    # ordering a Mentions section's `[[NNN slug]]` links follow, NOT
    # whatever order occurrence_targets.build's Record list happens to
    # carry them in.
    seg_position = {seg: i for i, seg in enumerate(full_order)}

    # D1: seg -> the rendered segment note's OWN relpath, built here (never
    # existed before this feature -- previously `rel_path` was a loop-local
    # discarded every iteration) so the entity loop below can resolve each
    # Mentions link to the exact filename identity the segment-writing loop
    # just emitted, the same "resolve-then-reuse" discipline
    # `_resolve_entity_notes`/`relpath_by_source_form` already establishes
    # for entity notes.
    segment_note_by_seg = {}
    for idx, seg in enumerate(full_order, start=1):
        seg_nodes = sorted(nodes_by_seg.get(seg, []), key=lambda n: n["order_index"])
        # #795 §6.3: a heading's marked spans are wikilinks like any other by
        # now, and a wikilink must not survive into the frontmatter `title:`
        # or the filename slug -- but resolving heading spans to bare payload
        # instead is exactly what would reopen the wrong-note problem inside
        # a heading. Flatten instead, and only in `index` mode.
        title = _segment_title(seg_nodes, seg, flatten_wikilinks=entity_markup_active)
        slug = sanitize_filename_component(title, _stable_fallback_name(seg or str(idx), "segment"))
        rel_path = f"{idx:03d} {slug}.md"
        segment_note_by_seg[seg] = rel_path
        note_text = _render_segment_note(
            seg, seg_nodes, footnote_text_by_n, linker, is_rtl,
            flatten_wikilinks=entity_markup_active,
        )
        if entity_markup_active:
            _reject_residual_entity_tokens(rel_path, note_text)  # §6.5 post-condition 2
        _write_note(out_dir, rel_path, note_text)
        written.append(rel_path)

    # D1: only ever read when effective-enabled -- `nodestream.get(
    # "mentions")` is ignored entirely otherwise, even if a caller left
    # stale/malformed data there (e.g. the standalone CLI's `target:
    # "custom"` path), so a dormant/foreign "mentions" key can never leak
    # a Mentions section into a non-effective-enabled render.
    mentions_by_source_form = (nodestream.get("mentions") or {}) if mentions_enabled else {}

    for source_form, rel_path in relpath_by_source_form.items():
        entry = entries[source_form]
        mentions_section = None
        if mentions_enabled:
            note_identities = _mentions_note_identities(
                mentions_by_source_form.get(source_form), segment_note_by_seg, seg_position
            )
            if note_identities:
                mentions_section = _render_mentions_section(note_identities)
        note_text = _render_entity_note(source_form, entry, is_rtl, mentions_section=mentions_section)
        if entity_markup_active:
            _reject_residual_entity_tokens(rel_path, note_text)  # §6.5 post-condition 2
        _write_note(out_dir, rel_path, note_text)
        written.append(rel_path)

    # #795 §6.6: one note per markup identity that canon has no entry for.
    # Written LAST, after the canon notes, mirroring the resolution order
    # that gave them their paths -- canon first, markup deduped against it.
    for identity, rel_path in markup_note_relpath.items():
        tag, label = identity
        record = markup_records[identity]
        note_text = _render_markup_note(
            tag, label, record["aliases"], record["ref"], is_rtl
        )
        _reject_residual_entity_tokens(rel_path, note_text)  # §6.5 post-condition 2
        _write_note(out_dir, rel_path, note_text)
        written.append(rel_path)

    delink_cost = _build_delink_cost(delinked_owners, linker)
    _warn_delink_cost(delink_cost)

    # Stamp/refresh the ownership marker LAST, only after every note has
    # been written successfully -- the next render into this same out_dir
    # sees it and _clean_vault_content proceeds normally (review round 2).
    # Symlink-safe write (review round 3): see _stamp_vault_marker. A
    # dotfile: never part of `written` (diff_rendered_output.py's own
    # vault walk already skips any dotfile entry, same as .baseline/.assembled).
    # #588: this final stamp is also what binds the measured `delink_cost`
    # to a COMPLETE vault -- see the unmeasured stamp right after the clean.
    _stamp_vault_marker(out_dir, delink_cost=delink_cost)

    manifest = {"written": sorted(written), "kind": "vault", "delink_cost": delink_cost}
    if entity_markup_report is not None:
        # #795 §6.7. An extra manifest key is already accepted by
        # diff_rendered_output.py -- `delink_cost` set that precedent.
        # `brackets_escaped` is read off the linker because BOTH §7 emission
        # sites fold into that one counter.
        entity_markup_report["brackets_escaped"] = linker.brackets_escaped
        manifest["entity_markup"] = entity_markup_report
    return manifest


# ---------------------------------------------------------------------------
# Standalone CLI -- a thin wrapper for manual smoke-testing. Not part of the
# assembler's real call path (assemble.py imports and calls render()
# in-process); D's tests are expected to import render()/its helpers
# directly against a hand-authored fixture NodeStream rather than shell out
# to this CLI, per the shared build contract.
# ---------------------------------------------------------------------------

def _emit_cli_error(reason, error_message):
    """One-JSON-line error envelope for a CLI precondition failure (review
    round 1: a missing --nodestream/--canon previously exited 1 with
    stderr-only text and an empty stdout -- inconsistent with this
    plugin's own one-JSON-line-on-stdout convention). Never returns."""
    print(dumps_line({"success": False, "reason": reason, "error": error_message}))
    sys.exit(1)


def _load_json_or_die(path, kind):
    if not path.is_file():
        _emit_cli_error(f"{kind}_not_found", f"{kind} not found at {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        _emit_cli_error(f"{kind}_invalid_json", f"{kind} at {path} is not valid JSON: {exc}")


def build_arg_parser():
    parser = argparse.ArgumentParser(
        prog="render_obsidian.py",
        description=(
            "Render an assembled NodeStream into an Obsidian vault. Normally "
            "invoked in-process by assemble.py via its render(...) entry "
            "point (see references/output-target-adapters/obsidian.md); this "
            "CLI wraps the same function for standalone smoke-testing."
        ),
    )
    parser.add_argument(
        "--nodestream", type=Path, default=None,
        help=f"Path to nodestream.json (default: {NODESTREAM_PATH}).",
    )
    parser.add_argument(
        "--canon", type=Path, default=None,
        help=f"Path to canon.json (default: {CANON_PATH}).",
    )
    parser.add_argument(
        "--out-dir", type=Path, default=None,
        help="Vault output directory (default: resolved from profile.yml's output.destination, mirroring assemble.py).",
    )
    return parser


def main(argv=None):
    try:
        args = build_arg_parser().parse_args(argv)
    except SystemExit:
        # argparse's own usage-error/--help exit -- usage text is already on
        # stderr, and this is the one INTENTIONAL non-JSON exit (review
        # round 3): standard CLI usage behavior, never converted to a JSON
        # envelope. Re-raise unchanged, never double-printed.
        raise

    # Everything below is wrapped so a profile/dependency precondition
    # failure (a sibling module sys.exit()ing at import time, or
    # cache_key.load_profile()'s own sys.exit() on a bad/missing
    # profile.yml -- previously both escaped as bare stderr-only fatals,
    # review round 3) and any render() failure (a RenderError's own
    # fail-closed reason, or any other unexpected exception) all still
    # surface as one JSON line on stdout -- never a bare traceback/
    # stderr-only exit.
    try:
        nodestream_path = args.nodestream or NODESTREAM_PATH
        canon_path = args.canon or CANON_PATH
        nodestream = _load_json_or_die(nodestream_path, "nodestream")
        canon = _load_json_or_die(canon_path, "canon")

        sys.path.insert(0, str(SCRIPTS_DIR))
        try:
            import cache_key        # flat sibling import -- reuses the existing profile.yml loader
            import output_resolve   # flat sibling import -- the shared out_dir default rule
        except SystemExit as exc:
            print(dumps_line({
                "success": False,
                "reason": "dependency_precondition",
                "error": f"a sibling module failed to import (its own dependency preflight halted): {exc}",
            }))
            return 2

        try:
            profile = cache_key.load_profile(DURABLE_ROOT)
        except SystemExit as exc:
            print(dumps_line({
                "success": False,
                "reason": "profile_precondition",
                "error": f"profile.yml failed to load/validate via cache_key.load_profile (exit {exc.code})",
            }))
            return 2

        if args.out_dir is not None:
            out_dir = args.out_dir
        else:
            # resolve_out_dir now rejects a destination reached through a
            # symlinked path component (or containing '..') -- surface it as
            # this CLI's own reason-coded one-JSON-line error, not the generic
            # catch-all. Narrow try so an earlier failure can never evaluate
            # this handler against an unbound output_resolve name.
            try:
                out_dir = output_resolve.resolve_out_dir(profile, DURABLE_ROOT)
            except output_resolve.OutputResolveError as exc:
                print(dumps_line({
                    "success": False,
                    "reason": "out_dir_symlink",
                    "error": str(exc),
                }))
                return 1
        manifest = render(nodestream, canon, profile, out_dir)
    except RenderError as exc:
        print(dumps_line({"success": False, "reason": exc.reason, "error": str(exc)}))
        return 1
    except Exception as exc:  # pragma: no cover -- defensive catch-all
        print(dumps_line({"success": False, "error": f"unexpected error: {exc}"}))
        return 1

    print(dumps_line(manifest))
    return 0


if __name__ == "__main__":
    sys.exit(main())
