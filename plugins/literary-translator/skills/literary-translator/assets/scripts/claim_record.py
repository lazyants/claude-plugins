#!/usr/bin/env python3
"""claim_record.py -- the durable per-run record that a draft has been
CLAIMED for re-review (#438).

A claim says exactly one thing: "this on-disk draft belongs to run X now,
and it was admitted for RE-REVIEW, never for re-translation". It is a
one-time state change on disk rather than an in-flight intent, which is
what makes it crash-safe: a process that dies after the claim leaves a
draft that legitimately belongs to this run, and the ordinary path picks
it up.

WHY A MARKER FILE, and not the two obvious alternatives -- both are ruled
out by code, not preference:

  * In the draft or the review document: impossible. Both schemas are
    `additionalProperties: false`. The attempt would also be far worse
    than a rejected patch -- compute_schema_hash() hashes both schemas and
    `schema_hash` is a CACHE_KEY_FIELD_ORDER member, so adding a field to
    either would stale EVERY converged segment in EVERY project in order
    to record a fact about one segment.

  * In the ledger fragment: erased by the next full-replace write. That
    erasure is precisely the gap `.ever_converged` was created to close,
    and it is live on disk today. Storing recovery state there repeats the
    bug the sentinel exists to prevent.

WHY PER-RUN (`runs/<RUN_ID>/.claimed.<seg>`) and not `segments/.claimed.<seg>`:
a claim asserts "this draft belongs to run X". Scoping the record to the
run makes a claim into a LATER run a fresh authorization rather than a
standing permission. This mirrors the argument already written for
`.resume_gate_ack` (select_segments.py's resume_gate_ack_path(): "a gate
with a wildcard escape is the invisible warning this check exists to
replace. Per-run makes the wildcard structurally impossible"). Only the
LOCATION argument is borrowed from that precedent -- NOT its read
discipline: `.resume_gate_ack`'s own readers use `.exists()`, which is
exactly the predicate this module forbids (see below). Retrofitting those
readers is out of scope and is recorded in OPEN.md.

WHY "claim"/"claimed" AND NEVER "adopt"/"adopted": that word is already
taken for a different operation in this pipeline -- codex_job.py sets
`adopted = True` / `reason = "adopted"` for a PENDING CODEX ATTEMPT, and
segment_dispatch_driver.py has adopt_pending(). Two adoptions in one
pipeline is a term that will be misread during an incident, which is the
worst possible moment to be reading the wrong noun.

THE READ DISCIPLINE IS THE POINT OF THIS MODULE. This record has two
readers (the selector admits, the driver acts) and one writer -- the
identical shape that produced the 1.19.1 sentinel data-loss bug. So from
day one it is a THREE-STATE predicate (ENOENT / regular file / everything
else), never `Path.exists()`, with ENOENT decided by catching
FileNotFoundError rather than by testing an errno that can genuinely be
None. `Path.exists()` returns False both for "no such file" and for
"permission denied", so it fails OPEN on exactly the states that need to
refuse.

**The AMBIGUOUS mapping is "do not claim", never "assume claimed".** A
draft that is not claimed keeps its old dispatch_token, and every existing
gate then refuses it -- which is the safe state. Assuming a claim on an
unreadable record would hand a re-review authorization to a segment nobody
authorized.

Unlike the four duplicated copies of the `.ever_converged` predicate, this
one is SHARED by import (`import claim_record`, the flat sibling-import
idiom already used for cache_key.py). A drift test and a census test pin
the readers together, so a third reader appearing later is caught rather
than left free to disagree.

A verified property that makes all of this content-safe by construction:
draft_content_sha1() projects out `dispatch_token` before hashing, so
rewriting the token cannot change any draft's content sha1 and every
`reviewed_draft_sha1` comparison downstream is untouched.
"""

import errno
import json
import os
import stat
from pathlib import Path

