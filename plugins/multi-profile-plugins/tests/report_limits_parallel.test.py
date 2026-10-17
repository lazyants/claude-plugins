#!/usr/bin/env python3
"""Concurrency contracts for the report, using fake readers and no live credentials or network."""
from __future__ import annotations

import contextlib
import importlib.util
import io
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parent.parent / "skills/code-limits/scripts/report_limits.py"
SPEC = importlib.util.spec_from_file_location("report_limits_parallel", SCRIPT)
R = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = R
SPEC.loader.exec_module(R)


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
        with patch.object(R, "ThreadPoolExecutor", wraps=ThreadPoolExecutor) as executor:
            outcomes = R._parallel_examine(jobs)
        self.assertEqual(executor.call_args.kwargs["max_workers"], 8)
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


if __name__ == "__main__":
    unittest.main()
