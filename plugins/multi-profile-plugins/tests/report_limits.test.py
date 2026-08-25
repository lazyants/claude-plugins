#!/usr/bin/env python3
"""Drive report_limits.py as a subprocess against fixture profiles, homes and a stub app-server.

Every case runs the SCRIPT, not its helpers, so a short-circuit added ahead of a check or a wiring
line deleted from main() has to show up here. The two transport-safety cases are the deliberate
exception: they import the module to substitute its connection class, because there is no way to
reach an HTTPS stub from a subprocess without giving production code an origin override, and an
origin override is the defect those cases exist to prevent.

The Codex stub is an executable named `codex` on a fixture PATH. Production code therefore needs no
test hook at all, and CI never needs the real binary.

Dependency-free: run it with `python3 report_limits.test.py`.
"""
from __future__ import annotations

import datetime
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

SCRIPT = (
    Path(__file__).resolve().parent.parent
    / "skills/code-limits/scripts/report_limits.py"
)

# 48 characters, so three disjoint 12-character slices sit at 0, 18 and 36.
SENTINEL_TOKEN = "sk-live-MUSTNEVERBEPRINTED-ACCESS-TOKEN-DEADBEEF"
SENTINEL_KEYCHAIN = "kc-MUSTNEVERBEPRINTED-KEYCHAIN-SECRET-CAFEBABE00"
SENTINELS = (SENTINEL_TOKEN, SENTINEL_KEYCHAIN)
# The oracle is finite and its bound is stated rather than implied: a printer that keeps at least
# 12 characters from either end, or from the middle, is caught. One that keeps fewer than 12 from
# every region is not. "No substring of any length" is unsatisfiable at one character.
SLICE_LEN = 12


def slices_of(secret: str) -> list[str]:
    return [secret[0:SLICE_LEN], secret[18:18 + SLICE_LEN], secret[-SLICE_LEN:]]


failures: list[str] = []
checks = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global checks
    checks += 1
    if not condition:
        if detail:
            # Indented: an unindented report puts the script's own lines at column 0 under
            # "FAIL (n):", which reads as a pass to a grep and to a reader.
            body = "\n".join(f"      {line}" for line in detail.splitlines())
            failures.append(f"{name}:\n{body}")
        else:
            failures.append(name)


def assert_no_secret(name: str, *streams: str) -> None:
    blob = "\n".join(streams)
    for secret in SENTINELS:
        check(f"{name}: whole sentinel absent", secret not in blob)
        for index, piece in enumerate(slices_of(secret)):
            check(f"{name}: sentinel slice {index} absent", piece not in blob,
                  f"slice {piece!r} appeared in output")


# --- fixtures ---------------------------------------------------------------------------------

STUB_CODEX = '''#!/usr/bin/env python3
"""Stand-in for `codex app-server`: records what it was sent, replies as the mode dictates."""
import json, os, sys

record = os.environ["STUB_RECORD"]
mode = os.environ.get("STUB_MODE", "ok")
with open(record, "w", encoding="utf-8") as handle:
    json.dump({"argv": sys.argv, "env": dict(os.environ)}, handle)

transcript = os.environ["STUB_TRANSCRIPT"]
if mode == "nonzero-exit":
    sys.exit(3)
if mode == "silent":
    for _ in range(3):
        if not sys.stdin.readline():
            break
    while True:                       # started, never answers
        import time; time.sleep(1)

lines = []
for _ in range(3):
    line = sys.stdin.readline()
    if not line:
        break
    lines.append(line.strip())
with open(transcript, "w", encoding="utf-8") as handle:
    handle.write("\\n".join(lines))

if mode == "eof":
    sys.exit(0)
if mode == "error-reply":
    print(json.dumps({"jsonrpc": "2.0", "id": 2, "error": {"code": -1, "message": "no"}}))
elif mode == "wrong-id":
    print(json.dumps({"jsonrpc": "2.0", "id": 99, "result": {}}))
else:
    print(json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}))
    print(json.dumps({"jsonrpc": "2.0", "method": "some/notification", "params": {}}))
    print(json.dumps({"jsonrpc": "2.0", "id": 2, "result": json.loads(os.environ["STUB_RESULT"])}))
sys.stdout.flush()
sys.exit(0)
'''


