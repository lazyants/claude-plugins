#!/usr/bin/env python3
"""#809's job-watch primitives, pinned as UNIT tests against a real companion
stand-in: launch_codex's jobId handling, read_job_status's parse,
wait_for_artifact's terminal-vs-unknown race, and _job_failed's
environmental-fault predicate.

These stay at the function level so tests/glossary_driver_sequence.test.py can
stay at the subprocess-outcome level (a batch ends before the deadline)
without re-deriving every boundary case -- timeout arithmetic, a status the
companion cannot answer, a terminal status racing the artifact -- through a
live driver run each time.

`mod` mirrors tests/glossary_dispatch_driver.test.py's own fixture: the driver
and its one hard sibling dependency, json_stdout.py, are staged into a
throwaway scripts/ directory and imported by exact path, the same way a
deployed copy loads it.
"""

import importlib.util
import json
import shutil
import sys
import time
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = PLUGIN_ROOT / "skills" / "literary-translator"
DRIVER = SKILL_ROOT / "assets" / "scripts" / "glossary_dispatch_driver.py"
JSON_STDOUT = SKILL_ROOT / "assets" / "scripts" / "json_stdout.py"


@pytest.fixture
def mod(tmp_path):
    scripts = tmp_path / "durable" / "scripts"
    scripts.mkdir(parents=True)
    target = scripts / "glossary_dispatch_driver.py"
    shutil.copy2(DRIVER, target)
    # json_stdout.py is the driver's one hard sibling dependency: it is loaded
    # by exact path at import time and the driver exits without it, exactly as
    # a deployed copy does. Staging it keeps this fixture a real scripts/ dir.
    shutil.copy2(JSON_STDOUT, target.parent / "json_stdout.py")
    spec = importlib.util.spec_from_file_location(
        f"gdd_jw{abs(hash(str(target)))}", target)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_companion(tmp_path, stdout: str, exit_code: int = 0) -> Path:
    """A throwaway python "companion" standing in for codex-companion.mjs: it
    prints exactly the given stdout and exits with the given code, whatever
    argv it is invoked with. launch_codex and read_job_status only ever look
    at stdout and the exit code, so a fixed-response script is a faithful stub
    for both without reimplementing the real CLI's flag parsing."""
    path = tmp_path / "companion.py"
    path.write_text(
        "import sys\n"
        f"sys.stdout.write({stdout!r})\n"
        f"sys.exit({exit_code})\n",
        encoding="utf-8",
    )
    return path


class FakeCtx:
    """The four attributes wait_for_artifact actually reads off its ctx
    argument -- not the real Ctx, which needs a template, a batches list and a
    dozen other fields this function never touches."""

    def __init__(self, *, deadline_sec, poll_sec, companion="c.py", node_bin="node"):
        self.deadline_sec = deadline_sec
        self.poll_sec = poll_sec
        self.companion = companion
        self.node_bin = node_bin


# ---------------------------------------------------------------------------
# launch_codex
# ---------------------------------------------------------------------------

def test_launch_codex_returns_the_job_id_from_a_well_formed_launch(mod, tmp_path):
    companion = make_companion(tmp_path, json.dumps({"jobId": "j1", "status": "queued"}))
    job_id = mod.launch_codex(
        companion=str(companion), node_bin=sys.executable, prompt="do it",
        effort="high", sandbox_root=tmp_path / "sandbox", tmpdir=tmp_path,
        label="dispatch-0-0")
    assert job_id == "j1"


@pytest.mark.parametrize("stdout,why", [
    (json.dumps({"ok": True}), "a well-formed object with no jobId field"),
    ("not json", "unparsable stdout"),
    (json.dumps([]), "a JSON array is not the object shape a launch prints"),
], ids=["missing-jobId", "malformed", "non-object"])
def test_launch_codex_refuses_a_launch_with_no_watchable_job_id(mod, tmp_path, stdout, why):
    """Empty, malformed and non-object stdout all take the SAME refusal path:
    a launch with no handle is a launch that cannot be watched, so the driver
    refuses rather than polling blind -- the same posture codex_job.py takes
    for W5."""
    companion = make_companion(tmp_path, stdout)
    with pytest.raises(mod.DriverError) as exc:
        mod.launch_codex(
            companion=str(companion), node_bin=sys.executable, prompt="do it",
            effort="high", sandbox_root=tmp_path / "sandbox", tmpdir=tmp_path,
            label="dispatch-0-0")
    assert "printed no jobId" in str(exc.value), f"{why}: {exc.value}"
    assert "dispatch-0-0" in str(exc.value)
    assert exc.value.extra["launch_stdout"] == stdout


