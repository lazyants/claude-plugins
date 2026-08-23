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
# Codex round 8: compute_frozen_input_hash() is deliberately NOT imported
# into skeptic_ready.py any more (nothing in its production code has called
# it directly since round 7) -- it stays test-only fixture-stamping sugar,
# so this suite imports it straight from suspicion_scan.py, where it is
# actually defined. `sr = _load_module(...)` above already triggered a real
# `import suspicion_scan` as a side effect of skeptic_ready.py's own
# top-level `from suspicion_scan import (...)`, so this is the SAME cached
# module object, not a second independent load.
suspicion_scan = sys.modules["suspicion_scan"]


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
    # Bool-valued on purpose: the shape a buggy agent emits. The reject is
    # by additionalProperties, never by value type, and pyright infers
    # insufficient_record()'s dict as dict[str, str] from its literals.
    rec["confirmed_ok"] = True  # pyright: ignore[reportArgumentType]
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
# #368: evidence_coverage.cited is DURABLE across repeated validation.
#
# --validate-fragment rewrites the fragment in place, and the shipped workflow
# runs it at least twice on the ordinary path (codex's own self-check, then the
# wait poll). Before #368 the second run recomputed `cited` from the
# ALREADY-PRUNED referent list, so an honest "3 offered, 2 verified" silently
# became "2 offered, 2 verified" and the only human-visible trace that a
# citation had been rejected -- skeptic_report.py's `(partial)` label -- was
# gone. Measured before the fix, three consecutive runs over one fragment:
# {'cited': 3, 'verified': 2} -> {'cited': 2, 'verified': 2} -> {'cited': 2,
# 'verified': 2}, with `coerced` 0 throughout because the VERDICT never moved.
#
# `cited` is now MONOTONE: max(the value already on disk, this call's referent
# count). The first four tests below were each watched RED against that old
# behaviour; the last two were GREEN before the fix as well and are labelled
# as characterization, not as regressions.
#
# Every one of them drives the SHIPPED writer, run_validate_fragment(), over a
# real on-disk fragment -- never _coerce_record() in isolation -- because it is
# the in-place rewrite that makes the second call see its own output.
# ---------------------------------------------------------------------------

def _partial_split_fixture(tmp_path, *, referent_count=3, bad_referents=1):
    """A propose_split fragment on disk whose LAST `bad_referents` referents
    cite a span shifted one character off the real production occurrence, so
    they cannot byte-verify. Returns (fragment_path, manifest_path,
    particle_config, languages_dir)."""
    lang_dir = tmp_path / "languages"
    particle_config = write_particle_config(lang_dir)
    lang = bn.load_language_config(particle_config, languages_dir=lang_dir)
    text = " and ".join(["Jean"] * referent_count) + " met at the market."
    block_id, blk = block(text)
    manifest_path = tmp_path / "manifest.json"
    write_json(manifest_path, make_manifest((block_id, blk)))

    referents = []
    for i in range(referent_count):
        ev = dict(evidence_for("Jean", block_id, "seg01", text, lang, index=i))
        if i >= referent_count - bad_referents:
            ev["char_start"] += 1
            ev["char_end"] += 1  # shifted off the real production span
        referents.append({"disambiguator": f"Jean {i}", "evidence": ev})

    frag_path = tmp_path / "triage_0.json"
    write_json(frag_path, {
        "schema_version": 1, "run_id": "run-1",
        "records": [propose_split_record("Jean", referents)],
    })
    return frag_path, manifest_path, particle_config, lang_dir


def _coverage_on_disk(frag_path):
    return json.loads(frag_path.read_text(encoding="utf-8"))["records"][0]["evidence_coverage"]


def test_validate_fragment_second_run_preserves_partial_coverage(tmp_path):
    """#368, the defect itself. The FIRST run prunes 3 referents to 2 and
    records {3, 2}; the SECOND run is handed the pruned list and must NOT
    recompute `cited` down to 2. Watched RED before the fix, where run 2
    wrote {'cited': 2, 'verified': 2} -- a partial coverage silently
    rewritten as complete."""
    frag_path, manifest_path, pc, lang_dir = _partial_split_fixture(tmp_path)

    first = sr.run_validate_fragment(frag_path, manifest_path, pc, languages_dir=lang_dir)
    assert first["coerced"] == 0
    assert _coverage_on_disk(frag_path) == {"cited": 3, "verified": 2}

    second = sr.run_validate_fragment(frag_path, manifest_path, pc, languages_dir=lang_dir)
    assert second["coerced"] == 0
    assert _coverage_on_disk(frag_path) == {"cited": 3, "verified": 2}, (
        "the second validation recomputed `cited` from the already-pruned "
        "referent list -- the #368 defect"
    )


def test_validate_fragment_is_idempotent_bytes(tmp_path):
    """The stronger form of the same claim, and the one that also covers the
    referent list and `notes`: under unchanged verifier inputs the second run
    writes a BYTE-IDENTICAL document. Watched RED before the fix (the two
    documents differed in `evidence_coverage.cited`)."""
    frag_path, manifest_path, pc, lang_dir = _partial_split_fixture(tmp_path)

    sr.run_validate_fragment(frag_path, manifest_path, pc, languages_dir=lang_dir)
    after_first = frag_path.read_bytes()

    sr.run_validate_fragment(frag_path, manifest_path, pc, languages_dir=lang_dir)
    assert frag_path.read_bytes() == after_first, (
        "a second --validate-fragment over an already-normalized fragment is "
        "not a no-op on disk"
    )


def test_evidence_coverage_cited_never_shrinks_on_further_pruning(tmp_path):
    """`cited` is the count ORIGINALLY offered -- a high-water mark ACROSS
    calls, never a recount of the list one call happened to receive. A fragment that starts at 4 referents with 1 unverifiable,
    then loses a second referent on a later validation, must read {4, 2} --
    4 offered against 2 that still verify. Watched RED before the fix, which
    reported {3, 2}: the count of what happened to be on disk at that call."""
    frag_path, manifest_path, pc, lang_dir = _partial_split_fixture(
        tmp_path, referent_count=4, bad_referents=1
    )
    sr.run_validate_fragment(frag_path, manifest_path, pc, languages_dir=lang_dir)
    assert _coverage_on_disk(frag_path) == {"cited": 4, "verified": 3}

    # A survivor stops verifying before the next validation -- the same shape
    # a moved manifest, a new #243 fold collision, or a meddling agent
    # produces. Only the citation moves; the referent stays present.
    doc = json.loads(frag_path.read_text(encoding="utf-8"))
    doc["records"][0]["referents"][0]["evidence"]["char_start"] += 1
    doc["records"][0]["referents"][0]["evidence"]["char_end"] += 1
    write_json(frag_path, doc)

    sr.run_validate_fragment(frag_path, manifest_path, pc, languages_dir=lang_dir)
    assert _coverage_on_disk(frag_path) == {"cited": 4, "verified": 2}


def test_evidence_coverage_inflated_prior_cited_is_kept_partial(tmp_path):
    """THE COUNTEREXAMPLE FOR THE TRADE-OFF, pinned so a later "tighten it"
    change has to argue with a test rather than with a comment.

    Taking the max means trusting a number the fragment's own author wrote.
    The direction of that trust is deliberate and one-way: an inflated `cited`
    can only make the record read MORE partial (`2/9 verified (partial)`),
    never complete, and skeptic_report.py already bounds an oversized label.
    Watched RED before the fix, which overwrote the inflated value with
    {2, 2} -- i.e. turned a suspicious record into a clean-looking one."""
    frag_path, manifest_path, pc, lang_dir = _partial_split_fixture(
        tmp_path, referent_count=2, bad_referents=0
    )
    doc = json.loads(frag_path.read_text(encoding="utf-8"))
    doc["records"][0]["evidence_coverage"] = {"cited": 9, "verified": 9}
    write_json(frag_path, doc)

    sr.run_validate_fragment(frag_path, manifest_path, pc, languages_dir=lang_dir)
    rec = json.loads(frag_path.read_text(encoding="utf-8"))["records"][0]
    assert rec["verdict"] == "propose_split"
    assert rec["evidence_coverage"] == {"cited": 9, "verified": 2}


def test_evidence_coverage_prior_cited_below_actual_is_ignored(tmp_path):
    """CHARACTERIZATION -- green before #368 as well as after, and recorded as
    such rather than dressed up as a regression. The max is one-directional:
    a prior BELOW this call's referent count can never pull `cited` down, so a
    deflated value is simply overridden. What it cannot do is recover a value
    an agent deflated AFTER a validation already pruned the list -- see the
    #368 non-goal in _coerce_record's own docstring."""
    frag_path, manifest_path, pc, lang_dir = _partial_split_fixture(
        tmp_path, referent_count=3, bad_referents=0
    )
    doc = json.loads(frag_path.read_text(encoding="utf-8"))
    doc["records"][0]["evidence_coverage"] = {"cited": 0, "verified": 0}
    write_json(frag_path, doc)

    sr.run_validate_fragment(frag_path, manifest_path, pc, languages_dir=lang_dir)
    assert _coverage_on_disk(frag_path) == {"cited": 3, "verified": 3}


