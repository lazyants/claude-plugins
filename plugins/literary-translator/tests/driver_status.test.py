"""tests/driver_status.test.py -- #765 the read-only progress surface.

## Scope

`driver_status.py` reports what the durable root's artifacts RECORD, and
deliberately publishes no lifecycle verdict. These tests therefore split in two:

  * the READING half -- journal epochs, run selection and its published basis,
    the fragment census, and the null-not-zero contract every census field
    carries.
  * the READ-ONLY half -- a structural guard over the shipped file's AST and a
    behavioural run against a read-only tree. Both are named GUARDS, not proofs:
    they bite on this implementation, and every clause of the structural one is
    mutation-tested so a green result is not a vacuous one.

## Fixture strategy

Every test drives the SHIPPED script as a subprocess against a staged
`tmp_path/durable_root`, with `driver_status.py` and its `json_stdout.py`
sibling copied into `durable_root/scripts/` -- the same `make_durable_root`
pattern `select_segments.test.py` and `segment_dispatch_driver.test.py` use, so
the script's own `Path(__file__)` self-anchoring resolves against the fixture.
No test stages `segment_dispatch_driver.py` itself: this script shells out to no
sibling and reads only artifacts, so a real driver would add nothing but time.
"""
import ast
import contextlib
import fcntl
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_SRC_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets" / "scripts"
SCHEMAS_SRC_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets" / "schemas"
STATUS_SRC = SCRIPTS_SRC_DIR / "driver_status.py"
JSON_STDOUT_SRC = SCRIPTS_SRC_DIR / "json_stdout.py"
FRAGMENT_SCHEMA_SRC = SCHEMAS_SRC_DIR / "ledger-fragment.schema.json"

TS = "2026-08-26T09:30:10Z"

# The FIFTEEN fields ledger-record-base.schema.json requires of a cache key,
# taken from a real fragment. A fixture thinner than the producer's own output
# blesses a shape `ledger_update.py` would refuse, and then proves nothing about
# real data -- which is what a one-field stand-in did here before.
_CACHE_KEY = {
    name: "0" * 40
    for name in (
        "agent_config_hash", "derivation_bundle_hash", "input_sha1",
        "note_map_hash", "particle_config_hash", "plugin_bundle_hash",
        "profile_semantics_hash", "prompt_hash", "schema_hash",
        "source_extraction_hash", "source_input_hash", "style_contract_hash",
        "used_terms_hash", "verse_map_hash",
    )
}
_CACHE_KEY["pipeline_version"] = "v1"


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def make_durable_root(tmp_path, segs=("seg01", "seg02"), with_schema=True):
    root = tmp_path / "durable_root"
    (root / "scripts").mkdir(parents=True)
    (root / "segments").mkdir()
    (root / "runs").mkdir()
    (root / "schemas").mkdir()
    shutil.copy2(STATUS_SRC, root / "scripts" / "driver_status.py")
    shutil.copy2(JSON_STDOUT_SRC, root / "scripts" / "json_stdout.py")
    if with_schema:
        shutil.copy2(FRAGMENT_SCHEMA_SRC, root / "schemas" / "ledger-fragment.schema.json")
    write_manifest(root, segs)
    return root


def write_manifest(root, segs):
    (root / "manifest.json").write_text(
        json.dumps({"segments": [{"seg": s} for s in segs]}), encoding="utf-8"
    )


def write_fragments(root, statuses: dict):
    """Fragments in the shape `ledger_update.py` really writes -- every field a
    converged record carries, not just `status`. A fixture that hand-builds a
    thinner artifact than the producer would blesses a shape the producer would
    refuse, and then the test proves nothing about real data."""
    frag_dir = root / "runs" / "ledger.d"
    frag_dir.mkdir(parents=True, exist_ok=True)
    for seg, status in statuses.items():
        (frag_dir / f"{seg}.json").write_text(
            json.dumps(
                {
                    "status": status,
                    "timestamp": "2026-08-26T09:30:00Z",
                    "rounds": 2,
                    "n_blocks": 38,
                    "n_footnotes": 0,
                    "n_verses": 0,
                    "reviewed_draft_sha1": "e894d71854e86fcee454d3037208b0115b5b4e21",
                    "cache_key": _CACHE_KEY,
                }
            ),
            encoding="utf-8",
        )
    return frag_dir


def write_journal(root, session_id, entries, trailing_partial=None):
    """One journal file. `entries` are dicts written one per line; when
    `trailing_partial` is given it is appended verbatim WITHOUT a newline, which
    is what a reader sees while the driver is mid-write."""
    run_dir = root / "runs" / session_id
    run_dir.mkdir(parents=True, exist_ok=True)
    body = "".join(json.dumps(e) + "\n" for e in entries)
    if trailing_partial is not None:
        body += trailing_partial
    (run_dir / "driver_journal.jsonl").write_text(body, encoding="utf-8")
    return run_dir / "driver_journal.jsonl"


def write_lock(root, pid=None, started_at=TS, raw=None):
    lock = root / "runs" / ".driver.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    if raw is not None:
        lock.write_text(raw, encoding="utf-8")
    else:
        lock.write_text(
            json.dumps({"pid": pid, "started_at": started_at}) + "\n", encoding="utf-8"
        )
    return lock


def started(ts=TS, pid=4242):
    return {"type": "driver_started", "pid": pid, "ts": ts}


def exited(ts="2026-08-26T10:00:00Z", summary=None):
    return {
        "type": "driver_exit",
        "success": True,
        "ts": ts,
        "summary": summary if summary is not None else {"converged": ["seg01"]},
    }


