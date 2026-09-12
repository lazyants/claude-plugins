#!/usr/bin/env python3
"""stale_records_report.py -- report-only pass: which names[]/notes[] records
a class correction has left stale (#931).

## What this is for

An operator hand-corrects a CLASS of renderings across many already-converged
drafts (e.g. a town name misread throughout a book) by editing
`segments/{seg}.draft.json`'s `blocks`; nothing re-runs the translator or the
reviewer for that correction. Left behind are the SAME segment's `names[]`
and `notes[]` entries, written when the old rendering was current and now
describing a form the blocks no longer carry. `final_audit.py`'s
glossary-diff WARN surfaces this a handful of segments at a time, across
review rounds; this pass lists every one, once, so a human reads the whole
thing in one pass.

This is a REPORT, not a gate: it never exits 1 because it found something,
and it never writes to a draft, a ledger fragment, canon.json, or any other
hashed surface. Fixing a record it flags is the existing #438 re-review route
(`--from-converged` / hand-edit + re-review) -- see SKILL.md's paragraph on
this pass. Nothing here moves a cache key.

## Population

Every ledger fragment `runs/ledger.d/{seg}.json` whose own on-disk `status`
is `"converged"` -- selection by status only, exactly as
`final_audit.load_converged_fragments` selects. The fragment's
`reviewed_draft_sha1` is deliberately NOT compared: the operator has just
hand-edited these drafts, so a mismatch against the review that approved the
OLD text is the expected state here, never a reason to exclude a segment.
A draft on disk with no converged fragment is ignored and counted
(`drafts_not_selected`), never treated as part of the population.

BOTH modes compute this population fresh, from the SAME function, each time
they run -- `--build` never reads it out of `prep.json`. A segment that
converges after `--prep` ran is part of `--build`'s population too, and needs
its own verdict file like any other (`verdict_missing` otherwise); nothing
here caches a membership list.

The prose corpus a record is checked against is the segment's `blocks` only
(string values, NFC-normalised, joined with `"\n"`) -- footnotes and verses
are out of scope on every side (the names predicate, the judge, and the
`--build` gate).

## Two modes, one human step between them

    python3 stale_records_report.py --prep
    #   -> stale_records/prep.json          (per-segment draft_sha1 -- the
    #                                         sha the judge copies verbatim)
    #   -> stale_records/names_report.json  (the names[] predicate's verdict,
    #                                         for the operator to read early)
    #   ... operator dispatches literary-translator:stale-notes-judge once per
    #       selected segment (per stale_notes_TASK.md), and writes each
    #       returned JSON verdict to stale_records/verdicts/{seg}.json
    python3 stale_records_report.py --build
    #   -> stale_records/stale_records_report.json
    #   -> stale_records/STALE_RECORDS.md

`--build` NEVER reads `prep.json` or `names_report.json` -- both are
`--prep`-only artifacts (the judge's input and the operator's early read).
It re-derives everything itself: the converged population, each draft's
content hash (to bind against a verdict's `draft_sha1`), and every `names[]`
row (by the SAME `evaluate_name_entry` predicate, over the SAME draft, read
once). Two validators that re-checked those files' SHAPE were tried and
removed (#931 round 2): checking a document's shape is a weaker guarantee
than not depending on the document at all, and a well-shaped but stale or
substituted value passes a shape check every time.

Exit 0 on success (whether or not anything was flagged -- this is a report,
not a gate), 1 when `--build` refuses a verdict, 2 on a usage or precondition
failure. One JSON line on stdout, human detail on stderr, per house style.
On exit 2, stdout carries NOTHING (nothing can be mistaken for a
schema-conforming result); on exit 1, stdout carries one
`{"success": false, "reason": ..., "error": ...}` line and nothing is
written.

## The names[] predicate (measured, not argued -- see CHANGELOG 1.132.0)

A `names[]` entry's target form (`target_form`, or `canonical_target_form` --
the same two-convention read as `final_audit._name_entry_forms`) is NFC
normalised; its capitalised letter-runs of length >= 2
(`re.findall(r"[^\\W\\d_]+", target)`, kept where `token[0].isupper() and
len(token) >= 2`) are each checked for WHOLE-TOKEN membership in the set of
tokens the NFC-joined blocks text tokenizes to -- never a substring check.
A substring check was measured to miss a retired form that is a strict
PREFIX of its replacement ("Odes" -> "Odessa" is a substring of the
corrected text, so a substring check reports it clean -- exactly the stale
row this pass exists to catch: 72/85 recall on the measured stale set, vs
84/85 for whole-token membership). Any qualifying token absent from the
block token set flags the row, naming which tokens are missing. A target
with no qualifying token (an all-lowercase gloss such as "which") is
`unverifiable`, never silently treated as clean. A row missing
`source_form` or missing both target-form fields is `names_unreadable`.

## `notes[]` free prose

Free text cannot be checked by a string predicate without either missing
everything (a naive "any capitalised token absent" check flags 91/92 of a
known-stale set AND 3560/3623 of a clean book) or missing almost everything
useful. `--prep` therefore only prepares; a per-segment LLM judge
(`literary-translator:stale-notes-judge`, dispatched by the operator against
`stale_notes_TASK.md`) reads each segment's `notes[]` beside its `blocks` and
returns a verdict per note: `stale` (describes a rendering, form or decision
the blocks no longer carry), `provenance` (names the old form while stating
the current one), or `current`. `--build` binds every verdict to the
draft's CONTENT hash, recomputed fresh from the draft on disk right now
(`draft_sha1` -- `draft_sha1.py` canonicalises key order/whitespace and
excludes `dispatch_token`, so this is never a check on the file's raw bytes,
and never a comparison against a value `--prep` recorded earlier) and
refuses a verdict that contradicts itself or the record rather than
trusting it blindly -- see the named refusals below. It never judges whether
a note is *right*, only whether it is still consistent with the prose beside
it.

`stale_form_present_in_blocks` -- a `stale` verdict whose quoted form is
self-contradictory because the blocks still carry it -- is WHOLE-TOKEN,
never substring, for the SAME reason the names[] predicate is: a quote that
is a strict PREFIX of a corrected form ("Odes" quoted as stale, blocks now
say "Odessa") is a substring of the corrected text, and a substring check
would wrongly refuse a legitimate `stale` verdict over it. `quoted_form`'s
tokens (`re.findall(r"[^\\W\\d_]+", ...)`, NFC both sides) must occur as a
CONTIGUOUS run in the blocks' own token sequence -- a one-token quote is
exact membership, a multi-token quote must appear together and in order,
never merely all present somewhere in the segment. A `quoted_form` with no
letter token at all (pure punctuation) can be judged by neither this check
nor `quoted_form_not_in_note`, so it is refused earlier, as `verdict_shape`.
`quoted_form_not_in_note` stays a substring check: it verifies the quote
against the NOTE, the judge's own quote source and free text, not against
tokenized blocks.

## Named refusals

Exit 2 (precondition/corruption -- never reachable by ordinary operator
error, since these mean something is broken rather than "nothing to
report"), shared by BOTH modes: both select the converged population from
`runs/ledger.d/` and read every selected segment's draft, fresh, each time
they run.

  - `ledger_d_unreadable`      -- runs/ledger.d/ exists but could not be listed.
  - `fragment_unreadable`      -- a ledger fragment file could not be read/parsed.
  - `fragment_not_object`      -- a ledger fragment's JSON value is not an object.
  - `fragment_status_invalid`  -- a ledger fragment has no string `status` field.
  - `no_converged_segments`    -- zero fragments have `status == "converged"`.
  - `draft_missing_for_converged` -- a converged segment has no draft on disk.
  - `draft_unreadable`         -- a selected segment's draft is unreadable/not
                                   a JSON object.
  - `draft_shape`              -- a selected segment's draft has a `names`
                                   or `notes` field that is not an array, a
                                   non-string `notes[]` entry, a `blocks`
                                   field that is not an object, or a
                                   non-string `blocks` VALUE -- refused,
                                   never coerced to an empty container or
                                   silently filtered (a corrupt draft must
                                   not read as "nothing to report").
  - `draft_changed_during_read` -- the draft's content hash taken before
                                   parsing it differs from the hash taken
                                   after -- it was replaced mid-read; never
                                   raced deterministically, so this cannot be
                                   pinned by a normal test, only by the code
                                   path existing (see `load_segment_draft`).
  - `sibling_import_failed`    -- draft_sha1.py could not be imported.
  - `verdicts_dir_unreadable`  -- `--build` only: stale_records/verdicts/
                                   exists but could not be listed.
  - `report_target_not_a_file` -- either half of either mode's output pair
                                   exists but is not a regular file (a
                                   directory, or a symlink checked by
                                   itself, never by what it points to) --
                                   refused before anything is staged.
  - `report_write_failed`      -- an OSError or UnicodeError (a lone
                                   surrogate codepoint a verdict string can
                                   carry, unencodable as UTF-8) while
                                   staging or replacing either mode's pair
                                   of output files.

`--build` additionally, exit 1, one refusal per run, nothing written,
naming the offending verdict file and segment:
    `verdict_unreadable`, `verdict_shape`, `verdict_extra_keys`,
    `verdict_seg_unknown`, `verdict_file_seg_mismatch`, `verdict_stale_draft`
    (the verdict's `draft_sha1` does not match the CURRENT on-disk draft's
    content hash -- never a comparison against a value `--prep` recorded),
    `verdict_coverage`, `verdict_needs_reason`, `verdict_string_multiline`,
    `quoted_form_not_in_note`, `stale_form_present_in_blocks`,
    `verdict_missing` (failure envelope carries `missing_segments`).

A refusal never leaves a partial write: every gate over every verdict runs
before `--build` writes anything, and a pre-existing report from an earlier
successful `--build` is left byte-identical on a refusal.

See `references/stale-records.md` (if present) and SKILL.md's paragraph on
this pass for the operator recipe.
"""

