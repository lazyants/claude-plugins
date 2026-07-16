#!/usr/bin/env python3
"""Offset-preserving source occurrence index (Phase 0, RFC #215, plan §0c).

For a given proper-noun surface form (a "source_form", e.g. "Jean" or a
multiword run like "Jean Valjean"), finds every span at which the
PRODUCTION extractor -- bootstrap_names.py's own tokenizer/run-building
matcher -- actually emits that exact name as a completed candidate run in
one manifest block's raw ``plain_text``. This module imports that matcher
directly and reuses it verbatim; it never reimplements any part of the
run-building decision logic (which tokens join a run, particle
continuation, sentence-boundary bridging, elision splitting, or any future
caseless/inventory-driven matching path) -- a second, independent
implementation would silently drift the moment bootstrap_names.py's own
algorithm changes, defeating the whole point of matcher-authenticated
evidence (Phase 1, see references/canon-and-glossary.md's evidence-verify
section).

``production_occurrences(source_form, block_text, language_config)`` is the
single authority Phase 1's ``evidence_verify.py`` binds every stored
``canon_senses.json`` evidence span against: an offset pair is valid
evidence ONLY if it is one of these spans, never merely an in-bounds
substring. ``language_config`` is REQUIRED and load-bearing -- it carries
``elision_re``/the particle set, so the same ``(source_form, block_text)``
pair can legitimately have different production spans under a different
resolved language config (e.g. "Effiat" is a production span of
"d'Effiat" only when elision is configured; see
``test_elision_no_elision_matcher_parity_identical_bytes``).

This module also builds the full per-manifest occurrence index (every
occurrence, of every candidate surface form, across every block) as a
browsing aid for whoever authors ``canon_senses.json`` evidence -- see
``index_manifest()``/the CLI below. No other Phase-1 script reads this
index file; they all call ``production_occurrences()`` directly with their
own single ``(source_form, block)`` pair.

Offsets throughout are half-open, Unicode-**codepoint** intervals into the
raw block ``plain_text`` -- CPython's ``str`` is already a codepoint
sequence (PEP 393), so plain ``str`` slicing/indexing is exactly the right
unit; no UTF-16-surrogate-pair handling is needed for non-BMP characters.
"""
import argparse
import hashlib
import json
import sys
from collections import defaultdict
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
DURABLE_ROOT = SCRIPT_DIR.parent

DEFAULT_MANIFEST_PATH = DURABLE_ROOT / "manifest.json"
DEFAULT_NAME_CANDIDATES_PATH = DURABLE_ROOT / "name_candidates.json"
DEFAULT_OUT_PATH = DURABLE_ROOT / "occurrence_index.json"

try:
    from bootstrap_names import extract_candidate_spans, load_language_config, BootstrapNamesError
except ImportError as exc:
    sys.exit(
        f"occ_index.py: cannot import bootstrap_names.py from {SCRIPT_DIR} ({exc}).\n"
        "bootstrap_names.py must be installed alongside occ_index.py under "
        "${durable_root}/scripts/ -- Step 0a copies the whole scripts/ set together. "
        "It supplies extract_candidate_spans(), the offset-preserving production "
        "tokenizer/matcher this module reuses (never reimplements). Re-run Step 0a, "
        "or verify the plugin install is not corrupted."
    )


def _run_spans(block_text: str, language_config):
    """ONE call into bootstrap_names' own offset-preserving matcher -- every
    ``(name, mid_sentence, char_start, char_end)`` run it emits for this
    block. Both ``production_occurrences()`` (single-name filter) and
    ``index_manifest()`` (every discovered name at once) go through this
    SAME function, so there is exactly one place in this module that talks
    to bootstrap_names.py. ``block_text`` is handed over RAW (sentinels
    included) -- ``extract_candidate_spans()`` masks ``⟦...⟧`` sentinels
    internally via its own same-length ``mask_sentinels()``, so every span
    it returns is already valid directly against ``block_text`` as given;
    no separate masking/remapping step belongs here.
    """
    return extract_candidate_spans(block_text, language_config)


