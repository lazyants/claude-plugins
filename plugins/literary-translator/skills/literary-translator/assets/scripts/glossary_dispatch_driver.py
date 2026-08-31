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
# the gap: a hostile codex job. The snapshot, index.json and the evidence bodies
# live under RUN_DIR, which every agent in this pass can write, so a job willing
# to forge can swap them between PREPARE and the judge, or between the digest
# check and the merge. That is the shipped design's own position -- the template's
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
    path = Path(raw).absolute()
    droot = durable_root.absolute()
    try:
        path.relative_to(droot)
    except ValueError:
        pass
    else:
        fatal(
            f"--verdict-dir {path} resolves inside the durable root {droot}. "
            "Every codex job this driver dispatches can write there, so a "
            "verdict placed in it would be forgeable by the very jobs it "
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
        return {"entries": []}
    with os.fdopen(fd, "r", encoding="utf-8") as fh:
        try:
            obj = json.load(fh)
        except ValueError:
            fatal("the pending state file is not readable JSON -- refusing to "
                  "guess which batches were awaiting a judge", exit_code=2)
    if not isinstance(obj, dict) or not isinstance(obj.get("entries"), list):
        fatal("the pending state file has an unrecognised shape", exit_code=2)
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


def pending_key(batch_index: int, attempt: int) -> str:
    return f"{batch_index}:{attempt}"


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
    import shlex
    argv = shlex.split(cmd)
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return 124, "", f"timed out after {timeout}s"
    except OSError as exc:
        return 127, "", repr(exc)
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def resolve_companion() -> str:
    """The installed codex-companion.mjs path, via the shipped resolver. Run from
    the DURABLE copy, exactly as segment_dispatch_driver.py does: a self-anchored
    driver has no plugin root to run the resolver from."""
    _refuse_unless_executable_leaf(RESOLVE_COMPANION_SCRIPT, "resolve_codex_companion.py")
    proc = subprocess.run(
        [sys.executable, str(RESOLVE_COMPANION_SCRIPT)],
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


def launch_codex(*, companion: str, node_bin: str, prompt: str, effort: str,
                 durable_root: Path, tmpdir: Path, label: str) -> None:
    """Fires one background codex turn for a batch.

    The argv mirrors codex_job.py's own launch() rather than a reduced guess:

      task --background --json --write --fresh [--effort E] --cwd R --prompt-file P

    --write is NOT optional. Read-only was #198's no-output failure: codex cannot
    create the fragment without it, so every batch would poll to its deadline and
    report glossary-pass-null. --fresh gives each attempt its own thread.

    --cwd is the DURABLE ROOT, not a per-job sandbox, and that is a deliberate
    difference from codex_job.py. This pass's contract is that codex writes its
    fragment directly into glossary/runs/<RUN_ID>/, so a sandbox outside the repo
    -- which is what makes codex_job.py's #409 confinement work -- would leave the
    job unable to produce the artifact the whole pass waits on. Relative to what
    this replaces it is a NARROWING: the pass currently reaches codex through the
    codex:codex-rescue forwarder, whose workspace-write root resolves by git
    walk-up from whatever cwd the session happens to hold. It is not confinement,
    and the module docstring says so rather than implying otherwise."""
    prompt_file = tmpdir / f"prompt-{label}.txt"
    prompt_file.write_text(prompt, encoding="utf-8")
    argv = [node_bin, companion, "task", "--background", "--json", "--write", "--fresh"]
    if effort:
        argv += ["--effort", effort]
    argv += ["--cwd", str(durable_root), "--prompt-file", str(prompt_file)]
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DriverError(f"codex launch failed for {label}: {exc!r}", label=label)
    if proc.returncode != 0:
        raise DriverError(
            f"codex launch returned {proc.returncode} for {label}",
            label=label, launch_stderr=(proc.stderr or "")[-1000:],
        )


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

    Read ONCE, immediately after the fetch, and never again: index.json lives
    under RUN_DIR, which a still-running codex job can overwrite, so the pairs
    are captured at the one moment they are known to describe this fetch."""
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
    rather than symmetrical. The attempt path is mutable -- the codex job that
    wrote it may still be rewriting it, which the template states at its snapshot
    -ordering comment -- so a position derived from the snapshot and applied to
    the attempt file can land on a different row after a reorder, and
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

def poll_until(predicate, *, deadline_sec: float, poll_sec: float, label: str) -> bool:
    """Local bounded poll. Replaces the template's chunked-wait apparatus
    wholesale: that machinery exists because a Bash tool call is clamped at
    600s and an agent's wait had to be split across several calls to stay under
    it. A local process has no such clamp, so none of it is reimplemented here."""
    deadline = time.monotonic() + deadline_sec
    while True:
        if predicate():
            return True
        if time.monotonic() >= deadline:
            log(f"{label}: deadline of {deadline_sec}s expired")
            return False
        time.sleep(min(poll_sec, max(0.0, deadline - time.monotonic())))


class Ctx:
    """Everything the per-batch machine needs, resolved once."""

    def __init__(self, *, template: Path, subst: dict, batches: list, node_bin: str,
                 companion: str, durable_root: Path, run_dir: Path, verdict_dir: Path,
                 research_mode: str, effort: str, poll_sec: float, deadline_sec: float,
                 max_citation_retries: int, tmpdir: Path):
        self.template = template
        self.subst = subst
        self.batches = batches
        self.node_bin = node_bin
        self.companion = companion
        self.durable_root = durable_root
        self.run_dir = run_dir
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
        {"key": "check", "fn": "checkBatchCmd", "args": [idx, attempt]},
        {"key": "dispatch", "fn": "batchDispatchPrompt",
         "args": [batch, attempt, rejection_reason]},
    ])
    fragment_path = Path(built["fragment"])

    # 1. DISPATCH -- skipped for a resumed attempt 0, whose fragment
    #    resume_setup.py already re-checked before this driver ever ran.
    if not (resumed and attempt == 0):
        log(f"batch {idx}: dispatching codex (attempt {attempt})")
        launch_codex(companion=ctx.companion, node_bin=ctx.node_bin,
                     prompt=strip_routing_line(built["dispatch"]), effort=ctx.effort,
                     durable_root=ctx.durable_root, tmpdir=ctx.tmpdir,
                     label=f"dispatch-{idx}-{attempt}")

        # 2. POLL -- the SAME --check-batch command the dispatch prompt told codex
        #    to self-check with, so readiness here and readiness there are one
        #    question asked once, spliced from one builder.
        ready = poll_until(lambda: _cmd_ok(built["check"], 300),
                           deadline_sec=ctx.deadline_sec, poll_sec=ctx.poll_sec,
                           label=f"batch {idx} attempt {attempt}")
        if not ready:
            # Unchanged reason string: the recovery docs key off it.
            return {"state": "failed", "reason": "glossary-pass-null",
                    "batchIndex": idx, "attempt": attempt,
                    "fragmentPath": str(fragment_path)}
    else:
        log(f"batch {idx}: resume-skip -- attempt 0 fragment already validated")

    # 3. OFFLINE -- research_mode forbids basis:"established" outright and
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
    code, _out, err = run_template_cmd(built["approve"], timeout=600)
    if code != 0:
        log(f"batch {idx}: could not snapshot attempt {attempt}: {err[-400:]}")
        return {"state": "evidence_failed", "batchIndex": idx, "attempt": attempt,
                "reason": "approve-failed"}

    # 5. FETCH -- the one network step, and the only one. This process launches it
    #    and never reads what it retrieved (see read_outcome_pairs).
    code, _out, err = run_template_cmd(built["fetch"], timeout=1800)
    if code != 0:
        log(f"batch {idx}: citation fetch failed for attempt {attempt}: {err[-400:]}")
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
        "key": pending_key(idx, attempt),
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
        {"key": "repair", "fn": "batchRepairPrompt",
         "args": [batch, attempt, failed_rows, None]},
        {"key": "repairpath", "fn": "repairFragmentPath", "args": [idx, attempt]},
        {"key": "nextfragment", "fn": "fragmentPath", "args": [idx, attempt + 1]},
        {"key": "nextcheck", "fn": "checkBatchCmd", "args": [idx, attempt + 1]},
    ])
    repair_path = Path(built["repairpath"])
    next_fragment = Path(built["nextfragment"])

    # A stale repair artifact from an earlier run of this same RUN_ID would sit at
    # exactly this attempt-scoped path, and the poll below would accept it. Nothing
    # in resume_setup.py wipes it -- this artifact is the driver's alone -- so the
    # driver removes it itself rather than teaching another script its filename.
    try:
        repair_path.unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise DriverError(f"could not clear a stale repair fragment: {exc!r}")

    log(f"batch {idx}: repairing {len(failed_positions)} unretrievable citation(s) "
        f"into rung {attempt + 1}")
    launch_codex(companion=ctx.companion, node_bin=ctx.node_bin,
                 prompt=strip_routing_line(built["repair"]), effort=ctx.effort,
                 durable_root=ctx.durable_root, tmpdir=ctx.tmpdir,
                 label=f"repair-{idx}-{attempt}")

    ok = poll_until(repair_path.exists, deadline_sec=ctx.deadline_sec,
                    poll_sec=ctx.poll_sec, label=f"batch {idx} repair {attempt}")
    if not ok:
        return {"state": "repair_invalid", "reason": "repair-never-written"}
    try:
        repair_rows = load_rows(repair_path)
        validate_repair_rows(repair_rows, expected_forms)
    except DriverError as exc:
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
    return {"state": "repaired", "attempt": attempt + 1}


def drive_batch(ctx: Ctx, batch: dict, resumed_indices: "set[int]") -> dict:
    """Runs one batch's ladder until it needs a judge, or ends.

    The ladder is bounded by MAX_CITATION_RETRIES exactly as the template's is:
    attempts 0..MAX_CITATION_RETRIES, and a repair reserves the next rung rather
    than adding one."""
    idx = batch["index"]
    attempt = 0
    rejection_reason = None
    while True:
        result = advance_batch(ctx, batch, attempt,
                               resumed=idx in resumed_indices,
                               rejection_reason=rejection_reason)
        state = result["state"]

        if state in ("awaiting_judge", "ready", "failed"):
            return result

        if state == "needs_repair":
            # TERMINAL RUNG: there is no attempt+1 to reserve, so repair is not
            # dispatched at all. Without this the repair would create a forbidden
            # attempt beyond the ladder and break the judge cap the driver
            # enforces up front.
            if attempt >= ctx.max_citation_retries:
                log(f"batch {idx}: unretrievable citation(s) at the terminal "
                    f"attempt {attempt}; no repair is dispatched")
                return {"state": "failed", "batchIndex": idx, "attempt": attempt,
                        "reason": "citation-review-exhausted",
                        "attemptsUsed": attempt + 1,
                        "lastRejection": "citations did not retrieve at the final "
                                         "attempt: " + repr(result["failedPositions"])}
            repaired = run_repair(ctx, batch, attempt, result["failedPositions"],
                                  Path(result["snapshotPath"]))
            # Either way the batch moves to the SAME reserved rung: a valid repair
            # lands its spliced fragment there, an invalid one regenerates there.
            attempt += 1
            if repaired["state"] == "repair_invalid":
                rejection_reason = (
                    "the previous attempt's citations could not be retrieved and a "
                    "per-item repair could not be applied")
                continue
            # Repaired in place: re-approve and re-fetch the spliced fragment, with
            # a fresh PREPARE nonce, before any judge sees it.
            built = ctx.build([{"key": "fragment", "fn": "fragmentPath",
                                "args": [idx, attempt]}])
            return prepare_and_hand_back(ctx, batch, attempt,
                                         Path(built["fragment"]))

        if state == "evidence_failed":
            if attempt >= ctx.max_citation_retries:
                return {"state": "failed", "batchIndex": idx, "attempt": attempt,
                        "reason": "citation-review-exhausted",
                        "attemptsUsed": attempt + 1,
                        "lastRejection": result.get("reason")}
            rejection_reason = (
                "the previous attempt's citation evidence could not be prepared: "
                + str(result.get("reason")))
            attempt += 1
            continue

        raise DriverError(f"internal error: unhandled batch state {state!r}")


# ---------------------------------------------------------------------------
# CONSUMING VERDICTS
# ---------------------------------------------------------------------------

def record_verdicts(ctx: Ctx, verdicts_path: Path) -> dict:
    """Consumes the session's judge replies.

    A verdict is refused unless it matches its pending entry on ALL of
    durable_root, run_id, batch, attempt, nonce and the snapshot digest RE-HASHED
    now -- and the nonce is consumed, so the same verdict cannot be applied twice.
    The reply itself is read with the TEMPLATE's own rejectedAnywhere +
    sentinelVerdict, never a Python re-implementation of them: those two carry the
    containment-guard-then-positive-proof discipline that #228/#308 exist for, and
    a second reader would be a second, drifting set of rules."""
    try:
        with open(verdicts_path, "r", encoding="utf-8") as fh:
            supplied = json.load(fh)
    except (OSError, ValueError) as exc:
        fatal(f"could not read --record-verdicts file: {exc!r}", exit_code=2)
    if not isinstance(supplied, list):
        fatal("--record-verdicts must hold a JSON array of "
              "{batch, attempt, nonce, reply} objects", exit_code=2)

    pending = read_pending(ctx.verdict_dir)
    by_key = {e["key"]: e for e in pending["entries"] if isinstance(e, dict)}
    results, refusals = [], []

    for item in supplied:
        if not isinstance(item, dict):
            refusals.append({"reason": "verdict entry is not an object"})
            continue
        batch_i, attempt = item.get("batch"), item.get("attempt")
        # Shape-check BEFORE the key is formed. A verdict file is written by the
        # session, so a missing or non-integer field is an ordinary malformed
        # input, not an impossible one -- and pending_key() would otherwise build
        # a key like "None:None" that could never match and would report itself
        # as "no pending entry", hiding the real defect behind a plausible one.
        if not isinstance(batch_i, int) or not isinstance(attempt, int):
            refusals.append({"batch": batch_i, "attempt": attempt,
                             "reason": "verdict entry has a non-integer batch or "
                                       "attempt"})
            continue
        key = pending_key(batch_i, attempt)
        entry = by_key.get(key)
        if entry is None:
            refusals.append({"batch": batch_i, "attempt": attempt,
                             "reason": "no pending entry -- this batch/attempt is "
                                       "not awaiting a judge in this run"})
            continue
        if entry.get("consumed"):
            refusals.append({"batch": batch_i, "attempt": attempt,
                             "reason": "this verdict's nonce was already consumed"})
            continue
        if item.get("nonce") != entry["nonce"]:
            refusals.append({"batch": batch_i, "attempt": attempt,
                             "reason": "nonce mismatch -- the verdict does not name "
                                       "the PREPARE it is answering"})
            continue
        if entry["durable_root"] != str(ctx.durable_root) or \
                entry["run_id"] != ctx.subst["run_id"]:
            refusals.append({"batch": batch_i, "attempt": attempt,
                             "reason": "pending entry belongs to another run or root"})
            continue

        snapshot = Path(ctx.build([{"key": "p", "fn": "approvedPath",
                                    "args": [batch_i, attempt]}])["p"])
        try:
            current_digest = _sha256_file(snapshot)
        except OSError as exc:
            refusals.append({"batch": batch_i, "attempt": attempt,
                             "reason": f"the approved snapshot is unreadable: {exc!r}"})
            continue
        if current_digest != entry["snapshot_sha256"]:
            # The bytes moved under the verdict. This is the resume-replay case:
            # same run id, same paths, regenerated content.
            refusals.append({"batch": batch_i, "attempt": attempt,
                             "reason": "the approved snapshot's bytes changed since "
                                       "the judge prompt was rendered"})
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
        entry["consumed"] = _utc_now_iso()

        if read["contained"] or not read["verdict"]:
            results.append({"batch": batch_i, "attempt": attempt,
                            "approved": False, "rejection": read["detail"]})
            continue

        record = ctx.build([{"key": "cmd", "fn": "recordApprovalCmd",
                             "args": [batch_i, attempt]}])["cmd"]
        code, _out, err = run_template_cmd(record, timeout=600)
        if code != 0:
            # The review DID approve; the bookkeeping write failed. Refuse the
            # merge rather than merging an approved set nobody can reconstruct --
            # the same direction the template's own unrecordedBatches gate takes.
            results.append({"batch": batch_i, "attempt": attempt, "approved": True,
                            "approvalRecorded": False,
                            "detail": (err or "")[-300:]})
            continue
        record_path = ctx.build([{"key": "p", "fn": "approvalRecordPath",
                                  "args": [batch_i, attempt]}])["p"]
        ready_entry = {"batchIndex": batch_i, "attempt": attempt,
                       "mergePath": str(snapshot), "approvalRecordPath": record_path,
                       "approvalRecorded": True, "citationReview": "approved"}
        pending.setdefault("ready", [])
        pending["ready"] = [r for r in pending["ready"]
                            if r.get("batchIndex") != batch_i] + [ready_entry]
        results.append({"batch": batch_i, "attempt": attempt, "approved": True,
                        "approvalRecorded": True, "mergePath": str(snapshot)})

    pending["entries"] = list(by_key.values())
    write_pending(ctx.verdict_dir, pending)
    return {"recorded": results, "refused": refusals,
            "ready": pending.get("ready", [])}


# ---------------------------------------------------------------------------
# MERGE -- the one serialized write into canon.json.
# ---------------------------------------------------------------------------

def merge_and_verify(ctx: Ctx, ready: list) -> dict:
    """One --merge-batches over every ready batch in index order, then the
    disk-independent --verify-merged.

    All-or-nothing, exactly as the Workflow's is: merging some batches while
    dropping one would freeze a partial canon and leave the dropped candidates
    looking like they were never researched.

    Refused outright if any approved batch lacks its approval record.
    canon_validate.py's --approval-records reader can only make the merge FAIL,
    never permit anything, and this gate keeps that direction: a batch whose
    record could not be written does not get merged on the strength of this
    driver having seen an approval."""
    unrecorded = [r["batchIndex"] for r in ready
                  if r.get("citationReview") != "skipped-offline"
                  and not r.get("approvalRecorded")]
    if unrecorded:
        return {"merged": False, "reason": "approval-records-missing",
                "unrecordedBatches": sorted(unrecorded)}

    ordered = sorted(ready, key=lambda r: r["batchIndex"])
    fragments = [r["mergePath"] for r in ordered]
    records = [r["approvalRecordPath"] for r in ordered
               if r.get("approvalRecordPath")]

    built = ctx.build([
        {"key": "merge", "fn": "mergeBatchesCmd", "args": [fragments, records]},
        {"key": "verify", "fn": "verifyMergedCmd", "args": [fragments]},
    ])
    code, out, err = run_template_cmd(built["merge"], timeout=1800)
    if code != 0:
        return {"merged": False, "reason": "merge-failed",
                "detail": (err or out or "")[-600:]}

    # Disk-independent: --verify-merged fresh-reads canon.json and every listed
    # fragment rather than trusting the merge call's own claim (#88). Run against
    # the SAME mergePath values, in the same order.
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
    return {"merged": True, "batches": [r["batchIndex"] for r in ordered]}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="glossary_dispatch_driver.py",
        description="Local driver for the W3a canon-and-glossary pass (#800).",
    )
    p.add_argument("--run-id", dest="run_id",
                   help="the glossary RUN_ID resume_setup.py accepted")
    p.add_argument("--batches-file", dest="batches_file",
                   help="the glossary_batch_plan.py args array, as JSON")
    p.add_argument("--verdict-dir", dest="verdict_dir", required=True,
                   help="session-owned directory for pending state and verdicts; "
                        "MUST be outside the durable root")
    p.add_argument("--plugin-root", dest="plugin_root", required=True,
                   help="the plugin install root; the ONLY copy of the workflow "
                        "template this driver will execute")
    p.add_argument("--record-verdicts", dest="record_verdicts",
                   help="consume a JSON array of {batch, attempt, nonce, reply}")
    p.add_argument("--source-lang", dest="source_lang", default="")
    p.add_argument("--target-lang", dest="target_lang", default="")
    p.add_argument("--research-mode", dest="research_mode", default="live",
                   choices=("live", "offline"))
    p.add_argument("--effort", default="high")
    p.add_argument("--citation-content-types", dest="citation_content_types",
                   default="")
    p.add_argument("--batch-agent-cap", dest="batch_agent_cap", type=int,
                   default=3500)
    p.add_argument("--max-citation-retries", dest="max_citation_retries", type=int,
                   default=2)
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
    template = resolve_template(args.plugin_root)

    try:
        batches = json.loads(Path(args.batches_file).read_text(encoding="utf-8")) \
            if args.batches_file else []
    except (OSError, ValueError) as exc:
        fatal(f"could not read --batches-file: {exc!r}", exit_code=2)
    if not isinstance(batches, list) or not batches:
        fatal("--batches-file must hold a non-empty JSON array of batches",
              exit_code=2)
    try:
        resumed = set(json.loads(args.resumed_batch_indices))
    except ValueError:
        fatal("--resumed-batch-indices must be a JSON array", exit_code=2)

    # The harness is loaded past the template's own Workflow-shaped preflight; the
    # REAL bound is enforced here, against the profile's cap.
    judges = enforce_local_cap(len(batches), args.max_citation_retries,
                               args.batch_agent_cap)

    subst = {
        "durable_root": str(durable_root),
        "source_lang": args.source_lang,
        "target_lang": args.target_lang,
        "research_mode": args.research_mode,
        "run_id": args.run_id,
        "effort": args.effort,
        "citation_content_types": args.citation_content_types,
        # Loaded past, never enforced -- see enforce_local_cap.
        "batch_agent_cap": 10 ** 9,
        "plugin_root": str(Path(args.plugin_root).absolute()),
        "resumed_batch_indices": sorted(resumed),
    }

    with tempfile.TemporaryDirectory(prefix="glossary-driver-") as tmp:
        ctx = Ctx(template=template, subst=subst, batches=batches,
                  node_bin=args.node_bin,
                  companion="" if args.record_verdicts else resolve_companion(),
                  durable_root=durable_root,
                  run_dir=durable_root / "glossary" / "runs" / args.run_id,
                  verdict_dir=verdict_dir, research_mode=args.research_mode,
                  effort=args.effort, poll_sec=args.poll_sec,
                  deadline_sec=args.deadline_sec,
                  max_citation_retries=args.max_citation_retries,
                  tmpdir=Path(tmp))

        if args.record_verdicts:
            out = record_verdicts(ctx, Path(args.record_verdicts))
            ready = out.pop("ready", [])
            payload = {"action": "record-verdicts", "run_id": args.run_id, **out,
                       "ready": [r["batchIndex"] for r in ready],
                       "merged": False, "generated": _utc_now_iso()}
            # THE MERGE HAPPENS HERE ON A LIVE RUN, and it has to: a live batch
            # never reaches `ready` on the driving invocation -- it hands back for
            # a judge and exits -- so the drive path's merge branch is reachable
            # only under offline. The run is complete when every batch this run
            # was given has an approved, recorded entry and nothing is still
            # awaiting a judge; only then is the all-or-nothing merge attempted.
            still_awaiting = [e for e in read_pending(verdict_dir)["entries"]
                              if isinstance(e, dict) and not e.get("consumed")]
            expected = {b.get("index") for b in batches}
            have = {r.get("batchIndex") for r in ready}
            if not still_awaiting and not out["refused"] and expected <= have:
                payload.update(merge_and_verify(ctx, ready))
            else:
                payload["reason"] = "awaiting-more-verdicts"
                payload["awaiting"] = sorted(expected - have)
            emit(payload)
            return 0 if not out["refused"] else 1

        needs_judge, ready, failed = [], [], []
        pending = read_pending(verdict_dir)
        pending_by_key = {e["key"]: e for e in pending["entries"]
                          if isinstance(e, dict)}

        for batch in batches:
            try:
                result = drive_batch(ctx, batch, resumed)
            except DriverError as exc:
                failed.append({"batchIndex": batch.get("index"),
                               "reason": str(exc), **exc.extra})
                continue
            if result["state"] == "awaiting_judge":
                pending_by_key[result["pending"]["key"]] = result["pending"]
                needs_judge.append({
                    "batch": result["batchIndex"], "attempt": result["attempt"],
                    "nonce": result["pending"]["nonce"],
                    "judgePrompt": result["judgePrompt"],
                    "agentType": "literary-translator:citation-judge",
                })
            elif result["state"] == "ready":
                ready.append(result)
            else:
                failed.append(result)

        pending["entries"] = list(pending_by_key.values())
        write_pending(verdict_dir, pending)

        payload = {
            "action": "drive", "run_id": args.run_id,
            "worstCaseJudgeCalls": judges,
            "needs_judge": needs_judge,
            "ready": [r["batchIndex"] for r in ready],
            "not_ready": failed,
            "merged": False,
            "generated": _utc_now_iso(),
        }
        # Offline never hands back, so an offline run reaches the merge in ONE
        # invocation. Live runs merge on the invocation that consumes the last
        # verdict, which is the session's next call.
        if not needs_judge and not failed and ready:
            payload.update(merge_and_verify(ctx, ready))
        emit(payload)
        return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
