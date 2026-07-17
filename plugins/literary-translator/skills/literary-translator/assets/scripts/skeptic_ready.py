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

Three deterministic CLI modes, mutually exclusive:

  --validate-fragment FRAGMENT --manifest-path M --particle-config NAME
      [--languages-dir DIR] [--expect-assignments-file PATH]
      [--schemas-dir DIR]
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
    FRAGMENT.

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
      [--canon PATH]
    Fresh-read, disk-INDEPENDENT verification, at the SAME rigor
    ``--validate-fragment`` applies (never weaker -- every one of that
    mode's checks is redone here too, plus merge-specific ones only
    meaningful post-merge): re-validates TRIAGE against the schema,
    re-validates AGGREGATE_MANIFEST (the assignments.json
    ``skeptic_setup.py`` wrote BEFORE dispatch) against
    ``skeptic-assignment.schema.json``, then, all FAIL-CLOSED (each
    accumulates into ``missing[]``, never raised):
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
        ``manifest_sha256`` (see ``skeptic-assignment.schema.json``), the
        on-disk ``--canon`` file (default ``{durable_root}/canon.json``) /
        ``manifest.json`` is re-hashed and must still match. This catches
        ACCIDENTAL/non-adversarial mutation (a crash mid-write, a stray
        process, a well-behaved but buggy agent) -- it is NOT sound against
        a prompt-injected adversarial agent, which has pipeline-wide
        filesystem write access and can rewrite or simply delete this same
        co-located stamp to match its own tampered canon/manifest (the
        stamp lives in ``assignments.json``, inside the very run_dir such an
        agent can already write to); a sound version (anchoring the
        setup-time hash in a channel the agent cannot reach) is deferred to
        Phase 3. Absent stamp -> the corresponding check is skipped (an
        older/hand-built aggregate manifest, backward-compatible; also the
        only way a determined adversary defeats this cheaply). A mismatch
        here is surfaced DISTINCTLY from every other failure above via the
        ``frozen_input_mismatch`` output field (see below) -- this is what
        lets a caller HALT the pipeline outright on "canon/manifest changed
        since setup" instead of treating it as just another advisory
        skeptic-pass failure like a coverage gap or an unverified citation.
    Never trusts anything this run itself, or --validate-fragment, already
    claimed -- every check here is redone from the two files on disk plus a
    fresh read of ``manifest.json``/``canon.json``. Prints
    ``{"verified": bool, "missing": [...], "frozen_input_mismatch": bool}``
    -- the base two fields are the same relay shape ``canon_validate.py
    --verify-merged``/``CANON_VERIFY_SCHEMA`` use; ``frozen_input_mismatch``
    is this mode's own addition, true iff at least one canon/manifest hash
    check above actually fired (its own reason is ALSO still folded into
    ``missing[]``, so ``verified`` stays False either way -- this field only
    adds the ability to distinguish WHICH kind of failure occurred).

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


def _evidence_failure_reason(source_form, evidence, manifest, language_config) -> "str | None":
    """Calls ``verify_evidence(source_form, {"sense_id": None, "evidence":
    mapped}, manifest, language_config)`` and returns its failure reason, or
    None when the citation byte-verifies (checks (i)-(iv) of
    ``evidence_verify.py``'s own docstring, including matcher-
    authentication). A citation whose ``block`` is an embedded-verse node
    fails here too -- ``evidence_verify`` authenticates only against
    ``manifest.blocks{}``, never ``verse.store[]`` -- which is exactly how
    an embedded-verse citation ends up coerced to ``insufficient_window``
    below, with no special-case code needed."""
    mapped = _map_evidence_record(evidence)
    failure = verify_evidence(source_form, {"sense_id": None, "evidence": mapped}, manifest, language_config)
    return None if failure is None else failure.reason


