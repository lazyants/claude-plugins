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
import select
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


def _kill_and_reap(procs):
    """Both fixtures below guarantee their subprocesses are gone when a test ends,
    whatever it asserted -- a leaked decoy or harness would otherwise be matched by a
    LATER test's pgrep."""
    for proc in procs:
        if proc.poll() is None:
            proc.kill()
        try:
            proc.wait(timeout=REAP_TIMEOUT_SEC)
        except subprocess.TimeoutExpired:  # pragma: no cover - a killed process reaps
            pass


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
    _kill_and_reap(spawned)


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
def test_broker_for_a_new_shaped_sandbox_is_terminated(tmp_path, decoys):
    """#915: the prefix gained a proj8 tag (`ltcj.p<proj8>.<seg>.<inv>.`) -- the argv
    matcher never parses the basename, so it must still match verbatim against the new
    shape exactly as it did against the old one."""
    sandbox = tmp_path / "ltcj.pdeadbeef.seg07.a1b2c3d4.Xy9Zq0Wv"
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

def _mk_job(tmp_path, seg="seg07"):
    root = tmp_path / "durable"
    (root / "segments").mkdir(parents=True, exist_ok=True)
    companion = tmp_path / "codex-companion.mjs"
    companion.write_text("// stub\n", encoding="utf-8")
    prompt_file = tmp_path / "prompt.txt"
    prompt_file.write_text("prompt\n", encoding="utf-8")
    return codex_job.CodexJob(
        kind="translate", seg=seg, tok="t0", disp="d0", root=str(root),
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


def test_finalize_clears_the_active_sandbox_reference(tmp_path, monkeypatch, capsys):
    """#915: finalize()'s LAST act on the sandbox, after the broker is stopped and the
    directory removed -- the module-level reference the signal handler reads must not
    still point at a sandbox that finalize() has already torn down."""
    job = _mk_job(tmp_path)
    sandbox = tmp_path / "ltcj.pdeadbeef.seg07.a1b2c3d4.Xy9Zq0Wv"
    sandbox.mkdir()
    job.sandbox_dir = str(sandbox)
    codex_job._register_active_sandbox(str(sandbox))
    monkeypatch.setattr(codex_job, "_shutdown_sandbox_broker", lambda path: None)

    job.finalize()
    capsys.readouterr()

    assert codex_job._ACTIVE_SANDBOX is None


# --------------------------------------------------------------------------- #
# #915: SIGTERM/SIGHUP reap the process's own broker before it dies
# --------------------------------------------------------------------------- #

@pytest.fixture(autouse=True)
def _reset_module_signal_state():
    """Every test above this point in the file runs in THIS process and shares the one
    `codex_job` module object imported at collection time -- so a test that registers a
    sandbox in-process must not leak that registration into the next one."""
    yield
    codex_job._ACTIVE_SANDBOX = None
    codex_job._REAPING = False


# The handler tests below drive a REAL child process and REAL signals -- same posture as
# the rest of this file, and for the same reason: a mocked `signal.signal` call proves
# nothing about whether the handler is actually installed and actually reached by a
# delivered signal. The child is a `python -c` script (this driver has no separate
# harness file to invoke) that loads codex_job.py the same way this test module does,
# registers a sandbox (immediately, or from a background thread after a delay -- see
# CJ_SANDBOX2/CJ_LATE_DELAY), optionally slows down `_shutdown_sandbox_broker` so a
# second signal can be sent while the first is still "in" its reap (CJ_SLOW_REAP), or
# replaces it with one that raises (CJ_BROKEN_REAP), installs the real handler, and
# sleeps until signalled.
_CHILD_SCRIPT = """
import os, sys, threading, time, importlib.util
spec = importlib.util.spec_from_file_location("codex_job_child", os.environ["CJ_DRIVER_SRC"])
codex_job = importlib.util.module_from_spec(spec)
spec.loader.exec_module(codex_job)

if os.environ.get("CJ_BROKEN_REAP"):
    def _boom(path):
        raise RuntimeError("simulated pgrep/kill failure")
    codex_job._shutdown_sandbox_broker = _boom
elif os.environ.get("CJ_SLOW_REAP"):
    _real = codex_job._shutdown_sandbox_broker
    _delay = float(os.environ["CJ_SLOW_REAP"])
    def _slow(path):
        time.sleep(_delay)
        return _real(path)
    codex_job._shutdown_sandbox_broker = _slow

sandbox1 = os.environ.get("CJ_SANDBOX1") or None
if sandbox1:
    codex_job._register_active_sandbox(sandbox1)

sandbox2 = os.environ.get("CJ_SANDBOX2") or None
late_delay = os.environ.get("CJ_LATE_DELAY") or None
if sandbox2 and late_delay:
    def _register_late():
        time.sleep(float(late_delay))
        codex_job._register_active_sandbox(sandbox2)
    threading.Thread(target=_register_late, daemon=True).start()

codex_job._install_reap_handler()
sys.stdout.write("ready\\n")
sys.stdout.flush()
time.sleep(30)
"""


def _spawn_child(env_overrides):
    env = dict(os.environ)
    env["CJ_DRIVER_SRC"] = str(DRIVER_SRC)
    env.update(env_overrides)
    return subprocess.Popen([sys.executable, "-c", _CHILD_SCRIPT], env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)


def _await_child_ready(proc):
    """Bounded wait for the child's "ready" line. A plain `proc.stdout.readline()` has
    no timeout, so a child that crashes or hangs before printing it would block this
    call forever -- the caller registers `proc` with the cleanup fixture BEFORE calling
    this, precisely so a timeout here still gets the process killed and waited on
    teardown instead of leaking for the rest of the suite."""
    ready, _, _ = select.select([proc.stdout], [], [], REAP_TIMEOUT_SEC)
    if not ready:
        raise AssertionError("child never reached its ready line within %ss" % REAP_TIMEOUT_SEC)
    line = proc.stdout.readline()
    assert line == "ready\n", "child did not reach its ready line: %r" % (line,)


@pytest.fixture
def child():
    """Spawns the signal-test child and guarantees it is gone when the test ends,
    whatever it asserted -- via the same `_kill_and_reap` finalizer the `decoys`
    fixture uses. The Popen is registered for cleanup IMMEDIATELY after spawning,
    before the (bounded) readiness read -- never after -- so a child that never
    becomes ready is still killed and waited on teardown rather than leaked."""
    spawned = []

    def factory(**env_overrides):
        proc = _spawn_child(env_overrides)
        spawned.append(proc)
        _await_child_ready(proc)
        return proc

    yield factory
    _kill_and_reap(spawned)


@skip_no_pgrep
@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGHUP])
def test_signal_reaps_the_registered_sandbox_and_dies_by_that_signal(tmp_path, decoys, child, sig):
    sandbox = tmp_path / "ltcj.pdeadbeef.seg07.a1b2c3d4.Xy9Zq0Wv"
    sandbox.mkdir()
    decoy = decoys(sandbox)
    proc = child(CJ_SANDBOX1=str(sandbox))

    proc.send_signal(sig)
    proc.wait(timeout=REAP_TIMEOUT_SEC)

    assert proc.returncode == -sig, "expected death by %r, got %r" % (sig, proc.returncode)
    _assert_terminated(decoy)


