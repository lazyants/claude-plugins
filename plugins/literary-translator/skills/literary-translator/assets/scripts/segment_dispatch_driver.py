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

That redirect is NOT a progress log, and no flag makes it one. Stdout
carries exactly ONE JSON line, printed on `main()`'s terminal path once
the run is over (see the exit-code paragraph at the end of this
docstring); this script emits no per-segment progress on stdout at all,
by design, so for the whole run that file holds nothing but what this
script writes to STDERR: the warnings below, and select_segments.py's own
stderr relayed verbatim (_relay_selector_stderr(), #551 -- which is how the
Step 1 gate's disclosures reach an operator on this path at all, including
its routine requested/emitted line). The live progress and liveness
channel is the append-only
journal (Property 5 below) at `runs/<SESSION_ID>/driver_journal.jsonl`,
flushed and fsynced per entry and opening with a `driver_started` entry
carrying this process's pid -- where that `<SESSION_ID>` is the one this
driver generates for itself, never the caller-chosen label in the
redirect above.

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
   BEFORE hygiene runs, not after); what does not exist ANYWHERE in
   `codex_job.py` is a PROJECT-level lock -- it only ever leases one
   segment at a time, never the project as a whole. `acquire_driver_lock()`
   below is what adds that, and it is itself the reason `git grep -rn
   LOCK_EX` across `assets/scripts/*.py` now finds THREE call sites, not
   one: `codex_job.py`'s per-segment lease, plus this function's own
   acquisition and its startup self-test probe (see `acquire_driver_lock()`'s
   own docstring for why the self-test exists). Without a project-level
   lease, two
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
`estimatedCodexJobs = len(SEGS) * CODEX_JOBS_PER_SEG`), because it is the
SAME resource under the SAME cap, just measured from a second, independent
entry point -- exactly how `skeptic_setup.py`'s own preflight duplicates
its Workflow template's estimator for the identical reason (two entry
points into one resource, each needing its own gate).

It is the same formula for the population the template can have, and #514
split off the one this driver can have that the template cannot. An id
this invocation admitted a claim for is charged
`codex_jobs_per_claimed_segment()` -- `max_fix_rounds + 1`, the reviews
alone -- because `claim_capability_refusal_for_translate()` refuses a
translate for it unconditionally, so the template's translate job is not
merely improbable there, it is undispatchable. The template needs no
equivalent: it has no notion of a claim, its `SEGS` come straight from
`args`, and `pipeline(SEGS, translateStage, reviewFixLoop)` puts every id
through the translate stage, so `max_fix_rounds + 2` remains its correct
worst case. This driver never runs the template's own two cap preflights
in any case -- `template_harness_source()` truncates the substituted
source before `function draftProbePrompt(`, which sits above both of their
top-level `return` statements.
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
        [--allow-empty] [--from-cap SEG1,SEG2,...] [--from-converged SEG1,SEG2,...]
        [--from-stalled SEG1,SEG2,...]
        [--max-concurrent-codex-jobs N] [--node BIN]

