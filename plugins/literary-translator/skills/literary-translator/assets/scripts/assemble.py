#!/usr/bin/env python3
"""assemble.py -- W9 Assemble: the deterministic assembler core.

Only invoked when `output.v1_scope == "assembled_book"` (a DETERMINISTIC
script step, never an agent workflow -- no review/fix loop, no ledger
prompts). Reconstructs the whole book, in reading order, from the
THREE-SOURCE JOIN a lone draft can never supply on its own:

  - `manifest.json`   -- STRUCTURE + ORDER (block ids, `order_index`, the
                          segment spine, the footnote anchor/def table, the
                          verse-placeholder inventory, front/back-matter
                          dispositions). `spine[].pos` is a RED HERRING --
                          never order by it; `order_index` is the single
                          global reading-order axis.
  - `segments/{seg}.draft.json`      -- CONTENT (translated text, still
                          carrying `⟦FNREF_N⟧`/`⟦VERSE_...⟧` sentinels
                          byte-for-byte).
  - `segments/segpack_{seg}.json`    -- the per-segment placeholder<->vid
                          join map.
  - `runs/ledger.json`   -- convergence gate: only `status=="converged"`
                          segments are assembled, and only after verifying
                          the on-disk draft's sha1 still matches the
                          fragment's own `reviewed_draft_sha1` (the SAME
                          stale-review-detection guard used elsewhere in
                          this plugin's own W7 audit gate) -- a hand-edit
                          the reviewer never saw must not silently ship.
                          Beyond this per-segment check, W9 also enforces a
                          WHOLE-PROJECT completeness gate (main()'s
                          assert_project_complete): EVERY manifest.segments[]
                          unit -- body segments and translate-decision
                          front/back matter alike -- must be converged, or
                          assembly refuses outright (exit 2, reason
                          project_incomplete) rather than shipping a partial
                          book missing segments.
                          Reads the MERGED `ledger.json` (never raw
                          `runs/ledger.d/*.json` fragments): W9 runs after
                          `ledger_merge.py` has reconciled any cache-key-
                          driven `stale` reclassification, which matters
                          for assembly in a way it does not for that
                          narrower, per-segment stale-review check.

Builds an in-memory NodeStream (the shared, target-neutral IR every output
adapter consumes -- see references/output-target-adapters/README.md for
the full contract) and emits it as two JSON artifacts for tests / the
render+diff acceptance tool:

    {durable_root}/out/.assembled/nodestream.json
    {durable_root}/out/.assembled/anchor_map.json

Then dispatches to whichever adapter `output.target` resolves to (via
`output_resolve.py`), calling its `render(nodestream, canon, profile,
out_dir) -> dict` entry point. `out_dir` is `profile.output.destination`
(already validated at Step 0; Step 0a mkdir -p's its parent).

## Sentinel resolution -- FAIL CLOSED

Two sentinel families appear byte-for-byte inside a draft block's text:
`⟦FNREF_N⟧` and each verse's own `placeholder` string (the EXACT sentinel
baked in at extraction time -- substituted verbatim, never reconstructed
from `vid`). Every sentinel actually found in an assembled block's text
must resolve to exactly one footnote/verse entry; `n` is unique book-wide;
any dangling reference, unrecognized sentinel, or footnote-number
collision across segments is a FATAL exit 1, never silently emitted or
silently dropped. This is deliberately re-verified here (not merely
inherited from a converged segment's own upstream `validate_draft.py`
pass): footnote-number book-wide uniqueness in particular is a
CROSS-segment invariant no single-segment validator could ever catch.

The NodeStream carries sentinels IN TEXT, unresolved -- this script never
substitutes/pre-renders them (token -> target syntax is each adapter's own
job, keeping the two adapters diverging only at render time). A footnote
definition's own text may itself contain a nested sentinel (e.g. an
embedded verse inside a footnote, `verse.store[].context == "footnote"`);
Phase 0 policy is to STRIP nested sentinels from footnote text (never
recursively expand) when building the book-wide `footnotes[]` array.

## Frontback dispositions

`translate` -> an ordinary `kind:"frontback"` segment WITH a draft,
processed identically to a body segment. `omit` -> dropped entirely, no
node, no warning (an already-approved extraction-time choice). `regenerate`
-> NO draft exists; Phase 0 emits a single, clearly-marked placeholder
BlockNode (positioned via its own `manifest.blocks[id].order_index`) plus a
stderr WARNING -- full fresh-matter synthesis is an explicitly later-phase
refinement, kept proportional here.

## What this script deliberately does NOT do

No entity resolution, no morphology/variant generation, no generic
renderer-plugin framework (obsidian/epub/custom are three FIXED presets),
no item-count acceptance gate (the render+diff tool is the real acceptance
gate). Stdlib only; no new dependency.

Usage: python3 assemble.py   (self-anchored, no CLI flags, no cwd
assumption -- matching every other script in this plugin)

Exit 0 = assembled + rendered successfully (one JSON line, `success:true`,
naming what was written). Exit 1 = a fatal defect -- one JSON line,
`success:false`, `error`, and (for the newer, reviewer-hardened checks) a
machine-matchable `reason`: `orphan_footnote_def` / `orphan_verse` (a
converged segment's own draft defines a footnote/verse never referenced by
any sentinel in its blocks), `duplicate_verse_placeholder` (the same verse
placeholder sentinel referenced more than once), `duplicate_footnote_ref`
(the same footnote number referenced more than once -- manifest.footnotes[]
records exactly one anchor per number, so a repeat is a data-model
violation, not a legitimate re-citation), `footnote_def_in_body` (a
malformed manifest lists a footnote-DEFINITION block inside an ordinary
segment's block_ids), `duplicate_order_index` (two blocks share the single
global reading-order axis), `incomplete_segment_in_assembly` (a defensive
backstop: a manifest segment reached nodestream assembly without being
converged -- unreachable once main()'s whole-project completeness gate has
run, kept fail-closed so a caller bypassing that gate can never silently
drop a segment), `malformed_manifest` (manifest.json's segments inventory
is absent, empty, or has a non-object / non-string-`seg` entry --
unassemblable, refused rather than coerced into an empty book), plus the
older, un-reasoned checks (dangling
sentinel, sha1-mismatch guard refusal, unknown output.target, adapter
failure). Exit 2 = a defined, non-fatal PRECONDITION state (mirrors
diff_rendered_output.py's own `reason`-carrying exit-2 convention): one JSON
line, `success:false`, `reason` naming the exact state
(`not_assembled_book_scope` | `no_manifest` | `no_ledger` |
`no_converged_segments` | `project_incomplete` (the whole-project
completeness gate: at least one manifest segment -- including any
translate-decision front/back matter -- is not yet converged, so the book
would be incomplete; assembly refuses a partial project rather than shipping
a book missing segments) | `profile_precondition` | `dependency_precondition`
(a BUILT-IN adapter module halted via sys.exit() during its own
module-level dependency preflight, e.g. a missing-package guard, while
dispatch_adapter() was importing it; mirrors the same reason this script's
own top-of-file validate_draft.py/output_resolve.py imports already use) |
`adapter_import_precondition` (a CUSTOM renderer module halted via
sys.exit() during its own module-level import-time precondition check --
distinct from `dependency_precondition` because a custom renderer is an
open extension point and its halt reason isn't necessarily a missing
dependency)).
"""
import hashlib
import importlib.util
import json
import os
import re
import sys
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import NoReturn

# ---------------------------------------------------------------------------
# Self-anchoring: this script always lives at {durable_root}/scripts/<name>.py.
# It never assumes cwd == durable_root, and never takes a --durable-root flag.
# ---------------------------------------------------------------------------
SCRIPTS_DIR = Path(__file__).resolve().parent
DURABLE_ROOT = SCRIPTS_DIR.parent
SEGMENTS_DIR = DURABLE_ROOT / "segments"
RUNS_DIR = DURABLE_ROOT / "runs"
MANIFEST_PATH = DURABLE_ROOT / "manifest.json"
CANON_PATH = DURABLE_ROOT / "canon.json"
CANON_SENSES_PATH = DURABLE_ROOT / "canon_senses.json"
LEDGER_PATH = RUNS_DIR / "ledger.json"
ASSEMBLED_DIR = DURABLE_ROOT / "out" / ".assembled"


def _dependency_precondition_fatal(error: str) -> NoReturn:
    """One-JSON-line, exit-2 precondition report for an import-time
    dependency failure -- the same `dependency_precondition` reason/shape
    every such failure in this script uses, whether the import itself
    raised (missing sibling file) or the imported module halted via
    sys.exit() during its own module-level preflight (missing PyYAML)."""
    print(json.dumps({"success": False, "reason": "dependency_precondition", "error": error}))
    sys.exit(2)


