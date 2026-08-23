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

Run this script only when no W5 dispatch is in flight against the same
durable root -- this script takes no lock, and neither does the read it
protects. ``select_segments.py`` takes its ``.ever_converged`` census ONCE,
at selection time, and nothing rechecks it when the translate work it
authorized is actually dispatched. A sentinel this script raises in between
does not revoke an authorization already granted: the dispatch proceeds
anyway and retranslates the very work the sentinel exists to guard. That is
why the caller sequences this script strictly before the first W5 dispatch,
never alongside one.

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
have other sessions actively working in it. "Dry run" here means the script
issues NO MUTATING OPERATION and changes NO PROJECT CONTENT -- not a
re-materialized ledger, not one sentinel file -- rather than the stronger
"zero filesystem writes of any kind" this once claimed. That stronger
wording was literally false and the test that appeared to prove it could not
have: reading ``ledger.json`` advances its access time (verified on APFS),
and the guard records only mtime and size. Access-time updates and the
explicitly authorized ``--allow-merge`` write are the two exceptions; both
are named. How the ledger is obtained depends on the mode:

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
body FORMAT, same mode, and the same create-only idempotence -- an existing
sentinel is never deleted, replaced, or overwritten, and finding one is a
no-op rather than an error.

The body is NOT the same bytes, and since #443 that is the point. Both writers
emit one line of JSON from the shared ``sentinel_body()``; this script writes
``"by": "backfill_ever_converged"`` and the evidence it actually has (the
ledger row's status, its source, and its ``reviewed_draft_sha1``), while
``ledger_update.py`` writes ``"by": "ledger_update"`` plus the reviewed draft
sha1 of the convergence it just recorded, and its run token and round label
when the recording call supplied a run token and a round label can be read off
the review artifact. Evidence is all-or-nothing: a body that would exceed
``SENTINEL_BODY_MAX_BYTES`` -- reachable through an unconstrained ``run_token``
-- is published with the identity fields alone rather than truncated. Before #443
both published the identical ten bytes ``converged\n``, so a marker this script
retrofitted and a marker earned at a real convergence were indistinguishable,
and the only thing separating them on the project that motivated the issue was
sentinel mtime at microsecond resolution.

None of this is authority. Every gate still classifies the entry by TYPE
through ``classify_ever_converged_sentinel()``; the body's only reader is this
script's own ``sentinel_attribution`` report, which decides nothing.

The mode is ``0o644 & ~umask``, not a flat ``0o644``, and the mask is applied
by hand for a specific reason: the sibling sets its mode through
``os.open(..., 0o644)``, which the KERNEL masks, while this script sets it on
an already-created staging file via ``fchmod``, which does not. Under any
non-default umask a flat ``0o644`` would silently diverge from the writer this
script is pinned against -- ``0o600`` vs ``0o644`` at umask ``077``.

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
``tests/backfill_ever_converged.test.py``: identical sentinel MODE, an
identical shared ``sentinel_body()``/``write_all()`` source, deliberately
DIFFERENT body bytes (the ``by`` field -- see #443 above), and agreement on
refusing a non-regular entry. Drift tests, not a second source of truth.

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
        Without it (the default), this script is DRY RUN: it issues no
        mutating operation and changes no project content -- not a
        re-materialized ``runs/ledger.json``, not one sentinel file; see the
        module docstring for the two named exceptions -- and only reports what
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
"sentinel_attribution": {"<seg>": "ledger_update" | "backfill_ever_converged"
                                 | "unattributed" | "unreadable"},
"missing_sentinels": [...], "ambiguous_sentinels": [...],
"not_evaluated": [...], "created": [...], "failed_to_create": [...],
"directory_sync_error": null, "segments_dir_replaced": null,
"counts": {...}}``. Fatal failure:
``{"success": false, "error": ...}``, sometimes with one or two extra
context keys (``seg`` for an unsafe segment id, ``ledger_path`` for an
empty result) -- so consume ``error`` and treat anything else as optional.

``success`` is NOT only about fatal conditions. It is false, and the exit
code 1, whenever ``failed_to_create`` is non-empty OR ``ambiguous_sentinels``
is non-empty OR ``directory_sync_error`` is non-null, OR
``segments_dir_replaced`` is.
They are separate keys because they are separate failures and an operator
acts on them differently. ``directory_sync_error``: the directory could not
be fsynced, so entries are where readers look but may not survive a crash --
re-running settles it. ``segments_dir_replaced``: EITHER ``segments/`` now
names a different directory than the one this run worked in -- so the whole
report describes a directory readers will not consult, including segments it
called already protected, and re-running does not settle that -- OR the
identity could not be DETERMINED because ``fstat``/``stat`` failed, which
re-running may well settle. The two need different responses, so read the
string to tell them apart; use the KEY to decide whether the run is
trustworthy at all, which it is not in either case. Such a payload carries
the full report, not an ``error`` field.

``ambiguous_sentinels`` names paths whose protection could not be established
and which this script will not repair. It DOES make ``success`` false, and did
not until security review showed what the exemption bought: with ``segments/``
readable but not searchable, every lstat under it fails, EVERY segment lands
in this bucket, ``missing_sentinels`` comes back empty -- and the run reported
``success: true`` and exit 0, which is what an operator reads before
dispatching. An entry here is a segment whose protection is UNPROVEN, which
for a dispatch decision has the same standing as ``failed_to_create``.

Note what that does and does not claim. The bucket is empty on a project whose
sentinel paths can all be read, which is the ordinary case -- but a transient
``lstat`` failure (``ESTALE`` after a network-filesystem failover, ``EIO``)
puts a perfectly good sentinel here too. Failing is still right: the entry may
be fine, and this script cannot show that it is. Re-running settles that class
on its own; the entries that persist need a human.

``not_evaluated`` does NOT make ``success`` false, and that asymmetry is the
reason these are separate buckets: it names what this script never considered
(see the Known limitation above), and it is non-empty on any project with
segments outside ``converged``/``stale`` -- every ordinary mixed or live one.
Failing on it would redden those runs while proving no protection defect. A
segment there may or may not have converged, and nothing here can tell -- so
``success: true`` still cannot be read as "every segment that ever converged
is protected now". Read the field before concluding a project is protected.
"""

import argparse
import errno
import json
import os
import re
import secrets
import stat
import subprocess
import sys
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


STAGING_PREFIX = ".ever_converged_staging."
_STAGING_NAME_ATTEMPTS = 32


# #443's marker body. The FUNCTION is spelled byte-identically to
# ledger_update.py's copy (the BODIES the two produce differ, deliberately) --
# `tests/backfill_ever_converged.test.py` pins the two with inspect.getsource,
# the same technique that keeps the sentinel PREDICATE's five copies honest --
# because a reader must be able to parse either writer's output with one rule.
# Duplicated rather than imported for the reason
# classify_ever_converged_sentinel()'s docstring gives at length: ledger_update.py
# is a PLUGIN_BUNDLE_MEMBERS entry and that tuple is a literal byte-hash
# allowlist to which a transitive import is invisible.
SENTINEL_MARKER_NAME = "ever_converged"
SENTINEL_BODY_VERSION = 1

# Byte-identical to ledger_update.py's, and the reason the reader below can
# refuse an oversized body outright: no writer publishes one. See
# sentinel_body()'s own docstring for why it drops evidence instead of
# truncating it.
SENTINEL_BODY_MAX_BYTES = 4096

# What this script writes into `by`. The ONE field that must differ from
# ledger_update.py's: #443 exists because a marker this script retrofitted from
# a ledger row and a marker earned at a real convergence were the same ten
# bytes on disk, so the census had to fall back on separating them by sentinel
# MTIME at microsecond resolution.
SENTINEL_WRITER_NAME = "backfill_ever_converged"


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


# The body reader. The ONLY one in the plugin, and it decides nothing: see
# read_sentinel_attribution()'s own docstring.
SENTINEL_ATTRIBUTION_UNATTRIBUTED = "unattributed"
SENTINEL_ATTRIBUTION_UNREADABLE = "unreadable"

# The only two names a marker may be attributed to. The body is UNTRUSTED --
# anyone who can write the file can write any `by` they like -- so the reader
# answers from a closed set rather than echoing whatever string it found:
# reporting an arbitrary `by` back would put a value outside this script's own
# documented output contract into its JSON, and would dress up a foreign file
# as provenance. Adding a third writer means adding it here, in the same
# commit that adds the writer.
SENTINEL_KNOWN_WRITERS = ("ledger_update", "backfill_ever_converged")

# ONE MORE BYTE than any writer will publish (SENTINEL_BODY_MAX_BYTES), which
# is what makes an over-long body DETECTABLE rather than silently truncated.
# Reading exactly the maximum could not tell a body that ends at the limit from
# one that runs past it, so a hostile file whose first 4096 bytes parse as a
# valid attributed marker -- with anything at all after them -- would have been
# reported as a known writer's. The extra byte is the whole difference between
# a cap and an overflow check.
SENTINEL_BODY_READ_CAP = SENTINEL_BODY_MAX_BYTES + 1


def read_sentinel_attribution(path: Path, *, dir_fd=None, expected_seg=None) -> str:
    """WHICH WRITER published the marker at `path` -- `"ledger_update"`,
    `"backfill_ever_converged"`, `"unattributed"` or `"unreadable"`.

    REPORT ONLY. This function is the plugin's first and only reader of the
    marker's body, and nothing it returns reaches a protection decision: the
    census calls it AFTER classify_ever_converged_sentinel() has already
    classified the entry, it never moves a segment between buckets, never
    changes a count, and never fails a run. That separation is the whole
    design of #443 -- the marker gained evidence WITHOUT the predicate gaining
    a way to reject one. Wire this into a gate and every provenance-free
    marker on every live project (42 of them on the one that motivated the
    issue, all ten bytes of `converged\n`) becomes unprotected in the same
    instant.

    WHAT IT IS NOT, second: this is the body's SELF-ATTRIBUTION, not an
    authenticated publisher identity. Nothing signs the marker, so anyone who
    can write the file can write `"by": "ledger_update"` into it. The value of
    the field is that a marker written by the plugin's own writers now SAYS so
    and carries the evidence to check by hand, not that a claim of authorship
    can be trusted on its own.

    Which is why a body is attributed only when it matches the shape
    sentinel_body() actually emits -- no longer than SENTINEL_BODY_MAX_BYTES,
    the right `marker`, this `v` as a real JSON integer (`true` and `1.0` both
    compare equal to 1 in Python and neither is anything a writer emits), a
    non-empty `seg` (equal to `expected_seg` when the caller knows which
    segment it is asking about, so a marker copied from another segment is
    not reported as this one's provenance), and a `by` from
    SENTINEL_KNOWN_WRITERS. Everything else is `unattributed`.

    So every failure answers, none raises. `unattributed` covers a legacy
    body, an empty or torn one, invalid UTF-8, a body that is not JSON, JSON
    that is not an object, and every field mismatch above -- all of them
    "there is no provenance here I can vouch for", which is exactly what an
    operator needs to be told. `unreadable` is kept separate and means the
    READ failed, because "I could not look" and "I looked and found nothing"
    are different facts for the operator, and folding them is how a
    diagnostic starts lying.

    Read relative to `dir_fd` like every other lookup this script makes, and
    O_NOFOLLOW|O_NONBLOCK on the final component: the caller has already
    established the entry is a regular file, but between that lstat and this
    open it could have become a symlink or a FIFO, and a diagnostic read must
    never block the run or follow a link out of `segments/`."""
    try:
        fd = os.open(
            path.name if dir_fd is not None else str(path),
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0),
            **({"dir_fd": dir_fd} if dir_fd is not None else {}),
        )
        try:
            # A LOOP, not one os.read(). A single read on a regular file
            # normally returns the whole request, but it is not guaranteed to:
            # an interruption or a filesystem implementation may return a short
            # count, and a short read that happened to land on a complete JSON
            # object would be indistinguishable from EOF -- so an oversized body
            # would be judged on its prefix, which is the very thing the +1 cap
            # above exists to prevent. Bounded by SENTINEL_BODY_READ_CAP, so it
            # terminates on EOF or at the cap and never on the file's own size.
            chunks = []
            remaining = SENTINEL_BODY_READ_CAP
            while remaining > 0:
                chunk = os.read(fd, remaining)
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            raw = b"".join(chunks)
        finally:
            os.close(fd)
    except OSError:
        return SENTINEL_ATTRIBUTION_UNREADABLE

    if len(raw) > SENTINEL_BODY_MAX_BYTES:
        # The read asked for one byte past what any writer publishes, so a full
        # buffer means the file runs past the maximum: this is not a marker
        # either writer wrote, and what came back is a PREFIX of something
        # else. Judging it on that prefix is how an oversized foreign file gets
        # a known writer's name.
        return SENTINEL_ATTRIBUTION_UNATTRIBUTED

    try:
        body = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        # RecursionError is NOT a ValueError, and deeply nested JSON is the
        # one malformed shape that raises it out of json.loads() rather than
        # returning a decode error. Measured: it does not reproduce under the
        # 4096-byte cap on this project's own CPython 3.14.7 (the C scanner
        # does not recurse in Python frames there) -- but this plugin's floor
        # is 3.10, where it does, and "the reader never raises" is a contract
        # about every supported interpreter rather than about the one that
        # happens to be installed.
        return SENTINEL_ATTRIBUTION_UNATTRIBUTED
    if not isinstance(body, dict):
        return SENTINEL_ATTRIBUTION_UNATTRIBUTED
    if body.get("marker") != SENTINEL_MARKER_NAME:
        return SENTINEL_ATTRIBUTION_UNATTRIBUTED
    version = body.get("v")
    if type(version) is not int or version != SENTINEL_BODY_VERSION:
        # `type(...) is not int` and not isinstance(): bool is a SUBCLASS of
        # int, so `True == 1` and isinstance(True, int) both hold, and a body
        # carrying `"v": true` would otherwise pass a check meant to pin an
        # exact emitted shape.
        return SENTINEL_ATTRIBUTION_UNATTRIBUTED
    seg = body.get("seg")
    if not isinstance(seg, str) or not seg:
        return SENTINEL_ATTRIBUTION_UNATTRIBUTED
    if expected_seg is not None and seg != expected_seg:
        return SENTINEL_ATTRIBUTION_UNATTRIBUTED
    writer = body.get("by")
    if writer not in SENTINEL_KNOWN_WRITERS:
        return SENTINEL_ATTRIBUTION_UNATTRIBUTED
    return writer


def sentinel_mode() -> int:
    """The mode a published sentinel must carry: `0o644 & ~umask`, which is
    what ledger_update.py's writer produces once the kernel has masked its
    `os.open(..., 0o644)`. A drift test pins the two together, and `fchmod`
    does NOT mask, so this script has to apply the umask by hand.

    Reading the umask requires setting it (there is no getumask), so this
    briefly widens the process-wide umask to 0. Hoisted out of the per-segment
    write for exactly that reason: the window is process-wide, not call-local,
    and this module has in-process callers by design (several tests drive
    `run()` and `mark_ever_converged()` directly, alongside the subprocess
    harness most of the suite uses). The caller opens it at most ONCE per run
    and not at all when there is nothing to create. It cannot be removed
    entirely without a getumask this interpreter does not have."""
    umask = os.umask(0)
    os.umask(umask)
    return 0o644 & ~umask


def _open_staging(dir_fd: int) -> "tuple[int, str]":
    """Create this call's staging file RELATIVE TO `dir_fd` and return
    `(fd, name)`. Raises OSError on failure, like the `os.open` it wraps.

    Replaces `tempfile.mkstemp(dir=str(segments_dir))`, which was the one
    mutating call left in the write path that still resolved `segments/`
    afresh BY PATHNAME. Review reproduced the consequence: with `segments/`
    re-pointed between the descriptor's open and the staging call, the staging
    file was created in the NEW directory while the link and the cleanup both
    operated on the old one -- so the run failed closed, correctly, and leaked
    a file into a directory of the retargeter's choosing that THIS INVOCATION
    could never remove, since `_cleanup_staging()` unlinks relative to the
    descriptor. The identity check is not a defence against it, and an
    earlier draft of this docstring got the reason wrong: the check DOES fire
    on that interleaving (the test asserts exactly that). What it cannot do is
    see the stranded file -- it compares directory IDENTITY and never the
    entries under either directory, so it can say "this path moved" and never
    "and it left this behind". It says nothing at all when the path is
    re-pointed and restored before the final sample.

    With the whole write path descriptor-relative there is no pathname
    resolution left in it at all, so a retarget can no longer place, publish,
    or strand anything.

    `mkstemp`'s other job -- picking a name nothing else holds -- is done here
    by `O_EXCL` over a 64-bit random suffix (`token_hex(8)` is 8 BYTES, 16 hex
    characters), retried on the structurally possible collision. `O_EXCL` is
    what actually establishes exclusivity; the entropy only keeps the retry
    loop from mattering. `0o600` matches what mkstemp created;
    the caller widens it to the sentinel's own mode on this descriptor BEFORE
    writing the bytes, which is safe under a staging name no reader consults --
    the public name is not published until those bytes are durable."""
    for _ in range(_STAGING_NAME_ATTEMPTS):
        name = f"{STAGING_PREFIX}{secrets.token_hex(8)}"
        try:
            fd = os.open(
                name,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o600,
                dir_fd=dir_fd,
            )
        except FileExistsError:
            continue
        return fd, name
    raise OSError(
        errno.EEXIST,
        f"could not find an unused staging name after "
        f"{_STAGING_NAME_ATTEMPTS} attempts",
    )


def _cleanup_staging(tmp_fd, tmp_name, dir_fd) -> None:
    """BEST-EFFORT removal of the staging file (and its fd) used by
    mark_ever_converged(). Errors are swallowed, so a leaked staging file is
    possible on both the failure AND the success path -- the docstrings say
    "best-effort" rather than "removed" for exactly that reason.

    Deliberately silent. Unlike the public sentinel name, the staging file is
    owned unambiguously by one call -- `_open_staging()` picked a name no
    other process holds -- so failing to remove it cannot destroy anyone's
    protection and cannot be mistaken for a sentinel by any reader, which
    matches on the exact `.ever_converged.{seg}` name. Leaking one is untidy;
    reporting it would bury the real error that caused the cleanup.

    NOTHING SWEEPS old staging files, and adding a sweeper would be a mistake.
    A run cannot tell a leaked `.ever_converged_staging.*` from one a
    CONCURRENT invocation is writing into right now, so a sweep is the same
    "cleanup that can destroy work this call does not own" that the stage-then-
    link design exists to remove -- one layer down. A stale staging file costs
    an inode; deleting a live one costs a sentinel.

    Unlinks relative to `dir_fd`, never by pathname, for the same reason the
    link does: the directory this call staged into is identified by an OPEN
    DESCRIPTOR, so a `segments/` that gets retargeted mid-run cannot redirect
    the removal into whatever now answers to that path."""
    if tmp_fd is not None:
        try:
            os.close(tmp_fd)
        except OSError:
            pass
    if tmp_name is not None:
        try:
            os.unlink(tmp_name, dir_fd=dir_fd)
        except OSError:
            pass


def sync_segments_dir(dir_fd: int):
    """fsync the segments directory, ONCE, after every sentinel this run
    creates has been linked and every staging name unlinked.

    This is where directory durability is actually established, and it is a
    whole-run step rather than a per-segment one because a per-segment version
    could not settle it. Review found the hole: a first run whose directory
    fsync failed left the sentinel published and reported the segment in
    `failed_to_create`, and the RETRY then classified that same file as
    SENTINEL_PRESENT during the pre-write scan, put it in
    `already_sentineled`, never called mark_ever_converged() for it at all,
    and so never reached the fsync that had failed. First run red, second run
    green, directory durability established by NEITHER -- the identical
    red-then-laundered-green shape this release exists to remove, one layer up
    from the residue version of it.

    Syncing the directory unconditionally at the end of every --apply run
    closes that: a retry re-syncs regardless of whether it created anything,
    so it genuinely does settle the previous run's unsynced entries. One
    fsync commits every link and every staging unlink in this directory, so
    it is also cheaper than the per-segment version it replaces.

    Takes the descriptor ONLY, and does exactly one job: fsync. Whether the
    path still names this directory is a different question with a different
    remedy, so it is `check_segments_dir_identity()`'s job and lands in its
    own output key.

    Returns None on success, or an "error: ..." string. The caller fails the
    run on a non-None return: sentinels whose directory entry is not durable
    are exactly the ones a crash can lose while the ledger fragment they back
    survives, which produces the asymmetry the dispatch gate reads as ABSENT
    and clears for retranslation."""
    try:
        os.fsync(dir_fd)
    except OSError as exc:
        return (
            f"error: this run's sentinels are linked, but the segments "
            f"directory could not be synced ({exc}), so those entries may "
            f"not survive a crash. They are NOT removed -- they are valid "
            f"markers now and another reader may already be relying on them. "
            f"Re-run once the directory is writable to sync them"
        )
    return None


def check_segments_dir_identity(dir_fd: int, segments_dir: Path):
    """Report whether `segments_dir` still names the directory this run has
    been working in. Returns None, or an "error: ..." string.

    SEPARATE from sync_segments_dir(), and separately reported, because the
    two failures are not the same failure and an operator acts on them
    differently: an unsynced directory is settled by re-running, a replaced
    one is not settled by anything this script can do. Folding both into one
    string also meant a caller had to match on the word "REPLACED" to tell
    them apart, which is not a contract worth asking anyone to depend on.

    Runs in DRY RUN TOO, which is the point of hoisting it here. SKILL.md
    tells the operator that a dry run's `missing_sentinels` decides whether
    backfilling is needed at all, so a census that read a directory the path
    no longer names can talk them out of running `--apply` and straight into
    a W5 dispatch. Review found that gap: the descriptor used to be opened
    only under `--apply`, leaving the dry-run census with no identity check
    of any kind.

    WHAT THIS DOES NOT PROVE, stated because three previous versions of this
    file claimed otherwise and were wrong every time. It samples identity
    ONCE, at the end. That detects a pathname displaced AT THAT INSTANT, and
    says nothing about any instant AFTER it: the dispatch gate resolves
    `segments/` by pathname at its own, later time, and a displacement
    between this sample and that lookup is outside anything a finished
    process can observe. That much is inherent and stays disclosed.

    What this check no longer has to carry, and what the third wrong version
    was: the census used to resolve `{segments_dir}/.ever_converged.<seg>`
    by pathname even though the descriptor was already open. A path swapped
    to B for the length of the census and back to A before this sample
    compared equal here -- while the census had read B. The destructive
    shape was B holding a sentinel A lacks: the segment reported protected,
    A left bare, `success: true`, this key null. Review reproduced exactly
    that. It is closed at the source rather than here -- every lookup this
    run makes, read and write alike, now resolves relative to the descriptor
    opened before the census, so a pathname swapped away and back has
    nothing left in this run to act on. Note what that correction cost: this
    paragraph previously said closing it needed "a locking protocol the
    directory's mutators also honour", which was simply false. The
    descriptor was already held; only the census had failed to use it.

    AND WHAT THE DESCRIPTOR DOES NOT CLOSE, because the disclosed limitation
    is a CLASS and only one member of it moved. Binding every lookup to the
    descriptor settles WHICH DIRECTORY is being read; it settles nothing
    about the ENTRIES inside it between the census and anyone acting on the
    report. A sentinel deleted after the census classified it PRESENT, or a
    sync/restore tool rewriting entries IN PLACE, leaves the directory inode
    untouched -- so the descriptor reads the right directory, this check
    compares equal, and the segment is still reported protected when it is
    not. That is the same silent-retranslation consequence, reached without
    ever touching the pathname, and it is disclosed in SKILL.md's upgrade
    note and tracked as #442 rather than papered over here.

    WHAT NO LONGER FOLLOWS FROM IT, since #442's first narrowing landed. This
    report going stale is still outside anything this process can observe --
    everything above stands. What changed is downstream: the dispatch gate no
    longer treats an absent marker as proof the segment never converged. A
    selected segment whose MATERIALIZED ledger record says converged/stale
    while its marker reads ABSENT is now refused by select_segments.py and
    reported as `lost_sentinels`, because ledger_update.py cannot publish a
    'converged' record without first writing that marker -- so the two can only
    disagree that way if something outside the plugin removed it, or the
    backfill this script performs was never run. So a marker deleted after this
    census called it PRESENT is caught there rather than silently retranslated
    HERE. The gate reads one witness this run cannot: a unit whose status has
    ALSO moved off converged/stale (a convergence commit interrupted after the
    marker was raised, or an interrupted re-dispatch) has neither witness left
    and is still dispatched -- that residual is #442's remaining scope and
    needs the marker provenance tracked as #443."""
    try:
        held = os.fstat(dir_fd)
        current = os.stat(str(segments_dir))
    except OSError as exc:
        return (
            f"error: the identity of {segments_dir} could not be confirmed "
            f"({exc}), so it is unknown whether this run's work is visible "
            f"under the path the dispatch gate reads. Re-run to establish it"
        )
    if (held.st_dev, held.st_ino) != (current.st_dev, current.st_ino):
        return (
            f"error: {segments_dir} names a DIFFERENT directory than the one "
            f"this run has been working in. Everything this run examined and "
            f"linked belongs to the directory that path named when the run "
            f"started, so this run's report -- including any segment it "
            f"called already protected -- describes a directory readers will "
            f"not consult. Nothing was removed. Establish which directory "
            f"the project should be using, then re-run"
        )
    return None


def mark_ever_converged(seg: str, segments_dir: Path, dir_fd: int,
                        mode=None, provenance=None) -> str:
    """Same OBSERVABLE contract as ledger_update.py's own
    `mark_ever_converged()` -- same filename, the same body FORMAT (one line
    of JSON from the shared `sentinel_body()`, not the same BYTES: #443 made
    `by` differ on purpose, and a drift test now pins that difference where it
    used to pin equality), the same mode (`0o644 & ~umask`, matching what the
    sibling's `os.open(..., 0o644)` produces once the kernel has masked it),
    the same create-only idempotence (an existing sentinel is never deleted,
    replaced, or overwritten; finding one is a no-op) -- and, post-review
    correction, the SAME property that EVERY OS call this function makes
    gets a clean, non-raising outcome, never an uncaught OSError escaping
    past this function's own contract.

    `mode` is that mode, computed ONCE per run by the caller so the
    process-wide umask window `sentinel_mode()` needs is opened once rather
    than once per segment; passing None (the standalone/library call) computes
    it here instead, so the contract is unchanged for a direct caller.

    The MECHANISM is no longer the sibling's single `O_CREAT | O_EXCL |
    O_WRONLY` open. This copy stages, fsyncs the STAGED FILE, then publishes
    with `os.link()` -- so the OS calls to keep non-raising are the staging
    open, fchmod, write, fsync, close, link and the staging unlink, not three.
    Every one of them is bound to `dir_fd`, so nothing on this function's
    WRITE path resolves the `segments/` pathname at all -- and neither does
    the one call that is not a write, the EEXIST classification below, which
    now passes the same descriptor. It was the last pathname lookup in this
    function, and it is a READ: it could never place, publish or strand a
    file, only misreport WHOSE entry it had examined -- returning
    "already_present", this function's way of saying "protected, nothing to
    do", on the strength of a regular file in a directory the pathname had
    been re-pointed at. `segments_dir` survives only to derive the sentinel's
    BASENAME, which touches nothing.
    This function does NOT sync the directory; `sync_segments_dir()` does
    that once per run, and its docstring explains why a per-segment version
    could not settle durability at all. `os.link()` is what keeps
    create-only idempotence intact across the change: it raises
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
    fd if still open and TRIES to remove the temp file. "Tries" is the exact
    word: that helper swallows its own errors, so a leaked descriptor or a
    leaked staging file remains possible and this docstring does not promise
    otherwise. Leaking one is untidy and nothing more -- the name is unique
    to this call and no reader matches it -- whereas reporting it would bury
    the error that caused the cleanup. A secondary error during cleanup is
    swallowed for the same reason. A close() that fails on its own (write
    succeeded; some filesystems, notably NFS, defer reporting a write error
    until close()) gets the identical "error: ..." treatment.

    NO failure here leaves anything at the public name. Directory durability
    is deliberately not this function's job: `sync_segments_dir()` establishes
    it once per run, and its docstring holds the account of the per-segment
    version that came before and the green retry that version laundered.

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
    tmp_name = None
    try:
        tmp_fd, tmp_name = _open_staging(dir_fd)
        # Staging is created 0o600, so the published mode has to be set
        # explicitly -- and it must come out EQUAL to what ledger_update.py's
        # writer produces, because a drift test pins the two together. That
        # writer uses `os.open(..., 0o644)`, whose mode the kernel MASKS.
        # fchmod does not mask, so a bare fchmod(0o644) diverges from the
        # sibling under any non-default umask: at umask 077 the ledger writes
        # 0o600 while this would publish 0o644. `sentinel_mode()` applies the
        # umask by hand to keep them identical; the caller passes it in so its
        # process-wide read happens once per run rather than once per segment.
        if mode is None:
            mode = sentinel_mode()
        os.fchmod(tmp_fd, mode)
        body = sentinel_body(seg, SENTINEL_WRITER_NAME, provenance)
        write_all(tmp_fd, body)
        os.fsync(tmp_fd)
        os.close(tmp_fd)
        tmp_fd = None
    except OSError as exc:
        _cleanup_staging(tmp_fd, tmp_name, dir_fd)
        return f"error: {exc}"

    try:
        os.link(tmp_name, path.name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
    except FileExistsError:
        # Someone else got there first. EEXIST is not proof a real sentinel is
        # there -- O_CREAT|O_EXCL and link() both raise it for ANY existing
        # entry, including a directory and a DANGLING SYMLINK, both verified
        # on this project's Python 3.14.6 -- and "already_present" is this
        # function's way of saying "protected, nothing to do", which for those
        # two is false. Kept in step with ledger_update.py's own copy.
        #
        # Classified relative to `dir_fd`, like the census and like the link
        # that just raised EEXIST. The link resolves the destination through
        # the descriptor, so the entry it collided with is in THAT directory;
        # re-reading the same name by pathname could answer about a different
        # directory's entry entirely, which is how a re-pointed `segments/`
        # turned somebody else's file into this segment's "protected".
        _cleanup_staging(None, tmp_name, dir_fd)
        state, detail = classify_ever_converged_sentinel(path, dir_fd=dir_fd)
        if state == SENTINEL_PRESENT:
            return "already_present"
        if state == SENTINEL_ABSENT:
            return (
                "error: the entry reported by link() as already existing had "
                "vanished by the time it was examined; retry"
            )
        return f"error: {detail}; refusing to treat this as an existing sentinel"
    except OSError as exc:
        # Every other link failure -- EACCES, EROFS, EIO, a parent removed
        # after the scan -- is reported for THIS segment and lets the loop
        # continue, rather than escaping to the top-level handler and
        # abandoning every segment after it. A `segments/` retargeted mid-run
        # no longer reaches here at all: staging is now created relative to
        # `dir_fd` too, so source and destination are the same directory
        # INODE by construction and the retarget cannot separate them. It is
        # caught at the end instead, by check_segments_dir_identity().
        _cleanup_staging(None, tmp_name, dir_fd)
        return f"error: {exc}"

    # The staging name is removed HERE, before the directory is synced, and
    # that ordering is deliberate. sync_segments_dir() runs once at the end of
    # the run and commits this unlink together with the link above, so a crash
    # cannot resurrect a staging entry that a later fsync had already been
    # told to forget. Per-segment fsync would have committed the link while
    # the staging name still existed and left the unlink uncommitted --
    # correct-looking, and the wrong order.
    _cleanup_staging(None, tmp_name, dir_fd)
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

    A ledger is JSON, and JSON can carry a LONE SURROGATE escape -- as a
    segment id or as a status. `ensure_ascii=False` keeps it verbatim, and
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
    except UnicodeDecodeError as exc:
        # NOT covered by the OSError below -- UnicodeDecodeError is a
        # ValueError. Uncaught it escaped this function entirely and surfaced
        # as main()'s defensive catch-all ("unexpected error: ..."), which is
        # the payload shape reserved for a bug in this script, not for a
        # malformed input file. Fatal is right here (unlike in
        # read_existing_ledger below): this reader only ever runs on a ledger
        # THIS script just merged.
        fatal(f"{what} at {path} is not valid UTF-8: {exc}")
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
    ledger_path = Path(
        merge_result.get("ledger_path") or (durable_root / "runs" / "ledger.json")
    )
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
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        # UnicodeDecodeError is a ValueError, so neither of the other two
        # covered it and a ledger holding invalid UTF-8 escaped this function
        # -- landing in main()'s catch-all instead of the refusal with the
        # actionable next step, and contradicting this docstring's "never
        # raises/fatals" in the one case an operator cannot distinguish from
        # a corrupt file. The existing corrupt-ledger test uses invalid JSON,
        # which is a different exception.
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

    def merge_fresh():
        """Re-materialize via ledger_merge.py and load the result. Spelled
        once because BOTH the --apply path and the --allow-merge path take it
        with the identical inputs; only the CONDITIONS for reaching it
        differ."""
        merge_result = run_ledger_merge(dirs, args.durable_root, args.plugin_root)
        segments, ledger_path = load_ledger_segments(
            merge_result, dirs["durable_root"]
        )
        return segments, ledger_path, "freshly_merged"

    if args.apply:
        # The merge is a legitimate part of doing the work -- always fresh,
        # immediately before any sentinel write, never the stale-tolerant
        # existing-file path below.
        return merge_fresh()

    existing = read_existing_ledger(dirs["durable_root"])
    if existing is not None:
        segments, ledger_path = existing
        return segments, ledger_path, "existing"

    if args.allow_merge:
        return merge_fresh()

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
    sentinel_evidence = {}
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
            # #443. The justification this script can honestly offer, which is
            # deliberately WEAKER than the one ledger_update.py writes, and the
            # asymmetry is the point: a backfill never observed a convergence,
            # so it records no run token and no round label. What it has is the
            # ledger row that put the segment in scope.
            sha1 = record.get("reviewed_draft_sha1")
            sentinel_evidence[seg] = {
                "ledger_status": status,
                "ledger_source": ledger_source,
                "reviewed_draft_sha1": sha1 if isinstance(sha1, str) and sha1 else None,
            }
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
    # The descriptor is acquired HERE, before the census in
    # _run_with_segments_dir() below, and the ordering is the entire
    # protection -- not an optimisation.
    #
    # Review found the previous version opening it AFTER the census, which
    # left the census itself outside everything the identity check covers.
    # The false success that produced: directory A holds
    # `.ever_converged.segX`, the census records segX in `already_sentineled`,
    # A is then renamed aside and an empty B takes the pathname, the open
    # lands on B, segX is not in `missing_sentinels` so B never receives a
    # marker -- and the final comparison finds fstat(dir_fd) and
    # stat(segments_dir) both naming B, agreeing perfectly. `success: true`,
    # and the dispatch gate reads B and sees nothing. Checking identity only
    # across the part of the run that WRITES is useless when the part that
    # DECIDES what to write is what got fooled.
    #
    # Opened before the census, and USED BY IT: the census classifies each
    # sentinel relative to this descriptor, so the ordering buys more than a
    # comparison that merely spans the census -- the census cannot read a
    # different directory in the first place, and a pathname swapped away and
    # back mid-run has nothing in this run left to act on. What the single
    # end sample still cannot prove is anything about the instants AFTER it;
    # see check_segments_dir_identity() for exactly what survives and why it
    # is disclosed rather than closed. It is opened in DRY RUN too -- the dry
    # run's census is what the operator acts on.
    #
    # O_DIRECTORY, never a bare O_RDONLY. Review reproduced what the bare form
    # costs: a `segments` that is a REGULAR FILE opens fine, fsyncs fine, and
    # compares equal to itself in the identity check -- so every structural
    # check in this script agrees with every other one while every sentinel
    # lookup underneath them returns ENOTDIR. The open is the one place that
    # can tell a directory from a file for free, and the right place to refuse.
    #
    # NOT O_NOFOLLOW, and that omission is deliberate -- the sibling
    # backfill_resume_gate_ack.py DOES pass it, so aligning the two "for
    # consistency" is a live temptation. A symlinked `segments/` is explicitly
    # supported here (see classify_ever_converged_sentinel()'s docstring), and
    # O_NOFOLLOW would refuse every project that has one. The retarget risk a
    # symlink carries is handled by holding this descriptor and checking
    # identity at the end, not by refusing the symlink.
    try:
        dir_fd = os.open(str(segments_dir), os.O_RDONLY | os.O_DIRECTORY)
    except OSError as exc:
        fatal(f"could not open segments directory {segments_dir}: {exc}")

    try:
        return _run_with_segments_dir(
            args, dirs, ledger_path, ledger_source, ever_converged_segs,
            not_evaluated, dir_fd, sentinel_evidence,
        )
    finally:
        # fatal() RAISES; it does not exit. Review verified the descriptor
        # stayed live after a FatalError escaped an in-process run() call, so
        # the old "the kernel reclaims it" note was true only for the CLI and
        # false for every test and library caller.
        try:
            os.close(dir_fd)
        except OSError:
            pass


def _run_with_segments_dir(args, dirs, ledger_path, ledger_source,
                           ever_converged_segs, not_evaluated, dir_fd,
                           sentinel_evidence) -> dict:
    """The half of run() that needs the segments-directory descriptor. Split
    out purely so one `finally` can own closing it."""
    segments_dir = dirs["segments_dir"]

    # THE CENSUS. Three buckets, not two. `.exists()` here used to fold two
    # very different states into "already sentineled": a real regular
    # sentinel, and a DIRECTORY sitting at the sentinel path (exists() is True
    # for one). It also folded a dangling symlink and an EACCES lookup into
    # "missing", so this script would report a sentinel CREATED for a segment
    # whose path it had not actually written -- the writer's O_CREAT|O_EXCL
    # gets EEXIST from a dangling link and, pre-fix, called that success.
    #
    # AMBIGUOUS is reported, never repaired and never counted as protected --
    # and it FAILS THE RUN (see the `ok` computation below). This script is the
    # REPAIR tool, so the branch that cannot mislead is the one that says "I
    # could not verify this and I did not touch it": claiming protection that
    # was never verified is what leaves a segment looking safe while the
    # dispatch gate can still retranslate it. Blind repair is wrong for the
    # same reason -- mark_ever_converged() would have to delete or overwrite
    # whatever is there, and it deliberately never does either.
    #
    # Classified RELATIVE TO `dir_fd`, never by pathname, for the same reason
    # every write is. Holding the descriptor while still resolving
    # `{segments_dir}/.ever_converged.<seg>` afresh made the ordering above
    # buy less than it looked: review reproduced a `segments/` re-pointed to
    # directory B for the length of the census and restored to A before the
    # end, and the run reported B's sentinel as A's protection with
    # `success: true` and `segments_dir_replaced: null` -- the identity
    # sample compared A to A and agreed, because by then it was A again,
    # while A had no sentinel at all. `ever_converged_path()` still builds
    # the Path (the basename is what the lookup needs, and the full path is
    # what an operator-facing detail string should name); only the
    # RESOLUTION moves onto the descriptor. With the writer's EEXIST re-read
    # bound the same way, no lookup this run makes resolves `segments/` by
    # pathname any more, so the swapped-away-and-back interleaving is closed
    # without a locking protocol -- the process already holds the directory
    # open. It closes WHICH DIRECTORY and nothing about the entries in it:
    # see check_segments_dir_identity() for what still survives.
    already_sentineled = []
    missing_sentinels = []
    ambiguous_sentinels = []
    # #443's report, and the reason the writers stamp anything at all. It
    # answers ONE question the census could not answer before: for a marker
    # that was already here, WHO put it there. Read only for markers this run
    # found already present -- a marker this run creates is attributed by the
    # `created` list it is already in, and re-reading it would report this
    # script's own writes back to itself.
    #
    # Read INSIDE the census loop, bound to the same descriptor, and strictly
    # AFTER the classification it annotates. It changes no bucket, no count and
    # no exit status; see read_sentinel_attribution()'s docstring for why
    # keeping it out of every decision is the design rather than a caution.
    sentinel_attribution = {}
    for seg in ever_converged_segs:
        sentinel_path = ever_converged_path(seg, segments_dir)
        state, detail = classify_ever_converged_sentinel(sentinel_path, dir_fd=dir_fd)
        if state == SENTINEL_PRESENT:
            already_sentineled.append(seg)
            sentinel_attribution[seg] = read_sentinel_attribution(
                sentinel_path, dir_fd=dir_fd, expected_seg=seg,
            )
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
            f"regular file containing the single line 'converged' -- that "
            f"protects it, and this report will call it 'unattributed', since "
            f"a hand-written marker carries none of the evidence a real "
            f"convergence records; only if it did NOT converge is removing the "
            f"entry correct.",
            file=sys.stderr,
        )

    created = []
    failed_to_create = []
    directory_sync_error = None
    if args.apply:
        # `dir_fd` was opened above, before the census. Every link, every
        # staging unlink and the final fsync are relative to it, so they all
        # reach the same directory INODE even if the `segments/` pathname is
        # retargeted (symlink re-pointed, directory renamed aside and
        # replaced) while the run is in flight. Resolving the path afresh at
        # each step was a MAJOR: a retarget between publishing a link and
        # syncing could fsync directory B while the sentinel lived in
        # directory A, then report `created` for a name absent from the
        # directory anyone would go on to read.
        #
        synced = False
        # ONCE per run, not once per segment -- and ZERO times when there is
        # nothing to create. sentinel_mode() has to widen the process-wide
        # umask to read it, and that window is visible to any other thread in
        # the process; this module has in-process callers by design, so the
        # window is real. N segments opened it N times for no benefit, since
        # the umask cannot change under our own feet. The `if missing_sentinels`
        # is not an optimization: hoisting it unconditionally was itself caught
        # as a regression, because the idempotent no-op re-run -- the one an
        # operator repeats most often, over a fully protected project -- used
        # to open no window at all and would have started opening one.
        mode = sentinel_mode() if missing_sentinels else None
        try:
            for seg in missing_sentinels:
                outcome = mark_ever_converged(
                    seg, segments_dir, dir_fd, mode,
                    sentinel_evidence.get(seg),
                )
                if outcome == "created":
                    created.append(seg)
                elif outcome == "already_present":
                    # Raced with something else that created it since our
                    # already_sentineled snapshot above -- not an error, just
                    # not newly created by THIS invocation.
                    pass
                else:
                    failed_to_create.append({"seg": seg, "error": outcome})
                    print(
                        f"backfill_ever_converged.py: warning: could not "
                        f"create sentinel for {seg!r}: {outcome}. Convergence "
                        f"for this segment is still recorded in the ledger; "
                        f"only the #409 durable-sentinel protection is not "
                        f"yet raised for it.",
                        file=sys.stderr,
                    )
            # UNCONDITIONAL, even when this run created nothing: that is what
            # makes a retry settle a previous run's unsynced entries instead
            # of skipping straight past them via `already_sentineled`. See
            # sync_segments_dir()'s docstring for the laundering it closes.
            directory_sync_error = sync_segments_dir(dir_fd)
            synced = True
            if directory_sync_error is not None:
                print(
                    f"backfill_ever_converged.py: warning: "
                    f"{directory_sync_error}.",
                    file=sys.stderr,
                )
        finally:
            if not synced:
                # An exception is escaping -- a FatalError from inside the
                # loop, or anything unforeseen. The sync above never ran, so
                # whatever this run already linked would be left unsynced.
                # The run is already failing and this cannot rescue it; it
                # only avoids leaving durable-looking work that is not
                # durable. Best-effort by design: raising here would replace
                # the real error with a less informative one.
                try:
                    os.fsync(dir_fd)
                except OSError:
                    pass
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
    # `ambiguous_sentinels` fails it too, and used not to. The argument for
    # exempting it -- "those are reported, untouched, and were never claimed as
    # protected" -- confuses the PAYLOAD with the SIGNAL. An ambiguous entry is
    # a segment this script has left unprotected and cannot repair, which is
    # the same standing as `failed_to_create`; `success: true` and exit 0 are
    # what the operator actually reads before dispatching, so exempting it
    # published a clean verdict over exactly the state this script exists to
    # rule out. Security review reproduced the extreme of it with nothing more
    # exotic than `chmod 444 segments`: every lstat under it fails EACCES, so
    # EVERY segment lands in AMBIGUOUS, `missing_sentinels` comes back EMPTY,
    # and a dry run whose census established nothing at all was indistinguish-
    # able at the `success`/`missing_sentinels`/exit-code level from a
    # perfectly healthy project. SKILL.md already told operators to treat
    # `ambiguous_sentinels` as one of the things that decides whether the
    # protection is up; this makes the code agree with that instead of
    # contradicting it. The bucket is empty whenever the sentinel paths can be
    # read -- NOT "on every healthy project", which overstates it: a transient
    # ESTALE or EIO puts a genuinely fine sentinel here, and failing is still
    # correct, because the entry may be fine and this cannot show that it is.
    #
    # `not_evaluated` still does NOT fail the run -- see its own note above.
    # That asymmetry is deliberate and is the reason the two are separate
    # buckets: `not_evaluated` holds every segment whose status is not
    # converged/stale, so it is non-empty on any ordinary mixed or live
    # project, and failing on it would redden those runs without proving a
    # single protection defect.
    #
    # `directory_sync_error` and `segments_dir_replaced` each fail the run,
    # for the same reason and independently: entries not proven durable, and
    # entries not VISIBLE under the path readers resolve. Either way,
    # reporting success would tell the operator protection is up when it is
    # not.
    # Checked in DRY RUN TOO: the dry run's `missing_sentinels` is what
    # SKILL.md tells the operator to act on, so a census that read a
    # directory the path no longer names must not come back clean.
    segments_dir_replaced = check_segments_dir_identity(dir_fd, segments_dir)
    if segments_dir_replaced is not None:
        print(
            f"backfill_ever_converged.py: warning: {segments_dir_replaced}.",
            file=sys.stderr,
        )

    ok = (
        not failed_to_create
        and not ambiguous_sentinels
        and directory_sync_error is None
        and segments_dir_replaced is None
    )
    return {
        "success": ok,
        "directory_sync_error": directory_sync_error,
        "segments_dir_replaced": segments_dir_replaced,
        "durable_root": str(dirs["durable_root"]),
        "applied": bool(args.apply),
        "ledger_path": ledger_path,
        "ledger_source": ledger_source,
        "ever_converged_segs": ever_converged_segs,
        "already_sentineled": already_sentineled,
        # #443. Which writer published each ALREADY-PRESENT marker, as read
        # from the marker's own body: "ledger_update" (earned at a real
        # convergence), "backfill_ever_converged" (retrofitted from a ledger
        # row by a run of this script), "unattributed" (no provenance in the
        # body at all -- every marker written before #443, and any an operator
        # created by hand), or "unreadable" (the body could not be read).
        # DIAGNOSTIC. It never moves a segment between the buckets above and
        # never affects `success`: an unattributed marker protects its segment
        # exactly as much as an attributed one, which is what keeps every
        # existing project's markers valid across this change.
        "sentinel_attribution": sentinel_attribution,
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
            "sentinel files. Without this flag the script issues no mutating "
            "operation and changes no project content -- not a "
            "re-materialized runs/ledger.json, not one sentinel file -- and "
            "only reports what it would do."
        ),
    )
    parser.add_argument(
        "--allow-merge",
        action="store_true",
        help=(
            "Without a pre-existing runs/ledger.json, a dry run refuses by "
            "default rather than silently re-materializing one (that write "
            "would break the 'a dry run changes no project content' "
            "guarantee). Pass this flag to explicitly authorize exactly that "
            "one write (never a sentinel write) for this run. Ignored under "
            "--apply, which always re-materializes regardless."
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

    # `mode_label`, not `mode`: this module's other `mode` is the sentinel's
    # PERMISSION BITS (sentinel_mode(), mark_ever_converged()'s parameter), and
    # one name for two unrelated things in one file reads as a shared concept.
    mode_label = "APPLY" if result["applied"] else "DRY RUN (pass --apply to write)"
    print("=" * 70, file=sys.stderr)
    print("BACKFILL EVER-CONVERGED SENTINELS", file=sys.stderr)
    print("=" * 70, file=sys.stderr)
    print(f"durable_root: {result['durable_root']}", file=sys.stderr)
    print(f"mode: {mode_label}", file=sys.stderr)
    print(
        f"ledger source: {result['ledger_source']} ({result['ledger_path']})",
        file=sys.stderr,
    )
    print(
        f"\never-converged segments (from merged ledger): "
        f"{result['counts']['ever_converged']}",
        file=sys.stderr,
    )
    # One membership test per bucket, so every branch below reads the same way
    # -- the mixed `seg in <list>` / `any(entry["seg"] == seg ...)` spelling it
    # replaces made the two dict-carrying buckets look like a different kind of
    # question than the two plain-list ones.
    already_sentineled = set(result["already_sentineled"])
    created = set(result["created"])
    failed_to_create = {entry["seg"] for entry in result["failed_to_create"]}
    ambiguous_sentinels = {entry["seg"] for entry in result["ambiguous_sentinels"]}
    for seg in result["ever_converged_segs"]:
        if seg in already_sentineled:
            status = "already sentineled"
        elif seg in created:
            status = "CREATED"
        elif seg in failed_to_create:
            status = "FAILED to create"
        elif seg in ambiguous_sentinels:
            # Listed as its own status rather than falling through to "missing
            # sentinel": the entry is NOT missing, and calling it missing is
            # exactly the misreading that let a dangling symlink pass as a
            # successful backfill.
            status = "AMBIGUOUS -- not protected, not modified"
        else:
            # Only a dry run leaves a missing sentinel merely missing. Under
            # --apply, reaching here means the segment was absent at census
            # time and is present now without THIS run creating it: something
            # else won the race, which mark_ever_converged() reports as
            # "already_present" and the buckets deliberately do not record.
            # The old unconditional "(dry run)" label printed a mode the run
            # was not in, on the one line an operator reads per segment.
            status = (
                "missing at census -- created by something else during this run"
                if result["applied"]
                else "missing sentinel (dry run)"
            )
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
