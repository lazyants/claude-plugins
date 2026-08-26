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
any sentinel in its blocks), `verse_fnref_coverage` (a footnote anchor the
SOURCE verse carried is absent from that verse's translated content, so it
would print on whichever other line kept it), `duplicate_verse_placeholder` (the same verse
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
import errno
import hashlib
import importlib.util
import json
import os
import re
import stat
import sys
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import NoReturn

# Importing a sibling module writes scripts/__pycache__/*.pyc. Several
# entrypoints here promise not to write anything (cache_key.py) or promise ZERO
# filesystem writes in dry-run (backfill_resume_gate_ack.py), so the whole set
# opts out uniformly rather than case by case.
sys.dont_write_bytecode = True


# --- the shared one-line JSON serialiser (#369) -----------------------------
# Loaded by EXACT PATH, never `import json_stdout`. A bare sibling import
# resolves through the global sys.modules cache regardless of which staged copy
# the CALLER intended, so one process that stages several durable roots would
# bind the FIRST root's copy for all of them. exec_module() opens this file's
# own sibling or raises -- the loud failure the staging discipline depends on,
# and it needs no cache eviction to get there. `Path(__file__).absolute()`
# rather than `.resolve()`: the unresolved form is what lets a caller's own
# no-follow symlink logic still see the path it was handed.
# (`importlib.util` is already imported at the top of this module.)

_JSON_STDOUT_PATH = Path(__file__).absolute().parent / "json_stdout.py"
try:
    _json_stdout_spec = importlib.util.spec_from_file_location(
        "json_stdout", _JSON_STDOUT_PATH
    )
    if _json_stdout_spec is None or _json_stdout_spec.loader is None:
        raise ImportError(f"no loader for {_JSON_STDOUT_PATH}")
    _json_stdout = importlib.util.module_from_spec(_json_stdout_spec)
    # OSError, not ImportError alone: spec_from_file_location() happily builds a
    # spec for a file that is not there, and it is exec_module() that raises
    # FileNotFoundError when it opens the source.
    _json_stdout_spec.loader.exec_module(_json_stdout)
except (ImportError, OSError) as _json_stdout_exc:  # pragma: no cover - staging error path
    sys.exit(
        f"assemble.py: cannot load json_stdout.py from {_JSON_STDOUT_PATH} "
        f"({_json_stdout_exc}).\n"
        "json_stdout.py must be installed alongside assemble.py under "
        "${durable_root}/scripts/ -- Step 0a's copy pass places it there."
    )

dumps_line = _json_stdout.dumps_line

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
CANON_LINK_GROUPS_PATH = DURABLE_ROOT / "canon_link_groups.json"
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


# validate_draft.py (profile loading), output_resolve.py (Step 0d adapter
# resolution) and cache_key.py (#492's live cache-key recomputation, imported
# a little further down) live next to this script -- import them directly
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
try:
    import cache_key as ck
except ImportError as exc:  # pragma: no cover -- defensive, should be unreachable
    _dependency_precondition_fatal(
        f"could not import cache_key.py from {SCRIPTS_DIR}: {exc}"
    )
# #492: deliberately NO `except SystemExit` arm here, unlike the two above.
# cache_key.py's PyYAML import is try/except-guarded (cache_key.py:104-107) and
# merely assigns `yaml = None`; its require_yaml() fail() fires later, from
# load_profile() -- which assert_live_inputs_match_ledger() calls inside a
# SystemExit handler of its own. So this module CANNOT exit during the import
# statement, and a handler here would be dead code claiming otherwise.
# Assembly already hard-requires PyYAML through the validate_draft import
# above, so this adds no dependency.


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
# #491: the machinery-only "stale" carve-out. A converged segment whose ONLY
# cache-key drift is tooling/schema/derivation-bundle (never the source
# text, style bible, prompts, canon terms, or engine config) never needed
# re-review in the first place -- ledger_merge.py now records WHICH fields
# moved (`stale_mismatched_fields`) on the materialized entry only, and
# load_converged_segments() below accepts such a record exactly like
# status=="converged", so a plugin upgrade can no longer strand a finished
# book. See final_audit.py's own `count_stale_previously_converged()`
# (`final_audit.py:1811-1893`), the sibling carve-out this one is designed
# to always agree with over the SAME materialized runs/ledger.json snapshot.
# ---------------------------------------------------------------------------

# Deliberately restated, not imported -- house convention for this plugin's
# self-contained scripts (see e.g. ledger_merge.py's own CACHE_KEY_FIELDS).
# A drift test (tests/stale_carveout.test.py) pins this against
# final_audit.SAFE_STALE_CARVEOUT_FIELDS and
# select_segments.MACHINERY_ONLY_CACHE_KEY_FIELDS so the three copies can
# never silently disagree.
SAFE_STALE_CARVEOUT_FIELDS = frozenset(
    {"plugin_bundle_hash", "schema_hash", "derivation_bundle_hash"}
)

# ---------------------------------------------------------------------------
# #533: the SECOND, opt-in acceptance path -- deliberately NOT a member of the
# allowlist above. That set means "can never change what the prose should
# say", which is FALSE for the style contract: a contract edit CAN change what
# the prose should say, and a REVERSED rule actively demanded the wrong choice
# in every segment converged under it. One global style_contract_hash
# (cache_key.py:273, GLOBAL_CACHE_KEY_FIELDS at :292) cannot tell an addition
# from a reversal, so this population is admitted only when the operator
# DECLARES it, per project, and every admitted segment is named. Widening the
# allowlist instead would also silently move final_audit.py's own
# project_complete arithmetic and select_segments.py's D6 semantics, which
# read the same field list for different questions.
# ---------------------------------------------------------------------------

CONTRACT_ONLY_STALE_FIELD = "style_contract_hash"

# ---------------------------------------------------------------------------
# #492: the fields assembly re-derives from the LIVE durable_root and compares
# to each shipped record's stored cache_key, so its verdict stops depending on
# whether ledger_merge.py happened to run since the last content edit.
#
# DERIVED from cache_key.py's own CACHE_KEY_FIELD_ORDER minus the
# machinery-only allowlist above -- never a fifth hand-written field list. A
# future 16th cache-key field is live-checked automatically, which is the
# fail-closed direction: a field nobody classified yet blocks rather than
# ships. The three carved-out fields are excluded for exactly the reason
# SAFE_STALE_CARVEOUT_FIELDS exists -- they cannot change what the prose
# should say -- so this check adds no machinery-only refusal and cannot
# re-strand a book a plugin upgrade already made deliverable.
# ---------------------------------------------------------------------------

LIVE_CHECKED_CACHE_KEY_FIELDS = tuple(
    f for f in ck.CACHE_KEY_FIELD_ORDER if f not in SAFE_STALE_CARVEOUT_FIELDS
)


def admit_contract_only_stale(profile: dict) -> bool:
    """Reads profile.yml's `validation.admit_contract_only_stale` (#533).

    True for a LITERAL `True` and nothing else. An absent `validation` block,
    a non-dict one, an absent key, `false`, `null`, the STRING "true" and the
    integer 1 all read as False -- `is True` rather than truthiness precisely
    so `1` (which compares equal to True) cannot become consent. Fail-closed:
    forgetting the declaration refuses, exactly as before this field existed.

    Restated in final_audit.py and validate_assembled.py rather than imported,
    and NOT hoisted into validate_draft.py -- which all three already import
    as `vd`, and which already owns load_profile(), so it is the obvious home.
    It is the wrong one: `validate_draft.py` is the first member of
    cache_key.py's PLUGIN_BUNDLE_MEMBERS and these three gate scripts are not
    members at all, so hosting the reader there would move
    plugin_bundle_hash for every project -- mass-invalidating every converged
    segment, which is the exact cost #533 exists to relieve. select_segments.py
    holds the fourth SAFE_STALE_CARVEOUT_FIELDS copy and does not import `vd`
    either -- since #446 it is itself a PLUGIN_BUNDLE_MEMBERS entry, so hosting
    the reader there would move the hash for exactly the same reason
    validate_draft.py would. The three copies are behaviourally identical
    (the signature and this docstring differ) and are driven over one shared
    table by tests/contract_stale_admission.test.py, which pins behaviour, not
    source identity."""
    validation = (profile or {}).get("validation")
    if not isinstance(validation, dict):
        return False
    return validation.get("admit_contract_only_stale") is True


# ---------------------------------------------------------------------------
# The shared #409 ever-converged sentinel predicate. This block is an EXACT
# duplicate of the copy in the other four sentinel scripts (search
# `SENTINEL_ABSENT` in ledger_update.py, select_segments.py, final_audit.py
# and backfill_ever_converged.py) -- see classify_ever_converged_sentinel()'s
# own docstring for why it is duplicated rather than imported, and which
# test (tests/select_segments.test.py) pins the copies together. #491 makes
# this script a fifth participant: the machinery-only carve-out above needs
# the SAME three-state read final_audit.py's own carve-out uses, for the
# same reason -- an AMBIGUOUS read (a dangling symlink, an EACCES) must
# never read as absent and refuse a finished book over an unreadable
# dotfile.
# ---------------------------------------------------------------------------

SENTINEL_ABSENT = "absent"
SENTINEL_PRESENT = "present"
SENTINEL_AMBIGUOUS = "ambiguous"


def _sentinel_entry_kind(mode: int) -> str:
    """A human name for the st_mode of whatever occupies a sentinel path --
    it goes straight into an operator-facing message, which has to say what
    is actually sitting there before it can ask anyone to fix it."""
    if stat.S_ISLNK(mode):
        return "a symbolic link"
    if stat.S_ISDIR(mode):
        return "a directory"
    if stat.S_ISFIFO(mode):
        return "a FIFO"
    if stat.S_ISSOCK(mode):
        return "a socket"
    if stat.S_ISBLK(mode):
        return "a block device"
    if stat.S_ISCHR(mode):
        return "a character device"
    return f"a non-regular entry (st_mode {stat.S_IFMT(mode):#o})"


def classify_ever_converged_sentinel(path, *, dir_fd=None) -> "tuple[str, str]":
    """Three-state classification of the `.ever_converged.<seg>` entry at
    `path`: `(SENTINEL_ABSENT|SENTINEL_PRESENT|SENTINEL_AMBIGUOUS, detail)`.

    THE SHARED PREDICATE. Every script that asks whether a segment has ever
    converged calls this, and all five must agree on it:
    ledger_update.py's `mark_ever_converged()` (the only writer),
    select_segments.py's #409 Step 1 dispatch gate,
    final_audit.py's `count_stale_previously_converged()` carve-out,
    backfill_ever_converged.py's `already_sentineled` scan, and
    assemble.py's #491 machinery-only stale carve-out.

    DUPLICATED RATHER THAN IMPORTED because importing it would be a live
    hazard -- NOT because of the "no shared lib between self-contained
    scripts" convention, which is already false here (canon_validate.py and
    glossary_batch_plan.py import canon_senses.py; scaffold_setup.py imports
    cache_key.py). The real reason: ledger_update.py is a
    PLUGIN_BUNDLE_MEMBERS entry, and cache_key.py:149-156 records that that
    tuple is a literal byte-hash allowlist to which a TRANSITIVE IMPORT IS
    INVISIBLE -- which is why canon_senses.py had to be registered
    explicitly once two members imported it. A shared module would put this
    predicate's bytes outside the hash meant to cover them, so WEAKENING
    this guard would no longer move plugin_bundle_hash, and every durable
    root scaffolded beforehand would go on trusting it: the exact
    false-green cache_key.py:163-167 names. Consolidation stays possible --
    it just has to register the new module in PLUGIN_BUNDLE_MEMBERS in the
    same commit.

    What keeps the five copies honest is ENFORCEMENT, not discipline. A
    remembered convention rots -- this docstring's own first version cited
    the false one -- while a test that fails loudly does not.
    tests/select_segments.test.py's
    test_sentinel_predicate_is_identical_in_all_five_scripts pins the copies
    byte for byte and across the state matrix; its
    test_exactly_these_five_scripts_participate_in_the_sentinel_contract
    fails when a sixth copy appears or one of the five goes away.

    Why three states, and why not `Path.exists()`. `exists()` answers the
    wrong question three ways, and NOT all of them in the same direction --
    an earlier draft of this docstring said "twice over, and BOTH point at
    absent", which is the claim the CHANGELOG had to correct. Two of the
    three do point at "absent", and that is the direction that authorizes
    destroying converged work:

      1. It FOLLOWS symlinks, so a DANGLING symlink named as the sentinel
         reads as absent -- while the writer's `os.open(O_CREAT|O_EXCL)` gets
         EEXIST from that same symlink and reports the segment successfully
         marked. That split is the whole finding: a segment recorded as
         converged that the gate then sees as unprotected and retranslates.
         Verified on this project's Python (3.14.6): `exists()` -> False,
         `os.open` -> FileExistsError, for one and the same dangling link.
      2. Since Python 3.14 `exists()` swallows EVERY OSError and returns
         False, so an EACCES/ESTALE/EIO on the lookup is reported as "this
         segment never converged". Verified on 3.14.6: with an unreadable
         parent directory `exists()` returns False while `lstat()` raises
         EACCES. (On 3.10-3.13 the same call re-raised for EACCES but still
         swallowed ELOOP/ENOTDIR/EBADF -- so no supported version answers
         this correctly, and the version-dependence is itself a reason not
         to route a data-loss guard through `exists()`.)
      3. In the OTHER direction: a DIRECTORY at the marker's path is
         `exists() == True`, so `exists()` reports converged a segment the
         writer never marked. That one cannot destroy finished work, which is
         why it went unnoticed -- but it is the reason "exists() at least
         fails safe in one direction" is false, and the reason the fix is a
         third state rather than a flipped default.

    So: only ENOENT means absent, and it is determined by catching
    FileNotFoundError rather than by comparing `exc.errno`, so the verdict
    never depends on an errno that may be None. `lstat`, deliberately not
    `stat` -- a symlink is not something `mark_ever_converged()` can have
    (its O_CREAT|O_EXCL open refuses to write through one), so following a
    link would only ask the question about some unrelated file. Either way
    only the final `.ever_converged.<seg>` component is left unresolved:
    WITHOUT `dir_fd` the PARENT components still resolve normally, so a
    project whose whole `segments/` directory is a symlink is unaffected;
    WITH `dir_fd` there are no parent components left to resolve, because
    the caller already resolved them once, when it opened the descriptor.

    `dir_fd` -- OPTIONAL, and today TWO callers pass it:
    backfill_ever_converged.py's census and select_segments.py's #409 Step 1
    dispatch gate, each of which opens `segments/` once and reads every
    entry through that descriptor. Omitted (every other caller), the
    lookup resolves the whole pathname afresh, which is the right thing for
    a reader that holds nothing open. Passed, the BASENAME is looked up
    relative to that descriptor instead, and `segments/` is not resolved by
    pathname at all. The difference matters only for a caller that already
    HOLDS the directory open and acts on its census afterwards, which is
    what both of those do: the backfill opens `segments/` once, does every
    write relative to the descriptor, and samples directory identity at the
    end; the dispatch gate opens it before the census and refuses outright
    when it cannot (#621). A census
    resolving the pathname afresh could therefore classify entries in a
    DIFFERENT directory than the one being written to -- re-point
    `segments/` at B for the length of the census and back to A before the
    run ends, and B's sentinel is reported as A's protection while the
    final identity sample compares A to A and agrees. Reproduced by review,
    not theorised. Binding the census to the descriptor removes that
    interleaving with no locking protocol at all, because the descriptor is
    already held; a caller that holds none gains nothing here and passes
    None.

    Anything that is neither ENOENT nor a regular file is AMBIGUOUS: it MAY
    be a converged segment whose sentinel this process cannot see. Each
    caller then maps AMBIGUOUS to ITS OWN work-preserving side, and that is
    deliberately NOT the same action in all five: the writer and the
    dispatch gate REFUSE (never destroy or mis-record converged work), while
    final_audit.py's carve-out COUNTS it (never declare a converged book
    incomplete and therefore undeliverable), backfill's scan reports it
    unprotected (never claim protection it did not verify), and
    assemble.py's #491 carve-out ADMITS it (never refuse to assemble a
    finished book over a sentinel this process cannot read). One predicate,
    five deliberate mappings -- see each call site's own comment. The
    asymmetry is the reason a false "absent" is the unacceptable answer
    everywhere: it costs a finished translation, or a finished book.
    """
    try:
        # `path.name` is the basename and the descriptor is its parent, so
        # the `dir_fd` branch resolves no part of `segments/` by pathname.
        # `os.lstat` keeps `follow_symlinks` off exactly as `Path.lstat`
        # does, so the FINAL component stays unresolved either way and both
        # branches raise the same exceptions into the same handlers below.
        st = path.lstat() if dir_fd is None else os.lstat(path.name, dir_fd=dir_fd)
    except FileNotFoundError:
        return (SENTINEL_ABSENT, "")
    except OSError as exc:
        # `OSError.errno` is typed `int | None` and genuinely can be None. A
        # missing errno is the LEAST informative failure there is, so it
        # lands on the ambiguous side like every other non-ENOENT outcome --
        # never silently treated as "some other errno", and above all never
        # as absence. The ENOENT verdict above does not consult `errno` at
        # all (FileNotFoundError IS ENOENT by construction), so a None errno
        # can never reach it, which is why this branch can be a plain guard
        # rather than a three-way comparison.
        if exc.errno is None:
            return (SENTINEL_AMBIGUOUS, f"lstat failed with no errno: {exc}")
        code = errno.errorcode.get(exc.errno, f"errno {exc.errno}")
        return (SENTINEL_AMBIGUOUS, f"lstat failed with {code}: {exc.strerror or exc}")
    if stat.S_ISREG(st.st_mode):
        return (SENTINEL_PRESENT, "")
    return (
        SENTINEL_AMBIGUOUS,
        f"the entry is {_sentinel_entry_kind(st.st_mode)}, not a regular file",
    )


def ever_converged_path(seg):
    """The durable 'this segment has converged at least once' sentinel.
    WRITTEN by ledger_update.py:mark_ever_converged (the single place
    convergence is recorded). Restated here -- assemble.py's own #491
    carve-out reader -- rather than imported; see
    classify_ever_converged_sentinel()'s own docstring for why the whole
    predicate is duplicated rather than shared. Never called for anything
    but the read-only carve-out check below: this script writes no
    sentinel."""
    return SEGMENTS_DIR / f".ever_converged.{seg}"


def _stale_carveout_refusal_reason(
    seg: str, record: dict, admit_contract_only: bool = False
) -> "str | None":
    """Returns None when `record` (a runs/ledger.json entry already known to
    have status=="stale") qualifies for the #491 machinery-only carve-out --
    i.e. is to be treated exactly like status=="converged" by every check
    load_converged_segments() runs after this one returns -- or a specific,
    human-readable refusal reason naming the first condition that failed.

    Four conditions, ALL of which must hold, checked in this order so the
    reason names the FIRST one that fails (1/3/4 are acceptance criteria
    1/2/3 of the #491 plan; 2 is a hardening added on review of the
    original #491 patch -- see below):
      1. `stale_mismatched_fields` is present and a non-empty list.
      2. Every member of that list is a `str`.
      3. Every one of its members is in SAFE_STALE_CARVEOUT_FIELDS -- OR
         (#533, only when `admit_contract_only` is True) the members outside
         that set are exactly `{style_contract_hash}`.
      4. The `.ever_converged.<seg>` sentinel is not SENTINEL_ABSENT.

    Condition 3's #533 arm is a SEPARATE acceptance path, not a widening of
    the allowlist: it is reached only by an explicit per-project declaration
    (`validation.admit_contract_only_stale`), it is tested as a SET so that
    a hand-edited `["style_contract_hash", "style_contract_hash"]` -- which
    ledger.schema.json permits, having minItems but no uniqueItems -- reaches
    the same verdict here as in final_audit.py's own count, and conditions 1,
    2 and 4 still apply unchanged. The draft-unchanged half of the #533
    predicate is NOT restated here: load_converged_segments() already
    recomputes every accepted record's draft sha1 against its own
    reviewed_draft_sha1, FATALLY, a few lines below.

    Condition 2 exists because a hand-edited or corrupted runs/ledger.json
    (this script never schema-validates the ledger it reads) can carry a
    `stale_mismatched_fields` list whose MEMBERS are malformed --
    `[{}]`/`[[]]` are unhashable and raise TypeError at condition 3's `f not
    in SAFE_STALE_CARVEOUT_FIELDS` frozenset test; `[1]`/`[None]` are
    hashable but then raise TypeError at `sorted()`/`', '.join()` over a
    mixed or non-string `unsafe`. Without this check the outer handler
    turns either crash into a generic "unexpected error" instead of this
    function's own per-segment `project_incomplete` refusal -- still
    fail-closed (assembly still aborts), but the wrong diagnostic for what
    is, structurally, just another "unusable stale_mismatched_fields" shape.

    FAIL-SAFE DIRECTION throughout, deliberately: missing, empty, or
    non-list `stale_mismatched_fields` refuses -- so a runs/ledger.json
    written before this change (which never wrote the field at all) blocks
    assembly rather than shipping. An unrecognised or future field name is
    absent from the allowlist by construction and refuses too, never
    silently becomes deliverable. And ONLY a clean ENOENT sentinel read
    blocks -- AMBIGUOUS (a dangling symlink, an EACCES) carves out exactly
    like PRESENT, mirroring final_audit.py's own
    count_stale_previously_converged() (`final_audit.py:1811-1893`) so the
    two whole-project completeness signals never disagree about the same
    materialized snapshot. Reading an unreadable dotfile as "absent" would
    declare a finished book undeliverable, and unrecoverably so: the
    operator's only route to a fresh sentinel is a retranslate, which
    select_segments.py's own #409 Step 1 gate refuses for a segment that
    already converged."""
    mismatched = record.get("stale_mismatched_fields")
    if not isinstance(mismatched, list) or not mismatched:
        return (
            f"segment {seg!r} is stale but its ledger record carries no "
            f"usable stale_mismatched_fields (missing, empty, or not a "
            f"list) -- cannot confirm the staleness is machinery-only; "
            f"re-review required"
        )
    non_str = [f for f in mismatched if not isinstance(f, str)]
    if non_str:
        return (
            f"segment {seg!r} is stale but its ledger record's "
            f"stale_mismatched_fields carries a non-string member "
            f"({non_str!r}) -- cannot confirm the staleness is "
            f"machinery-only; re-review required"
        )
    unsafe = sorted(f for f in mismatched if f not in SAFE_STALE_CARVEOUT_FIELDS)
    # Named once and reused by the sentinel-absent refusal below, which needs
    # the same answer to characterise the move truthfully. Two independent
    # spellings of one condition are two things that can drift apart.
    contract_only = admit_contract_only and set(unsafe) == {CONTRACT_ONLY_STALE_FIELD}
    if unsafe and not contract_only:
        return (
            f"segment {seg!r} is stale because of a content-affecting "
            f"cache-key field ({', '.join(unsafe)}) -- the machinery-only "
            f"carve-out requires every moved field to be one of "
            f"{{{', '.join(sorted(SAFE_STALE_CARVEOUT_FIELDS))}}}; "
            f"re-review required before assembling"
        )
    state, _detail = classify_ever_converged_sentinel(ever_converged_path(seg))
    if state == SENTINEL_ABSENT:
        # The characterisation has to match which arm of condition 3 let this
        # record through: calling style_contract_hash "machinery-only" would
        # be a false statement in this script's own refusal text, and the
        # #533 arm is exactly the case where it is not.
        moved_kind = (
            "the only field outside the machinery-only set is "
            f"{CONTRACT_ONLY_STALE_FIELD}"
            if contract_only
            else "every moved field is machinery-only"
        )
        return (
            f"segment {seg!r} is stale, and {moved_kind} "
            f"({', '.join(sorted(mismatched))}), but its "
            f".ever_converged sentinel is absent -- cannot confirm it ever "
            f"converged; re-review required"
        )
    return None


# ---------------------------------------------------------------------------
# #491 round-2 hardening (codex review of the original #491 patch): the
# manifest segment-id population, factored out here into ONE authoritative
# extraction so assert_project_complete() and load_converged_segments()
# (via main()'s own call site, below) can never derive a different notion
# of "in the manifest". assert_project_complete() calls the raising form
# directly -- it is, and remains, the sole place a malformed manifest is
# authoritatively reported. main()'s own call site instead goes through
# the NON-raising sibling right below it -- see that function's own
# docstring for why (round-2-on-round-2: codex found that raising straight
# out of main() had silently reordered two pre-existing, differently-coded
# outcomes -- see its docstring for the exact regression).
# ---------------------------------------------------------------------------


def _manifest_segment_ids(manifest: dict) -> "set[str]":
    """The exact manifest.segments[] population assert_project_complete()
    has always required completeness against -- moved here verbatim from
    that function's former inline copy so load_converged_segments() can
    reuse the IDENTICAL extraction (via _manifest_segment_ids_or_empty()
    right below, see its own docstring) to scope its own #491 stale-
    carveout fall-through: one implementation walked by both callers,
    never two copies that could silently drift apart.

    Raises the same AssembleError(reason="malformed_manifest") assembly has
    always raised when manifest.json's `segments` array is absent, empty,
    or holds an entry with no string `seg` id -- unchanged condition,
    unchanged message, and still surfaced ONLY from assert_project_complete
    (its only caller), exactly as before the #491 round-2 refactor that
    introduced this function. main()'s own call site feeding
    load_converged_segments() never calls this raising form directly --
    see _manifest_segment_ids_or_empty()'s own docstring for why raising
    from there would be a new failure mode, not merely an earlier
    surfacing of an old one."""
    segments = manifest.get("segments")
    if not isinstance(segments, list) or not segments:
        raise AssembleError(
            "manifest.json 'segments' must be a non-empty array -- refusing "
            "to assemble a book from a manifest with no segment inventory "
            "(a converged ledger alongside an empty/absent manifest segment "
            "list is a corrupt or inconsistent project state)",
            reason="malformed_manifest",
        )
    ids = set()
    for seg_entry in segments:
        seg = seg_entry.get("seg") if isinstance(seg_entry, dict) else None
        if not isinstance(seg, str):
            raise AssembleError(
                f"manifest.json 'segments' contains a malformed entry -- each "
                f"must be an object with a string 'seg' id: {seg_entry!r}",
                reason="malformed_manifest",
            )
        ids.add(seg)
    return ids


def _manifest_segment_ids_or_empty(manifest: dict) -> "set[str]":
    """Non-raising sibling of _manifest_segment_ids(), for the ONE caller
    that must never raise on a malformed manifest: main()'s own call site
    feeding load_converged_segments(). Returns _manifest_segment_ids(manifest),
    or an empty set on EITHER of two independent axes that would otherwise
    raise or crash:

      (1) `manifest` itself is not a dict -- a top-level JSON array, null,
          string, or number. manifest.json parses to whatever read_json()
          handed back, and nothing upstream of this function checks its
          type, so `manifest.get("segments")` inside _manifest_segment_ids()
          would otherwise raise an UNTYPED AttributeError that this
          function's own `except AssembleError` cannot catch (round-3
          finding, measured: a top-level `[]` produced exit 1 with
          `{"success": false, "error": "unexpected error: 'list' object
          has no attribute 'get'"}` -- no `reason` field at all -- for a
          project where nothing had converged or been refused, instead of
          the required exit 2 no_converged_segments).
      (2) `manifest` IS a dict but its `segments` value is absent, empty,
          non-list, or holds a malformed entry -- the shapes
          _manifest_segment_ids() itself raises AssembleError(reason=
          "malformed_manifest") for (round-2's original finding).

    Axis (1) is an explicit `isinstance(manifest, dict)` guard, deliberately
    NOT a widened `except (AssembleError, AttributeError)`: a broad except
    would also swallow a genuine AttributeError raised by some FUTURE edit
    inside _manifest_segment_ids()'s own extraction, silently turning a
    real bug into an empty population instead of a loud crash. The
    isinstance guard only ever fires for the one shape it names.

    WHY non-raising at all -- codex review of the FIRST round-2 patch (this
    function's own reason for existing): that patch called the raising
    _manifest_segment_ids() straight from main(), ahead of the ledger read,
    which made a malformed manifest surface as AssembleError(reason=
    "malformed_manifest", exit 1) even for a project where NOTHING has
    converged or been refused yet -- a case that, before #491 round 2 ever
    touched this code, hit main()'s own `if not converged and not refusals:
    raise AssemblePrecondition("no_converged_segments", ...)` first (exit
    2, a defined, non-fatal bootstrap state per AssemblePrecondition's own
    docstring). Silently upgrading that to a hard exit 1 is a real
    behavioral change a caller could depend on (e.g. "exit 2 means not
    ready yet, retry me"), not "the same error surfacing earlier" -- unlike
    a malformed manifest ALONGSIDE at least one converged/refused segment,
    where assert_project_complete() was always going to hit the SAME
    outcome (a typed malformed_manifest raise for axis (2), or the SAME
    untyped AttributeError crash base always had for axis (1) -- this
    function restores parity with base on axis (1), it does NOT newly type
    that crash; giving it a typed malformed_manifest reason is a separate
    change with its own blast radius, deliberately out of scope here) a
    few lines later regardless of what the loader does first.

    An empty set is safe for load_converged_segments() on EITHER axis: it
    just means every #491-carved-out stale record reads as out-of-manifest
    and gets silently skipped (see that function's own docstring), and the
    run is headed for one of the SAME pre-round-2 outcomes regardless of
    what the loader returns -- no_converged_segments via main()'s own
    early check if nothing converged or got refused, or whatever
    assert_project_complete() already did with that same malformed
    manifest otherwise. Either way, restores the exact pre-round-2
    ordering of those outcomes.

    DO NOT "tidy" this into a bare call to _manifest_segment_ids(), and do
    NOT replace the isinstance guard with a widened `except` -- either one
    silently re-opens a regression this function exists to prevent: the
    bare-call form re-flips main()'s exit code for the malformed-manifest-
    plus-nothing-converged-or-refused combination from 2 back to 1 (round
    2's finding); the widened-except form re-opens axis (1) above (round
    3's finding) AND risks masking an unrelated future AttributeError. See
    tests/stale_carveout.test.py's own dedicated exit-code-ordering
    section for the tests that pin both axes."""
    if not isinstance(manifest, dict):
        return set()
    try:
        return _manifest_segment_ids(manifest)
    except AssembleError:
        return set()


# ---------------------------------------------------------------------------
# Ledger convergence + sha1 gate.
# ---------------------------------------------------------------------------


def load_converged_segments(
    ledger: dict, manifest_seg_ids: "set[str]", admit_contract_only: bool = False
) -> "tuple[dict, dict, list]":
    """Returns `(converged, refusals, contract_admitted)`.

    `contract_admitted` (#533) is the sorted list of segment ids that reached
    `converged` through the OPT-IN contract-only acceptance path and could not
    have reached it any other way -- i.e. what this run is shipping without a
    review against the current style contract. Always empty when
    `admit_contract_only` is False. It is a LIST, not a count, because the
    operator act being recorded is "these segments", not "this many".

    `converged` is {seg: record} for every runs/ledger.json segments{} entry
    that is EITHER status=="converged" OR status=="stale", carved out by the
    #491 machinery-only carve-out above (_stale_carveout_refusal_reason
    returns None), AND (round-2 hardening below) inside `manifest_seg_ids`
    -- in every case also requiring the on-disk draft sha1 to currently
    match the record's own reviewed_draft_sha1, the same stale-review-
    detection guard this plugin's own W7 audit gate uses. A mismatch is a
    FATAL guard refusal (exit 1, via AssembleError), never a silent skip,
    for either population alike -- "a hand-edit the reviewer never saw must
    not silently ship" is the whole point of this gate, and the carve-out
    only widens WHICH records reach it, never what it does once they
    arrive.

    `manifest_seg_ids` (round-2 hardening; main()'s call site derives it via
    _manifest_segment_ids_or_empty() above -- normally the SAME set
    assert_project_complete() checks completeness against, or an empty set
    on a malformed manifest, which is safe here: see that function's own
    docstring for why the loader must never be the place a malformed
    manifest is first reported) scopes ONLY the new stale-carveout
    fall-through, never the status=="converged" branch. runs/ledger.json's
    segments{} map deliberately RETAINS historical entries for segments
    the CURRENT manifest no longer contains (see ledger_merge.py's own module
    docstring, and the mass-translate workflow template, on why merge never
    prunes). Before #491, ANY such retained "stale" entry was
    unconditionally skipped by the plain `elif status != "converged":
    continue` branch below, so a retained-but-no-longer-required fragment
    could never affect assembly. #491's carve-out widened what a "stale"
    record can become -- accepted exactly like "converged", including the
    FATAL sha1/draft-presence guards below -- which, left unscoped, would
    let a retained out-of-manifest entry that happens to qualify for the
    carve-out abort an otherwise-complete book over a segment it was never
    going to require. Scoping the fall-through to `seg in manifest_seg_ids`
    restores the pre-#491 "an out-of-manifest entry cannot newly block an
    otherwise assemblable book" invariant for this new branch, while
    leaving status=="converged" completely unscoped and unchanged -- an
    out-of-manifest CONVERGED entry hitting these same fatals is
    pre-existing behaviour, not something #491 is responsible for fixing.

    `refusals` is {seg: reason} for every "stale" entry the carve-out itself
    refused (never for pending/in_progress/non_converged/blocked/malformed
    records, and never for an out-of-manifest entry the carve-out accepted
    but this function then silently skipped -- a refusal there would name a
    segment this book does not even contain), so main() can still name a
    reason for an all-refused project instead of folding it into the
    generic "nothing has converged yet" precondition (see
    assert_project_complete)."""
    segments = ledger.get("segments") if isinstance(ledger, dict) else None
    if not isinstance(segments, dict):
        raise AssembleError("runs/ledger.json is missing its 'segments' object")

    converged = {}
    refusals = {}
    contract_admitted = []
    for seg, record in segments.items():
        if not isinstance(record, dict):
            continue
        via_contract = False
        status = record.get("status")
        if status == "stale":
            if seg not in manifest_seg_ids:
                # Round-2 hardening, moved ahead of the carve-out check
                # itself in round 3 (codex/security-review finding: this
                # membership test used to run AFTER
                # _stale_carveout_refusal_reason(), so an out-of-manifest
                # entry that FAILED the carve-out still cost a refusal --
                # and, via that function's own condition 4, a sentinel
                # lstat -- for a segment this book does not even contain).
                # A retained entry for a segment the CURRENT manifest no
                # longer requires is skipped silently, exactly as the
                # pre-#491 `elif status != "converged": continue` branch
                # always did for every stale entry regardless of its
                # shape -- never fall through to the fatal checks below
                # (which abort the WHOLE run), never call
                # _stale_carveout_refusal_reason() at all, and never
                # record a refusal (which would surface a segment this
                # book does not contain in assert_project_complete()'s own
                # diagnostics).
                continue
            reason = _stale_carveout_refusal_reason(seg, record, admit_contract_only)
            if reason is not None:
                refusals[seg] = reason
                continue
            # Carved out AND required by the current manifest -- falls
            # through to the shared checks below, exactly like
            # status=="converged".
            #
            # #533: note WHICH acceptance path this record took, so the run
            # can name what it is shipping unjudged against the current
            # contract. Re-derived from the record rather than returned by the
            # refusal function, so the two can never disagree about a record
            # the function already accepted: a record is contract-admitted
            # exactly when a field outside the machinery-only allowlist moved
            # -- which, past a `reason is None`, can only be
            # style_contract_hash under the opt-in.
            #
            # NOTED here, recorded only once the shared checks below accept
            # it. Here that ordering is invisible (those checks abort the run
            # rather than skip the segment); validate_assembled.py's sibling
            # is where it is load-bearing, and says why.
            via_contract = admit_contract_only and not set(
                record["stale_mismatched_fields"]
            ).issubset(SAFE_STALE_CARVEOUT_FIELDS)
        elif status != "converged":
            continue
        expected_sha1 = record.get("reviewed_draft_sha1")
        if not expected_sha1:
            raise AssembleError(
                f"runs/ledger.json segment {seg!r} has status={status!r} but "
                f"no reviewed_draft_sha1 recorded -- cannot confirm the "
                f"reviewer actually saw the current draft"
            )
        dp = draft_path(seg)
        if not dp.is_file():
            raise AssembleError(
                f"runs/ledger.json segment {seg!r} has status={status!r} but "
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
        if via_contract:
            contract_admitted.append(seg)
    return converged, refusals, sorted(contract_admitted)


def assert_project_complete(manifest: dict, converged: dict, refusals: dict) -> None:
    """W9's whole-project completeness gate (SKILL.md "W9 Assemble";
    references/assembly-and-output.md Path 2). Refuse to assemble unless
    EVERY manifest.segments[] unit is converged. manifest.segments[] is the
    single required-unit population -- it already includes translate-decision
    FRONTBACK:{id} units (each such entry's `seg` IS "FRONTBACK:{id}"), which
    share the one seg-id namespace with body segments and are ledgered
    identically, so a plain membership test over manifest.segments[] covers
    front/back matter too. `converged` is exactly the units accepted by
    load_converged_segments() -- status=="converged", or (#491)
    status=="stale" with a materialized `stale_mismatched_fields` that is
    non-empty, entirely inside SAFE_STALE_CARVEOUT_FIELDS, and a
    `.ever_converged` sentinel that is not ABSENT -- in every case with an
    on-disk draft sha1 still matching reviewed_draft_sha1. This reads the
    SAME materialized runs/ledger.json snapshot final_audit.py's own
    `count_stale_previously_converged()` carve-out reads (never an
    independent re-derivation of it: final_audit.py classifies via
    select_segments.py's own scan of manifest+segpacks, while this gate
    reads ledger.json directly) -- the two are designed, and tested
    (tests/stale_carveout.test.py's snapshot-parity fixture), to always
    agree about the same snapshot rather than to share one predicate.
    final_audit.py only prints its own summary and never persists it, and W9
    deliberately does NOT shell out to it (advisory-only, gated nothing, up
    to 300s -- a proportionality guardrail). Assembling a book from a
    not-fully-converged project is refused here (exit 2), never silently
    attempted over a partial set. A manifest whose `segments` inventory is
    absent, empty, or holds a non-object / non-string-`seg` entry is
    rejected as `malformed_manifest` (exit 1) rather than coerced into an
    empty required set (which would otherwise fail open into an empty
    "successful" book) -- via the shared _manifest_segment_ids() extraction
    (round-2 hardening; see its own docstring for why it moved out of this
    function and became shared with load_converged_segments()).

    `refusals` (#491) is load_converged_segments()'s own {seg: reason} map
    for every "stale" record the carve-out refused -- folded into the
    refusal message below so an all-refused project still names WHY, rather
    than just listing bare segment ids."""
    manifest_ids = _manifest_segment_ids(manifest)
    missing = []
    for seg in manifest_ids:
        if seg not in converged:
            reason = refusals.get(seg)
            missing.append(f"{seg} ({reason})" if reason else seg)
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


def _live_cache_key_fields(profile: dict, seg: str, globals_cache: dict) -> dict:
    """The live value of every LIVE_CHECKED_CACHE_KEY_FIELDS entry for `seg`,
    computed by calling cache_key.py's OWN field computers -- never a
    reimplementation of them, and never a re-derivation of which categories
    are eligible (the two dead ends #492's own body records: a parallel
    classifier that mis-decided a reverted key, and a `--classify-only`
    selector run that would have added a WRITE to assembly).

    `globals_cache` memoises the global fields across segments: they are pure
    functions of profile + durable_root, so computing them once per run rather
    than once per segment is what keeps this whole check at 0.2s for an
    81-segment book instead of 81x that.

    Every exception a computer can raise -- including the SystemExit
    cache_key.py's own fail() raises (cache_key.py:305-309), which is how a
    missing style_bible.md, an absent particle config and an unresolvable
    manifest source input all surface -- is converted by the caller into an
    AssembleError naming the segment and the field. "Cannot confirm this
    input" must never read as "this input is unchanged". (A PyYAML-less
    environment is NOT in that list: it halts this script far earlier, during
    the module-level `import validate_draft`, whose own preflight sys.exits
    inside the import statement.)"""
    live = {}
    segpack = None
    for field in LIVE_CHECKED_CACHE_KEY_FIELDS:
        if field in ck.PER_SEGMENT_FIELDS:
            if segpack is None:
                segpack = ck.load_segpack(DURABLE_ROOT, seg)
            live[field] = ck.PER_SEGMENT_FIELD_FUNCS[field](DURABLE_ROOT, segpack)
        else:
            if field not in globals_cache:
                globals_cache[field] = ck.GLOBAL_FIELD_FUNCS[field](
                    profile, DURABLE_ROOT
                )
            live[field] = globals_cache[field]
    return live


def _uncomputable_live_inputs(seg: str, detail: str) -> "AssembleError":
    """THE refusal for "a live cache-key input could not be computed", built
    in one place. Both raising arms below reach it: two spellings of one
    message are two things that can drift apart."""
    return AssembleError(
        f"could not recompute segment {seg!r}'s live cache-key inputs "
        f"({detail}) -- cannot confirm the book is being assembled from the "
        f"inputs it was reviewed against",
        reason="stale_live_inputs",
    )


def assert_live_inputs_match_ledger(
    converged: dict, manifest_seg_ids: "set[str]", admit_contract_only: bool = False
) -> "tuple[list, int]":
    """#492: refuse to assemble a book whose content-affecting cache-key
    inputs have MOVED since runs/ledger.json was materialized.

    Returns `(contract_admitted_live, compared_pairs)`.

    Every other gate in this script reads the ledger SNAPSHOT the last
    `ledger_merge.py` produced: `status`, `stale_mismatched_fields`, the
    stored `cache_key`. That snapshot ages. An operator who edits the
    STYLE_CONTRACT block of `style_bible.md` -- a correct, deliberate,
    R9-sanctioned edit that any consistency pass produces -- and then runs
    assembly WITHOUT re-running the merge gets a book built from records that
    still say `converged`, because nothing between the edit and the book ever
    recomputed anything. The draft sha1 guard in load_converged_segments()
    does not see it either: the drafts genuinely did not change; the standard
    they were reviewed against did. The pipeline does normally run W7 before
    W9, so the intended flow never hit this -- but nothing ENFORCED the
    ordering, and the failure was a green run, not a halt.

    This closes it by re-deriving the live values and comparing, so
    assembly's verdict is the same on both orderings. It is a READ: no
    write, no new persisted artifact, no new schema, no new flag, no
    reimplementation of `classify_converged_segment()`.

    SCOPED TO `manifest_seg_ids`, not to `converged`. runs/ledger.json retains
    historical entries for segments the CURRENT manifest no longer contains
    (see ledger_merge.py's own module docstring), and `converged` may hold
    such an entry through its unscoped status=="converged" branch. Assembly
    never ships one, and recomputing per-segment fields for a retained entry
    whose segpack is long gone would abort a book over a segment it does not
    contain -- precisely the invariant #491's round-2 hardening restored for
    the carve-out branch. Callable only AFTER assert_project_complete(),
    which guarantees both that the manifest is well-formed and that every
    manifest id is present in `converged`, so the lookup below cannot
    KeyError.

    THE CONTRACT-ONLY ARM (#533/R9) mirrors the merged path exactly, and the
    sentinel conjunct is load-bearing rather than decorative. When the
    declaration is present and `style_contract_hash` is the ONLY moved field,
    the record is admitted -- but only when its `.ever_converged.<seg>`
    sentinel is not SENTINEL_ABSENT, which is condition 4 of
    _stale_carveout_refusal_reason() and the documented contract in
    references/assembly-and-output.md. Without it the two orderings would
    disagree on a REACHABLE population: a project that converged before
    sentinels existed has status=="converged" records and no sentinels
    (backfill_ever_converged.py exists for exactly that), so the same edit
    would assemble without a merge and be refused with one. AMBIGUOUS carves
    out like PRESENT here for the same reason it does there -- reading an
    unreadable dotfile as "absent" would declare a finished book
    undeliverable.

    FAIL-CLOSED throughout: a missing or non-dict stored `cache_key` refuses
    (it cannot be read as "nothing moved"), and any failure to compute a live
    value refuses naming the segment and field. Every drifting segment is
    collected before raising, so one refusal names the whole repair job
    rather than its first item."""
    try:
        profile = ck.load_profile(DURABLE_ROOT)
    except SystemExit as exc:
        # ck.load_profile()'s own fail() -- a missing ownership marker, an
        # unresolvable owner_profile_path, or (only here) PyYAML absent. Not
        # expected: main() has already loaded the profile through vd by this
        # point. Converted anyway, because an escaping SystemExit would leave
        # this script's one-JSON-line contract unhonoured.
        raise AssembleError(
            f"could not load profile.yml to recompute the live cache-key "
            f"inputs (cache_key.py halted: {_system_exit_detail(exc)})",
            reason="stale_live_inputs",
        )
    globals_cache: dict = {}
    contract_admitted_live = []
    drifted = {}
    compared_pairs = 0

    for seg in sorted(manifest_seg_ids):
        record = converged[seg]
        stored = record.get("cache_key")
        if not isinstance(stored, dict):
            raise AssembleError(
                f"segment {seg!r} has no usable cache_key in runs/ledger.json "
                f"(missing, or not an object) -- cannot confirm its content "
                f"inputs are unchanged since the ledger was materialized; "
                f"re-run scripts/ledger_merge.py before assembling",
                reason="stale_live_inputs",
            )
        try:
            live = _live_cache_key_fields(profile, seg, globals_cache)
        except SystemExit as exc:
            # cache_key.py's own fail() path -- it has already written its
            # specific "ERROR: ..." line to stderr; the detail adds which
            # segment was being checked when it fired.
            raise _uncomputable_live_inputs(
                seg, f"cache_key.py halted: {_system_exit_detail(exc)}"
            )
        except (OSError, KeyError, TypeError, json.JSONDecodeError, ValueError) as exc:
            raise _uncomputable_live_inputs(seg, f"{type(exc).__name__}: {exc}")
        moved = [f for f in LIVE_CHECKED_CACHE_KEY_FIELDS if stored.get(f) != live[f]]
        # Counted from what was ACTUALLY compared, never from
        # len(LIVE_CHECKED_CACHE_KEY_FIELDS) -- that would be a constant, so a
        # field loop that ran zero times would still produce the total the
        # test asserts, which is precisely the vacuous green this counter
        # exists to make impossible.
        compared_pairs += len(live)
        if not moved:
            continue
        if admit_contract_only and set(moved) == {CONTRACT_ONLY_STALE_FIELD}:
            state, _detail = classify_ever_converged_sentinel(ever_converged_path(seg))
            if state != SENTINEL_ABSENT:
                contract_admitted_live.append(seg)
                continue
        drifted[seg] = moved

    if drifted:
        detail = "; ".join(
            f"{seg} ({', '.join(fields)})" for seg, fields in sorted(drifted.items())
        )
        raise AssembleError(
            f"refusing to assemble: {len(drifted)} segment(s) have "
            f"content-affecting cache-key inputs that have MOVED since "
            f"runs/ledger.json was materialized -- {detail}. The ledger still "
            f"reports them as reviewed, but the inputs they were reviewed "
            f"against are not the ones on disk now, so this book would ship "
            f"prose no reviewer judged under the current standard. Re-run "
            f"scripts/ledger_merge.py to re-classify them, then bring the "
            f"affected segments back to converged (or, for a style-contract "
            f"edit you accept, declare validation.admit_contract_only_stale "
            f"in profile.yml -- see R9).",
            reason="stale_live_inputs",
        )
    return sorted(contract_admitted_live), compared_pairs


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
    # #210 R1: optional per-block-type markdown heading level (1-6).
    # Validated HERE, independently of validate_extraction.py's own W2
    # jsonschema gate -- assemble.py is directly reachable on a resumed
    # project, so a manifest that never passed through W2 (or was hand-
    # edited after) must still be defended against a typo'd or malformed
    # heading_levels map. Absent map, or a type absent from it, both fall
    # back to level 2 at node-construction time below -- byte-identical to
    # pre-1.12.0 output.
    heading_levels = manifest.get("heading_levels") or {}
    if not isinstance(heading_levels, dict):
        raise AssembleError(
            f"manifest.json heading_levels must be an object, got "
            f"{type(heading_levels).__name__}"
        )
    heading_level_keys_allowed = heading_types | {"HEAD"}
    for level_key, level_value in heading_levels.items():
        if not isinstance(level_key, str) or not level_key:
            raise AssembleError(
                f"manifest.json heading_levels has an invalid key "
                f"{level_key!r} -- keys must be non-empty strings"
            )
        if level_key not in heading_level_keys_allowed:
            raise AssembleError(
                f"manifest.json heading_levels key {level_key!r} is not "
                f"declared in heading_types (or the built-in 'HEAD') -- add "
                f"it to heading_types or remove this heading_levels entry",
                reason="undeclared_heading_level_type",
            )
        if isinstance(level_value, bool) or not isinstance(level_value, int):
            raise AssembleError(
                f"manifest.json heading_levels[{level_key!r}] must be an "
                f"int 1..6, got {level_value!r}"
            )
        if not 1 <= level_value <= 6:
            raise AssembleError(
                f"manifest.json heading_levels[{level_key!r}] = "
                f"{level_value} is out of range -- must be 1..6"
            )

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
                    "level": heading_levels.get(raw_type, 2) if kind == "heading" else None,
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

        # -- per-verse FNREF anchor coverage (#433): a footnote anchor the
        # -- SOURCE verse carried must survive into THAT verse's translation.
        # -- The orphan-definition check above is segment-WIDE (its
        # -- seg_referenced_ns is a flat union over block text and every
        # -- verse's content), so an anchor the translation moved to a
        # -- different verse of this same segment satisfies it and the
        # -- footnote then prints on the wrong line with every gate green.
        # -- Ordered LAST so an input that also orphans a definition still
        # -- reports the older, narrower reason.
        # --
        # -- Iterates draft.verses, NOT the per-block `claims` walk above: a
        # -- verse embedded in a footnote DEFINITION is parented to a block
        # -- outside this segment's blocks[] and never appears there.
        # --
        # -- Expected comes from a sentinel scan of the source verse's
        # -- `plain_text` ALONE. _footnote_verse_cited_in_segment() unions
        # -- that scan with verse.store[].fnrefs, but there the union widens
        # -- an EXEMPTION; here it would widen what gets REFUSED, inverting
        # -- its safety direction -- and nothing validates that list
        # -- (validate_extraction.py scans plain_text), so a stale entry
        # -- naming a footnote whose anchor legitimately lives in the
        # -- surrounding prose would refuse a correct translation.
        if not verse_skip_mode:
            # Bounded by the footnotes THIS segment's segpack declares -- the
            # segpack is the translate job's contract, and it does not always
            # agree with the manifest. Measured on a real book: a manifest
            # anchored five footnotes in a verse's own parent block while that
            # segment's segpack carried neither, so the draft correctly cited
            # neither; an unbounded expectation refuses that book for anchors
            # its translator was never given. The defect this check exists for
            # is untouched -- an anchor that merely moved to another verse of
            # this segment is by construction one the segment declares.
            segpack_fn_ns = {
                f.get("n") for f in (segpack.get("footnotes") or [])
                if isinstance(f, dict)
            }
            for vid in sorted(draft_verses):
                store = verse_store_by_vid.get(vid) or {}
                expected = _fnref_numbers_in(store.get("plain_text") or "") & segpack_fn_ns
                if not expected:
                    # No source anchor recorded for this verse, no store entry
                    # at all, or none of its anchors is a footnote this segment
                    # carries -- nothing to require. Defensive by design,
                    # matching _footnote_verse_cited_in_segment.
                    continue
                # Normalize a malformed (non-object) verse entry to empty
                # rather than skipping it: skipping would let a draft that
                # is BOTH malformed and missing an anchor pass unchecked.
                # Deliberately an isinstance test, not
                # _scan_verse_content_fnrefs' `content = content or {}` -- that
                # idiom leaves a TRUTHY non-object in place and would raise
                # AttributeError on the .get() below rather than refuse. The
                # ordinary producer path cannot reach here -- validate_draft's
                # check 5 rejects a non-object verse entry -- so this is a
                # fail-CLOSED default, not a supported input shape.
                content = draft_verses.get(vid)
                if not isinstance(content, dict):
                    content = {}
                got = (
                    _fnref_numbers_in(content.get("rendered") or "")
                    | _fnref_numbers_in(content.get("literal_gloss") or "")
                )
                missing = sorted(expected - got)
                if missing:
                    raise AssembleError(
                        f"[{seg}] verse {vid} loses footnote anchor(s) "
                        f"{missing} its source verse carried: the translated "
                        f"rendered/literal_gloss cite "
                        f"{sorted(got) or 'none'} -- an anchor that survives "
                        f"elsewhere in this segment would print on the wrong "
                        f"line",
                        reason="verse_fnref_coverage",
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
                "level": None,
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
    `output.adapter_config.obsidian.mentions_section.enabled` must not be
    boolean `False`. ON BY DEFAULT (1.10.0+): an absent `mentions_section`
    block, an absent `enabled` key, or `enabled: null` all resolve to
    enabled -- an explicit `enabled: false` is the only way to opt out."""
    output_cfg = (profile or {}).get("output") or {}
    if output_cfg.get("target") != "obsidian":
        return False
    obsidian_cfg = (output_cfg.get("adapter_config") or {}).get("obsidian") or {}
    mentions_cfg = obsidian_cfg.get("mentions_section") or {}
    return mentions_cfg.get("enabled") is not False


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
    that ever needs them, and a flag-off project must incur ZERO new
    dependency surface -- `canon_senses.py` alone requires `jsonschema`,
    which assemble.py has otherwise never needed (`validate_draft.py`'s
    own profile loader is deliberately hand-rolled, jsonschema-free).

    FAIL-CLOSED, ALWAYS (codex review MAJOR-2, user-ratified): every
    failure point below -- dependency import, language-config resolution,
    canon_senses load, and `occurrence_targets.build()` itself -- raises
    unconditionally, regardless of whether `enabled` was written
    explicitly or only implied by an absent/null key. An earlier revision
    tried an implied-vs-explicit advisory-skip posture here (§O2a), but
    that posture wasn't actually achieved end to end:
    `validate_backlinks.py` (the LAST step of the same W9 chain, on the
    same default-on predicate) has no such distinction and unconditionally
    `_fatal`s (exit 2) on an identical broken dependency -- so a broken
    Mentions setup that this function let through with a warning still
    bricked the pipeline one step later. Matching `validate_backlinks.py`'s
    existing fail-closed posture here is what actually holds end to end,
    and it is simpler than the two-posture version it replaces."""
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

    # §O2b: unwrapped no longer -- a raise here previously surfaced only as
    # the generic "unexpected error" exit 1 catch-all (main()'s outermost
    # `except Exception`), with no `reason` field. Always fail-closed, same
    # as every other precondition in this function now.
    try:
        aggregate = occurrence_targets.build(manifest, canon, senses_result, language_config, nodestream)
    except Exception as exc:
        raise AssembleError(
            f"mentions_section.enabled is true but occurrence_targets.build() "
            f"failed: {type(exc).__name__}: {exc}",
            reason="mentions_occurrence_targets_failed",
        ) from exc
    nodestream["mentions"] = aggregate["eligible_by_source_form"]


def _attach_link_groups(nodestream: dict, canon: dict) -> None:
    """#588: attach `nodestream["link_groups"]` -- the `{member: primary}`
    projection of the `canon_link_groups.json` sidecar -- so
    `dispatch_adapter`'s render_obsidian.py sees which canon forms an
    upstream identity pass established as ONE referent, and can link their
    shared `canonical_target_form` instead of de-linking it.

    Same shape of plumbing as `_attach_mentions` above and for the same
    reason: the adapter contract is 4 positional args, so this data rides
    inside arg 1, attached BEFORE nodestream.json is persisted and before
    the adapter runs.

    Gated by the CALLER on `output.target == "obsidian"` only -- NOT on the
    Mentions `enabled` flag, because collision de-linking itself is not
    gated on it (#206/#207).

    ZERO new dependency surface when there is no sidecar: the loader (and
    with it `jsonschema`) is imported only once the file is known to be
    there, mirroring `_attach_mentions`'s own lazy-import discipline. A
    DANGLING SYMLINK is not "absent" -- `lexists` is what distinguishes a
    broken sidecar the operator meant to have from no sidecar at all, and
    the loader rejects it rather than silently skipping an identity pass
    they believe is applied.

    FAIL-CLOSED, like `_attach_mentions`: any load/validation failure
    raises rather than rendering with the groups silently dropped, which
    would ship a vault whose links contradict the operator's own recorded
    decision."""
    if not os.path.lexists(CANON_LINK_GROUPS_PATH):
        return

    sys.path.insert(0, str(SCRIPTS_DIR))
    try:
        import canon_link_groups
    except ImportError as exc:
        raise AssemblePrecondition(
            "dependency_precondition",
            f"{CANON_LINK_GROUPS_PATH.name} is present but canon_link_groups.py "
            f"could not be imported from {SCRIPTS_DIR}: {exc}",
        ) from exc
    except SystemExit as exc:
        raise AssemblePrecondition(
            "dependency_precondition",
            f"{CANON_LINK_GROUPS_PATH.name} is present but canon_link_groups.py "
            "halted during its own module-level dependency preflight -- "
            f"{_system_exit_detail(exc)}",
        ) from exc

    entries = (canon or {}).get("entries")
    try:
        primary_by_source_form = canon_link_groups.load_link_groups(
            CANON_LINK_GROUPS_PATH,
            entries if isinstance(entries, dict) else {},
        )
    except canon_link_groups.CanonLinkGroupsLoadError as exc:
        raise AssembleError(
            f"canon_link_groups.json failed to load: {exc}",
            reason="canon_link_groups_invalid",
        ) from exc
    if primary_by_source_form:
        nodestream["link_groups"] = primary_by_source_form


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

        # Non-raising -- see _manifest_segment_ids_or_empty()'s own
        # docstring. This must never be the place a malformed manifest is
        # first reported: assert_project_complete() below (and, when
        # nothing has converged or been refused at all, the
        # no_converged_segments precondition right after this) have to
        # keep winning first, exactly as they did before #491 round 2.
        manifest_seg_ids = _manifest_segment_ids_or_empty(manifest)
        contract_admission = admit_contract_only_stale(profile)
        converged, refusals, contract_admitted = load_converged_segments(
            ledger, manifest_seg_ids, contract_admission
        )
        if not converged and not refusals:
            raise AssemblePrecondition(
                "no_converged_segments",
                "runs/ledger.json has zero segments with status=converged -- "
                "nothing to assemble yet",
            )

        assert_project_complete(manifest, converged, refusals)

        # #492. Deliberately AFTER the completeness gate, not before it: an
        # incomplete project must keep getting its own project_incomplete
        # diagnostic first (it is the coarser precondition, and the one the
        # operator can act on), and it then pays no recompute at all. Nothing
        # is assembled between the two calls, so the ordering costs no
        # safety. assert_project_complete() having passed is also what lets
        # the check index `converged[seg]` for every manifest id.
        # `manifest_seg_ids` (not a second _manifest_segment_ids() call): once
        # assert_project_complete() has passed, the non-raising extraction
        # above and its strict sibling return the SAME set -- the only case
        # they differ in is the malformed manifest that gate has just refused.
        live_contract_admitted, _compared_pairs = assert_live_inputs_match_ledger(
            converged, manifest_seg_ids, contract_admission
        )
        if live_contract_admitted:
            # One population, one list: to the operator these are the same
            # thing the #533 disclosure below already describes -- "assembled
            # without a review against the current style contract" -- and
            # splitting them across two keys would make the operator read two
            # lists to answer one question.
            contract_admitted = sorted(
                set(contract_admitted) | set(live_contract_admitted)
            )

        if contract_admitted:
            # #533. Printed HERE -- after the completeness gate has passed and
            # before a single byte of the book is built -- because this is the
            # one line that says what the operator's declaration actually
            # bought on THIS run. Never printed when the declaration is absent
            # or nothing qualified: a gate that announces itself on every run
            # trains the reader to skip it.
            print(
                f"\nCONTRACT-ONLY STALE ADMITTED ({len(contract_admitted)}) -- "
                f"profile.yml declares validation.admit_contract_only_stale, so "
                f"these segments are being assembled although the style contract "
                f"moved after they converged. Their drafts are unchanged since "
                f"review (already verified against reviewed_draft_sha1) and their "
                f".ever_converged sentinels are not ABSENT -- an unreadable or dangling "
                f"one carves out like a present one. What they have NOT had is "
                f"a review against the CURRENT contract. If the contract edit "
                f"REVERSED a rule rather than adding one, re-review them instead "
                f"of shipping this run.",
                file=sys.stderr,
            )
            for seg in contract_admitted:
                print(f"  ~ {seg}", file=sys.stderr)

        canon = {"entries": {}, "review_queue": []}
        if CANON_PATH.is_file():
            canon = read_json(CANON_PATH, "canon.json")

        nodestream, anchor_map = build_nodestream(profile, manifest, converged)

        # #588: link groups gate on the TARGET alone, not on the Mentions
        # flag -- collision de-linking, which is what a group modifies, is
        # itself decoupled from that flag (#206/#207). Attached here, next
        # to the mentions data and for the same reason: before persistence,
        # before the adapter.
        #
        # #497: attached BEFORE _attach_mentions, which is the whole reason
        # this block moved above it. occurrence_targets.build() now reads
        # nodestream["link_groups"] to decide whether a fold-key collision
        # carries a complete one-referent ruling, so the map has to be on the
        # nodestream by the time _attach_mentions calls it. Ordering only --
        # _effective_mentions_enabled already requires output.target ==
        # "obsidian", so neither block's own gate changes, and a project
        # without a sidecar is untouched either way.
        if ((profile or {}).get("output") or {}).get("target") == "obsidian":
            _attach_link_groups(nodestream, canon)

        # D1 (lt-appendix-backlink-integrity, ON BY DEFAULT for
        # output.target: obsidian -- see _effective_mentions_enabled):
        # attach the source-anchored Mentions data BEFORE nodestream.json
        # is persisted below -- the e2e three-view parity test reads this
        # exact "persisted mentions" view back off disk. An explicit
        # `enabled: false`, or any other output.target: attaches nothing,
        # touches no new dependency, byte-identical to 1.7.0.
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
    if contract_admitted:
        # #533. Present only when the declaration is on AND something actually
        # qualified, so an undeclared run's stdout keys are unchanged and a
        # consumer cannot read an empty list as "we checked and there were
        # none" on a run where nothing was ever checked.
        result["contract_stale_admitted"] = contract_admitted
    print(dumps_line(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
