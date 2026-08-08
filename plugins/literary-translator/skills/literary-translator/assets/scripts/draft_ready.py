#!/usr/bin/env python3
"""Readiness probe: has the codex translator finished WRITING the draft?

Distinct from validate_draft.py (which judges QUALITY). This script only
answers "did codex deliver a structurally complete draft file", used to POLL
between the async codex translate stage and the review stage so review never
starts on a missing/partial draft (and a Claude fix agent never ends up
authoring a missing translation from scratch -- codex must translate).

Fully generic across projects/languages/verse-policy modes -- no per-project
adapt point. EXCLUDED from `plugin_bundle_hash` (it never gates cache reuse);
covered instead by `orchestration_bundle_hash` -- non-gating for convergence
but gating for resume (folded into resume_setup.py's resume-integrity
digest).

Canonical paths (no target-language suffix, unlike the real reference
project's own `.ru.draft.json` naming) -- matches segpack.py/validate_draft.py
exactly (see references/ledger-and-resumability.md's canonical-path
invariants):
  draft:   ${durable_root}/segments/{seg}.draft.json
  segpack: ${durable_root}/segments/segpack_{seg}.json

Exit 0 = delivered: file exists, valid JSON, draft.schema.json/
segpack.schema.json container SHAPE is valid (right types, not just right
keys), the draft's own 'seg' field equals the requested seg (guards a
mislabeled/cross-wired draft -- #178), block/footnote/verse KEY SETS match
the segpack 1:1, AND (when --expect-token is given) the draft's own
dispatch_token field equals it exactly. Exit 1 = not ready yet, or the
segpack/draft itself is missing/invalid/schema-malformed (prints the reason
either way). Exit 2 = usage error.

--expect-token TOK (1.2.0 addition, optional): closes the resume-integrity
gap where a stale/straggler draft from a DIFFERENT run (or a pre-1.2.0
draft with no dispatch_token at all) would otherwise look READY. Omit for
the pre-1.2.0 behavior (no token check) -- backward compatible.

1.21.0 addition, #438: a token mismatch's refusal message additionally
consults this run's OWN claim record for the segment (best-effort, never
fatal -- see _claim_note() below). When one is present, the mismatch is not
a generic stale/straggler draft: it means a claim THIS run made was LOST,
almost always by a fix round that failed to copy the draft's dispatch_token
byte for byte (see mass-translate-wf.template.js's fixPrompt()). The
refusal names the claim's profile and claim time instead of leaving the
operator to guess, and points at the re-claim that restores it -- which is
idempotent, not a second authorization.

Exactly TWO states degrade silently to the original pre-#438 message:
CLAIM_ABSENT (the ordinary stale/straggler case the plain message already
describes correctly) and claim_record.py not being co-located, which every
caller that predates #438 still is. An unreadable or ambiguous record, a
run component that is not usable as a claim-path component, and any
unexpected failure of the lookup itself each get their OWN clause -- see
_claim_note()'s own docstring for why an anomaly must never be reported
with the same silence as an absence.

Usage: python3 draft_ready.py SEG [--expect-token TOK] [--durable-root PATH]

Self-anchoring by default: this script always lives at
${durable_root}/scripts/draft_ready.py and derives durable_root from its own
path -- it never assumes cwd. #412 prerequisite: an explicit
`--durable-root PATH` overrides this, REPLACING the self-anchored root
entirely for DATA (segments/) -- both draft_path()/segpack_path() already
take `segments_dir` as an explicit parameter, so this only changes what
main() passes in, never what a given set of on-disk inputs resolves to.
Omitting the flag reproduces today's self-anchored behavior byte-for-byte.

This script is a LEAF: it shells out to nothing, so there is no
--plugin-root concern here at all, unlike select_segments.py/ledger_merge.py/
resume_setup.py/review_ready.py, each of which resolves at least one sibling
script and so needs that second, independent override. Adding --plugin-root
here regardless would be a flag accepted and never read -- see
references/gotchas.md §4 for the full two-flag rationale this script
deliberately does NOT need.
"""
import argparse
import json
import re
import sys
from pathlib import Path

# Self-anchored by default: this script always lives at
# ${durable_root}/scripts/draft_ready.py, so parents[1] is the durable root.
# Never assumes cwd. These module-level constants are the fallback used
# whenever --durable-root is omitted (see resolve_dirs() below).
DURABLE_ROOT = Path(__file__).resolve().parents[1]
SEGMENTS_DIR = DURABLE_ROOT / "segments"


