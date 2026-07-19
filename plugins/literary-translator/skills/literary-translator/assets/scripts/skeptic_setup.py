#!/usr/bin/env python3
"""skeptic_setup.py -- the skeptic pass's own resume-domain owner + assignment
manifest writer (RFC #215 Phase 2, "skeptic pass", 1.6.0, plan Part B).

This is the skeptic analogue of ``resume_setup.py`` -- SAME discipline
(validate-before-any-write, an input-digest gate deciding resume vs a fresh
run, atomic manifests written BEFORE any agent dispatch) -- but a SEPARATE,
standalone owner: it is NOT a ``PLUGIN_BUNDLE_MEMBERS`` entry (editing it
must never re-translate a converged mass/glossary segment), it does NOT add
a ``kind`` to ``resume_setup.py``, and ``resume_setup.py`` is never edited or
imported here. The skeptic pass owns its own resume domain end to end.

Two independent problems this script closes, both BEFORE any codex dispatch:

1. WORKLIST FRESHNESS: ``suspicion_scan.py`` stamps ``suspicion_worklist.json``
   with a ``producer_input_digest`` -- a sha256 over its own entire
   behavior-determining closure (canon.json, manifest.json, the resolved scan
   parameters, the resolved particle-config file's raw bytes, and the
   producer's own code closure). This script recomputes that IDENTICAL
   digest via the shared ``suspicion_scan.compute_producer_input_digest()``
   helper (never a second, independently-drifting reimplementation) and
   REJECTS a stale worklist outright, fail-closed -- so no upstream edit
   (canon, manifest, scan parameters, particle config, or the scanner/
   matcher/normalizer code itself) can let a since-invalidated worklist be
   silently reprocessed.

2. COVERAGE DISCIPLINE: this script is the SINGLE TRUSTED WRITER of the
   skeptic run's assignment manifests -- one per-batch fragment
   (``assignments_{index}.json``, a bare JSON array of that batch's own
   ``assignment_id`` strings, mirroring ``resume_setup.py``'s
   ``manifest_{index}.json`` convention exactly) plus the aggregate
   (``assignments.json``, schema ``skeptic-assignment.schema.json``) -- built
   directly from the worklist's own ``origin="block"`` occurrence_refs
   (CITABLE windows only; ``origin="verse_embedded"`` refs are label-only and
   never fed to the skeptic as a citable window), written atomically BEFORE
   any dispatch happens. ``skeptic-pass-wf.template.js`` feeds the per-batch
   file to ``skeptic_ready.py --validate-fragment --expect-assignments-file``
   as its own coverage ground truth, so a codex batch call can't pass its own
   self-check by quietly dropping an assigned entity.

Any failure here ABORTS (nonzero exit) before any dispatch -- never a
partial/best-effort setup. Mirrors ``resume_setup.py``'s own
validate-before-any-write discipline: a malformed/stale worklist, or a batch
count that would exceed ``--batch-agent-cap``, aborts with NOTHING written
under ``{durable_root}/skeptic/runs/`` at all -- not even a fresh RUN_ID's
``input.digest``.

CLI:

    python3 skeptic_setup.py --canon PATH --manifest PATH --worklist PATH
        --particle-config FILENAME --research-mode {live,offline}
        --source-format FORMAT
        --batch-agent-cap N --source-lang LANG
        [--senses-path PATH]
        [--languages-dir PATH] [--dispersion-threshold N] [--sample-cap N]
        [--windows-per-entity N] [--near-threshold F] [--near-cap N]
        [--near-pair-budget N] [--citation-block-types [TYPE ...]]
        [--entities-per-batch N] [--resume-from-run-id RUN_ID]

``--source-lang`` is the skeptic-only language token
``skeptic-pass-wf.template.js`` interpolates into the skeptic prompt as
``{{SOURCE_LANG}}`` (Fix M8) -- folded into ``config_values``/the skeptic
``input_digest`` below, NOT into ``producer_input_digest`` (the producer's
own scan has no notion of it). There is deliberately NO ``--target-lang``:
the skeptic pass never translates or canonicalizes anything, so target
language is irrelevant to what it reads or reasons about, and folding it in
would force a spurious fresh RUN_ID on a change this pass never observes.

Every scan-parameter flag (``--dispersion-threshold`` etc.) MUST be passed
the exact same resolved value the caller passed to ``suspicion_scan.py`` for
this worklist -- that is what lets the recomputed ``producer_input_digest``
match. A mismatched value here is indistinguishable, by design, from a
genuinely stale worklist: both fail closed the same way.

On success, prints one JSON line:

    {"success": true, "effectiveRunId": "...", "resume": true|false,
     "run_dir": "...", "input_digest": "...", "producer_input_digest": "...",
     "batch_count": N, "assignment_count": N}

On failure: {"success": false, "error": "..."}. Exit code 0/1 either way --
callers should read stdout, not rely on the exit code alone (same contract
as ``resume_setup.py``).

Self-anchored: this script always lives at
``${durable_root}/scripts/skeptic_setup.py``, so ``parents[1]`` is the
durable root. Never assumes cwd, never takes a ``--durable-root`` flag.

skeptic ``input_digest`` (see ``compute_skeptic_input_digest()`` below):
the producer set (canon.json, manifest.json, canon_senses.json's own raw
bytes (#243), the resolved scan parameters, the resolved
``LanguageConfig.raw_bytes``, and the producer's own code closure -- reused
verbatim via ``suspicion_scan.compute_producer_input_digest``)
PLUS: the worklist's own raw bytes, the fully-resolved per-entity windows +
batch assignment (so even a bug in THIS script's own derivation algorithm is
covered, belt-and-suspenders, on top of this script's own bytes already
being in the closure below), every CLI/config value (including the
skeptic-only ones, ``--entities-per-batch``/``--batch-agent-cap``/
``--source-lang`` -- the last is the ``{{SOURCE_LANG}}`` token
``skeptic-pass-wf.template.js`` interpolates into the skeptic prompt, Fix
M8), the schemas-dir hash, and the FULL skeptic code closure: every producer member
PLUS ``skeptic_setup.py`` (this file), ``skeptic-pass-wf.template.js``,
``skeptic_ready.py``, ``skeptic_report.py``, and ``evidence_verify.py``. A
change to ANY of these forces a fresh skeptic RUN_ID (this run's own resume
domain -- entirely separate from any mass/glossary RUN_ID) while leaving
every one of the 15 segment ``cache_key`` fields untouched (neither this
script nor any of its siblings is a ``PLUGIN_BUNDLE_MEMBERS`` entry, and
``schema_hash`` only ever hashes ``draft.schema.json``/``review.schema.json``/
``segpack.schema.json`` -- never the whole ``schemas/`` directory).

Filename note: the per-batch fragment filename pattern
``assignments_{index}.json`` (``ASSIGNMENT_BATCH_PREFIX`` below) matches
what ``skeptic-pass-wf.template.js`` (A3) already reads via its own
``assignmentsBatchPath()`` helper, passed to
``skeptic_ready.py --validate-fragment --expect-assignments-file``. Not yet
a ``skeptic_constants.py`` member (proposed to LEAD for promotion, mirroring
``SKEPTIC_FRAGMENT_PREFIX``'s own entry there).
"""
import argparse
import hashlib
import json
import os
import re
import secrets
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

