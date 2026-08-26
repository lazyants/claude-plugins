#!/usr/bin/env python3
"""refuse_finding.py -- #764: the ONLY component allowed to write
segments/{seg}.findings_refused.json, the durable record that ONE finding of
a stored review verdict was considered by a fix turn and REFUSED on the
merits.

WHY THIS EXISTS. Since #532 a fix turn refuses a finding it cannot
substantiate and leaves the text alone -- but SKILL.md states the residue in
its own words: "a refusal is a REPORT, not a record: it changes no file".
The turn's only durable output is segments/{seg}.draft.json, so a draft where
four of five findings were applied and the fifth was refused is BYTE-FOR-BYTE
INDISTINGUISHABLE from a draft where the fifth was simply missed. The next
round's reviewer re-raises the identical finding at the identical locus, and
the next round's FIX agent -- which since #541 can see the previous round's
verdict but never its disposition -- reads an unapplied finding and has only
one supported inference available: dropped. So it applies it.

Measured on SSK vol 1 (79 units, rounds 1-5): 10 reasoned refusals in one
working session, every one of them reported only in the agent's prose to the
orchestrator; 3 durable records on disk, all of them WHOLE-VERDICT
(reject_review.py's artifact). One confirmed corruption of the deliverable --
a conventional biblical patronymic split into two <person> tags, minting an
index entry for a referent the book never mentions, applied at round 5 against
a round-4 refusal the agent had no way to see. Every deterministic gate passed
on both forms, because both are well-formed.

WHY THIS IS NOT reject_review.py, AND NOT A MODE ON IT. That script's
granularity is the WHOLE verdict, and its record is an AUTHORIZATION: the
driver reads it and routes on it (derive_next_action()'s rejected_findings
branch). Rejecting a mixed verdict to set aside one unsound finding would
discard the sound ones with it, and at the mandatory final round it would
converge the unit over them. This record is the opposite kind of thing --
NOTHING READS IT BUT A PROMPT. derive_next_action() never opens this path, no
gate consults it, and a re-raised finding stays entirely legitimate. It buys
one thing: the next fix agent can tell a refusal from an oversight.

WHY THE OPERATOR RUNS THIS AND THE FIX TURN DOES NOT. fixPrompt gives the fix
turn exactly ONE write target ("Change nothing else on disk"), and says in the
same breath that "deciding that a stored verdict does not bind is the
operator's job, never yours". Both still hold. The operator reads the turn's
prose refusal report -- which fixPrompt already requires it to print -- and
records it here, exactly as reject_review.py is run from the same report.

WHY THIS RECORD IS NOT GATED ON A TOKEN, WHERE #541's ARCHIVE IS. One line
above this one's in fixPrompt, the previous round's verdict is admitted only
if its dispatch_token equals a literal the prompt names -- and a security
review asked why this read has no equivalent. Three reasons, recorded here so
the asymmetry is not re-raised as an oversight:

  * A CROSS-ROUND RECORD IS THE FEATURE. #541's archive is about ONE named
    prior round, so pinning it to that round's token is exactly right. A
    refusal made at r4 has to stay legible at r5 and r6 -- that is the whole
    of #764 -- so a current-token gate would reject every record it exists to
    surface. The prompt says outright that entries may name other rounds.
  * A RUN-ID GATE WOULD NOT DEFEND ANYTHING. The threat it was proposed
    against is a turn that writes into segments/ in violation of its own
    single-write-target instruction. That turn can read the run id out of
    {seg}.review.json in the directory it just wrote to, so it would satisfy
    any such gate on the first try.
  * THE SAME PRECONDITION ALREADY GRANTS MORE. A turn able to create
    {seg}.findings_refused.json is a turn able to write {seg}.draft.json --
    the deliverable itself. Planting a hint for a later turn is strictly
    weaker than editing the book directly, so this file adds no capability an
    attacker at that precondition does not already hold.

The bounds below are therefore about a COOPERATING writer's output staying
small and inert, which is what they can be about; they were never a claim to
authenticate the file against a hostile one, and no gate in this pipeline is.

EVERY FIELD IS COMPUTED, DERIVED-AND-REVALIDATED, OR BOUNDED. That is the
whole safety story of this script and it is not a caveat on it. It is a claim
about the RECORD's fields -- the values a prompt reads. Refusal messages on
the foreign-file path quote what they found, are operator-facing, reach no
prompt, and are outside it. Nothing
upstream bounds any string in review.json: review.schema.json types `loc` and
`dispatch_token` as bare strings, and findingsAuthentic()'s AUTHENTIC_LOC_RE
tests a loc's SHAPE (colon-delimited vs bare token), never its size -- its own
comment says so. So ANY value copied out of a stored review into a record that
a prompt reads is unbounded by construction, and this record is read by a turn
authorized to rewrite the book. Three plan-review rounds each found that same
defect at a different field before the design stopped copying:

  * `loc` is the ONE verbatim copy, because it is the only value the next
    turn actually needs, and this script bounds it ITSELF -- against the
    value STORED IN THE REVIEW, not against what the operator typed. A gate
    that only inspects --expect-loc cannot see the ingress it exists to
    guard: the reviewer authored that string, not the operator.
  * `round_label` is DERIVED from the stored dispatch_token and then
    RE-VALIDATED against a length-bounded pattern. Deriving alone is not
    enough: round_label_from_token() validates only the suffix after the
    last ":{seg}:r" marker, and reject_review.py's own _ROUND_LABEL_RE is
    `final|[0-9]+` with no length cap, so a five-thousand-digit label
    derives cleanly from a schema-valid token.
  * `dispatch_token` is READ and ATTESTED but NEVER STORED. Its run half is
    entirely unconfined -- the marker match requires only that SOMETHING
    precede it, and validate_run_id() (segment_dispatch_driver.py) is not on
    this path and carries no length cap either. A 49 008-byte schema-valid
    token whose run half was repeated instruction-like English was measured
    to parse with zero errors. reject_review.py stores its token because its
    CONSUMER matches on it; this record has no code consumer to match
    anything, so the field is simply not there.
  * `operator_invocation` -- reject_review.py's raw `" ".join(sys.argv)` --
    is not here either, for the same reason one field over. It would carry
    the durable-root path, every flag, and the whole --reason a second time,
    unbounded, multiplied by the per-file record cap.

`seg` is confined by its ALPHABET rather than by a length cap, and that is
deliberate: (FRONTBACK:)?[A-Za-z0-9_]+ cannot carry prose, whitespace or a
control character, so it cannot carry an instruction, and validate_seg() is
duplicated byte-for-byte across this plugin's scripts under a census that pins
the canonical contract. A cap in this copy alone would fork it.

WHY A FILE OF ITS OWN, and not the two obvious alternatives -- the same two
claim_record.py and reject_review.py both rule out: the review document is
`additionalProperties: false` (review.schema.json), and a ledger fragment is
erased by the next full-replace write.

NOT IN THE DRAFT, EVER. fixPrompt forbids a refusal marker in notes[] and
engine-loop.md says why: notes[] is the translator's channel and is READ BY
THE NEXT REVIEWER, so a marker there feeds the dispute back into the loop it
was meant to end -- and moving draft_sha1 invalidates the current review
without binding anything.

Exit 0 = the record is on disk and durable (freshly appended, or already
there -- see `already_recorded`). Exit 1 = refused, nothing written. Exit 2 =
usage error (bad seg id, or argparse's own complaint about a malformed
command line).
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
import unicodedata
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
# ${durable_root}/scripts/refuse_finding.py, so parents[1] is the durable
# root -- byte-for-byte the convention reject_review.py/review_ready.py
# already use. Never assumes cwd.
_SCRIPT_FILE = Path(__file__).resolve()
SCRIPTS_DIR = _SCRIPT_FILE.parent
DURABLE_ROOT = _SCRIPT_FILE.parents[1]
SEGMENTS_DIR = DURABLE_ROOT / "segments"
SCHEMAS_DIR = DURABLE_ROOT / "schemas"

# Bounds, all applied AT THIS WRITER. Sized to be generous for the real
# artifact and still far below anything that could crowd a prompt: a real loc
# is ~20 bytes ("PARA:seg12:0013"), and a real refusal reason is a sentence or
# two. Refused, never truncated -- a truncated loc would no longer match the
# finding it names, and a truncated reason is a different reason.
MAX_LOC_BYTES = 200
MAX_REASON_BYTES = 2000
MAX_REFUSALS = 64

# How long to wait for another operator's refusal on the same segment.
REFUSAL_LOCK_TIMEOUT_S = 10.0

_O_CLOEXEC = getattr(os, "O_CLOEXEC", 0)
_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_O_NONBLOCK = getattr(os, "O_NONBLOCK", 0)

# Canonical segment-id safety contract -- duplicated byte-for-byte per this
# project's "no shared lib between self-contained scripts" convention (see
# reject_review.py's/review_ready.py's own identical copies).
# NOTE: re.fullmatch (NOT re.match + "$") -- in Python "$" also matches just
# before a trailing newline, so re.match(r"...$", "seg01\n") would WRONGLY pass.
_SEG_ID_RE = re.compile(r"(?:FRONTBACK:)?[A-Za-z0-9_]+")

# The round labels segment_dispatch_driver.py mints -- "1".."N" and the
# terminal "final" -- WITH A LENGTH BOUND, which is the difference between
# this pattern and reject_review.py's otherwise identical one. That script
# only ever compares its label; this one STORES it in an artifact a prompt
# reads, and `[0-9]+` unbounded lets a schema-valid dispatch_token carry a
# five-thousand-digit label straight through the derivation. Four digits is
# already three orders of magnitude past any max_fix_rounds this pipeline
# would accept.
# `[0-9]` and NOT str.isdigit(): isdigit() is True for Arabic-Indic and
# superscript digits, which int() then rejects or silently reads as something
# else -- not a hypothetical in a pipeline whose sources are Hebrew and Arabic.
_ROUND_LABEL_RE = re.compile(r"final|[0-9]{1,4}")

# 64 lowercase hex -- exactly what hashlib.sha256().hexdigest() returns, so
# exactly what _finding_issue_digest() produces on both sides. Checked BEFORE
# the comparison so a truncated or uppercased paste refuses with "that is not
# a digest" rather than with a mismatch dump the operator must eyeball.
_SHA256_HEX_RE = re.compile(r"[0-9a-f]{64}")

# The record file's two pinned key sets. Kept as constants so the payload
# main() builds and the conforming-file check read_existing_refusals()
# applies cannot drift apart -- a field added to one and forgotten in the
# other would make this script's own idempotent re-run report success over a
# file its own reader treats as foreign.
#
# `seg` lives ONCE, at the top level, rather than being repeated in every
# record: it is a property of the file, and repeating it invites two records
# in one file disagreeing about which segment they describe.
REFUSAL_FILE_KEYS = frozenset({"seg", "refusals"})
REFUSAL_RECORD_KEYS = frozenset({
    "loc",
    "finding_index",
    "round_label",
    "issue_digest",
    "reason",
    "refused_at",
})

# ISO 8601, second resolution, UTC 'Z' -- exactly what now_iso8601() emits.
# Pinned as a PATTERN and not merely as "a string", because this field is read
# back out of an existing file and rewritten into one a prompt consumes: see
# _record_problem() for why every stored field is re-validated rather than
# trusted for having the right key.
_ISO8601_Z_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")

# 0..9999, the same four-digit ceiling _ROUND_LABEL_RE uses and for the same
# reason: it is stored, so it is bounded.
_FINDING_INDEX_RE = re.compile(r"[0-9]{1,4}")

# The SAME ceiling, spelled as a number because the write path compares against
# it before it has a string to match. The two must agree: the writer stores
# str(index) and the reader tests it with _FINDING_INDEX_RE, so an index this
# constant admits and that pattern rejects would write a file the very next
# invocation calls foreign -- a script poisoning its own artifact, reported as
# success. Reachable through a schema-valid review with 10 001 findings
# (review.schema.json sets no maxItems). Belt: the writer also runs the
# assembled record through _record_problem() below, so the class is closed at
# the boundary and not only at this one field.
MAX_FINDING_INDEX = 9999

# U+2028 LINE SEPARATOR and U+2029 PARAGRAPH SEPARATOR, spelled with chr()
# and NEVER as the literal characters. Written literally they are invisible in
# this file: a later edit, a copy-paste, or a reviewer's eye cannot see whether
# they are still there, and one of them silently vanishing would leave the
# check below passing while the guard it names was gone. (This exact
# substitution -- the literal pasted in place of the escape -- happened while
# this function was first being written.)
_LINE_SEPARATORS = (chr(0x2028), chr(0x2029))

_FILE_PRESENT = "present"
_FILE_ABSENT = "absent"
_FILE_AMBIGUOUS = "ambiguous"


def resolve_dirs(durable_root_str, plugin_root_str=None):
    """LT-409 convention, byte-for-byte reject_review.py's own split:
    `durable_root_str` governs DATA (segments/schemas) -- rebuilt from that
    root when given, self-anchored otherwise. `plugin_root_str` governs where
    the claim_record.py SIBLING this script imports (for fsync_directory())
    is found -- deliberately NEVER derived from `durable_root_str`, because
    ${durable_root}/scripts/ is a Step-0a copy other passes in this pipeline
    hold write access over, so resolving a helper this script trusts from
    inside the tree it writes into would let a tampered copy vouch for
    itself."""
    if durable_root_str is None:
        durable_root = DURABLE_ROOT
        segments_dir = SEGMENTS_DIR
        schemas_dir = SCHEMAS_DIR
    else:
        durable_root = Path(durable_root_str).resolve()
        segments_dir = durable_root / "segments"
        schemas_dir = durable_root / "schemas"

    scripts_dir = SCRIPTS_DIR
    if plugin_root_str is not None:
        scripts_dir = Path(plugin_root_str).resolve() / "assets" / "scripts"

    return {
        "durable_root": durable_root,
        "scripts_dir": scripts_dir,
        "segments_dir": segments_dir,
        "schemas_dir": schemas_dir,
    }


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


def refusals_path(seg, segments_dir=SEGMENTS_DIR):
    return segments_dir / f"{seg}.findings_refused.json"


def refusal_lock_path(seg: str, segments_dir: Path) -> Path:
    """`.refuse_finding.{seg}.lock`, beside the file it serialises -- the same
    naming and placement reject_review.py and codex_job.py use for their own
    per-segment locks."""
    return segments_dir / f".refuse_finding.{seg}.lock"


def classify_file(path: Path, *, follow_symlinks: bool):
    """Classify what occupies `path`: `(_FILE_PRESENT|_FILE_ABSENT|
    _FILE_AMBIGUOUS, detail)`, where `detail` is empty for the two decided
    verdicts and operator-actionable for the ambiguous one. A local copy of
    reject_review.py's own function, for the reason that one is local to it:
    this script must answer the question before it imports any sibling, and it
    needs both symlink policies rather than one.

    NEVER Path.exists()/is_file(): those SWALLOW OSError and answer as if the
    thing were absent, so "the review is not there" and "the review is there
    and I was not allowed to look" come back indistinguishable -- and from
    Python 3.14 Path.exists() swallows EVERY OSError while this plugin's floor
    is 3.10. Absence is established ONLY by the two errors that mean it
    (FileNotFoundError, NotADirectoryError); every other OSError is "could not
    look", which each caller maps to its own safe direction. For both callers
    here that direction is refusal.

    `follow_symlinks=True` for a file this script only READS (review.json,
    review.schema.json). `False` -- lstat -- for the refusals file itself,
    whose FINAL component must stay unresolved: a symlink there must never be
    read as "the file I would have written"."""
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
    halves around it can contain a colon -- `FRONTBACK:errata_02` is a shipped
    segment id -- so splitting on ':' from the left would hand back a fragment
    of the id and call it a run. The marker cannot occur inside the label
    itself, labels being digits or 'final', so the LAST occurrence is the real
    one.

    THE LENGTH BOUND IS HERE AND NOT ONLY IN THE PATTERN'S DOCSTRING. This
    function's counterpart in reject_review.py accepts `final|[0-9]+`, which
    is correct for a value that is only ever COMPARED. This one's result is
    STORED in an artifact a prompt reads, and a schema-valid dispatch_token
    can carry any number of digits, so an unbounded label would be an
    unbounded field by another name -- the exact defect this record's design
    exists to make impossible. `_ROUND_LABEL_RE` caps it at four digits.

    What is deliberately NOT checked here is the run half: it is not knowable
    from a leaf script, --expect-token binds the whole token to the review on
    disk, and -- decisively -- this script never stores it, so an unconfined
    run id has nowhere to go."""
    if not isinstance(token, str) or not token:
        return None, "the stored review carries no dispatch_token"
    marker = f":{seg}:r"
    idx = token.rfind(marker)
    if idx <= 0:
        return None, (
            f"the stored review's dispatch_token is not of the form "
            f"'<run_id>:{seg}:r<round_label>' that reviewDispatchPrompt mints for "
            f"segment {seg!r}"
        )
    label = token[idx + len(marker):]
    if not _ROUND_LABEL_RE.fullmatch(label):
        return None, (
            f"the stored review's dispatch_token carries a round label that is "
            f"neither 'final' nor one to four decimal digits (got {len(label)} "
            f"characters). Refusing rather than store it: this label goes into a "
            f"record the next fix turn reads"
        )
    return label, None


