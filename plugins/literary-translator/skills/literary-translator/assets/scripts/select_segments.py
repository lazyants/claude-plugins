#!/usr/bin/env python3
"""select_segments.py -- the W5 mass-translate preflight / resumability gate.

Part of the literary-translator plugin's ledger/resumability subsystem (see
references/ledger-and-resumability.md). This subsystem -- the per-segment
fragment ledger, the atomic writer, the merge/stale materializer, the
composite cache key, and this classification gate -- is NEW plugin
hardening layered on top of the source-proven historiettes-t3 engine loop.
It has not yet been run at scale; treat it as a careful first design, not
as something already proven surprise-free.

STATUS: purely diagnostic/orchestration -- covered by `orchestration_bundle_hash`
(never `plugin_bundle_hash`), and never itself a member of the 15-field
`cache_key` composite. It classifies; it never writes a ledger fragment.

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

Self-anchoring: this script always lives at
``${durable_root}/scripts/select_segments.py`` and derives durable_root
from its own path -- it never assumes cwd and never takes a
--durable-root flag.

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

# Canonical paths (references/ledger-and-resumability.md's "Canonical path
# invariants" -- deliberately WITHOUT a target-language suffix, unlike the
# real historiettes-t3 reference project's own .ru.draft.json naming).


def draft_path(seg: str) -> Path:
    return DURABLE_ROOT / "segments" / f"{seg}.draft.json"


def segpack_path(seg: str) -> Path:
    return DURABLE_ROOT / "segments" / f"segpack_{seg}.json"


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

# Actionable "what to rerun" message per derivation-state field, per
# ledger-and-resumability.md's own wording.
FIELD_TO_REGEN_STEP = {
    "source_extraction_hash": "W2 (re-run the source-format extractor)",
    "source_input_hash": "W2 (re-run the source-format extractor)",
    "particle_config_hash": "W3/W3a (re-run bootstrap_names.py, the glossary pass, then segpack.py)",
    "derivation_bundle_hash": "W3a (re-run segpack.py)",
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


def sha1_hex_bytes(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


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


def run_ledger_merge() -> dict:
    if not LEDGER_MERGE_SCRIPT.is_file():
        fatal(f"ledger_merge.py not found at {LEDGER_MERGE_SCRIPT}")
    try:
        proc = subprocess.run(
            [sys.executable, str(LEDGER_MERGE_SCRIPT)],
            capture_output=True,
            text=True,
            timeout=300,
            cwd=str(DURABLE_ROOT),
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


def load_ledger_segments(merge_result: dict) -> dict:
    ledger_path = Path(merge_result.get("ledger_path") or (DURABLE_ROOT / "runs" / "ledger.json"))
    doc = read_json(ledger_path, "materialized ledger.json")
    segments = doc.get("segments")
    if not isinstance(segments, dict):
        fatal(f"materialized ledger.json at {ledger_path} has no 'segments' object")
    return segments


# ---------------------------------------------------------------------------
# Step 2: candidate segment ids from manifest.json's segments[].
# ---------------------------------------------------------------------------


def load_candidate_segments() -> list:
    manifest = read_json(MANIFEST_PATH, "manifest.json")
    segments = manifest.get("segments")
    if not isinstance(segments, list):
        fatal(f"manifest.json at {MANIFEST_PATH} has no 'segments' array")

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
        fatal(f"manifest.json at {MANIFEST_PATH} has an empty 'segments' array")
    return candidates


# ---------------------------------------------------------------------------
# Step 3: per-segment classification.
# ---------------------------------------------------------------------------


def compute_current_cache_key(seg: str) -> "dict | str":
    """Runs cache_key.py --seg <id> and returns the parsed 15-field dict on
    success, or a string error message on failure (never raises/exits --
    a per-segment failure here becomes that segment's own human_escalation
    classification, it must never take down the whole run, matching
    ledger_merge.py's own "warn and continue" treatment of this exact
    subprocess call).
    """
    if not CACHE_KEY_SCRIPT.is_file():
        return f"cache_key.py not found at {CACHE_KEY_SCRIPT}"
    try:
        proc = subprocess.run(
            [sys.executable, str(CACHE_KEY_SCRIPT), "--seg", seg],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=str(DURABLE_ROOT),
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


def classify_converged_segment(seg: str, record: dict) -> dict:
    """A segment whose materialized status is 'converged' or 'stale' (the
    latter meaning: ledger_merge.py itself detected a cache-key mismatch
    against a fragment that was originally written as 'converged'). Returns
    a classification dict with a 'category' key plus supporting detail.
    """
    current_key = compute_current_cache_key(seg)
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

    dp = draft_path(seg)
    current_draft_sha1 = sha1_hex_bytes(dp.read_bytes()) if dp.is_file() else None
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
        sp = read_json(segpack_path(seg), f"segpack for segment {seg!r}")
        segpack_gen_hashes = sp.get("generation_hashes") or {}
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


def classify_segment(seg: str, ledger_segments: dict) -> dict:
    record = ledger_segments.get(seg)
    if record is None:
        return {"category": "not_started"}

    status = record.get("status")

    if status in WAS_CONVERGED_STATUSES:
        return classify_converged_segment(seg, record)

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


def run(args) -> dict:
    candidates = load_candidate_segments()
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

    merge_result = run_ledger_merge()
    ledger_segments = load_ledger_segments(merge_result)

    classification = {seg: classify_segment(seg, ledger_segments) for seg in candidates}
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
        "durable_root": str(DURABLE_ROOT),
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
    return parser


def main(argv=None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    try:
        result = run(args)
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