def resolve_dirs(durable_root_str):
    """#412 prerequisite: `durable_root_str` governs DATA (segments/) --
    rebuilt from that root when given, self-anchored otherwise. This script
    is a LEAF (see module docstring), so there is only ever the one root to
    resolve -- no separate --plugin-root concern. `durable_root_str=None`
    reproduces today's exact self-anchored values."""
    if durable_root_str is None:
        return {"durable_root": DURABLE_ROOT, "segments_dir": SEGMENTS_DIR}
    root = Path(durable_root_str).resolve()
    return {"durable_root": root, "segments_dir": root / "segments"}

# Canonical segment-id safety contract. A seg id is either an ordinary body
# id (e.g. "seg01", "seg05_blocked_regen", "segAnchor") or a translate-decision
# FRONTBACK:{id} unit (e.g. "FRONTBACK:fm01"). It is spliced into filesystem
# paths and workflow shell commands, so it MUST be a path- and shell-safe
# allowlist. Keep this identical across every consuming script.
# NOTE: re.fullmatch (NOT re.match + "$") -- in Python "$" also matches just
# before a trailing newline, so re.match(r"...$", "seg01\n") would WRONGLY pass.
_SEG_ID_RE = re.compile(r"(?:FRONTBACK:)?[A-Za-z0-9_]+")


def validate_seg(seg):
    """Return an error string if `seg` is not a path/shell-safe segment id,
    else None. Allows ONLY [A-Za-z0-9_] with an optional literal 'FRONTBACK:'
    prefix -- rejecting empties, path separators, '..', absolute paths, and
    every shell metacharacter."""
    if not isinstance(seg, str) or not seg:
        return "segment id must be a non-empty string."
    if not _SEG_ID_RE.fullmatch(seg):
        return (
            "segment id must match (FRONTBACK:)?[A-Za-z0-9_]+ (no path "
            f"separators, '..', or shell metacharacters); got {seg!r}."
        )
    return None


def draft_path(seg: str, segments_dir: Path = SEGMENTS_DIR) -> Path:
    return segments_dir / f"{seg}.draft.json"


def segpack_path(seg: str, segments_dir: Path = SEGMENTS_DIR) -> Path:
    return segments_dir / f"segpack_{seg}.json"


# ---------------------------------------------------------------------------
# Hand-rolled structural self-checks -- no jsonschema dependency (matches
# validate_draft.py's own check_draft_structure and the real source
# project's dependency-free scripts). These exist so a schema-invalid draft
# or segpack (wrong container type, missing required key) is refused with a
# named reason instead of silently degrading into an empty/matching
# container via .get(key, {})/.get(key, []) -- which is exactly how a
# schema-incomplete draft or segpack used to slip through as READY.
# ---------------------------------------------------------------------------

# (key, container_type, item_type, description) -- draft.schema.json's own
# container shapes.
_DRAFT_CONTAINER_SPECS = [
    ("blocks", dict, str, "object of string values"),
    ("footnotes", dict, str, "object of string values"),
    ("verses", dict, dict, "object of object values"),
    ("names", list, dict, "array of objects"),
    ("notes", list, str, "array of strings"),
]
_DRAFT_REQUIRED_KEYS = ["seg"] + [spec[0] for spec in _DRAFT_CONTAINER_SPECS]


def check_draft_structure(draft) -> list:
    """Structural self-check against draft.schema.json's container shape.
    Returns a list of error strings (empty = shape-valid)."""
    if not isinstance(draft, dict):
        return [f"draft.schema.json: draft root must be an object, got {type(draft).__name__}"]

    errs = [
        f"draft.schema.json: missing required key {k!r}"
        for k in _DRAFT_REQUIRED_KEYS if k not in draft
    ]
    if errs:
        # Can't safely type-check keys that aren't even present.
        return errs

    if not isinstance(draft["seg"], str):
        errs.append("draft.schema.json: 'seg' must be a string")

    for key, container_type, item_type, desc in _DRAFT_CONTAINER_SPECS:
        value = draft[key]
        if not isinstance(value, container_type):
            errs.append(f"draft.schema.json: {key!r} must be an {desc}")
            continue
        items = value.values() if container_type is dict else value
        if not all(isinstance(item, item_type) for item in items):
            errs.append(f"draft.schema.json: {key!r} must be an {desc}")

    return errs


# (key, item_key, item_key_type, description) -- segpack.schema.json's own
# blocks[]/footnotes[]/verses[] array shapes, restricted to the fields this
# script's key-set comparison actually reads (id/n/vid). Full segpack shape
# validation (title/kind/word_count/canon_names/etc.) is segpack.py's own
# job at write time, not this readiness probe's.
_SEGPACK_ARRAY_SPECS = [
    ("blocks", "id", str, "array of objects each with a string 'id'"),
    ("footnotes", "n", int, "array of objects each with an integer 'n'"),
    ("verses", "vid", str, "array of objects each with a string 'vid'"),
]