def now_iso8601():
    """Second-resolution UTC, 'Z' suffix -- byte-for-byte ledger_update.py's
    own now_iso8601(), duplicated per this project's "no shared lib"
    convention."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _finding_issue_digest(issue: str) -> str:
    """sha256 of ONE finding's `issue` string.

    Over the issue text ALONE, not over the whole finding object, and that is
    the point of the field: the next round's reviewer re-raising the same
    claim writes the same issue text, so an identical digest is what makes a
    re-raise recognisable as one. Folding in `severity` or `suggest` -- which
    a reviewer is free to reword between rounds while making the identical
    claim -- would make the digest change for a re-raise and defeat that.

    sha256, matching reject_review.py's own attestation digest, so the two
    tools' --expect-* values look alike to an operator moving between them."""
    return hashlib.sha256(issue.encode("utf-8")).hexdigest()


def _issue_digest_problem(issue: str) -> "str | None":
    """None, or why `issue` cannot be digested at all.

    The issue text is the one untrusted string this script HASHES without
    storing, so the bounded-domain guards below never see it -- and hashing is
    where it fails: json.loads() accepts "\\ud800" and returns a str that
    .encode("utf-8") refuses, so _finding_issue_digest() raises
    UnicodeEncodeError and the process dies on a traceback having printed no
    JSON line. Checked rather than caught, so the caller gets the same shaped
    refusal every other gate here produces. Reported by code point, never by
    echoing the character."""
    for index, ch in enumerate(issue):
        if unicodedata.category(ch) == "Cs":
            return (
                f"the stored finding's issue carries an unpaired surrogate "
                f"(U+{ord(ch):04X}) at offset {index}, which has no UTF-8 "
                f"encoding, so no digest can be computed for it."
            )
    return None


