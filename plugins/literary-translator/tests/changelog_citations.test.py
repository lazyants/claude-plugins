"""Every `file.ext:NNN` citation in the newest CHANGELOG entry must still point
at what it claims to point at.

WHY THIS EXISTS, and why two weaker versions of it did not work. Release notes
here cite source by line number, and line numbers move whenever anything above
them is edited. Five review rounds of 1.20.0 found broken citations.

The first checker verified only that line NNN *exists* in a file of at least
NNN lines. That cannot fail on drift -- a citation that slides nine rows still
points at a line that exists -- so it shared its blind spot with the defect and
reported clean for three rounds while eight citations pointed at unrelated
code. The cause was a docstring edit made in four scripts at once, which
inserted nine lines above most of the cited sites.

The second checker matched a single anchor string anywhere inside the cited
range. Better, but still defeatable, and it was defeated: inserting comment
lines *inside* a wide range pushed the code the claim is about past the range's
end while the anchor sat safely near the start, and the check passed.

So anchors are a LIST, and every one must be present IN THE DECLARED ORDER.
For a range, declare enough of them to span the claim -- typically its first
and last load-bearing line, plus whatever the claim is actually ABOUT in
between. Order is checked because for some citations the order IS the claim:
a two-probe call site that must ask the draft's owner first is pinned by two
anchors whose sequence is the whole property, and a presence-only check stays
green through a swap.

WHAT THIS STILL DOES NOT CATCH, stated because two earlier versions of this
docstring claimed more than the code delivered. Anchors detect content leaving
a range; they do not detect content CHANGING inside it. `fatal(` becoming
`print(` keeps every anchor in range, and so does refactoring a call into a
payload built in-range and dispatched after it. Anchoring the load-bearing
tokens of the claim narrows that -- which is why `fatal(` is anchored rather
than only the condition guarding it -- but a semantic mutant that preserves
every anchored token passes. This checks that a citation still POINTS at its
claim, not that the claim is still TRUE.

Nor is a range checked for TIGHTNESS: anchors must be INSIDE it, never fill
it, so widening one until it swallows an adjacent claim passes here --
measured, by widening a citation back over the block it had just been split
away from. Closing that is not wanted, because a tightness rule would fail
every legitimate range that spans a docstring; splitting one function into
two citations because the entry makes two claims about it is therefore a
convention a reviewer enforces, not something this test can.

MAINTENANCE CONTRACT. This covers the NEWEST entry only, since that is the one
under active edit. Adding a citation means adding its anchors here; the test
fails on a citation with no anchors and on anchors no citation uses, so neither
half can silently rot. When a new version entry lands, this map is rewritten
for it -- the failure that forces that is the point, not an annoyance."""

import re
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
CHANGELOG = PLUGIN_ROOT / "CHANGELOG.md"

# citation -> every string that must appear inside the cited line range.
# Anchors are identifiers and literals rather than whole lines, so reflowing a
# line does not fail this test while moving the code it names does. For a
# range, the first and last load-bearing lines are both anchored: one anchor
# only pins where the range STARTS, and a claim can slide out of the far end.
#
# Rewritten for 1.25.0 (#491/#490 -- the machinery-only stale carve-out, and
# --from-converged admitting a moved standard rather than only a moved
# draft), per the maintenance contract above: 1.24.0's own map went with its
# entry. Every citation below was resolved BY CONTENT against the worktree
# and RE-VERIFIED that each anchor appears, in order, inside its range --
# never by carrying an offset over, not even for the files this release
# leaves untouched (final_audit.py, cache_key.py). A uniform offset is never
# safe: 1.24.0's own rewrite shifted select_segments.py by +12 in one band
# and +55 in the next, and an untouched file whose citation still happens to
# contain its anchor text is exactly what a re-numbering produces without
# anyone re-reading it.
CITATION_ANCHORS = {
    # This map tracks the NEWEST changelog entry only, and is rewritten every
    # release. 1.26.0 is documentation-only: it states two operator rules (R8,
    # R9) and cites exactly one source range, the line that decides which
    # artifact a codex job may publish -- the whole basis for R8's claim that
    # codex cannot perform the fix turn. If that expression moves or is
    # renamed, R8's stated reason has silently lost its evidence.
    "codex_job.py:772": ['ext = "draft" if self.kind == "translate" else "review"'],
}