def gate_passed(segs, ts=TS):
    return {"type": "step1_gate_passed", "segs": segs, "ts": ts}


def lock_self_test_failed(ts="2026-08-26T09:30:09Z"):
    """The entry acquire_driver_lock() writes when its flock self-test fails --
    journalled BEFORE run() records driver_started, which is why a journal's
    first line legitimately is not a start."""
    return {"type": "lock_self_test_failed", "lock_path": "runs/.driver.lock", "ts": ts}


@contextlib.contextmanager
def sleeping_child(*extra_argv):
    """A live pid for the lock diagnostic to name, reaped on the way out.

    `extra_argv` lands in the child's command line, which is what `ps` reports:
    passing the driver script's path is how a test makes `ps_names_driver_script`
    true without paying for a real driver."""
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)", *extra_argv]
    )
    try:
        yield child
    finally:
        child.terminate()
        child.wait()


@contextlib.contextmanager
def read_only_tree(root: Path):
    """Directories 0555 and regular files 0444 for the body, restored after.

    Deepest-first, so a parent is still writable while its children are being
    locked down; the restore runs whatever the body did, because a tree left
    read-only would break tmp_path teardown."""
    paths = sorted(root.rglob("*"), reverse=True)
    try:
        for path in paths:
            path.chmod(0o555 if path.is_dir() else 0o444)
        root.chmod(0o555)
        yield
    finally:
        root.chmod(0o755)
        for path in sorted(root.rglob("*")):
            path.chmod(0o755 if path.is_dir() else 0o644)