import argparse
import json
import os
import re
import stat
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

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
import importlib.util as _importlib_util

_JSON_STDOUT_PATH = Path(__file__).absolute().parent / "json_stdout.py"
try:
    _json_stdout_spec = _importlib_util.spec_from_file_location(
        "json_stdout", _JSON_STDOUT_PATH
    )
    if _json_stdout_spec is None or _json_stdout_spec.loader is None:
        raise ImportError(f"no loader for {_JSON_STDOUT_PATH}")
    _json_stdout = _importlib_util.module_from_spec(_json_stdout_spec)
    # OSError, not ImportError alone: spec_from_file_location() happily builds a
    # spec for a file that is not there, and it is exec_module() that raises
    # FileNotFoundError when it opens the source.
    _json_stdout_spec.loader.exec_module(_json_stdout)
except (ImportError, OSError) as _json_stdout_exc:  # pragma: no cover - staging error path
    # print()+sys.exit(2), never sys.exit("<message>") -- the latter prints
    # the message to stderr but exits 1, which is the wrong code for a
    # precondition failure under this script's own exit contract (0/1/2).
    print(
        f"stale_records_report.py: cannot load json_stdout.py from {_JSON_STDOUT_PATH} "
        f"({_json_stdout_exc}).\n"
        "json_stdout.py must be installed alongside stale_records_report.py under "
        "${durable_root}/scripts/ -- Step 0a's copy pass places it there.",
        file=sys.stderr,
    )
    sys.exit(2)

dumps_line = _json_stdout.dumps_line

# ---------------------------------------------------------------------------
# Self-anchoring: this script lives at {durable_root}/scripts/<name>.py when
# copied, and at {plugin_root}/assets/scripts/<name>.py in the plugin tree.
# Never cwd, never a --durable-root flag (house style).
# ---------------------------------------------------------------------------
SCRIPTS_DIR = Path(__file__).resolve().parent
DURABLE_ROOT = SCRIPTS_DIR.parent
SEGMENTS_DIR = DURABLE_ROOT / "segments"
LEDGER_D = DURABLE_ROOT / "runs" / "ledger.d"
OUT_DIR = DURABLE_ROOT / "stale_records"
VERDICTS_DIR = OUT_DIR / "verdicts"

