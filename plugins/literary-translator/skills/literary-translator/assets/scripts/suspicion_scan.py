#!/usr/bin/env python3
"""suspicion_scan.py -- deterministic, confidence-INDEPENDENT structural-risk
scan over a FROZEN canon.json (RFC #215 Phase 2, "skeptic pass", plan Part A).

canon.json is built blind to the source text: the codex glossary pass
resolves each candidate's canonical rendering, but nothing re-checks that
resolution against what the source actually says. review_queue only ever
catches what the blind agent itself doubted -- an entry it was CONFIDENT
about, even wrongly so (an over-merge, a mis-identified homonym, a
citation-only figure folded into the person index), is never flagged. This
script closes that gap the ONLY way a script is allowed to under this
plugin's iron rule (scripts SURFACE + ENFORCE, they never make an identity
call): it re-derives seven purely STRUCTURAL risk signals from canon.json +
manifest.json + the resolved LanguageConfig, with NO dependence whatsoever
on any entry's own `confidence` field, and emits `suspicion_worklist.json`
(`suspicion-worklist.schema.json`) -- the ONLY input the downstream skeptic
pass (`skeptic_setup.py` -> `skeptic-pass-wf.template.js`) reads to decide
who gets adversarially re-examined. This script itself never adjudicates
anything; it only surfaces candidates, exactly like `bootstrap_names.py`
surfaces name candidates for the (also blind, but human/codex-authored)
glossary pass.

The seven risk classes (`skeptic_constants.RISK_CLASSES`, computed in this
order):

  1. merge_participant  -- >=2 distinct source_forms in canon.json share the
     same `normalize_form(canonical_target_form)` (a possible over-merge,
     issue #207). Grouping is INLINED here (~8 lines, mirroring
     `canon_adjudication_audit.py`'s own `group_by_normalized` idiom) --
     deliberately NOT imported from `canon_adjudication_audit.py`, which
     would drag its bootstrap/senses/evidence-verify dependency chain in
     for one grouping helper.
  2. established_offline -- `basis=="established"` while the project's
     `--research-mode` is `offline` (closes the frozen-canon research-mode
     gap: `canon_validate.py`'s offline backstop only checks INCOMING
     batches, never re-scans an already-frozen canon).
  3. singleton -- exactly one occurrence total (block-origin + embedded
     verse), re-derived from `occ_index.py`/`verse_occurrences()` below --
     never trusted from any stored `freq` field (canon-entry.schema.json
     has none).
  4. high_dispersion -- occurrences span >= `--dispersion-threshold`
     distinct segments.
  5. all_citation -- every occurrence sits in a citation-type block
     (adapter-safe: disabled, fail-safe, for any `source.format` with no
     configured citation-tag set -- never guessed from tag spelling).
  6. near_merge -- a NEW proximity check: `normalize_form(source_form)`
     pairs within `1 - difflib.SequenceMatcher.ratio()` of each other,
     found via character-bigram blocking (recall-preserving -- catches
     `Mordecai`/`Nordecai`, which first-char blocking misses) rather than
     any first-char/length bucketing.
  7. sampled -- a globally-capped, deterministic stratified sample of the
     remaining accepted high/medium-confidence entries (a spot-check safety
     net over everything the first six classes did not already catch).
  8. fold_collision (#243) -- this scope_in source_form's
     `bootstrap_names.fold_match_key` collides with ANOTHER scope_in
     source_form's (`canon_senses.fold_collision_map()`, computed over the
     UNION of every `canon.json` entry and every `canon_senses.json` form,
     split-only included -- the same "competitors" universe every #243
     consumer shares -- then re-checked against THIS scope: a collision with
     an out-of-scope or split-only-only competitor never fires this).
     ALWAYS flagged, and NEVER combined with classes 3/4/5/6's ordinary
     occurrence-count computation for the same entry: `verse_occurrences()`
     is called per-form, fold-collision-unaware, so two colliding forms
     sharing one physical verse span would otherwise double-file that span
     to BOTH while their block-origin counts are independently zeroed by
     `occ_index.py`'s own fail-closed collision handling -- the two paths
     disagreeing in opposite directions makes singleton/high_dispersion/
     near_merge meaningless for these forms. Skipping that computation
     entirely and flagging unconditionally guarantees a colliding entry can
     never end up with zero risk classes and silently never reach the
     skeptic pass (this script's own promise, one paragraph up).

Scope filter (classes 1, 3, 4, 5, 6, 7, 8 -- every class except
established_offline, which is a provenance signal, not an identity one):
entries with `is_proper_name: false` or `basis: "not_a_name"` are excluded
-- mirrors the shipped audit's own entity-merge filter
(`canon_adjudication_audit.py`'s `_proper_name_records`).

Verse handling is mount-aware and never double-counts (round-3 blocker 2 of
the RFC review): `extract.py.template` emits a `mount:"block"` (standalone)
verse node as BOTH a `verse.store[]` entry AND its own `VERSE:` block in
`blocks{}` -- `occ_index.py`'s block scan already counts it, so this script
must NOT re-scan it from `verse.store` (that would double `freq` and
silently strip `singleton`). A `mount:"embedded"` verse node's text lives
ONLY in `verse.store[].plain_text` (its carrier block has a placeholder
sentinel instead) -- `verse_occurrences()` below scans exactly this case,
classifying provenance via the carrier's OWN `parent_block` (never the
coarse verse `context`), because a citation-block carrier and a
narrative-prose carrier must not be conflated. `mount` is optional in
manifest.schema.json; anything other than the literal string `"embedded"`
(including absent) is treated as block-backed and left to the block scan --
the shipped segpack convention, applied here defensively so a `mount`-less
custom-adapter entry can never be double-counted.

Every offset-preserving match in this script goes through
`occ_index.production_occurrences()`/`occ_index.index_manifest()`, which in
turn goes through `bootstrap_names.extract_candidate_spans()` -- this module
NEVER reimplements any part of that matching logic; a second implementation
would silently drift the moment the production matcher's own algorithm
changes.

`producer_input_digest`: this script stamps every worklist it writes with a
sha256 hex digest over its own ENTIRE behavior-determining closure --
`canon.json` + `manifest.json` + `canon_senses.json` (#243, each a
state-tagged `(state, bytes)` pair, codex round 4 -- an absent sidecar is
state `"absent"`/bytes `b""`, distinct from a schema-valid logically-empty
document, which is state `"regular"`/non-empty-schema bytes; see
`--senses-path` below) + the
canonically-serialized resolved scan parameters + the resolved
`LanguageConfig.raw_bytes` + the raw bytes of every file in
`PRODUCER_CODE_CLOSURE` below. `skeptic_setup.py` (the skeptic resume-domain
owner, Part B) imports `compute_producer_input_digest` from this module and
recomputes the IDENTICAL value to reject a stale worklist fail-closed --
see that function's own docstring for the exact algorithm. `skeptic_constants.py`
is itself a closure member (governs every default this script uses), never
a `PLUGIN_BUNDLE_MEMBER` -- editing it must never re-translate a converged
segment, but it DOES invalidate a worklist's freshness, same as editing this
script itself.

Ambiguity competitors (#243): class 8 (`fold_collision`, see above) needs the
same "competitors" universe every #243 consumer shares -- the union of every
`canon.json` entry and every `canon_senses.json` form (split-only included).
`--senses-path` (mirrors `canon_adjudication_audit.py`'s own
`--senses-path`/`DEFAULT_SENSES_PATH`/`allow_absent` convention exactly: an
implicit default sidecar that is genuinely absent is tolerated as empty; an
EXPLICIT `--senses-path` that does not exist is a hard error) is parsed via
`canon_senses.load_senses_from_snapshot()` (codex round 5: from THIS
script's own already-captured snapshot, never a second independent read)
to build that universe, then `canon_senses.fold_collision_map()` computes
the collision groups passed into `build_worklist()`.

CLI:

    python3 suspicion_scan.py --particle-config fr.json \\
        --research-mode offline --source-format gutenberg_epub \\
        [--canon PATH] [--manifest PATH] [--senses-path PATH] \\
        [--languages-dir PATH] \\
        [--dispersion-threshold N] [--sample-cap N] [--near-threshold F] \\
        [--near-cap N] [--near-pair-budget N] [--windows-per-entity N] \\
        [--citation-block-types TYPE [TYPE ...]] [--out PATH]

Self-anchored: this script always lives at
``${durable_root}/scripts/suspicion_scan.py`` -- never assumes cwd, never
takes a ``--durable-root`` flag.
"""
import argparse
import difflib
import hashlib
import itertools
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
DURABLE_ROOT = SCRIPT_DIR.parent
SCHEMAS_DIR = DURABLE_ROOT / "schemas"