def test_downgraded_record_second_run_does_not_reappend_its_note(tmp_path):
    """CHARACTERIZATION -- green before #368 as well. It pins the OTHER half of
    the idempotence claim the fix now makes in prose: a record coerced down to
    insufficient_window keeps exactly ONE
    `skeptic_ready:coerced_insufficient_window:` note however many times the
    fragment is re-validated, because the second call takes the
    insufficient_window branch and returns a copy without re-appending."""
    lang_dir = tmp_path / "languages"
    particle_config = write_particle_config(lang_dir)
    lang = bn.load_language_config(particle_config, languages_dir=lang_dir)
    text = "Jean met Paul at the market."
    block_id, blk = block(text)
    manifest_path = tmp_path / "manifest.json"
    write_json(manifest_path, make_manifest((block_id, blk)))
    bad = dict(evidence_for("Jean", block_id, "seg01", text, lang))
    bad["char_start"] += 1
    bad["char_end"] += 1
    frag_path = tmp_path / "triage_0.json"
    write_json(frag_path, {
        "schema_version": 1, "run_id": "run-1",
        "records": [adverse_record("Jean", bad)],
    })

    first = sr.run_validate_fragment(frag_path, manifest_path, particle_config, languages_dir=lang_dir)
    assert first["coerced"] == 1
    after_first = frag_path.read_bytes()
    notes = json.loads(after_first)["records"][0]["notes"]
    assert sum(1 for n in notes if n.startswith("skeptic_ready:coerced_insufficient_window:")) == 1

    second = sr.run_validate_fragment(frag_path, manifest_path, particle_config, languages_dir=lang_dir)
    assert second["coerced"] == 0
    assert frag_path.read_bytes() == after_first


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


def test_verify_merged_preserves_the_machine_note_behind_many_agent_notes(tmp_path):
    """Round 10 (MEDIUM): `_coerce_record`'s own `_downgrade` always APPENDS
    the machine's own diagnosis LAST to `notes`
    (`notes.append(f"skeptic_ready:coerced_insufficient_window:{reason}")`)
    -- confirmed by reading `_downgrade` directly, the same convention
    skeptic_report.py's own `_bounded_items` docstring names and fixes for.
    That note then feeds `run_verify_merged`'s own composed verdict-mismatch
    message as `detail`, at the message's own END. A plain head-first
    character cap on the WHOLE composed message keeps every earlier
    agent-authored note and drops exactly the one note the agent did not
    write. This is the same base fixture as
    test_verify_merged_fails_on_post_merge_tampered_evidence_offset, with 20
    long agent-authored notes added to the record BEFORE tampering, so the
    composed message would exceed the per-item cap without the fix."""
    lang_dir = tmp_path / "languages"
    particle_config = write_particle_config(lang_dir)
    lang = bn.load_language_config(particle_config, languages_dir=lang_dir)
    text = "Jean met Paul at the market square."
    block_id, blk = block(text)
    manifest_path = tmp_path / "manifest.json"
    write_json(manifest_path, make_manifest((block_id, blk)))

    jean_evidence = evidence_for("Jean", block_id, "seg01", text, lang)
    rec = adverse_record("Jean", jean_evidence)
    rec["notes"] = [f"agent note #{i}: " + ("x" * 60) for i in range(20)]

    triage_path = tmp_path / "skeptic_triage.json"
    write_json(triage_path, {"schema_version": 1, "run_id": "run-1", "records": [rec]})
    aggregate_path = tmp_path / "assignments.json"
    write_json(aggregate_path, make_aggregate_manifest("run-1", [make_assignment("Jean", [window_for(jean_evidence)])]))

    assert sr.run_verify_merged(
        triage_path, aggregate_path, manifest_path, particle_config, languages_dir=lang_dir
    )["verified"] is True

    tampered = json.loads(triage_path.read_text(encoding="utf-8"))
    tampered["records"][0]["evidence"]["char_start"] += 1
    tampered["records"][0]["evidence"]["char_end"] += 1
    write_json(triage_path, tampered)

    result = sr.run_verify_merged(triage_path, aggregate_path, manifest_path, particle_config, languages_dir=lang_dir)
    assert result["verified"] is False
    assert any(
        "does not survive fresh re-verification" in m
        and "skeptic_ready:coerced_insufficient_window" in m
        for m in result["missing"]
    ), (
        f"the machine's own coercion diagnosis must survive behind 20 agent notes, not be "
        f"truncated away because it is the LAST thing in the composed message: {result['missing']}"
    )


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
    canon_sha256 = suspicion_scan.compute_frozen_input_hash(canon_path)

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


def test_verify_merged_missing_is_bounded_per_population_not_pooled(tmp_path):
    """Round 10 (verifier MEDIUM, reported HIGH): `missing[]` used to be ONE
    flat list -- schema failures, H1 frozen-input tamper reasons, coverage
    gaps, and per-record findings all pooled, `sorted()`ed alphabetically,
    then head-capped at 8. A lexical sort has no relationship to importance:
    whichever population happens to sort last can be evicted WHOLESALE by an
    unrelated, larger population that sorts earlier -- not merely trimmed.

    Measured directly (this is the fixture that proved it, not a
    hypothetical): a canon.json tamper (sorts after "canon") alongside 10
    "assignment <64-hex> has no triage record (coverage gap)" entries (sort
    before "canon" -- 'a' < 'c') -- under the old single-pool bound the
    tamper reason TEXT was completely ABSENT from `missing[]`, even though
    `frozen_input_mismatch` (the boolean skeptic-pass-wf.template.js actually
    gates HALT on) still correctly fired. That is why the verifier downgraded
    HIGH to MEDIUM: the safety behaviour survives: only the diagnostic text
    was at risk. Fixed by bounding the structural/coverage/per-record
    populations separately and concatenating -- this pins that the tamper
    reason and a representative sample of coverage gaps are BOTH present at
    once, not one crowding out the other."""
    lang_dir = tmp_path / "languages"
    particle_config = write_particle_config(lang_dir)
    text = "Jean walked home."
    block_id, blk = block(text)
    manifest_path = tmp_path / "manifest.json"
    write_json(manifest_path, make_manifest((block_id, blk)))

    canon_path = tmp_path / "canon.json"
    canon_path.write_text(json.dumps({"entries": {}}), encoding="utf-8")
    canon_sha256 = suspicion_scan.compute_frozen_input_hash(canon_path)

    rec = insufficient_record("Jean")
    triage_path = tmp_path / "skeptic_triage.json"
    write_json(triage_path, {"schema_version": 1, "run_id": "run-1", "records": [rec]})

    # "Jean" gets a real triage record; 10 OTHER assigned entities never do
    # -- each produces its own "assignment <aid> has no triage record
    # (coverage gap)" entry, sorting alphabetically before "canon.json ...".
    assignments = [make_assignment("Jean", [])]
    for i in range(10):
        assignments.append(make_assignment(f"Missing{i}", []))
    aggregate_path = tmp_path / "assignments.json"
    write_json(aggregate_path, {
        **make_aggregate_manifest("run-1", assignments),
        "canon_sha256": canon_sha256,
    })

    # Tamper canon.json AFTER stamping (same technique as the sibling tamper
    # test above).
    canon_path.write_text(json.dumps({"entries": {"INJECTED": {}}}), encoding="utf-8")

    result = sr.run_verify_merged(
        triage_path, aggregate_path, manifest_path, particle_config, languages_dir=lang_dir, canon_path=canon_path,
    )
    assert result["verified"] is False
    assert result["frozen_input_mismatch"] is True, (
        "the safety-relevant boolean must fire regardless of missing[]'s rendering"
    )
    assert any("canon.json" in m and "tamper" in m for m in result["missing"]), (
        f"the tamper reason must survive alongside the coverage-gap population, not be "
        f"evicted by it: {result['missing']}"
    )
    assert any("has no triage record (coverage gap)" in m for m in result["missing"]), (
        "the coverage-gap population must still be represented too -- this is not a "
        "structural-only fix that starves the other population instead"
    )