def _walk(node, prefix=""):
    """Every (key, value) leaf of the payload, so an assertion about the report
    can name WHERE it failed instead of grepping a JSON blob -- a substring test
    over the whole document reads `codex_dispatch_finished` as a lifecycle
    verdict."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _walk(value, f"{prefix}.{key}" if prefix else key)
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield from _walk(value, f"{prefix}[{i}]")
    else:
        yield prefix.rsplit(".", 1)[-1], node


def run_status(root, args=(), env=None, expect_code=0, script=None):
    cmd = [sys.executable, str(script or (root / "scripts" / "driver_status.py"))] + list(args)
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=120)
    assert proc.returncode == expect_code, (
        f"exit {proc.returncode} != {expect_code}\nstdout={proc.stdout}\nstderr={proc.stderr}"
    )
    if proc.stdout.strip():
        # The house contract: exactly one physical line of JSON on stdout.
        assert len(proc.stdout.rstrip("\n").split("\n")) == 1, proc.stdout
        return json.loads(proc.stdout)
    return None


# ---------------------------------------------------------------------------
# Journal reading and run selection
# ---------------------------------------------------------------------------


def test_recorded_exit_is_reported_with_its_summary_verbatim(tmp_path):
    root = make_durable_root(tmp_path)
    summary = {"converged": ["seg01"], "needs_fix": [{"seg": "seg02", "round_label": "2"}],
               "failed": []}
    write_journal(root, "20260826T093010Z", [started(), exited(summary=summary)])
    report = run_status(root)
    assert report["run"]["recorded_exit"]["summary"] == summary
    assert report["run"]["last_recorded_event"]["type"] == "driver_exit"


def test_no_recorded_exit_does_not_claim_the_run_died(tmp_path):
    """The whole point of the verdict-free design: a journal with no exit entry
    is reported as having no exit entry, and NOTHING in the payload says the run
    is dead, finished or running. `append_journal()` is best-effort, so a missing
    entry is not evidence about the process."""
    root = make_durable_root(tmp_path)
    write_journal(root, "20260826T093010Z", [started()])
    report = run_status(root)
    assert report["run"]["recorded_exit"] is None
    for key, value in _walk(report):
        assert key != "state", f"payload grew a lifecycle field: {key}"
        assert value not in ("died", "dead", "finished", "running", "crashed"), (
            f"payload leaked a lifecycle verdict at {key}: {value!r}"
        )


def test_same_second_epoch_collision_reports_only_the_last_epoch(tmp_path):
    """Two launches inside one UTC second share a journal file, so the FIRST
    run's driver_exit sits above the SECOND run's driver_started. Reading the
    file as one run would report the live batch as finished."""
    root = make_durable_root(tmp_path)
    write_journal(
        root,
        "20260826T093010Z",
        [
            started(pid=1111),
            exited(ts="2026-08-26T09:30:10Z", summary={"converged": ["seg01"]}),
            started(ts="2026-08-26T09:30:10Z", pid=2222),
            gate_passed(["seg02"], ts="2026-08-26T09:30:11Z"),
        ],
    )
    report = run_status(root)
    run = report["run"]
    assert run["epochs_in_journal"] == 2
    assert run["recorded_pid"] == 2222
    assert run["recorded_exit"] is None, "the FIRST epoch's exit must not be reported"


def test_prelude_entry_before_the_first_start_is_not_an_epoch(tmp_path):
    """`lock_self_test_failed` is written inside acquire_driver_lock(), before
    run() journals driver_started -- so a journal's first line legitimately is
    not a start. It is surfaced as a warning because it means the lease is not
    enforced on this filesystem."""
    root = make_durable_root(tmp_path)
    write_journal(
        root, "20260826T093010Z", [lock_self_test_failed(), started(), exited()]
    )
    report = run_status(root)
    assert report["run"]["epochs_in_journal"] == 1
    assert report["run"]["lease_enforcement_warning"] is True
    assert report["run"]["recorded_pid"] == 4242


def test_journal_without_a_recorded_start_is_not_a_run(tmp_path):
    root = make_durable_root(tmp_path)
    write_journal(root, "20260826T093010Z", [lock_self_test_failed()])
    report = run_status(root)
    assert report["run"] is None
    assert report["journals_found"] == 1
    assert report["journals_without_recorded_start"] == 1
    assert "no recorded driver_started" in report["run_unavailable_reason"]
    assert report["journals_unreadable"] == 0


def test_no_journal_at_all_is_an_absence_with_a_reason(tmp_path):
    root = make_durable_root(tmp_path)
    report = run_status(root)
    assert report["run"] is None
    assert report["journals_found"] == 0
    assert report["run_unavailable_reason"] == "no driver journal found under runs/"
    assert report["units"]["total"] == 2, "unit census still reported with no run"


def test_selection_prefers_the_epoch_the_live_lock_pid_names(tmp_path):
    """A live diagnostic pid naming one epoch is the strongest link the artifacts
    carry, so it wins over the greater recorded start time -- and the basis is
    published either way."""
    root = make_durable_root(tmp_path)
    with sleeping_child(str(root / "scripts" / "segment_dispatch_driver.py")) as child:
        write_journal(root, "20260826T090000Z",
                      [started(ts="2026-08-26T09:00:00Z", pid=child.pid)])
        write_journal(root, "20260826T093010Z",
                      [started(ts="2026-08-26T09:30:10Z", pid=999999), exited()])
        write_lock(root, child.pid)
        report = run_status(root)
        assert report["run"]["selected_by"] == "lock_diagnostic_pid"
        assert report["run"]["session_id"] == "20260826T090000Z"
        assert report["run"]["pid_matches_lock_diagnostic"] is True
        assert report["lock_diagnostic"]["pid_alive"] is True
        assert report["lock_diagnostic"]["ps_names_driver_script"] is True


def test_backward_clock_step_selects_by_recorded_time_and_says_so(tmp_path):
    """Both the session id and the entry timestamp come from a non-monotonic wall
    clock, so after a backward clock step the greatest recorded start is NOT the
    last invocation. The payload never calls it latest or newest; it names the
    ordering it actually used, and `journals_found` says how many others exist."""
    root = make_durable_root(tmp_path)
    write_journal(root, "20260826T120000Z", [started(ts="2026-08-26T12:00:00Z", pid=1),
                                             exited(ts="2026-08-26T12:05:00Z")])
    write_journal(root, "20260826T110000Z", [started(ts="2026-08-26T11:00:00Z", pid=2)])
    report = run_status(root)
    assert report["run"]["selected_by"] == "greatest_recorded_driver_started_ts"
    assert report["run"]["session_id"] == "20260826T120000Z"
    assert report["journals_found"] == 2
    blob = json.dumps(report)
    assert "latest" not in blob and "newest" not in blob


def test_pid_matches_lock_diagnostic_is_false_when_the_live_pid_names_no_epoch(tmp_path):
    root = make_durable_root(tmp_path)
    with sleeping_child() as child:
        write_journal(root, "20260826T093010Z", [started(pid=999999)])
        write_lock(root, child.pid)
        report = run_status(root)
        assert report["run"]["selected_by"] == "greatest_recorded_driver_started_ts"
        assert report["run"]["pid_matches_lock_diagnostic"] is False
        assert report["lock_diagnostic"]["pid_alive"] is True
        assert report["lock_diagnostic"]["ps_names_driver_script"] is False


def test_a_stripped_caller_path_does_not_break_the_ps_probe(tmp_path):
    """The `ps` spawn pins its own minimal PATH instead of inheriting the
    caller's, so that a shim earlier on the inherited PATH cannot be what runs.
    The observable consequence: stripping PATH entirely does not degrade the
    probe -- which is the inverse of what an inheriting implementation does."""
    root = make_durable_root(tmp_path)
    with sleeping_child(str(root / "scripts" / "segment_dispatch_driver.py")) as child:
        write_journal(root, "20260826T093010Z", [started(pid=child.pid)])
        write_lock(root, child.pid)
        report = run_status(root, env=dict(os.environ, PATH=""))
        assert report["lock_diagnostic"]["pid_alive"] is True
        assert report["lock_diagnostic"]["ps_command"] is not None
        assert report["lock_diagnostic"]["ps_names_driver_script"] is True


def test_a_zombie_pid_passes_the_kill_probe_but_ps_says_defunct(tmp_path):
    """Measured: os.kill(pid, 0) SUCCEEDS for an unreaped child while `ps` prints
    `<defunct>`. This is why liveness alone is never published as a conclusion --
    the ps text is what distinguishes the two."""
    root = make_durable_root(tmp_path)
    with sleeping_child(str(root / "scripts" / "segment_dispatch_driver.py")) as child:
        child.kill()
        for _ in range(50):
            time.sleep(0.1)
            out = subprocess.run(["ps", "-ww", "-p", str(child.pid), "-o", "command="],
                                 capture_output=True, text=True).stdout
            if "defunct" in out:
                break
        else:
            pytest.skip("could not observe a zombie on this platform")
        write_journal(root, "20260826T093010Z", [started(pid=child.pid)])
        write_lock(root, child.pid)
        report = run_status(root)
        assert report["lock_diagnostic"]["pid_alive"] is True
        assert report["lock_diagnostic"]["ps_names_driver_script"] is False


def test_mid_write_journal_counts_the_partial_line_and_keeps_the_rest(tmp_path):
    root = make_durable_root(tmp_path)
    write_journal(
        root,
        "20260826T093010Z",
        [started(), gate_passed(["seg01", "seg02"])],
        trailing_partial='{"type": "codex_dispatch_start',
    )
    report = run_status(root)
    assert report["run"]["malformed_journal_lines"] == 1
    assert report["run"]["journal_parse_complete"] is False
    assert report["run"]["recorded_dispatched_segs"] == 2


def test_in_flight_is_started_minus_finished_paired_on_disp(tmp_path):
    root = make_durable_root(tmp_path)
    entries = [started()]
    for i, disp in enumerate(("d1", "d2", "d3")):
        entries.append({"type": "codex_dispatch_started", "seg": f"seg0{i+1}",
                        "kind": "review", "round_label": str(i + 1), "disp": disp,
                        "ts": "2026-08-26T09:31:0%d" % i})
    entries.append({"type": "codex_dispatch_finished", "seg": "seg01", "kind": "review",
                    "round_label": "1", "disp": "d1", "ts": "2026-08-26T09:32:00Z"})
    write_journal(root, "20260826T093010Z", entries)
    report = run_status(root)
    dispatches = report["run"]["recorded_codex_dispatches"]
    assert dispatches["started"] == 3 and dispatches["finished"] == 1
    assert sorted(x["disp"] for x in dispatches["in_flight"]) == ["d2", "d3"]
    assert {x["kind"] for x in dispatches["in_flight"]} == {"review"}


def test_epoch_without_a_gate_entry_reports_null_not_zero(tmp_path):
    root = make_durable_root(tmp_path)
    write_journal(root, "20260826T093010Z", [started()])
    report = run_status(root)
    assert report["run"]["recorded_dispatched_segs"] is None


# ---------------------------------------------------------------------------
# The unit and fragment census
# ---------------------------------------------------------------------------


def test_unit_total_is_distinct_manifest_ids(tmp_path):
    root = make_durable_root(tmp_path)
    write_manifest(root, ["seg01", "seg02", "seg01", "FRONTBACK:fm01"])
    report = run_status(root)
    assert report["units"]["total"] == 3


def test_all_five_schema_statuses_are_zero_filled_from_the_schema(tmp_path):
    root = make_durable_root(tmp_path)
    write_fragments(root, {"seg01": "converged", "seg02": "in_progress"})
    report = run_status(root)
    progress = report["progress"]
    assert progress["status_enum_source"] == "schemas/ledger-fragment.schema.json"
    assert progress["recorded_fragment_status_counts"] == {
        "pending": 0, "in_progress": 1, "converged": 1, "non_converged": 0, "blocked": 0
    }
    assert progress["staleness_checked"] is False


def test_non_converged_is_counted_as_itself_not_folded_into_in_progress(tmp_path):
    """`segment_dispatch_driver.py` writes `non_converged` on the ordinary cap
    path, so a two-bucket census would call terminal work in progress."""
    root = make_durable_root(tmp_path)
    write_fragments(root, {"seg01": "non_converged", "seg02": "blocked"})
    counts = run_status(root)["progress"]["recorded_fragment_status_counts"]
    assert counts["non_converged"] == 1
    assert counts["blocked"] == 1
    assert counts["in_progress"] == 0


def test_absent_schema_falls_back_to_observed_statuses(tmp_path):
    root = make_durable_root(tmp_path, with_schema=False)
    write_fragments(root, {"seg01": "converged", "seg02": "converged"})
    progress = run_status(root)["progress"]
    assert progress["status_enum_source"] == "observed"
    assert progress["recorded_fragment_status_counts"]["converged"] == 2


def test_status_outside_the_enum_is_counted_not_dropped(tmp_path):
    root = make_durable_root(tmp_path)
    write_fragments(root, {"seg01": "converged", "seg02": "teleported"})
    progress = run_status(root)["progress"]
    assert progress["unrecognized_status"] == 1
    assert progress["recorded_fragment_status_counts"]["converged"] == 1


def test_unreadable_fragment_is_counted_not_silently_skipped(tmp_path):
    root = make_durable_root(tmp_path)
    frag_dir = write_fragments(root, {"seg01": "converged"})
    (frag_dir / "seg02.json").write_text("{not json", encoding="utf-8")
    progress = run_status(root)["progress"]
    assert progress["unreadable_fragments"] == 1
    assert progress["manifest_ids_without_fragment"] == 0


def test_manifest_and_fragment_sets_are_reconciled_both_directions(tmp_path):
    """The bare merge takes EVERY fragment with no manifest filter, so a total
    read off runs/ledger.json can count obsolete ids and miss present ones."""
    root = make_durable_root(tmp_path, segs=("seg01", "seg02", "seg03"))
    write_fragments(root, {"seg01": "converged", "seg99": "converged"})
    progress = run_status(root)["progress"]
    assert progress["manifest_ids_without_fragment"] == 2
    assert progress["fragment_ids_not_in_manifest"] == ["seg99"]
    assert progress["recorded_fragment_status_counts"]["converged"] == 1


def test_absent_fragment_dir_is_null_progress_not_zero_counts(tmp_path):
    root = make_durable_root(tmp_path)
    report = run_status(root)
    assert report["progress"] is None
    assert "is not a real directory" in report["progress_unavailable_reason"]


def test_lock_absent_empty_or_unparseable_is_null_with_a_reason(tmp_path):
    root = make_durable_root(tmp_path)
    report = run_status(root)
    assert report["lock_diagnostic"] is None
    assert "does not exist" in report["lock_diagnostic_unavailable_reason"]

    write_lock(root, raw="")
    report = run_status(root)
    assert report["lock_diagnostic"] is None
    assert "is empty" in report["lock_diagnostic_unavailable_reason"]

    write_lock(root, raw="{oops")
    report = run_status(root)
    assert report["lock_diagnostic"] is None
    assert "not valid JSON" in report["lock_diagnostic_unavailable_reason"]


@pytest.mark.parametrize(
    "body,needle",
    [
        ('{"segments": []}', "empty 'segments' array"),
        ('{"segments": [{"seg": "../etc"}]}', "unsafe segment id"),
        ('{"segments": [{"seg": 3}]}', "malformed segments[] entry"),
        ("{not json", "not valid JSON"),
        ('{"nope": 1}', "no 'segments' array"),
    ],
)
def test_manifest_that_cannot_be_read_exits_1_with_a_reason(tmp_path, body, needle):
    root = make_durable_root(tmp_path)
    (root / "manifest.json").write_text(body, encoding="utf-8")
    report = run_status(root, expect_code=1)
    assert report["success"] is False
    assert needle in report["error"]


def test_missing_manifest_exits_1(tmp_path):
    root = make_durable_root(tmp_path)
    (root / "manifest.json").unlink()
    report = run_status(root, expect_code=1)
    assert report["success"] is False


def test_usage_errors_exit_2(tmp_path):
    root = make_durable_root(tmp_path)
    run_status(root, args=["--durable-root", ""], expect_code=2)
    run_status(root, args=["--durable-root", str(tmp_path / "nope")], expect_code=2)


def test_durable_root_flag_targets_another_tree(tmp_path):
    """The plugin-path invocation: the script runs from one tree and reports on
    another, which is how an orchestrating session calls it."""
    root = make_durable_root(tmp_path)
    write_fragments(root, {"seg01": "converged", "seg02": "in_progress"})
    plugin_copy = tmp_path / "plugin" / "assets" / "scripts"
    plugin_copy.mkdir(parents=True)
    shutil.copy2(STATUS_SRC, plugin_copy / "driver_status.py")
    shutil.copy2(JSON_STDOUT_SRC, plugin_copy / "json_stdout.py")
    report = run_status(
        root, args=["--durable-root", str(root)], script=plugin_copy / "driver_status.py"
    )
    assert report["durable_root"] == str(root)
    assert report["progress"]["recorded_fragment_status_counts"]["converged"] == 1


# ---------------------------------------------------------------------------
# Read-only GUARDS. Not proofs: they bite on this implementation, and every
# clause of the structural one is mutation-tested so a green is not a vacuous one.
# ---------------------------------------------------------------------------

ALLOWED_IMPORTS = {
    "sys", "argparse", "importlib.util", "json", "os", "re", "subprocess",
    "datetime", "pathlib",
}

# Builtins that write, execute, or import dynamically.
FORBIDDEN_NAMES = {"open", "eval", "exec", "__import__", "compile", "input"}

# Attribute names that are unambiguously a write or a lock whatever they hang
# off -- no receiver in the standard library spells these harmlessly.
FORBIDDEN_ATTRS = {
    "flock", "lockf", "write_text", "write_bytes", "mkdir", "makedirs", "touch",
    "unlink", "rmtree", "rmdir", "import_module", "system", "popen",
    # Metadata is a WRITE. `chmod` and `utime` change nothing a naive
    # content-only snapshot would notice, which is exactly why they belong in
    # the enumerated set rather than being left to the snapshot to catch.
    "chmod", "lchmod", "chown", "utime", "chflags", "lchflags",
    "symlink_to", "hardlink_to",
    # `Path.open` is the same capability as the builtin under another name.
    "open",
}

# The MODULE-QUALIFIED half. `replace`, `remove` and `rename` are deliberately
# NOT in the bare set above: `str.replace`, `list.remove` and
# `datetime.replace` are ordinary reads, and a guard that fires on those is a
# false RED that gets deleted rather than obeyed. Qualifying them by receiver
# keeps the bite and drops the noise.
FORBIDDEN_QUALIFIED = {
    ("os", "open"), ("os", "replace"), ("os", "remove"), ("os", "rename"),
    ("os", "write"), ("os", "truncate"), ("os", "ftruncate"), ("os", "system"),
    ("os", "popen"), ("os", "mkdir"), ("os", "makedirs"), ("os", "unlink"),
    ("os", "chmod"), ("os", "chown"), ("os", "utime"), ("os", "link"),
    ("os", "chflags"), ("os", "lchflags"),
    ("os", "symlink"), ("os", "rmdir"), ("os", "removedirs"), ("os", "mknod"),
    ("os", "mkfifo"), ("os", "fdopen"),
    ("importlib", "import_module"),
}

# The import ALLOWLIST above carries the other half of this guard: a construct
# that is not reachable from an allowlisted module cannot be spelled at all, so
# the enumerated sets only have to cover what `os`, `pathlib` and the builtins
# can do.


def _forbidden_nodes(source: str):
    tree = ast.parse(source)
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name not in ALLOWED_IMPORTS:
                    hits.append(f"import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module not in ALLOWED_IMPORTS:
                hits.append(f"from {module} import ...")
        elif isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
            hits.append(f"name {node.id}")
        elif isinstance(node, ast.Attribute):
            if node.attr in FORBIDDEN_ATTRS:
                hits.append(f"attribute .{node.attr}")
            elif (
                isinstance(node.value, ast.Name)
                and (node.value.id, node.attr) in FORBIDDEN_QUALIFIED
            ):
                hits.append(f"call {node.value.id}.{node.attr}")
    return hits


def _subprocess_argv_heads(source: str):
    """Every literal list handed to a subprocess.* call, reduced to its argv[0]."""
    heads = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name)
                and func.value.id == "subprocess"):
            continue
        if not node.args or not isinstance(node.args[0], ast.List):
            heads.append(None)  # a non-literal argv is itself a finding
            continue
        first = node.args[0].elts[0] if node.args[0].elts else None
        heads.append(first.value if isinstance(first, ast.Constant) else None)
    return heads


def _imported_modules(source: str):
    tree = ast.parse(source)
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.add((node.module or "").split(".")[0])
    return names


def _string_constants(source: str):
    return {
        node.value
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }


def test_shipped_script_contains_no_write_or_lock_construct():
    source = STATUS_SRC.read_text(encoding="utf-8")
    assert _forbidden_nodes(source) == []
    assert _subprocess_argv_heads(source) == ["ps"]
    # Over the AST, not the text: the module docstring NAMES fcntl to explain
    # why it is absent, and a substring test would read its own documentation
    # as the defect.
    assert "fcntl" not in _imported_modules(source)
    assert "fcntl" not in _string_constants(source), "no dynamic import by name either"
    assert "sys.dont_write_bytecode = True" in source


@pytest.mark.parametrize(
    "mutation",
    [
        "import fcntl\n",
        "def _m(p):\n    return os.open(p, os.O_RDWR)\n",
        "def _m(p):\n    return Path(p).write_text('x')\n",
        "def _m(fd):\n    return getattr(__import__('fcntl'), 'flock')(fd, 2)\n",
        "import tempfile\n",
        "def _m():\n    return os.system('touch x')\n",
        "def _m(p):\n    return open(p, 'w')\n",
        "def _m(p):\n    return shutil.rmtree(p)\n",
        "def _m(p):\n    return Path(p).chmod(0o777)\n",
        "def _m(p):\n    return os.chmod(p, 0o777)\n",
        "def _m(p):\n    return os.utime(p, None)\n",
        "def _m(p):\n    return Path(p).open('w')\n",
        "def _m(p):\n    return os.rename(p, p)\n",
        "def _m(p):\n    return os.chflags(p, 1)\n",
        "def _m(p):\n    return Path(p).chmod(0o400)\n",
    ],
)
def test_the_write_and_lock_guard_bites(mutation):
    """Every clause of the guard above, proven RED by a mutation only that clause
    refuses. A guard whose mutations all pass is measuring nothing."""
    source = STATUS_SRC.read_text(encoding="utf-8") + "\n\n" + mutation
    hits = _forbidden_nodes(source)
    assert hits, f"mutation slipped past the guard: {mutation!r}"


def test_the_subprocess_allowlist_bites():
    source = STATUS_SRC.read_text(encoding="utf-8") + (
        "\n\ndef _m():\n    return subprocess.run(['rm', '-rf', '/tmp/x'])\n"
    )
    assert _subprocess_argv_heads(source) != ["ps"]


def _snapshot(root: Path):
    """Content AND metadata, for directories and for the root itself.

    An earlier version stored `None` for every directory and skipped the root
    entirely, so `durable_root.chmod(0o777)` -- a real mutation of the tree this
    script promises not to touch -- left the snapshot identical. Mode and mtime
    are part of what "mutates nothing" means."""
    entries = {}
    def metadata(stat):
        # st_flags is macOS/BSD-only and is the one mutator `os.chflags` moves
        # without touching mode, mtime or a single byte of content -- the exact
        # shape of write a content-only snapshot was already shown to miss.
        return (stat.st_mode, stat.st_mtime_ns, getattr(stat, "st_flags", None))

    root_stat = root.lstat()
    entries["."] = (None,) + metadata(root_stat)
    for path in sorted(root.rglob("*")):
        stat = path.lstat()
        key = str(path.relative_to(root)) + ("/" if path.is_dir() else "")
        content = None if path.is_dir() else path.read_bytes()
        entries[key] = (content,) + metadata(stat)
    return entries


def test_a_full_run_mutates_nothing_under_the_durable_root(tmp_path):
    root = make_durable_root(tmp_path)
    write_fragments(root, {"seg01": "converged", "seg02": "in_progress"})
    write_journal(root, "20260826T093010Z", [started(), exited()])
    write_lock(root, os.getpid())

    before = _snapshot(root)
    # A loop that enumerates nothing prints exactly what a passing one prints.
    assert len(before) >= 12, f"snapshot enumerated only {len(before)} entries"
    run_status(root)
    after = _snapshot(root)

    assert after == before
    assert not (root / "scripts" / "__pycache__").exists(), "the sibling import left a .pyc"
    assert not (root / "runs" / "ledger.json").exists(), "something materialized the ledger"


def test_a_run_against_a_read_only_tree_still_reports(tmp_path):
    """Directories 0555 AND regular files 0444, a private empty TMPDIR, and the
    plugin copy snapshotted -- so a write anywhere this script could reach either
    fails outright or shows up."""
    if os.geteuid() == 0:
        pytest.skip("root ignores the permission bits this guard relies on")
    root = make_durable_root(tmp_path)
    write_fragments(root, {"seg01": "converged"})
    write_journal(root, "20260826T093010Z", [started(), exited()])
    tmpdir = tmp_path / "private_tmp"
    tmpdir.mkdir()

    with read_only_tree(root):
        report = run_status(root, env=dict(os.environ, TMPDIR=str(tmpdir)))

    assert report["progress"]["recorded_fragment_status_counts"]["converged"] == 1
    assert list(tmpdir.iterdir()) == [], "the run left something in TMPDIR"


def test_the_read_only_tree_guard_bites(tmp_path):
    """The inverse of the test above: a copy that writes one file must fail under
    the same permissions, or that test proves nothing."""
    if os.geteuid() == 0:
        pytest.skip("root ignores the permission bits this guard relies on")
    root = make_durable_root(tmp_path)
    mutant = root / "scripts" / "driver_status.py"
    mutant.write_text(
        mutant.read_text(encoding="utf-8").replace(
            "    print(dumps_line(report))",
            "    (durable_root / 'runs' / 'probe').write_text('x')\n"
            "    print(dumps_line(report))",
        ),
        encoding="utf-8",
    )
    with read_only_tree(root):
        proc = subprocess.run(
            [sys.executable, str(mutant)], capture_output=True, text=True, timeout=120
        )
    assert proc.returncode != 0, "a writing mutant survived the read-only tree"


def test_a_held_project_lease_does_not_block_the_report(tmp_path):
    """Safe at any moment against a LIVE run: the driver holds LOCK_EX on
    runs/.driver.lock for its whole lifetime, and this script must neither
    contend for it nor wait on it."""
    root = make_durable_root(tmp_path)
    write_journal(root, "20260826T093010Z", [started()])
    lock_path = write_lock(root, os.getpid())
    fd = os.open(str(lock_path), os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        report = run_status(root)
        assert report["run"]["session_id"] == "20260826T093010Z"
    finally:
        os.close(fd)


def test_a_live_pid_that_is_not_the_driver_does_not_win_selection(tmp_path):
    """The lock's content is written best-effort, so a stale pid can survive
    there; if that pid is then REUSED by an unrelated live process, liveness
    alone would hand the selector an old epoch and report the wrong
    invocation's exit and summary. The command-text corroboration is what rules
    that out, so it is required, not merely reported."""
    root = make_durable_root(tmp_path)
    with sleeping_child() as child:
        write_journal(root, "20260826T090000Z",
                      [started(ts="2026-08-26T09:00:00Z", pid=child.pid)])
        write_journal(root, "20260826T093010Z",
                      [started(ts="2026-08-26T09:30:10Z", pid=999999), exited()])
        write_lock(root, child.pid)
        report = run_status(root)
        assert report["lock_diagnostic"]["pid_alive"] is True
        assert report["lock_diagnostic"]["ps_names_driver_script"] is False
        assert report["run"]["selected_by"] == "greatest_recorded_driver_started_ts"
        assert report["run"]["session_id"] == "20260826T093010Z"


def test_an_unreadable_journal_is_counted_apart_from_one_with_no_start(tmp_path):
    """"Could not establish" and "the driver recorded no start" are different
    facts. Folding the first into the second turns an unknown into an assertion
    about the driver."""
    root = make_durable_root(tmp_path)
    write_journal(root, "20260826T090000Z",
                  [lock_self_test_failed(ts="2026-08-26T09:00:00Z")])
    bad = root / "runs" / "20260826T093010Z"
    bad.mkdir(parents=True)
    (bad / "driver_journal.jsonl").symlink_to(root / "runs" / "nowhere.jsonl")
    report = run_status(root)
    assert report["journals_found"] == 2
    assert report["journals_without_recorded_start"] == 1
    assert report["journals_unreadable"] == 1
    assert report["run"] is None
    assert "unreadable" in report["run_unavailable_reason"]


def test_a_symlinked_fragment_dir_is_refused_not_followed(tmp_path):
    """A symlink can point at another tree, and counting through it would report
    an artifact that is not this durable root's population."""
    root = make_durable_root(tmp_path)
    elsewhere = tmp_path / "someone_elses_book"
    elsewhere.mkdir()
    (elsewhere / "seg01.json").write_text(json.dumps({"status": "converged"}), encoding="utf-8")
    (root / "runs" / "ledger.d").symlink_to(elsewhere)
    report = run_status(root)
    assert report["progress"] is None
    assert "symlink" in report["progress_unavailable_reason"]


