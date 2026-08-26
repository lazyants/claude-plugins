#!/usr/bin/env python3
"""driver_status.py -- #765 read-only progress surface for a mass-translate batch.

`segment_dispatch_driver.py` runs for hours and prints its one JSON line only
when it is over. While it runs there was no supported way to answer the
operator's actual question -- *is this still working, how far along is it, or
did it already finish?* -- and two book projects independently hand-rolled the
same `status.sh` to fill the gap. This script is that surface, shipped.

    python3 driver_status.py [--durable-root PATH]

Exit 0 = a report was produced, including "no driver journal exists here".
Exit 1 = no report is possible (durable root unreadable, or manifest.json
absent/unparseable/empty -- `select_segments.py` refuses an empty `segments[]`
rather than reporting zero work, and this script agrees with it rather than
printing a zero that means an absence). Exit 2 = usage / unsafe argument.

## What this script deliberately does NOT say

It reports what an artifact RECORDS, with its provenance, and never concludes a
process state. There is no `state: "running" | "finished" | "died"` field, and
that absence is the design, not an omission:

  * `append_journal()` is best-effort BY DESIGN -- its own docstring says a
    journal write failure "is logged to stderr but never aborts the driver".
    So a missing `driver_exit` does not mean the run died, and a recorded
    dispatch count is a count of ENTRIES, never of work.
  * `runs/.driver.lock`'s CONTENT is documented by `acquire_driver_lock()` as
    existing "purely for a human to read while debugging who is holding this"
    -- diagnostic only, its write likewise best-effort, and explicitly NOT the
    lease. The lease is the kernel `flock`, and probing that would take a lock
    against a live run, which this script must never do.
  * `ps` argv text is not process identity: POSIX permits truncating `args`.
  * `fresh_session_id()` and every journal `ts` come from the wall clock, which
    is not monotonic, so "the greatest recorded start time" is not "the latest
    invocation" after a backward clock step.

Every one of those is advisory, so a verdict built on them can always be wrong.
Two review rounds put their findings in exactly that place. What the payload
publishes instead is the observations themselves -- `recorded_exit`,
`pid_alive`, `ps_command`, `last_recorded_event.age_sec` -- each true by
construction, and each named so it cannot be read as more than it is. SKILL.md's
`jq` line composes them into the sentence an operator wants; a composition can
be read with judgement, a field saying `"died"` cannot.

## Read-only, and how far that claim goes

This script opens nothing for write, imports `fcntl` nowhere (so no `flock` is
reachable at all), never invokes `select_segments.py` (which shells
`ledger_merge.py`, and that REWRITES `runs/ledger.json` and shells
`cache_key.py` per converged fragment -- a durable write, however read-only the
classification itself is), and spawns exactly one subprocess: `ps -ww -p PID -o
command=`. `sys.dont_write_bytecode` is set before the `json_stdout.py` sibling
import below, because that import would otherwise leave
`${durable_root}/scripts/__pycache__/*.pyc` behind -- the one write this design
would otherwise make. Every read also refuses a
symlink or a non-regular file before opening it: a symlinked `runs/ledger.d`
would count another book's population as this one's, and a FIFO named like a
fragment would block forever -- which would break the one property that matters
most here. `tests/driver_status.test.py` guards each of those structurally (an
AST import allowlist plus an enumerated write/lock construct set -- metadata
mutators included, since a `chmod` changes nothing a content-only snapshot would
notice -- every clause mutation-tested) and behaviourally (a run against a
durable root whose directories are 0555 and files 0444, with a private empty
TMPDIR and a before/after snapshot that covers mode and mtime, the root's
included). Those are regression GUARDS on this implementation, not a proof about
every possible one.

## Where each number comes from

`units.total` is the distinct `segments[].seg` of `manifest.json`. It is
DERIVED, never configured: both hand-rolled copies hard-coded their book's unit
count (`/ 74`, `/ 79`) and #765 proposed substituting it at scaffold time.

Progress comes in TWO scopes, because they answer different questions and
diverge exactly when it matters: `run.batch_progress` is restricted to the
segment ids THIS run's Step 1 gate selected, and `progress` covers the whole
manifest. A run launched with `--only-segs` for ten fresh units in a book where
seventy already converged is 0/10 on the first and 70/80 on the second; showing
only the second would answer "how far along is this batch" over the wrong
population.

Both are the per-segment fragments under `runs/ledger.d/`, intersected with
the manifest -- NOT `runs/ledger.json`. SKILL.md states the reason itself:
"The driver does not refresh `runs/ledger.json`... it still reports PRE-run
state". `select_segments.py` materializes that file at run START and the driver
never re-merges, so it freezes for the whole run (measured on a live book:
fragments 21 converged, `ledger.json` 20). The bare merge also takes EVERY
fragment with no manifest filter, so a total read off it can count obsolete ids
and miss present ones -- hence `manifest_ids_without_fragment` and
`fragment_ids_not_in_manifest` are published rather than folded away.

All five `ledger-fragment.schema.json` statuses are zero-filled, read from that
schema rather than restated here: `non_converged` is written on the ordinary cap
path, and folding it (or `pending`/`blocked`) into `in_progress` would answer
"how far along" over the wrong population.

There is deliberately NO draft-file census. `segments/*.draft.json` also matches
`codex_job.py`'s private `.att.<seg>.<INV>.draft.json` and
`.att_pending.<seg>.draft.json` staging slots (`select_segments.py` documents
exactly this trap), and a draft exists from round 1 onward and never goes away,
so the count saturates long before the book converges. The fragment census
answers the same question without either failure.
"""
import sys

