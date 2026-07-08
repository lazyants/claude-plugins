#!/usr/bin/env python3
"""ledger_update.py -- the per-segment ledger fragment writer.

Part of the literary-translator plugin's ledger/resumability subsystem
(see references/ledger-and-resumability.md). This subsystem -- the
per-segment fragment ledger, this atomic writer, the merge/stale
materializer, the composite cache key, and the schema-confirmed write
paths -- is NEW plugin hardening layered on top of the source-proven
historiettes-t3 engine loop. It has not yet been run at scale; treat it
as a careful first design, not as something already proven surprise-free.

CLI:

    python3 ledger_update.py {seg} --payload-file <path>

The caller (an agent, shelling out mid-turn) first writes its intended
fields as a JSON object to a scratch payload file -- no shell
interpolation of field values -- then invokes this script with just that
path. The payload may set ONLY: status (required), rounds (a bare
integer), reason, note, cache_key. Anything else is refused.

Every write is a FULL REPLACE, never a read-modify-write merge: the
fragment written is built entirely fresh from (1) a freshly generated
timestamp, (2) status plus whichever other fields this payload supplied,
(3) n_blocks/n_footnotes/n_verses/reviewed_draft_sha1 -- derived by this
script itself, only when status == 'converged', never taken from the
payload. The prior on-disk fragment's field values are never read into
the new record.

Canonical paths (load-bearing, see ledger-and-resumability.md):

    draft_path(seg)   = {durable_root}/segments/{seg}.draft.json
    review_path(seg)  = {durable_root}/segments/{seg}.review.json
    segpack_path(seg) = {durable_root}/segments/segpack_{seg}.json

all three deliberately WITHOUT a target-language suffix (a divergence
from the real historiettes-t3 reference project's own .ru.draft.json
naming -- v1 has exactly one target language per project, already
recorded in profile.yml).

On stdout: exactly one JSON line matching
ledger-write-confirmation.schema.json. Success:
{"success": true, "status": ..., "fragment_path": ..., "fragment_sha1": ...}.
Failure: {"success": false, "error": ...} (plus optional exit_code/stderr).
The two shapes are never mixed -- a failure never claims a fragment_path/
fragment_sha1 that was never written.
"""

import argparse
import copy
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import NoReturn

try:
    import jsonschema
    import jsonschema.exceptions
    import jsonschema.validators
except ImportError:
    print(json.dumps({
        "success": False,
        "error": (
            "Missing required dependency 'jsonschema'. Install it with: "
            "pip install jsonschema (or: pip install -r requirements.txt "
            "from the literary-translator plugin root)."
        ),
    }))
    sys.exit(1)


# ---------------------------------------------------------------------------
# Self-anchoring: this script always lives at {durable_root}/scripts/<name>.py.
# It never assumes cwd == durable_root, and never takes a --durable-root flag.
# ---------------------------------------------------------------------------
DURABLE_ROOT = Path(__file__).resolve().parents[1]
SCHEMAS_DIR = DURABLE_ROOT / "schemas"
SEGMENTS_DIR = DURABLE_ROOT / "segments"
RUNS_DIR = DURABLE_ROOT / "runs"
LEDGER_FRAGMENT_DIR = RUNS_DIR / "ledger.d"

# The only statuses ledger_update.py itself ever writes to a fragment.
# 'stale' is never one of them -- that status is computed by ledger_merge.py
# only in the materialized ledger.json, never found on a fragment on disk.
FRAGMENT_STATUS_FALLBACK_ENUM = [
    "pending", "in_progress", "converged", "non_converged", "blocked",
]


def draft_path(seg):
    return SEGMENTS_DIR / f"{seg}.draft.json"


def review_path(seg):
    return SEGMENTS_DIR / f"{seg}.review.json"


def segpack_path(seg):
    return SEGMENTS_DIR / f"segpack_{seg}.json"


def sha1_bytes_of_file(path):
    """sha1 of a file's raw on-disk bytes.

    Must match draft_sha1.py's own hash exactly -- both hash the file's raw
    bytes, nothing re-serialized or re-canonicalized.
    """
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def now_iso8601():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def emit_failure(error, **extra) -> NoReturn:
    payload = {"success": False, "error": error}
    payload.update(extra)
    print(json.dumps(payload))
    sys.exit(1)


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


def emit_success(status, fragment_path, fragment_sha1):
    print(json.dumps({
        "success": True,
        "status": status,
        "fragment_path": fragment_path,
        "fragment_sha1": fragment_sha1,
    }))
    sys.exit(0)