# Any `name.ext:NNN`. Extension-AGNOSTIC, not extension-free: a dot and an
# alphabetic extension are still required. Pinning a list of extensions was
# worse -- `template.js:12` was unmatched by an earlier `(py|md|json)`
# alternation, and an unmatched citation is one nothing here checks at all.
#
# Still invisible, and this is a real gap rather than a rhetorical one:
# extensionless filenames (`Dockerfile:12`, `Makefile:12`) and dotfiles
# (`.gitignore:12`). This entry cites none, so the gap is currently latent;
# cite one and it will be silently unchecked rather than reported.
#
# In the other direction it over-matches: `foo.bar:1` in prose and a
# `https://host.tld:1/path` URL both look like citations. That direction is
# SAFE -- an over-match lands in `undeclared` and fails loudly, so the cost is
# a bogus anchor entry, never a missed drift. `v1.20.0:12`, `12:30` and `3:1`
# do not match (no alphabetic extension).
_CITATION = re.compile(r"\b([A-Za-z_][\w.-]*\.[A-Za-z][\w]*):(\d+)(?:-(\d+))?\b")
# A fence line, CommonMark subset: up to 3 leading spaces, then a run of at
# least 3 backticks or tildes, then an optional info string. An earlier version
# matched ANY indentation and either character interchangeably, which made it
# not a state machine at all: four-space-indented code opened a fence, a `~~~`
# line closed a backtick fence, and a short closer closed a long opener. Each
# of those silently blanked real content, and blanked content is invisible to
# every assertion below.
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})[^`]*$")
# `## 1.20.0 — 2026-08-06`: the version, then anything (this repo appends a
# release date). Requiring end-of-line after the version made this test fail on
# every real heading in the file.
_VERSION_HEADING = re.compile(r"^## (\d+\.\d+\.\d+)\b.*$", re.M)


def _strip_fenced_blocks(text):
    """Blank out fenced code blocks, keeping line numbering intact.

    A `## something` inside a fence is not a heading, and an earlier revision
    treated one as the start of the next entry -- everything after it became
    invisible to every assertion here.

    Only the opening fence's own character closes it, and only a closer at
    least as long. An unclosed fence RAISES rather than blanking the rest of
    the file: silently swallowing everything after a typo'd fence is the
    failure mode this whole test exists to avoid, and it looks identical to a
    clean run."""
    out = list(text.splitlines(keepends=True))
    marker = None  # the opening run, e.g. '```' -- None when outside a fence
    opened_at = None
    for i, line in enumerate(out):
        match = _FENCE.match(line.rstrip("\n"))
        if marker is None:
            if match:
                marker, opened_at = match.group(1), i + 1
                out[i] = "\n"
            continue
        # Inside: only the same character, at least as long, closes it.
        run = match.group(1) if match else ""
        if run and run[0] == marker[0] and len(run) >= len(marker):
            marker = None
        out[i] = "\n"
    assert marker is None, (
        f"CHANGELOG has an unclosed `{marker}` fence opened at line "
        f"{opened_at}. Everything after it would be treated as code and "
        f"silently excluded from every check here -- including any citation "
        f"in it. Close the fence."
    )
    return "".join(out)


def _newest_entry():
    """(version, text) of the first `## <semver>` section -- the release being
    edited. The heading must be a version: matching any `## <token>` let a
    non-version heading masquerade as the newest entry."""
    text = _strip_fenced_blocks(CHANGELOG.read_text(encoding="utf-8"))
    heads = list(_VERSION_HEADING.finditer(text))
    assert heads, "CHANGELOG has no `## <major.minor.patch>` heading"
    first = heads[0]
    end = heads[1].start() if len(heads) > 1 else len(text)
    return first.group(1), text[first.start() : end]


def _resolve(filename):
    """The single source file a citation names. `tests/` is excluded: a release
    note cites shipped code, and tests hold same-named helpers."""
    hits = [
        p
        for p in PLUGIN_ROOT.rglob(filename)
        if "node_modules" not in p.parts and "tests" not in p.parts
    ]
    assert len(hits) == 1, f"{filename} resolves to {len(hits)} files: {hits}"
    return hits[0]


def test_every_changelog_citation_still_points_at_its_claim():
    version, entry = _newest_entry()

    # An empty anchor list is indistinguishable from a declared one to every
    # check below -- the key is present, so it is not `undeclared`, and the
    # per-anchor loop runs zero times. Emptying a list is therefore the
    # cheapest way to silently retire a citation from this test while it still
    # looks covered. Measured: doing that to `segment_dispatch_driver.py`
    # passed before this assertion existed.
    empty = sorted(k for k, v in CITATION_ANCHORS.items() if not v)
    assert not empty, (
        f"these citations declare an EMPTY anchor list, which checks nothing: "
        f"{empty}. Declare the strings that must appear in each range, or drop "
        f"the citation from the entry."
    )
    seen = {}
    for match in _CITATION.finditer(entry):
        filename, start, end = match.group(1), int(match.group(2)), match.group(3)
        key = f"{filename}:{start}" + (f"-{end}" if end else "")
        seen[key] = (filename, start, int(end) if end else start)

    undeclared = sorted(set(seen) - set(CITATION_ANCHORS))
    assert not undeclared, (
        f"{version} cites source with no anchors declared in this test: "
        f"{undeclared}. Add each one to CITATION_ANCHORS with the strings that "
        f"must appear in its range -- an un-anchored citation is exactly the "
        f"one that drifts unnoticed."
    )
    unused = sorted(set(CITATION_ANCHORS) - set(seen))
    assert not unused, (
        f"CITATION_ANCHORS declares anchors nothing in {version} cites: "
        f"{unused}. Either the citation was removed (drop the anchors) or it "
        f"was renumbered by hand without updating this map (fix them)."
    )

    drifted = []
    for key, (filename, start, end) in sorted(seen.items()):
        lines = _resolve(filename).read_text(encoding="utf-8").splitlines()
        if end > len(lines):
            drifted.append(f"{key} runs past the end of the file ({len(lines)} lines)")
            continue
        body = "\n".join(lines[start - 1 : end])
        # Anchors are checked IN THE ORDER they are declared, because for some
        # citations the order is the property being pinned (a two-probe call
        # site that asks the draft's owner first, say). Presence alone would
        # stay green through a swap.
        cursor = 0
        for anchor in CITATION_ANCHORS[key]:
            at = body.find(anchor, cursor)
            if at < 0:
                if anchor in body:
                    drifted.append(
                        f"{key} still contains {anchor!r}, but no longer AFTER the "
                        f"anchor declared before it -- the order the citation pins "
                        f"has changed"
                    )
                else:
                    drifted.append(f"{key} no longer contains {anchor!r}")
                break
            cursor = at + len(anchor)
    assert not drifted, (
        "release-note citations have drifted off the code they describe:\n  "
        + "\n  ".join(drifted)
        + "\n\nRe-resolve each by CONTENT -- search for the anchor -- never by "
        "assuming a uniform offset. The shift is not uniform across files or "
        "within one: when this last fired, eight citations had moved by nine "
        "lines and a ninth had not moved at all."
    )
