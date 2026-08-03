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
functions under Node (never a hand-written second copy), relaying
codex_job.py's own reported `reason`/`error_detail` for any failure
UNCHANGED (see `_codex_job_outcome()`'s own docstring for the one narrow
exception: when the child produced no parseable stdout at all -- a genuine
invocation-level anomaly, not a codex_job.py-reported outcome -- this
driver attributes a `driver-`-prefixed reason to ITSELF, honestly labeled
by that prefix as its own, rather than inventing a codex_job.py-shaped one
in its place), and driving ledger/cache-key bookkeeping directly through
ledger_update.py/cache_key.py (no agent() indirection -- this mechanical
bookkeeping never needed judgment, only a shell call, which is exactly
what this driver has natively).

THE FIX STEP HAS TWO SHAPES NOW, chosen by `--fix-mode`, and the DEFAULT
is still the one this driver cannot perform itself.

`--fix-mode=handoff` (DEFAULT, and the previous release's behaviour
verbatim): applying review findings to a draft is a real LLM
content-editing turn (mass-translate-wf.template.js's own
`callFix`/`fixPrompt`, dispatched via a Claude `agent()` call), and a plain
Python process has no equivalent capability. So when a segment's review
comes back not-clean, `process_segment()` below stops at that segment and
returns a `needs_fix` result carrying the round label, the findings, AND
the exact fix prompt text (rendered the same executed-template way as every
other prompt) -- the caller (the orchestrating Claude session running W5)
performs ONE Claude fix turn using that prompt, then re-invokes this
driver, which re-derives the segment's state from durable disk facts (see
`derive_next_action()`) and picks up at the next review round.

