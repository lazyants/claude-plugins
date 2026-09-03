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
from datetime import datetime, timezone
import hashlib
import json
import os
import re
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
SCHEMA_FILENAME = "canon-harmonisation.schema.json"
CORPUS_SCHEMA_FILENAME = "canon-harmonisation-corpus.schema.json"


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
    """Returns (raw_bytes, entries) for canon.json at `canon_path`.
    Raises CanonHarmonisationFatalError on anything short of a readable
    JSON object carrying an entries{} mapping of dict-shaped records --
    this script trusts canon_validate.py's own validate-only pass to have
    already enforced canon-file.schema.json's full shape; it only needs
    entries{} itself to anchor and look up against.

    The per-VALUE check is not redundant with that trust: both modes call
    `.get("canonical_target_form")` on a record, so a canon.json whose
    entries{} maps a name straight to a string -- a hand edit, a truncated
    write -- would raise AttributeError and surface as an uncaught
    traceback under exit 1, which in this script's convention means "the
    ARTIFACT is bad". A canon fault is a fatal (exit 2), and saying so is
    the difference between the operator re-running the pass and the
    operator fixing their canon."""
    raw, doc = _read_json_bytes(canon_path, "canon.json")
    entries = doc.get("entries")
    if not isinstance(entries, dict):
        raise CanonHarmonisationFatalError(
            f"canon.json at {canon_path} has no entries{{}} mapping "
            f"(got {type(entries).__name__ if entries is not None else 'missing'})"
        )
    for source_form, entry in entries.items():
        if not isinstance(entry, dict):
            raise CanonHarmonisationFatalError(
                f"canon.json at {canon_path} maps {source_form!r} to a "
                f"{type(entry).__name__}, not an entry object"
            )
    return raw, entries


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


def _schema_errors(doc: dict, schemas_dir: Path, schema_filename: str = SCHEMA_FILENAME):
    """Validates `doc` against `schema_filename` (registered from
    `schemas_dir`, alongside every sibling schema there) and returns the
    sorted list of jsonschema ValidationErrors (empty if valid). Raises
    CanonHarmonisationFatalError (exit 2, both modes) if the schemas
    directory or the schema file itself cannot be loaded -- a missing
    schema is a deployment problem, never a gate-fail. Two schemas are
    validated through this one path: the attempt artifact and, in --check,
    the corpus file the session dispatched."""
    registry = _build_schema_registry(schemas_dir)
    schema = _load_schema_document(schemas_dir / schema_filename)
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


def _load_sibling(module_name: str, scripts_dir: Path):
    """Loads a staged sibling script by EXACT PATH, never `import <name>`,
    for the same reason json_stdout.py is loaded that way above: a bare
    sibling import resolves through the global sys.modules cache regardless
    of which staged copy the caller intended. Raises
    CanonHarmonisationFatalError (exit 2) when the sibling is absent -- a
    missing staged script is a deployment fault, never a gate-fail."""
    path = scripts_dir / f"{module_name}.py"
    try:
        spec = _importlib_util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            raise ImportError(f"no loader for {path}")
        module = _importlib_util.module_from_spec(spec)
        spec.loader.exec_module(module)
    except (ImportError, OSError) as exc:
        raise CanonHarmonisationFatalError(
            f"cannot load {module_name}.py from {path} ({exc}) -- it must be staged "
            "alongside canon_harmonisation.py under ${durable_root}/scripts/"
        )
    return module