DEFAULT_CANON_PATH = DURABLE_ROOT / "canon.json"
DEFAULT_MANIFEST_PATH = DURABLE_ROOT / "manifest.json"
# Sibling of DEFAULT_CANON_PATH, self-anchored the same way -- NOT imported
# from canon_adjudication_audit.py/canon_validate.py, each consumer computes
# its own copy (see canon_senses.py's own module docstring on why
# DEFAULT_SENSES_PATH is deliberately not defined there).
DEFAULT_SENSES_PATH = DURABLE_ROOT / "canon_senses.json"

try:
    import jsonschema
except ImportError as e:
    sys.stderr.write(
        "suspicion_scan.py requires the 'jsonschema' package (>=4.26.0) to "
        "self-validate suspicion_worklist.json against "
        "suspicion-worklist.schema.json before writing it. Install with:\n\n"
        "    pip install -r requirements.txt\n\n"
        "(or directly: pip install 'jsonschema>=4.26.0')\n\n"
        f"(import error: {e})\n"
    )
    sys.exit(1)

try:
    from occ_index import production_occurrences, index_manifest
except ImportError as exc:
    sys.exit(
        f"suspicion_scan.py: cannot import occ_index.py from {SCRIPT_DIR} ({exc}).\n"
        "occ_index.py must be installed alongside suspicion_scan.py under "
        "${durable_root}/scripts/ -- it supplies the offset-preserving production "
        "matcher this script reuses verbatim. Re-run Step 0a, or verify the plugin "
        "install is not corrupted."
    )

try:
    from bootstrap_names import (
        load_language_config, BootstrapNamesError, LANGUAGES_DIR as DEFAULT_LANGUAGES_DIR,
    )
except ImportError as exc:
    sys.exit(
        f"suspicion_scan.py: cannot import bootstrap_names.py from {SCRIPT_DIR} ({exc}).\n"
        "bootstrap_names.py must be installed alongside suspicion_scan.py under "
        "${durable_root}/scripts/. Re-run Step 0a, or verify the plugin install is "
        "not corrupted."
    )

try:
    from canon_senses import (
        normalize_form,
        fold_collision_map,
        load_senses_from_snapshot,
        CanonSensesLoadError,
    )
except ImportError as exc:
    sys.exit(
        f"suspicion_scan.py: cannot import canon_senses.py from {SCRIPT_DIR} ({exc}).\n"
        "canon_senses.py must be installed alongside suspicion_scan.py under "
        "${durable_root}/scripts/ -- it supplies normalize_form(), the shared "
        "NFC+casefold+whitespace-collapse comparator every grouping/matching key in "
        "this script is computed from, plus load_senses_from_snapshot()/"
        "fold_collision_map() (#243), the shared ambiguity-competitors projection "
        "class 8 (fold_collision) needs. Re-run Step 0a, or verify the plugin "
        "install is not corrupted."
    )

try:
    from skeptic_constants import (
        DISPERSION_THRESHOLD_DEFAULT, WINDOWS_PER_ENTITY_DEFAULT, SAMPLE_CAP_DEFAULT,
        NEAR_THRESHOLD_DEFAULT, NEAR_CAP_DEFAULT, NEAR_PAIR_BUDGET_DEFAULT,
        CITATION_BLOCK_TYPES_BY_FORMAT,
        RISK_MERGE_PARTICIPANT, RISK_ESTABLISHED_OFFLINE, RISK_SINGLETON,
        RISK_HIGH_DISPERSION, RISK_ALL_CITATION, RISK_NEAR_MERGE, RISK_SAMPLED,
        RISK_FOLD_COLLISION,
        RISK_CLASSES,
        CITATION_UNAVAILABLE_TAG, VERSE_PARENT_UNRESOLVED_TAG, ZERO_OCCURRENCE_TAG,
        NEAR_BUDGET_TRUNCATED_TAG, FOLD_COLLISION_OCCURRENCES_SUPPRESSED_TAG,
        NO_CATEGORY_SENTINEL, NON_IDENTITY_BASIS,
        OCC_ORIGIN_BLOCK, OCC_ORIGIN_VERSE_EMBEDDED, VERSE_MOUNT_EMBEDDED,
        SUSPICION_WORKLIST_FILENAME, SUSPICION_WORKLIST_SCHEMA,
    )
except ImportError as exc:
    sys.exit(
        f"suspicion_scan.py: cannot import skeptic_constants.py from {SCRIPT_DIR} ({exc}).\n"
        "skeptic_constants.py must be installed alongside suspicion_scan.py under "
        "${durable_root}/scripts/ -- it supplies every default value/tag/enum this "
        "RFC #215 Phase-2 script uses. Re-run Step 0a, or verify the plugin install "
        "is not corrupted."
    )

DEFAULT_OUT_PATH = DURABLE_ROOT / SUSPICION_WORKLIST_FILENAME

# The producer's ENTIRE behavior-determining closure -- every file whose
# bytes must be folded into producer_input_digest (see
# compute_producer_input_digest's own docstring). Read fresh off
# `script_dir` at digest time, never cached -- an edit to any one of these
# must change the digest on the very next scan.
PRODUCER_CODE_CLOSURE = (
    "suspicion_scan.py",
    "occ_index.py",
    "bootstrap_names.py",
    "canon_senses.py",
    "skeptic_constants.py",
)


# ---------------------------------------------------------------------------
# Scope filter (classes 1, 3, 4, 5, 6, 7 -- every class except
# established_offline)
# ---------------------------------------------------------------------------

def _in_scope(entry: dict) -> bool:
    """True unless `entry` is explicitly `is_proper_name: false` or
    `basis: "not_a_name"` -- the two ways a canon entry declares itself NOT
    identity-bearing. Mirrors canon_adjudication_audit.py's own
    `_proper_name_records` filter (its underlying entity-merge exclusion),
    applied here to every identity risk class."""
    return entry.get("is_proper_name") is not False and entry.get("basis") != NON_IDENTITY_BASIS


# ---------------------------------------------------------------------------
# Class 1: merge_participant
# ---------------------------------------------------------------------------

