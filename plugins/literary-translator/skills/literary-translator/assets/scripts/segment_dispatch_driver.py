#!/usr/bin/env python3
"""segment_dispatch_driver.py -- #409 local driver for W5 mass-translate.

STATUS: Phase 2 -- the real per-segment translate/review dispatch loop.
Phase 1 shipped the driver's SAFETY PROPERTIES (launch/process-isolation
contract, project-wide lease, the Step 1 re-translate gate, the volume
refusal, an append-only journal, a race-free codex_job.py dispatch
primitive). This release wires those into a concurrency-bounded loop that,
for every SEGS entry select_segments.py authorizes: resolves the
resume-integrity RUN_ID via resume_setup.py, dispatches a translate
codex_job.py job, then dispatches ONE review round at a time -- reading
codex prompt text by EXECUTING mass-translate-wf.template.js's own builder
functions under Node (never a hand-written second copy), reading
codex_job.py's own reported `reason`/`error_detail` for any failure (never
inventing one), and driving ledger/cache-key bookkeeping directly through
ledger_update.py/cache_key.py (no agent() indirection -- this mechanical
bookkeeping never needed judgment, only a shell call, which is exactly
what this driver has natively).

One capability this driver genuinely does NOT have: performing the FIX
step. Applying review findings to a draft is a real LLM content-editing
turn (mass-translate-wf.template.js's own `callFix`/`fixPrompt`, dispatched
via a Claude `agent()` call today) -- a plain Python process has no
equivalent capability, and PLAN.md's own step order defers redesigning fix
as a codex_job.py dispatch to a LATER phase ("Шаг 5 (B)"), explicitly
because it only pays off once this driver already exists. So when a
segment's review comes back not-clean, `process_segment()` below stops at
that segment and returns a `needs_fix` result carrying the round label,
the findings, AND the exact fix prompt text (rendered the same
executed-template way as every other prompt) -- the caller (today: the
orchestrating Claude session running W5, exactly as PLAN.md's own step
order anticipates) performs ONE Claude fix turn using that prompt, then
re-invokes this driver, which re-derives the segment's state from durable
disk facts (see `derive_next_action()`) and picks up at the next review
round. This driver's OWN contribution is eliminating the WAIT-polling
agent() calls around translate/review (#348's chunking apparatus) -- "B
only pays off after the driver removes the wait agents" is the project's
own framing for exactly this split.

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

## What this driver deliberately does NOT implement (say so, not stub it)

- The FIX step -- see the STATUS section above. `process_segment()`
  returns `needs_fix` (round label, findings, and the exact fix prompt
  text) instead of performing it.
- `mass-translate-wf.template.js`'s own W6 (`log(...)`d final summary) /
  batch-level `mergeLedgerPrompt` completeness check. This driver reports
  its own per-segment results (`run()`'s returned `summary`); the batch-
  final `ledger_merge.py --expected-segs ... --run-token ...` completeness
  re-check PLAN.md's Шаг 4 acceptance criteria describe is not wired in
  here -- it is a single, whole-batch, end-of-run concern the orchestrating
  session can run directly (mirroring `mergeLedgerPrompt`'s own script
  call, again with no agent() indirection needed) after this driver's
  `results` show every segment converged or accounted for.
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
same way. This script owns the ACCEPT decision for dispatched work (which segments
even get dispatched, whether a lease/volume/gate refusal is honored, and --
since Phase 2 -- which codex prompt/argv gets launched and which ledger
write gets recorded) -- `codex_job.py`'s own inclusion reasoning ("it
launches codex and VALIDATES the isolated attempt before atomically
promoting it... an old buggy driver may have wrongly accepted an
artifact") applies to this script by the identical logic: a bug in its own
gating (Step 1, the volume cap) OR in its own dispatch/ledger-write logic
is exactly the class of defect `plugin_bundle_hash` exists to
re-invalidate converged work against.

## CLI

    python3 segment_dispatch_driver.py
        [--durable-root PATH] [--plugin-root PATH]
        [--only-segs SEG1,SEG2,...] [--allow-retranslate-converged]
        [--allow-empty] [--max-concurrent-codex-jobs N] [--node BIN]

Forwards `--only-segs`/`--allow-retranslate-converged`/`--allow-empty`
verbatim to `select_segments.py` -- see that script's own module
docstring for their exact semantics; this script adds no independent
meaning to any of them. `--max-concurrent-codex-jobs` (default 40) and
`--node` are this driver's own -- see `build_arg_parser()`'s own help text
for the concurrency default's justification.

Exit 0 = every gate passed and the per-segment loop ran to completion
(a completion that reports EVERY segment converged, needs_fix, or failed
in its own `results`/`summary` -- exit 0 does NOT itself mean every
segment converged; read `summary.failed`/`summary.needs_fix`). Exit 1 = a
gate refused before any dispatch (lock contention, the Step 1 re-translate
gate, or the volume cap) -- the refusal reason is in the printed JSON and
the journal. Exit 2 = usage/environment error. Exactly ONE JSON line on
stdout either way; all human-readable detail on stderr.
"""

import argparse
import fcntl
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import uuid
from concurrent.futures import ThreadPoolExecutor
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
TEMPLATES_DIR = SCRIPTS_DIR.parent / "templates"
SELECT_SEGMENTS_SCRIPT = SCRIPTS_DIR / "select_segments.py"
CODEX_JOB_SCRIPT = SCRIPTS_DIR / "codex_job.py"

DRIVER_LOCK_NAME = ".driver.lock"

# Phase 2 -- every additional sibling this script shells out to for the real
# per-segment loop, beyond the two the skeleton already resolved. Every name
# here is Step-0a-copied to ${durable_root}/scripts/ exactly like
# select_segments.py/codex_job.py, so it gets the identical --plugin-root
# redirect treatment in resolve_dirs() below -- one table, not six near-
# duplicate if/else blocks.
_PHASE2_SIBLING_SCRIPTS = (
    "resume_setup.py",
    "resolve_codex_companion.py",
    "ledger_update.py",
    "cache_key.py",
    "draft_ready.py",
    "validate_draft.py",
    "review_ready.py",
)
_TEMPLATE_NAME = "mass-translate-wf.template.js"


def resolve_dirs(durable_root_str, plugin_root_str=None):
    """LT-409 convention: `durable_root_str` governs DATA (runs/) -- rebuilt
    from that root when given, self-anchored otherwise.

    `plugin_root_str` is a SEPARATE, independent input governing where the
    SIBLING SCRIPTS this script shells out to / Popens (select_segments.py,
    codex_job.py, and -- Phase 2 -- resume_setup.py, resolve_codex_companion.py,
    ledger_update.py, cache_key.py, draft_ready.py, validate_draft.py,
    review_ready.py, plus the mass-translate-wf.template.js TEMPLATE this
    script reads to obtain codex prompt text) are resolved from --
    deliberately NEVER derived from `durable_root_str`, for the identical
    tampered-copy reason select_segments.py's own `resolve_dirs()` states.
    When given, each sibling script resolves as
    `{plugin_root}/assets/scripts/<name>.py` and the template as
    `{plugin_root}/assets/templates/mass-translate-wf.template.js`.
    `plugin_root_str=None` reproduces today's self-anchored sibling lookup
    unchanged. Both None -> today's exact self-anchored values.
    """
    if durable_root_str is None:
        durable_root = DURABLE_ROOT
    else:
        durable_root = Path(durable_root_str).resolve()

    def _script_key(name: str) -> str:
        return name[: -len(".py")] + "_script"

    if plugin_root_str is None:
        select_segments_script = SELECT_SEGMENTS_SCRIPT
        codex_job_script = CODEX_JOB_SCRIPT
        scripts = {_script_key(name): SCRIPTS_DIR / name for name in _PHASE2_SIBLING_SCRIPTS}
        template_script = TEMPLATES_DIR / _TEMPLATE_NAME
        scripts_dir = SCRIPTS_DIR
    else:
        plugin_root = Path(plugin_root_str).resolve()
        plugin_scripts_dir = plugin_root / "assets" / "scripts"
        select_segments_script = plugin_scripts_dir / "select_segments.py"
        codex_job_script = plugin_scripts_dir / "codex_job.py"
        scripts = {_script_key(name): plugin_scripts_dir / name for name in _PHASE2_SIBLING_SCRIPTS}
        template_script = plugin_root / "assets" / "templates" / _TEMPLATE_NAME
        scripts_dir = plugin_scripts_dir

    dirs = {
        "durable_root": durable_root,
        "runs_dir": durable_root / "runs",
        "select_segments_script": select_segments_script,
        "codex_job_script": codex_job_script,
        "template_script": template_script,
        "scripts_dir": scripts_dir,
    }
    dirs.update(scripts)
    return dirs


