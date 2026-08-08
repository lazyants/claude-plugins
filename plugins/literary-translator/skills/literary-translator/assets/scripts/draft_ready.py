#!/usr/bin/env python3
"""Readiness probe: has the codex translator finished WRITING the draft?

Distinct from validate_draft.py (which judges QUALITY). This script only
answers "did codex deliver a structurally complete draft file", used to POLL
between the async codex translate stage and the review stage so review never
starts on a missing/partial draft (and a Claude fix agent never ends up
authoring a missing translation from scratch -- codex must translate).

Fully generic across projects/languages/verse-policy modes -- no per-project
adapt point. EXCLUDED from `plugin_bundle_hash` (it never gates cache reuse);
covered instead by `orchestration_bundle_hash` -- non-gating for convergence
but gating for resume (folded into resume_setup.py's resume-integrity
digest).

Canonical paths (no target-language suffix, unlike the real reference
project's own `.ru.draft.json` naming) -- matches segpack.py/validate_draft.py
exactly (see references/ledger-and-resumability.md's canonical-path
invariants):
  draft:   ${durable_root}/segments/{seg}.draft.json
  segpack: ${durable_root}/segments/segpack_{seg}.json

Exit 0 = delivered: file exists, valid JSON, draft.schema.json/
segpack.schema.json container SHAPE is valid (right types, not just right
keys), the draft's own 'seg' field equals the requested seg (guards a
mislabeled/cross-wired draft -- #178), block/footnote/verse KEY SETS match
the segpack 1:1, AND (when --expect-token is given) the draft's own
dispatch_token field equals it exactly. Exit 1 = not ready yet, or the
segpack/draft itself is missing/invalid/schema-malformed (prints the reason
either way). Exit 2 = usage error.

--expect-token TOK (1.2.0 addition, optional): closes the resume-integrity
gap where a stale/straggler draft from a DIFFERENT run (or a pre-1.2.0
draft with no dispatch_token at all) would otherwise look READY. Omit for
the pre-1.2.0 behavior (no token check) -- backward compatible.

1.21.0 addition, #438: a token mismatch's refusal message additionally
consults this run's OWN claim record for the segment (best-effort, never
fatal -- see _claim_note() below). When one is present, the mismatch is not
a generic stale/straggler draft -- but it is also not automatically THIS
run's claim being lost: _claim_note() compares the draft's ACTUAL current
dispatch_token against this run's own record to tell the two reachable
cases apart. If the current token is missing or does not parse to a run
id, the claim was genuinely LOST after the fact, almost always by a fix
round that failed to copy the draft's dispatch_token byte for byte (see
mass-translate-wf.template.js's fixPrompt()), and the refusal points at
the re-claim that restores it -- which is idempotent, not a second
authorization. If the current token instead parses to a DIFFERENT run T,
_claim_note() does not assume what that means from the parse alone -- it
looks T's OWN claim record up before characterising anything, because a
run id sitting on a token is not evidence that run holds anything.
T holding a live claim record is the FOREIGN case proper, and as of round 6
it is no longer a single verdict: select_segments.py's own reclaim guard
(rewrite_draft_dispatch_token()) now admits or refuses a retry by comparing
the two records' `claimed_at`, parsed into instants, with strict `>` --
this note performs the IDENTICAL comparison so its wording cannot
contradict what the guard is about to do. This run's own claim being the
PROVABLY LATER one means T's token is stale relative to it -- most likely a
crash between claiming and stamping -- and the refusal names the retry as
the remedy, not a superseded claim. T's claim being the PROVABLY LATER one
is the original SUPERSEDED case: T claimed the segment legitimately and the
refusal does not send the operator to reclaim it. The two `claimed_at`
values being IDENTICAL is a TIE neither side provably wins, reported as its
own case -- and since a same-run retry reuses this run's existing claim
record untouched, the refusal does not advise waiting and retrying, because
waiting can never change a tie. Either `claimed_at` missing, malformed, or
read off a record that is not itself CLAIM_PRESENT leaves the comparison
unable to run at all, and the refusal says ownership could not be
established rather than guessing which of the above applies. T holding NO
claim record at all means nobody currently holds the segment despite the
foreign-looking token -- closer to the LOST case in remedy, and the
refusal does not turn the operator away from the fix that will actually
work. T's record being
unreadable, or T's parsed run id not itself being safe to look up, means
ownership could not be determined at all, and the refusal says so instead
of guessing either way. Either way the refusal names the claim's profile
and claim time instead of leaving the operator to guess, for both this
run's record and, when reachable, T's.

Exactly TWO states degrade silently to the original pre-#438 message:
CLAIM_ABSENT (the ordinary stale/straggler case the plain message already
describes correctly) and claim_record.py not being co-located, which every
caller that predates #438 still is. An unreadable or ambiguous record, a
run component that is not usable as a claim-path component, a CLAIM_PRESENT
record whose LOST-vs-FOREIGN clause differs by case -- and, within FOREIGN
itself, further by what T's own record shows (see above) -- and any
unexpected failure of the lookup itself each get their OWN clause -- see
_claim_note()'s own docstring for why an anomaly must never be reported
with the same silence as an absence.

Usage: python3 draft_ready.py SEG [--expect-token TOK] [--durable-root PATH]

Self-anchoring by default: this script always lives at
${durable_root}/scripts/draft_ready.py and derives durable_root from its own
path -- it never assumes cwd. #412 prerequisite: an explicit
`--durable-root PATH` overrides this, REPLACING the self-anchored root
entirely for DATA (segments/) -- both draft_path()/segpack_path() already
take `segments_dir` as an explicit parameter, so this only changes what
main() passes in, never what a given set of on-disk inputs resolves to.
Omitting the flag reproduces today's self-anchored behavior byte-for-byte.

This script is a LEAF: it shells out to nothing, so there is no
--plugin-root concern here at all, unlike select_segments.py/ledger_merge.py/
resume_setup.py/review_ready.py, each of which resolves at least one sibling
script and so needs that second, independent override. Adding --plugin-root
here regardless would be a flag accepted and never read -- see
references/gotchas.md §4 for the full two-flag rationale this script
deliberately does NOT need.
"""
import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

