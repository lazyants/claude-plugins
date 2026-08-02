#!/usr/bin/env python3
"""select_segments.py -- the W5 mass-translate preflight / resumability gate.

Part of the literary-translator plugin's ledger/resumability subsystem (see
references/ledger-and-resumability.md). This subsystem -- the per-segment
fragment ledger, the atomic writer, the merge/stale materializer, the
composite cache key, and this classification gate -- is NEW plugin
hardening layered on top of the source-proven historiettes-t3 engine loop.
It has not yet been run at scale; treat it as a careful first design, not
as something already proven surprise-free.

STATUS: non-gating for convergence but gating for resume (folded into
resume_setup.py's resume-integrity digest) -- covered by
`orchestration_bundle_hash` (never `plugin_bundle_hash`), and never itself a
member of the 15-field `cache_key` composite. It classifies; it never writes
a ledger fragment.

What it does (SKILL.md's W5 "Mass-translate" section, authoritative):

  1. Runs `ledger_merge.py` (bare, no --expected-* flag) to materialize the
     current `runs/ledger.json` from whatever fragments exist under
     `runs/ledger.d/*.json`.
  2. Reads the full candidate segment-id list from `manifest.json`'s
     `segments[]`.
  3. For each candidate, classifies into exactly one of six categories by
     comparing the ledger's recorded state against freshly recomputed
     truth (`cache_key.py --seg <id>`, the current on-disk draft's sha1,
     and -- only when needed to resolve the derivation-state gate --
     `segments/segpack_{seg}.json`'s own `generation_hashes`):

       - reusable                  -- converged, cache key AND draft sha1
                                       both still match. Skip.
       - stale                     -- converged, but the cache key and/or
                                       the draft sha1 no longer match.
                                       Needs a fresh translate/review/fix
                                       pass. Records which trigger(s) fired
                                       in `stale_reason`
                                       (`cache_key_mismatch` and/or
                                       `draft_sha1_mismatch`). A
                                       draft_sha1_mismatch-triggered stale
                                       is NEVER reclassified as
                                       blocked_needs_regeneration -- the two
                                       gates are independent.
       - blocked_needs_regeneration -- converged, cache-key mismatch is
                                       confined to one or more of the four
                                       derivation-state fields
                                       (particle_config_hash,
                                       source_extraction_hash,
                                       source_input_hash,
                                       derivation_bundle_hash), draft sha1
                                       still matches, AND the segpack has
                                       not yet caught up with the current
                                       value of at least one of those
                                       fields. Excluded from SEGS,
                                       self-clearing once the named
                                       regeneration step reruns, never a
                                       manual-override target.
       - recoverable                -- an `in_progress` (or any other
                                       non-terminal) fragment exists --
                                       treated like not_started for
                                       dispatch, counted separately for
                                       visibility.
       - not_started                -- no fragment at all.
       - human_escalation           -- fragment status is `blocked` or
                                       `non_converged` (or its cache key
                                       could not be recomputed at all,
                                       e.g. a missing segpack) -- excluded
                                       from automatic re-dispatch by
                                       default.

  4. Emits `SEGS = not_started UNION recoverable UNION stale` (excluding
     reusable, human_escalation, blocked_needs_regeneration), plus the full
     per-segment classification report. This is the exact list that must
     become `mergeLedgerPrompt`'s `--expected-segs` later -- no drift
     between the dispatch decision and the completeness check.

CLI flags:

    --only-segs <comma-list>
        Intersects the emitted SEGS with this explicit id list (for
        operator-paced batches). Also the sole mechanism for retrying a
        human_escalation segment: naming a currently blocked/non_converged
        id here is an explicit, auditable override -- included in SEGS
        despite its classification, logged as an override.
        blocked_needs_regeneration is never overridable this way (it is
        self-clearing, not a human decision); a reusable segment named here
        is also not force-included (--only-segs narrows, it does not force
        a cache-valid segment to redo). Omitting --only-segs entirely
        reproduces default behavior byte-for-byte.
        FATALS if any named id is not present in manifest.json's
        segments[] at all -- names every unrecognized id, never silently
        drops them.

    --allow-empty
        Without this flag, an empty emitted SEGS is a FATAL error (guards
        against a silently-no-op mass-translate run). With it, an empty
        SEGS is reported normally -- for a deliberately narrow rerun that
        happens to select nothing right now.

Every invocation logs the requested ids (or "<all candidates>" when
--only-segs was omitted) alongside the actually-emitted SEGS ids, to
stderr, for audit.

Self-anchoring by default: this script always lives at
``${durable_root}/scripts/select_segments.py`` and derives durable_root
from its own path -- it never assumes cwd. LT-409: two INDEPENDENT,
orthogonal flags override this, deliberately kept separate:

  --durable-root PATH   the DATA root (manifest.json, segments/, runs/).
  --plugin-root PATH    where the SIBLING SCRIPTS this script shells out
                         to (ledger_merge.py, cache_key.py) are resolved
                         from, as ``{PATH}/assets/scripts/<name>.py``.

A single root cannot serve both roles: ``${durable_root}/scripts/`` is a
Step-0a copy that the codex process (via codex_job.py's ``--write`` over
the whole durable root) can write to, so resolving the checker scripts
FROM there would let a tampered copy validate itself -- exactly the
vulnerability this flag split exists to close. Each flag, independently,
is forwarded down the whole subprocess chain
(select_segments.py -> ledger_merge.py -> cache_key.py) as the sibling's
own same-named flag. Omitting BOTH reproduces today's self-anchored
behavior byte-for-byte.

Output: exactly one JSON object on stdout. Success:
{"success": true, "durable_root": ..., "segs": [...],
 "requested_only_segs": [...] | null, "classification": {seg: {...}},
 "counts": {...}, "ids_by_category": {category: [seg, ...]},
 "overrides": [...], "excluded_only_segs": [...]}. `counts` and
 `ids_by_category` are keyed by the same six ALL_CATEGORIES, one the
 per-category tally and the other the per-category segment-id list (each
 stale segment's own `stale_reason` lives inline in `classification`) --
 together this is the "classification report" the build spec requires
 (counts + IDs per category + stale_reason).
Failure: {"success": false, "error": ...}. Exit 0 on success, 1 on any
fatal condition -- callers should read stdout, not rely on the exit code
alone.
"""

