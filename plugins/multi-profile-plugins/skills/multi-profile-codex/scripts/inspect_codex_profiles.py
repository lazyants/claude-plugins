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

Read-only; stdlib only, and nothing it reads is ever echoed back. `auth.json` is
never walked: two named fields are read, the login mode is reported as one of a
fixed set of labels rather than as the file's own string, and the account id is
matched against an identifier shape and then truncated to a short fingerprint.
A `config.toml` value is never printed at all — a matched pin is reported as the
dotted key that holds it, because one MCP env value can carry both a path and a
token in the same string.

Usage:
    inspect_codex_profiles.py [profile_dir ...]

With no arguments, auto-detects profile dirs directly under the home
directory: anything matching `.codex*` that contains a `config.toml` or an
`auth.json`. Pass explicit directories to check a specific set instead (e.g.
against a fixture HOME in a test) — relative paths are normalized to absolute
(lexically, without resolving symlinks) before any comparison, since the paths
baked into a config are themselves absolute and unresolved.

Exit 0 = no cross-profile config pins, no shared content store, and no two
profiles on the same account (PASS). Where a login mode carries no account id
at all — an api-key login has none — ownership cannot be compared, and the PASS
line says so instead of claiming a distinctness this never established. That is
not a failure and not a gap: it is a limit of what the file can answer, and
warning about it forever would be a check nobody could satisfy.
Exit 1 = a warning was found (WARN): duplicate credentials, a config pinned
into another profile's home, or a shared content store — and also any profile
that could not be fully examined: a missing or unparseable `config.toml`, an
unreadable or malformed `auth.json`, or an explicitly-passed directory that is
not a Codex profile at all. That second group warns rather than skipping
quietly on purpose. What it means is that a profile was NOT examined, and a run
that did not examine a profile must not report it as clean; `not-logged-in` is
a real intended state for a freshly seeded home and is not in this group.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tomllib
from pathlib import Path

# Directories under a Codex home that hold per-profile state. Sharing any of
# them (usually a symlink, to dodge the disk cost of a second plugins/ tree)
# means one profile's writes, pruning, or `codex delete` reach the other's data.
CONTENT_STORES = ["sessions", "archived_sessions", "plugins", "cache", "computer-use"]

# A matched value is never printed, only the dotted key that holds it and the profile it
# points at. That is what a reader needs in order to fix the config, and it is the only form
# that is safe: a single MCP env value can hold BOTH a path and a token, so any rule that
# prints "the part that matched" is one unusual config away from printing a credential.

# Characters that delimit a path inside the three value shapes this script sees: a bare TOML
# string, a `:`-joined search path, and a JSON blob. Used on BOTH sides of a match, which is
# what separates a real reference from a same-suffix path: `/Volumes/backup/Users/me/.codex`
# is not a reference to `/Users/me/.codex`, and only the PRECEDING character says so.
VALUE_DELIMITERS = "\"'`:,;|= \t\r\n{}[]()<>"

# An account_id is an identifier, not a credential, but the full UUID is more
# than a health check needs: a short prefix is enough to tell two homes apart.
# It is a DISPLAY length only — comparisons use the full id, since two distinct
# accounts can share eight leading characters.
ACCOUNT_FINGERPRINT_LEN = 8

# The account id is printed, so it is matched against a shape before it is trusted to be
# an identifier at all rather than whatever a malformed auth.json happens to hold there.
ACCOUNT_ID_RE = re.compile(r"[0-9a-fA-F-]{8,64}")

# Login modes rendered verbatim. Anything else is reported as "unknown-mode": the value
# comes out of a credential file, and echoing an unrecognised one is how that file's
# contents would reach a terminal.
KNOWN_AUTH_MODES = ("chatgpt", "apikey", "api_key", "device")

# Modes that carry an account id. An api-key login legitimately has none, so demanding one
# would make a perfectly good profile warn on every run, forever -- a check nobody can
# satisfy, which is a worse outcome than the gap it was closing. Those profiles are reported
# as comparison-unavailable instead, and the final verdict says so rather than claiming a
# distinctness it could not establish.
ACCOUNT_BEARING_MODES = ("chatgpt",)