# Self-anchored by default: this script always lives at
# ${durable_root}/scripts/draft_ready.py, so parents[1] is the durable root.
# Never assumes cwd. These module-level constants are the fallback used
# whenever --durable-root is omitted (see resolve_dirs() below).
DURABLE_ROOT = Path(__file__).resolve().parents[1]
SEGMENTS_DIR = DURABLE_ROOT / "segments"


def resolve_dirs(durable_root_str):
    """#412 prerequisite: `durable_root_str` governs DATA (segments/) --
    rebuilt from that root when given, self-anchored otherwise. This script
    is a LEAF (see module docstring), so there is only ever the one root to
    resolve -- no separate --plugin-root concern. `durable_root_str=None`
    reproduces today's exact self-anchored values."""
    if durable_root_str is None:
        return {"durable_root": DURABLE_ROOT, "segments_dir": SEGMENTS_DIR}
    root = Path(durable_root_str).resolve()
    return {"durable_root": root, "segments_dir": root / "segments"}

# Canonical segment-id safety contract. A seg id is either an ordinary body
# id (e.g. "seg01", "seg05_blocked_regen", "segAnchor") or a translate-decision
# FRONTBACK:{id} unit (e.g. "FRONTBACK:fm01"). It is spliced into filesystem
# paths and workflow shell commands, so it MUST be a path- and shell-safe
# allowlist. Keep this identical across every consuming script.
# NOTE: re.fullmatch (NOT re.match + "$") -- in Python "$" also matches just
# before a trailing newline, so re.match(r"...$", "seg01\n") would WRONGLY pass.
_SEG_ID_RE = re.compile(r"(?:FRONTBACK:)?[A-Za-z0-9_]+")


def validate_seg(seg):
    """Return an error string if `seg` is not a path/shell-safe segment id,
    else None. Allows ONLY [A-Za-z0-9_] with an optional literal 'FRONTBACK:'
    prefix -- rejecting empties, path separators, '..', absolute paths, and
    every shell metacharacter."""
    if not isinstance(seg, str) or not seg:
        return "segment id must be a non-empty string."
    if not _SEG_ID_RE.fullmatch(seg):
        return (
            "segment id must match (FRONTBACK:)?[A-Za-z0-9_]+ (no path "
            f"separators, '..', or shell metacharacters); got {seg!r}."
        )
    return None


def draft_path(seg: str, segments_dir: Path = SEGMENTS_DIR) -> Path:
    return segments_dir / f"{seg}.draft.json"


def segpack_path(seg: str, segments_dir: Path = SEGMENTS_DIR) -> Path:
    return segments_dir / f"segpack_{seg}.json"


# ---------------------------------------------------------------------------
# Hand-rolled structural self-checks -- no jsonschema dependency (matches
# validate_draft.py's own check_draft_structure and the real source
# project's dependency-free scripts). These exist so a schema-invalid draft
# or segpack (wrong container type, missing required key) is refused with a
# named reason instead of silently degrading into an empty/matching
# container via .get(key, {})/.get(key, []) -- which is exactly how a
# schema-incomplete draft or segpack used to slip through as READY.
# ---------------------------------------------------------------------------

# (key, container_type, item_type, description) -- draft.schema.json's own
# container shapes.
_DRAFT_CONTAINER_SPECS = [
    ("blocks", dict, str, "object of string values"),
    ("footnotes", dict, str, "object of string values"),
    ("verses", dict, dict, "object of object values"),
    ("names", list, dict, "array of objects"),
    ("notes", list, str, "array of strings"),
]
_DRAFT_REQUIRED_KEYS = ["seg"] + [spec[0] for spec in _DRAFT_CONTAINER_SPECS]


def check_draft_structure(draft) -> list:
    """Structural self-check against draft.schema.json's container shape.
    Returns a list of error strings (empty = shape-valid)."""
    if not isinstance(draft, dict):
        return [f"draft.schema.json: draft root must be an object, got {type(draft).__name__}"]

    errs = [
        f"draft.schema.json: missing required key {k!r}"
        for k in _DRAFT_REQUIRED_KEYS if k not in draft
    ]
    if errs:
        # Can't safely type-check keys that aren't even present.
        return errs

    if not isinstance(draft["seg"], str):
        errs.append("draft.schema.json: 'seg' must be a string")

    for key, container_type, item_type, desc in _DRAFT_CONTAINER_SPECS:
        value = draft[key]
        if not isinstance(value, container_type):
            errs.append(f"draft.schema.json: {key!r} must be an {desc}")
            continue
        items = value.values() if container_type is dict else value
        if not all(isinstance(item, item_type) for item in items):
            errs.append(f"draft.schema.json: {key!r} must be an {desc}")

    return errs


