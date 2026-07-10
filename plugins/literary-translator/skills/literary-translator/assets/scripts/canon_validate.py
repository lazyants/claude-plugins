#!/usr/bin/env python3
"""canon_validate.py -- two-pass schema validation (and merge) backstop for
canon.json, the literary-translator plugin's frozen, hash-versioned,
cross-segment name/realia glossary.

STATUS: new plugin hardening -- the glossary-pass Workflow template that
feeds this script's batch-fragment modes is itself not yet source-proven
(see references/canon-and-glossary.md's "Glossary-pass call discipline"
section: the real historiettes-t3 project ran its glossary pass as ad hoc
`glossary/TASK.md` + codex batches, never through this schema-validated
pipeline). Authoritative spec for everything below:
references/canon-and-glossary.md, sections "`canon_validate.py`'s two
validation passes" and "Research preflight and offline-fallback policy for
`basis: "established"`" -- read those before changing anything here; this
file's behavior must match that doc exactly.

1.2.0 CHANGE -- fire-and-forget batch fragments, no agent-schema return:
pre-1.2.0, a glossary-pass batch call returned its result directly to the
JS via a discriminated-union agent `schema` (`CANON_BATCH_SCHEMA`) -- which
violates the tool-use API's "top-level object, no combinator" constraint
(issue #87). Since 1.2.0, glossary batches are schema-less, fire-and-forget
codex dispatches (`batchDispatchPrompt`) that write their own fragment file
atomically and self-validate it via THIS script's `--check-batch` mode,
never via an agent-returned schema. `CANON_BATCH_SCHEMA` no longer exists
in any template. Five CLI modes now exist, selected by which flag is given
(mutually exclusive; `--research-mode {live,offline}` is REQUIRED for
every one of them, even where it has no effect, so no call site can
accidentally omit declaring the precondition):

--check-batch PATH [--expect-source-forms-file M.json]
    Pass 1 (per-item) + the offline backstop on the ONE fragment at PATH.
    NO write. When --expect-source-forms-file is given (a JSON array of
    expected source_form strings, read from the FILE, never inline argv),
    additionally asserts the fragment's item source_forms are an EXACT
    match (no missing, no extra) -- the coverage half of the manifest-
    trust design (references/canon-and-glossary.md's "manifest disk-
    verify"), closing the gap where a codex batch could pass shape
    validation while silently omitting a candidate name.

--merge-batches P1 P2 ... [--expect-source-forms-file M.json is NOT
accepted here -- see --verify-merged]
    ONE process, single canon.json load: validates ALL given fragments
    (Pass 1 + offline backstop) FIRST, before merging any of them, so a
    later fragment's failure never leaves an earlier one half-applied.
    Then threads `acc = _merge_batch(acc, frag)` across every fragment IN
    THE GIVEN ORDER, stamps generation_hashes fresh, validates the
    in-memory accumulator against canon-file.schema.json (Pass 2) BEFORE
    ever touching disk, performs ONE atomic write, then re-reads the
    JUST-WRITTEN file fresh from disk and Pass-2-validates it AGAIN --
    genuinely from disk this time, with no masking fallback for a missing
    generation_hashes value, so a dropped-hash write corruption is
    actually caught rather than silently papered over.

--verify-merged --batch F1 [--batch F2 ...] [--expect-source-forms-file
M.json]
    Disk-INDEPENDENT verification that a set of already-processed
    fragments is correctly reflected in the CURRENT canon.json -- no
    write, fresh reads only. Per fragment item, by disposition: an
    'accepted' item must equal `canon["entries"][source_form]` exactly; a
    'review_queue' item must either still be present verbatim in
    `canon["review_queue"]`, OR its source_form must now be a key in
    `canon["entries"]` (accept-supersedes -- a later batch's ACCEPTED
    resolution for the same name is not a failure, never reported
    missing). When --expect-source-forms-file is given, additionally
    asserts every manifest name is covered by SOME fragment item. Reports
    `{"verified": true}` or `{"verified": false, "missing": [...]}` -- the
    exact relay shape the glossary-pass Workflow's disk-verify agent
    (`CANON_VERIFY_SCHEMA`) returns.

--batch PATH (legacy, single-fragment merge -- KEPT for existing callers)
    The pre-1.2.0 merge path: Pass 1 + offline backstop on the one
    fragment, merge, stamp generation_hashes, in-memory Pass 2, one atomic
    write, disk-re-read Pass 2 (same no-masking discipline as
    --merge-batches above). Equivalent to `--merge-batches PATH` with
    exactly one fragment, kept as its own code path only because existing
    tests/callers already invoke it this way.

(no batch flag at all) -- VALIDATE-ONLY mode
    A read-only health check: no merge, no write, no offline backstop
    (that backstop only ever applies to NEW entries in an incoming batch,
    per the authoritative spec's own "for every new entry" framing -- an
    already-frozen canon.json is not retroactively re-litigated just
    because this run happens to pass --research-mode offline for other
    reasons). Pass 1 (per-entry) validates every canon.json entries{}
    value against canon-entry.schema.json and every review_queue[] item
    against the QUEUED shape; Pass 2 validates the whole loaded document.

Single-writer note: canon.json has exactly one concurrent writer by
OPERATIONAL PRECONDITION -- the orchestrating Workflow serializes every
merge/verify call for one glossary pass onto a single Claude
`effort:"low"` invocation, never dispatches concurrent merges (see
references/orchestration-and-batching.md, "one serialized final merge").
This script performs no file locking of its own; it relies entirely on
that precondition, same as ledger_merge.py's own materialization step.

Reads canon-entry.schema.json / canon-batch.schema.json /
canon-file.schema.json from ${durable_root}/schemas/ -- never the plugin's
own assets/schemas/ (this script always runs from the durable, per-project
copy).

Usage:
    python3 canon_validate.py --research-mode live --check-batch out_0.json
    python3 canon_validate.py --research-mode live --check-batch out_0.json --expect-source-forms-file manifest_0.json
    python3 canon_validate.py --research-mode live --merge-batches out_0.json out_1.json
    python3 canon_validate.py --research-mode live --verify-merged --batch out_0.json --batch out_1.json --expect-source-forms-file manifest_all.json
    python3 canon_validate.py --research-mode live --batch glossary_out.json
    python3 canon_validate.py --research-mode offline --batch glossary_out.json
    python3 canon_validate.py --research-mode live
    python3 canon_validate.py --research-mode live --canon-path /path/to/canon.json

Exit code 0 on success, 1 on failure (for --verify-merged, "success" means
`verified: true`). Exactly one JSON line is printed to stdout either way --
callers (the glossary-pass Workflow, tests) should read stdout, not rely
on the exit code alone.
"""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

