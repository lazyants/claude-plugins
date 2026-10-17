#!/usr/bin/env python3
"""Concurrency contracts for the report, using fake readers and no live credentials or network."""
from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parent.parent / "skills/code-limits/scripts/report_limits.py"
SPEC = importlib.util.spec_from_file_location("report_limits_parallel", SCRIPT)
R = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = R
SPEC.loader.exec_module(R)
REAL_POPEN = subprocess.Popen

INTERRUPT_HARNESS = r'''
import importlib.util, os, socket, subprocess, sys, threading
from pathlib import Path

spec = importlib.util.spec_from_file_location("cancel_report", sys.argv[1])
R = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = R
spec.loader.exec_module(R)
root = Path(sys.argv[3])
kind = sys.argv[2]

def mark(name, value):
    with (root / name).open("a") as handle:
        handle.write(str(value) + "\n")

class Response:
    status = 200
    def __init__(self, sock):
        self.sock = sock
    def read(self):
        return self.sock.recv(1)

class Connection:
    def __init__(self, *_args, **_kwargs):
        self.sock = self.original = self.peer = None
    def request(self, *_args, **_kwargs):
        if kind == "connect":
            mark("started", "request")
            threading.Event().wait(60)  # no socket exposed yet: DNS/connect/TLS stand-in
        self.sock, self.peer = socket.socketpair()
        self.original = self.sock
        self.sock.settimeout(60)
        mark("started", "request")
    def getresponse(self):
        if kind == "headers":
            self.sock.recv(1)
        response = Response(self.sock)
        if kind == "body":
            self.sock = None  # Connection: close detaches it before the body finishes
        return response
    def close(self):
        for sock in (self.original, self.peer):
            if sock is not None:
                sock.close()
        mark("closed", "connection")

R._claude_user_agent = lambda: ""
R.HTTPSConnection = Connection
if kind in ("codex", "keychain"):
    producer = R._codex_records if kind == "codex" else R._claude_live
else:
    R._claude_token = lambda _path: "DUMMY-SECRET-MUST-NOT-APPEAR"
    producer = R._claude_live
aliases = sys.argv[4] == "aliases"
paths = [root / ("same-home" if aliases and i < 8 else str(i)) for i in range(12)]
def discover(_root, prefix, *_args, **_kwargs):
    chosen = ".codex" if kind == "codex" else ".claude"
    return [R.Candidate(path) for path in paths] if prefix == chosen else []
R._discover = discover
try:
    R.main(["--live"])
except KeyboardInterrupt:
    sys.exit(130)
sys.exit(1)  # interrupted runs must not quietly finish their queued work
'''

STALLED_HELPER = '''#!/usr/bin/env python3
import os, time
from pathlib import Path
with (Path(os.environ["CANCEL_FIXTURE_ROOT"]) / "started").open("a") as handle:
    handle.write(str(os.getpid()) + "\\n")
while True:
    time.sleep(1)
'''


def record(path):
    return [R.Record(path.name, R.REPORTED, percent=12)]