def test_a_fragment_dir_symlinked_INSIDE_the_root_is_still_refused(tmp_path):
    """The containment check alone does not cover this: a link whose target is
    inside the durable root passes `_within` and would be followed. `ledger.d`
    is a directory the driver's own writer creates, so one that is a link at all
    means something other than `ledger_update.py` made it, and the honest read is
    "not a real directory" rather than a census taken through it.

    This case exists because a refactor briefly dropped the `is_symlink()` clause
    from `_real_dir` and every test stayed green -- the only symlink fixture
    pointed OUTSIDE the root, where `_within` catches it either way."""
    root = make_durable_root(tmp_path)
    inside = root / "runs" / "actual_fragments"
    inside.mkdir(parents=True)
    (inside / "seg01.json").write_text(json.dumps({"status": "converged"}), encoding="utf-8")
    (root / "runs" / "ledger.d").symlink_to(inside)
    report = run_status(root)
    assert report["progress"] is None
    assert "symlink" in report["progress_unavailable_reason"]


def test_a_symlinked_fragment_is_refused_not_followed(tmp_path):
    root = make_durable_root(tmp_path)
    frag_dir = write_fragments(root, {"seg01": "converged"})
    elsewhere = tmp_path / "elsewhere.json"
    elsewhere.write_text(json.dumps({"status": "converged"}), encoding="utf-8")
    (frag_dir / "seg02.json").symlink_to(elsewhere)
    progress = run_status(root)["progress"]
    assert progress["recorded_fragment_status_counts"]["converged"] == 1
    assert progress["unreadable_fragments"] == 1


