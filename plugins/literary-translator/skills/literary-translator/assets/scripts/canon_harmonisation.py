#!/usr/bin/env python3
"""canon_harmonisation.py -- structural check + advisory report for
canon_harmonisation.json, the whole-canon target-form harmonisation sidecar
(#823). See references/canon-and-glossary.md for the authoritative narrative
this script's behavior must match -- why a sidecar and not review_queue[],
the artifact shape, and the W-step that dispatches the pass this script
checks and reports on.

WHAT THIS CLOSES. Nothing anywhere else in this plugin compares
canonical_target_form ACROSS canon.json entries{} -- glossary batches are
adjudicated blind of one another, and every existing cross-entry check
(suspicion_scan.py's merge_participant/near_merge, final_audit.py's WARN
glossary-diff) groups by source_form, never by target -- so a frozen canon
can carry one referent under two spellings, or two policy-legitimate
renderings of the same underlying name, with every shipped gate reporting
success. A dispatched codex pass reads the WHOLE canon['entries'] once and
proposes harmonisations; this script is the structural gate on that pass's
output (--check) and the read-only renderer for a human/agent operator
(--report). Neither mode, nor the dispatched pass itself, ever decides that
two forms denote the same referent -- THE IRON RULE, enforced here by
construction: every check below is mechanical (schema shape, byte-exact
anchoring against canon.json's own entries{}, cardinality, duplication),
never an accuracy/identity call. Acting on a proposal is the EXISTING,
unrelated canon_validate.py --correct route, always operator-driven.

TWO MUTUALLY EXCLUSIVE MODES (passing neither, or both, is a usage error:
argparse prints to stderr and exits 2)
--------------------------------------------------------------------------
  --check PATH [--approve-to DEST]
      Purely structural validation of the ATTEMPT file a dispatched pass
      just wrote (a per-attempt path, never the durable sidecar directly --
      see the SKILL.md W-step for why one dispatch needs a fresh path).
      Self-anchored: reads canon.json and the schemas directory from THIS
      script's own durable_root, never from a flag -- there is no
      --durable-root/--canon-path/--schemas-dir override on --check, unlike
      every other override-bearing mode in this plugin, because a pipeline
      W-step always runs against its one true install layout and a typo'd
      override here would silently check a proposal against the wrong
      canon.

      Enforces, in order:
        a. PATH is schema-valid against canon-harmonisation.schema.json
           (a blank note, an unknown kind, a missing field, or a stray key
           is caught HERE, by the schema, not by procedural code below).
        b. canon_sha256 equals the sha256 of canon.json's raw bytes on
           disk -- refuses an artifact anchored to a different canon than
           the one currently frozen.
        c. every proposal has >= 2 members.
        d. every members[].source_form is a BYTE-EXACT key of
           canon['entries'] (never folded, casefolded, or NFC/NFD-
           normalised -- a form matching a canon key only after
           normalisation is refused, exactly like canon_link_groups.py's
           own membership rule).
        e. every members[].canonical_target_form equals
           canon['entries'][source_form]['canonical_target_form'] byte-
           exact -- the anti-fabrication check: an artifact that misquotes
           the canon it was anchored to is refused.
        f. within one proposal, source_forms are pairwise distinct, AND at
           least 2 distinct canonical_target_form values appear among its
           members -- a proposal whose members already agree has nothing
           to harmonise.
        g. no two proposals share both the same kind and the same set of
           member source_forms.

      Exit 1 on any violation of (a)-(g). Failures under (c), (f) and (g)
      name the offending proposal's INDEX; failures under (d)/(e) name the
      offending proposal's index AND the source_form at fault. (a) and (b)
      are whole-DOCUMENT failures -- they can fire with zero proposals
      present at all -- and name the document, never an index. Exit 2 on
      canon.json absent/unreadable/not a JSON object (or missing/malformed
      entries{}), PATH absent/unreadable/not JSON, or the schemas directory
      absent. On EVERY non-zero exit, stdout carries NO JSON at all --
      every human-readable detail, including the offending index/form,
      goes to stderr only (the review_artifact_check.py/skeptic_report.py
      discipline: nothing on stdout can ever be mistaken for a schema-
      conforming result).

      --approve-to DEST is the ONLY write this script performs, and it
      happens ONLY on a full PASS: the EXACT bytes read from PATH (never
      re-serialised) are published atomically to DEST (write a temp file
      in DEST's own directory, fsync it, then os.replace() over whatever
      was there). On every exit-1 and exit-2 path DEST is left untouched --
      not created if absent, byte-identical if already present. Unlike
      canon_validate.py's create-once _write_approved_snapshot (a citation
      reviewer's one-shot fragment approval, refused on a second, DIFFERENT
      approval), this publish OVERWRITES: DEST is the durable sidecar
      itself, and a later, re-approved artifact is meant to replace an
      earlier one -- a project can run this pass more than once over its
      life, and only the newest approved read should be what --report
      renders.

      stdout on success, exactly one JSON line:
        {"success": true, "mode": "check", "proposals_count": N,
         "entries_in_canon": M, "canon_sha256": "<hex>",
         "approved_to": "<path>" | null}
      entries_in_canon is always computed fresh from canon.json on disk,
      never read from the artifact -- a model-authored coverage count is
      not evidence of coverage (see the SKILL.md W-step's own reasoning for
      why no entries_examined field exists in the artifact at all).

  --report [--harmonisation PATH] [--durable-root DIR] [--canon-path P]
           [--schemas-dir D]
      Read-only render for a human or an orchestrating agent. Unlike
      --check, this is a human-run reporting command, not a pipeline
      W-step bound to one fixed install layout, so --durable-root/
      --canon-path/--schemas-dir overrides ARE offered here (mirrors
      skeptic_report.py's own reasoning for the same asymmetry).

      Schema-validates the artifact FIRST -- a foreign/corrupt artifact
      fails LOUD, exit 2, no stdout JSON, the skeptic_report.py discipline
      -- then renders to stderr, per proposal: the kind, each member's
      source_form -> canonical_target_form read FRESH from canon.json
      (never from the artifact, which may have gone stale), the note, and
      a copy-pasteable canon_validate.py --correct skeleton naming the
      member. A member whose source_form is no longer a key of entries{}
      renders as "REMOVED (was: <the target stored in the artifact>)" --
      --correct's own "remove" disposition deletes an entry outright, so
      there is no fresh target left to read, and the row must neither
      crash nor silently vanish. THE IRON RULE holds here too: the printed
      --correct skeleton never fills in which spelling should win -- that
      is exactly the identity call this script may never make -- its
      new_entry.canonical_target_form is printed as null, which
      canon-entry.schema.json types as a string and canon_validate.py
      --correct therefore refuses outright, so an operator who pastes the
      skeleton UNEDITED gets a fail-closed refusal instead of the literal
      placeholder silently freezing as canon.

      `note` is free LLM-authored prose, and the only artifact-supplied
      string this renderer prints as plain text rather than through
      repr(). Before rendering (both the bare note line and the same note
      text embedded in a --correct skeleton's own `reason`), every
      str.splitlines() boundary character it may carry -- a newline,
      U+2028/U+2029/U+0085, and their siblings, all schema-valid since the
      schema only requires non-blank -- is escaped to a visible \\uXXXX
      form, so a model-authored note can never open a fresh physical line
      at column zero, where this script's own report markers and
      --correct skeletons live, and forge either one.

      Always exits 0 on a structurally valid artifact, WHATEVER it
      contains -- this mode reports, it never blocks. Reports
      canon_current: false when the artifact's canon_sha256 no longer
      matches canon.json's current bytes (the proposal set may be stale,
      but every member target rendered is still read fresh from disk).
      When proposals is empty, the render says so explicitly: a model's
      absence of findings is not a certificate that the canon is
      consistent.

      stdout on success, exactly one JSON line:
        {"success": true, "mode": "report", "proposals_count": N,
         "entries_in_canon": M, "canon_current": true|false}

Exit codes throughout: 0 clean, 1 gate-fail (--check only), 2 fatal.
"""

