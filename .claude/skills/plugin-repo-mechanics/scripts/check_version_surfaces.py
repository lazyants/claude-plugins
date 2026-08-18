#!/usr/bin/env python3
"""Assert that every plugin's version says the same thing on every surface it is written on.

A plugin's version is written in five places that nothing keeps in step (see
`references/version-and-surface-sync.md`, whose surface numbering this list deliberately does not
reuse):

  1. plugins/<name>/.claude-plugin/plugin.json  -> "version"
  2. .claude-plugin/marketplace.json            -> that plugin's "version"
  3. README.md table row                        -> the version cell
  4. README.md table row                        -> the anchor it links to
  5. README.md section heading                  -> "## `<name>` — vX.Y.Z", with an EM DASH

plus an entry for that version in the plugin's authoritative CHANGELOG -- the root one, unless the
plugin keeps its own (literary-translator does; the root file is frozen at its 1.1.0 entry).

Two failure modes have really shipped here, both silent. A release bumps some surfaces and not
others. And a merge or rebase resolves the shared README / marketplace hunk -- one hunk holds
EVERY plugin's row -- by keeping "ours", which reverts a SIBLING plugin's row while that plugin's
own heading merges cleanly. The other resolution of that same hunk, keeping BOTH sides, leaves a
stale row and a stale section beside the current ones; that is why a surface appearing twice for
one plugin is a finding rather than a last-one-wins.

What a clean run does and does not prove:

  - It compares the surfaces against EACH OTHER. There is no baseline, so it cannot tell you which
    surface your change forgot from which was already wrong before you started -- for that, the
    doc's next bullet has you grep each surface on `origin/main`. (`--repo` does accept a checkout
    of `main`, so it can be pointed at shipped state; it just cannot compare the two.)
  - The changelog is only checked once the five surfaces agree, because until they do there is no
    single version to look for. A first run over a mismatched tree therefore does not enumerate
    all the remaining work; re-run after fixing what it named.
  - It says nothing about whether a version is the right one to release, nothing about
    `metadata.version`, and nothing about the README section's BODY PROSE, which the doc calls a
    hidden layer of the README surface and which no parser can judge.

Exit: 0 every plugin agrees; 1 at least one disagreement (each named); 2 the sweep itself is
unsound -- a file it MUST read to run at all is missing or unreadable, or so few plugins were
found that a clean result would be vacuous. A plugin's own `plugin.json` or changelog going
missing is deliberately NOT unsoundness: those are findings about that plugin, exit 1.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

# A sweep over an implausibly small population is the failure that reads as an all-clear: the only
# thing separating it from a passing run is the count on the summary line, which is exactly the
# figure a reader skims past. The repo has had seven plugins since 2026-07; two is a floor no real
# state of it goes under, not an expected count.
MIN_PLAUSIBLE_PLUGINS = 2

# The version cell is captured whole and stripped in code: letting `\s*` and a lazy `[^|]+?`
# negotiate the same run of spaces is ambiguous, and quadratic-to-cubic on a pathological line.
ROW_RE = re.compile(r"^\|\s*\[`([a-z0-9][a-z0-9-]*)`\]\(#([^)]+)\)\s*\|([^|]*)\|", re.M)
# What a plugin may be called. The README regexes already enforce this shape; marketplace.json
# and the filesystem do not, and a name reaches the filesystem as a path component -- so a name
# of "../../elsewhere" in a manifest, or a symlinked plugins/<x>, would otherwise be read.
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
HEADING_RE = re.compile(r"^##\s+`([a-z0-9][a-z0-9-]*)`\s+—\s+v(\S+)\s*$", re.M)
VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
SURFACES = ("manifest", "marketplace", "row", "heading")
COLUMN = "  "
CELL = max(len(s) for s in SURFACES)


def read_text(repo: Path, rel: str, unsound: list[str]) -> str | None:
    """The one reader for every file this sweep needs.

    "Absent" and "there but unreadable" are the same answer to the only question asked of a file
    the sweep cannot run without, so they share a path -- which is also what covers a required
    file that is not UTF-8. Callers that treat absence as a FINDING check existence themselves
    before calling.
    """
    path = repo / rel
    if not path.resolve().is_relative_to(repo):
        # A symlinked plugins/<x> resolves outside the tree being checked. Refuse rather than
        # report on a file from somewhere else -- a sweep is about ONE checkout.
        unsound.append(f"{rel} resolves outside {repo}")
        return None
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        unsound.append(f"cannot read {rel}: {exc}")
        return None


def display(value: object) -> str:
    """Every printed cell comes from a file, and a version cell or an anchor has no charset gate.

    A raw control byte reaching a terminal can redraw the report -- including a forged summary
    line -- above the real findings that follow it. Anything not printable is shown escaped.
    """
    text = str(value)
    return text if text.isprintable() else repr(text)


def github_slug(heading_text: str) -> str:
    """Slugify a heading the way GitHub does, for the subset of characters these headings use.

    "## `enduser-handbook` — v1.0.3" (em dash) becomes "enduser-handbook--v103": the backticks,
    the em dash and the dots are dropped, and each remaining space becomes its own hyphen -- which
    is where the DOUBLE hyphen before the `v` comes from, and why bumping a version means editing
    the anchor's digits too.
    """
    kept = [c for c in heading_text.lower() if c.isalnum() or c in " -_"]
    return "".join("-" if c == " " else c for c in kept)


def changelog_source(repo: Path, name: str) -> tuple[str, re.Pattern[str]]:
    """The file that owns this plugin's changelog, and the shape of an entry in it."""
    own = f"plugins/{name}/CHANGELOG.md"
    if (repo / own).is_file():
        return own, re.compile(r"^##+\s*\[?v?([0-9]+\.[0-9]+\.[0-9]+)", re.M)
    return "CHANGELOG.md", re.compile(
        r"^##+\s*\[" + re.escape(name) + r"\s+([0-9]+\.[0-9]+\.[0-9]+)\]", re.M
    )