try:
    import jsonschema
except ImportError as exc:
    sys.stderr.write(
        "skeptic_setup.py requires the 'jsonschema' package (>=4.26.0). "
        "Install with:\n\n    pip install -r requirements.txt\n\n"
        f"(import error: {exc})\n"
    )
    sys.exit(1)

# ---------------------------------------------------------------------------
# Self-anchoring (mirrors resume_setup.py exactly)
# ---------------------------------------------------------------------------
_SCRIPT_FILE = Path(__file__).resolve()
SCRIPT_DIR = _SCRIPT_FILE.parent
DURABLE_ROOT = _SCRIPT_FILE.parents[1]
SCHEMAS_DIR = DURABLE_ROOT / "schemas"
TEMPLATES_DIR = DURABLE_ROOT / "templates"
LANGUAGES_DIR = DURABLE_ROOT / "languages"

try:
    from skeptic_constants import (
        DISPERSION_THRESHOLD_DEFAULT,
        SAMPLE_CAP_DEFAULT,
        WINDOWS_PER_ENTITY_DEFAULT,
        NEAR_THRESHOLD_DEFAULT,
        NEAR_CAP_DEFAULT,
        NEAR_PAIR_BUDGET_DEFAULT,
        OCC_ORIGIN_BLOCK,
        OCC_ORIGIN_VERSE_EMBEDDED,
        SUSPICION_WORKLIST_FILENAME,
        SKEPTIC_RUNS_SUBDIR,
        SKEPTIC_AGGREGATE_MANIFEST_FILENAME,
        SKEPTIC_INPUT_DIGEST_FILENAME,
        SUSPICION_WORKLIST_SCHEMA,
        SKEPTIC_ASSIGNMENT_SCHEMA,
    )
except ImportError as exc:
    sys.exit(
        f"skeptic_setup.py: cannot import skeptic_constants.py from {SCRIPT_DIR} ({exc}).\n"
        "skeptic_constants.py must be installed alongside skeptic_setup.py under "
        "${durable_root}/scripts/ -- it supplies the single source-of-truth default "
        "values/tags every skeptic-pass script shares. Re-run Step 0a, or verify the "
        "plugin install is not corrupted."
    )

try:
    from bootstrap_names import load_language_config, BootstrapNamesError
except ImportError as exc:
    sys.exit(
        f"skeptic_setup.py: cannot import bootstrap_names.py from {SCRIPT_DIR} ({exc}).\n"
        "bootstrap_names.py must be installed alongside skeptic_setup.py under "
        "${durable_root}/scripts/ -- Step 0a copies the whole scripts/ set together."
    )

try:
    from suspicion_scan import (
        compute_producer_input_digest,
        compute_frozen_input_hash,
        resolved_scan_params,
        resolve_citation_block_types,
        PRODUCER_CODE_CLOSURE,
    )