def _merge_participant_groups(scope_in: dict) -> dict:
    """`source_form -> normalize_form(canonical_target_form)` for every
    entry that is part of a >=2-distinct-source_form over-merge group.
    Filters to `scope_in` FIRST, then groups -- mirroring
    canon_adjudication_audit.py's compute_cat2_items precedent (filter,
    THEN `group_by_normalized`), not the other way around."""
    groups = defaultdict(list)
    for sf, entry in scope_in.items():
        key = normalize_form(entry.get("canonical_target_form", ""))
        groups[key].append(sf)
    result = {}
    for key, members in groups.items():
        if len(members) >= 2:
            for sf in members:
                result[sf] = key
    return result


# ---------------------------------------------------------------------------
# Class 2: established_offline (NOT scope-filtered -- a provenance signal)
# ---------------------------------------------------------------------------

def _established_offline_forms(all_entries: dict, research_mode: str) -> set:
    if research_mode != "offline":
        return set()
    return {sf for sf, e in all_entries.items() if e.get("basis") == "established"}


# ---------------------------------------------------------------------------
# verse_occurrences() -- mount-aware embedded-verse scan (Part A contract)
# ---------------------------------------------------------------------------

def verse_occurrences(source_form: str, manifest: dict, language_config) -> list:
    """Every occurrence of `source_form` in `manifest["verse"]["store"]`
    ENTRIES WHOSE MOUNT IS EXACTLY `"embedded"` -- a standalone
    (`mount:"block"`) verse node is already a `VERSE:` block `occ_index.py`
    counts via the ordinary block scan, so re-scanning it here would double
    its `freq` and silently strip `singleton` (round-3 blocker 2). Anything
    other than the literal string `"embedded"`, INCLUDING an absent
    `mount` key, is treated as block-backed and skipped here -- the shipped
    segpack tolerant-normalization convention, applied defensively so a
    `mount`-less custom-adapter entry is never double-counted.

    Returns a list of dicts, one per matched span:
    `{vid, parent_block, seg, block_type, char_start, char_end, malformed}`.
    `seg`/`block_type` are derived from `manifest["blocks"][parent_block]`
    (the carrier), NEVER the coarse verse `context` -- a citation-block
    carrier and a narrative-prose carrier must not be conflated (round-3
    blocker 2, second half). `malformed` is True iff `parent_block` is
    missing/empty/dangling (not a real key in `manifest["blocks"]`) -- only
    reachable if a manifest bypassed `validate_extraction.py`'s
    `block_graph_integrity`/`verse_placeholders_unique_and_mounted` gates,
    handled as belt-and-suspenders defense: `seg`/`block_type` come back
    `None` (never guessed), and the caller tags this occurrence
    `VERSE_PARENT_UNRESOLVED_TAG` and folds it into dispersion under a
    synthetic, always-distinct `("verse", vid)` unit rather than merging it
    into a shared `seg: None` bucket (which could silently UNDER-count
    dispersion).
    """
    blocks = manifest.get("blocks") if isinstance(manifest, dict) else None
    blocks = blocks if isinstance(blocks, dict) else {}
    verse = manifest.get("verse") if isinstance(manifest, dict) else None
    store = verse.get("store") if isinstance(verse, dict) else None
    store = store if isinstance(store, list) else []

    results = []
    for entry in store:
        if not isinstance(entry, dict):
            continue
        if entry.get("mount") != VERSE_MOUNT_EMBEDDED:
            continue  # standalone or mount-absent -- block scan owns it
        plain_text = entry.get("plain_text")
        if not isinstance(plain_text, str) or not plain_text:
            continue
        spans = production_occurrences(source_form, plain_text, language_config)
        if not spans:
            continue
        vid = entry.get("vid")
        parent_block = entry.get("parent_block")
        parent_record = (
            blocks.get(parent_block) if isinstance(parent_block, str) and parent_block else None
        )
        malformed = not isinstance(parent_record, dict)
        seg = None if malformed else parent_record.get("seg")
        block_type = None if malformed else parent_record.get("type")
        for char_start, char_end in spans:
            results.append({
                "vid": vid if isinstance(vid, str) else None,
                "parent_block": parent_block if isinstance(parent_block, str) else None,
                "seg": seg,
                "block_type": block_type,
                "char_start": char_start,
                "char_end": char_end,
                "malformed": malformed,
            })
    return results


# ---------------------------------------------------------------------------
# Class 8: fold_collision (#243) -- ALWAYS flagged, gates classes 3/4/5 off
# ---------------------------------------------------------------------------

def _fold_colliding_forms(scope_in_forms: list, competitors) -> set:
    """Every `scope_in_forms` member that shares its `fold_match_key` group
    (`competitors`, a `canon_senses.FoldCollisionMap` built by the caller
    over the shared #243 "competitors" universe -- every `canon.json` entry
    UNION every `canon_senses.json` form, split-only included, per this
    module's own docstring) with >=1 OTHER `scope_in_forms` member.

    Re-projects each collision group down to THIS consumer's own
    `scope_in_forms` -- a group that includes a competitor OUTSIDE
    `scope_in_forms` (an `is_proper_name:false`/`not_a_name` canon entry, or
    a split-only-only `canon_senses.json` form that never became a canon
    entry at all) does NOT, by itself, make its scope_in sibling collide:
    only another scope_in_forms member sharing the SAME group does. This is
    what keeps "a form colliding only with an out-of-scope entry keeps its
    ordinary counters" true while still sharing ONE global collision
    computation with every other #243 consumer.

    `competitors=None` (no `canon_senses.json` resolution -- see
    `build_worklist`'s own `competitors` parameter) means "nothing collides":
    returns the empty set, i.e. today's pre-#243 behavior."""
    if competitors is None:
        return set()
    scope_in_set = set(scope_in_forms)
    member_to_group = {}
    for group in competitors.groups.values():
        for member in group:
            member_to_group[member] = group
    colliding = set()
    for sf in scope_in_forms:
        group = member_to_group.get(sf)
        if group is None:
            continue
        if any(other != sf and other in scope_in_set for other in group):
            colliding.add(sf)
    return colliding


# ---------------------------------------------------------------------------
# Combined occurrence collection (block + embedded verse), classes 3/4/5
# ---------------------------------------------------------------------------

def _block_occurrences_by_form(manifest_path, scope_in_forms: list, language_config) -> dict:
    """One combined `occ_index.index_manifest()` pass over every in-scope
    form -- a single extraction pass per block, never once per (block,
    form) pair (see index_manifest's own docstring)."""
    by_form = defaultdict(list)
    for rec in index_manifest(manifest_path, scope_in_forms, language_config):
        by_form[rec["source_form"]].append(rec)
    return by_form