import argparse
import hashlib
import json
import os
import sys
import unicodedata
from pathlib import Path

try:
    import jsonschema
    from referencing import Registry, Resource
except ImportError as e:
    sys.stderr.write(
        "canon_harmonisation.py requires the 'jsonschema' package (>=4.26.0), "
        "which pulls in 'referencing' for schema registration by $id. Install "
        "with:\n\n"
        "    pip install -r requirements.txt\n\n"
        "(or directly: pip install 'jsonschema>=4.26.0')\n\n"
        f"(import error: {e})\n"
    )
    # A missing dependency is a FATAL deployment fault, never a gate-fail --
    # this plugin's 0 clean / 1 gate-fail (--check only) / 2 fatal convention
    # (see this module's own docstring, "Exit codes throughout").
    sys.exit(2)

# --- the shared one-line JSON serialiser (#369) -----------------------------
# Loaded by EXACT PATH, never `import json_stdout`. A bare sibling import
# resolves through the global sys.modules cache regardless of which staged
# copy the CALLER intended, so one process that stages several durable roots
# would bind the FIRST root's copy for all of them. Load-bearing here, not
# stylistic: `note` is free LLM-authored prose and may carry a literal
# U+2028/U+2029/U+0085, which json.dumps leaves raw and which turns one
# stdout line into two for the agent reading it.
import importlib.util as _importlib_util

