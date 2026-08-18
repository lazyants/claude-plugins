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

# A CommonMark blank is a SPACE or a TAB. `str.strip()` and `str.isspace()` also accept U+00A0
# and the other Unicode separators, and every place this file trimmed with them was a way for
# a line to look like a heading, or a fence to look closed, when the renderer disagrees.
#
# ONE RULE, and every pattern in this file obeys it: nothing is ever matched against multi-line
# text. A pattern that describes a LINE goes through `line_matches()`; a pattern that describes
# a whole VALUE goes through `.fullmatch()`. Neither `re.MULTILINE` nor a `$` anchor appears
# anywhere, because both are how a newline gets in: `\s` matches one, every negated class like
# `[^|]` matches one, `$` matches BEFORE a trailing one, and `.match()` does not require the
# whole string. Three review rounds each found a different spelling of that one mistake -- in
# the heading pattern, then the row pattern, then the changelog pattern and the name gate -- so
# the rule is stated once here and there is no per-pattern judgement left to get wrong.
ROW_RE = re.compile(r"\|[ \t]*\[`([a-z0-9][a-z0-9-]*)`\][ \t]*\(#([^)]+)\)[ \t]*\|([^|]*)\|")
# The heading pattern describes the heading TEXT and is applied with `fullmatch` by
# `heading_matches`, so trailing text cannot ride along -- GitHub slugifies whatever else is on
# the line, and the row's anchor would then not resolve.
HEADING_RE = re.compile(r"`([a-z0-9][a-z0-9-]*)`[ \t]+—[ \t]+v(\S+)")
VERSION_RE = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")
# What a plugin may be called. The README patterns already enforce this shape and a directory
# name is a single path component by construction; marketplace.json is the one source that
# could otherwise put "../../elsewhere" where a path component belongs. read_text's
# containment check is the backstop that does not depend on which source a name came from.
NAME_RE = re.compile(r"[a-z0-9][a-z0-9-]*")
FENCE_RE = re.compile(r"`{3,}|~{3,}")
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


def atx_heading(line: str, level: int = 2) -> str | None:
    """The TEXT of a CommonMark ATX heading of exactly `level`, or None if this line is not one.

    Three consumers need this -- the README's plugin sections and both changelog layouts -- and
    every hand-rolled version of it accepted something Markdown does not render as a heading:
    `##[name 1.2.3]` (no space after the hashes is not a heading at all), and `### ...` (a real
    heading, but not the release-entry level). A changelog entry that never rendered is exactly the
    "entry was never written" case this check exists to catch, so it is parsed once, here.
    """
    stripped = line.lstrip(" ")
    if len(line) - len(stripped) > 3:  # four spaces is an indented code block, not a heading
        return None
    hashes = len(stripped) - len(stripped.lstrip("#"))
    if hashes != level:
        return None
    rest = stripped[hashes:]
    if rest and rest[:1] not in (" ", "\t"):
        # `str.isspace()` was wrong here: it admits U+00A0 and the other Unicode separators, and
        # CommonMark admits none of them -- GitHub renders `##\u00a0Title` as literal text.
        return None
    text = rest.strip(" \t")
    closing = text.rstrip("#")  # an optional closing sequence, which CommonMark drops
    if closing != text and (not closing or closing[-1:] in (" ", "\t")):
        text = closing.strip(" \t")
    return text


