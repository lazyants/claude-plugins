#!/usr/bin/env python3
"""scaffold_setup.py -- Step 0a's shipped writer for the two bundle-hash
marker files.

NEW in #194. Invoked by the orchestrating Claude session as the FINAL action
of Step 0a (a plain bash call from the plugin path, AFTER every bundle member
has been copied into ${durable_root}/scripts/ and BEFORE any cache_key.py /
resume_setup.py call). See SKILL.md's "Step 0a" section.

THE GAP THIS CLOSES: cache_key.py's compute_plugin_bundle_hash READS
${durable_root}/runs/.plugin_bundle_hash rather than re-hashing the bundle
per segment, and resume_setup.py's compute_input_digest reads BOTH
.plugin_bundle_hash and .orchestration_bundle_hash. Before #194 SKILL.md's
Step 0a only PROSE-described "computes/writes" these markers -- no shipped
script wrote either, so a real run failed "has Step 0a run for this
project?". This script is that writer. (derivation_bundle_hash is computed
live -- no marker -- so this script writes only the two.)

    python3 scaffold_setup.py --durable-root PATH
    python3 scaffold_setup.py --verify --durable-root PATH [--plugin-root PATH]

`--verify` (#396) is the same computation in a READ-ONLY mode, run later in a
session rather than at Step 0a. The markers this script writes characterise the
DURABLE copies as of the moment they were written, but the workflow templates
and several bundle-member gate scripts are read from the LIVE plugin install on
every run (SKILL.md's W5/W3a instantiations; codex_job.py's `--plugin-root`
redirect). So a plugin update landing between Step 0a and a later batch swaps
the executing code without moving the recorded hash, and every gate downstream
stays green over a run that is no longer homogeneous. `--verify` re-checks the
two things that make a marker's claim true -- that it still matches a recompute
over ${durable_root}/scripts/, and that those durable bytes are the bytes the
live tree holds -- and REFUSES on either mismatch. It writes nothing and
repairs nothing: refreshing a marker on drift is exactly the silent masking it
exists to refuse.

PLUGIN-PATH-ONLY -- NEVER copied into a durable_root, and NOT a bundle
member. Two consequences worth stating outright so a later reader does not
"helpfully" shortcut them:

  * Because this script runs from the plugin path, its own `import cache_key`
    binds to the plugin-installed cache_key.py sitting beside it, whose
    module-level DURABLE_ROOT = Path(__file__).resolve().parents[1] points at
    the PLUGIN tree, not the target project. So this script reuses ONLY
    cache_key's PURE helpers (PLUGIN_BUNDLE_MEMBERS, concat_sorted_bytes,
    sha1_hex) and takes the durable_root STRICTLY from --durable-root. It
    must NEVER call cache_key.compute_plugin_bundle_hash() (which reads the
    marker this script writes -- a chicken-and-egg) nor read
    cache_key.DURABLE_ROOT (the wrong tree). It also adds no line to
    cache_key.py itself -- cache_key.py is a PLUGIN_BUNDLE_MEMBER, so a byte
    change there would flip plugin_bundle_hash.

  * Every entry in cache_key.PLUGIN_BUNDLE_MEMBERS -- named there, never
    counted here, so this comment cannot go stale the next time a member is
    added or removed -- both the *.py scripts AND the *.template.js workflow
    templates hash uniformly at ${durable_root}/scripts/<name>. The
    templates get the same scripts/-style placement in the durable tree
    (there is no scripts/templates/ subdir), so this mirrors cache_key.py's
    own compute_derivation_bundle_hash pattern exactly.
"""

import argparse
import os
import secrets
import stat
import sys
from pathlib import Path
from typing import NoReturn

