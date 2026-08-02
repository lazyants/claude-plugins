#!/usr/bin/env python3
"""select_segments.py -- the W5 mass-translate preflight / resumability gate.

Part of the literary-translator plugin's ledger/resumability subsystem (see
references/ledger-and-resumability.md). This subsystem -- the per-segment
fragment ledger, the atomic writer, the merge/stale materializer, the
composite cache key, and this classification gate -- is NEW plugin
hardening layered on top of the source-proven historiettes-t3 engine loop.
It has not yet been run at scale; treat it as a careful first design, not
as something already proven surprise-free.

STATUS: non-gating for convergence but gating for resume (folded into
resume_setup.py's resume-integrity digest) -- covered by
`orchestration_bundle_hash` (never `plugin_bundle_hash`), and never itself a
member of the 15-field `cache_key` composite. It classifies; it never writes
a ledger fragment.

What it does (SKILL.md's W5 "Mass-translate" section, authoritative):

  1. Runs `ledger_merge.py` (bare, no --expected-* flag) to materialize the
     current `runs/ledger.json` from whatever fragments exist under
     `runs/ledger.d/*.json`.
  2. Reads the full candidate segment-id list from `manifest.json`'s
     `segments[]`.
  3. For each candidate, classifies into exactly one of six categories by
     comparing the ledger's recorded state against freshly recomputed
     truth (`cache_key.py --seg <id>`, the current on-disk draft's sha1,
     and -- only when needed to resolve the derivation-state gate --
     `segments/segpack_{seg}.json`'s own `generation_hashes`):

       - reusable                  -- converged, cache key AND draft sha1
                                       both still match. Skip.
       - stale                     -- converged, but the cache key and/or
                                       the draft sha1 no longer match.
                                       Needs a fresh translate/review/fix
                                       pass. Records which trigger(s) fired
                                       in `stale_reason`
                                       (`cache_key_mismatch` and/or
                                       `draft_sha1_mismatch`). A
                                       draft_sha1_mismatch-triggered stale
                                       is NEVER reclassified as
                                       blocked_needs_regeneration -- the two
                                       gates are independent.
       - blocked_needs_regeneration -- converged, cache-key mismatch is
                                       confined to one or more of the four
                                       derivation-state fields
                                       (particle_config_hash,
                                       source_extraction_hash,
                                       source_input_hash,
                                       derivation_bundle_hash), draft sha1
                                       still matches, AND the segpack has
                                       not yet caught up with the current
                                       value of at least one of those
                                       fields. Excluded from SEGS,
                                       self-clearing once the named
                                       regeneration step reruns, never a
                                       manual-override target.
       - recoverable                -- an `in_progress` (or any other
                                       non-terminal) fragment exists --
                                       treated like not_started for
                                       dispatch, counted separately for
                                       visibility.
       - not_started                -- no fragment at all.
       - human_escalation           -- fragment status is `blocked` or
                                       `non_converged` (or its cache key
                                       could not be recomputed at all,
                                       e.g. a missing segpack) -- excluded
                                       from automatic re-dispatch by
                                       default.

  4. Emits `SEGS = not_started UNION recoverable UNION stale` (excluding
     reusable, human_escalation, blocked_needs_regeneration), plus the full
     per-segment classification report. This is the exact list that must
     become `mergeLedgerPrompt`'s `--expected-segs` later -- no drift
     between the dispatch decision and the completeness check.
  5. #409 Step 3: refuses an AUTHORIZING invocation when EITHER evidence
     signal still shows a prior RUN_ID having used this project without the
     resume-integrity gate -- i.e. a run id that either appears in some
     draft's `dispatch_token` OR has a `runs/workflows/<RUN_ID>/` directory,
     and has no `runs/<RUN_ID>/input.digest`. The two evidence halves are
     UNIONED because neither subsumes the other: the driver leaves only
     drafts (it never calls pipeline(), so it creates no workflow
     directory), while a draft holds only its most RECENT token, so a
     skipped run whose drafts were later overwritten survives only as a
     workflow directory. See `scan_workflow_run_ids()` for that, and the
     gate's own block comment in `run()` for the three states the set
     difference distinguishes (gate ran / gate skipped / first run ever) and
     why it is scanned over ALL drafts rather than over `segs`. The evidence
     (`runs_missing_digest`, `dispatching_run_ids`, `workflow_run_ids`,
     `run_id_evidence`, `drafts_scanned`, `drafts_untokened`,
     `unsafe_run_ids`) is reported on the success path too, so a caller can
     assert the exact set rather than merely that the run passed.

     HONEST LIMIT, not covered by "neither subsumes the other" above: a
     DRIVER-dispatched run whose EVERY draft is LATER overwritten by a
     subsequent (compliant or not) run is invisible to BOTH halves at once,
     not just one -- the driver never leaves a workflow directory (so that
     half was never going to see it), and once every draft pointing at it is
     overwritten, the draft scan cannot either (a draft holds only its most
     recent token). No artifact for that run id survives anywhere on disk in
     that combination, so no scan over current disk state can recover it;
     this is not a bug in the union, it is what "neither signal keeps
     history" means when both apply to the same run at once. See
     `scan_workflow_run_ids()`'s own docstring for the fourth (undetectable)
     case this adds to its three, and
     tests/resume_gate_skip_detection.test.py's
     test_driver_run_fully_overwritten_and_never_instantiated_is_undetectable
     for the fixture that pins it as an ACCEPTED gap, not silently assumed
     correctness.
  6. Security fix (found alongside #409 Step 3): every run id from EITHER
     evidence half is validated (`validate_run_id()`, the identical shape
     resume_setup.py's own RUN_ID_RE and backfill_resume_gate_ack.py's own
     validate_run_id() already enforce) BEFORE it is ever spliced into a
     filesystem path. A draft's `dispatch_token` is untrusted, unpatterned
     input -- draft.schema.json has no `pattern` on it, and draft_ready.py's
     `--expect-token` only checks EQUALITY against an expected token, never
     shape -- and this gate reads it from drafts that, by definition,
     predate the gate: the population with the LEAST controlled token
     provenance. Without this check, a run id like `'../../../../tmp/x'`
     (from a token `'../../../../tmp/x:seg01'`) escapes `runs_dir` when
     stat'd, and one like `'/etc'` (from `'/etc:seg01'`) discards `runs_dir`
     entirely -- `Path('runs') / '/etc' == Path('/etc')`. A run id that
     fails validation is never used to build a path; see `run()`'s own
     comment for what happens to it instead and why.
  7. Audit-accuracy fix: the Step 3 refusal's summary sentence no longer
     blanket-claims every listed run id "dispatched work" -- a
     `runs/workflows/<RUN_ID>/` directory with no draft pointing at it
     (`scan_workflow_run_ids()`'s own documented "instantiated and
     dispatched nothing" case) proves INSTANTIATION, never dispatch, and a
     refusal that said otherwise for that id would itself be an inaccurate
     durable-adjacent claim -- the same class of defect fixed on the writer
     side in `backfill_resume_gate_ack.py`'s own `.resume_gate_ack` marker
     (its `note` field used to say the identical untrue thing). Each run
     id's OWN evidence is still named per-id in the message detail
     (`evidence: drafts`/`workflow_dir`/`drafts+workflow_dir`), unchanged.

CLI flags:

    --only-segs <comma-list>
        Intersects the emitted SEGS with this explicit id list (for
        operator-paced batches). Also the sole mechanism for retrying a
        human_escalation segment: naming a currently blocked/non_converged
        id here is an explicit, auditable override -- included in SEGS
        despite its classification, logged as an override.
        blocked_needs_regeneration is never overridable this way (it is
        self-clearing, not a human decision); a reusable segment named here
        is also not force-included (--only-segs narrows, it does not force
        a cache-valid segment to redo). Omitting --only-segs entirely
        reproduces default behavior byte-for-byte.
        FATALS if any named id is not present in manifest.json's
        segments[] at all -- names every unrecognized id, never silently
        drops them.

    --allow-empty
        Without this flag, an empty emitted SEGS is a FATAL error (guards
        against a silently-no-op mass-translate run). With it, an empty
        SEGS is reported normally -- for a deliberately narrow rerun that
        happens to select nothing right now.

Every invocation logs the requested ids (or "<all candidates>" when
--only-segs was omitted) alongside the actually-emitted SEGS ids, to
stderr, for audit.

Self-anchoring by default: this script always lives at
``${durable_root}/scripts/select_segments.py`` and derives durable_root
from its own path -- it never assumes cwd. LT-409: two INDEPENDENT,
orthogonal flags override this, deliberately kept separate:

  --durable-root PATH   the DATA root (manifest.json, segments/, runs/).
  --plugin-root PATH    where the SIBLING SCRIPTS this script shells out
                         to (ledger_merge.py, cache_key.py) are resolved
                         from, as ``{PATH}/assets/scripts/<name>.py``.

A single root cannot serve both roles: ``${durable_root}/scripts/`` is a
Step-0a copy that the codex process (via codex_job.py's ``--write`` over
the whole durable root) can write to, so resolving the checker scripts
FROM there would let a tampered copy validate itself -- exactly the
vulnerability this flag split exists to close. The two flags do NOT
propagate identically, and the asymmetry is deliberate: ``--durable-root``
travels the whole subprocess chain (select_segments.py -> ledger_merge.py
-> cache_key.py) as each sibling's own same-named flag, but
``--plugin-root`` is passed only to ledger_merge.py, which resolves a
further sibling of its own. cache_key.py is a LEAF -- it has no siblings to
resolve and does not accept ``--plugin-root`` at all, so passing it would
simply make the invocation fail. When ``--plugin-root`` is given WITHOUT
``--durable-root``, a ``--durable-root`` synthesized from the resolved
durable root is passed to the leaf instead, because the leaf no longer
physically sits under that root and would otherwise self-anchor against the
wrong tree. Omitting BOTH reproduces today's self-anchored behavior
byte-for-byte.

Output: exactly one JSON object on stdout. Success:
{"success": true, "durable_root": ..., "segs": [...],
 "requested_only_segs": [...] | null, "classification": {seg: {...}},
 "counts": {...}, "ids_by_category": {category: [seg, ...]},
 "overrides": [...], "excluded_only_segs": [...]}. `counts` and
 `ids_by_category` are keyed by the same six ALL_CATEGORIES, one the
 per-category tally and the other the per-category segment-id list (each
 stale segment's own `stale_reason` lives inline in `classification`) --
 together this is the "classification report" the build spec requires
 (counts + IDs per category + stale_reason).
Failure: {"success": false, "error": ...}. Exit 0 on success, 1 on any
fatal condition -- callers should read stdout, not rely on the exit code
alone.
"""

