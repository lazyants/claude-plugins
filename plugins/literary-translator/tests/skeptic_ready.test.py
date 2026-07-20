"""Tests for scripts/skeptic_ready.py -- RFC #215 Phase 2's ``--validate-
fragment``/``--merge-fragments``/``--verify-merged`` split (the skeptic
pass's analogue of ``canon_validate.py``'s own ``--check-batch``/``--merge-
batches``/``--verify-merged``).

Module under test lives outside any Python package (a standalone script
copied to ``${durable_root}/scripts/`` at runtime, sibling of
``occ_index.py``/``bootstrap_names.py``/``evidence_verify.py``/
``skeptic_constants.py``, all of which it imports directly), so it is loaded
here via importlib from its real path, with ``SCRIPTS_DIR`` on ``sys.path``
for its own top-level imports to resolve -- mirrors ``tests/occ_index
.test.py``/``tests/evidence_verify.test.py``'s own loader exactly.

Every fixture is synthetic and inline. A REAL particle-config JSON file is
written to a per-test ``languages_dir`` (never a pre-built ``LanguageConfig``
object) because ``skeptic_ready.py`` itself calls
``bootstrap_names.load_language_config(particle_config, languages_dir=...)``
internally -- it never accepts an in-memory config -- so test evidence must
be built against the SAME on-disk config skeptic_ready.py will resolve.
Evidence citations are built from REAL ``occ_index.build_occurrence_records()``
output (context = whole block, matcher-authenticated spans) so a citation
that is asserted to "verify" is exercising the real matcher, never a
hand-typed offset that merely happens to be in range.
"""
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
ASSETS_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets"
SCRIPTS_DIR = ASSETS_DIR / "scripts"
SKEPTIC_READY_SCRIPT = SCRIPTS_DIR / "skeptic_ready.py"
OCC_INDEX_SCRIPT = SCRIPTS_DIR / "occ_index.py"
BOOTSTRAP_NAMES_SCRIPT = SCRIPTS_DIR / "bootstrap_names.py"

assert SKEPTIC_READY_SCRIPT.is_file(), f"skeptic_ready.py not found at {SKEPTIC_READY_SCRIPT}"
assert OCC_INDEX_SCRIPT.is_file(), f"occ_index.py not found at {OCC_INDEX_SCRIPT}"
assert BOOTSTRAP_NAMES_SCRIPT.is_file(), f"bootstrap_names.py not found at {BOOTSTRAP_NAMES_SCRIPT}"


def _load_module(name: str, path: Path, extra_sys_path: Path):
    """Mirrors tests/occ_index.test.py's own loader: SCRIPTS_DIR must be on
    sys.path around the in-process load so a standalone script's own
    top-level ``from ... import ...`` statements resolve exactly like they
    would under a real ``python3 skeptic_ready.py`` invocation."""
    sys.path.insert(0, str(extra_sys_path))
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None and spec.loader is not None, f"could not load spec for {path}"
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(extra_sys_path))


bn = _load_module("bootstrap_names_for_skeptic_ready_test", BOOTSTRAP_NAMES_SCRIPT, SCRIPTS_DIR)
occ = _load_module("occ_index_for_skeptic_ready_test", OCC_INDEX_SCRIPT, SCRIPTS_DIR)
sr = _load_module("skeptic_ready_under_test", SKEPTIC_READY_SCRIPT, SCRIPTS_DIR)


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def write_particle_config(languages_dir: Path, filename: str = "test.json", *,
                           particles=(), stopwords=(), has_elision=False,
                           elision_re=None, name_inventory=None) -> str:
    languages_dir.mkdir(parents=True, exist_ok=True)
    doc = {
        "PARTICLES": list(particles),
        "STOPWORDS": list(stopwords),
        "has_elision": has_elision,
        "ELISION_RE": elision_re,
    }
    if name_inventory is not None:
        doc["name_inventory"] = list(name_inventory)
    (languages_dir / filename).write_text(json.dumps(doc), encoding="utf-8")
    return filename


def block(text, seg="seg01", block_id="PARA:seg01:0001"):
    return block_id, {"seg": seg, "plain_text": text}


def make_manifest(*blocks_kv) -> dict:
    return {"blocks": dict(blocks_kv)}


def evidence_for(source_form, block_id, seg, text, lang, index=0) -> dict:
    """A schema-shaped, REAL byte+matcher-verifiable evidence dict (context
    = whole block, per occ_index.py's own convention)."""
    records = occ.build_occurrence_records(source_form, block_id, seg, text, lang)
    assert records, f"no production occurrence of {source_form!r} in block {block_id!r} under this lang config"
    rec = records[index]
    return {
        "block": rec["block"], "seg": rec["seg"],
        "char_start": rec["char_start"], "char_end": rec["char_end"],
        "context_start": rec["context_start"], "context_end": rec["context_end"],
        "sha256": rec["context_sha256"],
    }


def aid(source_form: str) -> str:
    return sr.compute_assignment_id(source_form)


def write_json(path: Path, doc) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")


def adverse_record(source_form, evidence, assignment_id=None, rationale="contradicts identity"):
    return {
        "assignment_id": assignment_id or aid(source_form),
        "source_form": source_form,
        "verdict": "adverse",
        "rationale": rationale,
        "evidence": evidence,
    }


def insufficient_record(source_form, assignment_id=None, rationale="not enough context"):
    return {
        "assignment_id": assignment_id or aid(source_form),
        "source_form": source_form,
        "verdict": "insufficient_window",
        "rationale": rationale,
    }


def propose_split_record(source_form, referents, assignment_id=None, rationale="looks like 2+ referents"):
    return {
        "assignment_id": assignment_id or aid(source_form),
        "source_form": source_form,
        "verdict": "propose_split",
        "rationale": rationale,
        "referents": referents,
    }


def window_for(evidence: dict) -> dict:
    return {
        "block": evidence["block"], "seg": evidence["seg"],
        "char_start": evidence["char_start"], "char_end": evidence["char_end"],
    }


def make_assignment(source_form, windows, risk_classes=("high_dispersion",), batch_index=0):
    return {
        "assignment_id": aid(source_form),
        "source_form": source_form,
        "canonical_target_form": source_form,
        "risk_classes": list(risk_classes),
        "windows": windows,
        "windows_truncated": False,
        "batch_index": batch_index,
    }


def make_aggregate_manifest(run_id, assignments) -> dict:
    return {
        "schema_version": 1, "run_id": run_id,
        "input_digest": "0" * 64, "producer_input_digest": "1" * 64,
        "batch_count": 1, "assignments": assignments,
    }


# ---------------------------------------------------------------------------
# --validate-fragment
# ---------------------------------------------------------------------------

def test_validate_fragment_accepts_well_formed_adverse(tmp_path):
    lang_dir = tmp_path / "languages"
    particle_config = write_particle_config(lang_dir)
    lang = bn.load_language_config(particle_config, languages_dir=lang_dir)

    text = "Jean met Paul in the market. Jean smiled."
    block_id, blk = block(text)
    manifest_path = tmp_path / "manifest.json"
    write_json(manifest_path, make_manifest((block_id, blk)))

    evidence = evidence_for("Jean", block_id, "seg01", text, lang)
    frag_path = tmp_path / "triage_0.json"
    write_json(frag_path, {
        "schema_version": 1, "run_id": "run-1",
        "records": [adverse_record("Jean", evidence)],
    })

    result = sr.run_validate_fragment(frag_path, manifest_path, particle_config, languages_dir=lang_dir)
    assert result == {"success": True, "records": 1, "coerced": 0}

    on_disk = json.loads(frag_path.read_text(encoding="utf-8"))
    assert on_disk["records"][0]["verdict"] == "adverse"
    assert on_disk["records"][0]["evidence_coverage"] == {"cited": 1, "verified": 1}


def test_validate_fragment_accepts_when_coverage_matches_exactly(tmp_path):
    lang_dir = tmp_path / "languages"
    particle_config = write_particle_config(lang_dir)
    text = "Jean walked home."
    block_id, blk = block(text)
    manifest_path = tmp_path / "manifest.json"
    write_json(manifest_path, make_manifest((block_id, blk)))

    frag_path = tmp_path / "triage_0.json"
    write_json(frag_path, {"schema_version": 1, "run_id": "run-1", "records": [insufficient_record("Jean")]})
    expect_path = tmp_path / "assignments_0.json"
    write_json(expect_path, [aid("Jean")])

    result = sr.run_validate_fragment(
        frag_path, manifest_path, particle_config, languages_dir=lang_dir,
        expect_assignments_file=expect_path,
    )
    assert result["success"] is True


def test_validate_fragment_rejects_confirmed_ok_shaped_field(tmp_path):
    """MUTATION this guards: if skeptic-triage.schema.json's record object
    ever set additionalProperties to true (or dropped it), this smuggled
    `confirmed_ok` field would pass schema validation silently -- the whole
    adverse-only safety invariant (no verdict/field can express a
    confirmation) would then be defeated by a single agent bug."""
    lang_dir = tmp_path / "languages"
    particle_config = write_particle_config(lang_dir)
    text = "Jean walked home."
    block_id, blk = block(text)
    manifest_path = tmp_path / "manifest.json"
    write_json(manifest_path, make_manifest((block_id, blk)))

    frag_path = tmp_path / "triage_0.json"
    rec = insufficient_record("Jean")
    rec["confirmed_ok"] = True
    original_doc = {"schema_version": 1, "run_id": "run-1", "records": [rec]}
    write_json(frag_path, original_doc)

    with pytest.raises(sr.SkepticReadyError) as excinfo:
        sr.run_validate_fragment(frag_path, manifest_path, particle_config, languages_dir=lang_dir)
    assert "schema validation" in str(excinfo.value)
    # A hard reject never (re)writes the fragment -- unlike a coercion,
    # which does.
    assert json.loads(frag_path.read_text(encoding="utf-8")) == original_doc


