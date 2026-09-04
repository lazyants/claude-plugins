#!/usr/bin/env python3
"""glossary_dispatch_driver.py -- local driver for the W3a canon-and-glossary pass (#800).

Runs the glossary pass's DETERMINISTIC steps in this process instead of paying a
subagent bootstrap for each of them, and hands back to the orchestrating session for
the ONE step that must stay an agent: the citation judge.

WHY THIS EXISTS. `glossary-pass-wf.template.js` is a Workflow script, and a Workflow
script cannot run bash. So every deterministic step in it -- reaching codex, polling a
file, snapshotting a fragment, fetching citations, writing a verdict record -- costs a
full `agent()` call whose entire content is "now run this command". #724 removed the two
that wrapped a single command each; the dispatch and the chunked wait remained, at up to
`1 + (2 + WAIT_CALLS) * (MAX_CITATION_RETRIES + 1)` calls per batch. Measured on a live
22-batch, 850-candidate volume: ~130 agent calls, against 24 driving the same gates from
a local process. This is the same case #409 made for W5 and #516 settled by making
`segment_dispatch_driver.py` the default there; the glossary pass was left behind.

THE GATES ARE UNCHANGED. This driver decides nothing about a name. It runs exactly the
commands the template builds -- `canon_validate.py --check-batch` (same flags, same
order), `--approve-to`, `--record-approval-to`, `--merge-batches`, `--verify-merged`,
and `fetch_citation.py --batch` -- and it obtains every one of them, plus every prompt,
by EXECUTING the template's own builder functions under Node. There is no second copy of
any prompt text or command line in this file. THE IRON RULE holds: this script surfaces
and enforces, codex decides, the judge audits.

THE ONE AGENT CALL, AND WHY IT STAYS ONE. The citation judge reads attacker-authored page
bodies. Since #353 its capability boundary is the harness's, not a prompt's promise: the
`literary-translator:citation-judge` agent holds `tools: Read` and nothing else. A local
Python process cannot dispatch a Claude agent, so the driver STOPS at that point, prints
the rendered judge prompt in its `needs_judge[]` hand-back, and the session dispatches
one agent per entry -- in parallel, which is the actual win: N judges in one round rather
than N serial ladders. The session then feeds the replies back via `--record-verdicts`.
Same shape `segment_dispatch_driver.py` uses for `needs_fix`.

THE #347 BOUNDARY, RESTATED AS A PROCESS PROPERTY. The actor that chooses which URLs to
retrieve must never read what was retrieved. In the Workflow that was a prompt clause; here
it is what this file does and does not do. This driver launches `fetch_citation.py` and then
reads, from its output, EXACTLY TWO fields per entry: `item_index` (that script's own loop
counter) and `outcome` (its own closed vocabulary). It never opens an `evidence_file`, and
never reads `source`, `source_form`, `final_origin`, `chain`, `content_type` or `bytes`.
Everything a cited page could influence stays unread until the tool-restricted judge reads it.

IN-PLACE REPAIR, AND WHY ITS SELECTOR IS NOT THE JUDGE. A fragment whose citations do not
retrieve is repaired per row rather than re-rolled whole: measured on the same volume, 29 of
143 established citations did not retrieve at all and no batch of 22 was clean, so the
whole-fragment ladder re-decides ~40 rows to fix a handful -- from the same distribution that
produced the bad URLs (18 of the 29 were one host answering 404, a URL-construction pattern a
re-roll re-draws). The repair set is derived from `fetch_citation.py`'s own `outcome` field,
NEVER from the judge's prose: a judge-authored item list is model output derived from
attacker-authored page bodies, so a hostile page cited for row A could name valid row B and
have B silently re-decided. The repair therefore runs BEFORE the judge, over `established`
rows only -- which also means that once a judge is dispatched at all, every citation in the
batch retrieved, so a rejection can only be about CONTENT and takes the whole-fragment ladder.

WHAT THE HAND-BACK CHANNEL DOES AND DOES NOT DEFEND. A verdict names one snapshot's bytes,
one PREPARE, one run, and is consumed once; the state saying so lives outside `durable_root`,
where no dispatched codex job can write. That closes the class the approval record closes --
the command that never ran, the verdict never produced, the stale verdict replayed after a
resume. It also no longer rests on the dispatched job's good behaviour for the WRITE half.

WHAT A DISPATCHED JOB MAY WRITE (1.76.0, #806). Every codex job this driver launches runs
with `--cwd` pointed at a fresh single-use directory that has been VERIFIED to have no
enclosing git repository, so codex-companion's own workspace-write resolution confines it to
that one directory; the driver publishes the artifact into `RUN_DIR` itself afterwards, from
bytes it re-reads with a no-follow walk and re-digests. A dispatched job therefore reaches
NOTHING under `durable_root`: not `canon.json`, not the snapshot, approval record, evidence
or `index.json` under `RUN_DIR`, not the gate scripts this driver shells
(`canon_validate.py`, `fetch_citation.py`), and not this file itself at its deployed
`${durable_root}/scripts/` path, which the documented two-invocation loop re-enters. See
`DispatchSandbox` for why only a directory with no enclosing repository achieves this, and
why pointing `--cwd` at `RUN_DIR` would have achieved nothing for the supported
`durable_root == project root` layout.

WHAT THAT STILL DOES NOT BUY, stated because a partial statement would imply more. The
`pipeline()` fallback below is UNCHANGED and wider: a Workflow script reaches codex through
the `codex:codex-rescue` forwarder, whose workspace-write root resolves by git walk-up from
whatever cwd the session happens to hold, and this plugin has no `--cwd` to give it. The
pass's own Claude turns -- the PREPARE that fetches citations -- still write under `RUN_DIR`
by design, so `RUN_DIR` is not a read-only region; what changed is that the untrusted
DISPATCHED job is no longer one of its writers. And confinement is about WRITES, never
reads: a job still reads `glossary_TASK.md`, `canon.json` and `style_bible.md` from the
durable root, which is exactly what it is for.

THE TEMP ROOTS ARE THE ONE EXCEPTION, and it is measured rather than assumed: under
`workspace-write` codex 0.151.0 also grants `/tmp` and `$TMPDIR`, verified by probing
`codex sandbox` directly (a write to each SUCCEEDS from a workspace elsewhere, while a
write to a path in $HOME is refused). So a `durable_root` -- or a `verdict_dir` -- that
itself lies under a temp root is still writable by a dispatched job, and no `--cwd` this
process can pass changes that: codex's own switch for excluding the temp roots would
exclude the sandbox too, which lives under TMPDIR. `refuse_if_under_a_temp_root()`
therefore REFUSES such a run outright, before any work -- the boundary cannot be
obtained there, and proceeding would advertise a confinement over the two paths whose
integrity the rest of this file exists to protect.

THE FALLBACK IS NOT RETIRED. `pipeline()` remains a shipped, documented path for this pass.
Per #436/#516's ordering rule, a fallback is not removed before its replacement has carried
a book end to end.

CLI:
    python3 glossary_dispatch_driver.py --run-id <RUN_ID> --batches-file <path>
      --verdict-dir <session-owned dir OUTSIDE durable_root>  [REQUIRED]
      [--plugin-root <plugin install root>] [--poll-sec 15] [--deadline-sec 2700]
      [--node node] [--only-batches 0,3,7]
    python3 glossary_dispatch_driver.py --record-verdicts <path> --verdict-dir <same dir>
      [... same root args]

Output: exactly ONE JSON line to stdout, all human detail to stderr.
Exit 0 = the pass advanced (read the JSON: `merged`, `needs_judge`, `not_ready`);
1 = a gate refused or a batch failed; 2 = usage/environment error.

stdlib-only, self-anchored (`Path(__file__).resolve().parents[1]`), no shared util module --
so the two no-follow filesystem helpers below are BYTE-IDENTICAL copies of
`segment_dispatch_driver.py`'s, per this plugin's duplicate-never-import convention, and
`tests/glossary_driver_helper_drift.test.py` pins them equal.
"""

import argparse
import errno
import fcntl
import hashlib
import importlib.util
import json
import os
import re
import secrets
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import NoReturn

# ---------------------------------------------------------------------------
# Self-anchored paths. A deployed copy under ${durable_root}/scripts/ can go
# stale relative to the plugin tree, so every sibling this file runs is resolved
# from ITS OWN location -- never from cwd, never from a --durable-root flag.
# ---------------------------------------------------------------------------
SCRIPTS_DIR = Path(__file__).absolute().parent

DURABLE_ROOT = SCRIPTS_DIR.parent
RESOLVE_COMPANION_SCRIPT = SCRIPTS_DIR / "resolve_codex_companion.py"

TEMPLATE_NAME = "glossary-pass-wf.template.js"

# The template's ten substitution tokens and how each is spliced. Read off the
# `const X = ...` line that consumes each one, never guessed:
#   "quoted" -- const X = "{{TOKEN}}"  : the template supplies the quotes, so
#               substitute JSON-escaped content with its own quotes stripped.
#   "int"    -- const X = {{TOKEN}}    : a bare integer literal.
#   "json"   -- const X = {{TOKEN}}    : the value supplies its own quotes
#               (PLUGIN_ROOT, a string) or its own brackets
#               (RESUMED_BATCH_INDICES, a bare array literal -- the template
#               refuses a non-array, so it must NOT arrive quoted).
_TEMPLATE_TOKEN_STYLE = {
    "DURABLE_ROOT": "quoted",
    "SOURCE_LANG": "quoted",
    "TARGET_LANG": "quoted",
    "RESEARCH_MODE": "quoted",
    "RUN_ID": "quoted",
    "EFFORT": "quoted",
    "CITATION_CONTENT_TYPES": "quoted",
    "BATCH_AGENT_CAP": "int",
    "PLUGIN_ROOT": "json",
    "RESUMED_BATCH_INDICES": "json",
}

# The three literals the harness matches EXACTLY. Each is a shape claim about the
# template, and each is fail-loud rather than fail-quiet: a template edit that
# moves any of them raises rather than silently mis-wrapping.
_EXPORT_META_LITERAL = "export const meta = {"
_EXPORT_META_REPLACEMENT = "const meta = {"
_TRUNCATE_BEFORE_MARKER = "const batchResults = await pipeline("

# Every builder the driver calls. Exported by the harness wrapper; a name here
# that the template does not define fails at harness time, not at use time.
TEMPLATE_EXPORTED_FUNCTIONS = (
    "fragmentPath", "manifestPath", "checkBatchCmd", "sandboxCheckBatchCmd",
    "approvedPath", "approveBatchCmd",
    "approvalRecordPath", "recordApprovalCmd",
    "evidenceDir", "evidenceIndexPath", "fetchCitationsCmd",
    "batchDispatchPrompt", "batchRepairPrompt", "repairFragmentPath",
    "citationJudgePrompt",
    "mergeBatchesCmd", "verifyMergedCmd",
    "rejectionDetail", "sentinelVerdict", "rejectedAnywhere",
)

# The routing token `batchDispatchPrompt` and `batchRepairPrompt` emit as their
# FIRST line. It is a control for the codex:codex-rescue forwarder, which strips
# it before codex sees the task; this driver shells the companion itself and so
# passes --background as a real flag instead. The prompt is sent without it.
#
# STRIPPED, NOT EXTRACTED, and the reason is measured rather than stylistic: six
# glossary test files reference batchDispatchPrompt, and glossary_epithet_rule
# .test.py slices its function BODY and asserts prompt prose inside it, while
# bounded_poll_present.test.py asserts that the function itself pushes this token
# unconditionally as its opening line. Splitting the prose into a task-text
# builder would retarget all of them to buy what one asserted identity gives:
# tests/glossary_driver_prompt_parity.test.py checks
# `prompt == ROUTING_LINE + "\n" + sent`, so the convention is asserted here, not
# duplicated as a rule.
_ROUTING_LINE = "--background"

# `fetch_citation.py`'s own success value. Everything else is a failure, and its
# vocabulary is that script's own (`http_error:<status>`, `refused:<...>`).
_FETCH_OK = "fetched"

# Failures that are about THIS RUN's shared budget rather than about a citation.
# `fetch_citation.py` records both as soft failures and still exits 0, so without
# an explicit branch they would fall through to the judge -- which would reject
# for want of evidence and have that rejection misread as a content rejection.
# They are also the one lever by which a hostile server can push a DIFFERENT
# row into the failure set (by consuming the shared time or byte budget), which
# is the second reason they are never repaired.
_SHARED_BUDGET_OUTCOMES = frozenset({
    "refused:batch-deadline",
    "refused:batch-byte-budget",
})

DEFAULT_POLL_SEC = 15
DEFAULT_DEADLINE_SEC = 2700

_RUN_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


class DriverError(Exception):
    """Carries a machine-readable payload folded into the failure JSON."""

    def __init__(self, message: str, **extra):
        super().__init__(message)
        self.extra = extra


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# --- the shared one-line JSON serialiser (#369) -----------------------------
# Loaded by EXACT PATH, never `import json_stdout`, for the reason
# segment_dispatch_driver.py states at its own copy of this block: a bare
# sibling import resolves through the global sys.modules cache regardless of
# which staged copy the CALLER intended, so one process staging several durable
# roots would bind the FIRST root's copy for all of them. exec_module() opens
# this file's own sibling or raises. `.absolute()` rather than `.resolve()`,
# so a caller's own no-follow logic still sees the path it was handed.
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
        f"glossary_dispatch_driver.py: cannot load json_stdout.py from "
        f"{_JSON_STDOUT_PATH} ({_json_stdout_exc}).\n"
        "json_stdout.py must be installed alongside glossary_dispatch_driver.py "
        "under ${durable_root}/scripts/ -- Step 0a's copy pass places it there."
    )

dumps_line = _json_stdout.dumps_line


def fatal(message: str, exit_code: int = 2, **extra) -> NoReturn:
    """A fatal error prints a named line to stderr ONLY and prints NO stdout
    JSON -- nothing this process emits on stdout may ever be mistaken for a
    schema-conforming result (the review_artifact_check.py discipline)."""
    detail = ""
    if extra:
        detail = " " + json.dumps(extra, ensure_ascii=False, sort_keys=True)
    print(f"FATAL: {message}{detail}", file=sys.stderr)
    sys.exit(exit_code)


def log(message: str) -> None:
    """All human detail goes to stderr, unbuffered enough to be useful while a
    long poll is running (#434: a progress log that only appears at exit is not
    a progress log)."""
    print(message, file=sys.stderr, flush=True)


def emit(payload: dict) -> None:
    """The ONE JSON line on stdout, through the shared serialiser.

    #369: U+0085, U+2028 and U+2029 survive `ensure_ascii=False` raw, and a
    payload carrying one renders to the reading agent as TWO physical lines --
    so the session parses a truncated object and this driver's whole result is
    lost. Source forms come from a book; those characters are not hypothetical
    here. `tests/stdout_json_line_escape_gate.test.py` admits no exemptions."""
    print(dumps_line(payload, sort_keys=True), flush=True)


