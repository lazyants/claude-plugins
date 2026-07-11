#!/usr/bin/env python3
"""review_artifact_check.py -- the review-artifact gate's deterministic core.

Part of the literary-translator plugin's Workflow-schema-validation
subsystem (see references/workflow-schema-validation.md, section "The
review-artifact gate"). This script is a NEW plugin addition -- it has no
counterpart in the real historiettes-t3 reference project -- introduced
because `review.schema.json` carries a deliberate plugin addition
(`draft_sha1`) and a schema-validated return alone does not guarantee that
what got WRITTEN to disk at review_path(seg) actually matches the object
the calling Workflow JS received.

The gap this closes: `reviewDispatchPrompt` (1.2.0; formerly `reviewPrompt`)
writes the review verdict to disk, and a SEPARATE `readReviewPrompt` agent
call later returns a schema-validated `REVIEW_SCHEMA` object to the JS.
Nothing about that schema-validated return itself guarantees the on-disk
file matches what the JS actually received -- a stale or divergent file
could sit there unnoticed. This script performs that check,
deterministically, rather than trusting the write blindly.

1.2.0 addition -- field projection: `review.schema.json` now carries a 5th
field, `dispatch_token` (a run-scoped freshness metadata string -- see that
schema's own field description), which `REVIEW_SCHEMA` (readReviewPrompt's
agent return, the --expected-file's own source) deliberately does NOT
include. So before comparing, this script projects BOTH the on-disk review
artifact AND the expected-file's contents down to exactly the 4 verdict
fields (`clean`, `coverage_ok`, `findings`, `draft_sha1`) -- a 5-field
on-disk file still correctly MATCHES a 4-field expected object; only a
divergence in one of the 4 projected fields counts as a real mismatch.

1.3.6 addition (#132) -- per-finding projection: each `findings[]` element
is further projected down to `{loc, severity}` on BOTH sides before
comparing, dropping the free-text `issue`/`suggest` bodies. By the time
this script ever runs, `review_ready.py` already guarantees the on-disk
artifact is schema-valid, `draft_sha1`-fresh, and `dispatch_token`-matched
-- so byte-comparing free-text prose can only false-block a valid,
already-verified review over an immaterial transcription slip; it can
never catch a decision-relevant divergence that the retained structural
fields wouldn't already catch.

1.3.6 (#132 option b) -- the fix-step-input gap this projection's own
narrowing opened (a read-agent transcription slip in `issue`/`suggest`
text, while `loc`/`severity` still matched, would pass this compare and
then have the fixer apply the WRONG free-text instruction from its
in-memory copy) is now closed a DIFFERENT way, not by this compare: the
fixer (`fixPrompt` in `mass-translate-wf.template.js`) no longer consumes
that in-memory copy at all -- it reads `review_path(seg)` from disk
itself, fresh and token-validated by `review_ready.py` this same round.
This compare's remaining job is narrower than "protects the fixer": it is
the structural binding for `ledger_update.py`'s later
`reviewed_draft_sha1`/`dispatch_token` check and audit-trail inspection
(see `references/ledger-and-resumability.md`) -- a slipped, dropped, or
fabricated finding in the CONSUME agent's own transcription still fails
this compare on `loc`, `severity`, or the findings array's own length,
which is what keeps THAT later binding meaningful. Do NOT "restore" the
free-text `issue`/`suggest` compare as a future bugfix -- see
`project_verdict_fields()`'s own docstring for why that would reintroduce
exactly the false-block this projection removes, without closing any real
gap (the fix-step gap it might look like it's protecting against is
already closed by the fixer's own disk read, not by this compare).

CLI:

    python3 review_artifact_check.py {seg} --expected-file <path>

The calling agent (never the JS, which has no filesystem access) first
writes revObj's own canonical-JSON text -- already fully formed, no
hashing/formatting logic needed -- VERBATIM to a scratch file, then
invokes this script with that file's path. The agent relays this script's
own printed line, verbatim, as its schema-validated return; it never
performs the comparison itself.

This script (never the agent) does the actual work: it reads the
canonical

    review_path(seg) = {durable_root}/segments/{seg}.review.json

(deliberately WITHOUT a target-language suffix, same reasoning as
draft_path(seg): v1 has exactly one target language per project, already
recorded in profile.yml -- see references/ledger-and-resumability.md's
canonical path invariants. Unlike draft_path(seg), which DOES diverge from
the real historiettes-t3 reference project's own per-language
`.ru.draft.json` naming, this no-suffix review path is NOT a divergence --
the reference project's own review_TASK.md and
historiettes-mass-translate-wf.reference.js both already write/read
`segments/{seg}.review.json` with no target-language suffix either), then
canonicalizes both it and the --expected-file's contents via sorted-key
JSON serialization, and does a byte-for-byte comparison of the two
canonical forms -- a diff that cannot be misjudged.

On stdout: exactly one JSON line matching
review-artifact-check.schema.json:

    {"match": true}
    {"match": false, "mismatch_detail": "<first differing key/value pair, named>"}

Both are NORMAL outcomes (exit 0) -- a mismatch is a real, expected
result the caller must act on (retry the review once, then `blocked` with
reason `review-artifact-mismatch` if it still mismatches), not a script
failure. Genuine script-level failures (segment id missing or unsafe --
absolute, or containing a path separator or a '..' component --
review_path(seg) not found, either file not valid JSON) print a single
named error line to stderr and exit non-zero -- never a raw traceback, and
never a line that could be mistaken for a schema-conforming {"match": ...}
result.

Residual risk this script cannot close (see references/
workflow-schema-validation.md for the full discussion): --expected-file is
itself written by an LLM agent, not the JS or this script. If that agent
ever writes something other than revObj's own exact text, this
deterministic comparison still "correctly" matches against a WRONG
expected-file. That risk is confined to the later ledger-binding/
audit-trail question (review_path(seg)'s draft_sha1 vs. the CURRENT
draft's hash is ledger_update.py's own, separate, binding check -- this
script never reads or recomputes a draft hash at all).

Dependency-free: stdlib `json` and `re` only. No requirements.txt entry, no
dependency preflight needed.
"""