# BEFORE the sibling import below: a by-path import of json_stdout.py would
# otherwise write ${durable_root}/scripts/__pycache__/json_stdout.*.pyc into the
# tree this script promises not to touch. `fix_scope_audit.py` names that same
# directory an EXECUTION surface for the same reason.
sys.dont_write_bytecode = True

import argparse
import importlib.util as _importlib_util
import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

# --- the shared one-line JSON serialiser (#369) -----------------------------
# Loaded by EXACT PATH, never `import json_stdout` -- the same loader block
# every other stdout site in this directory carries, and for the same reason: a
# bare sibling import resolves through the global sys.modules cache regardless
# of which staged copy the caller intended.
_JSON_STDOUT_PATH = Path(__file__).absolute().parent / "json_stdout.py"
try:
    _json_stdout_spec = _importlib_util.spec_from_file_location(
        "json_stdout", _JSON_STDOUT_PATH
    )
    if _json_stdout_spec is None or _json_stdout_spec.loader is None:
        raise ImportError(f"no loader for {_JSON_STDOUT_PATH}")
    _json_stdout = _importlib_util.module_from_spec(_json_stdout_spec)
    _json_stdout_spec.loader.exec_module(_json_stdout)
except (ImportError, OSError) as _json_stdout_exc:  # pragma: no cover - staging error path
    sys.exit(
        f"driver_status.py: cannot load json_stdout.py from {_JSON_STDOUT_PATH} "
        f"({_json_stdout_exc}).\n"
        "json_stdout.py must be installed alongside driver_status.py under "
        "${durable_root}/scripts/ -- Step 0a's copy pass places it there."
    )

dumps_line = _json_stdout.dumps_line

DURABLE_ROOT = Path(__file__).resolve().parents[1]

# Mirrors select_segments.py's `_SEG_ID_RE` -- (FRONTBACK:)?[A-Za-z0-9_]+.
# Restated rather than imported because importing that 6 000-line
# PLUGIN_BUNDLE_MEMBER for one pattern would couple a diagnostic to the dispatch
# gate. Mirroring is safe in this ONE direction only: a stricter pattern here can
# only refuse an id the selector would have accepted, never admit one it refuses,
# and every id reaching this script is used to build a path that is READ.
_SEG_ID_RE = re.compile(r"(FRONTBACK:)?[A-Za-z0-9_]+")

# The driver's own timestamp shape (`_utc_now_iso`), used for every journal
# entry and for the lock file's diagnostic `started_at`.
_TS_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

# The driver's journal entry types this script reads. Named here so a reader can
# see the whole dependency on the driver's record in one place.
_START = "driver_started"
_EXIT = "driver_exit"
_GATE_PASSED = "step1_gate_passed"
_DISPATCH_STARTED = "codex_dispatch_started"
_DISPATCH_FINISHED = "codex_dispatch_finished"
_LEASE_WARNING = "lock_self_test_failed"


