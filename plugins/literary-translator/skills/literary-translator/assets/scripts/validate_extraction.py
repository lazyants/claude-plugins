#!/usr/bin/env python3
"""validate_extraction.py -- managed post-extraction gate (issue #86).

The MANDATORY gate the pipeline runs immediately after ``extract.py`` writes
``manifest.json``. It exists to make a *false green* impossible: the round-trip
self-checks that ``extract.py`` runs on its own output live inside that file, so
a hand-adapted extractor could weaken or delete a check and still print "all
self-checks passed". This gate closes that hole two ways at once:

  A. It INDEPENDENTLY re-derives every manifest-derivable invariant straight
     from ``manifest.json`` (never trusting any result the extractor embedded),
     so a manifest that violates an invariant fails HERE even if the extractor
     reported it green.
  B. It PINS the extractor's self-check suite by hashing the sentinel-wrapped
     ``BEGIN/END SELF-CHECK REGION`` of the durable ``extract.py`` and comparing
     it to ``CURRENT_EXTRACTOR_SELFCHECK_HASH`` -- so the residual checks that
     are NOT re-derivable here (they read the extractor's in-memory build
     ``report``, absent from ``manifest.json``) still cannot be silently
     weakened: editing a self-check changes the hash and fails the gate.

Editing a self-check to reach green is a FALSE-GREEN ANTI-PATTERN. If a check
is genuinely wrong for your source, take the gap to a plugin issue -- do NOT
edit the shipped self-check region to make the gate pass.

**THIS SCRIPT IS NEVER COPIED TO ``durable_root``.** Like ``profile_validate.py``
it is always invoked directly from the plugin's own install path and
self-anchors relative to its own ``assets/scripts/`` location. It is NOT a
bundle member (nothing computes a generation hash over it) and it never writes
to ``durable_root``.

    python3 {{PLUGIN_ROOT}}/assets/scripts/validate_extraction.py \\
        --manifest ${durable_root}/manifest.json \\
        --extract  ${durable_root}/extract.py \\
        --profile  .claude/literary-translator/profile.yml

Order of operations:

  1. Read ``manifest.json`` (FATAL usage/env error, exit 2, if unreadable or
     not valid JSON).
  2. Read the two profile values this gate branches on -- ``project.
     max_segment_words`` and ``footnotes.apparatus_policy`` -- resolving them
     exactly as ``extract.py`` does (``yaml.safe_load``; the same
     ``VALID_APPARATUS_POLICIES`` set). A missing/unreadable profile, a
     non-mapping document, an absent required key, or an unknown
     apparatus_policy is a usage/env error (exit 2): the gate cannot know what
     to check without them.
  3. INDEPENDENTLY validate the manifest against the plugin's OWN
     ``manifest.schema.json`` (self-anchored, not a durable_root copy). Its
     top-level ``required`` + ``additionalProperties:false`` is what catches a
     manifest missing required keys (``source_inputs``, ``generation_hashes``,
     ...) or carrying stray ones -- a structurally-invalid manifest is a failed
     extraction and is FATAL (exit 1), so it can never be certified. (An
     unreadable/corrupt bundled schema is an install/env error, exit 2.)
  4. INDEPENDENTLY re-derive and check every manifest-derivable invariant
     (``run_derivable_checks``) -- collect ALL failures, never stop at the
     first. A schema-valid manifest that still lacks a check-only field, or has
     a value of the wrong type for one, is reported as a single FATAL
     validation failure (exit 1), not a crash.
  5. Read the durable ``extract.py`` and pin its self-check region:
     ``selfcheck_region_hash`` vs ``CURRENT_EXTRACTOR_SELFCHECK_HASH``. A
     missing/tampered region (``None``) or a hash mismatch is FATAL (exit 1),
     with a message naming the false-green anti-pattern and pointing genuine
     gaps to a plugin issue. (An unreadable ``--extract`` file is exit 2.)
     **SKIPPED when ``source.format`` is ``custom``** (read from ``--profile``
     via ``load_profile_values``): Step 0a still copies ``extract.py.template``
     to ``extract.py`` unconditionally, but for a custom source that copy is
     never adapted or run -- the real extractor that produced ``manifest.json``
     is the co-designed ``scripts/custom_extractors/<value>``, so pinning the
     unadapted template copy would only ever vacuously pass, certifying nothing
     (see ``references/source-format-adapters/custom.md``). ``region_ok``
     defaults ``True`` in this case; the gate's exit code for a custom source
     then depends only on steps 3-4.
  6. Print PASS/FAIL per check plus the region-pin result; exit 0 iff
     everything passed, 1 on any FATAL validation failure, 2 on usage/env
     error -- mirroring ``profile_validate.py``'s exit-code discipline.

REPORT-ONLY RESIDUAL -- the three self-checks this gate does NOT re-derive,
because their inputs live only in the extractor's in-memory build ``report``
(``body_toplevel_total`` / ``unclassified`` / ``orphan_fn`` /
``uncovered_verse_lines``), none of which are persisted to
``manifest.json`` (its schema forbids extra fields). (``n_verse_blocks`` is NOT
in this list: it is re-derivable as ``count(blocks type=="VERSE")`` and IS
re-checked, in ``verse_counts_reconcile`` below.) For gutenberg_epub/plain_text
they are covered ONLY by the region-hash pin (step 5), which guarantees the
extractor's own copy of them was not weakened; for a custom source (region pin
skipped) they are NOT covered here at all -- the custom extractor's own
equivalent of these checks is the co-designing project's own responsibility:

  * body_coverage_no_holes
  * no_orphan_footnote_continuation
  * verse_no_uncovered

Exit codes: 0 = clean; 1 = one or more FATAL validation failures (a derivable
check failed, the manifest was malformed, or the self-check region was
tampered/drifted); 2 = usage or environment error (bad CLI args, unreadable
manifest/extract/profile, missing dependency, missing profile key).
"""

import argparse
import hashlib
import json
import re
import sys
import unicodedata
from pathlib import Path
from typing import NoReturn

# Deferred dependency handles -- yaml (profile) and jsonschema (manifest schema
# validation) are imported only in the validation/main path (see
# _dependency_preflight), so merely importing this module for its pure helpers
# (selfcheck_region_hash, run_derivable_checks) never requires either package.
yaml = None
jsonschema = None

# ---------------------------------------------------------------------------
# Self-anchoring: like profile_validate.py, this script lives at the PLUGIN'S
# OWN ``assets/scripts/`` directory and is NEVER copied to a durable_root, so
# ``Path(__file__).resolve().parents[1]`` gives the plugin's ``assets/`` root.
# The durable manifest/extract/profile all arrive as CLI args, but the manifest
# SCHEMA is loaded from HERE (the plugin's own ``assets/schemas/``) -- never a
# durable_root copy -- so a tampered durable-root schema cannot weaken the gate.
# The anchor also locates requirements.txt for an actionable install message.
# ---------------------------------------------------------------------------
ASSETS_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_SCHEMA_PATH = ASSETS_ROOT / "schemas" / "manifest.schema.json"

# The apparatus policies extract.py branches its footnote self-checks on --
# transcribed verbatim from extract.py.template's VALID_APPARATUS_POLICIES so
# an unknown value is rejected here exactly as the extractor rejects it.
VALID_APPARATUS_POLICIES = ("translate_all", "preserve_source", "omit_apparatus", "body_refs_only")

# Marker regexes copied VERBATIM from extract.py.template (the shared literals
# near its top, which live OUTSIDE the pinned self-check region). They MUST stay
# byte-identical to the extractor's; a drift here would silently diverge this
# gate's fnref/body-ref re-derivation from the extractor's own.
FNREF_RE = re.compile(r"⟦FNREF_(\d+)⟧")
BODY_REF_MARKER_RE = re.compile(r"\[(\d+)\]")

# Broad heading-like allowlist (#210 R2) -- a BYTE-IDENTICAL duplicate of
# validate_assembled.py's own BROAD_HEADING_LIKE_RE. This plugin's standing
# rule is "no shared util module -- cross-cutting helpers are duplicated
# byte-identically, guarded by a drift test"
# (tests/heading_like_regex_drift.test.py pins the two copies against each
# other). Never edit this literal without also updating the sibling copy.
# An exact (not substring) match against a block's raw `type` tag; "HEAD"
# deliberately does NOT match (HEADING != HEAD, H[1-6] != HEAD), which is
# what keeps every shipped gutenberg_epub/plain_text adapter out of the
# population this gate can ever fire against.
BROAD_HEADING_LIKE_RE = re.compile(
    r"^(?:HEADING|TITLE|CHAPTER|SECTION|PART|SIMAN|PEREK|H[1-6])$", re.IGNORECASE
)

# ---------------------------------------------------------------------------
# Self-check region pin (issue #86)
# ---------------------------------------------------------------------------
# These two sentinel-line prefixes and the normalization below are the SINGLE
# source of truth for the region hash. extractor_selfcheck_hash_drift.test.py
# (T2) imports selfcheck_region_hash / BEGIN_SENTINEL_PREFIX /
# END_SENTINEL_PREFIX from this module rather than re-implementing them, so the
# extractor's drift test and this gate can never disagree by construction.
BEGIN_SENTINEL_PREFIX = "# BEGIN SELF-CHECK REGION"
END_SENTINEL_PREFIX = "# END SELF-CHECK REGION"

# The pinned hash of the shipped extract.py.template's self-check region. Filled
# by the plugin build (the LEAD) via selfcheck_region_hash(<final template
# text>) AFTER the region is finalized -- never computed or hardcoded here. Until
# it is filled, the region-pin check FAILS by design, so an un-provisioned build
# cannot certify green.
CURRENT_EXTRACTOR_SELFCHECK_HASH = "d81dd7c83488f4d1ff2cf9a71569e477c6982e40"


