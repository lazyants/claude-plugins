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
 "overrides": [...], "excluded_only_segs": [...], "claims": {seg: {...}}}.
 `counts` and `ids_by_category` are keyed by the same six ALL_CATEGORIES,
 one the per-category tally and the other the per-category segment-id list
 (each stale segment's own `stale_reason` lives inline in `classification`)
 -- together this is the "classification report" the build spec requires
 (counts + IDs per category + stale_reason). `claims` (#438) is always
 present, empty {} unless --from-converged/--from-cap were given -- see the
 claim admission gate section below for its per-id shape and each
 profile's closed condition list.
Failure: {"success": false, "error": ...}. Exit 0 on success, 1 on any
fatal condition -- callers should read stdout, not rely on the exit code
alone.
"""

import argparse
import errno
import hashlib
import json
# `os` is used by exactly one thing here: the shared sentinel predicate's
# `dir_fd` branch. That branch is unreachable from this script (nothing here
# holds a directory descriptor, so it always passes None), but the predicate
# is pinned BYTE-IDENTICAL across four scripts by
# tests/select_segments.test.py::test_sentinel_predicate_is_identical_in_all_
# four_scripts -- so the import has to exist wherever the copy does, or the
# branch would raise NameError instead of the OSError its contract promises.
import os
import re
import stat
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import NoReturn

# #438's claim admission gate needs claim_record.py -- a flat sibling
# import, the same idiom scaffold_setup.py already uses for `import
# cache_key`, rather than a duplicated copy, because claim_record.py's own
# module docstring is explicit that its read discipline (the three-state
# predicate, AMBIGUOUS mapping to "do not claim") must be SHARED, not
# reimplemented per reader: "the identical shape that produced the 1.19.1
# sentinel data-loss bug." Deliberately NOT imported at module level, unlike
# scaffold_setup.py's `import cache_key`: scaffold_setup.py always runs from
# a tree where cache_key.py is guaranteed present, while this script is
# invoked by every caller that merely wants a classification report, most of
# which never touch a claim at all -- see _import_claim_record() below for
# where and why it loads.

# ---------------------------------------------------------------------------
# Self-anchoring
# ---------------------------------------------------------------------------

SCRIPTS_DIR = Path(__file__).resolve().parent
DURABLE_ROOT = SCRIPTS_DIR.parent

MANIFEST_PATH = DURABLE_ROOT / "manifest.json"
LEDGER_MERGE_SCRIPT = SCRIPTS_DIR / "ledger_merge.py"
CACHE_KEY_SCRIPT = SCRIPTS_DIR / "cache_key.py"
# #438: the two leaf checkers the claim admission gate shells out to for
# S1/S2 (PLAN.md D2) -- resolved the SAME way as CACHE_KEY_SCRIPT, since both
# are leaves with no siblings of their own (see draft_ready.py's and
# validate_draft.py's own module docstrings: "this script is a LEAF").
DRAFT_READY_SCRIPT = SCRIPTS_DIR / "draft_ready.py"
VALIDATE_DRAFT_SCRIPT = SCRIPTS_DIR / "validate_draft.py"


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
        draft_ready_script = DRAFT_READY_SCRIPT
        validate_draft_script = VALIDATE_DRAFT_SCRIPT
    else:
        plugin_scripts_dir = Path(plugin_root_str).resolve() / "assets" / "scripts"
        ledger_merge_script = plugin_scripts_dir / "ledger_merge.py"
        cache_key_script = plugin_scripts_dir / "cache_key.py"
        # #438: same --plugin-root-aware resolution as cache_key_script --
        # both are leaves, resolved the identical way.
        draft_ready_script = plugin_scripts_dir / "draft_ready.py"
        validate_draft_script = plugin_scripts_dir / "validate_draft.py"

    return {
        "durable_root": durable_root,
        "manifest_path": manifest_path,
        "ledger_merge_script": ledger_merge_script,
        "cache_key_script": cache_key_script,
        "draft_ready_script": draft_ready_script,
        "validate_draft_script": validate_draft_script,
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

    Doubled-path fix: both flags are always forwarded as their RESOLVED
    value, never the raw CLI string. The sibling subprocess runs with `cwd`
    set to the resolved `dirs["durable_root"]` (see run_ledger_merge()'s own
    `subprocess.run(..., cwd=...)`), and the sibling's own resolve_dirs()
    does `Path(durable_root_str).resolve()` -- which resolves a RELATIVE
    fragment against ITS cwd. Forwarding the raw string when it happened to
    be relative (e.g. `--durable-root projects/book` run from `/repo`)
    resolved it a SECOND time against the already-resolved value, landing
    the sibling on `/repo/projects/book/projects/book` instead of
    `/repo/projects/book` -- silently: run_ledger_merge()'s own
    success/failure check only sees whether the subprocess printed valid
    JSON with `"success": true`, never which tree it actually read. The
    identical shape was independently confirmed (and fixed) in
    resume_setup.py and segment_dispatch_driver.py; --plugin-root had the
    same class of defect for a related reason -- a relative value forwarded
    raw resolves against the CHILD's cwd (durable_root), not the ORIGINAL
    invoker's cwd it was resolved against here, so the two processes could
    silently land on two DIFFERENT plugin roots. Every existing caller
    already passes an absolute path for both flags (`Path(absolute).resolve()`
    is a no-op), so this was unreachable until an operator passed a relative
    override; self-anchored behavior (both flags omitted) is untouched --
    the condition for forwarding each flag at all is unchanged, only the
    VALUE forwarded when it is."""
    args = []
    if durable_root_str is not None or plugin_root_str is not None:
        args += ["--durable-root", str(dirs["durable_root"])]
    if plugin_root_str is not None:
        args += ["--plugin-root", str(Path(plugin_root_str).resolve())]
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

    The filename convention is stated in FOUR scripts (this one,
    ledger_update.py, final_audit.py, backfill_ever_converged.py), restated
    rather than imported for the bundle-hash reason spelled out in
    classify_ever_converged_sentinel() below -- NOT for the "no shared lib
    between self-contained scripts" convention, which is already false in
    this codebase. tests/select_segments.test.py's
    test_sentinel_filename_matches_the_writer_in_ledger_update pins the name
    against the writer's, and
    test_exactly_these_four_scripts_participate_in_the_sentinel_contract
    pins the population at four -- drift tests, not a second source of
    truth."""
    return segments_dir / f".ever_converged.{seg}"


# ---------------------------------------------------------------------------
# The shared sentinel-presence predicate. This block is an EXACT duplicate of
# the copy in the other three sentinel scripts (search `SENTINEL_ABSENT` in
# ledger_update.py, final_audit.py and backfill_ever_converged.py) -- see
# classify_ever_converged_sentinel()'s docstring for why it is duplicated
# rather than imported, and which test pins the four copies together.
# ---------------------------------------------------------------------------

SENTINEL_ABSENT = "absent"
SENTINEL_PRESENT = "present"
SENTINEL_AMBIGUOUS = "ambiguous"


def _sentinel_entry_kind(mode: int) -> str:
    """A human name for the st_mode of whatever occupies a sentinel path --
    it goes straight into an operator-facing message, which has to say what
    is actually sitting there before it can ask anyone to fix it."""
    if stat.S_ISLNK(mode):
        return "a symbolic link"
    if stat.S_ISDIR(mode):
        return "a directory"
    if stat.S_ISFIFO(mode):
        return "a FIFO"
    if stat.S_ISSOCK(mode):
        return "a socket"
    if stat.S_ISBLK(mode):
        return "a block device"
    if stat.S_ISCHR(mode):
        return "a character device"
    return f"a non-regular entry (st_mode {stat.S_IFMT(mode):#o})"


def classify_ever_converged_sentinel(path, *, dir_fd=None) -> "tuple[str, str]":
    """Three-state classification of the `.ever_converged.<seg>` entry at
    `path`: `(SENTINEL_ABSENT|SENTINEL_PRESENT|SENTINEL_AMBIGUOUS, detail)`.

    THE SHARED PREDICATE. Every script that asks whether a segment has ever
    converged calls this, and all four must agree on it:
    ledger_update.py's `mark_ever_converged()` (the only writer),
    select_segments.py's #409 Step 1 dispatch gate,
    final_audit.py's `count_stale_previously_converged()` carve-out, and
    backfill_ever_converged.py's `already_sentineled` scan.

    DUPLICATED RATHER THAN IMPORTED because importing it would be a live
    hazard -- NOT because of the "no shared lib between self-contained
    scripts" convention, which is already false here (canon_validate.py and
    glossary_batch_plan.py import canon_senses.py; scaffold_setup.py imports
    cache_key.py). The real reason: ledger_update.py is a
    PLUGIN_BUNDLE_MEMBERS entry, and cache_key.py:100-107 records that that
    tuple is a literal byte-hash allowlist to which a TRANSITIVE IMPORT IS
    INVISIBLE -- which is why canon_senses.py had to be registered
    explicitly once two members imported it. A shared module would put this
    predicate's bytes outside the hash meant to cover them, so WEAKENING
    this guard would no longer move plugin_bundle_hash, and every durable
    root scaffolded beforehand would go on trusting it: the exact
    false-green cache_key.py:114-118 names. Consolidation stays possible --
    it just has to register the new module in PLUGIN_BUNDLE_MEMBERS in the
    same commit.

    What keeps the four copies honest is ENFORCEMENT, not discipline. A
    remembered convention rots -- this docstring's own first version cited
    the false one -- while a test that fails loudly does not.
    tests/select_segments.test.py's
    test_sentinel_predicate_is_identical_in_all_four_scripts pins the copies
    byte for byte and across the state matrix; its
    test_exactly_these_four_scripts_participate_in_the_sentinel_contract
    fails when a fifth copy appears or one of the four goes away.

    Why three states, and why not `Path.exists()`. `exists()` answers the
    wrong question three ways, and NOT all of them in the same direction --
    an earlier draft of this docstring said "twice over, and BOTH point at
    absent", which is the claim the CHANGELOG had to correct. Two of the
    three do point at "absent", and that is the direction that authorizes
    destroying converged work:

      1. It FOLLOWS symlinks, so a DANGLING symlink named as the sentinel
         reads as absent -- while the writer's `os.open(O_CREAT|O_EXCL)` gets
         EEXIST from that same symlink and reports the segment successfully
         marked. That split is the whole finding: a segment recorded as
         converged that the gate then sees as unprotected and retranslates.
         Verified on this project's Python (3.14.6): `exists()` -> False,
         `os.open` -> FileExistsError, for one and the same dangling link.
      2. Since Python 3.13 `exists()` swallows EVERY OSError and returns
         False, so an EACCES/ESTALE/EIO on the lookup is reported as "this
         segment never converged". Verified on 3.14.6: with an unreadable
         parent directory `exists()` returns False while `lstat()` raises
         EACCES. (On 3.8-3.12 the same call re-raised for EACCES but still
         swallowed ELOOP/ENOTDIR/EBADF -- so no supported version answers
         this correctly, and the version-dependence is itself a reason not
         to route a data-loss guard through `exists()`.)
      3. In the OTHER direction: a DIRECTORY at the marker's path is
         `exists() == True`, so `exists()` reports converged a segment the
         writer never marked. That one cannot destroy finished work, which is
         why it went unnoticed -- but it is the reason "exists() at least
         fails safe in one direction" is false, and the reason the fix is a
         third state rather than a flipped default.

    So: only ENOENT means absent, and it is determined by catching
    FileNotFoundError rather than by comparing `exc.errno`, so the verdict
    never depends on an errno that may be None. `lstat`, deliberately not
    `stat` -- a symlink is not something `mark_ever_converged()` can have
    (its O_CREAT|O_EXCL open refuses to write through one), so following a
    link would only ask the question about some unrelated file. Either way
    only the final `.ever_converged.<seg>` component is left unresolved:
    WITHOUT `dir_fd` the PARENT components still resolve normally, so a
    project whose whole `segments/` directory is a symlink is unaffected;
    WITH `dir_fd` there are no parent components left to resolve, because
    the caller already resolved them once, when it opened the descriptor.

    `dir_fd` -- OPTIONAL, and today exactly one caller passes it:
    backfill_ever_converged.py's census. Omitted (every other caller), the
    lookup resolves the whole pathname afresh, which is the right thing for
    a reader that holds nothing open. Passed, the BASENAME is looked up
    relative to that descriptor instead, and `segments/` is not resolved by
    pathname at all. The difference matters only for a caller that already
    HOLDS the directory open and acts on its census afterwards, which is
    exactly that one: it opens `segments/` once, does every write relative
    to the descriptor, and samples directory identity at the end. A census
    resolving the pathname afresh could therefore classify entries in a
    DIFFERENT directory than the one being written to -- re-point
    `segments/` at B for the length of the census and back to A before the
    run ends, and B's sentinel is reported as A's protection while the
    final identity sample compares A to A and agrees. Reproduced by review,
    not theorised. Binding the census to the descriptor removes that
    interleaving with no locking protocol at all, because the descriptor is
    already held; a caller that holds none gains nothing here and passes
    None.

    Anything that is neither ENOENT nor a regular file is AMBIGUOUS: it MAY
    be a converged segment whose sentinel this process cannot see. Each
    caller then maps AMBIGUOUS to ITS OWN work-preserving side, and that is
    deliberately NOT the same action in all four: the writer and the
    dispatch gate REFUSE (never destroy or mis-record converged work), while
    final_audit.py's carve-out COUNTS it (never declare a converged book
    incomplete and therefore undeliverable) and backfill's scan reports it
    unprotected (never claim protection it did not verify). One predicate,
    four deliberate mappings -- see each call site's own comment. The
    asymmetry is the reason a false "absent" is the unacceptable answer
    everywhere: it costs a finished translation, or a finished book.
    """
    try:
        # `path.name` is the basename and the descriptor is its parent, so
        # the `dir_fd` branch resolves no part of `segments/` by pathname.
        # `os.lstat` keeps `follow_symlinks` off exactly as `Path.lstat`
        # does, so the FINAL component stays unresolved either way and both
        # branches raise the same exceptions into the same handlers below.
        st = path.lstat() if dir_fd is None else os.lstat(path.name, dir_fd=dir_fd)
    except FileNotFoundError:
        return (SENTINEL_ABSENT, "")
    except OSError as exc:
        # `OSError.errno` is typed `int | None` and genuinely can be None. A
        # missing errno is the LEAST informative failure there is, so it
        # lands on the ambiguous side like every other non-ENOENT outcome --
        # never silently treated as "some other errno", and above all never
        # as absence. The ENOENT verdict above does not consult `errno` at
        # all (FileNotFoundError IS ENOENT by construction), so a None errno
        # can never reach it, which is why this branch can be a plain guard
        # rather than a three-way comparison.
        if exc.errno is None:
            return (SENTINEL_AMBIGUOUS, f"lstat failed with no errno: {exc}")
        code = errno.errorcode.get(exc.errno, f"errno {exc.errno}")
        return (SENTINEL_AMBIGUOUS, f"lstat failed with {code}: {exc.strerror or exc}")
    if stat.S_ISREG(st.st_mode):
        return (SENTINEL_PRESENT, "")
    return (
        SENTINEL_AMBIGUOUS,
        f"the entry is {_sentinel_entry_kind(st.st_mode)}, not a regular file",
    )


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
    siblings of its own): `--durable-root` is forwarded whenever EITHER was
    given, as the RESOLVED `durable_root` -- never the raw `durable_root_str`
    (doubled-path fix, the identical shape and reason as
    `_root_forward_args()`'s own docstring: the subprocess runs with `cwd`
    set to this SAME resolved `durable_root`, and cache_key.py's own
    resolve_dirs() does `Path(durable_root_str).resolve()`, which would
    resolve a RELATIVE raw string a second time against that already-resolved
    cwd). When `durable_root_str` is NOT given but `plugin_root_str` IS
    (meaning `cache_key_script` was itself resolved via --plugin-root, so it
    no longer physically sits under durable_root), forwarding `durable_root`
    is exactly as necessary as when it is given -- otherwise cache_key.py's
    own self-anchoring would silently resolve its data from the plugin root
    instead of the real durable root. Both branches now converge on the same
    forwarded value; only whether to forward at all still depends on which
    of the two was given.
    """
    if not cache_key_script.is_file():
        return f"cache_key.py not found at {cache_key_script}"
    if durable_root_str is not None or plugin_root_str is not None:
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
# #438 -- the claim admission gate. Authorizes RE-REVIEWING an already-
# dispatched draft under one of two closed, per-profile condition lists
# (PLAN.md's D1/D2), without ever re-translating it. See PLAN.md (D1-D6, D9,
# D10) and POPULATIONS.md for the full design. This section implements the
# SELECTOR half only: it validates admission and WRITES the durable claim
# record (claim_record.py), then reports the authorization in this script's
# own JSON output for the driver to consume (D3). It never rewrites a
# draft's own dispatch_token -- claim_record.py's own module docstring:
# "This record has two readers (the selector admits, the driver acts)".
# ---------------------------------------------------------------------------