def build_corpus(
    durable_root_str: "str | None",
    candidates_source: str,
    out_str: "str | None",
) -> dict:
    """Gathers the three corpora and writes the corpus file the dispatch is
    serialised from and --check is then validated against.

    This is a SCRIPT rather than session prose because every step of it is
    mechanical -- read these files, keep these rows, count the ones dropped
    -- and none of it is an identity call. Leaving it as prose would also
    leave the fail-closed rules below unenforceable: they are exactly the
    kind of rule a session skips without noticing.

    Fail-closed at all three draft steps, because the failure that matters
    here is a corpus that GATHERED nothing reading like one that FOUND
    nothing."""
    durable_root = Path(durable_root_str) if durable_root_str else DURABLE_ROOT
    scripts_dir = Path(__file__).absolute().parent
    canon_path = durable_root / "canon.json"

    canon_bytes, entries = _load_canon(canon_path)
    canon_sha256 = hashlib.sha256(canon_bytes).hexdigest()

    observations = []
    for source_form, entry in entries.items():
        observations.append({
            "corpus": "canon",
            "source_form": source_form,
            "target_form": entry.get("canonical_target_form"),
        })

    # --- draft corpus ------------------------------------------------------
    # (1) WHICH FRAGMENTS EXIST. ledger_merge._read_fragments, never
    # final_audit.load_converged_fragments: the latter asks with is_dir() +
    # glob(), and #463 measured that a ledger.d at mode 0o000 answers
    # is_dir() -> True and glob("*.json") -> [], so an unreadable POPULATED
    # directory reports itself empty. Here that would mean "no drafts" --
    # absence and failure printing identically. _read_fragments tells
    # ENOENT/ENOTDIR (genuinely nothing written yet) from every other errno.
    # Reading fragments rather than the materialized runs/ledger.json is
    # required on its own: the default driver writes only fragments and
    # leaves ledger.json at its pre-run state.
    ledger_merge = _load_sibling("ledger_merge", scripts_dir)
    final_audit = _load_sibling("final_audit", scripts_dir)
    ledger_d = durable_root / "runs" / "ledger.d"
    try:
        fragments = ledger_merge._read_fragments(ledger_d)
    except (OSError, ledger_merge.LedgerMergeError) as exc:
        # _read_fragments returns {} for ENOENT/ENOTDIR (genuinely nothing
        # written yet) and raises LedgerMergeError for every other errno --
        # the "could-not-look is not nothing-is-there" distinction #463 put
        # there. Catching its own exception type is the point: without it a
        # PermissionError surfaces as a traceback under exit 1, which in this
        # script's convention means the ARTIFACT is bad.
        raise CanonHarmonisationFatalError(
            f"cannot enumerate ledger fragments at {ledger_d} ({exc}) -- refusing to "
            "report an empty draft corpus for a directory that may be populated"
        )

    converged_segments = 0
    drafts_excluded_stale_review = 0
    draft_rows_skipped = 0
    draft_pairs = {}
    for seg in sorted(fragments):
        fragment = fragments[seg]
        if not isinstance(fragment, dict) or fragment.get("status") != "converged":
            continue
        # (2) WHICH MAY CONTRIBUTE. status == "converged" is not sufficient:
        # a draft edited after its review carries names no reviewer saw, so
        # the fragment's reviewed_draft_sha1 must still match the draft's
        # current content -- the same comparison final_audit.py's
        # hard_check_stale_review makes.
        expected = fragment.get("reviewed_draft_sha1")
        draft_file = durable_root / "segments" / f"{seg}.draft.json"
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
            raise CanonHarmonisationFatalError(
                f"converged, review-current draft {draft_file} could not be read "
                f"({exc}) -- refusing to silently drop it from the corpus"
            )
        converged_segments += 1
        # (3) WHICH ROWS INSIDE. final_audit._name_entry_forms already
        # accepts both field conventions draft.schema.json permits; a row it
        # cannot read is COUNTED, never silently dropped.
        for row in (draft.get("names") or []):
            source_form, target_form = final_audit._name_entry_forms(row)
            if source_form is None:
                draft_rows_skipped += 1
                continue
            draft_pairs.setdefault((source_form, target_form), set()).add(seg)

    for (source_form, target_form), segs in sorted(draft_pairs.items()):
        observations.append({
            "corpus": "draft",
            "source_form": source_form,
            "target_form": target_form,
            "n_segments": len(segs),
        })

    # --- candidate corpus --------------------------------------------------
    # On the glossary.enabled:false branch the bootstrap never ran and
    # deletes nothing, so a stale name_candidates.json can sit there looking
    # present. The corpus records WHY the rows are what they are and the
    # file is not opened at all -- which is why nothing here is nullable.
    if candidates_source == "bootstrap":
        candidates_path = durable_root / "name_candidates.json"
        _raw, candidates_doc = _read_json_bytes(candidates_path, "name_candidates.json")
        rows = candidates_doc.get("candidates")
        if not isinstance(rows, list):
            raise CanonHarmonisationFatalError(
                f"{candidates_path} has no candidates[] array -- "
                "--candidates-source bootstrap says the extractor ran"
            )
        for row in rows:
            if not isinstance(row, dict):
                continue
            name = row.get("name")
            freq = row.get("freq")
            if isinstance(name, str) and name.strip() and isinstance(freq, int) and freq >= 1:
                observations.append({
                    "corpus": "candidate",
                    "source_form": name,
                    "target_form": None,
                    "freq": freq,
                })

    doc = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "canon_sha256": canon_sha256,
        "candidates_source": candidates_source,
        "converged_segments": converged_segments,
        "drafts_excluded_stale_review": drafts_excluded_stale_review,
        "draft_rows_skipped": draft_rows_skipped,
        "observations": observations,
    }
    raw = json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8") + b"\n"

    schema_failure = _schema_failure_message(
        doc, durable_root / "schemas", Path(out_str) if out_str else durable_root,
        schema_filename=CORPUS_SCHEMA_FILENAME,
    )
    if schema_failure:
        # The builder's OWN output failing its schema is a fault in this
        # script, not in the project: fatal, and nothing is written.
        raise CanonHarmonisationFatalError(schema_failure)

    n_canon = sum(1 for o in observations if o["corpus"] == "canon")
    n_draft = sum(1 for o in observations if o["corpus"] == "draft")
    n_candidate = sum(1 for o in observations if o["corpus"] == "candidate")

    out_path = Path(out_str) if out_str else (
        durable_root / "harmonisation"
        / f"corpus_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
          f"_{os.urandom(4).hex()}.json"
    )
    _atomic_publish(out_path, raw)

    return {
        "success": True,
        "mode": "build-corpus",
        "corpus_path": str(out_path),
        "corpus_sha256": hashlib.sha256(raw).hexdigest(),
        "canon_sha256": canon_sha256,
        "candidates_source": candidates_source,
        "canon_observations": n_canon,
        "draft_observations": n_draft,
        "candidate_observations": n_candidate,
        "converged_segments": converged_segments,
        "drafts_excluded_stale_review": drafts_excluded_stale_review,
        "draft_rows_skipped": draft_rows_skipped,
        # The no-op test, computed here rather than left to the session:
        # dispatch when there is at least one canon observation to anchor
        # against, or at least two draft observations to disagree.
        "should_dispatch": n_canon >= 1 or n_draft >= 2,
    }