def test_validate_fragment_rejects_propose_split_with_fewer_than_2_referents_present():
    """referents with < 2 items IS a schema-level reject (minItems:2), never
    a coercion target -- only a totally ABSENT referents key is a
    procedural (coercible) gap. Exercised at the schema-validator level
    directly since it needs no manifest/evidence at all."""
    schema_path = ASSETS_DIR / "schemas" / "skeptic-triage.schema.json"
    import jsonschema
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(schema)
    doc = {
        "schema_version": 1, "run_id": "run-1",
        "records": [{
            "assignment_id": aid("Jean"), "source_form": "Jean",
            "verdict": "propose_split", "rationale": "x",
            "referents": [{"disambiguator": "only one", "evidence": {
                "block": "b", "seg": None, "char_start": 0, "char_end": 1,
                "context_start": 0, "context_end": 1, "sha256": "0" * 64,
            }}],
        }],
    }
    errors = list(validator.iter_errors(doc))
    assert errors, "a 1-item referents[] array must fail schema validation (minItems:2)"


def test_validate_fragment_coerces_mismatched_evidence_to_insufficient_window(tmp_path):
    """MUTATION this guards: dropping evidence_verify's matcher-
    authentication check (or skipping the evidence-adapter re-verification
    here entirely) would let a citation whose offsets actually span a
    DIFFERENT name survive as `adverse`."""
    lang_dir = tmp_path / "languages"
    particle_config = write_particle_config(lang_dir)
    lang = bn.load_language_config(particle_config, languages_dir=lang_dir)
    text = "Jean met Paul in the market."
    block_id, blk = block(text)
    manifest_path = tmp_path / "manifest.json"
    write_json(manifest_path, make_manifest((block_id, blk)))

    # Byte-valid, in-bounds, correctly-hashed -- but its offsets span "Paul",
    # not "Jean": matcher-authentication must reject it even though bytes/
    # hash/bounds all check out.
    paul_evidence = evidence_for("Paul", block_id, "seg01", text, lang)

    frag_path = tmp_path / "triage_0.json"
    write_json(frag_path, {
        "schema_version": 1, "run_id": "run-1",
        "records": [adverse_record("Jean", paul_evidence)],
    })

    result = sr.run_validate_fragment(frag_path, manifest_path, particle_config, languages_dir=lang_dir)
    assert result == {"success": True, "records": 1, "coerced": 1}

    on_disk = json.loads(frag_path.read_text(encoding="utf-8"))
    rec = on_disk["records"][0]
    assert rec["verdict"] == "insufficient_window"
    assert "evidence" not in rec
    assert any("coerced_insufficient_window" in n for n in rec["notes"])


def test_validate_fragment_rejects_assignment_id_token_mismatch(tmp_path):
    """MUTATION this guards: skipping the sha256(NFC(source_form)) ==
    assignment_id recompute would let a fragment file in an inconsistent
    state (source_form typo'd against a stale assignment_id) merge as if
    its join key were trustworthy."""
    lang_dir = tmp_path / "languages"
    particle_config = write_particle_config(lang_dir)
    text = "Jean walked home."
    block_id, blk = block(text)
    manifest_path = tmp_path / "manifest.json"
    write_json(manifest_path, make_manifest((block_id, blk)))

    frag_path = tmp_path / "triage_0.json"
    rec = insufficient_record("Jean", assignment_id="0" * 64)
    write_json(frag_path, {"schema_version": 1, "run_id": "run-1", "records": [rec]})

    with pytest.raises(sr.SkepticReadyError) as excinfo:
        sr.run_validate_fragment(frag_path, manifest_path, particle_config, languages_dir=lang_dir)
    assert "token mismatch" in str(excinfo.value)


def test_validate_fragment_rejects_coverage_mismatch(tmp_path):
    """MUTATION this guards: omitting the --expect-assignments-file check
    would let a batch fragment silently drop an assigned entity (never
    examined, never reported) while still reporting success."""
    lang_dir = tmp_path / "languages"
    particle_config = write_particle_config(lang_dir)
    text = "Jean walked home."
    block_id, blk = block(text)
    manifest_path = tmp_path / "manifest.json"
    write_json(manifest_path, make_manifest((block_id, blk)))

    frag_path = tmp_path / "triage_0.json"
    write_json(frag_path, {
        "schema_version": 1, "run_id": "run-1",
        "records": [insufficient_record("Jean")],
    })
    # This batch was actually assigned BOTH Jean and Marie.
    expect_path = tmp_path / "assignments_0.json"
    write_json(expect_path, [aid("Jean"), aid("Marie")])

    with pytest.raises(sr.SkepticReadyError) as excinfo:
        sr.run_validate_fragment(
            frag_path, manifest_path, particle_config, languages_dir=lang_dir,
            expect_assignments_file=expect_path,
        )
    assert "coverage mismatch" in str(excinfo.value)
    assert any("missing" in item for item in excinfo.value.offending)


def test_validate_fragment_byte_valid_but_semantically_irrelevant_quote_still_passes(tmp_path):
    """Documents auth != sufficiency (RFC #215 Phase 2 contract): a REAL
    production occurrence of the cited source_form is accepted as
    'verified' regardless of whether the surrounding prose actually
    contradicts anything -- evidence_verify.py only ever checks byte +
    matcher authenticity, never semantic relevance."""
    lang_dir = tmp_path / "languages"
    particle_config = write_particle_config(lang_dir)
    lang = bn.load_language_config(particle_config, languages_dir=lang_dir)
    text = "Jean bought bread at the bakery."  # utterly unremarkable mention
    block_id, blk = block(text)
    manifest_path = tmp_path / "manifest.json"
    write_json(manifest_path, make_manifest((block_id, blk)))

    evidence = evidence_for("Jean", block_id, "seg01", text, lang)
    frag_path = tmp_path / "triage_0.json"
    write_json(frag_path, {
        "schema_version": 1, "run_id": "run-1",
        "records": [adverse_record("Jean", evidence, rationale="Jean is claimed to be elsewhere (spurious)")],
    })

    result = sr.run_validate_fragment(frag_path, manifest_path, particle_config, languages_dir=lang_dir)
    assert result["coerced"] == 0
    on_disk = json.loads(frag_path.read_text(encoding="utf-8"))
    assert on_disk["records"][0]["verdict"] == "adverse"


def test_validate_fragment_propose_split_downgrades_when_fewer_than_2_referents_verify(tmp_path):
    lang_dir = tmp_path / "languages"
    particle_config = write_particle_config(lang_dir)
    lang = bn.load_language_config(particle_config, languages_dir=lang_dir)
    text = "Jean met Paul at the market."
    block_id, blk = block(text)
    manifest_path = tmp_path / "manifest.json"
    write_json(manifest_path, make_manifest((block_id, blk)))

    jean_evidence = evidence_for("Jean", block_id, "seg01", text, lang)
    mismatched_evidence = evidence_for("Paul", block_id, "seg01", text, lang)  # wrong span for "Jean"
    referents = [
        {"disambiguator": "Jean the baker", "evidence": jean_evidence},
        {"disambiguator": "Jean the soldier", "evidence": mismatched_evidence},
    ]
    frag_path = tmp_path / "triage_0.json"
    write_json(frag_path, {
        "schema_version": 1, "run_id": "run-1",
        "records": [propose_split_record("Jean", referents)],
    })

    result = sr.run_validate_fragment(frag_path, manifest_path, particle_config, languages_dir=lang_dir)
    assert result["coerced"] == 1
    on_disk = json.loads(frag_path.read_text(encoding="utf-8"))
    assert on_disk["records"][0]["verdict"] == "insufficient_window"


def test_validate_fragment_propose_split_survives_with_partial_referent_coverage(tmp_path):
    """>=2 verified referents survive even when a 3rd fails to verify -- the
    failed one is DROPPED, never the whole record downgraded, and
    evidence_coverage records the partial count (partial != invalid;
    skeptic_report.py renders it explicitly as partial)."""
    lang_dir = tmp_path / "languages"
    particle_config = write_particle_config(lang_dir)
    lang = bn.load_language_config(particle_config, languages_dir=lang_dir)
    text = "Jean met Jean and also Jean at the market."  # 3 occurrences
    block_id, blk = block(text)
    manifest_path = tmp_path / "manifest.json"
    write_json(manifest_path, make_manifest((block_id, blk)))

    ev0 = evidence_for("Jean", block_id, "seg01", text, lang, index=0)
    ev1 = evidence_for("Jean", block_id, "seg01", text, lang, index=1)
    bad_ev = dict(evidence_for("Jean", block_id, "seg01", text, lang, index=2))
    bad_ev["char_start"] += 1
    bad_ev["char_end"] += 1  # shifted off the real production span

    referents = [
        {"disambiguator": "Jean A", "evidence": ev0},
        {"disambiguator": "Jean B", "evidence": ev1},
        {"disambiguator": "Jean C", "evidence": bad_ev},
    ]
    frag_path = tmp_path / "triage_0.json"
    write_json(frag_path, {
        "schema_version": 1, "run_id": "run-1",
        "records": [propose_split_record("Jean", referents)],
    })

    result = sr.run_validate_fragment(frag_path, manifest_path, particle_config, languages_dir=lang_dir)
    on_disk = json.loads(frag_path.read_text(encoding="utf-8"))
    rec = on_disk["records"][0]
    assert rec["verdict"] == "propose_split"
    assert len(rec["referents"]) == 2
    assert rec["evidence_coverage"] == {"cited": 3, "verified": 2}


# ---------------------------------------------------------------------------
# --merge-fragments
# ---------------------------------------------------------------------------