# --report is documented (this module's own docstring) as a read-only render.
# Left unset, the dynamic load below would let CPython's default
# SourceFileLoader write a __pycache__/json_stdout....pyc into this file's own
# directory -- ${durable_root}/scripts/ -- on every run. Set process-wide,
# before the one dynamic load this script performs, since nothing else this
# script imports needs bytecode caching either.
sys.dont_write_bytecode = True

_JSON_STDOUT_PATH = Path(__file__).absolute().parent / "json_stdout.py"
try:
    _json_stdout_spec = _importlib_util.spec_from_file_location(
        "json_stdout", _JSON_STDOUT_PATH
    )
    if _json_stdout_spec is None or _json_stdout_spec.loader is None:
        raise ImportError(f"no loader for {_JSON_STDOUT_PATH}")
    _json_stdout = _importlib_util.module_from_spec(_json_stdout_spec)
    # OSError, not ImportError alone: spec_from_file_location() happily builds
    # a spec for a file that is not there, and it is exec_module() that
    # raises FileNotFoundError when it opens the source.
    _json_stdout_spec.loader.exec_module(_json_stdout)
except (ImportError, OSError) as _json_stdout_exc:  # pragma: no cover - staging error path
    # FATAL deployment fault (a missing scripts/ sibling), not a gate-fail --
    # stderr only, exit 2, no stdout, matching the jsonschema/referencing
    # ImportError branch above and this module's own 0/1/2 convention.
    # sys.exit(str) would print this text but always exit 1, so the message
    # is written explicitly instead.
    sys.stderr.write(
        f"canon_harmonisation.py: cannot load json_stdout.py from {_JSON_STDOUT_PATH} "
        f"({_json_stdout_exc}).\n"
        "json_stdout.py must be installed alongside canon_harmonisation.py under "
        "${durable_root}/scripts/ -- Step 0a's copy pass places it there.\n"
    )
    sys.exit(2)

dumps_line = _json_stdout.dumps_line

# ---------------------------------------------------------------------------
# Self-anchoring: this script always lives at {durable_root}/scripts/<name>.py.
# It never assumes cwd == durable_root. --check never takes a --durable-root
# override (see module docstring); --report's overrides are resolved from
# these same defaults in main().
# ---------------------------------------------------------------------------
SCRIPTS_DIR = Path(__file__).resolve().parent
DURABLE_ROOT = SCRIPTS_DIR.parent
SCHEMAS_DIR = DURABLE_ROOT / "schemas"
CANON_PATH = DURABLE_ROOT / "canon.json"
HARMONISATION_FILENAME = "canon_harmonisation.json"
HARMONISATION_PATH = DURABLE_ROOT / HARMONISATION_FILENAME
SCHEMA_FILENAME = "canon-harmonisation.schema.json"


class CanonHarmonisationFatalError(Exception):
    """Exit 2: an I/O or structural-input problem that stops this script
    from even attempting the check/report -- an unreadable/absent
    canon.json, an unreadable/non-JSON artifact, or a missing schemas
    directory/schema file. `offending`, when not None, is the exact value
    at fault, so main() never has to re-derive it from the message."""

    def __init__(self, message, offending=None):
        super().__init__(message)
        self.offending = offending


class CanonHarmonisationRefusal(Exception):
    """Exit 1 (--check only): the artifact is well-formed JSON but fails one
    of the structural gate checks (a)-(g) documented in this module's
    docstring. `offending`, when not None, names the proposal index (and,
    for a source_form-level failure, the form itself) so main() never has
    to re-derive it from the message."""

    def __init__(self, message, offending=None):
        super().__init__(message)
        self.offending = offending