def test_launch_codex_still_reports_a_nonzero_exit_as_before(mod, tmp_path):
    """Unchanged behaviour: a launch that exits non-zero fails on its
    returncode, before stdout is ever parsed for a jobId."""
    companion = make_companion(tmp_path, "", exit_code=1)
    with pytest.raises(mod.DriverError) as exc:
        mod.launch_codex(
            companion=str(companion), node_bin=sys.executable, prompt="do it",
            effort="high", sandbox_root=tmp_path / "sandbox", tmpdir=tmp_path,
            label="dispatch-0-0")
    assert "codex launch returned 1 for dispatch-0-0" in str(exc.value)


# ---------------------------------------------------------------------------
# read_job_status
# ---------------------------------------------------------------------------

def test_read_job_status_prefers_errorMessage_over_summary(mod, tmp_path):
    companion = make_companion(tmp_path, json.dumps(
        {"job": {"status": "failed", "summary": "s", "errorMessage": "e"}}))
    status, detail = mod.read_job_status(
        companion=str(companion), node_bin=sys.executable, job_id="j1",
        sandbox_root=tmp_path, timeout=30)
    assert (status, detail) == ("failed", "e")


def test_read_job_status_falls_back_to_summary_when_no_errorMessage(mod, tmp_path):
    companion = make_companion(tmp_path, json.dumps(
        {"job": {"status": "failed", "summary": "s"}}))
    status, detail = mod.read_job_status(
        companion=str(companion), node_bin=sys.executable, job_id="j1",
        sandbox_root=tmp_path, timeout=30)
    assert (status, detail) == ("failed", "s")


def test_read_job_status_detail_is_none_when_neither_field_has_content(mod, tmp_path):
    companion = make_companion(tmp_path, json.dumps(
        {"job": {"status": "failed", "errorMessage": ""}}))
    status, detail = mod.read_job_status(
        companion=str(companion), node_bin=sys.executable, job_id="j1",
        sandbox_root=tmp_path, timeout=30)
    assert (status, detail) == ("failed", None)


@pytest.mark.parametrize("stdout,exit_code,why", [
    ("", 1, "a non-zero exit"),
    ("not json", 0, "unparsable stdout"),
    (json.dumps({"job": None}), 0, "a missing job object"),
], ids=["nonzero-exit", "malformed", "null-job"])
def test_read_job_status_is_unknown_not_failure_when_it_cannot_be_read(
        mod, tmp_path, stdout, exit_code, why):
    """Every unreadable shape collapses to (None, None) -- UNKNOWN. The
    artifact poll stays in charge; a status this function cannot parse must
    never be read as a fact about the job."""
    companion = make_companion(tmp_path, stdout, exit_code=exit_code)
    status, detail = mod.read_job_status(
        companion=str(companion), node_bin=sys.executable, job_id="j1",
        sandbox_root=tmp_path, timeout=30)
    assert (status, detail) == (None, None), why


# ---------------------------------------------------------------------------
# wait_for_artifact
# ---------------------------------------------------------------------------

def test_wait_for_artifact_keeps_polling_while_status_is_unreadable(mod, monkeypatch):
    """(a) status (None, None) forever, ready() true on its 3rd call. An
    unreadable status is UNKNOWN, not terminal, so the wait must keep checking
    the artifact rather than giving up on it."""
    calls = {"ready": 0}

    def ready():
        calls["ready"] += 1
        return calls["ready"] >= 3

    monkeypatch.setattr(mod, "read_job_status", lambda **kw: (None, None))
    monkeypatch.setattr(mod.time, "sleep", lambda *a, **k: None)
    ctx = FakeCtx(deadline_sec=60, poll_sec=0.01)
    out = mod.wait_for_artifact(ctx, ready=ready, job_id="j1",
                                sandbox_root=Path("/sbx"), label="batch 0 attempt 0")
    assert out == {"ready": True, "jobStatus": None, "jobDetail": None}
    assert calls["ready"] == 3


def test_wait_for_artifact_stops_immediately_on_a_terminal_status(mod, monkeypatch):
    """(b) status ("failed", "cap") on the very first read, ready() always
    false. The batch must end within the poll iteration that read the
    terminal status -- never after another sleep -- so time.sleep raising if
    called at all is the assertion, not merely a convenience mock."""
    calls = {"ready": 0}

    def ready():
        calls["ready"] += 1
        return False

    def no_sleep(*a, **k):
        raise AssertionError("must not sleep once the job is terminal")

    monkeypatch.setattr(mod, "read_job_status", lambda **kw: ("failed", "cap"))
    monkeypatch.setattr(mod.time, "sleep", no_sleep)
    ctx = FakeCtx(deadline_sec=60, poll_sec=0.01)
    out = mod.wait_for_artifact(ctx, ready=ready, job_id="j1",
                                sandbox_root=Path("/sbx"), label="batch 0 attempt 0")
    assert out == {"ready": False, "jobStatus": "failed", "jobDetail": "cap"}
    assert calls["ready"] == 2, "the first check plus the post-terminal re-check, no more"