# The full str.splitlines() line-boundary codepoint set -- duplicated
# byte-identically from render_obsidian.py's `_MENTIONS_LINE_BREAK_CHARS` /
# skeptic_report.py's `_LINE_BREAK_CHARS`, per this project's "no shared util
# module between self-contained scripts" convention.
_LINE_BREAK_CHARS = frozenset("\n\r\v\f\x1c\x1d\x1e\x85" + chr(0x2028) + chr(0x2029))

# The names[] predicate's token pattern, exactly as measured (see the module
# docstring): a capitalised letter-run is the only signal cheap enough not to
# drown in "not_a_name" glosses, and the ONLY one measured to recall 84/85 of
# a known-stale set while flagging 0.3% (14/4675) of a clean book.
_TOKEN_RE = re.compile(r"[^\W\d_]+")

# The per-segment names[] tallies; the same keys name the totals in every
# emitted document, so one loop sums them.
_NAMES_COUNT_KEYS = ("names_total", "names_flagged", "names_unverifiable", "names_unreadable")
_NOTES_COUNT_KEYS = ("notes_total", "notes_stale", "notes_provenance")


class RegistryError(Exception):
    """A named, reportable failure. `reason` is the machine-readable slug
    that reaches stdout's JSON line on a code=1 refusal (never emitted at
    all on code=2, see main()); `extra` is folded into that same envelope
    (used for `verdict_missing`'s `missing_segments`)."""

    def __init__(self, reason: str, message: str, code: int = 1, extra: "dict | None" = None):
        super().__init__(message)
        self.reason = reason
        self.message = message
        self.code = code
        self.extra = extra or {}


def nfc(s) -> str:
    """NFC-normalize both a target/quoted form and the prose it is checked
    against: composed and decomposed spellings are the same text to a
    reader, and comparing them raw reports a present form as absent."""
    return unicodedata.normalize("NFC", s or "")


def inline_md(s) -> str:
    """Fold every str.splitlines() boundary character to a single space.
    Applied to EVERY value interpolated into a Markdown line in
    STALE_RECORDS.md: draft-sourced strings (a names[] form, a notes[] text)
    are schema-unconstrained free text this script does not gate, so a
    newline inside one could open a Markdown heading the generator never
    wrote. (Verdict-authored `quoted_form`/`reason` are additionally REFUSED
    when multiline, by `verdict_string_multiline` -- this renderer is the
    second wall, not a substitute.)"""
    s = "" if s is None else str(s)
    for ch in _LINE_BREAK_CHARS:
        s = s.replace(ch, " ")
    return s


def _json_text(doc) -> str:
    return json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _refuse_if_not_regular_file(path: Path) -> None:
    """Fatal `report_target_not_a_file` when `path` EXISTS and is not a
    regular file. `os.lstat`, never `os.stat`: a SYMLINK at `path` is
    refused by what it IS, not what it points to -- the same no-follow
    discipline the json_stdout loader above uses (`Path.absolute()` rather
    than `.resolve()`). This runs before `atomic_write_pair` stages
    anything: a directory or symlink target is the class of failure that
    left the ROLLBACK itself unable to recover (restoring `path_a` from
    its `.prev` copy is itself an `os.replace`, which fails the same way
    against a non-regular target) -- closed at the entrance rather than
    patched in the rollback, so the rollback only ever deals with regular
    files."""
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return
    if not stat.S_ISREG(st.st_mode):
        raise RegistryError(
            "report_target_not_a_file",
            f"{path} exists but is not a regular file (a directory or a symlink) -- "
            f"refusing to write through it",
            code=2,
        )


def atomic_write_pair(path_a: Path, text_a: str, path_b: Path, text_b: str) -> None:
    """Stage BOTH outputs as temp files, then replace both -- with a
    rollback if the SECOND replace fails after the first has already
    published. Both modes always emit a pair together; two independent
    atomic writes could leave a NEW file beside a STALE one if the second
    write failed after the first had replaced its target.

    Sequence: refuse (`report_target_not_a_file`) if EITHER target exists
    and is not a regular file, before anything is staged. Then write both
    temp files; if `path_a` already exists, move it aside to a `.prev`
    sibling; replace `path_a`; replace `path_b`. If replacing `path_b`
    fails, `path_a` is put back to exactly what it was before this call:
    restored from its `.prev` copy (a second `os.replace`, back) when
    there WAS a prior file, or removed (`os.unlink`, ENOENT ignored) when
    there was NONE -- a fresh publish over a name that did not exist yet
    must not be left behind just because it happened to succeed before the
    second write failed. That restore can itself fail (rare, since the
    entrance guard above already excludes the type-mismatch case that used
    to cause it); when it does, the failure is NOT swallowed -- it is
    appended to the `report_write_failed` message rather than silently
    dropped, so a mismatched pair is at least reported, never hidden. On
    success the `.prev` copy (if any) is removed. Any OSError or
    UnicodeError anywhere in this sequence is the same fatal
    `report_write_failed` refusal, never a raw traceback -- UnicodeError
    because `write_text(encoding="utf-8")` raises `UnicodeEncodeError` (not
    an `OSError`) on a lone surrogate codepoint, which a verdict's
    `reason`/`quoted_form` can carry (JSON permits `\\ud800` unpaired;
    every content gate above this checks CONTENT, not encodability).

    Accepted: the staged `.tmp.<pid>` / `.prev.<pid>` names are predictable
    and this function follows them through a pre-planted symlink with no
    `O_EXCL` / `O_NOFOLLOW` guard -- planting one requires write access
    inside the operator's own durable root, i.e. the same trust boundary as
    running this script at all, so there is no attacker this would
    exclude."""
    _refuse_if_not_regular_file(path_a)
    _refuse_if_not_regular_file(path_b)

    tmp_a = path_a.parent / f".{path_a.name}.tmp.{os.getpid()}"
    tmp_b = path_b.parent / f".{path_b.name}.tmp.{os.getpid()}"
    prev_a = path_a.parent / f".{path_a.name}.prev.{os.getpid()}"
    moved_a_aside = False
    published_a = False
    try:
        path_a.parent.mkdir(parents=True, exist_ok=True)
        path_b.parent.mkdir(parents=True, exist_ok=True)
        tmp_a.write_text(text_a, encoding="utf-8")
        tmp_b.write_text(text_b, encoding="utf-8")
        if path_a.exists():
            os.replace(path_a, prev_a)
            moved_a_aside = True
        os.replace(tmp_a, path_a)
        published_a = True
        os.replace(tmp_b, path_b)
    except (OSError, UnicodeError) as exc:
        restore_detail = ""
        if moved_a_aside:
            # path_a is either missing (the replace-in above failed) or
            # holds the NEW content (it succeeded but path_b's then
            # failed) -- either way the ORIGINAL is in prev_a and must
            # come back.
            try:
                os.replace(prev_a, path_a)
            except OSError as restore_exc:
                restore_detail = (
                    f"; additionally could not restore {path_a.name} from its "
                    f"backup {prev_a.name}: {restore_exc}"
                )
        elif published_a:
            # No prior file existed, but the fresh publish went through
            # before path_b's write failed -- undo it rather than leave a
            # file behind that this call never should have created.
            try:
                path_a.unlink()
            except OSError as restore_exc:
                restore_detail = (
                    f"; additionally could not remove the freshly published "
                    f"{path_a.name}: {restore_exc}"
                )
        for tmp in (tmp_a, tmp_b):
            try:
                tmp.unlink()
            except OSError:
                pass
        raise RegistryError(
            "report_write_failed",
            f"could not write {path_a.name}/{path_b.name}: {exc}{restore_detail}",
            code=2,
        )
    if moved_a_aside:
        try:
            prev_a.unlink()
        except OSError:
            pass