def selfcheck_region_hash(extract_py_text: str):
    """SHA-1 of the SELF-CHECK REGION between the sentinels. Returns None if the
    region is absent/malformed (the caller treats None as a FATAL 'region
    missing/tampered').

    Normalization (defined ONCE here; imported, never duplicated):
      1. find exactly one line beginning with BEGIN_SENTINEL_PREFIX and exactly
         one beginning with END_SENTINEL_PREFIX -- 0 or >1 of either, or an END
         that does not follow its BEGIN, -> None (malformed/absent region);
      2. take the lines strictly BETWEEN them (both sentinel lines excluded);
      3. rstrip() each such line (kills trailing whitespace / stray CR), join
         with "\\n", with NO trailing newline;
      4. return hashlib.sha1(normalized.encode("utf-8")).hexdigest().
    """
    lines = extract_py_text.splitlines()
    begin_idxs = [i for i, ln in enumerate(lines) if ln.startswith(BEGIN_SENTINEL_PREFIX)]
    end_idxs = [i for i, ln in enumerate(lines) if ln.startswith(END_SENTINEL_PREFIX)]
    if len(begin_idxs) != 1 or len(end_idxs) != 1:
        return None
    begin, end = begin_idxs[0], end_idxs[0]
    if begin >= end:
        return None
    region_lines = lines[begin + 1:end]
    normalized = "\n".join(line.rstrip() for line in region_lines)
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Dependency preflight (yaml) -- mirrors profile_validate.py
# ---------------------------------------------------------------------------

def _find_requirements_txt(max_up: int = 6):
    here = Path(__file__).resolve()
    for ancestor in list(here.parents)[:max_up]:
        candidate = ancestor / "requirements.txt"
        if candidate.is_file():
            return candidate
    return None


def _die_missing_dependency(package_name: str) -> NoReturn:
    req_path = _find_requirements_txt()
    where = str(req_path) if req_path else (
        "requirements.txt (see the literary-translator plugin's own root directory)"
    )
    print(
        f"ERROR: this gate requires the {package_name!r} Python package. "
        f"Install with: pip install -r {where}",
        file=sys.stderr,
    )
    sys.exit(2)


def _dependency_preflight():
    """Imports the packages the validation path needs -- PyYAML (to read the
    profile) and jsonschema (to validate the manifest) -- populating the
    module-level handles. Exits 2 with an actionable, package-named message on
    ImportError. Deferred so importing this module for its pure helpers
    (selfcheck_region_hash, run_derivable_checks) needs neither package."""
    global yaml, jsonschema
    try:
        import yaml as _yaml
    except ImportError:
        _die_missing_dependency("PyYAML")
    try:
        import jsonschema as _jsonschema
    except ImportError:
        _die_missing_dependency("jsonschema")
    yaml = _yaml
    jsonschema = _jsonschema


# ---------------------------------------------------------------------------
# Profile: resolve the two values the gate branches on
# ---------------------------------------------------------------------------

def load_profile_values(profile_path: Path):
    """Returns (max_segment_words, apparatus_policy, source_format), resolved
    exactly as extract.py resolves the first two (``project.max_segment_words``
    and ``footnotes.apparatus_policy`` via yaml.safe_load). Any unreadable/non-YAML/
    non-mapping profile, a missing required key, or an unknown apparatus_policy
    is a usage/env error (exit 2): the gate cannot decide what to check without
    them, and profile_validate.py (Step 0) is the place those are diagnosed in
    full. ``source_format`` (``profile["source"]["format"]``) is read
    best-effort -- a missing/malformed value is tolerated as ``None`` (treated
    as non-custom, fail-safe) rather than escalated to exit 2, since this gate's
    hard requirements are only the two values above."""
    assert yaml is not None, "_dependency_preflight() must run before load_profile_values()"
    try:
        text = profile_path.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"ERROR: could not read profile {profile_path}: {exc}", file=sys.stderr)
        sys.exit(2)
    try:
        profile = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        print(f"ERROR: {profile_path} is not valid YAML: {exc}", file=sys.stderr)
        sys.exit(2)
    if not isinstance(profile, dict):
        print(
            f"ERROR: {profile_path} did not parse to a mapping "
            f"(got {type(profile).__name__}).",
            file=sys.stderr,
        )
        sys.exit(2)

    try:
        max_segment_words = profile["project"]["max_segment_words"]
        apparatus_policy = profile["footnotes"]["apparatus_policy"]
    except (KeyError, TypeError) as exc:
        print(
            f"ERROR: {profile_path} is missing a key this gate needs "
            f"(project.max_segment_words and footnotes.apparatus_policy): {exc}. "
            f"Run Step 0 (profile_validate.py) first.",
            file=sys.stderr,
        )
        sys.exit(2)

    if not isinstance(max_segment_words, int) or isinstance(max_segment_words, bool):
        print(
            f"ERROR: {profile_path}: project.max_segment_words must be an integer "
            f"(got {max_segment_words!r}). Run Step 0 first.",
            file=sys.stderr,
        )
        sys.exit(2)
    if apparatus_policy not in VALID_APPARATUS_POLICIES:
        print(
            f"ERROR: {profile_path}: footnotes.apparatus_policy is {apparatus_policy!r}, "
            f"not one of {VALID_APPARATUS_POLICIES}. Run Step 0 first.",
            file=sys.stderr,
        )
        sys.exit(2)
    source_format = None
    source = profile.get("source")
    if isinstance(source, dict):
        fmt = source.get("format")
        if isinstance(fmt, str):
            source_format = fmt

    return max_segment_words, apparatus_policy, source_format


# ---------------------------------------------------------------------------
# Independent manifest schema validation
# ---------------------------------------------------------------------------

def _load_manifest_schema() -> dict:
    """Loads the plugin's OWN manifest.schema.json (self-anchored, never a
    durable_root copy -- tamper-independent). An unreadable or corrupt bundled
    schema is a broken plugin install, i.e. an environment error (exit 2)."""
    try:
        return json.loads(MANIFEST_SCHEMA_PATH.read_text(encoding="utf-8"))
    except OSError as exc:
        print(
            f"ERROR: could not read the bundled manifest schema "
            f"{MANIFEST_SCHEMA_PATH}: {exc}",
            file=sys.stderr,
        )
        sys.exit(2)
    except json.JSONDecodeError as exc:
        print(
            f"ERROR: the bundled manifest schema {MANIFEST_SCHEMA_PATH} is not "
            f"valid JSON: {exc}",
            file=sys.stderr,
        )
        sys.exit(2)


def validate_manifest_schema(manifest: dict):
    """Independently validates the manifest against the plugin's own
    manifest.schema.json, whose top-level ``required`` +
    ``additionalProperties:false`` is exactly what rejects a manifest missing a
    required top-level key (e.g. ``source_inputs`` / ``generation_hashes``) or
    carrying a stray one. Returns a list of location-named error strings --
    empty iff the manifest is schema-valid. Plain validation; this schema needs
    no format-checker."""
    assert jsonschema is not None, "_dependency_preflight() must run before validate_manifest_schema()"
    schema = _load_manifest_schema()
    # A structurally-broken BUNDLED schema is an install/env error, not a
    # manifest failure -- validate the schema itself (check_schema) and treat a
    # SchemaError (or any validator-construction error) as exit 2, mirroring the
    # unreadable-schema path, instead of crashing with a traceback later.
    try:
        jsonschema.Draft202012Validator.check_schema(schema)
        validator = jsonschema.Draft202012Validator(schema)
    except (jsonschema.exceptions.SchemaError, TypeError) as exc:
        print(
            f"ERROR: the bundled manifest schema {MANIFEST_SCHEMA_PATH} is itself "
            f"not a valid JSON Schema (broken plugin install): {exc}",
            file=sys.stderr,
        )
        sys.exit(2)
    errors = sorted(validator.iter_errors(manifest), key=lambda e: [str(p) for p in e.path])
    formatted = []
    for e in errors:
        location = ".".join(str(p) for p in e.path) or "<root>"
        formatted.append(f"{location}: {e.message}")
    return formatted


# ---------------------------------------------------------------------------
# Independent re-derivation of the manifest-derivable self-checks
# ---------------------------------------------------------------------------