def _combined_occurrences(source_form: str, block_records: list, manifest: dict,
                           language_config) -> list:
    """Every occurrence backing `source_form` -- block-origin (from
    `block_records`, `occ_index.index_manifest`'s own output) UNION
    embedded-verse (`verse_occurrences()`) -- deduplicated on the EXACT
    `(origin, block, char_start, char_end)` tuple for block-origin (block
    offsets are absolute within the block, so no `vid` is needed) and
    `(origin, vid, parent_block, char_start, char_end)` for embedded-verse
    (embedded-verse offsets are LOCAL to each verse node's own plain_text,
    so two DISTINCT verse nodes sharing a parent_block can legitimately
    have the same char_start/char_end -- `vid` is what keeps them from
    collapsing into one). Defensive in both directions: two representations
    of the same occurrence must never be counted twice, while two genuinely
    distinct spans in the same block/verse node must never collapse into
    one. Each returned dict carries the fields the caller needs for
    classification: `origin, block, seg, char_start, char_end, vid
    (verse-embedded only), dispersion_key, citation_type, malformed`.
    """
    blocks = manifest.get("blocks") if isinstance(manifest, dict) else None
    blocks = blocks if isinstance(blocks, dict) else {}

    combined = []
    seen = set()

    for rec in block_records:
        key = (OCC_ORIGIN_BLOCK, rec["block"], rec["char_start"], rec["char_end"])
        if key in seen:
            continue
        seen.add(key)
        block_record = blocks.get(rec["block"])
        block_type = block_record.get("type") if isinstance(block_record, dict) else None
        combined.append({
            "origin": OCC_ORIGIN_BLOCK,
            "block": rec["block"],
            "seg": rec["seg"],
            "char_start": rec["char_start"],
            "char_end": rec["char_end"],
            "dispersion_key": rec["seg"],
            "citation_type": block_type,
            "malformed": False,
        })

    for occ in verse_occurrences(source_form, manifest, language_config):
        key = (OCC_ORIGIN_VERSE_EMBEDDED, occ["vid"], occ["parent_block"],
               occ["char_start"], occ["char_end"])
        if key in seen:
            continue
        seen.add(key)
        combined.append({
            "origin": OCC_ORIGIN_VERSE_EMBEDDED,
            "block": occ["parent_block"] if occ["parent_block"] else "",
            "seg": occ["seg"],
            "char_start": occ["char_start"],
            "char_end": occ["char_end"],
            "vid": occ["vid"],
            "dispersion_key": occ["seg"] if not occ["malformed"] else ("verse", occ["vid"]),
            "citation_type": occ["block_type"],
            "malformed": occ["malformed"],
        })

    return combined


def _classify_occurrences(combined: list, citation_types) -> tuple:
    """Derives (risk: set, notes: set, dispersion_units: set) for ONE
    entry's combined occurrence list. `citation_types`, resolved by
    `resolve_citation_block_types()`, is `None` when the class-5 fail-safe
    is disabled (custom/unknown `source.format` with no override) --
    `all_citation` is then never asserted, and every non-zero-occurrence
    entry is annotated `CITATION_UNAVAILABLE_TAG` instead of a guess. A
    malformed embedded-verse occurrence's `citation_type` is always `None`,
    which is never a member of `citation_types` -- so `all_citation` is
    correctly never asserted for an entry carrying one (fail-safe, not a
    special case).
    """
    risk = set()
    notes = set()
    freq = len(combined)
    dispersion_units = {c["dispersion_key"] for c in combined}

    if freq == 0:
        notes.add(ZERO_OCCURRENCE_TAG)
    else:
        if freq == 1:
            risk.add(RISK_SINGLETON)
        if citation_types is None:
            notes.add(CITATION_UNAVAILABLE_TAG)
        elif all(c["citation_type"] in citation_types for c in combined):
            risk.add(RISK_ALL_CITATION)

    if any(c["malformed"] for c in combined):
        notes.add(VERSE_PARENT_UNRESOLVED_TAG)

    return risk, notes, dispersion_units


def resolve_citation_block_types(source_format: str, override):
    """The class-5 adapter resolution: an explicit `override` (the
    project's own `--citation-block-types`/`glossary.skeptic_pass.
    citation_block_types`) always wins; otherwise fall back to
    `CITATION_BLOCK_TYPES_BY_FORMAT[source_format]`, or `None` if
    `source_format` has no configured default (custom/unknown adapter --
    the class-5 fail-safe is then DISABLED, never guessed from tag
    spelling)."""
    if override is not None:
        return tuple(override)
    return CITATION_BLOCK_TYPES_BY_FORMAT.get(source_format)


# ---------------------------------------------------------------------------
# Class 6: near_merge -- bigram-blocked, budget-bounded, difflib-only
# ---------------------------------------------------------------------------

def _bigrams(s: str) -> frozenset:
    """Character bigrams of `s` -- the near_merge blocking key. A string
    shorter than 2 characters has no bigram, so it blocks on the WHOLE
    string instead (its own single-element key); two forms sharing NO
    bigram (and, for length<2, not textually identical) are never compared
    -- this is the documented, tested near_merge blind spot."""
    if len(s) < 2:
        return frozenset({s})
    return frozenset(s[i:i + 2] for i in range(len(s) - 1))


def _near_merge_candidate_pairs(scope_in_forms: list, normalized_of: dict,
                                 near_pair_budget: int) -> tuple:
    """Bigram-blocked candidate PAIR generation with a pre-enumeration
    materialization budget (mirrors canon_adjudication_audit.py's cat-3
    degenerate-scale guard): two forms are candidates iff they share >=1
    normalized-form bigram. Deterministic iteration order (sorted bigram
    keys, sorted members within each bigram's group) means the SAME input
    always truncates at the SAME pair, never a size-unordered-set
    artifact. The budget is enforced by NEVER letting `candidate_pairs`
    grow past `near_pair_budget` -- truncation is a logged, deterministic
    fact, not a silent cap. Returns `(candidate_pairs: set[(a, b)],
    truncated: bool)`.
    """
    bigram_index = defaultdict(set)
    for sf in scope_in_forms:
        for bg in _bigrams(normalized_of[sf]):
            bigram_index[bg].add(sf)

    candidate_pairs = set()
    truncated = False
    for bg in sorted(bigram_index):
        members = sorted(bigram_index[bg])
        for a, b in itertools.combinations(members, 2):
            pair = (a, b)
            if pair in candidate_pairs:
                continue
            if len(candidate_pairs) >= near_pair_budget:
                truncated = True
                break
            candidate_pairs.add(pair)
        if truncated:
            break
    return candidate_pairs, truncated


def _near_merge(scope_in_forms: list, near_threshold: float, near_cap: int,
                 near_pair_budget: int) -> tuple:
    """Class 6 end to end. Distance is `1 - difflib.SequenceMatcher(None,
    norm_a, norm_b).ratio()` (`ratio()` is a SIMILARITY in [0, 1]; the
    class is a proximity/distance check, hence the inversion). Pairs within
    `near_threshold` are kept, globally capped at the top `near_cap` by
    ascending distance, tie-broken by `(norm_a, norm_b)` (the pair's two
    normalized forms, always in sorted order regardless of which raw form
    the candidate-pair builder happened to label `a`/`b`), then by `(a, b)`
    themselves -- `candidate_pairs` is a SET, so two pairs that tie on
    distance AND normalized forms (e.g. several raw forms all normalizing
    to the same string) would otherwise survive in set-iteration order,
    which is PYTHONHASHSEED-dependent; sorting on the raw forms too makes
    the surviving top-`near_cap` pairs fully deterministic.

    Returns `(flagged: set[str], notes_by_form: dict[str, list[str]],
    truncated: bool)`.
    """
    normalized_of = {sf: normalize_form(sf) for sf in scope_in_forms}
    candidate_pairs, truncated = _near_merge_candidate_pairs(
        scope_in_forms, normalized_of, near_pair_budget
    )

    qualifying = []
    for a, b in candidate_pairs:
        na, nb = normalized_of[a], normalized_of[b]
        distance = 1 - difflib.SequenceMatcher(None, na, nb).ratio()
        if distance <= near_threshold:
            norm_lo, norm_hi = sorted((na, nb))
            qualifying.append((distance, norm_lo, norm_hi, a, b))
    # Full-tuple lexicographic order IS the deterministic 5-field tiebreak
    # (distance, then norm_lo/norm_hi, then a/b) -- see this function's docstring.
    qualifying.sort()
    top = qualifying[:near_cap]

    flagged = set()
    notes_by_form = defaultdict(list)
    for distance, _norm_lo, _norm_hi, a, b in top:
        flagged.add(a)
        flagged.add(b)
        notes_by_form[a].append(f"near_merge candidate: {b!r} (distance={distance:.4f})")
        notes_by_form[b].append(f"near_merge candidate: {a!r} (distance={distance:.4f})")

    return flagged, dict(notes_by_form), truncated