def import_draft_sha1():
    """Import draft_sha1.py -- the sole sha1 authority for draft files --
    from this script's own directory. Exactly `person_registry.
    import_siblings()`'s shape: lazy, path-scoped, and an ImportError names
    the path rather than leaving a bare traceback. Never a new copy of the
    hashing algorithm (seven copies already drift as a group)."""
    sys.path.insert(0, str(SCRIPTS_DIR))
    try:
        import draft_sha1
    except ImportError as exc:
        raise RegistryError(
            "sibling_import_failed",
            f"could not import draft_sha1.py (the sole sha1 authority for draft "
            f"files) from {SCRIPTS_DIR}: {exc}",
            code=2,
        )
    finally:
        try:
            sys.path.remove(str(SCRIPTS_DIR))
        except ValueError:
            pass
    return draft_sha1


# ---------------------------------------------------------------------------
# Ledger fragment reading -- ledger_merge._read_fragments' errno split,
# copied. An absent/plain-file ledger.d means "nothing has converged yet",
# not an error; any OTHER OSError means "could not look", which must refuse
# rather than silently report emptiness.
# ---------------------------------------------------------------------------

def read_ledger_fragments() -> dict:
    try:
        entries = sorted(LEDGER_D.iterdir())
    except (FileNotFoundError, NotADirectoryError):
        return {}
    except OSError as exc:
        raise RegistryError(
            "ledger_d_unreadable",
            f"the ledger fragment directory {LEDGER_D} exists but could not be "
            f"listed ({exc}) -- refusing to report it as empty",
            code=2,
        )
    fragments = {}
    for frag_path in entries:
        if not frag_path.name.endswith(".json"):
            continue
        try:
            fragments[frag_path.stem] = json.loads(frag_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, RecursionError) as exc:
            # ValueError, not json.JSONDecodeError alone: it also catches the
            # UnicodeDecodeError read_text(encoding="utf-8") raises on a
            # fragment that is not valid UTF-8. RecursionError is a
            # RuntimeError, not a ValueError, and is what deeply nested JSON
            # (e.g. a million-deep "[[[...") raises out of json.loads()
            # instead of a decode error.
            raise RegistryError(
                "fragment_unreadable",
                f"ledger fragment {frag_path.name} could not be read/parsed: {exc}",
                code=2,
            )
    return fragments


def select_converged_fragments() -> dict:
    """{seg: fragment} for every ledger fragment whose own on-disk `status`
    is "converged" -- the population selector BOTH modes call, computed
    fresh on every call, never cached across a --prep/--build pair. Fatal
    (exit 2) on any corruption: a non-object fragment or one without a
    string `status` means something is broken, never "not converged"; zero
    converged fragments means nothing to report on."""
    converged = {}
    for seg, record in read_ledger_fragments().items():
        if not isinstance(record, dict):
            raise RegistryError(
                "fragment_not_object",
                f"ledger fragment for segment {seg!r} does not contain a JSON object",
                code=2,
            )
        status = record.get("status")
        if not isinstance(status, str):
            raise RegistryError(
                "fragment_status_invalid",
                f"ledger fragment for segment {seg!r} has no string 'status' field",
                code=2,
            )
        if status == "converged":
            converged[seg] = record
    if not converged:
        raise RegistryError(
            "no_converged_segments",
            f"no ledger fragment under {LEDGER_D} has status \"converged\" -- "
            f"nothing to report on",
            code=2,
        )
    return converged


