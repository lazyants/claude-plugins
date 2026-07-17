"""tests/skeptic_report.test.py -- RFC #215 Phase 2 coverage for
skeptic_report.py, the SEPARATE, advisory-only report over
skeptic_triage.json (plan Part C / contract A4).

skeptic_report.py is loaded here via importlib from its real path, with
SCRIPTS_DIR temporarily on sys.path so its own `from skeptic_constants
import ...` resolves -- mirrors tests/occ_index.test.py's own loader
(contract A4: "mirror tests/occ_index.test.py:1-45").

Fixtures are built directly to skeptic-triage.schema.json's own shape
(never re-derived from a live skeptic run, since this suite does not
depend on A1/A2/A3 being complete) -- test_fixture_triage_conforms_to_
schema below schema-validates one full fixture against the REAL shipped
schema so a contract drift here fails loud, not silently.
"""
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import jsonschema
import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
ASSETS_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets"
SCRIPTS_DIR = ASSETS_DIR / "scripts"
SCHEMAS_DIR = ASSETS_DIR / "schemas"
SKEPTIC_REPORT_SCRIPT = SCRIPTS_DIR / "skeptic_report.py"
SKEPTIC_CONSTANTS_SCRIPT = SCRIPTS_DIR / "skeptic_constants.py"
TRIAGE_SCHEMA_PATH = SCHEMAS_DIR / "skeptic-triage.schema.json"

assert SKEPTIC_REPORT_SCRIPT.is_file(), f"skeptic_report.py not found at {SKEPTIC_REPORT_SCRIPT}"
assert SKEPTIC_CONSTANTS_SCRIPT.is_file(), f"skeptic_constants.py not found at {SKEPTIC_CONSTANTS_SCRIPT}"
assert TRIAGE_SCHEMA_PATH.is_file(), f"skeptic-triage.schema.json not found at {TRIAGE_SCHEMA_PATH}"

TRIAGE_SCHEMA = json.loads(TRIAGE_SCHEMA_PATH.read_text(encoding="utf-8"))


def _load_module(name: str, path: Path, extra_sys_path: Path):
    """Mirrors tests/occ_index.test.py's own loader: SCRIPTS_DIR must be on
    sys.path around the in-process load so a standalone script's own
    top-level `from skeptic_constants import ...` resolves exactly like it
    would under a real `python3 skeptic_report.py` invocation."""
    sys.path.insert(0, str(extra_sys_path))
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None and spec.loader is not None, f"could not load spec for {path}"
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(extra_sys_path))


