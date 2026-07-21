#!/usr/bin/env python3
"""Health-check a Claude Code multi-profile plugin topology.

Claude Code can run under several profile directories on one machine — a
base `~/.claude` plus any number of alternates (e.g. `~/.claude2`,
`~/.claude3`, or any other `~/.claude*` config dir) created by pointing
CLAUDE_CONFIG_DIR at a different directory per install.
Each profile is *supposed* to have its own independent `plugins/` store. If
two profiles end up sharing one (e.g. one profile's `plugins/` symlinked into
another's), `claude plugin` operations from either profile stomp on the same
registry file and cause "corrupted installLocation" churn.

This script scans a home directory for profile-looking dirs, reports whether
each has an independent plugins store, and flags any registry that points at
another profile's `plugins/` path — a leak that usually precedes (or causes)
a shared-store mixup. It also checks the CONTENT stores (`marketplaces/`,
`cache/`, `data/`, `.install-manifests/`) under each profile's `plugins/` dir:
a profile can have its own distinct registry file yet still share one of
these with another profile (e.g. only `cache/` and `data/` were symlinked to
a common target). That's still a real risk — the CLI's catalog-scoped startup
GC deletes `cache/<marketplace>/<plugin>/<version>` entries not referenced by
the CURRENT profile's install catalog, and `plugin uninstall` deletes
`data/<plugin>` — so a shared `cache/` or `data/` lets one profile's GC or
uninstall delete another profile's plugin content.

Read-only; stdlib only.

Usage:
    inspect_profiles.py [profile_dir ...]

With no arguments, auto-detects profile dirs directly under the home
directory: anything named `.claude` or matching `.claude*` that contains a
`settings.json` file or a `plugins` entry. Pass explicit directories to check
a specific set instead (e.g. against a fixture HOME in a test) — relative
paths are normalized to absolute (lexically, without resolving symlinks)
before any comparison, since a registry's `installLocation` values are
always absolute.

Exit 0 = every checked profile has an independent registry, independent
content stores, and no leaks (PASS).
Exit 1 = a warning was found (WARN): a shared registry, a shared content
store, a cross-profile pointer leak, or a profile whose
known_marketplaces.json is missing/unreadable.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REGISTRY = "plugins/known_marketplaces.json"
CONTENT_STORES = ["marketplaces", "cache", "data", ".install-manifests"]


def abspath_arg(s: str) -> Path:
    """argparse type: lexically absolutize a CLI-supplied profile dir.

    Uses os.path.abspath, NOT realpath/resolve — it must NOT follow symlinks,
    only anchor a relative path at the cwd, so downstream prefix matching lines
    up with the registry's own absolute (unresolved) installLocation strings.
    """
    return Path(os.path.abspath(s))


def looks_like_profile(p: Path) -> bool:
    """A dir counts as a Claude Code config profile if it has settings.json or a plugins/ entry."""
    return p.is_dir() and ((p / "settings.json").exists() or (p / "plugins").exists())


def discover_profiles(home: Path) -> list[Path]:
    if not home.is_dir():
        return []
    return sorted(p for p in home.glob(".claude*") if looks_like_profile(p))


def kind(p: Path) -> str:
    if os.path.islink(p):
        return "symlink"
    if p.is_dir():
        return "real-dir"
    if p.exists():
        return "real-file"
    return "absent"


def walk_strings(o):
    if isinstance(o, dict):
        for v in o.values():
            yield from walk_strings(v)
    elif isinstance(o, list):
        for v in o:
            yield from walk_strings(v)
    elif isinstance(o, str):
        yield o


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="inspect_profiles.py",
        description="Read-only health check for a Claude Code multi-profile plugin setup.",
        epilog="Exit 0 = independent stores, no leaks (PASS). Exit 1 = shared store and/or leak found (WARN).",
    )
    parser.add_argument(
        "profiles",
        nargs="*",
        type=abspath_arg,
        help="explicit profile directories to check (default: auto-detect .claude* dirs under $HOME)",
    )
    args = parser.parse_args(argv)

    home = Path.home()
    if args.profiles:
        profile_paths = list(args.profiles)
        print("Multi-profile plugin health check (explicit profile dirs)")
    else:
        profile_paths = discover_profiles(home)
        print(f"Multi-profile plugin health check (home={home})")
    if not profile_paths:
        print("No Claude Code profile directories found.")
        print("Pass explicit profile directories as arguments to check a specific set.")
        return 0

    # Internal comparison maps below key on the full Path (never prof.name) — two explicit
    # profile dirs can share a basename (e.g. .../one/.claude and .../two/.claude), and a
    # basename-keyed dict would silently collapse them into one entry, hiding a real shared
    # registry/store/leak between them. `label()` is DISPLAY-only: normally just the basename,
    # but falls back to the full path for any basename that isn't unique across profile_paths.
    name_counts: dict[str, int] = {}
    for prof in profile_paths:
        name_counts[prof.name] = name_counts.get(prof.name, 0) + 1

    def label(prof: Path) -> str:
        return prof.name if name_counts[prof.name] == 1 else str(prof)

    width = max(15, max(len(label(p)) for p in profile_paths) + 1)

    print(f"Profiles: {', '.join(label(p) for p in profile_paths)}\n")

    warns: list[str] = []

    # 1. plugins/ dir type per profile
    print("== plugins/ dir type ==")
    for prof in profile_paths:
        print(f"  {label(prof):<{width}} {kind(prof / 'plugins')}")

    # 2. registry inodes — same (device, inode) across profiles means one real file on disk
    print("\n== known_marketplaces.json identity ==")
    idents: dict[Path, tuple[int, int]] = {}
    for prof in profile_paths:
        rp = prof / REGISTRY
        try:
            st = os.stat(rp)
            idents[prof] = (st.st_dev, st.st_ino)
            print(f"  {label(prof):<{width}} inode={st.st_ino}")
        except OSError:
            warns.append(f"{label(prof)}/{REGISTRY} missing/unreadable")
            print(f"  {label(prof):<{width}} MISSING")

    by_ident: dict[tuple[int, int], list[Path]] = {}
    for prof, ident in idents.items():
        by_ident.setdefault(ident, []).append(prof)
    shared_groups = [profs for profs in by_ident.values() if len(profs) > 1]
    for profs in shared_groups:
        names = ", ".join(label(p) for p in profs)
        warns.append(f"shared registry (corrupted-installLocation churn risk): {names}")
        print(f"  <-- WARN: {names} share one registry (corrupted-installLocation churn risk)")

    # 3. content stores (marketplaces/, cache/, data/, .install-manifests/) — a profile can have its
    #    own distinct registry file yet still share one of these with another profile (e.g. only
    #    cache/ and data/ were symlinked to a common target, a partial de-share). That's still a real
    #    risk: catalog-scoped startup GC prunes cache/<marketplace>/<plugin>/<version> entries not
    #    referenced by the CURRENT profile's install catalog, and `plugin uninstall` deletes
    #    data/<plugin> — so a shared cache/ or data/ lets one profile's GC/uninstall delete another
    #    profile's plugin content. realpath (not stat inode) is used so a store that is itself a
    #    symlink resolves to the same target as a shared real dir reached another way.
    print("\n== content store identity (marketplaces/, cache/, data/, .install-manifests/) ==")
    for store in CONTENT_STORES:
        store_targets: dict[Path, str] = {}
        for prof in profile_paths:
            sp = prof / "plugins" / store
            if not sp.exists():
                continue
            store_targets[prof] = os.path.realpath(sp)
        status = ", ".join(f"{label(p)}={kind(p / 'plugins' / store)}" for p in profile_paths)
        print(f"  {store:18} {status}")

        by_target: dict[str, list[Path]] = {}
        for prof, target in store_targets.items():
            by_target.setdefault(target, []).append(prof)
        for profs in (g for g in by_target.values() if len(g) > 1):
            names = ", ".join(label(p) for p in profs)
            warns.append(
                f"profiles {names} share their `{store}` store — "
                "cross-profile GC/uninstall can delete each other's plugins"
            )
            print(f"    <-- WARN: {names} share `{store}` (cross-profile GC/uninstall risk)")

    # 4. cross-profile pointer leaks — EXACT prefix match against another profile's plugins/ dir,
    #    NEVER a substring check (".claude" is a substring of ".claude2", which would false-positive).
    print("\n== cross-profile pointer leaks ==")
    plugin_dir_prefixes = {prof: f"{prof / 'plugins'}/" for prof in profile_paths}
    for prof in profile_paths:
        rp = prof / REGISTRY
        try:
            reg = json.loads(rp.read_text())
        except (OSError, json.JSONDecodeError):
            print(f"  {label(prof):<{width}} unreadable, skipped")
            continue
        leaks = [
            (label(other), s)
            for s in walk_strings(reg)
            for other, prefix in plugin_dir_prefixes.items()
            if other != prof and s.startswith(prefix)
        ]
        if leaks:
            warns.append(f"{label(prof)} registry references another profile: {leaks[:3]}")
            print(f"  {label(prof):<{width}} LEAK -> {leaks[:3]}")
        else:
            print(f"  {label(prof):<{width}} clean")

    print()
    if warns:
        print(f"WARN ({len(warns)}):")
        for w in warns:
            print(f"  - {w}")
        return 1
    print("PASS — every checked profile has an independent plugin registry with no cross-profile leaks.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
