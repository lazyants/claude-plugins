#!/usr/bin/env python3
"""skeptic_ready.py -- RFC #215 Phase 2 skeptic pass: validate, deterministic
merge, and fresh-read verify of ``skeptic_triage.json`` -- the analogue of
``canon_validate.py``'s ``--check-batch``/``--merge-batches``/``--verify-
merged`` split, cloned for the skeptic pass's own adverse-only artifact
(``skeptic-triage.schema.json``, RFC #215 Phase 2).

SAFETY INVARIANT this script enforces (round-4 blocker 2, see
``skeptic-triage.schema.json``'s own description): the triage schema's
``verdict`` enum has no value able to express a confirmation, so a
compliant-but-WRONG skeptic (an agent that sincerely but mistakenly claims
``adverse``/``propose_split`` with evidence that does not actually
byte-verify) can at worst be silently DOWNGRADED to ``insufficient_window``
-- never silently trusted. That downgrade -- "coercion" throughout this
file -- is this script's own job, not codex's: unlike the glossary pass's
``--check-batch`` (which REJECTS a bad fragment and makes codex fix and
resubmit it), a citation that fails to byte-verify is not a shape bug codex
can "fix" by retrying -- it is coerced away instead. A genuine SHAPE
violation (malformed JSON, a smuggled ``confirmed_ok``-style field, a
``propose_split`` with fewer than 2 ``referents`` items when the property IS
given) is a hard schema-validation failure and IS rejected outright (codex
must actually fix its own file) -- see the module-level distinction between
"reject" (schema/token/coverage failures, raised) and "coerce" (evidence/
procedural gaps, silently downgraded and written back).

Four deterministic CLI modes, mutually exclusive:

  --validate-fragment FRAGMENT --manifest-path M --particle-config NAME
      [--languages-dir DIR] [--expect-assignments-file PATH]
      [--schemas-dir DIR] [--canon PATH] [--senses-path PATH]
    One run-scoped triage fragment (``triage_{index}.json``, one per
    dispatched batch): schema-validate against ``skeptic-triage.schema.json``
    (real ``jsonschema.Draft202012Validator``); reject a fragment whose
    ``assignment_id`` does not equal ``sha256(NFC(source_form))`` for any
    record (the join-key "token" check) or whose record set does not exactly
    match ``--expect-assignments-file``'s assignment_id array (the
    "coverage" check) -- both HARD rejects, fragment unchanged on disk.
    Otherwise re-authenticates every cited evidence record via the evidence
    adapter below, coerces whatever fails down to ``insufficient_window``,
    and ATOMICALLY rewrites the (possibly-coerced) fragment back to
    FRAGMENT. ``--canon``/``--senses-path`` (#243) project the shared
    ambiguity-competitors universe (``canon_senses.fold_collision_map``,
    the SAME universe ``suspicion_scan.py``'s ``build_worklist()`` uses) --
    a source_form that fold-collides with another competitor fails
    evidence re-verification unconditionally, since a byte-verified
    citation can no longer be trusted to belong to THIS entity rather than
    a colliding sibling (site 1's own "side door" fix, see
    ``_evidence_failure_reason``'s own docstring).

  --merge-fragments RUN_DIR [--out PATH] [--schemas-dir DIR]
    The SINGLE serialized, disk-independent merge: every
    ``{SKEPTIC_FRAGMENT_PREFIX}*.json`` fragment already sitting in RUN_DIR
    (already validated by --validate-fragment above) is fresh-read,
    re-validated against the schema (defense-in-depth -- never trust a prior
    step blindly), and its records concatenated, then sorted into a fully
    DETERMINISTIC order (by ``assignment_id`` then ``source_form``) so the
    merged output is byte-identical regardless of which physical fragment
    file held which record or the order fragments were read in. Written
    ATOMICALLY (temp file in the same directory, then ``rename``) to
    ``--out`` (default ``{durable_root}/skeptic_triage.json``) -- never a
    shared append target (the #90 fix this whole split is modeled on).

  --verify-merged TRIAGE AGGREGATE_MANIFEST --manifest-path M
      --particle-config NAME [--languages-dir DIR] [--schemas-dir DIR]
      [--canon PATH] [--senses-path PATH]
    Fresh-read, disk-INDEPENDENT verification, at the SAME rigor
    ``--validate-fragment`` applies (never weaker -- every one of that
    mode's checks is redone here too, plus merge-specific ones only
    meaningful post-merge): re-validates TRIAGE against the schema,
    re-validates AGGREGATE_MANIFEST (the assignments.json
    ``skeptic_setup.py`` wrote BEFORE dispatch) against
    ``skeptic-assignment.schema.json``, then every merged-verification check
    below accumulates into ``missing[]`` rather than raising -- WITH ONE
    DELIBERATE EXCEPTION: the frozen-input integrity tripwire's own read of
    canon.json/manifest.json/canon_senses.json (``frozen_input_check()``,
    called with ``tolerant_reads=False``) still raises RAW on an ``OSError``,
    same as every other genuine precondition failure in this function --
    see that call's own comment for why degrading it to ``missing[]`` would
    be fail-OPEN on exactly the property this release makes fail-closed.
    Every check that follows that call IS fail-closed into ``missing[]``:
      - coverage: the merged triage's ``assignment_id`` set is EXACTLY the
        assignment manifest's assigned set (a gap -- an assigned entity with
        no triage record -- is a FAIL, same as an extra record referencing
        an unassigned id);
      - multiplicity: EXACTLY one triage record per assigned
        ``assignment_id`` -- a duplicate record is a FAIL, never silently
        absorbed by the set-based coverage check above;
      - run_id binding: TRIAGE's own ``run_id`` must equal
        AGGREGATE_MANIFEST's;
      - per record: the SAME ``assignment_id``/``source_form`` token check
        ``--validate-fragment`` runs; the record's ``source_form`` must
        match its aggregate assignment's own ``source_form`` (join on
        ``assignment_id``); EVERY cited evidence record -- the top-level
        ``evidence`` key AND each ``referents[]`` item's own ``evidence``,
        independently, one at a time -- must fall inside that assignment's
        own ``windows[].block`` set (a citation that byte-verifies
        SOMEWHERE in ``manifest.blocks{}`` but outside the entity's
        actually-assigned windows is a FAIL) AND must still byte-verify via
        a fresh ``evidence_verify`` re-authentication (a post-merge
        tampered offset/sha256 is a FAIL) -- per-citation, so a
        ``propose_split`` with N>=3 referents where only ONE has been
        tampered is still caught even though >=2 genuine referents survive
        and the record's own verdict never flips; additionally, the whole
        record is re-coerced via the SAME ``_coerce_record`` machinery
        ``--validate-fragment`` uses, failing whenever the stored verdict no
        longer matches what a fresh re-coercion would produce -- this
        catches verdict-LEVEL structural coercion (an evidence-free
        adverse/propose_rescope, or a ``propose_split`` with fewer than 2
        referents present/verified at all) that the per-citation checks
        above do not themselves name, since only THOSE, never the
        verdict-delta alone, guarantee every individual citation;
      - frozen-input integrity tripwire (H1 mitigation, BEST-EFFORT only):
        whenever AGGREGATE_MANIFEST stamps ``canon_sha256``/
        ``manifest_sha256``/``senses_sha256`` (see
        ``skeptic-assignment.schema.json`` -- #243 added ``senses_sha256``
        as a THIRD stamp once this mode started parsing ``canon_senses.json``
        to project the ambiguity-competitors universe below), the on-disk
        ``--canon`` file (default ``{durable_root}/canon.json``) /
        ``manifest.json`` / ``--senses-path`` file (default
        ``{durable_root}/canon_senses.json``) is re-hashed and must still
        match. This catches ACCIDENTAL/non-adversarial mutation (a crash
        mid-write, a stray process, a well-behaved but buggy agent) -- it is
        NOT sound against a prompt-injected adversarial agent, which has
        pipeline-wide filesystem write access and can rewrite or simply
        delete this same co-located stamp to match its own tampered
        canon/manifest/senses (the stamp lives in ``assignments.json``,
        inside the very run_dir such an agent can already write to); a sound
        version (anchoring the setup-time hash in a channel the agent cannot
        reach) is deferred to Phase 3. Absent stamp -> the corresponding
        check is skipped (an older/hand-built aggregate manifest,
        backward-compatible; also the only way a determined adversary
        defeats this cheaply). A mismatch here is surfaced DISTINCTLY from
        every other failure above via the ``frozen_input_mismatch`` output
        field (see below) -- this is what lets a caller HALT the pipeline
        outright on "canon/manifest/senses changed since setup" instead of
        treating it as just another advisory skeptic-pass failure like a
        coverage gap or an unverified citation;
      - ambiguity-competitors projection (#243): ``--canon``/``--senses-path``
        are ALSO parsed (independently of the H1 stamps above, which only
        gate tamper-detection) to build the SAME ``fold_collision_map``
        universe ``--validate-fragment`` uses, so a source_form re-verified
        here that fold-collides with another competitor fails
        unconditionally -- see ``_evidence_failure_reason``'s own docstring.
    Never trusts anything this run itself, or --validate-fragment, already
    claimed -- every check here is redone from the files on disk plus a
    fresh read of ``manifest.json``/``canon.json``/``canon_senses.json``. Prints
    ``{"verified": bool, "missing": [...], "frozen_input_mismatch": bool}``
    -- the base two fields are the same relay shape ``canon_validate.py
    --verify-merged``/``CANON_VERIFY_SCHEMA`` use; ``frozen_input_mismatch``
    is this mode's own addition, true iff at least one canon/manifest/senses
    hash check above actually fired (its own reason is ALSO still folded into
    ``missing[]``, so ``verified`` stays False either way -- this field only
    adds the ability to distinguish WHICH kind of failure occurred).

  --check-frozen-inputs AGGREGATE_MANIFEST [--canon PATH] [--senses-path PATH]
      [--manifest-path PATH]
    (codex round 2) Standalone H1 tripwire ONLY -- the SAME
    canon.json/manifest.json/canon_senses.json re-hash-and-compare
    --verify-merged applies internally (``frozen_input_check()``, shared by
    both), exposed as its own mode so the calling Workflow can run it at a
    SECOND decision point --verify-merged never reaches: when every batch's
    own fragment fails to become ready, the pipeline gives up with an
    ordinary advisory outcome and never calls --verify-merged at all, so a
    sidecar tampered sometime after ``skeptic_setup.py`` stamped this run
    but before any batch's fragment ever validated would otherwise go
    completely unreported as the FATAL tamper it is. AGGREGATE_MANIFEST is
    read with a MINIMAL, tolerant raw JSON parse (never full schema
    validation, never crashes on a missing/malformed file -- degrades to
    ``frozen_input_mismatch: false``, nothing to compare against). Prints
    ``{"frozen_input_mismatch": bool, "missing": [...]}``. Exit 1 iff
    ``frozen_input_mismatch`` is true, 0 otherwise.

Evidence adapter (RFC #215 Phase 2 contract): for each cited evidence record
``{block, seg, char_start, char_end, context_start, context_end, sha256}``,
this script calls ``evidence_verify.verify_evidence(source_form,
{"sense_id": None, "evidence": mapped}, manifest, language_config)`` --
``mapped`` is the record with occ_index's own ``context_sha256`` key
(present on evidence built directly from ``occ_index.build_occurrence_
records()``/``index_manifest()`` output, e.g. by this script's own test
fixtures or ``skeptic_setup.py``'s window derivation) renamed to the
``sha256`` field the schema and ``verify_evidence()`` both expect. Any
failure -- including any citation whose ``block`` is actually an
embedded-verse node (``evidence_verify`` authenticates only against
``manifest.blocks{}``, never ``verse.store[]``, so such a citation can never
byte-verify) -- is treated as "does not verify", never raised.

Exit codes: 0 on success (``--validate-fragment``/``--merge-fragments``:
``{"success": true, ...}``; ``--verify-merged``: ``{"verified": true, ...}``),
1 on any failure. Exactly one JSON line is printed to stdout either way.
"""
import argparse
import hashlib
import json
import os
import sys
import unicodedata
from collections import Counter
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
DURABLE_ROOT = SCRIPT_DIR.parent
SCHEMAS_DIR_DEFAULT = DURABLE_ROOT / "schemas"
LANGUAGES_DIR_DEFAULT = DURABLE_ROOT / "languages"
DEFAULT_MANIFEST_PATH = DURABLE_ROOT / "manifest.json"
DEFAULT_CANON_PATH = DURABLE_ROOT / "canon.json"
# Sibling of DEFAULT_CANON_PATH, self-anchored the same way -- each consumer
# computes its own copy (see canon_senses.py's own module docstring on why
# DEFAULT_SENSES_PATH is deliberately not defined there). #243.
DEFAULT_SENSES_PATH = DURABLE_ROOT / "canon_senses.json"