def load_schema(name):
    path = SCHEMAS_DIR / name
    if not path.is_file():
        emit_failure(
            f"Required schema file not found: {path}. Was Step 0a run to "
            f"copy assets/schemas/ into {{DURABLE_ROOT}}/schemas/ for this "
            f"project?"
        )
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        emit_failure(f"Schema file at {path} is not valid JSON: {exc}")


def build_payload_schema(base_schema, fragment_schema):
    """Derive the embedded payload sub-schema from the two on-disk schema
    files, rather than hand-typing the 15-field cache_key list (or the
    status enum) a third time anywhere in this codebase.

    The caller may set only: status, rounds, reason, note, cache_key.
    """
    status_enum = fragment_schema.get("properties", {}).get("status", {}).get(
        "enum", FRAGMENT_STATUS_FALLBACK_ENUM
    )
    base_props = base_schema.get("properties", {})
    for required_prop in ("rounds", "reason", "note", "cache_key"):
        if required_prop not in base_props:
            emit_failure(
                f"Internal error: ledger-record-base.schema.json is missing "
                f"its own '{required_prop}' property definition -- cannot "
                f"derive the payload sub-schema."
            )
    return {
        "type": "object",
        "properties": {
            "status": {"type": "string", "enum": status_enum},
            "rounds": base_props["rounds"],
            "reason": base_props["reason"],
            "note": base_props["note"],
            "cache_key": base_props["cache_key"],
        },
        "required": ["status"],
        "additionalProperties": False,
    }


def build_combined_fragment_schema(base_schema, fragment_schema):
    """Inline ledger-record-base.schema.json directly into
    ledger-fragment.schema.json's own allOf, replacing the $ref. Both
    schemas are already loaded from disk, so this avoids standing up a
    $ref resolver purely to validate one already-composed instance.
    """
    combined = copy.deepcopy(fragment_schema)
    combined["allOf"] = [copy.deepcopy(base_schema)]
    return combined


def validate_final_fragment(fragment, base_schema, fragment_schema):
    combined_schema = build_combined_fragment_schema(base_schema, fragment_schema)
    try:
        validator_cls = jsonschema.validators.validator_for(combined_schema)
        validator_cls.check_schema(combined_schema)
        validator = validator_cls(combined_schema)
        errors = sorted(validator.iter_errors(fragment), key=str)
    except jsonschema.exceptions.SchemaError as exc:
        emit_failure(f"Internal error: composed fragment schema is invalid: {exc.message}")
        return
    if errors:
        messages = "; ".join(e.message for e in errors)
        emit_failure(f"Constructed ledger fragment failed schema validation: {messages}")


def read_json_file(path, what):
    if not path.is_file():
        return None, f"{what} not found at {path}"
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f), None
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"{what} at {path} is not valid JSON: {exc}"


def enrich_converged_fields(seg, fragment):
    """Populate n_blocks/n_footnotes/n_verses (from the segpack) and
    reviewed_draft_sha1 (via the review-artifact binding check) -- fields
    the calling agent's payload is never allowed to supply directly.

    Calls emit_failure() (which exits the process) on any problem, so this
    function either returns normally having mutated `fragment` in place,
    or the process has already exited.
    """
    spath = segpack_path(seg)
    segpack, err = read_json_file(spath, f"Segpack for segment '{seg}'")
    if err is not None:
        emit_failure(f"Cannot record convergence for segment '{seg}': {err}")

    for array_key, out_key in (
        ("blocks", "n_blocks"),
        ("footnotes", "n_footnotes"),
        ("verses", "n_verses"),
    ):
        array_value = segpack.get(array_key) if isinstance(segpack, dict) else None
        if not isinstance(array_value, list):
            emit_failure(
                f"Cannot record convergence for segment '{seg}': segpack at "
                f"{spath} has a missing or non-array '{array_key}' field."
            )
        fragment[out_key] = len(array_value)

    rpath = review_path(seg)
    review_obj, err = read_json_file(rpath, f"Review artifact for segment '{seg}'")
    if err is not None:
        emit_failure(f"Cannot record convergence for segment '{seg}': {err}")

    reviewer_draft_sha1 = review_obj.get("draft_sha1") if isinstance(review_obj, dict) else None
    if not isinstance(reviewer_draft_sha1, str) or not reviewer_draft_sha1:
        emit_failure(
            f"Cannot record convergence for segment '{seg}': review artifact "
            f"at {rpath} has no draft_sha1."
        )

    dpath = draft_path(seg)
    if not dpath.is_file():
        emit_failure(
            f"Cannot record convergence for segment '{seg}': draft not found "
            f"at {dpath}."
        )
    current_draft_sha1 = sha1_bytes_of_file(dpath)

    if current_draft_sha1 != reviewer_draft_sha1:
        # Exact literal per references/ledger-and-resumability.md -- the
        # calling recordLedgerPrompt() flow surfaces this verbatim.
        emit_failure("draft changed since review; cannot record convergence")

    fragment["reviewed_draft_sha1"] = current_draft_sha1