class ParallelReportTests(unittest.TestCase):
    def setUp(self):
        self.agent = self.enterContext(patch.object(R, "_claude_user_agent", return_value="test"))
        self.enterContext(patch.object(R, "HTTPSConnection", side_effect=AssertionError("live HTTP")))
        self.enterContext(patch.object(R.subprocess, "Popen", side_effect=AssertionError("live CLI")))

    def report(self, claude, codex, producer, *, live=False):
        def discover(_root, prefix, *_args, **_kwargs):
            return claude if prefix == ".claude" else codex

        with (patch.object(R, "_discover", side_effect=discover),
              patch.object(R, "_claude_live", producer),
              patch.object(R, "_codex_records", producer),
              patch.object(R, "_render") as render,
              contextlib.redirect_stdout(io.StringIO()) as output):
            status = R.main(["--live"] if live else [])
        return status, render.call_args.args, output.getvalue()

    def test_seven_profiles_overlap_across_vendors_and_keep_report_order(self):
        paths = [Path("/fixtures") / name for name in
                 [".claude", ".claude2", ".claude3", ".codex", ".codex2", ".codex3", ".codex4"]]
        candidates = [R.Candidate(path) for path in paths]
        barrier = threading.Barrier(len(paths))
        release = [threading.Event() for _ in paths]
        completed = []

        def producer(path):
            self.assertEqual(self.agent.call_count, 1)
            index = paths.index(path)
            barrier.wait(timeout=5)
            release[-1].set()
            if not release[index].wait(timeout=5):
                raise RuntimeError("a concurrent read never completed")
            completed.append(index)
            if index:
                release[index - 1].set()
            return record(path)

        status, (groups, notes, *_), output = self.report(
            candidates[:3], candidates[3:], producer, live=True)
        self.assertEqual(status, 0, output)
        self.assertEqual(completed, list(reversed(range(7))))
        self.assertEqual([group[1] for group in groups], [str(path) for path in paths])
        self.assertEqual(notes, [])
        self.agent.assert_called_once_with()

    def test_worker_count_is_bounded_at_eight(self):
        barrier = threading.Barrier(8)

        def producer(path):
            barrier.wait(timeout=5)
            return record(path)

        jobs = [(R.Candidate(Path("/fixtures") / str(index)), producer) for index in range(16)]
        with patch.object(R, "Thread", wraps=threading.Thread) as threads:
            outcomes = R._parallel_examine(jobs)
        self.assertEqual(threads.call_count, 8)
        self.assertTrue(all(call.kwargs["daemon"] for call in threads.call_args_list))
        self.assertTrue(all(state == R.REPORTED for state, _, _ in outcomes))
        self.assertEqual([rows[0].name for _, rows, _ in outcomes], [str(i) for i in range(16)])
        self.agent.assert_not_called()

    def test_aliases_of_one_home_do_not_overlap_or_change_reader_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / ".codex"
            home.mkdir()
            alias = Path(tmp) / ".codex-alias"
            alias.symlink_to(home, target_is_directory=True)
            guard = threading.Lock()
            active = 0
            peak = 0
            seen = []

            def producer(path):
                nonlocal active, peak
                with guard:
                    active += 1
                    peak = max(peak, active)
                    seen.append(path)
                # Stand in for blocking I/O while another worker tries the same home.
                threading.Event().wait(0.02)
                with guard:
                    active -= 1
                return record(path)

            paths = [home, alias, home, alias]
            outcomes = R._parallel_examine([(R.Candidate(path), producer) for path in paths])
        self.assertEqual(peak, 1)
        self.assertCountEqual(seen, paths)
        self.assertEqual([rows[0].name for _, rows, _ in outcomes], [p.name for p in paths])

    def test_failed_workers_keep_cache_fallback_diagnostics_and_live_semantics(self):
        claude = [R.Candidate(Path("/fixtures/.claude")),
                  R.Candidate(Path("/fixtures/.claude2"))]
        codex = [R.Candidate(Path("/fixtures/.codex"))]
        secret = "MUST-NOT-APPEAR-IN-A-DIAGNOSTIC"

        def producer(path):
            if path == claude[0].path:
                raise R.Malformed("http-error")
            if path == claude[1].path:
                raise RuntimeError(secret)
            return record(path)

        def cached(path, _now):
            if path == claude[0].path:
                return record(path)
            raise R.Malformed("no-usage-cache")

        with patch.object(R, "_claude_cached", side_effect=cached) as cache:
            status, (groups, notes, *_), output = self.report(claude, codex, producer)
            self.assertEqual(cache.call_count, 2)
        self.assertEqual(status, 1)
        self.assertEqual([len(group[3]) for group in groups], [1, 0, 1])
        self.assertTrue(any(".claude: the live read did not answer" in note for note in notes))
        self.assertIn(".claude2 [internal-error]", notes)
        self.assertIn("NOT checked", output)
        self.assertNotIn(secret, output + str(notes))

        with patch.object(R, "_claude_cached") as cache:
            status, (groups, notes, *_), output = self.report(claude, codex, producer, live=True)
            cache.assert_not_called()
        self.assertEqual(status, 1)
        self.assertEqual([len(group[3]) for group in groups], [0, 0, 1])
        self.assertIn(".claude [http-error]", notes)
        self.assertNotIn(secret, output + str(notes))

    def interrupt_case(self, kind, *, aliases=False):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            for binary in ("codex", "security"):
                path = bin_dir / binary
                path.write_text(STALLED_HELPER)
                path.chmod(0o755)
            env = dict(os.environ, PATH=str(bin_dir) + os.pathsep + os.environ.get("PATH", ""),
                       CANCEL_FIXTURE_ROOT=str(root), CODE_LIMITS_APPSERVER_TIMEOUT="60")
            child = REAL_POPEN(
                [sys.executable, "-c", INTERRUPT_HARNESS, str(SCRIPT), kind, str(root),
                 "aliases" if aliases else "distinct"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env,
                start_new_session=True,
            )
            expected = 1 if aliases else 8

            def markers(name):
                path = root / name
                return path.read_text().splitlines() if path.exists() else []

            try:
                deadline = time.monotonic() + 8
                while len(markers("started")) < expected and child.poll() is None:
                    if time.monotonic() >= deadline:
                        self.fail("fixture readers did not start")
                    time.sleep(0.01)
                self.assertIsNone(child.poll(), "fixture exited before interruption")
                started = markers("started")
                self.assertEqual(len(started), expected)
                before = time.monotonic()
                child.send_signal(signal.SIGINT)
                try:
                    stdout, stderr = child.communicate(timeout=3)
                except subprocess.TimeoutExpired:
                    self.fail("Ctrl+C kept waiting for stalled readers")
                self.assertLess(time.monotonic() - before, 3)
                self.assertEqual(child.returncode, 130, stdout + stderr)
                self.assertNotIn("DUMMY-SECRET", stdout + stderr)
                self.assertEqual(markers("started"), started, "queued profiles started after Ctrl+C")
                if kind in ("headers", "body"):
                    self.assertEqual(len(markers("closed")), expected)
                if kind in ("codex", "keychain"):
                    for pid in map(int, started):
                        with self.assertRaises(ProcessLookupError, msg=f"helper {pid} survived"):
                            os.kill(pid, 0)
            finally:
                # This process group belongs only to this newly created fixture session.
                # Also clean its stubs if a broken implementation orphaned one of them.
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                child.communicate(timeout=5)

    @unittest.skipUnless(os.name == "posix", "POSIX SIGINT integration fixture")
    def test_interrupt_closes_active_http_readers_and_cancels_queued_profiles(self):
        for kind in ("headers", "body", "connect"):
            with self.subTest(phase=kind):
                self.interrupt_case(kind)

    @unittest.skipUnless(os.name == "posix", "POSIX SIGINT integration fixture")
    def test_interrupt_does_not_start_alias_waiters_or_queued_profiles(self):
        self.interrupt_case("body", aliases=True)

    @unittest.skipUnless(os.name == "posix", "POSIX SIGINT integration fixture")
    def test_interrupt_kills_and_reaps_owned_codex_and_keychain_helpers(self):
        for kind in ("codex", "keychain"):
            with self.subTest(helper=kind):
                self.interrupt_case(kind)


if __name__ == "__main__":
    unittest.main()
