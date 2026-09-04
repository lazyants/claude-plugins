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

import ast
import datetime
import difflib
import hashlib
import json
import os
import pty
import re
import subprocess
import sys
import tempfile
import threading
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
payload = os.environ.get("STUB_SECURITY_PAYLOAD")
if payload is not None:          # the SUCCESS path; absent, this stub denies as before
    sys.stdout.write(payload)
    sys.exit(0)
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


def voucher_band(stdout: str) -> list[str]:
    """The rows of the RESET VOUCHERS band, which is a block above the table rather than a row
    inside it. Parsed structurally -- a row is keyed by its HOME, so there is no per-row label to
    grep for, and slicing the band is the only way to read a count without matching a percentage
    somewhere else on the page."""
    lines = stdout.splitlines()
    for index, line in enumerate(lines):
        # Matched on containment, not on a prefix: with --color=always the heading opens with an
        # escape, and a prefix test would silently find no band at all -- which reads exactly
        # like a report that printed none.
        if "RESET VOUCHERS" in line:
            rows = []
            for row in lines[index + 1:]:
                if not row.strip():
                    break
                rows.append(row.strip())
            return rows
    return []


def pool_rows(stdout: str) -> list[tuple[str, str, str]]:
    """(where, pool, whole line) per row of the pool table, read STRUCTURALLY.

    A row whose candidate repeats the row above prints a blank WHERE cell, so `line.split()[0]`
    is the candidate on some rows and the POOL on others -- a test that indexes into the split
    reads the wrong column on exactly the rows the grouping exists for. The candidate is carried
    down here the same way the eye carries it.
    """
    lines = stdout.splitlines()
    for index, line in enumerate(lines):
        if "\u2500\u2500" not in line:
            continue
        head = lines[index - 1]
        where_at, pool_at = head.index("WHERE"), head.index("POOL")
        rows, carried = [], ""
        for row in lines[index + 1:]:
            if not row.strip():
                break
            where = row[where_at:pool_at].strip() or carried
            carried = where
            rows.append((where, row[pool_at:].split()[0], row))
        return rows
    return []


def voucher_rows(stdout: str) -> list[str]:
    """The band's voucher rows only. The credits balance shares the block and is keyed by POOL
    rather than by home, so its fields sit one column across -- reading a count off it would be
    reading the wrong row entirely."""
    return [row for row in voucher_band(stdout) if not row.startswith("credits")]


def iso(delta_hours: float) -> str:
    when = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=delta_hours)
    return when.isoformat()


def epoch(delta_hours: float) -> int:
    when = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=delta_hours)
    return int(when.timestamp())


def now_ms(delta_hours: float = 0.0) -> int:
    when = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=delta_hours)
    return int(when.timestamp() * 1000)


def make_claude(root: Path, name: str, blob, token: bool = True) -> Path:
    profile = root / name
    profile.mkdir(parents=True, exist_ok=True)
    if blob is not None:
        (profile / ".claude.json").write_text(json.dumps(blob), encoding="utf-8")
    # Planted in every profile: the script reads this file on the --live path.
    #
    # `token=False` withholds it, and every STALE fixture uses that. Default mode retries a
    # cached window whose reset has passed against the API, so a stale fixture holding a usable
    # credential would send this suite's sentinel bearer to the real api.anthropic.com -- on
    # every developer machine and every CI run. Withheld, the retry stops at `token-absent`,
    # before any socket, which is also the only outcome an offline runner could agree on.
    if token:
        (profile / ".credentials.json").write_text(json.dumps({
            "claudeAiOauth": {"accessToken": SENTINEL_TOKEN, "expiresAt": now_ms(24)}
        }), encoding="utf-8")
    return profile


UNSET = object()


