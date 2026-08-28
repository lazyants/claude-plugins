"""#789: codex_job.py must stop the codex-companion app-server broker keyed to its own
per-invocation sandbox before it deletes that sandbox.

codex-companion keys a PERSISTENT broker to whatever `--cwd` it is handed, and tears one
down only from its own `SessionEnd` hook, keyed to the Claude session's cwd -- never to a
single-use sandbox this plugin invented. So without the teardown under test here, every
dispatch left an `app-server-broker.mjs` (plus the `codex app-server` and
`codex-code-mode-host` it owns) running against a directory that no longer exists,
reparented to init, alive until the machine rebooted.

These tests drive REAL processes rather than asserting on a mocked signal: the thing that
can silently stop working is the argv pattern's ability to match a broker's actual command
line, and a mock proves nothing about that. Each decoy is spawned with the exact argv SHAPE
`spawnBrokerProcess()` uses (`node <script> serve --endpoint <ep> --cwd <cwd> --pid-file
<pid>`), so a pattern that stops matching a real broker fails here too.

The negative cases are the load-bearing ones -- a matcher that kills everything passes the
positive test alone.
"""

import importlib.util
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets" / "scripts"
DRIVER_SRC = SCRIPTS_DIR / "codex_job.py"
assert DRIVER_SRC.is_file(), f"expected the driver at {DRIVER_SRC}"

_spec = importlib.util.spec_from_file_location("codex_job_broker_mod", str(DRIVER_SRC))
assert _spec is not None and _spec.loader is not None
codex_job = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(codex_job)

# The teardown shells out to pgrep. Without it there is nothing to test rather than
# something that silently passes.
skip_no_pgrep = pytest.mark.skipif(shutil.which("pgrep") is None,
                                   reason="pgrep unavailable")

# Long enough that a decoy never exits on its own inside a test, short enough that a leaked
# one cannot outlive the suite by much.
DECOY_LIFETIME_SEC = 60
# A signal is delivered to an already-running process in milliseconds; this is the ceiling
# before the test calls it a failure, not an expected wait.
REAP_TIMEOUT_SEC = 15
# How long a survivor is watched before it counts as having survived.
SURVIVE_WATCH_SEC = 1.5
# Present in every decoy's argv, broker-shaped or not -- see _await_visible_to_pgrep.
DECOY_MARKER = "cxc-lt789-decoy"


def _spawn_decoy(cwd_arg, *, script_name="app-server-broker.mjs"):
    """A process whose command line has the same argv shape as a real broker, with
    `cwd_arg` in the `--cwd` slot. `script_name` is a knob for the negative case where the
    path matches but the process is not a broker."""
    argv = [
        sys.executable, "-c",
        "import sys, time; time.sleep(%d)" % DECOY_LIFETIME_SEC,
        "/plugins/cache/openai-codex/codex/1.0.6/scripts/%s" % script_name,
        "serve", "--endpoint", "unix:/tmp/%s/broker.sock" % DECOY_MARKER,
        "--cwd", str(cwd_arg),
        "--pid-file", "/tmp/%s/broker.pid" % DECOY_MARKER,
    ]
    return subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


@pytest.fixture
def decoys():
    """Spawns decoys and guarantees every one is gone when the test ends, whatever it
    asserted -- a leaked 60 s sleeper would otherwise be matched by a LATER test's pgrep."""
    spawned = []

    def factory(cwd_arg, **kw):
        proc = _spawn_decoy(cwd_arg, **kw)
        spawned.append(proc)
        _await_visible_to_pgrep(proc)
        return proc

    yield factory
    for proc in spawned:
        if proc.poll() is None:
            proc.kill()
        try:
            proc.wait(timeout=REAP_TIMEOUT_SEC)
        except subprocess.TimeoutExpired:  # pragma: no cover - a killed process reaps
            pass


def _await_visible_to_pgrep(proc):
    """`Popen` returns before the kernel has necessarily published the new argv to
    /proc (or to BSD's kinfo). Without this, a positive test could pass or fail on
    scheduling rather than on the pattern, and -- worse -- a negative test would pass
    vacuously because the decoy was not yet visible to match."""
    deadline = time.monotonic() + REAP_TIMEOUT_SEC
    while time.monotonic() < deadline:
        # Probe on the marker EVERY decoy carries, never on the broker script name --
        # one decoy is deliberately not a broker, and probing for the script name would
        # make that test fail here instead of asserting what it is about.
        found = subprocess.run(["pgrep", "-f", DECOY_MARKER],
                               capture_output=True, text=True, timeout=10)
        if str(proc.pid) in (found.stdout or "").split():
            return
        time.sleep(0.05)
    raise AssertionError("decoy pid %d never became visible to pgrep" % proc.pid)


