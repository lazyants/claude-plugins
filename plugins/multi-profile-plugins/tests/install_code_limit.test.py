#!/usr/bin/env python3
"""Drive install_code_limit.py as a subprocess, then drive the `code-limit` it installed.

Every case runs the SHIPPED scripts. The installed command is exercised as a command -- executed
by its own path, from a directory that is not the repository -- because the property under test is
precisely that a file on PATH reaches the plugin's report and nothing else.

The suite's spine is one exact-bytes assertion. A wrapper that merely MENTIONS the shipped report
while executing a copy of it passes every behavioural check for as long as the copy is identical,
which is the whole failure mode the issue exists to prevent; only pinning the shim's three lines,
including the target of its single `exec`, can tell the two apart.

Fixtures never touch the machine: HOME is confined to a temp dir, and `codex`, `security` and
`python3` are stubs on a fixture PATH. The `python3` stub matters for determinism -- the shim
resolves `python3` at run time, so without it the suite would measure whichever interpreter the
host happens to put first, not the shim.

Dependency-free: run it with `python3 install_code_limit.test.py`.
"""
from __future__ import annotations

import contextlib
import datetime
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from collections.abc import Generator
from pathlib import Path
from typing import Any

PLUGIN = Path(__file__).resolve().parent.parent
SCRIPTS = PLUGIN / "skills/code-limits/scripts"
INSTALLER = SCRIPTS / "install_code_limit.py"
REPORT = SCRIPTS / "report_limits.py"

MARKER = "# multi-profile-plugins code-limits shim -- regenerate with install_code_limit.py"

failures: list[str] = []
checks = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global checks
    checks += 1
    if not condition:
        if detail:
            body = "\n".join(f"      {line}" for line in detail.splitlines())
            failures.append(f"{name}:\n{body}")
        else:
            failures.append(name)


def quoted(text: str) -> str:
    """POSIX single-quoting, written out here rather than imported from the installer.

    A test that called the installer's own helper would agree with it about a wrong answer.
    """
    return "'" + text.replace("'", "'\"'\"'") + "'"


def report_of(source: Path) -> Path:
    """The report path AS THE INSTALLER RESOLVES IT.

    `Path(__file__).resolve()` is what gets baked, so on macOS a `/var/folders/...` fixture is
    written into the shim as `/private/var/folders/...`. Resolving here keeps the expectation
    about the installer's contract rather than about the harness's spelling of a temp dir.
    """
    return (source / "report_limits.py").resolve()


def expected_shim(report: Path) -> str:
    return f"#!/bin/sh\n{MARKER}\nexec python3 {quoted(str(report))} \"$@\"\n"


# --- fixtures ---------------------------------------------------------------------------------

STUB_CODEX = '''#!/usr/bin/env python3
"""Stand-in for `codex app-server`. Records every method it is sent, then answers."""
import json, os, sys

methods = []
for _ in range(3):
    line = sys.stdin.readline()
    if not line:
        break
    try:
        methods.append(json.loads(line).get("method"))
    except ValueError:
        methods.append("<unparsable>")
with open(os.environ["STUB_METHODS"], "w", encoding="utf-8") as handle:
    json.dump(methods, handle)

print(json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}))
print(json.dumps({"jsonrpc": "2.0", "id": 2,
                  "result": json.loads(os.environ["STUB_RESULT"])}))
sys.stdout.flush()
sys.exit(0)
'''

STUB_SECURITY = '''#!/usr/bin/env python3
"""Stand-in for macOS `security`. Denies, and never reaches a real keychain."""
import sys
sys.exit(1)
'''


def epoch(delta_hours: float) -> int:
    when = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=delta_hours)
    return int(when.timestamp())


def iso(delta_hours: float) -> str:
    when = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=delta_hours)
    return when.isoformat()


def now_ms(delta_hours: float = 0.0) -> int:
    when = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=delta_hours)
    return int(when.timestamp() * 1000)


def codex_result(coupons: Any = "default") -> dict:
    result: dict = {
        "rateLimits": {
            "limitId": "codex",
            "primary": {"usedPercent": 54, "windowDurationMins": 10080, "resetsAt": epoch(72)},
        },
        "rateLimitsByLimitId": {
            "codex": {
                "limitId": "codex",
                "primary": {"usedPercent": 54, "windowDurationMins": 10080,
                            "resetsAt": epoch(72)},
            },
        },
    }
    if coupons != "omitted":
        result["rateLimitResetCredits"] = ({"availableCount": 1, "credits": [{"id": "x"}]}
                                           if coupons == "default" else coupons)
    return result