# ---------------------------------------------------------------------------
# Class 7: sampled -- globally-capped stratified sample, largest-remainder
# ---------------------------------------------------------------------------

def _sampled(scope_in: dict, per_entry_risk: dict, sample_cap: int) -> set:
    """A deterministic, RNG-free, resume-stable stratified sample of
    `scope_in` entries with `confidence` in `{"high", "medium"}` that no
    earlier class (1-6) already flagged. `--sample-cap` is a GLOBAL total,
    distributed across occupied `(category, confidence)` strata by
    largest-remainder apportionment (proportional to stratum size,
    fractional remainders rounded by descending size, ties broken by the
    stratum key's own tuple order -- `NO_CATEGORY_SENTINEL` sorts first,
    giving a TOTAL order even though `category` is open-vocabulary).
    Within a stratum, the first `quota` members by ascending
    `sha256(source_form)` hex are selected -- no randomness, stable across
    runs and resumes.
    """
    eligible = []
    for sf, entry in scope_in.items():
        if entry.get("confidence") not in ("high", "medium"):
            continue
        if per_entry_risk.get(sf):
            continue  # already flagged by classes 1-6
        category = entry.get("category")
        if not isinstance(category, str) or not category.strip():
            category = NO_CATEGORY_SENTINEL
        eligible.append((sf, category, entry.get("confidence")))

    if not eligible or sample_cap <= 0:
        return set()

    strata_members = defaultdict(list)
    for sf, category, confidence in eligible:
        strata_members[(category, confidence)].append(sf)
    strata_counts = {k: len(v) for k, v in strata_members.items()}

    total_eligible = len(eligible)
    cap = min(sample_cap, total_eligible)

    keys = sorted(strata_counts.keys())
    ideal = {k: strata_counts[k] * cap / total_eligible for k in keys}
    quota = {k: int(ideal[k]) for k in keys}
    remainder = cap - sum(quota.values())
    # Largest-remainder-first order; ties broken by the stratum key's own
    # tuple order (NO_CATEGORY_SENTINEL sorts before any real category).
    order = sorted(keys, key=lambda k: (-(ideal[k] - quota[k]), k))
    for k in order[:remainder]:
        quota[k] += 1

    selected = set()
    for k in keys:
        members_by_hash = sorted(
            strata_members[k], key=lambda sf: hashlib.sha256(sf.encode("utf-8")).hexdigest()
        )
        selected.update(members_by_hash[:quota[k]])
    return selected


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def _to_occurrence_ref(c: dict) -> dict:
    ref = {
        "block": c["block"],
        "seg": c["seg"],
        "char_start": c["char_start"],
        "char_end": c["char_end"],
        "origin": c["origin"],
    }
    vid = c.get("vid")
    if isinstance(vid, str) and vid:
        ref["vid"] = vid
    return ref


def build_worklist(canon_entries: dict, manifest: dict, manifest_path, language_config, *,
                    research_mode: str, citation_types, dispersion_threshold: int,
                    sample_cap: int, near_threshold: float, near_cap: int,
                    near_pair_budget: int, competitors=None) -> tuple:
    """Runs all eight risk classes over `canon_entries` (canon.json's own
    `entries{}`, keyed by source_form) + `manifest` (already-parsed
    manifest.json) and returns `(entries: list[dict], warnings: list[str])`
    -- `entries` is exactly `suspicion-worklist.schema.json`'s `entries[]`
    shape (one dict per flagged source_form; a source_form with zero
    fired classes is simply absent, per the schema's own "empty array is
    the schema-valid nothing-suspicious state" contract). `warnings` are
    human-facing lines (e.g. near_pair_budget truncation) the CLI prints to
    stderr; they never affect the worklist's own bytes.

    `competitors` (#243): a `canon_senses.FoldCollisionMap` built by the
    caller over the shared "competitors" universe (this module's own
    docstring, class 8) -- resolved OUTSIDE this function (via
    `canon_senses.load_senses_from_snapshot()` +
    `canon_senses.fold_collision_map()`) the same way `citation_types` is
    already resolved outside and passed in.
    `None` (the default) disables class 8 entirely -- today's pre-#243
    behavior -- so every existing caller that never resolves a senses
    sidecar keeps working unchanged.
    """
    warnings = []
    scope_in = {sf: e for sf, e in canon_entries.items() if _in_scope(e)}

    per_entry_risk = defaultdict(set)
    per_entry_notes = defaultdict(set)
    per_entry_group_key = {}

    for sf in _established_offline_forms(canon_entries, research_mode):
        per_entry_risk[sf].add(RISK_ESTABLISHED_OFFLINE)

    for sf, key in _merge_participant_groups(scope_in).items():
        per_entry_risk[sf].add(RISK_MERGE_PARTICIPANT)
        per_entry_group_key[sf] = key

    scope_in_forms = sorted(scope_in.keys())

    # Class 8 FIRST, before any occurrence is ever collected for these forms
    # -- a colliding form's block/verse occurrences are never trusted (see
    # this module's own docstring), so `_combined_occurrences` (the side
    # door verse_occurrences() call included) must never even run for one.
    fold_colliding = _fold_colliding_forms(scope_in_forms, competitors)
    for sf in fold_colliding:
        per_entry_risk[sf].add(RISK_FOLD_COLLISION)
        per_entry_notes[sf].add(FOLD_COLLISION_OCCURRENCES_SUPPRESSED_TAG)

    block_by_form = _block_occurrences_by_form(manifest_path, scope_in_forms, language_config)
    combined_by_form = {}
    for sf in scope_in_forms:
        if sf in fold_colliding:
            # Never combined with classes 3/4/5's occurrence counting --
            # occ_index.py's own site-2 collision handling already zeroes
            # this form's block-origin records, but the verse-embedded side
            # door (verse_occurrences(), called per-form, collision-unaware)
            # is not similarly protected; skipping the call entirely (rather
            # than trusting its output) is what actually closes it.
            combined_by_form[sf] = []
            continue
        combined = _combined_occurrences(sf, block_by_form.get(sf, []), manifest, language_config)
        combined_by_form[sf] = combined
        risk, notes, dispersion_units = _classify_occurrences(combined, citation_types)
        per_entry_risk[sf] |= risk
        per_entry_notes[sf] |= notes
        if len(dispersion_units) >= dispersion_threshold:
            per_entry_risk[sf].add(RISK_HIGH_DISPERSION)

    near_forms, near_notes, truncated = _near_merge(
        scope_in_forms, near_threshold, near_cap, near_pair_budget
    )
    for sf in near_forms:
        per_entry_risk[sf].add(RISK_NEAR_MERGE)
    for sf, note_list in near_notes.items():
        per_entry_notes[sf] |= set(note_list)
    if truncated:
        warnings.append(
            f"near_merge: pre-enumeration candidate-pair budget "
            f"({near_pair_budget}) reached -- candidate generation truncated "
            f"deterministically; near_merge recall for this run may be incomplete"
        )
        for sf in near_forms:
            per_entry_notes[sf].add(NEAR_BUDGET_TRUNCATED_TAG)

    for sf in _sampled(scope_in, per_entry_risk, sample_cap):
        per_entry_risk[sf].add(RISK_SAMPLED)

    entries_out = []
    for sf in sorted(per_entry_risk.keys()):
        risk_classes = per_entry_risk[sf]
        if not risk_classes:
            continue
        entry = canon_entries.get(sf, {})
        occurrence_refs = [_to_occurrence_ref(c) for c in combined_by_form.get(sf, [])]
        out_entry = {
            "source_form": sf,
            "canonical_target_form": entry.get("canonical_target_form", ""),
            "risk_classes": sorted(risk_classes, key=RISK_CLASSES.index),
            "occurrence_refs": occurrence_refs,
        }
        if sf in per_entry_group_key:
            out_entry["group_key"] = per_entry_group_key[sf]
        if per_entry_notes.get(sf):
            out_entry["notes"] = sorted(per_entry_notes[sf])
        entries_out.append(out_entry)

    return entries_out, warnings