def test_merge_fragments_is_deterministic_regardless_of_fragment_read_order(tmp_path):
    """MUTATION this guards: sorting records only WITHIN each fragment (or
    not sorting the merged list at all, just concatenating in glob order)
    would make this test fail the moment two runs disagree on which
    physical fragment file happened to hold which record."""
    run_dir = tmp_path / "runs" / "run-1"
    run_dir.mkdir(parents=True)
    rec_a = insufficient_record("Alice")
    rec_b = insufficient_record("Bob")

    write_json(run_dir / "triage_0.json", {"schema_version": 1, "run_id": "run-1", "records": [rec_a]})
    write_json(run_dir / "triage_1.json", {"schema_version": 1, "run_id": "run-1", "records": [rec_b]})
    out1 = tmp_path / "merged1.json"
    result1 = sr.run_merge_fragments(run_dir, out1)

    # Swap which fragment holds which record.
    write_json(run_dir / "triage_0.json", {"schema_version": 1, "run_id": "run-1", "records": [rec_b]})
    write_json(run_dir / "triage_1.json", {"schema_version": 1, "run_id": "run-1", "records": [rec_a]})
    out2 = tmp_path / "merged2.json"
    result2 = sr.run_merge_fragments(run_dir, out2)

    assert out1.read_bytes() == out2.read_bytes()
    assert result1["records"] == 2
    assert result2["records"] == 2
    merged = json.loads(out1.read_text(encoding="utf-8"))
    assert [r["source_form"] for r in merged["records"]] == ["Alice", "Bob"]


def test_merge_fragments_writes_atomically_no_leftover_tmp_file(tmp_path):
    run_dir = tmp_path / "runs" / "run-1"
    run_dir.mkdir(parents=True)
    write_json(run_dir / "triage_0.json", {"schema_version": 1, "run_id": "run-1", "records": [insufficient_record("Alice")]})
    out = tmp_path / "skeptic_triage.json"
    sr.run_merge_fragments(run_dir, out)
    assert out.is_file()
    leftovers = [p for p in tmp_path.rglob("*") if p.name.startswith(".") and "tmp" in p.name]
    assert leftovers == []


def test_merge_fragments_raises_on_schema_invalid_fragment(tmp_path):
    run_dir = tmp_path / "runs" / "run-1"
    run_dir.mkdir(parents=True)
    write_json(run_dir / "triage_0.json", {"schema_version": 1, "run_id": "run-1", "records": [{"assignment_id": "x"}]})
    with pytest.raises(sr.SkepticReadyError):
        sr.run_merge_fragments(run_dir, tmp_path / "out.json")


def test_merge_fragments_empty_run_dir_produces_schema_valid_empty_triage(tmp_path):
    run_dir = tmp_path / "runs" / "run-empty"
    run_dir.mkdir(parents=True)
    out = tmp_path / "out.json"
    result = sr.run_merge_fragments(run_dir, out)
    assert result["records"] == 0
    assert json.loads(out.read_text(encoding="utf-8")) == {
        "schema_version": 1, "run_id": "run-empty", "records": [],
    }


# ---------------------------------------------------------------------------
# --verify-merged
# ---------------------------------------------------------------------------

def test_validate_fragment_tolerates_explicit_but_absent_senses_path(tmp_path):
    """IMPORTANT regression (codex review): skeptic-pass-wf.template.js's
    checkCommand() ALWAYS passes --canon/--senses-path explicitly, pointing
    at the project's canonical paths, for EVERY project regardless of
    whether it ever adopted homonym-split senses -- a documented normal
    'no sidecar yet' state. Genuinely nonexistent, real `Path` objects here
    (never a canned mock) -- this must succeed exactly like the implicit-
    default-absent case, not hard-error the way an EXPLICIT missing
    --senses-path does in canon_adjudication_audit.py's own (human-facing,
    typo-protecting) CLI."""
    lang_dir = tmp_path / "languages"
    particle_config = write_particle_config(lang_dir)
    lang = bn.load_language_config(particle_config, languages_dir=lang_dir)
    text = "Jean walked home."
    block_id, blk = block(text)
    manifest_path = tmp_path / "manifest.json"
    write_json(manifest_path, make_manifest((block_id, blk)))

    jean_evidence = evidence_for("Jean", block_id, "seg01", text, lang)
    fragment_path = tmp_path / "triage_0.json"
    write_json(fragment_path, {
        "schema_version": 1, "run_id": "run-1",
        "records": [adverse_record("Jean", jean_evidence)],
    })

    missing_canon_path = tmp_path / "canon.json"
    missing_senses_path = tmp_path / "canon_senses.json"
    assert not missing_canon_path.is_file() and not missing_senses_path.is_file()

    result = sr.run_validate_fragment(
        fragment_path, manifest_path, particle_config, languages_dir=lang_dir,
        canon_path=missing_canon_path, senses_path=missing_senses_path,
    )
    # Mutation: reinstating allow_absent_senses=(senses_path is None) (an
    # EXPLICIT but absent path is a hard error) would raise SkepticReadyError
    # here instead of returning success.
    assert result["success"] is True


def test_verify_merged_tolerates_explicit_but_absent_senses_path(tmp_path):
    """Same regression, --verify-merged side -- verifyMergedPrompt() also
    always passes both flags explicitly. A hard error here is worse than
    --validate-fragment's: it makes skeptic_ready.py print the generic
    {"success": false, "error": ...} shape instead of the
    {"verified", "missing", "frozen_input_mismatch"} shape
    SKEPTIC_VERIFY_SCHEMA requires, breaking the relay contract entirely."""
    lang_dir = tmp_path / "languages"
    particle_config = write_particle_config(lang_dir)
    text = "Jean walked home."
    block_id, blk = block(text)
    manifest_path = tmp_path / "manifest.json"
    write_json(manifest_path, make_manifest((block_id, blk)))

    rec = insufficient_record("Jean")
    triage_path = tmp_path / "skeptic_triage.json"
    write_json(triage_path, {"schema_version": 1, "run_id": "run-1", "records": [rec]})
    aggregate_path = tmp_path / "assignments.json"
    write_json(aggregate_path, make_aggregate_manifest("run-1", [make_assignment("Jean", [])]))

    missing_canon_path = tmp_path / "canon.json"
    missing_senses_path = tmp_path / "canon_senses.json"
    assert not missing_canon_path.is_file() and not missing_senses_path.is_file()

    result = sr.run_verify_merged(
        triage_path, aggregate_path, manifest_path, particle_config, languages_dir=lang_dir,
        canon_path=missing_canon_path, senses_path=missing_senses_path,
    )
    assert result == {"verified": True, "missing": [], "frozen_input_mismatch": False}


def test_verify_merged_succeeds_on_clean_chain(tmp_path):
    lang_dir = tmp_path / "languages"
    particle_config = write_particle_config(lang_dir)
    lang = bn.load_language_config(particle_config, languages_dir=lang_dir)
    text = "Jean met Marie at the market."
    block_id, blk = block(text)
    manifest_path = tmp_path / "manifest.json"
    write_json(manifest_path, make_manifest((block_id, blk)))

    jean_evidence = evidence_for("Jean", block_id, "seg01", text, lang)
    triage_path = tmp_path / "skeptic_triage.json"
    write_json(triage_path, {
        "schema_version": 1, "run_id": "run-1",
        "records": [adverse_record("Jean", jean_evidence)],
    })
    aggregate_path = tmp_path / "assignments.json"
    write_json(aggregate_path, make_aggregate_manifest("run-1", [make_assignment("Jean", [window_for(jean_evidence)])]))

    result = sr.run_verify_merged(triage_path, aggregate_path, manifest_path, particle_config, languages_dir=lang_dir)
    assert result == {"verified": True, "missing": [], "frozen_input_mismatch": False}


def test_verify_merged_fails_on_missing_assigned_entity(tmp_path):
    """MUTATION this guards: computing coverage from the per-batch fragment
    files (instead of the fresh-read MERGED triage) would let a merge that
    silently dropped one entity's record pass as verified."""
    lang_dir = tmp_path / "languages"
    particle_config = write_particle_config(lang_dir)
    lang = bn.load_language_config(particle_config, languages_dir=lang_dir)
    text = "Jean met Marie at the market."
    block_id, blk = block(text)
    manifest_path = tmp_path / "manifest.json"
    write_json(manifest_path, make_manifest((block_id, blk)))

    jean_evidence = evidence_for("Jean", block_id, "seg01", text, lang)
    marie_evidence = evidence_for("Marie", block_id, "seg01", text, lang)
    triage_path = tmp_path / "skeptic_triage.json"
    write_json(triage_path, {
        "schema_version": 1, "run_id": "run-1",
        "records": [adverse_record("Jean", jean_evidence)],  # Marie's record is MISSING
    })
    aggregate_path = tmp_path / "assignments.json"
    write_json(aggregate_path, make_aggregate_manifest("run-1", [
        make_assignment("Jean", [window_for(jean_evidence)]),
        make_assignment("Marie", [window_for(marie_evidence)]),
    ]))

    result = sr.run_verify_merged(triage_path, aggregate_path, manifest_path, particle_config, languages_dir=lang_dir)
    assert result["verified"] is False
    assert any("coverage gap" in m for m in result["missing"])
    # An ordinary (non-frozen-input) failure must NOT set frozen_input_mismatch
    # -- a caller gates HALT vs advisory on this field specifically, so a
    # false positive here would wrongly halt the whole pipeline over a plain
    # coverage gap.
    assert result["frozen_input_mismatch"] is False


def test_verify_merged_fails_on_post_merge_tampered_evidence_offset(tmp_path):
    """MUTATION this guards: trusting the merged triage's own evidence
    without re-running verify_evidence fresh would let a hand-corrupted
    (or race-tampered) skeptic_triage.json pass as verified. Post fix M2(b),
    this is caught by the fresh re-coercion check (the SAME machinery
    --validate-fragment applies): a tampered offset no longer byte-verifies,
    so a fresh _coerce_record() call downgrades it to insufficient_window,
    which no longer matches the stored `adverse` verdict."""
    lang_dir = tmp_path / "languages"
    particle_config = write_particle_config(lang_dir)
    lang = bn.load_language_config(particle_config, languages_dir=lang_dir)
    text = "Jean met Paul at the market square."
    block_id, blk = block(text)
    manifest_path = tmp_path / "manifest.json"
    write_json(manifest_path, make_manifest((block_id, blk)))

    jean_evidence = evidence_for("Jean", block_id, "seg01", text, lang)
    triage_path = tmp_path / "skeptic_triage.json"
    write_json(triage_path, {
        "schema_version": 1, "run_id": "run-1",
        "records": [adverse_record("Jean", jean_evidence)],
    })
    aggregate_path = tmp_path / "assignments.json"
    write_json(aggregate_path, make_aggregate_manifest("run-1", [make_assignment("Jean", [window_for(jean_evidence)])]))

    # Sanity: verifies before tampering.
    assert sr.run_verify_merged(triage_path, aggregate_path, manifest_path, particle_config, languages_dir=lang_dir)["verified"] is True

    tampered = json.loads(triage_path.read_text(encoding="utf-8"))
    tampered["records"][0]["evidence"]["char_start"] += 1
    tampered["records"][0]["evidence"]["char_end"] += 1
    write_json(triage_path, tampered)

    result = sr.run_verify_merged(triage_path, aggregate_path, manifest_path, particle_config, languages_dir=lang_dir)
    assert result["verified"] is False
    assert any("does not survive fresh re-verification" in m and "evidence_unverified" in m for m in result["missing"])