def collect(repo: Path, unsound: list[str]) -> dict[str, dict[str, object]]:
    marketplace_raw = read_text(repo, ".claude-plugin/marketplace.json", unsound)
    readme = read_text(repo, "README.md", unsound)
    if marketplace_raw is None or readme is None:
        return {}

    try:
        entries = [(p["name"], p.get("version")) for p in json.loads(marketplace_raw)["plugins"]]
    except (ValueError, KeyError, TypeError) as exc:
        unsound.append(f"marketplace.json is not readable as a plugin list: {exc}")
        return {}

    rows = {m.group(1): (m.group(2), m.group(3).strip()) for m in ROW_RE.finditer(readme)}
    headings = {m.group(1): (m.group(2), m.group(0)[2:].strip()) for m in HEADING_RE.finditer(readme)}
    marketplace = dict(entries)
    # Counted, not just mapped: each of these three parses is last-one-wins, so a duplicate left
    # behind by a "keep both sides" conflict resolution would disappear into the map.
    seen = {
        "marketplace": Counter(name for name, _ in entries),
        "row": Counter(m.group(1) for m in ROW_RE.finditer(readme)),
        "heading": Counter(m.group(1) for m in HEADING_RE.finditer(readme)),
    }

    try:
        on_disk = {p.name for p in (repo / "plugins").iterdir() if (p / ".claude-plugin" / "plugin.json").is_file()}
    except OSError as exc:
        unsound.append(f"cannot list plugins/: {exc}")
        return {}

    # The UNION, not the marketplace list: a plugin present on disk and absent from the manifest
    # (or the README) is exactly the omission worth catching, and enumerating one source alone
    # would let it pass as "not a plugin".
    found: dict[str, dict[str, object]] = {}
    for name in sorted(set(marketplace) | set(rows) | set(headings) | on_disk):
        if not NAME_RE.match(name):
            unsound.append(f"{display(name)} is not a plugin name this sweep will build a path from")
            continue
        rel_manifest = f"plugins/{name}/.claude-plugin/plugin.json"
        manifest_version = None
        # Existence is checked HERE rather than inside the reader because an absent manifest is a
        # finding about this plugin (exit 1), not a reason to distrust the whole sweep (exit 2).
        if (repo / rel_manifest).exists():
            manifest_raw = read_text(repo, rel_manifest, unsound)
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
            "heading_text": heading_text,
            "heading": heading_version,
            "duplicates": {s: n for s, c in seen.items() if (n := c[name]) > 1},
        }
    return found


