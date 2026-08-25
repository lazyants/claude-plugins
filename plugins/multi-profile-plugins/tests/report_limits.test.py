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
import re
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
# Every `[token]` the script printed anywhere in this run. Collected from real output, so it can
# actually disagree with the enum -- unlike a set intersected with the enum, which cannot.
RENDERED_TOKENS: set[str] = set()


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

STUB_SECURITY = '''#!/usr/bin/env python3
"""Stand-in for macOS `security`. Never touches a real keychain, and leaves a marker proving so."""
import os, sys

marker = os.environ.get("STUB_SECURITY_MARKER")
if marker:
    with open(marker, "a", encoding="utf-8") as handle:
        print(" ".join(sys.argv[1:]), file=handle)
sys.exit(int(os.environ.get("STUB_SECURITY_RC", "1")))
'''

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
if mode == "flood":
    while True:
        sys.stdout.write("x" * 65536)     # no newline, ever, and no end
        sys.stdout.flush()
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
    leaked = ""
    home = os.environ.get("CODEX_HOME", "")
    if home and os.path.exists(os.path.join(home, "auth.json")):
        with open(os.path.join(home, "auth.json"), encoding="utf-8") as handle:
            leaked = json.load(handle).get("token", "")
    print(json.dumps({"jsonrpc": "2.0", "method": "log/line", "params": {"text": leaked}}))
    sys.stderr.write("child stderr carrying " + leaked + "\\n")
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


UNSET = object()


