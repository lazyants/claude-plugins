#!/usr/bin/env python3
"""canon_validate.py -- two-pass schema validation (and merge) backstop for
canon.json, the literary-translator plugin's frozen, hash-versioned,
cross-segment name/realia glossary.

STATUS: new plugin hardening -- the glossary-pass Workflow template that
feeds this script's MERGE mode is itself not yet source-proven (see
references/canon-and-glossary.md's "Glossary-pass call discipline"
section: the real historiettes-t3 project ran its glossary pass as ad hoc
`glossary/TASK.md` + codex batches, never through this schema-validated
pipeline). Authoritative spec for everything below:
references/canon-and-glossary.md, sections "`canon_validate.py`'s two
validation passes" and "Research preflight and offline-fallback policy for
`basis: "established"`" -- read those before changing anything here; this
file's behavior must match that doc exactly.

Two independent operating modes, selected by whether --batch is given:

MERGE mode (--batch PATH given)
    Reads a glossary-pass batch result -- the real return contract of
        agent(glossaryPrompt(batch), {agentType:'codex:codex-rescue',
        effort:'high', schema: CANON_BATCH_SCHEMA})
    (a JSON array on disk, canon-batch.schema.json shape) and merges it
    into canon.json:

    Pass 1 (per-batch-item). Every item is independently re-validated
    against canon-batch.schema.json's own discriminated-union item shape
    -- via jsonschema.Draft202012Validator(..., format_checker=<a
    FormatChecker with a stdlib urllib.parse-based "uri" checker registered
    on it>) set EXPLICITLY, since jsonschema.validate()'s convenience
    wrapper does not enable format assertions by default -- never the bare
    canon-entry.schema.json shape, since a batch item also carries the
    'disposition' field that shape does not have.

    Offline research-mode backstop. If --research-mode offline and ANY
    item in the batch claims basis:"established" (accepted or queued --
    the spec says "ANY entry", not "any accepted entry"), the WHOLE merge
    is FATALLY REJECTED, naming every offending item's source_form --
    never silently downgraded, never silently accepted. Nothing is written
    to canon.json when this fires. The correct upstream fix is for the
    glossary-pass agent to have assigned basis:"transliterated", or
    disposition:"review_queue" with a note carrying the literal prefix
    "SOURCE_UNAVAILABLE:" instead -- this script only enforces the
    backstop; it never re-decides an accuracy call itself (that is exactly
    the kind of judgment call this plugin reserves for codex, never a
    script).

    Dedup + collision checks, then routes each item into canon.json's
    entries{} (disposition:"accepted" -> canon-entry.schema.json shape,
    'disposition' field stripped -- entries{} values are
    additionalProperties:false and have no such field) or review_queue[]
    (disposition:"review_queue" -> appended AS-IS, 'disposition' kept --
    canon-file.schema.json's own review_queue items $ref the QUEUED branch,
    which requires 'disposition'). A source_form re-submitted identically
    to what is already on file is a silent no-op (idempotent re-run); a
    source_form re-submitted with a DIFFERENT resolution is a fatal
    collision, naming both the old and new values -- canon.json entries are
    frozen, never silently overwritten.

    Stamps generation_hashes.particle_config_hash and
    .derivation_bundle_hash by shelling out to `cache_key.py --field
    <name>` (the one shared hashing implementation -- never independently
    recomputed here).

    Atomically writes canon.json (tmp-write-then-os.replace(), the same
    durable pattern ledger_update.py/ledger_merge.py use).

    Pass 2 (whole-file). Re-reads the JUST-WRITTEN canon.json fresh from
    disk and validates it against canon-file.schema.json, fatally halting
    -- naming the specific problem -- if entries{} / review_queue /
    generation_hashes.particle_config_hash /
    generation_hashes.derivation_bundle_hash are missing or malformed. This
    is the check that actually enforces the two generation_hashes fields'
    presence, which select_segments.py's derivation-state gate is entirely
    load-bearing on.

VALIDATE-ONLY mode (--batch omitted)
    A read-only health check: no merge, no write, no offline backstop (that
    backstop only ever applies to NEW entries in an incoming --batch, per
    the authoritative spec's own "for every new entry" framing -- an
    already-frozen canon.json is not retroactively re-litigated just
    because this run happens to pass --research-mode offline for other
    reasons).

    Pass 1 (per-entry). Every canon.json entries{} value is independently
    validated against canon-entry.schema.json, and every review_queue[]
    item against the same QUEUED shape MERGE mode uses.

    Pass 2 (whole-file). The loaded document is validated against
    canon-file.schema.json.

    --research-mode is still a required flag in this mode (never
    defaulted, per the authoritative spec) even though it has no effect
    here -- kept required uniformly so no call site can accidentally omit
    declaring the precondition.

Reads canon-entry.schema.json / canon-batch.schema.json /
canon-file.schema.json from ${durable_root}/schemas/ -- never the plugin's
own assets/schemas/ (this script always runs from the durable, per-project
copy).

Usage:
    python3 canon_validate.py --research-mode live --batch glossary_out.json
    python3 canon_validate.py --research-mode offline --batch glossary_out.json
    python3 canon_validate.py --research-mode live
    python3 canon_validate.py --research-mode live --canon-path /path/to/canon.json

Exit code 0 on success, 1 on failure. Exactly one JSON line is printed to
stdout either way -- callers (the glossary-pass merge step, tests) should
read stdout, not rely on the exit code alone.
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


def run_merge(canon_path: Path, batch_path: str, research_mode: str, registry: "Registry") -> dict:
    batch = _load_batch(batch_path)
    canon = _load_canon(canon_path)

    _validate_batch_items(batch, registry)
    _enforce_offline_backstop(batch, research_mode)
    merged = _merge_batch(canon, batch)

    merged.setdefault("generation_hashes", {})
    for field in GENERATION_HASH_FIELDS:
        merged["generation_hashes"][field] = _stamp_generation_hash(field)

    _atomic_write_json(canon_path, merged)

    # Pass 2 re-reads the JUST-WRITTEN file fresh from disk -- never trusts
    # the in-memory `merged` dict this script itself just built.
    on_disk = _load_canon(canon_path)
    on_disk.setdefault("generation_hashes", merged.get("generation_hashes", {}))
    _validate_whole_file(on_disk, registry)

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
            "Two-pass jsonschema validation (and, with --batch, merge) "
            "backstop for canon.json -- see this file's own module "
            "docstring and references/canon-and-glossary.md for the full "
            "spec."
        )
    )
    parser.add_argument(
        "--research-mode",
        required=True,
        choices=RESEARCH_MODES,
        help=(
            "REQUIRED, never defaulted -- profile.yml's own "
            "glossary.research_mode, resolved once by the orchestrating "
            "Claude session. In MERGE mode (--batch given), 'offline' "
            "fatally forbids basis:\"established\" for every new entry in "
            "the batch. Has no effect in VALIDATE-ONLY mode (--batch "
            "omitted) -- kept required anyway so no call site can "
            "accidentally omit declaring the precondition."
        ),
    )
    parser.add_argument(
        "--batch",
        metavar="PATH",
        default=None,
        help=(
            "Path to a glossary-pass batch result JSON file (an array, "
            "canon-batch.schema.json shape). When given, runs MERGE mode: "
            "Pass 1 + offline backstop + dedup/collision merge + "
            "generation_hashes stamping + atomic write + Pass 2. When "
            "omitted, runs VALIDATE-ONLY mode against the existing "
            "canon.json (no write)."
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

    try:
        registry = _build_schema_registry()
        if args.batch is not None:
            result = run_merge(canon_path, args.batch, args.research_mode, registry)
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
    return 0


if __name__ == "__main__":
    sys.exit(main())