def check_segpack_structure(segpack) -> list:
    """Structural self-check against segpack.schema.json's blocks/footnotes/
    verses array shape (the three containers this script's readiness
    comparison reads). Returns a list of error strings (empty = shape-valid)."""
    if not isinstance(segpack, dict):
        return [f"segpack.schema.json: segpack root must be an object, got {type(segpack).__name__}"]

    errs = [
        f"segpack.schema.json: missing required key {k!r}"
        for k, _, _, _ in _SEGPACK_ARRAY_SPECS if k not in segpack
    ]
    if errs:
        return errs

    for key, item_key, item_key_type, desc in _SEGPACK_ARRAY_SPECS:
        value = segpack[key]
        if not isinstance(value, list):
            errs.append(f"segpack.schema.json: {key!r} must be an {desc}")
            continue
        for item in value:
            if not isinstance(item, dict) or item_key not in item or not isinstance(item[item_key], item_key_type):
                errs.append(f"segpack.schema.json: {key!r} must be an {desc}")
                break

    return errs


# ---------------------------------------------------------------------------
# #438 -- claim-aware enrichment of the --expect-token mismatch refusal.
# Deliberately best-effort and NEVER fatal: this script stays a LEAF for
# every consumer that predates #438 (it shells out to nothing, and every
# import here is optional). claim_record.py is the SHARED module #438
# introduced (its own docstring documents the "flat sibling-import idiom
# already used for cache_key.py" -- this is the third reader it names).
# ---------------------------------------------------------------------------

def _claim_run_id(token):
    """The RUN_ID out of a dispatch token shaped <RUN_ID>:<seg>[:r<label>],
    split on the FIRST colon only -- mirrors select_segments.py's own
    draft_run_id() derivation byte for byte (duplicated, not imported: this
    script is a LEAF and does not resolve sibling SCRIPTS, only the shared
    claim_record MODULE below). A RUN_ID can never contain ':'
    (resume_setup.py's validate_run_id() rejects it), while a seg id can
    (FRONTBACK:errata_02 is a real, shipped shape), so splitting on the
    first ':' is the only correct partition. Returns None on anything
    malformed -- this is purely a message-enrichment lookup key, never a
    security decision, so a None here just means the note stays empty."""
    if not isinstance(token, str):
        return None
    run_id, sep, rest = token.partition(":")
    if not sep or not run_id or not rest:
        return None
    return run_id