def _control_char_problem(value: str, what: str) -> "str | None":
    """None, or an error naming the first control character in `value`.

    Cc covers the C0/C1 ranges including U+0085 NEL; U+2028 LINE SEPARATOR and
    U+2029 PARAGRAPH SEPARATOR are category Zl/Zp and are added by hand,
    because they break a line in most consumers while passing every
    "is it a control character" test that only asks unicodedata. All three
    matter here for one reason: this value is spliced into a prompt, and a
    line break in the middle of it can make the text below read as a new
    instruction. Reported by CODE POINT, never by echoing the character --
    an error message is a second place the character would travel."""
    for index, ch in enumerate(value):
        # Cs -- an unpaired surrogate. It belongs in THIS guard and not in a
        # separate one, because it is the same class of defect one step
        # earlier: json.loads() accepts "\\ud800" and hands back a str that
        # .encode("utf-8") refuses, so without this the byte measurement below
        # raises UnicodeEncodeError and the script exits on a traceback having
        # printed no JSON line at all -- breaking the promise every path here
        # makes to a caller that branches on `success`. Measured: a stored loc
        # of "PARA:seg12:00\\ud800" did exactly that.
        if unicodedata.category(ch) in ("Cc", "Cs") or ch in _LINE_SEPARATORS:
            return (
                f"{what} carries a control or surrogate code point "
                f"(U+{ord(ch):04X}) at offset "
                f"{index}. Refusing rather than record it: this value is spliced "
                f"verbatim into the next fix turn's prompt, where a control "
                f"character can break the surrounding line, hide text from anyone "
                f"reading the record, or make what follows read as a fresh "
                f"instruction."
            )
    return None


def _bound_problem(value: str, what: str, max_bytes: int) -> "str | None":
    """None, or an error explaining why `value` is not storable. Length is
    measured in UTF-8 BYTES, not characters: the prompt carries bytes, and a
    Hebrew or Arabic character is two of them, so a character count would
    admit roughly double what it appears to.

    REFUSED, NEVER TRUNCATED. A truncated loc no longer names the finding it
    was about, and a truncated reason is a different reason -- both would be
    stored as if they were the real thing."""
    problem = _control_char_problem(value, what)
    if problem is not None:
        return problem
    encoded = len(value.encode("utf-8"))
    if encoded > max_bytes:
        return (
            f"{what} is {encoded} UTF-8 bytes, over this record's {max_bytes}-byte "
            f"cap. Refused rather than truncated -- a shortened value is stored as "
            f"if it were the real one. Nothing upstream bounds this string "
            f"(review.schema.json types it as a bare string), and it is spliced "
            f"into a prompt read by a turn authorized to rewrite the draft."
        )
    return None


