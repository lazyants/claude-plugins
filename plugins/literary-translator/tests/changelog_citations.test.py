"""Every `file.py:NNN` citation in the newest CHANGELOG entry must still point
at what it claims to point at.

WHY THIS EXISTS, and why the obvious check is worthless. Release notes here cite
source by line number, and line numbers move whenever anything above them is
edited. Five separate review rounds of 1.20.0 found broken citations, and each
time the repair was manual re-resolution. A checker was written after round 5
and passed all 24 citations every round -- because it verified only that line
NNN *exists* in a file of at least NNN lines. That check cannot fail on drift:
a citation that slides nine lines down still points at a line that exists. It
shares its blind spot with the defect, so it reported clean while eight of the
citations pointed at unrelated code.

Round 8 found those eight. The cause was an edit to a docstring shared by four
scripts, made in round 7 to fix an unrelated prose error, which inserted nine
lines above most of the file's cited sites.

So the check here is on CONTENT: each citation declares an anchor -- a short
string that must appear within the cited line range. A citation that drifts no
longer matches its anchor and fails immediately, at the commit that moved it,
rather than in the next review round.

MAINTENANCE CONTRACT. This covers the NEWEST entry only, since that is the one
under active edit. Adding a citation to it means adding its anchor here; the
test fails on a citation with no anchor, and on an anchor no citation uses, so
neither half can silently rot. When a new version entry lands, this map is
rewritten for it -- the failure that forces that is the point, not an
annoyance."""

import re
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
CHANGELOG = PLUGIN_ROOT / "CHANGELOG.md"

# citation -> a string that MUST appear inside the cited line range.
# Anchors are identifiers and literals, not whole lines: reflowing a line should
# not fail this test, while moving the code it names must.
CITATION_ANCHORS = {
    "SKILL.md:419-448": "#409 upgrade note",
    "cache_key.py:139": "ledger_update.py",
    "cache_key.py:146": "segment_dispatch_driver.py",
    "cache_key.py:152": "DERIVATION_BUNDLE_MEMBERS",
    "draft_ready.py:323-331": "expect_token",
    "ledger_update.py:798-801": "draft changed since review",
    "resume_setup.py:719-722": "plugin_bundle_hash",
    "resume_setup.py:723-725": "orchestration_bundle_hash",
    "resume_setup.py:729-736": "digest_input",
    "scaffold_setup.py:63-68": "ORCHESTRATION_BUNDLE_MEMBERS",
    "segment_dispatch_driver.py:2601-2602": "translate_dispatch_token",
    "segment_dispatch_driver.py:2789-2804": "_matched_review_round_label",
    "segment_dispatch_driver.py:2891": "draft_ok",
    "segment_dispatch_driver.py:2969": '"action": "translate"',
    "segment_dispatch_driver.py:3073-3075": "draft_matches_review",
    "segment_dispatch_driver.py:3223-3230": "current_sha1 is None",
    "select_segments.py:810": "HUMAN_ESCALATION_STATUSES",
    "select_segments.py:1192-1210": "ledger_segments.get(seg)",
    "select_segments.py:1201-1206": "HUMAN_ESCALATION_STATUSES",
    "select_segments.py:1228": "DEFAULT_ELIGIBLE_CATEGORIES",
    "select_segments.py:1378-1384": "classify_ever_converged_sentinel",
    "select_segments.py:1386-1394": "ambiguous_sentinels",
    "select_segments.py:1428": "allow_retranslate_converged",
    "select_segments.py:1428-1477": "allow_retranslate_converged",
}

_CITATION = re.compile(r"([A-Za-z_][\w./-]*\.(?:py|md|json)):\s*\n?\s*(\d+)(?:-(\d+))?")


def _newest_entry():
    """The text of the first `## <version>` section, i.e. the release being
    edited. Returns (version, text)."""
    text = CHANGELOG.read_text(encoding="utf-8")
    heads = list(re.finditer(r"^## (\S+)", text, re.M))
    assert heads, "CHANGELOG has no `## <version>` headings"
    first = heads[0]
    end = heads[1].start() if len(heads) > 1 else len(text)
    return first.group(1), text[first.start():end]


def _resolve(filename):
    """The single source file a citation names. Test files are excluded: a
    citation in a release note always refers to shipped code, and `tests/`
    holds same-named helpers that would make the match ambiguous."""
    hits = [
        p
        for p in PLUGIN_ROOT.rglob(filename)
        if "node_modules" not in p.parts and "tests" not in p.parts
    ]
    assert len(hits) == 1, f"{filename} resolves to {len(hits)} files: {hits}"
    return hits[0]


def _cited_text(filename, start, end):
    lines = _resolve(filename).read_text(encoding="utf-8").splitlines()
    assert end <= len(lines), (
        f"{filename}:{start}-{end} runs past the end of the file ({len(lines)} lines)"
    )
    return "\n".join(lines[start - 1 : end])


def test_every_changelog_citation_still_points_at_its_claim():
    version, entry = _newest_entry()
    seen = {}
    for match in _CITATION.finditer(entry):
        filename, start, end = match.group(1), int(match.group(2)), match.group(3)
        key = f"{filename}:{start}" + (f"-{end}" if end else "")
        seen[key] = (filename, start, int(end) if end else start)

    undeclared = sorted(set(seen) - set(CITATION_ANCHORS))
    assert not undeclared, (
        f"{version} cites source with no anchor declared in this test: "
        f"{undeclared}. Add each one to CITATION_ANCHORS with a short string "
        f"that must appear in the cited range -- an un-anchored citation is "
        f"exactly the one that drifts unnoticed."
    )

    unused = sorted(set(CITATION_ANCHORS) - set(seen))
    assert not unused, (
        f"CITATION_ANCHORS declares anchors nothing in {version} cites: "
        f"{unused}. Either the citation was removed (drop the anchor) or it was "
        f"renumbered by hand without updating this map (fix the anchor)."
    )

    drifted = []
    for key, (filename, start, end) in sorted(seen.items()):
        anchor = CITATION_ANCHORS[key]
        body = _cited_text(filename, start, end)
        if anchor not in body:
            drifted.append(f"{key} no longer contains {anchor!r}; it now reads: {body.strip()[:120]!r}")
    assert not drifted, (
        "release-note citations have drifted off the code they describe:\n  "
        + "\n  ".join(drifted)
        + "\n\nThis is what a line-number citation does when anything above it "
        "is edited. Re-resolve each by CONTENT -- searching for the anchor -- "
        "never by assuming a uniform offset, because the shift is not uniform "
        "across files or across a file."
    )