def test_verify_merged_routine_coverage_gaps_do_not_evict_the_integrity_kinds(tmp_path):
    """#377: round 10 gave COVERAGE its own budget, but coverage is three
    sub-populations of very unequal severity sharing one lexically-sorted
    head-4 slice -- and the routine kind sorts first: 'assignment ' (0x20,
    "has no triage record (coverage gap)") < 'assignment_' (0x5f, "has N
    triage records (expected exactly 1)") < 'triage ' ("references an
    assignment_id absent from the aggregate manifest").

    So four or more ordinary coverage gaps evicted exactly the two entries
    that distinguish "the agent skipped entities" (routine -- it ran out of
    budget, or the batch never came back) from "the agent injected a forged
    or duplicated record" (integrity). This fixture is the measurement, not a
    hypothetical: six gaps alongside one foreign record and one duplicated
    assignment_id -- under the single coverage pool the head-4 is six gaps'
    worth of prefix and BOTH integrity messages are absent from missing[]
    entirely, even though `verified` (computed from the unbounded list) is
    correctly False either way. Pins that all three kinds are represented at
    once, and that the routine kind is not starved in exchange."""
    lang_dir = tmp_path / "languages"
    particle_config = write_particle_config(lang_dir)
    text = "Dup walked home."
    block_id, blk = block(text)
    manifest_path = tmp_path / "manifest.json"
    write_json(manifest_path, make_manifest((block_id, blk)))

    canon_path = tmp_path / "canon.json"
    canon_path.write_text(json.dumps({"entries": {}}), encoding="utf-8")

    # "Dup" is assigned once and triaged TWICE (duplicate-record integrity);
    # three "Ghost<i>" records are triaged but never assigned (foreign-record
    # integrity); the six "Miss<i>" entities are assigned and never triaged
    # (the routine coverage-gap population that used to evict the other two).
    assignments = [make_assignment("Dup", [])]
    for i in range(6):
        assignments.append(make_assignment(f"Miss{i}", []))
    aggregate_path = tmp_path / "assignments.json"
    write_json(aggregate_path, make_aggregate_manifest("run-1", assignments))

    triage_path = tmp_path / "skeptic_triage.json"
    write_json(triage_path, {
        "schema_version": 1,
        "run_id": "run-1",
        "records": [
            insufficient_record("Dup"),
            insufficient_record("Dup"),
            *(insufficient_record(f"Ghost{i}") for i in range(3)),
        ],
    })

    result = sr.run_verify_merged(
        triage_path, aggregate_path, manifest_path, particle_config, languages_dir=lang_dir, canon_path=canon_path,
    )
    assert result["verified"] is False
    assert any(
        "references an assignment_id absent from the aggregate manifest" in m
        for m in result["missing"]
    ), (
        f"the FOREIGN-record integrity entry must survive alongside the routine coverage "
        f"gaps, not lose a lexical race to them: {result['missing']}"
    )
    assert any(
        "has 2 triage records (expected exactly 1)" in m for m in result["missing"]
    ), (
        f"the DUPLICATE-record integrity entry must survive the same way: "
        f"{result['missing']}"
    )
    assert any("has no triage record (coverage gap)" in m for m in result["missing"]), (
        "the routine coverage-gap population must still be represented -- this is not a "
        "fix that starves one population to feed another"
    )
    # Each population's own tail line counts ITS population, never the pooled
    # total: 6 gaps bounded at _MAX_LISTED_MISSING_HALF, so 4 shown of 6.
    assert "... and 2 more (showing the first 4 of 6)" in result["missing"], (
        f"the coverage-gap tail must report the gap population's own size: "
        f"{result['missing']}"
    )
    # And the integrity slice is bounded at the SMALLER
    # _MAX_LISTED_MISSING_INTEGRITY, not merely at something nonzero: 3
    # foreign records, 2 shown. Without this the cap could be raised back to
    # the coverage-gap budget -- or to the full 8 -- and every other
    # assertion here would stay green.
    assert "... and 1 more (showing the first 2 of 3)" in result["missing"], (
        f"the foreign-record population must be bounded at "
        f"_MAX_LISTED_MISSING_INTEGRITY: {result['missing']}"
    )


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
    senses_sha256 = suspicion_scan.compute_frozen_input_hash(senses_path)

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
    senses_sha256 = suspicion_scan.compute_frozen_input_hash(senses_path)

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
    senses_sha256 = suspicion_scan.compute_frozen_input_hash(senses_path)

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
        "canon_sha256": suspicion_scan.compute_frozen_input_hash(canon_path),
        "manifest_sha256": suspicion_scan.compute_frozen_input_hash(manifest_path),
        "senses_sha256": suspicion_scan.compute_frozen_input_hash(senses_path),
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
        "canon_sha256": suspicion_scan.compute_frozen_input_hash(canon_path),
        "manifest_sha256": suspicion_scan.compute_frozen_input_hash(manifest_path),
        "senses_sha256": suspicion_scan.compute_frozen_input_hash(senses_path),
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


# Codex round 9: `(frozen_input, label)` used to be a hand-typed second
# restatement of `sr.FROZEN_INPUT_SPECS` (the SAME tuple `frozen_input_check()`
# itself iterates, round 8) -- deriving it here means a fourth frozen input
# added to that tuple automatically grows this parametrization into a
# fourth case, instead of the suite staying green with no test for it.
@pytest.mark.parametrize("frozen_input, label", [
    (key, label) for key, label, _stamp_field in sr.FROZEN_INPUT_SPECS
])
def test_check_frozen_inputs_detects_tamper_per_frozen_input(tmp_path, frozen_input, label):
    """Codex round 8 (finding 1c, point 2): the two existing standalone
    --check-frozen-inputs tamper tests each exercise exactly ONE slot --
    the test above mutates canon_senses.json,
    test_check_frozen_inputs_cli_exit_code_reflects_mismatch below mutates
    canon.json -- so a manifest-only mismatch was never proven detected on
    THIS caller (run_check_frozen_inputs/--check-frozen-inputs), only on
    run_verify_merged's own test_verify_merged_fails_on_manifest_tamper.
    Parametrized over all three slots so every one of them has its own
    standalone mismatch case here, closing that gap directly rather than
    trusting one slot's coverage to generalize to the others -- the same
    reasoning that made frozen_input_check() itself table-driven applies to
    proving its callers actually exercise every row of that table."""
    canon_path = tmp_path / "canon.json"
    canon_path.write_text(json.dumps({"entries": {}}), encoding="utf-8")
    manifest_path = tmp_path / "manifest.json"
    write_json(manifest_path, make_manifest())
    senses_path = tmp_path / "canon_senses.json"
    senses_path.write_text(json.dumps({"schema_version": 1, "entries_by_source_form": {}}), encoding="utf-8")

    aggregate_path = tmp_path / "assignments.json"
    write_json(aggregate_path, {
        **make_aggregate_manifest("run-1", []),
        "canon_sha256": suspicion_scan.compute_frozen_input_hash(canon_path),
        "manifest_sha256": suspicion_scan.compute_frozen_input_hash(manifest_path),
        "senses_sha256": suspicion_scan.compute_frozen_input_hash(senses_path),
    })

    result = sr.run_check_frozen_inputs(
        aggregate_path, canon_path=canon_path, manifest_path=manifest_path, senses_path=senses_path,
    )
    assert result == {"frozen_input_mismatch": False, "missing": []}, f"[{frozen_input}] not clean before tamper: {result}"

    # Tamper: mutate ONLY this slot's on-disk content after stamping -- the
    # other two stay byte-identical to what was stamped, so a mismatch here
    # proves THIS slot's own comparison fired, not some other slot's.
    tamper_target = {"canon": canon_path, "manifest": manifest_path, "senses": senses_path}[frozen_input]
    tamper_target.write_text(tamper_target.read_text(encoding="utf-8") + " ", encoding="utf-8")

    result = sr.run_check_frozen_inputs(
        aggregate_path, canon_path=canon_path, manifest_path=manifest_path, senses_path=senses_path,
    )
    assert result["frozen_input_mismatch"] is True, f"[{frozen_input}] {result}"
    assert any(label in m and "tamper" in m for m in result["missing"]), f"[{frozen_input}] {result['missing']}"


