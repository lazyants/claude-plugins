#!/usr/bin/env python3
"""reject_review.py -- #461: the ONLY component allowed to create
segments/{seg}.review_rejected.json, the durable record that a stored
review verdict was judged UNFOUNDED and must not gate a fix round.

WHY THIS EXISTS. A review round can be well-formed (schema-valid,
authentic `loc`, not the fabricated_loc gate's problem) and still be
WRONG about the source: verified on a live segment where the sole finding
claimed a Hebrew string that occurs zero times in the block. Nothing was
applied to the draft -- correctly, there was nothing real to apply -- but
derive_next_action()'s not-clean branch (segment_dispatch_driver.py) has
no way to tell "the draft is unchanged because the fix was already
correct" apart from "the draft is unchanged because nothing was ever
attempted": both look identical on disk (draft_sha1 matches the review),
and the branch returns needs_fix forever, rendering a fix prompt for a
segment with nothing to fix. This script is the operator's way to say,
durably and auditably, "this specific verdict does not bind" -- so
derive_next_action() can advance to a fresh review round instead of
looping on a fix that was never wrong to skip.

WHY A MARKER FILE, and not the two obvious alternatives -- the same two
claim_record.py's own module docstring rules out, for the identical
reasons: the review document itself is `additionalProperties: false`
(review.schema.json), and the ledger fragment is erased by the next
full-replace write (ledger_update.py's own write_fragment_atomically()),
which is precisely the gap `.ever_converged` was created to close for a
different fact.

NO COMPONENT THAT CONSUMES THE REJECTION MAY CREATE ONE -- mirroring how
claims work in this codebase (claim_record.py's write_claim_record() is
the sole writer; every other module only reads). derive_next_action()
(segment_dispatch_driver.py) reads this artifact and NEVER writes it, by
construction: nothing in that function opens this path for writing.

THE REJECTION NEVER MAKES A TRANSLATE REACHABLE. It only ever converts a
would-be needs_fix into a fresh review at the next round label -- see
derive_next_action()'s own consuming branch for the full reasoning. This
script has no opinion on that at all; it only ever writes the artifact,
gated by the six refusals below.

REFUSES UNLESS ALL SIX HOLD, and refuses (never guesses) whenever any
of them cannot be established:

  1. segments/{seg}.review.json exists, parses as a JSON object, and
     validates FULLY against review.schema.json (the real jsonschema
     library, matching review_ready.py's own approach) -- AND its
     `clean` field is the literal `False`. Schema validation guarantees
     `clean` is present and boolean, so no separate "is it a bool" check
     is needed once validation passes. `coverage_ok` is deliberately NOT
     part of this gate: the #461 defect is about FINDINGS being
     unfounded (clean: false), a different fact from coverage being
     incomplete, and derive_next_action()'s own consuming branch is
     reached for either reason -- this script only ever asserts the one
     fact an operator can actually judge by reading a finding: whether
     it is real.

  2. --reason is supplied and, after stripping whitespace, non-empty.
     A rejection with no stated reason is unauditable -- the durable
     record exists specifically so a later reviewer can see WHY a
     verdict was set aside, per the #438 claim_record.py precedent of
     capturing evidence in the artifact that destroys standing (its own
     `pre_claim_review` field), never only in a log line from a process
     that has since exited.

  3. --expect-token EXACTLY equals the stored review's own
     `dispatch_token`. This is "the review it is rejecting is the one
     currently on disk" -- byte-for-byte the same TOCTOU discipline
     review_ready.py's own REQUIRED --expect-token already established
     for "is this review the one I think it is" (see that script's own
     docstring). The operator names the review they read; a stale or
     wrong token refuses rather than rejecting some OTHER verdict by
     accident.

  4. --expect-verdict-digest EXACTLY equals _review_verdict_digest()
     recomputed from the review currently on disk. THE TOKEN ALONE
     CANNOT SAY WHICH VERDICT WAS READ: review_dispatch_token()
     (segment_dispatch_driver.py) is a pure function of (run_id, seg,
     round_label), so every retry INSIDE one round -- a re-dispatched
     review after a codex failure, a second attempt at the same label --
     mints the IDENTICAL token. An operator who reads verdict V1, judges
     its findings unfounded and names V1's token would therefore
     silently authorize a V2 that replaced it under that same token,
     whose findings nobody ever read. The digest is over the WHOLE
     review object (see _review_verdict_digest()), so it moves with any
     byte of the verdict, and it is the exact value the consumer
     re-derives at consume time -- what this gate binds is precisely
     what derive_next_action() will later compare. A mismatch refuses
     naming BOTH digests, because which one is stale decides the
     operator's next move. THE VALUE IS OBTAINABLE, which a required
     flag over a private hash function otherwise would not be:
     `--print-verdict-digest` (below) prints it, and every refusal that
     demands the digest names that command with this segment already
     substituted in. What it deliberately does NOT do is fill the flag
     in automatically -- an auto-filled digest would describe whatever
     is on disk at write time instead of what a human inspected, which
     is the hole this gate exists to close.

  5. --round-label agrees with the round label the stored review's own
     dispatch_token encodes (round_label_from_token()). An audit field
     an operator types freely is one that can be wrong exactly when it
     matters -- the record would then attest a round the review it names
     never belonged to. Agreement is CHECKED, never substituted: a
     mismatch refuses naming both labels rather than quietly recording
     the derived one, because a caller that believes it is rejecting
     round 2 while the review on disk is round 3 has a worse problem
     than a mislabelled field.

  6. Nothing CONFLICTING already occupies
     segments/{seg}.review_rejected.json. A record naming the same
     (dispatch_token, verdict_digest) as this invocation but a DIFFERENT
     reason refuses: re-running with new wording would erase the first
     stated reason -- the entire audit value of the artifact -- and
     nothing here can tell a deliberate correction from one operator
     overwriting a colleague's record. A byte-identical reason is an
     idempotent no-op SUCCESS: nothing is rewritten, so the original
     rejected_at and operator_invocation survive. A record naming a
     DIFFERENT verdict is stale by construction and is replaced (see
     ATOMIC WRITE below); anything else at that path -- unreadable,
     malformed, not a regular file, or a JSON object whose key set is
     not the pinned seven -- refuses rather than being silently
     destroyed.

--round-label is REQUIRED and is CHECKED against the stored review,
never derived from driver state. The label is read back out of that
review's own dispatch_token, whose shape review_dispatch_token()
(segment_dispatch_driver.py) fixes as `{run_id}:{seg}:r{round_label}`.
What this script still does NOT reimplement is
_matched_review_round_label()'s different question -- "does this token
belong to THIS run, within max_fix_rounds" -- which needs run_id and
max_fix_rounds, context a standalone leaf script is never given, per
this project's "leaf scripts don't reconstruct driver state" convention
(review_ready.py's own docstring: "a LEAF with no siblings of its own to
resolve"). Nor does it need to: gate 3 binds the WHOLE token, run_id
half included, to the review on disk, so the only thing left to check is
that the operator's label agrees with the one that token carries.
derive_next_action()'s own consuming branch still does not read this
field back -- it uses its own freshly-computed matched_round_label,
never a value re-read from a file an operator typed by hand -- so the
field remains audit-only in EFFECT; what changed is that it can no
longer be audit-only in TRUTH.

ATOMIC WRITE: temp file + fsync + os.replace + directory fsync -- a
FULL-REPLACE write, deliberately NOT claim_record.py's own
O_CREAT|O_EXCL. A claim is a one-time authorization that must never be
silently overwritten (claim_record.py's own docstring: "overwriting would
destroy the one thing the record exists to preserve"). A rejection is
different: a segment can legitimately need a SECOND rejection later (a
different round's review, also unfounded), and the artifact only ever
needs to describe the MOST RECENT rejection for derive_next_action()'s
token+digest match to work -- so replacing an old, already-stale
rejection with a new one is correct, not data loss. The directory-fsync
half reuses claim_record.py's own fsync_directory() rather than a second
copy -- see _import_claim_record() below for why this is a sibling
IMPORT, not a duplicated helper: it is a small, easily-drifted primitive,
and this project already has one shared copy for exactly this purpose.

A FAILED DURABILITY STEP LEAVES NO LIVE AUTHORIZATION. If
fsync_directory() fails after the os.replace(), the record is UNLINKED
and the command reports the failure -- the opposite of what
claim_record.py's write_claim_record() does with its own record, and
write_rejection_record()'s own comment says why the two differ.

CLI -- two modes, the read one first because it is how the write one is
used:

    python3 reject_review.py SEG --print-verdict-digest [--durable-root PATH]

    python3 reject_review.py SEG --reason TEXT --round-label LABEL \\
        --expect-token TOK --expect-verdict-digest HEX64 \\
        [--durable-root PATH] [--plugin-root PATH]

--print-verdict-digest is a PURE READ: it prints the stored review's own
dispatch_token, verdict_digest and round label -- every value the second
form demands -- from ONE read, and writes nothing whatsoever (no record,
no directory, no sibling import). Both values come from that single read
on purpose: fetched separately, a token from before a re-dispatch and a
digest from after it form a pair that never described any one verdict.

--expect-verdict-digest is REQUIRED, which makes this CLI a BREAKING
change from the shape that bound only --expect-token. That is the right
direction for an authorization tool: a caller that has not been updated
refuses loudly instead of quietly authorizing on a weaker binding than
it believes it is using. Required and OBTAINABLE are not in tension --
the read mode above is the whole answer to "where do I get this value",
and it is named in every refusal that asks for it.

Exit 0 = written: prints the artifact (one JSON object, `{"success": true,
...}`) on stdout. An idempotent re-run (gate 6) also exits 0, printing
the record ALREADY on disk with `"already_recorded": true` -- nothing was
rewritten, so `rejected_at` and `operator_invocation` still describe the
first invocation. --print-verdict-digest exits 0 with
`{"success": true, "seg", "review_path", "dispatch_token",
"verdict_digest", "round_label", "round_label_problem"}` -- the last two
always both present, `round_label` null and `round_label_problem` set
when the token's label cannot be read, so a caller never has to branch on
which keys exist.
Exit 1 = refused: prints `{"success": false, "error": "..."}` -- the same
shape every one of the six refusals above produces, so a caller can
branch on `success` alone regardless of WHICH refusal fired. EVERY error
path emits this envelope, including the ones that are not gates at all
(an unreadable schema, a parent directory that cannot be created, an
unexpected internal failure -- see the __main__ backstop).
Exit 2 = usage error (bad seg id, or argparse's own complaint about a
genuinely missing/malformed flag).
"""
import argparse
import fcntl
import hashlib
import json
import os
import re
import shlex
import stat
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import NoReturn

