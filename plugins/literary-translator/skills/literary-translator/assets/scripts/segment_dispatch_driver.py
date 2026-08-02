#!/usr/bin/env python3
"""segment_dispatch_driver.py -- #409 local driver for W5 mass-translate.

STATUS: SKELETON. This release ships the driver's SAFETY PROPERTIES --
launch/process-isolation contract, project-wide lease, the Step 1
re-translate gate, the volume refusal, an append-only journal, and a
race-free codex_job.py dispatch primitive -- proven and tested in
isolation. It does NOT yet run the per-segment translate/review loop
(building/instantiating the actual codex prompts is a separate piece of
work, tracked as the next phase). Run today, this script authenticates
the batch (Step 1 gate + volume cap), journals its own decision, and
exits WITHOUT dispatching any codex job -- "READY, dispatch not yet
implemented" is a deliberate, honest terminal state, not a stub pretending
to be the real thing. `dispatch_codex_job()` below is the tested primitive
the next phase wires into a real per-segment loop.

Filename note: NOT `mass_translate_driver.py` -- `git grep -ln
mass_translate_driver` already returns 7 files (all `tests/*.test.py`
naming the EXISTING Workflow-driving mock-agent harness family, e.g.
`mass_translate_driver_smoke.test.py`), and reusing that name for a
different, unrelated artifact would be actively confusing, not merely
redundant.

## Why a local process, not the Workflow, at all

Measured: the real mass-translate wall clock is 0.40-2.96 h. The agent
Bash tool clamps any single call at 600 s, so a Workflow `agent()` step
cannot wait on a codex job directly -- the whole DISPATCH/WAIT/CONSUME
chunking apparatus (#348) exists only to work around that ceiling from
INSIDE an agent call. A local, out-of-band process has no such ceiling: it
can simply block on `Popen(...).wait()` for the real duration. See
"Property 6" below for what this buys structurally, not just practically.

## Launch contract (the ONE property this script cannot enforce on itself)

The orchestrating Claude session MUST launch this script as:

    nohup python3 {durable_root}/scripts/segment_dispatch_driver.py \\
        [--only-segs SEG1,SEG2,...] [--allow-retranslate-converged] \\
        > {durable_root}/runs/driver.<SESSION_ID>.log 2>&1 < /dev/null & disown

from an ORDINARY FOREGROUND Bash tool call -- NOT `run_in_background`. This
is a recorded anti-pattern in this project, not a style preference:
`run_in_background`'s own poll gets harness-stopped mid-wait while the
spawned worker keeps running, and combining `&`/`disown` with
`run_in_background` produces a false "completed" (the tool reports the
foreground command that backgrounded successfully, not the work the
background process goes on to do). A plain foreground call returns the
instant the shell has forked+disowned the child -- milliseconds -- so it
never approaches the 600 s ceiling; only WAITING on the driver's own exit
would, which nothing does, by design (`disown` + closed stdin/stdout is
what keeps the driver alive past this Bash call's own return).

## The 8 mandatory safety properties, and how each is closed

1. **Launch shape** -- see above; enforced by convention/documentation,
   not by code (a script cannot make its own caller invoke it correctly).
2. **`start_new_session=True` on every `Popen` of `codex_job.py`** --
   `dispatch_codex_job()` below. Otherwise a codex job joins the driver's
   own killable process group: driver death would leave the job running
   with nothing left to run `codex_job.py`'s own `finalize()` (no fail
   sentinel, no terminal joblog, no `.att_pending`), stranding finished
   paid work in a `.att.<seg>.<INV>.<kind>.json` that nothing ever reads
   again (`safe_adopt()` only looks at the canonical path,
   `adopt_pending()` only at `.att_pending`). `os.setsid` exists on macOS;
   the `setsid` *binary* does not, so `start_new_session=True` (which uses
   the syscall, not the binary) is the only portable way to get this.
3. **One project-wide `fcntl.flock`, held by descriptor, for the whole
   process lifetime, never a pid file** -- `acquire_driver_lock()` below,
   on `runs/.driver.lock`. Per-segment leases already exist
   (`codex_job.py`'s own `.codex_job.<seg>.lock`, acquired inside `run()`
   after hygiene); there is no project-level one today (confirmed by
   `git grep -rn LOCK_EX` across `assets/scripts/*.py`: exactly one
   call site, `codex_job.py`'s per-segment lease). Without it, two
   drivers on one project each try to lease every segment `codex_job.py`
   already protects individually, but nothing stops both from reaching
   `select_segments.py` and the volume check redundantly, or from
   producing a confusing pair of "lease-held" `translate-timeout`-shaped
   per-segment failures with no project-level record of WHY. A pid file
   is deliberately not used: checking "is pid N still alive" is exactly
   the stale-lease race a kernel-held flock (auto-released on crash, no
   unlink, no liveness probe) closes structurally.
4. **No new independent `draft_content_sha1`** -- `current_draft_sha1()`
   below IMPORTS `draft_sha1.py`'s own `draft_content_sha1()` rather than
   reimplementing it. See "Property 4" section below for the reasoning.
5. **Append-only per-dispatch journal** -- `append_journal()` below,
   writing to `runs/<SESSION_ID>/driver_journal.jsonl`. `codex_job.py`'s
   own terminal joblog is INTENTIONALLY overwritten by each next dispatch
   (it exists for hygiene's "was a prior job for this seg/kind still
   active" check, not as a history) and deliberately omits `reason`
   (measured on tome 1: 80 on-disk joblogs, every one `kind: "review"` --
   not one surviving record of a translate ever having been dispatched).
   A cause of failure that lives only in a file the next dispatch
   overwrites is not available for `#398`/`#400`-shaped debugging. The
   journal is the durable, independent record.
6. **The #348 race does not vanish on its own** -- "the job finished after
   the last poll" is a structural property of ANY deadline-bounded poll
   loop, chunked or not. `dispatch_codex_job()` below closes it by never
   polling at all: it calls `Popen(..., start_new_session=True)` then
   blocks on `proc.wait(timeout=...)`. `wait()` is the kernel's own
   `wait4`/`waitpid` notification of process termination -- there is no
   "last poll before which nothing looked" gap to reproduce, because
   nothing is polling. This is a stronger closure than "an authoritative
   post-deadline re-read" (the alternative property 6 names): a re-read
   still has to be scheduled at SOME wall-clock instant, and codex.job.py
   itself could terminate microseconds after that instant; a genuine
   `wait()` cannot miss a termination it is the one that observes.
   `codex_job.py`'s OWN internal poll of the codex companion process is a
   separate, already-shipped concern (its own `--deadline-sec`/`--poll-sec`
   machinery) -- what property 6 is about here is the DRIVER's relationship
   to `codex_job.py` AS a child process, and that is exactly what `wait()`
   closes without needing any chunking at all (chunking was only ever
   necessary because #348's polling had to happen INSIDE a 600 s-clamped
   agent Bash call -- a constraint a local Python process never has).
7. **Volume refusal is kept, not replaced by a concurrency limit** -- see
   "Property 7" section below for which knob and why.
8. **The driver goes through the Step 1 gate, never accepts arbitrary
   segment ids** -- `run_select_segments()` below. The driver NEVER
   computes or accepts a bare SEGS list itself; the only segment ids it
   ever acts on are the ones `select_segments.py` itself emits (or, for
   `--only-segs`, ids `select_segments.py` has already validated against
   `manifest.json` and folded into its own `overrides`/`excluded_only_segs`
   accounting). Re-translating a previously-converged segment requires
   `--allow-retranslate-converged` to have been passed to select_segments.py
   deliberately -- the driver forwards the operator's own flag, it never
   defaults it on.

## Property 4 in detail -- import, not shell-out

There are already SEVEN byte-identical implementations of
`draft_content_sha1` in this plugin (`draft_sha1.py`, `ledger_update.py`,
`ledger_merge.py`, `select_segments.py`, `final_audit.py`, `assemble.py`,
`validate_assembled.py`). An eighth, independent one is what's forbidden --
not a fourth, not a specific mechanism. This project's general convention
is "no shared lib between self-contained scripts" (duplicate byte-for-byte
instead) -- but `draft_sha1.py` is explicitly the SOLE authority for this
one hash (its own module docstring says so), and the existing duplicates
are all re-implementations that must be kept byte-identical BY HAND, which
is precisely how "silent divergence -> `draft_sha1_mismatch` -> `stale` ->
mass re-translation" happens. Reusing the authority directly, rather than
adding a ninth hand-maintained copy, is the safer choice specifically for
a NEW consumer that has no legacy byte-identical-duplicate obligation to
preserve. `draft_sha1.py`'s `draft_content_sha1(path)` is a pure,
side-effect-free, stdlib-only function (json/hashlib only) with an
explicit `path` argument -- no hidden global state to worry about --
so `current_draft_sha1()` below loads it via the same
`sys.path.insert(SCRIPTS_DIR); import draft_sha1` idiom `final_audit.py`
already uses for `validate_draft`/`bootstrap_names` (this repo's own
precedent for importing a pure sibling helper directly, per
`scaffold_setup.py`'s own import of `cache_key.py` helpers). This also
avoids spawning a subprocess for every sha1 check the eventual per-segment
loop will make, which shelling out to `draft_sha1.py` per call would cost.
`tests/segment_dispatch_driver.test.py::test_current_draft_sha1_matches_the_cli`
proves this returns byte-identical output to the real `draft_sha1.py` CLI.

## Property 7 in detail -- which knob, and why not `batch_agent_cap`

The resource `batch_agent_cap` estimates is Workflow `agent()` calls. The
driver makes ZERO Workflow `agent()` calls for its own dispatch path --
it calls `codex_job.py` directly via `Popen`, with no wait-chunking
apparatus at all (see property 6). So `batch_agent_cap` measures a
resource this script's own path does not spend; enforcing it here would
be checking the wrong number, not merely a redundant one.
`engine.max_codex_jobs_per_batch` (#409 stage 0, already shipped --
`profile.schema.json`'s own field, already a `resume_setup.SUBST_FIELDS`
member alongside `batch_agent_cap`) measures the resource the driver DOES
spend: real codex dispatches. `check_volume_cap()` below reproduces
`mass-translate-wf.template.js`'s own already-shipped preflight for this
exact knob (`CODEX_JOBS_PER_SEG = max_fix_rounds + 2`,
`estimatedCodexJobs = len(SEGS) * CODEX_JOBS_PER_SEG`), the SAME formula,
because it is the SAME resource under the SAME cap, just measured from a
second, independent entry point -- exactly how `skeptic_setup.py`'s own
preflight duplicates its Workflow template's estimator for the identical
reason (two entry points into one resource, each needing its own gate).
`max_fix_rounds` fix rounds are deliberately NOT counted as codex jobs:
today the fix step is a plain Workflow `agent()` call, never a
`codex_job.py` launch -- this is CURRENT reality, not yet the
codex-as-fixer redesign (a later phase), and counting fixes now would
measure a resource this driver does not spend yet either.
`batch_agent_cap` itself is untouched and unremoved -- it keeps doing its
own job for the glossary/skeptic Workflow passes and for
`resume_setup.SUBST_FIELDS`'s existing required-field contract; this
script simply never reads or enforces it, because a driver-dispatched
batch never triggers the resource it measures.

## What this skeleton deliberately does NOT implement (say so, not stub it)

- The actual per-segment translate/review/fix orchestration -- building
  the real translate/review prompts, threading dispatch tokens, writing
  ledger fragments. `dispatch_codex_job()` is the tested primitive for
  launching one `codex_job.py` invocation correctly; nothing yet calls it
  in a loop over `SEGS`.
- `resume_setup.py` integration (RUN_ID minting, the resume-integrity
  input digest, `SUBST_FIELDS` hashing). Journal entries below use their
  own session id (`fresh_session_id()`, same timestamp SHAPE as
  `resume_setup.fresh_run_id()` for readability, but explicitly NOT that
  RUN_ID) purely to namespace `runs/<session_id>/driver_journal.jsonl`.
  Wiring the real resume-integrity RUN_ID through is part of the
  per-segment orchestration phase, not this skeleton.
- Any of PLAN.md's later "Этап 0" trust-boundary work (a content-addressed
  read-only executable snapshot replacing today's plain durable-root
  script copies; a per-job writable sandbox with descriptor-pinned
  publish; root-threading through the WHOLE selector/resume chain). That
  is a separate, much larger body of work the plan's own later review
  rounds (7septies-7nonies) surfaced as a genuinely new prerequisite --
  it is not among this dispatch's 8 named properties, and this script
  does not attempt it. `codex_job.py`'s own module docstring shows some
  of that hardening (sandbox write-confinement, descriptor-pinned
  publish) already exists at the codex_job.py layer; none of it exists
  yet for the driver-to-codex_job.py or driver-to-select_segments.py
  boundary itself.

## Beyond the 8 named properties -- one deliberate addition, and why

This script also accepts an optional `--plugin-root PATH`, following
EXACTLY the v1.17.0 convention `select_segments.py`/`ledger_merge.py`/
`resume_setup.py`/`review_ready.py`/`canon_validate.py`/`final_audit.py`
already establish (see `references/gotchas.md` §4). Not asked for by
name in this dispatch, but the driver is itself Step-0a-copied (it is a
`PLUGIN_BUNDLE_MEMBERS` script, see below) and resolves TWO siblings --
`select_segments.py` (which DOES accept `--plugin-root`) and
`codex_job.py` (which does NOT yet accept any root-redirect flag at all)
-- from `${durable_root}/scripts/`, the SAME writable-by-codex tree the
whole `--plugin-root` mechanism exists to route around. Omitting this
would leave a brand-new, gate-enforcing script with the exact trust gap
the last several LT-409 hardening rounds closed everywhere else. For
`select_segments.py`, `--plugin-root` is forwarded verbatim (it accepts
the flag) together with a synthesized `--durable-root` (this script has
no `--durable-root` of its own to forward, so what's synthesized is
always its own resolved durable root -- see `resolve_dirs()`). For
`codex_job.py` (a leaf with no root-redirect flags of its own),
`--plugin-root` only changes WHICH FILE this script `Popen`s -- never a
flag forwarded to it -- but that alone is a real improvement: `codex_job.py`
resolves ITS OWN sibling gate scripts (`draft_ready.py`, `validate_draft.py`,
`review_ready.py`) relative to wherever ITS OWN `__file__` sits, so
launching the `{plugin_root}/assets/scripts/codex_job.py` copy transitively
moves codex_job.py's own gate resolution onto the trusted plugin tree too,
with no change to codex_job.py itself. `--durable-root` for the
DATA side (`runs/.driver.lock`, `runs/<session>/`) is likewise optional,
self-anchored by default, matching every other v1.17.0-hardened script.

## Bundle registration

Registered in `cache_key.py`'s `PLUGIN_BUNDLE_MEMBERS`. The criterion is
`cache_key.py`'s own, stated inline above that tuple: a change that
WEAKENS a security boundary must move `plugin_bundle_hash`, or a durable
root scaffolded before the change would go on treating its converged
segments as safe to reuse against a driver that no longer behaves the
same way. This script owns the ACCEPT decision for dispatched work (which
segments even get dispatched, whether a lease/volume/gate refusal is
honored) -- `codex_job.py`'s own inclusion reasoning ("it launches codex
and VALIDATES the isolated attempt before atomically promoting it... an
old buggy driver may have wrongly accepted an artifact") applies to this
script by the identical logic, even though today's skeleton does not yet
dispatch anything itself: the moment it starts shipping as a Step-0a-copied
script, a bug in its OWN gating (the Step 1 check, the volume cap) is
exactly the class of defect `plugin_bundle_hash` exists to re-invalidate
converged work against, so it is registered from this first release, not
deferred until the per-segment loop lands.

## CLI

    python3 segment_dispatch_driver.py
        [--durable-root PATH] [--plugin-root PATH]
        [--only-segs SEG1,SEG2,...] [--allow-retranslate-converged]
        [--allow-empty]

Forwards `--only-segs`/`--allow-retranslate-converged`/`--allow-empty`
verbatim to `select_segments.py` -- see that script's own module
docstring for their exact semantics; this script adds no independent
meaning to any of them.

Exit 0 = both gates passed, a "ready, not yet dispatching" summary was
journaled and printed. Exit 1 = a gate refused (lock contention, the Step
1 re-translate gate, or the volume cap) -- the refusal reason is in the
printed JSON and the journal. Exit 2 = usage/environment error. Exactly
ONE JSON line on stdout either way; all human-readable detail on stderr.
"""

