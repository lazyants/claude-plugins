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

  - It compares the surfaces against EACH OTHER, and it compares ONE of them -- `plugin.json`, the
    file `claude plugin update` reads -- against `origin/main`, refusing a tree that would publish
    a version LOWER than the one already there. It still cannot tell you which surface your change
    forgot from which was already wrong before you started: the baseline answers "does this move
    the published number backwards", not "which of these five is stale". For that, the doc's next
    bullet has you grep each surface on `origin/main`.
  - The baseline reads the LOCAL `origin/main`; nothing here fetches. A stale remote-tracking ref
    yields a stale baseline, and a checkout without `origin/main` gets no comparison at all -- the
    summary then says NOT COMPARED rather than reporting the sweep clean, because "could not
    compare" and "compared and agreed" are exactly the pair a green line used to conflate.
  - The LEFT-hand side is the WORKING TREE, on disk, which is what "run it before the commit" means
    and is deliberate: the point is to judge what you are about to commit. It is NOT the index, so a
    manifest staged differently from the file on disk is judged as the file on disk -- true of every
    surface this has ever read, and out of scope here. The right-hand side is git's own record at
    `origin/main`, and only that side has to be walkable.
  - A tree whose version EQUALS the version at `merge-base HEAD origin/main` is never refused,
    however far behind `origin/main` it sits. A merge applies this branch's DIFF, and a branch that
    ends where it started cannot carry the number backwards; unbumped branches are the normal case
    in this repo, so refusing them would be the same false reading with the sign flipped.
  - The changelog is only checked once the five surfaces agree, because until they do there is no
    single version to look for. A first run over a mismatched tree therefore does not enumerate
    all the remaining work; re-run after fixing what it named.
  - It says nothing about whether a version is the right one to release, nothing about
    `metadata.version`, and nothing about the README section's BODY PROSE, which the doc calls a
    hidden layer of the README surface and which no parser can judge.

Exit: 0 every plugin agrees; 1 at least one disagreement (each named); 2 the sweep itself is
unsound -- a file it MUST read to run at all is missing or unreadable, so few plugins were found
that a clean result would be vacuous, or git records something on the manifest's path at the
BASELINE ref that it will not walk into -- a symlink, a gitlink, a name this filesystem cannot tell
apart from another. Then released state is unreadable in a way that is indistinguishable from a
plugin that simply is not published yet, which is the one answer that would wave a downgrade
through, so the sweep refuses rather than guess. A plugin's own `plugin.json` or changelog going
missing is deliberately NOT unsoundness: those are findings about that plugin, exit 1.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import unicodedata
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
# What the released state is read from. Only ever read, never fetched: see the docstring.
BASELINE_REF = "origin/main"
COMPARED = "compared"
# The mode git records for a directory. Anything else at `plugins` or `plugins/<name>` -- a symlink
# (120000), a submodule gitlink (160000), a plain file -- means the two halves of this comparison
# are not looking through the same thing: the working-tree half FOLLOWS a symlink, and
# `git show <ref>:a/b/c` does not follow one stored in a tree and stops dead at a gitlink. A plugin
# behind either makes `git show` fail exactly as an absent plugin does, and "no baseline yet" is
# the one answer that must never be given for a plugin that HAS one -- it waves a downgrade through.
TREE_MODE = "040000"
BLOB_MODES = ("100644", "100755")
# Every way the baseline comparison can be unavailable, and the words the summary uses for it.
# These are a SET, not a fallback: "compared and agreed" and "could not compare" printing the same
# green line is the whole defect this baseline exists to close, so an unavailable comparison has to
# name which unavailability it was -- and each sentence has to be TRUE of every case that reaches
# it. "This plugin has no baseline yet" is the one that would wave a real downgrade through, so
# nothing else is allowed to borrow it.
NOT_COMPARED = {
    "not-a-repo": "this is not a git checkout (or git is unavailable), so nothing was compared",
    "no-ref": f"no usable {BASELINE_REF} commit in this checkout, so nothing was compared",
    "absent": f"the plugin has no manifest on {BASELINE_REF} yet (a new plugin has no baseline)",
    "unreadable": f"the manifest on {BASELINE_REF} is not readable as an X.Y.Z version",
    "topology": f"git records that path on {BASELINE_REF} as something it cannot walk into -- see UNSOUND",
    "no-merge-base": f"git could not establish a merge base between HEAD and {BASELINE_REF}",
    "disagree": "the surfaces do not agree on one valid X.Y.Z version, so there was nothing to compare",
}
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