def test_a_fifo_fragment_does_not_hang_the_read(tmp_path):
    """Safe to run at any moment is the property that matters most, and a FIFO
    named like a fragment would block the read forever. `is_file()` is already
    False for one, so it never gets opened."""
    root = make_durable_root(tmp_path)
    frag_dir = write_fragments(root, {"seg01": "converged"})
    os.mkfifo(frag_dir / "seg02.json")
    progress = run_status(root)["progress"]
    assert progress["recorded_fragment_status_counts"]["converged"] == 1
    assert progress["unreadable_fragments"] == 1


def test_batch_progress_is_scoped_to_the_gate_and_differs_from_the_manifest(tmp_path):
    """The census an operator watching a live batch actually wants. A run
    launched for two fresh units in a book where two already converged is 0/2,
    while the manifest census correctly reads 2/4 -- the same durable root, two
    different populations, and only one of them answers "how far along is THIS
    batch"."""
    root = make_durable_root(tmp_path, segs=("seg01", "seg02", "seg03", "seg04"))
    write_fragments(root, {"seg01": "converged", "seg02": "converged",
                           "seg03": "in_progress", "seg04": "in_progress"})
    write_journal(root, "20260826T093010Z", [
        started(),
        gate_passed(["seg03", "seg04"]),
    ])
    report = run_status(root)
    assert report["progress"]["scope"] == "manifest"
    assert report["progress"]["recorded_fragment_status_counts"]["converged"] == 2
    batch = report["run"]["batch_progress"]
    assert batch["dispatched"] == 2
    assert batch["recorded_fragment_status_counts"]["converged"] == 0
    assert batch["recorded_fragment_status_counts"]["in_progress"] == 2
    assert batch["dispatched_ids_without_fragment"] == 0