def make_stub_bin(root: Path) -> Path:
    """`codex`, `security` and `python3` on one fixture PATH."""
    bindir = root / "stub-bin"
    bindir.mkdir(parents=True, exist_ok=True)
    for name, body in (("codex", STUB_CODEX), ("security", STUB_SECURITY)):
        # A stub with a syntax error exits non-zero, which is what the failure it stands in for
        # also looks like. Compile it here so a broken fixture cannot pass as a real result.
        compile(body, f"<stub {name}>", "exec")
        target = bindir / name
        target.write_text(body, encoding="utf-8")
        target.chmod(0o755)
    # Pin the interpreter the SHIM will resolve. Without this the suite silently measures the
    # host's first `python3`, which on macOS can be older than the report's 3.11 floor.
    (bindir / "python3").write_text(
        f"#!/bin/sh\nexec {quoted(sys.executable)} \"$@\"\n", encoding="utf-8")
    (bindir / "python3").chmod(0o755)
    return bindir


def make_source(root: Path, *segments: str) -> Path:
    """Copy the two shipped scripts to `<root>/<segments...>/scripts/` and return that dir."""
    dest = root.joinpath(*segments) / "scripts"
    dest.mkdir(parents=True, exist_ok=True)
    for script in (INSTALLER, REPORT):
        shutil.copy2(script, dest / script.name)
    return dest


def make_claude_profile(root: Path, name: str) -> Path:
    profile = root / name
    profile.mkdir(parents=True, exist_ok=True)
    profile.joinpath(".claude.json").write_text(json.dumps({
        "hasAvailableSubscription": True,
        "cachedUsageUtilization": {
            "fetchedAtMs": now_ms(-1),
            "utilization": {"limits": [
                {"kind": "weekly_all", "percent": 42, "is_active": True, "resets_at": iso(48)},
            ]},
        },
    }), encoding="utf-8")
    return profile


def make_codex_home(root: Path, name: str) -> Path:
    home = root / name
    home.mkdir(parents=True, exist_ok=True)
    home.joinpath("config.toml").write_text('model = "x"\n', encoding="utf-8")
    home.joinpath("auth.json").write_text(json.dumps({"token": "t"}), encoding="utf-8")
    return home


def sandbox_env(root: Path, *, on_path: Path | None = None) -> dict:
    """An environment that cannot reach this machine's profiles, keychain or codex binary."""
    bindir = make_stub_bin(root)
    home = root / "home"
    home.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env.pop("HTTPS_PROXY", None)
    env["HOME"] = str(home)
    env["PATH"] = os.pathsep.join(
        [str(bindir)] + ([str(on_path)] if on_path is not None else []))
    env["STUB_METHODS"] = str(root / "methods.json")
    env["STUB_RESULT"] = json.dumps(codex_result())
    return env


def install(root: Path, source: Path, bin_dir: Path, *, force: bool = False,
            env: dict | None = None) -> subprocess.CompletedProcess:
    argv = [sys.executable, str(source / "install_code_limit.py"), "--bin-dir", str(bin_dir)]
    if force:
        argv.append("--force")
    return subprocess.run(argv, capture_output=True, text=True, timeout=120,
                          env=sandbox_env(root) if env is None else env)


def coupon_field(line: str) -> str:
    """The value the `reset coupons` row reports, without its trailing explanatory note."""
    return line.split("reset coupons", 1)[1].strip().split("  ", 1)[0].strip()


def unrunnable(path: Path, env: dict) -> bool:
    """Whether the file at `path` cannot be run as a command AT ALL.

    Not a return code: a shebang naming `/bin/sh\r`, or a file without execute bits, fails inside
    `execve` and surfaces as an OSError in the PARENT -- there is no child to report a status. A
    check written as `returncode != 0` never reaches its assertion on either.
    """
    try:
        done = subprocess.run([str(path)], capture_output=True, text=True, timeout=60,
                              env=env, cwd="/")
    except OSError:
        return True
    return done.returncode != 0