def cached(entries=None, flat=None, fetched_ms=UNSET, subscription=None, utilization=None):
    blob: dict = {}
    if subscription is not None:
        blob["hasAvailableSubscription"] = subscription
    # `utilization` REPLACES the container; the precedence is written first so it cannot look
    # like it composes with the two below it, which is how a fixture passing both would silently
    # lose them.
    inner: dict = {}
    if utilization is not None:
        inner = utilization
    else:
        if entries is not None:
            inner["limits"] = entries
        if flat is not None:
            inner.update(flat)
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
        extra_env: Any = None, cwd: Any = None):
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
                          env=env, timeout=timeout, cwd=None if cwd is None else str(cwd))
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
    # Structural, not a substring pin. Every `"method"` value in the module must be a string
    # LITERAL, and the set of those literals must be exactly the three read-only messages. The
    # old check asserted three constant NAMES were present, which stayed true for a mutant
    # adding `def _dynamic_rpc(operation): return {"method": operation}` beside them.
    method_values = [
        value
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Dict)
        for key, value in zip(node.keys, node.values)
        if isinstance(key, ast.Constant) and key.value == "method"
    ]
    check("1 every RPC method is a literal, never an expression",
          bool(method_values) and all(isinstance(v, ast.Constant) and isinstance(v.value, str)
                                      for v in method_values),
          str([ast.dump(v)[:70] for v in method_values]))
    check("1 the only methods the module can name are the three read-only ones",
          sorted(v.value for v in method_values
                 if isinstance(v, ast.Constant) and isinstance(v.value, str))
          == ["account/rateLimits/read", "initialize", "initialized"],
          str([ast.dump(v)[:70] for v in method_values]))
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
                                               fetched_ms=now_ms(-72)), token=False)
    done, _, _ = run(["--claude-profile", str(stale_root / ".claude9"),
                      "--codex-home", str(stale_root / ".nope")])
    check("5 past reset renders stale-after-reset", "stale-after-reset" in done.stdout, done.stdout)
    check("5 the stale percentage is labelled as the previous window",
          "is the PREVIOUS window" in done.stdout and "67%" in done.stdout, done.stdout)
    # The CELL carries the caveat, not a heading above a block of rows: a window whose reset has
    # passed renders its own `... ago`, which is the whole reason the figure beside it is the
    # previous one. Without that the row reads exactly like a current one.
    row = [ln for ln in done.stdout.splitlines() if "67%" in ln]
    check("5 it is not presented as a current row",
          len(row) == 1 and "ago" in row[0], str(row))
    check("6 the row carries its own cache age", "3d00h" in done.stdout, done.stdout)

    # 7 / 8 / 9 -- an absent cache is ONE state, whatever the subscription flag beside it says.
    empty = root / "states"
    empty.mkdir(exist_ok=True)
    clean_codex = make_codex_home(empty, ".codexClean")
    make_claude(empty, ".claudeA", {})                                     # no flag at all
    make_claude(empty, ".claudeB", {"hasAvailableSubscription": False})    # flag says no
    make_claude(empty, ".claudeC", {"hasAvailableSubscription": True})     # flag says yes
    done, _, _ = run(["--claude-profile", str(empty / ".claudeA"),
                      "--claude-profile", str(empty / ".claudeB"),
                      "--claude-profile", str(empty / ".claudeC"),
                      "--codex-home", str(clean_codex)], root=empty)
    check("7 absent cache is no-usage-cache, not 0%", "[no-usage-cache]" in done.stdout, done.stdout)
    check("8 the flag never renames the absence",
          "[no-subscription]" not in done.stdout, done.stdout)
    check("9 all three profiles land in that one state",
          done.stdout.count("[no-usage-cache]") == 3, done.stdout)
    check("7/8 the absence state does not gap the run", done.returncode == 0,
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
    # `.claudeD` carries NOTHING BUT the scoped pool this report is told to hide -- not a shape
    # the vendor produces, since `session` and `weekly_all` ship beside it. Putting it back was a
    # substitution like any other: the report would print the pool it was told to hide and call
    # the run clean. It gaps instead, visibly.
    check("10 a profile whose only entry is the hidden pool gaps",
          ".claudeD" in done.stdout and "[payload-malformed]" in done.stdout, done.stdout)
    check("10 and the hidden pool is not put back on the page",
          "Fable" not in done.stdout and "19%" not in done.stdout, done.stdout)
    flat = [ln for ln in done.stdout.splitlines() if ".claudeE" in ln]
    check("11 the flat fallback says which container shape answered",
          len(flat) == 1 and "(flat)" in flat[0], str(flat))
    check("11 and reports both of its windows on that one row",
          bool(flat) and "11%" in flat[0] and "22%" in flat[0], str(flat))

    # 12 -- container table: shapes that must gap BEFORE any record is formed.
    for label, blob in (
        ("limits null", cached(utilization={"limits": None})),
        ("limits is an object", cached(utilization={"limits": {}})),
        ("utilization not an object", {"cachedUsageUtilization":
                                       {"fetchedAtMs": now_ms(-1), "utilization": []}}),
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

    # 13 -- scalar table. The last two rows pin TZ, and that is not incidental: whether a
    # timestamp at the edge of the range can be rendered depends on the SIGN of the local UTC
    # offset, so an unpinned fixture tests the runner rather than the code. The max stamp
    # overflows only east of Greenwich and the min stamp only west of it -- on a UTC runner both
    # render cleanly, which is how the first version of this passed locally (CET) and went red in
    # CI. POSIX offset strings are used rather than zone names so no tzdata is required.
    for label, bad, env in (
        ("percent null", entry(percent=None), None),
        ("percent is a bool", entry(percent=True), None),
        ("percent out of range", entry(percent=140), None),
        ("percent is a string", entry(percent="42"), None),
        ("resets_at unparseable", entry(resets="not-a-date"), None),
        ("resets_at naive", entry(resets="2026-08-27T03:00:00"), None),
        ("is_active is truthy not bool", entry(active=1), None),
        ("kind is not a string", entry(kind=7), None),
        ("kind has a control character", entry(kind="week\x01ly"), None),
        ("a max resets_at east of Greenwich cannot be rendered",
         entry(resets="9999-12-31T23:59:59+00:00"), {"TZ": "XXX-14"}),
        ("a min resets_at west of Greenwich cannot be rendered",
         entry(resets="0001-01-01T00:00:00+00:00"), {"TZ": "XXX+12"}),
    ):
        box = root / f"s-{abs(hash(label))}"
        make_claude(box, ".claudeY", cached(entries=[bad]))
        done, _, _ = run(["--claude-profile", str(box / ".claudeY"),
                          "--codex-home", str(box / ".nope")], extra_env=env)
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
    check("14 the VALID row survives beside it", "46%" in done.stdout, done.stdout)
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
    pools = {pool: line for where, pool, line in pool_rows(done.stdout) if where == ".codexT"}
    # `rateLimitsByLimitId` enumerates pools a person at a terminal is not asking about -- a
    # model-specific one and a reserve. Only the pool the top-level `rateLimits` names answers
    # "how much can I still use here", and that is the one the report shows.
    check("18 only the pool the CLI spends from is shown", set(pools) == {"codex"}, str(pools))
    check("18 and the pools beside it are not on the page",
          "codex_bengalfox" not in done.stdout and "codex_twin" not in done.stdout, done.stdout)

    def pointed_at(limit_id):
        result = dict(DEFAULT_RESULT,
                      rateLimits=dict(DEFAULT_RESULT["rateLimits"], limitId=limit_id))
        run_done, _, _ = run(["--claude-profile", str(codex_root / ".nope"),
                              "--codex-home", str(home)], root=codex_root, stub_result=result)
        return run_done, {pool: line for where, pool, line in pool_rows(run_done.stdout)
                          if where == ".codexT"}

    spark, pools_5h = pointed_at("codex_bengalfox")
    header = [ln for ln in spark.stdout.splitlines() if "SOURCE" in ln][0]
    check("18 the 300-minute window is labelled 5h", "5H" in header, repr(header))
    check("18 and the pool's 5h figure sits under it, its weekly one after",
          "codex_bengalfox" in pools_5h
          and pools_5h["codex_bengalfox"].index("3%") < pools_5h["codex_bengalfox"].index("7%"),
          str(pools_5h))
    # The UNIT, not merely the presence. Read as milliseconds, epoch(72) lands in January 1970
    # and every future window renders `stale-after-reset` -- a KNOWN_ABSENT-adjacent state that
    # exits 0 and prints no warning, so every other assertion in this file stays green.
    check("18 a FUTURE codex reset renders as a current row, not a stale one",
          "[stale-after-reset]" not in done.stdout, done.stdout)
    expected_day = (datetime.datetime.now(datetime.timezone.utc)
                    + datetime.timedelta(hours=72)).astimezone().strftime("%d %b")
    # Relative now. A SECONDS epoch read as milliseconds lands ~55 000 years out and a
    # milliseconds epoch read as seconds lands in 1970, so a plausible three-day span is what
    # pins the unit -- more directly than the calendar date it used to print.
    check("18 and its reset lands in the present, which is what pins the epoch unit",
          "in 2d 23h" in done.stdout or "in 3d 00h" in done.stdout, done.stdout)
    _twin, pools_twin = pointed_at("codex_twin")
    check("18 two windows of ONE pool sharing a duration carry their slot",
          {"codex_twin(primary)", "codex_twin(secondary)"} <= set(pools_twin),
          str(sorted(pools_twin)))
    # A pointer the MAP does not hold is answered by the TOP-LEVEL object, which carries that
    # same pool's windows -- returning the whole map there dropped the only pool the operator
    # asked about and printed the others instead, on a run that exited 0.
    _named, pools_named = pointed_at("codex_absent")
    check("18 a pointer the map lacks is answered by the top-level pool",
          set(pools_named) == {"codex_absent"}, str(sorted(pools_named)))
    check("18 and that pool carries the top-level figure, not another pool's",
          "54%" in "".join(pools_named.values()), str(pools_named))

    # A selector that resolves to NEITHER a mapped pool nor a windowed top-level object gaps the
    # home. Every substitute tried here printed a reserve or a model-specific pool in the place
    # the operator reads their terminal quota, and exited 0 doing it.
    windowless, _, _ = run(["--claude-profile", str(codex_root / ".nope"),
                            "--codex-home", str(home)], root=codex_root,
                           stub_result=dict(DEFAULT_RESULT,
                                            rateLimits={"limitId": "codex_absent"}))
    pools_kept = {pool for where, pool, _l in pool_rows(windowless.stdout) if where == ".codexT"}
    check("18 a selector that resolves to nothing shows no usage pool at all",
          pools_kept == {"rateLimits"}, str(sorted(pools_kept)))
    check("18 it gaps rather than substituting another pool",
          "[payload-malformed]" in windowless.stdout and windowless.returncode == 1,
          f"rc={windowless.returncode}\n{windowless.stdout}")
    check("18 and names no other pool on the page",
          "codex_bengalfox" not in windowless.stdout, windowless.stdout)
    # One unreadable thing may not suppress its siblings: the voucher count was never part of
    # the question "which pool does the CLI spend from", and is still perfectly readable.
    vouchers_kept = [row for row in voucher_rows(windowless.stdout) if ".codexT" in row]
    check("18 while the voucher count beside it survives the gap",
          len(vouchers_kept) == 1 and vouchers_kept[0].split()[1] == "1", str(vouchers_kept))
    # The count itself, not merely the label: "1" occurs in every percentage on the page, so
    # `"1" in stdout` would stay green for any count the script chose to print.
    coupon_rows = voucher_rows(done.stdout)
    check("4 exactly one voucher row is printed", len(coupon_rows) == 1, done.stdout)
    check("4 and it carries the fixture's own count",
          bool(coupon_rows) and coupon_rows[0].split()[1] == "1", str(coupon_rows))
    check("4 the credit balance is printed", "347.89" in done.stdout, done.stdout)
    check("2 the transcript is exactly the three allowed messages, in order",
          [json.loads(line).get("method") for line in
           transcript.read_text(encoding="utf-8").splitlines() if line.strip()]
          == ["initialize", "initialized", "account/rateLimits/read"],
          transcript.read_text(encoding="utf-8"))
    assert_no_secret("25 codex run", done.stdout, done.stderr,
                     json.dumps(json.loads(record.read_text(encoding="utf-8"))))

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
              "42%" in done.stdout and ".claudeClean" not in done.stdout.split("warnings")[-1],
              done.stdout)

    for label, result in (
        ("no rateLimits at all", {"rateLimitsByLimitId": {}}),
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
        check(f"19 codex payload {label}: the Claude row survives", "42%" in done.stdout,
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
    check("19 a malformed credits balance gaps only itself", "61%" in done.stdout, done.stdout)
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
    check("21 it does NOT silently fall back to the cache", "88%" not in done.stdout, done.stdout)
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

# --- 26 - 31: the round-3 findings, each in a confined root ------------------------------------

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)

    # 26 -- one non-object entry must gap ITSELF, not its valid siblings. A container pass ahead
    # of the per-record loop rejected the whole candidate and lost every valid pool with it.
    mixed = root / "mixed"
    # A valid entry on EITHER side of the malformed one. With the only valid entry first, a
    # `break` after appending the gap row leaves this case green while later siblings vanish.
    make_claude(mixed, ".claudeM",
                cached(entries=[entry(percent=42), "not-an-object", entry(percent=63)]))
    done, _, _ = run(["--claude-profile", str(mixed / ".claudeM"),
                      "--codex-home", str(make_codex_home(mixed, ".codexClean"))], root=mixed)
    # Counted in the report BODY: the warning now quotes the gapped row verbatim, so counting
    # the whole of stdout would see the same diagnostic twice and say nothing about the rows.
    body = done.stdout.split("warnings")[0]
    check("26 the malformed entry gaps under its own index",
          "limits[1]" in body and body.count("[payload-malformed]") == 1, done.stdout)
    check("26 the warning quotes the gapped row rather than inventing a token",
          "limits[1] [payload-malformed]" in done.stdout.split("warnings")[-1], done.stdout)
    check("26 the VALID sibling before it still reports", "42%" in done.stdout, done.stdout)
    check("26 the VALID sibling AFTER it still reports", "63%" in done.stdout, done.stdout)
    check("26 the run gaps", done.returncode == 1, f"rc={done.returncode}")
    check("26 and it is the Claude profile the warning names",
          ".claudeM" in done.stdout.split("warnings")[-1], done.stdout)

    # 27 -- the subscription flag is not a field this report reads, so NOTHING it can hold
    # changes the answer to an absent cache. It used to: a boolean false renamed the absence
    # `no-subscription` and every other type gapped the profile as schema drift -- two verdicts
    # about billing, from a key measured false on live Max 20x accounts.
    for index, junk in enumerate((False, True, "false", 0, 1, None, [], {})):
        drift = root / f"drift{index}"
        make_claude(drift, ".claudeS", {"hasAvailableSubscription": junk})
        done, _, _ = run(["--claude-profile", str(drift / ".claudeS"),
                          "--codex-home", str(make_codex_home(drift, ".codexClean"))], root=drift)
        check(f"27 flag {junk!r} still reads no-usage-cache", "[no-usage-cache]" in done.stdout,
              done.stdout)
        check(f"27 flag {junk!r} claims nothing about a subscription",
              "[no-subscription]" not in done.stdout and "[field-malformed]" not in done.stdout,
              done.stdout)
        check(f"27 flag {junk!r} exits 0", done.returncode == 0, f"rc={done.returncode}")

    # 28 -- the coupon count is one of the things this report exists to print, so a response that
    # omits the container has not answered the question and must not print nothing and pass.
    nocoupon = root / "nocoupon"
    make_claude(nocoupon, ".claudeOK", cached(entries=[entry()]))
    done, _, _ = run(["--claude-profile", str(nocoupon / ".claudeOK"),
                      "--codex-home", str(make_codex_home(nocoupon, ".codexN"))], root=nocoupon,
                     stub_result={"rateLimits": {"limitId": "codex", "primary": {
                         "usedPercent": 61, "windowDurationMins": 10080, "resetsAt": epoch(30)}}})
    # The vendor's schema makes this container optional AND nullable, so its absence is the
    # backend declining to answer -- reported as a known absence, never gapped. What must still
    # gap is a container the schema does NOT allow, which is the second half below.
    check("28 an omitted voucher container is reported as a known absence, not a gap",
          any("not reported" in row for row in voucher_band(done.stdout)), done.stdout)
    check("28 it is not a silent omission either -- the row is printed",
          len(voucher_rows(done.stdout)) == 1,
          str(voucher_band(done.stdout)))
    check("28 the usage window beside it still reports", "61%" in done.stdout, done.stdout)
    check("28 and the run stays clean", done.returncode == 0,
          f"rc={done.returncode}\n{done.stdout}")

    # 28b -- the shape the schema forbids. availableCount is `required` and an integer, so a
    # present container without it is drift and must gap.
    done, _, _ = run(["--claude-profile", str(nocoupon / ".claudeOK"),
                      "--codex-home", str(make_codex_home(nocoupon, ".codexBad"))],
                     root=nocoupon,
                     stub_result={"rateLimits": {"limitId": "codex", "primary": {
                         "usedPercent": 61, "windowDurationMins": 10080, "resetsAt": epoch(30)}},
                         "rateLimitResetCredits": {"notTheCount": 1}})
    check("28b a present container missing its required count gaps",
          "reset vouchers" in done.stdout and "[field-malformed]" in done.stdout, done.stdout)
    check("28b the run exits 1", done.returncode == 1, f"rc={done.returncode}")
    check("28b the warning names the Codex home, not the clean Claude profile",
          ".codexBad" in done.stdout.split("warnings")[-1]
          and ".claudeOK" not in done.stdout.split("warnings")[-1], done.stdout)

    # 29 -- an escaped lone surrogate survives json.loads and raises only when something ENCODES
    # it, which happens in the renderer, outside the per-candidate handler.
    surrogate = root / "surrogate"
    # Both ends of the range: rejecting only U+D800 left the other 2 047 code points live.
    make_claude(surrogate, ".claudeU",
                cached(entries=[entry(percent=42), entry(kind="\ud800weekly"),
                                entry(kind="weekly\udfff")]))
    done, _, _ = run(["--claude-profile", str(surrogate / ".claudeU"),
                      "--codex-home", str(make_codex_home(surrogate, ".codexClean"))],
                     root=surrogate)
    check("29 the surrogate label gaps its own record",
          "limits[1]" in done.stdout and "[field-malformed]" in done.stdout, done.stdout)
    # Counted in the BODY: the warning quotes each gapped row verbatim, so a whole-stdout
    # count sees every diagnostic twice and stops saying anything about the rows.
    surr_body = done.stdout.split("warnings")[0]
    check("29 the OTHER end of the surrogate range gaps too",
          "limits[2]" in surr_body and surr_body.count("[field-malformed]") == 2, done.stdout)
    check("29 the valid sibling still reports", "42%" in done.stdout, done.stdout)
    check("29 no traceback escaped to stderr", "Traceback" not in done.stderr, done.stderr)

    # 30 -- under an ascii stdout an unencodable character raises out of print(), past every
    # per-candidate handler, ending the run with no warnings at all. The DIRECTORY name is the
    # half that _text cannot cover: it is not a vendor field and not this script's to validate,
    # so only the stream configuration can carry it. Both halves are in this one fixture.
    narrow = root / "narrow"
    make_claude(narrow, ".claudeW\u00e9", cached(entries=[entry()]))
    # The vendor string is a Codex pool NAME, which is what the pool column prints now that the
    # model-scoped Claude pool is not laid out at all.
    done, _, _ = run(["--claude-profile", str(narrow / ".claudeW\u00e9"),
                      "--codex-home", str(make_codex_home(narrow, ".codexClean"))],
                     root=narrow, extra_env={"PYTHONIOENCODING": "ascii"},
                     stub_result={"rateLimits": {"limitId": "codex"},
                                  "rateLimitsByLimitId": {"codex": {
                                      "limitId": "codex", "limitName": "Fabl\u00e9",
                                      "primary": {"usedPercent": 19,
                                                  "windowDurationMins": 10080,
                                                  "resetsAt": epoch(48)}}},
                                  "rateLimitResetCredits": {"availableCount": 0}})
    check("30 an ascii stdout does not abort the report",
          "UnicodeEncodeError" not in done.stderr and "Traceback" not in done.stderr, done.stderr)
    check("30 the vendor field is escaped rather than lost",
          "Fabl\\xe9" in done.stdout, done.stdout)
    check("30 the profile DIRECTORY name is escaped rather than aborting the report",
          ".claudeW\\xe9" in done.stdout, done.stdout)
    check("30 and the run is otherwise clean", done.returncode == 0,
          f"rc={done.returncode}\n{done.stdout}\n{done.stderr}")

    # 31 -- a credential file that EXISTS but cannot be read must not read as absent. Path.exists()
    # answers False for a permission failure from 3.14, so testing for the file first falls
    # through to the keychain and reports a DIFFERENT credential's pool as this profile's.
    if os.geteuid() != 0:                 # root bypasses the permission bits this case needs
        locked = root / "locked"
        prof = make_claude(locked, ".claudeP", cached(entries=[entry(percent=77)]))
        clean = make_codex_home(locked, ".codexClean")
        prof.chmod(0o000)
        try:
            done, _, _ = run(["--live", "--claude-profile", str(prof),
                              "--codex-home", str(clean)], root=locked)
        finally:
            prof.chmod(0o700)
        marker = locked / "security-called.txt"
        check("31 an unreadable credential file gaps the profile",
              "[candidate-unreadable]" in done.stdout, done.stdout)
        check("31 it never falls back to the keychain -- the fixture security left no marker",
              not marker.exists(),
              marker.read_text(encoding="utf-8") if marker.exists() else "")
        check("31 the cache is not used as a fallback either", "77%" not in done.stdout,
              done.stdout)
        check("31 and the run gaps", done.returncode == 1, f"rc={done.returncode}")


# --- 32: shapes the VENDOR'S OWN SCHEMA declares nullable or optional ---------------------------
#
# Source of truth, re-derivable: openai/codex, codex-rs/app-server-protocol/schema/json/v2/
# GetAccountRateLimitsResponse.json. Only `rateLimits` is in `required`; every field below is
# `... | null`, or absent from a `required` list, or both. A null there is the backend declining
# to answer, not schema drift -- so it must NOT gap. Gapping it would leave an account whose
# backend returns one unable to produce a clean report no matter what its owner did, which is the
# failure mode this whole report is built to avoid, pointed the other way.

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    OK_WINDOW = {"usedPercent": 5, "windowDurationMins": 10080, "resetsAt": epoch(3)}
    OK_POOL = {"limitId": "codex", "primary": OK_WINDOW}
    COUPONS = {"availableCount": 0}
    nullroot = root / "nullable"
    ok_claude = make_claude(nullroot, ".claudeClean", cached(entries=[entry()]))
    nullhome = make_codex_home(nullroot, ".codexNull")

    for label, result, expect in (
        ("rateLimitResetCredits absent", {"rateLimits": OK_POOL}, "not reported"),
        ("rateLimitResetCredits null",
         {"rateLimits": OK_POOL, "rateLimitResetCredits": None}, "not reported"),
        ("rateLimitsByLimitId null",
         {"rateLimits": OK_POOL, "rateLimitsByLimitId": None, "rateLimitResetCredits": COUPONS},
         "5%"),
        ("windowDurationMins null",
         {"rateLimits": {"limitId": "codex", "primary": dict(OK_WINDOW,
                                                             windowDurationMins=None)},
          "rateLimitResetCredits": COUPONS}, "PRIMARY"),
        ("windowDurationMins absent",
         {"rateLimits": {"limitId": "codex", "primary": {"usedPercent": 5,
                                                         "resetsAt": epoch(3)}},
          "rateLimitResetCredits": COUPONS}, "PRIMARY"),
        ("resetsAt null",
         {"rateLimits": {"limitId": "codex", "primary": dict(OK_WINDOW, resetsAt=None)},
          "rateLimitResetCredits": COUPONS}, "not reported"),
        ("limitId null",
         {"rateLimits": {"limitId": None, "primary": OK_WINDOW},
          "rateLimitResetCredits": COUPONS}, "5%"),
        ("credits null",
         {"rateLimits": {"limitId": "codex", "credits": None, "primary": OK_WINDOW},
          "rateLimitResetCredits": COUPONS}, "5%"),
        ("secondary null",
         {"rateLimits": {"limitId": "codex", "secondary": None, "primary": OK_WINDOW},
          "rateLimitResetCredits": COUPONS}, "5%"),
        # `usedPercent` is an UNBOUNDED int32 in that schema. A pool past its limit is the moment
        # the report is most worth reading, so refusing the number would be the worst possible
        # time to gap.
        ("usedPercent past 100",
         {"rateLimits": {"limitId": "codex", "primary": dict(OK_WINDOW, usedPercent=137)},
          "rateLimitResetCredits": COUPONS}, "137%"),
    ):
        done, _, _ = run(["--claude-profile", str(ok_claude), "--codex-home", str(nullhome)],
                         root=nullroot, stub_result=result)
        check(f"32 schema-nullable {label} does not gap the run",
              done.returncode == 0, f"rc={done.returncode}\n{done.stdout}")
        check(f"32 schema-nullable {label} emits no warning",
              "warnings" not in done.stdout, done.stdout)
        check(f"32 schema-nullable {label} still reports its row",
              expect in done.stdout, done.stdout)

    # `5%` proves a figure reached the page but not WHICH allowance it belongs to, and a null
    # limitId is exactly the case where the name is supplied by this script rather than read.
    done, _, _ = run(["--claude-profile", str(ok_claude), "--codex-home", str(nullhome)],
                     root=nullroot, stub_result={"rateLimits": {"limitId": None,
                                                                "primary": OK_WINDOW},
                                                 "rateLimitResetCredits": COUPONS})
    named = [pool for where, pool, _line in pool_rows(done.stdout) if where == ".codexNull"]
    check("32 a null limitId falls back to the pool name `codex`",
          named == ["codex"], str(named))

    # 33 -- a valid record whose sibling raises something OTHER than Malformed. The per-record
    # handler's comment claims one bad entry cannot suppress the rest; only catching Malformed
    # made that false for a 400-digit integer, which raises OverflowError inside float().
    huge = root / "huge"
    make_claude(huge, ".claudeH", cached(entries=[
        entry(percent=42), entry(kind="weekly_huge", percent=10 ** 400), entry(percent=63)]))
    done, _, _ = run(["--claude-profile", str(huge / ".claudeH"),
                      "--codex-home", str(make_codex_home(huge, ".codexClean"))], root=huge)
    check("33 the overflowing record gaps under its own index",
          "limits[1]" in done.stdout, done.stdout)
    check("33 the record BEFORE it still reports", "42%" in done.stdout, done.stdout)
    check("33 the record AFTER it still reports too", "63%" in done.stdout, done.stdout)
    check("33 and the diagnostic is a member of the closed enum, not a traceback",
          "Traceback" not in done.stderr, done.stderr)

    # 33b -- the same claim on the CODEX side, which has its own per-record handler and needed
    # the same total catch. A 400-digit usedPercent raises OverflowError inside float().
    ovf = root / "ovf"
    done, _, _ = run(["--claude-profile", str(make_claude(ovf, ".claudeClean",
                                                          cached(entries=[entry()]))),
                      "--codex-home", str(make_codex_home(ovf, ".codexO"))], root=ovf,
                     stub_result={"rateLimits": {"limitId": "codex_bad"},
                                  "rateLimitsByLimitId": {
                         "codex_bad": {"limitId": "codex_bad", "primary": {
                             "usedPercent": 10 ** 400, "windowDurationMins": 300,
                             "resetsAt": epoch(9)},
                             "secondary": {
                             "usedPercent": 5, "windowDurationMins": 10080,
                             "resetsAt": epoch(9)}}},
                         "rateLimitResetCredits": {"availableCount": 2}})
    gapped = [pool for _w, pool, line in pool_rows(done.stdout) if "[internal-error]" in line]
    check("33b the overflowing codex window gaps under its own pool and slot",
          gapped == ["codex_bad"], str(gapped))
    check("33b the VALID sibling window on that same pool still reports",
          any("5%" in line for _w, _p, line in pool_rows(done.stdout)), done.stdout)
    coupon_rows_33b = voucher_rows(done.stdout)
    check("33b and the voucher row beside it still reports its own count",
          len(coupon_rows_33b) == 1 and coupon_rows_33b[0].split()[1] == "2",
          str(coupon_rows_33b))
    check("33b no traceback escaped", "Traceback" not in done.stderr, done.stderr)

    # 33c -- a non-finite percentage, on the CODEX side, which is the only side where the guard
    # can bite. `json.dumps` writes float("inf") as the bare literal `Infinity` and `json.loads`
    # reads it back by default, so this is a shape a vendor really can send. Claude percentages
    # are bounded 0..100, so the range comparison refuses inf there whatever the guard does --
    # a Claude-side fixture left the mutant GREEN. Codex `usedPercent` is bounded by PERCENT_MAX,
    # which is +inf precisely because the vendor's schema puts no ceiling on it, so there the
    # range test admits inf and the row would render "inf%" as though it were a measurement.
    nonfinite = root / "nonfinite"
    make_claude(nonfinite, ".claudeF", cached(entries=[entry(percent=42)]))
    done, _, _ = run(["--claude-profile", str(nonfinite / ".claudeF"),
                      "--codex-home", str(make_codex_home(nonfinite, ".codexF"))],
                     root=nonfinite,
                     stub_result={"rateLimits": {"limitId": "codex", "primary": {
                         "usedPercent": float("inf"), "windowDurationMins": 10080,
                         "resetsAt": epoch(30)}},
                         "rateLimitResetCredits": {"availableCount": 1}})
    check("33c an infinite codex percentage gaps its own window",
          "codex/primary" in done.stdout and "[field-malformed]" in done.stdout, done.stdout)
    check("33c it is never rendered as a measurement", "inf%" not in done.stdout, done.stdout)
    check("33c the voucher row beside it still reports",
          any(row.split()[1:2] == ["1"] for row in voucher_band(done.stdout)),
          str(voucher_band(done.stdout)))
    check("33c and the Claude side stays clean and visible",
          "42%" in done.stdout and ".claudeF" not in done.stdout.split("warnings")[-1],
          done.stdout)
    check("33c the run gaps", done.returncode == 1, f"rc={done.returncode}")

    # 33d -- the FLAT shape's own per-record boundary. This is the site that had drifted: it
    # validated the entry outside its handler and caught only Malformed, so one bad `five_hour`
    # took `seven_day` with it. Both failure kinds are pinned, because the two arms of the shared
    # boundary are reached by different shapes.
    for label, bad, token in (
        ("a non-object slot", "not-an-object", "[payload-malformed]"),
        ("an overflowing utilization", {"utilization": 10 ** 400, "resets_at": iso(3)},
         "[internal-error]"),
    ):
        flat = root / f"flat-{abs(hash(label))}"
        make_claude(flat, ".claudeV", cached(flat={
            "five_hour": bad,
            "seven_day": {"utilization": 22, "resets_at": iso(50)},
        }))
        done, _, _ = run(["--claude-profile", str(flat / ".claudeV"),
                          "--codex-home", str(make_codex_home(flat, ".codexClean"))], root=flat)
        body = done.stdout.split("warnings")[0]
        # Its own CELL, not its own row: the flat shape's place follows from its key, so the
        # gapped window sits under 5H on the same `all (flat)` row as its healthy sibling rather
        # than opening a row and a column of its own. The record name still names the entry in
        # the warning, which is what identifies what could not be read.
        # Matched on the whole LINE: `pool_rows` splits the pool cell on whitespace, and this
        # allowance is named `all (flat)` -- two words, because which container shape answered is
        # part of what the row discloses.
        rows = [line for _w, _p, line in pool_rows(body) if "all (flat)" in line]
        check(f"33d flat {label} gaps only its own cell",
              len(rows) == 1 and token in rows[0], str(rows))
        check(f"33d flat {label}: the seven_day sibling still reports",
              bool(rows) and "22%" in rows[0], str(rows))
        check(f"33d flat {label}: the warning still names the entry",
              "five_hour (flat)" in done.stdout.split("warnings")[-1], done.stdout)
        check(f"33d flat {label}: the run gaps rather than tracebacking",
              done.returncode == 1 and "Traceback" not in done.stderr,
              f"rc={done.returncode}\n{done.stderr}")

    # 34 -- the report's own claim that only `_FRAMES` reaches the child. The AST literal gate
    # cannot see `dict([("method", operation)])`, so it is not on its own a statement about what
    # can be SENT. This pair is: the bytes written are frames, and the frames are these three.
    source_34 = SCRIPT.read_text(encoding="utf-8")
    lines_34 = source_34.splitlines()
    writes = [n for n, line in enumerate(lines_34, 1) if "stdin.write(" in line]
    check("34 the module writes to the child on exactly one line", len(writes) == 1, str(writes))
    check("34 and that line writes a frame from _FRAMES and nothing else",
          bool(writes) and lines_34[writes[0] - 1].strip() == "stdin.write(frame)",
          lines_34[writes[0] - 1] if writes else "no stdin.write at all")
    sec_lines = [n for n, line in enumerate(lines_34, 1) if '"security"' in line]
    check("34 `security` is named exactly once", len(sec_lines) == 1, str(sec_lines))
    # An absolute path would reach the host binary whatever PATH the suite installs, so the
    # marker fixture would prove nothing. The shebang's /usr/bin/env is not such a path, which
    # is why this pins the binary NAME rather than any occurrence of a bin directory.
    check("34 and never by an absolute path, which would bypass the fixture PATH entirely",
          "/security" not in source_34, "an absolute path to a security binary appears")


# --- 21b: the token expiry comparison, which nothing else in this file holds --------------------

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    expired_root = root / "expired"
    profile = make_claude(expired_root, ".claudeE", cached(entries=[entry(percent=88)]))
    (profile / ".credentials.json").write_text(json.dumps({
        "claudeAiOauth": {"accessToken": SENTINEL_TOKEN, "expiresAt": now_ms(-1)}
    }), encoding="utf-8")
    done, _, _ = run(["--live", "--claude-profile", str(profile),
                      "--codex-home", str(make_codex_home(expired_root, ".codexClean"))],
                     root=expired_root)
    check("21b an expired bearer is named as expired rather than sent",
          "[token-expired]" in done.stdout, done.stdout)
    check("21b the keychain is not consulted as a fallback for it",
          not (expired_root / "security-called.txt").exists(),
          (expired_root / "security-called.txt").read_text(encoding="utf-8")
          if (expired_root / "security-called.txt").exists() else "")
    check("21b it does NOT fall back to the cache either", "88%" not in done.stdout, done.stdout)
    check("21b and the run gaps", done.returncode == 1, f"rc={done.returncode}")
    assert_no_secret("21b expired token", done.stdout, done.stderr)


# --- 22 / 23: transport safety. These import the module, deliberately, because reaching an HTTPS
# stub from a subprocess would need a production origin override -- the very defect they prevent.
sys.path.insert(0, str(SCRIPT.parent))
import contextlib  # noqa: E402
import io  # noqa: E402
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
        raise_out, raise_err = io.StringIO(), io.StringIO()
        try:
            with contextlib.redirect_stdout(raise_out), \
                    contextlib.redirect_stderr(raise_err):
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
            # Drive the REAL renderer, not a per-record helper: the oracle below asks whether a
            # secret can reach stdout, and only what actually prints can answer that.
            R._render([("Claude Code", "/tmp/read/.claudeRead", ".claudeRead", produced)], [],
                      datetime.datetime.now(datetime.timezone.utc), R.Paint(False))
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

    # The runtime half of 34: whatever any helper could construct, only these bytes are written.
    frames = [json.loads(frame.decode("utf-8")) for frame in R._FRAMES]
    check("34 _FRAMES decodes to exactly the three read-only messages, in order",
          [f.get("method") for f in frames]
          == ["initialize", "initialized", "account/rateLimits/read"],
          str([f.get("method") for f in frames]))
    check("34 every frame ends in the newline the protocol frames on",
          all(frame.endswith(b"\n") for frame in R._FRAMES), str(R._FRAMES[:1]))

    # A state outside the declared three is refused at construction, so the comment saying there
    # are three cannot drift from the code again.
    try:
        R.Record("x", "info")
        refused = False
    except R.Malformed:
        refused = True
    check("24 Record refuses a state outside the three terminal ones", refused)
    check("24 the terminal set is exactly those three",
          R.TERMINAL_STATES == frozenset({R.REPORTED, R.NO_CURRENT, R.GAP}),
          str(sorted(R.TERMINAL_STATES)))

    # 35 -- the Keychain SUCCESS path. Every earlier case drove this stub into denial, so two
    # defects sat underneath it: `security ... -w` writes the whole stored credential OBJECT, and
    # returning its stdout put that object -- refresh token included -- into the Authorization
    # header, so no Keychain-backed profile could ever authenticate. Driven in-process because
    # the assertion that matters is on the header, and reaching a stub from a subprocess would
    # need a production origin override.
    keyroot = root / "keychain"
    bindir = install_stub(keyroot)
    keyprofile = keyroot / ".claudeK"
    keyprofile.mkdir(parents=True, exist_ok=True)          # deliberately NO .credentials.json
    stored = json.dumps({
        "claudeAiOauth": {"accessToken": SENTINEL_TOKEN, "expiresAt": now_ms(24),
                          "refreshToken": SENTINEL_KEYCHAIN},
        "mcpOAuth": {"whatever": SENTINEL_KEYCHAIN},
    })
    saved_env = {k: os.environ.get(k) for k in
                 ("PATH", "HOME", "STUB_SECURITY_MARKER", "STUB_SECURITY_PAYLOAD")}
    key_marker = keyroot / "security-called.txt"

    class KeyRecording:
        headers: list = []

        def __init__(self, host, timeout=None):
            self.host = host

        def request(self, method, path, headers=None):
            KeyRecording.headers.append(dict(headers or {}))

        def getresponse(self):
            payload = json.dumps({"limits": [
                {"kind": "weekly_all", "percent": 7, "is_active": True, "resets_at": iso(48)}]})

            class Response:
                status = 200

                def read(self):
                    return payload.encode("utf-8")
            return Response()

        def close(self):
            pass

    original = R.HTTPSConnection
    key_out, key_err = io.StringIO(), io.StringIO()
    try:
        os.environ["PATH"] = f"{bindir}{os.pathsep}{saved_env['PATH']}"
        os.environ["STUB_SECURITY_MARKER"] = str(key_marker)
        os.environ["STUB_SECURITY_PAYLOAD"] = stored
        R.HTTPSConnection = KeyRecording
        with contextlib.redirect_stdout(key_out), contextlib.redirect_stderr(key_err):
            key_records = R._claude_live(keyprofile)
    finally:
        R.HTTPSConnection = original
        for name, value in saved_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    check("35 the fixture security ran, so no real keychain was queried",
          key_marker.exists() and "find-generic-password" in key_marker.read_text("utf-8"),
          key_marker.read_text("utf-8") if key_marker.exists() else "no marker written")
    check("35 a keychain-only profile authenticates and reports",
          any(record.state == R.REPORTED for record in key_records),
          str([record.state for record in key_records]))
    # THE assertion. `in` would pass for the whole object too, since the object CONTAINS the
    # token -- which is precisely the bug. Equality is what distinguishes them.
    check("35 the header carries the access TOKEN, not the stored object around it",
          bool(KeyRecording.headers)
          and KeyRecording.headers[-1].get("Authorization") == f"Bearer {SENTINEL_TOKEN}",
          str(len(KeyRecording.headers)))
    check("35 and the refresh token never leaves the keychain payload",
          all(SENTINEL_KEYCHAIN not in header.get("Authorization", "")
              for header in KeyRecording.headers), str(len(KeyRecording.headers)))
    assert_no_secret("35 keychain success", key_out.getvalue(), key_err.getvalue(),
                     repr([vars(record) for record in key_records]))

    # 36 -- which item is asked for. The DEFAULT profile keeps its live credential under the
    # unsuffixed name; the suffixed item for that same path exists on this machine but holds an
    # empty token, which answers token-absent rather than failing visibly.
    fake_home = root / "svc-home"
    (fake_home / ".claude").mkdir(parents=True, exist_ok=True)
    saved_home = os.environ.get("HOME")
    try:
        os.environ["HOME"] = str(fake_home)
        default_service = R._keychain_service(fake_home / ".claude")
        other_service = R._keychain_service(fake_home / ".claude2")
        elsewhere = R._keychain_service(Path("/tmp/somewhere/.claude"))
    finally:
        if saved_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = saved_home
    check("36 the default profile asks for the UNSUFFIXED item",
          default_service == "Claude Code-credentials", default_service)
    check("36 a sibling profile asks for a suffixed item", other_service.startswith(
        "Claude Code-credentials-") and len(other_service) == len("Claude Code-credentials-") + 8,
        other_service)
    check("36 and `.claude` under a DIFFERENT parent is not the default",
          elsewhere != "Claude Code-credentials", elsewhere)
    check("36 the two services are different items",
          default_service != other_service, f"{default_service} {other_service}")

    # 37 -- the keychain payload is held to the same contract as the file, which is the point of
    # there being one extractor. A stored object with an expired token must not be sent.
    for label, blob, expect in (
        ("not JSON at all", "this-is-not-json", "response-malformed"),
        ("no claudeAiOauth", json.dumps({"mcpOAuth": {}}), "token-absent"),
        ("empty accessToken", json.dumps({"claudeAiOauth": {"accessToken": ""}}), "token-absent"),
        ("expired accessToken", json.dumps({"claudeAiOauth": {
            "accessToken": SENTINEL_TOKEN, "expiresAt": now_ms(-1)}}), "token-expired"),
    ):
        saved_path, saved_payload = os.environ.get("PATH"), os.environ.get("STUB_SECURITY_PAYLOAD")
        out37, err37 = io.StringIO(), io.StringIO()
        try:
            os.environ["PATH"] = f"{bindir}{os.pathsep}{saved_path}"
            os.environ["STUB_SECURITY_PAYLOAD"] = blob
            with contextlib.redirect_stdout(out37), contextlib.redirect_stderr(err37):
                try:
                    R._claude_token(keyprofile)
                    got = "no-error"
                except R.Malformed as exc:
                    got = exc.code
        finally:
            os.environ["PATH"] = saved_path
            if saved_payload is None:
                os.environ.pop("STUB_SECURITY_PAYLOAD", None)
            else:
                os.environ["STUB_SECURITY_PAYLOAD"] = saved_payload
        check(f"37 a stored payload that is {label} -> {expect}", got == expect, got)
        assert_no_secret(f"37 {label}", out37.getvalue(), err37.getvalue(), got)

    # 38 -- an explicitly named profile is made ABSOLUTE before anything hashes it. The
    # Keychain service is the hash of the profile's absolute path, so a relative
    # `--claude-profile .claudeR` run from the parent hashed the two-word spelling the caller
    # typed and found no item. Asserted on the service name the fixture `security` was actually
    # ASKED for, because that is the value the defect changed -- the resulting diagnostic is
    # `token-absent` either way.
    relroot = root / "relative"
    relprofile = relroot / ".claudeR"
    relprofile.mkdir(parents=True, exist_ok=True)     # no .credentials.json: forces the keychain

    def asked_service(marker_root, args, cwd=None):
        marker = marker_root / "security-called.txt"
        if marker.exists():
            marker.unlink()
        run(args, root=marker_root, cwd=cwd)
        line = marker.read_text(encoding="utf-8").strip() if marker.exists() else ""
        # Matched, not split: the service name CONTAINS A SPACE, so `line.split()` returned
        # "Claude" for every spelling -- which made the two values compare EQUAL and the
        # difference this case exists to catch invisible.
        found = re.search(r"-s (.+) -w", line)
        return found.group(1) if found else ""

    absolute_service = asked_service(relroot, ["--live", "--claude-profile", str(relprofile),
                                               "--codex-home", str(relroot / ".nope")])
    relative_service = asked_service(relroot, ["--live", "--claude-profile", ".claudeR",
                                               "--codex-home", str(relroot / ".nope")],
                                     cwd=relroot)
    def service_for(text: str) -> str:
        return "Claude Code-credentials-" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]

    check("38 a relative --claude-profile asks for a service name at all",
          relative_service.startswith("Claude Code-credentials"), relative_service)
    check("38 and NOT the hash of the spelling the caller typed, which is the defect",
          relative_service != service_for(".claudeR"), relative_service)
    check("38 it is hashed as the absolute path that spelling resolves to",
          relative_service == service_for(
              os.path.join(os.path.realpath(relroot), ".claudeR")),
          f"{relative_service} vs {service_for(os.path.join(os.path.realpath(relroot), '.claudeR'))}")
    # Deliberately NOT asserted equal to `absolute_service`. abspath normalises but does not
    # follow symlinks, and a child process's cwd is already symlink-resolved -- on macOS a temp
    # dir under /var/folders resolves to /private/var/folders, so a cwd-relative spelling and a
    # typed absolute one legitimately hash differently. Making them agree would mean resolve(),
    # which would hash a symlink's TARGET, and the vendor stores whatever path it was given.
    # That residual is the same one SKILL.md discloses: an unmatched spelling gaps as
    # token-absent, never as another account's number.
    check("38 the absolute spelling also asks for an absolute-path hash",
          absolute_service.startswith("Claude Code-credentials-"), absolute_service)

    # 39 -- a profile DIRECTORY name may not forge report structure. Reproduced before the fix:
    # this name printed its own `warnings` heading mid-report, with the profile's real rows
    # underneath it, and the run still exited 0.
    forged = root / "forged"
    evil = "".join((".claudeX", chr(10), "warnings", chr(10), "  Claude Code trusted: checked"))
    make_claude(forged, evil, cached(entries=[entry(percent=12)]))
    done, _, _ = run(["--claude-profile", str(forged / evil),
                      "--codex-home", str(make_codex_home(forged, ".codexClean"))], root=forged)
    check("39 the run is clean, so nothing else explains a warnings heading",
          done.returncode == 0, f"rc={done.returncode}\n{done.stdout}")
    check("39 no forged warnings heading is printed",
          chr(10) + "warnings" not in done.stdout, done.stdout)
    # The text still occurs -- inside the escaped name, which is the point. What must not
    # happen is it occupying a LINE of its own, where it reads as report output.
    forged_lines = [ln for ln in done.stdout.splitlines()
                    if "Claude Code trusted: checked" in ln]
    check("39 the injected text never forms a line of its own",
          len(forged_lines) == 1 and forged_lines[0].strip().startswith(".claudeX"),
          str(forged_lines))
    check("39 the name is escaped rather than dropped, so the profile is still named",
          ".claudeX" + chr(92) + "n" in done.stdout, done.stdout)
    check("39 and its real row still reports", "12%" in done.stdout, done.stdout)

    # 39b -- the same name on a profile that GAPS. Case 39's profile is clean, so it never
    # reaches the warning line, and escaping there was unpinned: reverting it alone stayed green.
    # A forged heading is worse in this direction, because the run legitimately prints one.
    forged2 = root / "forged2"
    evil2 = "".join((".claudeG", chr(10), "warnings", chr(10), "  Codex: all clear"))
    make_claude(forged2, evil2, {"cachedUsageUtilization": []})      # gaps: payload-malformed
    done, _, _ = run(["--claude-profile", str(forged2 / evil2),
                      "--codex-home", str(make_codex_home(forged2, ".codexClean"))], root=forged2)
    check("39b the run gaps, so a warnings section is genuinely printed",
          done.returncode == 1 and "[payload-malformed]" in done.stdout, done.stdout)
    check("39b exactly ONE warnings heading exists -- the real one",
          done.stdout.count(chr(10) + "warnings") == 1, done.stdout)
    # Found by its line marker, NOT by splitting on the word "warnings": this name CONTAINS
    # that word, so the split lands inside the escaped name and drops its prefix. The escaping
    # was correct and the assertion was what broke -- the same trick, one level up.
    warn_lines = [ln for ln in done.stdout.splitlines() if "NOT checked --" in ln]
    check("39b the warning names the profile with its name escaped",
          any(".claudeG" + chr(92) + "n" in ln for ln in warn_lines), str(warn_lines))
    check("39b and the forged line never stands alone",
          all(ln.strip() != "Codex: all clear" for ln in done.stdout.splitlines()), done.stdout)

    # 24 -- every diagnostic token the script can render is a member of the closed enum.
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

# --- 40 -- the table: one row per allowance, ordering, colour ----------------------------------------

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    # One account, three pools -- two windows of one quota plus a model-scoped one. They are one
    # account read three ways, so they take ONE line: the duplication this table exists to remove
    # was a row per window, each repeating the profile name and competing with its own siblings.
    ordered = make_claude(root, ".claudeOrd", cached(entries=[
        entry(kind="weekly_all", percent=12, resets=iso(40)),
        entry(kind="session", percent=91, resets=iso(2)),
        entry(kind="weekly_scoped", percent=55, resets=iso(30),
              scope={"model": {"id": None, "display_name": "Fable"}, "surface": None}),
    ]))
    done, _, _ = run(["--claude-profile", str(ordered)], root=root)
    rows = [ln for ln in done.stdout.splitlines() if ".claudeOrd" in ln]
    check("40 one account's windows share one row", len(rows) == 1, str(rows))
    check("40 and every figure it shows is on that row",
          bool(rows) and all(f"{p}%" in rows[0] for p in (12, 91)), str(rows))
    header = [ln for ln in done.stdout.splitlines() if "SOURCE" in ln][0]
    check("40 each window gets a column of its own",
          all(label in header for label in ("5H", "WEEKLY")), repr(header))
    check("40 in reading order, shortest window first",
          header.index("5H") < header.index("WEEKLY"), repr(header))
    check("40 and no percentage is padded out with a decimal it never measured",
          ".0%" not in done.stdout, done.stdout)
    # The model-scoped weekly pool is not shown: it read 0% on every account measured, and a
    # column empty on every row but one costs the table more width than the pool is worth.
    check("40 the model-scoped weekly pool is not shown",
          "55%" not in done.stdout and "Fable" not in done.stdout, done.stdout)

    # The order is by candidate, alphabetically, and NOT by consumption: a rank scattered one
    # Codex home's pools down the page and printed the same directory name in four places, and a
    # rank over a mix of current and expired windows orders numbers that are not comparable.
    hot = make_claude(root, ".claudeHot", cached(entries=[
        entry(kind="session", percent=91, resets=iso(2)),
        entry(kind="weekly_all", percent=12, resets=iso(40))]))
    cool = make_claude(root, ".claudeCool", cached(entries=[
        entry(kind="session", percent=12, resets=iso(2)),
        entry(kind="weekly_all", percent=13, resets=iso(40))]))
    done, _, _ = run(["--claude-profile", str(hot), "--claude-profile", str(cool),
                      "--codex-home", str(make_codex_home(root, ".codexOrd"))], root=root,
                     stub_result=dict(DEFAULT_RESULT, rateLimits=dict(
                         DEFAULT_RESULT["rateLimits"], limitId="codex_twin")))
    order = [where for where, _pool, _line in pool_rows(done.stdout)]
    check("40 the table is ordered by candidate, alphabetically",
          order == sorted(order) and order[:2] == [".claudeCool", ".claudeHot"], str(order))
    check("40 the more consumed account does NOT jump the queue",
          order.index(".claudeHot") > order.index(".claudeCool"), str(order))

    # One candidate's rows sit together, and its name is printed once for the group.
    grouped = [(where, line) for where, _pool, line in pool_rows(done.stdout)
               if where == ".codexOrd"]
    check("40 a candidate's allowances are adjacent", len(grouped) >= 2, str(grouped))
    check("40 and its name is printed once, not once per allowance",
          sum(1 for _w, line in grouped if ".codexOrd" in line) == 1, str(grouped))
    check("40 the allowances inside a candidate are alphabetical too",
          [pool for where, pool, _l in pool_rows(done.stdout) if where == ".codexOrd"]
          == sorted(pool for where, pool, _l in pool_rows(done.stdout) if where == ".codexOrd"),
          str(pool_rows(done.stdout)))

    # The sort key is TOTAL: equal percent and equal reset must still order deterministically,
    # or two runs over the same data can disagree.
    same = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=5)

    def one_pool(where: str, freshness: str = "cache 1h old") -> Any:
        pool = R.Pool(f"/tmp{where}", where, R.CLAUDE_GROUP, "all")
        pool.cells["weekly"] = R.Record("weekly_all", R.REPORTED, percent=50.0, resets=same,
                                        freshness=freshness, family="all", window="weekly")
        return pool

    twins = [one_pool(".b"), one_pool(".a")]
    check("40 the ordering is total -- a tie falls back to the candidate name",
          [pool.where for pool in R._sorted_pools(twins)] == [".a", ".b"],
          str([pool.where for pool in R._sorted_pools(twins)]))
    check("40 and it is stable across the reversed input",
          [pool.where for pool in R._sorted_pools(twins)]
          == [pool.where for pool in R._sorted_pools(list(reversed(twins)))])

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    # A stale row and a live row in one run: a previous window's percentage is not comparable to
    # a current one, and ranking them together is what put an expired 95% above a live 40%.
    live = make_claude(root, ".claudeLive", cached(entries=[
        entry(kind="weekly_all", percent=40, resets=iso(20))]))
    past = make_claude(root, ".claudePast", cached(entries=[
        entry(kind="weekly_all", percent=95, resets=iso(-3))]), token=False)
    done, _, _ = run(["--claude-profile", str(live), "--claude-profile", str(past),
                      "--codex-home", str(make_codex_home(root, ".codexMix"))], root=root)
    body = {where: line for where, _pool, line in pool_rows(done.stdout)}
    # Position ranks nothing now, so the cell has to carry the distinction on its own: an
    # expired window and a current one are not comparable, and a bare percentage cannot say it.
    check("41 the stale cell says so in its own words",
          "ago" in body.get(".claudePast", ""), str(body))
    check("41 while the current one states the time it has left",
          "in " in body.get(".claudeLive", "") and "ago" not in body.get(".claudeLive", ""),
          str(body))
    check("41 the legend carries the diagnostic token",
          "[stale-after-reset]" in done.stdout, done.stdout)
    only_live, _, _ = run(["--claude-profile", str(live),
                           "--codex-home", str(root / ".codexMix")], root=root)
    check("41 and it is printed only when such a cell is on the page",
          "[stale-after-reset]" not in only_live.stdout, only_live.stdout)

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    coloured = make_claude(root, ".claudeCol", cached(entries=[entry(percent=42)]))
    plain, _, _ = run(["--claude-profile", str(coloured), "--color=never"], root=root)
    forced, _, _ = run(["--claude-profile", str(coloured), "--color=always"], root=root)
    auto, _, _ = run(["--claude-profile", str(coloured)], root=root)
    check("42 --color=never emits no escape", "\x1b" not in plain.stdout, repr(plain.stdout[:200]))
    check("42 auto over a pipe emits no escape either -- this suite compares plain text",
          "\x1b" not in auto.stdout, repr(auto.stdout[:200]))
    check("42 --color=always does emit escapes", "\x1b" in forced.stdout)
    stripped = re.sub(r"\x1b\[[0-9;]*m", "", forced.stdout)
    # The load-bearing one: colour may change how the report LOOKS and nothing else. Widths are
    # computed on the plain cells, so stripping the escapes must give back the plain run exactly.
    check("42 stripping the colour yields byte-identical text",
          [ln.rstrip() for ln in stripped.splitlines()][2:]
          == [ln.rstrip() for ln in plain.stdout.splitlines()][2:],
          "\n".join(difflib.unified_diff(plain.stdout.splitlines(),
                                          stripped.splitlines(), lineterm=""))[:1200])
    check("42 the percentage really is painted, not just the title",
          "42%" in re.sub(r"\x1b\[[0-9;]*m", "", forced.stdout)
          and any("42%" in seg for seg in forced.stdout.split("\x1b[")), forced.stdout[:300])

    # `auto` must actually turn colour ON at a terminal -- over a pipe every branch looks alike,
    # so a renderer that only ever coloured under `--color=always` would pass everything above.
    controller, follower = pty.openpty()
    seen_chunks: list[bytes] = []

    def drain() -> None:
        while True:
            try:
                chunk = os.read(controller, 65536)
            except OSError:
                return
            if not chunk:
                return
            seen_chunks.append(chunk)

    reader_thread = threading.Thread(target=drain, daemon=True)
    reader_thread.start()
    env = dict(os.environ)
    env["HOME"] = str(root / "home")
    env["PATH"] = f"{install_stub(root)}{os.pathsep}{env['PATH']}"
    env["STUB_RECORD"] = str(root / "rec.json")
    env["STUB_TRANSCRIPT"] = str(root / "tr.txt")
    env["STUB_MODE"] = "ok"
    env["STUB_RESULT"] = json.dumps(DEFAULT_RESULT)
    env["STUB_SECURITY_MARKER"] = str(root / "sec.txt")
    env.pop("NO_COLOR", None)
    subprocess.run([sys.executable, str(SCRIPT), "--claude-profile", str(coloured)],
                   stdout=follower, stderr=subprocess.DEVNULL, env=env, timeout=90)
    os.close(follower)
    reader_thread.join(timeout=20)
    os.close(controller)
    seen = b"".join(seen_chunks)
    check("42 auto AT A TERMINAL turns colour on", b"\x1b[" in seen, repr(seen[:200]))

    env["NO_COLOR"] = "1"
    done_no, _, _ = run(["--claude-profile", str(coloured)], root=root,
                        extra_env={"NO_COLOR": "1"})
    check("42 NO_COLOR is honoured", "\x1b" not in done_no.stdout)

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    home = make_codex_home(root, ".codexVoucher")
    granted = epoch(-1)
    expires = epoch(24 * 20)
    done, _, _ = run(["--claude-profile", str(make_claude(root, ".claudeV", cached(
        entries=[entry()]))), "--codex-home", str(home)], root=root, stub_result=dict(
        DEFAULT_RESULT, rateLimitResetCredits={"availableCount": 1, "credits": [
            {"id": "x", "status": "available", "grantedAt": granted, "expiresAt": expires,
             "title": "Full reset"}]}))
    band = voucher_band(done.stdout)
    check("43 the voucher band carries the vendor's own title", any("Full reset" in r for r in band),
          str(band))
    check("43 and says when it expires", any("expires" in r for r in band), str(band))
    check("43 with the time left beside it", any("in 19d" in r or "in 20d" in r for r in band),
          str(band))
    check("43 the run stays clean", done.returncode == 0, done.stdout)

    # Expiry and title are OPTIONAL in the payload. A home that reports a bare count must render,
    # not gap -- the same rule the count itself already lives under.
    bare, _, _ = run(["--claude-profile", str(root / ".claudeV"), "--codex-home", str(home)],
                     root=root, stub_result=dict(DEFAULT_RESULT, rateLimitResetCredits={
                         "availableCount": 3}))
    check("43 a bare count with no credit list still renders",
          any(row.split()[1:2] == ["3"] for row in voucher_band(bare.stdout)),
          str(voucher_band(bare.stdout)))
    check("43 and does not gap the run", bare.returncode == 0, bare.stdout)

    widest = max((len(ln) for ln in done.stdout.splitlines()), default=0)
    check("44 no rendered line runs past 110 columns", widest <= 110, f"{widest}: {done.stdout}")