def _import_claim_record():
    """Lazy sibling import of claim_record.py -- deferred until a claim is
    actually requested, per the module-level comment above. Self-anchored
    the same way `import cache_key` resolves for scaffold_setup.py: Python
    adds a directly-run script's own directory to sys.path[0], so this
    finds claim_record.py beside THIS script wherever it is currently
    running from (self-anchored durable-root copy or --plugin-root install
    tree) -- both are ordinary Step 0a bundle members that always travel
    together, so the only way this import fails is a genuinely broken
    install, which is a whole-run FATAL, never a per-id one.

    THE FALLBACK BELOW IS NOT A SECOND GUESS AT WHERE THE SIBLING LIVES --
    it resolves the SAME location, without consulting sys.path at all.
    sys.path[0] is this script's own directory only when this script was
    RUN; when it is LOADED as a module by path instead
    (importlib.util.spec_from_file_location -- how every test that
    exercises rewrite_draft_dispatch_token() as a unit gets at it, and the
    idiom segment_dispatch_driver.py deliberately uses for this very
    sibling in production), sys.path[0] belongs to whoever did the loading
    and a bare `import claim_record` resolves against a tree that has
    nothing to do with this file. SCRIPTS_DIR is
    `Path(__file__).resolve().parent` -- literally "beside THIS script",
    the thing the paragraph above already claims -- so the fallback makes
    that claim TRUE under both invocation modes rather than widening what
    may be imported. It matters now and did not before: the sibling used to
    be needed only inside run()'s claim block (always reached by a real
    subprocess invocation), and is now needed by
    rewrite_draft_dispatch_token() for its directory fsync, which is called
    directly as a unit.

    Deliberately NOT registered in sys.modules under `claim_record`: a
    process that has already bound that name to some other copy must keep
    it, since the bare import above would have found that copy first
    anyway, and a fallback that silently rebinds it would make WHICH copy
    is in force depend on which caller happened to run first.

    A missing or unloadable sibling stays a whole-run FATAL, from whichever
    attempt ran last -- the fallback never converts a broken install into a
    per-segment failure."""
    try:
        import claim_record

        return claim_record
    except ImportError:
        pass
    # Local import on this one recovery path: the module-level import list
    # is deliberately minimal (see the note on `os` at the top of this
    # file), and nothing on the ordinary path needs importlib.
    import importlib.util

    path = SCRIPTS_DIR / "claim_record.py"
    spec = importlib.util.spec_from_file_location("claim_record", str(path))
    if spec is None or spec.loader is None:
        fatal(f"claim_record.py could not be imported (expected beside this script at {path})")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except (OSError, ImportError, SyntaxError, ValueError) as exc:
        fatal(
            f"claim_record.py could not be imported (expected beside this script at "
            f"{path}): {exc}"
        )
    return module


# Exactly two profiles -- a third (--from-incomplete, for P3) was proposed in
# an earlier revision and DELETED after codex round 2 showed no implementable
# condition separates a stalled unit from ordinary live work (PLAN.md D2's
# "P3" section). Never reintroduce a third.
CLAIM_PROFILE_FROM_CONVERGED = "from-converged"
CLAIM_PROFILE_FROM_CAP = "from-cap"
CLAIM_PROFILES = (CLAIM_PROFILE_FROM_CONVERGED, CLAIM_PROFILE_FROM_CAP)

# final_audit.py's own SAFE_STALE_CARVEOUT_FIELDS, restated per this
# project's "no shared lib between self-contained scripts" convention (this
# file already restates CACHE_KEY_FIELDS/DERIVATION_STATE_FIELDS the same
# way). A cache-key field in this set can only ever be MACHINERY (plugin
# bytes, schema shape, derivation-bundle bytes), never prose -- D6 records a
# moved field here as a REPORTING distinction on a claim, NEVER an admission
# condition (decision 5: no moved cache-key field, machinery or not, refuses
# a --from-converged claim).
MACHINERY_ONLY_CACHE_KEY_FIELDS = frozenset(
    {"plugin_bundle_hash", "schema_hash", "derivation_bundle_hash"}
)