def test_verify_merged_fails_on_schema_invalid_triage(tmp_path):
    lang_dir = tmp_path / "languages"
    particle_config = write_particle_config(lang_dir)
    text = "Jean walked home."
    block_id, blk = block(text)
    manifest_path = tmp_path / "manifest.json"
    write_json(manifest_path, make_manifest((block_id, blk)))

    triage_path = tmp_path / "skeptic_triage.json"
    write_json(triage_path, {"schema_version": 1, "run_id": "run-1", "records": [{"assignment_id": "bad"}]})
    aggregate_path = tmp_path / "assignments.json"
    write_json(aggregate_path, make_aggregate_manifest("run-1", []))

    result = sr.run_verify_merged(triage_path, aggregate_path, manifest_path, particle_config, languages_dir=lang_dir)
    assert result["verified"] is False
    assert any("schema validation" in m for m in result["missing"])


# ---------------------------------------------------------------------------
# --verify-merged rigor parity with --validate-fragment (codex fix M2) +
# window-scoping (fix M3) + frozen-input tamper detection (fix H1
# mitigation, verifier half). A codex adversarial review proved
# --verify-merged was WEAKER than --validate-fragment: a direct probe
# returned verified:true for an evidence-free adverse, a mismatched
# source_form, and duplicate records -- none of which the checks above this
# section ever caught.
# ---------------------------------------------------------------------------

def test_verify_merged_fails_on_evidence_free_adverse(tmp_path):
    """RED before fix M2(b): `evidence` is OPTIONAL in skeptic-triage
    .schema.json, so an `adverse` record with NO evidence key at all is
    schema-valid -- the OLD per-record loop only ever inspected evidence
    when the `evidence` key was actually present, so this slipped through
    as verified:true. Fresh re-coercion (the SAME machinery
    --validate-fragment applies) downgrades an evidence-free adverse to
    insufficient_window, which no longer matches the stored verdict."""
    lang_dir = tmp_path / "languages"
    particle_config = write_particle_config(lang_dir)
    text = "Jean walked home."
    block_id, blk = block(text)
    manifest_path = tmp_path / "manifest.json"
    write_json(manifest_path, make_manifest((block_id, blk)))

    rec = {
        "assignment_id": aid("Jean"), "source_form": "Jean",
        "verdict": "adverse", "rationale": "claims a contradiction but cites nothing",
    }
    triage_path = tmp_path / "skeptic_triage.json"
    write_json(triage_path, {"schema_version": 1, "run_id": "run-1", "records": [rec]})
    aggregate_path = tmp_path / "assignments.json"
    write_json(aggregate_path, make_aggregate_manifest("run-1", [make_assignment("Jean", [])]))

    result = sr.run_verify_merged(triage_path, aggregate_path, manifest_path, particle_config, languages_dir=lang_dir)
    assert result["verified"] is False
    assert any("does not survive fresh re-verification" in m for m in result["missing"])


def test_verify_merged_fails_on_token_mismatch(tmp_path):
    """RED before fix M2(a): --verify-merged never recomputed
    sha256(NFC(source_form)) against the merged record's own assignment_id
    -- only --validate-fragment did. A record whose join key is
    self-inconsistent by merge time (hand-corrupted or race-tampered
    skeptic_triage.json) slipped through."""
    lang_dir = tmp_path / "languages"
    particle_config = write_particle_config(lang_dir)
    text = "Jean walked home."
    block_id, blk = block(text)
    manifest_path = tmp_path / "manifest.json"
    write_json(manifest_path, make_manifest((block_id, blk)))

    bad_id = "0" * 64  # deliberately NOT sha256(NFC("Jean"))
    rec = insufficient_record("Jean", assignment_id=bad_id)
    triage_path = tmp_path / "skeptic_triage.json"
    write_json(triage_path, {"schema_version": 1, "run_id": "run-1", "records": [rec]})
    assignment = make_assignment("Jean", [])
    assignment["assignment_id"] = bad_id  # aggregate agrees on the (wrong) join key
    aggregate_path = tmp_path / "assignments.json"
    write_json(aggregate_path, make_aggregate_manifest("run-1", [assignment]))

    result = sr.run_verify_merged(triage_path, aggregate_path, manifest_path, particle_config, languages_dir=lang_dir)
    assert result["verified"] is False
    assert any("!= sha256(NFC(source_form))=" in m for m in result["missing"])


def test_verify_merged_fails_on_run_id_mismatch(tmp_path):
    """RED before fix M2(c): --verify-merged never bound the merged
    triage's own run_id to the aggregate assignment manifest's run_id -- a
    triage document belonging to an entirely different (or stale) run could
    still be accepted as long as its assignment_id coverage happened to
    line up."""
    lang_dir = tmp_path / "languages"
    particle_config = write_particle_config(lang_dir)
    lang = bn.load_language_config(particle_config, languages_dir=lang_dir)
    text = "Jean met Marie at the market."
    block_id, blk = block(text)
    manifest_path = tmp_path / "manifest.json"
    write_json(manifest_path, make_manifest((block_id, blk)))
    jean_evidence = evidence_for("Jean", block_id, "seg01", text, lang)

    triage_path = tmp_path / "skeptic_triage.json"
    write_json(triage_path, {
        "schema_version": 1, "run_id": "run-OTHER",
        "records": [adverse_record("Jean", jean_evidence)],
    })
    aggregate_path = tmp_path / "assignments.json"
    write_json(aggregate_path, make_aggregate_manifest("run-1", [make_assignment("Jean", [window_for(jean_evidence)])]))

    result = sr.run_verify_merged(triage_path, aggregate_path, manifest_path, particle_config, languages_dir=lang_dir)
    assert result["verified"] is False
    assert any("run_id" in m and "run-OTHER" in m and "run-1" in m for m in result["missing"])


def test_verify_merged_fails_on_source_form_not_matching_assignment(tmp_path):
    """RED before fix M2(d): a triage record's own source_form was never
    bound back to the aggregate assignment it joins to via assignment_id.
    Since assignment_id == sha256(NFC(source_form)), a record whose OWN
    token check passes cannot itself have a mismatched source_form -- the
    real gap this closes is a CORRUPTED aggregate manifest whose own
    assignment_id/source_form pairing disagrees with what it's supposed to
    mean (nothing recomputes that hash relation for the aggregate's own
    entries anywhere)."""
    lang_dir = tmp_path / "languages"
    particle_config = write_particle_config(lang_dir)
    lang = bn.load_language_config(particle_config, languages_dir=lang_dir)
    text = "Jean met Marie at the market."
    block_id, blk = block(text)
    manifest_path = tmp_path / "manifest.json"
    write_json(manifest_path, make_manifest((block_id, blk)))
    jean_evidence = evidence_for("Jean", block_id, "seg01", text, lang)

    triage_path = tmp_path / "skeptic_triage.json"
    write_json(triage_path, {
        "schema_version": 1, "run_id": "run-1",
        "records": [adverse_record("Jean", jean_evidence)],  # assignment_id = aid("Jean"), source_form "Jean"
    })
    # The aggregate's OWN entry for this SAME assignment_id claims a
    # DIFFERENT source_form -- a corrupted aggregate the record's own
    # token-consistency (fix a) cannot see, since that only ever recomputes
    # assignment_id from the RECORD's own source_form.
    bad_assignment = make_assignment("Jean", [window_for(jean_evidence)])
    bad_assignment["source_form"] = "Marie"
    aggregate_path = tmp_path / "assignments.json"
    write_json(aggregate_path, make_aggregate_manifest("run-1", [bad_assignment]))

    result = sr.run_verify_merged(triage_path, aggregate_path, manifest_path, particle_config, languages_dir=lang_dir)
    assert result["verified"] is False
    assert any("source_form" in m and "Marie" in m for m in result["missing"])


def test_verify_merged_fails_on_duplicate_triage_record(tmp_path):
    """RED before fix M2(e): the old coverage check was purely set-based
    (assigned_ids vs covered_ids), so TWO triage records for the SAME
    assigned assignment_id collapsed into one set member and passed as
    full coverage."""
    lang_dir = tmp_path / "languages"
    particle_config = write_particle_config(lang_dir)
    text = "Jean walked home."
    block_id, blk = block(text)
    manifest_path = tmp_path / "manifest.json"
    write_json(manifest_path, make_manifest((block_id, blk)))

    rec = insufficient_record("Jean")
    triage_path = tmp_path / "skeptic_triage.json"
    write_json(triage_path, {"schema_version": 1, "run_id": "run-1", "records": [rec, dict(rec)]})
    aggregate_path = tmp_path / "assignments.json"
    write_json(aggregate_path, make_aggregate_manifest("run-1", [make_assignment("Jean", [])]))

    result = sr.run_verify_merged(triage_path, aggregate_path, manifest_path, particle_config, languages_dir=lang_dir)
    assert result["verified"] is False
    assert any("2 triage records" in m for m in result["missing"])


