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
  segment's own first heading-kind node text, or the raw `seg` id if the
  segment carries no heading). Sentinels are resolved here: `⟦FNREF_N⟧`
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
from pathlib import Path

import yaml

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

DEFAULT_FOLDER = "other"

# Ownership marker (review round 2, item C1): a dotfile stamped into out_dir
# on every successful render, so _clean_vault_content can tell "a vault this
# adapter has already rendered into" (safe to clean) apart from "some other
# directory that happens to already have content" (refuse to touch). A
# dotfile so the existing dot-preserving clean keeps it across re-renders.
VAULT_MARKER_FILENAME = ".literary-translator-vault.json"


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
_PROTECTED_SPAN_RE = re.compile(r"\[\[.*?\]\]|\[\^\d+\]|⟦[^⟧]+⟧")


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


def build_entity_index(entries, note_identity_by_source_form):
    """Returns (compiled_pattern, target_to_entity) for every canon entry
    carrying a non-degenerate `canonical_target_form` -- the substring that
    actually appears in TRANSLATED body text (obsidian.md's asymmetry:
    never `source_form`, which is the original-script identity, not what
    shows up in the rendered prose).

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

    `canonical_target_form` is not guaranteed unique across entries -- the
    documented, fixed tiebreak: prefer the entry with the shortest
    `source_form`, then break ties lexicographically by `source_form`.
    Degenerate values (empty or whitespace-only) are skipped entirely --
    otherwise a blank/whitespace target would become a matcher that wraps
    the first space (or nothing) in every block (review round 1 finding).

    The compiled pattern alternates every distinct target string, LONGEST
    FIRST, so a shorter name can never shadow a longer one that contains it
    as a substring -- Python's `re` alternation tries alternatives in order
    at a given start position, so ordering longest-first is what makes that
    guarantee hold.
    """
    by_target = {}
    for source_form, entry in (entries or {}).items():
        if not isinstance(entry, dict):
            continue
        target = entry.get("canonical_target_form")
        if not target or not target.strip():
            continue
        key = (len(source_form), source_form)
        current = by_target.get(target)
        if current is None or key < current[0]:
            by_target[target] = (key, source_form)

    if not by_target:
        return None, {}

    targets_sorted = sorted(by_target, key=lambda t: (-len(t), t))
    target_to_entity = {}
    for t in targets_sorted:
        _, source_form = by_target[t]
        note_identity = note_identity_by_source_form.get(source_form, source_form)
        target_to_entity[t] = (note_identity, source_form)
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

    def link(self, text):
        if not text or self.pattern is None:
            return text

        # Protected spans (review round 1): never wrap a target that falls
        # inside an already-emitted [[...]], a [^N] footnote ref, or a raw
        # ⟦...⟧ sentinel -- computed once over this call's own text.
        protected = [(m.start(), m.end()) for m in _PROTECTED_SPAN_RE.finditer(text)]

        def _is_protected(start, end):
            return any(start < p_end and end > p_start for p_start, p_end in protected)

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


def _render_verse_block(content, linker):
    """A whole dedicated verse block (`kind: "verse"`, `mount: "block"`) --
    rendered as its own blockquote. Empty content (verse_policy.mode: skip)
    resolves to nothing at all, not an error and not a placeholder marker,
    per the shared assembler contract."""
    rendered, gloss = _verse_texts(content)
    rendered = linker.link(rendered)
    gloss = linker.link(gloss)
    body = rendered or gloss
    if not body:
        return ""
    lines = [f"> {line}".rstrip() for line in body.splitlines()]
    if rendered and gloss:
        lines.append(">")
        lines.append(f"> *Literal: {gloss}*")
    return "\n".join(lines)


def _render_verse_inline(content, linker):
    """A verse embedded inside a prose/heading block's own text (`mount`
    something other than "block", e.g. a footnote-embedded or
    quote-embedded verse) -- rendered as a compact single-line italic
    substitution in place of the placeholder, since a real blockquote
    cannot sit mid-paragraph in markdown."""
    rendered, gloss = _verse_texts(content)
    rendered = linker.link(rendered)
    gloss = linker.link(gloss)
    body = rendered or gloss
    if not body:
        return ""
    single = " / ".join(line.strip() for line in body.splitlines() if line.strip())
    out = f"*{single}*"
    if rendered and gloss:
        out += f" (lit.: {' '.join(gloss.splitlines())})"
    return out


# ---------------------------------------------------------------------------
# BlockNode -> markdown
# ---------------------------------------------------------------------------

def _render_block(node, linker):
    kind = node.get("kind")
    verses = node.get("verses") or []

    if kind == "verse":
        # A dedicated verse block IS its own verse (assemble.py's own
        # classification, contract's reconstruction algorithm step 3) --
        # render straight from its own verse entry, ignoring the raw
        # surrounding text (expected to be little more than the
        # placeholder itself).
        if not verses:
            return ""
        return _render_verse_block(verses[0].get("content") or {}, linker)

    text = node.get("text", "")
    for v in verses:
        placeholder = v.get("placeholder")
        if not placeholder:
            continue
        text = text.replace(placeholder, _render_verse_inline(v.get("content") or {}, linker))

    for n in node.get("fnrefs") or []:
        text = text.replace(_FNREF_SENTINEL_FMT.format(n=n), f"[^{n}]")

    text = linker.link(text).strip()
    if not text:
        return ""
    if kind == "heading":
        return f"## {text}"
    return text


def _segment_title(seg_nodes, seg):
    for node in seg_nodes:
        if node.get("kind") == "heading":
            text = (node.get("text") or "").strip()
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

    fn_lines = [
        f"[^{n}]: {linker.link(footnote_text_by_n.get(n, ''))}"
        for n in sorted(used_fnrefs)
    ]

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
    `sorted(entries)`, so collision order is stable across runs."""
    if base_path not in used_paths:
        used_paths.add(base_path)
        return base_path
    stem = base_path[: -len(".md")] if base_path.endswith(".md") else base_path
    n = 2
    while True:
        candidate = f"{stem}-{n}.md"
        if candidate not in used_paths:
            used_paths.add(candidate)
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


def _render_entity_note(source_form, entry, is_rtl):
    """Frontmatter mirrors canon-entry.schema.json exactly, in the field
    order obsidian.md documents, plus two adapter-computed fields:
    `aliases` (the raw `source_form`, so a reader/search can still find
    this note by its original-script identity even though the wikilink
    TARGET is now the sanitized note name -- round-trip per review round
    1) and `direction`. `note` is deliberately singular -- it mirrors
    canon-entry.schema.json's own field name, not a pluralized `notes`
    list. Entries with `basis: not_a_name` / `is_proper_name: false`
    (realia, not names) get the identical treatment -- this frontmatter
    never branches on `is_proper_name`."""
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

    entries = _canon_entries(canon)
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
    pattern, target_to_entity = build_entity_index(entries, note_identity_by_source_form)
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

    for idx, seg in enumerate(full_order, start=1):
        seg_nodes = sorted(nodes_by_seg.get(seg, []), key=lambda n: n["order_index"])
        title = _segment_title(seg_nodes, seg)
        slug = sanitize_filename_component(title, _stable_fallback_name(seg or str(idx), "segment"))
        rel_path = f"{idx:03d} {slug}.md"
        note_text = _render_segment_note(seg, seg_nodes, footnote_text_by_n, linker, is_rtl)
        _write_note(out_dir, rel_path, note_text)
        written.append(rel_path)

    for source_form, rel_path in relpath_by_source_form.items():
        entry = entries[source_form]
        note_text = _render_entity_note(source_form, entry, is_rtl)
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