except ImportError as exc:
    sys.exit(
        f"skeptic_setup.py: cannot import suspicion_scan.py from {SCRIPT_DIR} ({exc}).\n"
        "suspicion_scan.py must be installed alongside skeptic_setup.py under "
        "${durable_root}/scripts/ -- it supplies the shared producer_input_digest "
        "algorithm this script re-verifies worklist freshness against (never a second, "
        "independently-drifting implementation). Re-run Step 0a, or verify the plugin "
        "install is not corrupted."
    )

DEFAULT_CANON_PATH = DURABLE_ROOT / "canon.json"
DEFAULT_MANIFEST_PATH = DURABLE_ROOT / "manifest.json"
# Sibling of DEFAULT_CANON_PATH, self-anchored the same way -- each consumer
# computes its own copy (see canon_senses.py's own module docstring on why
# DEFAULT_SENSES_PATH is deliberately not defined there). #243: the sidecar
# became a THIRD frozen producer input this script recomputes/re-stamps.
DEFAULT_SENSES_PATH = DURABLE_ROOT / "canon_senses.json"
DEFAULT_WORKLIST_PATH = DURABLE_ROOT / SUSPICION_WORKLIST_FILENAME
SKEPTIC_RUNS_DIR = DURABLE_ROOT / SKEPTIC_RUNS_SUBDIR

# The full skeptic code closure = every producer-closure member (imported
# above, so it can never silently drift from what suspicion_scan.py itself
# hashes) PLUS the four skeptic-only scripts (round-3 blocker 1: the
# authoritative-defaults module -- already in PRODUCER_CODE_CLOSURE --
# and every skeptic script itself must be in the closure it governs).
# Sorted for a single, unambiguous deterministic order (mirrors
# resume_setup.py's own schemas-dir hashing: sorted-by-name, never a
# hand-maintained literal order that could quietly omit a new member).
_SKEPTIC_ONLY_SCRIPT_FILENAMES = (
    "evidence_verify.py",
    "skeptic_ready.py",
    "skeptic_report.py",
    "skeptic_setup.py",
)
SKEPTIC_CLOSURE_SCRIPT_FILENAMES = tuple(
    sorted(set(PRODUCER_CODE_CLOSURE) | set(_SKEPTIC_ONLY_SCRIPT_FILENAMES))
)
SKEPTIC_TEMPLATE_FILENAME = "skeptic-pass-wf.template.js"

# Per-batch assignment fragment filename pattern -- see this file's module
# docstring "Filename note" for the proposed skeptic_constants.py promotion.
ASSIGNMENT_BATCH_PREFIX = "assignments_"

# Same RUN_ID allowlist resume_setup.py enforces (references/
# ledger-and-resumability.md's "{{RUN_ID}} derivation" contract) --
# duplicated rather than imported from resume_setup.py, which this script
# must never touch or depend on (a separate, standalone resume domain).
RUN_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
RUN_ID_RETRY_LIMIT = 5


class SkepticSetupError(Exception):
    """Raised for any failure that should surface as a FAILURE result."""


def validate_run_id(run_id):
    """Return an error string if `run_id` is not a safe RUN_ID, else None."""
    if not isinstance(run_id, str) or not run_id:
        return "run id must be a non-empty string."
    if not RUN_ID_RE.fullmatch(run_id):
        return (
            "run id must match [A-Za-z0-9][A-Za-z0-9._-]* (letters/digits/"
            f"dot/underscore/hyphen only, no ':'); got {run_id!r}."
        )
    if run_id in (".", ".."):
        return f"run id must not be '.' or '..'; got {run_id!r}."
    if ".." in run_id:
        return f"run id must not contain '..'; got {run_id!r}."
    return None