def test_verify_merged_fails_on_evidence_outside_assigned_windows(tmp_path):
    """RED before fix M3: skeptic-assignment.schema.json's own windows[]
    description PROMISES 'a propose_split/adverse verdict's evidence
    offsets must fall inside one of these windows', but evidence_verify
    authenticates only against manifest.blocks{} as a whole -- a citation
    that is a REAL, byte-verifying, matcher-authenticated occurrence of the
    source_form, just from a block NOT among this entity's own assigned
    windows, passed every check that existed before this fix."""
    lang_dir = tmp_path / "languages"
    particle_config = write_particle_config(lang_dir)
    lang = bn.load_language_config(particle_config, languages_dir=lang_dir)
    text_a = "Jean walked home alone."
    text_b = "Jean also appears here, elsewhere."
    block_a_id, block_a = block(text_a, block_id="PARA:seg01:0001")
    block_b_id, block_b = block(text_b, seg="seg02", block_id="PARA:seg02:0002")
    manifest_path = tmp_path / "manifest.json"
    write_json(manifest_path, make_manifest((block_a_id, block_a), (block_b_id, block_b)))

    # This entity was assigned ONLY block A's window...
    evidence_in_a = evidence_for("Jean", block_a_id, "seg01", text_a, lang)
    # ...but the triage record cites a genuinely authentic occurrence in
    # block B instead -- a real, byte-verifying citation, just from the
    # wrong (unassigned) window.
    evidence_in_b = evidence_for("Jean", block_b_id, "seg02", text_b, lang)

    triage_path = tmp_path / "skeptic_triage.json"
    write_json(triage_path, {
        "schema_version": 1, "run_id": "run-1",
        "records": [adverse_record("Jean", evidence_in_b)],
    })
    aggregate_path = tmp_path / "assignments.json"
    write_json(aggregate_path, make_aggregate_manifest(
        "run-1", [make_assignment("Jean", [window_for(evidence_in_a)])],
    ))

    result = sr.run_verify_merged(triage_path, aggregate_path, manifest_path, particle_config, languages_dir=lang_dir)
    assert result["verified"] is False
    assert any("not among this assignment's own windows" in m for m in result["missing"])


def test_verify_merged_fails_on_canon_tamper(tmp_path):
    """RED before fix H1 mitigation (verifier half): --verify-merged never
    re-hashed canon.json against the aggregate manifest's own canon_sha256,
    so a skeptic agent that tampered the frozen canon mid-pass (source-text
    prompt injection) went completely undetected. Unmutated: passes."""
    lang_dir = tmp_path / "languages"
    particle_config = write_particle_config(lang_dir)
    text = "Jean walked home."
    block_id, blk = block(text)
    manifest_path = tmp_path / "manifest.json"
    write_json(manifest_path, make_manifest((block_id, blk)))

    canon_path = tmp_path / "canon.json"
    canon_path.write_text(json.dumps({"entries": {}}), encoding="utf-8")
    canon_sha256 = sr.compute_frozen_input_hash(canon_path)

    rec = insufficient_record("Jean")
    triage_path = tmp_path / "skeptic_triage.json"
    write_json(triage_path, {"schema_version": 1, "run_id": "run-1", "records": [rec]})
    aggregate_path = tmp_path / "assignments.json"
    write_json(aggregate_path, {
        **make_aggregate_manifest("run-1", [make_assignment("Jean", [])]),
        "canon_sha256": canon_sha256,
    })

    # Unmutated: passes clean.
    result = sr.run_verify_merged(
        triage_path, aggregate_path, manifest_path, particle_config, languages_dir=lang_dir, canon_path=canon_path,
    )
    assert result == {"verified": True, "missing": [], "frozen_input_mismatch": False}

    # Tamper: mutate canon.json on disk (simulated skeptic-agent injection).
    canon_path.write_text(json.dumps({"entries": {"INJECTED": {}}}), encoding="utf-8")
    result = sr.run_verify_merged(
        triage_path, aggregate_path, manifest_path, particle_config, languages_dir=lang_dir, canon_path=canon_path,
    )
    assert result["verified"] is False
    assert any("canon.json" in m and "tamper" in m for m in result["missing"])
    # P1 fix (review-bot #227): the mismatch must be surfaced DISTINCTLY
    # from an ordinary skeptic-pass failure, so a caller can HALT on it
    # specifically instead of treating it as merely advisory.
    assert result["frozen_input_mismatch"] is True


def test_verify_merged_fails_on_senses_tamper(tmp_path):
    """#243 H1 (third stamp): canon_senses.json joined canon.json/
    manifest.json as a THIRD frozen input once --verify-merged started
    parsing it to project the ambiguity-competitors universe -- the SAME
    tamper-tripwire mechanism as canon_sha256/manifest_sha256, mutated
    mid-pass must trip it exactly the same way."""
    lang_dir = tmp_path / "languages"
    particle_config = write_particle_config(lang_dir)
    text = "Jean walked home."
    block_id, blk = block(text)
    manifest_path = tmp_path / "manifest.json"
    write_json(manifest_path, make_manifest((block_id, blk)))

    senses_path = tmp_path / "canon_senses.json"
    senses_path.write_text(json.dumps({"schema_version": 1, "entries_by_source_form": {}}), encoding="utf-8")
    senses_sha256 = sr.compute_frozen_input_hash(senses_path)

    rec = insufficient_record("Jean")
    triage_path = tmp_path / "skeptic_triage.json"
    write_json(triage_path, {"schema_version": 1, "run_id": "run-1", "records": [rec]})
    aggregate_path = tmp_path / "assignments.json"
    write_json(aggregate_path, {
        **make_aggregate_manifest("run-1", [make_assignment("Jean", [])]),
        "senses_sha256": senses_sha256,
    })

    # Unmutated: passes clean.
    result = sr.run_verify_merged(
        triage_path, aggregate_path, manifest_path, particle_config,
        languages_dir=lang_dir, senses_path=senses_path,
    )
    assert result == {"verified": True, "missing": [], "frozen_input_mismatch": False}

    # Tamper: mutate canon_senses.json on disk (simulated skeptic-agent
    # injection, same threat model as canon.json/manifest.json) -- a
    # schema-VALID addition, so this exercises the H1 hash-tamper tripwire
    # itself, never the separate (also-fatal, but different) schema
    # validation path a malformed sidecar would hit instead.
    injected_evidence = {
        "block": "b1", "seg": "seg01", "char_start": 0, "char_end": 4,
        "context_start": 0, "context_end": 20, "sha256": "a" * 64,
    }
    injected_sense = lambda sid: {  # noqa: E731 -- local test-only shorthand
        "sense_id": sid, "disambiguator": sid, "index_scope": "narrative", "evidence": injected_evidence,
    }
    senses_path.write_text(json.dumps({
        "schema_version": 1,
        "entries_by_source_form": {"Injected": {"senses": [injected_sense("s1"), injected_sense("s2")]}},
    }), encoding="utf-8")
    result = sr.run_verify_merged(
        triage_path, aggregate_path, manifest_path, particle_config,
        languages_dir=lang_dir, senses_path=senses_path,
    )
    assert result["verified"] is False
    assert any("canon_senses.json" in m and "tamper" in m for m in result["missing"]), (
        "MUTATION CAUGHT: if senses_sha256 were not re-hashed/compared like "
        "canon_sha256/manifest_sha256, this sidecar tamper would go "
        "completely undetected"
    )
    assert result["frozen_input_mismatch"] is True


def test_verify_merged_fails_on_senses_deletion_after_stamping(tmp_path):
    """IMPORTANT regression (codex review): the H1 byte-level tamper checks
    must run BEFORE the parse-and-project step, so a DELETED sidecar (which
    can never be successfully parsed) still surfaces via
    frozen_input_mismatch -- not merely an ordinary advisory failure the
    template would treat as non-fatal (verify-failed) instead of HALT."""
    lang_dir = tmp_path / "languages"
    particle_config = write_particle_config(lang_dir)
    text = "Jean walked home."
    block_id, blk = block(text)
    manifest_path = tmp_path / "manifest.json"
    write_json(manifest_path, make_manifest((block_id, blk)))

    senses_path = tmp_path / "canon_senses.json"
    senses_path.write_text(json.dumps({"schema_version": 1, "entries_by_source_form": {}}), encoding="utf-8")
    senses_sha256 = sr.compute_frozen_input_hash(senses_path)

    rec = insufficient_record("Jean")
    triage_path = tmp_path / "skeptic_triage.json"
    write_json(triage_path, {"schema_version": 1, "run_id": "run-1", "records": [rec]})
    aggregate_path = tmp_path / "assignments.json"
    write_json(aggregate_path, {
        **make_aggregate_manifest("run-1", [make_assignment("Jean", [])]),
        "senses_sha256": senses_sha256,
    })

    # Unmutated: passes clean.
    result = sr.run_verify_merged(
        triage_path, aggregate_path, manifest_path, particle_config,
        languages_dir=lang_dir, senses_path=senses_path,
    )
    assert result == {"verified": True, "missing": [], "frozen_input_mismatch": False}

    # Tamper: DELETE the sidecar entirely (simulated skeptic-agent
    # injection) -- distinct from the mutation case above, which stays a
    # regular (parseable) file throughout.
    senses_path.unlink()
    result = sr.run_verify_merged(
        triage_path, aggregate_path, manifest_path, particle_config,
        languages_dir=lang_dir, senses_path=senses_path,
    )
    # Mutation: resolving competitors BEFORE the H1 byte-comparison loop
    # (the pre-fix ordering) would have this deletion tolerated silently by
    # _resolve_competitors' own absence-tolerance (finding 2's fix) and
    # never reach the byte comparison that would have caught it.
    assert result["verified"] is False
    assert any("canon_senses.json" in m and "tamper" in m for m in result["missing"])
    assert result["frozen_input_mismatch"] is True


