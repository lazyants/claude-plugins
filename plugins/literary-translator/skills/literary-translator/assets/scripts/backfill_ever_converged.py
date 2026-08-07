#!/usr/bin/env python3
"""backfill_ever_converged.py -- #409 Step 2: backfill the durable
'ever converged' sentinel for projects that converged segments BEFORE the
sentinel existed.

## Why this script exists

#409 Step 1 (ledger_update.py, select_segments.py) added a DURABLE sentinel,
``{durable_root}/segments/.ever_converged.{seg}`` -- written exclusively by
``ledger_update.py``'s ``mark_ever_converged()`` at the single site where
convergence is recorded, and read by ``select_segments.py``'s
``ever_converged_path()`` to refuse silently re-translating a segment that
has already converged (see both scripts' own docstrings for the full
rationale). A project that converged segments on an OLDER version of this
plugin has no sentinels at all, so the Step 1 gate cannot protect any of its
already-finished work -- the very first re-dispatch after upgrading would
sail through ungated. This script closes that gap for an EXISTING project:
it determines, from the project's own ledger, which segments have converged
at least once, and raises the missing sentinels for them.

## How "ever converged" is determined

This script never re-implements ledger merging -- when it needs a fresh
merge it shells out to ``ledger_merge.py`` BARE (no
``--expected-from-manifest``/``--expected-segs`` flag -- the same
"materialize, don't gate" invocation ``select_segments.py`` itself makes as
its own Step 1), then reads the materialized ``runs/ledger.json``'s
``segments`` mapping.

### The "dry run" write guarantee -- this governs WHICH of the above actually
### runs, and is load-bearing, not an implementation detail

``ledger_merge.py`` ATOMICALLY WRITES ``runs/ledger.json`` even when called
bare with no gating flag (tmp-write then ``os.replace`` -- see its own
module docstring). A version of this script that always shelled out to it
was therefore never actually dry: invoking it with no flags still mutated
the live project directory -- exactly the kind of concurrent-write collision
this plugin's own conventions exist to prevent, since a real project may
have other sessions actively working in it. "Dry run" here means ZERO
filesystem writes of any kind, not merely "no sentinel file", so how the
ledger is obtained depends on the mode:

  - Under ``--apply``: always re-materializes fresh via ``ledger_merge.py``
    immediately before writing any sentinel. The merge is a legitimate part
    of doing the work here, and this guarantees the segments acted on are
    current (never a stale view).
  - Otherwise (dry run): first tries to read the EXISTING
    ``runs/ledger.json`` directly, with NO subprocess call and NO write --
    the common case, since any project that has ever run
    ``select_segments.py``/``final_audit.py`` already has one on disk (both
    materialize it as their own first step). A missing OR unparseable
    existing file is treated identically: "nothing usable yet". This read
    may be STALE relative to ``runs/ledger.d/*.json`` if a fragment was
    written since the ledger was last materialized -- an accepted trade-off
    for the write-nothing guarantee; ``--apply`` is never subject to this
    staleness, since it always re-merges.
  - Only when there is no usable existing ``runs/ledger.json`` at all does a
    dry run even consider invoking ``ledger_merge.py`` -- and only when
    ``--allow-merge`` explicitly authorizes that one write (never a sentinel
    write). Without it, the run refuses with a clear next step rather than
    silently mutating the project.

The output's own ``ledger_source`` field (``"existing"`` or
``"freshly_merged"``) always names which path a given run actually took.

A segment counts as "ever converged" exactly when its MATERIALIZED status is
``converged`` or ``stale`` -- the identical ``WAS_CONVERGED_STATUSES``
predicate ``select_segments.py`` already uses to decide whether a fragment
needs its full converged-segment reclassification. This is correct because
``ledger_merge.py`` only ever computes ``stale`` for a fragment whose OWN
on-disk ``status`` is ``converged`` (see its ``_compute_stale_segments``) --
so both materialized values mean, without exception, "the fragment currently
on disk for this segment was written with status ``converged``".

### Known limitation (inherent to the ledger's own storage design, not a bug
### in this script)

``ledger_update.py`` writes are a FULL REPLACE, never a read-modify-write
merge (see its own module docstring) -- the prior fragment's field values,
including a PAST ``status: converged``, are never read into a new write. So
a segment that converged once, was later re-dispatched (which writes
``in_progress`` BEFORE translating), and has not reconverged since currently
shows a non-``converged``/``stale`` status in the ledger with no trace that
it ever converged. This script CANNOT recover that segment's history from
ledger data alone, because the data itself no longer contains it -- exactly
the gap ``mark_ever_converged()``'s own sentinel exists to close going
forward. This is expected: the durable sentinel is the fix; this script only
backfills what the ledger can still prove.

## Sentinel-write contract

Creates ``{durable_root}/segments/.ever_converged.{seg}`` for each missing
segment with the same OBSERVABLE contract as
``ledger_update.py:mark_ever_converged()``: same filename convention, same
content (``b"converged\\n"``), same mode (``0o644``), and the same create-only
idempotence -- an existing sentinel is never deleted, replaced, or
overwritten, and finding one is a no-op rather than an error.

The MECHANISM differs, deliberately. The sibling opens the public name with
``O_CREAT | O_EXCL | O_WRONLY`` and writes into it. This copy stages the
bytes in a uniquely-named temp file, fsyncs them, and only then publishes
the name with ``os.link()`` -- which raises ``FileExistsError`` exactly as
``O_EXCL`` would, so create-only idempotence survives the change. See
``mark_ever_converged()`` for why: publishing the name before the bytes are
durable forces a choice between leaving residue at the public name and
unlinking a name this call no longer provably owns, and the second option
can destroy another writer's protection. Duplicated here rather than
imported for the bundle-hash reason spelled out in
``classify_ever_converged_sentinel()`` below -- NOT for the "no shared lib
between self-contained scripts" convention, which is already false in this
codebase. Pinned against the real writer by dedicated drift tests in
``tests/backfill_ever_converged.test.py`` (byte identity of the sentinel's
content/mode, and agreement on refusing a non-regular entry), not a second
source of truth.

## CLI flags

    --durable-root PATH / --plugin-root PATH
        The same two INDEPENDENT, orthogonal overrides ``select_segments.py``
        documents at length in its own module docstring: ``--durable-root``
        is the DATA root (segments/, runs/); ``--plugin-root`` is where the
        sibling ``ledger_merge.py`` (and, transitively, its own
        ``cache_key.py`` sibling) is resolved from -- deliberately NEVER
        derived from ``--durable-root``, for the identical tampered-copy
        reason ``select_segments.py`` states. Omitting both reproduces
        today's self-anchored behavior byte-for-byte.

    --apply
        Without it (the default), this script is DRY RUN: it makes ZERO
        filesystem writes of any kind -- not a re-materialized
        ``runs/ledger.json``, not one sentinel file -- and only reports what
        it would do. With it, ``runs/ledger.json`` is freshly re-materialized
        and missing sentinels are actually created.

    --allow-merge
        A dry run with no pre-existing ``runs/ledger.json`` has nothing it
        can read without writing, so it refuses by default (see "The 'dry
        run' write guarantee" above) rather than silently invoking
        ``ledger_merge.py``. Pass this flag to explicitly authorize exactly
        that one write for this run (never a sentinel write). Ignored under
        ``--apply``, which always re-materializes regardless.

    --allow-empty
        Without this flag, a ZERO-segment "ever converged" result is a FATAL
        error: a genuinely fresh/never-converged project and a broken ledger
        read both print an almost-identical report differing only in one
        number, and an operator must not be able to mistake a broken read
        for "nothing to backfill". Pass this flag to confirm the zero is
        expected.

## Output

Exactly ONE JSON object on stdout (the house convention -- see
``select_segments.py``/``ledger_merge.py``), with a human-readable summary on
stderr. Success:
``{"success": true, "durable_root": ..., "applied": bool, "ledger_path": ...,
"ledger_source": "existing" | "freshly_merged",
"ever_converged_segs": [...], "already_sentineled": [...],
"missing_sentinels": [...], "ambiguous_sentinels": [...],
"not_evaluated": [...], "created": [...], "failed_to_create": [...],
"counts": {...}}``. Fatal failure: ``{"success": false, "error": ...}``.

``success`` is NOT only about fatal conditions. It is false, and the exit
code 1, whenever ``failed_to_create`` is non-empty -- a run that set out to
create sentinels and created none of them is not a success, however cleanly
it ran. Such a payload carries the full report, not an ``error`` field.

``ambiguous_sentinels`` and ``not_evaluated`` do NOT make ``success`` false:
neither is a failure to do the work, and both are reported precisely so that
``success: true`` cannot be read as "every segment that ever converged is
protected now". ``not_evaluated`` names what this script never considered
(see the Known limitation above); a segment there may or may not have
converged, and nothing here can tell. Read both fields before concluding a
project is protected.
"""