# Set before the `import cache_key` below, which is the only point where it
# still has any effect (verbatim_census.py:109 sets it for the same reason).
# A plain sibling import leaves assets/scripts/__pycache__/*.pyc behind in the
# PLUGIN tree this script runs from -- a real mutation of the very tree
# `--verify` reports on, and one that makes "this mode writes nothing" false.
# Deliberately UNCONDITIONAL rather than scoped to --verify: this is the one
# behavioural difference the write mode inherits, it costs one bytecode
# recompile on a script that runs once or twice per session, and a
# mode-conditional import (module-level for one mode, function-level for the
# other) is a far worse thing to maintain than the .pyc it would preserve.
sys.dont_write_bytecode = True

import cache_key

# Where a bundle member physically ships in a plugin install: the *.template.js
# workflow templates under assets/templates/, everything else under
# assets/scripts/. This is the LIVE-tree layout only -- inside a durable_root
# every member lands flat under scripts/ (see the module docstring), and the
# two must never be conflated.
LIVE_SCRIPTS_SUBPATH = ("assets", "scripts")
LIVE_TEMPLATES_SUBPATH = ("assets", "templates")

# This file's own install tree: assets/scripts/scaffold_setup.py -> the plugin
# root two levels up. Correct as the DEFAULT checked tree precisely because
# this script is never copied into a durable_root (see the module docstring),
# and SKILL.md invokes it as {{PLUGIN_ROOT}}/assets/scripts/scaffold_setup.py
# -- the same {{PLUGIN_ROOT}} the W5/W3a instantiations resolve from.
SELF_PLUGIN_ROOT = Path(__file__).resolve().parents[2]


# The orchestration-bundle scripts -- the tuple below is the authority on how
# many there are. plugin_bundle_hash excludes the orchestration-ONLY ones
# (they carry their own orchestration_bundle_hash); cache_key.py declares no
# shared constant for them (they are non-gating for convergence), so this
# script pins the tuple locally. Guarded byte-for-byte by
# tests/scaffold_setup.test.py::test_orchestration_members_pinned against a
# silent desync of resume_setup.py's resume-integrity digest.
#
# #438: claim_record.py is registered in BOTH bundles, deliberately. It is a
# PLUGIN_BUNDLE_MEMBERS entry (it gates dispatch), and it is ALSO here because
# select_segments.py -- an orchestration member that is deliberately NOT a
# plugin-bundle member -- now imports it. Without that a change to
# claim_record.py's bytes would move plugin_bundle_hash but leave
# orchestration_bundle_hash unchanged, even though the orchestration member's
# behavior changed. Same transitive-import invisibility that forced
# canon_senses.py to be registered once two members imported it. Kept HERE
# rather than inside the tuple so the justification survives a rewrite of the
# literal, and so a reader meets it before the names -- a double registration
# moves two hashes at once and is never to be inferred from the list alone.
ORCHESTRATION_BUNDLE_MEMBERS = (
    "claim_record.py",
    "draft_ready.py",
    "ledger_merge.py",
    "language_smoke_report.py",
    "select_segments.py",
)

# Marker file locations, relative to durable_root -- the EXACT paths
# cache_key.py's compute_plugin_bundle_hash (runs/.plugin_bundle_hash) and
# resume_setup.py's compute_input_digest (both markers) READ.
PLUGIN_BUNDLE_MARKER = ("runs", ".plugin_bundle_hash")
ORCHESTRATION_BUNDLE_MARKER = ("runs", ".orchestration_bundle_hash")

_O_CLOEXEC = getattr(os, "O_CLOEXEC", 0)
_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_O_DIRECTORY = getattr(os, "O_DIRECTORY", 0)


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def fail(message: str) -> NoReturn:
    """Fail loudly, naming the problem, and exit non-zero. Never a bare
    traceback for an expected/actionable condition."""
    print(f"ERROR: {message}", file=sys.stderr)
    sys.exit(1)


