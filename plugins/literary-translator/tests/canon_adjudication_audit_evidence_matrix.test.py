"""tests/canon_adjudication_audit_evidence_matrix.test.py -- RFC #215 Phase 1
(1e) coverage for canon_adjudication_audit.py's mandatory evidence
verification: the audit's `run_check` (`:1051`-ish, CLI-wired at the bottom
of `main()`) actually reads manifest.json + resolves --particle-config and
forwards BOTH into evidence_verify.verify_senses(), never dropping either
or defaulting to a no-config/no-manifest state. See tests/
canon_adjudication_audit_homonym_split.test.py for the category-5/
collapsed_split/canon_absent_with_senses/--advisory/--senses-path coverage
that does NOT depend on evidence correctness -- every fixture there uses
genuinely valid evidence so evidence_unverified never fires as a
confound. This file isolates the evidence dimension itself.

Fixture/staging conventions mirror canon_adjudication_audit_homonym_split.
test.py exactly: the audit script is staged via Infra's sanctioned
tests/_senses_fixture.py::stage_consumer() (which also brings
canon_senses.py + its schema), plus this audit script's own extra
bootstrap_names.py/occ_index.py/evidence_verify.py dependency closure.
"""
import hashlib
import json
import shutil
import subprocess
import sys
import importlib.util
from pathlib import Path

import jsonschema
import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_SRC_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets" / "scripts"
SCHEMAS_SRC_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets" / "schemas"
LANGUAGES_SRC_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets" / "languages"

SCRIPT_SRC = SCRIPTS_SRC_DIR / "canon_adjudication_audit.py"
SUMMARY_SCHEMA_PATH = SCHEMAS_SRC_DIR / "canon-adjudication-audit-summary.schema.json"
SUMMARY_SCHEMA = json.loads(SUMMARY_SCHEMA_PATH.read_text(encoding="utf-8"))
assert (LANGUAGES_SRC_DIR / "fr.json").is_file()

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))
from _senses_fixture import stage_consumer  # noqa: E402

# canon_adjudication_audit.py's own EXTRA dependency closure beyond canon_senses.py (which
# stage_consumer() already brings) -- see tests/canon_adjudication_audit_homonym_split.
# test.py's module docstring for the full explanation.
_EXTRA_DEP_SCRIPTS = ("bootstrap_names.py", "occ_index.py", "evidence_verify.py")
for _dep in _EXTRA_DEP_SCRIPTS:
    assert (SCRIPTS_SRC_DIR / _dep).is_file(), f"{_dep} not found at {SCRIPTS_SRC_DIR / _dep}"


def _load_module(name: str, path: Path, extra_sys_path: Path):
    sys.path.insert(0, str(extra_sys_path))
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(extra_sys_path))


bn = _load_module("bn_for_evidence_matrix_test", SCRIPTS_SRC_DIR / "bootstrap_names.py", SCRIPTS_SRC_DIR)
oi = _load_module("oi_for_evidence_matrix_test", SCRIPTS_SRC_DIR / "occ_index.py", SCRIPTS_SRC_DIR)
FR_LANG = bn.load_language_config("fr.json", languages_dir=LANGUAGES_SRC_DIR)


def make_durable_root(tmp_path):
    root = tmp_path / "durable_root"
    stage_consumer(root, "canon_adjudication_audit.py")
    scripts_dir = root / "scripts"
    for dep in _EXTRA_DEP_SCRIPTS:
        shutil.copy2(SCRIPTS_SRC_DIR / dep, scripts_dir / dep)
    shutil.copytree(LANGUAGES_SRC_DIR, root / "languages")
    return root


def write_no_elision_config(root, filename="fr-noelision.json"):
    """A project-local particle-config identical to shipped fr.json except
    has_elision:false/ELISION_RE:null -- proves matcher-authentication is
    genuinely config-parameterized (RFC #215 plan §0c/1b R5-F1), never a
    substring check."""
    fr = json.loads((LANGUAGES_SRC_DIR / "fr.json").read_text(encoding="utf-8"))
    fr["has_elision"] = False
    fr["ELISION_RE"] = None
    (root / "languages" / filename).write_text(json.dumps(fr, ensure_ascii=False), encoding="utf-8")
    return filename


def write_manifest(root, blocks):
    (root / "manifest.json").write_text(json.dumps({"blocks": blocks}, ensure_ascii=False), encoding="utf-8")


