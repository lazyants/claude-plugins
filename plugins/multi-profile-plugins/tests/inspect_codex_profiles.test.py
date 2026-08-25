#!/usr/bin/env python3
"""Drive inspect_codex_profiles.py as a subprocess against fixture Codex homes.

Every case runs the SCRIPT, not its helpers: a short-circuit added ahead of a
check, or a wiring line deleted from main(), has to show up here. Fixture homes
are built under a temp dir and passed explicitly, so nothing reads the real
`~/.codex*` and no case depends on how this machine happens to be logged in.

Dependency-free: run it with `python3 inspect_codex_profiles.test.py`.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPT = (
    Path(__file__).resolve().parent.parent
    / "skills/multi-profile-codex/scripts/inspect_codex_profiles.py"
)

# A credential shape that must never reach stdout, planted in every fixture.
FAKE_API_KEY = "sk-test-MUSTNEVERBEPRINTED-DEADBEEF"
FAKE_TOKEN = "eyJhbGciOiJUEST.MUSTNEVERBEPRINTED.token"
FAKE_MCP_SECRET = "mcp-env-MUSTNEVERBEPRINTED-CAFEBABE"

failures: list[str] = []
checks = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global checks
    checks += 1
    if not condition:
        failures.append(f"{name}: {detail}" if detail else name)


def make_home(root: Path, name: str, *, account: str, config: str = "") -> Path:
    """Create one fixture Codex home with an auth.json and a config.toml."""
    home = root / name
    home.mkdir(parents=True)
    (home / "auth.json").write_text(
        json.dumps(
            {
                "OPENAI_API_KEY": FAKE_API_KEY,
                "auth_mode": "chatgpt",
                "tokens": {"account_id": account, "access_token": FAKE_TOKEN},
            }
        )
    )
    (home / "config.toml").write_text(
        'model = "gpt-5.6-sol"\n'
        "\n"
        "[mcp_servers.probe.env]\n"
        f'API_KEY = "{FAKE_MCP_SECRET}"\n'
        "\n" + config
    )
    return home


def run(*profiles: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *(str(p) for p in profiles)],
        capture_output=True,
        text=True,
        check=False,
    )


def assert_no_secret(name: str, result: subprocess.CompletedProcess[str]) -> None:
    blob = result.stdout + result.stderr
    for secret in (FAKE_API_KEY, FAKE_TOKEN, FAKE_MCP_SECRET):
        check(f"{name}: no credential in output", secret not in blob, f"leaked {secret[:12]}…")
    # The account fingerprint is deliberately truncated, so the full id must not appear.
    check(
        f"{name}: account id truncated",
        "aaaaaaaa-1111-2222-3333-444444444444" not in blob,
        "printed a full account_id",
    )


# ---------------------------------------------------------------------------
# 1. A clean three-profile topology passes.
# ---------------------------------------------------------------------------
with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    a = make_home(root, ".codex", account="aaaaaaaa-1111-2222-3333-444444444444")
    b = make_home(root, ".codex2", account="bbbbbbbb-1111-2222-3333-444444444444")
    c = make_home(root, ".codex3", account="cccccccc-1111-2222-3333-444444444444")
    r = run(a, b, c)
    check("clean: exit 0", r.returncode == 0, f"exit={r.returncode}\n{r.stdout}")
    check("clean: says PASS", "PASS —" in r.stdout, r.stdout)
    check("clean: no WARN", "WARN" not in r.stdout, r.stdout)
    assert_no_secret("clean", r)

# ---------------------------------------------------------------------------
# 2. The real trap: a config copied from another home keeps its absolute paths.
#    All three shapes the live machine actually had are covered — a bare value,
#    a `:`-joined search path, and a path buried inside a JSON blob.
# ---------------------------------------------------------------------------
with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    a = make_home(root, ".codex", account="aaaaaaaa-1111-2222-3333-444444444444")
    copied = (
        f'notify = ["{a}/computer-use/Client.app/Contents/MacOS/Client", "turn-ended"]\n'
        "\n"
        "[marketplaces.openai-bundled]\n"
        f'source = "{a}/.tmp/bundled-marketplaces/openai-bundled"\n'
        "\n"
        "[mcp_servers.node_repl.env]\n"
        f'CODEX_HOME = "{a}"\n'
        f'NODE_REPL_TRUSTED_CODE_PATHS = "{a}:/Applications/ChatGPT.app/Contents/Resources"\n'
        f'NODE_REPL_TRUSTED_SERVICES = \'{{"browser":"{a}/plugins/cache/browser/service.mjs"}}\'\n'
        # The shape that makes printing a matched value unsafe: ONE string holding both a
        # path that matches and a credential that must not be echoed. A report that shows
        # "the value that matched" leaks the token here while looking like a clean finding.
        # The secrets come FIRST, before the path: a printer that truncates the value to a
        # "safe" length would otherwise cut them off and let the assertion pass vacuously.
        f'SERVICE_BLOB = \'{{"apiKey":"{FAKE_API_KEY}","token":"{FAKE_TOKEN}",'
        f'"mcp":"{FAKE_MCP_SECRET}","root":"{a}/plugins"}}\'\n'
    )
    b = make_home(root, ".codex2", account="bbbbbbbb-1111-2222-3333-444444444444", config=copied)
    r = run(a, b)
    check("copied config: exit 1", r.returncode == 1, f"exit={r.returncode}\n{r.stdout}")
    check("copied config: reports pins", "PIN(S) into another profile" in r.stdout, r.stdout)
    for key in (
        "notify[0]",
        "marketplaces.openai-bundled.source",
        "mcp_servers.node_repl.env.CODEX_HOME",
        "mcp_servers.node_repl.env.NODE_REPL_TRUSTED_CODE_PATHS",
        "mcp_servers.node_repl.env.NODE_REPL_TRUSTED_SERVICES",
        "mcp_servers.node_repl.env.SERVICE_BLOB",
    ):
        check(f"copied config: names {key}", key in r.stdout, r.stdout)
    check(
        "copied config: the CLEAN profile is still reported clean",
        ".codex " in r.stdout and "clean" in r.stdout,
        r.stdout,
    )
    # The key is reported; the value it holds is not. Asserted on the blob's own distinctive
    # substring so the check cannot be satisfied by the secrets simply being absent.
    check("copied config: value not echoed", '"root":' not in r.stdout, r.stdout)
    assert_no_secret("copied config", r)

# ---------------------------------------------------------------------------
# 3. The boundary case a substring match gets wrong: `.codex` is a substring of
#    `.codex2`. A profile referencing its OWN home, and a profile referencing a
#    same-prefix directory that is not a profile at all, are both clean.
# ---------------------------------------------------------------------------
with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    a = make_home(root, ".codex", account="aaaaaaaa-1111-2222-3333-444444444444")
    b = root / ".codex2"
    b = make_home(
        root,
        ".codex2",
        account="bbbbbbbb-1111-2222-3333-444444444444",
        config=(
            "[mcp_servers.own.env]\n"
            f'CODEX_HOME = "{root / ".codex2"}"\n'
            f'SERVICES = \'{{"browser":"{root / ".codex2"}/plugins/service.mjs"}}\'\n'
            f'BACKUP = "{root / ".codex2.bak"}/config.toml"\n'
            f'OLD = "{root / ".codex-old"}/sessions"\n'
            # `+` is a legal filename character, so a name continuing past the match is a
            # DIFFERENT directory even though `+` is not a letter, digit, dot or hyphen.
            f'PLUS = "{root / ".codex+work"}/sessions"\n'
            # A same-suffix path under a backup mount. Only the character BEFORE the match
            # distinguishes it from a real reference to the base profile.
            f'MIRROR = "/Volumes/backup{a}/plugins"\n'
            f'MIRROR_JSON = \'{{"p":"/Volumes/backup{a}"}}\'\n'
        ),
    )
    r = run(a, b)
    check("boundary: exit 0", r.returncode == 0, f"exit={r.returncode}\n{r.stdout}")
    check("boundary: no pin reported", "PIN(S)" not in r.stdout, r.stdout)
    check("boundary: says PASS", "PASS —" in r.stdout, r.stdout)

# ---------------------------------------------------------------------------
# 3b. The same boundary rule must still FIRE where it should. Without this, case 3 is
#     satisfiable by a matcher that has simply stopped matching anything at all.
# ---------------------------------------------------------------------------
with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    a = make_home(root, ".codex", account="aaaaaaaa-1111-2222-3333-444444444444")
    b = make_home(
        root,
        ".codex2",
        account="bbbbbbbb-1111-2222-3333-444444444444",
        config=(
            "[mcp_servers.probe2.env]\n"
            f'EXACT = "{a}"\n'
            f'SUBPATH = "{a}/sessions"\n'
            f'JOINED = "/opt/bin:{a}/plugins"\n'
            f'BLOB = \'{{"svc":"{a}/plugins/x.mjs"}}\'\n'
        ),
    )
    r = run(a, b)
    check("boundary fires: exit 1", r.returncode == 1, f"exit={r.returncode}\n{r.stdout}")
    for key in ("EXACT", "SUBPATH", "JOINED", "BLOB"):
        check(f"boundary fires: names {key}", f"probe2.env.{key}" in r.stdout, r.stdout)

# ---------------------------------------------------------------------------
# 3c. An explicitly-passed directory that is not a profile must WARN, not pass. It skips
#     the auto-detect filter entirely, so a typo or a moved home otherwise reaches every
#     check as an empty profile and lands on the unconditional PASS line.
# ---------------------------------------------------------------------------
with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    a = make_home(root, ".codex", account="aaaaaaaa-1111-2222-3333-444444444444")
    r = run(a, root / ".codex-typo")
    check("missing dir: exit 1", r.returncode == 1, f"exit={r.returncode}\n{r.stdout}")
    check("missing dir: named", "not a Codex profile directory" in r.stdout, r.stdout)
    check("missing dir: no PASS line", "PASS —" not in r.stdout, r.stdout)

    empty = root / ".codex-empty"
    empty.mkdir()
    r = run(a, empty)
    check("empty dir: exit 1", r.returncode == 1, f"exit={r.returncode}\n{r.stdout}")
    check("empty dir: named", "no config.toml and no auth.json" in r.stdout, r.stdout)

# ---------------------------------------------------------------------------
# 3d. A real profile with credentials but no config.toml: its pins were NOT checked, so the
#     run is not a clean bill of health for it.
# ---------------------------------------------------------------------------
with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    a = make_home(root, ".codex", account="aaaaaaaa-1111-2222-3333-444444444444")
    b = make_home(root, ".codex2", account="bbbbbbbb-1111-2222-3333-444444444444")
    (b / "config.toml").unlink()
    r = run(a, b)
    check("no config.toml: exit 1", r.returncode == 1, f"exit={r.returncode}\n{r.stdout}")
    check("no config.toml: named", "pins not checked" in r.stdout.lower(), r.stdout)
    assert_no_secret("no config.toml", r)

# ---------------------------------------------------------------------------
# 4. Two homes sharing one auth.json file (symlinked): a logout in either
#    logs out both.
# ---------------------------------------------------------------------------
with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    a = make_home(root, ".codex", account="aaaaaaaa-1111-2222-3333-444444444444")
    b = make_home(root, ".codex2", account="bbbbbbbb-1111-2222-3333-444444444444")
    (b / "auth.json").unlink()
    os.symlink(a / "auth.json", b / "auth.json")
    r = run(a, b)
    check("shared auth: exit 1", r.returncode == 1, f"exit={r.returncode}\n{r.stdout}")
    check("shared auth: named", "share one auth.json" in r.stdout, r.stdout)
    assert_no_secret("shared auth", r)

# ---------------------------------------------------------------------------
# 5. Two homes, two separate files, but the SAME account — one usage pool, which
#    is the outcome the whole split exists to avoid.
# ---------------------------------------------------------------------------
with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    same = "dddddddd-1111-2222-3333-444444444444"
    a = make_home(root, ".codex", account=same)
    b = make_home(root, ".codex2", account=same)
    r = run(a, b)
    check("same account: exit 1", r.returncode == 1, f"exit={r.returncode}\n{r.stdout}")
    check("same account: named", "logged into the same account" in r.stdout, r.stdout)
    check("same account: not misreported as a shared file", "share one auth.json" not in r.stdout, r.stdout)

# ---------------------------------------------------------------------------
# 6. A shared content directory — the disk-saving symlink that lets one profile
#    delete the other's sessions.
# ---------------------------------------------------------------------------
with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    a = make_home(root, ".codex", account="aaaaaaaa-1111-2222-3333-444444444444")
    b = make_home(root, ".codex2", account="bbbbbbbb-1111-2222-3333-444444444444")
    (a / "sessions").mkdir()
    os.symlink(a / "sessions", b / "sessions")
    r = run(a, b)
    check("shared sessions: exit 1", r.returncode == 1, f"exit={r.returncode}\n{r.stdout}")
    check("shared sessions: named", "share `sessions`" in r.stdout, r.stdout)

# ---------------------------------------------------------------------------
# 7. A config.toml that is not valid TOML must WARN, not pass quietly — an
#    unparseable config reads exactly like a clean one otherwise.
# ---------------------------------------------------------------------------
with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    a = make_home(root, ".codex", account="aaaaaaaa-1111-2222-3333-444444444444")
    b = make_home(root, ".codex2", account="bbbbbbbb-1111-2222-3333-444444444444")
    (b / "config.toml").write_text("this is [not valid TOML\n")
    r = run(a, b)
    check("bad TOML: exit 1", r.returncode == 1, f"exit={r.returncode}\n{r.stdout}")
    check("bad TOML: named", "UNREADABLE" in r.stdout, r.stdout)

# ---------------------------------------------------------------------------
# 8. Two explicit profile dirs sharing a basename must stay distinguishable —
#    a basename-keyed report would collapse them and hide the finding.
# ---------------------------------------------------------------------------
with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    a = make_home(root / "one", ".codex", account="aaaaaaaa-1111-2222-3333-444444444444")
    b = make_home(root / "two", ".codex", account="aaaaaaaa-1111-2222-3333-444444444444")
    r = run(a, b)
    check("same basename: exit 1", r.returncode == 1, f"exit={r.returncode}\n{r.stdout}")
    check("same basename: full paths shown", str(a) in r.stdout and str(b) in r.stdout, r.stdout)

# ---------------------------------------------------------------------------
# 9. Auto-detect against a HOME with no Codex profile at all: exit 0, and say so.
# ---------------------------------------------------------------------------
with tempfile.TemporaryDirectory() as td:
    r = subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "HOME": td},
    )
    check("empty home: exit 0", r.returncode == 0, f"exit={r.returncode}\n{r.stdout}")
    check("empty home: says so", "No Codex profile directories found." in r.stdout, r.stdout)

# ---------------------------------------------------------------------------
# 10. Auto-detect must find `.codex*` dirs, and must NOT be fooled by a
#     `.codex-backups` tree whose config.toml sits one level down.
# ---------------------------------------------------------------------------
with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    make_home(root, ".codex", account="aaaaaaaa-1111-2222-3333-444444444444")
    make_home(root, ".codex2", account="bbbbbbbb-1111-2222-3333-444444444444")
    make_home(root / ".codex-backups", "20260825-111153", account="aaaaaaaa-1111-2222-3333-444444444444")
    r = subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "HOME": str(root)},
    )
    check("auto-detect: exit 0", r.returncode == 0, f"exit={r.returncode}\n{r.stdout}")
    check("auto-detect: found both profiles", "Profiles: .codex, .codex2" in r.stdout, r.stdout)
    check("auto-detect: skipped the backup tree", ".codex-backups" not in r.stdout, r.stdout)

print(f"ran {checks} checks")

# A case that raises before its checks run, or a fixture block deleted wholesale, subtracts
# silently: the remaining cases still pass and the run still exits 0. Assert the floor so a
# suite that stopped exercising most of the script cannot read as a clean one.
MIN_CHECKS = 60
if checks < MIN_CHECKS:
    print(f"FAIL: only {checks} checks ran, expected at least {MIN_CHECKS}")
    sys.exit(1)

if failures:
    print(f"FAIL ({len(failures)}):")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("PASS — inspect_codex_profiles.py")