# ---------------------------------------------------------------------------
# canon.json / artifact loading -- shared by both modes.
# ---------------------------------------------------------------------------


def _read_json_bytes(path: Path, label: str):
    """Reads `path` and parses it as a JSON object. Returns (raw_bytes, doc).
    Raises CanonHarmonisationFatalError (exit 2 in both modes) on every
    failure: absent, unreadable, not valid UTF-8, not valid JSON, or not a
    JSON object at the top level."""
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        raise CanonHarmonisationFatalError(f"{label} not found at {path}")
    except OSError as exc:
        raise CanonHarmonisationFatalError(f"{label} at {path} could not be read: {exc}")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CanonHarmonisationFatalError(f"{label} at {path} is not valid UTF-8: {exc}")
    try:
        doc = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CanonHarmonisationFatalError(f"{label} at {path} is not valid JSON: {exc}")
    if not isinstance(doc, dict):
        raise CanonHarmonisationFatalError(
            f"{label} at {path} did not parse to a JSON object (got {type(doc).__name__})"
        )
    return raw, doc


def _load_canon(canon_path: Path):
    """Returns (raw_bytes, doc, entries) for canon.json at `canon_path`.
    Raises CanonHarmonisationFatalError on anything short of a readable
    JSON object carrying an entries{} mapping -- this script trusts
    canon_validate.py's own validate-only pass to have already enforced
    canon-file.schema.json's full shape; it only needs entries{} itself to
    be present and dict-shaped to anchor and look up against."""
    raw, doc = _read_json_bytes(canon_path, "canon.json")
    entries = doc.get("entries")
    if not isinstance(entries, dict):
        raise CanonHarmonisationFatalError(
            f"canon.json at {canon_path} has no entries{{}} mapping "
            f"(got {type(entries).__name__ if entries is not None else 'missing'})"
        )
    return raw, doc, entries


# ---------------------------------------------------------------------------
# Schema loading / registry (mirrors canon_validate.py::_build_schema_registry
# exactly -- every *.schema.json under the schemas directory is registered by
# its own $id, a bare filename, so this file's own $ref-free schema resolves
# the same way every sibling canon-*.schema.json does).
# ---------------------------------------------------------------------------


def _load_schema_document(schema_path: Path) -> dict:
    try:
        text = schema_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise CanonHarmonisationFatalError(f"schema file not found: {schema_path}")
    except OSError as exc:
        raise CanonHarmonisationFatalError(f"schema file {schema_path} could not be read: {exc}")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise CanonHarmonisationFatalError(f"invalid JSON in schema {schema_path.name}: {exc}")


def _build_schema_registry(schemas_dir: Path) -> "Registry":
    if not schemas_dir.is_dir():
        raise CanonHarmonisationFatalError(f"schemas directory not found: {schemas_dir}")
    resources = []
    for schema_file in sorted(schemas_dir.glob("*.schema.json")):
        contents = _load_schema_document(schema_file)
        schema_id = contents.get("$id", schema_file.name)
        resources.append((schema_id, Resource.from_contents(contents)))
    if not resources:
        raise CanonHarmonisationFatalError(f"no *.schema.json files found under {schemas_dir}")
    return Registry().with_resources(resources)


def _schema_errors(doc: dict, schemas_dir: Path):
    """Validates `doc` against canon-harmonisation.schema.json (registered
    from `schemas_dir`, alongside every sibling schema there) and returns
    the sorted list of jsonschema ValidationErrors (empty if valid). Raises
    CanonHarmonisationFatalError (exit 2, both modes) if the schemas
    directory or the schema file itself cannot be loaded -- a missing
    schema is a deployment problem, never a gate-fail."""
    registry = _build_schema_registry(schemas_dir)
    schema = _load_schema_document(schemas_dir / SCHEMA_FILENAME)
    validator = jsonschema.Draft202012Validator(schema, registry=registry)
    return sorted(validator.iter_errors(doc), key=lambda e: [str(p) for p in e.path])


# ---------------------------------------------------------------------------
# --check: the structural gate, (a)-(g) from the module docstring.
# ---------------------------------------------------------------------------


def _check_anchor(doc: dict, canon_sha256: str, path: Path) -> None:
    """(b): the artifact's own canon_sha256 must equal the sha256 of
    canon.json's CURRENT bytes on disk."""
    if doc["canon_sha256"] != canon_sha256:
        raise CanonHarmonisationRefusal(
            f"{path}: canon_sha256 {doc['canon_sha256']!r} does not match the sha256 of "
            f"canon.json's current bytes ({canon_sha256!r}) -- this artifact is anchored to "
            "a different canon than the one currently on disk"
        )


