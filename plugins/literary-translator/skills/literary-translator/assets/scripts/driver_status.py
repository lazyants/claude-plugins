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
would otherwise make. `tests/driver_status.test.py` guards each of those
structurally (an AST import allowlist plus an enumerated write/lock construct
set, every clause mutation-tested) and behaviourally (a run against a durable
root whose directories are 0555 and files 0444, with a private empty TMPDIR and
the plugin tree snapshotted). Those are regression GUARDS on this
implementation, not a proof about every possible one.

## Where each number comes from

`units.total` is the distinct `segments[].seg` of `manifest.json`. It is
DERIVED, never configured: both hand-rolled copies hard-coded their book's unit
count (`/ 74`, `/ 79`) and #765 proposed substituting it at scaffold time.

Progress is the per-segment fragments under `runs/ledger.d/`, intersected with
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


class StatusError(Exception):
    """No report is possible. Carries this script's exit code."""

    def __init__(self, message, exit_code=1):
        super().__init__(message)
        self.message = message
        self.exit_code = exit_code


# ---------------------------------------------------------------------------
# Small readers. Every one of them returns a value plus a REASON on failure --
# never a zero or an empty list that a caller could not tell from an absence.
# ---------------------------------------------------------------------------


def _read_text(path: Path):
    """Read `path` as UTF-8 text, or return (None, reason).

    `read_bytes` + a replacing decode rather than `read_text`: a journal being
    appended to right now can be truncated mid-codepoint, and a UnicodeDecodeError
    there would lose every well-formed line before it. The replacement character
    lands inside one line, that line fails to parse as JSON, and it is counted as
    malformed -- which is exactly what it is.
    """
    try:
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


def load_manifest_segs(manifest_path: Path) -> list:
    """The distinct `segments[].seg` of manifest.json, in manifest order.

    Refuses rather than reporting a zero, matching `load_candidate_segments()`
    in select_segments.py: an empty `segments[]` is a broken project, not a
    project with no work.
    """
    text, reason = _read_text(manifest_path)
    if text is None:
        raise StatusError(f"cannot read manifest.json ({reason})")
    try:
        manifest = json.loads(text)
    except json.JSONDecodeError as exc:
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


def fragment_status_enum(schemas_dir: Path):
    """The five statuses ledger_update.py may write, read from the schema that
    owns them rather than restated as a literal here -- a hand-copied member list
    is the restated-list-goes-stale trap this repo has been bitten by twice.
    Returns (statuses, source); `source` is "observed" when the schema cannot be
    read, and the caller then zero-fills nothing it did not see.
    """
    schema_path = schemas_dir / "ledger-fragment.schema.json"
    text, _reason = _read_text(schema_path)
    if text is None:
        return None, "observed"
    try:
        schema = json.loads(text)
    except json.JSONDecodeError:
        return None, "observed"
    enum = (
        schema.get("properties", {}).get("status", {}).get("enum")
        if isinstance(schema, dict)
        else None
    )
    if not isinstance(enum, list) or not all(isinstance(x, str) for x in enum) or not enum:
        return None, "observed"
    return list(enum), "schemas/ledger-fragment.schema.json"