@skip_no_pgrep
def test_a_second_signal_during_reap_does_not_cut_cleanup_short(tmp_path, decoys, child):
    """The handler is still "in" its first reap (simulated by a slowed
    `_shutdown_sandbox_broker`) when the second SIGTERM arrives. A handler that restored
    `SIG_DFL` before reaping would die right here, on the second signal, before the
    decoy is ever touched."""
    sandbox = tmp_path / "ltcj.pdeadbeef.seg07.a1b2c3d4.Xy9Zq0Wv"
    sandbox.mkdir()
    decoy = decoys(sandbox)
    proc = child(CJ_SANDBOX1=str(sandbox), CJ_SLOW_REAP="1.0")

    proc.send_signal(signal.SIGTERM)
    time.sleep(0.2)
    proc.send_signal(signal.SIGTERM)
    proc.wait(timeout=REAP_TIMEOUT_SEC)

    assert proc.returncode == -signal.SIGTERM
    _assert_terminated(decoy)


@skip_no_pgrep
def test_a_sigint_during_the_settle_does_not_prevent_death_by_the_original_signal(tmp_path, decoys, child):
    """#915 review round 1: `time.sleep(0.5)` is a real interruption point, and a SIGINT
    landing there raises `KeyboardInterrupt`. Without an enclosing `finally:` around the
    settle and the second reap, that exception skips both the second reap and the
    self-signal, so the process dies by SIGINT's own default disposition (-2) instead of
    the signal this handler was invoked for -- reproduced with real signals in review.
    The decoy here is already reaped by the FIRST pass, before the SIGINT ever lands, so
    its staying reaped is the sanity check; the exit code is the load-bearing assertion."""
    sandbox = tmp_path / "ltcj.pdeadbeef.seg07.a1b2c3d4.Xy9Zq0Wv"
    sandbox.mkdir()
    decoy = decoys(sandbox)
    proc = child(CJ_SANDBOX1=str(sandbox))

    proc.send_signal(signal.SIGTERM)
    time.sleep(0.2)
    proc.send_signal(signal.SIGINT)
    proc.wait(timeout=REAP_TIMEOUT_SEC)

    assert proc.returncode == -signal.SIGTERM, (
        "expected death by SIGTERM despite the SIGINT, got %r" % (proc.returncode,))
    _assert_terminated(decoy)