# --- 45 -- the voucher contract under a zero count and hostile optional detail ------------------

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    claude_ok = make_claude(root, ".claudeZ", cached(entries=[entry()]))
    home = make_codex_home(root, ".codexZ")

    # A REPORTED zero is an integer, and must not be reworded. `0` and `not reported` are two
    # different facts -- a measured empty balance, and a backend that sent no voucher data at all
    # -- and a renderer that prints a word for the number erases the difference.
    zero, _, _ = run(["--claude-profile", str(claude_ok), "--codex-home", str(home)], root=root,
                     stub_result=dict(DEFAULT_RESULT,
                                      rateLimitResetCredits={"availableCount": 0}))
    band = voucher_rows(zero.stdout)
    check("45 a reported zero prints the integer 0", len(band) == 1
          and band[0].split()[1] == "0", str(band))
    check("45 and never the word none", "none" not in " ".join(band), str(band))
    check("45 a zero count is still a clean run", zero.returncode == 0, zero.stdout)

    # ... and it must not be STYLED as a voucher there is one of. Every assertion above runs
    # through a pipe with painting off, so a renderer that dropped the zero branch would paint a
    # measured zero bold green -- correct text, and a colour that says the opposite.
    lit, _, _ = run(["--claude-profile", str(claude_ok), "--codex-home", str(home),
                     "--color=always"], root=root,
                    stub_result=dict(DEFAULT_RESULT,
                                     rateLimitResetCredits={"availableCount": 0}))
    zero_line = voucher_rows(lit.stdout)
    check("45 a coloured zero is dimmed, not painted as available",
          len(zero_line) == 1 and "\x1b[2m0\x1b[0m" in zero_line[0], repr(zero_line))
    one, _, _ = run(["--claude-profile", str(claude_ok), "--codex-home", str(home),
                     "--color=always"], root=root,
                    stub_result=dict(DEFAULT_RESULT,
                                     rateLimitResetCredits={"availableCount": 1}))
    one_line = voucher_rows(one.stdout)
    check("45 a coloured non-zero IS painted as available",
          len(one_line) == 1 and "\x1b[1;32m1\x1b[0m" in one_line[0], repr(one_line))

    # Optional detail may NEVER gap the candidate. Each of these is a shape the vendor could
    # send; every one must still print the validated count and every usage row beside it.
    for label, credits in (
        ("credits is not a list", 7),
        ("credits is a string", "nope"),
        ("a credit is not an object", [42]),
        ("an overlong title", [{"status": "available", "title": "T" * 400,
                                "expiresAt": epoch(48)}]),
        ("a control-bearing title", [{"status": "available", "title": "a\u0007b",
                                      "expiresAt": epoch(48)}]),
        ("an unusable expiry", [{"status": "available", "title": "Full reset",
                                 "expiresAt": "tomorrow"}]),
        ("no available credit", [{"status": "spent", "title": "Full reset"}]),
    ):
        done, _, _ = run(["--claude-profile", str(claude_ok), "--codex-home", str(home)],
                         root=root, stub_result=dict(DEFAULT_RESULT, rateLimitResetCredits={
                             "availableCount": 2, "credits": credits}))
        rows = voucher_rows(done.stdout)
        check(f"45 {label} does not gap the run", done.returncode == 0,
              f"rc={done.returncode}\n{done.stdout}")
        check(f"45 {label} still prints the validated count",
              len(rows) == 1 and rows[0].split()[1] == "2", str(rows))
        check(f"45 {label} keeps the usage rows beside it", "54%" in done.stdout, done.stdout)