try:
    import jsonschema
    import jsonschema.exceptions
    import jsonschema.validators
except ImportError as e:
    print(json.dumps({
        "success": False,
        "error": (
            "missing required dependency 'jsonschema' (>=4.26.0). Install "
            f"with: pip install -r requirements.txt (import error: {e})"
        ),
    }))
    sys.exit(1)

# Self-anchored by default: this script always lives at
# ${durable_root}/scripts/reject_review.py, so parents[1] is the durable
# root -- byte-for-byte the same convention review_ready.py/ledger_update.py
# already use. Never assumes cwd.
_SCRIPT_FILE = Path(__file__).resolve()
SCRIPTS_DIR = _SCRIPT_FILE.parent
DURABLE_ROOT = _SCRIPT_FILE.parents[1]
SEGMENTS_DIR = DURABLE_ROOT / "segments"
SCHEMAS_DIR = DURABLE_ROOT / "schemas"


def resolve_dirs(durable_root_str, plugin_root_str=None):
    """LT-409 convention, byte-for-byte review_ready.py's own split:
    `durable_root_str` governs DATA (segments/schemas) -- rebuilt from that
    root when given, self-anchored otherwise. `plugin_root_str` governs
    where the claim_record.py SIBLING this script imports (for
    fsync_directory()) is found -- deliberately NEVER derived from
    `durable_root_str`, for the identical tampered-copy reason
    review_ready.py's own resolve_dirs() states for draft_sha1.py:
    ${durable_root}/scripts/ is a Step-0a copy other processes in this
    pipeline can write to, so resolving a helper this script trusts from
    inside the thing a rejection is meant to correct would let a tampered
    copy validate itself."""
    if durable_root_str is None:
        durable_root = DURABLE_ROOT
        segments_dir = SEGMENTS_DIR
        schemas_dir = SCHEMAS_DIR
    else:
        durable_root = Path(durable_root_str).resolve()
        segments_dir = durable_root / "segments"
        schemas_dir = durable_root / "schemas"

    if plugin_root_str is None:
        scripts_dir = SCRIPTS_DIR
    else:
        scripts_dir = Path(plugin_root_str).resolve() / "assets" / "scripts"

    return {
        "durable_root": durable_root,
        "scripts_dir": scripts_dir,
        "segments_dir": segments_dir,
        "schemas_dir": schemas_dir,
    }


# Canonical segment-id safety contract -- duplicated byte-for-byte per this
# project's "no shared lib between self-contained scripts" convention (see
# review_ready.py's/ledger_update.py's own identical copies).
# NOTE: re.fullmatch (NOT re.match + "$") -- in Python "$" also matches just
# before a trailing newline, so re.match(r"...$", "seg01\n") would WRONGLY pass.
_SEG_ID_RE = re.compile(r"(?:FRONTBACK:)?[A-Za-z0-9_]+")

# The rejection record's key set, PINNED by the #461 contract: the consumer
# (segment_dispatch_driver.py's _rejection_matches()) refuses any record
# whose keys are not EXACTLY these -- MISSING ones because a two-field
# hand-written file must not authorize anything, EXTRA ones because an
# unrecognised field means the record came from a writer whose rules we do
# not know. Kept as one constant so the payload main() builds and the
# conforming-record check read_existing_rejection() applies cannot drift
# apart: a field added to one and forgotten in the other would make this
# script's own idempotent re-run report success over a record its own
# consumer rejects.
REJECTION_RECORD_KEYS = frozenset({
    "seg",
    "dispatch_token",
    "verdict_digest",
    "round_label",
    "reason",
    "rejected_at",
    "operator_invocation",
})

# 64 lowercase hex -- exactly what hashlib.sha256().hexdigest() returns, so
# exactly what _review_verdict_digest() produces on both sides. Checked
# BEFORE the comparison so a truncated or uppercased paste refuses with
# "that is not a digest" instead of with a mismatch dump the operator has to
# eyeball character by character.
_SHA256_HEX_RE = re.compile(r"[0-9a-f]{64}")

# The only round labels segment_dispatch_driver.py ever mints: "1".."N" and
# the terminal "final" (see _next_round_label(), whose int() arithmetic is
# what fixes the numeric form). `[0-9]+` and NOT str.isdigit(): isdigit() is
# True for Arabic-Indic and superscript digits, which int() then rejects or
# silently reads as something else -- not a hypothetical in a pipeline whose
# sources are Hebrew and Arabic.
_ROUND_LABEL_RE = re.compile(r"final|[0-9]+")

_FILE_PRESENT = "present"
_FILE_ABSENT = "absent"
_FILE_AMBIGUOUS = "ambiguous"


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


def review_path(seg, segments_dir=SEGMENTS_DIR):
    return segments_dir / f"{seg}.review.json"


def rejection_path(seg, segments_dir=SEGMENTS_DIR):
    return segments_dir / f"{seg}.review_rejected.json"


