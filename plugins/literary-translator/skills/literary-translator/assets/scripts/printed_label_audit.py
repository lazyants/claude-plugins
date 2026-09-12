#!/usr/bin/env python3
"""printed_label_audit.py -- report-only advisory audit for #929: nothing
else in this plugin compares a converged draft's PRINTED entity-markup
labels (`<person>Bob</person>`) against canon.json's frozen
`canonical_target_form`s. See references/canon-and-glossary.md for the
authoritative narrative and plan-929.md (round-5) for the design this
script implements.

WHAT THIS CLOSES. `final_audit.py::warn_glossary_diff` and
`canon_harmonisation.py` both compare canon against DRAFT `names[]` rows,
never against printed prose. `assemble.py` / `render_obsidian.py` are the
only scripts that parse `<person>`/`<place>` markup, and in index mode
`render_obsidian.py` looks the printed payload up in the canon-target
index -- but no shipped gate REPORTS when a printed label misses that
index and re-spells a name canon has already frozen under a different
form. This script surfaces those sites for a human to adjudicate.
Report-only: no gating, no automatic edit to canon.json or to any draft.
THE IRON RULE: deciding that a printed label and a frozen target denote
the same referent is a language judgement, made by a DISPATCHED model,
never by this script -- every check below is mechanical (schema shape,
byte-exact anchoring, cardinality, shard membership), never an identity
call.

TWO MUTUALLY EXCLUSIVE MODES (passing neither, or both, is a usage error:
argparse prints to stderr and exits 2)
--------------------------------------------------------------------------
  --build-corpus --durable-root DIR [--out PATH]
      Gathers every well-formed marked span across every CONVERGED,
      review-current draft into ONE per-attempt corpus file, and prints
      its path/digest/counts as one stdout JSON line. Fail-closed
      throughout -- see build_corpus()'s own docstring.

      Only an ABSENT profile.yml `output.entity_markup` block is mode
      "off" (nothing scanned). The ordinary configuration -- `index_from`
      absent or "canon" -- is STRIP mode and is scanned in full, exactly
      like INDEX mode (`index_from: markup`): `assemble.py`'s own
      `_entity_markup_mode` returns "strip" whenever `index_from` is not
      "markup", and strip mode still prints every payload verbatim.

      Every well-formed marked span becomes exactly one SITE in exactly
      one of two disjoint corpus lists: `dispatchable_sites` (shown to a
      judge) or `unavailable_sites` (reported, never sharded, never
      carries a verdict -- a site whose anchor fields alone exceed the
      per-site byte budget, whose matching canon-row count exceeds the
      per-site cap, or which cannot fit any shard alone). No off-canon
      prefilter: a site whose label IS a canon target is still a site,
      because a wrong reassignment (canon X->Aaron, Y->Bob, a carrier
      printing <person>Bob</person> beside source X) is exactly the
      defect this pass exists to surface and a global identity shortcut
      would drop it.

      `dispatchable_sites` is then partitioned, deterministically, into
      byte-bounded shards for dispatch (see _pack_shards()). A malformed
      span (assemble.py's own `entity_markup_malformed` family) is FATAL
      here, not skipped -- assemble.py already refuses it at W8, and a
      corpus that silently dropped it would understate the population.

      THE SCRIPT EMITS THE SHARDS; THE SESSION DISPATCHES THEM VERBATIM.
      Beside the corpus file, one file per shard is written --
      `{corpus file stem}.{shard_id}.json`, in the same directory -- each
      the EXACT bytes to hand to the judge for that shard (shard_id,
      shard_digest, sites), produced by the one function
      (_serialize_shard_payload) this script's own byte-budget sizing
      already used. Nothing downstream reconstructs or re-serializes this
      payload: a session's job is to read one shard file and dispatch its
      contents unchanged, never to rebuild it from dispatchable_sites
      itself, which is a byte-for-byte different (and, on non-ASCII
      content, much larger) serialization the moment two independent
      json.dumps calls are allowed to disagree. Every emitted file is
      re-read from disk and confirmed within budget before this command
      returns; the whole build is refused, nothing published, if any is
      not.

      stdout on success, exactly one JSON line:
        {"success": true, "mode": "build-corpus", "corpus_path": "<path>",
         "corpus_sha256": "<hex>", "canon_sha256": "<hex>",
         "entity_markup_mode": "off"|"strip"|"index",
         "converged_segments": N, "drafts_excluded_stale_review": N,
         "carriers_scanned": N, "dispatchable_sites": N,
         "unavailable_sites": N, "shards": N, "shard_dir": "<path>",
         "should_dispatch": true|false}

  --report --corpus PATH --expect-corpus-sha256 HEX
           [--attempt PATH [--attempt PATH ...]]
      ONE mode that both validates the dispatched judging pass's output
      and, only if every check passes, renders the operator's report from
      the same in-memory data -- and writes nothing, ever. There is no
      separate --check step and no --approve-to: this pass has no later
      pipeline consumer of a verdict (unlike #823's canon_harmonisation.json,
      which a later W-step reads), so there is nothing for a durable
      approved artifact to be pointed at and nothing to forge by writing
      one. See run_report()'s own docstring for the full validation order.

      --attempt is repeatable, one file per shard the corpus declared; it
      may be omitted entirely when the corpus declared zero shards (mode
      "off", or a book with no dispatchable sites at all).

      On success -- and ONLY then -- renders the full operator report as
      the same one stdout JSON line; every field comes from the corpus
      the digest just authenticated, never from a verdict, except a
      verdict's own kind and its anchor. Any check failing renders NOTHING
      on stdout and exits non-zero -- the reason goes to stderr only.

      stdout on success, exactly one JSON line:
        {"success": true, "mode": "report", "entity_markup_mode": "...",
         "sites_total": N, "matches_frozen_target_count": N,
         "no_canon_match_count": N, "unavailable_sites_total": N,
         "sites": [...], "unavailable_sites": [...]}

WHAT THIS SCRIPT NEVER DOES: decide that a printed label and a frozen
target denote the same referent; edit canon.json, any draft, or any
rendered output; block W7, W8 or W9. Acting on a verdict is the existing,
unrelated operator-driven canon_validate.py --correct route, or a draft
edit the operator makes by hand.

Exit codes throughout: 0 clean, 2 every failure (a fatal I/O/deployment
fault, a validation refusal, or a usage error) -- there is no separate
gate-fail code, because the fold above leaves no pipeline step this
script could report a partial pass to.
"""

import argparse
import hashlib
import json
import os
import re
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

try:
    import jsonschema
except ImportError as e:
    sys.stderr.write(
        "printed_label_audit.py requires the 'jsonschema' package (>=4.26.0). "
        "Install with:\n\n"
        "    pip install -r requirements.txt\n\n"
        "(or directly: pip install 'jsonschema>=4.26.0')\n\n"
        f"(import error: {e})\n"
    )
    sys.exit(2)

# --- the shared one-line JSON serialiser (#369) -----------------------------
# Loaded by EXACT PATH, never `import json_stdout` -- a bare sibling import
# resolves through the global sys.modules cache regardless of which staged
# copy the CALLER intended, so one process that stages several durable roots
# would bind the FIRST root's copy for all of them.
import importlib.util as _importlib_util

# This script's --report mode is a read-only render. Left unset, the dynamic
# loads below would let CPython's default SourceFileLoader write
# __pycache__/*.pyc into this file's own directory -- ${durable_root}/scripts/
# -- on every run. Set process-wide, before the first dynamic load.
sys.dont_write_bytecode = True

SCRIPTS_DIR = Path(__file__).resolve().parent
DURABLE_ROOT = SCRIPTS_DIR.parent
SCHEMAS_DIR = DURABLE_ROOT / "schemas"
CANON_PATH = DURABLE_ROOT / "canon.json"

SCHEMA_FILENAME = "printed-label-audit.schema.json"


def _load_sibling(module_name: str):
    """Loads a staged sibling script by EXACT PATH, never `import <name>`,
    for the same reason json_stdout.py is loaded that way below: a bare
    sibling import resolves through the global sys.modules cache regardless
    of which staged copy the caller intended. Raises
    PrintedLabelAuditFatalError (exit 2) when the sibling is absent -- a
    missing staged script is a deployment fault, never a validation
    refusal."""
    path = SCRIPTS_DIR / f"{module_name}.py"
    try:
        spec = _importlib_util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            raise ImportError(f"no loader for {path}")
        module = _importlib_util.module_from_spec(spec)
        spec.loader.exec_module(module)
    except (ImportError, OSError) as exc:
        raise PrintedLabelAuditFatalError(
            f"cannot load {module_name}.py from {path} ({exc}) -- it must be staged "
            "alongside printed_label_audit.py under ${durable_root}/scripts/"
        )
    return module