def _root_forward_args(dirs: dict, durable_root_str, plugin_root_str, *, supports_plugin_root=True) -> list:
    """The exact --durable-root[/--plugin-root] pair to forward to a Phase 2
    sibling script. Identical logic to select_segments.py's own
    `_root_forward_args()`/final_audit.py's own `run_completeness_gate()` --
    an explicit --durable-root MUST be forwarded whenever --plugin-root is
    given, even when THIS script itself was never passed --durable-root,
    because the sibling no longer physically sits under durable_root once
    relocated and would otherwise self-anchor against the wrong tree.

    `supports_plugin_root=False` (draft_ready.py, validate_draft.py,
    ledger_update.py, cache_key.py, resolve_codex_companion.py -- all
    LEAVES per their own module docstrings) omits --plugin-root from the
    result even when plugin_root_str is set; --durable-root is still
    forwarded so the leaf reads the right DATA root. select_segments.py,
    resume_setup.py and review_ready.py accept both (the default).
    codex_job.py is NOT covered by this helper at all -- it accepts
    neither flag on the data side; only the file path Popen'd for it
    changes (see resolve_dirs()).
    """
    args = []
    if durable_root_str is not None:
        args += ["--durable-root", durable_root_str]
    elif plugin_root_str is not None:
        args += ["--durable-root", str(dirs["durable_root"])]
    if plugin_root_str is not None and supports_plugin_root:
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
    `start_new_session=True` (property 2) -- and blocks on
    `proc.communicate()` (property 6) rather than polling.
    `communicate()` is `wait()` PLUS concurrent pipe-draining -- it still
    closes property 6 exactly as a bare `wait()` would (there is no
    "last poll before which nothing looked" gap either way; see the module
    docstring's "Property 6 in detail"), while also letting this function
    return the child's own captured output instead of forcing every caller
    to re-derive it from the terminal joblog file. stdin is /dev/null (a
    detached worker must never block on or inherit a controlling
    terminal's input). stdout/stderr default to PIPE (captured) unless the
    caller overrides either via `**popen_kwargs` -- e.g. to redirect to a
    log file instead.

    `codex_job_script` is taken as an EXPLICIT, separate argument (never
    folded into `job_args[0]`) so the file-existence check below is
    unambiguous -- checking "argv[0] exists" would actually be checking
    `sys.executable`, which always exists and proves nothing about
    whether the intended codex_job.py copy does.

    Returns {"exit_code": int, "stdout": str|None, "stderr": str|None} --
    codex_job.py's own documented exit contract (0 = promoted/adopted,
    1 = recoverable failure, 2 = usage/env error) plus whatever text landed
    on each stream (None for a stream the caller redirected away from
    PIPE, e.g. DEVNULL). codex_job.py's own `finalize()` writes its one-line
    result JSON to stdout UNCONDITIONALLY (even on a lease-loss exit, unlike
    its terminal joblog file, which is written only when this invocation
    held the lease) -- so `result["stdout"]` is the PRIMARY, always-present
    source callers should parse for `reason`/`error_detail`, never a
    driver-composed summary (see module docstring / Task 5 report).

    Raises DriverError if `communicate(timeout=wait_timeout)` itself
    expires -- codex_job.py has its own internal `--deadline-sec`/finalize
    budget and is expected to always terminate within it; `wait_timeout`
    here is a defense-in-depth backstop, not the mechanism that closes
    property 6. On backstop expiry the child (which is in its OWN session,
    so this cannot affect anything else) is SIGKILLed and reaped via a
    second `communicate()` before raising, so no zombie is left behind.
    """
    if not codex_job_script.is_file():
        fatal(f"codex_job.py not found at {codex_job_script}")
    argv = [sys.executable, str(codex_job_script), *job_args]
    popen_kwargs.setdefault("stdout", subprocess.PIPE)
    popen_kwargs.setdefault("stderr", subprocess.PIPE)
    popen_kwargs.setdefault("text", True)
    proc = subprocess.Popen(
        argv,
        start_new_session=True,
        stdin=subprocess.DEVNULL,
        **popen_kwargs,
    )
    try:
        stdout, stderr = proc.communicate(timeout=wait_timeout)
        return {"exit_code": proc.returncode, "stdout": stdout, "stderr": stderr}
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()  # reap -- never leave a zombie behind (kill() only signals)
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
# Phase 2 -- profile-derived substitution values mass-translate-wf.template.js
# itself needs to instantiate ({{TOKEN}} contract, see that file's own header
# comment) plus resume_setup.py's SUBST_FIELDS. Independent of
# load_engine_config() above (which stays untouched -- existing tests call it
# directly) -- duplicates that function's marker/profile-read preamble rather
# than refactoring it, so this addition cannot change what an already-tested
# function returns.
# ---------------------------------------------------------------------------


def load_translate_config(durable_root: Path) -> dict:
    """Every profile.yml field the per-segment loop needs beyond
    load_engine_config()'s two: engine.effort/model, source/target language
    codes, verse_policy (mode + threshold_lines), and the two fields
    resume_setup.py's SUBST_FIELDS requires that this driver has no other use
    for (research_mode, citation_content_types) -- present only so the
    resume-integrity payload below validates; their VALUES never influence
    dispatch decisions here, only the input_digest resume_setup.py computes
    from them."""
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
    if not isinstance(engine, dict):
        fatal(f"profile.yml at {profile_path} missing required field: engine", exit_code=2)
    for field in ("max_fix_rounds", "batch_agent_cap", "effort"):
        if field not in engine:
            fatal(f"profile.yml at {profile_path} missing required field: engine.{field}", exit_code=2)
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

    def _nested(*keys):
        node = profile
        for k in keys:
            if not isinstance(node, dict) or k not in node:
                fatal(
                    f"profile.yml at {profile_path} missing required field: {'.'.join(keys)}",
                    exit_code=2,
                )
            node = node[k]
        return node

    source_lang = _nested("source", "language", "code")
    target_lang = _nested("target", "language", "code")
    verse_policy = _nested("verse_policy")
    if not isinstance(verse_policy, dict) or "mode" not in verse_policy:
        fatal(f"profile.yml at {profile_path}: verse_policy.mode is required", exit_code=2)

    return {
        "max_fix_rounds": max_fix_rounds,
        "batch_agent_cap": engine["batch_agent_cap"],
        "max_codex_jobs_per_batch": max_codex_jobs_per_batch,
        "effort": engine["effort"],
        "model": engine.get("model") or "",
        "source_lang": source_lang,
        "target_lang": target_lang,
        "verse_policy": verse_policy,
        "research_mode": profile.get("research_mode", ""),
        "citation_content_types": profile.get("citation_content_types", []),
    }


# Transcribed VERBATIM from references/verse-policy.md's own "## The six
# modes" table ("Translator instruction" column) -- the one substitution
# value this driver does NOT obtain by executing template.js code, because no
# such code exists: SKILL.md's own Step 0b describes this as a lookup a
# Claude session performs by reading that table, not a script's output. This
# is a documented, deliberate exception to "never re-author a prompt", not an
# oversight -- see the module docstring / the Task 5 report for the residual
# risk it carries (an operator or future doc edit could drift this copy from
# the table without either side's own tests catching it).
_VERSE_POLICY_INSTRUCTIONS = {
    "full_rhymed_plus_literal": (
        "Every verse (long or short, incl. epigrams) gets a full rhymed "
        "literary rendering AND a mandatory literal gloss."
    ),
    "full_rhymed_only": "Full rhymed rendering; no forced literal safety-net copy.",
    "rhythmic_approximation": (
        "Meter/rhythm preserved but full end-rhyme not required -- a "
        "lighter-weight option for volume/cost-sensitive projects."
    ),
    "mixed_by_length": (
        "Verses at or over {threshold_lines} lines get full_rhymed_plus_literal; "
        "verses under it get rhythmic_approximation."
    ),
    "literal_only": (
        "No rhyme/meter attempt; a faithful prose gloss only -- for projects "
        "prioritizing informational accuracy over literary verse craft."
    ),
    "skip": (
        "Verses are left untranslated / passed through as-is (e.g. a project "
        "translating prose commentary only, quoting verse in the original), "
        "OR rendered with an explicit passthrough marker."
    ),
}


def verse_policy_instruction_block(verse_policy: dict) -> str:
    mode = verse_policy.get("mode")
    text = _VERSE_POLICY_INSTRUCTIONS.get(mode)
    if text is None:
        fatal(f"unknown verse_policy.mode {mode!r} -- not one of {sorted(_VERSE_POLICY_INSTRUCTIONS)}", exit_code=2)
    if mode == "mixed_by_length":
        threshold = verse_policy.get("threshold_lines")
        if not isinstance(threshold, int) or isinstance(threshold, bool):
            fatal("verse_policy.threshold_lines is required (and must be an integer) when mode is mixed_by_length", exit_code=2)
        text = text.format(threshold_lines=threshold)
    return text


# ---------------------------------------------------------------------------
# Phase 2 -- RUN_ID resolution via resume_setup.py's own resume-integrity
# gate. NEVER minted independently: a driver killed mid-batch and relaunched
# must get the IDENTICAL RUN_ID back on a matching digest, or every
# dispatch_token already on disk (draft/review artifacts written before the
# restart) is orphaned by a fresh id and silently redone. This is the
# resumability primitive this script reuses per the dispatch's explicit
# instruction -- never a parallel recovery mechanism of its own.
# ---------------------------------------------------------------------------


_RUN_ID_DIR_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


def _latest_resumable_run_id(runs_dir: Path) -> "str | None":
    """A candidate `resume_from_run_id` for resolve_run_id() below: the
    lexicographically-latest (== chronologically latest, since RUN_ID is the
    colon-free `YYYYMMDDTHHMMSSZ` form) subdirectory of `runs_dir` that
    LOOKS like a run id (matches resume_setup.py's own RUN_ID_RE) AND
    carries an `input.digest` file (the one marker that distinguishes a real
    prior run directory from `ledger.d`, `workflows/`, or any other
    non-run-id entry `runs/` also holds). Returns None if `runs_dir` does
    not exist or holds no such directory -- resolve_run_id() then omits
    `resume_from_run_id` entirely, exactly like a genuinely first-ever run.

    This is the ONLY thing that makes killing and relaunching this driver
    actually resumable: resume_setup.py's own resolve_run() NEVER resumes
    on its own initiative -- it only ever resumes a caller-SUPPLIED
    candidate whose digest matches (see that function's own docstring).
    Never invents a run_id itself, never writes anything -- a pure,
    read-only directory scan; resume_setup.py's digest comparison remains
    the ONLY authority on whether resuming this candidate is actually safe."""
    if not runs_dir.is_dir():
        return None
    candidates = [
        p.name for p in runs_dir.iterdir()
        if p.is_dir() and _RUN_ID_DIR_RE.fullmatch(p.name) and (p / "input.digest").is_file()
    ]
    if not candidates:
        return None
    return max(candidates)


def resolve_run_id(dirs: dict, *, cli_args: dict, segs: list, translate_cfg: dict,
                    plugin_root_str, durable_root_str) -> dict:
    """Builds the exact payload shape resume_setup.py's own module docstring
    documents (kind="mass", args, subst, plugin_root, segs), writes it to a
    scratch file, and invokes resume_setup.py --payload-file <path>
    [--durable-root ...] [--plugin-root ...]. Returns the parsed
    {"success", "effectiveRunId", "resume", "run_dir", "input_digest"}
    payload verbatim on success; raises DriverError (never a bare traceback)
    on any invocation failure or a `success: false` response.

    `resume_from_run_id` is populated from _latest_resumable_run_id() above,
    never left None -- omitting it would mean this driver can NEVER resume
    (resume_setup.py's own resolve_run() only ever resumes a caller-supplied
    candidate), silently defeating the whole "a driver killed mid-batch must
    be safely restartable" property on every single relaunch, not just an
    edge case."""
    script = dirs["resume_setup_script"]
    if not script.is_file():
        fatal(f"resume_setup.py not found at {script}", exit_code=2)

    payload = {
        "kind": "mass",
        "args": cli_args,
        "subst": {
            "research_mode": translate_cfg["research_mode"],
            "verse_policy": translate_cfg["verse_policy"],
            "source_lang": translate_cfg["source_lang"],
            "target_lang": translate_cfg["target_lang"],
            "max_fix_rounds": translate_cfg["max_fix_rounds"],
            "batch_agent_cap": translate_cfg["batch_agent_cap"],
            "max_codex_jobs_per_batch": translate_cfg["max_codex_jobs_per_batch"],
            "effort": translate_cfg["effort"],
            "citation_content_types": translate_cfg["citation_content_types"],
        },
        "plugin_root": plugin_root_str or "",
        "resume_from_run_id": _latest_resumable_run_id(dirs["runs_dir"]),
        "segs": segs,
    }

    with tempfile.TemporaryDirectory(prefix="ltdriver.resume.") as tmpdir:
        payload_path = Path(tmpdir) / "payload.json"
        payload_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        cmd = [sys.executable, str(script), "--payload-file", str(payload_path)]
        cmd += _root_forward_args(dirs, durable_root_str, plugin_root_str)
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        except (OSError, subprocess.TimeoutExpired) as exc:
            fatal(f"could not run resume_setup.py: {exc}", exit_code=2)

    try:
        result = json.loads(proc.stdout)
    except json.JSONDecodeError:
        fatal(
            "resume_setup.py did not print valid JSON on stdout "
            f"(exit {proc.returncode}): stdout={proc.stdout!r} stderr={proc.stderr!r}",
            exit_code=2,
        )
    if not isinstance(result, dict):
        fatal(f"resume_setup.py printed a non-object JSON value: {result!r}", exit_code=2)
    if not result.get("success"):
        fatal(f"resume_setup.py refused: {result.get('error')}", exit_code=1)
    return result


# ---------------------------------------------------------------------------
# Phase 2 -- resolve the codex-companion.mjs path, exactly like SKILL.md's
# own W5 instantiation step (1.4.7): resolve_codex_companion.py, never a
# durable-root copy, ABORT on any non-zero exit.
# ---------------------------------------------------------------------------


def resolve_companion_path(dirs: dict, *, durable_root_str, node_bin: str) -> str:
    script = dirs["resolve_codex_companion_script"]
    if not script.is_file():
        fatal(f"resolve_codex_companion.py not found at {script}", exit_code=2)
    cmd = [sys.executable, str(script), "--durable-root", str(dirs["durable_root"]), "--node", node_bin]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        fatal(f"could not run resolve_codex_companion.py: {exc}", exit_code=2)
    if proc.returncode != 0:
        fatal(
            f"resolve_codex_companion.py failed (exit {proc.returncode}): {proc.stderr.strip()}",
            exit_code=2,
        )
    try:
        result = json.loads(proc.stdout)
    except json.JSONDecodeError:
        fatal(f"resolve_codex_companion.py did not print valid JSON: stdout={proc.stdout!r}", exit_code=2)
    companion_path = result.get("companion_path") if isinstance(result, dict) else None
    if not isinstance(companion_path, str) or not companion_path:
        fatal(f"resolve_codex_companion.py printed no usable companion_path: {result!r}", exit_code=2)
    return companion_path


# ---------------------------------------------------------------------------
# Phase 2 -- the trust-critical piece: obtain codex prompt text and the
# codex_job.py dispatch argv by EXECUTING mass-translate-wf.template.js's own
# builder functions under Node, never by re-authoring them. See the module
# docstring's "Property 4 in detail" section for the identical reasoning
# applied to draft_content_sha1 -- this is the SAME class of defect avoided
# the SAME way: reuse the one authority, never a hand-maintained second copy.
#
# The template is a GENERATED-ONLY Workflow script, and NOT a plain ES
# module despite its `export const meta = ...` line: its top-level code also
# has top-level `await` AND top-level `return` statements (the
# batch_agent_cap / max_codex_jobs_per_batch preflights each end
# `if (...) return {...};` OUTSIDE any function, before the final
# `const results = await pipeline(SEGS, translateStage, reviewFixLoop);`
# call) -- legal only inside whatever function-body wrapper the real
# Workflow tool's own bespoke loader supplies at runtime, and a genuine
# `SyntaxError: Illegal return statement` under a plain Node `import()`
# otherwise (verified empirically while building this harness). This driver
# must never reach any of that tail regardless (it would either throw
# against stub globals or attempt real orchestration). So the harness:
#   1. Substitutes every {{TOKEN}} per the template's OWN documented
#      contract (its header comment, verified against the exact `const X =
#      ...;` lines below it -- see render_template_source()'s own comment
#      for the two substitution shapes).
#   2. Truncates the substituted source immediately before
#      `function draftProbePrompt(` -- the first declaration after
#      `fixPrompt` (the last function this driver calls) and, crucially,
#      before EITHER preflight's top-level return statement. Everything
#      above that point (every function/const declaration this driver
#      calls) is kept verbatim; only the batch-cap-preflights-then-
#      pipeline() epilogue this driver does not need, and cannot safely
#      parse as plain ESM, is dropped. The truncation point is found by
#      matching the literal statement text, not a line number, so a
#      template edit that moves this line is caught loudly (DriverError)
#      rather than silently mis-truncating.
#   3. Appends an `export { ... }` statement naming the pure builder
#      functions this driver calls -- the file only exports `meta` on its
#      own. This ADDS visibility, it does not touch a single byte of any
#      function BODY.
#   4. Runs the result under a fresh `node` subprocess with `args`/`log`/
#      `agent`/`pipeline` stubbed as harmless placeholders (agent()/
#      pipeline() are unreachable now that the tail is truncated away,
#      simply never invoked -- the stubs are defence in depth) and calls the
#      requested functions, printing their return values as one JSON object.
# ---------------------------------------------------------------------------

TEMPLATE_EXPORTED_FUNCTIONS = (
    "translatePrompt", "translateDrivePrompt",
    "reviewDispatchPrompt", "reviewDrivePrompt",
    "fixPrompt", "parseDisp",
)

# The template substitutes each token in one of three shapes -- see
# mass-translate-wf.template.js's own header comment (the block right above
# `export const meta = {`) for the authoritative spec this mirrors:
#   "quoted"     -- const X = "{{TOKEN}}";  -- the token sits INSIDE quotes
#                   the template already supplies; substitute the
#                   JSON-escaped, QUOTE-STRIPPED content only.
#   "json"       -- const X = {{TOKEN}};    -- the token supplies its OWN
#                   quotes; substitute the full json.dumps() output.
#   "int"        -- const X = {{TOKEN}};    -- a bare integer literal.
_TEMPLATE_TOKEN_STYLE = {
    "DURABLE_ROOT": "quoted",
    "RUN_ID": "quoted",
    "SOURCE_LANG": "quoted",
    "TARGET_LANG": "quoted",
    "EFFORT": "quoted",
    "MODEL": "quoted",
    "VERSE_POLICY_INSTRUCTION_BLOCK": "quoted",
    "MAX_FIX_ROUNDS": "int",
    "BATCH_AGENT_CAP": "int",
    "MAX_CODEX_JOBS_PER_BATCH": "int",
    "CODEX_COMPANION_PATH_JSON": "json",
    "PLUGIN_ROOT": "json",
}

_TRUNCATE_BEFORE_MARKER = "function draftProbePrompt("


def render_template_source(template_text: str, subst: dict) -> str:
    """`subst` keys: durable_root, run_id, source_lang, target_lang,
    max_fix_rounds, batch_agent_cap, max_codex_jobs_per_batch, effort, model,
    verse_policy_instruction_block, companion_path, plugin_root (str, "" if
    this dispatch does not opt into the #412 redirect). Returns the
    substituted source text -- the FULL file, still ending in its own
    pipeline() call; truncation happens separately in
    template_harness_source() below."""
    values = {
        "DURABLE_ROOT": subst["durable_root"],
        "RUN_ID": subst["run_id"],
        "SOURCE_LANG": subst["source_lang"],
        "TARGET_LANG": subst["target_lang"],
        "EFFORT": subst["effort"],
        "MODEL": subst["model"],
        "VERSE_POLICY_INSTRUCTION_BLOCK": subst["verse_policy_instruction_block"],
        "MAX_FIX_ROUNDS": subst["max_fix_rounds"],
        "BATCH_AGENT_CAP": subst["batch_agent_cap"],
        "MAX_CODEX_JOBS_PER_BATCH": subst["max_codex_jobs_per_batch"],
        "CODEX_COMPANION_PATH_JSON": subst["companion_path"],
        "PLUGIN_ROOT": subst["plugin_root"],
    }
    text = template_text
    for name, style in _TEMPLATE_TOKEN_STYLE.items():
        token = "{{%s}}" % name
        value = values[name]
        if style == "quoted":
            replacement = json.dumps(str(value))[1:-1]
        elif style == "int":
            replacement = str(int(value))
        elif style == "json":
            replacement = json.dumps(str(value))
        else:  # pragma: no cover -- defensive, every style above is exhaustive
            fatal(f"internal error: unknown template token style {style!r} for {name}", exit_code=2)
        text = text.replace(token, replacement)
    if "{{" in text and "}}" in text:
        fatal(
            "template substitution left an unresolved {{TOKEN}} in "
            "mass-translate-wf.template.js -- a new token was added to the "
            "template that this driver's _TEMPLATE_TOKEN_STYLE table does "
            "not know about yet.",
            exit_code=2,
        )
    return text


def template_harness_source(template_text: str, subst: dict) -> str:
    """render_template_source() plus the truncate-then-export step described
    in this section's own module-level comment above.

    Truncates immediately BEFORE `function draftProbePrompt(` -- the first
    declaration after `fixPrompt` (the last function this driver calls) --
    rather than before the `pipeline()` call further down. This is not an
    arbitrary earlier cut: the file's batch_agent_cap preflight block (well
    before the max_codex_jobs_per_batch one, and well before pipeline()
    itself) contains a top-level `if (...) return {...};` -- legal ONLY
    inside whatever function-body wrapper the real Workflow tool's own
    bespoke loader supplies at runtime (this file is neither a plain ES
    module nor plain CommonJS: it mixes top-level `export`, top-level
    `await`, AND top-level `return` -- verified empirically: a plain
    `import()` of a copy truncated only before pipeline() throws
    `SyntaxError: Illegal return statement` from the still-present
    batch_agent_cap block). Truncating before ANY such top-level
    control-flow statement -- not just before pipeline() itself -- is what
    makes the remaining prefix valid standalone ESM; every function
    declaration and `const`/schema literal this driver needs sits safely
    before that point regardless.

    Raises DriverError if the marker is not found -- the template's shape
    changed and this harness's truncation point must be re-derived before
    it can be trusted, never silently mis-truncated."""
    substituted = render_template_source(template_text, subst)
    idx = substituted.find(_TRUNCATE_BEFORE_MARKER)
    if idx == -1:
        fatal(
            "could not find the truncation marker "
            f"{_TRUNCATE_BEFORE_MARKER!r} in mass-translate-wf.template.js -- "
            "its shape has changed; re-derive this driver's truncation point "
            "before trusting harness output.",
            exit_code=2,
        )
    truncated = substituted[:idx]
    exports = "\nexport { %s };\n" % ", ".join(TEMPLATE_EXPORTED_FUNCTIONS)
    return truncated + exports


def call_template_functions(dirs: dict, subst: dict, calls: list, node_bin: str = "node") -> dict:
    """Runs `node` against a freshly instantiated, truncated copy of the
    REAL mass-translate-wf.template.js (dirs["template_script"]) and calls
    each requested function.

    `calls`: list of {"key": <result dict key>, "fn": <one of
    TEMPLATE_EXPORTED_FUNCTIONS>, "args": [JSON-serializable positional
    args]}. Returns {key: <that call's return value>}.

    Spawns exactly ONE node subprocess for the whole batch of calls, on a
    freshly written temp copy -- never a hand-copied excerpt of the
    template's text. `args`/`log`/`agent`/`pipeline` are stubbed as
    harmless globals before the dynamic import (agent()/pipeline() throw if
    ever reached; they must not be, since the pipeline()-invoking tail is
    truncated away before this source is written)."""
    template_path = dirs["template_script"]
    if not template_path.is_file():
        fatal(f"mass-translate-wf.template.js not found at {template_path}", exit_code=2)
    template_text = template_path.read_text(encoding="utf-8")
    harness_source = template_harness_source(template_text, subst)

    for c in calls:
        if c["fn"] not in TEMPLATE_EXPORTED_FUNCTIONS:
            fatal(f"internal error: {c['fn']!r} is not one of TEMPLATE_EXPORTED_FUNCTIONS", exit_code=2)

    with tempfile.TemporaryDirectory(prefix="ltdriver.tmpl.") as tmpdir:
        tmpl_path = Path(tmpdir) / "instantiated.mjs"
        tmpl_path.write_text(harness_source, encoding="utf-8")
        runner_path = Path(tmpdir) / "runner.mjs"
        runner_src = (
            "globalThis.args = \"[]\";\n"
            "globalThis.log = () => {};\n"
            "globalThis.agent = async () => { throw new Error(\"harness: agent() must never be called\"); };\n"
            "globalThis.pipeline = async () => { throw new Error(\"harness: pipeline() must never be called\"); };\n"
            "const mod = await import(" + json.dumps(tmpl_path.as_uri()) + ");\n"
            "const calls = " + json.dumps(calls, ensure_ascii=False) + ";\n"
            "const out = {};\n"
            "for (const c of calls) { out[c.key] = mod[c.fn](...c.args); }\n"
            "process.stdout.write(JSON.stringify(out));\n"
        )
        runner_path.write_text(runner_src, encoding="utf-8")
        try:
            proc = subprocess.run(
                [node_bin, str(runner_path)], capture_output=True, text=True, timeout=60,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            fatal(f"could not run node against the instantiated template: {exc}", exit_code=2)
        if proc.returncode != 0:
            fatal(
                "node failed while executing mass-translate-wf.template.js's own "
                f"builder functions (exit {proc.returncode}): {proc.stderr.strip()}",
                exit_code=2,
            )
        try:
            return json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            fatal(
                f"node did not print valid JSON: {exc}; stdout={proc.stdout!r} stderr={proc.stderr!r}",
                exit_code=2,
            )


# ---------------------------------------------------------------------------
# Phase 2 -- codex_job.py argv construction, task-file writing, dispatch
# tokens. Mirrors translateDrivePrompt's/reviewDrivePrompt's own FIELD
# VALUES exactly (never their shell text -- this driver Popens codex_job.py
# directly with no shell/nohup/heredoc anywhere).
# ---------------------------------------------------------------------------

# Mirrors mass-translate-wf.template.js's own CODEX_DEADLINE_SEC /
# CODEX_FINALIZE_BUDGET_SEC / CODEX_WAIT_GRACE_SEC constants (which
# themselves mirror codex_job.py's own timing constants) -- see that
# template's own comment for the derivation. This driver never chunks a
# wait (property 6: it holds the child directly), but WAIT_BOUND_SEC is
# still the right backstop for dispatch_codex_job()'s own wait_timeout: it
# is codex_job.py's documented worst-case own lifetime plus a grace margin,
# exactly what the backstop needs to never fire under normal operation.
CODEX_DEADLINE_SEC = 2700
CODEX_FINALIZE_BUDGET_SEC = 150
CODEX_WAIT_GRACE_SEC = 600
CODEX_JOB_WAIT_TIMEOUT_SEC = CODEX_DEADLINE_SEC + CODEX_FINALIZE_BUDGET_SEC + CODEX_WAIT_GRACE_SEC


def fresh_disp() -> str:
    """A disp nonce matching codex_job.py's own _DISP_RE
    ([0-9A-Za-z][0-9A-Za-z._-]{0,127}). This driver never shells it through
    bash the way translateDrivePrompt's heredoc-based dispatcher does (no
    uuidgen/$RANDOM fallback needed either), so a plain uuid4 hex string is
    both sufficient and simpler."""
    return uuid.uuid4().hex


def translate_dispatch_token(run_id: str, seg: str) -> str:
    return f"{run_id}:{seg}"


def review_dispatch_token(run_id: str, seg: str, round_label: str) -> str:
    return f"{run_id}:{seg}:r{round_label}"


def task_file_path(durable_root: Path, kind: str, seg: str, disp: str) -> Path:
    """Mirrors translateDrivePrompt's/reviewDrivePrompt's own TASKFILE
    naming: `segments/.codex_task.<kind>.<seg>.<DISP>` -- kind spelled
    "translate"/"review", matching the template's own taskFile prefix
    exactly (not codex_job.py's own draft/review extension spelling)."""
    return durable_root / "segments" / f".codex_task.{kind}.{seg}.{disp}"


def build_codex_job_argv(*, kind: str, seg: str, companion_path: str, durable_root: Path,
                          prompt_file: Path, expect_token: str, disp: str, deadline_sec: int,
                          effort: str, model: str, plugin_root_str, node_bin: str = "node") -> list:
    """The exact codex_job.py argv this driver Popens, built from the SAME
    field values translateDrivePrompt/reviewDrivePrompt splice into their
    own nohup shell command (--kind/--companion/--cwd/--seg/--prompt-file/
    --expect-token/--disp/--deadline-sec/--effort/[--model]/[--plugin-root]).
    tests/segment_dispatch_driver.test.py's equivalence test shlex-splits
    the template's OWN shell string (obtained by executing
    translateDrivePrompt/reviewDrivePrompt under Node) and asserts this
    function's output matches it field for field. `node_bin` is NOT part of
    that equivalence surface (the template always spells the codex_job.py
    launch with a bare `node`, resolved via the launching shell's own PATH,
    same as this driver's own --node default) -- it is appended only when
    the caller passed something other than codex_job.py's own "node"
    default, so the equivalence test's default-args comparison still
    matches byte for byte."""
    argv = [
        "--kind", kind,
        "--companion", companion_path,
        "--cwd", str(durable_root),
        "--seg", seg,
        "--prompt-file", str(prompt_file),
        "--expect-token", expect_token,
        "--disp", disp,
        "--deadline-sec", str(deadline_sec),
        "--effort", effort,
    ]
    if model:
        argv += ["--model", model]
    if plugin_root_str:
        argv += ["--plugin-root", plugin_root_str]
    if node_bin and node_bin != "node":
        argv += ["--node", node_bin]
    return argv


# ---------------------------------------------------------------------------
# Phase 2 -- direct (non-agent) ledger writes. Mirrors
# recordLedgerPrompt's own payload SHAPE exactly (status/reason/rounds/note,
# optionally cache_key + run_token), driven straight through ledger_update.py
# (and, for a convergence write, cache_key.py) with no agent() indirection at
# all -- this bookkeeping is fully mechanical, so removing the LLM turn the
# Workflow only ever needed because agent() was its one way to run a shell
# command is exactly where #409's token saving comes from.
# ---------------------------------------------------------------------------


def write_ledger(dirs: dict, seg: str, fields: dict, *, run_id=None, needs_cache_key=False,
                  durable_root_str=None, plugin_root_str=None) -> dict:
    """fields: dict with status (required) plus optionally reason/rounds/
    note. needs_cache_key=True mirrors recordLedgerPrompt's own
    needsCacheKey flag: runs cache_key.py --seg <seg> first and folds its
    JSON object into the payload's cache_key field, plus run_token=run_id.
    Returns ledger_update.py's own parsed JSON result verbatim (the
    ledger-write-confirmation.schema.json shape) or a synthesized
    {"success": False, "error": ...} on an invocation failure this function
    catches itself rather than raising -- a ledger-write failure is a
    per-segment outcome, never a driver-fatal one."""
    payload = dict(fields)
    if needs_cache_key:
        cache_key_script = dirs["cache_key_script"]
        if not cache_key_script.is_file():
            return {"success": False, "error": f"cache_key.py not found at {cache_key_script}"}
        cmd = [sys.executable, str(cache_key_script), "--seg", seg]
        cmd += _root_forward_args(dirs, durable_root_str, plugin_root_str, supports_plugin_root=False)
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {"success": False, "error": f"could not run cache_key.py: {exc}"}
        if proc.returncode != 0:
            return {"success": False, "error": f"cache_key.py failed (exit {proc.returncode}): {proc.stderr.strip()}"}
        try:
            cache_key_obj = json.loads(proc.stdout)
        except json.JSONDecodeError:
            return {"success": False, "error": f"cache_key.py did not print valid JSON: {proc.stdout!r}"}
        payload["cache_key"] = cache_key_obj
        payload["run_token"] = run_id

    ledger_update_script = dirs["ledger_update_script"]
    if not ledger_update_script.is_file():
        return {"success": False, "error": f"ledger_update.py not found at {ledger_update_script}"}
    with tempfile.TemporaryDirectory(prefix="ltdriver.ledger.") as tmpdir:
        payload_path = Path(tmpdir) / "payload.json"
        payload_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        cmd = [sys.executable, str(ledger_update_script), seg, "--payload-file", str(payload_path)]
        cmd += _root_forward_args(dirs, durable_root_str, plugin_root_str, supports_plugin_root=False)
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {"success": False, "error": f"could not run ledger_update.py: {exc}"}

    try:
        result = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"success": False, "error": f"ledger_update.py did not print valid JSON: {proc.stdout!r}"}
    if not isinstance(result, dict):
        return {"success": False, "error": f"ledger_update.py printed a non-object JSON value: {result!r}"}
    return result


# ---------------------------------------------------------------------------
# Phase 2 -- the resumable per-segment state derivation. Reads ONLY existing,
# durable on-disk state -- the canonical draft/review artifacts via the SAME
# gate scripts codex_job.py and the Workflow's own wait-poll already use, and
# the current draft's sha1 via current_draft_sha1() (Property 4's import) --
# to decide the ONE next action for a segment, with no state of its own. A
# driver killed and relaunched re-derives the identical answer from the
# identical durable facts on its next invocation -- this IS the resumability
# story the dispatch asked for, built from the existing primitives it named
# (codex_job.py's own per-segment lease covers the mid-codex-dispatch case;
# this covers "what should the NEXT driver invocation do for this segment"),
# never a parallel recovery mechanism of its own.
# ---------------------------------------------------------------------------


class DispatchContext:
    """Bundles the per-run() state every Phase 2 helper needs, so their own
    signatures stay short. Built once per run() invocation; read-only after
    construction (safe to share across ThreadPoolExecutor workers)."""

    def __init__(self, *, dirs, run_id, translate_cfg, companion_path,
                 durable_root_str, plugin_root_str, node_bin, session_id):
        self.dirs = dirs
        self.run_id = run_id
        self.translate_cfg = translate_cfg
        self.companion_path = companion_path
        self.durable_root_str = durable_root_str
        self.plugin_root_str = plugin_root_str
        self.node_bin = node_bin
        self.session_id = session_id


def _run_gate(script: Path, argv_rest: list, ctx: "DispatchContext", *, supports_plugin_root: bool) -> bool:
    """True iff the gate script exits 0 -- a genuine not-ready (non-zero
    exit) is never an error here, only a script that could not be invoked
    at all is (a driver-level fatal, matching every other subprocess
    invocation in this file)."""
    if not script.is_file():
        fatal(f"{script.name} not found at {script}", exit_code=2)
    cmd = [sys.executable, str(script)] + argv_rest
    cmd += _root_forward_args(ctx.dirs, ctx.durable_root_str, ctx.plugin_root_str,
                               supports_plugin_root=supports_plugin_root)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as exc:
        fatal(f"could not run {script.name}: {exc}", exit_code=2)
    return proc.returncode == 0


def derive_next_action(seg: str, ctx: "DispatchContext") -> dict:
    """Returns exactly one of:
      {"action": "translate"}
      {"action": "review", "round_label": "1".."<max_fix_rounds>"|"final"}
      {"action": "needs_fix", "round_label": ..., "findings": [...]}
      {"action": "cap_reached", "findings": [...]}
      {"action": "already_converged"}
    """
    dirs = ctx.dirs
    durable_root = dirs["durable_root"]
    segments_dir = durable_root / "segments"
    run_id = ctx.run_id
    max_fix_rounds = ctx.translate_cfg["max_fix_rounds"]

    draft_ok = (
        _run_gate(dirs["draft_ready_script"],
                  [seg, "--expect-token", translate_dispatch_token(run_id, seg)],
                  ctx, supports_plugin_root=False)
        and _run_gate(dirs["validate_draft_script"], [seg], ctx, supports_plugin_root=False)
    )
    if not draft_ok:
        return {"action": "translate"}

    review_path = segments_dir / f"{seg}.review.json"
    if not review_path.is_file():
        return {"action": "review", "round_label": "1"}
    try:
        review_obj = json.loads(review_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"action": "review", "round_label": "1"}
    if not isinstance(review_obj, dict):
        return {"action": "review", "round_label": "1"}

    token = review_obj.get("dispatch_token")
    matched_round_label = None
    for n in range(1, max_fix_rounds + 2):  # rounds 1..max_fix_rounds, then the mandatory "final"
        label = "final" if n == max_fix_rounds + 1 else str(n)
        if token == review_dispatch_token(run_id, seg, label):
            matched_round_label = label
            break
    if matched_round_label is None:
        # Absent, malformed, or belonging to a different run/round shape --
        # treated exactly like "no review yet" (safe degradation, matches
        # select_segments.py's own "unrecognized -> recoverable" default).
        return {"action": "review", "round_label": "1"}

    clean = review_obj.get("clean") is True
    coverage_ok = review_obj.get("coverage_ok") is True
    if clean and coverage_ok:
        return {"action": "already_converged"}

    if matched_round_label == "final":
        return {"action": "cap_reached", "findings": review_obj.get("findings") or []}

    # Not clean, not the mandatory final round -- a fix is needed before the
    # NEXT review round can be dispatched. Distinguish "still awaiting that
    # fix" from "fix already applied" the same primitive-reuse way
    # review_ready.py/draft_sha1.py already tie review<->draft together:
    # compare the CURRENT draft's content sha1 against what THIS review
    # recorded at review time. Any ambiguity (can't compute either sha1)
    # stays conservative -- report needs_fix rather than silently advancing.
    reviewed_sha1 = review_obj.get("draft_sha1")
    try:
        current_sha1 = current_draft_sha1(seg, segments_dir, dirs["scripts_dir"])
    except DriverError:
        current_sha1 = None
    if current_sha1 is None or reviewed_sha1 is None or current_sha1 == reviewed_sha1:
        return {"action": "needs_fix", "round_label": matched_round_label, "findings": review_obj.get("findings") or []}

    next_round = int(matched_round_label) + 1
    next_label = "final" if next_round == max_fix_rounds + 1 else str(next_round)
    return {"action": "review", "round_label": next_label}


# ---------------------------------------------------------------------------
# Phase 2 -- prompt text, ALWAYS by executing the real template's own
# builders (call_template_functions() above), never a second copy.
# ---------------------------------------------------------------------------


def _template_subst(ctx: "DispatchContext") -> dict:
    cfg = ctx.translate_cfg
    return {
        "durable_root": str(ctx.dirs["durable_root"]),
        "run_id": ctx.run_id,
        "source_lang": cfg["source_lang"],
        "target_lang": cfg["target_lang"],
        "max_fix_rounds": cfg["max_fix_rounds"],
        "batch_agent_cap": cfg["batch_agent_cap"],
        "max_codex_jobs_per_batch": cfg["max_codex_jobs_per_batch"],
        "effort": cfg["effort"],
        "model": cfg["model"],
        "verse_policy_instruction_block": verse_policy_instruction_block(cfg["verse_policy"]),
        "companion_path": ctx.companion_path,
        "plugin_root": ctx.plugin_root_str or "",
    }


def render_translate_prompt(ctx: "DispatchContext", seg: str) -> str:
    out = call_template_functions(
        ctx.dirs, _template_subst(ctx),
        [{"key": "text", "fn": "translatePrompt", "args": [seg]}],
        node_bin=ctx.node_bin,
    )
    return out["text"]


def render_review_prompt(ctx: "DispatchContext", seg: str, round_label: str) -> str:
    out = call_template_functions(
        ctx.dirs, _template_subst(ctx),
        [{"key": "text", "fn": "reviewDispatchPrompt", "args": [seg, round_label]}],
        node_bin=ctx.node_bin,
    )
    return out["text"]


def render_fix_prompt(ctx: "DispatchContext", seg: str, round_num: int, review_obj: dict) -> str:
    """fixPrompt(seg, round, revObj) -- only ever called by the template for
    a NUMERIC round (never "final"; runRound never dispatches a fix on the
    mandatory final round). Provided so this driver's "needs_fix" handoff
    (see module docstring) can surface the EXACT fix prompt text to whatever
    performs the fix turn, sourced the same byte-identical way as the
    translate/review prompts -- never a second hand-written copy for this
    one either."""
    out = call_template_functions(
        ctx.dirs, _template_subst(ctx),
        [{"key": "text", "fn": "fixPrompt", "args": [seg, round_num, review_obj]}],
        node_bin=ctx.node_bin,
    )
    return out["text"]


# ---------------------------------------------------------------------------
# Phase 2 -- per-segment codex dispatch: builds the prompt/argv, launches
# codex_job.py (Property 2/6's dispatch_codex_job), and reads the outcome
# from codex_job.py's OWN stdout line -- never a driver-composed summary.
# ---------------------------------------------------------------------------


def _codex_job_outcome(dispatch_result: dict) -> dict:
    """Parses codex_job.py's own one-line stdout JSON (written
    unconditionally by finalize(), see dispatch_codex_job()'s own
    docstring) into {"ok": bool, "reason": str|None, "error_detail":
    str|None, "job_status": ..., "adopted": ...}. Falls back to a
    driver-attributed reason ONLY when codex_job.py produced no parseable
    stdout at all (a genuine invocation-level anomaly, e.g. it crashed
    before its own finally: block ran) -- never overrides a reason
    codex_job.py itself reported."""
    stdout = dispatch_result.get("stdout")
    if stdout:
        try:
            line = json.loads(stdout.strip().splitlines()[-1]) if stdout.strip() else None
        except (json.JSONDecodeError, IndexError):
            line = None
        if isinstance(line, dict) and "ok" in line:
            return {
                "ok": bool(line.get("ok")),
                "reason": line.get("reason"),
                "error_detail": line.get("error_detail"),
                "job_status": line.get("job_status"),
                "adopted": line.get("adopted"),
            }
    return {
        "ok": False,
        "reason": "driver-no-parseable-stdout",
        "error_detail": (dispatch_result.get("stderr") or "").strip() or None,
        "job_status": None,
        "adopted": None,
    }


def run_one_codex_job(ctx: "DispatchContext", *, kind: str, seg: str, round_label=None) -> dict:
    """Dispatches ONE codex_job.py invocation for `seg` (translate, or one
    review round) and returns codex_job.py's OWN reported outcome (see
    _codex_job_outcome()) plus the {kind, seg, round_label, disp} this
    dispatch used. Writes the task-file, builds the argv via
    build_codex_job_argv(), and blocks via dispatch_codex_job() -- every
    property (start_new_session, no polling) that primitive already closes."""
    dirs = ctx.dirs
    durable_root = dirs["durable_root"]
    if kind == "translate":
        prompt_text = render_translate_prompt(ctx, seg)
        expect_token = translate_dispatch_token(ctx.run_id, seg)
    else:
        prompt_text = render_review_prompt(ctx, seg, round_label)
        expect_token = review_dispatch_token(ctx.run_id, seg, round_label)

    disp = fresh_disp()
    task_file = task_file_path(durable_root, kind, seg, disp)
    task_file.parent.mkdir(parents=True, exist_ok=True)
    task_file.write_text(prompt_text, encoding="utf-8")

    argv = build_codex_job_argv(
        kind=kind, seg=seg, companion_path=ctx.companion_path, durable_root=durable_root,
        prompt_file=task_file, expect_token=expect_token, disp=disp,
        deadline_sec=CODEX_DEADLINE_SEC, effort=ctx.translate_cfg["effort"],
        model=ctx.translate_cfg["model"], plugin_root_str=ctx.plugin_root_str,
        node_bin=ctx.node_bin,
    )
    append_journal(durable_root, ctx.session_id, {
        "type": "codex_dispatch_started", "seg": seg, "kind": kind,
        "round_label": round_label, "disp": disp,
    })
    dispatch_result = dispatch_codex_job(
        dirs["codex_job_script"], argv, wait_timeout=CODEX_JOB_WAIT_TIMEOUT_SEC,
    )
    outcome = _codex_job_outcome(dispatch_result)
    append_journal(durable_root, ctx.session_id, {
        "type": "codex_dispatch_finished", "seg": seg, "kind": kind,
        "round_label": round_label, "disp": disp, **outcome,
    })
    return {"kind": kind, "seg": seg, "round_label": round_label, "disp": disp, **outcome}


def _read_review_obj(ctx: "DispatchContext", seg: str, fallback_findings=None) -> dict:
    review_path = ctx.dirs["durable_root"] / "segments" / f"{seg}.review.json"
    try:
        obj = json.loads(review_path.read_text(encoding="utf-8"))
        if isinstance(obj, dict):
            return obj
    except (OSError, json.JSONDecodeError):
        pass
    return {"findings": fallback_findings}


def process_segment(seg: str, ctx: "DispatchContext") -> dict:
    """The unit of work ONE ThreadPoolExecutor worker performs for ONE
    segment on ONE run() invocation: "dispatch translate, wait, then the
    review/fix rounds" -- a real LOOP over this segment's rounds within
    this single call, not one dispatch per invocation. Each iteration
    re-derives the segment's next action from durable on-disk state
    (derive_next_action(), never trusting an in-memory assumption about
    what the last dispatch produced) and either performs exactly one codex
    dispatch and loops again, or reaches a genuine terminal/handoff state
    and returns:

      converged=True                      -- ledger recorded, done.
      converged=False, reason="cap"       -- mandatory final review still
                                              not clean; ledger recorded
                                              directly (fully mechanical,
                                              no fix dispatched on the
                                              final round -- matches
                                              runRound's own isFinal branch).
      converged=False, reason="needs_fix" -- STOPS here: applying findings
                                              to the draft is a real LLM
                                              content-editing turn this
                                              driver cannot perform (see
                                              module docstring). Carries
                                              round_label/findings/fix_prompt
                                              (rendered the same executed-
                                              template way as every other
                                              prompt) for whatever performs
                                              that one fix turn, which then
                                              re-invokes this driver.
      converged=False, stage=...          -- a codex_job.py dispatch itself
                                              failed; `reason`/`error_detail`
                                              are codex_job.py's OWN reported
                                              values, verbatim (#398) -- NO
                                              terminal ledger write, so the
                                              in_progress fragment already on
                                              disk stays the durable record
                                              and select_segments.py's
                                              "recoverable" default retries
                                              this segment next invocation.

    The iteration cap (codex_jobs_per_segment(max_fix_rounds) -- one
    translate plus every review round this segment could ever legitimately
    need) is a defensive bound against a derive_next_action() logic bug
    looping forever; it is never expected to bind in correct operation.
    """
    max_iterations = codex_jobs_per_segment(ctx.translate_cfg["max_fix_rounds"])
    for _ in range(max_iterations):
        action = derive_next_action(seg, ctx)

        if action["action"] == "already_converged":
            # A review already landed clean+coverage_ok but the convergence
            # ledger write may not have (a prior driver could have died
            # between the two) -- record it now, mechanically.
            review_obj = _read_review_obj(ctx, seg)
            rec = write_ledger(
                ctx.dirs, seg, {"status": "converged", "rounds": _round_number(review_obj.get("dispatch_token"))},
                run_id=ctx.run_id, needs_cache_key=True,
                durable_root_str=ctx.durable_root_str, plugin_root_str=ctx.plugin_root_str,
            )
            if not rec.get("success"):
                return {"seg": seg, "converged": False, "reason": "ledger-write-failed", "detail": rec.get("error")}
            return {"seg": seg, "converged": True}

        if action["action"] == "cap_reached":
            rec = write_ledger(
                ctx.dirs, seg, {"status": "non_converged", "reason": "cap"},
                durable_root_str=ctx.durable_root_str, plugin_root_str=ctx.plugin_root_str,
            )
            if not rec.get("success"):
                return {"seg": seg, "converged": False, "reason": "ledger-write-failed", "detail": rec.get("error")}
            return {"seg": seg, "converged": False, "reason": "cap", "lastFindings": action.get("findings")}

        if action["action"] == "needs_fix":
            round_label = action["round_label"]
            review_obj = _read_review_obj(ctx, seg, fallback_findings=action.get("findings"))
            fix_prompt = render_fix_prompt(ctx, seg, int(round_label), review_obj)
            return {
                "seg": seg, "converged": False, "reason": "needs_fix", "round_label": round_label,
                "findings": action.get("findings"), "fix_prompt": fix_prompt,
            }

        if action["action"] == "translate":
            rec = write_ledger(
                ctx.dirs, seg, {"status": "in_progress"},
                durable_root_str=ctx.durable_root_str, plugin_root_str=ctx.plugin_root_str,
            )
            if not rec.get("success"):
                return {"seg": seg, "converged": False, "reason": "ledger-write-failed", "detail": rec.get("error")}
            result = run_one_codex_job(ctx, kind="translate", seg=seg)
            if not result["ok"]:
                return {"seg": seg, "converged": False, "stage": "translate",
                         "reason": result["reason"], "error_detail": result["error_detail"]}
            continue  # re-derive: should now see "review round 1"

        if action["action"] == "review":
            round_label = action["round_label"]
            result = run_one_codex_job(ctx, kind="review", seg=seg, round_label=round_label)
            if not result["ok"]:
                return {"seg": seg, "converged": False, "stage": "review", "round_label": round_label,
                         "reason": result["reason"], "error_detail": result["error_detail"]}
            continue  # re-derive from the freshly promoted canonical review

        return {"seg": seg, "converged": None, "reason": f"unknown-action:{action['action']}"}  # pragma: no cover

    return {  # pragma: no cover -- defensive only, see docstring
        "seg": seg, "converged": None, "reason": "loop-exhausted-without-terminal-state",
    }


def _round_number(dispatch_token) -> "int | None":
    """Best-effort extraction of the trailing round digit from a review
    dispatch_token (RUN_ID:seg:rN form) for the ledger's own `rounds`
    field -- purely cosmetic (ledger_update.py does not require it), never
    load-bearing for any gate."""
    if not isinstance(dispatch_token, str):
        return None
    tail = dispatch_token.rsplit(":r", 1)
    if len(tail) != 2 or not tail[1].isdigit():
        return None
    return int(tail[1])


# ---------------------------------------------------------------------------
# Phase 2 -- the concurrency-bounded per-segment loop. See module docstring's
# "Concurrency" section for the knob choice and its justification.
# ---------------------------------------------------------------------------


def run_segment_loop(segs: list, ctx: "DispatchContext", max_concurrent_codex_jobs: int) -> list:
    """Runs process_segment() for every seg in `segs`, bounded to at most
    `max_concurrent_codex_jobs` concurrently in-flight codex_job.py
    dispatches. Each worker thread blocks on ONE dispatch_codex_job() call
    at a time (a real OS-level subprocess, not a coroutine) -- Python
    threads are the right tool here because the work is I/O-bound
    (Popen/communicate release the GIL while codex_job.py runs), and this
    driver needs REAL concurrent codex processes, not just concurrent
    Python bookkeeping. Returns per-segment result dicts in the SAME order
    as `segs` (not completion order), so a caller's own reporting stays
    stable across runs regardless of which segment happened to finish
    first."""
    if not segs:
        return []
    with ThreadPoolExecutor(max_workers=max(1, max_concurrent_codex_jobs)) as pool:
        results = list(pool.map(process_segment, segs, [ctx] * len(segs)))
    return results


# ---------------------------------------------------------------------------
# CLI / orchestration
# ---------------------------------------------------------------------------


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "#409 local driver for W5 mass-translate -- see this file's own "
            "module docstring for the safety properties this release closes "
            "and what it deliberately does not implement (the fix step)."
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
            "to resolve every sibling script this script shells out to/"
            "Popens (select_segments.py, codex_job.py, resume_setup.py, "
            "resolve_codex_companion.py, ledger_update.py, cache_key.py, "
            "draft_ready.py, validate_draft.py, review_ready.py) and the "
            "mass-translate-wf.template.js template it reads for codex "
            "prompt text, as {PATH}/assets/scripts/<name>.py / "
            "{PATH}/assets/templates/<name>.js -- deliberately NEVER derived "
            "from --durable-root. Optional; omit for today's self-anchored "
            "sibling lookup. See this file's own module docstring for the "
            "full rationale (this flag is a deliberate addition beyond the "
            "8 named safety properties)."
        ),
    )
    parser.add_argument(
        "--max-concurrent-codex-jobs",
        type=int,
        default=40,
        metavar="N",
        help=(
            "Upper bound on codex_job.py dispatches in flight at once. "
            "Default 40 -- the measured historical peak codex-job "
            "concurrency reached under the OLD Workflow-agent dispatch path "
            "(where it was an emergent side effect of agent-pool sizing, "
            "never a governed limit): 'pipeline() capped at 8 while codex "
            "job concurrency peaked at 40, producing 38.4 hours of queueing "
            "inside a 2.1-hour run.' This flag makes that same ceiling an "
            "explicit, overridable knob instead of an accident. See this "
            "file's own module docstring's 'Concurrency' section."
        ),
    )
    parser.add_argument(
        "--node",
        default="node",
        metavar="BIN",
        help="Node binary to invoke for both codex_job.py's own launches and this driver's template-execution harness. Default 'node' (resolved via PATH).",
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

        if not segs:
            result = {
                "success": True, "session_id": session_id, "durable_root": str(durable_root),
                "segs": segs, "counts": select_result.get("counts"), "engine": engine_cfg,
                "dispatched": False, "results": [],
                "note": "nothing to dispatch (SEGS is empty).",
            }
            append_journal(durable_root, session_id, {"type": "driver_exit", "success": True})
            return result

        translate_cfg = load_translate_config(durable_root)
        run_result = resolve_run_id(
            dirs,
            cli_args={
                "only_segs": args.only_segs,
                "allow_retranslate_converged": args.allow_retranslate_converged,
                "allow_empty": args.allow_empty,
            },
            segs=segs, translate_cfg=translate_cfg,
            plugin_root_str=args.plugin_root, durable_root_str=args.durable_root,
        )
        run_id = run_result["effectiveRunId"]
        append_journal(
            durable_root, session_id,
            {"type": "run_id_resolved", "run_id": run_id, "resume": run_result.get("resume")},
        )

        companion_path = resolve_companion_path(
            dirs, durable_root_str=args.durable_root, node_bin=args.node,
        )

        ctx = DispatchContext(
            dirs=dirs, run_id=run_id, translate_cfg=translate_cfg, companion_path=companion_path,
            durable_root_str=args.durable_root, plugin_root_str=args.plugin_root,
            node_bin=args.node, session_id=session_id,
        )

        append_journal(
            durable_root, session_id,
            {"type": "dispatch_loop_started", "segs": segs, "max_concurrent_codex_jobs": args.max_concurrent_codex_jobs},
        )
        segment_results = run_segment_loop(segs, ctx, args.max_concurrent_codex_jobs)
        converged = [r["seg"] for r in segment_results if r.get("converged") is True]
        needs_fix = [r for r in segment_results if r.get("reason") == "needs_fix"]
        failed = [r for r in segment_results if r.get("converged") is False and r.get("reason") != "needs_fix"]

        result = {
            "success": True,
            "session_id": session_id,
            "run_id": run_id,
            "resume": run_result.get("resume"),
            "durable_root": str(durable_root),
            "segs": segs,
            "counts": select_result.get("counts"),
            "engine": engine_cfg,
            "dispatched": True,
            "results": segment_results,
            "summary": {
                "converged": converged,
                "needs_fix": [{"seg": r["seg"], "round_label": r.get("round_label")} for r in needs_fix],
                "failed": [{"seg": r["seg"], "reason": r.get("reason")} for r in failed],
            },
        }
        append_journal(
            durable_root, session_id,
            {"type": "driver_exit", "success": True, "summary": result["summary"]},
        )
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
