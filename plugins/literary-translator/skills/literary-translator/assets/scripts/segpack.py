#!/usr/bin/env python3
"""Per-segment translator-input builder (generalized from historiettes-t3's segpack.py).

Given a segment id (or --all, every candidate segment), assembles from
manifest.json + canon.json everything a translator/reviewer needs for that one
segment: its blocks in reading order (with sentinel placeholders intact), the
footnote definitions it references (branching on footnotes.apparatus_policy),
the verses it contains (parented to this segment's own blocks, or embedded in
one of its referenced footnotes), the distinct proper-noun candidates found in
it, and the canon.json split of those candidates into locked (canon_names) vs
unresolved (new_names) forms. Deterministic; no LLM.

Output shape: ${durable_root}/segments/segpack_{seg}.json -- matches
segpack.schema.json exactly. Every output is walked through a hand-rolled,
dependency-free structural self-check against that same shape (no jsonschema
import -- matching the real historiettes-t3 project's own segpack.py /
validate_draft.py, which import only json/os/re/sys) before being written to
disk. A missing OR invalid segpack for any candidate segment is a FATAL W3a
preflight error -- this script's own exit code enforces that: any assembly or
validation failure aborts with a non-zero exit, naming every offending
segment, never a partial/best-effort write.

Self-anchoring: this script is always installed at ${durable_root}/scripts/
segpack.py by Step 0a, so its own durable_root is exactly
Path(__file__).resolve().parents[1] -- there is deliberately no --durable-root
flag and no reliance on cwd.

CONTRACT with bootstrap_names.py (sibling script, same scripts/ directory --
also copied to ${durable_root}/scripts/ by Step 0a, and part of the same
derivation_bundle_hash pairing as this script):
    load_language_config(particle_config_filename: str, languages_dir: Path) -> LanguageConfig
        Reads ${languages_dir}/{particle_config_filename} (a BARE filename,
        resolved exactly like bootstrap_names.py's own CLI resolution logic --
        never reconstructed from source.language.code) and returns the parsed
        particle/stopword/elision language-config object.
    extract_candidates(text: str, lang_config: dict) -> list[tuple[str, bool]]
        Yields (name, mid_sentence) pairs for each proper-noun run found in
        `text`, using the tokenizer / run-building / frequency-and-mid-sentence
        scoring algorithm, parameterized entirely by `lang_config` (never
        hardcoded per-language data -- that is bootstrap_names.py's own job).
        Sentinel masking (FNREF_N / VERSE_Vxxx placeholders) happens INSIDE
        this call now (#226) -- a same-length substitution
        (bootstrap_names.mask_sentinels) that never shifts a token's own
        offset, unlike the collapsing single-space substitution this script
        used to apply to raw text BEFORE calling extract_candidates. segpack.py
        passes raw block/footnote text straight through; it never pre-strips
        sentinels itself.

Usage:
    python3 segpack.py SEG --particle-config fr.json --apparatus-policy translate_all
    python3 segpack.py --all --particle-config fr.json --apparatus-policy body_refs_only

Both --particle-config and --apparatus-policy are supplied by the orchestrating
Claude session, which reads profile.yml directly (source.language.particle_config
and footnotes.apparatus_policy) and threads the resolved literal values through
as plain CLI strings -- this script never parses profile.yml/YAML itself.
"""
import argparse
import json
import re
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
DURABLE_ROOT = SCRIPT_DIR.parent

try:
    from bootstrap_names import load_language_config, extract_candidates
except ImportError as exc:
    sys.exit(
        f"segpack.py: cannot import bootstrap_names.py from {SCRIPT_DIR} ({exc}).\n"
        "bootstrap_names.py must be installed alongside segpack.py under "
        "${durable_root}/scripts/ -- Step 0a copies both scripts together as a "
        "pair (they share the derivation_bundle_hash). It supplies "
        "load_language_config / extract_candidates, the shared name-candidate "
        "extraction primitives segpack.py reuses per segment. Re-run Step 0a, "
        "or verify the plugin install is not corrupted."
    )

# Canonical segment-id safety contract. A seg id is either an ordinary body
# id (e.g. "seg01", "seg05_blocked_regen", "segAnchor") or a translate-decision
# FRONTBACK:{id} unit (e.g. "FRONTBACK:fm01"). It is spliced into filesystem
# paths and workflow shell commands, so it MUST be a path- and shell-safe
# allowlist. Keep this identical across every consuming script.
# NOTE: re.fullmatch (NOT re.match + "$") -- in Python "$" also matches just
# before a trailing newline, so re.match(r"...$", "seg01\n") would WRONGLY pass.
_SEG_ID_RE = re.compile(r"(?:FRONTBACK:)?[A-Za-z0-9_]+")