def _within(path: Path, root: Path) -> bool:
    """True when `path` resolves INSIDE `root`, symlinks and all.

    The final-component checks in `_read_text` catch a symlinked artifact and a
    FIFO, but not a symlinked ANCESTOR: a durable tree whose `runs/` or
    `schemas/` is a link into another book would otherwise have its fragments,
    its lock and its status enum read from that other tree and reported as this
    root's. Resolving and testing containment covers every level at once, and a
    link that stays inside the root is fine -- it is an ESCAPE that is refused,
    not a link.
    """
    try:
        return path.resolve().is_relative_to(root)
    except (OSError, ValueError):
        return False


def _real_dir(path: Path, root: Path) -> bool:
    """True when `path` is a real directory INSIDE `root`.

    The directory-granular twin of `_read_text`'s refusal, and named once
    because all three directories this script walks -- `runs/`, `runs/ledger.d`
    for the manifest census and the same for the batch census -- have to refuse
    the identical class. A symlinked `runs/ledger.d` would otherwise count
    another book's population as this root's.
    """
    return _within(path, root) and not path.is_symlink() and path.is_dir()


class StatusError(Exception):
    """No report is possible -- always this script's exit 1. A usage error is
    the OTHER path (exit 2) and never raises this."""

    def __init__(self, message):
        super().__init__(message)
        self.message = message


# ---------------------------------------------------------------------------
# Small readers. Every one of them returns a value plus a REASON on failure --
# never a zero or an empty list that a caller could not tell from an absence.
# ---------------------------------------------------------------------------


def _read_text(path: Path, root: Path):
    """Read `path` as UTF-8 text, or return (None, reason).

    REFUSES anything that is not a real regular file, before opening it. Two
    distinct reasons, and the second is why the check is here rather than at one
    call site: a SYMLINK can point at another tree, so following it would count
    an artifact that is not this durable root's; and a FIFO or device named
    `seg01.json` would BLOCK this read forever, which would break the one
    property that matters most -- safe to run at any moment. `is_file()` is
    already False for a FIFO, a socket and a directory; `is_symlink()` adds the
    link case, which `is_file()` follows. `fix_scope_audit.py` refuses the same
    class for the same reason, under its own `irregular` verdict.

    `read_bytes` + a replacing decode rather than `read_text`: a journal being
    appended to right now can be truncated mid-codepoint, and a UnicodeDecodeError
    there would lose every well-formed line before it. The replacement character
    lands inside one line, that line fails to parse as JSON, and it is counted as
    malformed -- which is exactly what it is.
    """
    try:
        if not _within(path, root):
            return None, f"{path} resolves outside the durable root"
        if path.is_symlink() or not path.is_file():
            return None, f"{path} is not a real regular file"
        return path.read_bytes().decode("utf-8", "replace"), None
    except OSError as exc:
        return None, f"{path}: {exc}"


def _parse_ts(value):
    if not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value, _TS_FORMAT).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _age_sec(ts_value, now):
    parsed = _parse_ts(ts_value)
    if parsed is None:
        return None
    return int((now - parsed).total_seconds())


def load_manifest_segs(manifest_path: Path, root: Path) -> list:
    """The distinct `segments[].seg` of manifest.json, in manifest order.

    Refuses rather than reporting a zero, matching `load_candidate_segments()`
    in select_segments.py: an empty `segments[]` is a broken project, not a
    project with no work.
    """
    text, reason = _read_text(manifest_path, root)
    if text is None:
        raise StatusError(f"cannot read manifest.json ({reason})")
    try:
        manifest = json.loads(text)
    # RecursionError, not just JSONDecodeError: a nested document that exhausts
    # the C stack raises that instead -- another way a malformed artifact could
    # escape as a traceback with no JSON line, which is the one output contract
    # every caller of this script depends on.
    except (json.JSONDecodeError, RecursionError) as exc:
        raise StatusError(f"manifest.json at {manifest_path} is not valid JSON ({exc})")
    if not isinstance(manifest, dict) or not isinstance(manifest.get("segments"), list):
        raise StatusError(f"manifest.json at {manifest_path} has no 'segments' array")

    segs = []
    seen = set()
    for item in manifest["segments"]:
        if not isinstance(item, dict) or not isinstance(item.get("seg"), str):
            raise StatusError(f"manifest.json: malformed segments[] entry: {item!r}")
        seg = item["seg"]
        if not _SEG_ID_RE.fullmatch(seg):
            raise StatusError(f"manifest.json: unsafe segment id: {seg!r}")
        if seg not in seen:
            seen.add(seg)
            segs.append(seg)
    if not segs:
        raise StatusError(f"manifest.json at {manifest_path} has an empty 'segments' array")
    return segs