# THREAT MODEL / ACCEPTED RESIDUALS: this is a LOCAL single-user tool with NO
# privilege boundary -- it runs as the invoking user, reading that user's own
# plugin scripts and writing that user's own durable_root. The worst-case
# impact of any residual TOCTOU/ABA race below is a WRONG or TRUNCATED
# bundle-hash marker -> a spurious cache decision on the NEXT run, not a
# security compromise. Two residuals are knowingly accepted rather than
# closed, because closing them is net-negative: (1) the sub-instruction
# stat->replace window in atomic_write_text (no portable atomic
# replace-from-fd exists). (2) the ABA directory-swap of scripts/ during
# hashing that dir_identity_or_none's before/after bracket cannot see --
# fully preventing it would require reading members through a pinned dir-fd,
# which is impossible without editing cache_key.py (a PLUGIN_BUNDLE_MEMBER;
# one byte flips plugin_bundle_hash) or reimplementing its byte-scheme (a
# cache-hash divergence risk worse than the residual it would close).


def atomic_write_text(dir_fd: int, name: str, text: str) -> None:
    """Write `text` to `name`, a leaf entry inside the directory pinned by
    `dir_fd`, atomically -- a managed durable_root's clean step is a
    data-loss surface, so a reader must never observe a half-written or
    truncated marker. `dir_fd` is opened by the caller (main()) with
    O_DIRECTORY|O_NOFOLLOW, which pins the directory's inode: main()'s
    scripts_dir/runs_dir guards only stop a symlinked directory at CHECK
    time (check-then-use), but every operation below is resolved relative
    to that already-open fd, so a directory swapped in AFTER the check
    cannot redirect this write. Mirrors resume_setup.py's
    _atomic_write_text (write a sibling temp file, then os.replace it into
    place) but hardened per codex_job.py's _write_joblog: the temp file is
    opened O_CREAT|O_EXCL|O_WRONLY|O_NOFOLLOW under an UNGUESSABLE leaf name
    (secrets.token_hex, not a predictable `.tmp.<pid>`), so a symlink cannot
    be pre-planted at a name known in advance. `name` itself is refused too
    if it is already a symlink -- os.replace()/rename(2) would just replace
    that symlink's own directory entry rather than dereference it, but
    refusing outright keeps this consistent with every other managed-dir
    write in this plugin. The write loops on os.write()'s return value --
    write(2) may legally write FEWER bytes than given without raising,
    which would otherwise publish a truncated marker. Before the final
    publish, an fsync + fstat/stat identity-and-size check fails closed if
    the temp file was substituted (a different inode at the same name) or
    changed size since this call wrote it -- collapsing the create->replace
    attack window to the sub-instruction sliver between that check and
    os.replace() itself, which cannot be eliminated portably (os.replace
    resolves by NAME; there is no atomic replace-from-fd). See the THREAT
    MODEL / ACCEPTED RESIDUALS note below for why that sliver is accepted
    rather than chased further."""
    try:
        existing = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    except FileNotFoundError:
        existing = None
    except OSError as exc:
        fail(f"refusing to write {name}: could not stat existing entry: {exc}")
    if existing is not None and stat.S_ISLNK(existing.st_mode):
        fail(f"refusing to write {name}: it is a symlink, not a real file")

    # Unguessable temp leaf: an attacker who cannot predict the name cannot
    # unlink-and-recreate a short/decoy file at it in the create->replace
    # window (os.replace resolves by NAME, so a substituted inode at a
    # PREDICTABLE temp path would otherwise be the bytes published).
    tmp_name = f".{name}.tmp.{os.getpid()}.{secrets.token_hex(8)}"
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | _O_NOFOLLOW | _O_CLOEXEC
    try:
        fd = os.open(tmp_name, flags, 0o600, dir_fd=dir_fd)
    except OSError as exc:
        fail(f"refusing to write {name}: could not create temp file {tmp_name}: {exc}")
    try:
        data = text.encode("utf-8")
        view = memoryview(data)
        written = 0
        while written < len(data):
            written += os.write(fd, view[written:])
        os.fsync(fd)
        # Fail closed if the entry we are about to publish is no longer the
        # exact inode we just wrote (a substitution in the create->replace
        # window). The final stat->replace sliver cannot be eliminated
        # portably (os.replace resolves by name; there is no atomic
        # replace-from-fd), but this collapses the exploitable window to
        # sub-instruction size and is a DOCUMENTED-ACCEPTED residual under
        # this tool's threat model (see the module note below).
        fd_st = os.fstat(fd)
        try:
            name_st = os.stat(tmp_name, dir_fd=dir_fd, follow_symlinks=False)
        except OSError as exc:
            fail(f"refusing to write {name}: temp file vanished before publish: {exc}")
        if (fd_st.st_dev, fd_st.st_ino) != (name_st.st_dev, name_st.st_ino):
            fail(f"refusing to write {name}: temp file was substituted before publish")
        if name_st.st_size != len(data):
            fail(f"refusing to write {name}: temp file changed size before publish")
    finally:
        os.close(fd)
    os.replace(tmp_name, name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)