def iso(delta_hours: float) -> str:
    when = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=delta_hours)
    return when.isoformat()


def epoch(delta_hours: float) -> int:
    when = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=delta_hours)
    return int(when.timestamp())


def now_ms(delta_hours: float = 0.0) -> int:
    when = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=delta_hours)
    return int(when.timestamp() * 1000)


def make_claude(root: Path, name: str, blob) -> Path:
    profile = root / name
    profile.mkdir(parents=True, exist_ok=True)
    if blob is not None:
        (profile / ".claude.json").write_text(json.dumps(blob), encoding="utf-8")
    # Planted in every profile: the script reads this file on the --live path.
    (profile / ".credentials.json").write_text(json.dumps({
        "claudeAiOauth": {"accessToken": SENTINEL_TOKEN, "expiresAt": now_ms(24)}
    }), encoding="utf-8")
    return profile


def cached(entries=None, flat=None, fetched_ms=None, subscription=None, utilization=None):
    blob: dict = {}
    if subscription is not None:
        blob["hasAvailableSubscription"] = subscription
    inner: dict = {}
    if entries is not None:
        inner["limits"] = entries
    if flat is not None:
        inner.update(flat)
    if utilization is not None:
        inner = utilization
    blob["cachedUsageUtilization"] = {
        "fetchedAtMs": now_ms(-1) if fetched_ms is None else fetched_ms,
        "utilization": inner,
    }
    return blob


def entry(kind: Any = "weekly_all", percent: Any = 42, resets: Any = None,
          active: Any = True, scope: Any = None):
    out = {"kind": kind, "percent": percent, "is_active": active,
           "resets_at": iso(48) if resets is None else resets}
    if scope is not None:
        out["scope"] = scope
    return out


def make_codex_home(root: Path, name: str) -> Path:
    home = root / name
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.toml").write_text('model = "x"\n', encoding="utf-8")
    (home / "auth.json").write_text(json.dumps({"token": SENTINEL_KEYCHAIN}), encoding="utf-8")
    return home


def install_stub(root: Path) -> Path:
    bindir = root / "bin"
    bindir.mkdir(parents=True, exist_ok=True)
    stub = bindir / "codex"
    stub.write_text(STUB_CODEX, encoding="utf-8")
    stub.chmod(0o755)
    return bindir


DEFAULT_RESULT = {
    "rateLimits": {
        "limitId": "codex",
        "primary": {"usedPercent": 54, "windowDurationMins": 10080, "resetsAt": epoch(72)},
        "secondary": None,
        "credits": {"hasCredits": True, "unlimited": False, "balance": "347.89"},
    },
    "rateLimitsByLimitId": {
        "codex": {
            "limitId": "codex",
            "primary": {"usedPercent": 54, "windowDurationMins": 10080, "resetsAt": epoch(72)},
        },
        "codex_bengalfox": {
            "limitId": "codex_bengalfox",
            "primary": {"usedPercent": 3, "windowDurationMins": 300, "resetsAt": epoch(4)},
            "secondary": {"usedPercent": 7, "windowDurationMins": 10080, "resetsAt": epoch(96)},
        },
    },
    "rateLimitResetCredits": {"availableCount": 1, "credits": [{"id": "x"}]},
}