import argparse
import fcntl
import importlib.util
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import NoReturn

try:
    import yaml
except ImportError:
    yaml = None

# ---------------------------------------------------------------------------
# Self-anchoring -- identical convention to select_segments.py/ledger_merge.py.
# ---------------------------------------------------------------------------

SCRIPTS_DIR = Path(__file__).resolve().parent
DURABLE_ROOT = SCRIPTS_DIR.parent
SELECT_SEGMENTS_SCRIPT = SCRIPTS_DIR / "select_segments.py"
CODEX_JOB_SCRIPT = SCRIPTS_DIR / "codex_job.py"

DRIVER_LOCK_NAME = ".driver.lock"


def resolve_dirs(durable_root_str, plugin_root_str=None):
    """LT-409 convention: `durable_root_str` governs DATA (runs/) -- rebuilt
    from that root when given, self-anchored otherwise.

    `plugin_root_str` is a SEPARATE, independent input governing where the
    SIBLING SCRIPTS this script shells out to / Popens (select_segments.py,
    codex_job.py) are resolved from -- deliberately NEVER derived from
    `durable_root_str`, for the identical tampered-copy reason
    select_segments.py's own `resolve_dirs()` states. When given, each
    sibling resolves as `{plugin_root}/assets/scripts/<name>.py`.
    `plugin_root_str=None` reproduces today's self-anchored sibling lookup
    unchanged. Both None -> today's exact self-anchored values.
    """
    if durable_root_str is None:
        durable_root = DURABLE_ROOT
    else:
        durable_root = Path(durable_root_str).resolve()

    if plugin_root_str is None:
        select_segments_script = SELECT_SEGMENTS_SCRIPT
        codex_job_script = CODEX_JOB_SCRIPT
    else:
        plugin_scripts_dir = Path(plugin_root_str).resolve() / "assets" / "scripts"
        select_segments_script = plugin_scripts_dir / "select_segments.py"
        codex_job_script = plugin_scripts_dir / "codex_job.py"

    return {
        "durable_root": durable_root,
        "runs_dir": durable_root / "runs",
        "select_segments_script": select_segments_script,
        "codex_job_script": codex_job_script,
    }