def _claim_now_iso8601() -> str:
    """Byte-for-byte the same format as ledger_update.py's own now_iso8601()."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_claim_requests(args) -> "dict[str, str]":
    """Combines --from-converged/--from-cap into {seg: profile}. FATAL when
    an id is named under BOTH -- an ambiguous profile is not a decision this
    script may resolve silently: D2's two profiles are closed condition
    lists over DIFFERENT populations, and a unit satisfying both is a design
    error the operator must resolve by naming it under exactly one."""
    requests: dict = {}
    collisions = set()
    for flag_name, profile, raw in (
        ("--from-converged", CLAIM_PROFILE_FROM_CONVERGED, args.from_converged),
        ("--from-cap", CLAIM_PROFILE_FROM_CAP, args.from_cap),
    ):
        if raw is None:
            continue
        for seg in parse_only_segs(raw):
            problem = validate_seg(seg)
            if problem is not None:
                fatal(f"{flag_name}: unsafe segment id: {problem}")
            if seg in requests and requests[seg] != profile:
                collisions.add(seg)
            requests[seg] = profile
    if collisions:
        fatal(
            f"{len(collisions)} segment id(s) were named under BOTH --from-converged "
            f"and --from-cap: {', '.join(sorted(collisions))}. Each id must be claimed "
            f"under exactly one profile -- naming it under both is not a decision this "
            f"script may resolve silently."
        )
    return requests


def _run_leaf_gate(script_path: Path, seg: str, durable_root: Path, label: str):
    """Runs a leaf checker script (validate_draft.py / draft_ready.py) as
    `<script> <seg> --durable-root <durable_root>` -- the same subprocess
    shape this file already uses for cache_key.py. Returns (True, "") on
    exit 0, (False, detail) otherwise.

    A per-id failure here becomes THIS id's own claim-admission reason,
    never a whole-run crash -- matching compute_current_cache_key()'s own
    "a per-segment failure must never take down the whole run" contract.
    Script ABSENCE is a whole-run problem (a plugin install defect, not a
    per-segment fact) and fatals immediately, matching run_ledger_merge()'s
    own check on ledger_merge_script."""
    if not script_path.is_file():
        fatal(f"{label} not found at {script_path}")
    try:
        proc = subprocess.run(
            [sys.executable, str(script_path), seg, "--durable-root", str(durable_root)],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=str(durable_root),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"could not run {label} {seg}: {exc}"
    if proc.returncode == 0:
        return True, ""
    detail = proc.stdout.strip() or proc.stderr.strip() or f"exit {proc.returncode}"
    return False, detail


def read_json_nonfatal(path: Path, what: str):
    """Same contract as read_segpack_nonfatal(): the parsed dict on success,
    or a string error message on failure -- NEVER raises/exits. A claim id's
    own artifact being unreadable must fail THAT id's admission alone, never
    take down the whole batch."""
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return f"{what} not found at {path}"
    except UnicodeDecodeError as exc:
        return f"{what} at {path} is not valid UTF-8: {exc}"
    except OSError as exc:
        return f"could not read {what} at {path}: {exc}"
    try:
        doc = json.loads(raw)
    except json.JSONDecodeError as exc:
        return f"{what} at {path} is not valid JSON: {exc}"
    if not isinstance(doc, dict):
        return f"{what} at {path} is not a JSON object"
    return doc


def review_path(seg: str, durable_root: Path) -> Path:
    """Byte-for-byte the same location as ledger_update.py's own
    review_path(): {durable_root}/segments/{seg}.review.json."""
    return durable_root / "segments" / f"{seg}.review.json"


# review.schema.json's own required/optional shape (required: clean,
# coverage_ok, findings, draft_sha1 -- optional: dispatch_token;
# additionalProperties:false). Restated per this project's "no shared lib"
# convention, matching how draft_ready.py already restates draft.schema.json's
# own container shape in its _DRAFT_CONTAINER_SPECS.
_REVIEW_REQUIRED_TYPES = {
    "clean": bool,
    "coverage_ok": bool,
    "findings": list,
    "draft_sha1": str,
}
_REVIEW_OPTIONAL_TYPES = {"dispatch_token": str}
_REVIEW_FINDING_REQUIRED_TYPES = {
    "loc": str,
    "severity": str,
    "issue": str,
    "suggest": str,
}


def check_review_structure(doc) -> list:
    """S4: 'the stored review is schema-valid on its own terms' --
    review.schema.json's required fields/types, additionalProperties:false,
    and findings[]'s own required shape. Returns a list of error strings
    (empty == valid)."""
    if not isinstance(doc, dict):
        return [f"review.schema.json: review root must be an object, got {type(doc).__name__}"]
    errs = [
        f"review.schema.json: missing required key {k!r}"
        for k in _REVIEW_REQUIRED_TYPES if k not in doc
    ]
    if errs:
        return errs
    for key, expected_type in _REVIEW_REQUIRED_TYPES.items():
        if not isinstance(doc[key], expected_type):
            errs.append(f"review.schema.json: {key!r} must be a {expected_type.__name__}")
    for key, expected_type in _REVIEW_OPTIONAL_TYPES.items():
        if key in doc and not isinstance(doc[key], expected_type):
            errs.append(f"review.schema.json: {key!r} must be a {expected_type.__name__}")
    allowed = set(_REVIEW_REQUIRED_TYPES) | set(_REVIEW_OPTIONAL_TYPES)
    extra = set(doc) - allowed
    if extra:
        errs.append(f"review.schema.json: unexpected field(s) {sorted(extra)}")
    if isinstance(doc.get("findings"), list):
        for i, item in enumerate(doc["findings"]):
            if not isinstance(item, dict):
                errs.append(f"review.schema.json: findings[{i}] must be an object")
                continue
            for key, expected_type in _REVIEW_FINDING_REQUIRED_TYPES.items():
                if key not in item:
                    errs.append(f"review.schema.json: findings[{i}] missing {key!r}")
                elif not isinstance(item[key], expected_type):
                    errs.append(f"review.schema.json: findings[{i}].{key} must be a string")
            item_extra = set(item) - set(_REVIEW_FINDING_REQUIRED_TYPES)
            if item_extra:
                errs.append(
                    f"review.schema.json: findings[{i}] has unexpected field(s) {sorted(item_extra)}"
                )
    return errs


def load_current_canon_entries(durable_root: Path):
    """Returns (entries_dict, None) or (None, error_string) -- canon.json's
    own 'entries' map, from the SAME location segpack.py itself reads
    (DURABLE_ROOT / "canon.json")."""
    doc = read_json_nonfatal(durable_root / "canon.json", "canon.json")
    if isinstance(doc, str):
        return None, doc
    entries = doc.get("entries")
    if not isinstance(entries, dict):
        return None, "canon.json has no 'entries' object"
    return entries, None


def _current_canon_target(entries: dict, name: str):
    """The target form the CURRENT canon.json would produce for `name`, or
    None -- byte-for-byte the same rule segpack.py's own canon-injection
    loop applies (build_pack(): entry = canon_entries.get(name); tf =
    entry.get('canonical_target_form') if isinstance(entry, dict) else None;
    recorded only if isinstance(tf, str) and tf)."""
    entry = entries.get(name)
    tf = entry.get("canonical_target_form") if isinstance(entry, dict) else None
    return tf if isinstance(tf, str) and tf else None


def evaluate_fresh_segpack_precondition(seg: str, durable_root: Path, canon_entries: dict) -> list:
    """D6's admission precondition for BOTH profiles: the segpack's frozen
    canon_map must agree with what the CURRENT canon.json would produce,
    over the segment's WHOLE 'names' partition -- not just canon_map's
    existing keys (codex round 2: canon_map is only a SUBSET of
    canon_names/new_names, so intersecting canon_map's own keys would miss a
    name that gained a target only in the CURRENT canon, or lost the one it
    had). Returns a list of mismatch dicts (empty == fresh); a single dict
    carrying only 'error' means the segpack itself could not be evaluated."""
    sp = read_json_nonfatal(segpack_path(seg, durable_root), f"segpack for segment {seg!r}")
    if isinstance(sp, str):
        return [{"error": sp}]
    names = sp.get("names")
    if not isinstance(names, list):
        return [{"error": f"segpack for segment {seg!r} has no 'names' array"}]
    stored_map = sp.get("canon_map")
    if not isinstance(stored_map, dict):
        return [{"error": f"segpack for segment {seg!r} has no 'canon_map' object"}]
    mismatches = []
    for name in names:
        if not isinstance(name, str):
            continue
        current_tf = _current_canon_target(canon_entries, name)
        stored_tf = stored_map.get(name)
        stored_tf = stored_tf if isinstance(stored_tf, str) and stored_tf else None
        if current_tf != stored_tf:
            mismatches.append(
                {"name": name, "segpack_target": stored_tf, "current_canon_target": current_tf}
            )
    return mismatches


# The one refusal S3 makes about a MISSING token, stated once so the plain
# refusal and every lost-token-recovery refusal below open with the same
# sentence. An operator reading either message is looking at the same
# observed fact; only the reason the sanctioned recovery did not apply
# differs, and that difference is what each caller appends.
_S3_NO_TOKEN = "S3: draft has no dispatch_token (absent or not a string)"


def evaluate_lost_token_recovery(seg: str, profile: str, run_id, durable_root: Path):
    """D9's sanctioned LOST-TOKEN RECOVERY. Returns (record, None) when THIS
    run's own claim record for `seg` exists, is readable, and agrees with the
    authorization being requested -- else (None, refusal_reason).

    WHY THIS EXISTS AT ALL. draft_ready.py's own claim note (its
    _claim_note(), on a dispatch_token-mismatch refusal) tells the operator,
    in as many words, to "re-claim {seg} under the same profile to restore
    it" once it finds a claim record for this run with no matching token on
    the draft -- the case where a fix round rewrote the draft and did not
    preserve `dispatch_token` byte for byte. Without this function that
    instruction names a command that cannot run: S3 refuses a token-less
    draft before the claim block ever consults an existing record, so the
    advertised recovery was unreachable and D9's residual was not in fact
    re-establishable. A tool that documents a remedy it then refuses is
    worse than one that documents none, because the operator spends the
    round trip discovering it.

    WHY THIS IS NOT A GENERAL HOLE, condition by condition. The recovery
    turns on evidence THIS RUN ITSELF wrote and nothing else:

      * a claim record must exist at runs/<run_id>/.claimed.<seg>, i.e. this
        very run already passed every S-gate, the profile's own condition
        list and D6 for this segment. A token-less draft with NO record is
        refused exactly as before -- that is an unclaimed draft that never
        had a token, not a claim whose token was lost, and the two are
        distinguishable only by the record;
      * the record must be READABLE (claim_record.read_claim_record(), never
        the lstat-only classifier -- this consumer is about to believe
        FIELDS). AMBIGUOUS refuses: an unreadable record is treated as NOT
        claimed, the safe direction claim_record.py's module docstring
        requires of every reader;
      * the record's own `seg`/`run_id` must agree with the path it was read
        from, so a record that does not describe itself cannot authorize
        anything;
      * the record's `profile` must equal the profile being requested now.
        "Re-claim under the same profile" is the literal instruction, and a
        DIFFERENT profile is a new authorization over a different condition
        list -- which needs a draft this gate can still read a token from.

    DELIBERATELY NARROW: this covers a token that is ABSENT (or not a
    non-empty string), never one that is present but malformed. A dropped
    field is what a schema-shaped rewrite produces (draft.schema.json makes
    `dispatch_token` optional, so a re-emitted draft simply loses it); a
    garbled non-empty token is a different event -- a cross-run collision, a
    hand edit, a partial write -- and reading it as "lost" would let this
    recovery answer a question it has no evidence about. It stays refused by
    S3's malformed-token branch.

    NOT re-checked here, on purpose: `pre_claim_content_sha1`. The record's
    copy is the draft as it looked at the ORIGINAL claim, and the fix round
    that dropped the token is exactly the thing that changed the content
    since. Requiring them to match would refuse every recovery this function
    exists to allow. What the draft must still satisfy is every gate in this
    same admission pass, evaluated against the draft as it is NOW."""
    if not isinstance(run_id, str) or not run_id:
        return None, (
            f"{_S3_NO_TOKEN}, and no --run-id is available to look up a claim record for "
            f"this segment, so the D9 lost-token recovery cannot be evaluated"
        )
    claim_record = _import_claim_record()
    try:
        path = claim_record.claimed_path(run_id, seg, durable_root / "runs")
    except ValueError as exc:
        # claimed_path() raises rather than sanitizing, so a reader cannot
        # forget the check. Kept a per-id reason here rather than letting it
        # escape: this function's whole contract is that ONE segment's own
        # problem never takes down the batch.
        return None, (
            f"{_S3_NO_TOKEN}, and the D9 lost-token recovery could not even look for a "
            f"claim record: {exc}"
        )
    state, payload, detail = claim_record.read_claim_record(path)
    if state == claim_record.CLAIM_ABSENT:
        return None, (
            f"{_S3_NO_TOKEN}, and run {run_id!r} holds no claim record for this segment "
            f"either -- so this is an unclaimed draft with no token at all, not the D9 "
            f"lost-token recovery (which restores a token dropped AFTER this run already "
            f"claimed the segment)"
        )
    if state != claim_record.CLAIM_PRESENT or not isinstance(payload, dict):
        return None, (
            f"{_S3_NO_TOKEN}, and run {run_id!r}'s own claim record for this segment is "
            f"unreadable ({detail or state}) -- treated as NOT claimed, the safe "
            f"direction, never assumed claimed. The D9 lost-token recovery needs a "
            f"readable record; resolve {path} by hand first"
        )
    recorded_seg = payload.get("seg")
    recorded_run_id = payload.get("run_id")
    if recorded_seg != seg or recorded_run_id != run_id:
        return None, (
            f"{_S3_NO_TOKEN}, and the claim record at {path} names "
            f"seg={recorded_seg!r}/run_id={recorded_run_id!r}, which disagrees with the "
            f"path it was read from -- refusing to recover a claim from a record that "
            f"does not describe itself"
        )
    recorded_profile = payload.get("profile")
    if recorded_profile != profile:
        return None, (
            f"{_S3_NO_TOKEN}, and run {run_id!r}'s claim record for this segment was "
            f"written under profile {recorded_profile!r}, not the requested {profile!r} "
            f"-- the sanctioned recovery is to re-claim under the SAME profile. Naming a "
            f"different profile is a NEW authorization over a different condition list, "
            f"and that needs a draft this gate can still read a token from"
        )
    return payload, None


def evaluate_claim_admission(
    seg: str,
    profile: str,
    record: "dict | None",
    dirs: dict,
    canon_entries: dict,
    args,
) -> "tuple[bool, list, dict]":
    """Evaluates ALL of D2's shared safety gates (S1-S5), the requested
    profile's own closed condition list, and D6's fresh-segpack precondition
    for ONE claim id -- every check runs independently and every failure is
    collected, per D2's 'all ids validated in ONE pass with every failure
    reported' (three sequential fatals cost an operator three round trips).

    Returns (ok, reasons, extras). `extras` is populated only when ok is
    True and carries everything the caller needs to build and write the
    claim record (claim_record.build_claim_record())."""
    durable_root = dirs["durable_root"]
    reasons = []
    ledger_record = record if isinstance(record, dict) else {}
    if record is None:
        reasons.append(
            f"{seg!r}: no ledger record exists at all (materialized runs/ledger.json "
            f"has nothing for it) -- profile {profile!r} requires one"
        )

    # ---- S1: validate_draft.py -- deterministic coverage/content ---------
    ok1, detail1 = _run_leaf_gate(dirs["validate_draft_script"], seg, durable_root, "validate_draft.py")
    if not ok1:
        reasons.append(f"S1 (validate_draft.py) failed: {detail1}")

    # ---- S2: draft_ready.py structural checks -- NO --expect-token: the --
    # ---- draft's token is still the OLD one at admission time, this only -
    # ---- checks required-key-set / seg field / segpack 1:1. ---------------
    ok2, detail2 = _run_leaf_gate(dirs["draft_ready_script"], seg, durable_root, "draft_ready.py")
    if not ok2:
        reasons.append(f"S2 (draft_ready.py structural check) failed: {detail2}")

    # ---- draft read: needed for S3, --from-converged's own ---------------
    # ---- reviewed_draft_sha1 comparison, and the claim record's own ------
    # ---- previous_dispatch_token / pre_claim_content_sha1. ----------------
    dp = draft_path(seg, durable_root)
    draft_doc = read_json_nonfatal(dp, f"draft for segment {seg!r}")
    current_draft_sha1 = None
    previous_token = None
    source_run_id = None
    lost_token_recovery = False
    if isinstance(draft_doc, str):
        reasons.append(f"S3 (dispatch_token): {draft_doc}")
    else:
        try:
            current_draft_sha1 = draft_content_sha1(dp)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            reasons.append(f"could not compute the draft's own content sha1: {exc}")
        previous_token = draft_doc.get("dispatch_token")
        if not isinstance(previous_token, str) or not previous_token:
            # D9's sanctioned lost-token recovery, the ONLY way a token-less
            # draft passes S3 -- and only because this same run already
            # claimed this segment and left the durable record proving it.
            # See evaluate_lost_token_recovery() for every condition and for
            # why none of them can be satisfied by an unclaimed draft.
            previous_token = None
            recovered, recovery_problem = evaluate_lost_token_recovery(
                seg, profile, args.run_id, durable_root
            )
            if recovered is None:
                reasons.append(recovery_problem)
            else:
                lost_token_recovery = True
                # Both provenance facts come from the record rather than
                # from the draft, because the draft no longer carries
                # either -- the record IS this run's own preserved
                # observation of what the token was at admission time.
                previous_token = recovered.get("previous_dispatch_token")
                recovered_source = recovered.get("source_run_id")
                if not isinstance(recovered_source, str) or not recovered_source:
                    reasons.append(
                        f"S3 (D9 lost-token recovery): run {args.run_id!r}'s claim record "
                        f"for this segment carries no usable 'source_run_id' "
                        f"({recovered_source!r}), so the source run this draft came from "
                        f"cannot be re-established"
                    )
                else:
                    # The SAME two checks the token path makes below, over
                    # the same fact from the other source. A recovery is not
                    # a weaker gate -- it reads the source run id off a
                    # durable record instead of off a field that was lost,
                    # and everything S3 asks about that id still applies.
                    problem = validate_run_id(recovered_source)
                    if problem is not None:
                        reasons.append(
                            f"S3 (D9 lost-token recovery): the claim record's "
                            f"'source_run_id' is not a safe run id: {problem}"
                        )
                    elif not (durable_root / "runs" / recovered_source).is_dir():
                        reasons.append(
                            f"S3 (D9 lost-token recovery): the claim record names source "
                            f"run {recovered_source!r}, which does not exist under runs/"
                        )
                    else:
                        source_run_id = recovered_source
        else:
            source_run_id = draft_run_id(previous_token)
            if source_run_id is None:
                reasons.append(
                    f"S3: draft's dispatch_token {previous_token!r} is malformed "
                    f"(cannot extract a run id)"
                )
            else:
                problem = validate_run_id(source_run_id)
                if problem is not None:
                    reasons.append(f"S3: draft's dispatch_token names an unsafe run id: {problem}")
                elif not (durable_root / "runs" / source_run_id).is_dir():
                    reasons.append(
                        f"S3: draft's dispatch_token names run {source_run_id!r}, which does "
                        f"not exist under runs/"
                    )

    # ---- S4/S5 + profile review conditions: read the stored review -------
    review_doc = read_json_nonfatal(review_path(seg, durable_root), f"review for segment {seg!r}")
    if isinstance(review_doc, str):
        reasons.append(f"S4 (stored review): {review_doc}")
        review_doc = None
    else:
        review_errs = check_review_structure(review_doc)
        if review_errs:
            reasons.append(f"S4: stored review is not schema-valid: {'; '.join(review_errs)}")
            review_doc = None

    if review_doc is not None:
        if review_doc.get("coverage_ok") is not True:
            reasons.append(
                f"S5: stored review's coverage_ok is {review_doc.get('coverage_ok')!r}, "
                f"required true under BOTH profiles"
            )
        if profile == CLAIM_PROFILE_FROM_CAP:
            if review_doc.get("clean") is not False:
                reasons.append(
                    f"{seg!r} requested under --from-cap, but its stored review's clean is "
                    f"{review_doc.get('clean')!r}, not false -- --from-cap identifies a capped, "
                    f"hand-fixed segment by its non-clean review with findings; if this segment "
                    f"actually converged cleanly, use --from-converged instead"
                )
            elif not review_doc.get("findings"):
                reasons.append(
                    f"{seg!r} requested under --from-cap, but its stored review's findings is "
                    f"empty -- --from-cap requires a non-clean review WITH findings"
                )
        elif profile == CLAIM_PROFILE_FROM_CONVERGED:
            if review_doc.get("clean") is not True:
                reasons.append(
                    f"{seg!r} requested under --from-converged, but its stored review's clean "
                    f"is {review_doc.get('clean')!r}, not true -- --from-converged requires the "
                    f"review that converged it, and a converged segment's last review is always "
                    f"clean:true"
                )

    # ---- profile-specific ledger-status/sentinel conditions ---------------
    # Both read from the MATERIALIZED ledger (`ledger_record`, sourced from
    # runs/ledger.json via classify_segment()'s own caller) -- never a
    # runs/ledger.d/*.json fragment; the two artifacts disagree by
    # construction (premise 8) and every status condition in this design
    # must name which one it reads.
    sentinel_state, sentinel_detail = classify_ever_converged_sentinel(
        ever_converged_path(seg, durable_root / "segments")
    )
    # AMBIGUOUS cannot reach this point for a seg drawn from `segs` -- the
    # run()-level ambiguous_sentinels fatal already aborted the whole
    # invocation before the claim gate runs at all (D5.1's placement).
    # Handled defensively anyway, and mapped the way claim_record.py's own
    # module docstring requires for every reader of ambiguous state: AMBIGUOUS
    # means "do not claim" for BOTH profiles, never "assume present" or
    # "assume absent".
    if profile == CLAIM_PROFILE_FROM_CAP:
        status = ledger_record.get("status")
        reason = ledger_record.get("reason")
        if not (status == "non_converged" and reason == "cap"):
            reasons.append(
                f"{seg!r} requested under --from-cap, but its materialized ledger status is "
                f"{status!r}/{reason!r}, not non_converged/reason=cap"
            )
        if sentinel_state != SENTINEL_ABSENT:
            detail = f" ({sentinel_detail})" if sentinel_detail else ""
            reasons.append(
                f"{seg!r} requested under --from-cap, but it carries a .ever_converged "
                f"sentinel ({sentinel_state}{detail}) -- --from-cap's population never "
                f"converged at all; if this segment converged and was then hand-edited, use "
                f"--from-converged instead"
            )
    elif profile == CLAIM_PROFILE_FROM_CONVERGED:
        status = ledger_record.get("status")
        if status not in WAS_CONVERGED_STATUSES:
            reasons.append(
                f"{seg!r} requested under --from-converged, but its materialized ledger status "
                f"is {status!r}, not one of {sorted(WAS_CONVERGED_STATUSES)}"
            )
        if sentinel_state != SENTINEL_PRESENT:
            detail = f" ({sentinel_detail})" if sentinel_detail else ""
            reasons.append(
                f"{seg!r} requested under --from-converged, but it carries no .ever_converged "
                f"sentinel ({sentinel_state}{detail}) -- --from-converged requires a segment "
                f"that has converged at least once"
            )
        reviewed_draft_sha1 = ledger_record.get("reviewed_draft_sha1")
        if not isinstance(reviewed_draft_sha1, str) or not reviewed_draft_sha1:
            reasons.append(
                f"{seg!r} requested under --from-converged, but its ledger record has no "
                f"'reviewed_draft_sha1' -- the drift baseline this profile requires"
            )
        elif current_draft_sha1 is not None and reviewed_draft_sha1 == current_draft_sha1:
            reasons.append(
                f"{seg!r} requested under --from-converged, but its current draft content sha1 "
                f"still matches 'reviewed_draft_sha1' -- nothing has been hand-edited since "
                f"convergence, so there is no re-review to authorize"
            )

    # ---- D6: fresh-segpack precondition -- BOTH profiles ------------------
    segpack_mismatches = evaluate_fresh_segpack_precondition(seg, durable_root, canon_entries)
    if segpack_mismatches:
        if len(segpack_mismatches) == 1 and "error" in segpack_mismatches[0]:
            reasons.append(f"D6 (fresh-segpack precondition): {segpack_mismatches[0]['error']}")
        else:
            names_detail = "; ".join(
                f"{m['name']!r}: segpack has {m['segpack_target']!r}, current canon.json would "
                f"produce {m['current_canon_target']!r}"
                for m in segpack_mismatches
            )
            reasons.append(
                f"D6 (fresh-segpack precondition): {len(segpack_mismatches)} name(s) in segment "
                f"{seg!r}'s segpack disagree with the current canon.json: {names_detail}. Re-run "
                f"segpack.py for this segment (from the durable copy) before claiming it."
            )

    # ---- current cache key -- required to RECORD the D4 baseline, never --
    # ---- to gate --from-converged (decision 5: no moved field refuses). --
    current_cache_key = compute_current_cache_key(
        seg, dirs["cache_key_script"], durable_root, args.durable_root, args.plugin_root
    )
    if isinstance(current_cache_key, str):
        reasons.append(f"could not compute the current cache key: {current_cache_key}")
        current_cache_key = None

    if reasons:
        return False, reasons, {}

    # ---- cache-key diff -- REPORTING only, never gating (decision 5). ----
    # --from-cap fragments carry no 'cache_key' field at all (it is written
    # only on the convergence path) -- D6's own "for --from-cap, this
    # condition CANNOT EXIST" box -- so there is no historical baseline to
    # diff against, recorded as a note rather than an empty (and misleading)
    # moved-fields list.
    stored_cache_key = ledger_record.get("cache_key")
    moved_fields = []
    cache_key_note = None
    if isinstance(stored_cache_key, dict):
        # Entry keys are `pre_claim`/`at_claim`, mirroring the two record
        # fields these entries diff (`pre_claim_cache_key` and
        # `cache_key_at_claim`) rather than the older `stored`/`current`.
        # Both endpoints now live in the record beside this list, and a
        # reader triaging an incident must be able to see at a glance which
        # endpoint each side of a moved field came from -- "current" reads
        # as "now, when I am reading this", which is exactly what it is not.
        moved_fields = [
            {"field": f, "pre_claim": stored_cache_key.get(f), "at_claim": current_cache_key.get(f)}
            for f in CACHE_KEY_FIELDS
            if stored_cache_key.get(f) != current_cache_key.get(f)
        ]
    else:
        cache_key_note = (
            "no recorded cache_key on this fragment -- expected for --from-cap (cache_key is "
            "written only on the convergence path); no historical baseline exists to compare "
            "against"
        )
    machinery_only = bool(moved_fields) and all(
        m["field"] in MACHINERY_ONLY_CACHE_KEY_FIELDS for m in moved_fields
    )

    extras = {
        "current_draft_sha1": current_draft_sha1,
        "previous_dispatch_token": previous_token,
        "source_run_id": source_run_id,
        # D9: True when this admission came through the lost-token recovery
        # rather than off a token the draft still carried. Reporting-only --
        # deliberately NOT a claim-record field, since the record it would
        # be written into is, on this path, the very record that authorized
        # the recovery and must not be rewritten. run() surfaces it on
        # stderr so a recovery is never silent.
        "lost_token_recovery": lost_token_recovery,
        "current_cache_key": current_cache_key,
        # D6's two ENDPOINTS, both recorded: the baseline as the ledger
        # fragment stored it, and the key this invocation just computed.
        # `pre_claim_cache_key` is None exactly when no baseline existed
        # (always so for --from-cap, whose fragments never carry a
        # cache_key), which is what tells an empty `cache_key_moved_fields`
        # apart from "nothing moved" -- see claim_record.py's own field
        # commentary.
        "pre_claim_cache_key": stored_cache_key if isinstance(stored_cache_key, dict) else None,
        "cache_key_moved_fields": moved_fields,
        "cache_key_movement_machinery_only": machinery_only if moved_fields else None,
        "cache_key_note": cache_key_note,
        # D10: captured BEFORE the claim voids the stored review's standing
        # -- otherwise the only record of what the operator was shown at
        # admission time is gone.
        #
        # None, never a four-key dict of Nones, when no usable review
        # document existed: "the operator was shown a review whose every
        # field happened to be null" and "there was no review to be shown"
        # are different facts, and a record that reports them identically
        # destroys the one it was extended to preserve. Unreachable today --
        # `review_doc` is None only when S4 already appended a reason, and
        # this block is past the `if reasons` return -- but the shape is
        # written honestly rather than left to depend on that.
        "pre_claim_review": {
            "dispatch_token": review_doc.get("dispatch_token"),
            "clean": review_doc.get("clean"),
            "coverage_ok": review_doc.get("coverage_ok"),
            "findings_count": len(review_doc.get("findings", [])),
        }
        if review_doc
        else None,
    }
    return True, [], extras


def draft_dispatch_token_for(run_id: str, seg: str) -> str:
    """Byte-for-byte the same format as ledger_update.py's/ledger_merge.py's
    own expected_draft_token(): '<run_id>:<seg>'."""
    return f"{run_id}:{seg}"


# os.O_NOFOLLOW is POSIX and present on every platform this pipeline runs
# on; the getattr keeps a hypothetical platform without it importable rather
# than failing at module load, exactly as claim_record.py's own _O_DIRECTORY
# does. Falling back to 0 there weakens the temp-file open by one guard and
# no more -- O_CREAT|O_EXCL already refuses to follow a symlink at the final
# component (POSIX requires EEXIST when the path names an existing symlink,
# dangling or not), so O_NOFOLLOW is the explicit statement of an intent
# O_EXCL enforces anyway, not the only thing enforcing it.
_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)


def _unlink_quietly(path: Path) -> None:
    """Remove a staged temp file, ignoring an already-gone or unremovable
    entry. Used on every refusal path in rewrite_draft_dispatch_token()
    AFTER the descriptor is closed -- a leftover `.tmp.<pid>` is untidy, but
    failing the claim a second time over the cleanup would replace a clear
    refusal with a confusing one, and the refusal is the fact the operator
    needs."""
    try:
        path.unlink()
    except OSError:
        pass


def rewrite_draft_dispatch_token(
    seg: str, durable_root: Path, new_token: str, *, expected_content_sha1: str
):
    """D4/#438: the actual "claim the draft into this run" state change --
    re-stamps segments/{seg}.draft.json's own `dispatch_token` field to
    `new_token`. Without this, nothing downstream (draft_ready.py
    --expect-token, derive_next_action(), codex_job.py's safe_adopt()) ever
    sees the draft as belonging to the claiming run, and the claim record
    alone authorizes nothing -- the segment stays permanently stuck.

    MUST run strictly AFTER the claim record is durably written for this
    id, never before: a crash between the two writes must leave the draft
    holding its OLD token plus a claim record on disk (every existing gate
    still refuses that token, and a re-claim recovers cleanly) -- never a
    draft re-stamped for this run with NO record, which for --from-cap's
    population (no `.ever_converged` sentinel) would leave nothing at all
    refusing it and the segment would simply be retranslated.

    Idempotent: a draft already carrying `new_token` is a no-op, not an
    error -- a re-claim in the same run must not be mistaken for a second
    authorization (D9). Returns (True, "") on success or no-op, (False,
    detail) on failure -- never raises.

    Atomic (temp file + fsync + os.replace + a directory fsync, the same
    discipline ledger_update.py's write_fragment_atomically() and
    codex_job.py's own promote paths already use), so a crash mid-write
    leaves either the OLD draft intact or the fully-written NEW one, never a
    torn file.

    THE DIRECTORY FSYNC IS PART OF THE RECORD-FIRST GUARANTEE, not polish.
    fsync on the temp file commits its CONTENTS; the rename that makes those
    contents findable as `{seg}.draft.json` is a directory-entry change, and
    an unsynced directory can lose it. Paired with claim_record.py's own
    fsync of the runs/<run_id>/ directory (its fsync_directory(), reused
    here rather than reimplemented), this is what makes "record first, token
    second" survive a power loss instead of holding only within one
    process's lifetime. Losing the record while keeping the token is the one
    asymmetry D8's guard cannot refuse -- it sees no record and reads
    "unclaimed" -- so a failed sync FAILS the rewrite rather than warning.

    `expected_content_sha1` is REQUIRED, keyword-only, and closes the TOCTOU
    between admission and this write. Admission gates ONE draft and records
    its content sha1; without a check here, this function would re-read
    whatever occupies the path at this later moment -- after S1's and S2's
    subprocesses, the segpack scan, a cache_key.py subprocess and the claim
    record's own write -- and hand this run's dispatch_token to a draft that
    passed nothing. THE ORDER IS WHAT MAKES THE CHECK REAL: the draft is
    read exactly ONCE, the bytes to be installed are staged into the temp
    file, and the check hashes THE STAGED FILE. There is no window between
    "the file I checked" and "the file I install" for anything to slip
    through, because they are the same bytes -- a check that re-read the
    live path a second time would be a second sample, and a second sample is
    a second chance to be handed a different file.

    Hashing the staged file also means the comparator is draft_content_sha1()
    itself -- the function that OWNS this hash, seven byte-identical copies
    of which the project already tracks -- rather than an eighth inline
    re-implementation of the canonicalization written for this call site.

    ON MISMATCH: REFUSE, name the drift, and install nothing. The staged
    temp file is removed and the draft on disk is untouched, so the failure
    is a refusal, never a partial claim. The claim RECORD stays on disk
    (it was written first, by design), which is precisely the recoverable
    state -- every existing gate still refuses the old token, and re-running
    the claim re-evaluates the admission gates against the draft that is
    actually there now.

    The comparator is THIS invocation's own admitted hash, never the claim
    record's `pre_claim_content_sha1`. On the D9 lost-token recovery path
    the record's copy is deliberately older than the draft (a fix round
    edited it after the original claim), so checking against the record
    would refuse exactly the recovery this release makes reachable. What
    must hold is "the draft I gated is the draft I stamp", which is a
    statement about one invocation.

    The temp file is created with O_CREAT|O_EXCL|O_NOFOLLOW, never a plain
    open(): its name is predictable (it has to be, for the cleanup below to
    find it), and a plain open() FOLLOWS a symlink planted at that name and
    truncates whatever it points at before the file is ever installed as the
    draft. O_EXCL makes a pre-existing entry of any kind -- symlink,
    dangling symlink, leftover file -- a refusal instead of a target.

    Idempotent: a draft already carrying `new_token` is a no-op, not an
    error -- a re-claim in the same run must not be mistaken for a second
    authorization (D9). The no-op path still stages and checks, so the
    identity guarantee is the SAME on both paths (a re-claim affirms an
    authorization too, and affirming it over a draft nobody gated is the
    same defect); it then removes the staged file and leaves the draft's
    bytes untouched. Returns (True, "") on success or no-op, (False, detail)
    on failure -- never raises.

    Preserves every OTHER field's VALUE -- not the raw file's byte-for-byte
    formatting, which a parse-mutate-reserialize round trip cannot
    guarantee and does not need to: draft_content_sha1() itself projects
    `dispatch_token` out before hashing via CANONICAL (sorted-key,
    compact-separator) re-serialization, so its result is insensitive to
    this function's own formatting choice. What must be (and is, per a
    dedicated test) proven is that draft_content_sha1() returns the SAME
    value before and after -- the property every `reviewed_draft_sha1`
    comparison downstream depends on. The staged-file check now enforces
    that property at RUNTIME as well: a re-serialization that moved any
    other field would move the staged hash and refuse."""
    dp = draft_path(seg, durable_root)
    if not isinstance(expected_content_sha1, str) or not expected_content_sha1:
        # Not a defensive nicety: without a baseline there is nothing to
        # check the draft against, and proceeding would be the unguarded
        # rewrite this parameter exists to make impossible.
        return False, (
            f"refusing to stamp a claimed dispatch_token without the content sha1 this "
            f"invocation admitted (got {expected_content_sha1!r}) -- there would be "
            f"nothing to check the draft against"
        )
    try:
        raw = dp.read_text(encoding="utf-8")
    except OSError as exc:
        return False, f"could not read draft to rewrite its dispatch_token: {exc}"
    try:
        doc = json.loads(raw)
    except json.JSONDecodeError as exc:
        return False, f"draft is not valid JSON, refusing to rewrite its dispatch_token: {exc}"
    if not isinstance(doc, dict):
        return False, "draft is not a JSON object, refusing to rewrite its dispatch_token"
    already_stamped = doc.get("dispatch_token") == new_token
    doc["dispatch_token"] = new_token

    tmp_path = dp.parent / f"{dp.name}.tmp.{os.getpid()}"
    try:
        # 0o644, matching claim_record.py's own CLAIM_MODE and, under the
        # usual umask, exactly what the `open(tmp_path, "w")` this replaced
        # produced. os.open() takes the mode explicitly where open() did not,
        # so leaving it unstated would silently RE-PERMISSION the draft on
        # every claim -- the file that lands here is installed as the draft
        # by the os.replace() below, and a rewrite must change the token, not
        # who can read the draft.
        fd = os.open(
            str(tmp_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL | _O_NOFOLLOW, 0o644
        )
    except OSError as exc:
        return False, (
            f"could not create the temp file for the re-stamped draft at {tmp_path}: "
            f"{exc}. It is created exclusively and without following a symlink, so an "
            f"entry already sitting at that name is refused rather than written through"
        )
    write_problem = None
    try:
        # os.write on the raw descriptor rather than os.fdopen(): the
        # descriptor is closed exactly once, in the finally below, on every
        # path -- including a failure inside the wrapper construction
        # itself, which an `os.fdopen(fd, ...)` inside a `with` would leak.
        # The byte stream is identical to what json.dump(f) + f.write("\n")
        # produced before. The loop is not decoration either: os.write() is
        # allowed to write fewer bytes than it was given, and a silent short
        # write here would produce a truncated draft that the staged-hash
        # check below would then correctly refuse -- turning a recoverable
        # write into an unexplained mismatch.
        payload = json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=False) + "\n"
        view = memoryview(payload.encode("utf-8"))
        while view:
            view = view[os.write(fd, view):]
        os.fsync(fd)
    except OSError as exc:
        write_problem = f"could not write the re-stamped draft: {exc}"
    finally:
        try:
            os.close(fd)
        except OSError:
            pass
    if write_problem is not None:
        _unlink_quietly(tmp_path)
        return False, write_problem

    try:
        staged_sha1 = draft_content_sha1(tmp_path)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        _unlink_quietly(tmp_path)
        return False, f"could not hash the staged re-stamped draft: {exc}"
    if staged_sha1 != expected_content_sha1:
        _unlink_quietly(tmp_path)
        return False, (
            f"the draft on disk is not the one this invocation admitted: its content "
            f"sha1 (dispatch_token projected out) is {staged_sha1}, but the admission "
            f"gates ran against {expected_content_sha1}. Something replaced or edited "
            f"{dp} between admission and this stamp; refusing to hand it this run's "
            f"dispatch_token. Nothing was installed -- re-run the claim so the gates "
            f"evaluate the draft that is actually there"
        )
    if already_stamped:
        # Idempotent no-op -- see docstring. The staged file proved the
        # identity and is discarded; the draft's own bytes are never
        # touched, so a re-claim remains byte-for-byte a no-op.
        _unlink_quietly(tmp_path)
        return True, ""

    try:
        os.replace(tmp_path, dp)
    except OSError as exc:
        _unlink_quietly(tmp_path)
        return False, f"could not install the re-stamped draft: {exc}"

    claim_record = _import_claim_record()
    sync_problem = claim_record.fsync_directory(dp.parent)
    if sync_problem is not None:
        # The draft IS re-stamped and its contents are fsynced; only the
        # durability of the new directory entry is unproven. Reported as a
        # failure of the whole rewrite because the caller's next move --
        # dispatching this segment as claimed -- rests on a token that a
        # crash could still take back while the claim record survives. Not
        # reverted: rolling back here would mean writing the OLD token
        # through the same unsynced directory, which establishes nothing and
        # destroys the token provenance the record has already preserved.
        return False, f"the draft was re-stamped but {sync_problem}"
    return True, ""


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

    # #438: parsed early (cheap, no I/O beyond arg strings) so the
    # --classify-only incompatibility fatals fast, before ledger_merge.py
    # even runs.
    claim_requests = parse_claim_requests(args)
    if claim_requests and args.classify_only:
        fatal(
            "--classify-only produces a read-only report and may not be combined with a "
            "claim (--from-converged/--from-cap write a durable claim record on disk) -- "
            "run the claim in its own invocation."
        )

    # #438/#409 Step 3: --run-id and --run-resume are a PAIR, validated
    # together regardless of whether a claim is involved (this is a general
    # Step 3 fix, not claim-specific logic -- see the fresh-evidence refusal
    # further down). Giving one without the other is refused rather than
    # silently treating the missing one as "not applicable".
    if (args.run_id is None) != (args.run_resume is None):
        fatal(
            "--run-id and --run-resume must be given TOGETHER or not at all -- "
            f"got --run-id={args.run_id!r} --run-resume={args.run_resume!r}. "
            "--run-resume carries resume_setup.py's own 'resume' field, which is what "
            "lets the fresh-evidence check below tell a legitimately resumed run id from "
            "a freshly-minted one; a run id without it cannot be evaluated safely."
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
    ambiguous_sentinels = []
    if authorizes_dispatch:
        # resolve_dirs() exposes durable_root, not a segments_dir -- derive it
        # the same way draft_path_for/segpack_path do, so a --durable-root
        # redirect moves the sentinel lookup with everything else.
        segments_dir = dirs["durable_root"] / "segments"
        for seg in segs:
            # classify_ever_converged_sentinel(), NOT `.exists()`: the writer
            # and this reader have to answer one question the same way, and
            # `exists()` answered it wrongly in the one direction that costs
            # finished work -- see that function's docstring for the two
            # mechanisms (a dangling symlink reads as absent while the writer
            # calls it marked; every OSError reads as absent since 3.13).
            state, detail = classify_ever_converged_sentinel(
                ever_converged_path(seg, segments_dir)
            )
            if state == SENTINEL_PRESENT:
                previously_converged.append(seg)
            elif state == SENTINEL_AMBIGUOUS:
                ambiguous_sentinels.append({"seg": seg, "detail": detail})

    # Refused BEFORE the previously-converged gate below, deliberately: this
    # one is not clearable by --allow-retranslate-converged, so reporting the
    # clearable refusal first would send an operator to pass a flag and then
    # hit this anyway -- a two-step discovery of a one-step problem.
    if ambiguous_sentinels:
        ambiguous_detail = "; ".join(
            f"{entry['seg']} ({entry['detail']})" for entry in ambiguous_sentinels
        )
        fatal(
            f"{len(ambiguous_sentinels)} segment(s) have an ever-converged "
            f"sentinel path that is neither absent nor a regular file: "
            f"{ambiguous_detail}. Refusing to dispatch. Only ENOENT means 'this segment "
            f"never converged'; every other outcome MAY be a converged segment "
            f"whose sentinel this process cannot see, and dispatching on that "
            f"assumption would silently retranslate finished work. "
            f"--allow-retranslate-converged does NOT clear this, on purpose: "
            f"that flag says 'I know these converged and I authorize redoing "
            f"them', and here nobody knows which it is. Resolve it at the path "
            f"instead. ledger_update.py:mark_ever_converged() only ever "
            f"publishes a REGULAR file, so if the entry is a permissions or "
            f"mount error, fix that and rerun; if it is some other entry "
            f"entirely and you can establish the segment really did converge, "
            f"replace it with a regular file containing the single line "
            f"'converged'; only if you can establish it did NOT converge is "
            f"removing the entry the right move.",
            classification=classification,
            counts=counts,
            ids_by_category=ids_by_category,
            # Machine-readable counterpart to the prose above, same reason as
            # `not_yet_converged` below: a caller (and its test) can assert the
            # exact set and the exact reason rather than grep an error string.
            ambiguous_sentinels=ambiguous_sentinels,
            # Carried so the two #409 Step 1 refusal shapes have the same key
            # set -- which segments DID have a valid sentinel is exactly the
            # context an operator triaging an ambiguous one needs, and a
            # consumer must not have to branch on the key's presence to read
            # it. Note this is the set found BEFORE the ambiguous entries were
            # hit: a segment whose sentinel is ambiguous is, by definition,
            # not in it.
            previously_converged=previously_converged,
        )

    # ---- #409 Step 3: evidence scan -----------------------------------------
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
    # #438 fix, and the reason this scan sits HERE -- strictly before the claim
    # block below, never after it: a claim writes (the claim record, and the
    # draft's own dispatch_token). Scanning AFTER those writes would let a
    # claim's own write feed this same invocation's evidence set -- proven by
    # hand while wiring rewrite_draft_dispatch_token() in: with the scan
    # positioned after the claim block, a brand-new run's own first claim
    # rewrites a draft's dispatch_token to `run_id:seg`, the scan then reads
    # that back off disk as "dispatch evidence for run_id", and the
    # fresh-evidence check below refuses the run for evidence it just
    # manufactured itself. Evidence bearing this run's id that PRE-EXISTS this
    # invocation must refuse (the skipped-gate laundering case Step 3 exists
    # to close); evidence this SAME invocation is about to create as part of
    # an admitted claim is expected and must not. Ordering is what separates
    # the two -- there is no content-based test that would -- so the scan
    # (and the fresh-evidence check that reads it, immediately below) must
    # complete before the claim block's first write, not merely before its
    # own two refusal fatals.
    #
    # THE NORMATIVE RULE (state this, not just "scan early"): Step 3's
    # evidence is a property of the tree AS THIS INVOCATION FOUND IT --
    # `evidence`/`dispatch_scan`/`workflow_run_ids`/`unsafe_run_ids`/
    # `safe_evidence`/`runs_acknowledged_pre_gate`/`runs_missing_digest` are
    # computed EXACTLY ONCE, right here, and every consumer -- the
    # fresh-evidence check immediately below AND the two refusal fatals
    # further down in this function -- reads this SAME snapshot. This is not
    # merely "the scan runs before the claim block"; ordering is a
    # coincidental property of where the claim block currently lives in
    # run(), and a future edit that reorders things again could silently
    # reintroduce the hazard while still "running before" some write. A
    # SNAPSHOT makes the bug class structurally inexpressible instead: "an
    # invocation refuses against its own writes" cannot be expressed at all
    # once evidence is a value fixed at scan time rather than a live query.
    # This is the THIRD time this exact defect surfaced under a new name
    # (r13's digest-laundering finding, then the runs_missing_digest
    # self-trip during the first wiring attempt, then this fresh-evidence
    # self-trip) -- each fix that only reordered things moved the defect
    # rather than closing it. Do not call scan_dispatching_run_ids() or
    # scan_workflow_run_ids() anywhere else in this function; re-scanning
    # live state downstream of this point IS the bug, not a refinement of it.
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

    # #438 fix to #409 Step 3 itself (general infrastructure, not
    # claim-specific -- found while wiring the claim's own --run-id/
    # single-phase ordering, but the defect predates #438). The gate above
    # trusts "a digest exists for this id" as proof the resume-integrity
    # gate ran for it. That is true for a RESUMED id (the digest genuinely
    # predates this invocation) but false for a FRESH one under the new
    # single-phase ordering: resume_setup.py runs BEFORE this script and
    # writes runs/<RUN_ID>/input.digest for the id it just minted, so a
    # digest existing for THIS invocation's own --run-id proves only that
    # resume_setup.py ran just now -- it proves NOTHING about whether any
    # PRE-EXISTING dispatch evidence bearing that exact id was ever gated.
    # resume_setup.py mints a fresh id merely when runs/<candidate> is
    # absent (never consulting workflow dirs or draft tokens), so a fresh
    # id colliding with pre-existing evidence would otherwise sail through
    # runs_missing_digest untouched -- laundering exactly the skipped-gate
    # dispatch this whole gate exists to refuse. Gated on `authorizes_dispatch`
    # for the same reason every other Step 3 refusal is: --classify-only
    # must stay a pure read.
    # KNOWN RESIDUAL, disclosed rather than closed (team-lead, #438 review):
    # this check is ONE-SIDED. It fires only when `--run-resume` is the
    # literal string "false" -- there is no cross-check on the "true" branch,
    # so a caller that relays "--run-resume true" for a genuinely FRESH id
    # carrying pre-existing evidence bypasses the refusal entirely. `--run-
    # resume` is a RELAY of resume_setup.py's own `resume` field, not
    # something this script re-derives: `resume_setup.py` reports `resume:
    # true` only when a candidate's prior digest matches the freshly computed
    # `input_digest`, but a FRESH run's digest is written with that exact
    # same value, so digest CONTENT cannot discriminate after the fact. The
    # genuine discriminator -- "the digest existed BEFORE this pipeline ran"
    # -- is not observable from inside this invocation. Attestation is
    # unavoidable here; do not try to close this with more machinery without
    # a new source of truth, and do not read this guard as self-verifying --
    # a false "true" defeats it completely.
    if authorizes_dispatch and args.run_id is not None:
        current_run_problem = validate_run_id(args.run_id)
        if current_run_problem is not None:
            fatal(
                f"--run-id {args.run_id!r} is not a safe run id: {current_run_problem}",
                classification=classification,
                counts=counts,
                ids_by_category=ids_by_category,
            )
        if args.run_resume == "false" and args.run_id in evidence:
            fatal(
                f"--run-id {args.run_id!r} was reported FRESH by resume_setup.py "
                f"(--run-resume false), but this project already has dispatch evidence "
                f"bearing that exact id: evidence={'+'.join(evidence[args.run_id])}. A "
                f"digest resume_setup.py just wrote for it proves only that "
                f"resume_setup.py ran as part of THIS invocation -- it proves nothing "
                f"about whether the pre-existing evidence was ever checked against the "
                f"resume-integrity gate, and admitting it here would launder exactly the "
                f"skipped-gate dispatch #409 Step 3 exists to refuse. If this id is "
                f"genuinely a resumed run, resume_setup.py should have reported "
                f"--run-resume true; if this is a wall-clock collision or a forged/"
                f"duplicate id, a different run id is required.",
                classification=classification,
                counts=counts,
                ids_by_category=ids_by_category,
            )
        # The one cheap check that IS derivable on the "true" branch: a
        # genuinely RESUMED run must already have an input.digest -- that is
        # the literal precondition resume_setup.py's own resume match
        # requires. It does not close the attestation hole above (a caller
        # could still relay "true" for an id that has BOTH pre-existing
        # evidence AND a digest from some unrelated prior run), but it does
        # catch the more likely accident: a malformed or garbled relay of
        # resume_setup.py's own field, as opposed to a deliberate lie.
        if args.run_resume == "true" and not input_digest_path(args.run_id, runs_dir).is_file():
            fatal(
                f"--run-id {args.run_id!r} was reported RESUMED by resume_setup.py "
                f"(--run-resume true), but no runs/{args.run_id}/input.digest exists for "
                f"it. A genuine resume match requires resume_setup.py to have already "
                f"written that digest -- its absence means --run-resume does not agree "
                f"with what is actually on disk, most likely a malformed or stale relay "
                f"of resume_setup.py's own 'resume' field rather than resume_setup.py's "
                f"real, current output.",
                classification=classification,
                counts=counts,
                ids_by_category=ids_by_category,
            )

    # ---- #438: the claim admission gate -----------------------------------
    # D5.1: placed strictly AFTER ambiguous_sentinels (never before -- an
    # ambiguous sentinel means convergence history is UNKNOWN, and letting a
    # claim run first would let a new authorization clear a refusal
    # deliberately built to be unclearable) and BEFORE previously_converged
    # (a successful --from-converged claim clears THAT gate for exactly its
    # own successfully-admitted ids -- D5.2).
    claims_payload: dict = {}
    if claim_requests:
        claim_record = _import_claim_record()
        run_id = args.run_id
        if run_id is None:
            fatal(
                "a claim (--from-converged/--from-cap) was requested but --run-id was not "
                "given. --run-id must be passed explicitly and is never derived from a token "
                "-- deriving it would make a malformed token read as 'not claimed' and "
                "silently proceed (PLAN.md D8).",
                classification=classification,
                counts=counts,
                ids_by_category=ids_by_category,
            )
        run_id_problem = validate_run_id(run_id)
        if run_id_problem is not None:
            fatal(
                f"--run-id {run_id!r} is not a safe run id: {run_id_problem}",
                classification=classification,
                counts=counts,
                ids_by_category=ids_by_category,
            )

        # D3: the authorization must be a SUBSET of the emitted segs -- a
        # human_escalation id (tome1's --from-cap population) needs
        # --only-segs naming it too, exactly as any other retry does.
        segs_set = set(segs)
        not_in_segs = sorted(seg for seg in claim_requests if seg not in segs_set)
        if not_in_segs:
            fatal(
                f"{len(not_in_segs)} claimed id(s) are not in this invocation's own emitted "
                f"segs, so the authorization would not be a subset of what is dispatched: "
                f"{', '.join(not_in_segs)}. A human_escalation id needs --only-segs naming "
                f"it too, exactly as any other retry does.",
                classification=classification,
                counts=counts,
                ids_by_category=ids_by_category,
            )

        # D5.3: overlap between a claim and --allow-retranslate-converged is
        # REJECTED OUTRIGHT, not resolved by precedence -- checked over the
        # REQUESTED --from-converged ids, before any admission work runs.
        # --from-cap is deliberately absent here: its population carries no
        # sentinel, so it never reaches previously_converged at all.
        from_converged_requested = {
            seg for seg, profile in claim_requests.items() if profile == CLAIM_PROFILE_FROM_CONVERGED
        }
        if args.allow_retranslate_converged:
            overlap = sorted(from_converged_requested & set(previously_converged))
            if overlap:
                fatal(
                    f"{len(overlap)} segment(s) are named under --from-converged AND covered "
                    f"by --allow-retranslate-converged: {', '.join(overlap)}. Rejected "
                    f"outright -- --allow-retranslate-converged authorizes RE-TRANSLATION, a "
                    f"claim authorizes RE-REVIEW only, and 'claim wins' would be one flag "
                    f"silently changing the other's meaning. Split this into two invocations.",
                    classification=classification,
                    counts=counts,
                    ids_by_category=ids_by_category,
                    previously_converged=previously_converged,
                )

        canon_entries, canon_err = load_current_canon_entries(dirs["durable_root"])
        if canon_entries is None:
            fatal(
                f"could not evaluate the fresh-segpack precondition (D6) for any requested "
                f"claim: {canon_err}",
                classification=classification,
                counts=counts,
                ids_by_category=ids_by_category,
            )

        # D2: all ids validated in ONE PASS, every failure reported together
        # -- three sequential fatals would cost an operator three round
        # trips to learn three problems.
        admitted: dict = {}
        failures: dict = {}
        for seg, profile in sorted(claim_requests.items()):
            ok, reasons, extras = evaluate_claim_admission(
                seg, profile, ledger_segments.get(seg), dirs, canon_entries, args
            )
            if ok:
                admitted[seg] = (profile, extras)
            else:
                failures[seg] = reasons

        if failures:
            detail = "; ".join(
                f"{seg} [{claim_requests[seg]}]: {' | '.join(reasons)}"
                for seg, reasons in sorted(failures.items())
            )
            fatal(
                f"{len(failures)} of {len(claim_requests)} requested claim(s) refused "
                f"admission (every failure reported together, per D2): {detail}",
                classification=classification,
                counts=counts,
                ids_by_category=ids_by_category,
                claim_failures=failures,
            )

        # Every requested id passed -- write the durable claim record for
        # each (claim_record.py's own three-state predicate; NEVER
        # Path.exists()). AMBIGUOUS reads (an unreadable existing record)
        # map to a write failure below, never to "assume claimed".
        runs_dir = dirs["durable_root"] / "runs"
        operator_invocation = " ".join(sys.argv)
        write_failures = []
        for seg, (profile, extras) in sorted(admitted.items()):
            # Every one of CLAIM_RECORD_FIELDS' fourteen fields, by keyword
            # -- build_claim_record() is keyword-only with no defaults, so a
            # field added there and forgotten here is a TypeError at this
            # line rather than a record silently missing the evidence it was
            # extended to carry. D6's cache-key endpoints and D10's
            # pre-claim review are passed INTO the record here; they used to
            # be spliced onto the stdout payload only, which meant the one
            # durable account of a state change that voids a review did not
            # contain the evidence that justified it.
            payload = claim_record.build_claim_record(
                seg=seg,
                profile=profile,
                run_id=run_id,
                source_run_id=extras["source_run_id"],
                previous_dispatch_token=extras["previous_dispatch_token"],
                pre_claim_content_sha1=extras["current_draft_sha1"],
                pre_claim_review=extras["pre_claim_review"],
                pre_claim_cache_key=extras["pre_claim_cache_key"],
                cache_key_at_claim=extras["current_cache_key"],
                cache_key_moved_fields=extras["cache_key_moved_fields"],
                cache_key_movement_machinery_only=extras["cache_key_movement_machinery_only"],
                cache_key_note=extras["cache_key_note"],
                operator_invocation=operator_invocation,
                claimed_at=_claim_now_iso8601(),
            )
            marker_path = claim_record.claimed_path(run_id, seg, runs_dir)
            published, write_detail = claim_record.write_claim_record(marker_path, payload)
            if published:
                record_for_output = payload
            elif write_detail == "already claimed by this run":
                # Idempotent: the SAME authorization being reapplied, not a
                # new one (D9) -- report what is ACTUALLY on disk, never a
                # freshly recomputed payload, since the durable record is
                # the one thing a re-run must not silently overwrite.
                state, existing, read_detail = claim_record.read_claim_record(marker_path)
                if state != claim_record.CLAIM_PRESENT or existing is None:
                    write_failures.append(
                        f"{seg}: already claimed by this run, but the existing record could "
                        f"not be re-read ({read_detail}) -- state={state}"
                    )
                    continue
                record_for_output = existing
            else:
                write_failures.append(f"{seg}: {write_detail}")
                continue

            # D4/D9: rewrite the draft's OWN dispatch_token to this run's
            # value, strictly AFTER the record write above (never before --
            # token-first would destroy the previous token's provenance
            # before anything durable recorded it; if the draft then became
            # ABSENT or INVALID with no record ever written, D8's guard would
            # not protect it and a translate could launch). Safe to do here,
            # inside the same single-phase invocation, because the #409 Step
            # 3 evidence scan above (and the fresh-evidence check that reads
            # it) already completed BEFORE this claim block's first write --
            # see that scan's own comment for the self-refusal this ordering
            # exists to prevent.
            #
            # `expected_content_sha1` is THIS invocation's own admitted hash
            # (extras), never `record_for_output["pre_claim_content_sha1"]`.
            # On the idempotent/D9-recovery path the record's copy is
            # deliberately older than the draft -- a fix round edited it
            # after the original claim -- so checking against the record
            # would refuse exactly the recovery this release makes
            # reachable. The property being enforced is "the draft these
            # gates just evaluated is the draft that gets stamped", which is
            # a statement about one invocation.
            token_ok, token_detail = rewrite_draft_dispatch_token(
                seg,
                dirs["durable_root"],
                f"{run_id}:{seg}",
                expected_content_sha1=extras["current_draft_sha1"],
            )
            if not token_ok:
                write_failures.append(f"{seg}: dispatch_token rewrite failed: {token_detail}")
                continue

            if extras.get("lost_token_recovery"):
                # D9: an admission that came through the lost-token recovery
                # must never be silent. It is not reported in the JSON --
                # `claims` is the record verbatim, and this is a fact about
                # HOW this invocation reached it, not about the claim -- but
                # an operator who lost a token by accident needs to see that
                # the tool noticed and restored it rather than quietly
                # re-authorizing something.
                print(
                    f"select_segments.py: {seg} admitted via the D9 lost-token recovery "
                    f"-- its draft carried no dispatch_token and run {run_id!r}'s own "
                    f"claim record for it authorized re-stamping",
                    file=sys.stderr,
                )

            # The record VERBATIM -- no freshly recomputed fields spliced on
            # top. It used to carry a four-field spread of `extras` here,
            # which on the already-claimed path silently overwrote the
            # DURABLE record's values with values recomputed in this
            # invocation, contradicting the comment above about reporting
            # what is actually on disk. The record now carries all four
            # itself, so the spread is not merely wrong, it is redundant.
            claims_payload[seg] = record_for_output

        if write_failures:
            fatal(
                f"{len(write_failures)} claim record write failure(s): "
                f"{'; '.join(write_failures)}",
                classification=classification,
                counts=counts,
                ids_by_category=ids_by_category,
            )

        # D5.2: a --from-converged claim clears previously_converged for
        # EXACTLY its own SUCCESSFULLY-ADMITTED-AND-RECORDED ids -- never
        # the merely requested ones. A claim that failed any gate above
        # already fataled the whole invocation, so by construction every
        # key in claims_payload here passed every S-gate, its profile
        # condition, and D6.
        cleared = {
            seg for seg in claims_payload if claim_requests.get(seg) == CLAIM_PROFILE_FROM_CONVERGED
        }
        previously_converged = [seg for seg in previously_converged if seg not in cleared]

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
            # Always [] on this path -- a non-empty set refused above -- but
            # carried anyway so every failure shape has the same key set, the
            # same reason unsafe_run_ids is repeated across the shapes below.
            # A consumer that must branch on the key's presence is a consumer
            # that will get it wrong on the shape nobody thought to test.
            ambiguous_sentinels=ambiguous_sentinels,
        )

    # ---- #409 Step 3 refusal fatals -----------------------------------------
    # `evidence`/`dispatch_scan`/`workflow_run_ids`/`unsafe_run_ids`/
    # `safe_evidence`/`runs_acknowledged_pre_gate`/`runs_missing_digest` are
    # all computed EARLIER now (see "#409 Step 3: evidence scan" above,
    # snapshotted strictly before the #438 claim block) -- #438 fix, see that
    # comment for why. Only the two refusal fatals stay at this original
    # position; they are ambivalent to WHEN their inputs were computed
    # (nothing here writes anything the scan would see), so keeping them here
    # preserves this gate's existing relative precedence against
    # previously_converged/ambiguous_sentinels exactly as before #438.
    #
    # DO NOT re-scan here, or anywhere else downstream of the snapshot above.
    # `evidence` is a fixed value from the tree as this invocation FOUND it,
    # not a live query -- that is what makes "refuses against its own
    # writes" structurally inexpressible. A "helpful" refresh of `evidence`
    # /`dispatch_scan`/`runs_missing_digest` here would silently reintroduce
    # the exact self-refusal this snapshot exists to close -- pinned by
    # tests/claim_selector.test.py's own
    # test_step3_admits_a_fresh_claim_that_rewrites_its_own_evidence
    # (section 13): a real claim, rewrite wired in, under --run-resume
    # false, that MUST succeed.
    #
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
        # #438 D3: the claim authorization, keyed by segment id -- a subset
        # of `segs` by construction (validated above). Empty {} when no
        # claim was requested. Each entry is EXACTLY the claim_record.py
        # payload durably written to runs/<run_id>/.claimed.<seg>, field for
        # field and in CLAIM_RECORD_FIELDS order -- nothing is added here.
        #
        # It used to be that payload PLUS a spread of four D6/D10 fields
        # (cache_key_moved_fields, cache_key_movement_machinery_only,
        # cache_key_note, pre_claim_review) that the marker file did not
        # carry. Those four are now record fields, written to the marker
        # like every other one, which is where the evidence for a claim
        # belongs: a consumer reading this JSON and an operator reading the
        # marker file after the fact must not be able to see different
        # things. On the already-claimed path this is the record as re-read
        # FROM DISK, so a re-claim reports the original authorization rather
        # than a freshly recomputed lookalike.
        "claims": claims_payload,
        # #409: a consumer must be able to tell an authorizing result from a
        # merely descriptive one without re-deriving which flags were passed.
        "authorizes_dispatch": authorizes_dispatch,
        "previously_converged": previously_converged,
        # Sentinel paths that were neither absent nor a regular file. Reported
        # on the success path for the same reason runs_missing_digest is: a
        # consumer must be able to see the exact set, not merely that the run
        # passed. Always [] here -- an authorizing invocation with a non-empty
        # set has already refused above, and --classify-only never populates
        # it -- but "the scan ran and found none" and "the scan never ran" are
        # different facts and a caller that can only read a verdict cannot
        # separate them.
        "ambiguous_sentinels": ambiguous_sentinels,
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
        "--from-converged",
        default=None,
        metavar="SEG1,SEG2,...",
        help=(
            "#438: claim these ids for RE-REVIEW under the --from-converged profile "
            "(PLAN.md D2) -- a segment that converged cleanly at least once and was then "
            "hand-edited. Never re-translates. Requires --run-id. A successfully-admitted id "
            "clears the previously_converged refusal for itself only (D5.2)."
        ),
    )
    parser.add_argument(
        "--from-cap",
        default=None,
        metavar="SEG1,SEG2,...",
        help=(
            "#438: claim these ids for RE-REVIEW under the --from-cap profile (PLAN.md D2) "
            "-- a segment that hit the review cap (non_converged, reason=cap) and was then "
            "hand-edited. Never re-translates. Requires --run-id and, being human_escalation, "
            "--only-segs naming the same ids."
        ),
    )
    parser.add_argument(
        "--run-id",
        default=None,
        metavar="RUN_ID",
        help=(
            "#438: the current run id a claim re-stamps a draft's authorization to. Required "
            "whenever --from-converged/--from-cap is given; never derived from an existing "
            "token (PLAN.md D8: deriving it would make a malformed token read as 'not "
            "claimed' and silently proceed). Must be paired with --run-resume."
        ),
    )
    parser.add_argument(
        "--run-resume",
        default=None,
        choices=("true", "false"),
        help=(
            "#409 Step 3 fix: resume_setup.py's own 'resume' field for --run-id, forwarded "
            "verbatim ('true' when --run-id matched a prior run's digest and is being "
            "resumed, 'false' when a fresh id was minted). Required whenever --run-id is "
            "given -- a digest resume_setup.py just wrote for a FRESH id proves only that "
            "resume_setup.py ran as part of this invocation, never that any pre-existing "
            "dispatch evidence bearing the same id was ever gated; this flag is what lets "
            "that be told apart from a legitimate resume."
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
