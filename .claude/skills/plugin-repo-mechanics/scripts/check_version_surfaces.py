#!/usr/bin/env python3
"""Assert that every plugin's version says the same thing on every surface it is written on.

A plugin's version is written in five places that nothing keeps in step (see
`references/version-and-surface-sync.md`):

  1. plugins/<name>/.claude-plugin/plugin.json  -> "version"
  2. .claude-plugin/marketplace.json            -> that plugin's "version"
  3. README.md table row                        -> the version cell
  4. README.md table row                        -> the anchor it links to
  5. README.md section heading                  -> `## `<name>` -- vX.Y.Z`

plus an entry for that version in the plugin's authoritative CHANGELOG -- the root one, unless the
plugin keeps its own (literary-translator does; the root file is frozen at its 1.1.0 entry).

Two failure modes have really shipped here, both silent. A release bumps some surfaces and not
others. And a merge or rebase resolves the shared README / marketplace hunk -- one hunk holds
EVERY plugin's row -- by keeping "ours", which reverts a SIBLING plugin's row while that plugin's
own heading merges cleanly.

Scope, stated because it bounds what a clean run proves: this reads the WORKING TREE and compares
the surfaces against EACH OTHER. It has no baseline and never consults `main`, so it is only worth
anything run BEFORE the commit that would publish the mismatch. It says nothing about whether a
version is the right one to release, and nothing about the section's body prose, which the doc
calls surface #4's hidden fifth layer and no parser can judge.

Exit: 0 every plugin agrees; 1 at least one disagreement (each named); 2 the sweep itself is
unsound -- a file it needs is missing or unreadable, or so few plugins were found that a clean
result would be vacuous.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# A sweep that matches nothing prints exactly what a passing sweep prints, so refuse an
# implausible population instead of reporting it clean. The repo has had seven plugins since
# 2026-07; two is a floor no real state of this repo goes under, not an expected count.
MIN_PLAUSIBLE_PLUGINS = 2

ROW_RE = re.compile(r"^\|\s*\[`([a-z0-9][a-z0-9-]*)`\]\(#([^)]+)\)\s*\|\s*([^|]+?)\s*\|", re.M)
HEADING_RE = re.compile(r"^##\s+`([a-z0-9][a-z0-9-]*)`\s+—\s+v(\S+)\s*$", re.M)
VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")


def github_slug(heading_text: str) -> str:
    """Slugify a heading the way GitHub does, for the subset of characters these headings use.

    `## `enduser-handbook` -- v1.0.3` -> `enduser-handbook--v103`: backticks, the em dash and the
    dots are dropped, each remaining space becomes a hyphen -- which is where the DOUBLE hyphen
    before the `v` comes from, and why bumping a version means editing the anchor's digits too.
    """
    kept = [c for c in heading_text.lower() if c.isalnum() or c in " -_"]
    return "".join("-" if c == " " else c for c in kept)


def changelog_source(repo: Path, name: str) -> tuple[Path, re.Pattern[str]]:
    """The file that owns this plugin's changelog, and the shape of an entry in it."""
    own = repo / "plugins" / name / "CHANGELOG.md"
    if own.exists():
        return own, re.compile(r"^##+\s*\[?v?([0-9]+\.[0-9]+\.[0-9]+)", re.M)
    return repo / "CHANGELOG.md", re.compile(
        r"^##+\s*\[" + re.escape(name) + r"\s+([0-9]+\.[0-9]+\.[0-9]+)\]", re.M
    )