try:
    import jsonschema
    from referencing import Registry, Resource
except ImportError as e:
    sys.stderr.write(
        "canon_validate.py requires the 'jsonschema' package (>=4.26.0), "
        "which pulls in 'referencing' for $ref resolution across the "
        "canon-*.schema.json files. Install with:\n\n"
        "    pip install -r requirements.txt\n\n"
        "(or directly: pip install 'jsonschema>=4.26.0')\n\n"
        f"(import error: {e})\n"
    )
    sys.exit(1)

# Self-anchored: this script always lives at
# ${durable_root}/scripts/canon_validate.py, so parents[1] is the durable
# root. Never assumes cwd, never takes a --durable-root flag -- see
# references/ledger-and-resumability.md's "Script self-anchoring" invariant
# (the same rule applies to every copied script, not just the ledger ones).
_SCRIPT_FILE = Path(__file__).resolve()
SCRIPTS_DIR = _SCRIPT_FILE.parent
DURABLE_ROOT = _SCRIPT_FILE.parents[1]
SCHEMAS_DIR = DURABLE_ROOT / "schemas"
DEFAULT_CANON_PATH = DURABLE_ROOT / "canon.json"
CACHE_KEY_SCRIPT = SCRIPTS_DIR / "cache_key.py"

RESEARCH_MODES = ("live", "offline")

# The two global generation_hashes fields canon.json stamps at merge time
# (references/canon-and-glossary.md, "Bootstrap sequence" step 4) -- both
# computed via cache_key.py --field <name>, never independently recomputed
# here.
GENERATION_HASH_FIELDS = ("particle_config_hash", "derivation_bundle_hash")

# canon-entry.schema.json's own shape has no 'disposition' property and is
# additionalProperties:false -- this is the exact set of keys an ACCEPTED
# batch item may carry once merged into entries{}.
CANON_ENTRY_FIELDS = (
    "source_form",
    "is_proper_name",
    "canonical_target_form",
    "basis",
    "source",
    "confidence",
    "note",
    "category",
)


class CanonValidationError(Exception):
    """Raised for any failure that should surface as a FAILURE result.

    `offending`, when not None, is folded into the failure payload verbatim
    -- naming which batch items / entries triggered the failure, so a
    caller never has to re-derive that from a bare error string.
    """

    def __init__(self, message, offending=None):
        super().__init__(message)
        self.offending = offending


# ---------------------------------------------------------------------------
# Schema loading / registry (mirrors ledger_merge.py's own pattern exactly,
# with format_checker added explicitly -- canon-entry.schema.json is the
# one shipped schema that actually needs a real format assertion).
# ---------------------------------------------------------------------------