def fragment_status_enum(schemas_dir: Path, root: Path):
    """The five statuses ledger_update.py may write, read from the schema that
    owns them rather than restated as a literal here -- a hand-copied member list
    is the restated-list-goes-stale trap this repo has been bitten by twice.
    Returns (statuses, source); `source` is "observed" when the schema cannot be
    read, and the caller then zero-fills nothing it did not see.
    """
    schema_path = schemas_dir / "ledger-fragment.schema.json"
    text, _reason = _read_text(schema_path, root)
    if text is None:
        return None, "observed"
    try:
        schema = json.loads(text)
    except (json.JSONDecodeError, RecursionError):
        return None, "observed"
    enum = (
        schema.get("properties", {}).get("status", {}).get("enum")
        if isinstance(schema, dict)
        else None
    )
    if not isinstance(enum, list) or not all(isinstance(x, str) for x in enum) or not enum:
        return None, "observed"
    return list(enum), "schemas/ledger-fragment.schema.json"


def read_fragment_statuses(frag_dir: Path, segs: list, root: Path):
    """The recorded `status` of every one of `segs` that has a fragment.

    Returns (statuses, unreadable, missing): a {seg: status} map, the count of
    fragments present but not readable as an object with a string status, and
    the manifest ids with no fragment at all. Keyed by EXACT filename, never by
    prefix, so `seg1` and `seg11` cannot be confused for one another.
    """
    statuses = {}
    unreadable = 0
    missing = []
    for seg in segs:
        path = frag_dir / f"{seg}.json"
        text, _reason = _read_text(path, root)
        if text is None:
            if path.exists() or path.is_symlink():
                unreadable += 1
            else:
                missing.append(seg)
            continue
        try:
            fragment = json.loads(text)
        except (json.JSONDecodeError, RecursionError):
            unreadable += 1
            continue
        status = fragment.get("status") if isinstance(fragment, dict) else None
        if isinstance(status, str):
            statuses[seg] = status
        else:
            unreadable += 1
    return statuses, unreadable, missing


def census(statuses: dict, enum):
    """Per-status counts, with every schema status zero-filled when the enum is
    known. Returns (counts, unrecognized): a status outside the enum is COUNTED
    separately rather than dropped, because a dropped one is invisible in a total
    that still looks complete."""
    counts = {name: 0 for name in (enum or [])}
    unrecognized = 0
    for status in statuses.values():
        if enum is not None and status not in counts:
            unrecognized += 1
        else:
            counts[status] = counts.get(status, 0) + 1
    return counts, unrecognized


def read_progress(durable_root: Path, manifest_segs: list, enum, enum_source: str):
    """The per-segment fragment census over the WHOLE manifest.

    Returns (progress, reason). `reason` is non-None exactly when `progress` is
    None -- an absent `runs/ledger.d/` is an ABSENCE, and reporting it as five
    zeroes would be indistinguishable from a book nothing has run against.

    `scope` is published because this is the DURABLE ROOT's progress, not the
    running batch's: a run launched with `--only-segs` for ten fresh units in a
    book where seventy already converged is 0/10, while this census correctly
    reads 70/80. The batch-scoped number lives on `run.batch_progress`, taken
    from the gate's own segment list.
    """
    frag_dir = durable_root / "runs" / "ledger.d"
    if not _real_dir(frag_dir, durable_root):
        return None, (
            f"{frag_dir} is not a real directory -- no fragment has been written "
            f"yet, or the path is a symlink into another tree"
        )
    try:
        on_disk = {
            entry.name[: -len(".json")]
            for entry in frag_dir.iterdir()
            if entry.name.endswith(".json")
        }
    except OSError as exc:
        return None, f"{frag_dir} is not readable ({exc})"

    statuses, unreadable, missing = read_fragment_statuses(
        frag_dir, manifest_segs, durable_root
    )
    counts, unrecognized = census(statuses, enum)

    manifest_set = set(manifest_segs)
    extra = sorted(name for name in on_disk if name not in manifest_set)

    progress = {
        "source": "runs/ledger.d",
        "scope": "manifest",
        "status_enum_source": enum_source,
        "recorded_fragment_status_counts": counts,
        "unrecognized_status": unrecognized,
        "unreadable_fragments": unreadable,
        "manifest_ids_without_fragment": len(missing),
        "fragment_ids_not_in_manifest": extra,
        # A `converged` fragment records the last convergence; whether it is
        # still current is `select_segments.py --classify-only`'s answer, and
        # that path writes runs/ledger.json. Stated rather than implied.
        "staleness_checked": False,
        # And the fragment is read as "a JSON object with a string status",
        # NOT validated against ledger-fragment.schema.json -- which requires a
        # timestamp, and for a converged record a cache key, round count and
        # reviewed sha1. A hand-edited or half-written artifact the producer
        # would refuse is therefore counted here. Stated rather than implied,
        # for the same reason as the line above.
        "schema_validated": False,
    }
    return progress, None