def _root_forward_args(dirs: dict, durable_root_str, plugin_root_str) -> list:
    """The exact --durable-root/--plugin-root pair to forward to
    select_segments.py (which accepts BOTH). Identical logic to
    select_segments.py's own `_root_forward_args()`/final_audit.py's own
    `run_completeness_gate()` -- an explicit --durable-root MUST be
    forwarded whenever --plugin-root is given, even when THIS script
    itself was never passed --durable-root, because select_segments.py no
    longer physically sits under durable_root once relocated and would
    otherwise self-anchor against the wrong tree. codex_job.py is NOT
    covered by this helper -- it accepts neither flag; only the file path
    Popen'd for it changes (see resolve_dirs()).
    """
    args = []
    if durable_root_str is not None:
        args += ["--durable-root", durable_root_str]
    elif plugin_root_str is not None:
        args += ["--durable-root", str(dirs["durable_root"])]
    if plugin_root_str is not None:
        args += ["--plugin-root", plugin_root_str]
    return args


class DriverError(Exception):
    """Raised for any failure that should surface as a top-level FAILURE
    JSON payload on stdout (exit 1 or 2), never a bare traceback."""

    def __init__(self, message: str, exit_code: int = 1, **extra):
        super().__init__(message)
        self.exit_code = exit_code
        self.extra = extra