def _load_corpus(corpus_path: Path, expected_sha256: str, schemas_dir: Path) -> dict:
    """Reads, anchors and schema-validates the corpus file the session
    serialised into this pass's prompt, and returns
    {(corpus, source_form, target_form): observation}.

    THE THREE-WAY COMPARISON is the whole point and it happens here, before
    any proposal is looked at and long before _atomic_publish. The corpus
    file lives in the durable root the dispatched pass can WRITE, so a
    digest recomputed from that file and compared only against the
    artifact's own field proves self-consistency and nothing else: a pass
    could rewrite the corpus with fabricated observations, stamp the
    matching digest into its artifact, and pass. `expected_sha256` is the
    digest the SESSION computed before dispatching and kept in its own
    context, where the pass cannot reach it -- so expected, on-disk and
    artifact must all three agree.

    The observations are keyed by the whole TRIPLE rather than by
    source_form: `canon: X -> A` beside a converged `draft: X -> B` is the
    cross-segment discrepancy shape final_audit.py's WARN glossary-diff
    already reports, and keying by source_form alone would collapse the two
    rows this pass exists to put in front of the operator."""
    raw, doc = _read_json_bytes(corpus_path, "harmonisation corpus")

    actual = hashlib.sha256(raw).hexdigest()
    if actual != expected_sha256:
        raise CanonHarmonisationRefusal(
            f"{corpus_path} does not match --expect-corpus-sha256: the session "
            f"dispatched {expected_sha256}, the file on disk now hashes to {actual}"
        )

    schema_failure = _schema_failure_message(
        doc, schemas_dir, corpus_path, schema_filename=CORPUS_SCHEMA_FILENAME
    )
    if schema_failure:
        raise CanonHarmonisationRefusal(schema_failure)

    observations = {}
    for observation in doc["observations"]:
        key = (
            observation["corpus"],
            observation["source_form"],
            observation["target_form"],
        )
        observations[key] = observation
    return {"doc": doc, "observations": observations}


def _normalise_referent(text: str) -> str:
    """Trims and collapses internal whitespace runs, so `referents` entries
    differing only in spacing count as ONE referent. `referents` is
    multi_referent's only structural protection against a content-free
    assertion, and raw distinctness would let "Reb Noson" and "Reb  Noson"
    defeat it."""
    return " ".join(text.split())


# The per-kind cardinality and target rules, kept as ONE table rather than a
# chain of branches: every kind's rule is then visible in one place, and a
# kind added to the schema without a rule here fails loudly in
# _check_proposals rather than falling through to a default that would let
# it pass unchecked.
KIND_RULES = {
    "divergent_spelling": "divergent",
    "divergent_policy": "divergent",
    "shared_target": "shared_target",
    "multi_referent": "multi_referent",
    "uncanonized_variant": "uncanonized_variant",
}


