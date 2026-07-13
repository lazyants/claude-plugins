#!/usr/bin/env python3
"""Readiness probe: has the codex translator finished WRITING the draft?

Distinct from validate_draft.py (which judges QUALITY). This script only
answers "did codex deliver a structurally complete draft file", used to POLL
between the async codex translate stage and the review stage so review never
starts on a missing/partial draft (and a Claude fix agent never ends up
authoring a missing translation from scratch -- codex must translate).

Fully generic across projects/languages/verse-policy modes -- no per-project
adapt point. EXCLUDED from `plugin_bundle_hash` (it never gates cache reuse);
covered instead by `orchestration_bundle_hash`, which is diagnostic-only.

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

Usage: python3 draft_ready.py SEG [--expect-token TOK]
"""
import argparse
import json
import re
import sys
from pathlib import Path

# Self-anchored: this script lives at ${durable_root}/scripts/draft_ready.py,
# so parents[1] is the durable root. Never assumes cwd, never takes a
# --durable-root flag.
DURABLE_ROOT = Path(__file__).resolve().parents[1]
SEGMENTS_DIR = DURABLE_ROOT / "segments"

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


def draft_path(seg: str) -> Path:
    return SEGMENTS_DIR / f"{seg}.draft.json"


def segpack_path(seg: str) -> Path:
    return SEGMENTS_DIR / f"segpack_{seg}.json"


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
    return parser


def main() -> None:
    args = build_arg_parser().parse_args()
    seg = args.seg
    _seg_err = validate_seg(seg)
    if _seg_err:
        print(f"Error: {_seg_err}", file=sys.stderr)
        sys.exit(2)

    dp = draft_path(seg)
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

    sp = segpack_path(seg)
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
            print(
                f"[{seg}] not ready: dispatch_token mismatch "
                f"(draft={token!r}, expected={args.expect_token!r}) -- "
                f"stale/straggler draft from a different run"
            )
            sys.exit(1)

    print(f"[{seg}] READY (delivered)")
    sys.exit(0)


if __name__ == "__main__":
    main()