def load_segment_draft(seg: str, draft_sha1_mod) -> dict:
    """Read one selected segment's draft once, for either mode:
    {"draft_sha1", "notes_texts", "names_list", "blocks_nfc", "block_tokens"}.

    The read is bracketed by the content hash: `hash -> read+parse -> hash
    again`, and the two must agree. `draft_sha1.draft_content_sha1` opens
    the file itself, so hashing and then `read_text()`-ing are two
    independent reads of a file this process does not lock -- a draft
    replaced between them (a hand-edit racing this scan) would otherwise
    bind the FIRST read's hash to the SECOND read's notes and blocks.
    `draft_sha1.py` is a `cache_key.py` `PLUGIN_BUNDLE_MEMBER`, so its
    surface is deliberately not widened with a doc-returning variant;
    bracketing with the existing hash-only entry point touches nothing
    else. A draft replaced in between is refused
    (`draft_changed_during_read`, fatal), never merged."""
    draft_path = SEGMENTS_DIR / f"{seg}.draft.json"
    if not draft_path.is_file():
        raise RegistryError(
            "draft_missing_for_converged",
            f"segment {seg!r} has a converged ledger fragment but no draft "
            f"at {draft_path}",
            code=2,
        )

    def content_sha(verb: str) -> str:
        try:
            return draft_sha1_mod.draft_content_sha1(draft_path)
        except (OSError, ValueError, RecursionError) as exc:
            # RecursionError: draft_content_sha1 parses the draft as JSON
            # too, and a deeply nested draft raises it there, not a
            # ValueError.
            raise RegistryError(
                "draft_unreadable",
                f"could not {verb} draft_sha1 for segment {seg!r} at {draft_path}: {exc}",
                code=2,
            )

    sha_before = content_sha("compute")
    try:
        draft_doc = json.loads(draft_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError) as exc:
        # ValueError also catches UnicodeDecodeError; RecursionError is a
        # RuntimeError a million-deep "[[[..." raises instead.
        raise RegistryError(
            "draft_unreadable",
            f"could not read draft for segment {seg!r} at {draft_path}: {exc}",
            code=2,
        )
    if not isinstance(draft_doc, dict):
        raise RegistryError(
            "draft_unreadable",
            f"draft for segment {seg!r} at {draft_path} is not a JSON object",
            code=2,
        )
    sha_after = content_sha("recompute")
    if sha_after != sha_before:
        raise RegistryError(
            "draft_changed_during_read",
            f"segment {seg!r}'s draft at {draft_path} changed between the hash taken "
            f"before parsing it and the hash taken after ({sha_before} -> {sha_after}) "
            f"-- refused rather than binding the first hash to the second read's "
            f"notes/blocks; re-run once the draft is no longer being edited",
            code=2,
        )
    # Malformed `names`/`notes`/`blocks` is refused, never coerced to an
    # empty container: silently treating a corrupt draft as "nothing to
    # report" is exactly the failure mode this whole pass exists to avoid
    # for names[]/notes[] entries themselves, and reads no differently for
    # the containers that hold them.
    names_list = draft_doc.get("names")
    if not isinstance(names_list, list):
        raise RegistryError(
            "draft_shape",
            f"segment {seg!r}'s draft at {draft_path} has a 'names' field that is "
            f"not an array",
            code=2,
        )
    notes_texts = draft_doc.get("notes")
    if not isinstance(notes_texts, list):
        raise RegistryError(
            "draft_shape",
            f"segment {seg!r}'s draft at {draft_path} has a 'notes' field that is "
            f"not an array",
            code=2,
        )
    for i, note in enumerate(notes_texts):
        if not isinstance(note, str):
            raise RegistryError(
                "draft_shape",
                f"segment {seg!r}'s draft at {draft_path} has a non-string "
                f"notes[{i}] entry",
                code=2,
            )
    blocks = draft_doc.get("blocks")
    if not isinstance(blocks, dict):
        raise RegistryError(
            "draft_shape",
            f"segment {seg!r}'s draft at {draft_path} has a 'blocks' field that is "
            f"not an object",
            code=2,
        )
    for block_id, value in blocks.items():
        if not isinstance(value, str):
            raise RegistryError(
                "draft_shape",
                f"segment {seg!r}'s draft at {draft_path} has a non-string block "
                f"{block_id!r}",
                code=2,
            )
    blocks_nfc = blocks_corpus_nfc(draft_doc)
    return {
        "draft_sha1": sha_after,
        "notes_texts": notes_texts,
        "names_list": names_list,
        "blocks_nfc": blocks_nfc,
        "block_tokens": block_token_set(blocks_nfc),
        "block_token_list": block_token_list(blocks_nfc),
    }


# ---------------------------------------------------------------------------
# The names[] predicate.
# ---------------------------------------------------------------------------

def classify_name_entry(entry) -> tuple:
    """Returns (status, row): status "unreadable"/"unverifiable" with row
    None, or "candidate" with the row's fields plus `_qualifying_tokens`
    (membership still to be checked by `evaluate_name_entry`, which alone
    has the blocks corpus)."""
    if not isinstance(entry, dict):
        return "unreadable", None
    source_form = entry.get("source_form")
    if not isinstance(source_form, str) or not source_form:
        return "unreadable", None
    target = entry.get("target_form")
    if not isinstance(target, str) or not target:
        target = entry.get("canonical_target_form")
    if not isinstance(target, str) or not target:
        return "unreadable", None
    qualifying = [t for t in _TOKEN_RE.findall(nfc(target)) if t[0].isupper() and len(t) >= 2]
    if not qualifying:
        return "unverifiable", None
    return "candidate", {
        "source_form": source_form,
        "canonical_target_form": target,
        "basis": entry.get("basis"),
        "confidence": entry.get("confidence"),
        "_qualifying_tokens": qualifying,
    }


def evaluate_name_entry(entry, block_tokens: set, index: int):
    """Returns (status, row): "flagged" rows carry `missing_tokens`;
    "clean"/"unverifiable"/"unreadable" carry None.

    `block_tokens` is a SET of whole tokens (see `block_token_set`), never
    the raw blocks text: a qualifying token is missing when it is not a
    member, WHOLE-TOKEN, never substring (the "Odes" -> "Odessa" prefix case
    in the module docstring). `_qualifying_tokens` are already single
    `[^\\W\\d_]+` runs, so this is exact set membership, not another
    tokenization pass."""
    status, candidate = classify_name_entry(entry)
    if status != "candidate":
        return status, None
    missing = [t for t in candidate.pop("_qualifying_tokens") if t not in block_tokens]
    if not missing:
        return "clean", None
    return "flagged", {"index": index, **candidate, "missing_tokens": missing}


def tally_names(names_list: list, block_tokens: set) -> tuple:
    """Runs `evaluate_name_entry` over one segment's names[] and returns
    ({names_total, names_flagged, names_unverifiable, names_unreadable},
    flagged_rows) -- the one predicate pass both modes report from."""
    counts = dict.fromkeys(_NAMES_COUNT_KEYS, 0)
    counts["names_total"] = len(names_list)
    rows = []
    for idx, entry in enumerate(names_list):
        status, row = evaluate_name_entry(entry, block_tokens, idx)
        if status == "flagged":
            rows.append(row)
        if status != "clean":
            counts[f"names_{status}"] += 1
    return counts, rows


