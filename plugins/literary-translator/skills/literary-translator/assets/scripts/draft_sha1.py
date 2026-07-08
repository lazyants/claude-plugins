#!/usr/bin/env python3
"""draft_sha1.py -- print the sha1 of a segment's draft file.

Part of the literary-translator plugin's ledger/resumability subsystem
(see references/ledger-and-resumability.md). Tiny and dependency-free:
stdlib hashlib only.

CLI:

    python3 draft_sha1.py {seg}

Prints the sha1 hex digest of

    {durable_root}/segments/{seg}.draft.json

to stdout (bare hex string, newline-terminated), where {durable_root} is
this script's own grandparent directory (self-anchored, never cwd, never
a --durable-root flag).

Canonical path (load-bearing, see ledger-and-resumability.md):

    draft_path(seg) = {durable_root}/segments/{seg}.draft.json

deliberately WITHOUT a target-language suffix (a divergence from the real
historiettes-t3 reference project's own .ru.draft.json naming -- v1 has
exactly one target language per project, already recorded in profile.yml).

This is the SOLE sha1 authority for draft files -- no template or prompt
anywhere in this plugin invokes a raw shell `sha1sum`/`shasum` command
directly. The hash computed here MUST match, byte for byte, the hash
ledger_update.py independently recomputes at write time
(sha1_bytes_of_file() in that script): both hash the file's raw on-disk
bytes in binary mode, nothing re-serialized or re-canonicalized as JSON.

On any failure (missing segment id, draft file not found, unreadable
file), prints a one-line error to stderr and exits non-zero. On success,
exits 0.
"""

import hashlib
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


def sha1_bytes_of_file(path: Path) -> str:
    """sha1 of a file's raw on-disk bytes.

    Must match ledger_update.py's own sha1_bytes_of_file() exactly -- both
    hash the file's raw bytes, nothing re-serialized or re-canonicalized.
    """
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


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
        digest = sha1_bytes_of_file(path)
    except OSError as exc:
        print(f"Error: could not read draft at {path}: {exc}", file=sys.stderr)
        return 1

    print(digest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
