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
segment EXACTLY as ``ledger_update.py:mark_ever_converged()`` does: same
filename convention, same content (``b"converged\\n"``), same mode (``0o644``),
same ``O_CREAT | O_EXCL | O_WRONLY`` idempotent-create semantics (never
deletes or overwrites an existing sentinel). Duplicated here rather than
imported, per this project's "no shared lib between self-contained scripts"
convention (both are standalone entrypoints) -- pinned against the real
writer by a dedicated byte-identity test,
``tests/backfill_ever_converged.test.py``, a drift test, not a second source
of truth.

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
"missing_sentinels": [...], "created": [...], "failed_to_create": [...],
"counts": {...}}``. Failure: ``{"success": false, "error": ...}``. Exit 0 on
success, 1 on any fatal condition -- callers should read stdout, not rely on
the exit code alone.
"""

import argparse
import json
import os
import re
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
    """
    args = []
    if durable_root_str is not None:
        args += ["--durable-root", durable_root_str]
    elif plugin_root_str is not None:
        args += ["--durable-root", str(dirs["durable_root"])]
    if plugin_root_str is not None:
        args += ["--plugin-root", plugin_root_str]
    return args


# ever_converged_path() -- the DURABLE 'this segment has converged at least
# once' sentinel. Stated identically in ledger_update.py (the writer) and
# select_segments.py (the reader) -- and now here (also a writer) -- because
# all three are standalone entrypoints with no shared import; see this
# project's "no shared lib between self-contained scripts" convention.
# tests/backfill_ever_converged.test.py pins this against ledger_update.py's
# own copy by name, a drift test, not a second source of truth.


def ever_converged_path(seg: str, segments_dir: Path) -> Path:
    return segments_dir / f".ever_converged.{seg}"


def mark_ever_converged(seg: str, segments_dir: Path) -> str:
    """Byte-identical duplicate of ledger_update.py's own
    `mark_ever_converged()`: same filename, same content (`b"converged\\n"`),
    same mode (`0o644`), same idempotent `O_CREAT | O_EXCL | O_WRONLY`
    create-only semantics -- NEVER deletes or overwrites an existing
    sentinel.

    Unlike the original (which is non-fatal-by-design and merely warns on
    stderr), this returns a string outcome so the caller can build an
    accurate report: "created" (this call raised it), "already_present" (a
    sentinel already existed -- a no-op, not an error), or an error message
    string (the create failed for some other OSError).
    """
    path = ever_converged_path(seg, segments_dir)
    try:
        fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError:
        return "already_present"
    except OSError as exc:
        return f"error: {exc}"
    try:
        # Content is deliberately fixed, with no timestamp -- see
        # ledger_update.py's own mark_ever_converged() docstring for why.
        os.write(fd, b"converged\n")
    finally:
        os.close(fd)
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


def fatal(message: str, **extra) -> NoReturn:
    raise FatalError(json.dumps({"success": False, "error": message, **extra}, ensure_ascii=False))


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


def load_ledger_segments(merge_result: dict, durable_root: Path) -> dict:
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

    ever_converged_segs = []
    for seg, record in ledger_segments.items():
        if isinstance(record, dict) and record.get("status") in WAS_CONVERGED_STATUSES:
            problem = validate_seg(seg)
            if problem is not None:
                fatal(f"materialized ledger.json: unsafe segment id: {problem}", seg=seg)
            ever_converged_segs.append(seg)
    ever_converged_segs.sort()

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
    already_sentineled = sorted(
        seg for seg in ever_converged_segs if ever_converged_path(seg, segments_dir).exists()
    )
    missing_sentinels = sorted(set(ever_converged_segs) - set(already_sentineled))

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

    return {
        "success": True,
        "durable_root": str(dirs["durable_root"]),
        "applied": bool(args.apply),
        "ledger_path": ledger_path,
        "ledger_source": ledger_source,
        "ever_converged_segs": ever_converged_segs,
        "already_sentineled": already_sentineled,
        "missing_sentinels": missing_sentinels,
        "created": created,
        "failed_to_create": failed_to_create,
        "counts": {
            "ever_converged": len(ever_converged_segs),
            "already_sentineled": len(already_sentineled),
            "missing_sentinels": len(missing_sentinels),
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
            json.dumps({"success": False, "error": f"unexpected error: {exc}"}, ensure_ascii=False),
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
        else:
            status = "missing sentinel (dry run)"
        print(f"  - {seg}: {status}", file=sys.stderr)
    print(
        f"\nalready sentineled: {result['counts']['already_sentineled']}\n"
        f"missing sentinels:  {result['counts']['missing_sentinels']}",
        file=sys.stderr,
    )
    if result["applied"]:
        print(
            f"created this run:   {result['counts']['created']}\n"
            f"failed to create:   {result['counts']['failed_to_create']}",
            file=sys.stderr,
        )
    print("\n" + "=" * 70, file=sys.stderr)

    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