def validate_seg(seg):
    """Return an error string if `seg` is not a path/shell-safe segment id,
    else None. Allows ONLY [A-Za-z0-9_] with an optional literal 'FRONTBACK:'
    prefix -- rejecting empties, path separators, '..', absolute paths, and
    every shell metacharacter."""
    if not isinstance(seg, str) or not seg:
        return "segment id must be a non-empty string."
    if not _SEG_ID_RE.fullmatch(seg):
        return (
            "segment id must match (FRONTBACK:)?[A-Za-z0-9_]+ (no path "
            f"separators, '..', or shell metacharacters); got {seg!r}."
        )
    return None


APPARATUS_POLICIES = ("translate_all", "preserve_source", "omit_apparatus", "body_refs_only")
FOOTNOTE_CARRYING_POLICIES = ("translate_all", "preserve_source")

# Cross-check only: FNREF sentinel occurrences found directly in block text,
# compared against the manifest's own per-block fnrefs[] list as a cheap
# "does the recorded data agree with the text" sanity signal (WARN, not FATAL
# -- a real mismatch here is a manifest bug (e.g. in the gutenberg_epub
# adapter's own extract.py.template extraction pass, or the equivalent
# producer for whichever adapter built this manifest) worth surfacing
# loudly, but not this script's job to adjudicate).
FNREF_RE = re.compile(r"⟦FNREF_(\d+)⟧")

# Fallback literal-marker detector for apparatus_policy: body_refs_only, matching
# the illustrative convention documented in
# references/source-format-adapters/gutenberg-epub.md (a bare "[N]" baked
# verbatim into the block's own plain text in place of the stripped FNREF
# sentinel). Used ONLY when the manifest block itself does not already carry
# an explicit body_ref_markers list to pass through unchanged.
LITERAL_MARKER_RE = re.compile(r"\[\d+\]")

_TAG_RE = re.compile(r"<[^>]+>")