def fresh_run_id():
    """Colon-free, sortable, COLLISION-FREE run id: a microsecond-resolution
    UTC timestamp prefix plus a short random hex suffix, e.g.
    '20260710T143022123456-a1b2c3'. This is a wholly separate RUN_ID
    namespace from resume_setup.py's own (${durable_root}/skeptic/runs/,
    never ${durable_root}/runs/), so it does not need to match that
    script's 1-second-resolution shape -- and deliberately does NOT: two
    fresh runs launched back-to-back against the SAME durable root (e.g.
    two of this test suite's own digest-change regression tests) can easily
    land in the same wall-clock SECOND, which the prior bare
    '%Y%m%dT%H%M%SZ' format would collide on, relying on a `time.sleep(1)`
    retry in `resolve_skeptic_run()` to escape -- nondeterministic timing a
    reviewer would rightly flag. Microseconds alone narrow the window but
    don't close it (two calls in the same process can still share a
    microsecond tick on a coarse clock), so the random suffix is what
    actually makes same-tick collision astronomically unlikely rather than
    just less likely."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    return f"{ts}-{secrets.token_hex(3)}"


# ---------------------------------------------------------------------------
# Small I/O helpers (mirrors resume_setup.py's own)
# ---------------------------------------------------------------------------


def _canonical_json_bytes(obj) -> bytes:
    return json.dumps(
        obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.parent / f".{path.name}.tmp.{os.getpid()}"
    tmp_path.write_text(text, encoding="utf-8")
    os.replace(tmp_path, path)


def _atomic_write_json(path: Path, doc) -> None:
    _atomic_write_text(path, json.dumps(doc, indent=2, ensure_ascii=False, sort_keys=True) + "\n")


def _schemas_dir_hash() -> str:
    """sha256 over every *.schema.json file's raw bytes, sorted by filename
    -- mirrors resume_setup.py::_schemas_dir_hash() exactly (duplicated, not
    imported -- same "never depend on resume_setup.py" rule as RUN_ID
    above)."""
    if not SCHEMAS_DIR.is_dir():
        raise SkepticSetupError(f"schemas directory not found: {SCHEMAS_DIR}")
    files = sorted(SCHEMAS_DIR.glob("*.schema.json"), key=lambda p: p.name)
    if not files:
        raise SkepticSetupError(f"no *.schema.json files found under {SCHEMAS_DIR}")
    h = hashlib.sha256()
    for f in files:
        h.update(f.read_bytes())
    return h.hexdigest()


def _load_schema(schema_filename: str) -> dict:
    path = SCHEMAS_DIR / schema_filename
    if not path.is_file():
        raise SkepticSetupError(f"schema file not found: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SkepticSetupError(f"invalid JSON in schema {schema_filename}: {exc}")


def _validate_against_schema(instance, schema_filename: str, what: str) -> None:
    schema = _load_schema(schema_filename)
    validator = jsonschema.Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(instance), key=lambda e: [str(p) for p in e.path])
    if errors:
        first = errors[0]
        loc = "/".join(str(p) for p in first.path) or "<root>"
        raise SkepticSetupError(
            f"{what} failed schema validation against {schema_filename} at '{loc}': {first.message}"
        )


def _build_entity_windows(entry: dict, windows_per_entity: int):
    """Per-entity CITABLE windows: ONLY origin='block' occurrence_refs
    (origin='verse_embedded' refs are label-only -- evidence_verify can
    never authenticate against verse.store, so they are not citable windows
    at all), capped at `windows_per_entity`, taken in the worklist's own
    occurrence_refs order (already deterministic -- suspicion_scan.py's own
    concern, never re-sorted here). Returns (windows, windows_truncated).

    windows_truncated is True when EITHER there are more origin='block'
    refs than `windows_per_entity` (the ordinary cap) OR the entity has ANY
    origin='verse_embedded' ref at all (Fix M5) -- those are unconditionally
    dropped from `windows` above, so their mere presence already means the
    skeptic's view is incomplete, even when every block ref fit uncapped.
    Without this, an entity backed by e.g. one block occurrence plus one
    embedded-narrative-verse occurrence would read windows_truncated=False,
    letting the skeptic conclude "full coverage" on the false premise that
    every occurrence is a citation/allusion it was shown."""
    block_refs = [r for r in entry["occurrence_refs"] if r.get("origin") == OCC_ORIGIN_BLOCK]
    capped = block_refs[:windows_per_entity]
    windows = [
        {"block": r["block"], "seg": r["seg"], "char_start": r["char_start"], "char_end": r["char_end"]}
        for r in capped
    ]
    has_omitted_verse_refs = any(
        r.get("origin") == OCC_ORIGIN_VERSE_EMBEDDED for r in entry["occurrence_refs"]
    )
    windows_truncated = len(block_refs) > windows_per_entity or has_omitted_verse_refs
    return windows, windows_truncated


# ---------------------------------------------------------------------------
# skeptic input_digest computation
# ---------------------------------------------------------------------------


def compute_skeptic_input_digest(
    *, canon_bytes: bytes, manifest_bytes: bytes, worklist_bytes: bytes,
    assignments: list, config_values: dict, language_config_raw_bytes: bytes,
    schemas_dir_hash_hex: str, script_dir: Path, template_bytes: bytes,
) -> str:
    """sha256 hex over, concatenated in this FIXED order: canon.json bytes +
    manifest.json bytes + the worklist's own raw bytes + canonical-JSON of
    the fully-resolved per-entity windows/batch assignment + canonical-JSON
    of every CLI/config value (the producer's own scan parameters plus the
    two skeptic-only ones) + the resolved LanguageConfig.raw_bytes + the
    schemas-dir hash + the full skeptic code closure's bytes (sorted) + the
    skeptic template's own bytes. Any single-byte change to any one of these
    inputs changes this digest -- see this module's docstring for the full
    member list and rationale. Every member is separated by a single NUL
    byte (mirrors suspicion_scan.compute_producer_input_digest()'s own
    framing exactly) so two adjacent variable-length members -- most
    notably two adjacent closure script files -- can never collide by
    boundary concatenation (e.g. "AB"+"C" vs "A"+"BC" hashing identically
    with no separator)."""
    h = hashlib.sha256()
    parts = [
        canon_bytes,
        manifest_bytes,
        worklist_bytes,
        _canonical_json_bytes(assignments),
        _canonical_json_bytes(config_values),
        language_config_raw_bytes,
        schemas_dir_hash_hex.encode("utf-8"),
    ]
    for name in SKEPTIC_CLOSURE_SCRIPT_FILENAMES:
        parts.append((script_dir / name).read_bytes())
    parts.append(template_bytes)
    for part in parts:
        h.update(part)
        h.update(b"\x00")
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Resume decision (mirrors resume_setup.py::resolve_run, single "skeptic"
# domain -- no kind branching)
# ---------------------------------------------------------------------------


def _assert_run_dir_contained(run_dir: Path) -> None:
    """Fix M7: `validate_run_id()` only does LEXICAL validation (regex, no
    '..') -- it can never catch a pre-planted SYMLINK sitting at a
    lexically-valid run id. On a `--resume-from-run-id` whose recorded
    digest happens to match (an attacker who knows/guesses the id can plant
    both), `run_dir = SKEPTIC_RUNS_DIR / run_id` would silently resolve
    THROUGH that symlink, and every subsequent write (per-batch fragments,
    the aggregate manifest) would land outside the durable root. Called on
    BOTH the fresh and resume paths, before `run_dir.mkdir()`/any write into
    it -- a fresh run's randomly-drawn id is not attacker-predictable, but
    the check costs nothing to apply unconditionally rather than trusting
    that asymmetry."""
    resolved_run_dir = run_dir.resolve()
    resolved_runs_dir = SKEPTIC_RUNS_DIR.resolve()
    if resolved_run_dir != resolved_runs_dir and resolved_runs_dir not in resolved_run_dir.parents:
        raise SkepticSetupError(
            f"run directory {run_dir} resolves to {resolved_run_dir}, OUTSIDE "
            f"the durable skeptic runs directory {resolved_runs_dir} -- refusing "
            "(a symlink planted at this run id would let every write below "
            "escape the durable root)"
        )


def resolve_skeptic_run(input_digest: str, resume_from_run_id):
    """Returns (run_id, resume). MATCH against a caller-supplied
    resume_from_run_id's own recorded digest -> resume with that same id.
    MISMATCH, absent candidate digest, or no candidate at all -> a fresh
    RUN_ID, never resumed -- and the candidate's own input.digest (if any)
    is NEVER overwritten."""
    if resume_from_run_id is not None:
        err = validate_run_id(resume_from_run_id)
        if err:
            raise SkepticSetupError(f"--resume-from-run-id is invalid: {err}")
        candidate_digest_path = SKEPTIC_RUNS_DIR / resume_from_run_id / SKEPTIC_INPUT_DIGEST_FILENAME
        if candidate_digest_path.is_file():
            prior_digest = candidate_digest_path.read_text(encoding="utf-8").strip()
            if prior_digest == input_digest:
                return resume_from_run_id, True
            # MISMATCH -- never overwrite the old run's digest file; fall
            # through to a fresh run below.

    for _ in range(RUN_ID_RETRY_LIMIT):
        candidate = fresh_run_id()
        if not (SKEPTIC_RUNS_DIR / candidate).exists():
            return candidate, False
        # fresh_run_id() is already microsecond+random-suffix collision-free
        # (see its own docstring) -- a repeat collision here would mean the
        # random suffix itself collided, astronomically unlikely, so the
        # retry is sleepless: just draw another fresh id immediately, no
        # nondeterministic-timing wait needed.
    raise SkepticSetupError(
        "could not generate a unique fresh skeptic RUN_ID after repeated attempts "
        "(random-suffix collision)"
    )


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def run(args) -> dict:
    canon_path = Path(args.canon) if args.canon else DEFAULT_CANON_PATH
    manifest_path = Path(args.manifest) if args.manifest else DEFAULT_MANIFEST_PATH
    senses_path = Path(args.senses_path) if args.senses_path else DEFAULT_SENSES_PATH
    worklist_path = Path(args.worklist) if args.worklist else DEFAULT_WORKLIST_PATH

    # canon.json absence is TOLERATED -- mirrors suspicion_scan.py's own
    # main() exactly (an empty/absent canon is "nothing to scan", not an
    # error), so the two scripts' canon_bytes agree byte-for-byte
    # (b"" both times) and the recomputed producer_input_digest can match.
    canon_bytes = canon_path.read_bytes() if canon_path.is_file() else b""
    # #243: canon_senses.json's own raw bytes, re-read HERE off disk (never
    # trusted from a caller, same discipline canon_bytes/manifest_bytes
    # already follow) -- mirrors suspicion_scan.py's own tolerant read
    # exactly, so the two scripts' senses_bytes agree byte-for-byte and the
    # recomputed producer_input_digest below can match.
    senses_bytes = senses_path.read_bytes() if senses_path.is_file() else b""
    if not manifest_path.is_file():
        raise SkepticSetupError(f"manifest.json not found: {manifest_path}")
    if not worklist_path.is_file():
        raise SkepticSetupError(
            f"suspicion_worklist.json not found: {worklist_path} -- run suspicion_scan.py first"
        )

    manifest_bytes = manifest_path.read_bytes()
    worklist_bytes = worklist_path.read_bytes()

    try:
        worklist = json.loads(worklist_bytes)
    except json.JSONDecodeError as exc:
        raise SkepticSetupError(f"{worklist_path} is not valid JSON: {exc}")
    if not isinstance(worklist, dict):
        raise SkepticSetupError(f"{worklist_path} must contain a JSON object")

    # 1) SCHEMA validation FIRST -- before any RUN_ID is resolved, before any
    #    directory under skeptic/runs/ is even created.
    _validate_against_schema(worklist, SUSPICION_WORKLIST_SCHEMA, "suspicion_worklist.json")

    # 2) Resolve the language config (needed to recompute producer_input_digest).
    languages_dir = Path(args.languages_dir) if args.languages_dir else LANGUAGES_DIR
    try:
        lang = load_language_config(args.particle_config, languages_dir)
    except BootstrapNamesError as exc:
        raise SkepticSetupError(f"could not load particle config: {exc}")

    # citation_override/resolved_citation_types mirrors suspicion_scan.py's
    # own main() exactly: an explicit --citation-block-types always wins --
    # including a zero-arg invocation, an EXPLICIT EMPTY override (checked
    # via `is not None`, never bare truthiness: `[]` is falsy but still a
    # real, explicit override) -- otherwise resolve_citation_block_types()
    # falls back to the source-format default, or None (class-5 fail-safe
    # disabled) for a custom/unknown format. Reusing the SAME imported
    # functions (never a second, independently-drifting reimplementation)
    # is what guarantees producer and verifier compute byte-identical
    # digests.
    citation_override = (
        tuple(args.citation_block_types) if args.citation_block_types is not None else None
    )
    resolved_citation_types = resolve_citation_block_types(args.source_format, citation_override)
    resolved_params = resolved_scan_params(
        dispersion_threshold=args.dispersion_threshold,
        sample_cap=args.sample_cap,
        windows_per_entity=args.windows_per_entity,
        near_threshold=args.near_threshold,
        near_cap=args.near_cap,
        near_pair_budget=args.near_pair_budget,
        research_mode=args.research_mode,
        source_format=args.source_format,
        resolved_citation_types=resolved_citation_types,
    )

    # 3) FRESHNESS -- recompute producer_input_digest via the SAME shared
    #    helper suspicion_scan.py itself stamped the worklist with, and
    #    reject fail-closed on any mismatch (stale canon/manifest/params/
    #    language-config/producer-code).
    recomputed_producer_digest = compute_producer_input_digest(
        canon_bytes, manifest_bytes, senses_bytes, resolved_params, lang.raw_bytes, SCRIPT_DIR,
    )
    stamped_producer_digest = worklist.get("producer_input_digest")
    if recomputed_producer_digest != stamped_producer_digest:
        raise SkepticSetupError(
            "suspicion_worklist.json is STALE: its stamped producer_input_digest "
            f"({stamped_producer_digest!r}) does not match the freshly recomputed "
            f"digest ({recomputed_producer_digest!r}) over the current canon.json / "
            "manifest.json / scan-parameters / language-config / producer-code-closure "
            "-- re-run suspicion_scan.py before this step (fail-closed: a stale "
            "worklist is never reprocessed)."
        )

    # 4) Build per-entity CITABLE windows (deterministic, worklist order).
    assignments = []
    for entry in worklist.get("entries", []):
        source_form = entry["source_form"]
        windows, windows_truncated = _build_entity_windows(entry, args.windows_per_entity)
        assignment_id = hashlib.sha256(
            unicodedata.normalize("NFC", source_form).encode("utf-8")
        ).hexdigest()
        assignments.append({
            "assignment_id": assignment_id,
            "source_form": source_form,
            "canonical_target_form": entry["canonical_target_form"],
            "risk_classes": list(entry["risk_classes"]),
            "windows": windows,
            "windows_truncated": windows_truncated,
        })

    entities_per_batch = args.entities_per_batch
    if entities_per_batch < 1:
        raise SkepticSetupError(f"--entities-per-batch must be >= 1, got {entities_per_batch}")

    for i, assignment in enumerate(assignments):
        assignment["batch_index"] = i // entities_per_batch
    batch_count = (len(assignments) + entities_per_batch - 1) // entities_per_batch

    # 5) batch_agent_cap preflight (mirrors glossary-pass-wf.template.js's own
    #    formula, glossary-pass-wf.template.js:144-166: precheck+dispatch+wait
    #    == 3 per batch, plus the fixed merge+verify pair == 2). Refuse the
    #    WHOLE run -- nothing written under skeptic/runs/ at all -- if the
    #    worst-case agent-call estimate would exceed the cap.
    estimated_calls = 3 * batch_count + 2
    if estimated_calls > args.batch_agent_cap:
        entity_word = "entity" if len(assignments) == 1 else "entities"
        raise SkepticSetupError(
            f"batch too large: estimatedCalls={estimated_calls} exceeds "
            f"--batch-agent-cap={args.batch_agent_cap} for {batch_count} skeptic "
            f"batch(es) ({len(assignments)} assigned {entity_word}) -- re-run with "
            "a larger --entities-per-batch, a smaller/narrower worklist, or a "
            "higher engine.batch_agent_cap."
        )

    # 6) Compute the skeptic input_digest.
    config_values = dict(resolved_params)
    config_values["entities_per_batch"] = entities_per_batch
    config_values["batch_agent_cap"] = args.batch_agent_cap
    config_values["particle_config_filename"] = args.particle_config
    # Fix M8: the skeptic prompt's own interpolated {{SOURCE_LANG}} token --
    # NOT otherwise covered by resolved_params (the producer's own scan
    # parameters know nothing of it) -- so a project that changes source
    # language while canon/manifest/particle-config-bytes stay constant
    # forces a fresh RUN_ID rather than resuming fragments generated under
    # the old language context. No target_lang: confirmed with A3 (the
    # template's own owner) that skeptic-pass-wf.template.js has no
    # {{TARGET_LANG}} token at all -- the skeptic pass never translates or
    # canonicalizes anything, so folding target language in here would
    # force a spurious fresh RUN_ID on a change this pass never reads.
    config_values["source_lang"] = args.source_lang

    schemas_dir_hash_hex = _schemas_dir_hash()
    template_path = TEMPLATES_DIR / SKEPTIC_TEMPLATE_FILENAME
    if not template_path.is_file():
        raise SkepticSetupError(f"skeptic template not found: {template_path}")
    template_bytes = template_path.read_bytes()

    skeptic_input_digest = compute_skeptic_input_digest(
        canon_bytes=canon_bytes, manifest_bytes=manifest_bytes, worklist_bytes=worklist_bytes,
        assignments=assignments, config_values=config_values,
        language_config_raw_bytes=lang.raw_bytes, schemas_dir_hash_hex=schemas_dir_hash_hex,
        script_dir=SCRIPT_DIR, template_bytes=template_bytes,
    )

    # 7) Resolve RUN_ID (fresh vs resume) using the skeptic input_digest.
    SKEPTIC_RUNS_DIR.mkdir(parents=True, exist_ok=True)
    run_id, resume = resolve_skeptic_run(skeptic_input_digest, args.resume_from_run_id)
    run_dir = SKEPTIC_RUNS_DIR / run_id
    _assert_run_dir_contained(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)

    digest_path = run_dir / SKEPTIC_INPUT_DIGEST_FILENAME
    if not resume:
        if digest_path.exists():
            # Unreachable in practice (resolve_skeptic_run() only returns a
            # fresh id for a run_dir whose input.digest didn't already
            # exist) -- refuse to clobber a foreign run's digest.
            raise SkepticSetupError(f"refusing to overwrite existing {digest_path}")
        _atomic_write_text(digest_path, skeptic_input_digest + "\n")

    # 8) Write per-batch + aggregate assignment manifests -- BEFORE any
    #    dispatch (this script performs no dispatch at all; these files are
    #    the ONLY output any downstream dispatch step may trust for
    #    coverage). Rewritten on BOTH fresh and resumed runs -- safe because
    #    `assignments` is a pure deterministic derivation of the
    #    digest-hashed worklist+config (a MATCH-resume rebuild is always
    #    byte-identical to what's already on disk), mirroring
    #    resume_setup.py's own glossary-manifest-rewrite-on-resume rationale.
    aggregate = {
        "schema_version": 1,
        "run_id": run_id,
        "input_digest": skeptic_input_digest,
        "producer_input_digest": recomputed_producer_digest,
        # Fix H1 (writer half): the frozen canon/manifest/senses inputs' own
        # state-tagged hash (compute_frozen_input_hash -- codex round 2:
        # hashing raw bytes alone made absent/regular-empty/irregular
        # indistinguishable; folding in the path state closes that), so
        # --verify-merged/--check-frozen-inputs (skeptic_ready.py) can
        # re-hash the on-disk files and HALT the pass if a skeptic agent
        # tampered any of them after this setup ran (source-text prompt
        # injection). #243: canon_senses.json joined canon.json/manifest.json
        # as a THIRD frozen input the moment the verifier started parsing it
        # to project the ambiguity-competitors universe. Re-hashed from
        # `canon_path`/`manifest_path`/`senses_path` directly (never from the
        # `*_bytes` already read above) so the state tag is computed fresh,
        # consistent with compute_frozen_input_hash's own self-contained
        # path-state read.
        "canon_sha256": compute_frozen_input_hash(canon_path),
        "manifest_sha256": compute_frozen_input_hash(manifest_path),
        "senses_sha256": compute_frozen_input_hash(senses_path),
        "batch_count": batch_count,
        "assignments": assignments,
    }
    _validate_against_schema(aggregate, SKEPTIC_ASSIGNMENT_SCHEMA, "generated assignments.json")

    # Per-batch fragment: a BARE JSON array of that batch's own
    # assignment_id strings -- mirrors resume_setup.py's manifest_{index}
    # .json convention exactly (never the full schema-envelope shape;
    # skeptic-pass-wf.template.js's assignmentsBatchPath()/skeptic_ready.py
    # --expect-assignments-file both expect this bare-array shape).
    for idx in range(batch_count):
        batch_ids = sorted(a["assignment_id"] for a in assignments if a["batch_index"] == idx)
        _atomic_write_json(run_dir / f"{ASSIGNMENT_BATCH_PREFIX}{idx}.json", batch_ids)

    _atomic_write_json(run_dir / SKEPTIC_AGGREGATE_MANIFEST_FILENAME, aggregate)

    return {
        "success": True,
        "effectiveRunId": run_id,
        "resume": resume,
        "run_dir": str(run_dir),
        "input_digest": skeptic_input_digest,
        "producer_input_digest": recomputed_producer_digest,
        "batch_count": batch_count,
        "assignment_count": len(assignments),
    }


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Skeptic-pass resume-domain gate + assignment-manifest writer -- "
            "see this file's own module docstring for the full contract."
        ),
    )
    parser.add_argument("--canon", metavar="PATH", default=None,
                         help=f"Path to canon.json (default: {DEFAULT_CANON_PATH}).")
    parser.add_argument("--manifest", metavar="PATH", default=None,
                         help=f"Path to manifest.json (default: {DEFAULT_MANIFEST_PATH}).")
    parser.add_argument("--senses-path", metavar="PATH", default=None,
                         help=f"Path to canon_senses.json (default: {DEFAULT_SENSES_PATH}). "
                              "MUST be the same sidecar suspicion_scan.py resolved this "
                              "worklist against -- re-read fresh off disk here (never trusted "
                              "from a caller) to recompute producer_input_digest (#243) and "
                              "to stamp senses_sha256 into the aggregate manifest (H1).")
    parser.add_argument("--worklist", metavar="PATH", default=None,
                         help=f"Path to suspicion_worklist.json (default: {DEFAULT_WORKLIST_PATH}).")
    parser.add_argument("--particle-config", required=True, metavar="FILENAME",
                         help="Bare filename under ${durable_root}/languages/ -- MUST be the "
                              "same value suspicion_scan.py resolved this worklist with.")
    parser.add_argument("--languages-dir", metavar="PATH", default=None,
                         help=f"Override for ${{durable_root}}/languages/ (default: {LANGUAGES_DIR}).")
    parser.add_argument("--research-mode", required=True, choices=("live", "offline"))
    parser.add_argument("--source-format", required=True, metavar="FORMAT",
                         help="The project's profile.yml source.format value (e.g. "
                              "gutenberg_epub, plain_text, custom) -- MUST be the same value "
                              "suspicion_scan.py resolved this worklist with. No choices= "
                              "restriction here, matching suspicion_scan.py's own CLI exactly.")
    parser.add_argument("--dispersion-threshold", type=int, default=DISPERSION_THRESHOLD_DEFAULT)
    parser.add_argument("--sample-cap", type=int, default=SAMPLE_CAP_DEFAULT)
    parser.add_argument("--windows-per-entity", type=int, default=WINDOWS_PER_ENTITY_DEFAULT)
    parser.add_argument("--near-threshold", type=float, default=NEAR_THRESHOLD_DEFAULT)
    parser.add_argument("--near-cap", type=int, default=NEAR_CAP_DEFAULT)
    parser.add_argument("--near-pair-budget", type=int, default=NEAR_PAIR_BUDGET_DEFAULT)
    parser.add_argument("--citation-block-types", nargs="*", default=None, metavar="TYPE",
                         help="Override the adapter-default citation-block type set (same "
                              "shape as suspicion_scan.py's own flag -- space-separated, not "
                              "comma-separated). Must match what suspicion_scan.py resolved "
                              "this worklist with (explicit override -- a zero-arg invocation "
                              "is an explicit EMPTY override, disabling class 5 entirely, and "
                              "is distinct from omitting the flag, which uses the same "
                              "source-format default suspicion_scan.py used).")
    parser.add_argument("--entities-per-batch", type=int, default=5,
                         help="How many assigned entities share one codex dispatch batch "
                              "(default: 5).")
    parser.add_argument("--source-lang", required=True, metavar="LANG",
                         help="The human-readable source-language label interpolated into "
                              "skeptic-pass-wf.template.js's {{SOURCE_LANG}} placeholder (e.g. "
                              "'French') -- NOT source.language.code (the locale code). Folded "
                              "into config_values/the skeptic input_digest (Fix M8) so a "
                              "source-language change alone forces a fresh RUN_ID rather than "
                              "resuming stale fragments generated under a different language "
                              "context. There is deliberately no --target-lang: the skeptic "
                              "pass never translates or canonicalizes anything, so target "
                              "language is irrelevant to it.")
    parser.add_argument("--batch-agent-cap", type=int, required=True,
                         help="engine.batch_agent_cap -- the same profile field mass/glossary "
                              "read. Refuses the whole run (nothing written) if the worst-case "
                              "agent-call estimate for the resulting batch count would exceed it.")
    parser.add_argument("--resume-from-run-id", metavar="RUN_ID", default=None,
                         help="A prior skeptic RUN_ID to attempt resuming -- honored only on an "
                              "exact input_digest match; otherwise a fresh RUN_ID is used.")
    return parser


def main(argv=None) -> int:
    args = build_arg_parser().parse_args(argv)
    try:
        result = run(args)
    except SkepticSetupError as e:
        print(json.dumps({"success": False, "error": str(e)}, ensure_ascii=False))
        return 1
    except Exception as e:  # pragma: no cover -- defensive catch-all
        print(json.dumps({"success": False, "error": f"unexpected error: {e}"}, ensure_ascii=False))
        return 1

    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