def test_verify_merged_fails_on_senses_malformed_after_stamping_still_reports_shape(tmp_path):
    """IMPORTANT regression (codex review): tampering the sidecar into
    SCHEMA-INVALID form (never merely deleting or validly editing it) must
    ALSO surface via frozen_input_mismatch (a raw byte comparison, which
    does not care whether the bytes parse) -- and, either way, the function
    must still return the well-formed {"verified", "missing",
    "frozen_input_mismatch"} shape, never raise SkepticReadyError out of
    run_verify_merged entirely (which would make the caller's `except
    SkepticReadyError` branch print the DIFFERENT {"success": false,
    "error": ...} shape SKEPTIC_VERIFY_SCHEMA cannot accept)."""
    lang_dir = tmp_path / "languages"
    particle_config = write_particle_config(lang_dir)
    text = "Jean walked home."
    block_id, blk = block(text)
    manifest_path = tmp_path / "manifest.json"
    write_json(manifest_path, make_manifest((block_id, blk)))

    senses_path = tmp_path / "canon_senses.json"
    senses_path.write_text(json.dumps({"schema_version": 1, "entries_by_source_form": {}}), encoding="utf-8")
    senses_sha256 = sr.compute_frozen_input_hash(senses_path)

    rec = insufficient_record("Jean")
    triage_path = tmp_path / "skeptic_triage.json"
    write_json(triage_path, {"schema_version": 1, "run_id": "run-1", "records": [rec]})
    aggregate_path = tmp_path / "assignments.json"
    write_json(aggregate_path, {
        **make_aggregate_manifest("run-1", [make_assignment("Jean", [])]),
        "senses_sha256": senses_sha256,
    })

    # Tamper: overwrite with SCHEMA-INVALID content (a 1-sense record,
    # minItems:2 -- load_senses hard-rejects this at parse time).
    senses_path.write_text(json.dumps({
        "schema_version": 1,
        "entries_by_source_form": {"Injected": {"senses": [
            {"sense_id": "s1", "disambiguator": "only one", "index_scope": "narrative",
             "evidence": {"block": "b1", "seg": "seg01", "char_start": 0, "char_end": 4,
                          "context_start": 0, "context_end": 20, "sha256": "a" * 64}},
        ]}},
    }), encoding="utf-8")

    result = sr.run_verify_merged(
        triage_path, aggregate_path, manifest_path, particle_config,
        languages_dir=lang_dir, senses_path=senses_path,
    )
    # Mutation: letting _resolve_competitors' CanonSensesLoadError propagate
    # unguarded (never caught by run_verify_merged itself) would raise
    # SkepticReadyError straight through this call instead of returning a
    # dict -- this assertion would then error on `result["verified"]` with
    # an uncaught exception rather than a clean assertion failure.
    assert result["verified"] is False
    assert any("canon_senses.json" in m and "tamper" in m for m in result["missing"]), (
        "the byte-level H1 comparison must still fire even though the "
        "malformed content can never successfully parse"
    )
    assert result["frozen_input_mismatch"] is True
    # The parse failure itself is ALSO reported (belt-and-suspenders,
    # distinct message from the tamper one above) -- never silently
    # swallowed, just never allowed to crash the whole function.
    assert any("canon_senses.json error" in m for m in result["missing"])


# ---------------------------------------------------------------------------
# --check-frozen-inputs (codex round 2): the standalone H1 tripwire, exposed
# so the calling Workflow can run it at the "batches never became ready"
# decision point too -- --verify-merged never reaches that point at all.
# ---------------------------------------------------------------------------

def test_check_frozen_inputs_clean_reports_no_mismatch(tmp_path):
    canon_path = tmp_path / "canon.json"
    canon_path.write_text(json.dumps({"entries": {}}), encoding="utf-8")
    manifest_path = tmp_path / "manifest.json"
    write_json(manifest_path, make_manifest())
    senses_path = tmp_path / "canon_senses.json"
    senses_path.write_text(json.dumps({"schema_version": 1, "entries_by_source_form": {}}), encoding="utf-8")

    aggregate_path = tmp_path / "assignments.json"
    write_json(aggregate_path, {
        **make_aggregate_manifest("run-1", []),
        "canon_sha256": sr.compute_frozen_input_hash(canon_path),
        "manifest_sha256": sr.compute_frozen_input_hash(manifest_path),
        "senses_sha256": sr.compute_frozen_input_hash(senses_path),
    })

    result = sr.run_check_frozen_inputs(
        aggregate_path, canon_path=canon_path, manifest_path=manifest_path, senses_path=senses_path,
    )
    assert result == {"frozen_input_mismatch": False, "missing": []}


def test_check_frozen_inputs_detects_tamper_the_verify_merged_path_never_reaches(tmp_path):
    """The exact codex round-2 scenario: the sidecar becomes malformed
    AFTER stamping but BEFORE fragment validation -- a point
    run_verify_merged is never even called from, since the pipeline gives
    up on notReadyBatches before ever attempting merge+verify. This is what
    that decision point now calls instead of silently doing nothing."""
    canon_path = tmp_path / "canon.json"
    canon_path.write_text(json.dumps({"entries": {}}), encoding="utf-8")
    manifest_path = tmp_path / "manifest.json"
    write_json(manifest_path, make_manifest())
    senses_path = tmp_path / "canon_senses.json"
    senses_path.write_text(json.dumps({"schema_version": 1, "entries_by_source_form": {}}), encoding="utf-8")

    aggregate_path = tmp_path / "assignments.json"
    write_json(aggregate_path, {
        **make_aggregate_manifest("run-1", []),
        "canon_sha256": sr.compute_frozen_input_hash(canon_path),
        "manifest_sha256": sr.compute_frozen_input_hash(manifest_path),
        "senses_sha256": sr.compute_frozen_input_hash(senses_path),
    })

    # Tamper: sidecar overwritten with SCHEMA-INVALID content (never even
    # gets to "deleted" -- codex's own scenario framing), simulating a
    # skeptic-agent injection that happened before any fragment validated.
    senses_path.write_text(json.dumps({
        "schema_version": 1,
        "entries_by_source_form": {"Injected": {"senses": [
            {"sense_id": "s1", "disambiguator": "only one", "index_scope": "narrative",
             "evidence": {"block": "b1", "seg": "seg01", "char_start": 0, "char_end": 4,
                          "context_start": 0, "context_end": 20, "sha256": "a" * 64}},
        ]}},
    }), encoding="utf-8")

    result = sr.run_check_frozen_inputs(
        aggregate_path, canon_path=canon_path, manifest_path=manifest_path, senses_path=senses_path,
    )
    assert result["frozen_input_mismatch"] is True
    assert any("canon_senses.json" in m and "tamper" in m for m in result["missing"])


def test_check_frozen_inputs_tolerates_a_read_failure(tmp_path, monkeypatch):
    """Codex round 6 BLOCKER: frozen_input_check() used to read
    canon.json/canon_senses.json UNCONDITIONALLY, even though
    run_check_frozen_inputs discards both returned snapshots and has no
    downstream parser that could ever consume them. A transient read
    failure (a real I/O error, not absence -- codex's own repro forced
    one) therefore propagated raw out of run_check_frozen_inputs, breaking
    its own documented "never a crash" contract.
    test_check_frozen_inputs_tolerates_missing_aggregate_manifest/
    ..._malformed_aggregate_manifest above prove the AGGREGATE-unreadable
    half of that contract; this proves the canon/senses-unreadable half,
    which frozen_input_check()'s round-5 refactor accidentally regressed.

    Forces the failure via a monkeypatch on read_frozen_input_snapshot
    (rather than chmod, which is unreliable when tests run as root or in
    sandboxes that ignore permission bits) targeting canon_path
    specifically -- a genuine canon_sha256 stamp IS present (matches the
    real content), so the read is genuinely attempted, not skipped via the
    "no stamp -> no read" gate this same round also added to
    frozen_input_check()."""
    canon_path = tmp_path / "canon.json"
    canon_path.write_text(json.dumps({"entries": {}}), encoding="utf-8")
    manifest_path = tmp_path / "manifest.json"
    write_json(manifest_path, make_manifest())
    senses_path = tmp_path / "canon_senses.json"
    senses_path.write_text(json.dumps({"schema_version": 1, "entries_by_source_form": {}}), encoding="utf-8")

    aggregate_path = tmp_path / "assignments.json"
    write_json(aggregate_path, {
        **make_aggregate_manifest("run-1", []),
        "canon_sha256": sr.compute_frozen_input_hash(canon_path),
        "manifest_sha256": sr.compute_frozen_input_hash(manifest_path),
        "senses_sha256": sr.compute_frozen_input_hash(senses_path),
    })

    real_read_frozen_input_snapshot = sr.read_frozen_input_snapshot

    def _fail_on_canon(path):
        if Path(path) == canon_path:
            raise OSError("simulated transient read failure")
        return real_read_frozen_input_snapshot(path)

    monkeypatch.setattr(sr, "read_frozen_input_snapshot", _fail_on_canon)

    # MUTATION CAUGHT if this raises instead of returning: --check-frozen-inputs
    # exists specifically to keep answering when something else has already
    # gone wrong, and this mode never consumes canon/senses beyond the hash
    # comparison itself, so a read failure here should degrade this ONE
    # check, never crash the whole call.
    result = sr.run_check_frozen_inputs(
        aggregate_path, canon_path=canon_path, manifest_path=manifest_path, senses_path=senses_path,
    )
    assert result == {"frozen_input_mismatch": False, "missing": []}


def test_verify_merged_still_raises_on_the_same_read_failure(tmp_path, monkeypatch):
    """The mirror-image assertion for the OTHER caller of
    frozen_input_check(): run_verify_merged must NOT swallow the identical
    read failure the test above tolerates. Its own competitors universe is
    parsed from the SAME snapshot the H1 check reads -- degrading canon to
    an empty snapshot there would silently empty the competitors universe
    and let every ambiguous form sail through unflagged (fail-OPEN on the
    exact property this release makes fail-closed), so this caller passes
    tolerant_reads=False and the failure must propagate. Unlike the test
    above, this one was never broken by the round-5/6 refactors -- included
    to make the "same code, two callers, opposite correct answers" property
    an explicit, checked fact rather than an implicit one."""
    lang_dir = tmp_path / "languages"
    particle_config = write_particle_config(lang_dir)
    text = "Jean walked home."
    block_id, blk = block(text)
    manifest_path = tmp_path / "manifest.json"
    write_json(manifest_path, make_manifest((block_id, blk)))

    canon_path = tmp_path / "canon.json"
    canon_path.write_text(json.dumps({"entries": {}}), encoding="utf-8")
    canon_sha256 = sr.compute_frozen_input_hash(canon_path)

    rec = insufficient_record("Jean")
    triage_path = tmp_path / "skeptic_triage.json"
    write_json(triage_path, {"schema_version": 1, "run_id": "run-1", "records": [rec]})
    aggregate_path = tmp_path / "assignments.json"
    write_json(aggregate_path, {
        **make_aggregate_manifest("run-1", [make_assignment("Jean", [])]),
        "canon_sha256": canon_sha256,
    })

    real_read_frozen_input_snapshot = sr.read_frozen_input_snapshot

    def _fail_on_canon(path):
        if Path(path) == canon_path:
            raise OSError("simulated transient read failure")
        return real_read_frozen_input_snapshot(path)

    monkeypatch.setattr(sr, "read_frozen_input_snapshot", _fail_on_canon)

    with pytest.raises(OSError):
        sr.run_verify_merged(
            triage_path, aggregate_path, manifest_path, particle_config,
            languages_dir=lang_dir, canon_path=canon_path,
        )


