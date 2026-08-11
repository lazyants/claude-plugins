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
# Rewritten for 1.24.0 (#455, the --from-stalled admission profile). Every
# range below was re-resolved BY CONTENT against the worktree TWICE after the
# first resolution: once after simplifier-455's cleanup pass, a fix-now D3b
# guard, and a --help rewrite to match it landed together (moving every
# select_segments.py range -- not by a uniform offset, some by single digits,
# some by dozens -- and changing one CONTENT rather than merely position: the
# duplicated two-probe continuation logic that used to sit inline at both
# --from-stalled's and --from-converged's call sites was extracted into
# evaluate_open_review_loop_with_recovery(), so the citation that used to
# point at inline duplication now names that function, and the old single
# 2235-2313 citation split into two -- one for the extracted wrapper, one for
# the base predicate it still calls); and again after a codex review round
# caught a producer/schema mismatch a prior CHANGELOG draft had missed --
# claim_record.py's field DOCUMENTATION for cache_key_note was made
# profile-aware, but select_segments.py, the field's actual PRODUCER, still
# hard-coded a --from-cap-only string into every claim record lacking a
# cache_key, including --from-stalled ones. Fixing that string shifted every
# select_segments.py range below it a second time. segment_dispatch_driver.py,
# claim_record.py, cache_key.py, scaffold_setup.py, codex_job.py and
# mass-translate-wf.template.js were untouched both rounds and were
# RE-VERIFIED by content anyway, not merely assumed stable because their line
# numbers had not moved -- a citation that happens to still contain its
# anchor text by coincidence is exactly the case a re-numbering could produce
# without anyone re-reading it. Never assume a uniform offset going forward
# either -- re-resolve by content.
CITATION_ANCHORS = {
    # --- #455: the population, and the branch that admits it ---------------
    # parse_claim_requests(): the three-profile collision map, anchored on
    # the def, on the per-id bookkeeping dict, on the map->collision
    # derivation the entry's "names which TWO flags collide" claim rests on,
    # and on the return -- so a version that reverted to the old "count only"
    # message keeps every anchor but the middle one is still present, and the
    # ORDER (dict built, then filtered into collisions) is what a swap back
    # to the old set-of-ids shape would break.
    "select_segments.py:1643-1688": [
        'def parse_claim_requests(args) -> "dict[str, str]":',
        "flags_by_seg: dict = {}",
        "collisions = {seg: names for seg, names in flags_by_seg.items() if len(names) > 1}",
        "return requests",
    ],
    # evaluate_claim_admission()'s own --from-stalled branch. Anchored on the
    # elif that opens it, on the two ledger-field reads the condition list's
    # "materialized status" and "reviewed_draft_sha1" claims are about, on the
    # continuation call (the entry's "authenticated the same way" claim --
    # now the extracted _with_recovery() wrapper, not the base predicate
    # directly), and on the disclosure appended to the staleness refusal --
    # in the order the branch evaluates them, since the entry's own prose
    # walks the conditions in this order and a reordering that kept every
    # string would still be a different claim about what fails first.
    "select_segments.py:2662-2797": [
        "elif profile == CLAIM_PROFILE_FROM_STALLED:",
        'status = ledger_record.get("status")',
        'reviewed_draft_sha1 = ledger_record.get("reviewed_draft_sha1")',
        "open_loop, why_not = evaluate_open_review_loop_with_recovery(",
        'f"{FROM_STALLED_DISCLOSURE}"',
    ],
    # The single disclosure line inside that branch, cited standalone (the
    # entry names this exact line as one of the four refusal sites carrying
    # FROM_STALLED_DISCLOSURE verbatim).
    "select_segments.py:2796": ['f"{FROM_STALLED_DISCLOSURE}"'],
    # evaluate_open_review_loop()'s #455 generalization: anchored on the def
    # (which now carries the keyword-only expected_profile param in its own
    # signature -- a regression to a hard-coded profile changes this exact
    # line), on the profile-agreement check the entry's "own profile rather
    # than silently inheriting" claim is about, and on the success return.
    # This is now the BASE predicate only -- both call sites reach it through
    # evaluate_open_review_loop_with_recovery() below, not directly.
    "select_segments.py:2238-2316": [
        "def evaluate_open_review_loop(seg: str, owner_run_id, dirs: dict, *, expected_profile: str):",
        'if payload.get("profile") != expected_profile:',
        'return True, ""',
    ],
    # evaluate_open_review_loop_with_recovery(): the #455-era extraction of
    # the two-probe continuation logic that used to be duplicated once per
    # profile. Anchored on the def, on the docstring's own "ONE construction,
    # used by BOTH profiles" claim (the entry's own reworded paragraph rests
    # on this being true), on the SECOND probe (D9's lost-token fallback to
    # THIS run, the entry's own "or, on the lost-token path, this run"
    # clause), and on the merged return -- in order, since the first probe
    # (inside evaluate_open_review_loop() itself, cited separately above) has
    # to run and fail before this second one is reachable at all.
    "select_segments.py:2319-2405": [
        "def evaluate_open_review_loop_with_recovery(",
        "ONE construction, used by BOTH profiles that admit on a continuation:",
        "open_loop, why_not_self = evaluate_open_review_loop(",
        "return open_loop, why_not",
    ],
    # --- #455: the shared disclosure constant -------------------------------
    # The constant's own comment block, cited for the entry's claim that the
    # honest limit (SKILL.md/CHANGELOG stay hand-diffed prose) is stated in
    # the module rather than left implicit. Anchored on its first load-bearing
    # line and its last -- the whole block is the claim, so both ends matter.
    "select_segments.py:1594-1613": [
        "#   * the refusals where an operator could reasonably conclude the plugin",
        "# HONEST LIMIT: SKILL.md and the changelog are prose in other files and cannot",
        "# know which one they are looking at.",
    ],
    # The constant's definition. Anchored on the assignment and on its first
    # and last content lines -- the entry quotes its ending ("nobody has
    # re-reviewed") as the promised cost sentence, so that exact tail has to
    # still be there for the citation to mean what the entry says it means.
    "select_segments.py:1614-1623": [
        "FROM_STALLED_DISCLOSURE = (",
        "no OTHER select_segments.py claim invocation is ",
        "\"this claim's re-stamped draft carrying content that nobody has re-reviewed.\"",
    ],
    # --- #455: the two kernel leases ----------------------------------------
    # The module-level fd list the entry's "held for the rest of the
    # process's life" claim depends on -- nothing else in the file may ever
    # pop or close it, which is a claim this single-line citation cannot
    # itself verify (that is a grep, not an anchor), but the declaration
    # existing at all is the site the entry names.
    "select_segments.py:3583": ['_HELD_LOCK_FDS: "list[int]" = []'],
    # acquire_and_hold_lease(): anchored on the def, on the append that MUST
    # happen before the self-test (the entry's "parked before the self-test"
    # claim -- an append moved after the return would still pass a
    # presence-only check but fail this one, since the append anchor would no
    # longer precede anything meaningful), on the branch that turns an
    # enforced-flock self-test failure into a REFUSAL rather than a warning,
    # and on the success return.
    "select_segments.py:3732-3795": [
        'def acquire_and_hold_lease(lock_path: Path, what: str) -> "tuple[bool, str]":',
        "_HELD_LOCK_FDS.append(fd)",
        "if state == _PROBE_ACQUIRED:",
        'return True, ""',
    ],
    # acquire_from_stalled_leases(), the driver-lease half: anchored on the
    # def, on the --driver-lease-held branch (the entry's "vouched for" claim),
    # on the SAME probe call the entry says doubles as the unenforced-flock
    # check for this path, and on the refusal that carries the disclosure --
    # in evaluation order, since "the probe runs before the refusal it can
    # trigger" is part of what the entry claims.
    "select_segments.py:3798-3870": [
        'def acquire_from_stalled_leases(stalled_segs: list, dirs: dict, args) -> "dict[str, list]":',
        "if args.driver_lease_held:",
        "state, detail = _independent_lock_attempt(lock_path)",
        'return {seg: [f"{seg!r}: {problem}. {FROM_STALLED_DISCLOSURE}"] for seg in stalled_segs}',
    ],
    # The per-segment lease half of the same function: anchored on the loop
    # over every requested id (the entry's "for every requested id" claim)
    # and the per-segment lock-path construction, and on the function's own
    # return -- the entry's claim that a lock-acquisition failure refuses
    # only THAT id rather than the whole invocation depends on this being a
    # per-seg loop rather than an all-or-nothing gate.
    "select_segments.py:3895-3909": [
        "for seg in stalled_segs:",
        "seg_lock = codex_job_lock_path(seg, segments_dir)",
        "return failures",
    ],
    # The three disclosure use sites inside acquire_from_stalled_leases(),
    # cited standalone: the entry names all four in-file refusal sites
    # carrying FROM_STALLED_DISCLOSURE verbatim (2796 above is the first; the
    # three below are the driver-lease-held branch's, the standalone acquire
    # branch's, and the per-segment loop's, in file order).
    "select_segments.py:3870": [
        'return {seg: [f"{seg!r}: {problem}. {FROM_STALLED_DISCLOSURE}"] for seg in stalled_segs}',
    ],
    "select_segments.py:3881": ['f"{FROM_STALLED_DISCLOSURE}"'],
    "select_segments.py:3907": ['f"{FROM_STALLED_DISCLOSURE}"'],
    # --- #455: D3b, the late fix-now enforcement guard ---------------------
    # D3b itself: anchored on its own opening comment line, on the
    # `if stalled_requested:` guard (the entry's "when at least one
    # --from-stalled id is requested" claim, and the acceptance-criterion-4
    # concern that this must not fire on an ordinary run), on the actual
    # subset computation (`segs` minus claimed -- the entry's "every emitted
    # seg must ALSO be a subset of the claimed ids" claim, the direction D3
    # itself does not check), and on the fatal message's own framing of what
    # the operator must do about it.
    "select_segments.py:4347-4396": [
        "# D3b (#455): when a --from-stalled id is requested, the emitted segs",
        "if stalled_requested:",
        "unclaimed_emitted = sorted(seg for seg in segs if seg not in claim_requests)",
        "dispatch ONLY what it claims -- pass --only-segs naming exactly the",
    ],
    # --- #455: ordering, D5.2, D5.3 -----------------------------------------
    # The call site proving both leases are acquired strictly BEFORE any
    # admission gate runs: anchored on the comment stating that ordering
    # claim, on the acceptance-criterion-4 guard (an ordinary claim never
    # reaches this line), and on the call itself -- in that order, since the
    # entry's claim is specifically that the guard gates the call, not merely
    # that both exist somewhere in the function.
    "select_segments.py:4431-4446": [
        "# #455: BOTH kernel leases, taken here and HELD until this process",
        "if stalled_requested:",
        "lock_failures = acquire_from_stalled_leases(stalled_requested, dirs, args)",
    ],
    # D5.3's overlap rejection: anchored on the requested-set comprehension,
    # on the profile tuple that now includes CLAIM_PROFILE_FROM_STALLED (the
    # entry's whole claim is that --from-stalled joins this set), and on the
    # guard that only checks it when --allow-retranslate-converged is given.
    "select_segments.py:4398-4429": [
        "sentinel_bearing_requested = {",
        "if profile in (CLAIM_PROFILE_FROM_CONVERGED, CLAIM_PROFILE_FROM_STALLED)",
        "if args.allow_retranslate_converged:",
    ],
    # D5.2's clearing: anchored on the comprehension and on the same
    # now-two-profile tuple, and on the previously_converged filter it feeds --
    # the entry's claim is specifically that BOTH sentinel-bearing profiles
    # clear, not just --from-converged, which is exactly the tuple content.
    "select_segments.py:4647-4653": [
        "cleared = {",
        "in (CLAIM_PROFILE_FROM_CONVERGED, CLAIM_PROFILE_FROM_STALLED)",
        "previously_converged = [seg for seg in previously_converged if seg not in cleared]",
    ],
    # --- #455: the --help text, now interpolating the constant AND stating --
    # --- D3b's real reason instead of --from-cap's borrowed one -------------
    # --from-stalled's --help block: anchored on a field before the
    # interpolation, on the comment stating the interpolation is deliberate
    # (a restated-in-its-own-words version would drop this exact sentence),
    # on the interpolation itself, and on the trailing citation the constant
    # deliberately omits -- the entry's claim is specifically that codex_job.py
    # and the template line are named LOCALLY, around the constant, not
    # folded into it. Also the site of the D3b requirement-sentence rewrite:
    # this range is not just re-anchored, it is now cited a second time (in
    # the D3b section above) for that separate, later claim.
    "select_segments.py:4982-5013": [
        "default=None,",
        "# The disclosure sentences are INTERPOLATED from FROM_STALLED_DISCLOSURE,",
        "+ FROM_STALLED_DISCLOSURE",
        "(codex_job.py:1524), and the fix turn's byte-for-byte dispatch_token copy is \"",
    ],
    # --driver-lease-held's --help block: anchored on its own field, on the
    # comment stating it shares the SAME rule as --from-stalled's block above
    # (the entry's "the disclosure comes from the constant" claim), and on
    # the interpolation.
    "select_segments.py:5014-5041": [
        'action="store_true",',
        "# Same rule as --from-stalled above: the disclosure comes from the",
        "+ FROM_STALLED_DISCLOSURE",
    ],
    # --- #455: the driver-side lease machinery this profile reuses ---------
    # acquire_driver_lock(), pre-existing and UNCHANGED by #455 -- cited
    # because the entry's claim is that --from-stalled reuses this exact
    # mechanism rather than inventing a second one. Anchored on the def, the
    # acquire itself, and the refusal message a second driver hits.
    "segment_dispatch_driver.py:1124-1181": [
        'def acquire_driver_lock(durable_root: Path, session_id: "str | None" = None):',
        "fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)",
        'f"another driver already holds the project lease at {lock_path} "',
    ],
    # The driver's own unenforced-flock self-test, also pre-existing and
    # unchanged -- cited for the entry's claim that the driver WARNS here
    # where the selector's own mirrored self-test REFUSES. Anchored on the
    # comment naming it, the probe, and the warning message.
    "segment_dispatch_driver.py:1195-1250": [
        "# codex round-3: a runtime self-test that the lease this function just",
        "fcntl.flock(self_test_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)",
        'f"WARNING: the project lease at {lock_path} is NOT enforced by "',
    ],
    # The driver's own --driver-lease-held forwarding: anchored on the guard,
    # on the --from-stalled flag it forwards alongside, and on the flag
    # itself -- in order, since the entry's claim is that the lease flag is
    # forwarded ONLY when --from-stalled is also being forwarded, never on
    # its own.
    "segment_dispatch_driver.py:1499-1537": [
        "if from_stalled is not None:",
        'cmd += ["--from-stalled", from_stalled]',
        'cmd += ["--driver-lease-held"]',
    ],
    # The three-profile roster the driver now forwards. One anchor: the
    # entry's claim is the whole literal tuple, and there is no meaningful
    # sub-span to split it into.
    "segment_dispatch_driver.py:1584": [
        'KNOWN_CLAIM_PROFILES = ("from-cap", "from-converged", "from-stalled")'
    ],
    # --- #455: claim_record.py's profile-aware prose, schema unchanged -----
    # pre_claim_cache_key's doc: anchored on the field header, on the new
    # --from-stalled clause the entry says was added, and on its closing
    # line -- the entry's claim is specifically that the SAME absence is now
    # explained for two different profiles for two different reasons, so the
    # closing "different underlying reason" phrase has to survive.
    "claim_record.py:417-429": [
        "#   pre_claim_cache_key / cache_key_at_claim",
        "since #455, for --from-stalled as well",
        "capped one, for a different underlying reason.",
    ],
    # cache_key_note's doc: same shape, anchored on its own header and the
    # matching --from-stalled clause.
    "claim_record.py:467-474": [
        "#   cache_key_note",
        "fact the documented, expected shape for --from-cap and, since #455,",
        "reason.",
    ],
    # --- #455/codex-round: the PRODUCER of cache_key_note, not just its ------
    # --- schema doc -- the gap codex caught after the first CHANGELOG draft --
    # select_segments.py's own comment explaining the branch. NOT anchored on
    # a specific profile count: this comment was ITSELF corrected mid-release
    # after a second codex round found the first fix's own comment overclaimed
    # ("exactly two profiles reach here" -- false, --from-converged can too, on
    # an anomalous record). Anchored instead on the section header, on the
    # "ANY profile can reach this branch" framing the second fix landed on
    # (deliberately count-free), on the --from-converged anomaly explanation
    # (the entry's whole point in citing this a second time), and on the
    # persistence-consequence sentence -- an anchor on "TWO profiles" would
    # have faithfully pinned the false claim, which is the failure mode this
    # citation was rewritten specifically to stop being able to do again.
    "select_segments.py:2828-2853": [
        "# ---- cache-key diff -- REPORTING only, never gating (decision 5). ----",
        "ANY profile can reach this branch",
        "--from-converged reaches it too, and only an ANOMALOUS record gets it",
        "The note is persisted verbatim as the durable record's `cache_key_note`,",
    ],
    # The actual runtime string, cited standalone: the entry's claim is
    # specifically that this is no longer hard-coded to --from-cap, so the
    # anchor is the f-string's OWN interpolation syntax, not just its prose --
    # a static string that happened to still read "for --from-cap" would fail
    # this exact anchor, which a presence-only check on the sentence's other
    # words would not have caught. Unaffected by the comment-only second fix.
    "select_segments.py:2870-2875": [
        "else:",
        'f"no recorded cache_key on this fragment -- expected for --{profile} (cache_key is "',
    ],
    # --- #455: both bundle-membership tuples, cited for what they OMIT -----
    # PLUGIN_BUNDLE_MEMBERS: the entry's claim is that segment_dispatch_driver.py
    # is a member and select_segments.py deliberately is NOT -- so this
    # anchors the tuple opening and the one script that IS in it; the
    # absence of "select_segments.py" from this range is the point and
    # cannot itself be a positive anchor.
    "cache_key.py:143-171": [
        "PLUGIN_BUNDLE_MEMBERS = (",
        '"segment_dispatch_driver.py",',
    ],
    # ORCHESTRATION_BUNDLE_MEMBERS: the mirror-image claim -- select_segments.py
    # IS a member here.
    "scaffold_setup.py:63-78": [
        "ORCHESTRATION_BUNDLE_MEMBERS = (",
        '"select_segments.py",',
    ],
    # --- #455: the two facts the disclosure text points a reader at --------
    # codex_job.py's canonical promotion -- the exact line the --from-stalled
    # --help text and the profile's own design cite as the reason the
    # per-segment lock has to be held across the token re-stamp.
    "codex_job.py:1524": ["os.replace(self.attempt, self.canonical)"],
    # The fix turn's byte-for-byte dispatch_token copy -- what a wrong
    # --from-stalled assertion collides with. One anchor: the entry quotes
    # this exact instruction as the reason the disclosure sentence is
    # deliberately specific rather than "work may be lost".
    "mass-translate-wf.template.js:1284": [
        'lines.push("The draft also carries a dispatch_token top-level field -- copy its existing value byte for byte into your rewritten draft, unchanged; never invent, drop, or recompute it.");'
    ],
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