def _split_lf_lines(s):
    r"""Line-split on LF ONLY -- NOT str.splitlines(), which also breaks on
    the exotic Unicode boundaries U+2028/U+2029/U+0085/U+000B/U+000C/
    U+001C-U+001E. Mirrors validate_draft.py's own _split_lf_lines (#188) so
    this script's LEGACY-manifest fallback (below) counts lines the SAME way
    the validator will -- a manifest's own plain_text may legitimately carry
    a real U+2028 (e.g. a verse-payload sentinel join) that str.splitlines()
    would wrongly count as a second line (#192). DUPLICATED, not imported --
    segpack.py has no dependency on validate_draft.py and this train's
    convention (A-C4) is independent copies over a shared import for a
    small, stable helper. Preserves splitlines()'s own "a single trailing
    line terminator yields no empty trailing element"."""
    lines = (s or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def _verse_line_count(v):
    """Source line count for one manifest verse.store node -- feeds
    validate_draft.py check 5's per-verse line policy (mixed_by_length; the
    multi-line-source -> non-single-line-rendering guard).

    Thread the extractor's authoritative n_line DIRECTLY. Post-#92
    verse_payload counts a bare-<p> stanza's lines (mirrors verse_plain)
    BEFORE normalize_text collapses newlines, so a manifest n_line is
    correct for both `.line`-marked and bare-<p> poems. A missing/non-
    positive n_line means a LEGACY manifest (pre-#92 extractor); fall back
    to plain_text / source_html non-blank line counts as best-effort, LF-only
    (`_split_lf_lines`, #192 -- NOT str.splitlines(), which also breaks on
    U+2028 and the other exotic Unicode line boundaries a real plain_text may
    legitimately contain) -- but note plain_text has ALREADY been
    newline-collapsed by normalize_text for a legacy bare-<p> stanza, so that
    fallback under-counts it (source_html tag boundaries recover it more
    faithfully). For all NEW manifests the fallback is never reached."""
    n = v.get("n_line")
    if isinstance(n, int) and not isinstance(n, bool) and n > 0:
        return n
    text = v.get("plain_text")
    if isinstance(text, str) and text.strip():
        c = len([ln for ln in _split_lf_lines(text) if ln.strip()])
        if c > 0:
            return c
    html = v.get("source_html")
    if isinstance(html, str) and html:
        c = len([ln for ln in _split_lf_lines(_TAG_RE.sub("\n", html)) if ln.strip()])
        if c > 0:
            return c
    return 0


# ---------------------------------------------------------------------------
# segpack.schema.json's own shape, hand-rolled (see validate_segpack() below).
# ---------------------------------------------------------------------------
_TOP_LEVEL_KEYS = {
    "seg", "title", "kind", "word_count", "blocks", "footnotes",
    "verses", "names", "canon_names", "new_names", "canon_map", "generation_hashes",
}
_BLOCK_KEYS = {"id", "order_index", "source_html", "plain_text", "body_ref_markers"}
_FOOTNOTE_KEYS = {"n", "source_text"}
_VERSE_KEYS = {"vid", "placeholder", "parent_block", "mount", "n_line"}
_GENERATION_HASH_KEYS = {
    "source_extraction_hash", "source_input_hash",
    "particle_config_hash", "derivation_bundle_hash",
}


class SegpackError(RuntimeError):
    """Fatal segpack assembly error for one segment -- always a W3a preflight failure."""


def load_json(path, label):
    if not path.exists():
        raise SegpackError(f"{label} not found at {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SegpackError(f"{label} at {path} is not valid JSON: {exc}") from exc


def _scan_text(entry):
    """Plain-text view of a schema-shaped block/footnote entry, for name-scanning
    and body_ref_markers detection -- prefers plain_text, else strips tags out
    of source_html as a best-effort fallback (never a bs4/lxml dependency)."""
    if entry.get("plain_text") is not None:
        return entry["plain_text"]
    return _TAG_RE.sub(" ", entry.get("source_html", "") or "")


def _def_blocks_for(fn_ns, fn_entries_by_n):
    """The set of def_block ids for the given footnote numbers, skipping any
    number with no manifest.footnotes[] entry or no def_block. Feeds the
    embedded-verse discovery worklist's frontier in build_pack() -- a footnote's
    def block must itself be scanned for embedded verses (a verse quoted inside
    that footnote's definition may cite yet another footnote, at arbitrary
    nesting depth)."""
    out = set()
    for n in fn_ns:
        fe = fn_entries_by_n.get(n)
        if fe is None:
            continue
        db = fe.get("def_block")
        if db:
            out.add(db)
    return out


def build_pack(seg_id, manifest, canon, lang_config, apparatus_policy):
    """Assemble one segpack dict for seg_id. Raises SegpackError on any
    structural problem in the SOURCE data (missing segment/block/hash field);
    the caller separately runs validate_segpack() on the RESULT before writing."""
    blocks_by_id = manifest.get("blocks", {})
    seg = next((s for s in manifest.get("segments", []) if s.get("seg") == seg_id), None)
    if seg is None:
        raise SegpackError(f"segment {seg_id!r} not found in manifest.json segments[]")

    block_ids = seg.get("block_ids")
    if not block_ids:
        raise SegpackError(f"segment {seg_id!r} has no block_ids in manifest.json")

    seg_blocks = []
    for bid in block_ids:
        b = blocks_by_id.get(bid)
        if b is None:
            raise SegpackError(f"segment {seg_id!r} references unknown block id {bid!r}")
        seg_blocks.append(b)
    seg_blocks.sort(key=lambda b: b.get("order_index", 0))
    seg_block_ids = set(block_ids)

    if "word_count" not in seg:
        raise SegpackError(f"segment {seg_id!r} has no word_count in manifest.json")
    if "kind" not in seg:
        raise SegpackError(f"segment {seg_id!r} has no kind in manifest.json")

    # ---- blocks in reading order (schema shape only) ----
    blocks_out = []
    for b in seg_blocks:
        if "id" not in b:
            raise SegpackError(f"segment {seg_id!r} has a block with no id")
        entry = {"id": b["id"], "order_index": b.get("order_index", 0)}
        if "source_html" in b:
            entry["source_html"] = b["source_html"]
        if "plain_text" in b:
            entry["plain_text"] = b["plain_text"]
        if "source_html" not in entry and "plain_text" not in entry:
            raise SegpackError(
                f"block {b['id']!r} in segment {seg_id!r} has neither source_html nor plain_text"
            )
        blocks_out.append(entry)

    # ---- ADAPT-POINT: footnote inclusion branches on apparatus_policy's
    #      four-way distinction. Under body_refs_only, body_ref_markers[] is
    #      carried through unchanged whenever the manifest block already
    #      recorded it; otherwise this script derives it itself via the
    #      literal-marker fallback regex (see LITERAL_MARKER_RE above), since
    #      manifest.schema.json's block shape does not (yet) reserve a field
    #      for it. Under omit_apparatus, no apparatus and no markers exist at
    #      all. ----
    footnotes_out = []
    footnote_def_block_ids = set()

    if apparatus_policy in FOOTNOTE_CARRYING_POLICIES:
        fn_entries_by_n = {fe.get("n"): fe for fe in manifest.get("footnotes", [])}

        # Embedded verses indexed by their parent_block. Only mount=="embedded"
        # verses carry footnote citations that survive ONLY on the verse.store
        # node: an embedded verse's own text is lifted out of its carrier block
        # (replaced by its ⟦VERSE_…⟧ placeholder), so neither the carrier's
        # fnrefs[] nor its plain_text carries that footnote n. A mount=="block"
        # standalone verse is a real blocks[] entry, already covered by the
        # ordinary-block scan below -- do NOT double-scan it ("keep non-embedded
        # behavior unchanged").
        embedded_verses_by_parent = {}
        for v in manifest.get("verse", {}).get("store", []):
            if v.get("mount") != "embedded":
                continue
            embedded_verses_by_parent.setdefault(v.get("parent_block"), []).append(v)

        # Round 0: footnotes discovered directly by scanning this segment's own
        # ordinary blocks (recorded fnrefs[] + sentinel occurrences present in
        # the assembled text).
        fn_ns_recorded = {n for b in seg_blocks for n in (b.get("fnrefs") or [])}
        fn_ns_in_text = set()
        for entry in blocks_out:
            fn_ns_in_text.update(int(m) for m in FNREF_RE.findall(_scan_text(entry)))

        # Worklist / fixed point (#118 item 2): a footnote can be cited inside a
        # verse that is itself embedded in ANOTHER footnote's def block, at
        # arbitrary nesting depth (fn1's def embeds V2, V2 cites fn2 whose def
        # embeds V3, ...). Each round scans embedded verses parented to the
        # current frontier of blocks, folds their footnote citations into the
        # discovered set, then grows the frontier to include the def-blocks of
        # every footnote discovered so far. SEED the frontier with this
        # segment's own blocks AND the def-blocks of the round-0 footnotes: the
        # issue's own primary topology (an outer block cites fn1, fn1's def
        # embeds a verse) has fn1 discovered by the round-0 block scan, so its
        # def-block must be seeded here -- a worklist enqueuing only def-blocks
        # of footnotes surfaced by embedded-verse rounds would never reach it,
        # never find that verse, and silently terminate. `scanned` bounds the
        # frontier against a pathological citation cycle in source data (which
        # must never legitimately occur, but must not infinite-loop).
        scanned = set()
        frontier = set(seg_block_ids)
        frontier.update(_def_blocks_for(fn_ns_recorded | fn_ns_in_text, fn_entries_by_n))
        while True:
            current = frontier - scanned
            if not current:
                break
            scanned |= current
            for parent in current:
                for v in embedded_verses_by_parent.get(parent, []):
                    fn_ns_recorded.update(v.get("fnrefs") or [])
                    fn_ns_in_text.update(
                        int(m) for m in FNREF_RE.findall(v.get("plain_text") or "")
                    )
            frontier.update(_def_blocks_for(fn_ns_recorded | fn_ns_in_text, fn_entries_by_n))

        # Stale-manifest cross-check -- run ONCE after the fixed point converges,
        # over the WHOLE accumulated set, so a footnote first discovered in a
        # LATER worklist round still gets the same consistency WARN as a
        # first-pass one (the restructure must not silently drop that coverage).
        if fn_ns_in_text != fn_ns_recorded:
            print(
                f"WARN [{seg_id}]: FNREF sentinels found in text {sorted(fn_ns_in_text)} "
                f"disagree with manifest fnrefs[] {sorted(fn_ns_recorded)} -- manifest.json may be stale",
                file=sys.stderr,
            )
        fn_ns = sorted(fn_ns_in_text | fn_ns_recorded)

        for n in fn_ns:
            fe = fn_entries_by_n.get(n)
            if fe is None:
                print(
                    f"WARN [{seg_id}]: footnote ref {n} has no definition in "
                    "manifest.json footnotes[]",
                    file=sys.stderr,
                )
                continue
            def_block = blocks_by_id.get(fe.get("def_block"))
            if def_block is None:
                print(
                    f"WARN [{seg_id}]: footnote {n}'s def_block {fe.get('def_block')!r} "
                    "missing from manifest.json blocks{}",
                    file=sys.stderr,
                )
                continue
            footnotes_out.append({"n": n, "source_text": def_block.get("plain_text", "")})
            footnote_def_block_ids.add(fe.get("def_block"))
    else:
        for b in seg_blocks:
            if b.get("fnrefs"):
                print(
                    f"WARN [{seg_id}]: block {b.get('id')} carries fnrefs under "
                    f"apparatus_policy={apparatus_policy!r} (expected none) -- ignoring",
                    file=sys.stderr,
                )
        if apparatus_policy == "body_refs_only":
            for b, entry in zip(seg_blocks, blocks_out):
                markers = b.get("body_ref_markers")
                if markers is None:
                    markers = LITERAL_MARKER_RE.findall(_scan_text(entry))
                entry["body_ref_markers"] = list(markers)
        # omit_apparatus: nothing left behind at all -- footnotes_out stays [].

    # ---- verses parented to this segment (own blocks, or embedded in one of
    #      this segment's own referenced footnote-definition blocks) ----
    verses_out = []
    for v in manifest.get("verse", {}).get("store", []):
        parent = v.get("parent_block")
        # Include a verse iff its parent is materialized in THIS segpack --
        # either the segment's own blocks[] (a standalone VERSE block, OR the
        # PARA/QUOTE/HEAD/FRONTBACK block an inline verse was replaced within)
        # OR one of the segment's referenced footnote-definition blocks (a
        # verse quoted inside a footnote).
        if parent not in seg_block_ids and parent not in footnote_def_block_ids:
            continue
        for key in ("vid", "placeholder", "parent_block"):
            if key not in v:
                raise SegpackError(
                    f"segment {seg_id!r}: verse.store entry for parent_block "
                    f"{parent!r} missing required field {key!r}"
                )
        # mount is the EXTRACTOR's authoritative classification (the
        # gutenberg_epub adapter's extract.py.template mounting pass, or the
        # equivalent pass of whichever adapter produced this manifest),
        # normalized TOLERANTLY onto segpack's own {block,embedded} output
        # enum: exactly "embedded" -> "embedded"; anything else (missing,
        # "block", or an unknown adapter value) -> "block". Do NOT re-derive
        # from parent-kind -- a verse
        # embedded inside a PARA/QUOTE/translated-FRONTBACK block has its
        # parent IN seg_block_ids yet is authoritatively "embedded".
        verses_out.append({
            "vid": v["vid"],
            "placeholder": v["placeholder"],
            "parent_block": parent,
            "mount": "embedded" if v.get("mount") == "embedded" else "block",
            "n_line": _verse_line_count(v),
        })

    # ---- proper-noun candidates: scan this segment's own blocks + whatever
    #      footnote definitions it carries, reusing bootstrap_names.py's own
    #      tokenizer/scoring algorithm (no per-project adapt point here).
    #      #226: raw_text is handed to extract_candidates UNSTRIPPED --
    #      no local SENTINEL_RE.sub(" ", raw_text) pre-pass. That collapsing
    #      substitution replaced a multi-character sentinel (e.g.
    #      "⟦FNREF_12⟧") with a SINGLE space, shifting every
    #      subsequent character's offset by len(sentinel) - 1; extract_
    #      candidates' own bootstrap_names.mask_sentinels() already performs
    #      the SAME masking internally with a length-preserving space run, so
    #      the extra pre-pass here was redundant AND the one place in this
    #      script that could have silently corrupted a span-based caller. ----
    name_stats = {}
    scan_texts = [_scan_text(entry) for entry in blocks_out]
    scan_texts += [fo["source_text"] for fo in footnotes_out]
    for raw_text in scan_texts:
        for name, mid_sentence in extract_candidates(raw_text, lang_config):
            d = name_stats.setdefault(name, {"freq": 0, "mid": 0, "multiword": len(name.split()) > 1})
            d["freq"] += 1
            d["mid"] += int(mid_sentence)
    strong_names = sorted(
        (name for name, d in name_stats.items() if (d["mid"] > 0 or d["multiword"]) and len(name) != 1),
        key=lambda name: (-name_stats[name]["freq"], name),
    )

    # ---- canon injection: split into locked (canon_names) vs unresolved (new_names),
    #      plus canon_map (source_form -> frozen canonical_target_form) for every
    #      canonized name that carries a non-empty target form (#130: the frozen
    #      target form must actually reach the translate/review prompts, not just
    #      the canon_names source-form list). A canon entry with an empty/missing
    #      canonical_target_form is validly omitted from canon_map (canon_names
    #      is still the source of truth for "is this name canonized"). ----
    canon_entries = canon.get("entries", {})
    canon_names, new_names = [], []
    canon_map = {}
    for name in strong_names:
        entry = canon_entries.get(name)
        if entry is None:
            new_names.append(name)
            continue
        canon_names.append(name)
        tf = entry.get("canonical_target_form") if isinstance(entry, dict) else None
        if isinstance(tf, str) and tf:
            canon_map[name] = tf

    # ---- generation_hashes: copied verbatim, never recomputed ----
    manifest_hashes = manifest.get("generation_hashes", {})
    canon_hashes = canon.get("generation_hashes", {})
    generation_hashes = {}
    for field in ("source_extraction_hash", "source_input_hash"):
        if field not in manifest_hashes:
            raise SegpackError(f"manifest.json generation_hashes missing required field {field!r}")
        generation_hashes[field] = manifest_hashes[field]
    for field in ("particle_config_hash", "derivation_bundle_hash"):
        if field not in canon_hashes:
            raise SegpackError(f"canon.json generation_hashes missing required field {field!r}")
        generation_hashes[field] = canon_hashes[field]

    return {
        "seg": seg_id,
        "title": seg.get("title_text", ""),
        "kind": seg["kind"],
        "word_count": seg["word_count"],
        "blocks": blocks_out,
        "footnotes": footnotes_out,
        "verses": verses_out,
        "names": strong_names,
        "canon_names": canon_names,
        "new_names": new_names,
        "canon_map": canon_map,
        "generation_hashes": generation_hashes,
    }


def validate_segpack(pack, seg_id=None):
    """Hand-rolled, dependency-free structural self-check against
    segpack.schema.json. Deliberately does NOT use the jsonschema library --
    matching the real historiettes-t3 project's own scripts, which import
    only json/os/re/sys. Walks the parsed dict and asserts required keys plus
    Python types. Returns a list of error strings (empty == valid). A
    non-empty result means the segpack is INVALID and must be treated as a
    FATAL W3a preflight error -- never written to disk, never silently
    trusted downstream."""
    errors = []
    label = seg_id or (pack.get("seg") if isinstance(pack, dict) else "<unknown>")

    if not isinstance(pack, dict):
        return [f"segpack {label}: top-level value is not an object"]

    extra = pack.keys() - _TOP_LEVEL_KEYS
    if extra:
        errors.append(f"segpack {label}: unexpected top-level field(s) {sorted(extra)}")
    missing = _TOP_LEVEL_KEYS - pack.keys()
    if missing:
        errors.append(f"segpack {label}: missing required top-level field(s) {sorted(missing)}")
        return errors  # nothing further can be safely checked

    if not isinstance(pack["seg"], str) or not pack["seg"]:
        errors.append(f"segpack {label}: 'seg' must be a non-empty string")
    else:
        seg_error = validate_seg(pack["seg"])
        if seg_error is not None:
            errors.append(f"segpack {label}: 'seg' is not a safe segment id: {seg_error}")
    if not isinstance(pack["title"], str):
        errors.append(f"segpack {label}: 'title' must be a string")
    if pack["kind"] not in ("body", "frontback"):
        errors.append(f"segpack {label}: 'kind' must be 'body' or 'frontback', got {pack['kind']!r}")
    wc = pack["word_count"]
    if not isinstance(wc, int) or isinstance(wc, bool) or wc < 0:
        errors.append(f"segpack {label}: 'word_count' must be a non-negative integer")

    for list_field in ("blocks", "footnotes", "verses", "names", "canon_names", "new_names"):
        if not isinstance(pack[list_field], list):
            errors.append(f"segpack {label}: '{list_field}' must be an array")

    if isinstance(pack.get("blocks"), list):
        for i, b in enumerate(pack["blocks"]):
            if not isinstance(b, dict):
                errors.append(f"segpack {label}: blocks[{i}] is not an object")
                continue
            extra_b = b.keys() - _BLOCK_KEYS
            if extra_b:
                errors.append(f"segpack {label}: blocks[{i}] has unexpected field(s) {sorted(extra_b)}")
            if not isinstance(b.get("id"), str) or not b.get("id"):
                errors.append(f"segpack {label}: blocks[{i}] missing/invalid 'id'")
            oi = b.get("order_index")
            if not isinstance(oi, int) or isinstance(oi, bool) or oi < 0:
                errors.append(f"segpack {label}: blocks[{i}] missing/invalid 'order_index'")
            if "source_html" not in b and "plain_text" not in b:
                errors.append(f"segpack {label}: blocks[{i}] has neither 'source_html' nor 'plain_text'")
            if "source_html" in b and not isinstance(b["source_html"], str):
                errors.append(f"segpack {label}: blocks[{i}]['source_html'] must be a string")
            if "plain_text" in b and not isinstance(b["plain_text"], str):
                errors.append(f"segpack {label}: blocks[{i}]['plain_text'] must be a string")
            if "body_ref_markers" in b:
                bm = b["body_ref_markers"]
                if not isinstance(bm, list) or not all(isinstance(x, str) for x in bm):
                    errors.append(
                        f"segpack {label}: blocks[{i}]['body_ref_markers'] must be an array of strings"
                    )

    if isinstance(pack.get("footnotes"), list):
        for i, fo in enumerate(pack["footnotes"]):
            if not isinstance(fo, dict):
                errors.append(f"segpack {label}: footnotes[{i}] is not an object")
                continue
            extra_f = fo.keys() - _FOOTNOTE_KEYS
            if extra_f:
                errors.append(f"segpack {label}: footnotes[{i}] has unexpected field(s) {sorted(extra_f)}")
            n = fo.get("n")
            if not isinstance(n, int) or isinstance(n, bool):
                errors.append(f"segpack {label}: footnotes[{i}] missing/invalid 'n'")
            if not isinstance(fo.get("source_text"), str):
                errors.append(f"segpack {label}: footnotes[{i}] missing/invalid 'source_text'")

    if isinstance(pack.get("verses"), list):
        for i, v in enumerate(pack["verses"]):
            if not isinstance(v, dict):
                errors.append(f"segpack {label}: verses[{i}] is not an object")
                continue
            extra_v = v.keys() - _VERSE_KEYS
            if extra_v:
                errors.append(f"segpack {label}: verses[{i}] has unexpected field(s) {sorted(extra_v)}")
            for key in ("vid", "placeholder", "parent_block"):
                if not isinstance(v.get(key), str) or not v.get(key):
                    errors.append(f"segpack {label}: verses[{i}] missing/invalid {key!r}")
            mount = v.get("mount")
            if mount not in ("block", "embedded"):
                errors.append(
                    f"segpack {label}: verses[{i}] 'mount' must be 'block' or 'embedded', got {mount!r}"
                )
            n_line = v.get("n_line")
            if not isinstance(n_line, int) or isinstance(n_line, bool) or n_line < 0:
                errors.append(f"segpack {label}: verses[{i}] 'n_line' must be a non-negative integer")

    for list_field in ("names", "canon_names", "new_names"):
        val = pack.get(list_field)
        if isinstance(val, list) and not all(isinstance(x, str) for x in val):
            errors.append(f"segpack {label}: '{list_field}' must be an array of strings")

    # canon_map: source_form -> frozen canonical_target_form, for the segment's
    # already-canonized names. Every key must be a non-empty string that is
    # ITSELF one of canon_names (a subset, not necessarily equal -- a canon
    # entry with an empty/missing target form is validly omitted; see #130).
    cm = pack.get("canon_map")
    if not isinstance(cm, dict):
        errors.append(f"segpack {label}: 'canon_map' must be an object")
    else:
        canon_names_val = pack.get("canon_names")
        canon_names_set = set(canon_names_val) if isinstance(canon_names_val, list) else None
        for k, v in cm.items():
            if not isinstance(k, str) or not k:
                errors.append(f"segpack {label}: 'canon_map' has a non-string/empty key {k!r}")
            elif canon_names_set is not None and k not in canon_names_set:
                errors.append(f"segpack {label}: 'canon_map' key {k!r} is not in 'canon_names'")
            if not isinstance(v, str) or not v:
                errors.append(f"segpack {label}: canon_map[{k!r}] must be a non-empty string")

    gh = pack.get("generation_hashes")
    if not isinstance(gh, dict):
        errors.append(f"segpack {label}: 'generation_hashes' must be an object")
    else:
        extra_g = gh.keys() - _GENERATION_HASH_KEYS
        if extra_g:
            errors.append(f"segpack {label}: generation_hashes has unexpected field(s) {sorted(extra_g)}")
        missing_g = _GENERATION_HASH_KEYS - gh.keys()
        if missing_g:
            errors.append(f"segpack {label}: generation_hashes missing required field(s) {sorted(missing_g)}")
        for key in _GENERATION_HASH_KEYS & gh.keys():
            if not isinstance(gh[key], str) or not gh[key]:
                errors.append(f"segpack {label}: generation_hashes[{key!r}] must be a non-empty string")

    return errors


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description=(
            "Assemble a per-segment segpack (segments/segpack_{seg}.json) from "
            "manifest.json + canon.json. Invoked at W3a for every candidate "
            "segment; a missing or invalid segpack is a FATAL preflight error."
        )
    )
    parser.add_argument(
        "seg", nargs="?",
        help="Segment id to assemble (matches a manifest.json segments[].seg). "
             "Omit and pass --all to assemble every candidate segment instead.",
    )
    parser.add_argument(
        "--all", action="store_true",
        help="Assemble every segments[] entry in manifest.json (W3a bulk preflight mode) "
             "-- body AND translate-decision FRONTBACK:{id} segments alike, no special-casing.",
    )
    parser.add_argument(
        "--particle-config", required=True, metavar="FILENAME",
        help="Bare filename under ${durable_root}/languages/ -- the resolved literal "
             "value of profile.yml's source.language.particle_config, never rebuilt "
             "from source.language.code.",
    )
    parser.add_argument(
        "--apparatus-policy", required=True, choices=APPARATUS_POLICIES,
        help="Resolved literal value of profile.yml's footnotes.apparatus_policy.",
    )
    args = parser.parse_args(argv)
    if args.all and args.seg:
        parser.error("specify a single SEG or --all, not both")
    if not args.all and not args.seg:
        parser.error("either SEG or --all is required")
    return args


def main(argv=None):
    args = parse_args(argv)

    if args.seg is not None:
        seg_error = validate_seg(args.seg)
        if seg_error is not None:
            sys.exit(f"FATAL: {seg_error}")

    manifest_path = DURABLE_ROOT / "manifest.json"
    canon_path = DURABLE_ROOT / "canon.json"
    languages_dir = DURABLE_ROOT / "languages"
    segments_dir = DURABLE_ROOT / "segments"

    try:
        manifest = load_json(manifest_path, "manifest.json")
        canon = load_json(canon_path, "canon.json")
    except SegpackError as exc:
        sys.exit(f"FATAL: {exc}")

    try:
        lang_config = load_language_config(args.particle_config, languages_dir)
    except Exception as exc:
        sys.exit(
            f"FATAL: could not load particle config {args.particle_config!r} "
            f"from {languages_dir}: {exc}"
        )

    if args.all:
        seg_ids = [s["seg"] for s in manifest.get("segments", []) if "seg" in s]
        if not seg_ids:
            sys.exit("FATAL: manifest.json has no segments[] entries to segpack")
    else:
        seg_ids = [args.seg]

    segments_dir.mkdir(parents=True, exist_ok=True)

    failures = {}
    written = []
    for seg_id in seg_ids:
        # Validate the id BEFORE build_pack / the out_path write, so the
        # path-safety guarantee is local here (not merely inherited from
        # validate_segpack seeing pack["seg"] == seg_id downstream). In
        # --all mode seg_id comes straight from manifest.json segments[],
        # which an untrusted custom extractor controls.
        seg_error = validate_seg(seg_id)
        if seg_error:
            failures[seg_id] = [seg_error]
            continue
        try:
            pack = build_pack(seg_id, manifest, canon, lang_config, args.apparatus_policy)
        except SegpackError as exc:
            failures[seg_id] = [str(exc)]
            continue
        errors = validate_segpack(pack, seg_id)
        if errors:
            failures[seg_id] = errors
            continue
        out_path = segments_dir / f"segpack_{seg_id}.json"
        out_path.write_text(json.dumps(pack, ensure_ascii=False, indent=1), encoding="utf-8")
        written.append((seg_id, out_path, pack))

    for seg_id, out_path, pack in written:
        print(f"segment {seg_id}: {pack['title']!r}")
        print(
            f"  kind={pack['kind']} words={pack['word_count']} blocks={len(pack['blocks'])} "
            f"footnotes={len(pack['footnotes'])} verses={len(pack['verses'])} names={len(pack['names'])}"
        )
        print(f"  canon_names={len(pack['canon_names'])} new_names={len(pack['new_names'])}")
        print(f"  -> {out_path}")

    if failures:
        print(
            "\nFATAL: segpack assembly/validation failed for the following segment(s):",
            file=sys.stderr,
        )
        for seg_id, errs in failures.items():
            print(f"  {seg_id}:", file=sys.stderr)
            for e in errs:
                print(f"    - {e}", file=sys.stderr)
        sys.exit(1)

    print(f"\n{len(written)} segpack(s) written under {segments_dir}")


if __name__ == "__main__":
    main()