`--fix-mode=codex` (#409 track B): a numeric round becomes ONE `--kind
fixreview` codex job that reviews the draft it finds and then applies its
own findings, producing TWO artifacts. The self-attestation that ordering
invites is prevented STRUCTURALLY rather than by prompt wording: the
review is bound to the sha1 of the PRE-edit draft, so a round that changed
anything publishes a pair whose review no longer describes the draft, and
convergence requires those to match. The review half of round N therefore
judges round N-1's edit and never its own; its own edit is judged by N+1.
What that does NOT buy -- stated because an overstated mitigation stops
being questioned -- is any defence against a call that copies the draft
unchanged and reports it clean. That converges, and nothing detects it; it
is the same under-reporting risk a plain review round has always carried,
untouched by this change rather than introduced by it. Two artifacts have no
single canonical path, so `codex_job.py` validates and STAGES them and never
promotes; this driver publishes the pair through the transaction layer below
(durable intent, review renamed first, roll-forward on recovery). The
mandatory final round stays a plain review in both modes.

This driver's OWN contribution is eliminating the WAIT-polling agent()
calls around translate/review (#348's chunking apparatus) -- "B only pays
off after the driver removes the wait agents" is the project's own framing
for exactly this split.

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
   after hygiene); there was no project-level one before this lock.
   `git grep -rn LOCK_EX` across `assets/scripts/*.py` now returns FOUR
   call sites, and they are three different locks: `codex_job.py`'s
   per-segment lease, this project lock plus its own self-test, and
   `segment_lease()` below -- which is not a fourth lock at all, but
   this driver taking `codex_job.py`'s per-segment lease file by exact
   path, so that reading canonical state and writing it happen under one
   boundary. (This sentence used to claim "exactly one call site". It was
   already wrong when written and the count moved again afterwards; a
   claim that cites a command has to be re-run, not re-read.) Without
   the project lock, two
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
spend: real codex dispatches. `check_volume_cap()` below gates the SAME
resource under the SAME cap as `mass-translate-wf.template.js`'s own
already-shipped preflight, from a second, independent entry point --
exactly how `skeptic_setup.py`'s own preflight duplicates its Workflow
template's estimator for the identical reason (two entry points into one
resource, each needing its own gate).

THE PER-SEGMENT NUMBER IS NOT SHARED WITH THE TEMPLATE, and this used to
claim it was. The template counts `max_fix_rounds + 2`: its own round
structure, where a fix is a plain Workflow `agent()` call and never a
`codex_job.py` launch. This driver runs its own loop, and admits against
the number that loop is actually capped at -- see
`codex_jobs_per_segment()`, which is `max_fix_rounds + 3` on the default
`--fix-mode=handoff` and larger under `codex`, where a numeric round IS a
codex job (`--kind fixreview`) and the per-round redispatch allowances are
terms of the bound. Two consumers of one knob, each counting its own loop;
copying the template's figure here made the admission smaller than the
spend it was admitting.
`batch_agent_cap` itself is untouched and unremoved -- it keeps doing its
own job for the glossary/skeptic Workflow passes and for
`resume_setup.SUBST_FIELDS`'s existing required-field contract; this
script simply never reads or enforces it, because a driver-dispatched
batch never triggers the resource it measures.

## What this driver deliberately does NOT implement (say so, not stub it)

- The FIX step, ON THE DEFAULT `--fix-mode=handoff` -- see the STATUS
  section above. `process_segment()` returns `needs_fix` (round label,
  findings, and the exact fix prompt text) instead of performing it.
  `--fix-mode=codex` DOES perform it, as half of a merged `--kind
  fixreview` call whose two artifacts this driver publishes through the
  transaction layer; that mode is reachable but not the default, because
  turning it on moves who edits the user's text.
- `mass-translate-wf.template.js`'s own W6 (`log(...)`d final summary) /
  batch-level `mergeLedgerPrompt` completeness check. This driver reports
  its own per-segment results (`run()`'s returned `summary`); the batch-
  final `ledger_merge.py --expected-segs ... --run-token ...` completeness
  re-check that PLAN.md's step-4 acceptance criteria describe is not wired in
  here -- it is a single, whole-batch, end-of-run concern the orchestrating
  session can run directly (mirroring `mergeLedgerPrompt`'s own script
  call, again with no agent() indirection needed) after this driver's
  `results` show every segment converged or accounted for.
- Any of PLAN.md's later stage-0 trust-boundary work (a content-addressed
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
`select_segments.py` and `codex_job.py` -- from `${durable_root}/scripts/`,
the SAME writable-by-codex tree the whole `--plugin-root` mechanism exists
to route around. Omitting this would leave a brand-new, gate-enforcing
script with the exact trust gap the last several LT-409 hardening rounds
closed everywhere else. For `select_segments.py`, `--plugin-root` is
forwarded verbatim (it accepts the flag) together with a synthesized
`--durable-root` (this script has no `--durable-root` of its own to
forward, so what's synthesized is always its own resolved durable root --
see `resolve_dirs()`).

codex round-3 correction: two claims used to stand here about `codex_job.py`
-- that `--plugin-root` "only changes WHICH FILE this script Popens, never
a flag forwarded to it", and (in `_root_forward_args()`'s own docstring)
that `codex_job.py` "accepts neither flag on the data side". Both were
false, and the first one directly contradicted `build_codex_job_argv()`'s
OWN docstring in this same file, which already listed `[--plugin-root]`
among the flags it splices. Verified against the shipped `codex_job.py`:
it DOES accept `--plugin-root` (`codex_job.py:1050`) -- `build_codex_job_argv()`
forwards it whenever `plugin_root_str` is set (below, in the argv-building
section) -- and consumes it in `_trusted_scripts_dir()` (`codex_job.py:327-336`),
which returns `{plugin_root}/assets/scripts/` directly when given and falls
back to its own `__file__`-relative `SCRIPTS_DIR` only when it is absent.
"Accepts neither flag" is HALF right, not simply backwards: `codex_job.py`
accepts `--plugin-root` but NOT `--durable-root` -- for the DATA side it
takes a required `--cwd` instead (`codex_job.py:1030`), which this driver
already forwards as `str(durable_root)` in every dispatch (build_codex_job_argv()'s
own `--cwd` argument), matching every other v1.17.0-hardened script's
self-anchored-unless-redirected shape without needing a second, redundant
root flag on this particular leaf.

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
import contextlib
import fcntl
import importlib.util
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import NoReturn, Optional, TypeGuard

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
)
# review_ready.py deliberately NOT here (codex, round 2): the canonical
# segments/{seg}.review.json this driver reads is ALREADY validated by
# review_ready.py before it is ever written -- codex_job.py runs it
# internally as part of its own validate-before-promote flow -- so a
# second, driver-side call would re-check an artifact review_ready.py
# already gated, with no round-matching benefit of its own (this driver
# still has to try each candidate --expect-token itself, in
# derive_next_action(), to learn WHICH round is recorded -- a single
# review_ready.py call only answers yes/no for ONE token, never "which").
# A prior release resolved this sibling and staged a fixture for it
# without ever calling it -- deleted rather than wired in.
_TEMPLATE_NAME = "mass-translate-wf.template.js"


def resolve_dirs(durable_root_str, plugin_root_str=None):
    """LT-409 convention: `durable_root_str` governs DATA (runs/) -- rebuilt
    from that root when given, self-anchored otherwise.

    `plugin_root_str` is a SEPARATE, independent input governing where the
    SIBLING SCRIPTS this script shells out to / Popens (select_segments.py,
    codex_job.py, and -- Phase 2 -- resume_setup.py, resolve_codex_companion.py,
    ledger_update.py, cache_key.py, draft_ready.py, validate_draft.py,
    plus the mass-translate-wf.template.js TEMPLATE this
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
    ledger_update.py, cache_key.py -- all LEAVES per their own module
    docstrings) omits --plugin-root from the result even when
    plugin_root_str is set; --durable-root is still forwarded so the leaf
    reads the right DATA root. select_segments.py and resume_setup.py
    accept both (the default).
    codex_job.py is NOT covered by this helper at all -- build_codex_job_argv()
    hand-builds its own --plugin-root handling inline rather than calling
    this function. codex round-3 correction: this used to also claim
    codex_job.py "accepts neither flag on the data side" -- false, and
    HALF backwards, not simply reversed: codex_job.py DOES accept
    --plugin-root (codex_job.py:1050, forwarded by build_codex_job_argv()
    whenever plugin_root_str is set) but does NOT accept --durable-root --
    for the DATA side it takes a required --cwd instead (codex_job.py:1030),
    which this driver already forwards separately as str(durable_root).
    The file path Popen'd for it changes too (see resolve_dirs()), but
    that is in addition to, not instead of, the --plugin-root flag itself
    changing behavior inside codex_job.py (see the module docstring's own
    "Beyond the 8 named properties" section for the full correction).
    resolve_codex_companion.py is ALSO not covered (codex round-3
    correction -- it used to be listed above, wrongly): resolve_companion_
    path() below hand-builds its own argv rather than calling this helper,
    and always forwards --durable-root unconditionally, including in the
    "both root strings None" self-anchored case where this helper would
    return []. See that function's own comment for why switching it to
    this helper would be a real behavior change, not a cleanup.
    """
    # codex round-4 MAJOR: forwards the ALREADY-RESOLVED dirs["durable_root"]
    # (a Path resolve_dirs() computed FROM this same durable_root_str),
    # never the raw durable_root_str itself. The raw string used to be
    # forwarded here whenever it was given -- but run_select_segments()
    # (this function's own caller at :892-897) runs its subprocess with
    # `cwd=str(dirs["durable_root"])`, the already-resolved absolute path.
    # A RELATIVE --durable-root value (a real, supported CLI shape: this
    # driver's own --durable-root help text says "omit for today's self-
    # anchored behavior", implying any other value, relative or absolute,
    # is accepted) would then be resolved by the CHILD a second time
    # against that already-resolved cwd -- from /repo with
    # --durable-root projects/book, the parent resolves to
    # /repo/projects/book, forwards the raw "projects/book" string, and
    # the child lands on /repo/projects/book/projects/book. Nothing
    # depended on forwarding the raw string: every sibling script resolves
    # its own --durable-root via Path(value).resolve(), and
    # Path(absolute).resolve() is a no-op, so forwarding the already-
    # resolved absolute path is correct and safe for every caller of this
    # function, including the ones that do NOT override cwd (they inherit
    # this driver's own process cwd, against which the raw string would
    # ALSO have resolved correctly by coincidence -- but relying on that
    # coincidence, rather than always forwarding the one value that is
    # correct regardless of the child's cwd, is exactly what let the
    # run_select_segments() call site diverge). Same fix shape as the
    # identical defect found (and fixed) in resume_setup.py and twice in
    # select_segments.py.
    args = []
    if durable_root_str is not None or plugin_root_str is not None:
        args += ["--durable-root", str(dirs["durable_root"])]
    if plugin_root_str is not None and supports_plugin_root:
        # Same fix, same reason, for the OTHER root: resolve_dirs() already
        # resolved plugin_root_str once, against THIS process's own cwd, to
        # build every plugin-anchored path in `dirs` -- forwarding the raw
        # string here would let a relative --plugin-root resolve a SECOND
        # time, against the CHILD's cwd (durable_root, for the one caller
        # that overrides it), landing parent and child on two DIFFERENT
        # plugin roots. Path(plugin_root_str).resolve() here reproduces
        # the identical resolution resolve_dirs() already performed (same
        # string, same unchanged process cwd), never a second, independent
        # answer.
        args += ["--plugin-root", str(Path(plugin_root_str).resolve())]
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
# "no shared lib between self-contained scripts" convention. codex round-3
# correction: this comment used to claim this script "never builds a path
# from a segment id directly" -- false, it does, at task_file_path()
# (durable_root / "segments" / f".codex_task.{kind}.{seg}.{disp}"),
# derive_next_action()'s own review_path (segments_dir / f"{seg}.review.
# json"), and _read_review_obj()'s copy of the same. The REAL protection is
# cross-file, not "this script never does it": select_segments.py's own
# load_candidate_segments() fatals on any manifest.json `seg` failing its
# validate_seg() (select_segments.py:764-786, the check at :778-780) --
# every `seg` this script ever operates on already came from THAT
# validated output (the `segs` list Step 1's own gate returns), never from
# an unvalidated source, before this script ever builds a path from one.
# --only-segs values are still checked here FIRST regardless (validate_seg()
# below, identical to select_segments.py's own copy), so a malformed id is
# refused before it is ever spliced into the select_segments.py subprocess
# argv -- this script's own check is real, it is just not the reason a
# manifest-sourced seg id is safe to build a path from.
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


def _dedupe_segs(segs: list) -> tuple:
    """(deduped_segs, duplicate_ids) -- order-preserving, first occurrence
    wins. manifest.schema.json has no uniqueItems on segments[], and
    select_segments.py's default (non---only-segs) path appends every
    manifest entry with no dedupe of its own (only the --only-segs path
    does) -- so a duplicate manifest entry reaches this driver's own SEGS
    list unfiltered. See run()'s own call site for why this matters:
    pool.map() driving the same segment on two worker threads at once."""
    seen = set()
    deduped = []
    duplicates = []
    for seg in segs:
        if seg in seen:
            duplicates.append(seg)
            continue
        seen.add(seg)
        deduped.append(seg)
    return deduped, duplicates


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


def acquire_driver_lock(durable_root: Path, session_id: "str | None" = None):
    """Acquires the project-wide LOCK_EX|LOCK_NB lease on
    runs/.driver.lock. Returns the open file descriptor on success -- the
    CALLER must keep it open (never close it) for the whole process
    lifetime; closing (or letting the process exit) is what releases it,
    kernel-side, with no unlink and no stale-pid probe ever needed.

    Raises DriverError (exit_code=1) if another process already holds the
    lease -- non-blocking by design (LOCK_NB): a second driver on the same
    project must refuse immediately and namelessly-loudly, never queue
    behind the first one silently.

    codex round-3: what this lease actually excludes, stated precisely
    rather than asserted as an absolute. It excludes a SECOND DRIVER ON
    THIS SAME MACHINE, and only on a filesystem that genuinely enforces
    `flock` -- see the self-test right below for detecting when that
    second condition does not hold. It does NOT exclude two drivers on
    TWO DIFFERENT MACHINES each pointed at what looks like the same
    durable root through a sync-replicated folder (Synology Drive,
    Dropbox, iCloud, and similar): each machine takes a perfectly valid
    LOCAL lock against its own local replica, the self-test below PASSES
    on each of them individually (the local filesystem really does
    enforce flock), and the sync daemon reconciles the resulting
    conflicting writes afterward -- there is no shared kernel between the
    two machines for any flock-based scheme to see across, so this is a
    DIFFERENT failure than an unenforced filesystem and the self-test
    below cannot detect it. Closing that case needs a lease with holder
    identity and a heartbeat, checked against a shared authority both
    machines can reach -- a real redesign, explicitly out of scope here.

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
            f"be stale while a process holds it, on a filesystem that "
            f"enforces flock, on THIS machine. It does not see a second "
            f"driver on a DIFFERENT machine sharing this durable root "
            f"through a sync-replicated folder -- that is a different "
            f"failure this lease cannot exclude at all.",
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

    # codex round-3: a runtime self-test that the lease this function just
    # returned is genuinely enforced HERE, on THIS durable root's
    # filesystem -- not merely a wording fix to the refusal message above,
    # which is true only on the FAILURE path; the dangerous direction is
    # the opposite one, where acquisition SILENTLY SUCCEEDS TWICE and
    # nothing is ever printed. `flock` is scoped per OPEN FILE
    # DESCRIPTION, not per process or per fd table, so opening the SAME
    # path a SECOND time (a genuinely independent open, not a dup of
    # `fd`) and attempting the SAME LOCK_EX|LOCK_NB is a real, separate
    # contention attempt against the lease `fd` already holds -- never a
    # self-deadlock, since flock() within one process across two
    # DIFFERENT open file descriptions on the same file behaves exactly
    # like two different processes would. On a conforming local
    # filesystem this second attempt MUST be refused (measured directly
    # on this machine: BlockingIOError, errno 35). If it instead
    # SUCCEEDS, the filesystem is not enforcing flock at all and the
    # lease this function just "acquired" is worthless -- warned on
    # stderr unconditionally (this driver's own CLI docstring already
    # promises "all human-readable detail on stderr", and a caller with
    # no session_id to journal against, e.g. a direct test, still
    # deserves to see this) and journaled when a session_id is given, but
    # NEVER fatal: failing the acquire over a DETECTED gap would turn a
    # detection into a brand-new outage on every affected filesystem,
    # strictly worse than the silent gap it replaces.
    self_test_fd = None
    try:
        self_test_fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
        fcntl.flock(self_test_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        pass  # expected: a conforming filesystem refuses the second attempt
    else:
        sys.stderr.write(
            f"WARNING: the project lease at {lock_path} is NOT enforced by "
            f"this filesystem -- a second flock() attempt against the SAME "
            f"path succeeded instead of being refused. Two drivers can run "
            f"against this project AT ONCE, on THIS machine, with no "
            f"warning from the refusal path above (it never fires). Known "
            f"on some network filesystems (NFS/SMB) that do not implement "
            f"flock; not a substitute for a real holder-identity+heartbeat "
            f"lock on such a mount.\n"
        )
        if session_id is not None:
            try:
                append_journal(
                    durable_root, session_id,
                    {"type": "lock_self_test_failed", "lock_path": str(lock_path)},
                )
            except Exception:
                pass  # best-effort diagnostic only, must never mask the real acquire above
    finally:
        if self_test_fd is not None:
            try:
                fcntl.flock(self_test_fd, fcntl.LOCK_UN)
            except OSError:
                pass
            os.close(self_test_fd)

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


# --fix-mode. `handoff` is 1.18.0's behaviour verbatim: a not-clean numeric
# round returns needs_fix and the caller performs the edit. `codex` dispatches
# --kind fixreview -- one codex call producing BOTH a fixed draft and its
# review -- and publishes the pair through the transaction layer further down.
#
# handoff is the DEFAULT, and that is a deliberate release decision rather than
# caution: this release makes the codex path reachable, not active. Flipping the
# default is a separate change, because it moves who edits the user's text.
#
# Declared HERE, well above the transaction layer that is the mode's real
# subject matter, because the volume admission immediately below is already
# per-mode and evaluates these at def time.
FIX_MODE_HANDOFF = "handoff"
FIX_MODE_CODEX = "codex"
FIX_MODES = (FIX_MODE_HANDOFF, FIX_MODE_CODEX)

# --fix-mode=codex's three per-round redispatch allowances (#409 track B).
# Each is a term of the K below, and each has to be a NUMBER rather than the
# word "bounded": a redispatch family with no count is exactly how the
# clean-but-stale path came to have no bound of its own.
DEFAULT_MAX_REJECTED_CANDIDATES_PER_ROUND = 2   # R_reject, engine-overridable
MAX_STALE_REDISPATCHES_PER_ROUND = 1            # R_stale
MAX_FABRICATED_LOC_RETRIES = 1                  # R_final -- the existing one-retry bound
DEFAULT_MAX_TXN_FAILURES_PER_SEGMENT = 3

# #409 track B, "the size fallback" (R_size = 0 falls back to handoff). A
# --fix-mode=codex fixreview call has to fit inside the model's real context
# window, and references/orchestration-and-batching.md's own §6 measured
# that the largest real segment does NOT fit with the required margin -- so
# a segment whose estimated cost is unobservable or over budget falls back
# to `handoff` for itself alone, silently, never as a batch failure or a
# truncated job. See fixreview_context_estimate()/effective_fix_mode() below
# (defined once DispatchContext exists, since both take a `ctx` and read
# ctx.segments_dir / ctx.dirs["durable_root"]) for the mechanism; this block
# is only the numbers it runs on.
#
# EVERY CONSTANT BELOW IS MEASURED AGAINST ONE NAMED MODEL, `gpt-5.6-sol` --
# the model ~/.codex/config.toml pins at measurement time, per ~/.codex/
# models_cache.json (S6-TOKEN-MARGIN-RESULTS.md §8.1). THIS BUDGET DOES NOT
# ADAPT TO engine.model. profile.schema.json documents engine.model as
# OPTIONAL -- unset (the common case) resolves to whatever ~/.codex/
# config.toml's own default is at RUN time, which this file never reads,
# and a profile MAY pin it to a different model outright. In either case,
# if the real model in use has a smaller context window than gpt-5.6-sol's,
# this gate measures against the WRONG ceiling and reports a margin that
# is not the margin actually available -- a comfortable-looking pass that
# is not one. Deliberately NOT auto-resolved here: doing so would mean
# either hardcoding a model->window catalog that goes stale the moment a
# new model ships, or reading ~/.codex/models_cache.json at dispatch time,
# coupling this plugin to Codex's own private cache layout for a value it
# has no other reason to depend on. Until one of those is judged worth
# building, an operator pinning engine.model to a smaller-window model
# MUST lower engine.max_fixreview_context_tokens by hand to match.
#
# TOKENIZER: cl100k_base throughout, the CONSERVATIVE (higher tokens-per-byte)
# of the two plausible tokenizers §6 measured against (the other being
# o200k_base). No public tokenizer spec exists for gpt-5.6-sol itself (the
# model ~/.codex/config.toml pins) -- cl100k_base is a defensible stand-in
# precisely because it is the one that reports LESS margin, not more; a
# budget sized against it fails closed rather than optimistic. Confirmed on
# base_instructions below too: cl100k gives 3,576 tokens against o200k's
# 3,552 -- cl100k is again the higher (more conservative) figure.
#
# TOKEN BUDGET, NOT A BYTE BUDGET, AND THAT DISTINCTION IS LOAD-BEARING. An
# earlier draft of this gate compared one blended byte SUM against a single
# byte threshold (423,297 B, the byte-domain equivalent of the token budget
# below at this formula's own per-component ratios). Across the 207 real
# segments measured, the two formulations agree on the verdict for every
# one today -- but 75% of real segments already sit past the ratio SKEW
# boundary between the segpack/draft/prose terms (a 33.63% draft-byte
# fraction is where a single blended ratio stops describing the actual mix),
# and the corpus's second-largest segment has only 72,276 B of headroom
# before the two formulations would disagree -- 29% less headroom than an
# earlier, less conservative version of this budget had. Where they would
# ever disagree, the byte sum errs in the EXPENSIVE direction: it can admit
# a call the real, per-component token estimate below would have refused,
# letting an oversized job fail only after it has already been paid for.
# Summing PER-COMPONENT token estimates (each divided by ITS OWN measured
# ratio, never one blended figure) is what removes that failure mode.
FIXREVIEW_CONTEXT_WINDOW_TOKENS = 272_000
# gpt-5.6-sol's own `context_window`, per ~/.codex/models_cache.json -- the
# cache the codex runtime itself resolves against (orchestration-and-
# batching.md §6). Describes the MODEL, not any one project; not itself
# profile-configurable.
FIXREVIEW_EFFECTIVE_CONTEXT_WINDOW_PERCENT = 0.95
# The SAME model record's `effective_context_window_percent` field -- easy
# to miss sitting next to `context_window` above, and missing it already
# flipped a verdict once during this feature's own measurement pass. The
# window this mode may actually plan against is 272,000 * 0.95 = 258,400
# tokens, not the raw 272,000 -- the model reserves the remaining 5% for
# itself, and this gate has no visibility into what for.
FIXREVIEW_REQUIRED_MARGIN = 0.25
# The margin bar §6 states this mode must clear to be considered safely
# within budget, applied to the EFFECTIVE window above, not the raw one.
# Feeds ONLY the default below -- profile.yml may override the resulting
# token count directly via engine.max_fixreview_context_tokens, without
# either ratio above ever being re-consulted.
DEFAULT_MAX_FIXREVIEW_CONTEXT_TOKENS = int(
    FIXREVIEW_CONTEXT_WINDOW_TOKENS * FIXREVIEW_EFFECTIVE_CONTEXT_WINDOW_PERCENT
    * (1 - FIXREVIEW_REQUIRED_MARGIN))
# = 258400 * 0.75 = 193800. Keep profile.schema.json's own "default"
# annotation (documentation only -- nothing fills it in at validation time,
# see _optional_engine_int()'s own docstring) numerically equal to this by
# hand; every other OPTIONAL engine.* knob in this file already carries
# that same obligation.

# bytes-per-token ratios, MEASURED under cl100k_base on the real corpora
# (orchestration-and-batching.md §6) -- divide an observed byte count by the
# matching ratio to get an estimated token count.
FIXREVIEW_SEGPACK_BYTES_PER_TOKEN = 3.0376
# Measured on real segpack_<seg>.json content (source text + canon_map)
# across the live corpora.
FIXREVIEW_DRAFT_BYTES_PER_TOKEN = 3.4155
# Measured on real <seg>.draft.json content, same corpora and tokenizer.
# Differs from the segpack ratio above because the draft carries the
# TARGET-language text (this project's case: Cyrillic), whose cl100k_base
# encoding density differs from the segpack's mostly-source-language
# content -- a real difference in what is being measured, not rounding
# noise on the same quantity. This is also the term fixreview_context_
# estimate() below DOUBLES (the draft is read once as input, rewritten once
# as output), so the density difference and the doubling compound rather
# than offset.
FIXREVIEW_PROSE_BYTES_PER_TOKEN = 3.403
# style_bible.md and review_TASK.md are ordinary English project prose --
# but carrying non-Latin TARGET-language excerpts, which is why this ratio
# sits well below base_instructions' own ~5.0 B/token pure-English figure.
# 3.403 is the WORST (lowest, i.e. most token-dense) of eight real
# measurements across four books, cl100k_base throughout: style_bible.md
# ranged 3.459-4.042 B/token, review_TASK.md 3.403-4.114 B/token. Taking the
# worst rather than an average is what keeps this safe: every real ratio
# observed is HIGHER than 3.403, so dividing by 3.403 always estimates AT
# OR ABOVE the true token count for any of the eight measured files, and
# the gate can only over-reserve budget, never under-reserve it -- the
# correct direction for a guard whose failure mode is letting an oversized
# call through onto paid work.
#
# MEASURED PER PROJECT AT RUNTIME, NEVER BAKED IN, AND THIS IS WHY: total
# per-project overhead from style_bible.md + review_TASK.md alone measured
# 13,756 / 13,814 / 14,428 / 17,956 tokens across four real books -- a ~30%
# spread from style-guide length alone, before anyone writes a longer one.
# A 200 KB style_bible.md (this ratio applied) would alone consume roughly
# a third of the whole budget below. A module constant could never have
# known any one project's own style_bible.md size in advance; only
# stat()-ing it fresh, every call, catches this (see
# fixreview_context_estimate()'s own docstring).
FIXREVIEW_BASE_INSTRUCTIONS_TOKENS = 3_576
# Codex's OWN fixed agent system prompt (`base_instructions`), shipped on
# EVERY call regardless of project or segment -- measured at 17,766 bytes,
# 3,576 tokens under cl100k_base (o200k_base gives 3,552; cl100k is again
# the conservative, higher figure -- see the tokenizer note above). Plain
# English prose at ~5.0 B/token -- denser than it looks by byte count
# alone, which is why it is shipped here as a directly measured TOKEN
# count rather than a byte figure divided by any of this file's own
# ratios (none of which describe Codex's own prompt text).
FIXREVIEW_DISPATCH_PROMPT_TOKENS = 1_508
# fixReviewDispatchPrompt()'s own rendered length (mass-translate-wf.
# template.js), directly measured in tokens -- FIXED, because the prompt
# embeds no segment content, only a path-based instruction to go read the
# four files this estimate sizes separately, at runtime (orchestration-
# and-batching.md §6: both real rendered prompts measured exactly 6,489
# bytes; the byte figure is offered here only as provenance -- 6489 divided
# by neither ratio above lands on 1,508, because this is an independent
# direct token measurement of the SAME text, not a byte-ratio conversion).
FIXREVIEW_WORST_CASE_REVIEW_OUTPUT_TOKENS = 2_408
# STEP 1's own review JSON output (the findings[] array the call must emit
# BEFORE it rewrites the draft), directly measured worst case -- the draft
# rewrite itself is already counted by the "2 *" term on
# FIXREVIEW_DRAFT_BYTES_PER_TOKEN in fixreview_context_estimate() below;
# this is the review verdict alone.
#
# THE BUDGET THIS ALL ADDS UP TO, AS A DERIVATION (not itself computed this
# way in code -- the code computes ONE estimate and compares it against the
# budget constant above; this is the same arithmetic read the other way,
# for a reader checking the numbers):
#   usable window   (272,000 * 0.95)                 = 258,400
#   * 0.75 (the 25% margin reserved)                 = 193,800  <- the budget
#     - FIXREVIEW_BASE_INSTRUCTIONS_TOKENS               3,576
#     - FIXREVIEW_DISPATCH_PROMPT_TOKENS                 1,508
#     - style_bible.md + review_TASK.md      (per-project, measured above)
#     - FIXREVIEW_WORST_CASE_REVIEW_OUTPUT_TOKENS        2,408
#   = what remains for segpack_bytes + 2 * draft_bytes, at their own ratios.
#
# WHAT THIS BUYS, MEASURED: across the 207 real segments, exactly ONE falls
# back under this budget, and it fails by 39.2% -- not a knife's-edge
# margin. The next-largest segment clears by +17.0%, and the one after that
# by +50.5%: a real gap between the one failure and everything else, not a
# threshold that happens to sit in the middle of a cluster. That one
# segment's own four real inputs total 599,128 B and fail the margin under
# BOTH plausible tokenizers -- under the conservative one (cl100k_base,
# used throughout this file) it does not fit the window AT ALL. This gate
# is not a precaution against a hypothetical; it is what keeps a real,
# existing segment from being dispatched into a merged round it cannot
# complete.


def codex_jobs_per_segment(
    max_fix_rounds: int,
    fix_mode: str = FIX_MODE_HANDOFF,
    *,
    max_rejected_candidates_per_round: int = DEFAULT_MAX_REJECTED_CANDIDATES_PER_ROUND,
) -> int:
    """The per-segment upper bound on codex jobs for ONE driver invocation --
    which is the SAME number as process_segment()'s own iteration cap, by
    construction: that loop launches at most one job per iteration, so
    bounding the iterations bounds the jobs and one expression serves both.

    Keeping them one number is not tidiness. Track B's earlier drafts carried
    an admission estimate and a loop cap derived separately, and then had to
    assert "estimate <= cap" -- an inequality no configuration could satisfy
    once any redispatch term was positive, because the cap WAS the base term.

      handoff: M + 3   -- unchanged control flow, but see the correction below
      codex:   M + 3 + M*(R_reject + R_stale) + R_final

    THE HANDOFF VALUE IS M+3, NOT THE M+2 THIS FUNCTION USED TO RETURN, and
    that is a correction rather than a new allowance. M+2 counts the NORMAL
    path (one translate, M reviews, one mandatory final review) and was being
    used as if it were an upper bound; the live iteration cap has always been
    M+3, and the persistent-staleness path really can spend every one of those
    iterations on a dispatch (the loop's own regression test asserts
    len(dispatches) == max_iterations). So the admission was under-counting by
    one job per segment -- N jobs per run -- against a bound the loop never
    promised. Nothing about handoff's BEHAVIOUR changes here; only the number
    the admission checks stops being smaller than what the loop permits.

    mass-translate-wf.template.js's own preflight keeps its M+2: that estimate
    describes the WORKFLOW's round structure (where fixes are separate
    agent() calls, not codex jobs), not this driver's loop, and the two have
    never been the same quantity."""
    if fix_mode not in FIX_MODES:
        fatal(f"internal error: unknown fix mode {fix_mode!r}", exit_code=2)
    base = max_fix_rounds + 3
    if fix_mode == FIX_MODE_HANDOFF:
        return base
    per_round_redispatches = max_rejected_candidates_per_round + MAX_STALE_REDISPATCHES_PER_ROUND
    return base + max_fix_rounds * per_round_redispatches + MAX_FABRICATED_LOC_RETRIES


# The keys BOTH profile loaders read, and therefore the ones a mid-startup edit
# can split. Named as a tuple rather than derived from a dict intersection: the
# two loaders return overlapping-but-different shapes, and an intersection would
# silently shrink the moment either gained or lost an unrelated field.
_SHARED_PROFILE_KEYS = (
    "max_fix_rounds", "max_codex_jobs_per_batch", "max_rejected_candidates_per_round",
    "max_txn_failures_per_segment", "max_fixreview_context_tokens",
)


def profile_snapshots_disagree(engine_cfg: dict, translate_cfg: dict):
    """None if the two independent reads of profile.yml agree on every key in
    _SHARED_PROFILE_KEYS, else a refusal dict naming the first disagreement.

    An explicit list, not "every key both happen to return": the two loaders
    return overlapping-but-different shapes, so an intersection would shrink
    the moment either gained or lost an unrelated field -- and the shrinking
    would look exactly like agreement.

    TWO READS OF ONE FILE, AND THE BOUND LIVES IN BOTH. The volume admission
    resolves its numbers from load_engine_config(); the dispatch loop resolves
    its own from load_translate_config(), separately and later. An edit landing
    between them lets the SMALLER number admit the batch while the loop runs on
    the LARGER one -- which defeats precisely the "admission and cap are one
    number" property this release rests on, and defeats it invisibly, because
    both reads are individually valid.

    Refusing is cheap and right: nothing has been dispatched at that point, and
    a profile edited mid-startup is a state the operator wants named rather
    than one for this script to pick a winner for. Pure, so the comparison can
    be tested without racing a real file."""
    for key in _SHARED_PROFILE_KEYS:
        if key not in engine_cfg or key not in translate_cfg:
            continue
        if engine_cfg[key] != translate_cfg[key]:
            return {
                "key": key,
                "admissionValue": engine_cfg[key],
                "dispatchValue": translate_cfg[key],
                "message": (
                    f"profile.yml changed while this driver was starting: "
                    f"engine.{key} read as {engine_cfg[key]!r} for the volume "
                    f"admission and {translate_cfg[key]!r} for the dispatch loop. "
                    f"Nothing has been dispatched; re-run once the profile is settled."
                ),
            }
    return None


def check_volume_cap(n_segs: int, max_fix_rounds: int, max_codex_jobs_per_batch: int,
                     fix_mode: str = FIX_MODE_HANDOFF, *,
                     max_rejected_candidates_per_round: int = DEFAULT_MAX_REJECTED_CANDIDATES_PER_ROUND):
    """Returns None if `n_segs` is within the cap, or a refusal dict
    (mirrors mass-translate-wf.template.js's own `{reason,
    estimatedCodexJobs, codexJobsCap}` result shape) otherwise. Never
    raises -- this is a pure, side-effect-free check the caller decides
    what to do with.

    Admission is PER MODE because the bound is: refusing a codex-mode batch
    against handoff's much smaller number would let a run start that its own
    loop is permitted to overspend by a factor of roughly four."""
    per_seg = codex_jobs_per_segment(
        max_fix_rounds, fix_mode,
        max_rejected_candidates_per_round=max_rejected_candidates_per_round,
    )
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


def _optional_engine_int(engine: dict, key: str, default: int, minimum: int, profile_path) -> int:
    """One OPTIONAL integer engine knob, resolved and range-checked.

    Shared by this file's TWO profile loaders because they must agree: they
    read the same profile and feed the same run, so a knob honoured by one and
    defaulted by the other would make the admission check and the loop cap
    disagree about the very number that is supposed to be one number.

    profile.schema.json's `"default"` annotation is documentation-only --
    nothing fills it in at validation time -- so every consumer applies it
    independently, exactly as max_codex_jobs_per_batch already does.

    `isinstance(value, bool)` is rejected explicitly: bool IS an int subclass
    in Python, so `max_rejected_candidates_per_round: true` would otherwise
    resolve to 1 rather than being refused."""
    value = engine.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        fatal(
            f"profile.yml at {profile_path}: engine.{key} must be an integer "
            f">= {minimum} (per profile.schema.json), got {value!r}",
            exit_code=2,
        )
    return value


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
    # codex round-4 MAJOR: was `< 0` ("non-negative"), accepting 0 despite
    # profile.schema.json's own `"minimum": 1` -- see load_translate_
    # config()'s own copy of this same check for the full consequence
    # (an unmatchable round token, the SAME failure class this file
    # already closed once for a different cause).
    if not isinstance(max_fix_rounds, int) or isinstance(max_fix_rounds, bool) or max_fix_rounds < 1:
        fatal(
            f"profile.yml at {profile_path}: engine.max_fix_rounds must be "
            f"a positive integer (minimum 1, per profile.schema.json), got {max_fix_rounds!r}",
            exit_code=2,
        )
    max_codex_jobs_per_batch = engine.get("max_codex_jobs_per_batch", 400)
    if not isinstance(max_codex_jobs_per_batch, int) or isinstance(max_codex_jobs_per_batch, bool) or max_codex_jobs_per_batch < 1:
        fatal(
            f"profile.yml at {profile_path}: engine.max_codex_jobs_per_batch "
            f"must be a positive integer, got {max_codex_jobs_per_batch!r}",
            exit_code=2,
        )
    return {
        "max_fix_rounds": max_fix_rounds,
        "max_codex_jobs_per_batch": max_codex_jobs_per_batch,
        "max_rejected_candidates_per_round": _optional_engine_int(
            engine, "max_rejected_candidates_per_round",
            DEFAULT_MAX_REJECTED_CANDIDATES_PER_ROUND, 0, profile_path),
        "max_txn_failures_per_segment": _optional_engine_int(
            engine, "max_txn_failures_per_segment",
            DEFAULT_MAX_TXN_FAILURES_PER_SEGMENT, 0, profile_path),
        # #409 track B, the size fallback. Not itself a term of any admission
        # formula below (codex_jobs_per_segment/check_volume_cap do not need
        # it -- a fallback changes what KIND a round dispatches, never how
        # MANY iterations a segment may spend), but read here anyway, by the
        # same discipline as its two siblings above: both profile loaders
        # resolving one OPTIONAL knob from the same key with the same default
        # is what lets profile_snapshots_disagree() catch a mid-startup edit
        # for the WHOLE engine block, not a subset of it carved out because
        # one knob happens not to feed admission math today.
        "max_fixreview_context_tokens": _optional_engine_int(
            engine, "max_fixreview_context_tokens",
            DEFAULT_MAX_FIXREVIEW_CONTEXT_TOKENS, 0, profile_path),
    }


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
# Properties 2 + 6 -- the codex_job.py dispatch primitive. codex round-3:
# this comment used to say "not yet called by main() below in a per-segment
# loop", left over from this driver's skeleton phase -- Phase 2 wired it in
# for real: run_one_codex_job() (below) calls dispatch_codex_job(), and
# process_segment() calls run_one_codex_job() for both the translate and
# review dispatch.
# ---------------------------------------------------------------------------


# Mirrors codex_job.py's own `_ACTIVE = frozenset(("queued", "running"))`
# (codex_job.py:102) -- duplicated, not imported, per this project's "no
# shared lib between self-contained scripts" convention. Used ONLY by
# _attempt_cancel_orphan()'s own live status check below, never a
# re-derivation of anything hygiene() itself decides differently.
_ORPHAN_CANCEL_ACTIVE_STATUSES = frozenset(("queued", "running"))


def _attempt_cancel_orphan(*, durable_root: Path, seg: str, disp: str, companion_path: str, node_bin: str) -> None:
    """codex round-2 item 10: best-effort orphan cancellation, called ONLY
    from dispatch_codex_job()'s own backstop-timeout path, right after the
    SIGKILL+reap. Mirrors codex_job.py's own `hygiene()` method
    (codex_job.py:704-740), including its live status check (codex round-4
    MINOR correction: an earlier version of this function skipped that
    check and went straight from "joblog says launched" to cancelling --
    a real, not merely cosmetic, divergence from the claim this docstring
    already made. The joblog's local "launched" status only means "not
    yet reaped locally", not "still active remotely": the companion
    task-worker runs the model turn independently of this backstop's own
    local process, so a wedged LOCAL wrapper can coexist with an
    ALREADY-COMPLETED remote job. A blind unconditional cancel would send
    a cancel command against a job that finished on its own --
    companion 1.0.6's own handleCancel writes status:"cancelled"
    UNCONDITIONALLY after a non-blocking attempt, so it would overwrite
    the job's own completed status with a cancelled one purely because of
    a local wedge that has nothing to do with the remote job's own
    outcome. hygiene()'s own live status query, verified `workspaceRoot`
    match, and `status in _ACTIVE` gate before cancelling are now
    mirrored here for real, not merely claimed.

    The ONLY place to learn where to query/cancel is the joblog's own
    recorded `jobCwd` -- codex-companion keys its job store by a hash of
    the git-toplevel-resolved cwd, and codex_job.py's sandbox is a fresh
    `mkdtemp()` PER INVOCATION, so querying with any other cwd (e.g.
    `durable_root`) hits a DIFFERENT, unrelated store and returns
    "No job found" -- the identical string whether the job never existed
    or the wrong workspace was asked. Do not let that false absence read
    as "already gone".

    `jobId`/`jobCwd` ARE durable by the time this driver's backstop can
    realistically fire: codex_job.py's own `launch()` writes both, via an
    atomic O_EXCL+os.replace, BEFORE `poll()` (codex_job.py:797-802) --
    `poll()` is the long phase, so the joblog this reads is already
    written in the overwhelming majority of cases. The one case this
    genuinely cannot close, stated rather than papered over: if the
    backstop fires MID-launch(), before that write completes, there is no
    id to cancel with -- unlikely (the write is local and fast) but not
    impossible, and this function silently does nothing in that case,
    which is the correct behavior (there is nothing to act on).

    Cancellation, when it does happen, is still BEST EFFORT ONLY and
    proves NEITHER that billing stopped NOR that the process died:
    companion 1.0.6's own handleCancel writes status:"cancelled"
    UNCONDITIONALLY after two independent, non-blocking attempts -- an
    app-server turn/interrupt (gated on live thread/turn ids and a live
    broker; returns attempted:false when either is missing) and a
    terminateProcessTree (SIGTERM, not SIGKILL, whose own return value is
    discarded at its own call site). Never raises -- a failed cancel
    attempt (OR a failed/inconclusive status query, which now ALSO means
    "do not cancel") must never turn an already-fatal dispatch into a
    worse one; this is cleanup on a best-effort path, not a gate."""
    joblog_path = durable_root / "segments" / f".codex_job.{seg}.json"
    try:
        joblog = json.loads(joblog_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(joblog, dict) or joblog.get("status") != "launched":
        return
    if joblog.get("disp") != disp:
        # A DIFFERENT dispatch's record (hygiene() or a later invocation
        # already overwrote it since this one launched) -- never cancel a
        # job this call did not itself launch.
        return
    job_id = joblog.get("jobId")
    job_cwd = joblog.get("jobCwd")
    if not isinstance(job_id, str) or not job_id or not isinstance(job_cwd, str) or not job_cwd:
        return
    # Live status check, mirroring hygiene()'s own -- see this function's
    # own docstring for why a blind cancel on the joblog's stale LOCAL
    # "launched" status alone would be wrong. Any failure to positively
    # CONFIRM the job is still active means "do not cancel" -- the
    # inconclusive case defaults to the SAFER, more conservative side
    # (leave a possibly-still-running job alone) rather than the more
    # aggressive one (cancel on ambiguous evidence).
    try:
        status_proc = subprocess.run(
            [node_bin, companion_path, "status", job_id, "--json", "--cwd", job_cwd],
            capture_output=True, text=True, timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        return
    if status_proc.returncode != 0:
        return
    try:
        status_obj = json.loads(status_proc.stdout)
    except json.JSONDecodeError:
        return
    if not isinstance(status_obj, dict):
        return
    job = status_obj.get("job")
    job = job if isinstance(job, dict) else {}
    workspace_root = status_obj.get("workspaceRoot") or job.get("workspaceRoot")
    if workspace_root != job_cwd or job.get("status") not in _ORPHAN_CANCEL_ACTIVE_STATUSES:
        return
    try:
        subprocess.run(
            [node_bin, companion_path, "cancel", job_id, "--cwd", job_cwd],
            capture_output=True, text=True, timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        pass  # best-effort -- see this function's own docstring


def dispatch_codex_job(codex_job_script: Path, job_args: list, *, wait_timeout: float,
                        cancel_context: "dict | None" = None, **popen_kwargs):
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
    result JSON to stdout UNCONDITIONALLY *when finalize() runs at all* --
    which makes it the PRIMARY source callers should parse for
    `reason`/`error_detail` on every NORMAL exit (0/1/2), never a
    driver-composed summary. That source is NOT present on the backstop-
    timeout path below: SIGKILL skips codex_job.py's own
    `finally: finalize()` entirely, so there is no stdout line to read on
    exactly the one failure this docstring used to describe as "always-
    present". Corrected here rather than left to overclaim a second time.

    Raises DriverError if `communicate(timeout=wait_timeout)` itself
    expires -- codex_job.py has its own internal `--deadline-sec`/finalize
    budget and is expected to always terminate within it; `wait_timeout`
    here is a defense-in-depth backstop, not the mechanism that closes
    property 6. On backstop expiry the child (which is in its OWN session,
    so this cannot affect anything else) is SIGKILLed and reaped via a
    second `communicate()` before raising, so no zombie is left behind.
    If `cancel_context` ({"durable_root", "seg", "disp", "companion_path",
    "node_bin"}) is given, a best-effort orphan-cancel is attempted first
    (see `_attempt_cancel_orphan()`'s own docstring for exactly what that
    can and cannot prove) -- `cancel_context=None` (the default) skips it
    entirely, e.g. for a caller/test using a `job_args` shape that is not
    a real codex_job.py invocation.

    What the NEXT reader of this segment's own durable state will find
    after a backstop timeout -- these absences are EXPECTED on this path,
    not evidence of a different, separate problem: the joblog wedged at
    `status: "launched"` forever (or, if this driver's own best-effort
    cancel above genuinely reached and stopped it, whatever `jobCwd`
    happened to hold at that instant -- this function never rewrites it);
    NO fail sentinel for this `disp` (codex_job.py's own
    `_write_fail_sentinel()` also lives inside the `finally:` block SIGKILL
    skips); and NO stdout line at all (see this docstring's own correction
    above) -- so a caller reading `dispatch_result["stdout"]` after this
    exception was avoided (i.e. after catching this DriverError) gets
    nothing to parse, by design, not by omission.
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
        if cancel_context is not None:
            try:
                _attempt_cancel_orphan(**cancel_context)
            except Exception:
                pass  # best-effort cleanup must never mask the real fatal() below
        fatal(
            f"codex_job.py (pid {proc.pid}) did not terminate within its own "
            f"deadline (backstop wait_timeout={wait_timeout}s exceeded) -- "
            f"killed and reaped. This should not happen if codex_job.py's own "
            f"--deadline-sec/finalize budget is honored; treat as a driver-level "
            f"failure for this dispatch, not a normal 'not ready yet' outcome. "
            f"A best-effort orphan-cancel was attempted if cancel_context was "
            f"given; it proves neither that billing stopped nor that the "
            f"process died (see _attempt_cancel_orphan()'s own docstring). No "
            f"stdout line exists for this dispatch -- codex_job.py's own "
            f"finalize() never ran.",
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
    reader debugging AFTER a driver death) depends on.

    Verification-round finding: the claim above ("logged to stderr but
    never aborts the driver") used to be false for one real, content-
    triggerable failure. `event` payloads can embed strings sourced from
    a review/codex_job.py error message -- the same lone-Unicode-surrogate
    class call_template_functions() was fixed for elsewhere (a review
    carrying an unpaired \\uD800-shaped escape decodes fine via
    json.loads() and is promoted normally by review_ready.py, which has
    no pattern to reject it). json.dumps(..., ensure_ascii=False) does
    NOT reject a lone surrogate either -- it round-trips into `line`
    unexamined -- but `fh.write(line)` against a UTF-8-encoded file
    handle does: a lone surrogate cannot be encoded to UTF-8, and that
    raises UnicodeEncodeError, a ValueError subclass, never an OSError.
    The bare `except OSError` below did not catch it, so it propagated
    straight through every caller -- including the two unguarded call
    sites inside run_one_codex_job() -- reaching process_segment()'s own
    outer `except Exception`, which absorbs it but reports the SEGMENT as
    "unexpected-error:UnicodeEncodeError" even when codex_job.py had
    already durably promoted the real result before this write ever ran:
    the journal's own problem, misreported as the segment's. Two
    exception types now, not a bare `except Exception` -- this function's
    claim is specifically about WRITE failures (I/O and the one confirmed
    encoding failure), not an invitation to swallow every possible bug a
    future caller's payload could trigger.

    Review-bot finding: `path.parent.mkdir()` used to sit OUTSIDE the try
    below -- a genuinely different failure (the journal's HOME cannot be
    created at all: an unwritable runs/ directory, a permissions problem
    on the session subdirectory, or -- reproduced directly by the test
    for this -- a plain FILE already occupying where a directory needs
    to exist) escaped this function as a raw OSError, the same "aborts
    the driver" outcome the encoding fix above closed for the write
    itself. Given its own try/except rather than folded into the write's:
    the two failures are different in KIND (home missing vs. write to an
    existing home failing) and an operator debugging one should not be
    told about the other. mkdir() raises only OSError and its subclasses
    for this -- no encoding failure is possible here, `session_id` is
    never attacker-controlled free text in the way a review finding's
    `issue`/`suggest` text is -- so this one stays a plain `except
    OSError`, not the pair the write below needs."""
    path = journal_path(durable_root, session_id)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(
            f"segment_dispatch_driver.py: warning: could not create journal directory "
            f"{path.parent}: {exc}", file=sys.stderr,
        )
        return
    entry = {"ts": _utc_now_iso(), **event}
    line = json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n"
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line)
            fh.flush()
            os.fsync(fh.fileno())
    except (OSError, UnicodeEncodeError) as exc:
        print(f"segment_dispatch_driver.py: warning: could not write journal entry to {path}: {exc}", file=sys.stderr)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# #409 track B -- durable per-segment transaction counters.
#
# TWO counters, deliberately separate, because identity and terminality are
# different jobs and one number cannot do both:
#
#   <seg>.attempts      monotonic, UNBOUNDED, never deleted for the life of
#                       the RUN_ID. Its only job is to make each transaction
#                       attempt's id unique. Bounding it would break valid
#                       projects: it advances on every intent INCLUDING
#                       successful ones, and engine.max_fix_rounds has no
#                       schema maximum, so a legitimate max_fix_rounds >= 7
#                       would exhaust a ceiling of 6 on the NORMAL path.
#
#   <seg>.txn_failures  advances ONLY on a refused transaction. This is what
#                       a ceiling may bound.
#
# Neither lives inside the intent file: the intent is deleted on refusal and
# after commit, so a counter kept there would have no durable allocation
# source at all and a replacement driver would reissue an id already used.
# ---------------------------------------------------------------------------


def _fsync_dir(directory: Path) -> bool:
    """fsync a directory so a rename inside it survives a system crash.

    Split out because it is needed on TWO paths: after a fresh write, and
    again when replaying a charge that a previous run left visible but
    unconfirmed."""
    try:
        dir_fd = os.open(str(directory), os.O_RDONLY)
    except OSError as exc:
        print(
            f"segment_dispatch_driver.py: warning: could not open {directory} to fsync it: {exc}",
            file=sys.stderr,
        )
        return False
    ok = True
    try:
        os.fsync(dir_fd)
    except OSError as exc:
        print(
            f"segment_dispatch_driver.py: warning: could not fsync {directory}: {exc}",
            file=sys.stderr,
        )
        ok = False
    # close(2) CAN report an error, and a close in `finally` outside the
    # handler would escape as an exception rather than a False. That matters
    # because charge_txn_failure() calls this helper on the replay path,
    # OUTSIDE _atomic_write_json()'s own try -- so an escaping OSError would
    # crash the driver mid-batch instead of refusing the round, contradicting
    # the contract every other durability failure here follows. A failed close
    # also leaves the preceding fsync unconfirmed, so it fails the call.
    try:
        os.close(dir_fd)
    except OSError as exc:
        print(
            f"segment_dispatch_driver.py: warning: could not close the fd for {directory}: {exc}",
            file=sys.stderr,
        )
        ok = False
    return ok


def _atomic_write_json(path: Path, obj) -> bool:
    """Durably replace `path` with `obj` as JSON: temp file -> fsync(temp) ->
    os.replace() -> fsync(PARENT DIRECTORY).

    The directory fsync is not decoration. Without it the rename itself may
    not survive a system crash, so a "durable" counter would be durable only
    against process death -- which is the failure this whole mechanism is
    least worried about. Returns False rather than raising: every caller here
    is on a path where the correct response is to refuse the round, not to
    crash the driver mid-batch."""
    tmp = path.with_name(path.name + ".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, ensure_ascii=False, sort_keys=True)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        # NOTE the ordering hazard this creates and that the caller must know
        # about: os.replace() has ALREADY made the new bytes visible by now, so
        # a False from here does NOT mean "nothing changed" -- it means "the
        # change is visible but not confirmed durable".
        return _fsync_dir(path.parent)
    except (OSError, TypeError, ValueError) as exc:
        print(
            f"segment_dispatch_driver.py: warning: could not durably write {path}: {exc}",
            file=sys.stderr,
        )
        try:
            tmp.unlink()
        except OSError:
            pass
        return False


def _is_counter_int(value) -> TypeGuard[int]:
    """A usable counter value: a non-negative int that is NOT a bool.

    The bool exclusion is load-bearing, not pedantry -- `isinstance(False, int)`
    is True in Python and `False >= 0` holds, so {"count": false} would read as
    a legitimate zero and re-authorise a segment whose refusals were already
    exhausted.

    Typed as a TypeGuard (matching _is_valid_intent()'s own reasoning above)
    so a True result narrows `value` to int for the caller -- needed at
    next_attempt_seq()'s own call site, where the value being checked is
    bound to a local BEFORE the check, specifically so this narrowing has
    something to attach to (a TypeGuard on a repeated function-call
    expression does not narrow across the two separate calls)."""
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _attempt_seq_of(parsed):
    """The `attempt_seq` of a parsed attempts file, or None for any shape that
    does not carry one."""
    if not isinstance(parsed, dict):
        return None
    return parsed.get("attempt_seq")


def _read_counter(path: Path):
    """Read a counter file. Returns ("absent", None), ("ok", <parsed>), or
    ("corrupt", None).

    ABSENCE AND CORRUPTION ARE NOT THE SAME STATE, and collapsing them --
    which an earlier version of this did, treating "nothing recorded yet" as
    the conservative reading -- destroys both invariants these counters
    exist for. An unreadable `<seg>.attempts` would restart allocation at 1
    and hand out a transaction id already in use; an unreadable
    `<seg>.txn_failures` would forget the idempotence history AND reset an
    already-exhausted ceiling, silently re-authorising work that was refused.
    Absence may safely initialise an empty counter, because nothing has been
    promised yet. An existing counter that cannot be trusted must fail
    CLOSED."""
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ("absent", None)
    except (OSError, UnicodeDecodeError):
        return ("corrupt", None)
    try:
        return ("ok", json.loads(raw))
    except (ValueError, TypeError):
        return ("corrupt", None)


# ---------------------------------------------------------------------------
# #409 track B -- recovery classification for the paired-publication
# transaction.
#
# Deliberately PURE: it takes an already-gathered `observed` mapping and
# returns a decision, so every reachable state can be exercised without
# staging real files, real crashes, or a real codex job. The I/O that
# gathers `observed` is a separate, thin layer; keeping them apart is what
# makes the state space testable at all.
#
# An ORDERED PROCEDURE, not a table: predicates are evaluated top-down and
# the first match wins, so disjointness is structural rather than promised.
# A table of independent rows is what this was first written as, and its
# rows overlapped -- "the review matches the staged review" could hold at
# the same time as "draft staging is missing" and as "the draft matches
# neither preimage nor postimage", with different rows prescribing
# different actions for one state.
# ---------------------------------------------------------------------------

TXN_PROCEED = "proceed"                          # nothing in flight
TXN_ABORTED_PREPARE = "txn-aborted-prepare"      # staging without an intent
TXN_COMMITTED_CLEANED = "txn-committed-cleaned"  # committed, cleanup only
TXN_ROLLED_FORWARD_TAIL = "txn-rolled-forward-tail"  # both renamed already
TXN_ROLL_FORWARD_DRAFT = "txn-rolled-forward"    # review renamed; draft to go
TXN_ROLL_FORWARD_BOTH = "txn-rolled-forward"     # neither renamed yet
TXN_PREIMAGE_DIVERGED = "txn-preimage-diverged"  # CAS refusal
TXN_STAGING_LOST = "txn-staging-lost"            # unrecoverable, publish nothing
TXN_INTENT_INVALID = "txn-intent-invalid"        # intent present but untrustworthy

TXN_SCHEMA_VERSION = 1
_TXN_PHASES = ("prepared", "committed")
_TXN_REQUIRED_FIELDS = (
    "txn_id", "phase", "round_label", "pre_edit_draft_sha1", "pre_edit_draft_token",
    "staged_draft_sha256", "staged_review_sha256", "review_preimage",
)
# The producer's own shape: `"final" if n == max_fix_rounds + 1 else str(n)`
# (_matched_review_round_label). A STRING, never an int and never None.
_TXN_ROUND_LABEL_RE = re.compile(r"^([1-9][0-9]*|final)$")

# A hash slot whose file EXISTS but could not be read. Distinct from None,
# which means "absent": absence is an observation, a read failure is the
# ABSENCE OF ONE, and only the first may license deleting anything.
TXN_UNREADABLE = "<unreadable>"
TXN_UNOBSERVABLE = "txn-unobservable"


def txn_intent_path(txn_dir: Path, seg: str) -> Path:
    """ONE path per segment, so discovery is deterministic. A replacement
    driver finds the intent by this path and READS its txn_id from it -- the
    id never has to be recomputed, which is why it does not need to be
    derivable."""
    return txn_dir / f"{seg}.intent.json"


def staged_paths(txn_dir: Path, seg: str, round_label: str) -> dict:
    return {
        "draft": txn_dir / f"{seg}.{round_label}.staged.draft.json",
        "review": txn_dir / f"{seg}.{round_label}.staged.review.json",
    }


def write_txn_intent(txn_dir: Path, seg: str, intent: dict) -> bool:
    """Publish the pre-commit intent durably, in `prepared` phase.

    REFUSES to write an intent this module's own recovery could not later
    interpret. Writing one would be strictly worse than not starting the
    transaction: recovery would find a record it must classify as invalid,
    and an invalid intent is deliberately never cleaned up, so it would sit
    there blocking the segment until a human removed it."""
    if intent.get("phase") != "prepared":
        print(
            f"segment_dispatch_driver.py: refusing to write an intent for {seg!r} in phase "
            f"{intent.get('phase')!r}: an intent is always published as 'prepared'",
            file=sys.stderr,
        )
        return False
    if not _is_valid_intent(intent):
        print(
            f"segment_dispatch_driver.py: refusing to write an intent for {seg!r} that this "
            f"module's own recovery would classify as invalid",
            file=sys.stderr,
        )
        return False
    return _atomic_write_json(txn_intent_path(txn_dir, seg), intent)


def commit_txn_intent(txn_dir: Path, seg: str) -> bool:
    """Flip a durable `prepared` intent to `committed`.

    Reads what is on disk and rewrites it rather than taking the caller's
    copy: the caller's mapping may be stale, and the whole point of the phase
    flip is that it describes the state the DISK is in. Refuses on anything it
    cannot interpret, and is idempotent -- committing an already-committed
    intent is a no-op success, because recovery replays this."""
    path = txn_intent_path(txn_dir, seg)
    status, parsed = _read_counter(path)
    if status != "ok" or not _is_valid_intent(parsed):
        print(
            f"segment_dispatch_driver.py: refusing to commit the intent for {seg!r}: "
            f"it is absent or cannot be interpreted",
            file=sys.stderr,
        )
        return False
    if parsed.get("phase") == "committed":
        # ALREADY VISIBLE IS NOT ALREADY DURABLE -- the same post-os.replace()
        # hazard charge_txn_failure() already handles, and it did not
        # propagate here on its own. A first commit can publish `committed`
        # and still report failure because the directory fsync failed, so a
        # retry that returns True on sight of the phase claims a durability
        # nothing confirmed.
        return _fsync_dir(txn_dir)
    parsed["phase"] = "committed"
    return _atomic_write_json(path, parsed)


def publish_txn(txn_dir: Path, seg: str, segments_dir: Path, decision: dict,
                scripts_dir: Path = SCRIPTS_DIR) -> bool:
    """Perform the renames a recovery decision prescribes. Nothing else.

    THIS IS THE ONLY FUNCTION IN THE TRANSACTION LAYER THAT TOUCHES WORK THE
    USER OWNS, which is why it does as little as possible: it renames what
    `decision["publish"]` names, in the order given, and neither decides nor
    cleans up. The decision comes from classify_txn_recovery(), so every
    refusal has already happened before control reaches here.

    REVIEW BEFORE DRAFT, AND DURABLY SO. review_ready.py compares a candidate
    review against the CURRENT canonical draft, so "old draft + new review" is
    a SHA-consistent intermediate state and "new draft + old review" is not.
    Ordering the two renames is not enough to guarantee that: without a
    barrier between them a crash can persist the draft rename and lose the
    review rename, which materialises precisely the inconsistent pair the
    order exists to avoid. Each rename is therefore flushed before the next
    begins -- the same LAST / ONLY-IF / DURABLY-BEFORE distinction cleanup_txn
    needed, applied where the cost of getting it wrong is the user's text.

    ONE REVALIDATION PER RENAME, NOT ONE PER PUBLICATION. An earlier version
    revalidated once and then performed both renames, which left the widest
    window of all between the two: it spans a directory fsync, so the canonical
    draft's observation was already stale by the time the draft was replaced.
    Every iteration below re-observes, so the barrier is no longer inside any
    artifact's exposure window.

    Refuses (returning False) rather than continuing if any staged source is
    missing or any rename fails, leaving whatever has already been published
    in place: it is a consistent prefix by construction, and the recovery
    procedure recognises it on the next pass.

    WHAT THIS STILL DOES NOT CLOSE, stated because an overstated mitigation
    reads as caution and then never gets attacked:

    * The final gap between the last read of a file and the os.replace that
      destroys it cannot be closed at all. POSIX has no compare-and-rename,
      there is no rename-if-fd, and RENAME_EXCHANGE/RENAME_NOREPLACE are Linux
      only and unexposed in Python. What the gap COSTS has changed -- the pin
      below preserves the replaced bytes and refuses rather than reporting
      success over them -- but the gap itself is still there, and it is not
      bounded in wall clock because the thread can be preempted inside it.
      The pin's own coverage is asymmetric and the weaker half is the one to
      remember: an IN-PLACE writer shares the pinned inode and is always
      caught; a RENAME-BASED writer that swaps the name between the link and
      the replace is not caught and cannot be. Only that interleaving is still
      lost silently.
    * A refusal AFTER the review rename leaves a canonical review describing a
      draft that was never published. That is a THIRD state, distinct from both
      pairs the ordering argument reasons about, and the next recovery answers
      preimage-diverged with cleanup, which DISCARDS the staged fixed draft --
      the round's work must be redone, not merely resumed. It is still strictly
      better than the alternative, because a review is regenerable and the
      user's destroyed text is not.
    * The real-world writer this exists to survive is not another driver -- the
      project lease already excludes that. It is the fix step itself: on
      --fix-mode=handoff, a Claude agent told to rewrite
      segments/<seg>.draft.json from a separate process holding no lock (see
      callFix() in mass-translate-wf.template.js), plus a human editor and a
      sync daemon.

      A per-segment lease DOES now reach some of them, and saying "no advisory
      lock reaches any of them" would be wrong: segment_lease() takes
      codex_job.py's OWN .codex_job.<seg>.lock by exact path, so every
      in-product writer of a canonical draft -- both codex_job.py promote
      paths and this publication -- is mutually excluded. What it does not
      reach is precisely the list above, none of which takes a lock at all.
      That is the population the pin exists for, and it is also why the pin
      is not redundant with the lease."""
    order = list(decision.get("publish") or [])
    if not order:
        return True

    # THE DECISION MUST CARRY ITS OWN PREMISE, AND ONLY classify CAN MINT ONE.
    # A caller cannot hand-build a decision that publishes, because it cannot
    # produce a binding that will match the one re-derived below.
    expected = decision.get("binding")
    if expected is None:
        print(
            f"segment_dispatch_driver.py: refusing to publish for {seg!r}: the decision "
            f"carries no transaction identity, so it did not come from "
            f"classify_txn_recovery()",
            file=sys.stderr,
        )
        return False

    canonical = {
        "draft": segments_dir / f"{seg}.draft.json",
        "review": segments_dir / f"{seg}.review.json",
    }
    observed_key = {
        "draft": "canonical_draft_sha256",
        "review": "canonical_review_sha256",
    }
    staged_key = {
        "draft": "staged_draft_sha256",
        "review": "staged_review_sha256",
    }

    for index, what in enumerate(order):
        remaining = order[index:]
        observed = gather_txn_observed(seg, txn_dir, segments_dir, scripts_dir)
        fresh = classify_txn_recovery(observed)

        # COMPARED AGAINST THE REMAINING TAIL, NOT THE WHOLE LIST. After the
        # review rename the classifier legitimately answers ["draft"], because
        # that is exactly the roll-forward state it is built to recognise;
        # comparing against the original list would refuse the second half of
        # every publication this function performs.
        if (fresh.get("publish") or []) != remaining:
            print(
                f"segment_dispatch_driver.py: refusing to publish {what} for {seg!r}: the "
                f"on-disk state changed since it was classified (expected {remaining}, now "
                f"{fresh.get('publish') or []} / {fresh.get('outcome')})",
                file=sys.stderr,
            )
            return False

        # THE PART THE PUBLISH LIST CANNOT DO. Only three lists are ever
        # emitted, so shape alone admits a different, fully valid transaction
        # that happens to need the same work.
        if fresh.get("binding") != expected:
            print(
                f"segment_dispatch_driver.py: refusing to publish {what} for {seg!r}: the "
                f"transaction on disk is no longer the one this decision was made for",
                file=sys.stderr,
            )
            return False

        # The round label comes from the intent THIS pass validated. A separate
        # _read_counter here -- which is what the previous version did -- renames
        # the staging named by one read while having validated the staging named
        # by another, and that second read was the widest window in the function.
        #
        # A matching non-None binding already implies _is_valid_intent(intent),
        # since txn_binding() returns None for anything else. That is a chain of
        # three inferences across two functions, so it is asserted here rather
        # than relied upon: if the chain is ever broken by a change elsewhere,
        # this refuses instead of indexing None.
        intent = observed.get("intent")
        if not _is_valid_intent(intent):
            print(
                f"segment_dispatch_driver.py: refusing to publish {what} for {seg!r}: the "
                f"intent that carried this transaction's identity is no longer interpretable",
                file=sys.stderr,
            )
            return False
        staged = staged_paths(txn_dir, seg, intent["round_label"])

        if what not in staged:
            print(
                f"segment_dispatch_driver.py: refusing to publish {what!r} for {seg!r}: "
                f"unknown artifact",
                file=sys.stderr,
            )
            return False
        source = staged[what]
        if not source.is_file():
            print(
                f"segment_dispatch_driver.py: refusing to publish {what} for {seg!r}: "
                f"{source} is missing",
                file=sys.stderr,
            )
            return False

        # A SYMLINK IS NOT A REGULAR FILE AND os.replace WOULD REPLACE THE LINK.
        # The confirm below hashes what the path RESOLVES to, so without this
        # the two would be talking about different inodes: we would approve the
        # target's bytes and then destroy the link. Refuse instead of guessing
        # which the operator meant.
        destination = canonical[what]
        try:
            if destination.is_symlink():
                print(
                    f"segment_dispatch_driver.py: refusing to publish {what} for {seg!r}: "
                    f"{destination} is a symlink, and replacing it would not replace the "
                    f"file that was checked",
                    file=sys.stderr,
                )
                return False
        except OSError as exc:
            print(
                f"segment_dispatch_driver.py: refusing to publish {what} for {seg!r}: could "
                f"not stat {destination}: {exc}",
                file=sys.stderr,
            )
            return False

        # LAST THING READ IS THE FILE THE RENAME DESTROYS. gather reads five
        # artifacts in a fixed order, so the canonical draft -- read first and
        # renamed last -- carries the oldest observation of all. This is not a
        # second copy of the recovery rules: it asserts that the premise those
        # rules just consumed is still true for this one file, and it can only
        # refuse, never authorise. The staged source is confirmed too, so bytes
        # nothing validated cannot reach a canonical name.
        if _sha256_of(source) != observed.get(staged_key[what]):
            print(
                f"segment_dispatch_driver.py: refusing to publish {what} for {seg!r}: the "
                f"staged bytes changed after they were validated",
                file=sys.stderr,
            )
            return False
        expected_destination = observed.get(observed_key[what])
        if _sha256_of(destination) != expected_destination:
            print(
                f"segment_dispatch_driver.py: refusing to publish {what} for {seg!r}: "
                f"{destination} changed after it was classified; publishing would destroy "
                f"the newer content",
                file=sys.stderr,
            )
            return False

        # NON-DESTRUCTIVE, WHICH IS NOT THE SAME AS ATOMIC, AND THE DIFFERENCE
        # IS THE WHOLE POINT. The check above and the rename below are two
        # operations resolving the same NAME twice, and POSIX offers nothing
        # that fuses them: renameat2(RENAME_EXCHANGE) is Linux-only and not
        # exposed in Python. So the window cannot be closed -- but what happens
        # inside it can be changed from "the newer bytes are gone" to "the newer
        # bytes are on disk under another name and we refused".
        #
        # os.link() pins the INODE currently at the name, and the two writer
        # shapes are covered DIFFERENTLY. Stating which is which, because the
        # weaker half is the one a reader will otherwise assume away:
        #
        #   IN-PLACE writer -- an editor, a shell redirect, the handoff fix
        #   step's rewrite of the canonical draft. The pin SHARES that inode,
        #   so the racer's bytes appear in the pin whenever they land, before
        #   or after the link, and the hash below always catches them.
        #
        #   RENAME-BASED writer -- every publisher in this codebase, this one
        #   included. The swap installs a NEW inode at the name. A swap before
        #   the link is caught, because the pin then holds the racer's inode. A
        #   swap between the link and the rename is NOT caught and cannot be:
        #   those are two adjacent syscalls with no I/O between them, and
        #   nothing portable fuses them.
        #
        # Whenever the mismatch is detectable at all, the bytes that were
        # replaced survive at `pinned` and we refuse, so the operator finds out
        # instead of losing the content silently.
        #
        # THE LEASE IS THE OTHER HALF, and it covers more than this does: every
        # in-product writer of a canonical draft takes the same
        # `.codex_job.<seg>.lock` (both codex_job.py promote paths and this
        # publication). What it does NOT cover is a writer that takes no lock --
        # a human with an editor, or the handoff fix step in
        # mass-translate-wf.template.js's callFix(), which rewrites the canonical
        # draft directly. This pin is what stands between that writer and
        # unrecoverable loss.
        pinned = None
        if expected_destination is not None:
            pinned = destination.with_name(
                f"{destination.name}.superseded-{intent['txn_id']}")
            try:
                os.link(destination, pinned)
            except FileExistsError:
                # Our own earlier attempt at THIS transaction, or somebody
                # else's evidence. Same preimage -> it is ours and carries
                # nothing new. Different -> it is evidence, and overwriting
                # evidence is the one thing this block exists to prevent.
                if _sha256_of(pinned) != expected_destination:
                    print(
                        f"segment_dispatch_driver.py: refusing to publish {what} for "
                        f"{seg!r}: {pinned} already holds superseded content that is not "
                        f"this transaction's preimage; a human has to look at it first",
                        file=sys.stderr,
                    )
                    return False
            except OSError as exc:
                print(
                    f"segment_dispatch_driver.py: refusing to publish {what} for {seg!r}: "
                    f"could not preserve {destination} before replacing it: {exc}",
                    file=sys.stderr,
                )
                return False

        try:
            os.replace(source, destination)
        except OSError as exc:
            _discard_superseded_pin(pinned)
            print(
                f"segment_dispatch_driver.py: could not publish {what} for {seg!r}: {exc}",
                file=sys.stderr,
            )
            return False

        if pinned is not None:
            if _sha256_of(pinned) != expected_destination:
                print(
                    f"segment_dispatch_driver.py: published {what} for {seg!r} over content "
                    f"that changed after it was checked; the replaced bytes are preserved at "
                    f"{pinned} and nothing was lost, but this transaction is refused so the "
                    f"race is not silent",
                    file=sys.stderr,
                )
                return False
            # Byte-identical to the preimage the intent already records by
            # digest, so it is a duplicate rather than evidence.
            _discard_superseded_pin(pinned)
        # Barrier BETWEEN the renames, not merely after both.
        if not _fsync_dir(segments_dir):
            print(
                f"segment_dispatch_driver.py: published {what} for {seg!r} but could not make "
                f"it durable; stopping before the next rename",
                file=sys.stderr,
            )
            return False
    return True


def cleanup_txn(txn_dir: Path, seg: str, round_label: str) -> bool:
    """Remove the staging files and then the intent, in that order.

    ORDER MATTERS AND IS NOT ARBITRARY: the intent is the record that a
    transaction was in flight, so it is removed LAST. Crashing after the
    staging is gone but before the intent leaves a state recovery recognises
    (intent present, staging missing) rather than orphan staging nobody can
    explain. Touches ONLY the transaction directory -- never a canonical
    draft or review.

    Absent files are success: this runs on a replay path."""
    def _remove(path: Path) -> bool:
        try:
            path.unlink()
        except FileNotFoundError:
            return True
        except OSError as exc:
            print(
                f"segment_dispatch_driver.py: warning: could not remove {path}: {exc}",
                file=sys.stderr,
            )
            return False
        return True

    # THE DURABLE INTENT OWNS THE ROUND HERE TOO. gather_txn_observed() already
    # treats it as the sole authority; cleanup going back to the caller's label
    # recreates the orphan state this helper exists to prevent -- with a
    # round-1 intent and round-1 staging, a caller passing "2" removes nothing
    # (absent files count as removed), deletes the intent, and leaves the real
    # staging behind. A replacement driver on a different round is exactly the
    # case that produces it.
    intent_path = txn_intent_path(txn_dir, seg)
    status, parsed = _read_counter(intent_path)
    if status == "ok" and _is_valid_intent(parsed):
        label = parsed["round_label"]
    elif status == "absent":
        label = round_label  # aborted-prepare: no intent to disagree with
    else:
        print(
            f"segment_dispatch_driver.py: refusing to clean up {seg!r}: an intent exists but "
            f"cannot be interpreted, so its staging cannot be identified",
            file=sys.stderr,
        )
        return False

    paths = staged_paths(txn_dir, seg, label)
    staging_gone = _remove(paths["draft"])
    # Both are attempted even if the first failed -- removing what can be
    # removed is useful -- but the INTENT is gated on both succeeding.
    staging_gone = _remove(paths["review"]) and staging_gone

    if not staging_gone:
        # LAST is not the same as ONLY-IF, and only the second gives the
        # invariant. Deleting the intent here would leave staging on disk with
        # no durable record explaining it -- precisely the orphan state the
        # ordering exists to avoid, reached through the failure path instead
        # of through a crash. Keep the intent so recovery can still classify
        # this, and still flush whatever did change.
        _fsync_dir(txn_dir)
        return False

    # ...and ONLY-IF is not the same as DURABLY-BEFORE, which is the third and
    # last form this ordering needs. Unlinking the intent and flushing once at
    # the end makes the order process-visible but not crash-durable: nothing
    # persists the staging removals ahead of the intent removal, so a crash
    # can preserve the intent deletion while losing one or both staging
    # deletions -- orphan staging with no record, arrived at through the
    # durability layer this time. Barrier FIRST, so the staging removals are
    # on disk before the record explaining them can go.
    if not _fsync_dir(txn_dir):
        print(
            f"segment_dispatch_driver.py: refusing to remove the intent for {seg!r}: the "
            f"staging removals could not be made durable first",
            file=sys.stderr,
        )
        return False

    ok = _remove(intent_path)
    return _fsync_dir(txn_dir) and ok


def _discard_superseded_pin(pinned):
    """Remove a hard link that existed only to make a rename non-destructive.

    Best-effort, and deliberately quiet: the pin is byte-identical to a preimage
    the intent already records by digest, so failing to remove it leaves litter
    and never a correctness problem. It is NEVER called on a pin that turned out
    to hold content the preimage check did not approve -- that one is evidence
    of a concurrent writer and is kept on purpose."""
    if pinned is None:
        return
    try:
        os.unlink(pinned)
    except OSError:
        pass


def _entry_is_absent(path: Path) -> bool:
    """True only when the namespace positively says there is no entry at `path`.

    os.lstat, never Path.exists(). exists() RESOLVES the link, so it cannot tell
    a missing file from a self-referential symlink (ELOOP), from a permission
    error on the parent directory, or from any other lookup failure -- it
    answers False for every one of them. Callers use this answer to decide
    whether it is safe to PROCEED, so a lookup that merely failed must read as
    "present". Only ENOENT is an absence."""
    try:
        os.lstat(path)
        return False
    except FileNotFoundError:
        return True
    except OSError as exc:
        print(
            f"segment_dispatch_driver.py: warning: could not stat {path}: {exc}; "
            f"treating the entry as present",
            file=sys.stderr,
        )
        return False


def _sha256_of(path: Path):
    """sha256 of a file's RAW bytes, None when it is absent, and
    TXN_UNREADABLE when it is present but could not be read.

    Raw bytes, deliberately: the comparison must be sensitive to key order and
    formatting, so that a competing rewrite -- even a purely cosmetic one --
    invalidates a stale transaction exactly like any other concurrent
    publication. An unreadable-but-present file returns the sentinel rather
    than raising: the classifier's refusal paths are the correct response, and
    this gather step must not crash the driver mid-batch. It is a SENTINEL and
    not None because absence and unreadability are different observations --
    absence licenses deleting staging, unreadability licenses nothing -- and
    callers that compare two of these must reject the sentinel explicitly,
    since it compares equal to itself (see premise_is_observable()).

    "Absent" means NO DIRECTORY ENTRY, and that is established with lexists()
    rather than with the read's own errno -- a dangling symlink fails the read
    with ENOENT while still being an entry somebody owns."""
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except FileNotFoundError:
        # ENOENT IS NOT "NO DIRECTORY ENTRY". Reading through a DANGLING SYMLINK
        # raises FileNotFoundError while os.lstat() on the link itself succeeds:
        # an entry exists and something owns it. Reporting that as None would be
        # reporting the one observation that licenses REPLACING the path --
        # os.replace() needs write permission on the DIRECTORY, not on the entry,
        # so the link would be destroyed exactly like a readable file would.
        # lexists() asks about the LINK and never about its target.
        if os.path.lexists(path):
            print(
                f"segment_dispatch_driver.py: warning: {path} is a dangling symlink; "
                f"treating it as unreadable rather than as absent",
                file=sys.stderr,
            )
            return TXN_UNREADABLE
        return None
    except OSError as exc:
        print(
            f"segment_dispatch_driver.py: warning: could not read {path} to hash it: {exc}",
            file=sys.stderr,
        )
        return TXN_UNREADABLE


_TXN_INTENT_UNREADABLE = "<unreadable-intent>"


def gather_txn_observed(seg: str, txn_dir: Path, segments_dir: Path,
                        scripts_dir: Path = SCRIPTS_DIR, round_label=None) -> dict:
    """Collect the on-disk state classify_txn_recovery() decides from.

    READ-ONLY BY CONSTRUCTION: this function opens files and hashes them and
    does nothing else. It is split from the classifier so the decision can be
    tested over the whole state space without a filesystem, and split from
    publication so that reading can never be the thing that mutates.

    An intent file that exists but cannot be decoded is reported as a
    NON-None sentinel, never as None. None means "absent", which licenses
    deleting staging; an unreadable intent must instead reach the classifier's
    invalid branch, which refuses without deleting anything.

    The draft's CONTENT sha1 comes from current_draft_sha1(), i.e. from
    draft_sha1.py's own draft_content_sha1() -- the module that owns this
    operation -- never from a hash computed here. A missing or malformed draft
    yields None, which cannot match any recorded preimage, so the classifier
    refuses rather than this layer deciding anything."""
    intent_path = txn_dir / f"{seg}.intent.json"
    status, parsed = _read_counter(intent_path)
    if status == "absent":
        intent = None
    elif status == "corrupt":
        intent = _TXN_INTENT_UNREADABLE
    else:
        intent = parsed

    # THE DURABLE INTENT IS THE SOLE AUTHORITY ON THE ROUND ONCE IT EXISTS.
    # A caller-supplied label that disagrees would hash one round's staging
    # while _is_valid_intent() blesses another's, so real, correct staging
    # reads as missing and the classifier answers STAGING_LOST -- which
    # licenses deleting it. The argument survives only for the no-intent case,
    # where there is nothing to disagree with.
    if isinstance(intent, dict):
        durable = intent.get("round_label")
        if round_label is not None and round_label != durable:
            return {"intent": _TXN_INTENT_UNREADABLE,
                    "staged_draft_sha256": None, "staged_review_sha256": None,
                    "canonical_draft_sha256": None, "canonical_review_sha256": None,
                    "canonical_draft_content_sha1": None, "canonical_draft_token": None}
        label = durable
    else:
        label = round_label

    staged_draft = txn_dir / f"{seg}.{label}.staged.draft.json"
    staged_review = txn_dir / f"{seg}.{label}.staged.review.json"

    draft_path = segments_dir / f"{seg}.draft.json"
    draft_sha256, content_sha1, draft_token = _draft_observation(
        seg, draft_path, segments_dir, scripts_dir)

    return {
        "intent": intent,
        "staged_draft_sha256": _sha256_of(staged_draft),
        "staged_review_sha256": _sha256_of(staged_review),
        "canonical_draft_sha256": draft_sha256,
        "canonical_review_sha256": _sha256_of(segments_dir / f"{seg}.review.json"),
        "canonical_draft_content_sha1": content_sha1,
        "canonical_draft_token": draft_token,
    }


def _draft_observation(seg: str, draft_path: Path, segments_dir: Path, scripts_dir: Path):
    """The draft's (raw sha256, content sha1, dispatch_token) from ONE snapshot.

    All three feed the CAS, so reading the file three times lets a competing
    rewrite between reads synthesise an observation that never existed on disk
    -- `preimage_intact` computed from the OLD content while the newer bytes
    sit there, and the classifier then authorises publication over exactly the
    edit the CAS exists to protect.

    The token is taken from the SAME buffer as the raw hash, and the authority
    call (draft_sha1.py owns content hashing; it takes a path, so it cannot be
    handed a buffer) is BRACKETED by two raw reads. If the bytes moved at any
    point across that window the observation is discarded as unreadable, which
    the classifier turns into a refusal that touches nothing. Detecting the
    race is enough here; winning it is not required."""
    try:
        before = draft_path.read_bytes()
    except FileNotFoundError:
        return (None, None, None)
    except OSError:
        return (TXN_UNREADABLE, None, None)

    content_sha1 = None
    try:
        content_sha1 = current_draft_sha1(seg, segments_dir, scripts_dir)
    except DriverError:
        content_sha1 = None

    try:
        after = draft_path.read_bytes()
    except OSError:
        return (TXN_UNREADABLE, None, None)
    if after != before:
        print(
            f"segment_dispatch_driver.py: the draft for {seg!r} changed while it was being "
            f"observed; refusing to decide from a composite snapshot",
            file=sys.stderr,
        )
        return (TXN_UNREADABLE, None, None)

    token = None
    try:
        parsed = json.loads(before.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        parsed = None
    if isinstance(parsed, dict):
        raw_token = parsed.get("dispatch_token")
        token = raw_token if isinstance(raw_token, str) else None

    return (hashlib.sha256(before).hexdigest(), content_sha1, token)


def txn_binding(observed: dict):
    """The identity of the transaction `observed` describes, or None.

    WHY A DECISION MUST CARRY ITS OWN PREMISE. classify_txn_recovery emits only
    three publish lists -- (), ("draft",) and ("review", "draft") -- so the list
    identifies the SHAPE of the work and nothing else. Two entirely different
    transactions, different round, different staged bytes, produce the same
    list; comparing lists therefore cannot tell "the state is unchanged" from
    "the state was replaced by another valid transaction of the same shape".
    Publishing then applies one transaction's decision to another's staging.

    Derived from `observed` and never from an argument, so what a decision
    carries is always the identity the rules were actually applied to. Pure."""
    intent = observed.get("intent")
    if not _is_valid_intent(intent):
        return None
    return (intent["txn_id"], intent["round_label"], intent["phase"],
            intent["staged_draft_sha256"], intent["staged_review_sha256"])


def classify_txn_recovery(observed: dict) -> dict:
    """Decide what to do about a segment's transaction, from observed state.

    `observed` keys:
      intent                 the intent mapping, or None when absent
      staged_draft_sha256    sha256 of the staged draft on disk, or None
      staged_review_sha256   sha256 of the staged review on disk, or None
      canonical_draft_sha256 sha256 of the canonical draft bytes, or None
      canonical_review_sha256 sha256 of the canonical review bytes, or None
      canonical_draft_content_sha1  the draft's CONTENT sha1 (token-excluded)
      canonical_draft_token  the canonical draft's dispatch_token, or None

    Returns {"outcome": <one of the constants>, "publish": [...],
             "commit_intent": bool, "cleanup": bool, "binding": <tuple|None>}.
    `publish` lists which artifacts still need renaming, in the order they
    must be renamed -- review first, always, because review_ready.py compares
    a candidate review against the CURRENT canonical draft, so "old draft +
    new review" is a SHA-consistent intermediate state and "new draft + old
    review" is not.

    `binding` is txn_binding(observed) -- the identity of the transaction these
    rules were applied to. NOTHING HERE DECIDES DIFFERENTLY BECAUSE OF IT: no
    predicate reads it, no ordering depends on it, no outcome changes. The
    classifier simply stops discarding a premise it had already read, so that
    publish_txn can assert at the point of use that it is acting on the same
    transaction this decision described. Every return path carries it,
    including the unobservable one below, so that a binding-less decision is
    unspellable rather than merely discouraged.
    """
    binding = txn_binding(observed)

    def decision(outcome, publish=(), commit_intent=False, cleanup=False):
        return {"outcome": outcome, "publish": list(publish),
                "commit_intent": commit_intent, "cleanup": cleanup,
                "binding": binding}

    intent = observed.get("intent")
    # A TRANSIENT READ FAILURE IS NOT AN OBSERVATION. Every decision below
    # reasons about what the artifacts ARE; if any of them could not be read,
    # the premise is missing, and answering STAGING_LOST or DIVERGED from a
    # missing premise deletes evidence over a permission blip. Refuse without
    # touching anything and let the next invocation look again.
    if any(observed.get(k) == TXN_UNREADABLE for k in (
            "staged_draft_sha256", "staged_review_sha256",
            "canonical_draft_sha256", "canonical_review_sha256")):
        # Routed through decision() like every other path. Built as a dict
        # literal it would be the one return that carries no `binding`, and a
        # single binding-less decision is all publish_txn's identity check
        # needs to be bypassable.
        return decision(TXN_UNOBSERVABLE)
    sd = observed.get("staged_draft_sha256")
    sr = observed.get("staged_review_sha256")
    cd = observed.get("canonical_draft_sha256")
    cr = observed.get("canonical_review_sha256")

    # --- 0. no intent -------------------------------------------------------
    if intent is None:
        if sd is not None or sr is not None:
            # Reachable BY CONSTRUCTION: staging is written before the intent
            # is made durable, so a crash in between leaves exactly this.
            return decision(TXN_ABORTED_PREPARE, cleanup=True)
        return decision(TXN_PROCEED)

    # --- 0b. an intent that exists but cannot be trusted ---------------------
    # ABSENT and INVALID are different states here for the same reason they are
    # for the counters, and getting it wrong is worse on this path: treating a
    # non-mapping intent as absence returns cleanup=True and DELETES the only
    # recovery evidence, while letting any mapping through means an unknown
    # `phase` or a missing `txn_schema` can reach a publish decision. Refuse
    # with neither publication NOR cleanup -- an operator can inspect what is
    # left, which is impossible once it has been deleted.
    if not _is_valid_intent(intent):
        return decision(TXN_INTENT_INVALID)

    want_draft = intent.get("staged_draft_sha256")
    want_review = intent.get("staged_review_sha256")

    # --- 1. already committed ----------------------------------------------
    if intent.get("phase") == "committed":
        return decision(TXN_COMMITTED_CLEANED, cleanup=True)

    # --- 2. both destinations already renamed ------------------------------
    # Staging is legitimately GONE here; treating that as staging-loss (which
    # an earlier table did) would refuse a transaction that in fact succeeded.
    if cd is not None and cr is not None and cd == want_draft and cr == want_review:
        return decision(TXN_ROLLED_FORWARD_TAIL, commit_intent=True, cleanup=True)

    preimage_intact = (
        observed.get("canonical_draft_content_sha1") == intent.get("pre_edit_draft_sha1")
        and observed.get("canonical_draft_token") == intent.get("pre_edit_draft_token")
    )
    review_is_preimage = _review_matches_preimage(cr, intent.get("review_preimage"))
    review_is_postimage = cr is not None and cr == want_review

    # --- 3. review renamed, draft still to go ------------------------------
    if (review_is_postimage and preimage_intact
            and sd is not None and sd == want_draft):
        return decision(TXN_ROLL_FORWARD_DRAFT, publish=("draft",),
                        commit_intent=True, cleanup=True)

    # --- 4. nothing renamed yet --------------------------------------------
    if (review_is_preimage and preimage_intact
            and sd is not None and sd == want_draft
            and sr is not None and sr == want_review):
        return decision(TXN_ROLL_FORWARD_BOTH, publish=("review", "draft"),
                        commit_intent=True, cleanup=True)

    # --- 5. CAS refusal -----------------------------------------------------
    # ORDERED ABOVE step 6 deliberately: a diverged preimage must never be
    # read as mere staging loss, or a roll-forward could overwrite an
    # unrelated newer draft. The `review_is_postimage` disjunct is required --
    # without it, "this transaction already renamed the review but its draft
    # staging is gone" fails step 3 on the missing staging and lands here,
    # reporting a divergence THIS transaction itself produced as somebody
    # else's.
    if not preimage_intact or not (review_is_preimage or review_is_postimage):
        return decision(TXN_PREIMAGE_DIVERGED, cleanup=True)

    # --- 6. staging unusable ------------------------------------------------
    return decision(TXN_STAGING_LOST, cleanup=True)


def _is_valid_intent(intent) -> TypeGuard[dict]:
    """A durable intent is trustworthy only when its whole shape is
    recognised: the right schema version, every required field present, and a
    KNOWN phase.

    Validating the phase against a closed set is the load-bearing part. This
    value arrives from JSON on disk, so "any mapping is fine" lets a truncated
    write, a hand-edit, or a record written by a FUTURE schema reach a publish
    decision -- and publishing on an intent this code does not understand is
    the one outcome no recovery path may risk.

    Typed as a TypeGuard (matching canon_adjudication_audit.py's own
    _risk_accepted()) so a True result narrows the checked value to dict for
    every caller's own follow-on field reads -- this is what a `_read_counter()`
    result's loosely-typed (status, parsed) pair cannot give the type checker
    on its own: `status == "ok"` and `parsed` are two independent variables
    to it, so narrowing has to come from checking `parsed` itself, through
    this function, not from checking `status`."""
    if not isinstance(intent, dict):
        return False
    # `!= TXN_SCHEMA_VERSION` alone is NOT enough, for the third time in this
    # file: `True == 1` in Python, so {"txn_schema": true} would pass as
    # schema 1 and, with matching hashes, authorise BOTH publications. The
    # same trap was fixed for the counters in _is_counter_int() and did not
    # propagate here just because the two sit side by side.
    schema = intent.get("txn_schema")
    if isinstance(schema, bool) or not isinstance(schema, int) or schema != TXN_SCHEMA_VERSION:
        return False
    if any(field not in intent for field in _TXN_REQUIRED_FIELDS):
        return False
    if intent.get("phase") not in _TXN_PHASES:
        return False
    if not isinstance(intent.get("txn_id"), str) or not intent["txn_id"]:
        return False
    # The comparison fields are matched against values derived from REAL files,
    # so a non-string here is the same class as the bool schema above: it
    # cannot legitimately equal a hash or a token, and typing it keeps the
    # refusal at the boundary instead of relying on a comparison to fail.
    for field in ("pre_edit_draft_sha1", "pre_edit_draft_token",
                  "staged_draft_sha256", "staged_review_sha256"):
        if not isinstance(intent.get(field), str) or not intent[field]:
            return False
    if not _is_valid_review_preimage(intent.get("review_preimage")):
        return False
    # round_label DERIVES THE STAGING PATHS, so an unusable one is not a
    # cosmetic gap: gathering would hash `<seg>.None.staged.*`, miss the real
    # staging, and the classifier would answer STAGING_LOST -- which licenses
    # cleanup and deletes the staging that was there all along. This file has
    # already closed exactly this failure class once, as the `rNone` defect
    # that review_dispatch_token() now refuses explicitly.
    label = intent.get("round_label")
    if not isinstance(label, str) or not _TXN_ROUND_LABEL_RE.match(label):
        return False
    return True


def _is_valid_review_preimage(preimage) -> bool:
    """`review_preimage` is a TAGGED UNION with exactly two recognised shapes:
    {"absent": true} or {"sha256": "<hex>"}. Validate it HERE rather than
    letting _review_matches_preimage() simply return False for anything else.

    The difference is not cosmetic and is the same fail-closed rule one level
    down: an unrecognised nested shape that merely fails to MATCH is reported
    as a preimage divergence, and divergence licenses cleanup -- so the caller
    would delete the staging and the intent, destroying the only durable
    evidence for a record whose shape this schema does not understand. An
    unrecognised shape must be INVALID (refuse, keep everything), never
    DIVERGED (refuse, delete)."""
    if not isinstance(preimage, dict):
        return False
    has_absent = "absent" in preimage
    has_sha = "sha256" in preimage
    if has_absent == has_sha:
        return False  # neither tag, or both at once
    if has_absent:
        return preimage["absent"] is True
    return isinstance(preimage["sha256"], str) and bool(preimage["sha256"])


def _review_matches_preimage(canonical_review_sha256, preimage) -> bool:
    """`review_preimage` is {"absent": true} or {"sha256": "<hex of the RAW
    canonical review bytes>"}. Raw bytes, not parsed-JSON equality: it is
    cheap and unambiguous, and it is deliberately sensitive to key order and
    formatting, because a competing rewrite -- even a purely cosmetic one --
    must invalidate a stale transaction exactly like any other concurrent
    publication."""
    if not isinstance(preimage, dict):
        return False
    if preimage.get("absent") is True:
        return canonical_review_sha256 is None
    want = preimage.get("sha256")
    return isinstance(want, str) and canonical_review_sha256 == want


def next_attempt_seq(txn_dir: Path, seg: str) -> int:
    """Allocate the next attempt number for `seg`, durably, BEFORE any intent
    is written. Monotonic and never reset. Returns 0 if the allocation could
    not be made durable -- callers must treat 0 as "do not start a
    transaction", because an id that is not durably reserved can be handed
    out twice."""
    path = txn_dir / f"{seg}.attempts"
    status, current = _read_counter(path)
    if status == "absent":
        # Nothing promised yet, so a fresh sequence is safe here and ONLY here.
        return 1 if _atomic_write_json(path, {"attempt_seq": 1}) else 0
    # Bound ONCE, then checked and reused from this one local -- not two
    # separate _attempt_seq_of(current) calls. A TypeGuard narrows the
    # EXPRESSION it is applied to; a second, syntactically fresh call is not
    # the same expression to the type checker (or, in general, provably the
    # same VALUE -- it only is here because _attempt_seq_of() is pure).
    seq = _attempt_seq_of(current)
    if status == "corrupt" or not _is_counter_int(seq):
        # UNPARSEABLE AND PARSEABLE-BUT-WRONG-SHAPE ARE THE SAME HAZARD. `{}`,
        # `[]`, `null` and {"attempt_seq": "bad"} all parse cleanly, so keying
        # this on json.loads() succeeding would let them fall through to a
        # fresh sequence -- reissuing an id the corrupted file had already
        # allocated. Only a mapping carrying a non-negative, non-bool integer
        # may continue the sequence; every other EXISTING shape refuses.
        print(
            f"segment_dispatch_driver.py: refusing to allocate an attempt id for {seg}: "
            f"{path} exists but does not carry a usable attempt_seq; restarting the "
            f"sequence would reuse an id already issued",
            file=sys.stderr,
        )
        return 0
    nxt = seq + 1
    if not _atomic_write_json(path, {"attempt_seq": nxt}):
        return 0
    return nxt


def make_txn_id(run_id: str, seg: str, round_label, attempt_seq: int) -> str:
    """`RUN_ID:seg:round_label:attempt_seq`.

    Deliberately NOT derived from the staged output hashes: two attempts that
    happen to produce identical bytes would then collide, and a truncated hash
    can collide outright -- either one breaks any rule keyed on transaction
    identity. Discovery does not need derivability, because the intent is
    found by its fixed PATH; a replacement driver READS this id rather than
    recomputing it."""
    return f"{run_id}:{seg}:{round_label}:{attempt_seq}"


def read_txn_failures(txn_dir: Path, seg: str):
    """`{"count": int, "charged": [txn_id, ...]}` normalised, or **None** when
    the file exists but cannot be trusted.

    None is not "zero". Callers must treat it as "refuse", never as an empty
    counter -- see _read_counter for why the two must stay distinguishable."""
    status, raw = _read_counter(txn_dir / f"{seg}.txn_failures")
    if status == "corrupt":
        return None
    if status == "absent":
        return {"count": 0, "charged": []}
    if not isinstance(raw, dict):
        return None
    count = raw.get("count")
    charged = raw.get("charged")
    if not _is_counter_int(count):
        return None
    if not isinstance(charged, list) or any(not isinstance(c, str) for c in charged):
        return None
    return {"count": count, "charged": list(charged)}


def txn_failures_exhausted(txn_dir: Path, seg: str, ceiling: int) -> bool:
    """True when a further transaction must NOT be started for `seg`.

    `>=`, not `>`: with `>` the knob named max_txn_failures_per_segment would
    permit failure number ceiling+1, which is not what the name says.

    An undecodable counter returns True -- fail CLOSED. Reading it as zero
    would re-authorise a segment whose refusals were already exhausted."""
    state = read_txn_failures(txn_dir, seg)
    if state is None:
        return True
    return state["count"] >= ceiling


def charge_txn_failure(txn_dir: Path, seg: str, txn_id: str, ceiling: int) -> Optional[dict]:
    """Charge ONE failure against `seg`, idempotently, keyed on `txn_id`.

    Exactly-once here is a property of IDEMPOTENCE, not of write ordering, and
    that distinction is the whole point: cleanup deletes staging and the
    intent, so with a plain counter, incrementing first and crashing before
    the deletes double-charges the same transaction, while deleting first and
    crashing before the increment never charges it at all. No ordering of two
    writes fixes that. Because the charge is a no-op when `txn_id` is already
    recorded, the caller may safely charge FIRST and delete afterwards, and a
    replayed recovery of the same transaction changes nothing.

    `charged` is truncated to the most recent `ceiling + 1` ids: that is all
    the history any decision here needs, and an evicted id cannot come back,
    because a segment has at most one live intent and no new transaction is
    started once the count has reached the ceiling.

    Returns the resulting state, or **None** when the charge could not be
    made durable -- either because the existing counter cannot be decoded or
    because the write failed. None must abort the cleanup that would otherwise
    follow: this function's whole contract is "charge first, then delete", so
    a caller that deletes the intent after an undurable charge loses the
    refusal entirely, which is precisely the double-/never-charge hazard the
    idempotence was introduced to remove. Returning the incremented in-memory
    state here regardless -- as an earlier version did, discarding
    _atomic_write_json()'s own False -- reports a durability the disk does not
    have."""
    state = read_txn_failures(txn_dir, seg)
    if state is None:
        print(
            f"segment_dispatch_driver.py: refusing to charge a transaction failure for {seg}: "
            f"the existing counter cannot be decoded",
            file=sys.stderr,
        )
        return None
    if txn_id in state["charged"]:
        # ALREADY CHARGED IS NOT AUTOMATICALLY ALREADY DURABLE. _atomic_write_json
        # can fail AFTER os.replace() has made the new counter visible -- the
        # directory fsync is the step that confirms the rename, and it is the
        # step most likely to fail on its own. A previous run can therefore have
        # left this txn_id visible in `charged` while returning failure. Simply
        # returning success here would tell the caller the refusal is recorded,
        # it would delete the live intent, and a later system crash could drop
        # the rename and lose the refusal permanently. So the replay path
        # re-establishes the durability barrier and still reports failure if it
        # cannot.
        if not _fsync_dir(txn_dir):
            return None
        return state
    state["count"] += 1
    state["charged"] = (state["charged"] + [txn_id])[-(max(ceiling, 0) + 1):]
    if not _atomic_write_json(txn_dir / f"{seg}.txn_failures", state):
        return None
    return state


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
    # codex round-4 MAJOR: was `< 0` ("non-negative"), accepting 0 despite
    # profile.schema.json's own `"minimum": 1` (profile.schema.json:494-497)
    # -- an invalid profile this driver should refuse, not silently accept.
    # A fresh segment at max_fix_rounds=0 dispatches translate and review
    # round "1" (derive_next_action()'s own "no review yet" branch always
    # starts at round_label "1", never "final", regardless of this value),
    # but round recognition (_matched_review_round_label()'s own `for n in
    # range(1, max_fix_rounds + 2)` loop) admits ONLY "final" when
    # max_fix_rounds=0 -- so round "1" can never be matched, every later
    # invocation treats that review as absent, and the segment re-reviews
    # forever until the generic loop-exhaustion fallback. The exact
    # unmatchable-round-token failure class this file already closed once
    # (the `rNone` defect fixed by review_dispatch_token()'s own explicit
    # refusal), recreated here through a profile value nothing rejects.
    if not isinstance(max_fix_rounds, int) or isinstance(max_fix_rounds, bool) or max_fix_rounds < 1:
        fatal(
            f"profile.yml at {profile_path}: engine.max_fix_rounds must be "
            f"a positive integer (minimum 1, per profile.schema.json), got {max_fix_rounds!r}",
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
        # Read by BOTH loaders from the same key with the same default, so
        # process_segment()'s loop cap and run()'s admission cannot disagree
        # about the bound -- see _optional_engine_int()'s own docstring.
        "max_rejected_candidates_per_round": _optional_engine_int(
            engine, "max_rejected_candidates_per_round",
            DEFAULT_MAX_REJECTED_CANDIDATES_PER_ROUND, 0, profile_path),
        "max_txn_failures_per_segment": _optional_engine_int(
            engine, "max_txn_failures_per_segment",
            DEFAULT_MAX_TXN_FAILURES_PER_SEGMENT, 0, profile_path),
        # #409 track B, the size fallback -- the ONE value effective_fix_mode()
        # actually reads at runtime (via ctx.translate_cfg). See
        # load_engine_config()'s own copy of this same key for why it is read
        # there too even though admission math never consumes it.
        "max_fixreview_context_tokens": _optional_engine_int(
            engine, "max_fixreview_context_tokens",
            DEFAULT_MAX_FIXREVIEW_CONTEXT_TOKENS, 0, profile_path),
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
    # Checked as a real precondition (isinstance, not just "in the dict"),
    # not merely to satisfy the type checker: verse_policy.get("mode") can
    # genuinely be None (or any other malformed value) for a hand-edited or
    # corrupted profile.yml, and passing that straight to
    # _VERSE_POLICY_INSTRUCTIONS.get(mode) -- a dict keyed by str -- would
    # let a non-str key reach a lookup that assumes str. fatal() is
    # declared -> NoReturn, so `mode` is narrowed to str for every line
    # below this guard.
    if not isinstance(mode, str) or mode not in _VERSE_POLICY_INSTRUCTIONS:
        fatal(f"unknown verse_policy.mode {mode!r} -- not one of {sorted(_VERSE_POLICY_INSTRUCTIONS)}", exit_code=2)
    text = _VERSE_POLICY_INSTRUCTIONS[mode]
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


def validate_run_id(name: str) -> "str | None":
    """Return an error string if `name` is not a safe RUN_ID, else None --
    same contract as the four siblings this mirrors (resume_setup.py,
    select_segments.py, skeptic_setup.py, backfill_resume_gate_ack.py, all
    named `validate_run_id`; `git grep validate_run_id` over the scripts
    directory finds all five). This one used to be a same-file-only bool
    predicate named `_looks_like_safe_run_id` -- a fifth spelling with a
    fifth contract, invisible to that grep and to a drift check built on
    it, which is exactly the blind spot this rename closes.

    codex round-4 MAJOR: the regex alone is NOT the authority's full
    contract. resume_setup.py's own validate_run_id() rejects `..`
    (both the whole value being exactly "." or ".." and any substring
    occurrence) IN ADDITION TO the shared `_RUN_ID_DIR_RE` pattern -- the
    regex's character class ([A-Za-z0-9._-]) admits dots freely, so it
    alone accepts "z..poison", "a..b", "trail..". This driver's own
    _RUN_ID_DIR_RE-only filter used to offer such a name as a candidate if
    one ever existed under runs_dir with an input.digest sibling
    (untrusted directory names -- not something this driver created, but
    not something it should assume either). The consequence is not that
    resume_setup.py would be fooled by it: _resume_from_candidates()
    validates the WHOLE resume_from_run_ids list before matching ANY of
    them and aborts on the first invalid entry -- so one unsafe-looking
    name offered ALONGSIDE a perfectly valid candidate would abort the
    entire resolve, discarding the valid one too, never reaching it.
    Mirrors the authority's own decision exactly (not a re-derivation of
    its regex alone) so a name this function offers can never be the one
    that trips that abort.

    The `isinstance` guard below is NOT decoration copied out of caution
    -- resume_setup.py's own validate_run_id() has it first, before its
    regex check, so a non-string probe (the drift check's own adversarial
    set includes one) hits that sibling's early return rather than its
    regex line. Without the identical guard here, the same probe reaches
    `_RUN_ID_DIR_RE.fullmatch(name)` directly and raises TypeError instead
    of returning an error string -- a crash, not a disagreement, but still
    not the same DECISION the sibling makes on that input."""
    if not isinstance(name, str) or not name:
        return "run id must be a non-empty string."
    if not _RUN_ID_DIR_RE.fullmatch(name):
        return f"run id must match {_RUN_ID_DIR_RE.pattern!r}; got {name!r}."
    if name in (".", ".."):
        return f"run id must not be '.' or '..'; got {name!r}."
    if ".." in name:
        return f"run id must not contain '..'; got {name!r}."
    return None


def _resumable_run_id_candidates(runs_dir: Path, durable_root: Path) -> list:
    """Candidate `resume_from_run_ids` entries for resolve_run_id() below
    (all offered together, in the order returned here), most recent first:
    every subdirectory of `runs_dir` that LOOKS like a run id
    (matches resume_setup.py's own RUN_ID_RE) AND carries an `input.digest`
    file (the one marker that distinguishes a real prior run directory from
    `ledger.d`, `workflows/`, or any other non-run-id entry `runs/` also
    holds), sorted lexicographically descending (== chronologically, since
    RUN_ID is the colon-free `YYYYMMDDTHHMMSSZ` form).

    codex round-4 MAJOR: NEVER capped. An earlier version capped this list
    at 5 (`_RESUMABLE_CANDIDATE_LIMIT`, "mirrors resume_setup.py's own
    RUN_ID_RETRY_LIMIT bounded-retry shape") -- but RUN_ID_RETRY_LIMIT
    bounds a DIFFERENT quantity entirely (fresh-id collision retries, a
    write-time concern), and the cap's REAL justification -- capping how
    many resume_setup.py round-trips a caller pays for -- stopped applying
    the moment resolve_run_id() switched to the plural `resume_from_run_ids`
    field (8815800 / this driver's own round-2 follow-up): resume_setup.py
    now computes input_digest EXACTLY ONCE per call regardless of how many
    candidates are offered, so a longer candidate list costs a few more
    cheap file reads and string compares inside that ONE call, never an
    additional subprocess round-trip. With the cost gone, the cap became
    pure downside: an interrupted, genuinely matching run behind FIVE OR
    MORE newer non-matching candidates (any mix of glossary runs without a
    detectable sibling yet, or mass runs from repeated retries) is dropped
    before resolve_run() ever sees it, minting a fresh RUN_ID and silently
    re-doing already-promoted, already-paid-for work -- exactly the #392
    defect class this whole resumability story exists to prevent. Nothing
    here recreates a cap on different grounds either: `runs_dir` only ever
    grows by one entry per non-resuming invocation (a genuine digest
    mismatch or a first-ever run), which in real operation stays small
    enough that offering every discovered candidate is unconditionally
    the right trade.

    #392 (codex, round 2): a SINGLE "latest" candidate is not enough.
    `{durable_root}/runs/` mixes mass AND glossary run dirs -- both kinds
    write `input.digest` there via write_run_dir() (resume_setup.py), the
    glossary tree at `{durable_root}/glossary/runs/<run_id>/` is an
    ADDITIONAL directory, never a substitute -- so an interrupted mass run
    at 09:00 followed by any glossary pass at 10:00 means the newest
    `runs/` entry is the glossary one, which can never match a kind="mass"
    digest, and the mass run's own genuinely-resumable candidate was never
    even offered to resolve_run_id() before. Candidates whose id also has a
    `{durable_root}/glossary/runs/<id>/` sibling are dropped here -- a
    write_run_dir() call for kind="glossary" ALWAYS creates that directory,
    unconditionally, so its presence is strong (if not perfectly certain)
    evidence the candidate is a glossary run and would waste a doomed
    resume_setup.py round-trip trying it. This is a filter over EXISTING
    on-disk artifacts, never a re-derivation of compute_input_digest()'s
    own algorithm -- resume_setup.py's digest comparison remains the ONLY
    authority on whether resuming any one of these candidates is actually
    safe; this function only decides which candidates are worth OFFERING
    to that authority, and in what order.

    Never invents a run_id itself, never writes anything -- a pure,
    read-only scan. Returns [] if `runs_dir` does not exist or holds no
    such directory -- resolve_run_id() then omits `resume_from_run_ids`
    entirely, exactly like a genuinely first-ever run (resume_setup.py's
    own module docstring: "Omitting both fields is a genuinely-first-ever-
    run signal")."""
    if not runs_dir.is_dir():
        return []
    glossary_runs_dir = durable_root / "glossary" / "runs"
    candidates = [
        p.name for p in runs_dir.iterdir()
        if p.is_dir() and validate_run_id(p.name) is None and (p / "input.digest").is_file()
        and not (glossary_runs_dir / p.name).is_dir()
    ]
    return sorted(candidates, reverse=True)


def resolve_run_id(dirs: dict, *, translate_cfg: dict,
                    plugin_root_str, durable_root_str) -> dict:
    """Builds the exact payload shape resume_setup.py's own module docstring
    documents for a kind="mass" caller (kind, args, subst, plugin_root,
    resume_from_run_ids), writes it to a scratch file, and invokes
    resume_setup.py --payload-file <path> [--durable-root ...]
    [--plugin-root ...] EXACTLY ONCE. Returns the parsed {"success",
    "effectiveRunId", "resume", "run_dir", "input_digest"} payload verbatim
    on success; raises DriverError (never a bare traceback) on any
    invocation failure or a `success: false` response.

    Deliberately does NOT send `segs` (codex round-2 follow-up, post-
    8815800): the shipped resume_setup.py derives the input_digest's
    domain itself, straight from manifest.json's own segments[]
    (_load_manifest_seg_ids(), resume_setup.py:548) -- never from a
    caller-supplied list -- and reads a `segs` field literally NOWHERE in
    its own source; resume_integrity.test.py:test_mass_segs_field_omitted_
    still_works proves omission is accepted for kind="mass" specifically,
    not merely inferred from the module docstring. This driver ships in
    the SAME release as that exact resume_setup.py commit, so it carries
    no pre-8815800 caller to stay backward-compatible with. That window --
    which existed in resume_setup.py's docstring for OTHER,
    separately-versioned callers -- covered 1.18.0 and is now spent; the field is gone
    from the contract entirely, inert only because the payload has no key
    allowlist. Sending it would reintroduce dead code with nothing on the
    receiving end to read it.

    The #392 defect this domain choice closes is still worth carrying
    here, because it is the property the whole resume story depends on:
    select_segments.py's own eligible list SHRINKS by one entry every time
    a segment converges (DEFAULT_ELIGIBLE_CATEGORIES excludes `reusable`),
    so a digest domain built from THAT list mints a fresh RUN_ID on every
    single convergence -- orphaning every dispatch_token already on disk,
    including a fix just applied by hand to a DIFFERENT still-in-progress
    segment. manifest.json's full segments[] does not shrink as segments
    converge (a segment's own cache_key does not change just because its
    ledger status did), which is exactly why the authority derives the
    domain from there rather than from anything this driver -- or any
    caller -- could pass in.

    codex round-2 follow-up: `resume_from_run_ids` (plural, shipped
    8815800) carries EVERY candidate _resumable_run_id_candidates() offers
    (most recent first) in this ONE call, omitted entirely when there are
    none -- resume_setup.py's own resolve_run() (resume_setup.py:720) now
    does the try-each-candidate-in-order/first-match-wins loop internally
    and computes input_digest EXACTLY ONCE regardless of candidate count.
    This replaces an earlier version of this function that called
    resume_setup.py once PER candidate with the deprecated singular
    `resume_from_run_id` field: correct, but for a project with N segments
    and K offered candidates that cost up to N*K cache_key.py subprocess
    spawns (up to 5*81=405 on tome1's real candidate/segment counts,
    resume_setup.py's own module docstring's `resume_from_run_ids`
    paragraph). The shipped fix moved the candidate loop server-side
    specifically to close that cost; this function now matches it rather
    than re-introducing the same multiplication client-side. See
    _resumable_run_id_candidates()'s own docstring for why more than one
    candidate must be OFFERED at all (`runs/` mixes mass and glossary run
    dirs, and the newest entry can be a glossary run that can never match a
    kind="mass" digest even when an older, genuinely resumable mass run
    sits right behind it) -- that reasoning is UNCHANGED by the plural
    switch; only which side iterates over the offered candidates did.

    `args` is always `{}` for kind="mass" -- resume_setup.py now REJECTS
    (ResumeSetupError) any other value outright, before its own expensive
    per-segment cache_key.py shell-outs (compute_input_digest(),
    resume_setup.py:622-634), for the reason this driver already applies:
    this driver's own CLI scoping flags (--only-segs/
    --allow-retranslate-converged/--allow-empty) govern Step 1's OWN
    gating (select_segments.py, already run and already enforced before
    resolve_run_id() is ever called) -- they do not change what any
    already-promoted per-segment artifact MEANS, so they have no business
    gating whether this run's digest matches a prior one. Every input that
    genuinely SHOULD invalidate resume (engine config, verse policy,
    language pair, the plugin/orchestration bundle hashes, each segment's
    own cache_key) is still fully covered by `subst`/`domain`/`version`."""
    script = dirs["resume_setup_script"]
    if not script.is_file():
        fatal(f"resume_setup.py not found at {script}", exit_code=2)

    payload = {
        "kind": "mass",
        "args": {},
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
    }
    candidates = _resumable_run_id_candidates(dirs["runs_dir"], dirs["durable_root"])
    if candidates:
        # Omitted entirely (never an empty list) when there are none --
        # resume_setup.py's own module docstring: "Omitting both fields is
        # a genuinely-first-ever-run signal, exactly as before."
        payload["resume_from_run_ids"] = candidates
    return _call_resume_setup(script, payload, dirs, durable_root_str, plugin_root_str)


def _call_resume_setup(script: Path, payload: dict, dirs: dict, durable_root_str, plugin_root_str) -> dict:
    """ONE resume_setup.py --payload-file invocation for the given payload.
    Raises DriverError on a genuine invocation failure (bad output, or
    resume_setup.py's own `success: false`, e.g. a malformed manifest) --
    never on a mere digest MISMATCH, which resume_setup.py itself reports
    as `success: true, resume: false` (a valid, expected outcome, not an
    error)."""
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


def resolve_companion_path(dirs: dict, *, node_bin: str) -> str:
    """No `durable_root_str` parameter -- a prior version accepted and
    silently ignored one (dead: the `--durable-root` value actually passed
    below always came from `dirs["durable_root"]`, which resolve_dirs()
    already resolved from that same string). A parameter nothing reads is
    the same "sets something nothing reads" shape as a test patching an
    attribute nothing checks -- removed rather than left to invite a
    caller into believing it does something."""
    script = dirs["resolve_codex_companion_script"]
    if not script.is_file():
        fatal(f"resolve_codex_companion.py not found at {script}", exit_code=2)
    # codex round-3 correction: hand-built rather than routed through
    # _root_forward_args() -- --durable-root is forwarded UNCONDITIONALLY
    # here, even in the "both root strings None" self-anchored case, where
    # that helper returns [] (no flag at all). Switching to the helper
    # would silently drop this flag in the common case, a real behavior
    # change, not a cleanup.
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
    "fixReviewDispatchPrompt",  # #409 track B -- the merged review+fix prompt, driver-only
    "fixPrompt", "parseDisp",
    "matchedVerdict",  # codex round 2, item 8 -- the fabricated-finding gate, see derive_next_action()
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
    truncated away before this source is written).

    STANDING TRAP (codex, round 2): `globalThis.args = "[]"` makes SEGS
    ALWAYS EMPTY (`const SEGS = Array.isArray(args) ? args : JSON.parse(
    args);`, template.js:494) -- and the truncation marker
    (_TRUNCATE_BEFORE_MARKER) sits well AFTER that line, so this harness
    DOES execute the template's own top-level SEGS guards (the duplicate-id
    `seen`-set check at template.js:536-541, and the SEG_ID_RE safety loop
    at template.js:513-518) on every call. They run, and they look like
    coverage -- but against an always-empty SEGS they are zero-iteration
    loops: they can never fire, on any input, ever, under this harness.
    This is NOT a one-time bug to fix; it is a property of this
    architecture that will stay true for any FUTURE guard added to that
    same top-level block. Do not treat those guards as tested by anything
    that runs through this function. (This is exactly why this driver's
    own SEGS-facing checks -- validate_seg()'s --only-segs loop in run(),
    _dedupe_segs() -- are separate, independent code, never delegated to
    "the template already checks this.")"""
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
    exactly (not codex_job.py's own draft/review extension spelling).

    "fixreview" has no template counterpart to mirror (the Workflow never
    dispatches that kind), so it simply continues the same scheme -- and gets
    its OWN prefix rather than borrowing "review"'s, so a stale task file can
    always be attributed to the dispatch that wrote it."""
    return durable_root / "segments" / f".codex_task.{kind}.{seg}.{disp}"


def build_codex_job_argv(*, kind: str, seg: str, companion_path: str, durable_root: Path,
                          prompt_file: Path, expect_token: str, disp: str, deadline_sec: int,
                          effort: str, model: str, plugin_root_str, node_bin: str = "node",
                          expect_review_token: "str | None" = None) -> list:
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
    matches byte for byte.

    `expect_review_token` is the ONE field with no counterpart in the template,
    and deliberately so: --kind fixreview does not exist there at all. It is
    required exactly for that kind (codex_job.py refuses the kind without it and
    refuses the flag for every other kind), so it is emitted iff a value is
    given, and the equivalence test's translate/review comparisons never see it."""
    if kind == "fixreview" and not expect_review_token:
        fatal("--kind fixreview requires a review token; refusing to launch a job "
              "codex_job.py will reject", exit_code=2)
    if kind != "fixreview" and expect_review_token:
        fatal(f"a review token was supplied for --kind {kind}, which does not accept one",
              exit_code=2)
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
    if expect_review_token:
        argv += ["--expect-review-token", expect_review_token]
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
                 durable_root_str, plugin_root_str, node_bin, session_id,
                 fix_mode=FIX_MODE_HANDOFF):
        self.dirs = dirs
        self.run_id = run_id
        self.translate_cfg = translate_cfg
        self.companion_path = companion_path
        self.durable_root_str = durable_root_str
        self.plugin_root_str = plugin_root_str
        self.node_bin = node_bin
        self.session_id = session_id
        # `handoff` reproduces 1.18.0 exactly: a not-clean numeric round stops
        # and hands the fix out to the caller. `codex` dispatches --kind
        # fixreview instead and publishes the pair through the transaction
        # layer. The DEFAULT is deliberately the old behaviour -- this release
        # makes the new path reachable, not active.
        self.fix_mode = fix_mode

    @property
    def txn_dir(self) -> Path:
        """runs/<RUN_ID>/txn/ -- per RUN, not per durable root.

        Scoping it to the run is what keeps a transaction from a previous,
        abandoned run from being recovered into this one: recovery discovers by
        a fixed intent PATH, so two runs sharing a directory would have their
        intents collide on the same name."""
        return self.dirs["runs_dir"] / self.run_id / "txn"

    @property
    def segments_dir(self) -> Path:
        return self.dirs["durable_root"] / "segments"


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


def _matched_review_round_label(review_obj, run_id: str, seg: str, max_fix_rounds: int) -> "str | None":
    """The round_label `review_obj`'s own `dispatch_token` matches for
    THIS run+seg (rounds 1..max_fix_rounds, then the mandatory "final"),
    or None if it belongs to a different run/round shape -- absent,
    malformed, or a stale token left over from a prior run/round. Shared
    by derive_next_action()'s own review-reading branch and its
    post-fix-invalid-draft discriminator (codex round-3 MAJOR) below, both
    of which need the identical "is this review genuinely evidence about
    THIS run" check -- a review from an unrelated run must never be read
    as proof that a fix was applied in this one."""
    token = review_obj.get("dispatch_token") if isinstance(review_obj, dict) else None
    for n in range(1, max_fix_rounds + 2):
        label = "final" if n == max_fix_rounds + 1 else str(n)
        if token == review_dispatch_token(run_id, seg, label):
            return label
    return None


def _translate_redispatched_since(dirs: dict, seg: str, review_path: Path) -> bool:
    """True iff THIS driver dispatched a fresh translate for `seg` --
    process_segment()'s own `if action["action"] == "translate":` branch,
    which writes runs/ledger.d/{seg}.json's status="in_progress" fragment
    via ledger_update.py IMMEDIATELY BEFORE every translate dispatch, both
    the very first one and any later retry -- STRICTLY AFTER `review_path`
    was last written.

    Verification-round finding: derive_next_action()'s `if not draft_ok:`
    branch used to assume the ONLY thing that ever edits a draft after a
    review is a fix turn -- true only because a genuine RE-TRANSLATE under
    the SAME run_id was assumed unreachable. It is reachable:
    translate_dispatch_token(run_id, seg) is a PURE function of run_id and
    seg, so a legitimately re-selected segment (select_segments.py's own
    --only-segs retry of a human_escalation segment, resolved to the SAME
    run_id by resume_setup.py matching the same input digest on a later
    invocation) produces a byte-identical token to what a fix turn's "copy
    dispatch_token exactly" instruction ALSO produces -- so the draft_sha1
    comparison alone cannot tell "a fix edited this draft" apart from "a
    fresh retranslate overwrote this draft", and misreading the second as
    the first wrongly terminates a segment that merely needs retrying.

    The ledger fragment is the durable evidence the sha1 alone is not: a
    retranslate always writes a FRESH runs/ledger.d/{seg}.json (a new file
    mtime, stamped by ledger_update.py's own now_iso8601() at write time,
    ledger_update.py:712) strictly after the review it invalidates; a fix
    turn edits the draft directly and never goes through process_segment()
    at all, so it writes no ledger fragment -- the fragment's mtime stays
    exactly where the LAST translate dispatch (the original one, or an
    earlier retry) left it, older than the review.

    Conservative on any doubt: a missing or unreadable fragment returns
    False, the same direction the sha1 comparison's own "cannot prove it"
    case already takes -- this function only ever ADDS a way to prove a
    genuine retranslate, never a way to prove a fix. An unprovable case
    still terminates as invalid_post_fix_draft, which errs toward
    stopping rather than silently discarding real work."""
    fragment_path = dirs["runs_dir"] / "ledger.d" / f"{seg}.json"
    try:
        fragment_mtime_ns = fragment_path.stat().st_mtime_ns
        review_mtime_ns = review_path.stat().st_mtime_ns
    except OSError:
        return False
    return fragment_mtime_ns > review_mtime_ns


def derive_next_action(seg: str, ctx: "DispatchContext") -> dict:
    """Returns exactly one of:
      {"action": "translate"}
      {"action": "review", "round_label": "1".."<max_fix_rounds>"|"final"}
      {"action": "review", "round_label": ..., "cause": "fabricated_loc"} -- a
        re-review, same as the row above, but caused SPECIFICALLY by a
        fabricated (inauthentic) finding rather than a stale/absent
        review or a round advance -- see process_segment()'s own retry
        counter, which this marker exists for.
      {"action": "review", "round_label": ..., "cause": "clean_stale"} -- a
        re-review at the SAME label because a clean review's draft_sha1 no
        longer describes the draft. Same dispatch, its own counter.
      {"action": "review", "round_label": ..., "cause": "merged_fix",
       "findings": [...]} -- --fix-mode=codex only: the state that returns
        needs_fix under handoff. Dispatch is a fixreview at the SAME label.
      {"action": "needs_fix", "round_label": ..., "findings": [...]}
        -- --fix-mode=handoff only.
      {"action": "cap_reached", "findings": [...]}
      {"action": "already_converged", "round_label": "1".."<max_fix_rounds>"|"final"}
      {"action": "invalid_post_fix_draft"} -- codex round-3 MAJOR, see the
        `if not draft_ok:` branch below for the full reasoning: an invalid
        draft is NOT always safe to re-translate.
      {"action": "unreadable_draft"} -- the canonical draft EXISTS and its
        bytes cannot be read. Distinct from absent, and refused in BOTH modes:
        see the check at the top of this function.
    """
    dirs = ctx.dirs
    durable_root = dirs["durable_root"]
    segments_dir = durable_root / "segments"
    run_id = ctx.run_id
    max_fix_rounds = ctx.translate_cfg["max_fix_rounds"]

    # A CANONICAL DRAFT THAT EXISTS AND CANNOT BE READ IS NOT A MISSING ONE,
    # AND THE DIFFERENCE IS THE USER'S TEXT.
    #
    # Checked here, before the gates and in BOTH modes, because it is the
    # DEFAULT handoff path that has no other protection: the gates fail on a
    # file they cannot read, this function answers "translate", codex_job.py
    # cannot adopt it either, and its fresh attempt is os.replace()d over the
    # top. Replacing a file needs write permission on the DIRECTORY, not on
    # the file, so an unreadable draft is fully replaceable -- and the only
    # copy of text nobody in this pipeline ever managed to read is gone.
    # Nothing downstream can recover it and nothing upstream noticed.
    #
    # Absence is deliberately NOT this case: no draft yet is the ordinary
    # state of a fresh segment, and refusing it would make translation
    # impossible. The discriminator is the sentinel _sha256_of() returns for
    # present-but-unreadable, never a truthiness test that collapses the two.
    #
    # Deliberately conservative on a codex round-4 finding whose severity I
    # would otherwise have argued down: it is NOT a regression from this
    # branch -- handoff has always behaved this way -- but "no worse than
    # before" is a poor answer when the cost is destroyed source text and the
    # observation needed to prevent it is already being computed two functions
    # away.
    if _sha256_of(segments_dir / f"{seg}.draft.json") == TXN_UNREADABLE:
        return {"action": "unreadable_draft"}

    draft_ok = (
        _run_gate(dirs["draft_ready_script"],
                  [seg, "--expect-token", translate_dispatch_token(run_id, seg)],
                  ctx, supports_plugin_root=False)
        and _run_gate(dirs["validate_draft_script"], [seg], ctx, supports_plugin_root=False)
    )
    if not draft_ok:
        # codex round-3 MAJOR: an invalid draft used to mean "translate",
        # unconditionally -- correct for a genuinely fresh/post-translate
        # invalid draft (nothing has happened to it yet, redo the
        # translate), but WRONG for a POST-FIX invalid draft: after the
        # orchestrating session performs a fix turn, if that edit broke
        # coverage or a placeholder, validate_draft_script fails here --
        # draft_ready_script's own token check still passes, because
        # fixPrompt tells the fixer to copy dispatch_token byte for byte,
        # so validate_draft_script is the sole hinge -- and returning
        # "translate" unconditionally would discard BOTH the fix AND the
        # reviewed draft it was applied to, re-translating from scratch
        # over already-paid-for review work. The same failure MODE as
        # this branch's own headline BLOCKER (already-completed work
        # silently discarded and redone), surviving in a different branch
        # of this same function.
        #
        # The discriminator lives in durable state, the same primitive
        # the "clean but stale" branch below already uses: a review for
        # THIS run+seg (_matched_review_round_label(), never a stale
        # token from a different run -- a genuinely fresh translate can
        # have an UNRELATED review.json on disk too, e.g. after
        # --allow-retranslate-converged on a previously-converged
        # segment, and that must NOT be misread as fix evidence) whose
        # OWN recorded draft_sha1 differs from the CURRENT draft's
        # content hash means something edited the draft SINCE that
        # review was written. current_draft_sha1() only needs the draft
        # file to be parseable in the shape draft_content_sha1() expects
        # -- it does not depend on validate_draft_script's OWN (unrelated)
        # notion of validity, so it can still be computed here even
        # though draft_ok is False.
        #
        # Verification-round finding: a sha1 mismatch is NOT proof of a
        # fix by itself -- a genuine RE-TRANSLATE under the SAME run_id
        # also changes the draft's content after a review exists, and
        # produces the identical dispatch_token a fix turn's "copy it
        # byte for byte" instruction also produces (both are a pure
        # function of run_id+seg). _translate_redispatched_since() (see
        # its own docstring) closes the gap with the one piece of
        # evidence that genuinely differs between the two: whether THIS
        # driver itself dispatched a translate (writing a fresh ledger
        # fragment) after the review was written. Only a sha1 mismatch
        # WITHOUT that evidence is treated as fix-caused; a mismatch WITH
        # it falls through to plain "translate", the same outcome a
        # fresh, review-free invalid draft already gets.
        #
        # Deliberately NOT re-surfaced as "needs_fix" with the same old
        # findings: those findings describe the ORIGINAL translation
        # issues, not "your fix broke draft validity", and _run_gate()
        # only returns a bool -- validate_draft_script's own specific
        # complaint is never captured anywhere this function could relay.
        # Re-issuing stale findings under a NEW, different problem would
        # mislead the orchestrating session about what actually needs
        # fixing. Terminating and leaving the segment recoverable (see
        # process_segment()'s own handling of this action) hands the
        # decision back honestly instead of guessing at guidance this
        # function cannot construct correctly.
        prior_review_path = segments_dir / f"{seg}.review.json"
        if prior_review_path.is_file():
            try:
                prior_review = json.loads(prior_review_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                prior_review = None
            if isinstance(prior_review, dict) and _matched_review_round_label(
                prior_review, run_id, seg, max_fix_rounds
            ) is not None:
                prior_draft_sha1 = prior_review.get("draft_sha1")
                try:
                    current_sha1 = current_draft_sha1(seg, segments_dir, dirs["scripts_dir"])
                except DriverError:
                    current_sha1 = None
                if (
                    current_sha1 is not None
                    and prior_draft_sha1 is not None
                    and current_sha1 != prior_draft_sha1
                    and not _translate_redispatched_since(dirs, seg, prior_review_path)
                ):
                    return {"action": "invalid_post_fix_draft"}
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

    matched_round_label = _matched_review_round_label(review_obj, run_id, seg, max_fix_rounds)
    if matched_round_label is None:
        # Absent, malformed, or belonging to a different run/round shape --
        # treated exactly like "no review yet" (safe degradation, matches
        # select_segments.py's own "unrecognized -> recoverable" default).
        return {"action": "review", "round_label": "1"}

    # #392 round-2 item 8: the fabricated-finding gate, PORTED (never
    # transcribed) from the template's own findingsAuthentic()/
    # matchedVerdict() (mass-translate-wf.template.js:568-581), by
    # executing them via call_template_functions() -- the same authority
    # every prompt this driver sends is already sourced from.
    # review.schema.json types findings[].loc as a bare string with no
    # pattern, so a reviewer that died mid-judgment can emit a
    # structurally-valid review (review_ready.py has nothing to reject,
    # codex_job.py promotes it normally) whose finding content is
    # semantically empty -- e.g. loc: "TASK" instead of a real
    # block_id/FN:n/VERSE:vid reference. Reading review.json directly with
    # no LLM in this driver's OWN read path closes ARTIFACT AUTHENTICITY
    # (tampering, forgery, an agent misreporting what it read) -- that is
    # why this driver never needs an equivalent of the template's own
    # verifyReviewArtifactPrompt/review_artifact_check.py double-check.
    # It says nothing about a LEGITIMATELY promoted artifact carrying a
    # semantically empty finding -- a different property, and nothing else
    # in this driver caught it before this check existed.
    #
    # codex round-3 MAJOR: `node_bin=ctx.node_bin` is REQUIRED here -- the
    # other three call_template_functions() call sites (render_translate_
    # prompt()/render_review_prompt()/render_fix_prompt()) all pass it;
    # this one, until fixed, passed nothing and silently fell back to this
    # function's own `node_bin: str = "node"` default (bare `node` on
    # PATH). Under --node pointing at a different interpreter, this gate
    # would have run against a DIFFERENT node than every prompt render --
    # or fataled outright if PATH had no `node` at all.
    verdict = call_template_functions(
        ctx.dirs, _template_subst(ctx),
        [{"key": "verdict", "fn": "matchedVerdict", "args": [review_obj]}],
        node_bin=ctx.node_bin,
    )["verdict"]
    if verdict.get("status") != "ok":
        # Mirrors the template's own runRound() handling of a "blocked"
        # getVerifiedReview verdict: never terminal, never a ledger write,
        # never routed through needs_fix (a fabricated finding has nothing
        # real to apply -- dispatching a fix over it would edit a real
        # draft on the strength of an empty judgment). Unlike the
        # Workflow -- which relies on a WHOLE NEW run's translateStage to
        # eventually retry -- this driver's own draft is still valid, so
        # only a fresh review at the SAME round label is needed, never a
        # re-translate.
        #
        # codex round-2 follow-up: `cause: "fabricated_loc"` is a marker
        # for process_segment()'s OWN retry counter, not a new action type
        # -- this is still plain "review" as far as dispatch goes. Without
        # it, process_segment() cannot tell "re-review because the draft
        # changed" (derive_next_action's OWN "clean but stale" branch,
        # above) apart from "re-review because the verdict was
        # fabricated" -- and a reviewer that persistently emits a
        # colonless holistic loc (the template's own comment above
        # AUTHENTIC_LOC_RE names this as something a HEALTHY reviewer can
        # do) would otherwise re-fire this branch every iteration until
        # process_segment()'s defensive iteration cap, burning a full
        # codex_jobs_per_segment worth of real, wasted dispatches and
        # exiting through a reason string that names none of this.
        return {"action": "review", "round_label": matched_round_label, "cause": "fabricated_loc"}

    clean = review_obj.get("clean") is True
    coverage_ok = review_obj.get("coverage_ok") is True

    # Distinguish "still awaiting a fix" / "genuinely converged" from "this
    # review's own verdict no longer applies" the same primitive-reuse way
    # review_ready.py/draft_sha1.py already tie review<->draft together:
    # compare the CURRENT draft's content sha1 against what THIS review
    # recorded at review time. Computed once, used by both branches below.
    reviewed_sha1 = review_obj.get("draft_sha1")
    try:
        current_sha1 = current_draft_sha1(seg, segments_dir, dirs["scripts_dir"])
    except DriverError:
        current_sha1 = None
    draft_matches_review = (
        current_sha1 is not None and reviewed_sha1 is not None and current_sha1 == reviewed_sha1
    )

    if clean and coverage_ok:
        if draft_matches_review:
            # round_label is REQUIRED here, not decoration: the caller needs
            # it to compute the ledger's own `rounds` field (a real integer,
            # per mass-translate-wf.template.js's own runRound(),
            # template.js:1595-1596 -- `rounds: round`, the NUMERIC loop
            # variable, which equals MAXFIX + 1 on the mandatory final call,
            # template.js:1757). Without this, a segment that converges on
            # the FINAL round -- an entirely ordinary outcome -- could never
            # be told apart from one that converged on a numbered round.
            return {"action": "already_converged", "round_label": matched_round_label}
        # codex #392-class MAJOR: a CLEAN review whose draft_sha1 no longer
        # matches the CURRENT draft (edited out-of-band since this review
        # was written -- or the sha1 simply could not be recomputed) must
        # NEVER fall through to already_converged: ledger_update.py's own
        # independent check (enrich_converged_fields, ledger_update.py:
        # 499-502) refuses that convergence write outright, and with no
        # branch that ever re-dispatches a review in that case, every later
        # invocation would repeat the SAME refused write forever -- a
        # live-lock, not a transient failure. There is also nothing to FIX
        # (this review's own findings are empty), so this is never routed
        # through needs_fix either -- the only correct move is a fresh
        # review of the current draft, at the SAME round label (a genuine
        # re-check of what changed, not a new round spent).
        #
        # `cause: "clean_stale"` is a marker for process_segment()'s own
        # per-round counter, exactly like "fabricated_loc" above, and it is
        # what makes the codex-mode bound real rather than asserted. This
        # branch re-dispatches at the SAME label with no bound of its own, so
        # a draft edited out-of-band on every iteration re-fires it until the
        # loop's cap -- which is fine for handoff (the cap IS the bound there,
        # and the loop's own docstring says so), but codex mode's K claims a
        # SPECIFIC per-round allowance for staleness, and a claimed allowance
        # nothing counts is just the old unbounded path with a number written
        # next to it. Without a distinguishable cause, process_segment() cannot
        # tell this re-dispatch from an ordinary round advance and has nothing
        # to count.
        return {"action": "review", "round_label": matched_round_label, "cause": "clean_stale"}

    if matched_round_label == "final":
        return {"action": "cap_reached", "findings": review_obj.get("findings") or []}

    # Not clean, not the mandatory final round -- a fix is needed before the
    # NEXT review round can be dispatched. Any ambiguity (can't compute
    # either sha1) stays conservative -- report needs_fix rather than
    # silently advancing.
    if draft_matches_review or current_sha1 is None or reviewed_sha1 is None:
        if effective_fix_mode(ctx, seg) == FIX_MODE_CODEX:
            # THE MODE BRANCH SITS HERE, not as a blanket replacement of this
            # return, because the handoff branch below has to stay live -- it
            # is the default and the whole of 1.18.0's behaviour.
            #
            # effective_fix_mode(), NOT ctx.fix_mode directly (#409 track B,
            # the size fallback): a segment whose merged fixreview call is
            # unobservable or over engine.max_fixreview_context_tokens must
            # fall to THIS branch's sibling below (needs_fix) instead of
            # merged_fix, even though the RUN is configured --fix-mode=codex
            # -- see effective_fix_mode()'s own docstring for why it fails
            # closed rather than proceeding on an unobservable size.
            #
            # On the ordinary codex path this state is not reached at all: a
            # fixreview round publishes a review bound to the PRE-edit draft
            # together with the edited draft, so the review and the draft
            # never match unless the round changed nothing, and a round that
            # changed nothing is convergence, handled above. What DOES reach
            # here is a project switched from handoff to codex mid-flight,
            # carrying a canonical not-clean review from its previous run --
            # plus the ambiguous sub-case where neither sha1 could be
            # computed.
            #
            # The answer for both is a fixreview at the SAME label rather than
            # the next one. There is no separate fix dispatcher in this mode,
            # so the pending findings can only be applied by a merged call;
            # dispatching the next label instead would advance the round
            # counter over findings nobody applied, and the fresh review would
            # simply rediscover them one round poorer. Re-reviewing costs one
            # round's judgment and keeps the round counter honest.
            return {"action": "review", "round_label": matched_round_label,
                    "cause": "merged_fix", "findings": review_obj.get("findings") or []}
        return {"action": "needs_fix", "round_label": matched_round_label, "findings": review_obj.get("findings") or []}

    return {"action": "review", "round_label": _next_round_label(matched_round_label, max_fix_rounds)}


def _next_round_label(round_label: str, max_fix_rounds: int) -> str:
    """The round label immediately after `round_label` -- "final" stays
    "final" (there is no round beyond the mandatory final one; a fresh
    re-review of a stale "final" round is dispatched at the SAME label, see
    derive_next_action()'s clean-but-stale branch)."""
    if round_label == "final":
        return "final"
    next_round = int(round_label) + 1
    return "final" if next_round == max_fix_rounds + 1 else str(next_round)


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


def render_fixreview_prompt(ctx: "DispatchContext", seg: str, round_label: str) -> str:
    """The merged review+fix prompt for --kind fixreview (#409 track B),
    sourced the same executed-template way as every other prompt here.

    Only ever rendered for a NUMERIC round: the mandatory final round stays a
    plain review that edits nothing (see the dispatch table in the plan's §4.1,
    and run_one_codex_job()'s own kind selection), so there is no "final"
    spelling of this prompt to render."""
    out = call_template_functions(
        ctx.dirs, _template_subst(ctx),
        [{"key": "text", "fn": "fixReviewDispatchPrompt", "args": [seg, round_label]}],
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
# from codex_job.py's OWN stdout line, relayed unchanged -- see
# _codex_job_outcome()'s own docstring for the one narrow exception (no
# parseable stdout at all), where this driver attributes a `driver-`
# prefixed reason to ITSELF rather than inventing a codex_job.py-shaped one.
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
            outcome = {
                "ok": bool(line.get("ok")),
                "reason": line.get("reason"),
                "error_detail": line.get("error_detail"),
                "job_status": line.get("job_status"),
                "adopted": line.get("adopted"),
            }
            # THE STAGED FIELDS ARE THE ONLY POINTER TO THE CANDIDATES.
            # --kind fixreview never promotes; it leaves both artifacts at
            # private per-invocation paths whose `inv` component is random and
            # reported ONLY here. Dropping these -- which this function did,
            # returning a fixed five-key dict -- loses the files entirely: no
            # deterministic slot exists to find them again, and nothing sweeps
            # for them. Copied verbatim, never re-derived.
            for field in ("staged", "staged_draft_path", "staged_review_path",
                          "staged_draft_sha256", "staged_review_sha256"):
                if field in line:
                    outcome[field] = line[field]
            return outcome
    return {
        "ok": False,
        "reason": "driver-no-parseable-stdout",
        "error_detail": (dispatch_result.get("stderr") or "").strip() or None,
        "job_status": None,
        "adopted": None,
    }


def run_one_codex_job(ctx: "DispatchContext", *, kind: str, seg: str, round_label: "str | None" = None) -> dict:
    """Dispatches ONE codex_job.py invocation for `seg` (translate, one
    review round, or one merged fixreview round) and returns codex_job.py's
    OWN reported outcome (see _codex_job_outcome()) plus the {kind, seg,
    round_label, disp} this dispatch used. Writes the task-file, builds the argv via
    build_codex_job_argv(), and blocks via dispatch_codex_job() -- every
    property (start_new_session, no polling) that primitive already closes.

    round_label is genuinely optional for kind="translate" (there is no
    round for a translate dispatch) but REQUIRED for kind="review" and
    kind="fixreview" -- render_review_prompt()/review_dispatch_token() both
    declare it `str`, never `Optional`. Checked explicitly here rather than left implicit:
    an unchecked None reaching review_dispatch_token()'s f-string would not
    crash -- it would silently build "<run_id>:<seg>:rNone", a
    syntactically fine but semantically orphaned token no real round label
    can ever match, so the resulting review is dispatched, promoted, and
    then invisible to every future derive_next_action() call -- the exact
    "a value derived by a lookup that can fail, fed into something that
    assumes it cannot" class as the `rounds: null` defect fixed earlier."""
    dirs = ctx.dirs
    durable_root = dirs["durable_root"]
    expect_review_token = None
    if kind == "translate":
        prompt_text = render_translate_prompt(ctx, seg)
        expect_token = translate_dispatch_token(ctx.run_id, seg)
    elif kind == "fixreview":
        if round_label is None:
            fatal(f"internal error: round_label is required for kind={kind!r}, got None", exit_code=2)
        # THE TWO TOKENS ARE NOT INTERCHANGEABLE AND NEITHER IS OPTIONAL.
        # A fixreview call writes a DRAFT and a REVIEW, and each is gated
        # against its own kind's token: --expect-token carries the draft's
        # (the run-and-segment token every draft in this run already
        # carries, which the call copies through unchanged), while
        # --expect-review-token carries THIS round's review token. Handing
        # either one the other's value is not a mismatch codex_job.py can
        # report usefully -- draft_ready.py and review_ready.py would each
        # reject a perfectly good artifact for carrying the token it is
        # supposed to carry.
        prompt_text = render_fixreview_prompt(ctx, seg, round_label)
        expect_token = translate_dispatch_token(ctx.run_id, seg)
        expect_review_token = review_dispatch_token(ctx.run_id, seg, round_label)
    else:
        if round_label is None:
            fatal(f"internal error: round_label is required for kind={kind!r}, got None", exit_code=2)
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
        node_bin=ctx.node_bin, expect_review_token=expect_review_token,
    )
    append_journal(durable_root, ctx.session_id, {
        "type": "codex_dispatch_started", "seg": seg, "kind": kind,
        "round_label": round_label, "disp": disp,
    })
    # codex #392-class BLOCKER: dispatch_codex_job() calls fatal() (raises
    # DriverError) on its own backstop-timeout path and on a missing
    # codex_job.py script -- BOTH left uncaught here would propagate through
    # process_segment() -> pool.map() -> run(), discarding every OTHER
    # segment's already-completed result and reporting the whole batch a
    # failure over ONE segment's overrun. Every other per-segment failure in
    # this file is carefully returned as an outcome, never raised past its
    # own segment's boundary -- this is the one path that broke that
    # discipline. Caught here and reshaped into the SAME outcome shape
    # _codex_job_outcome() produces, so process_segment()'s existing
    # `if not result["ok"]:` handling needs no changes at all.
    try:
        dispatch_result = dispatch_codex_job(
            dirs["codex_job_script"], argv, wait_timeout=CODEX_JOB_WAIT_TIMEOUT_SEC,
            cancel_context={
                "durable_root": durable_root, "seg": seg, "disp": disp,
                "companion_path": ctx.companion_path, "node_bin": ctx.node_bin,
            },
        )
        outcome = _codex_job_outcome(dispatch_result)
    except DriverError as exc:
        outcome = {
            "ok": False, "reason": "driver-dispatch-error", "error_detail": str(exc),
            "job_status": None, "adopted": None,
        }
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


# ---------------------------------------------------------------------------
# Phase 2 -- driving the paired publication (#409 track B).
#
# The transaction LAYER (write_txn_intent / gather_txn_observed /
# classify_txn_recovery / publish_txn / cleanup_txn / the two counters) sits
# far above, deliberately I/O-thin and pure where it can be. What follows is
# the part that USES it: staging a validated pair, minting the intent, and
# taking a transaction to a terminal state.
#
# THE FORWARD PATH AND THE RECOVERY PATH ARE THE SAME CODE, and that is the
# design rather than a convenience. Immediately after the intent is made
# durable, the on-disk state IS the classifier's "nothing renamed yet" case --
# so publishing a fresh pair is literally the act of recovering a transaction
# that has just been prepared. Any state the forward path can produce is
# therefore a state the recovery path has already been made to understand;
# there is no second implementation to drift.
# ---------------------------------------------------------------------------


@contextlib.contextmanager
def segment_lease(segments_dir: Path, seg: str):
    """Hold codex_job.py's OWN per-segment flock for the duration of the block.

    Yields True if the lease was taken, False if another process holds it.

    THE POINT IS TO READ CANONICAL STATE UNDER THE SAME LEASE THAT PROTECTS
    WRITING IT. derive_next_action() reads the draft and the review from
    outside any lease, while the pair is published by a child that holds this
    one -- so a driver whose predecessor died with a child still running can
    observe "review published, draft not yet" and act on it. Recovery has to
    happen before that read, under this lease, which is why it cannot be an
    action derive_next_action() returns: derive IS the read.

    NON-BLOCKING ON PURPOSE. The alternative -- waiting -- serialises the whole
    batch behind whichever segment holds the longest-running codex job, and
    buys nothing: while a child holds the lease the segment is genuinely not
    actionable, and the next driver invocation picks it up. The caller reports
    a recoverable failure and writes no terminal ledger row.

    The lock file is codex_job.py's, by exact path (`.codex_job.<seg>.lock` in
    the segments directory) and is never unlinked -- a second lock file of this
    driver's own would be an independent lease excluding nobody."""
    lock_path = segments_dir / f".codex_job.{seg}.lock"
    fd = None
    held = False
    try:
        try:
            segments_dir.mkdir(parents=True, exist_ok=True)
            fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
        except OSError as exc:
            print(f"segment_dispatch_driver.py: warning: could not open the per-segment "
                  f"lease at {lock_path}: {exc}", file=sys.stderr)
            yield False
            return
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            held = True
        except OSError:
            held = False
        yield held
    finally:
        if fd is not None:
            if held:
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                except OSError:
                    pass
            try:
                os.close(fd)
            except OSError:
                pass


def _stage_candidate(source: Path, destination: Path, expected_sha256) -> bool:
    """Copy ONE validated candidate into the transaction's own staging slot,
    durably, and only if its bytes still hash to what codex_job.py reported.

    Re-hashing here is not a duplicate of codex_job.py's own check. That digest
    describes the bytes its four gates validated; this one describes the bytes
    that will actually be renamed over the user's text. Between the two the
    file has been sitting at a private path in the segments directory, reachable
    by anything with write access to it. Copying without re-hashing would carry
    the VALIDATED digest into the intent while the STAGED bytes are something
    else -- and every later check compares against the intent, so the swap would
    be invisible from that point on."""
    try:
        data = source.read_bytes()
    except OSError as exc:
        print(f"segment_dispatch_driver.py: could not read the staged candidate "
              f"{source}: {exc}", file=sys.stderr)
        return False
    digest = hashlib.sha256(data).hexdigest()
    if not isinstance(expected_sha256, str) or digest != expected_sha256:
        print(f"segment_dispatch_driver.py: refusing to stage {source}: its bytes hash to "
              f"{digest}, not to the {expected_sha256!r} codex_job.py validated",
              file=sys.stderr)
        return False
    tmp = destination.with_name(destination.name + ".tmp")
    try:
        with open(tmp, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(str(tmp), str(destination))
    except OSError as exc:
        print(f"segment_dispatch_driver.py: could not stage {destination}: {exc}", file=sys.stderr)
        try:
            tmp.unlink()
        except OSError:
            pass
        return False
    return _fsync_dir(destination.parent)


def _discard_unpublished_candidates(result: dict) -> None:
    """Remove codex_job.py's two private per-invocation candidates.

    Best effort by design: a failed unlink is litter, never a reason to fail an
    otherwise valid publication.

    ONE CALLER, and deliberately so. This runs only once the durable intent
    owns the bytes. An earlier revision also called it when the publication
    lease could not be taken; that was removed, because the dispatch journal
    records those paths and deleting them destroyed recoverable work in order
    to avoid leaving litter."""
    for key in ("staged_draft_path", "staged_review_path"):
        path = result.get(key)
        if not isinstance(path, str) or not path:
            continue
        try:
            os.unlink(path)
        except OSError:
            pass


def _txn_failure_ceiling(ctx: "DispatchContext") -> int:
    return ctx.translate_cfg.get("max_txn_failures_per_segment",
                                 DEFAULT_MAX_TXN_FAILURES_PER_SEGMENT)


def advance_txn(ctx: "DispatchContext", seg: str, round_label=None) -> dict:
    """Take ONE segment's transaction to a terminal state: observe, classify,
    publish whatever the decision prescribes, commit, clean up -- charging a
    durable failure when it refuses.

    Returns {"outcome": <TXN_* constant>, "published": bool, "txn_id": str|None,
    "charged": dict|None, "charge_lost": bool, "commit_failed": bool,
    "cleanup_failed": bool}. The last two exist because commit_txn_intent() and
    cleanup_txn() both report durability failure by returning False, and a
    DISCARDED False is exactly the shape of a segment that reads terminal and
    is not. Never raises; every refusal is a return value,
    because this runs inside per-segment work whose failures must not discard
    other segments' results.

    CHARGE FIRST, THEN CLEAN. The counter is keyed by txn_id and skips an id it
    has already charged, so charging before cleanup is safe in both crash
    directions: crash after charging and the next pass sees the same id and
    does not double-charge; crash after cleanup and there was nothing left to
    charge for. No ORDERING of a bare increment and a delete has that property
    -- one order double-charges, the other never charges -- which is why the
    counter records the ids rather than only a number.

    A FAILED PUBLICATION IS NOT CLEANED UP. publish_txn leaves a consistent
    prefix behind on refusal and the next pass classifies it; deleting the
    staging here would turn a resumable half-publication into lost work."""
    txn_dir = ctx.txn_dir
    segments_dir = ctx.segments_dir
    scripts_dir = ctx.dirs["scripts_dir"]

    observed = gather_txn_observed(seg, txn_dir, segments_dir, scripts_dir,
                                   round_label=round_label)
    decision = classify_txn_recovery(observed)
    outcome = decision["outcome"]
    intent = observed.get("intent")
    txn_id = intent.get("txn_id") if isinstance(intent, dict) else None
    # The label to clean up under is the intent's OWN, never the caller's guess
    # -- the same rule gather_txn_observed applies when it hashes staging.
    label = intent.get("round_label") if isinstance(intent, dict) else round_label

    published = True
    if decision["publish"]:
        published = publish_txn(txn_dir, seg, segments_dir, decision, scripts_dir)

    charged = None
    failed = (not published) or outcome in (TXN_PREIMAGE_DIVERGED, TXN_STAGING_LOST)
    charge_required = failed and isinstance(txn_id, str) and bool(txn_id)
    # `charge_required` already guarantees `isinstance(txn_id, str)` -- but it
    # is a SEPARATE bool variable, and a type checker cannot narrow txn_id
    # from a check it performed on a DIFFERENT name. Repeating the isinstance
    # here, directly on txn_id, is redundant at runtime (charge_required is
    # already False otherwise) but gives the narrowing something to attach to.
    if charge_required and isinstance(txn_id, str):
        charged = charge_txn_failure(txn_dir, seg, txn_id, _txn_failure_ceiling(ctx))

    committed = None
    if published and decision["commit_intent"]:
        committed = commit_txn_intent(txn_dir, seg)
    # AN UNCHARGED REFUSAL MAY NOT DELETE ITS OWN EVIDENCE. "Charge first, then
    # clean" is only idempotent while the charge actually lands: charging is
    # keyed by txn_id, so a second pass over the same intent does not
    # double-count -- but if charge_txn_failure() could not make the count
    # durable it returns None, and cleaning up regardless removes the intent
    # that carries the id. There is then nothing left for any later pass to
    # charge, and the segment can fail transactions without limit across
    # invocations while the ceiling reads zero. Keeping the intent costs one
    # more classification next time; losing the charge costs the only
    # cross-invocation bound this mode has.
    charge_lost = charge_required and charged is None
    cleaned = None
    if published and decision["cleanup"] and isinstance(label, str) and not charge_lost:
        cleaned = cleanup_txn(txn_dir, seg, label)

    # `is False`, not `not committed`: None means the step was NOT REQUIRED by
    # this decision, and "was not asked for" must never read as "failed".
    commit_failed = committed is False
    cleanup_failed = cleaned is False

    append_journal(ctx.dirs["durable_root"], ctx.session_id, {
        "type": "txn_recovery", "seg": seg, "txn_id": txn_id,
        "outcome": outcome, "published": published,
        "publish": decision["publish"],
        "txn_failures": (charged or {}).get("count"),
        "charge_lost": charge_lost,
        "commit_failed": commit_failed, "cleanup_failed": cleanup_failed,
    })
    return {"outcome": outcome, "published": published, "txn_id": txn_id,
            "charged": charged, "charge_lost": charge_lost,
            "commit_failed": commit_failed, "cleanup_failed": cleanup_failed}


def orphaned_staging_labels(txn_dir: Path, seg: str) -> list:
    """The round labels this segment has staging for, discovered by GLOB.

    Needed because the one state with no intent to read the label from --
    staging written, then a crash before the intent was made durable -- is
    exactly the state the recovery procedure's step 0 exists for. Guessing a
    label there would answer "nothing in flight" for real orphaned files and
    leave them to be mistaken for a later round's staging."""
    labels = set()
    try:
        entries = list(txn_dir.glob(f"{seg}.*.staged.draft.json"))
        entries += list(txn_dir.glob(f"{seg}.*.staged.review.json"))
    except OSError:
        return []
    for path in entries:
        rest = path.name[len(seg) + 1:]
        for suffix in (".staged.draft.json", ".staged.review.json"):
            if rest.endswith(suffix):
                label = rest[: -len(suffix)]
                if _TXN_ROUND_LABEL_RE.match(label):
                    labels.add(label)
    return sorted(labels)


def recover_segment_txns(ctx: "DispatchContext", seg: str) -> list:
    """The pre-derive recovery phase for ONE segment. MUST run holding the
    per-segment lease and BEFORE derive_next_action() reads canonical state.

    Runs advance_txn() once for the segment's intent (if any), then once per
    orphaned staging label -- the intentless leftovers a crash between staging
    and the durable intent produces, which have no intent to name their round.

    ONE ATTEMPT PER TRANSACTION PER INVOCATION. Retrying inside one invocation
    would re-run the identical observation against the identical state; the
    bound that matters is the durable failure counter, not a loop here."""
    results = [advance_txn(ctx, seg)]
    if results[0]["outcome"] == TXN_PROCEED:
        for label in orphaned_staging_labels(ctx.txn_dir, seg):
            results.append(advance_txn(ctx, seg, round_label=label))
    return results


def recovery_left_the_segment_blocked(ctx: "DispatchContext", seg: str, results) -> bool:
    """True when a transaction intent is STILL ON DISK after recovery ran.

    OBSERVED, NOT INFERRED, and that is the entire point of the signature. An
    earlier version decided this from the outcome NAMES recovery reported,
    against an allow-list of outcomes that "clean up after themselves" -- and
    an outcome name describes what the classifier DECIDED, never what actually
    happened afterwards. A roll-forward whose publication then failed reports a
    roll-forward name with published=False; commit_txn_intent() and
    cleanup_txn() both return a bool that was discarded. Every one of those
    leaves the intent in place while reading as cleared, and the loop then
    derives, dispatches and PAYS for a round whose publication refuses
    `txn-intent-already-present` -- on every invocation, charging nothing,
    because charging is idempotent per transaction id and that id was already
    charged once.

    Asking the filesystem removes the whole class: the question is "is there an
    intent that will refuse my publication", and that is a fact about a file.
    `results` is still consulted for the one thing the file cannot show -- a
    failure whose charge did not go durable, which must block even in the
    window where the intent has been removed.

    NOT Path.exists(). exists() follows the link and answers False for a
    self-referential symlink (ELOOP) exactly as it does for a missing file, and
    it swallows every other stat error the same way -- so the single question
    that decides whether a PAID dispatch may proceed would read "clear" from a
    lookup that merely failed. _entry_is_absent() asks lstat about the ENTRY and
    treats anything that is not ENOENT as present.

    The filesystem answers "will an intent refuse my publication". It cannot
    answer "did the commit and the cleanup this decision required actually
    succeed", so those are carried positively in the results and checked here
    too: a discarded False from commit_txn_intent()/cleanup_txn() is exactly the
    shape of a segment that reads clear and is not."""
    if not _entry_is_absent(txn_intent_path(ctx.txn_dir, seg)):
        return True
    for result in results or ():
        result = result or {}
        if result.get("charge_lost"):
            return True
        if result.get("commit_failed") or result.get("cleanup_failed"):
            return True
    return False


def owner_profile_path(durable_root: Path):
    """The profile.yml this durable root is owned by, per its ownership marker,
    or None if the marker cannot be read. Never fatals: this is used to HASH
    the profile for the premise below, and a premise that cannot be built must
    refuse the round, not abort the batch."""
    try:
        marker = json.loads(
            (durable_root / ".literary-translator-root.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    path = marker.get("owner_profile_path") if isinstance(marker, dict) else None
    return Path(path) if path else None


def decision_premise(ctx: "DispatchContext", seg: str) -> tuple:
    """Everything derive_next_action()'s answer and the round's validation both
    depend on, as a comparable tuple of EIGHT fields in this order: the raw
    sha256 of the canonical draft, its CONTENT sha1, its dispatch_token, then
    the raw sha256 of the canonical review, the segpack, the owning profile,
    review_TASK.md and style_bible.md (None for any that is absent). Compared
    as a WHOLE and never by position -- an index into this is a shifting
    target, as its growth from three fields to five to eight across three
    review rounds shows.

    Captured around the DECIDE step under the per-segment lease and re-asserted
    at PUBLISH time. The transaction's own CAS is not a substitute: it binds the
    state observed AFTER the codex job returns, so a competing publication that
    lands in the gap between the parent releasing the lease and the child
    taking it becomes part of the transaction's premise instead of
    invalidating it -- and the round then publishes work that, on the state now
    on disk, would never have been dispatched.

    THE SEGPACK AND THE PROFILE ARE IN HERE, not just the two artifacts, because
    they are the AUTHORITY the round was judged against, not merely context.
    draft_ready.py and validate_draft.py both read the segpack; the child's
    prompt is built from the profile. If the segpack's content changes while
    keeping its key topology -- a corrected source line, an adjusted canon
    entry -- the draft and review bytes are untouched, so a premise over those
    two alone still matches and the pair publishes as if validated against the
    new source. Worse than a stale publication: the resulting convergence's
    cache key is computed from the NEW segpack, marking work derived from the
    old one reusable under it.

    A component that could not be read comes back as TXN_UNREADABLE, and
    COMPARING TWO PREMISES IS NOT ENOUGH TO CATCH THAT: TXN_UNREADABLE equals
    itself, so two failed reads of the same file agree perfectly and the round
    proceeds having confirmed nothing. Callers must reject an unobservable
    premise explicitly -- premise_is_observable() below -- rather than rely on
    the comparison. ABSENT is different and must stay so: None equals None on
    purpose, because a segment with no review yet, or a project with no
    segpack, is a real and stable state, not a failure to look."""
    draft_path = ctx.segments_dir / f"{seg}.draft.json"
    # THE RAW DRAFT HASH IS IN HERE, not only its content sha1 and token.
    # _draft_observation() reports an unreadable draft as (TXN_UNREADABLE,
    # None, None) -- the sentinel is in the RAW slot, and the two derived
    # slots are indistinguishable from "no draft at all". Dropping the raw
    # value made a draft that exists and cannot be read look absent to
    # premise_is_observable(), so the round proceeded, the gates failed on it,
    # derive answered "translate", and the replacement was renamed over content
    # nobody had ever read.
    draft_sha256, content_sha1, token = _draft_observation(
        seg, draft_path, ctx.segments_dir, ctx.dirs["scripts_dir"])
    review_sha256 = _sha256_of(ctx.segments_dir / f"{seg}.review.json")
    segpack_sha256 = _sha256_of(ctx.segments_dir / f"segpack_{seg}.json")
    profile_path = owner_profile_path(ctx.dirs["durable_root"])
    profile_sha256 = _sha256_of(profile_path) if profile_path is not None else None
    # The two files the merged child is told to read that are NOT the segpack:
    # the round's task contract and the style contract it judges against.
    # Same argument as the segpack -- authority, not context.
    task_sha256 = _sha256_of(ctx.dirs["durable_root"] / "review_TASK.md")
    style_sha256 = _sha256_of(ctx.dirs["durable_root"] / "style_bible.md")
    return (draft_sha256, content_sha1, token, review_sha256, segpack_sha256,
            profile_sha256, task_sha256, style_sha256)


def premise_is_observable(premise) -> bool:
    """False if any component of `premise` is a file that EXISTS but could not
    be read.

    This exists because equality cannot express it. A premise is used by
    comparing two of them, and TXN_UNREADABLE compares equal to TXN_UNREADABLE
    -- so a segpack whose permissions broke reads as "unchanged" on both looks
    and the round proceeds having verified nothing about the authority it was
    judged against. Absence is deliberately NOT treated this way: None equals
    None because "there is no review yet" is a real, stable state, whereas
    "there is a review and I could not read it" is the absence of an
    observation."""
    return TXN_UNREADABLE not in tuple(premise or ())


def decide_under_premise(ctx: "DispatchContext", seg: str):
    """derive_next_action(), BRACKETED by the premise it is deciding from.

    Returns (action, premise), or (None, None) when the state moved during the
    read -- in which case the caller must re-derive rather than act.

    THE BRACKET IS THE WHOLE POINT AND THE SINGLE-SIDED VERSION WAS VACUOUS.
    Capturing the premise only AFTER derive returns records whatever is on disk
    at that moment, including a write that landed DURING the decision: derive
    answers from state A, the premise records state B, and the publish-time
    check then compares B against B and passes. The guard agreed with itself
    about exactly the writer it existed to catch. Reading before and after and
    requiring them equal is what makes "the state derive saw" a claim with
    evidence behind it rather than a comment.

    This still does not make the read atomic -- nothing available here does --
    but a writer must now land entirely between two reads that bracket the
    decision, and be gone by the second, to go unnoticed."""
    before = decision_premise(ctx, seg)
    action = derive_next_action(seg, ctx)
    after = decision_premise(ctx, seg)
    if before != after or not premise_is_observable(after):
        return None, None
    return action, after


def publish_fixreview_pair(ctx: "DispatchContext", seg: str, round_label: str,
                           result: dict, premise: tuple) -> dict:
    """Stage, record the intent, and publish ONE validated fixreview pair.

    `result` is run_one_codex_job()'s outcome for a --kind fixreview dispatch,
    carrying the five staged_* fields verbatim (their per-invocation paths are
    the ONLY pointer to the candidates -- nothing sweeps for them).

    Returns {"ok": bool, "reason": str|None, "outcome": <TXN_* or None>,
    "charged": dict|None}.

    THE PRE-IMAGE IS READ BEFORE ANYTHING IS STAGED, and it binds more than
    content. pre_edit_draft_sha1 comes from draft_sha1.py, which deliberately
    EXCLUDES dispatch_token from the hash, while derive_next_action() reads
    both the draft and the review. So a competing writer can leave the content
    identical and the token different, or leave the draft alone and replace the
    review -- and a CAS over content alone would wave both through. The intent
    therefore records the token and the review's identity as well."""
    txn_dir = ctx.txn_dir
    segments_dir = ctx.segments_dir

    def refuse(reason, outcome=None, charged=None, charge_lost=False):
        return {"ok": False, "reason": reason, "outcome": outcome,
                "charged": charged, "charge_lost": charge_lost}

    for field in ("staged_draft_path", "staged_review_path",
                  "staged_draft_sha256", "staged_review_sha256"):
        if not isinstance(result.get(field), str) or not result[field]:
            return refuse("txn-staged-fields-missing")
    if result.get("staged") is not True:
        return refuse("txn-staged-fields-missing")

    try:
        txn_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(f"segment_dispatch_driver.py: could not create {txn_dir}: {exc}", file=sys.stderr)
        return refuse("txn-dir-unavailable")

    # THE DECISION'S PREMISE, RE-ASSERTED BEFORE THE TRANSACTION IS STARTED.
    # Read first, so a stale round is refused before an attempt_seq is burned
    # or a byte is staged. What this catches is precisely the handoff window:
    # the parent released the lease to launch the child, and something else
    # published in between. Without it the pre-image below simply records the
    # competitor's state and the CAS then confirms it happily -- the round
    # would overwrite a review it never saw, with work dispatched against a
    # state that no longer exists.
    now = decision_premise(ctx, seg)
    if not premise_is_observable(premise) or not premise_is_observable(now):
        # Checked on BOTH sides. An unreadable component makes the comparison
        # below agree with itself (see premise_is_observable), so without this
        # the guard would report a match it never made.
        return refuse("txn-premise-unobservable")
    if now != premise:
        return refuse("txn-decision-stale")

    pre = gather_txn_observed(seg, txn_dir, segments_dir, ctx.dirs["scripts_dir"],
                              round_label=round_label)
    if pre.get("intent") is not None:
        # Recovery ran under the lease moments ago and left nothing behind, so
        # an intent here means one appeared since. Refuse rather than start a
        # second transaction over it -- the fixed draft is still staged at its
        # private path and the next invocation re-derives from scratch.
        return refuse("txn-intent-already-present")
    pre_sha1 = pre.get("canonical_draft_content_sha1")
    pre_token = pre.get("canonical_draft_token")
    review_sha256 = pre.get("canonical_review_sha256")
    if not isinstance(pre_sha1, str) or not isinstance(pre_token, str) or not pre_token:
        return refuse("txn-preimage-unreadable")
    if TXN_UNREADABLE in (review_sha256, pre.get("canonical_draft_sha256")):
        # A file that exists but could not be read is the ABSENCE of an
        # observation, never an observation of absence -- recording it as
        # {"absent": true} would let a later pass "confirm" a preimage nobody
        # ever saw.
        #
        # SHADOWED FOR THE ORDINARY PATH, BUT GENUINELY REACHABLE. The premise
        # check a few lines above rejects an unreadable review or draft, so a
        # mutant of this branch survives -- verified, not assumed. It is NOT
        # dead: readability can change between that check and this read, which
        # is a real window rather than a rhetorical one, and this guards a
        # different moment (the WRITE of the preimage, not the decision). An
        # earlier version of this comment called it untestable; the true
        # statement is narrower -- no test here reproduces that window today.
        return refuse("txn-preimage-unreadable")
    review_preimage = {"absent": True} if review_sha256 is None else {"sha256": review_sha256}

    attempt_seq = next_attempt_seq(txn_dir, seg)
    if attempt_seq == 0:
        return refuse("txn-attempt-seq-unavailable")
    txn_id = make_txn_id(ctx.run_id, seg, round_label, attempt_seq)

    slots = staged_paths(txn_dir, seg, round_label)
    staged_ok = (
        _stage_candidate(Path(result["staged_draft_path"]), slots["draft"],
                         result["staged_draft_sha256"])
        and _stage_candidate(Path(result["staged_review_path"]), slots["review"],
                             result["staged_review_sha256"])
    )
    if not staged_ok:
        # No intent exists yet, so this leaves at most orphaned staging -- the
        # classifier's step 0, which the next recovery pass deletes. Removing
        # it here too is belt and braces, not the mechanism.
        cleanup_txn(txn_dir, seg, round_label)
        return refuse("txn-staging-copy-failed")

    intent = {
        "txn_schema": TXN_SCHEMA_VERSION,
        "txn_id": txn_id,
        "attempt_seq": attempt_seq,
        "seg": seg,
        "round_label": round_label,
        "phase": "prepared",
        "draft_token": translate_dispatch_token(ctx.run_id, seg),
        "review_token": review_dispatch_token(ctx.run_id, seg, round_label),
        "pre_edit_draft_sha1": pre_sha1,
        "pre_edit_draft_token": pre_token,
        "review_preimage": review_preimage,
        "staged_draft_sha256": result["staged_draft_sha256"],
        "staged_review_sha256": result["staged_review_sha256"],
        "dest_draft_path": str(segments_dir / f"{seg}.draft.json"),
        "dest_review_path": str(segments_dir / f"{seg}.review.json"),
    }
    if not write_txn_intent(txn_dir, seg, intent):
        cleanup_txn(txn_dir, seg, round_label)
        return refuse("txn-intent-write-failed")

    # ONLY NOW. codex_job.py deliberately KEEPS its two private per-invocation
    # candidates when it reports `staged` -- they are the only pointer to the
    # work and it cannot know whether the driver consumed them; nobody else
    # ever removes them, so leaving them accumulates a draft-sized pair per
    # round forever. But they may not go until something durable OWNS the
    # bytes, and staging alone is not that: an intent write that fails takes
    # the staging with it on the line above, and a crash before the intent
    # lands leaves staging that the next recovery classifies as an aborted
    # prepare and deletes. Unlinked at either of those moments, no copy would
    # survive at all. The durable intent is what makes the staging the record.
    _discard_unpublished_candidates(result)

    advanced = advance_txn(ctx, seg, round_label=round_label)
    if advanced["published"] and advanced["outcome"] in (
            TXN_ROLL_FORWARD_BOTH, TXN_ROLL_FORWARD_DRAFT, TXN_ROLLED_FORWARD_TAIL):
        return {"ok": True, "reason": None, "outcome": advanced["outcome"],
                "charged": advanced["charged"], "charge_lost": advanced["charge_lost"]}
    return refuse(advanced["outcome"], outcome=advanced["outcome"],
                  charged=advanced["charged"], charge_lost=advanced["charge_lost"])


def _size_of(path: Path):
    """Byte size of `path`, or None when it is absent OR cannot be read
    (any OSError -- including a dangling symlink, whose stat() raises
    FileNotFoundError exactly like a genuinely absent path).

    Unlike _sha256_of() above, this collapses absent and unreadable into ONE
    outcome rather than three, because every caller here
    (fixreview_context_estimate()) already fails CLOSED on either: an absent
    draft (segment not translated yet) and an unreadable one both mean "this
    size cannot be trusted right now", and both must produce the identical
    None the caller falls back on. A richer three-way split would only
    invite a caller to treat one of them as safe to proceed on, which is
    exactly what effective_fix_mode() below must never do."""
    try:
        return path.stat().st_size
    except OSError:
        return None


def fixreview_context_estimate(ctx: "DispatchContext", seg: str):
    """Estimated INPUT+OUTPUT token cost of ONE --fix-mode=codex fixreview
    dispatch for `seg` -- the merged review+fix call fixReviewDispatchPrompt()
    describes (mass-translate-wf.template.js). None when ANY of the four
    real inputs that call reads cannot be sized right now; see
    effective_fix_mode() below for why that must fall back rather than
    proceed as if the call were known to fit.

    THE PROMPT ITSELF IS NOT ONE OF THE FOUR. fixReviewDispatchPrompt()
    embeds no segment content -- it is a FIXED instruction telling the model
    to go read review_TASK.md, style_bible.md, the segpack, and the draft BY
    PATH (orchestration-and-batching.md §6). Its own fixed length is
    counted as FIXREVIEW_DISPATCH_PROMPT_TOKENS above, a module constant,
    never a function of `seg`.

    THE DRAFT IS COUNTED TWICE. It is read once as the pre-edit INPUT the
    call reviews and rewritten once as the fixed OUTPUT (fixReviewDispatch
    Prompt()'s own STEP 2) -- both inside the SAME context window. A single
    count of it under-counts every real segment; measured, it would have
    caught ZERO of 207 real segments, including the one that motivated this
    whole mechanism.

    style_bible.md and review_TASK.md are read from the durable root and are
    PER PROJECT -- sized fresh here, every call, never baked into a module
    constant: a 200 KB style_bible.md alone can consume a real fraction of
    the budget, and a constant could never see that."""
    segpack_bytes = _size_of(ctx.segments_dir / f"segpack_{seg}.json")
    draft_bytes = _size_of(ctx.segments_dir / f"{seg}.draft.json")
    style_bible_bytes = _size_of(ctx.dirs["durable_root"] / "style_bible.md")
    review_task_bytes = _size_of(ctx.dirs["durable_root"] / "review_TASK.md")
    # Four SEPARATE `is None` checks, not `None in (...)`: the `in` form
    # reads identically to a human but does not narrow any of the four
    # names for the type checker, so every arithmetic use below would still
    # be flagged as possibly-None despite this guard already having
    # returned for exactly that case.
    if (segpack_bytes is None or draft_bytes is None
            or style_bible_bytes is None or review_task_bytes is None):
        return None
    estimate = (
        segpack_bytes / FIXREVIEW_SEGPACK_BYTES_PER_TOKEN
        + 2 * draft_bytes / FIXREVIEW_DRAFT_BYTES_PER_TOKEN
        + (style_bible_bytes + review_task_bytes) / FIXREVIEW_PROSE_BYTES_PER_TOKEN
        + FIXREVIEW_DISPATCH_PROMPT_TOKENS
        + FIXREVIEW_BASE_INSTRUCTIONS_TOKENS
        + FIXREVIEW_WORST_CASE_REVIEW_OUTPUT_TOKENS
    )
    return math.ceil(estimate)


def effective_fix_mode(ctx: "DispatchContext", seg: str) -> str:
    """ctx.fix_mode, EXCEPT for one case: the configured mode is `codex` and
    THIS segment's merged fixreview call is -- or might be -- too big for
    engine.max_fixreview_context_tokens. In that one case, this segment
    alone falls back to FIX_MODE_HANDOFF; every other segment in the same
    run is unaffected, and nothing here ever mutates ctx.fix_mode itself
    (DispatchContext is read-only after construction, shared across worker
    threads -- see its own docstring).

    FAIL CLOSED: fixreview_context_estimate() returning None (any of the
    four real inputs could not be sized) is treated EXACTLY like an
    over-budget estimate, never as "unknown, so proceed". An unobservable
    size most commonly means the segment has no draft yet (nothing has been
    translated), which is completely ordinary -- but ordinary is not the
    same as SAFE to assume small, and this function has no way to tell "not
    yet written" apart from "could not be read" (see _size_of()'s own
    docstring for why it does not try to).

    EVALUATE FRESH, EVERY CALL, NEVER CACHE. The four sizes the estimate
    depends on can change between iterations of process_segment()'s own
    loop -- the draft grows across fix rounds, and style_bible.md/
    review_TASK.md can be edited out-of-band -- so a decision cached from an
    earlier iteration could authorize a call the CURRENT state would refuse.
    Every one of this function's own callers calls it again rather than
    reusing an old answer, exactly like every other per-iteration decision
    derive_next_action() and its neighbours make from durable disk state,
    never from an in-memory assumption about what an earlier read saw."""
    if ctx.fix_mode != FIX_MODE_CODEX:
        return ctx.fix_mode
    budget = ctx.translate_cfg.get(
        "max_fixreview_context_tokens", DEFAULT_MAX_FIXREVIEW_CONTEXT_TOKENS)
    estimate = fixreview_context_estimate(ctx, seg)
    if estimate is None or estimate > budget:
        return FIX_MODE_HANDOFF
    return ctx.fix_mode


def _journal_fixreview_size_fallback(ctx: "DispatchContext", seg: str, round_label) -> None:
    """Appends ONE `fixreview_size_fallback` journal event -- the visibility
    #409 track B's size fallback needs. An operator watching a project run
    --fix-mode=codex who sees a `needs_fix` handoff, or a plain `review`
    dispatch where a `fixreview` was expected, has no other way to learn
    THIS is why: effective_fix_mode() returns a plain string, and the two
    process_segment() call sites that act on a fallen-back decision (the
    needs_fix return, and the review-action dispatch-kind branch) are the
    only places that know BOTH what was configured and what actually ran.

    Recomputes fixreview_context_estimate() itself rather than accepting it
    as a parameter -- this function is called only after a caller has
    already established (via effective_fix_mode()) that a fallback is
    happening, so the extra stat() calls are spent on an iteration that is
    about to make one real codex dispatch decision anyway; see
    effective_fix_mode()'s own docstring for why re-deriving rather than
    threading a cached value through is this file's standing discipline."""
    append_journal(ctx.dirs["durable_root"], ctx.session_id, {
        "type": "fixreview_size_fallback",
        "seg": seg,
        "round_label": round_label,
        "configured_fix_mode": ctx.fix_mode,
        "estimated_tokens": fixreview_context_estimate(ctx, seg),
        "budget_tokens": ctx.translate_cfg.get(
            "max_fixreview_context_tokens", DEFAULT_MAX_FIXREVIEW_CONTEXT_TOKENS),
    })


def _dispatch_kind_for_round(fix_mode: str, round_label: str) -> str:
    """Which codex job kind a `review` action dispatches.

    The mandatory final round stays a plain review in BOTH modes: it is the
    confirming round that edits nothing, and turning it into a merged call
    would let the run's last word be spoken by the same call that wrote the
    text it judges."""
    if fix_mode == FIX_MODE_CODEX and round_label != "final":
        return "fixreview"
    return "review"


def process_segment(seg: str, ctx: "DispatchContext") -> dict:
    """The unit of work ONE ThreadPoolExecutor worker performs for ONE
    segment on ONE run() invocation: "dispatch translate, wait, then the
    review/fix rounds" -- a real LOOP over this segment's rounds within
    this single call, not one dispatch per invocation. Each iteration
    re-derives the segment's next action from durable on-disk state
    (derive_next_action(), never trusting an in-memory assumption about
    what the last dispatch produced) and either performs exactly one codex
    dispatch and loops again, or reaches a genuine terminal/handoff state
    and returns.

    Every return carries an explicit `"outcome"` field -- ONE of
    "converged" / "needs_fix" / "failed" -- and `run()`'s own summary
    aggregation partitions on THAT field, never on independent predicates
    over `converged`/`reason`. This is load-bearing, not decoration: see
    run()'s own totality check, which FATALs on any result missing or
    carrying an unrecognized `outcome` rather than silently dropping it
    (codex round-2 follow-up -- a `converged: None` result used to satisfy
    none of three ad hoc filters and vanish from every summary bucket
    while still consuming real spend).

      outcome="converged"                 -- ledger recorded, done.
      outcome="failed", reason="cap"      -- mandatory final review still
                                              not clean; ledger recorded
                                              directly (fully mechanical,
                                              no fix dispatched on the
                                              final round -- matches
                                              runRound's own isFinal branch).
      outcome="needs_fix"                 -- STOPS here: applying findings
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
      outcome="failed", stage=...         -- a codex_job.py dispatch itself
                                              failed; `reason`/`error_detail`
                                              are codex_job.py's OWN reported
                                              values, verbatim (#398) -- NO
                                              terminal ledger write, so the
                                              in_progress fragment already on
                                              disk stays the durable record
                                              and select_segments.py's
                                              "recoverable" default retries
                                              this segment next invocation.
      outcome="failed", reason=
        "unexpected-error:<TYPE>"         -- ANY exception raised anywhere
                                              in this iteration's own body
                                              (codex round-3 BLOCKER,
                                              corrected -- see the
                                              try/except wrapping this
                                              whole iteration, right below
                                              the loop's own `for` line,
                                              for the full enumeration of
                                              the worker subtree's 13
                                              known raise sites across 6
                                              functions, and why this is
                                              `except Exception`, not a
                                              narrower `except
                                              DriverError`: at least one
                                              real, content-triggerable
                                              exception in that subtree --
                                              a poisoned review finding
                                              carrying a lone Unicode
                                              surrogate -- is a raw
                                              UnicodeEncodeError, never a
                                              DriverError). `<TYPE>` is
                                              `type(exc).__name__`;
                                              `error_detail` is the caught
                                              exception's own message,
                                              verbatim -- together they
                                              carry both without needing a
                                              second, richer outcome shape
                                              for every possible cause. NO
                                              terminal ledger write, same
                                              recoverable-next-invocation
                                              story as every other row
                                              here.
      outcome="failed", reason=
        "review-fabricated-loc"           -- a fabricated (inauthentic)
                                              finding recurred on the ONE
                                              retry this driver allows (see
                                              `fabricated_loc_retries`
                                              below) -- terminates with the
                                              template's OWN reason string
                                              (mass-translate-wf.template.js's
                                              matchedVerdict()), never an
                                              invented one, and -- like
                                              every other transient/infra
                                              failure above -- writes NO
                                              terminal ledger entry, so the
                                              segment is picked back up
                                              "recoverable" next invocation
                                              exactly as the template's own
                                              runRound() leaves it.
      outcome="failed", reason=
        "invalid-post-fix-draft"          -- codex round-3 MAJOR: the
                                              draft failed draft_ready_
                                              script/validate_draft_script
                                              AFTER a fix turn edited it
                                              (a review for THIS run+seg
                                              exists and its OWN recorded
                                              draft_sha1 differs from the
                                              current draft's content hash
                                              -- see derive_next_action()'s
                                              own `if not draft_ok:`
                                              branch for the full
                                              discriminator). Deliberately
                                              NEVER re-translated (that
                                              would discard the fix AND
                                              the reviewed draft it was
                                              applied to) and NEVER
                                              re-surfaced as needs_fix
                                              (the old findings describe a
                                              different problem, and this
                                              function has no way to
                                              relay validate_draft_
                                              script's OWN specific
                                              complaint). NO terminal
                                              ledger write, same
                                              recoverable-next-invocation
                                              story as every other row
                                              here -- a human has to look.
      outcome="failed", reason=
        "loop-exhausted-without-
        terminal-state"                   -- the defensive iteration cap
                                              bound below. NOT purely
                                              defensive: reachable (without
                                              the retry bound above) if a
                                              draft keeps changing out from
                                              under a clean review every
                                              single iteration -- see
                                              derive_next_action()'s own
                                              "clean but stale" branch,
                                              which re-reviews at the SAME
                                              round label with no bound of
                                              its own. Kept generic on
                                              purpose: unlike the
                                              fabricated-loc case, this path
                                              has no single template-known
                                              reason to borrow, because it
                                              is not one specific condition
                                              -- it is "nothing else
                                              terminated in time".

      outcome="failed", reason=
        "segment-busy"                    -- --fix-mode=codex only: another
                                              process holds the per-segment
                                              lease, so canonical state cannot
                                              be recovered and read safely
                                              right now. Not a defect and not
                                              terminal; no ledger write.
      outcome="failed", reason=
        "rejected-candidates-exhausted"   -- --fix-mode=codex only: this
                                              numeric round produced
                                              engine.max_rejected_candidates_
                                              per_round candidates that failed
                                              codex_job.py's four gates.
                                              Nothing was published by any of
                                              them (a rejected pair is
                                              quarantined, never promoted), so
                                              no ledger write.
      outcome="failed", reason=
        "stale-redispatch-exhausted"      -- --fix-mode=codex only: the draft
                                              kept changing under a clean
                                              review for this round more times
                                              than the per-round allowance.
                                              This path has no bound of its own
                                              in handoff, where the loop cap is
                                              the bound; codex needs a real
                                              counter because its own per-mode
                                              bound claims a specific one.
      outcome="failed", reason=
        "txn-failures-exhausted"          -- --fix-mode=codex only, and the one
                                              CROSS-INVOCATION bound here: this
                                              segment has accumulated
                                              engine.max_txn_failures_per_
                                              segment refused transactions in
                                              its durable counter. A ceiling on
                                              transaction attempts, NOT durable
                                              terminality -- the segment stays
                                              selectable and will be refused
                                              again next invocation, which is
                                              exactly why no ledger row is
                                              written.
      outcome="failed", stage="publish"   -- --fix-mode=codex only: the pair
                                              validated and staged, but the
                                              transaction refused to publish it
                                              (`reason` is the TXN_* outcome).
                                              Whether anything reached a
                                              canonical name depends on the
                                              outcome; recovery classifies it
                                              on the next pass.

    The iteration cap comes from codex_jobs_per_segment(max_fix_rounds,
    fix_mode) -- see that function for why the two modes get different
    numbers and why the cap and the volume admission are deliberately one
    expression. `fabricated_loc_retries` is a SEPARATE, narrower counter
    (never reusing the loop's own iteration count) so an expected
    condition (a reviewer emitting a fabricated finding, which the
    template's own comment above AUTHENTIC_LOC_RE says a HEALTHY reviewer
    can do) is bounded and reported on its OWN terms, one retry, rather
    than silently spending the whole per-segment budget and then being
    reported as if the defensive backstop itself had fired. The two
    per-round counters (`rejected_candidates`, `stale_redispatches`) exist
    for the same reason and are keyed BY ROUND, not by segment.

    The cap is one MORE than the normal path's job count, and that extra
    iteration is load-bearing rather than padding. Recognizing "the one
    permitted retry ALSO came back fabricated" costs a full extra LOOP
    ITERATION beyond the raw dispatch count -- the retry's own review must be
    DISPATCHED (one iteration) before its result can be RE-READ and classified
    (a SEPARATE, later iteration, even though that one dispatches nothing
    new). At max_fix_rounds=1 the normal path is 3 jobs (translate + review r1
    + the one retry), which is exactly enough budget for the three DISPATCHES
    but leaves no iteration to make the classification -- the loop would hit
    its cap and fall through to the generic "loop-exhausted-without-terminal-
    state" reason on the SAME iteration that should have produced
    "review-fabricated-loc". The segment terminates correctly either way (no
    data loss, no wrong dispatch); only the reported REASON was wrong, quietly
    relabeling an identified, expected condition as the defensive backstop.
    The existing test for this path passed only because its fixture uses
    max_fix_rounds=2, which happens to leave the needed spare iteration; it
    never exercised the boundary.
    """
    max_fix_rounds = ctx.translate_cfg["max_fix_rounds"]
    max_rejected = ctx.translate_cfg.get("max_rejected_candidates_per_round",
                                         DEFAULT_MAX_REJECTED_CANDIDATES_PER_ROUND)
    # DELIBERATELY ctx.fix_mode HERE, NOT effective_fix_mode(ctx, seg)
    # (#409 track B, the size fallback). This sizes the LOOP'S OWN iteration
    # cap for the whole segment, decided ONCE before the loop starts -- and
    # at that point the segment may not even have a draft yet, so its own
    # fixreview estimate is not just unknown but UNKNOWABLE this early.
    # Over-reserving iteration budget for a segment that later falls back to
    # handoff for some or all of its rounds is SAFE: handoff's own
    # per-segment bound (codex_jobs_per_segment's own `base` term) is
    # strictly smaller than codex mode's, so a cap sized for codex mode is
    # never tighter than what a fallen-back round actually needs.
    # Under-reserving would do the opposite -- size the cap for the cheaper
    # mode and then let a segment that stays in codex mode the whole way
    # through exhaust it early. See effective_fix_mode()'s own docstring for
    # the PER-ROUND decision this one-time, per-segment cap does not need to
    # track.
    max_iterations = codex_jobs_per_segment(
        max_fix_rounds, ctx.fix_mode, max_rejected_candidates_per_round=max_rejected)
    fabricated_loc_retries = 0
    # Per-NUMERIC-ROUND, never per-segment: each is a term the per-mode bound
    # multiplies by max_fix_rounds, so pooling them across rounds would let one
    # bad round spend every other round's allowance and still be "within" it.
    rejected_candidates = {}
    stale_redispatches = {}
    for _ in range(max_iterations):
        # codex round-3 BLOCKER, corrected after an initial fix was itself
        # wrong. The worker subtree below `derive_next_action()` (this
        # loop's own decision step) reaches 13 fatal()/raise sites across
        # 6 functions, enumerated by reading every callee, not assumed --
        # named by FUNCTION, deliberately never by line number (a citation
        # is correct when written and silently false the moment anything
        # is inserted above it, exactly the drift this comment itself was
        # caught by once already; a function name survives that insertion,
        # a line number does not):
        # call_template_functions() (missing-template-script,
        # internal-error-unknown-fn, could-not-run-node,
        # node-exited-nonzero, node-did-not-print-valid-JSON);
        # _run_gate() (missing-gate-script, could-not-run-script, called
        # for both draft_ready_script and validate_draft_script);
        # template_harness_source() (truncation-marker-not-found);
        # render_template_source() (internal-error-unknown-token-style,
        # unresolved-{{TOKEN}}); verse_policy_instruction_block()
        # (unknown-mode, missing/invalid-threshold_lines);
        # run_one_codex_job() (round_label-required-for-review, reached
        # before ITS OWN narrower try/except below, which only wraps
        # dispatch_codex_job() specifically).
        #
        # An EARLIER version of this fix caught only `except DriverError`
        # around derive_next_action() alone, on the theory that the
        # per-segment/transient sites should be caught while the
        # seemingly-global ones (missing script, malformed verse_policy)
        # could reasonably keep aborting the whole batch. That theory was
        # wrong on both axes:
        #
        # (1) There is a REAL non-DriverError path. call_template_
        # functions()'s `runner_path.write_text(runner_src,
        # encoding="utf-8")` raises a raw UnicodeEncodeError, not a
        # DriverError, when `runner_src` embeds a lone Unicode surrogate --
        # measured end to end: review.schema.json types findings[].issue/
        # suggest as a bare string with no pattern, json.loads() accepts an
        # UNPAIRED \uD800-shaped escape and decodes it into a Python str
        # holding a genuine lone surrogate code point (confirmed:
        # json.loads('{"x":"\\ud800"}') succeeds), review_ready.py has no
        # pattern to reject it so a review carrying one is promoted
        # normally, and `call_template_functions()`'s own
        # `json.dumps(calls, ensure_ascii=False)` re-serializes that
        # in-memory string WITHOUT escaping it, embedding the raw
        # surrogate into `runner_src` -- which then fails on
        # `.write_text(encoding="utf-8")`. `except DriverError` alone
        # would have let this one specific, real, content-triggerable
        # exception escape and still discard every other segment's
        # result -- proven by test_a_poisoned_review_with_a_lone_
        # surrogate_does_not_discard_other_segments below, and by its own
        # mutation-proof (narrowing back to `except DriverError` there
        # turns it red).
        #
        # (2) Even for the genuinely GLOBAL sites (a missing gate script
        # or a malformed verse_policy really is the identical condition
        # for every segment in this run -- ctx.translate_cfg/ctx.dirs are
        # built once in run() and never rebuilt per segment), catching is
        # STRICTLY BETTER than aborting, not merely "arguably correct":
        # every one of these fires BEFORE that segment's own dispatch, so
        # letting each segment discover the SAME global condition
        # independently costs nothing extra (no wasted codex spend), and
        # with an abort the operator gets one error line and loses the
        # report for every segment that had ALREADY converged and been
        # paid for, whereas with catching they get a complete summary
        # naming the identical reason N times. dispatch_codex_job()'s OWN
        # existing catch below already applies this exact reasoning to ITS
        # missing-script case ("codex_job.py not found", itself just as
        # global) with no preflight in front of it -- this is that same
        # precedent, generalized rather than selectively half-applied.
        #
        # Left uncaught, ANY of the 13 (or any other exception this
        # worker subtree could ever raise) would propagate through this
        # loop -> pool.map() -> run(), discarding every OTHER segment's
        # already-completed result -- the SAME batch-wide-abort class
        # already fixed for dispatch_codex_job() (see that try/except's
        # own comment), which this loop body had NOT been covered by
        # until now, making that comment's "this is the ONE path that
        # broke that discipline" claim false since the moment this file
        # shipped both helpers. Fixed here, not by rewording that claim,
        # so it is accurate again.
        #
        # `except Exception`, not a bare `except:` -- KeyboardInterrupt and
        # SystemExit are NOT Exception subclasses (confirmed:
        # issubclass(KeyboardInterrupt, Exception) is False, both ARE
        # BaseException subclasses), so Ctrl-C and a deliberate exit still
        # propagate through this loop exactly as before; only genuine
        # per-segment worker failures are absorbed.
        try:
            if effective_fix_mode(ctx, seg) == FIX_MODE_CODEX:
                # RECOVERY BEFORE THE READ, UNDER THE LEASE THAT PROTECTS THE
                # WRITE. derive_next_action() is itself the read of canonical
                # state, so recovery cannot be an action it returns -- by then
                # the intermediate state has already been acted on. Both happen
                # inside one lease so nothing can publish between them.
                #
                # Gated on the mode because handoff has no transactions to
                # recover and its control flow is deliberately untouched by
                # this release; taking a lease it never needed would be a
                # behaviour change on the default path.
                #
                # effective_fix_mode(), NOT ctx.fix_mode directly (#409 track
                # B, the size fallback): a segment whose merged fixreview
                # call is unobservable or over budget takes the handoff-style
                # `else` branch below for this WHOLE iteration -- no lease,
                # no recovery, no transaction premise -- exactly as if the
                # run itself were configured --fix-mode=handoff. There is
                # nothing for the lease/recovery machinery to protect for a
                # round this iteration will dispatch as a plain review (or
                # hand off as needs_fix) rather than publish through the
                # transaction layer.
                with segment_lease(ctx.segments_dir, seg) as leased:
                    if not leased:
                        # A codex_job.py child (very likely a predecessor
                        # driver's) holds the lease: the segment is not
                        # actionable now and is not broken. No terminal ledger
                        # write, so it stays recoverable next invocation.
                        return {"seg": seg, "converged": False, "outcome": "failed",
                                "reason": "segment-busy"}
                    recovery = recover_segment_txns(ctx, seg)
                    if recovery_left_the_segment_blocked(ctx, seg, recovery):
                        # Recovery kept an intent it could not resolve, and
                        # publication will refuse to publish over it. Deriving
                        # from here dispatches and PAYS for a round that cannot
                        # land -- every invocation, forever, charging nothing.
                        return {"seg": seg, "converged": False, "outcome": "failed",
                                "reason": "recovery-blocked",
                                "detail": [r.get("outcome") for r in recovery]}
                    # Decided and premised in one bracketed read, inside the
                    # lease -- see decide_under_premise() for why capturing the
                    # premise only afterwards made the guard agree with itself.
                    action, premise = decide_under_premise(ctx, seg)
                if action is None:
                    # An unreadable canonical draft makes the premise
                    # unobservable too, so it lands here BEFORE derive's own
                    # `unreadable_draft` answer can be used -- and retrying it
                    # to the loop cap would report the generic exhaustion
                    # reason for a condition that has a precise name and needs
                    # a human. Named here so both modes say the same thing.
                    if _sha256_of(ctx.segments_dir / f"{seg}.draft.json") == TXN_UNREADABLE:
                        return {"seg": seg, "converged": False, "outcome": "failed",
                                "reason": "unreadable-draft"}
                    # Otherwise: something wrote while the decision was being
                    # read. Nothing was dispatched, so this costs one iteration
                    # of the bounded loop and no codex job; re-derive against
                    # whatever is there now rather than act on a decision whose
                    # premise moved.
                    continue
            else:
                action = derive_next_action(seg, ctx)
                premise = None

            if action["action"] == "already_converged":
                # A review already landed clean+coverage_ok but the convergence
                # ledger write may not have (a prior driver could have died
                # between the two) -- record it now, mechanically. `rounds` is
                # computed from the round_label derive_next_action() just
                # reported (see _ledger_rounds_value()'s own docstring), never
                # re-parsed from the review's own dispatch_token string.
                rounds = _ledger_rounds_value(action["round_label"], ctx.translate_cfg["max_fix_rounds"])
                rec = write_ledger(
                    ctx.dirs, seg, {"status": "converged", "rounds": rounds},
                    run_id=ctx.run_id, needs_cache_key=True,
                    durable_root_str=ctx.durable_root_str, plugin_root_str=ctx.plugin_root_str,
                )
                if not rec.get("success"):
                    return {"seg": seg, "converged": False, "outcome": "failed",
                            "reason": "ledger-write-failed", "detail": rec.get("error")}
                return {"seg": seg, "converged": True, "outcome": "converged"}

            if action["action"] == "cap_reached":
                rec = write_ledger(
                    ctx.dirs, seg, {"status": "non_converged", "reason": "cap"},
                    durable_root_str=ctx.durable_root_str, plugin_root_str=ctx.plugin_root_str,
                )
                if not rec.get("success"):
                    return {"seg": seg, "converged": False, "outcome": "failed",
                            "reason": "ledger-write-failed", "detail": rec.get("error")}
                return {"seg": seg, "converged": False, "outcome": "failed",
                        "reason": "cap", "lastFindings": action.get("findings")}

            if action["action"] == "needs_fix":
                round_label = action["round_label"]
                if ctx.fix_mode == FIX_MODE_CODEX:
                    # #409 track B, the size fallback, made VISIBLE. This
                    # action shape is --fix-mode=handoff's own; a run
                    # genuinely configured codex returns "review" with
                    # cause="merged_fix" for the identical not-clean state
                    # instead (derive_next_action()'s own branch just above
                    # this one). So reaching needs_fix while ctx.fix_mode is
                    # STILL configured codex is itself proof
                    # effective_fix_mode() fell back for this segment/round.
                    _journal_fixreview_size_fallback(ctx, seg, round_label)
                review_obj = _read_review_obj(ctx, seg, fallback_findings=action.get("findings"))
                fix_prompt = render_fix_prompt(ctx, seg, int(round_label), review_obj)
                return {
                    "seg": seg, "converged": False, "outcome": "needs_fix", "reason": "needs_fix",
                    "round_label": round_label, "findings": action.get("findings"), "fix_prompt": fix_prompt,
                }

            if action["action"] == "translate":
                rec = write_ledger(
                    ctx.dirs, seg, {"status": "in_progress"},
                    durable_root_str=ctx.durable_root_str, plugin_root_str=ctx.plugin_root_str,
                )
                if not rec.get("success"):
                    return {"seg": seg, "converged": False, "outcome": "failed",
                            "reason": "ledger-write-failed", "detail": rec.get("error")}
                result = run_one_codex_job(ctx, kind="translate", seg=seg)
                if not result["ok"]:
                    return {"seg": seg, "converged": False, "outcome": "failed", "stage": "translate",
                             "reason": result["reason"], "error_detail": result["error_detail"]}
                continue  # re-derive: should now see "review round 1"

            if action["action"] == "review":
                round_label = action["round_label"]
                # effective_fix_mode(), NOT ctx.fix_mode directly (#409
                # track B, the size fallback) -- see effective_fix_mode()'s
                # own docstring and the lease branch above for why this
                # segment's iteration must be judged against ITS OWN
                # estimate, not the run's configured mode, at every site
                # that acts on ctx.fix_mode.
                effective_mode = effective_fix_mode(ctx, seg)
                if ctx.fix_mode == FIX_MODE_CODEX and effective_mode != FIX_MODE_CODEX:
                    # Made VISIBLE: without this, a downgraded fixreview ->
                    # review dispatch is silent -- the round still converges
                    # normally, just one codex call poorer, and nothing else
                    # in this file's output distinguishes it from an
                    # ordinary handoff-mode review.
                    _journal_fixreview_size_fallback(ctx, seg, round_label)
                kind = _dispatch_kind_for_round(effective_mode, round_label)
                if kind == "fixreview":
                    # EVERY TERMINAL CHECK HAPPENS BEFORE THE DISPATCH, not
                    # after it. Checking afterwards still spends the job whose
                    # spend the bound exists to refuse -- the bound would be
                    # reporting overspend rather than preventing it.
                    if action.get("cause") == "clean_stale":
                        if stale_redispatches.get(round_label, 0) >= MAX_STALE_REDISPATCHES_PER_ROUND:
                            return {"seg": seg, "converged": False, "outcome": "failed",
                                    "reason": "stale-redispatch-exhausted",
                                    "round_label": round_label}
                        stale_redispatches[round_label] = stale_redispatches.get(round_label, 0) + 1
                    # The durable, CROSS-INVOCATION bound, and the only one
                    # here that is: it counts refused transactions in
                    # runs/<RUN_ID>/txn/<seg>.txn_failures, so a segment cannot
                    # be retried forever by relaunching the driver. Deliberately
                    # only on this callsite -- translate is not transactional
                    # and the final round is a plain review, so checking it at
                    # either would refuse legitimate work over transaction
                    # failures those paths cannot cause.
                    if txn_failures_exhausted(ctx.txn_dir, seg, _txn_failure_ceiling(ctx)):
                        return {"seg": seg, "converged": False, "outcome": "failed",
                                "reason": "txn-failures-exhausted",
                                "round_label": round_label}
                if action.get("cause") == "fabricated_loc":
                    if fabricated_loc_retries >= MAX_FABRICATED_LOC_RETRIES:
                        # Already retried once -- the reviewer is persistently
                        # emitting fabricated locs (within its own documented
                        # latitude, not a fault of its own). Terminate NOW,
                        # never dispatch a third time: the template's own
                        # reason, no ledger write (matches runRound()'s own
                        # "blocked" -> recoverable-next-run handling).
                        return {"seg": seg, "converged": False, "outcome": "failed",
                                "reason": "review-fabricated-loc"}
                    fabricated_loc_retries += 1
                result = run_one_codex_job(ctx, kind=kind, seg=seg, round_label=round_label)
                if not result["ok"]:
                    if kind == "fixreview" and result.get("reason") == "validate-failed":
                        # A candidate REJECTED by one of the four gates -- not
                        # an infrastructure failure. Nothing was published and
                        # both candidates are quarantined, so another attempt
                        # at this round is meaningful in a way that retrying a
                        # dead companion is not. Bounded per round, because
                        # "the model keeps emitting a fabricated loc" is a
                        # condition that reproduces rather than resolves.
                        seen = rejected_candidates.get(round_label, 0) + 1
                        rejected_candidates[round_label] = seen
                        if seen >= max_rejected:
                            return {"seg": seg, "converged": False, "outcome": "failed",
                                    "stage": kind, "round_label": round_label,
                                    "reason": "rejected-candidates-exhausted",
                                    "error_detail": result["error_detail"]}
                        continue
                    return {"seg": seg, "converged": False, "outcome": "failed", "stage": kind,
                             "round_label": round_label,
                             "reason": result["reason"], "error_detail": result["error_detail"]}
                if kind == "fixreview":
                    # The pair is validated and staged, and NOTHING has been
                    # published yet: codex_job.py never promotes this kind
                    # (there is no single canonical path for two artifacts).
                    # Publication is this driver's, through the transaction.
                    #
                    # PREMISE MUST BE A REAL TUPLE HERE, and this is checked
                    # explicitly rather than assumed, because it is
                    # REACHABLE, not hypothetical, for it to be None: this
                    # ONE iteration calls effective_fix_mode(ctx, seg) THREE
                    # separate times (the lease-branch check above,
                    # derive_next_action()'s own internal check, and this
                    # `kind` computed a few lines above) -- and each is a
                    # fresh read of the four sized files, not a cached value.
                    # If the lease-branch call read HANDOFF (taking the
                    # `else` branch above, which sets premise = None on
                    # purpose -- a genuine handoff-style iteration has no
                    # transaction premise to carry), and the four files then
                    # changed before the LATER two calls, `kind` can still
                    # come out "fixreview" on a `premise` that was never
                    # captured. publish_fixreview_pair() would in fact refuse
                    # this safely today anyway -- premise_is_observable(None)
                    # reads True (TXN_UNREADABLE not in ()), but its own
                    # `now != premise` compares a real tuple against None and
                    # is never equal -- but that safety is INCIDENTAL to
                    # those two functions' own unrelated logic, not asserted
                    # here. Refusing explicitly, with the SAME reason that
                    # incidental path would have produced, rather than
                    # depending on it staying true if either function's
                    # internals ever change.
                    if premise is None:
                        return {"seg": seg, "converged": False, "outcome": "failed",
                                "stage": "publish", "round_label": round_label,
                                "reason": "txn-decision-stale"}
                    # UNDER THE LEASE, and the child has exited by now (the
                    # dispatch blocks), so it is free to take. The renames
                    # themselves cannot be made atomic -- POSIX has no
                    # compare-and-rename, and publish_txn says so plainly --
                    # but holding the lease across them at least excludes
                    # every writer that DOES honour it, which is every
                    # codex_job.py in the system. It leaves exactly the
                    # writers the lease never reached: a fix agent in another
                    # process, a human editor, a sync daemon.
                    with segment_lease(ctx.segments_dir, seg) as leased:
                        if not leased:
                            # THE CANDIDATES ARE LEFT ALONE HERE, deliberately,
                            # and this is a reversal: an earlier revision
                            # deleted them, on the argument that their random
                            # per-invocation paths make them unreachable
                            # anyway. They are not unreachable -- the dispatch
                            # journal records both paths -- so deleting was
                            # destroying validated, paid-for work that a human
                            # can still recover, to avoid leaving litter. The
                            # trade is not close. What stays is a known leak:
                            # one draft-sized pair per occurrence, traceable
                            # through the journal, with no automatic sweep.
                            return {"seg": seg, "converged": False, "outcome": "failed",
                                    "stage": "publish", "round_label": round_label,
                                    "reason": "segment-busy"}
                        txn = publish_fixreview_pair(ctx, seg, round_label, result, premise)
                    if not txn["ok"]:
                        return {"seg": seg, "converged": False, "outcome": "failed",
                                "stage": "publish", "round_label": round_label,
                                "reason": txn["reason"]}
                continue  # re-derive from the freshly promoted canonical review

            if action["action"] == "unreadable_draft":
                # Terminal for this invocation, in BOTH modes, and with NO
                # ledger write -- the segment stays recoverable and a human
                # has to look at why a file in segments/ cannot be read.
                # Dispatching anything here is what destroys it.
                return {"seg": seg, "converged": False, "outcome": "failed",
                        "reason": "unreadable-draft"}

            if action["action"] == "invalid_post_fix_draft":
                # codex round-3 MAJOR: see derive_next_action()'s own
                # `if not draft_ok:` branch for the full reasoning this
                # action exists to close. Terminates like every other
                # infra/environment failure in this function -- NO
                # terminal ledger write, so the in_progress fragment
                # already on disk stays the durable record and
                # select_segments.py's "recoverable" default retries this
                # segment next invocation -- and deliberately does NOT
                # re-dispatch anything: re-reviewing an invalid draft is
                # pointless (there is nothing new to judge), and
                # re-translating is the exact defect this action exists
                # to prevent.
                return {"seg": seg, "converged": False, "outcome": "failed",
                        "reason": "invalid-post-fix-draft"}

            # Still genuinely unreachable: derive_next_action()'s own return
            # contract (see its docstring) is EXHAUSTIVELY one of the 7 actions
            # checked above (translate/review/needs_fix/cap_reached/
            # already_converged/invalid_post_fix_draft/unreadable_draft), so
            # nothing can reach this line. Unlike the loop-exhaustion fallback
            # below, this one is not made reachable by anything shipped so far.
            #
            # This count is load-bearing and has already been wrong once: the
            # unreadable_draft handler above was added while this comment still
            # said "6 ... nothing in this release added a 7th". Adding an action
            # means editing this line in the same commit.
            return {"seg": seg, "converged": None, "outcome": "failed",
                    "reason": f"unknown-action:{action['action']}"}  # pragma: no cover
        except Exception as exc:
            return {"seg": seg, "converged": False, "outcome": "failed",
                    "reason": f"unexpected-error:{type(exc).__name__}", "error_detail": str(exc)}

    # Reachable (not purely defensive) -- see this function's own docstring
    # for the "clean but stale" scenario that can drive it: a draft edited
    # out-of-band on EVERY iteration never lets a clean review's sha1
    # catch up, so derive_next_action() keeps returning a same-label
    # "review" re-dispatch (no cause="fabricated_loc" marker, so the retry
    # bound above never engages) until this loop's own iteration cap.
    return {
        "seg": seg, "converged": False, "outcome": "failed",
        "reason": "loop-exhausted-without-terminal-state",
    }


def _ledger_rounds_value(round_label: str, max_fix_rounds: int) -> int:
    """The ledger's own `rounds` field for a converged write -- a REQUIRED
    plain integer, per ledger-record-base.schema.json:15 (`"rounds":
    {"type": "integer"}`) and that same schema's allOf block (:78-86),
    which requires `rounds` outright whenever `status == "converged"`.
    `null` satisfies neither.

    Mirrors mass-translate-wf.template.js's own runRound(seg, round,
    isFinal) exactly (template.js:1574): `recordLedgerCall(seg, {status:
    "converged", rounds: round, ...})` (template.js:1595-1596) always
    writes the NUMERIC loop variable `round`, never a value derived from
    the "final" round LABEL -- and on the mandatory final call that
    variable is `MAXFIX + 1` (`runRound(seg, MAXFIX + 1, true)`,
    template.js:1757). So round_label == "final" -> max_fix_rounds + 1;
    every other round_label is already the decimal round number as a
    string and converts directly. This REPLACES the former
    `_round_number()`, which parsed the trailing digit out of a
    dispatch_token string and returned None for a "...:rfinal" token --
    silently writing `rounds: null` on every final-round convergence, a
    write ledger_update.py/its schema then rejects twice over. Never call
    this with a round_label this function cannot classify; the caller
    (derive_next_action()) is the sole source of round_label values, and
    every one it produces is either "final" or a decimal string."""
    if round_label == "final":
        return max_fix_rounds + 1
    return int(round_label)


# ---------------------------------------------------------------------------
# Phase 2 -- the concurrency-bounded per-segment loop. codex round-3: the
# module docstring has no "Concurrency" section (its `##` headings are Why a
# local process/Launch contract/The 8 mandatory safety properties/Property 4
# in detail/Property 7 in detail/What this driver deliberately does NOT
# implement/Beyond the 8 named properties/Bundle registration/CLI) -- see
# run_segment_loop()'s own docstring below for the threads-vs-async
# reasoning behind the knob choice.
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
            "draft_ready.py, validate_draft.py) and the "
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
            "explicit, overridable knob instead of an accident. See "
            "run_segment_loop()'s own docstring for the threads-vs-async "
            "reasoning behind the knob."
        ),
    )
    parser.add_argument(
        "--node",
        default="node",
        metavar="BIN",
        help="Node binary to invoke for both codex_job.py's own launches and this driver's template-execution harness. Default 'node' (resolved via PATH).",
    )
    parser.add_argument(
        "--fix-mode",
        choices=list(FIX_MODES),
        default=FIX_MODE_HANDOFF,
        help=(
            "Who performs the fix on a not-clean NUMERIC round. 'handoff' (default) "
            "reproduces 1.18.0: the driver stops at that segment and returns needs_fix "
            "with the rendered fix prompt for the caller to run as an agent turn. "
            "'codex' dispatches --kind fixreview instead -- one codex call producing "
            "both a fixed draft and its review -- and publishes the pair through the "
            "durable-intent transaction. The FINAL round is a plain review in both "
            "modes; there is no fix after it."
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

    lock_fd = acquire_driver_lock(durable_root, session_id=session_id)
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
        segs, duplicate_segs = _dedupe_segs(segs)
        if duplicate_segs:
            # #392 (codex, round 2): manifest.json's segments[] carries no
            # uniqueItems constraint (manifest.schema.json), and
            # select_segments.py's default (non---only-segs) manifest path
            # appends every entry with no dedupe of its own -- unlike the
            # mass-translate-wf.template.js Workflow template, which refuses
            # a duplicate outright with an explicit `seen` set (template.js:
            # 536-541). Without this, pool.map() would drive the SAME
            # segment on two worker threads at once: two codex_job.py
            # dispatches racing for the same per-segment flock lease, two
            # ledger writes, two entries in the argv/journal log -- silent
            # duplicate work, never a crash. Deduped here (first occurrence
            # wins, order preserved) rather than in select_segments.py
            # itself, which this dispatch does not own.
            append_journal(
                durable_root, session_id,
                {"type": "duplicate_segs_dropped", "duplicates": duplicate_segs},
            )
        append_journal(
            durable_root, session_id,
            {"type": "step1_gate_passed", "segs": segs, "counts": select_result.get("counts")},
        )

        engine_cfg = load_engine_config(durable_root)
        # Admission is checked against the SAME per-segment bound the loop is
        # capped at, for the SAME mode -- see codex_jobs_per_segment(). Passing
        # handoff's number while running codex would admit a batch its own loop
        # is entitled to overspend several times over.
        fix_mode = getattr(args, "fix_mode", FIX_MODE_HANDOFF)
        volume_refusal = check_volume_cap(
            len(segs), engine_cfg["max_fix_rounds"], engine_cfg["max_codex_jobs_per_batch"],
            fix_mode,
            max_rejected_candidates_per_round=engine_cfg["max_rejected_candidates_per_round"],
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
                "estimatedCodexJobs": len(segs) * codex_jobs_per_segment(
                    engine_cfg["max_fix_rounds"], fix_mode,
                    max_rejected_candidates_per_round=engine_cfg["max_rejected_candidates_per_round"]),
                "codexJobsCap": engine_cfg["max_codex_jobs_per_batch"],
                "fixMode": fix_mode,
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
        # TWO INDEPENDENT READS OF ONE FILE, AND THE BOUND LIVES IN BOTH.
        # Admission (engine_cfg, above) and execution (translate_cfg, here)
        # each open profile.yml. An edit landing between them lets the smaller
        # number admit the batch while the loop runs on the larger one --
        # exactly the "admission and cap are one number" property this release
        # rests on, defeated by a file that moved. Refusing is right and
        # cheap: nothing has been dispatched yet, and a profile edited
        # mid-startup is a state the operator wants to know about, not one to
        # silently pick a winner for.
        disagreement = profile_snapshots_disagree(engine_cfg, translate_cfg)
        if disagreement is not None:
            append_journal(durable_root, session_id,
                           {"type": "profile_changed_during_startup", **disagreement})
            fatal(disagreement["message"], exit_code=2,
                  reason="profile-changed-during-startup")
        run_result = resolve_run_id(
            dirs, translate_cfg=translate_cfg,
            plugin_root_str=args.plugin_root, durable_root_str=args.durable_root,
        )
        run_id = run_result["effectiveRunId"]
        append_journal(
            durable_root, session_id,
            {"type": "run_id_resolved", "run_id": run_id, "resume": run_result.get("resume")},
        )

        companion_path = resolve_companion_path(dirs, node_bin=args.node)

        ctx = DispatchContext(
            dirs=dirs, run_id=run_id, translate_cfg=translate_cfg, companion_path=companion_path,
            durable_root_str=args.durable_root, plugin_root_str=args.plugin_root,
            node_bin=args.node, session_id=session_id,
            fix_mode=fix_mode,
        )

        append_journal(
            durable_root, session_id,
            {"type": "dispatch_loop_started", "segs": segs, "max_concurrent_codex_jobs": args.max_concurrent_codex_jobs},
        )
        segment_results = run_segment_loop(segs, ctx, args.max_concurrent_codex_jobs)
        # codex round-2 follow-up: partition on the explicit `outcome`
        # field process_segment() now stamps on every result it returns
        # (see that function's own docstring), never on independent
        # predicates over `converged`/`reason` -- three overlapping
        # filters that happened to be disjoint today is exactly what let
        # a `converged: None` result vanish from every bucket while still
        # having consumed real spend. `_KNOWN_OUTCOMES` plus the fatal()
        # below make that structurally impossible now: a future
        # process_segment() result shape that forgets to set `outcome` (or
        # sets one nobody added a bucket for) is refused LOUDLY here,
        # never silently dropped.
        _KNOWN_OUTCOMES = ("converged", "needs_fix", "failed")
        converged = [r for r in segment_results if r.get("outcome") == "converged"]
        needs_fix = [r for r in segment_results if r.get("outcome") == "needs_fix"]
        failed = [r for r in segment_results if r.get("outcome") == "failed"]
        unaccounted = [r for r in segment_results if r.get("outcome") not in _KNOWN_OUTCOMES]
        if unaccounted:
            fatal(
                f"internal error: {len(unaccounted)} segment result(s) carry an unrecognized "
                f"or missing 'outcome' field and cannot be placed in any summary bucket -- "
                f"refusing to silently drop them: {unaccounted}",
                exit_code=2,
            )

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
                "converged": [r["seg"] for r in converged],
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
