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
    SENTINEL_RE
        Compiled regex matching the FNREF_N / VERSE_Vxxx sentinel placeholders
        -- stripped out of any text before it is handed to extract_candidates,
        so a sentinel token is never itself mistaken for a proper-noun run.
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
    from bootstrap_names import SENTINEL_RE, load_language_config, extract_candidates
except ImportError as exc:
    sys.exit(
        f"segpack.py: cannot import bootstrap_names.py from {SCRIPT_DIR} ({exc}).\n"
        "bootstrap_names.py must be installed alongside segpack.py under "
        "${durable_root}/scripts/ -- Step 0a copies both scripts together as a "
        "pair (they share the derivation_bundle_hash). It supplies "
        "SENTINEL_RE / load_language_config / extract_candidates, the shared "
        "name-candidate extraction primitives segpack.py reuses per segment. "
        "Re-run Step 0a, or verify the plugin install is not corrupted."
    )

APPARATUS_POLICIES = ("translate_all", "preserve_source", "omit_apparatus", "body_refs_only")
FOOTNOTE_CARRYING_POLICIES = ("translate_all", "preserve_source")

# Cross-check only: FNREF sentinel occurrences found directly in block text,
# compared against the manifest's own per-block fnrefs[] list as a cheap
# "does the recorded data agree with the text" sanity signal (WARN, not FATAL
# -- a real mismatch here is a manifest/extract.py.template bug worth
# surfacing loudly, but not this script's job to adjudicate).
FNREF_RE = re.compile(r"⟦FNREF_(\d+)⟧")

# Fallback literal-marker detector for apparatus_policy: body_refs_only, matching
# the illustrative convention documented in
# references/source-format-adapters/gutenberg-epub.md (a bare "[N]" baked
# verbatim into the block's own plain text in place of the stripped FNREF
# sentinel). Used ONLY when the manifest block itself does not already carry
# an explicit body_ref_markers list to pass through unchanged.
LITERAL_MARKER_RE = re.compile(r"\[\d+\]")

_TAG_RE = re.compile(r"<[^>]+>")

# ---------------------------------------------------------------------------
# segpack.schema.json's own shape, hand-rolled (see validate_segpack() below).
# ---------------------------------------------------------------------------
_TOP_LEVEL_KEYS = {
    "seg", "title", "kind", "word_count", "blocks", "footnotes",
    "verses", "names", "canon_names", "new_names", "generation_hashes",
}
_BLOCK_KEYS = {"id", "order_index", "source_html", "plain_text", "body_ref_markers"}
_FOOTNOTE_KEYS = {"n", "source_text"}
_VERSE_KEYS = {"vid", "placeholder", "parent_block"}
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
        fn_ns_recorded = {n for b in seg_blocks for n in (b.get("fnrefs") or [])}
        # cross-check: sentinel occurrences actually present in the assembled text
        fn_ns_in_text = set()
        for entry in blocks_out:
            fn_ns_in_text.update(int(m) for m in FNREF_RE.findall(_scan_text(entry)))
        if fn_ns_in_text != fn_ns_recorded:
            print(
                f"WARN [{seg_id}]: FNREF sentinels found in text {sorted(fn_ns_in_text)} "
                f"disagree with manifest fnrefs[] {sorted(fn_ns_recorded)} -- manifest.json may be stale",
                file=sys.stderr,
            )
        fn_ns = sorted(fn_ns_in_text | fn_ns_recorded)

        fn_entries_by_n = {fe.get("n"): fe for fe in manifest.get("footnotes", [])}
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
        if parent in seg_block_ids or (
            v.get("mount") == "embedded" and parent in footnote_def_block_ids
        ):
            for key in ("vid", "placeholder", "parent_block"):
                if key not in v:
                    raise SegpackError(
                        f"segment {seg_id!r}: verse.store entry for parent_block "
                        f"{parent!r} missing required field {key!r}"
                    )
            verses_out.append(
                {"vid": v["vid"], "placeholder": v["placeholder"], "parent_block": parent}
            )

    # ---- proper-noun candidates: scan this segment's own blocks + whatever
    #      footnote definitions it carries, reusing bootstrap_names.py's own
    #      tokenizer/scoring algorithm (no per-project adapt point here). ----
    name_stats = {}
    scan_texts = [_scan_text(entry) for entry in blocks_out]
    scan_texts += [fo["source_text"] for fo in footnotes_out]
    for raw_text in scan_texts:
        text = SENTINEL_RE.sub(" ", raw_text)
        for name, mid_sentence in extract_candidates(text, lang_config):
            d = name_stats.setdefault(name, {"freq": 0, "mid": 0, "multiword": len(name.split()) > 1})
            d["freq"] += 1
            d["mid"] += int(mid_sentence)
    strong_names = sorted(
        (name for name, d in name_stats.items() if (d["mid"] > 0 or d["multiword"]) and len(name) != 1),
        key=lambda name: (-name_stats[name]["freq"], name),
    )

    # ---- canon injection: split into locked (canon_names) vs unresolved (new_names) ----
    canon_entries = canon.get("entries", {})
    canon_names, new_names = [], []
    for name in strong_names:
        (canon_names if name in canon_entries else new_names).append(name)

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

    for list_field in ("names", "canon_names", "new_names"):
        val = pack.get(list_field)
        if isinstance(val, list) and not all(isinstance(x, str) for x in val):
            errors.append(f"segpack {label}: '{list_field}' must be an array of strings")

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