# Codex round 9: same fix as test_check_frozen_inputs_detects_tamper_per_frozen_input
# above -- `frozen_input` now comes from `sr.FROZEN_INPUT_SPECS` itself
# rather than a second hand-typed `["canon", "manifest", "senses"]` list, so
# a fourth frozen input added to that tuple is automatically exercised here
# too.
@pytest.mark.parametrize("frozen_input", [key for key, _label, _stamp_field in sr.FROZEN_INPUT_SPECS])
def test_check_frozen_inputs_tolerates_a_read_failure(tmp_path, monkeypatch, frozen_input):
    """Codex round 6 BLOCKER (canon/senses) + codex round 7 BLOCKER
    (manifest): frozen_input_check() must route EVERY frozen input's read
    through the tolerant_reads gate, not just canon/senses --
    run_check_frozen_inputs discards all three captured snapshots and has
    no downstream parser that could ever consume them, so a transient read
    failure (a real I/O error, not absence -- codex's own repro forced one)
    on ANY ONE of the three should degrade that one check, never crash the
    whole call, breaking its own documented "never a crash" contract.
    test_check_frozen_inputs_tolerates_missing_aggregate_manifest/
    ..._malformed_aggregate_manifest above prove the AGGREGATE-unreadable
    half of that contract; this proves the per-input-unreadable half.

    Parameterized over canon/manifest/senses (codex round 7): this test
    used to target canon only (round 6's own repro), so it could not have
    caught manifest.json's bypass -- manifest.json was wired to a
    hand-written call straight to a path-based, ungated tamper-reason
    helper, never reaching frozen_input_check()'s own _snapshot_or_none()
    at all, regardless of tolerant_reads. The parametrization is itself
    part of the fix: every frozen input this check covers now shares the
    exact same code path, so a single test body proves the gate holds for
    all three instead of trusting canon's coverage to generalize (see
    frozen_input_check()'s own docstring for the full story).

    Forces the failure via a monkeypatch on read_frozen_input_snapshot
    (rather than chmod, which is unreliable when tests run as root or in
    sandboxes that ignore permission bits) targeting ONE path at a time --
    a genuine stamp IS present for it (matches the real content), so the
    read is genuinely attempted, not skipped via the "no stamp -> no read"
    gate. Patched in TWO places, not one: skeptic_ready.py's own
    `read_frozen_input_snapshot` name (what the fixed frozen_input_check()
    calls for all three inputs today) AND suspicion_scan.py's own module
    attribute of the same name (what the round-7 BUG's manifest.json path
    called -- compute_frozen_input_hash() is defined in suspicion_scan.py
    and its internal `read_frozen_input_snapshot(path)` call resolves via
    THAT module's globals, a separate binding `monkeypatch.setattr(sr, ...)`
    alone never reaches). Patching only the first would make this
    parametrization blind to a regression back to the exact bypass this
    round fixes -- the [manifest] case would silently pass again even if
    frozen_input_check() were reverted to call compute_frozen_input_hash()
    for manifest.json directly, since the injected failure would never
    reach that call chain.

    Codex round 8 (BLOCKER): a mutation that excludes manifest from the
    check under tolerant_reads=True (e.g. dropping its stamp from the
    table before the loop runs) makes frozen_input_check() never even
    attempt manifest.json's read -- no OSError is raised, the "clean"
    result comes back unchanged, and the [manifest] case passed for a
    reason that has nothing to do with tolerance. Below, `_reads` records
    every path either patched function actually saw, and the final assert
    proves `failing_path` was among them -- a slot whose read was skipped
    entirely (rather than attempted-and-tolerated) now fails loud instead
    of passing vacuously."""
    canon_path = tmp_path / "canon.json"
    canon_path.write_text(json.dumps({"entries": {}}), encoding="utf-8")
    manifest_path = tmp_path / "manifest.json"
    write_json(manifest_path, make_manifest())
    senses_path = tmp_path / "canon_senses.json"
    senses_path.write_text(json.dumps({"schema_version": 1, "entries_by_source_form": {}}), encoding="utf-8")

    failing_path = {"canon": canon_path, "manifest": manifest_path, "senses": senses_path}[frozen_input]

    aggregate_path = tmp_path / "assignments.json"
    write_json(aggregate_path, {
        **make_aggregate_manifest("run-1", []),
        "canon_sha256": suspicion_scan.compute_frozen_input_hash(canon_path),
        "manifest_sha256": suspicion_scan.compute_frozen_input_hash(manifest_path),
        "senses_sha256": suspicion_scan.compute_frozen_input_hash(senses_path),
    })

    suspicion_scan_module = sys.modules.get("suspicion_scan")
    assert suspicion_scan_module is not None, (
        "suspicion_scan must already be a real, cached import (triggered by "
        "skeptic_ready.py's own top-level `from suspicion_scan import ...`) "
        "-- if this ever stops holding, the second monkeypatch below would "
        "silently become a no-op instead of failing loud"
    )
    # Pin IDENTITY, not mere presence: sr.read_frozen_input_snapshot must be
    # the EXACT SAME function object as suspicion_scan_module's own
    # attribute of that name, proving skeptic_ready.py's own `from
    # suspicion_scan import read_frozen_input_snapshot` really did bind to
    # THIS module's current attribute (not a stale/shadow/reloaded copy).
    # A truthiness-only check (`is not None`) would let the second
    # monkeypatch below silently patch a function nothing in the call chain
    # actually resolves through, degrading it to a no-op that the [manifest]
    # slot in particular has no other coverage for (it is the ONE slot whose
    # legacy call site, per the docstring above, used to resolve
    # read_frozen_input_snapshot via suspicion_scan's own globals rather
    # than skeptic_ready's).
    assert suspicion_scan_module.read_frozen_input_snapshot is sr.read_frozen_input_snapshot, (
        "suspicion_scan_module.read_frozen_input_snapshot is not the same object as "
        "sr.read_frozen_input_snapshot -- the two names have diverged, so patching "
        "the module attribute below would not affect what skeptic_ready.py's own "
        "`from suspicion_scan import read_frozen_input_snapshot` binding calls"
    )

    _reads = []

    def _make_failing(real):
        def _fail_on_target(path):
            _reads.append(Path(path))
            if Path(path) == failing_path:
                raise OSError("simulated transient read failure")
            return real(path)
        return _fail_on_target

    monkeypatch.setattr(sr, "read_frozen_input_snapshot", _make_failing(sr.read_frozen_input_snapshot))
    monkeypatch.setattr(
        suspicion_scan_module, "read_frozen_input_snapshot",
        _make_failing(suspicion_scan_module.read_frozen_input_snapshot),
    )

    # MUTATION CAUGHT if this raises instead of returning: --check-frozen-inputs
    # exists specifically to keep answering when something else has already
    # gone wrong, and this mode never consumes any of the three frozen
    # inputs beyond the hash comparison itself, so a read failure on ANY
    # ONE of them should degrade this ONE check, never crash the whole
    # call. Before the round-7 fix, this raised for frozen_input="manifest"
    # specifically -- manifest.json bypassed the tolerant_reads gate
    # entirely via its own hand-written call site.
    result = sr.run_check_frozen_inputs(
        aggregate_path, canon_path=canon_path, manifest_path=manifest_path, senses_path=senses_path,
    )
    assert result == {"frozen_input_mismatch": False, "missing": []}
    # MUTATION CAUGHT (codex round 8) if this fails: proves failing_path was
    # actually READ (and its failure actually caught), not silently skipped
    # -- a slot excluded from the check before the read is ever attempted
    # would satisfy the assertion above for the wrong reason.
    assert failing_path in _reads, (
        f"{frozen_input}'s frozen input ({failing_path}) was never read -- "
        "the clean result above proves nothing about this slot unless its "
        "own read was genuinely attempted and its failure genuinely caught"
    )


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
    canon_sha256 = suspicion_scan.compute_frozen_input_hash(canon_path)

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
        "canon_sha256": suspicion_scan.compute_frozen_input_hash(canon_path),
        "manifest_sha256": suspicion_scan.compute_frozen_input_hash(manifest_path),
        "senses_sha256": suspicion_scan.compute_frozen_input_hash(senses_path),
    })

    argv_clean = [
        "--check-frozen-inputs", str(aggregate_path),
        "--canon", str(canon_path), "--manifest-path", str(manifest_path), "--senses-path", str(senses_path),
    ]
    assert sr.main(argv_clean) == 0

    canon_path.write_text(json.dumps({"entries": {"INJECTED": {}}}), encoding="utf-8")
    assert sr.main(argv_clean) == 1, "exit code must reflect frozen_input_mismatch, not just succeed unconditionally"


@pytest.mark.parametrize("direction", [
    "extra_tuple_entry", "missing_tuple_entry",
    "duplicate_key_entry", "same_count_key_swap",
])
def test_check_frozen_inputs_fails_closed_on_frozen_input_specs_key_mismatch(tmp_path, monkeypatch, direction):
    """Round 12 (#243): `frozen_input_check()`'s own `paths` dict
    (skeptic_ready.py:1051) is a FIXED, three-key `{"canon", "manifest",
    "senses"}` literal -- unlike the two digest functions'
    `{key: (state, bytes)}` maps (built fresh from their own kwargs every
    call, see tests/suspicion_scan.test.py's/tests/skeptic_setup.test.py's
    own `..._fails_closed_on_frozen_input_specs_key_mismatch` siblings),
    this dict never changes shape at runtime -- so a `FROZEN_INPUT_SPECS`
    entry that diverges from it (a fourth spec with no matching `paths`
    key, a spec dropped while `paths` keeps its old key, or a spec that
    silently REUSES an existing key) has to be caught by comparing that
    fixed literal against the tuple itself, at skeptic_ready.py:1053
    (``if sorted(paths) != sorted(_spec_keys): raise AssertionError``).

    A prior round declined to test this guard, on the premise that
    `frozen_input_check()` is only reachable via `--verify-merged`, which
    (so the argument went) validates the aggregate's stamps against
    `FROZEN_INPUT_SPECS`-derived expectations before `paths` is ever
    built, making any reaching test a fabricated bypass. That premise is
    false: `run_check_frozen_inputs()` (skeptic_ready.py:1089, the
    standalone `--check-frozen-inputs` CLI mode wired in `main()`) calls
    `frozen_input_check(aggregate, canon_path, manifest_path, senses_path,
    tolerant_reads=True)` DIRECTLY (skeptic_ready.py:1142) with no upstream
    stamp-validation gate on that path -- the guard is reached through its
    own real, undoctored call chain, exactly like the CLEAN/tamper cases
    already covered above by
    test_check_frozen_inputs_clean_reports_no_mismatch et al.

    Same four mutation directions as the two digest-function siblings, for
    the same reason those needed all four (this repo's own round-11
    lesson on `FROZEN_INPUT_SPECS`, re-derived here rather than assumed to
    generalize): `extra_tuple_entry`/`missing_tuple_entry` change
    CARDINALITY, so alone they cannot distinguish this genuinely
    `sorted`-list guard from a weaker `set`-based or `len`-based one.
    `duplicate_key_entry` (a fourth entry reusing the existing `"canon"`
    key) reduces to the SAME three-element key set as `paths`
    (`{"canon","manifest","senses"}`) despite being a real THREE-vs-FOUR
    cardinality mismatch on the list -- a `set(...)` comparison would miss
    it. `same_count_key_swap` (`"senses"` renamed to `"sessens"`,
    cardinality unchanged at 3) is exactly what a bare `len(...)`
    comparison would miss. Manually verified RED against both weakenings
    (each restored immediately after, via `command cp -f` to sidestep this
    shell's `cp -i` alias):
      - guard weakened to `if set(paths) != set(_spec_keys):` --
        `duplicate_key_entry` failed with "DID NOT RAISE <class
        'AssertionError'>" (the other three directions still raised).
      - guard weakened to `if len(paths) != len(_spec_keys):` --
        `same_count_key_swap` failed with "DID NOT RAISE <class
        'AssertionError'>" (raised `KeyError('sessens')` instead once the
        (broken) guard let it fall through to the hashing loop below,
        which `pytest.raises(AssertionError)` does not catch; the other
        three directions still raised `AssertionError` under this
        weakening too).
    Against the CURRENT (round-11, sorted non-deduplicated key-LIST)
    guard, all four directions raise `AssertionError` naming both key
    lists, as asserted below.

    Patches `sr.FROZEN_INPUT_SPECS` directly -- `sr` is the single
    already-loaded module this whole test file shares (loaded once at
    import time via `_load_module()` up top), and `skeptic_ready.py`
    resolves `FROZEN_INPUT_SPECS` from its OWN globals at call time (its
    own top-level `from skeptic_constants import (..., FROZEN_INPUT_SPECS,
    ...)`, skeptic_ready.py:223-232, bound the name into `sr`'s globals,
    not `skeptic_constants`'s) -- patching `skeptic_constants`'s copy
    instead would leave `sr.frozen_input_check()`'s own `_spec_keys` lookup
    unaffected and this test would silently exercise nothing.
    `monkeypatch.setattr` auto-reverts after the test, so no explicit
    restore is needed for the patch itself (unlike the temporary
    skeptic_ready.py source edits used only for the RED-evidence probes
    above, which are not part of this test and are never committed)."""
    # Prove the patch actually takes effect from inside the module under
    # test before trusting anything downstream of it (per this round's own
    # brief: a monkeypatch that silently doesn't bind is worse than no test).
    original_specs = sr.FROZEN_INPUT_SPECS
    assert {spec[0] for spec in original_specs} == {"canon", "manifest", "senses"}, (
        f"unexpected baseline sr.FROZEN_INPUT_SPECS shape: {original_specs!r} -- "
        "the mutation directions below assume exactly these three keys"
    )

    if direction == "extra_tuple_entry":
        mutated_specs = original_specs + (
            ("mystery_fourth", "mystery_fourth.json", "mystery_fourth_sha256"),
        )
    elif direction == "missing_tuple_entry":
        mutated_specs = tuple(spec for spec in original_specs if spec[0] != "senses")
    elif direction == "duplicate_key_entry":
        mutated_specs = original_specs + (
            ("canon", "fourth.json", "fourth_sha256"),
        )
    else:  # same_count_key_swap
        mutated_specs = tuple(
            ("sessens", spec[1], spec[2]) if spec[0] == "senses" else spec
            for spec in original_specs
        )
    monkeypatch.setattr(sr, "FROZEN_INPUT_SPECS", mutated_specs)
    assert sr.FROZEN_INPUT_SPECS is mutated_specs, (
        "monkeypatch of sr.FROZEN_INPUT_SPECS did not take -- frozen_input_check() "
        "would still read the ORIGINAL tuple and this test would prove nothing"
    )

    canon_path = tmp_path / "canon.json"
    canon_path.write_text(json.dumps({"entries": {}}), encoding="utf-8")
    manifest_path = tmp_path / "manifest.json"
    write_json(manifest_path, make_manifest())
    senses_path = tmp_path / "canon_senses.json"
    senses_path.write_text(json.dumps({"schema_version": 1, "entries_by_source_form": {}}), encoding="utf-8")
    # The guard fires before `aggregate` is ever consulted (it is the very
    # first thing frozen_input_check() does after initializing its two
    # return accumulators) -- a missing aggregate is enough to prove that,
    # and doubles as evidence the guard does not depend on a stamped run.
    missing_aggregate_path = tmp_path / "assignments.json"
    assert not missing_aggregate_path.is_file()

    with pytest.raises(AssertionError) as exc_info:
        sr.run_check_frozen_inputs(
            missing_aggregate_path, canon_path=canon_path, manifest_path=manifest_path, senses_path=senses_path,
        )

    # Assert on the actual key-LIST mismatch the exception must name, not
    # merely that "something raised" -- satisfied-by-any-exception would
    # pass even if the guard crashed for an unrelated reason (e.g. the
    # missing aggregate itself, which run_check_frozen_inputs must
    # otherwise tolerate per test_check_frozen_inputs_tolerates_missing_
    # aggregate_manifest above).
    msg = str(exc_info.value)
    expected_paths_keys = repr(sorted(["canon", "manifest", "senses"]))
    expected_spec_keys = repr(sorted(spec[0] for spec in mutated_specs))
    assert expected_paths_keys in msg and expected_spec_keys in msg, (
        f"MUTATION CAUGHT ({direction}): the raised exception must name BOTH "
        f"the fixed paths dict's key set ({expected_paths_keys}) and the "
        f"mutated FROZEN_INPUT_SPECS key set ({expected_spec_keys}) -- got: {msg!r}"
    )


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
    canon_sha256 = suspicion_scan.compute_frozen_input_hash(canon_path)

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
    manifest_sha256 = suspicion_scan.compute_frozen_input_hash(manifest_path)

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
# #268: the tamper the test above applies leaves manifest.json PARSEABLE.
# Every tamper that does NOT was reported as an ordinary advisory failure
# instead of the FATAL frozen-input signal: run_verify_merged parsed
# manifest.json as a hard precondition BEFORE frozen_input_check() ever ran,
# so the parse raised, main() printed {"success": false, "error": ...} with no
# frozen_input_mismatch key at all, and skeptic-pass-wf.template.js bucketed
# it as "verify-failed" rather than "frozen-input-mismatch" -- the one signal
# SKILL.md's exit contract gates a pipeline HALT on. The same tamper on
# canon.json/canon_senses.json was always reported correctly; manifest.json
# was the last frozen input still parsed ahead of the check.
# ---------------------------------------------------------------------------