def fatal(message: str, exit_code: int = 1, **extra) -> NoReturn:
    raise DriverError(message, exit_code=exit_code, **extra)


# ---------------------------------------------------------------------------
# Segment id safety contract -- duplicated byte-for-byte per this project's
# "no shared lib between self-contained scripts" convention. This script
# never builds a path from a segment id directly (it only ever forwards ids
# to/from select_segments.py, which validates them against manifest.json
# itself), but --only-segs values are still checked here FIRST so a
# malformed id is refused before it is ever spliced into the
# select_segments.py subprocess argv.
# ---------------------------------------------------------------------------

_SEG_ID_RE = re.compile(r"(?:FRONTBACK:)?[A-Za-z0-9_]+")


def validate_seg(seg):
    if not isinstance(seg, str) or not seg:
        return "segment id must be a non-empty string."
    if not _SEG_ID_RE.fullmatch(seg):
        return (
            "segment id must match (FRONTBACK:)?[A-Za-z0-9_]+ (no path "
            f"separators, '..', or shell metacharacters); got {seg!r}."
        )
    return None


# ---------------------------------------------------------------------------
# Property 4 -- draft_content_sha1 reuse via import, never a new copy.
# See module docstring's "Property 4 in detail" section for the reasoning.
# ---------------------------------------------------------------------------


