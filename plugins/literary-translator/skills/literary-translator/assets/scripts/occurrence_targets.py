#!/usr/bin/env python3
"""occurrence_targets.py -- the source-anchored occurrence-eligibility engine
behind the Obsidian adapter's opt-in `## Mentions` appendix section (RFC
appendix-backlink-integrity, 1.8.0).

## Why this module exists

The shipped `## Mentions` design (`references/output-target-adapters/
obsidian.md`) has historically treated the inline linker's own scan of
*translated* prose (`build_entity_index`/`_Linker`, `render_obsidian.py:
255-466`) AS the occurrence index. That model silently drops any occurrence
whose translated surface differs from the one `canonical_target_form` the
linker matches (#206), and collapses two distinct `source_form`s that
happen to share a target string (#207-a). This module fixes both by
deriving the occurrence universe from the *source* side instead --
`bootstrap_names.extract_candidate_spans()`, the same offset-preserving
production tokenizer/matcher `occ_index.production_occurrences()`/
`evidence_verify.py`/`suspicion_scan.py` already trust -- so a Mentions link
never depends on how (or whether) a given occurrence's translated surface
happens to spell the name.

## Entry point

    build(manifest, canon, senses_result, language_config, nodestream) -> {
        "eligible_by_source_form": { source_form: [ Record, ... ] },
        "unresolved_homonyms":     { source_form: {"count": int, "segs": [seg, ...]} },
    }
    Record = { source_form, seg, origin ∈ {"block", "embedded_verse", "footnote"},
               source_block, vid?, footnote_n? }

`manifest`/`canon`/`nodestream` are already-parsed dicts (the exact shapes
`assemble.py` itself builds/loads before `dispatch_adapter`); `senses_result`
is a `canon_senses.SensesResult` (from `load_senses(..., allow_absent=True)`);
`language_config` is a resolved `bootstrap_names.LanguageConfig` (there is no
default -- the production matcher's spans are configuration-dependent, see
`bootstrap_names.extract_candidate_spans`'s own docstring, and
`occ_index.production_occurrences`'s, which wraps the same call).

This is the ONE place eligibility is decided (codex R5 b1 / R4 b1): both
`assemble.py` (which attaches `nodestream["mentions"] =
aggregate["eligible_by_source_form"]`) and `validate_backlinks.py` (which
re-derives the whole aggregate fresh, never trusting the persisted one) call
this same function, so the persisted mentions, a from-scratch rebuild, and
the rendered `## Mentions` sections are one identical `(source_form, seg)`
universe.

## Eligibility, two independent layers

1. **Canon-entry eligibility** (`entry_is_index_eligible`) -- excludes only
   the two ways a canon entry declares itself NOT identity-bearing:
   `basis == "not_a_name"` or `is_proper_name is False`. Mirrors
   `suspicion_scan.py`'s own `_in_scope` predicate exactly.
   `basis == "sense_translated"` stays ELIGIBLE here even though the inline
   linker (`build_entity_index`) deliberately skips it -- the schema forces
   `is_proper_name: true` for it, and the linker's skip is only about
   *unanchored target-text* auto-linking being unsafe for an
   ordinary-word rendering ("Hope", "Wolf"); the source-anchored index is
   exactly where such speaking names get an authoritative Mentions list
   safely.
2. **Occurrence-render eligibility** (`block_renders_nonempty`/
   `verse_renders_nonempty`, per origin) -- a source occurrence only
   produces a Record if the place it would link TO actually renders
   non-empty content: not an omitted/regenerate-placeholder block, not a
   skip-mode-empty verse (block-mount OR embedded), not a footnote whose
   nodestream text is empty. This is resolved against the ASSEMBLED
   NodeStream's own node/verse inventory, never guessed from the raw
   manifest alone.

`is_split(senses_result, source_form)` source_forms (canon_senses.json
homonym splits, >=2 senses) are held out of `eligible_by_source_form`
entirely -- every one of their (render-eligible) occurrences instead
accumulates in `unresolved_homonyms` (count + segs), since there is no
per-sense note to route an individual occurrence to (#207-b, gate-report-
only in 1.8.0).

## Verse eligibility -- the exact renderer rule, imported not reimplemented

`verse_renders_nonempty(content) = any(_verse_texts(content))` -- BYTE FOR
BYTE the renderer's own empty rule, imported directly from
`render_obsidian` (never reimplemented -- a parity test in
`occurrence_targets.test.py` asserts the two can never drift).

A block-mount (standalone) verse's occurrence is resolved via
`block_renders_nonempty`, keyed on the SOURCE block's own verse-mount claim
in `manifest["verse"]["store"]` -- NEVER on the assembled node's classified
`kind`. Declared-heading precedence (`assemble._classify_kind`, #210) can
make a `mount:"block"` verse whose block also carries a declared heading
render as a HEADING node that the renderer drops to `""` when the verse
content is skip-empty; branching on `kind == "verse"` would wrongly treat
that as an ordinary (non-verse) node and emit a phantom mention. Checking
the manifest's own mount claim instead is immune to that.

An embedded verse (`mount == "embedded"`) is eligible only if its OWN
content renders non-empty AND its carrier is not a footnote-definition
block. Both conditions collapse into ONE check here for free: a footnote-
definition block is never listed in any segment's `block_ids[]`
(`assemble.py` raises `AssembleError` if it ever is), so it never becomes an
ordinary NodeStream node, so an embedded verse whose sole carrier is such a
block is simply never present in ANY node's `verses[]` inventory --
`content_by_vid` (built entirely from `nodestream["nodes"][*]["verses"]`)
has no entry for it, and `verse_renders_nonempty(None)` is `False` by
construction (`_verse_texts` treats `content=None` as `{}`). The same
absence-is-ineligible rule covers a malformed/dangling `parent_block`
(never guessed).
"""
import sys
from collections import defaultdict
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
DURABLE_ROOT = SCRIPT_DIR.parent

