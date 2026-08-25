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

# Account ids used by the fixtures. Named because the literals appeared 39 times across the
# file, including one hardcoded a third way inside assert_no_secret -- a typo in any single
# copy would have quietly changed what a case was testing.
ACCOUNT_A = "aaaaaaaa-1111-2222-3333-444444444444"
ACCOUNT_B = "bbbbbbbb-1111-2222-3333-444444444444"
ACCOUNT_C = "cccccccc-1111-2222-3333-444444444444"
ACCOUNT_SHARED = "dddddddd-1111-2222-3333-444444444444"
# Same first eight characters as ACCOUNT_A, different account.
ACCOUNT_A_TWIN = "aaaaaaaa-9999-8888-7777-666666666666"

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
        if detail:
            # Indented: an unindented report puts the script's own "PASS —" line at column 0
            # underneath "FAIL (n):", which reads as a pass to a grep and to a reader.
            body = "\n".join(f"      {line}" for line in detail.splitlines())
            failures.append(f"{name}:\n{body}")
        else:
            failures.append(name)


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
    # The caller's config goes BEFORE the probe table, and the probe table goes LAST.
    # Appending after a table header silently reparents every top-level key the caller
    # writes: `notify = [...]` became `mcp_servers.probe.env.notify`, and the assertion
    # looking for "notify[0]" still passed, on a substring of the wrong key path.
    (home / "config.toml").write_text(
        'model = "gpt-5.6-sol"\n'
        "\n" + config + "\n"
        "[mcp_servers.probe.env]\n"
        f'API_KEY = "{FAKE_MCP_SECRET}"\n'
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
        ACCOUNT_A not in blob,
        "printed a full account_id",
    )


# ---------------------------------------------------------------------------
# 1. A clean three-profile topology passes.
# ---------------------------------------------------------------------------
with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    a = make_home(root, ".codex", account=ACCOUNT_A)
    b = make_home(root, ".codex2", account=ACCOUNT_B)
    c = make_home(root, ".codex3", account=ACCOUNT_C)
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
    a = make_home(root, ".codex", account=ACCOUNT_A)
    copied = (
        f'notify = ["{a}/computer-use/Client.app/Contents/MacOS/Client", "turn-ended"]\n'
        "\n"
        "[marketplaces.openai-bundled]\n"
        f'source = "{a}/.tmp/bundled-marketplaces/openai-bundled"\n'
        "\n"
        "[mcp_servers.node_repl.env]\n"
        f'CODEX_HOME = "{a}"\n'
        f'NODE_REPL_TRUSTED_CODE_PATHS = "{a}:/Applications/ChatGPT.app/Contents/Resources"\n'
        # The mirror shape: the home is not first, so it is PRECEDED by the `:` rather than
        # followed by one. Both ends of the boundary rule are exercised by this file only here.
        f'PATH_JOINED = "/opt/bin:{a}/plugins"\n'
        f'NODE_REPL_TRUSTED_SERVICES = \'{{"browser":"{a}/plugins/cache/browser/service.mjs"}}\'\n'
        # The shape that makes printing a matched value unsafe: ONE string holding both a
        # path that matches and a credential that must not be echoed. A report that shows
        # "the value that matched" leaks the token here while looking like a clean finding.
        # The secrets come FIRST, before the path: a printer that truncates the value to a
        # "safe" length would otherwise cut them off and let the assertion pass vacuously.
        f'SERVICE_BLOB = \'{{"apiKey":"{FAKE_API_KEY}","token":"{FAKE_TOKEN}",'
        f'"mcp":"{FAKE_MCP_SECRET}","root":"{a}/plugins"}}\'\n'
    )
    b = make_home(root, ".codex2", account=ACCOUNT_B, config=copied)
    r = run(a, b)
    check("copied config: exit 1", r.returncode == 1, f"exit={r.returncode}\n{r.stdout}")
    check("copied config: reports pins", "PIN(S) into another profile" in r.stdout, r.stdout)
    # Schema-chosen components survive; the MCP server's own name and the env var names do
    # not, because the file chose those. Asserted as shapes rather than as full key paths.
    for key in (
        "notify[0] ",
        "marketplaces.<redacted>.source",
        "mcp_servers.<redacted>.env.CODEX_HOME",
        "mcp_servers.<redacted>.env.<redacted>",
    ):
        check(f"copied config: names {key}", key in r.stdout, r.stdout)
    # The COUNT must not fall when several pins in one table redact to the same string: six
    # values pin the other home here, and dedup happens on the real key, not the rendered one.
    check("copied config: counts every pin", "7 PIN(S)" in r.stdout, r.stdout)
    check(
        "copied config: server name redacted",
        "node_repl" not in r.stdout and "openai-bundled" not in r.stdout,
        r.stdout,
    )
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
# 2b. A TOML table key is FILE CONTENT, not structure. Quoted-key syntax accepts arbitrary
#     text, and the key path is the one thing this report does print — so a credential in a
#     table name reached stdout while the value beside it was being carefully dropped. The
#     marker goes FIRST in the key so a redactor that only trimmed a tail would still fail.
# ---------------------------------------------------------------------------
with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    a = make_home(root, ".codex", account=ACCOUNT_A)
    b = make_home(
        root,
        ".codex2",
        account=ACCOUNT_B,
        config=(
            f'[mcp_servers."{FAKE_API_KEY}-in-key".env]\n'
            f'CODEX_HOME = "{root / ".codex"}"\n'
            f'[mcp_servers.plain.env."{FAKE_MCP_SECRET}"]\n'
            f'NESTED = "{root / ".codex"}/plugins"\n'
        ),
    )
    r = run(a, b)
    check("quoted key: exit 1", r.returncode == 1, f"exit={r.returncode}\n{r.stdout}")
    check("quoted key: pin still reported", "PIN(S) into another profile" in r.stdout, r.stdout)
    check("quoted key: position kept", "<redacted>" in r.stdout, r.stdout)
    # The bare components around it must survive, or the report stops naming the line to edit.
    check("quoted key: bare parts kept", "mcp_servers." in r.stdout, r.stdout)
    check("quoted key: CODEX_HOME still named", "CODEX_HOME" in r.stdout, r.stdout)
    assert_no_secret("quoted key", r)