# --- 46 -- a candidate-level diagnostic must still REACH stdout ---------------------------------

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    # The suite harvests `[token]`s from stdout and checks them against the enum. That oracle is
    # only as good as the tokens that actually print, so this pins the candidate-level path
    # explicitly: blanking the code after _examine still exits 1 and still warns, and would
    # otherwise pass every other check while the token silently vanished from the report.
    unreadable = root / ".claudeNo"
    unreadable.mkdir()
    (unreadable / ".claude.json").write_text("{ not json", encoding="utf-8")
    done, _, _ = run(["--claude-profile", str(unreadable)], root=root)
    body = done.stdout.split("warnings")[0]
    check("46 the candidate's diagnostic token reaches the report body, not just the warning",
          "[payload-malformed]" in body, done.stdout)
    check("46 and the run gaps", done.returncode == 1, f"rc={done.returncode}")

# --- 47 -- column arithmetic is measured in terminal columns, not code points -------------------

check("47 a CJK character counts as two columns", R._width("\u4e2d") == 2)
check("47 an ASCII character counts as one", R._width("a") == 1)
check("47 a combining mark adds nothing", R._width("e\u0301") == 1, str(R._width("e\u0301")))
check("47 a figure drops the zero it never measured", R._figure(42.0) == "42")
check("47 and keeps a fraction the vendor did report", R._figure(7.5) == "7.5")
check("47 a percentage measures the columns it prints", R._width(R._figure(100.0) + "%") == 4)
check("47 padding fills to the column count, not the code-point count",
      R._width(R._pad("\u4e2d\u4e2d", 8)) == 8, repr(R._pad("\u4e2d\u4e2d", 8)))

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    wide = make_claude(root, ".claude\u4e2d\u6587", cached(entries=[entry(percent=42)]))
    # A Codex home too: with none, the Codex side reports no candidates and gaps the run, which
    # would make the clean-exit assertion below about the fixture rather than about the name.
    done, _, _ = run(["--claude-profile", str(wide),
                      "--codex-home", str(make_codex_home(root, ".codexW"))], root=root)
    check("47 a wide profile name does not gap the run", done.returncode == 0, done.stdout)
    rows = [ln for ln in done.stdout.splitlines() if "42%" in ln]
    header = [ln for ln in done.stdout.splitlines() if "SOURCE" in ln]
    # The SOURCE cell is the last on the line and the only one that is never padded, so it is
    # where every earlier column's arithmetic lands. A `len()`-based renderer under-counts the
    # wide name and the whole row slides left, which is the defect this fixture exists for.
    source = R._source("cache 1h00m old", R.CLAUDE_GROUP)
    check("47 and the row's SOURCE column starts at the header's column",
          len(rows) == 1 and source in rows[0]
          and R._width(rows[0][:rows[0].index(source)])
          == R._width(header[0][:header[0].index("SOURCE")]),
          f"{rows[0]!r} vs {header[0]!r}")