def classify_file(path: Path, *, follow_symlinks: bool):
    """Classify what occupies `path`: `(_FILE_PRESENT|_FILE_ABSENT|
    _FILE_AMBIGUOUS, detail)`, where `detail` is empty for the two decided
    verdicts and operator-actionable for the ambiguous one. Shaped after
    claim_record.py's own classify_claim_record(); LOCAL rather than a
    sibling import because this script must answer the question for
    review.json BEFORE it imports any sibling at all, and because it needs
    both symlink policies rather than that function's one.

    NEVER Path.exists()/is_file()/is_dir(): those SWALLOW OSError and answer
    as if the thing were absent, so "the review is not there" and "the
    review is there and I was not allowed to look" come back
    indistinguishable -- and the swallowing is not even stable across
    interpreters, since from Python 3.13 Path.exists() swallows EVERY
    OSError while this plugin's floor is 3.10. Absence is established ONLY
    by the two errors that mean it (ENOENT via FileNotFoundError, ENOTDIR
    via NotADirectoryError, i.e. a non-directory component in the path);
    every other OSError is "could not look", which each caller must map to
    its own safe direction and say so at the call site. For both callers
    here that direction is refusal.

    `follow_symlinks=True` for a file this script is about to READ
    (review.json, review.schema.json): it reproduces the is_file() semantics
    the rest of the pipeline already applies to those, minus the swallowing.
    `False` -- lstat -- for the rejection record itself, where the FINAL
    component must stay unresolved: the consumer refuses a symlinked record
    outright, so a symlink at that path must never be read here as "the
    record I would have written"."""
    try:
        st = os.stat(path) if follow_symlinks else os.lstat(path)
    except (FileNotFoundError, NotADirectoryError):
        return (_FILE_ABSENT, "")
    except OSError as exc:
        return (_FILE_AMBIGUOUS, f"{path} could not be examined: {exc.strerror or exc}")
    if stat.S_ISREG(st.st_mode):
        return (_FILE_PRESENT, "")
    return (_FILE_AMBIGUOUS, f"{path} exists but is not a regular file")


def round_label_from_token(token, seg) -> "tuple[str | None, str | None]":
    """`(label, None)` or `(None, error)`: the round label carried by a
    review's own `dispatch_token`, whose shape review_dispatch_token()
    (segment_dispatch_driver.py) fixes as `{run_id}:{seg}:r{round_label}`.

    Matched from the RIGHT, on the literal `:{seg}:r` marker, because BOTH
    halves around it can contain a colon -- `FRONTBACK:errata_02` is a
    shipped segment id (see claim_record.py's own note on colons in `seg`),
    so splitting on ':' from the left would hand back a fragment of the id
    and call it a run. The marker cannot occur inside the label itself,
    labels being digits or 'final', so the LAST occurrence is the real one.

    A label this function cannot recognise is a refusal, not a shrug: a
    token whose label the driver's own _matched_review_round_label() could
    never match to any round is one no rejection could ever be consumed
    against, so accepting it would write an authorization that is dead on
    arrival -- an artifact that looks like it did something and did
    nothing. What is deliberately NOT checked here is the RANGE (is this
    label within max_fix_rounds) or the run: neither is knowable from a
    leaf script, and --expect-token already binds the whole token,
    including its run_id half, to the review on disk."""
    if not isinstance(token, str) or not token:
        return None, "the stored review carries no dispatch_token"
    marker = f":{seg}:r"
    idx = token.rfind(marker)
    if idx <= 0:
        return None, (
            f"the stored review's dispatch_token {token!r} is not of the form "
            f"'<run_id>:{seg}:r<round_label>' that reviewDispatchPrompt mints for "
            f"segment {seg!r}"
        )
    label = token[idx + len(marker):]
    if not _ROUND_LABEL_RE.fullmatch(label):
        return None, (
            f"the stored review's dispatch_token {token!r} carries round label "
            f"{label!r}, which is neither a decimal round number nor 'final'"
        )
    return label, None