def _check_proposals(doc: dict, entries: dict, observations: dict) -> None:
    """(c)-(g): every proposal-level and cross-proposal structural check.
    Raises CanonHarmonisationRefusal on the FIRST violation found, always
    naming the offending proposal's index (and, where one is at fault, the
    source_form) via `offending`.

    Membership is checked against the CORPUS the session dispatched, not
    against live canon.json: the corpus is what the pass was actually shown,
    and a check against anything else could refuse a correct proposal (the
    canon moved) or accept a fabricated one (a row the pass invented that
    happens to exist now). The one thing still measured against LIVE canon
    is uncanonized_variant's absence rule and, in --report, every member's
    route -- a corpus tag records where a row was read, never what canon
    holds now (name_candidates.json carries the extractor's whole list and
    only glossary_batch_plan.py later drops the forms already resolved)."""
    seen_signatures = set()
    for idx, proposal in enumerate(doc["proposals"]):
        members = proposal["members"]
        kind = proposal["kind"]
        rule = KIND_RULES.get(kind)
        if rule is None:
            # Unreachable through the schema, which enumerates the kinds --
            # but a kind added there without a rule here must fail LOUD
            # rather than fall through to a default that checks nothing.
            raise CanonHarmonisationFatalError(
                f"proposals[{idx}]: kind {kind!r} has no rule in KIND_RULES -- "
                "the schema and this script disagree about the kinds that exist"
            )

        triples = []
        canon_members = []
        target_bearing = []
        for member in members:
            corpus = member["corpus"]
            source_form = member["source_form"]
            target_form = member["canonical_target_form"]
            triple = (corpus, source_form, target_form)

            # (d) byte-exact membership in the dispatched corpus. A plain
            # dict lookup never folds, casefolds, or NFC/NFD-normalises, so
            # a form matching an observation only after normalisation
            # correctly misses here. Matching the whole triple is what makes
            # a corpus tag unforgeable: a row cannot be relabelled into
            # another corpus, because the relabelled triple is not there.
            observation = observations.get(triple)
            if observation is None:
                raise CanonHarmonisationRefusal(
                    f"proposals[{idx}]: member ({corpus!r}, {source_form!r}, "
                    f"{target_form!r}) is not a byte-exact observation in the "
                    "corpus this pass was dispatched with",
                    offending={"proposal_index": idx, "source_form": source_form},
                )

            # (d) the carried reporting counts must be the corpus's own.
            # --report never receives --corpus, so it renders these from the
            # artifact; an unverified count would be a number the pass chose.
            for field in ("n_segments", "freq"):
                if member.get(field) != observation.get(field):
                    raise CanonHarmonisationRefusal(
                        f"proposals[{idx}]: member {source_form!r} carries "
                        f"{field}={member.get(field)!r}, the corpus observation says "
                        f"{observation.get(field)!r}",
                        offending={"proposal_index": idx, "source_form": source_form},
                    )

            triples.append(triple)
            if corpus == "canon":
                canon_members.append(member)
            if target_form is not None:
                target_bearing.append(target_form)

        # (f) pairwise-distinct TRIPLES within this one proposal. Distinct
        # triples rather than distinct source_forms, because one source form
        # legitimately appears twice when two corpora disagree about it --
        # which is exactly the finding this pass exists to surface.
        duplicate_triple = _first_duplicate(triples)
        if duplicate_triple is not None:
            raise CanonHarmonisationRefusal(
                f"proposals[{idx}]: member ({duplicate_triple[0]!r}, "
                f"{duplicate_triple[1]!r}, {duplicate_triple[2]!r}) appears more than "
                "once in members[] -- members within one proposal must be pairwise "
                "distinct",
                offending={"proposal_index": idx, "source_form": duplicate_triple[1]},
            )

        _check_kind_rule(idx, kind, rule, members, canon_members, target_bearing,
                         proposal, entries)

        # (g) no two proposals may name the same kind over the same set of
        # member observations.
        signature = (kind, frozenset(triples))
        if signature in seen_signatures:
            raise CanonHarmonisationRefusal(
                f"proposals[{idx}]: duplicate proposal -- another proposal already names "
                f"the same kind ({kind!r}) over the same member observations",
                offending={"proposal_index": idx},
            )
        seen_signatures.add(signature)


