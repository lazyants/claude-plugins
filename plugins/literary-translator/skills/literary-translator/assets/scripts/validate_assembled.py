#!/usr/bin/env python3
"""validate_assembled.py -- #202 union structural-completeness gate: every
manifest-DECLARED heading must actually surface, book-wide, as non-empty
translated text -- run at W7/W8 (default `segment_drafts_and_audit` scope)
and again at W9 (`assembled_book` scope), AFTER `final_audit.py`/
`assemble.py` respectively. See SKILL.md's W7/W8/W9 sections and
`references/assembly-and-output.md` for the authoritative wiring; this
script's own module docstring is the ground truth for the gate's LOGIC.

## What this gate is, and is not

Ships ONLY the union structural-completeness invariant over the manifest's
DECLARED-heading set (`manifest.heading_types` plus the always-heading
built-in `"HEAD"`, #210). It deliberately does NOT attempt a per-block
length-band check (source/target ratios vary too wildly across language
pairs for a deterministic band to avoid permanent false-rejects -- "stub vs
terse" is a semantic judgment call for the W5 codex reviewer, not this
script) and does NOT derive its HARD gate from a broad type-name heuristic
(an ambiguous allowlist would permanently false-RED a legitimate project
whose own prose tag happens to be named e.g. `TITLE`). The DECLARED set is
the only non-heuristic source of truth for the HARD gate; the broad
allowlist survives only as a non-gating WARN (below).

## The invariant

Let `H = manifest.heading_types (or []) | {"HEAD"}`. **Source markers** are a
`Counter` keyed by `(seg, block_id)`, built by scanning EVERY `segments[]`
entry's own `block_ids[]` across the FULL manifest.json -- not only the
currently-converged subset (scoping to converged-only would vacuously pass a
zero-converged-but-declared-headings project; full-manifest scoping makes
that case genuinely RED instead, complementing rather than duplicating
`final_audit.py`'s own separate whole-project completeness gate) -- for every
block whose own `manifest.blocks[block_id].type` is in `H`.

A plain `set` cannot express the invariant this script actually enforces:
`manifest.schema.json` constrains neither `segments[].seg` uniqueness nor
within-one-segment `block_ids[]` uniqueness, so the exact same `(seg,
block_id)` key can legitimately recur -- an id repeated inside one segment's
own `block_ids[]`, or two manifest `segments[]` entries sharing the same
`seg` that both cite it. `Counter` is what lets the invariant demand
`C_output(key) >= C_source(key)` PER KEY, so a dropped occurrence can never
hide behind its surviving twin. Identity is `(seg, block_id)`, never text --
tolerant of source garble, nikud, or a translator's legitimate rewording. A
siman re-cited BY NUMBER in another chapter's own prose is not a
`block_ids[]` reference and is never counted; only its own home heading
block is.

Two scope-specific ways to compute `C_output` (never mixed):

  - **`assembled_book`** (`output.v1_scope`): `C_output` is a `Counter` of
    `(node["seg"], node["id"])` over `out/.assembled/nodestream.json`'s own
    `nodes[]`, for every node with `kind == "heading"` and non-empty `text`
    (nodestream nodes retain both `id` and `seg`, see `assemble.py`'s own
    `build_nodestream()`). Catches a declared heading that produced NO
    heading node at all -- assembly/misclassification loss -- the one
    incremental check no other gate provides (`validate_draft.py` only ever
    sees pre-assembly per-segment drafts, never the assembled book).
  - **default `segment_drafts_and_audit` scope (no render):** `C_output` is
    built from each declared heading's CURRENT converged-draft text,
    `text.strip() != ""` (whitespace does NOT count as surfaced -- the
    renderer strips and drops a whitespace-only heading,
    `render_obsidian.py`'s own body-heading handling), for exactly the
    `(seg, block_id)` keys the source side already named (this never widens
    the population). The honest incremental value here (on top of
    `validate_draft.py`, which already flags an empty translation of a
    NON-empty source block) is (a) a SOURCE-EMPTY declared heading -- not
    flagged by `validate_draft.py`, whose check is source-conditioned -- and
    (b) the CROSS-SEGMENT aggregate view a per-segment validator cannot give.
    A key belonging to a segment that is not (yet) converged contributes `0`
    -- naturally RED, no vacuous pass on a zero-converged project.

    **Reviewed-SHA rebind (mirrors `assemble.py`'s own
    `load_converged_segments()` guard):** every downstream draft consumer
    must recheck the ledger-recorded `reviewed_draft_sha1` before trusting a
    draft's current bytes. Because this gate reads converged drafts strictly
    AFTER `final_audit.py` and BEFORE delivery, a hand edit landing in that
    exact window would otherwise be evaluated (and delivered) without proof
    the reviewer ever saw those bytes. The rebind covers **every converged
    segment in `runs/ledger.json`**, not merely the heading-bearing subset
    the coverage invariant above happens to iterate -- an all-prose
    segment hand-edited after review must fail this gate too, even though
    it declares no heading at all and the coverage invariant alone would
    never look at it. For every converged segment, it recomputes that
    draft's current canonical sha1 from a SINGLE on-disk read (dispatch_token
    excluded, identical algorithm to `final_audit.py`/`assemble.py`'s own
    `draft_content_sha1()` -- hashed and parsed from the same read, never two
    independent reads of the same path, which would leave a TOCTOU window
    for an atomic swap between them) and compares it against `runs/
    ledger.json`'s merged `segments[seg].reviewed_draft_sha1`; a mismatch, or
    a converged record missing that field, is reported as its own
    `stale_review_since_audit` defect -- HARD, exit 1 -- rather than
    silently substituting untrusted content into `C_output`. A converged
    draft that cannot be read/decoded/parsed at all is a DIFFERENT case: a
    corrupt artifact this gate cannot evaluate at all, which raises an
    env/usage precondition (exit 2) instead -- never folded into the same
    HARD-defect bucket as a genuine reviewed-SHA mismatch (see
    `_rebind_or_flag_stale`'s own docstring for the exact split).

**WARN only** (exit 0, advisory, non-gating -- never a permanent
false-reject, and fires even for a project whose adapter hasn't declared
`heading_types` yet): a block whose own `type` is NOT in `H` but matches a
broad heading-like allowlist (`HEADING|TITLE|CHAPTER|SECTION|PART|SIMAN|
PEREK|H[1-6]`, case-insensitive, exact match) -- "possible undeclared
heading -- declare it in `manifest.heading_types` (see #210)". The HARD gate
never depends on this allowlist in either direction.

**No false-GREEN / false-RED:** a book with no `HEAD` blocks and no declared
`heading_types` yields an empty source Counter -- HARD clean by construction
(nothing declared, nothing to drop; the WARN scan still runs independently).
An empty declared heading is never legitimately "saved" by
`segments[].title_text` -- assembly puts the DRAFT text directly into the
heading node (`assemble.py`'s own block-loop), and the renderer derives
body/title/filename from that non-empty heading-node text; `title_text` is
only ever copied into the segpack's own `title` field, never an assembly
fallback. So rejecting an empty declared heading matches what actually ships
at runtime.

## Canonical paths (load-bearing, no target-language suffix)

    draft_path(seg)   = {durable_root}/segments/{seg}.draft.json
    segpack_path(seg) = {durable_root}/segments/segpack_{seg}.json

Self-anchored: this script always lives at `{durable_root}/scripts/<name>.py`
(copied there at Step 0a like every sibling script). It never assumes
`cwd == durable_root`, and takes no `--durable-root`/positional argument --
whole-project scope, like `final_audit.py`. NOT added to any
`*_BUNDLE_MEMBERS`/`_RENDER_VERSION_FILES`/named `*.schema.json` list -- it
flips no cache/render hash (those iterate explicit name tuples, never a
directory glob).

## Reporting

One JSON line on stdout: `{"defects": [{"seg", "block_id", "kind"}, ...],
"warnings": [{"seg", "block_id", "kind", "raw_type"}, ...]}`. A
`stale_review_since_audit` defect carries `block_id: null` (it is a
per-segment finding, not a per-block one); a `missing_heading` defect always
carries the real block id. Human-readable detail goes to stderr, matching
this plugin's own `final_audit.py`/`validate_draft.py` convention (callers
read stdout, not the exit code alone, for machine detail).

Exit `0` clean (defects empty; `warnings` may still be non-empty -- WARN
never gates), `1` on any HARD defect, `2` on an env/usage precondition
(missing PyYAML, missing/malformed manifest.json, a manifest whose
`blocks`/`segments`/`heading_types` do not match their own
manifest.schema.json shape, an unrecognized `output.v1_scope`, a missing
`out/.assembled/nodestream.json` in `assembled_book` scope, a missing/
malformed `runs/ledger.json` in default scope, or a bad CLI invocation).
Every one of these is a FAIL-CLOSED refusal, never a silent empty-default
or a silent fall-through to the wrong scope -- this is a delivery GATE, and
a gate that fails open on malformed input is worse than no gate at all.

Usage: python3 validate_assembled.py
"""
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import NoReturn