# validate_draft.py (profile loading) and output_resolve.py (Step 0d
# adapter resolution) live next to this script -- import them directly
# (never reimplemented), matching this plugin's own established
# `import validate_draft as vd` sibling-import pattern.
sys.path.insert(0, str(SCRIPTS_DIR))
try:
    import validate_draft as vd
except ImportError as exc:  # pragma: no cover -- defensive, should be unreachable
    _dependency_precondition_fatal(
        f"could not import validate_draft.py from {SCRIPTS_DIR}: {exc}"
    )
except SystemExit:
    # validate_draft.py's own module-level dependency preflight (its
    # PyYAML import guard) can sys.exit(2) DURING this very import
    # statement -- before main()'s own try/except JSON-envelope machinery
    # ever gets a chance to run. Scoped to just this import (never a
    # broader try block), so this can't swallow an unrelated SystemExit
    # from elsewhere. Re-surface it as the same one-JSON-line contract
    # every other precondition in this script uses, rather than letting a
    # bare stderr-only exit escape.
    _dependency_precondition_fatal(
        f"could not import validate_draft.py from {SCRIPTS_DIR} -- it "
        "halted during its own module-level dependency preflight (see "
        "stderr for the specific reason)"
    )
try:
    import output_resolve
except ImportError as exc:  # pragma: no cover -- defensive, should be unreachable
    _dependency_precondition_fatal(
        f"could not import output_resolve.py from {SCRIPTS_DIR}: {exc}"
    )
except SystemExit:  # pragma: no cover -- defensive: output_resolve.py is
    # currently pure stdlib (json/re/sys/pathlib/typing) and cannot
    # SystemExit at import time today. Mirrors validate_draft's own
    # handler above purely for symmetry, so a future module-level
    # dependency added there stays covered by the same contract -- not
    # load-bearing yet.
    _dependency_precondition_fatal(
        f"could not import output_resolve.py from {SCRIPTS_DIR} -- it "
        "halted during its own module-level dependency preflight (see "
        "stderr for the specific reason)"
    )


class AssembleError(Exception):
    """Raised for any fatal defect (dangling sentinel, sha1-mismatch guard
    refusal, unknown target, adapter failure, ...). Caught centrally by
    main() and reported as one JSON line + exit 1 -- never a bare
    traceback for an expected/actionable condition. `reason`, when given,
    is folded into the JSON payload as a machine-matchable code (e.g.
    `orphan_footnote_def`, `duplicate_order_index`) for the newer,
    reviewer-hardened fail-closed checks; older call sites that don't
    pass one simply omit the field, unchanged."""

    def __init__(self, message: str, reason: "str | None" = None):
        super().__init__(message)
        self.reason = reason


class AssemblePrecondition(Exception):
    """A defined, non-fatal BOOTSTRAP state -- distinct from AssembleError
    -- exit 2, mirroring diff_rendered_output.py's own `reason`-carrying
    exit-2 convention (see the shared build contract, section 2)."""

    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason


# Sentinel patterns -- format-neutral, matching validate_draft.py's own
# convention: ⟦FNREF_N⟧ for footnote anchors, any other ⟦...⟧-bracketed
# token for a verse placeholder (segpack.schema.json's `placeholder` field
# is free-form, not guaranteed to follow any one internal-naming
# convention).
ANY_SENTINEL_RE = re.compile(r"⟦[^⟧]+⟧")
FNREF_RE = re.compile(r"^⟦FNREF_(\d+)⟧$")


def draft_path(seg: str) -> Path:
    return SEGMENTS_DIR / f"{seg}.draft.json"


def segpack_path(seg: str) -> Path:
    return SEGMENTS_DIR / f"segpack_{seg}.json"