def git(repo: Path, *args: str) -> str | None:
    """One git read, or None if git cannot answer it.

    Every failure collapses to None deliberately: no git binary on PATH, `repo` not a checkout at
    all, the ref absent (a fresh clone with no remote, a detached CI checkout), the path absent at
    that ref. None of those says anything is wrong with the tree in front of us, so none of them
    may reach `unsound` and turn a legitimate checkout into exit 2. They make the comparison
    UNAVAILABLE, which the summary reports in its own words instead of hiding behind a clean line.
    """
    try:
        proc = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, check=False)
    except OSError:
        return None
    if proc.returncode != 0:
        return None
    try:
        # Decoded HERE, not by subprocess: `text=True` raises UnicodeDecodeError out of the call
        # itself, which is not an OSError and escaped as a traceback under exit 1 -- the code that
        # means "a real disagreement". Undecodable bytes are just another thing git would not give
        # us in a usable form, and they take the same None path as the rest.
        return proc.stdout.decode("utf-8")
    except UnicodeDecodeError:
        return None


def version_key(value: object) -> tuple[int, ...] | None:
    """X.Y.Z as NUMBERS, or None if it is not one.

    Not a string comparison, ever: `"1.9.0" > "1.10.0"` lexically, which is a real version pair in
    this repo's history and would have read as "the tree is ahead" while it was nine releases behind.
    """
    return tuple(int(part) for part in str(value).split(".")) if VERSION_RE.fullmatch(str(value)) else None


def tree_index(repo: Path, ref: str) -> dict[str, tuple[str, str]] | None:
    """Every path git records under `plugins/` at `ref`, with its mode and object id.

    ONE listing, and every question about released state is answered out of it. That is the whole
    design, and it exists because looking a path UP -- `git show <ref>:a/b/c` -- cannot tell "not
    there" from "there, behind something git will not walk into", and a symlink or a gitlink at ANY
    component produces the second while reading exactly like the first. Three review rounds each
    found a new depth for that same confusion: `plugins` itself, then `plugins/<name>`, then
    `.claude-plugin` and the manifest leaf. Enumerating the topologies was the wrong shape -- there
    is always a deeper one. A recursive listing is the SET of what exists, so absence is set
    membership and topology is the mode already sitting beside each entry. Neither is inferred from
    a failure, and there is no next level to be surprised by.
    """
    listing = git(repo, "ls-tree", "-r", "-t", "-z", "--full-tree", ref, "--", "plugins")
    if listing is None:
        return None
    index: dict[str, tuple[str, str]] = {}
    for entry in listing.split("\0"):
        meta, _, path = entry.partition("\t")
        fields = meta.split()
        if path and len(fields) == 3:
            index[path] = (fields[0], fields[2])
    return index


def same_path(one: str, other: str) -> bool:
    """Whether two tracked paths would collide as FILES on this checkout's filesystem.

    macOS is case-insensitive and normalization-insensitive; git is neither. A manifest committed as
    `plugins/Alpha/...` and a directory read from disk as `plugins/alpha` are one file here and two
    keys there. So the tree can say two things where the checkout can only hold one, and which one
    it holds is decided by tree order -- measured: three spellings of one name materialised a single
    inode carrying the LAST-sorting one's bytes. Not a thing to model; a thing to refuse.
    """
    return (unicodedata.normalize("NFC", one).casefold()
            == unicodedata.normalize("NFC", other).casefold())


def baseline_blob(
    index: dict[str, tuple[str, str]], name: str, where: str = BASELINE_REF
) -> tuple[str | None, str, str]:
    """The manifest blob for `name` in a tree index: (object id, outcome, detail).

    `where` names the ref being read, for the message only. Passed in rather than patched into the
    finished sentence afterwards: the old spelling rewrote the ref name inside the composed string,
    which quietly depended on no repository path ever containing the literal "origin/main".

    `topology` means the sweep must not answer at all: something on the path is not a directory
    git can walk into, or the tree spells the name differently than this filesystem does, and in
    both cases a missing entry says nothing about whether a baseline exists.
    """
    rel = f"plugins/{name}/.claude-plugin/plugin.json"
    for step in ("plugins", f"plugins/{name}", f"plugins/{name}/.claude-plugin"):
        mode = index.get(step, (None, None))[0]
        if mode is not None and mode != TREE_MODE:
            return None, "topology", f"{step} is mode {mode} on {where}, not a directory git can walk into"
    # Every key this filesystem cannot tell apart from the manifest path, the exact spelling
    # included. Asked as ONE question rather than as an exact hit with a fallback: a tree can hold
    # several colliding spellings AND the exact one at the same time, and then the exact key is
    # found, used, and the ambiguity never surfaces -- while a checkout collapses all of them into
    # one file whose content is decided by tree order, which is not a thing to model.
    spellings = sorted(path for path in index if same_path(path, rel))
    if len(spellings) > 1:
        return None, "topology", (
            f"{where} carries {len(spellings)} spellings of this manifest "
            f"({', '.join(spellings)}), which are one file on this filesystem"
        )
    if not spellings:
        return None, "absent", ""
    mode, sha = index[spellings[0]]
    if mode in BLOB_MODES:
        return sha, COMPARED, ""
    return None, "topology", f"{spellings[0]} is mode {mode} on {where}, not a regular file"