try:
    import yaml  # noqa: F401  # pyright: ignore[reportUnusedImport] -- transitively required by validate_draft.load_profile()
except ImportError:
    print(
        "ERROR: validate_assembled.py requires the 'PyYAML' package to read "
        "profile.yml (via validate_draft.py's own profile loader). Install "
        "with: pip install PyYAML (or: pip install -r requirements.txt from "
        "the literary-translator plugin's own directory).",
        file=sys.stderr,
    )
    sys.exit(2)

# ---------------------------------------------------------------------------
# Self-anchoring: this script always lives at {durable_root}/scripts/<name>.py.
# It never assumes cwd == durable_root, and never takes a --durable-root flag.
# ---------------------------------------------------------------------------
SCRIPTS_DIR = Path(__file__).resolve().parent
DURABLE_ROOT = SCRIPTS_DIR.parent
SEGMENTS_DIR = DURABLE_ROOT / "segments"
RUNS_DIR = DURABLE_ROOT / "runs"
MANIFEST_PATH = DURABLE_ROOT / "manifest.json"
LEDGER_PATH = RUNS_DIR / "ledger.json"
ASSEMBLED_DIR = DURABLE_ROOT / "out" / ".assembled"
NODESTREAM_PATH = ASSEMBLED_DIR / "nodestream.json"

# validate_draft.py lives next to this script -- imported directly (never
# reimplemented) purely for its own load_profile(), matching final_audit.py's
# and assemble.py's own established `import validate_draft as vd` pattern.
sys.path.insert(0, str(SCRIPTS_DIR))
try:
    import validate_draft as vd
except ImportError as exc:  # pragma: no cover -- defensive, should be unreachable
    print(
        f"ERROR: validate_assembled.py could not import validate_draft.py "
        f"from {SCRIPTS_DIR}: {exc}",
        file=sys.stderr,
    )
    sys.exit(2)

# Broad heading-like allowlist, WARN-only (never gates the HARD invariant) --
# an exact (not substring) match against a block's raw `type` tag.
BROAD_HEADING_LIKE_RE = re.compile(
    r"^(?:HEADING|TITLE|CHAPTER|SECTION|PART|SIMAN|PEREK|H[1-6])$", re.IGNORECASE
)

# ledger.schema.json's own closed `segments{}.status` enum -- verified
# against the real schema file, not assumed. `stale` is a status
# ledger_merge.py COMPUTES (a cache-key mismatch against a converged
# fragment) when materializing runs/ledger.json; it never appears in a raw
# ledger.d/*.json fragment, but IS a legitimate value here since this
# script reads the merged ledger.json, not raw fragments.
LEDGER_STATUS_ENUM = frozenset(
    {"pending", "in_progress", "converged", "non_converged", "blocked", "stale"}
)


class _MalformedArtifact(Exception):
    """THE INVARIANT this exception enforces: every input that determines
    the HARD population -- a manifest.heading_types ELEMENT, a manifest
    segments[] ENTRY (and its own `seg`/`block_ids`), the manifest.blocks{}
    VALUE a cited OR uncited block_id resolves to (and its own `type`/
    `seg`), a block_id's own CROSS-REFERENCE into manifest.blocks{} (codex
    R5-2 -- a dangling citation, not merely a shape defect), every ledger
    segments{} RECORD (shape + status), a trusted converged draft's own
    `blocks` field, and the assembled nodestream's own top-level `nodes`
    container -- must be either (a) well-formed per its own schema and
    counted, or (b) fatal (exit 2). It must NEVER be silently skipped in a
    way that could shrink source_counter, drop a segment from the
    reviewed-SHA rebind, or demote a real heading to a mere WARN -- every
    one of those is a fail-OPEN hole on a delivery gate. There is no
    remaining "chartered tolerance" in this exception's own domain as of
    codex R5 -- the one that used to exist (a dangling block_id citation)
    was itself found to be fail-open and was reversed (R5-2); see
    collect_source_markers' own docstring for the full history.

    A malformed value that would become an unhashable Counter-key
    component or `frozenset`/`set` membership operand is a RELATED but
    distinct hazard (an uncaught TypeError/AttributeError escaping this
    script's own exit-2 contract) -- fixed at each such site, but NOT
    always via this exception: `_validate_manifest_shape()`'s own
    manifest-shape guards (`seg`, block `type`) DO raise this (fatal,
    since skipping there would ALSO fail-open the coverage population);
    `collect_nodestream_output_markers`'s own heading-NODE `seg`/`id` guard
    deliberately does NOT (a SKIP there, consistent with that scope's own
    existing non-dict-node fence), while that SAME function's top-level
    `nodes` CONTAINER guard (codex R5-6) DOES raise this (a wrong-shaped
    container is a corrupt artifact, not a per-node defect the skip-fence
    policy was ever meant to cover).

    Raised by `_validate_manifest_shape()` below (the SINGLE up-front,
    gate-scoped manifest shape check, run once in main() before any
    collector, closing this whole class in one auditable place after
    several review rounds of scattered per-site guards each leaking a new
    cell), by `collect_source_markers()`'s own cross-reference check
    (R5-2), and by a small number of OTHER-artifact guards that are
    deliberately NOT manifest shape (a ledger record/status,
    `_rebind_or_flag_stale`'s corrupt-draft read AND its own trusted-draft
    `blocks`-shape check covering EVERY converged segment (R5-5, not just
    the heading-bearing subset), `collect_nodestream_output_markers`'s own
    `nodes` container check (R5-6)) -- each documented at its own raise
    site. Caught ONCE in main() and converted to the same `_fatal()`/
    exit-2 contract every other env/usage precondition in this script
    already uses."""