def run_derivable_checks(manifest: dict, apparatus_policy: str, max_segment_words: int):
    """Independently re-derives and checks EVERY manifest-derivable invariant,
    straight from ``manifest.json`` -- ignoring any result the extractor may
    have embedded. Returns a list of (name, ok, detail) tuples; collects ALL
    failures (never raises for a failed check). Semantics mirror
    extract.py.template's run_self_checks exactly for the derivable subset --
    plus the GATE-ONLY checks that have no counterpart in that suite at all
    (#210's two heading checks, and #397's two empty-block checks), which are
    re-derived here because the manifest is the only place they are visible.

    Raises KeyError/TypeError only if the manifest is so structurally malformed
    that an invariant cannot even be evaluated -- main() converts that into a
    single FATAL validation failure rather than a crash."""
    results = []

    def chk(name, ok, detail=""):
        results.append((name, bool(ok), detail))

    blocks = manifest["blocks"]          # dict: id -> block
    segments = manifest["segments"]      # list
    footnotes = manifest["footnotes"]    # list
    verse = manifest["verse"]
    verse_store = verse["store"]
    frontback = manifest["frontback"]
    spine = manifest["spine"]

    # 1. block-ID uniqueness (dict keyed by id, but re-check the inner ids)
    block_ids = [b["id"] for b in blocks.values()]
    chk("block_ids_unique", len(blocks) == len(set(block_ids)), f"n_blocks={len(blocks)}")

    # 1b. GATE-ONLY referential integrity -- has no run_self_checks counterpart;
    #     vanilla JSON Schema cannot express these cross-references (block_ids is
    #     just an array of string, blocks has no propertyNames), so the gate
    #     re-derives them and is deliberately STRICTER than the extractor:
    #       (a) every blocks{} entry's inner id equals its dict key;
    #       (b) every segments[].block_ids entry resolves to a real block key;
    #       (c) every footnotes[].anchor_block/def_block and verse.store[].
    #           parent_block resolves to a real block key WHEN non-empty (the ""
    #           sentinel = dangling/never-mounted is skipped; a non-string value
    #           is a schema concern, caught upstream, so it is skipped here too).
    #     Note: (a) makes block_ids_unique redundant (id==key => keys unique =>
    #     ids unique), so a duplicate-id manifest fails BOTH by construction.
    id_key_mismatch = [f"{k}->{v.get('id')!r}" for k, v in blocks.items() if v.get("id") != k]
    dangling_segment_refs = sorted({bid for s in segments for bid in s["block_ids"] if bid not in blocks})
    dangling_ref_targets = set()
    for f in footnotes:
        for val in (f.get("anchor_block"), f.get("def_block")):
            if isinstance(val, str) and val and val not in blocks:
                dangling_ref_targets.add(val)
    for e in verse_store:
        pb = e.get("parent_block")
        if isinstance(pb, str) and pb and pb not in blocks:
            dangling_ref_targets.add(pb)
    dangling_ref_targets = sorted(dangling_ref_targets)
    chk(
        "block_graph_integrity",
        not id_key_mismatch and not dangling_segment_refs and not dangling_ref_targets,
        f"id_key_mismatch={id_key_mismatch[:5]} "
        f"dangling_segment_block_ids={dangling_segment_refs[:5]} "
        f"dangling_ref_targets={dangling_ref_targets[:5]}",
    )

    # 2. spine order preserved
    chk(
        "spine_order_preserved",
        spine == sorted(spine, key=lambda x: x["pos"]),
        f"n_spine={len(spine)}",
    )

    # 3. every body segment carries at least one content block
    body_segments = [s for s in segments if s["kind"] == "body"]
    empty = [s["seg"] for s in body_segments if not (s["n_para"] + s["n_verse"] + s["n_quote"])]
    chk("segmentation_nonempty", not empty, f"n_body_segments={len(body_segments)} empty={empty}")

    # 4. #83: a GLOBAL backstop -- fires only when a source that HAS body files
    #    yields ZERO body segments in total (the wrapper-collapse bug this
    #    catches). It is NOT a per-file guarantee: a single body file collapsing
    #    is not manifest-derivable (the collapse still emits that file's
    #    FRONTBACK blocks, so the file still has blocks) -- that case is
    #    prevented by the extractor's flatten fix + `segmentation_nonempty`, not
    #    here. Body-file count is derived from the SPINE (klass=="body"), never a
    #    report.
    n_body_files = sum(1 for s in spine if s["klass"] == "body")
    chk(
        "body_files_yield_segments",
        not (n_body_files > 0 and len(body_segments) == 0),
        f"n_body_files={n_body_files} n_body_segments={len(body_segments)}",
    )

    # 5. no body segment built entirely from footnote-definition files
    notes_files = {s["file"] for s in spine if s["klass"] == "footnote-defs"}
    pseudo = [s["seg"] for s in body_segments if notes_files and set(s["source_files"]) <= notes_files]
    chk("no_pseudo_segments_from_notes", not pseudo, f"pseudo={pseudo}")

    # 6. footnote checks, branched EXACTLY as run_self_checks branches
    if apparatus_policy in ("translate_all", "preserve_source"):
        anchors = {f["n"] for f in footnotes if f["anchor_block"]}
        defs = {f["n"] for f in footnotes if f["def_block"]}
        chk(
            "fn_bijection", anchors == defs,
            f"anchors={len(anchors)} defs={len(defs)} "
            f"dangling_anchor={sorted(anchors - defs)[:8]} dangling_def={sorted(defs - anchors)[:8]}",
        )

        ref_count, ref_block = {}, {}
        for b in blocks.values():
            for m in FNREF_RE.findall(b["plain_text"]):
                n = int(m)
                ref_count[n] = ref_count.get(n, 0) + 1
                ref_block.setdefault(n, set()).add(b["id"])
        # ALSO scan footnotes cited INSIDE an embedded verse -- mirrors
        # extract.py.template's #93 Fix B post-mount scan byte-for-byte (the
        # carrier block's plain_text holds only the ⟦VERSE_...⟧ placeholder,
        # the ⟦FNREF_n⟧ sentinel lives on the verse.store entry itself).
        for e in verse_store:
            if e.get("mount") != "embedded":
                continue
            parent = e.get("parent_block")
            if not parent:
                # Unmounted embedded verse -> surfaced by
                # verse_placeholders_unique_and_mounted below. NEVER add None to
                # ref_block: a set mixing None + a real block id crashes
                # `sorted(bs)` at the `multi` comprehension below, aborting
                # run_derivable_checks before the unmounted entry can report.
                continue
            for m in FNREF_RE.findall(e.get("plain_text", "")):
                n = int(m)
                ref_count[n] = ref_count.get(n, 0) + 1
                ref_block.setdefault(n, set()).add(parent)
        dup = {n: c for n, c in ref_count.items() if c != 1}
        multi = {n: sorted(bs) for n, bs in ref_block.items() if len(bs) != 1}
        chk(
            "fnref_sentinel_unique",
            not dup and not multi and set(ref_count) == {f["n"] for f in footnotes},
            f"n_refs={len(ref_count)} dup={dict(list(dup.items())[:5])} multi={dict(list(multi.items())[:5])}",
        )
    elif apparatus_policy == "body_refs_only":
        markers = []
        for b in blocks.values():
            markers.extend(BODY_REF_MARKER_RE.findall(b["plain_text"]))
        # ALSO scan a literal [n] marker inside an embedded verse (mirrors the
        # template's #93 Fix B, same carrier-only-holds-the-placeholder reasoning).
        for e in verse_store:
            if e.get("mount") != "embedded" or not e.get("parent_block"):
                continue
            markers.extend(BODY_REF_MARKER_RE.findall(e.get("plain_text", "")))
        dup = {n: markers.count(n) for n in set(markers) if markers.count(n) != 1}
        chk("body_ref_markers_well_formed_and_unique", not dup, f"n_markers={len(markers)} duplicated={dup}")
    else:  # omit_apparatus
        chk("footnote_checks_not_applicable", True, f"apparatus_policy={apparatus_policy}")

    # 7. front-back inventory + the translate<->segments[] cross-reference
    missing = [
        x["id"] for x in frontback
        if x["decision"] not in ("translate", "regenerate", "omit") or not x["reason"]
    ]
    seg_ids = {s["seg"] for s in segments if s["kind"] == "frontback"}
    fb_translate_ids = {x["id"] for x in frontback if x["decision"] == "translate"}
    fb_other_ids = {x["id"] for x in frontback if x["decision"] != "translate"}
    missing_from_segments = sorted(fb_translate_ids - seg_ids)
    leaked_into_segments = sorted(fb_other_ids & seg_ids)
    chk(
        "frontback_inventory",
        not missing and not missing_from_segments and not leaked_into_segments,
        f"n_fb={len(frontback)} missing_fields={missing} "
        f"missing_from_segments={missing_from_segments} leaked_into_segments={leaked_into_segments}",
    )

    # 8. verse store: placeholders unique, every entry mounted
    placeholders = [e["placeholder"] for e in verse_store]
    unmounted = [e["vid"] for e in verse_store if not e["parent_block"]]
    chk(
        "verse_placeholders_unique_and_mounted",
        len(placeholders) == len(set(placeholders)) and not unmounted,
        f"n={len(placeholders)} unique={len(set(placeholders))} unmounted={unmounted[:5]}",
    )

    # 9. #84: no verse.store entry with empty/whitespace plain_text
    empty_verse = [e["vid"] for e in verse_store if not (e.get("plain_text") or "").strip()]
    chk("verse_plain_text_nonempty", not empty_verse, f"empty_verse={empty_verse[:5]}")

    # 10. no segment may exceed max_segment_words
    offenders = [(s["seg"], s["word_count"]) for s in segments if s["word_count"] > max_segment_words]
    chk(
        "no_segment_exceeds_max_words",
        not offenders,
        f"max_segment_words={max_segment_words} offenders={offenders}",
    )

    # 11. verse counts reconcile (FULL) -- n_verse_blocks RE-DERIVED from block
    #     types, never a persisted scalar/report.
    n_verse_blocks = sum(1 for b in blocks.values() if b["type"] == "VERSE")
    chk(
        "verse_counts_reconcile",
        verse["n_block"] + verse["n_embedded"] == verse["n_nodes"] and verse["n_block"] == n_verse_blocks,
        f"block={verse['n_block']} embedded={verse['n_embedded']} nodes={verse['n_nodes']} "
        f"n_verse_blocks={n_verse_blocks}",
    )

    # 12. #210 R2: FAIL LOUD when `heading_types` is wholly ABSENT from the
    #     manifest and at least one block's raw `type` looks heading-shaped.
    #     An explicit `heading_types: []` is a POSITIVE opt-out ("this source
    #     has no heading blocks") and must NOT trigger this -- only the key's
    #     outright absence does, so `"heading_types" not in manifest` (never
    #     `manifest.get("heading_types")` falsy-checked) is the condition.
    #     Without this, a custom extractor emitting e.g. CHAPTER and
    #     forgetting to declare it silently renders every heading as an
    #     undifferentiated H2 with `_segment_title` falling back to the raw
    #     seg id -- and the only existing diagnostic
    #     (validate_assembled.collect_undeclared_heading_like_warnings) is a
    #     non-gating WARN that does not run until AFTER the whole book is
    #     translated. "HEAD" can never appear here: it does not match
    #     BROAD_HEADING_LIKE_RE, so every shipped gutenberg_epub/plain_text
    #     project (which never sets heading_types) is provably untouched.
    if "heading_types" in manifest:
        undeclared_heading_like = []
    else:
        undeclared_heading_like = sorted({
            b["type"] for b in blocks.values() if BROAD_HEADING_LIKE_RE.fullmatch(b["type"])
        })
    chk(
        "heading_types_declared_when_heading_shaped_blocks_exist",
        not undeclared_heading_like,
        (
            f"heading_types is absent from the manifest but these block types look "
            f"heading-shaped: {undeclared_heading_like} -- either list them in "
            f"heading_types, or set heading_types: [] to affirm this source has no "
            f"heading blocks"
        ) if undeclared_heading_like else "",
    )

    # 13. #210 R1 cross-field guard (not expressible in manifest.schema.json's
    #     vanilla JSON Schema -- a cross-property key-set comparison is
    #     outside its reach): every key of the optional `heading_levels` map
    #     must be a member of `heading_types ∪ {"HEAD"}`. A key outside that
    #     set is a typo that would otherwise silently no-op (assemble.py only
    #     ever reads heading_levels.get(raw_type, ...) for a type that IS a
    #     heading, so a mistyped key is never applied and never diagnosed
    #     anywhere else). assemble.py independently re-raises this as
    #     AssembleError -- this gate must not trust that W2 ran, since
    #     assemble.py is also reachable on a resumed project.
    declared_heading_types = set(manifest.get("heading_types") or ()) | {"HEAD"}
    heading_levels = manifest.get("heading_levels") or {}
    undeclared_level_keys = sorted(k for k in heading_levels if k not in declared_heading_types)
    chk(
        "heading_levels_keys_are_declared_heading_types",
        not undeclared_level_keys,
        (
            f"heading_levels keys not in heading_types ∪ {{'HEAD'}}: "
            f"{undeclared_level_keys}"
        ) if undeclared_level_keys else "",
    )

    # 14. #397: a content unit that reaches a draft but carries no text for the
    #     translator makes its segment converge ONLY on an invented-text draft:
    #     the faithful (empty) draft is rejected forever, and nothing said so
    #     until the paid translation job had already run. TWO checks, because
    #     two different branches of validate_draft.py judge the two populations
    #     and they do NOT agree on what "empty" means. Both are gate-only: the
    #     extractor's own self-check region has no counterpart for either.
    #
    # 14a. Segment blocks. segpack.py copies source_html/plain_text verbatim for
    #      every id in a segment's block_ids, so manifest block -> segpack block
    #      is 1:1. validate_draft._block_source_text() then prefers plain_text
    #      and falls back to source_html, and the empty-translation branch fires
    #      only when the draft block is blank AND that fallback is truthy.
    #
    #      The test is FALSY plain_text, deliberately NOT `.strip()`-empty:
    #      _block_source_text tests `if pt:`, so a whitespace-only plain_text is
    #      TRUTHY, is returned verbatim, and leaves src_text.strip() empty --
    #      the guard does not fire and that block CONVERGES today. A
    #      `.strip()`-based test here would be a false RED on it. (The shipped
    #      extractor cannot emit whitespace-only text anyway: normalize_text
    #      collapses it. This matters for a custom extractor.)
    #
    #      A block that is the parent of EXACTLY ONE non-embedded verse is
    #      exempt: validate_draft requires that block's draft value to equal the
    #      verse placeholder and `continue`s before ever reaching the
    #      empty-translation branch, so it converges with no text of its own.
    #      Exactly one, not "any": a parent claimed by SEVERAL non-embedded
    #      verses is reported as a SOURCE DEFECT and deliberately kept OUT of
    #      that placeholder map, so it does fall through and must stay checked.
    #      The exemption is derived from verse.store, never from the literal
    #      type name "VERSE" -- manifest.schema.json states block `type` is
    #      deliberately not a fixed enum, so a name-keyed test would be blind on
    #      exactly the adapters most likely to emit a junk block.
    #
    #      No `fnrefs` guard, though #397's text proposes one: the shipped
    #      serializer derives fnrefs FROM the normalized text, so falsy
    #      plain_text implies fnrefs == [] and the guard is vacuous; and where a
    #      custom extractor records fnrefs independently, the block still has a
    #      truthy source_html and so is still refused downstream -- the guard
    #      would open a hole, not close one.
    seg_block_ids = list(dict.fromkeys(
        bid for s in segments for bid in s["block_ids"]
    ))

    nonembedded_claim_count = {}
    for e in verse_store:
        if e.get("mount") == "embedded":
            continue
        parent = e.get("parent_block")
        if parent:
            nonembedded_claim_count[parent] = nonembedded_claim_count.get(parent, 0) + 1

    empty_seg_blocks = [
        bid for bid in seg_block_ids
        if bid in blocks
        and nonembedded_claim_count.get(bid, 0) != 1
        and not blocks[bid].get("plain_text")
        and (blocks[bid].get("source_html") or "").strip()
    ]
    chk(
        "no_untranslatable_empty_blocks",
        not empty_seg_blocks,
        (
            f"segment blocks with empty plain_text but non-empty source_html "
            f"(validate_draft reports the faithful empty draft block as an "
            f"empty translation, so the segment converges only if the "
            f"translator invents text the source does not have): "
            f"{empty_seg_blocks[:5]} (n={len(empty_seg_blocks)}) -- adapt "
            f"extract.py so a purely structural node is not emitted as a "
            f"content block, then re-extract"
        ) if empty_seg_blocks else f"n_segment_blocks={len(seg_block_ids)}",
    )

    # 14b. Footnote definitions. A definition block is in NO segment's
    #      block_ids; segpack.py discovers it through the segment's footnote
    #      references and emits {"n": n, "source_text": def_block["plain_text"]}
    #      -- plain_text ONLY, with no source_html fallback. validate_draft then
    #      refuses a blank footnote TRANSLATION unconditionally, with no test of
    #      the source at all, so here whitespace-only IS fatal (`.strip()`) and
    #      no source_html conjunct applies. That asymmetry against 14a is the
    #      consumer's, not a slip.
    #
    #      There is deliberately NO reachability filter. build_pack() does not
    #      read anchor_block at all -- it seeds from segment blocks' recorded
    #      fnrefs[] plus FNREF sentinels found in their TEXT, then grows a
    #      frontier over embedded-verse parents and def-blocks. Re-deriving
    #      that traversal here would be a second implementation free to drift
    #      from the first, and this gate cannot import segpack.py (it needs
    #      canon/profile inputs the gate does not have). Two attempts at a
    #      reachability model -- an anchor_block-in-a-segment test, then an
    #      anchor_block-seeded transitive closure -- were both false GREENs;
    #      refusing every empty definition makes the class impossible instead.
    #
    #      The over-catch is knowing and bounded: a definition that no segpack
    #      would carry is refused too. The operator's remedy is the same either
    #      way -- do not emit a definition with no text. Under omit_apparatus
    #      and body_refs_only nothing is refused at all, because the extractor
    #      builds no footnote table and segpack carries no footnote text.
    if apparatus_policy in ("translate_all", "preserve_source"):
        empty_fn_defs = [
            f["n"] for f in footnotes
            if f.get("def_block")
            and f["def_block"] in blocks
            and not (blocks[f["def_block"]].get("plain_text") or "").strip()
        ]
        chk(
            "no_empty_footnote_definitions",
            not empty_fn_defs,
            (
                f"footnotes whose definition block carries no text (validate_draft "
                f"refuses a blank footnote translation unconditionally, so the "
                f"segment converges only if the translator invents a note the "
                f"source does not have): {empty_fn_defs[:5]} (n={len(empty_fn_defs)}) "
                f"-- adapt extract.py so an empty definition is not emitted, then "
                f"re-extract"
            ) if empty_fn_defs else f"n_footnotes={len(footnotes)}",
        )

    return results