def read_progress(durable_root: Path, manifest_segs: list, schemas_dir: Path):
    """The per-segment fragment census, intersected with the manifest.

    Returns (progress, reason). `reason` is non-None exactly when `progress` is
    None -- an absent `runs/ledger.d/` is an ABSENCE, and reporting it as five
    zeroes would be indistinguishable from a book nothing has run against.
    """
    frag_dir = durable_root / "runs" / "ledger.d"
    if not frag_dir.is_dir():
        return None, f"{frag_dir} does not exist -- no fragment has been written yet"
    try:
        entries = sorted(p.name for p in frag_dir.iterdir())
    except OSError as exc:
        return None, f"{frag_dir} is not readable ({exc})"

    statuses, enum_source = fragment_status_enum(schemas_dir)
    counts = {name: 0 for name in (statuses or [])}
    unrecognized = 0
    unreadable = 0

    manifest_set = set(manifest_segs)
    on_disk = {name[: -len(".json")] for name in entries if name.endswith(".json")}
    missing = [seg for seg in manifest_segs if seg not in on_disk]
    extra = sorted(name for name in on_disk if name not in manifest_set)

    for seg in manifest_segs:
        if seg not in on_disk:
            continue
        text, _reason = _read_text(frag_dir / f"{seg}.json")
        if text is None:
            unreadable += 1
            continue
        try:
            fragment = json.loads(text)
        except json.JSONDecodeError:
            unreadable += 1
            continue
        status = fragment.get("status") if isinstance(fragment, dict) else None
        if not isinstance(status, str):
            unreadable += 1
        elif statuses is not None and status not in counts:
            unrecognized += 1
        else:
            counts[status] = counts.get(status, 0) + 1

    progress = {
        "source": "runs/ledger.d",
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
    }
    return progress, None


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
    text, reason = _read_text(lock_path)
    if text is None:
        return None, f"{lock_path} is not readable ({reason})"
    if not text.strip():
        return None, f"{lock_path} is empty -- the driver's diagnostic write is best-effort"
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        return None, f"{lock_path} is not valid JSON ({exc})"
    if not isinstance(payload, dict) or not isinstance(payload.get("pid"), int):
        return None, f"{lock_path} carries no integer 'pid'"

    pid = payload["pid"]
    try:
        os.kill(pid, 0)
        alive = True
    except ProcessLookupError:
        alive = False
    except PermissionError:
        # The pid exists and belongs to another user. Alive is the honest read.
        alive = True
    except OSError:
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


def read_journal(path: Path):
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
    text, reason = _read_text(path)
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
        except json.JSONDecodeError:
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
    dispatched = gate_entry.get("segs") if gate_entry is not None else None

    return {
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
        "recorded_dispatched_segs": len(dispatched) if isinstance(dispatched, list) else None,
        "recorded_codex_dispatches": {
            "started": dispatch_started,
            "finished": dispatch_finished,
            "in_flight": in_flight,
        },
    }


def collect_runs(durable_root: Path, now: datetime):
    """Every journal under runs/*/driver_journal.jsonl, reduced to its LAST epoch.

    Returns (candidates, journals_found, journals_without_recorded_start).
    """
    runs_dir = durable_root / "runs"
    if not runs_dir.is_dir():
        return [], 0, 0
    try:
        journals = sorted(runs_dir.glob("*/driver_journal.jsonl"))
    except OSError:
        return [], 0, 0

    candidates = []
    without_start = 0
    for journal in journals:
        epochs, malformed, lease_warning, reason = read_journal(journal)
        if reason is not None or not epochs:
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
    return candidates, len(journals), without_start


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
    if lock_diagnostic is not None and lock_diagnostic["pid_alive"]:
        matches = [c for c in candidates if c["recorded_pid"] == lock_diagnostic["pid"]]
        if len(matches) == 1:
            chosen = matches[0]
            chosen["selected_by"] = "lock_diagnostic_pid"
            return chosen
    chosen = max(candidates, key=lambda c: (c["recorded_start"] or "", c["session_id"]))
    chosen["selected_by"] = "greatest_recorded_driver_started_ts"
    return chosen


def build_report(durable_root: Path) -> dict:
    now = datetime.now(timezone.utc)
    manifest_segs = load_manifest_segs(durable_root / "manifest.json")
    progress, progress_reason = read_progress(
        durable_root, manifest_segs, durable_root / "schemas"
    )
    lock_diagnostic, lock_reason = read_lock_diagnostic(durable_root)
    candidates, journals_found, without_start = collect_runs(durable_root, now)
    run = select_run(candidates, lock_diagnostic)

    if run is not None:
        run["pid_matches_lock_diagnostic"] = (
            None
            if lock_diagnostic is None
            else run["recorded_pid"] == lock_diagnostic["pid"]
        )
        run_reason = None
    elif journals_found:
        run_reason = (
            f"{journals_found} journal(s) found under runs/, none with a recorded "
            f"{_START} entry"
        )
    else:
        run_reason = "no driver journal found under runs/"

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
        sys.exit(exc.exit_code)

    print(dumps_line(report))
    sys.exit(0)


if __name__ == "__main__":
    main()