def _coerce_record(record: dict, manifest: dict, language_config) -> dict:
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
    schema-conformant record."""
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
                source_form, evidence, manifest, language_config
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
        if _evidence_failure_reason(source_form, evidence, manifest, language_config) is not None:
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
) -> dict:
    fragment_path = Path(fragment_path)
    schemas_dir = Path(schemas_dir) if schemas_dir else SCHEMAS_DIR_DEFAULT

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

    coerced_records = [_coerce_record(rec, manifest, language_config) for rec in records]
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

def _read_bytes_tolerant(path: Path) -> bytes:
    """Mirrors skeptic_setup.py's own canon.json tolerance exactly: an
    ABSENT file hashes as ``b""`` (never a read error) -- a project may
    genuinely have no canon.json yet, and skeptic_setup.py's own stamped
    canon_sha256 is computed the same tolerant way, so the two must agree
    byte-for-byte to ever match."""
    path = Path(path)
    return path.read_bytes() if path.is_file() else b""


def _frozen_input_tamper_reason(label: str, path: Path, stamped_sha256) -> "str | None":
    """H1 mitigation (verifier half): re-hashes ``path`` (tolerant of
    absence, see ``_read_bytes_tolerant``) and compares it against
    ``stamped_sha256`` -- the aggregate manifest's own ``canon_sha256``/
    ``manifest_sha256``, stamped by skeptic_setup.py at setup time. Returns
    None when ``stamped_sha256`` itself is absent (an older/hand-built
    aggregate manifest predating this check -- skipped, not a failure) or
    when the hashes agree; otherwise a human-readable mismatch reason.

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
    actual = hashlib.sha256(_read_bytes_tolerant(path)).hexdigest()
    if actual == stamped_sha256:
        return None
    return (
        f"{label} at {path} has changed since skeptic_setup.py stamped this run "
        f"(sha256 {actual} != stamped {stamped_sha256}) -- possible tamper of the frozen input, HALTING"
    )


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
) -> dict:
    triage_path = Path(triage_path)
    aggregate_manifest_path = Path(aggregate_manifest_path)
    schemas_dir = Path(schemas_dir) if schemas_dir else SCHEMAS_DIR_DEFAULT
    canon_path = Path(canon_path) if canon_path else DEFAULT_CANON_PATH

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
            # trust its own canon_sha256/manifest_sha256 stamps.
            for label, path, stamped in (
                ("canon.json", canon_path, aggregate.get("canon_sha256")),
                ("manifest.json", Path(manifest_path), aggregate.get("manifest_sha256")),
            ):
                reason = _frozen_input_tamper_reason(label, path, stamped)
                if reason:
                    missing.append(reason)
                    frozen_input_mismatch = True

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
        f"assignment {aid} has no triage record (coverage gap)" for aid in sorted(assigned_ids - covered_ids)
    )
    missing.extend(
        f"triage record {aid} references an assignment_id absent from the aggregate manifest"
        for aid in sorted(covered_ids - assigned_ids)
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
                reason = _evidence_failure_reason(rec.get("source_form"), evidence, manifest, language_config)
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
        coerced = _coerce_record(rec, manifest, language_config)
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
            "module docstring for the full three-mode contract."
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
        help=f"--verify-merged only: path to canon.json (default: {DEFAULT_CANON_PATH}). Used ONLY "
             "when the aggregate manifest stamps canon_sha256 -- re-hashed and compared as a "
             "BEST-EFFORT integrity tripwire (fail-closed on mismatch), not a sound tamper-proof "
             "guarantee against an adversarial agent (see _frozen_input_tamper_reason's own "
             "docstring); the check is skipped entirely when the aggregate manifest carries no "
             "such stamp, e.g. an older/hand-built one.",
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
            )
        elif args.merge_fragments is not None:
            out_path = Path(args.out) if args.out else DURABLE_ROOT / SKEPTIC_TRIAGE_FILENAME
            result = run_merge_fragments(Path(args.merge_fragments), out_path, schemas_dir=args.schemas_dir)
        else:
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
    return 0


if __name__ == "__main__":
    sys.exit(main())
