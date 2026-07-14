#!/usr/bin/env python3
"""review_ready.py -- readiness probe for the async codex reviewer's
review.json artifact.

NEW in 1.2.0, part of the #97/#88 hardening: review is now a fire-and-
forget codex dispatch (reviewDispatchPrompt, schema-less, atomic write)
followed by a bounded Claude poll of THIS script (reviewWaitPrompt) --
mirroring translate's own established dispatch+bounded-poll discipline, so
review no longer depends on a synchronous agent() return that a detached
forwarder job could hang on indefinitely. See references/engine-loop.md
and references/false-green-gate.md's sibling script, draft_ready.py, whose
role this script plays for review.json instead of draft.json.

CLI:

    python3 review_ready.py SEG --expect-token TOK

Exit 0 = READY:
  1. segments/{seg}.review.json exists, parses as JSON, and validates
     FULLY against review.schema.json (via the real jsonschema library --
     unlike draft_ready.py's hand-rolled probe, review.schema.json is a
     flat, fully-enumerable shape with no verse_policy-style conditionals,
     so there is no need to hand-roll a second parallel structural check;
     this matches ledger_update.py's/canon_validate.py's own jsonschema
     usage).
  2. Its `draft_sha1` field equals a FRESH shell-out to draft_sha1.py {seg}
     -- the sole sha1 authority for draft files, never independently
     recomputed here.
  3. Its `dispatch_token` field equals --expect-token EXACTLY.

Exit 1 = not ready yet (prints the specific reason as one JSON line, same
as exit 0's success line). Exit 2 = usage error (bad args/segment id).

--expect-token TOK (REQUIRED, unlike draft_ready.py's optional
--expect-token): closes the resume-integrity gap where a stale/straggler
review.json from a DIFFERENT run (or a pre-1.2.0 review.json with no
dispatch_token at all, which fails review.schema.json's now-required
dispatch_token field before the token comparison is even reached) would
otherwise look READY however plausible its other fields are.

Carries the byte-identical `_SEG_ID_RE`/`validate_seg()` copy from
draft_ready.py (this project's "no shared lib between self-contained
scripts" convention) and calls validate_seg(seg) FIRST, before any path is
built.

Self-anchored: this script always lives at
${durable_root}/scripts/review_ready.py, so parents[1] is the durable
root. Never assumes cwd, never takes a --durable-root flag -- see
references/ledger-and-resumability.md's "Script self-anchoring" invariant.

Part of `plugin_bundle_hash` (see cache_key.py's own PLUGIN_BUNDLE_MEMBERS
and its comment there for why this joins the gating bundle rather than
orchestration_bundle_hash's bucket -- non-gating for convergence but gating
for resume, since its marker is folded into resume_setup.py's
resume-integrity digest -- unlike draft_ready.py).
"""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

try:
    import jsonschema
    import jsonschema.validators
except ImportError as e:
    print(json.dumps({
        "ready": False,
        "reason": (
            "missing required dependency 'jsonschema' (>=4.26.0). Install "
            f"with: pip install -r requirements.txt (import error: {e})"
        ),
    }))
    sys.exit(1)

# Self-anchored: this script always lives at
# ${durable_root}/scripts/review_ready.py, so parents[1] is the durable
# root. Never assumes cwd, never takes a --durable-root flag.
_SCRIPT_FILE = Path(__file__).resolve()
SCRIPTS_DIR = _SCRIPT_FILE.parent
DURABLE_ROOT = _SCRIPT_FILE.parents[1]
SEGMENTS_DIR = DURABLE_ROOT / "segments"
SCHEMAS_DIR = DURABLE_ROOT / "schemas"
DRAFT_SHA1_SCRIPT = SCRIPTS_DIR / "draft_sha1.py"

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


def review_path(seg):
    return SEGMENTS_DIR / f"{seg}.review.json"


def _not_ready(seg, reason):
    print(json.dumps({"ready": False, "reason": f"[{seg}] {reason}"}))
    sys.exit(1)


def _load_review_schema():
    """Returns (schema_dict, None) or (None, error_message) -- never
    raises. A missing/malformed review.schema.json is reported through the
    same "not ready" JSON-line channel as every other failure reason here,
    never a bare traceback."""
    path = SCHEMAS_DIR / "review.schema.json"
    if not path.is_file():
        return None, f"review.schema.json not found at {path}"
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except json.JSONDecodeError as exc:
        return None, f"review.schema.json at {path} is not valid JSON: {exc}"


def _current_draft_sha1(seg):
    """Shells out to draft_sha1.py -- the sole sha1 authority for draft
    files -- rather than independently recomputing a hash here. Returns
    (digest, None) or (None, error_message)."""
    if not DRAFT_SHA1_SCRIPT.is_file():
        return None, f"{DRAFT_SHA1_SCRIPT} not found"
    try:
        proc = subprocess.run(
            [sys.executable, str(DRAFT_SHA1_SCRIPT), seg],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, f"could not run draft_sha1.py: {exc}"
    if proc.returncode != 0:
        return None, f"draft_sha1.py exited {proc.returncode}: {proc.stderr.strip()}"
    digest = proc.stdout.strip()
    if not digest:
        return None, "draft_sha1.py printed an empty value"
    return digest, None


def build_arg_parser():
    parser = argparse.ArgumentParser(
        description=(
            "Readiness probe for segments/{seg}.review.json -- see this "
            "file's own module docstring."
        ),
    )
    parser.add_argument("seg", help="Segment identifier.")
    parser.add_argument(
        "--expect-token",
        required=True,
        metavar="TOK",
        help=(
            "The current run's expected dispatch_token "
            "(RUN_ID:seg:rN form, roundLabel = the round number or "
            "'final'). REQUIRED -- a review.json missing or mismatching "
            "this is never READY."
        ),
    )
    return parser


def main():
    args = build_arg_parser().parse_args()

    seg = args.seg
    seg_err = validate_seg(seg)
    if seg_err:
        print(f"Error: {seg_err}", file=sys.stderr)
        sys.exit(2)

    rpath = review_path(seg)
    if not rpath.exists() or rpath.stat().st_size == 0:
        _not_ready(seg, f"review file absent/empty ({rpath})")

    try:
        review = json.loads(rpath.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _not_ready(seg, f"review not valid JSON ({exc})")

    schema, err = _load_review_schema()
    if err is not None:
        _not_ready(seg, f"internal error: {err}")

    validator_cls = jsonschema.validators.validator_for(schema)
    validator_cls.check_schema(schema)
    validator = validator_cls(schema)
    errors = sorted(validator.iter_errors(review), key=str)
    if errors:
        detail = "; ".join(e.message for e in errors)
        _not_ready(seg, f"review not schema-valid against review.schema.json ({detail})")

    reviewer_sha1 = review.get("draft_sha1") if isinstance(review, dict) else None
    current_sha1, err = _current_draft_sha1(seg)
    if err is not None:
        _not_ready(seg, f"could not verify draft_sha1 ({err})")
    if reviewer_sha1 != current_sha1:
        _not_ready(
            seg,
            f"draft_sha1 mismatch (review={reviewer_sha1!r}, "
            f"current={current_sha1!r}) -- draft changed since review, or "
            f"review is stale",
        )

    token = review.get("dispatch_token") if isinstance(review, dict) else None
    if token != args.expect_token:
        _not_ready(
            seg,
            f"dispatch_token mismatch (review={token!r}, "
            f"expected={args.expect_token!r}) -- stale/straggler review "
            f"from a different run",
        )

    print(json.dumps({"ready": True}))
    sys.exit(0)


if __name__ == "__main__":
    main()