import argparse
import errno
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import NoReturn

# ---------------------------------------------------------------------------
# Self-anchoring -- identical convention to select_segments.py/ledger_merge.py.
# ---------------------------------------------------------------------------

SCRIPTS_DIR = Path(__file__).resolve().parent
DURABLE_ROOT = SCRIPTS_DIR.parent
LEDGER_MERGE_SCRIPT = SCRIPTS_DIR / "ledger_merge.py"


def resolve_dirs(durable_root_str, plugin_root_str=None):
    """LT-409-style split, identical in spirit to select_segments.py's own
    ``resolve_dirs()``: `durable_root_str` governs DATA (segments/, runs/),
    rebuilt from that root when given, self-anchored otherwise.
    `plugin_root_str` is a SEPARATE, independent input governing where the
    sibling `ledger_merge.py` this script shells out to is resolved from --
    deliberately never derived from `durable_root_str` (see this script's own
    module docstring, and select_segments.py's `resolve_dirs()` docstring,
    for the full tampered-copy rationale). Both None -> today's exact
    self-anchored values for both concerns.
    """
    if durable_root_str is None:
        durable_root = DURABLE_ROOT
    else:
        durable_root = Path(durable_root_str).resolve()

    if plugin_root_str is None:
        ledger_merge_script = LEDGER_MERGE_SCRIPT
    else:
        ledger_merge_script = (
            Path(plugin_root_str).resolve() / "assets" / "scripts" / "ledger_merge.py"
        )

    return {
        "durable_root": durable_root,
        "segments_dir": durable_root / "segments",
        "ledger_merge_script": ledger_merge_script,
    }