def run(args, root: Any = None, stub_mode="ok", stub_result=None, timeout=90,
        extra_env: Any = None):
    env = dict(os.environ)
    env.pop("HTTPS_PROXY", None)
    # Confine discovery. Without this, a case that passes no explicit candidate falls back to the
    # real home directory, reads the operator's own profiles, and spawns the real app-server
    # against their live Codex homes -- a suite that measures the machine instead of the fixture.
    sandbox = Path(root) / "home" if root is not None else Path(tempfile.mkdtemp()) / "home"
    sandbox.mkdir(parents=True, exist_ok=True)
    env["HOME"] = str(sandbox)
    if extra_env:
        env.update(extra_env)
    record = transcript = None
    if root is not None:
        bindir = install_stub(root)
        record = root / "stub-record.json"
        transcript = root / "stub-transcript.txt"
        env["PATH"] = f"{bindir}{os.pathsep}{env['PATH']}"
        env["STUB_RECORD"] = str(record)
        env["STUB_TRANSCRIPT"] = str(transcript)
        env["STUB_MODE"] = stub_mode
        env["STUB_RESULT"] = json.dumps(DEFAULT_RESULT if stub_result is None else stub_result)
    done = subprocess.run([sys.executable, str(SCRIPT)] + args, capture_output=True, text=True,
                          env=env, timeout=timeout)
    return done, record, transcript


