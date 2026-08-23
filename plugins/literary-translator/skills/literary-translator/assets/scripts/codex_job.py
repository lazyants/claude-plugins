#!/usr/bin/env python3
"""codex_job.py -- shipped, isolating, validate-before-promote codex-job driver (#198, v1.4.7).

Drives ONE codex `task` job to a terminal state and, on success, VALIDATES the
isolated attempt artifact and only then ATOMICALLY PROMOTES it into its canonical
`segments/<seg>.<draft|review>.json` path. Codex stays the sole translator/reviewer;
this driver only launches it, polls to terminal, pre-filters the result, and
os.replace()s a validated attempt into place. Claude still only drives/polls/fixes.

Design (PLAN-198 §2.1; the 7 steps below map 1:1) -- #409 SANDBOX HARDENING:
  1. Validate args + establish TWO absolute time ceilings (poll window + finalize budget).
     EVERY subprocess.run gets an explicit stdlib timeout= (NO external `timeout` binary).
  2. Isolate codex output BY WRITE-CONFINEMENT, not by a checked path: codex is launched
     with `--cwd` pointed at a FRESH, single-use, per-invocation SANDBOX directory that is
     verified to sit OUTSIDE any git working tree (see _sandbox_is_confined). codex-companion
     resolves its own `workspace-write` root via `git rev-parse --show-toplevel` WALKING UP
     from `--cwd` (lib/workspace.mjs:resolveWorkspaceRoot) -- so `--cwd self.root` (the OLD
     design) handed codex write access to the WHOLE durable_root repo (scripts/, segments/,
     the lock, the joblog); `--cwd` a mere SUBDIRECTORY of that same repo is not the fix
     either, since the git walk-up still finds the SAME outer toplevel. Only a `--cwd` that
     resolves to ITSELF (no enclosing repo) shrinks the actual OS-level sandbox to that one
     directory. The sandbox holds nothing but this job's frozen prompt and (once codex writes
     it) its attempt file. On success, the candidate is copied OUT via a FILE-DESCRIPTOR-
     pinned, digest-verified copy (_publish_from_sandbox) into the private staging slot in
     segdir -- never trusted by path, since a path re-checked-then-reused is exactly what a
     symlink swap defeats. On timeout/failure the sandbox is never read again and is
     abandoned (rmtree'd); a straggling codex turn that outlives our poll/cancel can then only
     write into a directory nobody will ever consume from -- isolation, not proof of kill (the
     detached codex worker runs in its OWN session; codex-companion's own `cancel` is
     best-effort and does not prove the turn stopped).
  3. Acquire an exclusive per-seg DRIVER lease via a KERNEL fcntl.flock on a never-unlinked
     sentinel `.codex_job.<seg>.lock` (kernel auto-releases on crash -- no stale-break race).
     A lease-loser writes ONLY its own fail sentinel + stdout, NEVER the hygiene joblog.
  4. Hygiene (cancel a verified-same-workspace stale prior job) -> safe adoption of an
     already-valid same-token canonical -> #438 D8: for a translate, REFUSE if this seg
     holds a live claim record under --run-id (a healthy claimed draft already adopted
     above and never reaches this check; reaching it WITH a claim on record means the
     draft went missing/invalid since the claim) -> adopt a prior run's DEFERRED completed
     attempt (#213; re-validated through the same candidate gates before promotion) ->
     else launch fresh (detached background codex). The D8 refusal sits BETWEEN the two
     adoptions deliberately: launch() is NOT the only route in this file that can
     overwrite the canonical -- adopt_pending() os.replace()s a deferred attempt straight
     over it (see _canonical_replaceable(), which guards BOTH write sites by name) -- so a
     guard placed after adopt_pending() would still let a same-run deferred attempt
     destroy the exact draft the claim exists to preserve.
  5. Poll to a terminal job status or the poll deadline (cancel-on-deadline).
  6. Best-effort validate the ATTEMPT (kind-specific candidate-file gate), then ONE atomic
     os.replace -- no backup, no post-confirm. Validation-failure => canonical untouched.
     The pre-promote validation is a BEST-EFFORT PRE-FILTER; consumption-safety rests SOLELY
     on the Workflow's own ACCEPT gate re-validating the CURRENT canonical (§2.3). A
     `completed` attempt reached with no finalize budget left to validate it is DEFERRED
     (#213) to the deterministic pending slot rather than discarded -- recoverable; the
     NEXT dispatch's step-4 adopt_pending() validates + adopts it.
  7. Finalize within a reserved FINALIZE_TAIL: emit the ONE stdout JSON line, write the
     empty per-dispatch fail sentinel (iff not promoted) + terminal hygiene joblog (iff we
     hold the lease), and clean this invocation's OWN scratch by exact path -- with ONE
     deliberate exception, which is not disposable scratch despite carrying this
     invocation's own random component: the #429 `.att_superseded.*` link, a preserved copy
     of a pending occupant this invocation displaced. It is kept for HAND recovery and
     nothing ever re-adopts or collects it (see _defer_attempt()).

CLI (canonical path is DERIVED, never caller-supplied):
    python3 codex_job.py --kind {translate|review} --companion <abs codex-companion.mjs>
      --cwd <durable_root> --seg <seg> --prompt-file <abs prompt with EXACTLY one ⟦JOB_OUT⟧>
      --expect-token <RUN_ID:seg|RUN_ID:seg:r<label>> --run-id <RUN_ID, #438, REQUIRED>
      --disp <per-dispatch nonce>
      --deadline-sec <int> [--poll-sec <int default 15>]
      [--write] [--fresh] [--effort high] [--model <model>] [--node <exe default "node">]
      [--plugin-root <plugin install root, #412>]

Exit codes: 0 = promoted (or adopted) a validated artifact; 1 = launch/run/validate failure
(recoverable, wrote an empty fail sentinel); 2 = usage/env error.

stdlib-only, self-anchoring (sibling gate scripts located via __file__); copied to
<durable_root>/scripts/ at Step 0a (it IS a PLUGIN_BUNDLE_MEMBERS script -- see cache_key.py).
"""

import argparse
import fcntl
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import TypeGuard

# ---- Constants (PLAN-198 §2.1 / CONTRACT.md; frozen) ------------------------
CODEX_DEADLINE_SEC = 2700        # default codex-run poll window (45 min); overridden by --deadline-sec
CODEX_FINALIZE_BUDGET_SEC = 150  # extra budget past the poll deadline for cancel+validate+promote+finalize
FINALIZE_TAIL = 10               # reserved at the very end for the non-subprocess finalize (stdout/sentinel/joblog)
PER_CALL_CAP = 90                # hard ceiling for ANY single subprocess (sized to the slowest gate call)
CODEX_WAIT_GRACE_SEC = 600       # (Workflow-side wait grace; documented here for the shared wait-bound arithmetic)

# The JOB_OUT placeholder, spelled via escapes to avoid pasting raw U+27E6/U+27E7.
JOB_OUT_PLACEHOLDER = "⟦JOB_OUT⟧"

# Canonical segment-id safety contract. A seg id is either an ordinary body
# id (e.g. "seg01", "seg05_blocked_regen", "segAnchor") or a translate-decision
# FRONTBACK:{id} unit (e.g. "FRONTBACK:fm01"). It is spliced into filesystem
# paths and workflow shell commands, so it MUST be a path- and shell-safe
# allowlist. Keep this identical across every consuming script.
# NOTE: re.fullmatch (NOT re.match + "$") -- in Python "$" also matches just
# before a trailing newline, so re.match(r"...$", "seg01\n") would WRONGLY pass.
_SEG_ID_RE = re.compile(r"(?:FRONTBACK:)?[A-Za-z0-9_]+")
# Per-dispatch nonce: uuidgen hex+hyphens or the $RANDOM digit fallback, plus a couple of
# filename-safe extras. Must be a single path component -- never a separator, dot-only, or control char.
_DISP_RE = re.compile(r"[0-9A-Za-z][0-9A-Za-z._-]{0,127}")

_TERMINAL = frozenset(("completed", "failed", "cancelled"))
_ACTIVE = frozenset(("queued", "running"))

_O_CLOEXEC = getattr(os, "O_CLOEXEC", 0)
_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_O_NONBLOCK = getattr(os, "O_NONBLOCK", 0)
_O_DIRECTORY = getattr(os, "O_DIRECTORY", 0)

_COPY_CHUNK = 1 << 20  # 1 MiB read chunk for the sandbox->staging copy

# _is_regular() drains the WHOLE file to confirm it is readable (main's own version
# reads only the first byte); the bounds below cap that drain against two independent
# overrun shapes -- a huge or growing canonical could otherwise cost real time confirming
# an anomalous file:
_MAX_REGULAR_READ_BYTES = 64 << 20  # 64 MiB. Real canonical drafts run tens to a
# couple hundred KB (measured on the actual corpus) -- this is roughly 400x headroom
# over the largest legitimate file, generous enough to never trip on real content, small
# enough to still refuse rather than spend unbounded time confirming an anomalous one.
# Bounds the HUGE case: an upfront os.fstat().st_size check short-circuits before
# reading a single byte, and a running counter inside the drain loop backstops a file
# that GROWS past what st_size reported at open time.
#
# A single os.read() call that never returns at all (a hung network/FUSE mount) is NOT
# bounded here, deliberately: `git show main:.../codex_job.py` shows _sha256_fd() and
# _publish_from_sandbox() each drain a file with a plain, unbounded os.read() loop, on
# the SAME class of files, under the SAME per-segment lock, on every job -- neither has a
# stall bound. Bounding only THIS read loop out of three structurally identical ones
# would be a false sense of security, not a real one -- the process could still hang
# forever inside either of the other two -- and O_NONBLOCK has no effect on a regular
# file's read(), so nothing short of a signal-based interrupt (real, process-global
# machinery, and the only such handler this file would otherwise need) could close it
# here alone. A stalled read is a KNOWN, ACCEPTED limit shared with _sha256_fd() and
# _publish_from_sandbox(), not a defect specific to this method. The HUGE/GROWING bounds
# above and the phase-deadline check in the drain loop below still apply on every
# ITERATION between reads; only a single os.read() call that never returns at all falls
# outside all of them, exactly as it always has for the other two read loops.


SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))

# claim_record.py (#438) is a plugin-path sibling import -- SCRIPTS_DIR must be on
# sys.path first, same idiom final_audit.py/assemble.py/validate_assembled.py already
# use for their own sibling imports: a `python3 codex_job.py ...` invocation gets this
# for free (Python auto-prepends the running script's own directory), but a caller that
# loads this file via importlib.util.spec_from_file_location (every test in this suite)
# does not.
sys.path.insert(0, SCRIPTS_DIR)
import claim_record  # noqa: E402


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


def canonical_path(root, seg, kind):
    """Pure, side-effect-free canonical artifact-path deriver (importable by the
    draft/review path-convention audits). Returns
    ``<root>/segments/<seg>.<draft|review>.json`` -- draft for kind "translate",
    review otherwise -- NEVER a language-suffixed ``.ru.`` variant."""
    ext = "draft" if kind == "translate" else "review"
    return os.path.join(root, "segments", "%s.%s.json" % (seg, ext))


def _valid_disp(disp):
    return isinstance(disp, str) and disp not in (".", "..") and bool(_DISP_RE.fullmatch(disp))


def _silent_remove(path):
    if not path:
        return
    try:
        os.remove(path)
    except OSError:
        pass


def _ok(proc):
    # type: (subprocess.CompletedProcess | None) -> TypeGuard[subprocess.CompletedProcess]
    """A gate/companion subprocess result counts as success only on a real exit 0."""
    return proc is not None and proc.returncode == 0


def _stderr_text(proc):
    # type: (subprocess.CompletedProcess | None) -> "str | None"
    """#400: whatever stderr text a companion subprocess produced, or None if there is
    none to read (proc is None -- a timeout or spawn failure never produced a
    CompletedProcess at all -- or stderr is empty/whitespace-only). Never raises."""
    if proc is None:
        return None
    text = (getattr(proc, "stderr", None) or "").strip()
    return text or None


def _sha256_fd(fd):
    """Hash the CURRENT contents of an already-open fd (rewinds first). Never opens a
    path -- callers hold the identity via the fd itself."""
    h = hashlib.sha256()
    os.lseek(fd, 0, os.SEEK_SET)
    while True:
        chunk = os.read(fd, _COPY_CHUNK)
        if not chunk:
            break
        h.update(chunk)
    return h.hexdigest()


def _silent_unlinkat(dir_fd, name):
    try:
        os.unlink(name, dir_fd=dir_fd)
    except OSError:
        pass