def _root_forward_args(dirs: dict, durable_root_str, plugin_root_str) -> list:
    """The exact --durable-root/--plugin-root pair to forward to the
    ledger_merge.py subprocess. Identical logic to select_segments.py's own
    `_root_forward_args()` -- see that function's docstring for why an
    explicit --durable-root must be forwarded whenever --plugin-root is
    given, even when THIS script itself was never passed --durable-root.

    Doubled-path fix (this file's own copy of the shape select_segments.py
    already fixed): both flags are always forwarded as their RESOLVED
    value, never the raw CLI string. `run_ledger_merge()` runs the
    subprocess with `cwd` set to the resolved `dirs["durable_root"]`, and
    ledger_merge.py's own `resolve_dirs()` does
    `Path(durable_root_str).resolve()` -- which resolves a RELATIVE
    fragment against ITS cwd. Forwarding the raw string when it happened to
    be relative resolved it a SECOND time against the already-resolved
    value, silently landing the sibling one level too deep --
    `run_ledger_merge()`'s own success/failure check only sees whether the
    subprocess printed valid JSON with `"success": true`, never which tree
    it actually read. Every existing caller already passes an absolute
    path for both flags (`Path(absolute).resolve()` is a no-op), so this
    was unreachable until an operator passed a relative override;
    self-anchored behavior (both flags omitted) is untouched -- the
    condition for forwarding each flag at all is unchanged, only the VALUE
    forwarded when it is.
    """
    args = []
    if durable_root_str is not None or plugin_root_str is not None:
        args += ["--durable-root", str(dirs["durable_root"])]
    if plugin_root_str is not None:
        args += ["--plugin-root", str(Path(plugin_root_str).resolve())]
    return args


# ever_converged_path() -- the DURABLE 'this segment has converged at least
# once' sentinel. Stated identically in all FOUR scripts that touch it --
# ledger_update.py (the writer), select_segments.py (the dispatch gate),
# final_audit.py (the completeness carve-out) and here (reader and writer
# both) -- restated rather than imported for the bundle-hash reason spelled
# out in classify_ever_converged_sentinel() below, NOT for the "no shared lib
# between self-contained scripts" convention, which is already false here.
# tests/backfill_ever_converged.test.py pins this against ledger_update.py's
# own copy by name, a drift test, not a second source of truth.


def ever_converged_path(seg: str, segments_dir: Path) -> Path:
    return segments_dir / f".ever_converged.{seg}"


# ---------------------------------------------------------------------------
# The shared sentinel-presence predicate. This block is an EXACT duplicate of
# the copy in the other three sentinel scripts (search `SENTINEL_ABSENT` in
# ledger_update.py, select_segments.py and final_audit.py) -- see
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