import argparse
import hashlib
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import NoReturn

# ---------------------------------------------------------------------------
# Self-anchoring
# ---------------------------------------------------------------------------

SCRIPTS_DIR = Path(__file__).resolve().parent
DURABLE_ROOT = SCRIPTS_DIR.parent

MANIFEST_PATH = DURABLE_ROOT / "manifest.json"
LEDGER_MERGE_SCRIPT = SCRIPTS_DIR / "ledger_merge.py"
CACHE_KEY_SCRIPT = SCRIPTS_DIR / "cache_key.py"


def resolve_dirs(durable_root_str, plugin_root_str=None):
    """LT-409: `durable_root_str` governs DATA (manifest.json) -- rebuilt
    from that root when given, self-anchored DURABLE_ROOT/MANIFEST_PATH
    otherwise.

    `plugin_root_str` is a SEPARATE, independent input governing where the
    SIBLING SCRIPTS this script shells out to (ledger_merge.py, cache_key.py)
    are resolved from -- deliberately NEVER derived from `durable_root_str`.
    `${durable_root}/scripts/` is copied there by Step 0a and is writable by
    the codex process these scripts gate (codex_job.py runs it with --write
    over the whole durable root) -- resolving the checker from inside the
    thing it checks would let a tampered durable-root copy silently pass
    itself. When given, a sibling resolves as
    `{plugin_root}/assets/scripts/<name>.py` -- the SAME layout SKILL.md
    documents for the plugin-anchored scripts (profile_validate.py etc.,
    see SKILL.md's Step 0/W2 sections), NOT durable_root's own flattened
    `scripts/<name>.py` copy layout (Step 0a strips the `assets/` prefix on
    copy). `plugin_root_str=None` reproduces today's self-anchored sibling
    lookup (`Path(__file__).resolve().parent`) unchanged.

    Both None -> today's exact self-anchored values for both concerns.
    """
    if durable_root_str is None:
        durable_root = DURABLE_ROOT
        manifest_path = MANIFEST_PATH
    else:
        durable_root = Path(durable_root_str).resolve()
        manifest_path = durable_root / "manifest.json"

    if plugin_root_str is None:
        ledger_merge_script = LEDGER_MERGE_SCRIPT
        cache_key_script = CACHE_KEY_SCRIPT
    else:
        plugin_scripts_dir = Path(plugin_root_str).resolve() / "assets" / "scripts"
        ledger_merge_script = plugin_scripts_dir / "ledger_merge.py"
        cache_key_script = plugin_scripts_dir / "cache_key.py"

    return {
        "durable_root": durable_root,
        "manifest_path": manifest_path,
        "ledger_merge_script": ledger_merge_script,
        "cache_key_script": cache_key_script,
    }


def _root_forward_args(dirs: dict, durable_root_str, plugin_root_str) -> list:
    """LT-409: the exact --durable-root/--plugin-root pair to forward to a
    sibling subprocess.

    Whenever --plugin-root redirects where a sibling's OWN script file is
    found, that sibling's self-anchored DATA resolution breaks too -- its
    __file__ no longer sits under durable_root, so its own
    Path(__file__).resolve().parents[1] would silently point at the plugin
    root instead. So an explicit --durable-root MUST be forwarded whenever
    --plugin-root is given, even if THIS script itself was never passed
    --durable-root (and is itself self-anchored) -- using the resolved
    dirs["durable_root"] as the value. Omitting BOTH flags never forwards
    anything, preserving today's self-anchored behavior on both sides.
    """
    args = []
    if durable_root_str is not None:
        args += ["--durable-root", durable_root_str]
    elif plugin_root_str is not None:
        args += ["--durable-root", str(dirs["durable_root"])]
    if plugin_root_str is not None:
        args += ["--plugin-root", plugin_root_str]
    return args


# Canonical paths (references/ledger-and-resumability.md's "Canonical path
# invariants" -- deliberately WITHOUT a target-language suffix, unlike the
# real historiettes-t3 reference project's own .ru.draft.json naming).


def draft_path(seg: str, durable_root: Path = DURABLE_ROOT) -> Path:
    return durable_root / "segments" / f"{seg}.draft.json"


def ever_converged_path(seg: str, segments_dir: Path) -> Path:
    """#409 Step 1: the durable 'this segment has converged at least once'
    sentinel. WRITTEN by ledger_update.py:mark_ever_converged (the single
    place convergence is recorded); this script only ever READS it.

    The filename convention is stated in two scripts because both are
    standalone entrypoints with no shared import, so
    tests/select_segments.test.py's
    test_sentinel_filename_matches_the_writer_in_ledger_update pins them
    against each other by name -- a drift test, not a second source of
    truth."""
    return segments_dir / f".ever_converged.{seg}"


def segpack_path(seg: str, durable_root: Path = DURABLE_ROOT) -> Path:
    return durable_root / "segments" / f"segpack_{seg}.json"


def input_digest_path(run_id: str, runs_dir: Path) -> Path:
    """resume_setup.py's own `runs/<RUN_ID>/input.digest` -- the ONE artifact
    whose presence proves the resume-integrity gate ran for that RUN_ID.
    Written exclusively by resume_setup.py's `write_run_dir()`; this script
    only ever stats it."""
    return runs_dir / run_id / "input.digest"


def resume_gate_ack_path(run_id: str, runs_dir: Path) -> Path:
    """#409 Step 3: the durable 'this RUN_ID dispatched before the
    resume-integrity gate was enforced' acknowledgement, written exclusively
    by `backfill_resume_gate_ack.py` (this script only ever READS it, exactly
    as it only reads `ever_converged_path()`'s sentinel).

    Deliberately PER-RUN_ID and stored inside that run's own directory, not a
    project-level list file. A single project-level marker would be one edit
    away from becoming a blanket off-switch (a `"*"` entry, an `"all": true`
    key), and a gate with a wildcard escape is the invisible warning this
    check exists to replace. Per-run makes the wildcard structurally
    inexpressible: acknowledging a run requires naming it, and the NEXT
    skipped run has a different name and is therefore still refused. Same
    shape, and same reason, as `.ever_converged.{seg}` being one marker per
    protected segment rather than one flag per project.

    It sits beside the `input.digest` that SHOULD have been there, which is
    also the most legible place for a human reading `runs/` later. Creating
    `runs/<RUN_ID>/` for a run that has no digest is safe for every existing
    consumer: both resume_setup.py's `resolve_run()` and the driver's own
    candidate scan require `input.digest` to be a FILE before they will
    consider a directory at all, so an ack-only directory is invisible to
    resume and can never be mistaken for a resumable run."""
    return runs_dir / run_id / ".resume_gate_ack"