def run_command(path: Path, args: list[str], env: dict, cwd: Path | str = "/",
                timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run([str(path)] + args, capture_output=True, text=True,
                          timeout=timeout, env=env, cwd=str(cwd))


@contextlib.contextmanager
def stable_case() -> Generator[tuple[Path, Path, Path], None, None]:
    """A temp root, the shipped scripts under a version-STABLE layout, and a bin dir path.

    The layout is the one case 14 contrasts against, so it is named once here rather than spelled
    out per case. The bin dir is only NAMED: cases that need it to exist, or to already hold a
    foreign file, say so themselves, and the ones that leave it absent are testing exactly that.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        yield root, make_source(root, "stable", "skills", "code-limits"), root / "bin"


# --- cases ------------------------------------------------------------------------------------

# 1 -- the exact shim, and the exec target.
with stable_case() as (root, source, bin_dir):
    done = install(root, source, bin_dir)
    shim = bin_dir / "code-limit"
    check("1 a fresh install exits 0", done.returncode == 0, done.stdout + done.stderr)
    check("1 the shim exists", shim.is_file())
    want = expected_shim(report_of(source))
    check("1 the shim is EXACTLY the three expected lines",
          shim.read_text(encoding="utf-8") == want,
          f"want:\n{want}\ngot:\n{shim.read_text(encoding='utf-8')}")
    lines = shim.read_text(encoding="utf-8").splitlines()
    check("1 the shim is three lines and no more", len(lines) == 3, repr(lines))
    check("1 its single exec targets the shipped report",
          lines[2] == f"exec python3 {quoted(str(report_of(source)))} \"$@\"", lines[2])
    check("1 the shim is executable", bool(shim.stat().st_mode & stat.S_IXUSR))
    check("1 the shim is mode 755", stat.S_IMODE(shim.stat().st_mode) == 0o755,
          oct(stat.S_IMODE(shim.stat().st_mode)))
    check("1 the shim carries no report logic",
          not any(token in shim.read_text(encoding="utf-8")
                  for token in ("rateLimit", "method", "app-server", "usedPercent")))
    check("1 the install reports the action", "code-limit: created" in done.stdout, done.stdout)
    check("1 the install states which interpreter the command will use",
          "code-limit will run " in done.stdout, done.stdout)
    check("1 no legacy name is manufactured",
          not (bin_dir / "claude-limit").exists() and not (bin_dir / "claude-limits").exists())
    check("1 nothing else is created in the bin dir",
          sorted(p.name for p in bin_dir.iterdir()) == ["code-limit"],
          str(sorted(p.name for p in bin_dir.iterdir())))

    # 2 -- idempotent.
    again = install(root, source, bin_dir)
    check("2 a second install exits 0", again.returncode == 0, again.stdout + again.stderr)
    check("2 the shim is byte-identical", shim.read_text(encoding="utf-8") == want)
    check("2 the second run says so", "already installed" in again.stdout, again.stdout)

    # 3 -- runs as a command, from a directory that is not the repository.
    env = sandbox_env(root)
    empty = run_command(shim, [], env, cwd="/")
    check("3 the installed command runs from /", "code-limits" in empty.stdout, empty.stdout[:400])
    check("3 an empty HOME exits 1 per the report's own contract", empty.returncode == 1,
          f"rc={empty.returncode}\n{empty.stdout}{empty.stderr}")
    check("3 it names both vendor groups",
          "Claude Code" in empty.stdout and "Codex" in empty.stdout, empty.stdout[:400])

    # 4 -- exit status passes through, across three distinct codes.
    profile = make_claude_profile(root, "profile-a")
    home = make_codex_home(root, "codex-a")
    for label, args, want_rc in (
        ("0", ["--claude-profile", str(profile), "--codex-home", str(home)], 0),
        ("1", [], 1),
        ("2", ["--nonesuch"], 2),
    ):
        via_shim = run_command(shim, args, sandbox_env(root), cwd="/")
        direct = subprocess.run(
            [sys.executable, str(source / "report_limits.py")] + args,
            capture_output=True, text=True, timeout=120, env=sandbox_env(root), cwd="/")
        check(f"4 exit {label} matches the script's own", via_shim.returncode == direct.returncode,
              f"shim={via_shim.returncode} direct={direct.returncode}\n{via_shim.stderr[:300]}")
        check(f"4 exit {label} is the expected code", via_shim.returncode == want_rc,
              f"rc={via_shim.returncode}\n{via_shim.stdout[-400:]}{via_shim.stderr[-400:]}")

    # 5 -- options reach the parser.
    picked = run_command(shim, ["--claude-profile", str(profile)], sandbox_env(root), cwd="/")
    check("5 --claude-profile selects that profile", "profile-a" in picked.stdout,
          picked.stdout[:600])
    env_codex = sandbox_env(root)
    clean = run_command(shim, ["--claude-profile", str(profile), "--codex-home", str(home)],
                        env_codex, cwd="/")
    check("5 --codex-home reaches the stub app-server and the run is clean",
          clean.returncode == 0, f"rc={clean.returncode}\n{clean.stdout}{clean.stderr}")
    live = run_command(shim, ["--live", "--claude-profile", str(profile)], sandbox_env(root),
                       cwd="/")
    check("5 --live takes the token path rather than tripping the parser",
          live.returncode == 1 and "usage:" not in live.stderr,
          f"rc={live.returncode}\n{live.stderr[:300]}")

    # 6 -- an unknown flag.
    bad = run_command(shim, ["--nonesuch"], sandbox_env(root), cwd="/")
    check("6 an unknown flag exits 2", bad.returncode == 2, str(bad.returncode))
    check("6 argparse's own usage reaches stderr through the shim",
          "usage:" in bad.stderr and "--nonesuch" in bad.stderr, bad.stderr[:300])

    # 7 -- the no-redemption guarantee, measured through the installed command.
    env_methods = sandbox_env(root)
    run_command(shim, ["--claude-profile", str(profile), "--codex-home", str(home)],
                env_methods, cwd="/")
    sent = json.loads(Path(env_methods["STUB_METHODS"]).read_text(encoding="utf-8"))
    check("7 exactly the three read-only methods are sent through the shim",
          sent == ["initialize", "initialized", "account/rateLimits/read"], str(sent))
    check("7 no redeem method is sent",
          not any(m and "Credit" in m and "consume" in m.lower() for m in sent), str(sent))

    # 15 -- the reset-voucher contract, through the installed command.
    zero_env = sandbox_env(root)
    zero_env["STUB_RESULT"] = json.dumps(codex_result({"availableCount": 0, "credits": []}))
    zero = run_command(shim, ["--claude-profile", str(profile), "--codex-home", str(home)],
                       zero_env, cwd="/")
    voucher = [ln for ln in zero.stdout.splitlines() if "reset coupons" in ln]
    # The FIELD, not a substring: `"0" in line` is also true of a row reading 10, and a
    # falsy-boundary mutant rendering `count or 10` would sail through that. The row carries a
    # trailing note after a double space, so the field is what precedes it.
    check("15 availableCount 0 renders as exactly 0, not as an absence",
          len(voucher) == 1 and coupon_field(voucher[0]) == "0", str(voucher))
    check("15 a zero count is still a clean run", zero.returncode == 0,
          f"rc={zero.returncode}\n{zero.stdout}")
    gone_env = sandbox_env(root)
    gone_env["STUB_RESULT"] = json.dumps(codex_result("omitted"))
    gone = run_command(shim, ["--claude-profile", str(profile), "--codex-home", str(home)],
                       gone_env, cwd="/")
    absent = [ln for ln in gone.stdout.splitlines() if "reset coupons" in ln]
    check("15 an omitted field renders as exactly `not reported`",
          len(absent) == 1 and coupon_field(absent[0]) == "not reported", str(absent))
    check("15 an omitted field is still a clean run", gone.returncode == 0,
          f"rc={gone.returncode}\n{gone.stdout}")

# 8 / 9 -- a foreign file at a managed name, including one that quotes the marker.
for label, body in (
    ("8", "#!/bin/sh\necho somebody else's tool\n"),
    ("9", f"#!/usr/bin/env python3\n# documents the shim: {MARKER}\nprint('mine')\n"),
):
    with stable_case() as (root, source, bin_dir):
        bin_dir.mkdir()
        foreign = bin_dir / "code-limit"
        foreign.write_text(body, encoding="utf-8")
        foreign.chmod(0o755)
        done = install(root, source, bin_dir)
        check(f"{label} a foreign code-limit is refused", done.returncode == 1, done.stdout)
        check(f"{label} its bytes are untouched", foreign.read_text(encoding="utf-8") == body)
        check(f"{label} the refusal names the path", str(foreign) in done.stdout, done.stdout)
        check(f"{label} the refusal says how to proceed", "--force" in done.stdout, done.stdout)
        forced = install(root, source, bin_dir, force=True)
        check(f"{label} --force exits 0", forced.returncode == 0, forced.stdout + forced.stderr)
        check(f"{label} --force installs the shim",
              foreign.read_text(encoding="utf-8") == expected_shim(report_of(source)))
        check(f"{label} --force reports a replacement", "replaced" in forced.stdout, forced.stdout)

# 10 -- a symlink is foreign even when its target is a genuine shim.
with stable_case() as (root, source, bin_dir):
    other = root / "elsewhere"
    other.mkdir()
    install(root, source, other)                      # a genuine shim, somewhere else
    genuine = other / "code-limit"
    before = genuine.read_text(encoding="utf-8")
    bin_dir.mkdir()
    (bin_dir / "code-limit").symlink_to(genuine)
    done = install(root, source, bin_dir)
    check("10 a symlink at a managed name is foreign", done.returncode == 1, done.stdout)
    check("10 it is still a symlink", (bin_dir / "code-limit").is_symlink())
    forced = install(root, source, bin_dir, force=True)
    check("10 --force exits 0", forced.returncode == 0, forced.stdout + forced.stderr)
    check("10 --force leaves a regular file, not a link",
          not (bin_dir / "code-limit").is_symlink() and (bin_dir / "code-limit").is_file())
    # What this proves is that the install did not FOLLOW the link: a plain `open(dest, "w")`
    # writes through a symlink and rewrites whatever it points at, which here would be another
    # of the user's files. It says nothing about the pre-unlink question -- unlinking a symlink
    # removes the link, not its target, so both orderings leave these bytes alone. That one is a
    # crash-window property, argued in the installer and deliberately not claimed as tested.
    check("10 the former target's bytes are UNCHANGED -- the install never wrote through the link",
          genuine.read_text(encoding="utf-8") == before)

# 11 -- both legacy names, parameterised so neither can be implemented and the other forgotten.
for legacy in ("claude-limit", "claude-limits"):
    with stable_case() as (root, source, bin_dir):
        bin_dir.mkdir()
        body = f"#!/bin/sh\necho stale {legacy}\n"
        old = bin_dir / legacy
        old.write_text(body, encoding="utf-8")
        old.chmod(0o755)
        done = install(root, source, bin_dir)
        check(f"11 {legacy} present and foreign is refused", done.returncode == 1, done.stdout)
        check(f"11 {legacy} is named in the output", legacy in done.stdout, done.stdout)
        check(f"11 {legacy} keeps its bytes", old.read_text(encoding="utf-8") == body)
        check(f"11 code-limit itself is still installed alongside {legacy}",
              (bin_dir / "code-limit").is_file())
        forced = install(root, source, bin_dir, force=True)
        check(f"11 --force migrates {legacy}", forced.returncode == 0,
              forced.stdout + forced.stderr)
        check(f"11 {legacy} now IS the shim",
              old.read_text(encoding="utf-8") == expected_shim(report_of(source)))
        other_name = "claude-limits" if legacy == "claude-limit" else "claude-limit"
        check(f"11 the absent {other_name} is not manufactured", not (bin_dir / other_name).exists())

# 12 -- a bin dir that is a regular file.
with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    source = make_source(root, "stable", "skills", "code-limits")
    occupied = root / "not-a-dir"
    occupied.write_text("x", encoding="utf-8")
    done = install(root, source, occupied)
    check("12 a bin dir that is a file exits 1", done.returncode == 1, done.stdout + done.stderr)
    check("12 it says so without a traceback",
          "Traceback" not in done.stderr and "not a directory" in done.stderr, done.stderr[:300])

# 13 -- quoting, where the quoted value actually goes: the embedded report path.
with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    source = make_source(root, "it's a dir", "skills", "code-limits")
    bin_dir = root / "bin"
    done = install(root, source, bin_dir)
    shim = bin_dir / "code-limit"
    check("13 installing from a quote-and-space path exits 0", done.returncode == 0,
          done.stdout + done.stderr)
    report = report_of(source)
    check("13 the shim quotes the embedded path POSIX-style",
          shim.read_text(encoding="utf-8") == expected_shim(report),
          shim.read_text(encoding="utf-8"))
    check("13 the quoting is the closing/literal/reopening form",
          "'\"'\"'" in shim.read_text(encoding="utf-8"), shim.read_text(encoding="utf-8"))
    ran = run_command(shim, [], sandbox_env(root), cwd="/")
    check("13 and the command still runs", "code-limits" in ran.stdout,
          f"rc={ran.returncode}\n{ran.stdout[:300]}{ran.stderr[:300]}")

# 13b -- the PATH advisory is shell code too.
with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    source = make_source(root, "stable", "skills", "code-limits")
    bin_dir = root / "b in's"
    done = install(root, source, bin_dir)
    check("13b the advisory quotes the bin dir exactly",
          f"export PATH={quoted(str(bin_dir))}:$PATH" in done.stdout, done.stdout)
    check("13b the advisory fires only when the dir is off PATH",
          "is not on PATH" in done.stdout, done.stdout)
    # And it does NOT fire when the directory IS on PATH.
    env = sandbox_env(root, on_path=bin_dir)
    on = install(root, source, bin_dir, env=env)
    check("13b no advisory when the bin dir is already on PATH",
          "is not on PATH" not in on.stdout, on.stdout)

# 14 -- a version-scoped plugin cache is refused; the same copy elsewhere installs.
with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    cached = make_source(root, "plugins", "cache", "lazyants", "mpp", "9.9.9",
                         "skills", "code-limits")
    bin_dir = root / "bin"
    done = install(root, cached, bin_dir)
    check("14 a version-scoped cache source is refused", done.returncode == 1,
          done.stdout + done.stderr)
    check("14 the refusal names the path", str(report_of(cached)) in done.stderr,
          done.stderr[:400])
    check("14 the refusal explains and redirects",
          "garbage-collected" in done.stderr and "marketplaces" in done.stderr, done.stderr[:400])
    check("14 nothing was written", not bin_dir.exists() or not any(bin_dir.iterdir()))
    # The other direction, so the check cannot pass by refusing everything.
    stable = make_source(root, "stable", "skills", "code-limits")
    ok = install(root, stable, bin_dir)
    check("14 the same scripts under a stable layout install", ok.returncode == 0,
          ok.stdout + ok.stderr)

# 14b -- a GENUINELY shallow source, the one an unguarded parents[6] tracebacks on.
# Deliberately under /tmp and not under the per-user temp root: `tempfile.mkdtemp()` returns a
# path many levels deep on both macOS and CI, so a "shallow" fixture built there has eight
# parents anyway and the missing length guard sails past it. Measured: dropping the guard
# survived this suite until the fixture moved here.
shallow_root = Path(tempfile.mkdtemp(dir="/tmp", prefix="cl"))
try:
    depth = len((shallow_root / "code-limits/scripts/report_limits.py").resolve().parents)
    check("14b the fixture really is shallow -- fewer than eight parents", depth < 8, str(depth))
    shallow = make_source(shallow_root, "code-limits")
    ran = install(shallow_root, shallow, shallow_root / "bin")
    check("14b a shallow source installs rather than raising IndexError", ran.returncode == 0,
          ran.stdout + ran.stderr)
    check("14b and it did not traceback", "Traceback" not in ran.stderr, ran.stderr[:300])
    check("14b the shim it wrote is the expected one",
          (shallow_root / "bin/code-limit").read_text(encoding="utf-8")
          == expected_shim(report_of(shallow)))
finally:
    shutil.rmtree(shallow_root, ignore_errors=True)

# 16 -- the marker is not a substring test in the other direction either: a shim whose exec line
# was edited by hand is no longer ours, so it is not silently rewritten.
with stable_case() as (root, source, bin_dir):
    install(root, source, bin_dir)
    shim = bin_dir / "code-limit"
    tampered = shim.read_text(encoding="utf-8").replace(
        "exec python3 ", "exec python3 -c 'import os' # ")
    shim.write_text(tampered, encoding="utf-8")
    done = install(root, source, bin_dir)
    check("16 a hand-edited exec line makes the file foreign", done.returncode == 1, done.stdout)
    check("16 and it is left alone", shim.read_text(encoding="utf-8") == tampered)
    # A trailing fourth line likewise: the pattern admits three lines and nothing longer.
    shim.write_text(expected_shim(report_of(source)) + "echo extra\n", encoding="utf-8")
    fourth = install(root, source, bin_dir)
    check("16 a fourth line makes the file foreign", fourth.returncode == 1, fourth.stdout)

# 18 -- a crafted file that KEEPS the exec prefix and suffix but smuggles a second command.
# The predicate this kills accepted `'.+'` between them, so `'x' ; echo mine # '` matched: a
# quoted region, another command, and a comment that eats the closing quote.
with stable_case() as (root, source, bin_dir):
    bin_dir.mkdir()
    crafted = (f"#!/bin/sh\n{MARKER}\n"
               "exec python3 'x' ; echo foreign-owned # ' \"$@\"\n")
    planted = bin_dir / "code-limit"
    planted.write_text(crafted, encoding="utf-8")
    planted.chmod(0o755)
    done = install(root, source, bin_dir)
    check("18 a crafted exec line is NOT adopted", done.returncode == 1, done.stdout)
    check("18 its bytes are untouched", planted.read_text(encoding="utf-8") == crafted)
    check("18 it is reported as not ours", "not this plugin's shim" in done.stdout, done.stdout)

# 19 -- a genuine shim mangled to CRLF. `read_text` translates newlines, so it compares EQUAL to
# the real thing while `#!/bin/sh\r` makes the command exit 127.
with stable_case() as (root, source, bin_dir):
    install(root, source, bin_dir)
    shim = bin_dir / "code-limit"
    shim.write_bytes(expected_shim(report_of(source)).replace("\n", "\r\n").encode("utf-8"))
    check("19 the CRLF copy really is unrunnable", unrunnable(shim, sandbox_env(root)))
    done = install(root, source, bin_dir)
    check("19 a CRLF copy is not reported as already installed",
          "already installed" not in done.stdout, done.stdout)
    check("19 it is treated as foreign", done.returncode == 1, done.stdout)
    forced = install(root, source, bin_dir, force=True)
    check("19 --force repairs it", forced.returncode == 0, forced.stdout + forced.stderr)
    check("19 the bytes are the shim's again",
          shim.read_bytes() == expected_shim(report_of(source)).encode("utf-8"))
    fixed = run_command(shim, [], sandbox_env(root), cwd="/")
    check("19 and it runs again", "code-limits" in fixed.stdout, fixed.stdout[:200])

# 20 -- an exact shim whose execute bits were lost. Content equality is not "installed": the
# command exits 126, and reporting success over that is the same lie as reporting it over a
# missing file.
with stable_case() as (root, source, bin_dir):
    install(root, source, bin_dir)
    shim = bin_dir / "code-limit"
    shim.chmod(0o644)
    check("20 a mode-644 shim really is unrunnable", unrunnable(shim, sandbox_env(root)))
    done = install(root, source, bin_dir)
    check("20 the rerun does not claim it was already installed",
          "already installed" not in done.stdout, done.stdout)
    check("20 the rerun exits 0", done.returncode == 0, done.stdout + done.stderr)
    check("20 the mode is restored", stat.S_IMODE(shim.stat().st_mode) == 0o755,
          oct(stat.S_IMODE(shim.stat().st_mode)))
    alive = run_command(shim, [], sandbox_env(root), cwd="/")
    check("20 and the command runs again", "code-limits" in alive.stdout, alive.stdout[:200])

# 21 -- a managed name that cannot be written. Without a handler the OSError ends the run past
# every remaining name as a traceback, and the staging file it leaves behind is invisible.
with stable_case() as (root, source, bin_dir):
    bin_dir.mkdir()
    (bin_dir / "code-limit").mkdir()
    done = install(root, source, bin_dir, force=True)
    check("21 an unwritable managed name exits 1", done.returncode == 1,
          done.stdout + done.stderr)
    check("21 it does not traceback", "Traceback" not in done.stderr, done.stderr[:300])
    check("21 the warning names it", "NOT installed" in done.stdout, done.stdout)
    staged = [q.name for q in bin_dir.iterdir() if q.name.startswith(".code-limit.")]
    check("21 no staging file is left behind", staged == [], str(staged))

# 24 -- a planted file whose target is canonically quoted but carries a control character.
# The installer refuses to EMIT such a path, so a decoder that accepts one recognises a shim it
# could never have written -- and adopts somebody else's executable on the strength of it.
with stable_case() as (root, source, bin_dir):
    bin_dir.mkdir()
    planted = bin_dir / "code-limit"
    tabbed = (f"#!/bin/sh\n{MARKER}\n"
              f"exec python3 {quoted('/tmp/foreign\ttool')} \"$@\"\n")
    planted.write_text(tabbed, encoding="utf-8")
    planted.chmod(0o755)
    done = install(root, source, bin_dir)
    check("24 a control-bearing target is NOT ours", done.returncode == 1, done.stdout)
    check("24 its bytes are untouched", planted.read_text(encoding="utf-8") == tabbed)
    check("24 it is reported as not ours", "not this plugin's shim" in done.stdout, done.stdout)
    forced = install(root, source, bin_dir, force=True)
    check("24 --force still replaces it", forced.returncode == 0, forced.stdout + forced.stderr)
    check("24 and installs the real shim",
          planted.read_text(encoding="utf-8") == expected_shim(report_of(source)))

# 25 -- an exact shim that is over-permissive. 0755 is the mode, not "some execute bit": a
# world-writable command on PATH is a worse outcome than a missing one, and reporting it as
# already installed leaves it there.
with stable_case() as (root, source, bin_dir):
    install(root, source, bin_dir)
    shim = bin_dir / "code-limit"
    shim.chmod(0o777)
    done = install(root, source, bin_dir)
    check("25 an 0777 shim is not reported as already installed",
          "already installed" not in done.stdout, done.stdout)
    check("25 the rerun exits 0", done.returncode == 0, done.stdout + done.stderr)
    check("25 the mode is brought back to 0755", stat.S_IMODE(shim.stat().st_mode) == 0o755,
          oct(stat.S_IMODE(shim.stat().st_mode)))

# 22 -- the staging primitive itself, imported rather than driven, because the window it closes
# cannot be produced from outside: `place` only reaches the no-clobber path when the name did NOT
# exist a moment earlier. So this pins the primitive's contract and says plainly that the race
# between the check and the link is NOT covered by any test here.
sys.path.insert(0, str(SCRIPTS))
import install_code_limit as installer                                      # noqa: E402
with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    occupied = root / "taken"
    occupied.write_text("someone else's", encoding="utf-8")
    refused = False
    try:
        installer.write_atomically(occupied, b"ours", clobber=False)
    except FileExistsError:
        refused = True
    check("22 clobber=False refuses an existing name", refused)
    check("22 and leaves its bytes alone",
          occupied.read_text(encoding="utf-8") == "someone else's")
    check("22 no staging file survives the refusal",
          [q.name for q in root.iterdir() if q.name.startswith(".taken.")] == [])
    installer.write_atomically(occupied, b"ours", clobber=True)
    check("22 clobber=True does replace it", occupied.read_bytes() == b"ours")
    check("22 and the staged file arrives as 0755, set on the DESCRIPTOR",
          stat.S_IMODE(occupied.stat().st_mode) == 0o755,
          oct(stat.S_IMODE(occupied.stat().st_mode)))
    check("22 and cleans up after itself",
          [q.name for q in root.iterdir() if q.name.startswith(".taken.")] == [])

# 23 -- the shim grammar, at the level the predicate reasons about.
check("23 a canonical quote round-trips",
      installer.unquote_canonical(installer.posix_quote("it's /a b")) == "it's /a b")
for evil in ("'x' ; echo mine # '", "'a'b'", "no quotes", "'", ""):
    check(f"23 {evil!r} is not canonical", installer.unquote_canonical(evil) is None)
check("23 a fourth line is not a shim",
      installer.shim_target((expected_shim(REPORT) + "echo x\n").encode("utf-8")) is None)
check("23 CRLF bytes are not a shim",
      installer.shim_target(expected_shim(REPORT).replace("\n", "\r\n").encode("utf-8")) is None)
check("23 the real thing is a shim",
      installer.shim_target(expected_shim(REPORT).encode("utf-8")) == str(REPORT))
# The decoder's language must equal the emitter's, checked on the predicate they share.
for bad in ("/tmp/a\tb", "/tmp/a\nb", "/tmp/a\x00b", ""):
    check(f"23 {bad!r} is not emittable", not installer.emittable(bad))
    check(f"23 a shim naming {bad!r} is not ours",
          installer.shim_target(
              f"#!/bin/sh\n{MARKER}\nexec python3 {quoted(bad)} \"$@\"\n".encode("utf-8"))
          is None)
check("23 an ordinary path is emittable", installer.emittable("/tmp/a b/it's.py"))
# A lone surrogate is how a non-UTF-8 path arrives on Linux. Screened here rather than left to
# raise UnicodeEncodeError out of the encode step, past every handler, as a traceback.
check("23 a lone surrogate is not emittable", not installer.emittable("/tmp/x\udcffy.py"))
check("23 the surrogate range boundaries are rejected",
      not installer.emittable("/tmp/\ud800.py") and not installer.emittable("/tmp/\udfff.py"))
check("23 a non-ASCII but well-formed path is still emittable",
      installer.emittable("/tmp/\u00e9\u4e2d.py"))

# 17 -- the installer refuses a source whose report is missing, rather than shimming onto nothing.
with stable_case() as (root, source, bin_dir):
    (source / "report_limits.py").unlink()
    done = install(root, source, bin_dir)
    check("17 a missing report is refused", done.returncode == 1, done.stdout + done.stderr)
    check("17 the refusal names the missing file",
          "report_limits.py" in done.stderr and "Traceback" not in done.stderr, done.stderr[:300])

print(f"ran {checks} checks")
if failures:
    print(f"FAIL ({len(failures)}):")
    for failure in failures:
        print(f"  {failure}")
    sys.exit(1)

# A case that raises before its checks run, or a deleted fixture block, otherwise subtracts
# silently: the remaining cases pass, the run exits 0, and that reads exactly like full coverage.
MIN_CHECKS = 140
if checks < MIN_CHECKS:
    print(f"FAIL: only {checks} checks ran, expected at least {MIN_CHECKS}")
    sys.exit(1)
print("PASS")