def blocks_corpus_nfc(draft_doc: dict) -> str:
    """The segment's `blocks` values, NFC-normalised and `"\\n"`-joined --
    the ONLY corpus a names[]/notes[] record is checked against. Assumes
    `blocks` is an object of strings: `load_segment_draft`'s `draft_shape`
    gate refuses a draft that is not, before this is ever called, so this
    silently filtering to only the string-valued entries would otherwise
    hide exactly the corruption that gate exists to catch."""
    return nfc("\n".join(draft_doc["blocks"].values()))


def block_token_set(blocks_nfc: str) -> set:
    """The set of whole tokens `evaluate_name_entry` checks against,
    tokenized with the SAME pattern as the target side, so a qualifying
    token must match a whole token of the prose, never merely appear inside
    a longer one (an apostrophe splits a possessive: "Hirsch's" tokenizes to
    {"Hirsch", "s"}, so the bare token "Hirsch" is still a member)."""
    return set(_TOKEN_RE.findall(blocks_nfc))


def block_token_list(blocks_nfc: str) -> list:
    """The blocks' tokens IN ORDER, same pattern as `block_token_set` --
    kept separately because a SET alone cannot answer whether a MULTI-token
    quoted form's tokens occur together, in sequence (`_token_sequence_in_
    list`, used by `stale_form_present_in_blocks`); a set only answers
    membership per token, which would pass a form whose tokens are all
    present somewhere in the segment but never adjacent."""
    return _TOKEN_RE.findall(blocks_nfc)


def _token_sequence_in_list(needle: list, haystack: list) -> bool:
    """True iff `needle` (a non-empty token list, in order) occurs as a
    CONTIGUOUS run inside `haystack`. A one-token needle is exact
    membership."""
    n = len(needle)
    if n == 0 or n > len(haystack):
        return False
    return any(haystack[i:i + n] == needle for i in range(len(haystack) - n + 1))


def _sum_counts(rows: list, keys: tuple) -> dict:
    return {k: sum(r[k] for r in rows) for k in keys}


# ---------------------------------------------------------------------------
# --prep
# ---------------------------------------------------------------------------

def cmd_prep(args) -> dict:
    converged = select_converged_fragments()
    draft_sha1_mod = import_draft_sha1()

    draft_segs = set()
    if SEGMENTS_DIR.is_dir():
        draft_segs = {p.name[: -len(".draft.json")] for p in SEGMENTS_DIR.glob("*.draft.json")}

    prep_segments = []
    names_report_segments = []
    for seg in sorted(converged):
        data = load_segment_draft(seg, draft_sha1_mod)
        counts, rows = tally_names(data["names_list"], data["block_tokens"])
        prep_segments.append({
            "seg": seg,
            "draft_sha1": data["draft_sha1"],
            "notes_total": len(data["notes_texts"]),
            "names_total": counts["names_total"],
        })
        names_report_segments.append({"seg": seg, **counts, "rows": rows})

    generated_at = datetime.now(timezone.utc).isoformat()
    totals = _sum_counts(names_report_segments, _NAMES_COUNT_KEYS)
    prep_doc = {"schema_version": 1, "generated_at": generated_at, "segments": prep_segments}
    names_report_doc = {
        "schema_version": 1,
        "generated_at": generated_at,
        "segments": names_report_segments,
        "totals": totals,
    }

    prep_path = OUT_DIR / "prep.json"
    names_report_path = OUT_DIR / "names_report.json"
    atomic_write_pair(prep_path, _json_text(prep_doc), names_report_path, _json_text(names_report_doc))

    return {
        "success": True,
        "mode": "prep",
        "segments_selected": len(converged),
        "drafts_not_selected": len(draft_segs - set(converged)),
        **totals,
        "notes_total": sum(s["notes_total"] for s in prep_segments),
        "prep_path": str(prep_path),
        "names_report_path": str(names_report_path),
    }


# ---------------------------------------------------------------------------
# --build
# ---------------------------------------------------------------------------

_VERDICT_TOP_KEYS = {"schema_version", "seg", "draft_sha1", "notes"}
_VERDICT_NOTE_KEYS = {"index", "verdict", "quoted_form", "reason"}
_VERDICT_KINDS = ("stale", "current", "provenance")


def _validate_verdict_shape(vf: Path, doc) -> None:
    def shape(detail: str) -> RegistryError:
        return RegistryError("verdict_shape", f"verdict file {vf.name}{detail}")

    def extra_keys(detail: str) -> RegistryError:
        return RegistryError("verdict_extra_keys", f"verdict file {vf.name}{detail}")

    if not isinstance(doc, dict):
        raise shape(" must be a JSON object")
    extra_top = sorted(set(doc) - _VERDICT_TOP_KEYS)
    if extra_top:
        raise extra_keys(f" has unknown key(s): {extra_top}")
    missing_top = sorted(_VERDICT_TOP_KEYS - set(doc))
    if missing_top:
        raise shape(f" is missing key(s): {missing_top}")
    schema_version = doc["schema_version"]
    if not isinstance(schema_version, int) or isinstance(schema_version, bool) or schema_version != 1:
        raise shape("'s schema_version must be 1")
    if not isinstance(doc["seg"], str) or not doc["seg"]:
        raise shape("'s seg must be a non-empty string")
    if not isinstance(doc["draft_sha1"], str) or not doc["draft_sha1"]:
        raise shape("'s draft_sha1 must be a non-empty string")
    if not isinstance(doc["notes"], list):
        raise shape("'s notes must be an array")
    for item in doc["notes"]:
        if not isinstance(item, dict):
            raise shape(" has a notes[] entry that is not an object")
        extra_item = sorted(set(item) - _VERDICT_NOTE_KEYS)
        if extra_item:
            raise extra_keys(f" has a notes[] entry with unknown key(s): {extra_item}")
        missing_item = sorted(_VERDICT_NOTE_KEYS - set(item))
        if missing_item:
            raise shape(f" has a notes[] entry missing key(s): {missing_item}")
        if not isinstance(item["index"], int) or isinstance(item["index"], bool):
            raise shape(" has a notes[] entry whose index is not an integer")
        if item["verdict"] not in _VERDICT_KINDS:
            raise shape(f" has a notes[] entry with an unknown verdict {item['verdict']!r}")
        qf = item["quoted_form"]
        if qf is not None and not isinstance(qf, str):
            raise shape(" has a notes[] entry whose quoted_form is neither a string nor null")
        if qf == "":
            # The template says quoted_form is null when there is no quoted
            # form -- never an empty string. Refusing here keeps that a shape
            # violation, not a silent normalisation.
            raise shape(
                " has a notes[] entry whose quoted_form is an empty string -- use null "
                "when there is no quoted form"
            )
        if qf is not None and not _TOKEN_RE.findall(nfc(qf)):
            # A quoted form made entirely of punctuation carries no letter
            # token, so neither the whole-token stale_form_present_in_blocks
            # check nor a meaningful quote can be built from it -- refused
            # here rather than let it silently pass every gate that follows.
            raise shape(
                " has a notes[] entry whose quoted_form contains no letter token "
                "(pure punctuation) -- quoted_form must quote at least one word"
            )
        if not isinstance(item["reason"], str):
            raise shape(" has a notes[] entry whose reason is not a string")