def _check_kind_rule(idx, kind, rule, members, canon_members, target_bearing,
                     proposal, entries) -> None:
    """(c)/(e): the cardinality and target rule for ONE kind. Split out of
    _check_proposals so each kind's rule reads as one block and every
    refusal can name the kind and the count it measured -- a mislabelled
    proposal is then refused with the reason, never silently re-read as
    another kind."""
    distinct_targets = set(target_bearing)

    if rule == "divergent":
        # The original #823 question: >=2 target-bearing members disagreeing
        # about the target. May be satisfied entirely by `draft` members --
        # an inconsistency living wholly in the per-segment corpus is the
        # class the issue's scope correction says is invisible today, and
        # requiring a canon anchor would make it unreportable.
        if len(target_bearing) < 2:
            raise CanonHarmonisationRefusal(
                f"proposals[{idx}]: kind {kind!r} needs at least 2 target-bearing "
                f"members, got {len(target_bearing)}",
                offending={"proposal_index": idx, "member_count": len(target_bearing)},
            )
        if len(distinct_targets) < 2:
            raise CanonHarmonisationRefusal(
                f"proposals[{idx}]: kind {kind!r} needs at least 2 distinct target "
                f"forms, got {len(distinct_targets)} ({sorted(distinct_targets)!r}) -- "
                "members that already agree have nothing to harmonise",
                offending={"proposal_index": idx},
            )
        return

    if rule == "shared_target":
        # The INVERSE and the more damaging direction: two canon entries
        # frozen under ONE target give two different people one vault page.
        # CANON members specifically -- a frozen target is a canon property,
        # and no draft or candidate row can establish that two referents
        # share a page.
        if len(canon_members) < 2:
            raise CanonHarmonisationRefusal(
                f"proposals[{idx}]: kind 'shared_target' needs at least 2 members whose "
                f"corpus is 'canon', got {len(canon_members)} -- a frozen target is a "
                "canon property, so a draft or candidate row cannot establish one",
                offending={"proposal_index": idx, "member_count": len(canon_members)},
            )
        canon_targets = {m["canonical_target_form"] for m in canon_members}
        if len(canon_targets) != 1:
            raise CanonHarmonisationRefusal(
                f"proposals[{idx}]: kind 'shared_target' needs exactly 1 distinct target "
                f"form among its canon members, got {len(canon_targets)} "
                f"({sorted(canon_targets)!r}) -- differing targets are a divergent_* "
                "proposal, not a shared one",
                offending={"proposal_index": idx},
            )
        return

    if rule == "multi_referent":
        # Exactly ONE member, total. "One canon member plus whatever" would
        # let arbitrary draft or candidate rows ride along on a claim that
        # is about a single entry covering several people.
        if len(members) != 1 or len(canon_members) != 1:
            raise CanonHarmonisationRefusal(
                f"proposals[{idx}]: kind 'multi_referent' needs exactly 1 member whose "
                f"corpus is 'canon', got {len(members)} member(s) of which "
                f"{len(canon_members)} canon",
                offending={"proposal_index": idx, "member_count": len(members)},
            )
        # `referents` is this kind's only structural protection against a
        # content-free assertion, so distinctness is measured after
        # collapsing whitespace: "Reb Noson" and "Reb  Noson" are ONE.
        normalised = [_normalise_referent(r) for r in proposal["referents"]]
        if len(set(normalised)) < 2:
            raise CanonHarmonisationRefusal(
                f"proposals[{idx}]: kind 'multi_referent' needs at least 2 referents "
                f"distinct after whitespace normalisation, got "
                f"{len(set(normalised))} ({sorted(set(normalised))!r})",
                offending={"proposal_index": idx},
            )
        return

    # rule == "uncanonized_variant": a form that never reached canon is
    # proposed as a variant of one that did.
    candidate_members = [m for m in members if m["corpus"] == "candidate"]
    if not canon_members or not candidate_members:
        raise CanonHarmonisationRefusal(
            f"proposals[{idx}]: kind 'uncanonized_variant' needs at least 1 'canon' "
            f"member and at least 1 'candidate' member, got {len(canon_members)} canon "
            f"and {len(candidate_members)} candidate",
            offending={"proposal_index": idx},
        )
    for member in candidate_members:
        # Measured against LIVE canon, not against the corpus tag.
        # name_candidates.json is the extractor's COMPLETE list; only
        # glossary_batch_plan.py later drops the forms already resolved, so
        # a candidate observation's source_form may well be a canon key --
        # and then it is not uncanonized at all.
        source_form = member["source_form"]
        if source_form in entries:
            raise CanonHarmonisationRefusal(
                f"proposals[{idx}]: kind 'uncanonized_variant' names {source_form!r} as "
                "a candidate member, but it IS a key of canon.json's entries{} -- the "
                "corpus tag records where the row was read, not what canon holds now",
                offending={"proposal_index": idx, "source_form": source_form},
            )


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
    tmp_path = dest.parent / f".{dest.name}.tmp.{os.getpid()}.{os.urandom(4).hex()}"
    replaced = False
    try:
        # INSIDE the try on purpose: mkdir raises OSError of its own (a
        # regular file where a parent directory is expected raises
        # FileExistsError), and outside it that escaped as an uncaught
        # traceback under exit 1 -- "the ARTIFACT is bad" in this script's
        # convention, for what is actually a filesystem fault. Publishing
        # failures are fatals (exit 2), which is what the sibling
        # os.replace failure already asserts.
        dest.parent.mkdir(parents=True, exist_ok=True)
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