sr = _load_module("skeptic_report_under_test", SKEPTIC_REPORT_SCRIPT, SCRIPTS_DIR)


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def hex64(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def make_manifest(blocks: dict) -> dict:
    """`blocks` is `{block_id: plain_text}`; every block gets `seg: None`."""
    return {"blocks": {bid: {"seg": None, "plain_text": text} for bid, text in blocks.items()}}


def make_evidence(block, char_start, char_end, context_start, context_end, seg=None):
    return {
        "block": block, "seg": seg,
        "char_start": char_start, "char_end": char_end,
        "context_start": context_start, "context_end": context_end,
        "sha256": hex64(f"{block}:{char_start}:{char_end}:{context_start}:{context_end}"),
    }


def make_record(source_form, verdict, rationale="a fixture rationale", evidence=None,
                 referents=None, evidence_coverage=None, notes=None):
    rec = {
        "assignment_id": hex64(f"assignment:{source_form}:{verdict}"),
        "source_form": source_form,
        "verdict": verdict,
        "rationale": rationale,
    }
    if evidence is not None:
        rec["evidence"] = evidence
    if referents is not None:
        rec["referents"] = referents
    if evidence_coverage is not None:
        rec["evidence_coverage"] = evidence_coverage
    if notes is not None:
        rec["notes"] = notes
    return rec


def make_triage(records, run_id="test-run"):
    return {"schema_version": 1, "run_id": run_id, "records": records}


def validate_triage(doc):
    jsonschema.Draft202012Validator(TRIAGE_SCHEMA).validate(doc)


# ---------------------------------------------------------------------------
# 1. Fixture fidelity -- catches contract drift between this suite's
#    hand-built fixtures and the REAL shipped schema.
# ---------------------------------------------------------------------------

def test_fixture_triage_conforms_to_schema():
    block_text = "Jean parla a Jean, un soldat different."
    evidence = make_evidence("b1", 5, 9, 0, len(block_text))
    triage = make_triage([
        make_record("Jean", "adverse", evidence=evidence, evidence_coverage={"cited": 1, "verified": 1}),
        make_record("Marie", "propose_split", evidence=None, referents=[
            {"disambiguator": "the baker", "evidence": make_evidence("b1", 0, 4, 0, len(block_text))},
            {"disambiguator": "the soldier", "evidence": make_evidence("b1", 13, 17, 0, len(block_text))},
        ]),
        make_record("Paul", "propose_rescope", evidence=evidence),
        make_record("Luc", "insufficient_window"),
    ])
    validate_triage(triage)  # must not raise


# ---------------------------------------------------------------------------
# 2. derive_quote -- offsets -> correct derived quotes, never confusing
#    char_start/char_end with context_start/context_end.
# ---------------------------------------------------------------------------

def test_adverse_derives_quote_from_char_offsets_not_context_offsets():
    """NAMED MUTATION: swapping char_start/char_end with context_start/
    context_end inside derive_quote() (using the context pair to slice
    `quote` and/or the char pair to slice `context`) would make this test
    fail -- the fixture below deliberately makes the narrow cited span
    ("Jean") and its enclosing context (the whole sentence) two visibly
    DIFFERENT substrings, so a swap produces a wrong-but-plausible string
    instead of silently passing."""
    block_text = "Jean parla a Jean, un soldat different."
    #             0123456789...
    # "Jean" (the second occurrence) sits at [13, 17).
    evidence = make_evidence("b1", 13, 17, 0, len(block_text))
    manifest = make_manifest({"b1": block_text})

    result = sr.derive_quote(manifest, evidence)

    assert result["quote"] == "Jean"
    assert result["context"] == block_text
    assert result["quote"] != result["context"], "fixture must keep char span and context span visibly distinct"
    assert result["unavailable_reason"] is None


def test_derive_quote_block_not_found_reports_unavailable_not_crash():
    manifest = make_manifest({"b1": "some text"})
    evidence = make_evidence("does-not-exist", 0, 4, 0, 9)

    result = sr.derive_quote(manifest, evidence)

    assert result["quote"] is None
    assert result["unavailable_reason"] is not None
    assert "does-not-exist" in result["unavailable_reason"]


def test_derive_quote_out_of_range_offsets_reports_unavailable_not_crash():
    manifest = make_manifest({"b1": "short"})
    evidence = make_evidence("b1", 0, 999, 0, 5)  # char_end far past len("short")

    result = sr.derive_quote(manifest, evidence)

    assert result["quote"] is None
    assert result["unavailable_reason"] is not None


# ---------------------------------------------------------------------------
# 3. evidence_coverage -- partial coverage always explicitly flagged.
# ---------------------------------------------------------------------------

def test_evidence_coverage_partial_is_labeled_partial():
    label = sr.coverage_label({"cited": 3, "verified": 1})
    assert "1/3" in label
    assert "partial" in label


def test_evidence_coverage_full_is_not_labeled_partial():
    label = sr.coverage_label({"cited": 2, "verified": 2})
    assert "2/2" in label
    assert "partial" not in label


def test_evidence_coverage_absent_is_not_recorded():
    assert sr.coverage_label(None) == "not recorded"
    assert sr.coverage_label({}) == "not recorded"


def test_evidence_coverage_zero_cited_is_no_citations():
    assert sr.coverage_label({"cited": 0, "verified": 0}) == "no citations"


# ---------------------------------------------------------------------------
# 4. build_report -- all four verdict kinds render; referents each get
#    their OWN derived quote (not swapped with a sibling's).
# ---------------------------------------------------------------------------

def test_build_report_renders_adverse_with_evidence_and_partial_coverage():
    block_text = "Jean the baker met Jean the soldier at dawn."
    manifest = make_manifest({"b1": block_text})
    evidence = make_evidence("b1", 0, 4, 0, len(block_text))
    triage = make_triage([
        make_record("Jean", "adverse", rationale="contradicting sentence found",
                    evidence=evidence, evidence_coverage={"cited": 2, "verified": 1}),
    ])
    validate_triage(triage)

    report = sr.build_report(triage, manifest)

    assert report["record_count"] == 1
    entry = report["entries"][0]
    assert entry["source_form"] == "Jean"
    assert entry["verdict"] == "adverse"
    assert entry["evidence"]["quote"] == "Jean"
    assert entry["evidence_coverage_label"] == "1/2 verified (partial)"


def test_build_report_propose_split_referents_each_render_own_quote_not_swapped():
    block_text = "Jean the baker; later, Jean the grandchild appeared."
    manifest = make_manifest({"b1": block_text})
    baker_span = (0, 4)
    grandchild_span = (24, 28)
    referents = [
        {"disambiguator": "the baker", "evidence": make_evidence("b1", *baker_span, 0, len(block_text))},
        {"disambiguator": "the grandchild", "evidence": make_evidence("b1", *grandchild_span, 0, len(block_text))},
    ]
    triage = make_triage([
        make_record("Jean", "propose_split", rationale="two distinct referents", referents=referents),
    ])
    validate_triage(triage)

    report = sr.build_report(triage, manifest)

    entry = report["entries"][0]
    assert entry["verdict"] == "propose_split"
    assert len(entry["referents"]) == 2
    baker_quote = entry["referents"][0]["evidence"]["quote"]
    grandchild_quote = entry["referents"][1]["evidence"]["quote"]
    assert baker_quote == block_text[baker_span[0]:baker_span[1]]
    assert grandchild_quote == block_text[grandchild_span[0]:grandchild_span[1]]
    assert baker_quote != grandchild_quote, "the two referents must not end up sharing/swapping each other's quote"


def test_build_report_propose_rescope_renders_like_adverse():
    block_text = "citation-only figure appears here only."
    manifest = make_manifest({"b1": block_text})
    evidence = make_evidence("b1", 0, 9, 0, len(block_text))
    triage = make_triage([
        make_record("Melchizedek", "propose_rescope", rationale="only ever cited, never narrated", evidence=evidence),
    ])
    validate_triage(triage)

    report = sr.build_report(triage, manifest)

    entry = report["entries"][0]
    assert entry["verdict"] == "propose_rescope"
    assert entry["evidence"]["quote"] == "citation-"


def test_build_report_insufficient_window_has_no_evidence_key_and_no_crash():
    manifest = make_manifest({"b1": "irrelevant text"})
    triage = make_triage([
        make_record("Obscura", "insufficient_window", rationale="too few windows to judge"),
    ])
    validate_triage(triage)

    report = sr.build_report(triage, manifest)

    entry = report["entries"][0]
    assert entry["verdict"] == "insufficient_window"
    assert "evidence" not in entry
    assert entry["evidence_coverage_label"] == "not recorded"


def test_build_report_all_four_verdicts_render_via_format_report():
    block_text = "Jean the baker; later, Jean the grandchild appeared, cited only elsewhere."
    manifest = make_manifest({"b1": block_text})
    evidence = make_evidence("b1", 0, 4, 0, len(block_text))
    triage = make_triage([
        make_record("Adverse-Entity", "adverse", evidence=evidence),
        make_record("Split-Entity", "propose_split", referents=[
            {"disambiguator": "sense A", "evidence": make_evidence("b1", 0, 4, 0, len(block_text))},
            {"disambiguator": "sense B", "evidence": make_evidence("b1", 24, 28, 0, len(block_text))},
        ]),
        make_record("Rescope-Entity", "propose_rescope", evidence=evidence),
        make_record("Insufficient-Entity", "insufficient_window"),
    ])
    validate_triage(triage)

    report = sr.build_report(triage, manifest)
    text = sr.format_report(report)

    for source_form, verdict in [
        ("Adverse-Entity", "adverse"),
        ("Split-Entity", "propose_split"),
        ("Rescope-Entity", "propose_rescope"),
        ("Insufficient-Entity", "insufficient_window"),
    ]:
        assert source_form in text
        assert verdict in text


# ---------------------------------------------------------------------------
# 5. risk_classes -- best-effort worklist enrichment, never fatal absent.
# ---------------------------------------------------------------------------

def test_risk_classes_enriched_from_worklist_when_present():
    manifest = make_manifest({"b1": "Jean text"})
    triage = make_triage([make_record("Jean", "insufficient_window")])
    worklist_risk_classes = {"Jean": ["near_merge", "high_dispersion"]}

    report = sr.build_report(triage, manifest, worklist_risk_classes)

    assert report["entries"][0]["risk_classes"] == ["near_merge", "high_dispersion"]


def test_risk_classes_unavailable_when_no_worklist_given():
    manifest = make_manifest({"b1": "Jean text"})
    triage = make_triage([make_record("Jean", "insufficient_window")])

    report = sr.build_report(triage, manifest)

    assert report["entries"][0]["risk_classes"] is None


def test_load_worklist_risk_classes_missing_file_is_empty_not_fatal(tmp_path):
    missing = tmp_path / "does-not-exist.json"
    assert sr.load_worklist_risk_classes(missing) == {}


def test_load_worklist_risk_classes_malformed_json_is_empty_not_fatal(tmp_path):
    bad = tmp_path / "suspicion_worklist.json"
    bad.write_text("{not valid json", encoding="utf-8")
    assert sr.load_worklist_risk_classes(bad) == {}


# ---------------------------------------------------------------------------
# 5b. _sanitize / format_report injection guard (fix L12) -- every
#     agent-authored field is neutralized before it reaches the human's
#     terminal: a raw newline must never forge another report-looking
#     line, and a raw ANSI/control escape must never survive to spoof the
#     terminal this report is the SOLE human-facing consumer of.
# ---------------------------------------------------------------------------

def test_sanitize_escapes_newlines_and_strips_ansi_and_control_chars():
    assert sr._sanitize("clean text") == "clean text"
    assert sr._sanitize("a\nb") == "a\\nb"
    assert sr._sanitize("a\r\nb") == "a\\nb"
    assert sr._sanitize("a\rb") == "a\\nb"
    assert sr._sanitize("\x1b[2J\x1b[31mHACK") == "[2J[31mHACK"
    assert sr._sanitize(None) is None


def test_format_report_sanitizes_rationale_against_forged_lines_and_ansi_escapes():
    """NAMED MUTATION guard for fix L12: format_report's SOLE human-facing
    consumer reads this text to make identity decisions -- an agent-
    authored rationale must never be able to forge a fake
    "[n] SomeName (verdict: ...)" line via an embedded newline, nor clear/
    spoof the terminal via an embedded ANSI escape sequence."""
    block_text = "irrelevant block text for this fixture"
    manifest = make_manifest({"b1": block_text})
    malicious_rationale = "line1\n[9] FORGED (verdict: adverse)" + " \x1b[2J\x1b[31mHACK"
    triage = make_triage([
        make_record("Jean", "adverse", rationale=malicious_rationale,
                    evidence=make_evidence("b1", 0, 4, 0, len(block_text))),
    ])
    validate_triage(triage)

    report = sr.build_report(triage, manifest)
    text = sr.format_report(report)

    lines = text.split("\n")
    assert not any(line.strip().startswith("[9] FORGED") for line in lines), (
        "a newline embedded in rationale must never forge its own report-looking line"
    )
    assert "\n[9] FORGED" not in text
    assert "\x1b" not in text, "no raw ESC byte may survive into the rendered report"


def test_format_report_clean_rationale_renders_unchanged():
    """A rationale with no control characters must render byte-identical
    to the pre-fix output -- _sanitize is the identity function on clean
    input."""
    block_text = "Jean the baker met Jean the soldier at dawn."
    manifest = make_manifest({"b1": block_text})
    evidence = make_evidence("b1", 0, 4, 0, len(block_text))
    triage = make_triage([
        make_record("Jean", "adverse",
                    rationale="a perfectly clean rationale with no control chars",
                    evidence=evidence),
    ])
    validate_triage(triage)

    report = sr.build_report(triage, manifest)
    text = sr.format_report(report)

    assert "rationale: a perfectly clean rationale with no control chars" in text
    assert "evidence quote: 'Jean'" in text


# ---------------------------------------------------------------------------
# 6. Fatal input handling -- load_triage rejects a schema-invalid artifact
#    (e.g. a smuggled confirmation-shaped field) loud, never silently.
# ---------------------------------------------------------------------------

def test_load_triage_rejects_schema_invalid_document(tmp_path):
    bad_doc = {
        "schema_version": 1,
        "run_id": "r1",
        "records": [
            {
                "assignment_id": hex64("x"),
                "source_form": "Jean",
                "verdict": "adverse",
                "rationale": "ok",
                "confirmed_ok": True,  # additionalProperties:false must reject this
            },
        ],
    }
    triage_path = tmp_path / "skeptic_triage.json"
    triage_path.write_text(json.dumps(bad_doc), encoding="utf-8")

    with pytest.raises(sr.SkepticReportError):
        sr.load_triage(triage_path, TRIAGE_SCHEMA_PATH)


def test_load_triage_missing_file_is_fatal(tmp_path):
    missing = tmp_path / "skeptic_triage.json"
    with pytest.raises(sr.SkepticReportError):
        sr.load_triage(missing, TRIAGE_SCHEMA_PATH)


def test_load_manifest_missing_blocks_mapping_is_fatal(tmp_path):
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps({"not_blocks": {}}), encoding="utf-8")
    with pytest.raises(sr.SkepticReportError):
        sr.load_manifest(manifest_path)