class PrintedLabelAuditFatalError(Exception):
    """Exit 2: an I/O or deployment problem that stops this script from
    even attempting its work -- an unreadable/absent canon.json, an
    unreadable ledger.d, a missing staged sibling, a malformed marked
    span. `offending`, when not None, is the exact value at fault."""

    def __init__(self, message, offending=None):
        super().__init__(message)
        self.offending = offending


class PrintedLabelAuditRefusal(Exception):
    """Exit 2: --report's input is well-formed JSON but fails one of the
    validation checks documented in run_report()'s own docstring -- a
    tamper shape, a shape violation, or a cardinality/anchoring mismatch.
    `offending`, when not None, names the offending value."""

    def __init__(self, message, offending=None):
        super().__init__(message)
        self.offending = offending


_JSON_STDOUT_PATH = SCRIPTS_DIR / "json_stdout.py"
try:
    _json_stdout_spec = _importlib_util.spec_from_file_location(
        "json_stdout", _JSON_STDOUT_PATH
    )
    if _json_stdout_spec is None or _json_stdout_spec.loader is None:
        raise ImportError(f"no loader for {_JSON_STDOUT_PATH}")
    _json_stdout = _importlib_util.module_from_spec(_json_stdout_spec)
    _json_stdout_spec.loader.exec_module(_json_stdout)
except (ImportError, OSError) as _json_stdout_exc:  # pragma: no cover - staging error path
    sys.stderr.write(
        f"printed_label_audit.py: cannot load json_stdout.py from {_JSON_STDOUT_PATH} "
        f"({_json_stdout_exc}).\n"
        "json_stdout.py must be installed alongside printed_label_audit.py under "
        "${durable_root}/scripts/ -- Step 0a's copy pass places it there.\n"
    )
    sys.exit(2)

dumps_line = _json_stdout.dumps_line


# ---------------------------------------------------------------------------
# Named constants -- every cap here is asserted at its boundary by
# tests/printed_label_audit.test.py (plan §6/§8).
# ---------------------------------------------------------------------------

# Context-excerpt window: up to this many characters on EACH side of the
# anchor position (the printed span for a translated excerpt, a matched
# source_form's occurrence for a source excerpt). Truncatable -- unlike the
# anchor fields (label/source_form/canonical_target_form), which are never
# windowed or shortened.
EXCERPT_CONTEXT_CHARS = 100

# Appended at whichever end of an excerpt window is not the carrier's own
# text boundary, so a judge (and an operator reading the render) can never
# mistake a truncated excerpt for the carrier's full text.
TRUNCATION_MARKER = "..."

# A BACKSTOP against a genuinely pathological site, not the real bound on
# prompt size -- MAX_ANCHOR_BYTES_PER_SITE and MAX_SHARD_BYTES below are
# what actually bound what a judge is shown; do not "tune" this value back
# down thinking it is the budget. Measured over all 13 local books with
# entity_markup (31,618 sites, via the plugin's own scanner): median 5
# canon rows/site, p90 23, p99 61, p99.9 90, observed MAX 90 -- so 250
# refuses zero real sites and is not expected to fire in normal use. A
# site over the cap still goes to unavailable_sites with reason
# canon_row_cap_exceeded, never silently trimmed.
MAX_CANON_ROWS_PER_SITE = 250

# THE REAL PER-SITE BOUND: a site whose anchor-only fields (tag, label, and
# every matched canon row's source_form/canonical_target_form, UTF-8, NFC)
# serialise to more than this many bytes goes to unavailable_sites -- these
# fields are the ones validation binds byte-exactly and render displays
# verbatim, so they are never truncated, only capped by refusing the site
# outright. DERIVED from the shard-fit guarantee this exists to give, not
# picked as a tuning value: at 16384 bytes a single site is at most ~8% of
# MAX_SHARD_BYTES (200,000), which is margin enough that a site passing
# this gate can always be packed into a fresh shard alone. The previous
# 4096 was never derived from that guarantee and measurably bound on the
# ORDINARY case: on one real 13-book corpus it refused 517 of 4340 sites
# (12%) on a single book, entirely because real canon_entry forms average
# ~18 UTF-8 bytes each side (n=1066) and real sites there carry up to 52
# matching rows -- see MAX_CANON_ROWS_PER_SITE's own comment for the
# matching row-count measurement this pairs with.
MAX_ANCHOR_BYTES_PER_SITE = 16384

# THE REAL PROMPT-SIZE BOUND: the serialised byte budget for one shard's
# complete dispatch object (metadata plus every site assigned to it, canon
# rows and excerpts included) -- measured over the whole object, never over
# the site list alone. A single site whose own object already exceeds this
# cannot be packed into any shard and is moved to unavailable_sites instead.
MAX_SHARD_BYTES = 200_000


# ---------------------------------------------------------------------------
# JSON / canon loading -- shared by both modes.
# ---------------------------------------------------------------------------


def _read_json_bytes(path: Path, label: str):
    """Reads `path` and parses it as a JSON object. Returns (raw_bytes, doc).
    Raises PrintedLabelAuditFatalError -- and only ever that -- for every
    failure here: an I/O error, invalid UTF-8, invalid JSON, or bytes that
    parse to something other than a JSON object. Mirrors
    canon_harmonisation.py's own _read_json_bytes."""
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        raise PrintedLabelAuditFatalError(f"{label} not found at {path}")
    except OSError as exc:
        raise PrintedLabelAuditFatalError(f"{label} at {path} could not be read: {exc}")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PrintedLabelAuditFatalError(f"{label} at {path} is not valid UTF-8: {exc}")
    try:
        doc = json.loads(text)
    except (json.JSONDecodeError, RecursionError) as exc:
        # RecursionError joins JSONDecodeError: json.loads recurses once per
        # nesting level, and it is not a JSONDecodeError subclass, so a
        # sufficiently deeply nested document (an untrusted --attempt file,
        # or a hand-edited corpus) escaped as an uncaught traceback -- exit
        # 1, contradicting this module's own documented "0 clean, 2 every
        # failure" contract.
        raise PrintedLabelAuditFatalError(f"{label} at {path} is not valid JSON: {exc}")
    if not isinstance(doc, dict):
        raise PrintedLabelAuditFatalError(
            f"{label} at {path} did not parse to a JSON object (got {type(doc).__name__})"
        )
    return raw, doc


def _load_canon(canon_path: Path):
    """Returns (raw_bytes, entries) for canon.json at `canon_path`. Raises
    PrintedLabelAuditFatalError on anything short of a readable JSON object
    carrying an entries{} mapping of dict-shaped records whose
    canonical_target_form (when present) is a string -- mirrors
    canon_harmonisation.py::_load_canon exactly."""
    raw, doc = _read_json_bytes(canon_path, "canon.json")
    entries = doc.get("entries")
    if not isinstance(entries, dict):
        raise PrintedLabelAuditFatalError(
            f"canon.json at {canon_path} has no entries{{}} mapping "
            f"(got {type(entries).__name__ if entries is not None else 'missing'})"
        )
    for source_form, entry in entries.items():
        if not isinstance(entry, dict):
            raise PrintedLabelAuditFatalError(
                f"canon.json at {canon_path} maps {source_form!r} to a "
                f"{type(entry).__name__}, not an entry object"
            )
        target = entry.get("canonical_target_form")
        if target is not None and not isinstance(target, str):
            raise PrintedLabelAuditFatalError(
                f"canon.json at {canon_path} entry {source_form!r} has a "
                f"non-string canonical_target_form ({type(target).__name__})"
            )
    return raw, entries


# Portable fallbacks (0, a no-op flag) exactly as scaffold_setup.py defines
# these -- some platforms lack one of these O_* flags; ORing in a 0 changes
# nothing where a flag is unavailable rather than raising AttributeError.
_O_CLOEXEC = getattr(os, "O_CLOEXEC", 0)
_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_O_DIRECTORY = getattr(os, "O_DIRECTORY", 0)