def blob_version(repo: Path, sha: str) -> str | None:
    """The `version` an already-located manifest blob holds, or None if it holds no X.Y.Z one."""
    raw = git(repo, "cat-file", "blob", sha)
    if raw is None:
        return None
    try:
        # AttributeError, not only ValueError: a manifest that parses as a list, a number or a
        # string is valid JSON with no `.get` on it.
        version = json.loads(raw).get("version")
    except (ValueError, AttributeError):
        return None
    return str(version) if version_key(version) else None


def baseline_refs(repo: Path) -> tuple[str | None, str | None, str | None]:
    """`origin/main`, the merge base of HEAD with it, and -- when there is no baseline -- WHY.

    The merge base is what separates "this branch MOVES the version" from "this branch is merely
    BEHIND". Resolved once for the checkout rather than per plugin, so the rows cannot disagree.
    The two reasons are kept apart because "you have no remote-tracking ref" and "this is not a
    checkout at all" are different things to go and fix.
    """
    published = git(repo, "rev-parse", "--verify", f"{BASELINE_REF}^{{commit}}")
    if published is None:
        return None, None, "no-ref" if git(repo, "rev-parse", "--git-dir") is not None else "not-a-repo"
    base = git(repo, "merge-base", "HEAD", BASELINE_REF)
    return published.strip(), (base.strip() if base and base.strip() else None), None


def baseline_problem(
    repo: Path,
    name: str,
    version: str,
    refs: tuple[str | None, str | None, str | None],
    indexes: dict[str, dict[str, tuple[str, str]] | None],
    unsound: list[str],
) -> tuple[str | None, str]:
    """Would merging this tree LOWER what `origin/main` already publishes?

    Returns the problem (or None) and which comparison actually happened -- `COMPARED`, or a key of
    `NOT_COMPARED`. The caller needs both: a run that compared nothing must not read like a run
    that compared everything and agreed.

    Merge-to-main IS publish in this repo, and `claude plugin update` resolves the number in
    `plugin.json`, so a tree stamped below the published version is a downgrade shipped to every
    installed copy. That is why this is a refusal and not a warning.
    """
    published_ref, base_ref, unavailable = refs
    if published_ref is None:
        return None, unavailable or "no-ref"
    if version_key(version) is None:
        # Every surface says the same thing and that thing is not a version. `judge` has already
        # said so; what must not happen is this plugin being TALLIED as compared when no comparison
        # was possible, which is the summary lying about its own coverage.
        return None, "disagree"
    published_index = indexes[published_ref]
    if published_index is None:
        # The listing itself failed, so no manifest was looked at at all -- this branch BORROWS the
        # unreadable sentence rather than being described by it. Near-unreachable (the ref was
        # verified above, and `ls-tree` of a valid commit exits 0 even when its pathspec matches
        # nothing), which is why it stays a borrowed reason instead of earning its own key.
        return None, "unreadable"
    sha, outcome, detail = baseline_blob(published_index, name)
    if outcome == "topology":
        unsound.append(detail)
        return None, outcome
    if sha is None:
        return None, outcome
    published = blob_version(repo, sha)
    if published is None:
        return None, "unreadable"
    here, there = version_key(version), version_key(published)
    if here >= there:
        return None, COMPARED
    # Behind what is published. Only a tree that CHANGED the version carries that backwards on
    # merge -- a branch whose manifest still holds the merge-base value merges as a diff that never
    # mentions the file. Equality with the merge base is the test, not "did the branch touch the
    # file": a bump that was later reverted also ends where it started and is equally harmless.
    base_index = indexes.get(base_ref) if base_ref is not None else None
    if base_index is None:
        return None, "no-merge-base"
    base_sha, base_outcome, base_detail = baseline_blob(base_index, name, "the merge base")
    if base_outcome == "topology":
        unsound.append(base_detail)
        return None, "topology"
    if base_sha is not None and blob_version(repo, base_sha) == version:
        return None, COMPARED
    return (
        f"would publish a DOWNGRADE: this tree stamps {version}, "
        f"{BASELINE_REF} already publishes {published}"
    ), COMPARED


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