# Every profile must be marked for each of these before the run may report PASS. The
# marks are what makes "not examined" impossible to confuse with "examined and clean":
# two separate findings in review were a check that could not run, printing nothing and
# letting the final PASS line speak for a profile it never looked at.
CHECKS = ("credentials", "config", "stores")


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

    So: find every occurrence and require a path-component boundary on BOTH sides.

    After the match, `/` counts (a subpath is still a reference) and so does any
    delimiter or the end of the string; anything else continues the basename into a
    DIFFERENT directory, which is what separates `.codex` from `.codex2`,
    `.codex.bak`, `.codex-old`, and `.codex+work`.

    Before the match, only the start of the string or a delimiter counts. Checking
    the following character alone is not enough: `/Volumes/backup/Users/me/.codex`
    is a same-suffix path under a backup mount, not a reference to `/Users/me/.codex`,
    and the preceding character is the only thing that says so.
    """
    start = 0
    while (i := value.find(needle_dir, start)) != -1:
        before_ok = i == 0 or value[i - 1] in VALUE_DELIMITERS
        after = value[i + len(needle_dir):]
        after_ok = not after or after[0] == "/" or after[0] in VALUE_DELIMITERS
        if before_ok and after_ok:
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
    """Return (status, full account id) for a profile's auth.json.

    Reads two named fields and nothing else — the same file holds an API key and
    OAuth tokens, which must never reach stdout.

    NOTHING here is a passthrough of file content. The status is one of AUTH_STATUSES,
    chosen by matching the file's value, never by echoing it: a field this function
    forwarded verbatim would put whatever a malformed auth.json contains onto the
    terminal, which is the one outcome the whole script is written to avoid. The
    account id is returned in FULL for comparison and truncated only where it is
    rendered — comparing fingerprints would report two distinct accounts sharing
    eight leading characters as one.
    """
    try:
        auth = json.loads((prof / "auth.json").read_text())
    except FileNotFoundError:
        return "not-logged-in", None
    except (OSError, json.JSONDecodeError):
        return "unreadable", None
    if not isinstance(auth, dict):
        return "unreadable", None
    raw_mode = auth.get("auth_mode")
    mode = raw_mode if raw_mode in KNOWN_AUTH_MODES else "unknown-mode"
    tokens = auth.get("tokens")
    account = tokens.get("account_id") if isinstance(tokens, dict) else None
    if not isinstance(account, str) or not ACCOUNT_ID_RE.fullmatch(account):
        return mode, None
    return mode, account


def fingerprint(account: str) -> str:
    """Render an account id for display: a short prefix, never the whole identifier."""
    return account[:ACCOUNT_FINGERPRINT_LEN]


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

    # Every internal map below keys on the full Path, never on prof.name: two explicit
    # profile dirs can share a basename (.../a/.codex and .../b/.codex), and a
    # basename-keyed dict would collapse them into one entry, hiding a real duplicate.
    #
    # DISPLAY is settled by where the paths came from, which removes the ambiguity instead
    # of detecting it: discovered profiles are all siblings under one home, so a basename
    # identifies them; explicitly passed ones can come from anywhere, so they are shown in
    # full. An earlier version counted basenames and fell back to the full path only on a
    # collision, which is machinery for a case this rule cannot produce.
    def label(prof: Path) -> str:
        return prof.name if not args.profiles else str(prof)

    width = max(15, max(len(label(p)) for p in profile_paths) + 1)

    print(f"Profiles: {', '.join(label(p) for p in profile_paths)}\n")

    warns: list[str] = []

    # Checks that could NOT run, per profile, with the reason. Twice in review a check that
    # could not run said nothing and let the final PASS line speak for a profile it had never
    # looked at — once for a missing config.toml, once for a malformed auth.json. Patching
    # each site as it was found would have left the next one to be found the same way, so
    # "could not examine" is recorded HERE and nowhere else, and one loop at the end turns
    # every gap into a warning. A check added later gets the invariant by construction: it
    # records a gap, or it completes. There is deliberately no second path to the same
    # conclusion — a guard that some other line already covers is one nothing can be shown
    # to need.
    gaps: dict[Path, dict[str, str]] = {prof: {} for prof in profile_paths}

    # 0. An explicitly-passed directory is taken on trust everywhere below: unlike a discovered
    #    one it never went through looks_like_profile. A typo, or a home that has since been
    #    moved, therefore reaches every check as a profile with nothing in it, each check finds
    #    nothing to report, and the run ends on the unconditional PASS line -- a clean verdict
    #    over a directory that was never examined. Auto-detected profiles cannot reach this.
    if args.profiles:
        print("== explicit profile dirs ==")
        for prof in profile_paths:
            if looks_like_profile(prof):
                print(f"  {label(prof):<{width}} ok")
            else:
                # Says "no readable", not "no": looks_like_profile is a boolean probe, so a
                # directory whose contents cannot be read is indistinguishable here from one
                # that is genuinely empty, and the message must not claim the difference.
                reason = "does not exist" if not prof.exists() else "no readable config.toml or auth.json"
                for check_name in CHECKS:
                    gaps[prof][check_name] = f"not a Codex profile directory ({reason})"
                print(f"  {label(prof):<{width}} NOT A PROFILE ({reason})")
        print()

    # 1. Credentials. Two homes holding the same account defeat the whole point of
    #    the split — the accounts' rate limits are one pool — and if they reached
    #    that state by sharing one auth.json inode, `codex logout` in either logs
    #    both out. Inode identity is checked separately from account identity so
    #    the report distinguishes "same file" from "same account, two files".
    print("== credentials (auth.json) ==")
    idents: dict[Path, tuple[int, int]] = {}
    accounts: dict[Path, str] = {}
    uncomparable: list[Path] = []
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
        # "not-logged-in" is a real, intended state for a freshly seeded home and stays
        # clean. "unreadable" is not: the credentials were NOT examined, so this profile
        # cannot be part of a clean verdict about credentials.
        if mode == "unreadable":
            gaps[prof]["credentials"] = "auth.json is unreadable or malformed"
        elif mode in ACCOUNT_BEARING_MODES and account is None:
            # The file is there and parses, and this mode is supposed to carry an id, but
            # nothing comparable came out: absent, or failing the identifier shape. Either
            # way the profile drops out of the same-account comparison below -- the check
            # most worth having, since two homes on one account is what the split exists to
            # avoid. The shape gate is itself one way to land here.
            gaps[prof]["credentials"] = "auth.json has no comparable account id"
        elif mode != "not-logged-in" and account is None:
            # A mode that carries no account id by design. Not a gap -- nothing failed --
            # but ownership cannot be compared, and the verdict must not imply otherwise.
            uncomparable.append(prof)
        shown = f"account={fingerprint(account)}…" if account else "account=-"
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
        warns.append(
            f"profiles {names} are logged into the SAME account ({fingerprint(account)}…) — one usage pool"
        )
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
            # Not merely skipped: this profile's pins were NOT checked, so the run is not a
            # clean bill of health for it, and reporting it as anything but a warning is the
            # false-PASS the docstring's exit contract promises it is not.
            gaps[prof]["config"] = "no config.toml"
            print(f"  {label(prof):<{width}} NO config.toml (pins not checked)")
            continue
        except (OSError, tomllib.TOMLDecodeError) as exc:
            gaps[prof]["config"] = f"config.toml is unreadable or invalid TOML ({type(exc).__name__})"
            print(f"  {label(prof):<{width}} UNREADABLE ({type(exc).__name__})")
            continue
        # The VALUE is deliberately dropped here and never printed. One MCP env value can hold
        # a path and a token in the same string, so printing "the value that matched" leaks a
        # credential on exactly the config a user would most want to run this against. The
        # dotted key names the line to edit, which is all a reader needs.
        pins = sorted({
            (key, label(other))
            for key, value in walk_values(cfg)
            for other in profile_paths
            if other != prof and path_occurs_in(str(other), value)
        })
        if pins:
            keys = ", ".join(f"{k} -> {o}" for k, o in pins)
            warns.append(f"{label(prof)}/config.toml points into another profile's home: {keys}")
            print(f"  {label(prof):<{width}} {len(pins)} PIN(S) into another profile:")
            for key, other in pins:
                print(f"      {key} -> {other}")
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
            try:
                # Follows symlinks deliberately, matching the realpath comparison on the next
                # line. An lstat here would admit a BROKEN symlink, whose realpath then names
                # a directory that does not exist -- and two broken links to one missing
                # target would be reported as sharing a store that nothing has.
                os.stat(sp)
            except FileNotFoundError:
                continue  # genuinely absent, which is normal and not a gap
            except OSError as exc:
                # Path.exists() answers False for a permission error too, so the store would
                # read as absent and this profile would reach PASS with the store unexamined.
                gaps[prof]["stores"] = f"`{store}` is inaccessible ({type(exc).__name__})"
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

    # The single place "could not examine" becomes "not clean". A profile some check could
    # not look at must never be covered by the PASS line, whatever the reason was.
    for prof in profile_paths:
        for check_name in CHECKS:
            reason = gaps[prof].get(check_name)
            if reason:
                warns.append(f"{label(prof)}: {check_name} NOT checked -- {reason}")

    print()
    if warns:
        print(f"WARN ({len(warns)}):")
        for w in warns:
            print(f"  - {w}")
        return 1
    if uncomparable:
        names = ", ".join(label(p) for p in uncomparable)
        print(
            "PASS — no cross-profile pins and no shared content stores. Account ownership "
            f"could not be compared for {names} (this login mode carries no account id), so "
            "this verdict does not claim those profiles are on different accounts."
        )
        return 0
    print("PASS — every checked profile has its own credentials, config, and content stores.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