def rendered_lines(text: str) -> list[str]:
    """The file's lines with fenced code blocks dropped. This is the whole of the context handling.

    Why anything at all: a table row or a section heading shown as an EXAMPLE inside a fence is not
    a live surface, and counting one is a false green on the surface it fakes and a false duplicate
    on the surface it doubles. Code examples are the one construct these three files actually
    contain, so fences are worth the twenty lines.

    Why nothing MORE, stated as a contract rather than left as a bug: **this reads LINES, and
    comments are not one of the things it reads.** A surface inside an `<!-- -->` comment still
    counts as present, so if you comment a plugin's section out, remove or update its table row in
    the same edit. The same cut runs the other way too and is worth knowing before you meet it: a
    fence marker written INSIDE a comment does open a fence here, so a commented-out code example
    can hide surfaces that follow it. Both are the same missing knowledge, and the ordinary edit
    that produces either -- parking a section -- is one a person makes deliberately. A heading
    inside a blockquote, which GitHub does render, is likewise not seen.

    That boundary is a decision, not an oversight. An earlier version tracked HTML comments too and
    then spent four review rounds on how comments and fences nest -- a literal `<!--` in a fenced
    example, one in an info string, one in inline code -- each an incremental step toward
    reimplementing CommonMark, and each failing OPEN when it was wrong: a mis-parsed wrapper hides
    every surface after it, silently. Checking a version number does not justify carrying a Markdown
    parser, and a hand-rolled one is the worst of the three options.

    Fences are matched by character and length, so a ``` inside a ~~~ block does not end it, and a
    closing fence carries no info string. A backtick fence whose info string contains a backtick is
    not a fence at all (CommonMark forbids it) -- the one conformance rule kept, because getting it
    wrong drops live content.
    """
    kept: list[str] = []
    fence: tuple[str, int] | None = None
    for line in text.splitlines():
        stripped = line.lstrip(" ")
        marker = FENCE_RE.match(stripped) if len(line) - len(stripped) <= 3 else None
        info = stripped[len(marker.group(0)):] if marker else ""
        if fence is None:
            if marker and not (marker.group(0)[0] == "`" and "`" in info):
                fence = (marker.group(0)[0], len(marker.group(0)))
            else:
                kept.append(line)  # including ```a`b, which is content, not a fence opener
            continue
        if (
            marker
            and marker.group(0)[0] == fence[0]
            and len(marker.group(0)) >= fence[1]
            and not info.strip(" \t")
        ):
            fence = None
    return kept


def heading_matches(pattern: re.Pattern[str], text: str) -> list[re.Match[str]]:
    """Every match against a heading: the line is parsed as one first, the pattern reads its TEXT."""
    found = []
    for line in rendered_lines(text):
        heading = atx_heading(line)
        if heading is not None:
            match = pattern.fullmatch(heading)
            if match:
                found.append(match)
    return found


def line_matches(pattern: re.Pattern[str], text: str) -> list[re.Match[str]]:
    """Every match this file makes against a file: pattern anchored at the start of ONE line."""
    return [m for m in (pattern.match(line) for line in rendered_lines(text)) if m]


def emit(text: str, stream: object = None) -> None:
    """The one write in this file, and the reason there is only one.

    Every line printed is composed from repository text -- a version cell, an anchor, a path, a
    DIRECTORY name -- and none of those has a charset gate. A raw control byte reaching a terminal
    can redraw the report, and a plugin's table line prints BEFORE that plugin's findings, so
    crafted bytes could stand a forged clean summary over the real ones. Escaping per value was
    the first attempt and it leaked twice, at the sites nobody thought of as values; escaping at
    the single exit cannot leak, whatever a future caller composes.
    """
    print(
        "".join(c if c.isprintable() else c.encode("unicode_escape").decode("ascii") for c in text),
        file=stream if stream is not None else sys.stdout,
    )


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
        return own, re.compile(r"\[?v?([0-9]+\.[0-9]+\.[0-9]+).*")
    return "CHANGELOG.md", re.compile(
        r"\[" + re.escape(name) + r"[ \t]+([0-9]+\.[0-9]+\.[0-9]+)\].*"
    )