# ---------------------------------------------------------------------------
# Advisory scans (#489) -- REPORT-ONLY, never part of the exit decision
# ---------------------------------------------------------------------------
#
# A source EPUB produced by a PDF converter can carry RTL text in VISUAL order
# rather than logical order. Extraction preserves those bytes faithfully -- that
# is correct behaviour, measured byte-identical against the raw EPUB -- and
# every gate downstream passes the result, because token counts, digests, schema
# validation and validate_draft never read what a fragment MEANS. The damage
# lands on the LLM turns: a visual-order run tears words apart, a reviewer reads
# a stranded fragment as a real word and files a finding against a CORRECT
# draft, and a translator can invert who did what to whom. Both have happened on
# a live book, the second reaching a converged draft.
#
# This scan exists to END THE SILENCE, not to render a verdict. It is a
# LEADING-TERMINAL-PUNCTUATION SCREEN: it detects visual-order *handling*, not
# word *reordering*, and its output routes an operator to an adjudication turn
# that decides. Deliberately NOT in run_derivable_checks(): an advisory must
# never refuse an ingestion (the operator may legitimately want to translate a
# mangled book) and must never rescue a failing one.
#
# Two obvious reordering detectors were MEASURED and returned clean on the known
# positive control: whole-block reversal scoring and tail-window reversal
# scoring. A clean sweep from either would have read as "no visual-order input
# found" -- a false all-clear. Do not swap this signature for one of those.

# Punctuation that in LOGICAL order can only FOLLOW a word, never lead one.
# Opening marks (Ps/Pi) are excluded by omission and by category: an opening
# bracket or quote before a word is ordinary logical order. The ELLIPSIS is
# excluded deliberately -- a sentence-initial elision is legitimate, and it
# contributed zero of the positive control book's 921 hits, so excluding it
# costs nothing measured and removes the one false-positive class found.
# Hebrew's word-INTERNAL marks (geresh U+05F3, gershayim U+05F4, maqaf U+05BE)
# are likewise absent on purpose.
_TERMINAL_PUNCT = frozenset(
    ".,;:!?)]}"
    "»"  # RIGHT-POINTING DOUBLE ANGLE QUOTATION MARK
    "”"  # RIGHT DOUBLE QUOTATION MARK
    "’"  # RIGHT SINGLE QUOTATION MARK
    "،"  # ARABIC COMMA
    "؛"  # ARABIC SEMICOLON
    "؟"  # ARABIC QUESTION MARK
    "۔"  # ARABIC FULL STOP
    "׃"  # HEBREW PUNCTUATION SOF PASUQ
)

_ADVISORY_SAMPLE_CAP = 8
VISUAL_ORDER_SCAN_NAME = "visual_order_scan"
BLOCK_SIZE_SCAN_NAME = "block_size_census"
HEADING_LEVEL_SCAN_NAME = "heading_level_outline"

# The label main()'s last-resort boundary prints when reporting itself failed.
# Deliberately NOT one scan's name: that boundary now sits above several scans,
# and naming one of them would misattribute a failure that belongs to none.
ADVISORY_UNAVAILABLE_NAME = "advisory_scans"