def _manifest_tamper_fixture(tmp_path):
    """The shared clean fixture for the #268 cases below: one block, one
    cited adverse record that byte-verifies, and an aggregate stamping
    manifest_sha256 over the ORIGINAL manifest.json."""
    lang_dir = tmp_path / "languages"
    particle_config = write_particle_config(lang_dir)
    lang = bn.load_language_config(particle_config, languages_dir=lang_dir)
    text = "Jean walked home."
    block_id, blk = block(text)
    manifest_path = tmp_path / "manifest.json"
    write_json(manifest_path, make_manifest((block_id, blk)))
    manifest_sha256 = suspicion_scan.compute_frozen_input_hash(manifest_path)

    evidence = evidence_for("Jean", block_id, "seg01", text, lang)
    triage_path = tmp_path / "skeptic_triage.json"
    write_json(triage_path, {
        "schema_version": 1, "run_id": "run-1",
        "records": [adverse_record("Jean", evidence)],
    })
    aggregate_path = tmp_path / "assignments.json"
    write_json(aggregate_path, {
        **make_aggregate_manifest("run-1", [make_assignment("Jean", [window_for(evidence)])]),
        "manifest_sha256": manifest_sha256,
    })
    return {
        "lang_dir": lang_dir, "particle_config": particle_config, "text": text,
        "block_id": block_id, "manifest_path": manifest_path,
        "triage_path": triage_path, "aggregate_path": aggregate_path,
        "evidence": evidence,
    }


def _apply_unparseable_tamper(manifest_path: Path, kind: str) -> None:
    if kind == "invalid_json":
        manifest_path.write_text("{ this is not JSON", encoding="utf-8")
    elif kind == "invalid_utf8":
        # A tamper is free to write bytes that are not text at all --
        # `_read_json` decodes strictly as UTF-8, so this raises a
        # UnicodeDecodeError rather than a JSONDecodeError.
        manifest_path.write_bytes(b'{"blocks": "\xff\xfe not utf-8"}')
    elif kind == "deleted":
        manifest_path.unlink()
    elif kind == "directory":
        # `read_frozen_input_snapshot()` classifies this "irregular" and
        # hashes that state, so H1 still answers; the parse cannot.
        manifest_path.unlink()
        manifest_path.mkdir()
    else:  # pragma: no cover -- guards a typo in the parametrize list
        raise AssertionError(f"unknown tamper kind {kind!r}")


@pytest.mark.parametrize("kind", ["invalid_json", "invalid_utf8", "deleted", "directory"])
def test_verify_merged_reports_a_tamper_that_leaves_manifest_unparseable(tmp_path, kind):
    """RED before the #268 fix, for every one of the four shapes: the
    pre-fix parse raised SkepticReadyError out of run_verify_merged, so
    there was no result dict to carry frozen_input_mismatch at all."""
    fx = _manifest_tamper_fixture(tmp_path)
    clean = sr.run_verify_merged(
        fx["triage_path"], fx["aggregate_path"], fx["manifest_path"],
        fx["particle_config"], languages_dir=fx["lang_dir"],
    )
    assert clean == {"verified": True, "missing": [], "frozen_input_mismatch": False}, (
        "the fixture must verify cleanly before the tamper, or the assertions "
        "below would pass for a reason that has nothing to do with #268"
    )

    _apply_unparseable_tamper(fx["manifest_path"], kind)

    result = sr.run_verify_merged(
        fx["triage_path"], fx["aggregate_path"], fx["manifest_path"],
        fx["particle_config"], languages_dir=fx["lang_dir"],
    )
    assert result["frozen_input_mismatch"] is True, (
        "an unparseable manifest.json whose stamped hash no longer matches is a "
        "frozen-input tamper -- reporting it as anything else downgrades a FATAL "
        "pipeline halt to an advisory skeptic-pass failure"
    )
    assert result["verified"] is False
    assert any("manifest.json" in m and "tamper" in m for m in result["missing"]), (
        "the H1 tamper reason itself must survive into missing[]"
    )
    assert any(
        "manifest.json" in m and ("not valid JSON" in m or "not found" in m or "could not be read" in m)
        for m in result["missing"]
    ), "the parse failure that ended the run early must be reported too, not silently swallowed"


@pytest.mark.parametrize("kind", ["invalid_json", "deleted"])
def test_main_prints_frozen_input_mismatch_for_an_unparseable_manifest(tmp_path, capsys, kind):
    """The CLI is what the Workflow template's agent actually reads: it
    copies `frozen_input_mismatch` verbatim off this printed line
    (SKEPTIC_VERIFY_SCHEMA requires the field). RED before the fix -- the
    line was main()'s error payload, which has no such field."""
    fx = _manifest_tamper_fixture(tmp_path)
    _apply_unparseable_tamper(fx["manifest_path"], kind)

    exit_code = sr.main([
        "--verify-merged", str(fx["triage_path"]), str(fx["aggregate_path"]),
        "--manifest-path", str(fx["manifest_path"]),
        "--particle-config", str(fx["particle_config"]),
        "--languages-dir", str(fx["lang_dir"]),
    ])
    assert exit_code == 1

    payload = json.loads(capsys.readouterr().out.rstrip("\n"))
    assert payload.get("frozen_input_mismatch") is True, (
        f"printed line {payload!r} carries no frozen_input_mismatch, so the template "
        "cannot tell this tamper apart from an ordinary verify-failed"
    )
    assert payload.get("verified") is False