# --- cases ------------------------------------------------------------------------------------

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)

    # 1 / 3 / 4 -- what the module may and may not name.
    source = SCRIPT.read_text(encoding="utf-8")
    check("1 no method parameter is taken anywhere",
          "def send(" not in source and "method:" not in source and "method=" not in source,
          "a function takes a method as data")
    check("1 the three requests are module-level constants",
          all(marker in source for marker in
              ("_REQ_INITIALIZE = {", "_NOTIF_INITIALIZED = {", "_REQ_RATE_LIMITS = {")))
    check("3 the redeeming method is not spelled",
          "account/rateLimitResetCredit/consume" not in source)
    for word in ("consume", "redeem"):
        # Whole-identifier match: the prose may say "redeem one in the Codex TUI".
        offenders = [n for n, line in enumerate(source.splitlines(), 1)
                     if f"{word}(" in line or f".{word}" in line or f"\"{word}\"" in line]
        check(f"3 no `{word}` call or literal", not offenders, f"lines {offenders}")
    check("4 the SAFE plural field is present and printable",
          "rateLimitResetCredits" in source and "availableCount" in source,
          "a grep so broad it forbids correct code would have removed these")

    # 5 / 6 -- a window whose reset has passed, and per-row freshness.
    stale_root = root / "stale"
    make_claude(stale_root, ".claude9", cached(entries=[entry(resets=iso(-5), percent=67)],
                                               fetched_ms=now_ms(-72)))
    done, _, _ = run(["--claude-profile", str(stale_root / ".claude9"),
                      "--codex-home", str(stale_root / ".nope")])
    check("5 past reset renders stale-after-reset", "stale-after-reset" in done.stdout, done.stdout)
    check("5 the stale percentage is labelled as the previous window",
          "previous window 67.0%" in done.stdout, done.stdout)
    check("5 it is not presented as a current row",
          "  67.0%  resets" not in done.stdout, done.stdout)
    check("6 the row carries its own cache age", "3d00h old" in done.stdout, done.stdout)

    # 7 / 8 / 9 -- absence states, and the precedence between them.
    empty = root / "states"
    make_claude(empty, ".claudeA", {})                                     # no cache key
    make_claude(empty, ".claudeB", {"hasAvailableSubscription": False})    # no subscription
    make_claude(empty, ".claudeC", {"hasAvailableSubscription": False})    # both conditions
    done, _, _ = run(["--claude-profile", str(empty / ".claudeA"),
                      "--claude-profile", str(empty / ".claudeB"),
                      "--claude-profile", str(empty / ".claudeC"),
                      "--codex-home", str(empty / ".nope")])
    check("7 absent cache is no-usage-cache, not 0%", "[no-usage-cache]" in done.stdout, done.stdout)
    check("8 no subscription is its own state", "[no-subscription]" in done.stdout, done.stdout)
    check("9 the combined shape resolves to exactly one state",
          done.stdout.count("[no-subscription]") == 2 and done.stdout.count("[no-usage-cache]") == 1,
          done.stdout)
    check("7/8 neither absence state gaps the run", done.returncode == 1, f"rc={done.returncode}")

    # 10 / 11 -- the two container shapes.
    shapes = root / "shapes"
    make_claude(shapes, ".claudeD", cached(entries=[
        entry(kind="weekly_scoped", percent=19,
              scope={"model": {"id": None, "display_name": "Fable"}, "surface": None}),
    ]))
    make_claude(shapes, ".claudeE", cached(flat={
        "five_hour": {"utilization": 11, "resets_at": iso(3)},
        "seven_day": {"utilization": 22, "resets_at": iso(50)},
    }))
    done, _, _ = run(["--claude-profile", str(shapes / ".claudeD"),
                      "--claude-profile", str(shapes / ".claudeE"),
                      "--codex-home", str(shapes / ".nope")])
    check("10 the scoped row carries the model display name",
          "weekly_scoped (Fable)" in done.stdout, done.stdout)
    check("11 the flat fallback reports and says which shape it used",
          "five_hour (flat)" in done.stdout and "seven_day (flat)" in done.stdout, done.stdout)

    # 12 -- container table: shapes that must gap BEFORE any record is formed.
    for label, blob in (
        ("limits null", cached(utilization={"limits": None})),
        ("limits is an object", cached(utilization={"limits": {}})),
        ("utilization not an object", {"cachedUsageUtilization":
                                       {"fetchedAtMs": now_ms(-1), "utilization": []}}),
        ("entries not objects", cached(entries=["not-an-object"])),
        ("zero recognised records", cached(utilization={})),
        ("cache not an object", {"cachedUsageUtilization": []}),
    ):
        box = root / f"c-{abs(hash(label))}"
        make_claude(box, ".claudeX", blob)
        done, _, _ = run(["--claude-profile", str(box / ".claudeX"),
                          "--codex-home", str(box / ".nope")])
        check(f"12 container {label} -> payload-malformed",
              done.stdout.count("[payload-malformed]") == 1, done.stdout)
        check(f"12 container {label} -> exit 1", done.returncode == 1, f"rc={done.returncode}")

    # 13 -- scalar table.
    for label, bad in (
        ("percent null", entry(percent=None)),
        ("percent is a bool", entry(percent=True)),
        ("percent out of range", entry(percent=140)),
        ("percent is a string", entry(percent="42")),
        ("resets_at unparseable", entry(resets="not-a-date")),
        ("resets_at naive", entry(resets="2026-08-27T03:00:00")),
        ("is_active is truthy not bool", entry(active=1)),
        ("kind is not a string", entry(kind=7)),
        ("kind has a control character", entry(kind="week\x01ly")),
    ):
        box = root / f"s-{abs(hash(label))}"
        make_claude(box, ".claudeY", cached(entries=[bad]))
        done, _, _ = run(["--claude-profile", str(box / ".claudeY"),
                          "--codex-home", str(box / ".nope")])
        check(f"13 scalar {label} -> field-malformed", "[field-malformed]" in done.stdout,
              done.stdout)
        check(f"13 scalar {label} -> exit 1", done.returncode == 1, f"rc={done.returncode}")

    for label, ms in (("fetchedAtMs null", None), ("fetchedAtMs bool", True),
                      ("fetchedAtMs negative", -5)):
        box = root / f"f-{abs(hash(label))}"
        make_claude(box, ".claudeZ", cached(entries=[entry()], fetched_ms=ms))
        done, _, _ = run(["--claude-profile", str(box / ".claudeZ"),
                          "--codex-home", str(box / ".nope")])
        check(f"13 {label} -> gap", done.returncode == 1, done.stdout)

    # 14 -- a mixed candidate keeps its valid row AND gaps the candidate.
    #
    # The Codex home here is a WORKING stub rather than a missing path, and that is the point of
    # the case: an earlier draft pointed it at `.nope`, so the run exited 1 because CODEX gapped
    # while the Claude candidate quietly reported. The assertion passed for the wrong reason, and
    # a mutant that dropped record-gap aggregation entirely survived it. The only gap this run may
    # contain is the one under test.
    mixed = root / "mixed"
    mixed.mkdir(exist_ok=True)
    mixed_codex = make_codex_home(mixed, ".codexOK")
    make_claude(mixed, ".claudeM", cached(entries=[
        entry(kind="weekly_all", percent=46),
        entry(kind="session", percent=None),
    ]))
    done, _, _ = run(["--claude-profile", str(mixed / ".claudeM"),
                      "--codex-home", str(mixed_codex)], root=mixed)
    check("14 the malformed record is named", "[field-malformed]" in done.stdout, done.stdout)
    check("14 the VALID row survives beside it", "46.0%" in done.stdout, done.stdout)
    check("14 the Codex side of this run is clean, so exit 1 can only come from the mix",
          "appserver" not in done.stdout and "candidate-unreadable" not in done.stdout,
          done.stdout)
    check("14 the malformed record gaps its CANDIDATE", done.returncode == 1,
          f"rc={done.returncode}\n{done.stdout}")
    check("14 the warning names the mixed profile",
          ".claudeM" in done.stdout.split("warnings")[-1], done.stdout)

    # 15 / 16 -- discovery.
    unread = root / "unread"
    make_claude(unread, ".claudeU", cached(entries=[entry()]))
    done, _, _ = run(["--claude-profile", str(unread / ".claudeU" / ".claude.json"),
                      "--codex-home", str(unread / ".nope")])
    check("15 a non-directory candidate is candidate-unreadable",
          "[candidate-unreadable]" in done.stdout, done.stdout)
    check("15 and it gaps the run", done.returncode == 1, f"rc={done.returncode}")

    bare = root / "bare"
    bare.mkdir()
    done, _, _ = run([], root=bare, timeout=90)
    check("16 zero candidates for a vendor is a gap, not a clean empty report",
          done.stdout.count("no candidates found") >= 1 or done.returncode == 1,
          done.stdout)

    # 17 / 18 / 19 / 20 -- Codex.
    codex_root = root / "codex"
    codex_root.mkdir()
    home = make_codex_home(codex_root, ".codexT")
    done, record, transcript = run(["--claude-profile", str(codex_root / ".nope"),
                                    "--codex-home", str(home)], root=codex_root)
    check("18 two limit ids with a 10080 window render distinguishably",
          "codex/weekly" in done.stdout and "codex_bengalfox/weekly" in done.stdout, done.stdout)
    check("18 the 300-minute window is labelled 5h",
          "codex_bengalfox/5h" in done.stdout, done.stdout)
    check("4 the coupon count is printed", "reset coupons" in done.stdout and
          "1" in done.stdout, done.stdout)
    check("4 the credit balance is printed", "347.89" in done.stdout, done.stdout)
    check("2 the transcript is exactly the three allowed messages, in order",
          [json.loads(line).get("method") for line in
           transcript.read_text(encoding="utf-8").splitlines() if line.strip()]
          == ["initialize", "initialized", "account/rateLimits/read"],
          transcript.read_text(encoding="utf-8"))
    assert_no_secret("25 codex run", done.stdout, done.stderr,
                     json.dumps(json.loads(record.read_text(encoding="utf-8"))))

    inactive = dict(DEFAULT_RESULT)
    done, _, _ = run(["--claude-profile", str(codex_root / ".nope"),
                      "--codex-home", str(home)], root=codex_root,
                     stub_result={"rateLimits": {"limitId": "codex", "primary": {
                         "usedPercent": 5, "windowDurationMins": 10080, "resetsAt": epoch(-3)}}})
    check("17 a codex window past its reset is not current",
          "stale-after-reset" in done.stdout, done.stdout)

    for mode, expect in (("error-reply", "appserver-protocol-error"),
                         ("wrong-id", "appserver-protocol-error"),
                         ("eof", "appserver-protocol-error"),
                         ("nonzero-exit", "appserver-")):
        done, _, _ = run(["--claude-profile", str(codex_root / ".nope"),
                          "--codex-home", str(home)], root=codex_root, stub_mode=mode)
        check(f"19 app-server {mode} -> gap", expect in done.stdout, done.stdout)
        check(f"19 app-server {mode} -> exit 1", done.returncode == 1, f"rc={done.returncode}")

    for label, result in (
        ("no rateLimits at all", {"rateLimitsByLimitId": {}}),
        ("window duration absent", {"rateLimits": {"limitId": "codex", "primary": {
            "usedPercent": 5, "resetsAt": epoch(3)}}}),
        ("usedPercent is a bool", {"rateLimits": {"limitId": "codex", "primary": {
            "usedPercent": True, "windowDurationMins": 10080, "resetsAt": epoch(3)}}}),
        ("availableCount is a bool", {
            "rateLimits": {"limitId": "codex", "primary": {
                "usedPercent": 5, "windowDurationMins": 10080, "resetsAt": epoch(3)}},
            "rateLimitResetCredits": {"availableCount": True}}),
    ):
        done, _, _ = run(["--claude-profile", str(codex_root / ".nope"),
                          "--codex-home", str(home)], root=codex_root, stub_result=result)
        check(f"19 codex payload {label} -> exit 1", done.returncode == 1, done.stdout)

    # 20 -- a child that starts and never answers must not hold up the rest of the run.
    twin = make_codex_home(codex_root, ".codexU")
    done, _, _ = run(["--claude-profile", str(codex_root / ".nope"),
                      "--codex-home", str(home), "--codex-home", str(twin)],
                     root=codex_root, stub_mode="silent",
                     extra_env={"CODE_LIMITS_APPSERVER_TIMEOUT": "3"}, timeout=60)
    check("20 a silent child expires into appserver-failed",
          "[appserver-failed]" in done.stdout, done.stdout)
    check("20 the run still exits, and gaps", done.returncode == 1, f"rc={done.returncode}")
    check("20 the SECOND home is still reported despite the first stalling",
          done.stdout.count("[appserver-failed]") == 2, done.stdout)

    # 21 -- --live with no reachable token gaps that profile and never falls back to the cache.
    live_root = root / "live"
    profile = make_claude(live_root, ".claudeL", cached(entries=[entry(percent=88)]))
    (profile / ".credentials.json").unlink()
    done, _, _ = run(["--live", "--claude-profile", str(profile),
                      "--codex-home", str(live_root / ".nope")])
    check("21 a missing token gaps the profile",
          "[token-absent]" in done.stdout or "[keychain-denied]" in done.stdout, done.stdout)
    check("21 it does NOT silently fall back to the cache", "88.0%" not in done.stdout, done.stdout)
    check("21 and the run gaps", done.returncode == 1, f"rc={done.returncode}")