# ---------------------------------------------------------------------------
# 3. The boundary case a substring match gets wrong: `.codex` is a substring of
#    `.codex2`. A profile referencing its OWN home, and a profile referencing a
#    same-prefix directory that is not a profile at all, are both clean.
# ---------------------------------------------------------------------------
with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    a = make_home(root, ".codex", account=ACCOUNT_A)
    b = make_home(
        root,
        ".codex2",
        account=ACCOUNT_B,
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
# 3c. An explicitly-passed directory that is not a profile must WARN, not pass. It skips
#     the auto-detect filter entirely, so a typo or a moved home otherwise reaches every
#     check as an empty profile and lands on the unconditional PASS line.
# ---------------------------------------------------------------------------
with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    a = make_home(root, ".codex", account=ACCOUNT_A)
    r = run(a, root / ".codex-typo")
    check("missing dir: exit 1", r.returncode == 1, f"exit={r.returncode}\n{r.stdout}")
    check("missing dir: named", "not a Codex profile directory" in r.stdout, r.stdout)
    check("missing dir: no PASS line", "PASS —" not in r.stdout, r.stdout)

    empty = root / ".codex-empty"
    empty.mkdir()
    r = run(a, empty)
    check("empty dir: exit 1", r.returncode == 1, f"exit={r.returncode}\n{r.stdout}")
    check("empty dir: named", "no readable config.toml or auth.json" in r.stdout, r.stdout)

# ---------------------------------------------------------------------------
# 3d. A real profile with credentials but no config.toml: its pins were NOT checked, so the
#     run is not a clean bill of health for it.
# ---------------------------------------------------------------------------
with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    a = make_home(root, ".codex", account=ACCOUNT_A)
    b = make_home(root, ".codex2", account=ACCOUNT_B)
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
    a = make_home(root, ".codex", account=ACCOUNT_A)
    b = make_home(root, ".codex2", account=ACCOUNT_B)
    (b / "auth.json").unlink()
    os.symlink(a / "auth.json", b / "auth.json")
    r = run(a, b)
    check("shared auth: exit 1", r.returncode == 1, f"exit={r.returncode}\n{r.stdout}")
    check("shared auth: named", "share ONE auth.json file" in r.stdout, r.stdout)
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
    check("same account: named", "logged into the SAME account" in r.stdout, r.stdout)
    check("same account: not misreported as a shared file", "share ONE auth.json file" not in r.stdout, r.stdout)

# ---------------------------------------------------------------------------
# 6. A shared content directory — the disk-saving symlink that lets one profile
#    delete the other's sessions.
# ---------------------------------------------------------------------------
with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    a = make_home(root, ".codex", account=ACCOUNT_A)
    b = make_home(root, ".codex2", account=ACCOUNT_B)
    (a / "sessions").mkdir()
    os.symlink(a / "sessions", b / "sessions")
    r = run(a, b)
    check("shared sessions: exit 1", r.returncode == 1, f"exit={r.returncode}\n{r.stdout}")
    check("shared sessions: named", "share their `sessions` directory" in r.stdout, r.stdout)

# ---------------------------------------------------------------------------
# 6b. auth_mode is rendered from a fixed set of labels, never echoed. The field sits in
#     the credential file, so a malformed profile that puts a secret there would print it
#     if the value were forwarded verbatim.
# ---------------------------------------------------------------------------
with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    a = make_home(root, ".codex", account=ACCOUNT_A)
    b = make_home(root, ".codex2", account=ACCOUNT_B)
    (b / "auth.json").write_text(
        json.dumps({
            "auth_mode": FAKE_API_KEY,
            "tokens": {"account_id": ACCOUNT_B},
        })
    )
    r = run(a, b)
    assert_no_secret("auth_mode echo", r)
    check("auth_mode echo: reported as unknown-mode", "unknown-mode" in r.stdout, r.stdout)

# ---------------------------------------------------------------------------
# 6c. A malformed auth.json means the credentials were NOT examined. Everything else about
#     the profile can be clean, so without this the run reaches the PASS line and states
#     that every profile has its own credentials — about a file it could not read.
# ---------------------------------------------------------------------------
with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    a = make_home(root, ".codex", account=ACCOUNT_A)
    b = make_home(root, ".codex2", account=ACCOUNT_B)
    (b / "auth.json").write_text("{not json at all")
    r = run(a, b)
    check("bad auth: exit 1", r.returncode == 1, f"exit={r.returncode}\n{r.stdout}")
    check("bad auth: no PASS line", "PASS —" not in r.stdout, r.stdout)
    check("bad auth: named", "credentials NOT checked" in r.stdout, r.stdout)

# ---------------------------------------------------------------------------
# 6d. A freshly seeded home with no auth.json is a real intended state, not a gap: it must
#     stay clean, or the check above would just be an exit-1 generator.
# ---------------------------------------------------------------------------
with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    a = make_home(root, ".codex", account=ACCOUNT_A)
    b = make_home(root, ".codex2", account=ACCOUNT_B)
    (b / "auth.json").unlink()
    r = run(a, b)
    check("not-logged-in: exit 0", r.returncode == 0, f"exit={r.returncode}\n{r.stdout}")
    check("not-logged-in: says PASS", "PASS —" in r.stdout, r.stdout)
    check("not-logged-in: labelled", "not-logged-in" in r.stdout, r.stdout)

# ---------------------------------------------------------------------------
# 6f. An auth.json that is present and parses but yields no comparable account id drops out
#     of the same-account comparison. Two homes on one account is the outcome the whole
#     split exists to avoid, so silently not checking for it must not read as clean. Both
#     routes here: no account_id at all, and one that fails the identifier shape.
# ---------------------------------------------------------------------------
for label_, tokens in (
    ("no account_id", {}),
    ("unusable account_id", {"account_id": "not a uuid at all!!"}),
):
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        a = make_home(root, ".codex", account=ACCOUNT_A)
        b = make_home(root, ".codex2", account=ACCOUNT_B)
        (b / "auth.json").write_text(json.dumps({"auth_mode": "chatgpt", "tokens": tokens}))
        r = run(a, b)
        check(f"{label_}: exit 1", r.returncode == 1, f"exit={r.returncode}\n{r.stdout}")
        check(f"{label_}: no PASS line", "PASS —" not in r.stdout, r.stdout)
        check(f"{label_}: named", "no comparable account id" in r.stdout, r.stdout)

# ---------------------------------------------------------------------------
# 6f2. An api-key login carries no account id BY DESIGN. Demanding one would make a valid
#      profile warn on every run forever — a check nobody can satisfy. It stays clean, and
#      the verdict says out loud that ownership was not compared rather than implying it was.
# ---------------------------------------------------------------------------
with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    a = make_home(root, ".codex", account=ACCOUNT_A)
    b = make_home(root, ".codex2", account=ACCOUNT_B)
    (b / "auth.json").write_text(json.dumps({"auth_mode": "apikey", "OPENAI_API_KEY": FAKE_API_KEY}))
    r = run(a, b)
    check("api-key: exit 0", r.returncode == 0, f"exit={r.returncode}\n{r.stdout}")
    check("api-key: not a gap", "credentials NOT checked" not in r.stdout, r.stdout)
    check("api-key: limitation stated", "could not be compared" in r.stdout, r.stdout)
    check(
        "api-key: does not claim distinct credentials",
        "every checked profile has its own credentials" not in r.stdout,
        r.stdout,
    )
    assert_no_secret("api-key", r)

# ---------------------------------------------------------------------------
# 6g0. A BROKEN symlink store points at nothing. Two of them aimed at the same missing
#      target must not be reported as sharing a directory that does not exist.
# ---------------------------------------------------------------------------
with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    a = make_home(root, ".codex", account=ACCOUNT_A)
    b = make_home(root, ".codex2", account=ACCOUNT_B)
    missing = root / "gone"
    os.symlink(missing, a / "sessions")
    os.symlink(missing, b / "sessions")
    r = run(a, b)
    check("broken symlink: exit 0", r.returncode == 0, f"exit={r.returncode}\n{r.stdout}")
    check("broken symlink: no share claim", "share their `sessions` directory" not in r.stdout, r.stdout)

# ---------------------------------------------------------------------------
# 6g. A store that cannot be stat'ed must not read as absent. Path.exists() answers False
#     for a permission error exactly as it does for a missing directory, so without this the
#     profile reaches PASS with the store never examined.
# ---------------------------------------------------------------------------
if os.geteuid() != 0:  # root ignores the mode bits, so the case cannot be staged
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        a = make_home(root, ".codex", account=ACCOUNT_A)
        b = make_home(root, ".codex2", account=ACCOUNT_B)
        (b / "sessions").mkdir()
        b.chmod(0o000)
        try:
            r = run(a, b)
        finally:
            b.chmod(0o700)  # or TemporaryDirectory cannot clean up
        check("blocked store: exit 1", r.returncode == 1, f"exit={r.returncode}\n{r.stdout}")
        check("blocked store: named", "is inaccessible" in r.stdout, r.stdout)
        check("blocked store: not called absent", "PASS —" not in r.stdout, r.stdout)
        # The store ROW must not say "absent" about a directory that is there. This is the
        # half that has to hold identically on every supported Python: the Path booleans
        # answer False for a permission error from 3.14 and raise below it.
        check("blocked store: row says unreadable", "=unreadable" in r.stdout, r.stdout)

# ---------------------------------------------------------------------------
# 6e. Two DIFFERENT accounts sharing the first eight characters are two accounts. The
#     eight-character form exists for display; comparing on it merges them.
# ---------------------------------------------------------------------------
with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    a = make_home(root, ".codex", account=ACCOUNT_A)
    b = make_home(root, ".codex2", account=ACCOUNT_A_TWIN)
    r = run(a, b)
    check("same prefix: exit 0", r.returncode == 0, f"exit={r.returncode}\n{r.stdout}")
    check(
        "same prefix: not called one account",
        "logged into the SAME account" not in r.stdout,
        r.stdout,
    )

# ---------------------------------------------------------------------------
# 7. A config.toml that is not valid TOML must WARN, not pass quietly — an
#    unparseable config reads exactly like a clean one otherwise.
# ---------------------------------------------------------------------------
with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    a = make_home(root, ".codex", account=ACCOUNT_A)
    b = make_home(root, ".codex2", account=ACCOUNT_B)
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
    a = make_home(root / "one", ".codex", account=ACCOUNT_A)
    b = make_home(root / "two", ".codex", account=ACCOUNT_A)
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
    make_home(root, ".codex", account=ACCOUNT_A)
    make_home(root, ".codex2", account=ACCOUNT_B)
    make_home(root / ".codex-backups", "20260825-111153", account=ACCOUNT_A)
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
MIN_CHECKS = 90
if checks < MIN_CHECKS:
    print(f"FAIL: only {checks} checks ran, expected at least {MIN_CHECKS}")
    sys.exit(1)

if failures:
    print(f"FAIL ({len(failures)}):")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("PASS — inspect_codex_profiles.py")