# Block-size census (issue #504). A block whose source is an extraction artifact
# -- a whole narrative wrapped as one block by the converter -- is invisible to
# every check this gate runs: the only size-aware one is the per-SEGMENT word
# count, and a 17 896-character block passes it exactly as a 400-character one
# does. The three constants below were MEASURED, not chosen:
#
#   * reference p90 rather than the median. The median is degenerate on real
#     books -- the field manifest's median block is SIX characters (1170 short
#     dialogue paragraphs), and 600 of its 1212 blocks are >= 10x that.
#   * reference p90 rather than p95/p99. A high percentile sits ON the artifact
#     once more than one block is affected: 98 ordinary 400-char paragraphs plus
#     TWO 18 000-char wrap-joined blocks give p99 = 18 000, so nothing can ever
#     reach 4x it. p90 flags both, and still flags all ten when a tenth of the
#     book is affected.
#   * multiple 10. Over the five manifests measured (max/p90): 21.03 on the book
#     carrying the known artifact, and 4.97 / 4.86 / 5.78 / 6.04 on four clean
#     ones. 10 clears the noisiest clean book by 1.66x and the true positive
#     clears 10 by 2.10x -- the widest two-sided margin of p90/p95/p99.
#   * minimum population 30. Below it the reference is the outlier's own
#     neighbourhood and the comparison means nothing. The census NOTE prints the
#     count anyway, so a population too small to screen is stated rather than
#     silently skipped.
_BLOCK_SIZE_REFERENCE_Q = 0.90
_BLOCK_SIZE_OUTLIER_MULTIPLE = 10
_BLOCK_SIZE_MIN_POPULATION = 30
# Derived, never spelled a second time: every line the census and the advisory
# print names this percentile, and a hand-written "p90" left beside a changed
# _BLOCK_SIZE_REFERENCE_Q is a silent lie inside the operator's own evidence.
_BLOCK_SIZE_REFERENCE_PCT = int(_BLOCK_SIZE_REFERENCE_Q * 100)


def _is_rtl_letter(ch: str) -> bool:
    """A letter Unicode itself classifies as right-to-left.

    Asked of Unicode (bidi class R or AL) rather than of a hand-kept table of
    codepoint ranges. A range table silently stops at whatever the author
    remembered: an earlier revision of this file listed eleven BMP blocks and
    therefore could not see ``.<ADLAM CAPITAL ALIF>``, Hanifi Rohingya, the
    Syriac supplement, or the Arabic mathematical alphabets -- a source with the
    exact signature below would have finished on an unqualified green. Measured
    when this replaced the table: 1415 codepoints newly admitted, ZERO newly
    rejected, and both the positive control book (921 hits over 476 of 1212
    units) and the 46 762-token logical-order negative corpus were unchanged."""
    return (
        unicodedata.category(ch).startswith("L")
        and unicodedata.bidirectional(ch) in ("R", "AL")
    )


def _is_terminal_punct(ch: str) -> bool:
    # Pe (close bracket) and Pf (final quote) are whole categories that can only
    # trail a word; the rest are named individually above.
    return ch in _TERMINAL_PUNCT or unicodedata.category(ch) in ("Pe", "Pf")


def _leading_terminal_punct_hits(text):
    """Tokens whose FIRST character is terminal punctuation that is followed --
    across any combining marks or bidi/format controls -- by an RTL letter.

    In logical order this cannot occur: a full stop or comma is stored AFTER the
    word it ends. In visual order it is what a converter emits at the left edge
    of a run.

    Skipping ``M*`` matters because niqqud can sit between the punctuation and
    the letter, which defeats a naive lookahead -- that mistake turned a real
    777-occurrence count into 677. Skipping ``Cf`` matters because RLM/LRM
    (U+200F/U+200E) are exactly what a bidi-aware converter emits there."""
    hits = []
    for token in (text or "").split():
        if not _is_terminal_punct(token[0]):
            continue
        # Start at 1: token[0] is already known to be terminal punctuation.
        i = 1
        while i < len(token):
            category = unicodedata.category(token[i])
            if not (
                _is_terminal_punct(token[i])
                or category.startswith("M")
                or category == "Cf"
            ):
                break
            i += 1
        if i < len(token) and _is_rtl_letter(token[i]):
            hits.append(token)
    return hits


def _advisory_text_units(manifest: dict):
    """Every INDEPENDENT source-text store the extractor emits, as
    ``(label, text)``.

    Blocks are not all of it. An EMBEDDED verse's text is lifted OUT of its
    carrier block and replaced by a placeholder sentinel (see
    extract.py.template's ``container.replace_with(...)`` and segpack.py's own
    note), so a blocks-only scan cannot see it at all. A STANDALONE verse
    (``mount == "block"``) is already an ordinary blocks[] entry and must NOT be
    scanned twice. ``segments[].title_text`` needs no scan of its own: the
    extractor derives it from the heading block this already reads."""
    blocks = manifest.get("blocks")
    if isinstance(blocks, dict):
        for block_id, block in blocks.items():
            if isinstance(block, dict):
                yield str(block_id), block.get("plain_text")
    verse = manifest.get("verse")
    store = verse.get("store") if isinstance(verse, dict) else None
    if isinstance(store, list):
        for entry in store:
            if not isinstance(entry, dict) or entry.get("mount") != "embedded":
                continue
            yield f"verse:{entry.get('vid')}", entry.get("plain_text")


def scan_visual_order(manifest: dict):
    """Returns ``(n_hits, n_units_with_hits, n_rtl_units, histogram, samples)``.

    Pure: reads the manifest, decides nothing, writes nothing."""
    n_hits = 0
    n_units_with_hits = 0
    rtl_units = 0
    histogram = {}
    samples = []
    for label, text in _advisory_text_units(manifest):
        if not isinstance(text, str) or not text:
            continue
        if not any(_is_rtl_letter(ch) for ch in text):
            continue
        rtl_units += 1
        hits = _leading_terminal_punct_hits(text)
        if not hits:
            continue
        n_units_with_hits += 1
        n_hits += len(hits)
        for token in hits:
            key = f"U+{ord(token[0]):04X}"
            histogram[key] = histogram.get(key, 0) + 1
        if len(samples) < _ADVISORY_SAMPLE_CAP:
            samples.append((label, hits[0]))
    return n_hits, n_units_with_hits, rtl_units, histogram, samples


def _escape_char(ch: str) -> str:
    """One character as printable ASCII. Above the BMP a four-digit ``\\uXXXX``
    form is not merely unconventional, it is AMBIGUOUS -- ``\\u1F600`` reads as
    U+1F60 followed by ``0`` -- so an astral codepoint takes the eight-digit
    ``\\UXXXXXXXX`` form. Evidence whose own spelling is ambiguous cannot settle
    the question it was printed to settle."""
    if ch.isascii() and ch.isprintable() and ch != "\\":
        return ch
    return f"\\u{ord(ch):04X}" if ord(ch) <= 0xFFFF else f"\\U{ord(ch):08X}"


def _escape_evidence(token: str) -> str:
    """Renders a token as pure-ASCII ``\\uXXXX`` escapes.

    NEVER print a raw RTL token as evidence. A bidi-reordering terminal renders
    a CORRUPTED token identically to an intact one -- a finding was once filed
    and then retracted for exactly that reason -- so evidence a human or an LLM
    is meant to ADJUDICATE has to be codepoints, not glyphs. The histogram keys
    above are ``U+XXXX`` for the same reason."""
    return "".join(_escape_char(ch) for ch in token)


def _floor_index_percentile(values, q: float):
    """The value at ``sorted(values)[int(q * (n - 1))]``.

    Named for the formula rather than called "nearest-rank", because it is NOT
    that definition: nearest-rank is ``ceil(q * n) - 1``, and the two disagree.
    They happen to agree at many n (both give 26 at n=30 and 89 at n=100), which
    is exactly what makes a wrong label survive review -- n=12 separates them,
    floor-index 9 against nearest-rank 10. Callers must not read one definition's
    boundary behaviour off the other's name.

    An all-equal population returns that value, so no member can reach a multiple
    of it above 1. ``values`` must be non-empty; the caller guarantees it."""
    ordered = sorted(values)
    return ordered[int(q * (len(ordered) - 1))]


def _segment_member_block_sizes(manifest: dict):
    """``[(block_id, character_count)]`` for the DISTINCT blocks that some
    segment claims, skipping any whose ``plain_text`` is missing or empty.

    Membership in ``segments[].block_ids`` -- not a ``kind`` filter -- is what
    bounds the population. ``kind`` is the wrong key twice over: filtering to
    ``body`` would drop the frontback units a project legitimately translates,
    and a real manifest on disk carries values outside the schema enum
    (``novella``, ``heading``), so the filter can yield an EMPTY population,
    which prints exactly what a populated one prints. Membership also excludes
    the ``FN:`` definition blocks and the unattached front/back matter that
    carries a Project Gutenberg licence -- ~18 800 characters in one block, in
    every Gutenberg book, and not a paragraph anyone translates.

    DISTINCT matters: ``block_ids`` is an ordinary array, the schema does not
    require its members to be unique, and no derivable check rejects a repeat
    (measured: a baseline manifest with 29 repeated ids fails none of them). A
    repeated id counted once per occurrence would move both ``n`` and the
    reference percentile, in either direction, without any block existing to
    justify it.

    Every access is ``.get()``: an advisory may not raise on a shape a mandatory
    check owns, and it may not assume keys the schema does not require. That is
    also why this is NOT folded into ``no_untranslatable_empty_blocks``'s own
    de-duplicated ``seg_block_ids`` (#397) despite the resemblance: that one
    indexes ``s["block_ids"]`` directly and is allowed to, because a raise there
    lands on run_derivable_checks()'s structural boundary and exits 1. A raise
    HERE would have to be swallowed, and an advisory that silently reports
    nothing is the failure mode this whole feature exists to remove."""
    blocks = manifest.get("blocks")
    if not isinstance(blocks, dict):
        return []
    seen = set()
    sizes = []
    for segment in manifest.get("segments") or []:
        if not isinstance(segment, dict):
            continue
        for block_id in segment.get("block_ids") or []:
            if block_id in seen:
                continue
            seen.add(block_id)
            block = blocks.get(block_id)
            if not isinstance(block, dict):
                continue
            text = block.get("plain_text")
            if not isinstance(text, str) or not text:
                continue
            sizes.append((str(block_id), len(text)))
    return sizes