def _claim_note(run_id, seg, durable_root):
    """Best-effort clause to append to a dispatch_token-mismatch refusal
    when this run's OWN claim record explains it. NEVER fatal and never
    able to change this probe's exit code: enriching a message must not be
    able to crash the check it enriches, so every failure below becomes
    TEXT rather than an exception -- and only the two states listed further
    down (claim_record.py not co-located; CLAIM_ABSENT) return "" and leave
    the plain pre-#438 message standing unchanged.

    CLAIM_PRESENT is the only state worth a positive claim: it means THIS
    run legitimately claimed `seg`, so a token that no longer matches is
    the claim being LOST after the fact (most likely a fix round that did
    not preserve dispatch_token byte for byte), not a foreign straggler.
    CLAIM_AMBIGUOUS still gets a clause -- silently dropping a genuine
    anomaly (an unreadable record) would hide it from the one operator
    positioned to investigate -- but per claim_record.py's own documented
    discipline it is reported as unreadable, never asserted as a claim.
    CLAIM_ABSENT says nothing: that is the ordinary pre-#438 stale/
    straggler case and the plain message already covers it correctly.

    NOTHING HERE RETURNS "" FOR AN ANOMALY ANY MORE. The only two states
    that produce an empty clause are the two that genuinely have nothing to
    say: claim_record.py is not co-located (a pre-#438 deployment, which
    cannot have claims at all), and CLAIM_ABSENT. Every other outcome --
    an unusable run component, an unreadable record, an unexpected failure
    of the lookup itself -- gets a clause of its own. An anomaly that
    returned "" was reported identically to "no claim exists", which is
    the same collapse of "cannot tell" into "nothing there" that
    claim_record.py's three-state predicate exists to prevent one layer
    down; reproducing it in the layer that PRINTS the result would hide
    the anomaly from the one operator positioned to act on it.
    """
    try:
        import claim_record  # sibling module, #438 -- optional at runtime
    except ImportError:
        return ""

    # VALIDATED BEFORE THE PATH IS BUILT, not after. `run_id` was partitioned
    # out of --expect-token by _claim_run_id() above, and --expect-token is a
    # free-form caller-supplied string with no schema `pattern` anywhere
    # behind it -- so `--expect-token /tmp/x:seg_001` used to build
    # /tmp/x/.claimed.seg_001, read whatever regular file happened to sit
    # there, and echo its `profile` and `claimed_at` to stdout as if they
    # described this run's own claim. claim_record.claimed_path() now refuses
    # such a value by raising, but a refusal reached through an exception is
    # not the same as never asking: checking here keeps the ANSWER specific
    # (this token's run component is unusable) instead of collapsing into the
    # generic lookup-failed clause below. Refusing an odd value costs nothing
    # -- this note is message enrichment and never a decision.
    problem = claim_record.validate_run_id(run_id)
    if problem is not None:
        return (
            f" -- NOTE: no claim record was consulted, because the expected "
            f"token's own run component {run_id!r} is not usable as one "
            f"({problem}) -- nothing was read from disk under that name"
        )

    try:
        runs_dir = durable_root / "runs"
        path = claim_record.claimed_path(run_id, seg, runs_dir)
        state, payload, detail = claim_record.read_claim_record(path)
    except Exception as exc:
        # THE NARROWING IS IN THE RESULT, NOT IN THE CLASS, and that is a
        # decision rather than an oversight. Narrowing to (OSError, ValueError)
        # and letting anything else propagate was the alternative, and it
        # breaks this helper's never-fatal contract (see this docstring's
        # opening) for no gain: an escaping exception replaces the readiness
        # probe's own "[seg] not ready: ..." line with a traceback at the SAME
        # exit code 1, so the poller reading that line loses its only
        # diagnostic and gains nothing -- the enrichment lookup would have
        # taken down the check it was enriching. What actually changed is that
        # an unexpected failure is now REPORTED instead of being mapped onto
        # the same "" that CLAIM_ABSENT returns, which is the collapse the
        # finding was about. The
        # enumerated shapes are already handled without reaching here --
        # classify_claim_record() absorbs every lstat OSError into
        # CLAIM_AMBIGUOUS and read_claim_record() absorbs the read/parse
        # failures, and claimed_path()'s ValueError is pre-empted by the
        # validation above -- so reaching this line at all is itself the
        # anomaly worth naming.
        return (
            f" -- WARNING: this run's claim state for {seg!r} could not be "
            f"determined ({exc!r}); treated as NOT claimed (the safe "
            f"direction), never assumed claimed"
        )

    if state == claim_record.CLAIM_PRESENT:
        profile = payload.get("profile") if isinstance(payload, dict) else None
        claimed_at = payload.get("claimed_at") if isinstance(payload, dict) else None
        return (
            f" -- a claim record for run {run_id!r} IS present for this "
            f"segment (profile={profile!r}, claimed_at={claimed_at!r}): the "
            f"claim was LOST, not merely absent -- something after the "
            f"claim (most likely a fix round) overwrote or dropped the "
            f"draft's dispatch_token instead of preserving it byte for "
            f"byte. To restore it, re-run select_segments.py's claim step "
            f"for {seg} under that same profile with --run-id {run_id!r}: "
            f"the re-claim is admitted on the strength of the record above, "
            f"so the draft's missing or foreign dispatch_token is not what "
            f"blocks it, and it re-stamps the token. Re-claiming a segment "
            f"this run already holds a record for is idempotent and is not "
            f"a second authorization."
        )
    if state == claim_record.CLAIM_AMBIGUOUS:
        return (
            f" -- WARNING: this run's claim record for {seg!r} is "
            f"unreadable ({detail}); treated as NOT claimed (the safe "
            f"direction), never assumed claimed"
        )
    return ""


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Readiness probe for segments/{seg}.draft.json -- see this file's own module docstring.",
    )
    parser.add_argument("seg", help="Segment identifier.")
    parser.add_argument(
        "--expect-token",
        default=None,
        metavar="TOK",
        help=(
            "When given, READY additionally requires the draft's own "
            "dispatch_token field to equal TOK exactly (RUN_ID:seg form) -- "
            "closes a stale/straggler-draft-from-an-old-run gap. Omit for "
            "the pre-1.2.0 behavior (no token check)."
        ),
    )
    parser.add_argument(
        "--candidate-file",
        default=None,
        metavar="PATH",
        help=(
            "When given, read the draft from PATH instead of the canonical "
            "segments/{seg}.draft.json -- lets the W5 codex_job.py driver "
            "FULLY validate an isolated attempt artifact BEFORE promoting it "
            "to canonical (1.4.7, #198). The segpack is STILL read from its "
            "canonical path, and every existing check (schema shape, seg "
            "field == seg, key sets, --expect-token) runs against the "
            "candidate. Omit for today's canonical-path behavior."
        ),
    )
    parser.add_argument(
        "--durable-root",
        default=None,
        metavar="PATH",
        help=(
            "#412 prerequisite: use PATH as the durable root instead of "
            "this script's own self-anchored location. Optional; omit for "
            "today's self-anchored behavior. This script is a LEAF (shells "
            "out to nothing), so there is no companion --plugin-root flag."
        ),
    )
    return parser