def test_check_frozen_inputs_tolerates_missing_aggregate_manifest(tmp_path):
    """Nothing to compare against -- degrades to no-mismatch, never a
    crash, exactly like _frozen_input_tamper_reason's own "stamped hash
    absent -> skip" rule applied one level up (this mode's whole point is
    to keep answering even when something else is already broken)."""
    canon_path = tmp_path / "canon.json"
    manifest_path = tmp_path / "manifest.json"
    write_json(manifest_path, make_manifest())
    senses_path = tmp_path / "canon_senses.json"
    missing_aggregate_path = tmp_path / "assignments.json"
    assert not missing_aggregate_path.is_file()

    result = sr.run_check_frozen_inputs(
        missing_aggregate_path, canon_path=canon_path, manifest_path=manifest_path, senses_path=senses_path,
    )
    assert result == {"frozen_input_mismatch": False, "missing": []}


def test_check_frozen_inputs_tolerates_malformed_aggregate_manifest(tmp_path):
    canon_path = tmp_path / "canon.json"
    manifest_path = tmp_path / "manifest.json"
    write_json(manifest_path, make_manifest())
    senses_path = tmp_path / "canon_senses.json"
    aggregate_path = tmp_path / "assignments.json"
    aggregate_path.write_text("{not valid json", encoding="utf-8")

    result = sr.run_check_frozen_inputs(
        aggregate_path, canon_path=canon_path, manifest_path=manifest_path, senses_path=senses_path,
    )
    assert result == {"frozen_input_mismatch": False, "missing": []}


def test_check_frozen_inputs_cli_exit_code_reflects_mismatch(tmp_path):
    canon_path = tmp_path / "canon.json"
    canon_path.write_text(json.dumps({"entries": {}}), encoding="utf-8")
    manifest_path = tmp_path / "manifest.json"
    write_json(manifest_path, make_manifest())
    senses_path = tmp_path / "canon_senses.json"

    aggregate_path = tmp_path / "assignments.json"
    write_json(aggregate_path, {
        **make_aggregate_manifest("run-1", []),
        "canon_sha256": sr.compute_frozen_input_hash(canon_path),
        "manifest_sha256": sr.compute_frozen_input_hash(manifest_path),
        "senses_sha256": sr.compute_frozen_input_hash(senses_path),
    })

    argv_clean = [
        "--check-frozen-inputs", str(aggregate_path),
        "--canon", str(canon_path), "--manifest-path", str(manifest_path), "--senses-path", str(senses_path),
    ]
    assert sr.main(argv_clean) == 0

    canon_path.write_text(json.dumps({"entries": {"INJECTED": {}}}), encoding="utf-8")
    assert sr.main(argv_clean) == 1, "exit code must reflect frozen_input_mismatch, not just succeed unconditionally"


# ---------------------------------------------------------------------------
# #243 site 1 fix: a fold-colliding source_form's citation can no longer be
# trusted to belong to THIS entity rather than a colliding sibling, so
# _evidence_failure_reason/_coerce_record must fail it unconditionally --
# derived from the FULL --canon/--senses-path files, never anything local to
# one batch's own triage/assignment data (the whole reason a batch-local
# derivation would miss a cross-batch collision).
# ---------------------------------------------------------------------------

FOLD_FORM_A = "משה לייב"
FOLD_FORM_B = "מֹשֶׁה־לַיִיב"


def test_verify_merged_fails_closed_on_cross_batch_fold_collision(tmp_path):
    lang_dir = tmp_path / "languages"
    particle_config = write_particle_config(lang_dir, name_inventory=[FOLD_FORM_A])
    lang = bn.load_language_config(particle_config, languages_dir=lang_dir)
    text = f"ראה {FOLD_FORM_A} אתמול."
    block_id, blk = block(text)
    manifest_path = tmp_path / "manifest.json"
    write_json(manifest_path, make_manifest((block_id, blk)))

    form_a_evidence = evidence_for(FOLD_FORM_A, block_id, "seg01", text, lang)

    # This record/assignment is built as though it were its OWN solo batch
    # -- it carries zero local knowledge of FOLD_FORM_B. canon.json is the
    # only place both forms appear together.
    triage_path = tmp_path / "skeptic_triage.json"
    write_json(triage_path, {
        "schema_version": 1, "run_id": "run-1",
        "records": [adverse_record(FOLD_FORM_A, form_a_evidence)],
    })
    aggregate_path = tmp_path / "assignments.json"
    write_json(aggregate_path, make_aggregate_manifest(
        "run-1", [make_assignment(FOLD_FORM_A, [window_for(form_a_evidence)])]
    ))

    # Sanity: with NO --canon passed (default, empty competitors universe --
    # see _resolve_competitors' own tolerant-absent reading), this
    # byte-verified adverse record verifies cleanly -- proving the failure
    # below comes from the collision check, not a fixture bug.
    baseline = sr.run_verify_merged(
        triage_path, aggregate_path, manifest_path, particle_config, languages_dir=lang_dir
    )
    assert baseline["verified"] is True

    canon_path = tmp_path / "canon.json"
    write_json(canon_path, {"entries": {
        FOLD_FORM_A: {"canonical_target_form": "Target", "is_proper_name": True,
                      "basis": "transliterated", "confidence": "high"},
        FOLD_FORM_B: {"canonical_target_form": "Target", "is_proper_name": True,
                      "basis": "transliterated", "confidence": "high"},
    }})

    result = sr.run_verify_merged(
        triage_path, aggregate_path, manifest_path, particle_config,
        languages_dir=lang_dir, canon_path=canon_path,
    )
    # Mutation: deriving the collision check from anything local to this
    # batch's own triage/assignment records (instead of re-reading the FULL
    # canon.json this call was given) would never see FOLD_FORM_B at all and
    # wrongly keep this verified.
    assert result["verified"] is False
    assert any("does not survive fresh re-verification" in m for m in result["missing"])


def test_verify_merged_resolve_competitors_consumes_h1s_own_snapshot(tmp_path, monkeypatch):
    """Codex round 5 BLOCKER: run_verify_merged() used to hash canon.json
    for the H1 tamper check (frozen_input_check()), then call
    _resolve_competitors() -- a SEPARATE, independent re-read of
    canon.json -- to build the #243 ambiguity-competitors universe. A
    mutation landing in the window between those two reads let H1 approve
    the ORIGINAL snapshot (frozen_input_mismatch: False) while the
    collision check silently verified against the MUTATED one -- the same
    canon-widening mechanism as
    test_verify_merged_fails_closed_on_cross_batch_fold_collision above,
    but arriving as a TAMPER between two reads of the SAME call rather than
    a legitimate wider --canon input.

    Proves there is now only ONE read: monkeypatches
    read_frozen_input_snapshot (frozen_input_check()'s own capture point)
    to return the ORIGINAL bytes it just captured via the real
    implementation, then mutate canon.json on disk immediately after --
    injecting FOLD_FORM_B, which was NOT present in the canon.json this
    run's own H1 stamp describes. canon_sha256 is stamped from the
    ORIGINAL (FOLD_FORM_A-only) content, so H1 must still report
    frozen_input_mismatch=False (the captured snapshot IS what the stamp
    describes) -- but in the pre-fix code, _resolve_competitors()'s own
    independent second read would see the mutated (FOLD_FORM_B-added) file
    and wrongly fail this record closed, while frozen_input_mismatch
    stayed False throughout -- silently hiding that the run's OWN
    collision check disagreed with what its OWN H1 check just certified.
    In the fixed code (_resolve_competitors()'s own canon_snapshot reuse
    parses the SAME snapshot H1 hashed, never a second read), this record
    must still verify."""
    lang_dir = tmp_path / "languages"
    particle_config = write_particle_config(lang_dir, name_inventory=[FOLD_FORM_A])
    lang = bn.load_language_config(particle_config, languages_dir=lang_dir)
    text = f"ראה {FOLD_FORM_A} אתמול."
    block_id, blk = block(text)
    manifest_path = tmp_path / "manifest.json"
    write_json(manifest_path, make_manifest((block_id, blk)))

    form_a_evidence = evidence_for(FOLD_FORM_A, block_id, "seg01", text, lang)

    triage_path = tmp_path / "skeptic_triage.json"
    write_json(triage_path, {
        "schema_version": 1, "run_id": "run-1",
        "records": [adverse_record(FOLD_FORM_A, form_a_evidence)],
    })

    canon_path = tmp_path / "canon.json"
    canon_path.write_text(json.dumps({"entries": {
        FOLD_FORM_A: {"canonical_target_form": "Target", "is_proper_name": True,
                      "basis": "transliterated", "confidence": "high"},
    }}), encoding="utf-8")
    canon_sha256 = sr.compute_frozen_input_hash(canon_path)

    aggregate_path = tmp_path / "assignments.json"
    write_json(aggregate_path, {
        **make_aggregate_manifest("run-1", [make_assignment(FOLD_FORM_A, [window_for(form_a_evidence)])]),
        "canon_sha256": canon_sha256,
    })

    mutated_canon_bytes = json.dumps({"entries": {
        FOLD_FORM_A: {"canonical_target_form": "Target", "is_proper_name": True,
                      "basis": "transliterated", "confidence": "high"},
        FOLD_FORM_B: {"canonical_target_form": "Target", "is_proper_name": True,
                      "basis": "transliterated", "confidence": "high"},
    }}).encode("utf-8")

    real_read_frozen_input_snapshot = sr.read_frozen_input_snapshot

    def _capture_then_mutate_canon(path):
        result = real_read_frozen_input_snapshot(path)
        if Path(path) == canon_path:
            canon_path.write_bytes(mutated_canon_bytes)
        return result

    monkeypatch.setattr(sr, "read_frozen_input_snapshot", _capture_then_mutate_canon)

    result = sr.run_verify_merged(
        triage_path, aggregate_path, manifest_path, particle_config,
        languages_dir=lang_dir, canon_path=canon_path,
    )
    assert result["frozen_input_mismatch"] is False, (
        "the H1 stamp describes the ORIGINAL (FOLD_FORM_A-only) canon.json "
        "-- the captured snapshot this run actually hashed -- so it must "
        "still match regardless of the later on-disk mutation"
    )
    assert result["verified"] is True, (
        "MUTATION CAUGHT: this record was rejected, meaning the "
        "competitors universe was built from the MUTATED canon.json (now "
        "containing FOLD_FORM_B) -- a second, independent re-read after "
        "frozen_input_check() already hashed and approved the ORIGINAL "
        "snapshot, letting the collision check and the H1 result silently "
        "describe two different canon.json versions"
    )
    # The on-disk file genuinely IS mutated now -- confirms this is a real
    # injected mutation, not a no-op.
    assert canon_path.read_bytes() == mutated_canon_bytes