def _block_size_stats(sizes):
    """``(n, p50, reference, threshold, hits)`` from an already-derived
    population, so a caller that also needs the population itself -- the census
    formatter does, for its largest-blocks sample -- derives it once instead of
    walking every segment twice WITHIN that call. The advisory and the census
    are separate callers and do each walk it; that is one extra O(n log n) pass
    over data ``json.loads`` has already materialised, and it buys both of them
    a pure function with no shared state."""
    n = len(sizes)
    if not n:
        return 0, 0, 0, 0, []
    lengths = [size for _, size in sizes]
    p50 = _floor_index_percentile(lengths, 0.50)
    reference = _floor_index_percentile(lengths, _BLOCK_SIZE_REFERENCE_Q)
    threshold = reference * _BLOCK_SIZE_OUTLIER_MULTIPLE
    if n < _BLOCK_SIZE_MIN_POPULATION:
        return n, p50, reference, threshold, []
    hits = sorted(
        (
            (block_id, size, size / reference if reference else 0.0)
            for block_id, size in sizes
            if size >= threshold
        ),
        key=lambda hit: hit[1],
        reverse=True,
    )
    return n, p50, reference, threshold, hits


def scan_block_size_outliers(manifest: dict):
    """Returns ``(n, p50, reference, threshold, hits)``.

    ``hits`` is ``[(block_id, size, ratio)]``, largest first, and is empty when
    the population is under ``_BLOCK_SIZE_MIN_POPULATION`` -- the reference is
    then the outlier's own neighbourhood.

    Pure: reads the manifest, decides nothing, writes nothing."""
    return _block_size_stats(_segment_member_block_sizes(manifest))


def format_block_size_census(manifest: dict) -> str:
    """The census line, built for EVERY schema-valid run -- a fired advisory and
    a clean one alike.

    It is a NOTE, not an advisory: it never enters ``advisories`` and so never
    moves the ``(N ADVISORY)`` status suffix, which must keep meaning "something
    wants an operator's eyes". What it does is make the scan's SILENCE
    auditable -- a population of 12 blocks and a population of 1200 with no
    outlier print differently, where a hits-only design prints nothing for
    either. Issue #504 asks for the distribution rather than a verdict; this is
    where the distribution lives."""
    sizes = _segment_member_block_sizes(manifest)
    n, p50, reference, threshold, hits = _block_size_stats(sizes)
    if not n:
        return (
            "block_size_census: no segment-member block carries text -- no "
            "size distribution to report."
        )
    largest = sorted(sizes, key=lambda entry: entry[1], reverse=True)
    sample = "; ".join(
        f"{_escape_block_id(block_id)}={size}"
        for block_id, size in largest[:_ADVISORY_SAMPLE_CAP]
    )
    if n < _BLOCK_SIZE_MIN_POPULATION:
        return (
            f"block_size_census: n={n} blocks (median {p50}, largest "
            f"{largest[0][1]}). NOT screened for wrap/extraction artifacts: "
            f"under {_BLOCK_SIZE_MIN_POPULATION} blocks the "
            f"p{_BLOCK_SIZE_REFERENCE_PCT} reference sits inside the outlier's "
            f"own neighbourhood, so the comparison would mean nothing. "
            f"largest: {sample}"
        )
    return (
        f"block_size_census: n={n} blocks, median {p50}, "
        f"p{_BLOCK_SIZE_REFERENCE_PCT} {reference}, largest "
        f"{largest[0][1]} characters; artifact threshold "
        f"{_BLOCK_SIZE_OUTLIER_MULTIPLE}x p{_BLOCK_SIZE_REFERENCE_PCT} = "
        f"{threshold} ({len(hits)} block(s) at or above it). largest: {sample}"
    )


def _scan_unavailable_detail(exc: Exception) -> str:
    """For ONE scan failing inside its own boundary -- its siblings did run."""
    return (
        f"scan unavailable ({type(exc).__name__}) -- this is an advisory only "
        f"and does NOT affect whether this gate passes; the mandatory checks "
        f"are unaffected, and every other advisory still ran."
    )


def _advisories_unavailable_detail(exc: Exception) -> str:
    """For the ORCHESTRATION failing, above every per-scan boundary.

    It must not borrow the sentence above: nothing here knows how many scans
    got to run, and claiming they all did would be a false statement printed
    at exactly the moment the operator is least able to check it."""
    return (
        # The "scan unavailable" opener is the shipped #489 wording and is
        # asserted by that suite; only what FOLLOWS it may differ here.
        f"scan unavailable ({type(exc).__name__}) -- the advisory scans could "
        f"not be run at all. This is an advisory only and does NOT affect "
        f"whether this gate passes; the mandatory checks are unaffected. One or "
        f"more scans may not have run, so treat this run as carrying NO "
        f"advisory information."
    )


def _visual_order_advisory(manifest: dict):
    """The #489 advisory, as ``[(name, detail)]`` -- empty when nothing fired."""
    results = []
    n_hits, n_units_with_hits, n_rtl_units, histogram, samples = scan_visual_order(manifest)
    if n_hits:
        marks = ", ".join(
            f"{key}x{count}" for key, count in sorted(histogram.items())
        )
        # The LABEL is escaped too, not just the token. A block id is authored by
        # the extractor -- a custom one may emit anything -- and a raw RTL id
        # reorders the very diagnostic being adjudicated. This is also what makes
        # the payload ASCII by CONSTRUCTION rather than by fixture.
        evidence = "; ".join(
            f"{_escape_evidence(label)} {_escape_evidence(token)}"
            for label, token in samples
        )
        detail = (
            f"{n_hits} token(s) in {n_units_with_hits} of {n_rtl_units} RTL-bearing "
            f"text unit(s) begin with terminal punctuation immediately followed by an "
            f"RTL letter. In logical order that cannot happen, so this source is "
            f"probably in VISUAL order (a PDF-to-EPUB conversion artifact). "
            f"leading marks: {marks}. sample (escaped -- never judge RTL text by "
            f"looking at it): {evidence}. This is a SCREEN, not a verdict: it "
            f"detects visual-order handling, not word reordering, and it neither "
            f"passes nor fails this gate. Adjudicate the sampled units against "
            f"the source before translating, and if positive, record the "
            f"condition in style_bible.md's E-traps section (see SKILL.md, "
            f"'Visual-order source')."
        )
        results.append((VISUAL_ORDER_SCAN_NAME, detail))
    return results


def _block_size_advisory(manifest: dict):
    """The #504 advisory, as ``[(name, detail)]`` -- empty when nothing fired.

    The census itself is emitted unconditionally as a NOTE (see
    ``format_block_size_census``); this fires only when a block crosses the
    threshold, so a fired advisory keeps meaning what it has meant since #489."""
    n, p50, reference, threshold, hits = scan_block_size_outliers(manifest)
    if not hits:
        return []
    named = "; ".join(
        f"{_escape_block_id(block_id)} {size} chars "
        f"({ratio:.1f}x p{_BLOCK_SIZE_REFERENCE_PCT})"
        for block_id, size, ratio in hits[:_ADVISORY_SAMPLE_CAP]
    )
    more = (
        f" (+{len(hits) - _ADVISORY_SAMPLE_CAP} more not listed)"
        if len(hits) > _ADVISORY_SAMPLE_CAP else ""
    )
    return [(
        BLOCK_SIZE_SCAN_NAME,
        f"{len(hits)} of {n} segment-member block(s) are at or above "
        f"{_BLOCK_SIZE_OUTLIER_MULTIPLE}x this book's own "
        f"p{_BLOCK_SIZE_REFERENCE_PCT} block size "
        f"({reference} characters, median {p50}), i.e. >= {threshold} "
        f"characters: {named}{more}. A block that large is usually a "
        f"WRAP/EXTRACTION ARTIFACT -- a converter joining a whole narrative "
        f"into one block -- rather than a paragraph of the printed book, and "
        f"the rest of this pipeline treats block structure as the source's "
        f"own. This is a SCREEN, not a verdict: a genuinely long paragraph "
        f"looks identical here. ADJUDICATE the named block(s) against the "
        f"printed source before translating, and see SKILL.md, "
        f"'Oversized source block'. Nothing here changes whether this gate "
        f"passes, and nothing is re-paragraphed for you: re-cutting a block "
        f"changes the segpack's key set, which validate_draft.py locks 1:1 "
        f"against the draft."
    )]


# An id is truncated to this many SOURCE characters before escaping.
_EVIDENCE_ID_CHARS = 80


def _escape_block_id(block_id: str) -> str:
    """A manifest-authored block id, printable AND visibly bounded.

    Two things beyond ``_escape_evidence``. TRUNCATED, because escaping expands
    one character into six ASCII ones (ten above the BMP) while the manifest
    schema puts neither a pattern nor a maxLength on a block id: a 100 000-
    character id becomes a 600 000-character line, and this line is printed
    BEFORE the gate's own PASS/FAIL lines, so it can push the verdict out of a
    reader's window. QUOTED, because escaping neutralises control characters and
    every non-ASCII codepoint but passes ASCII prose through verbatim -- an id
    can therefore read as a sentence of the diagnostic it sits inside, and the
    documented consumer of these lines is a later LLM turn as well as a human.
    Quoting keeps injected prose visibly inside a field.

    A delimiter only contains what cannot reproduce it, so the quote itself is
    escaped HERE rather than in ``_escape_evidence``: an id holding ``"`` would
    otherwise close the field it was meant to sit inside and the rest of it
    would read as diagnostic prose. ``\\`` is already escaped one level down,
    so a literal ``\\u0022`` in an id renders as ``\\u005Cu0022`` and cannot
    forge the escape. The visual-order samples do not go through here because
    they are not delimited, so #489's shipped wording is untouched.

    What this deliberately does NOT do is restrict what an id may CONTAIN.
    Narrowing the schema would reject manifests this gate accepts today, from
    custom extractors nobody here has seen; that is a separate decision from
    printing safely."""
    clipped = block_id[:_EVIDENCE_ID_CHARS]
    marker = "..." if len(block_id) > _EVIDENCE_ID_CHARS else ""
    body = "".join(
        "\\u0022" if ch == '"' else _escape_char(ch) for ch in clipped
    )
    return f'"{body}{marker}"'