try:
    from bootstrap_names import extract_candidate_spans
except ImportError as exc:
    sys.exit(
        f"occurrence_targets.py: cannot import bootstrap_names.py from {SCRIPT_DIR} ({exc}).\n"
        "bootstrap_names.py must be installed alongside occurrence_targets.py under "
        "${durable_root}/scripts/ -- it supplies extract_candidate_spans(), the "
        "offset-preserving production tokenizer/matcher this module reuses (never "
        "reimplements) -- the SAME entry point occ_index.production_occurrences() "
        "itself calls (occ_index.py's own _run_spans). Called directly (rather than "
        "via production_occurrences) so ONE extraction pass over a given text can "
        "be grouped by name and shared across every canon source_form looked up "
        "against it, instead of re-tokenizing the same text once per source_form. "
        "Re-run Step 0a, or verify the plugin install is not corrupted."
    )

try:
    from canon_senses import is_split
except ImportError as exc:
    sys.exit(
        f"occurrence_targets.py: cannot import canon_senses.py from {SCRIPT_DIR} ({exc}).\n"
        "canon_senses.py must be installed alongside occurrence_targets.py under "
        "${durable_root}/scripts/ -- it supplies is_split(), the homonym-split "
        "predicate this module reuses (never reimplements). Re-run Step 0a, or "
        "verify the plugin install is not corrupted."
    )

try:
    from render_obsidian import _verse_texts
except ImportError as exc:
    sys.exit(
        f"occurrence_targets.py: cannot import render_obsidian.py from {SCRIPT_DIR} ({exc}).\n"
        "render_obsidian.py must be installed alongside occurrence_targets.py "
        "under ${durable_root}/scripts/ -- it supplies _verse_texts(), the exact "
        "verse-empty-content rule this module must mirror byte-for-byte (never "
        "reimplement) so a verse eligibility decision here can never drift from "
        "what the renderer itself would actually emit. Re-run Step 0a, or verify "
        "the plugin install is not corrupted."
    )