# --- 48 -- the vendor stays readable off the row, and the tie-break is complete -----------------

# Under --live BOTH vendors are live, and two candidates can share a directory basename, so the
# source cell has to distinguish them or the flat table can print two indistinguishable rows.
check("48 a live Claude row reads `api`", R._source("live 26 Aug 19:00 CEST", R.CLAUDE_GROUP)
      == "api")
check("48 a live Codex row reads `live`", R._source("live 26 Aug 19:00 CEST", R.CODEX_GROUP)
      == "live")
check("48 a cached Claude row reads its age", R._source("cache 2d04h old", R.CLAUDE_GROUP)
      == "2d04h")

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    same = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=6)
    live_row = R.Record("weekly_all", R.REPORTED, percent=40.0, resets=same,
                        freshness="live 26 Aug 19:00 CEST")
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        R._render([(R.CLAUDE_GROUP, "/tmp/dup/.dup", ".dup", [live_row])], [],
                  datetime.datetime.now(datetime.timezone.utc), R.Paint(False))
    check("48 and the renderer prints it, so the vendor is recoverable from the row",
          " api" in out.getvalue(), out.getvalue())

# The tie-break must reach freshness: two rows alike in every earlier key but visibly different
# on the page would otherwise swap places when the arguments are reversed.
def freshness_twin(age: str) -> Any:
    pool = R.Pool("/tmp/.x", ".x", R.CLAUDE_GROUP, "all")
    pool.cells["weekly"] = R.Record("weekly_all", R.REPORTED, percent=50.0, resets=same,
                                    freshness=age, family="all", window="weekly")
    return pool


twins = [freshness_twin("cache 9h old"), freshness_twin("cache 1h old")]
check("48 the ordering is total down to freshness",
      [pool.freshness() for pool in R._sorted_pools(twins)]
      == [pool.freshness() for pool in R._sorted_pools(list(reversed(twins)))],
      str([pool.freshness() for pool in R._sorted_pools(twins)]))
check("48 and it is the earlier cache that sorts first",
      R._sorted_pools(twins)[0].freshness() == "cache 1h old",
      R._sorted_pools(twins)[0].freshness())

# --- 49 -- what a vendor may put on the page ----------------------------------------------------