Forwards `--only-segs`/`--allow-retranslate-converged`/`--allow-empty`/
`--from-cap`/`--from-converged`/`--from-stalled` verbatim to
`select_segments.py` -- see that script's own module docstring for their
exact semantics; this script adds no independent meaning to any of them.
`--from-cap`/`--from-converged`/`--from-stalled` (#438, #455) request
claim admission for the named ids from the Step 1 gate call, and that
admission is SINGLE-PHASE: the one `select_segments.py` invocation this
driver makes both validates the ids and, for every id that passes,
writes the durable claim record and re-stamps the draft's own
`dispatch_token`. There is no separate commit call, here or anywhere
else -- an earlier revision of the design split admission into
validate/commit and was abandoned; nothing in this file drives a second
phase, and no future caller should be built expecting one. Because the
write is part of THAT call, the claim needs a RUN_ID before it, so
`run()` resolves the run id BEFORE selection whenever a claim flag is
present and forwards it as `--run-id`/`--run-resume` (see
`run_select_segments()` and `run()`'s own call site for the #409 Step 3
ordering property that makes resolving early safe). Whatever
`select_segments.py` reports back in its own `claims` field is read back,
validated (see `parse_claims_field()`), and folded into the dispatch
context -- fatal on anything missing, malformed, or mismatched (#438 D3).
Its sibling `claims_admitted_via` field is validated in the same
fail-closed way (see `parse_claims_admitted_via()`) and journaled beside it: `claims` reports
the DURABLE record, which on a re-claim inside one run id was written at
that run id's first claim, while `claims_admitted_via` reports the gate
THIS invocation admitted under (#545/#549). Report-only -- nothing gates
on it.
A third sibling, `claims_from_cap_over_sentinel` (#536), names the ids
--from-cap admitted over a PRESENT `.ever_converged` sentinel. It is
validated (see `parse_claims_from_cap_over_sentinel()`, which also owns
why it is required only under `--from-cap`) and journaled into
`step1_gate_passed`, which is the point: the selector announces that
admission on its own stderr and nowhere else, so before this the fact
survived no run. This driver adds the DURABLE record, not a second
announcement -- relaying the selector's stderr is #551's job, and a
driver-side re-print of a line the relay already carries would put the
same fact in the run log twice.
`--from-stalled` additionally carries `--driver-lease-held` forward to
`select_segments.py` whenever at least one id is requested under it, but
only ever on this code path -- run after `acquire_driver_lock()` has
already returned -- see `run_select_segments()`'s own docstring and its
call site in `run()` for exactly what that flag asserts and why an
ordinary dispatch (no `--from-stalled` id) never sends it.
`--max-concurrent-codex-jobs` (default 40) and `--node` are this driver's
own -- see `build_arg_parser()`'s own help text for the concurrency
default's justification.

`--resume-from-run-id RUN_ID` (#458) is this driver's own too, and it
changes only WHICH prior run is offered to `resume_setup.py` -- never
whether resuming is safe, which stays that script's sole decision. It
exists because resolution is otherwise newest-match-wins over a list this
driver builds itself, so a prior run sharing a digest with a newer one
cannot be named at all, and an invocation matching no candidate mints a
fresh RUN_ID and claims every selected segment under it. Under a pin the
driver refuses (exit 1) rather than dispatching under a run the operator
did not name: when `runs/RUN_ID` is not a directory or carries no regular
`input.digest`, when the pinned run's digest does not match this
invocation, and when a SELECTED segment's draft is stamped for a
different run (`refuse_run_over_foreign_drafts()`). An unsafe id,
or a filesystem state this script could not establish, is exit 2 instead.
When the flag is ABSENT resolution itself is unchanged, in every respect
but one: an unpinned invocation that mints a fresh RUN_ID says so on
stderr, with the number of ELIGIBLE candidates that were offered (a number
no other artifact carries -- `"resume": false` itself already reaches both
the printed JSON and the journal). The foreign-draft refusal is NOT
pinned-only, though: since #742 an unpinned invocation refuses too (exit 1)
when a selected segment whose classification is not in
`FOREIGN_DRAFT_GATE_EXEMPT_CATEGORIES` carries a draft stamped for another
run -- naming every affected id and its owner. A resolved run id can differ
from a draft's owner without any pin (a fresh mint, or a resume that
matched a NEWER candidate), and re-translating such a draft destroys
editorial work already on disk.

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
import hashlib
import importlib.util
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import NoReturn

# Importing a sibling module writes scripts/__pycache__/*.pyc. Several
# entrypoints here promise not to write anything (cache_key.py) or promise ZERO
# filesystem writes in dry-run (backfill_resume_gate_ack.py), so the whole set
# opts out uniformly rather than case by case.
sys.dont_write_bytecode = True


# --- the shared one-line JSON serialiser (#369) -----------------------------
# Loaded by EXACT PATH, never `import json_stdout`. A bare sibling import
# resolves through the global sys.modules cache regardless of which staged copy
# the CALLER intended, so one process that stages several durable roots would
# bind the FIRST root's copy for all of them. exec_module() opens this file's
# own sibling or raises -- the loud failure the staging discipline depends on,
# and it needs no cache eviction to get there. `Path(__file__).absolute()`
# rather than `.resolve()`: the unresolved form is what lets a caller's own
# no-follow symlink logic still see the path it was handed.
# (`importlib.util` is already imported at the top of this module.)

_JSON_STDOUT_PATH = Path(__file__).absolute().parent / "json_stdout.py"
try:
    _json_stdout_spec = importlib.util.spec_from_file_location(
        "json_stdout", _JSON_STDOUT_PATH
    )
    if _json_stdout_spec is None or _json_stdout_spec.loader is None:
        raise ImportError(f"no loader for {_JSON_STDOUT_PATH}")
    _json_stdout = importlib.util.module_from_spec(_json_stdout_spec)
    # OSError, not ImportError alone: spec_from_file_location() happily builds a
    # spec for a file that is not there, and it is exec_module() that raises
    # FileNotFoundError when it opens the source.
    _json_stdout_spec.loader.exec_module(_json_stdout)
except (ImportError, OSError) as _json_stdout_exc:  # pragma: no cover - staging error path
    sys.exit(
        f"segment_dispatch_driver.py: cannot load json_stdout.py from {_JSON_STDOUT_PATH} "
        f"({_json_stdout_exc}).\n"
        "json_stdout.py must be installed alongside segment_dispatch_driver.py under "
        "${durable_root}/scripts/ -- Step 0a's copy pass places it there."
    )

dumps_line = _json_stdout.dumps_line

try:
    import yaml
except ImportError:
    yaml = None

# ---------------------------------------------------------------------------
# Self-anchoring -- identical convention to select_segments.py/ledger_merge.py,
# EXCEPT for `.absolute()` in place of `.resolve()` (both those sibling files
# still use `.resolve()`, unchanged by this release -- see this file's own
# `resolve_dirs()` docstring for why `.resolve()` cannot be used for any path
# `_open_regular_no_follow_walk()` will later verify: `.resolve()` follows
# every ancestor symlink and hands the walk an already-canonicalized target,
# so it never sees the symlink it exists to refuse). `SCRIPTS_DIR` feeds
# `_self_anchored_template_path()`, which feeds the same walk for the
# self-anchored (no `--plugin-root`) branch -- so it needs the identical
# treatment, not just the `--plugin-root` branch in `resolve_dirs()` below.
# `.absolute()` makes `__file__` absolute (joins it against this process's
# CWD if it is relative) WITHOUT touching the filesystem at all: no symlink
# is ever followed, and no `..`/`.` component is collapsed -- a literal `..`
# in the resulting path is still safe, because `_open_regular_no_follow_walk()`
# opens it as an ordinary directory ENTRY (never a symlink) via the kernel's
# own directory structure, not by lexically guessing its target.
#
# `SCRIPTS_DIR` ITSELF never had the "intermediate directory unchecked"
# gap -- worth saying explicitly, because "the root check was fixed"
# reads as if both branches were equally broken and only one was.
# `SCRIPTS_DIR` sits
# DIRECTLY at the `assets/scripts` level (this file's own `__file__` IS
# in `assets/scripts/`), so there is no separate "assets" component for
# the self-anchored branch to skip over the way `--plugin-root`'s
# `plugin_root / "assets" / "scripts"` construction could. What the
# self-anchored branch genuinely lacked, same as --plugin-root, was
# verification of the LEAF filename itself -- `_refuse_unless_executable_
# leaf()` (below) is what closes that, for both branches identically,
# not a second root-level fix.
# ---------------------------------------------------------------------------

SCRIPTS_DIR = Path(__file__).absolute().parent
DURABLE_ROOT = SCRIPTS_DIR.parent
SELECT_SEGMENTS_SCRIPT = SCRIPTS_DIR / "select_segments.py"
CODEX_JOB_SCRIPT = SCRIPTS_DIR / "codex_job.py"

DRIVER_LOCK_NAME = ".driver.lock"

# Phase 2 -- every additional sibling this script shells out to for the real
# per-segment loop, beyond the two the skeleton already resolved. Every name
# here is Step-0a-copied to ${durable_root}/scripts/ exactly like
# select_segments.py/codex_job.py, so it gets the identical --plugin-root
# redirect treatment in resolve_dirs() below -- one table, not six near-
# duplicate if/else blocks.
#
# resolve_codex_companion.py belongs here, and it was NOT always true that
# it did. SKILL.md's own Step 0a copy-pass section used to exclude it,
# fourth alongside profile_validate.py/validate_extraction.py/
# glossary_preflight.py, on the claimed reason that a durable copy "could
# not glob the plugin's own install locations to find the newest installed
# codex-companion.mjs". That reason was false: resolve_codex_companion.py
# reads no `__file__` -- its own location never enters its search -- and
# imports nothing plugin-specific; its DEFAULT search is rooted at the
# RUNNING Claude config profile (`$CLAUDE_CONFIG_DIR`, else `~/.claude`)
# and then at `os.path.expanduser("~")` against
# `~/.claude*/plugins/cache/openai-codex/**/codex-companion.mjs`, a
# DIFFERENT plugin's own install cache, found identically regardless of
# where resolve_codex_companion.py itself happens to be running from. This
# is mechanically pinned by
# tests/resolve_codex_companion.test.py::test_the_resolver_contains_no_executable_reference_to_dunder_file
# (parses the file with `ast`, flags only a genuine executable reference,
# never a prose mention) -- see SKILL.md's own Step 0a section for the
# full disproof and the corrected copy-pass rule.
#
# Before this was corrected, the documented default launch --
# `nohup python3 {durable_root}/scripts/segment_dispatch_driver.py ...`,
# no --plugin-root, exactly as SKILL.md instructs -- could not complete a
# single dispatch: dirs["resolve_codex_companion_script"] resolved to a
# path Step 0a never created, and resolve_companion_path()'s own
# `script.is_file()` check fataled (exit_code=2) before any segment got a
# prompt rendered. Do not re-exclude this script by re-deriving the same
# plausible-but-wrong glob argument -- check the file's own source for
# `__file__` first.
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


def _template_candidate_state(path: Path) -> str:
    """Tri-state verdict for ONE template candidate path, using `os.lstat()`
    -- never `Path.is_file()`. `is_file()` FOLLOWS a valid symlink and
    reports True for it exactly like a real regular file, and it collapses
    every lookup failure (permission denied on an ancestor directory,
    ELOOP, ENOTDIR, ...) to a bare False, indistinguishable from genuine
    absence. Resolving the prompt-building template is not a cosmetic
    layout question: `call_template_functions()` dynamically imports and
    EXECUTES whatever this path resolves to
    (mirrors codex_job.py's own `_is_regular()`/`_clear_nonregular()`
    lstat-based discipline for the identical reason, applied here to a
    lookup rather than a write-slot). Collapsing "a symlink to something
    attacker-controlled" or "could not tell" into the same signal as
    "nothing is there" would let a planted or tampered file silently become
    the driver's authority for every prompt this file renders.

    Returns one of:
      "absent"     -- os.lstat() raised FileNotFoundError: the namespace
                       positively says there is no entry at `path`.
      "file"       -- a real, non-symlink regular file. The only state a
                       caller may treat as a usable authority.
      "suspicious" -- anything else: a symlink (even one that resolves to a
                       genuine file), a directory, or any other lookup
                       failure. The caller must refuse, not guess."""
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return "absent"
    except OSError as exc:
        print(
            f"segment_dispatch_driver.py: warning: could not stat {path}: {exc}; "
            f"treating the template candidate as suspicious",
            file=sys.stderr,
        )
        return "suspicious"
    if stat.S_ISREG(st.st_mode):
        return "file"
    return "suspicious"


def _self_anchored_template_path():
    """Where the prompt-building template lives relative to THIS file, across
    the two layouts a self-anchored (no --plugin-root) driver can actually be
    running from. They are not the same directory, and there is no single
    path that names both:

      * a DEPLOYED durable root -- Step 0a's copy pass places every bundle
        member FLAT at ${durable_root}/scripts/<name>, the .template.js
        workflow template exactly like the .py gates. A real deployed root
        has no templates/ directory at all, and the one that matters here is
        ${durable_root}/templates/ -- that is what `SCRIPTS_DIR.parent /
        "templates"` (the checkout candidate below) resolves to when this
        file runs from a durable root, so THAT is the absence to verify, not
        a scripts/templates/ nobody ever names. So the template sits BESIDE
        this file.
      * this PLUGIN checkout -- assets/scripts/ and assets/templates/ are
        siblings, and the template sits ONE DIRECTORY OVER.

    A hardcoded guess at either layout alone is wrong for the other: naming
    only the checkout shape (SCRIPTS_DIR.parent / "templates", what this
    function replaces) leaves a deployed, self-anchored driver invocation --
    the one SKILL.md's own documented launch command produces -- unable to
    find the template at all. Naming only the deployed shape would break
    every self-anchored test that runs straight out of this checkout.

    Nor can the two candidates simply be tried in a fixed order and the
    first winner trusted: this function selects EXECUTABLE AUTHORITY, not
    merely a layout (call_template_functions() dynamically imports whatever
    it returns), so a stray or planted file at either path is an ambiguity
    to refuse, not a tiebreak to resolve silently. Using
    _template_candidate_state()'s lstat-based tri-state verdict instead of
    is_file():
      * BOTH candidates non-absent (in any state) -> fatal(). There is no
        principled way to prefer one from inside this function.
      * exactly one candidate is a genuine regular file ("file") and the
        other is absent -> return the file.
      * the one non-absent candidate is "suspicious" (symlink, directory, or
        an unreadable/unlookupable entry) -> fatal(). Falling through to
        treat it as though it were absent would be exactly the silent guess
        this function exists to refuse.
      * BOTH candidates absent -> return the deployed path, unchanged from
        the previous behavior's failure mode: the caller's own "could not
        read <path>" is the useful error, and naming the durable root points
        at the layout a real deployment has to satisfy."""
    deployed = SCRIPTS_DIR / _TEMPLATE_NAME
    checkout = SCRIPTS_DIR.parent / "templates" / _TEMPLATE_NAME
    deployed_state = _template_candidate_state(deployed)
    checkout_state = _template_candidate_state(checkout)

    if deployed_state != "absent" and checkout_state != "absent":
        fatal(
            f"ambiguous prompt-template authority: both {deployed} "
            f"({deployed_state}) and {checkout} ({checkout_state}) exist -- "
            f"refusing to guess which is authoritative; remove the stale one",
            template_deployed=str(deployed),
            template_deployed_state=deployed_state,
            template_checkout=str(checkout),
            template_checkout_state=checkout_state,
        )
    if deployed_state == "file":
        return deployed
    if deployed_state == "suspicious":
        fatal(
            f"prompt-template candidate at {deployed} is not a genuine "
            f"regular file (symlink, directory, or unreadable) -- refusing "
            f"to guess its authority",
            template_path=str(deployed),
            template_state=deployed_state,
        )
    if checkout_state == "file":
        return checkout
    if checkout_state == "suspicious":
        fatal(
            f"prompt-template candidate at {checkout} is not a genuine "
            f"regular file (symlink, directory, or unreadable) -- refusing "
            f"to guess its authority",
            template_path=str(checkout),
            template_state=checkout_state,
        )
    return deployed


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

    THE TWO BRANCHES ARE NOT SYMMETRIC FOR THE TEMPLATE. The --plugin-root
    branch can name assets/templates/ outright, because it is told which
    plugin install to trust. The self-anchored branch cannot name any
    single directory, because the two trees it might be running from put
    the template in different places -- see _self_anchored_template_path()'s
    own docstring before changing either branch."""
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
        template_script = _self_anchored_template_path()
        scripts_dir = SCRIPTS_DIR
    else:
        if not plugin_root_str.strip():
            # An unset {{PLUGIN_ROOT}} template substitution renders as the
            # empty string, not as the flag being omitted -- and
            # Path("").absolute() (like Path("").resolve()) is CWD, silently
            # making wherever this process happens to be launched from the
            # executable authority for the template AND every sibling
            # script. codex_job.py:1436 already refuses this same input;
            # refusing it here too, before any path is built from it, keeps
            # both scripts consistent instead of one being the loophole.
            fatal(
                "--plugin-root was given but is empty/whitespace-only -- "
                "this usually means a {{PLUGIN_ROOT}} template substitution "
                "did not happen. Omit the flag entirely for today's "
                "self-anchored behavior, or pass a real path.",
                exit_code=2,
            )
        # `.absolute()`, never `.resolve()`: see this module's own
        # SCRIPTS_DIR comment above for why -- `.resolve()` would follow
        # every ancestor symlink in `plugin_root_str` before
        # `_refuse_unless_executable_leaf()` (each artifact's own point-
        # of-use check, below its own Popen site, not here) ever gets a
        # chance to refuse one, silently narrowing "refuses a symlink
        # anywhere on the path" down to "anywhere below whatever
        # --plugin-root already resolved to".
        plugin_root = Path(plugin_root_str).absolute()
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
        # plugin roots. `.absolute()`, matching resolve_dirs() exactly (see
        # its own comment on the same line for why never `.resolve()`),
        # reproduces the identical computation resolve_dirs() already
        # performed (same string, same unchanged process cwd), never a
        # second, independent answer -- and never a canonicalized one that
        # would disagree with what this driver's own no-follow walk on
        # `dirs["template_script"]` just verified.
        args += ["--plugin-root", str(Path(plugin_root_str).absolute())]
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
# validate_seg() (select_segments.py:904-915, the regex check at :911) --
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
    happens to be first on sys.path rather than THIS resolved sibling).

    Not tracked in `resolve_dirs()`'s own `dirs` dict -- `draft_sha1.py`
    is resolved here, independently, against whatever `scripts_dir` the
    caller passes (always `dirs["scripts_dir"]` in practice; see
    `current_draft_sha1()` below) -- so it needs its OWN full-path
    no-follow verification, at its own point of use, the same shape
    `_refuse_unless_executable_leaf()` gives every subprocess-executed
    sibling at ITS point of use. This one matters MORE than a
    subprocess-executed sibling, not less: `exec_module()` runs
    `draft_sha1.py`'s own top-level code INSIDE this process, with this
    process's own privileges -- no subprocess boundary at all.

    Deliberately verify-then-reopen-by-path, exactly like every
    subprocess-executed sibling, rather than reading the verified fd's
    bytes and `exec()`-ing them directly: `draft_sha1.py` computes
    `DURABLE_ROOT = Path(__file__).resolve().parents[1]` at module level,
    and `importlib`'s own loader is what correctly sets `__file__` before
    that line runs. Hand-rolling an exec-from-bytes loader would mean
    reproducing exactly what `spec_from_file_location()` already does
    right, with a real chance of getting `__file__` subtly wrong -- a
    correctness bug hiding inside a security fix, worse than the narrow,
    disclosed TOCTOU residual this leaves (see `call_template_functions()`'s
    own docstring for that residual's exact shape -- the same one every
    verify-then-reopen-by-path artifact in this file leaves, the template
    being the one exception, since it alone reads through the SAME
    verified fd rather than reopening)."""
    path = scripts_dir / "draft_sha1.py"
    verified_fd, verified_state = _open_regular_no_follow_walk(path)
    if verified_fd is not None:
        os.close(verified_fd)
    if verified_state != "file":
        fatal(
            f"draft_sha1.py at {path} is not usable (state={verified_state}) "
            f"-- refusing to import an executable that is not reachable "
            f"without following a symlink somewhere on the way",
            exit_code=2, artifact_path=str(path), artifact_state=verified_state,
        )
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
# #438 D4/D8 -- the shared claim_record.py sibling, loaded the SAME
# verify-then-reopen-by-path way _load_draft_sha1_module() loads
# draft_sha1.py, and for the identical reason: this driver must never
# trust whatever claim_record.py happens to sit beside its own execution
# location via a bare `import claim_record` -- that would resolve against
# sys.path[0] (this PROCESS's own physical directory) even when
# dirs["scripts_dir"] names a different, trusted --plugin-root tree, the
# same self-anchored-vs-redirected split every other in-process-executed
# sibling in this file already accounts for. claim_record.py's own module
# docstring calls this driver one of its two documented readers (the
# other is select_segments.py's own admission check) and a third,
# independently hand-rolled presence predicate is exactly the drift shape
# the 1.19.1 sentinel bug came from -- sharing the import, not just the
# convention, is what a drift test can actually pin.
# ---------------------------------------------------------------------------


def _load_claim_record_module(scripts_dir: Path = SCRIPTS_DIR):
    """Loads the real sibling claim_record.py via importlib. See this
    section's own comment above for why never a bare `import
    claim_record`."""
    path = scripts_dir / "claim_record.py"
    verified_fd, verified_state = _open_regular_no_follow_walk(path)
    if verified_fd is not None:
        os.close(verified_fd)
    if verified_state != "file":
        fatal(
            f"claim_record.py at {path} is not usable (state={verified_state}) "
            f"-- refusing to import an executable that is not reachable "
            f"without following a symlink somewhere on the way",
            exit_code=2, artifact_path=str(path), artifact_state=verified_state,
        )
    spec = importlib.util.spec_from_file_location("claim_record", str(path))
    if spec is None or spec.loader is None:
        fatal(f"could not load claim_record.py from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _foreign_claim_refusal_for_translate(ctx, seg, claim_record_mod) -> "str | None":
    """D8's cross-run half, DELEGATED to claim_record.py's shared
    foreign_owner_refusal() rather than decided here.

    The delegation is the point. This guard and codex_job.py's own D8
    chokepoint each hand-rolled "is this segment claimed?" against their OWN
    run id, and both read "I have not claimed this" as "nobody has" -- three
    consecutive BLOCKERs of one shape. A second local implementation here, even
    a correct one, would leave the fourth chokepoint free to repeat it. The
    predicate lives in the module both readers already import, so agreeing with
    the selector's notion of ownership is structural rather than a convention
    to be re-honoured at each site.

    See foreign_owner_refusal() for every case and the reasoning behind each
    direction -- in particular why a tokenless draft consults the claim records
    (D9's lost-token state) while a TOKENED one never enumerates them."""
    return claim_record_mod.foreign_owner_refusal(
        seg=seg,
        this_run_id=ctx.run_id,
        draft_path=ctx.dirs["durable_root"] / "segments" / f"{seg}.draft.json",
        runs_dir=ctx.dirs["runs_dir"],
    )


def claim_refusal_for_translate(ctx: "DispatchContext", seg: str) -> "str | None":
    """#438 D8: 'a claimed segment may NEVER be dispatched for
    translation.' Returns None when `seg` may be translated, else a
    human-readable refusal detail naming why.

    This is the driver's OWN, earlier layer -- D8's own text is explicit
    that it is a second layer, not what makes the design safe by itself
    (codex_job.py's chokepoint, owned separately, is what actually stands
    in front of launch()). What this check catches is D8's own named
    residual scenario for this side: a claimed draft that went invalid or
    missing between admission and dispatch, which would otherwise fall
    through derive_next_action()'s `if not draft_ok:` branch to plain
    {"action": "translate"} with nothing else in this file the wiser.
    Placed BEFORE process_segment()'s own `write_ledger(..., {"status":
    "in_progress"})` translate-branch write (never after), so a refusal
    here loses neither the draft bytes nor the ledger fragment -- unlike
    the fallback template path's own chokepoint, whose own residual
    section states plainly that recordLedgerCall() already ran by the
    time codex_job.py's refusal could fire.

    Reads the ON-DISK claim record directly via claim_record.py's own
    three-state predicate (classify_claim_record(), never Path.exists())
    -- this driver is one of its two documented readers, and a THIRD,
    independently hand-rolled presence test would repeat the exact drift
    the 1.19.1 sentinel bug came from.

    AMBIGUOUS maps to REFUSE here -- the OPPOSITE direction from
    claim_record.py's own module-level guidance for ADMISSION ("the
    AMBIGUOUS mapping is 'do not claim', never 'assume claimed'"). That
    guidance is about GRANTING a new authorization, where the safe
    default is to grant nothing. This call site decides whether to
    CREATE new work -- a translate dispatch that would overwrite a draft
    this run cannot prove is unclaimed -- so the safe default flips:
    failing to block here is the destructive direction D8 exists to
    prevent, while over-blocking on a record this run cannot read only
    leaves the segment recoverable for the next invocation.

    claimed_path() RAISES ValueError on a run id it will not build a path
    from (#438: it validates with claim_record.py's own validate_run_id()
    rather than trusting its callers, so a reader cannot forget). That
    exception is mapped to a REFUSAL here, in the same direction as the
    two returns below: a run id this driver cannot even name a claim
    record for is strictly WORSE than a record it cannot read -- it means
    D8 has no way to look, so it cannot possibly clear the segment.
    run() validates the resolved run id with accepted_run_id() before it
    ever becomes ctx.run_id, so this cannot fire on the shipped path; it
    is caught here anyway so the property holds by inspection of THIS
    function rather than by tracing which caller built the context --
    the same reasoning _call_resume_setup() states for repeating its own
    caller's leaf check. Letting it escape instead would reach main()'s
    defensive catch-all as `unexpected error`, exit 2, killing a whole
    batch over one segment."""
    claim_record_mod = _load_claim_record_module(ctx.dirs["scripts_dir"])
    try:
        path = claim_record_mod.claimed_path(ctx.run_id, seg, ctx.dirs["runs_dir"])
    except ValueError as exc:
        return (
            f"segment {seg!r}'s claim record path could not be built for run id "
            f"{ctx.run_id!r} ({exc}) -- refusing the translate dispatch rather than "
            f"dispatching under a run id whose claim records this driver cannot "
            f"even look for (#438 D8)"
        )
    state, detail = claim_record_mod.classify_claim_record(path)
    if state == claim_record_mod.CLAIM_ABSENT:
        # "This run has not claimed seg" is NOT "seg is unclaimed", and
        # conflating the two was this guard's defect: it built the lookup
        # path out of ctx.run_id, so it could only ever see its OWN
        # namespace. An ordinary later run therefore failed the token gate,
        # found nothing under its own id, read that as unclaimed, and
        # dispatched translate over a draft another run was actively
        # holding -- destroying exactly the hand edit #438 exists to
        # protect. Ask the question the selector's own reclaim guard asks:
        # not "do I own this?" but "who owns this, and is it me?".
        return _foreign_claim_refusal_for_translate(ctx, seg, claim_record_mod)
    if state == claim_record_mod.CLAIM_PRESENT:
        return (
            f"segment {seg!r} holds a live claim record for this run at {path} -- "
            f"a claimed segment may never be translated (#438 D8)"
        )
    # CLAIM_AMBIGUOUS
    return (
        f"segment {seg!r}'s claim record at {path} could not be read "
        f"unambiguously ({detail}) -- refusing the translate dispatch rather "
        f"than risk overwriting a draft this run cannot prove is unclaimed (#438 D8)"
    )


def claim_capability_refusal_for_translate(ctx: "DispatchContext", seg: str) -> "str | None":
    """#450: 'a segment THIS INVOCATION admitted a claim for must never be
    translated -- full stop, independently of whatever the ON-DISK claim
    record happens to say by the time dispatch actually reaches it.'
    Returns None when `seg` carries no claim in ctx.claims, else a
    refusal naming the claim and the profile it was granted under.

    A THIRD layer alongside claim_refusal_for_translate() (this driver's
    own on-disk check, directly above) and codex_job.py's own chokepoint --
    in ADDITION to both, never a replacement for either. Those two exist
    precisely because ctx.claims is private process memory: nothing
    OUTSIDE this run() invocation (a fix-turn LLM, a resumed later run,
    codex_job.py's own subprocess) can read it, so every cross-process or
    cross-invocation guard has to be keyed off the durable record instead
    -- that split is correct and deliberate, not a gap this function
    papers over.

    The gap #450 actually found is narrower, and specific to a fact THIS
    process already holds that both on-disk checks re-derive from
    scratch every time: select_segments.py granted this segment's claim
    and validated it exactly once (parse_claims_field(), #438 D3), folding
    it into ctx.claims BEFORE this invocation ever touched a segment. The
    filesystem those two on-disk checks trust can move AFTER that moment,
    from something neither of them owns -- a partial restore, a runs/
    prune, any concurrent writer. Both existing chokepoints read "the
    record I can find right now says nothing" as "nothing was ever
    granted": claim_refusal_for_translate()'s own CLAIM_ABSENT branch
    falls through to foreign_owner_refusal(), which returns None (proceed)
    for a draft still stamped with THIS run's own token -- the ordinary-
    retry shape, indistinguishable on disk from "this run's claim record
    was just deleted out from under it." Re-deriving from disk a second
    and third time cannot close that hole; the disk is exactly what is
    untrustworthy here. What closes it is the one fact that cannot have
    moved: this process's own memory of what it was granted, minted once
    at the top of run() and never touched again after.

    So this check is UNCONDITIONAL, not a reconciliation against the
    on-disk state: `seg in ctx.claims` refuses on its own. An on-disk
    record that still agrees only makes the refusal doubly justified
    (both this check and claim_refusal_for_translate() below independently
    refuse); one that disagrees is precisely the drift #450 exists to
    catch, so disagreement is never grounds to defer to the disk instead.

    Placed in process_segment() BEFORE both write_ledger()'s in_progress
    write AND the existing claim_refusal_for_translate() call, on the same
    reasoning that check's own docstring already gives for its own
    placement: a refusal here must lose neither the draft bytes nor the
    ledger fragment."""
    profile = ctx.claims.get(seg)
    if profile is None:
        return None
    return (
        f"segment {seg!r} was admitted under this invocation's own claim "
        f"(granted under profile {profile!r}) -- a segment this run's own "
        f"select_segments.py call claimed for re-review may never be "
        f"dispatched for translation by this invocation, regardless of "
        f"what the on-disk claim record shows by the time dispatch "
        f"reaches it (#450)"
    )


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
# For n_claimed == 0 -- and ONLY for that population -- the formula and the
# message shape mirror mass-translate-wf.template.js's own already-shipped
# preflight for this exact knob, verbatim. #514 added the claimed population,
# which the template cannot have; restoring blanket template parity across
# THAT path would recreate #514. Why, in the module docstring above.
# ---------------------------------------------------------------------------


def codex_jobs_per_segment(max_fix_rounds: int) -> int:
    """1 translate job + (max_fix_rounds + 1) review jobs (one per normal
    round, plus the one mandatory final confirming review). Fix rounds are
    NOT counted -- see module docstring.

    EXACT for mass-translate-wf.template.js, whose review retry re-reads
    the artifact codex already wrote instead of starting a second job; a
    FLOOR here, because `process_segment()` below may additionally spend
    the fabricated-loc re-review, hard-capped at one per segment by its own
    `fabricated_loc_retries` counter. That function's `max_iterations` is
    sized off this one accordingly, so this driver's real per-invocation
    ceiling is max_fix_rounds + 3 -- an overspend of at most one job per
    segment against the cap this function feeds."""
    return max_fix_rounds + 2


def codex_jobs_per_claimed_segment(max_fix_rounds: int) -> int:
    """The same count for an id THIS INVOCATION admitted a claim for:
    (max_fix_rounds + 1) review jobs and NO translate job.

    #514. claim_capability_refusal_for_translate() refuses `seg in
    ctx.claims` unconditionally -- before the ledger write and before the
    on-disk check -- so the one translate job codex_jobs_per_segment()
    above charges a claimed id is not merely unlikely, it is
    undispatchable. Charging it made check_volume_cap() pessimistic by
    exactly one job per claimed segment and refused batches that fitted:
    on a live book, 80 ids admitted under --from-converged at
    max_fix_rounds=4 were computed as 480 against the shipped cap of 400,
    when the reachable count was 400 -- exactly the cap -- and the refusal
    told the operator to raise a limit that did not need raising.

    A FLOOR on this driver's path by exactly one, like its sibling, and
    that parity is not incidental -- it is why process_segment()'s
    `max_iterations` is sized off THIS function for a claimed segment
    rather than off codex_jobs_per_segment() for every segment alike.
    Charging one job less while still permitting the unclaimed number of
    loop iterations would have doubled the gap between what
    check_volume_cap() charges and what a segment can actually dispatch
    (derive_next_action()'s clean-but-stale branch re-dispatches a review
    at the SAME round label on every iteration while the draft keeps
    moving out of band, and the fabricated-loc retry spends one more), and
    the schema calls this knob a WORST-CASE preflight cap: a two-job gap
    is an overrun of the operator's configured bound, not the one-job
    floor #440 already documented. Two independent reviewers caught that
    on the first draft of #514, which charged less without bounding
    less."""
    return max_fix_rounds + 1


def estimate_codex_jobs(n_segs: int, n_claimed: int, max_fix_rounds: int) -> int:
    """The ONE authority for a batch's estimated codex-job count: the
    unclaimed population at codex_jobs_per_segment() plus the claimed one
    at codex_jobs_per_claimed_segment(). Both check_volume_cap() below and
    run()'s own `volume_check_passed` journal event go through this, so the
    refused and the admitted paths can never report two different numbers
    for the same batch (they carried two hand-written copies of the one
    formula before #514).

    `n_claimed` is `len(ctx.claims)`. parse_claims_field() has already
    proved that set is a SUBSET of this invocation's own post-dedupe
    `segs` -- it is fatal there for a claims key that is not a member --
    and `claims` is a dict, so duplicate keys cannot inflate it either.
    The unclaimed count below therefore cannot go negative."""
    n_unclaimed = n_segs - n_claimed
    return (
        n_unclaimed * codex_jobs_per_segment(max_fix_rounds)
        + n_claimed * codex_jobs_per_claimed_segment(max_fix_rounds)
    )


def check_volume_cap(
    n_segs: int, max_fix_rounds: int, max_codex_jobs_per_batch: int, *, n_claimed: int = 0
):
    """Returns None if this batch is within the cap, or a refusal dict
    (mirrors mass-translate-wf.template.js's own `{reason,
    estimatedCodexJobs, codexJobsCap}` result shape) otherwise. Never
    raises -- this is a pure, side-effect-free check the caller decides
    what to do with.

    `n_claimed` (#514) is keyword-only and defaults to 0, which is the
    pre-#514 arithmetic exactly: charge every id a translate job. A caller
    that forgets it therefore OVER-estimates rather than under-estimates,
    and cannot silently land it in the wrong positional slot.

    The refusal dict's KEY SET is unchanged -- that shape is the template's,
    and this function mirrors it. Only `message` gains a clause, and only
    when n_claimed > 0, so a batch with no claims still produces the
    template's own message byte for byte."""
    estimated = estimate_codex_jobs(n_segs, n_claimed, max_fix_rounds)
    if estimated <= max_codex_jobs_per_batch:
        return None
    # Named rather than left for the operator to derive: without it the
    # message states a need that does not divide by any per-segment count
    # they could look up, which is the shape #514's own refusal had.
    claimed_clause = (
        f" ({n_claimed} of them admitted under a re-review claim, which spends "
        "no translate job)"
        if n_claimed
        else ""
    )
    return {
        "reason": "batch-too-large-codex-jobs",
        "estimatedCodexJobs": estimated,
        "codexJobsCap": max_codex_jobs_per_batch,
        "message": (
            f"Batch too large: this batch needs estimatedCodexJobs={estimated} "
            f"for {n_segs} segment(s){claimed_clause} at max_fix_rounds={max_fix_rounds}, over "
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
    return {"max_fix_rounds": max_fix_rounds, "max_codex_jobs_per_batch": max_codex_jobs_per_batch}


# ---------------------------------------------------------------------------
# Property 8 -- the Step 1 gate. Every segment id this script ever acts on
# comes FROM select_segments.py's own output; this script never computes or
# accepts a bare SEGS list of its own.
# ---------------------------------------------------------------------------


def _relay_selector_stderr(captured: "str | bytes | None") -> None:
    """#551: relay select_segments.py's OWN stderr to this driver's stderr.

    That stream is the only channel two unusual-but-correct admissions
    disclose themselves on -- the D9 lost-token recovery and #537's
    `--from-cap` over a PRESENT `.ever_converged` sentinel (select_segments.
    py's own post-publication disclosures). Both are deliberately reporting-
    only: neither is a claim-record field, because one describes the record
    that authorized it and the other describes HOW this invocation reached
    the admission. That reasoning is sound for the record and does not
    extend to "therefore nowhere" -- and this driver, which captures the
    selector's output, is the documented path for a real run, so without
    this relay the disclosure reaches no terminal and no log.

    Relayed VERBATIM, with no second prefix: every one of the selector's
    stderr sites already opens with `select_segments.py: `, so the lines
    name their own author. Whole-block, not line-by-line, so the selector's
    own ordering (requested/emitted and #530 first, then the per-segment
    D9/#537 disclosures printed after each record and token are published)
    survives intact.

    `captured` may be BYTES, not str. subprocess hands back raw bytes on the
    TimeoutExpired path even under `text=True` (measured, CPython 3.14.7),
    and `str(exc)` for that exception names only the command and the
    timeout -- so a naive relay of it would print `b'select_segments.py:
    ...'`. Decoded here rather than at each call site, with `errors=
    "replace"` because a disclosure mangled by one bad byte is still worth
    more than a driver that raises while reporting one.
    """
    if not captured:
        return
    if isinstance(captured, bytes):
        captured = captured.decode("utf-8", errors="replace")
    if captured.strip():
        print(captured.rstrip("\n"), file=sys.stderr)


def run_select_segments(
    dirs: dict,
    *,
    only_segs=None,
    allow_retranslate_converged=False,
    allow_empty=False,
    from_cap=None,
    from_converged=None,
    from_stalled=None,
    run_id=None,
    run_resume=None,
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

    `from_cap`/`from_converged`/`from_stalled` (#438 D1/D2, #455): forwarded
    verbatim to select_segments.py's own like-named flags, requesting claim
    ADMISSION for the named ids under the `--from-cap`/`--from-converged`/
    `--from-stalled` profile respectively. Admission is SINGLE-PHASE -- this
    one call validates the ids AND writes each admitted id's durable claim
    record plus its draft's re-stamped dispatch_token, all before it
    returns. See run()'s own call site and parse_claims_field() below for
    how the resulting authorization is read back out of select_segments.py's
    JSON payload -- never assumed from these arguments alone.

    `from_stalled` additionally causes `--driver-lease-held` to be forwarded
    (see the cmd-building block below for exactly when and why) -- an
    ordinary call with `from_stalled=None` never sends that flag, matching
    acceptance criterion 4 (no behaviour change, no new lock acquisition,
    off this path).

    `run_id`/`run_resume`: forwarded as select_segments.py's own
    `--run-id`/`--run-resume`, which that script requires as a PAIR (it
    refuses either one alone -- "--run-id and --run-resume must be given
    TOGETHER or not at all" -- before any gate work runs) and requires
    outright whenever a claim is requested ("a claim (--from-converged/
    --from-cap) was requested but --run-id was not given" -- a claim
    re-stamps a draft's authorization TO a run, so there is no such thing
    as a claim without one). `run_resume` is the literal string
    "true"/"false" relaying resume_setup.py's own boolean `resume` field,
    not a Python bool -- see run_resume_literal() below, which is the only
    sanctioned way to produce it.

    An EARLIER revision of this driver passed `run_id=None` here
    unconditionally and documented that it must never carry one: that
    text described a two-phase validate/commit design (D1a) that was
    abandoned, and it made `--from-cap`/`--from-converged` dead on
    arrival on this path -- select_segments.py fatals on a claim without
    a --run-id, so every claim the driver forwarded was refused at the
    gate. The hazard that reasoning was built on was real but is closed
    elsewhere now: resolve_run_id() -> resume_setup.py writes
    runs/<ID>/input.digest as a side effect, and the #409 Step 3 gate
    reads a digest as proof the resume-integrity gate ran for that id, so
    minting an id before this call used to be able to manufacture that
    evidence. select_segments.py's Step 3 evidence is now a ONE-SHOT
    SNAPSHOT taken before that invocation writes anything -- its "#409
    Step 3: evidence scan" block calls scan_dispatching_run_ids() and
    scan_workflow_run_ids() exactly once each and states the rule
    normatively ("Step 3's evidence is a property of the tree AS THIS
    INVOCATION FOUND IT") -- and a FRESH id (`--run-resume false`) that
    collides with pre-existing dispatch evidence is refused outright
    ("was reported FRESH by resume_setup.py ... but this project already
    has dispatch evidence bearing that exact id"). A freshly minted id
    carries no drafts and no runs/workflows/ directory of its own, so it
    never enters the evidence set at all and the digest written for it
    proves nothing to the gate either way. The remaining honest cost of
    resolving early is an ORPHANED runs/<ID>/ directory whenever the
    selector subsequently refuses; run() pays it deliberately and reports
    the id, rather than shipping a flag that cannot work."""
    if (run_id is None) != (run_resume is None):
        # Mirrors select_segments.py's own run() pairing rule ("--run-id and
        # --run-resume must be given TOGETHER or not at all") rather than
        # letting the child refuse an argv this function built: a caller that
        # resolved a run id but forgot to relay resume_setup.py's `resume`
        # has a bug HERE, and the child would name the argv, not the caller.
        fatal(
            "run_select_segments(): run_id and run_resume must be given TOGETHER or "
            f"not at all -- got run_id={run_id!r} run_resume={run_resume!r}. "
            "select_segments.py refuses the pair split (its own --run-resume carries "
            "resume_setup.py's 'resume' field, which is what lets its #409 Step 3 "
            "fresh-evidence check tell a resumed id from a freshly minted one).",
            exit_code=2,
        )
    if run_resume is not None and run_resume not in ("true", "false"):
        fatal(
            f"run_select_segments(): run_resume must be the literal string 'true' or "
            f"'false' (select_segments.py's own --run-resume choices), got "
            f"{run_resume!r} -- build it with run_resume_literal(), never by "
            "formatting a Python bool.",
            exit_code=2,
        )

    select_segments_script = dirs["select_segments_script"]
    _refuse_unless_executable_leaf(select_segments_script, "select_segments.py")

    cmd = [sys.executable, str(select_segments_script)]
    if only_segs is not None:
        cmd += ["--only-segs", only_segs]
    if allow_retranslate_converged:
        cmd += ["--allow-retranslate-converged"]
    if allow_empty:
        cmd += ["--allow-empty"]
    if from_cap is not None:
        cmd += ["--from-cap", from_cap]
    if from_converged is not None:
        cmd += ["--from-converged", from_converged]
    if from_stalled is not None:
        cmd += ["--from-stalled", from_stalled]
        # #455: --driver-lease-held is forwarded HERE, and ONLY here -- never
        # for an ordinary dispatch, never for --from-cap/--from-converged.
        # This is safe to do unconditionally at this call site (no separate
        # "did we actually acquire the lease" check needed) because
        # run_select_segments() has exactly ONE caller in this file: run()'s
        # own `select_result = run_select_segments(...)` below, which is
        # reached only after `acquire_driver_lock()` has already returned
        # successfully (a failed acquire calls fatal() and this function is
        # never reached at all) -- see run()'s own body for that ordering.
        # If a second call site is ever added that can reach here WITHOUT
        # first holding the lease, this flag must not be forwarded
        # unconditionally on `from_stalled is not None` alone; it would then
        # need its own "was the lease actually acquired" parameter.
        #
        # WHY the flag has to be forwarded at all, rather than the child
        # just noticing its parent already holds the lease: `flock` is
        # scoped per OPEN FILE DESCRIPTION, not per path or per process, so
        # select_segments.py opening runs/.driver.lock fresh (its own
        # independent open()) gets an independent file description with no
        # visibility into this driver's lease no matter how the two
        # processes are related. And even if that were not true,
        # subprocess.run() below passes no `pass_fds`, so Python's own
        # default `close_fds=True` applies and this driver's lease fd would
        # not survive into the child's fd table regardless. Without
        # --driver-lease-held the child would attempt its own independent
        # `LOCK_EX|LOCK_NB` against a lease its own parent already holds,
        # and be refused by that parent -- an ordinary driver-invoked
        # --from-stalled claim would be unconditionally impossible.
        #
        # The flag is a POINTER ("a driver in my own process tree already
        # holds this lease"), never a GRANT: select_segments.py re-confirms
        # it independently against the kernel (its own LOCK_EX|LOCK_NB
        # attempt, which must FAIL) rather than trusting the flag's mere
        # presence -- see that script's own admission logic for the
        # self-test this mirrors. A forged flag from outside a real driver
        # is still refused whenever the lease genuinely is free.
        cmd += ["--driver-lease-held"]
    if run_id is not None:
        cmd += ["--run-id", run_id, "--run-resume", run_resume]
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
        # TimeoutExpired carries whatever the child had already written;
        # OSError is a SPAWN failure and carries no such attribute at all
        # (no child ran, so there is nothing to relay) -- hence getattr
        # rather than a second except clause.
        _relay_selector_stderr(getattr(exc, "stderr", None))
        fatal(f"could not run select_segments.py: {exc}", exit_code=2)

    # BEFORE the decode, not after: this driver's own fatal() raises
    # DriverError, and main() serializes that as JSON on STDOUT -- so a
    # selector that disclosed an admission and then printed malformed stdout
    # would deliver the disclosure only as a repr inside a JSON string on
    # the other stream. Relaying first makes the placement uniform across
    # success, selector refusal and decode failure, and is why the decode
    # fatal below no longer embeds `stderr` itself: it would be the second
    # copy, in the redirected run log, of a line already printed above.
    _relay_selector_stderr(proc.stderr)

    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        fatal(
            "select_segments.py did not print valid JSON on stdout "
            f"(exit {proc.returncode}): stdout={proc.stdout!r} "
            "(its stderr, if any, was relayed to this driver's own stderr)",
            exit_code=2,
        )
    if not isinstance(payload, dict):
        fatal(f"select_segments.py printed a non-object JSON value: {payload!r}", exit_code=2)
    return payload


# ---------------------------------------------------------------------------
# #438 D3 -- fail-closed transport of the claim authorization from
# select_segments.py's own JSON output into this driver's dispatch
# context. The failure this closes: select_segments.py emits the
# authorization, this driver ignores or loses the field but still
# consumes `segs`, and a segment that was never actually admitted for
# re-review gets translated -- the current #438 bug with a new flag on
# top. So the field is REQUIRED on every invocation (an empty list when
# no --from-cap/--from-converged ids were requested, never merely
# absent), and every entry is validated the same way `segs` itself
# already is.
# ---------------------------------------------------------------------------

# The three admission profiles -- the original #438 D2 pair plus #455's
# `--from-stalled` -- spelled identically to the CLI flag names that
# request them (`--from-cap`/`--from-converged`/`--from-stalled`) so a
# mismatched literal is visible by inspection rather than needing a
# lookup table.
# #536: mirrors select_segments.py's own constant of the same name, and lets
# parse_claims_from_cap_over_sentinel() compare against this ONE profile with
# nothing to drift from. Deliberately NOT `KNOWN_CLAIM_PROFILES[0]` -- an index
# re-derives the meaning from tuple ORDER, so reordering the tuple would
# silently change which profile that check enforces.
CLAIM_PROFILE_FROM_CAP = "from-cap"
KNOWN_CLAIM_PROFILES = (CLAIM_PROFILE_FROM_CAP, "from-converged", "from-stalled")


def parse_claims_field(select_result: dict, segs: list) -> dict:
    """Validates select_segments.py's own `claims` field and returns
    `{seg: profile}`. select_segments.py reports `claims` as a JSON
    object keyed by segment id, each value the full claim_record.py
    payload (plus that script's own D6/D10 reporting-only fields) -- see
    its own module docstring. This function extracts and validates only
    what this driver's own D8/D11 logic needs (which ids are claimed and
    under which profile: identity, safety, and subset-of-segs); it does
    NOT re-validate the rest of the record's fields (previous_dispatch_
    token, cache_key, ...) -- claim_refusal_for_translate() reads the
    record's own on-disk copy directly via claim_record.py's shared
    three-state predicate, which is the source of truth for the D8
    on-disk check.

    What this function returns is NOT audit-only, and is not merely a
    fail-closed transport either: #450 gave it direct enforcement power.
    claim_capability_refusal_for_translate() refuses a TRANSLATE straight
    off `ctx.claims` -- unconditionally, before write_ledger() and before
    the on-disk check -- so an id admitted here can never be translated by
    this invocation no matter what the record on disk says by then. The
    two layers are additive and answer different questions: the on-disk
    record is the only evidence that survives across processes and
    invocations, while this in-memory grant is the only fact a concurrent
    writer, a partial restore or a runs/ prune cannot move.

    The transport requirement stands as well: a stripped/malformed/
    mismatched 'claims' field is caught here, fail-closed, rather than
    silently proceeding to consume `segs` regardless (#438 D3 -- "the
    selector emits the authorization, the driver ignores or loses the
    field but still consumes segs").

    FATAL (never a silent default) on any of: the field missing
    entirely; not a JSON object; a key that is not a safe segment id
    (validate_seg()); a value that is not a JSON object, or whose own
    'seg' disagrees with the key it is filed under, or whose 'profile' is
    outside KNOWN_CLAIM_PROFILES; or a key that is NOT a member of THIS
    SAME invocation's own `segs` (the authorization must be a SUBSET of
    what was actually selected, never a superset that could smuggle in an
    id this run never even considered dispatching)."""
    claims = select_result.get("claims")
    if claims is None:
        fatal(
            "select_segments.py's JSON output has no 'claims' field -- "
            "refusing to proceed with an unauthenticated claim-authorization "
            "channel (#438 D3)",
            exit_code=2,
        )
    if not isinstance(claims, dict):
        fatal(
            f"select_segments.py's 'claims' field is not a JSON object: {claims!r} (#438 D3)",
            exit_code=2,
        )
    segs_set = set(segs)
    result = {}
    for seg, entry in claims.items():
        problem = validate_seg(seg)
        if problem is not None:
            fatal(f"claims key {seg!r} is an unsafe segment id ({problem}) (#438 D3)", exit_code=2)
        if not isinstance(entry, dict):
            fatal(f"claims[{seg!r}] is not a JSON object: {entry!r} (#438 D3)", exit_code=2)
        entry_seg = entry.get("seg")
        if entry_seg != seg:
            fatal(
                f"claims[{seg!r}]['seg'] is {entry_seg!r}, which disagrees with its own "
                f"dict key -- refusing rather than guessing which is authoritative (#438 D3)",
                exit_code=2,
            )
        profile = entry.get("profile")
        if profile not in KNOWN_CLAIM_PROFILES:
            fatal(
                f"claims[{seg!r}]['profile'] must be one of {KNOWN_CLAIM_PROFILES}, got {profile!r} (#438 D3)",
                exit_code=2,
            )
        if seg not in segs_set:
            fatal(
                f"claims names segment {seg!r}, which is not a member of this invocation's "
                f"own 'segs' set -- the claim authorization must be a subset of what was "
                f"actually selected (#438 D3)",
                exit_code=2,
            )
        result[seg] = profile
    return result


def parse_claims_admitted_via(select_result: dict, claims: dict) -> dict:
    """Validates select_segments.py's `claims_admitted_via` field and returns
    `{seg: profile}` -- the gate THIS invocation admitted each id under.

    `claims` must be parse_claims_field()'s own RETURN VALUE (the reduced
    `{seg: profile}` map), never select_segments.py's raw `claims` JSON. That
    is what makes the key check below sufficient on its own: every key in the
    reduced map has already passed validate_seg() and the subset-of-`segs`
    check up there, so equality with it transitively supplies both here.

    #545: the `profile` inside select_segments.py's own `claims` record, which
    parse_claims_field() above reduces this map's values to, is the DURABLE
    claim record's. On a re-claim inside one run id that record is the one
    written at that run id's FIRST claim, so its profile can be older than the
    admission this invocation actually performed. Both are true
    statements about different questions, and the `step1_gate_passed` record
    below is the only durable copy of either REDUCED map (#549) -- the full
    record stays durable at runs/<RUN_ID>/.claimed.<seg>, but nothing on disk
    would otherwise say which gate this invocation ran. So both are carried,
    and neither is corrected into the other.

    Validated for the same reason and in the same fail-closed way as `claims`
    itself: a selector that stopped emitting the field has broken the
    transport, and reading that as "nothing was admitted" is exactly the
    silent-loss shape #438 D3 refuses.
    The key set must be EQUAL to `claims`' (not merely a subset): the selector
    writes both in one loop iteration after the same guards, so any divergence
    means one of the two maps is not the one this invocation produced.

    This field is REPORT-ONLY. Nothing gates on it -- `ctx.claims` still
    carries the record's profile, and claim_capability_refusal_for_translate()
    refuses on MEMBERSHIP, never on the profile string. Widening its authority
    would make a reporting fix into an admission change, which #545 explicitly
    is not.

    FATAL (never a silent default) on: the field missing entirely; not a JSON
    object; a key set that is not exactly `claims`' key set; or a value outside
    KNOWN_CLAIM_PROFILES."""
    admitted_via = select_result.get("claims_admitted_via")
    if admitted_via is None:
        fatal(
            "select_segments.py's JSON output has no 'claims_admitted_via' field -- "
            "refusing to journal a claim map that cannot say which gate this "
            "invocation admitted under (#545). A driver from this release requires a "
            "selector from it: --plugin-root can point the two at different installs, "
            "and this is where that mismatch is caught.",
            exit_code=2,
        )
    if not isinstance(admitted_via, dict):
        fatal(
            f"select_segments.py's 'claims_admitted_via' field is not a JSON object: "
            f"{admitted_via!r} (#545)",
            exit_code=2,
        )
    if set(admitted_via) != set(claims):
        fatal(
            f"'claims_admitted_via' names {sorted(admitted_via)!r}, which disagrees with "
            f"'claims' ({sorted(claims)!r}) -- the two are written together by the "
            f"selector, so a disagreement means one of them is not this invocation's "
            f"own (#545)",
            exit_code=2,
        )
    for seg, profile in admitted_via.items():
        if profile not in KNOWN_CLAIM_PROFILES:
            fatal(
                f"claims_admitted_via[{seg!r}] must be one of {KNOWN_CLAIM_PROFILES}, "
                f"got {profile!r} (#545)",
                exit_code=2,
            )
    return dict(admitted_via)


def parse_claims_from_cap_over_sentinel(select_result: dict, claims: dict, admitted_via: dict) -> list:
    """Validates select_segments.py's `claims_from_cap_over_sentinel` field and
    returns it as a list -- the ids `--from-cap` admitted over a PRESENT
    `.ever_converged` sentinel (#537's converged-then-staled-then-capped
    population, #536's transport of the fact).

    Called ONLY when this invocation actually forwarded `--from-cap`. That is
    the one place its two siblings and this function differ, and the narrowing
    is LOSSLESS rather than a relaxation: run_select_segments() forwards the
    flag iff `from_cap is not None`, select_segments.py builds a `from-cap`
    request only from that flag, and its `from_cap_over_sentinel` is set only
    inside the CLAIM_PROFILE_FROM_CAP branch -- so a real admission over a
    sentinel IMPLIES the flag was forwarded, and skipping the check without it
    cannot read one as `[]`. Requiring it there anyway would only add a fresh
    refusal surface for a selector/driver pair that `claims_admitted_via`
    (required unconditionally since 1.57.0) already refuses on every
    invocation -- it would detect no skew that is not detected already.

    UNDER `--from-cap` the field IS required, for the reason
    `eligible_not_dispatched` gives for its own: a selector that stopped
    emitting it would be read as "no unit was admitted over a sentinel", which
    is exactly the silent green this field closes.

    `claims` must be parse_claims_field()'s own RETURN VALUE (the reduced
    `{seg: profile}` map), never the raw `claims` JSON. Its check is there FOR
    THE MESSAGE, and that is worth saying plainly: parse_claims_admitted_via()
    has already fatalled unless `set(admitted_via) == set(claims)`, so an id
    outside `claims` is outside `admitted_via` too and the profile check below
    would refuse it anyway -- just while naming a profile of None rather than
    saying the id was never claimed. Membership does still supply
    validate_seg() and the subset-of-`segs` check transitively, since every
    key of the reduced map passed both.

    `admitted_via` must be parse_claims_admitted_via()'s return value, and
    every member must map to `from-cap` in it. Membership in `claims` alone is
    NOT sufficient, and the gap was not cosmetic: distinct ids may legitimately
    be admitted under DIFFERENT profiles in one selector call, so a payload
    placing a `from-converged` id in this list would make this driver journal
    and print, as fact, that a `--from-cap` claim was admitted over a sentinel
    when no such admission happened. The one thing this field exists to report
    is exactly the thing membership in `claims` cannot establish.

    Checked against `admitted_via` rather than `claims[seg]` deliberately:
    `claims[seg]` is the DURABLE record's profile, which on a re-claim inside
    one run id is the FIRST claim's, while this field describes what THIS
    invocation admitted -- the distinction #545 exists for.

    A repeated member is refused too. Unreachable from the real selector, which
    appends inside a loop visiting each id once -- but the operator disclosure
    counts this list, so a repeat would misstate how many claims were admitted
    that way, and one set rules it out. Order is otherwise the selector's own
    publication order, NOT re-sorted here -- a second ordering in this file
    would drift from the one the selector's stderr disclosure prints in.

    This field is REPORT-ONLY. Nothing gates on it, exactly as for
    `claims_admitted_via`: widening its authority would make a reporting fix
    into an admission change.

    FATAL (never a silent default) on: the field missing entirely; not a JSON
    array; a member that is not a key of `claims`; a member this invocation did
    not admit under `from-cap`; or a repeated member."""
    over_sentinel = select_result.get("claims_from_cap_over_sentinel")
    if over_sentinel is None:
        fatal(
            "select_segments.py's JSON output has no 'claims_from_cap_over_sentinel' "
            "field -- this driver cannot report which --from-cap ids were admitted "
            "over an ever-converged sentinel, and reporting nothing would be "
            "indistinguishable from none having been. Refused rather than defaulted "
            "(#536). A driver from this release requires a selector from it: "
            "--plugin-root can point the two at different installs.",
            exit_code=2,
        )
    if not isinstance(over_sentinel, list):
        fatal(
            f"select_segments.py's 'claims_from_cap_over_sentinel' field is not a JSON "
            f"array: {over_sentinel!r} (#536)",
            exit_code=2,
        )
    seen = set()
    for seg in over_sentinel:
        if seg not in claims:
            fatal(
                f"claims_from_cap_over_sentinel names {seg!r}, which is not a key of "
                f"this invocation's own 'claims' -- the disclosure must name ids this "
                f"run actually claimed (#536)",
                exit_code=2,
            )
        if admitted_via.get(seg) != CLAIM_PROFILE_FROM_CAP:
            fatal(
                f"claims_from_cap_over_sentinel names {seg!r}, which this invocation "
                f"admitted under {admitted_via.get(seg)!r}, not {CLAIM_PROFILE_FROM_CAP!r} -- "
                f"this field reports a --from-cap admission specifically, and "
                f"journalling it for an id admitted by another gate would record a "
                f"claim that was never made (#536)",
                exit_code=2,
            )
        if seg in seen:
            fatal(
                f"claims_from_cap_over_sentinel names {seg!r} more than once -- the "
                f"operator disclosure counts this list, so a repeat would misstate how "
                f"many claims were admitted over a sentinel (#536)",
                exit_code=2,
            )
        seen.add(seg)
    return list(over_sentinel)


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
    # `companion_path` is not built from SCRIPTS_DIR/plugin_root by
    # concatenation the way every OTHER executed artifact in this file
    # is -- it is a STRING resolve_codex_companion.py printed on its own
    # stdout, discovered dynamically, not derived from a trusted root
    # this driver controls. It is verified before being executed, but NOT
    # with the whole-path walk the other artifacts get, and the asymmetry
    # is deliberate: those paths are built from a root this driver is
    # handed, so an attacker-supplied symlink ANYWHERE along them is a
    # redirection. This one is discovered inside the user's own plugin
    # store, where a symlinked ancestor is an ordinary, supported layout
    # -- several profiles have shared one `plugins/` directory that way.
    # Requiring a symlink-free ancestor chain here refuses a normal
    # install: the resolver preserves such ancestors by design, so the
    # whole-path walk reports `suspicious` for a perfectly legitimate
    # companion and this best-effort cleanup silently stops running.
    # What IS checked is the thing this function is about to execute: the
    # leaf must be a regular file and must not itself be a symlink.
    # Verified fresh on every call rather than cached from
    # resolve_companion_path()'s earlier resolution, since this can fire
    # long afterwards.
    try:
        _companion_fd = os.open(
            companion_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return  # symlinked leaf, absent, or unreadable -- do not execute it
    try:
        if not stat.S_ISREG(os.fstat(_companion_fd).st_mode):
            return
    except OSError:
        return
    finally:
        try:
            os.close(_companion_fd)
        except OSError:
            pass
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
    _refuse_unless_executable_leaf(codex_job_script, "codex_job.py")
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


def _definitive_stat(path: Path, *, refusal: str):
    """os.stat(path) -> its stat_result, or None when `path` (or a
    directory component leading to it) definitively does not exist
    (FileNotFoundError/NotADirectoryError). Any OTHER OSError -- EACCES,
    EIO, ELOOP, ... -- means the filesystem could not answer at all, and
    this refuses immediately via DriverError (`refusal`, exit_code=2)
    naming `path`, rather than letting a caller mistake "could not look"
    for "not there".

    Exists because Path.is_dir()/Path.is_file() BOTH swallow the
    underlying stat error and answer False on ANY OSError -- so an
    `except OSError` wrapped around either one never fires, and "I could
    not look" is delivered in the same word as "it does not exist" / "it
    is not that kind". Measured on the Python this ships against (3.14.6)
    for both EACCES and ELOOP. Same trap, same fix, as
    select_segments.py's evaluate_takeover_since_this_claim() -- see its
    own comment for what silently collapsing the two answers cost there.

    claim_record.py's any_foreign_claim() does NOT take this fix, and does
    not have this trap to take it for; that divergence is deliberate and
    retained in this release. It stats nothing at all, so there is no
    swallowed stat error there to split -- a non-directory entry reaches
    it undetected and is caught one level down, by the child lstat.
    So a non-directory entry under runs/ (ledger.json, the
    materialized ledger every project has) is taken as a candidate holder
    whose claim path lstat's ENOTDIR -> CLAIM_AMBIGUOUS -> reported as a
    foreign holder. Fixing it there would flip a refusal into a proceed:
    that AMBIGUOUS is what keeps a legacy token-less draft refused at the
    translate chokepoint, via foreign_owner_refusal()'s no-token branch.
    Do not mirror this helper into that enumeration. `refusal` is
    the caller's own description of what could not be established, so the
    DriverError message says WHAT was being checked, not just which path
    failed."""
    try:
        return os.stat(path)
    except (FileNotFoundError, NotADirectoryError):
        return None
    except OSError as exc:
        fatal(f"{refusal} ({path} could not be inspected: {exc})", exit_code=2)


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
    run signal").

    codex round-5 (post-1.21.0 review): an entry that becomes unstattable
    here -- EACCES, EIO, ELOOP, transient or not -- used to vanish from
    the returned list SILENTLY, via Path.is_dir()/Path.is_file()'s own
    error-swallowing (see _definitive_stat()'s own docstring for the
    mechanism). That is a real hole, not a theoretical one: on the
    ORDINARY (non-claim) dispatch path, run_select_segments() has already
    finished (run()'s own `select_result = run_select_segments(...)` call)
    BEFORE this function's caller runs -- resolve_run_id() is invoked
    afterward, only once `run_result is None` -- so the selector's own
    fresh-RUN_ID collision refusal has nothing to do with a candidate this
    scan drops: on that path the selector was never even given a run id to
    check evidence against. A run entry that flickers unreadable in
    exactly this window would be dropped here, a FRESH run id minted in
    its place, and an in-progress, hand-claimed draft still naming the
    old (now orphaned) run would read as unowned to every ownership guard
    downstream -- silently permitting exactly the retranslation-over-a-
    hand-edit #438 exists to stop.

    DIRECTION CHOSEN: refuse outright (DriverError via _definitive_stat(),
    exit_code=2, naming the unreadable path) rather than silently treating
    an unstattable entry as either answer. The alternative considered --
    folding an unstattable entry INTO the candidate list, on the theory
    that "it might be a real run, so do not silently forget it" -- was
    rejected: this function's return value is forwarded verbatim into
    resume_setup.py's own `resume_from_run_ids`, and that authority's
    resolve_run() (resume_setup.py:785) decides a MATCH by reading
    `runs/<id>/input.digest` with its OWN `Path.is_file()` call
    (resume_setup.py:807) -- the identical swallow-pattern, one layer
    down, in a file this fix does not own. Passing an unreadable candidate
    through would not close the hole; it would only relocate it to a site
    this change cannot reach, while looking closed here. Refusing at the
    point where the ambiguity is actually discovered is the only
    direction that is both honest about what could not be established and
    fully within this function's own power to guarantee."""
    runs_stat = _definitive_stat(
        runs_dir,
        refusal=f"could not establish whether {runs_dir} holds any resumable prior run",
    )
    if runs_stat is None or not stat.S_ISDIR(runs_stat.st_mode):
        return []
    glossary_runs_dir = durable_root / "glossary" / "runs"
    try:
        entries = sorted(runs_dir.iterdir())
    except (FileNotFoundError, NotADirectoryError):
        # DEFINITIVELY nothing: gone between the stat above and this listing.
        return []
    except OSError as exc:
        fatal(
            f"runs directory {runs_dir} could not be listed ({exc}), so whether a "
            f"resumable prior run exists there is unknown",
            exit_code=2,
        )
    candidates = []
    for entry in entries:
        # Name-shape check FIRST, before any stat: runs_dir also holds
        # ledger.json, workflows/, and any other non-run-id entry (see
        # this function's own docstring above) -- a name that already
        # disqualifies an entry excludes it on that basis alone, with
        # nothing to establish about its filesystem state and no risk of
        # refusing over a path that was never a candidate to begin with.
        if validate_run_id(entry.name) is not None:
            continue
        entry_stat = _definitive_stat(
            entry, refusal=f"could not establish whether {entry.name} is a run directory",
        )
        if entry_stat is None or not stat.S_ISDIR(entry_stat.st_mode):
            continue
        digest_stat = _definitive_stat(
            entry / "input.digest",
            refusal=f"could not establish whether {entry.name} carries an input.digest marker",
        )
        if digest_stat is None or not stat.S_ISREG(digest_stat.st_mode):
            continue
        glossary_stat = _definitive_stat(
            glossary_runs_dir / entry.name,
            refusal=f"could not establish whether {entry.name} is a glossary run",
        )
        if glossary_stat is not None and stat.S_ISDIR(glossary_stat.st_mode):
            continue
        candidates.append(entry.name)
    return sorted(candidates, reverse=True)


def resolve_run_id(dirs: dict, *, translate_cfg: dict,
                    plugin_root_str, durable_root_str,
                    pinned_run_id: "str | None" = None) -> dict:
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
    (_load_manifest_seg_ids(), resume_setup.py:552) -- never from a
    caller-supplied list -- and reads a `segs` field literally NOWHERE in
    its own source; resume_integrity.test.py:test_mass_segs_field_omitted_
    still_works proves omission is accepted for kind="mass" specifically,
    not merely inferred from the module docstring. This driver ships in
    the SAME release as that exact resume_setup.py commit, so it carries
    no pre-8815800 caller to stay backward-compatible with (the field's
    one-release acceptance window in resume_setup.py's own docstring
    exists for OTHER, separately-versioned callers, not this one) --
    sending it would only reintroduce dead code with nothing on the
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
    none -- resume_setup.py's own resolve_run() (resume_setup.py:724) now
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
    resume_setup.py:626-638), for the reason this driver already applies:
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
    _refuse_unless_executable_leaf(script, "resume_setup.py")

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
    if pinned_run_id is not None:
        # Verified HERE, not only in run(), even though run() already
        # refuses an unsafe pin before this function is reachable. Same
        # reasoning _call_resume_setup()'s own docstring gives for
        # re-verifying the script it executes: a guard that holds because
        # of the call graph rather than because of where it is written is
        # a guard waiting for a second caller that never gets it, and this
        # value goes on to build runs/<ID> two statements below. run()'s
        # check stays -- it fails FAST, before the driver lock and before
        # any subprocess; this one makes the property true by inspection of
        # this function alone. (Raised as a deliberate non-finding by the
        # closing security pass, and folded in because this file already
        # argues the position against itself.)
        problem = validate_run_id(pinned_run_id)
        if problem is not None:
            fatal(f"resolve_run_id(): unsafe pinned run id: {problem}", exit_code=2)
        # #458. The pin replaces the SCAN, never the digest comparison:
        # resume_setup.py still decides whether resuming this id is safe,
        # and still mints a fresh id when it is not. What the operator gets
        # is the ability to say WHICH prior run this invocation is about --
        # without it the newest matching candidate always wins
        # (resume_setup.py's first-match-wins loop over the order given),
        # so a run sharing a digest with a newer one is unreachable by
        # construction rather than by configuration.
        #
        # _definitive_stat() rather than Path.is_dir()/is_file() for the
        # same reason the scan uses it (see _resumable_run_id_candidates()'s
        # own round-5 docstring): those swallow EACCES/EIO/ELOOP and answer
        # False, which here would report a perfectly good pinned run as
        # absent. The two outcomes are filed differently on purpose --
        # ESTABLISHED absence is a gate refusing (exit 1), an
        # INDETERMINATE state is an environment error (exit 2, raised by
        # _definitive_stat() itself).
        run_dir = dirs["runs_dir"] / pinned_run_id
        run_dir_stat = _definitive_stat(
            run_dir,
            refusal=f"could not establish whether the pinned run directory {run_dir} exists",
        )
        if run_dir_stat is None or not stat.S_ISDIR(run_dir_stat.st_mode):
            fatal(
                f"--resume-from-run-id {pinned_run_id!r}: {run_dir} is not a run directory, "
                f"so there is no prior run to resume from. Nothing was dispatched.",
                exit_code=1,
                pinned_run_id=pinned_run_id,
            )
        digest_path = run_dir / "input.digest"
        digest_stat = _definitive_stat(
            digest_path,
            refusal=f"could not establish whether the pinned run {pinned_run_id} carries an input.digest marker",
        )
        if digest_stat is None or not stat.S_ISREG(digest_stat.st_mode):
            fatal(
                f"--resume-from-run-id {pinned_run_id!r}: {digest_path} is missing or is not a "
                f"regular file, so the pinned run records no digest to compare this invocation "
                f"against. Nothing was dispatched.",
                exit_code=1,
                pinned_run_id=pinned_run_id,
            )
        # ped-ant #618: _definitive_stat() establishes only that this is a
        # REGULAR FILE, never that it can be READ. An existing but unreadable
        # digest (chmod 000, a restored root with wrong ownership) therefore
        # passed every gate above and failed inside resume_setup.py's own
        # read_text(), whose catch-all comes back as `success: false` and is
        # converted by _call_resume_setup() into exit 1 -- reporting an
        # ENVIRONMENTAL incident as an established gate refusal, in the exact
        # contract this release introduced, and dropping `pinned_run_id` from
        # the payload on the way. Establish readability here instead, where
        # the pin is what makes the file load-bearing.
        #
        # UnicodeDecodeError is caught alongside OSError, and it is NOT
        # redundant: it is a ValueError, so an OSError-only handler lets a
        # corrupt (non-UTF-8) digest escape as a bare exception into main()'s
        # generic catch-all, which emits "unexpected error" and drops
        # `pinned_run_id` -- the exact operator context this probe exists to
        # preserve. Caught here rather than sidestepped by reading BYTES,
        # because decodability is part of what must be established:
        # resume_setup.py reads this same file with read_text(), so a digest
        # this probe could not decode would fail there instead and come back
        # as the exit-1 misclassification above. (ped-ant #618, second round.)
        #
        # The bytes are DISCARDED, deliberately. This is a readability probe,
        # not a digest comparison: resume_setup.py remains the sole authority
        # on whether the recorded digest matches, and comparing it here would
        # be a second, drifting implementation of the one decision this whole
        # flag is built to leave with that script. Do not "optimize" this into
        # a comparison.
        try:
            digest_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            fatal(
                f"--resume-from-run-id {pinned_run_id!r}: {digest_path} exists but could not be "
                f"read ({exc}), so whether the pinned run can be resumed is UNKNOWN rather than "
                f"refused. Nothing was dispatched.",
                exit_code=2,
                pinned_run_id=pinned_run_id,
            )
        payload["resume_from_run_ids"] = [pinned_run_id]
        result = _call_resume_setup(script, payload, dirs, durable_root_str, plugin_root_str)
        if not result.get("resume"):
            # The operator named a run. resume_setup.py answered that this
            # invocation's inputs do not match it, and (having been offered
            # no other candidate) minted a fresh id. PROCEEDING under that
            # fresh id is precisely the measured #458 harm -- every named
            # segment claimed under a run nobody asked for -- and it is
            # worse here than on the unpinned path, because here the
            # operator made an explicit statement this outcome contradicts.
            # The fresh runs/<id>/ directory resume_setup.py just wrote is
            # left behind; that orphan is the same cost every claim-path
            # refusal already pays (see run()'s own note).
            minted = result.get("effectiveRunId")
            fatal(
                f"--resume-from-run-id {pinned_run_id!r}: this invocation's input digest does "
                f"NOT match that run's own recorded digest, so it cannot be resumed. "
                f"resume_setup.py minted a fresh RUN_ID {minted!r} instead; refusing to dispatch "
                f"under a run you did not ask for. Nothing was dispatched. If the inputs really "
                f"did change, re-run without --resume-from-run-id to accept the fresh run.",
                exit_code=1,
                pinned_run_id=pinned_run_id,
                minted_run_id=minted,
                offered_candidate_count=1,
            )
        return result

    candidates = _resumable_run_id_candidates(dirs["runs_dir"], dirs["durable_root"])
    if candidates:
        # Omitted entirely (never an empty list) when there are none --
        # resume_setup.py's own module docstring: "Omitting both fields is
        # a genuinely-first-ever-run signal, exactly as before."
        payload["resume_from_run_ids"] = candidates
    result = _call_resume_setup(script, payload, dirs, durable_root_str, plugin_root_str)
    if not result.get("resume"):
        # #458. `"resume": false` already reaches the printed JSON (run()'s
        # own result payload) and the journal, so this line is not what
        # makes the mint visible -- it is what makes it READABLE, and it
        # carries the one datum neither artifact holds: how many candidates
        # were actually offered.
        #
        # Emitted on EVERY unpinned mint, including a zero-candidate one.
        # Zero offered does NOT mean "first-ever run": the scan also returns
        # [] after DROPPING entries it could not accept (wrong name shape,
        # no input.digest, a glossary sibling), so a project with prior runs
        # on disk can reach zero. The wording below therefore claims only
        # what the count actually proves.
        print(
            f"segment_dispatch_driver.py: warning: none of the eligible candidates offered "
            f"matched this invocation's input digest -- minted a FRESH RUN_ID "
            f"{result.get('effectiveRunId')}. "
            f"{len(candidates)} eligible resume candidate(s) were offered; entries filtered out "
            f"before offering (wrong name shape, no input.digest, glossary siblings) are not "
            f"counted, so this is not a count of directories under runs/.",
            file=sys.stderr,
        )
    return result


def _call_resume_setup(script: Path, payload: dict, dirs: dict, durable_root_str, plugin_root_str) -> dict:
    """ONE resume_setup.py --payload-file invocation for the given payload.
    Raises DriverError on a genuine invocation failure (bad output, or
    resume_setup.py's own `success: false`, e.g. a malformed manifest) --
    never on a mere digest MISMATCH, which resume_setup.py itself reports
    as `success: true, resume: false` (a valid, expected outcome, not an
    error).

    Verifies `script` itself, here, even though this function's own only
    current caller (`resolve_run_id()`) already does -- every OTHER
    executed artifact in this file has its verification and its exec in
    the SAME function, and this was the one exception, correct only
    because of which function happens to call it. A guard that holds
    because of the call graph rather than because of where it is written
    is a guard waiting for a second caller that never gets it, and that
    shape has been the source of more than one defect on this boundary.
    Redundant with `resolve_run_id()`'s own check on the single path that
    reaches here today; it makes the property true by inspection of THIS
    function alone, rather than by tracing every caller, for whatever
    paths reach it later."""
    _refuse_unless_executable_leaf(script, "resume_setup.py")
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


def accepted_run_id(run_result: dict) -> str:
    """The `effectiveRunId` out of a resolve_run_id() payload, validated
    with this file's own validate_run_id() before any caller is allowed to
    build a path or an argv out of it.

    resume_setup.py mints and validates its own ids, so in practice this
    check passes -- which is exactly why it was missing here, and exactly
    why it belongs. Every downstream consumer of this value splices it,
    UNQUOTED, into something that trusts it: claim_record.claimed_path()
    builds `runs/<RUN_ID>/.claimed.<seg>` from it (and, since #438, RAISES
    on an unsafe one rather than returning a path outside the durable
    root), build_codex_job_argv() passes it to codex_job.py as `--run-id`,
    and translate_dispatch_token()/review_dispatch_token() bake it into
    every dispatch_token this run writes. Taking `run_result["effective
    RunId"]` unchecked made all of that conditional on a SIBLING script's
    behaviour rather than on anything provable here -- and this driver
    already owned an identical validate_run_id() it simply never applied
    to the one id it actually runs on.

    Refuses with exit 2 (an environment/contract failure, not a gate
    refusal): a resume_setup.py that answers with a run id this driver
    cannot safely use is a broken install or a tampered sibling, never a
    project-state decision an operator can act on."""
    run_id = run_result.get("effectiveRunId")
    problem = validate_run_id(run_id)
    if problem is not None:
        fatal(
            f"resume_setup.py returned an unusable effectiveRunId: {problem} "
            f"This driver refuses to build claim paths, dispatch tokens or "
            f"codex_job.py argv out of it.",
            exit_code=2,
        )
    return run_id


def run_resume_literal(run_result: dict) -> str:
    """resolve_run_id()'s own boolean `resume` field as the literal string
    select_segments.py's `--run-resume` accepts ("true"/"false", its
    argparse `choices`).

    Deliberately strict about the field being a real bool rather than
    coercing whatever is there with a truth test. `--run-resume` is a
    RELAY, and select_segments.py's #409 Step 3 fresh-evidence check
    branches on the two literals in OPPOSITE directions: "false" arms a
    refusal (a fresh id colliding with pre-existing dispatch evidence),
    "true" arms a different, weaker one (a resumed id with no digest). A
    missing or non-bool `resume` silently coerced to False would relay a
    verdict this driver never actually received into a security gate --
    the one place a plausible default is worse than a refusal."""
    resume = run_result.get("resume")
    if not isinstance(resume, bool):
        fatal(
            f"resume_setup.py's 'resume' field is {resume!r}, not a JSON boolean -- "
            f"select_segments.py's --run-resume relays exactly this field into its "
            f"#409 Step 3 fresh-evidence check, and a guessed value there would be a "
            f"claim about the resume-integrity gate that nothing actually made.",
            exit_code=2,
        )
    return "true" if resume else "false"


# ---------------------------------------------------------------------------
# Phase 2 -- resolve the codex-companion.mjs path by running
# resolve_codex_companion.py, ABORT on any non-zero exit. `dirs[
# "resolve_codex_companion_script"]` is resolve_dirs()'s own answer for
# WHERE that script is -- the durable-root copy in the self-anchored case,
# exactly like every other Phase 2 sibling, or {plugin_root}/assets/
# scripts/resolve_codex_companion.py when --plugin-root is given. SKILL.md's
# own W5 instantiation step (1.4.7) separately runs the SAME script directly
# from the plugin path, because the orchestrating session already has the
# plugin root in hand at that step and there is no reason to prefer an
# indirect copy there -- but that is a DIFFERENT call site than this one,
# not a claim about what this function itself does.
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
    _refuse_unless_executable_leaf(script, "resolve_codex_companion.py")
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


def _open_regular_no_follow_walk(path: Path):
    """Opens `path` component-by-component from the filesystem root, with
    `os.O_NOFOLLOW` at EVERY step -- not just the leaf. This closes two
    gaps `_template_candidate_state()`'s own `os.lstat()` cannot:

      * `lstat(path)` only inspects the FINAL path component. Every
        component BEFORE it is resolved by the kernel exactly like
        `Path.is_file()` would -- so a symlinked ANCESTOR directory (e.g.
        `assets/templates` itself replaced with a symlink, with a genuine
        regular file sitting at the far end of it) passes lstat's own
        check on the leaf while the actual bytes read come from somewhere
        else entirely. Walking with O_NOFOLLOW at every step refuses that.
      * a check (`_template_candidate_state()`) and a SEPARATE later
        `read_text()` are two independent lookups, with a window between
        them for an atomic leaf swap to install a symlink or FIFO. The fd
        this returns is the SAME fd the caller reads from -- one lookup,
        PATHNAME substitution and NEW-INODE substitution both closed
        (nothing can swap what this fd points at out from under the
        caller). What this does NOT close: SAME-INODE mutation. The fd
        pins an inode, not immutable bytes -- a concurrent truncate,
        append, or overwrite-in-place on this exact inode, after this
        function returns it, still changes what the caller's later read
        sees. Closing that needs a content check (e.g. reading once and
        hashing), not a filesystem-structure one.

    Returns `(fd, "file")` on success -- the CALLER owns the fd and must
    close it. Returns `(None, "absent")` if the leaf genuinely does not
    exist, or `(None, "suspicious")` for anything else refused along the
    way (a symlinked or non-directory ancestor, a symlinked/non-regular
    leaf, or any other lookup failure) -- the SAME tri-state vocabulary
    `_template_candidate_state()` already uses, so callers do not need a
    second failure shape.

    THE LEAF OPEN CARRIES `O_NONBLOCK`, and this is load-bearing, not
    cosmetic -- the point-of-use `os.lstat()` this replaced refused a FIFO
    without ever opening it. Without it, a FIFO planted at the leaf path --
    e.g. the checkout-provided one classified regular a moment earlier,
    then swapped for a FIFO before this exact call -- blocks INSIDE
    `os.open()` itself, before type checking ever runs and before the
    caller's own Node timeout can even start: an attacker-triggerable
    hang, strictly worse than the check this fix exists to close.
    `O_NONBLOCK` makes the open on a FIFO with no writer return
    immediately instead of blocking, so classification (`os.fstat()` +
    `S_ISREG`) always runs and can refuse it. This is the SAME shape
    `codex_job.py`'s own `_is_regular()` already uses for the identical
    reason (`O_NOFOLLOW | O_NONBLOCK`) -- read that helper before touching
    this one; the answer already existed one file over. `O_NONBLOCK` has
    done its whole job the moment `S_ISREG` confirms the leaf is a genuine
    regular file (it existed only to keep a FIFO's `open()` from blocking
    before classification could even run), and it is CLEARED on this
    exact descriptor right after that check passes, before this function
    returns it: ordinary regular-file I/O ignores the flag in the common
    case, but `S_ISREG` does not UNIVERSALLY guarantee a nonblocking read
    cannot short-read or return `EWOULDBLOCK` (Linux exposes regular
    pseudo-files, and FUSE implementations choose their own read
    semantics), so leaving it set would ask every caller to be right
    about a guarantee this function does not actually make. Clearing it
    here, once, is cheaper than auditing every current and future caller.

    WHAT THIS DOES NOT DO: verify the file's CONTENT. A process with write
    access to this exact location -- the documented, accepted #412 risk on
    the self-anchored default, closed only by an orchestrating session
    actually passing `--plugin-root` -- can still replace this path with
    an ORDINARY regular file carrying malicious top-level JavaScript.
    Every check here is about STRUCTURE (symlink? directory? genuinely a
    regular file, reached with no substitution along the way?), never
    about whether the bytes inside are the real, unmodified template. No
    filesystem-type check, however thorough, can establish that; it would
    need content-level provenance (a pinned hash checked against a trusted
    value) -- a materially bigger mechanism this fix does not attempt. See
    `call_template_functions()`'s own docstring for where that boundary is
    drawn and why."""
    if not path.is_absolute():
        fatal(f"internal error: {path} must be absolute for the no-follow walk", exit_code=2)
    parts = path.parts
    fd = None
    leaf_fd = None

    def _safe_close(descriptor: int) -> None:
        """Every release of `fd`/`leaf_fd` goes through here, and EVERY
        caller detaches ownership (sets its own variable to None) BEFORE
        calling this, never after. `close()` is not guaranteed to succeed;
        detaching first means a failure inside this function can never
        leave a caller thinking it still owns something it already tried
        to give up, which is what a retried close on the same already-
        failed fd would mean."""
        try:
            os.close(descriptor)
        except OSError:
            pass

    try:
        fd = os.open(parts[0], os.O_RDONLY | os.O_DIRECTORY)
        for name in parts[1:-1]:
            next_fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            closing, fd = fd, None
            _safe_close(closing)
            fd = next_fd
        leaf_fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        closing, fd = fd, None
        _safe_close(closing)
        # fstat() is INSIDE this same try, not a bare statement after it --
        # an EIO/ESTALE here (a network/FUSE filesystem, damaged storage)
        # is exactly the metadata-failure shape this whole tri-state
        # design exists to catch; letting it propagate as an uncaught
        # exception (instead of the documented "suspicious" verdict) AND
        # leaking leaf_fd until process exit was the bug, not a variant of
        # the same fix already applied to codex_job.py's _is_regular().
        st = os.fstat(leaf_fd)
        if not stat.S_ISREG(st.st_mode):
            closing, leaf_fd = leaf_fd, None
            _safe_close(closing)
            return None, "suspicious"
        # O_NONBLOCK must be cleared here, before this function returns the
        # fd -- the caller reads it as an ordinary EOF-complete text stream
        # (os.fdopen(fd, "r").read()). Ordinary disk files ignore the flag,
        # but S_ISREG does not UNIVERSALLY guarantee that -- Linux exposes
        # regular pseudo-files, and FUSE implementations choose their own
        # read semantics, so a nonblocking short-read or EWOULDBLOCK on
        # some regular-typed entry is not provably impossible. The flag
        # has done its whole job the moment S_ISREG passes (it existed
        # ONLY to keep a FIFO's open() from blocking before classification
        # could run); clear it now, on this SAME verified descriptor,
        # guarded the same way every other metadata call in this function
        # already is.
        try:
            current_flags = fcntl.fcntl(leaf_fd, fcntl.F_GETFL)
            fcntl.fcntl(leaf_fd, fcntl.F_SETFL, current_flags & ~os.O_NONBLOCK)
        except OSError as exc:
            closing, leaf_fd = leaf_fd, None
            _safe_close(closing)
            print(
                f"segment_dispatch_driver.py: warning: could not clear "
                f"O_NONBLOCK on {path} after classifying it regular: {exc}; "
                f"treating as suspicious",
                file=sys.stderr,
            )
            return None, "suspicious"
    except FileNotFoundError:
        if fd is not None:
            closing, fd = fd, None
            _safe_close(closing)
        if leaf_fd is not None:
            closing, leaf_fd = leaf_fd, None
            _safe_close(closing)
        return None, "absent"
    except OSError as exc:
        if fd is not None:
            closing, fd = fd, None
            _safe_close(closing)
        if leaf_fd is not None:
            closing, leaf_fd = leaf_fd, None
            _safe_close(closing)
        print(
            f"segment_dispatch_driver.py: warning: no-follow walk to {path} "
            f"refused: {exc}; treating as suspicious",
            file=sys.stderr,
        )
        return None, "suspicious"
    return leaf_fd, "file"


def _refuse_unless_executable_leaf(path: Path, label: str) -> None:
    """Full-path no-follow verification for an executable artifact --
    root, EVERY intermediate directory, AND the leaf itself, via
    `_open_regular_no_follow_walk()` above -- at this specific artifact's
    OWN point of use, immediately before it is Popen'd. Not upfront,
    batched, inside `resolve_dirs()`: an artifact this particular run
    never actually reaches should never have to exist just to call
    `resolve_dirs()` -- many fixtures throughout this file's own test
    suite build a MINIMAL sibling set on purpose, staging only what the
    property under test needs, and an upfront check requiring all of them
    made `resolve_dirs()` itself fail for reasons unrelated to what those
    tests exercise. Point-of-use matches the ONE artifact that already
    had full protection before this fix -- the template, checked inside
    `call_template_functions()`, never inside `resolve_dirs()` -- so this
    makes every executed artifact consistent with that existing
    precedent, not a new, third shape.

    Every one of this file's OWN existing point-of-use checks
    (`select_segments_script.is_file()`, `codex_job_script.is_file()`,
    etc.) already fired at exactly this same call site, for exactly this
    same "not found" case -- `Path.is_file()` just could not tell a
    genuine regular file from a symlink pointing at one. This replaces
    each of those in place, strengthened, not a new site added
    elsewhere.

    THE RE-WALK FROM `/` AT EVERY CALL SITE IS DELIBERATE, NOT AN
    OVERSIGHT TO OPTIMIZE AWAY. Every one of this function's callers
    walks its OWN full path from the filesystem root, independently, even
    when two artifacts share the same parent directory -- so the SAME
    unmodified `_open_regular_no_follow_walk()` verifies every single
    executed path, with no exceptions and no variant shapes. A future
    "optimization" sharing one directory `dir_fd` across multiple leaf
    opens would reintroduce exactly the surface this design avoids. Each
    narrower mechanism written for this boundary has had its own gap, in
    a different layer than the one before it; the walk below is the only
    one that has been attacked from every layer and held. Widening its
    use costs a few redundant syscalls per artifact, dwarfed by the
    subprocess spawn each call guards, and buys the property that matters
    here: there is exactly ONE piece of code deciding whether an
    executed path is safe, so it can be audited once rather than per
    call site."""
    fd, state = _open_regular_no_follow_walk(path)
    if fd is not None:
        os.close(fd)
    if state != "file":
        fatal(
            f"{label} at {path} is not usable (state={state}) -- refusing "
            f"to derive an executable path that is not reachable without "
            f"following a symlink somewhere on the way (the root, an "
            f"intermediate directory, or the leaf itself)",
            exit_code=2, artifact_path=str(path), artifact_state=state,
        )


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
    "the template already checks this.")

    THE READ BELOW IS THE ACTUAL TRUST BOUNDARY, not _self_anchored_
    template_path()'s own resolution. dirs["template_script"] can come from
    EITHER resolve_dirs() branch -- the self-anchored one, already fail-
    closed via _self_anchored_template_path(), or the --plugin-root one,
    which just joins a path with no check of its own (it is TOLD which
    plugin to trust, so there is nothing for it to probe). Using
    _open_regular_no_follow_walk() HERE, unconditionally, protects both:
    whichever branch produced this path, this driver refuses a symlink
    ANYWHERE on the path to it (not just the leaf), a non-regular leaf, and
    the check/read race a separate is_file()-then-read_text() pair leaves
    open -- one fd, opened with O_NOFOLLOW the entire way down, is both the
    verification and the bytes executed.

    STILL NOT A CONTENT CHECK. This establishes the path structurally
    reaches a genuine, unsubstituted regular file -- it says nothing about
    whether that file's BYTES are the real, unmodified template. A process
    with write access to this exact location (the documented, accepted
    #412 risk on the self-anchored default; --plugin-root is the actual
    closure) can still replace it with an ordinary regular file carrying
    malicious top-level JavaScript, and no filesystem-structure check can
    detect that -- it would take a pinned content hash checked against a
    trusted value, which this fix does not attempt. Narrow any claim about
    what this closes accordingly: structural substitution (symlinks,
    ancestor swaps, non-regular entries, the check/read race), not content
    tampering of a genuinely regular file at a location already writable
    by whatever produced it.

    THE TEMPLATE READ BELOW IS UNBOUNDED, AND NOTHING TIME-LIMITS IT. The
    accepted fd is handed to a plain `fh.read()` -- no size cap, no
    deadline -- and the 60s bound this function DOES enforce (the `node`
    subprocess.run() call further down) only starts once that read has
    already finished. A genuine regular file at the verified path that is
    huge or, on a stalled network/FUSE filesystem, slow to deliver its
    bytes can exhaust memory or hang here, before Node ever starts and
    before any timeout in this function has a chance to apply. Disclosed,
    not fixed here: closing it needs a size ceiling and a deadline-bound
    read, the same shape `codex_job.py` already had to add for its own
    unbounded artifact drain -- a materially bigger change than this
    function's own scope."""
    template_path = dirs["template_script"]
    template_fd, template_state = _open_regular_no_follow_walk(template_path)
    if template_fd is None:
        fatal(
            f"mass-translate-wf.template.js at {template_path} is not usable "
            f"(state={template_state}) -- refusing rather than following a "
            f"symlink (anywhere on the path, not just the leaf) or reading a "
            f"non-regular entry",
            exit_code=2, template_path=str(template_path), template_state=template_state,
        )
    try:
        # Unbounded and undeadlined -- see this function's own docstring
        # ("THE TEMPLATE READ BELOW IS UNBOUNDED") for why this is a
        # disclosed, not closed, gap.
        with os.fdopen(template_fd, "r", encoding="utf-8") as fh:
            template_text = fh.read()
    except OSError as exc:
        fatal(f"could not read {template_path}: {exc}", exit_code=2)
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


def draft_token_owner(seg: str, segments_dir: Path) -> "str | None":
    """#458. The RUN_ID that `seg`'s draft on disk is stamped for, or None
    when there is no draft, it cannot be read or parsed, or its
    `dispatch_token` names no owner.

    The PARSE is byte-for-byte claim_record.py's own `draft_owner_run_id()`
    -- which is itself byte-for-byte select_segments.py's `draft_run_id()`
    and draft_ready.py's `_claim_run_id()`, duplicated per this project's
    "no shared lib between self-contained scripts" convention. Do not
    "simplify" it back to `split(":", 1)[0]`: that returns a TRUTHY owner
    for `"RUN-A"` and `"RUN-A:"`, which all three peers reject, and a guard
    disagreeing with the three components that decide ownership everywhere
    else is worse than no guard -- here it would refuse a pinned invocation
    over a draft that, to every other reader in this plugin, names nobody.
    (codex code-review round 1, MAJOR: the first cut of this function did
    exactly that, and also accepted `"../escape:seg01"` as an owner.)
    `partition`, not `rsplit`: a seg id may itself contain a colon
    (`FRONTBACK:errata_02`), so only the FIRST separator delimits the run id.

    The owner is additionally required to be a SAFE run id by this file's own
    validate_run_id() -- the same allowlist every other run id here passes
    before it is compared or spliced. An unsafe prefix is not an owner this
    function will report; it is a draft whose ownership cannot be
    established.

    None means "no owner this function can establish", NEVER "unowned" --
    every caller must treat it as "no contradiction found", not as
    permission. That asymmetry is the whole safety property: the gate below
    refuses only on a token it positively parsed into a DIFFERENT, valid
    run, so a read failure can never manufacture a refusal, and
    (deliberately) can never manufacture an approval either, because the
    pre-existing `draft_ready.py --expect-token` gate downstream still sees
    the draft."""
    draft_path = segments_dir / f"{seg}.draft.json"
    try:
        obj = json.loads(draft_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        # codex code-review round 1, MINOR: read_text() raises
        # UnicodeDecodeError -- a ValueError, NOT an OSError -- on a draft
        # that is not valid UTF-8. Uncaught it escaped to run()'s generic
        # handler and aborted the whole pinned invocation with exit 2,
        # contradicting this function's own "None on a read failure"
        # contract in the one direction that costs the operator every
        # OTHER selected segment.
        return None
    if not isinstance(obj, dict):
        return None
    token = obj.get("dispatch_token")
    if not isinstance(token, str):
        return None
    run_id, sep, rest = token.partition(":")
    if not sep or not run_id or not rest:
        return None
    if validate_run_id(run_id) is not None:
        return None
    return run_id


# #742. The categories whose foreign-token drafts the UNPINNED gate below
# deliberately does NOT protect. An EXEMPTION list rather than an inclusion
# list, and the direction is the point: a category added to
# select_segments.py's ALL_CATEGORIES later defaults to PROTECTED (the gate
# refuses and the operator decides) rather than to silently destroyed, which
# is the only safe default for a guard whose false-GREEN destroys human work.
#
# `stale` alone is exempt, and its exemption is bounded by a SECOND gate
# rather than granted outright: a `stale` unit is one that WAS converged and
# whose cache key has drifted, so its draft carries -- by definition -- the
# token of the run that converged it, and the same cache-key move is what
# mints the fresh RUN_ID. Refusing over it would refuse EVERY
# input-change-driven retranslation the cache-key design exists to perform.
# What keeps that from being a hole is that dispatching a previously-converged
# unit already requires an explicit --allow-retranslate-converged
# (select_segments.py's own previously_converged refusal), so the destruction
# this exemption permits is one the operator authorised in as many words.
#
# `not_started` is deliberately NOT exempt (codex plan review round 2,
# BLOCKER). classify_segment() calls a segment `not_started` on an ABSENT
# ledger record alone -- it never looks at whether a draft exists -- so a
# partial restore that keeps segments/<seg>.draft.json and loses
# runs/ledger.d/<seg>.json lands surviving editorial work in that category.
# Exempting it would buy nothing in exchange: a genuinely new segment has no
# draft at all, draft_token_owner() returns None for it, and the gate cannot
# refuse over it either way.
#
# The member strings are select_segments.py's own category vocabulary
# (its ALL_CATEGORIES), restated here per this project's "no shared lib
# between self-contained scripts" convention -- the same way this file
# already restates other cross-script literals. A rename on that side would
# make this set exempt nothing, which is the SAFE direction of failure for
# this particular set (more segments protected, never fewer).
FOREIGN_DRAFT_GATE_EXEMPT_CATEGORIES = frozenset({"stale"})


def segs_covered_by_foreign_draft_gate(segs: list, select_result: dict) -> list:
    """The subset of `segs` the UNPINNED foreign-draft gate checks: everything
    whose classification is not exempt, with an unrecognised or missing entry
    treated as COVERED.

    Lives beside the set it reads rather than inline at the call site so the
    predicate, the set, and the reason for the set's direction are one thing
    to read -- and so the two edge branches (a seg absent from
    `classification`, an entry that is not a dict) are reachable without a
    full CLI round trip."""
    classification = select_result.get("classification")
    if not isinstance(classification, dict):
        # A CONTRACT break with the selector, not a known gap:
        # select_segments.py builds `classification` over every manifest
        # candidate and both selection branches index it, so every seg that
        # reaches here has an entry. Exit 2 rather than returning an empty
        # list, because an empty covered set is exactly what a CLEAN project
        # looks like -- "the filter found nothing" and "the filter never ran"
        # must not print the same verdict.
        fatal(
            "select_segments.py's JSON output has no 'classification' object -- "
            "refusing rather than checking an empty set of drafts, which would be "
            "indistinguishable from a project with no foreign drafts at all",
            exit_code=2,
        )

    def _is_exempt(seg) -> bool:
        entry = classification.get(seg)
        return (
            isinstance(entry, dict)
            and entry.get("category") in FOREIGN_DRAFT_GATE_EXEMPT_CATEGORIES
        )

    return [seg for seg in segs if not _is_exempt(seg)]


def refuse_run_over_foreign_drafts(
    segs: list, run_id: str, segments_dir: Path, *, pinned: bool, resumed: bool,
) -> None:
    """Refuse the whole invocation when any segment in `segs` carries a draft
    stamped for a different run -- before a single segment is dispatched.

    The branch this closes: derive_next_action() gates the draft with
    `draft_ready.py --expect-token translate_dispatch_token(run_id, seg)`, and
    a token naming another run fails that gate and falls through to
    `{"action": "translate"}`, overwriting the draft and exiting 0.
    claim_record.py permits a foreign token when no claim record exists, so
    nothing downstream stops it either.

    #458 shipped this gate PINNED-ONLY, on the argument that a pin is a
    declared statement about which run this invocation belongs to while an
    unpinned invocation makes no such statement. #742 is the issue that
    revisited that scope, and the argument does not survive contact with the
    measured consequence: on a live 74-segment volume three hand-fixed drafts
    were re-translated and roughly twenty hand edits destroyed, reported as
    success (`"kind": "translate", "adopted": false, "reason": "promoted"`),
    with 61 further hand-fixed drafts exposed in the same invocation. Nothing
    about that loss requires the operator to have declared a run id first.
    Destroying editorial work already committed to disk is not something a
    missing declaration makes acceptable; it is something that must be ASKED
    about, because no gate in this plugin can tell a draft holding hand fixes
    from one that merely needs re-translating.

    WHAT DIFFERS BETWEEN THE TWO CALLERS is the SEGMENT SET, and the caller
    -- not this function -- decides it: pinned passes every selected segment
    (#458's behaviour, unchanged byte for byte); unpinned passes the selected
    segments whose classification is not in
    FOREIGN_DRAFT_GATE_EXEMPT_CATEGORIES. `pinned` and `resumed` here select
    ONLY the wording of the explanation and the remedy, never which segments
    are checked -- keeping the comparison itself single-sourced, so the two
    paths cannot drift into two different notions of "foreign".

    ONE EXCEPTION, and it is not a hole: on a
    `--from-cap`/`--from-converged`/`--from-stalled` invocation, Step 1 has
    ALREADY re-stamped every ADMITTED draft's dispatch_token to this run
    (select_segments.py's D4/D9 write) by the time this gate runs, so an
    admitted draft can never look foreign here. That is the claim's whole
    purpose -- admission is the authorized transfer of a draft between runs
    -- and it is stated rather than left for a reader to discover: what this
    gate protects on a claim invocation is the segments the claim did NOT
    admit, which is exactly the population #742 was reported from.

    Refuses the WHOLE invocation rather than skipping the offending
    segments: a gate that refuses before the loop cannot half-dispatch, and
    silently dropping ids from a set the operator named is the failure mode
    this file spends most of its refusals avoiding."""
    foreign = []
    for seg in segs:
        owner = draft_token_owner(seg, segments_dir)
        if owner is not None and owner != run_id:
            foreign.append((seg, owner))
    if not foreign:
        return
    detail = ", ".join(f"{seg} (stamped for {owner})" for seg, owner in foreign)
    # Built once for the same reason `detail` and `common` are: the two
    # fatal() calls below carry a payload key two tests assert on by name,
    # and a rename applied to one branch only is exactly the drift a single
    # local prevents.
    foreign_payload = [{"seg": seg, "run_id": owner} for seg, owner in foreign]
    common = (
        f"{len(foreign)} selected segment(s) carry a draft stamped for a DIFFERENT run: "
        f"{detail}. Dispatching them under {run_id!r} would retranslate those drafts and "
        f"discard whatever they hold, because a draft whose dispatch_token names another "
        f"run fails this driver's own token gate and falls through to translate. Nothing "
        f"was dispatched."
    )
    if pinned:
        fatal(
            f"--resume-from-run-id {run_id!r}: {common} Name only the ids that belong to "
            f"this run with --only-segs, or re-run without --resume-from-run-id.",
            exit_code=1,
            pinned_run_id=run_id,
            foreign_drafts=foreign_payload,
        )
    # #742. Every clause below has to be TRUE on every path it can print on,
    # which is why the cause is READ from `resumed` rather than inferred: a
    # foreign draft does NOT prove a fresh id was minted. resolve_run_id()
    # returns the NEWEST digest-matching candidate, so a draft naming an
    # OLDER run reaches this gate on a perfectly ordinary resume, and a
    # message that announced a mint there would be diagnosing the wrong
    # thing.
    cause = (
        f"This invocation RESUMED run {run_id!r}, and these drafts name another one -- "
        f"not necessarily an earlier one: the first digest-MATCHING candidate wins, so a "
        f"run created after the one resumed here can still own a draft."
        if resumed
        else (
            f"This invocation MINTED a fresh RUN_ID {run_id!r}, because its inputs matched "
            f"no eligible resume candidate."
        )
    )
    # The remedies are each qualified rather than merely listed, because an
    # unqualified one is a dead end the operator only discovers by running
    # it: --only-segs does not RECOVER anything (the named ids hit this same
    # gate); a pin alone still refuses over a SECOND owner in the same
    # selection, and refuses anyway if the inputs have moved; and re-stamping
    # an invalid draft does not resume its review -- validate_draft.py fails
    # and derivation then either halts the segment as invalid_post_fix_draft
    # or retranslates it, and WHICH of the two depends on state the operator
    # is not looking at, so this must not promise either.
    fatal(
        f"{common} {cause} Nothing here is repaired automatically -- re-stamping a draft "
        f"on your behalf is the same silent mutation this refusal exists to stop. What "
        f"you can do, per remedy: (1) re-run with --only-segs naming only the UNAFFECTED "
        f"ids -- that CONTINUES the rest, it does not recover the ids above, which hit "
        f"this same gate; (2) re-run with --resume-from-run-id <owner> TOGETHER WITH "
        f"--only-segs naming only the ids that run owns -- the pin alone is not enough, "
        f"because this gate still checks every selected segment and would refuse over a "
        f"second owner, and it refuses anyway unless that run's input digest still "
        f"matches this invocation; (3) decide per segment by hand -- DELETE the draft to "
        f"accept the retranslation (which is also how an unfinished draft picks up a "
        f"style-bible or canon edit), or re-stamp its dispatch_token to {run_id!r} to "
        f"keep the work, which preserves it only if the draft then passes draft_ready.py "
        f"AND validate_draft.py. Back up before re-stamping: a draft that fails "
        f"validate_draft.py is not resumed into review.",
        exit_code=1,
        resolved_run_id=run_id,
        resumed=resumed,
        foreign_drafts=foreign_payload,
    )


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
                          effort: str, model: str, plugin_root_str, run_id: str, node_bin: str = "node") -> list:
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

    `run_id` (#438 D8): codex_job.py's `--run-id` is now REQUIRED --
    main() there returns exit 2 without it (never derived from
    --expect-token: a malformed token would then read as "no claim
    record", i.e. "not claimed", and proceed -- the exact silent
    degradation D8 refuses). Always this driver's own `ctx.run_id`,
    passed unconditionally (never omitted the way --model/--plugin-root
    are), for both translate AND review dispatches -- codex_job.py's own
    claim check only actually gates a translate, but its CLI makes the
    flag required on every invocation regardless of kind.

    UNLIKE `node_bin` above, `--run-id` is INSIDE the equivalence surface,
    and both sides emit it: mass-translate-wf.template.js's own
    translateDrivePrompt (template:974) and reviewDrivePrompt
    (template:1036) splice `--run-id " + RUN_ID + "` in the same argv
    position this function does, immediately after --expect-token and
    before --disp. Nothing excepts it from the comparison either --
    tests/segment_dispatch_driver.test.py calls
    _assert_argv_positionally_equivalent() with
    excepted_value_flags=("--disp", "--prompt-file") only, at BOTH of its
    call sites -- so this field is already compared name, position
    and value on both the translate and the review path. An earlier
    revision of this paragraph said the template emitted no --run-id at
    all and called the resulting divergence "a known, disclosed gap": that
    was true of the template as it stood when #438 started and stopped
    being true in the same commit, so it is corrected rather than kept.
    Do NOT re-add an exception for this flag to the equivalence test to
    "fix" a failure here -- a disagreement on --run-id now means the two
    dispatch paths genuinely stamp different ids, which is the defect the
    comparison exists to catch."""
    argv = [
        "--kind", kind,
        "--companion", companion_path,
        "--cwd", str(durable_root),
        "--seg", seg,
        "--prompt-file", str(prompt_file),
        "--expect-token", expect_token,
        "--run-id", run_id,
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
        # Not _refuse_unless_executable_leaf() -- that one fatal()s
        # (raises), and this function's own contract, stated in its own
        # docstring, is that an invocation failure here is a per-segment
        # outcome, never driver-fatal. Same full-path no-follow check,
        # inlined so a bad path becomes a {"success": False, ...} return
        # like every other failure in this function, not an exception.
        _cache_key_fd, _cache_key_state = _open_regular_no_follow_walk(cache_key_script)
        if _cache_key_fd is not None:
            os.close(_cache_key_fd)
        if _cache_key_state != "file":
            return {
                "success": False,
                "error": f"cache_key.py at {cache_key_script} is not usable "
                         f"(state={_cache_key_state})",
            }
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
    # Same reason as the cache_key.py check above: inlined, not
    # _refuse_unless_executable_leaf(), so a bad path stays a per-segment
    # {"success": False, ...} outcome, never a driver-fatal exception.
    _ledger_update_fd, _ledger_update_state = _open_regular_no_follow_walk(ledger_update_script)
    if _ledger_update_fd is not None:
        os.close(_ledger_update_fd)
    if _ledger_update_state != "file":
        return {
            "success": False,
            "error": f"ledger_update.py at {ledger_update_script} is not usable "
                     f"(state={_ledger_update_state})",
        }
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
                 durable_root_str, plugin_root_str, node_bin, session_id, claims=None):
        self.dirs = dirs
        self.run_id = run_id
        self.translate_cfg = translate_cfg
        self.companion_path = companion_path
        self.durable_root_str = durable_root_str
        self.plugin_root_str = plugin_root_str
        self.node_bin = node_bin
        self.session_id = session_id
        # #438 D3: {seg: profile} for every id parse_claims_field() admitted
        # from select_segments.py's own 'claims' field THIS invocation --
        # folded in here per D3's own requirement and reported alongside
        # run()'s own journal/result. {} when this run requested no claim.
        #
        # ENFORCING, not audit-only: #450's claim_capability_refusal_for_
        # translate() refuses a translate dispatch directly off this dict,
        # unconditionally and before any ledger write. It is read INSTEAD
        # of the on-disk record there, never reconciled against it -- that
        # is the whole point of the layer. claim_refusal_for_translate()
        # still does not consult it, and deliberately so: that check is
        # keyed off the durable record because it must answer for runs and
        # processes this dict cannot reach. Both run, in that order.
        self.claims = claims if claims is not None else {}


def _run_gate(script: Path, argv_rest: list, ctx: "DispatchContext", *, supports_plugin_root: bool) -> bool:
    """True iff the gate script exits 0 -- a genuine not-ready (non-zero
    exit) is never an error here, only a script that could not be invoked
    at all is (a driver-level fatal, matching every other subprocess
    invocation in this file)."""
    _refuse_unless_executable_leaf(script, script.name)
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


# The fixed head of every promotion note. It exists ONLY so that
# _carried_promotion_note() can recognise the SHAPE of evidence it must not
# erase without re-hashing the draft. NOTHING may accept a note on a prefix
# match: _translate_redispatched_since() compares the whole string, because
# the part this constant leaves out -- the draft's own hash -- is the entire
# point of the note. If you find yourself reaching for .startswith() in a
# reader, that is the bug.
_TRANSLATE_PROMOTION_NOTE_PREFIX = (
    "translate promoted by segment_dispatch_driver.py (#620); promoted draft sha1: "
)


def _translate_promotion_note(draft_sha1: str) -> str:
    """The ledger note the translate branch stamps AFTER codex_job.py promotes
    a draft, and the only thing _translate_redispatched_since() below accepts
    as proof that the draft currently on disk came out of a translate.

    This whole string is BEHAVIOUR, not prose: the reader compares a
    fragment's note against this function's return value for EQUALITY, so
    rewording the literal is a behaviour change, not a copy edit. One builder
    rather than a concatenation at each end, so the writer and the reader
    cannot drift apart. Written at exactly two sites, both in
    process_segment()'s translate branch -- the stamp after a promotion, and
    the carry-forward in the pre-dispatch write that would otherwise erase it
    -- and never by the workflow template; see the reader's own docstring for
    why not."""
    return _TRANSLATE_PROMOTION_NOTE_PREFIX + draft_sha1


def _carried_promotion_note(dirs: dict, seg: str) -> "str | None":
    """The promotion note ALREADY standing in runs/ledger.d/{seg}.json, so
    process_segment()'s pre-dispatch in_progress write can re-state it rather
    than erase it -- otherwise one transient translate failure destroys the
    evidence and turns the very case this marker exists to keep retriable
    into the permanent invalid_post_fix_draft halt.

    Why re-stating is safe, and cannot manufacture evidence: the note is
    returned VERBATIM and nothing between this read and that write touches
    the canonical draft, so `note == _translate_promotion_note(<current
    draft's sha1>)` holds afterwards exactly when it held before. A note left
    over from a draft that has since moved keeps failing the reader's
    equality test on its own -- carrying it forward does not make it true,
    and the reader, not this function, is what decides.

    Deliberately NOT mtime-relative: this is asked at the write site, where
    the reader's `newer than the review` conjunct is not in scope and will be
    re-evaluated against the fresh fragment anyway. It is status-relative,
    matching the reader: only an in_progress fragment ever legitimately
    carries this prefix.

    Every doubt -- missing, unreadable, unparseable, not an object, any other
    status, absent or non-string note, or a note of some other shape (the
    #432/#461 reopen note, a rejection note) -- returns None, which leaves
    the pre-dispatch write exactly as note-less as it was before #620."""
    fragment_path = dirs["runs_dir"] / "ledger.d" / f"{seg}.json"
    try:
        fragment = json.loads(fragment_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    if not isinstance(fragment, dict) or fragment.get("status") != "in_progress":
        return None
    note = fragment.get("note")
    if isinstance(note, str) and note.startswith(_TRANSLATE_PROMOTION_NOTE_PREFIX):
        return note
    return None


def _in_progress_fragment_since(dirs: dict, seg: str, review_path: Path) -> "dict | None":
    """runs/ledger.d/{seg}.json parsed, iff it is strictly newer than
    `review_path` AND records status "in_progress" -- otherwise None.

    Exactly the part the two helpers below agree on: one physical fact read
    off one file. They diverge on what MORE they demand of it, and that
    divergence is deliberate and documented in each one's own docstring --
    sharing this reader is not a step toward collapsing them into one
    predicate, which both docstrings explicitly refuse. If RAW #1's other
    formulation ("the last in_progress write is newer than the review") ever
    lands in the sibling, this base splits again.

    Every doubt -- missing, unreadable, unparseable, not an object, equal or
    older mtime, any other status -- returns None, which both callers map to
    False."""
    fragment_path = dirs["runs_dir"] / "ledger.d" / f"{seg}.json"
    try:
        fragment_mtime_ns = fragment_path.stat().st_mtime_ns
        review_mtime_ns = review_path.stat().st_mtime_ns
    except OSError:
        return None
    if fragment_mtime_ns <= review_mtime_ns:
        return None
    try:
        fragment = json.loads(fragment_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    if not isinstance(fragment, dict) or fragment.get("status") != "in_progress":
        return None
    return fragment


def _translate_redispatched_since(dirs: dict, seg: str, review_path: Path,
                                  current_sha1: str) -> bool:
    """True iff runs/ledger.d/{seg}.json is durable evidence that a translate
    THIS driver ran promoted the draft whose content hash is `current_sha1`,
    and did so strictly after `review_path` was last written.

    Used by derive_next_action()'s `if not draft_ok:` branch as the one thing
    that can turn an invalid, moved draft back into a plain "translate": a
    draft that differs from the review's own recorded draft_sha1 was either
    edited by a fix turn (terminate, do not discard it) or produced by a
    genuine same-run retranslate (retry it). translate_dispatch_token(run_id,
    seg) is a pure function of run_id and seg, so a legitimately re-selected
    segment produces the byte-identical token a fix turn's "copy dispatch_token
    exactly" instruction also produces -- the sha1 comparison alone cannot tell
    the two apart, which is why this evidence exists at all.

    WHAT IT REQUIRES, and why each conjunct is load-bearing (#620; the mtime
    comparison used to be the whole test, and each of the three cases below
    defeated it):

    1. The fragment is NEWER than the review. Cheap first gate, unchanged.
       Necessary and nowhere near sufficient: process_segment() has SIX
       write_ledger() sites and only ONE of them ORIGINATES this evidence.
       Four cannot carry it at all (converged, converged-by-rejection, cap,
       and the #432/#461 reopen-capped in_progress write that precedes a
       REVIEW), and the sixth -- the pre-dispatch in_progress write -- only
       ever RE-STATES a note already on disk, verbatim, via
       _carried_promotion_note(); it can preserve a match but never mint
       one. The cap in particular is written after the very review it caps,
       so it is necessarily newer -- a capped segment an operator then
       hand-repaired into an invalid state was silently re-translated
       over.

    2. status == "in_progress". Not sufficient EITHER, and narrowing to it
       alone would not have closed the defect: the reopen-capped write is
       itself `{"status": "in_progress", "note": ...}`, and when its review
       dispatch fails process_segment() returns without a further write, so
       that fragment stays the newest artifact over a draft no translate
       produced. Never test the ABSENCE of a note instead -- that write
       already carries one, and an absence test would silently re-break the
       day any other in_progress write grows one.

    3. The note equals _translate_promotion_note(current_sha1) -- one
       equality over the whole string, never a prefix test. Two distinct
       properties ride it. That such a note exists at all says a translate
       promoted a draft, because it is stamped only after codex_job.py
       reports a genuine promotion and never before the dispatch (the
       writer's own comment in process_segment()'s translate branch
       enumerates the three states a pre-dispatch stamp would falsely cover).
       That the hash in it is the CURRENT draft's says the promoted draft is
       the one still on disk: a constant marker would say "a translate ran"
       while the operator's later hand repair sat in the file, and
       re-translating over that repair is the exact harm this function exists
       to prevent.

    `current_sha1` is passed in rather than recomputed: derive_next_action()
    has already computed it for its own draft_sha1 comparison, so the two
    readings cannot disagree and the draft is hashed once. The call site only
    reaches this function when that value is not None.

    What the hash does and does not prove: draft_content_sha1() excludes
    dispatch_token and canonicalises the JSON (draft_sha1.py), so this is
    trusted ledger evidence about the translated-CONTENT projection, not byte
    provenance. Rewriting only the token, the whitespace or the key order
    keeps it valid, and a hand-authored or restored fragment that matches on
    every conjunct passes too. Both sit outside this project's trust boundary
    -- its standing operator rule is that the ledger is never hand-written --
    and neither can silently lose DIFFERENT translated prose, because
    different prose changes the hash.

    Conservative on every doubt: missing, unreadable, unparseable, not an
    object, equal or older mtime, wrong status, absent/unrelated/near-match
    note, or a recorded hash that no longer matches the draft on disk all
    return False. False routes to invalid_post_fix_draft, which stops and
    hands the decision back rather than discarding work -- the same direction
    the sha1 comparison's own "cannot prove it" case already takes. This
    function only ever ADDS a way to prove a genuine retranslate.

    The workflow template's translateStage() also writes a pre-translate
    `{"status": "in_progress"}` fragment (mass-translate-wf.template.js) and is
    deliberately NOT stamped, so a driver pickup of a template-written run
    reads False here. Its translate is a DETACHED dispatch -- a returned DISP
    proves a launch, never a promotion -- so a stamp there would reintroduce
    exactly the "evidence written before the outcome" defect described under
    conjunct 3, and it would have to route a machine-compared hash through an
    LLM agent's payload that recordLedgerCall() never verifies. That segment
    therefore halts, and the halt PERSISTS rather than clearing on a retry --
    the full recovery is in references/ledger-and-resumability.md's site-0
    entry, which is the one place an operator looks.

    One residual this does NOT close, pre-existing and unchanged by #620:
    derive_next_action() hashes the draft and process_segment() dispatches a
    moment later, so a draft saved in between is still overwritten. That
    window existed identically when this test was mtime-only; re-hashing
    before the dispatch would only shrink it, never close it, and the
    per-segment lease codex_job.py already holds is the mechanism that
    covers the dispatch itself."""
    fragment = _in_progress_fragment_since(dirs, seg, review_path)
    # No isinstance() guard on the note: _translate_promotion_note() returns a
    # str, and nothing json.loads() can produce compares equal to a str except
    # a str -- an absent note is None, which is simply unequal.
    return fragment is not None and fragment.get("note") == _translate_promotion_note(current_sha1)


def _translate_in_progress_since(dirs: dict, seg: str, review_path: Path) -> bool:
    """True iff a runs/ledger.d/{seg}.json fragment written STRICTLY AFTER
    `review_path` records status "in_progress" -- RAW #7 (#441): the
    round-advance branch at the tail of derive_next_action() used to read
    "the draft moved since the review" as proof a fix landed, even when
    the move was a same-run RETRANSLATE. What this proves, exactly: a
    translate was DISPATCHED after this review -- not that one produced
    new prose. Both places that dispatch one write
    `{"status": "in_progress"}` immediately BEFORE the dispatch: this
    driver's own translate branch (see process_segment()'s
    `if action["action"] == "translate":`, just before its codex
    dispatch) and the shipped workflow's translateStage()
    (mass-translate-wf.template.js:1856). A fix turn goes through neither,
    so it writes no fragment at all -- which is what makes the discriminator
    work at all.

    Three states satisfy this predicate without a retranslate having
    happened, and none of them is closed here. (1) A dispatch interrupted
    between the ledger write and the job. (2) A job whose safe_adopt()
    finds the canonical draft already valid and returns 0 without
    launching (codex_job.py, the `self.adopted = True` return). (3) The
    reopen-capped path, which writes `{"status": "in_progress", "note":
    ...}` and then dispatches a REVIEW, not a translate. (3) CANNOT reach
    this call site: the `matched_round_label == "final"` block above
    returns on every path (verified structurally, not by reading), and the
    reopen is only ever dispatched at `final`. (1) and (2) can.

    They are accepted, and the reason is the asymmetry of the two
    mistakes. A false hold costs one extra review at the same label, and a
    review that COMPLETES clears it -- promoting or adopting a verdict
    bound to the current draft makes review.json newer than the fragment,
    so this function reads False on the next derivation. Do not read that
    as unconditional, which an earlier revision of this docstring did: a
    review that FAILS (no launch, a timeout, an attempt rejected by
    validation) changes neither artifact, so a later invocation holds
    again, and persistent review failure repeats it without a bound. Those
    retries stay explicit failures -- the segment lands in summary.failed
    and assemble.py refuses any manifest segment absent from the converged
    population -- so the cost is dispatches, never prose that reaches the
    book. A false ADVANCE, the behaviour this replaces, spends
    a fix round the segment never got and at max_fix_rounds: 1 caps it.
    Binding this to a completion record instead (the .codex_job.<seg>.json
    terminal log) would buy that back at the price of advancing over a
    GENUINE retranslate whose best-effort log was lost or overwritten,
    which is the direction this fix exists to remove. Do not "harden" it
    that way without re-deciding that trade.

    Why _translate_redispatched_since()'s own mtime-only test above is NOT
    reusable here, unmodified, at THIS call site: the workflow also writes
    `{"status": "blocked", "reason": "draft-missing"}` AFTER a numbered
    review (mass-translate-wf.template.js:1754), and a segment left
    `blocked` is explicitly retried via `--only-segs` under the SAME
    run_id (resume_setup.py resolves it to the same run by matching the
    same input digest). A mtime-only guard would hold the round label for
    that recovery too -- a paid same-label review dispatched over a draft
    no translate actually produced. This was a codex review finding
    against an earlier revision of this fix, not a hypothetical: reusing
    the sibling helper as-is regresses exactly this case.

    Why every doubt resolves to False: False is "advance", which is
    today's existing behaviour, so a missing, unreadable, equal-mtime, or
    differently-statused fragment changes nothing this function did not
    already do before it existed. True is the only new outcome this
    function can produce, and all it ever does is trade one round-advance
    for a same-label re-review -- never the reverse, so it can never
    manufacture the live-lock a wrongly-conservative guard would risk.

    Why this helper reads status while the sibling above reads status AND a
    draft-bound note, rather than the two sharing one predicate: they answer
    different questions. This one asks "was a translate DISPATCHED after this
    review", which is all its own call site needs to decline a round advance,
    and its accepted false positives are listed above. The sibling has to
    answer the strictly harder "is the draft ON DISK RIGHT NOW the output of a
    translate", because a wrong True there re-translates over an operator's
    hand repair. A status test cannot answer that -- the reopen-capped write
    is itself `in_progress` -- and neither can a constant marker, which still
    reads True once the operator has edited the promoted draft. That was RAW
    #1; it is fixed (#620), by evidence stamped after codex_job.py reports a
    promotion and naming that draft's own content hash. Do not collapse the
    two helpers into one: widening THIS one to require the note would hold the
    round label over a `blocked`/`review-timeout` recovery the note never
    describes, which is the regression the paragraph above exists to prevent.

    The status spelling checked below is authoritative from
    ledger-fragment.schema.json:13 / FRAGMENT_STATUS_FALLBACK_ENUM
    (ledger_update.py:139-141): the on-disk FRAGMENT enum has exactly five
    values -- pending, in_progress, converged, non_converged, blocked.
    Never write or compare against "stale" here -- that is the
    MATERIALIZED ledger's sixth value (ledger.schema.json), which
    ledger_update.py never writes to a fragment at all."""
    return _in_progress_fragment_since(dirs, seg, review_path) is not None


def derive_next_action(seg: str, ctx: "DispatchContext") -> dict:
    """Returns exactly one of:
      {"action": "translate"}
      {"action": "review", "round_label": "1".."<max_fix_rounds>"|"final"}
      {"action": "review", "round_label": ..., "cause": "fabricated_loc"} -- a
        re-review, same as the row above, but caused SPECIFICALLY by a
        fabricated (inauthentic) finding rather than a stale/absent
        review or a round advance -- see process_segment()'s own retry
        counter, which this marker exists for.
      {"action": "review", "round_label": "final", "reopen_capped": True} -- a
        re-review of a segment a PRIOR invocation may already have capped
        terminally (#432). The marker tells process_segment() to make the
        segment durably recoverable BEFORE it spends the codex job; it is
        never a new action type, still plain "review" as far as dispatch
        goes, exactly like the cause="fabricated_loc" marker above.
      {"action": "review", "round_label": ..., "cause": "rejected_findings"} --
        #461: a re-review in place of what would otherwise be needs_fix
        (numbered rounds) or a terminal cap_reached ("final"), because a
        durable rejection (reject_review.py, segments/{seg}.review_
        rejected.json) still names THIS review and has not yet been spent
        -- an operator judged the stored findings unfounded. At a numbered
        round it advances to the NEXT label; at "final" there is no next
        label, so it re-dispatches "final" and carries reopen_capped as
        well. At "final" this is now the FALL-THROUGH, not the outcome: a
        rejection that also has the draft it was written against and a
        coverage_ok verdict converges the unit instead (see the action
        below). Never routes to "translate"; still plain "review" as far as
        dispatch goes, exactly like the two markers above.
      {"action": "converged_by_rejection", "round_label": "final",
        "rejection": {...}, "reviewed_sha1": ..., "reviewed_token": ...,
        "reviewed_digest": ...} -- #527: the mandatory final round's stored
        verdict is non-clean, an unspent rejection names it, the draft has
        not moved since it was written, and the reviewer itself reported
        coverage_ok. The operator's attested refutation TERMINATES the unit
        as converged; the record travels whole so process_segment() can
        write the operator's own reason into the ledger note, and the three
        reviewed_* fields travel for the same reason cap_reached carries
        them -- the terminal write happens later and must still bind the
        verdict this decision was made from.
      {"action": "needs_fix", "round_label": ..., "findings": [...]}
      {"action": "cap_reached", "findings": [...], "reviewed_sha1": ...,
        "reviewed_token": ..., "reviewed_digest": ...} -- the draft sha1,
        the dispatch_token AND a content digest of the review this cap
        verdict was derived FROM, so process_segment() can refuse the
        terminal write if any of them moved in between (a cap must describe
        bytes a reviewer actually read, and a VERDICT a reviewer actually
        reached -- the digest is what makes the second half true).
      {"action": "already_converged", "round_label": "1".."<max_fix_rounds>"|"final",
        "reviewed_sha1": ..., "reviewed_token": ..., "reviewed_digest": ...}
        -- the ordinary clean convergence. The three reviewed_* fields
        travel for the same reason cap_reached and converged_by_rejection
        carry them: process_segment() commits the ledger write in a LATER
        step and must still be able to bind it to the verdict THIS
        decision was made from.
      {"action": "invalid_post_fix_draft"} -- codex round-3 MAJOR, see the
        `if not draft_ok:` branch below for the full reasoning: an invalid
        draft is NOT always safe to re-translate.
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
                    and not _translate_redispatched_since(
                        dirs, seg, prior_review_path, current_sha1
                    )
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
    # block_id/FN:n/VERSE:vid/NOTE:n reference. What the ported gate tests is
    # the SHAPE of that loc (colon-delimited vs bare token), never whether it
    # resolves against the draft -- see the template's own comment above
    # AUTHENTIC_LOC_RE, and #539 for what the gap cost while notes[] had no
    # conforming spelling. Reading review.json directly with
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
    # The caught DriverError is KEPT, not discarded: the final-round branch
    # below fails on it rather than folding an infrastructure failure into a
    # content verdict (see that branch's own comment), and it is the only
    # place the underlying cause -- "draft not found", "not valid JSON",
    # "draft_sha1.py is not usable" -- survives to reach the operator.
    # It is NOT re-raised: that branch calls fatal(), which raises a NEW
    # DriverError carrying this one's text interpolated into a message that
    # names what was refused. Deliberate -- the operator needs "refusing to
    # record a terminal cap over a draft this invocation never read" more
    # than it needs this exception's identity -- but it does mean the
    # original's exit_code and any extra fields do not survive, so nothing
    # downstream may match on the original exception object.
    current_sha1_error = None
    try:
        current_sha1 = current_draft_sha1(seg, segments_dir, dirs["scripts_dir"])
    except DriverError as exc:
        current_sha1 = None
        current_sha1_error = exc
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
            # reviewed_sha1/reviewed_token/reviewed_digest travel with this
            # decision exactly as they do with cap_reached and
            # converged_by_rejection below, and the digest is taken from the
            # review_obj THIS function parsed -- a re-read here would accept a
            # substitute on its own terms. WHAT the triple buys on this
            # particular fork, which is narrower than on the cap fork, is
            # argued once where the check is actually made: see
            # process_segment()'s already_converged branch.
            return {
                "action": "already_converged",
                "round_label": matched_round_label,
                "reviewed_sha1": reviewed_sha1,
                "reviewed_token": review_obj.get("dispatch_token"),
                "reviewed_digest": _review_verdict_digest(review_obj),
            }
        # codex #392-class MAJOR: a CLEAN review whose draft_sha1 no longer
        # matches the CURRENT draft (edited out-of-band since this review
        # was written -- or the sha1 simply could not be recomputed) must
        # NEVER fall through to already_converged: ledger_update.py's own
        # independent check (enrich_converged_fields, ledger_update.py:
        # 789-792) refuses that convergence write outright, and with no
        # branch that ever re-dispatches a review in that case, every later
        # invocation would repeat the SAME refused write forever -- a
        # live-lock, not a transient failure. There is also nothing to FIX
        # (this review's own findings are empty), so this is never routed
        # through needs_fix either -- the only correct move is a fresh
        # review of the current draft, at the SAME round label (a genuine
        # re-check of what changed, not a new round spent).
        return {"action": "review", "round_label": matched_round_label}

    if matched_round_label == "final":
        # #432: cap_reached used to fire unconditionally here, on the
        # theory that a non-clean mandatory final review is always the
        # true terminal state. That theory misses the exact case the
        # clean branch above (`if clean and coverage_ok:`, the
        # "codex #392-class MAJOR" comment) was already fixed for on the
        # OTHER side of this same fork: a review's own recorded
        # draft_sha1 can go stale the moment the draft changes after the
        # review was written, and staleness does not become less true
        # just because the review happened to be non-clean instead of
        # clean.
        #
        # The clean branch needs its guard because ledger_update.py's own
        # enrich_converged_fields REFUSES a convergence write whose
        # draft_sha1 disagrees with the current draft -- so without a
        # re-dispatch, a stale-but-clean review would live-lock on that
        # refused write forever. This branch has no equivalent write to
        # refuse (cap_reached's own ledger write in process_segment() is
        # unconditional -- {"status": "non_converged", "reason": "cap"}
        # never carries or checks a draft_sha1), so the failure mode here
        # is quieter but just as permanent: this function only ever READS
        # review.json, so a stale non-clean review keeps matching "final"
        # and keeps returning cap_reached, forever -- even after every
        # one of its findings has been applied to the draft by hand and
        # validate_draft_script confirms the result is clean. Verified on
        # a real book run: two segments landed exactly here, every
        # finding applied, draft valid, with no branch anywhere in this
        # function that would ever re-read the corrected draft -- both
        # were permanently reported as outcome="failed", reason="cap" on
        # every subsequent invocation.
        #
        # Fixed with the SAME discriminator the clean branch already
        # uses, not a second one: draft_matches_review (computed once,
        # above, from the current_sha1/reviewed_sha1 already in scope
        # here) is True only when this stored final verdict was written
        # against the draft that exists right now -- exactly when
        # cap_reached is honest. When it is False the draft moved since
        # the review read it, so a fresh review is dispatched at the SAME
        # "final" label (never a new round -- there is no round past
        # "final"; _next_round_label() treats it as absorbing, and this
        # is a re-check of what changed, not a round spent).
        #
        # AMBIGUITY IS NEVER TERMINAL HERE, and this is where this branch
        # deliberately STOPS copying the not-clean/not-final branch below.
        # An earlier version of this fix reused that branch's tri-state
        # guard (`draft_matches_review or current_sha1 is None or
        # reviewed_sha1 is None`) verbatim and called the two
        # interchangeable. They are not: the guard's FORM is identical but
        # its CONSEQUENCE is inverted. Down there, ambiguity yields
        # needs_fix -- non-terminal, no ledger write, the segment comes
        # back next invocation. Up here it yielded cap_reached -- a
        # TERMINAL content verdict plus a {"status": "non_converged",
        # "reason": "cap"} ledger write that select_segments.py's own
        # HUMAN_ESCALATION_STATUSES then excludes from every later default
        # selection (select_segments.py's classify_segment()). So the same
        # "stay conservative" words bought caution on one branch and a
        # permanent, unrecoverable verdict about a draft NOBODY READ on
        # the other -- the exact #432 failure shape, re-entered through
        # the guard added to fix it. The two ambiguities are also
        # different conditions and are answered separately:
        #
        # current_sha1 is None -- INFRASTRUCTURE, not content. Note how
        # narrow this is: draft_ok gated this whole path above, so
        # draft_ready.py AND validate_draft.py both just passed on this
        # segment; for current_draft_sha1() to fail moments later the
        # draft has to have been deleted/mangled in that window, or
        # draft_sha1.py itself is unusable. Neither is a fact about the
        # translation, and neither is improved by writing a cap. Raised
        # (never re-run -- `current_sha1_error` is the original DriverError
        # captured above, and its text is interpolated verbatim, so the
        # operator gets the real cause without a second, possibly
        # differently-failing probe; the exception OBJECT is new, see the
        # comment at the capture site) so it lands in process_segment()'s
        # own `except Exception` and becomes
        # outcome="failed", reason="unexpected-error:DriverError" -- which,
        # per that function's docstring, writes NO terminal ledger entry
        # and dispatches NO codex job, leaving the segment "recoverable"
        # for select_segments.py exactly like every other infra failure in
        # this driver. Cheaper than the alternative too: routing it to
        # "review" would spend a real codex job judging a draft this
        # process cannot even hash, and routing it to "needs_fix" is not
        # merely wrong-in-principle but BROKEN at this label --
        # process_segment()'s needs_fix branch calls
        # `int(round_label)`, and int("final") raises ValueError.
        #
        # The GUARANTEE this makes, stated at its real width: ambiguity
        # never MINTS a terminal verdict. It does not repair one already
        # on disk. "No ledger write" equals "reachable by default
        # selection" only when the existing fragment is in_progress or
        # absent -- classify_segment() then reports recoverable/
        # not_started, both inside select_segments.py's own
        # DEFAULT_ELIGIBLE_CATEGORIES ({"not_started", "recoverable",
        # "stale"}). A segment carrying a non_converged/cap fragment from
        # a PRIOR run keeps it, stays human_escalation, and remains
        # reachable only through the --only-segs override that brought it
        # here. That asymmetry is chosen, not overlooked: reopening on
        # this path would durably un-escalate a segment on the strength of
        # an infrastructure failure, over a draft this process cannot even
        # hash -- overturning a human-visible escalation on no evidence.
        # The reopen below repairs a cap only where there IS evidence (a
        # draft that demonstrably moved). Pinned by test_an_uncomputable_
        # draft_sha1_leaves_a_pre_existing_cap_exactly_as_it_found_it.
        #
        # reviewed_sha1 is None -- the STORED REVIEW has no draft_sha1
        # (hand-written, or predating the field). Re-reviewed, not capped:
        # a cap here would be a terminal verdict resting on a review that
        # cannot be tied to any draft at all, and unlike the infra case
        # there is a real, bounded way forward, because the re-review
        # cannot silently adopt the wrong draft -- review.schema.json
        # REQUIRES draft_sha1 (verified: its own `required` list), and
        # review_ready.py independently refuses to promote any candidate
        # whose draft_sha1 differs from the draft it just hashed or whose
        # dispatch_token differs from the expected one, so whatever comes
        # back is bound to the current draft and this run or it does not
        # land. Raising instead would be the #432 defect again with a new
        # reason string: nothing on disk changes between invocations, so
        # it would raise forever.
        #
        # Both non-terminal answers fall out of one condition, since
        # draft_matches_review is False whenever reviewed_sha1 is None.
        if current_sha1 is None:
            fatal(
                f"segment {seg!r}: a stored non-clean 'final' review cannot be "
                f"judged against the current draft because the draft's own "
                f"content sha1 could not be computed ({current_sha1_error}) "
                f"-- refusing to record a terminal cap over a draft this "
                f"invocation never read"
            )
        # #461, the ORDERING half. A rejection filed against a FINAL-round
        # review used to be written and then never consumed: every path out
        # of this branch -- the fatal above, cap_reached, the reopen
        # re-review -- returns before the _rejection_matches() call site
        # further down is ever evaluated. reject_review.py accepts
        # `--round-label final` and reports SUCCESS, so an operator who set
        # aside an unfounded final verdict was told it worked while nothing
        # changed. The final round is also where an unfounded verdict does
        # the MOST damage rather than the least: cap_reached writes the
        # terminal {"status": "non_converged", "reason": "cap"} fragment,
        # which select_segments.py's own HUMAN_ESCALATION_STATUSES then
        # excludes from every default selection, so nothing re-reads the
        # draft again on its own initiative.
        #
        # WHAT STOPS THE LOOP -- the question this branch has and the
        # numbered rounds do not. "final" is ABSORBING: _next_round_label()
        # maps it to itself, so consuming a rejection here re-dispatches
        # "final" rather than advancing to a fresh label. review_dispatch_
        # token() is a pure function of run_id+seg+round_label, so the
        # replacement review carries a byte-IDENTICAL dispatch_token, and a
        # reviewer that independently reaches the same verdict over the same
        # (unchanged) draft produces a byte-identical verdict digest too.
        # Token and digest -- the only two facts the pre-#461 matcher
        # compared -- therefore cannot tell "the review the operator
        # rejected" from "its replacement", and this branch would re-spend a
        # real codex job every invocation, forever, on one operator decision.
        #
        # #527 CHANGED WHAT A `final` REJECTION BUYS, because what it used
        # to buy did not work. It bought EXACTLY ONE re-review: rule 8 (the
        # record must be strictly newer than review.json) spends the record
        # the moment codex_job.py promotes the replacement, so a verdict
        # that came back non-clean again capped the unit with the override
        # already spent. That is a second opinion, and a second opinion is
        # exactly the remedy this case cannot use: the two reviewers read
        # the SAME unchanged input, so when the input itself is what misleads
        # them -- a source block stored in VISUAL order, where a quoted
        # phrase's closing mark precedes its opening one -- they are one
        # observation, not two, and the same false finding is re-derived
        # every round. Measured twice on one block of a live he/yi->en book
        # (seg06 PARA:seg06:0003, rounds 1 and 2, two different reviewers,
        # refuted on the same evidence both times), on a source with 1141
        # glued-punctuation tokens across 581 blocks. The unit could not
        # converge by any route: nothing to apply, and a cap at the end of
        # it.
        #
        # So a matching record now TERMINATES the unit as converged on the
        # operator's own attested reason -- but only over the draft the
        # rejected verdict was written against, and only when the reviewer
        # itself asserted it had read the whole segment:
        #
        #   draft_matches_review -- the attestation is about THESE bytes. If
        #   the draft moved since the verdict, the operator's judgment no
        #   longer describes what is on disk, and the fall-through below
        #   (today's re-review) is the right answer, not a convergence.
        #
        #   coverage_ok is True -- reject_review.py deliberately gates on
        #   `clean` alone (see its own condition 1: coverage being incomplete
        #   is "a different fact from findings being unfounded", and the
        #   operator is only ever asked to judge whether a FINDING is real).
        #   So an operator's refutation says nothing about coverage, and this
        #   branch is reachable with coverage_ok False. Converging there would
        #   mark a segment done over a review that affirmatively reports
        #   dropped blocks/footnotes/verses (review.schema.json's own
        #   description of the field). A fresh review is the right answer to
        #   an incomplete-coverage verdict, and that is what falls through.
        #
        # `clean is False` needs no test here: it is rule 7 inside
        # _rejection_record(), so a record cannot match a clean verdict at
        # all. What the operator's attestation REPLACES is exactly one proof
        # the ordinary already_converged branch above makes -- `clean is
        # True` -- and nothing else: both routes have already passed draft
        # readiness, deterministic validation, the current-run token match,
        # the fabricated-loc gate and the draft_sha1 binding by the time they
        # reach a convergence.
        #
        # STANDING LICENCE, honestly: rule 8 no longer spends the record on
        # this path, because nothing rewrites review.json any more. While the
        # verdict and the draft both sit unchanged, a re-driven segment
        # re-derives this same action and re-writes the SAME convergence --
        # a semantic fixed point (same status, rounds and
        # reviewed_draft_sha1; the timestamp and the recomputed cache_key
        # make the bytes differ), not the repeated codex spend the old
        # re-review path would have cost. It lapses the moment the draft
        # moves at all, which is the condition the operator's attestation is
        # about.
        #
        # Placed AFTER the `current_sha1 is None` fatal, deliberately. That
        # fatal is about INFRASTRUCTURE -- the draft cannot be hashed at all
        # -- while a rejection is an operator's judgment about CONTENT.
        # Letting a content-level authorization jump an infrastructure
        # failure would spend a real codex job judging a draft this process
        # cannot even read, the exact cost the fatal's own comment above
        # rejects. Ordering the rejection before the CAP is the whole fix;
        # ordering it before the fatal would be a different, worse one.
        #
        # On the FALL-THROUGH (the draft moved, or coverage_ok is not True)
        # reopen_capped travels with the re-review for the same reason the
        # branch below sets it, only more surely: a segment arriving here has
        # very likely ALREADY been capped by a prior invocation -- that
        # terminal verdict is what the operator was looking at when they
        # filed the rejection -- so process_segment() must replace the
        # terminal fragment with a recoverable one BEFORE spending the
        # dispatch, or a dispatch failure leaves the cap standing as the only
        # durable fact. That write durably UN-ESCALATES a human_escalation
        # segment on the strength of this record alone, which is precisely
        # why _rejection_record() validates the record's whole shape, its
        # audit trail and its provenance rather than two guessable fields.
        # The convergence above needs no such pre-write: it is ONE fragment
        # write that supersedes the cap outright, and if it does not land the
        # cap simply stands and the identical invocation re-derives it.
        #
        # reviewed_sha1/reviewed_token/reviewed_digest travel with the
        # convergence exactly as they do with cap_reached below, and for the
        # identical reason: process_segment() commits in a LATER step, and
        # _terminal_write_still_binds_what_was_reviewed() must be able to ask
        # whether the verdict this decision was made from is still the one on
        # disk -- against what THIS function parsed, never a re-read that
        # would accept a substitute on its own terms.
        rejection = _rejection_record(seg, segments_dir, review_obj)
        if rejection is not None:
            if draft_matches_review and review_obj.get("coverage_ok") is True:
                return {
                    "action": "converged_by_rejection",
                    "round_label": "final",
                    "rejection": rejection,
                    "reviewed_sha1": reviewed_sha1,
                    "reviewed_token": review_obj.get("dispatch_token"),
                    "reviewed_digest": _review_verdict_digest(review_obj),
                }
            return {
                "action": "review",
                "round_label": "final",
                "reopen_capped": True,
                "cause": "rejected_findings",
            }
        if draft_matches_review:
            # reviewed_token and reviewed_digest travel with the verdict so
            # process_segment() can bind the cap WRITE to the review this
            # decision was made from, not merely to a review that happens to
            # be on disk when the write runs -- see
            # _terminal_write_still_binds_what_was_reviewed(). The digest is
            # taken from review_obj, the object THIS function parsed, which
            # is the whole point: a digest re-read from disk at write time
            # would describe the replacement, not the reviewed verdict.
            return {
                "action": "cap_reached",
                "findings": review_obj.get("findings") or [],
                "reviewed_sha1": reviewed_sha1,
                "reviewed_token": review_obj.get("dispatch_token"),
                "reviewed_digest": _review_verdict_digest(review_obj),
            }
        # reopen_capped: a previous invocation may already have written the
        # terminal {"status": "non_converged", "reason": "cap"} fragment
        # this branch exists to undo. process_segment() must replace it
        # with a recoverable record BEFORE spending the re-review -- see
        # its own review branch for why a dispatch failure would otherwise
        # leave the old cap standing.
        return {"action": "review", "round_label": "final", "reopen_capped": True}

    # #461: a durable rejection (reject_review.py, the ONLY writer -- see
    # its own module docstring) lets an operator set aside a well-formed
    # but UNFOUNDED review verdict -- one whose findings are structurally
    # authentic (past the fabricated_loc gate above) yet simply wrong about
    # the source. Verified on a real segment: the sole finding claimed the
    # Hebrew source read a phrase that occurs zero times in the block.
    # Nothing was applied to the draft, correctly -- there was nothing real
    # to apply -- but that leaves draft_matches_review True below, and
    # WITHOUT this check this branch returns needs_fix forever: a fix
    # prompt rendered, every invocation, for a segment with nothing to fix.
    #
    # Checked BEFORE draft_matches_review, unconditionally: a rejection
    # that still names THIS review (same dispatch_token AND same verdict
    # digest, both matched against review_obj -- the object THIS
    # invocation already parsed, never a second independent re-read of
    # review.json) converts the outcome into a fresh review at the NEXT
    # round label, the exact action the round-advance path below already
    # returns when draft_matches_review happens to be False -- so placing
    # this ahead of that test changes behavior ONLY in the needs_fix case
    # (the one #461 is about) and is a no-op, modulo the `cause` tag, in
    # the other. NEVER routes to "translate" -- the only two outcomes
    # reachable from here are both "review", satisfying the constraint
    # that a rejection must never make a translate reachable.
    #
    # A STALE rejection -- token or digest no longer matching, e.g. left
    # over from a PRIOR round this segment has since moved past -- is
    # ignored entirely, as if it were never written: _rejection_matches()
    # itself decides this; see its own docstring for why "cannot
    # establish" and "does not exist" are deliberately indistinguishable
    # to this caller.
    if _rejection_matches(seg, segments_dir, review_obj):
        return {
            "action": "review",
            "round_label": _next_round_label(matched_round_label, max_fix_rounds),
            "cause": "rejected_findings",
        }

    # Not clean, not the mandatory final round -- a fix is needed before the
    # NEXT review round can be dispatched. Any ambiguity (can't compute
    # either sha1) stays conservative -- report needs_fix rather than
    # silently advancing.
    if draft_matches_review or current_sha1 is None or reviewed_sha1 is None:
        return {"action": "needs_fix", "round_label": matched_round_label, "findings": review_obj.get("findings") or []}

    # RAW #7 (#441): the draft moved since this review, but that alone does
    # not prove a fix was applied -- a same-run RETRANSLATE moves it too,
    # and _translate_in_progress_since() is the only evidence that tells
    # the two apart (see its own docstring). This makes the non-clean
    # branch here agree with the CLEAN-but-stale branch above, which
    # already re-reviews at `matched_round_label` rather than spending a
    # round when the draft merely moved out from under a stale verdict.
    if _translate_in_progress_since(dirs, seg, review_path):
        return {"action": "review", "round_label": matched_round_label}

    return {"action": "review", "round_label": _next_round_label(matched_round_label, max_fix_rounds)}


def _next_round_label(round_label: str, max_fix_rounds: int) -> str:
    """The round label immediately after `round_label` -- "final" stays
    "final" (there is no round beyond the mandatory final one). Neither of
    derive_next_action()'s stale-verdict branches ROUTES THROUGH this
    function to reach that no-advance outcome: the clean-but-stale branch
    re-dispatches at `matched_round_label` (whatever round the stale
    verdict was written for, "final" included), and the #432
    non-clean-but-stale-final branch returns the literal "final" it has
    just tested for. Same reason in both cases -- the stored verdict no
    longer describes the current draft, so this is a re-check of the same
    round, not an advance -- but they are two different mechanisms, not
    one, and only the second is a hardcoded label."""
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


def run_one_codex_job(ctx: "DispatchContext", *, kind: str, seg: str, round_label: "str | None" = None) -> dict:
    """Dispatches ONE codex_job.py invocation for `seg` (translate, or one
    review round) and returns codex_job.py's OWN reported outcome (see
    _codex_job_outcome()) plus the {kind, seg, round_label, disp} this
    dispatch used. Writes the task-file, builds the argv via
    build_codex_job_argv(), and blocks via dispatch_codex_job() -- every
    property (start_new_session, no polling) that primitive already closes.

    round_label is genuinely optional for kind="translate" (there is no
    round for a translate dispatch) but REQUIRED for kind="review" --
    render_review_prompt()/review_dispatch_token() both declare it `str`,
    never `Optional`. Checked explicitly here rather than left implicit:
    an unchecked None reaching review_dispatch_token()'s f-string would not
    crash -- it would silently build "<run_id>:<seg>:rNone", a
    syntactically fine but semantically orphaned token no real round label
    can ever match, so the resulting review is dispatched, promoted, and
    then invisible to every future derive_next_action() call -- the exact
    "a value derived by a lookup that can fail, fed into something that
    assumes it cannot" class as the `rounds: null` defect fixed earlier."""
    dirs = ctx.dirs
    durable_root = dirs["durable_root"]
    if kind == "translate":
        prompt_text = render_translate_prompt(ctx, seg)
        expect_token = translate_dispatch_token(ctx.run_id, seg)
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
        run_id=ctx.run_id, node_bin=ctx.node_bin,
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


def _review_verdict_digest(review_obj: dict) -> str:
    """sha256 over the WHOLE review object, in the sorted-key canonical form
    review_artifact_check.py's own canonical_text() already uses for the
    same artifact -- so this is the repo's existing notion of "the same
    review", hashed, not a second one invented here.

    Digest over the whole object rather than an enumerated list of
    decision-bearing fields (`clean`, `coverage_ok`, `findings`): the
    enumeration is what silently stops covering the verdict the day a field
    is added. review.schema.json is `additionalProperties: false` over
    exactly five properties TODAY, and review_ready.py refuses to promote
    anything that does not validate against it, so an enumeration would be
    complete right now -- and would go quietly incomplete at the next schema
    change, with nothing failing to say so.

    Hashed from an already-PARSED object, never from the file's raw bytes,
    so a re-serialization that reorders keys or changes whitespace without
    changing a single value is not mistaken for a different verdict. What is
    NOT excused: findings[] element order, which is array order and part of
    the value. That direction is the safe one anyway -- a mismatch refuses a
    write and records nothing, so the segment simply comes back next
    invocation and is re-derived from whatever is actually on disk."""
    return hashlib.sha256(
        json.dumps(review_obj, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


# #461: the rejection record's EXACT key set, pinned by the contract both
# sides of this artifact are written against -- producer reject_review.py
# (the sole writer), consumer _rejection_record() below (the sole reader;
# _rejection_matches() is its predicate wrapper, not a second reader).
# Named rather than inlined because "exactly these, no more and no fewer"
# IS the rule, in both directions: a MISSING key means a hand-written stub
# is trying to authorize with an audit trail it never wrote, and an EXTRA
# key means the record came from something whose rules this reader does not
# know. Neither is a tolerable subset/superset of a record that authorizes
# overriding a reviewer.
REJECTION_RECORD_KEYS = frozenset({
    "seg",
    "dispatch_token",
    "verdict_digest",
    "round_label",
    "reason",
    "rejected_at",
    "operator_invocation",
})


def _rejection_record(seg: str, segments_dir: Path, review_obj: dict) -> "dict | None":
    """#461: the record at segments/{seg}.review_rejected.json
    (reject_review.py, the sole writer) when it is a well-formed,
    still-unspent AUTHORIZATION to set aside `review_obj`'s verdict, or None
    -- `review_obj` being the SAME review derive_next_action() already parsed
    for THIS invocation, never a second independent re-read of review.json.
    The RECORD is returned rather than a bool because #527 gave it a second
    consumer: the operator's own `reason` and `rejected_at` are written into
    the convergence ledger note, so the ledger says WHY a terminal verdict
    went away. _rejection_matches() below is the predicate for the two
    callers that need only the yes/no.

    NO SECOND READ OF review.json HERE, and since #527 that is a narrower
    claim than it was. This function judges the record against the verdict
    derive_next_action() parsed, nothing more. The read/commit gap that
    the cap fork already closes is now closed on the convergence fork the same
    way -- at the WRITE site, by
    _terminal_write_still_binds_what_was_reviewed(), which is the cap fork's
    own helper under a name that no longer claims only one of the two
    terminal writes it now guards.
    Re-reading review.json here would move that gap, not close it.

    THIS FILE IS THE ONLY THING IN THE PIPELINE THAT CAN MAKE AN UNCHANGED
    DRAFT ADVANCE PAST A needs_fix LOOP; since the #461 ordering fix in
    derive_next_action()'s "final" branch, the only thing that can reopen a
    TERMINAL cap on content grounds; and since #527, the only thing that can
    TERMINATE a unit as CONVERGED on content grounds -- an operator's
    attested refutation of the final-round findings, over a draft unchanged
    since the verdict it names. It is an authorization, not a note, so every
    rule below is a rule about authorizations, not about JSON hygiene:

    1. REGULAR FILE, NOT A SYMLINK, judged on the OPENED DESCRIPTOR.
    2. The key set equals REJECTION_RECORD_KEYS exactly.
    3. Every value is a `str`, non-empty after strip.
    4. `record["seg"]` equals the segment being decided.
    5. `record["dispatch_token"]` equals the live review's.
    6. `record["verdict_digest"]` equals _review_verdict_digest(review_obj),
       recomputed now.
    7. `review_obj.get("clean") is False`.
    8. The record is strictly NEWER than the review.json on disk.

    Rules 2-4 are what the pre-#461 matcher was missing, and the gap was
    not theoretical: it compared `dispatch_token` and `verdict_digest` and
    nothing else, so a two-field hand-written file -- or a SYMLINK pointing
    at one -- was a complete, sufficient authorization to override a
    genuine reviewer over a draft nobody re-read. Both values are also
    discoverable by anything that can read review.json, which is everything
    that can write next to it. Rule 3 exists because an authorization whose
    audit trail (`reason`, `rejected_at`, `operator_invocation`) is an
    empty string is not one: the fields would be present, the accountability
    absent, and rule 2 alone cannot tell those apart. Rule 4 stops a record
    filed under one segment from authorizing its neighbour.

    Rule 8, the FRESHNESS/REPLAY rule, is the one that is not about forgery.
    Rules 5 and 6 pin WHICH verdict was rejected, and on the numbered rounds
    that is enough on its own, because consuming the rejection advances the
    round and review_dispatch_token() bakes the round label into the token
    -- the replacement review simply cannot match rule 5 again. On "final"
    it is NOT enough: _next_round_label() treats "final" as absorbing, so
    the replacement is dispatched at the same label, carries a
    byte-IDENTICAL dispatch_token (review_dispatch_token() is a pure
    function of run_id+seg+round_label), and -- if the reviewer independently
    reaches the same verdict over the same draft -- a byte-identical digest
    too. Rules 5+6 would then keep matching forever, re-spending a real
    codex job on one operator decision every invocation. The mtime ordering
    is the fact that CANNOT repeat: the record is newer than the review it
    rejects exactly until codex_job.py promotes a replacement, after which
    it is spent whether or not the bytes came back the same. So a rejection
    is consumable exactly ONCE per review it names, at every round label,
    and "final" needs no special case of its own. Stated as a property
    rather than as a loop-breaker: the record attests a verdict the file on
    disk has not been rewritten since -- which is the same thing rules 5+6
    are trying to say, made true again after a token can repeat.

    #527 BREAKS THE "exactly ONCE" HALF OF THAT AT `final`, and says so
    rather than narrowing the words until they still fit. The `final`
    outcome no longer produces a replacement review, so nothing rewrites
    review.json and nothing spends the record: it stays REPLAYABLE, and
    every later invocation over the same review and draft authorizes another
    convergence write. Those writes are idempotent in content (same status,
    rounds and reviewed_draft_sha1; the timestamp and recomputed cache_key
    make the bytes differ), they spend no codex job, and they cannot touch
    the draft -- so what rule 8 exists to prevent, one operator decision
    buying unbounded spend, is still prevented. Replayability ends the
    moment the draft moves, which is the condition the attestation is
    about.

    Compared with `>` and not `>=` (st_mtime_ns, the same primitive
    _translate_redispatched_since() already uses for "did this driver act
    after that file was written"): a tie refuses, so a coarse-granularity
    filesystem costs a legitimate rejection filed within the same mtime tick
    as the review it rejects, and never grants a spent one. Refusing is
    recoverable -- the operator re-runs reject_review.py -- and this is the
    direction every other unknown here takes.

    RESIDUAL OF RULE 8, STATED RATHER THAN DEFENDED. Rule 8 rests on the
    filesystem clock, and it is the only decision in this file that does.
    What mtime is trusted for here is narrow: the RELATIVE order of two
    files in one directory, written minutes apart, by two processes on one
    host. It is NOT trusted as an identity, an authority, or a total order
    over anything -- and nothing is authorized BY it. Rules 1-7 have already
    established that this record names this verdict before rule 8 is
    consulted; rule 8 can only take an authorization AWAY.

    The realistic ways it can be wrong, and the direction each takes:
      - A copy or restore that PRESERVES mtimes (cp -p, tar, most backup
        tools) preserves their relative ORDER too. A spent rejection stays
        spent, a live one stays live. No change in either direction.
      - A restore that does NOT preserve mtimes stamps both files at restore
        time. Either the record lands older -- REFUSES -- or it lands newer,
        which is the open direction below.
      - A clock adjustment BACKWARDS between the review being promoted and
        the record being written makes the record look older than the very
        verdict it rejects: REFUSES. Visible to the operator as the segment
        simply not moving, and fixed by re-running reject_review.py.
      - Coarse mtime granularity, ties: REFUSES (the `>` above).
    The only OPEN-direction failure needs a backwards clock jump AND a
    re-dispatch landing the replacement review underneath it.

    WORST REACHABLE OUTCOME on that open direction: the rejection is
    consumed a second time, costing ONE spurious re-review plus the
    reopen_capped un-escalation that precedes it. Both are recoverable --
    the re-review re-reads the CURRENT draft, and the segment re-derives
    from whatever is actually on disk -- and neither can reach draft bytes:
    every outcome of a consumed rejection is action "review" or (since #527,
    at `final` only) a convergence write, never "translate", and neither a
    review nor a ledger write touches the draft. The closed
    direction, far likelier, costs the operator one re-run of
    reject_review.py. Neither failure destroys work. That is the whole
    reason a clock is an acceptable instrument HERE, and would not be for
    an ownership or admission decision elsewhere in this pipeline, where a
    wrong answer locks a rightful owner out durably.

    ABSENT, UNREADABLE, MALFORMED, MISMATCHED OR SPENT ALL RETURN None,
    and deliberately indistinguishably so: "no rejection exists" and "a
    rejection exists but cannot be trusted" take the identical safe
    direction, which is to fall through to the ordinary needs_fix/cap/review
    logic exactly as if reject_review.py had never run. There is no
    direction here that fails toward "trust it" -- a returned record that
    should have been None would let a
    STALE rejection (one written against a PRIOR round's review, left behind
    after the segment moved on) silently swallow a genuinely new, unrelated
    finding a later round raised.

    `review_obj.get("clean") is False` is checked HERE TOO, not trusted
    solely via the digest match, even though the digest is over the WHOLE
    review object and therefore already encodes `clean` -- a legitimately
    produced rejection's digest genuinely cannot match a clean:true review
    without also mismatching on `clean` itself. The explicit check is
    defense in depth against a rejection artifact that did not come from
    reject_review.py's own gate at all (hand-edited, restored from a
    backup, produced by a future writer that forgets the gate) -- exactly
    the same "never trust a single signal alone" reasoning
    _terminal_write_still_binds_what_was_reviewed() already applies by checking
    provenance AND digest as two separate facts rather than folding one
    into the other. Rule 6 needs no separate "64 lowercase hex" format
    check for the same reason in reverse: equality with a hexdigest already
    pins the format, and a second spelling of the format would be a second
    source of truth that can drift from the first."""
    if review_obj.get("clean") is not False:
        return None

    path = segments_dir / f"{seg}.review_rejected.json"
    # O_NOFOLLOW plus an FSTAT ON THE OPENED DESCRIPTOR -- never
    # Path.is_file(), and never an lstat()/read_text() pair. Two separate
    # reasons, both real:
    #   - Path.exists()/is_file()/is_dir()/glob() SWALLOW OSError and answer
    #     as if the thing were absent, and from Python 3.14 exists()
    #     swallows EVERY OSError while this plugin's floor is 3.10 -- so
    #     their answer differs by the operator's interpreter. Every refusal
    #     here happens to land in the same direction, but what authorizes an
    #     override must not be decided by a predicate whose MEANING changes
    #     under python.
    #   - lstat()-then-open() is a TOCTOU: what lstat() proved was a regular
    #     file can be a symlink by the time read_text() follows it, and the
    #     bytes that get parsed are then not the bytes that were judged.
    #     Opening with O_NOFOLLOW and fstat()ing THAT descriptor judges the
    #     exact bytes this function goes on to parse.
    # O_NONBLOCK is not decoration: without it os.open() on a FIFO left at
    # this path BLOCKS until a writer appears -- a hang inside the driver's
    # own derivation, strictly worse than the refusal S_ISREG gives it one
    # line later. On a regular file it is a no-op.
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        # FileNotFoundError/NotADirectoryError (definitive: there is no
        # record) and ELOOP/EACCES/EIO (could not look) are NOT split apart
        # here, unlike the general rule for this codebase, because there is
        # nothing to split them FOR: both mean "do not authorize", and the
        # caller has exactly one non-authorizing outcome to fall through to.
        return None
    try:
        with os.fdopen(fd, "rb") as handle:
            st = os.fstat(handle.fileno())
            if not stat.S_ISREG(st.st_mode):
                return None
            record_mtime_ns = st.st_mtime_ns
            raw = handle.read()
    except OSError:
        return None
    try:
        record = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(record, dict):
        return None

    if set(record) != REJECTION_RECORD_KEYS:
        return None
    for key in REJECTION_RECORD_KEYS:
        value = record[key]
        if not isinstance(value, str) or not value.strip():
            return None

    if record["seg"] != seg:
        return None
    if record["dispatch_token"] != review_obj.get("dispatch_token"):
        return None
    if record["verdict_digest"] != _review_verdict_digest(review_obj):
        return None

    # Rule 8. os.stat(), not lstat(): review.json is this pipeline's own
    # artifact, promoted by codex_job.py and already read by the caller a
    # few lines into derive_next_action(), so following it adds no trust
    # assumption that read has not already made. An unreadable review.json
    # refuses -- the same direction as everything else here, and the only
    # honest one, since without its mtime there is no way to tell a live
    # rejection from a spent one.
    try:
        review_mtime_ns = os.stat(segments_dir / f"{seg}.review.json").st_mtime_ns
    except OSError:
        return None
    if record_mtime_ns <= review_mtime_ns:
        return None
    return record


def _rejection_matches(seg: str, segments_dir: Path, review_obj: dict) -> bool:
    """True iff _rejection_record() above finds a still-unspent authorization
    for `review_obj`'s verdict. ONE production caller -- derive_next_action()'s
    numbered-round branch, which needs only the yes/no -- plus this file's test
    suite, whose assertions read as `is True`/`is False` around it. The `final`
    branch does NOT come through here: since #527 it binds the record itself,
    because the operator's `reason` and `rejected_at` are written into the
    convergence ledger note, and that is the only reason the reader was split
    in two at all. Kept as a wrapper rather than inlined at its one caller so
    every rule, refusal direction and residual lives in ONE place above."""
    return _rejection_record(seg, segments_dir, review_obj) is not None


# The operator's reason is free text they typed; the ledger note is a field
# other tools render whole. 300 characters is a budget, not a validation --
# reject_review.py requires only that the reason be non-empty, so nothing
# upstream bounds it, and the WHOLE attestation is on disk in the record this
# note names. Truncation is visible ("...") rather than silent.
REJECTION_NOTE_REASON_BUDGET = 300


def _rejection_convergence_note(seg: str, record: dict) -> str:
    """The ledger `note` for a #527 convergence: what made a terminal verdict
    go away, in the one place an operator later looks to find out.

    The same reasoning as the #432/#461 reopen note a few hundred lines below
    -- "a durable note asserting an edit that never happened would be exactly
    the 'record outlives the fact it attests' shape this whole artifact exists
    to avoid" -- read in the other direction: this convergence rests on a
    human judgement rather than on a clean review, and a fragment that did not
    say so would be indistinguishable, forever, from one whose reviewer
    actually returned clean. The stored review sitting beside it says
    clean:false, so without this note the pair reads as corruption.

    QUOTED, NOT MERELY POINTED AT, and that is load-bearing rather than
    generous: the record this note names can legitimately be gone by the
    time anyone reads the note. reject_review.py publishes with os.replace()
    and only then learns whether the directory sync succeeded; on a failure
    it unlinks the record and reports failure, while a driver that read it
    in between has already converged. Carrying the reason and the timestamp
    IN the ledger means the audit trail survives that, and the path stays a
    convenience rather than the only copy.

    Whitespace is collapsed because a reason can carry newlines and the note
    is a single JSON string field."""
    reason = " ".join((record.get("reason") or "").split())
    if len(reason) > REJECTION_NOTE_REASON_BUDGET:
        reason = reason[: REJECTION_NOTE_REASON_BUDGET - 3].rstrip() + "..."
    return (
        "converged on an operator's rejection of the final-round verdict as "
        f"unfounded (#527, reject_review.py): {reason} "
        f"[rejected_at={record.get('rejected_at')}; full record at "
        f"segments/{seg}.review_rejected.json]"
    )


def _terminal_write_still_binds_what_was_reviewed(
    seg: str, ctx: "DispatchContext", action: dict, *, what: str = "cap"
) -> "str | None":
    """None if the terminal verdict in `action` still describes the review
    and the draft bytes it was derived from, or a human-readable reason
    string if either moved in between. `what` names the write in those
    strings ("cap", or "convergence" for EITHER convergence route -- the
    ordinary already_converged one and #527's operator-attested
    converged_by_rejection, which share it because they share the refusal
    reason and the operator's remedy) --
    the CHECK is identical for both, and that is the point of one helper
    rather than two: what has to hold before a terminal write is a property
    of the write being terminal, not of which verdict it records.

    Why this exists at all: derive_next_action()'s sha comparison is a
    POINT-IN-TIME observation, and process_segment() commits the cap in a
    LATER step. Nothing in this driver owns the draft -- a human applying
    findings by hand (the exact workflow #432 was reported from) can edit
    it inside that window, and the cap would then be recorded against
    bytes no reviewer examined, terminally, with select_segments.py's own
    HUMAN_ESCALATION_STATUSES excluding the segment from every later
    default selection.

    The CONVERGENCE write is already protected against its own version of
    this, one layer down, and this mirrors that protection's SHAPE rather
    than inventing a second discipline: ledger_update.py's
    enrich_converged_fields() re-reads review.json, re-checks its
    dispatch_token, re-hashes the draft on disk, and refuses the write
    ("draft changed since review; cannot record convergence") if the
    review's recorded draft_sha1 no longer matches. The non_converged
    write goes through the same ledger_update.py and gets NONE of that --
    those preconditions live entirely inside its `if fragment["status"]
    == "converged":` arm -- so the check has to be made here, by the
    caller, for the terminal write on the other side of the fork.

    Deliberately compared against what derive_next_action() OBSERVED
    (`reviewed_sha1`/`reviewed_token`, carried on the action), never
    re-derived from whatever is on disk now: re-deriving would re-read the
    same file the race can have replaced, so a swapped review.json would
    simply be re-accepted on its own terms. Fixing this in ledger_update.py
    instead -- making a non_converged write carry its own precondition,
    the way a converged one does -- would close it for every caller, not
    just this driver; that is a change to a file this driver does not own.

    WIDER THAN THE CONVERGENCE WRITE'S PAIR, deliberately. An earlier
    version of this helper bound only (draft_sha1, dispatch_token) -- the
    SAME two facts enrich_converged_fields() binds a convergence write to,
    mirrored rather than widened -- and disclosed the rest as an accepted
    residual: a replacement review.json carrying BOTH the same draft_sha1
    and the same dispatch_token was indistinguishable here however
    different its verdict (a hand-flipped `clean`, different findings).
    That is now CLOSED by also carrying `reviewed_digest`, a sha256 over
    the whole review object derive_next_action() actually parsed (see
    _review_verdict_digest() for why the whole object and not an
    enumeration of `clean`/`coverage_ok`/`findings`). The provenance pair
    is kept alongside it, not replaced: the digest subsumes it, but the
    pair's own mismatch message names WHICH bound fact moved, which a
    digest cannot.

    Why the pair alone was not enough, kept because it is the argument the
    widening rests on. It is not the flock: `runs/.driver.lock` excludes
    another DRIVER, while codex_job.py never acquires it -- it takes only its
    own per-segment `.codex_job.<seg>.lock` -- and the fallback workflow
    template path launches codex_job.py DETACHED, independently of any driver.
    And it is not the sha half, because the draft need not change for the
    verdict to.

    Reachability, kept at its real width so the fix is read neither as
    over- nor under-motivated -- the ordinary detached-job route does NOT
    reach this on its own. A competing codex_job.py serializes on the
    per-segment `.codex_job.<seg>.lock` and then calls `safe_adopt()`
    BEFORE it would launch or promote anything (codex_job.py:1300); a
    canonical review.json that still passes `review_ready.py
    --expect-token` is ADOPTED, leaving the artifact byte-identical rather
    than replacing it. SKILL.md additionally forbids running the default
    Workflow and this driver against one project concurrently.

    What reached it was therefore narrower than "a detached job", and all
    of it lies outside that cooperating path:
      - a WRITER THAT NEVER TAKES THE PER-SEGMENT LOCK -- a human editing
        review.json, an ad-hoc script, a restored backup;
      - an ABA in which the canonical stops passing `review_ready.py`
        between this driver's read and the job's `safe_adopt()` (removed,
        truncated, momentarily unreadable), so the job relaunches and
        promotes a fresh verdict at the SAME draft_sha1 and dispatch_token;
      - the same outcome WITHOUT any change to the canonical: `safe_adopt()`
        reads its gate through `_gate()`, which returns None when the gate
        could not RUN at all -- a timeout, or a spawn failure -- and
        `_ok(None)` is false, so a perfectly valid canonical is treated as
        unadoptable. The job then launches, and if the transient clears it
        promotes a different verdict at the same bound pair. Worth
        separating from the ABA case: nothing about the artifact has to
        change for this one, only the checker's luck. NOT reachable via an
        exhausted poll budget, though that also makes `_gate()` return None:
        `launch()` immediately re-reads the same exhausted `poll_timeout()`
        and returns None too, so that path cannot promote anything;
      - the two-machines-on-sync-replicated-storage case
        acquire_driver_lock()'s own docstring discloses, where the flock is
        not a shared kernel object in the first place.
    Every one of those replaces the ARTIFACT, so every one of them changes
    the digest -- including the human case an earlier revision put on the
    wrong side of the line: applying findings to the DRAFT changes
    draft_sha1 and was always caught by the sha half, but editing
    review.json ALONE -- flipping a verdict without touching the draft --
    changed neither bound fact and used to pass.

    RESIDUAL, and it is a DIFFERENT one, not this race narrowed: the whole
    check is still check-then-write, so the DRAFT can be replaced between
    the re-hash below and write_ledger() landing the cap. Nothing here
    closes that window -- only moving the precondition into ledger_update.py,
    so the non_converged write carries its own the way a converged one
    does, would, and that is a file this driver does not own. Tracked in
    CHANGELOG.md's Known limitations.
    """
    reviewed_sha1 = action.get("reviewed_sha1")
    reviewed_token = action.get("reviewed_token")
    reviewed_digest = action.get("reviewed_digest")
    # fallback_findings deliberately omitted -- an unreadable/absent review
    # yields {"findings": None}, whose missing draft_sha1 fails the
    # comparison below, which is the correct answer for "the artifact this
    # verdict came from is no longer there".
    review_now = _read_review_obj(ctx, seg)
    if review_now.get("draft_sha1") != reviewed_sha1 or review_now.get("dispatch_token") != reviewed_token:
        return (
            f"review artifact for segment {seg!r} changed between the {what} "
            f"decision and the {what} write (decided from draft_sha1="
            f"{reviewed_sha1!r}/dispatch_token={reviewed_token!r}, now "
            f"draft_sha1={review_now.get('draft_sha1')!r}/dispatch_token="
            f"{review_now.get('dispatch_token')!r})"
        )
    # The verdict itself, not merely its provenance. Compared against the
    # digest of what derive_next_action() PARSED -- carried on the action
    # for the same reason reviewed_sha1/reviewed_token are, since
    # re-deriving it here would re-read the very file the race replaced and
    # accept the substitute on its own terms. A cap_reached action with no
    # reviewed_digest at all fails this too, which is the correct direction:
    # refusing writes nothing.
    digest_now = _review_verdict_digest(review_now)
    if digest_now != reviewed_digest:
        return (
            f"review verdict for segment {seg!r} was replaced between the {what} "
            f"decision and the {what} write by a DIFFERENT verdict carrying the "
            f"same provenance (draft_sha1={reviewed_sha1!r}/dispatch_token="
            f"{reviewed_token!r}; review content sha256 {reviewed_digest!r} "
            f"-> {digest_now!r})"
        )
    try:
        current_sha1 = current_draft_sha1(
            seg, ctx.dirs["durable_root"] / "segments", ctx.dirs["scripts_dir"]
        )
    except DriverError as exc:
        return f"could not re-hash the draft for segment {seg!r} before the {what} write: {exc}"
    if current_sha1 != reviewed_sha1:
        return (
            f"draft changed since review; cannot record the {what} for segment "
            f"{seg!r} (review={reviewed_sha1!r}, current={current_sha1!r})"
        )
    return None


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

      outcome="converged"                 -- ledger recorded, done. #527 adds
                                              a second way to reach it, tagged
                                              cause="rejected_findings": the
                                              final round's verdict was
                                              non-clean and an operator's
                                              durable rejection set it aside
                                              over an unmoved draft. Same
                                              outcome value on purpose --
                                              run()'s totality check
                                              partitions on this field, so a
                                              separate bucket would drop those
                                              segments out of every summary --
                                              and the ledger fragment's own
                                              `note` carries the operator's
                                              reason, since the review beside
                                              it still says clean:false.
      outcome="failed", reason=
        "converge-write-review-moved"     -- a convergence above was NOT
                                              recorded: the draft moved, or
                                              the review artifact changed --
                                              its provenance OR its verdict --
                                              between
                                              derive_next_action()'s decision
                                              and the write. Shared by BOTH
                                              convergence routes, the ordinary
                                              already_converged one and #527's
                                              operator-attested
                                              converged_by_rejection, because
                                              the refused write and the
                                              operator's remedy are the same
                                              on either; the `detail` names
                                              which bound fact moved. NO
                                              ledger write, so whatever
                                              fragment is on disk simply
                                              stands, and the identical
                                              invocation re-derives from the
                                              record and review that are
                                              actually there. WHICH fragment
                                              that is differs by route: on the
                                              rejection route it is very
                                              likely the cap the operator was
                                              looking at, while on the
                                              ordinary route it is absent or
                                              the in_progress one a prior
                                              invocation left -- both of which
                                              select_segments.py admits by
                                              default, so that route clears
                                              itself. Neither is guaranteed:
                                              a segment force-included by
                                              --only-segs can carry a
                                              `blocked` or `non_converged`
                                              fragment, which survives the
                                              refusal and needs the same
                                              explicit override again.
      outcome="failed", reason="cap"      -- mandatory final review still
                                              not clean AND still judging
                                              the draft that is on disk
                                              right now; ledger recorded
                                              directly (fully mechanical,
                                              no fix dispatched on the
                                              final round -- matches
                                              runRound's own isFinal branch).
      outcome="failed", reason=
        "cap-write-draft-moved"           -- the cap above was NOT recorded:
                                              the draft moved, or the review
                                              artifact changed -- its
                                              provenance OR its verdict --
                                              between
                                              derive_next_action()'s
                                              decision and the write (see
                                              _cap_still_binds_what_was_
                                              reviewed()). NO ledger write,
                                              so whatever fragment is on
                                              disk survives: recoverable
                                              where that is not_started or
                                              the in_progress a reopen just
                                              wrote, human_escalation where
                                              an un-reopened cap or a
                                              `blocked` is what was there.
                                              Either way the next
                                              invocation re-derives from
                                              the draft and review that are
                                              actually there -- a cap must
                                              never describe bytes no
                                              reviewer read, nor a verdict
                                              no reviewer reached.
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
        "promotion-hash-failed"           -- #620: the translate itself
                                              SUCCEEDED and promoted a draft,
                                              but hashing that draft to stamp
                                              the promotion evidence raised.
                                              No second ledger write happens,
                                              so no evidence is recorded
                                              rather than evidence naming a
                                              hash this driver could not
                                              compute; the promoted draft and
                                              the recoverable in_progress
                                              fragment both stay on disk.
                                              `detail`, not `error_detail` --
                                              this is the driver's own
                                              failure, not a codex_job.py
                                              relay.
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
                                              here. As of #432 this row is
                                              also reached DELIBERATELY,
                                              not only by accident:
                                              derive_next_action()'s
                                              non-clean-final branch raises
                                              a DriverError carrying the
                                              captured cause for a draft
                                              whose sha1 could not be
                                              computed, precisely
                                              BECAUSE this row is
                                              recoverable and spends no
                                              codex job -- see that
                                              branch's own comment for why
                                              capping on that ambiguity was
                                              the wrong answer.
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
        "claimed-segment-translate-
        refused"                          -- #438 D8: derive_next_action()
                                              returned {"action":
                                              "translate"} for a segment
                                              this run's own on-disk claim
                                              record still names -- the
                                              claimed draft went invalid or
                                              missing between admission and
                                              dispatch (see
                                              claim_refusal_for_translate()'s
                                              own docstring). Checked and
                                              returned BEFORE this
                                              iteration's write_ledger()
                                              call, so NEITHER the draft
                                              bytes NOR the ledger fragment
                                              are touched -- recoverable
                                              next invocation once the
                                              underlying draft problem is
                                              fixed, exactly like
                                              "invalid-post-fix-draft"
                                              above.
      outcome="failed", reason=
        "invocation-claim-
        translate-refused"                -- #450: derive_next_action()
                                              returned {"action":
                                              "translate"} for a segment
                                              THIS INVOCATION admitted a
                                              claim for (present in
                                              ctx.claims) -- see
                                              claim_capability_refusal_
                                              for_translate()'s own
                                              docstring for why this check
                                              is unconditional rather than
                                              a reconciliation with the
                                              on-disk claim record the row
                                              above reads. Checked and
                                              returned BEFORE the row
                                              above AND before this
                                              iteration's write_ledger()
                                              call, so neither the draft
                                              bytes nor the ledger
                                              fragment are touched --
                                              recoverable next invocation,
                                              same story as every other
                                              refusal-before-write row
                                              here.
      outcome="failed", reason=
        "loop-exhausted-without-
        terminal-state"                   -- the defensive iteration cap
                                              bound below. NOT purely
                                              defensive: reachable (without
                                              the retry bound above) if a
                                              draft keeps changing out from
                                              under a review every single
                                              iteration -- see
                                              derive_next_action()'s own
                                              "clean but stale" branch AND
                                              (#432) its non-clean "final
                                              but stale" branch, both of
                                              which re-review at the SAME
                                              round label with no bound of
                                              their own -- an operator who
                                              keeps hand-editing the draft
                                              between every mandatory final
                                              review drives this exact
                                              path, one real edit per
                                              cycle, until this loop's own
                                              iteration cap. Kept generic on
                                              purpose: unlike the
                                              fabricated-loc case, this path
                                              has no single template-known
                                              reason to borrow, because it
                                              is not one specific condition
                                              -- it is "nothing else
                                              terminated in time".

    The iteration cap (this segment's own per-segment job count -- one
    translate plus every review round it could ever legitimately need, or
    for a CLAIMED segment the reviews alone, since #514 -- PLUS ONE, see
    the codex round-4 MINOR fix below) bounds the LOOP overall; `fabricated_loc_retries` is a SEPARATE, narrower counter
    (never reusing the loop's own iteration count) so an expected
    condition (a reviewer emitting a fabricated finding, which the
    template's own comment above AUTHENTIC_LOC_RE says a HEALTHY reviewer
    can do) is bounded and reported on its OWN terms, one retry, rather
    than silently spending the whole per-segment budget and then being
    reported as if the defensive backstop itself had fired.

    codex round-4 MINOR: the `+1` above is load-bearing, not padding.
    Recognizing "the one permitted retry ALSO came back fabricated" costs
    a full extra LOOP ITERATION beyond the raw dispatch count -- the
    retry's own review must be DISPATCHED (one iteration) before its
    result can be RE-READ and classified (a SEPARATE, later iteration,
    even though that one dispatches nothing new). At max_fix_rounds=1,
    codex_jobs_per_segment() = 3 (translate + review r1 + the one retry),
    which is exactly enough budget for the three DISPATCHES but leaves no
    iteration left to make the classification -- the loop hits its raw
    cap and falls through to the generic "loop-exhausted-without-
    terminal-state" reason on the SAME iteration that should have
    produced "review-fabricated-loc" instead. The segment still correctly
    terminates either way (no data loss, no wrong dispatch) -- only the
    reported REASON was wrong, silently relabeling an identified,
    expected condition as the generic defensive backstop. The existing
    test for this path passed only because its fixture uses
    max_fix_rounds=2 (budget 4), which happens to leave the needed spare
    iteration; it never exercised the boundary.
    """
    # #514: sized off the per-segment charge for THIS segment's own
    # population, so the two numbers keep the relationship the paragraph
    # above describes. A claimed segment is charged one job less because
    # claim_capability_refusal_for_translate() makes its translate
    # undispatchable -- and it therefore needs one ITERATION less too: the
    # translate iteration the unclaimed budget reserves can never be spent
    # here. Leaving this claim-unaware would have widened the gap between
    # what check_volume_cap() charges and what this loop permits from one
    # job per segment to two, which is a real overrun of the operator's
    # configured cap rather than the documented one-job floor. The `+ 1`
    # spare classification iteration survives intact for both: at the
    # schema's minimum max_fix_rounds of 1 a claimed segment gets 3 --
    # review r1, the one permitted fabricated-loc retry, and the iteration
    # that re-reads and classifies it -- exactly the headroom the unclaimed
    # budget of 4 gives the same sequence with a translate in front of it.
    per_segment_jobs = (
        codex_jobs_per_claimed_segment(ctx.translate_cfg["max_fix_rounds"])
        if seg in ctx.claims
        else codex_jobs_per_segment(ctx.translate_cfg["max_fix_rounds"])
    )
    max_iterations = per_segment_jobs + 1
    fabricated_loc_retries = 0
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
            action = derive_next_action(seg, ctx)

            if action["action"] == "already_converged":
                # A review already landed clean+coverage_ok but the convergence
                # ledger write may not have (a prior driver could have died
                # between the two) -- record it now, mechanically. `rounds` is
                # computed from the round_label derive_next_action() just
                # reported (see _ledger_rounds_value()'s own docstring), never
                # re-parsed from the review's own dispatch_token string.
                # #622. The ordinary convergence is the THIRD terminal write in
                # this function and was the only one that bound nothing: it goes
                # through the same pre-write check the cap and the #527
                # rejection convergence do, under the helper's own general name.
                #
                # DRIVER-SIDE HALF ONLY, deliberately, and this comment is where
                # that is recorded rather than in a release note. The half that
                # MOTIVATED it is the VERDICT: ledger_update.py's
                # enrich_converged_fields() already re-reads review.json and
                # refuses on a draft_sha1 mismatch or a dispatch_token that does
                # not match the run/segment prefix, but it never reads whether
                # the review declared the segment clean at all -- so a non-clean
                # substitute at the same token over the same unread draft was
                # accepted there. The provenance half is NOT merely mirrored
                # either, and saying it was would be wrong: review_token_matches()
                # is a PREFIX match that admits any ':r<roundLabel>' suffix
                # (ledger_update.py:821), while the comparison here is against
                # the EXACT token this decision was made from, round label
                # included. A precondition
                # THERE would close it for every caller, including the shipped
                # Workflow template, and it is not bought: converged_by_rejection
                # records convergence over a review whose `clean` is False by
                # design, so a blanket clean check in ledger_update.py refuses
                # that route outright, and discriminating the two needs either a
                # caller-supplied flag (self-authorizing -- the calling agent is
                # exactly who that file distrusts) or a second copy of
                # _rejection_record()'s whole contract in a file that cannot see
                # this one. The residual for a caller that is not this driver
                # therefore stands, unclosed and stated.
                #
                # What a refusal leaves behind, and the one case that does not
                # clear itself, are stated in this function's own return
                # contract above under converge-write-review-moved rather than
                # a second time here.
                bind_failure = _terminal_write_still_binds_what_was_reviewed(
                    seg, ctx, action, what="convergence"
                )
                if bind_failure is not None:
                    return {"seg": seg, "converged": False, "outcome": "failed",
                            "reason": "converge-write-review-moved", "detail": bind_failure}
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

            if action["action"] == "converged_by_rejection":
                # #527. The OTHER terminal ledger write a later invocation
                # cannot undo by itself, and the one that rests on a human
                # judgement rather than on a reviewer's clean verdict -- so it
                # goes through the same pre-write binding check the cap does,
                # under the helper's own general name. Refusing writes
                # NOTHING, which on this path is self-healing in the strong
                # sense: the record and the review are both still on disk and
                # both still match, so the identical invocation re-derives the
                # identical action next time.
                bind_failure = _terminal_write_still_binds_what_was_reviewed(
                    seg, ctx, action, what="convergence"
                )
                if bind_failure is not None:
                    return {"seg": seg, "converged": False, "outcome": "failed",
                            "reason": "converge-write-review-moved", "detail": bind_failure}
                # RESIDUAL, stated because the cap fork's own version of it is
                # stated one branch below and is INCOMPLETE for both: the
                # helper is check-then-write, so review.json can still be
                # replaced between it and ledger_update.py's own read -- by a
                # V2 carrying the same run/seg/round token (a pure function of
                # those three) over the same unread draft. enrich_converged_
                # fields() re-reads review.json but binds only that token
                # prefix and the draft hash, so such a V2 would be converged
                # having never been attested. NOT closed here, deliberately,
                # and the reason is no longer an asymmetry with the branch
                # above: since #622 all THREE terminal writes in this function
                # -- the cap, this rejection convergence and the ordinary
                # already_converged one -- go through the same pre-write
                # binding check, so nothing would be evened out by adding a
                # lease on this one fork. What remains is that the check is
                # check-then-write in every one of the three. Closing THAT
                # needs a precondition inside ledger_update.py, where the
                # authoritative read happens -- a file this driver does not
                # own, which is the same boundary the cap fork draws, and one
                # this driver cannot cross for the further reason that a
                # blanket clean-verdict precondition there would refuse this
                # very fork (see the already_converged branch above for the
                # whole argument). Tracked in CHANGELOG.md's Known
                # limitations.
                rounds = _ledger_rounds_value(action["round_label"], ctx.translate_cfg["max_fix_rounds"])
                rec = write_ledger(
                    ctx.dirs, seg,
                    {"status": "converged", "rounds": rounds,
                     "note": _rejection_convergence_note(seg, action["rejection"])},
                    run_id=ctx.run_id, needs_cache_key=True,
                    durable_root_str=ctx.durable_root_str, plugin_root_str=ctx.plugin_root_str,
                )
                if not rec.get("success"):
                    return {"seg": seg, "converged": False, "outcome": "failed",
                            "reason": "ledger-write-failed", "detail": rec.get("error")}
                # `cause` travels so run()'s own per-segment `results` (never
                # its `summary`, whose converged bucket carries segment IDs
                # alone) can tell this convergence from a reviewer's clean one
                # without re-reading the ledger. The outcome field itself stays
                # "converged" -- run()'s totality check partitions on THAT, and
                # a new bucket would drop these segments out of every summary.
                return {"seg": seg, "converged": True, "outcome": "converged",
                        "cause": "rejected_findings"}

            if action["action"] == "cap_reached":
                # The ONE terminal ledger write in this function that a
                # later invocation cannot undo by itself, so it is the one
                # that has to prove it still describes reviewed bytes AND
                # the verdict reached over them -- see
                # _terminal_write_still_binds_what_was_reviewed() for the
                # race, and for the precondition it starts from and then
                # widens. Refusing writes NOTHING, so whatever fragment is
                # already on disk is what survives -- which is better than
                # capping over unreviewed bytes in every case, but is only
                # SELF-HEALING where that fragment is one select_segments.py
                # still selects. It is, on the two paths that matter: a
                # segment with no prior entry stays not_started, and a
                # segment reached through reopen_capped has already had its
                # cap replaced by in_progress (see that branch above, which
                # makes the reopen durable BEFORE dispatch precisely so this
                # is true). It is NOT self-healing on a segment carrying a
                # cap this invocation did not reopen, or a `blocked`
                # fragment: those stay human_escalation and need a human.
                # Do not shorten this back to "no ledger write means the
                # segment stays selectable" -- that sentence was in the
                # 1.20.0 release note and was wrong.
                bind_failure = _terminal_write_still_binds_what_was_reviewed(seg, ctx, action)
                if bind_failure is not None:
                    return {"seg": seg, "converged": False, "outcome": "failed",
                            "reason": "cap-write-draft-moved", "detail": bind_failure}
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
                review_obj = _read_review_obj(ctx, seg, fallback_findings=action.get("findings"))
                fix_prompt = render_fix_prompt(ctx, seg, int(round_label), review_obj)
                return {
                    "seg": seg, "converged": False, "outcome": "needs_fix", "reason": "needs_fix",
                    "round_label": round_label, "findings": action.get("findings"), "fix_prompt": fix_prompt,
                }

            if action["action"] == "translate":
                # #450: this invocation's OWN claim admission is checked
                # FIRST and UNCONDITIONALLY -- before write_ledger() AND
                # before the on-disk-only check right below, never after.
                # See claim_capability_refusal_for_translate()'s own
                # docstring for why this is a THIRD, additive layer rather
                # than a replacement for either #438 D8 chokepoint (this
                # driver's own check below, and codex_job.py's, owned
                # separately): it closes the one case neither of those can
                # see -- ctx.claims still names this segment even though
                # the on-disk record the row below reads has since moved
                # out from under this run.
                capability_refusal = claim_capability_refusal_for_translate(ctx, seg)
                if capability_refusal is not None:
                    return {"seg": seg, "converged": False, "outcome": "failed",
                            "reason": "invocation-claim-translate-refused",
                            "detail": capability_refusal}
                # #438 D8: refuse BEFORE the ledger write below, never
                # after -- see claim_refusal_for_translate()'s own
                # docstring for why placement here (rather than after
                # run_one_codex_job()) is what keeps the ledger fragment,
                # not only the draft bytes, intact on refusal.
                claim_refusal = claim_refusal_for_translate(ctx, seg)
                if claim_refusal is not None:
                    return {"seg": seg, "converged": False, "outcome": "failed",
                            "reason": "claimed-segment-translate-refused",
                            "detail": claim_refusal}
                # #620: this write replaces the fragment WHOLESALE, so writing
                # a bare {"status": "in_progress"} here erases any promotion
                # note already standing. That is not cosmetic: the one path
                # that reaches this branch WITH such a note standing is the
                # positive exception the note exists to serve -- a promoted
                # draft that reads invalid, being legitimately re-translated
                # -- and if run_one_codex_job() below then fails transiently,
                # the draft is untouched while the evidence naming it is gone,
                # so the NEXT derivation halts at invalid_post_fix_draft and
                # stays there. Re-state it instead; see
                # _carried_promotion_note() for why re-stating can only
                # preserve a match, never create one.
                in_progress_fields = {"status": "in_progress"}
                carried_note = _carried_promotion_note(ctx.dirs, seg)
                if carried_note is not None:
                    in_progress_fields["note"] = carried_note
                rec = write_ledger(
                    ctx.dirs, seg, in_progress_fields,
                    durable_root_str=ctx.durable_root_str, plugin_root_str=ctx.plugin_root_str,
                )
                if not rec.get("success"):
                    return {"seg": seg, "converged": False, "outcome": "failed",
                            "reason": "ledger-write-failed", "detail": rec.get("error")}
                result = run_one_codex_job(ctx, kind="translate", seg=seg)
                if not result["ok"]:
                    return {"seg": seg, "converged": False, "outcome": "failed", "stage": "translate",
                             "reason": result["reason"], "error_detail": result["error_detail"]}
                # #620: the promotion evidence _translate_redispatched_since()
                # reads, and the reason it is written HERE rather than folded
                # into the in_progress write above. That write happens before
                # the dispatch, so it can only ever prove INTENT: a driver
                # killed between the two, a launch that failed, or a
                # codex_job.py that adopted an already-valid canonical without
                # launching would all leave it standing over a draft no
                # translate produced -- and an operator's later hand repair of
                # that draft would then be re-translated over, which is the
                # whole defect. `ok and not adopted` is exactly
                # codex_job.py's own `promoted` (its finalize() sets
                # ok = promoted or adopted), i.e. a candidate that passed its
                # gates and REPLACED the canonical.
                #
                # EVERY adoption is deliberately left unstamped, including
                # adopt_pending() -- which DOES replace the canonical, with a
                # prior run's validated attempt, and so is a promotion in the
                # ordinary sense. Admitting it would buy one avoided halt in a
                # rarer case at the cost of widening the only condition that
                # can ever authorize discarding a draft, and the direction
                # this costs is the safe one: an unstamped fragment reads
                # False, which halts instead of retrying.
                #
                # The note names the promoted draft's content hash, not just
                # the fact of a promotion: a constant marker still reads True
                # after the operator has edited that draft, so the hash is
                # what makes the evidence describe the file currently on disk.
                # Hashing failure means NO evidence rather than wrong
                # evidence: this invocation records none and returns failed,
                # leaving the promoted draft and the recoverable in_progress
                # fragment on disk for the next invocation to derive from.
                #
                # result["adopted"], not .get(): every parseable outcome
                # carries the key (see _codex_job_outcome() and
                # run_one_codex_job()'s except fallback), and .get()'s None
                # default is FALSY, i.e. it would stamp -- the permissive
                # direction on the one flag that can authorize discarding a
                # draft. A KeyError becomes unexpected-error, which is the
                # halt, matching this function's own "every doubt stops"
                # policy.
                if not result["adopted"]:
                    try:
                        promoted_sha1 = current_draft_sha1(
                            seg, ctx.dirs["durable_root"] / "segments", ctx.dirs["scripts_dir"]
                        )
                    except DriverError as exc:
                        # reason + detail, NOT stage + error_detail: that shape
                        # is reserved for codex_job.py's OWN verbatim reported
                        # values (#398), and this is the driver's own failure.
                        return {"seg": seg, "converged": False, "outcome": "failed",
                                "reason": "promotion-hash-failed", "detail": str(exc)}
                    rec = write_ledger(
                        ctx.dirs, seg,
                        {"status": "in_progress",
                         "note": _translate_promotion_note(promoted_sha1)},
                        durable_root_str=ctx.durable_root_str, plugin_root_str=ctx.plugin_root_str,
                    )
                    if not rec.get("success"):
                        return {"seg": seg, "converged": False, "outcome": "failed",
                                "reason": "ledger-write-failed", "detail": rec.get("error")}
                continue  # re-derive: should now see "review round 1"

            if action["action"] == "review":
                round_label = action["round_label"]
                if action.get("cause") == "fabricated_loc":
                    if fabricated_loc_retries >= 1:
                        # Already retried once -- the reviewer is persistently
                        # emitting fabricated locs (within its own documented
                        # latitude, not a fault of its own). Terminate NOW,
                        # never dispatch a third time: the template's own
                        # reason, no ledger write (matches runRound()'s own
                        # "blocked" -> recoverable-next-run handling).
                        return {"seg": seg, "converged": False, "outcome": "failed",
                                "reason": "review-fabricated-loc"}
                    fabricated_loc_retries += 1
                if action.get("reopen_capped"):
                    # #432, second half. derive_next_action() decided to
                    # re-review a segment a PRIOR invocation may already
                    # have capped, but that decision lives only in this
                    # process's memory; the durable record still says
                    # {"status": "non_converged", "reason": "cap"}, which
                    # select_segments.py's classify_segment() maps to
                    # human_escalation via HUMAN_ESCALATION_STATUSES and
                    # EXCLUDES from the default dispatch set. Every way
                    # this iteration can end without reaching a terminal
                    # write -- the dispatch below failing or timing out,
                    # the driver being killed after codex_job.py promotes
                    # the review but before the convergence write, any
                    # exception in the loop body -- would then leave that
                    # cap standing as the only durable fact, and only an
                    # explicit --only-segs override could ever pick the
                    # segment up again. That directly contradicts this
                    # function's own stated invariant for every non-
                    # terminal failure (see its docstring: "NO terminal
                    # ledger write, so the in_progress fragment already on
                    # disk stays the durable record and select_segments.
                    # py's 'recoverable' default retries this segment next
                    # invocation") -- an invariant that silently assumes
                    # the fragment on disk is ALREADY in_progress, which is
                    # true for every other path here and false for exactly
                    # this one.
                    #
                    # So the reopen is made durable FIRST and CONFIRMED
                    # (ledger_update.py replaces the fragment wholesale --
                    # its own "Full replace only" contract -- so `reason:
                    # "cap"` is gone, not merely overlaid), and a failed
                    # reopen returns without dispatching: spending a codex
                    # job whose successful result could not be recorded
                    # recoverably is the worse of the two failures. Note
                    # the write is unconditional rather than gated on
                    # reading the current fragment back -- a segment
                    # reaching this branch that was never capped is
                    # already in_progress, so writing in_progress is a
                    # no-op for it, and this avoids adding a ledger READ
                    # path to a driver that deliberately has none.
                    # The #432 note covers BOTH ways that branch is reached
                    # -- the draft moved since the capped review, and the
                    # capped review carrying no draft_sha1 at all -- so it
                    # never claims an edit that did not happen. #461 gets
                    # its OWN sentence rather than being folded into that
                    # one, because it is a different fact: there the draft
                    # did NOT move, an operator set the verdict aside. A
                    # durable note asserting an edit that never happened
                    # would be exactly the "record outlives the fact it
                    # attests" shape this whole artifact exists to avoid,
                    # and the ledger is the one place an operator later
                    # reads to find out why the cap went away.
                    reopen_note = (
                        "reopened for a fresh final review: an operator rejected the "
                        "review this segment was capped on as unfounded (#461)"
                        if action.get("cause") == "rejected_findings"
                        else "reopened for a fresh final review: the review this "
                             "segment was capped on no longer describes the draft "
                             "on disk (#432)"
                    )
                    rec = write_ledger(
                        ctx.dirs, seg,
                        {"status": "in_progress", "note": reopen_note},
                        durable_root_str=ctx.durable_root_str, plugin_root_str=ctx.plugin_root_str,
                    )
                    if not rec.get("success"):
                        return {"seg": seg, "converged": False, "outcome": "failed",
                                "reason": "ledger-write-failed", "detail": rec.get("error")}
                result = run_one_codex_job(ctx, kind="review", seg=seg, round_label=round_label)
                if not result["ok"]:
                    return {"seg": seg, "converged": False, "outcome": "failed", "stage": "review",
                             "round_label": round_label,
                             "reason": result["reason"], "error_detail": result["error_detail"]}
                continue  # re-derive from the freshly promoted canonical review

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
            # already_converged/converged_by_rejection/invalid_post_fix_draft)
            # -- #527 added the 7th and handled it above in the same change,
            # which is the discipline this comment exists to keep. Unlike the
            # loop-exhaustion fallback below, this one is not made reachable
            # by anything shipped so far.
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
        "--from-cap",
        default=None,
        metavar="SEG1,SEG2,...",
        help=(
            "#438: forwarded verbatim to select_segments.py's own --from-cap "
            "-- request claim ADMISSION for the named ids under the "
            "'--from-cap' admission profile (capped, human_escalation "
            "segments with a non-clean stored review). Admission is "
            "single-phase and it WRITES: every id that passes gets a durable "
            "claim record plus a re-stamped draft dispatch_token, inside the "
            "one gate call, so this driver resolves a RUN_ID before it (and "
            "leaves runs/<RUN_ID>/ behind even if the gate then refuses). "
            "Never a blanket authorization: only the ids named here, admitted "
            "and reported back in this run's 'claims'."
        ),
    )
    parser.add_argument(
        "--from-converged",
        default=None,
        metavar="SEG1,SEG2,...",
        help=(
            "#438: forwarded verbatim to select_segments.py's own "
            "--from-converged -- request claim ADMISSION for the named "
            "ids under the '--from-converged' admission profile (previously "
            "converged segments whose draft has since drifted). Admission is "
            "single-phase and it WRITES -- see --from-cap's own help text for "
            "what that costs. Never a blanket authorization: only the ids "
            "named here."
        ),
    )
    # The population sentence below names the convergence sentinel WITHOUT
    # spelling its marker filename, and that is deliberate rather than sloppy.
    # This driver is not a participant in the sentinel contract, and
    # select_segments.test.py's census refuses a non-participant that carries
    # that token ANYWHERE outside a docstring -- an argparse help= string is
    # not one, so spelling it here would trade a clean census for an exemption
    # this file has no business holding. The exact filename lives in
    # select_segments.py's own --from-stalled help, which is a participant and
    # spells it there.
    parser.add_argument(
        "--from-stalled",
        default=None,
        metavar="SEG1,SEG2,...",
        help=(
            "#455: forwarded verbatim to select_segments.py's own "
            "--from-stalled -- claims the named ids for RE-REVIEW under the "
            "'--from-stalled' admission profile (a stalled, previously-"
            "converged unit stuck outside every other route: materialized "
            "status in_progress, the convergence sentinel present, no "
            "reviewed_draft_sha1, and a stored review that is stale against "
            "the current draft; select_segments.py --help names the marker "
            "file). An admitted id can NEVER be translated by "
            "this invocation -- claim_capability_refusal_for_translate() "
            "refuses it unconditionally, same as --from-cap/--from-converged. "
            "Admission is single-phase and it WRITES: every id that passes "
            "gets a durable claim record plus a re-stamped draft "
            "dispatch_token, inside the one gate call -- see --from-cap's "
            "own help text for the RUN_ID consequence that follows from "
            "that. UNLIKE --from-cap/--from-converged, this profile also "
            "rests on an assertion this plugin cannot check: naming an id "
            "here ASSERTS, on the operator's word alone, that no Workflow "
            "fix turn and no OTHER select_segments.py claim invocation is "
            "touching that id right now. This plugin proves only that no "
            "driver and no codex job currently hold this segment (runs/"
            ".driver.lock, segments/.codex_job.<seg>.lock) -- it cannot "
            "prove the rest, and does not pretend to. If the assertion is "
            "wrong, a concurrent fix turn writes the canonical draft "
            "directly and copies whatever dispatch_token it read "
            "(mass-translate-wf.template.js:1288): depending on timing it "
            "either loses its own work, or leaves the claim's re-stamped "
            "draft carrying content that nobody has re-reviewed. Never a "
            "blanket authorization: only the ids named here."
        ),
    )
    parser.add_argument(
        "--resume-from-run-id",
        default=None,
        metavar="RUN_ID",
        help=(
            "#458: resolve this invocation's resume-integrity RUN_ID against "
            "RUN_ID ALONE, instead of against every candidate "
            "_resumable_run_id_candidates() would discover. Pins WHICH "
            "candidate is offered; it does NOT bypass the digest comparison "
            "-- resume_setup.py remains the sole authority on whether "
            "resuming is safe, exactly as when this flag is absent. Without "
            "it the newest digest-matching run always wins, so a prior run "
            "sharing a digest with a newer one cannot be reached at all, and "
            "an invocation whose payload matches NO candidate mints "
            "a fresh RUN_ID and claims every named segment under it -- except "
            "that since #742 a segment whose draft is stamped for another run is "
            "refused rather than claimed -- on this UNPINNED path every selected "
            "segment whose classification is not one of: "
            + ", ".join(sorted(FOREIGN_DRAFT_GATE_EXEMPT_CATEGORIES))
            + " (see FOREIGN_DRAFT_GATE_EXEMPT_CATEGORIES for why); under a pin, "
            "every selected segment, with no exemption at all. "
            "Refusals under a pin: exit 1 when runs/RUN_ID is not a "
            "directory or carries no regular input.digest (an established "
            "state, so a gate refusal), when the pinned run's digest does "
            "NOT match this invocation (refused rather than minting a fresh "
            "id nobody asked for), or when a SELECTED segment's draft is "
            "stamped for a different run (scope with --only-segs -- the pin "
            "does not adopt another run's drafts); exit 2 for an unsafe id "
            "or a filesystem state this script could not establish."
        ),
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
    return parser


def run(args, dirs: dict) -> dict:
    session_id = fresh_session_id()
    durable_root = dirs["durable_root"]

    if args.only_segs is not None:
        for seg in (s.strip() for s in args.only_segs.split(",") if s.strip()):
            problem = validate_seg(seg)
            if problem is not None:
                fatal(f"--only-segs: unsafe segment id: {problem}", exit_code=2)
    # #438/#455: same fail-fast local validation --only-segs already gets
    # above, before any of the three flag values is ever spliced into the
    # select_segments.py subprocess argv.
    for flag_name, flag_value in (
        ("--from-cap", args.from_cap),
        ("--from-converged", args.from_converged),
        ("--from-stalled", args.from_stalled),
    ):
        if flag_value is not None:
            for seg in (s.strip() for s in flag_value.split(",") if s.strip()):
                problem = validate_seg(seg)
                if problem is not None:
                    fatal(f"{flag_name}: unsafe segment id: {problem}", exit_code=2)

    # #458: same fail-fast shape, before the pinned id is ever used to build
    # runs/<ID> or handed to resume_setup.py. exit 2 -- an id this script
    # refuses to spell is a USAGE error, not a gate refusing an established
    # state (see this file's own exit-code contract in the module docstring).
    if args.resume_from_run_id is not None:
        problem = validate_run_id(args.resume_from_run_id)
        if problem is not None:
            fatal(f"--resume-from-run-id: {problem}", exit_code=2)

    lock_fd = acquire_driver_lock(durable_root, session_id=session_id)
    append_journal(durable_root, session_id, {"type": "driver_started", "pid": os.getpid()})
    try:
        # #438: a claim is SINGLE-PHASE and the ONE select_segments.py call
        # below is what writes it, so the run id it re-stamps drafts to must
        # exist BEFORE that call. Resolved here, once, and reused verbatim
        # as ctx.run_id further down rather than resolved a second time
        # after selection.
        #
        # Stated as measured, not as a scare: a second resolve does NOT
        # produce a different id today. resume_setup.py recomputes the same
        # input_digest, finds the runs/<ID>/input.digest this invocation
        # just wrote, and RESUMES the same id (verified by mutating this
        # branch to resolve unconditionally: same id, same dispatch, still
        # green). What the single resolution buys is therefore not a bug fix
        # but the removal of a dependency: reusing the value makes "the id
        # the claim stamped onto the drafts IS the id the dispatch loop runs
        # under" true by construction here, instead of true because a
        # sibling script's digest matching happens to be stable across a
        # window in which THIS invocation has already written to the tree.
        # If it ever were not -- a fresh second id -- every draft the claim
        # re-stamped would be orphaned, derive_next_action() would fall
        # through to "translate", and D8's guard would not catch it (it
        # looks for a record under the DISPATCH run's id, and the record
        # would be filed under the other one). It also saves a second full
        # resume_setup.py round trip, which costs a per-segment cache_key.py
        # spawn each.
        #
        # An earlier revision refused to do this and passed no run id at
        # all, which made --from-cap/--from-converged DEAD ON ARRIVAL here:
        # select_segments.py fatals on a claim without --run-id, so every
        # claim this driver forwarded was refused at the gate. The hazard
        # that revision named was real -- resolve_run_id() -> resume_setup.py
        # writes runs/<ID>/input.digest, and #409 Step 3 reads a digest as
        # proof the resume-integrity gate ran for that id -- but it is closed
        # on the selector's side now, not worked around here. See
        # run_select_segments()'s own docstring for the full account of what
        # closed it (the one-shot evidence snapshot, plus the fresh-id-with-
        # pre-existing-evidence refusal) and why a newly minted id cannot
        # enter that evidence set to begin with.
        #
        # The cost that is NOT closed, and is accepted here deliberately:
        # ANY refusal after this point leaves an ORPHANED runs/<ID>/
        # directory behind -- a selector refusal (an S-gate, a profile
        # condition, D6, the --allow-retranslate-converged overlap
        # rejection), and equally this driver's own volume cap below, which
        # runs after the gate. Resolution is therefore
        # gated on a claim actually having been requested -- an ordinary
        # dispatch keeps its original "refuse with no side effects"
        # property, unchanged from before #438 -- and the id is journalled
        # the moment it is minted so an operator triaging a refusal can see
        # which directory this invocation created.
        claim_requested = (
            args.from_cap is not None
            or args.from_converged is not None
            or args.from_stalled is not None
        )
        translate_cfg = None
        run_result = None
        run_id = None
        if claim_requested:
            translate_cfg = load_translate_config(durable_root)
            run_result = resolve_run_id(
                dirs, translate_cfg=translate_cfg,
                plugin_root_str=args.plugin_root, durable_root_str=args.durable_root,
                pinned_run_id=args.resume_from_run_id,
            )
            run_id = accepted_run_id(run_result)
            append_journal(
                durable_root, session_id,
                {
                    "type": "run_id_resolved", "run_id": run_id,
                    "resume": run_result.get("resume"), "before_selection": True,
                },
            )

        select_result = run_select_segments(
            dirs,
            only_segs=args.only_segs,
            allow_retranslate_converged=args.allow_retranslate_converged,
            allow_empty=args.allow_empty,
            from_cap=args.from_cap,
            from_converged=args.from_converged,
            from_stalled=args.from_stalled,
            run_id=run_id,
            run_resume=run_resume_literal(run_result) if run_result is not None else None,
            durable_root_str=args.durable_root,
            plugin_root_str=args.plugin_root,
        )
        if not select_result.get("success"):
            # #530: a refusal payload MAY carry the outstanding-eligible set.
            # Exactly one does -- select_segments.py's empty-SEGS refusal,
            # which is that issue's purest shape (the operator's own
            # --only-segs selected nothing, and what is still outstanding is
            # what turns the refusal into a next action). Forwarded here
            # rather than left in the child's payload because this driver
            # runs the selector with capture_output=True, so its stdout is
            # what this driver reads. The field is what makes the remainder
            # DURABLE -- it is journaled here, where a stderr line never
            # could be. The operator-facing copy of the same fact is the
            # selector's own stderr line, which #551 now relays verbatim
            # (_relay_selector_stderr(), above); this driver deliberately
            # does NOT re-print it, or the run log would carry the same
            # sentence twice under two different prefixes.
            #
            # Conditional on the key being a list, not on the refusal kind:
            # every OTHER post-selection refusal is about specific named
            # segments and deliberately omits it, and a driver that demanded
            # it here would refuse those. Absent means "this refusal has
            # nothing to say about the remainder", never "the remainder is
            # empty" -- which is why the key is omitted rather than reported
            # as [].
            refusal_outstanding = select_result.get("eligible_not_dispatched")
            refusal_extra = (
                {"eligible_not_dispatched": refusal_outstanding}
                if isinstance(refusal_outstanding, list)
                else {}
            )
            append_journal(
                durable_root, session_id,
                {
                    "type": "step1_gate_refused", "error": select_result.get("error"),
                    **refusal_extra,
                },
            )
            fatal(
                f"Step 1 gate refused: {select_result.get('error')}",
                exit_code=1,
                classification=select_result.get("classification"),
                counts=select_result.get("counts"),
                **refusal_extra,
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

        # #438 D3: validated on EVERY invocation, regardless of whether
        # --from-cap/--from-converged were passed -- a select_segments.py
        # that silently stopped emitting 'claims' at all must be refused
        # here, not read as "nothing was claimed". See parse_claims_field()'s
        # own docstring for the full validation list.
        claims = parse_claims_field(select_result, segs)
        # #545/#549: validated on EVERY invocation for the same reason `claims`
        # is, and journaled beside it -- this record is the only DURABLE copy
        # of the reported profile, so a fix that reached stdout alone would
        # leave the audit surface wrong.
        claims_admitted_via = parse_claims_admitted_via(select_result, claims)
        # #536: gated on the flag, not validated unconditionally -- see
        # parse_claims_from_cap_over_sentinel() for why that is lossless.
        claims_from_cap_over_sentinel = (
            parse_claims_from_cap_over_sentinel(select_result, claims, claims_admitted_via)
            if args.from_cap is not None
            else []
        )

        # #530: the eligible units this dispatch is NOT carrying. Computed by
        # select_segments.py (which owns DEFAULT_ELIGIBLE_CATEGORIES) and read
        # from its payload rather than re-derived here -- a second copy of that
        # membership in this file would drift silently the first time the
        # eligible set changed.
        #
        # REQUIRED, not defaulted -- written in the SHAPE of the `segs` array
        # check above (one isinstance, then fatal with exit_code=2 and the same
        # "has no 'X' array" wording), for the REASON parse_claims_field()
        # gives for its own missing-field refusal: "a select_segments.py that
        # silently stopped emitting 'claims' at all must be refused here, not
        # read as 'nothing was claimed'". That reason applies with full force
        # to a field whose whole purpose is that absence and "nothing
        # outstanding" must not print identically. The risk classes differ --
        # `claims` gates dispatch safety and this field is report-only -- but
        # reading a missing key as `[]` here would recreate exactly the silent
        # green this field closes. Skew between
        # driver and selector is possible during an interrupted manual Step 0a
        # (the copy pass runs before the bundle markers are written); how often
        # is not measured, and exit 2 naming the field is the chosen fail-loud
        # tradeoff.
        eligible_not_dispatched = select_result.get("eligible_not_dispatched")
        if not isinstance(eligible_not_dispatched, list):
            fatal(
                "select_segments.py's JSON output has no 'eligible_not_dispatched' array -- "
                "this driver cannot report which eligible units the dispatch is leaving out, "
                "and reporting nothing would be indistinguishable from nothing being left out. "
                "Refused rather than defaulted.",
                exit_code=2,
            )

        append_journal(
            durable_root, session_id,
            {
                "type": "step1_gate_passed", "segs": segs,
                "counts": select_result.get("counts"), "claims": claims,
                "claims_admitted_via": claims_admitted_via,
                # #536: the only DURABLE record of this fact on the driver
                # path. The selector writes it nowhere on disk -- deliberately
                # not a claim-record field -- and its stderr disclosure is
                # discarded here, so without this line the run leaves no trace
                # that a re-review was authorized over a unit that had already
                # converged once.
                "claims_from_cap_over_sentinel": claims_from_cap_over_sentinel,
                "eligible_not_dispatched": eligible_not_dispatched,
            },
        )
        # The operator-facing half of this disclosure is NOT re-printed here.
        # select_segments.py prints its own one-line version before it emits
        # either payload that carries the field, and #551 relays that stream
        # verbatim onto this driver's stderr -- which is the channel that
        # reaches the redirected run log live, since this driver's stdout
        # carries exactly ONE line at exit. A driver-side copy would put the
        # same sentence in that log twice under two different prefixes, and
        # the ids -- which are what distinguish a deliberate batch from a
        # forgotten unit -- are read less, not more, when doubled. What this
        # driver owns is the DURABLE copy in the journal entry above.

        engine_cfg = load_engine_config(durable_root)
        volume_refusal = check_volume_cap(
            len(segs), engine_cfg["max_fix_rounds"], engine_cfg["max_codex_jobs_per_batch"],
            # #514: the ids in `claims` can never spend a translate job --
            # see codex_jobs_per_claimed_segment().
            n_claimed=len(claims),
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
                "estimatedCodexJobs": estimate_codex_jobs(
                    len(segs), len(claims), engine_cfg["max_fix_rounds"]
                ),
                # #514. Without it an operator reading this event cannot
                # divide the estimate by any per-segment figure the docs
                # give them: a claimed id and an unclaimed one cost
                # different amounts, and only this count says how many of
                # each the batch held.
                "claimedSegs": len(claims),
                "codexJobsCap": engine_cfg["max_codex_jobs_per_batch"],
            },
        )

        if not segs:
            result = {
                "success": True, "session_id": session_id, "durable_root": str(durable_root),
                "segs": segs, "counts": select_result.get("counts"), "engine": engine_cfg,
                "dispatched": False, "results": [], "claims": claims,
                "claims_admitted_via": claims_admitted_via,
                # #536: carried in BOTH result payloads for the same reason
                # `claims_admitted_via` is -- this driver's stdout is the one
                # artifact every caller reads, and a fact reported only on
                # stderr and in the journal is a fact two of the three
                # channels disagree about.
                "claims_from_cap_over_sentinel": claims_from_cap_over_sentinel,
                # Always present, `null` on the ordinary path that never got
                # as far as resolving one -- never a key that appears only
                # sometimes. On a claim invocation this is the id whose
                # runs/<ID>/ directory now exists on disk, which an operator
                # reading an empty-SEGS result needs to be told about
                # (nothing else in this payload would mention it).
                "run_id": run_id,
                "resume": run_result.get("resume") if run_result is not None else None,
                "note": "nothing to dispatch (SEGS is empty).",
            }
            append_journal(durable_root, session_id, {"type": "driver_exit", "success": True})
            return result

        # Resolved ONCE per invocation. On a claim invocation both are
        # already in hand from before the selector ran (the claim's own
        # writes are stamped with THIS id, so re-resolving here could hand
        # the dispatch loop a different one and orphan every draft the claim
        # just re-stamped); on an ordinary invocation this is the original,
        # unchanged pre-#438 position, after both gates have passed and with
        # no side effect paid for a run that refused.
        if run_result is None:
            translate_cfg = load_translate_config(durable_root)
            run_result = resolve_run_id(
                dirs, translate_cfg=translate_cfg,
                plugin_root_str=args.plugin_root, durable_root_str=args.durable_root,
                pinned_run_id=args.resume_from_run_id,
            )
            run_id = accepted_run_id(run_result)
            append_journal(
                durable_root, session_id,
                {
                    "type": "run_id_resolved", "run_id": run_id,
                    "resume": run_result.get("resume"), "before_selection": False,
                },
            )

        # #458, widened to the unpinned path by #742. Placed here on purpose:
        # run_id is final for BOTH paths by now (the claim path resolved it
        # before the selector, the ordinary path just above), `segs` is the
        # final selected set, and nothing has been dispatched yet.
        #
        # The branch decides only the SEGMENT SET -- pinned checks every
        # selected segment, #458's own scope, unchanged byte for byte. The
        # wording flags are passed the same way on both paths, including the
        # real `resume` value: `resumed` is unread when `pinned` is true, and
        # hardcoding a false one there would be a lie a future reader could
        # start believing the moment the pinned message wants it.
        pinned = args.resume_from_run_id is not None
        refuse_run_over_foreign_drafts(
            segs if pinned else segs_covered_by_foreign_draft_gate(segs, select_result),
            run_id,
            durable_root / "segments",
            pinned=pinned,
            resumed=bool(run_result.get("resume")),
        )

        companion_path = resolve_companion_path(dirs, node_bin=args.node)

        ctx = DispatchContext(
            dirs=dirs, run_id=run_id, translate_cfg=translate_cfg, companion_path=companion_path,
            durable_root_str=args.durable_root, plugin_root_str=args.plugin_root,
            node_bin=args.node, session_id=session_id, claims=claims,
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
            "claims": claims,
            "claims_admitted_via": claims_admitted_via,
            # #536 -- see the empty-SEGS result above for why both carry it.
            "claims_from_cap_over_sentinel": claims_from_cap_over_sentinel,
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
        print(dumps_line(payload))
        return exc.exit_code
    except Exception as exc:  # pragma: no cover -- defensive catch-all
        print(
            dumps_line({"success": False, "error": f"unexpected error: {exc}"})
        )
        return 2

    print(dumps_line(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