# (key, item_key, item_key_type, description) -- segpack.schema.json's own
# blocks[]/footnotes[]/verses[] array shapes, restricted to the fields this
# script's key-set comparison actually reads (id/n/vid). Full segpack shape
# validation (title/kind/word_count/canon_names/etc.) is segpack.py's own
# job at write time, not this readiness probe's.
_SEGPACK_ARRAY_SPECS = [
    ("blocks", "id", str, "array of objects each with a string 'id'"),
    ("footnotes", "n", int, "array of objects each with an integer 'n'"),
    ("verses", "vid", str, "array of objects each with a string 'vid'"),
]


def check_segpack_structure(segpack) -> list:
    """Structural self-check against segpack.schema.json's blocks/footnotes/
    verses array shape (the three containers this script's readiness
    comparison reads). Returns a list of error strings (empty = shape-valid)."""
    if not isinstance(segpack, dict):
        return [f"segpack.schema.json: segpack root must be an object, got {type(segpack).__name__}"]

    errs = [
        f"segpack.schema.json: missing required key {k!r}"
        for k, _, _, _ in _SEGPACK_ARRAY_SPECS if k not in segpack
    ]
    if errs:
        return errs

    for key, item_key, item_key_type, desc in _SEGPACK_ARRAY_SPECS:
        value = segpack[key]
        if not isinstance(value, list):
            errs.append(f"segpack.schema.json: {key!r} must be an {desc}")
            continue
        for item in value:
            if not isinstance(item, dict) or item_key not in item or not isinstance(item[item_key], item_key_type):
                errs.append(f"segpack.schema.json: {key!r} must be an {desc}")
                break

    return errs


# ---------------------------------------------------------------------------
# #438 -- claim-aware enrichment of the --expect-token mismatch refusal.
# Deliberately best-effort and NEVER fatal: this script stays a LEAF for
# every consumer that predates #438 (it shells out to nothing, and every
# import here is optional). claim_record.py is the SHARED module #438
# introduced (its own docstring documents the "flat sibling-import idiom
# already used for cache_key.py" -- this is the third reader it names).
# ---------------------------------------------------------------------------

def _claim_run_id(token):
    """The RUN_ID out of a dispatch token shaped <RUN_ID>:<seg>[:r<label>],
    split on the FIRST colon only -- mirrors select_segments.py's own
    draft_run_id() derivation byte for byte (duplicated, not imported: this
    script is a LEAF and does not resolve sibling SCRIPTS, only the shared
    claim_record MODULE below). A RUN_ID can never contain ':'
    (resume_setup.py's validate_run_id() rejects it), while a seg id can
    (FRONTBACK:errata_02 is a real, shipped shape), so splitting on the
    first ':' is the only correct partition. Returns None on anything
    malformed -- this is purely a message-enrichment lookup key, never a
    security decision. Used on --expect-token in main() (a None there means
    the note stays empty, since there is no run id to look a claim up
    under) and, since #438's LOST-vs-FOREIGN split, on the draft's OWN
    current dispatch_token inside _claim_note() (a None there instead means
    the current token is unparseable, which is itself the LOST signal --
    see _claim_note()'s docstring)."""
    if not isinstance(token, str):
        return None
    run_id, sep, rest = token.partition(":")
    if not sep or not run_id or not rest:
        return None
    return run_id


def _claim_instant(value):
    """Parse a claim record's `claimed_at` field into a naive UTC
    `datetime`, or None when it cannot be trusted. Mirrors the STRPTIME
    parse inside select_segments.py's own `_claim_record_claimed_at()` --
    the function rewrite_draft_dispatch_token()'s #438 round 5/6 ownership-
    age check uses on BOTH sides of its own comparison -- against
    `_claim_now_iso8601()`'s one and only writer format: second-resolution
    UTC with a literal trailing 'Z', never a numeric offset or a timezone
    name. NOT imported: this script is a LEAF and does not resolve sibling
    SCRIPTS (see this file's module docstring), only the shared
    claim_record MODULE -- but duplicated
    deliberately, because _claim_note() below has to agree with the
    selector's own guard on which of two claims is later, and a lexical
    string compare instead of a parsed one lets a malformed value
    impersonate the newest possible claimant (e.g. the string "z" sorts
    after any real timestamp). Anything but a non-empty string, or a string
    that does not match that GRAMMAR over its whole length, returns None --
    the same signal a missing field produces, so a caller comparing two of
    these results with `is not None` treats "malformed" and "absent"
    identically, which is the safe direction. Grammar, not a byte-for-byte
    match: strptime's numeric fields accept unpadded values, so
    "2026-1-2T0:0:0Z" parses here too. Both parsers agree on that, which is
    what matters -- a value either becomes a real instant on BOTH sides or
    on neither, so this note can never describe an outcome the selector's
    own guard would not produce."""
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return None