# Heading-level outline disclosure (issue #233). ``heading_levels`` is optional
# and per-tier: a book can cite three heading-shaped block types and declare a
# level for none, one, or all of them, and nothing in a schema-valid manifest
# distinguishes "the operator meant level 2 for this tier" from "the operator
# forgot to declare this tier" -- assemble.py:1949 resolves an undeclared tier
# to level 2 exactly the same way it resolves a tier the operator deliberately
# pinned there. That ambiguity is why this can only ever be a disclosure, never
# a HARD check: there is no way to tell the two apart from the manifest alone,
# and refusing every undeclared tier would refuse legal books that use the
# default on purpose.
#
# The population is the CITED tiers, not the declared vocabulary: every block
# type in H (``manifest.heading_types | {"HEAD"}``) that some segment's
# ``block_ids`` actually names, not H itself. ``assemble.py:1849-1955`` builds
# an outline node only for a block a segment cites -- a type an operator lists
# in ``heading_types`` but this book never actually uses would inflate the
# outline with a tier that never reaches the assembled book or the published
# vault.
#
# Two tier names sharing an 80-character prefix render identically in the NOTE
# and WARN below (``_escape_block_id`` truncates there, same as everywhere else
# in this file) -- accepted: the NOTE is a disclosure, not an identity key, and
# nothing downstream keys off this string.


def _cited_heading_tiers(manifest: dict):
    """The set U of heading-shaped block TYPES actually cited by some segment.

    H = ``set(manifest.get("heading_types") or ()) | {"HEAD"}`` is the
    vocabulary; membership in some segment's ``block_ids`` -- not H alone -- is
    what bounds U (see the module-level comment above this function for why).
    A block id a segment names that resolves to no ``blocks{}`` entry is
    skipped here too: ``block_graph_integrity`` (``run_derivable_checks``) and
    ``assemble.py`` both own that error, and this advisory must not raise on a
    shape a mandatory check already rejects -- every access below is
    ``.get()``, mirroring ``_segment_member_block_sizes``'s own reasoning."""
    blocks = manifest.get("blocks")
    segments = manifest.get("segments")
    if not isinstance(blocks, dict) or not isinstance(segments, list):
        return set()
    declared_vocabulary = set(manifest.get("heading_types") or ()) | {"HEAD"}
    cited = set()
    for segment in segments:
        if not isinstance(segment, dict):
            continue
        for block_id in segment.get("block_ids") or []:
            block = blocks.get(block_id)
            if not isinstance(block, dict):
                continue
            block_type = block.get("type")
            if block_type in declared_vocabulary:
                cited.add(block_type)
    return cited


def _resolved_heading_level(block_type: str, heading_levels: dict):
    """``(level, is_declared)`` for one tier, mirroring
    ``assemble.py:1949``'s ``heading_levels.get(raw_type, 2)`` exactly.

    Precondition: the manifest is schema-valid (``main()`` exits 1 on schema
    errors before any advisory runs), so a present value is an ``int`` 1..6.
    The type/range check below is therefore DEFENSIVE ONLY and unreachable
    through ``main()`` -- it must not be described as load-bearing, and it
    exists so a caller importing this module directly (bypassing ``main()``'s
    schema gate) still gets ``default`` instead of a malformed level rendered
    as declared. ``bool`` is excluded explicitly because it is a subclass of
    ``int`` in Python (``isinstance(True, int)`` is ``True``) and would
    otherwise silently pass the range check."""
    if block_type in heading_levels:
        value = heading_levels[block_type]
        if isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 6:
            return value, True
    return 2, False


def heading_level_tiers(manifest: dict) -> list:
    """``(type, resolved_level, is_declared)`` for every tier in U, sorted by
    ``(resolved_level, type)``."""
    heading_levels = manifest.get("heading_levels")
    if not isinstance(heading_levels, dict):
        heading_levels = {}
    tiers = [
        (block_type,) + _resolved_heading_level(block_type, heading_levels)
        for block_type in _cited_heading_tiers(manifest)
    ]
    return sorted(tiers, key=lambda tier: (tier[1], tier[0]))


def format_heading_level_outline(manifest: dict):
    """The NOTE payload, or ``None`` when U is empty -- a book that cites NO
    block whose type is in H (``manifest.heading_types | {"HEAD"}``) at all,
    i.e. no heading block of any kind. A book citing only a "HEAD" block still
    has U = {"HEAD"}, which is NOT empty: the NOTE IS printed for it, one
    tier, level 2, ``default`` -- only the WARN stays silent there (a single
    tier has no nesting order to get wrong; see ``_heading_level_advisory``).

    Building this may fail exactly as ``format_block_size_census``'s docstring
    describes for the census: ``main()`` treats a raise here as a finding, not
    a crash (see the call site)."""
    tiers = heading_level_tiers(manifest)
    if not tiers:
        return None
    rendered = ", ".join(
        f"{_escape_block_id(block_type)}={level} "
        f"({'declared' if is_declared else 'default'})"
        for block_type, level, is_declared in tiers
    )
    return f"heading_level_outline: {len(tiers)} heading tier(s) cited: {rendered}"


def _heading_level_advisory(manifest: dict):
    """The #233 advisory, as ``[(name, detail)]`` -- empty when nothing fired.

    Fires iff U has at least two tiers AND at least one of them defaulted: a
    single-tier book has no nesting order to get wrong, and a book where every
    tier was explicitly declared -- including two tiers the operator
    deliberately pinned to the SAME level -- is a decision, not a gap."""
    tiers = heading_level_tiers(manifest)
    if len(tiers) < 2:
        return []
    defaulted = [tier for tier in tiers if not tier[2]]
    if not defaulted:
        return []
    declared = [tier for tier in tiers if tier[2]]
    n, d = len(tiers), len(defaulted)
    defaulted_names = ", ".join(_escape_block_id(t[0]) for t in defaulted)
    declared_sentence = (
        f" Declared: {', '.join(f'{_escape_block_id(t[0])}={t[1]}' for t in declared)}."
        if declared else ""
    )
    has_verb = "has" if d == 1 else "have"
    render_clause = "it renders" if d == 1 else "they render"
    detail = (
        f"this book cites {n} heading tier(s) and {d} of them {has_verb} no "
        f"level in manifest.heading_levels, so {render_clause} at level 2 by "
        f"default: {defaulted_names}.{declared_sentence} A markdown outline "
        f"whose tiers collapse into one level, or nest in the wrong order, "
        f"reaches the assembled book and the published vault with every gate "
        f"green -- nothing downstream re-derives a heading's level, and "
        f"validate_assembled.py proves heading KIND completeness only. This "
        f"is a SCREEN, not a verdict: an outline you meant to be flat looks "
        f"identical here. Declare a level for every tier this book renders (a "
        f"level of 2 states the default in writing), or leave it and carry "
        f"on. Nothing here changes whether this gate passes."
    )
    return [(HEADING_LEVEL_SCAN_NAME, detail)]


def run_advisory_scans(manifest: dict):
    """Report-only scans, as ``[(name, detail)]``. Never affects the exit code.

    Each scan is isolated: one raising scan degrades to its own named
    ``scan unavailable`` entry and the others still report. Without that, a
    single boundary around all of them would let one failure silently swallow
    every sibling finding -- and a swallowed advisory looks exactly like a clean
    book. ``main()``'s own boundary stays above this as the last-resort net for
    a failing ``print``. Only ``Exception`` is caught, deliberately:
    KeyboardInterrupt and SystemExit are not advisory failures."""
    results = []
    for name, scan in (
        (VISUAL_ORDER_SCAN_NAME, _visual_order_advisory),
        (BLOCK_SIZE_SCAN_NAME, _block_size_advisory),
        (HEADING_LEVEL_SCAN_NAME, _heading_level_advisory),
    ):
        try:
            results.extend(scan(manifest))
        except Exception as exc:  # noqa: BLE001 -- an advisory may never gate a run
            results.append((name, _scan_unavailable_detail(exc)))
    return results


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

REPORT_ONLY_RESIDUAL = (
    "body_coverage_no_holes",
    "no_orphan_footnote_continuation",
    "verse_no_uncovered",
)


def _load_manifest(manifest_path: Path) -> dict:
    try:
        text = manifest_path.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"ERROR: could not read manifest {manifest_path}: {exc}", file=sys.stderr)
        sys.exit(2)
    try:
        manifest = json.loads(text)
    except json.JSONDecodeError as exc:
        print(f"ERROR: {manifest_path} is not valid JSON: {exc}", file=sys.stderr)
        sys.exit(2)
    if not isinstance(manifest, dict):
        print(
            f"ERROR: {manifest_path} did not parse to a JSON object "
            f"(got {type(manifest).__name__}).",
            file=sys.stderr,
        )
        sys.exit(2)
    return manifest


def build_arg_parser():
    parser = argparse.ArgumentParser(
        description=(
            "Managed post-extraction gate (issue #86): independently re-derive "
            "the manifest's self-check invariants and pin the extractor's "
            "self-check region. Always invoked from the plugin's own install "
            "path -- never a durable-root copy."
        )
    )
    parser.add_argument("--manifest", required=True, help="Path to ${durable_root}/manifest.json.")
    parser.add_argument("--extract", required=True, help="Path to the durable ${durable_root}/extract.py.")
    parser.add_argument("--profile", required=True, help="Path to the project's profile.yml.")
    return parser