# ---------------------------------------------------------------------------
# producer_input_digest -- the shared helper skeptic_setup.py imports
# ---------------------------------------------------------------------------

def _canonical_json_bytes(obj) -> bytes:
    """Deterministic canonical JSON (sorted keys, compact separators,
    non-ASCII preserved verbatim) -- a small local copy of the same idiom
    `cache_key.py`/`canon_adjudication_audit.py` each already define
    independently, rather than importing either of those modules for one
    three-line utility."""
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def resolved_scan_params(*, dispersion_threshold: int, sample_cap: int, windows_per_entity: int,
                          near_threshold: float, near_cap: int, near_pair_budget: int,
                          research_mode: str, source_format: str, resolved_citation_types) -> dict:
    """The canonical "resolved scan parameters" object hashed into
    `producer_input_digest` -- every value that governs scan OR downstream
    skeptic-setup behavior, fully resolved (never a raw, possibly-absent
    CLI override): `citation_block_types` is the ACTUAL resolved
    classification set (`resolve_citation_block_types()`'s return value,
    sorted for canonical hashing), or `None` when the class-5 fail-safe is
    disabled. `windows_per_entity` is not consumed by this script's own
    scan logic at all -- it is a skeptic_setup.py/Part-B knob -- but is
    folded in here anyway so producer and verifier always hash the SAME
    single resolved-parameters blob, per the lead contract.
    """
    return {
        "dispersion_threshold": dispersion_threshold,
        "sample_cap": sample_cap,
        "windows_per_entity": windows_per_entity,
        "near_threshold": near_threshold,
        "near_cap": near_cap,
        "near_pair_budget": near_pair_budget,
        "research_mode": research_mode,
        "source_format": source_format,
        "citation_block_types": (
            sorted(resolved_citation_types) if resolved_citation_types is not None else None
        ),
    }


def compute_producer_input_digest(canon_state: str, canon_bytes: bytes,
                                   manifest_state: str, manifest_bytes: bytes,
                                   senses_state: str, senses_bytes: bytes,
                                   resolved_params: dict,
                                   language_config_raw_bytes: bytes,
                                   script_dir: Path) -> str:
    """THE one shared producer_input_digest algorithm: sha256 hex over --
    in this fixed order -- `canon_state`, `canon_bytes`, `manifest_state`,
    `manifest_bytes`, `senses_state`, `senses_bytes` (#243: the
    `canon_senses.json` sidecar's own raw bytes -- an absent sidecar is
    `b""`, tolerant-read the SAME way `canon_bytes` already is, NEVER via
    `canon_senses.load_senses_from_snapshot()`, which exposes no raw bytes
    and would make an absent sidecar indistinguishable from a schema-valid
    logically-empty
    one), the canonically-serialized `resolved_params` (see
    `resolved_scan_params()`), `language_config_raw_bytes` (the resolved
    particle-config FILE's own exact bytes --
    `bootstrap_names.LanguageConfig.raw_bytes`), then the raw bytes of every
    file named in `PRODUCER_CODE_CLOSURE`, read FRESH off `script_dir`
    (never cached, never trusted from a caller). Each part is separated by
    a single NUL byte so two adjacent parts can never collide by boundary
    concatenation (e.g. `"AB"+"C"` vs `"A"+"BC"` hashing identically with no
    separator).

    Codex round 4: `canon_state`/`manifest_state`/`senses_state` (each
    "absent"/"regular"/"irregular", see `_frozen_input_path_state`) are
    hashed ALONGSIDE their matching bytes, never folded into the bytes
    themselves (a caller elsewhere -- `main()`'s own `json.loads(canon_bytes)`
    -- still needs `canon_bytes` to be the LITERAL file content). Without
    this, a purely STATE-only change (an absent sidecar replaced by a
    genuinely-empty regular file, or by a directory -- content `b""` either
    way) is invisible to this digest: the worklist-freshness check this
    digest drives would then treat a project whose frozen inputs changed
    STATE as still-fresh, and (transitively, since `skeptic_setup.py`'s own
    resume digest hashes the worklist's raw bytes) could let `skeptic_setup.py`
    silently RESUME an existing run and overwrite its H1 stamps with the new
    state, laundering the very state-only mutation H1 (round 2/3) exists to
    catch. Caller's responsibility to resolve each `*_state` the SAME way
    `compute_frozen_input_hash`/`read_frozen_input_snapshot` would (never a
    second, independently-drifting classification).

    `suspicion_scan.py` calls this to STAMP a freshly-produced worklist;
    `skeptic_setup.py` (Part B) imports this EXACT function to RECOMPUTE
    and compare against a worklist's stored `producer_input_digest` before
    trusting it -- the two must never drift into two independent
    algorithms that could disagree on the same inputs.
    """
    hasher = hashlib.sha256()
    parts = [canon_state.encode("ascii"), canon_bytes,
             manifest_state.encode("ascii"), manifest_bytes,
             senses_state.encode("ascii"), senses_bytes,
             _canonical_json_bytes(resolved_params), language_config_raw_bytes]
    for member in PRODUCER_CODE_CLOSURE:
        parts.append((script_dir / member).read_bytes())
    for part in parts:
        hasher.update(part)
        hasher.update(b"\x00")
    return hasher.hexdigest()


# ---------------------------------------------------------------------------
# H1 frozen-input tamper hashing -- the ONE shared algorithm skeptic_setup.py
# (stamps canon_sha256/manifest_sha256/senses_sha256 at setup time) and
# skeptic_ready.py (re-hashes and compares at --verify-merged /
# --check-frozen-inputs time) both import, mirroring
# compute_producer_input_digest's own "one algorithm, never two independently
# -drifting copies" discipline immediately above -- H1 is exactly the same
# class of problem: a producer stamp and a verifier re-hash that must always
# agree on the identical bytes for the identical on-disk state.
# ---------------------------------------------------------------------------

