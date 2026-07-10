#!/usr/bin/env python3
"""draft_sha1.py -- print the content sha1 of a segment's draft file.

Part of the literary-translator plugin's ledger/resumability subsystem
(see references/ledger-and-resumability.md). Tiny and dependency-free:
stdlib hashlib/json only.

CLI:

    python3 draft_sha1.py {seg}

Prints a sha1 hex digest of

    {durable_root}/segments/{seg}.draft.json

to stdout (bare hex string, newline-terminated), where {durable_root} is
this script's own grandparent directory (self-anchored, never cwd, never
a --durable-root flag).

Canonical path (load-bearing, see ledger-and-resumability.md):

    draft_path(seg) = {durable_root}/segments/{seg}.draft.json

deliberately WITHOUT a target-language suffix (a divergence from the real
historiettes-t3 reference project's own .ru.draft.json naming -- v1 has
exactly one target language per project, already recorded in profile.yml).

1.2.0 CHANGE -- content hash, dispatch_token EXCLUDED: pre-1.2.0, this
script hashed the file's raw on-disk bytes verbatim (nothing re-serialized).
Since 1.2.0, draft.schema.json requires a `dispatch_token` metadata field
(a run-scoped freshness token, checked independently by draft_ready.py's
--expect-token gate and at every downstream consume/commit point -- see
draft.schema.json's own field description) that must NEVER perturb this
hash: draft_sha1 answers "has the TRANSLATED CONTENT changed since I
reviewed it", a question deliberately decoupled from "is this artifact
from the CURRENT run" (dispatch_token's own, separate job). So this script
now parses the draft as JSON, drops the top-level 'dispatch_token' key if
present, re-serializes the remainder via sorted-key canonical JSON
(matching this project's canonical-JSON convention elsewhere, e.g.
cache_key.py's canonical_json_bytes -- sort_keys, compact separators,
non-ASCII preserved verbatim), and hashes THAT -- deterministic regardless
of the file's own on-disk key order/whitespace, and stable across a
token-only change to an otherwise-unchanged draft.

This is the SOLE sha1 authority for draft files -- no template or prompt
anywhere in this plugin invokes a raw shell `sha1sum`/`shasum` command
directly. The hash computed here MUST match, byte for byte, the hash
ledger_update.py independently recomputes at convergence-write time
(draft_content_sha1() in that script -- a byte-identical duplicate of
this file's own implementation, per this project's "no shared lib between
self-contained scripts" convention). Note ledger_update.py ALSO keeps a
separate, unrelated sha1_bytes_of_file() helper for hashing its own
ledger-fragment output file -- a plain file, never a draft, still hashed
as raw bytes; the two hashing schemes are not interchangeable.

On any failure (missing segment id, draft file not found, unreadable
file, draft not valid JSON, draft not a JSON object), prints a one-line
error to stderr and exits non-zero. On success, exits 0.
"""

import hashlib
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


def draft_content_sha1(path: Path) -> str:
    """sha1 of a draft's CONTENT, with the 'dispatch_token' metadata field
    deliberately EXCLUDED -- see this file's own module docstring for why.

    Must match, byte for byte, ledger_update.py's own draft_content_sha1()
    -- both parse the draft as JSON, drop 'dispatch_token' if present, and
    re-serialize the remainder via identical sorted-key canonical JSON
    before hashing.

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


def main() -> int:
    if len(sys.argv) != 2:
        print(
            "Usage: python3 draft_sha1.py {seg}\n"
            "  seg: segment identifier (matches manifest.json's "
            "segments[]/frontback[] id).",
            file=sys.stderr,
        )
        return 2

    seg = sys.argv[1]
    _seg_err = validate_seg(seg)
    if _seg_err:
        print(f"Error: {_seg_err}", file=sys.stderr)
        return 2

    path = draft_path(seg)
    if not path.is_file():
        print(f"Error: draft not found for segment '{seg}' at {path}", file=sys.stderr)
        return 1

    try:
        digest = draft_content_sha1(path)
    except OSError as exc:
        print(f"Error: could not read draft at {path}: {exc}", file=sys.stderr)
        return 1
    except json.JSONDecodeError as exc:
        print(f"Error: draft at {path} is not valid JSON: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(digest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