def cached(entries=None, flat=None, fetched_ms=UNSET, subscription=None, utilization=None):
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
        "fetchedAtMs": now_ms(-1) if fetched_ms is UNSET else fetched_ms,
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
    for name, body in (("codex", STUB_CODEX), ("security", STUB_SECURITY)):
        # A stub with a syntax error exits non-zero, which is exactly what the failure it stands
        # in for looks like. Compile it here so a broken fixture cannot pass as a real result.
        compile(body, f"<stub {name}>", "exec")
        stub = bindir / name
        stub.write_text(body, encoding="utf-8")
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
        # One pool whose two windows share a duration: this is the shape the slot suffix exists
        # for, and equal durations across DIFFERENT limit ids do not exercise it.
        "codex_twin": {
            "limitId": "codex_twin",
            "primary": {"usedPercent": 11, "windowDurationMins": 300, "resetsAt": epoch(2)},
            "secondary": {"usedPercent": 22, "windowDurationMins": 300, "resetsAt": epoch(5)},
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
    if root is None:
        root = Path(tempfile.mkdtemp())
    root = Path(root)
    sandbox = root / "home"
    sandbox.mkdir(parents=True, exist_ok=True)
    env["HOME"] = str(sandbox)
    if extra_env:
        env.update(extra_env)
    # ALWAYS, not only when a case supplies a root. A case that omitted it inherited the real
    # PATH, so a keychain fallback reached the operator's own `security` and could prompt them
    # from what reads as a nominal fixture test.
    bindir = install_stub(root)
    record = root / "stub-record.json"
    transcript = root / "stub-transcript.txt"
    env["PATH"] = f"{bindir}{os.pathsep}{env['PATH']}"
    env["STUB_RECORD"] = str(record)
    env["STUB_TRANSCRIPT"] = str(transcript)
    env["STUB_MODE"] = stub_mode
    env["STUB_RESULT"] = json.dumps(DEFAULT_RESULT if stub_result is None else stub_result)
    env["STUB_SECURITY_MARKER"] = str(root / "security-called.txt")
    done = subprocess.run([sys.executable, str(SCRIPT)] + args, capture_output=True, text=True,
                          env=env, timeout=timeout)
    RENDERED_TOKENS.update(re.findall(r"\[([a-z-]+)\]", done.stdout))
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
    empty.mkdir(exist_ok=True)
    clean_codex = make_codex_home(empty, ".codexClean")
    make_claude(empty, ".claudeA", {})                                     # no cache key
    make_claude(empty, ".claudeB", {"hasAvailableSubscription": False})    # no subscription
    make_claude(empty, ".claudeC", {"hasAvailableSubscription": False})    # both conditions
    done, _, _ = run(["--claude-profile", str(empty / ".claudeA"),
                      "--claude-profile", str(empty / ".claudeB"),
                      "--claude-profile", str(empty / ".claudeC"),
                      "--codex-home", str(clean_codex)], root=empty)
    check("7 absent cache is no-usage-cache, not 0%", "[no-usage-cache]" in done.stdout, done.stdout)
    check("8 no subscription is its own state", "[no-subscription]" in done.stdout, done.stdout)
    check("9 the combined shape resolves to exactly one state",
          done.stdout.count("[no-subscription]") == 2 and done.stdout.count("[no-usage-cache]") == 1,
          done.stdout)
    check("7/8 neither absence state gaps the run", done.returncode == 0,
          f"rc={done.returncode}\n{done.stdout}")
    check("7/8 and no warning is emitted for them", "warnings" not in done.stdout, done.stdout)

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
        check(f"12 container {label} -> the CLAUDE candidate is what gapped",
              ".claudeX" in done.stdout.split("warnings")[-1], done.stdout)

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
        ("resets_at parses but cannot be rendered", entry(resets="9999-12-31T23:59:59+00:00")),
    ):
        box = root / f"s-{abs(hash(label))}"
        make_claude(box, ".claudeY", cached(entries=[bad]))
        done, _, _ = run(["--claude-profile", str(box / ".claudeY"),
                          "--codex-home", str(box / ".nope")])
        check(f"13 scalar {label} -> field-malformed", "[field-malformed]" in done.stdout,
              done.stdout)
        check(f"13 scalar {label} -> the CLAUDE candidate is what gapped",
              ".claudeY" in done.stdout.split("warnings")[-1], done.stdout)

    for label, ms in (("fetchedAtMs null", None), ("fetchedAtMs bool", True),
                      ("fetchedAtMs negative", -5)):
        box = root / f"f-{abs(hash(label))}"
        box.mkdir(parents=True, exist_ok=True)
        ok_codex = make_codex_home(box, ".codexClean")
        make_claude(box, ".claudeZ", cached(entries=[entry()], fetched_ms=ms))
        done, _, _ = run(["--claude-profile", str(box / ".claudeZ"),
                          "--codex-home", str(ok_codex)], root=box)
        check(f"13 {label} -> gap", done.returncode == 1, done.stdout)
        check(f"13 {label} -> the gap is the Claude profile, not the Codex home",
              ".claudeZ" in done.stdout.split("warnings")[-1]
              and ".codexClean" not in done.stdout.split("warnings")[-1], done.stdout)

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

    # 15b -- DISCOVERY, not an explicit argument: a profile directory the process cannot look
    # into must become a gap. `Path.is_dir()`/`.exists()` answer False for a permission error from
    # 3.14, so a truth test drops it silently on exactly the interpreter CI pins.
    if os.geteuid() != 0:  # root bypasses the mode bits, so the case would prove nothing
        blocked_root = root / "blocked"
        blocked_home = blocked_root / "home"
        blocked_home.mkdir(parents=True, exist_ok=True)
        make_codex_home(blocked_home, ".codexClean")
        shut = blocked_home / ".claudeShut"
        shut.mkdir(exist_ok=True)
        (shut / ".claude.json").write_text("{}", encoding="utf-8")
        shut.chmod(0o000)
        try:
            done, _, _ = run([], root=blocked_root)
            check("15b an unreadable discovered profile is candidate-unreadable",
                  "[candidate-unreadable]" in done.stdout, done.stdout)
            check("15b it does not vanish before the ledger",
                  ".claudeShut" in done.stdout, done.stdout)
            check("15b and it gaps the run", done.returncode == 1, f"rc={done.returncode}")
        finally:
            shut.chmod(0o700)

    # N2 -- os.stat on the CANDIDATE ITSELF fails (a self-referential symlink is ELOOP, not
    # ENOENT), which is the branch a mode-0 directory never reaches.
    loop_root = root / "loop"
    loop_home = loop_root / "home"
    loop_home.mkdir(parents=True, exist_ok=True)
    make_codex_home(loop_home, ".codexClean")
    os.symlink(".claudeLoop", str(loop_home / ".claudeLoop"))
    done, _, _ = run([], root=loop_root)
    check("15c a candidate os.stat cannot resolve is candidate-unreadable",
          "[candidate-unreadable]" in done.stdout, done.stdout)
    check("15c it is named rather than dropped", ".claudeLoop" in done.stdout, done.stdout)
    check("15c and it gaps the run", done.returncode == 1, f"rc={done.returncode}")

    # N6 -- a profile carrying credentials but no usage cache is a real shape, and `no-usage-cache`
    # is a state the report declares; it has to be DISCOVERABLE for that state to be reachable.
    cred_root = root / "credonly"
    cred_home = cred_root / "home"
    cred_home.mkdir(parents=True, exist_ok=True)
    make_codex_home(cred_home, ".codexClean")
    cred_profile = cred_home / ".claudeCredOnly"
    cred_profile.mkdir(exist_ok=True)
    (cred_profile / ".credentials.json").write_text(json.dumps({
        "claudeAiOauth": {"accessToken": SENTINEL_TOKEN, "expiresAt": now_ms(24)}}),
        encoding="utf-8")
    done, _, _ = run([], root=cred_root)
    check("16b a credentials-only profile is discovered",
          ".claudeCredOnly" in done.stdout, done.stdout)
    check("16b and reports no-usage-cache rather than vanishing",
          "[no-usage-cache]" in done.stdout, done.stdout)
    assert_no_secret("16b credentials-only discovery", done.stdout, done.stderr)

    bare = root / "bare"
    bare.mkdir()
    done, _, _ = run([], root=bare, timeout=90)
    check("16 zero candidates for a vendor says so", "no candidates found" in done.stdout,
          done.stdout)
    tail = done.stdout.split("warnings")[-1]
    check("16 both vendors report it", "Claude Code:" in tail and "Codex:" in tail, done.stdout)
    check("16 and it gaps the run rather than exiting clean", done.returncode == 1,
          f"rc={done.returncode}")

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
    check("18 two windows of ONE pool sharing a duration carry their slot",
          "codex_twin/5h(primary)" in done.stdout and "codex_twin/5h(secondary)" in done.stdout,
          done.stdout)
    # The count itself, not merely the label: "1" occurs in every percentage on the page, so
    # `"1" in stdout` would stay green for any count the script chose to print.
    coupon_rows = [ln for ln in done.stdout.splitlines() if ln.strip().startswith("reset coupons")]
    check("4 exactly one coupon row is printed", len(coupon_rows) == 1, done.stdout)
    check("4 and it carries the fixture's own count",
          bool(coupon_rows) and coupon_rows[0].split()[2] == "1", str(coupon_rows))
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

    ok_claude = make_claude(codex_root, ".claudeClean", cached(entries=[entry()]))
    for mode, expect in (("error-reply", "appserver-protocol-error"),
                         ("wrong-id", "appserver-protocol-error"),
                         ("eof", "appserver-protocol-error"),
                         ("nonzero-exit", "appserver-")):
        done, _, _ = run(["--claude-profile", str(ok_claude),
                          "--codex-home", str(home)], root=codex_root, stub_mode=mode)
        check(f"19 app-server {mode} -> gap", expect in done.stdout, done.stdout)
        check(f"19 app-server {mode} -> exit 1", done.returncode == 1, f"rc={done.returncode}")
        check(f"19 app-server {mode}: the Claude side stayed clean and visible",
              "42.0%" in done.stdout and ".claudeClean" not in done.stdout.split("warnings")[-1],
              done.stdout)

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
        done, _, _ = run(["--claude-profile", str(ok_claude),
                          "--codex-home", str(home)], root=codex_root, stub_result=result)
        check(f"19 codex payload {label} -> exit 1", done.returncode == 1, done.stdout)
        check(f"19 codex payload {label}: the Claude row survives", "42.0%" in done.stdout,
              done.stdout)

    # N7 -- a child that streams without a newline must not be buffered without bound.
    done, _, _ = run(["--claude-profile", str(codex_root / ".claudeClean"),
                      "--codex-home", str(home)], root=codex_root, stub_mode="flood", timeout=180,
                     extra_env={"CODE_LIMITS_APPSERVER_TIMEOUT": "3"})
    # The CODE matters, not merely that it gapped: without the size cap this run reaches the
    # deadline instead and answers appserver-failed, which is a different defect being masked.
    # The deadline is deliberately short. The cap is reached in milliseconds either way, but the
    # UNCAPPED run buffers for the whole deadline, and a long one lets the parent exhaust memory
    # first -- which ends the child, reads as EOF, and answers appserver-protocol-error after
    # all. That would make this assertion pass for the wrong reason, on some machines only.
    check("19 a newline-free flood is refused by the size cap, not by the deadline",
          "[appserver-protocol-error]" in done.stdout
          and "[appserver-failed]" not in done.stdout, done.stdout)
    check("19 the flood still gaps the run", done.returncode == 1, f"rc={done.returncode}")

    # N4 -- a malformed OPTIONAL field must not discard the valid windows beside it.
    done, _, _ = run(["--claude-profile", str(codex_root / ".claudeClean"),
                      "--codex-home", str(home)], root=codex_root, stub_result={
        "rateLimits": {"limitId": "codex",
                       "primary": {"usedPercent": 61, "windowDurationMins": 10080,
                                   "resetsAt": epoch(50)},
                       "credits": {"hasCredits": True, "balance": "not-a-number"}}})
    check("19 a malformed credits balance gaps only itself", "61.0%" in done.stdout, done.stdout)
    check("19 and the malformed record is named", "[field-malformed]" in done.stdout, done.stdout)
    check("19 and the candidate still gaps", done.returncode == 1, f"rc={done.returncode}")

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
    live_codex = make_codex_home(live_root, ".codexClean")
    done, _, _ = run(["--live", "--claude-profile", str(profile),
                      "--codex-home", str(live_codex)], root=live_root)
    check("21 a missing token gaps the profile",
          "[token-absent]" in done.stdout or "[keychain-denied]" in done.stdout, done.stdout)
    check("21 it does NOT silently fall back to the cache", "88.0%" not in done.stdout, done.stdout)
    check("21 and the run gaps", done.returncode == 1, f"rc={done.returncode}")
    check("21 the gap is the Claude profile, not the Codex home",
          ".claudeL" in done.stdout.split("warnings")[-1]
          and ".codexClean" not in done.stdout.split("warnings")[-1], done.stdout)
    marker = live_root / "security-called.txt"
    check("21 the FIXTURE security ran -- proved by a marker the host binary cannot write",
          marker.exists() and "find-generic-password" in marker.read_text(encoding="utf-8"),
          marker.read_text(encoding="utf-8") if marker.exists() else "no marker written")
    check("21 the keychain failure maps to a closed code",
          "[keychain-denied]" in done.stdout, done.stdout)

