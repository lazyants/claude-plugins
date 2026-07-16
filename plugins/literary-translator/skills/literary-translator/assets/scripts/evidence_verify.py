#!/usr/bin/env python3
"""evidence_verify.py -- binds every stored ``canon_senses.json`` evidence
record to hard, re-derivable facts about the manifest it claims to come
from (Phase 1, RFC #215, plan §1b).

``canon_senses.json`` (schema: ``canon-senses.schema.json``, loaded via
``canon_senses.py::load_senses``) stores, per sense, the MINIMAL verifiable
evidence set: ``{block, seg, char_start, char_end, context_start,
context_end, sha256}``. No field in that set is left unverified -- this
module is what verifies all of them, against one manifest's raw ``blocks``
(``manifest.schema.json``'s ``blocks{}.plain_text``):

  (i)   ``block`` exists in the manifest, and the evidence's ``seg`` matches
        that block's own ``seg`` field exactly (``None`` == ``None`` is a
        valid match -- a ``seg: null`` block is a normal, indexed block, per
        ``manifest.schema.json``).
  (ii)  ``[char_start, char_end)`` and ``[context_start, context_end)`` are
        both in-bounds half-open codepoint intervals into the block's
        ``plain_text``, ``char_start < char_end``, and the context window
        encloses the occurrence: ``context_start <= char_start`` and
        ``char_end <= context_end``.
  (iii) sha256 of the EXACT raw UTF-8 bytes of ``plain_text[context_start:
        context_end]`` equals the stored ``sha256`` -- never NFC-normalized
        first; this is real byte verification, not a Unicode-equivalence
        check.
  (iv)  MATCHER-AUTHENTICATION -- the strongest check: ``[char_start,
        char_end)`` must be one of the exact spans that
        ``occ_index.py::production_occurrences(source_form, plain_text,
        language_config)`` emits for this sense's ``source_form`` in this
        block, under the project's RESOLVED ``LanguageConfig``. A span that
        merely lies in-bounds and hashes correctly is NOT enough: without
        this check, evidence for source_form "Jean" could point its offsets
        at an entirely different name in the same block (e.g. block text
        "Jean met Paul", context = the whole block with a correct hash,
        offsets spanning "Paul") and pass every other check. Matcher
        authentication is config-parameterized because production matching
        is: e.g. "Effiat" is a production span of "d'Effiat" only when the
        resolved language config carries the elision pattern that splits
        it -- the SAME evidence bytes are valid under one config and
        invalid under another (see
        ``test_elision_no_elision_matcher_parity_identical_bytes`` below).

This module is a pure verification library -- it owns no CLI, no default
paths, and no sidecar-loading logic of its own. Its two entry points each
receive their inputs FULLY RESOLVED by the caller (the mandatory W-step,
``canon_adjudication_audit.py::run_check``):

  - ``manifest`` is the already-parsed ``manifest.json`` document (a dict
    with a top-level ``blocks`` mapping) -- the caller reads and parses
    manifest.json itself, exactly as it already does for canon.json/
    canon_adjudications.json; this module never resolves a manifest path.
  - ``language_config`` is the project's RESOLVED ``LanguageConfig``
    (``bootstrap_names.py``'s dataclass, carrying the real ``elision_re``/
    particle set) -- never a default/unresolved stand-in.
  - ``senses_result`` / each ``sense`` dict is exactly what
    ``canon_senses.py::load_senses`` already returned -- this module never
    re-reads or re-validates ``canon_senses.json`` itself.

``verify_senses(senses_result, manifest, language_config)`` is the public
entry the mandatory W-step calls once per audit run; ``verify_evidence(
source_form, sense, manifest, language_config)`` is the per-sense primitive
it is built from, exposed directly for callers (and tests) that already
have one sense in hand. Every failure is returned, never raised -- a bad
evidence record is a normal, expected outcome (an authoring mistake or a
stale sidecar), not a script bug -- so callers fold the returned
``EvidenceFailure`` list into their own blocking-count accounting
(``evidence_unverified``, per ``canon_adjudication_audit.py``'s W-step
wiring) rather than catching an exception.
"""
import hashlib
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

SCRIPT_DIR = Path(__file__).resolve().parent

try:
    from occ_index import production_occurrences, _run_spans
except ImportError as exc:
    sys.exit(
        f"evidence_verify.py: cannot import occ_index.py from {SCRIPT_DIR} ({exc}).\n"
        "occ_index.py must be installed alongside evidence_verify.py under "
        "${durable_root}/scripts/ -- it supplies production_occurrences() and _run_spans(), "
        "the shared matcher-authentication authority every stored evidence span is bound "
        "against. Re-run Step 0a, or verify the plugin install is not corrupted."
    )


@dataclass(frozen=True)
class EvidenceFailure:
    """One evidence-verification failure for one (source_form, sense_id)
    pair -- always reported with its block + span so a caller (or a human
    reading the audit report) can locate exactly which stored evidence
    record failed and why, without re-deriving anything from `reason`."""

    source_form: str
    sense_id: Optional[str]
    block: Optional[str]
    seg: object
    char_start: object
    char_end: object
    reason: str