def main(argv=None):
    args = build_arg_parser().parse_args(argv)
    manifest_path = Path(args.manifest)
    extract_path = Path(args.extract)
    profile_path = Path(args.profile)

    manifest = _load_manifest(manifest_path)

    _dependency_preflight()
    max_segment_words, apparatus_policy, source_format = load_profile_values(profile_path)

    # --- (a) independent schema validation ------------------------------------
    # A structurally-invalid manifest (missing a required top-level key, a stray
    # one, or a wrong-typed value) is a failed extraction; reject it before the
    # semantic re-derivation can be trusted -- mirrors profile_validate.py's
    # schema-first discipline.
    schema_errors = validate_manifest_schema(manifest)
    if schema_errors:
        print(
            f"FAIL manifest_schema: {manifest_path} does not conform to the "
            f"plugin's manifest.schema.json -- a structurally-invalid manifest "
            f"cannot be certified (this is itself a failed extraction):",
            file=sys.stderr,
        )
        for err in schema_errors:
            print(f"  - {err}", file=sys.stderr)
        sys.exit(1)

    # --- (a2) report-only advisory scans (#489, #504, #233) -------------------
    # Runs BEFORE re-derivation on purpose. run_derivable_checks() has its own
    # structural-exception boundary that exits 1 immediately, and a SCHEMA-VALID
    # manifest can legitimately reach it (the count fields it indexes are not
    # schema-required) -- so an advisory placed after that boundary would be
    # silently skipped on exactly the malformed inputs most likely to be
    # mangled.
    #
    # Every step below sits inside its OWN boundary: scan, build each NOTE
    # payload, print the advisories, print each NOTE. Deliberately NOT counted
    # here -- the count grew once already (#233 added a second NOTE) and a
    # stale number inside the reasoning is worse than no number.
    # EMITTING is inside a boundary at all
    # because it was once outside one, which quietly broke the promise that an
    # advisory can never change the exit decision in EITHER direction: stderr on
    # a closed pipe or a full filesystem raises OSError, and an ADVISORY would
    # then have refused an otherwise-valid ingestion -- the exact thing this
    # feature is documented as unable to do. The steps are SEPARATE because one
    # shared boundary let a failure in the WARN loop skip the census entirely,
    # reproduced at exit 0 with `(1 ADVISORY)` and no NOTE on a run whose stdout
    # was healthy; a census that goes missing exactly when reporting is degraded
    # is the silence this feature exists to end. Building BEFORE emitting is
    # what keeps the two census failures distinguishable: one that could not be
    # COMPUTED is a finding and becomes an advisory, while one that could not be
    # PRINTED is a reporting failure and stays silent rather than inventing one.
    # None of them may reach the exit decision: a scan that raises degrades
    # to a named advisory and the mandatory checks proceed untouched, because
    # reporting is best-effort by design and the gate's verdict is not. Note
    # only Exception is caught, deliberately: KeyboardInterrupt and SystemExit
    # are not advisory failures and must keep propagating.
    advisories = []
    try:
        advisories = run_advisory_scans(manifest)
    except Exception as exc:  # noqa: BLE001 -- an advisory may never gate a run
        advisories = [(ADVISORY_UNAVAILABLE_NAME, _advisories_unavailable_detail(exc))]
    census = None
    heading_level_outline = None
    # Evaluated BEFORE the try, not inside its handler: the handler is the one
    # place a raise would escape every boundary in this block and crash a gate
    # whose whole premise is that reporting cannot reach the exit decision.
    block_size_already_reported = any(
        name == BLOCK_SIZE_SCAN_NAME for name, _ in advisories
    )
    heading_level_already_reported = any(
        name == HEADING_LEVEL_SCAN_NAME for name, _ in advisories
    )
    try:
        census = format_block_size_census(manifest)
    except Exception as exc:  # noqa: BLE001 -- the census may never gate a run
        # Not appended blindly: run_advisory_scans() shares this scan's helpers,
        # so ONE broken helper would otherwise be reported twice and counted
        # twice in the status suffix, reading as two independent problems.
        if not block_size_already_reported:
            advisories.append((BLOCK_SIZE_SCAN_NAME, _scan_unavailable_detail(exc)))
    try:
        heading_level_outline = format_heading_level_outline(manifest)
    except Exception as exc:  # noqa: BLE001 -- the outline may never gate a run
        # Same de-duplication as the census above: _heading_level_advisory
        # shares heading_level_tiers() with this formatter, so one broken
        # helper must not be reported as two independent problems.
        if not heading_level_already_reported:
            advisories.append((HEADING_LEVEL_SCAN_NAME, _scan_unavailable_detail(exc)))
    try:
        for name, detail in advisories:
            print(f"WARN {name}: {detail}", file=sys.stderr)
    except Exception:  # noqa: BLE001 -- reporting the failure may also fail
        pass
    # A NOTE, not an advisory -- format_block_size_census() carries why it is
    # unconditional. Printing it HERE is what makes "every schema-valid run"
    # true: after the schema gate above, and before every check below that can
    # exit non-zero.
    if census is not None:
        try:
            print(f"NOTE {census}")
        except Exception:  # noqa: BLE001 -- reporting may fail; it may not gate
            pass
    # A NOTE too, and printed immediately after the census for the same
    # reason -- see format_heading_level_outline() and issue #233.
    if heading_level_outline is not None:
        try:
            print(f"NOTE {heading_level_outline}")
        except Exception:  # noqa: BLE001 -- reporting may fail; it may not gate
            pass

    # --- (b) independent re-derivation of the derivable checks ----------------
    try:
        check_results = run_derivable_checks(manifest, apparatus_policy, max_segment_words)
    except (KeyError, TypeError, AttributeError) as exc:
        print(
            f"FAIL manifest_wellformed: {manifest_path} is missing a structural "
            f"field or has a value of the wrong type, so its invariants cannot be "
            f"re-derived ({type(exc).__name__}: {exc}). This is itself a failed "
            f"extraction -- the manifest is not the shape a valid extraction "
            f"produces.",
            file=sys.stderr,
        )
        sys.exit(1)

    derivable_ok = True
    for name, ok, detail in check_results:
        if ok:
            print(f"PASS {name}  ({detail})" if detail else f"PASS {name}")
        else:
            derivable_ok = False
            print(f"FAIL {name}: {detail}", file=sys.stderr)

    # --- (c) pin the extractor's self-check region -----------------------------
    # SKIPPED for source.format: custom -- Step 0a copies extract.py.template to
    # ${durable_root}/extract.py unconditionally, but for a custom source that
    # copy is never adapted or run: the real extractor that produced this
    # manifest.json lives at scripts/custom_extractors/<value>. Pinning the
    # unadapted template copy would only ever vacuously pass (it matches
    # CURRENT_EXTRACTOR_SELFCHECK_HASH trivially), certifying nothing about the
    # actual custom extractor -- see references/source-format-adapters/custom.md
    # and references/false-green-gate.md.
    region_ok = True
    if source_format == "custom":
        print(
            "NOTE selfcheck_region_pin: SKIPPED for source.format: custom -- "
            f"{extract_path} is Step 0a's unadapted extract.py.template copy, "
            "not the co-designed custom extractor "
            "(scripts/custom_extractors/<value>) that actually produced this "
            "manifest.json. The custom extractor's own equivalent of the "
            "residual self-checks is the project's own responsibility."
        )
        print(
            "NOTE report-only residual: NOT covered here for source.format: "
            "custom (the region pin above is skipped) -- the custom "
            "extractor's own equivalent of these checks is the project's own "
            f"responsibility: {', '.join(REPORT_ONLY_RESIDUAL)}"
        )
    else:
        try:
            extract_text = extract_path.read_text(encoding="utf-8")
        except OSError as exc:
            print(f"ERROR: could not read extractor {extract_path}: {exc}", file=sys.stderr)
            sys.exit(2)

        actual_hash = selfcheck_region_hash(extract_text)
        if actual_hash is None:
            region_ok = False
            print(
                f"FAIL selfcheck_region_pin: could not locate exactly one "
                f"BEGIN/END SELF-CHECK REGION sentinel pair in {extract_path} -- the "
                f"self-check region is missing or tampered. This gate cannot certify "
                f"a build whose self-check suite has been removed or altered (a "
                f"false-green anti-pattern). Restore the shipped self-check region "
                f"verbatim; if a check is genuinely wrong for your source, take the "
                f"gap to a plugin issue rather than editing the region.",
                file=sys.stderr,
            )
        elif actual_hash != CURRENT_EXTRACTOR_SELFCHECK_HASH:
            region_ok = False
            pending = CURRENT_EXTRACTOR_SELFCHECK_HASH == "PENDING_LEAD_FILL"
            hint = (
                " (this plugin build ships an un-provisioned "
                "CURRENT_EXTRACTOR_SELFCHECK_HASH placeholder -- report it as a "
                "plugin packaging bug)"
                if pending else ""
            )
            print(
                f"FAIL selfcheck_region_pin: the extractor's SELF-CHECK REGION hash "
                f"{actual_hash} does not match this plugin build's pinned "
                f"CURRENT_EXTRACTOR_SELFCHECK_HASH {CURRENT_EXTRACTOR_SELFCHECK_HASH}"
                f"{hint}. Either a self-check was edited (editing a check to reach "
                f"green is a false-green anti-pattern -- take genuine gaps to a "
                f"plugin issue) or this durable extract.py drifted from the shipped "
                f"template. Re-derive from the shipped template; do NOT weaken a "
                f"check to pass this gate.",
                file=sys.stderr,
            )
        else:
            print(f"PASS selfcheck_region_pin  ({actual_hash})")

        print(
            "NOTE report-only residual (NOT re-derived here -- inputs live only in "
            "the extractor's in-memory build report; covered by the region pin "
            f"above): {', '.join(REPORT_ONLY_RESIDUAL)}"
        )

    if derivable_ok and region_ok:
        # A fired advisory is named in the final status line so it can never sit
        # under an unqualified OK -- the gate genuinely passed, and something
        # still wants an operator's eyes.
        suffix = f" ({len(advisories)} ADVISORY)" if advisories else ""
        print(f"{manifest_path}: OK -- post-extraction gate passed{suffix}")
        sys.exit(0)
    sys.exit(1)


if __name__ == "__main__":
    main()