# ---------------------------------------------------------------------------
# 7. CLI smoke test -- the real subprocess entry point, self-anchored
#    (no --durable-root override), staged into an isolated tmp durable root.
# ---------------------------------------------------------------------------

def test_cli_smoke_renders_report_and_exits_zero(tmp_path):
    import shutil
    import subprocess

    root = tmp_path / "durable_root"
    scripts_dir = root / "scripts"
    schemas_dir = root / "schemas"
    scripts_dir.mkdir(parents=True)
    schemas_dir.mkdir(parents=True)
    shutil.copy2(SKEPTIC_REPORT_SCRIPT, scripts_dir / "skeptic_report.py")
    shutil.copy2(SKEPTIC_CONSTANTS_SCRIPT, scripts_dir / "skeptic_constants.py")
    shutil.copy2(TRIAGE_SCHEMA_PATH, schemas_dir / "skeptic-triage.schema.json")

    block_text = "Jean the baker met Jean the soldier at dawn."
    (root / "manifest.json").write_text(
        json.dumps(make_manifest({"b1": block_text})), encoding="utf-8",
    )
    evidence = make_evidence("b1", 0, 4, 0, len(block_text))
    triage = make_triage([make_record("Jean", "adverse", evidence=evidence)])
    validate_triage(triage)
    (root / "skeptic_triage.json").write_text(json.dumps(triage), encoding="utf-8")

    proc = subprocess.run(
        [sys.executable, str(scripts_dir / "skeptic_report.py")],
        capture_output=True, text=True, timeout=30,
    )

    assert proc.returncode == 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    assert "Jean" in proc.stdout
    assert "adverse" in proc.stdout


def test_cli_smoke_fatal_on_missing_triage(tmp_path):
    import shutil
    import subprocess

    root = tmp_path / "durable_root"
    scripts_dir = root / "scripts"
    schemas_dir = root / "schemas"
    scripts_dir.mkdir(parents=True)
    schemas_dir.mkdir(parents=True)
    shutil.copy2(SKEPTIC_REPORT_SCRIPT, scripts_dir / "skeptic_report.py")
    shutil.copy2(SKEPTIC_CONSTANTS_SCRIPT, scripts_dir / "skeptic_constants.py")
    shutil.copy2(TRIAGE_SCHEMA_PATH, schemas_dir / "skeptic-triage.schema.json")
    (root / "manifest.json").write_text(json.dumps(make_manifest({})), encoding="utf-8")
    # No skeptic_triage.json written.

    proc = subprocess.run(
        [sys.executable, str(scripts_dir / "skeptic_report.py")],
        capture_output=True, text=True, timeout=30,
    )

    assert proc.returncode == 2
    assert proc.stdout.strip() == ""
    assert "skeptic_triage.json" in proc.stderr