def dir_identity_or_none(path: Path):
    """Return `path`'s (st_dev, st_ino), opened with O_DIRECTORY|O_NOFOLLOW,
    or None if it cannot be opened as a stable real directory right now
    (missing, swapped for a symlink, or somehow not a directory despite an
    earlier check). Used to bracket compute_bundle_hash()'s scripts_dir
    reads with a before/after identity check: concat_sorted_bytes reads
    each member by PATH (re-resolving through scripts_dir on every read),
    so it can't be routed through a pinned dir-fd without risking a
    divergence from cache_key.py's own hash scheme -- this detects (rather
    than prevents) a directory swap that happens mid-hash and refuses to
    trust the result, which is the fail-closed posture main() needs."""
    flags = os.O_RDONLY | _O_DIRECTORY | _O_NOFOLLOW | _O_CLOEXEC
    try:
        fd = os.open(path, flags)
    except OSError:
        return None
    try:
        st = os.fstat(fd)
    finally:
        os.close(fd)
    if not stat.S_ISDIR(st.st_mode):
        return None
    return (st.st_dev, st.st_ino)


def compute_bundle_hash(durable_root: Path, members, what: str) -> str:
    """sha1 of the sorted-by-filename concatenated raw bytes of `members`
    under ${durable_root}/scripts/ -- the exact scheme cache_key.py's
    compute_derivation_bundle_hash uses (concat_sorted_bytes -> sha1_hex).
    Reuses cache_key's PURE helpers only (see the module docstring's footgun
    note); concat_sorted_bytes fails loudly, naming the path, if a member is
    missing from scripts/."""
    paths = [durable_root / "scripts" / name for name in members]
    blob = cache_key.concat_sorted_bytes(paths, what)
    return cache_key.sha1_hex(blob)


# ---------------------------------------------------------------------------
# --verify (#396): the marker's claim, re-checked against the live install
# ---------------------------------------------------------------------------


def live_member_path(plugin_root: Path, name: str) -> Path:
    """Where `name` sits in a plugin INSTALL tree -- not in a durable_root,
    where every member lands flat under scripts/. assets/scripts/ holds no
    *.js at all, so the suffix is the whole rule."""
    if name.endswith(".template.js"):
        return plugin_root.joinpath(*LIVE_TEMPLATES_SUBPATH, name)
    return plugin_root.joinpath(*LIVE_SCRIPTS_SUBPATH, name)


def regular_file_problem(path: Path):
    """None when `path` is a genuine, non-symlink regular file; otherwise a
    short phrase naming what it is instead.

    Byte equality at a SYMLINKED member path is not evidence about the tree
    that member's own siblings resolve in: review_ready.py runs the
    draft_sha1.py sitting beside its RESOLVED __file__, and codex_job.py
    invokes it without forwarding --plugin-root. So a member symlinked to a
    byte-identical copy in a directory holding a divergent sibling would
    compare EQUAL here while executing code from outside the checked tree.
    Refusing every non-regular member closes that, and covers the ordinary
    case it shares a shape with -- a member missing from one tree because the
    install was downgraded. os.lstat, never Path.is_file(), which FOLLOWS the
    symlink and would report the target's type."""
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return "missing"
    except OSError as exc:
        return f"unreadable ({exc.strerror or exc})"
    if stat.S_ISLNK(st.st_mode):
        return "a symlink"
    if not stat.S_ISREG(st.st_mode):
        return "not a regular file"
    return None