def main() -> None:
    args = build_arg_parser().parse_args()
    seg = args.seg
    _seg_err = validate_seg(seg)
    if _seg_err:
        print(f"Error: {_seg_err}", file=sys.stderr)
        sys.exit(2)

    dirs = resolve_dirs(args.durable_root)
    segments_dir = dirs["segments_dir"]

    dp = Path(args.candidate_file) if args.candidate_file else draft_path(seg, segments_dir)
    if not dp.exists() or dp.stat().st_size == 0:
        print(f"[{seg}] not ready: draft file absent/empty ({dp})")
        sys.exit(1)
    try:
        draft = json.loads(dp.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[{seg}] not ready: draft not valid JSON ({e})")
        sys.exit(1)

    draft_errs = check_draft_structure(draft)
    if draft_errs:
        print(f"[{seg}] not ready: draft not schema-valid ({'; '.join(draft_errs)})")
        sys.exit(1)

    # #178: check_draft_structure only type-checks 'seg' (must be a str); it
    # never compares it to the requested seg. A mislabeled/cross-wired draft
    # would otherwise read READY. Struct check above guarantees draft["seg"]
    # is a str here.
    if draft["seg"] != seg:
        print(
            f"[{seg}] not ready: draft 'seg' is {draft['seg']!r}, expected {seg!r} "
            f"(mislabeled/cross-wired draft)"
        )
        sys.exit(1)

    sp = segpack_path(seg, segments_dir)
    try:
        segpack = json.loads(sp.read_text(encoding="utf-8"))
    except FileNotFoundError:
        print(f"[{seg}] not ready: segpack missing ({sp}) -- run segpack.py first")
        sys.exit(1)
    except Exception as e:
        print(f"[{seg}] not ready: segpack not valid JSON ({e})")
        sys.exit(1)

    segpack_errs = check_segpack_structure(segpack)
    if segpack_errs:
        print(f"[{seg}] not ready: segpack not schema-valid ({'; '.join(segpack_errs)})")
        sys.exit(1)

    # segpack's blocks/footnotes/verses are ARRAYS of objects keyed by
    # id/n/vid respectively (segpack.schema.json); the draft mirrors them as
    # DICTS keyed by the same ids (draft.schema.json). Readiness = the two
    # key sets match exactly -- no more, no less.
    want_b = {b["id"] for b in segpack.get("blocks", [])}
    want_f = {str(f["n"]) for f in segpack.get("footnotes", [])}
    want_v = {v["vid"] for v in segpack.get("verses", [])}

    # JSON object keys are always strings post-parse, so plain set() suffices
    # for all three -- no str() cast needed on footnote keys.
    got_b = set(draft.get("blocks", {}))
    got_f = set(draft.get("footnotes", {}))
    got_v = set(draft.get("verses", {}))

    if got_b != want_b or got_f != want_f or got_v != want_v:
        print(
            f"[{seg}] not ready: key sets incomplete "
            f"(blocks {len(got_b)}/{len(want_b)}, "
            f"footnotes {len(got_f)}/{len(want_f)}, "
            f"verses {len(got_v)}/{len(want_v)})"
        )
        sys.exit(1)

    if args.expect_token is not None:
        token = draft.get("dispatch_token")
        if token != args.expect_token:
            note = ""
            run_id = _claim_run_id(args.expect_token)
            if run_id is not None:
                note = _claim_note(run_id, seg, dirs["durable_root"])
            print(
                f"[{seg}] not ready: dispatch_token mismatch "
                f"(draft={token!r}, expected={args.expect_token!r}) -- "
                f"stale/straggler draft from a different run{note}"
            )
            sys.exit(1)

    print(f"[{seg}] READY (delivered)")
    sys.exit(0)


if __name__ == "__main__":
    main()