def classify_ever_converged_sentinel(path) -> "tuple[str, str]":
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
    PLUGIN_BUNDLE_MEMBERS entry, and cache_key.py:100-107 records that that
    tuple is a literal byte-hash allowlist to which a TRANSITIVE IMPORT IS
    INVISIBLE -- which is why canon_senses.py had to be registered
    explicitly once two members imported it. A shared module would put this
    predicate's bytes outside the hash meant to cover them, so WEAKENING
    this guard would no longer move plugin_bundle_hash, and every durable
    root scaffolded beforehand would go on trusting it: the exact
    false-green cache_key.py:114-118 names. Consolidation stays possible --
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
      2. Since Python 3.13 `exists()` swallows EVERY OSError and returns
         False, so an EACCES/ESTALE/EIO on the lookup is reported as "this
         segment never converged". Verified on 3.14.6: with an unreadable
         parent directory `exists()` returns False while `lstat()` raises
         EACCES. (On 3.8-3.12 the same call re-raised for EACCES but still
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
    link would only ask the question about some unrelated file. Note that
    lstat still resolves symlinks in the PARENT components, so a project
    whose whole `segments/` directory is a symlink is unaffected: only the
    final `.ever_converged.<seg>` component is left unresolved.

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
        st = path.lstat()
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


def _cleanup_staging(tmp_fd, tmp_path) -> None:
    """Best-effort removal of the staging file (and its fd) used by
    mark_ever_converged().

    Deliberately silent. Unlike the public sentinel name, the staging file is
    owned unambiguously by one call -- tempfile.mkstemp() picked a name no
    other process holds -- so failing to remove it cannot destroy anyone's
    protection and cannot be mistaken for a sentinel by any reader, which
    matches on the exact `.ever_converged.{seg}` name. Leaking one is untidy;
    reporting it would bury the real error that caused the cleanup."""
    if tmp_fd is not None:
        try:
            os.close(tmp_fd)
        except OSError:
            pass
    if tmp_path is not None:
        try:
            os.unlink(str(tmp_path))
        except OSError:
            pass


def mark_ever_converged(seg: str, segments_dir: Path) -> str:
    """Same OBSERVABLE contract as ledger_update.py's own
    `mark_ever_converged()` -- same filename, same content
    (`b"converged\\n"`), same mode (`0o644`), the same create-only
    idempotence (an existing sentinel is never deleted, replaced, or
    overwritten; finding one is a no-op) -- and, post-review correction, the
    SAME property that EVERY OS call this function makes gets a clean,
    non-raising outcome, never an uncaught OSError escaping past this
    function's own contract.

    The MECHANISM is no longer the sibling's single `O_CREAT | O_EXCL |
    O_WRONLY` open. This copy stages, fsyncs, then publishes with
    `os.link()`, and syncs the parent directory afterwards -- so the OS
    calls to keep non-raising are mkstemp, fchmod, write, fsync, close,
    link, and the directory open/fsync, not three. `os.link()` is what
    keeps create-only idempotence intact across the change: it raises
    `FileExistsError` on an existing target exactly as `O_EXCL` does, where
    `os.rename()` would silently clobber. The reasoning for staging at all
    is in the block comment below.

    NO LONGER byte-identical to the sibling, and this docstring says so
    explicitly rather than leaving the old claim to go stale silently a
    second time: it was true when written, and became false the moment
    ledger_update.py's own copy was fixed first, with nothing checking the
    two had drifted apart -- the review bot caught it by injecting ENOSPC
    into THIS copy and watching it escape uncaught, the identical shape
    already fixed in the sibling an hour earlier. The two functions'
    CONTRACTS differ on purpose, not by accident:
      - ledger_update.py's copy is non-fatal-by-design: it returns a plain
        `bool` and prints its own explanation to stderr, because its
        caller (enrich_converged_fields(), deep inside one ledger write)
        has no report-building machinery to hand a string outcome to.
      - THIS copy returns a STRING outcome instead -- "created" (this call
        raised the sentinel), "already_present" (one already existed, a
        no-op, not an error), or an `"error: ..."` string (open, write, or
        close failed) -- because ITS caller (run(), below) builds a
        per-segment report from exactly these three shapes and prints
        nothing of its own on this path: a bare `False` would give that
        report nothing to show the operator, and an uncaught OSError would
        crash the whole backfill run over one segment's sentinel.

    Any failure before the link runs `_cleanup_staging()`, which closes the
    fd if still open and removes the temp file, so a failed attempt leaks
    neither a descriptor nor a file. A secondary error during that cleanup
    is swallowed rather than reported, since the original error is the one
    worth surfacing. A close() that fails on its own (write succeeded; some
    filesystems, notably NFS, defer reporting a write error until close())
    gets the identical "error: ..." treatment.

    ONE failure leaves the public name in place: a directory-fsync error
    after a successful link. That is deliberate and documented at the call
    site -- past the link the name may already be another reader's
    protection, so removing it is exactly the destruction staging exists to
    prevent. It is still reported, so the segment lands in
    `failed_to_create` and the run fails: the sentinel exists, but its
    durability is unproven, and only a re-run can settle that.

    No shared message-building helper, unlike the sibling's own fix:
    ledger_update.py's stderr text is a multi-sentence explanation
    genuinely at risk of drifting if hand-duplicated, which is exactly why
    it factored one out. This copy's generic outcome is the single f-string
    `f"error: {exc}"` at two call sites below -- nothing for a helper to
    centralize that repeating it twice does not already give for free.
    """
    path = ever_converged_path(seg, segments_dir)

    # STAGE THEN LINK. The public name is never published until the bytes
    # behind it are already durable, so no failure path here ever has to
    # remove a file at the public name -- which is what makes this safe.
    #
    # The previous shape created the public name with O_CREAT|O_EXCL, then
    # wrote, fsynced, and on any failure unlinked it again. That unlink was a
    # BLOCKER: O_EXCL proves only that this call installed the entry at open
    # time, it does not reserve the pathname. Between the failed write and the
    # unlink, another actor can remove our incomplete inode and install a
    # REAL, fully-synced sentinel -- and the unlink then deletes THAT.
    # Reproduced, twice, including via replacement of `segments/` itself. A
    # cleanup that can destroy protection somebody else established is worse
    # than the residue it was cleaning up.
    #
    # Staging removes the dilemma rather than narrowing it. A failure before
    # the link leaves only a uniquely-named temp file, which no reader can
    # mistake for a sentinel (`classify_ever_converged_sentinel()` looks at
    # the exact `.ever_converged.{seg}` name), and unlinking THAT is
    # unambiguously safe because nothing else can hold it. There is no window
    # in which a half-made file sits at the name readers consult.
    #
    # os.link() -- not os.rename() -- is what preserves the create-only
    # contract this script shares with ledger_update.py's writer: link fails
    # with EEXIST if the target exists, so an existing sentinel is still never
    # replaced or overwritten. rename() would silently clobber it.
    tmp_fd = None
    tmp_path = None
    try:
        tmp_fd, tmp_name = tempfile.mkstemp(
            prefix=".ever_converged_staging.", dir=str(segments_dir)
        )
        tmp_path = Path(tmp_name)
        # mkstemp is 0o600 by design; the sentinel's mode is part of the
        # contract pinned against ledger_update.py's writer by a drift test.
        os.fchmod(tmp_fd, 0o644)
        os.write(tmp_fd, b"converged\n")
        os.fsync(tmp_fd)
        os.close(tmp_fd)
        tmp_fd = None
    except OSError as exc:
        _cleanup_staging(tmp_fd, tmp_path)
        return f"error: {exc}"

    try:
        os.link(str(tmp_path), str(path))
    except FileExistsError:
        # Someone else got there first. EEXIST is not proof a real sentinel is
        # there -- O_CREAT|O_EXCL and link() both raise it for ANY existing
        # entry, including a directory and a DANGLING SYMLINK, both verified
        # on this project's Python 3.14.6 -- and "already_present" is this
        # function's way of saying "protected, nothing to do", which for those
        # two is false. Kept in step with ledger_update.py's own copy.
        _cleanup_staging(None, tmp_path)
        state, detail = classify_ever_converged_sentinel(path)
        if state == SENTINEL_PRESENT:
            return "already_present"
        if state == SENTINEL_ABSENT:
            return (
                "error: the entry reported by link() as already existing had "
                "vanished by the time it was examined; retry"
            )
        return f"error: {detail}; refusing to treat this as an existing sentinel"
    except OSError as exc:
        # Every other link failure -- EACCES, EROFS, EIO, EXDEV, a parent
        # removed after the scan -- is reported for THIS segment and lets the
        # loop continue, rather than escaping to the top-level handler and
        # abandoning every segment after it.
        _cleanup_staging(None, tmp_path)
        return f"error: {exc}"

    # The link is what published the name, and a directory entry is not
    # durable until its directory is synced. Without this a crash can lose the
    # entry while the ledger fragment it backs -- fsynced in a DIFFERENT
    # directory -- survives, which is exactly the asymmetry the dispatch gate
    # reads as ABSENT and clears for retranslation.
    #
    # A failure here does NOT unlink: the name is already published and, from
    # this point on, may legitimately be another reader's protection. It is
    # reported so the segment lands in `failed_to_create` and the run fails,
    # which is the honest answer -- the sentinel exists but its durability is
    # unproven.
    dir_fd = None
    try:
        dir_fd = os.open(str(segments_dir), os.O_RDONLY)
        os.fsync(dir_fd)
    except OSError as exc:
        return (
            f"error: the sentinel for {seg!r} was created, but its directory "
            f"entry could not be synced ({exc}), so it may not survive a "
            f"crash. It is NOT removed -- it is a valid marker now and "
            f"another reader may already be relying on it. Re-run to sync it"
        )
    finally:
        if dir_fd is not None:
            try:
                os.close(dir_fd)
            except OSError:
                pass  # best-effort; the fsync above is what mattered
        _cleanup_staging(None, tmp_path)

    return "created"


# ---------------------------------------------------------------------------
# Segment id validation -- duplicated from select_segments.py's/
# ledger_update.py's own `validate_seg()`/`_SEG_ID_RE`, per this project's
# "no shared lib between self-contained scripts" convention. A materialized
# ledger.json segment id is not attacker-controlled in the ordinary case
# (it originates from a ledger_update.py fragment write, which already
# validates it), but this script builds filesystem paths directly from it,
# so it is checked again here rather than trusted transitively.
# ---------------------------------------------------------------------------

_SEG_ID_RE = re.compile(r"(?:FRONTBACK:)?[A-Za-z0-9_]+")


def validate_seg(seg):
    if not isinstance(seg, str) or not seg:
        return "segment id must be a non-empty string."
    if not _SEG_ID_RE.fullmatch(seg):
        return (
            "segment id must match (FRONTBACK:)?[A-Za-z0-9_]+ (no path "
            f"separators, '..', or shell metacharacters); got {seg!r}."
        )
    return None


# The identical predicate select_segments.py's own WAS_CONVERGED_STATUSES
# uses -- see this script's module docstring for why both materialized
# values mean "this fragment's own on-disk status is converged".
WAS_CONVERGED_STATUSES = frozenset({"converged", "stale"})


class FatalError(Exception):
    """Raised for any failure that should surface as a top-level FAILURE
    JSON payload on stdout (exit 1), never a bare traceback."""


def _json_line(payload) -> str:
    """`json.dumps(..., ensure_ascii=False)`, falling back to ASCII escaping
    when the result cannot be encoded for stdout.

    A ledger is JSON, and JSON can carry a LONE SURROGATE -- a lone-surrogate escape as a
    segment id or a status. `ensure_ascii=False` keeps it verbatim, and
    printing it then raises UnicodeEncodeError from the print itself, OUTSIDE
    every handler here: the process dies with a traceback and no JSON at all,
    exactly where this script's contract promises a failure payload. Escaping
    is strictly better than crashing -- the operator still gets the report,
    with the offending value shown as an escape rather than as bytes the
    terminal cannot represent.

    stdout keeps this deterministic-JSON treatment rather than a stream-level
    error handler, so the bytes emitted are always valid JSON chosen here
    rather than whatever an encoder substituted. STDERR is different -- it
    carries the human summary, not a parseable contract -- and is reconfigured
    with `errors="backslashreplace"` in main() instead. It has to be: the
    summary prints segment ids and statuses BEFORE this payload is emitted, so
    an unencodable one killed the process before stdout was ever reached, and
    guarding stdout alone fixed nothing."""
    line = json.dumps(payload, ensure_ascii=False)
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    try:
        line.encode(encoding)
    except UnicodeEncodeError:
        return json.dumps(payload, ensure_ascii=True)
    return line


def fatal(message: str, **extra) -> NoReturn:
    raise FatalError(_json_line({"success": False, "error": message, **extra}))


def read_json(path: Path, what: str):
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        fatal(f"{what} not found at {path}")
    except OSError as exc:
        fatal(f"could not read {what} at {path}: {exc}")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        fatal(f"{what} at {path} is not valid JSON: {exc}")


# ---------------------------------------------------------------------------
# Step 1: materialize the ledger via ledger_merge.py (bare -- no
# --expected-* flag; this only ever needs the current merged view, never a
# completeness check).
# ---------------------------------------------------------------------------


def run_ledger_merge(dirs: dict, durable_root_str=None, plugin_root_str=None) -> dict:
    ledger_merge_script = dirs["ledger_merge_script"]
    if not ledger_merge_script.is_file():
        fatal(f"ledger_merge.py not found at {ledger_merge_script}")
    cmd = [sys.executable, str(ledger_merge_script)] + _root_forward_args(
        dirs, durable_root_str, plugin_root_str
    )
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
            cwd=str(dirs["durable_root"]),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        fatal(f"could not run ledger_merge.py: {exc}")

    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        fatal(
            "ledger_merge.py did not print valid JSON on stdout "
            f"(exit {proc.returncode}): stdout={proc.stdout!r} stderr={proc.stderr!r}"
        )

    if not isinstance(payload, dict) or not payload.get("success"):
        error = payload.get("error") if isinstance(payload, dict) else None
        fatal(
            "ledger_merge.py failed to materialize runs/ledger.json"
            + (f": {error}" if error else f" (stdout={proc.stdout!r})")
        )

    return payload


def load_ledger_segments(merge_result: dict, durable_root: Path) -> "tuple[dict, str]":
    ledger_path = Path(merge_result.get("ledger_path") or (durable_root / "runs" / "ledger.json"))
    doc = read_json(ledger_path, "materialized ledger.json")
    segments = doc.get("segments")
    if not isinstance(segments, dict):
        fatal(f"materialized ledger.json at {ledger_path} has no 'segments' object")
    return segments, str(ledger_path)


def read_existing_ledger(durable_root: Path):
    """Attempts to read the EXISTING materialized `runs/ledger.json`
    directly -- NO subprocess, NO write -- the read-only fast path a dry run
    prefers. Returns `(segments_dict, ledger_path_str)` on success, or
    `None` if there is nothing usable yet: missing, unreadable, not valid
    JSON, not an object, or missing/malformed `segments`. Every one of those
    cases is treated identically ("no usable materialized ledger"), leaving
    the decision of what to do about it to the caller -- this function never
    raises/fatals, unlike `load_ledger_segments()` above (which is only ever
    called right after a merge THIS script itself just ran, where a bad
    result is a genuine internal error worth failing loudly on).
    """
    ledger_path = durable_root / "runs" / "ledger.json"
    if not ledger_path.is_file():
        return None
    try:
        doc = json.loads(ledger_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(doc, dict):
        return None
    segments = doc.get("segments")
    if not isinstance(segments, dict):
        return None
    return segments, str(ledger_path)


def resolve_ledger_segments(args, dirs: dict):
    """Obtains the ledger's segments mapping while honoring the dry-run
    write-nothing guarantee -- see this module's own docstring, "The 'dry
    run' write guarantee", for the full rationale. Returns
    `(ledger_segments, ledger_path, ledger_source)`, where `ledger_source`
    is `"existing"` or `"freshly_merged"`.
    """
    if args.apply:
        # The merge is a legitimate part of doing the work -- always fresh,
        # immediately before any sentinel write, never the stale-tolerant
        # existing-file path below.
        merge_result = run_ledger_merge(dirs, args.durable_root, args.plugin_root)
        segments, ledger_path = load_ledger_segments(merge_result, dirs["durable_root"])
        return segments, ledger_path, "freshly_merged"

    existing = read_existing_ledger(dirs["durable_root"])
    if existing is not None:
        segments, ledger_path = existing
        return segments, ledger_path, "existing"

    if args.allow_merge:
        merge_result = run_ledger_merge(dirs, args.durable_root, args.plugin_root)
        segments, ledger_path = load_ledger_segments(merge_result, dirs["durable_root"])
        return segments, ledger_path, "freshly_merged"

    fatal(
        "no usable materialized ledger.json found at "
        f"{dirs['durable_root'] / 'runs' / 'ledger.json'} -- this is a DRY "
        "RUN, and re-materializing one would WRITE to this project (the one "
        "guarantee a dry run makes -- see this script's own --help). Either "
        "run ledger_merge.py yourself first (select_segments.py/"
        "final_audit.py already do this as their own first step, so most "
        "projects already have a usable runs/ledger.json), or pass "
        "--allow-merge to explicitly authorize this script to do exactly "
        "that one write (never a sentinel write), or pass --apply directly "
        "if you already intend to write sentinels this run."
    )


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def run(args, dirs: dict) -> dict:
    ledger_segments, ledger_path, ledger_source = resolve_ledger_segments(args, dirs)

    # WHAT THIS CANNOT SEE, and why it is reported rather than silently
    # omitted. Eligibility is the segment's CURRENT status, because that is
    # the only convergence evidence the ledger keeps: ledger_update.py builds
    # each fragment entirely fresh, so a segment that converged and later went
    # back to `in_progress` (a full replace) has had that convergence ERASED.
    # For a project that converged before sentinels existed, such a segment
    # has no sentinel, no ledger evidence, and nothing here can distinguish it
    # from one that never converged at all -- so this script cannot protect
    # it, and the operator has to inventory it by hand.
    #
    # This does NOT fail the run. `in_progress` segments are ordinary on a
    # live project, so failing on their presence would fail nearly every real
    # invocation and the flag would simply be bypassed -- which protects less
    # than reporting it does. What it must not do is let the caller read
    # `success: true` as "every segment that ever converged is protected now",
    # which is a claim this script is not in a position to make.
    ever_converged_segs = []
    not_evaluated = []
    for seg, record in ledger_segments.items():
        # Validation runs for EVERY segment, before the status branch decides
        # anything. It used to run only on the converged branch, which was
        # sound while that branch was the only one producing output -- adding
        # `not_evaluated` created a second path to stdout and would otherwise
        # have carried an unvalidated id straight out through it. Two ways
        # that bites: `../unsafe` reaching a caller that treats the reported
        # id as a path, and a lone surrogate reaching `json.dumps(...,
        # ensure_ascii=False)`, which raises UnicodeEncodeError from OUTSIDE
        # main()'s handler and produces no JSON at all -- a crash where the
        # contract promises a failure payload.
        problem = validate_seg(seg)
        if problem is not None:
            fatal(f"materialized ledger.json: unsafe segment id: {problem}", seg=seg)
        status = record.get("status") if isinstance(record, dict) else None
        # `status in <frozenset>` raises TypeError on an unhashable value, and
        # a ledger is JSON: a status of `[]` or `{}` is malformed but entirely
        # parseable. Testing the type first keeps a malformed record inside
        # the report it belongs in instead of aborting the whole run.
        if isinstance(status, str) and status in WAS_CONVERGED_STATUSES:
            ever_converged_segs.append(seg)
        else:
            not_evaluated.append({"seg": seg, "status": status})
    ever_converged_segs.sort()
    not_evaluated.sort(key=lambda entry: entry["seg"])

    if not ever_converged_segs and not args.allow_empty:
        fatal(
            "the merged ledger yields ZERO ever-converged segments -- "
            "refusing to report this silently. A genuinely fresh project "
            "and a broken ledger read look almost identical here (both emit "
            "an empty list), differing only in one number no one is "
            "watching for -- pass --allow-empty to confirm this project "
            "genuinely has no converged segments yet.",
            ledger_path=ledger_path,
        )

    segments_dir = dirs["segments_dir"]
    # Three buckets, not two. `.exists()` here used to fold two very different
    # states into "already sentineled": a real regular sentinel, and a
    # DIRECTORY sitting at the sentinel path (exists() is True for one). It
    # also folded a dangling symlink and an EACCES lookup into "missing", so
    # this script would report a sentinel CREATED for a segment whose path it
    # had not actually written -- the writer's O_CREAT|O_EXCL gets EEXIST from
    # a dangling link and, pre-fix, called that success.
    #
    # AMBIGUOUS is reported, never repaired and never counted as protected.
    # This script is the REPAIR tool, so the branch that cannot mislead is the
    # one that says "I could not verify this and I did not touch it": claiming
    # protection that was never verified is what leaves a segment looking safe
    # while the dispatch gate can still retranslate it. Blind repair is wrong
    # for the same reason -- mark_ever_converged() would have to delete or
    # overwrite whatever is there, and it deliberately never does either.
    already_sentineled = []
    missing_sentinels = []
    ambiguous_sentinels = []
    for seg in ever_converged_segs:
        state, detail = classify_ever_converged_sentinel(ever_converged_path(seg, segments_dir))
        if state == SENTINEL_PRESENT:
            already_sentineled.append(seg)
        elif state == SENTINEL_ABSENT:
            missing_sentinels.append(seg)
        else:
            ambiguous_sentinels.append({"seg": seg, "detail": detail})
    already_sentineled.sort()
    missing_sentinels.sort()
    ambiguous_sentinels.sort(key=lambda entry: entry["seg"])

    for entry in ambiguous_sentinels:
        print(
            f"backfill_ever_converged.py: warning: the sentinel path for "
            f"{entry['seg']!r} is neither absent nor a regular file "
            f"({entry['detail']}). NOT counted as protected and NOT modified "
            f"-- this script only ever creates a sentinel that is missing, it "
            f"never replaces one that is there. Repair the path by hand: if "
            f"the segment really did converge, replace the entry with a "
            f"regular file containing the single line 'converged'; only if it "
            f"did NOT converge is removing the entry correct.",
            file=sys.stderr,
        )

    created = []
    failed_to_create = []
    if args.apply:
        for seg in missing_sentinels:
            outcome = mark_ever_converged(seg, segments_dir)
            if outcome == "created":
                created.append(seg)
            elif outcome == "already_present":
                # Raced with something else that created it since our
                # already_sentineled snapshot above -- not an error, just not
                # newly created by THIS invocation.
                pass
            else:
                failed_to_create.append({"seg": seg, "error": outcome})
                print(
                    f"backfill_ever_converged.py: warning: could not create "
                    f"sentinel for {seg!r}: {outcome}. Convergence for this "
                    f"segment is still recorded in the ledger; only the "
                    f"#409 durable-sentinel protection is not yet raised for "
                    f"it.",
                    file=sys.stderr,
                )
        created.sort()

    # `success` is NOT unconditional, and this is the whole point of the
    # script. Its caller is an operator running it before a W5 dispatch to
    # raise the #409 protection, and the exit code is what they read to decide
    # the protection is up. A run where every single create failed used to
    # print `success: true` and exit 0, reporting the failures only in a
    # stderr warning and an array nobody is required to look at -- so the
    # operator dispatches believing converged work is protected when none of
    # it is. Unprotected-but-reported-protected is the one outcome that
    # destroys finished work, so it fails loudly instead.
    #
    # `ambiguous_sentinels` does NOT fail the run: those are reported,
    # untouched, and were never claimed as protected. `not_evaluated` does
    # not either -- see its own note above.
    ok = not failed_to_create
    return {
        "success": ok,
        "durable_root": str(dirs["durable_root"]),
        "applied": bool(args.apply),
        "ledger_path": ledger_path,
        "ledger_source": ledger_source,
        "ever_converged_segs": ever_converged_segs,
        "already_sentineled": already_sentineled,
        "missing_sentinels": missing_sentinels,
        # Neither protected nor repairable by this script -- reported so a
        # caller can assert the exact set instead of grepping stderr, and so
        # that "no ambiguous entries found" is distinguishable from "nothing
        # looked", which an absent key would not be.
        "ambiguous_sentinels": ambiguous_sentinels,
        # Segments this script did not consider at all, with the status that
        # excluded each. Any of them MAY have converged before sentinels
        # existed; the ledger no longer records it either way. Reported so
        # "protected" is never read as "complete".
        "not_evaluated": not_evaluated,
        "created": created,
        "failed_to_create": failed_to_create,
        "counts": {
            "ever_converged": len(ever_converged_segs),
            "already_sentineled": len(already_sentineled),
            "missing_sentinels": len(missing_sentinels),
            "ambiguous_sentinels": len(ambiguous_sentinels),
            "not_evaluated": len(not_evaluated),
            "created": len(created),
            "failed_to_create": len(failed_to_create),
        },
    }


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "#409 Step 2: backfill the durable .ever_converged.{seg} sentinel "
            "for a project's already-converged segments, from the merged "
            "ledger. DRY RUN by default -- pass --apply to actually write."
        )
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help=(
            "Actually re-materialize runs/ledger.json and create the missing "
            "sentinel files. Without this flag the script makes ZERO "
            "filesystem writes and only reports what it would do."
        ),
    )
    parser.add_argument(
        "--allow-merge",
        action="store_true",
        help=(
            "Without a pre-existing runs/ledger.json, a dry run refuses by "
            "default rather than silently re-materializing one (that write "
            "would break the 'dry run makes zero writes' guarantee). Pass "
            "this flag to explicitly authorize exactly that one write "
            "(never a sentinel write) for this run. Ignored under --apply, "
            "which always re-materializes regardless."
        ),
    )
    parser.add_argument(
        "--allow-empty",
        action="store_true",
        help=(
            "Do not fatally error if the merged ledger yields zero "
            "ever-converged segments."
        ),
    )
    parser.add_argument(
        "--durable-root",
        default=None,
        metavar="PATH",
        help=(
            "Use PATH as the DATA root instead of this script's own "
            "self-anchored location -- replaces where segments/ (and the "
            "ledger_merge.py subprocess's own runs/schemas data) are found, "
            "forwarded to it as its own --durable-root. Optional; omit for "
            "today's self-anchored behavior. Independent of --plugin-root "
            "below -- this flag never affects where the SIBLING SCRIPT "
            "itself is found."
        ),
    )
    parser.add_argument(
        "--plugin-root",
        default=None,
        metavar="PATH",
        help=(
            "Use PATH (the plugin's own install root, i.e. "
            "{{PLUGIN_ROOT}}) to resolve the sibling ledger_merge.py script "
            "this script shells out to, as {PATH}/assets/scripts/"
            "ledger_merge.py -- deliberately NEVER derived from "
            "--durable-root (see this script's own module docstring for "
            "why). Optional; omit for today's self-anchored sibling lookup."
        ),
    )
    return parser