def _validate_manifest_shape(manifest, manifest_blocks, manifest_segments):
    """A SINGLE, up-front, gate-scoped shape validator -- run once in
    main() right after the top-level container-type checks (manifest_blocks
    is a dict, manifest_segments is a list) and BEFORE any collector runs.
    Closes the whole "a malformed manifest element silently shrinks
    source_counter, or becomes an unhashable Counter-key component /
    membership operand and crashes" class in ONE auditable place, so no
    collector needs (or should re-add) its own scattered per-site guard --
    exactly what let three separate review rounds each surface a new
    uncovered cell (a cited-only block guard that missed uncited blocks
    read by the WARN collector; a block `type`/segment `seg` that was
    schema-checked for shape but not for being hashable/str). After this
    function returns cleanly, `collect_source_markers` and
    `collect_undeclared_heading_like_warnings` may TRUST: every
    manifest_blocks value is a dict, whose own `type` (if present) is a
    str; every manifest_segments entry is a dict, whose own `seg` is a str
    and `block_ids` is a list of str.

    Deliberately NOT full jsonschema validation -- only the fields THIS
    gate actually reads are checked (never e.g. word_count/kind/order_index,
    which no collector here touches; checking those would over-catch on
    real partial manifests and this suite's own minimal fixtures). Every
    check below is grounded directly in manifest.schema.json's own
    constraint on that exact field (type, required, minItems, minLength --
    re-verified against the real schema file at codex R5, not assumed; two
    earlier "don't over-catch" judgment calls in this function turned out
    to be WRONG reads of the schema and are corrected here, see R5-3/R5-4
    below). Does NOT check cross-references between manifest.blocks{} and
    a segment's own block_ids[] citations -- collect_source_markers owns
    that (a dangling citation, not a shape violation; see that function's
    own docstring for why it is ALSO fatal, not tolerated).

    Returns the raw declared `heading_types` list (`[]` if the key is
    absent) -- the one place that already resolved "key present vs.
    absent" correctly (`"heading_types" in manifest`, never a
    None-returning `.get()`, since a PRESENT `null` must fatal, not be
    silently treated the same as an absent key), so main() doesn't
    re-derive the same membership logic.

    Raises _MalformedArtifact on the first violation found."""
    for bid, mb in manifest_blocks.items():
        if not isinstance(mb, dict):
            raise _MalformedArtifact(
                f"manifest.json 'blocks' entry {bid!r} must be an object "
                f"(manifest.schema.json: blocks.additionalProperties.type "
                f"== 'object'), got {type(mb).__name__}"
            )
        # codex R5-1 (BLOCKER): `type` is REQUIRED on EVERY block
        # (manifest.schema.json: blocks.additionalProperties.required
        # includes "type"). The earlier guard here tolerated an ABSENT
        # type (reasoning: "None in heading_types is harmless") -- that
        # was a fail-OPEN hole, not merely harmless: a cited HEAD block
        # that lost its `type` field would drop out of source_counter
        # entirely (never counted, never WARNed), shrinking the HARD
        # population silently. Absent and non-string are now both fatal,
        # uniformly, for every block (cited or not).
        block_type = mb.get("type")
        if not isinstance(block_type, str):
            raise _MalformedArtifact(
                f"manifest.json 'blocks' entry {bid!r} has a non-string "
                f"(or missing) 'type' field (manifest.schema.json: "
                f"blocks.additionalProperties.required includes 'type', "
                f"type 'string'), got "
                f"{type(block_type).__name__ if block_type is not None else 'missing'}"
            )
        # codex R5-7 (MINOR): block `seg` is read by the WARN collector
        # (collect_undeclared_heading_like_warnings embeds it directly into
        # a warning entry) -- manifest.schema.json: blocks.
        # additionalProperties.properties.seg.type == ["string", "null"].
        # Absent/null is a legitimate value (an unassigned block); only a
        # PRESENT non-string-non-null value is fatal.
        block_seg = mb.get("seg")
        if block_seg is not None and not isinstance(block_seg, str):
            raise _MalformedArtifact(
                f"manifest.json 'blocks' entry {bid!r} has a non-string, "
                f"non-null 'seg' field (manifest.schema.json: blocks."
                f"additionalProperties.properties.seg.type == "
                f"['string','null']), got {type(block_seg).__name__}"
            )

    for seg_entry in manifest_segments:
        if not isinstance(seg_entry, dict):
            raise _MalformedArtifact(
                f"manifest.json 'segments[]' entry {seg_entry!r} must be an "
                f"object (manifest.schema.json: segments.items.type == "
                f"'object')"
            )
        seg = seg_entry.get("seg")
        # `seg` becomes a Counter-key COMPONENT in collect_source_markers
        # (`counter[(seg, bid)]`) -- an unhashable seg (e.g. a list) would
        # crash with an uncaught TypeError. manifest.schema.json:
        # segments.items.properties.seg is REQUIRED, type "string".
        if not isinstance(seg, str):
            raise _MalformedArtifact(
                f"manifest.json segments[] entry {seg_entry!r} has a "
                f"non-string 'seg' (manifest.schema.json: "
                f"segments.items.properties.seg.type == 'string' -- "
                f"REQUIRED), got {type(seg).__name__}"
            )
        raw_bids = seg_entry.get("block_ids")
        if not isinstance(raw_bids, list):
            raise _MalformedArtifact(
                f"manifest.json segments[] entry {seg!r} has 'block_ids' "
                f"that is not an array (manifest.schema.json: block_ids is "
                f"REQUIRED, type 'array'), got "
                f"{type(raw_bids).__name__ if raw_bids is not None else 'missing'}"
            )
        # codex R5-3 (MAJOR): manifest.schema.json's block_ids carries
        # `minItems: 1` -- an earlier version of this check explicitly
        # (and WRONGLY) tolerated `[]` as "present, iterates nothing,
        # therefore valid". It is not: a segment with zero block_ids
        # contributes zero source markers no matter what it should have
        # declared, silently shrinking source_counter exactly like the
        # absent/null cases above.
        if not raw_bids:
            raise _MalformedArtifact(
                f"manifest.json segments[] entry {seg!r} has an empty "
                f"'block_ids' (manifest.schema.json: block_ids has "
                f"minItems: 1) -- refusing to silently treat a segment "
                f"with no declared blocks as contributing nothing"
            )
        for bid in raw_bids:
            if not isinstance(bid, str):
                raise _MalformedArtifact(
                    f"manifest.json segments[] entry {seg!r} has a "
                    f"non-string block_ids[] element {bid!r} "
                    f"(manifest.schema.json: block_ids.items.type == "
                    f"'string')"
                )

    if "heading_types" in manifest:
        raw_heading_types = manifest["heading_types"]
        if not isinstance(raw_heading_types, list):
            raise _MalformedArtifact(
                f"manifest.json 'heading_types' must be an array when "
                f"present (manifest.schema.json), got "
                f"{type(raw_heading_types).__name__} -- refusing to "
                f"silently treat a present null/scalar as absent, or "
                f"blindly iterate a scalar string's own characters"
            )
        for _item in raw_heading_types:
            # codex R5-4 (MAJOR): manifest.schema.json's heading_types.items
            # carries `minLength: 1` -- an earlier version of this check
            # explicitly (and WRONGLY) declined to reject an empty string,
            # reasoning "the schema also allows any non-empty string" (a
            # misreading -- the schema's own minLength IS "non-empty").
            # `""` would never equal any real block's own `type` tag
            # either (a block type is never an empty string in practice,
            # but even if it legitimately declared "SIMAN", a
            # heading_types element of "" can never match it), silently
            # demoting a real declared heading to WARN-only exactly like a
            # non-string element.
            if not isinstance(_item, str) or not _item:
                raise _MalformedArtifact(
                    f"manifest.json 'heading_types' element {_item!r} must "
                    f"be a non-empty string (manifest.schema.json: "
                    f"heading_types.items.minLength == 1) -- a non-string "
                    f"or empty element would never match any block's own "
                    f"string 'type' tag, silently demoting a real declared "
                    f"heading to a mere WARN"
                )
        return raw_heading_types
    return []  # genuinely absent -- the one tolerated case