import argparse
import hashlib
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import NoReturn

# ---------------------------------------------------------------------------
# Self-anchoring
# ---------------------------------------------------------------------------

SCRIPTS_DIR = Path(__file__).resolve().parent
DURABLE_ROOT = SCRIPTS_DIR.parent

MANIFEST_PATH = DURABLE_ROOT / "manifest.json"
LEDGER_MERGE_SCRIPT = SCRIPTS_DIR / "ledger_merge.py"
CACHE_KEY_SCRIPT = SCRIPTS_DIR / "cache_key.py"


def resolve_dirs(durable_root_str, plugin_root_str=None):
    """LT-409: `durable_root_str` governs DATA (manifest.json) -- rebuilt
    from that root when given, self-anchored DURABLE_ROOT/MANIFEST_PATH
    otherwise.

    `plugin_root_str` is a SEPARATE, independent input governing where the
    SIBLING SCRIPTS this script shells out to (ledger_merge.py, cache_key.py)
    are resolved from -- deliberately NEVER derived from `durable_root_str`.
    `${durable_root}/scripts/` is copied there by Step 0a and is writable by
    the codex process these scripts gate (codex_job.py runs it with --write
    over the whole durable root) -- resolving the checker from inside the
    thing it checks would let a tampered durable-root copy silently pass
    itself. When given, a sibling resolves as
    `{plugin_root}/assets/scripts/<name>.py` -- the SAME layout SKILL.md
    documents for the plugin-anchored scripts (profile_validate.py etc.,
    see SKILL.md's Step 0/W2 sections), NOT durable_root's own flattened
    `scripts/<name>.py` copy layout (Step 0a strips the `assets/` prefix on
    copy). `plugin_root_str=None` reproduces today's self-anchored sibling
    lookup (`Path(__file__).resolve().parent`) unchanged.

    Both None -> today's exact self-anchored values for both concerns.
    """
    if durable_root_str is None:
        durable_root = DURABLE_ROOT
        manifest_path = MANIFEST_PATH
    else:
        durable_root = Path(durable_root_str).resolve()
        manifest_path = durable_root / "manifest.json"

    if plugin_root_str is None:
        ledger_merge_script = LEDGER_MERGE_SCRIPT
        cache_key_script = CACHE_KEY_SCRIPT
    else:
        plugin_scripts_dir = Path(plugin_root_str).resolve() / "assets" / "scripts"
        ledger_merge_script = plugin_scripts_dir / "ledger_merge.py"
        cache_key_script = plugin_scripts_dir / "cache_key.py"

    return {
        "durable_root": durable_root,
        "manifest_path": manifest_path,
        "ledger_merge_script": ledger_merge_script,
        "cache_key_script": cache_key_script,
    }