# ---------------------------------------------------------------------------
# The three-state claim-record predicate.
#
# Deliberately NOT a copy of the `.ever_converged` SENTINEL_* block: that one
# is duplicated across four scripts because those scripts must stay
# independently runnable from a durable root. This record is read by two
# callers that already import shared helpers, so it is shared by import --
# which is what lets one drift test pin every reader at once.
# ---------------------------------------------------------------------------

CLAIM_ABSENT = "absent"
CLAIM_PRESENT = "present"
CLAIM_AMBIGUOUS = "ambiguous"

CLAIM_PREFIX = ".claimed."

# The mode a published claim record carries: 0o644 & ~umask, matching what
# the kernel produces from os.open(..., 0o644). Stated as the same literal
# the writer passes so a drift test can pin the two together.
CLAIM_MODE = 0o644


def _claim_entry_kind(mode: int) -> str:
    """A human name for the st_mode of whatever occupies a claim path -- it
    goes straight into an operator-facing message, which has to say what is
    actually sitting there before it can ask anyone to fix it."""
    if stat.S_ISLNK(mode):
        return "a symbolic link"
    if stat.S_ISDIR(mode):
        return "a directory"
    if stat.S_ISFIFO(mode):
        return "a FIFO"
    if stat.S_ISSOCK(mode):
        return "a socket"
    if stat.S_ISCHR(mode):
        return "a character device"
    if stat.S_ISBLK(mode):
        return "a block device"
    return f"an entry of unknown type (st_mode={mode:#o})"


def claimed_path(run_id: str, seg: str, runs_dir: Path) -> Path:
    """`{runs_dir}/{run_id}/.claimed.{seg}`.

    The `seg` component reaches a real filename and CAN CONTAIN A COLON --
    `FRONTBACK:errata_02` is a shipped shape and `runs/ledger.d/
    FRONTBACK:errata_02.json` already exists on disk. A colon is legal in a
    POSIX filename; nothing here may split, sanitize or rewrite it, because
    the round trip through this path is how the driver finds the record the
    selector wrote.
    """
    return runs_dir / run_id / f"{CLAIM_PREFIX}{seg}"


def classify_claim_record(path: Path):
    """Classify what occupies `path`: `(CLAIM_ABSENT|CLAIM_PRESENT|
    CLAIM_AMBIGUOUS, detail)`.

    `detail` is empty for the two decided verdicts and carries an
    operator-actionable reason for AMBIGUOUS.

    Every caller must map AMBIGUOUS to its own SAFE direction and say so at
    the call site. For the claim record that direction is always "not
    claimed" -- see the module docstring. A false PRESENT authorizes a
    re-review nobody asked for; a false ABSENT merely leaves the existing
    gates in force, which is the recoverable side.
    """
    try:
        # lstat, never stat: the FINAL component stays unresolved, so a
        # symlink pointing at a regular file is reported as the symlink it
        # is rather than as the file it aims at. A claim record is a local
        # fact about this run; a symlink is not one.
        st = path.lstat()
    except FileNotFoundError:
        # FileNotFoundError IS ENOENT by construction, so this verdict never
        # consults .errno -- which is why the None-errno guard below can be a
        # plain branch rather than a three-way comparison.
        return (CLAIM_ABSENT, "")
    except OSError as exc:
        # OSError.errno is typed `int | None` and genuinely can be None. A
        # missing errno is the LEAST informative failure there is, so it
        # lands on the ambiguous side like every other non-ENOENT outcome --
        # never silently treated as "some other errno", and above all never
        # as absence.
        if exc.errno is None:
            return (CLAIM_AMBIGUOUS, f"lstat failed with no errno: {exc}")
        code = errno.errorcode.get(exc.errno, f"errno {exc.errno}")
        return (CLAIM_AMBIGUOUS, f"lstat failed with {code}: {exc.strerror or exc}")
    if stat.S_ISREG(st.st_mode):
        return (CLAIM_PRESENT, "")
    return (
        CLAIM_AMBIGUOUS,
        f"the entry is {_claim_entry_kind(st.st_mode)}, not a regular file",
    )