def _gate_one_verdict_file(vf: Path, seg_data: dict) -> tuple:
    """Runs every per-file gate, in the plan's stated precedence, and
    returns (seg, notes) for the segment this file names -- the caller must
    use the returned `seg`, never re-open the file. Raises RegistryError
    (code=1) on the first violation.

    `seg_data` is `--build`'s own {seg: load_segment_draft(...)} map -- every
    draft was already read ONCE by the caller, so `verdict_stale_draft` is a
    single comparison against that already-fresh `draft_sha1`, never against
    a value `--prep` recorded (this function does not know `--prep` ran)."""
    filename_seg = vf.name[: -len(".json")]

    try:
        raw = vf.read_text(encoding="utf-8")
    except (OSError, ValueError) as exc:  # ValueError also catches UnicodeDecodeError
        raise RegistryError("verdict_unreadable", f"could not read verdict file {vf.name}: {exc}")
    try:
        doc = json.loads(raw)
    except (ValueError, RecursionError) as exc:
        # RecursionError is a RuntimeError, not a ValueError -- a
        # million-deep "[[[..." verdict file raises it instead.
        raise RegistryError("verdict_unreadable", f"verdict file {vf.name} is not valid JSON: {exc}")

    _validate_verdict_shape(vf, doc)

    seg = doc["seg"]
    if seg not in seg_data:
        raise RegistryError(
            "verdict_seg_unknown",
            f"verdict file {vf.name} names seg {seg!r}, which is not among the "
            f"currently converged segments",
        )
    if filename_seg != seg:
        raise RegistryError(
            "verdict_file_seg_mismatch",
            f"verdict file {vf.name}'s filename does not match its own seg field {seg!r}",
        )

    data = seg_data[seg]
    if doc["draft_sha1"] != data["draft_sha1"]:
        raise RegistryError(
            "verdict_stale_draft",
            f"verdict for {seg!r} was judged against draft_sha1 {doc['draft_sha1']}, "
            f"but the segment's draft on disk now hashes to {data['draft_sha1']} -- "
            f"the draft changed since this verdict was written; re-judge it",
        )

    notes_texts = data["notes_texts"]
    notes_list = doc["notes"]
    indices = sorted(item["index"] for item in notes_list)
    if indices != list(range(len(notes_texts))):
        raise RegistryError(
            "verdict_coverage",
            f"verdict for {seg!r} must list notes[].index exactly "
            f"0..{len(notes_texts) - 1} once each; got {indices}",
        )

    for item in notes_list:
        kind, reason, qf, idx = item["verdict"], item["reason"], item["quoted_form"], item["index"]
        if kind in ("stale", "provenance") and not reason.strip():
            raise RegistryError(
                "verdict_needs_reason",
                f"verdict for {seg!r} notes[{idx}] is {kind!r} but its reason is empty",
            )
        for field_name, val in (("quoted_form", qf), ("reason", reason)):
            if isinstance(val, str) and any(ch in _LINE_BREAK_CHARS for ch in val):
                raise RegistryError(
                    "verdict_string_multiline",
                    f"verdict for {seg!r} notes[{idx}]'s {field_name} contains "
                    f"a line-break character",
                )
        if qf is None:
            continue
        note_text = notes_texts[idx] if isinstance(notes_texts[idx], str) else ""
        if nfc(qf) not in nfc(note_text):
            raise RegistryError(
                "quoted_form_not_in_note",
                f"verdict for {seg!r} notes[{idx}] quotes {qf!r}, which is "
                f"not a substring of the note text",
            )
        if kind == "stale" and _token_sequence_in_list(
            _TOKEN_RE.findall(nfc(qf)), data["block_token_list"]
        ):
            raise RegistryError(
                "stale_form_present_in_blocks",
                f"verdict for {seg!r} notes[{idx}] marks {qf!r} stale, but its tokens "
                f"are still present, contiguous and in order, in the segment's blocks",
            )

    return seg, notes_list


def list_verdict_files() -> list:
    """Every stale_records/verdicts/*.json file -- the same errno split
    `read_ledger_fragments` uses. ENOENT/ENOTDIR means no verdicts written
    yet (every selected segment then reads `verdict_missing`, as today);
    any OTHER OSError -- an unlistable directory, e.g. mode 000 -- means
    "could not look", which must refuse (`verdicts_dir_unreadable`, fatal)
    rather than silently reporting zero verdicts. `Path.glob()` on a
    directory it cannot read returns `[]` with no exception, which is
    exactly the swallow this function exists to avoid: every segment would
    otherwise read as `verdict_missing`, indistinguishable from "the
    operator has not judged anything yet"."""
    try:
        entries = sorted(VERDICTS_DIR.iterdir())
    except (FileNotFoundError, NotADirectoryError):
        return []
    except OSError as exc:
        raise RegistryError(
            "verdicts_dir_unreadable",
            f"the verdicts directory {VERDICTS_DIR} exists but could not be "
            f"listed ({exc}) -- refusing to report it as empty",
            code=2,
        )
    return [p for p in entries if p.name.endswith(".json")]