# --- 22 / 23: transport safety. These import the module, deliberately, because reaching an HTTPS
# stub from a subprocess would need a production origin override -- the very defect they prevent.
sys.path.insert(0, str(SCRIPT.parent))
import contextlib as contextlib_module  # noqa: E402
import io as io_module  # noqa: E402
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
            R._claude_live(profile)
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
        raise_out, raise_err = io_module.StringIO(), io_module.StringIO()
        try:
            with contextlib_module.redirect_stdout(raise_out), \
                    contextlib_module.redirect_stderr(raise_err):
                R._claude_live(bad)
            code = "no-error"
        except R.Malformed as exc:
            code = exc.code
        check("22 the header ValueError maps to a closed code", code == "http-error", code)
        check("22 the code is a member of the closed enum", code in R.DIAGNOSTIC_SET, code)
        # The exception text embeds the whole bearer. Asserting only the code left a `print(exc)`
        # on this path green, and this is the load-bearing failure path.
        assert_no_secret("22 invalid-header path", raise_out.getvalue(), raise_err.getvalue(),
                         code)
    finally:
        R.HTTPSConnection = original

    # 25b -- the credential oracle around an execution that ACTUALLY READS the sentinel.
    #
    # Without this the oracle was vacuous: its only call sat on a run whose Claude profile did not
    # exist, so no token was ever opened and `print(token[:12])` inside _claude_live would have
    # passed. The recorded Authorization header is the proof that the read happened; the captured
    # streams are the thing under test.
    import contextlib, io  # noqa: E402

    class Recording:
        headers: list = []

        def __init__(self, host, timeout=None):
            self.host = host

        def request(self, method, path, headers=None):
            Recording.headers.append(dict(headers or {}))

        def getresponse(self):
            payload = json.dumps({"limits": [
                {"kind": "weekly_all", "percent": 5, "is_active": True, "resets_at": iso(48)}]})

            class Response:
                status = 200
                def read(self):
                    return payload.encode("utf-8")
            return Response()

        def close(self):
            pass

    reader = make_claude(root, ".claudeRead", cached(entries=[entry()]))
    original = R.HTTPSConnection
    out_buf, err_buf = io.StringIO(), io.StringIO()
    try:
        R.HTTPSConnection = Recording
        with contextlib.redirect_stdout(out_buf), contextlib.redirect_stderr(err_buf):
            produced = R._claude_live(reader)
            for record in produced:
                print(record.render())
    finally:
        R.HTTPSConnection = original

    check("25b the token really WAS read (else the oracle proves nothing)",
          any(SENTINEL_TOKEN in header.get("Authorization", "")
              for header in Recording.headers), str(len(Recording.headers)))
    check("25b the live row rendered, so the renderer was exercised",
          any(record.state == R.REPORTED for record in produced),
          str([record.state for record in produced]))
    assert_no_secret("25b live read", out_buf.getvalue(), err_buf.getvalue(),
                     repr([vars(record) for record in produced]))

    # 24 -- every diagnostic token the script can render is a member of the closed enum.
    check("24 the enum has a total fallback", "internal-error" in R.DIAGNOSTIC_SET)
    check("24 the enum is a frozenset of unique tokens",
          len(R.DIAGNOSTICS) == len(R.DIAGNOSTIC_SET))
    check("24 every diagnostic this suite actually RENDERED is a member of the enum",
          RENDERED_TOKENS <= R.DIAGNOSTIC_SET, str(sorted(RENDERED_TOKENS - R.DIAGNOSTIC_SET)))
    check("24 the suite rendered a substantial share of the enum, not one token",
          len(RENDERED_TOKENS) >= 8, str(sorted(RENDERED_TOKENS)))
    check("24 the enum has a total fallback so nothing must invent a message",
          "internal-error" in R.DIAGNOSTIC_SET)

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

MIN_CHECKS = 140
if checks < MIN_CHECKS:
    print(f"FAIL: only {checks} checks ran, expected at least {MIN_CHECKS}")
    sys.exit(1)
print("PASS")