def _first_duplicate(items):
    """Returns the first item that recurs in `items` (in iteration order),
    or None if every item is unique. Small enough to inline, but named so
    the (f) pairwise-distinct check below reads as one line."""
    seen = set()
    for item in items:
        if item in seen:
            return item
        seen.add(item)
    return None


def _check_proposals(doc: dict, entries: dict) -> None:
    """(c)-(g): every proposal-level and cross-proposal structural check.
    Raises CanonHarmonisationRefusal on the FIRST violation found, always
    naming the offending proposal's index (and, for (d)/(e), the
    source_form at fault) via `offending`."""
    seen_signatures = set()
    for idx, proposal in enumerate(doc["proposals"]):
        members = proposal["members"]

        # (c) cardinality, checked before any per-member lookup: a proposal
        # naming fewer than 2 members asserts nothing to harmonise.
        if len(members) < 2:
            raise CanonHarmonisationRefusal(
                f"proposals[{idx}]: has {len(members)} member(s) -- a harmonisation "
                "proposal needs at least 2 to name anything to harmonise",
                offending={"proposal_index": idx, "member_count": len(members)},
            )

        source_forms = []
        target_forms = set()
        for member in members:
            source_form = member["source_form"]
            canonical_target_form = member["canonical_target_form"]

            # (d) byte-exact membership -- a plain dict lookup never folds,
            # casefolds, or NFC/NFD-normalises, so a form matching a canon
            # key only after normalisation correctly misses here and is
            # refused rather than silently tolerated.
            if source_form not in entries:
                raise CanonHarmonisationRefusal(
                    f"proposals[{idx}]: source_form {source_form!r} is not a byte-exact "
                    "key of canon.json's entries{} (never folded, casefolded, or "
                    "NFC/NFD-normalised)",
                    offending={"proposal_index": idx, "source_form": source_form},
                )

            # (e) anti-fabrication: the artifact must not misquote the very
            # canon it claims to be anchored to.
            stored_target_form = entries[source_form].get("canonical_target_form")
            if canonical_target_form != stored_target_form:
                raise CanonHarmonisationRefusal(
                    f"proposals[{idx}]: canonical_target_form {canonical_target_form!r} for "
                    f"source_form {source_form!r} does not match canon.json's stored value "
                    f"{stored_target_form!r}",
                    offending={"proposal_index": idx, "source_form": source_form},
                )

            source_forms.append(source_form)
            target_forms.add(canonical_target_form)

        # (f) pairwise-distinct source_forms within this one proposal.
        duplicate_source_form = _first_duplicate(source_forms)
        if duplicate_source_form is not None:
            raise CanonHarmonisationRefusal(
                f"proposals[{idx}]: source_form {duplicate_source_form!r} appears more than "
                "once in members[] -- source_forms within one proposal must be pairwise "
                "distinct",
                offending={"proposal_index": idx, "source_form": duplicate_source_form},
            )

        # (f) at least 2 distinct target forms -- a proposal whose members
        # already agree on canonical_target_form has nothing to harmonise
        # (exact-equality of targets is already suspicion_scan.py's
        # merge_participant; this script's whole reason to exist is the
        # DIVERGENT case).
        if len(target_forms) < 2:
            raise CanonHarmonisationRefusal(
                f"proposals[{idx}]: every member already shares one canonical_target_form "
                f"({sorted(target_forms)!r}) -- nothing to harmonise",
                offending={"proposal_index": idx},
            )

        # (g) no two proposals may name the same kind over the same set of
        # member source_forms.
        signature = (proposal["kind"], frozenset(source_forms))
        if signature in seen_signatures:
            raise CanonHarmonisationRefusal(
                f"proposals[{idx}]: duplicate proposal -- another proposal already names "
                f"the same kind ({proposal['kind']!r}) over the same member source_forms",
                offending={"proposal_index": idx},
            )
        seen_signatures.add(signature)