def draft_run_id(dispatch_token) -> "str | None":
    """The RUN_ID out of a draft's `dispatch_token`, or None when the token
    is absent/malformed.

    The token is `<RUN_ID>:<seg>`, and a RUN_ID can never contain ':'
    (resume_setup.py's own `validate_run_id()` rejects it explicitly), while
    a SEG id certainly can -- `FRONTBACK:fm04` is a real, shipped segment id
    shape. So the split is on the FIRST colon only: partitioning
    `'20260801T090001Z:FRONTBACK:fm04'` yields the run id, not
    `'20260801T090001Z:FRONTBACK'`. A naive `rsplit`/`split(':')[-2]` gets
    this wrong on exactly the frontback segments, which are the ones least
    likely to appear in a hand-built fixture."""
    if not isinstance(dispatch_token, str):
        return None
    run_id, sep, rest = dispatch_token.partition(":")
    if not sep or not run_id or not rest:
        return None
    return run_id


def scan_dispatching_run_ids(segments_dir: Path) -> dict:
    """Every RUN_ID that has actually DISPATCHED work into this project,
    read from the canonical drafts' own `dispatch_token` fields.

    Returns `{"by_run_id": {run_id: [seg, ...]}, "drafts_scanned": N,
    "drafts_untokened": N}` -- the counts are reported in this script's own
    JSON output on purpose. A scan that silently matched nothing (wrong
    directory, wrong glob, a durable root that moved) produces an empty
    `by_run_id` and would make the gate below pass vacuously, looking
    exactly like a clean project; `drafts_scanned` is what lets a reader --
    and the test suite -- tell those two apart.

    KNOWN HOLE, and its direction: `draft.schema.json` lists
    `dispatch_token` in `properties` but NOT in `required` (its own
    description says "OPTIONAL at the schema level"), so a draft may
    legitimately carry no token at all. Such a draft is unattributable and
    contributes nothing here. That can only ever cause a FALSE NEGATIVE (a
    skipped run this check fails to notice), never a false positive (a
    compliant run wrongly refused). The same one-way direction applies to a
    draft whose token was OVERWRITTEN by a later run: a draft holds only the
    most recent dispatch, so a skipped run whose every draft was later
    re-dispatched under a compliant run leaves no trace here and is not
    detected. Both are deliberate: this gate under-detects rather than
    risking a refusal a compliant project cannot explain.

    Those two holes COMPOSE: a driver-dispatched run whose every draft is
    later overwritten is invisible here for the second reason above AND
    invisible to `scan_workflow_run_ids()` for a separate, unrelated reason
    (the driver never writes a workflow directory at all) -- so that
    combination has no surviving trace in EITHER scan, not just this one.
    See `scan_workflow_run_ids()`'s own docstring for that fourth case."""
    by_run_id: dict = {}
    scanned = 0
    untokened = 0
    if not segments_dir.is_dir():
        return {"by_run_id": by_run_id, "drafts_scanned": 0, "drafts_untokened": 0}
    for path in sorted(segments_dir.glob("*.draft.json")):
        scanned += 1
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            # An unreadable draft is not this gate's business to adjudicate
            # (validate_draft.py owns that); it simply cannot be attributed.
            untokened += 1
            continue
        run_id = draft_run_id(doc.get("dispatch_token") if isinstance(doc, dict) else None)
        if run_id is None:
            untokened += 1
            continue
        seg = doc.get("seg") if isinstance(doc.get("seg"), str) else path.name
        by_run_id.setdefault(run_id, []).append(seg)
    for segs in by_run_id.values():
        segs.sort()
    return {
        "by_run_id": by_run_id,
        "drafts_scanned": scanned,
        "drafts_untokened": untokened,
    }


# A run-id-shaped directory name. Mirrors resume_setup.py's own RUN_ID_RE
# (kept as an independent restatement per this project's "no shared lib
# between self-contained scripts" convention). Used two ways: to skip
# entries under runs/workflows/ that cannot be run ids at all (inside
# scan_workflow_run_ids() below), and -- security fix -- as the shape
# validate_run_id() enforces on EVERY run id from either evidence half,
# draft-derived included, before it is ever spliced into a path.
_RUN_ID_DIR_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


def scan_workflow_run_ids(runs_dir: Path) -> list:
    """Every RUN_ID for which a Workflow template was INSTANTIATED, from the
    `runs/workflows/<RUN_ID>/` directories the orchestrating session writes
    (see each template's own header comment -- the three *-wf.template.js
    files are the only writers of that path).

    The second half of the Step 3 evidence, and it exists because the first
    half has a structural blind spot the other way round. A draft holds only
    its MOST RECENT dispatch token, so a skipped run whose drafts were all
    later re-dispatched under some other run leaves no trace in the draft
    scan at all. Measured on the project that motivated this check: the
    draft scan sees four run ids, the workflow directories show six.

    Neither signal subsumes the other, which is why the gate unions them:

      drafts only          -- a driver run. The driver never creates a
                              workflow directory (verified: its sole mention
                              of `workflows/` is a docstring listing it as a
                              non-run-id entry to filter OUT, and none of its
                              three mkdir calls touch that path), because it
                              never calls pipeline().
      workflow dir only    -- a manual-path run whose drafts were later
                              overwritten, or one that instantiated and
                              dispatched nothing.
      both                 -- the ordinary manual-path run.
      neither (undetectable) -- a DRIVER run (so no workflow directory was
                              ever written for it) whose every draft was
                              LATER overwritten by a subsequent run (so the
                              draft scan's own trace is also gone). This
                              combination leaves NO artifact in either scan,
                              on either side of the union -- not a gap in
                              how the two are combined, but the two
                              documented one-way holes above landing on the
                              same run id at once. No scan over current disk
                              state can recover a run id with zero surviving
                              evidence; this is accepted, not fixed, and
                              tests/resume_gate_skip_detection.test.py's
                              test_driver_run_fully_overwritten_and_never_instantiated_is_undetectable
                              pins it as exactly that.

    A workflow directory proves INSTANTIATION, not dispatch -- which is
    sound for this gate in a way it would not be for a stronger claim: the
    documented order is resume_setup.py FIRST, then instantiate with the
    resolved {{RUN_ID}}. So a workflow directory whose run id has no digest
    means the template was instantiated without the gate having run, whether
    or not any segment was subsequently dispatched. Verified against the one
    fully-compliant live project: all six of its workflow directories have
    digests, so this half contributes zero false positives there."""
    workflows_dir = runs_dir / "workflows"
    if not workflows_dir.is_dir():
        return []
    return sorted(
        p.name
        for p in workflows_dir.iterdir()
        if p.is_dir() and _RUN_ID_DIR_RE.fullmatch(p.name)
    )


def validate_run_id(run_id):
    """Return an error string if `run_id` is not a safe RUN_ID, else None.

    Security fix, same class as `validate_seg()` above: `input_digest_path()`
    and `resume_gate_ack_path()` both splice `run_id`, UNQUOTED, into a
    filesystem path (`runs_dir / run_id / ...`), so a run id sourced from
    untrusted evidence -- a draft's `dispatch_token` has no schema `pattern`
    and is never shape-checked before this script reads it -- must be
    rejected HERE, before either path function is ever called, exactly as
    `validate_seg()` rejects a bad segment id before `draft_path()` is built
    from it.

    Byte-for-byte the same check as backfill_resume_gate_ack.py's own
    `validate_run_id()` (itself mirroring resume_setup.py's `RUN_ID_RE` --
    the pattern that script's own `validate_run_id()` uses to reject a
    RUN_ID at the point it is MINTED). Duplicated rather than imported per
    this project's "no shared lib between self-contained scripts"
    convention; pinned against drift by
    tests/resume_gate_skip_detection.test.py's
    test_both_copies_of_validate_run_id_agree. Matching the sibling's shape
    on purpose: a run id this script refuses must be a run id
    backfill_resume_gate_ack.py refuses too, or the refusal below would
    recommend a remedy that cannot work (see run()'s own comment)."""
    if not isinstance(run_id, str) or not run_id:
        return "run id must be a non-empty string."
    if not _RUN_ID_DIR_RE.fullmatch(run_id):
        return (
            "run id must match [A-Za-z0-9][A-Za-z0-9._-]* (letters/digits/"
            f"dot/underscore/hyphen only, no ':'); got {run_id!r}."
        )
    if run_id in (".", ".."):
        return f"run id must not be '.' or '..'; got {run_id!r}."
    if ".." in run_id:
        return f"run id must not contain '..'; got {run_id!r}."
    return None