def _root_forward_args(dirs: dict, durable_root_str, plugin_root_str) -> list:
    """LT-409: the exact --durable-root/--plugin-root pair to forward to a
    sibling subprocess.

    Whenever --plugin-root redirects where a sibling's OWN script file is
    found, that sibling's self-anchored DATA resolution breaks too -- its
    __file__ no longer sits under durable_root, so its own
    Path(__file__).resolve().parents[1] would silently point at the plugin
    root instead. So an explicit --durable-root MUST be forwarded whenever
    --plugin-root is given, even if THIS script itself was never passed
    --durable-root (and is itself self-anchored) -- using the resolved
    dirs["durable_root"] as the value. Omitting BOTH flags never forwards
    anything, preserving today's self-anchored behavior on both sides.
    """
    args = []
    if durable_root_str is not None:
        args += ["--durable-root", durable_root_str]
    elif plugin_root_str is not None:
        args += ["--durable-root", str(dirs["durable_root"])]
    if plugin_root_str is not None:
        args += ["--plugin-root", plugin_root_str]
    return args


# Canonical paths (references/ledger-and-resumability.md's "Canonical path
# invariants" -- deliberately WITHOUT a target-language suffix, unlike the
# real historiettes-t3 reference project's own .ru.draft.json naming).


def draft_path(seg: str, durable_root: Path = DURABLE_ROOT) -> Path:
    return durable_root / "segments" / f"{seg}.draft.json"


def segpack_path(seg: str, durable_root: Path = DURABLE_ROOT) -> Path:
    return durable_root / "segments" / f"segpack_{seg}.json"


# The authoritative 15-field cache-key list (references/ledger-and-
# resumability.md, "Composite cache key -- exact 15-field structure";
# mirrors cache_key.py's own CACHE_KEY_FIELD_ORDER and ledger_merge.py's own
# CACHE_KEY_FIELDS literal -- kept as an independent restatement per this
# project's "no shared lib between self-contained scripts" convention).
CACHE_KEY_FIELDS = [
    "input_sha1",
    "style_contract_hash",
    "used_terms_hash",
    "pipeline_version",
    "schema_hash",
    "prompt_hash",
    "agent_config_hash",
    "profile_semantics_hash",
    "particle_config_hash",
    "source_extraction_hash",
    "source_input_hash",
    "derivation_bundle_hash",
    "verse_map_hash",
    "note_map_hash",
    "plugin_bundle_hash",
]

# The four "flag-only, needs regeneration" derivation-state fields (see
# "Derivation-state gate" in ledger-and-resumability.md). A cache-key
# mismatch confined to these does not by itself prove segpack_{seg}.json
# has caught up -- that must be checked separately against the segpack's
# own recorded generation_hashes.
DERIVATION_STATE_FIELDS = frozenset(
    {
        "particle_config_hash",
        "source_extraction_hash",
        "source_input_hash",
        "derivation_bundle_hash",
    }
)

def _w3_regen_step(field: str) -> str:
    """The W3/W3a remedy, generated once for every derivation-state field
    whose regeneration routes through the W3 glossary pass.

    Both such fields share one remedy AND one hole. `particle_config_hash`
    flips when the resolved particle config file's bytes change;
    `derivation_bundle_hash` flips when bootstrap_names.py's or segpack.py's
    bytes change (cache_key.py's DERIVATION_BUNDLE_MEMBERS). Either way the
    fix is the same chain, and either way it dead-ends the same way: the
    glossary pass only re-stamps canon.json when it actually MERGES
    something, and a mature project with zero unresolved candidates skips
    that pass entirely -- so for exactly that project the remedy is
    unreachable and the block never clears (#193). 1.15.0 (#291) additionally
    removed the undocumented `--merge-batches <empty-batch.json>` restamp
    that had been its only unsanctioned way out, so the hint must name the
    sanctioned replacement.

    Two orderings are load-bearing, not cosmetic. bootstrap_names.py comes
    FIRST: jumping straight to the glossary pass when only its bytes changed
    would consume stale name_candidates.json rows and still re-stamp, quietly
    papering over the staleness. segpack.py comes LAST: it copies
    canon.json's stamp forward rather than recomputing it, so running it
    before the restamp merely re-copies the stale value.

    Generated rather than spelled out per field because these were two
    hand-maintained strings -- which is exactly how the escape came to be
    added to one and forgotten on the other.
    """
    return (
        "W3/W3a (re-run bootstrap_names.py to regenerate name candidates, "
        f"then the glossary pass to re-stamp canon.json's {field} -- or, on "
        "a project with no new candidates left to merge, canon_validate.py "
        "--restamp-derivation -- then segpack.py)"
    )


# The W2 remedy. One literal for both W2 fields for the same reason
# _w3_regen_step() exists: two identical hand-maintained strings is how the
# W3 pair drifted, one gaining the restamp escape while the other silently
# did not. These two need no escape (they are re-run by the extractor at W2,
# never by the glossary pass), but they get one source of truth anyway --
# the point is that editing this remedy is a single edit, always.
_W2_REGEN_STEP = "W2 (re-run the source-format extractor)"