def _assert_terminated(proc):
    proc.wait(timeout=REAP_TIMEOUT_SEC)
    assert proc.returncode == -signal.SIGTERM, (
        "expected SIGTERM, got returncode %r" % (proc.returncode,))


def _assert_survives(proc):
    with pytest.raises(subprocess.TimeoutExpired):
        proc.wait(timeout=SURVIVE_WATCH_SEC)


# --------------------------------------------------------------------------- #
# the pattern's escaping
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("meta", list(r"\.[]{}()*+?^$|"))
def test_every_ere_metacharacter_is_escaped(meta):
    """re.escape() is deliberately not used (it also escapes `-`, `&`, `~`, `#` and space,
    and a backslash before an ordinary character is undefined in POSIX ERE), so the
    replacement has to be checked to actually cover the metacharacters it claims."""
    assert codex_job._ere_escape("a%sb" % meta) == "a\\%sb" % meta


def test_ordinary_characters_are_left_alone():
    """The other half of the same claim. These are the characters re.escape() would have
    escaped and POSIX ERE leaves undefined behind a backslash; they are reachable because
    the sandbox path is TMPDIR-prefixed and TMPDIR belongs to the operator."""
    assert codex_job._ere_escape("a-b c&d~e#f/g:h_i") == "a-b c&d~e#f/g:h_i"


# --------------------------------------------------------------------------- #
# what gets signalled, and what does not
# --------------------------------------------------------------------------- #

@skip_no_pgrep
def test_broker_for_this_sandbox_is_terminated(tmp_path, decoys):
    sandbox = tmp_path / "ltcj.seg07.a1b2c3d4.Xy9Zq0Wv"
    sandbox.mkdir()
    proc = decoys(sandbox)

    codex_job._shutdown_sandbox_broker(str(sandbox))

    _assert_terminated(proc)


@skip_no_pgrep
def test_broker_for_a_different_sandbox_survives(tmp_path, decoys):
    """The signal is scoped to THIS invocation's sandbox: a concurrent codex_job.py in
    another profile or another book holds a live sandbox of its own, and killing its broker
    would abort a paid turn that is still being consumed."""
    mine = tmp_path / "ltcj.seg07.a1b2c3d4.Xy9Zq0Wv"
    theirs = tmp_path / "ltcj.seg08.e5f6a7b8.Mn3Kp1Rt"
    mine.mkdir()
    theirs.mkdir()
    proc = decoys(theirs)

    codex_job._shutdown_sandbox_broker(str(mine))

    _assert_survives(proc)


@skip_no_pgrep
def test_a_non_broker_process_holding_the_same_cwd_survives(tmp_path, decoys):
    """`codex-companion task --cwd <sandbox>` and its detached task-worker carry the very
    same path in their argv. Only the broker outlives the job, so only the broker is a
    target."""
    sandbox = tmp_path / "ltcj.seg07.a1b2c3d4.Xy9Zq0Wv"
    sandbox.mkdir()
    proc = decoys(sandbox, script_name="codex-companion.mjs")

    codex_job._shutdown_sandbox_broker(str(sandbox))

    _assert_survives(proc)


@skip_no_pgrep
def test_a_longer_path_with_this_sandbox_as_its_prefix_survives(tmp_path, decoys):
    """The pattern anchors the end of the path. Without that anchor a sandbox name that is
    a prefix of a live one would take the live one's broker down with it."""
    sandbox = tmp_path / "ltcj.seg07.a1b2c3d4.Xy9Zq0Wv"
    sandbox.mkdir()
    proc = decoys(str(sandbox) + "AndThenSome")

    codex_job._shutdown_sandbox_broker(str(sandbox))

    _assert_survives(proc)


@skip_no_pgrep
def test_a_dot_in_the_sandbox_name_is_not_a_wildcard(tmp_path, decoys):
    """`mkdtemp(prefix="ltcj.<seg>.<inv>.")` puts four dots in every sandbox name. Unescaped
    they are ERE wildcards, and this decoy -- identical except where a dot sits -- is what
    an unescaped pattern would kill."""
    sandbox = tmp_path / "ltcj.seg07.a1b2c3d4.Xy9Zq0Wv"
    sandbox.mkdir()
    near_miss = tmp_path / "ltcjXseg07.a1b2c3d4.Xy9Zq0Wv"
    proc = decoys(near_miss)

    codex_job._shutdown_sandbox_broker(str(sandbox))

    _assert_survives(proc)


# --------------------------------------------------------------------------- #
# never raises
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("sandbox", [None, ""])
def test_no_sandbox_is_a_silent_no_op(sandbox, monkeypatch):
    def explode(*a, **kw):  # pragma: no cover - the point is that it is not reached
        raise AssertionError("pgrep must not run without a sandbox path")

    monkeypatch.setattr(codex_job.subprocess, "run", explode)
    codex_job._shutdown_sandbox_broker(sandbox)