def _foreign_file_error(path: Path, what: str) -> str:
    """The one refusal wording for "something occupies the refusals file's
    path and it is not a file this script may append to or silently destroy".
    Shared by every such branch so the operator gets the identical
    instruction whichever way the file is unusable."""
    return (
        f"the file at {path} is not a refusal record this script can append to "
        f"({what}). Replacing it silently would destroy whatever it is: inspect "
        f"it, delete it deliberately if it is not wanted, then re-run."
    )


def _record_problem(record) -> "str | None":
    """None if one entry of `refusals` satisfies EVERY rule this script's own
    writer applies, else a description of the first violation.

    THE KEY SET IS NOT ENOUGH, and an earlier revision of this function
    checking only the key set plus "every value is a string" was the hole that
    made the module docstring's invariant false. This script rewrites the whole
    array on every append, so an entry it merely PRESERVES is an entry it
    PUBLISHES -- and the file it publishes is read verbatim by a turn
    authorized to rewrite the draft. An exact-key record carrying 5 KB fields,
    an invalid digest and a U+2028 therefore crossed the trust boundary
    untouched, signed by the sole writer, while every bound below was applied
    only to the ONE field the current invocation happened to add.

    So the reader re-derives the writer's rules rather than trusting the shape:
    every field goes through the same bound, the same control-character
    refusal, and the same pattern the writer would have applied. Anything that
    fails makes the FILE foreign (see read_existing_refusals()), which refuses
    rather than silently dropping the entry -- dropping it would destroy an
    operator's record, and this script never destroys one.

    Extra keys are refused rather than ignored for the reason the key set is
    pinned at all: an unrecognised field means the entry came from a writer
    whose rules we do not know."""
    if not isinstance(record, dict):
        return f"it is a JSON {type(record).__name__}, not a JSON object"
    if set(record) != REFUSAL_RECORD_KEYS:
        missing = sorted(REFUSAL_RECORD_KEYS - set(record))
        extra = sorted(set(record) - REFUSAL_RECORD_KEYS)
        return f"its key set is wrong -- missing {missing}, unexpected {extra}"
    for key in sorted(REFUSAL_RECORD_KEYS):
        if not isinstance(record[key], str):
            return f"its {key} is a JSON {type(record[key]).__name__}, not a string"
    problem = _bound_problem(record["loc"], "its stored loc", MAX_LOC_BYTES)
    if problem is not None:
        return problem
    problem = _bound_problem(record["reason"], "its stored reason", MAX_REASON_BYTES)
    if problem is not None:
        return problem
    if not _ROUND_LABEL_RE.fullmatch(record["round_label"]):
        return "its round_label is neither 'final' nor one to four decimal digits"
    if not _FINDING_INDEX_RE.fullmatch(record["finding_index"]):
        return "its finding_index is not one to four decimal digits"
    if not _SHA256_HEX_RE.fullmatch(record["issue_digest"]):
        return "its issue_digest is not 64 lowercase hex characters"
    if not _ISO8601_Z_RE.fullmatch(record["refused_at"]):
        return "its refused_at is not a second-resolution UTC ISO 8601 timestamp"
    return None


def read_existing_refusals(path: Path, seg: str) -> "tuple[list | None, str | None]":
    """`(refusals_list, None)` for a conforming file already on disk,
    `([], None)` for "nothing is there", or `(None, error)` when the path is
    occupied by something this script must not silently destroy.

    Called INSIDE the lock and BEFORE the write, because the append is a
    read-modify-write and every indeterminate answer must refuse: "I could not
    look" reported as "there was nothing there" is the precise shape that lets
    one operator's records be erased by another's append.

    A non-regular entry refuses rather than letting os.replace() quietly
    consume it -- a refusal record is a local fact and a symlink is not one.
    A file whose `seg` disagrees with this invocation's is FOREIGN, not stale:
    it describes a different segment and appending to it would put two
    segments' refusals in one file."""
    state, detail = classify_file(path, follow_symlinks=False)
    if state == _FILE_ABSENT:
        return [], None
    if state == _FILE_AMBIGUOUS:
        return None, _foreign_file_error(path, detail)
    try:
        raw = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        return None, _foreign_file_error(path, f"it is not valid UTF-8: {exc}")
    except OSError as exc:
        return None, _foreign_file_error(path, f"it is unreadable: {exc}")
    try:
        loaded = json.loads(raw)
    except (ValueError, RecursionError) as exc:
        return None, _foreign_file_error(path, f"it could not be parsed as JSON: {exc}")
    if not isinstance(loaded, dict):
        return None, _foreign_file_error(
            path, f"it is a JSON {type(loaded).__name__}, not a JSON object"
        )
    if set(loaded) != REFUSAL_FILE_KEYS:
        missing = sorted(REFUSAL_FILE_KEYS - set(loaded))
        extra = sorted(set(loaded) - REFUSAL_FILE_KEYS)
        return None, _foreign_file_error(
            path, f"its key set is not the pinned two -- missing {missing}, unexpected {extra}"
        )
    if loaded["seg"] != seg:
        return None, _foreign_file_error(
            path, f"it records segment {loaded['seg']!r}, not {seg!r}"
        )
    refusals = loaded["refusals"]
    if not isinstance(refusals, list):
        return None, _foreign_file_error(
            path, f"its `refusals` is a JSON {type(refusals).__name__}, not an array"
        )
    if len(refusals) > MAX_REFUSALS:
        # CARDINALITY IS A PROPERTY OF THE FILE, so it is checked HERE and not
        # only on the append path. The append cap further down is never reached
        # when the invocation matches a record already stored: that branch
        # renews durability and returns success, so without this an over-cap
        # file would be accepted, reported durable, and -- the part that
        # matters -- spliced whole into the next fix turn's prompt. This script
        # never writes more than MAX_REFUSALS, so a file over it was not
        # written by this script, which is exactly what _foreign_file_error()
        # is for. `>` and not `>=`: a file holding exactly the cap is one the
        # writer itself produces.
        return None, _foreign_file_error(
            path, f"it carries {len(refusals)} records, over this file's cap of "
                  f"{MAX_REFUSALS}. Every record here is spliced into the next fix "
                  f"turn's prompt, and an unbounded array is an unbounded prompt"
        )
    for index, record in enumerate(refusals):
        problem = _record_problem(record)
        if problem is not None:
            return None, _foreign_file_error(
                path, f"its refusals[{index}] is not a record this script would "
                      f"have written ({problem})"
            )
    return refusals, None


def _load_review_schema(schemas_dir=SCHEMAS_DIR):
    """Returns (schema_dict, None) or (None, error_message) -- never raises,
    and that promise is TOTAL. classify_file() rather than Path.is_file() (see
    there for why the swallowing matters), and the read catches the unrelated
    ways it fails: OSError for IO, UnicodeDecodeError (a ValueError, which
    `except OSError` does NOT catch) for a non-UTF-8 byte, and
    `(ValueError, RecursionError)` for the parse.

    `except json.JSONDecodeError` WOULD NOT KEEP THAT PROMISE, and the gap is
    silent -- so all three parse sites in this file catch the pair instead.
    json.loads() has three failure modes, not one: JSONDecodeError IS a
    ValueError but the reverse does not hold, so an integer token longer than
    sys.get_int_max_str_digits() (4300 since 3.11) raises a plain ValueError a
    JSONDecodeError handler does not catch; and deep nesting exhausts the C
    stack with a RecursionError, which is not a ValueError at all. Measured
    against this script on 3.14: a review.json of 300 000 nested arrays, and
    one carrying a 5 000-digit integer token, each escaped as a bare traceback
    printing no JSON line -- so a caller branching on `success` had nothing to
    branch on."""
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
    except (ValueError, RecursionError) as exc:
        return None, f"review.schema.json at {path} could not be parsed as JSON: {exc}"


