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
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import unicodedata
from collections import defaultdict
from pathlib import Path

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
# alphanumeric, str.isalnum(), plus a small curated punctuation set) rather
# than a denylist: everything else -- including every path separator, ".",
# control/NUL bytes -- is replaced, never merely blocked after the fact.
# Dropping "." entirely also means a run of them (a ".." traversal segment)
# can never survive sanitization.
_FILENAME_EXTRA_CHARS = " _-()'"

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


def build_entity_index(entries, note_identity_by_source_form, collision_delink=False):
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
    a `canonical_target_form` with >=2 owners is NEVER inline-linked, ever
    -- a misattributed inline link actively misleads (a reader clicks
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
    Degenerate values (empty or whitespace-only) are skipped entirely --
    otherwise a blank/whitespace target would become a matcher that wraps
    the first space (or nothing) in every block (review round 1 finding).

    `basis: "sense_translated"` entries (#138) never WIN the tiebreak and
    never get an inline auto-link -- deliberately, and unlike every other
    basis. A sense-rendering is an ordinary word BY CONSTRUCTION ("Hope",
    "Wolf"), so the unanchored, no-word-boundary alternation below would
    otherwise wikilink every incidental occurrence of that word in the
    prose, not just the entity's own mentions. The entity note itself is
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

    The compiled pattern alternates every distinct target string, LONGEST
    FIRST, so a shorter name can never shadow a longer one that contains it
    as a substring -- Python's `re` alternation tries alternatives in order
    at a given start position, so ordering longest-first is what makes that
    guarantee hold.
    """
    # Every owner of each normalized target, UNREDUCED -- order within a
    # list follows `entries` iteration order (immaterial: both the
    # tiebreak and the delink check below are order-independent).
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
        key = (len(source_form), source_form)
        # `basis` carried alongside (#240) so the sense_translated exclusion
        # can be applied AFTER the collision tally below, not before it --
        # see this function's own docstring.
        owners_by_target[target].append((key, source_form, entry.get("basis")))

    by_target = {}
    for target, owners in owners_by_target.items():
        if collision_delink and len(owners) >= 2:
            continue  # >=2 owners of ANY basis, delinked entirely -- no inline link for this string at all
        survivors = [
            (key, source_form) for key, source_form, basis in owners
            if basis != "sense_translated"
        ]
        if not survivors:
            continue  # every owner is sense_translated -- never auto-linked, drop the target entirely
        _, winner_source_form = min(survivors)  # shortest source_form, then lexicographic, sense_translated excluded
        by_target[target] = winner_source_form

    if not by_target:
        return None, {}

    targets_sorted = sorted(by_target, key=lambda t: (-len(t), t))
    target_to_entity = {}
    for t in targets_sorted:
        source_form = by_target[t]
        note_identity = note_identity_by_source_form.get(source_form, source_form)
        target_to_entity[t] = (note_identity, source_form)
    # #206: this is a conservative verbatim same-surface affordance --
    # case-sensitive, no morphology, no identity call -- never the
    # authoritative occurrence index; that is the default-on
    # source-anchored `## Mentions` appendix (see obsidian.md).
    pattern = re.compile("|".join(re.escape(t) for t in targets_sorted))
    return pattern, target_to_entity


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
    """

    def __init__(self, pattern, target_to_entity, parenthetical_mode):
        self.pattern = pattern
        self.target_to_entity = target_to_entity  # target -> (note_identity, source_form)
        self.parenthetical_mode = parenthetical_mode
        self.global_seen = set()

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
        if not text or self.pattern is None:
            return text

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
        for m in self.pattern.finditer(text):
            if _is_protected(m.start(), m.end()):
                continue  # inside a protected span -- leave untouched, don't count as "seen"
            target = m.group(0)
            if target in seen_in_block:
                continue
            seen_in_block.add(target)
            out.append(text[last:m.start()])
            note_identity, source_form = self.target_to_entity[target]
            piece = f"[[{note_identity}|{target}]]"
            if self.parenthetical_mode == "first_occurrence" and target not in self.global_seen:
                piece += f" ({source_form})"
            self.global_seen.add(target)
            out.append(piece)
            last = m.end()
        out.append(text[last:])
        return "".join(out)


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
        return f"## {text}"
    return text


def _heading_plain_text(node):
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
    whitespace collapse)."""
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
    if not substitutions and not _TITLE_FNREF_ANCHOR_RE.search(original):
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
    return re.sub(r"\s+", " ", text).strip()


def _segment_title(seg_nodes, seg):
    for node in seg_nodes:
        if node.get("kind") == "heading":
            text = _heading_plain_text(node)
            if text:
                return text
    return seg


def _render_segment_note(seg, seg_nodes, footnote_text_by_n, linker, is_rtl):
    title = _segment_title(seg_nodes, seg)
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
    never a denylist: every character that is not `str.isalnum()` or in the
    small curated punctuation set is replaced with "_", not merely rejected
    after the fact via a blocklist of "dangerous" characters. "." is
    deliberately excluded from the allowed set (not just ".."), which also
    means a filename can never end up carrying a file extension of its own
    -- this script always appends ".md" itself."""
    if not isinstance(value, str) or not value:
        return fallback
    kept = "".join(ch if (ch.isalnum() or ch in _FILENAME_EXTRA_CHARS) else "_" for ch in value)
    kept = re.sub(r"_+", "_", kept).strip("_ ")
    if not kept or kept in (".", ".."):
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


def _resolve_entity_notes(entries, folders_map):
    """Resolves every entry's note relpath (folder/stem.md) UP FRONT, in
    the same `sorted(entries)` order the entity-note-writing loop uses --
    so the wikilink identity used while rendering narrative pages (via
    `build_entity_index`/`_Linker`) is guaranteed IDENTICAL to the actual
    filename the writing loop emits later, collision-dedup included
    (review round 1: the link target and the emitted filename must be the
    same string, or the link never resolves to the note). Returns
    {source_form: relpath}; the writing loop reuses this same mapping
    rather than re-resolving (and re-deduping) a second time."""
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


def _marker_payload():
    return {"managed_by": "literary-translator", "target": "obsidian"}


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


def _stamp_vault_marker(out_dir):
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
            f.write(json.dumps(_marker_payload()) + "\n")
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
    the symlink guard immediately below (`out_dir_is_symlink`)."""
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
    out_dir.mkdir(parents=True, exist_ok=True)
    _clean_vault_content(out_dir)  # marker-gated; raises RenderError if unmanaged -- review round 1+2
    written = []

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

    entries = _canon_entries(canon)
    if mentions_enabled:
        # Fail-closed, before any note is written: no canon field may
        # already carry the reserved marker token or an unsafe line-break
        # (D1, codex R5/R6 -- see the function's own docstring).
        _validate_mentions_safe_canon(entries)
    # Resolve every entity note's actual (collision-deduped) filename UP
    # FRONT, so the wikilinker below points at the SAME identity the
    # entity-note-writing loop later emits as a filename -- never the raw
    # source_form (review round 1: a raw source_form target doesn't
    # resolve to the emitted note, and could itself be path-like).
    relpath_by_source_form = _resolve_entity_notes(entries, folders_map)
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
    # D3 (#206/#207): collision de-linking is de-coupled from the `##
    # Mentions` appendix `enabled` flag -- a >=2-owner canonical_target_form
    # is never inline-linked on ANY real obsidian render, appendix on or
    # off. It still gates on `_is_obsidian_target(profile)` (the same
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
        collision_delink=_is_obsidian_target(profile),
    )
    linker = _Linker(pattern, target_to_entity, parenthetical_mode)

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
        title = _segment_title(seg_nodes, seg)
        slug = sanitize_filename_component(title, _stable_fallback_name(seg or str(idx), "segment"))
        rel_path = f"{idx:03d} {slug}.md"
        segment_note_by_seg[seg] = rel_path
        note_text = _render_segment_note(seg, seg_nodes, footnote_text_by_n, linker, is_rtl)
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
        _write_note(out_dir, rel_path, note_text)
        written.append(rel_path)

    # Stamp/refresh the ownership marker LAST, only after every note has
    # been written successfully -- the next render into this same out_dir
    # sees it and _clean_vault_content proceeds normally (review round 2).
    # Symlink-safe write (review round 3): see _stamp_vault_marker. A
    # dotfile: never part of `written` (diff_rendered_output.py's own
    # vault walk already skips any dotfile entry, same as .baseline/.assembled).
    _stamp_vault_marker(out_dir)

    return {"written": sorted(written), "kind": "vault"}


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
    print(json.dumps({"success": False, "reason": reason, "error": error_message}, ensure_ascii=False))
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
            print(json.dumps({
                "success": False,
                "reason": "dependency_precondition",
                "error": f"a sibling module failed to import (its own dependency preflight halted): {exc}",
            }, ensure_ascii=False))
            return 2

        try:
            profile = cache_key.load_profile(DURABLE_ROOT)
        except SystemExit as exc:
            print(json.dumps({
                "success": False,
                "reason": "profile_precondition",
                "error": f"profile.yml failed to load/validate via cache_key.load_profile (exit {exc.code})",
            }, ensure_ascii=False))
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
                print(json.dumps({
                    "success": False,
                    "reason": "out_dir_symlink",
                    "error": str(exc),
                }, ensure_ascii=False))
                return 1
        manifest = render(nodestream, canon, profile, out_dir)
    except RenderError as exc:
        print(json.dumps({"success": False, "reason": exc.reason, "error": str(exc)}, ensure_ascii=False))
        return 1
    except Exception as exc:  # pragma: no cover -- defensive catch-all
        print(json.dumps({"success": False, "error": f"unexpected error: {exc}"}, ensure_ascii=False))
        return 1

    print(json.dumps(manifest, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