def test_batch_progress_is_null_without_a_gate_entry_and_ids_never_leak(tmp_path):
    root = make_durable_root(tmp_path)
    write_fragments(root, {"seg01": "converged"})
    write_journal(root, "20260826T093010Z", [started()])
    report = run_status(root)
    assert report["run"]["batch_progress"] is None
    assert "_gate_segs" not in json.dumps(report)


@pytest.mark.parametrize("pid", [0, -1, True, 2 ** 70, "4242", None])
def test_a_lock_pid_that_cannot_be_probed_is_a_reason_not_a_traceback(tmp_path, pid):
    """An out-of-range integer raises OverflowError from os.kill -- not an
    OSError -- and uncaught it would print a traceback and NO JSON line, which
    is the one output contract every caller depends on."""
    root = make_durable_root(tmp_path)
    write_lock(root, raw=json.dumps({"pid": pid, "started_at": TS}))
    report = run_status(root)
    assert report["lock_diagnostic"] is None
    assert "positive integer" in report["lock_diagnostic_unavailable_reason"]


def test_a_gate_with_no_fragments_yet_is_a_zero_batch_not_an_unknown(tmp_path):
    """`runs/ledger.d/` does not exist until the first fragment is written, so
    the ordinary state of a fresh run is a fired gate and no fragment dir. That
    is `0/N`, not `null`: the surface knows the number and must not print `?`."""
    root = make_durable_root(tmp_path)
    write_journal(root, "20260826T093010Z", [
        started(),
        gate_passed(["seg01", "seg02"]),
    ])
    report = run_status(root)
    assert report["progress"] is None, "the project census is genuinely absent"
    batch = report["run"]["batch_progress"]
    assert batch is not None
    assert batch["dispatched"] == 2
    assert batch["dispatched_ids_without_fragment"] == 2
    assert batch["recorded_fragment_status_counts"]["converged"] == 0