def now_iso8601():
    """Second-resolution UTC, 'Z' suffix -- byte-for-byte
    ledger_update.py's own now_iso8601(), duplicated per this project's
    "no shared lib" convention for scripts that are not claim_record.py."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _review_verdict_digest(review_obj: dict) -> str:
    """sha256 over the WHOLE review object, sorted-key canonical form --
    MUST stay byte-for-byte identical to segment_dispatch_driver.py's own
    _review_verdict_digest(), which is what compares this script's output
    against a freshly re-read review.json at consume time. Duplicated
    rather than imported (segment_dispatch_driver.py is the driver that
    calls INTO its leaf siblings, never the reverse -- importing it here
    would be backwards and would drag in a 5000-line module for one hash
    function). See that function's own docstring for why the WHOLE object
    is hashed rather than an enumerated field list: the same reasoning
    applies verbatim here, since this script and the driver must agree on
    what "the same verdict" means without either one special-casing a
    field the other forgot."""
    return hashlib.sha256(
        json.dumps(review_obj, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def _load_review_schema(schemas_dir=SCHEMAS_DIR):
    """Returns (schema_dict, None) or (None, error_message) -- never
    raises, and that promise is TOTAL, which is the half review_ready.py's
    own _load_review_schema() (this function's origin) does not yet keep.
    Two edges there escape as a bare traceback rather than as a refusal a
    caller can branch on, and both are closed here: `Path.is_file()`
    becomes classify_file() (see there for why the swallowing matters), and
    the read catches the three unrelated ways read_text() fails -- OSError
    for IO, UnicodeDecodeError (a ValueError, which `except OSError` does
    NOT catch) for a non-UTF-8 byte, JSONDecodeError for the parse."""
    path = schemas_dir / "review.schema.json"
    state, detail = classify_file(path, follow_symlinks=True)
    if state == _FILE_ABSENT:
        return None, f"review.schema.json not found at {path}"
    if state == _FILE_AMBIGUOUS:
        return None, f"review.schema.json could not be read: {detail}"
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except UnicodeDecodeError as exc:
        return None, f"review.schema.json at {path} is not valid UTF-8: {exc}"
    except OSError as exc:
        return None, f"review.schema.json is unreadable: {exc}"
    except json.JSONDecodeError as exc:
        return None, f"review.schema.json at {path} is not valid JSON: {exc}"


def _foreign_record_error(path: Path, what: str) -> str:
    """The one refusal wording for "something occupies the rejection
    record's path and it is not a record this script may compare against or
    silently destroy". Shared by every such branch in
    read_existing_rejection() so the operator gets the identical
    instruction whichever way the file is unusable."""
    return (
        f"the file at {path} is not a rejection record this script can compare "
        f"against ({what}). It authorizes nothing in that state -- the consumer "
        f"refuses it too -- but replacing it silently would destroy whatever it "
        f"is: inspect it, delete it deliberately if it is not wanted, then re-run."
    )


def read_existing_rejection(path: Path) -> "tuple[dict | None, str | None]":
    """`(record, None)` for a conforming rejection record already on disk,
    `(None, None)` for "nothing is there", or `(None, error)` when the path
    is occupied by something this script must not silently destroy.

    Called BEFORE the write, so the conflict gate (module docstring, gate
    6) can compare reasons -- and every indeterminate answer refuses,
    because a conflict gate that cannot see the file it guards establishes
    nothing, and "I could not look" reported as "there was nothing there"
    is the precise shape that lets one operator's record be overwritten by
    another's.

    A non-regular entry refuses for the same reason the consumer refuses
    one -- a rejection is a local fact and a symlink is not one -- rather
    than letting os.replace() quietly consume it. A record whose key set is
    not EXACTLY REJECTION_RECORD_KEYS is treated as FOREIGN, not as a stale
    record to overwrite: the consumer refuses such a file outright, so
    reporting an idempotent success against one would tell the operator an
    authorization exists where none does."""
    state, detail = classify_file(path, follow_symlinks=False)
    if state == _FILE_ABSENT:
        return None, None
    if state == _FILE_AMBIGUOUS:
        return None, _foreign_record_error(path, detail)
    try:
        raw = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        return None, _foreign_record_error(path, f"it is not valid UTF-8: {exc}")
    except OSError as exc:
        return None, _foreign_record_error(path, f"it is unreadable: {exc}")
    try:
        record = json.loads(raw)
    except json.JSONDecodeError as exc:
        return None, _foreign_record_error(path, f"it is not valid JSON: {exc}")
    if not isinstance(record, dict):
        return None, _foreign_record_error(
            path, f"it is a JSON {type(record).__name__}, not a JSON object"
        )
    if set(record) != REJECTION_RECORD_KEYS:
        missing = sorted(REJECTION_RECORD_KEYS - set(record))
        extra = sorted(set(record) - REJECTION_RECORD_KEYS)
        return None, _foreign_record_error(
            path,
            f"its key set is not the pinned seven -- missing {missing}, "
            f"unexpected {extra}",
        )
    return record, None


def _import_claim_record(scripts_dir: Path):
    """Sibling import of claim_record.py, for its fsync_directory() only --
    loaded BY PATH from `scripts_dir`, unconditionally.

    THE SAME SHAPE segment_dispatch_driver.py's _load_claim_record_module()
    already uses for this exact sibling, and for the reason its own section
    comment states: a bare `import claim_record` resolves against sys.path[0]
    -- THIS PROCESS's own physical directory -- even when `scripts_dir` names
    a different, trusted --plugin-root tree.

    DELIBERATELY NOT select_segments.py's shape, which does try the bare
    import first. That is sound there and only there: its own docstring says
    its by-path fallback "resolves the SAME location", it takes no
    scripts_dir at all, and it never promised to redirect anywhere -- so
    consulting sys.path first costs it nothing.

    Here it would cost everything. resolve_dirs() states the contract this
    script is built on: `scripts_dir` comes from --plugin-root and is
    NEVER derived from --durable-root, because `${durable_root}/scripts/` is
    a Step-0a copy that other passes in this pipeline hold write access over
    (the glossary and skeptic codex passes, and the manual W5 drive -- see
    codex_job.py's own note on #412). In production THIS script runs from
    that same durable copy, so sys.path[0] IS the tamperable directory: a
    bare import would load a poisoned sibling and execute it, with
    --plugin-root inert in exactly the deployment it exists for, and a
    no-op fsync_directory() would then publish a record while reporting it
    durable. Making `scripts_dir` the only thing that decides which file is
    executed is what makes the flag real.

    A missing or unloadable sibling is a whole-run FATAL (mirrors the same
    call in select_segments.py): there is no safe direction to guess a
    directory-durability primitive is available when it is not."""
    import importlib.util

    path = scripts_dir / "claim_record.py"
    spec = importlib.util.spec_from_file_location("claim_record", str(path))
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except (OSError, ImportError, SyntaxError, ValueError):
        return None
    return module


_O_CLOEXEC = getattr(os, "O_CLOEXEC", 0)

REJECTION_LOCK_TIMEOUT_S = 10.0

# Open lock descriptors, held for the life of the process. See main()'s own
# comment: an flock lives on its fd, so this list is what keeps it alive.
_HELD_LOCK_FDS: "list[int]" = []


def rejection_lock_path(seg: str, segments_dir: Path) -> Path:
    """`.reject_review.{seg}.lock`, beside the record it serialises -- the same
    naming and placement codex_job.py uses for its own per-segment lease
    (`.codex_job.{seg}.lock`)."""
    return segments_dir / f".reject_review.{seg}.lock"


def acquire_rejection_lock(seg: str, segments_dir: Path,
                           timeout_s: float = REJECTION_LOCK_TIMEOUT_S):
    """`(fd, None)` holding an exclusive kernel flock, or `(None, problem)`.

    WHY A LOCK AT ALL, when this is a command a human types. Gate 6 READS the
    record on disk, decides from it, and only later publishes with an
    unconditional os.replace(). Nothing spans those two steps, so two
    operators rejecting the same verdict with DIFFERENT reasons can both
    observe an absent-or-stale record, both pass the conflict gate, and the
    later replace silently erases the first one's record -- exactly what the
    conflict gate's own refusal message promises cannot happen ("nothing here
    can tell a deliberate correction from a second operator replacing a
    colleague's record"). A documented guarantee that a race can break is
    worse than no guarantee. The cleanup paths carry the same hazard in
    reverse: an unconditional unlink can remove a record another process
    published in between.

    So the WHOLE sequence -- read, conflict check, write, directory sync,
    post-write freshness check, and any cleanup -- runs inside one critical
    section, and this is what opens it.

    A KERNEL FLOCK, not a lockfile whose presence means "locked", for the
    reason codex_job.py's own lease states: the kernel releases it when the
    holder dies, so a crashed operator cannot wedge the segment and there is
    no stale-break race to get wrong. The lock FILE is left behind on purpose;
    it carries no state and creating it is not acquiring anything.

    LOCK_NB in a bounded retry loop rather than a blocking LOCK_EX: a human
    waiting on a terminal deserves a refusal that names the problem, not an
    indefinite hang with no output."""
    lock_path = rejection_lock_path(seg, segments_dir)
    try:
        segments_dir.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR | _O_CLOEXEC, 0o600)
    except OSError as exc:
        return None, (
            f"could not open the rejection lock at {lock_path}: {exc}. "
            f"Refusing rather than publish a record without serialising "
            f"against a concurrent operator."
        )
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd, None
        except OSError:
            if time.monotonic() >= deadline:
                os.close(fd)
                return None, (
                    f"another rejection for segment {seg!r} is in progress and "
                    f"still holds {lock_path} after {timeout_s:g}s. Nothing was "
                    f"read, written or removed. Wait for it to finish and "
                    f"re-run this command -- if no other operator is running "
                    f"one, a process holding that lock is stuck and must be "
                    f"ended before this can proceed."
                )
            time.sleep(0.1)


def _rejection_outlives_review(rej_path: Path, rev_path: Path) -> bool:
    """Does the record at `rej_path` still satisfy the consumer's rule 8 --
    strictly newer than the review it names?

    A DELIBERATE SECOND COPY of segment_dispatch_driver.py's own comparison,
    for the same reason REJECTION_RECORD_KEYS and _review_verdict_digest()
    are duplicated: these are two standalone scripts with no shared import,
    and a test pins the two sides equal. Read the SAME direction (`>`, so a
    tie is NOT fresh) -- a producer that were laxer here would report
    "already recorded" over a record the consumer has already spent, which
    is precisely the dead end this predicate exists to detect.

    Only ever used to decide between an idempotent no-op and a renewal, and
    a renewal is always the safe answer, so anything that cannot be
    established -- either file missing, unreadable, replaced mid-check --
    answers False and renews. Nothing is AUTHORIZED by this function; the
    consumer re-derives rule 8 for itself at consume time from its own
    descriptors."""
    try:
        rej_ns = os.stat(rej_path).st_mtime_ns
        rev_ns = os.stat(rev_path).st_mtime_ns
    except OSError:
        return False
    return rej_ns > rev_ns


def load_rejectable_review(seg: str, dirs: dict) -> "tuple[dict | None, str | None]":
    """`(review_obj, None)` for a stored review this script could act on, or
    `(None, error)`. Everything that is a fact about the REVIEW ON DISK --
    it is there, it is readable, it parses to an object, it is schema-valid,
    and its `clean` is the literal False -- and nothing that is a fact about
    the caller's INTENT (the --expect-* bindings, --reason, --round-label,
    the conflict gate, the write).

    ONE function because both modes must agree byte-for-byte on what "the
    review" is: --print-verdict-digest hands the operator a digest, and the
    rejection path then demands that same digest back. If the two computed
    it over even slightly differently-obtained bytes -- a second read, a
    different parse path -- the tool would print a value it then refuses,
    which is the unusable-remedy shape the flag exists to avoid. Sharing
    the loader makes that impossible by construction rather than by
    matching two copies.

    `clean is False` is part of THIS function, not of the rejection gates
    above it, on purpose: a clean:true review cannot be rejected, so
    printing a digest for one would advertise a next step that refuses.
    The operator learns the real blocker from the read command instead."""
    rpath = review_path(seg, dirs["segments_dir"])
    review_state, review_detail = classify_file(rpath, follow_symlinks=True)
    if review_state == _FILE_ABSENT:
        return None, f"no stored review for segment {seg!r} at {rpath}"
    if review_state == _FILE_AMBIGUOUS:
        return None, f"stored review for segment {seg!r} cannot be used: {review_detail}"
    try:
        review_obj = json.loads(rpath.read_text(encoding="utf-8"))
    except UnicodeDecodeError as exc:
        # Listed separately because it is NOT an OSError -- UnicodeDecodeError
        # is a ValueError, so the clause below never caught it and a review
        # holding one bad byte escaped as a traceback instead of a refusal.
        # Same three-clause read claim_record.py's read_claim_record() spells
        # out, and the wording matters: "unreadable" would send an operator to
        # check permissions on a file whose permissions are fine.
        return None, f"stored review for segment {seg!r} at {rpath} is not valid UTF-8: {exc}"
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"stored review for segment {seg!r} is not readable/valid JSON: {exc}"
    if not isinstance(review_obj, dict):
        return None, (
            f"stored review for segment {seg!r} must be a JSON object, "
            f"got {type(review_obj).__name__}"
        )

    schema, err = _load_review_schema(dirs["schemas_dir"])
    if err is not None or schema is None:
        return None, f"internal error: {err}"
    try:
        validator_cls = jsonschema.validators.validator_for(schema)
        validator_cls.check_schema(schema)
        validator = validator_cls(schema)
        errors = sorted(validator.iter_errors(review_obj), key=str)
    except jsonschema.exceptions.SchemaError as exc:
        # check_schema() RAISES on a malformed schema -- it is the one call
        # here whose failure means "the shipped schema is broken", not "this
        # review is". Returning it as an error string rather than letting it
        # escape keeps the promise a caller branches on: `success` is false
        # for every reason this command can fail, not just the six gates.
        return None, f"internal error: review.schema.json is not a valid JSON Schema: {exc}"
    if errors:
        detail = "; ".join(e.message for e in errors)
        return None, (
            f"stored review for segment {seg!r} is not schema-valid against "
            f"review.schema.json ({detail})"
        )

    if review_obj.get("clean") is not False:
        return None, (
            f"stored review for segment {seg!r} has clean={review_obj.get('clean')!r} "
            f"-- only a clean:false review (unfounded findings) may be rejected"
        )
    return review_obj, None


def write_rejection_record(path: Path, payload: dict, claim_record_mod) -> "str | None":
    """Publish the rejection record: None on a fresh, durable write, or an
    error string otherwise. Temp file + fsync + os.replace() + directory
    fsync -- see this module's own docstring for why this is a FULL-REPLACE
    write rather than claim_record.py's own exclusive-create.

    ENCODE BEFORE CREATING THE TEMP FILE, the same ordering
    claim_record.py's write_claim_record() uses and explains at length:
    `ensure_ascii=False` lets json.dumps() return a str containing a lone
    surrogate when `reason` (arbitrary operator text) or
    `operator_invocation` (raw argv) happens to carry one, and encoding
    that to UTF-8 raises UnicodeEncodeError -- a ValueError, not an
    OSError. Catching it BEFORE any file exists means the failure leaves
    no partial artifact behind, the same "state stays exactly as if this
    call never happened" property write_claim_record() protects.

    A FAILED DURABILITY STEP LEAVES NO LIVE AUTHORIZATION -- see the
    fsync branch below, which is the one place this function deliberately
    departs from write_claim_record()'s own handling."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        # An error string, never a traceback: this is a refusal like any
        # other, and a caller branching on `success` must be able to see it
        # as one. A segments/ directory that cannot be created is also the
        # case where NOTHING has been written yet, so there is nothing to
        # clean up.
        return f"could not create {path.parent} for the rejection record: {exc}"
    body = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False) + "\n"
    try:
        blob = body.encode("utf-8")
    except UnicodeEncodeError as exc:
        return f"could not encode the rejection record as UTF-8, so nothing was written: {exc}"

    # O_CREAT|O_EXCL, and a RANDOM suffix rather than the pid. A plain
    # open() on a predictable name follows a symlink planted at that name,
    # so anything that can write in segments/ -- the very population this
    # record is defended against -- could redirect these bytes onto a file
    # OUTSIDE the durable root (a hand-corrected draft in another book),
    # after which os.replace() renames the symlink onto the record path and
    # the consumer's O_NOFOLLOW refuses it: the operator is told the
    # rejection succeeded, nothing is authorized, and an unrelated file is
    # destroyed. O_EXCL is what closes that (it refuses ANY pre-existing
    # entry, symlink included -- the same argument claim_record.py makes for
    # its own record); the random suffix removes the predictability AND the
    # stale-leftover collision a recycled pid would cause. 0o644 keeps the
    # mode the previous plain open() produced under this pipeline's umask.
    tmp_path = path.parent / f".{path.name}.tmp.{os.getpid()}.{os.urandom(6).hex()}"
    try:
        fd = os.open(tmp_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except OSError as exc:
        # NOTHING IS UNLINKED ON THIS PATH, and the split from the try below
        # exists to make that true. This open is what would have CREATED
        # tmp_path, so if it raised, whatever occupies that path is not ours
        # -- and O_EXCL's whole job is to refuse exactly the entry an attacker
        # planted there. Unlinking it would erase the only visible trace of
        # the attack and hand the next attempt a clean path to be attacked
        # again. The cleanup below is for a file this process really did
        # create and then failed to fill.
        return (
            f"could not create the rejection record's temporary file at "
            f"{tmp_path} (nothing was written, and anything already at that "
            f"path was left exactly as it was): {exc}"
        )
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(blob)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    except OSError as exc:
        try:
            tmp_path.unlink()
        except OSError:
            pass
        return f"could not write the rejection record: {exc}"

    sync_problem = claim_record_mod.fsync_directory(path.parent)
    if sync_problem is not None:
        # UNLINKED, NOT KEPT -- deliberately the opposite of
        # write_claim_record()'s own choice for its own record, because the
        # two artifacts fail in opposite directions. The reasoning that
        # once stood here said keeping it was the fail-CLOSED side: a
        # reader that finds no rejection falls back to needs_fix. That is
        # true of the READER and irrelevant to the RECORD. This artifact is
        # an AUTHORIZATION, and the asymmetry runs the other way. A missing
        # rejection leaves a live-lock the operator recovers from by
        # re-running this one command. A record the operator was TOLD had
        # failed, left live on disk, authorizes an unchanged draft to
        # advance on the driver's next pass with nobody watching -- the
        # "record outlives the fact it attests" shape, whose damage (a
        # hand-edited translation overwritten) has no recovery at all.
        # Between a recoverable refusal and an unrecoverable grant, the
        # refusal is what fail-closed means here.
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
        except OSError as exc:
            return (
                f"the rejection record could NOT be made durable ({sync_problem}) "
                f"and could NOT be removed afterwards ({exc}) -- a live "
                f"authorization this command did not grant is on disk at {path}. "
                f"DELETE IT BY HAND before running the driver again."
            )
        # THE REMOVAL NEEDS ITS OWN DIRECTORY SYNC, for the same reason the
        # creation did -- claim_record.py's own fsync_directory() says it: an
        # unlink changes a DIRECTORY ENTRY, and an unsynced one can be lost.
        # The record's creation was already made durable before this branch
        # was reached, so a crash between the unlink and its sync restores a
        # record this command reported as removed -- exactly the authorization
        # nobody granted that the unlink exists to prevent.
        removal_sync = claim_record_mod.fsync_directory(path.parent)
        if removal_sync is not None:
            return (
                f"the rejection record could not be made durable "
                f"({sync_problem}) and its REMOVAL could not be made durable "
                f"either ({removal_sync}). The file is gone from this running "
                f"system, but that deletion may not survive a crash, and if it "
                f"does not, a live authorization this command did not grant "
                f"comes back at {path}. VERIFY THAT PATH IS ABSENT before "
                f"running the driver again."
            )
        return (
            f"the rejection record could not be made durable -- {sync_problem} -- "
            f"so it was removed again, and the removal was itself synced: "
            f"nothing authorizes this rejection and nothing was left behind "
            f"that could. Fix the directory (permissions, filesystem) and "
            f"re-run this command."
        )
    return None


def _refuse(error: str) -> NoReturn:
    print(json.dumps({"success": False, "error": error}))
    sys.exit(1)


def _accept(payload: dict) -> NoReturn:
    print(json.dumps(payload))
    sys.exit(0)


def _print_digest_command(seg: str, args) -> str:
    """The EXACT --print-verdict-digest invocation for this segment, in the
    operator's own setup: same interpreter spelling and same script path
    they just used (sys.argv[0]), and --durable-root carried over when they
    passed one, since without it the read would self-anchor somewhere else
    and answer about a different tree.

    Quoted with shlex so a root containing a space stays one argument. `seg`
    is already validated to (FRONTBACK:)?[A-Za-z0-9_]+ before any caller
    reaches here, so it needs no quoting -- it gets it anyway, because the
    day that pattern widens this line must not become the exception nobody
    remembers to check.

    A refusal that names a remedy the operator cannot reach is worse than
    one that names none: it reads as an instruction and terminates in a
    dead end. Every refusal that demands a digest calls this."""
    parts = [
        shlex.quote(sys.executable if sys.executable else "python3"),
        shlex.quote(sys.argv[0]),
        shlex.quote(seg),
        "--print-verdict-digest",
    ]
    if args.durable_root:
        parts += ["--durable-root", shlex.quote(str(args.durable_root))]
    return " ".join(parts)


def _print_verdict_digest(seg: str, dirs: dict) -> NoReturn:
    """--print-verdict-digest: a PURE READ that hands the operator both
    values the rejection path will demand, and exits.

    It exists because --expect-verdict-digest is required and
    _review_verdict_digest() is a private function over the whole parsed
    review -- there is otherwise no way for a person to obtain the value the
    tool insists on, and a required flag whose value cannot be obtained is
    an unusable tool, not a strict one.

    IT DOES NOT WEAKEN THE BINDING. The operator still reads the verdict and
    passes the digest back by hand; what is removed is the impossibility of
    doing so, not the attestation. Nothing here auto-fills the flag into a
    rejection -- that would restore the exact hole gate 4 closes, since the
    value would then describe whatever is on disk at write time rather than
    what a human inspected.

    THE TOKEN COMES BACK IN THE SAME BREATH, and that is the point of
    printing both rather than leaving --expect-token to a separate lookup:
    two independent reads can straddle a re-dispatch and produce a token
    from before it with a digest from after, a pair that never described one
    verdict. One read, one pair.

    WRITES NOTHING -- no record, no parent directory, no claim_record
    import. A read-only mode that can fail with a filesystem side effect is
    not a read-only mode, and this one is reached by operators who are
    already unsure what is on disk.

    ITS SUCCESS ENVELOPE IS ITS OWN, and shares only `success` with the
    rejection path's. Keys: `seg`, `review_path`, `dispatch_token`,
    `verdict_digest`, `round_label`, `round_label_problem`. It carries no
    `path`, no `already_recorded` and no `renewed` -- this command decides
    nothing and writes nothing, so there is no record whose state those
    would describe. A caller must branch on the MODE it invoked before
    reading mode-specific keys; the rejection path's uniform `renewed` is a
    promise about that path's two shapes, not about this one."""
    review_obj, err = load_rejectable_review(seg, dirs)
    if err is not None:
        _refuse(err)
    token = review_obj.get("dispatch_token")
    label, label_err = round_label_from_token(token, seg)
    # BOTH keys are always present, `null` when unavailable, rather than one
    # of them appearing only sometimes: a caller that has to branch on which
    # keys exist is a caller that will get it wrong once. An unparseable
    # round label is reported and not fatal here -- the digest and token are
    # still exactly right, and the rejection path will refuse on the label
    # with its own message if the operator goes on to try.
    _accept({
        "success": True,
        "seg": seg,
        "review_path": str(review_path(seg, dirs["segments_dir"])),
        "dispatch_token": token,
        "verdict_digest": _review_verdict_digest(review_obj),
        "round_label": label,
        "round_label_problem": label_err,
    })


def build_arg_parser():
    parser = argparse.ArgumentParser(
        description=(
            "Record that a stored review verdict for segments/{seg}.review.json "
            "was judged unfounded -- see this file's own module docstring."
        ),
    )
    parser.add_argument("seg", help="Segment identifier.")
    parser.add_argument(
        "--print-verdict-digest",
        action="store_true",
        help=(
            "READ-ONLY MODE. Print the stored review's own dispatch_token and "
            "verdict_digest for SEG -- the two values --expect-token and "
            "--expect-verdict-digest require -- then exit without writing "
            "anything at all. Both come from ONE read, so they always "
            "describe the same verdict. Every other flag below is a "
            "rejection-path flag and is not consulted in this mode; only "
            "--durable-root still applies, since it decides which tree is "
            "read."
        ),
    )
    parser.add_argument(
        "--reason",
        default=None,
        metavar="TEXT",
        help=(
            "REQUIRED, non-empty after stripping whitespace. Free-text "
            "explanation of why the stored verdict does not apply -- the "
            "durable audit trail this artifact exists to carry. Application-"
            "checked (not argparse `required=True`) so a missing/blank "
            "reason refuses through the same {\"success\": false} JSON "
            "envelope every other refusal here uses, rather than a bare "
            "argparse usage crash."
        ),
    )
    parser.add_argument(
        "--round-label",
        default=None,
        metavar="LABEL",
        help=(
            "REQUIRED. The round label (a decimal number, or 'final') the "
            "stored review belongs to. Must AGREE with the label that "
            "review's own dispatch_token encodes -- a mismatch refuses "
            "rather than recording the derived label (see module docstring)."
        ),
    )
    parser.add_argument(
        "--expect-token",
        default=None,
        metavar="TOK",
        help=(
            "REQUIRED. The stored review's own dispatch_token, exactly as "
            "the caller read it. Must equal review.json's current "
            "dispatch_token EXACTLY -- this is 'the review being rejected "
            "is the one currently on disk', the same TOCTOU discipline "
            "review_ready.py's own required --expect-token already "
            "establishes."
        ),
    )
    parser.add_argument(
        "--expect-verdict-digest",
        default=None,
        metavar="HEX64",
        help=(
            "REQUIRED. sha256, 64 lowercase hex, of the WHOLE review object "
            "the operator actually inspected -- exactly the value "
            "segment_dispatch_driver.py's own _review_verdict_digest() "
            "recomputes at consume time. Must equal the digest of "
            "review.json as it stands NOW. The token cannot stand in for "
            "this: same-round retries reuse the token, so a verdict the "
            "operator never read can sit under the token they named (see "
            "module docstring, gate 4). REQUIRED makes this CLI a breaking "
            "change on purpose -- an un-updated caller must refuse, not "
            "authorize on the weaker binding."
        ),
    )
    parser.add_argument(
        "--durable-root",
        default=None,
        metavar="PATH",
        help=(
            "LT-409: use PATH as the DATA root instead of this script's own "
            "self-anchored location. Optional; omit for today's "
            "self-anchored behavior."
        ),
    )
    parser.add_argument(
        "--plugin-root",
        default=None,
        metavar="PATH",
        help=(
            "LT-409: use PATH (the plugin's own install root, i.e. "
            "{{PLUGIN_ROOT}}) to resolve the sibling claim_record.py this "
            "script imports, as {PATH}/assets/scripts/claim_record.py -- "
            "deliberately NEVER derived from --durable-root. Optional; "
            "omit for today's self-anchored sibling lookup."
        ),
    )
    return parser


def main():
    args = build_arg_parser().parse_args()

    seg = args.seg
    seg_err = validate_seg(seg)
    if seg_err:
        print(f"Error: {seg_err}", file=sys.stderr)
        sys.exit(2)

    if args.print_verdict_digest:
        # Branches BEFORE every rejection-path check, deliberately: the read
        # mode must not demand a --reason, a --round-label or an --expect-*
        # value the operator is running this command precisely to obtain.
        # --plugin-root is not passed on either -- resolve_dirs() only uses
        # it to locate claim_record.py, which a pure read never imports
        # (`scripts_dir` is consumed on the rejection path alone). Passing it
        # here would change nothing today and make the comment above, and the
        # read mode's own CLI synopsis, false the moment that stops holding.
        _print_verdict_digest(seg, resolve_dirs(args.durable_root))

    reason = (args.reason or "").strip()
    if not reason:
        _refuse("a --reason is required and must be non-empty (after stripping whitespace)")

    round_label = (args.round_label or "").strip()
    if not round_label:
        _refuse("a --round-label is required and must be non-empty")

    expect_token = args.expect_token
    if not expect_token:
        _refuse("a --expect-token is required")

    expect_digest = (args.expect_verdict_digest or "").strip()
    if not expect_digest:
        _refuse(
            f"a --expect-verdict-digest is required: the digest of the review "
            f"verdict you actually read, which is what binds this rejection to "
            f"THAT verdict rather than to whatever now sits under the same "
            f"dispatch_token. Read the verdict, then get its digest and matching "
            f"token from one read with: {_print_digest_command(seg, args)}"
        )
    if not _SHA256_HEX_RE.fullmatch(expect_digest):
        _refuse(
            f"--expect-verdict-digest must be 64 lowercase hex characters (a "
            f"sha256 hexdigest); got {expect_digest!r}"
        )

    dirs = resolve_dirs(args.durable_root, args.plugin_root)

    review_obj, load_err = load_rejectable_review(seg, dirs)
    if load_err is not None:
        _refuse(load_err)

    token = review_obj.get("dispatch_token")
    if token != expect_token:
        _refuse(
            f"stored review for segment {seg!r} has dispatch_token={token!r}, "
            f"expected {expect_token!r} -- refusing rather than reject a "
            f"DIFFERENT review than the one named"
        )

    digest = _review_verdict_digest(review_obj)
    if digest != expect_digest:
        # Deliberately NOT "re-run with the digest printed above": that would
        # let the operator authorize, by copy-paste, a verdict they have not
        # read -- which is the whole hole this gate closes. The remedy named
        # here is to go and read the review that is actually on disk.
        _refuse(
            f"stored review for segment {seg!r} digests to {digest}, but "
            f"--expect-verdict-digest names {expect_digest}. The dispatch_token "
            f"matches, so this is the same run/segment/round -- the VERDICT under "
            f"it changed since you read it (a re-dispatched review re-uses the "
            f"token). Read the review now on disk at "
            f"{review_path(seg, dirs['segments_dir'])}, then take its current "
            f"token+digest pair from: {_print_digest_command(seg, args)}"
        )

    token_label, label_err = round_label_from_token(token, seg)
    if label_err is not None:
        _refuse(
            f"cannot establish which round the stored review for segment {seg!r} "
            f"belongs to: {label_err}"
        )
    if round_label != token_label:
        _refuse(
            f"--round-label {round_label!r} disagrees with the stored review's "
            f"own dispatch_token, which names round {token_label!r}. Refusing "
            f"rather than record a round this review never belonged to -- if "
            f"{token_label!r} is the round you meant, pass it; if it is not, you "
            f"are looking at a different review than the one on disk"
        )

    rej_path = rejection_path(seg, dirs["segments_dir"])
    renewed = False

    # THE CRITICAL SECTION OPENS HERE and closes when this process exits --
    # every _refuse()/_accept() below is a sys.exit(), and the kernel drops the
    # flock with the fd. It deliberately spans gate 6's READ, the write, the
    # directory sync, the post-write freshness check and every cleanup, because
    # the hazard is precisely the gap between reading the record and replacing
    # it. See acquire_rejection_lock() for what that gap allows.
    #
    # Taken AFTER every gate that needs nothing but the review on disk: a
    # malformed invocation must refuse without ever contending for a lock, and
    # --print-verdict-digest (which exits far above this) must stay a pure read
    # that creates nothing at all.
    lock_fd, lock_problem = acquire_rejection_lock(seg, dirs["segments_dir"])
    if lock_problem is not None:
        _refuse(lock_problem)
    # THE FD IS THE LOCK. Parked in module state rather than left as a local
    # nobody reads, so that "this value is unused" can never become a reason to
    # drop it: closing it -- or letting anything close it -- releases the flock
    # and silently reopens the race this section exists to close.
    _HELD_LOCK_FDS.append(lock_fd)

    # Gate 6, BEFORE the sibling import and before any write: an idempotent
    # re-run is a pure READ and must not depend on the write path's helper
    # being importable, and a conflicting record must refuse before anything
    # on disk is touched.
    existing, existing_err = read_existing_rejection(rej_path)
    if existing_err is not None:
        _refuse(existing_err)
    if (
        existing is not None
        and existing.get("dispatch_token") == token
        and existing.get("verdict_digest") == digest
    ):
        if existing.get("reason") == reason:
            # Byte-identical reason for the identical verdict: the fact this
            # command exists to record is already recorded. Returning success
            # WITHOUT rewriting is the point -- a rewrite would move
            # rejected_at and operator_invocation onto this invocation and
            # erase who first made the call and when, which is the only thing
            # a later reviewer has to go on.
            #
            # UNLESS THE RECORD IS ALREADY SPENT, which is not a hypothetical:
            # at the absorbing `final` label a replacement review carries a
            # byte-identical token AND digest (review_dispatch_token() is a
            # pure function of run/seg/label), so the consumer's rule 8 --
            # record strictly newer than review.json -- is the ONLY thing
            # separating the rejected verdict from its replacement, and it has
            # already taken this record's authorization away. Without the
            # branch below, the operator who wants to reject the replacement
            # too is dead-ended: the identical reason returns success and
            # rewrites nothing, a different reason refuses as a conflict, and
            # the only way forward is deleting the file by hand. That is the
            # #465 defect class -- a remedy the tool documents but cannot
            # reach -- reappearing inside the release that names it.
            #
            # THE COST IS REAL AND IS PAID DELIBERATELY: renewing rewrites
            # rejected_at and operator_invocation, so the FIRST decision's
            # timestamp and command line are lost. `reason` -- the substantive
            # audit content, the thing a later reviewer actually needs -- is
            # what must match for this branch to be taken at all, so it
            # survives by construction. A stat that cannot be established
            # renews too: renewing is never unsafe (it only ever makes the
            # record current for a verdict the operator has already judged),
            # while a wrong "already recorded" is the dead end above.
            if _rejection_outlives_review(
                rej_path, review_path(seg, dirs["segments_dir"])
            ):
                _accept({
                    "success": True,
                    "path": str(rej_path),
                    "already_recorded": True,
                    # Present-and-False, never absent: `renewed` is on every
                    # success payload OF THE REJECTION PATH -- the two shapes
                    # this branch and the write below can return -- so a
                    # caller can branch on it without first testing whether
                    # the key exists. This path is the one where it would
                    # otherwise be missing, and a KeyError here would land on
                    # the "nothing needed doing" case, the one a caller is
                    # least likely to have exercised. It is NOT on
                    # --print-verdict-digest's envelope, which shares no key
                    # with these two beyond `success` and is a different
                    # command with a different answer; see that function.
                    "renewed": False,
                    **existing,
                })
            renewed = True
        else:
            _refuse(
                f"a rejection for this exact verdict is already recorded at "
                f"{rej_path} with a DIFFERENT reason: "
                f"{existing.get('reason')!r} on disk versus {reason!r} now. "
                f"Refusing rather than overwrite the stated reason -- it is "
                f"the whole audit trail, and nothing here can tell a "
                f"deliberate correction from a second operator replacing a "
                f"colleague's record. Re-run with the recorded reason verbatim "
                f"(a no-op, or a renewal if that record has already been "
                f"spent), or delete that file deliberately if the new wording "
                f"is meant to replace it"
            )

    claim_record_mod = _import_claim_record(dirs["scripts_dir"])
    if claim_record_mod is None:
        _refuse(
            f"claim_record.py could not be imported (expected beside this "
            f"script at {dirs['scripts_dir'] / 'claim_record.py'}) -- needed "
            f"for its fsync_directory() helper"
        )

    payload = {
        "seg": seg,
        "dispatch_token": token,
        "verdict_digest": digest,
        "round_label": round_label,
        "reason": reason,
        "rejected_at": now_iso8601(),
        "operator_invocation": " ".join(sys.argv),
    }
    if set(payload) != REJECTION_RECORD_KEYS:
        # The record's key set is pinned by the #461 contract and the consumer
        # refuses anything else, so a field added or dropped here must fail
        # LOUDLY at the writer rather than produce a file that validates
        # nowhere. Cheap, and it is the only place the two can be compared.
        _refuse(
            f"internal error: the rejection record would have keys "
            f"{sorted(payload)}, not the pinned {sorted(REJECTION_RECORD_KEYS)}"
        )

    write_err = write_rejection_record(rej_path, payload, claim_record_mod)
    if write_err is not None:
        _refuse(write_err)

    # THE RECORD IS ON DISK; THIS ASKS WHETHER IT CAN ACTUALLY AUTHORIZE.
    # The renewal decision above was made from the mtimes BEFORE this write,
    # and the record it produced is stamped with the clock as it is NOW --
    # which is not necessarily ahead of review.json. If the clock has moved
    # backwards since that review was written (or the review carries a future
    # mtime for any other reason), the fresh record is still older than the
    # review it names, so the consumer's rule 8 refuses it and the segment
    # stays exactly as stuck as it was. Reporting success there would be the
    # worst of the available outcomes: the operator is told the remedy worked,
    # the driver silently disagrees, and every repeat of the command reports
    # success again. So this refuses instead, and says which way the two
    # stamps run.
    #
    # AND THE RECORD IS REMOVED AGAIN, exactly as write_rejection_record()
    # does when the directory fsync fails, for the reason stated there in
    # full: this artifact is an AUTHORIZATION, so a record the operator was
    # TOLD had failed must not stay on disk.
    #
    # An earlier version of this branch kept it, reasoning that a stale record
    # is "harmless because rule 8 ignores it". That reasoning is wrong, and
    # the way it is wrong is the "record outlives the fact it attests" shape
    # this file already refuses elsewhere: rule 8 compares the record against
    # whatever review.json is on disk AT CONSUME TIME, not against the one
    # this command read. Restore or re-write a byte-identical review with an
    # older mtime -- same token, same digest, so gates 3 and 4 would still
    # have passed -- and the retained record starts authorizing, with nobody
    # having re-run anything and the operator holding an exit-1 saying it did
    # not take effect. Removing it makes the message true.
    rev_path = review_path(seg, dirs["segments_dir"])
    if not _rejection_outlives_review(rej_path, rev_path):
        try:
            rej_ns = os.stat(rej_path).st_mtime_ns
            rev_ns = os.stat(rev_path).st_mtime_ns
            stamps = (
                f" (record {rej_ns}, review {rev_ns}, both mtime nanoseconds)"
            )
        except OSError:
            stamps = ""
        try:
            os.unlink(rej_path)
        except FileNotFoundError:
            pass
        except OSError as exc:
            _refuse(
                f"the rejection record written to {rej_path} cannot authorize "
                f"anything{stamps} -- this host's clock is behind the "
                f"timestamp on {rev_path} -- and it could NOT be removed "
                f"afterwards ({exc}), so a record this command did not grant "
                f"is on disk and will start authorizing if that review is "
                f"ever replaced with an older stamp. DELETE IT BY HAND before "
                f"running the driver again."
            )
        # The removal is synced for the same reason write_rejection_record()
        # syncs its own: the record's CREATION was already made durable a few
        # lines ago, so an unsynced unlink can be undone by a crash and bring
        # back a record this command is about to report as removed.
        removal_sync = claim_record_mod.fsync_directory(rej_path.parent)
        if removal_sync is not None:
            _refuse(
                f"the rejection record written to {rej_path} cannot authorize "
                f"anything{stamps} -- this host's clock is behind the "
                f"timestamp on {rev_path} -- and although it was removed, that "
                f"removal could NOT be made durable ({removal_sync}). The file "
                f"is gone from this running system, but if that deletion does "
                f"not survive a crash, a record this command did not grant "
                f"comes back and starts authorizing the moment that review is "
                f"replaced with an older stamp. VERIFY THAT PATH IS ABSENT "
                f"before running the driver again."
            )
        _refuse(
            f"the rejection record could not be made to authorize anything, "
            f"so it was removed again and the removal was itself synced: the "
            f"driver only consults a record strictly newer than the review it "
            f"names, and this one was not{stamps}, which means this host's "
            f"clock is behind the timestamp on {rev_path}. Nothing was left "
            f"behind that could later authorize on its own, and nothing about "
            f"the draft or the review was changed. Re-run this exact command "
            f"once the clock is past that timestamp"
        )

    _accept({
        "success": True,
        "path": str(rej_path),
        # True only on the renewal branch of gate 6: an identical-reason
        # re-run over a record the consumer had already spent. Reported so a
        # caller can tell "nothing needed doing" (already_recorded) from "the
        # previous record was spent and has been replaced" -- the two look
        # alike from outside and mean opposite things about what happens next.
        "renewed": renewed,
        **payload,
    })


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001 -- deliberate CLI backstop
        # LAST-RESORT ENVELOPE, not a substitute for the specific handling
        # above: every failure this script can reason about is caught where it
        # happens, with a message that tells the operator what to do. This
        # catches what is left -- an unresolvable $ref inside the schema
        # library, a genuine bug here -- so that the ONE promise a caller
        # depends on holds without exception: stdout is always one JSON object
        # and `success` is always present. The full traceback still goes to
        # stderr, so nothing is traded away for the guarantee; only stdout is
        # kept parseable. `except Exception`, never BaseException:
        # KeyboardInterrupt and SystemExit (which is how _refuse()/_accept()
        # return at all) must pass straight through.
        traceback.print_exc()
        print(json.dumps({
            "success": False,
            "error": (
                f"internal error: {type(exc).__name__}: {exc} -- nothing was "
                f"authorized; see the traceback on stderr"
            ),
        }))
        sys.exit(1)