def draft_content_sha1(path: Path) -> str:
    """sha1 of a draft's CONTENT, with the 'dispatch_token' metadata field
    deliberately EXCLUDED -- see draft_sha1.py's own module docstring for why.

    Must match, byte for byte, draft_sha1.py's and ledger_update.py's own
    draft_content_sha1() -- both parse the draft as JSON, drop
    'dispatch_token' if present, and re-serialize the remainder via
    identical sorted-key canonical JSON before hashing. This is compared
    directly against reviewed_draft_sha1, which ledger_update.py writes via
    this exact algorithm -- NOT a raw-bytes hash of the on-disk file.

    Raises OSError (unreadable file), json.JSONDecodeError (not valid
    JSON), or ValueError (valid JSON but not an object) on failure --
    callers handle all three.
    """
    raw = path.read_text(encoding="utf-8")
    doc = json.loads(raw)
    if not isinstance(doc, dict):
        raise ValueError(f"draft at {path} must be a JSON object, got {type(doc).__name__}")
    projected = {k: v for k, v in doc.items() if k != "dispatch_token"}
    canonical = json.dumps(
        projected, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha1(canonical).hexdigest()


def read_json(path: Path, label: str):
    if not path.is_file():
        raise AssembleError(f"{label} not found at {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AssembleError(f"{label} at {path} is not valid JSON: {exc}")


def _write_json_atomically(path: Path, data) -> None:
    """Writes `data` as JSON to `path` atomically (mkstemp + fsync +
    os.replace), similar to ledger_update.py's own
    write_fragment_atomically() -- so an adapter failure or interruption
    mid-write can never leave a truncated/half-updated
    nodestream.json/anchor_map.json artifact on disk.

    `.assembled/` is a preserved dotfile (render_obsidian's own
    clean-render never recurses into it), so a PREDICTABLE tmp name (the
    prior `path.with_name(f"{path.name}.tmp.{os.getpid()}")` + a plain
    `open(tmp_path, "w")`) could survive across renders and be
    pre-planted as a symlink to an external file -- a plain open() for
    write FOLLOWS that symlink and clobbers the external target (the
    same class of bug review round 4 fixed in render_obsidian's own
    marker write). `tempfile.mkstemp` closes this: it creates the temp
    file with O_CREAT|O_EXCL under a securely-randomized, unpredictable
    name (refusing to follow/reuse anything already planted there) and a
    NON-dot prefix ("lt-assembled-tmp-") so a crash-leftover from an
    interrupted prior run is swept by ordinary housekeeping rather than
    surviving forever like a dotfile would. `os.replace` itself always
    replaces whatever directory entry sits at the FINAL destination
    (symlink or regular file) rather than following it, so `path` is
    already safe once the write goes through a real, mkstemp'd tmp file
    first. The cleanup below is broadened to `BaseException` (not just
    OSError) so a tmp file is never left behind even if fsync/replace is
    itself interrupted, matching render_obsidian's `_stamp_vault_marker`
    -- but only a genuine OSError is wrapped into AssembleError; anything
    else (KeyboardInterrupt, SystemExit, ...) is cleaned up and
    re-raised bare, exactly as Python convention expects."""
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix="lt-assembled-tmp-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)
    except BaseException as exc:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        if isinstance(exc, OSError):
            raise AssembleError(f"failed writing {path} atomically: {exc}") from exc
        raise


def _profile_get(profile: dict, dotted_path: str):
    cur = profile
    parts = dotted_path.split(".")
    for i, key in enumerate(parts):
        if not isinstance(cur, dict) or key not in cur:
            raise AssembleError(
                f"profile.yml is missing required field '{'.'.join(parts[: i + 1])}'"
            )
        cur = cur[key]
    return cur


# ---------------------------------------------------------------------------
# Ledger convergence + sha1 gate.
# ---------------------------------------------------------------------------


def load_converged_segments(ledger: dict) -> dict:
    """Returns {seg: record} for every runs/ledger.json segments{} entry
    whose status=="converged" AND whose on-disk draft sha1 currently
    matches the record's own reviewed_draft_sha1 -- the same
    stale-review-detection guard this plugin's own W7 audit gate uses. A
    mismatch is a FATAL guard refusal (exit 1, via AssembleError), never a
    silent skip -- "a hand-edit the reviewer never saw must not silently
    ship" is the whole point of this gate."""
    segments = ledger.get("segments") if isinstance(ledger, dict) else None
    if not isinstance(segments, dict):
        raise AssembleError("runs/ledger.json is missing its 'segments' object")

    converged = {}
    for seg, record in segments.items():
        if not isinstance(record, dict) or record.get("status") != "converged":
            continue
        expected_sha1 = record.get("reviewed_draft_sha1")
        if not expected_sha1:
            raise AssembleError(
                f"runs/ledger.json segment {seg!r} has status=converged but "
                f"no reviewed_draft_sha1 recorded -- cannot confirm the "
                f"reviewer actually saw the current draft"
            )
        dp = draft_path(seg)
        if not dp.is_file():
            raise AssembleError(
                f"runs/ledger.json segment {seg!r} has status=converged but "
                f"its draft is missing on disk at {dp}"
            )
        try:
            actual_sha1 = draft_content_sha1(dp)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            raise AssembleError(
                f"segment {seg!r} draft at {dp} is unreadable/corrupt -- "
                f"cannot confirm the reviewer saw it; re-review before "
                f"assembling ({exc})"
            )
        if actual_sha1 != expected_sha1:
            raise AssembleError(
                f"segment {seg!r} draft has changed since review (current "
                f"sha1={actual_sha1}, reviewed_draft_sha1={expected_sha1}) -- "
                f"a hand-edit the reviewer never saw must not be assembled; "
                f"re-review (or restore the reviewed draft) before assembling"
            )
        converged[seg] = record
    return converged


def assert_project_complete(manifest: dict, converged: dict) -> None:
    """W9's whole-project completeness gate (SKILL.md "W9 Assemble";
    references/assembly-and-output.md Path 2). Refuse to assemble unless
    EVERY manifest.segments[] unit is converged. manifest.segments[] is the
    single required-unit population -- it already includes translate-decision
    FRONTBACK:{id} units (each such entry's `seg` IS "FRONTBACK:{id}"), which
    share the one seg-id namespace with body segments and are ledgered
    identically, so a plain membership test over manifest.segments[] covers
    front/back matter too. `converged` is exactly the units whose materialized
    ledger status is "converged" with an on-disk draft sha1 still matching
    reviewed_draft_sha1 (see load_converged_segments); ledger_merge.py has
    already collapsed any cache-key mismatch to status "stale", so "converged
    in the materialized ledger" is equivalent to final_audit.py's `reusable`
    classification. This re-derives the SAME predicate as final_audit.py's
    `final-audit-summary.project_complete: true` directly from manifest +
    ledger -- final_audit.py only prints that summary and never persists it,
    and W9 deliberately does NOT shell out to it (advisory-only, gated
    nothing, up to 300s -- a proportionality guardrail). Assembling a book
    from a not-fully-converged project is refused here (exit 2), never
    silently attempted over a partial set. A manifest whose `segments`
    inventory is absent, empty, or holds a non-object / non-string-`seg`
    entry is itself rejected here as `malformed_manifest` (exit 1) rather
    than coerced into an empty required set (which would otherwise fail
    open into an empty "successful" book)."""
    segments = manifest.get("segments")
    if not isinstance(segments, list) or not segments:
        raise AssembleError(
            "manifest.json 'segments' must be a non-empty array -- refusing "
            "to assemble a book from a manifest with no segment inventory "
            "(a converged ledger alongside an empty/absent manifest segment "
            "list is a corrupt or inconsistent project state)",
            reason="malformed_manifest",
        )
    missing = []
    for seg_entry in segments:
        seg = seg_entry.get("seg") if isinstance(seg_entry, dict) else None
        if not isinstance(seg, str):
            raise AssembleError(
                f"manifest.json 'segments' contains a malformed entry -- each "
                f"must be an object with a string 'seg' id: {seg_entry!r}",
                reason="malformed_manifest",
            )
        if seg not in converged:
            missing.append(seg)
    if missing:
        raise AssemblePrecondition(
            "project_incomplete",
            f"refusing to assemble: {len(missing)} manifest segment(s) are "
            f"not converged in runs/ledger.json "
            f"({', '.join(sorted(missing))}) -- assembled_book requires the "
            f"whole-project completeness gate "
            f"(final-audit-summary.project_complete: true): every segment, "
            f"including translate-decision front/back matter, must converge "
            f"before the book can be assembled. Run the pipeline to "
            f"convergence for the remaining segment(s), then re-run assembly.",
        )


# ---------------------------------------------------------------------------
# Sentinel scan + FAIL-CLOSED bijection check for one block's text.
# ---------------------------------------------------------------------------


def _scan_footnote_def_embedded_verses(
    text_for_n,
    seg,
    block_id,
    n,
    footnote_entries_by_n,
    draft_footnotes,
    book_footnotes,
    placeholder_set,
    placeholder_to_vid,
    draft_verses,
    book_seen_placeholders,
):
    """Shared "a footnote's own def text embeds a verse" scan, extracted from
    the two footnote-embeds-verse branches (`_scan_and_validate_sentinels`'s
    ⟦FNREF_N⟧ branch and `_scan_verse_content_fnrefs`) that previously carried
    it as a byte-identical "lockstep residual" duplication -- now ONE helper
    both call so they can't drift.

    Given footnote `n`'s own definition text `text_for_n`, find the verse
    placeholders embedded in it (`verse.store[].context == "footnote"`), mark
    each REFERENCED (book-wide `duplicate_verse_placeholder` dedup via
    `book_seen_placeholders`, plus the fail-closed "the embedded vid must exist
    in draft.verses" guard), and then RECURSE into each embedded verse's OWN
    translated content: that content may itself cite footnotes (whose defs may
    embed further verses...), an arbitrarily deep chain the single-level scan
    used to miss (#118 item 2). Everything discovered THROUGH a def-embedded
    verse is REFERENCED-ONLY -- the embedded verse is stripped-not-rendered, so
    neither it nor any footnote reached via its content may ever surface in a
    node's `fnrefs`/`verses`; they only satisfy the orphan checks. Returns
    `(embedded_vids, nested_ns)`:
      - `embedded_vids`: every def-embedded verse vid marked referenced across
        the whole recursion (feeds the caller's `referenced_vids`; already
        added to `book_seen_placeholders`).
      - `nested_ns`: every footnote number discovered by recursing INTO a
        def-embedded verse's own content -- referenced-only, funnelled into
        `seg_referenced_ns` (+ `book_footnotes`, set by the recursive
        `_scan_verse_content_fnrefs` call), NEVER any node's `fnrefs`.

    `book_seen_placeholders.add(...)` happens BEFORE the recursive call (bounds
    the recursion against a pathological verse<->footnote citation cycle in
    source data: a repeat placeholder is caught as `duplicate_verse_placeholder`
    rather than looping forever)."""
    embedded_vids = set()
    nested_ns = set()
    for embedded_token in ANY_SENTINEL_RE.findall(text_for_n):
        if FNREF_RE.fullmatch(embedded_token):
            continue
        if embedded_token not in placeholder_set:
            continue
        embedded_vid = placeholder_to_vid[embedded_token]
        if embedded_vid not in draft_verses:
            raise AssembleError(
                f"[{seg}/{block_id}] footnote n={n}'s own definition text "
                f"embeds verse placeholder {embedded_token!r} "
                f"(vid={embedded_vid}), but draft.verses has no entry for it"
            )
        if embedded_token in book_seen_placeholders:
            raise AssembleError(
                f"[{seg}/{block_id}] verse placeholder {embedded_token!r} "
                f"(vid={embedded_vid}), embedded in footnote n={n}'s own "
                f"definition text, is referenced more than once across the book",
                reason="duplicate_verse_placeholder",
            )
        book_seen_placeholders.add(embedded_token)
        embedded_vids.add(embedded_vid)
        inner_ns, inner_vids, inner_nested = _scan_verse_content_fnrefs(
            draft_verses[embedded_vid],
            seg,
            block_id,
            embedded_vid,
            footnote_entries_by_n,
            draft_footnotes,
            book_footnotes,
            placeholder_set,
            placeholder_to_vid,
            draft_verses,
            book_seen_placeholders,
        )
        embedded_vids.update(inner_vids)
        nested_ns.update(inner_ns)
        nested_ns.update(inner_nested)
    return embedded_vids, nested_ns


def _scan_and_validate_sentinels(
    text,
    seg,
    block_id,
    placeholder_set,
    placeholder_to_vid,
    vid_to_parent,
    draft_verses,
    footnote_entries_by_n,
    draft_footnotes,
    book_footnotes,
    book_seen_placeholders,
):
    """Returns (fnrefs, referenced_vids, nested_ns): `fnrefs` is the sorted,
    distinct list of footnote numbers referenced by `text` (these feed BOTH
    this block node's own `fnrefs` and seg_referenced_ns); `referenced_vids` is
    the set of verse vids referenced by `text` -- INCLUDING any verse embedded
    inside a referenced footnote's own definition text (see below), even
    though that embedded verse's placeholder is uniformly stripped, never
    resolved into any node's own `verses` list; `nested_ns` is the set of
    footnote numbers discovered by recursing INTO a def-embedded verse's own
    translated content -- REFERENCED-ONLY, feeding seg_referenced_ns (+
    book_footnotes) but NEVER this node's `fnrefs`. The caller accumulates all
    three per-segment, to drive the orphan-definition check below. Raises
    AssembleError (fatal) on any dangling FNREF, unknown verse placeholder,
    misplaced verse placeholder (found in a different block than segpack
    records as its parent_block), malformed sentinel bracket, a repeated
    footnote reference, or a repeated verse placeholder.

    Duplicate policy (data-model-derived, not an arbitrary choice):
    `manifest.footnotes[]` records exactly ONE `anchor_block`/`anchor_seg`
    per footnote number (enforced by build_nodestream()'s own upfront
    n-uniqueness check) -- the data model has no notion of "cited more than
    once," so a repeat ⟦FNREF_N⟧ anywhere is `duplicate_footnote_ref`, not
    a legitimate re-citation. Likewise each verse `placeholder` string bakes
    in an 8-hex uniqueness suffix at generation time specifically so it can
    never collide -- a repeat is `duplicate_verse_placeholder`, always
    fatal, book-wide (keyed by the placeholder STRING itself, not the bare
    `vid`, since `vid` is only guaranteed unique WITHIN one segment's own
    segpack -- two different segments legitimately reusing a short vid like
    "V001" is normal and must never be confused with a genuine dup)."""
    text = text or ""
    tokens = ANY_SENTINEL_RE.findall(text)
    open_count = text.count("⟦")
    close_count = text.count("⟧")
    if open_count != len(tokens) or close_count != len(tokens):
        raise AssembleError(
            f"[{seg}/{block_id}] malformed sentinel bracket(s) in block text "
            f"-- mismatched ⟦/⟧ count (found {open_count} "
            f"'⟦' and {close_count} '⟧' for {len(tokens)} "
            f"matched sentinel(s))"
        )

    fnrefs = set()
    referenced_vids = set()
    nested_ns = set()
    for token in tokens:
        m = FNREF_RE.fullmatch(token)
        if m:
            n = int(m.group(1))
            fe = footnote_entries_by_n.get(n)
            if fe is None:
                raise AssembleError(
                    f"[{seg}/{block_id}] dangling footnote reference {token!r}: "
                    f"no manifest.footnotes[] entry for n={n}"
                )
            if fe.get("anchor_seg") != seg:
                raise AssembleError(
                    f"[{seg}/{block_id}] footnote reference {token!r} (n={n}) "
                    f"found in segment {seg!r}, but manifest.footnotes[] "
                    f"records its anchor_seg as {fe.get('anchor_seg')!r} -- "
                    f"data inconsistency"
                )
            text_for_n = (draft_footnotes or {}).get(str(n))
            if text_for_n is None:
                raise AssembleError(
                    f"[{seg}/{block_id}] dangling footnote reference {token!r}: "
                    f"draft.footnotes has no entry for n={n}"
                )
            if n in book_footnotes:
                raise AssembleError(
                    f"[{seg}/{block_id}] footnote reference {token!r} (n={n}) "
                    f"is referenced more than once -- manifest.footnotes[] "
                    f"records a SINGLE anchor per footnote number, so a "
                    f"repeat anywhere in the book is a data-model violation",
                    reason="duplicate_footnote_ref",
                )
            # Phase 0 policy: strip nested sentinels from footnote DEF text
            # (never recursively expand) -- a footnote may itself embed a
            # verse (verse.store[].context == "footnote"). Register the
            # stripped def text and count n BEFORE the embedded-verse scan
            # below, which RECURSES via the shared helper: the
            # `n in book_footnotes` guard runs on entry, so a nested duplicate
            # citation of THIS same n (from inside a def-embedded verse's own
            # content) must see it already registered -- else it would slip
            # that guard and fail later as a confusing
            # duplicate_verse_placeholder instead of the correct
            # duplicate_footnote_ref.
            book_footnotes[n] = ANY_SENTINEL_RE.sub("", text_for_n)
            fnrefs.add(n)
            # An embedded verse in this footnote's own def text (segpack.py
            # attributes it to the SAME segment that anchors this footnote, so
            # `placeholder_set`/`placeholder_to_vid` already know about it) is
            # marked REFERENCED (so the orphan-verse check doesn't false-fatal
            # it) and recursed into for further nested footnotes -- all
            # referenced-only (stripped-not-rendered, never resolved into any
            # node's `verses`). Its nested_ns feed seg_referenced_ns only,
            # NEVER this block node's own fnrefs.
            emb_vids, emb_nested = _scan_footnote_def_embedded_verses(
                text_for_n, seg, block_id, n,
                footnote_entries_by_n, draft_footnotes, book_footnotes,
                placeholder_set, placeholder_to_vid, draft_verses,
                book_seen_placeholders,
            )
            referenced_vids.update(emb_vids)
            nested_ns.update(emb_nested)
            continue

        if token in placeholder_set:
            vid = placeholder_to_vid[token]
            if vid not in draft_verses:
                raise AssembleError(
                    f"[{seg}/{block_id}] dangling verse placeholder {token!r} "
                    f"(vid={vid}): draft.verses has no entry for it"
                )
            if token in book_seen_placeholders:
                raise AssembleError(
                    f"[{seg}/{block_id}] verse placeholder {token!r} "
                    f"(vid={vid}) is referenced more than once across the "
                    f"book -- each verse placeholder is a unique, one-time "
                    f"sentinel",
                    reason="duplicate_verse_placeholder",
                )
            book_seen_placeholders.add(token)
            claimed_parent = vid_to_parent.get(vid)
            if claimed_parent != block_id:
                raise AssembleError(
                    f"[{seg}/{block_id}] verse placeholder {token!r} "
                    f"(vid={vid}) found here, but segpack records its "
                    f"parent_block as {claimed_parent!r} -- misplaced verse"
                )
            referenced_vids.add(vid)
            continue

        raise AssembleError(
            f"[{seg}/{block_id}] unrecognized sentinel {token!r} -- matches "
            f"neither a known ⟦FNREF_N⟧ footnote nor a known verse "
            f"placeholder for this segment"
        )

    return sorted(fnrefs), referenced_vids, nested_ns


def _scan_verse_content_fnrefs(content, seg, block_id, vid,
                               footnote_entries_by_n, draft_footnotes, book_footnotes,
                               placeholder_set, placeholder_to_vid, draft_verses,
                               book_seen_placeholders):
    """FNREFs cited INSIDE a verse's translated content (rendered/literal_gloss)
    -- validated exactly like the block-text FNREF branch (dangling / anchor_seg
    / draft-def / cross-ref duplicate), then registered into book_footnotes so
    nodestream.footnotes carries the def. Per-field distinct-n dedup below; per-n
    it delegates the "does this footnote's OWN def embed a verse" scan to the
    SHARED helper `_scan_footnote_def_embedded_verses` (the same one the block
    branch now uses -- the old byte-identical "lockstep residual" duplication is
    gone), which marks that inner vid referenced (else orphan_verse false-fatals
    it) AND recurses into the inner verse's own content for further nested
    footnotes (#118 item 2). Returns (ns, referenced_vids, nested_ns): `ns` =
    distinct footnote numbers cited directly in THIS verse's content;
    `referenced_vids` = def-embedded verse vids marked referenced;
    `nested_ns` = footnote numbers discovered by recursing into a def-embedded
    verse's content -- referenced-only, funnelled to seg_referenced_ns (never a
    node's `fnrefs`, since that inner verse is stripped-not-rendered)."""
    content = content or {}
    # Scan the two alternate representations SEPARATELY (codex r5 finding 4):
    # dedup ACROSS fields (a footnote naturally in BOTH the rhymed `rendered` and
    # the `literal_gloss` of full_rhymed_plus_literal is ONE citation), but RETAIN
    # within-field duplicate detection (the same footnote cited TWICE inside one
    # field is a genuine `duplicate_footnote_ref`, matching assemble's block
    # "repeat anywhere" invariant). Distinct-n = union of each field's set.
    ns = set()
    for field in ("rendered", "literal_gloss"):
        field_text = content.get(field) or ""
        tokens = ANY_SENTINEL_RE.findall(field_text)
        # Mirror the block branch's bracket-balance guard (:477-485) -- an
        # unclosed/malformed sentinel in verse content must fail closed the
        # same way, not silently pass through unscanned.
        open_count = field_text.count("⟦")
        close_count = field_text.count("⟧")
        if open_count != len(tokens) or close_count != len(tokens):
            raise AssembleError(
                f"[{seg}/{block_id}] malformed sentinel bracket(s) in verse "
                f"{vid}'s {field} text -- mismatched ⟦/⟧ count (found "
                f"{open_count} '⟦' and {close_count} '⟧' for {len(tokens)} "
                f"matched sentinel(s))"
            )
        field_counts = {}
        for tok in tokens:
            m = FNREF_RE.fullmatch(tok)
            if m is None:
                # Mirror the block branch's terminal else-raise (:594) -- a
                # verse's own translated content may only ever cite
                # ⟦FNREF_n⟧ (the embedded-verse-in-def case is a FOOTNOTE's
                # own def text, never a verse's own rendered/literal_gloss;
                # there is no such thing as a verse embedding another verse).
                # Any other sentinel here -- a stray ⟦VERSE_...⟧ placeholder,
                # a typo, garbage -- must fail closed, never leak unresolved.
                raise AssembleError(
                    f"[{seg}/{block_id}] verse {vid}'s {field} text contains "
                    f"unrecognized sentinel {tok!r} -- matches neither a "
                    f"known ⟦FNREF_N⟧ footnote nor any sentinel a verse's own "
                    f"translated content may legitimately carry"
                )
            n = int(m.group(1))
            field_counts[n] = field_counts.get(n, 0) + 1
        for n, c in field_counts.items():
            if c > 1:
                raise AssembleError(f"[{seg}/{block_id}] footnote n={n} (cited in verse {vid}) "
                                    f"is referenced {c}x within one field", reason="duplicate_footnote_ref")
            ns.add(n)
    referenced_vids = set()
    nested_ns = set()
    for n in sorted(ns):
        fe = footnote_entries_by_n.get(n)
        if fe is None:
            raise AssembleError(f"[{seg}/{block_id}] verse {vid} cites ⟦FNREF_{n}⟧ "
                                f"but no manifest.footnotes[] entry for n={n}")
        if fe.get("anchor_seg") != seg:
            raise AssembleError(f"[{seg}/{block_id}] verse {vid} cites ⟦FNREF_{n}⟧ but its "
                                f"anchor_seg is {fe.get('anchor_seg')!r} -- data inconsistency")
        text_for_n = (draft_footnotes or {}).get(str(n))
        if text_for_n is None:
            raise AssembleError(f"[{seg}/{block_id}] verse {vid} cites ⟦FNREF_{n}⟧ "
                                f"but draft.footnotes has no entry for n={n}")
        if n in book_footnotes:
            raise AssembleError(f"[{seg}/{block_id}] footnote n={n} (cited in verse {vid}) "
                                f"is referenced more than once", reason="duplicate_footnote_ref")
        # Phase 0 policy: strip nested sentinels from the def text. Register it
        # (book_footnotes[n]) BEFORE the shared embedded-verse scan below (which
        # RECURSES): the `n in book_footnotes` guard runs on entry, so a nested
        # duplicate citation of THIS same n (from inside a def-embedded verse's
        # content) must see it already registered -- else it fails later as a
        # confusing duplicate_verse_placeholder instead of duplicate_footnote_ref.
        book_footnotes[n] = ANY_SENTINEL_RE.sub("", text_for_n)
        # A verse embedded in footnote n's OWN def text (context=="footnote"),
        # attributed by segpack to THIS segment, is marked referenced (else
        # orphan_verse false-fatals it) and recursed into for further nested
        # footnotes -- all referenced-only (stripped-not-rendered).
        emb_vids, emb_nested = _scan_footnote_def_embedded_verses(
            text_for_n, seg, block_id, n,
            footnote_entries_by_n, draft_footnotes, book_footnotes,
            placeholder_set, placeholder_to_vid, draft_verses,
            book_seen_placeholders,
        )
        referenced_vids.update(emb_vids)
        nested_ns.update(emb_nested)
    return ns, referenced_vids, nested_ns


# ---------------------------------------------------------------------------
# Kind classification (contract section 4, point 3).
# ---------------------------------------------------------------------------


def _classify_kind(raw_type: str, claims: list, verse_store_by_vid: dict,
                    heading_types: frozenset = frozenset()) -> str:
    # Declared-heading precedence (#210): a manifest-declared heading type
    # wins even over a block-mount verse claim -- mirrors "HEAD" always
    # winning today. Checked ABOVE the is_block_mount test below.
    if raw_type == "HEAD" or raw_type in heading_types:
        return "heading"
    is_block_mount = any(
        verse_store_by_vid.get(c["vid"], {}).get("mount") == "block" for c in claims
    )
    if is_block_mount:
        return "verse"
    return "prose"


def _fnref_numbers_in(text) -> set:
    """Set of footnote numbers whose ⟦FNREF_n⟧ sentinel appears anywhere in
    `text` (order-independent). Reuses ANY_SENTINEL_RE + the anchored FNREF_RE
    rather than introducing a second, drift-prone non-anchored FNREF pattern."""
    out = set()
    for tok in ANY_SENTINEL_RE.findall(text or ""):
        m = FNREF_RE.fullmatch(tok)
        if m:
            out.add(int(m.group(1)))
    return out


def _footnote_verse_cited_in_segment(n, draft_verses, verse_store_by_vid) -> bool:
    """True iff footnote number `n` is cited by SOME verse in this segment's
    `draft_verses`, per the manifest's MODE-INDEPENDENT verse.store ground
    truth -- either the verse's recorded `fnrefs[]` OR a direct ⟦FNREF_n⟧ scan
    of its `plain_text` (mirroring the union segpack.py uses to decide a
    footnote is verse-cited, so a stale manifest whose sentinel survives in
    plain_text but is missing from fnrefs[] -- the exact case segpack.py itself
    WARNs about -- cannot silently re-open the skip-mode deadlock through a gap
    in this exemption's own condition). Defensive `.get()` throughout (never
    bracket-index), matching the `_classify_kind` precedent -- a manifest with
    no verse.store entry for a draft vid must contribute nothing, never
    KeyError."""
    for vid in draft_verses:
        store = verse_store_by_vid.get(vid, {})
        if n in (store.get("fnrefs") or []):
            return True
        if n in _fnref_numbers_in(store.get("plain_text") or ""):
            return True
    return False


# ---------------------------------------------------------------------------
# NodeStream construction -- the core reconstruction algorithm.
# ---------------------------------------------------------------------------


def build_nodestream(profile: dict, manifest: dict, converged: dict) -> tuple:
    """Returns (nodestream, anchor_map), both plain dicts matching the
    shared build contract's EXACT shapes (section 5). Pure with respect to
    the filesystem beyond reading each converged segment's own draft/
    segpack files (via the self-anchored draft_path()/segpack_path())."""
    manifest_segments = manifest.get("segments") or []
    manifest_blocks = manifest.get("blocks") or {}
    manifest_footnotes = manifest.get("footnotes") or []
    manifest_frontback = manifest.get("frontback") or []
    manifest_verse_store = (manifest.get("verse") or {}).get("store") or []
    # #210: manifest-declared block types that classify as headings in
    # addition to the always-heading built-in "HEAD" (empty by default --
    # byte-identical to pre-#210 behavior when the manifest omits it).
    heading_types = frozenset(manifest.get("heading_types") or ())

    footnote_entries_by_n = {}
    for fe in manifest_footnotes:
        n = fe.get("n")
        if n in footnote_entries_by_n:
            raise AssembleError(
                f"manifest.json footnotes[] has a duplicate n={n} -- "
                f"book-wide footnote numbers must be unique"
            )
        footnote_entries_by_n[n] = fe

    # Footnote-DEFINITION block ids -- these must never appear inside any
    # segment's own block_ids[] (they carry the FN:{N} def text, surfaced
    # only via the book-wide footnotes[] array, never rendered inline).
    fn_def_block_ids = {fe.get("def_block") for fe in manifest_footnotes if fe.get("def_block")}

    # NOTE: this is manifest.verse.store's own vid space, GLOBALLY unique
    # book-wide (manifest.schema.json's own "this verse's unique key"
    # description) -- a different, stronger guarantee than segpack's own
    # per-segment vid, which is unique only WITHIN one segment (that
    # weaker, segment-local guarantee is exactly why the cross-block
    # duplicate check elsewhere in this function keys on the placeholder
    # STRING instead of the bare vid). The duplicate check below enforces
    # the manifest-level invariant and does not conflict with segpack
    # legitimately reusing a short vid like "V001" across two segments.
    verse_store_by_vid = {}
    for v in manifest_verse_store:
        vid = v.get("vid")
        if vid in verse_store_by_vid:
            raise AssembleError(f"manifest.json verse.store has a duplicate vid={vid!r}")
        verse_store_by_vid[vid] = v

    # order_index is THE single global reading-order axis (section 3/14) --
    # two blocks sharing one is an ambiguous axis, always a manifest defect
    # (gaps in the sequence are fine; only collisions are fatal).
    order_index_owners = defaultdict(list)
    for bid, mb in manifest_blocks.items():
        if isinstance(mb, dict) and "order_index" in mb:
            order_index_owners[mb["order_index"]].append(bid)
    for oi, owners in order_index_owners.items():
        if len(owners) > 1:
            raise AssembleError(
                f"manifest.json has {len(owners)} blocks sharing "
                f"order_index={oi}: {sorted(owners)} -- order_index is the "
                f"single global reading-order axis and must be unique per "
                f"block",
                reason="duplicate_order_index",
            )

    book_footnotes = {}  # n -> stripped text
    book_seen_placeholders = set()  # every verse placeholder STRING seen so far, book-wide
    all_nodes = []
    seg_min_order_index = {}

    # Fix B (#118 item 1): under verse_policy.mode: skip a verse's content is
    # voided, so a footnote whose sole citation site is that content is
    # legitimately unresolvable-by-design -- the orphan-definition check below
    # exempts it rather than fatally raising orphan_footnote_def. Read once here
    # (mode-independent, book-wide), matching the `meta` field's own read below.
    verse_skip_mode = _profile_get(profile, "verse_policy.mode") == "skip"

    # -- ordinary segments (body + translate-decision frontback) --------
    for seg_entry in manifest_segments:
        seg = seg_entry.get("seg")
        if seg not in converged:
            # Unreachable in the normal flow: main()'s assert_project_complete
            # gate already refuses any not-fully-converged project before
            # build_nodestream runs. Kept as a defensive fail-closed backstop
            # so a caller that ever bypasses that gate can never silently drop
            # a segment from the assembled book (the old behavior this
            # replaces -- no contract section blesses a partial book).
            raise AssembleError(
                f"internal invariant violated: manifest segment {seg!r} is "
                f"not converged but reached nodestream assembly -- the "
                f"whole-project completeness gate must run before assembly",
                reason="incomplete_segment_in_assembly",
            )

        draft = read_json(draft_path(seg), f"draft {seg}")
        segpack = read_json(segpack_path(seg), f"segpack {seg}")

        segpack_verses = segpack.get("verses") or []
        verses_by_parent = defaultdict(list)
        placeholder_to_vid = {}
        vid_to_parent = {}
        placeholder_set = set()
        for v in segpack_verses:
            verses_by_parent[v["parent_block"]].append(v)
            placeholder_to_vid[v["placeholder"]] = v["vid"]
            vid_to_parent[v["vid"]] = v["parent_block"]
            placeholder_set.add(v["placeholder"])

        draft_blocks = draft.get("blocks") or {}
        draft_footnotes = draft.get("footnotes") or {}
        draft_verses = draft.get("verses") or {}

        # Accumulated across this segment's own blocks -- drives the
        # orphan-definition check once the block loop below finishes:
        # every draft.footnotes[]/draft.verses[] entry this segment defines
        # must be referenced by at least one sentinel somewhere in it.
        seg_referenced_ns = set()
        seg_referenced_vids = set()

        block_ids = seg_entry.get("block_ids") or []
        seg_order_indices = []
        for bid in block_ids:
            mb = manifest_blocks.get(bid)
            if mb is None:
                raise AssembleError(
                    f"segment {seg!r} names block_id {bid!r} in "
                    f"manifest.segments[], but no such block exists in "
                    f"manifest.blocks{{}}"
                )
            if bid in fn_def_block_ids:
                raise AssembleError(
                    f"segment {seg!r} names block_id {bid!r} in its own "
                    f"block_ids[], but manifest.footnotes[] records "
                    f"{bid!r} as a footnote DEFINITION block (def_block) -- "
                    f"footnote definitions must never be listed as ordinary "
                    f"body content",
                    reason="footnote_def_in_body",
                )
            text = draft_blocks.get(bid)
            if text is None:
                raise AssembleError(
                    f"[{seg}] draft is missing block {bid!r} -- should be "
                    f"impossible post-convergence (validate_draft.py's own "
                    f"coverage check should have caught this upstream)"
                )
            try:
                order_index = mb["order_index"]
                raw_type = mb["type"]
            except KeyError as exc:
                raise AssembleError(
                    f"manifest.blocks[{bid!r}] is missing required field "
                    f"{exc.args[0]!r}"
                )
            seg_order_indices.append(order_index)
            medium = "html" if mb.get("source_html") is not None else "plain"

            claims = verses_by_parent.get(bid, [])
            kind = _classify_kind(raw_type, claims, verse_store_by_vid, heading_types)

            fnrefs, referenced_vids, block_nested_ns = _scan_and_validate_sentinels(
                text,
                seg,
                bid,
                placeholder_set,
                placeholder_to_vid,
                vid_to_parent,
                draft_verses,
                footnote_entries_by_n,
                draft_footnotes,
                book_footnotes,
                book_seen_placeholders,
            )
            seg_referenced_ns.update(fnrefs)
            # nested_ns (footnotes reached only THROUGH a def-embedded verse's
            # content) are referenced-only -- they satisfy the orphan check but
            # must NEVER join this block node's own fnrefs below.
            seg_referenced_ns.update(block_nested_ns)
            seg_referenced_vids.update(referenced_vids)

            verses_field = []
            verse_fnrefs = set()
            for c in claims:
                vid = c["vid"]
                if vid not in draft_verses:
                    raise AssembleError(
                        f"[{seg}/{bid}] dangling verse: segpack claims "
                        f"vid={vid!r} parented to this block, but draft.verses "
                        f"has no entry for it"
                    )
                # A verse's own translated content (rendered/literal_gloss) may
                # itself carry ⟦FNREF_n⟧ -- a footnote cited from inside the
                # poem, not the surrounding block text -- which the block-text
                # scan above never sees (it only tokenizes `text`). Scan it here
                # so the footnote is registered into book_footnotes/node.fnrefs
                # (else render leaks a raw sentinel with no [^n]: def) and its
                # orphan-definition/orphan-verse checks below don't false-fatal.
                v_ns, v_ref_vids, v_nested_ns = _scan_verse_content_fnrefs(
                    draft_verses[vid], seg, bid, vid,
                    footnote_entries_by_n, draft_footnotes, book_footnotes,
                    placeholder_set, placeholder_to_vid, draft_verses,
                    book_seen_placeholders,
                )
                verse_fnrefs.update(v_ns)
                seg_referenced_vids.update(v_ref_vids)
                # Referenced-only: nested footnotes reached through a
                # def-embedded verse's content satisfy the orphan check but must
                # never join this rendered verse's carrier node fnrefs.
                seg_referenced_ns.update(v_nested_ns)
                verses_field.append(
                    {"vid": vid, "placeholder": c["placeholder"], "content": draft_verses[vid]}
                )
            seg_referenced_ns.update(verse_fnrefs)

            all_nodes.append(
                {
                    "id": bid,
                    "seg": seg,
                    "kind": kind,
                    "raw_type": raw_type,
                    "order_index": order_index,
                    "medium": medium,
                    "text": text,
                    "fnrefs": sorted(set(fnrefs) | verse_fnrefs),
                    "verses": verses_field,
                }
            )

        if seg_order_indices:
            seg_min_order_index[seg] = min(seg_order_indices)

        # -- orphan-definition check: every footnote/verse THIS segment's
        # -- own draft defines must be referenced by at least one sentinel
        # -- somewhere in its own blocks -- a defined-but-never-referenced
        # -- entry is a fatal bijection violation, not silently dropped.
        for n_str in draft_footnotes:
            n = int(n_str)
            if n in seg_referenced_ns:
                continue
            if verse_skip_mode and _footnote_verse_cited_in_segment(
                n, draft_verses, verse_store_by_vid
            ):
                # Fix B (#118 item 1): a skip-mode footnote whose sole citation
                # site is a mode-voided verse's content -- legitimately
                # unresolvable, not an orphan. Referenced-ONLY: its def text is
                # NOT registered into book_footnotes and n never joins any
                # node's fnrefs, so nothing dangles at render. But a verse
                # EMBEDDED in this exempted footnote's own def text must still be
                # marked referenced (else the orphan_verse loop below
                # false-fatals it): scan it via the shared helper and fold the
                # found vids into seg_referenced_vids. Under skip the helper's
                # recursion into each embedded verse's own content is a no-op
                # (content == {}), so no book_footnotes are set and nested_ns is
                # empty -- an arbitrarily deep skip-voided chain
                # (V001->fn1->V002->fn2->...) converges via THIS flat loop
                # instead, because every footnote in the chain is independently
                # exempted by the same manifest-ground-truth condition,
                # order-independent, and each exemption scans its own def text.
                emb_vids, _emb_nested = _scan_footnote_def_embedded_verses(
                    draft_footnotes.get(n_str) or "",
                    seg,
                    f"{n_str}:skip-exempt",
                    n,
                    footnote_entries_by_n,
                    draft_footnotes,
                    book_footnotes,
                    placeholder_set,
                    placeholder_to_vid,
                    draft_verses,
                    book_seen_placeholders,
                )
                seg_referenced_vids.update(emb_vids)
                continue
            raise AssembleError(
                f"[{seg}] draft.footnotes[{n_str!r}] is defined but "
                f"never referenced by any ⟦FNREF_{n_str}⟧ sentinel in "
                f"this segment's blocks -- orphan footnote definition",
                reason="orphan_footnote_def",
            )
        for vid in draft_verses:
            if vid not in seg_referenced_vids:
                raise AssembleError(
                    f"[{seg}] draft.verses[{vid!r}] is defined but never "
                    f"referenced by any verse placeholder sentinel in this "
                    f"segment's blocks (including any footnote def text) -- "
                    f"orphan verse",
                    reason="orphan_verse",
                )

    # -- monotonicity sanity WARN: manifest.segments[] array order vs. --
    # -- each included segment's own minimum block order_index. Nodes  --
    # -- are sorted by order_index regardless, so a violation here     --
    # -- cannot mis-order the actual assembled output -- it is purely  --
    # -- an early-warning signal of a possible extraction bug.         --
    prev_seg = prev_min = None
    for seg_entry in manifest_segments:
        seg = seg_entry.get("seg")
        if seg not in seg_min_order_index:
            continue
        cur_min = seg_min_order_index[seg]
        if prev_min is not None and cur_min < prev_min:
            print(
                f"WARNING: manifest.segments[] array order disagrees with "
                f"block order_index -- {seg!r} (min order_index={cur_min}) "
                f"follows {prev_seg!r} (min order_index={prev_min}) but has a "
                f"SMALLER order_index. Nodes are still sorted by order_index, "
                f"so book order is correct regardless -- this may indicate an "
                f"extraction bug worth investigating.",
                file=sys.stderr,
            )
        prev_seg, prev_min = seg, cur_min

    # -- frontback regenerate/omit entries (never in manifest.segments[]) --
    for fb in manifest_frontback:
        decision = fb.get("decision")
        if decision == "translate":
            continue  # already handled via the ordinary segments[] loop above
        if decision == "omit":
            continue  # drop entirely -- an already-approved extraction-time choice
        if decision != "regenerate":
            raise AssembleError(
                f"manifest.json frontback[] entry {fb.get('id')!r} has an "
                f"unrecognized decision {decision!r} -- expected "
                f"translate|regenerate|omit"
            )

        fb_id = fb.get("id")
        mb = manifest_blocks.get(fb_id)
        if mb is None:
            print(
                f"WARNING: frontback {fb_id!r} has decision=regenerate but no "
                f"matching manifest.blocks entry to position it by "
                f"order_index -- SKIPPING (cannot place it safely)",
                file=sys.stderr,
            )
            continue
        origin = mb.get("origin", "unknown")
        reason = fb.get("reason", "")
        print(
            f"WARNING: frontback {fb_id!r} (origin={origin}) is "
            f"decision=regenerate -- emitting a documented placeholder node, "
            f"not real content (full regeneration is a later-phase "
            f"refinement)",
            file=sys.stderr,
        )
        placeholder_text = (
            f"[REGENERATE PLACEHOLDER -- {fb_id}, origin={origin}: fresh "
            f"target-language matter not yet synthesized (Phase 0 scope); "
            f"reason: {reason}]"
        )
        all_nodes.append(
            {
                "id": fb_id,
                "seg": fb_id,
                "kind": "prose",
                "raw_type": "FRONTBACK_REGENERATE_PLACEHOLDER",
                "order_index": mb["order_index"],
                "medium": "plain",
                "text": placeholder_text,
                "fnrefs": [],
                "verses": [],
            }
        )

    all_nodes.sort(key=lambda n: n["order_index"])

    seg_order = []
    seen_segs = set()
    for node in all_nodes:
        if node["seg"] not in seen_segs:
            seen_segs.add(node["seg"])
            seg_order.append(node["seg"])

    book_title = (profile.get("project") or {}).get("title") or None

    meta = {
        "target": _profile_get(profile, "target.language.code"),
        "verse_mode": _profile_get(profile, "verse_policy.mode"),
        "apparatus_policy": _profile_get(profile, "footnotes.apparatus_policy"),
    }

    footnotes_field = [{"n": n, "text": book_footnotes[n]} for n in sorted(book_footnotes)]

    nodestream = {
        "book": {"seg_order": seg_order, "title": book_title},
        "nodes": all_nodes,
        "footnotes": footnotes_field,
        "meta": meta,
    }

    anchor_map = {
        "blocks": [
            {"block_id": n["id"], "seg": n["seg"], "kind": n["kind"], "order_index": n["order_index"]}
            for n in all_nodes
        ],
        "footnotes": sorted(book_footnotes),
        "verses": [v["vid"] for n in all_nodes for v in n["verses"]],
    }

    return nodestream, anchor_map


# ---------------------------------------------------------------------------
# Adapter dispatch (contract section 10).
# ---------------------------------------------------------------------------


def _system_exit_detail(exc: SystemExit) -> str:
    """`sys.exit(some_string)` sets `SystemExit.code` to that string, but
    Python only auto-prints it to stderr when the exception propagates all
    the way to the interpreter uncaught -- since dispatch_adapter() catches
    it here, that message would otherwise be silently discarded even when
    the halting module never itself wrote anything to stderr. Surface it
    when present; `exc.code` is `None`/an int/empty for a plain
    `sys.exit()` or `sys.exit(2)`, which carries no extra information
    beyond "see stderr"."""
    if isinstance(exc.code, str) and exc.code:
        return f"it exited with: {exc.code!r}"
    return "see this run's stderr for the specific reason it halted"


# ---------------------------------------------------------------------------
# Mentions-section source data (D1, opt-in -- RFC lt-appendix-backlink-
# integrity). Attaches nodestream["mentions"] BEFORE nodestream.json is
# persisted and BEFORE dispatch_adapter runs, so both the on-disk artifact
# and the in-process render() call carry it -- the adapter contract itself
# (4 positional args) never changes; this data simply rides inside arg 1.
# ---------------------------------------------------------------------------


def _effective_mentions_enabled(profile: dict) -> bool:
    """Mirrors render_obsidian.py's own `_effective_mentions_enabled` and
    validate_backlinks.py's identical predicate -- computed independently
    in each file from the SAME two profile fields (never imported from one
    another), so a dormant `obsidian` sub-block under a different
    `output.target` can never activate this feature anywhere it's gated.
    `output.target` must be EXACTLY "obsidian" AND
    `output.adapter_config.obsidian.mentions_section.enabled` must be
    boolean `True`."""
    output_cfg = (profile or {}).get("output") or {}
    if output_cfg.get("target") != "obsidian":
        return False
    obsidian_cfg = (output_cfg.get("adapter_config") or {}).get("obsidian") or {}
    mentions_cfg = obsidian_cfg.get("mentions_section") or {}
    return mentions_cfg.get("enabled") is True


def _attach_mentions(nodestream: dict, profile: dict, manifest: dict, canon: dict) -> None:
    """D1: when `_effective_mentions_enabled(profile)` holds, resolve this
    project's `language_config` + `canon_senses.json` sidecar, derive the
    source-anchored occurrence aggregate via `occurrence_targets.build`
    (the pinned contract -- see the plan's "Contract" section), and attach
    `nodestream["mentions"] = aggregate["eligible_by_source_form"]` so
    `dispatch_adapter`'s render_obsidian.py sees it. Mutates `nodestream`
    in place; the caller is expected to have already checked
    `_effective_mentions_enabled` (kept a caller precondition, not
    re-checked here, so this function's own unit tests can exercise it
    directly without needing a full effective-enabled profile).

    `bootstrap_names`/`canon_senses`/`occurrence_targets` are imported
    LAZILY, here, rather than at module level: this is the ONLY code path
    that ever needs them, and a flag-off (the default) project must incur
    ZERO new dependency surface -- `canon_senses.py` alone requires
    `jsonschema`, which assemble.py has otherwise never needed
    (`validate_draft.py`'s own profile loader is deliberately hand-rolled,
    jsonschema-free)."""
    particle_config = _profile_get(profile, "source.language.particle_config")

    sys.path.insert(0, str(SCRIPTS_DIR))
    try:
        import bootstrap_names
        import canon_senses
        import occurrence_targets
    except ImportError as exc:
        raise AssemblePrecondition(
            "dependency_precondition",
            "mentions_section.enabled is true but bootstrap_names.py/"
            f"canon_senses.py/occurrence_targets.py could not be imported "
            f"from {SCRIPTS_DIR}: {exc}",
        ) from exc
    except SystemExit as exc:
        raise AssemblePrecondition(
            "dependency_precondition",
            "mentions_section.enabled is true but a dependency "
            "(bootstrap_names.py/canon_senses.py/occurrence_targets.py) "
            f"halted during its own module-level dependency preflight -- "
            f"{_system_exit_detail(exc)}",
        ) from exc

    try:
        language_config = bootstrap_names.load_language_config(particle_config)
    except bootstrap_names.BootstrapNamesError as exc:
        raise AssembleError(
            f"mentions_section.enabled is true but the language config "
            f"failed to load: {exc}",
            reason="mentions_language_config_invalid",
        ) from exc

    try:
        senses_result = canon_senses.load_senses(CANON_SENSES_PATH, allow_absent=True)
    except canon_senses.CanonSensesLoadError as exc:
        raise AssembleError(
            f"mentions_section.enabled is true but canon_senses.json "
            f"failed to load: {exc}",
            reason="mentions_canon_senses_invalid",
        ) from exc

    aggregate = occurrence_targets.build(manifest, canon, senses_result, language_config, nodestream)
    nodestream["mentions"] = aggregate["eligible_by_source_form"]


def dispatch_adapter(nodestream: dict, canon: dict, profile: dict, out_dir: Path) -> dict:
    try:
        adapter = output_resolve.resolve_output_adapter(profile, DURABLE_ROOT)
    except output_resolve.OutputResolveError as exc:
        raise AssembleError(str(exc)) from exc

    if isinstance(adapter, str):
        sys.path.insert(0, str(SCRIPTS_DIR))
        try:
            mod = __import__(adapter)
        except ImportError as exc:
            raise AssembleError(
                f"could not import built-in adapter module {adapter!r} from "
                f"{SCRIPTS_DIR}: {exc} -- has this adapter shipped yet?"
            ) from exc
        except SystemExit as exc:
            # A built-in adapter (e.g. render_obsidian.py) can halt via
            # sys.exit() during its own module-level dependency preflight
            # (a missing-package guard) -- SystemExit deliberately does not
            # subclass Exception, so it would otherwise escape both this
            # function's own `except Exception` below and main()'s
            # outermost `except Exception` too, crashing the process with
            # no JSON on stdout. Re-surface it as the same
            # `dependency_precondition` contract the top-of-file
            # validate_draft/output_resolve imports already use.
            raise AssemblePrecondition(
                "dependency_precondition",
                f"built-in adapter module {adapter!r} halted during its "
                f"own module-level dependency preflight while being "
                f"imported from {SCRIPTS_DIR} -- {_system_exit_detail(exc)}",
            ) from exc
    else:
        # A Path -- the resolved, path-safety-checked custom renderer module.
        if not adapter.is_file():
            raise AssembleError(f"custom renderer module not found at {adapter}")
        spec = importlib.util.spec_from_file_location("custom_output_renderer", adapter)
        if spec is None or spec.loader is None:
            raise AssembleError(f"could not load custom renderer module spec from {adapter}")
        mod = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(mod)
        except SystemExit as exc:
            # Mirrors the built-in-adapter case above -- SystemExit would
            # otherwise escape uncaught the same way -- but unlike a
            # built-in adapter (whose only module-level sys.exit() is a
            # known dependency guard), a user-authored custom renderer is
            # an open extension point: its own module-level halt could be
            # ANY precondition it chooses to check, not necessarily a
            # missing dependency. Use a distinct, honest reason rather than
            # claiming "dependency preflight" for a cause we don't actually
            # know.
            raise AssemblePrecondition(
                "adapter_import_precondition",
                f"custom renderer module at {adapter} halted during its "
                f"own module-level import-time precondition check -- "
                f"{_system_exit_detail(exc)}",
            ) from exc
        except Exception as exc:
            raise AssembleError(f"custom renderer module at {adapter} failed to import: {exc}") from exc

    if not hasattr(mod, "render"):
        raise AssembleError(
            f"adapter module {adapter!r} has no render(nodestream, canon, "
            f"profile, out_dir) entry point"
        )
    try:
        return mod.render(nodestream, canon, profile, out_dir)
    except AssembleError:
        raise
    except Exception as exc:
        raise AssembleError(f"adapter render() failed: {exc}") from exc


# ---------------------------------------------------------------------------
# CLI entry point.
# ---------------------------------------------------------------------------


def main() -> int:
    try:
        try:
            profile = vd.load_profile()
        except SystemExit as exc:
            # validate_draft.py's own load_profile() halts via sys.exit(2)
            # on a profile/environment precondition, printing only to
            # stderr -- never a bare stdout-less exit here; re-surface it
            # as the same one-JSON-line, reason-carrying contract every
            # other precondition below uses.
            raise AssemblePrecondition(
                "profile_precondition",
                "profile.yml failed to load/validate via validate_draft.py's "
                "own profile loader (see this run's stderr for the specific "
                "reason it halted)",
            ) from exc

        v1_scope = _profile_get(profile, "output.v1_scope")
        if v1_scope != "assembled_book":
            raise AssemblePrecondition(
                "not_assembled_book_scope",
                f"output.v1_scope is {v1_scope!r}, not 'assembled_book' -- "
                f"this project's profile does not request book assembly; "
                f"nothing to do",
            )

        if not MANIFEST_PATH.is_file():
            raise AssemblePrecondition(
                "no_manifest",
                f"manifest.json not found at {MANIFEST_PATH} -- extraction "
                f"has not run yet",
            )
        manifest = read_json(MANIFEST_PATH, "manifest.json")

        if not LEDGER_PATH.is_file():
            raise AssemblePrecondition(
                "no_ledger",
                f"runs/ledger.json not found at {LEDGER_PATH} -- nothing has "
                f"converged yet (run ledger_merge.py after at least one "
                f"segment converges)",
            )
        ledger = read_json(LEDGER_PATH, "runs/ledger.json")

        converged = load_converged_segments(ledger)
        if not converged:
            raise AssemblePrecondition(
                "no_converged_segments",
                "runs/ledger.json has zero segments with status=converged -- "
                "nothing to assemble yet",
            )

        assert_project_complete(manifest, converged)

        canon = {"entries": {}, "review_queue": []}
        if CANON_PATH.is_file():
            canon = read_json(CANON_PATH, "canon.json")

        nodestream, anchor_map = build_nodestream(profile, manifest, converged)

        # D1 (opt-in, lt-appendix-backlink-integrity): attach the
        # source-anchored Mentions data BEFORE nodestream.json is
        # persisted below -- the e2e three-view parity test reads this
        # exact "persisted mentions" view back off disk. Flag off (the
        # default) or any other output.target: attaches nothing, touches
        # no new dependency, byte-identical to 1.7.0.
        if _effective_mentions_enabled(profile):
            _attach_mentions(nodestream, profile, manifest, canon)

        if ASSEMBLED_DIR.parent.is_symlink():
            # The vector isn't just `.assembled/` itself -- its PARENT
            # (DURABLE_ROOT/"out") is a direct child of the trusted
            # DURABLE_ROOT too, and a planted `out -> /external` symlink
            # would let mkdir(parents=True)/mkstemp write both artifacts
            # (and everything the adapter later writes) straight into an
            # external target, before the adapter's own out_dir guard ever
            # runs. Checking `.is_symlink()` (never a realpath-containment
            # check) also correctly does NOT reject a legitimately
            # symlinked skill INSTALL, where DURABLE_ROOT itself may be a
            # symlink but "out/" underneath it is a real subdirectory.
            raise AssembleError(
                f"refusing to write assembled artifacts: "
                f"{ASSEMBLED_DIR.parent} is a symlink, not a real directory",
                reason="out_dir_is_symlink",
            )
        if ASSEMBLED_DIR.is_symlink():
            # `.assembled/` is a preserved dotfile (render_obsidian's own
            # clean-render never recurses into it), so a planted
            # `out/.assembled -> /external/dir` symlink survives across
            # renders indefinitely -- mkdir(exist_ok=True) happily accepts
            # an existing symlink-to-directory, which would silently write
            # nodestream.json/anchor_map.json outside durable_root
            # entirely. Refuse outright rather than follow it.
            raise AssembleError(
                f"refusing to write assembled artifacts: {ASSEMBLED_DIR} is "
                f"a symlink, not a real directory",
                reason="assembled_dir_is_symlink",
            )
        ASSEMBLED_DIR.mkdir(parents=True, exist_ok=True)
        nodestream_path = ASSEMBLED_DIR / "nodestream.json"
        anchor_map_path = ASSEMBLED_DIR / "anchor_map.json"
        _write_json_atomically(nodestream_path, nodestream)
        _write_json_atomically(anchor_map_path, anchor_map)

        try:
            out_dir = output_resolve.resolve_out_dir(profile, DURABLE_ROOT)
        except output_resolve.OutputResolveError as exc:
            raise AssembleError(str(exc)) from exc
        out_dir.mkdir(parents=True, exist_ok=True)

        adapter_result = dispatch_adapter(nodestream, canon, profile, out_dir)

    except AssemblePrecondition as exc:
        print(json.dumps({"success": False, "reason": exc.reason, "error": str(exc)}))
        return 2
    except AssembleError as exc:
        payload = {"success": False, "error": str(exc)}
        if exc.reason:
            payload["reason"] = exc.reason
        print(json.dumps(payload))
        return 1
    except Exception as exc:  # pragma: no cover -- defensive catch-all
        print(json.dumps({"success": False, "error": f"unexpected error: {exc}"}))
        return 1

    result = {
        "success": True,
        "target": profile.get("output", {}).get("target"),
        "segments_assembled": len(converged),
        "nodes": len(nodestream["nodes"]),
        "footnotes": len(nodestream["footnotes"]),
        "verses": sum(len(n["verses"]) for n in nodestream["nodes"]),
        "nodestream_path": str(nodestream_path),
        "anchor_map_path": str(anchor_map_path),
        "adapter_result": adapter_result,
    }
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
