#!/usr/bin/env python3
"""codex_job.py -- shipped, isolating, validate-before-promote codex-job driver (#198, v1.4.7).

Drives ONE codex `task` job to a terminal state and, on success, VALIDATES the
isolated attempt artifact and only then ATOMICALLY PROMOTES it into its canonical
`segments/<seg>.<draft|review>.json` path. Codex stays the sole translator/reviewer;
this driver only launches it, polls to terminal, pre-filters the result, and
os.replace()s a validated attempt into place. Claude still only drives/polls/fixes.

Design (PLAN-198 §2.1; the 7 steps below map 1:1):
  1. Validate args + establish TWO absolute time ceilings (poll window + finalize budget).
     EVERY subprocess.run gets an explicit stdlib timeout= (NO external `timeout` binary).
  2. Isolate codex output: substitute the single ⟦JOB_OUT⟧ placeholder in the prompt-file
     with the per-run attempt path, so a zombie codex that OBEYS only ever writes an
     abandoned attempt file, never the canonical.
  3. Acquire an exclusive per-seg DRIVER lease via a KERNEL fcntl.flock on a never-unlinked
     sentinel `.codex_job.<seg>.lock` (kernel auto-releases on crash -- no stale-break race).
     A lease-loser writes ONLY its own fail sentinel + stdout, NEVER the hygiene joblog.
  4. Hygiene (cancel a verified-same-workspace stale prior job) -> safe adoption of an
     already-valid same-token canonical -> adopt a prior run's DEFERRED completed attempt
     (#213; re-validated through the same candidate gates before promotion) -> else launch
     fresh (detached background codex).
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
     hold the lease), and clean this invocation's OWN scratch by exact path.

CLI (canonical path is DERIVED, never caller-supplied):
    python3 codex_job.py --kind {translate|review} --companion <abs codex-companion.mjs>
      --cwd <durable_root> --seg <seg> --prompt-file <abs prompt with EXACTLY one ⟦JOB_OUT⟧>
      --expect-token <RUN_ID:seg|RUN_ID:seg:r<label>> --disp <per-dispatch nonce>
      --deadline-sec <int> [--poll-sec <int default 15>]
      [--write] [--fresh] [--effort high] [--model <model>] [--node <exe default "node">]

Exit codes: 0 = promoted (or adopted) a validated artifact; 1 = launch/run/validate failure
(recoverable, wrote an empty fail sentinel); 2 = usage/env error.

stdlib-only, self-anchoring (sibling gate scripts located via __file__); copied to
<durable_root>/scripts/ at Step 0a (it IS a PLUGIN_BUNDLE_MEMBERS script -- see cache_key.py).
"""

import argparse
import fcntl
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import time
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

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))


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