def collect(repo: Path, unsound: list[str]) -> dict[str, dict[str, object]]:
    marketplace_raw = read_text(repo, ".claude-plugin/marketplace.json", unsound)
    readme = read_text(repo, "README.md", unsound)
    if marketplace_raw is None or readme is None:
        return {}

    try:
        listed = list(json.loads(marketplace_raw)["plugins"])
    except (ValueError, KeyError, TypeError) as exc:
        unsound.append(f"marketplace.json is not readable as a plugin list: {exc}")
        return {}
    entries: list[tuple[str, object]] = []
    for entry in listed:
        name = entry.get("name") if isinstance(entry, dict) else None
        # Typed here rather than trusted downstream: a name that is present but not a string
        # (JSON null, a number, an object) used to survive as far as sorting the union and die
        # there on a TypeError -- an unclassified traceback exiting 1, the DISAGREEMENT code.
        if not isinstance(name, str) or not NAME_RE.fullmatch(name):
            unsound.append(f"marketplace.json lists {name!r}, which is not a plugin name")
            continue
        entries.append((name, entry.get("version")))

    row_matches = line_matches(ROW_RE, readme)
    heading_hits = heading_matches(HEADING_RE, readme)
    # `strip(" \t")`, not `strip()`: a version cell padded with U+00A0 is a cell whose text is
    # not the version, and saying so is more useful than silently accepting it.
    rows = {m.group(1): (m.group(2), m.group(3).strip(" \t")) for m in row_matches}
    # The slug is computed from the heading text as MATCHED, not rebuilt from the two groups:
    # GitHub turns each literal space into its own hyphen, so a double space slugs differently.
    headings = {m.group(1): (m.group(2), m.group(0)) for m in heading_hits}
    marketplace = dict(entries)
    # Counted, not just mapped: each of these three parses is last-one-wins, so a duplicate left
    # behind by a "keep both sides" conflict resolution would disappear into the map.
    seen = {
        "marketplace": Counter(name for name, _ in entries),
        "row": Counter(m.group(1) for m in row_matches),
        "heading": Counter(m.group(1) for m in heading_hits),
    }

    try:
        # Every directory, NOT only those carrying a manifest: a plugin directory with no
        # plugin.json is exactly the half-added plugin worth reporting, and filtering on the
        # manifest would drop the one case it should catch.
        on_disk = {d.name for d in (repo / "plugins").iterdir() if d.is_dir() and not d.name.startswith(".")}
    except OSError as exc:
        unsound.append(f"cannot list plugins/: {exc}")
        return {}

    # The UNION, not the marketplace list: a plugin present on disk and absent from the manifest
    # (or the README) is exactly the omission worth catching, and enumerating one source alone
    # would let it pass as "not a plugin".
    found: dict[str, dict[str, object]] = {}
    for name in sorted(set(marketplace) | set(rows) | set(headings) | on_disk):
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
        if not VERSION_RE.fullmatch(str(value)):
            problems.append(f"{label} carries {value!r}, which is not an X.Y.Z version")
    distinct = {str(v) for v in versions.values()}
    if len(distinct) > 1:
        problems.append(
            "surfaces disagree: " + ", ".join(f"{k}={v}" for k, v in versions.items())
        )
    if surfaces["heading_text"] is not None:
        expected = github_slug(str(surfaces["heading_text"]))
        if surfaces["anchor"] is None:
            problems.append(f"no README table row links to the section (its anchor would be #{expected})")
        elif surfaces["anchor"] != expected:
            problems.append(
                f"table row links to #{surfaces['anchor']}, but the heading slugifies to #{expected}"
            )
    if len(distinct) == 1:
        version = distinct.pop()
        rel, entry_re = changelog_source(repo, name)
        if not (repo / rel).is_file():
            problems.append(f"no changelog at {rel}")
        else:
            text = read_text(repo, rel, unsound)
            entries_found = [m.group(1) for m in heading_matches(entry_re, text or "")]
            if text is not None and version not in entries_found:
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
            emit(f"UNSOUND: {line}", sys.stderr)
        return 2

    if unsound:
        return give_up()
    if len(found) < MIN_PLAUSIBLE_PLUGINS:
        emit(f"UNSOUND: found {len(found)} plugin(s) under {repo} -- refusing to report that clean", sys.stderr)
        return 2

    width = max([len("plugin")] + [len(n) for n in found])
    print(COLUMN.join([f"{'plugin':{width}}"] + [f"{s:{CELL}}" for s in SURFACES]) + COLUMN + "anchor")
    failures = 0
    for name, surfaces in found.items():
        problems = judge(repo, name, surfaces, unsound)
        failures += bool(problems)
        cells = COLUMN.join(f"{str(surfaces[s]) if surfaces[s] else '-':{CELL}}" for s in SURFACES)
        emit(f"{name:{width}}{COLUMN}{cells}{COLUMN}{surfaces['anchor'] or '-'}")
        for problem in problems:
            emit(f"{'':{width}}{COLUMN}-> {problem}")

    emit("")
    emit(f"{len(found)} plugin(s) checked on 5 version surfaces + changelog; {failures} disagreeing")
    if unsound:
        return give_up()
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