def cmd_build(args) -> dict:
    converged = select_converged_fragments()
    draft_sha1_mod = import_draft_sha1()
    seg_data = {seg: load_segment_draft(seg, draft_sha1_mod) for seg in sorted(converged)}

    notes_by_seg = dict(_gate_one_verdict_file(vf, seg_data) for vf in list_verdict_files())

    missing_segs = sorted(set(seg_data) - set(notes_by_seg))
    if missing_segs:
        raise RegistryError(
            "verdict_missing",
            "no verdict file for segment(s): " + ", ".join(missing_segs),
            extra={"missing_segments": missing_segs},
        )

    report_segments = []
    for seg, data in seg_data.items():
        # Names rows are recomputed over the SAME draft this build already
        # read -- never read out of names_report.json, which --build does
        # not open.
        counts, names_rows = tally_names(data["names_list"], data["block_tokens"])
        notes_texts = data["notes_texts"]
        notes_rows = [
            {
                "index": item["index"],
                "verdict": item["verdict"],
                "quoted_form": item["quoted_form"],
                "reason": item["reason"],
                "note": notes_texts[item["index"]],
            }
            for item in sorted(notes_by_seg[seg], key=lambda it: it["index"])
            if item["verdict"] != "current"
        ]
        report_segments.append({
            "seg": seg,
            **counts,
            "names_rows": names_rows,
            "notes_total": len(notes_texts),
            "notes_stale": sum(r["verdict"] == "stale" for r in notes_rows),
            "notes_provenance": sum(r["verdict"] == "provenance" for r in notes_rows),
            "notes_rows": notes_rows,
        })

    totals = _sum_counts(report_segments, _NAMES_COUNT_KEYS + _NOTES_COUNT_KEYS)
    report_doc = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "segments_selected": len(seg_data),
        **totals,
        "segments": report_segments,
    }

    report_path = OUT_DIR / "stale_records_report.json"
    markdown_path = OUT_DIR / "STALE_RECORDS.md"
    atomic_write_pair(report_path, _json_text(report_doc), markdown_path, render_markdown(report_doc))

    return {
        "success": True,
        "mode": "build",
        "segments_selected": len(seg_data),
        **totals,
        "report_path": str(report_path),
        "markdown_path": str(markdown_path),
    }


# ---------------------------------------------------------------------------
# Markdown rendering. EVERY value interpolated into a line goes through
# inline_md() -- see that function's own docstring for why.
# ---------------------------------------------------------------------------

def _counts_line(c: dict) -> str:
    """`c` is any document carrying the names/notes count keys -- the report
    itself (totals) or one of its segment rows."""
    return (
        f"names: {c['names_total']} total, {c['names_flagged']} flagged, "
        f"{c['names_unverifiable']} unverifiable, {c['names_unreadable']} unreadable "
        f"· notes: {c['notes_total']} total, {c['notes_stale']} stale, "
        f"{c['notes_provenance']} provenance"
    )


def render_markdown(report_doc: dict) -> str:
    lines = [
        "# Stale Records Report",
        "",
        f"Generated: {inline_md(report_doc['generated_at'])}",
        f"Segments selected: {report_doc['segments_selected']}",
        _counts_line(report_doc),
        "",
    ]
    for seg_row in report_doc["segments"]:
        lines += [f"## {inline_md(seg_row['seg'])}", ""]
        # (clean) ONLY when there is truly nothing to look at -- an
        # unverifiable or unreadable count is never hidden behind it, even
        # though neither is itemised (only the aggregate count exists).
        if not any(seg_row[k] for k in ("names_flagged", "names_unverifiable", "names_unreadable",
                                        "notes_stale", "notes_provenance")):
            lines += ["(clean)", ""]
            continue
        lines += [_counts_line(seg_row), ""]
        if seg_row["names_rows"]:
            lines += ["### Names", ""]
            for nr in seg_row["names_rows"]:
                missing = ", ".join(inline_md(t) for t in nr["missing_tokens"])
                lines.append(
                    f"- names[{nr['index']}] ‹{inline_md(nr['source_form'])}› "
                    f"→ ‹{inline_md(nr['canonical_target_form'])}› "
                    f"— missing: {missing}"
                )
            lines.append("")
        if seg_row["notes_rows"]:
            lines += ["### Notes", ""]
            for nr in seg_row["notes_rows"]:
                quoted = f" «{inline_md(nr['quoted_form'])}»" if nr["quoted_form"] else ""
                lines.append(
                    f"- notes[{nr['index']}] {nr['verdict'].upper()}{quoted} — {inline_md(nr['reason'])}"
                )
                lines.append(f"  > {inline_md(nr['note'])}")
            lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="stale_records_report.py",
        description="#931: report every names[]/notes[] record in a converged draft "
                    "that a class correction has left describing a rendering the "
                    "segment's blocks no longer carry. Report-only -- no gate, moves "
                    "no hash.",
    )
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--prep", action="store_true",
        help="scan converged drafts and write stale_records/prep.json + "
             "stale_records/names_report.json",
    )
    mode.add_argument(
        "--build", action="store_true",
        help="re-derive the converged population and every draft fresh, gate "
             "stale_records/verdicts/<seg>.json against them, and write "
             "stale_records/stale_records_report.json + STALE_RECORDS.md",
    )
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    handler = cmd_prep if args.prep else cmd_build
    try:
        payload = handler(args)
    except RegistryError as exc:
        if exc.code == 2:
            # Fatal: no stdout JSON at all -- nothing can be mistaken for a
            # schema-conforming result (the review_artifact_check.py
            # discipline, per this project's house style). `exc.reason`
            # still goes verbatim into the stderr text so a fatal failure is
            # name-pinnable the same way an exit-1 refusal is.
            print(f"Error: {exc.reason}: {exc.message}", file=sys.stderr)
            return exc.code
        envelope = {"success": False, "reason": exc.reason, "error": exc.message}
        envelope.update(exc.extra)
        print(dumps_line(envelope))
        print(f"Error: {exc.message}", file=sys.stderr)
        return exc.code
    print(dumps_line(payload))
    return 0


if __name__ == "__main__":
    sys.exit(main())