try:
    import jsonschema
except ImportError as exc:
    sys.stderr.write(
        "skeptic_ready.py requires the 'jsonschema' package (>=4.26.0) to validate "
        "skeptic-triage.json/skeptic-assignment.json fragments. Install with:\n\n"
        "    pip install -r requirements.txt\n\n"
        f"(import error: {exc})\n"
    )
    sys.exit(1)

try:
    from skeptic_constants import (
        SKEPTIC_TRIAGE_FILENAME,
        SKEPTIC_TRIAGE_SCHEMA,
        SKEPTIC_ASSIGNMENT_SCHEMA,
        SKEPTIC_FRAGMENT_PREFIX,
        TRIAGE_ADVERSE,
        TRIAGE_PROPOSE_SPLIT,
        TRIAGE_PROPOSE_RESCOPE,
        TRIAGE_INSUFFICIENT_WINDOW,
        FROZEN_INPUT_SPECS,
    )
except ImportError as exc:
    sys.exit(
        f"skeptic_ready.py: cannot import skeptic_constants.py from {SCRIPT_DIR} ({exc}).\n"
        "skeptic_constants.py must be installed alongside skeptic_ready.py under "
        "${durable_root}/scripts/ -- it supplies every filename/default this script uses. "
        "Re-run Step 0a, or verify the plugin install is not corrupted."
    )

try:
    from bootstrap_names import load_language_config, BootstrapNamesError
except ImportError as exc:
    sys.exit(
        f"skeptic_ready.py: cannot import bootstrap_names.py from {SCRIPT_DIR} ({exc}).\n"
        "bootstrap_names.py must be installed alongside skeptic_ready.py under "
        "${durable_root}/scripts/ -- it supplies load_language_config(), the resolved "
        "LanguageConfig every evidence re-verification below needs. Re-run Step 0a, "
        "or verify the plugin install is not corrupted."
    )

try:
    from evidence_verify import verify_evidence
except ImportError as exc:
    sys.exit(
        f"skeptic_ready.py: cannot import evidence_verify.py from {SCRIPT_DIR} ({exc}).\n"
        "evidence_verify.py must be installed alongside skeptic_ready.py under "
        "${durable_root}/scripts/ -- it supplies verify_evidence(), the shared "
        "matcher-authentication authority every cited evidence record is bound "
        "against. Re-run Step 0a, or verify the plugin install is not corrupted."
    )

try:
    from canon_senses import (
        fold_collision_map,
        load_senses,
        load_senses_from_snapshot,
        CanonSensesLoadError,
    )
except ImportError as exc:
    sys.exit(
        f"skeptic_ready.py: cannot import canon_senses.py from {SCRIPT_DIR} ({exc}).\n"
        "canon_senses.py must be installed alongside skeptic_ready.py under "
        "${durable_root}/scripts/ -- it supplies load_senses()/"
        "load_senses_from_snapshot()/fold_collision_map() (#243), the shared "
        "ambiguity-competitors projection every evidence re-verification below "
        "needs. Re-run Step 0a, or verify the plugin install is not corrupted."
    )

try:
    from suspicion_scan import (
        compute_frozen_input_hash_from_state,
        read_frozen_input_snapshot,
    )
except ImportError as exc:
    sys.exit(
        f"skeptic_ready.py: cannot import suspicion_scan.py from {SCRIPT_DIR} ({exc}).\n"
        "suspicion_scan.py must be installed alongside skeptic_ready.py under "
        "${durable_root}/scripts/ -- it supplies read_frozen_input_snapshot() and "
        "compute_frozen_input_hash_from_state(), which frozen_input_check() uses "
        "for canon/manifest/senses alike (codex round 5 canon/senses, round 7 "
        "manifest) so the H1 comparison and any downstream competitor-resolution "
        "parse consume the SAME captured snapshot, never two independent re-reads "
        "that could silently disagree; skeptic_setup.py's own stamps are computed "
        "from that same captured-snapshot formula via "
        "compute_frozen_input_hash_from_state() -- see that function's own "
        "docstring for why the stamper and verifier need opposite freshness "
        "semantics from the same hash formula. compute_frozen_input_hash() (the "
        "fresh-read, path-based wrapper around the same two, also defined in "
        "suspicion_scan.py) is deliberately NOT imported here (round 8): nothing "
        "in this module's production code has called it directly since codex "
        "round 7 closed the last such call site, and importing it purely so this "
        "module's own namespace could re-export it to tests put a test-only need "
        "in the PRODUCTION import list -- the test suite now imports it straight "
        "from suspicion_scan.py, where it is actually defined. Re-run "
        "Step 0a, or verify the plugin install is not corrupted."
    )


class SkepticReadyError(Exception):
    """Raised for any failure that should surface as a FAILURE result --
    mirrors ``canon_validate.py``'s own ``CanonValidationError``. ``offending``,
    when not None, is folded into the failure payload verbatim (one string
    per offending item), so a caller never has to re-derive that from a bare
    message."""

    def __init__(self, message, offending=None):
        super().__init__(message)
        self.offending = offending


# ---------------------------------------------------------------------------
# JSON I/O helpers
# ---------------------------------------------------------------------------

def _read_json(path: Path, label: str) -> dict:
    path = Path(path)
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise SkepticReadyError(f"{label} not found: {path}")
    except OSError as exc:
        raise SkepticReadyError(f"{label} could not be read: {path} ({exc})")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SkepticReadyError(f"{label} is not valid JSON: {path} ({exc})")


def _read_json_or_none(path: Path, missing: list, label: str):
    """Like ``_read_json``, but degrades a failure to a `missing[]` entry
    and returns None instead of raising -- used only by ``--verify-merged``,
    whose whole job is to REPORT a bad artifact as a verification failure,
    never to crash on one."""
    try:
        return _read_json(path, label)
    except SkepticReadyError as exc:
        missing.append(str(exc))
        return None


def _atomic_write_json(path: Path, doc) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    tmp_path.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    tmp_path.replace(path)  # atomic on the same filesystem