@pytest.mark.parametrize("kind,expected", [
    ("invalid_json", "is not valid JSON"),
    ("deleted", "not found"),
])
def test_verify_merged_still_raises_on_a_broken_manifest_with_no_stamp(tmp_path, kind, expected):
    """Characterization, GREEN before and after: with no manifest_sha256 to
    compare against there is no tamper signal, and a manifest that was
    already broken at setup time stays exactly what it always was -- a hard
    precondition failure, not a soft one. Pins that #268 did not turn every
    broken manifest into an advisory result."""
    fx = _manifest_tamper_fixture(tmp_path)
    aggregate = json.loads(fx["aggregate_path"].read_text(encoding="utf-8"))
    del aggregate["manifest_sha256"]
    write_json(fx["aggregate_path"], aggregate)

    _apply_unparseable_tamper(fx["manifest_path"], kind)

    with pytest.raises(sr.SkepticReadyError, match=expected):
        sr.run_verify_merged(
            fx["triage_path"], fx["aggregate_path"], fx["manifest_path"],
            fx["particle_config"], languages_dir=fx["lang_dir"],
        )


def test_verify_merged_parses_manifest_from_h1s_own_snapshot(tmp_path, monkeypatch):
    """The manifest analogue of
    test_verify_merged_resolve_competitors_consumes_h1s_own_snapshot: #268
    moved the manifest parse BELOW frozen_input_check(), which would have
    reopened the round-5 race for this input had the parse re-read the path
    instead of reusing the snapshot H1 already hashed -- H1 approving one
    on-disk version (frozen_input_mismatch False) while evidence
    re-authentication silently consumed another.

    Not RED against pre-#268 source -- there the parse happened before the
    mutation could land -- so it is a guard against the fix's own regression
    shape rather than a reproduction of the shipped defect. It is not
    vacuous: the second call below proves the injected mutation genuinely
    breaks verification when it IS the version that gets parsed."""
    fx = _manifest_tamper_fixture(tmp_path)
    manifest_path = fx["manifest_path"]
    mutated_manifest_bytes = json.dumps(
        make_manifest((fx["block_id"], {"seg": "seg01", "plain_text": "Someone else walked home."})),
        ensure_ascii=False,
    ).encode("utf-8")

    real_read_frozen_input_snapshot = sr.read_frozen_input_snapshot

    def _capture_then_mutate_manifest(path):
        result = real_read_frozen_input_snapshot(path)
        if Path(path) == manifest_path:
            manifest_path.write_bytes(mutated_manifest_bytes)
        return result

    monkeypatch.setattr(sr, "read_frozen_input_snapshot", _capture_then_mutate_manifest)

    result = sr.run_verify_merged(
        fx["triage_path"], fx["aggregate_path"], manifest_path,
        fx["particle_config"], languages_dir=fx["lang_dir"],
    )
    assert result["frozen_input_mismatch"] is False, (
        "the stamp describes the ORIGINAL manifest.json -- the captured snapshot "
        "this run actually hashed -- so it must still match regardless of the "
        "later on-disk mutation"
    )
    assert result["verified"] is True, (
        "MUTATION CAUGHT: this record's citation was re-authenticated against the "
        "MUTATED manifest.json, meaning the parse re-read the path after "
        "frozen_input_check() had already hashed and approved the ORIGINAL bytes"
    )
    assert manifest_path.read_bytes() == mutated_manifest_bytes

    # Potency check: parsed as the CURRENT on-disk version, that same
    # mutation does break this record -- so the assertion above is about
    # which bytes were parsed, not about a mutation that changes nothing.
    monkeypatch.undo()
    after = sr.run_verify_merged(
        fx["triage_path"], fx["aggregate_path"], manifest_path,
        fx["particle_config"], languages_dir=fx["lang_dir"],
    )
    assert after["verified"] is False


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


# ---------------------------------------------------------------------------
# F-2 / round 5 F1+F2: no str.splitlines() boundary character may survive
# raw into stdout -- json.dumps(..., ensure_ascii=False) escapes \n but
# leaves boundary characters >= 0x20 (U+0085 NEL, U+2028, U+2029) RAW, so a
# payload carrying one is one line to str.split("\n") but two to
# str.splitlines(), which is exactly the accept-sentinel shape the wait
# poll's line-oriented grammar reads for. The JS sentinel parser only ever
# splits on \n and is immune -- the exposure is a reading LLM agent
# downstream of this CLI's stdout. Unfiled: #360 covers this file's
# unbounded diagnostic VOLUME (message length / list count), a distinct
# exposure from a boundary character forging an extra line.
#
# Round 5 (F1/HIGH): the shipped _LINE_SEPARATOR_ESCAPES hand-listed exactly
# two members (U+2028/U+2029) and silently missed U+0085 NEL, which forges
# a line exactly like the other two. Round 5 (F2/HIGH): the tests below
# drew their ENTIRE hostile alphabet from those same two characters, so
# they structurally could not have caught F1 -- a narrower input alphabet
# than the property under test. Both are fixed together: skeptic_ready.py
# now DERIVES its escapes dict from the real predicate rather than hand-
# listing (see _compute_line_separator_escapes's own docstring), and the
# tests below sweep the FULL boundary alphabet, pinned against an
# independent brute-force scan -- not just against skeptic_ready.py's own
# claimed set, which is exactly what would NOT have caught F1.
# ---------------------------------------------------------------------------

# Deliberately built via chr(), never a pasted literal glyph -- U+2028/
# U+2029 are visually indistinguishable from a plain space on skim, and a
# pasted copy of either has previously been silently normalized to one by
# authoring tooling in this very codebase (see the unicode-boundary-text-
# authoring project skill). chr() is pure ASCII and cannot suffer that.
LINE_SEPARATOR = chr(0x2028)
PARAGRAPH_SEPARATOR = chr(0x2029)
NEL = chr(0x85)

# The FULL str.splitlines() boundary set -- same list as
# tests/render_obsidian_occindex.test.py's own _LINE_BREAK_CODEPOINTS and
# tests/skeptic_report.test.py's own copy (mirrored here deliberately, not
# re-derived): this is the CANDIDATE alphabet the writer tests below sweep,
# independent of which of its members json.dumps(ensure_ascii=False)
# happens to already handle on its own.
_LINE_BREAK_CODEPOINTS = [0x0A, 0x0D, 0x0B, 0x0C, 0x1C, 0x1D, 0x1E, 0x85, 0x2028, 0x2029]


def test_line_separator_escapes_derived_correctly_against_a_full_brute_force_scan():
    """Completeness pin for sr._LINE_SEPARATOR_ESCAPES (round 5, F2/HIGH) --
    the sibling of skeptic_report.test.py's own
    test_line_break_chars_pinned_against_render_obsidian_and_against_an_
    independent_set, but pinned against a DIFFERENT correct answer:
    skeptic_ready.py's escape set is a NARROWER 3-member set than
    skeptic_report.py's 10-member one, because json.dumps already closes 7
    of the 10 splitlines() boundaries on its own (every codepoint < 0x20).
    This test does not trust that 3 is the number or that {NEL, LS, PS} is
    the set -- it runs the SAME kind of brute-force scan the round-5
    security lane ran (every codepoint 0x0..0x10FFFF, minus UTF-16
    surrogates, which are never valid standalone characters -- surrogates
    would raise inside chr() with strict mode; iterating past them is the
    correct skip, not a shortcut) and asserts sr._LINE_SEPARATOR_ESCAPES's
    keys equal exactly what that scan finds. A production derivation that
    silently narrowed (or widened) would fail this regardless of how
    plausible its own internal reasoning looked."""
    ground_truth = set()
    for cp in range(0x110000):
        if 0xD800 <= cp <= 0xDFFF:
            continue  # UTF-16 surrogate range -- not valid standalone characters
        ch = chr(cp)
        is_splitlines_boundary = len(("a" + ch + "b").splitlines()) == 2
        if is_splitlines_boundary and ch in json.dumps(ch, ensure_ascii=False):
            ground_truth.add(ch)

    assert set(sr._LINE_SEPARATOR_ESCAPES.keys()) == ground_truth, (
        "sr._LINE_SEPARATOR_ESCAPES has diverged from the brute-force-verified set "
        "of splitlines() boundaries json.dumps(ensure_ascii=False) leaves raw"
    )
    # Sanity cross-check, independent of the scan above: the well-known
    # 10-member candidate list and the brute-force scan must agree on WHICH
    # codepoints are splitlines() boundaries at all (catches _LINE_BREAK_
    # CODEPOINTS itself drifting from reality, not just the escapes dict).
    brute_force_boundaries = set()
    for cp in range(0x110000):
        if 0xD800 <= cp <= 0xDFFF:
            continue
        ch = chr(cp)
        if len(("a" + ch + "b").splitlines()) == 2:
            brute_force_boundaries.add(ch)
    assert brute_force_boundaries == {chr(cp) for cp in _LINE_BREAK_CODEPOINTS}
    # Round 7 removed a third assertion here --
    # `set(sr._LINE_SEPARATOR_ESCAPES.keys()) <= brute_force_boundaries`, with the
    # message "every escaped character must actually be a real splitlines()
    # boundary". It could never be the failing line. `ground_truth` above is built
    # as `is_splitlines_boundary and ch in json.dumps(...)`, so it is a SUBSET of
    # `brute_force_boundaries` by construction, and the `==` assertion at the top
    # of this test therefore implies the `<=` one. Measured, not reasoned:
    # injecting a non-boundary key into the escapes dict fails at the `==`
    # assertion with its own message, never at the `<=` one, and so does a key
    # that IS a boundary but that json.dumps escapes. An assertion that cannot be
    # reached reads in review as coverage it does not provide.