@skip_no_pgrep
def test_a_sandbox_registered_between_the_two_passes_is_still_reaped(tmp_path, decoys, child):
    """Nothing is registered at signal time (the first pass reaps nothing), and a
    background thread registers the decoy's sandbox 0.2s into the handler's 0.5s settle
    sleep -- well before the second pass fires. Only a second pass that RE-READS the
    reference, rather than reusing the first pass's (empty) snapshot, can catch this."""
    sandbox = tmp_path / "ltcj.pdeadbeef.seg08.e5f6a7b8.Mn3Kp1Rt"
    sandbox.mkdir()
    decoy = decoys(sandbox)
    proc = child(CJ_SANDBOX2=str(sandbox), CJ_LATE_DELAY="0.2")

    proc.send_signal(signal.SIGTERM)
    proc.wait(timeout=REAP_TIMEOUT_SEC)

    assert proc.returncode == -signal.SIGTERM
    _assert_terminated(decoy)


def test_a_reap_that_raises_still_dies_by_signal(tmp_path, child):
    """Best-effort: an exception out of `_shutdown_sandbox_broker` must not leave the
    handler stuck mid-cleanup, or escape it and kill the process some other way."""
    sandbox = tmp_path / "ltcj.pdeadbeef.seg07.a1b2c3d4.Xy9Zq0Wv"
    proc = child(CJ_SANDBOX1=str(sandbox), CJ_BROKEN_REAP="1")

    proc.send_signal(signal.SIGTERM)
    proc.wait(timeout=REAP_TIMEOUT_SEC)

    assert proc.returncode == -signal.SIGTERM


# --------------------------------------------------------------------------- #
# #915: proj8 attribution and the sandbox-basename byte budget
# --------------------------------------------------------------------------- #

def test_proj8_matches_across_root_spellings(tmp_path):
    root = tmp_path / "durable"
    root.mkdir()
    link = tmp_path / "durable-link"
    link.symlink_to(root)

    base = codex_job._proj8(str(root))
    assert codex_job._proj8(str(root) + "/") == base
    assert codex_job._proj8(str(link)) == base


def test_proj8_differs_for_a_different_root(tmp_path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    assert codex_job._proj8(str(a)) != codex_job._proj8(str(b))


@pytest.mark.parametrize("label_len", [
    pytest.param(codex_job._SANDBOX_LABEL_CAP, id="at-cap"),
    pytest.param(codex_job._SANDBOX_LABEL_CAP + 1, id="cap-plus-one"),
])
def test_sandbox_basename_never_exceeds_name_max(tmp_path, label_len):
    """Asserts the RESULTING BASENAME length that `_setup_sandbox()` actually produces
    on a real mkdtemp() call -- not the truncated label length -- at the cap and at
    cap+1, the one place an off-by-one in the budget arithmetic would show up."""
    job = _mk_job(tmp_path, seg="s" * label_len)

    assert job._setup_sandbox() is True
    try:
        basename = os.path.basename(job.sandbox_dir)
        assert len(basename.encode("utf-8")) <= 255
        assert basename.startswith("ltcj.p")
    finally:
        shutil.rmtree(job.sandbox_dir, ignore_errors=True)