# Actionable "what to rerun" message per derivation-state field, per
# ledger-and-resumability.md's own wording.
FIELD_TO_REGEN_STEP = {
    "source_extraction_hash": _W2_REGEN_STEP,
    "source_input_hash": _W2_REGEN_STEP,
    "particle_config_hash": _w3_regen_step("particle_config_hash"),
    "derivation_bundle_hash": _w3_regen_step("derivation_bundle_hash"),
}

# Fragment statuses that mean "a human must resolve this before automated
# re-dispatch" (ledger.schema.json's status enum, minus the ones this
# script handles specially: converged/stale -> classify_converged_segment,
# everything else non-terminal -> recoverable).
HUMAN_ESCALATION_STATUSES = frozenset({"blocked", "non_converged"})

# Statuses ledger_merge.py may hand back for a segment that WAS converged
# at write time (a plain 'converged' if nothing has drifted, or 'stale' --
# computed by ledger_merge.py itself, never written to an on-disk fragment
# -- if at least one cache-key field has drifted since).
WAS_CONVERGED_STATUSES = frozenset({"converged", "stale"})


class FatalError(Exception):
    """Raised for any failure that should surface as a top-level FAILURE
    JSON payload on stdout (exit 1), never a bare traceback."""


def fatal(message: str, **extra) -> NoReturn:
    raise FatalError(json.dumps({"success": False, "error": message, **extra}))