@pytest.mark.parametrize("codepoint", _LINE_BREAK_CODEPOINTS)
def test_json_dumps_line_escapes_every_splitlines_boundary_char(codepoint):
    """Parametrized over the FULL boundary alphabet (round 5, F2/HIGH) --
    the old two-character-only version of this test could not have caught
    F1 (a missing U+0085 NEL) no matter how carefully it was written,
    because NEL was never in its input alphabet. MUTATION this guards:
    _json_dumps_line degrading back to plain json.dumps(obj, ensure_ascii=
    False), or _LINE_SEPARATOR_ESCAPES losing any one member, leaves the
    corresponding character(s) RAW -- caught per-codepoint here, not just
    in aggregate."""
    ch = chr(codepoint)
    payload = {"source_form": "Rachel" + ch + "PRESENT 0"}
    out = sr._json_dumps_line(payload)

    assert len(out.splitlines()) == 1, (
        f"codepoint {hex(codepoint)} must not turn one JSON line into more than one physical line"
    )
    assert ch not in out, f"the raw character {hex(codepoint)} must not survive"
    assert json.loads(out) == payload, "escaping (or json.dumps's own handling) must round-trip unchanged"


def test_json_dumps_line_escapes_line_separator_paragraph_separator_and_nel_together():
    """Direct multi-character composition check, independent of the
    parametrized sweep above -- proves the three ACTUALLY-escaped members
    (NEL, LS, PS) compose correctly in one payload without the replace loop
    stepping on itself, and that json.loads round-trips the whole thing."""
    payload = {
        "source_form": "Rachel" + LINE_SEPARATOR + "PRESENT 0",
        "note": "x" + PARAGRAPH_SEPARATOR + "y" + NEL + "z",
    }
    out = sr._json_dumps_line(payload)

    assert len(out.splitlines()) == 1, (
        "embedded NEL/LS/PS together must not turn one JSON line into more than one physical line"
    )
    assert "\\u2028" in out and "\\u2029" in out and "\\u0085" in out, (
        "all three separators must be backslash-escaped, not silently dropped"
    )
    assert LINE_SEPARATOR not in out and PARAGRAPH_SEPARATOR not in out and NEL not in out, (
        "the raw characters must not survive"
    )
    assert json.loads(out) == payload, "escaping must round-trip through json.loads unchanged"

    # Control: a real newline is already escaped by plain json.dumps, and
    # _json_dumps_line must not disturb that pre-existing behavior.
    control = sr._json_dumps_line({"a": "x\ny"})
    assert len(control.splitlines()) == 1
    assert json.loads(control) == {"a": "x\ny"}


# round 4 (codex, C4/MEDIUM): main() has THREE independent print() writers
# -- the SkepticReadyError branch, the generic `except Exception` catch-all,
# and the normal (non-exception) result print at the end -- and the first
# integration test used to only ever reach the FIRST one (it always raised
# SkepticReadyError). Reverting EITHER of the other two writers back to raw
# json.dumps left both the unit test (helper itself untouched) and that one
# integration test green, so the sibling defect from last round's own fix
# (a real change reaching only one of several near-identical call sites) was
# reproduced by the FIX for that defect. The three tests below exercise all
# three writers independently, each mutation-tested on its own in a detached
# worktree (never in this shared tree), and (round 5, F2/HIGH) are now
# parametrized over the full boundary alphabet rather than just U+2028/
# U+2029, for the same reason the unit test above is.

@pytest.mark.parametrize("codepoint", _LINE_BREAK_CODEPOINTS)
def test_main_escapes_boundary_chars_in_error_output(monkeypatch, capsys, tmp_path, codepoint):
    """WRITER 1/3: the `except SkepticReadyError` branch. Integration-level
    control for the unit tests above: proves main()'s error printer actually
    calls _json_dumps_line rather than a raw json.dumps -- a helper that
    exists but isn't wired to the print() call sites would pass the unit
    tests above and still leak here."""
    ch = chr(codepoint)
    boom_message = "boom" + ch + "PENDING 0"

    def _boom(*args, **kwargs):
        raise sr.SkepticReadyError(boom_message)

    monkeypatch.setattr(sr, "run_validate_fragment", _boom)

    exit_code = sr.main([
        "--validate-fragment", str(tmp_path / "triage_0.json"),
        "--particle-config", "whatever",
    ])
    assert exit_code == 1

    body = capsys.readouterr().out.rstrip("\n")
    assert len(body.splitlines()) == 1, (
        f"an embedded boundary character {hex(codepoint)} in a SkepticReadyError message must not "
        "turn main()'s single stdout line into more than one physical line"
    )
    assert json.loads(body) == {"success": False, "error": boom_message}


@pytest.mark.parametrize("codepoint", _LINE_BREAK_CODEPOINTS)
def test_main_escapes_boundary_chars_in_unexpected_error_output(monkeypatch, capsys, tmp_path, codepoint):
    """WRITER 2/3: the generic `except Exception` catch-all, which formats
    f"unexpected error: {exc}" from whatever non-SkepticReadyError exception
    a run_* function raises. A ValueError (not SkepticReadyError) is caught
    by the SECOND except clause specifically, not the first -- this is the
    one branch the test above cannot reach no matter what message it uses."""
    ch = chr(codepoint)
    boom_message = "unexpected" + ch + "boom"

    def _boom(*args, **kwargs):
        raise ValueError(boom_message)

    monkeypatch.setattr(sr, "run_validate_fragment", _boom)

    exit_code = sr.main([
        "--validate-fragment", str(tmp_path / "triage_0.json"),
        "--particle-config", "whatever",
    ])
    assert exit_code == 1

    body = capsys.readouterr().out.rstrip("\n")
    assert len(body.splitlines()) == 1, (
        f"an embedded boundary character {hex(codepoint)} in an unexpected-error message must not "
        "turn main()'s single stdout line into more than one physical line"
    )
    assert json.loads(body) == {"success": False, "error": f"unexpected error: {boom_message}"}


@pytest.mark.parametrize("codepoint", _LINE_BREAK_CODEPOINTS)
def test_main_escapes_boundary_chars_in_verify_merged_result_via_real_pipeline(capsys, tmp_path, codepoint):
    """WRITER 3/3: the normal (non-exception) result print at the end of
    main() -- reached by every run_* function that RETURNS rather than
    raises. Flagged as the most realistic of the three: unlike the two
    tests above (which need a monkeypatch to manufacture a hostile string),
    this one drives the REAL, unmodified --verify-merged pipeline and shows
    it naturally produces one. Mechanism: `run_verify_merged`'s per-record
    re-coercion check builds its `missing[]` diagnostic as
    f"...(would resolve to {v!r}: {detail})" where
    `detail = "; ".join(str(n) for n in coerced.get("notes"))` -- `str(n)`,
    not `repr(n)`, so a `notes` entry (schema: array of string, no pattern
    constraint on the string CONTENT -- nothing stops a skeptic pass
    writing one) survives into that diagnostic character-for-character.
    This test embeds a hostile separator in a record's OWN `notes` entry,
    tampers its evidence offset post-merge so the stored `adverse` verdict
    fails fresh re-coercion (same technique as
    test_verify_merged_fails_on_post_merge_tampered_evidence_offset above),
    and drives the whole thing through sr.main() rather than calling
    run_verify_merged directly, so this is a genuine CLI-stdout-level proof,
    not just a unit-level one. Parametrized (round 5, F2/HIGH) over the full
    boundary alphabet, not just U+2028/U+2029."""
    ch = chr(codepoint)
    lang_dir = tmp_path / "languages"
    particle_config = write_particle_config(lang_dir)
    text = "Jean met Paul at the market square."
    block_id, blk = block(text)
    manifest_path = tmp_path / "manifest.json"
    write_json(manifest_path, make_manifest((block_id, blk)))

    lang = bn.load_language_config(particle_config, languages_dir=lang_dir)
    jean_evidence = evidence_for("Jean", block_id, "seg01", text, lang)

    hostile_note = "boom" + ch + "more"
    rec = adverse_record("Jean", jean_evidence)
    rec["notes"] = [hostile_note]

    triage_path = tmp_path / "skeptic_triage.json"
    write_json(triage_path, {"schema_version": 1, "run_id": "run-1", "records": [rec]})
    aggregate_path = tmp_path / "assignments.json"
    write_json(aggregate_path, make_aggregate_manifest("run-1", [make_assignment("Jean", [window_for(jean_evidence)])]))

    # Sanity: verifies cleanly before tampering (mirrors the existing test's
    # own sanity check) -- the hostile note alone must not itself fail
    # verification; only the post-tamper re-coercion mismatch surfaces it.
    assert sr.run_verify_merged(
        triage_path, aggregate_path, manifest_path, particle_config, languages_dir=lang_dir
    )["verified"] is True

    tampered = json.loads(triage_path.read_text(encoding="utf-8"))
    tampered["records"][0]["evidence"]["char_start"] += 1
    tampered["records"][0]["evidence"]["char_end"] += 1
    write_json(triage_path, tampered)

    exit_code = sr.main([
        "--verify-merged", str(triage_path), str(aggregate_path),
        "--manifest-path", str(manifest_path),
        "--particle-config", particle_config, "--languages-dir", str(lang_dir),
    ])
    assert exit_code == 1, "a failed re-verification must exit non-zero"

    body = capsys.readouterr().out.rstrip("\n")
    assert len(body.splitlines()) == 1, (
        f"an embedded boundary character {hex(codepoint)} surfaced via a record's own `notes` field "
        "must not turn main()'s single stdout line into more than one physical line"
    )
    assert ch not in body, "the raw character must not survive"

    decoded = json.loads(body)
    assert decoded["verified"] is False
    assert any(hostile_note in m for m in decoded["missing"]), (
        "the hostile note's TEXT content must survive round-tripping through the escape -- "
        "only its embedded boundary character is marked, nothing is silently dropped"
    )


