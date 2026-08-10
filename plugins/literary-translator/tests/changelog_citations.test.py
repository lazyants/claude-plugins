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
# Rewritten for 1.22.0 (#460, #450), and rewritten AGAIN within that same
# version: an earlier shape of this release also touched `claim_record.py`
# and `codex_job.py` (a fifth sentinel-participant role, an `any_foreign_claim()`
# fix, and a translate-chokepoint sentinel check). An eighth review round found
# that last piece still let a never-converged hand-edited draft be destroyed,
# and the owner cut it back out rather than ship it. This map covers only what
# actually ships: `select_segments.py` and `segment_dispatch_driver.py`.
# `claim_record.py` and `codex_job.py` are cited below only for CONTEXT they
# already carried before this release (they are not in the diff), never for a
# change 1.22.0 makes to them. The previous map (1.21.0's, #438) is gone in
# full -- it described the entry that is now the SECOND section in
# CHANGELOG.md, and this test covers the newest one only.
CITATION_ANCHORS = {
    # --- the admission relaxation: a dirty review admitted only as the -----
    # --- CONTINUATION of a loop this project already opened ----------------
    # Anchored at both ends: the def pins where the range starts, and the
    # docstring line stating the ordering rule pins that the rule is still
    # inside it by the time the range ends.
    "select_segments.py:2164-2169": [
        "def evaluate_open_review_loop(seg: str, owner_run_id, dirs: dict):",
        "it passes the DRAFT's own owner first.",
    ],
    # The two-probe call site: the draft's OWNER is asked first
    # (source_run_id), and only on refusal -- and only on D9's lost-token
    # recovery -- is THIS run asked second (args.run_id). Anchored on both
    # calls IN ORDER plus the gate between them, so a version that swapped the
    # probes, dropped the second, or dropped the gate that confines it to D9
    # loses an anchor. The gate is the load-bearing one: without it the second
    # probe widens admission to any run holding an unrelated older record.
    "select_segments.py:2402-2444": [
        "open_loop, why_not = evaluate_open_review_loop(seg, source_run_id, dirs)",
        "and lost_token_recovery",
        "open_loop, why_not_self = evaluate_open_review_loop(seg, args.run_id, dirs)",
    ],
    # The full-fourteen-field record check. Anchored on the field-membership
    # test itself and on the refusal message's closing clause, so a version
    # that kept the loop but stopped refusing on a partial record loses the
    # second anchor.
    "select_segments.py:2219-2225": [
        "missing = [field for field in claim_record.CLAIM_RECORD_FIELDS if field not in payload]",
        "not what select_segments.py produces",
    ],
    # --- the D9 ownership rule: "does anybody own it NOW" ------------------
    # evaluate_takeover_since_this_claim(), the admission-only helper that
    # REPLACED an any-holder rule that was written, rejected in review, and
    # never released. Anchored on the def, on the sentence separating it from
    # any_foreign_claim(), on the soundness argument the entry states, and
    # then on all four refusal clauses IN THE ORDER THE CODE EVALUATES THEM:
    # unreadable-record, the previous_dispatch_token successor test, the tie,
    # and strictly-later.
    #
    # The order is not decoration here. The tie branch exists precisely
    # because the comparison written the other way round (`foreign > this`
    # with no separate equality test) silently ADMITS a tie, which is the one
    # arrangement that lets an unprovable claim through -- so a version that
    # folded the tie into the comparison would keep `foreign_claimed_at >
    # this_claimed_at` and lose the anchor before it.
    "select_segments.py:1800-2024": [
        "def evaluate_takeover_since_this_claim(",
        "ADMISSION-ONLY, and deliberately NOT claim_record.any_foreign_claim().",
        "WHY A TIMESTAMP COMPARISON WORKS HERE, and EXACTLY HOW FAR THAT GOES.",
        'if claim_record.draft_owner_run_id(payload.get("previous_dispatch_token")) == this_run_id:',
        "if this_claimed_at == foreign_claimed_at:",
        "if foreign_claimed_at > this_claimed_at:",
        # The strictly-later branch's REFUSAL, not just its condition. Without
        # this the range stopped on the `if` and a change to what that branch
        # returns would leave every anchor in place.
        "owns the segment, so this run must not recover the draft.",
    ],
    # The same function's OWN enumeration is the first of the four is_dir()
    # sites below -- cited separately from the range above because the entry
    # makes a claim specifically about THIS loop's stat handling, not about
    # the function's ownership logic. Anchored on the iterdir(), the stat
    # call, and the S_ISDIR decision.
    "select_segments.py:1918-1968": [
        "entries = sorted(runs_dir.iterdir())",
        "entry_stat = os.stat(entry)",
        "if not stat.S_ISDIR(entry_stat.st_mode):",
    ],
    # Its call site: the LAST thing evaluate_lost_token_recovery() does, and
    # the refusal that carries the helper's reason outward. Anchored on both,
    # so a version that computed the verdict and then failed to refuse on it
    # -- the shape that would restore the original defect while keeping the
    # helper intact -- loses the second anchor.
    "select_segments.py:2153-2159": [
        "still_ours, takeover = evaluate_takeover_since_this_claim(",
        "segment, not that it still owns it: {takeover}",
    ],
    # write_claim_record()'s directory-fsync branch, which leaves a COMPLETE
    # record on disk on purpose rather than unlinking it -- pre-existing
    # 1.21.0 code, cited here only because #460's own new docstring above
    # names it as the ordinary-operator route to one of its two disclosed
    # residuals. Anchored on the deliberate-non-removal comment and the
    # failure return.
    "claim_record.py:720-726": [
        "sync_problem = fsync_directory(path.parent)",
        "Left on disk on purpose",
        "the claim record was written but {sync_problem}",
    ],
    "select_segments.py:3805-3812": [
        "token_ok, token_detail = rewrite_draft_dispatch_token(",
        "if not token_ok:",
        'write_failures.append(f"{seg}: dispatch_token rewrite failed',
    ],
    # any_foreign_claim() -- pre-existing, untouched by this release, cited
    # for the entry's claim that it keeps the any-holder rule where that rule
    # is still right (a translate chokepoint with no record of its own to
    # compare), and that it never had the is_dir() trap the four sites below
    # fix, because it never calls Path.is_dir() on an entry at all. Anchored
    # on the def, on the unreadable-counts-as-held docstring line, and on the
    # per-entry read that goes straight to classify_claim_record() with no
    # is_dir() guard in front of it.
    "claim_record.py:770-819": [
        "def any_foreign_claim(seg, this_run_id, runs_dir):",
        "Unreadable entries count as held.",
        "state, _detail = classify_claim_record(entry / f\"{CLAIM_PREFIX}{seg}\")",
        # The conversion of a non-ABSENT state into a REPORTED HOLDER is the
        # whole claim the entry makes about this function -- AMBIGUOUS counts
        # as held. Anchored so the range cannot stop just before it.
        "if state != CLAIM_ABSENT:",
        "return (run_id, state, entry / f\"{CLAIM_PREFIX}{seg}\")",
    ],
    # any_foreign_claim()'s ONLY caller anywhere in the shipped scripts, cited
    # for the entry's correction that the deliberately-unfixed ledger.json
    # defect does NOT reach D9's lost-token recovery. The claim is a
    # REACHABILITY one, so the anchors pin the two halves that carry it: that
    # this predicate is entered on the NO-TOKEN branch, and that the call to
    # any_foreign_claim() lives here rather than on any admission path.
    "claim_record.py:823-875": [
        "def foreign_owner_refusal(*, seg, this_run_id, draft_path, runs_dir):",
        "- NO TOKEN AT ALL -> any_foreign_claim() decides",
        "holder, state, path = any_foreign_claim(seg, this_run_id, Path(runs_dir))",
    ],
    # The other half of the same correction: the recovery's whole body, cited
    # for the claim that it reaches only claimed_path(), read_claim_record()
    # and evaluate_takeover_since_this_claim() -- never any_foreign_claim().
    # Anchored on the def and on both claim_record calls it does make, so a
    # future edit that adds an any_foreign_claim() call here does not silently
    # keep this citation valid while the sentence above it goes false.
    "select_segments.py:2034-2153": [
        "def evaluate_lost_token_recovery(seg: str, profile: str, run_id, durable_root: Path):",
        'path = claim_record.claimed_path(run_id, seg, durable_root / "runs")',
        "state, payload, detail = claim_record.read_claim_record(path)",
        # The third and last claim_record-reaching call the entry names. The
        # sentence citing this range asserts the recovery reaches exactly
        # these three and never any_foreign_claim(), so the range has to run
        # far enough to contain the last of them.
        "still_ours, takeover = evaluate_takeover_since_this_claim(",
    ],
    # classify_claim_record()'s lstat, cited for the mechanism the "what this
    # release does not fix" section describes: an ENOTDIR under a plain file
    # (runs/ledger.json) lands on the AMBIGUOUS branch via a non-None errno,
    # never on the FileNotFoundError/ABSENT branch. Anchored on the lstat
    # call, the OSError branch, and the final AMBIGUOUS return it reaches.
    "claim_record.py:291-306": [
        "st = path.lstat()",
        "except OSError as exc:",
        'return (CLAIM_AMBIGUOUS, f"lstat failed with {code}: {exc.strerror or exc}")',
    ],
    # The shipped path's only cross-run gate -- pre-existing, untouched by
    # this release, cited for the entry's claim that a token-less converged
    # draft with no sentinel protection reaches this SAME predicate on the
    # codex_job.py path, which is why the deliberately-unfixed defect above
    # affects both chokepoints identically.
    "codex_job.py:1100-1108": [
        "foreign_owner_refusal() is the single",
        "foreign = claim_record.foreign_owner_refusal(",
        "if foreign is not None:",
    ],
    # --- the is_dir()/glob() census: four sites, not five ------------------
    # scan_workflow_run_ids(), issue #462, pulled into this release. Anchored
    # on the def, the guarded iterdir(), and the per-entry stat split that
    # replaced a bare `p.is_dir()` filter inside a generator expression.
    "select_segments.py:785-871": [
        "entries = sorted(workflows_dir.iterdir())",
        "run_ids = []",
        "return sorted(run_ids)",
    ],
    # scan_dispatching_run_ids() -- the site with TWO swallowing constructs,
    # not one. Anchored on the def, the iterdir() that replaced `.is_dir()`,
    # and the loop that replaced `.glob("*.draft.json")` with a hand filter,
    # so a version that fixed only the guard and left the glob call in place
    # loses the last anchor.
    "select_segments.py:651-700": [
        "entries = sorted(segments_dir.iterdir())",
        "except (FileNotFoundError, NotADirectoryError):",
        "for path in entries:",
        'if not path.name.endswith(".draft.json"):',
    ],
    # _definitive_stat(), the named helper _resumable_run_id_candidates()
    # below converges on rather than hand-rolling a fourth copy of the split.
    # Anchored on the def and both arms of the split.
    "segment_dispatch_driver.py:2203-2241": [
        "def _definitive_stat(path: Path, *, refusal: str):",
        "except (FileNotFoundError, NotADirectoryError):",
        'fatal(f"{refusal} ({path} could not be inspected: {exc})", exit_code=2)',
    ],
    # _resumable_run_id_candidates()'s own fix, built on the helper above.
    # Anchored on the top-level stat, the guarded iterdir(), and the final
    # sort, so a version that restored the bare `p.is_dir()` list
    # comprehension loses every code-bearing anchor at once.
    "segment_dispatch_driver.py:2339-2385": [
        "runs_stat = _definitive_stat(",
        "entries = sorted(runs_dir.iterdir())",
        "candidates = []",
        "return sorted(candidates, reverse=True)",
    ],
    # --- the driver's new refusal: ctx.claims enforced, not merely read ----
    # Anchored on the def, the two lines of its actual logic, and the tail of
    # its refusal message -- four anchors across a docstring-heavy function,
    # so a version that kept the prose but changed the check (or its refuse
    # direction) loses one of the code-bearing anchors.
    "segment_dispatch_driver.py:1045-1102": [
        "def claim_capability_refusal_for_translate",
        "profile = ctx.claims.get(seg)",
        "if profile is None:",
        "reaches it (#450)",
    ],
    # The call site inside process_segment(), and what it sits before: the
    # PRE-EXISTING chokepoint (claim_refusal_for_translate) immediately
    # after it, and the ledger write after THAT. Anchored on all three calls
    # in order, so a version that reordered them, or dropped the pre-existing
    # check, loses an anchor rather than passing on prose alone.
    "segment_dispatch_driver.py:4728-4743": [
        "capability_refusal = claim_capability_refusal_for_translate(ctx, seg)",
        "claim_refusal = claim_refusal_for_translate(ctx, seg)",
        "rec = write_ledger(",
    ],
    # --- migration: which single script moves which single hash -----------
    # cache_key.py:156 pins segment_dispatch_driver.py as the sole changed
    # PLUGIN_BUNDLE_MEMBERS entry this release touches -- claim_record.py
    # sits on the neighboring line but is not part of this citation, because
    # the entry's claim is specifically that claim_record.py's membership
    # does NOT contribute this time.
    "cache_key.py:156": ['"segment_dispatch_driver.py",'],
    # scaffold_setup.py:77 pins select_segments.py as the sole changed
    # ORCHESTRATION_BUNDLE_MEMBERS entry, for the same reason.
    "scaffold_setup.py:77": ['"select_segments.py",'],
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
