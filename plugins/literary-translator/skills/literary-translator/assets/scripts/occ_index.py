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

## #243 -- this module is now mark/connector-FOLD-AWARE (supersedes A-C6)

A-C6 (#238/#241) had left this module's own comparison deliberately
unfolded (A-C6 = NO that train, lead-decisions.md), tracked as a known
residual: ``occurrence_targets.py`` (the ``## Mentions`` appendix's own
occurrence engine) folded Hebrew niqqud/cantillation marks and maqaf/
geresh/gershayim connectors at LOOKUP time (``bootstrap_names.
fold_match_key``), so an unpointed/space-joined canon entry found a
pointed/maqaf-joined source occurrence there, while this module's own
``production_occurrences()`` -- the SAME evidence-matching authority
``evidence_verify.py``/``suspicion_scan.py``/``canon_adjudication_audit.py``
all bind against -- did not, silently diverging from the appendix path.

**#243 closes that residual.** ``production_occurrences()``'s comparison
below is now ``fold_match_key(name) == fold_match_key(source_form)``, never
a raw, unfolded ``==`` -- so a pointed/maqaf-joined production occurrence IS
found under an unpointed/space-joined canon ``source_form``, on every path
that ultimately calls ``production_occurrences()``
(``evidence_verify.py``, ``suspicion_scan.py``, this module's own
``index_manifest()``). Two consequences that must not be conflated:

- **Folding is comparison-only, never emission.** The EMITTED ``source_form``
  and ``quote`` fields (``_build_record()``, below) stay the caller's own raw,
  unfolded strings -- exactly as before -- so a byte-for-byte evidence check
  against the raw source text is unaffected. Folding only widens which
  production spans a given ``source_form`` is compared against.
- **Fold KEYS can collide many-to-one** (e.g. a pointed and a maqaf-joined
  form of the same name both fold to one key) -- a fact ``production_
  occurrences()`` itself, a single-``source_form`` primitive, has no way to
  detect (it cannot see whether some OTHER canon ``source_form`` shares its
  fold key). Every caller that iterates more than one ``source_form`` at once
  (``index_manifest()`` below, ``evidence_verify.py``'s
  ``_group_production_spans_by_name()``, ``suspicion_scan.py``'s
  ``build_worklist()``) is responsible for its own fail-closed collision
  guard, built from ``canon_senses.fold_collision_map()`` -- crediting one
  physical occurrence to more than one colliding ``source_form``, or
  silently overwriting one with another, is exactly the bug that guard
  exists to prevent (see ``index_manifest()``'s own docstring below).
"""
import argparse
import hashlib
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Optional

SCRIPT_DIR = Path(__file__).resolve().parent
DURABLE_ROOT = SCRIPT_DIR.parent

DEFAULT_MANIFEST_PATH = DURABLE_ROOT / "manifest.json"
DEFAULT_NAME_CANDIDATES_PATH = DURABLE_ROOT / "name_candidates.json"
DEFAULT_OUT_PATH = DURABLE_ROOT / "occurrence_index.json"

try:
    from bootstrap_names import (
        extract_candidate_spans, fold_match_key, load_language_config, BootstrapNamesError,
    )
except ImportError as exc:
    sys.exit(
        f"occ_index.py: cannot import bootstrap_names.py from {SCRIPT_DIR} ({exc}).\n"
        "bootstrap_names.py must be installed alongside occ_index.py under "
        "${durable_root}/scripts/ -- Step 0a copies the whole scripts/ set together. "
        "It supplies extract_candidate_spans(), the offset-preserving production "
        "tokenizer/matcher this module reuses (never reimplements), and "
        "fold_match_key(), the #238/#241 Hebrew mark/connector MATCH KEY #243 uses "
        "to compare production_occurrences()'s emitted name against source_form. "
        "Re-run Step 0a, or verify the plugin install is not corrupted."
    )

try:
    from canon_senses import fold_collision_map, FoldCollisionMap
except ImportError as exc:
    sys.exit(
        f"occ_index.py: cannot import canon_senses.py from {SCRIPT_DIR} ({exc}).\n"
        "canon_senses.py must be installed alongside occ_index.py under "
        "${durable_root}/scripts/ -- it supplies fold_collision_map(), the shared "
        "#238/#241 fold-key collision detector index_manifest() uses to fail-closed "
        "a many-to-one match rather than silently crediting one physical occurrence "
        "to more than one source_form (or overwriting one with another). Re-run Step "
        "0a, or verify the plugin install is not corrupted."
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

    ``fold_match_key(name) == fold_match_key(source_form)`` (#243, supersedes
    A-C6's deliberate unfolded comparison -- see this module's own
    docstring): a pointed/maqaf-joined production occurrence IS found under
    an unpointed/space-joined ``source_form``, matching ``occurrence_targets.
    py``'s own lookup semantics. This is a single-``source_form`` primitive
    with no notion of a fold-key COLLISION among several other
    ``source_form``s -- a caller comparing more than one ``source_form`` at
    once must apply its own fail-closed guard (``canon_senses.
    fold_collision_map()``); see ``index_manifest()``'s docstring below.
    """
    key = fold_match_key(source_form)
    return [
        (char_start, char_end)
        for name, _mid_sentence, char_start, char_end in _run_spans(block_text, language_config)
        if fold_match_key(name) == key
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


def _warn_fold_collisions(fold_groups: dict, colliding: set) -> None:
    """stderr WARN, unconditional whenever ``colliding`` is non-empty --
    mirrors ``occurrence_targets._colliding_source_forms()``'s own warn
    style. A LOCAL group (>=2 members within this call's own
    ``source_forms``) gets one combined line naming every member and their
    shared key; a form excluded ONLY because the caller's own
    ``competitors`` map flagged it (no sibling visible within this call's
    own ``source_forms``) gets its own line, since there is no local group
    here to name."""
    reported = set()
    for key in sorted(fold_groups):
        group = fold_groups[key]
        if len(group) >= 2:
            reported.update(group)
            print(
                f"WARN occ_index.py: source_forms {sorted(group)!r} all fold to the "
                f"same #238/#241 match key {key!r} in index_manifest() -- excluded "
                "from the occurrence index entirely (fail-closed); neither is "
                "credited any occurrence until the operator disambiguates (rename "
                "or merge the colliding canon entries).",
                file=sys.stderr,
            )
    for name in sorted(colliding - reported):
        print(
            f"WARN occ_index.py: source_form {name!r} folds to the same #238/#241 "
            "match key as another competitor outside this call's own source_forms "
            "-- excluded from the occurrence index entirely (fail-closed).",
            file=sys.stderr,
        )


def index_manifest(manifest_path, source_forms, language_config, *,
                    collisions_out: Optional[list] = None,
                    competitors: Optional["FoldCollisionMap"] = None) -> list:
    """Every occurrence record, of every non-colliding form in
    ``source_forms``, across every block of ``manifest_path`` -- a flat
    list, in manifest block order then ``source_forms`` order. Return shape
    is unchanged by #243 (still a flat list): ``suspicion_scan.py`` iterates
    it directly and several tests call this function positionally with
    exactly 3 args -- ``collisions_out``/``competitors`` are keyword-only
    additions, never repositioning an existing parameter.

    Calls ``_run_spans()`` (the expensive tokenizer/run-building pass) exactly
    ONCE per block -- never once per ``(block, source_form)`` pair, which
    would re-run the full extraction ``len(source_forms)`` times over the
    same text. The single pass's spans are grouped by
    ``bootstrap_names.fold_match_key(name)`` (#243 -- previously grouped by
    raw ``name``; preserving each key's own start-order, since ``_run_spans()``
    already returns spans sorted by ``char_start`` and grouping-by-filtering a
    sorted sequence preserves that order even when several distinct raw
    ``name``s share one folded key), then records are emitted per
    ``source_form`` in ``source_forms`` order by looking up that form's own
    fold key from the grouped map -- identical output to calling
    ``build_occurrence_records()`` per form (also #243 fold-aware, via
    ``production_occurrences()``), just without re-triggering the extraction
    primitive per form.

    ``source_forms`` itself is iterated exactly ONCE overall (building
    ``rank``/the fold grouping together, below), never once per block: a
    manifest with many blocks and a large ``source_forms`` list would
    otherwise probe EVERY form on EVERY block regardless of whether it ever
    occurs there -- O(blocks x forms) -- even though most forms don't occur
    in most blocks (finding 8, RFC #215 Phase 0 review round 4). Per block,
    only the fold keys this block's own extraction pass actually matched are
    considered, so the per-block cost scales with that block's matched-key
    count, not with ``len(source_forms)``.

    **#243 fold-key collisions, fail-closed.** Folding is many-to-one: two
    DISTINCT raw forms in ``source_forms`` (e.g. a maqaf-joined and a
    space-joined spelling of the same Hebrew name) can legally share one
    fold key. A naive folded ``dict`` keyed by fold key would silently keep
    only the LAST such form (the exact silent-overwrite bug this guards
    against) -- so collisions are detected BEFORE any span is matched, in
    the SAME single ``enumerate(source_forms)`` pass that builds ``rank``
    (never a second pass over ``source_forms`` -- that would fail the
    iterate-once invariant above): every name is also grouped by its own
    fold key (``fold_groups``), and any key whose group has >= 2 members is
    fail-closed excluded from ``rank`` entirely, by a POST-FILTER after the
    single pass completes. Colliding spans are emitted to NEITHER form --
    never double-filed, never silently assigned to whichever form happened
    to be seen last.

    ``competitors``, when given, is a ``canon_senses.FoldCollisionMap``
    already built over the full COMPETITOR universe (union of every
    ``canon.json`` entry and every ``canon_senses.json`` form, split-only
    included -- see ``canon_senses.fold_collision_map()``'s own docstring on
    why this must be the broader universe, never just this call's own
    ``source_forms``): a form unique within THIS call's ``source_forms`` can
    still collide against a sibling that lives only in that broader universe
    (e.g. a split-only form, or a sibling that landed in a different scope
    projection) -- ``competitors.is_colliding()`` catches exactly that case,
    on top of (never instead of) the local ``fold_groups`` check. Omitted
    (``None``, the default), only the local collision check runs -- every
    existing caller and the 5 tests that call this positionally with 3 args
    keep today's exact behavior plus the #243 fold.

    Excluded forms are reported two ways: an unconditional stderr WARN
    (``_warn_fold_collisions``, above -- never gated by ``collisions_out``),
    and, when ``collisions_out`` is given (default ``None``, so no existing
    caller is forced to change), one ``{"source_form", "fold_key"}`` dict per
    excluded form -- never one per group, so a caller can always recover
    exactly which individual forms were dropped. The return value's own
    shape (a flat list) never carries collision information itself -- this
    is a side-channel via the caller-owned receiver, not a second return
    value.
    """
    rank = {}
    fold_key_of = {}
    fold_groups = defaultdict(list)
    for i, name in enumerate(source_forms):
        rank[name] = i
        key = fold_match_key(name)
        fold_key_of[name] = key
        fold_groups[key].append(name)

    colliding = {
        name
        for group in fold_groups.values() if len(group) >= 2
        for name in group
    }
    if competitors is not None:
        colliding |= {name for name in rank if competitors.is_colliding(name)}

    if colliding:
        _warn_fold_collisions(fold_groups, colliding)
        if collisions_out is not None:
            for name in sorted(colliding, key=rank.__getitem__):
                collisions_out.append({"source_form": name, "fold_key": fold_key_of[name]})
        for name in colliding:
            del rank[name]

    fold_to_name = {fold_key_of[name]: name for name in rank}

    records = []
    for block_id, seg, text in iter_manifest_blocks(manifest_path):
        folded_spans_by_key = defaultdict(list)
        for name, _mid_sentence, char_start, char_end in _run_spans(text, language_config):
            folded_spans_by_key[fold_match_key(name)].append((char_start, char_end))
        context_start, context_end = _context_window(text)
        context_sha256 = hashlib.sha256(
            text[context_start:context_end].encode("utf-8")
        ).hexdigest()
        present_keys = sorted(
            folded_spans_by_key.keys() & fold_to_name.keys(),
            key=lambda k: rank[fold_to_name[k]],
        )
        for key in present_keys:
            source_form = fold_to_name[key]
            for char_start, char_end in folded_spans_by_key[key]:
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
