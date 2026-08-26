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
    frag_dir = root / "runs" / "ledger.d"
    frag_dir.mkdir(parents=True, exist_ok=True)
    for seg, status in statuses.items():
        (frag_dir / f"{seg}.json").write_text(
            json.dumps({"status": status, "rounds": 1}), encoding="utf-8"
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


def write_lock(root, pid, started_at=TS, raw=None):
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
            {"type": "step1_gate_passed", "segs": ["seg02"], "ts": "2026-08-26T09:30:11Z"},
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
        root,
        "20260826T093010Z",
        [
            {"type": "lock_self_test_failed", "lock_path": "runs/.driver.lock",
             "ts": "2026-08-26T09:30:09Z"},
            started(),
            exited(),
        ],
    )
    report = run_status(root)
    assert report["run"]["epochs_in_journal"] == 1
    assert report["run"]["lease_enforcement_warning"] is True
    assert report["run"]["recorded_pid"] == 4242


def test_journal_without_a_recorded_start_is_not_a_run(tmp_path):
    root = make_durable_root(tmp_path)
    write_journal(
        root,
        "20260826T093010Z",
        [{"type": "lock_self_test_failed", "ts": "2026-08-26T09:30:09Z"}],
    )
    report = run_status(root)
    assert report["run"] is None
    assert report["journals_found"] == 1
    assert report["journals_without_recorded_start"] == 1
    assert "none with a recorded driver_started" in report["run_unavailable_reason"]


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
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)",
         str(root / "scripts" / "segment_dispatch_driver.py")]
    )
    try:
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
    finally:
        child.terminate()
        child.wait()


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
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        write_journal(root, "20260826T093010Z", [started(pid=999999)])
        write_lock(root, child.pid)
        report = run_status(root)
        assert report["run"]["selected_by"] == "greatest_recorded_driver_started_ts"
        assert report["run"]["pid_matches_lock_diagnostic"] is False
        assert report["lock_diagnostic"]["pid_alive"] is True
        assert report["lock_diagnostic"]["ps_names_driver_script"] is False
    finally:
        child.terminate()
        child.wait()


def test_ps_unavailable_degrades_to_null_and_still_reports(tmp_path):
    root = make_durable_root(tmp_path)
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        write_journal(root, "20260826T093010Z", [started(pid=child.pid)])
        write_lock(root, child.pid)
        env = dict(os.environ, PATH="")
        report = run_status(root, env=env)
        assert report["lock_diagnostic"]["pid_alive"] is True
        assert report["lock_diagnostic"]["ps_command"] is None
        assert report["lock_diagnostic"]["ps_names_driver_script"] is False
    finally:
        child.terminate()
        child.wait()


def test_a_zombie_pid_passes_the_kill_probe_but_ps_says_defunct(tmp_path):
    """Measured: os.kill(pid, 0) SUCCEEDS for an unreaped child while `ps` prints
    `<defunct>`. This is why liveness alone is never published as a conclusion --
    the ps text is what distinguishes the two."""
    root = make_durable_root(tmp_path)
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)",
         str(root / "scripts" / "segment_dispatch_driver.py")]
    )
    try:
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
    finally:
        child.wait()


def test_mid_write_journal_counts_the_partial_line_and_keeps_the_rest(tmp_path):
    root = make_durable_root(tmp_path)
    write_journal(
        root,
        "20260826T093010Z",
        [started(), {"type": "step1_gate_passed", "segs": ["seg01", "seg02"], "ts": TS}],
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
    assert "does not exist" in report["progress_unavailable_reason"]


def test_lock_absent_empty_or_unparseable_is_null_with_a_reason(tmp_path):
    root = make_durable_root(tmp_path)
    report = run_status(root)
    assert report["lock_diagnostic"] is None
    assert "does not exist" in report["lock_diagnostic_unavailable_reason"]

    write_lock(root, None, raw="")
    report = run_status(root)
    assert report["lock_diagnostic"] is None
    assert "is empty" in report["lock_diagnostic_unavailable_reason"]

    write_lock(root, None, raw="{oops")
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
    entries = {}
    for path in sorted(root.rglob("*")):
        if path.is_dir():
            entries[str(path.relative_to(root)) + "/"] = None
        else:
            stat = path.stat()
            entries[str(path.relative_to(root))] = (path.read_bytes(), stat.st_mtime_ns)
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

    paths = sorted(root.rglob("*"), reverse=True)
    try:
        for path in paths:
            path.chmod(0o555 if path.is_dir() else 0o444)
        root.chmod(0o555)
        env = dict(os.environ, TMPDIR=str(tmpdir))
        report = run_status(root, env=env)
    finally:
        root.chmod(0o755)
        for path in sorted(root.rglob("*")):
            path.chmod(0o755 if path.is_dir() else 0o644)

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
    paths = sorted(root.rglob("*"), reverse=True)
    try:
        for path in paths:
            path.chmod(0o555 if path.is_dir() else 0o444)
        root.chmod(0o555)
        proc = subprocess.run(
            [sys.executable, str(mutant)], capture_output=True, text=True, timeout=120
        )
    finally:
        root.chmod(0o755)
        for path in sorted(root.rglob("*")):
            path.chmod(0o755 if path.is_dir() else 0o644)
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
        # And the lease is still ours afterwards: nothing released or took it.
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        os.close(fd)