def judge(
    repo: Path,
    name: str,
    surfaces: dict[str, object],
    unsound: list[str],
    refs: tuple[str | None, str | None, str | None],
    indexes: dict[str, dict[str, tuple[str, str]] | None],
) -> tuple[list[str], str]:
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
    if len(distinct) != 1:
        # Same gate the changelog check has always used, and for the same reason: with the surfaces
        # in disagreement there is no single version this tree can be said to stamp, so there is
        # nothing to compare a baseline against. The run already exits 1 on the disagreement, and
        # the re-run this file's docstring requires applies the baseline to the repaired tree --
        # which is why the outcome is REPORTED rather than guessed at from one chosen surface.
        return problems, "disagree"
    version = distinct.pop()
    rel, entry_re = changelog_source(repo, name)
    if not (repo / rel).is_file():
        problems.append(f"no changelog at {rel}")
    else:
        text = read_text(repo, rel, unsound)
        entries_found = [m.group(1) for m in heading_matches(entry_re, text or "")]
        if text is not None and version not in entries_found:
            problems.append(f"{rel} has no entry for {version}")
    regression, outcome = baseline_problem(repo, name, version, refs, indexes, unsound)
    if regression is not None:
        problems.append(regression)
    return problems, outcome


def baseline_summary(refs: tuple[str | None, str | None, str | None], outcomes: Counter[str]) -> list[str]:
    """The line that has to be readable as something OTHER than an all-clear when nothing compared.

    The summary above it counts disagreements, and a sweep that compared no baseline at all
    produces exactly the same count as one that compared every plugin and found them all ahead.
    Separating those two is the entire point of the baseline, so it gets its own line and the
    words NOT COMPARED, with the reason spelled out per bucket rather than left to be inferred
    from a zero.
    """
    published_ref = refs[0]
    where = f"{BASELINE_REF} ({published_ref[:8]})" if published_ref else BASELINE_REF
    compared = outcomes.get(COMPARED, 0)
    skipped = sorted((key, n) for key, n in outcomes.items() if key != COMPARED)
    lines = [f"baseline vs {where}: {compared} compared, {sum(n for _, n in skipped)} NOT COMPARED"]
    lines.extend(f"  NOT COMPARED x{n}: {NOT_COMPARED[key]}" for key, n in skipped)
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[4],
                        help="repository root (default: the checkout this script lives in)")
    args = parser.parse_args()
    repo = args.repo.resolve()

    unsound: list[str] = []
    found = collect(repo, unsound)
    refs = baseline_refs(repo)
    # One recursive listing per ref, shared by every plugin: absence, topology and content all
    # come out of it, so no plugin can be told a different story about the same tree.
    indexes = {ref: tree_index(repo, ref) for ref in dict.fromkeys(refs[:2]) if ref is not None}

    def give_up() -> int:
        # De-duplicated: a problem with `plugins` itself is true of every plugin and is therefore
        # discovered once per plugin. Printing it seven times reads as seven findings.
        for line in dict.fromkeys(unsound):
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
    outcomes: Counter[str] = Counter()
    for name, surfaces in found.items():
        problems, outcome = judge(repo, name, surfaces, unsound, refs, indexes)
        outcomes[outcome] += 1
        failures += bool(problems)
        cells = COLUMN.join(f"{str(surfaces[s]) if surfaces[s] else '-':{CELL}}" for s in SURFACES)
        emit(f"{name:{width}}{COLUMN}{cells}{COLUMN}{surfaces['anchor'] or '-'}")
        for problem in problems:
            emit(f"{'':{width}}{COLUMN}-> {problem}")

    emit("")
    emit(f"{len(found)} plugin(s) checked on 5 version surfaces + changelog; {failures} disagreeing")
    for line in baseline_summary(refs, outcomes):
        emit(line)
    if unsound:
        return give_up()
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