# The authoritative 15-field cache-key list (references/ledger-and-
# resumability.md, "Composite cache key -- exact 15-field structure";
# mirrors cache_key.py's own CACHE_KEY_FIELD_ORDER and ledger_merge.py's own
# CACHE_KEY_FIELDS literal -- kept as an independent restatement per this
# project's "no shared lib between self-contained scripts" convention).
CACHE_KEY_FIELDS = [
    "input_sha1",
    "style_contract_hash",
    "used_terms_hash",
    "pipeline_version",
    "schema_hash",
    "prompt_hash",
    "agent_config_hash",
    "profile_semantics_hash",
    "particle_config_hash",
    "source_extraction_hash",
    "source_input_hash",
    "derivation_bundle_hash",
    "verse_map_hash",
    "note_map_hash",
    "plugin_bundle_hash",
]

# The four "flag-only, needs regeneration" derivation-state fields (see
# "Derivation-state gate" in ledger-and-resumability.md). A cache-key
# mismatch confined to these does not by itself prove segpack_{seg}.json
# has caught up -- that must be checked separately against the segpack's
# own recorded generation_hashes.
DERIVATION_STATE_FIELDS = frozenset(
    {
        "particle_config_hash",
        "source_extraction_hash",
        "source_input_hash",
        "derivation_bundle_hash",
    }
)

def _w3_regen_step(field: str) -> str:
    """The W3/W3a remedy, generated once for every derivation-state field
    whose regeneration routes through the W3 glossary pass.

    Both such fields share one remedy AND one hole. `particle_config_hash`
    flips when the resolved particle config file's bytes change;
    `derivation_bundle_hash` flips when bootstrap_names.py's or segpack.py's
    bytes change (cache_key.py's DERIVATION_BUNDLE_MEMBERS). Either way the
    fix is the same chain, and either way it dead-ends the same way: the
    glossary pass only re-stamps canon.json when it actually MERGES
    something, and a mature project with zero unresolved candidates skips
    that pass entirely -- so for exactly that project the remedy is
    unreachable and the block never clears (#193). 1.15.0 (#291) additionally
    removed the undocumented `--merge-batches <empty-batch.json>` restamp
    that had been its only unsanctioned way out, so the hint must name the
    sanctioned replacement.

    Two orderings are load-bearing, not cosmetic. bootstrap_names.py comes
    FIRST: jumping straight to the glossary pass when only its bytes changed
    would consume stale name_candidates.json rows and still re-stamp, quietly
    papering over the staleness. segpack.py comes LAST: it copies
    canon.json's stamp forward rather than recomputing it, so running it
    before the restamp merely re-copies the stale value.

    Generated rather than spelled out per field because these were two
    hand-maintained strings -- which is exactly how the escape came to be
    added to one and forgotten on the other.
    """
    return (
        "W3/W3a (re-run bootstrap_names.py to regenerate name candidates, "
        f"then the glossary pass to re-stamp canon.json's {field} -- or, on "
        "a project with no new candidates left to merge, canon_validate.py "
        "--restamp-derivation -- then segpack.py)"
    )


# The W2 remedy. One literal for both W2 fields for the same reason
# _w3_regen_step() exists: two identical hand-maintained strings is how the
# W3 pair drifted, one gaining the restamp escape while the other silently
# did not. These two need no escape (they are re-run by the extractor at W2,
# never by the glossary pass), but they get one source of truth anyway --
# the point is that editing this remedy is a single edit, always.
_W2_REGEN_STEP = "W2 (re-run the source-format extractor)"

# Actionable "what to rerun" message per derivation-state field, per
# ledger-and-resumability.md's own wording.
FIELD_TO_REGEN_STEP = {
    "source_extraction_hash": _W2_REGEN_STEP,
    "source_input_hash": _W2_REGEN_STEP,
    "particle_config_hash": _w3_regen_step("particle_config_hash"),
    "derivation_bundle_hash": _w3_regen_step("derivation_bundle_hash"),
}

# Fragment statuses that mean "a human must resolve this before automated
# re-dispatch" (ledger.schema.json's status enum, minus the ones this
# script handles specially: converged/stale -> classify_converged_segment,
# everything else non-terminal -> recoverable).
HUMAN_ESCALATION_STATUSES = frozenset({"blocked", "non_converged"})

# Statuses ledger_merge.py may hand back for a segment that WAS converged
# at write time (a plain 'converged' if nothing has drifted, or 'stale' --
# computed by ledger_merge.py itself, never written to an on-disk fragment
# -- if at least one cache-key field has drifted since).
WAS_CONVERGED_STATUSES = frozenset({"converged", "stale"})


class FatalError(Exception):
    """Raised for any failure that should surface as a top-level FAILURE
    JSON payload on stdout (exit 1), never a bare traceback."""


def fatal(message: str, **extra) -> NoReturn:
    raise FatalError(json.dumps({"success": False, "error": message, **extra}))


