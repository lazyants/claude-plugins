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

So anchors are a LIST, and every one must be present. For a range, declare
enough of them to span the claim -- typically its first and last load-bearing
line. Content leaving the range then fails, which is the whole point.

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
CITATION_ANCHORS = {
    "SKILL.md:419-448": ["#409 upgrade note", "backfill_ever_converged.py"],
    "cache_key.py:139": ["ledger_update.py"],
    "cache_key.py:146": ["segment_dispatch_driver.py"],
    "cache_key.py:152": ["DERIVATION_BUNDLE_MEMBERS"],
    "draft_ready.py:323-331": ["expect_token", "stale/straggler draft"],
    "ledger_update.py:798-801": [
        "current_draft_sha1 != reviewer_draft_sha1",
        "draft changed since review",
    ],
    "resume_setup.py:719-722": ["version = {", "plugin_bundle_hash"],
    "resume_setup.py:723-725": ["orchestration_bundle_hash", "_read_marker"],
    "resume_setup.py:729-736": ["digest_input = {", "_sha256_hex"],
    "scaffold_setup.py:63-68": [
        "ORCHESTRATION_BUNDLE_MEMBERS",
        "select_segments.py",
    ],
    "segment_dispatch_driver.py:2601-2602": [
        "translate_dispatch_token",
        'f"{run_id}:{seg}"',
    ],
    "segment_dispatch_driver.py:2789-2804": [
        "_matched_review_round_label",
        "return None",
    ],
    "segment_dispatch_driver.py:2891": ["draft_ok"],
    "segment_dispatch_driver.py:2969": ['"action": "translate"'],
    "segment_dispatch_driver.py:3073-3075": [
        "draft_matches_review",
        "current_sha1 == reviewed_sha1",
    ],
    "segment_dispatch_driver.py:3223-3230": [
        "current_sha1 is None",
        "invocation never read",
    ],
    "select_segments.py:810": ["HUMAN_ESCALATION_STATUSES"],
    "select_segments.py:1192-1210": [
        "ledger_segments.get(seg)",
        "HUMAN_ESCALATION_STATUSES",
        '"category": "recoverable"',
    ],
    "select_segments.py:1201-1206": [
        "HUMAN_ESCALATION_STATUSES",
        'record.get("reason")',
    ],
    "select_segments.py:1228": ["DEFAULT_ELIGIBLE_CATEGORIES"],
    "select_segments.py:1378-1384": [
        "classify_ever_converged_sentinel",
        "ambiguous_sentinels.append",
    ],
    "select_segments.py:1386-1394": [
        "not clearable by --allow-retranslate-converged",
        "if ambiguous_sentinels:",
        "fatal(",
    ],
    "select_segments.py:1428": ["allow_retranslate_converged"],
    "select_segments.py:1428-1477": [
        "if previously_converged and not args.allow_retranslate_converged:",
        "second_loss",
        "previously_converged=previously_converged",
    ],
}

# Any `name.ext:NNN`, extension-agnostic. Pinning a list of extensions is how
# a citation becomes INVISIBLE to this test -- `template.js:12` and
# `Dockerfile:12` were both unmatched by an earlier `(py|md|json)` alternation,
# and an unmatched citation is one nothing here checks at all.
_CITATION = re.compile(r"\b([A-Za-z_][\w.-]*\.[A-Za-z][\w]*):(\d+)(?:-(\d+))?\b")
_FENCE = re.compile(r"^([ \t]*)(```+|~~~+).*$", re.M)
# `## 1.20.0 — 2026-08-06`: the version, then anything (this repo appends a
# release date). Requiring end-of-line after the version made this test fail on
# every real heading in the file.
_VERSION_HEADING = re.compile(r"^## (\d+\.\d+\.\d+)\b.*$", re.M)


def _strip_fenced_blocks(text):
    """Blank out fenced code blocks, keeping line numbering intact.

    A `## something` inside a fence is not a heading, and an earlier revision
    treated one as the start of the next entry -- everything after it became
    invisible to every assertion here."""
    out = list(text.splitlines(keepends=True))
    inside = False
    for i, line in enumerate(out):
        if _FENCE.match(line.rstrip("\n")):
            inside = not inside
            out[i] = "\n"
        elif inside:
            out[i] = "\n"
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
        for anchor in CITATION_ANCHORS[key]:
            if anchor not in body:
                drifted.append(f"{key} no longer contains {anchor!r}")
    assert not drifted, (
        "release-note citations have drifted off the code they describe:\n  "
        + "\n  ".join(drifted)
        + "\n\nRe-resolve each by CONTENT -- search for the anchor -- never by "
        "assuming a uniform offset. The shift is not uniform across files or "
        "within one: when this last fired, eight citations had moved by nine "
        "lines and a ninth had not moved at all."
    )
