"""Quota fallback and machine-output contracts; all HTTP and CLI processes are fixtures."""
import datetime
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "skills/code-limits/scripts/report_limits.py"
spec = importlib.util.spec_from_file_location("quota_report", SCRIPT)
R = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = R
spec.loader.exec_module(R)
NOW = datetime.datetime.now(datetime.timezone.utc)
RESET = str(int((NOW + datetime.timedelta(days=2)).timestamp()))
TOKEN = "secret-token-must-not-reach-the-report"


def headers(*windows, status="allowed"):
    result = [("anthropic-ratelimit-unified-status", status)]
    for window, utilization in windows:
        result += [(f"anthropic-ratelimit-unified-{window}-utilization", str(utilization)),
                   (f"anthropic-ratelimit-unified-{window}-reset", RESET)]
    return result


class ProbeTests(unittest.TestCase):
    def read(self, responses, **kwargs):
        calls = []
        closed = []

        class Connection:
            def __init__(self, host, timeout):
                self.index = len(calls)
                self.sock = None
                self.response = responses[self.index]
                self.host = host

            def request(self, method, path, **options):
                calls.append((self.host, method, path, options))

            def getresponse(self):
                status, body, fields = self.response

                class Response:
                    def read(self, *args):
                        return body

                    def getheaders(self):
                        return fields

                reply = Response()
                reply.status = status
                return reply

            def close(self):
                closed.append(self.index)

        with patch.object(R, "_claude_token", return_value=TOKEN), \
                patch.object(R, "_claude_user_agent", return_value="claude-cli/2.1.319 (external, cli)"), \
                patch.object(R, "HTTPSConnection", Connection):
            try:
                records = R._claude_live(Path("/fixture/.claude"), **kwargs)
            except R.Malformed as error:
                records = error.code
        self.assertEqual(len(calls), len(closed))
        return records, calls

    def test_default_remains_one_get_without_generation(self):
        result, calls = self.read([(429, b"throttled", [])])
        self.assertEqual(result, "rate-limited")
        self.assertEqual([c[1] for c in calls], ["GET"])

    def test_opt_in_fallback_matches_native_one_token_probe(self):
        records, calls = self.read([(429, b"throttled", []),
                                    (200, b"ignored", headers(("7d", .8)))], quota_probe=True)
        self.assertEqual([(c[0], c[1], c[2]) for c in calls],
                         [(R.API_HOST, "GET", R.API_PATH), (R.API_HOST, "POST", "/v1/messages")])
        self.assertEqual(json.loads(calls[1][3]["body"]),
                         {"model": R.QUOTA_MODEL, "max_tokens": 1,
                          "messages": [{"role": "user", "content": "quota"}]})
        self.assertEqual(calls[1][3]["headers"]["Authorization"], "Bearer " + TOKEN)
        self.assertEqual(records[0].percent, 80)
        self.assertEqual(records[0].window, "weekly")
        self.assertEqual(R._source(records[0].freshness, R.CLAUDE_GROUP), "headers")
        self.assertEqual(records[-1].state, R.NO_CURRENT)

    def test_successful_statistics_read_never_probes(self):
        body = json.dumps({"seven_day": {"utilization": 21,
                                        "resets_at": (NOW + datetime.timedelta(days=30)).isoformat()}}).encode()
        records, calls = self.read([(200, body, [])], quota_probe=True)
        self.assertEqual(records[0].percent, 21)
        self.assertEqual(len(calls), 1)

    def test_max_or_pro_keep_both_session_and_weekly_constraints(self):
        rows = R._claude_header_rows(headers(("5h", .95), ("7d", .2)), NOW)
        self.assertEqual({r.window: r.percent for r in rows}, {"5h": 95, "weekly": 20})

    def test_session_only_or_future_windows_do_not_require_ultra_shape(self):
        for duration, label in [("5h", "5h"), ("30d", "43200min")]:
            with self.subTest(duration=duration):
                rows = R._claude_header_rows(headers((duration, .4)), NOW)
                self.assertEqual([(r.window, r.percent) for r in rows], [(label, 40)])

    def test_paid_overage_is_never_subscription_headroom(self):
        fields = headers(("7d", 1)) + [("anthropic-ratelimit-unified-overage-utilization", "0"),
                                        ("anthropic-ratelimit-unified-overage-reset", RESET)]
        rows = R._claude_header_rows(fields, NOW)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].percent, 100)

    def test_model_scoped_window_is_kept_when_reported_by_probe(self):
        rows = R._claude_header_rows(headers(("7d", .2), ("7d-overage-included", 1)), NOW)
        self.assertEqual([r.family for r in rows], ["all", "model-scoped"])
        self.assertEqual(rows[1].percent, 100)

    def test_quota_rejection_with_usage_headers_is_still_readable(self):
        records, calls = self.read([(429, b"throttled", []),
                                    (429, b"quota exhausted", headers(("7d", 1), status="rejected"))],
                                   quota_probe=True)
        self.assertEqual(records[0].percent, 100)
        self.assertEqual(len(calls), 2)

    def test_unexplained_quota_rejection_cannot_look_available(self):
        rows = R._claude_header_rows(headers(("7d", .3), status="rejected"), NOW)
        self.assertEqual(rows[-1].diagnostic, "quota-rejected")
        self.assertEqual(rows[-1].state, R.GAP)

    def test_throttled_probe_without_quota_headers_is_unknown(self):
        result, calls = self.read([(429, b"throttled", []), (429, b"throttled", [])],
                                  quota_probe=True)
        self.assertEqual(result, "rate-limited")
        self.assertEqual(len(calls), 2)

    def test_auth_errors_redirects_and_other_errors_never_probe_or_retry(self):
        for status in (301, 401, 403, 500):
            with self.subTest(status=status):
                result, calls = self.read([(status, TOKEN.encode(), [])], quota_probe=True)
                self.assertEqual(result, "http-error")
                self.assertEqual(len(calls), 1)

    def test_probe_failure_never_retries_or_prints_response_body(self):
        result, calls = self.read([(429, b"throttled", []), (403, TOKEN.encode(), [])], quota_probe=True)
        self.assertEqual(result, "http-error")
        self.assertEqual(len(calls), 2)

    def test_missing_or_invalid_header_field_gaps_that_window(self):
        for value in ("nan", "inf", "-0.1", "not-a-number"):
            with self.subTest(value=value):
                rows = R._claude_header_rows(headers(("7d", value)), NOW)
                self.assertEqual(rows[0].state, R.GAP)
        fields = headers(("5h", .2), ("7d", .3))[:-1]
        rows = R._claude_header_rows(fields, NOW)
        self.assertEqual([r.state for r in rows], [R.REPORTED, R.GAP])

    def test_duplicate_headers_and_missing_primary_pool_are_refused(self):
        for fields in (headers(("7d", .2), ("7d", .3)),
                       headers(("5h", 1), ("300m", .2)),
                       headers(("7d-overage-included", .2)), []):
            with self.subTest(fields=fields), self.assertRaises(R.Malformed):
                R._claude_header_rows(fields, NOW)

    def test_status_only_window_is_incomplete_not_an_absent_limit(self):
        fields = headers(("7d", .2)) + [("anthropic-ratelimit-unified-5h-status", "allowed")]
        rows = R._claude_header_rows(fields, NOW)
        self.assertEqual([r.state for r in rows], [R.REPORTED, R.GAP])

    def test_rejected_window_below_one_hundred_is_not_available(self):
        fields = headers(("5h", .9)) + [("anthropic-ratelimit-unified-5h-status", "rejected")]
        self.assertEqual(R._claude_header_rows(fields, NOW)[0].diagnostic, "quota-rejected")

    def test_expired_header_window_never_becomes_current(self):
        fields = [(k, "1" if k.endswith("-reset") else v) for k, v in headers(("7d", .2))]
        rows = R._claude_header_rows(fields, NOW)
        self.assertEqual(rows[0].kind(), "stale")

    def test_unknown_request_paths_are_refused_before_credentials_move(self):
        with patch.object(R, "HTTPSConnection") as conn, self.assertRaises(R.Malformed):
            R._claude_http(TOKEN, "", "POST", "/other", b"{}")
        conn.assert_not_called()

    def test_json_retains_model_scoped_limits_omitted_by_human_layout(self):
        utilization = {"limits": [{"kind": "weekly_all", "percent": 10,
                                   "resets_at": (NOW + datetime.timedelta(days=30)).isoformat()},
                                  {"kind": "weekly_scoped", "percent": 100,
                                   "resets_at": (NOW + datetime.timedelta(days=30)).isoformat()}]}
        self.assertEqual(len(R._claude_rows(utilization, "live now", NOW)), 1)
        self.assertEqual(len(R._claude_rows(utilization, "live now", NOW, include_scoped=True)), 2)

    def test_cli_json_vendor_filter_and_probe_are_wired_without_real_network(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            profile = home / ".claude"
            profile.mkdir()
            (profile / ".credentials.json").write_text(json.dumps({"claudeAiOauth": {
                "accessToken": TOKEN, "expiresAt": 4102444800000}}))
            (home / ".codex").mkdir()
            (home / ".codex/auth.json").write_text("{}")
            stub = home / "sitecustomize.py"
            stub.write_text('''import http.client, json
class Connection:
 def __init__(self,*a,**kw): pass
 def request(self,method,path,**kw): self.method=method
 def getresponse(self):
  class Response:
   status=429 if self.method=='GET' else 200
   def read(self,*a):return b'ignored secret-token-must-not-reach-the-report'
   def getheaders(self):return ''' + repr(headers(("5h", .1), ("7d", .4))) + '''
  return Response()
 def close(self):pass
http.client.HTTPSConnection=Connection
''')
            bin_dir = home / "bin"
            bin_dir.mkdir()
            for name, contents in (("claude", '#!/bin/sh\necho "2.1.319 (Claude Code)"\n'),
                                   ("codex", '#!/bin/sh\necho unexpected >&2\nexit 99\n')):
                command = bin_dir / name
                command.write_text(contents)
                command.chmod(0o755)
            env = dict(os.environ, HOME=str(home), PYTHONPATH=str(home),
                       PATH=str(bin_dir) + os.pathsep + os.environ.get("PATH", ""))
            result = subprocess.run([sys.executable, str(SCRIPT), "--live", "--quota-probe",
                                     "--vendor", "claude", "--json"],
                                    env=env, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            data = json.loads(result.stdout)
            self.assertEqual(data["schema_version"], 1)
            self.assertEqual(len(data["profiles"]), 1)
            self.assertEqual(data["profiles"][0]["vendor"], "claude")
            self.assertEqual([r["percent"] for r in data["profiles"][0]["records"]], [10, 40])
            self.assertEqual({r["source"] for r in data["profiles"][0]["records"]}, {"headers"})
            self.assertNotIn(TOKEN, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