def test_validate_fragment_fails_closed_on_fold_collision(tmp_path):
    """Same site-1 fix, --validate-fragment side (per-batch precheck/
    dispatch self-check) -- must fail the SAME way as --verify-merged."""
    lang_dir = tmp_path / "languages"
    particle_config = write_particle_config(lang_dir, name_inventory=[FOLD_FORM_A])
    lang = bn.load_language_config(particle_config, languages_dir=lang_dir)
    text = f"ראה {FOLD_FORM_A} אתמול."
    block_id, blk = block(text)
    manifest_path = tmp_path / "manifest.json"
    write_json(manifest_path, make_manifest((block_id, blk)))

    form_a_evidence = evidence_for(FOLD_FORM_A, block_id, "seg01", text, lang)
    fragment_path = tmp_path / "triage_0.json"
    write_json(fragment_path, {
        "schema_version": 1, "run_id": "run-1",
        "records": [adverse_record(FOLD_FORM_A, form_a_evidence)],
    })

    canon_path = tmp_path / "canon.json"
    write_json(canon_path, {"entries": {
        FOLD_FORM_A: {"canonical_target_form": "Target", "is_proper_name": True,
                      "basis": "transliterated", "confidence": "high"},
        FOLD_FORM_B: {"canonical_target_form": "Target", "is_proper_name": True,
                      "basis": "transliterated", "confidence": "high"},
    }})

    result = sr.run_validate_fragment(
        fragment_path, manifest_path, particle_config, languages_dir=lang_dir, canon_path=canon_path,
    )
    assert result["success"] is True  # --validate-fragment COERCES, never rejects, a bad citation
    coerced = json.loads(fragment_path.read_text(encoding="utf-8"))
    assert coerced["records"][0]["verdict"] == "insufficient_window", (
        "MUTATION CAUGHT: --validate-fragment's own _coerce_record call must "
        "also receive the collision-aware competitors map -- omitting it "
        "here would leave this byte-verified adverse record uncoerced"
    )


def test_verify_merged_fails_on_manifest_tamper(tmp_path):
    """Same H1 mitigation mechanism as canon.json, for manifest_sha256 --
    skeptic-assignment.schema.json documents the identical rationale for
    both stamps."""
    lang_dir = tmp_path / "languages"
    particle_config = write_particle_config(lang_dir)
    text = "Jean walked home."
    block_id, blk = block(text)
    manifest_path = tmp_path / "manifest.json"
    write_json(manifest_path, make_manifest((block_id, blk)))
    manifest_sha256 = sr.compute_frozen_input_hash(manifest_path)

    rec = insufficient_record("Jean")
    triage_path = tmp_path / "skeptic_triage.json"
    write_json(triage_path, {"schema_version": 1, "run_id": "run-1", "records": [rec]})
    aggregate_path = tmp_path / "assignments.json"
    write_json(aggregate_path, {
        **make_aggregate_manifest("run-1", [make_assignment("Jean", [])]),
        "manifest_sha256": manifest_sha256,
    })

    result = sr.run_verify_merged(triage_path, aggregate_path, manifest_path, particle_config, languages_dir=lang_dir)
    assert result == {"verified": True, "missing": [], "frozen_input_mismatch": False}

    # Tamper: mutate manifest.json on disk after setup stamped its hash.
    tampered_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    tampered_manifest["blocks"][block_id]["plain_text"] += " injected."
    write_json(manifest_path, tampered_manifest)

    result = sr.run_verify_merged(triage_path, aggregate_path, manifest_path, particle_config, languages_dir=lang_dir)
    assert result["verified"] is False
    assert any("manifest.json" in m and "tamper" in m for m in result["missing"])
    assert result["frozen_input_mismatch"] is True


# ---------------------------------------------------------------------------
# codex round-2 High: the coerce-delta check (fix M2b) alone cannot catch a
# PARTIAL propose_split referent tamper -- _coerce_record's own propose_split
# branch drops a referent that fails to re-verify but leaves the record's
# verdict at propose_split as long as >=2 OTHER referents still verify, so a
# 3-referent propose_split with exactly one tampered referent produces no
# verdict delta at all. Fixed by extending the M3 window-scoping loop to
# ALSO independently byte-re-authenticate every citation (top-level evidence
# AND every referents[].evidence), per-citation, regardless of verdict.
# ---------------------------------------------------------------------------

def test_verify_merged_fails_on_propose_split_partial_referent_tamper(tmp_path):
    """RED before the fix: a merged propose_split record with 3 referents,
    ONE of which has a tampered offset that no longer byte-verifies (the
    other 2 still do), with a stored evidence_coverage that falsely claims
    full coverage ({"cited": 3, "verified": 3}) -- the coerce-delta check
    alone sees verdict stay propose_split (>=2 referents still survive
    re-coercion) and never flags it. Must now FAIL, naming the bad
    referent specifically."""
    lang_dir = tmp_path / "languages"
    particle_config = write_particle_config(lang_dir)
    lang = bn.load_language_config(particle_config, languages_dir=lang_dir)
    text = "Jean met Jean and also Jean at the market."  # 3 occurrences
    block_id, blk = block(text)
    manifest_path = tmp_path / "manifest.json"
    write_json(manifest_path, make_manifest((block_id, blk)))

    ev0 = evidence_for("Jean", block_id, "seg01", text, lang, index=0)
    ev1 = evidence_for("Jean", block_id, "seg01", text, lang, index=1)
    ev2 = evidence_for("Jean", block_id, "seg01", text, lang, index=2)
    tampered_ev2 = dict(ev2)
    tampered_ev2["char_start"] += 1
    tampered_ev2["char_end"] += 1  # shifted off the real production span

    referents = [
        {"disambiguator": "Jean A", "evidence": ev0},
        {"disambiguator": "Jean B", "evidence": ev1},
        {"disambiguator": "Jean C", "evidence": tampered_ev2},
    ]
    rec = propose_split_record("Jean", referents)
    rec["evidence_coverage"] = {"cited": 3, "verified": 3}  # falsely claims full coverage

    triage_path = tmp_path / "skeptic_triage.json"
    write_json(triage_path, {"schema_version": 1, "run_id": "run-1", "records": [rec]})
    aggregate_path = tmp_path / "assignments.json"
    write_json(aggregate_path, make_aggregate_manifest(
        "run-1", [make_assignment("Jean", [window_for(ev0), window_for(ev1), window_for(ev2)])],
    ))

    result = sr.run_verify_merged(triage_path, aggregate_path, manifest_path, particle_config, languages_dir=lang_dir)
    assert result["verified"] is False
    assert any("referents[2].evidence" in m and "no longer byte-verifies" in m for m in result["missing"])


def test_verify_merged_passes_on_clean_propose_split_with_3_referents(tmp_path):
    """Positive control for the fix above: a genuinely clean 3-referent
    propose_split (every citation byte-verifies, every citation is inside
    the assignment's own windows) must still return verified:true -- the
    new per-citation loop must not misfire on legitimate evidence."""
    lang_dir = tmp_path / "languages"
    particle_config = write_particle_config(lang_dir)
    lang = bn.load_language_config(particle_config, languages_dir=lang_dir)
    text = "Jean met Jean and also Jean at the market."
    block_id, blk = block(text)
    manifest_path = tmp_path / "manifest.json"
    write_json(manifest_path, make_manifest((block_id, blk)))

    ev0 = evidence_for("Jean", block_id, "seg01", text, lang, index=0)
    ev1 = evidence_for("Jean", block_id, "seg01", text, lang, index=1)
    ev2 = evidence_for("Jean", block_id, "seg01", text, lang, index=2)

    referents = [
        {"disambiguator": "Jean A", "evidence": ev0},
        {"disambiguator": "Jean B", "evidence": ev1},
        {"disambiguator": "Jean C", "evidence": ev2},
    ]
    rec = propose_split_record("Jean", referents)
    rec["evidence_coverage"] = {"cited": 3, "verified": 3}

    triage_path = tmp_path / "skeptic_triage.json"
    write_json(triage_path, {"schema_version": 1, "run_id": "run-1", "records": [rec]})
    aggregate_path = tmp_path / "assignments.json"
    write_json(aggregate_path, make_aggregate_manifest(
        "run-1", [make_assignment("Jean", [window_for(ev0), window_for(ev1), window_for(ev2)])],
    ))

    result = sr.run_verify_merged(triage_path, aggregate_path, manifest_path, particle_config, languages_dir=lang_dir)
    assert result == {"verified": True, "missing": [], "frozen_input_mismatch": False}