def _claim_note(run_id, seg, durable_root, current_token):
    """Best-effort clause to append to a dispatch_token-mismatch refusal
    when this run's OWN claim record explains it. NEVER fatal and never
    able to change this probe's exit code: enriching a message must not be
    able to crash the check it enriches, so every failure below becomes
    TEXT rather than an exception -- and only the two states listed further
    down (claim_record.py not co-located; CLAIM_ABSENT) return "" and leave
    the plain pre-#438 message standing unchanged.

    CLAIM_PRESENT is the only state worth a positive claim, but it is NOT
    a single verdict: this run's own claim record being present says only
    that THIS run legitimately claimed `seg` at some point -- never, by
    itself, that the CURRENT mismatch is that claim being lost. `seg`'s
    own current dispatch_token can also have moved on legitimately, if a
    later run claimed it after this one did. `current_token` -- the
    draft's ACTUAL current dispatch_token, i.e. the exact value that just
    failed to equal --expect-token back in main() -- is what tells the two
    apart, by parsing it with _claim_run_id() the same way --expect-token
    itself was parsed to get `run_id`:

      * `current_token` missing or unparseable (`_claim_run_id() is None`):
        nothing legible has claimed the segment since, so the token was
        simply not preserved. Genuinely LOST -- most likely a fix round
        that did not copy dispatch_token byte for byte (see
        mass-translate-wf.template.js's fixPrompt()) -- and the clause
        names the re-claim that restores it.
      * `current_token` parses to a run id T that is NOT this run's own
        `run_id`: FOREIGN-shaped, but this clause does not stop at parsing
        -- it looks T's OWN claim record up (runs/<T>/.claimed.<seg>, the
        identical claim_record.read_claim_record() call used for this run's
        own record above) before characterising anything, because a parsed
        run id says only that some other run's id is on the token, never
        that the run actually holds a claim. Three sub-outcomes:

          - T holds a live claim record (CLAIM_PRESENT): round 6 replaced
            the single "superseded" verdict with the same comparison
            select_segments.py's own reclaim guard performs --
            rewrite_draft_dispatch_token() now admits or refuses a retry
            by comparing the two records' `claimed_at`, parsed into
            instants via _claim_instant() above, with strict `>`. This
            clause performs the IDENTICAL comparison rather than a
            weaker or independent one, because a note that disagreed with
            the guard it is describing would be actively misleading.
            Four outcomes, evaluated against `claimed_at` (this run's own,
            already read above) and `f_claimed_at` (T's, read here):

              * both parse and THIS run's is strictly LATER: T's token is
                stale relative to this run's own claim -- most likely a
                crash between claiming and stamping (the exact D9
                crash-recovery case #438 restores) -- and the clause names
                the retry as the remedy. This run's record is explicitly
                NOT called superseded.
              * both parse and T's is strictly LATER: the original
                SUPERSEDED wording -- T claimed the segment legitimately,
                this run's own record above is a superseded authorization,
                and the clause must NOT send the operator to reclaim it.
              * both parse and are IDENTICAL (a second-resolution tie):
                neither side is provably later, so the clause says so
                explicitly -- and, because a same-run retry reuses this
                run's EXISTING claim record (whose `claimed_at` therefore
                never changes), it does NOT tell the operator to wait and
                retry; a tie needs a different run identity or a manual
                ownership decision.
              * either side fails to parse (missing, not a string, or not
                read off a CLAIM_PRESENT record): the comparison cannot
                run at all, and the clause says ownership could not be
                established rather than asserting any of the above.

            In every one of the four the selector's own guard remains
            authoritative on whether a reclaim is actually admitted, but
            THREE of the four do name the decision it will reach, because
            they read the same records through the same parse and the same
            strict `>` -- hedging there would be a hedge about arithmetic.
            Later ADMITS, older REFUSES, and a tie REFUSES (and that tie
            branch additionally says the refusal is permanent, which is a
            claim about the selector's behaviour, not merely about the
            evidence). Only the UNPARSEABLE branch asserts no outcome: with
            an unusable ordering key on either side there is nothing to
            compare, so it says only that this run's claim is not provably
            the later one.
          - T holds NO claim record (CLAIM_ABSENT): nobody currently holds
            the segment despite the foreign-looking token -- closer to the
            LOST case in REMEDY (re-claiming under this run's own --run-id
            is the correct recovery and is not refused merely because the
            token currently names T) even though it is reached through the
            FOREIGN branch.
          - T's record is unreadable (CLAIM_AMBIGUOUS), or T's parsed run
            id is not itself safe to use as a claim-path component: which
            run owns the segment could not be determined at all. The
            clause says so and defers to the selector's own guard rather
            than asserting either LOST or FOREIGN.
      * `current_token` parses to run id `run_id` ITSELF, differing only
        in its trailing `:seg[:r<label>]` component: still this run's own
        token, so it is treated as the LOST case above -- re-claiming
        under this run's own id is the correct, idempotent recovery, not
        a reclaim from anyone else.

    CLAIM_AMBIGUOUS still gets a clause -- silently dropping a genuine
    anomaly (an unreadable record) would hide it from the one operator
    positioned to investigate -- but per claim_record.py's own documented
    discipline it is reported as unreadable, never asserted as a claim.
    CLAIM_ABSENT says nothing: that is the ordinary pre-#438 stale/
    straggler case and the plain message already covers it correctly.

    NOTHING HERE RETURNS "" FOR AN ANOMALY ANY MORE. The only two states
    that produce an empty clause are the two that genuinely have nothing to
    say: claim_record.py is not co-located (a pre-#438 deployment, which
    cannot have claims at all), and CLAIM_ABSENT. Every other outcome --
    an unusable run component, an unreadable record, a LOST claim, a
    FOREIGN claim, an unexpected failure of the lookup itself -- gets a
    clause of its own. An anomaly that returned "" was reported identically
    to "no claim exists", which is the same collapse of "cannot tell" into
    "nothing there" that claim_record.py's three-state predicate exists to
    prevent one layer down; reproducing it in the layer that PRINTS the
    result would hide the anomaly from the one operator positioned to act
    on it.
    """
    try:
        import claim_record  # sibling module, #438 -- optional at runtime
    except ImportError:
        return ""

    # VALIDATED BEFORE THE PATH IS BUILT, not after. `run_id` was partitioned
    # out of --expect-token by _claim_run_id() above, and --expect-token is a
    # free-form caller-supplied string with no schema `pattern` anywhere
    # behind it -- so `--expect-token /tmp/x:seg_001` used to build
    # /tmp/x/.claimed.seg_001, read whatever regular file happened to sit
    # there, and echo its `profile` and `claimed_at` to stdout as if they
    # described this run's own claim. claim_record.claimed_path() now refuses
    # such a value by raising, but a refusal reached through an exception is
    # not the same as never asking: checking here keeps the ANSWER specific
    # (this token's run component is unusable) instead of collapsing into the
    # generic lookup-failed clause below. Refusing an odd value costs nothing
    # -- this note is message enrichment and never a decision.
    problem = claim_record.validate_run_id(run_id)
    if problem is not None:
        return (
            f" -- NOTE: no claim record was consulted, because the expected "
            f"token's own run component {run_id!r} is not usable as one "
            f"({problem}) -- nothing was read from disk under that name"
        )

    try:
        runs_dir = durable_root / "runs"
        path = claim_record.claimed_path(run_id, seg, runs_dir)
        state, payload, detail = claim_record.read_claim_record(path)
    except Exception as exc:
        # THE NARROWING IS IN THE RESULT, NOT IN THE CLASS, and that is a
        # decision rather than an oversight. Narrowing to (OSError, ValueError)
        # and letting anything else propagate was the alternative, and it
        # breaks this helper's never-fatal contract (see this docstring's
        # opening) for no gain: an escaping exception replaces the readiness
        # probe's own "[seg] not ready: ..." line with a traceback at the SAME
        # exit code 1, so the poller reading that line loses its only
        # diagnostic and gains nothing -- the enrichment lookup would have
        # taken down the check it was enriching. What actually changed is that
        # an unexpected failure is now REPORTED instead of being mapped onto
        # the same "" that CLAIM_ABSENT returns, which is the collapse the
        # finding was about. The
        # enumerated shapes are already handled without reaching here --
        # classify_claim_record() absorbs every lstat OSError into
        # CLAIM_AMBIGUOUS and read_claim_record() absorbs the read/parse
        # failures, and claimed_path()'s ValueError is pre-empted by the
        # validation above -- so reaching this line at all is itself the
        # anomaly worth naming.
        return (
            f" -- WARNING: this run's claim state for {seg!r} could not be "
            f"determined ({exc!r}); treated as NOT claimed (the safe "
            f"direction), never assumed claimed"
        )

    if state == claim_record.CLAIM_PRESENT:
        profile = payload.get("profile") if isinstance(payload, dict) else None
        claimed_at = payload.get("claimed_at") if isinstance(payload, dict) else None
        actual_run_id = _claim_run_id(current_token)
        if actual_run_id is not None and actual_run_id != run_id:
            # FOREIGN-shaped: the draft's current token names some OTHER
            # run T. Parsing a run id off the token is not evidence T holds
            # anything -- T's OWN claim record has to be looked up before
            # this clause may say what happened, the same way this run's
            # own record was looked up above. See this function's own
            # docstring for the three reachable sub-outcomes.
            problem = claim_record.validate_run_id(actual_run_id)
            if problem is not None:
                return (
                    f" -- a claim record for run {run_id!r} IS present for "
                    f"this segment (profile={profile!r}, "
                    f"claimed_at={claimed_at!r}), and the draft's CURRENT "
                    f"dispatch_token now names a DIFFERENT run, "
                    f"{actual_run_id!r} -- but that name is not usable as a "
                    f"run id ({problem}), so no claim record could be "
                    f"looked up for it and which run actually owns {seg!r} "
                    f"now could not be determined. Do not assume either "
                    f"this run's record above or the foreign token is still "
                    f"in force; the selector's own guard decides, not this "
                    f"note. Resolve {seg!r}'s ownership by hand before "
                    f"reclaiming it."
                )
            try:
                foreign_path = claim_record.claimed_path(actual_run_id, seg, runs_dir)
                foreign_state, foreign_payload, foreign_detail = claim_record.read_claim_record(foreign_path)
            except Exception as exc:
                return (
                    f" -- a claim record for run {run_id!r} IS present for "
                    f"this segment (profile={profile!r}, "
                    f"claimed_at={claimed_at!r}), and the draft's CURRENT "
                    f"dispatch_token now names a DIFFERENT run, "
                    f"{actual_run_id!r} -- but that run's own claim record "
                    f"could not even be looked up ({exc!r}), so which run "
                    f"actually owns {seg!r} now could not be determined. "
                    f"Treated as UNDETERMINED, never assumed either claimed "
                    f"or released. Resolve {seg!r}'s ownership by hand "
                    f"before reclaiming it."
                )
            if foreign_state == claim_record.CLAIM_PRESENT:
                f_profile = foreign_payload.get("profile") if isinstance(foreign_payload, dict) else None
                f_claimed_at = foreign_payload.get("claimed_at") if isinstance(foreign_payload, dict) else None
                # #438 round 6: T holding a live record is no longer a
                # single verdict -- see this function's own docstring for
                # the four-way split. this_instant/foreign_instant mirror
                # EXACTLY what select_segments.py's own reclaim guard
                # compares (rewrite_draft_dispatch_token(), strict `>` over
                # _claim_record_claimed_at()'s parsed instants), via
                # _claim_instant() above -- so this note cannot describe an
                # outcome the guard itself would not produce.
                this_instant = _claim_instant(claimed_at)
                foreign_instant = _claim_instant(f_claimed_at)
                if this_instant is None or foreign_instant is None:
                    # Checked FIRST, as a guard clause: both sides must parse
                    # before anything can be compared at all, so the case that
                    # cannot be decided is disposed of before the three that
                    # can. At least one side's claimed_at could not be trusted
                    # -- missing, not a string, or not matching the grammar the
                    # field's one writer emits (_claim_now_iso8601(), which
                    # lives in select_segments.py; claim_record.py stores
                    # claimed_at as an opaque caller-supplied value and does not
                    # own its format) -- so which claim is later could not be
                    # established at all. The side at fault is named ONLY when
                    # it is the reason the comparison could not be run,
                    # mirroring select_segments.py's own this_claim_note.
                    unparseable_side = (
                        f"this run's own claim record's claimed_at ({claimed_at!r})"
                        if this_instant is None
                        else f"{actual_run_id!r}'s claim record's claimed_at ({f_claimed_at!r})"
                    )
                    return (
                        f" -- a claim record for run {run_id!r} IS present for this "
                        f"segment (profile={profile!r}, claimed_at={claimed_at!r}), "
                        f"and the draft's CURRENT dispatch_token now names a "
                        f"DIFFERENT run, {actual_run_id!r}, which itself holds a live "
                        f"claim record for {seg!r} (profile={f_profile!r}, "
                        f"claimed_at={f_claimed_at!r}) -- but {unparseable_side} does not "
                        f"parse as a timestamp, so which claim is later could not be "
                        f"established. The selector's own guard refuses whenever it "
                        f"cannot show this run's own claim is provably the later one "
                        f"(the same safe direction it takes on a tie), so treat this "
                        f"as UNDETERMINED -- neither a guaranteed refusal nor a "
                        f"green light. Resolve {seg!r}'s ownership by hand before "
                        f"reclaiming it."
                    )
                if this_instant > foreign_instant:
                    # THIS run's own claim outranks T's. T's token is
                    # stale relative to it, not the other way around --
                    # do not call this run's record superseded.
                    return (
                        f" -- a claim record for run {run_id!r} IS present for "
                        f"this segment (profile={profile!r}, "
                        f"claimed_at={claimed_at!r}), and the draft's CURRENT "
                        f"dispatch_token now names a DIFFERENT run, "
                        f"{actual_run_id!r}, which itself holds a live claim "
                        f"record for {seg!r} (profile={f_profile!r}, "
                        f"claimed_at={f_claimed_at!r}) -- but THIS run's own "
                        f"claim is the LATER of the two. {actual_run_id!r}'s "
                        f"token is stale relative to this run's own claim, "
                        f"most likely because this run claimed {seg!r} and "
                        f"then crashed before stamping the draft's "
                        f"dispatch_token -- the crash-between-claim-and-stamp "
                        f"recovery #438 restores. This run's record above is "
                        f"NOT superseded: re-running select_segments.py's "
                        f"claim step for {seg} under THIS run's --run-id "
                        f"{run_id!r} is the remedy, and the selector's own "
                        f"guard admits it on the strength of the claim ages "
                        f"above."
                    )
                if this_instant < foreign_instant:
                    # T's claim outranks this run's -- the original
                    # SUPERSEDED case, but NOT round 5's wording, and the
                    # difference is the whole point of this branch. Round 5
                    # ended it with "this note has no way to establish from
                    # here which way that resolves ... neither a guaranteed
                    # refusal nor a green light". Round 6 DELETES that
                    # hedge, because the comparison three lines up DOES
                    # establish it -- same records, same parse, same strict
                    # `>` the selector's own guard uses. The clause now
                    # STATES the refusal rather than disclaiming knowledge
                    # of it. (Said explicitly because a comment claiming
                    # the wording was unchanged sent a reader looking for a
                    # caution that is no longer there.)
                    return (
                        f" -- a claim record for run {run_id!r} IS present for "
                        f"this segment (profile={profile!r}, "
                        f"claimed_at={claimed_at!r}), but the draft's CURRENT "
                        f"dispatch_token now names a DIFFERENT run, "
                        f"{actual_run_id!r}, which itself holds a live claim "
                        f"record for {seg!r} (profile={f_profile!r}, "
                        f"claimed_at={f_claimed_at!r}): that run has since "
                        f"claimed this segment, and this run's own record above "
                        f"is a SUPERSEDED authorization -- nothing was "
                        f"overwritten or dropped; the segment was claimed out "
                        f"from under this run by {actual_run_id!r}. Re-running "
                        f"select_segments.py's claim step for {seg} under THIS "
                        f"run's --run-id {run_id!r} would take the segment back "
                        f"from {actual_run_id!r} and is NOT the fix -- it is the "
                        f"unauthorized reclaim #438 exists to gate: the "
                        f"selector's guard refuses it whenever {actual_run_id!r}'s "
                        f"claim outranks this run's, and both claim ages are "
                        f"printed above, so that is settled here rather than "
                        f"guessed at -- {actual_run_id!r} claimed later, and the "
                        f"reclaim WILL be refused. Work under {actual_run_id!r} "
                        f"instead, or make a deliberate decision to take "
                        f"ownership back if that is genuinely intended: this "
                        f"note does not make that call for you."
                    )
                # IDENTICAL claimed_at -- a second-resolution TIE that
                # neither side provably wins. The selector refuses a
                # tie the same safe direction as an older claim, but
                # unlike an older claim a tie is PERMANENT for this
                # run: a same-run retry reuses the SAME claim record
                # (claim_record.py's write is exclusive), so its
                # claimed_at can never change and waiting cannot break
                # the tie -- do not advise a retry here.
                return (
                    f" -- a claim record for run {run_id!r} IS present for "
                    f"this segment (profile={profile!r}, "
                    f"claimed_at={claimed_at!r}), and the draft's CURRENT "
                    f"dispatch_token now names a DIFFERENT run, "
                    f"{actual_run_id!r}, which itself holds a live claim "
                    f"record for {seg!r} (profile={f_profile!r}, "
                    f"claimed_at={f_claimed_at!r}) -- the two records carry "
                    f"the IDENTICAL claimed_at, a TIE neither side provably "
                    f"wins. The selector's own guard refuses a tie the same "
                    f"safe direction as an older claim, and that refusal is "
                    f"PERMANENT for this run: a same-run retry reuses this "
                    f"run's EXISTING claim record, so its claimed_at can "
                    f"never change and waiting cannot break the tie. Do NOT "
                    f"re-run the claim step expecting a different outcome; "
                    f"{seg!r}'s ownership needs either a fresh run under a "
                    f"different --run-id, or a deliberate manual decision, "
                    f"before either side may proceed."
                )
            if foreign_state == claim_record.CLAIM_ABSENT:
                return (
                    f" -- a claim record for run {run_id!r} IS present for "
                    f"this segment (profile={profile!r}, "
                    f"claimed_at={claimed_at!r}), and the draft's CURRENT "
                    f"dispatch_token now names a DIFFERENT run, "
                    f"{actual_run_id!r} -- but {actual_run_id!r} holds NO "
                    f"claim record for {seg!r}: nobody currently holds this "
                    f"segment, so despite the foreign-looking token this is "
                    f"closer to the LOST case than to a legitimate foreign "
                    f"claim. The token was most likely rewritten (by a fix "
                    f"round, or by hand) without going through "
                    f"select_segments.py's claim step at all. Re-running "
                    f"that claim step for {seg} under THIS run's --run-id "
                    f"{run_id!r} is the correct recovery and is admitted on "
                    f"the strength of the record above; it is not refused "
                    f"merely because the token currently names "
                    f"{actual_run_id!r}."
                )
            # CLAIM_AMBIGUOUS -- T's own record exists but could not be read.
            f_detail = f" ({foreign_detail})" if foreign_detail else ""
            return (
                f" -- a claim record for run {run_id!r} IS present for this "
                f"segment (profile={profile!r}, claimed_at={claimed_at!r}), "
                f"and the draft's CURRENT dispatch_token now names a "
                f"DIFFERENT run, {actual_run_id!r} -- but "
                f"{actual_run_id!r}'s own claim record is "
                f"unreadable{f_detail}, so which run actually owns {seg!r} "
                f"now could not be determined. Do not assume either record "
                f"is authoritative; the selector refuses rather than guess "
                f"when it cannot tell either, so resolve {seg!r}'s "
                f"ownership by hand before reclaiming it."
            )
        return (
            f" -- a claim record for run {run_id!r} IS present for this "
            f"segment (profile={profile!r}, claimed_at={claimed_at!r}): the "
            f"claim was LOST, not merely absent -- something after the "
            f"claim (most likely a fix round) overwrote or dropped the "
            f"draft's dispatch_token instead of preserving it byte for "
            f"byte. To restore it, re-run select_segments.py's claim step "
            f"for {seg} under that same profile with --run-id {run_id!r}: "
            f"the re-claim is admitted on the strength of the record above, "
            f"so the draft's missing dispatch_token is not what blocks it, "
            f"and it re-stamps the token. Re-claiming a segment this run "
            f"already holds a record for is idempotent and is not a second "
            f"authorization."
        )
    if state == claim_record.CLAIM_AMBIGUOUS:
        return (
            f" -- WARNING: this run's claim record for {seg!r} is "
            f"unreadable ({detail}); treated as NOT claimed (the safe "
            f"direction), never assumed claimed"
        )
    return ""


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Readiness probe for segments/{seg}.draft.json -- see this file's own module docstring.",
    )
    parser.add_argument("seg", help="Segment identifier.")
    parser.add_argument(
        "--expect-token",
        default=None,
        metavar="TOK",
        help=(
            "When given, READY additionally requires the draft's own "
            "dispatch_token field to equal TOK exactly (RUN_ID:seg form) -- "
            "closes a stale/straggler-draft-from-an-old-run gap. Omit for "
            "the pre-1.2.0 behavior (no token check)."
        ),
    )
    parser.add_argument(
        "--candidate-file",
        default=None,
        metavar="PATH",
        help=(
            "When given, read the draft from PATH instead of the canonical "
            "segments/{seg}.draft.json -- lets the W5 codex_job.py driver "
            "FULLY validate an isolated attempt artifact BEFORE promoting it "
            "to canonical (1.4.7, #198). The segpack is STILL read from its "
            "canonical path, and every existing check (schema shape, seg "
            "field == seg, key sets, --expect-token) runs against the "
            "candidate. Omit for today's canonical-path behavior."
        ),
    )
    parser.add_argument(
        "--durable-root",
        default=None,
        metavar="PATH",
        help=(
            "#412 prerequisite: use PATH as the durable root instead of "
            "this script's own self-anchored location. Optional; omit for "
            "today's self-anchored behavior. This script is a LEAF (shells "
            "out to nothing), so there is no companion --plugin-root flag."
        ),
    )
    return parser