# Every string `_text` guards is PRINTED, so the rule is about forging report structure, not
# about tidiness. Authored with chr() throughout: pasting these characters literally into a
# source file is how they end up invisible in a diff and silently wrong.
LINE_SEPARATOR, PARAGRAPH_SEPARATOR, RIGHT_TO_LEFT_OVERRIDE = chr(0x2028), chr(0x2029), chr(0x202E)
for label, character, allowed in (
    ("U+2028 LINE SEPARATOR", LINE_SEPARATOR, False),
    ("U+2029 PARAGRAPH SEPARATOR", PARAGRAPH_SEPARATOR, False),
    ("U+202E RIGHT-TO-LEFT OVERRIDE", RIGHT_TO_LEFT_OVERRIDE, False),
    ("U+200D ZERO WIDTH JOINER", chr(0x200D), False),
    ("U+0007 BEL", chr(0x07), False),
    ("a lone surrogate", chr(0xD800), False),
    # Zs is deliberately allowed: a vendor label may hold a no-break space, and refusing it would
    # be a check the field's owner could never clear.
    ("U+00A0 NO-BREAK SPACE", chr(0xA0), True),
    ("U+3000 IDEOGRAPHIC SPACE", chr(0x3000), True),
):
    try:
        R._text("a" + character + "b")
        verdict = True
    except R.Malformed:
        verdict = False
    check(f"49 _text {'accepts' if allowed else 'refuses'} {label}", verdict == allowed)

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    # The whole point, end to end: `str.splitlines()` breaks on U+2028, so a title carrying one
    # would add lines to the report that look like the report's own -- forged structure out of
    # vendor JSON, on a run that stays clean.
    forged = f"Full{LINE_SEPARATOR}warnings{LINE_SEPARATOR}  trusted"
    done, _, _ = run(["--claude-profile", str(make_claude(root, ".claudeU", cached(
        entries=[entry()]))), "--codex-home", str(make_codex_home(root, ".codexU"))],
        root=root, stub_result=dict(DEFAULT_RESULT, rateLimitResetCredits={
            "availableCount": 1, "credits": [{"status": "available", "title": forged,
                                              "expiresAt": epoch(48)}]}))
    check("49 a separator-bearing title cannot forge a line",
          not any(line.strip() == "trusted" for line in done.stdout.splitlines()), done.stdout)
    check("49 nor a second warnings heading",
          done.stdout.count("warnings") == 0, done.stdout)
    check("49 the title is simply dropped, and the count still prints",
          any(row.split()[1:2] == ["1"] for row in voucher_band(done.stdout)),
          str(voucher_band(done.stdout)))
    check("49 and the run stays clean", done.returncode == 0, done.stdout)

# --- 50 -- width is per GRAPHEME CLUSTER, not per code point ------------------------------------

for label, text, columns in (
    ("a thumbs-up with a skin tone", chr(0x1F44D) + chr(0x1F3FD), 2),
    ("an emoji with a variation selector", chr(0x1F600) + chr(0xFE0F), 2),
    ("a ZWJ family cluster", chr(0x1F468) + chr(0x200D) + chr(0x1F469), 2),
    ("a bare wide emoji", chr(0x1F600), 2),
    ("a CJK ideograph", chr(0x4E2D), 2),
    ("plain ASCII", "abc", 3),
):
    check(f"50 {label} measures {columns} columns", R._width(text) == columns,
          f"{text!r} -> {R._width(text)}")
check("50 padding a cluster fills to the column count",
      R._width(R._pad(chr(0x1F44D) + chr(0x1F3FD), 6)) == 6)

# --- 51 -- the voucher band's own vocabulary and its one unescaped field ------------------------

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    claude_v = make_claude(root, ".claudeV2", cached(entries=[entry()]))
    home_v = make_codex_home(root, ".codexV2")

    def voucher_run(credit, colour=False):
        argv = ["--claude-profile", str(claude_v), "--codex-home", str(home_v)]
        if colour:
            argv.append("--color=always")
        done, _, _ = run(argv, root=root, stub_result=dict(
            DEFAULT_RESULT, rateLimitResetCredits={"availableCount": 1, "credits": [credit]}))
        return done

    # The title is the ONE printed vendor string that used to skip _safe_name, and it lands in
    # exactly the slot the report's own `expires <date>  in <N>d` pair occupies -- so a title
    # spelling that pair out reads as the report's own claim about when the voucher lapses.
    forged = voucher_run({"status": "available", "title": "expires 31 Dec 2099 CEST  in 9999d"})
    band = voucher_rows(forged.stdout)
    check("51 a title is quoted, so it cannot pose as the report's own expiry",
          len(band) == 1 and '"expires 31 Dec 2099 CEST  in 9999d"' in band[0], str(band))
    check("51 and no genuine expiry is claimed beside it",
          band and band[0].count("expires") == 1, str(band))

    # A no-break space is legitimate in a vendor label and _text allows it -- but it must PRINT
    # as an escape like every other name, not as an invisible space.
    nbsp = voucher_run({"status": "available", "title": "Full" + chr(0xA0) + "reset"})
    band = voucher_rows(nbsp.stdout)
    check("51 an invisible space in a title is escaped, not printed",
          bool(band) and chr(0xA0) not in band[0] and "xa0" in band[0], str(band))

    # An expired voucher is not a window that reset. `_relative`'s "reset ... ago" is the wrong
    # noun, and a lapsed voucher is exactly the case the operator needs stated plainly.
    lapsed = voucher_run({"status": "available", "title": "Full reset", "expiresAt": epoch(-400 * 24)})
    band = voucher_rows(lapsed.stdout)
    check("51 a lapsed voucher reads `expired`",
          bool(band) and "expired" in band[0] and "ago" not in band[0], str(band))
    live_v = voucher_run({"status": "available", "title": "Full reset", "expiresAt": epoch(48)})
    band = voucher_rows(live_v.stdout)
    check("51 and one still in date reads the time left",
          bool(band) and "in 1d" in band[0] and "expired" not in band[0], str(band))

# --- 52 -- the dim rides the CELL, because a figure that is not current sits beside one -------

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    # Both windows of ONE account, one current and one whose reset has passed. They now share a
    # line, so the distinction can no longer be carried by a section heading above a block --
    # only the cell itself can say that its figure is not comparable to its neighbour's.
    mixed = make_claude(root, ".claudeDim", cached(entries=[
        entry(kind="weekly_all", percent=40, resets=iso(20)),
        entry(kind="session", percent=95, resets=iso(-3)),
    ]), token=False)
    done, _, _ = run(["--claude-profile", str(mixed), "--color=always"], root=root)
    rows = [ln for ln in done.stdout.splitlines() if "95%" in ln]

    def opening_code(row: str, needle: str) -> str:
        """The escape that paints the run `needle` sits in -- what a reader actually sees."""
        head = row[:row.index(needle)]
        return head[head.rindex("\x1b[") + 2:head.rindex("m")] if "\x1b[" in head else ""

    check("52 both figures are on one row", len(rows) == 1 and "40%" in rows[0], str(rows))
    check("52 the stale cell recedes", bool(rows) and opening_code(rows[0], "95%").startswith("2;"),
          repr(rows[0] if rows else ""))
    check("52 while the current cell beside it keeps full weight",
          bool(rows) and not opening_code(rows[0], "40%").startswith("2"),
          repr(rows[0] if rows else ""))
    check("52 and the hue still tracks consumption, not the dim",
          bool(rows) and opening_code(rows[0], "95%").endswith(R.RED)
          and opening_code(rows[0], "40%") == R.GREEN, repr(rows[0] if rows else ""))

# --- 53 -- one layout for every row -------------------------------------------------------------

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    # BOTH candidate names are two characters, so the WHERE column's widest value is narrower
    # than the word WHERE. That is the only column a real report can drive under its own label,
    # and without the floor the header prints `WHEREPOOL` with nothing between them. The Codex
    # home supplies the long POOL values and a second, differently shaped source cell.
    mixed = make_claude(root, ".a", cached(entries=[
        entry(kind="session", percent=40, resets=iso(20)),
        entry(kind="weekly_all", percent=95, resets=iso(-3)),
    ]), token=False)
    done, _, _ = run(["--claude-profile", str(mixed),
                      "--codex-home", str(make_codex_home(root, ".b"))], root=root,
                     stub_result=dict(DEFAULT_RESULT, rateLimits=dict(
                         DEFAULT_RESULT["rateLimits"], limitId="codex_twin")))
    header = [ln for ln in done.stdout.splitlines() if "SOURCE" in ln][0]
    labels = ("WHERE", "POOL", "5H", "WEEKLY", "SOURCE")
    # Not "a space follows each label" -- that stays true when a LATER column is too narrow. Each
    # label must occupy its own column, so the header must equal the labels padded to the widths
    # the rows use, which is what the floor exists to guarantee.
    for label in labels[:-1]:
        check(f"53 the header keeps a gap after {label}", f"{label} " in header, repr(header))
    starts_at = [header.index(label) for label in labels]
    check("53 the header's labels are in column order and never run together",
          starts_at == sorted(starts_at)
          and all(nxt - at > len(label) for at, nxt, label in
                  zip(starts_at, starts_at[1:], labels)),
          repr(header))

    # One layout for every row. SOURCE is the last cell on the line and the only one never
    # padded, so every earlier column's arithmetic lands on where it starts; a per-row width
    # would move it, and a table whose columns move down the page is not a table.
    body = [line for _w, _p, line in pool_rows(done.stdout)]
    source_at = {R._width(ln[:len(ln) - len(ln.split()[-1])]) for ln in body}
    check("53 every row starts its SOURCE column at the same place",
          len(body) >= 3 and len(source_at) == 1, f"{source_at} in {body}")
    check("53 and that is where the header puts it",
          source_at and source_at.pop() == R._width(header[:header.index("SOURCE")]),
          repr(header))
    check("53 no rendered line runs past 110 columns",
          max(R._width(ln) for ln in done.stdout.splitlines()) <= 110, done.stdout)

    # A report whose pools are ALL stale still needs column names.
    # No Codex home on purpose: a Codex row would be CURRENT, which is the condition that hides
    # this defect. The run gaps on the absent Codex side, which is beside the point being made.
    stale_only = make_claude(root, ".claudeAllStale", cached(entries=[
        entry(kind="session", percent=71, resets=iso(-5))]), token=False)
    done, _, _ = run(["--claude-profile", str(stale_only)], root=root)
    check("53 a report with no current rows still prints the header",
          any("WHERE" in ln and "SOURCE" in ln for ln in done.stdout.splitlines()), done.stdout)
    check("53 above the row it belongs to",
          done.stdout.index("WHERE") < done.stdout.index("71%"), done.stdout)
    check("53 and that row is marked as the previous window",
          any("71%" in ln and "ago" in ln for ln in done.stdout.splitlines()), done.stdout)

# The short RESET labels stay distinguishable -- that distinction is why the cell carries the
# note at all, and the long prose would set the column width for every row.
check("53 an inactive pool and an absent reset time still read differently",
      R.RESET_LABELS["inactive, no reset time reported"]
      != R.RESET_LABELS["no reset time reported by the backend"])
check("53 an unmapped note falls through to itself, wide but correct",
      R.RESET_LABELS.get("something new", "something new") == "something new")
check("53 and no label survives for a state the report no longer produces",
      "inactive, no current window" not in R.RESET_LABELS, str(sorted(R.RESET_LABELS)))

# --- 58 -- `is_active` marks the BINDING pool; it does not withdraw a current window ------------

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    # MEASURED across four real accounts: exactly one pool per account carries `is_active`, and
    # it is whichever one binds first. A five-hour window 9% used and resetting in 17 minutes
    # carried `is_active: false` because the weekly pool was the binding one -- and was rendered
    # as "no current window", dimmed, with its reset time greyed out along with it.
    binding = make_claude(root, ".claudeAct", cached(entries=[
        entry(kind="weekly_all", percent=87, resets=iso(57), active=True),
        entry(kind="session", percent=9, resets=iso(0.3), active=False),
    ]))
    done, _, _ = run(["--claude-profile", str(binding), "--color=always",
                      "--codex-home", str(make_codex_home(root, ".codexAct"))], root=root)
    row = [ln for ln in done.stdout.splitlines() if "9%" in ln]
    check("58 a non-binding window with a future reset states its reset time",
          len(row) == 1 and "in 17m" in row[0], repr(row))

    def opening_code(line: str, needle: str) -> str:
        head = line[:line.index(needle)]
        return head[head.rindex("\x1b[") + 2:head.rindex("m")] if "\x1b[" in head else ""

    check("58 and is not dimmed for it",
          bool(row) and not opening_code(row[0], "9%").startswith("2"), repr(row))
    check("58 while the binding pool beside it reads exactly the same way",
          bool(row) and not opening_code(row[0], "87%").startswith("2"), repr(row))

    # What `is_active` still decides for a pool reporting NO reset time at all: WHICH quiet state
    # it is. The vendor sends `resets_at: null` with `is_active: false` for a window it has
    # nothing to say about, and that reads `inactive`; the same pair with `is_active: true` reads
    # `unopened` (section 65). Whether such a pool is malformed at all is decided by CONSUMPTION,
    # not by this flag -- an active pool above 0% has a window open and must report its reset.
    # `resets_at` literally null -- `entry()`'s own default stands in for "unspecified", so the
    # null has to be written onto the dict rather than passed through it.
    quiet = make_claude(root, ".claudeQuiet", cached(entries=[
        dict(entry(kind="weekly_all", percent=0, active=False), resets_at=None)]))
    plain, _, _ = run(["--claude-profile", str(quiet),
                       "--codex-home", str(root / ".codexAct")], root=root)
    check("58 a pool with no reset time at all still reads `inactive`",
          any("inactive" in ln for ln in plain.stdout.splitlines()), plain.stdout)
    check("58 and does not gap the run", plain.returncode == 0, plain.stdout)

# --- 54 -- the pivot: an allowance is a row, a window is a column, nothing is overwritten ------

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    # `codex_twin` in DEFAULT_RESULT carries TWO windows of equal duration under one limit id --
    # a shape the vendor's schema permits. Both would key the same cell, and the second would
    # silently overwrite the first, so the pool splits into two rows of the same column instead.
    done, _, _ = run(["--claude-profile", str(make_claude(root, ".claudeTwin",
                                                          cached(entries=[entry()]))),
                      "--codex-home", str(make_codex_home(root, ".codexTwin"))], root=root,
                     stub_result=dict(DEFAULT_RESULT, rateLimits=dict(
                         DEFAULT_RESULT["rateLimits"], limitId="codex_twin")))
    twins = [ln for ln in done.stdout.splitlines() if "codex_twin" in ln]
    check("54 a pool with two same-duration windows takes two rows, not one cell",
          len(twins) == 2, str(twins))
    check("54 and both figures survive",
          any("11%" in ln for ln in twins) and any("22%" in ln for ln in twins), str(twins))
    check("54 each naming the slot it came from",
          all(("(primary)" in ln) != ("(secondary)" in ln) for ln in twins), str(twins))
    # Reading only the POOL column left the WINDOW wiring unpinned: both rows would still name
    # their slot with the column derived from the RPC slot name instead of the duration.
    header = [ln for ln in done.stdout.splitlines() if "SOURCE" in ln][0]
    check("54 and both sit under the column their duration names",
          "5H" in header and all(ln.index("11%" if "11%" in ln else "22%")
                                 >= header.index("5H") for ln in twins), repr(header))
    check("54 the run stays clean", done.returncode == 0, done.stdout)