# ---------------------------------------------------------------------------
# Per-text span grouping -- ONE extraction pass per source text, regardless of
# canon size. Mirrors occ_index.index_manifest's own single-pass-per-block
# optimization (that function's docstring: "call the expensive tokenizer/
# run-building pass exactly ONCE per block -- never once per (block,
# source_form) pair"). The three per-origin scanners below each hand every
# text they touch through this ONE helper instead of calling the matcher once
# per (text, source_form) pair -- O(texts), never O(texts x canon entries).
# ---------------------------------------------------------------------------

def _spans_by_name(text: str, language_config) -> dict:
    """Every matcher-emitted run in `text`, grouped by `name` -- built from
    exactly ONE call to `extract_candidate_spans()` (the same entry point
    `occ_index.production_occurrences()` itself wraps), regardless of how
    many source_forms are subsequently looked up against the result. Each
    name's own spans stay in `char_start` order (`extract_candidate_spans`
    already returns its full list sorted that way)."""
    grouped = defaultdict(list)
    for name, _mid_sentence, char_start, char_end in extract_candidate_spans(text, language_config):
        grouped[name].append((char_start, char_end))
    return grouped


# ---------------------------------------------------------------------------
# Canon-entry eligibility -- the ONE predicate every category-based inclusion/
# exclusion in the Mentions universe goes through.
# ---------------------------------------------------------------------------

def entry_is_index_eligible(entry: dict) -> bool:
    """True unless `entry` is explicitly `basis: "not_a_name"` or
    `is_proper_name: false` -- the two ways a canon entry declares itself NOT
    identity-bearing. Mirrors `suspicion_scan.py`'s own `_in_scope`
    (`canon_adjudication_audit.py`'s underlying entity-merge exclusion).
    `basis: "sense_translated"` is deliberately NOT excluded here -- see this
    module's own docstring."""
    return entry.get("is_proper_name") is not False and entry.get("basis") != "not_a_name"


# ---------------------------------------------------------------------------
# Verse render-eligibility -- the renderer's own empty rule, imported not
# reimplemented.
# ---------------------------------------------------------------------------

def verse_renders_nonempty(content) -> bool:
    """`any(_verse_texts(content))` -- byte-for-byte the renderer's own
    empty-verse rule (`render_obsidian._verse_texts`, imported directly).
    `content is None` (no claim found at all -- a dangling/unresolved verse,
    or a footnote-embedded verse's content, which is never carried into any
    node's `verses[]`) is handled by `_verse_texts` itself (`content or {}`)
    and correctly yields `False` here without any special-casing."""
    return any(_verse_texts(content))


# ---------------------------------------------------------------------------
# Render index -- one pass over the assembled NodeStream + the manifest's own
# verse-store/footnote relations, giving block/verse/footnote eligibility O(1)
# lookups instead of re-scanning the NodeStream per block/verse/footnote.
# ---------------------------------------------------------------------------

