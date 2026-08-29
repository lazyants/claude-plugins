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
discipline: `.resume_gate_ack`'s own readers do use `.exists()`
(select_segments.py's runs_acknowledged_pre_gate / runs_missing_digest
split, backfill_resume_gate_ack.py's needs_ack one). That collapse is safe
THERE because it fails CLOSED: a False -- genuinely absent or merely
unreadable, which `.exists()` cannot tell apart -- keeps the run id out of
the acknowledged list and puts it in runs_missing_digest, where
select_segments.py refuses to authorize any dispatch and
backfill_resume_gate_ack.py --apply is the sanctioned way to clear it
(create-only, and it warns on stderr when the create failed). Here the
polarity is the other way round -- ABSENT means PERMIT (see below) -- so
the same collapse would fail OPEN. No retrofit of those readers is owed.

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

**EXISTENCE IS NOT VALIDITY -- the two readers here answer different
questions and are not interchangeable.** classify_claim_record() is `lstat`
plus `S_ISREG` and nothing more: it establishes that something occupies the
path and that the something is a regular file. It never opens the file, so a
ZERO-LENGTH record, a record holding `null`, a record whose bytes are not
valid UTF-8, and a record torn by a crash between the exclusive create and
the fsync ALL classify PRESENT. The AMBIGUOUS verdict for any of those comes
from read_claim_record()'s own decode and JSON parse, never from the
classifier -- and it comes back as a RETURNED verdict in every one of those
cases, never as a raised exception, which is a property read_claim_record()'s
own docstring spells out because it was once untrue for the decode.

That split is deliberate, and both directions are load-bearing:

  * A REFUSAL predicate should use classify_claim_record(). "Something is
    sitting where this run's claim record goes" is already enough to refuse
    a translate, and refusing without reading a byte means an unparseable
    record cannot talk its way past the guard. Both D8 guards do exactly
    this -- codex_job.py's _refuse_claimed_translate() (via _claim_state())
    and segment_dispatch_driver.py's claim_refusal_for_translate() -- and
    both map AMBIGUOUS to REFUSE.

  * Any consumer about to BELIEVE A FIELD must go through
    read_claim_record(). draft_ready.py's _claim_note() reads `profile` and
    `claimed_at`; select_segments.py re-reads an already-claimed record
    rather than reporting a freshly recomputed payload. Reading a field off
    a classifier PRESENT is the mistake this paragraph exists to forbid:
    PRESENT is not a promise that the file has contents, let alone the right
    ones.

No consumer gets this wrong today. That every one of them happens to respect
an invariant nothing states is precisely the gap -- an unwritten invariant is
one refactor away from being false, with nothing red when it stops holding.

**RECORD-FIRST IS AN ORDERING ON DISK, NOT ON SOURCE LINES.** The selector
writes this record and only then re-stamps the draft's dispatch_token, so
that a crash in between leaves a record with no token (recoverable: every
existing gate still refuses the old token, and a re-claim is idempotent)
rather than a token with no record (which the D8 guard cannot refuse -- it
sees no record and reads "unclaimed"). Source order alone does not buy that
guarantee: a file created and fsynced is not reachable after a power loss
until its DIRECTORY ENTRY is durable too, and the two writes land in two
different directories. So write_claim_record() fsyncs the containing
directory before returning success, and success is exactly what a caller is
entitled to read as "the record is on disk". The other half of the ordering
belongs to the caller: fsync_directory() below is exported for it, and
select_segments.py must call it on the draft's own directory after its
os.replace().

This is a DIFFERENT failure from the already-disclosed residual about a
crash between the exclusive create and the file fsync. That one leaves a
record whose CONTENTS are missing (a zero-length file, which per the split
above still classifies PRESENT); this one leaves a record whose DIRECTORY
ENTRY is missing, so the record is not there at all. Closing either does
nothing for the other.

Unlike the five duplicated copies of the `.ever_converged` predicate, this
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
import re
import stat
from pathlib import Path

# ---------------------------------------------------------------------------
# The three-state claim-record predicate.
#
# Deliberately NOT a copy of the `.ever_converged` SENTINEL_* block: that one
# is duplicated across five scripts because those scripts must stay
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

# getattr, not os.O_DIRECTORY: the flag does not exist on every platform
# Python runs on, and the same guarded idiom already spells codex_job.py's
# and scaffold_setup.py's own `_O_DIRECTORY`. Falling back to 0 keeps the
# open legal there and leaves whatever the platform's own directory-open
# semantics are to decide -- fsync_directory() below treats a refusal as a
# durability failure either way, so the fallback can never turn into a
# silent success.
_O_DIRECTORY = getattr(os, "O_DIRECTORY", 0)


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


_RUN_ID_DIR_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


def validate_run_id(run_id):
    """Return an error string if `run_id` is not a safe RUN_ID, else None.

    Byte-for-byte the same check as select_segments.py's own
    `validate_run_id()` (itself mirroring resume_setup.py's `RUN_ID_RE` and
    `validate_run_id()`, the script that OWNS this contract because it mints
    run ids and builds `runs/<RUN_ID>/` from them). The other copies of the
    same decision live in backfill_resume_gate_ack.py, skeptic_setup.py and
    segment_dispatch_driver.py, all spelled `validate_run_id`. DUPLICATED
    rather than imported, per this project's "no shared lib between
    self-contained scripts" convention, and pinned against drift by
    tests/run_id_pattern_drift.test.py, which enumerates every module-level
    `re.compile` whose variable name mentions RUN_ID across every shipped
    script and compares both the PATTERN and the DECISION against the
    owner's -- so this copy must be added to that test's `EXPECTED_COPIES`
    roster as `("claim_record.py", "_RUN_ID_DIR_RE")` in the same change
    that introduces it.

    The regex alone is NOT the whole contract: `[A-Za-z0-9._-]` admits dots
    freely, so `.`, `..` and any value CONTAINING `..` pass it and are
    refused separately below. A copy carrying only the pattern therefore
    ACCEPTS `z..poison` while the owner REFUSES it -- agreement on the
    pattern is not agreement on the answer, which is why all three branches
    are reproduced here rather than just the fullmatch.

    WHY THIS MODULE NEEDS ITS OWN COPY AT ALL: the asymmetry that made
    claimed_path() a path-traversal hole. The WRITER validated its run id
    before minting a claim (select_segments.py:5020, called from its claim
    block) and every READER built the same path from an unvalidated one.
    A run id can reach a reader from an untrusted place -- draft_ready.py's
    `_claim_run_id()` derives it from a draft's own `dispatch_token`, a
    field with no schema `pattern` -- and a relocated lookup returns
    CLAIM_ABSENT, which every guard built on this record reads as "not
    claimed" and proceeds. Validating in the reader was rejected for the
    same reason: it leaves the next reader free to forget. It is validated
    where the path is CONSTRUCTED, so no caller can bypass it.
    """
    if not isinstance(run_id, str) or not run_id:
        return "run id must be a non-empty string."
    if not _RUN_ID_DIR_RE.fullmatch(run_id):
        return (
            "run id must match [A-Za-z0-9][A-Za-z0-9._-]* (letters/digits/"
            f"dot/underscore/hyphen only, no ':'); got {run_id!r}."
        )
    if run_id in (".", ".."):
        return f"run id must not be '.' or '..'; got {run_id!r}."
    if ".." in run_id:
        return f"run id must not contain '..'; got {run_id!r}."
    return None


def claimed_path(run_id: str, seg: str, runs_dir: Path) -> Path:
    """`{runs_dir}/{run_id}/.claimed.{seg}`.

    RAISES ValueError when `run_id` fails validate_run_id() above -- it does
    not return a sentinel path, and it does not fall back to a sanitized id.
    Measured against the unvalidated version this replaces: `'/tmp/elsewhere'`
    discarded `runs_dir` entirely (`Path` join semantics: an absolute
    right-hand side wins), `'..'` and `'a/../..'` walked out of the durable
    root, and `'  20260101T000000Z'` addressed a different directory that
    merely looks like the right one. Every one of those produced a path whose
    lookup then reported CLAIM_ABSENT, and ABSENT is the verdict every guard
    built on this record treats as "nothing to refuse". The failure was
    therefore silent AND fail-open, which is why it raises rather than
    returning anything a caller could keep using: a claim path that cannot be
    trusted must stop the operation, not quietly become a different path.

    The `seg` component is DELIBERATELY NOT validated or sanitized here. It
    reaches a real filename and CAN CONTAIN A COLON -- `FRONTBACK:errata_02`
    is a shipped shape and `runs/ledger.d/FRONTBACK:errata_02.json` already
    exists on disk. A colon is legal in a POSIX filename; nothing here may
    split, sanitize or rewrite it, because the round trip through this path
    is how the driver finds the record the selector wrote. Nor is validation
    missing from the system: every reader checks `seg` on its own way in --
    select_segments.py's parse_claim_requests(), codex_job.py's main(), and
    segment_dispatch_driver.py's parse_claims_field().
    """
    problem = validate_run_id(run_id)
    if problem is not None:
        raise ValueError(f"unsafe RUN_ID for a claim path: {problem}")
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

    THAT PROMISE IS TOTAL -- IT RETURNS A VERDICT, IT DOES NOT RAISE ONE --
    and the decode is the clause that nearly broke it. `read_text` fails in
    two unrelated ways that share no base class: an IO failure raises
    OSError, while a body that is not valid UTF-8 raises UnicodeDecodeError,
    a subclass of ValueError that `except OSError` does not catch. Left
    uncaught, a record holding invalid bytes RAISED out of here instead of
    returning AMBIGUOUS -- falsifying the paragraph above, which every reader
    maps to its own safe direction on the strength of, and which is the only
    statement of the contract they have.

    The escape was safe in DIRECTION (nothing downstream grants a claim on a
    traceback) and expensive in BLAST RADIUS, which is the half that made it
    worth fixing rather than documenting: neither of select_segments.py's two
    call sites guards this call -- evaluate_lost_token_recovery()'s read, and
    the re-read of an already-claimed record inside run()'s claim block -- so
    ONE segment's malformed record aborted the entire admission batch,
    contradicting the per-id isolation that same file states for every other
    unreadable artifact (_run_leaf_gate(): "a per-id failure here becomes THIS
    id's own claim-admission reason, never a whole-run crash";
    read_json_nonfatal(): "must fail THAT id's admission alone, never take
    down the whole batch"). Both of those already spell exactly this
    three-clause read, down to the separate "not valid UTF-8" wording, and
    this one now matches them. The wording is not cosmetic either:
    "unreadable" sends an operator to check permissions on a file whose
    permissions are fine.

    Deliberately NOT widened to a blanket `except Exception`. The failures a
    reader can actually produce here are enumerable -- classify_claim_record()
    has already absorbed every lstat OSError, and what remains is IO, decode
    and parse -- and a catch-all would launder a programming error into
    AMBIGUOUS, which reads as a fact about the disk. draft_ready.py's
    `_claim_note()` does catch broadly, and its own comment says why that is
    right THERE and not here: it is a never-fatal enrichment probe, so it
    trades diagnosis for survival. This function is the diagnosis.
    """
    state, detail = classify_claim_record(path)
    if state != CLAIM_PRESENT:
        return (state, None, detail)
    try:
        raw = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        # Listed first to mirror select_segments.py's read_json_nonfatal() /
        # read_segpack_nonfatal(); the ORDER is cosmetic (the two classes are
        # unrelated, so neither can shadow the other), the separate clause is
        # not. This one names `path` and the OSError clause below does not,
        # because UnicodeDecodeError carries the offending BYTE and no
        # filename, while an OSError's own str() already carries the filename.
        return (
            CLAIM_AMBIGUOUS,
            None,
            f"claim record at {path} is not valid UTF-8: {exc}",
        )
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


# ---------------------------------------------------------------------------
# The record's contents.
#
# A claim VOIDS things: the stored review's standing (D10) and, for
# --from-converged, the segment's previously-converged status. Evidence that
# a claim destroys has to be captured in the record that destroys it, or the
# only account of what the operator was shown at admission time is a line of
# stdout from a process that has since exited. D6 says the admission evidence
# is "recorded in the claim record"; D10 says the review evidence is
# preserved before being voided. Both are fields here, not report lines.
#
# Field by field, beyond the six that were always self-explanatory (`seg`,
# `profile`, `run_id`, `source_run_id`, `previous_dispatch_token`,
# `pre_claim_content_sha1`, plus `operator_invocation` and `claimed_at`):
#
#   pre_claim_review
#       D10. What the segment's review document said at the moment the claim
#       was admitted, as `{"dispatch_token", "clean", "coverage_ok",
#       "findings_count"}`, or None when the segment had no review document
#       to preserve. A consumer may conclude: this claim was granted over a
#       review with these verdicts -- and, when `clean` is True, that a
#       passing review was deliberately set aside, which is the case an
#       audit most wants to be able to find later. It may NOT conclude
#       anything about the review document on disk NOW; the claim's whole
#       purpose is to void it.
#
#   pre_claim_cache_key / cache_key_at_claim
#       The MOVEMENT, as two endpoints rather than one value. `pre_claim_cache_key`
#       is the `cache_key` recorded on the segment's own ledger fragment as
#       read at admission -- what the key WAS when the work being re-reviewed
#       was produced; None when the fragment carries none, which is always so
#       for --from-cap (a cache_key is written only on the convergence path) and,
#       since #455, for --from-stalled as well -- a stalled unit's materialized
#       ledger status is `in_progress`, the identical never-reached-convergence
#       shape, even though the SEGMENT converged at some earlier point in its
#       history. That history is what the `.ever_converged` sentinel records;
#       it is not carried onto the fragment's own `cache_key` field, so a
#       stalled fragment is a full replacement with no baseline exactly like a
#       capped one, for a different underlying reason. "Always" there is a
#       statement about this plugin's own WRITERS, not a guarantee about the
#       field: since #796 --from-stalled also admits a fragment that carries
#       convergence-derived fields it could only have got out of band, and such
#       a fragment can carry a `cache_key` too. The record then stores what was
#       read, as it always does -- nothing here derives a value from the
#       profile.
#       `cache_key_at_claim` is the key freshly computed by this invocation --
#       what it IS now.
#
#       WHY `cache_key` WAS RENAMED to `cache_key_at_claim` rather than kept
#       as-is beside its new sibling: with two keys in one record, a bare
#       `cache_key` no longer says which of the two it is, and this record
#       sits one directory away from ledger fragments whose OWN `cache_key`
#       field means the other one. Same argument as this module's
#       "claim"/"adopt" naming rule: a name that can be misread during an
#       incident will be, and the incident is the worst possible moment. The
#       field is unreleased (1.21.0 is in flight), so the rename costs
#       coordination, not compatibility.
#
#   cache_key_moved_fields
#       The selector's derived diff of those two endpoints over the
#       authoritative 15-field cache-key list: a list of
#       `{"field", "pre_claim", "at_claim"}` for every field that differs,
#       `[]` when none do. Derived rather than primary -- the two full keys
#       above are the evidence -- but recorded because re-deriving it
#       requires the 15-field list, and a consumer duplicating that list is
#       a sixth copy of it appearing for the sake of reading a record.
#
#       A consumer must NOT read an empty list as "nothing moved": it is
#       equally what a claim with NO baseline produces (`pre_claim_cache_key`
#       is None, so there is nothing to diff). Those two are told apart by
#       `pre_claim_cache_key is None`, and `cache_key_note` says which in
#       words.
#
#   cache_key_movement_machinery_only
#       True when every moved field is machinery-only (plugin_bundle_hash,
#       schema_hash, derivation_bundle_hash -- a plugin upgrade rather than a
#       content change), False when at least one content-bearing field moved,
#       and None when there was no movement to characterise. Tri-state on
#       purpose: `False` and "not applicable" are different facts and a
#       two-valued field would report them identically. REPORTING only -- per
#       D2 decision 5, no moved field refuses a claim.
#
#   cache_key_note
#       A human-readable explanation when there is no baseline to compare
#       against, else None. This is what keeps `pre_claim_cache_key: null`
#       from being read as "the key was missing unexpectedly" when it is in
#       fact the documented, expected shape for --from-cap and, since #455,
#       for --from-stalled -- see `pre_claim_cache_key`'s own entry above for
#       why the same absence shows up under each profile for a different
#       reason.
# ---------------------------------------------------------------------------

CLAIM_RECORD_FIELDS = (
    "seg",
    "profile",
    "run_id",
    "source_run_id",
    "previous_dispatch_token",
    "pre_claim_content_sha1",
    "pre_claim_review",
    "pre_claim_cache_key",
    "cache_key_at_claim",
    "cache_key_moved_fields",
    "cache_key_movement_machinery_only",
    "cache_key_note",
    "operator_invocation",
    "claimed_at",
)


def build_claim_record(
    *,
    seg,
    profile,
    run_id,
    source_run_id,
    previous_dispatch_token,
    pre_claim_content_sha1,
    pre_claim_review,
    pre_claim_cache_key,
    cache_key_at_claim,
    cache_key_moved_fields,
    cache_key_movement_machinery_only,
    cache_key_note,
    operator_invocation,
    claimed_at,
):
    """Assemble a claim record payload with every field CLAIM_RECORD_FIELDS
    names, in that order. Built here rather than at each call site so the
    field set has exactly one definition and a drift test can pin it.

    KEYWORD-ONLY, and every argument REQUIRED -- no defaults. Fourteen
    positional parameters of which eight are strings is a shape where a
    misordered call still runs and writes a record with `profile` in
    `source_run_id`; the leading `*` makes that unexpressible. Requiring all
    fourteen is the same reasoning one step further: a field added to
    CLAIM_RECORD_FIELDS and forgotten at a call site must be a TypeError at
    that call site, never a record silently missing the evidence it was
    extended to carry. Both properties matter more than they would in an
    ordinary constructor, because this record is the only durable account of
    a state change that voids a review."""
    return {
        "seg": seg,
        "profile": profile,
        "run_id": run_id,
        "source_run_id": source_run_id,
        "previous_dispatch_token": previous_dispatch_token,
        "pre_claim_content_sha1": pre_claim_content_sha1,
        "pre_claim_review": pre_claim_review,
        "pre_claim_cache_key": pre_claim_cache_key,
        "cache_key_at_claim": cache_key_at_claim,
        "cache_key_moved_fields": cache_key_moved_fields,
        "cache_key_movement_machinery_only": cache_key_movement_machinery_only,
        "cache_key_note": cache_key_note,
        "operator_invocation": operator_invocation,
        "claimed_at": claimed_at,
    }


def fsync_directory(directory) -> "str | None":
    """Make `directory`'s own entry list durable. Returns None on success, or
    an error string in the same shape validate_run_id() uses (None means "the
    thing you asked about is fine").

    EXPORTED because record-first ordering has two halves and this module
    owns only one of them. Writing file A, then file B, guarantees nothing
    across a power loss unless each file's DIRECTORY ENTRY is durable too:
    fsync on the file commits its contents, not the link that makes it
    findable. The claim record and the draft it authorizes live in different
    directories, so each write needs its own call -- write_claim_record()
    makes it for the record's directory, and select_segments.py must make it
    for the draft's directory after its os.replace().

    A FAILURE IS A FAILURE, never a shrug, and that is the deliberate choice
    here. The precedent in this repo runs both ways: ledger_update.py's
    write_fragment_atomically() swallows it (`pass  # best-effort directory-
    entry durability; not fatal`), while backfill_ever_converged.py's sync of
    the segments directory returns an error the caller fails the run on,
    reasoning that a sentinel whose directory entry is not durable is exactly
    the one a crash can lose while the ledger fragment it backs survives.
    The claim record is the second case, not the first: the asymmetry it
    protects against is token-without-record, the one state D8's guard cannot
    refuse, because it sees no record and reads "unclaimed". A best-effort
    sync would return success having established nothing, which is the shape
    of the defect rather than its fix.

    An open that fails is treated identically to an fsync that fails, and the
    platform reality is the reason rather than an oversight: opening a
    directory for fsync is not portable, and from in here a platform that
    refuses the open is indistinguishable from a directory that has been
    removed or made unreadable underneath us. Both mean the same thing to a
    caller -- this code cannot establish that the entry is durable -- and
    silently downgrading the first case to success would be an unprovable
    guarantee dressed as a proven one. The remedy on such a platform is to
    decide, deliberately and in the open, that this pipeline does not support
    it, not to weaken this function until it stops saying so.

    Deliberately NOT O_NOFOLLOW, and NOT an identity check. Its one job is
    durability of whatever directory the caller just wrote into. Whether that
    directory is still the one the caller means is a different question with
    a different remedy, and it is kept separate for the same reason
    backfill_ever_converged.py keeps its own sync and
    check_segments_dir_identity() apart."""
    try:
        dir_fd = os.open(str(directory), os.O_RDONLY | _O_DIRECTORY)
    except OSError as exc:
        return (
            f"its directory entry is not durable: {directory} could not be "
            f"opened for fsync ({exc})"
        )
    try:
        os.fsync(dir_fd)
    except OSError as exc:
        return (
            f"its directory entry is not durable: fsync on {directory} "
            f"failed ({exc})"
        )
    finally:
        os.close(dir_fd)
    return None


def write_claim_record(path: Path, payload: dict):
    """Publish a claim record exclusively: `(True, "")` on a fresh, DURABLE
    write, `(False, detail)` otherwise -- the already-claimed case (detail
    exactly `"already claimed by this run"`, which callers compare against
    literally), an occupied-but-unusable path, a failed create or write, or
    a directory whose entry list could not be made durable.

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

    SUCCESS MEANS DURABLE, which is what the caller's record-first ordering
    rests on: the record's directory is fsynced before True is returned, so
    a crash after this call cannot leave the re-stamped draft token on disk
    with the record that authorizes it missing. A directory that cannot be
    synced turns the write into a FAILURE -- see fsync_directory() for why
    this one is not best-effort.

    That failure does NOT unlink the record, and the difference from the
    partial-write path below is the reason. There, the file's CONTENTS are
    untrustworthy -- a record whose pre-claim baseline may be missing or torn
    would be believed and be wrong -- so removing it is what keeps the state
    honest. Here the contents are complete and fsynced and only the entry's
    durability is unproven; and removing a valid record is the fail-OPEN
    direction for every READER of it, since both D8 guards refuse a translate
    on PRESENT/AMBIGUOUS and let one through on ABSENT. Deleting the record
    would delete the guard in order to fix a durability doubt.

    The already-claimed branch syncs too, for the case that makes it matter:
    a retry after a sync failure finds the record already there, takes that
    branch, and would otherwise return the idempotent "already claimed by
    this run" -- which the caller reads as permission to re-stamp the token
    -- having never established the durability the first attempt failed to.
    When the sync fails there, the detail deliberately does NOT equal the
    literal "already claimed by this run" the caller compares against, so it
    lands on that caller's write-failure path instead.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False) + "\n"
    # ENCODE BEFORE CREATING THE PATH -- the ordering is the fix, not a
    # micro-optimisation. `ensure_ascii=False` means json.dumps() happily
    # returns a str containing a LONE SURROGATE, because json.loads() decodes
    # a "\ud800" escape into one and `dispatch_token` (hence
    # `previous_dispatch_token` here) is an arbitrary string that no schema
    # rejects and no content hash sees -- draft_content_sha1() projects
    # dispatch_token out. Encoding that str to UTF-8 raises
    # UnicodeEncodeError, which is a ValueError and NOT an OSError, so the
    # write-path `except OSError` below never caught it.
    #
    # This is the WRITE-side twin of the bug read_claim_record() already
    # fixed for UnicodeDecodeError, and it failed worse: the exclusive create
    # had ALREADY happened, so the exception escaped this function entirely
    # -- breaking its "returns a verdict, does not raise" contract -- and
    # left a ZERO-BYTE regular file behind. That file classifies PRESENT
    # (classify_claim_record() is lstat + S_ISREG and never opens it) but
    # reads AMBIGUOUS, which is precisely the pair no gate can recover from:
    # every retry sees a claim that exists and cannot be parsed, so the
    # segment is unclaimable until someone deletes the file by hand.
    #
    # Doing the encode here makes the failure unreachable rather than
    # handled: nothing has been created yet, so the state stays ABSENT --
    # which every gate already refuses safely -- and the caller gets the
    # ordinary (False, detail) it knows how to report.
    try:
        blob = body.encode("utf-8")
    except UnicodeEncodeError as exc:
        return (
            False,
            f"could not encode the claim record as UTF-8, so nothing was "
            f"created: {exc}",
        )
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
            sync_problem = fsync_directory(path.parent)
            if sync_problem is not None:
                return (
                    False,
                    f"this run had already claimed this segment, but "
                    f"{sync_problem} -- the existing record cannot be treated "
                    f"as published, so the draft's dispatch_token must not be "
                    f"re-stamped from it",
                )
            return (False, "already claimed by this run")
        return (False, f"claim path is occupied but unusable: {detail}")
    except OSError as exc:
        return (False, f"could not create the claim record: {exc}")
    try:
        # Binary mode over the pre-encoded bytes: text mode would re-do the
        # encode HERE, after the path exists, which is the window the block
        # above exists to remove. `UnicodeError` stays in the except list
        # even though it is now unreachable -- if anyone moves the encode
        # back inside this block, the partial entry gets unlinked instead of
        # stranding the segment.
        with os.fdopen(fd, "wb") as handle:
            handle.write(blob)
            handle.flush()
            os.fsync(handle.fileno())
    except (OSError, UnicodeError) as exc:
        # A record that exists but was never fully written is worse than no
        # record: its pre-claim baseline would be trusted and wrong. Remove
        # the partial entry so the state stays ABSENT, which every gate
        # already refuses safely.
        try:
            path.unlink()
        except OSError:
            pass
        return (False, f"could not write the claim record: {exc}")
    sync_problem = fsync_directory(path.parent)
    if sync_problem is not None:
        # Left on disk on purpose -- see the docstring's "does NOT unlink"
        # paragraph. The record is complete; what is unproven is whether its
        # directory entry survives a crash, and the answer to that is not to
        # remove the entry.
        return (False, f"the claim record was written but {sync_problem}")
    return (True, "")


# ---------------------------------------------------------------------------
# THE SHARED OWNERSHIP PREDICATE (#438, added after the third consecutive
# BLOCKER of the same shape).
#
# Three chokepoints independently decided "is this segment claimed?" and two of
# them got it wrong the SAME way: they built the lookup path out of their OWN
# run id, so they could only ever see their own namespace, and read "I have not
# claimed this" as "nobody has". An ordinary run then translated over a draft
# another run was actively holding -- destroying the hand edit the whole
# feature exists to protect, with no operator intent involved.
#
# Patching each site as it was found is what produced three rounds of the same
# BLOCKER. The fix that makes the class IMPOSSIBLE rather than DETECTED is one
# predicate, here, in the module every reader already imports: a fourth
# chokepoint cannot reintroduce the bug without deliberately declining to call
# this. That is also why the answer is a FUNCTION rather than a documented
# convention -- claim_record.py's own docstring already carried the convention,
# and the convention is exactly what drifted.
# ---------------------------------------------------------------------------


def draft_owner_run_id(dispatch_token):
    """The run id a draft's `dispatch_token` names, or None when it names none.

    Byte-for-byte the parse select_segments.py's `draft_run_id()` and
    draft_ready.py's `_claim_run_id()` already use: a colon is REQUIRED and
    both sides of it must be non-empty. `partition`, never `split(':')[0]` --
    the latter returns a truthy owner for `"RUN-A"` and `"RUN-A:"`, which both
    peers reject, so a guard using it would disagree with the two components
    that decide ownership everywhere else. `partition` and not `rsplit`
    either: a seg id may itself contain a colon (`FRONTBACK:errata_02`), so
    only the FIRST separator delimits the run id."""
    if not isinstance(dispatch_token, str):
        return None
    run_id, sep, rest = dispatch_token.partition(":")
    if not sep or not run_id or not rest:
        return None
    return run_id


def any_foreign_claim(seg, this_run_id, runs_dir):
    """`(run_id, state, path)` for some run OTHER than `this_run_id` holding a
    claim entry for `seg`, or `(None, None, None)`.

    Used ONLY for a draft that names no owner at all. Ownership is normally
    read off the draft's token, and deliberately so: nothing releases a claim,
    so records are immortal, and refusing whenever any foreign record existed
    would make a segment claimed once permanently un-translatable by anybody --
    an ownership guard turned into a project-wide denial of service.

    A TOKENLESS draft is the one case where that reasoning does not apply,
    because there is no token to read an owner from and the state is a
    documented one: D9's lost-token recovery, where a run claimed a segment and
    a later fix round dropped the token from the draft. The claim record is
    then the ONLY surviving evidence of who owns the hand edit, so consulting
    it is the difference between recovering that draft and overwriting it. The
    denial-of-service objection does not bite here either: a project with no
    claim records -- every pre-1.21.0 project -- enumerates nothing and is
    unaffected, and a project that DOES have one plus a tokenless draft is in
    exactly the state that needs a human, not another translation.

    Unreadable entries count as held. An owner that cannot be established is
    never assumed absent; that is the direction every other #438 guard takes."""
    try:
        entries = sorted(runs_dir.iterdir())
    except (FileNotFoundError, NotADirectoryError):
        # DEFINITIVELY no foreign claim. A runs/ that does not exist is the
        # ordinary state of a project that has never claimed anything, and
        # there is nothing there to have missed.
        return (None, None, None)
    except OSError as exc:
        # COULD NOT LOOK -- which is NOT the same answer, and collapsing the
        # two into one `except OSError` was a fail-open defect. A runs/ that
        # is searchable but not readable (mode 0o111) refuses `iterdir()`
        # while every `.claimed.<seg>` inside it stays reachable BY PATH, so
        # a live foreign claim is fully in force and simply invisible to the
        # enumeration. Reporting that as "no foreign claim" handed back
        # permission to overwrite the very draft it protects.
        #
        # Returned as AMBIGUOUS with no holder: the caller must refuse, the
        # same direction every other #438 guard takes when ownership cannot
        # be established. Absence and failure must never print identically.
        return (None, CLAIM_AMBIGUOUS, f"runs directory {runs_dir} could not be listed: {exc}")
    for entry in entries:
        run_id = entry.name
        if run_id == this_run_id or validate_run_id(run_id) is not None:
            continue
        state, _detail = classify_claim_record(entry / f"{CLAIM_PREFIX}{seg}")
        if state != CLAIM_ABSENT:
            return (run_id, state, entry / f"{CLAIM_PREFIX}{seg}")
    return (None, None, None)


def foreign_owner_refusal(*, seg, this_run_id, draft_path, runs_dir):
    """None when a translate dispatch for `seg` may proceed, or a reason string
    when it must be REFUSED because another run owns the draft.

    Call this from EVERY chokepoint that is about to overwrite a draft, in
    addition to -- never instead of -- the caller's own "have I claimed this?"
    check. The two answer different questions and only both together answer
    "may I destroy this draft?".

    The cases, with the safe direction chosen per case rather than uniformly,
    because "cannot determine the owner" is several different situations:

      - NO DRAFT on disk -> proceed. The ordinary first translation; there is
        nothing to overwrite. Refusing here would block every normal dispatch,
        which is the failure mode a blanket "cannot read the owner -> refuse"
        would produce.
      - DRAFT unreadable, not JSON, or not a JSON object -> REFUSE. Content
        exists whose owner cannot be established.
      - TOKEN naming THIS run -> proceed. The ordinary retry/resume case.
      - TOKEN naming ANOTHER run -> that run's record decides. PRESENT or
        AMBIGUOUS both REFUSE (a foreign owner this run cannot read is strictly
        worse than one it can); ABSENT proceeds, because a foreign token whose
        run holds nothing is closer to a LOST claim than a live one, and
        blocking it would strand the recovery draft_ready.py points operators
        at.
      - NO TOKEN AT ALL -> any_foreign_claim() decides; see its docstring. This
        is the D9 lost-token state, and treating it as unowned was a BLOCKER:
        a legacy tokenless draft and a hand edit whose token a fix round
        dropped are indistinguishable from the draft alone."""
    draft_path = Path(draft_path)
    if not draft_path.is_file():
        return None
    try:
        raw = draft_path.read_text(encoding="utf-8")
        doc = json.loads(raw)
    except (OSError, ValueError) as exc:
        # ValueError covers json.JSONDecodeError AND the UnicodeDecodeError a
        # non-UTF-8 draft raises -- the latter is a ValueError, not an OSError,
        # the same trap read_claim_record() and write_claim_record() were both
        # fixed for.
        return (
            f"segment {seg!r} has a draft at {draft_path} that could not be read to "
            f"determine who owns it ({exc}) -- refusing rather than overwrite a draft "
            f"whose owner this run cannot establish (#438 D8)"
        )
    if not isinstance(doc, dict):
        return (
            f"segment {seg!r}'s draft at {draft_path} is not a JSON object, so its "
            f"owner cannot be established -- refusing rather than overwrite it (#438 D8)"
        )
    owner = draft_owner_run_id(doc.get("dispatch_token"))
    if owner is None:
        holder, state, path = any_foreign_claim(seg, this_run_id, Path(runs_dir))
        if holder is None and state == CLAIM_AMBIGUOUS:
            # The enumeration itself failed -- see any_foreign_claim(). Not
            # "nobody holds it": nobody could be LOOKED for, while the records
            # themselves remain reachable by path and any claim in them is
            # still in force.
            return (
                f"segment {seg!r}'s draft at {draft_path} names NO run in its "
                f"dispatch_token, and whether another run holds a claim on it could "
                f"not be established ({path}) -- refusing rather than overwrite a "
                f"draft whose owner cannot be ruled out (#438 D8)"
            )
        if holder is None:
            return None
        return (
            f"segment {seg!r}'s draft at {draft_path} names NO run in its "
            f"dispatch_token, but run {holder!r} holds a claim record for it at "
            f"{path} ({state}). A claimed segment whose token was dropped by a later "
            f"fix round is D9's lost-token state: {holder!r}'s record is the only "
            f"surviving evidence of who owns this draft, and translating would "
            f"overwrite it. Recover it under {holder!r}, or resolve ownership by hand "
            f"(#438 D8)"
        )
    if owner == this_run_id:
        return None
    try:
        foreign_path = claimed_path(owner, seg, Path(runs_dir))
    except ValueError as exc:
        return (
            f"segment {seg!r}'s draft is stamped with a token naming run {owner!r}, "
            f"but no claim record path can be built for that run id ({exc}) -- "
            f"refusing rather than proceed against an owner that cannot be looked up "
            f"(#438 D8)"
        )
    state, detail = classify_claim_record(foreign_path)
    if state == CLAIM_ABSENT:
        return None
    if state == CLAIM_PRESENT:
        return (
            f"segment {seg!r} is OWNED BY RUN {owner!r}, not by this run "
            f"({this_run_id!r}): the draft at {draft_path} is stamped for {owner!r} "
            f"and {owner!r} holds a live claim record at {foreign_path}. Translating "
            f"would overwrite a draft another run is actively working on -- exactly "
            f"what a claim exists to prevent. Work under {owner!r}, or resolve "
            f"ownership by hand first (#438 D8)"
        )
    return (
        f"segment {seg!r}'s draft is stamped for run {owner!r}, whose claim record at "
        f"{foreign_path} could not be read unambiguously ({detail}) -- refusing rather "
        f"than risk overwriting a draft this run cannot prove is unowned (#438 D8)"
    )