def _fatal(msg) -> NoReturn:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(2)


def load_json(path, label):
    if not path.exists():
        return None, f"{label} missing: {path}"
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    # codex R8-1 (the 10th hole, closes the whole class): ValueError is the
    # actual shared parent of every failure mode here. json.JSONDecodeError
    # AND UnicodeDecodeError (from .read_text()) are BOTH ValueError
    # subclasses, so naming them separately (as an earlier round did) is
    # redundant -- and json.loads() can ALSO raise a BARE ValueError that is
    # neither: a syntactically-valid but oversized integer literal trips
    # Python's own int-string conversion digit limit (default 4300 digits),
    # e.g. `json.loads('{"n": ' + '9'*5000 + '}')` raises a plain
    # ValueError, not JSONDecodeError. Catching ValueError directly, rather
    # than enumerating its subclasses one exotic input at a time, closes
    # this class in one place -- the same lesson the profile-load boundary
    # already learned (R6-1/R7-1: enumerating exception types at a
    # black-box parse boundary is whack-a-mole). security-review: deeply
    # nested adversarial JSON (`"["*2000 + "]"*2000`) makes json.loads()
    # raise RecursionError -- a RuntimeError subclass, NOT a ValueError --
    # which would otherwise escape this same boundary uncaught; added here
    # as the third genuinely INPUT-triggered load-boundary class (I/O,
    # parse/decode/int-limit, deep-nest), deliberately still NOT a blanket
    # `except Exception`, so a genuine internal-code bug still surfaces as
    # an uncaught traceback rather than being silently misreported as a
    # content defect.
    except (OSError, ValueError, RecursionError) as exc:
        return None, f"{label} at {path} is not valid JSON: {exc}"


def draft_path(seg):
    return SEGMENTS_DIR / f"{seg}.draft.json"


def segpack_path(seg):
    return SEGMENTS_DIR / f"segpack_{seg}.json"