def draft_content_sha1(path: Path) -> str:
    """sha1 of a draft's CONTENT, with the 'dispatch_token' metadata field
    deliberately EXCLUDED -- see draft_sha1.py's own module docstring for why.

    Must match, byte for byte, draft_sha1.py's and ledger_update.py's own
    draft_content_sha1() -- both parse the draft as JSON, drop
    'dispatch_token' if present, and re-serialize the remainder via
    identical sorted-key canonical JSON before hashing.

    Raises OSError (unreadable file), json.JSONDecodeError (not valid
    JSON), or ValueError (valid JSON but not an object) on failure --
    callers handle all three.
    """
    raw = path.read_text(encoding="utf-8")
    doc = json.loads(raw)
    if not isinstance(doc, dict):
        raise ValueError(f"draft at {path} must be a JSON object, got {type(doc).__name__}")
    projected = {k: v for k, v in doc.items() if k != "dispatch_token"}
    canonical = json.dumps(
        projected, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha1(canonical).hexdigest()


def read_json(path: Path, what: str):
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        fatal(f"{what} not found at {path}")
    except OSError as exc:
        fatal(f"could not read {what} at {path}: {exc}")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        fatal(f"{what} at {path} is not valid JSON: {exc}")


def read_segpack_nonfatal(seg: str, durable_root: Path = DURABLE_ROOT) -> "dict | str":
    """Read segments/segpack_{seg}.json for the derivation-state gate,
    returning the parsed dict on success or a string error message on
    failure -- NEVER raising/exiting. A per-segment segpack gone unreadable
    (concurrent regeneration/cleanup, transient IO) must escalate only THAT
    segment, never abort the whole W5 preflight -- matching
    compute_current_cache_key()'s isolation contract and this file's
    "a per-segment failure must never take down the whole run" rule."""
    path = segpack_path(seg, durable_root)
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return f"segpack for segment {seg!r} not found at {path}"
    except UnicodeDecodeError as exc:
        # Invalid/truncated UTF-8 -> UnicodeDecodeError is a subclass of
        # ValueError, NOT OSError, so `except OSError` alone would miss it.
        return f"segpack for segment {seg!r} at {path} is not valid UTF-8: {exc}"
    except OSError as exc:
        return f"could not read segpack for segment {seg!r} at {path}: {exc}"
    try:
        doc = json.loads(raw)
    except json.JSONDecodeError as exc:
        return f"segpack for segment {seg!r} at {path} is not valid JSON: {exc}"
    if not isinstance(doc, dict):
        return f"segpack for segment {seg!r} at {path} is not a JSON object"
    return doc


# ---------------------------------------------------------------------------
# Segment id validation -- the SOURCE guard. Every seg id this script ever
# handles (manifest.json's segments[], and --only-segs) ends up spliced,
# unquoted, into the generated mass-translate-wf.js's shell command strings
# (see mass-translate-wf.template.js's translatePrompt/reviewPrompt/etc.), so
# a poisoned manifest or a hand-typed --only-segs value must be rejected
# HERE, before any path is built or any id is emitted into SEGS -- never
# left for the workflow's own defense-in-depth JS guard to catch first.
# Canonical allowlist, kept identical to review_artifact_check.py's own
# validate_seg() per this project's "no shared lib between self-contained
# scripts" convention.
# ---------------------------------------------------------------------------

# A seg id is either an ordinary body id (e.g. "seg01", "seg05_blocked_regen")
# or a translate-decision FRONTBACK:{id} unit (e.g. "FRONTBACK:fm01"). Using
# re.fullmatch (NOT re.match + "$") -- in Python "$" also matches just before
# a trailing newline, so re.match(r"...$", "seg01\n") would WRONGLY pass.
_SEG_ID_RE = re.compile(r"(?:FRONTBACK:)?[A-Za-z0-9_]+")


def validate_seg(seg):
    """Return an error string if `seg` is not a path/shell-safe segment id,
    else None. Allows ONLY [A-Za-z0-9_] with an optional literal
    'FRONTBACK:' prefix -- rejecting empties, path separators, '..',
    absolute paths, and every shell metacharacter."""
    if not isinstance(seg, str) or not seg:
        return "segment id must be a non-empty string."
    if not _SEG_ID_RE.fullmatch(seg):
        return (
            "segment id must match (FRONTBACK:)?[A-Za-z0-9_]+ (no path "
            f"separators, '..', or shell metacharacters); got {seg!r}."
        )
    return None


# ---------------------------------------------------------------------------
# Step 1: run ledger_merge.py (bare -- no completeness flag; this is only
# ever meant to freshly materialize runs/ledger.json, not to gate on which
# segments happen to already have a fragment).
# ---------------------------------------------------------------------------


def run_ledger_merge(dirs: dict, durable_root_str=None, plugin_root_str=None) -> dict:
    ledger_merge_script = dirs["ledger_merge_script"]
    if not ledger_merge_script.is_file():
        fatal(f"ledger_merge.py not found at {ledger_merge_script}")
    cmd = [sys.executable, str(ledger_merge_script)] + _root_forward_args(
        dirs, durable_root_str, plugin_root_str
    )
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
            cwd=str(dirs["durable_root"]),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        fatal(f"could not run ledger_merge.py: {exc}")

    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        fatal(
            "ledger_merge.py did not print valid JSON on stdout "
            f"(exit {proc.returncode}): stdout={proc.stdout!r} stderr={proc.stderr!r}"
        )

    if not isinstance(payload, dict) or not payload.get("success"):
        error = payload.get("error") if isinstance(payload, dict) else None
        fatal(
            "ledger_merge.py failed to materialize runs/ledger.json"
            + (f": {error}" if error else f" (stdout={proc.stdout!r})")
        )

    return payload


def load_ledger_segments(merge_result: dict, durable_root: Path = DURABLE_ROOT) -> dict:
    ledger_path = Path(merge_result.get("ledger_path") or (durable_root / "runs" / "ledger.json"))
    doc = read_json(ledger_path, "materialized ledger.json")
    segments = doc.get("segments")
    if not isinstance(segments, dict):
        fatal(f"materialized ledger.json at {ledger_path} has no 'segments' object")
    return segments


# ---------------------------------------------------------------------------
# Step 2: candidate segment ids from manifest.json's segments[].
# ---------------------------------------------------------------------------


def load_candidate_segments(manifest_path: Path = MANIFEST_PATH) -> list:
    manifest = read_json(manifest_path, "manifest.json")
    segments = manifest.get("segments")
    if not isinstance(segments, list):
        fatal(f"manifest.json at {manifest_path} has no 'segments' array")

    candidates = []
    for item in segments:
        # manifest.schema.json's segments[] entries are REQUIRED to be
        # objects with (at least) their own `seg` field -- a bare string
        # is not a valid entry under that schema and must be rejected
        # fatally, never silently coerced into a candidate id.
        if isinstance(item, dict) and isinstance(item.get("seg"), str):
            seg = item["seg"]
            problem = validate_seg(seg)
            if problem is not None:
                fatal(f"manifest.json: unsafe segment id: {problem}")
            candidates.append(seg)
        else:
            fatal(f"manifest.json: malformed segments[] entry: {item!r}")
    if not candidates:
        fatal(f"manifest.json at {manifest_path} has an empty 'segments' array")
    return candidates


# ---------------------------------------------------------------------------
# Step 3: per-segment classification.
# ---------------------------------------------------------------------------


def compute_current_cache_key(
    seg: str,
    cache_key_script: Path = CACHE_KEY_SCRIPT,
    durable_root: Path = DURABLE_ROOT,
    durable_root_str=None,
    plugin_root_str=None,
) -> "dict | str":
    """Runs cache_key.py --seg <id> and returns the parsed 15-field dict on
    success, or a string error message on failure (never raises/exits --
    a per-segment failure here becomes that segment's own human_escalation
    classification, it must never take down the whole run, matching
    ledger_merge.py's own "warn and continue" treatment of this exact
    subprocess call).

    LT-409: `cache_key_script` is the resolved sibling path to shell out
    against -- self-anchored by default, or resolve_dirs()'s own
    --plugin-root-aware `{plugin_root}/assets/scripts/cache_key.py` (never
    derived from durable_root; see resolve_dirs()'s own docstring for why).
    `durable_root` is cache_key.py's DATA root (cwd for the subprocess).
    `durable_root_str`/`plugin_root_str` are THIS script's own CLI values
    (not cache_key.py's -- it has no --plugin-root, being a leaf with no
    siblings of its own): `durable_root_str` is forwarded verbatim as
    cache_key.py's own --durable-root when given; when it is NOT given but
    `plugin_root_str` IS (meaning `cache_key_script` was itself resolved via
    --plugin-root, so it no longer physically sits under durable_root),
    `durable_root` is forwarded explicitly anyway -- otherwise cache_key.py's
    own self-anchoring would silently resolve its data from the plugin root
    instead of the real durable root.
    """
    if not cache_key_script.is_file():
        return f"cache_key.py not found at {cache_key_script}"
    if durable_root_str is not None:
        cmd_extra = ["--durable-root", durable_root_str]
    elif plugin_root_str is not None:
        cmd_extra = ["--durable-root", str(durable_root)]
    else:
        cmd_extra = []
    try:
        proc = subprocess.run(
            [sys.executable, str(cache_key_script), "--seg", seg, *cmd_extra],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=str(durable_root),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"could not run cache_key.py --seg {seg}: {exc}"

    if proc.returncode != 0:
        return f"cache_key.py --seg {seg} exited {proc.returncode}: {proc.stderr.strip()}"

    try:
        current_key = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return f"cache_key.py --seg {seg} did not print valid JSON: {proc.stdout!r}"

    if not isinstance(current_key, dict):
        return f"cache_key.py --seg {seg} printed a non-object JSON value"

    return current_key


def classify_converged_segment(
    seg: str, record: dict, dirs: dict, durable_root_str=None, plugin_root_str=None
) -> dict:
    """A segment whose materialized status is 'converged' or 'stale' (the
    latter meaning: ledger_merge.py itself detected a cache-key mismatch
    against a fragment that was originally written as 'converged'). Returns
    a classification dict with a 'category' key plus supporting detail.
    """
    current_key = compute_current_cache_key(
        seg, dirs["cache_key_script"], dirs["durable_root"], durable_root_str, plugin_root_str
    )
    if isinstance(current_key, str):
        return {
            "category": "human_escalation",
            "status": "cache_key_recompute_failed",
            "detail": current_key,
        }

    stored_key = record.get("cache_key")
    if not isinstance(stored_key, dict):
        # A schema-valid converged/stale record always has this; if it's
        # missing anyway, don't silently trust an anomalous record.
        return {
            "category": "human_escalation",
            "status": record.get("status"),
            "detail": "converged/stale ledger record is missing its 'cache_key' object",
        }

    mismatched = sorted(f for f in CACHE_KEY_FIELDS if stored_key.get(f) != current_key.get(f))
    cache_key_mismatch = bool(mismatched)

    dp = draft_path(seg, dirs["durable_root"])
    current_draft_sha1 = None
    if dp.is_file():
        try:
            current_draft_sha1 = draft_content_sha1(dp)
        except (OSError, json.JSONDecodeError, ValueError):
            current_draft_sha1 = None
    reviewed_draft_sha1 = record.get("reviewed_draft_sha1")
    draft_sha1_mismatch = current_draft_sha1 is None or current_draft_sha1 != reviewed_draft_sha1

    if not cache_key_mismatch and not draft_sha1_mismatch:
        return {"category": "reusable"}

    if draft_sha1_mismatch:
        # Draft-sha1-triggered staleness is never reclassified as
        # blocked_needs_regeneration -- the two gates are independent.
        stale_reason = ["draft_sha1_mismatch"]
        if cache_key_mismatch:
            stale_reason.append("cache_key_mismatch")
        return {
            "category": "stale",
            "stale_reason": stale_reason,
            "mismatched_fields": mismatched,
        }

    # cache_key_mismatch is True, draft_sha1_mismatch is False: check
    # whether the mismatch is (at least partly) a derivation-state field
    # the segpack itself hasn't caught up with yet.
    derivation_mismatched = [f for f in mismatched if f in DERIVATION_STATE_FIELDS]
    if derivation_mismatched:
        sp = read_segpack_nonfatal(seg, dirs["durable_root"])
        if isinstance(sp, str):
            return {
                "category": "human_escalation",
                "status": "segpack_read_failed",
                "detail": sp,
            }
        # generation_hashes: absent/None -> {} (segpack hasn't caught up, the
        # existing semantics); present-but-not-a-mapping is an anomalous
        # segpack and must ESCALATE, never crash (a bare `or {}` would leave
        # a wrong-type value like a list truthy, and the `.get()` below would
        # raise an uncaught AttributeError -> whole-run crash).
        segpack_gen_hashes = sp.get("generation_hashes")
        if segpack_gen_hashes is None:
            segpack_gen_hashes = {}
        elif not isinstance(segpack_gen_hashes, dict):
            return {
                "category": "human_escalation",
                "status": "segpack_read_failed",
                "detail": f"segpack for segment {seg!r} has a non-object 'generation_hashes'",
            }
        pending_fields = sorted(
            f for f in derivation_mismatched if segpack_gen_hashes.get(f) != current_key.get(f)
        )
        if pending_fields:
            steps = sorted({FIELD_TO_REGEN_STEP[f] for f in pending_fields})
            return {
                "category": "blocked_needs_regeneration",
                "pending_fields": pending_fields,
                "message": (
                    f"segment {seg!r} is blocked on regeneration: rerun "
                    + "; then rerun ".join(steps)
                    + " before this segment can be reclassified"
                ),
            }
        # Every derivation-state field that mismatched has already been
        # caught up by the segpack -- safe to reclassify as ordinary stale.

    return {
        "category": "stale",
        "stale_reason": ["cache_key_mismatch"],
        "mismatched_fields": mismatched,
    }


def classify_segment(
    seg: str, ledger_segments: dict, dirs: dict, durable_root_str=None, plugin_root_str=None
) -> dict:
    record = ledger_segments.get(seg)
    if record is None:
        return {"category": "not_started"}

    status = record.get("status")

    if status in WAS_CONVERGED_STATUSES:
        return classify_converged_segment(seg, record, dirs, durable_root_str, plugin_root_str)

    if status in HUMAN_ESCALATION_STATUSES:
        return {
            "category": "human_escalation",
            "status": status,
            "reason": record.get("reason"),
        }

    # in_progress, pending, or any other non-terminal/unrecognized status:
    # treated identically to not_started for dispatch, counted separately.
    return {"category": "recoverable", "status": status}


# ---------------------------------------------------------------------------
# Step 4: SEGS selection (default set, or --only-segs override).
# ---------------------------------------------------------------------------

# All possible classify_segment() categories, in the fixed order used for
# the `counts` field so a zeroed counter is emitted for empty categories.
ALL_CATEGORIES = (
    "reusable",
    "stale",
    "blocked_needs_regeneration",
    "recoverable",
    "not_started",
    "human_escalation",
)

DEFAULT_ELIGIBLE_CATEGORIES = frozenset({"not_started", "recoverable", "stale"})


def parse_only_segs(raw: str) -> list:
    seen = set()
    ordered = []
    for part in raw.split(","):
        part = part.strip()
        if not part or part in seen:
            continue
        seen.add(part)
        ordered.append(part)
    return ordered


def select_default(classification: dict, candidates: list) -> list:
    return [seg for seg in candidates if classification[seg]["category"] in DEFAULT_ELIGIBLE_CATEGORIES]


def select_only_segs(only_segs: list, classification: dict):
    """Returns (segs, overrides, excluded) for the --only-segs path.

    - segs: the ids actually emitted into SEGS, in the requested order.
    - overrides: ids whose classification was human_escalation but were
      force-included anyway (the sole explicit override this flag grants).
    - excluded: ids named but NOT included, each with its category and why
      (reusable segments are not force-redone; blocked_needs_regeneration
      is never a manual-override target).
    """
    segs = []
    overrides = []
    excluded = []
    for seg in only_segs:
        category = classification[seg]["category"]
        if category in DEFAULT_ELIGIBLE_CATEGORIES:
            segs.append(seg)
        elif category == "human_escalation":
            segs.append(seg)
            overrides.append(seg)
        elif category == "reusable":
            excluded.append(
                {
                    "seg": seg,
                    "category": category,
                    "reason": "reusable segments are not force-redone by --only-segs",
                }
            )
        elif category == "blocked_needs_regeneration":
            excluded.append(
                {
                    "seg": seg,
                    "category": category,
                    "reason": "blocked_needs_regeneration is self-clearing, never a manual-override target",
                }
            )
        else:  # pragma: no cover -- defensive, every category is handled above
            excluded.append({"seg": seg, "category": category, "reason": "unrecognized category"})
    return segs, overrides, excluded


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def run(args, dirs: dict) -> dict:
    candidates = load_candidate_segments(dirs["manifest_path"])
    candidate_set = set(candidates)

    only_segs = None
    if args.only_segs is not None:
        only_segs = parse_only_segs(args.only_segs)
        for seg in only_segs:
            problem = validate_seg(seg)
            if problem is not None:
                fatal(f"--only-segs: unsafe segment id: {problem}")
        unknown = [seg for seg in only_segs if seg not in candidate_set]
        if unknown:
            fatal(
                f"--only-segs names {len(unknown)} id(s) not present in "
                f"manifest.json's segments[]: {', '.join(unknown)}"
            )

    merge_result = run_ledger_merge(dirs, args.durable_root, args.plugin_root)
    ledger_segments = load_ledger_segments(merge_result, dirs["durable_root"])

    classification = {
        seg: classify_segment(seg, ledger_segments, dirs, args.durable_root, args.plugin_root)
        for seg in candidates
    }
    observed_counts = Counter(entry["category"] for entry in classification.values())
    counts = {cat: observed_counts.get(cat, 0) for cat in ALL_CATEGORIES}

    # Aggregated per-category segment-id lists (candidate order), alongside
    # `counts`' per-category numbers -- the build spec's "classification
    # report" is explicitly counts + IDs per category + each stale
    # segment's own stale_reason (the latter already lives inline in
    # `classification`).
    ids_by_category: dict = {cat: [] for cat in ALL_CATEGORIES}
    for seg in candidates:
        ids_by_category[classification[seg]["category"]].append(seg)

    if only_segs is not None:
        segs, overrides, excluded_only_segs = select_only_segs(only_segs, classification)
        requested_display = only_segs
    else:
        segs = select_default(classification, candidates)
        overrides = []
        excluded_only_segs = []
        requested_display = candidates

    print(
        f"select_segments.py: requested={requested_display} emitted={segs}",
        file=sys.stderr,
    )

    if not segs and not args.allow_empty:
        fatal(
            "emitted SEGS is empty -- refusing to no-op silently. Pass "
            "--allow-empty to confirm a deliberately narrow rerun that "
            "selects nothing.",
            classification=classification,
            counts=counts,
            ids_by_category=ids_by_category,
        )

    # ---- #409 Step 1: refuse to silently re-translate converged work -------
    # The predicate is the DURABLE sentinel ledger_update.py raises when it
    # records convergence, never the ledger status. The status is overwritten
    # with `in_progress` BEFORE a re-dispatch, so a status-based check would
    # not fire on the very path this guards.
    #
    # Placed after the empty check so an empty selection still reports its own
    # dedicated error, and before the return so no caller can receive an
    # authorizing result it did not earn.
    authorizes_dispatch = not args.classify_only
    previously_converged = []
    if authorizes_dispatch:
        # resolve_dirs() exposes durable_root, not a segments_dir -- derive it
        # the same way draft_path_for/segpack_path do, so a --durable-root
        # redirect moves the sentinel lookup with everything else.
        segments_dir = dirs["durable_root"] / "segments"
        for seg in segs:
            if ever_converged_path(seg, segments_dir).exists():
                previously_converged.append(seg)

    if previously_converged and not args.allow_retranslate_converged:
        detail = []
        for seg in previously_converged:
            mismatched = classification.get(seg, {}).get("mismatched_fields") or []
            detail.append(f"{seg} (diverged: {', '.join(mismatched) or 'none recorded'})")

        # #409: the flag authorizes ONE thing and costs TWO. Everything above
        # is about converged work; but the same cache-key move that made those
        # segments stale also moves resume_setup.py's input_digest (the digest
        # domain is built FROM the per-segment cache keys), which mints a fresh
        # RUN_ID, which orphans the dispatch_token on every not-yet-converged
        # draft in this same selection -- so those retranslate too, discarding
        # any fix an operator applied by hand. Measured on a live project: 21
        # converged authorized, 21 in_progress silently lost, the unmentioned
        # half exactly the size of the half being asked about.
        #
        # Stated WITH its condition rather than as a certainty. The second loss
        # follows only if this dispatch actually mints a fresh RUN_ID, which it
        # will whenever a cache-key field moved -- the usual case here, since
        # that is what made these segments stale -- but NOT when the flag is
        # passed for an unrelated reason against an unchanged bundle. A warning
        # that overstates is one operators learn to skip past.
        not_yet_converged = [seg for seg in segs if seg not in set(previously_converged)]
        second_loss = ""
        if not_yet_converged:
            second_loss = (
                f" BEFORE AUTHORIZING, THE SECOND NUMBER: this selection also "
                f"holds {len(not_yet_converged)} not-yet-converged segment(s) "
                f"({', '.join(not_yet_converged)}). If this dispatch also mints "
                f"a fresh RUN_ID -- which it will whenever a cache-key field has "
                f"moved, the same cause that made the segments above stale -- "
                f"their existing drafts are orphaned by the new dispatch token "
                f"and they retranslate from scratch as well, discarding any fix "
                f"already applied by hand. This flag does not ask about those, "
                f"and nothing else will."
            )

        fatal(
            f"{len(previously_converged)} previously CONVERGED segment(s) would "
            f"be translated again: {'; '.join(detail)}. Refusing. A converged "
            f"segment becomes dispatch-eligible as soon as any cache-key field "
            f"moves (a plugin upgrade moves plugin_bundle_hash for every "
            f"segment at once), so this would discard finished work without "
            f"anyone asking for it. Pass --allow-retranslate-converged to "
            f"authorize exactly this dispatch, or --classify-only if you only "
            f"need the classification and will not translate." + second_loss,
            classification=classification,
            counts=counts,
            ids_by_category=ids_by_category,
            previously_converged=previously_converged,
            # Machine-readable counterpart to `second_loss` above, so a caller
            # (and its test) can assert the exact set rather than grep prose.
            not_yet_converged=not_yet_converged,
        )

    # ---- #409 Step 3: refuse when a prior run dispatched WITHOUT the -------
    # ---- resume-integrity gate ---------------------------------------------
    # SKILL.md's W5 tells the orchestrating session to run resume_setup.py
    # before the Workflow launches; the driver's own resolve_run_id() does it
    # unconditionally. Neither fact was ever CHECKED, and a real project ran
    # six consecutive batches with the step skipped entirely -- hand-labelled
    # run ids, not one `input.digest` on disk, and nothing noticed. The defect
    # this closes is that INVISIBILITY, not the skip.
    #
    # The discriminator is a set difference over evidence that already exists:
    # the RUN_IDs that actually dispatched work (each draft's own
    # `dispatch_token`) MINUS the RUN_IDs the gate demonstrably ran for (each
    # `runs/<RUN_ID>/input.digest`). Three states fall out of it without a
    # special case for any of them:
    #
    #   gate ran       -- drafts exist, every one of their run ids has a digest
    #   gate skipped   -- a run id dispatched drafts and has no digest
    #   first run ever -- no tokened draft exists, so the left-hand set is
    #                     empty and the difference is empty. A brand-new
    #                     project is not a skipped gate, and does not need to
    #                     be special-cased into not being one.
    #
    # Scanned over ALL drafts, never over `segs`. Scoping the scan to the
    # current selection would make a HISTORY question depend on which segments
    # happen to be eligible right now -- the exact selection-dependence bug
    # class that made resume_setup.py's own digest domain unstable (#392).
    #
    # The scan runs unconditionally and its result is always reported, but only
    # an AUTHORIZING invocation refuses: --classify-only must stay a pure read
    # (final_audit.py's completeness gate calls it and must never start
    # refusing). Scanning only when authorizing would make `runs_missing_digest`
    # an empty list under --classify-only -- indistinguishable from a clean
    # project, which is the "absence and failure print identically" failure
    # this whole check exists to stop reproducing.
    runs_dir = dirs["durable_root"] / "runs"
    dispatch_scan = scan_dispatching_run_ids(dirs["durable_root"] / "segments")
    workflow_run_ids = scan_workflow_run_ids(runs_dir)
    # The UNION of both evidence halves -- neither subsumes the other; see
    # scan_workflow_run_ids()'s own docstring for the three cases and for why
    # a draft-only scan structurally cannot see a run whose drafts were later
    # overwritten.
    evidence: dict = {}
    for run_id in dispatch_scan["by_run_id"]:
        evidence.setdefault(run_id, []).append("drafts")
    for run_id in workflow_run_ids:
        evidence.setdefault(run_id, []).append("workflow_dir")

    # Security fix: validate EVERY run id in the union before either path
    # function below is ever called with it. The workflow-derived half is
    # already shape-filtered by scan_workflow_run_ids() itself (a
    # runs/workflows/ entry that doesn't match _RUN_ID_DIR_RE is silently
    # skipped, never added to `workflow_run_ids` at all), but the
    # draft-derived half is not: draft_run_id() only ever splits on the first
    # colon, by design -- see its own docstring -- and never validates what
    # it returns. This is the ONE choke point both halves pass through
    # before `input_digest_path()`/`resume_gate_ack_path()` ever splice a
    # run id into a filesystem path, so neither scanner's own shape can
    # drift out from under it.
    unsafe_run_ids = {}
    safe_evidence = {}
    for run_id, sources in evidence.items():
        problem = validate_run_id(run_id)
        if problem is not None:
            unsafe_run_ids[run_id] = problem
        else:
            safe_evidence[run_id] = sources

    runs_acknowledged_pre_gate = sorted(
        run_id
        for run_id in safe_evidence
        if not input_digest_path(run_id, runs_dir).is_file()
        and resume_gate_ack_path(run_id, runs_dir).exists()
    )
    runs_missing_digest = sorted(
        run_id
        for run_id in safe_evidence
        if not input_digest_path(run_id, runs_dir).is_file()
        and not resume_gate_ack_path(run_id, runs_dir).exists()
    )

    # An unsafe run id must neither silently vanish (that would reintroduce
    # exactly the "gate passes when it should refuse" failure #409 Step 3
    # exists to close -- a traversing id that happens to resolve onto some
    # unrelated existing input.digest would otherwise read as gated) nor
    # point the operator at a remedy that cannot work. This gets its OWN
    # refusal rather than folding into `runs_missing_digest` below, because
    # backfill_resume_gate_ack.py validates the IDENTICAL shape (its own
    # validate_run_id(), matched to this one on purpose) and would refuse
    # these same id(s) too -- recommending it here, the way the
    # runs_missing_digest refusal recommends it below, would send the
    # operator into a dead end: refuse -> --apply -> refuse again, through
    # neither script. Gated on `authorizes_dispatch`, the same as
    # runs_missing_digest below, so --classify-only stays a pure read
    # (final_audit.py's completeness gate calls it and must never start
    # refusing) while still reporting `unsafe_run_ids` on the success path
    # for that caller to see. The message names the one remedy that DOES
    # exist: fix or remove the offending artifact by hand.
    if unsafe_run_ids and authorizes_dispatch:
        detail = "; ".join(
            f"{run_id!r} ({problem}) [evidence: {'+'.join(evidence[run_id])}]"
            for run_id, problem in sorted(unsafe_run_ids.items())
        )
        fatal(
            f"{len(unsafe_run_ids)} RUN_ID(s) found in this project's own "
            f"evidence (a draft's dispatch_token, or a runs/workflows/ "
            f"directory name) do not match the safe RUN_ID shape "
            f"resume_setup.py itself only ever generates: {detail}. Refusing "
            f"-- a run id this malformed is never turned into a "
            f"runs/<RUN_ID>/ filesystem lookup, because one containing '..' "
            f"could escape the durable root and one starting with '/' would "
            f"discard runs_dir entirely (Path('runs') / '/etc' == "
            f"Path('/etc')). This is NOT the same as a run that merely "
            f"predates the gate: backfill_resume_gate_ack.py validates the "
            f"identical shape and would refuse these same id(s) too, so "
            f"running it here would not help. There is no automated remedy "
            f"-- find and fix (or remove) the offending "
            f"segments/<seg>.draft.json's dispatch_token, or the "
            f"runs/workflows/<RUN_ID>/ directory, by hand, then rerun.",
            classification=classification,
            counts=counts,
            ids_by_category=ids_by_category,
            unsafe_run_ids=unsafe_run_ids,
            # Computed over `safe_evidence` only (never over an unsafe id --
            # no path was built for one), but included here so this failure
            # shape carries the same key set as the runs_missing_digest one
            # below, and as the success path.
            runs_missing_digest=runs_missing_digest,
            runs_acknowledged_pre_gate=runs_acknowledged_pre_gate,
            run_id_evidence={k: evidence[k] for k in sorted(evidence)},
            dispatching_run_ids=sorted(dispatch_scan["by_run_id"]),
            workflow_run_ids=workflow_run_ids,
            drafts_scanned=dispatch_scan["drafts_scanned"],
        )

    if runs_missing_digest and authorizes_dispatch:
        detail = "; ".join(
            f"{run_id} ({len(dispatch_scan['by_run_id'].get(run_id, []))} draft(s), "
            f"evidence: {'+'.join(evidence[run_id])})"
            for run_id in runs_missing_digest
        )
        fatal(
            # Audit-accuracy fix: this used to say every listed id
            # "dispatched work" unconditionally. A workflow_dir-only id
            # proves INSTANTIATION, never dispatch (scan_workflow_run_ids()'s
            # own docstring documents "instantiated and dispatched nothing"
            # as a legitimate shape) -- so the summary now names both
            # possibilities and leaves WHICH applies to each id to the
            # per-id `detail` above, which already carries its own draft
            # count and evidence tag.
            f"{len(runs_missing_digest)} prior RUN_ID(s) show evidence of "
            f"having dispatched work and/or had a Workflow template "
            f"instantiated in this project without the resume-integrity "
            f"gate having run for them: {detail}. "
            f"Refusing. resume_setup.py writes runs/<RUN_ID>/input.digest "
            f"before any dispatch, and these run ids have no digest -- so "
            f"whatever each one actually did in this project was never "
            f"checked against the inputs it consumed, and no later run can "
            f"safely resume it. There is deliberately NO flag to wave this "
            f"through: run "
            f"backfill_resume_gate_ack.py --apply to record, per run id, that "
            f"these predate the gate (it never fabricates an input.digest -- "
            f"an honest acknowledgement of a gap, not a forged proof). A run "
            f"id acknowledged there stops blocking; a NEWLY skipped run has a "
            f"new id and is refused again.",
            classification=classification,
            counts=counts,
            ids_by_category=ids_by_category,
            runs_missing_digest=runs_missing_digest,
            # The same provenance the success path reports. An operator
            # triaging a refusal needs to know WHICH half fired for each run
            # id -- and a refusal that reported less than the success path
            # would make the failing case the harder one to diagnose.
            run_id_evidence={k: evidence[k] for k in sorted(evidence)},
            dispatching_run_ids=sorted(dispatch_scan["by_run_id"]),
            workflow_run_ids=workflow_run_ids,
            drafts_scanned=dispatch_scan["drafts_scanned"],
            # Empty here by construction -- a non-empty unsafe_run_ids would
            # already have fataled above, before this check ever runs. Kept
            # in the payload anyway so every failure shape carries the same
            # key set.
            unsafe_run_ids=unsafe_run_ids,
        )

    return {
        "success": True,
        "durable_root": str(dirs["durable_root"]),
        "segs": segs,
        "requested_only_segs": only_segs,
        "classification": classification,
        "counts": counts,
        "ids_by_category": ids_by_category,
        "overrides": overrides,
        "excluded_only_segs": excluded_only_segs,
        # #409: a consumer must be able to tell an authorizing result from a
        # merely descriptive one without re-deriving which flags were passed.
        "authorizes_dispatch": authorizes_dispatch,
        "previously_converged": previously_converged,
        # #409 Step 3. Machine-readable evidence, deliberately reported even
        # on the success path: a caller (and a test) must be able to assert the
        # EXACT set this scan produced, never merely that the run passed. A
        # check whose scan silently found nothing and one that genuinely found
        # nothing return the same verdict -- `drafts_scanned` is what separates
        # them. `runs_missing_digest` is non-empty here only under
        # --classify-only (an authorizing invocation with a non-empty set has
        # already refused above).
        "runs_missing_digest": runs_missing_digest,
        "runs_acknowledged_pre_gate": runs_acknowledged_pre_gate,
        # Security fix: run ids from either evidence half that failed
        # validate_run_id() -- {run_id: reason}. Never fed into
        # input_digest_path()/resume_gate_ack_path(). Non-empty here only
        # under --classify-only (an authorizing invocation with a non-empty
        # set has already refused above, matching runs_missing_digest's own
        # documented shape).
        "unsafe_run_ids": unsafe_run_ids,
        "dispatching_run_ids": sorted(dispatch_scan["by_run_id"]),
        "workflow_run_ids": workflow_run_ids,
        # Provenance per run id ("drafts", "workflow_dir", or both). An
        # operator triaging a refusal needs to know which half fired: a
        # drafts hit is proof work was dispatched, a workflow_dir-only hit
        # proves the template was instantiated without the gate and the
        # drafts have since been overwritten (or nothing was dispatched).
        "run_id_evidence": {k: evidence[k] for k in sorted(evidence)},
        "drafts_scanned": dispatch_scan["drafts_scanned"],
        "drafts_untokened": dispatch_scan["drafts_untokened"],
    }


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "W5 mass-translate preflight: classify every manifest segment "
            "as reusable/stale/blocked_needs_regeneration/recoverable/"
            "not_started/human_escalation and emit the dispatch set SEGS."
        )
    )
    parser.add_argument(
        "--only-segs",
        default=None,
        metavar="SEG1,SEG2,...",
        help=(
            "Comma-separated explicit segment id list. Intersects the "
            "emitted SEGS with this list instead of the full eligible set, "
            "and is also the sole mechanism for retrying a "
            "human_escalation segment (an explicit, auditable override, "
            "logged as such)."
        ),
    )
    parser.add_argument(
        "--allow-empty",
        action="store_true",
        help="Do not fatally error if the emitted SEGS is empty.",
    )
    parser.add_argument(
        "--allow-retranslate-converged",
        action="store_true",
        help=(
            "#409: permit dispatching segments that have ALREADY converged at "
            "least once. Without this flag such a selection is refused, "
            "because a moved plugin_bundle_hash marks every converged segment "
            "'stale' and stale is dispatch-eligible by default -- which would "
            "silently re-translate finished, paid-for work. Naming this flag "
            "is the authorization; it does not delete the durable sentinel."
        ),
    )
    parser.add_argument(
        "--classify-only",
        action="store_true",
        help=(
            "#409: produce the classification report WITHOUT authorizing a "
            "dispatch. For consumers that need the categories but never "
            "translate anything (final_audit.py's completeness gate). The "
            "previously-converged refusal does not apply, because nothing "
            "downstream of this call can dispatch; the emitted 'segs' is "
            "reported as usual and 'authorizes_dispatch' is false."
        ),
    )
    parser.add_argument(
        "--durable-root",
        default=None,
        metavar="PATH",
        help=(
            "LT-409: use PATH as the DATA root instead of this script's own "
            "self-anchored location -- replaces where manifest.json (and "
            "the ledger_merge.py/cache_key.py subprocesses' own data) are "
            "found, forwarded down the subprocess chain as their own "
            "--durable-root. Optional; omit for today's self-anchored "
            "behavior. Independent of --plugin-root below -- this flag "
            "never affects where the SIBLING SCRIPTS themselves are found."
        ),
    )
    parser.add_argument(
        "--plugin-root",
        default=None,
        metavar="PATH",
        help=(
            "LT-409: use PATH (the plugin's own install root, i.e. "
            "{{PLUGIN_ROOT}}) to resolve the sibling ledger_merge.py/"
            "cache_key.py scripts this script shells out to, as "
            "{PATH}/assets/scripts/<name>.py -- deliberately NEVER derived "
            "from --durable-root, because ${durable_root}/scripts/ is "
            "writable by the codex process these scripts gate (codex_job.py "
            "grants --write over the whole durable root), so resolving a "
            "checker from inside the thing it checks would let a tampered "
            "copy pass itself. Passed on only to ledger_merge.py, which "
            "resolves a further sibling of its own; the leaf cache_key.py "
            "does not accept this flag and receives only --durable-root. "
            "Optional; omit for today's self-anchored sibling lookup."
        ),
    )
    return parser


def main(argv=None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    try:
        dirs = resolve_dirs(args.durable_root, args.plugin_root)
        result = run(args, dirs)
    except FatalError as exc:
        print(str(exc), file=sys.stdout)
        return 1
    except Exception as exc:  # pragma: no cover -- defensive catch-all
        print(
            json.dumps({"success": False, "error": f"unexpected error: {exc}"}, ensure_ascii=False),
            file=sys.stdout,
        )
        return 1

    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