def batch_progress(durable_root: Path, gate_segs, refused: int, enum):
    """The same census restricted to the segments THIS run's Step 1 gate
    selected -- the number an operator watching a live batch actually wants.

    `None` ONLY when the epoch records no `step1_gate_passed`, which is a
    different fact from a batch of zero units. In particular a gate that HAS
    fired but whose units have no fragment yet -- the ordinary state of a fresh
    run, since `runs/ledger.d/` does not exist until `ledger_update.py` writes
    the first fragment -- is `0/N` with every id counted missing, never `null`.
    Reporting the surface as unknown there would hide a number this script
    knows.
    """
    if not isinstance(gate_segs, list):
        return None
    frag_dir = durable_root / "runs" / "ledger.d"
    if _real_dir(frag_dir, durable_root):
        statuses, unreadable, missing = read_fragment_statuses(
            frag_dir, gate_segs, durable_root
        )
    else:
        # No fragment directory yet is the ordinary state of a fresh run, so
        # every dispatched id is simply missing -- 0/N, never an unknown.
        statuses, unreadable, missing = {}, 0, list(gate_segs)
    counts, unrecognized = census(statuses, enum)
    return {
        "dispatched": len(gate_segs),
        # Non-zero means the epoch's `step1_gate_passed.segs` held ids that are
        # not segment ids; those are excluded from `dispatched` and from the
        # census, so this is the difference against `recorded_dispatched_segs`.
        "unsafe_recorded_ids": refused,
        "recorded_fragment_status_counts": counts,
        "unrecognized_status": unrecognized,
        "unreadable_fragments": unreadable,
        "dispatched_ids_without_fragment": len(missing),
        # MAJOR 4 round 2: the two caveats travel with BOTH progress numbers,
        # because both come from the same unvalidated, staleness-unchecked read.
        # A caveat that rides only one of two sibling censuses reads as if the
        # other one were stronger.
        "staleness_checked": False,
        "schema_validated": False,
    }


def ps_command(pid: int):
    """`ps -ww -p PID -o command=`, or None when ps cannot answer.

    `-ww` because GNU procps otherwise truncates to an undefined width when
    stdout is not a terminal. The result is reported as TEXT and never as
    identity: POSIX permits truncating `args`, and a zombie prints `<defunct>`
    while `os.kill(pid, 0)` still succeeds -- which is exactly why liveness
    alone is not published as a conclusion.
    """
    try:
        proc = subprocess.run(
            ["ps", "-ww", "-p", str(pid), "-o", "command="],
            capture_output=True,
            text=True,
            timeout=10,
            # A FIXED minimal PATH rather than the inherited environment: this
            # is the only executable this script ever spawns, and inheriting
            # PATH would let a shim earlier on it be what actually runs. The
            # argv stays the literal "ps" so the test's AST allowlist can still
            # read it.
            env={"PATH": "/usr/bin:/bin"},
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    out = proc.stdout.strip()
    return out or None


def read_lock_diagnostic(durable_root: Path):
    """runs/.driver.lock's CONTENT, plus what `ps` says about the pid it names.

    Never the lease. `acquire_driver_lock()` calls this content diagnostic-only
    and writes it best-effort (its own write failure is caught and ignored), so
    an absent or malformed value means "no diagnostic", never "no driver".
    """
    lock_path = durable_root / "runs" / ".driver.lock"
    if not lock_path.is_file():
        return None, f"{lock_path} does not exist"
    text, reason = _read_text(lock_path, durable_root)
    if text is None:
        return None, f"{lock_path} is not readable ({reason})"
    if not text.strip():
        return None, f"{lock_path} is empty -- the driver's diagnostic write is best-effort"
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, RecursionError) as exc:
        return None, f"{lock_path} is not valid JSON ({exc})"
    pid = payload.get("pid") if isinstance(payload, dict) else None
    # `bool` is an `int` subclass, and a zero/negative pid means something else
    # entirely to kill(2) (0 = this process group, -1 = every process). The
    # upper bound is `pid_t`, a signed 32-bit int on every platform this runs
    # on: a larger value raises OverflowError from os.kill, which is NOT an
    # OSError -- uncaught it would print a traceback and no JSON line at all,
    # breaking the one-line stdout contract on a malformed input. Refused
    # rather than probed, because a pid that cannot be probed is not an
    # observation about anything.
    if (
        isinstance(pid, bool)
        or not isinstance(pid, int)
        or pid <= 0
        or pid > 2 ** 31 - 1
    ):
        return None, f"{lock_path} carries no usable positive integer 'pid'"

    try:
        os.kill(pid, 0)
        alive = True
    except ProcessLookupError:
        alive = False
    except PermissionError:
        # The pid exists and belongs to another user. Alive is the honest read.
        alive = True
    except (OSError, OverflowError, ValueError):
        alive = False

    command = ps_command(pid) if alive else None
    return (
        {
            "pid": pid,
            "started_at": payload.get("started_at"),
            "pid_alive": alive,
            "ps_command": command,
            "ps_names_driver_script": bool(command and "segment_dispatch_driver.py" in command),
            "ps_names_this_durable_root": bool(command and str(durable_root) in command),
        },
        None,
    )