def build_render_index(manifest: dict, nodestream: dict) -> dict:
    """Precomputes everything `block_renders_nonempty`/the per-origin record
    scanners need from the assembled NodeStream + the manifest's verse-store/
    footnote relations:

      - `node_by_block_id`: `nodestream["nodes"][*]["id"] -> node`. A block
        id absent here means the block became no node at all (frontback
        `omit`, or -- structurally -- a footnote-definition block, which
        `assemble.py` never allows into any segment's own `block_ids[]`).
      - `content_by_vid`: `verses[*]["vid"] -> verses[*]["content"]`, built
        from EVERY node's `verses[]` (both a block-mount verse's own carrier
        node and an embedded verse's separate carrier node contribute here
        identically). A vid absent here means no node ever claimed it --
        either its sole carrier is a footnote-definition block (never a
        node) or its `parent_block` is dangling/malformed.
      - `standalone_verse_by_parent_block`: `manifest["verse"]["store"]`
        entries with `mount == "block"`, keyed by `parent_block` -- the
        SOURCE-side claim `block_renders_nonempty` keys its verse check on,
        never the assembled node's own classified `kind` (see module
        docstring).
      - `footnote_text_by_n`: `nodestream["footnotes"][*]["n"] ->
        [*]["text"]` -- the assembled (already-stripped) footnote definition
        text, keyed by footnote number.
    """
    nodes = (nodestream or {}).get("nodes") or []

    node_by_block_id = {}
    content_by_vid = {}
    for node in nodes:
        if not isinstance(node, dict):
            continue
        block_id = node.get("id")
        if block_id is not None:
            node_by_block_id[block_id] = node
        for claim in node.get("verses") or []:
            if isinstance(claim, dict) and claim.get("vid") is not None:
                content_by_vid[claim["vid"]] = claim.get("content")

    verse_store = ((manifest or {}).get("verse") or {}).get("store") or []
    standalone_verse_by_parent_block = {}
    for entry in verse_store:
        if isinstance(entry, dict) and entry.get("mount") == "block":
            parent_block = entry.get("parent_block")
            if parent_block is not None:
                standalone_verse_by_parent_block[parent_block] = entry

    footnote_text_by_n = {
        fn["n"]: fn.get("text", "")
        for fn in ((nodestream or {}).get("footnotes") or [])
        if isinstance(fn, dict) and "n" in fn
    }

    return {
        "node_by_block_id": node_by_block_id,
        "content_by_vid": content_by_vid,
        "standalone_verse_by_parent_block": standalone_verse_by_parent_block,
        "footnote_text_by_n": footnote_text_by_n,
    }


def block_renders_nonempty(block_id, render_index: dict) -> bool:
    """True iff `block_id` (a `manifest["blocks"]` key) renders non-empty
    content in the assembled vault -- the block-origin eligibility gate,
    D2's table:

      - absent from the NodeStream node inventory (frontback `omit`, or a
        footnote-definition block, which never becomes an ordinary node) ->
        False.
      - the node's `raw_type == "FRONTBACK_REGENERATE_PLACEHOLDER"` ->
        False (real translated text is absent; only documented placeholder
        prose stands in for it).
      - the block is claimed by a standalone (`mount: "block"`) verse in
        `manifest["verse"]["store"]` -> `verse_renders_nonempty` of THAT
        verse's assembled content, regardless of the node's own classified
        `kind` (declared-heading precedence, #210, can make this a HEADING
        node -- see module docstring).
      - else -> True (an ordinary prose/heading node always renders its own
        `text` verbatim; there is no other empty-content case at block
        granularity).
    """
    node = render_index["node_by_block_id"].get(block_id)
    if node is None:
        return False
    if node.get("raw_type") == "FRONTBACK_REGENERATE_PLACEHOLDER":
        return False
    verse_claim = render_index["standalone_verse_by_parent_block"].get(block_id)
    if verse_claim is not None:
        content = render_index["content_by_vid"].get(verse_claim.get("vid"))
        return verse_renders_nonempty(content)
    return True


# ---------------------------------------------------------------------------
# Per-origin record scanners. Each emits one Record per matcher-authenticated
# occurrence (extract_candidate_spans' own per-span granularity, filtered to
# each eligible source_form) -- never deduplicated here; that's the
# renderer's job (dedup per note).
# ---------------------------------------------------------------------------

def _block_records(source_forms, manifest: dict, language_config, render_index: dict) -> list:
    records = []
    blocks = (manifest or {}).get("blocks") or {}
    for block_id, block in blocks.items():
        if not isinstance(block, dict):
            continue
        text = block.get("plain_text", "")
        if not text or not text.strip():
            continue
        if not block_renders_nonempty(block_id, render_index):
            continue
        seg = render_index["node_by_block_id"][block_id].get("seg")
        spans_by_name = _spans_by_name(text, language_config)
        for source_form in source_forms:
            for _ in spans_by_name.get(source_form, ()):
                records.append({
                    "source_form": source_form,
                    "seg": seg,
                    "origin": "block",
                    "source_block": block_id,
                })
    return records