# A record built without a place keeps its own name for both, so it opens its own row and its
# own column -- never a silent share of a cell belonging to some other pool.
loose = R.Record("something-new", R.REPORTED, percent=5.0)
check("54 a record with no declared place falls back to its own name",
      (loose.family, loose.window) == ("something-new", "something-new"),
      f"{loose.family} / {loose.window}")

# --- 55 -- the DEFAULT profile keeps its config beside ~/.claude, not inside it ----------------

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    sandbox = root / "home"
    sandbox.mkdir(parents=True, exist_ok=True)
    # The layout measured on a real machine: `~/.claude` is the data directory and carries a
    # STALE `.claude.json` left over from an older release, while the live config -- the one the
    # vendor's own diagnostics call `~/.claude.json` -- sits beside it. Reading the inner file
    # reported `no-usage-cache` for an account whose weekly pool was nearly spent.
    make_claude(sandbox, ".claude", cached(entries=[entry(kind="weekly_all", percent=41,
                                                          resets=iso(30))]))
    (sandbox / ".claude.json").write_text(json.dumps(cached(entries=[
        entry(kind="weekly_all", percent=87, resets=iso(30))])), encoding="utf-8")
    done, _, _ = run(["--codex-home", str(make_codex_home(root, ".codexHome"))], root=root)
    check("55 the default profile is read from ~/.claude.json",
          "87%" in done.stdout, done.stdout)
    check("55 not from the stale copy inside the data directory",
          "41%" not in done.stdout, done.stdout)
    check("55 and it is not reported as having no cache",
          "[no-usage-cache]" not in done.stdout, done.stdout)
    check("55 the run stays clean", done.returncode == 0, done.stdout)

    # A profile named explicitly by that same path is the same directory, so it resolves the
    # same way -- an invocation form that disagreed with discovery would report two different
    # numbers for one account depending on how it was asked for.
    named, _, _ = run(["--claude-profile", str(sandbox / ".claude"),
                       "--codex-home", str(root / ".codexHome")], root=root)
    check("55 naming it explicitly resolves the same file", "87%" in named.stdout, named.stdout)

    # Every OTHER profile keeps its config inside its own directory, which is what makes the
    # default a special case rather than a rule.
    other = make_claude(sandbox, ".claude2", cached(entries=[
        entry(kind="weekly_all", percent=61, resets=iso(30))]))
    done, _, _ = run(["--claude-profile", str(other),
                      "--codex-home", str(root / ".codexHome")], root=root)
    check("55 a named profile still reads the file inside it", "61%" in done.stdout, done.stdout)

# --- 56 -- the subscription flag explains nothing: it neither hides a cache nor names an absence --

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    # MEASURED on two real accounts: `hasAvailableSubscription: false` ships beside a full,
    # freshly fetched `limits` array, one of them at 100% of its weekly pool. Read as "not
    # subscribed", the flag suppressed exactly the account whose number mattered most -- and did
    # it under a diagnostic that exits 0, so nothing said a pool had gone unread.
    spent = cached(entries=[entry(kind="weekly_all", percent=100, resets=iso(17))],
                   subscription=False)
    profile = make_claude(root, ".claudeSpent", spent)
    done, _, _ = run(["--claude-profile", str(profile),
                      "--codex-home", str(make_codex_home(root, ".codexSpent"))], root=root)
    check("56 a present cache is reported whatever the flag says", "100%" in done.stdout,
          done.stdout)
    check("56 and the profile is not written off as unsubscribed",
          "[no-subscription]" not in done.stdout, done.stdout)
    check("56 the run stays clean", done.returncode == 0, done.stdout)

    # With NO cache the flag decides nothing either. MEASURED on the DEFAULT profile of that
    # same Max 20x subscription: `hasAvailableSubscription: false` and no cache key at all,
    # while --live reads its pools without trouble. Wording the absence off that flag printed
    # `no-subscription` about a paid account -- a billing claim from a report that reads usage.
    bare = make_claude(root, ".claudeBare", {"hasAvailableSubscription": False})
    done, _, _ = run(["--claude-profile", str(bare),
                      "--codex-home", str(root / ".codexSpent")], root=root)
    check("56 an absent cache under a false flag reads no-usage-cache",
          "[no-usage-cache]" in done.stdout and done.returncode == 0, done.stdout)
    check("56 and the report never claims the account is unsubscribed",
          "[no-subscription]" not in done.stdout, done.stdout)

# --- 57 -- the pool column carries the vendor's own name for the pool, not its internal id ------

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    claude_n = make_claude(root, ".claudeN", cached(entries=[entry()]))
    home_n = make_codex_home(root, ".codexN")

    def named_run(by_id, spends_from):
        # Exactly one pool renders -- the one the selector names -- so the naming rule is read
        # off that pool. The FILTER itself has its own cases in 18.
        done, _, _ = run(["--claude-profile", str(claude_n), "--codex-home", str(home_n)],
                         root=root, stub_result={
                             "rateLimits": {"limitId": spends_from},
                             "rateLimitsByLimitId": by_id,
                             "rateLimitResetCredits": {"availableCount": 0}})
        return done, [pool for where, pool, _l in pool_rows(done.stdout) if where == ".codexN"]

    WINDOW = {"usedPercent": 4, "windowDurationMins": 10080, "resetsAt": epoch(70)}
    # `codex_bengalfox` and `base_model_inference` are internal ids; the backend names the same
    # pools in the same object, and the id is unreadable to the person the report is for.
    done, named = named_run({
        "codex_bengalfox": {"limitId": "codex_bengalfox", "limitName": "GPT-5.3-Codex-Spark",
                            "primary": WINDOW}}, "codex_bengalfox")
    check("57 a pool with a limitName is printed under it",
          named == ["GPT-5.3-Codex-Spark"], str(named))
    check("57 and its internal id is not on the page",
          "codex_bengalfox" not in done.stdout, done.stdout)
    check("57 the run stays clean", done.returncode == 0, done.stdout)

    # Null, which the schema permits explicitly.
    _done, named = named_run({"codex": {"limitId": "codex", "limitName": None,
                                        "primary": WINDOW}}, "codex")
    check("57 a null limitName leaves the id standing", named == ["codex"], str(named))

    # Absent, not null: optional in the schema, and a .get default answers only the absent case.
    _done, named = named_run({"codex_x": {"limitId": "codex_x", "primary": WINDOW}}, "codex_x")
    check("57 an absent limitName leaves the id standing", named == ["codex_x"], str(named))

    # Detail may never gap a candidate: a name this module refuses is DROPPED, and the pool's
    # numbers still print under its id. The same rule the voucher title lives under.
    done, named = named_run({
        "codex_e": {"limitId": "codex_e", "limitName": "Spark" + chr(0x2028) + "warnings",
                    "primary": WINDOW}}, "codex_e")
    check("57 a name that forges structure is dropped, not raised",
          named == ["codex_e"], str(named))
    check("57 and it does not gap the home",
          done.returncode == 0 and "4%" in done.stdout, done.stdout)

# --- 59 -- a cached window that is over is re-read live, because the file cannot answer ---------

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    codex_r = make_codex_home(root, ".codexRefresh")
    # Signing in does NOT refresh this: the CLI rewrites `.claude.json` at login and refreshes
    # `cachedUsageUtilization` only after a request that carries usage back. So a profile can be
    # freshly authenticated and still describe a window days gone -- with nothing on the page to
    # suggest that signing in again was not the answer. No credential here, so the retry stops
    # at the token and never opens a socket.
    stale = make_claude(root, ".claudeStale", cached(entries=[
        entry(kind="weekly_all", percent=71, resets=iso(-5))]), token=False)
    done, _, _ = run(["--claude-profile", str(stale), "--codex-home", str(codex_r)], root=root)
    check("59 a stale cache is retried against the API",
          "the cache describes a window that is over" in done.stdout, done.stdout)
    check("59 the retry names why it could not answer",
          "token-absent" in done.stdout or "keychain-denied" in done.stdout, done.stdout)
    # A failed retry may not COST anything. The cached figures are stale, which their own cells
    # already say, and losing them to a failed network call would be the worse trade.
    check("59 and the cached figures survive it",
          any("71%" in line for _w, _p, line in pool_rows(done.stdout)), done.stdout)
    check("59 the reason is a note, not a warning -- the default mode read what it promised to",
          done.returncode == 0 and "warnings" not in done.stdout, done.stdout)

    # The trigger is the window being OVER, not the cache being old: a current window is exactly
    # what the file is for, and a report that phoned home on every run would be a different tool.
    fresh = make_claude(root, ".claudeFresh", cached(entries=[
        entry(kind="weekly_all", percent=12, resets=iso(40))]), token=False)
    done, _, _ = run(["--claude-profile", str(fresh), "--codex-home", str(codex_r)], root=root)
    check("59 a cache whose window is still open is not retried",
          "the cache describes a window that is over" not in done.stdout, done.stdout)
    check("59 and it reports from the file", "12%" in done.stdout and done.returncode == 0,
          done.stdout)

# --- 60 -- the round-1 review fixes, each pinned where it would silently regress ---------------

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    codex_60 = make_codex_home(root, ".codex60")

    # A pool is grouped by the CANDIDATE, never by its printable name. `--claude-profile` is
    # repeatable and takes paths under different parents, so two candidates can share a basename;
    # grouped by the printed name they merged into ONE row -- one account supplying the 5h figure
    # and the other the weekly, under one account's freshness, on a run that exited 0.
    (root / "a").mkdir(); (root / "b").mkdir()
    left = make_claude(root / "a", ".claude", cached(entries=[
        entry(kind="session", percent=10, resets=iso(3))]), token=False)
    right = make_claude(root / "b", ".claude", cached(entries=[
        entry(kind="weekly_all", percent=90, resets=iso(50))]), token=False)
    done, _, _ = run(["--claude-profile", str(left), "--claude-profile", str(right),
                      "--codex-home", str(codex_60)], root=root)
    same_name = [line for where, _pool, line in pool_rows(done.stdout) if where == ".claude"]
    check("60 two candidates sharing a basename stay two rows", len(same_name) == 2,
          str(same_name))
    check("60 and neither account's figure is attributed to the other",
          not any("10%" in line and "90%" in line for line in same_name), str(same_name))
    # Blanking the repeated WHERE is what would present them as one group, so it reads identity
    # too: both rows print the name, because they are not the same account.
    check("60 the second row is not blanked into the first's group",
          sum(1 for line in same_name if ".claude" in line) == 2, str(same_name))

    # The default profile keeps its config BESIDE its directory, so an installation that
    # authenticates through the Keychain has neither in-directory marker. Discovery dropped it
    # before the ledger, with no diagnostic, on a report that still exited 0 because the other
    # profiles kept the candidate list non-empty.
    sandbox = root / "home"
    sandbox.mkdir(parents=True, exist_ok=True)
    make_claude(sandbox, ".claude", None, token=False)          # the directory, and nothing in it
    (sandbox / ".claude.json").write_text(json.dumps(cached(entries=[
        entry(kind="weekly_all", percent=63, resets=iso(30))])), encoding="utf-8")
    inner = list((sandbox / ".claude").iterdir())
    check("60 the fixture really has no marker inside the directory", inner == [], str(inner))
    done, _, _ = run(["--codex-home", str(codex_60)], root=root)
    check("60 a default profile with no in-directory marker is still discovered",
          "63%" in done.stdout, done.stdout)
    check("60 and the run stays clean", done.returncode == 0, done.stdout)

    # A window that GAPS has no validated kind to place it by, and the fallback names it after
    # its INDEX in the payload -- so a malformed `session` opened a row AND a column both called
    # `limits[0]`, while its healthy weekly sibling sat under `all`.
    broken = make_claude(root, ".claudeBroken", cached(entries=[
        entry(kind="session", percent="not-a-number", resets=iso(3)),
        entry(kind="weekly_all", percent=44, resets=iso(50)),
    ]), token=False)
    done, _, _ = run(["--claude-profile", str(broken), "--codex-home", str(codex_60)], root=root)
    rows = [(pool, line) for where, pool, line in pool_rows(done.stdout)
            if where == ".claudeBroken"]
    check("60 a gapped window stays on its own pool's row",
          len(rows) == 1 and rows[0][0] == "all", str(rows))
    check("60 carrying its diagnostic beside the healthy sibling",
          bool(rows) and "[field-malformed]" in rows[0][1] and "44%" in rows[0][1], str(rows))
    # The payload index survives in the WARNING, which is where it belongs -- that line names
    # WHICH entry could not be read. What it may no longer do is name a pool or a column.
    check("60 and the payload index names no pool and opens no column",
          not any("limits[" in line for _w, _p, line in pool_rows(done.stdout))
          and "LIMITS[" not in done.stdout, done.stdout)
    check("60 while the warning still says which entry it was",
          "limits[0]" in done.stdout.split("warnings")[-1], done.stdout)
    check("60 the run still gaps, because a window went unread",
          done.returncode == 1, f"rc={done.returncode}")

# A `limitName` of nothing but spaces passes `_text`, which allows Zs deliberately, and would
# render a pool with no visible name at all.
with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    claude_b = make_claude(root, ".claudeBlank", cached(entries=[entry()]))
    home_b = make_codex_home(root, ".codexBlank")
    done, _, _ = run(["--claude-profile", str(claude_b), "--codex-home", str(home_b)],
                     root=root, stub_result={
                         "rateLimits": {"limitId": "codex_w"},
                         "rateLimitsByLimitId": {"codex_w": {
                             "limitId": "codex_w", "limitName": "   ",
                             "primary": {"usedPercent": 4, "windowDurationMins": 10080,
                                         "resetsAt": epoch(70)}}},
                         "rateLimitResetCredits": {"availableCount": 0}})
    blank = [pool for where, pool, _l in pool_rows(done.stdout) if where == ".codexBlank"]
    check("60 a whitespace-only limitName is not a name", blank == ["codex_w"], str(blank))