def _frozen_input_path_state(path: Path) -> str:
    """Classifies `path` as "absent" / "regular" / "irregular" -- a small,
    deliberate LOCAL duplicate of `canon_senses.py`'s own private
    `_path_state` (never imported across the module boundary; mirrors this
    plugin's existing "duplicate a tiny primitive rather than import a
    sibling module's private name" convention, e.g. `skeptic_setup.py`'s own
    `RUN_ID_RE`; pinned by `tests/frozen_input_path_state_parity.test.py`,
    which asserts the two classifiers never disagree). Stays PRIVATE to
    this module -- `skeptic_setup.py` (codex round 3) reaches
    `read_frozen_input_snapshot()` below instead of this raw classifier
    directly, so this function's own name/signature is free to change
    without touching that caller. Presence is detected via `os.path.lexists`
    (never `Path.exists`, which follows symlinks and would misreport a
    dangling symlink as absent); a present path only counts as "regular" if
    `Path.is_file()` is also true."""
    if not os.path.lexists(path):
        return "absent"
    if path.is_file():
        return "regular"
    return "irregular"


def compute_frozen_input_hash_from_state(state: str, content: bytes) -> str:
    """H1's own tamper-detection hash CORE: sha256 hex over `state`
    ("absent"/"regular"/"irregular", see `_frozen_input_path_state`) plus
    `content` (the caller's own responsibility to have resolved
    consistently with `state` -- `b""` for anything but "regular"),
    separated by a single NUL byte. Pure -- no I/O of any kind.

    Codex round 3: this is deliberately SEPARATE from any path-reading --
    `skeptic_setup.py` (the STAMPER) must hash the EXACT (state, content)
    snapshot it already captured at derivation-read time (before the
    freshness/worklist validation that snapshot fed, via
    `read_frozen_input_snapshot()` below), never a fresh re-read of the path
    moments later when it publishes the aggregate. A path-based re-read at
    stamp time launders any mutation that happens in that window: the stamp
    would describe the MUTATED state while everything else this run derived
    (the worklist check, the assignments, `input_digest`) still describes
    the ORIGINAL one -- a real, disk-level instance of the trust-the-caller
    class this whole H1 mechanism exists to close, just moved to a
    different boundary. `compute_frozen_input_hash` below (path-based,
    re-reads) remains correct for a VERIFIER that has no reason to keep the
    read around, whose entire job IS to re-read fresh and compare against
    what was stamped -- the STAMPER and the VERIFIER need opposite
    freshness semantics from the same hash formula, which is exactly why
    the formula itself is split out here as its own pure function.
    `skeptic_ready.py` (this codebase's own VERIFIER) is NOT that caller,
    though: since codex round 7 it calls `read_frozen_input_snapshot()`
    plus THIS function directly for canon.json/manifest.json/
    canon_senses.json alike -- the STAMPER's own shape, not
    `compute_frozen_input_hash`'s -- because two of the three snapshots
    also feed a downstream parse it needs to stay byte-consistent with
    (see `skeptic_ready.py`'s own `frozen_input_check()` docstring). Its
    test suite remains the one caller left that still wants the
    read-fresh-and-hash-NOW convenience, to stamp fixtures.
    """
    hasher = hashlib.sha256()
    hasher.update(state.encode("ascii"))
    hasher.update(b"\x00")
    hasher.update(content)
    return hasher.hexdigest()


def read_frozen_input_snapshot(path: Path) -> tuple:
    """Reads `path` EXACTLY ONCE, returning `(state, content)` -- the
    snapshot `compute_frozen_input_hash_from_state()` needs. This is what a
    STAMPER (`skeptic_setup.py`, codex round 3) calls at derivation-read
    time: it captures the tuple once, uses it for whatever validation
    follows (the freshness/worklist check), and later hashes THAT SAME
    captured tuple for the H1 stamp -- never touching `path` again in
    between. `compute_frozen_input_hash()` below (which calls this and
    hashes immediately) is the read-fresh-and-hash-NOW convenience a
    VERIFIER wants instead."""
    state = _frozen_input_path_state(path)
    content = path.read_bytes() if state == "regular" else b""
    return state, content


def compute_frozen_input_hash(path: Path) -> str:
    """H1's own tamper-detection hash for ONE frozen input file (canon.json
    / manifest.json / canon_senses.json), READ FRESH off `path` -- correct
    for a VERIFIER re-hashing "what's on disk right now" to compare against
    a stamp, PROVIDED it has no reason to keep the read around afterward
    (see `compute_frozen_input_hash_from_state`'s own docstring, codex
    round 7, for why `skeptic_ready.py` -- this codebase's own VERIFIER --
    is no longer such a caller in production: it needs the captured
    snapshot itself, not just this function's hash, so it calls
    `read_frozen_input_snapshot()` + `compute_frozen_input_hash_from_state()`
    directly instead). `skeptic_ready.py`'s own test suite still imports
    this one, purely to stamp fixtures conveniently. A STAMPER must NOT
    call this -- see `compute_frozen_input_hash_from_state`'s own docstring
    (codex round 3) for why re-reading at stamp time is dangerous
    specifically for a stamper, and never for a verifier.

    Codex round-2 finding this formula itself closes: hashing raw content
    bytes ALONE (the pre-round-2 scheme) makes an absent file, a
    genuinely-empty regular file, and a directory/dangling-symlink all hash
    IDENTICALLY to `sha256(b"")` -- replacing a stamped non-empty sidecar
    with an empty regular file, or with a directory, would silently NOT
    trip the tamper tripwire. Folding the state tag in makes all three
    states produce DIFFERENT hashes, closing that hole for every
    H1-covered input, not just the one codex happened to probe.
    """
    state, content = read_frozen_input_snapshot(path)
    return compute_frozen_input_hash_from_state(state, content)


# ---------------------------------------------------------------------------
# Self-validation + atomic write
# ---------------------------------------------------------------------------

def _validate_worklist(worklist: dict) -> None:
    """Self-validates the PRODUCED worklist against the pinned
    suspicion-worklist.schema.json before it is ever written to disk -- a
    producer bug must fail LOUD here, never silently ship a schema-invalid
    artifact `skeptic_setup.py` would then have to reject anyway."""
    schema_path = SCHEMAS_DIR / SUSPICION_WORKLIST_SCHEMA
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(worklist), key=lambda e: [str(p) for p in e.path])
    if errors:
        first = errors[0]
        loc = "/".join(str(p) for p in first.path) or "<root>"
        raise ValueError(
            f"suspicion_scan.py: produced worklist failed its own schema validation "
            f"at '{loc}': {first.message} -- this is a producer bug, refusing to write"
        )