def _load_schema_document(schema_path: Path) -> dict:
    if not schema_path.is_file():
        raise CanonValidationError(f"schema file not found: {schema_path}")
    try:
        return json.loads(schema_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise CanonValidationError(f"invalid JSON in schema {schema_path.name}: {e}")


def _build_schema_registry() -> "Registry":
    """Registers every *.schema.json file under SCHEMAS_DIR by its own
    `$id` (a bare filename, per this project's convention -- e.g.
    "canon-entry.schema.json"), so canon-file.schema.json's/canon-batch's
    $refs to those filenames resolve regardless of load order.
    """
    if not SCHEMAS_DIR.is_dir():
        raise CanonValidationError(f"schemas directory not found: {SCHEMAS_DIR}")
    resources = []
    for schema_file in sorted(SCHEMAS_DIR.glob("*.schema.json")):
        contents = _load_schema_document(schema_file)
        schema_id = contents.get("$id", schema_file.name)
        resources.append((schema_id, Resource.from_contents(contents)))
    if not resources:
        raise CanonValidationError(f"no *.schema.json files found under {SCHEMAS_DIR}")
    return Registry().with_resources(resources)


def _draft202012_validator(schema: dict, registry: "Registry") -> "jsonschema.Draft202012Validator":
    """Constructs a jsonschema.Draft202012Validator with format_checker=
    _uri_format_checker() set EXPLICITLY. This is REQUIRED, not optional --
    jsonschema.validate()'s convenience wrapper does not enable format
    assertions by default, and canon-entry.schema.json's established->URI
    conditional depends entirely on the 'uri' format assertion actually
    running.
    """
    return jsonschema.Draft202012Validator(
        schema, registry=registry, format_checker=_uri_format_checker()
    )


def _validator_for_schema_file(schema_filename: str, registry: "Registry"):
    schema = _load_schema_document(SCHEMAS_DIR / schema_filename)
    return _draft202012_validator(schema, registry)


def _validator_for_ref(ref: str, registry: "Registry"):
    """Builds a validator for a bare $ref pointer into an already-registered
    schema document (e.g. "canon-batch.schema.json#/items" for the
    discriminated-union item shape, or
    "canon-batch.schema.json#/items/oneOf/1" for the QUEUED branch alone --
    the exact same pointer canon-file.schema.json itself uses for
    review_queue[] items).
    """
    wrapper = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$ref": ref,
    }
    return _draft202012_validator(wrapper, registry)


def _error_path_key(error):
    """Stable sort key for jsonschema ValidationError instances by
    instance-path, used to make error output deterministic across runs."""
    return [str(p) for p in error.path]


def _sorted_errors(validator, instance):
    """Runs validator.iter_errors() and returns the errors sorted by
    instance-path -- the one canonical way this script orders
    ValidationErrors for deterministic reporting."""
    return sorted(validator.iter_errors(instance), key=_error_path_key)


def _format_errors(errors):
    parts = []
    for e in errors:
        loc = "/".join(str(p) for p in e.path) or "<root>"
        parts.append(f"at '{loc}': {e.message}")
    return "; ".join(parts)


def _indexed_item_label(kind: str, index: int, item) -> str:
    """Builds a "kind[i]" or "kind[i] ('source_form')" label for a batch or
    review_queue item, so a Pass-1 failure names exactly which item broke."""
    source_form = item.get("source_form") if isinstance(item, dict) else None
    return f"{kind}[{index}]" + (f" ({source_form!r})" if source_form else "")


def _is_uri(value: str) -> bool:
    """A value is a valid URI iff urllib.parse.urlparse() yields BOTH a
    non-empty scheme AND a non-empty netloc -- i.e. a real absolute URL
    like "https://host/path", not a bare path or a scheme-less string.
    """
    parsed = urlparse(value)
    return bool(parsed.scheme) and bool(parsed.netloc)


def _check_uri_format(value) -> bool:
    """The 'uri' format checker registered on _uri_format_checker()'s
    FormatChecker. jsonschema's format_checker protocol requires a checker
    to either return a bool or raise one of its declared `raises` types --
    never silently no-op -- so a malformed value raises ValueError rather
    than returning False, matching how fc.checks(..., raises=(ValueError,))
    is registered below.
    """
    if not isinstance(value, str):
        return True  # format checks only apply to strings; type is schema's job
    if not _is_uri(value):
        raise ValueError(f"{value!r} is not a valid URI (need scheme + netloc)")
    return True


def _uri_format_checker() -> "jsonschema.FormatChecker":
    """Builds a jsonschema.FormatChecker with a stdlib urllib.parse-based
    'uri' checker registered on it, so canon-entry.schema.json's
    basis:"established" -> source:{format:"uri"} conditional is enforced
    deterministically regardless of whether the optional (GPLv3+) 'rfc3987'
    package is installed -- this plugin is intentionally stdlib-first and
    never adds rfc3987 to requirements.txt. Registering a custom 'uri'
    checker overrides jsonschema's own (rfc3987-backed, otherwise-no-op)
    default for that format name.
    """
    fc = jsonschema.FormatChecker()
    fc.checks("uri", raises=(ValueError,))(_check_uri_format)
    return fc


# ---------------------------------------------------------------------------
# canon.json I/O
# ---------------------------------------------------------------------------