def collect(repo: Path) -> tuple[dict[str, dict[str, object]], list[str]]:
    unsound: list[str] = []

    def read(rel: str) -> str | None:
        # One path, not two: "absent" and "there but unreadable" are the same answer to the only
        # question this sweep asks -- it could not read a file it needs, so it must not report the
        # tree clean. Catching the read is also what covers a required file that is not UTF-8.
        try:
            return (repo / rel).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            unsound.append(f"cannot read {rel}: {exc}")
            return None

    marketplace_raw = read(".claude-plugin/marketplace.json")
    readme = read("README.md")
    if marketplace_raw is None or readme is None:
        return {}, unsound

    try:
        marketplace = {p["name"]: p.get("version") for p in json.loads(marketplace_raw)["plugins"]}
    except (ValueError, KeyError, TypeError) as exc:
        unsound.append(f"marketplace.json is not readable as a plugin list: {exc}")
        return {}, unsound

    rows = {m.group(1): (m.group(2), m.group(3)) for m in ROW_RE.finditer(readme)}
    headings = {m.group(1): (m.group(2), m.group(0).split("##", 1)[1].strip()) for m in HEADING_RE.finditer(readme)}
    try:
        on_disk = {p.name for p in (repo / "plugins").iterdir() if (p / ".claude-plugin" / "plugin.json").is_file()}
    except OSError as exc:
        unsound.append(f"cannot list plugins/: {exc}")
        return {}, unsound

    # The UNION, not the marketplace list: a plugin present on disk and absent from the manifest
    # (or the README) is exactly the omission worth catching, and enumerating one source alone
    # would let it pass as "not a plugin".
    names = sorted(set(marketplace) | set(rows) | set(headings) | on_disk)
    found: dict[str, dict[str, object]] = {}
    for name in names:
        rel_manifest = f"plugins/{name}/.claude-plugin/plugin.json"
        manifest_version = None
        manifest_raw = read(rel_manifest) if (repo / rel_manifest).exists() else None
        if manifest_raw is not None:
            try:
                manifest_version = json.loads(manifest_raw).get("version")
            except (ValueError, AttributeError) as exc:
                unsound.append(f"{rel_manifest} is not a JSON object: {exc}")
        anchor, row_version = rows.get(name, (None, None))
        heading_version, heading_text = headings.get(name, (None, None))
        found[name] = {
            "manifest": manifest_version,
            "marketplace": marketplace.get(name),
            "row": row_version,
            "anchor": anchor,
            "heading": heading_version,
            "heading_text": heading_text,
        }
    return found, unsound


def judge(repo: Path, name: str, surfaces: dict[str, object]) -> list[str]:
    problems: list[str] = []
    labels = ("manifest", "marketplace", "row", "heading")
    for label in labels:
        if surfaces[label] is None:
            problems.append(f"no version on the {label} surface (plugin absent from it, or its shape changed)")
    versions = {label: surfaces[label] for label in labels if surfaces[label] is not None}
    for label, value in versions.items():
        if not VERSION_RE.match(str(value)):
            problems.append(f"{label} carries {value!r}, which is not an X.Y.Z version")
    distinct = sorted({str(v) for v in versions.values()})
    if len(distinct) > 1:
        problems.append(
            "surfaces disagree: " + ", ".join(f"{label}={versions[label]}" for label in labels if label in versions)
        )
    if surfaces["heading_text"] is not None:
        expected = github_slug(str(surfaces["heading_text"]))
        if surfaces["anchor"] is None:
            problems.append(f"no README table row links to the section (its anchor would be #{expected})")
        elif surfaces["anchor"] != expected:
            problems.append(f"table row links to #{surfaces['anchor']}, but the heading slugifies to #{expected}")
    if len(distinct) == 1:
        version = distinct[0]
        path, entry_re = changelog_source(repo, name)
        if not path.is_file():
            problems.append(f"no changelog at {path.relative_to(repo)}")
        elif version not in entry_re.findall(path.read_text(encoding="utf-8")):
            problems.append(f"{path.relative_to(repo)} has no entry for {version}")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[4],
                        help="repository root (default: the checkout this script lives in)")
    args = parser.parse_args()
    repo = args.repo.resolve()

    found, unsound = collect(repo)
    if unsound:
        for line in unsound:
            print(f"UNSOUND: {line}", file=sys.stderr)
        return 2
    if len(found) < MIN_PLAUSIBLE_PLUGINS:
        print(f"UNSOUND: found {len(found)} plugin(s) under {repo} -- refusing to report that clean", file=sys.stderr)
        return 2

    width = max([len("plugin")] + [len(n) for n in found])
    print(f"{'plugin':{width}}  {'manifest':9} {'market':9} {'row':9} {'heading':9}  anchor")
    failures = 0
    for name, surfaces in found.items():
        problems = judge(repo, name, surfaces)
        failures += bool(problems)
        cells = "  ".join(f"{str(surfaces[k] or '-'):9}" for k in ("manifest", "marketplace", "row", "heading"))
        print(f"{name:{width}}  {cells} {surfaces['anchor'] or '-'}")
        for problem in problems:
            print(f"{'':{width}}  -> {problem}")

    print(f"\n{len(found)} plugin(s) checked on 5 version surfaces + changelog; {failures} disagreeing")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