# --- 61 -- the round-2 review fixes ------------------------------------------------------------

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    # A Codex window that GAPS is placed by its DURATION, exactly as its healthy sibling is.
    # Falling back to the RPC slot name put a malformed five-hour window under a column called
    # PRIMARY, beside the very 5H column it belongs in.
    done, _, _ = run(["--claude-profile", str(make_claude(root, ".claudeSlot",
                                                          cached(entries=[entry()]))),
                      "--codex-home", str(make_codex_home(root, ".codexSlot"))], root=root,
                     stub_result={
                         "rateLimits": {"limitId": "codex", "primary": {
                             "usedPercent": "not-a-number", "windowDurationMins": 300,
                             "resetsAt": epoch(4)}},
                         "rateLimitResetCredits": {"availableCount": 0}})
    header = [ln for ln in done.stdout.splitlines() if "SOURCE" in ln][0]
    gapped = [line for where, _p, line in pool_rows(done.stdout) if where == ".codexSlot"]
    check("61 a malformed Codex window opens no column of its own",
          "PRIMARY" not in header and "5H" in header, repr(header))
    check("61 and its diagnostic sits under the column its duration names",
          len(gapped) == 1 and "[field-malformed]" in gapped[0]
          and gapped[0].index("[field-malformed]") >= header.index("5H"), str(gapped))
    check("61 the run gaps, because a window went unread", done.returncode == 1,
          f"rc={done.returncode}")

# --- 62 -- the three fixes a mutation could still have reverted unnoticed ----------------------

# Fix 1, the refresh rule. Its live half cannot be reached from this suite without a socket, so
# the DECISION is a function and the function is stated here: whatever the live read produced is
# the answer whenever it produced anything, and the cache answers only when it produced nothing.
# Restoring a per-window merge would have to change this.
cached_two = [R.Record("session", R.NO_CURRENT, percent=88.0, freshness="cache 3d00h old",
                       diagnostic="stale-after-reset", family="all", window="5h"),
              R.Record("weekly_all", R.REPORTED, percent=40.0, freshness="cache 3d00h old",
                       family="all", window="weekly")]
live_partial = [R.Record("session", R.REPORTED, percent=2.0, freshness="live now",
                         family="all", window="5h"),
                R.Record("weekly_all", R.GAP, diagnostic="field-malformed",
                         family="all", window="weekly")]
state, records, code = R._refreshed((R.GAP, live_partial, ""), (R.NO_CURRENT, cached_two, ""))
check("62 a live read that produced anything answers the row, whole",
      records == live_partial and state == R.GAP, str([r.name for r in records]))
check("62 so no cached cell can survive beside a live one",
      not any(r.freshness.startswith("cache") for r in records),
      str([r.freshness for r in records]))
state, records, code = R._refreshed((R.GAP, [], "token-expired"),
                                   (R.NO_CURRENT, cached_two, ""))
check("62 while a live read that produced nothing leaves the cache untouched",
      records == cached_two and state == R.NO_CURRENT and code == "", str(state))

# Fix 3, the removed collision machinery, rests on one property: the selector yields AT MOST one
# pool. Two ids can only ever share a label if that is false, so the property is what to pin.
WIN = {"usedPercent": 4, "windowDurationMins": 10080, "resetsAt": 1}
many = {"codex": {"limitId": "codex", "primary": WIN},
        "codex_other": {"limitId": "codex_other", "primary": WIN}}
picked = R._codex_spent_from({"limitId": "codex", "primary": WIN}, many)
check("62 a resolvable selector yields exactly one pool", list(picked) == ["codex"], str(picked))
picked = R._codex_spent_from({"limitId": "codex_top", "primary": WIN}, many)
check("62 and so does one answered by the top-level object",
      list(picked) == ["codex_top"], str(picked))
try:
    R._codex_spent_from({"limitId": "codex_none"}, many)
    resolved = True
except R.Malformed:
    resolved = False
check("62 an unresolvable selector yields none of them, rather than all", not resolved)

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    # Fix 5. An unknown kind keeps its OWN name for both its row and its column. Placed by a
    # guess about its prefix it would join `all`, where it either overwrites the weekly figure
    # beside it or hides behind it -- and this fixture would look identical either way unless
    # the family and the column are both checked.
    unknown = make_claude(root, ".claudeKind", cached(entries=[
        entry(kind="weekly_bonus", percent=33, resets=iso(30)),
        entry(kind="weekly_all", percent=44, resets=iso(30)),
    ]))
    done, _, _ = run(["--claude-profile", str(unknown),
                      "--codex-home", str(make_codex_home(root, ".codexKind"))], root=root)
    rows = [(pool, line) for where, pool, line in pool_rows(done.stdout)
            if where == ".claudeKind"]
    header = [ln for ln in done.stdout.splitlines() if "SOURCE" in ln][0]
    check("62 an unknown kind takes a row of its own, under its own name",
          sorted(pool for pool, _l in rows) == ["all", "weekly_bonus"], str(rows))
    check("62 and a column of its own, so it overwrites nothing",
          "WEEKLY_BONUS" in header and "WEEKLY " in header, repr(header))
    check("62 with both figures still on the page",
          "33%" in done.stdout and "44%" in done.stdout, done.stdout)
    check("62 the run stays clean", done.returncode == 0, done.stdout)

# --- 63 -- the corner the de-duplicated pool pointer moved ------------------------------------

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    # No `rateLimitsByLimitId` at all, and a top-level object carrying neither window. The
    # pointer used to be derived twice -- once here, once in the selector -- and this shape hit
    # the copy that gapped the whole home, taking the voucher count with it. Derived once, it
    # follows the rule the selector's own handler states: the quota gaps, its siblings report.
    done, _, _ = run(["--claude-profile", str(make_claude(root, ".claudeDup",
                                                          cached(entries=[entry()]))),
                      "--codex-home", str(make_codex_home(root, ".codexDup"))], root=root,
                     stub_result={"rateLimits": {"limitId": "codex"},
                                  "rateLimitResetCredits": {"availableCount": 3}})
    rows = [pool for where, pool, _l in pool_rows(done.stdout) if where == ".codexDup"]
    check("63 a windowless single-pool payload gaps the quota", rows == ["rateLimits"], str(rows))
    check("63 with the diagnostic naming what could not be read",
          "[payload-malformed]" in done.stdout and done.returncode == 1,
          f"rc={done.returncode}\n{done.stdout}")
    kept = [row for row in voucher_rows(done.stdout) if ".codexDup" in row]
    check("63 while the voucher count beside it still reports",
          len(kept) == 1 and kept[0].split()[1] == "3", str(kept))
    check("63 and the Claude side is untouched by it", "42%" in done.stdout, done.stdout)

# --- 64 -- a gap is the one cell that may not recede -------------------------------------------

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    # Every record `_row_or_gap` builds carries `percent is None`, so a missing-value test above
    # the GAP test answered for every gap on the page: the diagnostic saying a window went unread
    # rendered in the same dim as an inactive pool's `-`.
    broken = make_claude(root, ".claudeRed", cached(entries=[
        entry(kind="session", percent="not-a-number", resets=iso(3)),
        entry(kind="weekly_all", percent=44, resets=iso(50)),
    ]), token=False)
    done, _, _ = run(["--claude-profile", str(broken), "--color=always",
                      "--codex-home", str(make_codex_home(root, ".codexRed"))], root=root)
    # The warnings block prints the same token; only the TABLE cell is under test here.
    row = [ln for ln in done.stdout.split("warnings")[0].splitlines()
           if "[field-malformed]" in ln]

    def opens_with(line: str, needle: str) -> str:
        head = line[:line.index(needle)]
        return head[head.rindex("\x1b[") + 2:head.rindex("m")] if "\x1b[" in head else ""

    check("64 a gap cell is painted red", len(row) == 1
          and opens_with(row[0], "[field-malformed]") == R.RED,
          repr(opens_with(row[0], "[field-malformed]") if row else row))
    check("64 and not dimmed like an ordinary missing value",
          bool(row) and not opens_with(row[0], "[field-malformed]").startswith("2"), repr(row))
    check("64 while the healthy figure beside it keeps its own hue",
          bool(row) and opens_with(row[0], "44%") == R.GREEN, repr(row))

# --- 65 -- an ACTIVE pool at 0% with no reset time is an unopened window, not a malformed one ---

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    # MEASURED on a real profile that has consumed nothing: the vendor ships
    # `{"kind": "session", "percent": 0, "resets_at": null, "is_active": true}` -- the binding
    # pool of an account whose five-hour window has never OPENED. Read as drift, that gapped the
    # whole 5H cell of a profile whose only fault was going unused, and printed a warning about a
    # payload that was exactly what the vendor sends. `entry()`'s default stands in for
    # "unspecified", so the null is written onto the dict rather than passed through it.
    unused = make_claude(root, ".claudeUnused", cached(entries=[
        dict(entry(kind="session", percent=0, active=True), resets_at=None),
        dict(entry(kind="weekly_all", percent=0, active=False), resets_at=None),
    ]))
    done, _, _ = run(["--claude-profile", str(unused),
                      "--codex-home", str(make_codex_home(root, ".codexUnused"))], root=root)
    body = done.stdout.split("warnings")[0]
    rows = [line for _w, pool, line in pool_rows(body) if pool == "all"]
    check("65 an unused account does not gap", "[field-malformed]" not in done.stdout, done.stdout)
    check("65 and the run stays clean", done.returncode == 0, f"rc={done.returncode}\n{done.stdout}")
    check("65 its unopened window states that in its own cell",
          len(rows) == 1 and "unopened" in rows[0], str(rows))
    # By POSITION, not by membership: the row carries both cells, so `"inactive" in row` is
    # satisfied by either one and cannot tell which pool got which label -- the exact thing this
    # check exists to pin. Column order on the line is 5H then WEEKLY.
    check("65 while the inactive sibling beside it still reads `inactive`",
          bool(rows) and rows[0].index("unopened") < rows[0].index("inactive"), str(rows))
    check("65 and both figures are still printed",
          bool(rows) and rows[0].count("0%") == 2, str(rows))

    # The other half of the rule, which is what keeps the relaxation from being a hole: an active
    # pool ABOVE zero has a window open by definition, so no reset time for it is still drift.
    used = make_claude(root, ".claudeUsedNoReset", cached(entries=[
        dict(entry(kind="session", percent=37, active=True), resets_at=None),
        entry(kind="weekly_all", percent=44, resets=iso(50)),
    ]))
    done, _, _ = run(["--claude-profile", str(used),
                      "--codex-home", str(make_codex_home(root, ".codexUsed"))], root=root)
    gapped = [line for _w, _p, line in pool_rows(done.stdout.split("warnings")[0])
              if "[field-malformed]" in line]
    # That the healthy sibling survives its gap is section 26/33's subject, not this one's.
    check("65 an active pool with usage and no reset time still gaps",
          len(gapped) == 1 and "37%" not in gapped[0], str(gapped))

    # An INACTIVE pool carrying usage is untouched by the rule -- its window may simply have
    # closed, which no measurement here calls malformed. Pinned so the relaxation cannot be
    # "tidied" into a symmetric test that gaps a state the report used to accept.
    closed = make_claude(root, ".claudeClosed", cached(entries=[
        dict(entry(kind="session", percent=31, active=False), resets_at=None),
        entry(kind="weekly_all", percent=44, resets=iso(50)),
    ]))
    done, _, _ = run(["--claude-profile", str(closed),
                      "--codex-home", str(make_codex_home(root, ".codexClosed"))], root=root)
    closed_rows = [line for _w, _p, line in pool_rows(done.stdout.split("warnings")[0])
                   if "31%" in line]
    check("65 an inactive pool with usage and no reset time still reads `inactive`",
          len(closed_rows) == 1 and "inactive" in closed_rows[0], str(closed_rows))
    check("65 and does not gap the run", done.returncode == 0, done.stdout)

    # The flat container has no `is_active` to consult, and the same profile carries the same
    # unopened window there: `{"utilization": 0, "resets_at": null}` under `five_hour`.
    # Keyed by `slug`, never by `expect`: a directory name derived from the expected TOKEN
    # collides the moment two cases expect the same one, and the second fixture would overwrite
    # the first while the loop still ran twice and reported two passes.
    for slug, label, five_hour, expect in (
        ("unopened", "an unopened window", {"utilization": 0, "resets_at": None}, "unopened"),
        ("used", "usage with no reset time",
         {"utilization": 55, "resets_at": None}, "[field-malformed]"),
    ):
        flat_root = root / f"flat-{slug}"
        make_claude(flat_root, ".claudeFlat", cached(flat={
            "five_hour": five_hour,
            "seven_day": {"utilization": 22, "resets_at": iso(50)},
        }))
        done, _, _ = run(["--claude-profile", str(flat_root / ".claudeFlat"),
                          "--codex-home", str(make_codex_home(flat_root, ".codexFlat"))],
                         root=flat_root)
        flat_rows = [line for _w, _p, line in pool_rows(done.stdout.split("warnings")[0])
                     if "all (flat)" in line]
        # The healthy `seven_day` sibling on this row is section 33d's subject, on the identical
        # fixture; only the 5H cell is new here.
        check(f"65 flat {label}: the 5H cell says so", len(flat_rows) == 1
              and expect in flat_rows[0], str(flat_rows))

# The new short RESET label has to stay distinguishable from the two that were already there, for
# the same reason they do: the cell carries the note precisely because the state it names is not
# the others. That the older PAIR differs is section 53's check, not restated here.
check("65 an unopened window reads as neither of the other two quiet states",
      R.RESET_LABELS["no window opened yet"] not in (
          R.RESET_LABELS["inactive, no reset time reported"],
          R.RESET_LABELS["no reset time reported by the backend"]),
      str(sorted(R.RESET_LABELS.values())))

print(f"ran {checks} checks")
if failures:
    print(f"FAIL ({len(failures)}):")
    for failure in failures:
        print(f"  {failure}")
    sys.exit(1)

# The count this revision actually runs, not a floor left behind by an older one. A stale floor
# lets every check a revision ADDED disappear while the suite still prints PASS -- 53 of them, at
# the point this was noticed. Raise it with the suite.
MIN_CHECKS = 554
if checks < MIN_CHECKS:
    print(f"FAIL: only {checks} checks ran, expected at least {MIN_CHECKS}")
    sys.exit(1)
print("PASS")
