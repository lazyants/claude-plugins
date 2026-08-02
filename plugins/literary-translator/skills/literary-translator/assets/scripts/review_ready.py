#!/usr/bin/env python3
"""review_ready.py -- readiness probe for the async codex reviewer's
review.json artifact.

NEW in 1.2.0, part of the #97/#88 hardening; the review dispatch was
rebuilt in 1.4.7 (#198): review is launched by the shipped codex_job.py
driver -- a DETACHED (nohup) process that runs codex, validates codex's
isolated attempt, and only then atomically promotes it to the canonical
review.json -- followed by a bounded Claude poll of THIS script
(reviewWaitPrompt). codex writes disk; its RETURN is not the verdict --
this probe reading the promoted canonical review.json is. That mirrors
translate's own driver+bounded-poll discipline, so review no longer
depends on a synchronous agent() return that a detached forwarder job
could hang on indefinitely. See references/engine-loop.md and
references/false-green-gate.md's sibling script, draft_ready.py, whose
role this script plays for review.json instead of draft.json.

CLI:

    python3 review_ready.py SEG --expect-token TOK [--candidate-file PATH]
        [--durable-root PATH] [--plugin-root PATH]

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

Self-anchored by default: this script always lives at
${durable_root}/scripts/review_ready.py, so parents[1] is the durable
root. Never assumes cwd. LT-409 (post-review correction): --durable-root
PATH and --plugin-root PATH are TWO INDEPENDENT overrides. --durable-root
governs DATA (SCHEMAS_DIR/SEGMENTS_DIR). --plugin-root governs where the
sibling draft_sha1.py script is found, as
{PATH}/assets/scripts/draft_sha1.py -- deliberately NEVER derived from
--durable-root, because ${durable_root}/scripts/ is writable by the codex
process this review-readiness gate protects (codex_job.py grants --write
over the whole durable root), so resolving the checker from inside the
thing it checks would let a tampered copy pass itself. Each flag, when
given, is forwarded to the draft_sha1.py subprocess as its own same-named
flag. Omitting BOTH reproduces today's self-anchored behavior byte-for-byte
-- see references/ledger-and-resumability.md's "Script self-anchoring"
invariant.

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
from typing import NoReturn

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

# Self-anchored by default: this script always lives at
# ${durable_root}/scripts/review_ready.py, so parents[1] is the durable
# root. Never assumes cwd. These module-level constants are the fallback
# used whenever --durable-root is omitted (see resolve_dirs() below for the
# LT-409 override path).
_SCRIPT_FILE = Path(__file__).resolve()
SCRIPTS_DIR = _SCRIPT_FILE.parent
DURABLE_ROOT = _SCRIPT_FILE.parents[1]
SEGMENTS_DIR = DURABLE_ROOT / "segments"
SCHEMAS_DIR = DURABLE_ROOT / "schemas"
DRAFT_SHA1_SCRIPT = SCRIPTS_DIR / "draft_sha1.py"


def resolve_dirs(durable_root_str, plugin_root_str=None):
    """LT-409: `durable_root_str` governs DATA (segments/schemas) -- rebuilt
    from that root when given, self-anchored otherwise.

    `plugin_root_str` is a SEPARATE, independent input governing where the
    draft_sha1.py SIBLING SCRIPT this script shells out to is resolved from
    -- deliberately NEVER derived from `durable_root_str`:
    ${durable_root}/scripts/ is a Step-0a copy the codex process can write
    to (codex_job.py runs it with --write over the whole durable root), so
    resolving the checker from inside the thing it checks would let a
    tampered copy validate itself. When given, it resolves as
    `{plugin_root}/assets/scripts/draft_sha1.py` -- the SAME layout
    SKILL.md documents for the plugin-anchored scripts, NOT durable_root's
    own flattened `scripts/draft_sha1.py` copy layout. `plugin_root_str=None`
    reproduces today's self-anchored sibling lookup unchanged.

    Returns a dict with keys durable_root/scripts_dir/segments_dir/
    schemas_dir/draft_sha1_script. Both None -> today's exact self-anchored
    values for both concerns.
    """
    if durable_root_str is None:
        durable_root = DURABLE_ROOT
        segments_dir = SEGMENTS_DIR
        schemas_dir = SCHEMAS_DIR
    else:
        durable_root = Path(durable_root_str).resolve()
        segments_dir = durable_root / "segments"
        schemas_dir = durable_root / "schemas"

    if plugin_root_str is None:
        scripts_dir = SCRIPTS_DIR
        draft_sha1_script = DRAFT_SHA1_SCRIPT
    else:
        scripts_dir = Path(plugin_root_str).resolve() / "assets" / "scripts"
        draft_sha1_script = scripts_dir / "draft_sha1.py"

    return {
        "durable_root": durable_root,
        "scripts_dir": scripts_dir,
        "segments_dir": segments_dir,
        "schemas_dir": schemas_dir,
        "draft_sha1_script": draft_sha1_script,
    }

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


def review_path(seg, segments_dir=SEGMENTS_DIR):
    return segments_dir / f"{seg}.review.json"


def _not_ready(seg, reason) -> NoReturn:
    print(json.dumps({"ready": False, "reason": f"[{seg}] {reason}"}))
    sys.exit(1)


def _load_review_schema(schemas_dir=SCHEMAS_DIR):
    """Returns (schema_dict, None) or (None, error_message) -- never
    raises. A missing/malformed review.schema.json is reported through the
    same "not ready" JSON-line channel as every other failure reason here,
    never a bare traceback."""
    path = schemas_dir / "review.schema.json"
    if not path.is_file():
        return None, f"review.schema.json not found at {path}"
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except json.JSONDecodeError as exc:
        return None, f"review.schema.json at {path} is not valid JSON: {exc}"


def _current_draft_sha1(
    seg,
    draft_sha1_script=DRAFT_SHA1_SCRIPT,
    durable_root_str=None,
    durable_root=DURABLE_ROOT,
    plugin_root_str=None,
):
    """Shells out to draft_sha1.py -- the sole sha1 authority for draft
    files -- rather than independently recomputing a hash here. Returns
    (digest, None) or (None, error_message).

    LT-409: `draft_sha1_script` is the resolved sibling path to invoke --
    self-anchored by default, or resolve_dirs()'s own --plugin-root-aware
    `{plugin_root}/assets/scripts/draft_sha1.py` (never derived from
    durable_root; see resolve_dirs()'s own docstring for why).
    `durable_root_str`/`plugin_root_str` are THIS script's own CLI values
    (draft_sha1.py has no --plugin-root, being a leaf with no siblings of
    its own): `durable_root_str` is forwarded verbatim as draft_sha1.py's
    own --durable-root when given; when it is NOT given but
    `plugin_root_str` IS (meaning `draft_sha1_script` was itself resolved
    via --plugin-root, so it no longer physically sits under durable_root),
    `durable_root` is forwarded explicitly anyway -- otherwise
    draft_sha1.py's own self-anchoring would silently resolve its data from
    the plugin root instead of the real durable root."""
    if not draft_sha1_script.is_file():
        return None, f"{draft_sha1_script} not found"
    cmd = [sys.executable, str(draft_sha1_script), seg]
    if durable_root_str is not None:
        cmd += ["--durable-root", durable_root_str]
    elif plugin_root_str is not None:
        cmd += ["--durable-root", str(durable_root)]
    try:
        proc = subprocess.run(
            cmd,
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
    parser.add_argument(
        "--candidate-file",
        default=None,
        metavar="PATH",
        help=(
            "When given, read the review artifact from PATH instead of the "
            "canonical segments/{seg}.review.json -- lets the W5 codex_job.py "
            "driver FULLY validate an isolated attempt BEFORE promoting it to "
            "canonical (1.4.7, #198). draft_sha1.py {seg} STILL runs against "
            "the CURRENT canonical draft (the review must reference the "
            "on-disk draft); schema + --expect-token run against the "
            "candidate. Omit for today's canonical-path behavior."
        ),
    )
    parser.add_argument(
        "--durable-root",
        default=None,
        metavar="PATH",
        help=(
            "LT-409: use PATH as the DATA root instead of this script's own "
            "self-anchored location -- replaces where segments/ and "
            "schemas/ are found (including the draft_sha1.py subprocess's "
            "own data), forwarded to it as its own --durable-root. "
            "Optional; omit for today's self-anchored behavior. Independent "
            "of --plugin-root below -- never affects where the SIBLING "
            "SCRIPT itself is found."
        ),
    )
    parser.add_argument(
        "--plugin-root",
        default=None,
        metavar="PATH",
        help=(
            "LT-409: use PATH (the plugin's own install root, i.e. "
            "{{PLUGIN_ROOT}}) to resolve the sibling draft_sha1.py script "
            "this script shells out to, as {PATH}/assets/scripts/"
            "draft_sha1.py -- deliberately NEVER derived from "
            "--durable-root, because ${durable_root}/scripts/ is writable "
            "by the codex process this review-readiness gate protects "
            "(codex_job.py grants --write over the whole durable root), so "
            "resolving the checker from inside the thing it checks would "
            "let a tampered copy pass itself. Optional; omit for today's "
            "self-anchored sibling lookup."
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

    dirs = resolve_dirs(args.durable_root, args.plugin_root)

    rpath = Path(args.candidate_file) if args.candidate_file else review_path(seg, dirs["segments_dir"])
    if not rpath.exists() or rpath.stat().st_size == 0:
        _not_ready(seg, f"review file absent/empty ({rpath})")

    try:
        review = json.loads(rpath.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _not_ready(seg, f"review not valid JSON ({exc})")

    schema, err = _load_review_schema(dirs["schemas_dir"])
    if err is not None or schema is None:
        _not_ready(seg, f"internal error: {err}")

    validator_cls = jsonschema.validators.validator_for(schema)
    validator_cls.check_schema(schema)
    validator = validator_cls(schema)
    errors = sorted(validator.iter_errors(review), key=str)
    if errors:
        detail = "; ".join(e.message for e in errors)
        _not_ready(seg, f"review not schema-valid against review.schema.json ({detail})")

    reviewer_sha1 = review.get("draft_sha1") if isinstance(review, dict) else None
    current_sha1, err = _current_draft_sha1(
        seg, dirs["draft_sha1_script"], args.durable_root, dirs["durable_root"], args.plugin_root
    )
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