def _blocks_mapping(manifest) -> dict:
    """The manifest's ``blocks`` mapping, or ``{}`` if `manifest` is
    missing, not a dict, or its ``blocks`` value is malformed -- every
    lookup against an empty mapping then fails as a normal "block not
    found" evidence failure below, rather than raising. A missing/malformed
    manifest is thus reported the same way as a missing block: both mean
    there is no raw text to verify evidence against."""
    if not isinstance(manifest, dict):
        return {}
    blocks = manifest.get("blocks")
    return blocks if isinstance(blocks, dict) else {}


def _group_production_spans_by_name(block_text: str, language_config) -> dict:
    """Every production span `_run_spans()` emits for `block_text`, grouped
    by the exact `name` the matcher assigns each run -- ONE extraction pass
    regardless of how many distinct source_forms end up querying this
    block's spans. Mirrors ``occ_index.py::index_manifest()``'s own
    grouping (same `_run_spans()` call, same `defaultdict(list)` shape) so
    the two never drift. This is the per-block cache `verify_senses()`
    builds lazily, once per block id, instead of calling
    `production_occurrences()` -- which re-runs the full extraction --
    once per (block, source_form) pair."""
    grouped = defaultdict(list)
    for name, _mid_sentence, char_start, char_end in _run_spans(block_text, language_config):
        grouped[name].append((char_start, char_end))
    return grouped


def verify_evidence(source_form: str, sense: dict, manifest, language_config,
                     production_spans_by_form: Optional[dict] = None) -> Optional[EvidenceFailure]:
    """Verifies ONE sense's stored `evidence` record against `manifest`.
    Returns `None` if every check (i)-(iv) above passes, else the first
    failing check's `EvidenceFailure`. Checks run in the module docstring's
    (i)-(iv) order -- each later check assumes the block's raw text is
    available and in-bounds, so an earlier failure short-circuits (a bad
    block reference or an out-of-bounds span makes every later check
    meaningless, not merely redundant).

    `production_spans_by_form`, when given, is this evidence's OWN block's
    `{name: [(char_start, char_end), ...]}` map (as
    `_group_production_spans_by_name()` builds it) -- the matcher-
    authentication check (iv) then looks up `source_form` in it instead of
    calling `production_occurrences()`, which would re-run the full
    extraction for a block already extracted by the caller.
    `verify_senses()` wires its per-block cache through this parameter;
    every direct caller (and every test that omits it) keeps today's exact
    behavior via the `None` default, which still calls
    `production_occurrences()` here."""
    sense_id = sense.get("sense_id")
    evidence = sense.get("evidence") or {}
    block_id = evidence.get("block")
    seg = evidence.get("seg")
    char_start = evidence.get("char_start")
    char_end = evidence.get("char_end")
    context_start = evidence.get("context_start")
    context_end = evidence.get("context_end")
    sha256_hex = evidence.get("sha256")

    def fail(reason: str) -> EvidenceFailure:
        return EvidenceFailure(
            source_form=source_form, sense_id=sense_id, block=block_id, seg=seg,
            char_start=char_start, char_end=char_end, reason=reason,
        )

    blocks = _blocks_mapping(manifest)
    block_record = blocks.get(block_id)
    if not isinstance(block_record, dict) or "plain_text" not in block_record:
        return fail(f"block {block_id!r} not found in manifest (or manifest missing/malformed)")

    block_text = block_record["plain_text"]
    if not isinstance(block_text, str):
        # A present-but-non-string plain_text (e.g. a hostile/corrupt manifest storing
        # null, or the wrong JSON type) is distinct from a genuinely MISSING block above --
        # both are manifest malformation, but this one must be caught explicitly: `"plain_text"
        # not in block_record` is False here (the key IS present), so without this check
        # `len(block_text)` below would raise TypeError instead of degrading to a per-sense
        # failure, aborting verification for every remaining sense.
        return fail(
            f"block {block_id!r}'s plain_text is not a string (found "
            f"{type(block_text).__name__}) -- manifest malformed"
        )

    if block_record.get("seg") != seg:
        return fail(
            f"evidence seg {seg!r} does not match manifest block {block_id!r}'s "
            f"actual seg {block_record.get('seg')!r}"
        )

    text_len = len(block_text)

    offsets = (char_start, char_end, context_start, context_end)
    if not all(isinstance(o, int) and not isinstance(o, bool) for o in offsets):
        return fail("evidence char_start/char_end/context_start/context_end must all be integers")

    if not (0 <= char_start < char_end <= text_len):
        return fail(
            f"occurrence span [{char_start},{char_end}) is out of bounds for block "
            f"{block_id!r} (length {text_len}) or not char_start < char_end"
        )

    if not (0 <= context_start <= char_start and char_end <= context_end <= text_len):
        return fail(
            f"context window [{context_start},{context_end}) does not enclose occurrence "
            f"span [{char_start},{char_end}) or is out of bounds for block {block_id!r} "
            f"(length {text_len})"
        )

    # block_text is untrusted (sourced from the caller's manifest.json), so a
    # lone surrogate codepoint can slip past the isinstance(str) check above
    # and then raise UnicodeEncodeError out of .encode("utf-8") below --
    # and it's unclear whether production_occurrences()'s tokenizer/matcher
    # is equally hostile-input-proof. Both calls are netted here so either
    # one raising still degrades to a normal EvidenceFailure, per this
    # module's documented never-raise contract -- verify_senses()'s own
    # broad except is a second line of defense, not a substitute for this
    # one, since verify_evidence() is itself a documented direct entry point.
    try:
        context_bytes = block_text[context_start:context_end].encode("utf-8")
        actual_sha256 = hashlib.sha256(context_bytes).hexdigest()
        if actual_sha256 != sha256_hex:
            return fail(
                f"sha256 mismatch on context window [{context_start},{context_end}) of block "
                f"{block_id!r}: evidence has {sha256_hex!r}, raw bytes hash to {actual_sha256!r}"
            )

        if production_spans_by_form is not None:
            valid_spans = production_spans_by_form.get(source_form, ())
        else:
            valid_spans = production_occurrences(source_form, block_text, language_config)
        if (char_start, char_end) not in valid_spans:
            return fail(
                f"occurrence span [{char_start},{char_end}) of block {block_id!r} is not a "
                f"production match for {source_form!r} under the resolved language config "
                "(matcher-authentication failed -- not merely an in-bounds substring)"
            )
    except Exception as exc:
        return fail(
            f"internal error verifying evidence bytes/matcher for block {block_id!r}: "
            f"{type(exc).__name__}: {exc}"
        )

    return None