def main(argv=None) -> int:
    # The human summary printed below carries segment ids and statuses straight
    # from the ledger, and a ledger is JSON: any of them can be a lone
    # surrogate that stderr's encoder cannot represent. Unguarded, that raises
    # UnicodeEncodeError from the print itself -- BEFORE the JSON payload on
    # stdout is reached -- so the process dies emitting no report at all.
    # Guarding stdout alone fixed nothing, because stderr goes first. A
    # mangled character in a diagnostic line costs nothing next to that.
    # getattr, not a direct call: sys.stderr is only guaranteed to be a
    # TextIO, and a test harness or a caller that replaced it with a plain
    # file-like object has no `reconfigure`. Losing the setting is harmless
    # -- it only widens what stderr can render -- so a miss is silent.
    reconfigure = getattr(sys.stderr, "reconfigure", None)
    if reconfigure is not None:
        try:
            reconfigure(errors="backslashreplace")
        except (OSError, ValueError):
            pass  # a non-standard stderr; the stdout payload still stands
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    try:
        dirs = resolve_dirs(args.durable_root, args.plugin_root)
        result = run(args, dirs)
    except FatalError as exc:
        print(str(exc), file=sys.stdout)
        return 1
    except Exception as exc:  # pragma: no cover -- defensive catch-all
        print(
            _json_line({"success": False, "error": f"unexpected error: {exc}"}),
            file=sys.stdout,
        )
        return 1

    mode = "APPLY" if result["applied"] else "DRY RUN (pass --apply to write)"
    print("=" * 70, file=sys.stderr)
    print("BACKFILL EVER-CONVERGED SENTINELS", file=sys.stderr)
    print("=" * 70, file=sys.stderr)
    print(f"durable_root: {result['durable_root']}", file=sys.stderr)
    print(f"mode: {mode}", file=sys.stderr)
    print(
        f"ledger source: {result['ledger_source']} ({result['ledger_path']})",
        file=sys.stderr,
    )
    print(
        f"\never-converged segments (from merged ledger): "
        f"{result['counts']['ever_converged']}",
        file=sys.stderr,
    )
    for seg in result["ever_converged_segs"]:
        if seg in result["already_sentineled"]:
            status = "already sentineled"
        elif seg in result["created"]:
            status = "CREATED"
        elif any(f["seg"] == seg for f in result["failed_to_create"]):
            status = "FAILED to create"
        elif any(a["seg"] == seg for a in result["ambiguous_sentinels"]):
            # Listed as its own status rather than falling through to "missing
            # sentinel": the entry is NOT missing, and calling it missing is
            # exactly the misreading that let a dangling symlink pass as a
            # successful backfill.
            status = "AMBIGUOUS -- not protected, not modified"
        else:
            status = "missing sentinel (dry run)"
        print(f"  - {seg}: {status}", file=sys.stderr)
    print(
        f"\nalready sentineled: {result['counts']['already_sentineled']}\n"
        f"missing sentinels:  {result['counts']['missing_sentinels']}\n"
        f"ambiguous:          {result['counts']['ambiguous_sentinels']}\n"
        f"not evaluated:      {result['counts']['not_evaluated']}",
        file=sys.stderr,
    )
    if result["counts"]["not_evaluated"]:
        print(
            f"\nbackfill_ever_converged.py: note: "
            f"{result['counts']['not_evaluated']} segment(s) were NOT "
            f"evaluated, because their current ledger status is not one this "
            f"script can read as converged. A segment that converged and was "
            f"later replaced no longer records that anywhere -- so this run "
            f"protects the segments it names and makes NO claim about the "
            f"rest. If this project converged segments before the sentinel "
            f"existed, inventory the not_evaluated set by hand before the "
            f"next W5 dispatch.",
            file=sys.stderr,
        )
    if result["applied"]:
        print(
            f"created this run:   {result['counts']['created']}\n"
            f"failed to create:   {result['counts']['failed_to_create']}",
            file=sys.stderr,
        )
    print("\n" + "=" * 70, file=sys.stderr)

    print(_json_line(result))
    # Exit status tracks `success`, so a caller that checks only `$?` learns
    # the same thing the JSON says. SKILL.md's #409 upgrade note names this
    # field first, ahead of `missing_sentinels`, because a non-empty
    # `missing_sentinels` says what the run INTENDED and `success` says
    # whether it got there.
    return 0 if result["success"] else 1


if __name__ == "__main__":
    sys.exit(main())