def main() -> None:
    args = build_arg_parser().parse_args()
    seg = args.seg
    _seg_err = validate_seg(seg)
    if _seg_err:
        print(f"Error: {_seg_err}", file=sys.stderr)
        sys.exit(2)

    dirs = resolve_dirs(args.durable_root)
    segments_dir = dirs["segments_dir"]

    dp = Path(args.candidate_file) if args.candidate_file else draft_path(seg, segments_dir)
    if not dp.exists() or dp.stat().st_size == 0:
        print(f"[{seg}] not ready: draft file absent/empty ({dp})")
        sys.exit(1)
    try:
        draft = json.loads(dp.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[{seg}] not ready: draft not valid JSON ({e})")
        sys.exit(1)

    draft_errs = check_draft_structure(draft)
    if draft_errs:
        print(f"[{seg}] not ready: draft not schema-valid ({'; '.join(draft_errs)})")
        sys.exit(1)

    # #178: check_draft_structure only type-checks 'seg' (must be a str); it
    # never compares it to the requested seg. A mislabeled/cross-wired draft
    # would otherwise read READY. Struct check above guarantees draft["seg"]
    # is a str here.
    if draft["seg"] != seg:
        print(
            f"[{seg}] not ready: draft 'seg' is {draft['seg']!r}, expected {seg!r} "
            f"(mislabeled/cross-wired draft)"
        )
        sys.exit(1)

    sp = segpack_path(seg, segments_dir)
    try:
        segpack = json.loads(sp.read_text(encoding="utf-8"))
    except FileNotFoundError:
        print(f"[{seg}] not ready: segpack missing ({sp}) -- run segpack.py first")
        sys.exit(1)
    except Exception as e:
        print(f"[{seg}] not ready: segpack not valid JSON ({e})")
        sys.exit(1)

    segpack_errs = check_segpack_structure(segpack)
    if segpack_errs:
        print(f"[{seg}] not ready: segpack not schema-valid ({'; '.join(segpack_errs)})")
        sys.exit(1)

    # segpack's blocks/footnotes/verses are ARRAYS of objects keyed by
    # id/n/vid respectively (segpack.schema.json); the draft mirrors them as
    # DICTS keyed by the same ids (draft.schema.json). Readiness = the two
    # key sets match exactly -- no more, no less.
    want_b = {b["id"] for b in segpack.get("blocks", [])}
    want_f = {str(f["n"]) for f in segpack.get("footnotes", [])}
    want_v = {v["vid"] for v in segpack.get("verses", [])}

    # JSON object keys are always strings post-parse, so plain set() suffices
    # for all three -- no str() cast needed on footnote keys.
    got_b = set(draft.get("blocks", {}))
    got_f = set(draft.get("footnotes", {}))
    got_v = set(draft.get("verses", {}))

    if got_b != want_b or got_f != want_f or got_v != want_v:
        print(
            f"[{seg}] not ready: key sets incomplete "
            f"(blocks {len(got_b)}/{len(want_b)}, "
            f"footnotes {len(got_f)}/{len(want_f)}, "
            f"verses {len(got_v)}/{len(want_v)})"
        )
        sys.exit(1)

    if args.expect_token is not None:
        token = draft.get("dispatch_token")
        if token != args.expect_token:
            note = ""
            run_id = _claim_run_id(args.expect_token)
            if run_id is not None:
                note = _claim_note(run_id, seg, dirs["durable_root"], token)
            print(
                f"[{seg}] not ready: dispatch_token mismatch "
                f"(draft={token!r}, expected={args.expect_token!r}) -- "
                f"stale/straggler draft from a different run{note}"
            )
            sys.exit(1)

    print(f"[{seg}] READY (delivered)")
    sys.exit(0)


if __name__ == "__main__":
    main()