def _load_draft_sha1_module(scripts_dir: Path = SCRIPTS_DIR):
    """Loads the REAL sibling draft_sha1.py via importlib (never a bare
    `import draft_sha1`, which would silently succeed against whatever
    happens to be first on sys.path rather than THIS resolved sibling)."""
    path = scripts_dir / "draft_sha1.py"
    if not path.is_file():
        fatal(f"draft_sha1.py not found at {path}")
    spec = importlib.util.spec_from_file_location("draft_sha1", str(path))
    if spec is None or spec.loader is None:
        fatal(f"could not load draft_sha1.py from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def current_draft_sha1(seg: str, segments_dir: Path, scripts_dir: Path = SCRIPTS_DIR) -> str:
    """The draft content sha1 for `seg`, computed by IMPORTING
    draft_sha1.py's own draft_content_sha1() -- never an independent
    reimplementation. Raises DriverError (never a bare traceback) on any
    failure -- missing draft, unreadable, invalid JSON, wrong shape."""
    mod = _load_draft_sha1_module(scripts_dir)
    path = mod.draft_path(seg, segments_dir)
    if not path.is_file():
        fatal(f"draft not found for segment {seg!r} at {path}")
    try:
        return mod.draft_content_sha1(path)
    except OSError as exc:
        fatal(f"could not read draft for segment {seg!r} at {path}: {exc}")
    except json.JSONDecodeError as exc:
        fatal(f"draft for segment {seg!r} at {path} is not valid JSON: {exc}")
    except ValueError as exc:
        fatal(f"draft for segment {seg!r}: {exc}")


# ---------------------------------------------------------------------------
# Property 3 -- one project-wide fcntl.flock, held by descriptor, never a
# pid file.
# ---------------------------------------------------------------------------


def driver_lock_path(durable_root: Path) -> Path:
    return durable_root / "runs" / DRIVER_LOCK_NAME


def acquire_driver_lock(durable_root: Path):
    """Acquires the project-wide LOCK_EX|LOCK_NB lease on
    runs/.driver.lock. Returns the open file descriptor on success -- the
    CALLER must keep it open (never close it) for the whole process
    lifetime; closing (or letting the process exit) is what releases it,
    kernel-side, with no unlink and no stale-pid probe ever needed.

    Raises DriverError (exit_code=1) if another process already holds the
    lease -- non-blocking by design (LOCK_NB): a second driver on the same
    project must refuse immediately and namelessly-loudly, never queue
    behind the first one silently.

    The lock file's CONTENT (pid + UTC start time) is written AFTER a
    successful acquire, purely for a human to read while debugging "who is
    holding this" -- it is diagnostic only. The lock itself is the kernel
    flock; nothing in this script ever reads that content back to decide
    whether the lease is held (that would reintroduce the exact
    liveness-probe race a kernel-held, never-unlinked flock exists to
    avoid).
    """
    lock_path = driver_lock_path(durable_root)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        fatal(
            f"another driver already holds the project lease at {lock_path} "
            f"-- refusing to start a second one against the same project. "
            f"If you are certain no other driver is running, the lease is "
            f"kernel-held (auto-released on process exit/crash); it cannot "
            f"be stale while a process holds it.",
            exit_code=1,
            lock_path=str(lock_path),
        )
    try:
        os.ftruncate(fd, 0)
        os.lseek(fd, 0, os.SEEK_SET)
        os.write(
            fd,
            json.dumps(
                {"pid": os.getpid(), "started_at": _utc_now_iso()}, ensure_ascii=False
            ).encode("utf-8")
            + b"\n",
        )
    except OSError:
        pass  # diagnostic content only -- never fatal to the lease itself
    return fd


def release_driver_lock(fd) -> None:
    """Best-effort explicit close -- matches codex_job.py's own
    `finally: os.close(lock_fd)` pattern. Not required for correctness
    (process exit releases the kernel flock regardless) but keeps the fd
    table tidy for a long-lived driver that might do other work later."""
    try:
        os.close(fd)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Property 7 -- volume refusal via engine.max_codex_jobs_per_batch.
# See module docstring's "Property 7 in detail" section for the reasoning.
# Formula and message shape mirror mass-translate-wf.template.js's own
# already-shipped preflight for this exact knob, verbatim.
# ---------------------------------------------------------------------------


def codex_jobs_per_segment(max_fix_rounds: int) -> int:
    """1 translate job + (max_fix_rounds + 1) review jobs (one per normal
    round, plus the one mandatory final confirming review). Fix rounds are
    NOT counted -- see module docstring."""
    return max_fix_rounds + 2


def check_volume_cap(n_segs: int, max_fix_rounds: int, max_codex_jobs_per_batch: int):
    """Returns None if `n_segs` is within the cap, or a refusal dict
    (mirrors mass-translate-wf.template.js's own `{reason,
    estimatedCodexJobs, codexJobsCap}` result shape) otherwise. Never
    raises -- this is a pure, side-effect-free check the caller decides
    what to do with."""
    per_seg = codex_jobs_per_segment(max_fix_rounds)
    estimated = n_segs * per_seg
    if estimated <= max_codex_jobs_per_batch:
        return None
    return {
        "reason": "batch-too-large-codex-jobs",
        "estimatedCodexJobs": estimated,
        "codexJobsCap": max_codex_jobs_per_batch,
        "message": (
            f"Batch too large: this batch needs estimatedCodexJobs={estimated} "
            f"for {n_segs} segment(s) at max_fix_rounds={max_fix_rounds}, over "
            f"the effective engine.max_codex_jobs_per_batch limit of "
            f"{max_codex_jobs_per_batch}. Raise it in profile.yml under "
            f"engine: to allow a larger batch."
        ),
    }


# ---------------------------------------------------------------------------
# profile.yml resolution -- duplicated per this project's "no shared lib"
# convention (matches validate_draft.py's/cache_key.py's own load_profile()
# shape). Only the two fields this script actually needs are read.
# ---------------------------------------------------------------------------


def load_engine_config(durable_root: Path) -> dict:
    """Returns {"max_fix_rounds": int, "max_codex_jobs_per_batch": int}
    resolved from profile.yml (via the ownership marker, matching every
    other profile-consuming script). `max_codex_jobs_per_batch` falls back
    to profile.schema.json's own documented default (400) when the
    profile omits the OPTIONAL key -- the schema's own "default" annotation
    is documentation-only (nothing fills it in at validation time), so
    every consumer must apply it independently; this mirrors
    mass-translate-wf.template.js's own instantiation-time fallback for
    the identical field."""
    if yaml is None:
        fatal(
            "missing required dependency 'PyYAML' to read profile.yml. "
            "Install with: pip install -r requirements.txt from the "
            "literary-translator plugin's own directory.",
            exit_code=2,
        )
    marker_path = durable_root / ".literary-translator-root.json"
    if not marker_path.is_file():
        fatal(
            f"ownership marker not found: {marker_path} -- run Step 0a "
            f"(durable-root scaffolding) before this driver.",
            exit_code=2,
        )
    try:
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fatal(f"ownership marker at {marker_path} is not valid JSON: {exc}", exit_code=2)
    owner_profile_path = marker.get("owner_profile_path") if isinstance(marker, dict) else None
    if not owner_profile_path:
        fatal(f"ownership marker at {marker_path} has no owner_profile_path", exit_code=2)
    profile_path = Path(owner_profile_path)
    if not profile_path.is_file():
        fatal(f"profile.yml not found at {profile_path} (per {marker_path})", exit_code=2)
    try:
        profile = yaml.safe_load(profile_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        fatal(f"profile.yml at {profile_path} is not valid YAML: {exc}", exit_code=2)
    if not isinstance(profile, dict):
        fatal(f"profile.yml at {profile_path} did not parse to a mapping", exit_code=2)

    engine = profile.get("engine")
    if not isinstance(engine, dict) or "max_fix_rounds" not in engine:
        fatal(f"profile.yml at {profile_path} missing required field: engine.max_fix_rounds", exit_code=2)
    max_fix_rounds = engine["max_fix_rounds"]
    if not isinstance(max_fix_rounds, int) or isinstance(max_fix_rounds, bool) or max_fix_rounds < 0:
        fatal(
            f"profile.yml at {profile_path}: engine.max_fix_rounds must be "
            f"a non-negative integer, got {max_fix_rounds!r}",
            exit_code=2,
        )
    max_codex_jobs_per_batch = engine.get("max_codex_jobs_per_batch", 400)
    if not isinstance(max_codex_jobs_per_batch, int) or isinstance(max_codex_jobs_per_batch, bool) or max_codex_jobs_per_batch < 1:
        fatal(
            f"profile.yml at {profile_path}: engine.max_codex_jobs_per_batch "
            f"must be a positive integer, got {max_codex_jobs_per_batch!r}",
            exit_code=2,
        )
    return {"max_fix_rounds": max_fix_rounds, "max_codex_jobs_per_batch": max_codex_jobs_per_batch}


# ---------------------------------------------------------------------------
# Property 8 -- the Step 1 gate. Every segment id this script ever acts on
# comes FROM select_segments.py's own output; this script never computes or
# accepts a bare SEGS list of its own.
# ---------------------------------------------------------------------------


def run_select_segments(
    dirs: dict,
    *,
    only_segs=None,
    allow_retranslate_converged=False,
    allow_empty=False,
    durable_root_str=None,
    plugin_root_str=None,
) -> dict:
    """Shells out to select_segments.py -- the Step 1 re-translate gate --
    WITHOUT --classify-only, so a clean result genuinely authorizes
    dispatch (this script is a dispatcher, not an audit like
    final_audit.py's own --classify-only call). Returns the parsed JSON
    payload on ANY response (success or refusal) -- the caller decides
    what a refusal means; this function itself never raises for a
    select_segments.py-side refusal, only for a genuine invocation failure
    (missing script, bad subprocess, unparseable output).
    """
    select_segments_script = dirs["select_segments_script"]
    if not select_segments_script.is_file():
        fatal(f"select_segments.py not found at {select_segments_script}", exit_code=2)

    cmd = [sys.executable, str(select_segments_script)]
    if only_segs is not None:
        cmd += ["--only-segs", only_segs]
    if allow_retranslate_converged:
        cmd += ["--allow-retranslate-converged"]
    if allow_empty:
        cmd += ["--allow-empty"]
    cmd += _root_forward_args(dirs, durable_root_str, plugin_root_str)

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
            cwd=str(dirs["durable_root"]),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        fatal(f"could not run select_segments.py: {exc}", exit_code=2)

    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        fatal(
            "select_segments.py did not print valid JSON on stdout "
            f"(exit {proc.returncode}): stdout={proc.stdout!r} stderr={proc.stderr!r}",
            exit_code=2,
        )
    if not isinstance(payload, dict):
        fatal(f"select_segments.py printed a non-object JSON value: {payload!r}", exit_code=2)
    return payload


# ---------------------------------------------------------------------------
# Properties 2 + 6 -- the codex_job.py dispatch primitive. Not yet called by
# main() below in a per-segment loop (see module docstring); provided and
# tested as the primitive the next phase wires in.
# ---------------------------------------------------------------------------


def dispatch_codex_job(codex_job_script: Path, job_args: list, *, wait_timeout: float, **popen_kwargs):
    """Launches ONE codex_job.py invocation (`[sys.executable,
    str(codex_job_script), *job_args]`) as a fully detached child --
    `start_new_session=True` (property 2) -- and blocks on `proc.wait()`
    (property 6) rather than polling. stdin is /dev/null (a detached
    worker must never block on or inherit a controlling terminal's input).
    stdout/stderr are inherited by default; pass explicit `stdout=`/
    `stderr=` via `**popen_kwargs` at the call site if the caller wants
    them captured instead (deliberately not hardcoded here, so a caller
    can redirect to its own per-dispatch log file).

    `codex_job_script` is taken as an EXPLICIT, separate argument (never
    folded into `job_args[0]`) so the file-existence check below is
    unambiguous -- checking "argv[0] exists" would actually be checking
    `sys.executable`, which always exists and proves nothing about
    whether the intended codex_job.py copy does.

    Returns the child's exit code (codex_job.py's own documented contract:
    0 = promoted/adopted, 1 = recoverable failure, 2 = usage/env error).

    Raises DriverError if `proc.wait(timeout=wait_timeout)` itself expires
    -- codex_job.py has its own internal `--deadline-sec`/finalize budget
    and is expected to always terminate within it; `wait_timeout` here is
    a defense-in-depth backstop, not the mechanism that closes property 6
    (that is the plain, un-timed-out `wait()` call itself). On backstop
    expiry the child (which is in its OWN session, so this cannot affect
    anything else) is SIGKILLed and reaped via a second `wait()` before
    raising, so no zombie is left behind.
    """
    if not codex_job_script.is_file():
        fatal(f"codex_job.py not found at {codex_job_script}")
    argv = [sys.executable, str(codex_job_script), *job_args]
    proc = subprocess.Popen(
        argv,
        start_new_session=True,
        stdin=subprocess.DEVNULL,
        **popen_kwargs,
    )
    try:
        return proc.wait(timeout=wait_timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()  # reap -- never leave a zombie behind (kill() only signals)
        fatal(
            f"codex_job.py (pid {proc.pid}) did not terminate within its own "
            f"deadline (backstop wait_timeout={wait_timeout}s exceeded) -- "
            f"killed and reaped. This should not happen if codex_job.py's own "
            f"--deadline-sec/finalize budget is honored; treat as a driver-level "
            f"failure for this dispatch, not a normal 'not ready yet' outcome.",
        )


# ---------------------------------------------------------------------------
# Property 5 -- append-only per-dispatch journal.
# ---------------------------------------------------------------------------


def fresh_session_id() -> str:
    """Colon-free sortable timestamp, e.g. '20260802T143022Z' -- the SAME
    shape resume_setup.py's own fresh_run_id() uses, for readability, but
    explicitly NOT the resume-integrity RUN_ID (see module docstring).
    """
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def journal_path(durable_root: Path, session_id: str) -> Path:
    return durable_root / "runs" / session_id / "driver_journal.jsonl"


def append_journal(durable_root: Path, session_id: str, event: dict) -> None:
    """Appends ONE JSON line to runs/<session_id>/driver_journal.jsonl.
    Append-only by construction (open with 'a', never truncated, never
    read-modify-written) -- unlike codex_job.py's own terminal joblog,
    every entry this function ever writes survives every later dispatch.
    Each entry is stamped with a UTC timestamp and the event's own `type`;
    the caller supplies the rest of the payload. Best-effort: a journal
    write failure is logged to stderr but never aborts the driver -- the
    journal is a durable RECORD, not a gate; losing one entry must not
    itself lose the ability to actually run the batch. flush()+fsync()
    after every write, since this file is exactly the kind of durable-
    audit-trail artifact the driver's own crash-recovery reasoning (a
    reader debugging AFTER a driver death) depends on."""
    path = journal_path(durable_root, session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {"ts": _utc_now_iso(), **event}
    line = json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n"
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line)
            fh.flush()
            os.fsync(fh.fileno())
    except OSError as exc:
        print(f"segment_dispatch_driver.py: warning: could not write journal entry to {path}: {exc}", file=sys.stderr)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# CLI / orchestration
# ---------------------------------------------------------------------------


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "#409 local driver (SKELETON) for W5 mass-translate -- see this "
            "file's own module docstring for the safety properties this "
            "release closes and what it deliberately does not implement yet."
        )
    )
    parser.add_argument(
        "--only-segs",
        default=None,
        metavar="SEG1,SEG2,...",
        help="Forwarded verbatim to select_segments.py's own --only-segs. Omit to select the full eligible set.",
    )
    parser.add_argument(
        "--allow-retranslate-converged",
        action="store_true",
        help="Forwarded verbatim to select_segments.py's own --allow-retranslate-converged.",
    )
    parser.add_argument(
        "--allow-empty",
        action="store_true",
        help="Forwarded verbatim to select_segments.py's own --allow-empty.",
    )
    parser.add_argument(
        "--durable-root",
        default=None,
        metavar="PATH",
        help=(
            "Use PATH as the DATA root instead of this script's own "
            "self-anchored location -- replaces where runs/ (the project "
            "lease, the journal) is found, forwarded to select_segments.py "
            "as its own --durable-root. Optional; omit for today's "
            "self-anchored behavior. Independent of --plugin-root below."
        ),
    )
    parser.add_argument(
        "--plugin-root",
        default=None,
        metavar="PATH",
        help=(
            "Use PATH (the plugin's own install root, i.e. {{PLUGIN_ROOT}}) "
            "to resolve the sibling select_segments.py/codex_job.py scripts "
            "this script shells out to/Popens, as "
            "{PATH}/assets/scripts/<name>.py -- deliberately NEVER derived "
            "from --durable-root. Optional; omit for today's self-anchored "
            "sibling lookup. See this file's own module docstring for the "
            "full rationale (this flag is a deliberate addition beyond the "
            "8 named safety properties)."
        ),
    )
    return parser


def run(args, dirs: dict) -> dict:
    session_id = fresh_session_id()
    durable_root = dirs["durable_root"]

    if args.only_segs is not None:
        for seg in (s.strip() for s in args.only_segs.split(",") if s.strip()):
            problem = validate_seg(seg)
            if problem is not None:
                fatal(f"--only-segs: unsafe segment id: {problem}", exit_code=2)

    lock_fd = acquire_driver_lock(durable_root)
    append_journal(durable_root, session_id, {"type": "driver_started", "pid": os.getpid()})
    try:
        select_result = run_select_segments(
            dirs,
            only_segs=args.only_segs,
            allow_retranslate_converged=args.allow_retranslate_converged,
            allow_empty=args.allow_empty,
            durable_root_str=args.durable_root,
            plugin_root_str=args.plugin_root,
        )
        if not select_result.get("success"):
            append_journal(
                durable_root, session_id,
                {"type": "step1_gate_refused", "error": select_result.get("error")},
            )
            fatal(
                f"Step 1 gate refused: {select_result.get('error')}",
                exit_code=1,
                classification=select_result.get("classification"),
                counts=select_result.get("counts"),
            )

        segs = select_result.get("segs")
        if not isinstance(segs, list):
            fatal("select_segments.py's JSON output has no 'segs' array", exit_code=2)
        append_journal(
            durable_root, session_id,
            {"type": "step1_gate_passed", "segs": segs, "counts": select_result.get("counts")},
        )

        engine_cfg = load_engine_config(durable_root)
        volume_refusal = check_volume_cap(
            len(segs), engine_cfg["max_fix_rounds"], engine_cfg["max_codex_jobs_per_batch"]
        )
        if volume_refusal is not None:
            append_journal(durable_root, session_id, {"type": "volume_check_refused", **volume_refusal})
            fatal(
                volume_refusal["message"],
                exit_code=1,
                reason=volume_refusal["reason"],
                estimatedCodexJobs=volume_refusal["estimatedCodexJobs"],
                codexJobsCap=volume_refusal["codexJobsCap"],
            )

        append_journal(
            durable_root, session_id,
            {
                "type": "volume_check_passed",
                "estimatedCodexJobs": len(segs) * codex_jobs_per_segment(engine_cfg["max_fix_rounds"]),
                "codexJobsCap": engine_cfg["max_codex_jobs_per_batch"],
            },
        )

        result = {
            "success": True,
            "session_id": session_id,
            "durable_root": str(durable_root),
            "segs": segs,
            "counts": select_result.get("counts"),
            "engine": engine_cfg,
            "dispatched": False,
            "note": (
                "SKELETON: gates passed, nothing dispatched yet -- the "
                "per-segment translate/review loop is a later phase. See "
                "this script's own module docstring."
            ),
        }
        append_journal(durable_root, session_id, {"type": "driver_exit", "success": True})
        return result
    finally:
        release_driver_lock(lock_fd)


def main(argv=None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    try:
        dirs = resolve_dirs(args.durable_root, args.plugin_root)
        result = run(args, dirs)
    except DriverError as exc:
        payload = {"success": False, "error": str(exc), **exc.extra}
        print(json.dumps(payload, ensure_ascii=False))
        return exc.exit_code
    except Exception as exc:  # pragma: no cover -- defensive catch-all
        print(
            json.dumps({"success": False, "error": f"unexpected error: {exc}"}, ensure_ascii=False)
        )
        return 2

    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