def production_occurrences(source_form: str, block_text: str, language_config) -> list:
    """The exact matcher spans the PRODUCTION tokenizer/matcher emits for
    ``source_form`` in ``block_text`` under ``language_config``. Half-open
    Unicode-codepoint offsets. An offset pair is valid evidence ONLY if it
    is one of these spans -- never a mere in-bounds substring. Never call
    this with an unresolved/default config; a project's RESOLVED
    ``LanguageConfig`` (carrying its real ``elision_re``/particle set) is
    required and load-bearing.
    """
    return [
        (char_start, char_end)
        for name, _mid_sentence, char_start, char_end in _run_spans(block_text, language_config)
        if name == source_form
    ]


def _context_window(block_text: str):
    """The enclosing evidence "context" window for one block: the WHOLE
    block text (``[0, len(block_text))``). Trivially satisfies ``context ⊇
    occurrence`` for every occurrence in the block, with no separate
    windowing heuristic to argue about.
    """
    return 0, len(block_text)


def _build_record(source_form: str, block: str, seg, block_text: str,
                   char_start: int, char_end: int,
                   context_start: int, context_end: int, context_sha256: str) -> dict:
    """One occurrence record ``{source_form, block, seg, char_start, char_end,
    quote, context_start, context_end, context_sha256}`` -- the shared record
    shape both ``build_occurrence_records()`` (single source_form) and
    ``index_manifest()`` (every source_form, one extraction pass per block)
    emit, so the two never drift on field names/order.
    """
    return {
        "source_form": source_form,
        "block": block,
        "seg": seg,
        "char_start": char_start,
        "char_end": char_end,
        "quote": block_text[char_start:char_end],
        "context_start": context_start,
        "context_end": context_end,
        "context_sha256": context_sha256,
    }


def build_occurrence_records(source_form: str, block: str, seg, block_text: str,
                              language_config) -> list:
    """Every occurrence record for ONE ``source_form`` in ONE block:
    ``{source_form, block, seg, char_start, char_end, quote, context_start,
    context_end, context_sha256}``. ``seg`` is passed through as-is
    (nullable; a ``seg: null`` block is still indexed by ``block``, per
    ``manifest.schema.json``). ``context_sha256`` is the sha256 of the
    EXACT raw UTF-8 bytes of the context window -- never NFC-normalized.
    """
    context_start, context_end = _context_window(block_text)
    context_sha256 = hashlib.sha256(
        block_text[context_start:context_end].encode("utf-8")
    ).hexdigest()
    return [
        _build_record(source_form, block, seg, block_text, char_start, char_end,
                      context_start, context_end, context_sha256)
        for char_start, char_end in production_occurrences(source_form, block_text, language_config)
    ]


def iter_manifest_blocks(manifest_path):
    """Yield ``(block_id, seg, plain_text)`` for every non-empty block in
    ``manifest.json``, in the manifest's own ``blocks{}`` iteration order.
    Reads the raw ``blocks{}`` shape directly (``manifest.schema.json``)
    rather than ``bootstrap_names.iter_manifest_texts()``, which discards
    the block id this index needs.
    """
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    for block_id, block in manifest.get("blocks", {}).items():
        text = block.get("plain_text", "")
        if text and text.strip():
            yield block_id, block.get("seg"), text