import argparse
import json
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Self-anchoring: this script always lives at {durable_root}/scripts/<name>.py.
# It never assumes cwd == durable_root, and never takes a --durable-root flag.
# ---------------------------------------------------------------------------
DURABLE_ROOT = Path(__file__).resolve().parents[1]
SEGMENTS_DIR = DURABLE_ROOT / "segments"

# Values longer than this are truncated in mismatch_detail so a long
# findings[] array or issue string doesn't blow up the printed line.
_TRUNCATE_LEN = 120

# The 4 REVIEW_SCHEMA verdict fields (readReviewPrompt's own agent return
# shape) -- both the on-disk review.json (5 fields, including 1.2.0's
# dispatch_token) and the --expected-file (already 4 fields) are projected
# down to exactly this set before comparing. See this file's own module
# docstring, "1.2.0 addition -- field projection".
REVIEW_VERDICT_FIELDS = ("clean", "coverage_ok", "findings", "draft_sha1")

# 1.3.6 (#132): each findings[] element is further projected down to just
# these two fields before comparing -- see project_verdict_fields()'s own
# docstring for why the free-text issue/suggest bodies are excluded.
_FINDING_COMPARE_FIELDS = ("loc", "severity")

# The canonical allowlist (kept identical to select_segments.py's own
# validate_seg() per this project's "no shared lib between self-contained
# scripts" convention): [A-Za-z0-9_], with an optional literal 'FRONTBACK:'
# prefix. Using re.fullmatch (NOT re.match + "$") -- in Python "$" also
# matches just before a trailing newline, so re.match(r"...$", "seg01\n")
# would WRONGLY pass.
_SEG_ID_RE = re.compile(r"(?:FRONTBACK:)?[A-Za-z0-9_]+")


def validate_seg(seg):
    """Reject any seg value that is not on the path/shell-safe allowlist.
    Must be called on the raw seg string BEFORE review_path(seg) is ever
    built.

    review_path(seg) builds SEGMENTS_DIR / f"{seg}.review.json" using
    pathlib's `/` operator, which silently DISCARDS the SEGMENTS_DIR
    prefix entirely when seg is itself an absolute path -- standard
    pathlib behavior, e.g. Path('/a') / '/b' == Path('/b') -- letting the
    script read/compare a review JSON file completely outside segments/
    when seg contains a path separator, '..', or is itself an absolute
    path. seg also flows into shell command strings built by the
    mass-translate workflow (mass-translate-wf.template.js), so a bare
    path-escape denylist is not enough either -- a shell metacharacter
    (e.g. "seg;rm") passed every one of the checks below unscathed. The
    single source of truth is therefore the allowlist regex _SEG_ID_RE;
    the specific checks below exist only to give the classic path-escape
    cases their own precise, historical wording -- every input _SEG_ID_RE
    would reject for any OTHER reason (shell metacharacters, whitespace, a
    stray '.', etc.) falls through to the generic allowlist message.

    Returns None when seg is a safe bare segment id, or a human-readable
    problem description otherwise.
    """
    if not seg:
        return "segment id must not be empty."
    if _SEG_ID_RE.fullmatch(seg):
        return None
    if Path(seg).is_absolute():
        return f"segment id must not be an absolute path (got {seg!r})."
    if "/" in seg or "\\" in seg:
        return f"segment id must not contain a path separator (got {seg!r})."
    if ".." in Path(seg).parts:
        return f"segment id must not contain a '..' path component (got {seg!r})."
    return (
        "segment id must match (FRONTBACK:)?[A-Za-z0-9_]+ (no path "
        f"separators, '..', or shell metacharacters); got {seg!r}."
    )