def verify_senses(senses_result, manifest, language_config) -> list:
    """The public entry the mandatory W-step calls once per audit run:
    verifies EVERY sense's evidence in `senses_result.entries_by_source_form`
    (as returned by `canon_senses.py::load_senses` -- already schema- and
    procedurally-validated) against `manifest`, and returns the flat list of
    `EvidenceFailure`s -- one per failing sense, in `entries_by_source_form`/
    `senses` iteration order. An empty list means every stored evidence
    record verified clean.

    A single sense's verification is never allowed to abort the batch: one
    hostile/corrupt block in `manifest` must degrade to THAT sense's own
    `EvidenceFailure`, not a crash that silently drops every remaining
    sense's verification (the totality invariant `verify_evidence` upholds
    for every field it explicitly checks). `verify_evidence` guards every
    manifest-shaped value it explicitly indexes/lens/slices, but this
    broad `except Exception` is deliberate defense-in-depth against
    whatever that per-field sweep didn't anticipate (e.g. a lone-surrogate
    `plain_text` string raising `UnicodeEncodeError` on `.encode('utf-8')`)
    -- the reason string preserves the exception's class + message for
    debuggability rather than swallowing it silently.

    Builds a per-CALL cache of production spans, keyed by block id, so a
    block referenced by N senses (e.g. every sense of a heavily split entry
    pointing at the same paragraph) triggers exactly ONE
    `_run_spans()`/`extract_candidate_spans()` extraction pass instead of
    N -- `verify_evidence()`'s own matcher-authentication check is otherwise
    the dominant cost of this whole function. Safe to cache across the
    WHOLE call because `manifest` is fixed for its duration: the same block
    id always maps to the same `plain_text`. A missing/malformed block
    (not found, not a dict, or a non-string `plain_text`) caches as `{}` --
    harmless, since `verify_evidence()`'s own checks (i)-(iii) already fail
    that sense on the block/`plain_text` problem itself, long before the
    matcher-authentication step that would consult this map ever runs for
    that block."""
    blocks = _blocks_mapping(manifest)
    production_spans_by_block = {}

    failures = []
    for source_form, entry in senses_result.entries_by_source_form.items():
        for sense in entry.get("senses", []):
            sense_record = sense if isinstance(sense, dict) else {}
            evidence = sense_record.get("evidence")
            evidence = evidence if isinstance(evidence, dict) else {}
            block_id = evidence.get("block")
            try:
                if block_id not in production_spans_by_block:
                    block_record = blocks.get(block_id)
                    block_text = (
                        block_record.get("plain_text") if isinstance(block_record, dict) else None
                    )
                    production_spans_by_block[block_id] = (
                        _group_production_spans_by_name(block_text, language_config)
                        if isinstance(block_text, str) else {}
                    )
                failure = verify_evidence(
                    source_form, sense, manifest, language_config,
                    production_spans_by_form=production_spans_by_block[block_id],
                )
            except Exception as exc:
                failure = EvidenceFailure(
                    source_form=source_form,
                    sense_id=sense_record.get("sense_id"),
                    block=evidence.get("block"), seg=evidence.get("seg"),
                    char_start=evidence.get("char_start"), char_end=evidence.get("char_end"),
                    reason=(
                        f"unexpected {type(exc).__name__} during evidence verification: {exc}"
                    ),
                )
            if failure is not None:
                failures.append(failure)
    return failures