def _schema_failure_message(
    doc: dict, schemas_dir: Path, path: Path, schema_filename: str = SCHEMA_FILENAME
) -> "str | None":
    """The ONE wording for a schema rejection, shared by both modes. Two
    verbatim copies of a contract string drift; this one does not, and the
    tests pin the wording in exactly one place. Only the FIRST error is
    reported (the list _schema_errors returns is sorted, so "first" is
    deterministic) -- an operator fixes one thing at a time, and the
    remaining errors are re-derived on the next run."""
    errors = _schema_errors(doc, schemas_dir, schema_filename=schema_filename)
    if not errors:
        return None
    first = errors[0]
    loc = "/".join(str(p) for p in first.path) or "<root>"
    return f"{path} failed schema validation at '{loc}': {first.message}"


def run_check(
    path_str: str,
    approve_to_str: "str | None",
    corpus_str: str,
    expect_corpus_sha256: str,
) -> dict:
    path = Path(path_str)
    canon_bytes, entries = _load_canon(CANON_PATH)
    canon_sha256 = hashlib.sha256(canon_bytes).hexdigest()

    # The corpus is loaded, anchored against the session's own expected
    # digest and schema-validated BEFORE the artifact is looked at, and long
    # before _atomic_publish: a corpus that is not the one dispatched makes
    # every later membership check meaningless, so there is nothing to be
    # gained by reading further.
    corpus = _load_corpus(Path(corpus_str), expect_corpus_sha256, SCHEMAS_DIR)

    raw, doc = _read_json_bytes(path, "harmonisation attempt artifact")

    schema_failure = _schema_failure_message(doc, SCHEMAS_DIR, path)
    if schema_failure:
        raise CanonHarmonisationRefusal(schema_failure)

    _check_anchor(doc, canon_sha256, path)
    if doc["corpus_sha256"] != expect_corpus_sha256:
        raise CanonHarmonisationRefusal(
            f"{path} claims corpus_sha256 {doc['corpus_sha256']}, but the session "
            f"dispatched {expect_corpus_sha256}"
        )
    _check_proposals(doc, entries, corpus["observations"])

    approved_to = None
    if approve_to_str is not None:
        dest = Path(approve_to_str)
        _atomic_publish(dest, raw)
        approved_to = str(dest)

    corpus_doc = corpus["doc"]
    return {
        "success": True,
        "mode": "check",
        "proposals_count": len(doc["proposals"]),
        "entries_in_canon": len(entries),
        "canon_sha256": canon_sha256,
        "corpus_sha256": expect_corpus_sha256,
        "observations_in_corpus": len(corpus_doc["observations"]),
        "candidates_source": corpus_doc["candidates_source"],
        "converged_segments": corpus_doc["converged_segments"],
        "drafts_excluded_stale_review": corpus_doc["drafts_excluded_stale_review"],
        "draft_rows_skipped": corpus_doc["draft_rows_skipped"],
        "approved_to": approved_to,
    }


# ---------------------------------------------------------------------------
# --report: read-only render.
# ---------------------------------------------------------------------------