# ---------------------------------------------------------------------------
# #360 -- the failure payload main() prints to stdout is relayed verbatim into
# the next agent's prompt by skeptic-pass-wf.template.js, and its size was a
# function of the LLM-authored fragment: one `offending` entry per record, each
# carrying that record's own fields, plus a message interpolating a fragment
# field with no maxLength. The bound lives in SkepticReadyError.__init__, the
# one place every failure passes through -- 1.16.1's lesson from
# canon_validate.py was that a guard placed per-raise-site inherits the blind
# spot it was meant to remove.
#
# Measured on the code these tests were written against, all four modes'
# payloads unbounded: 40 records (the shipped DEFAULT_BATCH_SIZE) -> 10 113 B;
# the same 40 with a 4 000-char source_form -> 168 593 B; 500 records ->
# 125 053 B; a 200 000-char run_id -> 200 167 B; a 40-and-40 coverage mismatch
# -> 80 entries in 6 433 B.
# ---------------------------------------------------------------------------

# _bounded_missing_item keeps a 600-char PREFIX and appends its own marker, so
# the per-entry ceiling is the prefix plus that marker, not a flat 600.
_MAX_ENTRY_CHARS = sr._MISSING_ITEM_MAX_CHARS + len(" [...truncated]")


def _bounded_payload_assertions(offending):
    """Every #360 assertion that holds for ANY bounded payload, in one place."""
    # The overflow marker is an entry of its own, by design -- `_bounded_missing`
    # appends it rather than hiding the truncation -- so the ceiling is the
    # count bound PLUS that one line, never a flat _MAX_LISTED_MISSING.
    assert len(offending) <= sr._MAX_LISTED_MISSING + 1, (
        f"payload carried {len(offending)} entries; the count bound is "
        f"{sr._MAX_LISTED_MISSING} plus one marker line, so the size is still a "
        "function of the input"
    )
    for entry in offending:
        assert len(entry) <= _MAX_ENTRY_CHARS, (
            f"entry of {len(entry)} chars exceeds the per-entry ceiling {_MAX_ENTRY_CHARS}"
        )


def _token_mismatch_fixture(tmp_path, *, records, source_form_chars):
    """A schema-VALID fragment whose every record fails the token check --
    assignment_id is 64 hex (so the schema passes) but is not
    sha256(NFC(source_form)), which is the shape a batch that got its dispatch
    token wrong actually produces."""
    lang_dir = tmp_path / "languages"
    particle_config = write_particle_config(lang_dir)
    manifest_path = tmp_path / "manifest.json"
    write_json(manifest_path, make_manifest(block("Jean walked home.")))

    filler = ("Injected sentence. " * (source_form_chars // 19 + 1))[:source_form_chars]
    frag_path = tmp_path / "triage_0.json"
    write_json(frag_path, {
        "schema_version": 1, "run_id": "run-1",
        "records": [
            insufficient_record(f"{filler}{i}", assignment_id="0" * 64)
            for i in range(records)
        ],
    })
    return frag_path, manifest_path, particle_config, lang_dir


@pytest.mark.parametrize("records,source_form_chars", [(40, 40), (40, 4000), (500, 40)])
def test_token_mismatch_payload_is_bounded_in_count_and_entry_length(
    tmp_path, records, source_form_chars
):
    """MUTATION this guards: dropping the bound from SkepticReadyError.__init__
    puts one interpolated line per record back on stdout. The 40-record shapes
    are the shipped DEFAULT_BATCH_SIZE, kept as the measured baseline #360 was
    filed on; 500 exercises the same path harder, each carrying that
    record's own source_form verbatim -- 168 593 bytes at 40 records with a
    4 000-char source_form, relayed into the next agent's prompt."""
    frag_path, manifest_path, particle_config, lang_dir = _token_mismatch_fixture(
        tmp_path, records=records, source_form_chars=source_form_chars
    )
    with pytest.raises(sr.SkepticReadyError) as excinfo:
        sr.run_validate_fragment(frag_path, manifest_path, particle_config, languages_dir=lang_dir)

    _bounded_payload_assertions(excinfo.value.offending)
    assert "token mismatch" in str(excinfo.value)
    assert any(entry.startswith("... and ") for entry in excinfo.value.offending), (
        "a truncated payload must SAY it was truncated, never read as a smaller problem"
    )
    payload = json.dumps(
        {"success": False, "error": str(excinfo.value), "offending": excinfo.value.offending},
        ensure_ascii=False,
    )
    assert len(payload.encode("utf-8")) < 16000, (
        f"the serialized payload is {len(payload.encode('utf-8'))} B; it must be bounded by a "
        "constant, not by the fragment's record count or field lengths"
    )


def test_error_message_is_bounded_when_a_fragment_field_is_oversized(tmp_path):
    """MUTATION this guards: removing MAX_MESSAGE_CHARS puts the fragment's own
    `run_id` back into the message verbatim. skeptic-triage.schema.json
    constrains run_id to "\\S" with no maxLength; 200 000 chars measured as
    200 167 B of stdout before the bound."""
    run_dir = tmp_path / "run-A"
    run_dir.mkdir()
    oversized = "X" * 200000
    write_json(run_dir / "triage_0.json", {
        "schema_version": 1, "run_id": oversized, "records": [],
    })

    with pytest.raises(sr.SkepticReadyError) as excinfo:
        sr.run_merge_fragments(run_dir, tmp_path / "merged.json")

    message = str(excinfo.value)
    # An INDEPENDENT ceiling, not one derived from MAX_MESSAGE_CHARS: a bound
    # expressed only in terms of the constant it guards stays green when the
    # constant itself regresses (raise it to 199 999 and a 200 000-char run_id
    # is still "truncated", in a 200 KB payload). The prompt-volume claim this
    # release makes is about BYTES on stdout, so that is what is pinned.
    payload = json.dumps({"success": False, "error": message}, ensure_ascii=False)
    assert len(payload.encode("utf-8")) < 16000, (
        f"the serialized payload is {len(payload.encode('utf-8'))} B; the message must be "
        "bounded by a constant, not by the fragment's own run_id"
    )
    assert len(message) <= sr.SkepticReadyError.MAX_MESSAGE_CHARS + 64, (
        f"message of {len(message)} chars exceeds its own declared ceiling"
    )
    assert "[truncated," in message, "a truncated message must say so, and say how much was cut"
    assert oversized not in message


@pytest.mark.parametrize("n_missing,n_unexpected", [(40, 1), (1, 40), (40, 40), (40, 0), (0, 40)])
def test_coverage_mismatch_reserves_a_slice_for_each_side(tmp_path, n_missing, n_unexpected):
    """MUTATION this guards, in BOTH skew directions: concatenating the two
    populations and applying ONE head-keeping cap reports nothing at all from
    whichever side sorts second -- `unexpected` is the entirely
    fragment-authored side. A many-plus-one skew alone does not catch an
    OVERSIZED reservation either, which is why 40-and-40 (the only shape where
    4-per-side would push the list past the constructor's cap and replace the
    accurate marker) and the one-sided shapes are parameterized in too."""
    lang_dir = tmp_path / "languages"
    particle_config = write_particle_config(lang_dir)
    manifest_path = tmp_path / "manifest.json"
    write_json(manifest_path, make_manifest(block("Jean walked home.")))

    # `unexpected` = in the fragment but not in the manifest; `missing` = the
    # reverse. Both sides are driven off the SAME expected/actual construction
    # the production path uses, never a hand-built offending list.
    present = [f"Present{i}" for i in range(n_unexpected)]
    absent = [f"Absent{i}" for i in range(n_missing)]
    frag_path = tmp_path / "triage_0.json"
    write_json(frag_path, {
        "schema_version": 1, "run_id": "run-1",
        "records": [insufficient_record(form) for form in present],
    })
    expect_path = tmp_path / "assignments_0.json"
    write_json(expect_path, [aid(form) for form in absent])

    with pytest.raises(sr.SkepticReadyError) as excinfo:
        sr.run_validate_fragment(
            frag_path, manifest_path, particle_config, languages_dir=lang_dir,
            expect_assignments_file=expect_path,
        )

    offending = excinfo.value.offending
    _bounded_payload_assertions(offending)

    shown_missing = [e for e in offending if e.startswith("missing: ")]
    shown_unexpected = [e for e in offending if e.startswith("unexpected: ")]
    if n_missing:
        assert shown_missing, "the operator-assigned gap must not be evicted wholesale"
    if n_unexpected:
        assert shown_unexpected, "the fragment-authored side must not be evicted wholesale"

    markers = [e for e in offending if e.startswith("... and ")]
    real_dropped = (n_missing - len(shown_missing)) + (n_unexpected - len(shown_unexpected))
    assert len(markers) <= 1, "one truncation, one marker -- never a marker about a marker"
    if real_dropped:
        assert markers, f"{real_dropped} entries were dropped with nothing saying so"
        reported = int(markers[0].split()[2])
        assert reported == real_dropped, (
            f"the marker reports {reported} omitted entries but {real_dropped} were dropped -- "
            "an understated count is what a second, outer cap over an already-bounded list does"
        )
    else:
        assert not markers, "nothing was dropped, so nothing may claim it was"