def review_path(seg):
    return SEGMENTS_DIR / f"{seg}.review.json"


def read_json_file(path, what):
    """Read and parse a JSON file. Returns (obj, None) on success, or
    (None, error_message) on any problem -- never raises."""
    if not path.is_file():
        return None, f"{what} not found at {path}"
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f), None
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"{what} at {path} is not valid JSON: {exc}"


def canonical_text(obj):
    """Sorted-key JSON serialization -- the canonical form both the on-disk
    review artifact and the expected-file are compared in, so differing
    key order alone never counts as a mismatch."""
    return json.dumps(obj, sort_keys=True, ensure_ascii=False)


def _project_finding(f):
    """Projects a single findings[] element down to _FINDING_COMPARE_FIELDS
    (`loc`, `severity`), dropping the free-text `issue`/`suggest` bodies.
    Non-dict elements pass through unprojected -- same reasoning as
    project_verdict_fields()'s own non-dict passthrough, below."""
    if not isinstance(f, dict):
        return f
    return {k: f[k] for k in _FINDING_COMPARE_FIELDS if k in f}


def project_verdict_fields(obj):
    """Projects an arbitrary JSON value down to exactly the 4
    REVIEW_VERDICT_FIELDS -- so an on-disk review.json carrying the 5th
    dispatch_token metadata field still compares equal to REVIEW_SCHEMA's
    own 4-field expected object. Non-dict inputs pass through unprojected
    (the subsequent comparison / first_diff() call reports the resulting
    type mismatch on its own -- this function's only job is dropping extra
    keys off an already-dict-shaped value, never type-checking).

    1.3.6 addition (#132) -- each findings[] element is ALSO projected down
    to {loc, severity} via _project_finding(), dropping the free-text
    `issue`/`suggest` bodies. review_ready.py already guarantees the
    on-disk artifact is schema-valid, draft_sha1-fresh, and
    dispatch_token-matched by the time this script runs, so byte-comparing
    free-text prose can only false-block a valid review on an immaterial
    transcription slip -- it can never catch a decision-relevant divergence
    the retained structural fields wouldn't already catch.

    1.3.6 (#132 option b): the structural binding this compare keeps --
    clean/coverage_ok/draft_sha1, plus each finding's loc, severity, and the
    findings array's own length (still enforced by first_diff()'s
    list-length check) -- is NO LONGER what protects the fixer. The fixer
    (fixPrompt in mass-translate-wf.template.js) now reads review_path(seg)
    from disk itself, fresh and token-validated by review_ready.py this
    same round, instead of consuming the in-memory revObj copy this compare
    validates -- so a slipped/dropped/fabricated finding in THAT copy can no
    longer reach the fixer regardless of what this compare concludes. This
    compare's remaining job is narrower: the structural binding for
    ledger_update.py's later reviewed_draft_sha1/dispatch_token check and
    audit-trail inspection (references/ledger-and-resumability.md). Do NOT
    "restore" the free-text issue/suggest compare as a future bugfix --
    that would reintroduce exactly the false-block this projection exists
    to remove, without closing any real gap (the fix-step gap it might look
    like it's protecting against is already closed by the fixer's own disk
    read, not by this compare)."""
    if not isinstance(obj, dict):
        return obj
    out = {k: obj[k] for k in REVIEW_VERDICT_FIELDS if k in obj}
    if isinstance(out.get("findings"), list):
        out["findings"] = [_project_finding(f) for f in out["findings"]]
    return out


def _fmt(value):
    text = json.dumps(value, sort_keys=True, ensure_ascii=False)
    if len(text) > _TRUNCATE_LEN:
        text = text[:_TRUNCATE_LEN] + "...(truncated)"
    return text