class CodexJob:
    def __init__(self, kind, seg, tok, disp, root, companion, prompt_text, prompt_file,
                 deadline_sec, poll_sec, effort, node, model=None):
        self.kind = kind
        self.seg = seg
        self.tok = tok
        self.disp = disp
        self.root = os.path.realpath(root)
        self.companion = companion
        self.prompt_text = prompt_text
        self.prompt_file = prompt_file
        self.poll_sec = poll_sec
        self.effort = effort
        self.node = node
        self.model = model

        self.inv = os.urandom(8).hex()
        self.segdir = os.path.join(self.root, "segments")
        ext = "draft" if kind == "translate" else "review"
        self.canonical = canonical_path(self.root, seg, kind)
        self.attempt = os.path.join(self.segdir, ".att.%s.%s.%s.json" % (seg, self.inv, ext))
        self.pending = os.path.join(self.segdir, ".att_pending.%s.%s.json" % (seg, ext))
        self.lock = os.path.join(self.segdir, ".codex_job.%s.lock" % seg)
        self.joblog = os.path.join(self.segdir, ".codex_job.%s.json" % seg)
        self.fail_sentinel = os.path.join(self.segdir, ".codex_failed.%s.%s" % (seg, disp))
        self.final_prompt = None

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

    def _gate(self, args, timeout):
        script = os.path.join(SCRIPTS_DIR, args[0])
        return self._run([sys.executable, script] + list(args[1:]), timeout)

    # ---- shared regular-file / candidate-gate helpers (#213) ----------------
    def _is_regular(self, path):
        """O_NOFOLLOW|O_NONBLOCK open + S_ISREG: reject a symlink, FIFO, dir, or absent file."""
        try:
            fd = os.open(path, os.O_RDONLY | _O_NOFOLLOW | _O_NONBLOCK | _O_CLOEXEC)
        except OSError:
            return False
        try:
            st = os.fstat(fd)
        finally:
            os.close(fd)
        return stat.S_ISREG(st.st_mode)

    def _clear_nonregular(self, path):
        """Remove a NON-REGULAR entry squatting on a deterministic driver slot so it cannot
        permanently block an os.replace into that slot (#213). A regular file is LEFT untouched
        (callers overwrite it via os.replace or delete it via _silent_remove). lstat (never
        follows) classifies: a symlink/FIFO/socket is unlinked as the entry itself; a real
        directory is removed recursively (the slot is never legitimately a directory). Best-effort."""
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

    def _validate_candidate(self, candidate, timeout_fn):
        """Kind-specific candidate-file gate against `candidate`; each gate call is bounded by a
        FRESH timeout_fn() (remaining budget re-read per call). Returns True iff every gate PASSED
        (an _ok()-True). Used by validate_attempt (attempt path)."""
        if self.kind == "translate":
            if not _ok(self._gate(["draft_ready.py", self.seg, "--expect-token", self.tok,
                                   "--candidate-file", candidate], timeout_fn())):
                return False
            return _ok(self._gate(["validate_draft.py", self.seg, "--candidate-file", candidate],
                                 timeout_fn()))
        return _ok(self._gate(["review_ready.py", self.seg, "--expect-token", self.tok,
                              "--candidate-file", candidate], timeout_fn()))

    # ---- step 2: isolate codex output ---------------------------------------
    def _write_final_prompt(self):
        final = os.path.join(self.segdir, ".codex_prompt.%s.%s.txt" % (self.seg, self.inv))
        text = self.prompt_text.replace(JOB_OUT_PLACEHOLDER, self.attempt)
        with open(final, "w", encoding="utf-8") as fh:
            fh.write(text)
        self.final_prompt = final

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

    # ---- step 4: workspace root, hygiene, adoption, launch ------------------
    def resolve_expected_ws_root(self):
        """Replicate the companion's resolveWorkspaceRoot(<root>): git top-level of
        <root> if a repo, else <root> -- so the hygiene guard field matches exactly."""
        proc = self._run(["git", "-C", self.root, "rev-parse", "--show-toplevel"],
                         self.poll_timeout())
        if _ok(proc):
            out = proc.stdout.strip()
            if out:
                return out
        return self.root

    def read_joblog(self):
        try:
            with open(self.joblog, "r", encoding="utf-8") as fh:
                obj = json.load(fh)
            return obj if isinstance(obj, dict) else None
        except (OSError, ValueError):
            return None

    def _write_joblog(self, obj):
        """Atomic never-torn write via O_EXCL/O_NOFOLLOW tmp + os.replace. Best-effort."""
        tmp = os.path.join(self.segdir, ".codex_job.%s.%s.tmp" % (self.seg, self.inv))
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | _O_CLOEXEC | _O_NOFOLLOW
        try:
            fd = os.open(tmp, flags, 0o600)
        except OSError:
            return
        try:
            os.write(fd, json.dumps(obj).encode("utf-8"))
        finally:
            os.close(fd)
        try:
            os.replace(tmp, self.joblog)
        except OSError:
            _silent_remove(tmp)

    def hygiene(self, expected_ws):
        """Cancel a still-active prior job ONLY IF its status.workspaceRoot matches the
        expected root AND its status is active. The joblog is codex-writable, so a forged
        jobId is verified (and one naming a different-store job is NOT FOUND -> no cancel)."""
        prior = self.read_joblog()
        if not prior or prior.get("status") != "launched":
            return
        pj = prior.get("jobId")
        if not isinstance(pj, str) or not pj:
            return
        proc = self._run([self.node, self.companion, "status", pj, "--json", "--cwd", self.root],
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
        if ws == expected_ws and job.get("status") in _ACTIVE:
            self._run([self.node, self.companion, "cancel", pj, "--cwd", self.root],
                      self.poll_timeout())

    def safe_adopt(self):
        """A pre-existing valid same-token canonical -> adopt without relaunching."""
        if not os.path.exists(self.canonical):
            return False
        if self.kind == "translate":
            if not _ok(self._gate(["draft_ready.py", self.seg, "--expect-token", self.tok],
                                  self.poll_timeout())):
                return False
            return _ok(self._gate(["validate_draft.py", self.seg], self.poll_timeout()))
        return _ok(self._gate(["review_ready.py", self.seg, "--expect-token", self.tok],
                             self.poll_timeout()))

    def adopt_pending(self):
        """#213: try to adopt a completed-but-unvalidated attempt DEFERRED by a prior run of the same
        seg/kind. Validate through the same candidate gates (which also enforce --expect-token against
        the candidate's own dispatch_token) and, only on a FULL PASS, atomically promote it -> return
        True. Return False (caller launches fresh codex) in every other case, handling the pending
        file so it is never lost or left to block a future run:
          - absent / a non-regular squatter -> cleared, return False;
          - a gate that RAN and REJECTED the candidate (proc.returncode != 0: bad content / stale
            cross-run token) -> DISCARD the pending, return False;
          - a gate that could NOT run (proc is None: exhausted budget / timeout / spawn fail) -> LEAVE
            the pending intact for a future run (never delete recoverable work), return False.
        Never promotes unvalidated content; runs before launch, so uses the poll-window budget. Because
        False always falls through to launch(), the no-budget case cannot starve (MINOR-1)."""
        if not self._is_regular(self.pending):
            self._clear_nonregular(self.pending)
            return False
        gates = ([("draft_ready.py", True), ("validate_draft.py", False)]
                 if self.kind == "translate" else [("review_ready.py", True)])
        for name, with_token in gates:
            argv = [name, self.seg]
            if with_token:
                argv += ["--expect-token", self.tok]
            argv += ["--candidate-file", self.pending]
            proc = self._gate(argv, self.poll_timeout())
            if proc is None:
                return False                       # could not validate -> keep pending, launch fresh
            if proc.returncode != 0:
                _silent_remove(self.pending)       # gate ran & rejected -> discard stale/bad, launch fresh
                return False
        os.replace(self.pending, self.canonical)   # every gate passed
        return True

    def launch(self):
        # ALWAYS workspace-write (codex MUST write its ⟦JOB_OUT⟧ attempt -- read-only was
        # the #198 no-output failure) and a FRESH per-attempt codex thread. `--effort`
        # defaults to "high" (belt-and-suspenders with the prompt's own effort opener).
        argv = [self.node, self.companion, "task", "--background", "--json", "--write", "--fresh"]
        if self.effort:
            argv += ["--effort", self.effort]
        if self.model:
            argv += ["--model", self.model]
        argv += ["--cwd", self.root, "--prompt-file", self.final_prompt]
        proc = self._run(argv, self.poll_timeout())
        if not _ok(proc):
            return False
        try:
            obj = json.loads(proc.stdout)
        except ValueError:
            obj = None
        jid = obj.get("jobId") if isinstance(obj, dict) else None
        if not isinstance(jid, str) or not jid:
            return False
        self.jobId = jid
        self._write_joblog({
            "jobId": jid, "kind": self.kind, "seg": self.seg, "token": self.tok,
            "disp": self.disp, "inv": self.inv, "status": "launched",
        })
        return True

    # ---- step 5: poll to terminal or the poll deadline ----------------------
    def poll(self):
        while True:
            if self.poll_remaining() <= 0:
                break
            proc = self._run([self.node, self.companion, "status", self.jobId, "--json",
                             "--cwd", self.root], self.poll_timeout())
            if _ok(proc):
                try:
                    obj = json.loads(proc.stdout)
                    job = obj.get("job") if isinstance(obj, dict) else None
                    if isinstance(job, dict):
                        self.job_status = job.get("status")
                except ValueError:
                    pass
            if self.job_status in _TERMINAL:
                return
            rem = self.poll_remaining()
            if rem <= 0:
                break
            time.sleep(min(self.poll_sec, rem))
        if self.job_status not in _TERMINAL:
            # Poll deadline reached while (possibly) active -> cancel, finalize-bounded.
            self._run([self.node, self.companion, "cancel", self.jobId, "--cwd", self.root],
                      self.finalize_timeout())
            self.timed_out = True

    # ---- step 6: validate the attempt (kind-specific candidate gate) --------
    def validate_attempt(self):
        if not self._is_regular(self.attempt):
            return False
        return self._validate_candidate(self.attempt, self.finalize_timeout)

    def _defer_attempt(self):
        """#213: atomically move a completed-but-unvalidated attempt into the stable per-seg/kind
        pending slot so the NEXT run's adopt_pending() can validate + adopt it, instead of
        discarding a rare late-completing codex result. Clears any non-regular squatter on the slot
        first so the rename cannot fail into finalize()'s discard. Returns True iff a real regular
        attempt file was preserved. Promotes NOTHING.

        The single per-seg/kind slot deliberately retains the MOST RECENT completed attempt
        (last-writer-wins). Validity cannot be determined at defer time -- the defer is triggered
        precisely because no budget remained to run the candidate gate -- so preferentially KEEPING
        an existing pending over a fresh attempt risks sticking on an unadoptable one (a same-token
        but structurally invalid pending would be kept forever while valid fresh attempts are
        discarded). Always refreshing the slot instead guarantees it tracks the latest completion
        and can NEVER get stuck: an invalid pending is superseded by the next fresh attempt, and
        adopt_pending() discards it outright the first time a gate can actually run. Superseding an
        equal-status older attempt is a bounded cost, and still strictly better than the pre-#213
        status quo (which discarded EVERY tail-completed attempt). A multi-slot queue would only
        trade this bounded, self-healing residual for unbounded pending-file accumulation (or, if
        capped, the same discard at the cap) for no convergence benefit -- adopt_pending() promotes
        the first candidate that passes, and a failing one is superseded by the next launch anyway."""
        if not self._is_regular(self.attempt):
            return False
        self._clear_nonregular(self.pending)
        try:
            os.replace(self.attempt, self.pending)
        except OSError:
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
        if not self.promoted:
            _silent_remove(self.attempt)  # the os.replace consumed it iff promoted
        _silent_remove(self.final_prompt)
        _silent_remove(self.prompt_file)
        # Terminal hygiene joblog ONLY IF we hold the lease (a lease-loser must never clobber
        # the live holder's control state -- HIGH-3 r8).
        if self.holds_lock:
            self._write_joblog({
                "jobId": self.jobId, "kind": self.kind, "seg": self.seg, "token": self.tok,
                "disp": self.disp, "inv": self.inv, "status": "terminal", "ok": self.ok,
                "timed_out": self.timed_out, "job_status": self.job_status, "adopted": self.adopted,
            })
        line = {
            "ok": self.ok, "kind": self.kind, "seg": self.seg, "jobId": self.jobId,
            "job_status": self.job_status, "timed_out": self.timed_out,
            "adopted": self.adopted, "reason": self.reason,
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
            self._write_final_prompt()
            lock_fd = os.open(self.lock, os.O_CREAT | os.O_RDWR | _O_CLOEXEC, 0o600)
            self.holds_lock = self._acquire_flock(lock_fd)
            if not self.holds_lock:
                # Lease held past our poll window -> recoverable; re-dispatch on the NEXT W5 run.
                self.reason = "lease-held"
                return 1
            expected_ws = self.resolve_expected_ws_root()
            self.hygiene(expected_ws)
            if self.safe_adopt():
                _silent_remove(self.pending)          # canonical already valid -> any deferred attempt is moot
                self.adopted = True
                self.reason = "adopted"
                return 0
            if self.adopt_pending():                  # NEW: promote a prior run's deferred completed attempt
                self.adopted = True
                self.reason = "adopted-pending"
                return 0
            if not self.launch():                     # False (incl. no-budget, pending kept) -> launch fresh
                self.reason = "launch-failed"
                return 1
            self.poll()
            if self.job_status == "completed" and self.abs_remaining() > FINALIZE_TAIL:
                if self.validate_attempt():
                    os.replace(self.attempt, self.canonical)
                    self.promoted = True
                    self.reason = "promoted"
                    return 0
                self.reason = "validate-failed"
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
    if not os.path.isfile(args.companion):
        print("Error: --companion not found: %s" % args.companion, file=sys.stderr)
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
        model=args.model,
    )
    return job.run()


if __name__ == "__main__":
    sys.exit(main())