def _read_json_file(path: Path, what: str):
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise CanonValidationError(f"{what} not found at {path}")
    except OSError as e:
        raise CanonValidationError(f"could not read {what} at {path}: {e}")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise CanonValidationError(f"{what} at {path} is not valid JSON: {e}")


def _load_canon(canon_path: Path) -> dict:
    """Loads canon.json, or -- if it does not exist yet -- returns a fresh
    skeleton (entries={}, review_queue=[]) for MERGE mode's very first
    glossary-pass batch on a brand-new project. generation_hashes is left
    absent here; MERGE mode always stamps it fresh before writing, and
    VALIDATE-ONLY mode on a not-yet-existing file is a hard error (nothing
    to validate).

    For an EXISTING file, the raw parsed shape is returned exactly as
    loaded -- 'entries'/'review_queue' are NEVER defaulted into existence
    here. Autofilling a missing top-level key before whole-file schema
    validation (Pass 2, `_validate_whole_file` against
    canon-file.schema.json) has run would silently paper over a genuinely
    malformed canon.json that is missing a required top-level field --
    exactly the failure Pass 2 exists to catch loudly, by name. Only the
    TYPE of an already-present key is checked here; presence itself is
    schema-validated downstream, never assumed here.
    """
    if not canon_path.is_file():
        return {"entries": {}, "review_queue": []}
    doc = _read_json_file(canon_path, "canon.json")
    if not isinstance(doc, dict):
        raise CanonValidationError(f"canon.json at {canon_path} is not a JSON object")
    if "entries" in doc and not isinstance(doc["entries"], dict):
        raise CanonValidationError(f"canon.json at {canon_path}: 'entries' is not an object")
    if "review_queue" in doc and not isinstance(doc["review_queue"], list):
        raise CanonValidationError(f"canon.json at {canon_path}: 'review_queue' is not an array")
    return doc


def _load_batch(batch_path_str: str) -> list:
    batch_path = Path(batch_path_str)
    doc = _read_json_file(batch_path, "batch file")
    if not isinstance(doc, list):
        raise CanonValidationError(f"batch file at {batch_path} does not contain a JSON array")
    return doc


def _load_source_forms_manifest(manifest_path_str: str) -> list:
    """Loads --expect-source-forms-file's own JSON array of expected
    source_form strings -- always a FILE, never inline argv (the manifest
    can be arbitrarily long and may contain names with spaces/apostrophes/
    unicode, none of which belong on a command line)."""
    manifest_path = Path(manifest_path_str)
    doc = _read_json_file(manifest_path, "--expect-source-forms-file")
    if not isinstance(doc, list) or not all(isinstance(x, str) for x in doc):
        raise CanonValidationError(
            f"--expect-source-forms-file at {manifest_path} must be a JSON "
            f"array of strings"
        )
    return doc


def _assert_exact_source_form_coverage(items: list, expected_forms: list) -> None:
    """Asserts the set of source_form values across `items` EXACTLY equals
    `expected_forms` -- no missing, no extra. Raises CanonValidationError
    naming both sides of any discrepancy (mirrors the naming discipline of
    every other CanonValidationError raised in this module)."""
    got = {item.get("source_form") for item in items if isinstance(item, dict)}
    want = set(expected_forms)
    missing = sorted(want - got)
    extra = sorted(got - want)
    if missing or extra:
        parts = []
        if missing:
            parts.append(f"missing from batch: {missing}")
        if extra:
            parts.append(f"unexpected extra in batch: {extra}")
        raise CanonValidationError(
            "batch does not exactly cover the expected source_form "
            "manifest (" + "; ".join(parts) + ")",
            offending=missing + extra,
        )


