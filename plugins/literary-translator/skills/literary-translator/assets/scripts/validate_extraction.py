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
    extract.py.template's run_self_checks exactly for the derivable subset.

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
        print(f"{manifest_path}: OK -- post-extraction gate passed")
        sys.exit(0)
    sys.exit(1)


if __name__ == "__main__":
    main()