def _atomic_publish_create_once(dest: Path, raw: bytes) -> None:
    """Publishes `raw` at `dest` CREATE-ONCE (os.link, EEXIST fatal) --
    never exists()-then-replace, which has a window and whose Path.exists()
    follows a dangling symlink. Mirrors canon_harmonisation.py's own
    _atomic_publish(create_only=True): the corpus file is per-attempt and
    the digest a session keeps refers to THOSE bytes, so silently
    overwriting an existing path would leave a kept digest pointing at
    bytes that are gone.

    THE PARENT DIRECTORY ITSELF IS PINNED, not just the two file names in
    it -- a security-review finding this repo has already seen twice on
    this plugin. O_CREAT|O_EXCL on the temp name and os.link's own
    refusal to follow a symlink AT dest both guard the two NAMES, but
    neither says anything about the DIRECTORY those names live in: an
    `is_symlink()` check on `dest.parent` before opening it is
    check-then-use (scaffold_setup.py's own atomic_write_text docstring
    walks through why), because the directory can be swapped for a
    symlink to anywhere between that check and the write. Copied,
    not reinvented, from this repo's own worked example
    (scaffold_setup.py's runs_dir dir-fd pattern, :657-671): mkdir first
    (the common case, parent genuinely absent), THEN open it with
    O_DIRECTORY|O_NOFOLLOW|O_CLOEXEC to pin its inode, and resolve every
    later operation against that fd (dir_fd=) rather than by re-walking
    the path name -- a directory swapped in after the open cannot
    redirect a dir_fd-relative operation. Verified: a symlinked
    `printed_label_audit/` sending both files outside durable_root before
    this fix now fails closed instead."""
    # The mkdir is INSIDE the error translation, not before it (ped-ant P2). A
    # parent that is a regular file, or is unwritable, raises a bare OSError that
    # main() does not catch -- the command then exits 1 with a traceback, while
    # this script's documented contract, and the W7 step's failure disposition,
    # both promise exit 2 and a named FATAL line. canon_harmonisation.py's own
    # publisher translates it for the same reason.
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise PrintedLabelAuditFatalError(
            f"could not create the directory {dest.parent} to publish "
            f"{dest.name}: {exc}"
        )
    dir_flags = os.O_RDONLY | _O_DIRECTORY | _O_NOFOLLOW | _O_CLOEXEC
    try:
        dir_fd = os.open(dest.parent, dir_flags)
    except OSError as exc:
        raise PrintedLabelAuditFatalError(
            f"could not open {dest.parent} to publish {dest.name}: {exc} -- refusing if "
            "it is a symlink rather than a real directory"
        )
    try:
        tmp_name = f".{dest.name}.tmp.{os.getpid()}.{os.urandom(4).hex()}"
        replaced = False
        try:
            fd = os.open(
                tmp_name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _O_NOFOLLOW | _O_CLOEXEC,
                0o644, dir_fd=dir_fd,
            )
            with os.fdopen(fd, "wb") as f:
                f.write(raw)
                f.flush()
                os.fsync(f.fileno())
            try:
                os.link(tmp_name, dest.name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
            except FileExistsError:
                raise PrintedLabelAuditFatalError(
                    f"{dest} already exists -- a corpus/shard file is per-attempt and "
                    "is never overwritten"
                )
            replaced = True
        except OSError as exc:
            raise PrintedLabelAuditFatalError(f"could not publish at {dest}: {exc}")
        finally:
            # UNCONDITIONAL, success included (ped-ant P2). This publisher always
            # uses os.link, which leaves the SOURCE name in place, so on success
            # both names point at one inode. Skipping the unlink when `replaced`
            # is true left a hidden `.<name>.tmp.<pid>.<hex>` beside every
            # published file -- hundreds per audit on a large book, and deleting
            # the visible files would not reclaim the data, because the link
            # count never reaches zero. Best-effort either way: a failure to
            # clean up must not mask the publish's own outcome.
            try:
                os.unlink(tmp_name, dir_fd=dir_fd)
            except OSError:
                pass
    finally:
        os.close(dir_fd)


# ---------------------------------------------------------------------------
# Excerpt windows -- context fields only; anchor fields are NEVER windowed.
# ---------------------------------------------------------------------------


def _excerpt_window(text: str, start: int, end: int) -> dict:
    """A window of `text` covering [start, end) plus up to
    EXCERPT_CONTEXT_CHARS on each side, with an explicit truncation marker
    at whichever end is not the carrier's own text boundary. `start`/`end`
    may be equal (a zero-width anchor, e.g. a canon source_form occurrence
    used only as a centring point)."""
    window_start = max(0, start - EXCERPT_CONTEXT_CHARS)
    window_end = min(len(text), end + EXCERPT_CONTEXT_CHARS)
    truncated_start = window_start > 0
    truncated_end = window_end < len(text)
    piece = text[window_start:window_end]
    if truncated_start:
        piece = TRUNCATION_MARKER + piece
    if truncated_end:
        piece = piece + TRUNCATION_MARKER
    return {"text": piece, "truncated_start": truncated_start, "truncated_end": truncated_end}


def _canon_rows_for_source_text(entries: dict, source_text: str) -> list:
    """Every (source_form, canonical_target_form, occurrence_start) triple
    from canon `entries` whose source_form occurs (substring, byte-exact,
    never folded/normalised) in `source_text` -- the evidence a judge needs
    and nothing more (plan §4). No off-canon prefilter and no per-referent
    dedup here: R1-1 -- a site whose label IS a canon target is still
    reported with every canon row present in its carrier's source text.
    Deterministically ordered by (occurrence_start, source_form) rather
    than left in canon.json's own key order, so the corpus's own content is
    reproducible independent of entries{}'s on-disk key ordering."""
    rows = []
    for source_form, entry in entries.items():
        if not source_form.strip():
            continue
        occurrence = source_text.find(source_form)
        if occurrence == -1:
            continue
        target_form = entry.get("canonical_target_form")
        if not isinstance(target_form, str) or not target_form.strip():
            continue
        rows.append((source_form, target_form, occurrence))
    rows.sort(key=lambda row: (row[2], row[0]))  # (occurrence, source_form)
    return rows


def _carrier_kind_from_locator(locator: str) -> str:
    """block / footnote / verse, read off final_audit.py::term_carriers's
    own label prefix convention ("blocks[...]", "footnotes[...]",
    "verses[...].field") -- never re-derived from anything else, so a
    change to that convention fails LOUD here rather than silently
    mis-classifying every site."""
    if locator.startswith("blocks["):
        return "block"
    if locator.startswith("footnotes["):
        return "footnote"
    if locator.startswith("verses["):
        return "verse"
    raise PrintedLabelAuditFatalError(
        f"carrier locator {locator!r} matches none of the known "
        "term_carriers() label prefixes (blocks[/footnotes[/verses[) -- "
        "refusing to guess its carrier kind"
    )


def _carriers_dropped_for_missing_source(final_audit_module, segpack: dict, draft: dict, term_check) -> list:
    """Every (locator, target_text) pair final_audit.term_carriers() DROPS
    purely because its source counterpart is empty/absent -- round-2
    review MINOR. term_carriers()'s own `add()` silently excludes a
    carrier whenever its source_text is falsy, REGARDLESS of whether the
    translated target_text is delivered to a reader with content of its
    own -- a translated carrier is printed either way, so a malformed
    span inside one must still be caught (plan §4: a malformed span is
    FATAL, never silently skipped, precisely so the corpus cannot
    understate the population). Left unmodified, --build-corpus would
    scan only what term_carriers() returns and exit 0 over a converged
    segment carrying an unterminated `<person>` tag in exactly this shape.

    Deliberately mirrors term_carriers()'s own traversal and its TWO
    legitimate exclusions (a policy gate -- apparatus_policy/verse_mode --
    and the placeholder-only-block rule for verse-embedded blocks) rather
    than reimplementing carrier discovery from scratch: this function
    must keep agreeing with term_carriers() on which locators exist AT
    ALL and why each is skipped, precisely so it reports the ONE
    additional reason (source missing) term_carriers() cannot itself
    report. The established precedent for this kind of INDEPENDENT
    recomputation against the same two profile fields is render_obsidian.
    py's own `_entity_markup_mode` copy of assemble.py's function of the
    same name -- change one traversal's exclusions and change this one's
    to match.

    Never returns a "carrier": there is no source text to anchor a canon
    row against, so nothing here is ever added to dispatchable_sites or
    unavailable_sites -- callers only scan each returned target_text for
    malformed markup and raise fatally on a hit."""
    draft_blocks = final_audit_module._as_mapping(draft.get("blocks"))
    draft_footnotes = final_audit_module._as_mapping(draft.get("footnotes"))
    draft_verses = final_audit_module._as_mapping(draft.get("verses"))
    segpack_verses = [
        v for v in final_audit_module._as_sequence(segpack.get("verses")) if isinstance(v, dict)
    ]
    placeholder_only_blocks = {
        v.get("parent_block") for v in segpack_verses
        if v.get("mount") != "embedded" and isinstance(v.get("parent_block"), str)
    }

    dropped = []

    for block in final_audit_module._as_sequence(segpack.get("blocks")):
        if not isinstance(block, dict):
            continue
        block_id = block.get("id")
        if not isinstance(block_id, str) or block_id in placeholder_only_blocks:
            continue
        target = draft_blocks.get(block_id)
        if not isinstance(target, str) or not target.strip():
            continue
        if not final_audit_module._carrier_source_text(block):
            dropped.append((f"blocks[{block_id!r}]", target))

    if term_check.apparatus_policy != "preserve_source":
        for footnote in final_audit_module._as_sequence(segpack.get("footnotes")):
            if not isinstance(footnote, dict):
                continue
            number = footnote.get("n")
            if number is None:
                continue
            target = draft_footnotes.get(str(number))
            if not isinstance(target, str) or not target.strip():
                continue
            source_text = footnote.get("source_text")
            if not isinstance(source_text, str) or not source_text:
                dropped.append((f"footnotes[{str(number)!r}]", target))

    if term_check.verse_mode != "skip":
        for verse in segpack_verses:
            vid = verse.get("vid")
            if not isinstance(vid, str):
                continue
            rendered_verse = draft_verses.get(vid)
            if not isinstance(rendered_verse, dict):
                continue
            source_text = term_check.verse_sources.get(vid)
            for field in final_audit_module.DELIVERED_VERSE_FIELDS:
                target = rendered_verse.get(field)
                if not isinstance(target, str) or not target.strip():
                    continue
                if not source_text:  # covers both a missing vid (None) and an empty string
                    dropped.append((f"verses[{vid!r}].{field}", target))

    return dropped


# ---------------------------------------------------------------------------
# --build-corpus
# ---------------------------------------------------------------------------


# THE one shape of an unavailable_sites row. Built at three points -- the
# canon-row cap and the anchor-byte cap in build_corpus(), and
# _pack_shards()'s own "cannot fit any shard alone" -- and rendered
# verbatim by --report, so the field list lives in exactly one place and
# the three cannot drift apart.
_UNAVAILABLE_SITE_FIELDS = ("site_id", "tag", "segment", "carrier_kind", "carrier_locator")


def _unavailable_entry(site: dict, reason: str) -> dict:
    """An unavailable_sites row: `site`'s identifying fields plus `reason`.
    `site` may be a full dispatchable site or just the identifying head of
    one -- only _UNAVAILABLE_SITE_FIELDS are read, and every one of them is
    required (a KeyError here is a bug in the caller, never a data fault)."""
    entry = {field: site[field] for field in _UNAVAILABLE_SITE_FIELDS}
    entry["reason"] = reason
    return entry


# A sha256 hex digest is ALWAYS exactly this many characters -- used only
# as a same-length stand-in for `shard_digest` while packing, before the
# real digest over a shard's final site_id set is even computable. Never
# used for anything that leaves this module: it exists solely so sizing a
# shard payload costs the same number of bytes as shipping one.
_PLACEHOLDER_SHARD_DIGEST = "0" * 64


def _build_shard_payload(shard_id: str, shard_digest: str, sites: list) -> dict:
    """THE single builder for a shard's dispatched payload -- shard_id,
    shard_digest, sites, field for field, exactly as
    printed-label-audit.schema.json's --attempt shape requires the
    dispatched pass to echo back. NOTHING ELSE in this module (or in
    tests/printed_label_audit.test.py) constructs a shard payload by hand.

    This is the fix for a recurring class of defect, not just its latest
    instance: round 1 found the packer's own size probe omitted the
    `shard_id` key every packed site actually carries; round 2 found it
    also omitted `shard_digest` itself, which the same schema requires.
    Both were "a field the sizer forgot" bugs, and the fix each time was
    to add the missing field to a SEPARATELY built sizing object -- which
    only sets up the next missing field to be found the same way. Routing
    every construction of this shape through ONE function removes the
    second place a field could ever be added: `_pack_shards`'s size probe
    below calls this with `_PLACEHOLDER_SHARD_DIGEST` (same byte length as
    a real digest, so the measured size is exact, not approximate), and
    the real emission -- currently only the test suite's own
    near-boundary regression, and any future dispatch-side code -- calls
    it with the real digest. A field added to this shape in the future is
    therefore automatically counted during packing, because there is
    nowhere else for it to be added.

    The assertion below is the guard that keeps the placeholder and a
    real digest interchangeable for sizing: if a sha256 hex digest were
    ever not exactly 64 characters, sizing during packing would silently
    stop matching what ships, and this raises instead of a shard silently
    going over budget."""
    assert len(shard_digest) == 64, (
        f"shard_digest must be exactly 64 characters (a sha256 hex digest, or "
        f"_PLACEHOLDER_SHARD_DIGEST, the same-length sizing stand-in), got "
        f"{len(shard_digest)} -- packing's sizing no longer matches what ships"
    )
    return {"shard_id": shard_id, "shard_digest": shard_digest, "sites": sites}


def _serialize_shard_payload(shard_id: str, shard_digest: str, sites: list) -> bytes:
    """THE single serialization of a shard's dispatched payload -- one
    json.dumps call, one set of options, used for BOTH sizing during
    packing (with _PLACEHOLDER_SHARD_DIGEST) and the real file
    --build-corpus writes (with the real digest). Round 3's defect was
    that _build_shard_payload fixed the SHAPE but not the BYTES: sizing
    used `ensure_ascii=False` while nothing else in this script, or
    anywhere else, pinned what a SESSION would actually serialize when it
    built the real dispatch payload itself out of the corpus's own
    dispatchable_sites -- Python's own json.dumps DEFAULT
    (`ensure_ascii=True`) escapes every non-ASCII codepoint as `\\uXXXX`,
    which measurably more than doubled a Hebrew-heavy shard (199,853 ->
    442,641 bytes on the same site set). The fix is not a tighter
    measurement of an object nobody emits; it is that this script now
    WRITES the exact bytes a session dispatches (see build_corpus()'s own
    shard-file-emission step), so there is no second serialization left
    anywhere to diverge from this one.

    Compact, no indentation: a shard file is a machine-dispatch payload,
    not an operator-facing artifact, and every byte here is prompt
    budget."""
    payload = _build_shard_payload(shard_id, shard_digest, sites)
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _pack_shards(dispatchable_sites: list) -> tuple:
    """Deterministically bin-packs `dispatchable_sites` (already sorted by
    site_id) into byte-bounded shards, greedy-first-fit in ORDER (never a
    smarter packing -- reproducibility matters more than fill efficiency
    here). Returns (sites_with_shard_id, still_unavailable, shards_map)
    where `still_unavailable` holds any site whose own serialised object
    already exceeds MAX_SHARD_BYTES even alone -- such a site cannot be
    dispatched in any shard and is moved out of the dispatchable list
    entirely, with reason "exceeds_shard_budget_alone".

    The byte budget is measured over the COMPLETE shard payload
    (_build_shard_payload's shape -- shard_id, shard_digest and every site
    assigned so far), metadata included, not over the site list alone --
    plan §6's explicit requirement. The real digest is not known while
    packing (it depends on the final site_id set), so sizing uses
    _PLACEHOLDER_SHARD_DIGEST -- see _build_shard_payload's own docstring
    for why that is exact, not approximate. size_of() calls
    _serialize_shard_payload(), the SAME function build_corpus() calls to
    write the real shard file -- same function, same json.dumps options,
    not merely the same shape (round 3's own defect)."""

    def size_of(shard_id: str, sites: list) -> int:
        return len(_serialize_shard_payload(shard_id, _PLACEHOLDER_SHARD_DIGEST, sites))

    def with_shard_id(site: dict, shard_id: str) -> dict:
        # SIZED AND STORED WITH THE SAME SHAPE: a shallow copy carrying the
        # `shard_id` key it will actually be dispatched with. Sizing a site
        # WITHOUT this key (round-1 review MAJOR) undercounts every site's
        # real serialised footprint, since every packed site carries it --
        # a shard measured under budget that way can come out over budget
        # once the key is added afterward.
        return {**site, "shard_id": shard_id}

    packed_sites = []
    still_unavailable = []
    shards_map = {}
    current_sites = []  # each already carries its prospective shard_id
    shard_n = 0

    def close_current_shard():
        nonlocal current_sites, shard_n
        if not current_sites:
            return
        shard_n += 1
        site_ids = sorted(s["site_id"] for s in current_sites)
        digest = hashlib.sha256(
            json.dumps(site_ids, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        # The id every site in `current_sites` was already sized/stored
        # under -- see the probe_shard_id computation below, which is
        # stable for as long as shard_n does not change.
        shard_id = f"shard_{shard_n:04d}"
        shards_map[shard_id] = digest
        packed_sites.extend(current_sites)
        current_sites = []

    for site in dispatchable_sites:
        probe_shard_id = f"shard_{shard_n + 1:04d}"
        sited = with_shard_id(site, probe_shard_id)
        if size_of(probe_shard_id, [sited]) > MAX_SHARD_BYTES:
            still_unavailable.append(_unavailable_entry(site, "exceeds_shard_budget_alone"))
            continue
        candidate_sites = current_sites + [sited]
        if current_sites and size_of(probe_shard_id, candidate_sites) > MAX_SHARD_BYTES:
            close_current_shard()
            # close_current_shard() just incremented shard_n, so the
            # overflow-triggering site's `sited` copy above is stamped
            # with the STALE probe_shard_id from before that increment --
            # the id of the shard that just closed, not the fresh shard
            # this site is actually starting. Re-derive and re-stamp, or
            # this site is dispatched under one shard_id while permanently
            # claiming membership (and inflating the byte count) of the
            # PREVIOUS, already-closed one once grouped by that field.
            probe_shard_id = f"shard_{shard_n + 1:04d}"
            sited = with_shard_id(site, probe_shard_id)
            # ROLLOVER GUARD (round-3 review MINOR 2): shard_n just
            # changed, so the shard_id STRING'S OWN WIDTH can change too
            # (shard_9999 -> shard_10000, 10 chars -> 11) -- which changes
            # THIS site's own serialised size, since it carries the id
            # twice (the shard payload's own "shard_id" key and this
            # site's "shard_id" field). The "fits alone" check above ran
            # only against the OLD, shorter id; a site that fit under it
            # can fail to fit once restamped with the new, longer one, and
            # nothing downstream ever re-checks a site once it is inside
            # current_sites. Re-run the SAME check here.
            if size_of(probe_shard_id, [sited]) > MAX_SHARD_BYTES:
                still_unavailable.append(_unavailable_entry(site, "exceeds_shard_budget_alone"))
                continue
            candidate_sites = [sited]
        current_sites = candidate_sites
    close_current_shard()

    return packed_sites, still_unavailable, shards_map


def build_corpus(durable_root_str: "str | None", out_str: "str | None") -> dict:
    """Gathers every well-formed marked span across every converged,
    review-current draft into the corpus file --report is later validated
    against. See this module's own docstring for the corpus shape and the
    fail-closed rules. Mirrors canon_harmonisation.py::build_corpus's own
    three-step draft-gathering discipline exactly:

      1. WHICH FRAGMENTS EXIST -- ledger_merge.py::_read_fragments, never
         final_audit.py::load_converged_fragments (the latter's is_dir() +
         glob() answer True/[] for a populated directory at mode 0o000, so
         an unreadable runs/ledger.d would report itself empty).
      2. WHICH DRAFTS SURVIVE -- status == "converged" is not sufficient:
         the fragment's reviewed_draft_sha1 must still match the draft's
         current draft_content_sha1, else the draft was hand-edited after
         the review that approved it. Excluded and counted, never fatal.
      3. WHICH SPANS INSIDE -- every carrier kind (block, footnote, verse)
         final_audit.py::term_carriers already walks, scanned with
         assemble.py's own _entity_markup_scan(). A malformed span is
         FATAL here, not skipped."""
    durable_root = Path(durable_root_str) if durable_root_str else DURABLE_ROOT
    canon_path = durable_root / "canon.json"
    ledger_d = durable_root / "runs" / "ledger.d"
    manifest_path = durable_root / "manifest.json"

    final_audit = _load_sibling("final_audit")
    assemble = _load_sibling("assemble")
    ledger_merge = _load_sibling("ledger_merge")
    vd = _load_sibling("validate_draft")

    canon_bytes, entries = _load_canon(canon_path)
    canon_sha256 = hashlib.sha256(canon_bytes).hexdigest()

    profile = vd.load_profile(durable_root)
    try:
        mode = assemble._entity_markup_mode(profile)
    except assemble.AssembleError as exc:
        raise PrintedLabelAuditFatalError(
            f"profile.yml's output.entity_markup is not usable: {exc}"
        )

    converged_segments = 0
    drafts_excluded_stale_review = 0
    carriers_scanned = 0
    draft_content_sha1_map = {}
    dispatchable_sites = []
    unavailable_sites = []

    if mode != "off":
        cfg = assemble._entity_markup_config(profile)
        open_re, close_re, any_re = assemble._compile_entity_markup(cfg)

        policy = vd.ProfileConfig(profile)
        manifest, manifest_err = final_audit.load_json(manifest_path, "manifest.json")
        verse_sources = (
            final_audit.verse_source_index(manifest)
            if not manifest_err and isinstance(manifest, dict) else {}
        )
        term_check = final_audit.TermCheck((), policy.apparatus_policy, policy.verse_mode, verse_sources)

        try:
            fragments = ledger_merge._read_fragments(ledger_d)
        except (OSError, ledger_merge.LedgerMergeError) as exc:
            raise PrintedLabelAuditFatalError(
                f"cannot enumerate ledger fragments at {ledger_d} ({exc}) -- refusing to "
                "report an empty corpus for a directory that may be populated"
            )

        for seg in sorted(fragments):
            fragment = fragments[seg]
            if not isinstance(fragment, dict):
                raise PrintedLabelAuditFatalError(
                    f"ledger fragment for {seg!r} is a {type(fragment).__name__}, not an "
                    "object -- refusing to treat unreadable state as a segment that has "
                    "not converged"
                )
            status = fragment.get("status")
            if not isinstance(status, str) or not status:
                raise PrintedLabelAuditFatalError(
                    f"ledger fragment for {seg!r} carries no usable status "
                    f"({status!r}) -- refusing to guess whether it converged"
                )
            if status != "converged":
                continue

            # Built from THIS script's own `durable_root` variable, never
            # from final_audit.draft_path(seg)/segpack_path(seg) -- those
            # are self-anchored to wherever final_audit.py itself was
            # loaded from (SCRIPTS_DIR, this script's own directory), which
            # diverges from `durable_root` whenever --durable-root points
            # somewhere other than this script's own install location.
            # Mirrors canon_harmonisation.py::build_corpus's identical
            # explicit-path construction, for the identical reason.
            draft_file = durable_root / "segments" / f"{seg}.draft.json"
            expected = fragment.get("reviewed_draft_sha1")
            if not isinstance(expected, str) or not expected or not draft_file.is_file():
                drafts_excluded_stale_review += 1
                continue
            try:
                current = final_audit.draft_content_sha1(draft_file)
            except (OSError, ValueError, json.JSONDecodeError):
                drafts_excluded_stale_review += 1
                continue
            if current != expected:
                drafts_excluded_stale_review += 1
                continue

            try:
                draft = json.loads(draft_file.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise PrintedLabelAuditFatalError(
                    f"converged, review-current draft {draft_file} could not be read "
                    f"({exc}) -- refusing to silently drop it from the corpus"
                )
            segpack, segpack_err = final_audit.load_json(
                durable_root / "segments" / f"segpack_{seg}.json", f"segpack {seg}"
            )
            if segpack_err or not isinstance(segpack, dict):
                raise PrintedLabelAuditFatalError(
                    f"segpack for converged, review-current segment {seg!r} could not be "
                    f"read ({segpack_err}) -- refusing to silently drop it from the corpus"
                )

            converged_segments += 1
            draft_content_sha1_map[seg] = current

            carriers = final_audit.term_carriers(segpack, draft, term_check)
            carriers_scanned += len(carriers)

            for locator, source_text, target_text in carriers:
                where = f"segment {seg!r} {locator}"
                try:
                    spans = assemble._entity_markup_scan(target_text, open_re, close_re, any_re, where)
                except assemble.AssembleError as exc:
                    raise PrintedLabelAuditFatalError(
                        f"malformed entity markup found while auditing {where}: {exc}"
                    )
                if not spans:
                    continue
                carrier_kind = _carrier_kind_from_locator(locator)
                canon_rows = _canon_rows_for_source_text(entries, source_text)
                # The anchor-only projection of this carrier's canon rows is
                # the same for every span in it -- only tag/label vary below.
                canon_rows_anchor = [
                    {"source_form": sf, "canonical_target_form": tf} for sf, tf, _occ in canon_rows
                ]
                for start, end, tag, _ref, payload in spans:
                    # The identifying head every row below carries -- an
                    # unavailable row is exactly this plus a reason, a
                    # dispatchable site exactly this plus its evidence.
                    site_head = {
                        "site_id": f"{seg}::{locator}::{start}",
                        "tag": tag,
                        "segment": seg,
                        "carrier_kind": carrier_kind,
                        "carrier_locator": locator,
                    }
                    label = unicodedata.normalize("NFC", payload)

                    if len(canon_rows) > MAX_CANON_ROWS_PER_SITE:
                        unavailable_sites.append(
                            _unavailable_entry(site_head, "canon_row_cap_exceeded")
                        )
                        continue

                    anchor_only = {"tag": tag, "label": label, "canon_rows": canon_rows_anchor}
                    anchor_bytes = len(json.dumps(anchor_only, ensure_ascii=False).encode("utf-8"))
                    if anchor_bytes > MAX_ANCHOR_BYTES_PER_SITE:
                        unavailable_sites.append(
                            _unavailable_entry(site_head, "anchor_budget_exceeded")
                        )
                        continue

                    dispatchable_sites.append({
                        **site_head,
                        "label": label,
                        "translated_excerpt": _excerpt_window(target_text, start, end),
                        "canon_rows": [
                            {
                                "source_form": sf,
                                "canonical_target_form": tf,
                                "source_excerpt": _excerpt_window(source_text, occ, occ + len(sf)),
                            }
                            for sf, tf, occ in canon_rows
                        ],
                    })

            # FAIL-CLOSED FOR A CARRIER term_carriers() ITSELF DROPS --
            # round-2 review MINOR: term_carriers()'s own add() silently
            # excludes any carrier whose SOURCE is empty, even when its
            # translated target_text is delivered to a reader with content
            # of its own. Never contributes a site (there is no source to
            # anchor a canon row against) -- only a fail-closed malformed-
            # markup check, exactly like the main loop above.
            for locator, target_text in _carriers_dropped_for_missing_source(
                final_audit, segpack, draft, term_check
            ):
                where = f"segment {seg!r} {locator} (source empty/absent)"
                try:
                    assemble._entity_markup_scan(target_text, open_re, close_re, any_re, where)
                except assemble.AssembleError as exc:
                    raise PrintedLabelAuditFatalError(
                        f"malformed entity markup found while auditing {where}: {exc}"
                    )

        if converged_segments > 0 and carriers_scanned == 0:
            raise PrintedLabelAuditFatalError(
                f"{converged_segments} converged, review-current segment(s) contributed "
                "zero carriers to the printed-label corpus -- implausible for any segment "
                "with blocks, refusing rather than silently reporting an empty corpus"
            )

    dispatchable_sites.sort(key=lambda s: s["site_id"])
    dispatchable_sites, exceeds_alone, shards_map = _pack_shards(dispatchable_sites)
    unavailable_sites.extend(exceeds_alone)

    # THE SCRIPT EMITS THE SHARDS; THE SESSION DISPATCHES THEM VERBATIM
    # (round 3): every prior fix carefully measured a payload nothing ever
    # actually wrote, leaving the SESSION to reconstruct and serialize the
    # real dispatch payload itself -- with no guarantee its json.dumps call
    # would agree with this script's. Serialize the REAL, final bytes of
    # every shard HERE, with _serialize_shard_payload -- the exact same
    # function _pack_shards's own size probe already called -- and refuse
    # the whole build before writing anything if any one of them is over
    # budget, rather than discovering it after publishing a corpus file
    # with no matching shards.
    sites_by_shard = {}
    for site in dispatchable_sites:
        sites_by_shard.setdefault(site["shard_id"], []).append(site)
    shard_bytes_by_id = {}
    for shard_id, sites in sites_by_shard.items():
        raw_shard = _serialize_shard_payload(shard_id, shards_map[shard_id], sites)
        if len(raw_shard) > MAX_SHARD_BYTES:
            raise PrintedLabelAuditFatalError(
                f"shard {shard_id} serialises to {len(raw_shard)} bytes, over the "
                f"{MAX_SHARD_BYTES} budget -- refusing to publish any file for this build "
                "(packing's own sizing should make this unreachable; treat it as a bug)"
            )
        shard_bytes_by_id[shard_id] = raw_shard

    doc = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "durable_root": str(durable_root),
        "mode": mode,
        "canon_sha256": canon_sha256,
        "converged_segments": converged_segments,
        "drafts_excluded_stale_review": drafts_excluded_stale_review,
        "carriers_scanned": carriers_scanned,
        "draft_content_sha1": draft_content_sha1_map,
        "dispatchable_sites": dispatchable_sites,
        "unavailable_sites": unavailable_sites,
        "shards": shards_map,
        "should_dispatch": len(dispatchable_sites) > 0,
    }
    raw = json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8") + b"\n"

    out_path = Path(out_str) if out_str else (
        durable_root / "printed_label_audit"
        / f"corpus_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{os.urandom(4).hex()}.json"
    )
    shard_dir = out_path.parent

    # SHARDS FIRST, CORPUS LAST. The corpus is the commit point: its
    # digest is what the session holds and what --report anchors every
    # later check to. Publishing it before the shards meant a mid-way
    # shard-write failure left a create-once corpus on disk referencing
    # shards that do not exist, permanently wedging a re-run under an
    # explicit --out (create-once refuses to publish the SAME path
    # twice). Publishing shards first means the same failure instead
    # leaves only unreferenced shard files nothing has pointed at yet --
    # inert, and a re-run (a fresh --out by default) is unaffected.
    shard_paths_by_id = {}
    for shard_id, raw_shard in shard_bytes_by_id.items():
        shard_path = shard_dir / f"{out_path.stem}.{shard_id}.json"
        _atomic_publish_create_once(shard_path, raw_shard)
        shard_paths_by_id[shard_id] = shard_path

    # POST-WRITE VERIFY, before the corpus is published: re-read every
    # emitted shard's size FROM DISK, not from the bytes already held in
    # memory -- the invariant check that makes the next drift of this
    # class impossible to ship rather than merely unlikely. A future edit
    # that changes what gets WRITTEN without also changing what was SIZED
    # (exactly round 3's own defect, one layer down) is caught here even
    # if this function's own logic missed it -- and because this runs
    # BEFORE the corpus write, a failure here still leaves no corpus file
    # referencing them.
    for shard_id, shard_path in shard_paths_by_id.items():
        on_disk_size = shard_path.stat().st_size
        if on_disk_size > MAX_SHARD_BYTES:
            raise PrintedLabelAuditFatalError(
                f"shard file {shard_path} is {on_disk_size} bytes on disk, over the "
                f"{MAX_SHARD_BYTES} budget -- refusing this build even though the file "
                "was already written"
            )

    _atomic_publish_create_once(out_path, raw)

    return {
        "success": True,
        "mode": "build-corpus",
        "corpus_path": str(out_path),
        "corpus_sha256": hashlib.sha256(raw).hexdigest(),
        "canon_sha256": canon_sha256,
        "entity_markup_mode": mode,
        "converged_segments": converged_segments,
        "drafts_excluded_stale_review": drafts_excluded_stale_review,
        "carriers_scanned": carriers_scanned,
        "dispatchable_sites": len(dispatchable_sites),
        "unavailable_sites": len(unavailable_sites),
        "shards": len(shards_map),
        "shard_dir": str(shard_dir),
        "should_dispatch": doc["should_dispatch"],
    }


# ---------------------------------------------------------------------------
# --report: folded validate-and-render.
# ---------------------------------------------------------------------------


def _schema_validator() -> "jsonschema.Draft202012Validator":
    schema_path = SCHEMAS_DIR / SCHEMA_FILENAME
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise PrintedLabelAuditFatalError(f"schema file not found: {schema_path}")
    except OSError as exc:
        raise PrintedLabelAuditFatalError(f"schema file {schema_path} could not be read: {exc}")
    except json.JSONDecodeError as exc:
        raise PrintedLabelAuditFatalError(f"invalid JSON in schema {schema_path.name}: {exc}")
    return jsonschema.Draft202012Validator(schema)


def _load_corpus(corpus_path: Path, expected_sha256: str) -> dict:
    """Reads, hash-anchors and structurally validates the corpus file the
    session serialised into the dispatch's prompt. THE HASH CHECK is the
    real trust boundary here, not a schema: this script wrote every byte
    of the corpus itself at --build-corpus time (already schema-shaped by
    construction), so bytes that hash to `expected_sha256` -- the digest
    the session computed BEFORE dispatching and holds in its own context,
    never a digest recomputed from the file the dispatched pass can also
    write to -- are guaranteed byte-identical to what this script already
    produced. The isinstance checks below are defence in depth against
    corruption between build and report, never the primary gate."""
    raw, doc = _read_json_bytes(corpus_path, "printed-label audit corpus")
    actual = hashlib.sha256(raw).hexdigest()
    if actual != expected_sha256:
        raise PrintedLabelAuditRefusal(
            f"{corpus_path} does not match --expect-corpus-sha256: the session "
            f"dispatched {expected_sha256}, the file on disk now hashes to {actual}"
        )
    required = (
        "mode", "durable_root", "canon_sha256", "draft_content_sha1", "dispatchable_sites",
        "unavailable_sites", "shards",
    )
    missing = [k for k in required if k not in doc]
    if missing:
        raise PrintedLabelAuditRefusal(
            f"{corpus_path} is missing required field(s) {missing} -- not a corpus this "
            "script produced"
        )
    for field, expected_type in (
        ("dispatchable_sites", list), ("unavailable_sites", list),
        ("shards", dict), ("draft_content_sha1", dict),
    ):
        if not isinstance(doc[field], expected_type):
            raise PrintedLabelAuditRefusal(
                f"{corpus_path} has a field of the wrong shape: {field} is a "
                f"{type(doc[field]).__name__}, not a {expected_type.__name__}"
            )
    for site in doc["dispatchable_sites"]:
        if not isinstance(site, dict) or "site_id" not in site or "shard_id" not in site:
            raise PrintedLabelAuditRefusal(
                f"{corpus_path} has a dispatchable site missing site_id/shard_id"
            )
    return doc


def run_report(corpus_str: str, expect_corpus_sha256: str, attempt_strs: list) -> dict:
    """Validates the dispatched pass's output and, only if every check
    passes, renders the report -- one invocation, no artifact written.
    Enforces, in order:

      a. the corpus file's current bytes hash to --expect-corpus-sha256
         (see _load_corpus's own docstring for why this is the real trust
         boundary, not a schema check on the corpus).
      b. canon_sha256 still matches canon.json's current bytes on disk,
         and every participating draft's draft_content_sha1 still matches
         -- an ordinary edit during the dispatch invalidates the attempt.
      c. every --attempt file is schema-valid against
         printed-label-audit.schema.json.
      d. the shard set is complete and exact: the set of shard_ids across
         every --attempt equals exactly the corpus's own declared set, no
         shard missing/extra/duplicated, and each shard's shard_digest
         matches the corpus's own recorded digest for that shard_id.
      e. anchoring, scoped by verdict kind: a matches_frozen_target
         verdict must reproduce a (site_id, tag, label, source_form,
         canonical_target_form) five-tuple the corpus actually carries for
         that site; a no_canon_match verdict must reproduce (site_id, tag,
         label) only. A site answered under the wrong shard_id is fatal.
      f. cardinality: exactly one verdict per dispatchable site, no
         duplicate (within or across shards), every dispatchable site
         answered.

    On success, renders every dispatchable site (with its verdict) and
    every unavailable site (with its reason) -- a book with unavailable
    sites can never read as completely audited. Any failure renders
    NOTHING and raises (main() turns that into exit 2, no stdout)."""
    corpus_path = Path(corpus_str)
    doc = _load_corpus(corpus_path, expect_corpus_sha256)

    # --report is self-anchored to THIS install's own durable root (never a
    # --durable-root override -- canon_harmonisation.py's own module
    # docstring explains why a pipeline step should not take one), and
    # that is deliberate house convention, kept here. But nothing
    # previously compared the corpus's OWN recorded durable_root against
    # it: a corpus built from a DIFFERENT root re-checks canon.json and
    # every draft against the wrong install, and the resulting refusal
    # blames the wrong cause -- "draft ... changed since this corpus was
    # built" when no draft was ever touched. Catch the actual cause first.
    if doc["durable_root"] != str(DURABLE_ROOT):
        raise PrintedLabelAuditRefusal(
            f"{corpus_path} was gathered from a DIFFERENT durable root "
            f"({doc['durable_root']!r}) than this install's own "
            f"({str(DURABLE_ROOT)!r}) -- canon.json and every draft would be re-checked "
            "against the wrong project"
        )

    final_audit = _load_sibling("final_audit")

    canon_bytes, _entries = _load_canon(CANON_PATH)
    canon_sha256 = hashlib.sha256(canon_bytes).hexdigest()
    if doc["canon_sha256"] != canon_sha256:
        raise PrintedLabelAuditRefusal(
            f"canon.json changed since this corpus was built: the corpus was gathered "
            f"against {doc['canon_sha256']!r}, canon.json now hashes to {canon_sha256!r}"
        )

    for seg, expected_sha1 in doc["draft_content_sha1"].items():
        # Built from THIS script's own self-anchored DURABLE_ROOT, never
        # from final_audit.draft_path(seg) -- see the identical comment in
        # build_corpus() for why the two can diverge.
        draft_file = DURABLE_ROOT / "segments" / f"{seg}.draft.json"
        try:
            current_sha1 = final_audit.draft_content_sha1(draft_file)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise PrintedLabelAuditRefusal(
                f"draft for segment {seg!r} ({draft_file}) could not be re-read ({exc}) -- "
                "it moved or was deleted since this corpus was built"
            )
        if current_sha1 != expected_sha1:
            raise PrintedLabelAuditRefusal(
                f"draft for segment {seg!r} ({draft_file}) changed since this corpus was "
                f"built: expected {expected_sha1!r}, now {current_sha1!r}"
            )

    corpus_shards = doc["shards"]
    corpus_sites_by_id = {s["site_id"]: s for s in doc["dispatchable_sites"]}

    validator = _schema_validator()
    seen_shard_ids = set()
    all_verdicts_by_site = {}

    for attempt_str in attempt_strs:
        attempt_path = Path(attempt_str)
        _raw, attempt_doc = _read_json_bytes(attempt_path, "attempt shard")
        # min(), not sorted()[0]: an untrusted attempt file can carry an
        # unbounded number of malformed verdicts, and sorted() drains and
        # materialises the WHOLE iter_errors() generator just to read its
        # first element -- measured at 548 MB peak RSS for one schema-
        # invalid attempt with 100,000 bad verdicts. min() with the same
        # key returns the first among equal keys exactly as a stable sort
        # would (Python's min() is stable: the first minimal element in
        # iteration order wins ties), so behaviour is unchanged, and only
        # ONE error object is ever held at a time.
        first = min(
            validator.iter_errors(attempt_doc), key=lambda e: [str(p) for p in e.path], default=None
        )
        if first is not None:
            loc = "/".join(str(p) for p in first.path) or "<root>"
            raise PrintedLabelAuditRefusal(
                f"{attempt_path} failed schema validation at '{loc}': {first.message}"
            )

        shard_id = attempt_doc["shard_id"]
        if shard_id in seen_shard_ids:
            raise PrintedLabelAuditRefusal(
                f"{attempt_path}: shard_id {shard_id!r} was already answered by another "
                "--attempt file -- a shard may be answered only once"
            )
        seen_shard_ids.add(shard_id)
        if shard_id not in corpus_shards:
            raise PrintedLabelAuditRefusal(
                f"{attempt_path}: shard_id {shard_id!r} is not one the corpus declared"
            )
        if attempt_doc["shard_digest"] != corpus_shards[shard_id]:
            raise PrintedLabelAuditRefusal(
                f"{attempt_path}: shard_digest for {shard_id!r} does not match the corpus's "
                f"own recorded digest for that shard -- the pass was not shown this shard "
                "as the session dispatched it"
            )

        seen_site_ids_this_shard = set()
        for verdict in attempt_doc["verdicts"]:
            site_id = verdict["site_id"]
            if site_id in seen_site_ids_this_shard:
                raise PrintedLabelAuditRefusal(
                    f"{attempt_path}: site_id {site_id!r} is answered twice within shard "
                    f"{shard_id!r}"
                )
            seen_site_ids_this_shard.add(site_id)
            if site_id in all_verdicts_by_site:
                raise PrintedLabelAuditRefusal(
                    f"site_id {site_id!r} is answered by more than one --attempt file"
                )

            corpus_site = corpus_sites_by_id.get(site_id)
            if corpus_site is None:
                raise PrintedLabelAuditRefusal(
                    f"{attempt_path}: site_id {site_id!r} does not exist in the corpus"
                )
            if corpus_site["shard_id"] != shard_id:
                raise PrintedLabelAuditRefusal(
                    f"{attempt_path}: site_id {site_id!r} belongs to shard "
                    f"{corpus_site['shard_id']!r}, not {shard_id!r} -- answered under the "
                    "wrong shard"
                )
            if verdict["tag"] != corpus_site["tag"] or verdict["label"] != corpus_site["label"]:
                raise PrintedLabelAuditRefusal(
                    f"{attempt_path}: site_id {site_id!r} verdict's tag/label does not match "
                    "the corpus's own recorded tag/label for that site"
                )
            if verdict["verdict"] == "matches_frozen_target":
                pair = (verdict["source_form"], verdict["canonical_target_form"])
                known_pairs = {(r["source_form"], r["canonical_target_form"]) for r in corpus_site["canon_rows"]}
                if pair not in known_pairs:
                    raise PrintedLabelAuditRefusal(
                        f"{attempt_path}: site_id {site_id!r} claims "
                        f"(source_form, canonical_target_form) = {pair!r}, which the corpus "
                        "never offered as a canon row for that site"
                    )

            all_verdicts_by_site[site_id] = verdict

    declared_shard_ids = set(corpus_shards.keys())
    if seen_shard_ids != declared_shard_ids:
        missing_shards = sorted(declared_shard_ids - seen_shard_ids)
        extra_shards = sorted(seen_shard_ids - declared_shard_ids)
        raise PrintedLabelAuditRefusal(
            f"the --attempt shard set does not match the corpus's own declared set -- "
            f"missing: {missing_shards}, extra: {extra_shards}"
        )

    unanswered = sorted(set(corpus_sites_by_id) - set(all_verdicts_by_site))
    if unanswered:
        raise PrintedLabelAuditRefusal(
            f"{len(unanswered)} dispatchable site(s) were never answered by any --attempt "
            f"file: {unanswered[:5]}{'...' if len(unanswered) > 5 else ''}"
        )

    sites_total = len(corpus_sites_by_id)
    if sites_total != len(all_verdicts_by_site):
        # Unreachable given the checks above (every dispatchable site is
        # matched exactly once by construction), kept as a fail-closed
        # self-check per plan §4/§8's "refuse an implausible zero/mismatch"
        # requirement rather than trusting the invariant silently.
        raise PrintedLabelAuditFatalError(
            "internal invariant broken: sites_total does not equal the number of verdicts "
            "collected -- refusing to render a possibly-incomplete report"
        )

    rendered_sites = []
    matches_count = 0
    for site_id in sorted(corpus_sites_by_id):
        site = corpus_sites_by_id[site_id]
        verdict = all_verdicts_by_site[site_id]
        rendered = dict(site)
        rendered.pop("shard_id", None)
        rendered["verdict"] = verdict["verdict"]
        if verdict["verdict"] == "matches_frozen_target":
            rendered["source_form"] = verdict["source_form"]
            rendered["canonical_target_form"] = verdict["canonical_target_form"]
            matches_count += 1
        rendered_sites.append(rendered)

    return {
        "success": True,
        "mode": "report",
        "entity_markup_mode": doc["mode"],
        "sites_total": sites_total,
        "matches_frozen_target_count": matches_count,
        "no_canon_match_count": sites_total - matches_count,
        "unavailable_sites_total": len(doc["unavailable_sites"]),
        "sites": rendered_sites,
        "unavailable_sites": doc["unavailable_sites"],
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Report-only audit (#929) for printed entity-markup labels that re-spell a "
            "canon.json source term under a different frozen target. See this file's own "
            "module docstring for the full contract."
        ),
    )
    parser.add_argument(
        "--build-corpus", action="store_true",
        help="Gather every well-formed marked span across every converged, review-current "
             "draft into the corpus file --report is later validated against. Mutually "
             "exclusive with --report.",
    )
    parser.add_argument(
        "--durable-root", metavar="DIR", default=None,
        help="REQUIRED with --build-corpus: base directory to gather from -- stated "
             "explicitly rather than self-anchored, matching this script's frozen CLI "
             "contract.",
    )
    parser.add_argument(
        "--out", metavar="PATH", default=None,
        help="Only with --build-corpus: where to write the corpus file (default: "
             "{durable_root}/printed_label_audit/corpus_<UTC>_<8 hex>.json).",
    )
    parser.add_argument(
        "--report", action="store_true",
        help="Validate the dispatched judging pass's output and, only if every check "
             "passes, render the operator report -- one invocation, writes nothing. "
             "Mutually exclusive with --build-corpus.",
    )
    parser.add_argument(
        "--corpus", metavar="PATH", default=None,
        help="REQUIRED with --report: the corpus file the session serialised into the "
             "dispatch's prompt.",
    )
    parser.add_argument(
        "--expect-corpus-sha256", metavar="HEX", default=None,
        help="REQUIRED with --report: the sha256 the session computed over --corpus BEFORE "
             "dispatching.",
    )
    parser.add_argument(
        "--attempt", metavar="PATH", action="append", default=None,
        help="Only with --report: one file per shard the corpus declared. Repeatable; may "
             "be omitted entirely when the corpus declared zero shards.",
    )
    return parser


def parse_args(argv=None) -> argparse.Namespace:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    have_build = args.build_corpus
    have_report = args.report
    if have_build and have_report:
        parser.error("--build-corpus and --report are mutually exclusive -- pass exactly one.")
    if not (have_build or have_report):
        parser.error("nothing to do -- pass --build-corpus or --report. See --help.")

    if have_build:
        report_only = [
            ("--corpus", args.corpus),
            ("--expect-corpus-sha256", args.expect_corpus_sha256),
            ("--attempt", args.attempt),
        ]
        offending = [name for name, value in report_only if value is not None]
        if offending:
            parser.error(f"{', '.join(offending)} only valid with --report, not --build-corpus.")
        if args.durable_root is None:
            parser.error(
                "--durable-root required with --build-corpus -- this script's frozen CLI "
                "contract states it explicitly rather than self-anchoring it."
            )
        return args

    # have_report
    build_only = [("--durable-root", args.durable_root), ("--out", args.out)]
    offending = [name for name, value in build_only if value is not None]
    if offending:
        parser.error(f"{', '.join(offending)} only valid with --build-corpus, not --report.")
    missing = [
        name for name, value in (
            ("--corpus", args.corpus),
            ("--expect-corpus-sha256", args.expect_corpus_sha256),
        ) if value is None
    ]
    if missing:
        parser.error(
            f"{', '.join(missing)} required with --report -- verdicts are checked against "
            "the corpus the session dispatched, not against live state alone."
        )
    if not re.fullmatch(r"[0-9a-f]{64}", args.expect_corpus_sha256):
        parser.error("--expect-corpus-sha256 must be 64 lowercase hex characters.")
    return args


def main(argv=None) -> int:
    args = parse_args(argv)
    try:
        if args.build_corpus:
            summary = build_corpus(args.durable_root, args.out)
        else:
            summary = run_report(args.corpus, args.expect_corpus_sha256, args.attempt or [])
    except PrintedLabelAuditRefusal as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        if e.offending is not None:
            print(f"offending: {e.offending!r}", file=sys.stderr)
        return 2
    except PrintedLabelAuditFatalError as e:
        print(f"FATAL: {e}", file=sys.stderr)
        if e.offending is not None:
            print(f"offending: {e.offending!r}", file=sys.stderr)
        return 2

    print(dumps_line(summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())