def load_review(seg: str, dirs: dict) -> "tuple[dict | None, str | None]":
    """`(review_obj, None)` for a stored review this script could act on, or
    `(None, error)`. Everything that is a fact about the REVIEW ON DISK -- it
    is there, it is readable, it parses to an object, it is schema-valid --
    and nothing that is a fact about the caller's INTENT.

    ONE function because both modes must agree byte-for-byte on what "the
    review" is: --print-finding-digests hands the operator digests, and the
    write path then demands one of them back. If the two computed over even
    slightly differently-obtained bytes, the tool would print a value it then
    refuses.

    Deliberately NO `clean is False` gate, which is where this departs from
    reject_review.py's otherwise identical loader. That script cannot reject a
    clean verdict, so printing a digest for one would advertise a next step
    that refuses. Here the predicate is different and weaker: review.schema
    .json's own `findings` description says a verdict may be `clean: true`
    and still carry residual findings the reviewer chose not to fix-round, and
    a fix turn can be dispatched over any verdict that carries findings at
    all. Gating on `clean` would refuse to record a refusal that really
    happened."""
    rpath = review_path(seg, dirs["segments_dir"])
    state, detail = classify_file(rpath, follow_symlinks=True)
    if state == _FILE_ABSENT:
        return None, (
            f"no stored review at {rpath} -- there is no finding to refuse. A "
            f"refusal record names a finding some reviewer actually raised."
        )
    if state == _FILE_AMBIGUOUS:
        return None, f"the stored review could not be read: {detail}"
    try:
        raw = rpath.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        return None, f"the stored review at {rpath} is not valid UTF-8: {exc}"
    except OSError as exc:
        return None, f"the stored review at {rpath} is unreadable: {exc}"
    try:
        review_obj = json.loads(raw)
    except (ValueError, RecursionError) as exc:
        return None, f"the stored review at {rpath} could not be parsed as JSON: {exc}"
    if not isinstance(review_obj, dict):
        return None, (
            f"the stored review at {rpath} is a JSON "
            f"{type(review_obj).__name__}, not a JSON object"
        )
    schema, schema_err = _load_review_schema(dirs["schemas_dir"])
    if schema_err is not None:
        return None, schema_err
    try:
        jsonschema.validate(instance=review_obj, schema=schema)
    except jsonschema.exceptions.ValidationError as exc:
        return None, (
            f"the stored review at {rpath} does not satisfy review.schema.json "
            f"({exc.message}). Refusing to record a refusal against a verdict "
            f"nothing downstream would accept."
        )
    except jsonschema.exceptions.SchemaError as exc:
        return None, f"review.schema.json is not a usable schema: {exc.message}"
    return review_obj, None


def acquire_refusal_lock(seg: str, segments_dir: Path,
                         timeout_s: float = REFUSAL_LOCK_TIMEOUT_S):
    """`(fd, None)` holding an exclusive kernel flock, or `(None, problem)`.

    WHY A LOCK, when this is a command a human types. The append is a
    read-modify-write: this script reads `refusals`, adds one entry, and
    publishes the whole array with an unconditional os.replace(). Two
    operators recording two different refusals on the same segment can both
    read the same array and the later replace erases the earlier one's entry
    -- silently, and a record whose absence is exactly what #764 is about.

    So the WHOLE sequence -- read, idempotence check, cap check, write,
    directory sync -- runs inside one critical section, and this opens it.

    A KERNEL FLOCK, not a lockfile whose presence means "locked": the kernel
    releases it when the holder dies, so a crashed operator cannot wedge the
    segment and there is no stale-break race to get wrong. The lock FILE is
    left behind on purpose; it carries no state.

    LOCK_NB in a bounded retry loop rather than a blocking LOCK_EX: a human
    waiting on a terminal deserves a refusal that names the problem, not an
    indefinite hang."""
    lock_path = refusal_lock_path(seg, segments_dir)
    try:
        segments_dir.mkdir(parents=True, exist_ok=True)
        # O_NOFOLLOW|O_NONBLOCK AND an fstat, for the provenance reason
        # reject_review.py states for its own lock: the path is predictable
        # and lives in a directory other processes can write. Without
        # O_NOFOLLOW a planted symlink is FOLLOWED and O_CREAT then creates
        # its external target with the operator's privileges. O_NONBLOCK
        # because a FIFO at this path would otherwise hang the command
        # instead of refusing it.
        fd = os.open(
            str(lock_path),
            os.O_CREAT | os.O_RDWR | _O_CLOEXEC | _O_NOFOLLOW | _O_NONBLOCK,
            0o600,
        )
    except OSError as exc:
        return None, (
            f"could not open the refusal lock at {lock_path}: {exc}. A symlink "
            f"or special file at that path is REFUSED rather than followed -- if "
            f"one is there, something else put it there. Nothing was read, "
            f"written or removed."
        )
    # The descriptor, never the path: what was opened is the only thing that
    # can be judged without reintroducing the TOCTOU the open just closed.
    try:
        lock_st = os.fstat(fd)
    except OSError as exc:
        os.close(fd)
        return None, (
            f"could not stat the refusal lock at {lock_path} after opening it: "
            f"{exc}. Refusing rather than serialise on something whose kind could "
            f"not be established."
        )
    if not stat.S_ISREG(lock_st.st_mode):
        os.close(fd)
        return None, (
            f"the refusal lock path {lock_path} is not a regular file. Refusing "
            f"rather than take a lease on a device, socket or directory -- remove "
            f"whatever is at that path and re-run. Nothing was read, written or "
            f"removed."
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
                    f"another refusal for segment {seg!r} is in progress and still "
                    f"holds {lock_path} after {timeout_s:g}s. Nothing was read, "
                    f"written or removed. Wait for it to finish and re-run -- if no "
                    f"other operator is running one, a process holding that lock is "
                    f"stuck and must be ended before this can proceed."
                )
            time.sleep(0.1)


def _import_claim_record(scripts_dir: Path):
    """Sibling import of claim_record.py, for its fsync_directory() only --
    loaded BY PATH from `scripts_dir`, unconditionally.

    THE SAME SHAPE reject_review.py and segment_dispatch_driver.py already use
    for this exact sibling, and for the same reason: a bare `import
    claim_record` resolves against sys.path[0] -- THIS process's own physical
    directory -- even when `scripts_dir` names a different, trusted
    --plugin-root tree, so the trusted path would be silently ignored."""
    import importlib.util

    path = scripts_dir / "claim_record.py"
    state, detail = classify_file(path, follow_symlinks=True)
    if state == _FILE_ABSENT:
        return None, (
            f"claim_record.py not found at {path} -- this script needs its "
            f"fsync_directory() to make the record durable. Pass --plugin-root "
            f"pointing at the plugin's own skills/literary-translator directory."
        )
    if state == _FILE_AMBIGUOUS:
        return None, f"claim_record.py could not be read: {detail}"
    try:
        spec = importlib.util.spec_from_file_location("claim_record_for_refusal", str(path))
        if spec is None or spec.loader is None:
            return None, f"claim_record.py at {path} could not be loaded as a module"
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    except Exception as exc:  # noqa: BLE001 -- any import failure is a refusal, not a traceback
        return None, f"claim_record.py at {path} could not be imported: {exc}"
    if not hasattr(module, "fsync_directory"):
        return None, (
            f"claim_record.py at {path} has no fsync_directory() -- this is not "
            f"the module this script expects."
        )
    return module, None