# ---------------------------------------------------------------------------
# BYTE-IDENTICAL COPIES -- do not edit here alone.
#
# This plugin ships no shared util module: every script is self-contained, so a
# cross-cutting helper is DUPLICATED byte-for-byte rather than imported. These
# two come from segment_dispatch_driver.py and must stay equal to it;
#
# KNOWN WART, kept ON PURPOSE rather than fixed here: the walk's two stderr
# warnings hardcode the string "segment_dispatch_driver.py", so a refusal raised
# while THIS driver is running names the wrong script. The path in the message is
# still correct and specific, so an operator is not left without the offending
# artifact. Parameterising the prefix was implemented and reverted: it requires
# editing the sibling too (byte-identity is the whole point of this duplication),
# and inserting one constant there shifted its line numbers enough to drift 19
# `file:NNN` citations across the test suite. That is a large, unrelated diff to
# buy a cosmetic improvement to a message that fires only when the no-follow walk
# has already refused something. Tracked separately instead.
# tests/glossary_driver_helper_drift.test.py compares the two files' copies and
# fails if either side is touched alone.
# ---------------------------------------------------------------------------
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


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _atomic_write_json(path: Path, obj) -> None:
    """tmp -> os.replace, in the SAME directory, so a partially written file is
    never visible at the target path."""
    data = json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def validate_run_id(name) -> "str | None":
    """Returns a refusal string, or None when the id is safe to splice into a
    filesystem path. Allowlist, never a denylist of shell metacharacters.

    Every refusal below is resume_setup.py's, which OWNS this contract, and the
    set has to match its DECISIONS rather than merely its regex. The regex alone
    admits `a..b`, and this driver's run id names both `glossary/runs/<id>/` and
    the scope of the verdict state document -- so a value this accepts and the
    owner refuses produces a run directory the owner's own candidate scan then
    rejects, aborting a resume before it reaches any good candidate behind it.
    `tests/run_id_pattern_drift.test.py` compares the two answers directly."""
    if not isinstance(name, str) or not name:
        return "run id must be a non-empty string"
    if not _RUN_ID_RE.fullmatch(name):
        return f"unsafe run id: {name!r}"
    if name in (".", ".."):
        return f"run id must not be '.' or '..'; got {name!r}"
    if ".." in name:
        return f"run id must not contain '..'; got {name!r}"
    return None


# ---------------------------------------------------------------------------
# THE TEMPLATE HARNESS -- obtain every prompt and every command by EXECUTING
# glossary-pass-wf.template.js's own builders, never by re-authoring them.
#
# segment_dispatch_driver.py does the same thing for mass-translate-wf.template
# .js by substituting tokens, truncating at a marker, appending an `export {...}`
# line and running the result as plain ESM. THAT SHAPE DOES NOT TRANSFER HERE,
# and the reason is structural rather than stylistic: this template's batch-cap
# preflight ends in a TOP-LEVEL `return {...}` that sits ABOVE every prompt
# builder (the mass template's equivalent sits below its truncation point). A
# retained prefix containing it is a genuine `SyntaxError: Illegal return
# statement` under a plain `import()`. The same prefix also reads `args` at top
# level.
#
# So this harness WRAPS rather than truncating-and-exporting:
#   1. substitute the ten tokens (see _TEMPLATE_TOKEN_STYLE);
#   2. rewrite the one `export const meta = {` into `const meta = {`, because a
#      top-level export cannot appear inside a function body;
#   3. truncate before `const batchResults = await pipeline(`;
#   4. wrap the remainder in `async function __harness(args) { ... return
#      {builders} }` -- top-level `return` and `await` are then legal BY
#      CONSTRUCTION, and `args` is supplied rather than stubbed.
#
# Every one of steps 2 and 3 matches a LITERAL and raises if it is absent: a
# template edit that moves either is caught loudly rather than silently
# mis-wrapping. agent()/log()/pipeline() are stubbed to throw -- they are
# unreachable once the tail is gone, and a stub that throws turns any future
# reachability into a loud failure instead of a silent no-op.
#
# Calling the wrapper with the REAL batches array is deliberate: the template's
# own duplicate-index check and every startup guard (PLUGIN_ROOT quoting,
# CITATION_TYPE_LIST caps, RESEARCH_MODE, the two WAIT_* bounds) then run for
# this driver too, rather than being bypassed by a driver that only wanted the
# functions.
# ---------------------------------------------------------------------------


def resolve_template(plugin_root: "str | None") -> Path:
    """The template's bytes are EXECUTED, so where they come from is a trust
    boundary, not a lookup convenience.

A durable copy of the template would be JavaScript that this process then RUNS,
    sitting in a directory this driver does not own -- which is why there is no
    durable fallback here at all. #806 removed the sharpest writer of that copy
    (a dispatched codex job can no longer reach `${durable_root}/` at all -- see
    the module docstring), and this refusal is deliberately NOT relaxed on the
    strength of it: the `pipeline()` path's jobs are still unconfined, the manual
    W5 drive still runs with `--write` and cwd = durable_root, and a durable copy
    can be stale as easily as it can be hostile. The plugin install tree is the
    only accepted source, and it is the same value SKILL.md already threads as
    {{PLUGIN_ROOT}}.

    Refusing rather than falling back is the point: a fallback would turn a
    missing --plugin-root into silent execution of the weaker copy."""
    if not plugin_root:
        fatal(
            "--plugin-root is required: this driver EXECUTES the template's own "
            "builder functions, and the only copy it will execute is the "
            "plugin install tree's. A durable-root copy is writable by the codex "
            "jobs this driver dispatches, so it is never used as a fallback.",
            exit_code=2,
        )
    candidate = Path(plugin_root).absolute() / "assets" / "templates" / TEMPLATE_NAME
    _refuse_unless_executable_leaf(candidate, "glossary workflow template")
    return candidate


def read_template_text(path: Path) -> str:
    """Reads through the SAME no-follow walk that verified the path, so the
    bytes read are the bytes verified -- a separate open() would reintroduce the
    check-then-use window the walk exists to close."""
    fd, state = _open_regular_no_follow_walk(path)
    if state != "file" or fd is None:
        if fd is not None:
            os.close(fd)
        fatal(
            f"glossary workflow template at {path} is not readable without "
            f"following a symlink (state={state})",
            exit_code=2, template_path=str(path), template_state=state,
        )
    try:
        with os.fdopen(fd, "r", encoding="utf-8") as fh:
            return fh.read()
    except OSError as exc:
        fatal(f"could not read the glossary workflow template at {path}: {exc!r}",
              exit_code=2)


def render_template_source(template_text: str, subst: dict) -> str:
    """Substitutes the ten tokens per the template's own documented contract.

    `subst` keys are the lower-cased token names. Refuses on any surviving
    `{{TOKEN}}`: a token this table does not know about is a token the template
    grew, and substituting the nine it knows would ship a prompt with a literal
    `{{...}}` in it."""
    text = template_text
    for name, style in _TEMPLATE_TOKEN_STYLE.items():
        key = name.lower()
        if key not in subst:
            fatal(f"internal error: no value supplied for template token {name}",
                  exit_code=2)
        value = subst[key]
        if style == "quoted":
            replacement = json.dumps(str(value))[1:-1]
        elif style == "int":
            replacement = str(int(value))
        elif style == "json":
            replacement = json.dumps(value)
        else:  # pragma: no cover -- the table above is exhaustive
            fatal(f"internal error: unknown token style {style!r} for {name}",
                  exit_code=2)
        text = text.replace("{{%s}}" % name, replacement)
    if "{{" in text and "}}" in text:
        leftover = sorted(set(re.findall(r"\{\{[A-Z_]+\}\}", text)))
        fatal(
            "template substitution left an unresolved token in "
            f"{TEMPLATE_NAME} -- the template grew a token this driver's "
            "_TEMPLATE_TOKEN_STYLE table does not know about yet",
            exit_code=2, unresolved=leftover,
        )
    return text


def template_harness_source(template_text: str, subst: dict) -> str:
    """render_template_source() plus the rewrite-truncate-wrap described in this
    section's own comment above."""
    src = render_template_source(template_text, subst)

    if _EXPORT_META_LITERAL not in src:
        fatal(
            f"could not find {_EXPORT_META_LITERAL!r} in {TEMPLATE_NAME} -- its "
            "shape has changed; re-derive this harness's wrapper before trusting "
            "its output.",
            exit_code=2,
        )
    src = src.replace(_EXPORT_META_LITERAL, _EXPORT_META_REPLACEMENT, 1)

    idx = src.find(_TRUNCATE_BEFORE_MARKER)
    if idx == -1:
        fatal(
            f"could not find the truncation marker {_TRUNCATE_BEFORE_MARKER!r} in "
            f"{TEMPLATE_NAME} -- its shape has changed; re-derive this harness's "
            "truncation point before trusting its output.",
            exit_code=2,
        )
    body = src[:idx]

    prelude = (
        "const log = () => {};\n"
        "const agent = async () => { throw new Error("
        "'agent() is unreachable in the driver harness'); };\n"
        "const pipeline = async () => { throw new Error("
        "'pipeline() is unreachable in the driver harness'); };\n"
    )
    exports = "\n  return { %s };\n}\n" % ", ".join(TEMPLATE_EXPORTED_FUNCTIONS)
    return prelude + "export async function __harness(args) {\n" + body + exports


