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

    python3 ledger_update.py {seg} --payload-file <path> [--durable-root PATH]

The caller (an agent, shelling out mid-turn) first writes its intended
fields as a JSON object to a scratch payload file -- no shell
interpolation of field values -- then invokes this script with just that
path. The payload may set ONLY: status (required), rounds (a bare
integer), reason, note, cache_key, run_token (a bare RUN_ID string, 1.2.0
addition -- see below). Anything else is refused.

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

1.2.0 addition -- payload's `run_token`: when the payload's status is
'converged', this script ALREADY re-checks draft_sha1 (via
draft_content_sha1(), which deliberately excludes the draft's own
dispatch_token metadata field from the hash -- see that function's own
docstring). `run_token` (a bare RUN_ID string, written by the calling
recordLedgerPrompt agent alongside `cache_key`) layers an INDEPENDENT
precondition on top: this script reconstructs the exact expected draft
token, expected_draft_token(run_token, seg) = '<run_token>:<seg>', and
requires the on-disk draft's own dispatch_token to equal it EXACTLY, and
review.json's own dispatch_token to equal it plus a ':r<roundLabel>'
SUFFIX (review_token_matches(), a prefix match -- review's token format
carries a round label the draft's own form does not). Reconstructing the
FULL expected token (not just a bare RUN_ID comparison) also catches a
same-run-but-wrong-segment token. A mismatch refuses convergence
(structured failure, not recorded) -- closing a stale/straggler
draft-or-review-from-a-different-run gap that a content-sha1 match alone
cannot catch. Optional and backward-compatible when the payload omits it.
"""

import argparse
import copy
import errno
import hashlib
import json
import os
import re
import stat
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
# Self-anchoring by default: this script always lives at
# {durable_root}/scripts/<name>.py. It never assumes cwd == durable_root.
# LT-409: an explicit --durable-root PATH overrides this at runtime (see
# resolve_dirs() below) -- these module-level constants remain the fallback
# used whenever the flag is omitted.
# ---------------------------------------------------------------------------
DURABLE_ROOT = Path(__file__).resolve().parents[1]
SCHEMAS_DIR = DURABLE_ROOT / "schemas"
SEGMENTS_DIR = DURABLE_ROOT / "segments"
RUNS_DIR = DURABLE_ROOT / "runs"
LEDGER_FRAGMENT_DIR = RUNS_DIR / "ledger.d"


def resolve_dirs(durable_root_str):
    """LT-409: when `durable_root_str` is given (the --durable-root CLI
    value), every path this script derives is rebuilt from THAT root
    instead of the self-anchored module-level constants above. Returns a
    dict with keys durable_root/schemas_dir/segments_dir/runs_dir/
    ledger_fragment_dir. `durable_root_str=None` reproduces today's
    self-anchored values unchanged."""
    if durable_root_str is None:
        return {
            "durable_root": DURABLE_ROOT,
            "schemas_dir": SCHEMAS_DIR,
            "segments_dir": SEGMENTS_DIR,
            "runs_dir": RUNS_DIR,
            "ledger_fragment_dir": LEDGER_FRAGMENT_DIR,
        }
    root = Path(durable_root_str).resolve()
    runs_dir = root / "runs"
    return {
        "durable_root": root,
        "schemas_dir": root / "schemas",
        "segments_dir": root / "segments",
        "runs_dir": runs_dir,
        "ledger_fragment_dir": runs_dir / "ledger.d",
    }

# The only statuses ledger_update.py itself ever writes to a fragment.
# 'stale' is never one of them -- that status is computed by ledger_merge.py
# only in the materialized ledger.json, never found on a fragment on disk.
FRAGMENT_STATUS_FALLBACK_ENUM = [
    "pending", "in_progress", "converged", "non_converged", "blocked",
]


def draft_path(seg, segments_dir=SEGMENTS_DIR):
    return segments_dir / f"{seg}.draft.json"


def review_path(seg, segments_dir=SEGMENTS_DIR):
    return segments_dir / f"{seg}.review.json"


def ever_converged_path(seg, segments_dir=SEGMENTS_DIR):
    """#409 Step 1: the DURABLE 'this segment has converged at least once'
    sentinel. A dotfile, matching the existing `.att.*`/`.att_pending.*`
    convention in the same directory -- tree walkers here already skip
    dot-entries (diff_rendered_output.py, render_obsidian.py) and nothing
    globs this directory wholesale, so it adds no file a consumer must learn
    to ignore."""
    return segments_dir / f".ever_converged.{seg}"


# ---------------------------------------------------------------------------
# The shared sentinel-presence predicate. This block is an EXACT duplicate of
# the copy in the other three sentinel scripts (search `SENTINEL_ABSENT` in
# select_segments.py, final_audit.py and backfill_ever_converged.py) -- see
# classify_ever_converged_sentinel()'s docstring for why it is duplicated
# rather than imported, and which test pins the four copies together.
# ---------------------------------------------------------------------------

SENTINEL_ABSENT = "absent"
SENTINEL_PRESENT = "present"
SENTINEL_AMBIGUOUS = "ambiguous"


def _sentinel_entry_kind(mode: int) -> str:
    """A human name for the st_mode of whatever occupies a sentinel path --
    it goes straight into an operator-facing message, which has to say what
    is actually sitting there before it can ask anyone to fix it."""
    if stat.S_ISLNK(mode):
        return "a symbolic link"
    if stat.S_ISDIR(mode):
        return "a directory"
    if stat.S_ISFIFO(mode):
        return "a FIFO"
    if stat.S_ISSOCK(mode):
        return "a socket"
    if stat.S_ISBLK(mode):
        return "a block device"
    if stat.S_ISCHR(mode):
        return "a character device"
    return f"a non-regular entry (st_mode {stat.S_IFMT(mode):#o})"


def classify_ever_converged_sentinel(path, *, dir_fd=None) -> "tuple[str, str]":
    """Three-state classification of the `.ever_converged.<seg>` entry at
    `path`: `(SENTINEL_ABSENT|SENTINEL_PRESENT|SENTINEL_AMBIGUOUS, detail)`.

    THE SHARED PREDICATE. Every script that asks whether a segment has ever
    converged calls this, and all four must agree on it:
    ledger_update.py's `mark_ever_converged()` (the only writer),
    select_segments.py's #409 Step 1 dispatch gate,
    final_audit.py's `count_stale_previously_converged()` carve-out, and
    backfill_ever_converged.py's `already_sentineled` scan.

    DUPLICATED RATHER THAN IMPORTED because importing it would be a live
    hazard -- NOT because of the "no shared lib between self-contained
    scripts" convention, which is already false here (canon_validate.py and
    glossary_batch_plan.py import canon_senses.py; scaffold_setup.py imports
    cache_key.py). The real reason: ledger_update.py is a
    PLUGIN_BUNDLE_MEMBERS entry, and cache_key.py:102-109 records that that
    tuple is a literal byte-hash allowlist to which a TRANSITIVE IMPORT IS
    INVISIBLE -- which is why canon_senses.py had to be registered
    explicitly once two members imported it. A shared module would put this
    predicate's bytes outside the hash meant to cover them, so WEAKENING
    this guard would no longer move plugin_bundle_hash, and every durable
    root scaffolded beforehand would go on trusting it: the exact
    false-green cache_key.py:116-120 names. Consolidation stays possible --
    it just has to register the new module in PLUGIN_BUNDLE_MEMBERS in the
    same commit.

    What keeps the four copies honest is ENFORCEMENT, not discipline. A
    remembered convention rots -- this docstring's own first version cited
    the false one -- while a test that fails loudly does not.
    tests/select_segments.test.py's
    test_sentinel_predicate_is_identical_in_all_four_scripts pins the copies
    byte for byte and across the state matrix; its
    test_exactly_these_four_scripts_participate_in_the_sentinel_contract
    fails when a fifth copy appears or one of the four goes away.

    Why three states, and why not `Path.exists()`. `exists()` answers the
    wrong question three ways, and NOT all of them in the same direction --
    an earlier draft of this docstring said "twice over, and BOTH point at
    absent", which is the claim the CHANGELOG had to correct. Two of the
    three do point at "absent", and that is the direction that authorizes
    destroying converged work:

      1. It FOLLOWS symlinks, so a DANGLING symlink named as the sentinel
         reads as absent -- while the writer's `os.open(O_CREAT|O_EXCL)` gets
         EEXIST from that same symlink and reports the segment successfully
         marked. That split is the whole finding: a segment recorded as
         converged that the gate then sees as unprotected and retranslates.
         Verified on this project's Python (3.14.6): `exists()` -> False,
         `os.open` -> FileExistsError, for one and the same dangling link.
      2. Since Python 3.14 `exists()` swallows EVERY OSError and returns
         False, so an EACCES/ESTALE/EIO on the lookup is reported as "this
         segment never converged". Verified on 3.14.6: with an unreadable
         parent directory `exists()` returns False while `lstat()` raises
         EACCES. (On 3.10-3.13 the same call re-raised for EACCES but still
         swallowed ELOOP/ENOTDIR/EBADF -- so no supported version answers
         this correctly, and the version-dependence is itself a reason not
         to route a data-loss guard through `exists()`.)
      3. In the OTHER direction: a DIRECTORY at the marker's path is
         `exists() == True`, so `exists()` reports converged a segment the
         writer never marked. That one cannot destroy finished work, which is
         why it went unnoticed -- but it is the reason "exists() at least
         fails safe in one direction" is false, and the reason the fix is a
         third state rather than a flipped default.

    So: only ENOENT means absent, and it is determined by catching
    FileNotFoundError rather than by comparing `exc.errno`, so the verdict
    never depends on an errno that may be None. `lstat`, deliberately not
    `stat` -- a symlink is not something `mark_ever_converged()` can have
    (its O_CREAT|O_EXCL open refuses to write through one), so following a
    link would only ask the question about some unrelated file. Either way
    only the final `.ever_converged.<seg>` component is left unresolved:
    WITHOUT `dir_fd` the PARENT components still resolve normally, so a
    project whose whole `segments/` directory is a symlink is unaffected;
    WITH `dir_fd` there are no parent components left to resolve, because
    the caller already resolved them once, when it opened the descriptor.

    `dir_fd` -- OPTIONAL, and today TWO callers pass it:
    backfill_ever_converged.py's census and select_segments.py's #409 Step 1
    dispatch gate, each of which opens `segments/` once and reads every
    entry through that descriptor. Omitted (every other caller), the
    lookup resolves the whole pathname afresh, which is the right thing for
    a reader that holds nothing open. Passed, the BASENAME is looked up
    relative to that descriptor instead, and `segments/` is not resolved by
    pathname at all. The difference matters only for a caller that already
    HOLDS the directory open and acts on its census afterwards, which is
    what both of those do: the backfill opens `segments/` once, does every
    write relative to the descriptor, and samples directory identity at the
    end; the dispatch gate opens it before the census and refuses outright
    when it cannot (#621). A census
    resolving the pathname afresh could therefore classify entries in a
    DIFFERENT directory than the one being written to -- re-point
    `segments/` at B for the length of the census and back to A before the
    run ends, and B's sentinel is reported as A's protection while the
    final identity sample compares A to A and agrees. Reproduced by review,
    not theorised. Binding the census to the descriptor removes that
    interleaving with no locking protocol at all, because the descriptor is
    already held; a caller that holds none gains nothing here and passes
    None.

    Anything that is neither ENOENT nor a regular file is AMBIGUOUS: it MAY
    be a converged segment whose sentinel this process cannot see. Each
    caller then maps AMBIGUOUS to ITS OWN work-preserving side, and that is
    deliberately NOT the same action in all four: the writer and the
    dispatch gate REFUSE (never destroy or mis-record converged work), while
    final_audit.py's carve-out COUNTS it (never declare a converged book
    incomplete and therefore undeliverable) and backfill's scan reports it
    unprotected (never claim protection it did not verify). One predicate,
    four deliberate mappings -- see each call site's own comment. The
    asymmetry is the reason a false "absent" is the unacceptable answer
    everywhere: it costs a finished translation, or a finished book.
    """
    try:
        # `path.name` is the basename and the descriptor is its parent, so
        # the `dir_fd` branch resolves no part of `segments/` by pathname.
        # `os.lstat` keeps `follow_symlinks` off exactly as `Path.lstat`
        # does, so the FINAL component stays unresolved either way and both
        # branches raise the same exceptions into the same handlers below.
        st = path.lstat() if dir_fd is None else os.lstat(path.name, dir_fd=dir_fd)
    except FileNotFoundError:
        return (SENTINEL_ABSENT, "")
    except OSError as exc:
        # `OSError.errno` is typed `int | None` and genuinely can be None. A
        # missing errno is the LEAST informative failure there is, so it
        # lands on the ambiguous side like every other non-ENOENT outcome --
        # never silently treated as "some other errno", and above all never
        # as absence. The ENOENT verdict above does not consult `errno` at
        # all (FileNotFoundError IS ENOENT by construction), so a None errno
        # can never reach it, which is why this branch can be a plain guard
        # rather than a three-way comparison.
        if exc.errno is None:
            return (SENTINEL_AMBIGUOUS, f"lstat failed with no errno: {exc}")
        code = errno.errorcode.get(exc.errno, f"errno {exc.errno}")
        return (SENTINEL_AMBIGUOUS, f"lstat failed with {code}: {exc.strerror or exc}")
    if stat.S_ISREG(st.st_mode):
        return (SENTINEL_PRESENT, "")
    return (
        SENTINEL_AMBIGUOUS,
        f"the entry is {_sentinel_entry_kind(st.st_mode)}, not a regular file",
    )


_SENTINEL_REMEDY_DISPLACED = (
    "Find out what replaced the segments directory during the run -- a "
    "concurrent backfill, an interrupted move, or a restore from backup -- "
    "put the intended directory back at that path, then re-run. Convergence "
    "was deliberately NOT recorded: the marker is durable under the "
    "displaced directory, not under the path the dispatch gate reads."
)

_SENTINEL_REMEDY_OS = (
    "Retry once the underlying OS problem (permissions/quota/I/O) is fixed."
)

_SENTINEL_REMEDY_OCCUPIED = (
    "Nothing this script wrote is at that path: mark_ever_converged() only "
    "ever publishes a REGULAR file (os.open with O_CREAT|O_EXCL|O_WRONLY), so "
    "whatever occupies it came from somewhere else. Resolve it at the path "
    "before retrying -- if you can establish that this segment really did "
    "converge, replace the entry with a regular sentinel file whose contents "
    "are the single line 'converged' -- that still protects the segment, and "
    "backfill_ever_converged.py's census reports it as 'unattributed', "
    "because an operator assertion carries none of the evidence a real "
    "convergence writes into the marker; if it did not, remove the entry and let "
    "the next convergence publish the sentinel itself. Do NOT simply delete "
    "it to make this message go away: select_segments.py's dispatch gate "
    "reads the same path, and deleting a sentinel is what makes a converged "
    "segment eligible for silent retranslation."
)

_SENTINEL_REMEDY_VANISHED = (
    "The filesystem is fine; just retry -- the next attempt's own "
    "O_CREAT|O_EXCL open will create the sentinel."
)


# The marker's own self-identification. A reader that finds some other JSON
# object at the path must be able to say "this is not one of ours" rather than
# guessing, so both fields are checked before any other field is believed.
SENTINEL_MARKER_NAME = "ever_converged"
SENTINEL_BODY_VERSION = 1

# The marker's body never exceeds this, BY CONSTRUCTION rather than by
# convention -- sentinel_body() drops the evidence and emits identity alone
# rather than publish a body its reader would have to truncate. The reader
# (backfill_ever_converged.py's read_sentinel_attribution()) refuses anything
# longer, so an unbounded writer field would have produced markers that are
# genuinely earned and REPORT AS `unattributed`, defeating #443 on exactly the
# markers it exists for. Reachable without anything hostile: `run_token` is a
# free-form string with no length constraint anywhere in the payload schema.
SENTINEL_BODY_MAX_BYTES = 4096

# Written by THIS script's mark_ever_converged(); backfill_ever_converged.py
# writes its own name here. #443: the two writers used to publish the identical
# fixed bytes `b"converged\n"`, so nothing on disk could tell a marker earned at
# a real convergence from one a backfill or an operator asserted -- the marker
# was strong enough to BLOCK and too empty to AUTHORISE.
SENTINEL_WRITER_NAME = "ledger_update"


def sentinel_body(seg, writer, evidence=None) -> bytes:
    """The marker's body: ONE line of UTF-8 JSON, sorted keys, trailing LF.

    Byte-identical in spelling to the sibling writer's copy -- ledger_update.py
    and backfill_ever_converged.py each carry one, pinned against each other by
    `tests/backfill_ever_converged.test.py` -- because a reader must be able to
    parse either writer's output with one rule. What differs, deliberately and
    for the first time, is `by`: #443 exists because the two writers were
    INDISTINGUISHABLE on disk.

    `evidence` is the caller's justification -- whatever it can actually
    prove. It is merged FIRST and the writer-owned identity fields are
    assigned AFTER it, so no caller can forge `by`, `marker`, `v` or `seg`;
    a direct in-process caller supplying `{"by": "..."}` moves nothing.

    BOUNDED, and by dropping evidence rather than by truncating it. A body
    that would exceed SENTINEL_BODY_MAX_BYTES is re-emitted with the identity
    fields alone, because a truncated JSON body is not shorter evidence, it is
    unparseable evidence, and the reader would report the marker `unattributed`
    either way. Losing the evidence while keeping a marker that still says WHO
    wrote it is the better half. All-or-nothing on purpose: choosing which
    evidence to keep would put schema-specific priorities inside a serializer
    that is duplicated across two scripts and knows nothing about either.

    The identity fallback is not re-measured, and the claim that it fits is
    OPERATIONAL rather than proven by this function: `validate_seg()` bounds a
    segment id's CHARACTERS, not its length, and `writer` is an ordinary
    argument. What actually holds is narrower -- both shipped writer names are
    fixed and short, and a `seg` long enough to overflow 4096 bytes cannot
    reach a published marker at all, because `.ever_converged.<seg>` would
    exceed the filesystem's own component limit (255 on this project's
    platform, so at most 239 bytes of segment id) and the publishing open or
    link fails first. A third writer with an unbounded name would break that,
    which is why backfill_ever_converged.py's SENTINEL_KNOWN_WRITERS -- the
    reader's closed set -- is registered by hand.

    WHAT THIS BODY IS NOT: it is not authority. Nothing in this plugin gates
    on it, and classify_ever_converged_sentinel() above still decides
    protection from the entry's TYPE alone. A marker written before this
    field existed -- every marker on every live project today -- carries no
    body this parses, keeps classifying SENTINEL_PRESENT, and keeps blocking
    dispatch exactly as it did. That is the whole reason the provenance went
    into the body rather than into the predicate."""
    def encode(fields):
        return (
            json.dumps(fields, sort_keys=True, ensure_ascii=False,
                       separators=(",", ":"))
            + "\n"
        ).encode("utf-8")

    identity = {
        "marker": SENTINEL_MARKER_NAME,
        "v": SENTINEL_BODY_VERSION,
        "by": writer,
        "seg": seg,
    }
    fields = {k: v for k, v in dict(evidence or {}).items() if v is not None}
    fields.update(identity)
    body = encode(fields)
    if len(body) > SENTINEL_BODY_MAX_BYTES:
        return encode(identity)
    return body


def write_all(fd, data: bytes) -> None:
    """`os.write()` until every byte is out, or raise.

    A single `os.write()` may return a SHORT count, and the old call site
    treated whatever it returned as success. With a 10-byte body that was
    theoretical; the provenance body is an order of magnitude longer, so the
    loop is spelled out rather than left as an assumption. A zero-byte return
    is RAISED rather than looped on -- spinning on it would hang the writer,
    which is strictly worse than the clean refusal each writer's own OSError
    handler gives every other write failure."""
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise OSError(
                f"os.write() returned {written} with {len(view)} byte(s) of "
                f"the sentinel body still unwritten"
            )
        view = view[written:]


def _report_sentinel_failure(path, exc, remedy=_SENTINEL_REMEDY_OS):
    """The one place this message is spelled out -- shared by every OSError
    exit from mark_ever_converged() below (open, write, and close alike),
    so a future edit to the wording can't drift into three copies the way
    the open-only version of this function once left the write/close paths
    with no message at all (see mark_ever_converged()'s own docstring).

    `remedy` is the only part that varies, and it is a parameter rather than
    a second copy of this function for exactly that reason: the refusal's
    consequence paragraph (convergence not recorded, status unchanged,
    draft/review artifacts intact) is identical whatever went wrong, and the
    only thing an OS error and an occupied path differ on is what the
    operator should DO next. `exc` is interpolated, so it may be an exception
    or a plain reason string."""
    sys.stderr.write(
        f"warning: could not create the ever-converged sentinel at {path}: "
        f"{exc}. Convergence was NOT recorded for this segment -- the "
        f"ledger write is refused without its protecting sentinel, so "
        f"the segment stays whatever status it already had. Nothing on "
        f"disk was lost: the draft and review artifacts both survive "
        f"untouched; only the ledger's own 'converged' verdict is "
        f"withheld. {remedy}\n"
    )


# getattr, not a bare os.O_*: none of these three flags exists on every
# platform Python runs on, and the same guarded idiom already spells
# claim_record.py's, codex_job.py's and scaffold_setup.py's own constants.
# Falling back to 0 keeps the open legal there; _fsync_one() below treats a
# refusal as a durability failure either way, so a fallback can never turn
# into a silent success.
_O_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_O_NONBLOCK = getattr(os, "O_NONBLOCK", 0)


def _fsync_fd(fd: int, label: str) -> "str | None":
    """fsync an ALREADY-OPEN descriptor, naming `label` on failure. Separate
    from _fsync_one() below because the caller that pins a directory must
    keep that descriptor open across the file sync -- reopening the
    directory by name is precisely the window this split exists to close."""
    try:
        os.fsync(fd)
    except OSError as exc:
        return f"could not fsync {label}: {exc}"
    return None


def _fsync_one(target: str, label: str, extra_flags: int = 0,
               dir_fd: "int | None" = None) -> "str | None":
    """fsync one already-published path, naming `label` in either error and
    guarding the open with `extra_flags`. When `dir_fd` is given, `target` is
    resolved RELATIVE to that descriptor rather than against the process cwd,
    which is what binds the open to a directory the caller has pinned.
    Returns None on success or a short error string.

    The descriptor is closed in a `finally` so a failing fsync cannot leak
    it, and the close's own OSError is swallowed on purpose -- the fsync
    result already decided this call's outcome, and a close() failure after
    a successful fsync reports nothing a caller can act on differently."""
    try:
        fd = os.open(target, os.O_RDONLY | extra_flags, dir_fd=dir_fd)
    except OSError as exc:
        return f"could not open {label} for fsync: {exc}"
    try:
        os.fsync(fd)
    except OSError as exc:
        return f"could not fsync {label}: {exc}"
    finally:
        try:
            os.close(fd)
        except OSError:
            pass  # best-effort close; the fsync result above already decided this call's outcome
    return None


def _sync_published_sentinel(path, *, dir_fd: int) -> "str | None":
    """Make one ever-converged sentinel's publication durable, entirely
    relative to `dir_fd` -- a descriptor on the directory that RECEIVED the
    sentinel, opened by the caller before it touched the entry. fsync the
    sentinel FILE, then fsync that directory, so the entry that makes the
    file findable survives a crash too: fsync on a file commits its
    contents, not its link. Same two-halves reasoning claim_record.py's
    fsync_directory() docstring states for the claim record
    (claim_record.py:565-577).

    Returns None on success, or a short error string naming which half
    failed. Deliberately does NOT import claim_record.fsync_directory():
    this module does not import claim_record today, and reject_review.py /
    select_segments.py both document why a bare `import claim_record` is
    unsafe here -- sys.path[0] can resolve into a codex-writable tree (#412).

    FAIL-CLOSED, unlike write_fragment_atomically()'s own directory sync
    (which swallows its OSError as "best-effort ... not fatal"). That is the
    right policy for a fragment already committed via os.replace(); it is
    the wrong one here. A directory entry that is not durable is exactly the
    one a crash can lose while the artifact it backs survives -- and for
    THIS sentinel that artifact is the 'converged' fragment
    mark_ever_converged() exists to protect. A best-effort sync would return
    success having established nothing, which is the shape of the defect
    this fold exists to close, not its fix.

    THE `dir_fd` PARAMETER IS THE WHOLE POINT, and it is not a convenience.
    Two MR review rounds reproduced the same class of defect against earlier
    revisions that resolved `segments/` BY NAME -- first after the file
    sync, then after the sentinel create. Both times a rename of `segments/`
    mid-call redirected a sync onto whatever had taken the name, while the
    directory that actually received the sentinel went unsynced and the
    function reported success. Narrowing the window twice did not close it,
    because the window is the pathname resolution itself. So this function
    no longer resolves any part of `segments/`: the caller pins it once,
    before the create, and every operation here is relative to that
    descriptor. Do not add a path-based open back.

    The sentinel's own open still guards the FINAL component with
    O_NOFOLLOW | O_NONBLOCK -- it is never legitimately a symlink
    (classify_ever_converged_sentinel() above requires S_ISREG on an LSTAT,
    so a symlink-to-regular classifies AMBIGUOUS and never reaches here),
    and a FIFO left at the path would otherwise block the ledger writer
    forever. A symlinked `segments/` stays supported: it is resolved once by
    the caller's open, and O_NOFOLLOW binds only the final component."""
    failure = _fsync_one(
        path.name, "the sentinel", _O_NOFOLLOW | _O_NONBLOCK, dir_fd=dir_fd,
    )
    if failure is not None:
        return failure
    return _fsync_fd(dir_fd, str(path.parent))


def _pinned_dir_still_canonical(segments_dir, dir_fd: int) -> "str | None":
    """None if `segments_dir` still NAMES the directory `dir_fd` is held on,
    or an error string if the pathname has been displaced.

    Why this is needed on top of the descriptor pin, which is a distinction
    that took an MR round to see: the pin guarantees the create and both
    fsyncs land on ONE directory -- the one that received the sentinel. It
    does not guarantee that directory is still what `segments/` resolves to.
    Rename it aside mid-call and the marker is durably written somewhere the
    dispatch gate never looks: reproduced on this branch, with
    classify_ever_converged_sentinel() returning SENTINEL_ABSENT at the
    canonical path while this function returned success. The caller then
    records convergence over a segment nothing protects, and the next
    cache-key move retranslates it -- the exact loss the sentinel exists to
    prevent. What the caller needs promised is "a marker is at the path the
    reader reads", not "a marker is durable somewhere".

    Modelled on backfill_ever_converged.py's check_segments_dir_identity(),
    the same test the sibling writer of this artifact already performs, and
    kept SEPARATE from the syncs for the reason that one gives: an unsynced
    directory is settled by re-running and a displaced one is not, so an
    operator acts on them differently and they must not share a message.

    WHAT THIS DOES NOT PROVE, stated because the sibling's docstring records
    three earlier versions that claimed otherwise and were wrong every time:
    it samples identity ONCE. That detects a pathname displaced AT THAT
    INSTANT and says nothing about any instant after it -- the dispatch gate
    resolves `segments/` by pathname at its own, later time, and a
    displacement in between is outside anything this call can observe. This
    makes the function FAIL CLOSED on an observable displacement; it does not
    make it race-free, and no check inside one process could."""
    try:
        held = os.fstat(dir_fd)
        current = os.stat(str(segments_dir))
    except OSError as exc:
        return (
            f"could not confirm that {segments_dir} still names the directory "
            f"this call wrote the sentinel into: {exc}"
        )
    if (held.st_dev, held.st_ino) != (current.st_dev, current.st_ino):
        return (
            f"{segments_dir} was replaced during this call: the sentinel was "
            f"written durably into the directory that path named at the start, "
            f"but that is no longer the directory the dispatch gate will read, "
            f"so the segment is NOT protected at the canonical path"
        )
    return None


def mark_ever_converged(seg, segments_dir=SEGMENTS_DIR, provenance=None):
    """Create the sentinel for `seg`, idempotently.

    `provenance` (#443) is the evidence this convergence actually had, and it
    is OPTIONAL: omitted, the marker still carries `marker`/`v`/`by`/`seg` and
    simply claims nothing further. The real caller -- enrich_converged_fields()
    below, the single site in the plugin where convergence is fixed -- passes
    the run token, the review's round label and the reviewed draft sha1 it is
    about to write into the fragment. See sentinel_body() for what the body is
    and, more importantly, for what it is NOT: no gate reads it. Called ONLY from
    enrich_converged_fields, after every convergence precondition has passed
    -- that function is the single place in the whole plugin where
    convergence is recorded.

    Why a separate file rather than reading the ledger status: the status is
    MUTABLE and is overwritten with `in_progress` BEFORE a re-dispatch, by
    which time a status-based guard can no longer tell that the segment had
    ever converged -- so it never fires on the one path it exists to guard.
    ledger_update.py rebuilds each fragment from scratch; this sentinel is a
    separate file it only ever creates.

    Never removed by any ledger write. The single sanctioned way to clear it
    is an explicit, authorized re-translate of that segment.

    Failure to create the sentinel IS FATAL to recording convergence
    (post-review correction). The original version of this docstring called
    a failure here non-fatal, reasoning that the convergence was already
    proven and refusing would discard paid work over a bookkeeping file --
    that reasoning had the dangerous direction backwards. This sentinel is
    the ONLY thing that later refuses to re-select and retranslate a segment
    that has already converged (see the "MUTABLE status" paragraph above): a
    ledger fragment recorded as 'converged' WITHOUT it is a segment that
    looks done but carries no protection, and a later re-dispatch will
    silently retranslate it -- discarding the exact work this call exists to
    protect. The caller (enrich_converged_fields, below) now checks this
    return value and refuses to record convergence at all when it is False.
    Nothing already on disk (the draft/review artifacts) is lost either way
    -- only the ledger's own 'converged' verdict is withheld until the
    sentinel can actually be written, on this attempt or a retry. Still
    reported on stderr in addition to the fatal failure, so the underlying
    OS problem is visible without having to parse the JSON error.

    ALL THREE OS calls this function makes -- open, write, close -- are
    covered by that same clean-False-plus-message contract (second post-
    review correction). The first cut of this fix only wrapped open(): the
    single os.write() and the os.close() that follow a successful open()
    were left outside any except OSError, so an ENOSPC/EDQUOT/EIO on the
    write, or a write error some filesystems (notably NFS) defer reporting
    until close(), propagated as an uncaught exception instead of the
    documented refusal -- exactly the failure this promise exists to turn
    into a clean, actionable message.

    The create-then-fill ORDER is deliberately left unchanged, and this is
    NOT a temp-file-plus-`os.link()` atomic publish the way
    backfill_resume_gate_ack.py's write_ack()/_publish_ack() is for
    `.resume_gate_ack` -- that script needed it because ITS marker's JSON
    BODY is read later (by a human or a future consumer, per its own
    docstring), so a torn write there corrupts information someone will
    parse. This sentinel's body is no longer fixed -- #443 put this
    convergence's own justification in it -- but it is still not AUTHORITY,
    and that is the property this paragraph rests on. Every one of the five
    consumers (assemble.py, select_segments.py, final_audit.py,
    backfill_ever_converged.py and this function itself) asks only whether a
    regular file is there, through the one shared
    classify_ever_converged_sentinel() predicate above; the only reader of
    the body is backfill_ever_converged.py's census, which REPORTS what it
    finds and gates on nothing. So a torn body costs a diagnostic line, never
    a protection decision -- which is why the create-then-fill order still
    does not need to become an atomic publish. And
    a torn write here can never represent a FALSE fact -- with the scope of
    that claim now stated, because leaving it implicit is what made the old
    FileExistsError branch look safe. It holds only for an entry THIS
    FUNCTION wrote: this function only ever runs after every convergence
    precondition already passed, so a regular file left at `path` by a torn
    run of it -- complete or torn -- correctly asserts "this segment
    converged at least once", which is true. It says NOTHING about an entry
    that arrived some other way, and the old branch silently generalized it
    to every entry EEXIST can report (see the next paragraph). A retry's own
    os.open() O_CREAT|O_EXCL then hits FileExistsError against that leftover
    REGULAR file, the predicate classifies it as present, and it is treated
    as already-marked, exactly as if the first attempt had fully succeeded. So
    the ONLY thing worth guaranteeing here is that this function itself
    never raises past its own documented contract -- not that the sentinel's
    bytes are written atomically.

    EEXIST ALONE IS NOT THAT PROOF, which is the fail-closed correction at
    the FileExistsError branch below (third post-review correction).
    O_CREAT|O_EXCL reports EEXIST for any existing entry, including a
    directory and a dangling symlink; neither is a sentinel this function
    wrote, and returning True for them recorded convergence with no
    protection actually in place -- while the reader, which followed the
    link, called the same path absent and retranslated the segment. See that
    branch's own comment for the full mechanism.

    FOURTH POST-REVIEW CORRECTION (#441 RAW #8): the paragraph above stops
    at "never raises past its own contract", and that stopping point
    predates this correction and is no longer the whole story. Not raising
    says nothing about SURVIVING A CRASH -- until now, neither the write nor
    the create was ever fsynced, while the converged FRAGMENT this sentinel
    protects (written by write_fragment_atomically(), elsewhere in this
    file) always was. A crash between the two could keep the fragment and
    lose the marker, and the next cache-key move would silently retranslate
    already-converged work -- the same class of loss the EEXIST correction
    above closes for a different cause. Both return-True paths below now
    call _sync_published_sentinel() -- the fresh-create path after its own
    os.close() succeeds, the already-present path before returning -- and a
    sync failure on EITHER path returns False through the same
    _report_sentinel_failure() contract as every other failure here,
    fail-closed rather than best-effort (see that helper's own docstring for
    why, citing claim_record.py:565-577). Covering the already-present path
    too, not only fresh-create, is load-bearing: without it, a retry after a
    failed sync would hit FileExistsError, classify SENTINEL_PRESENT, and
    return True having synced nothing -- laundering the earlier failure into
    a green result. `tests/backfill_ever_converged.test.py` pins this
    directly against the shipped laundering test for the sibling writer
    (`backfill_ever_converged.py:603-623`, test `:1644-1709`)."""
    path = ever_converged_path(seg, segments_dir)

    # ONE pathname resolution of segments/, here, BEFORE the entry is
    # touched. Everything below -- the create, the EEXIST classification and
    # both fsyncs -- is relative to this descriptor, so a rename of
    # segments/ during the call can no longer redirect any of them onto a
    # directory that did not receive the sentinel. Two MR review rounds
    # reproduced that redirection against revisions that pinned later; see
    # _sync_published_sentinel()'s docstring for why narrowing the window
    # was the wrong shape of fix.
    try:
        seg_dir_fd = os.open(str(segments_dir), os.O_RDONLY | _O_DIRECTORY)
    except OSError as exc:
        _report_sentinel_failure(path, exc)
        return False
    try:
        return _mark_ever_converged_within(
            path, seg_dir_fd, segments_dir,
            sentinel_body(seg, SENTINEL_WRITER_NAME, provenance),
        )
    finally:
        try:
            os.close(seg_dir_fd)
        except OSError:
            pass  # best-effort close; the return value above already decided this call


def _mark_ever_converged_within(path, seg_dir_fd: int, segments_dir,
                                body: bytes) -> bool:
    """mark_ever_converged()'s body, with `segments/` already pinned as
    `seg_dir_fd`. Split out purely so the descriptor's close can live in one
    `finally` rather than on every one of this body's eight return paths."""
    try:
        fd = os.open(
            path.name, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644,
            dir_fd=seg_dir_fd,
        )
    except FileExistsError:
        # EEXIST is NOT proof that a previous run of this function published
        # a sentinel here. O_CREAT|O_EXCL reports it for ANY existing entry:
        # a directory raises it, and so does a DANGLING SYMLINK -- both
        # verified on this project's Python 3.14.6. Returning True on those
        # was the fail-OPEN half of a data-loss bug, because the reader in
        # select_segments.py disagreed about the same path: `Path.exists()`
        # follows the link and reports the dangling case ABSENT. So the
        # segment was recorded as converged while the dispatch gate saw it as
        # unprotected, and the next cache-key move retranslated it -- exactly
        # the loss this sentinel exists to prevent, with the ledger asserting
        # the protection was in place.
        #
        # Both halves now route through the SAME predicate
        # (classify_ever_converged_sentinel, duplicated verbatim in both
        # scripts, drift-tested against each other), so there is no longer a
        # path where the writer says "marked" and the reader says "absent".
        state, detail = classify_ever_converged_sentinel(path, dir_fd=seg_dir_fd)
        if state == SENTINEL_PRESENT:
            # Sync here too, not just on the fresh-create path below: a
            # retry landing on this branch is exactly what happens after an
            # earlier call published the entry but failed its own sync (see
            # this function's docstring, fourth post-review correction).
            # Returning True unconditionally would launder that failure into
            # a green result over a directory entry that was never made
            # durable.
            sync_error = _sync_published_sentinel(path, dir_fd=seg_dir_fd)
            if sync_error is not None:
                _report_sentinel_failure(path, sync_error)
                return False
            displaced = _pinned_dir_still_canonical(segments_dir, seg_dir_fd)
            if displaced is not None:
                _report_sentinel_failure(path, displaced, _SENTINEL_REMEDY_DISPLACED)
                return False
            return True      # already marked -- idempotent, nothing to do
        if state == SENTINEL_ABSENT:
            # Raced: the entry existed at os.open() and was gone by the lstat
            # a moment later. Refuse rather than retry in-place -- a retry
            # loop here would be racing the same unknown deleter, and the
            # caller's refusal to record convergence is already the
            # work-preserving outcome (nothing on disk is lost, and the next
            # attempt simply creates the sentinel).
            _report_sentinel_failure(
                path,
                "the entry reported by O_CREAT|O_EXCL as already existing had "
                "vanished by the time it was examined",
                _SENTINEL_REMEDY_VANISHED,
            )
            return False
        _report_sentinel_failure(path, detail, _SENTINEL_REMEDY_OCCUPIED)
        return False
    except OSError as exc:
        _report_sentinel_failure(path, exc)
        return False

    # #443: the body carries this convergence's own justification. It used to
    # be the fixed bytes `b"converged\n"`, on the stated grounds that a varying
    # body would make an otherwise identical project directory compare unequal
    # -- but nothing in this plugin ever compared them: no digest, diff, cache
    # key, resume identity or attestation reads these bytes, and every gate
    # classifies on the entry's TYPE, which is what makes the change safe.
    # What the fixed body DID buy was that a marker asserted by a backfill or
    # by an operator was indistinguishable from one earned here, which is the
    # defect #443 names.
    #
    # Still no timestamp: the body is derived entirely from the convergence it
    # records, so re-deriving it produces the same bytes.
    try:
        write_all(fd, body)
    except OSError as exc:
        try:
            os.close(fd)
        except OSError:
            pass  # best-effort cleanup; already reporting the write failure
        _report_sentinel_failure(path, exc)
        return False

    # fsync the descriptor THIS call created, before closing it -- never a
    # reopen by name. The fd is already bound to the inode that was just
    # written, so nothing a concurrent rename does can redirect it.
    sync_error = _fsync_fd(fd, "the sentinel")
    if sync_error is not None:
        try:
            os.close(fd)
        except OSError:
            pass  # best-effort cleanup; already reporting the fsync failure
        _report_sentinel_failure(path, sync_error)
        return False

    try:
        os.close(fd)
    except OSError as exc:
        # Some filesystems (notably NFS) defer reporting a write error until
        # close() -- caught here so it gets the SAME clean refusal as a
        # failure at open(), write() or the fsync above would, never an
        # uncaught exception.
        _report_sentinel_failure(path, exc)
        return False

    # Durability, not just a non-raising write: fsync the sentinel and its
    # segments/ directory entry before claiming success (see this function's
    # docstring, fourth post-review correction). Fail-closed on either sync
    # failure -- the write and close above succeeded, but a crash before the
    # entry is durable can still lose it while the converged fragment it
    # backs survives, which is the exact loss this sentinel exists to
    # prevent.
    sync_error = _fsync_fd(seg_dir_fd, str(path.parent))
    if sync_error is not None:
        _report_sentinel_failure(path, sync_error)
        return False

    # Durable is not the same as FINDABLE. Both fsyncs above landed on the
    # pinned directory by construction; this asks the separate question of
    # whether that directory is still what `segments/` resolves to, because
    # that is the path the dispatch gate reads.
    displaced = _pinned_dir_still_canonical(segments_dir, seg_dir_fd)
    if displaced is not None:
        _report_sentinel_failure(path, displaced, _SENTINEL_REMEDY_DISPLACED)
        return False

    return True


def segpack_path(seg, segments_dir=SEGMENTS_DIR):
    return segments_dir / f"segpack_{seg}.json"


def sha1_bytes_of_file(path):
    """sha1 of a file's raw on-disk bytes -- used for this script's OWN
    ledger-fragment output file (write_fragment_atomically()'s
    fragment_sha1), never for a draft. Plain files have no dispatch_token
    to exclude, so raw-byte hashing is exactly right here; see
    draft_content_sha1() below for the (different) draft-hashing scheme.
    """
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def expected_draft_token(run_token, seg):
    """Constructs the exact draft-form dispatch_token expected for THIS
    segment under the given bare run_token: '<run_token>:<seg>' -- draft
    dispatch_token's own documented format. Reconstructing the FULL
    expected token (not just extracting/comparing a RUN_ID prefix) also
    catches a same-run-but-wrong-segment token (e.g. a corrupted/misplaced
    draft carrying some OTHER segment's token under the same run), which a
    bare RUN_ID-component comparison alone would miss. Must match, byte for
    byte, ledger_merge.py's own copy of this function.
    """
    return f"{run_token}:{seg}"


def review_token_matches(review_token, draft_token):
    """review.json's own dispatch_token = '<draft_token>:r<roundLabel>' --
    a ':r<roundLabel>' SUFFIX the draft's own token does not carry (see
    review.schema.json's/draft.schema.json's field descriptions). Matched
    by PREFIX here, not exact string equality, since the round label
    varies per review round and this precondition only cares that the
    review is from the SAME run+segment as `draft_token`, never which
    round it happened to converge at. Must match, byte for byte,
    ledger_merge.py's own copy of this function.
    """
    return isinstance(review_token, str) and review_token.startswith(f"{draft_token}:r")


_MAX_ROUND_LABEL = 32


def _review_round_label(review_obj, expected_token):
    """The `<roundLabel>` out of review.json's `'<draft_token>:r<roundLabel>'`,
    or None when it cannot be read off with certainty.

    Split by LENGTH against the token review_token_matches() already accepted,
    never by searching for ':r' -- a segment id or run id containing that pair
    would otherwise cut the string in the wrong place and record a round label
    that was never dispatched. `expected_token` is None on the pre-1.2.0 path,
    where there is no anchor at all and the honest answer is to record nothing.

    Truncated at _MAX_ROUND_LABEL. The label reaches this only through a token
    the checks above accepted, so this is a bound on the marker's SIZE rather
    than a validation: the census reads at most one small body per segment and
    nothing downstream should have to defend against an unbounded one."""
    if expected_token is None:
        return None
    token = review_obj.get("dispatch_token") if isinstance(review_obj, dict) else None
    if not isinstance(token, str):
        return None
    prefix = f"{expected_token}:r"
    if not token.startswith(prefix):
        return None
    label = token[len(prefix):]
    return label[:_MAX_ROUND_LABEL] or None


def draft_content_sha1(path):
    """sha1 of a draft's CONTENT, with the 'dispatch_token' metadata field
    deliberately EXCLUDED -- must match, byte for byte, draft_sha1.py's own
    draft_content_sha1() (a byte-identical duplicate, per this project's
    "no shared lib between self-contained scripts" convention). See that
    script's own module docstring for the full rationale: dispatch_token is
    a run-scoped freshness token, checked independently at every consume/
    commit point, and must never perturb the "has the translated CONTENT
    changed since review" question this hash answers.

    Raises OSError (unreadable file), json.JSONDecodeError (not valid
    JSON), or ValueError (valid JSON but not an object) on failure --
    callers handle all three via emit_failure().
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


def load_schema(name, schemas_dir=SCHEMAS_DIR):
    path = schemas_dir / name
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

    The caller may set only: status, rounds, reason, note, cache_key,
    run_token (1.2.0 -- a bare RUN_ID string, hand-written here since it is
    a transient precondition input, never a ledger-record-base.schema.json
    field and never persisted into the written fragment; see
    enrich_converged_fields()'s own docstring).
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
            "run_token": {"type": "string"},
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


def enrich_converged_fields(seg, fragment, run_token=None, segments_dir=SEGMENTS_DIR):
    """Populate n_blocks/n_footnotes/n_verses (from the segpack) and
    reviewed_draft_sha1 (via the review-artifact binding check) -- fields
    the calling agent's payload is never allowed to supply directly.

    1.2.0 addition: when `run_token` is given (a bare RUN_ID, from the
    payload's own `run_token` field), ALSO asserts -- before recording
    convergence -- that the on-disk draft's own dispatch_token equals
    expected_draft_token(run_token, seg) = '<run_token>:<seg>' EXACTLY, and
    that review.json's own dispatch_token equals that same value plus a
    ':r<roundLabel>' SUFFIX (review_token_matches(), a prefix match --
    review's token format carries a round label the draft's own form does
    not). Reconstructing the full expected draft token (not just comparing
    a bare RUN_ID) also catches a same-run-but-wrong-segment token.
    Refuses convergence (structured failure) for a stale/straggler draft or
    review from a different run, even one that otherwise looks
    content-valid. Omit for the pre-1.2.0 behavior (no token precondition)
    -- backward compatible.

    Calls emit_failure() (which exits the process) on any problem, so this
    function either returns normally having mutated `fragment` in place,
    or the process has already exited.
    """
    spath = segpack_path(seg, segments_dir)
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

    rpath = review_path(seg, segments_dir)
    review_obj, err = read_json_file(rpath, f"Review artifact for segment '{seg}'")
    if err is not None:
        emit_failure(f"Cannot record convergence for segment '{seg}': {err}")

    reviewer_draft_sha1 = review_obj.get("draft_sha1") if isinstance(review_obj, dict) else None
    if not isinstance(reviewer_draft_sha1, str) or not reviewer_draft_sha1:
        emit_failure(
            f"Cannot record convergence for segment '{seg}': review artifact "
            f"at {rpath} has no draft_sha1."
        )

    expected_token = expected_draft_token(run_token, seg) if run_token is not None else None

    if expected_token is not None:
        review_token = review_obj.get("dispatch_token") if isinstance(review_obj, dict) else None
        if not review_token_matches(review_token, expected_token):
            emit_failure(
                f"Cannot record convergence for segment '{seg}': review "
                f"artifact's dispatch_token {review_token!r} does not match "
                f"the expected prefix '{expected_token}:r' (run_token="
                f"{run_token!r}) -- stale/straggler review, refusing to "
                f"record convergence."
            )

    dpath = draft_path(seg, segments_dir)
    if not dpath.is_file():
        emit_failure(
            f"Cannot record convergence for segment '{seg}': draft not found "
            f"at {dpath}."
        )

    if expected_token is not None:
        draft_obj, err = read_json_file(dpath, f"Draft for segment '{seg}'")
        if err is not None:
            emit_failure(f"Cannot record convergence for segment '{seg}': {err}")
        draft_token = draft_obj.get("dispatch_token") if isinstance(draft_obj, dict) else None
        if draft_token != expected_token:
            emit_failure(
                f"Cannot record convergence for segment '{seg}': draft's "
                f"dispatch_token {draft_token!r} does not equal the expected "
                f"{expected_token!r} (run_token={run_token!r}) -- "
                f"stale/straggler draft, refusing to record convergence."
            )

    try:
        current_draft_sha1 = draft_content_sha1(dpath)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        emit_failure(
            f"Cannot record convergence for segment '{seg}': could not "
            f"compute draft content sha1 at {dpath}: {exc}"
        )

    if current_draft_sha1 != reviewer_draft_sha1:
        # Exact literal per references/ledger-and-resumability.md -- the
        # calling recordLedgerPrompt() flow surfaces this verbatim.
        emit_failure("draft changed since review; cannot record convergence")

    fragment["reviewed_draft_sha1"] = current_draft_sha1

    # #409 Step 1. This is the single site in the plugin where convergence is
    # fixed, so it is the only correct place to raise the durable sentinel.
    # Deliberately AFTER every precondition above: a segment that failed the
    # token, draft-presence or draft-changed-since-review checks has not
    # converged and must not be marked as having done so.
    #
    # Post-review correction: the sentinel write's own success is now a hard
    # precondition for recording convergence at all -- checked HERE, before
    # this function returns, and therefore before write_fragment_atomically()
    # in main() ever runs. See mark_ever_converged()'s own docstring for why
    # treating a sentinel failure as non-fatal was the dangerous direction to
    # fail open in: a fragment written as 'converged' without its sentinel is
    # invisible to the one check that refuses to re-select and retranslate an
    # already-converged segment.
    # #443. The evidence this convergence actually had, recorded INSIDE the
    # marker. Every field is one this function has already VERIFIED above --
    # never something the calling agent's payload supplied and never a
    # re-derivation: `reviewed_draft_sha1` is the sha1 whose equality with the
    # reviewer's own was just enforced, and the round label is read off the
    # very dispatch_token review_token_matches() just accepted. A pre-1.2.0
    # call (no run_token) records neither token nor round rather than
    # guessing at them; sentinel_body() drops the None-valued keys.
    if not mark_ever_converged(seg, segments_dir, {
        "run_token": run_token,
        "round": _review_round_label(review_obj, expected_token),
        "reviewed_draft_sha1": current_draft_sha1,
    }):
        emit_failure(
            f"Cannot record convergence for segment '{seg}': failed to "
            f"create the ever-converged sentinel at "
            f"{ever_converged_path(seg, segments_dir)} (see stderr for the "
            f"underlying OS error). Refusing to write a 'converged' ledger "
            f"fragment without its protecting sentinel -- doing so would "
            f"leave the segment looking done while remaining eligible for "
            f"silent re-selection and retranslation. The draft/review "
            f"artifacts are untouched; retry once the underlying filesystem "
            f"problem (permissions/quota/I/O) is fixed."
        )


def write_fragment_atomically(seg, fragment, ledger_fragment_dir=LEDGER_FRAGMENT_DIR):
    try:
        ledger_fragment_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        emit_failure(f"Could not create ledger fragment directory {ledger_fragment_dir}: {exc}")

    final_path = ledger_fragment_dir / f"{seg}.json"
    tmp_path = ledger_fragment_dir / f"{seg}.json.tmp.{os.getpid()}"

    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(fragment, f, indent=2, ensure_ascii=False, sort_keys=True)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, final_path)
        try:
            dir_fd = os.open(ledger_fragment_dir, os.O_RDONLY)
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
            "(required), plus optionally rounds, reason, note, cache_key, "
            "run_token."
        ),
    )
    parser.add_argument(
        "--durable-root",
        default=None,
        metavar="PATH",
        help=(
            "LT-409: use PATH as the durable root instead of this script's "
            "own self-anchored location. Optional; omit for today's "
            "self-anchored behavior."
        ),
    )
    args = parser.parse_args()

    seg = args.seg
    seg_error = validate_seg(seg)
    if seg_error is not None:
        emit_failure(seg_error)

    dirs = resolve_dirs(args.durable_root)

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

    base_schema = load_schema("ledger-record-base.schema.json", dirs["schemas_dir"])
    fragment_schema = load_schema("ledger-fragment.schema.json", dirs["schemas_dir"])

    payload_schema = build_payload_schema(base_schema, fragment_schema)
    try:
        jsonschema.validate(payload, payload_schema)
    except jsonschema.exceptions.ValidationError as exc:
        emit_failure(f"Malformed payload: {exc.message}")
    except jsonschema.exceptions.SchemaError as exc:
        emit_failure(f"Internal error: derived payload schema is invalid: {exc.message}")

    # Build the fragment entirely fresh -- the prior on-disk fragment (if
    # any) is never read for its field values, only implicitly superseded
    # by os.replace()'s atomic rename below. `run_token` is deliberately
    # NOT copied in here -- it is a transient precondition input for
    # enrich_converged_fields() below, never a persisted fragment field.
    fragment = {"timestamp": now_iso8601(), "status": payload["status"]}
    for key in ("reason", "note", "rounds", "cache_key"):
        if key in payload:
            fragment[key] = payload[key]

    if fragment["status"] == "converged":
        enrich_converged_fields(seg, fragment, payload.get("run_token"), dirs["segments_dir"])

    validate_final_fragment(fragment, base_schema, fragment_schema)

    final_path = write_fragment_atomically(seg, fragment, dirs["ledger_fragment_dir"])
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
