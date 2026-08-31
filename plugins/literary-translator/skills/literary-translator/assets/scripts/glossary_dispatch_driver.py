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
resume. It does NOT defend against a hostile codex job: the snapshot, `index.json` and the
evidence bodies stay under `RUN_DIR`, which every agent in this pass can write. That is the
shipped design's own position, not a gap this file opens -- see `approvalRecordPath()`'s
comment in the template ("buys no defence against a hostile agent ... what it closes is the
case that happens WITHOUT malice"). Closing the hostile class means confining what the
dispatch job may write, which is a separate change.

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
import json
import os
import re
import secrets
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
    "fragmentPath", "manifestPath", "checkBatchCmd",
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
    """The ONE JSON line on stdout."""
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True), flush=True)


# ---------------------------------------------------------------------------
# BYTE-IDENTICAL COPIES -- do not edit here alone.
#
# This plugin ships no shared util module: every script is self-contained, so a
# cross-cutting helper is DUPLICATED byte-for-byte rather than imported. These
# two come from segment_dispatch_driver.py and must stay equal to it;
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


def validate_run_id(name: str) -> "str | None":
    """Returns a refusal string, or None when the id is safe to splice into a
    filesystem path. Allowlist, never a denylist of shell metacharacters."""
    if not name:
        return "run id is empty"
    if not _RUN_ID_RE.fullmatch(name):
        return f"unsafe run id: {name!r}"
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

    ${durable_root}/ is writable by the very codex jobs this driver dispatches
    (see the module docstring's hand-back section), so a durable copy of the
    template is model-writable JavaScript that this process would then run --
    which is why there is no durable fallback here at all. The plugin install
    tree is the only accepted source, and it is the same value SKILL.md already
    threads as {{PLUGIN_ROOT}}.

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


def enforce_local_cap(n_batches: int, max_citation_retries: int,
                      batch_agent_cap: int) -> int:
    """The driver's REAL agent-call bound: one judge per batch per attempt, and
    nothing else. Returns the worst-case judge count.

    This is the number that must fit under engine.batch_agent_cap -- not the
    template's own estimate, which counts a dispatch, a chunked wait and a
    re-check per attempt that this driver performs in-process. Enforcing the
    template's estimate would refuse runs the driver can comfortably afford
    (measured shape: 7 batches under a cap of 100 need at most 21 judges against
    a 114-call Workflow estimate)."""
    judges = n_batches * (max_citation_retries + 1)
    if judges > batch_agent_cap:
        fatal(
            f"this run's worst-case judge count ({judges} = {n_batches} batches "
            f"x {max_citation_retries + 1} attempts) exceeds engine."
            f"batch_agent_cap ({batch_agent_cap})",
            exit_code=1, judges=judges, cap=batch_agent_cap, batches=n_batches,
        )
    return judges