def first_diff(disk_value, expected_value, path=""):
    """Walk both (already-parsed) JSON values in sorted-key order and
    return a human-readable description of the FIRST differing key/value
    pair, or None if the two values are structurally identical.

    This is what powers mismatch_detail: "the first differing key/value
    pair, named" (references/workflow-schema-validation.md).
    """
    label = path if path else "<root>"

    if isinstance(disk_value, dict) and isinstance(expected_value, dict):
        for key in sorted(set(disk_value) | set(expected_value)):
            sub_path = f"{path}.{key}" if path else key
            if key not in disk_value:
                return (
                    f"key '{sub_path}' missing from on-disk review artifact "
                    f"(expected-file has it: {_fmt(expected_value[key])})"
                )
            if key not in expected_value:
                return (
                    f"key '{sub_path}' missing from expected-file "
                    f"(on-disk review artifact has it: {_fmt(disk_value[key])})"
                )
            nested = first_diff(disk_value[key], expected_value[key], sub_path)
            if nested is not None:
                return nested
        return None

    if isinstance(disk_value, list) and isinstance(expected_value, list):
        if len(disk_value) != len(expected_value):
            return (
                f"'{label}' array length differs (on-disk={len(disk_value)}, "
                f"expected-file={len(expected_value)})"
            )
        for i, (dv, ev) in enumerate(zip(disk_value, expected_value)):
            nested = first_diff(dv, ev, f"{path}[{i}]")
            if nested is not None:
                return nested
        return None

    if disk_value != expected_value:
        return (
            f"'{label}' differs (on-disk={_fmt(disk_value)}, "
            f"expected-file={_fmt(expected_value)})"
        )
    return None


def emit_match(match, mismatch_detail=None):
    """Print the one schema-conforming stdout line and return the process
    exit code (0 in both branches -- match:false is a normal, expected
    outcome, not a script failure)."""
    if match:
        print(json.dumps({"match": True}))
    else:
        print(json.dumps({"match": False, "mismatch_detail": mismatch_detail}))
    return 0


def emit_error(message):
    """Print a single named error line to stderr -- never a raw traceback,
    never a line that could be mistaken for a {"match": ...} result -- and
    return the non-zero exit code."""
    print(f"Error: {message}", file=sys.stderr)
    return 1


def build_arg_parser():
    parser = argparse.ArgumentParser(
        prog="review_artifact_check.py",
        description=(
            "Byte-for-byte (canonical, sorted-key JSON) comparison of a "
            "segment's on-disk review_path(seg) against an --expected-file "
            "written by the calling agent. See references/"
            "workflow-schema-validation.md's 'review-artifact gate' section."
        ),
    )
    parser.add_argument(
        "seg",
        help="Segment identifier (matches manifest.json's segments[]/frontback[] id).",
    )
    parser.add_argument(
        "--expected-file",
        required=True,
        type=Path,
        help=(
            "Path to the scratch file the calling agent wrote revObj's own "
            "canonical-JSON text to, verbatim."
        ),
    )
    return parser


def main(argv=None):
    args = build_arg_parser().parse_args(argv)

    seg = args.seg
    problem = validate_seg(seg)
    if problem is not None:
        return emit_error(problem)

    rpath = review_path(seg)
    disk_obj, err = read_json_file(rpath, f"Review artifact for segment '{seg}'")
    if err is not None:
        return emit_error(err)

    expected_obj, err = read_json_file(args.expected_file, "Expected-file")
    if err is not None:
        return emit_error(err)

    # Project BOTH sides down to the 4 verdict fields (and each finding down
    # to {loc, severity}) BEFORE comparing -- see "1.2.0 addition -- field
    # projection" and "1.3.6 addition (#132)" in this file's own module
    # docstring. expected_obj is already 4-field in practice (REVIEW_SCHEMA
    # never carries dispatch_token), so the top-level projection is a no-op
    # on that side; it's what lets a 5-field on-disk review.json still
    # match. The per-finding projection strips the free-text issue/suggest
    # bodies from BOTH sides -- deliberate, not a bug (#132): see
    # project_verdict_fields()'s own docstring for why restoring the
    # free-text compare would be a net-negative.
    disk_projected = project_verdict_fields(disk_obj)
    expected_projected = project_verdict_fields(expected_obj)

    if canonical_text(disk_projected) == canonical_text(expected_projected):
        return emit_match(True)

    detail = first_diff(disk_projected, expected_projected)
    if detail is None:
        # The canonical forms differ but the structural walk found nothing
        # -- should not be reachable for two plain JSON objects/arrays/
        # scalars parsed by the stdlib decoder. Fall back to a generic, but
        # still truthful, detail rather than silently claiming a match.
        detail = (
            "canonical JSON text differs but no structural field difference "
            "was found (unexpected -- inputs may not be plain JSON values)"
        )
    return emit_match(False, detail)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # defensive: never let a raw traceback escape
        print(f"Error: unexpected failure in review_artifact_check.py: {exc}", file=sys.stderr)
        sys.exit(1)