def write_refusals_file(path: Path, payload: dict, claim_record_mod) -> "str | None":
    """Publish the refusals file: None on a durable write, or an error string.
    Temp file + fsync + os.replace() + directory fsync.

    ENCODE BEFORE CREATING THE TEMP FILE, the ordering claim_record.py's
    write_claim_record() uses and explains: `ensure_ascii=False` lets
    json.dumps() return a str containing a lone surrogate when `reason`
    (operator text) carries one, and encoding that to UTF-8 raises
    UnicodeEncodeError -- a ValueError, not an OSError. Catching it BEFORE any
    file exists leaves no partial artifact behind.

    A FAILED DURABILITY STEP KEEPS THE FILE, and that is the deliberate
    OPPOSITE of reject_review.py's choice for its own record. The two
    artifacts fail in opposite directions because they are opposite kinds of
    thing. That one is an AUTHORIZATION: a record left live after a reported
    failure lets an unchanged draft advance with nobody watching, so removing
    it is what fail-closed means there. This one AUTHORIZES NOTHING -- no gate
    reads it, no routing turns on it, the prompt that reads it says in its own
    text that it justifies refusing nothing. The only thing at stake is an
    operator's reason, and losing that is the failure #764 is about. So the
    file stays and the problem is reported."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return f"could not create {path.parent} for the refusal record: {exc}"
    body = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False) + "\n"
    try:
        blob = body.encode("utf-8")
    except UnicodeEncodeError as exc:
        return f"could not encode the refusal record as UTF-8, so nothing was written: {exc}"

    # O_CREAT|O_EXCL and a RANDOM suffix rather than the pid, for the reason
    # reject_review.py gives for its own temp file: a plain open() on a
    # predictable name follows a symlink planted at that name, redirecting
    # these bytes onto a file outside the durable root, after which
    # os.replace() renames the symlink onto the record path. O_EXCL refuses
    # ANY pre-existing entry, symlink included; the random suffix removes both
    # the predictability and the stale-leftover collision a recycled pid
    # would cause.
    tmp_path = path.parent / f".{path.name}.tmp.{os.getpid()}.{os.urandom(6).hex()}"
    try:
        fd = os.open(tmp_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except OSError as exc:
        # NOTHING IS UNLINKED ON THIS PATH, and the split from the try below
        # exists to make that true. This open is what would have CREATED
        # tmp_path, so if it raised, whatever occupies that path is not ours,
        # and O_EXCL's whole job is to refuse exactly the entry someone else
        # planted there.
        return (
            f"could not create the refusal record's temporary file at {tmp_path} "
            f"(nothing was written, and anything already at that path was left "
            f"exactly as it was): {exc}"
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
        return f"could not write the refusal record: {exc}"

    sync_problem = claim_record_mod.fsync_directory(path.parent)
    if sync_problem is not None:
        return (
            f"the refusal record was written but could NOT be made durable "
            f"({sync_problem}), so it may not survive a crash. The file at {path} "
            f"is KEPT -- it authorizes nothing, and a lost reason is the failure "
            f"this record exists to prevent. Fix the directory (permissions, "
            f"filesystem) and re-run this command to renew it."
        )
    return None


def _refuse(error: str) -> NoReturn:
    print(json.dumps({"success": False, "error": error}))
    sys.exit(1)


def _accept(payload: dict) -> NoReturn:
    print(json.dumps(payload))
    sys.exit(0)


def _print_digests_command(seg: str, args) -> str:
    """The EXACT --print-finding-digests invocation for this segment, in the
    operator's own setup: same interpreter spelling and same script path they
    just used, and --durable-root carried over when they passed one, since
    without it the read would self-anchor somewhere else and answer about a
    different tree.

    A refusal that names a remedy the operator cannot reach is worse than one
    that names none. Every refusal that demands an --expect-* value calls
    this."""
    parts = [
        shlex.quote(sys.executable if sys.executable else "python3"),
        shlex.quote(sys.argv[0]),
        shlex.quote(seg),
        "--print-finding-digests",
    ]
    if args.durable_root:
        parts += ["--durable-root", shlex.quote(str(args.durable_root))]
    return " ".join(parts)


def _print_finding_digests(seg: str, dirs: dict) -> NoReturn:
    """--print-finding-digests: a PURE READ that hands the operator every
    value the write path will demand, and exits.

    It exists because --expect-issue-digest is required and
    _finding_issue_digest() is a private function over the stored review --
    there is otherwise no way for a person to obtain the value the tool
    insists on, and a required flag whose value cannot be obtained is an
    unusable tool, not a strict one.

    IT DOES NOT WEAKEN THE BINDING. The operator still reads the finding and
    passes the values back by hand; what is removed is the impossibility of
    doing so. Nothing here auto-fills a flag into a write -- that would
    restore the exact hole the attestation closes, since the values would then
    describe whatever is on disk at write time rather than what a human read.

    ONE READ, ONE SET. The token, the round label and every finding's digest
    come from a single parse, so they always describe one verdict; two
    independent reads can straddle a re-dispatch and produce a token from
    before it with a digest from after.

    WRITES NOTHING -- no file, no parent directory, no claim_record import. A
    read-only mode that can fail with a filesystem side effect is not a
    read-only mode, and this one is reached by operators who are already
    unsure what is on disk.

    IT REPORTS AN OVER-BOUND `loc` RATHER THAN HIDING IT. A finding whose
    stored loc the write path will refuse is listed with its `loc_problem`
    filled in and its `loc` omitted -- so the operator learns why that finding
    cannot be recorded here, without this read-only mode becoming a second
    place the offending string travels."""
    review_obj, err = load_review(seg, dirs)
    if err is not None:
        _refuse(err)
    token = review_obj.get("dispatch_token")
    label, label_err = round_label_from_token(token, seg)
    findings = review_obj.get("findings") or []
    rows = []
    for index, finding in enumerate(findings):
        loc = finding.get("loc")
        issue = finding.get("issue")
        # Both are `required` in review.schema.json and the object validated
        # above, so a non-string here is not reachable through the schema --
        # but this loop is what a person reads to decide, so it reports the
        # anomaly rather than raising on it.
        loc_problem = (
            _bound_problem(loc, "the stored finding's loc", MAX_LOC_BYTES)
            if isinstance(loc, str) else "the stored finding's loc is not a string"
        )
        issue_problem = (
            _issue_digest_problem(issue) if isinstance(issue, str)
            else "the stored finding's issue is not a string"
        )
        rows.append({
            "finding_index": index,
            "loc": None if loc_problem else loc,
            "loc_problem": loc_problem,
            "severity": finding.get("severity"),
            "issue_digest": (
                None if issue_problem else _finding_issue_digest(issue)
            ),
            # Symmetric with loc_problem, and present for the same reason: a
            # row whose digest is null tells the operator nothing about WHY,
            # and this mode exists to be read by a person deciding what to do.
            "issue_problem": issue_problem,
        })
    # EVERY key is always present, `null` when unavailable, rather than some
    # appearing only sometimes: a caller that has to branch on which keys
    # exist is a caller that will get it wrong once. An unparseable round
    # label is reported and not fatal here -- the digests are still exactly
    # right, and the write path will refuse on the label with its own message.
    _accept({
        "success": True,
        "seg": seg,
        "review_path": str(review_path(seg, dirs["segments_dir"])),
        "dispatch_token": token,
        "round_label": label,
        "round_label_problem": label_err,
        "findings": rows,
    })


def build_arg_parser():
    parser = argparse.ArgumentParser(
        description=(
            "Record durably that ONE finding of a stored review verdict was "
            "considered and REFUSED on the merits (#764). Records only -- it "
            "authorizes nothing, gates nothing, and changes no routing."
        ),
    )
    parser.add_argument("seg", help="Segment identifier.")
    parser.add_argument(
        "--print-finding-digests",
        action="store_true",
        help=(
            "PURE READ. Print the stored review's dispatch_token and round "
            "label, and every finding's index, loc and issue digest, from one "
            "read. Writes nothing. Every --expect-* value below comes from here."
        ),
    )
    parser.add_argument(
        "--finding-index",
        type=int,
        default=None,
        help=(
            "0-based index into the stored review's findings[] of the finding "
            "that was refused. An INDEX and not a loc: review.schema.json puts "
            "no uniqueness constraint on loc, and one block routinely carries "
            "several findings, so a loc selector would silently record the "
            "wrong one."
        ),
    )
    parser.add_argument(
        "--reason",
        default=None,
        help=(
            "Why the finding was refused, in the fix turn's own terms. Required "
            "and checked here (not argparse `required=True`) so a missing value "
            "refuses with this tool's own JSON error line rather than an "
            "argparse usage crash."
        ),
    )
    parser.add_argument(
        "--round-label",
        default=None,
        help=(
            "The round the refused finding belongs to. Must EQUAL the label "
            "derived from the stored review's own dispatch_token; a disagreement "
            "is refused rather than recorded."
        ),
    )
    parser.add_argument(
        "--expect-token",
        default=None,
        help=(
            "The stored review's dispatch_token, copied verbatim from "
            "--print-finding-digests. Nothing auto-fills it: passing it back IS "
            "the attestation that a human read that exact verdict."
        ),
    )
    parser.add_argument(
        "--expect-loc",
        default=None,
        help="The selected finding's loc, copied verbatim from --print-finding-digests.",
    )
    parser.add_argument(
        "--expect-issue-digest",
        default=None,
        help=(
            "The selected finding's issue digest (64 lowercase hex), copied "
            "verbatim from --print-finding-digests."
        ),
    )
    parser.add_argument(
        "--durable-root",
        default=None,
        help=(
            "Durable root governing segments/ and schemas/. Omitted, this script "
            "self-anchors to its own ${durable_root}/scripts/ location."
        ),
    )
    parser.add_argument(
        "--plugin-root",
        default=None,
        help=(
            "The plugin's skills/literary-translator directory, deciding where "
            "the trusted claim_record.py sibling is loaded from. Never derived "
            "from --durable-root: that scripts/ copy is writable by other passes "
            "in this pipeline."
        ),
    )
    return parser


def main():
    args = build_arg_parser().parse_args()

    seg = args.seg
    seg_problem = validate_seg(seg)
    if seg_problem is not None:
        print(json.dumps({"success": False, "error": seg_problem}))
        sys.exit(2)

    dirs = resolve_dirs(args.durable_root, args.plugin_root)

    if args.print_finding_digests:
        # The read mode takes NO --plugin-root-resolved sibling and creates no
        # directory: resolve_dirs() is called for its data paths only, and
        # nothing below this line runs. Kept as an early return rather than a
        # branch further down so that "this mode writes nothing" is true by
        # structure and not by review.
        _print_finding_digests(seg, resolve_dirs(args.durable_root))

    if args.finding_index is None:
        _refuse(
            "a --finding-index is required (0-based, into the stored review's "
            f"findings[]). Read the findings with: {_print_digests_command(seg, args)}"
        )
    if args.finding_index < 0:
        _refuse(
            f"--finding-index must be 0 or greater; got {args.finding_index}. "
            f"Negative indices would silently select from the end of findings[]."
        )
    if args.finding_index > MAX_FINDING_INDEX:
        _refuse(
            f"--finding-index must be at most {MAX_FINDING_INDEX}; got "
            f"{args.finding_index}. The index is STORED, so it is bounded like "
            f"every other stored field: a wider one writes a record this "
            f"script's own reader calls foreign, which would report success now "
            f"and lose every later refusal for this segment."
        )

    reason = (args.reason or "").strip()
    if not reason:
        _refuse("a --reason is required and must be non-empty (after stripping whitespace)")
    reason_problem = _bound_problem(reason, "--reason", MAX_REASON_BYTES)
    if reason_problem is not None:
        _refuse(reason_problem)

    round_label = (args.round_label or "").strip()
    if not round_label:
        _refuse("a --round-label is required and must be non-empty")

    if not args.expect_token:
        _refuse(
            "an --expect-token is required. Take it from: "
            f"{_print_digests_command(seg, args)}"
        )
    if args.expect_loc is None:
        _refuse(
            "an --expect-loc is required. Take it from: "
            f"{_print_digests_command(seg, args)}"
        )
    expect_digest = (args.expect_issue_digest or "").strip()
    if not expect_digest:
        _refuse(
            "an --expect-issue-digest is required. Take it from: "
            f"{_print_digests_command(seg, args)}"
        )
    if not _SHA256_HEX_RE.fullmatch(expect_digest):
        _refuse(
            "--expect-issue-digest is not a digest: it must be exactly 64 "
            "lowercase hex characters. Checked before any comparison so a "
            "truncated or uppercased paste says so, rather than producing a "
            "mismatch dump to eyeball."
        )

    review_obj, err = load_review(seg, dirs)
    if err is not None:
        _refuse(err)

    token = review_obj.get("dispatch_token")
    if token != args.expect_token:
        _refuse(
            f"--expect-token does not match the stored review's own "
            f"dispatch_token for segment {seg!r}. Either you are looking at a "
            f"different review than the one on disk, or it changed since you read "
            f"it. Read it now: {_print_digests_command(seg, args)}"
        )

    token_label, label_err = round_label_from_token(token, seg)
    if label_err is not None:
        _refuse(
            f"cannot establish which round the stored review for segment {seg!r} "
            f"belongs to: {label_err}"
        )
    if round_label != token_label:
        _refuse(
            f"--round-label {round_label!r} disagrees with the stored review's own "
            f"dispatch_token, which names round {token_label!r}. Refusing rather "
            f"than record a round this review never belonged to -- if {token_label!r} "
            f"is the round you meant, pass it; if it is not, you are looking at a "
            f"different review than the one on disk"
        )

    findings = review_obj.get("findings") or []
    if args.finding_index >= len(findings):
        _refuse(
            f"--finding-index {args.finding_index} is out of range: the stored "
            f"review for segment {seg!r} carries {len(findings)} finding(s). A "
            f"refusal record names a finding some reviewer actually raised."
        )
    finding = findings[args.finding_index]

    stored_loc = finding.get("loc")
    if not isinstance(stored_loc, str):
        _refuse(
            f"the stored finding at index {args.finding_index} has no string loc, "
            f"so there is nothing to record it against."
        )
    # THE BOUND IS ON THE STORED VALUE, checked before the equality gate below
    # and not after it. The reviewer -- an LLM -- authored this string, and
    # nothing upstream bounds it; a gate that only inspected --expect-loc
    # would be inspecting what the operator typed, which is not the ingress.
    # Checking it FIRST also means an over-bound loc refuses with the reason
    # ("this value is too large to store") rather than with a mismatch the
    # operator would try to fix by pasting more carefully.
    loc_problem = _bound_problem(stored_loc, "the stored finding's loc", MAX_LOC_BYTES)
    if loc_problem is not None:
        _refuse(loc_problem)
    if stored_loc != args.expect_loc:
        _refuse(
            f"--expect-loc does not match the loc of the finding at index "
            f"{args.finding_index}. Refusing rather than record a refusal against "
            f"a finding you did not read -- one block routinely carries several "
            f"findings, so an index off by one selects a real but different claim. "
            f"Read them: {_print_digests_command(seg, args)}"
        )

    stored_issue = finding.get("issue")
    if not isinstance(stored_issue, str):
        _refuse(
            f"the stored finding at index {args.finding_index} has no string issue, "
            f"so no digest can be computed for it."
        )
    issue_problem = _issue_digest_problem(stored_issue)
    if issue_problem is not None:
        _refuse(issue_problem)
    actual_digest = _finding_issue_digest(stored_issue)
    if actual_digest != expect_digest:
        _refuse(
            f"--expect-issue-digest does not match the finding at index "
            f"{args.finding_index}: its issue text digests to {actual_digest}. "
            f"Either the review changed since you read it, or you copied a "
            f"different finding's digest. Read them: {_print_digests_command(seg, args)}"
        )

    claim_record_mod, import_err = _import_claim_record(dirs["scripts_dir"])
    if import_err is not None:
        _refuse(import_err)

    rpath = refusals_path(seg, dirs["segments_dir"])
    lock_fd, lock_problem = acquire_refusal_lock(seg, dirs["segments_dir"])
    if lock_problem is not None:
        _refuse(lock_problem)
    try:
        existing, read_err = read_existing_refusals(rpath, seg)
        if read_err is not None:
            _refuse(read_err)

        # IDEMPOTENCE ON THE FULL FOUR, not on the loc alone. An operator
        # re-running the command -- because the first run reported a
        # durability problem, or because they lost the terminal -- must not
        # grow the file; but two genuinely different findings at one loc, or
        # the same claim refused again at a later round, are distinct records
        # and both belong. The reason is deliberately NOT part of the key: a
        # reworded reason for the same refusal is the same refusal, and
        # keying on it would let a typo double the entry.
        #
        # WHY finding_index IS IN THE KEY, when the record does not otherwise
        # need it. (loc, round_label, issue_digest) is not an identity:
        # review.schema.json puts no uniqueness constraint on findings, so ONE
        # review may legitimately carry two entries with the same loc AND the
        # same issue text, differing only in severity or suggest. Those digest
        # identically, so the second refusal came back `already_recorded`,
        # reporting success while its own reason was never stored -- the exact
        # failure #764 is about, recreated inside the tool that exists to
        # prevent it.
        #
        # The index rather than a wider digest, and that is the whole of the
        # choice. Folding severity/suggest into `issue_digest` would fix this
        # collision and break the field's real job: the next round's reviewer
        # rewords a `suggest` freely while making the identical claim, and an
        # issue-only digest is what lets a re-raise be RECOGNISED as one. So
        # the two jobs get two values -- issue_digest stays issue-only for the
        # consumer, and the index disambiguates for the writer. It is stable
        # for exactly as long as it needs to be: a record is always written
        # against the review the same invocation just read, and `round_label`
        # is already in the key, so an index from another round can never be
        # compared against this one's.
        for record in existing:
            if (record["loc"], record["finding_index"], record["round_label"],
                    record["issue_digest"]) == (
                stored_loc, str(args.finding_index), round_label, actual_digest
            ):
                # THE RENEWAL, and it is not belt-and-braces. This branch is
                # exactly where an operator lands after write_refusals_file()
                # reported that the directory sync failed and told them to fix
                # the filesystem and re-run: the record is already on disk, so
                # the re-run matches here and would otherwise exit 0 -- which
                # this script's own docstring defines as "the record is on disk
                # AND durable". The file's own fsync does not make its
                # DIRECTORY ENTRY durable (claim_record.py's fsync_directory()
                # says why), so without this the remedy the failure message
                # names would hand back a success that means less than the
                # first failure did.
                sync_problem = claim_record_mod.fsync_directory(rpath.parent)
                if sync_problem is not None:
                    _refuse(
                        f"this refusal is already recorded at {rpath}, but the "
                        f"directory entry could NOT be made durable "
                        f"({sync_problem}), so a crash can still lose it. The "
                        f"record is KEPT -- it authorizes nothing. Fix the "
                        f"directory (permissions, filesystem) and re-run."
                    )
                _accept({
                    "success": True,
                    "seg": seg,
                    "path": str(rpath),
                    "already_recorded": True,
                    "loc": stored_loc,
                    "round_label": round_label,
                    "issue_digest": actual_digest,
                    "refusals": len(existing),
                })

        if len(existing) >= MAX_REFUSALS:
            _refuse(
                f"segment {seg!r} already carries {len(existing)} refusal records, "
                f"this file's cap of {MAX_REFUSALS}. Refusing to append: every "
                f"record here is spliced into the next fix turn's prompt, and an "
                f"unbounded array is an unbounded prompt. A segment at this cap is "
                f"not converging -- take it up with reject_review.py or the cap, "
                f"not with one more record."
            )

        payload = {
            "seg": seg,
            "refusals": existing + [{
                "loc": stored_loc,
                "finding_index": str(args.finding_index),
                "round_label": round_label,
                "issue_digest": actual_digest,
                "reason": reason,
                "refused_at": now_iso8601(),
            }],
        }
        for record in payload["refusals"]:
            # THE READER'S OWN GATE, run against what is about to be written.
            # It subsumes the key-set check this loop used to make and closes
            # the class rather than one field: whatever the writer assembles,
            # it cannot produce a file read_existing_refusals() would call
            # foreign on the next invocation -- the failure mode that reports
            # success now and silently loses every later refusal. Nothing is
            # written when it fires.
            problem = _record_problem(record)
            if problem is not None:
                _refuse(
                    f"internal: a refusal record this run assembled is not one "
                    f"this script's own reader would accept ({problem}). Nothing "
                    f"was written."
                )

        write_problem = write_refusals_file(rpath, payload, claim_record_mod)
        if write_problem is not None:
            _refuse(write_problem)

        _accept({
            "success": True,
            "seg": seg,
            "path": str(rpath),
            "already_recorded": False,
            "loc": stored_loc,
            "round_label": round_label,
            "issue_digest": actual_digest,
            "refusals": len(payload["refusals"]),
        })
    finally:
        # Released by closing the descriptor. The lock FILE stays; it carries
        # no state and its presence means nothing.
        try:
            os.close(lock_fd)
        except OSError:
            pass


if __name__ == "__main__":
    main()