# ---------------------------------------------------------------------------
# The journal
# ---------------------------------------------------------------------------


def read_journal(path: Path, root: Path):
    """Parse one driver journal into (epochs, malformed, lease_warning, reason).

    An EPOCH is the run of entries from one `driver_started` up to the next.
    `fresh_session_id()` has one-second resolution and `append_journal()` only
    ever appends, so two launches inside the same UTC second share one file --
    and then the FIRST run's `driver_exit` sits above the SECOND run's
    `driver_started`. Reading the file as one run would report the live batch as
    finished. Entries above the first `driver_started` are a prelude belonging to
    no epoch: `lock_self_test_failed` is written inside `acquire_driver_lock()`,
    before `run()` journals its start, so a journal's first line legitimately is
    not a start at all.
    """
    text, reason = _read_text(path, root)
    if text is None:
        return [], 0, False, reason

    epochs = []
    malformed = 0
    lease_warning = False
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except (json.JSONDecodeError, RecursionError):
            malformed += 1
            continue
        if not isinstance(entry, dict):
            malformed += 1
            continue
        if entry.get("type") == _LEASE_WARNING:
            lease_warning = True
        if entry.get("type") == _START:
            epochs.append([entry])
        elif epochs:
            epochs[-1].append(entry)
        # else: a prelude entry, belonging to no epoch, deliberately dropped
        # from the per-run view while still counted above for lease_warning.
    return epochs, malformed, lease_warning, None