def call_template_functions(template_path: Path, subst: dict, batches: list,
                            calls: list, node_bin: str = "node") -> dict:
    """Runs `node` against the wrapped, substituted REAL template and calls each
    requested builder.

    `calls`: list of {"key": <result key>, "fn": <name in
    TEMPLATE_EXPORTED_FUNCTIONS>, "args": [...]}. Returns {key: value}.

    THE BATCH-CAP PREFLIGHT. The template's own preflight estimates the
    WORKFLOW's agent-call count and returns `{merged:false,
    reason:"batch-too-large"}` instead of running. That estimate is about a path
    this driver does not take -- it counts a dispatch, a chunked wait and a
    re-check per attempt, none of which exist here, where the only agent call is
    the judge. So the harness is loaded with a cap high enough to reach the
    builders and the REAL bound is enforced locally by the caller against the
    profile's own engine.batch_agent_cap (see enforce_local_cap).

    Detection of that preflight return is STRUCTURAL -- "the wrapper returned an
    object that is not the builder set" -- never a string match on `reason`: the
    driver must not start recognising the template's failure vocabulary."""
    for call in calls:
        if call["fn"] not in TEMPLATE_EXPORTED_FUNCTIONS:
            fatal(f"internal error: {call['fn']!r} is not a harness-exported builder",
                  exit_code=2)

    template_text = read_template_text(template_path)
    harness_src = template_harness_source(template_text, subst)

    runner = (
        "import { __harness } from './harness.mjs';\n"
        "const args = JSON.parse(process.argv[2]);\n"
        "const calls = JSON.parse(process.argv[3]);\n"
        "const builders = await __harness(args);\n"
        "const missing = calls.map(c => c.fn)"
        ".filter(n => typeof builders[n] !== 'function');\n"
        "if (missing.length) {\n"
        "  console.log(JSON.stringify({__harness_error: 'not-builders',\n"
        "    missing, keys: Object.keys(builders)}));\n"
        "  process.exit(0);\n"
        "}\n"
        "const out = {};\n"
        "for (const c of calls) out[c.key] = builders[c.fn](...(c.args || []));\n"
        "console.log(JSON.stringify({__harness_ok: true, values: out}));\n"
    )

    with tempfile.TemporaryDirectory(prefix="glossary-harness-") as tmpdir:
        Path(tmpdir, "harness.mjs").write_text(harness_src, encoding="utf-8")
        Path(tmpdir, "run.mjs").write_text(runner, encoding="utf-8")
        try:
            proc = subprocess.run(
                [node_bin, str(Path(tmpdir, "run.mjs")),
                 json.dumps(batches), json.dumps(calls)],
                capture_output=True, text=True, timeout=120, cwd=tmpdir,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            fatal(f"could not run node against the template harness: {exc!r}",
                  exit_code=2)

    if proc.returncode != 0:
        fatal(
            "the template harness failed under node -- the template's shape or "
            "one of its startup guards refused this run",
            exit_code=2, node_stderr=(proc.stderr or "")[-2000:],
        )
    try:
        obj = json.loads(proc.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        fatal("the template harness printed no readable JSON line", exit_code=2,
              node_stdout=(proc.stdout or "")[-2000:])

    if obj.get("__harness_error") == "not-builders":
        # The wrapper returned the preflight object instead of the builder set.
        # Reported with the keys it DID return, so an operator sees the
        # template's own verdict without this driver parsing its reason string.
        fatal(
            "the glossary template's own preflight refused this run before "
            "returning its builders (most likely the batch-cap preflight). This "
            "driver's real bound is its judge count, enforced separately -- if "
            "this fires, raise engine.batch_agent_cap or split the run.",
            exit_code=1,
            returned_keys=sorted(obj.get("keys") or []),
            missing_builders=sorted(obj.get("missing") or []),
        )
    if not obj.get("__harness_ok"):
        fatal("the template harness returned an unrecognised result", exit_code=2)
    return obj["values"]


def worst_case_codex_jobs(n_batches: int, max_citation_retries: int,
                          research_mode: str) -> int:
    """The most background codex jobs this run can launch.

    Live: one whole-batch dispatch per rung, plus at most one repair per rung.
    Offline: exactly one dispatch per batch -- there is no citation to fetch, so
    no repair and no rejection exists to climb a rung with."""
    if research_mode != "live":
        return n_batches
    return n_batches * 2 * (max_citation_retries + 1)


def enforce_local_cap(n_batches: int, max_citation_retries: int,
                      batch_agent_cap: int, research_mode: str) -> int:
    """Refuses an oversized run before ANY dispatch, and returns the worst-case
    JUDGE count -- which is the driver's agent-call bound but NOT its only one.

    TWO ceilings, because the driver's two costs are no longer the same number.
    The Workflow spent an agent call to reach codex, so the template's single
    preflight (`(1 + (2 + WAIT_CALLS) * attempts) * BATCHES + 2` against
    engine.batch_agent_cap) bounded both at once. This driver launches codex
    in-process, so:

      - JUDGES are the agent calls, one per batch per attempt. Enforcing the
        template's estimate here would refuse runs the driver can comfortably
        afford (7 batches under a cap of 100 need at most 21 judges against a
        114-call Workflow estimate). Outside `live` this count is ZERO, not a
        smaller estimate: research_mode forbids basis:"established" outright, so
        the batch reaches `ready` with no judge ever rendered.
      - CODEX JOBS are the work itself, and they do NOT go to zero offline --
        every batch still gets its dispatch. Bounding only the judges would
        therefore have left an offline run with no ceiling at all, which the
        template's preflight did impose (at cap 3500 it admitted 874 offline
        batches). The caller's remedy is unchanged and is the template's own:
        re-plan smaller batches with glossary_batch_plan.py --batch-size.
    """
    jobs = worst_case_codex_jobs(n_batches, max_citation_retries, research_mode)
    if jobs > batch_agent_cap:
        fatal(
            f"this run's worst-case codex job count ({jobs} for {n_batches} "
            f"{research_mode} batch(es)) exceeds engine.batch_agent_cap "
            f"({batch_agent_cap}); re-plan smaller batches",
            exit_code=1, codexJobs=jobs, cap=batch_agent_cap, batches=n_batches,
        )
    if research_mode != "live":
        return 0
    judges = n_batches * (max_citation_retries + 1)
    if judges > batch_agent_cap:
        fatal(
            f"this run's worst-case judge count ({judges} = {n_batches} batches "
            f"x {max_citation_retries + 1} attempts) exceeds engine."
            f"batch_agent_cap ({batch_agent_cap})",
            exit_code=1, judges=judges, cap=batch_agent_cap, batches=n_batches,
        )
    return judges


# ---------------------------------------------------------------------------
# THE VERDICT CHANNEL -- the one artifact in this driver that can PERMIT.
#
# The pass runs across TWO driver invocations: this process prepares evidence and
# stops, the session dispatches judges, a second invocation consumes the replies.
# So the "batch i attempt n was approved" fact has to survive on disk, and that
# makes it authorization rather than bookkeeping.
#
# THE PROPERTY, stated once rather than patched per attack:
#
#   A verdict authorizes exactly one snapshot, produced by exactly one PREPARE,
#   exactly once, in exactly this run -- and the state saying so lives where no
#   dispatched codex job can write.
#
# Five bindings enforce it, and each closes a case the others do not:
#   durable_root + run_id  -- state from another project or run never applies.
#   batch + attempt        -- the coarse identity; predictable, so never enough
#                             on its own.
#   nonce                  -- generated at PREPARE time, unguessable, consumed
#                             ONCE. Because every PREPARE mints a new one, any
#                             re-fetch invalidates the prior pending entry by
#                             construction. That is what closes replay across a
#                             resume, where resume_setup.py deletes snapshots and
#                             evidence so both get REGENERATED at the very same
#                             paths -- same tuple, same filenames, different
#                             bytes and different pages.
#   snapshot_sha256        -- re-hashed immediately before the approval record is
#                             written, so a verdict cannot be carried onto bytes
#                             it never named.
#   sentinels              -- the attempt-scoped strings this driver itself
#                             issued, read back with the TEMPLATE's own reader.
#
# WHAT THIS DOES NOT DEFEND, stated because overclaiming here would be worse than
# the gap. Since #806 the DISPATCHED codex job is no longer a writer under
# RUN_DIR at all -- it is confined to a throwaway directory outside every git
# working tree. What remains is the pass's own Claude turns: the PREPARE holds
# unrestricted Bash and writes the snapshot, index.json and the evidence bodies
# under RUN_DIR by design, so a turn willing to forge can still swap them between
# PREPARE and the judge, or between the digest check and the merge. That is the shipped design's own position -- the template's
# approvalRecordPath() comment says the approval record "buys no defence against a
# hostile agent ... what it closes is the case that happens WITHOUT malice" -- and
# this channel closes exactly the same class: the command that never ran, the
# verdict never produced, the stale verdict replayed. Closing the hostile class
# means confining what the dispatch job may write, which is a separate change.
# ---------------------------------------------------------------------------

PENDING_FILENAME = "pending.json"


def resolve_verdict_dir(raw: "str | None", durable_root: Path) -> Path:
    """Validates and returns the session-owned directory the pending state and
    the verdicts live in.

    REQUIRED, never defaulted. A default under durable_root would put the one
    permitting artifact inside the model's write root, and a default elsewhere
    would be a path the session does not know it must protect.

    Refuses: a path inside durable_root; an existing entry that is not a
    directory; an existing directory not owned by this uid; an existing directory
    that is group- or world-writable. Every later open in this channel is done
    relative to a pinned descriptor with O_NOFOLLOW, so a symlink planted at
    pending.json cannot redirect the write back into durable_root."""
    if not raw:
        fatal(
            "--verdict-dir is required. The judge verdicts this driver reads are "
            "AUTHORIZATION to write an approval record and merge into an "
            "immutable canon, so they must live in a session-owned directory "
            "that the codex jobs this driver dispatches cannot write.",
            exit_code=2,
        )
    # RESOLVED, not merely absolute: a lexical check compares the path as typed,
    # so a verdict dir whose ANCESTOR is a symlink into the durable root looks
    # outside it and is not. resolve() follows every component, which makes the
    # containment test a fact about the filesystem rather than about the string.
    path = Path(raw).resolve(strict=False)
    droot = durable_root.resolve(strict=False)
    try:
        path.relative_to(droot)
    except ValueError:
        pass
    else:
        fatal(
            f"--verdict-dir {path} resolves inside the durable root {droot}. "
            "The durable root is written by the agents this pass drives, so a "
            "verdict placed in it would be forgeable by the very turns it "
            "authorizes.",
            exit_code=2, verdict_dir=str(path), durable_root=str(droot),
        )
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        os.makedirs(path, mode=0o700, exist_ok=False)
        return path
    except OSError as exc:
        fatal(f"could not stat --verdict-dir {path}: {exc!r}", exit_code=2)
    if not stat.S_ISDIR(st.st_mode):
        fatal(f"--verdict-dir {path} exists and is not a directory", exit_code=2)
    if st.st_uid != os.getuid():
        fatal(
            f"--verdict-dir {path} is owned by uid {st.st_uid}, not by this "
            f"process's uid {os.getuid()}",
            exit_code=2,
        )
    if st.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        fatal(
            f"--verdict-dir {path} is group- or world-writable (mode "
            f"{stat.S_IMODE(st.st_mode):04o}); it must be private to this user",
            exit_code=2,
        )
    return path


def _open_channel_file(verdict_dir: Path, name: str, flags: int, mode: int = 0o600) -> int:
    """Opens `name` INSIDE verdict_dir relative to a pinned directory descriptor,
    with O_NOFOLLOW on the leaf.

    Relative-to-dirfd rather than by pathname: the directory was validated once,
    and opening by full pathname again would re-resolve every component and
    reopen the window that validation closed. O_NOFOLLOW then refuses a symlink
    planted at the leaf -- the specific move that would otherwise redirect this
    write into durable_root."""
    dir_fd = os.open(str(verdict_dir), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        return os.open(name, flags | os.O_NOFOLLOW, mode, dir_fd=dir_fd)
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.EMLINK):
            fatal(
                f"{name} inside --verdict-dir is a symlink; refusing to follow it "
                "-- this file is authorization state and must be a regular file "
                "in the session-owned directory",
                exit_code=2, channel_file=name,
            )
        raise
    finally:
        os.close(dir_fd)


def read_pending(verdict_dir: Path) -> dict:
    """The pending map, or an empty one. Absent is not an error: the first
    invocation of a run has none."""
    try:
        fd = _open_channel_file(verdict_dir, PENDING_FILENAME, os.O_RDONLY)
    except FileNotFoundError:
        return {}
    with os.fdopen(fd, "r", encoding="utf-8") as fh:
        try:
            obj = json.load(fh)
        except ValueError:
            fatal("the state file is not readable JSON -- refusing to guess "
                  "which batches were awaiting a judge", exit_code=2)
    # Shape is load_state()'s business, not this reader's: it is the layer that
    # knows which run the document must belong to, and a document from another
    # run is RESET rather than refused. Checking a shape here would turn a
    # reusable directory into a fatal error.
    if not isinstance(obj, dict):
        fatal("the state file does not hold a JSON object", exit_code=2)
    return obj


def write_pending(verdict_dir: Path, obj: dict) -> None:
    """Whole-file rewrite through the pinned-descriptor open. Not atomic-rename:
    os.replace by pathname would resolve the target name again outside the
    dirfd, which is the resolution this channel deliberately avoids. A truncated
    write here fails CLOSED -- read_pending() refuses unreadable JSON, and a
    refused pending map leaves every batch awaiting a judge rather than ready."""
    data = json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    fd = _open_channel_file(
        verdict_dir, PENDING_FILENAME,
        os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
    )
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())


def new_nonce() -> str:
    """Minted at PREPARE time, not at dispatch: the nonce's job is to bind a
    verdict to the evidence retrieval it was asked about, and a new retrieval is
    exactly the event that must invalidate an older verdict."""
    return secrets.token_hex(16)



# ---------------------------------------------------------------------------
# RUNNING THE TEMPLATE'S COMMANDS
# ---------------------------------------------------------------------------

def run_template_cmd(cmd: str, *, timeout: float) -> "tuple[int, str, str]":
    """Runs a command string the TEMPLATE built.

    Parsed with shlex and run WITHOUT a shell. The Workflow path hands these
    strings to bash because an agent's only executor is bash; this driver has a
    real argv, so it uses one. shlex parses the POSIX quoting the template
    already emits (fetchCitationsCmd single-quotes each --allow-content-type),
    and running without a shell means no metacharacter in any spliced value can
    reach a shell in the first place -- strictly narrower than the path it
    replaces, not merely equivalent."""
    argv = shlex.split(cmd)
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return 124, "", f"timed out after {timeout}s"
    except OSError as exc:
        return 127, "", repr(exc)
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def resolve_companion(node_bin: str = "node") -> str:
    """The installed codex-companion.mjs path, via the shipped resolver. Run from
    the DURABLE copy, exactly as segment_dispatch_driver.py does: a self-anchored
    driver has no plugin root to run the resolver from.

    BOTH arguments are part of the resolver's shipped CLI contract, not optional
    politeness: `--durable-root` is `required=True` there, so omitting it makes the
    resolver exit on its own argparse error and every dispatch fail before it
    starts. `--node` decides which node binary the resolver probes the candidate
    companion with, so passing this driver's own `--node` keeps the probe and the
    later launch talking about the same runtime."""
    _refuse_unless_executable_leaf(RESOLVE_COMPANION_SCRIPT, "resolve_codex_companion.py")
    proc = subprocess.run(
        [sys.executable, str(RESOLVE_COMPANION_SCRIPT),
         "--durable-root", str(DURABLE_ROOT), "--node", node_bin],
        capture_output=True, text=True, timeout=120,
    )
    if proc.returncode != 0:
        fatal("could not resolve codex-companion.mjs -- codex is the required "
              "engine for this pass and there is nothing to fall back to",
              exit_code=2, resolver_stderr=(proc.stderr or "")[-1000:])
    try:
        path = json.loads(proc.stdout.strip().splitlines()[-1])["companion_path"]
    except (ValueError, IndexError, KeyError):
        fatal("the codex-companion resolver printed no readable companion_path",
              exit_code=2)
    _refuse_unless_executable_leaf(Path(path), "codex-companion.mjs")
    return path


def strip_routing_line(prompt: str) -> str:
    """Drops the leading routing token from a dispatch or repair prompt.

    The token is a control for the codex:codex-rescue forwarder, which strips it
    before codex sees the task. This driver shells the companion itself, so it
    passes --background as a REAL flag and must not leave the token in the task
    text, where codex would read it as part of the instructions.

    Refuses rather than tolerating a prompt that does not open with it: the whole
    reason this is a strip and not a template split is that the convention is
    ASSERTED, and an assertion that silently passes when its premise is false is
    not an assertion. tests/glossary_driver_prompt_parity.test.py checks the
    matching identity from the other side."""
    line, sep, rest = prompt.partition("\n")
    if line != _ROUTING_LINE or not sep:
        fatal(
            "a codex dispatch prompt did not open with the expected routing "
            f"line {_ROUTING_LINE!r}; refusing to guess where the task text "
            "starts. The template's dispatch prompt shape changed.",
            exit_code=2, first_line=line[:120],
        )
    return rest


# ---------------------------------------------------------------------------
# #806 -- THE WRITE-CONFINED DISPATCH SANDBOX.
#
# THE PROBLEM THIS CLOSES. Until 1.76.0 every codex job this driver launched ran
# with `--write --cwd <durable_root>`, which made EVERY file under the durable
# root model-writable: canon.json, the snapshot and evidence under RUN_DIR, the
# gate scripts this driver shells (canon_validate.py, fetch_citation.py), and
# this file itself at its deployed ${durable_root}/scripts/ path -- which the
# documented two-invocation loop re-enters, so a job that rewrote it would have
# its bytes executed as the session user by the very process holding the verdict
# nonces and the merge decision.
#
# WHY THE OBVIOUS NARROWING DOES NOT WORK, recorded because it is the answer a
# reader will reach for first. Pointing `--cwd` at RUN_DIR closes nothing:
# codex-companion resolves its workspace-write root by walking UP from `--cwd`
# with `git rev-parse --show-toplevel` (lib/workspace.mjs:resolveWorkspaceRoot),
# so a mere SUBDIRECTORY of an enclosing repository still resolves to that same
# outer toplevel. `durable_root` coinciding with a project's own root is an
# EXPLICITLY SUPPORTED layout (SKILL.md, Step 0a), so that narrowing would have
# obtained nothing at all for exactly the operators most likely to run it, while
# advertising a boundary it did not have.
#
# WHAT ACTUALLY CONFINES. Only a `--cwd` that resolves to ITSELF -- no enclosing
# repository anywhere above it -- shrinks the OS-level sandbox to one directory.
# So each launch gets a fresh single-use mkdtemp directory, VERIFIED against the
# companion's own algorithm before anything is dispatched, and the job writes its
# artifact there. The driver publishes it into RUN_DIR afterwards. The job then
# reaches nothing under durable_root at all.
#
# THIS IS codex_job.py's #409 SHAPE, PORTED -- not a new invention. That file has
# run W5's dispatches this way for several releases; read its module docstring
# for the same argument at greater length. The two are deliberate copies rather
# than a shared module: this script is stdlib-only and self-anchored, and the
# plugin's convention for that is a copy plus a drift test.
# ---------------------------------------------------------------------------

# The four outcomes of the enclosing-repository probe. A bare boolean would be
# wrong here and the polarity is why: ABSENCE of a repository is the SUCCESS
# condition, so collapsing "git said no repository", "git timed out" and "git
# could not be spawned" into one None would score every no-verdict probe as
# confined and fail OPEN -- granting exactly the access the check exists to deny.
_PROBE_ENCLOSED = "enclosed"          # git ran, exit 0: an enclosing repo exists
_PROBE_STANDALONE = "standalone"      # git ran, non-zero: genuinely no repository
_PROBE_GIT_ABSENT = "git-absent"      # git is not installed / cannot be spawned
_PROBE_NO_VERDICT = "no-verdict"      # timed out or errored: we learned nothing

# THE ONLY TWO OUTCOMES THAT MAY LICENSE A DISPATCH, and the membership of this
# tuple is the whole confinement decision -- widening it by one member is exactly
# the fail-open the four outcomes exist to prevent.
#
# A sandbox is confined iff it resolves to ITSELF under codex-companion's own
# workspace-root algorithm (git top-level walking UP from the path, else the path
# unchanged), read from the installed companion's lib/workspace.mjs rather than
# assumed.
#
# _PROBE_NO_VERDICT is therefore ABSENT, and its absence is the fail-closed rule:
# a bounded `git` call that timed out or could not run tells us nothing, while the
# companion's own probe is UNBOUNDED and would still find an enclosing repository.
# Only a probe that actually RAN may license a dispatch.
#
# _PROBE_GIT_ABSENT is PRESENT, and is the one no-result case that is still safe --
# only because it is not really no-result: the companion's resolver degrades the
# SAME way, falling back to the path itself, so there is no enclosing root for it
# to find either.
_CONFINED_PROBE_OUTCOMES = (_PROBE_STANDALONE, _PROBE_GIT_ABSENT)

PROBE_TIMEOUT_SEC = 30
BROKER_TEARDOWN_TIMEOUT_SEC = 5

# A fragment is a JSON array of one batch's decisions -- kilobytes. The cap is a
# bound on THIS PROCESS'S MEMORY when reading a file an untrusted job wrote, not
# a trust check: the published bytes are re-validated downstream either way
# (--check-batch under live, --merge-batches/--verify-merged under offline).
MAX_PUBLISHED_FRAGMENT_BYTES = 64 << 20

# ERE metacharacters, for the one pattern this file hands to `pgrep -f`.
# Deliberately NOT re.escape(): that also escapes `-`, `&`, `~`, `#` and the
# space, and a backslash before an ordinary character is UNDEFINED in POSIX ERE,
# so re.escape() would build a pattern whose behaviour depends on which regex
# implementation `pgrep` was linked against. Those exact characters are
# reachable -- the sandbox path is TMPDIR-prefixed and TMPDIR is the operator's,
# not this script's. Byte-identical to codex_job.py's copy, for the same reason.
_ERE_META = frozenset(r"\.[]{}()*+?^$|")


def _ere_escape(text: str) -> str:
    return "".join("\\" + ch if ch in _ERE_META else ch for ch in text)


def implicit_write_roots() -> "list[Path]":
    """The directories codex makes writable under `workspace-write` IN ADDITION to
    the workspace root itself.

    MEASURED, not read off a doc. Under `codex sandbox -c sandbox_mode=
    "workspace-write"` on 0.151.0, a write to `/tmp` and a write to `$TMPDIR` both
    SUCCEED from a workspace elsewhere, while a write to an ordinary path outside
    the workspace (a file in $HOME) is refused with EPERM. So the sandbox boundary
    is "the workspace root, plus the temp roots", and a confinement argument that
    names only the workspace root is incomplete.

    This is what the sandbox RELIES on, too: the per-launch directory lives under
    TMPDIR, so excluding the temp roots is not an option even where codex offers
    it -- the job would be unable to write the artifact the pass waits on.

    WHAT IS DELIBERATELY NOT HERE, recorded so the next reader does not re-open
    it. `/var/tmp` (and its canonical `/private/var/tmp`) was raised in review as
    a third implicit root, cited from the seatbelt policy source. Probed on this
    runtime it is REFUSED -- `Operation not permitted` -- while the host can write
    there freely (it is mode 1777), so the refusal is the sandbox's and not a
    permission artifact. `/var/folders` itself, `~/.cache` and `/usr/local/share`
    are refused too; only `/tmp`, `/private/tmp` and `$TMPDIR` are granted. Adding
    a root that is not actually granted is not free: it would warn an operator
    that a perfectly confined durable root is exposed, and a security warning that
    cries wolf is one nobody reads. Re-measure before adding one."""
    roots = [Path("/tmp")]
    env_tmp = os.environ.get("TMPDIR")
    if env_tmp:
        roots.append(Path(env_tmp))
    return roots


def refuse_if_under_a_temp_root(durable_root: Path, verdict_dir: Path) -> None:
    """The one confinement case no `--cwd` can reach -- REFUSED, not disclosed.

    Everything else here confines a job to its own throwaway directory. That
    argument holds for every location EXCEPT one already inside a root codex makes
    writable implicitly: a `durable_root` there is still fully model-writable --
    `scripts/`, `canon.json`, this file's own deployed copy -- and a `verdict_dir`
    there is writable too, which is worse, because that directory is the
    AUTHORIZATION channel and `resolve_verdict_dir()` places it outside the durable
    root precisely so a dispatched job cannot reach it.

    THIS SHIPPED AS A WARNING FIRST, and the reason it no longer is belongs here
    rather than in a commit message. The argument for warning was that no
    alternative obtains the boundary -- codex's own switch for excluding the temp
    roots would exclude the per-launch sandbox too, which lives under TMPDIR -- and
    that refusing would forbid a layout this plugin's own test beds used, since a
    pytest `tmp_path` is under `$TMPDIR`. The first half is true and is why this
    function exists at all; the second half was a reason about the TESTS, and a
    test fixture is not a reason to weaken a production check. The fixtures now
    point `TMPDIR` at a directory of their own, which is what a real operator's
    machine looks like anyway: a durable root is not normally inside the temp dir.

    So it fails closed. That the boundary cannot be obtained here is exactly why
    the run must not start: proceeding would advertise a confinement the dispatched
    job does not have, over the two paths whose integrity the rest of this file is
    built to protect.

    Compared CANONICALLY on both sides: macOS resolves `/tmp` to `/private/tmp` and
    `$TMPDIR` under `/var/folders` to `/private/var/folders`, so a lexical test
    would miss the very case it is for."""
    temp_roots = []
    for raw in implicit_write_roots():
        try:
            temp_roots.append(Path(os.path.realpath(str(raw))))
        except OSError:
            continue
    for label, path in (("durable root", durable_root), ("verdict directory", verdict_dir)):
        resolved = Path(os.path.realpath(str(path)))
        for temp_root in temp_roots:
            if resolved == temp_root or temp_root in resolved.parents:
                fatal(
                    f"refusing to run: the {label} {resolved} lies under {temp_root}, "
                    f"which codex makes writable under workspace-write whatever this "
                    f"driver passes as --cwd. A dispatched job could still write "
                    f"there, so the per-launch sandbox would confine it away from "
                    f"everything EXCEPT the paths this pass most needs protected. "
                    f"Move it outside the temp roots -- durable state is meant to "
                    f"outlive a reboot in any case -- or set TMPDIR elsewhere.",
                    exit_code=2, offending=label, path=str(resolved),
                    implicit_write_root=str(temp_root))


def probe_enclosing_repo(path: Path) -> str:
    """Runs the companion's OWN workspace-root probe against `path` and reports
    WHICH outcome occurred, never a bare boolean -- the polarity here is inverted
    from the usual (absence of a repository is the SUCCESS condition), so a
    collapsed None would score a no-verdict probe as confined and fail OPEN. See
    _CONFINED_PROBE_OUTCOMES for how each outcome is scored."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, timeout=PROBE_TIMEOUT_SEC, cwd=str(path),
        )
    except FileNotFoundError:
        return _PROBE_GIT_ABSENT
    except (subprocess.TimeoutExpired, OSError, ValueError):
        return _PROBE_NO_VERDICT
    return _PROBE_ENCLOSED if proc.returncode == 0 else _PROBE_STANDALONE


class DispatchSandbox:
    """One launch's write-confined directory, as a context manager.

    REFUSES rather than degrades. If the directory turns out to be reachable
    from an enclosing git repository -- a TMPDIR inside a working tree -- the
    dispatch does not happen. Unlike durable_root, where an enclosing repository
    is a supported layout, a TMPDIR inside one is pathological, so refusing here
    costs no sanctioned configuration; and an unconfined sandbox is worse than no
    launch at all, because it would hand back exactly the access this class
    exists to remove while reporting that it had been removed.

    TEARDOWN KILLS THE BROKER BEFORE REMOVING THE DIRECTORY, and that order is
    load-bearing. codex-companion keys a PERSISTENT broker to whatever `--cwd`
    it is handed (`ensureBrokerSession` spawns `app-server-broker.mjs serve
    --cwd <dir>` detached and unref'd), so a per-launch cwd leaves one broker per
    launch behind -- the leak codex_job.py measured at 2794 state directories in
    a single day before it added the same teardown. SIGTERM, never SIGKILL: the
    broker's own handler closes its app-server client, taking `codex app-server`
    and `codex-code-mode-host` down with it; SIGKILL leaves exactly those
    children behind, which is the leak itself.

    Killing a broker whose codex turn is still streaming is INTENDED. By the time
    this context exits the driver has either taken the artifact or given up on
    the turn, and the directory it writes into is about to disappear, so its
    output is discarded either way -- stopping it also stops paying for it.

    Best-effort teardown, never raising: cleanup on the way out must not turn a
    finished batch into a failed one."""

    def __init__(self, label: str):
        self.label = label
        self.path = None

    def __enter__(self) -> "DispatchSandbox":
        # BOTH failures below exit the PROCESS (code 2, this driver's documented
        # environment/usage code) rather than raising DriverError, and that
        # classification is the whole point rather than a style choice. A
        # DriverError here would reach drive_all(), which records the batch
        # `status="failed"` -- one of the two statuses the next invocation SKIPS.
        # The operator would fix TMPDIR, re-run exactly as documented, and find
        # the batch permanently skipped: a recoverable environment fault turned
        # into a wedged run that only deleting authorization state can clear.
        # Neither condition is a fact about this batch, and neither can be
        # answered by advancing the ladder, so no state may be written about it.
        try:
            raw = tempfile.mkdtemp(prefix="ltgd.%s." % self.label)
        except OSError as exc:
            fatal(f"could not create a dispatch sandbox for {self.label}: {exc!r}",
                  exit_code=2, label=self.label)
        # Pin ONE canonical spelling now -- macOS's /tmp -> /private/tmp symlink
        # otherwise yields two spellings of the same directory across this run
        # (mkdtemp's raw return vs anything realpath'd later, including the
        # companion's own state-dir keying), which would silently miss each other.
        self.path = Path(os.path.realpath(raw))
        outcome = probe_enclosing_repo(self.path)
        if outcome not in _CONFINED_PROBE_OUTCOMES:
            self._teardown()
            fatal(
                "refusing to dispatch: the codex sandbox is not write-confined "
                f"(probe={outcome}). codex-companion resolves its workspace-write "
                "root by walking up from --cwd to the enclosing git top level, so "
                "a sandbox inside a working tree would hand the job write access "
                "to that whole repository. Set TMPDIR to a directory outside every "
                "git working tree and re-run -- nothing about this run has been "
                "recorded, so the re-run resumes exactly where this one stopped.",
                exit_code=2, label=self.label, sandbox_probe=outcome)
        log(f"{self.label}: codex write root confined to {self.path} (probe={outcome})")
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        self._teardown()
        return False

    def artifact(self, name: str) -> Path:
        """The path inside this sandbox that the job will be told to write. The
        NAME still comes from the template's own path builder -- this file never
        invents an artifact filename -- only its directory changes."""
        return self.path / name

    def _teardown(self) -> None:
        if self.path is None:
            return
        self._shutdown_broker()
        shutil.rmtree(str(self.path), ignore_errors=True)
        # ignore_errors keeps cleanup from turning a finished batch into a failed
        # one, but a directory that SURVIVED must still be named: the job owned
        # this directory and could have made it unremovable (dropped traversal
        # permissions, a set flag), and a leaked path nobody can find is worse
        # than a leaked path in the log. Same posture as
        # segment_dispatch_driver.py's _teardown_staging.
        if self.path.exists():
            log(f"{self.label}: sandbox {self.path} could not be removed and is "
                f"left on disk; remove it by hand")
        self.path = None

    def _shutdown_broker(self) -> None:
        """Matched on ARGV rather than by reading the companion's own broker
        record: reading that record would mean duplicating its private state
        directory scheme here, where a silent upstream change would present as
        this cleanup simply never firing -- and the record is not written for
        every broker that exists, so an argv match is what finds an unrecorded
        one. The match cannot hit anything else: the sandbox path is a
        single-use mkdtemp path and it reaches the broker's own argv verbatim,
        the pattern additionally requires app-server-broker.mjs, and the path is
        anchored so a longer sibling path cannot match."""
        pattern = "app-server-broker\\.mjs .*--cwd %s( |$)" % _ere_escape(str(self.path))
        try:
            proc = subprocess.run(["pgrep", "-f", pattern], capture_output=True,
                                  text=True, timeout=BROKER_TEARDOWN_TIMEOUT_SEC)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            return
        # pgrep: 0 = matched, 1 = nothing matched, >=2 = pgrep itself failed.
        # Only 0 carries pids.
        if proc.returncode != 0:
            return
        own = os.getpid()
        for field in (proc.stdout or "").split():
            try:
                pid = int(field)
            except ValueError:
                continue
            if pid <= 1 or pid == own:
                continue
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass


def read_sandbox_artifact(path: Path, label: str) -> bytes:
    """Reads an artifact a dispatched codex job wrote, with the same no-follow
    walk this file already uses for executables.

    A confined job can still WRITE A SYMLINK inside its own sandbox -- write
    confinement restricts where writes LAND, never what a symlink's target
    string names -- so refusing anything that is not a regular file reached
    without following a link at any step is load-bearing here, not decoration."""
    fd, state = _open_regular_no_follow_walk(path)
    if state != "file" or fd is None:
        raise DriverError(
            f"{label}: the sandbox artifact at {path} is not a regular file "
            f"reachable without following a symlink (state={state})",
            label=label, artifact_state=state)
    try:
        with os.fdopen(fd, "rb") as fh:
            data = fh.read(MAX_PUBLISHED_FRAGMENT_BYTES + 1)
    except OSError as exc:
        raise DriverError(f"{label}: could not read the sandbox artifact: {exc!r}", label=label)
    if len(data) > MAX_PUBLISHED_FRAGMENT_BYTES:
        raise DriverError(
            f"{label}: the sandbox artifact exceeds {MAX_PUBLISHED_FRAGMENT_BYTES} bytes",
            label=label)
    return data


def publish_fragment(sandbox_path: Path, fragment_path: Path, staging_path: Path,
                     gate_cmd: str, label: str) -> None:
    """Moves the job's artifact onto its canonical RUN_DIR path, gating the exact
    bytes that land there.

    THE ORDER IS THE POINT, and it is not the obvious one. Polling the sandbox
    path until --check-batch passes gates an object the job can still rewrite: the
    turn may outlive the poll, and it owns that file. So the poll only decides WHEN
    to look; what is captured is then gated again, HERE, against the driver's own
    staged copy, and only a copy that passes is renamed into place. Gating after
    the rename would be too late -- a refused fragment would already be sitting at
    the path a resume reads.

    BYTES, never a re-serialization: the object --check-batch validated must be the
    object that lands, or the gate and the artifact are two different things. The
    staged copy lives in RUN_DIR beside its destination so the rename is atomic and
    within one filesystem, and RUN_DIR is outside every dispatched job's write
    root -- so what passes here stays passed.

    The digest is re-read from the DESTINATION afterwards, which is what makes the
    "same bytes" claim a check rather than a hope."""
    data = read_sandbox_artifact(sandbox_path, label)
    digest = hashlib.sha256(data).hexdigest()
    # O_EXCL, never O_TRUNC: the name carries a fresh random token, so a path that
    # already exists is not a stale copy of ours to overwrite -- it is someone
    # else's file at our name, and adopting it is how two publications come to
    # share one inode.
    #
    # OUTSIDE the cleanup block below, deliberately. That block unlinks
    # staging_path on any failure, and until this open SUCCEEDS this publication
    # owns nothing at that path -- so a collision would otherwise be answered by
    # deleting the other publication's file, which is the ownership rule inverted.
    fd = os.open(str(staging_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        # os.fdopen() does NOT close its argument when it raises, and from here on
        # the cleanup below unlinks the path -- so a failure between the open and
        # the wrapper would leave a descriptor open on a file with no name. Taking
        # ownership explicitly is the only point at which that gap can be closed.
        try:
            handle = os.fdopen(fd, "wb")
        except BaseException:
            os.close(fd)
            raise
        with handle as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        if not _cmd_ok(gate_cmd, 300):
            raise DriverError(
                f"{label}: the bytes captured from the sandbox did not pass "
                f"--check-batch. The turn had already passed that gate against its "
                f"own copy, so it rewrote the artifact after passing it",
                label=label)
        os.replace(str(staging_path), str(fragment_path))
    except BaseException:
        try:
            os.unlink(str(staging_path))
        except OSError:
            pass
        raise
    if _sha256_file(fragment_path) != digest:
        raise DriverError(
            f"{label}: the published fragment does not match the bytes that "
            f"passed the gate", label=label, fragment_path=str(fragment_path))


def launch_codex(*, companion: str, node_bin: str, prompt: str, effort: str,
                 sandbox_root: Path, tmpdir: Path, label: str) -> str:
    """Fires one background codex turn for a batch and returns its jobId (#809).

    The argv mirrors codex_job.py's own launch() rather than a reduced guess:

      task --background --json --write --fresh [--effort E] --cwd R --prompt-file P

    --write is NOT optional. Read-only was #198's no-output failure: codex cannot
    create the fragment without it, so every batch would poll to its deadline and
    report glossary-pass-null. --fresh gives each attempt its own thread.

--cwd is the PER-LAUNCH SANDBOX (#806), never durable_root and never RUN_DIR
    -- see DispatchSandbox above for why only a directory with no enclosing git
    repository actually shrinks codex-companion's workspace-write resolution, and
    why a subdirectory of the durable root does not. The caller has already proved
    that property of this path before reaching here; this function does not
    re-derive it, and must never be handed a root that has not been through
    DispatchSandbox.

    The jobId is what lets the caller ask codex-companion about THIS turn later
    (read_job_status) instead of only ever watching the artifact it may or may
    not write. A launch that prints no usable jobId is a launch that cannot be
    watched, so it is refused here rather than handed back to poll blind --
    the same posture codex_job.py's own launch() takes."""
    prompt_file = tmpdir / f"prompt-{label}.txt"
    prompt_file.write_text(prompt, encoding="utf-8")
    argv = [node_bin, companion, "task", "--background", "--json", "--write", "--fresh"]
    if effort:
        argv += ["--effort", effort]
    argv += ["--cwd", str(sandbox_root), "--prompt-file", str(prompt_file)]
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DriverError(f"codex launch failed for {label}: {exc!r}", label=label)
    if proc.returncode != 0:
        raise DriverError(
            f"codex launch returned {proc.returncode} for {label}",
            label=label, launch_stderr=(proc.stderr or "")[-1000:],
        )
    # Empty, malformed and non-object stdout all take the same refusal path --
    # none of them names a job this driver could later ask codex-companion about.
    try:
        obj = json.loads(proc.stdout)
    except ValueError:
        obj = None
    job_id = obj.get("jobId") if isinstance(obj, dict) else None
    if not isinstance(job_id, str) or not job_id:
        raise DriverError(
            f"codex launch printed no jobId for {label}",
            label=label, launch_stdout=(proc.stdout or "")[-1000:],
            launch_stderr=(proc.stderr or "")[-1000:],
        )
    log(f"{label}: codex job {job_id} queued")
    return job_id


# ---------------------------------------------------------------------------
# THE RETRIEVAL OUTCOME READ -- the #347 boundary, as a function.
# ---------------------------------------------------------------------------

def read_outcome_pairs(index_path: Path) -> "list[dict]":
    """Reads fetch_citation.py's index.json and returns ONLY
    [{"item_index": int, "outcome": str}].

    THESE TWO FIELDS AND NO OTHERS, and the restriction is the boundary rather
    than tidiness. `item_index` is that script's own loop counter and `outcome`
    is its own closed vocabulary, so neither is authored by a retrieved page.
    Everything else in an entry either came from the fragment (`source`,
    `source_form`, `basis`) or from the server (`final_origin`, `chain`,
    `content_type`, `bytes`), and `evidence_file` names the retrieved bytes
    themselves. The judge reads those; this process must not, because it is the
    actor that decides what to fetch next.

    Read ONCE, immediately after the fetch, and never again: every PREPARE
    rewrites index.json wholesale, so the pairs are captured at the one moment
    they are known to describe THIS fetch. (Since #806 a dispatched codex job is
    not among the writers that could reach it -- the pass's own turns are.)"""
    try:
        with open(index_path, "r", encoding="utf-8") as fh:
            obj = json.load(fh)
    except (OSError, ValueError) as exc:
        raise DriverError(f"could not read the citation evidence index: {exc!r}",
                          index_path=str(index_path))
    entries = obj.get("entries")
    if not isinstance(entries, list):
        raise DriverError("the citation evidence index has no entries array",
                          index_path=str(index_path))
    pairs = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        idx, outcome = entry.get("item_index"), entry.get("outcome")
        if isinstance(idx, int) and isinstance(outcome, str):
            pairs.append({"item_index": idx, "outcome": outcome})
    return pairs


def classify_outcomes(pairs: "list[dict]", established_indices: "set[int]") -> dict:
    """The repair gate's THREE branches, computed in one place so they are
    exhaustive by construction.

    Restricted to `established_indices` because fetch_citation.py indexes every
    source-bearing row while the judge is told to ignore every non-established
    one: an unrestricted set would repair rows no judge would ever object to.

    Shared-budget outcomes are their own branch, never repaired, for two separate
    reasons. They are environment faults rather than facts about a citation -- a
    fresh URL cannot fix a run that ran out of time or bytes. And they are the one
    lever by which a hostile server can push a DIFFERENT row into the failure set,
    by consuming the shared budget before that row is reached."""
    budget, failed = [], []
    for pair in pairs:
        idx = pair["item_index"]
        if idx not in established_indices:
            continue
        outcome = pair["outcome"]
        if outcome == _FETCH_OK:
            continue
        if outcome in _SHARED_BUDGET_OUTCOMES:
            budget.append(idx)
        else:
            failed.append(idx)
    return {"budget_failed": sorted(budget), "repairable": sorted(failed)}


# ---------------------------------------------------------------------------
# THE SPLICE -- the only place this driver writes a canon fragment.
# ---------------------------------------------------------------------------

def load_rows(path: Path) -> list:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            rows = json.load(fh)
    except (OSError, ValueError) as exc:
        raise DriverError(f"could not read {path.name}: {exc!r}", path=str(path))
    if not isinstance(rows, list):
        raise DriverError(f"{path.name} is not a JSON array", path=str(path))
    return rows


def established_indices(rows: list) -> "set[int]":
    """Positions whose basis is `established` -- the only rows that carry a
    citation claim, and so the only rows a retrieval failure can be about."""
    out = set()
    for i, row in enumerate(rows):
        if isinstance(row, dict) and row.get("basis") == "established":
            out.add(i)
    return out


def validate_repair_rows(repair_rows: list, expected_forms: "list[str]") -> None:
    """The repair artifact must hold EXACTLY the rows it was asked for, in order.

    Sequence equality, not set membership, and not a subset check. Each of the
    rejected shapes is a real failure this catches:
      * a MISSING row leaves a rejected citation in place while the ladder
        advances, so the batch burns rungs without the defect ever being fixed;
      * an EXTRA row rewrites a row whose citation retrieved fine -- a row no
        judge objected to and this repair was never authorised to touch;
      * a DUPLICATE makes the positional splice ambiguous;
      * a REORDER lands each repaired decision on the wrong row while the final
        source_form SET stays identical, which is precisely the corruption
        --check-batch cannot see (it compares sets, never order)."""
    if not isinstance(repair_rows, list):
        raise DriverError("the repair fragment is not a JSON array")
    got = []
    for row in repair_rows:
        if not isinstance(row, dict) or not isinstance(row.get("source_form"), str):
            raise DriverError("a repair fragment row has no string source_form")
        got.append(row["source_form"])
    if got != expected_forms:
        raise DriverError(
            "the repair fragment's source_form sequence does not match the rows "
            "it was asked to repair",
            expected=expected_forms, got=got,
        )


def splice_repair(snapshot_rows: list, failed_positions: "list[int]",
                  repair_rows: list) -> list:
    """Returns snapshot_rows with the failed positions replaced, in order.

    THE BASE IS THE SNAPSHOT, never the attempt fragment, and that is load-bearing
    rather than symmetrical. The snapshot is the artifact PINNED for this attempt,
    while the attempt path is the pass's own working file -- so a position derived
    from the snapshot and applied to the attempt file can land on a different row
    after a reorder, and
    --check-batch would not catch it because it compares source-form SETS rather
    than order. The snapshot is the one artifact pinned for the attempt, so it is
    both the only correct source of positions and the only correct base."""
    out = list(snapshot_rows)
    for position, row in zip(failed_positions, repair_rows):
        out[position] = row
    return out


# ---------------------------------------------------------------------------
# POLLING
# ---------------------------------------------------------------------------

class Ctx:
    """Everything the per-batch machine needs, resolved once."""

    def __init__(self, *, template: Path, subst: dict, batches: list, node_bin: str,
                 companion: str, durable_root: Path, verdict_dir: Path,
                 research_mode: str, effort: str, poll_sec: float, deadline_sec: float,
                 max_citation_retries: int, tmpdir: Path):
        self.template = template
        self.subst = subst
        self.batches = batches
        self.node_bin = node_bin
        self.companion = companion
        self.durable_root = durable_root
        self.verdict_dir = verdict_dir
        self.research_mode = research_mode
        self.effort = effort
        self.poll_sec = poll_sec
        self.deadline_sec = deadline_sec
        self.max_citation_retries = max_citation_retries
        self.tmpdir = tmpdir

    def build(self, calls: list) -> dict:
        return call_template_functions(self.template, self.subst, self.batches,
                                       calls, self.node_bin)


def _cmd_ok(cmd: str, timeout: float) -> bool:
    code, _out, _err = run_template_cmd(cmd, timeout=timeout)
    return code == 0


_JOB_TERMINAL = frozenset(("completed", "failed", "cancelled"))
_STATUS_TIMEOUT = 120.0


def read_job_status(*, companion: str, node_bin: str, job_id: str, sandbox_root: Path,
                    timeout: float) -> "tuple[str | None, str | None]":
    """Asks codex-companion for one job's recorded status, with the SAME --cwd it
    was launched with (job records are keyed by the workspace root codex-companion
    resolves from --cwd -- a mismatched sandbox_root would ask about a workspace
    that never held this job).

    Returns (status, detail). `status` is job["status"] when that is a string.
    `detail` prefers job["errorMessage"] (a non-empty/non-blank string) and falls
    back to job["summary"]; both are the companion's own words, never this
    driver's -- the observed case is a runner exit landing in `summary`
    ("Selected model is at capacity...") while a thrown error lands in
    `errorMessage`.

    ANY failure here -- a spawn error, the timeout, a non-zero exit, unparsable
    stdout, or a missing/non-dict `job` -- returns (None, None): UNKNOWN, never a
    fact about the job. The artifact poll stays in charge on an unknown read; a
    status this driver cannot read must never turn into a failure verdict."""
    argv = [node_bin, companion, "status", job_id, "--json", "--cwd", str(sandbox_root)]
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None, None
    if proc.returncode != 0:
        return None, None
    try:
        obj = json.loads(proc.stdout)
    except ValueError:
        return None, None
    job = obj.get("job") if isinstance(obj, dict) else None
    if not isinstance(job, dict):
        return None, None
    status = job.get("status")
    status = status if isinstance(status, str) else None
    err = job.get("errorMessage")
    summary = job.get("summary")
    if isinstance(err, str) and err.strip():
        detail = err
    elif isinstance(summary, str) and summary.strip():
        detail = summary
    else:
        detail = None
    return status, detail


def wait_for_artifact(ctx: Ctx, *, ready, job_id: str, sandbox_root: Path,
                      label: str) -> dict:
    """Local bounded poll for the artifact a dispatched job writes, watching the
    job's own recorded status alongside it (#809). Replaces the template's
    chunked-wait apparatus wholesale, as the plain bounded poll it supersedes
    did: that apparatus exists because a Bash tool call is clamped at 600s and
    an agent's wait had to be split across several calls to stay under it. A
    local process has no such clamp, so none of it is reimplemented here.

    WHY THE STATUS READ EXISTS AT ALL: a job codex-companion has already recorded
    completed/failed/cancelled is a turn that is OVER -- nothing can write the
    artifact after it -- so waiting out the rest of the deadline on an artifact
    check alone buys nothing. This reads the job's own status alongside the
    artifact check so a job that failed at launch (capacity, auth, a thrown
    error) ends the wait the moment codex-companion says so, not at the deadline.

    THE ARTIFACT IS RE-CHECKED BEFORE EVERY NOT-READY RETURN. The status probe
    itself takes real time, and a job can write its artifact and then go
    terminal, so the artifact check at the top of the loop can predate the write
    on either exit path -- an artifact that exists wins over the job record and
    over the clock.

    The probe is never started with no time left (`if remaining > 0`) and never
    given more than the time left (`min(_STATUS_TIMEOUT, remaining)`), so the
    wait cannot overrun its deadline by more than one probe that was itself
    bounded by it.

    Returns {"ready": bool, "jobStatus": str | None, "jobDetail": str | None}.
    `ready`, `job_id` and `sandbox_root` all name the SAME turn: the artifact
    predicate the caller built and the job this function watches while it waits."""
    deadline = time.monotonic() + ctx.deadline_sec
    while True:
        if ready():
            return {"ready": True, "jobStatus": None, "jobDetail": None}
        remaining = deadline - time.monotonic()
        status = detail = None
        if remaining > 0:
            status, detail = read_job_status(
                companion=ctx.companion, node_bin=ctx.node_bin, job_id=job_id,
                sandbox_root=sandbox_root, timeout=min(_STATUS_TIMEOUT, remaining))
            remaining = deadline - time.monotonic()
        if status in _JOB_TERMINAL or remaining <= 0:
            if ready():
                return {"ready": True, "jobStatus": None, "jobDetail": None}
            if status in _JOB_TERMINAL:
                log(f"{label}: codex job {job_id} ended {status} without the "
                    f"artifact" + (f": {detail}" if detail else ""))
            else:
                log(f"{label}: deadline of {ctx.deadline_sec}s expired")
            return {"ready": False, "jobStatus": status, "jobDetail": detail}
        time.sleep(min(ctx.poll_sec, remaining))


def _job_failed(outcome: dict) -> bool:
    """The job did not run to completion -- an environmental fault, never a fact
    about the candidates."""
    return outcome["jobStatus"] in ("failed", "cancelled")


def advance_batch(ctx: Ctx, batch: dict, attempt: int, resumed: bool,
                  rejection_reason: "str | None") -> dict:
    """Drives ONE batch from dispatch up to the point a judge is needed, or to a
    terminal state. Never dispatches a judge itself -- that is the session's job.

    Returns one of:
      {"state": "awaiting_judge", ...}  -- pending entry written, judge prompt ready
      {"state": "ready", ...}           -- offline only; no review is owed
      {"state": "failed", "reason": ...}
    """
    idx = batch["index"]
    built = ctx.build([
        {"key": "fragment", "fn": "fragmentPath", "args": [idx, attempt]},
    ])
    fragment_path = Path(built["fragment"])

    # 1. DISPATCH -- skipped for a resumed attempt 0, whose fragment
    #    resume_setup.py already re-checked before this driver ever ran.
    if not (resumed and attempt == 0):
        log(f"batch {idx}: dispatching codex (attempt {attempt})")
        # #806: the job writes inside a throwaway directory with no enclosing git
        # repository, so it can reach nothing under durable_root. Its artifact
        # keeps the template's own filename -- this file invents none -- and the
        # driver publishes it to the canonical RUN_DIR path once the gate passes.
        # The prompt and its self-check command are built AFTER the sandbox
        # exists, because both have to name that path; that is one extra node
        # call per attempt, against a dispatch measured in minutes.
        with DispatchSandbox(f"dispatch-{idx}-{attempt}") as sandbox:
            sandbox_out = sandbox.artifact(fragment_path.name)
            # The driver's own staging copy, beside the destination so the rename
            # is atomic. A private name, not a pass artifact -- nothing but
            # publish_fragment() ever opens it. UNIQUE PER PUBLICATION rather than
            # per (batch, attempt): two drivers pointed at the same run would
            # otherwise open one deterministic name, and one could still hold a
            # descriptor on it while the other gated and renamed -- writing
            # through to the renamed inode and undoing the byte-binding this
            # staging step exists to create. The token also lets the open be
            # O_EXCL, so a publication never adopts a file it did not create.
            staging = (fragment_path.parent /
                       f".publish_{idx}_attempt_{attempt}_{secrets.token_hex(8)}.json")
            dispatch = ctx.build([
                {"key": "prompt", "fn": "batchDispatchPrompt",
                 "args": [batch, attempt, rejection_reason, str(sandbox_out)]},
                {"key": "check", "fn": "sandboxCheckBatchCmd",
                 "args": [str(sandbox_out), idx]},
                {"key": "stagecheck", "fn": "sandboxCheckBatchCmd",
                 "args": [str(staging), idx]},
            ])
            job_id = launch_codex(companion=ctx.companion, node_bin=ctx.node_bin,
                                  prompt=strip_routing_line(dispatch["prompt"]),
                                  effort=ctx.effort, sandbox_root=sandbox.path,
                                  tmpdir=ctx.tmpdir, label=f"dispatch-{idx}-{attempt}")

            # 2. POLL -- the SAME --check-batch command the dispatch prompt told
            #    codex to self-check with, so readiness here and readiness there
            #    are one question asked once, spliced from one builder. Watches
            #    the job's own recorded status alongside it (#809), so a job
            #    codex-companion marks failed/cancelled -- or completes without
            #    writing -- ends the wait without burning the rest of the deadline.
            outcome = wait_for_artifact(
                ctx, ready=lambda: _cmd_ok(dispatch["check"], 300), job_id=job_id,
                sandbox_root=sandbox.path, label=f"batch {idx} attempt {attempt}")
            if not outcome["ready"]:
                # Unchanged reason string: the recovery docs key off it. A job
                # codex-companion recorded failed/cancelled gets its own reason,
                # naming the job, instead of the generic null-artifact one.
                return {"state": "failed",
                        "reason": "codex-job-failed" if _job_failed(outcome)
                                  else "glossary-pass-null",
                        "batchIndex": idx, "attempt": attempt,
                        "fragmentPath": str(fragment_path), "jobId": job_id,
                        "jobStatus": outcome["jobStatus"],
                        "jobDetail": outcome["jobDetail"]}

            # 3. PUBLISH -- capture, re-gate the captured bytes, then rename onto
            #    the canonical path every later step names. Inside the sandbox
            #    context on purpose: the artifact must be taken before teardown
            #    removes it.
            publish_fragment(sandbox_out, fragment_path, staging,
                             dispatch["stagecheck"], f"batch {idx} attempt {attempt}")
    else:
        log(f"batch {idx}: resume-skip -- attempt 0 fragment already validated")

    # 4. OFFLINE -- research_mode forbids basis:"established" outright and
    #    canon_validate.py enforces that independently at merge time, so there is
    #    no citation to review and no snapshot to take.
    if ctx.research_mode != "live":
        return {"state": "ready", "batchIndex": idx, "attempt": attempt,
                "fragmentPath": str(fragment_path),
                "mergePath": str(fragment_path),
                "citationReview": "skipped-offline"}

    return prepare_and_hand_back(ctx, batch, attempt, fragment_path)


def prepare_and_hand_back(ctx: Ctx, batch: dict, attempt: int,
                          fragment_path: Path) -> dict:
    """APPROVE -> FETCH -> repair gate -> pending entry. Live mode only."""
    idx = batch["index"]
    built = ctx.build([
        {"key": "approve", "fn": "approveBatchCmd", "args": [idx, attempt]},
        {"key": "approved", "fn": "approvedPath", "args": [idx, attempt]},
        {"key": "fetch", "fn": "fetchCitationsCmd", "args": [idx, attempt]},
        {"key": "index", "fn": "evidenceIndexPath", "args": [idx, attempt]},
        {"key": "judge", "fn": "citationJudgePrompt", "args": [batch, attempt]},
    ])
    approved_path = Path(built["approved"])

    # 4. APPROVE -- pin the attempt's bytes. Everything downstream reads the
    #    snapshot, never the still-mutable attempt path.
    #
    #    #851: the failure reason is read off the stream that actually CARRIES it,
    #    and from the end of that stream that keeps it. Every script this driver
    #    runs -- canon_validate.py and fetch_citation.py alike -- reports a VERDICT
    #    on STDOUT, as one JSON line, and never on stderr. Stderr carries only what
    #    is NOT a verdict: an import-time dependency guard, argparse misuse, an
    #    uncaught traceback. So logging `err` alone logged a bare empty reason on
    #    exactly the refusals worth reading, and `err` still comes first because in
    #    those non-verdict cases it is the stream with the message -- run_template_cmd
    #    synthesises it for a timeout and an OSError too. The ENDS differ because the
    #    shapes do: a traceback puts its message LAST, while the JSON line puts
    #    "error" FIRST and a redundant "offending" array last, so a tail-slice of
    #    stdout drops exactly the field worth reading.
    code, out, err = run_template_cmd(built["approve"], timeout=600)
    if code != 0:
        log(f"batch {idx}: could not snapshot attempt {attempt}: "
            f"{err[-400:] if err else out[:400]}")
        return {"state": "evidence_failed", "batchIndex": idx, "attempt": attempt,
                "reason": "approve-failed"}

    # 5. FETCH -- the one network step, and the only one. This process launches it
    #    and never reads what it retrieved (see read_outcome_pairs).
    code, out, err = run_template_cmd(built["fetch"], timeout=1800)
    if code != 0:
        log(f"batch {idx}: citation fetch failed for attempt {attempt}: "
            f"{err[-400:] if err else out[:400]}")
        return {"state": "evidence_failed", "batchIndex": idx, "attempt": attempt,
                "reason": "fetch-failed"}

    pairs = read_outcome_pairs(Path(built["index"]))
    snapshot_rows = load_rows(approved_path)
    classified = classify_outcomes(pairs, established_indices(snapshot_rows))

    # 6a. Shared-budget failure -- an environment fault, not a citation fault. NO
    #     judge is spent: fetch_citation.py exits 0 on these, so without this
    #     branch they would fall through, a judge would reject for want of
    #     evidence, and that rejection would be misread as a content rejection.
    if classified["budget_failed"]:
        log(f"batch {idx}: fetch hit this run's shared time/byte budget on "
            f"{len(classified['budget_failed'])} row(s); no judge spent")
        return {"state": "evidence_failed", "batchIndex": idx, "attempt": attempt,
                "reason": "fetch-budget-exhausted",
                "budgetFailed": classified["budget_failed"]}

    # 6b. Repairable retrieval failures -- handled by the caller, which owns the
    #     rung accounting.
    if classified["repairable"]:
        return {"state": "needs_repair", "batchIndex": idx, "attempt": attempt,
                "failedPositions": classified["repairable"],
                "snapshotPath": str(approved_path)}

    # 6c. Every established citation retrieved. This is the ONLY branch that
    #     reaches a judge, which is what makes a rejection from here necessarily a
    #     CONTENT rejection rather than a retrieval one.
    nonce = new_nonce()
    entry = {
        "durable_root": str(ctx.durable_root),
        "run_id": ctx.subst["run_id"],
        "batch": idx,
        "attempt": attempt,
        "nonce": nonce,
        "snapshot_sha256": _sha256_file(approved_path),
        "ok_sentinel": f"CITATIONS_OK {idx} ATTEMPT {attempt}",
        "fail_sentinel": f"CITATIONS_REJECTED {idx} ATTEMPT {attempt}",
        "created": _utc_now_iso(),
    }
    return {"state": "awaiting_judge", "batchIndex": idx, "attempt": attempt,
            "fragmentPath": str(fragment_path), "snapshotPath": str(approved_path),
            "pending": entry, "judgePrompt": built["judge"]}


def run_repair(ctx: Ctx, batch: dict, attempt: int, failed_positions: "list[int]",
               snapshot_path: Path) -> dict:
    """Repairs the failed rows into the RESERVED rung attempt+1.

    RUNG ACCOUNTING, stated because "consumes a rung" is ambiguous at both ends.
    Repair reserves attempt+1 before dispatching, and nothing else may claim it.
    At the terminal rung there is no attempt+1 to reserve, so the caller must not
    reach here at all -- it exhausts instead. A repair that fails validation
    regenerates into that SAME reserved rung, never attempt+2, so a malformed
    repair costs the batch nothing beyond the rung it already reserved."""
    idx = batch["index"]
    snapshot_rows = load_rows(snapshot_path)
    failed_rows = [snapshot_rows[p] for p in failed_positions]
    expected_forms = [r.get("source_form") for r in failed_rows]

    built = ctx.build([
        {"key": "repairpath", "fn": "repairFragmentPath", "args": [idx, attempt]},
        {"key": "nextfragment", "fn": "fragmentPath", "args": [idx, attempt + 1]},
        {"key": "nextcheck", "fn": "checkBatchCmd", "args": [idx, attempt + 1]},
    ])
    next_fragment = Path(built["nextfragment"])

    log(f"batch {idx}: repairing {len(failed_positions)} unretrievable citation(s) "
        f"into rung {attempt + 1}")
    # #806: same confinement as the ordinary dispatch. The repair artifact is
    # never published -- the driver reads the repaired rows here and writes the
    # SPLICED whole fragment itself -- so the sandbox is the only place it ever
    # exists. That also retires the stale-artifact unlink this function used to
    # perform: a leftover from an earlier run of the same RUN_ID could sit at the
    # attempt-scoped RUN_DIR path and be accepted by the poll, but a freshly
    # created mkdtemp directory cannot contain one.
    with DispatchSandbox(f"repair-{idx}-{attempt}") as sandbox:
        repair_path = sandbox.artifact(Path(built["repairpath"]).name)
        repair = ctx.build([
            {"key": "prompt", "fn": "batchRepairPrompt",
             "args": [batch, attempt, failed_rows, str(repair_path)]},
        ])
        job_id = launch_codex(companion=ctx.companion, node_bin=ctx.node_bin,
                              prompt=strip_routing_line(repair["prompt"]),
                              effort=ctx.effort, sandbox_root=sandbox.path,
                              tmpdir=ctx.tmpdir, label=f"repair-{idx}-{attempt}")

        outcome = wait_for_artifact(ctx, ready=repair_path.exists, job_id=job_id,
                                    sandbox_root=sandbox.path,
                                    label=f"batch {idx} repair {attempt}")
        if not outcome["ready"]:
            if _job_failed(outcome):
                # TERMINAL for the batch, exactly like the dispatch failure. The
                # repair_invalid fallback below spends the reserved rung on a
                # whole-fragment regeneration and folds the reason into
                # rejection prose -- right for a repair turn that ran and wrote
                # nothing, wrong for a job that never ran: that is a retry of an
                # environmental fault, and it would lose the job's own diagnostics.
                return {"state": "failed", "reason": "codex-job-failed",
                        "batchIndex": idx, "attempt": attempt, "jobId": job_id,
                        "jobStatus": outcome["jobStatus"],
                        "jobDetail": outcome["jobDetail"]}
            return {"state": "repair_invalid", "reason": "repair-never-written"}
        try:
            repair_rows = json.loads(read_sandbox_artifact(
                repair_path, f"batch {idx} repair {attempt}").decode("utf-8"))
            if not isinstance(repair_rows, list):
                raise DriverError("the repair fragment is not a JSON array")
            validate_repair_rows(repair_rows, expected_forms)
        except (DriverError, ValueError, UnicodeDecodeError) as exc:
            log(f"batch {idx}: repair refused ({exc}); falling back to whole-fragment "
                f"regeneration in the same reserved rung {attempt + 1}")
            return {"state": "repair_invalid", "reason": "repair-shape-refused"}

    spliced = splice_repair(snapshot_rows, failed_positions, repair_rows)
    _atomic_write_json(next_fragment, spliced)

    # The spliced whole fragment is the object that must satisfy coverage, so it
    # goes through the ordinary gate -- unchanged command, unchanged flags.
    if not _cmd_ok(built["nextcheck"], 600):
        log(f"batch {idx}: the spliced fragment failed --check-batch; falling back "
            f"to whole-fragment regeneration in rung {attempt + 1}")
        return {"state": "repair_invalid", "reason": "spliced-check-failed"}
    # The reserved rung is now POPULATED, and the caller must be told with what:
    # dispatching an ordinary whole-batch job at this rung would order an agent to
    # re-decide every candidate and atomically overwrite exactly this path, which
    # is the per-row repair undone.
    return {"state": "repaired", "attempt": attempt + 1,
            "fragmentPath": str(next_fragment)}


# ---------------------------------------------------------------------------
# THE RUN-SCOPED STATE RECORD, and the ONE transition function over it.
#
# The pass spans TWO driver invocations -- one prepares evidence and stops, the
# session dispatches judges, another consumes the replies -- so every fact that
# has to survive between them lives in ONE document, scoped to one durable_root
# and one RUN_ID, and every path advances a batch through the SAME loop.
#
# It is one record and one loop because the alternative was measured and failed
# in three ways at once: a rejection recorded on the second invocation had
# nowhere to advance to and stranded the batch, so the shipped three-attempt
# ladder could never run; a repair's own second PREPARE returned an intermediate
# state straight to a caller that understood only two of them; and readiness
# persisted with no run scoping, so a stale approval from an earlier run could
# satisfy a later run's merge admission. All three are one root cause: half the
# state machine lived in memory on one invocation.
#
# A batch is in exactly one status: pending (not yet driven), awaiting_judge
# (the session owes a verdict), ready (approved, recorded, holds its mergePath),
# or failed (terminal, carries its reason).
# ---------------------------------------------------------------------------

STATE_VERSION = 1


def fresh_state(durable_root: Path, run_id: str) -> dict:
    return {"version": STATE_VERSION, "durable_root": str(durable_root),
            "run_id": run_id, "batches": {}}


def load_state(verdict_dir: Path, durable_root: Path, run_id: str) -> dict:
    """Reads the state document, or starts a fresh one.

    A document belonging to another root or run is RESET, not merged and not
    refused. Reusing one verdict directory across runs is an ordinary operator
    habit, so refusing would be hostile; merging would be worse than either --
    that is exactly how an approval from a previous run reaches a later run's
    merge. Resetting makes a reused directory behave like a fresh one."""
    doc = read_pending(verdict_dir)
    if not doc:
        return fresh_state(durable_root, run_id)
    if doc.get("version") != STATE_VERSION or \
            doc.get("durable_root") != str(durable_root) or \
            doc.get("run_id") != run_id:
        log(f"verdict-dir holds state for run {doc.get('run_id')!r} under "
            f"{doc.get('durable_root')!r}; this run is {run_id!r} under "
            f"{durable_root} -- discarding it rather than mixing two runs")
        return fresh_state(durable_root, run_id)
    if not isinstance(doc.get("batches"), dict):
        fatal("the state document's batches map has an unrecognised shape",
              exit_code=2)
    return doc


def save_state(verdict_dir: Path, state: dict) -> None:
    write_pending(verdict_dir, state)


def batch_state(state: dict, index: int) -> dict:
    return state["batches"].setdefault(
        str(index), {"attempt": 0, "status": "pending", "rejection_reason": None})


def _stale_status_reason(st: dict) -> "str | None":
    """Why a settled-looking status can no longer be honoured, or None.

    `awaiting_judge` and `ready` are the two statuses drive_all SKIPS, and each
    one is a promise about a file: the approved snapshot the judge is reading, or
    the snapshot and approval record the merge will name. Both promises can be
    broken from outside this process while the state document stays intact."""
    status = st.get("status")
    if status == "awaiting_judge":
        snapshot = st.get("snapshotPath")
        digest = (st.get("pending") or {}).get("snapshot_sha256")
        if not snapshot or not digest:
            return "the awaiting entry names no snapshot to verify"
        try:
            if _sha256_file(Path(snapshot)) != digest:
                return "the approved snapshot's bytes changed since the hand-back"
        except OSError:
            return "the approved snapshot the judge was handed is gone"
        return None
    if status == "ready":
        merge_path = st.get("mergePath")
        if not merge_path or not Path(merge_path).exists():
            return "the fragment this batch was made ready to merge is gone"
        record = st.get("approvalRecordPath")
        if st.get("approvalRecorded") and (not record or not Path(record).exists()):
            return "the approval record admitting this batch to the merge is gone"
    return None


def _release_approved_slots(ctx: Ctx, idx: int) -> "list[str]":
    """Unlinks every approved-snapshot slot a reset batch's ladder can re-enter.

    canon_validate.py publishes the approved snapshot CREATE-ONCE (os.link) and
    REFUSES a different fragment into a path that already holds one. A reset sends
    the batch back to attempt 0 with every snapshot its earlier rungs left behind
    still in place, so each of those refuses the rung that would republish it --
    one rung spent per refusal, until the ladder is gone and the batch settles as
    citation-review-exhausted, which the all-or-nothing merge turns into the loss
    of the whole pass (#852). Releasing the slots is what makes the reset's own
    promise -- re-drive from attempt 0 -- performable at all.

    EVERY rung, not only the one the dropped status named. The state document
    banks just the CURRENT attempt's snapshotPath, while the reset re-enters at 0
    and can climb back through all of them, so the file that blocks a re-drive is
    usually one no field in that document mentions.

    The paths are BUILT from approvedPath() -- the same template function PREPARE
    approves into -- never globbed and never taken from the state document, whose
    stored strings are checked only for run identity and the shape of its batches
    map. Nothing outside this run's canonical slots for this batch is reachable
    from here.

    Deliberately NOT released: out_{i}_attempt_0.json, a validated fragment
    resume_setup.py preserves on purpose and that publication overwrites anyway;
    and the approval record, whose writer replaces rather than refuses, so it can
    block nothing and is worth keeping as evidence. Returns the paths that could
    not be removed."""
    built = ctx.build([{"key": str(rung), "fn": "approvedPath", "args": [idx, rung]}
                       for rung in range(ctx.max_citation_retries + 1)])
    undeleted = []
    for path in built.values():
        try:
            Path(path).unlink(missing_ok=True)
        except OSError as exc:
            undeleted.append(f"{path}: {exc}")
    return undeleted


def reconcile_state(ctx: Ctx, batches: list, state: dict) -> "list[dict]":
    """Resets any batch whose skipped status outlives the artifacts it promises.

    A resume reuses the SAME run_id, so a state document written before the
    interruption is kept rather than discarded -- and resume_setup.py has
    meanwhile deleted every approved snapshot, approval record and evidence
    directory in that run. Without this, the two statuses drive_all skips become
    permanent: an `awaiting_judge` batch re-emits a hand-back whose verdict is
    refused forever because its snapshot is unreadable, and a `ready` batch fails
    merge forever against a deleted path. Neither has any transition out.

    So the check is on the ARTIFACT, never on a resume flag: whatever removed the
    file, a promise about a file that is gone is not a status. A reset sends the
    batch back through the ordinary attempt-0 path, where PREPARE mints a fresh
    snapshot, evidence, nonce and record -- the same path a resumed batch already
    takes, so a surviving out_{i}_attempt_0.json is still honoured.

    A reset also RELEASES that batch's approved slots, because the snapshot is
    published create-once: a status this function has just declared untrusted must
    not leave a file behind that refuses its own replacement. See
    _release_approved_slots()."""
    reset = []
    for batch in batches:
        idx = batch["index"]
        st = state["batches"].get(str(idx))
        if not st:
            continue
        reason = _stale_status_reason(st)
        if reason is None:
            continue
        log(f"batch {idx}: dropping its {st.get('status')!r} status -- {reason}; "
            f"re-driving it from attempt 0")
        undeleted = _release_approved_slots(ctx, idx)
        state["batches"][str(idx)] = {"attempt": 0, "status": "pending",
                                      "rejection_reason": None}
        entry = {"batch": idx, "was": st.get("status"),
                 "attempt": st.get("attempt"), "reason": reason}
        if undeleted:
            # Not fatal: an unremovable file is an environment fault, and the rung
            # it blocks reports it itself as approve-failed. Failing the whole
            # invocation here would take down the batches that are fine.
            entry["undeleted"] = undeleted
            log(f"batch {idx}: could not release {len(undeleted)} approved slot(s); "
                f"the re-drive may be refused at approve time: {undeleted}")
        reset.append(entry)
    return reset


def _exhaust(st: dict, attempt: int, last_rejection, *, attempts_used: int) -> dict:
    """The ladder's ONE terminal transition, recorded at the rung that ran.

    `attempts_used` stays a parameter rather than being derived: the loop-top
    guard is entered with an attempt that never ran (a hand-edited state document
    is the only way in), so it reports the ladder's own length, while every other
    caller exhausts at a rung it actually drove."""
    st.update(status="failed", attempt=attempt, reason="citation-review-exhausted",
              attemptsUsed=attempts_used, lastRejection=last_rejection)
    return st


def _clear_awaiting(st: dict) -> None:
    """Drops what only an awaiting batch owns. A verdict is consumed once, so the
    nonce and the rendered prompt must not survive the transition that answers
    them -- on ANY branch, including the ones that go on to fail."""
    st.pop("pending", None)
    st.pop("judgePrompt", None)


def advance_until_blocked(ctx: Ctx, batch: dict, state: dict,
                          resumed_indices: "set[int]") -> dict:
    """Drives ONE batch until it is awaiting a judge, ready, or terminal.

    THE ONLY transition function. Both invocations enter here, so a batch resumed
    after a rejection takes exactly the path a freshly driven one takes, and every
    intermediate state -- needs_repair, evidence_failed, a repair's own second
    PREPARE -- is consumed by this loop rather than escaping to a caller that
    would read it as a failure. The bound is the template's MAX_CITATION_RETRIES."""
    idx = batch["index"]
    st = batch_state(state, idx)
    attempt = st["attempt"]
    rejection_reason = st.get("rejection_reason")
    # Set ONLY by a valid repair: the rung it names already holds bytes that
    # passed --check-batch, so that rung re-enters at APPROVE and never at
    # DISPATCH. See the `repaired` branch below for why that distinction is the
    # whole feature and not an optimisation.
    prepared_fragment: "Path | None" = None

    while True:
        if attempt > ctx.max_citation_retries:
            return _exhaust(st, attempt, rejection_reason,
                            attempts_used=ctx.max_citation_retries + 1)

        if prepared_fragment is not None:
            result = prepare_and_hand_back(ctx, batch, attempt, prepared_fragment)
            prepared_fragment = None
        else:
            result = advance_batch(ctx, batch, attempt,
                                   resumed=idx in resumed_indices,
                                   rejection_reason=rejection_reason)
        kind = result["state"]

        if kind == "awaiting_judge":
            st.update(status="awaiting_judge", attempt=attempt,
                      rejection_reason=rejection_reason,
                      pending=result["pending"],
                      judgePrompt=result["judgePrompt"],
                      snapshotPath=result["snapshotPath"])
            return st

        if kind == "ready":                       # offline only
            st.update(status="ready", attempt=attempt,
                      mergePath=result["mergePath"],
                      citationReview=result.get("citationReview"))
            return st

        if kind == "failed":
            # "state" is this function's own vocabulary and "attempt"/"status"
            # are set explicitly; spreading any of them collides with the keyword.
            st.update(status="failed", attempt=attempt, **{
                k: v for k, v in result.items()
                if k not in ("state", "attempt", "status")})
            return st

        if kind == "needs_repair":
            # TERMINAL RUNG: no attempt+1 exists to reserve, so no repair is
            # dispatched and the batch exhausts here. Dispatching one would create
            # an attempt outside the ladder and break the judge cap.
            if attempt >= ctx.max_citation_retries:
                return _exhaust(
                    st, attempt,
                    "citations did not retrieve at the final attempt: "
                    + repr(result["failedPositions"]),
                    attempts_used=attempt + 1)
            repaired = run_repair(ctx, batch, attempt, result["failedPositions"],
                                  Path(result["snapshotPath"]))
            if repaired["state"] == "failed":
                # A job codex-companion recorded failed/cancelled during repair
                # settles the batch at its CURRENT rung -- the one whose repair
                # failed -- exactly as the `kind == "failed"` branch above settles
                # an ordinary dispatch failure. Reached here, not there, because
                # run_repair() returns this rather than raising: the rung it
                # reserved must NOT be advanced past for a job that never ran.
                st.update(status="failed", attempt=attempt, **{
                    k: v for k, v in repaired.items()
                    if k not in ("state", "attempt", "status")})
                return st
            # The rung is RESERVED either way: a valid repair writes its spliced
            # fragment there, an invalid one regenerates there. Never attempt+2.
            attempt += 1
            st["attempt"] = attempt
            if repaired["state"] == "repair_invalid":
                rejection_reason = (
                    "the previous attempt's citations could not be retrieved and a "
                    "per-item repair could not be applied (" +
                    str(repaired.get("reason")) + ")")
            else:
                # Repaired in place. The reserved rung ALREADY holds the spliced
                # fragment, so this rung re-enters at APPROVE -- re-approve and
                # re-fetch through THIS loop, so a replacement URL that also fails
                # to retrieve reserves the next rung like any other retrieval
                # failure instead of escaping. Handing it back to advance_batch()
                # would dispatch a whole-batch job whose prompt orders the agent to
                # decide every candidate and atomically write this same path: the
                # untouched rows would be silently re-decided, and which of the two
                # writes the next APPROVE snapshotted would depend on scheduling.
                prepared_fragment = Path(repaired["fragmentPath"])
                rejection_reason = None
            continue

        if kind == "evidence_failed":
            if attempt >= ctx.max_citation_retries:
                return _exhaust(st, attempt, result.get("reason"),
                                attempts_used=attempt + 1)
            rejection_reason = (
                "the previous attempt's citation evidence could not be prepared: "
                + str(result.get("reason")))
            attempt += 1
            st["attempt"] = attempt
            continue

        raise DriverError(f"internal error: unhandled batch state {kind!r}")


def drive_all(ctx: Ctx, batches: list, state: dict,
              resumed_indices: "set[int]") -> None:
    """Advances every batch not already settled or awaiting a judge."""
    for batch in batches:
        st = batch_state(state, batch["index"])
        if st["status"] in ("ready", "failed", "awaiting_judge"):
            continue
        try:
            advance_until_blocked(ctx, batch, state, resumed_indices)
        except DriverError as exc:
            st.update(status="failed", reason=str(exc), **exc.extra)


# ---------------------------------------------------------------------------
# CONSUMING VERDICTS
# ---------------------------------------------------------------------------

def record_verdicts(ctx: Ctx, verdicts_path: Path, state: dict) -> dict:
    """Consumes the session's judge replies INTO the state record.

    A verdict is admitted only when it matches its batch's awaiting entry on all
    of: this run's durable_root and RUN_ID (which the document itself is scoped
    to), the batch and attempt, the nonce minted at PREPARE time and not yet
    consumed, and the snapshot digest RE-HASHED now. The reply is read with the
    TEMPLATE's own rejectedAnywhere + sentinelVerdict, never a Python
    re-implementation -- those carry #228/#308's containment-guard-then-positive
    -proof discipline, and a second reader would be a second set of rules.

    A REJECTION IS A TRANSITION, not a note: it clears the awaiting status and
    advances the batch to the next rung carrying the reviewer's own prose, and the
    caller re-enters the drive loop, so the shipped three-attempt ladder actually
    runs. Recording `approved: false` and stopping stranded the batch with nothing
    pending and nothing owed."""
    # The verdict file carries the nonces that admit an approval, so it belongs in
    # the same session-owned directory as the state it answers -- not at an
    # arbitrary path, which could sit under durable_root -- written by the agents
    # this pass drives, so it could be rewritten before it is read.
    resolved = verdicts_path.resolve(strict=False)
    if resolved.parent != ctx.verdict_dir.resolve(strict=False):
        fatal(
            f"--record-verdicts must name a file inside --verdict-dir "
            f"({ctx.verdict_dir}); it carries the nonces that admit an approval, "
            f"so it is authorization input rather than an ordinary argument",
            exit_code=2, given=str(resolved),
        )
    try:
        with open(resolved, "r", encoding="utf-8") as fh:
            supplied = json.load(fh)
    except (OSError, ValueError) as exc:
        fatal(f"could not read --record-verdicts file: {exc!r}", exit_code=2)
    if not isinstance(supplied, list):
        fatal("--record-verdicts must hold a JSON array of "
              "{batch, attempt, nonce, reply} objects", exit_code=2)

    admitted, refusals = [], []
    for item in supplied:
        if not isinstance(item, dict):
            refusals.append({"reason": "verdict entry is not an object"})
            continue
        batch_i, attempt = item.get("batch"), item.get("attempt")
        if not isinstance(batch_i, int) or not isinstance(attempt, int):
            refusals.append({"batch": batch_i, "attempt": attempt,
                             "reason": "verdict entry has a non-integer batch or "
                                       "attempt"})
            continue
        st = state["batches"].get(str(batch_i))
        if st is None or st.get("status") != "awaiting_judge":
            refusals.append({"batch": batch_i, "attempt": attempt,
                             "reason": "this batch is not awaiting a judge in "
                                       "this run"})
            continue
        entry = st.get("pending") or {}
        if st.get("attempt") != attempt:
            refusals.append({"batch": batch_i, "attempt": attempt,
                             "reason": f"this batch awaits a verdict for attempt "
                                       f"{st.get('attempt')}"})
            continue
        if item.get("nonce") != entry.get("nonce"):
            refusals.append({"batch": batch_i, "attempt": attempt,
                             "reason": "nonce mismatch -- the verdict does not "
                                       "name the PREPARE it is answering"})
            continue
        snapshot = Path(ctx.build([{"key": "p", "fn": "approvedPath",
                                    "args": [batch_i, attempt]}])["p"])
        try:
            current_digest = _sha256_file(snapshot)
        except OSError as exc:
            refusals.append({"batch": batch_i, "attempt": attempt,
                             "reason": f"the approved snapshot is unreadable: {exc!r}"})
            continue
        if current_digest != entry.get("snapshot_sha256"):
            refusals.append({"batch": batch_i, "attempt": attempt,
                             "reason": "the approved snapshot's bytes changed "
                                       "since the judge prompt was rendered"})
            continue
        reply = item.get("reply")
        if not isinstance(reply, str):
            refusals.append({"batch": batch_i, "attempt": attempt,
                             "reason": "verdict carries no reply text"})
            continue

        read = ctx.build([
            {"key": "contained", "fn": "rejectedAnywhere",
             "args": [reply, entry["fail_sentinel"]]},
            {"key": "verdict", "fn": "sentinelVerdict",
             "args": [reply, entry["ok_sentinel"], entry["fail_sentinel"]]},
            {"key": "detail", "fn": "rejectionDetail",
             "args": [reply, entry["ok_sentinel"], entry["fail_sentinel"]]},
        ])

        if read["contained"] or not read["verdict"]:
            _clear_awaiting(st)
            if attempt >= ctx.max_citation_retries:
                # The ladder is 0..max, so there is no attempt+1 to advance to.
                # Incrementing anyway would persist and REPORT an attempt outside
                # the ladder, naming a rung that never ran as the one that
                # exhausted. Exhaust here instead, at the rung actually rejected.
                _exhaust(st, attempt, read["detail"], attempts_used=attempt + 1)
            else:
                st.update(status="pending", attempt=attempt + 1,
                          rejection_reason=read["detail"])
            admitted.append({"batch": batch_i, "attempt": attempt,
                             "approved": False, "rejection": read["detail"]})
            continue

        rec = ctx.build([
            {"key": "cmd", "fn": "recordApprovalCmd", "args": [batch_i, attempt]},
            {"key": "path", "fn": "approvalRecordPath", "args": [batch_i, attempt]},
        ])
        code, out, err = run_template_cmd(rec["cmd"], timeout=600)
        record_path = rec["path"]
        if code != 0:
            # The review DID approve; the bookkeeping write failed. The batch is
            # NOT ready: merging an approved set nobody can reconstruct is the
            # guesswork #723 exists to remove.
            _clear_awaiting(st)
            st.update(status="failed", attempt=attempt,
                      reason="approval-record-write-failed",
                      detail=(err[-300:] if err else out[:300]))
            admitted.append({"batch": batch_i, "attempt": attempt,
                             "approved": True, "approvalRecorded": False})
            continue

        _clear_awaiting(st)
        st.update(status="ready", attempt=attempt, mergePath=str(snapshot),
                  approvalRecordPath=record_path, approvalRecorded=True,
                  citationReview="approved")
        admitted.append({"batch": batch_i, "attempt": attempt, "approved": True,
                         "approvalRecorded": True, "mergePath": str(snapshot)})

    return {"recorded": admitted, "refused": refusals}


# ---------------------------------------------------------------------------
# MERGE -- the one serialized write into canon.json.
# ---------------------------------------------------------------------------

def merge_and_verify(ctx: Ctx, batches: list, state: dict) -> dict:
    """One --merge-batches over THIS RUN's ready batches in index order, then the
    disk-independent --verify-merged.

    Admission is EXACT membership of this run's own batch list, never a subset
    test over whatever the state document happens to hold: `expected <= have`
    would admit a merge whose ready set came from somewhere other than this run,
    and --verify-merged cannot catch it because it checks that every manifest form
    is PRESENT, not that no extra fragment was merged.

    All-or-nothing, exactly as the Workflow's is."""
    expected = [b["index"] for b in batches]
    ready = []
    for idx in expected:
        st = state["batches"].get(str(idx))
        if not st or st.get("status") != "ready":
            return {"merged": False, "reason": "awaiting-more-verdicts",
                    "awaiting": [i for i in expected
                                 if (state["batches"].get(str(i)) or {}).get("status")
                                 != "ready"]}
        ready.append((idx, st))

    unrecorded = [idx for idx, st in ready
                  if st.get("citationReview") != "skipped-offline"
                  and not st.get("approvalRecorded")]
    if unrecorded:
        return {"merged": False, "reason": "approval-records-missing",
                "unrecordedBatches": sorted(unrecorded)}

    fragments = [st["mergePath"] for _i, st in ready]
    records = [st["approvalRecordPath"] for _i, st in ready
               if st.get("approvalRecordPath")]
    built = ctx.build([
        {"key": "merge", "fn": "mergeBatchesCmd", "args": [fragments, records]},
        {"key": "verify", "fn": "verifyMergedCmd", "args": [fragments]},
    ])
    code, out, err = run_template_cmd(built["merge"], timeout=1800)
    if code != 0:
        return {"merged": False, "reason": "merge-failed",
                "detail": (err or out or "")[-600:]}
    code, out, err = run_template_cmd(built["verify"], timeout=1800)
    if code != 0:
        return {"merged": False, "reason": "verify-failed",
                "detail": (err or out or "")[-600:]}
    try:
        verified = json.loads(out.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {"merged": False, "reason": "verify-unreadable"}
    if verified.get("verified") is not True or verified.get("missing"):
        return {"merged": False, "reason": "verify-refused", "verify": verified}
    return {"merged": True, "batches": expected}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

_MAX_RETRIES_RE = re.compile(r"^const MAX_CITATION_RETRIES = (\d+)\s*$", re.M)


def template_max_citation_retries(template_text: str) -> int:
    """The ladder bound, READ FROM THE TEMPLATE rather than offered as a flag.

    It is not a driver preference. The whole point of this driver is that both
    paths run the same pass, and the template's own comment explains why the value
    is 2 rather than a round number. A `--max-citation-retries` flag would let one
    path climb a rung the other cannot -- the fallback would exhaust before ever
    creating that attempt."""
    match = _MAX_RETRIES_RE.search(template_text)
    if not match:
        fatal(
            "could not read MAX_CITATION_RETRIES from the glossary template -- "
            "its shape changed, and this driver will not guess a ladder bound "
            "that must match the pipeline() path's exactly.",
            exit_code=2,
        )
    return int(match.group(1))


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="glossary_dispatch_driver.py",
        description="Local driver for the W3a canon-and-glossary pass (#800).",
    )
    p.add_argument("--run-id", dest="run_id", required=True,
                   help="the glossary RUN_ID resume_setup.py accepted")
    p.add_argument("--batches-file", dest="batches_file", required=True,
                   help="the glossary_batch_plan.py args array, as JSON")
    p.add_argument("--verdict-dir", dest="verdict_dir", required=True,
                   help="session-owned directory for this run's state and its "
                        "verdicts; MUST be outside the durable root")
    p.add_argument("--plugin-root", dest="plugin_root", required=True,
                   help="the plugin install root; the ONLY copy of the workflow "
                        "template this driver will execute")
    p.add_argument("--record-verdicts", dest="record_verdicts",
                   help="a JSON array of {batch, attempt, nonce, reply} INSIDE "
                        "--verdict-dir; consumed, then the run continues")
    p.add_argument("--source-lang", dest="source_lang", default="")
    p.add_argument("--target-lang", dest="target_lang", default="")
    p.add_argument("--research-mode", dest="research_mode", default="live",
                   choices=("live", "offline"))
    p.add_argument("--effort", default="high")
    p.add_argument("--citation-content-types", dest="citation_content_types",
                   default="")
    p.add_argument("--batch-agent-cap", dest="batch_agent_cap", type=int,
                   default=3500)
    p.add_argument("--resumed-batch-indices", dest="resumed_batch_indices",
                   default="[]")
    p.add_argument("--poll-sec", dest="poll_sec", type=float,
                   default=DEFAULT_POLL_SEC)
    p.add_argument("--deadline-sec", dest="deadline_sec", type=float,
                   default=DEFAULT_DEADLINE_SEC)
    p.add_argument("--node", dest="node_bin", default="node")
    return p


def main(argv=None) -> int:
    args = build_arg_parser().parse_args(argv)

    problem = validate_run_id(args.run_id or "")
    if problem:
        fatal(f"--run-id: {problem}", exit_code=2)

    durable_root = DURABLE_ROOT
    verdict_dir = resolve_verdict_dir(args.verdict_dir, durable_root)
    # The one layout no --cwd can confine: refused before any work (see function).
    refuse_if_under_a_temp_root(durable_root, verdict_dir)
    template = resolve_template(args.plugin_root)
    max_retries = template_max_citation_retries(read_template_text(template))

    try:
        batches = json.loads(Path(args.batches_file).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        fatal(f"could not read --batches-file: {exc!r}", exit_code=2)
    if not isinstance(batches, list) or not batches:
        fatal("--batches-file must hold a non-empty JSON array of batches",
              exit_code=2)
    for batch in batches:
        if not isinstance(batch, dict) or not isinstance(batch.get("index"), int):
            fatal("every batch must be an object with an integer index",
                  exit_code=2)
    try:
        resumed_raw = json.loads(args.resumed_batch_indices)
    except ValueError:
        fatal("--resumed-batch-indices must be a JSON array", exit_code=2)
    # SHAPE, not just parseability. set() accepts any iterable, so the JSON
    # string "0" would become {"0"} -- which never equals the integer index 0, so
    # batch 0 would be redispatched and its already-validated attempt-0 fragment
    # overwritten, while the value still reached the template as a well-formed
    # array and slipped past its own guard. A scalar number raises instead.
    if not isinstance(resumed_raw, list) or \
            not all(isinstance(i, int) and not isinstance(i, bool)
                    for i in resumed_raw):
        fatal("--resumed-batch-indices must be a JSON array of integer batch "
              "indices", exit_code=2, given=repr(resumed_raw)[:200])
    resumed = set(resumed_raw)

    judges = enforce_local_cap(len(batches), max_retries, args.batch_agent_cap,
                               args.research_mode)

    subst = {
        "durable_root": str(durable_root),
        "source_lang": args.source_lang,
        "target_lang": args.target_lang,
        "research_mode": args.research_mode,
        "run_id": args.run_id,
        "effort": args.effort,
        "citation_content_types": args.citation_content_types,
        # Loaded past on purpose; the real bound is enforce_local_cap() above.
        "batch_agent_cap": 10 ** 9,
        "plugin_root": str(Path(args.plugin_root).absolute()),
        "resumed_batch_indices": sorted(resumed),
    }

    with tempfile.TemporaryDirectory(prefix="glossary-driver-") as tmp:
        ctx = Ctx(template=template, subst=subst, batches=batches,
                  node_bin=args.node_bin,
                  companion=resolve_companion(args.node_bin),
                  durable_root=durable_root,
                  verdict_dir=verdict_dir, research_mode=args.research_mode,
                  effort=args.effort, poll_sec=args.poll_sec,
                  deadline_sec=args.deadline_sec,
                  max_citation_retries=max_retries,
                  tmpdir=Path(tmp))

        state = load_state(verdict_dir, durable_root, args.run_id)
        # BEFORE the verdicts are read, not after: a verdict answering a hand-back
        # whose snapshot no longer exists must be refused as "not awaiting a judge"
        # rather than as a snapshot fault, and the batch must be re-driven in the
        # same invocation instead of waiting for one that never comes.
        reset_batches = reconcile_state(ctx, batches, state)
        recorded = {"recorded": [], "refused": []}
        if args.record_verdicts:
            recorded = record_verdicts(ctx, Path(args.record_verdicts), state)
            save_state(verdict_dir, state)

        # ONE drive call, on BOTH paths. A rejection recorded just above has
        # already moved its batch back to `pending` at the next rung, so this is
        # what actually runs the ladder rather than stranding it.
        drive_all(ctx, batches, state, resumed)
        save_state(verdict_dir, state)

        needs_judge, ready, failed = [], [], []
        for batch in batches:
            idx = batch["index"]
            st = state["batches"].get(str(idx)) or {}
            if st.get("status") == "awaiting_judge":
                needs_judge.append({
                    "batch": idx, "attempt": st["attempt"],
                    "nonce": st["pending"]["nonce"],
                    "judgePrompt": st["judgePrompt"],
                    "agentType": "literary-translator:citation-judge",
                })
            elif st.get("status") == "ready":
                ready.append(idx)
            elif st.get("status") == "failed":
                failed.append({"batchIndex": idx,
                               **{k: v for k, v in st.items()
                                  if k not in ("status", "pending", "judgePrompt")}})

        payload = {
            "action": "record-verdicts" if args.record_verdicts else "drive",
            "run_id": args.run_id,
            "worstCaseJudgeCalls": judges,
            "maxCitationRetries": max_retries,
            "recorded": recorded["recorded"],
            "refused": recorded["refused"],
            "needs_judge": needs_judge,
            "ready": sorted(ready),
            "not_ready": failed,
            # Reported, not just logged: a reset silently costs a judge call and
            # re-runs a review the operator may believe already happened.
            "reset": reset_batches,
            "merged": False,
            "generated": _utc_now_iso(),
        }
        gate_refused = False
        if not needs_judge and not failed:
            outcome = merge_and_verify(ctx, batches, state)
            payload.update(outcome)
            save_state(verdict_dir, state)
            # The merge and the disk-independent verify are GATES, and the CLI
            # contract above says a refused gate exits 1. Without this the run
            # that most needs a non-zero status -- every batch approved, the one
            # irreversible write refused -- emitted merged:false and exited 0,
            # so shell-level orchestration read a failed final gate as success.
            # "awaiting-more-verdicts" is not a refusal: it is the ordinary
            # not-yet, and it cannot be reached from here anyway.
            gate_refused = (not outcome.get("merged")
                            and outcome.get("reason") != "awaiting-more-verdicts")
        elif needs_judge:
            payload["reason"] = "awaiting-more-verdicts"
        emit(payload)
        return 0 if not (failed or recorded["refused"] or gate_refused) else 1


if __name__ == "__main__":
    sys.exit(main())
