#!/usr/bin/env python3
"""Health-check a Codex CLI multi-profile (CODEX_HOME) topology.

The Codex CLI keeps everything for one profile under a single home directory,
selected by the CODEX_HOME environment variable (default `~/.codex`): its
`auth.json`, `config.toml`, sessions, logs, and plugins. Pointing CODEX_HOME at
a different directory per invocation is how one machine runs several OpenAI
accounts side by side (e.g. `~/.codex`, `~/.codex2`, `~/.codex3`).

That isolation is real but shallow: it only covers where files are READ FROM,
not what they SAY. A home seeded by copying another home's `config.toml` keeps
every absolute path baked into that file, so the new profile reads its config
from its own directory and is then sent straight back into the old one. The
paths that matter are written by the ChatGPT desktop app, not by the CLI:

  * `notify`                       — a helper binary under the base home
  * `[marketplaces.*] source`      — `<base-home>/.tmp/bundled-marketplaces/...`
  * `[mcp_servers.*.env]`          — trusted-code paths, service paths, and
                                     `CODEX_HOME` itself, which re-enters the
                                     base home from inside the MCP subprocess

This script scans a home directory for Codex profile dirs and reports three
things: whether each profile has its own distinct credentials, whether any
profile's `config.toml` points into ANOTHER profile's home, and whether two
profiles share a content directory (`sessions/`, `plugins/`, ...) that one of
them can delete out from under the other.

Read-only; stdlib only. It never prints a credential: `auth.json` is read for
`auth_mode` and a truncated `account_id` fingerprint only — never walked — and
a config value is printed only when it matched another profile's home path.

Usage:
    inspect_codex_profiles.py [profile_dir ...]

With no arguments, auto-detects profile dirs directly under the home
directory: anything matching `.codex*` that contains a `config.toml` or an
`auth.json`. Pass explicit directories to check a specific set instead (e.g.
against a fixture HOME in a test) — relative paths are normalized to absolute
(lexically, without resolving symlinks) before any comparison, since the paths
baked into a config are themselves absolute and unresolved.

Exit 0 = every checked profile has distinct credentials, no cross-profile
config pins, and no shared content store (PASS).
Exit 1 = a warning was found (WARN): duplicate credentials, a config pinned
into another profile's home, a shared content store, or a profile whose
config.toml is missing/unparseable.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tomllib
from pathlib import Path

# Directories under a Codex home that hold per-profile state. Sharing any of
# them (usually a symlink, to dodge the disk cost of a second plugins/ tree)
# means one profile's writes, pruning, or `codex delete` reach the other's data.
CONTENT_STORES = ["sessions", "archived_sessions", "plugins", "cache", "computer-use"]

# How much of a matched config value to show. Matched values are file paths, but
# they are still config content, so they are truncated rather than dumped whole.
VALUE_DISPLAY_LEN = 120

# An account_id is an identifier, not a credential, but the full UUID is more
# than a health check needs: a short prefix is enough to tell two homes apart.
ACCOUNT_FINGERPRINT_LEN = 8


def abspath_arg(s: str) -> Path:
    """argparse type: lexically absolutize a CLI-supplied profile dir.

    Uses os.path.abspath, NOT realpath/resolve — it must NOT follow symlinks,
    only anchor a relative path at the cwd, so downstream matching lines up with
    the absolute, unresolved paths a config.toml actually contains.
    """
    return Path(os.path.abspath(s))


def looks_like_profile(p: Path) -> bool:
    """A dir counts as a Codex home if it has a config.toml or an auth.json."""
    return p.is_dir() and ((p / "config.toml").exists() or (p / "auth.json").exists())


def discover_profiles(home: Path) -> list[Path]:
    if not home.is_dir():
        return []
    return sorted(p for p in home.glob(".codex*") if looks_like_profile(p))


def kind(p: Path) -> str:
    if os.path.islink(p):
        return "symlink"
    if p.is_dir():
        return "real-dir"
    if p.exists():
        return "real-file"
    return "absent"


def path_occurs_in(needle_dir: str, value: str) -> bool:
    """True if `value` references the directory `needle_dir` as a path.

    A plain `needle_dir in value` substring test is WRONG here, and wrong in the
    direction that matters: `/home/u/.codex` is a substring of
    `/home/u/.codex2/sessions`, so every sibling profile would be reported as a
    leak into the base one. A plain `value.startswith(needle_dir)` is also
    wrong — real configs bury the path mid-string, inside a `:`-joined search
    path or a JSON blob (`{"browser":"<home>/plugins/..."}`).

    So: find every occurrence, and accept one only where the following character
    cannot extend it into a DIFFERENT directory name. `/` and `:` and quotes end
    a path; an alphanumeric, `.`, `-`, or `_` continues the basename, which is
    what distinguishes `.codex` from `.codex2`, `.codex.bak`, and `.codex-old`.
    """
    start = 0
    while (i := value.find(needle_dir, start)) != -1:
        after = value[i + len(needle_dir):]
        if not after or after[0] not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-":
            return True
        start = i + 1
    return False


def walk_values(o, prefix: str = ""):
    """Yield (dotted_key_path, string_value) for every string VALUE in the TOML.

    Table KEYS are deliberately not yielded. A Codex config's biggest table by
    far is `[projects."<abs path>"]`, whose keys are the directories the user has
    trusted — including, legitimately, another profile's home. Those are trust
    records, not pointers the runtime follows, so matching on them would bury a
    real finding under noise.
    """
    if isinstance(o, dict):
        for k, v in o.items():
            yield from walk_values(v, f"{prefix}.{k}" if prefix else str(k))
    elif isinstance(o, list):
        for n, v in enumerate(o):
            yield from walk_values(v, f"{prefix}[{n}]")
    elif isinstance(o, str):
        yield prefix, o


def read_account(prof: Path) -> tuple[str, str | None]:
    """Return (status, account fingerprint) for a profile's auth.json.

    Reads two named fields and nothing else — the same file holds an API key and
    OAuth tokens, which must never reach stdout.
    """
    try:
        auth = json.loads((prof / "auth.json").read_text())
    except FileNotFoundError:
        return "not-logged-in", None
    except (OSError, json.JSONDecodeError):
        return "unreadable", None
    if not isinstance(auth, dict):
        return "unreadable", None
    mode = auth.get("auth_mode") or "unknown"
    tokens = auth.get("tokens")
    account = tokens.get("account_id") if isinstance(tokens, dict) else None
    if not isinstance(account, str) or not account:
        return mode, None
    return mode, account[:ACCOUNT_FINGERPRINT_LEN]


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="inspect_codex_profiles.py",
        description="Read-only health check for a Codex CLI multi-profile (CODEX_HOME) setup.",
        epilog="Exit 0 = distinct credentials, no cross-profile pins, no shared store (PASS). Exit 1 = WARN.",
    )
    parser.add_argument(
        "profiles",
        nargs="*",
        type=abspath_arg,
        help="explicit profile directories to check (default: auto-detect .codex* dirs under $HOME)",
    )
    args = parser.parse_args(argv)

    home = Path.home()
    if args.profiles:
        profile_paths = list(args.profiles)
        print("Codex multi-profile health check (explicit profile dirs)")
    else:
        profile_paths = discover_profiles(home)
        print(f"Codex multi-profile health check (home={home})")
    if not profile_paths:
        print("No Codex profile directories found.")
        print("Pass explicit profile directories as arguments to check a specific set.")
        return 0

    # Keyed on the full Path, never prof.name — two explicit profile dirs can share
    # a basename (.../a/.codex and .../b/.codex), and a basename-keyed dict would
    # collapse them into one entry, hiding a real duplicate/share between them.
    # `label()` is DISPLAY-only: the basename when unique, else the full path.
    name_counts: dict[str, int] = {}
    for prof in profile_paths:
        name_counts[prof.name] = name_counts.get(prof.name, 0) + 1

    def label(prof: Path) -> str:
        return prof.name if name_counts[prof.name] == 1 else str(prof)

    width = max(15, max(len(label(p)) for p in profile_paths) + 1)

    print(f"Profiles: {', '.join(label(p) for p in profile_paths)}\n")

    warns: list[str] = []

    # 1. Credentials. Two homes holding the same account defeat the whole point of
    #    the split — the accounts' rate limits are one pool — and if they reached
    #    that state by sharing one auth.json inode, `codex logout` in either logs
    #    both out. Inode identity is checked separately from account identity so
    #    the report distinguishes "same file" from "same account, two files".
    print("== credentials (auth.json) ==")
    idents: dict[Path, tuple[int, int]] = {}
    accounts: dict[Path, str] = {}
    for prof in profile_paths:
        ap = prof / "auth.json"
        mode, account = read_account(prof)
        try:
            st = os.stat(ap)
            idents[prof] = (st.st_dev, st.st_ino)
            inode = f"inode={st.st_ino}"
        except OSError:
            inode = "no auth.json"
        if account:
            accounts[prof] = account
        shown = f"account={account}…" if account else "account=-"
        print(f"  {label(prof):<{width}} {mode:<14} {shown:<20} {inode}")

    by_ident: dict[tuple[int, int], list[Path]] = {}
    for prof, ident in idents.items():
        by_ident.setdefault(ident, []).append(prof)
    for profs in (g for g in by_ident.values() if len(g) > 1):
        names = ", ".join(label(p) for p in profs)
        warns.append(f"profiles {names} share ONE auth.json file — a logout in either logs out both")
        print(f"  <-- WARN: {names} share one auth.json (logout in either logs out both)")

    by_account: dict[str, list[Path]] = {}
    for prof, account in accounts.items():
        by_account.setdefault(account, []).append(prof)
    for account, profs in ((a, g) for a, g in by_account.items() if len(g) > 1):
        names = ", ".join(label(p) for p in profs)
        warns.append(f"profiles {names} are logged into the SAME account ({account}…) — one usage pool")
        print(f"  <-- WARN: {names} are logged into the same account (one usage pool, not two)")

    # 2. Cross-profile config pins — the failure a copied config.toml introduces.
    #    Matching is boundary-aware, never a bare substring: see path_occurs_in.
    print("\n== cross-profile config.toml pins ==")
    for prof in profile_paths:
        cp = prof / "config.toml"
        try:
            with cp.open("rb") as fh:
                cfg = tomllib.load(fh)
        except FileNotFoundError:
            print(f"  {label(prof):<{width}} no config.toml, skipped")
            continue
        except (OSError, tomllib.TOMLDecodeError) as exc:
            warns.append(f"{label(prof)}/config.toml is unreadable or invalid TOML: {type(exc).__name__}")
            print(f"  {label(prof):<{width}} UNREADABLE ({type(exc).__name__})")
            continue
        pins = [
            (label(other), key, value)
            for key, value in walk_values(cfg)
            for other in profile_paths
            if other != prof and path_occurs_in(str(other), value)
        ]
        if pins:
            keys = ", ".join(sorted({f"{k} -> {o}" for o, k, _ in pins}))
            warns.append(f"{label(prof)}/config.toml points into another profile's home: {keys}")
            print(f"  {label(prof):<{width}} {len(pins)} PIN(S) into another profile:")
            for other, key, value in pins:
                print(f"      {key} -> {other}: {value[:VALUE_DISPLAY_LEN]}")
        else:
            print(f"  {label(prof):<{width}} clean")

    # 3. Content stores. realpath (not stat inode) so a store that is itself a
    #    symlink resolves to the same target as a shared real dir reached
    #    another way.
    print("\n== content store identity ==")
    for store in CONTENT_STORES:
        store_targets: dict[Path, str] = {}
        for prof in profile_paths:
            sp = prof / store
            if not sp.exists():
                continue
            store_targets[prof] = os.path.realpath(sp)
        status = ", ".join(f"{label(p)}={kind(p / store)}" for p in profile_paths)
        print(f"  {store:18} {status}")

        by_target: dict[str, list[Path]] = {}
        for prof, target in store_targets.items():
            by_target.setdefault(target, []).append(prof)
        for profs in (g for g in by_target.values() if len(g) > 1):
            names = ", ".join(label(p) for p in profs)
            warns.append(f"profiles {names} share their `{store}` directory — each can overwrite the other's data")
            print(f"    <-- WARN: {names} share `{store}`")

    print()
    if warns:
        print(f"WARN ({len(warns)}):")
        for w in warns:
            print(f"  - {w}")
        return 1
    print("PASS — every checked profile has its own credentials, config, and content stores.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