def summarize_epoch(entries: list, now: datetime) -> dict:
    """The reported facts of ONE epoch. Every count is of ENTRIES the journal
    holds: `append_journal()` never aborts the driver on a write failure, so a
    lost entry lowers a recorded count without lowering the work. The names all
    say `recorded_`.
    """
    start = entries[0]
    exit_entry = None
    gate_entry = None
    started_by_disp = {}
    finished_disps = set()
    dispatch_started = 0
    dispatch_finished = 0

    for entry in entries:
        etype = entry.get("type")
        if etype == _EXIT:
            exit_entry = entry
        elif etype == _GATE_PASSED:
            gate_entry = entry
        elif etype == _DISPATCH_STARTED:
            dispatch_started += 1
            disp = entry.get("disp")
            if isinstance(disp, str):
                started_by_disp[disp] = entry
        elif etype == _DISPATCH_FINISHED:
            dispatch_finished += 1
            disp = entry.get("disp")
            if isinstance(disp, str):
                finished_disps.add(disp)

    in_flight = [
        {
            "seg": entry.get("seg"),
            "kind": entry.get("kind"),
            "round_label": entry.get("round_label"),
            "disp": disp,
            "started_at": entry.get("ts"),
            "age_sec": _age_sec(entry.get("ts"), now),
        }
        for disp, entry in started_by_disp.items()
        if disp not in finished_disps
    ]

    last = entries[-1]
    gate_segs = gate_entry.get("segs") if gate_entry is not None else None
    if not isinstance(gate_segs, list):
        gate_segs = None
        gate_segs_refused = 0
    else:
        # Validated with the SAME regex load_manifest_segs() applies, and for
        # the same reason: these ids are joined onto `runs/ledger.d/` to build a
        # path. The journal is written best-effort by the driver, so a truncated
        # or hand-edited entry can carry anything -- and `_within` alone would
        # still let `../manifest` name a real in-root file whose `status` would
        # then be counted as a segment's. Refused ids are COUNTED, not dropped
        # silently: `recorded_dispatched_segs` stays the number the journal
        # recorded, so the gap against `batch_progress.dispatched` is visible.
        gate_segs_recorded = len(gate_segs)
        gate_segs = [
            seg
            for seg in gate_segs
            if isinstance(seg, str) and _SEG_ID_RE.fullmatch(seg)
        ]
        gate_segs_refused = gate_segs_recorded - len(gate_segs)

    return {
        # Consumed by build_report() to scope the batch census, then dropped
        # from the payload: 59 ids is a wall of text, and the census is the
        # answer the list exists to produce.
        "_gate_segs": gate_segs,
        "_gate_segs_refused": gate_segs_refused,
        "recorded_start": start.get("ts"),
        "recorded_pid": start.get("pid"),
        "recorded_exit": (
            {
                "ts": exit_entry.get("ts"),
                "success": exit_entry.get("success"),
                "summary": exit_entry.get("summary"),
            }
            if exit_entry is not None
            else None
        ),
        "last_recorded_event": {
            "type": last.get("type"),
            "ts": last.get("ts"),
            "age_sec": _age_sec(last.get("ts"), now),
        },
        # null, never 0: an epoch that records no step1_gate_passed has not told
        # us how many units it selected, which is a different fact from zero.
        "recorded_dispatched_segs": (
            None if gate_segs is None else len(gate_segs) + gate_segs_refused
        ),
        "recorded_codex_dispatches": {
            "started": dispatch_started,
            "finished": dispatch_finished,
            "in_flight": in_flight,
        },
    }


def collect_runs(durable_root: Path, now: datetime):
    """Every journal under runs/*/driver_journal.jsonl, reduced to its LAST epoch.

    Returns (candidates, journals_found, journals_without_recorded_start,
    journals_unreadable). The last two are SEPARATE counts on purpose: a journal
    this script could not read at all is "could not establish", and folding it
    into "readable but records no start" would turn an unknown into an assertion
    about the driver.
    """
    runs_dir = durable_root / "runs"
    if not _real_dir(runs_dir, durable_root):
        return [], 0, 0, 0
    try:
        journals = sorted(runs_dir.glob("*/driver_journal.jsonl"))
    except OSError:
        return [], 0, 0, 0

    candidates = []
    without_start = 0
    unreadable = 0
    for journal in journals:
        epochs, malformed, lease_warning, reason = read_journal(journal, durable_root)
        if reason is not None:
            unreadable += 1
            continue
        if not epochs:
            without_start += 1
            continue
        record = summarize_epoch(epochs[-1], now)
        record.update(
            {
                "session_id": journal.parent.name,
                "journal": str(journal.relative_to(durable_root)),
                "epochs_in_journal": len(epochs),
                "lease_enforcement_warning": lease_warning,
                "journal_parse_complete": malformed == 0,
                "malformed_journal_lines": malformed,
            }
        )
        candidates.append(record)
    return candidates, len(journals), without_start, unreadable


def select_run(candidates: list, lock_diagnostic):
    """Pick the run to report, and SAY on what basis.

    A live diagnostic pid that names one of these epochs is the strongest link
    available, so it wins. Otherwise the greatest recorded start time -- which
    is NOT called "latest" or "newest" anywhere, because both the session id and
    the entry timestamp come from a non-monotonic wall clock: after a backward
    clock step the greatest recorded time is not the last invocation, and no
    artifact in this tree carries a durable launch order that would settle it.
    `journals_found` tells the operator how many others exist.
    """
    if not candidates:
        return None
    # BOTH conditions, not just liveness. The lock's content is written
    # best-effort -- its truncate/write failure is caught and ignored by the
    # driver -- so a stale pid can survive there while a newer run holds the
    # real lease. If that stale pid is then REUSED by any unrelated live
    # process, liveness alone would hand this selector an old epoch and it would
    # report the wrong invocation's exit, summary and last event. Requiring the
    # command text to name the driver script is the corroboration that rules
    # that out; without it the ordinary ordering wins and says so.
    if (
        lock_diagnostic is not None
        and lock_diagnostic["pid_alive"]
        and lock_diagnostic["ps_names_driver_script"]
    ):
        matches = [c for c in candidates if c["recorded_pid"] == lock_diagnostic["pid"]]
        if len(matches) == 1:
            chosen = matches[0]
            chosen["selected_by"] = "lock_diagnostic_pid"
            return chosen
    # `str()`, not the raw value: `recorded_start` is whatever the journal's
    # `driver_started` entry carried, and two journals holding a string and a
    # number would make this tuple comparison raise TypeError -- a traceback and
    # NO JSON line, which is the one output contract every caller depends on.
    # Same shape as the pid path's OverflowError guard: a malformed artifact
    # gets a worse ORDERING, never a crash.
    chosen = max(
        candidates,
        key=lambda c: (str(c["recorded_start"] or ""), str(c["session_id"])),
    )
    chosen["selected_by"] = "greatest_recorded_driver_started_ts"
    return chosen