def read_marker(durable_root: Path, marker_rel, what: str) -> str:
    """The marker's recorded value, `.strip()`ed and strictly decoded exactly
    as cache_key.py's compute_plugin_bundle_hash and resume_setup.py's
    compute_input_digest read it -- never a second parsing convention.

    Its shape is NOT checked here: run_verify()'s first pass has already
    refused a marker that is not a plain file, alongside the members, so that
    one run names every offender. What remains -- unreadable, or present but
    empty -- aborts on the spot rather than aggregating, because it is a
    different question ("has Step 0a run at all?") than "which files drifted"."""
    path = durable_root.joinpath(*marker_rel)
    try:
        raw = path.read_bytes()
    except OSError as exc:
        fail(f"could not read {what} at {path}: {exc} -- has Step 0a run for this project?")
    value = raw.decode("utf-8").strip()
    if not value:
        fail(f"{path} is empty -- has Step 0a run for this project?")
    return value


def run_verify(durable_root: Path, plugin_root: Path, scripts_dir: Path) -> int:
    """--verify's body. Three checks, in this order, each one a precondition
    of the next, each with its own reason code so the label is exactly true:

      1. member_not_regular -- every member, in BOTH trees, is a genuine
         non-symlink regular file. Nothing below can be trusted otherwise:
         compute_bundle_hash would fail with its own generic message on the
         first unreadable member, and a byte comparison through a symlink
         compares the target, not the member.
      2. marker_stale -- each marker still equals a recompute over
         ${durable_root}/scripts/. Checked BEFORE the live comparison
         deliberately: a durable copy edited in place (the #412 vector, where
         the durable tree is codex-writable) differs from the live tree too,
         and reporting THAT as a plugin drift would name the wrong culprit.
      3. live_plugin_drift -- the durable bytes are the bytes `plugin_root`
         holds. Only reachable once the marker is known truthful about the
         durable copies, which is what makes this the plugin's drift.

    Writes nothing and repairs nothing on any path."""
    bundles = (
        (
            "plugin_bundle_hash",
            PLUGIN_BUNDLE_MARKER,
            cache_key.PLUGIN_BUNDLE_MEMBERS,
            "a plugin-bundle member",
        ),
        (
            "orchestration_bundle_hash",
            ORCHESTRATION_BUNDLE_MARKER,
            ORCHESTRATION_BUNDLE_MEMBERS,
            "an orchestration-bundle member",
        ),
    )

    # Deduped by member NAME, not by tuple: claim_record.py is deliberately
    # registered in both bundles, and reporting it twice would misrepresent
    # one offending file as two. The dedup is a REPORTING device only -- the
    # hashes below are each computed over their own full tuple.
    checked = []
    seen = set()
    for _field, _marker_rel, members, _what in bundles:
        for name in members:
            if name not in seen:
                seen.add(name)
                checked.append(name)

    # The two SOURCE DIRECTORIES in the checked tree get the same treatment
    # ${durable_root}/scripts/ already gets from main(). Without it the byte
    # comparison can be made self-referential: point a plugin root whose
    # assets/scripts is a symlink INTO the durable tree at a tampered durable
    # root and every member compares equal to itself, so a tampered state
    # verifies clean. Reproduced before this guard existed. is_dir() follows
    # symlinks, which is exactly why it cannot stand alone here.
    for subpath in (LIVE_SCRIPTS_SUBPATH, LIVE_TEMPLATES_SUBPATH):
        source_dir = plugin_root.joinpath(*subpath)
        if source_dir.is_symlink() or not source_dir.is_dir():
            fail(
                f"refusing to verify: {source_dir} is not a real directory in "
                "the checked plugin tree, so a byte comparison against it "
                "would not be a comparison against that tree "
                "(reason=plugin_source_dir_not_real)"
            )

    shape = []
    for name in checked:
        for label, path in (
            ("durable", scripts_dir / name),
            ("plugin", live_member_path(plugin_root, name)),
        ):
            problem = regular_file_problem(path)
            if problem is not None:
                shape.append(f"{name}: {label} copy is {problem} ({path})")
    # The MARKERS get the same predicate. atomic_write_text refuses to publish
    # over a marker NAME that is a symlink, but read_marker would follow one:
    # the runs/ guard covers the directory, not the leaf, so without this a
    # marker symlinked anywhere would be believed.
    for field, marker_rel, _members, _what in bundles:
        marker_path = durable_root.joinpath(*marker_rel)
        problem = regular_file_problem(marker_path)
        if problem is not None:
            shape.append(f"{field}: marker is {problem} ({marker_path})")
    if shape:
        fail(
            "refusing to verify: a bundle member or marker is not a plain "
            "file where it must be, so neither the recomputed hash nor a byte "
            "comparison would mean what it claims "
            "(reason=member_not_regular)\n  - "
            + "\n  - ".join(shape)
        )

    computed = {}
    stale = []
    for field, marker_rel, members, what in bundles:
        recomputed = compute_bundle_hash(durable_root, members, what)
        computed[field] = recomputed
        recorded = read_marker(durable_root, marker_rel, f"the {field} marker file")
        if recorded != recomputed:
            marker_path = durable_root.joinpath(*marker_rel)
            stale.append(
                f"{field}: {marker_path} records {recorded}, but "
                f"{scripts_dir} now hashes to {recomputed}"
            )
    if stale:
        fail(
            "refusing: a Step 0a marker no longer describes the durable copies "
            "it was computed over -- re-run the Step 0a copy pass and this "
            "script's write mode only if that is a change you intend "
            "(reason=marker_stale)\n  - " + "\n  - ".join(stale)
        )

    drift = []
    for name in checked:
        live = live_member_path(plugin_root, name)
        if (scripts_dir / name).read_bytes() != live.read_bytes():
            drift.append(f"{name}: durable copy differs from {live}")
    if drift:
        fail(
            f"refusing: the live install at {plugin_root} no longer holds the "
            "bytes the Step 0a markers characterise, so this run would execute "
            "orchestration code the recorded hashes do not describe "
            "(reason=live_plugin_drift)\n  - " + "\n  - ".join(drift)
        )

    print(
        "scaffold_setup: verified "
        f"plugin_bundle_hash={computed['plugin_bundle_hash']} "
        f"orchestration_bundle_hash={computed['orchestration_bundle_hash']} "
        f"plugin_root={plugin_root}"
    )
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Step 0a's final action: compute and atomically write the "
            "plugin_bundle_hash / orchestration_bundle_hash marker files into "
            "${durable_root}/runs/. Run from the plugin path, after the Step "
            "0a copy pass has populated ${durable_root}/scripts/. With "
            "--verify, re-checks those markers against the durable copies AND "
            "against a live plugin install, and writes nothing."
        )
    )
    parser.add_argument(
        "--durable-root",
        required=True,
        metavar="PATH",
        help="The project's durable_root (its scripts/ already populated by "
        "the Step 0a copy pass).",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Read-only mode (#396): refuse if either marker disagrees with a "
        "recompute over ${durable_root}/scripts/, or if any bundle member's "
        "durable bytes differ from the live plugin install's. Writes nothing.",
    )
    parser.add_argument(
        "--plugin-root",
        default=None,
        metavar="PATH",
        help="--verify only: the plugin install whose bytes the durable copies "
        "are checked against ({{PLUGIN_ROOT}}, i.e. the directory holding "
        "assets/scripts/). Omitted, this script's own install tree is used.",
    )
    args = parser.parse_args(argv)

    if args.plugin_root is not None and not args.verify:
        # Refused rather than ignored: silently accepting it would let an
        # operator believe a WRITE was checked against a named tree.
        fail("--plugin-root is only meaningful with --verify")

    plugin_root = SELF_PLUGIN_ROOT
    if args.plugin_root is not None:
        if not args.plugin_root.strip():
            # An unsubstituted {{PLUGIN_ROOT}} token renders as the empty
            # string, not as the flag being omitted, and Path("").resolve()
            # is the CWD -- silently making wherever this happens to be
            # launched from the tree the verdict is about. codex_job.py and
            # segment_dispatch_driver.py refuse the same input by name.
            fail(
                "--plugin-root was given but is empty/whitespace-only -- this "
                "usually means a {{PLUGIN_ROOT}} template substitution did not "
                "happen. Omit the flag to check this script's own install tree, "
                "or pass a real path."
            )
        # .resolve() to an ABSOLUTE path, and echoed on the success line
        # below: a relative value names a different tree from a different
        # cwd, and segment_dispatch_driver.py resolves its own --plugin-root
        # against ITS runtime cwd -- so "pass the same string" is not by
        # itself enough to bind the verified tree to the executed one.
        plugin_root = Path(args.plugin_root).resolve()
        if not plugin_root.joinpath(*LIVE_SCRIPTS_SUBPATH).is_dir():
            fail(
                f"--plugin-root {args.plugin_root} does not resolve to a "
                f"directory containing assets/scripts/ (resolved: {plugin_root})"
            )

    durable_root = Path(args.durable_root)
    if not durable_root.is_dir():
        fail(f"--durable-root does not resolve to a directory: {durable_root}")
    scripts_dir = durable_root / "scripts"
    if not scripts_dir.is_dir():
        fail(
            f"{scripts_dir} does not exist -- run the Step 0a copy pass "
            "(which populates scripts/) before scaffold_setup.py"
        )
    if scripts_dir.is_symlink():
        # scripts_dir is a DIRECT child of the trusted durable_root -- the
        # same vector assemble.py's own out_dir guard closes for "out/"
        # (its ASSEMBLED_DIR.parent check). is_dir() above FOLLOWS a
        # symlink, so a planted `${durable_root}/scripts -> /external`
        # symlink would otherwise pass that check and let
        # compute_bundle_hash()'s member reads hash bytes from OUTSIDE
        # durable_root entirely. durable_root ITSELF is deliberately NOT
        # checked here -- a legitimately symlinked skill install
        # (durable_root pointing elsewhere, scripts/ a real subdirectory
        # underneath) must keep working, mirroring the same carve-out
        # assemble.py documents for its own out_dir/DURABLE_ROOT pair.
        fail(
            f"refusing to hash bundle members: {scripts_dir} is a symlink, "
            "not a real directory (reason=scripts_dir_is_symlink)"
        )
    runs_dir = durable_root / "runs"
    if runs_dir.is_symlink():
        # Same vector, one level over: both PLUGIN_BUNDLE_MARKER and
        # ORCHESTRATION_BUNDLE_MARKER write through ${durable_root}/runs/
        # -- a planted `runs -> /external` symlink would let the mkdir +
        # os.replace below land the marker files outside durable_root.
        # Checked fail-closed BEFORE any hashing/writing happens, same as
        # the scripts_dir guard above (runs/ not existing yet is fine --
        # it gets created fresh below). This is a friendly early error for
        # the common case; the dir-fd open right before the writes (below)
        # is the guard that actually closes the race against a swap that
        # happens AFTER this check.
        fail(
            f"refusing to trust the bundle-hash markers: {runs_dir} is a "
            "symlink, not a real directory (reason=runs_dir_is_symlink)"
        )

    # scripts_dir's is_symlink() check above is check-then-use: a directory
    # that is real at check time but SWAPPED (for a symlink, or for a
    # different real directory) before/during the hash reads below is not
    # caught by that check alone. concat_sorted_bytes reads each member by
    # path -- re-resolving through scripts_dir on every read -- so it can't
    # be pinned to a dir-fd without risking a byte-scheme divergence from
    # cache_key.py's own hashing (the load-bearing invariant this script
    # exists to preserve). Bracket the hash with a before/after inode
    # identity check instead: fail closed if scripts_dir's (st_dev, st_ino)
    # is not the SAME real directory on both sides of the hashing pass.
    before_scripts_identity = dir_identity_or_none(scripts_dir)
    if before_scripts_identity is None:
        fail(
            f"refusing to hash bundle members: {scripts_dir} is not a "
            "stable real directory right now (reason=scripts_dir_swapped)"
        )

    if args.verify:
        # Branches BEFORE the write path's own hashing: compute_bundle_hash
        # goes through concat_sorted_bytes, which fails loudly on the first
        # unreadable member -- correct when a marker is about to be written,
        # but it would pre-empt run_verify()'s whole job of naming EVERY
        # offending member in one run.
        #
        # The before/after identity bracket still closes around it. run_verify
        # reads scripts_dir MORE than the write path does -- both bundle
        # hashes, plus a byte read per member for the drift leg -- so leaving
        # the bracket to the write path alone would leave the ABA
        # directory-swap window widest open in the mode that reads most. Its
        # refusals exit non-zero from inside, so this only ever guards the
        # GREEN path, which is precisely where a masked swap turns into a
        # false pass.
        verify_status = run_verify(durable_root, plugin_root, scripts_dir)
        if dir_identity_or_none(scripts_dir) != before_scripts_identity:
            fail(
                f"refusing to trust the verification: {scripts_dir} changed "
                "identity while its members were being read "
                "(reason=scripts_dir_swapped)"
            )
        return verify_status

    plugin_bundle_hash = compute_bundle_hash(
        durable_root, cache_key.PLUGIN_BUNDLE_MEMBERS, "a plugin-bundle member"
    )
    orchestration_bundle_hash = compute_bundle_hash(
        durable_root, ORCHESTRATION_BUNDLE_MEMBERS, "an orchestration-bundle member"
    )

    after_scripts_identity = dir_identity_or_none(scripts_dir)
    if after_scripts_identity != before_scripts_identity:
        fail(
            f"refusing to trust the computed bundle hashes: {scripts_dir} "
            "changed identity while its members were being hashed "
            "(reason=scripts_dir_swapped)"
        )

    # runs_dir's is_symlink() check above is the same check-then-use gap,
    # one level over. Pin runs/ with a dir-fd opened O_DIRECTORY|O_NOFOLLOW
    # -- it fails closed if runs/ is (or just became) a symlink, and every
    # write below is resolved relative to this already-open fd, so it is
    # immune to a LATER swap of runs/ itself. mkdir first (exist_ok) so the
    # common first-run case, where runs/ does not exist yet, still works;
    # the O_NOFOLLOW open right after is the real security boundary, not
    # the mkdir (mkdir's exist_ok path would silently accept a symlink that
    # happens to point at a real directory -- the open does not).
    runs_dir.mkdir(parents=True, exist_ok=True)
    runs_flags = os.O_RDONLY | _O_DIRECTORY | _O_NOFOLLOW | _O_CLOEXEC
    try:
        runs_fd = os.open(runs_dir, runs_flags)
    except OSError as exc:
        fail(f"refusing to write bundle-hash markers: could not open {runs_dir}: {exc}")
    try:
        atomic_write_text(runs_fd, PLUGIN_BUNDLE_MARKER[-1], plugin_bundle_hash + "\n")
        atomic_write_text(runs_fd, ORCHESTRATION_BUNDLE_MARKER[-1], orchestration_bundle_hash + "\n")
    finally:
        os.close(runs_fd)

    print(
        "scaffold_setup: wrote "
        f"plugin_bundle_hash={plugin_bundle_hash} "
        f"orchestration_bundle_hash={orchestration_bundle_hash}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