def _write_json_atomic(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(path.name + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    tmp_path.replace(path)  # atomic on the same filesystem


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "Deterministic, confidence-INDEPENDENT structural-risk scan over a "
            "frozen canon.json (RFC #215 Phase 2). Emits suspicion_worklist.json, "
            "the sole input the skeptic pass (skeptic_setup.py) reads to decide who "
            "gets adversarially re-examined."
        ),
    )
    p.add_argument(
        "--canon", metavar="PATH", default=None,
        help=f"Path to canon.json (default: {DEFAULT_CANON_PATH}). Absent is "
             "tolerated (treated as an empty canon -- nothing to scan).",
    )
    p.add_argument(
        "--manifest", metavar="PATH", default=None,
        help=f"Path to manifest.json (default: {DEFAULT_MANIFEST_PATH}).",
    )
    p.add_argument(
        "--senses-path", metavar="PATH", default=None,
        help=f"Override the canon_senses.json path (default: {DEFAULT_SENSES_PATH}). "
             "Parsed to build the #243 ambiguity-competitors universe (union of every "
             "canon.json entry and every canon_senses.json form, split-only included) "
             "that drives class 8 (fold_collision) -- mirrors "
             "canon_adjudication_audit.py's own --senses-path/allow_absent convention: "
             "an implicit default sidecar that is genuinely absent is tolerated as "
             "empty; an EXPLICIT --senses-path that does not exist is a hard error "
             "instead (a typo'd path must never silently disable class 8).",
    )
    p.add_argument(
        "--particle-config", required=True, metavar="FILENAME",
        help="Bare filename under ${durable_root}/languages/ -- the profile's own "
             "source.language.particle_config LITERAL value.",
    )
    p.add_argument(
        "--languages-dir", metavar="PATH", default=None,
        help=f"Override the languages/ directory (default: {DEFAULT_LANGUAGES_DIR}).",
    )
    p.add_argument(
        "--research-mode", required=True, choices=("live", "offline"),
        help="The project's profile.yml glossary.research_mode value -- drives "
             "class 2 (established_offline).",
    )
    p.add_argument(
        "--source-format", required=True, metavar="FORMAT",
        help="The project's profile.yml source.format value (e.g. gutenberg_epub, "
             "plain_text, custom) -- drives class 5's (all_citation) adapter-safe "
             "citation-block-type resolution.",
    )
    p.add_argument("--dispersion-threshold", type=int, default=DISPERSION_THRESHOLD_DEFAULT)
    p.add_argument("--sample-cap", type=int, default=SAMPLE_CAP_DEFAULT)
    p.add_argument("--near-threshold", type=float, default=NEAR_THRESHOLD_DEFAULT)
    p.add_argument("--near-cap", type=int, default=NEAR_CAP_DEFAULT)
    p.add_argument("--near-pair-budget", type=int, default=NEAR_PAIR_BUDGET_DEFAULT)
    p.add_argument(
        "--windows-per-entity", type=int, default=WINDOWS_PER_ENTITY_DEFAULT,
        help="Not consumed by this script's own scan -- folded into "
             "producer_input_digest only, so a change forces a fresh worklist "
             "(skeptic_setup.py/Part B consumes the value itself).",
    )
    p.add_argument(
        "--citation-block-types", nargs="*", default=None, metavar="TYPE",
        help="Override the adapter-default citation-block type set for class 5 "
             "(all_citation) -- takes effect for ANY source.format, including "
             "custom/unknown ones that otherwise disable the class fail-safe. "
             "Passed with zero TYPEs, this is an explicit empty set (all_citation "
             "permanently disabled), distinct from omitting the flag entirely "
             "(which falls back to the source.format default).",
    )
    p.add_argument(
        "--out", metavar="PATH", default=None,
        help=f"Where to write suspicion_worklist.json (default: {DEFAULT_OUT_PATH}).",
    )
    return p


def main(argv=None) -> int:
    args = build_arg_parser().parse_args(argv)

    canon_path = Path(args.canon) if args.canon else DEFAULT_CANON_PATH
    manifest_path = Path(args.manifest) if args.manifest else DEFAULT_MANIFEST_PATH
    languages_dir = Path(args.languages_dir) if args.languages_dir else DEFAULT_LANGUAGES_DIR
    out_path = Path(args.out) if args.out else DEFAULT_OUT_PATH
    senses_path = Path(args.senses_path) if args.senses_path else DEFAULT_SENSES_PATH
    # allow_absent=True ONLY for the genuinely-implicit default -- an EXPLICIT
    # --senses-path that turns out missing must BLOCK (mirrors
    # canon_adjudication_audit.py's own convention exactly).
    allow_absent_senses = args.senses_path is None

    # codex round 4: canon/manifest/senses are each captured as a (state,
    # bytes) SNAPSHOT via read_frozen_input_snapshot() -- both feed
    # compute_producer_input_digest() below, state ALONGSIDE bytes (see that
    # function's own docstring for why a state-only change must move this
    # digest too, not just H1's own stamp).
    canon_state, canon_bytes = read_frozen_input_snapshot(canon_path)
    canon_entries = {}
    if canon_bytes:
        try:
            canon_doc = json.loads(canon_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            print(f"error: {canon_path} is not valid UTF-8 JSON: {exc}", file=sys.stderr)
            return 1
        if isinstance(canon_doc, dict) and isinstance(canon_doc.get("entries"), dict):
            canon_entries = canon_doc["entries"]

    manifest_state, manifest_bytes = read_frozen_input_snapshot(manifest_path)
    if manifest_state != "regular":
        print(f"error: manifest not found: {manifest_path}", file=sys.stderr)
        return 1
    try:
        manifest = json.loads(manifest_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        print(f"error: {manifest_path} is not valid UTF-8 JSON: {exc}", file=sys.stderr)
        return 1

    try:
        lang = load_language_config(args.particle_config, languages_dir)
    except BootstrapNamesError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    # #243: senses_bytes feeds producer_input_digest directly (tolerant raw
    # read, mirrors canon_bytes -- see compute_producer_input_digest's own
    # docstring on why NOT via a parsed load); the PARSED senses entries
    # feed the #243 ambiguity-competitors universe (class 8, fold_collision)
    # via canon_senses.fold_collision_map(). Both genuinely derive from the
    # SAME captured (state, bytes) snapshot, read once -- codex round 5:
    # load_senses_from_snapshot() parses THIS snapshot directly, never a
    # second, independent read of senses_path (the pre-fix shape let the
    # digest and the parsed entries silently disagree about which on-disk
    # version of the sidecar they each described).
    senses_state, senses_bytes = read_frozen_input_snapshot(senses_path)
    try:
        senses_result = load_senses_from_snapshot(
            senses_path, senses_state, senses_bytes, allow_absent=allow_absent_senses
        )
    except CanonSensesLoadError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    competitor_forms = set(canon_entries.keys()) | set(senses_result.entries_by_source_form.keys())
    competitors = fold_collision_map(competitor_forms)

    citation_override = (
        tuple(args.citation_block_types) if args.citation_block_types is not None else None
    )
    resolved_citation_types = resolve_citation_block_types(args.source_format, citation_override)

    entries_out, warnings = build_worklist(
        canon_entries, manifest, manifest_path, lang,
        research_mode=args.research_mode,
        citation_types=resolved_citation_types,
        dispersion_threshold=args.dispersion_threshold,
        sample_cap=args.sample_cap,
        near_threshold=args.near_threshold,
        near_cap=args.near_cap,
        near_pair_budget=args.near_pair_budget,
        competitors=competitors,
    )
    for w in warnings:
        print(f"warning: {w}", file=sys.stderr)

    resolved_params = resolved_scan_params(
        dispersion_threshold=args.dispersion_threshold,
        sample_cap=args.sample_cap,
        windows_per_entity=args.windows_per_entity,
        near_threshold=args.near_threshold,
        near_cap=args.near_cap,
        near_pair_budget=args.near_pair_budget,
        research_mode=args.research_mode,
        source_format=args.source_format,
        resolved_citation_types=resolved_citation_types,
    )
    digest = compute_producer_input_digest(
        canon_state, canon_bytes, manifest_state, manifest_bytes, senses_state, senses_bytes,
        resolved_params, lang.raw_bytes, SCRIPT_DIR,
    )

    worklist = {"schema_version": 1, "producer_input_digest": digest, "entries": entries_out}

    try:
        _validate_worklist(worklist)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    _write_json_atomic(out_path, worklist)
    print(
        f"suspicion_worklist: {len(entries_out)} flagged entries -> {out_path}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