def _escape_terminal_controls(text: str, keep: "frozenset[str]" = frozenset()) -> str:
    """Neutralises every control character and Unicode line/paragraph
    separator model-authored text may carry, before it reaches
    operator-facing report text. Three call sites, all in _render_report:
    the bare `note:` line, the same note text embedded in a --correct
    skeleton's `reason` field, and the SERIALISED skeleton block as a
    whole (whose `source_form`, `old_entry` and `new_entry` come from the
    artifact and from canon.json without passing through repr()).
    `note` is free LLM-authored prose and the schema's only
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

    `keep` names the characters a caller has ALREADY established are this
    script's own structure rather than artifact content -- only the
    skeleton call site uses it, for the real newlines json.dumps(indent=2)
    puts between the dump's own physical lines. It is never a way to let
    an artifact-supplied character through: json.dumps escapes every C0
    character inside a string, so a raw newline in that dump can only be
    one this script emitted.

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
        if ch not in keep
        and (unicodedata.category(ch) == "Cc" or ch in (chr(0x2028), chr(0x2029)))
        else ch
        for ch in text
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
        # and every --correct skeleton's `reason`): see _escape_terminal_controls' own
        # docstring for why `note` is the one artifact-supplied string this
        # renderer must never print raw.
        sanitized_note = _escape_terminal_controls(proposal["note"])
        for member in proposal["members"]:
            source_form = member["source_form"]
            stored_target_form = member["canonical_target_form"]
            corpus = member["corpus"]
            entry = entries.get(source_form)
            # EVERY line below is decided by whether the form is a LIVE canon
            # key, never by `corpus`. The tag records where the row was read;
            # name_candidates.json carries the extractor's complete list and
            # only glossary_batch_plan.py later drops the forms already
            # resolved, so a `candidate` row's form may be a canon key -- and
            # a `canon` row's form may have been removed since.
            if entry is not None:
                lines.append(
                    f"    {source_form!r} -> {entry.get('canonical_target_form')!r}"
                )
            elif corpus == "canon":
                lines.append(f"    {source_form!r} -> REMOVED (was: {stored_target_form!r})")
            elif corpus == "draft":
                lines.append(
                    f"    {source_form!r} -> {stored_target_form!r} "
                    f"(draft, {member['n_segments']} segment(s); NOT IN CANON)"
                )
            else:
                lines.append(
                    f"    {source_form!r} -> NOT IN CANON "
                    f"(candidate, freq {member['freq']})"
                )
        if proposal["kind"] == "multi_referent":
            referents = ", ".join(
                _escape_terminal_controls(r) for r in proposal["referents"]
            )
            lines.append(f"    referents claimed: {referents}")
        lines.append(f"    note: {sanitized_note}")
        if proposal["kind"] == "multi_referent":
            # No --correct skeleton at all: the claim is that ONE entry covers
            # several people, and the answer to that is a canon_senses.json
            # split, which the mandatory homonym-split gate then requires
            # evidence for. --correct would retarget the entry, which is a
            # different operation entirely.
            lines.append(
                "    no --correct skeleton: a source form covering several referents is "
                "recorded as a split in canon_senses.json, and the mandatory "
                "homonym-split gate then requires that split's evidence. --correct "
                "retargets an entry, which is a different operation."
            )
            continue
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
                # canon_validate.py --correct refuses a source_form that is not
                # a canon key and sends it to the ordinary glossary merge, so a
                # skeleton naming one would be a paste that cannot work. The
                # test is live canon-key membership, not the member's corpus:
                # a `canon` member whose entry has since been removed and a
                # `draft`/`candidate` member that was never canonized both land
                # here, and the line says which.
                if member["corpus"] == "canon":
                    lines.append(
                        f"      # {source_form!r} was already removed from canon.json's "
                        "entries{} -- no --correct skeleton to offer."
                    )
                else:
                    lines.append(
                        f"      # {source_form!r} is not in canon.json's entries{{}} -- "
                        "--correct refuses a form it cannot find, so the route is a NEW "
                        "canon entry through the ordinary glossary merge, not a "
                        "correction."
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
            # Round 3 (MAJOR): json.dumps escapes C0 (U+0000-U+001F) because
            # JSON forbids it raw, but leaves U+007F, the whole C1 block
            # (U+0080-U+009F, DEL and CSI among them) and U+2028/U+2029 as
            # literal characters. `source_form` and the canon entry copied
            # into old_entry/new_entry are LLM-authored and only pattern-
            # constrained, so a control-bearing canon row would reach the
            # operator's terminal raw through THIS block -- the member lines
            # above are safe only because repr() escapes those codepoints,
            # and a JSON dump is not a repr. Escaping the SERIALISED text is
            # safe and lossless: control characters can only occur inside a
            # JSON string literal (every structural byte is ASCII-printable),
            # and \uXXXX there parses back to the identical string. Escaping
            # the dump rather than passing ensure_ascii=True keeps the
            # non-Latin source forms the operator must read and edit legible.
            lines.append(
                _escape_terminal_controls(
                    json.dumps(skeleton, ensure_ascii=False, indent=2, sort_keys=True),
                    # indent=2 puts REAL newlines between the dump's own
                    # physical lines; they are this script's structure, not
                    # artifact content. No other C0 character can survive
                    # json.dumps (it escapes every one inside a string), so
                    # keeping exactly '\n' loses nothing.
                    keep=frozenset("\n"),
                )
            )
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

    schema_failure = _schema_failure_message(doc, schemas_dir, harmonisation_path)
    if schema_failure:
        raise CanonHarmonisationFatalError(schema_failure)

    canon_bytes, entries = _load_canon(canon_path)
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
        "--build-corpus", action="store_true",
        help="Gather the three corpora (canon entries, converged-draft names[], "
             "name_candidates rows) into the corpus file the dispatch is serialised "
             "from and --check is validated against. Prints its path, its sha256 and "
             "the per-corpus counts. Mutually exclusive with --check and --report.",
    )
    parser.add_argument(
        "--candidates-source", metavar="WHICH", default=None,
        choices=["bootstrap", "disabled"],
        help="REQUIRED with --build-corpus: 'bootstrap' when this W3 ran "
             "bootstrap_names.py, so name_candidates.json is fresh; 'disabled' on the "
             "glossary.enabled:false branch, where the file is NOT read at all because "
             "an older run's copy would look identical to a fresh one.",
    )
    parser.add_argument(
        "--out", metavar="PATH", default=None,
        help="Only with --build-corpus: where to write the corpus file (default: "
             "{durable_root}/harmonisation/corpus_<UTC>_<8 hex>.json).",
    )
    parser.add_argument(
        "--corpus", metavar="PATH", default=None,
        help="REQUIRED with --check: the corpus file the session serialised into "
             "this pass's prompt. Members are checked for byte-exact membership "
             "against it, never against live canon.json.",
    )
    parser.add_argument(
        "--expect-corpus-sha256", metavar="HEX", default=None,
        help="REQUIRED with --check: the sha256 the session computed over --corpus "
             "BEFORE dispatching. The corpus file lives in the durable root the "
             "dispatched pass can write, so this session-held value -- not a digest "
             "recomputed from disk -- is what makes the corpus an anchor.",
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
        help=f"With --report or --build-corpus: base directory the other defaults "
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
    have_build = args.build_corpus
    if sum([have_check, have_report, have_build]) > 1:
        parser.error(
            "--check, --report and --build-corpus are mutually exclusive -- pass "
            "exactly one."
        )
    if not (have_check or have_report or have_build):
        parser.error(
            "nothing to do -- pass --build-corpus, --check PATH or --report. See --help."
        )

    if have_build:
        if args.candidates_source is None:
            parser.error(
                "--candidates-source required with --build-corpus: whether this W3 ran "
                "bootstrap_names.py decides whether name_candidates.json may be read at "
                "all, and that is not readable from disk."
            )
        build_only_offenders = [
            name for name, value in (
                ("--check", args.check),
                ("--corpus", args.corpus),
                ("--expect-corpus-sha256", args.expect_corpus_sha256),
                ("--approve-to", args.approve_to),
                ("--harmonisation", args.harmonisation),
                ("--canon-path", args.canon_path),
                ("--schemas-dir", args.schemas_dir),
            ) if value is not None
        ]
        if build_only_offenders:
            parser.error(
                f"{', '.join(build_only_offenders)} not valid with --build-corpus."
            )
        return args

    if args.candidates_source is not None or args.out is not None:
        offending = [
            name for name, value in (
                ("--candidates-source", args.candidates_source),
                ("--out", args.out),
            ) if value is not None
        ]
        parser.error(f"{', '.join(offending)} only valid with --build-corpus.")

    if have_check:
        missing = [
            name for name, value in (
                ("--corpus", args.corpus),
                ("--expect-corpus-sha256", args.expect_corpus_sha256),
            ) if value is None
        ]
        if missing:
            parser.error(
                f"{', '.join(missing)} required with --check -- a proposal is checked "
                "against the corpus the session dispatched, not against live canon.json."
            )
        if not re.fullmatch(r"[0-9a-f]{64}", args.expect_corpus_sha256):
            parser.error(
                "--expect-corpus-sha256 must be 64 lowercase hex characters."
            )
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
        check_only = [
            ("--approve-to", args.approve_to),
            ("--corpus", args.corpus),
            ("--expect-corpus-sha256", args.expect_corpus_sha256),
        ]
        offending_flags = [name for name, value in check_only if value is not None]
        if offending_flags:
            parser.error(
                f"{', '.join(offending_flags)} only valid with --check, not --report."
            )

    return args


def main(argv=None) -> int:
    args = parse_args(argv)
    try:
        if args.check is not None:
            summary = run_check(
                args.check, args.approve_to, args.corpus,
                args.expect_corpus_sha256,
            )
        elif args.build_corpus:
            summary = build_corpus(
                args.durable_root, args.candidates_source, args.out
            )
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