def _load_schema_document(schema_path: Path) -> dict:
    if not schema_path.is_file():
        raise SkepticReadyError(f"schema file not found: {schema_path}")
    try:
        return json.loads(schema_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SkepticReadyError(f"invalid JSON in schema {schema_path.name}: {exc}")


def _format_error(error: "jsonschema.exceptions.ValidationError") -> str:
    where = "/".join(str(p) for p in error.path) or "<root>"
    return f"{where}: {error.message}"


def _schema_errors(doc, validator) -> list:
    return [_format_error(e) for e in sorted(validator.iter_errors(doc), key=lambda e: list(e.path))]


# ---------------------------------------------------------------------------
# Token (assignment_id/source_form) check -- the join key every triage
# record carries back to the assignment manifest must be self-consistent;
# a mismatch means the record cannot be safely filed under ANY assignment,
# so it is a hard reject, never a coercion target.
# ---------------------------------------------------------------------------

def compute_assignment_id(source_form: str) -> str:
    """sha256 hex of the NFC-normalized source_form -- skeptic-assignment
    .schema.json's own definition of ``assignment_id``. Deliberately pure
    NFC only (no casefold/whitespace-collapse): this is a stable JOIN key,
    not the coarser ``canon_senses.normalize_form`` comparator used for
    merge-group dedup elsewhere in this plugin."""
    nfc = unicodedata.normalize("NFC", source_form)
    return hashlib.sha256(nfc.encode("utf-8")).hexdigest()


def _token_mismatch_reason(record: dict) -> "str | None":
    source_form = record.get("source_form")
    assignment_id = record.get("assignment_id")
    if not isinstance(source_form, str) or not isinstance(assignment_id, str):
        return "missing or non-string source_form/assignment_id"
    expected = compute_assignment_id(source_form)
    if expected != assignment_id:
        return (
            f"assignment_id {assignment_id!r} != sha256(NFC(source_form))={expected!r} "
            f"for source_form {source_form!r}"
        )
    return None


def _load_expected_ids(path: Path) -> list:
    doc = _read_json(path, "expected assignments file")
    if not isinstance(doc, list) or not all(isinstance(x, str) for x in doc):
        raise SkepticReadyError(f"{path}: expected a JSON array of assignment_id strings")
    return doc


# ---------------------------------------------------------------------------
# #243 ambiguity-competitors projection -- shared by --validate-fragment and
# --verify-merged, so the two modes can never disagree about which forms
# collide.
# ---------------------------------------------------------------------------

def _load_canon_entries(canon_path: Path) -> dict:
    """Tolerant canon.json read -- mirrors suspicion_scan.py's own main()
    exactly: an absent/unparseable/shapeless canon is "nothing to compare",
    never an error (this is a projection input for evidence-integrity
    checks, not the authoritative canon-freeze gate itself)."""
    if not canon_path.is_file():
        return {}
    try:
        doc = json.loads(canon_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    if isinstance(doc, dict) and isinstance(doc.get("entries"), dict):
        return doc["entries"]
    return {}


def _parse_canon_entries_from_bytes(state: str, content: bytes) -> dict:
    """Byte-based twin of `_load_canon_entries` -- parses an ALREADY-
    CAPTURED `(state, content)` snapshot instead of reading `canon_path`
    itself a second time. Same unconditional tolerance as the path-based
    version: any non-regular state or parse failure is "nothing to
    compare", never an error. Codex round 5: used where this same snapshot
    also feeds an H1 tamper comparison (`frozen_input_check()`), so the
    two can never independently disagree about which on-disk version of
    canon.json they each describe."""
    if state != "regular":
        return {}
    try:
        doc = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}
    if isinstance(doc, dict) and isinstance(doc.get("entries"), dict):
        return doc["entries"]
    return {}


def _resolve_competitors(
    canon_path: Path, senses_path: Path,
    *, canon_snapshot=None, senses_snapshot=None,
):
    """Builds the #243 ambiguity-competitors `FoldCollisionMap` -- the union
    of every `canon.json` entry and every `canon_senses.json` form
    (split-only included), the SAME universe `suspicion_scan.py`'s
    `build_worklist()` projects -- so a source_form this script re-verifies
    is judged against the identical collision groups the worklist that
    flagged it in the first place used. Raises `SkepticReadyError` (never a
    raw `CanonSensesLoadError`) on a blocked sidecar load, matching every
    other precondition failure in this module.

    `canon_snapshot`/`senses_snapshot` (codex round 5, reshaped round 6):
    an already-captured `(state, bytes)` pair to REUSE instead of a fresh
    read of the matching path -- e.g. `frozen_input_check()`'s own H1
    reads. Each defaults to `None` (a fresh read for THAT input) and is
    considered INDEPENDENTLY of the other -- never both-or-nothing.
    `run_validate_fragment` passes neither (no H1 check, no AGGREGATE
    visibility at all -- a single ordinary read for both is genuinely
    correct there, not an instance of the round-5 race).
    `run_verify_merged` passes whatever `frozen_input_check()` actually
    captured, which (codex round 6) may be `None` for either input
    independently -- an absent `canon_sha256`/`senses_sha256` stamp means
    that ONE input was never read at all (see `frozen_input_check()`'s own
    "no stamp -> no read" fix), not that both must fall back together. A
    round-6 regression this exact shape closes: treating "canon_snapshot
    is not None and senses_snapshot is not None" as one all-or-nothing
    gate at the CALL site discarded a perfectly good, already H1-approved
    canon_snapshot the moment senses_sha256 happened to be absent (a
    realistic case -- an older aggregate manifest, or a project that never
    stamped a senses hash) -- silently re-reading canon.json fresh instead,
    which could disagree with what H1 just certified. Resolving each
    snapshot independently, right here, is what makes that impossible: a
    caller with an H1-approved canon_snapshot but no senses_snapshot still
    gets canon parsed from that EXACT snapshot, only senses falls back to
    a fresh read.

    Codex round 5 BLOCKER (original motivation): `run_verify_merged`
    previously hashed canon/senses for the H1 tamper check, then called
    this function with NO snapshot at all, which independently re-read
    both paths to PARSE them. A mutation landing between those two reads
    let H1 approve snapshot A while this projection silently consumed
    snapshot B -- `frozen_input_mismatch` could report `False` even though
    the competitors universe this run actually verified against was NOT
    the one H1 just certified.

    Both inputs are read UNCONDITIONALLY tolerant of absence
    (`_load_canon_entries`/`_parse_canon_entries_from_bytes`, and
    `load_senses`/`load_senses_from_snapshot(..., allow_absent=True)`
    here) -- mirrors `--canon`'s own pre-existing unconditional tolerance
    in this file (`frozen_input_check()`'s own per-input table, used
    regardless of whether `--canon` was explicitly passed). This is a
    deliberate DEPARTURE from
    `canon_adjudication_audit.py`'s explicit-path-must-exist convention
    (codex round: `skeptic-pass-wf.template.js` ALWAYS passes
    `--senses-path ${ROOT}/canon_senses.json` explicitly, unconditionally,
    for every project regardless of whether that project ever adopted
    homonym-split senses -- unlike `canon_adjudication_audit.py`'s
    `--senses-path`, which a HUMAN operator types by hand and where an
    explicit-but-missing path is plausibly a real typo worth catching, this
    script's `--senses-path`/`--canon` are ALWAYS machine-generated by the
    template with the canonical default value baked in; treating that as a
    hard error would turn every precheck/dispatch-self-check/wait-poll/
    verify-merged call into a fatal crash for the (extremely common) case of
    a project with no `canon_senses.json` at all, rather than the documented
    normal "nothing to project" state -- a schema-invalid or non-regular
    sidecar still raises via `load_senses`/`load_senses_from_snapshot`
    itself, only genuine absence is tolerated."""
    if canon_snapshot is not None:
        canon_entries = _parse_canon_entries_from_bytes(canon_snapshot[0], canon_snapshot[1])
    else:
        canon_entries = _load_canon_entries(canon_path)
    try:
        if senses_snapshot is not None:
            senses_result = load_senses_from_snapshot(
                senses_path, senses_snapshot[0], senses_snapshot[1], allow_absent=True
            )
        else:
            senses_result = load_senses(senses_path, allow_absent=True)
    except CanonSensesLoadError as exc:
        raise SkepticReadyError(f"canon_senses.json error: {exc}")
    competitor_forms = set(canon_entries.keys()) | set(senses_result.entries_by_source_form.keys())
    return fold_collision_map(competitor_forms)


# ---------------------------------------------------------------------------
# Evidence adapter (RFC #215 Phase 2 contract).
# ---------------------------------------------------------------------------

def _map_evidence_record(evidence: dict) -> dict:
    """Renames occ_index's own emitted ``context_sha256`` key to the
    ``sha256`` field ``verify_evidence()`` (and this schema) expect. Every
    schema-valid triage record's evidence already carries ``sha256`` (the
    schema requires it), so this is a no-op for those -- it exists for
    callers that build an evidence dict straight from
    ``occ_index.build_occurrence_records()``/``index_manifest()`` output
    (this script's own test fixtures; ``skeptic_setup.py``'s window
    derivation) and need the same rename before embedding it as ``evidence``
    or handing it to ``verify_evidence()`` directly."""
    mapped = dict(evidence)
    if "sha256" not in mapped and "context_sha256" in mapped:
        mapped["sha256"] = mapped.pop("context_sha256")
    return mapped


def _evidence_failure_reason(source_form, evidence, manifest, language_config, *,
                              competitors=None) -> "str | None":
    """Calls ``verify_evidence(source_form, {"sense_id": None, "evidence":
    mapped}, manifest, language_config)`` and returns its failure reason, or
    None when the citation byte-verifies (checks (i)-(iv) of
    ``evidence_verify.py``'s own docstring, including matcher-
    authentication). A citation whose ``block`` is an embedded-verse node
    fails here too -- ``evidence_verify`` authenticates only against
    ``manifest.blocks{}``, never ``verse.store[]`` -- which is exactly how
    an embedded-verse citation ends up coerced to ``insufficient_window``
    below, with no special-case code needed.

    ``competitors`` (#243): a ``canon_senses.FoldCollisionMap`` over the
    shared ambiguity-competitors universe (``_resolve_competitors()``). This
    is site 1's own "side door" fix -- ``production_occurrences()`` (which
    ``verify_evidence()`` is ultimately built on) has, and can have, NO
    notion of collision groups; it matches on a single ``source_form`` in
    isolation. Once its own comparison folds ``fold_match_key`` (#243, A2a),
    a byte-verified citation for a fold-colliding ``source_form`` can no
    longer be trusted to belong to THIS entity rather than a colliding
    sibling competitor -- so a colliding ``source_form`` fails here
    UNCONDITIONALLY, before ``verify_evidence()`` is even called, regardless
    of what it would have reported. ``competitors=None`` (caller resolved no
    senses/canon projection) disables this check -- pre-#243 behavior."""
    if competitors is not None and competitors.is_colliding(source_form):
        return (
            f"source_form {source_form!r} fold-collides with another #243 "
            "ambiguity competitor (canon_senses.fold_collision_map) -- a "
            "byte-verified citation cannot be trusted to belong to THIS "
            "entity rather than a colliding sibling, so it is treated as "
            "unverifiable regardless of evidence_verify's own result"
        )
    mapped = _map_evidence_record(evidence)
    failure = verify_evidence(source_form, {"sense_id": None, "evidence": mapped}, manifest, language_config)
    return None if failure is None else failure.reason


def _coerce_record(record: dict, manifest: dict, language_config, *, competitors=None) -> dict:
    """Enforces the verdict-specific PROCEDURAL requirements
    ``skeptic-triage.schema.json`` documents but leaves to code:
    ``propose_split`` needs >=2 referents each byte-verified;
    ``adverse``/``propose_rescope`` need one byte-verified citation;
    ``insufficient_window`` needs neither. Any citation that fails to
    byte-verify -- or is simply absent where required -- is coerced DOWN to
    ``insufficient_window`` (never up): the fail-closed half of the RFC #215
    safety invariant. A ``propose_split`` that started with more referents
    than survive verification is not thrown away wholesale as long as >=2
    verified referents remain -- the failed ones are dropped and
    ``evidence_coverage`` records the partial count (rendered by
    ``skeptic_report.py``). Never mutates ``record``; always returns a NEW,
    schema-conformant record.

    ``competitors`` (#243): threaded straight through to every
    ``_evidence_failure_reason()`` call below -- see that function's own
    docstring. ``competitors=None`` disables the fold-collision check
    entirely (pre-#243 behavior)."""
    verdict = record.get("verdict")
    source_form = record.get("source_form")

    def _downgrade(reason: str) -> dict:
        notes = list(record.get("notes") or [])
        notes.append(f"skeptic_ready:coerced_insufficient_window:{reason}")
        return {
            "assignment_id": record.get("assignment_id"),
            "source_form": source_form,
            "verdict": TRIAGE_INSUFFICIENT_WINDOW,
            "rationale": record.get("rationale", ""),
            "notes": notes,
        }

    if verdict == TRIAGE_PROPOSE_SPLIT:
        referents = record.get("referents")
        if not isinstance(referents, list) or len(referents) < 2:
            return _downgrade("missing_or_insufficient_referents")
        verified_referents = []
        for ref in referents:
            evidence = ref.get("evidence") if isinstance(ref, dict) else None
            if isinstance(evidence, dict) and _evidence_failure_reason(
                source_form, evidence, manifest, language_config, competitors=competitors
            ) is None:
                verified_referents.append(ref)
        if len(verified_referents) < 2:
            return _downgrade("fewer_than_2_referents_byte_verified")
        new_record = dict(record)
        new_record["referents"] = verified_referents
        new_record["evidence_coverage"] = {"cited": len(referents), "verified": len(verified_referents)}
        return new_record

    if verdict in (TRIAGE_ADVERSE, TRIAGE_PROPOSE_RESCOPE):
        evidence = record.get("evidence")
        if not isinstance(evidence, dict):
            return _downgrade("missing_evidence")
        if _evidence_failure_reason(
            source_form, evidence, manifest, language_config, competitors=competitors
        ) is not None:
            return _downgrade("evidence_unverified")
        new_record = dict(record)
        new_record["evidence_coverage"] = {"cited": 1, "verified": 1}
        return new_record

    if verdict == TRIAGE_INSUFFICIENT_WINDOW:
        return dict(record)

    # Unreachable once schema validation (enum-restricted `verdict`) has
    # already passed -- defensive only, never trust that invariant blindly.
    return _downgrade("unrecognized_verdict")


# ---------------------------------------------------------------------------
# --validate-fragment
# ---------------------------------------------------------------------------

def run_validate_fragment(
    fragment_path,
    manifest_path,
    particle_config: str,
    languages_dir=None,
    expect_assignments_file=None,
    schemas_dir=None,
    canon_path=None,
    senses_path=None,
) -> dict:
    fragment_path = Path(fragment_path)
    schemas_dir = Path(schemas_dir) if schemas_dir else SCHEMAS_DIR_DEFAULT
    # #243: same --canon/--senses-path convention as --verify-merged (see
    # build_arg_parser()'s own help and _resolve_competitors' own docstring)
    # -- absence is tolerated UNCONDITIONALLY, whether the flag was omitted
    # or explicitly passed (the template always passes both explicitly).
    resolved_canon_path = Path(canon_path) if canon_path else DEFAULT_CANON_PATH
    resolved_senses_path = Path(senses_path) if senses_path else DEFAULT_SENSES_PATH
    competitors = _resolve_competitors(resolved_canon_path, resolved_senses_path)

    doc = _read_json(fragment_path, "triage fragment")

    schema = _load_schema_document(schemas_dir / SKEPTIC_TRIAGE_SCHEMA)
    validator = jsonschema.Draft202012Validator(schema)
    errors = _schema_errors(doc, validator)
    if errors:
        raise SkepticReadyError(
            f"{fragment_path} failed schema validation against {SKEPTIC_TRIAGE_SCHEMA}",
            offending=errors,
        )

    records = doc.get("records", [])

    token_errors = []
    for i, rec in enumerate(records):
        reason = _token_mismatch_reason(rec)
        if reason:
            token_errors.append(f"records[{i}]: {reason}")
    if token_errors:
        raise SkepticReadyError(
            f"{fragment_path}: assignment_id/source_form token mismatch", offending=token_errors
        )

    if expect_assignments_file is not None:
        expected_ids = set(_load_expected_ids(Path(expect_assignments_file)))
        actual_ids = {rec.get("assignment_id") for rec in records}
        gap = sorted(expected_ids - actual_ids)
        extra = sorted(actual_ids - expected_ids)
        if gap or extra:
            offending = [f"missing: {aid}" for aid in gap] + [f"unexpected: {aid}" for aid in extra]
            raise SkepticReadyError(f"{fragment_path}: assignment_id coverage mismatch", offending=offending)

    manifest = _read_json(Path(manifest_path), "manifest.json")
    try:
        language_config = load_language_config(
            particle_config, languages_dir=Path(languages_dir) if languages_dir else LANGUAGES_DIR_DEFAULT
        )
    except BootstrapNamesError as exc:
        raise SkepticReadyError(f"particle config error: {exc}")

    coerced_records = [
        _coerce_record(rec, manifest, language_config, competitors=competitors) for rec in records
    ]
    coerced_count = sum(
        1 for orig, new in zip(records, coerced_records) if orig.get("verdict") != new.get("verdict")
    )
    new_doc = {
        "schema_version": doc.get("schema_version"),
        "run_id": doc.get("run_id"),
        "records": coerced_records,
    }

    post_errors = _schema_errors(new_doc, validator)
    if post_errors:
        # Unreachable in practice -- _coerce_record's own branches only ever
        # produce schema-valid shapes -- but a coercion bug must fail loud
        # here, never silently write a broken fragment to disk.
        raise SkepticReadyError(
            "internal error: coerced fragment failed schema validation", offending=post_errors
        )

    _atomic_write_json(fragment_path, new_doc)
    return {"success": True, "records": len(coerced_records), "coerced": coerced_count}


# ---------------------------------------------------------------------------
# --merge-fragments
# ---------------------------------------------------------------------------

def run_merge_fragments(run_dir, out_path, schemas_dir=None) -> dict:
    run_dir = Path(run_dir)
    if not run_dir.is_dir():
        raise SkepticReadyError(f"run dir not found: {run_dir}")
    schemas_dir = Path(schemas_dir) if schemas_dir else SCHEMAS_DIR_DEFAULT

    schema = _load_schema_document(schemas_dir / SKEPTIC_TRIAGE_SCHEMA)
    validator = jsonschema.Draft202012Validator(schema)

    run_id = run_dir.name
    fragment_paths = sorted(run_dir.glob(f"{SKEPTIC_FRAGMENT_PREFIX}*.json"))

    all_records = []
    for frag_path in fragment_paths:
        doc = _read_json(frag_path, f"fragment {frag_path.name}")
        errors = _schema_errors(doc, validator)
        if errors:
            raise SkepticReadyError(f"fragment {frag_path} failed schema validation", offending=errors)
        frag_run_id = doc.get("run_id")
        if frag_run_id != run_id:
            raise SkepticReadyError(
                f"fragment {frag_path} run_id {frag_run_id!r} does not match run dir name {run_id!r}"
            )
        all_records.extend(doc.get("records", []))

    # Deterministic order regardless of which fragment file held which
    # record, or the order fragments were globbed/read in: the RECORD-level
    # sort (never the file order) is what makes this byte-identical.
    all_records.sort(key=lambda r: (r.get("assignment_id") or "", r.get("source_form") or ""))

    merged = {"schema_version": 1, "run_id": run_id, "records": all_records}
    post_errors = _schema_errors(merged, validator)
    if post_errors:
        raise SkepticReadyError(
            "internal error: merged document failed schema validation", offending=post_errors
        )

    out_path = Path(out_path)
    _atomic_write_json(out_path, merged)
    return {
        "success": True,
        "run_id": run_id,
        "fragments_merged": len(fragment_paths),
        "records": len(all_records),
        "out_path": str(out_path),
    }


# ---------------------------------------------------------------------------
# --verify-merged helpers
# ---------------------------------------------------------------------------

def _frozen_input_tamper_reason(
    label: str, path: Path, state: str, content: bytes, stamped_sha256
) -> "str | None":
    """H1 mitigation (verifier half): hashes an ALREADY-CAPTURED
    ``(state, content)`` snapshot via ``compute_frozen_input_hash_from_state``
    (imported from ``suspicion_scan.py``; ``skeptic_setup.py`` stamps with
    the SAME function, over its own captured snapshot at derivation-read
    time -- see that function's own docstring for why the stamper and
    verifier need opposite freshness semantics from the same underlying
    hash formula, never two independently-drifting copies; state-tagged, so
    absent/regular-empty/irregular paths hash DIFFERENTLY, codex round 2)
    and compares it against ``stamped_sha256`` -- the aggregate manifest's
    own ``canon_sha256``/``manifest_sha256``/``senses_sha256`` (#243),
    stamped by skeptic_setup.py at setup time. Returns None when
    ``stamped_sha256`` itself is absent (an older/hand-built aggregate
    manifest predating this check -- skipped, not a failure) or when the
    hashes agree; otherwise a human-readable mismatch reason.

    Codex round 7: this is the ONLY tamper-reason comparator
    ``frozen_input_check()`` uses now, for canon.json/manifest.json/
    canon_senses.json alike -- it used to have a path-based twin (re-reading
    ``path`` itself via ``compute_frozen_input_hash()``) that
    ``frozen_input_check()`` called for manifest.json specifically, on the
    reasoning that manifest.json has no downstream parser in this module to
    keep consistent with a REUSED snapshot the way canon/senses do. That
    reasoning was true but answered the wrong question: whether a read can
    fail without crashing the caller (``tolerant_reads``) is about SNAPSHOT
    CAPTURE, not about snapshot reuse. The path-based twin captured its own
    snapshot via ``compute_frozen_input_hash()``'s un-gated
    ``read_frozen_input_snapshot()`` call -- a second capture path
    ``tolerant_reads`` never saw -- so a stamped manifest.json OSError
    escaped ``run_check_frozen_inputs`` raw even with ``tolerant_reads=True``,
    despite that caller's whole contract being "never a crash". Every
    frozen input this check covers now goes through the SAME gated capture
    (``frozen_input_check()``'s own table + ``_snapshot_or_none()``) before
    ever reaching this function, so nothing in this module calls
    ``compute_frozen_input_hash()`` any more (the test suite still imports
    it directly to stamp fixtures -- see the top-of-file import comment).

    BEST-EFFORT integrity tripwire only, NOT a sound tamper-detection
    guarantee: it reliably catches ACCIDENTAL/non-adversarial mutation (a
    crash mid-write, a stray process, a well-behaved but buggy agent), but a
    prompt-injected ADVERSARIAL agent -- the actual threat this was written
    against -- has pipeline-wide filesystem write access and can simply
    rewrite (or delete) this very stamp inside ``assignments.json`` to match
    whatever it tampered, defeating this check without needing to evade it.
    A sound version (anchoring the setup-time hash somewhere such an agent
    cannot reach) is deferred to Phase 3; never a filesystem sandbox."""
    if not isinstance(stamped_sha256, str):
        return None
    actual = compute_frozen_input_hash_from_state(state, content)
    if actual == stamped_sha256:
        return None
    return (
        f"{label} at {path} has changed since skeptic_setup.py stamped this run "
        f"(sha256 {actual} != stamped {stamped_sha256}) -- possible tamper of the frozen input, HALTING"
    )


def frozen_input_check(
    aggregate: dict, canon_path: Path, manifest_path: Path, senses_path: Path,
    *, tolerant_reads: bool = False,
) -> tuple:
    """THE one shared H1 tripwire check (codex round 2): every one of
    ``canon.json``/``manifest.json``/``canon_senses.json`` against
    AGGREGATE's own ``canon_sha256``/``manifest_sha256``/``senses_sha256``
    stamps -- a state-tagged hash comparison only (never requires any of
    the three to successfully PARSE, so a deleted or schema-malformed
    frozen input still produces an answer here). Returns
    ``(frozen_input_mismatch: bool, reasons: list[str], canon_snapshot:
    tuple[str, bytes] | None, senses_snapshot: tuple[str, bytes] | None)``.
    A snapshot is ``None`` whenever there was no stamp to compare it
    against (see below) OR (``tolerant_reads=True`` only) the read itself
    failed.

    All three inputs are driven off ONE table (codex round 7, see the loop
    below) -- each entry supplies its own label/stamp-key/path, and every
    entry, with no exception, is captured via ``_snapshot_or_none()`` (the
    ``tolerant_reads`` gate) and compared via ``_frozen_input_tamper_reason()``.
    This is what makes the round-7 bug structurally unrepeatable rather
    than just fixed once: manifest.json used to be wired in as a
    hand-written call straight to a path-based twin of
    ``_frozen_input_tamper_reason`` (re-reading manifest_path itself via
    ``compute_frozen_input_hash()``, a SEPARATE capture path that never
    passed through ``_snapshot_or_none``/``tolerant_reads`` at all) -- a
    stamped manifest.json ``OSError`` therefore escaped
    ``run_check_frozen_inputs`` raw even with ``tolerant_reads=True``,
    despite that caller's whole contract being "never a crash". Folding
    manifest.json into the same table/loop as canon and senses closes that
    specific gap and removes the capacity for a future fourth frozen input
    to reintroduce it by the same route: the ONLY way to wire one in is to
    append an entry to the table, and the table has no room for a call
    site that skips the gate.

    Codex round 5: canon.json and canon_senses.json are each hashed from
    their captured ``(state, bytes)`` snapshot -- the snapshot is RETURNED
    (not just hashed-and-discarded, the pre-round-5 shape) so a caller that
    also needs to PARSE canon/senses downstream (``run_verify_merged``'s
    own ``_resolve_competitors`` call, via its ``canon_snapshot``/
    ``senses_snapshot`` kwargs) can reuse this EXACT snapshot instead of
    re-reading the path a second time -- closing the race where H1
    approves one on-disk version while a later independent read silently
    consumes another. manifest.json has no such downstream parser in this
    module, so its snapshot is captured through the SAME table/loop (codex
    round 7) but not part of the return tuple -- only its tamper
    comparison feeds ``reasons``/``frozen_input_mismatch``.

    Codex round 6 BLOCKER: canon.json/canon_senses.json used to be read
    UNCONDITIONALLY, even when AGGREGATE had no stamp at all for that
    input -- there was nothing to compare the read against, so the read
    bought nothing, yet a transient failure on it (a forced I/O error,
    codex's own repro) still propagated raw. manifest.json never had this
    particular bug (the stamp was always checked before the read), but it
    had the OSError-tolerance one above instead -- both are now the same
    guard, shared by all three entries in the round-7 table: the stamp is
    checked FIRST, and the read is skipped entirely (snapshot stays
    ``None``) when it's absent, regardless of ``tolerant_reads``.

    ``tolerant_reads`` (codex round 6) governs the SEPARATE case where a
    stamp genuinely IS present but the read itself fails (``OSError``):
    ``False`` (default, ``run_verify_merged``'s choice) lets it raise RAW
    -- fail-closed, matching the reasoning already established for that
    caller (degrading canon to ``{}`` downstream would silently empty the
    competitors universe and let every ambiguous form sail through
    unflagged, fail-OPEN on the exact property this release makes
    fail-closed). ``True`` (``run_check_frozen_inputs``'s choice) catches
    it and leaves that snapshot ``None`` instead -- that caller discards
    ALL THREE captured snapshots unconditionally (no downstream parse to
    keep consistent with anything), so raising there buys nothing but
    breaks its own documented "never a crash" contract; a read failure
    degrades that one check the same way an absent stamp already does.
    Codex round 7: this now genuinely covers manifest.json too, closing
    the gap the round-6 fix itself left open by leaving manifest on its
    own ungated path.

    Used by BOTH ``run_verify_merged`` (called AFTER schema-validating
    AGGREGATE, before anything downstream ever attempts to PARSE
    canon.json/canon_senses.json) and the standalone ``--check-frozen-inputs``
    CLI mode below -- the latter exists so the calling Workflow can run this
    exact check at a SECOND decision point the merged-verification path
    never reaches: when every batch's own fragment fails to become ready
    (``skeptic-pass-wf.template.js``'s ``notReadyBatches.length > 0``
    branch), the pipeline today gives up with an ORDINARY advisory
    ``fragment-check-failed`` and never calls ``--verify-merged`` at all --
    so a sidecar tampered sometime after ``skeptic_setup.py`` stamped this
    run but before any batch's fragment ever validated would previously go
    completely unreported as the FATAL tamper it is. That CLI mode has no
    downstream parser either -- it discards all three captured snapshots.
    Deliberately NOT folded into ``run_validate_fragment`` itself (which
    has no visibility into AGGREGATE at all -- the per-batch check only
    ever receives that batch's own bare assignment_id array) -- see the
    module docstring's own note on why this lives at the ORCHESTRATION
    decision point instead."""
    reasons = []
    frozen_input_mismatch = False

    def _snapshot_or_none(path: Path):
        if not tolerant_reads:
            return read_frozen_input_snapshot(path)
        try:
            return read_frozen_input_snapshot(path)
        except OSError:
            return None

    # Codex round 7: the table itself is the fix -- every frozen input this
    # check covers is an entry here, and the loop below is the ONLY place
    # that reads one, so a future fourth input (or a rewrite of this
    # function) cannot wire a read in without going through
    # _snapshot_or_none. manifest.json joining canon/senses here (it used
    # to be a separate hand-written call to a path-based, ungated
    # comparator) is the round-7 fix itself -- see _frozen_input_tamper_reason's
    # own docstring for the bug that call site reintroduced.
    #
    # Round 8 (#243 codex follow-up): the (key, label, stamp_key) triples
    # below are no longer a literal written here -- they come from
    # `FROZEN_INPUT_SPECS` (skeptic_constants.py), the SAME table
    # skeptic_setup.py's stamper iterates to build the stamp fields it
    # writes into `assignments.json`. Before this, the round-7 table above
    # was still an independent, hand-maintained copy of the set the stamper
    # separately enumerated: a fourth frozen input could be added to the
    # stamper (and to this schema) while simply never being typed in here,
    # and nothing would fail -- it just wouldn't be checked. Sourcing both
    # sides from one tuple closes that: this loop is still the only place
    # that reads a frozen input's bytes (round-7's own guarantee, unchanged),
    # and now it is also true that the SET of inputs it reads can't diverge
    # from the set the stamper stamps, because both read that set from the
    # same place. `paths` below is the one place PER-CALL path overrides
    # (``--canon``/``--manifest-path``/``--senses-path``) join the shared
    # key names -- a genuinely NEW frozen input (a key with no existing
    # entry in `paths`) still needs a matching entry here too (this
    # function's own signature has no room for a path it was never told
    # about), and omitting one fails loudly (``KeyError``) rather than
    # silently. That claim was only ever half true, though: a
    # `FROZEN_INPUT_SPECS` entry that instead REUSES an existing key does
    # NOT ``KeyError`` -- ``paths[key]`` resolves fine to the path already
    # there -- so this dict alone never closed the pre-round-8 gap against
    # THAT shape of drift; see round 11 below.
    # Round 9 (#243): this dict, and the digest functions in
    # skeptic_setup.py/suspicion_scan.py, are the other hand-maintained
    # sites FROZEN_INPUT_SPECS does NOT drive -- see that tuple's own
    # comment in skeptic_constants.py for the full list. That was true of
    # all three in the same way at the time: each is a fixed-shape
    # signature/literal that a new frozen input still needs a hand-added
    # entry in.
    #
    # Round 10 (#243): the two digest functions no longer belong in quite
    # the same bucket as this `paths` dict. Their SIGNATURES are still
    # hand-maintained exactly as before (unchanged this round, still not
    # derived from FROZEN_INPUT_SPECS) -- but each function BODY now
    # builds its own `{key: (state, bytes)}` map from the parameters it
    # already receives and asserts that map's key set equals
    # FROZEN_INPUT_SPECS's key set before hashing anything, the same
    # fail-loud discipline this `paths` dict already had (`KeyError` here,
    # `AssertionError` there). A frozen input added to the tuple with no
    # matching hand-added parameter now fails CLOSED in both digest
    # functions too, instead of the digest silently omitting it forever --
    # see skeptic_constants.py's own comment for the current, re-derived
    # list of what still needs a hand-added entry versus what now fails
    # loud automatically.
    #
    # Round 11 (#243): codex found the exact gap the round-8 comment above
    # now calls out directly -- a `FROZEN_INPUT_SPECS` entry that REUSES an
    # existing key (rather than getting its own) sails through
    # `paths[key]` with no `KeyError`, so `specs` below ends up with FOUR
    # rows but only THREE distinct paths: the reused key's path (e.g.
    # canon.json) gets tamper-checked TWICE, under two different
    # `stamp_key`s, while the actual fourth frozen input is never
    # represented in `paths` at all and is silently never checked. This is
    # the same class the two digest functions' own round-10 key-set guard
    # was ALSO blind to until round 11 hardened it from a set to a sorted,
    # non-deduplicated key list -- this `paths` dict never had ANY guard
    # against it, set-based or otherwise, so it gets the same fix fresh
    # here rather than a hardening of a prior one.
    paths = {"canon": canon_path, "manifest": manifest_path, "senses": senses_path}
    _spec_keys = [key for key, _label, _stamp_key in FROZEN_INPUT_SPECS]
    if sorted(paths) != sorted(_spec_keys):
        raise AssertionError(
            "frozen_input_check(): paths keys "
            f"{sorted(paths)} != FROZEN_INPUT_SPECS keys {sorted(_spec_keys)} "
            "-- a frozen input was added to skeptic_constants.FROZEN_INPUT_SPECS "
            "without a matching hand-added path parameter/paths entry here (or "
            "vice versa), or FROZEN_INPUT_SPECS contains a duplicate key; see "
            "skeptic_constants.py's \"what FROZEN_INPUT_SPECS does NOT cover\" "
            "comment."
        )
    specs = tuple((key, label, stamp_key, paths[key]) for key, label, stamp_key in FROZEN_INPUT_SPECS)
    snapshots = {}
    for key, label, stamp_key, path in specs:
        snapshot = None
        stamp = aggregate.get(stamp_key)
        if isinstance(stamp, str):
            snapshot = _snapshot_or_none(path)
            if snapshot is not None:
                reason = _frozen_input_tamper_reason(label, path, snapshot[0], snapshot[1], stamp)
                if reason:
                    reasons.append(reason)
                    frozen_input_mismatch = True
        snapshots[key] = snapshot

    # manifest_snapshot is captured through the exact same gated loop above
    # (that parity is the whole point of this round's fix) but was never
    # part of this function's return contract -- manifest.json has no
    # downstream parser in this module to hand it to (unlike canon/senses,
    # see _resolve_competitors' own canon_snapshot/senses_snapshot reuse).
    return frozen_input_mismatch, reasons, snapshots["canon"], snapshots["senses"]


# ---------------------------------------------------------------------------
# --check-frozen-inputs (codex round 2)
# ---------------------------------------------------------------------------

def run_check_frozen_inputs(aggregate_manifest_path, canon_path=None, manifest_path=None,
                             senses_path=None) -> dict:
    """Standalone entry point for the SAME H1 tripwire `run_verify_merged`
    applies internally (`frozen_input_check()`) -- exists so the calling
    Workflow can run this exact check at a decision point
    `--verify-merged` never reaches: when every batch's own fragment fails
    to become ready (`skeptic-pass-wf.template.js`'s own
    `notReadyBatches.length > 0` branch), the pipeline today gives up with
    an ordinary advisory `fragment-check-failed` and never calls
    `--verify-merged` at all -- so a sidecar tampered sometime after
    `skeptic_setup.py` stamped this run but before any batch's fragment
    ever validated would previously go completely unreported as the FATAL
    tamper it is (codex round 2). This mode is the fix: call it
    UNCONDITIONALLY at that decision point too, not just after a successful
    merge.

    Reads AGGREGATE_MANIFEST with a MINIMAL, tolerant raw JSON parse --
    deliberately NOT full `skeptic-assignment.schema.json` validation --
    because this mode's entire reason to exist is to answer "did the frozen
    inputs change" even when something else has already gone wrong; a
    missing/unreadable/malformed AGGREGATE_MANIFEST degrades to
    `frozen_input_mismatch=False` (nothing to compare against, exactly the
    `_frozen_input_tamper_reason`'s own "stamped hash absent -> skip" rule,
    now applied one level up), never a crash. `canon_path`/`manifest_path`/
    `senses_path` are read via `frozen_input_check()` -- raw bytes plus path
    state (codex round-2 path-state fix), never JSON-parsed here either, so
    a deleted or schema-malformed frozen input still produces a definitive
    answer."""
    aggregate_manifest_path = Path(aggregate_manifest_path)
    canon_path = Path(canon_path) if canon_path else DEFAULT_CANON_PATH
    manifest_path = Path(manifest_path) if manifest_path else DEFAULT_MANIFEST_PATH
    senses_path = Path(senses_path) if senses_path else DEFAULT_SENSES_PATH

    aggregate: dict = {}
    try:
        doc = json.loads(aggregate_manifest_path.read_text(encoding="utf-8"))
        if isinstance(doc, dict):
            aggregate = doc
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        pass

    # This mode has no downstream parser of canon/senses to feed -- discard
    # the two returned snapshots (codex round 5 added them for
    # run_verify_merged's own _resolve_competitors() reuse).
    # tolerant_reads=True (codex round 6 BLOCKER): this mode is documented
    # as "never a crash", but a read failure on canon/senses used to raise
    # RAW regardless -- since nothing here ever consumes the snapshot,
    # there is nothing a tolerant degradation could put at risk. Codex
    # round 7: this flag now genuinely reaches manifest.json's read too --
    # it never did before, because manifest.json bypassed
    # frozen_input_check()'s own tolerance gate entirely via a
    # hand-written call site, so a manifest read failure escaped raw even
    # though this exact tolerant_reads=True was already being passed.
    frozen_input_mismatch, reasons, _canon_snapshot, _senses_snapshot = frozen_input_check(
        aggregate, canon_path, manifest_path, senses_path, tolerant_reads=True
    )
    return {"frozen_input_mismatch": frozen_input_mismatch, "missing": reasons}


def _iter_record_evidence(record: dict):
    """Yields ``(label, evidence-or-None)`` for every evidence slot a
    schema-valid triage record may carry: the top-level ``evidence`` key
    (adverse/propose_rescope) and each ``referents[]`` item's own
    ``evidence`` key (propose_split) -- used by the window-scoping check
    (fix M3, see ``run_verify_merged``)."""
    if "evidence" in record:
        yield "evidence", record.get("evidence")
    for i, ref in enumerate(record.get("referents") or []):
        if isinstance(ref, dict):
            yield f"referents[{i}].evidence", ref.get("evidence")


# ---------------------------------------------------------------------------
# --verify-merged
# ---------------------------------------------------------------------------

def run_verify_merged(
    triage_path,
    aggregate_manifest_path,
    manifest_path,
    particle_config: str,
    languages_dir=None,
    schemas_dir=None,
    canon_path=None,
    senses_path=None,
) -> dict:
    triage_path = Path(triage_path)
    aggregate_manifest_path = Path(aggregate_manifest_path)
    schemas_dir = Path(schemas_dir) if schemas_dir else SCHEMAS_DIR_DEFAULT
    canon_path = Path(canon_path) if canon_path else DEFAULT_CANON_PATH
    # #243: same --senses-path convention as --validate-fragment -- absence
    # is tolerated UNCONDITIONALLY (see _resolve_competitors' own docstring).
    # NOT resolved into `competitors` here -- see the ordering note further
    # down, right before the actual `_resolve_competitors()` call: it must
    # run AFTER the H1 byte-level tamper checks, never before.
    resolved_senses_path = Path(senses_path) if senses_path else DEFAULT_SENSES_PATH

    if not triage_path.is_file():
        raise SkepticReadyError(f"{triage_path} not found (nothing to verify)")
    if not aggregate_manifest_path.is_file():
        raise SkepticReadyError(f"{aggregate_manifest_path} not found (nothing to verify against)")

    # These two are genuine preconditions (without them evidence can't be
    # re-authenticated at all) -- a bad manifest/particle-config raises,
    # same as everywhere else. TRIAGE/AGGREGATE_MANIFEST content problems,
    # by contrast, degrade to `missing[]` below -- they are the very things
    # this mode's contract documents as expected FAIL cases, not crashes.
    manifest = _read_json(Path(manifest_path), "manifest.json")
    try:
        language_config = load_language_config(
            particle_config, languages_dir=Path(languages_dir) if languages_dir else LANGUAGES_DIR_DEFAULT
        )
    except BootstrapNamesError as exc:
        raise SkepticReadyError(f"particle config error: {exc}")

    missing = []
    # P1 fix (review-bot #227): tracked SEPARATELY from the generic
    # `missing[]` accumulation so a caller can HALT specifically on "the
    # frozen canon/manifest changed since setup" rather than treating it as
    # just another advisory skeptic-pass failure -- see the module
    # docstring's own note on ``frozen_input_mismatch``.
    frozen_input_mismatch = False
    # Populated by frozen_input_check() below when AGGREGATE is schema-valid
    # enough to run it, and independently per-input even then (codex round
    # 6: an absent canon_sha256/senses_sha256 stamp means that ONE stays
    # None). _resolve_competitors() further down resolves each
    # independently -- None falls back to a fresh read for THAT input only
    # (see its own docstring).
    canon_snapshot = None
    senses_snapshot = None

    triage_schema = _load_schema_document(schemas_dir / SKEPTIC_TRIAGE_SCHEMA)
    triage_validator = jsonschema.Draft202012Validator(triage_schema)
    triage = _read_json_or_none(triage_path, missing, "skeptic_triage.json")
    records = []
    if triage is not None:
        errors = _schema_errors(triage, triage_validator)
        if errors:
            missing.append(f"{triage_path} failed schema validation: {errors[0]}")
        else:
            records = triage.get("records", [])

    assignment_schema = _load_schema_document(schemas_dir / SKEPTIC_ASSIGNMENT_SCHEMA)
    assignment_validator = jsonschema.Draft202012Validator(assignment_schema)
    aggregate = _read_json_or_none(aggregate_manifest_path, missing, "assignment manifest")
    assigned_ids = set()
    assignments_by_id = {}
    if aggregate is not None:
        errors = _schema_errors(aggregate, assignment_validator)
        if errors:
            missing.append(f"{aggregate_manifest_path} failed schema validation: {errors[0]}")
        else:
            assignments_by_id = {a["assignment_id"]: a for a in aggregate.get("assignments", [])}
            assigned_ids = set(assignments_by_id)

            # H1 mitigation: frozen-input integrity tripwire (BEST-EFFORT
            # only -- see _frozen_input_tamper_reason's own docstring for
            # why it cannot be sound against an adversarial agent), only
            # meaningful once the aggregate itself is schema-valid enough to
            # trust its own canon_sha256/manifest_sha256/senses_sha256
            # stamps. #243: canon_senses.json joined canon.json/manifest.json
            # as a THIRD frozen input this function now also parses (see
            # _resolve_competitors() below) to project the ambiguity-
            # competitors universe -- untampered-canon/manifest alone no
            # longer suffices once a stale/tampered sidecar can silently
            # change which forms this run treats as colliding.
            # frozen_input_check() is a state-tagged hash comparison (never
            # parses canon/manifest/senses as JSON), so it runs and
            # correctly flags a deleted OR schema-malformed frozen input
            # even though neither can be successfully PARSED -- deliberately
            # called BEFORE the parse-and-project step further down (round
            # after codex review): parsing first would let a
            # malformed/deleted sidecar raise out of this function before
            # frozen_input_mismatch is ever computed, silently downgrading a
            # genuine post-setup tamper into an ordinary advisory failure
            # the caller cannot distinguish from a plain coverage gap.
            # Codex round 5: also captures canon_snapshot/senses_snapshot --
            # the SAME (state, bytes) frozen_input_check() just hashed --
            # for _resolve_competitors() to parse further down (via its
            # own canon_snapshot/senses_snapshot kwargs), instead of that
            # independently re-reading both paths.
            # tolerant_reads=False (codex round 6, explicit -- this is the
            # default, but stated here so the fail-closed choice is visible
            # at the call site, not just in frozen_input_check()'s own
            # docstring): a read failure here must raise RAW. Degrading
            # canon to {} instead would silently empty the competitors
            # universe downstream and let every ambiguous form sail through
            # unflagged -- fail-OPEN on the exact property this release
            # makes fail-closed.
            mismatch, reasons, canon_snapshot, senses_snapshot = frozen_input_check(
                aggregate, canon_path, Path(manifest_path), resolved_senses_path,
                tolerant_reads=False,
            )
            missing.extend(reasons)
            frozen_input_mismatch = frozen_input_mismatch or mismatch

    # #243: project the ambiguity-competitors universe -- AFTER the H1
    # byte-level tamper checks above, never before (see their own comment).
    # A parse failure here (a malformed canon_senses.json that was ALREADY
    # broken at setup time, hash unchanged -- vs. one tampered post-setup,
    # which the H1 check above already caught) is NOT a precondition this
    # function crashes on, unlike a bad manifest/particle-config: it
    # degrades to `missing[]`, same as every other TRIAGE/AGGREGATE_MANIFEST
    # content problem, and disables collision-checking for the rest of this
    # call (`competitors=None`, `_evidence_failure_reason`/`_coerce_record`'s
    # own documented pre-#243 fallback) rather than aborting verification
    # entirely and losing every other check this function would otherwise
    # still report.
    #
    # Codex round 5: when frozen_input_check() above captured
    # canon_snapshot/senses_snapshot, project from THOSE SAME bytes rather
    # than re-reading independently -- a mutation landing between an
    # independent re-read and the H1 check could otherwise let H1 approve
    # one on-disk version while this projection silently consumed another,
    # with frozen_input_mismatch still reporting False.
    #
    # Codex round 6: each snapshot is passed INDEPENDENTLY, never gated as
    # a pair -- frozen_input_check() may now capture only one of the two
    # (an absent canon_sha256/senses_sha256 stamp means that ONE input was
    # never read at all, see its own "no stamp -> no read" fix). An
    # all-or-nothing "both or neither" gate here was the round-6
    # regression: it discarded a perfectly good, already H1-approved
    # canon_snapshot the moment senses_sha256 happened to be absent (a
    # realistic case, not a corrupted-AGGREGATE edge), silently re-reading
    # canon.json fresh instead -- which could disagree with what H1 just
    # certified. _resolve_competitors() itself now resolves each
    # independently (`canon_snapshot=None`/`senses_snapshot=None` each
    # falls back to a fresh read for THAT input only), so passing both
    # here -- whichever is None or not -- is always correct.
    try:
        competitors = _resolve_competitors(
            canon_path, resolved_senses_path,
            canon_snapshot=canon_snapshot, senses_snapshot=senses_snapshot,
        )
    except SkepticReadyError as exc:
        missing.append(str(exc))
        competitors = None

    # FIX (c): run_id binding -- a triage merged against a foreign/stale
    # run's coverage universe is meaningless no matter how clean it
    # otherwise looks.
    if triage is not None and aggregate is not None:
        triage_run_id = triage.get("run_id")
        aggregate_run_id = aggregate.get("run_id")
        if triage_run_id != aggregate_run_id:
            missing.append(
                f"triage run_id {triage_run_id!r} does not match aggregate manifest run_id {aggregate_run_id!r}"
            )

    # FIX (e): multiplicity -- exactly one triage record per assigned
    # assignment_id. A duplicate (of an assigned id or a foreign one) is a
    # FAIL; the set-based coverage checks below would silently absorb it.
    id_counts = Counter(r.get("assignment_id") for r in records if isinstance(r, dict))
    covered_ids = set(id_counts)
    missing.extend(
        f"assignment {aid} has no triage record (coverage gap)"
        for aid in sorted(assigned_ids - covered_ids, key=lambda aid: aid or "")
    )
    missing.extend(
        f"triage record {aid} references an assignment_id absent from the aggregate manifest"
        for aid in sorted(covered_ids - assigned_ids, key=lambda aid: aid or "")
    )
    missing.extend(
        f"assignment_id {aid} has {count} triage records (expected exactly 1)"
        for aid, count in sorted(id_counts.items(), key=lambda kv: kv[0] or "")
        if count > 1
    )

    for rec in records:
        if not isinstance(rec, dict):
            continue
        rec_aid = rec.get("assignment_id")

        # FIX (a): re-run the SAME token check --validate-fragment enforces
        # -- a merged record's own source_form/assignment_id pairing must
        # still be self-consistent.
        token_reason = _token_mismatch_reason(rec)
        if token_reason:
            missing.append(f"{rec_aid}: {token_reason}")

        assignment = assignments_by_id.get(rec_aid)
        if assignment is not None:
            # FIX (d): source_form binding -- the record must describe the
            # SAME entity the aggregate manifest assigned this id to (a
            # corrupted aggregate could otherwise disagree with a
            # self-consistent record about which entity an id refers to).
            if rec.get("source_form") != assignment.get("source_form"):
                missing.append(
                    f"{rec_aid}: triage source_form {rec.get('source_form')!r} does not match "
                    f"aggregate assignment source_form {assignment.get('source_form')!r}"
                )

            # FIX M3 + High (codex round 2): every cited evidence record --
            # the top-level `evidence` AND each `referents[]` item's own
            # `evidence`, INDEPENDENTLY -- must (i) fall inside this
            # assignment's own windows (evidence_verify only proves a
            # citation byte-verifies SOMEWHERE in manifest.blocks{}, never
            # that it came from the bounded window set this entity was
            # actually fed) and (ii) still byte-verify via a FRESH
            # evidence_verify call. (ii) is what the coerce-delta check
            # below cannot guarantee by itself: _coerce_record's own
            # propose_split branch DROPS a referent that fails to verify
            # but leaves the record's verdict at propose_split as long as
            # >=2 others survive, so a 3-referent propose_split with ONE
            # tampered referent (offset/sha256 no longer real) never flips
            # verdict and would sail through a verdict-only check -- this
            # per-citation loop catches that referent directly, regardless
            # of how many siblings still verify.
            allowed_blocks = {w.get("block") for w in assignment.get("windows", [])}
            for label, evidence in _iter_record_evidence(rec):
                if not isinstance(evidence, dict):
                    # Unreachable once schema validation has passed (every
                    # evidence slot this loop visits is schema-required to
                    # be an object) -- defensive only.
                    missing.append(f"{rec_aid}: {label} missing or malformed evidence")
                    continue
                if evidence.get("block") not in allowed_blocks:
                    missing.append(
                        f"{rec_aid}: {label} cites block {evidence.get('block')!r}, which is "
                        f"not among this assignment's own windows "
                        f"{sorted(b for b in allowed_blocks if b is not None)}"
                    )
                reason = _evidence_failure_reason(
                    rec.get("source_form"), evidence, manifest, language_config, competitors=competitors
                )
                if reason is not None:
                    missing.append(f"{rec_aid}: {label} no longer byte-verifies ({reason})")

        # FIX (b): re-run the SAME procedural coercion --validate-fragment
        # applies, and fail whenever the merged record's stored verdict no
        # longer matches what a fresh re-coercion would produce -- this
        # catches verdict-LEVEL structural coercion (an evidence-free
        # adverse/propose_rescope, or a propose_split with fewer than 2
        # referents present/verified AT ALL) that the per-citation loop
        # above does not itself name (it flags each bad citation, never the
        # verdict as a whole). Belt-and-suspenders with that loop, not a
        # substitute for it -- see this function's own docstring.
        coerced = _coerce_record(rec, manifest, language_config, competitors=competitors)
        if coerced.get("verdict") != rec.get("verdict"):
            detail = "; ".join(str(n) for n in (coerced.get("notes") or []))
            missing.append(
                f"{rec_aid}: stored verdict {rec.get('verdict')!r} does not survive fresh "
                f"re-verification (would resolve to {coerced.get('verdict')!r}: {detail})"
            )

    return {
        "verified": not missing,
        "missing": sorted(set(missing)),
        "frozen_input_mismatch": frozen_input_mismatch,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Validate/coerce, deterministically merge, and fresh-read verify "
            "skeptic_triage.json (RFC #215 Phase 2) -- see this file's own "
            "module docstring for the full four-mode contract."
        )
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--validate-fragment", metavar="PATH", default=None,
        help="Schema-validate + evidence-adapter-coerce ONE run-scoped triage fragment, "
             "atomically rewriting it in place.",
    )
    group.add_argument(
        "--merge-fragments", metavar="RUN_DIR", default=None,
        help="Serialized, deterministic merge of every fragment in RUN_DIR into "
             "skeptic_triage.json (see --out).",
    )
    group.add_argument(
        "--verify-merged", metavar=("TRIAGE", "AGGREGATE_MANIFEST"), nargs=2, default=None,
        help="Fresh-read, disk-independent verification of a merged skeptic_triage.json "
             "against the assignment manifest that gates its coverage.",
    )
    group.add_argument(
        "--check-frozen-inputs", metavar="AGGREGATE_MANIFEST", default=None,
        help="(codex round 2) Standalone H1 tripwire check ONLY -- the SAME "
             "canon.json/manifest.json/canon_senses.json re-hash-and-compare "
             "--verify-merged applies internally, exposed as its own mode so the "
             "calling Workflow can run it at a SECOND decision point --verify-merged "
             "never reaches: when every batch's own fragment failed to become ready, "
             "the pipeline previously gave up with an ordinary advisory outcome and "
             "never checked the frozen inputs at all. AGGREGATE_MANIFEST is read with "
             "a minimal, tolerant raw JSON parse (never full schema validation, and "
             "never crashes on a missing/malformed file -- degrades to "
             "frozen_input_mismatch:false, nothing to compare against). Prints "
             "{\"frozen_input_mismatch\": bool, \"missing\": [...]}. Uses the same "
             "--canon/--senses-path/--manifest-path flags (and defaults) as the other "
             "two modes.",
    )
    parser.add_argument(
        "--manifest-path", metavar="PATH", default=None,
        help=f"Path to manifest.json (default: {DEFAULT_MANIFEST_PATH}). Required by "
             "--validate-fragment and --verify-merged.",
    )
    parser.add_argument(
        "--particle-config", metavar="FILENAME", default=None,
        help="The profile's own source.language.particle_config LITERAL value. "
             "Required by --validate-fragment and --verify-merged.",
    )
    parser.add_argument(
        "--languages-dir", metavar="PATH", default=None,
        help=f"Override the languages directory (default: {LANGUAGES_DIR_DEFAULT}).",
    )
    parser.add_argument(
        "--schemas-dir", metavar="PATH", default=None,
        help=f"Override the schemas directory (default: {SCHEMAS_DIR_DEFAULT}).",
    )
    parser.add_argument(
        "--canon", metavar="PATH", default=None,
        help=f"Path to canon.json (default: {DEFAULT_CANON_PATH}). Used by BOTH modes (#243) to "
             "project the ambiguity-competitors universe (fold-collision detection, see "
             "canon_senses.fold_collision_map) every cited evidence record is re-verified against. "
             "--verify-merged ALSO uses it, when the aggregate manifest stamps canon_sha256, for a "
             "BEST-EFFORT integrity tripwire (fail-closed on mismatch), not a sound tamper-proof "
             "guarantee against an adversarial agent (see _frozen_input_tamper_reason's own "
             "docstring); that check is skipped entirely when the aggregate manifest carries no "
             "such stamp, e.g. an older/hand-built one.",
    )
    parser.add_argument(
        "--senses-path", metavar="PATH", default=None,
        help=f"Path to canon_senses.json (default: {DEFAULT_SENSES_PATH}). Used by BOTH modes "
             "(#243), together with --canon, to project the ambiguity-competitors universe -- "
             "absence is tolerated UNCONDITIONALLY, whether this flag is omitted or explicitly "
             "given (unlike canon_adjudication_audit.py's own --senses-path, which a human types "
             "by hand: this script's copy is always machine-generated by "
             "skeptic-pass-wf.template.js with the canonical default value baked in, so an "
             "explicit-but-absent path is the documented normal 'no senses sidecar yet' state, "
             "never a typo to punish -- a schema-invalid or non-regular sidecar still raises). "
             "--verify-merged ALSO uses it, when the aggregate manifest stamps senses_sha256, for "
             "the same BEST-EFFORT tamper tripwire as --canon/canon_sha256.",
    )
    parser.add_argument(
        "--expect-assignments-file", metavar="PATH", default=None,
        help="--validate-fragment only: a JSON array of expected assignment_id strings "
             "(this batch's own manifest slice) -- asserts EXACT coverage, no missing, "
             "no extra.",
    )
    parser.add_argument(
        "--out", metavar="PATH", default=None,
        help=f"--merge-fragments only: where to write the merged triage "
             f"(default: {DURABLE_ROOT / SKEPTIC_TRIAGE_FILENAME}).",
    )
    return parser