def test_wait_for_artifact_rechecks_the_artifact_before_reporting_terminal(mod, monkeypatch):
    """(c) status ("completed", None), ready() false then TRUE on the
    re-check. The terminal race guard: a job writes its artifact and THEN goes
    terminal, so the top-of-loop check can predate the write -- the re-check
    after a terminal status must win over the job record."""
    calls = {"ready": 0}

    def ready():
        calls["ready"] += 1
        return calls["ready"] >= 2

    def no_sleep(*a, **k):
        raise AssertionError("a terminal status must resolve without sleeping")

    monkeypatch.setattr(mod, "read_job_status", lambda **kw: ("completed", None))
    monkeypatch.setattr(mod.time, "sleep", no_sleep)
    ctx = FakeCtx(deadline_sec=60, poll_sec=0.01)
    out = mod.wait_for_artifact(ctx, ready=ready, job_id="j1",
                                sandbox_root=Path("/sbx"), label="batch 0 attempt 0")
    assert out == {"ready": True, "jobStatus": None, "jobDetail": None}
    assert calls["ready"] == 2


def test_wait_for_artifact_gives_up_at_the_deadline_when_the_job_keeps_running(mod, monkeypatch):
    """(d) status ("running", None), ready() always false. A job that never
    terminates must still be bounded by the clock -- and the report at expiry
    carries the last status actually read ("running"), not swallowed into
    None, when the read that finally crosses the deadline is the one that
    read it. The read itself is what consumes the real time that crosses the
    deadline, exactly the race #809's own docstring names: remaining is
    recomputed AFTER the read, so `status` from that same read survives into
    the terminal branch even though `remaining` has already gone negative."""
    def slow_running_status(**kw):
        time.sleep(0.25)  # past the 0.2s deadline below, before this read returns
        return ("running", None)

    monkeypatch.setattr(mod, "read_job_status", slow_running_status)
    ctx = FakeCtx(deadline_sec=0.2, poll_sec=0.05)
    out = mod.wait_for_artifact(ctx, ready=lambda: False, job_id="j1",
                                sandbox_root=Path("/sbx"), label="batch 0 attempt 0")
    assert out == {"ready": False, "jobStatus": "running", "jobDetail": None}


def test_wait_for_artifact_never_fails_a_batch_over_an_unreadable_status_at_the_deadline(
        mod, monkeypatch):
    """(e) THE DEADLINE RACE. The status probe itself takes real time; if it
    returns only after the deadline has already passed, remaining<=0 makes the
    status branch terminal on that same iteration -- but an artifact that
    showed up while the probe was running must still win over an unreadable
    status. An unreadable status can never turn an existing artifact into a
    failure."""
    state = {"ready": False}

    def slow_unreadable_status(**kw):
        time.sleep(0.25)  # past the 0.2s deadline below
        state["ready"] = True
        return (None, None)

    monkeypatch.setattr(mod, "read_job_status", slow_unreadable_status)
    ctx = FakeCtx(deadline_sec=0.2, poll_sec=0.05)
    out = mod.wait_for_artifact(ctx, ready=lambda: state["ready"], job_id="j1",
                                sandbox_root=Path("/sbx"), label="batch 0 attempt 0")
    assert out == {"ready": True, "jobStatus": None, "jobDetail": None}


def test_wait_for_artifact_never_hands_the_probe_a_non_positive_or_overlong_timeout(
        mod, monkeypatch):
    """(f) Every timeout the probe is handed, recorded across a run of case
    (d)'s shape, must be strictly positive and never exceed the deadline --
    the probe is never started with no time left and never given more than
    the time left, so the wait cannot overrun its deadline by more than one
    probe that was itself bounded by it."""
    seen = []

    def recording_status(**kw):
        seen.append(kw["timeout"])
        return ("running", None)

    monkeypatch.setattr(mod, "read_job_status", recording_status)
    deadline_sec = 0.3
    ctx = FakeCtx(deadline_sec=deadline_sec, poll_sec=0.05)
    mod.wait_for_artifact(ctx, ready=lambda: False, job_id="j1",
                          sandbox_root=Path("/sbx"), label="batch 0 attempt 0")
    assert seen, "the probe was never called"
    assert all(0 < t <= deadline_sec for t in seen), seen


# ---------------------------------------------------------------------------
# _job_failed
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("status,expected", [
    ("failed", True),
    ("cancelled", True),
    ("completed", False),
    ("running", False),
    (None, False),
])
def test_job_failed_is_true_only_for_an_environmental_fault(mod, status, expected):
    """failed/cancelled are the job not running to completion -- an
    environmental fault, never a fact about the candidates it was deciding."""
    assert mod._job_failed({"jobStatus": status}) is expected


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