class CodexJob:
    def __init__(self, kind, seg, tok, disp, root, companion, prompt_text, prompt_file,
                 deadline_sec, poll_sec, effort, node, model=None, plugin_root=None,
                 run_id=None):
        self.kind = kind
        self.seg = seg
        self.tok = tok
        self.disp = disp
        # #438: the CURRENT run's own id -- used ONLY to look up a claim record at
        # runs/<run_id>/.claimed.<seg> before a translate launch (see run()'s D8
        # guard below). main() makes --run-id REQUIRED and passes it straight
        # through (never derived from --expect-token -- see D8's own reasoning for
        # why deriving it would trade a loud failure for a silent one). None here
        # only ever happens when a caller constructs CodexJob() directly without
        # going through main() (every white-box test that predates #438) -- such a
        # caller never wrote a claim in the first place, so the D8 guard is a
        # no-op for it by construction, exactly like an omitted --plugin-root
        # reproduces this file's pre-#412 default.
        self.run_id = run_id
        self.root = os.path.realpath(root)
        self.companion = companion
        self.prompt_text = prompt_text
        self.prompt_file = prompt_file
        self.poll_sec = poll_sec
        self.effort = effort
        self.node = node
        self.model = model
        # #412: the plugin's own install root -- realpath'd at construction
        # (matching self.root's own treatment) so a symlink cannot be swapped
        # underneath an already-resolved trust decision. main() now resolves
        # this exactly once and passes the RESOLVED value here (never the
        # raw CLI string a second time -- re-resolving would open a TOCTOU
        # window between main()'s own validation and this call, benign under
        # this file's threat model since winning that race already requires
        # write access to the one tree #412 exists to keep out of codex's
        # reach, but pointless to carry once removing it is this cheap); the
        # realpath() call here stays for any OTHER caller (tests, a future
        # caller) that constructs a CodexJob directly with an unresolved
        # path -- idempotent, so a caller that already resolved it pays
        # nothing extra. Trust-boundary fix: "given" is tested as `is not
        # None` here, matching main()'s own definition exactly -- a bare
        # truthiness test (`if plugin_root`) used to treat an empty string
        # as "not given" too, silently falling back to SCRIPTS_DIR (the
        # codex-writable durable-root copy) even though main()'s own
        # pre-flight check had validated `os.path.realpath("")` (the
        # CURRENT WORKING DIRECTORY) as a real assets/scripts/ location --
        # an operator who passed the flag at all believed the redirect was
        # active. `None` (the flag genuinely omitted) is the only value
        # that reproduces the pre-#412 default; see _trusted_scripts_dir()'s
        # own docstring.
        self.plugin_root = os.path.realpath(plugin_root) if plugin_root is not None else None

        self.inv = os.urandom(8).hex()
        self.segdir = os.path.join(self.root, "segments")
        ext = "draft" if kind == "translate" else "review"
        self.canonical = canonical_path(self.root, seg, kind)
        self.attempt = os.path.join(self.segdir, ".att.%s.%s.%s.json" % (seg, self.inv, ext))
        self.pending = os.path.join(self.segdir, ".att_pending.%s.%s.json" % (seg, ext))
        # #429: the name a displaced pending occupant is PRESERVED under (see
        # _defer_attempt()). It carries `ext` and `seg` because hand recovery cannot read
        # identity out of the payload -- the candidate is UNVALIDATED at defer time, and
        # review.schema.json has no `seg` property at all -- and `inv` because a second
        # deferral of the same segment must not overwrite the first. It deliberately does
        # NOT end in `.draft.json`, and it is DOT-PREFIXED like every other private entry
        # the driver writes here. The dot is what actually excludes it from both dispatch
        # scans: since #428 they skip the whole dot-prefixed namespace rather than testing
        # the suffix, which is the stronger property and the one to preserve if this name
        # ever changes. Avoiding the suffix is belt-and-braces on top of that.
        # The field order DIVERGES from self.attempt and self.pending, which both lead with
        # `seg`, and that is deliberate rather than an oversight: leading with `ext` is what
        # leaves `inv` last, so the name ends in hex and cannot read as a draft to the one
        # consumer NEITHER the dot skip nor any suffix rule binds -- fixPrompt()'s
        # natural-language census of segments/. Reordering it to match the siblings would
        # undo that.
        self.superseded = os.path.join(
            self.segdir, ".att_superseded.%s.%s.%s" % (ext, seg, self.inv))
        self.lock = os.path.join(self.segdir, ".codex_job.%s.lock" % seg)
        self.joblog = os.path.join(self.segdir, ".codex_job.%s.json" % seg)
        self.fail_sentinel = os.path.join(self.segdir, ".codex_failed.%s.%s" % (seg, disp))
        self.final_prompt = None
        # #398: "validate_draft.py ran on a TRANSLATE candidate and returned exit 1" -- its
        # contract for "the candidate's own content is defective" (see that script's own Exit
        # codes section). Two setters, both reading that one contract: _validate_candidate()
        # for a FRESH attempt (#398) and adopt_pending() for a DEFERRED one (#665). Initialized
        # once and deliberately never reset: each of those runs at most once per run(), and
        # run() returns at the first of them to fire, so a per-call reset would be choreography
        # for a reuse nothing performs.
        self.translate_content_rejected = False
        # #398: best-effort outcome of the terminal ledger write, surfaced in the terminal
        # joblog. None means "never attempted" -- the ordinary case for every job that did not
        # end in a content rejection.
        self.ledger_write = None
        # Per-invocation write-isolated sandbox (#409) -- set by _setup_sandbox(), never in
        # __init__ (creating it is real filesystem I/O, not pure state setup).
        self.sandbox_dir = None
        self.sandbox_attempt = None

        # Two hard, absolute time ceilings, fixed at construction (step 1).
        now = time.monotonic()
        self.poll_deadline = now + deadline_sec
        self.abs_ceiling = self.poll_deadline + CODEX_FINALIZE_BUDGET_SEC

        # Outcome state (also consumed by finalize()).
        self.ok = False
        self.promoted = False
        self.adopted = False
        self.timed_out = False
        self.holds_lock = False
        self.jobId = None
        self.job_status = None
        self.reason = None
        # #398/#400: the ONE piece of free-text diagnostic detail this driver ever
        # captures, from whichever of two sources actually produced one -- the
        # companion job store's own `errorMessage` (poll(), set when codex-companion's
        # tracked-job runner caught an exception -- e.g. an API/quota error -- and
        # persisted its message) or the `task` LAUNCH subprocess's own stderr
        # (launch(), set when the launch invocation itself failed to even queue a job).
        # Never both at once in practice (a launch that never queued a job has nothing
        # for poll() to later overwrite this with). See poll()/launch() for exactly
        # when each is set.
        self.error_detail = None
        # #399: the gate that refused to ADOPT a pre-existing canonical (safe_adopt()),
        # with that gate's own output. A DEDICATED field rather than error_detail, for
        # two reasons that are both about the record staying true:
        #   * error_detail is last-writer-wins and safe_adopt() runs FIRST -- every one
        #     of _refuse_claimed_translate(), adopt_pending(), launch() and poll() can
        #     still write it afterwards, so an adoption refusal parked there would be
        #     destroyed on exactly the runs an operator is trying to diagnose;
        #   * a refused adoption is not an error of THIS run. The run continues and can
        #     finish ok -- and segment_dispatch_driver._codex_job_outcome() relays
        #     error_detail into its `codex_dispatch_finished` journal entry
        #     unconditionally, success included, so riding error_detail would label a
        #     successful dispatch with an error text.
        # Written once, by one method, and read by nothing in-process.
        self.adopt_rejection = None
        # Set when a canonical-unreadable refusal (see _canonical_replaceable()) blocks a
        # promote. A DEDICATED flag, not inferred from self.reason: self.reason is
        # reassigned by whatever this run does NEXT (e.g. a later launch-failed), but
        # finalize()'s decision to KEEP self.attempt on disk must survive that
        # reassignment -- a string comparison there would stop protecting the file the
        # moment anything downstream narrates a different outcome.
        self.canonical_unreadable = False

    # ---- time helpers (FLOAT, no floor) -------------------------------------
    def poll_remaining(self):
        return self.poll_deadline - time.monotonic()

    def abs_remaining(self):
        return self.abs_ceiling - time.monotonic()

    def poll_timeout(self):
        return max(0.0, min(PER_CALL_CAP, self.poll_remaining()))

    def finalize_timeout(self):
        # Reserve FINALIZE_TAIL so the non-subprocess finalize always completes.
        return max(0.0, min(PER_CALL_CAP, self.abs_remaining() - FINALIZE_TAIL))

    # ---- subprocess runner (monkeypatched in white-box tests) ---------------
    def _run(self, argv, timeout):
        # type: (list, float) -> "subprocess.CompletedProcess | None"
        """Run a bounded subprocess with cwd=<root>. Returns the CompletedProcess, or
        None on a skip (timeout <= 0), a timeout expiry, or a spawn failure."""
        if timeout is None or timeout <= 0:
            return None
        try:
            return subprocess.run(argv, cwd=self.root, capture_output=True,
                                  text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return None
        except (OSError, ValueError):
            return None

    # TWO INDEPENDENT SEAMS (lane A's relayed contract) -- never collapse them, even
    # though both CAN resolve to the SAME durable_root-derived value:
    #   - _durable_root_args(): the DATA root a gate script should read segments/,
    #     schemas/, canon.json, etc. from -- confirmed contract: `--durable-root PATH`,
    #     optional, byte-identical to self-anchored behavior when omitted.
    #   - _trusted_scripts_dir(): the TRUSTED location _gate() itself resolves gate
    #     EXECUTABLES from. WITHOUT --plugin-root this is still SCRIPTS_DIR
    #     (codex_job.py's own directory), which in production IS the durable-root
    #     copy Step 0a makes -- the SAME vulnerability class #409 exists to close,
    #     just on the driver's own gate-invocation path rather than codex's.
    #
    #     DO NOT read _setup_sandbox() as closing this by itself. The sandbox confines
    #     only the codex processes THIS driver launches. Other shipped passes still
    #     hand codex write access over the whole durable root -- the glossary and
    #     skeptic passes dispatch `agentType: "codex:codex-rescue"` whose job is to
    #     WRITE a fragment under ${durable_root}/..., and the sanctioned manual W5
    #     drive launches codex-companion with `--write` and cwd = durable_root.
    #     ${durable_root}/scripts/ sits inside every one of those write roots, and the
    #     glossary pass runs BEFORE W5 in the same project, so a gate script tampered
    #     with there is exactly what _gate() would execute later.
    #
    #     #412: CLOSED, but opt-in, not forced. `--plugin-root PATH` (threaded
    #     resume_setup.py -> the `{{PLUGIN_ROOT}}` template token -> this driver's own
    #     argparse -- see mass-translate-wf.template.js's header token doc for the
    #     exact substitution shape) redirects _trusted_scripts_dir() to
    #     `{plugin_root}/assets/scripts/`, the SAME layout SKILL.md's never-copied
    #     plugin-path scripts already use (profile_validate.py, validate_extraction.py,
    #     glossary_preflight.py -- resolve_codex_companion.py was a fourth exclusion
    #     here once, but is now copied, with its own migration handling; see SKILL.md's
    #     own note on why the exclusion was dropped) -- a location the codex
    #     process this driver launches cannot write to. Omitting --plugin-root
    #     reproduces the pre-#412 vulnerability unchanged (byte-identical default);
    #     closing it for a given dispatch requires the ORCHESTRATING SESSION to
    #     actually pass the flag, which is outside this file's own scope. Moving the
    #     redirect destination required draft_ready.py and validate_draft.py to adopt
    #     --durable-root FIRST (both were __file__-anchored at parents[1] with no root
    #     flag, so they would otherwise start looking for segments/ inside the plugin
    #     once resolved from there) -- landed, see _DURABLE_ROOT_CONTRACT_SCRIPTS below.
    # #398 adds ledger_update.py: _record_translate_rejected() invokes it through the same
    # _gate() path, and without membership here it would receive no --durable-root and
    # self-anchor to its own installation tree (ledger_update.py's resolve_dirs()) -- writing
    # the fragment under the PLUGIN root on any launch that passes --plugin-root, i.e. exactly
    # the production shape. It has accepted --durable-root since #409.
    _DURABLE_ROOT_CONTRACT_SCRIPTS = frozenset({"review_ready.py", "draft_ready.py",
                                                "validate_draft.py", "ledger_update.py"})

    def _durable_root_args(self, script_name):
        """`--durable-root <resolved self.root>` for scripts confirmed under lane A's
        contract; [] for everything else (an un-adopted script would error on an
        unrecognized flag -- never pass it speculatively). self.root is already
        os.path.realpath()'d at construction, matching the contract's own
        `Path(PATH).resolve()` expectation."""
        if script_name in self._DURABLE_ROOT_CONTRACT_SCRIPTS:
            return ["--durable-root", self.root]
        return []

    def _trusted_scripts_dir(self):
        """Where _gate() resolves gate EXECUTABLES from -- see the seam note above for
        the full rationale. #412: when self.plugin_root is given, returns
        `{plugin_root}/assets/scripts/` -- a location the codex process this driver
        launches cannot write to, unlike SCRIPTS_DIR. Falls back to today's
        byte-identical default (SCRIPTS_DIR, never a value derived from self.root) when
        self.plugin_root is None -- the data root must not be able to decide which
        executable validates it, and omitting --plugin-root must reproduce today's
        exact behavior."""
        if self.plugin_root:
            return os.path.join(self.plugin_root, "assets", "scripts")
        return SCRIPTS_DIR

    def _gate(self, args, timeout):
        script = os.path.join(self._trusted_scripts_dir(), args[0])
        argv = [sys.executable, script] + list(args[1:]) + self._durable_root_args(args[0])
        return self._run(argv, timeout)

    # ---- shared regular-file / candidate-gate helpers (#213) ----------------
    def _is_regular(self, path, remaining_fn):
        """O_NOFOLLOW|O_NONBLOCK open + S_ISREG + a confirmed read: reject a symlink,
        FIFO, dir, or absent file, and confirm the descriptor is actually READABLE, not
        merely open-able.

        fstat() and close() are guarded the same way open() already was: an OSError from
        either -- a stale file handle or a transient I/O error on a network/FUSE
        filesystem, both real even though open() just succeeded -- used to propagate
        straight out of this method uncaught, past every caller's own "False means do not
        proceed" check. Every call site here already treats a bare False as "refuse", so
        there was never a wrong ANSWER to correct, only a code path that could raise
        instead of answering at all.

        open()+fstat() succeeding only proves the entry EXISTS and is regular -- neither
        one actually reads a byte (main's own version of this check stops at exactly
        open()+fstat()+S_ISREG, so it never confirms the descriptor is actually
        READABLE). On a network/FUSE filesystem or damaged storage, the metadata calls
        can both succeed while a real read still returns EIO/ESTALE -- exactly the
        failure this method exists to catch, slipping through both open() and fstat().
        Draining the descriptor to EOF, in the SAME try/except as fstat() (one answer
        for "could not stat it" and "could not read it", not two), closes that: every
        byte the promote is about to discard gets read at least once before this method
        calls the file trustworthy. A single read is not enough either -- a regular file
        can serve a good prefix and then fail on a later page or extent, so only
        draining the WHOLE file catches a later read failure (see
        test_is_regular_false_when_a_later_read_fails_after_a_successful_prefix).

        The unbounded drain above is a partial defect on its own: every call site here
        runs AFTER this process holds the per-segment flock lease, so a huge or growing
        file does not just cost time, it wedges every cooperating retry for that segment
        behind a lease nothing releases. Two independently necessary bounds cover that:
          - HUGE: st.st_size is already in hand from fstat() above -- checked BEFORE
            reading a single byte, so an oversized file costs one comparison, not one
            attempted full read.
          - GROWING: st_size is a snapshot at fstat() time and could be stale by the
            time the drain loop runs -- a running byte counter backstops it, refusing
            the instant the ACTUAL bytes read exceed the cap, regardless of what
            st_size claimed.
        An over-cap file REFUSES (returns False), the same direction every other
        uncertain outcome in this method already goes: destroying an oversized canonical
        because this method gave up checking it would be wrong in the same way trusting
        an unread file would be, just at the opposite end -- a legitimately huge
        canonical becomes unpromotable rather than accepted unread, and that is
        deliberate.

        A STALLED file -- one whose os.read() call never returns at all, a hung
        network/FUSE mount; O_NONBLOCK has NO EFFECT on a regular file's read() -- is NOT
        bounded here. See the module-level comment above _MAX_REGULAR_READ_BYTES for the
        full reasoning: a stall is a KNOWN, ACCEPTED limit shared across all three
        structurally identical read loops in this file (_sha256_fd(),
        _publish_from_sandbox(), and this one), not a defect specific to this method --
        closing it for real would mean bounding all three consistently, a larger,
        deliberate change this method does not make on its own.

        Cost, normally: ONE full read of the canonical per promote attempt (not per
        loop iteration elsewhere) -- real canonical drafts run tens to a couple hundred
        KB, so a handful of _COPY_CHUNK-sized calls, not a meaningful cost against a
        paid codex turn. An EMPTY regular file drains to b"" on the FIRST read --
        falsy, but NOT an error and NOT a failure of this check: os.read() only raises
        on a real I/O failure, so a zero-length canonical still correctly answers True
        here. Do not "fix" a falsy b"" into a rejection; that would refuse every
        legitimately empty file.

        `remaining_fn`: a zero-arg callable returning the CALLER's own current
        remaining-seconds budget for ITS phase (self.poll_remaining for a poll-window
        operation, self.finalize_timeout to leave FINALIZE_TAIL for the non-subprocess
        finalize() that follows, self.abs_remaining for a caller with nothing further to
        reserve) -- never a value read once and reused, and never this method's own
        substitute for one. Sharing this job's WHOLE abs_remaining() ceiling across every
        caller regardless of phase would let a poll-window operation (adopt_pending())
        eat the 150s finalize budget while holding the lease, and let attempt validation
        consume FINALIZE_TAIL -- reserved for the non-subprocess finalize()
        (stdout/sentinel/joblog) -- instead of leaving it alone. `remaining_fn` is
        re-invoked fresh at EVERY check below, per-read and after EOF, because real
        wall-clock time passes between drain-loop iterations and a stale snapshot taken
        once would silently re-introduce the same overrun for a caller whose phase budget
        shrinks as this method runs."""
        try:
            fd = os.open(path, os.O_RDONLY | _O_NOFOLLOW | _O_NONBLOCK | _O_CLOEXEC)
        except OSError:
            return False
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode):
                return False
            if st.st_size > _MAX_REGULAR_READ_BYTES:
                return False  # HUGE, per the fstat() snapshot -- refuse before reading
            total_read = 0
            while True:
                # Checked BEFORE every read, using the CALLER's own phase-specific
                # remaining_fn() -- not this job's whole abs_remaining() ceiling -- so a
                # poll-window caller cannot eat the finalize budget, and validation cannot
                # eat FINALIZE_TAIL. Re-invoked fresh each iteration (never a value read
                # once and reused): real wall-clock time passes between reads, and a stale
                # snapshot would silently re-introduce the same overrun for a caller whose
                # phase budget shrinks as this runs.
                if remaining_fn() <= 0:
                    return False  # SLOW: the CALLER's own phase budget, exhausted
                chunk = os.read(fd, _COPY_CHUNK)
                if not chunk:
                    break  # EOF -- b"" ends the loop, not an error
                total_read += len(chunk)
                if total_read > _MAX_REGULAR_READ_BYTES:
                    return False  # GROWING: actual bytes read outran the fstat() snapshot
        except OSError:
            return False  # covers fstat()'s own failure AND a real read failure --
            # one answer for both, not two exception classes (open()'s own OSError has
            # its own handler above)
        finally:
            try:
                os.close(fd)
            except OSError:
                pass
        # Reaching EOF itself takes real wall-clock time (the read that returns b""
        # still has to complete), so a file that consumed the ENTIRE phase budget
        # confirming EOF must not be reported trustworthy just because every byte was
        # eventually read; re-checking here catches that even though the loop's own
        # per-iteration check never runs again after a break.
        if remaining_fn() <= 0:
            return False
        return True

    def _canonical_replaceable(self, remaining_fn):
        """True iff an os.replace() landing on self.canonical is safe to perform RIGHT NOW:
        either there is no directory entry there at all (an ordinary first promotion,
        nothing to destroy), or an entry IS there and this call just confirmed it is a
        regular file.

        `remaining_fn`: consulted directly for the ENOENT (absent) case below, and threaded
        through to _is_regular()'s own read for the regular-file case -- see that method's
        docstring for what a caller must pass and why it must be phase-specific, not this
        job's whole abs_remaining() ceiling.

        os.replace() only needs WRITE permission on the CONTAINING DIRECTORY, not on the
        target itself -- so an unreadable regular file, or a symlink whose target vanished,
        is fully replaceable at the OS level. Both write sites this check guards
        (adopt_pending()'s os.replace(self.pending, self.canonical) and run()'s own
        promote step, os.replace(self.attempt, self.canonical)) used to call os.replace()
        directly, with no check at all: if self.canonical had ever become unreadable --
        permissions changed underneath it, a transient I/O error, a dangling symlink left
        by a crashed writer -- the promote would silently destroy whatever was there, and
        nothing had ever actually read those bytes to know they were safe to discard. Both
        sites run while this process holds the per-seg flock lease (acquired in run(),
        before either can be reached) -- the only place the observation this method makes
        and the mutation that follows it share one concurrency boundary.

        os.lstat() (never follows a symlink) is what makes "no entry at all" and "an entry
        exists but cannot be read" distinguishable, PROVIDED the exception handling below
        treats ONLY FileNotFoundError as absence. Any OTHER OSError -- EACCES, a transient
        EIO, ENOTDIR, a self-referential symlink -- means the lookup FAILED, not that it
        found nothing; treating a failed lookup the same as a genuinely empty one is
        exactly the mistake that would license the destruction this method exists to
        prevent. A dangling symlink's *read* raises the SAME FileNotFoundError a truly
        absent path raises, and os.path.exists() (which follows the link) reports False
        for both too -- either check alone would treat "someone's symlink lost its target"
        the same as "nothing here, go ahead". A symlink at the canonical path is refused
        UNCONDITIONALLY, dangling or not: confirming a symlink's TARGET and replacing the
        LINK are different operations, and os.replace() replaces the link."""
        try:
            os.lstat(self.canonical)
        except FileNotFoundError:
            # ENOENT only: no directory entry at all -- nothing to destroy. Still
            # consult the CALLER's own phase deadline before saying yes: an absent
            # canonical is otherwise promotable regardless of remaining_fn(), so a
            # caller whose phase budget is already exhausted would get a bare `True`
            # here and then spend its os.replace() anyway, on the strength of a
            # deadline this method never actually checked.
            return remaining_fn() > 0
        except OSError as exc:
            # A lookup that merely FAILED must read as "present", not absent -- see the
            # docstring above. State is set FIRST, and the diagnostic write that follows
            # is wrapped so no failure of the write itself can prevent this refusal from
            # taking effect: if fd 2 is closed (making sys.stderr None) or the stream
            # itself is already closed, sys.stderr.write() can raise AttributeError or
            # ValueError -- not just OSError -- and either would otherwise propagate out
            # of this method, skip `return False` below, and leave the caller's own
            # protective state unset, turning a refusal into a silent promote.
            self.error_detail = "canonical unreadable: %s: %s" % (self.canonical, exc)
            try:
                sys.stderr.write(
                    "codex_job.py: could not stat %s: %s -- treating it as present, "
                    "refusing to replace it\n" % (self.canonical, exc))
            except Exception:
                pass
            return False
        return self._is_regular(self.canonical, remaining_fn)

    def _clear_nonregular(self, path):
        """Remove a NON-REGULAR entry squatting on a deterministic driver slot so it cannot
        permanently block a promote into that slot (#213). A regular file is LEFT untouched
        (callers overwrite it via os.replace, or delete it via _silent_remove). lstat
        (never follows) classifies: a symlink/FIFO/socket is unlinked as the entry itself; a
        real directory is removed recursively (the slot is never legitimately a directory).
        Best-effort.

        KNOWN, ACCEPTED LIMIT, inherited unchanged from before this release (this shape is
        present verbatim on main): the classify (lstat) and the destroy (remove/rmtree) are
        two separate syscalls, not one atomic operation. A non-cooperating writer -- the flock
        only serialises COOPERATING codex_job.py processes, per this file's own module
        docstring -- could replace a genuinely non-regular squatter with a VALIDATED REGULAR
        FILE in the window between them, and the destroy would fire on whatever occupies
        `path` AT THAT MOMENT, not on what was classified moments earlier. It needs a racing
        writer to reach at all, and real machinery to close it (an atomic rename-to-quarantine
        step: a second mutating syscall plus a private, per-invocation name), which this
        release does not build. Disclosed instead, alongside the other instances of this same
        threat model in this release's own "Known limits" section. NOTE for whoever changes
        this: that section also describes this helper by its call sites -- grep for
        `_clear_nonregular(` rather than trust a hand-maintained count anywhere, including
        this note, before touching either that section or a caller of this method."""
        try:
            st = os.lstat(path)
        except OSError:
            return
        if stat.S_ISREG(st.st_mode):
            return
        try:
            if stat.S_ISDIR(st.st_mode):
                shutil.rmtree(path)
            else:
                os.remove(path)
        except OSError:
            pass

    def _prev_review_slot(self, label):
        """segments/.prev_review.<seg>.r<label>.json -- where the OUTGOING review verdict
        at that round label is kept once a newer one takes the canonical. Dot-prefixed,
        alongside this file's other private staging entries (.att.*, .att_pending.*,
        .codex_job.*). Both dispatch-evidence scans over segments/ skip the dot-prefixed
        namespace outright since #428 -- select_segments.py and
        backfill_resume_gate_ack.py each drop a leading-dot entry before any suffix test
        -- and a canonical {seg}.draft.json can never be dot-prefixed, the seg id being
        `(?:FRONTBACK:)?[A-Za-z0-9_]+`. So this name is invisible to them by the same
        rule that already covers the staging entries beside it."""
        return os.path.join(self.segdir, ".prev_review.%s.r%s.json" % (self.seg, label))

    def _archive_outgoing_review(self, remaining_fn):
        """#541: preserve the review verdict this promote is about to destroy, so the NEXT
        round's fix turn can see that a locus was already contested. Called immediately
        before every os.replace() landing on self.canonical, on REVIEW jobs only.

        WHAT THIS RECORD IS, and the property everything else rests on: it is CONTEXT, never
        authority. Every verdict at one round label mints an identical dispatch_token
        (review_dispatch_token() is a pure function of run/seg/label -- see
        reject_review.py's own docstring), so NO record here can prove it is the exact
        instance that preceded the current round. A design needing that proof would need
        unique per-promotion candidates or a digest binding, and every failure of that
        machinery would become a wrong edit. This one refuses the premise: fixPrompt() is
        told the record authorizes nothing, and every change the fix turn makes still stands
        on its own substantiation against the source. A superseded, foreign or absent record
        therefore costs at most a moment of extra scrutiny.

        KEYED BY THE OUTGOING VERDICT'S OWN LABEL, never a single slot and never the incoming
        label: a same-label promote is reachable (a same-run retranslate invalidates
        review_ready.py's draft-freshness check, so safe_adopt() refuses and a fresh attempt
        promotes at the SAME label), and either of those keyings would let it overwrite the
        genuine predecessor with a same-label verdict. Only a NUMERIC label is kept -- no fix
        turn can ever consume an `rfinal` verdict, since neither drive path dispatches a fix
        on the mandatory final round.

        REMOVE FIRST, THEN WRITE. A slot's own token cannot tell a superseded verdict at one
        label from the one that replaced it, so refreshing in place would leave a plausible,
        token-valid, WRONG body behind on a failed write. Unlinking first makes an ordinary
        write failure degrade to ABSENCE, which fixPrompt() already handles as the ordinary
        case. A failing unlink can still leave a superseded body -- accepted, not engineered
        away, because of the context-not-authority property above.

        BEST-EFFORT AND NEVER A GATE: this returns None on every path, raises nothing, and
        touches no field finalize() reads. self.promoted / self.adopted / self.reason / the
        stdout line / the fail sentinel / the joblog are all unaffected by anything here.

        `remaining_fn` is the CALLER's own phase-specific remaining-seconds callable, the
        same one _is_regular() takes and for the same reason: this runs after the caller has
        taken the per-segment flock lease and BEFORE the authoritative os.replace, so
        unbounded advisory I/O here would delay the promote, the stdout line and every
        cooperating retry behind a lease nothing releases. An exhausted budget abandons the
        copy, never leaving a stale body behind: spent before the unlink -- which the
        read's own post-EOF check is there to catch, since the read that returns EOF can
        itself spend the last of it -- the existing record survives untouched; spent
        after, the slot is absent, which fixPrompt() already handles as the ordinary
        case."""
        try:
            if self.kind == "translate":
                return
            data = self._read_regular_bounded(self.canonical, remaining_fn)
            if data is None:                # absent, non-regular, oversized, unreadable,
                return                      # or out of budget
            try:
                obj = json.loads(data.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                return
            if not isinstance(obj, dict):
                return
            tok = obj.get("dispatch_token")
            if not isinstance(tok, str):
                return
            # RUN_ID:seg:r<label>, split from the ENDS rather than by counting colons: a
            # seg id may legitimately be a FRONTBACK:{id} unit (see the segment-id contract
            # above), so a token for one carries FOUR colon-separated pieces and a
            # three-part check would silently exclude that whole segment class.
            run, _, rest = tok.partition(":")
            middle, _, label = rest.rpartition(":")
            # The seg must be THIS segment -- an artifact naming another one is not this
            # segment's predecessor whatever else it is. That one comparison also rejects
            # every shape whose middle piece comes out empty because a colon was missing
            # at either end, since a seg id never is; only a leading-colon token, whose
            # run piece is empty while its middle still matches, needs its own conjunct.
            if not run or middle != self.seg:
                return
            # Rounds are minted as 1, 2, 3 ... -- never zero, never zero-padded (the
            # template's own loop starts at 1 and the driver's _next_round_label()
            # increments). Admitting "r0" or "r01" would key a slot under a label no
            # consumer ever asks for, which is a silently unreachable record rather
            # than a useful one.
            # ASCII digits only: str.isdigit() alone admits superscript aliases.
            m = re.fullmatch(r"r([1-9][0-9]*)", label)
            if m is None:
                return                      # "rfinal", or anything not a minted round
            slot = self._prev_review_slot(m.group(1))
            tmp = os.path.join(self.segdir, ".prev_review_tmp.%s.%s.json" % (self.seg, self.inv))
            _silent_remove(slot)            # absence beats a stale body -- see above
            flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | _O_CLOEXEC | _O_NOFOLLOW
            try:
                fd = os.open(tmp, flags, 0o600)
            except OSError:
                return
            try:
                written = os.write(fd, data)
            finally:
                os.close(fd)
            if written != len(data):
                # POSIX write() may write fewer bytes than asked. Publishing a truncated
                # body is worse than publishing nothing, and a zero-length return -- a
                # wedged descriptor -- lands here too rather than in a loop that would
                # spin against it while holding the lease. Same guard _write_joblog()
                # and _publish_from_sandbox() apply to their own tmp-file writes.
                _silent_remove(tmp)
                return
            try:
                os.replace(tmp, slot)
            except OSError:
                _silent_remove(tmp)
        except Exception:                   # noqa: BLE001 -- an advisory copy may never
            return                          # break a promote, whatever it hit

    def _read_regular_bounded(self, path, remaining_fn):
        """The whole content of `path` as bytes, or None if it is absent, not a regular
        file, larger than _MAX_REGULAR_READ_BYTES, unreadable, or if the caller's own
        phase budget ran out mid-read. O_NOFOLLOW: a symlink that appeared at a
        deterministic slot is refused rather than followed. `remaining_fn` is re-invoked
        fresh at every check rather than snapshotted once, for the reason _is_regular()'s
        own docstring gives: real wall-clock time passes between iterations. Never
        raises."""
        fd = None
        try:
            fd = os.open(path, os.O_RDONLY | _O_NOFOLLOW | _O_NONBLOCK | _O_CLOEXEC)
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode):
                return None
            if st.st_size > _MAX_REGULAR_READ_BYTES:
                return None
            buf = bytearray()
            while True:
                if remaining_fn() <= 0:
                    return None             # SLOW: the caller's own phase budget, spent
                chunk = os.read(fd, _COPY_CHUNK)
                if not chunk:
                    break
                buf.extend(chunk)
                if len(buf) > _MAX_REGULAR_READ_BYTES:
                    return None             # GROWING past the fstat() snapshot
            if remaining_fn() <= 0:
                # The read that returned EOF may itself have spent the last of the
                # budget. Returning the payload here would let the CALLER go on to
                # decode it, unlink the existing slot and publish a replacement --
                # all under the per-segment flock lease the budget exists to bound,
                # and all after this advisory copy stopped being affordable. Failing
                # here instead leaves the genuine predecessor in place, which is the
                # better of the two absences.
                return None
            return bytes(buf)
        except OSError:
            return None
        finally:
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass

    # #399: a gate that RAN and REJECTED an attempt currently discards both its
    # own diagnostic output and the rejected attempt itself (finalize()
    # _silent_remove()s self.attempt when not promoted) -- so diagnosing WHY a
    # rejection happened requires re-running the whole translation. Capture the
    # rejecting gate's own combined stdout+stderr into self.error_detail
    # (reusing the #400 plumbing) instead.
    _GATE_OUTPUT_CAP = 4000  # chars; this text lands in the durable joblog, so it must stay bounded

    def _gate_rejection_text(self, gate_name, proc):
        """`proc` is the CompletedProcess of a gate call that just REJECTED
        (returncode != 0) -- never a None proc (a gate that could not even run
        has nothing to capture; _ok(None) is already False and the caller
        never reaches here for that case). This plugin's gate scripts are NOT
        uniform about which stream carries the diagnostic text (see each
        script's own docstring), so both stdout and stderr are captured
        rather than guessing one. Truncated to _GATE_OUTPUT_CAP chars with an
        explicit marker naming the exact bound -- never left unbounded.

        Returns the "<gate>: <output>" string, or None when the gate printed
        NOTHING on either stream: there is no diagnostic to record, and a
        caller must treat that as "nothing to say" rather than as a value.
        Split out of _capture_gate_rejection() (#399) so safe_adopt() can put
        the same text in its OWN field without touching error_detail."""
        out = getattr(proc, "stdout", None) or ""
        err = getattr(proc, "stderr", None) or ""
        combined = (out + (("\n" + err) if err else "")).strip()
        if not combined:
            return None
        if len(combined) > self._GATE_OUTPUT_CAP:
            combined = combined[: self._GATE_OUTPUT_CAP] + (
                "... [truncated at %d chars]" % self._GATE_OUTPUT_CAP
            )
        return "%s: %s" % (gate_name, combined)

    def _capture_gate_rejection(self, gate_name, proc):
        """Record a rejecting gate's own output in self.error_detail. A gate that
        printed nothing leaves error_detail EXACTLY as it was -- never cleared:
        this method can run after another stage already wrote a real diagnostic
        there (adopt_pending() rejects a pending, run() launches fresh, and the
        fresh attempt is then rejected by a silent gate), and overwriting that
        with None would destroy the one record the operator has."""
        detail = self._gate_rejection_text(gate_name, proc)
        if detail is not None:
            self.error_detail = detail

    # #398. A FIXED bound, never a remaining-budget function: by the time a content
    # rejection is known, finalize_timeout() can legitimately be 0.0, and _run() SKIPS a
    # subprocess whose timeout is non-positive -- silently turning a genuine rejection into
    # no write at all. 60s is the same bound segment_dispatch_driver.py already uses for its
    # own direct ledger_update.py calls.
    #
    # This DELIBERATELY overrides this class's internal ceiling. run() enters validation with
    # only abs_remaining() > FINALIZE_TAIL, so in the worst case this write runs past the
    # nominal absolute deadline and holds the per-segment flock until finalize() returns. That
    # margin is taken from the 600s outer grace BOTH launchers allow (the driver's backstop and
    # the template's own wait bound) -- it is margin, not a guarantee that the sandbox teardown,
    # fail sentinel and joblog writes that follow always complete.
    _LEDGER_WRITE_TIMEOUT_SEC = 60

    def _record_translate_rejected(self):
        """#398: write the TERMINAL ledger fragment for a translate candidate the content gate
        rejected, so neither dispatch path auto-redispatches it unchanged.

        Why HERE and not in either caller: this is the only component both dispatch paths
        share. The Workflow template has no filesystem access at all and launches this script
        with stdout discarded, and segment_dispatch_driver.py sees only the `reason` string,
        which conflates a content rejection with a sandbox-publish failure, a non-regular
        attempt file and a gate that could not run. The exit code this method acts on is
        visible in exactly one place: here.

        `blocked` is the project's existing "an operator must look at this" status --
        select_segments.py maps it to human_escalation and drops it from the default dispatch
        set, while --only-segs still reaches it, exactly as the workflow's own
        blocked/draft-missing fragment is retried.

        BEST EFFORT, and load-bearing that it stays so: this runs inside run(), whose outcome
        (exit code, stdout line, reason) must not change because a bookkeeping write failed.
        Every exception is caught here; nothing propagates. A failed write leaves the segment
        in exactly its pre-#398 state -- recoverable, re-dispatched next run -- which is a
        return to the old behaviour, never a new failure mode.

        The payload file is dot-prefixed, so it sits inside the namespace every segments/ scan
        already excludes and is inert if the unlink below never runs. NO `detail`/`error_detail`
        key: ledger_update.py validates the payload against a schema derived with
        additionalProperties:false over ledger-record-base.schema.json, which declares no such
        field, and would REFUSE the write outright. The rejecting gate's own output is already
        carried into the terminal joblog via self.error_detail."""
        payload_path = os.path.join(
            self.segdir, ".codex_ledger_payload.%s.%s.json" % (self.seg, self.inv))
        try:
            # O_EXCL|O_NOFOLLOW|O_NONBLOCK, not a plain open(..., "w"): this file is
            # created inside segments/, a directory the codex process this driver launches
            # holds write access over, and a straggler turn can outlive poll()'s
            # best-effort cancel (see finalize()'s own comment). A plain truncating open
            # would FOLLOW a symlink planted at this exact name, and would BLOCK
            # indefinitely on a FIFO -- before _gate()'s timeout starts, so the bound
            # below would not cover it, in a method whose entire contract is that it
            # cannot disturb the job. The name already carries a fresh per-invocation
            # random component, so O_EXCL cannot collide with this driver's own work.
            fd = os.open(payload_path,
                         os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_NONBLOCK,
                         0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump({"status": "blocked", "reason": "translate-rejected"}, fh)
            proc = self._gate(["ledger_update.py", self.seg, "--payload-file", payload_path],
                              self._LEDGER_WRITE_TIMEOUT_SEC)
            if proc is None:
                # A skip, a spawn failure, or a timeout expiry. ledger_update.py commits at an
                # os.replace() and can still fail AFTER that, so neither this nor a non-zero
                # exit proves the fragment was not written -- report it as unconfirmed rather
                # than claiming a failure this process cannot establish.
                self.ledger_write = "not-confirmed: ledger_update.py did not complete"
            elif proc.returncode != 0:
                # _gate_rejection_text() (#399), not a second combine of the same two
                # streams: this plugin's scripts are not uniform about which stream carries
                # the diagnostic, and ledger_update.py in particular prints its structured
                # error to STDOUT while only a sentinel WARNING goes to stderr -- so a
                # stderr-first read of it records the warning and drops the error. It
                # returns None when the writer printed nothing at all.
                self.ledger_write = ("not-confirmed: ledger_update.py exit %d: %s"
                                     % (proc.returncode,
                                        self._gate_rejection_text("ledger_update.py", proc) or ""))
            else:
                self.ledger_write = "ok"
        except Exception as exc:  # noqa: BLE001 -- see this method's own docstring
            self.ledger_write = ("failed: %r" % (exc,))[: self._GATE_OUTPUT_CAP]
        finally:
            _silent_remove(payload_path)

    def _validate_candidate(self, candidate, timeout_fn):
        """Kind-specific candidate-file gate against `candidate`; each gate call is bounded by a
        FRESH timeout_fn() (remaining budget re-read per call). Returns True iff every gate PASSED
        (an _ok()-True). Used by validate_attempt (attempt path). #399: on a REJECTING gate (ran,
        returned non-zero), captures that gate's own output via _capture_gate_rejection() before
        returning False -- a gate that could not even run (proc is None) has nothing to capture."""
        if self.kind == "translate":
            proc = self._gate(["draft_ready.py", self.seg, "--expect-token", self.tok,
                               "--candidate-file", candidate], timeout_fn())
            if not _ok(proc):
                if proc is not None:
                    self._capture_gate_rejection("draft_ready.py", proc)
                return False
            proc = self._gate(["validate_draft.py", self.seg, "--candidate-file", candidate],
                             timeout_fn())
            if not _ok(proc):
                if proc is not None:
                    self._capture_gate_rejection("validate_draft.py", proc)
                    if proc.returncode == 1:
                        # #398: exit 1 -- and ONLY exit 1 -- is validate_draft.py's verdict on
                        # the CANDIDATE's own content, a condition re-running the identical
                        # translation cannot clear. Exit 2 is usage/environment/source
                        # availability (a missing segpack, an unreadable profile.yml, an
                        # internal error), which must stay recoverable; so must a gate that
                        # could not run at all, which is the proc-is-None branch above.
                        self.translate_content_rejected = True
                return False
            return True
        proc = self._gate(["review_ready.py", self.seg, "--expect-token", self.tok,
                          "--candidate-file", candidate], timeout_fn())
        if not _ok(proc):
            if proc is not None:
                self._capture_gate_rejection("review_ready.py", proc)
            return False
        return True

    # ---- step 2: write-isolated sandbox (#409) -------------------------------
    # Outcomes of the enclosing-repository probe. These MUST stay distinct: the generic
    # _run() helper collapses "git ran and reported no repository", "git timed out" and
    # "git could not be spawned" into a single None, and for THIS predicate those three
    # do not mean the same thing. Everywhere else that collapse is safe because None
    # fails the gate closed (see _gate/_ok); here the polarity is inverted -- absence of
    # a repository is the SUCCESS condition -- so a None-means-success reading would turn
    # every no-verdict probe into "confined" and fail OPEN.
    _PROBE_ENCLOSED = "enclosed"        # git ran, exit 0: an enclosing repo exists
    _PROBE_STANDALONE = "standalone"    # git ran, non-zero: genuinely no repository
    _PROBE_GIT_ABSENT = "git-absent"    # git is not installed / cannot be spawned
    _PROBE_NO_VERDICT = "no-verdict"    # timed out or errored: we learned nothing

    def _probe_enclosing_repo(self, path):
        """Run the companion's own workspace-root probe against `path` and report WHICH
        outcome occurred, never a bare boolean. See _sandbox_is_confined for how each
        outcome is scored."""
        timeout = self.poll_timeout()
        if timeout is None or timeout <= 0:
            return self._PROBE_NO_VERDICT
        try:
            proc = subprocess.run(
                ["git", "-C", path, "rev-parse", "--show-toplevel"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, timeout=timeout, cwd=path,
            )
        except FileNotFoundError:
            return self._PROBE_GIT_ABSENT
        except (subprocess.TimeoutExpired, OSError, ValueError):
            return self._PROBE_NO_VERDICT
        return self._PROBE_ENCLOSED if proc.returncode == 0 else self._PROBE_STANDALONE

    def _sandbox_is_confined(self, path):
        """True iff `path` resolves to ITSELF under codex-companion's OWN workspace-root
        algorithm (git top-level walking UP from `path`, else `path` unchanged -- read
        directly from the installed companion's lib/workspace.mjs:resolveWorkspaceRoot /
        lib/git.mjs:ensureGitRepository, not assumed). If `path` is reachable from an
        ENCLOSING git repository -- e.g. it was accidentally created inside the
        durable_root's own working tree -- codex's `workspace-write` sandbox would resolve
        to that OUTER repo root instead, silently handing codex write access back to
        scripts/, segments/, the lock, and the joblog.

        FAILS CLOSED on a probe that produced no verdict. A bounded `git` call that times
        out or cannot run tells us nothing about `path`, while the companion's own probe
        is UNBOUNDED and would still find an enclosing repository -- so scoring a
        no-verdict probe as confined would grant exactly the access this check exists to
        deny. Only a probe that actually RAN can license a dispatch.

        Absence of `git` on the machine is the one no-result case that is still safe, and
        only because it is not really no-result: the companion's resolver degrades the
        SAME way (falls back to `path` itself), so there is no enclosing root for it to
        find either. That is its real, verified behavior, not a weaker assumption."""
        outcome = self._probe_enclosing_repo(path)
        return outcome in (self._PROBE_STANDALONE, self._PROBE_GIT_ABSENT)

    def _setup_sandbox(self):
        """Create the per-invocation, single-use, write-isolated sandbox and verify it is
        actually confined before returning True. Real filesystem I/O -- never called from
        __init__. On any failure (mkdtemp error, or the sandbox turns out to be inside a
        git working tree after all) the caller MUST refuse to dispatch: an unconfined
        sandbox is worse than no launch at all (strictness bias)."""
        ext = "draft" if self.kind == "translate" else "review"
        try:
            raw = tempfile.mkdtemp(prefix="ltcj.%s.%s." % (self.seg, self.inv))
        except OSError:
            return False
        # Pin ONE canonical form now -- macOS's /tmp -> /private/tmp symlink otherwise
        # produces two spellings of the same directory across this run (mkdtemp's raw
        # return vs. anything realpath'd later, e.g. by the companion's own state-dir
        # keying), which would silently miss each other.
        self.sandbox_dir = os.path.realpath(raw)
        if not self._sandbox_is_confined(self.sandbox_dir):
            return False
        self.sandbox_attempt = os.path.join(self.sandbox_dir, "attempt.%s.json" % ext)
        return True

    def _write_final_prompt(self):
        """Write the frozen prompt INSIDE the sandbox (its only other content besides the
        attempt file codex will create) and substitute ⟦JOB_OUT⟧ with the SANDBOX attempt
        path -- never a path outside it, or codex's own write-confinement would simply
        reject the write and the #198 no-output failure returns."""
        final = os.path.join(self.sandbox_dir, "prompt.txt")
        text = self.prompt_text.replace(JOB_OUT_PLACEHOLDER, self.sandbox_attempt)
        with open(final, "w", encoding="utf-8") as fh:
            fh.write(text)
        self.final_prompt = final

    # ---- step 6b: fd-pinned, digest-verified sandbox -> staging copy --------
    def _publish_from_sandbox(self, src_path, dst_path):
        """Copy `src_path` (inside the per-job sandbox, NEVER trusted directly) to
        `dst_path` (the private staging file in segdir) with every step bound to a file
        descriptor, never re-resolved by path -- a path re-checked-then-reused is exactly
        what a symlink swapped in between defeats.

          1. Open src_path O_NOFOLLOW: a symlink (even one that, at open time, still
             points inside the sandbox) is refused outright -- the sandbox is only ever
             supposed to hold a REGULAR file at this path. A malicious/buggy codex CAN
             still WRITE a symlink there (write-confinement restricts where writes land,
             not what a symlink's target string names), so this refusal is load-bearing,
             not defensive theatre.
          2. fstat the opened fd (never lstat/stat the path again) and require S_ISREG.
          3. Read the whole file through that SAME fd and hash it; fstat the SAME fd a
             second time and require size/ino/dev/mtime unchanged -- catches a writer
             still mutating the file underneath the read (identity + digest BEFORE).
          4. Open the destination DIRECTORY once, by fd (O_DIRECTORY), and do every
             subsequent create/verify/rename relative to that fd -- the directory itself
             is pinned, not re-resolved by path either.
          5. Write to an O_EXCL temp name under that dir fd, then re-open + re-hash what
             actually LANDED on disk and require it match the digest from step 3
             (identity + digest AFTER) before the final rename (same dir_fd both sides,
             same device -- atomic, POSIX overwrite semantics).

        Returns True iff every step succeeded and every check passed; never raises --
        refuse-and-report is the only failure mode (strictness bias: the only tolerable
        failure is refusing to publish, never publishing something unverified)."""
        segdir_fd = None
        src_fd = None
        tmp_name = ".pub.%s.%s.tmp" % (self.seg, self.inv)
        try:
            try:
                src_fd = os.open(src_path, os.O_RDONLY | _O_NOFOLLOW | _O_NONBLOCK | _O_CLOEXEC)
            except OSError:
                return False
            try:
                st1 = os.fstat(src_fd)
                if not stat.S_ISREG(st1.st_mode):
                    return False
                data = bytearray()
                os.lseek(src_fd, 0, os.SEEK_SET)
                while True:
                    chunk = os.read(src_fd, _COPY_CHUNK)
                    if not chunk:
                        break
                    data.extend(chunk)
                st2 = os.fstat(src_fd)
            except OSError:
                return False
            if (st1.st_dev, st1.st_ino, st1.st_size, st1.st_mtime_ns) != \
               (st2.st_dev, st2.st_ino, st2.st_size, st2.st_mtime_ns):
                return False   # mutated under us between the two fstats -- refuse
            digest = hashlib.sha256(bytes(data)).hexdigest()

            dst_dir = os.path.dirname(dst_path)
            dst_name = os.path.basename(dst_path)
            try:
                segdir_fd = os.open(dst_dir, os.O_RDONLY | _O_DIRECTORY | _O_NOFOLLOW | _O_CLOEXEC)
            except OSError:
                return False
            flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | _O_CLOEXEC | _O_NOFOLLOW
            try:
                tmp_fd = os.open(tmp_name, flags, 0o600, dir_fd=segdir_fd)
            except OSError:
                return False
            try:
                written = os.write(tmp_fd, bytes(data))
            finally:
                os.close(tmp_fd)
            if written != len(data):
                _silent_unlinkat(segdir_fd, tmp_name)
                return False
            # Re-open what actually LANDED and re-hash it -- proves the bytes on disk
            # (not just the in-memory buffer) match what was verified above.
            try:
                check_fd = os.open(tmp_name, os.O_RDONLY | _O_NOFOLLOW | _O_CLOEXEC,
                                   dir_fd=segdir_fd)
            except OSError:
                _silent_unlinkat(segdir_fd, tmp_name)
                return False
            try:
                on_disk = _sha256_fd(check_fd)
            finally:
                os.close(check_fd)
            if on_disk != digest:
                _silent_unlinkat(segdir_fd, tmp_name)
                return False
            try:
                # POSIX os.rename() overwrites an existing dst atomically (same semantics
                # as os.replace(); os.replace() itself does not accept dir_fd on this
                # platform, so the fd-pinned form is spelled with rename()).
                os.rename(tmp_name, dst_name, src_dir_fd=segdir_fd, dst_dir_fd=segdir_fd)
            except OSError:
                _silent_unlinkat(segdir_fd, tmp_name)
                return False
            return True
        finally:
            if src_fd is not None:
                os.close(src_fd)
            if segdir_fd is not None:
                os.close(segdir_fd)

    # ---- preflight: staging and canonical must share a device (#409) --------
    def _preflight_same_device(self):
        """The FINAL step of every promote path is os.replace(staging_file, self.canonical)
        -- a cross-device rename is NOT atomic on POSIX (falls back to copy+unlink, which
        can observably leave a partial destination on a crash mid-rename). Refuse BEFORE
        any dispatch if the private staging directory (segdir, where attempt/pending
        live) is not on the same filesystem as segments/ itself, rather than discovering
        it at promote time with a real codex turn already spent. Checked fresh every run
        via real stat() calls, not hardcoded -- segdir/attempt/pending are ONE directory
        today, so this is a live regression guard against that ever silently changing."""
        try:
            seg_dev = os.stat(self.segdir).st_dev
            staging_dev = os.stat(os.path.dirname(self.attempt)).st_dev
            pending_dev = os.stat(os.path.dirname(self.pending)).st_dev
        except OSError:
            return False
        return seg_dev == staging_dev == pending_dev

    # ---- step 3: per-seg kernel flock lease ---------------------------------
    def _acquire_flock(self, fd):
        """LOCK_NB retry within poll_remaining(); True only on a successful acquire."""
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return True
            except OSError:
                rem = self.poll_remaining()
                if rem <= 0:
                    return False
                time.sleep(min(0.25, rem))

    # ---- step 4: hygiene, adoption, launch -----------------------------------
    def read_joblog(self):
        try:
            with open(self.joblog, "r", encoding="utf-8") as fh:
                obj = json.load(fh)
            return obj if isinstance(obj, dict) else None
        except (OSError, ValueError):
            return None

    def _write_joblog(self, obj):
        """Atomic never-torn write via O_EXCL/O_NOFOLLOW tmp + os.replace. Best-effort.

        Consistency fix: checks os.write()'s own return value against the
        payload length before publishing, matching the short-write guard
        _publish_from_sandbox() already applies to its own tmp-file write
        (below) -- POSIX write() is permitted to write FEWER bytes than
        requested. Without this check a short write left a TRUNCATED,
        invalid-JSON temp file that os.replace() would still publish as the
        joblog -- jobId/jobCwd are what hygiene()'s cancel-a-stale-prior-job
        path and a human reading this file after a crash both trust; a
        corrupt joblog is silently worse than a merely-missing one (which
        is a value read_joblog() already handles as `None`)."""
        tmp = os.path.join(self.segdir, ".codex_job.%s.%s.tmp" % (self.seg, self.inv))
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | _O_CLOEXEC | _O_NOFOLLOW
        try:
            fd = os.open(tmp, flags, 0o600)
        except OSError:
            return
        try:
            data = json.dumps(obj).encode("utf-8")
            written = os.write(fd, data)
        finally:
            os.close(fd)
        if written != len(data):
            _silent_remove(tmp)
            return
        try:
            os.replace(tmp, self.joblog)
        except OSError:
            _silent_remove(tmp)

    def hygiene(self):
        """Cancel a still-active prior job for this seg/kind. Prior runs each get their
        OWN unpredictable sandbox path (#409), so the ONLY place to learn where to look is
        the prior joblog's own recorded `jobCwd` -- status/cancel MUST be queried with that
        exact --cwd, since codex-companion's job store is keyed by resolveWorkspaceRoot of
        whatever --cwd it is given (a query against self.root would search a DIFFERENT,
        unrelated store and simply never find the job). A missing/malformed `jobCwd` (an
        old-format joblog from before #409, or a truly forged one) is treated as
        "cannot locate" -> no cancel attempted; a live status check against the recorded
        cwd is still required before cancelling, both as defense-in-depth and because
        "launched" alone doesn't mean "still active". Unlike the pre-#409 design, the
        joblog now lives in segdir OUTSIDE any sandbox codex can reach, so it is no longer
        codex-writable -- the "forged jobId" threat this guard originally defended against
        is structurally closed by the sandbox itself, not by this check."""
        prior = self.read_joblog()
        if not prior or prior.get("status") != "launched":
            return
        pj = prior.get("jobId")
        prior_cwd = prior.get("jobCwd")
        if not isinstance(pj, str) or not pj or not isinstance(prior_cwd, str) or not prior_cwd:
            return
        proc = self._run([self.node, self.companion, "status", pj, "--json", "--cwd", prior_cwd],
                        self.poll_timeout())
        if not _ok(proc):
            return
        try:
            obj = json.loads(proc.stdout)
        except ValueError:
            return
        if not isinstance(obj, dict):
            return
        job = obj.get("job")
        job = job if isinstance(job, dict) else {}
        ws = obj.get("workspaceRoot") or job.get("workspaceRoot")
        if ws == prior_cwd and job.get("status") in _ACTIVE:
            self._run([self.node, self.companion, "cancel", pj, "--cwd", prior_cwd],
                      self.poll_timeout())

    # ---- #438 D8: a claimed segment may never be dispatched for translation ---
    def _claim_state(self):
        """`(state, detail, path)` for this seg's claim record under `self.run_id`,
        via claim_record's shared three-state predicate -- NEVER `Path.exists()`
        (see claim_record.py's own module docstring for why: it collapses ENOENT
        and "unreadable" into the same False, which is exactly the fail-open shape
        this plan keeps finding). `path` is returned for the refusal message (D8:
        "naming the segment and the claim").

        RAISES ValueError when `self.run_id` is not a value claim_record's own
        validate_run_id() accepts -- claimed_path() refuses to build a path from an
        unsafe run id rather than quietly relocating the lookup. The single caller,
        _refuse_claimed_translate(), catches it and REFUSES; see its own docstring
        for why that direction, and never let a future caller of this method let it
        escape to run()'s generic handler."""
        path = claim_record.claimed_path(self.run_id, self.seg, Path(self.root) / "runs")
        state, detail = claim_record.classify_claim_record(path)
        return state, detail, path

    def _refuse_claimed_translate(self):
        """True iff a translate launch for this seg must be refused because a claim
        record exists (D8). Only ever consulted for kind == "translate", and reached
        immediately after safe_adopt() -- a HEALTHY claimed segment already returned
        via safe_adopt() above and never gets here at all (D8's own measurement:
        safe_adopt() passes for a healthy claimed draft because the claim re-stamped
        its token to exactly what --expect-token checks). Reaching this point WITH a
        claim on record means the draft has gone missing or invalid since the claim
        -- the one scenario D8 exists to close.

        WHAT THIS DOCSTRING USED TO CLAIM, AND WHY IT WAS WRONG: it said "launch()
        is the sole route in this file that can overwrite the canonical", and used
        that to justify sitting immediately before launch(). It is false, and this
        same file already contradicted it -- _canonical_replaceable()'s own
        docstring names TWO write sites it guards: adopt_pending()'s
        os.replace(self.pending, self.canonical) and run()'s post-launch promote,
        os.replace(self.attempt, self.canonical). With the guard placed after
        adopt_pending(), the exact state D8 exists for -- a claimed segment whose
        draft went missing or invalid, so safe_adopt() fails -- gave adopt_pending()
        first refusal, and a SAME-RUN deferred attempt (a cross-run one is already
        blocked by the candidate gates' own --expect-token check) could be promoted
        over the claimed draft, destroying the pre_claim_content_sha1 baseline the
        claim exists to preserve. The call therefore moved UP, to sit between
        safe_adopt() and adopt_pending(); it did NOT move any higher, because the
        justification above is load-bearing and only holds below safe_adopt().

        PRESENT and AMBIGUOUS both refuse. An unreadable claim record (a non-
        regular entry, EACCES, ...) is NOT the same as no record -- mapping it to
        "proceed" would be this plan's third fail-open defect (see the "Standing
        consequence" / premises section of the #438 plan and claim_record.py's own
        AMBIGUOUS-maps-to-"do not claim" rule, mirrored here as "cannot rule out a
        claim -> refuse the launch"). Only CLAIM_ABSENT lets a translate proceed.

        Returns `(refuse, state, detail, path)`. `path` is None ONLY on the
        unusable-run-id branch below, and run() renders that case with its own
        wording -- every other refusal carries the real claim path for the
        operator to go look at."""
        if self.kind != "translate" or self.run_id is None:
            return False, None, None, None
        try:
            state, detail, path = self._claim_state()
        except ValueError as exc:
            # claim_record.claimed_path() REFUSES to build a path out of a run id
            # that fails its own validate_run_id() -- it raises rather than
            # returning a sanitized or sentinel path, precisely so a reader cannot
            # forget to check. main() validates --run-id before ever constructing a
            # CodexJob, so a real CLI invocation never reaches here; a caller that
            # constructs CodexJob() directly (every white-box test that predates
            # #438) can. The direction is the same one every other unreadable claim
            # state takes: an unusable run id means the claim state cannot be
            # determined AT ALL, which is strictly worse than an unreadable record,
            # so it REFUSES. Mapping it to "proceed" would reintroduce the fail-open
            # shape from the other side -- and letting the ValueError escape to
            # run()'s generic `except Exception` handler would turn a deliberate
            # refusal into a "reason: error: ValueError(...)" that reads like a
            # driver crash rather than a claim the driver could not rule out.
            return True, claim_record.CLAIM_AMBIGUOUS, str(exc), None
        if state == claim_record.CLAIM_ABSENT:
            # "This run has not claimed seg" is NOT "seg is unclaimed", and
            # conflating the two was this guard's defect -- the same one the
            # optional driver's D8 had, found here second and reachable on the
            # DEFAULT path, since mass-translate-wf.template.js launches
            # codex_job.py directly and never passes through that driver. An
            # ordinary run B supplies --expect-token B:seg and --run-id B, so
            # the consistency check agrees; safe_adopt() rejects the A-stamped
            # draft; B's own namespace reads absent; and the translate reached
            # launch() while A's claim record sat untouched.
            #
            # Delegated, not re-implemented. Two independent hand-rolled
            # answers to "is this claimed?" is exactly what produced three
            # rounds of one BLOCKER; foreign_owner_refusal() is the single
            # predicate both chokepoints now share.
            foreign = claim_record.foreign_owner_refusal(
                seg=self.seg,
                this_run_id=self.run_id,
                draft_path=Path(self.root) / "segments" / f"{self.seg}.draft.json",
                runs_dir=Path(self.root) / "runs",
            )
            if foreign is not None:
                return True, claim_record.CLAIM_PRESENT, foreign, None
            return False, None, None, None
        return True, state, detail, path

    def _adoption_gates(self):
        """The kind's ordered adoption gates as (script, passes --expect-token) pairs.
        ONE definition shared by safe_adopt() (gating a pre-existing canonical) and
        adopt_pending() (gating a deferred attempt). adopt_pending() spelled this list
        out itself until #399 gave safe_adopt() the same shape; one definition rather
        than two copies, because the two adoption paths must not be able to drift apart
        on WHICH gates run, in WHAT order, or which one carries the token check. Order is load-bearing for translate -- the ready gate before the quality
        gate -- and anything that is not a translate is gated as a review, exactly as
        both call sites read when each spelled the list out itself."""
        if self.kind == "translate":
            return [("draft_ready.py", True), ("validate_draft.py", False)]
        return [("review_ready.py", True)]

    def safe_adopt(self):
        """A pre-existing valid same-token canonical -> adopt without relaunching.

        #399: a gate that RAN and REFUSED the canonical has its own output recorded in
        self.adopt_rejection before this returns False. That refusal is the whole reason
        the run goes on to do something else with the segment -- refuse it as claimed,
        adopt a deferred pending over it, or launch a fresh turn whose promote OVERWRITES
        it -- and until now the only trace of it was the absence of `"adopted"` in the
        reason. The operator who hand-applied review findings to segments/<seg>.draft.json
        and re-invoked the driver could not learn why that draft was rejected without
        re-running the whole translation, which is exactly the cost #399 filed.

        A gate that could NOT run (proc is None: exhausted budget, timeout, spawn
        failure) has nothing to capture -- _ok(None) is already False, and inventing a
        rejection text for it would report a refusal that never happened."""
        if not os.path.exists(self.canonical):
            return False
        for name, with_token in self._adoption_gates():
            argv = [name, self.seg]
            if with_token:
                argv += ["--expect-token", self.tok]
            proc = self._gate(argv, self.poll_timeout())
            if not _ok(proc):
                if proc is not None:
                    self.adopt_rejection = self._gate_rejection_text(name, proc)
                return False
        return True

    def adopt_pending(self):
        """#213: try to adopt a completed-but-unvalidated attempt DEFERRED by a prior run of the same
        seg/kind. Validate through the same candidate gates (which also enforce --expect-token against
        the candidate's own dispatch_token) and, only on a FULL PASS, atomically promote it -> return
        True. Return False in every other case, handling the pending file so it is never lost or left
        to block a future run:
          - absent / a non-regular squatter -> cleared, return False;
          - a gate that RAN and REJECTED the candidate (proc.returncode != 0: bad content / stale
            cross-run token) -> DISCARD the pending, return False;
          - a gate that could NOT run (proc is None: exhausted budget / timeout / spawn fail) -> LEAVE
            the pending intact for a future run (never delete recoverable work), return False;
          - every gate PASSED but the canonical guard refuses the promote (self.canonical_unreadable
            set) -> LEAVE the pending intact, return False.
        Never promotes unvalidated content; runs before launch, so uses the poll-window budget. Only
        the no-budget gate case above is guaranteed to fall through to caller's launch() (MINOR-1) --
        the canonical-unreadable case is NOT: the caller stops there instead (see run()'s own
        canonical_unreadable branch, right after this call), since a fresh codex turn cannot succeed
        either while the canonical stays unreadable.

        The deadline this method's own _is_regular()/_canonical_replaceable() calls are bounded
        by is self.poll_remaining -- a poll-window operation, per this method's own name and
        docstring -- never self.abs_remaining(), the WHOLE JOB's ceiling: sharing that wider
        ceiling would let this method eat into the 150s finalize budget while holding the lease."""
        if not self._is_regular(self.pending, self.poll_remaining):
            self._clear_nonregular(self.pending)
            return False
        for name, with_token in self._adoption_gates():
            argv = [name, self.seg]
            if with_token:
                argv += ["--expect-token", self.tok]
            argv += ["--candidate-file", self.pending]
            proc = self._gate(argv, self.poll_timeout())
            if proc is None:
                return False                       # could not validate -> keep pending, launch fresh
            if proc.returncode != 0:
                self._capture_gate_rejection(name, proc)  # #399: capture before discarding
                if (name == "validate_draft.py" and proc.returncode == 1
                        and self._is_regular(self.pending, self.poll_remaining)):
                    # #665: the SAME exit-1 contract _validate_candidate() reads for a fresh
                    # attempt, read here for a DEFERRED one. That this is a same-token verdict
                    # on CONTENT, and never a stale cross-run token, is guaranteed by the gate
                    # ORDER _adoption_gates() owns: draft_ready.py carries --expect-token and
                    # runs first, and this loop returns on its rejection -- so reaching
                    # validate_draft.py at all proves the pending's own dispatch_token already
                    # matched THIS run. run() acts terminally on the flag; every other rejection
                    # here (draft_ready.py at any code, validate_draft.py exit 2, review_ready.py)
                    # leaves it false and keeps the discard-and-relaunch this branch always did.
                    #
                    # The trailing _is_regular() is NOT a repeat of the one at the top of this
                    # method -- it re-confirms the candidate AFTER the gates ran, and it is what
                    # makes the terminal verdict safe. Each gate re-OPENS self.pending by PATH,
                    # and unlike validate_attempt()'s candidate (self.attempt, an unguessable
                    # per-invocation .att.<seg>.<inv>... name), this slot's name is deterministic
                    # and persists across runs -- derivable by the codex process this driver
                    # launches, which holds write access over segments/ and whose straggler turn
                    # can outlive poll()'s best-effort cancel (see _record_translate_rejected()'s
                    # own comment on that same actor). A candidate deleted, truncated, or replaced
                    # by a symlink inside that window makes validate_draft.py exit 1 too -- its
                    # contract puts a missing/malformed candidate there deliberately -- and
                    # without this re-check that concurrent write, not a content verdict, would
                    # block the segment permanently. On a False here the flag stays down and the
                    # discard-and-relaunch below runs, exactly as it did before #665: a
                    # recoverable outcome for a state nothing has actually judged.
                    self.translate_content_rejected = True
                _silent_remove(self.pending)       # gate ran & rejected -> discard stale/bad, launch fresh
                return False
        if not self._canonical_replaceable(self.poll_remaining):
            # Every gate above validated self.pending, never self.canonical -- os.replace()
            # only needs write permission on the DIRECTORY, not the target, so blindly
            # replacing here could destroy bytes this process never read. Refuse and leave
            # self.pending exactly as every other "could not promote" branch above does:
            # intact, for a future dispatch to retry once the canonical is readable again.
            self.canonical_unreadable = True
            self.reason = "canonical-unreadable"
            return False
        self._archive_outgoing_review(self.poll_remaining)  # #541, advisory; never gates this promote
        os.replace(self.pending, self.canonical)   # every gate passed
        return True

    def launch(self):
        # ALWAYS workspace-write (codex MUST write its ⟦JOB_OUT⟧ attempt -- read-only was
        # the #198 no-output failure) and a FRESH per-attempt codex thread. `--effort`
        # defaults to "high" (belt-and-suspenders with the prompt's own effort opener).
        # `--cwd` is the per-job SANDBOX (#409), never self.root/durable_root -- see
        # _sandbox_is_confined for why a mere subdirectory of the same repo would NOT
        # shrink codex-companion's own workspace-write resolution.
        argv = [self.node, self.companion, "task", "--background", "--json", "--write", "--fresh"]
        if self.effort:
            argv += ["--effort", self.effort]
        if self.model:
            argv += ["--model", self.model]
        argv += ["--cwd", self.sandbox_dir, "--prompt-file", self.final_prompt]
        proc = self._run(argv, self.poll_timeout())
        if not _ok(proc):
            # #400: the launch invocation itself failed (non-zero exit, or _run()
            # returned None on a timeout/spawn failure) -- capture whatever stderr
            # the companion printed (its own thrown error, e.g. auth/quota) rather
            # than silently falling through to run()'s generic "launch-failed"
            # reason with no detail behind it. proc is None on a timeout/spawn
            # failure, so there is nothing to read in that case.
            self.error_detail = _stderr_text(proc)
            return False
        try:
            obj = json.loads(proc.stdout)
        except ValueError:
            obj = None
        jid = obj.get("jobId") if isinstance(obj, dict) else None
        if not isinstance(jid, str) or not jid:
            # The companion exited 0 but produced no usable jobId -- capture its
            # stderr too (usually empty on a clean exit, but never discarded).
            self.error_detail = _stderr_text(proc)
            return False
        self.jobId = jid
        self._write_joblog({
            "jobId": jid, "kind": self.kind, "seg": self.seg, "token": self.tok,
            "disp": self.disp, "inv": self.inv, "status": "launched",
            "jobCwd": self.sandbox_dir,
        })
        return True

    # ---- step 5: poll to terminal or the poll deadline ----------------------
    def poll(self):
        while True:
            if self.poll_remaining() <= 0:
                break
            proc = self._run([self.node, self.companion, "status", self.jobId, "--json",
                             "--cwd", self.sandbox_dir], self.poll_timeout())
            if _ok(proc):
                try:
                    obj = json.loads(proc.stdout)
                    job = obj.get("job") if isinstance(obj, dict) else None
                    if isinstance(job, dict):
                        self.job_status = job.get("status")
                        # #400: the companion's own job store persists errorMessage
                        # when its tracked-job runner caught a thrown exception (an
                        # API/quota/auth error, not a content defect) -- this is
                        # EXACTLY the "N unrelated per-segment content failures"
                        # signal #400 reports as missing. Present (a non-empty
                        # string) once the job reaches a real failure, never before
                        # -- so plain assignment (not "first/last non-empty wins")
                        # is fine, but guard the type/emptiness rather than
                        # overwriting a real capture with an absent/blank one on a
                        # later poll of the SAME job (polling stops the instant
                        # job_status goes terminal, so in practice there is no
                        # "later poll" once errorMessage first appears -- this
                        # guard is defensive, not load-bearing on that guarantee).
                        err = job.get("errorMessage")
                        if isinstance(err, str) and err.strip():
                            self.error_detail = err.strip()
                except ValueError:
                    pass
            if self.job_status in _TERMINAL:
                return
            rem = self.poll_remaining()
            if rem <= 0:
                break
            time.sleep(min(self.poll_sec, rem))
        if self.job_status not in _TERMINAL:
            # Poll deadline reached while (possibly) active -> best-effort cancel,
            # finalize-bounded. This does NOT prove the detached codex turn stopped (it
            # runs in its own session; codex-companion's cancel is best-effort) -- we never
            # read from the sandbox again on this path (see finalize()), so a straggler
            # that outlives this call is neutralised by the isolation itself, not by proof
            # of termination.
            self._run([self.node, self.companion, "cancel", self.jobId, "--cwd",
                      self.sandbox_dir], self.finalize_timeout())
            self.timed_out = True

    # ---- step 6: validate the attempt (kind-specific candidate gate) --------
    def validate_attempt(self):
        # #409: PUBLISH first -- codex's raw output never left the sandbox until this
        # fd-pinned, digest-verified copy lands it in the private staging slot. Only
        # THEN do the existing candidate gates (unchanged) get to see it.
        if not self._publish_from_sandbox(self.sandbox_attempt, self.attempt):
            return False
        if not self._is_regular(self.attempt, self.finalize_timeout):
            return False
        return self._validate_candidate(self.attempt, self.finalize_timeout)

    def _defer_attempt(self):
        """#213: atomically move a completed-but-unvalidated attempt into the stable per-seg/kind
        pending slot so the NEXT run's adopt_pending() can validate + adopt it, instead of
        discarding a rare late-completing codex result. Clears any non-regular squatter on the slot
        first so the rename cannot fail into finalize()'s discard. Returns True iff a real regular
        attempt file was preserved. Promotes NOTHING.

        #429 CONTRACT (the reasoning behind each clause is at its own site below, not restated
        here): a REGULAR occupant of the slot is first given a second name, self.superseded, so
        overwriting the slot no longer destroys its bytes. Only FileNotFoundError is read as "no
        occupant"; every other failure REFUSES the deferral instead of destroying what it could
        not preserve. Nothing re-adopts or collects a `.att_superseded.*` file -- it is durable
        rather than ephemeral, its accumulation is bounded by nothing, and it exists for HAND
        recovery only.

        The single per-seg/kind slot deliberately retains the MOST RECENT completed attempt
        (last-writer-wins) -- it never sticks on a stale/invalid pending. Validity cannot be
        determined at defer time -- the defer is triggered precisely because no budget remained to
        run the candidate gate -- so preferentially KEEPING an existing pending over a fresh attempt
        risks sticking on an unadoptable one (a same-token but structurally invalid pending would be
        kept forever while valid fresh attempts are discarded). Always refreshing the slot instead
        guarantees it tracks the latest completion and can NEVER get stuck: an invalid pending is
        superseded by the next fresh attempt, and adopt_pending() discards it outright the first time
        a gate can actually run.

        #409: PUBLISH from the sandbox first (fd-pinned, digest-verified) -- at this point
        nothing has validated the candidate yet, but the sandbox->staging copy is not itself
        a trust decision, only a relocation; adopt_pending() on the NEXT run still runs the
        real candidate gates before anything is promoted."""
        if not self._publish_from_sandbox(self.sandbox_attempt, self.attempt):
            return False
        # abs_remaining here is deliberate, not an oversight against the phase-budget
        # taxonomy _is_regular() documents (poll_remaining / finalize_timeout / abs_remaining
        # for a caller with nothing further to reserve). The phase-correct value by that
        # taxonomy is finalize_timeout, since finalize() always follows -- but this method is
        # only ever reached on the branch where abs_remaining() <= FINALIZE_TAIL already, and
        # finalize_timeout() is max(0.0, ... - FINALIZE_TAIL): it is pinned at exactly zero by
        # construction at this point, so threading it would refuse at this very first check
        # every single time, silently disabling the #213 deferral this method exists to do.
        # The trade this leaves standing: the drain below can run inside the reserved finalize
        # tail rather than before it. In practice that is bounded by self.attempt being a small
        # local JSON file this same process just wrote via _publish_from_sandbox, still subject
        # to _is_regular()'s own size ceiling and growth counter -- not a network fetch, not an
        # arbitrary pre-existing file -- but it is not a guarantee, and is disclosed as a known
        # limit rather than closed here.
        if not self._is_regular(self.attempt, self.abs_remaining):
            return False
        self._clear_nonregular(self.pending)
        # #429: PRESERVE any occupant before the slot is overwritten. os.link(), never a
        # rename: a link ADDS a name and removes none, so self.pending is never vacated and
        # the os.replace() below stays the single mutation of it -- there is no window, and
        # no failure combination, in which a regular occupant leaves the slot without the
        # fresh candidate arriving. (A rename-based preserve has both: a crash or a failing
        # replace between the two mutations strands BOTH candidates at names no later run
        # consults.) The occupant is never OPENED -- link needs only directory write -- which
        # is the whole point: the occupant this exists for is one that has gone UNREADABLE
        # between runs, which adopt_pending() refuses and _clear_nonregular() leaves alone.
        try:
            os.link(self.pending, self.superseded, follow_symlinks=False)
        except FileNotFoundError:
            pass            # definitively NO occupant -- nothing to preserve, carry on
        except (OSError, NotImplementedError) as exc:
            # NOT FileNotFoundError, so this is "could not preserve", never "absent". Reading
            # any other errno as absence is the refuted reasoning that makes an ESTALE
            # destroy a good candidate. NotImplementedError is in the tuple because an
            # unsupported follow_symlinks raises it and it is not an OSError.
            #
            # Refuse. This discards the fresh attempt, which is UNVALIDATED by construction
            # (the defer fires precisely because no budget remained to gate it) -- the
            # less-established of the two artifacts, which is the correct trade at THIS site
            # and not at the canonical-relocate site, whose fresh candidate has passed every
            # gate. A durable_root on a filesystem without hard links refuses every occupied
            # deferral this way: an availability cost, never a data-loss one.
            self.error_detail = "pending preserve failed: %r" % (exc,)
            return False
        try:
            os.replace(self.attempt, self.pending)
        except OSError as exc:
            # Any occupant is still in the slot (the link never removed it) and, when there
            # was one, also under the `.att_superseded.*` name, which is then redundant.
            # Nothing collects it.
            self.error_detail = "defer replace failed: %r" % (exc,)
            return False
        return True

    # ---- step 7: finalize within the reserved tail --------------------------
    def _write_fail_sentinel(self):
        """Empty per-dispatch marker; O_EXCL|O_NOFOLLOW so a pre-existing forged entry of ANY
        type (regular/FIFO/symlink/dir) cannot block, follow, or crash finalization."""
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | _O_CLOEXEC | _O_NOFOLLOW
        try:
            fd = os.open(self.fail_sentinel, flags, 0o600)
            os.close(fd)
        except FileExistsError:
            pass  # the NAME's presence is the whole signal; never opened/read
        except OSError:
            pass  # best-effort (e.g. an existing symlink -> ELOOP under O_NOFOLLOW)

    def finalize(self):
        self.ok = self.promoted or self.adopted
        if not self.ok:
            self._write_fail_sentinel()
        # Clean ONLY this invocation's own scratch, by EXACT path (never a wildcard).
        if not self.promoted and not self.canonical_unreadable:
            _silent_remove(self.attempt)  # the os.replace consumed it iff promoted; a
            # canonical-unreadable refusal is a data-safety refusal, not a candidate
            # defect (see _canonical_replaceable()'s own docstring) -- the validated
            # attempt is left in place rather than discarded. self.canonical_unreadable,
            # not self.reason: reason is reassigned by whatever this run does NEXT (a
            # later launch-failed/validate-failed/job-completed/etc reaching THIS
            # finalize() call), so a string comparison here would stop protecting the
            # file the moment anything downstream narrates a different outcome.
        if self.sandbox_dir:
            # Abandon the WHOLE sandbox unconditionally -- on every path (success,
            # validate-failure, timeout) we are done reading from it by this point
            # (validate_attempt/_defer_attempt already PUBLISHED whatever mattered out
            # via the fd-pinned copy). A straggling codex turn that outlives poll()'s
            # best-effort cancel can then only write into a directory nobody will ever
            # consume from again -- this rmtree is the "neutralised by the isolation
            # itself" half of #409, not a courtesy cleanup.
            shutil.rmtree(self.sandbox_dir, ignore_errors=True)
        _silent_remove(self.prompt_file)
        # Terminal hygiene joblog ONLY IF we hold the lease (a lease-loser must never clobber
        # the live holder's control state -- HIGH-3 r8).
        #
        # #398/#400: `reason` and `error_detail` are BOTH carried into this joblog, not just
        # the stdout line below -- run() launches this driver DETACHED (`nohup ... >/dev/null
        # 2>&1 &`, see mass-translate-wf.template.js), so the stdout line's own "reason"/
        # "error_detail" are thrown away at the shell level on that path; THIS FILE is the
        # only durable place either ever lands. Previously `reason` (a gate-REJECTED attempt
        # reported no differently from a genuine timeout) and error_detail's two sources
        # (poll()'s companion errorMessage, launch()'s stderr) were computed and discarded.
        # #399: `adopt_rejection` rides the same two carriers for the same reason -- it is
        # the ONLY record that a pre-existing canonical was refused, and it is emitted
        # whatever this run went on to do, including a run that finished ok.
        if self.holds_lock:
            joblog_record = {
                "jobId": self.jobId, "kind": self.kind, "seg": self.seg, "token": self.tok,
                "disp": self.disp, "inv": self.inv, "status": "terminal", "ok": self.ok,
                "timed_out": self.timed_out, "job_status": self.job_status, "adopted": self.adopted,
                "reason": self.reason, "error_detail": self.error_detail,
                "adopt_rejection": self.adopt_rejection,
            }
            # #398: the ONE conditional key in this record -- present only when a terminal
            # ledger write was actually attempted, so an ordinary job's joblog shape is
            # unchanged (pinned by the negative table in codex_job_driver.test.py). Siblings
            # `error_detail` and `adopt_rejection` are emitted unconditionally as None; the
            # difference is deliberate. Best-effort observability either way --
            # _write_joblog() returns silently on an I/O failure.
            if self.ledger_write is not None:
                joblog_record["ledger_write"] = self.ledger_write
            self._write_joblog(joblog_record)
        line = {
            "ok": self.ok, "kind": self.kind, "seg": self.seg, "jobId": self.jobId,
            "job_status": self.job_status, "timed_out": self.timed_out,
            "adopted": self.adopted, "reason": self.reason, "error_detail": self.error_detail,
            "adopt_rejection": self.adopt_rejection,
        }
        sys.stdout.write(json.dumps(line) + "\n")
        sys.stdout.flush()

    # ---- orchestration ------------------------------------------------------
    def run(self):
        lock_fd = None
        try:
            try:
                os.makedirs(self.segdir, exist_ok=True)
            except OSError:
                pass
            if not self._preflight_same_device():
                # #409 property 3: staging (segdir) and the canonical segments/ tree must
                # share a device, or the final promote os.replace() is not atomic. Refuse
                # BEFORE spending a real codex turn.
                self.reason = "device-mismatch"
                return 1
            if not self._canonical_replaceable(self.finalize_timeout):
                # Same shape as the device-mismatch check above: refuse BEFORE spending a
                # real codex turn, not after. If the canonical entry exists but cannot be
                # observed right now, neither safe_adopt() (which reads self.canonical
                # directly and would fail the same way) NOR adopt_pending()/this run's
                # own eventual promote step (both refuse via this SAME check -- see
                # adopt_pending() and the promote branch further below) can succeed this
                # run; launching a fresh codex turn anyway buys nothing but cost. This is
                # the common case; the later guards remain in place for the file that
                # turns unreadable DURING this run, after this check already passed --
                # neither makes the other redundant.
                #
                # finalize_timeout, not abs_remaining: this drain runs BEFORE the flock is
                # even acquired (below), with the WHOLE job's ceiling still ahead of it --
                # sharing that unbounded ceiling here would let this one check consume the
                # 10s FINALIZE_TAIL reserved for finalize()'s own stdout/sentinel/joblog
                # write, the exact overrun the phase threading elsewhere in this file exists
                # to prevent (see _is_regular()'s own docstring for the three legitimate
                # remaining_fn shapes and why abs_remaining is not one of them here).
                self.canonical_unreadable = True
                self.reason = "canonical-unreadable"
                return 1
            if not self._setup_sandbox():
                # #409 property 4-adjacent: an unconfined sandbox is worse than none.
                self.reason = "sandbox-not-isolated"
                return 1
            self._write_final_prompt()
            lock_fd = os.open(self.lock, os.O_CREAT | os.O_RDWR | _O_CLOEXEC, 0o600)
            self.holds_lock = self._acquire_flock(lock_fd)
            if not self.holds_lock:
                # Lease held past our poll window -> recoverable; re-dispatch on the NEXT W5 run.
                self.reason = "lease-held"
                return 1
            self.hygiene()
            if self.safe_adopt():
                _silent_remove(self.pending)          # canonical already valid -> any deferred attempt is moot
                self.adopted = True
                self.reason = "adopted"
                return 0
            # #438 D8: placed HERE -- after safe_adopt(), before EVERY remaining
            # route in this file that can overwrite the canonical draft. Not right
            # after --kind parsing, which would fire before safe_adopt() and break
            # the flow that already works today (a healthy claimed segment adopts
            # and returns 0 above, never reaching this line at all); and no longer
            # after adopt_pending(), which is where it originally sat on the false
            # premise that launch() was the only destructive route. adopt_pending()
            # ends in os.replace(self.pending, self.canonical) -- in exactly the
            # state this guard exists for (a claimed segment whose draft went
            # missing or invalid, so safe_adopt() failed), a same-run deferred
            # attempt would have been promoted over the claimed draft, destroying
            # the pre_claim_content_sha1 baseline the claim exists to preserve.
            # A refusal here also leaves self.pending untouched, unlike the older
            # order, where a claimed segment's refusal could be preceded by
            # adopt_pending() discarding a gate-rejected pending on its way past.
            refuse, claim_state, claim_detail, claim_path = self._refuse_claimed_translate()
            if refuse:
                self.reason = "claimed-segment-refused"
                if claim_path is None:
                    # The run id itself could not be turned into a claim path (see
                    # _refuse_claimed_translate()'s own ValueError branch) -- there
                    # is no record to name, and asserting a claim would overstate
                    # what is known. What IS known is the only thing that matters
                    # here: the claim state is unverifiable, so the translate stops.
                    self.error_detail = (
                        "a claim on segment %r cannot be ruled out: this run's own "
                        "id %r cannot be turned into a claim path (%s) -- a "
                        "translate may never proceed on a claim state this driver "
                        "is unable to read (#438 D8)"
                        % (self.seg, self.run_id, claim_detail)
                    )
                else:
                    self.error_detail = (
                        "segment %r is claimed under run %r (record %s) -- a claimed "
                        "segment may never be translated (#438 D8): its draft is "
                        "missing or failed validation and must be repaired or "
                        "re-claimed, never overwritten by a fresh translate%s"
                        % (
                            self.seg, self.run_id, claim_path,
                            "" if claim_state == claim_record.CLAIM_PRESENT
                            else (" [claim record unreadable: %s]" % claim_detail),
                        )
                    )
                return 1
            if self.adopt_pending():                  # NEW: promote a prior run's deferred completed attempt
                self.adopted = True
                self.reason = "adopted-pending"
                return 0
            if self.canonical_unreadable:
                # adopt_pending() found a candidate that passed every gate, but its own
                # canonical guard refused the promotion -- neither "no usable pending" nor
                # #665's content rejection, the two other reasons adopt_pending() returns
                # False (the content one is handled immediately below). self.pending was
                # left untouched by that refusal (see adopt_pending()'s own comment), so
                # there is nothing to lose by stopping here, and everything to lose by not
                # stopping: falling through to launch() spends a fresh paid turn that can
                # never succeed either (the canonical is still unreadable), and if that
                # fresh completion then lands in the no-budget branch below,
                # _defer_attempt()'s own documented last-writer-wins semantics would
                # overwrite the still-good pending candidate with the new, unvalidated
                # one. Since #429 that no longer destroys the candidate's BYTES -- they
                # survive under the `.att_superseded.*` name -- but nothing re-adopts that
                # name, so the validated candidate still becomes unreachable to every later
                # run and the work is still regenerated. Stopping here is therefore correct
                # for exactly the same practical reason it always was.
                return 1
            if self.kind == "translate" and self.translate_content_rejected:
                # The `kind` conjunct mirrors the #398 site below, for the same reason it
                # gives: defence in depth, not load-bearing -- _adoption_gates() yields
                # validate_draft.py only for translate, so only a translate can set the flag.
                #
                # #665: the deferred candidate adopt_pending() just discarded was refused by
                # validate_draft.py exit 1 -- the same permanent content verdict #398 already
                # acts on for a fresh attempt, reached by the other route. Falling through to
                # launch() from here is what made that route unbounded: the segment kept its
                # recoverable fragment, the next run re-dispatched it, and each pass paid for a
                # full translation the shipped gate has already refused. Stop, and take #398's
                # terminal write so select_segments.py escalates the segment instead
                # (--only-segs still reaches it, as it does every other blocked fragment).
                #
                # A DISTINCT reason, unlike #398's site: that one kept "validate-failed"
                # because an existing label was already being read; this path had no label of
                # its own at all -- the reason it ended up reporting was whatever the FRESH job
                # then produced, which is precisely what made the repeat invisible.
                self.reason = "pending-rejected"
                self._record_translate_rejected()
                return 1
            if not self.launch():                     # False (incl. no-budget, pending kept) -> launch fresh
                self.reason = "launch-failed"
                return 1
            self.poll()
            if self.job_status == "completed" and self.abs_remaining() > FINALIZE_TAIL:
                if self.validate_attempt():
                    if not self._canonical_replaceable(self.finalize_timeout):
                        # Data-safety refusal, not a candidate defect: self.attempt just
                        # passed every gate, but self.canonical cannot be read right now
                        # (an unreadable regular file, or a symlink whose target vanished)
                        # -- promoting over it would destroy bytes nothing has read.
                        self.canonical_unreadable = True
                        self.reason = "canonical-unreadable"
                        # self.attempt lives at this invocation's own random
                        # .att.<seg>.<inv>... path -- nothing ever revisits that path on a
                        # later run (only self.canonical and self.pending are consulted),
                        # so a validated candidate refused here is unreachable by any
                        # future dispatch: the bytes survive on disk (finalize() never
                        # discards self.attempt while canonical_unreadable is set -- see
                        # its own comment), but nothing will ever find or promote them,
                        # and the next dispatch pays to regenerate the same work. This is
                        # a disclosed limit, not a defect this release closes: see the
                        # release's own "Known limits" text.
                    else:
                        self._archive_outgoing_review(self.finalize_timeout)  # #541, advisory
                        os.replace(self.attempt, self.canonical)
                        self.promoted = True
                        self.reason = "promoted"
                        return 0
                else:
                    self.reason = "validate-failed"
                    # The `kind` conjunct is defence in depth, not load-bearing: only the
                    # translate branch of _validate_candidate() can set the flag.
                    if self.kind == "translate" and self.translate_content_rejected:
                        # #398: the reason string stays exactly as it shipped -- the driver
                        # and every existing test read it unchanged. What is NEW is the
                        # durable consequence: a content rejection now leaves a TERMINAL
                        # ledger fragment, so select_segments.py stops calling the segment
                        # recoverable and neither dispatch path pays for the same rejected
                        # translation again.
                        self._record_translate_rejected()
            elif self.job_status == "completed":       # NEW: completed but no budget to validate this run
                self.reason = "deferred-completed" if self._defer_attempt() else "job-completed"
            elif self.timed_out:
                self.reason = "timed-out"
            else:
                self.reason = "job-%s" % (self.job_status,)
            return 1
        except Exception as exc:  # never overrun the finally: recoverable failure
            self.reason = "error: %r" % (exc,)
            return 1
        finally:
            try:
                self.finalize()
            except Exception:
                pass
            if lock_fd is not None:
                try:
                    os.close(lock_fd)
                except OSError:
                    pass


def _build_parser():
    p = argparse.ArgumentParser(
        prog="codex_job.py",
        description="Isolating, validate-before-promote codex-job driver (#198).",
    )
    p.add_argument("--kind", required=True, choices=("translate", "review"))
    p.add_argument("--companion", required=True)
    p.add_argument("--cwd", required=True)
    p.add_argument("--seg", required=True)
    p.add_argument("--prompt-file", required=True, dest="prompt_file")
    p.add_argument("--expect-token", required=True, dest="expect_token")
    # #438: REQUIRED, never derived from --expect-token. Splitting --expect-token
    # at its first colon would recover a RUN_ID in the common case (the same
    # derivation select_segments.py's draft_run_id() uses), but a malformed token
    # yields no run id, which yields "no claim record found", which reads as
    # "not claimed" and proceeds -- trading a loud failure for a silent one. See
    # D8's own reasoning in the #438 plan.
    # NOT argparse `required=True`: kept a plain optional flag (default None) so
    # ITS OWN absence is checked, and reported, by main() below alongside every
    # other hand-validated flag (--seg, --disp, --deadline-sec) -- an
    # argparse-level `required=True` here would raise SystemExit straight out of
    # parse_args(), before main()'s own usage checks ever run, which is a
    # DIFFERENT failure shape (uncaught exception vs. a clean `return 2`) than
    # every other manually-validated flag in this parser.
    p.add_argument("--run-id", default=None, dest="run_id")
    p.add_argument("--disp", required=True)
    p.add_argument("--deadline-sec", required=True, type=int, dest="deadline_sec")
    p.add_argument("--poll-sec", type=int, default=15, dest="poll_sec")
    # --write/--fresh are ACCEPTED for dispatcher compatibility but IMPLIED-ALWAYS: the
    # driver unconditionally launches codex workspace-write + fresh (see CodexJob.launch).
    p.add_argument("--write", action="store_true")
    p.add_argument("--fresh", action="store_true")
    p.add_argument("--effort", default="high")
    p.add_argument("--model", default=None)
    p.add_argument("--node", default="node")
    # #412: the plugin's own install root -- see _trusted_scripts_dir()'s own
    # docstring and the seam comment above _DURABLE_ROOT_CONTRACT_SCRIPTS for
    # why gate EXECUTABLES must be resolvable from somewhere the codex
    # process this driver launches cannot write to. Optional; omitting it
    # reproduces today's pre-#412 default (SCRIPTS_DIR) byte-for-byte -- this
    # is opt-in hardening, not a forced migration.
    p.add_argument("--plugin-root", default=None, dest="plugin_root")
    return p


def main(argv=None):
    args = _build_parser().parse_args(argv)  # argparse exits 2 on bad usage / choice

    seg_err = validate_seg(args.seg)
    if seg_err:
        print("Error: %s" % seg_err, file=sys.stderr)
        return 2
    if args.deadline_sec <= 0:
        print("Error: --deadline-sec must be a positive integer", file=sys.stderr)
        return 2
    if not _valid_disp(args.disp):
        print("Error: --disp is not a safe single path component", file=sys.stderr)
        return 2
    if args.run_id is None or not args.run_id.strip():
        # #438 D8: FATAL, not "unclaimed" -- omitting --run-id must not silently
        # degrade into "cannot look up a claim, so proceed as if unclaimed". An
        # empty/whitespace-only value is caught the same way as an absent one
        # (the same silent-degradation trap the --plugin-root check below closes
        # for a different flag): either would mis-resolve the claim path
        # (Path("runs") / "" is Path("runs") unchanged) and read every claim
        # lookup as "not found" -> "not claimed" -> proceed. RUN_ID is
        # deliberately never derived from --expect-token here either -- see D8's
        # own reasoning at _build_parser()'s --run-id.
        print("Error: --run-id is required and must be a non-empty string "
              "(never derived -- pass the run's own RUN_ID explicitly)",
              file=sys.stderr)
        return 2
    # SHAPE, not just presence. The check above only proves --run-id is not blank;
    # it says nothing about whether the value is usable as the single path
    # component this driver splices into runs/<RUN_ID>/.claimed.<seg>. A path-like
    # value ('../x', '/tmp/elsewhere') used to relocate that lookup silently, and a
    # relocated lookup reports CLAIM_ABSENT -- which the D8 guard reads as "not
    # claimed" and proceeds, the fail-open shape #438 exists to refuse. As of
    # claim_record.claimed_path()'s own validation that same value now RAISES
    # instead, which closes the silent relocation but would surface, from a CLI
    # invocation, as a traceback out of run()'s generic handler ("reason: error:
    # ValueError(...)"). Neither is an acceptable answer to a mistyped flag, so the
    # value is checked HERE and turned into a clean exit 2 that names the flag.
    #
    # claim_record's copy is called rather than a sixth local copy of the same
    # regex: it is the exact function claimed_path() will apply, so agreement is
    # structural rather than pinned by a drift test, and adding a module-level
    # RUN_ID-named pattern to THIS file would enlist it into
    # tests/run_id_pattern_drift.test.py's roster for no benefit.
    run_id_problem = claim_record.validate_run_id(args.run_id)
    if run_id_problem is not None:
        print("Error: --run-id is not usable as a claim-path component: %s"
              % run_id_problem, file=sys.stderr)
        return 2
    # #438: the claim NAMESPACE must be the run the token dispatches for.
    # _claim_state() looks the claim up under --run-id while every gate checks the
    # draft against --expect-token, and nothing tied the two together: a direct
    # invocation with `--expect-token RUN-A:seg --run-id RUN-B` looked up a claim
    # that cannot exist under RUN-B, read CLAIM_ABSENT as "not claimed", and could
    # reach launch() over a draft claimed under RUN-A. Both shipped callers already
    # pass agreeing values (mass-translate-wf.template.js builds `expectToken` as
    # RUN_ID + ":" + seg right beside its own `--run-id RUN_ID`;
    # segment_dispatch_driver.py builds both from the same ctx.run_id), but the
    # chokepoint must not depend on a contract it never enforces.
    #
    # THIS IS A CONSISTENCY CHECK BETWEEN TWO INDEPENDENTLY SUPPLIED VALUES, NOT A
    # DERIVATION -- the next reader will assume the deliberate "RUN_ID is NEVER
    # derived from --expect-token" rule (see _build_parser()'s own --run-id
    # comment) was broken here. It is not: the token's run component is never
    # ADOPTED as the run id, and a token that carries none is REFUSED rather than
    # mined for one. Non-derivation is a rule about where RUN_ID comes FROM (the
    # caller, always, even when the token would have yielded the same string); it
    # says nothing about whether a malformed token may be waved through.
    #
    # A TOKEN WITH NO RUN COMPONENT IS FATAL, NOT SKIPPED. An earlier revision
    # skipped this whole check whenever the token had no colon, or an empty leading
    # component, justifying the skip with "the gates already refuse a malformed
    # token on their own". That justification is FALSE on the one path that matters
    # -- the DESTRUCTIVE one. The gates refuse the EXISTING draft, and refusing the
    # existing draft is precisely what makes run() treat the segment as needing
    # work and launch a fresh translate; the post-launch os.replace then destroys
    # the claimed draft (and with it the pre_claim_content_sha1 baseline taken from
    # it), and the gates re-run against codex's NEW attempt, whose dispatch_token is
    # whatever the prompt told it to stamp -- so the malformed token never has to
    # satisfy anything. The gates protect the old bytes from being ADOPTED; they do
    # not protect them from being OVERWRITTEN, which is the harm D8 exists to stop.
    # Measured by driving the real main() with a deliberately-missing --companion,
    # so the NEXT check's own message says whether this block fired at all:
    # `--expect-token RUN-A:<seg> --run-id RUN-B` and `RUN-A:<seg>:r2 --run-id
    # RUN-B` were refused by the disagreement branch below, while `BOGUS`,
    # `:<seg>` and `""` all sailed past into the companion check with --run-id
    # RUN-B entirely unexamined. From there the rest follows from this
    # file: a claim minted under RUN-A is invisible to a lookup under RUN-B, and
    # _refuse_claimed_translate() returns refuse=False on CLAIM_ABSENT (see its
    # `if state == claim_record.CLAIM_ABSENT: return False, ...` branch), so run()
    # proceeds to adopt_pending()/launch() over a segment another run holds.
    #
    # Refusing costs nothing legitimate: --expect-token is required=True (see
    # _build_parser()), and EVERY legitimate value is <RUN_ID>:<seg> or
    # <RUN_ID>:<seg>:r<label> -- mass-translate-wf.template.js builds RUN_ID + ":" +
    # seg (and + ":r" + roundLabel), segment_dispatch_driver.py's
    # translate_dispatch_token()/review_dispatch_token() build the same two shapes
    # from ctx.run_id -- and a validated RUN_ID can never contain a ':' itself
    # (claim_record.validate_run_id()'s own pattern excludes it), so the run
    # component is always exactly the text before the FIRST colon. A token with no
    # colon, or with an empty leading component, is therefore not a case to tolerate
    # but a mis-wired caller, and the whole point of #438 is that a chokepoint must
    # answer a mis-wired caller loudly instead of degrading into "cannot compare, so
    # proceed".
    token_run_id, token_colon, _token_rest = args.expect_token.partition(":")
    if not token_colon or not token_run_id:
        print(
            "Error: --expect-token %r carries no run component -- it must be "
            "spelled <RUN_ID>:<seg> (translate) or <RUN_ID>:<seg>:r<label> "
            "(review). Without one there is nothing to check --run-id against, and "
            "an unchecked --run-id is free to name a foreign claim namespace: the "
            "lookup at runs/<RUN_ID>/.claimed.<seg> then finds nothing, which reads "
            "as 'not claimed' and would let a claimed segment be re-translated over."
            % (args.expect_token,),
            file=sys.stderr,
        )
        return 2
    if token_run_id != args.run_id:
        print(
            "Error: --expect-token names run %r but --run-id is %r -- the claim "
            "namespace this driver reads (runs/<RUN_ID>/.claimed.<seg>) must be "
            "the same run the token dispatches for. A mismatch looks up a claim "
            "that cannot exist, which reads as 'not claimed' and would let a "
            "claimed segment be translated." % (token_run_id, args.run_id),
            file=sys.stderr,
        )
        return 2
    if not os.path.isfile(args.companion):
        print("Error: --companion not found: %s" % args.companion, file=sys.stderr)
        return 2
    # Resolved exactly ONCE, right here, and reused for both the validation
    # below and the CodexJob() construction further down -- never re-derived
    # from the raw `args.plugin_root` string a second time. A second
    # `os.path.realpath()` call on the raw string would open a TOCTOU window
    # (a symlink swapped between the two resolutions could make them
    # disagree, so a caller passing a validated PATH could still end up with
    # a CodexJob resolving somewhere else); not a live attack in this
    # threat model (an actor able to swap a symlink INSIDE --plugin-root
    # already has write access to the one tree #412 exists to keep
    # write-inaccessible to codex, which is a strictly worse position than
    # winning this race), but resolving once removes the possibility of the
    # two ever meaning something different -- the exact "two definitions of
    # the same value can drift" shape the empty-string bug above was.
    resolved_plugin_root = None
    if args.plugin_root is not None:
        # #412: fail loudly at usage time on a misconfigured --plugin-root
        # (e.g. a typo, or a plugin layout that predates assets/scripts/)
        # rather than silently falling through _gate()'s own OSError->None
        # handling later, which would otherwise be indistinguishable from an
        # ordinary "gate ran out of budget" case.
        #
        # Trust-boundary fix: an EMPTY or whitespace-only --plugin-root
        # (e.g. a `{{PLUGIN_ROOT}}` template substitution that silently
        # resolved to nothing) is rejected HERE, explicitly, rather than
        # being let through to CodexJob.__init__ -- which used to test
        # `plugin_root` for TRUTHINESS (`if plugin_root else None`), so an
        # empty string (is-not-None, but falsy) was silently treated as
        # "not given" and fell back to SCRIPTS_DIR, the self-anchored,
        # codex-WRITABLE durable-root copy -- defeating this whole redirect
        # while this very check below still validated (and passed for)
        # `os.path.realpath("")`, which resolves to the CURRENT WORKING
        # DIRECTORY, not the empty string the operator actually passed. A
        # caller who set --plugin-root at all believed the redirect was
        # active; silently discarding it is exactly the failure mode this
        # flag exists to close. Confirmed exploitable: with the durable
        # root's own draft_ready.py poisoned (a stub that accepts anything)
        # and cwd/assets/scripts present, `--plugin-root ""` passed this
        # directory check, then silently used the poisoned copy and wrongly
        # promoted a wrong-token attempt.
        if not args.plugin_root.strip():
            print(
                "Error: --plugin-root was given but is empty/whitespace-only "
                "-- this usually means a {{PLUGIN_ROOT}} template "
                "substitution did not happen. Omit the flag entirely for "
                "today's self-anchored behavior, or pass a real path.",
                file=sys.stderr,
            )
            return 2
        resolved_plugin_root = os.path.realpath(args.plugin_root)
        plugin_scripts_dir = os.path.join(resolved_plugin_root, "assets", "scripts")
        if not os.path.isdir(plugin_scripts_dir):
            print(
                "Error: --plugin-root %s does not resolve to a directory containing "
                "assets/scripts/ (resolved: %s)" % (args.plugin_root, plugin_scripts_dir),
                file=sys.stderr,
            )
            return 2
    try:
        prompt_text = open(args.prompt_file, "r", encoding="utf-8").read()
    except OSError as exc:
        print("Error: --prompt-file unreadable (%s)" % exc, file=sys.stderr)
        return 2
    if prompt_text.count(JOB_OUT_PLACEHOLDER) != 1:
        print("Error: --prompt-file must contain EXACTLY one JOB_OUT placeholder", file=sys.stderr)
        return 2

    poll_sec = args.poll_sec if args.poll_sec > 0 else 15
    job = CodexJob(
        kind=args.kind, seg=args.seg, tok=args.expect_token, disp=args.disp, root=args.cwd,
        companion=args.companion, prompt_text=prompt_text, prompt_file=args.prompt_file,
        deadline_sec=args.deadline_sec, poll_sec=poll_sec, effort=args.effort, node=args.node,
        model=args.model, plugin_root=resolved_plugin_root, run_id=args.run_id,
    )
    return job.run()


if __name__ == "__main__":
    sys.exit(main())