def index_manifest(manifest_path, source_forms, language_config) -> list:
    """Every occurrence record, of every form in ``source_forms``, across
    every block of ``manifest_path`` -- a flat list, in manifest block order
    then ``source_forms`` order.

    Calls ``_run_spans()`` (the expensive tokenizer/run-building pass) exactly
    ONCE per block -- never once per ``(block, source_form)`` pair, which
    would re-run the full extraction ``len(source_forms)`` times over the
    same text. The single pass's spans are grouped by ``name`` (preserving
    each name's own start-order, since ``_run_spans()`` already returns spans
    sorted by ``char_start``), then records are emitted per ``source_form``
    in ``source_forms`` order by looking up that form's spans from the
    grouped map -- identical output to calling ``build_occurrence_records()``
    per form, just without re-triggering the extraction primitive per form.

    ``source_forms`` itself is iterated exactly ONCE overall (building
    ``rank``, below), never once per block: a manifest with many blocks and
    a large ``source_forms`` list would otherwise probe EVERY form on EVERY
    block regardless of whether it ever occurs there -- O(blocks x forms) --
    even though most forms don't occur in most blocks (finding 8, RFC #215
    Phase 0 review round 4). Per block, only the names this block's own
    extraction pass actually matched (``spans_by_name``) are considered, so
    the per-block cost scales with that block's matched-name count, not with
    ``len(source_forms)``.
    """
    rank = {name: i for i, name in enumerate(source_forms)}
    records = []
    for block_id, seg, text in iter_manifest_blocks(manifest_path):
        spans_by_name = defaultdict(list)
        for name, _mid_sentence, char_start, char_end in _run_spans(text, language_config):
            spans_by_name[name].append((char_start, char_end))
        context_start, context_end = _context_window(text)
        context_sha256 = hashlib.sha256(
            text[context_start:context_end].encode("utf-8")
        ).hexdigest()
        present_forms = sorted(spans_by_name.keys() & rank.keys(), key=rank.__getitem__)
        for source_form in present_forms:
            for char_start, char_end in spans_by_name[source_form]:
                records.append(
                    _build_record(source_form, block_id, seg, text, char_start, char_end,
                                  context_start, context_end, context_sha256)
                )
    return records


def _write_json_atomic(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(path.name + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    tmp_path.replace(path)  # atomic on the same filesystem


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "Builds the offset-preserving source occurrence index over "
            "manifest.json's raw blocks -- a browsing aid for authoring "
            "canon_senses.json evidence spans. Every other Phase-1 script "
            "calls production_occurrences() directly; this CLI's output is "
            "not a dependency of any of them."
        ),
    )
    p.add_argument(
        "--particle-config", required=True, metavar="FILENAME",
        help="Bare filename under ${durable_root}/languages/ -- the profile's "
             "own source.language.particle_config LITERAL value.",
    )
    p.add_argument(
        "--manifest", metavar="PATH", default=None,
        help=f"Path to manifest.json (default: {DEFAULT_MANIFEST_PATH}).",
    )
    p.add_argument(
        "--name-candidates", metavar="PATH", default=None,
        help=f"Path to name_candidates.json (default: {DEFAULT_NAME_CANDIDATES_PATH}) "
             "-- its candidates[].name list is the set of source forms indexed.",
    )
    p.add_argument(
        "--out", metavar="PATH", default=None,
        help=f"Where to write the occurrence index JSON (default: {DEFAULT_OUT_PATH}).",
    )
    return p


def main(argv=None) -> int:
    args = build_arg_parser().parse_args(argv)

    try:
        lang = load_language_config(args.particle_config)
    except BootstrapNamesError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    manifest_path = Path(args.manifest) if args.manifest else DEFAULT_MANIFEST_PATH
    if not manifest_path.is_file():
        print(f"error: manifest not found: {manifest_path}", file=sys.stderr)
        return 1

    name_candidates_path = (
        Path(args.name_candidates) if args.name_candidates else DEFAULT_NAME_CANDIDATES_PATH
    )
    if not name_candidates_path.is_file():
        print(
            f"error: name_candidates.json not found: {name_candidates_path} "
            "-- run bootstrap_names.py first",
            file=sys.stderr,
        )
        return 1
    name_candidates = json.loads(name_candidates_path.read_text(encoding="utf-8"))
    source_forms = [row["name"] for row in name_candidates.get("candidates", [])]

    records = index_manifest(manifest_path, source_forms, lang)

    out_path = Path(args.out) if args.out else DEFAULT_OUT_PATH
    _write_json_atomic(out_path, {"occurrences": records})
    print(
        f"occurrences: {len(records)}  (forms={len(source_forms)}) -> {out_path}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