def test_both_censuses_carry_the_same_caveats(tmp_path):
    """A caveat that rides only one of two sibling numbers reads as if the other
    were stronger."""
    root = make_durable_root(tmp_path)
    write_fragments(root, {"seg01": "converged"})
    write_journal(root, "20260826T093010Z", [
        started(), gate_passed(["seg01"]),
    ])
    report = run_status(root)
    for census in (report["progress"], report["run"]["batch_progress"]):
        assert census["staleness_checked"] is False
        assert census["schema_validated"] is False


@pytest.mark.parametrize("link_name", ["runs", "schemas"])
def test_a_symlinked_ancestor_directory_is_refused(tmp_path, link_name):
    """The final-component check catches a symlinked artifact; it does not catch
    a symlinked PARENT. A durable tree whose `runs/` or `schemas/` links into
    another book would otherwise have that book's fragments, lock and status
    enum read and reported as this root's."""
    root = make_durable_root(tmp_path)
    write_fragments(root, {"seg01": "converged", "seg02": "converged"})
    elsewhere = tmp_path / "other_tree"
    shutil.move(str(root / link_name), str(elsewhere))
    (root / link_name).symlink_to(elsewhere)
    report = run_status(root)
    if link_name == "runs":
        assert report["progress"] is None
        assert report["run"] is None
        assert report["lock_diagnostic"] is None
    else:
        # The status enum is read from schemas/; escaping it must degrade to the
        # observed-statuses fallback rather than silently using another tree's.
        assert report["progress"]["status_enum_source"] == "observed"