def build_report(durable_root: Path) -> dict:
    now = datetime.now(timezone.utc)
    manifest_segs = load_manifest_segs(durable_root / "manifest.json", durable_root)
    # Read ONCE and handed to both censuses. Two reads of a file this script
    # does not lock could disagree, and two numbers zero-filled over different
    # status sets are not comparable -- which is the whole point of publishing
    # them side by side.
    enum, enum_source = fragment_status_enum(durable_root / "schemas", durable_root)
    progress, progress_reason = read_progress(
        durable_root, manifest_segs, enum, enum_source
    )
    lock_diagnostic, lock_reason = read_lock_diagnostic(durable_root)
    candidates, journals_found, without_start, unreadable_journals = collect_runs(
        durable_root, now
    )
    run = select_run(candidates, lock_diagnostic)

    if run is not None:
        run["pid_matches_lock_diagnostic"] = (
            None
            if lock_diagnostic is None
            else run["recorded_pid"] == lock_diagnostic["pid"]
        )
        # Popped, not read: `_gate_segs` is scratch for the census below, and
        # the selected run is the only candidate this payload publishes.
        run["batch_progress"] = batch_progress(
            durable_root, run.pop("_gate_segs"), run.pop("_gate_segs_refused"), enum
        )
        run_reason = None
    else:
        parts = []
        if without_start:
            parts.append(f"{without_start} with no recorded {_START} entry")
        if unreadable_journals:
            parts.append(f"{unreadable_journals} unreadable")
        run_reason = (
            f"{journals_found} journal(s) found under runs/: " + ", ".join(parts)
            if parts
            else "no driver journal found under runs/"
        )

    return {
        "success": True,
        "durable_root": str(durable_root),
        "generated_at": now.strftime(_TS_FORMAT),
        "units": {"total": len(manifest_segs)},
        "progress": progress,
        "progress_unavailable_reason": progress_reason,
        "lock_diagnostic": lock_diagnostic,
        "lock_diagnostic_unavailable_reason": lock_reason,
        "run": run,
        "run_unavailable_reason": run_reason,
        "journals_found": journals_found,
        "journals_without_recorded_start": without_start,
        "journals_unreadable": unreadable_journals,
    }


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only progress surface for a mass-translate batch. Takes no "
            "lock, writes nothing, and never runs select_segments.py."
        )
    )
    parser.add_argument(
        "--durable-root",
        default=None,
        metavar="PATH",
        help=(
            "The DATA root (manifest.json, segments/, runs/). Omit for this "
            "script's own self-anchored parent, the same convention every "
            "sibling here uses. There is no --plugin-root: this script shells "
            "out to no sibling script, so there is no second root to resolve."
        ),
    )
    return parser


def main():
    args = build_arg_parser().parse_args()

    if args.durable_root is not None:
        if not args.durable_root.strip():
            print("Error: --durable-root was given an empty value.", file=sys.stderr)
            sys.exit(2)
        durable_root = Path(args.durable_root).resolve()
    else:
        durable_root = DURABLE_ROOT

    if not durable_root.is_dir():
        print(f"Error: durable root is not a directory: {durable_root}", file=sys.stderr)
        sys.exit(2)

    try:
        report = build_report(durable_root)
    except StatusError as exc:
        print(dumps_line({"success": False, "error": exc.message}))
        sys.exit(1)

    print(dumps_line(report))
    sys.exit(0)


if __name__ == "__main__":
    main()