def judge(repo: Path, name: str, surfaces: dict[str, object], unsound: list[str]) -> list[str]:
    problems: list[str] = []
    for surface, count in sorted(surfaces["duplicates"].items()):  # type: ignore[union-attr]
        problems.append(
            f"the {surface} surface carries {count} entries for it -- the parse takes the last, "
            "so a stale one left beside the current one would not be seen"
        )
    for label in SURFACES:
        if surfaces[label] is None:
            problems.append(f"no version on the {label} surface (plugin absent from it, or its shape changed)")
    versions = {label: surfaces[label] for label in SURFACES if surfaces[label] is not None}
    for label, value in versions.items():
        if not VERSION_RE.match(str(value)):
            problems.append(f"{label} carries {value!r}, which is not an X.Y.Z version")
    distinct = {str(v) for v in versions.values()}
    if len(distinct) > 1:
        problems.append("surfaces disagree: " + ", ".join(f"{k}={v}" for k, v in versions.items()))
    if surfaces["heading_text"] is not None:
        expected = github_slug(str(surfaces["heading_text"]))
        if surfaces["anchor"] is None:
            problems.append(f"no README table row links to the section (its anchor would be #{expected})")
        elif surfaces["anchor"] != expected:
            problems.append(
                f"table row links to #{display(surfaces['anchor'])}, but the heading slugifies to #{expected}"
            )
    if len(distinct) == 1:
        version = distinct.pop()
        rel, entry_re = changelog_source(repo, name)
        if not (repo / rel).is_file():
            problems.append(f"no changelog at {rel}")
        else:
            text = read_text(repo, rel, unsound)
            if text is not None and version not in entry_re.findall(text):
                problems.append(f"{rel} has no entry for {version}")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[4],
                        help="repository root (default: the checkout this script lives in)")
    args = parser.parse_args()
    repo = args.repo.resolve()

    unsound: list[str] = []
    found = collect(repo, unsound)

    def give_up() -> int:
        for line in unsound:
            print(f"UNSOUND: {line}", file=sys.stderr)
        return 2

    if unsound:
        return give_up()
    if len(found) < MIN_PLAUSIBLE_PLUGINS:
        print(f"UNSOUND: found {len(found)} plugin(s) under {repo} -- refusing to report that clean", file=sys.stderr)
        return 2

    width = max([len("plugin")] + [len(n) for n in found])
    print(COLUMN.join([f"{'plugin':{width}}"] + [f"{s:{CELL}}" for s in SURFACES]) + COLUMN + "anchor")
    failures = 0
    for name, surfaces in found.items():
        problems = judge(repo, name, surfaces, unsound)
        failures += bool(problems)
        cells = COLUMN.join(f"{display(surfaces[s]) if surfaces[s] else '-':{CELL}}" for s in SURFACES)
        print(f"{name:{width}}{COLUMN}{cells}{COLUMN}{display(surfaces['anchor']) if surfaces['anchor'] else '-'}")
        for problem in problems:
            print(f"{'':{width}}{COLUMN}-> {problem}")

    print(f"\n{len(found)} plugin(s) checked on 5 version surfaces + changelog; {failures} disagreeing")
    if unsound:
        return give_up()
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