def write_senses(root, entries_by_source_form):
    doc = {"schema_version": 1, "entries_by_source_form": entries_by_source_form}
    (root / "canon_senses.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return doc


def write_canon(root, entries):
    keyed = {e["source_form"]: e for e in entries}
    doc = {
        "entries": keyed, "review_queue": [],
        "generation_hashes": {"particle_config_hash": "x", "derivation_bundle_hash": "y"},
    }
    (root / "canon.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")


def entry(source_form, target):
    return {"source_form": source_form, "canonical_target_form": target, "is_proper_name": True, "basis": "transliterated"}


def make_sense(sense_id, disambiguator, block_text, char_start, char_end, block="b1", seg=None,
               context_start=None, context_end=None, sha256_override=None):
    if context_start is None:
        context_start = 0
    if context_end is None:
        context_end = len(block_text)
    sha = sha256_override if sha256_override is not None else hashlib.sha256(
        block_text[context_start:context_end].encode("utf-8")
    ).hexdigest()
    return {
        "sense_id": sense_id, "disambiguator": disambiguator, "index_scope": "narrative",
        "evidence": {
            "block": block, "seg": seg, "char_start": char_start, "char_end": char_end,
            "context_start": context_start, "context_end": context_end, "sha256": sha,
        },
    }


def run_audit(root, *args, timeout=30):
    return subprocess.run(
        [sys.executable, str(root / "scripts" / "canon_adjudication_audit.py"), *args],
        capture_output=True, text=True, timeout=timeout,
    )


def parse_stdout(proc):
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    assert len(lines) == 1, f"expected exactly one stdout line, got {len(lines)}:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
    return json.loads(lines[0])


def assert_summary_schema_valid(summary):
    jsonschema.validate(instance=summary, schema=SUMMARY_SCHEMA)


# A second, distinct sense is needed alongside every fixture's "sense under test" purely to
# satisfy canon-senses.schema.json's minItems:2 -- always genuinely valid, never itself the
# thing a test is probing, so it never contributes to evidence_unverified.
def _decoy_valid_sense(source_form, block_text):
    spans = oi.production_occurrences(source_form, block_text, FR_LANG)
    assert spans, f"fixture bug: no real production span for {source_form!r} in {block_text!r}"
    s, e = spans[0]
    return make_sense("decoy", "a second, always-valid sense", block_text, s, e)


# ===========================================================================
# 1. Evidence matrix -- each dimension isolated
# ===========================================================================


def test_evidence_offset_shifted_to_different_name_same_block(tmp_path):
    """R4 must-fix 1: block 'Jean met Paul', split key 'Jean', context =
    whole block with a CORRECT hash, offsets spanning 'Paul' instead --
    every bounds/hash check passes but matcher-authentication must fail."""
    root = make_durable_root(tmp_path)
    block_text = "Jean met Paul"
    write_manifest(root, {"b1": {"seg": None, "plain_text": block_text}})
    paul_start, paul_end = block_text.index("Paul"), block_text.index("Paul") + len("Paul")
    bad_sense = make_sense("s1", "shifted to Paul", block_text, paul_start, paul_end)
    write_senses(root, {"Jean": {"senses": [bad_sense, _decoy_valid_sense("Jean", block_text)]}})
    write_canon(root, [entry("Marie", "Marie")])

    proc = run_audit(root, "--check", "--particle-config", "fr.json")
    summary = parse_stdout(proc)
    assert_summary_schema_valid(summary)
    assert summary["totals"]["evidence_unverified"] == 1
    assert proc.returncode == 1


def test_evidence_wrong_block_reference(tmp_path):
    root = make_durable_root(tmp_path)
    block_text = "Jean parla."
    write_manifest(root, {"b1": {"seg": None, "plain_text": block_text}})
    bad_sense = make_sense("s1", "wrong block", block_text, 0, 4, block="does-not-exist")
    write_senses(root, {"Jean": {"senses": [bad_sense, _decoy_valid_sense("Jean", block_text)]}})
    write_canon(root, [entry("Marie", "Marie")])

    proc = run_audit(root, "--check", "--particle-config", "fr.json")
    summary = parse_stdout(proc)
    assert_summary_schema_valid(summary)
    assert summary["totals"]["evidence_unverified"] == 1
    assert proc.returncode == 1


def test_evidence_sha256_mismatch(tmp_path):
    root = make_durable_root(tmp_path)
    block_text = "Jean parla."
    write_manifest(root, {"b1": {"seg": None, "plain_text": block_text}})
    bad_sense = make_sense("s1", "wrong hash", block_text, 0, 4, sha256_override="0" * 64)
    write_senses(root, {"Jean": {"senses": [bad_sense, _decoy_valid_sense("Jean", block_text)]}})
    write_canon(root, [entry("Marie", "Marie")])

    proc = run_audit(root, "--check", "--particle-config", "fr.json")
    summary = parse_stdout(proc)
    assert_summary_schema_valid(summary)
    assert summary["totals"]["evidence_unverified"] == 1
    assert proc.returncode == 1


def test_evidence_out_of_bounds_range(tmp_path):
    root = make_durable_root(tmp_path)
    block_text = "Jean parla."
    write_manifest(root, {"b1": {"seg": None, "plain_text": block_text}})
    bad_sense = make_sense("s1", "out of bounds", block_text, 0, 999)
    write_senses(root, {"Jean": {"senses": [bad_sense, _decoy_valid_sense("Jean", block_text)]}})
    write_canon(root, [entry("Marie", "Marie")])

    proc = run_audit(root, "--check", "--particle-config", "fr.json")
    summary = parse_stdout(proc)
    assert_summary_schema_valid(summary)
    assert summary["totals"]["evidence_unverified"] == 1
    assert proc.returncode == 1


def test_evidence_context_does_not_enclose_occurrence(tmp_path):
    root = make_durable_root(tmp_path)
    block_text = "Jean parla à Jean encore."
    write_manifest(root, {"b1": {"seg": None, "plain_text": block_text}})
    spans = oi.production_occurrences("Jean", block_text, FR_LANG)
    (s1, e1) = spans[1]  # the SECOND occurrence
    # context window covers only the FIRST few characters -- does not enclose the second span.
    bad_sense = make_sense("s1", "context too narrow", block_text, s1, e1, context_start=0, context_end=4)
    write_senses(root, {"Jean": {"senses": [bad_sense, _decoy_valid_sense("Jean", block_text)]}})
    write_canon(root, [entry("Marie", "Marie")])

    proc = run_audit(root, "--check", "--particle-config", "fr.json")
    summary = parse_stdout(proc)
    assert_summary_schema_valid(summary)
    assert summary["totals"]["evidence_unverified"] == 1
    assert proc.returncode == 1


@pytest.mark.parametrize("manifest_state", ["absent", "malformed_json", "invalid_utf8", "deeply_nested"])
def test_evidence_bad_manifest_is_not_fatal_but_unverified(tmp_path, manifest_state):
    """A missing/malformed/invalid-UTF-8/deeply-nested manifest.json is
    reported the SAME way as a missing block (evidence_verify.py's own
    tolerant design, via _read_manifest_for_evidence's own total-function
    contract) -- an evidence_unverified finding per sense, never a
    whole-run fatal (manifest is not in the audit's own exit-2
    structurally-validated-input list, unlike canon.json/
    canon_adjudications.json/canon_senses.json)."""
    root = make_durable_root(tmp_path)
    block_text = "Jean parla."
    if manifest_state == "malformed_json":
        (root / "manifest.json").write_text("{not valid json", encoding="utf-8")
    elif manifest_state == "invalid_utf8":
        (root / "manifest.json").write_bytes(b"\xff\xfe\x00not valid utf-8")
    elif manifest_state == "deeply_nested":
        (root / "manifest.json").write_text("[" * 200_000, encoding="utf-8")
    # else "absent": no manifest.json written at all.

    valid_span = oi.production_occurrences("Jean", block_text, FR_LANG)[0]
    sense = make_sense("s1", "would be valid if the manifest existed", block_text, *valid_span)
    write_senses(root, {"Jean": {"senses": [sense, _decoy_valid_sense("Jean", block_text)]}})
    write_canon(root, [entry("Marie", "Marie")])

    proc = run_audit(root, "--check", "--particle-config", "fr.json")
    summary = parse_stdout(proc)
    assert_summary_schema_valid(summary)
    assert summary["totals"]["evidence_unverified"] == 2, "every sense fails when there is no usable manifest to verify against"
    assert proc.returncode == 1
    assert proc.stdout.strip() != "", "a bad manifest is BLOCKING, never fatal -- a summary must still print"


def test_evidence_seg_null_positive_case(tmp_path):
    """A seg:null block is a normal, indexed block -- evidence against it
    must verify CLEAN, not be spuriously rejected."""
    root = make_durable_root(tmp_path)
    block_text = "Jean parla."
    write_manifest(root, {"b1": {"seg": None, "plain_text": block_text}})
    span = oi.production_occurrences("Jean", block_text, FR_LANG)[0]
    sense = make_sense("s1", "seg:null block", block_text, *span, seg=None)
    write_senses(root, {"Jean": {"senses": [sense, _decoy_valid_sense("Jean", block_text)]}})
    write_canon(root, [entry("Marie", "Marie")])

    proc = run_audit(root, "--check", "--particle-config", "fr.json")
    summary = parse_stdout(proc)
    assert_summary_schema_valid(summary)
    assert summary["totals"]["evidence_unverified"] == 0
    assert summary["totals"]["by_kind"]["homonym_split"] == 1


def test_evidence_duplicate_occurrence_distinct_spans_both_verify(tmp_path):
    """Two occurrences of the same source_form in one block get DISTINCT
    spans -- each must verify independently against its own real span."""
    root = make_durable_root(tmp_path)
    block_text = "Jean parla à Jean encore une fois."
    write_manifest(root, {"b1": {"seg": None, "plain_text": block_text}})
    spans = oi.production_occurrences("Jean", block_text, FR_LANG)
    assert len(spans) == 2
    senses = [make_sense(f"s{i}", f"sense {i}", block_text, s, e) for i, (s, e) in enumerate(spans)]
    write_senses(root, {"Jean": {"senses": senses}})
    write_canon(root, [entry("Marie", "Marie")])

    proc = run_audit(root, "--check", "--particle-config", "fr.json")
    summary = parse_stdout(proc)
    assert_summary_schema_valid(summary)
    assert summary["totals"]["evidence_unverified"] == 0


# ===========================================================================
# 2. Elision/no-elision matcher parity + end-to-end --particle-config
#    threading through the real audit CLI (R5-F1 / R6-F2 -- proves
#    run_check forwards the resolved config to evidence_verify/
#    production_occurrences, not just the helper in isolation).
# ===========================================================================


def test_elision_no_elision_matcher_parity_and_e2e_config_threading(tmp_path):
    """IDENTICAL manifest+sidecar bytes, run through the real --check CLI
    twice with two different --particle-config files (elision vs
    no-elision). 'Effiat' is a production span of "d'Effiat" ONLY under the
    elision config -- proves the config genuinely reaches
    production_occurrences() through run_check (`canon_adjudication_audit.
    py`'s CLI, not evidence_verify.py in isolation), and that
    matcher-authentication is config-parameterized rather than a bare
    in-bounds-substring check."""
    root = make_durable_root(tmp_path)
    no_elision_filename = write_no_elision_config(root)

    block_text = "Le comte d'Effiat parla, puis d'Effiat partit."
    write_manifest(root, {"b1": {"seg": None, "plain_text": block_text}})

    elision_spans = oi.production_occurrences("Effiat", block_text, FR_LANG)
    assert len(elision_spans) == 2, f"expected 2 elided 'Effiat' occurrences, got {elision_spans}"
    (s0, e0), (s1, e1) = elision_spans

    senses = [
        make_sense("s1", "sense one", block_text, s0, e0),
        make_sense("s2", "sense two", block_text, s1, e1),
    ]
    write_senses(root, {"Effiat": {"senses": senses}})
    write_canon(root, [entry("Marie", "Marie")])

    proc_elision = run_audit(root, "--check", "--particle-config", "fr.json")
    summary_elision = parse_stdout(proc_elision)
    assert_summary_schema_valid(summary_elision)
    assert summary_elision["totals"]["evidence_unverified"] == 0, (
        "under the WITH-elision config, [char_start,char_end) IS a production span for "
        "'Effiat' -- evidence must verify clean"
    )

    proc_no_elision = run_audit(root, "--check", "--particle-config", no_elision_filename)
    summary_no_elision = parse_stdout(proc_no_elision)
    assert_summary_schema_valid(summary_no_elision)
    assert summary_no_elision["totals"]["evidence_unverified"] == 2, (
        "under the NO-elision config, production never emits a bare 'Effiat' span at all -- "
        "the SAME stored offsets must now be REJECTED by matcher-authentication, proving "
        "run_check forwards the resolved --particle-config all the way to "
        "production_occurrences() rather than defaulting to no-config or a substring check"
    )
    assert proc_no_elision.returncode == 1