def main(argv=None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    manifest_path = Path(args.manifest_path) if args.manifest_path else DEFAULT_MANIFEST_PATH

    try:
        if args.validate_fragment is not None:
            if not args.particle_config:
                parser.error("--validate-fragment requires --particle-config")
            result = run_validate_fragment(
                Path(args.validate_fragment),
                manifest_path,
                args.particle_config,
                languages_dir=args.languages_dir,
                expect_assignments_file=args.expect_assignments_file,
                schemas_dir=args.schemas_dir,
                canon_path=args.canon,
                senses_path=args.senses_path,
            )
        elif args.merge_fragments is not None:
            out_path = Path(args.out) if args.out else DURABLE_ROOT / SKEPTIC_TRIAGE_FILENAME
            result = run_merge_fragments(Path(args.merge_fragments), out_path, schemas_dir=args.schemas_dir)
        elif args.verify_merged is not None:
            if not args.particle_config:
                parser.error("--verify-merged requires --particle-config")
            triage_arg, aggregate_arg = args.verify_merged
            result = run_verify_merged(
                Path(triage_arg),
                Path(aggregate_arg),
                manifest_path,
                args.particle_config,
                languages_dir=args.languages_dir,
                schemas_dir=args.schemas_dir,
                canon_path=args.canon,
                senses_path=args.senses_path,
            )
        else:
            assert args.check_frozen_inputs is not None  # guaranteed by the required mutex group
            result = run_check_frozen_inputs(
                Path(args.check_frozen_inputs),
                canon_path=args.canon,
                manifest_path=manifest_path,
                senses_path=args.senses_path,
            )
    except SkepticReadyError as exc:
        payload = {"success": False, "error": str(exc)}
        if exc.offending is not None:
            payload["offending"] = exc.offending
        print(json.dumps(payload, ensure_ascii=False))
        return 1
    except Exception as exc:  # pragma: no cover -- defensive catch-all
        print(json.dumps({"success": False, "error": f"unexpected error: {exc}"}, ensure_ascii=False))
        return 1

    print(json.dumps(result, ensure_ascii=False))
    if args.verify_merged is not None:
        return 0 if result.get("verified") else 1
    if args.check_frozen_inputs is not None:
        return 1 if result.get("frozen_input_mismatch") else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