def _canonical_draft_projection_sha1(doc):
    """sha1 of the canonical (dispatch_token-excluded, sorted-key) JSON
    projection of an ALREADY-PARSED draft dict `doc`. Factored out of
    draft_content_sha1() so a caller that must hash AND evaluate a draft's
    content -- _rebind_or_flag_stale() below -- can do both from the SAME
    single on-disk read, never two independent path.read_text() calls
    (a real TOCTOU window: an atomic swap between the two reads could hash
    reviewed bytes A while evaluating unreviewed bytes B into C_output)."""
    if not isinstance(doc, dict):
        raise ValueError(f"draft must be a JSON object, got {type(doc).__name__}")
    projected = {k: v for k, v in doc.items() if k != "dispatch_token"}
    canonical = json.dumps(
        projected, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha1(canonical).hexdigest()


def draft_content_sha1(path):
    """sha1 of a draft's CONTENT, with the 'dispatch_token' metadata field
    deliberately EXCLUDED. Byte-for-byte the same algorithm as
    final_audit.py's/assemble.py's own draft_content_sha1() (duplicated
    here, matching this plugin's established per-script convention rather
    than a shared import) -- parses the draft as JSON, drops
    'dispatch_token' if present, and re-serializes the remainder via sorted-
    key canonical JSON before hashing. Compared directly against a
    converged segment's own `reviewed_draft_sha1`, never a raw-bytes hash of
    the on-disk file. A single-purpose convenience wrapper (one path in, one
    read, one digest out) -- _rebind_or_flag_stale() below does NOT call
    this; it shares one read with _canonical_draft_projection_sha1()
    directly instead (see that function's own docstring).

    Raises OSError (unreadable file), json.JSONDecodeError (not valid
    JSON), or ValueError (valid JSON but not an object) on failure --
    callers handle all three."""
    doc = json.loads(path.read_text(encoding="utf-8"))
    return _canonical_draft_projection_sha1(doc)


# ---------------------------------------------------------------------------
# Source markers -- Counter keyed by (seg, block_id), full manifest.
# ---------------------------------------------------------------------------


def collect_source_markers(manifest_segments, manifest_blocks, heading_types):
    """Counter of (seg, block_id) over every declared-heading block cited by
    ANY segments[] entry's own block_ids[] in the FULL manifest -- not only
    converged segments. Multiplicity-preserving on purpose (see module
    docstring): the same key legitimately recurs when an id is repeated
    inside one segment's own block_ids[], or when two segments[] entries
    share a `seg` and both cite it -- manifest.schema.json enforces neither
    uniqueness.

    Trusts `_validate_manifest_shape()` already ran (main() calls it
    before this function): every seg_entry is a dict with a str `seg` and
    a non-empty list-of-str `block_ids`; every manifest_blocks value is a
    dict whose own `type` is ALWAYS a real, non-empty string -- so
    `counter[(seg, bid)]` can never hit an unhashable key component, and
    `mb["type"] in heading_types` can never hit an unhashable membership
    operand.

    codex R5-2 (BLOCKER, reverses an earlier ratified tolerance): a
    block_id cited by a segment's own block_ids[] that names NO
    manifest.blocks{} entry is now FATAL, not tolerated. manifest.
    schema.json's own description of block_ids is explicit -- "keys into
    the top-level blocks{} object" -- so a dangling citation is an
    INTERNAL CONSISTENCY violation the gate cannot evaluate (was this
    dangling id supposed to be a declared heading? unknowable), not an
    out-of-charter extraction-level nicety. The earlier "chartered
    tolerance" reasoning (deleting a block record while leaving its
    citation in place should be silently ignored) was itself a fail-open
    hole: it let source_counter silently shrink to nothing for exactly
    that deleted id, the same failure mode every other guard in this
    module exists to close."""
    counter = Counter()
    for seg_entry in manifest_segments:
        seg = seg_entry["seg"]
        for bid in seg_entry["block_ids"]:
            if bid not in manifest_blocks:
                raise _MalformedArtifact(
                    f"segment {seg!r} cites block_id {bid!r} that has no "
                    f"manifest.blocks{{}} entry -- manifest is internally "
                    f"inconsistent (block_ids are keys into blocks{{}}, "
                    f"manifest.schema.json's own description); cannot "
                    f"evaluate whether it would have been a declared heading"
                )
            mb = manifest_blocks[bid]
            if mb["type"] in heading_types:
                counter[(seg, bid)] += 1
    return counter


# ---------------------------------------------------------------------------
# assembled_book scope: C_output from the assembled NodeStream.
# ---------------------------------------------------------------------------


def collect_nodestream_output_markers(nodestream):
    """Counter of (node["seg"], node["id"]) for every nodestream.json node
    with kind=="heading" and non-empty text -- exactly the population a
    declared-heading source marker must surface as, post-assembly.

    codex R5-6 (MAJOR): `nodestream.get("nodes") or []` tolerated ANY
    falsy-or-absent value as "no nodes" and, worse, silently accepted any
    OTHER truthy non-list value only to crash on the very next line -- a
    truthy int (`"nodes": 5`) makes `for node in 5` raise an uncaught
    `TypeError: 'int' object is not iterable` (exit 1, escaping this
    script's own exit-2 contract), while a wrong-shaped-but-falsy value
    (`"nodes": {}`, an empty dict) silently iterates zero nodes and exits
    0 clean on a project that assembled nothing. There is no nodestream
    JSON Schema (assemble.py's own contract is the sole source of truth,
    never schema-validated), so this is hand-rolled: the `nodes` key must
    itself be an array, checked ONCE before the loop -- a corrupt
    assembled artifact this gate cannot evaluate, distinct from a
    per-NODE defect (which stays a fail-closed skip, per the fence
    comment below)."""
    nodes = nodestream.get("nodes")
    if not isinstance(nodes, list):
        raise _MalformedArtifact(
            f"assembled nodestream 'nodes' must be an array, got "
            f"{type(nodes).__name__ if nodes is not None else 'missing'} "
            f"-- a corrupt assembled artifact this gate cannot evaluate"
        )
    counter = Counter()
    for node in nodes:
        if not isinstance(node, dict):
            # fail-CLOSED, not fail-open: a dropped/malformed node only makes
            # coverage MORE likely to RED (this key's own C_output stays 0,
            # never inflated) -- over-catch/safe direction, so this is
            # deliberately NOT fatal, unlike the manifest/ledger element
            # guards above (which fail-open toward exit 0 if skipped). The
            # SAME reasoning covers a heading node whose own `seg`/`id` are
            # not both strings (codex R4 FIX 9): guarded below via `continue`
            # (a skip), NOT `_MalformedArtifact` -- an unhashable/non-str
            # component would otherwise crash a Counter-key build with an
            # uncaught TypeError, but SKIPPING it is already fail-closed
            # (this key's own C_output stays 0, so its source marker
            # naturally REDs via compute_missing_heading_defects, never
            # masked) and keeps this whole scope's own skip-fence policy
            # internally consistent -- assembled_book scope never raises
            # _MalformedArtifact for a per-node defect, only for a
            # top-level nodestream shape failure (see main()).
            continue
        if node.get("kind") != "heading":
            continue
        text = node.get("text")
        if not (isinstance(text, str) and text.strip()):
            continue
        # A REAL, non-empty heading node's own `seg`/`id` become a
        # Counter-KEY component -- guarded only on THIS path (the one that
        # actually builds a key); a non-heading or empty-text node's own
        # seg/id are never read into a key at all, so guarding them
        # unconditionally would over-catch on a placeholder/irrelevant
        # node's own junk metadata.
        node_seg, node_id = node.get("seg"), node.get("id")
        if not isinstance(node_seg, str) or not isinstance(node_id, str):
            continue  # fail-closed skip -- see the fence comment above
        counter[(node_seg, node_id)] += 1
    return counter


# ---------------------------------------------------------------------------
# default segment_drafts_and_audit scope: C_output from converged drafts,
# rebinding to each segment's own ledger-recorded reviewed_draft_sha1.
# ---------------------------------------------------------------------------


def _rebind_or_flag_stale(seg, record, stale_segs):
    """Returns the parsed draft dict for `seg` iff its on-disk canonical
    sha1 currently matches `record["reviewed_draft_sha1"]` -- mirrors
    assemble.py's own load_converged_segments() guard byte-for-byte.

    Two DIFFERENT failure classes, deliberately reported differently: (1) no
    recorded sha1, a missing draft file, or a genuine sha1 MISMATCH are all
    "this gate CAN evaluate the draft, and it fails the reviewed-SHA check"
    -- HARD, added to `stale_segs`, exit 1 (a real, actionable defect: a
    hand edit the reviewer never saw). (2) a draft that cannot be
    read/decoded/parsed at all, OR that parses fine but has a malformed
    own `blocks` field, is a CORRUPT ARTIFACT this gate cannot evaluate --
    raises _MalformedArtifact (env exit 2 via main()), matching load_json's
    own fatal treatment of a corrupt manifest/ledger, rather than being
    folded into the same HARD-defect bucket as a genuine mismatch.

    codex R5-5 (MAJOR): the `blocks`-must-be-a-dict check used to live in
    collect_default_output_markers, gated behind `for seg, bid in
    source_keys` -- so it only ever ran for segments that OWN at least one
    declared-heading key. An ALL-PROSE converged segment (no heading keys
    at all) with a SHA-matching-but-structurally-corrupt draft (e.g.
    `"blocks": ["text"]`) was never checked at all -- the exact same
    heading-bearing-subset blind spot BLOCKER-1 (R4) fixed for the rebind
    itself. Moving the check HERE means it runs for EVERY converged
    segment this function ever returns a draft for, regardless of whether
    that segment happens to declare a heading -- draft.schema.json:
    `blocks` is REQUIRED, type "object".

    Reads the draft's on-disk bytes EXACTLY ONCE, then hashes AND parses
    from that SAME read -- never two independent path.read_text() calls
    (a TOCTOU window: an atomic file swap between a hash-only read and a
    separate content-only read could let a matching-SHA reviewed draft A
    be the one hashed while an unreviewed draft B is what actually supplies
    C_output)."""
    expected = record.get("reviewed_draft_sha1")
    dp = draft_path(seg)
    if not isinstance(expected, str) or not expected or not dp.is_file():
        stale_segs.add(seg)
        return None
    try:
        draft = json.loads(dp.read_text(encoding="utf-8"))
        actual = _canonical_draft_projection_sha1(draft)
    # codex R8/security-review: collapsed to the same DRY form as
    # load_json's own except tuple -- json.JSONDecodeError and
    # UnicodeDecodeError are BOTH ValueError subclasses (redundant to name
    # separately), and RecursionError (deeply-nested adversarial JSON) is
    # the third genuinely input-triggered load-boundary class, none of
    # them a blanket `except Exception`. See load_json's own comment for
    # the full rationale.
    except (OSError, ValueError, RecursionError) as exc:
        raise _MalformedArtifact(
            f"converged segment {seg!r}'s draft at {dp} could not be "
            f"read/decoded/parsed ({exc}) -- a corrupt artifact this gate "
            f"cannot evaluate"
        ) from exc
    if actual != expected:
        stale_segs.add(seg)
        return None
    draft_blocks = draft.get("blocks")
    if not isinstance(draft_blocks, dict):
        raise _MalformedArtifact(
            f"converged segment {seg!r}'s draft has a 'blocks' field that "
            f"is not an object (draft.schema.json: blocks is REQUIRED, "
            f"type 'object'), got "
            f"{type(draft_blocks).__name__ if draft_blocks is not None else 'missing'} "
            f"-- a trusted-but-structurally-corrupt draft"
        )
    return draft


def collect_reviewed_draft_rebind(ledger_segments):
    """Rebind-checks EVERY converged segment in `ledger_segments` against
    its own recorded reviewed_draft_sha1 -- mirrors assemble.py's own
    load_converged_segments() guard, and deliberately covers the WHOLE
    converged population, not merely the heading-bearing subset the
    coverage invariant happens to need. The rebind is a general
    draft-trust guard ("did the reviewer actually see these bytes"), not a
    heading-scoped one: an all-prose converged segment hand-edited (or its
    draft replaced) strictly after review must RED too, even though it
    declares no heading at all and the coverage invariant would otherwise
    never look at it (BLOCKER 1). Returns (trusted_drafts: {seg: draft
    dict}, stale_segs: set) -- `trusted_drafts` holds only segments that
    passed the rebind; any other converged-or-not segment is simply absent
    from it.

    Three element-shape/security guards, all fatal (raise _MalformedArtifact,
    never silently skipped -- see that exception's own docstring for why a
    skip here is a fail-open hole): a SEG KEY that fails vd.validate_seg()'s
    own path-safety allowlist (SECURITY -- this key becomes draft_path(seg),
    so an unvalidated one is a path-traversal read primitive, see the guard's
    own comment below), a non-object ledger record (ledger.schema.json), and
    a `status` outside LEDGER_STATUS_ENUM (ledger.schema.json's own closed
    enum). The status guard matters most:
    an UNRECOGNIZED status (a typo, e.g. "convergd") would otherwise be
    silently treated as "not converged" and skipped by the `!= "converged"`
    test below -- reincarnating BLOCKER-1 for exactly the segment it fixed
    (a genuinely-converged, all-prose segment whose draft was hand-edited
    after review, now invisible to BOTH the rebind AND the heading-coverage
    invariant, since it declares no heading at all)."""
    trusted_drafts = {}
    stale_segs = set()
    for seg, record in ledger_segments.items():
        # SECURITY (path-traversal): this seg key becomes draft_path(seg) =
        # SEGMENTS_DIR / f"{seg}.draft.json" (via _rebind_or_flag_stale
        # below), so an unsanitized key like "../../../../tmp/x" would read
        # a file OUTSIDE segments/ -- ledger.schema.json has no
        # propertyNames pattern constraining this key's own shape. Reject
        # at ingestion, before ANY draft_path() can be built from it --
        # vd.validate_seg() (validate_draft.py's own established allowlist,
        # reused rather than reimplemented) forbids '..', path separators,
        # absolute paths, and shell metacharacters. Fatal, not skipped --
        # same fail-closed rationale as the record/status guards below.
        if (err := vd.validate_seg(seg)) is not None:
            raise _MalformedArtifact(f"runs/ledger.json segment key {seg!r}: {err}")
        if not isinstance(record, dict):
            raise _MalformedArtifact(
                f"runs/ledger.json segment {seg!r} record must be an object "
                f"(ledger.schema.json), got {type(record).__name__}"
            )
        status = record.get("status")
        # `isinstance` MUST come first and short-circuit via `or` -- a bare
        # `status not in LEDGER_STATUS_ENUM` raises TypeError: unhashable
        # type for status: []/{} (a frozenset membership test hashes its
        # operand), which would escape this handler as an uncaught
        # traceback (exit 1), not this script's own clean exit-2 contract.
        if not isinstance(status, str) or status not in LEDGER_STATUS_ENUM:
            raise _MalformedArtifact(
                f"runs/ledger.json segment {seg!r} has status={status!r}, "
                f"not one of {sorted(LEDGER_STATUS_ENUM)} (ledger.schema.json) "
                f"-- cannot classify its convergence state"
            )
        if status != "converged":
            continue
        draft = _rebind_or_flag_stale(seg, record, stale_segs)
        if draft is not None:
            trusted_drafts[seg] = draft
    return trusted_drafts, stale_segs


def collect_default_output_markers(source_counter, trusted_drafts):
    """Counter of surfaced (seg, block_id) keys, restricted to exactly the
    keys `source_counter` names (never widens the population). Reads draft
    content ONLY from `trusted_drafts` (see collect_reviewed_draft_rebind)
    -- a segment that failed the reviewed-SHA rebind, or was never
    converged at all, is simply absent from `trusted_drafts` and
    contributes 0 to every one of its own keys, naturally RED (no vacuous
    pass -- #208 remains the general completeness gate).

    ped-ant PR#230 [P1]: `draft.blocks{}` is KEYED by block_id -- exactly
    ONE string per id, never one-per-occurrence. When a segment's own
    block_ids[] cites the SAME id twice (schema-legal: manifest.schema.json
    sets no uniqueItems on block_ids), assemble.py's own block loop
    (`for bid in block_ids:`) iterates BOTH occurrences and emits a node
    for EACH, but both nodes read the identical draft["blocks"][bid]
    string -- the assembler REPLICATES one keyed draft across every
    occurrence, and `duplicate_order_index` (which iterates the unique
    manifest_blocks dict, never a segment's own possibly-repeating
    block_ids[] list) does not and was never meant to reject this. So in
    THIS scope, a present, non-empty draft block genuinely satisfies ALL
    of that key's declared source occurrences -- crediting only 1
    regardless of `src_mult` would falsely RED a schema-valid,
    assembler-supported book (a real reviewer-caught P1: a doubly-cited
    heading is legitimate content, not a defect). Occurrence-level DROP
    detection -- a genuinely missing individual rendered NODE -- is the
    `assembled_book` scope's own job (collect_nodestream_output_markers),
    where per-occurrence nodes actually, physically exist to be counted;
    the default (no-render) scope structurally cannot represent
    occurrences at all, only key presence.

    codex R5-5: the trusted-draft `blocks`-must-be-a-dict check used to
    live HERE, gated behind the heading-bearing keys loop -- so an
    all-prose segment's own corrupt draft was never checked. It now lives
    in `_rebind_or_flag_stale` itself (see that function's own docstring),
    which runs for EVERY converged segment regardless of whether it
    declares a heading -- so by the time a draft lands in `trusted_drafts`
    here, its own `blocks` field is ALREADY guaranteed a dict, and
    `.get(bid)` below can never hit a non-dict."""
    counter = Counter()
    for (seg, bid), src_mult in source_counter.items():
        draft = trusted_drafts.get(seg)
        if draft is None:
            continue
        text = draft["blocks"].get(bid)
        if isinstance(text, str) and text.strip():
            counter[(seg, bid)] += src_mult
    return counter


# ---------------------------------------------------------------------------
# The invariant check itself -- a pure function, deliberately factored out
# of main() so it is independently unit-testable (and mutation-testable:
# tests/validate_assembled.test.py monkeypatches this exact function to a
# no-op to prove the RED fixtures it exercises aren't vacuously green
# regardless of what this function does).
# ---------------------------------------------------------------------------


def compute_missing_heading_defects(source_counter, output_counter, stale_segs):
    """For every (seg, block_id) key the source side declared, C_output(key)
    must be >= C_source(key) -- a Counter comparison, never a set membership
    test (see module docstring: the same key can legitimately recur, and
    only a per-key COUNT catches a dropped occurrence hiding behind its
    surviving twin). A key belonging to a segment already reported
    stale_review_since_audit is skipped here -- that segment's whole draft
    is unreviewed-since-audit, already reported once, not re-reported per
    heading key."""
    defects = []
    for (seg, bid), want in source_counter.items():
        if seg in stale_segs:
            continue
        got = output_counter.get((seg, bid), 0)
        if got < want:
            defects.append({"seg": seg, "block_id": bid, "kind": "missing_heading"})
    return defects


# ---------------------------------------------------------------------------
# #201 enforced heading-shape output contract -- a surfaced, NON-EMPTY
# translated heading must not itself carry a leading markdown heading marker
# (the renderer supplies the `## `/level). Cache-free: this script is in no
# bundle/render/schema list (see module docstring), so this check flips no
# cache/render hash. Reuses the SAME object main() already built for the
# current scope (no re-parse, no new I/O).
# ---------------------------------------------------------------------------

# The ONLY banned shape: a leading markdown heading marker, optionally
# preceded by whitespace the renderer would strip. Deliberately narrow -- a
# `#` anywhere but the head, a source-language echo, or a bilingual
# source+target line is all legitimate in some projects and never flagged
# (false-RED-averse: an ambiguous echo/duplicate-line heuristic would
# permanently false-reject a legitimate bilingual book).
_HEADING_LEADING_HASH_RE = re.compile(r"^\s*#")


def _heading_text_has_leading_hash(text):
    """True iff `text` is a present, NON-EMPTY (post-strip) string whose
    first non-whitespace character is a markdown heading marker `#`. In the
    default (no-render) scope this alone keeps this check from ever
    double-firing with compute_missing_heading_defects' own missing_heading
    for the same key: collect_default_output_markers credits a key's FULL
    source multiplicity the moment a present, non-empty draft exists,
    malformed or not, so missing_heading only ever fires there when this
    check would also skip (draft absent). It is NOT enough in the
    `assembled_book` scope, where collect_nodestream_output_markers counts
    per PHYSICAL NODE regardless of content -- a key can be both under-
    surfaced AND have one of its few surviving nodes be malformed at once
    (codex review of #201, Low). See collect_heading_shape_defects'
    `missing_keys` parameter for how that scope's own double-fire is
    suppressed."""
    return (
        isinstance(text, str)
        and text.strip() != ""
        and _HEADING_LEADING_HASH_RE.match(text) is not None
    )


def collect_heading_shape_defects(source_counter, trusted_drafts=None, nodestream=None, missing_keys=None):
    """For every declared-heading (seg, block_id) key `source_counter`
    already names, emit a `heading_leading_hash` defect when its surfaced,
    NON-EMPTY translated heading text begins with a markdown heading marker
    (`^\\s*#`). Scoped strictly to source_counter's own declared heading
    keys -- never widens the population -- and reuses the SAME object main()
    already built for the current scope (no re-parse, no new I/O):
    `trusted_drafts` in default `segment_drafts_and_audit` scope,
    `nodestream` in `assembled_book` scope.

    Distinct from compute_missing_heading_defects: that gate owns the
    empty/absent case (`missing_heading`); this one owns the present-but-
    malformed case. In the default scope the two are naturally mutually
    exclusive per key (see _heading_text_has_leading_hash's own docstring).
    In the `assembled_book` scope they are NOT -- a key can be under-
    surfaced (fewer physical nodes than declared occurrences) while one of
    its few surviving nodes is also malformed, so both would independently
    be true (codex review of #201, Low; see
    tests/validate_assembled.test.py::
    test_red_assembled_book_duplicate_heading_dropped_node_also_malformed).
    Both are hard defects (exit 1 either way), so `missing_keys` -- the set
    of (seg, block_id) keys compute_missing_heading_defects already flagged
    -- enforces "one defect per key" as an actual invariant rather than an
    incidental non-collision: the under-surfaced defect is the more
    fundamental one, and any malformed node sharing that key is by
    construction one of the SAME under-count's own surfaced subset (a key
    can never be simultaneously under-surfaced and fully-surfaced), so
    nothing distinct is lost by not also reporting it. A segment that
    failed the reviewed-SHA rebind is absent from `trusted_drafts` and is
    skipped here too (already reported once as stale_review_since_audit;
    its untrusted bytes are never inspected)."""
    defects = []
    missing_keys = missing_keys or set()
    if nodestream is not None:
        declared = set(source_counter)
        flagged = set()
        nodes = nodestream.get("nodes")
        for node in nodes if isinstance(nodes, list) else []:
            if not isinstance(node, dict) or node.get("kind") != "heading":
                continue
            node_seg, node_id = node.get("seg"), node.get("id")
            # A non-str seg/id is never a real declared source key AND is an
            # unhashable set-membership operand -- skip it, mirroring
            # collect_nodestream_output_markers' own fail-closed fence (its
            # own source marker then naturally REDs via missing_heading;
            # never an uncaught TypeError).
            if not isinstance(node_seg, str) or not isinstance(node_id, str):
                continue
            key = (node_seg, node_id)
            if key in flagged or key not in declared or key in missing_keys:
                continue
            if _heading_text_has_leading_hash(node.get("text")):
                flagged.add(key)  # one defect per key, even if several nodes share it
                defects.append({"seg": node_seg, "block_id": node_id, "kind": "heading_leading_hash"})
    else:
        drafts = trusted_drafts or {}
        for seg, bid in source_counter:
            if (seg, bid) in missing_keys:
                continue
            draft = drafts.get(seg)
            if draft is None:
                continue
            # `draft["blocks"]` is guaranteed a dict by _rebind_or_flag_stale.
            if _heading_text_has_leading_hash(draft["blocks"].get(bid)):
                defects.append({"seg": seg, "block_id": bid, "kind": "heading_leading_hash"})
    return defects


# ---------------------------------------------------------------------------
# WARN: undeclared but heading-shaped block types -- advisory, never gating.
# ---------------------------------------------------------------------------


def collect_undeclared_heading_like_warnings(manifest_blocks, heading_types):
    """codex R4 FIX 10: iterates EVERY manifest_blocks value -- including
    blocks NOT cited by any segment's own block_ids[] (an uncited block is
    still worth a WARN if its own type looks heading-shaped). This is
    exactly the path collect_source_markers' own cited-only guards never
    covered across three review rounds -- an uncited block's malformed
    value would otherwise crash this loop. Trusts `_validate_manifest_
    shape()` already ran (main() calls it before this function, over the
    SAME manifest_blocks): every value is a dict, whose own `type` is
    ALWAYS a non-empty-required str (codex R5-1 -- absent/non-string type
    is now fatal for every block, not merely tolerated) and whose own
    `seg` (if present) is a str (codex R5-7) -- so `.get("type")` can
    never hit a non-dict, is never anything but a real string, and can
    never be an unhashable `in heading_types` operand."""
    warnings = []
    for bid, mb in sorted(manifest_blocks.items()):
        raw_type = mb["type"]
        if raw_type in heading_types:
            continue
        if BROAD_HEADING_LIKE_RE.fullmatch(raw_type):
            warnings.append(
                {"seg": mb.get("seg"), "block_id": bid, "kind": "undeclared_heading_like", "raw_type": raw_type}
            )
    return warnings


def main():
    if len(sys.argv) != 1:
        print("usage: python3 validate_assembled.py", file=sys.stderr)
        sys.exit(2)

    manifest, err = load_json(MANIFEST_PATH, "manifest.json")
    if err:
        _fatal(err)
    if not isinstance(manifest, dict):
        _fatal(f"manifest.json at {MANIFEST_PATH} did not parse to an object")

    # Structural shape checks -- BLOCKER 2: a malformed manifest must never
    # silently coerce to an empty/degenerate population and fail OPEN. Every
    # one of these three fields is `required`/typed in manifest.schema.json;
    # a violation here means Step 0's own schema validation was bypassed or
    # the file was hand-edited since -- an env/usage precondition (exit 2),
    # never a silent empty-default.
    manifest_blocks = manifest.get("blocks")
    if not isinstance(manifest_blocks, dict):
        _fatal(
            f"manifest.json 'blocks' must be an object, got "
            f"{type(manifest_blocks).__name__ if manifest_blocks is not None else 'missing'}"
        )
    manifest_segments = manifest.get("segments")
    if not isinstance(manifest_segments, list):
        _fatal(
            f"manifest.json 'segments' must be an array, got "
            f"{type(manifest_segments).__name__ if manifest_segments is not None else 'missing'}"
        )

    # codex R6-1/R7-1: vd.load_profile() (validate_draft.py) is a BLACK-BOX
    # config-load boundary this script does not own -- enumerating its
    # possible failure modes one exception type at a time is whack-a-mole
    # (R6-1 added OSError/UnicodeDecodeError for its two `.read_text()`
    # calls; R7 found a 9th hole where a valid-JSON-but-wrong-shaped
    # ownership marker, e.g. `{"owner_profile_path": 1}`, passes
    # load_profile()'s own truthiness check and reaches `Path(1)`, raising
    # TypeError -- a type neither R6-1's tuple nor the schema-shape guards
    # elsewhere in this file were ever going to anticipate). The general
    # rule for a black-box loader: ANY exception it raises means "can't
    # load the profile, can't evaluate, env error" -- exit 2 -- so this
    # catches broadly rather than adding a 10th named type. `except
    # Exception` deliberately does NOT catch `SystemExit` (a BaseException,
    # not an Exception) -- load_profile()'s own detected-error paths call
    # validate_draft.py's own `_fatal()`, which `sys.exit(2)`s directly,
    # and that SystemExit(2) propagates through this try/except completely
    # untouched, preserving the correct exit code for THAT case exactly as
    # before. Scoped LOCAL to this script (never editing validate_draft.py's
    # own load_profile() -- it has other consumers whose exit contracts
    # this fix must not ripple into). The exception's own type name is
    # folded into the message so a real error stays diagnosable, just
    # reported at the correct exit code instead of a raw traceback.
    try:
        profile = vd.load_profile()
    except Exception as exc:
        _fatal(
            f"could not load profile.yml (via validate_draft.py's own "
            f"profile loader): {type(exc).__name__}: {exc}"
        )
    try:
        v1_scope = profile["output"]["v1_scope"]
    except (KeyError, TypeError) as exc:
        _fatal(f"profile.yml is missing required field 'output.v1_scope' ({exc})")

    stale_segs = set()
    # Whichever of these the selected scope builds is the object
    # collect_heading_shape_defects (#201) reuses after the try -- default
    # scope binds trusted_drafts, assembled_book scope binds nodestream; the
    # other stays None (a scope that never built it), which that collector
    # treats as "nothing to check on this side".
    trusted_drafts = None
    nodestream = None
    # codex R4: `_validate_manifest_shape()` -- the SINGLE, up-front,
    # gate-scoped shape validator -- runs FIRST, before any collector, so
    # every collector below can TRUST every element shape it reads (see
    # that function's own docstring for exactly what it guarantees). Every
    # collector call, and the shape validator itself, can raise
    # _MalformedArtifact (a manifest element, ledger record, trusted-draft
    # field, or assembled-nodestream node that violates its own schema's
    # shape) -- caught ONCE here and converted to this script's own
    # _fatal()/exit-2 contract, never left to silently skip an element in a
    # way that could shrink source_counter, drop a segment from the
    # reviewed-SHA rebind, demote a real heading to WARN, or crash with an
    # uncaught TypeError/AttributeError (see _MalformedArtifact's own
    # docstring for the full invariant).
    try:
        raw_heading_types = _validate_manifest_shape(manifest, manifest_blocks, manifest_segments)
        # #210: manifest-declared block types that classify as headings,
        # plus the always-heading built-in "HEAD" -- empty declared set is
        # byte-identical to "only HEAD is a heading" (pre-#210 behavior).
        heading_types = frozenset(raw_heading_types) | {"HEAD"}

        source_counter = collect_source_markers(manifest_segments, manifest_blocks, heading_types)

        # MAJOR 2: an explicit elif/else, never a bare if/else that silently
        # routes any unrecognized value into the default branch -- a typo'd
        # scope (e.g. "assembled-boook") must fail closed, not quietly skip
        # the nodestream invariant it was supposed to select. profile.yml's
        # own loader (vd.load_profile()) does NOT run jsonschema validation
        # (see that function's own module), so profile.schema.json's
        # output.v1_scope enum is NOT already enforced upstream of this
        # read -- this check is load-bearing, not defensive-redundant.
        if v1_scope == "assembled_book":
            nodestream, err = load_json(NODESTREAM_PATH, "assembled nodestream")
            if err:
                _fatal(f"{err} -- run assemble.py before validate_assembled.py in assembled_book scope")
            if not isinstance(nodestream, dict):
                _fatal(f"nodestream at {NODESTREAM_PATH} did not parse to an object")
            output_counter = collect_nodestream_output_markers(nodestream)
        elif v1_scope == "segment_drafts_and_audit":
            ledger, err = load_json(LEDGER_PATH, "runs/ledger.json")
            if err:
                _fatal(f"{err} -- run final_audit.py before validate_assembled.py in default scope")
            ledger_segments = ledger.get("segments") if isinstance(ledger, dict) else None
            if not isinstance(ledger_segments, dict):
                _fatal(f"runs/ledger.json at {LEDGER_PATH} is missing its 'segments' object")
            # BLOCKER 1: the reviewed-SHA rebind covers EVERY converged
            # segment in the ledger, not merely the heading-bearing subset
            # source_counter happens to name -- an all-prose segment
            # hand-edited after review must RED too (see
            # collect_reviewed_draft_rebind's own docstring).
            trusted_drafts, stale_segs = collect_reviewed_draft_rebind(ledger_segments)
            output_counter = collect_default_output_markers(source_counter, trusted_drafts)
        else:
            _fatal(
                f"profile.yml output.v1_scope={v1_scope!r} is not one of "
                f"'segment_drafts_and_audit'/'assembled_book' (profile.schema.json's "
                f"own enum) -- refusing to silently fall back to the default scope"
            )
    except _MalformedArtifact as exc:
        _fatal(str(exc))

    defects = [
        {"seg": seg, "block_id": None, "kind": "stale_review_since_audit"} for seg in sorted(stale_segs)
    ]
    missing_heading_defects = compute_missing_heading_defects(source_counter, output_counter, stale_segs)
    defects.extend(missing_heading_defects)
    # #201: a surfaced, present heading whose text carries a leading markdown
    # heading marker -- reuses whichever object the selected scope already
    # built (trusted_drafts or nodestream), never a re-parse. `missing_keys`
    # excludes any key compute_missing_heading_defects already flagged (see
    # collect_heading_shape_defects' own docstring for why the assembled_book
    # scope needs this and the default scope doesn't).
    missing_keys = {(d["seg"], d["block_id"]) for d in missing_heading_defects}
    defects.extend(
        collect_heading_shape_defects(
            source_counter, trusted_drafts=trusted_drafts, nodestream=nodestream, missing_keys=missing_keys
        )
    )

    warnings = collect_undeclared_heading_like_warnings(manifest_blocks, heading_types)

    # --- human-readable report, to stderr -----------------------------------
    print("=" * 70, file=sys.stderr)
    print(f"VALIDATE ASSEMBLED -- scope={v1_scope}", file=sys.stderr)
    print("=" * 70, file=sys.stderr)
    print(
        f"\nHARD ({len(defects)}): {'CLEAN' if not defects else str(len(defects)) + ' DEFECTS'}",
        file=sys.stderr,
    )
    for d in defects:
        print(f"  ✗ [{d['seg']}/{d['block_id']}] {d['kind']}", file=sys.stderr)
    print(f"\nWARN ({len(warnings)}):", file=sys.stderr)
    for w in warnings:
        print(f"  • [{w['seg']}/{w['block_id']}] {w['kind']} (type={w['raw_type']!r})", file=sys.stderr)

    # --- structured stdout: exactly one JSON line ---------------------------
    print(json.dumps({"defects": defects, "warnings": warnings}, ensure_ascii=False))

    sys.exit(1 if defects else 0)


if __name__ == "__main__":
    main()