def _atomic_write_json(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.parent / f".{path.name}.tmp.{os.getpid()}"
    tmp_path.write_text(
        json.dumps(doc, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp_path, path)


def _stamp_generation_hash(field: str) -> str:
    """Shells out to `cache_key.py --field <field>` -- the one shared
    hashing implementation -- and returns its bare stdout value, stripped.
    Never independently recomputed here. A missing/failing cache_key.py, or
    an empty value, is FATAL: canon-file.schema.json only requires the
    generation_hashes KEYS be present strings, it cannot itself catch an
    empty-but-present value, so this script must refuse to write one.
    """
    if not CACHE_KEY_SCRIPT.is_file():
        raise CanonValidationError(
            f"cannot stamp generation_hashes.{field}: {CACHE_KEY_SCRIPT} not found"
        )
    try:
        proc = subprocess.run(
            [sys.executable, str(CACHE_KEY_SCRIPT), "--field", field],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=str(DURABLE_ROOT),
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        raise CanonValidationError(f"could not run cache_key.py --field {field}: {e}")
    if proc.returncode != 0:
        raise CanonValidationError(
            f"cache_key.py --field {field} exited {proc.returncode}: {proc.stderr.strip()}"
        )
    value = proc.stdout.strip()
    if not value:
        raise CanonValidationError(f"cache_key.py --field {field} printed an empty value")
    return value


# ---------------------------------------------------------------------------
# Pass 1 -- per-item validation
# ---------------------------------------------------------------------------


def _validate_batch_items(batch: list, registry: "Registry") -> None:
    """Pass 1, MERGE mode: every batch item independently re-validated
    against canon-batch.schema.json's own discriminated-union item shape
    (canon-batch.schema.json#/items) -- never the bare
    canon-entry.schema.json shape, since a batch item also carries
    'disposition'. Raises CanonValidationError naming every offending item
    (by index and, when present, source_form) if any item fails.
    """
    validator = _validator_for_ref("canon-batch.schema.json#/items", registry)
    problems = []
    for i, item in enumerate(batch):
        errors = _sorted_errors(validator, item)
        if errors:
            label = _indexed_item_label("batch", i, item)
            problems.append(f"{label}: {_format_errors(errors)}")
    if problems:
        raise CanonValidationError(
            "batch failed per-item schema validation:\n  " + "\n  ".join(problems),
            offending=problems,
        )


def _validate_existing_entries(canon: dict, registry: "Registry") -> None:
    """Pass 1, VALIDATE-ONLY mode: every entries{} value validated against
    canon-entry.schema.json directly, and every review_queue[] item against
    the QUEUED shape -- the same per-item discipline as MERGE mode's Pass 1,
    applied to an already-merged file instead of an incoming batch.
    """
    entry_validator = _validator_for_schema_file("canon-entry.schema.json", registry)
    queued_validator = _validator_for_ref("canon-batch.schema.json#/items/oneOf/1", registry)
    problems = []

    for source_form, entry in sorted(canon.get("entries", {}).items()):
        errors = _sorted_errors(entry_validator, entry)
        if errors:
            problems.append(f"entries[{source_form!r}]: {_format_errors(errors)}")

    for i, item in enumerate(canon.get("review_queue", [])):
        errors = _sorted_errors(queued_validator, item)
        if errors:
            label = _indexed_item_label("review_queue", i, item)
            problems.append(f"{label}: {_format_errors(errors)}")

    if problems:
        raise CanonValidationError(
            "canon.json failed per-item schema validation:\n  " + "\n  ".join(problems),
            offending=problems,
        )


# ---------------------------------------------------------------------------
# Offline research-mode backstop
# ---------------------------------------------------------------------------


def _enforce_offline_backstop(batch: list, research_mode: str) -> None:
    """If research_mode == "offline", FATALLY rejects the whole batch merge
    when ANY item claims basis:"established" -- accepted or queued alike,
    matching the authoritative spec's literal "ANY entry" wording. Nothing
    is written to canon.json when this fires; the correct fix is upstream,
    in the glossary-pass agent's own output (basis:"transliterated", or
    disposition:"review_queue" with a note:"SOURCE_UNAVAILABLE: ..."
    prefix), never a silent downgrade performed by this script.
    """
    if research_mode != "offline":
        return
    offenders = [
        item.get("source_form", f"<item {i}>")
        for i, item in enumerate(batch)
        if isinstance(item, dict) and item.get("basis") == "established"
    ]
    if offenders:
        raise CanonValidationError(
            "research_mode=offline forbids basis:\"established\" for every new "
            "entry, but the batch claims it for: " + ", ".join(repr(o) for o in offenders)
            + ". Reassign basis:\"transliterated\" (if mechanical transliteration "
            "suffices) or disposition:\"review_queue\" with a note carrying the "
            "literal prefix \"SOURCE_UNAVAILABLE:\" instead -- the whole batch "
            "merge is rejected, canon.json is unchanged.",
            offending=offenders,
        )


# ---------------------------------------------------------------------------
# Merge (dedup + collision checks, routing by disposition)
# ---------------------------------------------------------------------------


def _entry_from_accepted_item(item: dict) -> dict:
    """Strips 'disposition' (and any other non-canon-entry key) from an
    ACCEPTED batch item, leaving exactly canon-entry.schema.json's own
    field set -- that schema is additionalProperties:false and has no
    'disposition' property at all.
    """
    return {k: item[k] for k in CANON_ENTRY_FIELDS if k in item}


def _merge_batch(canon: dict, batch: list) -> dict:
    """Merges a Pass-1-validated, offline-backstop-cleared batch into an
    in-memory copy of `canon`. Never mutates `canon` in place, and never
    touches disk -- the caller writes only after this returns successfully.
    Raises CanonValidationError (naming both old and new values) on a
    genuine cross-run collision: two different resolutions claimed for the
    same source_form. An identical re-submission is a silent no-op.
    """
    entries = dict(canon.get("entries", {}))
    review_queue = list(canon.get("review_queue", []))
    collisions = []

    for item in batch:
        disposition = item.get("disposition")
        source_form = item.get("source_form")

        if disposition == "accepted":
            new_entry = _entry_from_accepted_item(item)
            existing = entries.get(source_form)
            if existing is not None and existing != new_entry:
                collisions.append(
                    f"{source_form!r}: existing entry {existing!r} conflicts with "
                    f"newly merged {new_entry!r}"
                )
                continue
            entries[source_form] = new_entry
            # A name that is now resolved and accepted no longer belongs in
            # review_queue -- drop any queued entries for the same
            # source_form, since it has just been frozen.
            review_queue = [
                q for q in review_queue if q.get("source_form") != source_form
            ]

        elif disposition == "review_queue":
            if source_form in entries:
                # Already resolved/accepted -- either from a prior
                # canon.json, or from an EARLIER item in this same batch
                # (or an earlier fragment, when this is called repeatedly
                # by run_merge_batches's threaded acc = _merge_batch(acc,
                # frag) loop) -- a review_queue submission for an
                # already-accepted source_form is superseded, never
                # appended, regardless of merge order.
                continue
            if item not in review_queue:
                review_queue.append(item)

        else:  # pragma: no cover -- Pass 1 schema validation already rejects this
            collisions.append(
                f"{source_form!r}: unrecognized disposition {disposition!r}"
            )

    if collisions:
        raise CanonValidationError(
            "batch merge rejected due to entries{} collision(s):\n  "
            + "\n  ".join(collisions),
            offending=collisions,
        )

    merged = dict(canon)
    merged["entries"] = entries
    merged["review_queue"] = review_queue
    return merged


# ---------------------------------------------------------------------------
# Pass 2 -- whole-file validation
# ---------------------------------------------------------------------------


def _validate_whole_file(canon: dict, registry: "Registry") -> None:
    validator = _validator_for_schema_file("canon-file.schema.json", registry)
    errors = _sorted_errors(validator, canon)
    if errors:
        raise CanonValidationError(
            f"canon.json failed whole-file schema validation: {_format_errors(errors)}"
        )


# ---------------------------------------------------------------------------
# Top-level modes
# ---------------------------------------------------------------------------


def _stamp_write_verify(canon_path: Path, merged: dict, registry: "Registry") -> dict:
    """Shared by every mode that writes canon.json (`run_merge`,
    `run_merge_batches`): stamps generation_hashes fresh onto the in-memory
    `merged` document, Pass-2-validates it BEFORE ever touching disk (so a
    corrupted merge is caught before it's written, not just after), performs
    ONE atomic write, then re-reads the JUST-WRITTEN file fresh from disk
    and Pass-2-validates it AGAIN -- genuinely from disk, with NO masking
    fallback for a missing generation_hashes value (the pre-1.2.0 version of
    this function re-injected the just-stamped value via
    `on_disk.setdefault("generation_hashes", ...)` here, which silently
    defeated the whole point of the post-write re-read: a write that
    somehow dropped generation_hashes would still "validate" against the
    value this script itself remembered, not what actually landed on disk).
    Returns the freshly re-read on-disk document.
    """
    merged.setdefault("generation_hashes", {})
    for field in GENERATION_HASH_FIELDS:
        merged["generation_hashes"][field] = _stamp_generation_hash(field)

    _validate_whole_file(merged, registry)

    _atomic_write_json(canon_path, merged)

    on_disk = _load_canon(canon_path)
    _validate_whole_file(on_disk, registry)
    return on_disk


def run_merge(canon_path: Path, batch_path: str, research_mode: str, registry: "Registry") -> dict:
    """Legacy single-fragment merge path (--batch PATH). Equivalent to
    `run_merge_batches(canon_path, [batch_path], ...)`, kept as its own
    code path because existing tests/callers already invoke it this way.
    """
    batch = _load_batch(batch_path)
    canon = _load_canon(canon_path)

    _validate_batch_items(batch, registry)
    _enforce_offline_backstop(batch, research_mode)
    merged = _merge_batch(canon, batch)

    on_disk = _stamp_write_verify(canon_path, merged, registry)

    n_accepted = sum(1 for item in batch if item.get("disposition") == "accepted")
    n_queued = sum(1 for item in batch if item.get("disposition") == "review_queue")
    return {
        "success": True,
        "mode": "merge",
        "canon_path": str(canon_path),
        "research_mode": research_mode,
        "batch_items": len(batch),
        "merged_accepted": n_accepted,
        "merged_queued": n_queued,
        "entries_count": len(on_disk["entries"]),
        "review_queue_count": len(on_disk["review_queue"]),
    }


def run_check_batch(
    batch_path: str, research_mode: str, manifest_path: "str | None", registry: "Registry"
) -> dict:
    """--check-batch PATH [--expect-source-forms-file M.json]: Pass 1 +
    offline backstop on ONE fragment, NO write. When a manifest is given,
    additionally asserts exact source_form coverage."""
    batch = _load_batch(batch_path)
    _validate_batch_items(batch, registry)
    _enforce_offline_backstop(batch, research_mode)
    if manifest_path is not None:
        expected_forms = _load_source_forms_manifest(manifest_path)
        _assert_exact_source_form_coverage(batch, expected_forms)

    return {
        "success": True,
        "mode": "check_batch",
        "source_forms": len({item.get("source_form") for item in batch if isinstance(item, dict)}),
    }


def run_merge_batches(
    canon_path: Path, batch_paths: list, research_mode: str, registry: "Registry"
) -> dict:
    """--merge-batches P1 P2 ...: single process, single canon.json load.
    Validates ALL given fragments (Pass 1 + offline backstop) FIRST, before
    merging any of them, then threads `acc = _merge_batch(acc, frag)`
    across every fragment IN THE GIVEN ORDER."""
    batches = [_load_batch(p) for p in batch_paths]
    for batch in batches:
        _validate_batch_items(batch, registry)
        _enforce_offline_backstop(batch, research_mode)

    canon = _load_canon(canon_path)
    acc = canon
    for batch in batches:
        acc = _merge_batch(acc, batch)

    on_disk = _stamp_write_verify(canon_path, acc, registry)

    n_accepted = sum(1 for batch in batches for item in batch if item.get("disposition") == "accepted")
    n_queued = sum(1 for batch in batches for item in batch if item.get("disposition") == "review_queue")
    return {
        "success": True,
        "mode": "merge_batches",
        "canon_path": str(canon_path),
        "research_mode": research_mode,
        "fragments_merged": len(batch_paths),
        "merged_accepted": n_accepted,
        "merged_queued": n_queued,
        "entries_count": len(on_disk["entries"]),
        "review_queue_count": len(on_disk["review_queue"]),
    }


def _verify_merged_item(canon: dict, item: dict) -> "str | None":
    """Verifies ONE already-processed batch item is correctly reflected in
    the CURRENT canon.json, by disposition. Returns the item's own
    source_form (to be reported in `missing`) if verification fails, or
    None if it passes."""
    source_form = item.get("source_form") if isinstance(item, dict) else None
    label = source_form if isinstance(source_form, str) and source_form else "<item without a valid source_form>"
    disposition = item.get("disposition") if isinstance(item, dict) else None

    if disposition == "accepted":
        expected_entry = _entry_from_accepted_item(item)
        actual_entry = canon.get("entries", {}).get(source_form)
        return None if actual_entry == expected_entry else label

    if disposition == "review_queue":
        in_queue = item in canon.get("review_queue", [])
        # Accept-supersedes: a LATER batch's accepted resolution for the
        # same source_form is not a failure -- never reported missing.
        superseded = isinstance(source_form, str) and source_form in canon.get("entries", {})
        return None if (in_queue or superseded) else label

    # An unrecognized disposition here means the fragment was never Pass-1
    # validated before --verify-merged ran (this mode is disk-independent
    # and does not itself re-run Pass 1) -- unverifiable, report it.
    return label


def run_verify_merged(
    canon_path: Path, batch_paths: list, manifest_path: "str | None", registry: "Registry"
) -> dict:
    """--verify-merged --batch F1 [--batch F2 ...] [--expect-source-forms-file
    M.json]: disk-INDEPENDENT verification, fresh reads only, no write."""
    if not canon_path.is_file():
        raise CanonValidationError(f"canon.json not found at {canon_path} (nothing to verify)")
    canon = _load_canon(canon_path)

    missing = []
    covered_forms = set()
    for batch_path in batch_paths:
        batch = _load_batch(batch_path)
        for item in batch:
            source_form = item.get("source_form") if isinstance(item, dict) else None
            if isinstance(source_form, str) and source_form:
                covered_forms.add(source_form)
            failure_label = _verify_merged_item(canon, item)
            if failure_label is not None:
                missing.append(failure_label)

    if manifest_path is not None:
        expected_forms = _load_source_forms_manifest(manifest_path)
        missing.extend(sorted(set(expected_forms) - covered_forms))

    missing = sorted(set(missing))
    return {"verified": not missing, "missing": missing}


def run_validate_only(canon_path: Path, research_mode: str, registry: "Registry") -> dict:
    if not canon_path.is_file():
        raise CanonValidationError(f"canon.json not found at {canon_path} (nothing to validate)")
    canon = _load_canon(canon_path)

    _validate_existing_entries(canon, registry)
    _validate_whole_file(canon, registry)

    return {
        "success": True,
        "mode": "validate",
        "canon_path": str(canon_path),
        "research_mode": research_mode,
        "entries_count": len(canon["entries"]),
        "review_queue_count": len(canon["review_queue"]),
    }


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Two-pass jsonschema validation (and, with --batch/"
            "--merge-batches, merge) backstop for canon.json -- see this "
            "file's own module docstring and references/canon-and-"
            "glossary.md for the full spec."
        )
    )
    parser.add_argument(
        "--research-mode",
        required=True,
        choices=RESEARCH_MODES,
        help=(
            "REQUIRED, never defaulted, for EVERY mode below -- profile.yml's "
            "own glossary.research_mode, resolved once by the orchestrating "
            "Claude session. In any mode that validates a batch fragment "
            "(--check-batch, --merge-batches, legacy --batch), 'offline' "
            "fatally forbids basis:\"established\" for every new entry. Has "
            "no effect in --verify-merged or VALIDATE-ONLY mode -- kept "
            "required anyway so no call site can accidentally omit "
            "declaring the precondition."
        ),
    )
    parser.add_argument(
        "--batch",
        metavar="PATH",
        action="append",
        default=None,
        help=(
            "Legacy single-fragment MERGE mode when given ALONE (a JSON "
            "array, canon-batch.schema.json shape): Pass 1 + offline "
            "backstop + dedup/collision merge + generation_hashes stamping "
            "+ atomic write + Pass 2. Repeatable ONLY under --verify-merged, "
            "where it names the set of already-processed fragments to "
            "verify against the current canon.json. Omitted entirely: runs "
            "VALIDATE-ONLY mode against the existing canon.json (no write)."
        ),
    )
    parser.add_argument(
        "--check-batch",
        metavar="PATH",
        default=None,
        help=(
            "Pass 1 + offline backstop on the ONE fragment at PATH, NO "
            "write. Combine with --expect-source-forms-file for exact "
            "coverage checking."
        ),
    )
    parser.add_argument(
        "--merge-batches",
        metavar="PATH",
        nargs="+",
        default=None,
        help=(
            "Merge MULTIPLE fragments, in the given order, in ONE atomic "
            "operation (validate all first, thread the merge, stamp "
            "generation_hashes, in-memory Pass 2, one atomic write, "
            "disk-re-read Pass 2)."
        ),
    )
    parser.add_argument(
        "--verify-merged",
        action="store_true",
        help=(
            "Disk-independent verification that the fragment(s) named by "
            "--batch are correctly reflected in the CURRENT canon.json -- "
            "no write. Requires one or more --batch PATH."
        ),
    )
    parser.add_argument(
        "--expect-source-forms-file",
        metavar="PATH",
        default=None,
        help=(
            "Path to a JSON array of expected source_form strings (a FILE, "
            "never inline argv). --check-batch: asserts the one fragment's "
            "own coverage is EXACT. --verify-merged: asserts the union of "
            "every named fragment's coverage is EXACT against this list "
            "(pass the aggregate manifest_all.json here for the final "
            "verify call)."
        ),
    )
    parser.add_argument(
        "--canon-path",
        metavar="PATH",
        default=None,
        help=(
            f"Override the canon.json path (default: "
            f"{DEFAULT_CANON_PATH})."
        ),
    )
    return parser


def main(argv=None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    canon_path = Path(args.canon_path) if args.canon_path else DEFAULT_CANON_PATH

    modes_selected = sum([
        args.check_batch is not None,
        args.merge_batches is not None,
        args.verify_merged,
    ])
    if modes_selected > 1:
        parser.error(
            "--check-batch, --merge-batches, and --verify-merged are "
            "mutually exclusive"
        )
    if args.verify_merged and not args.batch:
        parser.error("--verify-merged requires one or more --batch PATH")
    if not args.verify_merged and args.batch is not None and len(args.batch) > 1:
        parser.error(
            "--batch may be given more than once only under --verify-merged"
        )
    if args.merge_batches is not None and args.expect_source_forms_file is not None:
        # --merge-batches does not enforce source-form coverage (that is the
        # job of --check-batch per fragment and --verify-merged for the merged
        # set); silently dropping the flag would give a false sense that merge
        # verified coverage. Fail loud instead.
        parser.error(
            "--expect-source-forms-file is not accepted with --merge-batches "
            "(coverage is enforced by --check-batch per fragment and by "
            "--verify-merged for the merged set)"
        )

    try:
        registry = _build_schema_registry()
        if args.check_batch is not None:
            result = run_check_batch(
                args.check_batch, args.research_mode, args.expect_source_forms_file, registry
            )
        elif args.merge_batches is not None:
            result = run_merge_batches(canon_path, args.merge_batches, args.research_mode, registry)
        elif args.verify_merged:
            result = run_verify_merged(
                canon_path, args.batch, args.expect_source_forms_file, registry
            )
        elif args.batch is not None:
            result = run_merge(canon_path, args.batch[0], args.research_mode, registry)
        else:
            result = run_validate_only(canon_path, args.research_mode, registry)
    except CanonValidationError as e:
        payload = {"success": False, "error": str(e)}
        if e.offending is not None:
            payload["offending"] = e.offending
        print(json.dumps(payload, ensure_ascii=False))
        return 1
    except Exception as e:  # pragma: no cover -- defensive catch-all
        print(
            json.dumps({"success": False, "error": f"unexpected error: {e}"}, ensure_ascii=False)
        )
        return 1

    print(json.dumps(result, ensure_ascii=False))
    if args.verify_merged:
        return 0 if result.get("verified") else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