def read_claim_record(path: Path):
    """Read and parse a claim record: `(state, payload_or_None, detail)`.

    A record that classifies PRESENT but does not parse as a JSON object is
    reported AMBIGUOUS, not PRESENT-with-empty-contents. A truncated record
    is a record whose pre-claim baseline is gone, and proceeding on one
    would silently discard the only evidence of what the draft looked like
    before the claim.
    """
    state, detail = classify_claim_record(path)
    if state != CLAIM_PRESENT:
        return (state, None, detail)
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        return (CLAIM_AMBIGUOUS, None, f"claim record unreadable: {exc}")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        return (CLAIM_AMBIGUOUS, None, f"claim record is not valid JSON: {exc}")
    if not isinstance(payload, dict):
        return (
            CLAIM_AMBIGUOUS,
            None,
            f"claim record must be a JSON object, got {type(payload).__name__}",
        )
    return (CLAIM_PRESENT, payload, "")


# The fields a claim record carries. `cache_key` is the FRESHLY COMPUTED key
# at claim time -- recorded so that a LATER claim on the same segment has the
# baseline this one lacked, which is the whole reason --from-cap cannot do a
# cache-key comparison today.
CLAIM_RECORD_FIELDS = (
    "seg",
    "profile",
    "run_id",
    "source_run_id",
    "previous_dispatch_token",
    "pre_claim_content_sha1",
    "operator_invocation",
    "cache_key",
    "claimed_at",
)


def build_claim_record(
    seg,
    profile,
    run_id,
    source_run_id,
    previous_dispatch_token,
    pre_claim_content_sha1,
    operator_invocation,
    cache_key,
    claimed_at,
):
    """Assemble a claim record payload with every field CLAIM_RECORD_FIELDS
    names, in that order. Built here rather than at each call site so the
    field set has exactly one definition and a drift test can pin it."""
    return {
        "seg": seg,
        "profile": profile,
        "run_id": run_id,
        "source_run_id": source_run_id,
        "previous_dispatch_token": previous_dispatch_token,
        "pre_claim_content_sha1": pre_claim_content_sha1,
        "operator_invocation": operator_invocation,
        "cache_key": cache_key,
        "claimed_at": claimed_at,
    }


def write_claim_record(path: Path, payload: dict):
    """Publish a claim record exclusively: `(True, "")` on a fresh write,
    `(False, detail)` when this run has ALREADY claimed this segment.

    O_CREAT|O_EXCL rather than a temp-file rename, because the exclusivity
    IS the semantic: a second claim of the same segment within the same run
    is the SAME authorization, and overwriting would destroy the one thing
    the record exists to preserve -- the pre-claim content sha1 and the
    token the draft carried before this run touched it. Reporting the
    already-claimed case as a non-fatal False lets the caller treat a
    re-run as idempotent while still being able to say which ids were
    already claimed.

    A claim into a LATER run lands on a different path by construction and
    is therefore a fresh authorization, never a standing permission.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False) + "\n"
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, CLAIM_MODE)
    except FileExistsError:
        # EEXIST is NOT proof that a previous claim published a record here:
        # O_CREAT|O_EXCL reports it for ANY existing entry, including a
        # directory or a dangling symlink. Re-classify so the caller learns
        # which of those it is instead of reading "already claimed" off a
        # broken entry.
        state, detail = classify_claim_record(path)
        if state == CLAIM_PRESENT:
            return (False, "already claimed by this run")
        return (False, f"claim path is occupied but unusable: {detail}")
    except OSError as exc:
        return (False, f"could not create the claim record: {exc}")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as exc:
        # A record that exists but was never fully written is worse than no
        # record: its pre-claim baseline would be trusted and wrong. Remove
        # the partial entry so the state stays ABSENT, which every gate
        # already refuses safely.
        try:
            path.unlink()
        except OSError:
            pass
        return (False, f"could not write the claim record: {exc}")
    return (True, "")