def write_fragment_atomically(seg, fragment):
    try:
        LEDGER_FRAGMENT_DIR.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        emit_failure(f"Could not create ledger fragment directory {LEDGER_FRAGMENT_DIR}: {exc}")

    final_path = LEDGER_FRAGMENT_DIR / f"{seg}.json"
    tmp_path = LEDGER_FRAGMENT_DIR / f"{seg}.json.tmp.{os.getpid()}"

    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(fragment, f, indent=2, ensure_ascii=False, sort_keys=True)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, final_path)
        try:
            dir_fd = os.open(LEDGER_FRAGMENT_DIR, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError:
            pass  # best-effort directory-entry durability; not fatal
    except OSError as exc:
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError:
            pass
        emit_failure(f"Failed writing ledger fragment for segment '{seg}': {exc}")

    return final_path


def main():
    parser = argparse.ArgumentParser(
        prog="ledger_update.py",
        description=(
            "Write one fragment to runs/ledger.d/{seg}.json. Full replace "
            "only -- never a read-modify-write merge against the prior "
            "on-disk fragment."
        ),
    )
    parser.add_argument(
        "seg",
        help="Segment identifier (matches manifest.json's segments[]/frontback[] id).",
    )
    parser.add_argument(
        "--payload-file",
        required=True,
        help=(
            "Path to a JSON file with the intended fields: status "
            "(required), plus optionally rounds, reason, note, cache_key."
        ),
    )
    args = parser.parse_args()

    seg = args.seg
    seg_error = validate_seg(seg)
    if seg_error is not None:
        emit_failure(seg_error)

    payload_path = Path(args.payload_file)

    if not payload_path.is_file():
        emit_failure(f"Payload file not found: {payload_path}")

    payload, err = read_json_file(payload_path, "Payload file")
    if err is not None:
        emit_failure(err)
    if not isinstance(payload, dict):
        emit_failure(
            f"Payload file at {payload_path} must contain a JSON object, "
            f"got {type(payload).__name__}."
        )

    base_schema = load_schema("ledger-record-base.schema.json")
    fragment_schema = load_schema("ledger-fragment.schema.json")

    payload_schema = build_payload_schema(base_schema, fragment_schema)
    try:
        jsonschema.validate(payload, payload_schema)
    except jsonschema.exceptions.ValidationError as exc:
        emit_failure(f"Malformed payload: {exc.message}")
    except jsonschema.exceptions.SchemaError as exc:
        emit_failure(f"Internal error: derived payload schema is invalid: {exc.message}")

    # Build the fragment entirely fresh -- the prior on-disk fragment (if
    # any) is never read for its field values, only implicitly superseded
    # by os.replace()'s atomic rename below.
    fragment = {"timestamp": now_iso8601(), "status": payload["status"]}
    for key in ("reason", "note", "rounds", "cache_key"):
        if key in payload:
            fragment[key] = payload[key]

    if fragment["status"] == "converged":
        enrich_converged_fields(seg, fragment)

    validate_final_fragment(fragment, base_schema, fragment_schema)

    final_path = write_fragment_atomically(seg, fragment)
    fragment_sha1 = sha1_bytes_of_file(final_path)

    # Best-effort scratch cleanup -- a failure to delete the already-consumed
    # payload file does not undo the successful, already-committed write.
    try:
        payload_path.unlink()
    except OSError:
        pass

    emit_success(fragment["status"], str(final_path), fragment_sha1)


if __name__ == "__main__":
    # SystemExit inherits from BaseException, not Exception, so emit_failure()
    # / emit_success()'s sys.exit() propagates cleanly past this handler; only
    # genuinely unexpected errors are caught and re-shaped into the JSON
    # failure envelope so stdout stays single-line JSON.
    try:
        main()
    except Exception as exc:  # noqa: BLE001 -- last-resort: keep stdout JSON-only
        emit_failure(f"Unexpected error in ledger_update.py: {exc}")