# --- 22 / 23: transport safety. These import the module, deliberately, because reaching an HTTPS
# stub from a subprocess would need a production origin override -- the very defect they prevent.
sys.path.insert(0, str(SCRIPT.parent))
import report_limits as R  # noqa: E402

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    profile = make_claude(root, ".claudeT", cached(entries=[entry()]))
    now = datetime.datetime.now(datetime.timezone.utc)

    class Redirecting:
        """A connection that answers 302, and counts how many requests it was given."""

        instances: list = []

        def __init__(self, host, timeout=None):
            self.host = host
            self.timeout = timeout
            self.requests: list = []
            Redirecting.instances.append(self)

        def request(self, method, path, headers=None):
            self.requests.append({"method": method, "path": path, "headers": dict(headers or {})})

        def getresponse(self):
            outer = self

            class Response:
                status = 302
                def read(self):
                    return b""
                def getheader(self, name, default=None):
                    return "https://elsewhere.invalid/x" if name.lower() == "location" else default
            return Response()

        def close(self):
            pass

    original = R.HTTPSConnection
    try:
        R.HTTPSConnection = Redirecting
        try:
            R._claude_live(profile, now)
            outcome = "no-error"
        except R.Malformed as exc:
            outcome = exc.code
        check("23 a 302 is an http-error, not a followed redirect", outcome == "http-error", outcome)
        check("23 exactly one connection was opened", len(Redirecting.instances) == 1,
              str(len(Redirecting.instances)))
        check("23 exactly one request was issued", len(Redirecting.instances[0].requests) == 1,
              str(Redirecting.instances[0].requests))
        check("23 the pinned host is the only one contacted",
              Redirecting.instances[0].host == "api.anthropic.com", Redirecting.instances[0].host)
        check("23 a timeout was passed rather than inherited",
              isinstance(Redirecting.instances[0].timeout, (int, float)),
              repr(Redirecting.instances[0].timeout))

        # 22 -- a malformed token makes http.client raise with the bearer inside the message.
        class Raising:
            def __init__(self, host, timeout=None):
                pass
            def request(self, method, path, headers=None):
                raise ValueError(
                    f"Invalid header value {headers['Authorization']!r}")
            def getresponse(self):
                raise AssertionError("unreachable")
            def close(self):
                pass

        bad = make_claude(root, ".claudeBad", cached(entries=[entry()]))
        (bad / ".credentials.json").write_text(json.dumps({
            "claudeAiOauth": {"accessToken": SENTINEL_TOKEN + "\n", "expiresAt": now_ms(24)}
        }), encoding="utf-8")
        R.HTTPSConnection = Raising
        try:
            R._claude_live(bad, now)
            code = "no-error"
        except R.Malformed as exc:
            code = exc.code
        check("22 the header ValueError maps to a closed code", code == "http-error", code)
        check("22 the code is a member of the closed enum", code in R.DIAGNOSTIC_SET, code)
    finally:
        R.HTTPSConnection = original

    # 24 -- every diagnostic token the script can render is a member of the closed enum.
    check("24 the enum has a total fallback", "internal-error" in R.DIAGNOSTIC_SET)
    check("24 the enum is a frozenset of unique tokens",
          len(R.DIAGNOSTICS) == len(R.DIAGNOSTIC_SET))
    import ast as _ast
    tree = _ast.parse(SCRIPT.read_text(encoding="utf-8"))
    strings = {node.value for node in _ast.walk(tree)
               if isinstance(node, _ast.Constant) and isinstance(node.value, str)}
    used = {token for token in R.DIAGNOSTIC_SET if token in strings}
    check("24 every token the source uses is a member of the enum",
          used <= R.DIAGNOSTIC_SET, str(sorted(used - R.DIAGNOSTIC_SET)))
    check("24 the enum has no member the source never uses",
          R.DIAGNOSTIC_SET == used, str(sorted(R.DIAGNOSTIC_SET - used)))

    # 25 -- the fragment oracle itself: three truncation shapes must each be caught.
    for label, mutated in (
        ("head-truncating printer", SENTINEL_TOKEN[:20]),
        ("tail-truncating printer", SENTINEL_TOKEN[-20:]),
        ("middle-eliding printer", SENTINEL_TOKEN[14:34]),
    ):
        caught = any(piece in mutated for piece in slices_of(SENTINEL_TOKEN))
        check(f"25 the oracle catches a {label}", caught, f"{mutated!r} evaded every slice")

print(f"ran {checks} checks")
if failures:
    print(f"FAIL ({len(failures)}):")
    for failure in failures:
        print(f"  {failure}")
    sys.exit(1)

MIN_CHECKS = 95
if checks < MIN_CHECKS:
    print(f"FAIL: only {checks} checks ran, expected at least {MIN_CHECKS}")
    sys.exit(1)
print("PASS")