def draft_content_sha1(path: Path) -> str:
    """sha1 of a draft's CONTENT, with the 'dispatch_token' metadata field
    deliberately EXCLUDED -- see draft_sha1.py's own module docstring for why.

    Must match, byte for byte, draft_sha1.py's and ledger_update.py's own
    draft_content_sha1() -- both parse the draft as JSON, drop
    'dispatch_token' if present, and re-serialize the remainder via
    identical sorted-key canonical JSON before hashing.

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


def read_json(path: Path, what: str):
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        fatal(f"{what} not found at {path}")
    except OSError as exc:
        fatal(f"could not read {what} at {path}: {exc}")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        fatal(f"{what} at {path} is not valid JSON: {exc}")


def read_segpack_nonfatal(seg: str, durable_root: Path = DURABLE_ROOT) -> "dict | str":
    """Read segments/segpack_{seg}.json for the derivation-state gate,
    returning the parsed dict on success or a string error message on
    failure -- NEVER raising/exiting. A per-segment segpack gone unreadable
    (concurrent regeneration/cleanup, transient IO) must escalate only THAT
    segment, never abort the whole W5 preflight -- matching
    compute_current_cache_key()'s isolation contract and this file's
    "a per-segment failure must never take down the whole run" rule."""
    path = segpack_path(seg, durable_root)
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return f"segpack for segment {seg!r} not found at {path}"
    except UnicodeDecodeError as exc:
        # Invalid/truncated UTF-8 -> UnicodeDecodeError is a subclass of
        # ValueError, NOT OSError, so `except OSError` alone would miss it.
        return f"segpack for segment {seg!r} at {path} is not valid UTF-8: {exc}"
    except OSError as exc:
        return f"could not read segpack for segment {seg!r} at {path}: {exc}"
    try:
        doc = json.loads(raw)
    except json.JSONDecodeError as exc:
        return f"segpack for segment {seg!r} at {path} is not valid JSON: {exc}"
    if not isinstance(doc, dict):
        return f"segpack for segment {seg!r} at {path} is not a JSON object"
    return doc


# ---------------------------------------------------------------------------
# Segment id validation -- the SOURCE guard. Every seg id this script ever
# handles (manifest.json's segments[], and --only-segs) ends up spliced,
# unquoted, into the generated mass-translate-wf.js's shell command strings
# (see mass-translate-wf.template.js's translatePrompt/reviewPrompt/etc.), so
# a poisoned manifest or a hand-typed --only-segs value must be rejected
# HERE, before any path is built or any id is emitted into SEGS -- never
# left for the workflow's own defense-in-depth JS guard to catch first.
# Canonical allowlist, kept identical to review_artifact_check.py's own
# validate_seg() per this project's "no shared lib between self-contained
# scripts" convention.
# ---------------------------------------------------------------------------

# A seg id is either an ordinary body id (e.g. "seg01", "seg05_blocked_regen")
# or a translate-decision FRONTBACK:{id} unit (e.g. "FRONTBACK:fm01"). Using
# re.fullmatch (NOT re.match + "$") -- in Python "$" also matches just before
# a trailing newline, so re.match(r"...$", "seg01\n") would WRONGLY pass.
_SEG_ID_RE = re.compile(r"(?:FRONTBACK:)?[A-Za-z0-9_]+")


def validate_seg(seg):
    """Return an error string if `seg` is not a path/shell-safe segment id,
    else None. Allows ONLY [A-Za-z0-9_] with an optional literal
    'FRONTBACK:' prefix -- rejecting empties, path separators, '..',
    absolute paths, and every shell metacharacter."""
    if not isinstance(seg, str) or not seg:
        return "segment id must be a non-empty string."
    if not _SEG_ID_RE.fullmatch(seg):
        return (
            "segment id must match (FRONTBACK:)?[A-Za-z0-9_]+ (no path "
            f"separators, '..', or shell metacharacters); got {seg!r}."
        )
    return None


# ---------------------------------------------------------------------------
# Step 1: run ledger_merge.py (bare -- no completeness flag; this is only
# ever meant to freshly materialize runs/ledger.json, not to gate on which
# segments happen to already have a fragment).
# ---------------------------------------------------------------------------


def run_ledger_merge(dirs: dict, durable_root_str=None, plugin_root_str=None) -> dict:
    ledger_merge_script = dirs["ledger_merge_script"]
    if not ledger_merge_script.is_file():
        fatal(f"ledger_merge.py not found at {ledger_merge_script}")
    cmd = [sys.executable, str(ledger_merge_script)] + _root_forward_args(
        dirs, durable_root_str, plugin_root_str
    )
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
            cwd=str(dirs["durable_root"]),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        fatal(f"could not run ledger_merge.py: {exc}")

    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        fatal(
            "ledger_merge.py did not print valid JSON on stdout "
            f"(exit {proc.returncode}): stdout={proc.stdout!r} stderr={proc.stderr!r}"
        )

    if not isinstance(payload, dict) or not payload.get("success"):
        error = payload.get("error") if isinstance(payload, dict) else None
        fatal(
            "ledger_merge.py failed to materialize runs/ledger.json"
            + (f": {error}" if error else f" (stdout={proc.stdout!r})")
        )

    return payload


def load_ledger_segments(merge_result: dict, durable_root: Path = DURABLE_ROOT) -> dict:
    ledger_path = Path(merge_result.get("ledger_path") or (durable_root / "runs" / "ledger.json"))
    doc = read_json(ledger_path, "materialized ledger.json")
    segments = doc.get("segments")
    if not isinstance(segments, dict):
        fatal(f"materialized ledger.json at {ledger_path} has no 'segments' object")
    return segments


# ---------------------------------------------------------------------------
# Step 2: candidate segment ids from manifest.json's segments[].
# ---------------------------------------------------------------------------


def load_candidate_segments(manifest_path: Path = MANIFEST_PATH) -> list:
    manifest = read_json(manifest_path, "manifest.json")
    segments = manifest.get("segments")
    if not isinstance(segments, list):
        fatal(f"manifest.json at {manifest_path} has no 'segments' array")

    candidates = []
    for item in segments:
        # manifest.schema.json's segments[] entries are REQUIRED to be
        # objects with (at least) their own `seg` field -- a bare string
        # is not a valid entry under that schema and must be rejected
        # fatally, never silently coerced into a candidate id.
        if isinstance(item, dict) and isinstance(item.get("seg"), str):
            seg = item["seg"]
            problem = validate_seg(seg)
            if problem is not None:
                fatal(f"manifest.json: unsafe segment id: {problem}")
            candidates.append(seg)
        else:
            fatal(f"manifest.json: malformed segments[] entry: {item!r}")
    if not candidates:
        fatal(f"manifest.json at {manifest_path} has an empty 'segments' array")
    return candidates


# ---------------------------------------------------------------------------
# Step 3: per-segment classification.
# ---------------------------------------------------------------------------


def compute_current_cache_key(
    seg: str,
    cache_key_script: Path = CACHE_KEY_SCRIPT,
    durable_root: Path = DURABLE_ROOT,
    durable_root_str=None,
    plugin_root_str=None,
) -> "dict | str":
    """Runs cache_key.py --seg <id> and returns the parsed 15-field dict on
    success, or a string error message on failure (never raises/exits --
    a per-segment failure here becomes that segment's own human_escalation
    classification, it must never take down the whole run, matching
    ledger_merge.py's own "warn and continue" treatment of this exact
    subprocess call).

    LT-409: `cache_key_script` is the resolved sibling path to shell out
    against -- self-anchored by default, or resolve_dirs()'s own
    --plugin-root-aware `{plugin_root}/assets/scripts/cache_key.py` (never
    derived from durable_root; see resolve_dirs()'s own docstring for why).
    `durable_root` is cache_key.py's DATA root (cwd for the subprocess).
    `durable_root_str`/`plugin_root_str` are THIS script's own CLI values
    (not cache_key.py's -- it has no --plugin-root, being a leaf with no
    siblings of its own): `durable_root_str` is forwarded verbatim as
    cache_key.py's own --durable-root when given; when it is NOT given but
    `plugin_root_str` IS (meaning `cache_key_script` was itself resolved via
    --plugin-root, so it no longer physically sits under durable_root),
    `durable_root` is forwarded explicitly anyway -- otherwise cache_key.py's
    own self-anchoring would silently resolve its data from the plugin root
    instead of the real durable root.
    """
    if not cache_key_script.is_file():
        return f"cache_key.py not found at {cache_key_script}"
    if durable_root_str is not None:
        cmd_extra = ["--durable-root", durable_root_str]
    elif plugin_root_str is not None:
        cmd_extra = ["--durable-root", str(durable_root)]
    else:
        cmd_extra = []
    try:
        proc = subprocess.run(
            [sys.executable, str(cache_key_script), "--seg", seg, *cmd_extra],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=str(durable_root),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"could not run cache_key.py --seg {seg}: {exc}"

    if proc.returncode != 0:
        return f"cache_key.py --seg {seg} exited {proc.returncode}: {proc.stderr.strip()}"

    try:
        current_key = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return f"cache_key.py --seg {seg} did not print valid JSON: {proc.stdout!r}"

    if not isinstance(current_key, dict):
        return f"cache_key.py --seg {seg} printed a non-object JSON value"

    return current_key


def classify_converged_segment(
    seg: str, record: dict, dirs: dict, durable_root_str=None, plugin_root_str=None
) -> dict:
    """A segment whose materialized status is 'converged' or 'stale' (the
    latter meaning: ledger_merge.py itself detected a cache-key mismatch
    against a fragment that was originally written as 'converged'). Returns
    a classification dict with a 'category' key plus supporting detail.
    """
    current_key = compute_current_cache_key(
        seg, dirs["cache_key_script"], dirs["durable_root"], durable_root_str, plugin_root_str
    )
    if isinstance(current_key, str):
        return {
            "category": "human_escalation",
            "status": "cache_key_recompute_failed",
            "detail": current_key,
        }

    stored_key = record.get("cache_key")
    if not isinstance(stored_key, dict):
        # A schema-valid converged/stale record always has this; if it's
        # missing anyway, don't silently trust an anomalous record.
        return {
            "category": "human_escalation",
            "status": record.get("status"),
            "detail": "converged/stale ledger record is missing its 'cache_key' object",
        }

    mismatched = sorted(f for f in CACHE_KEY_FIELDS if stored_key.get(f) != current_key.get(f))
    cache_key_mismatch = bool(mismatched)

    dp = draft_path(seg, dirs["durable_root"])
    current_draft_sha1 = None
    if dp.is_file():
        try:
            current_draft_sha1 = draft_content_sha1(dp)
        except (OSError, json.JSONDecodeError, ValueError):
            current_draft_sha1 = None
    reviewed_draft_sha1 = record.get("reviewed_draft_sha1")
    draft_sha1_mismatch = current_draft_sha1 is None or current_draft_sha1 != reviewed_draft_sha1

    if not cache_key_mismatch and not draft_sha1_mismatch:
        return {"category": "reusable"}

    if draft_sha1_mismatch:
        # Draft-sha1-triggered staleness is never reclassified as
        # blocked_needs_regeneration -- the two gates are independent.
        stale_reason = ["draft_sha1_mismatch"]
        if cache_key_mismatch:
            stale_reason.append("cache_key_mismatch")
        return {
            "category": "stale",
            "stale_reason": stale_reason,
            "mismatched_fields": mismatched,
        }

    # cache_key_mismatch is True, draft_sha1_mismatch is False: check
    # whether the mismatch is (at least partly) a derivation-state field
    # the segpack itself hasn't caught up with yet.
    derivation_mismatched = [f for f in mismatched if f in DERIVATION_STATE_FIELDS]
    if derivation_mismatched:
        sp = read_segpack_nonfatal(seg, dirs["durable_root"])
        if isinstance(sp, str):
            return {
                "category": "human_escalation",
                "status": "segpack_read_failed",
                "detail": sp,
            }
        # generation_hashes: absent/None -> {} (segpack hasn't caught up, the
        # existing semantics); present-but-not-a-mapping is an anomalous
        # segpack and must ESCALATE, never crash (a bare `or {}` would leave
        # a wrong-type value like a list truthy, and the `.get()` below would
        # raise an uncaught AttributeError -> whole-run crash).
        segpack_gen_hashes = sp.get("generation_hashes")
        if segpack_gen_hashes is None:
            segpack_gen_hashes = {}
        elif not isinstance(segpack_gen_hashes, dict):
            return {
                "category": "human_escalation",
                "status": "segpack_read_failed",
                "detail": f"segpack for segment {seg!r} has a non-object 'generation_hashes'",
            }
        pending_fields = sorted(
            f for f in derivation_mismatched if segpack_gen_hashes.get(f) != current_key.get(f)
        )
        if pending_fields:
            steps = sorted({FIELD_TO_REGEN_STEP[f] for f in pending_fields})
            return {
                "category": "blocked_needs_regeneration",
                "pending_fields": pending_fields,
                "message": (
                    f"segment {seg!r} is blocked on regeneration: rerun "
                    + "; then rerun ".join(steps)
                    + " before this segment can be reclassified"
                ),
            }
        # Every derivation-state field that mismatched has already been
        # caught up by the segpack -- safe to reclassify as ordinary stale.

    return {
        "category": "stale",
        "stale_reason": ["cache_key_mismatch"],
        "mismatched_fields": mismatched,
    }


def classify_segment(
    seg: str, ledger_segments: dict, dirs: dict, durable_root_str=None, plugin_root_str=None
) -> dict:
    record = ledger_segments.get(seg)
    if record is None:
        return {"category": "not_started"}

    status = record.get("status")

    if status in WAS_CONVERGED_STATUSES:
        return classify_converged_segment(seg, record, dirs, durable_root_str, plugin_root_str)

    if status in HUMAN_ESCALATION_STATUSES:
        return {
            "category": "human_escalation",
            "status": status,
            "reason": record.get("reason"),
        }

    # in_progress, pending, or any other non-terminal/unrecognized status:
    # treated identically to not_started for dispatch, counted separately.
    return {"category": "recoverable", "status": status}


# ---------------------------------------------------------------------------
# Step 4: SEGS selection (default set, or --only-segs override).
# ---------------------------------------------------------------------------

# All possible classify_segment() categories, in the fixed order used for
# the `counts` field so a zeroed counter is emitted for empty categories.
ALL_CATEGORIES = (
    "reusable",
    "stale",
    "blocked_needs_regeneration",
    "recoverable",
    "not_started",
    "human_escalation",
)

DEFAULT_ELIGIBLE_CATEGORIES = frozenset({"not_started", "recoverable", "stale"})


def parse_only_segs(raw: str) -> list:
    seen = set()
    ordered = []
    for part in raw.split(","):
        part = part.strip()
        if not part or part in seen:
            continue
        seen.add(part)
        ordered.append(part)
    return ordered


def select_default(classification: dict, candidates: list) -> list:
    return [seg for seg in candidates if classification[seg]["category"] in DEFAULT_ELIGIBLE_CATEGORIES]


def select_only_segs(only_segs: list, classification: dict):
    """Returns (segs, overrides, excluded) for the --only-segs path.

    - segs: the ids actually emitted into SEGS, in the requested order.
    - overrides: ids whose classification was human_escalation but were
      force-included anyway (the sole explicit override this flag grants).
    - excluded: ids named but NOT included, each with its category and why
      (reusable segments are not force-redone; blocked_needs_regeneration
      is never a manual-override target).
    """
    segs = []
    overrides = []
    excluded = []
    for seg in only_segs:
        category = classification[seg]["category"]
        if category in DEFAULT_ELIGIBLE_CATEGORIES:
            segs.append(seg)
        elif category == "human_escalation":
            segs.append(seg)
            overrides.append(seg)
        elif category == "reusable":
            excluded.append(
                {
                    "seg": seg,
                    "category": category,
                    "reason": "reusable segments are not force-redone by --only-segs",
                }
            )
        elif category == "blocked_needs_regeneration":
            excluded.append(
                {
                    "seg": seg,
                    "category": category,
                    "reason": "blocked_needs_regeneration is self-clearing, never a manual-override target",
                }
            )
        else:  # pragma: no cover -- defensive, every category is handled above
            excluded.append({"seg": seg, "category": category, "reason": "unrecognized category"})
    return segs, overrides, excluded


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def run(args, dirs: dict) -> dict:
    candidates = load_candidate_segments(dirs["manifest_path"])
    candidate_set = set(candidates)

    only_segs = None
    if args.only_segs is not None:
        only_segs = parse_only_segs(args.only_segs)
        for seg in only_segs:
            problem = validate_seg(seg)
            if problem is not None:
                fatal(f"--only-segs: unsafe segment id: {problem}")
        unknown = [seg for seg in only_segs if seg not in candidate_set]
        if unknown:
            fatal(
                f"--only-segs names {len(unknown)} id(s) not present in "
                f"manifest.json's segments[]: {', '.join(unknown)}"
            )

    merge_result = run_ledger_merge(dirs, args.durable_root, args.plugin_root)
    ledger_segments = load_ledger_segments(merge_result, dirs["durable_root"])

    classification = {
        seg: classify_segment(seg, ledger_segments, dirs, args.durable_root, args.plugin_root)
        for seg in candidates
    }
    observed_counts = Counter(entry["category"] for entry in classification.values())
    counts = {cat: observed_counts.get(cat, 0) for cat in ALL_CATEGORIES}

    # Aggregated per-category segment-id lists (candidate order), alongside
    # `counts`' per-category numbers -- the build spec's "classification
    # report" is explicitly counts + IDs per category + each stale
    # segment's own stale_reason (the latter already lives inline in
    # `classification`).
    ids_by_category: dict = {cat: [] for cat in ALL_CATEGORIES}
    for seg in candidates:
        ids_by_category[classification[seg]["category"]].append(seg)

    if only_segs is not None:
        segs, overrides, excluded_only_segs = select_only_segs(only_segs, classification)
        requested_display = only_segs
    else:
        segs = select_default(classification, candidates)
        overrides = []
        excluded_only_segs = []
        requested_display = candidates

    print(
        f"select_segments.py: requested={requested_display} emitted={segs}",
        file=sys.stderr,
    )

    if not segs and not args.allow_empty:
        fatal(
            "emitted SEGS is empty -- refusing to no-op silently. Pass "
            "--allow-empty to confirm a deliberately narrow rerun that "
            "selects nothing.",
            classification=classification,
            counts=counts,
            ids_by_category=ids_by_category,
        )

    return {
        "success": True,
        "durable_root": str(dirs["durable_root"]),
        "segs": segs,
        "requested_only_segs": only_segs,
        "classification": classification,
        "counts": counts,
        "ids_by_category": ids_by_category,
        "overrides": overrides,
        "excluded_only_segs": excluded_only_segs,
    }


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "W5 mass-translate preflight: classify every manifest segment "
            "as reusable/stale/blocked_needs_regeneration/recoverable/"
            "not_started/human_escalation and emit the dispatch set SEGS."
        )
    )
    parser.add_argument(
        "--only-segs",
        default=None,
        metavar="SEG1,SEG2,...",
        help=(
            "Comma-separated explicit segment id list. Intersects the "
            "emitted SEGS with this list instead of the full eligible set, "
            "and is also the sole mechanism for retrying a "
            "human_escalation segment (an explicit, auditable override, "
            "logged as such)."
        ),
    )
    parser.add_argument(
        "--allow-empty",
        action="store_true",
        help="Do not fatally error if the emitted SEGS is empty.",
    )
    parser.add_argument(
        "--durable-root",
        default=None,
        metavar="PATH",
        help=(
            "LT-409: use PATH as the DATA root instead of this script's own "
            "self-anchored location -- replaces where manifest.json (and "
            "the ledger_merge.py/cache_key.py subprocesses' own data) are "
            "found, forwarded down the subprocess chain as their own "
            "--durable-root. Optional; omit for today's self-anchored "
            "behavior. Independent of --plugin-root below -- this flag "
            "never affects where the SIBLING SCRIPTS themselves are found."
        ),
    )
    parser.add_argument(
        "--plugin-root",
        default=None,
        metavar="PATH",
        help=(
            "LT-409: use PATH (the plugin's own install root, i.e. "
            "{{PLUGIN_ROOT}}) to resolve the sibling ledger_merge.py/"
            "cache_key.py scripts this script shells out to, as "
            "{PATH}/assets/scripts/<name>.py -- deliberately NEVER derived "
            "from --durable-root, because ${durable_root}/scripts/ is "
            "writable by the codex process these scripts gate (codex_job.py "
            "grants --write over the whole durable root), so resolving a "
            "checker from inside the thing it checks would let a tampered "
            "copy pass itself. Forwarded down the subprocess chain as their "
            "own --plugin-root. Optional; omit for today's self-anchored "
            "sibling lookup."
        ),
    )
    return parser


def main(argv=None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    try:
        dirs = resolve_dirs(args.durable_root, args.plugin_root)
        result = run(args, dirs)
    except FatalError as exc:
        print(str(exc), file=sys.stdout)
        return 1
    except Exception as exc:  # pragma: no cover -- defensive catch-all
        print(
            json.dumps({"success": False, "error": f"unexpected error: {exc}"}, ensure_ascii=False),
            file=sys.stdout,
        )
        return 1

    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