def _atomic_publish(dest: Path, raw: bytes) -> None:
    """Publishes `raw` at `dest`: write a temp file in DEST's own directory,
    fsync it, then os.replace() over whatever was there. See the module
    docstring's --approve-to section for why this OVERWRITES (unlike
    canon_validate.py's create-once _write_approved_snapshot) -- DEST is the
    durable sidecar itself, and a later, re-approved artifact is meant to
    replace an earlier one, not collide with it.

    The `finally` below covers the WHOLE create/write/replace sequence, not
    just the write: a failure in os.replace() itself (e.g. DEST is an
    existing directory) used to leave the temp file behind, uncleaned, even
    though DEST itself stays untouched and uncorrupted -- the "no orphan"
    property this function's own callers rely on was claimed and false.
    `replaced` tracks whether os.replace() actually ran to completion; the
    unlink is best-effort in BOTH senses -- missing_ok=True (the SUCCESS
    path has already renamed tmp_path away by the time `finally` runs, so
    "already gone" is expected, not an error) AND a further OSError from
    the unlink itself (e.g. a permission or filesystem error) is caught
    and swallowed rather than propagated, so a failed cleanup can never
    replace the CanonHarmonisationFatalError this function is already
    raising for the primary failure -- main() only ever catches that
    exception type (plus CanonHarmonisationRefusal), so anything else
    reaching it would surface as an uncaught traceback instead of the
    named fatal."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = dest.parent / f".{dest.name}.tmp.{os.getpid()}.{os.urandom(4).hex()}"
    replaced = False
    try:
        fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        with os.fdopen(fd, "wb") as f:
            f.write(raw)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, dest)
        replaced = True
    except OSError as exc:
        raise CanonHarmonisationFatalError(
            f"--approve-to could not publish the validated snapshot at {dest}: {exc}"
        )
    finally:
        if not replaced:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass


def run_check(path_str: str, approve_to_str: "str | None") -> dict:
    path = Path(path_str)
    canon_bytes, _canon_doc, entries = _load_canon(CANON_PATH)
    canon_sha256 = hashlib.sha256(canon_bytes).hexdigest()

    raw, doc = _read_json_bytes(path, "harmonisation attempt artifact")

    errors = _schema_errors(doc, SCHEMAS_DIR)
    if errors:
        first = errors[0]
        loc = "/".join(str(p) for p in first.path) or "<root>"
        raise CanonHarmonisationRefusal(
            f"{path} failed schema validation at '{loc}': {first.message}"
        )

    _check_anchor(doc, canon_sha256, path)
    _check_proposals(doc, entries)

    approved_to = None
    if approve_to_str is not None:
        dest = Path(approve_to_str)
        _atomic_publish(dest, raw)
        approved_to = str(dest)

    return {
        "success": True,
        "mode": "check",
        "proposals_count": len(doc["proposals"]),
        "entries_in_canon": len(entries),
        "canon_sha256": canon_sha256,
        "approved_to": approved_to,
    }


# ---------------------------------------------------------------------------
# --report: read-only render.
# ---------------------------------------------------------------------------

def _sanitize_note(note: str) -> str:
    """Neutralises every control character and Unicode line/paragraph
    separator a model-authored `note` may carry, before it reaches
    operator-facing report text: _render_report's bare `note:` line, and
    the same note text embedded in a --correct skeleton's own `reason`
    field. `note` is free LLM-authored prose and the schema's only
    constraint on it is non-blank (pattern "\\S") -- so it may carry
    anything from a plain str.splitlines() boundary (newline, U+0085, ...)
    to a raw terminal control sequence (ESC, backspace, DEL, another C1
    control) with no line-boundary character involved at all. Rendered
    raw, a control sequence can erase or reposition the operator's
    terminal cursor -- painting a counterfeit report line -- or a boundary
    character can open a fresh physical line at column zero, where this
    script's OWN report markers ("[idx] kind: ...", "--correct
    skeleton(s)...") and --correct skeletons live. Forging either one and
    letting an operator mistake fabricated content for this script's own
    output is exactly the identity call THE IRON RULE reserves for the
    operator, made through the note text instead of through this script.

    THE RULE (a category, not a character list -- a list is what drifted
    across the previous round): escape every character whose Unicode
    general category is `Cc` (unicodedata.category(ch) == "Cc" -- this
    covers the whole of C0 and C1, so ESC, backspace, DEL and every other
    terminal control land here, along with the ten str.splitlines()
    boundary characters that are C0/C1 controls), PLUS U+2028 LINE
    SEPARATOR and U+2029 PARAGRAPH SEPARATOR, which are `Zl`/`Zp` rather
    than `Cc` and so need their own check.

    Escapes each such character to a visible \\uXXXX form (the same
    visible-escape convention json_stdout.dumps_line already uses for the
    codepoints json.dumps itself leaves raw) rather than stripping it, so
    the note's own content stays fully legible -- just structurally
    incapable of controlling the operator's terminal or starting a new
    physical line, whether it lands in the bare `note:` line (plain text,
    where json.dumps' own escaping never applies) or inside a --correct
    skeleton's `reason` (a JSON string, where json.dumps already escapes
    most controls but leaves U+0085/U+2028/U+2029 raw -- the same gap
    json_stdout.py exists to close on stdout)."""
    return "".join(
        f"\\u{ord(ch):04x}"
        if unicodedata.category(ch) == "Cc" or ch in (chr(0x2028), chr(0x2029))
        else ch
        for ch in note
    )


def _render_report(doc: dict, entries: dict, canon_current: bool) -> None:
    proposals = doc["proposals"]
    lines = [
        f"Canon Target-Form Harmonisation Report -- {len(proposals)} proposal(s), "
        f"{len(entries)} canon entries",
        "=" * 70,
    ]
    if not canon_current:
        lines.append(
            "WARNING: canon_sha256 in this artifact no longer matches canon.json's current "
            "bytes on disk -- canon.json has changed since this pass ran. Every member "
            "target below is still read FRESH from the current canon; the proposal set "
            "itself may now be stale."
        )
    if not proposals:
        lines.append(
            "no proposals returned -- this is a model's answer, not a certificate that "
            "the canon is consistent."
        )
    for idx, proposal in enumerate(proposals):
        lines.append(f"[{idx}] kind: {proposal['kind']}")
        # Sanitised ONCE per proposal and reused below (the bare note line
        # and every --correct skeleton's `reason`): see _sanitize_note's own
        # docstring for why `note` is the one artifact-supplied string this
        # renderer must never print raw.
        sanitized_note = _sanitize_note(proposal["note"])
        for member in proposal["members"]:
            source_form = member["source_form"]
            stored_target_form = member["canonical_target_form"]
            entry = entries.get(source_form)
            if entry is None:
                lines.append(f"    {source_form!r} -> REMOVED (was: {stored_target_form!r})")
            else:
                lines.append(
                    f"    {source_form!r} -> {entry.get('canonical_target_form')!r}"
                )
        lines.append(f"    note: {sanitized_note}")
        lines.append(
            "    --correct skeleton(s) (canon_validate.py --correct PATH; this script "
            "never decides which spelling wins -- new_entry.canonical_target_form is "
            "printed as null, which --correct refuses, so pasting the skeleton unedited "
            "fails closed instead of freezing a placeholder as canon; fill in the CHOSEN "
            "canonical_target_form yourself):"
        )
        for member in proposal["members"]:
            source_form = member["source_form"]
            entry = entries.get(source_form)
            if entry is None:
                lines.append(
                    f"      # {source_form!r} was already removed from canon.json's "
                    "entries{} -- no --correct skeleton to offer."
                )
                continue
            skeleton = {
                "source_form": source_form,
                "disposition": "correct",
                "old_entry": entry,
                # null, not a string placeholder: canon-entry.schema.json
                # types canonical_target_form as a string, so canon_validate.py
                # --correct refuses this skeleton outright until the operator
                # replaces null with the CHOSEN form -- an unedited paste fails
                # closed instead of silently freezing a placeholder as canon.
                "new_entry": {**entry, "canonical_target_form": None},
                "reason": (
                    f"harmonised per canon_harmonisation.json proposal [{idx}] "
                    f"({proposal['kind']}): {sanitized_note} -- CHOOSE ONE CANONICAL FORM "
                    "and replace new_entry.canonical_target_form (currently null, which "
                    "canon_validate.py --correct refuses) with it before re-running --correct."
                ),
            }
            lines.append(json.dumps(skeleton, ensure_ascii=False, indent=2, sort_keys=True))
    print("\n".join(lines), file=sys.stderr)


def run_report(
    harmonisation_str: "str | None",
    durable_root_str: "str | None",
    canon_path_str: "str | None",
    schemas_dir_str: "str | None",
) -> dict:
    durable_root = Path(durable_root_str) if durable_root_str else DURABLE_ROOT
    harmonisation_path = (
        Path(harmonisation_str) if harmonisation_str else durable_root / HARMONISATION_FILENAME
    )
    canon_path = Path(canon_path_str) if canon_path_str else durable_root / "canon.json"
    schemas_dir = Path(schemas_dir_str) if schemas_dir_str else durable_root / "schemas"

    _raw, doc = _read_json_bytes(harmonisation_path, "canon_harmonisation.json")

    errors = _schema_errors(doc, schemas_dir)
    if errors:
        first = errors[0]
        loc = "/".join(str(p) for p in first.path) or "<root>"
        raise CanonHarmonisationFatalError(
            f"{harmonisation_path} failed schema validation at '{loc}': {first.message}"
        )

    canon_bytes, _canon_doc, entries = _load_canon(canon_path)
    canon_sha256 = hashlib.sha256(canon_bytes).hexdigest()
    canon_current = doc["canon_sha256"] == canon_sha256

    _render_report(doc, entries, canon_current)

    return {
        "success": True,
        "mode": "report",
        "proposals_count": len(doc["proposals"]),
        "entries_in_canon": len(entries),
        "canon_current": canon_current,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Structural check (--check) and advisory report (--report) for "
            "canon_harmonisation.json, the whole-canon target-form harmonisation "
            "sidecar (#823). See this file's own module docstring for the full "
            "contract."
        ),
    )
    parser.add_argument(
        "--check", metavar="PATH", default=None,
        help="Structurally validate the attempt artifact at PATH against "
             "canon.json's current entries{}. Mutually exclusive with --report.",
    )
    parser.add_argument(
        "--approve-to", metavar="DEST", default=None,
        help="Only with --check: on a PASS, atomically publish PATH's exact bytes "
             "to DEST (the durable sidecar). Writes nothing on failure.",
    )
    parser.add_argument(
        "--report", action="store_true",
        help="Read-only render of the durable sidecar (or --harmonisation PATH) "
             "for a human/agent operator. Mutually exclusive with --check.",
    )
    parser.add_argument(
        "--harmonisation", metavar="PATH", default=None,
        help=f"Only with --report: override the artifact path (default: "
             f"{{durable_root}}/{HARMONISATION_FILENAME}).",
    )
    parser.add_argument(
        "--durable-root", metavar="DIR", default=None,
        help=f"Only with --report: base directory the other --report defaults "
             f"are computed from (default: this script's own self-anchored "
             f"durable root, {DURABLE_ROOT}).",
    )
    parser.add_argument(
        "--canon-path", metavar="PATH", default=None,
        help="Only with --report: override canon.json's path (default: "
             "{durable_root}/canon.json).",
    )
    parser.add_argument(
        "--schemas-dir", metavar="DIR", default=None,
        help="Only with --report: override the schemas directory (default: "
             f"{{durable_root}}/schemas), used to locate {SCHEMA_FILENAME}.",
    )
    return parser


def parse_args(argv=None) -> argparse.Namespace:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    have_check = args.check is not None
    have_report = args.report
    if have_check and have_report:
        parser.error("--check and --report are mutually exclusive -- pass exactly one.")
    if not have_check and not have_report:
        parser.error("nothing to do -- pass --check PATH or --report. See --help.")

    if have_check:
        report_only = [
            ("--harmonisation", args.harmonisation),
            ("--durable-root", args.durable_root),
            ("--canon-path", args.canon_path),
            ("--schemas-dir", args.schemas_dir),
        ]
        offending_flags = [name for name, value in report_only if value is not None]
        if offending_flags:
            parser.error(
                f"{', '.join(offending_flags)} only valid with --report, not --check."
            )
    else:  # have_report
        if args.approve_to is not None:
            parser.error("--approve-to only valid with --check, not --report.")

    return args


def main(argv=None) -> int:
    args = parse_args(argv)
    try:
        if args.check is not None:
            summary = run_check(args.check, args.approve_to)
        else:
            summary = run_report(
                args.harmonisation, args.durable_root, args.canon_path, args.schemas_dir
            )
    except CanonHarmonisationRefusal as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        if e.offending is not None:
            print(f"offending: {e.offending!r}", file=sys.stderr)
        return 1
    except CanonHarmonisationFatalError as e:
        print(f"FATAL: {e}", file=sys.stderr)
        if e.offending is not None:
            print(f"offending: {e.offending!r}", file=sys.stderr)
        return 2

    print(dumps_line(summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())