def _embedded_verse_records(source_forms, manifest: dict, language_config, render_index: dict) -> list:
    records = []
    verse_store = ((manifest or {}).get("verse") or {}).get("store") or []
    for entry in verse_store:
        if not isinstance(entry, dict) or entry.get("mount") != "embedded":
            continue  # standalone or mount-absent -- the block scan owns those
        vid = entry.get("vid")
        content = render_index["content_by_vid"].get(vid)
        if not verse_renders_nonempty(content):
            continue  # covers BOTH skip-empty content AND a footnote-def/dangling carrier
        plain_text = entry.get("plain_text")
        if not isinstance(plain_text, str) or not plain_text:
            continue
        parent_block = entry.get("parent_block")
        carrier_node = render_index["node_by_block_id"].get(parent_block)
        if carrier_node is None:
            continue  # unresolved carrier -- never guessed a seg
        seg = carrier_node.get("seg")
        spans_by_name = _spans_by_name(plain_text, language_config)
        for source_form in source_forms:
            for _ in spans_by_name.get(source_form, ()):
                records.append({
                    "source_form": source_form,
                    "seg": seg,
                    "origin": "embedded_verse",
                    "source_block": parent_block,
                    "vid": vid,
                })
    return records


def _footnote_records(source_forms, manifest: dict, language_config, render_index: dict) -> list:
    records = []
    blocks = (manifest or {}).get("blocks") or {}
    for fn_entry in (manifest or {}).get("footnotes") or []:
        if not isinstance(fn_entry, dict):
            continue
        n = fn_entry.get("n")
        def_block = fn_entry.get("def_block")
        # Eligibility reads the ASSEMBLED nodestream's own footnote text --
        # never manifest.footnotes/draft text -- so a footnote the renderer
        # itself voided (e.g. an unresolvable apparatus_policy case) can
        # never produce a phantom mention (mutation: "read relation from
        # nodestream -> unresolved" tests the anchor_seg half of this same
        # split; this half tests the eligibility source).
        if not render_index["footnote_text_by_n"].get(n):
            continue
        block = blocks.get(def_block)
        if not isinstance(block, dict):
            continue
        block_text = block.get("plain_text", "")
        if not block_text or not block_text.strip():
            continue
        anchor_seg = fn_entry.get("anchor_seg")
        spans_by_name = _spans_by_name(block_text, language_config)
        for source_form in source_forms:
            for _ in spans_by_name.get(source_form, ()):
                records.append({
                    "source_form": source_form,
                    "seg": anchor_seg,
                    "origin": "footnote",
                    "source_block": def_block,
                    "footnote_n": n,
                })
    return records


# ---------------------------------------------------------------------------
# build() -- the single entry point.
# ---------------------------------------------------------------------------

def build(manifest: dict, canon: dict, senses_result, language_config, nodestream: dict) -> dict:
    entries = (canon or {}).get("entries") or {}
    render_index = build_render_index(manifest, nodestream)

    source_forms = [
        source_form
        for source_form, entry in entries.items()
        if isinstance(entry, dict) and entry_is_index_eligible(entry)
    ]

    records_by_source_form = defaultdict(list)
    for rec in _block_records(source_forms, manifest, language_config, render_index):
        records_by_source_form[rec["source_form"]].append(rec)
    for rec in _embedded_verse_records(source_forms, manifest, language_config, render_index):
        records_by_source_form[rec["source_form"]].append(rec)
    for rec in _footnote_records(source_forms, manifest, language_config, render_index):
        records_by_source_form[rec["source_form"]].append(rec)

    eligible_by_source_form = {}
    unresolved_homonyms = {}
    for source_form in source_forms:
        records = records_by_source_form.get(source_form) or []
        if not records:
            continue  # zero source occurrences -- nothing to route anywhere
        if is_split(senses_result, source_form):
            unresolved_homonyms[source_form] = {
                "count": len(records),
                "segs": [rec["seg"] for rec in records],
            }
        else:
            eligible_by_source_form[source_form] = records

    return {
        "eligible_by_source_form": eligible_by_source_form,
        "unresolved_homonyms": unresolved_homonyms,
    }