@pytest.mark.parametrize("boom", [OSError("no pgrep"), subprocess.TimeoutExpired("pgrep", 5)])
def test_a_failing_pgrep_never_propagates(tmp_path, monkeypatch, boom):
    """Cleanup on the way out must never turn a finished, promoted job into a failed one."""
    def raiser(*a, **kw):
        raise boom

    monkeypatch.setattr(codex_job.subprocess, "run", raiser)
    codex_job._shutdown_sandbox_broker(str(tmp_path / "ltcj.seg07.a1b2c3d4.Xy9Zq0Wv"))


def test_a_nonzero_pgrep_yields_no_signals(tmp_path, monkeypatch):
    """pgrep exits 1 when nothing matched and >=2 when pgrep itself failed. Neither carries
    pids, and a stdout read on either would be reading noise."""
    monkeypatch.setattr(codex_job.subprocess, "run",
                        lambda *a, **kw: subprocess.CompletedProcess(
                            a[0] if a else [], 1, stdout="99999\n", stderr=""))

    def explode(*a, **kw):  # pragma: no cover - the point is that it is not reached
        raise AssertionError("no signal may be sent on a non-zero pgrep")

    monkeypatch.setattr(codex_job.os, "kill", explode)
    codex_job._shutdown_sandbox_broker(str(tmp_path / "ltcj.seg07.a1b2c3d4.Xy9Zq0Wv"))


def test_a_dead_pid_between_pgrep_and_kill_is_absorbed(tmp_path, monkeypatch):
    """The window between pgrep reporting a pid and the signal reaching it is real: the
    broker can exit on its own in it."""
    monkeypatch.setattr(codex_job.subprocess, "run",
                        lambda *a, **kw: subprocess.CompletedProcess(
                            a[0] if a else [], 0, stdout="99999\n", stderr=""))

    def gone(pid, sig):
        raise ProcessLookupError(pid)

    monkeypatch.setattr(codex_job.os, "kill", gone)
    codex_job._shutdown_sandbox_broker(str(tmp_path / "ltcj.seg07.a1b2c3d4.Xy9Zq0Wv"))


def test_this_process_is_never_signalled(tmp_path, monkeypatch):
    """pgrep -f matches on a substring of the whole command line, so a pytest invocation
    naming this file can match its own pattern."""
    own = os.getpid()
    monkeypatch.setattr(codex_job.subprocess, "run",
                        lambda *a, **kw: subprocess.CompletedProcess(
                            a[0] if a else [], 0,
                            stdout="%d\n1\n0\n" % own, stderr=""))
    signalled = []
    monkeypatch.setattr(codex_job.os, "kill", lambda pid, sig: signalled.append(pid))
    codex_job._shutdown_sandbox_broker(str(tmp_path / "ltcj.seg07.a1b2c3d4.Xy9Zq0Wv"))
    assert signalled == []


# --------------------------------------------------------------------------- #
# where finalize() calls it
# --------------------------------------------------------------------------- #

def _mk_job(tmp_path):
    root = tmp_path / "durable"
    (root / "segments").mkdir(parents=True, exist_ok=True)
    companion = tmp_path / "codex-companion.mjs"
    companion.write_text("// stub\n", encoding="utf-8")
    prompt_file = tmp_path / "prompt.txt"
    prompt_file.write_text("prompt\n", encoding="utf-8")
    return codex_job.CodexJob(
        kind="translate", seg="seg07", tok="t0", disp="d0", root=str(root),
        companion=str(companion), prompt_text="prompt", prompt_file=str(prompt_file),
        deadline_sec=100, poll_sec=1, effort="high", node="node")


def test_finalize_stops_the_broker_while_the_sandbox_still_exists(tmp_path, monkeypatch, capsys):
    """Ordering, not merely occurrence. The rmtree is what makes the broker's cwd vanish,
    and a straggling turn writing into the sandbox during the delete is exactly what
    stopping it first prevents."""
    job = _mk_job(tmp_path)
    sandbox = tmp_path / "ltcj.seg07.a1b2c3d4.Xy9Zq0Wv"
    sandbox.mkdir()
    job.sandbox_dir = str(sandbox)

    seen = []
    monkeypatch.setattr(codex_job, "_shutdown_sandbox_broker",
                        lambda path: seen.append((path, os.path.isdir(path))))

    job.finalize()
    capsys.readouterr()

    assert seen == [(str(sandbox), True)]
    assert not sandbox.exists()


def test_finalize_without_a_sandbox_does_not_call_it(tmp_path, monkeypatch, capsys):
    job = _mk_job(tmp_path)
    job.sandbox_dir = None

    monkeypatch.setattr(codex_job, "_shutdown_sandbox_broker",
                        lambda path: pytest.fail("no sandbox, nothing to stop"))

    job.finalize()
    capsys.readouterr()
