"""Tests for scripts/language_smoke_report.py and its
language-smoke-report.schema.json contract.

Each test copies the REAL script (and the REAL schema, since the script
loads it relative to its own self-anchored ``${durable_root}/schemas/``
location) into an isolated ``durable_root`` fixture and invokes it as a real
subprocess -- the exact way it is actually run in production
(`python3 ${durable_root}/scripts/language_smoke_report.py ...`) -- so its
``Path(__file__).resolve().parents[1]``-based self-anchoring resolves against
the isolated fixture root rather than this repo's real assets directory.
``--particle-config``/``--manifest``/``--report-path`` are always given as
explicit (slash-containing) absolute paths, which bypasses bare-filename
resolution under ``${durable_root}/languages/`` entirely -- so no test here
ever needs a real or fake ``profile.yml`` / PyYAML on the path.

Coverage (see references/language-pair-parameterization.md, "Mandatory
language-config smoke test" + "Sample selection algorithm" + "Low-name-density
path" + "Zero-candidate case" + "particle_smoke_cases is DECOUPLED" +
"elision_test_cases's conditional requirement"):

  - The three-hash computation (particle_config_sha1, source_sample_sha1,
    smoke_report_contract_hash) -- particle_config_sha1/contract_hash are
    EXACT byte hashes (no normalization); source_sample_sha1 undergoes
    whitespace-run collapsing BEFORE hashing.
  - The stratified sample-selection algorithm: first/middle/late/high-density
    body anchors (deduplicated for small N), PLUS the fifth ``frontback``
    anchor -- present only when manifest.json's frontback[] has a
    ``decision:"translate"`` entry, and concatenating ALL such entries'
    text (not just one), while regenerate/omit-decision entries never
    contribute even when a matching segment record exists.
  - The 10-name floor and its two escape branches: the default branch
    (>=10 DISTINCT checked names), the low-name-density branch
    (--low-name-density-confirmed + checked_names, dedup-aware, must set-
    cover every distinct candidate), and the zero-candidate branch
    (--no-names-confirmed, requiring BOTH flags).
  - particle_smoke_cases's requirement DECOUPLED from name density --
    keyed only to particle_list_size > 0, with its own
    --no-particles-confirmed escape for a genuinely particle-free language.
  - elision_test_cases's conditional requirement, driven by the in-report
    has_elision field copied verbatim from the resolved particle_config file.
"""
import importlib.util
import json
import re
import shutil
import subprocess
import sys
import unicodedata
import uuid
from pathlib import Path

import jsonschema
import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_SRC = (
    PLUGIN_ROOT
    / "skills"
    / "literary-translator"
    / "assets"
    / "scripts"
    / "language_smoke_report.py"
)
SCHEMA_SRC = (
    PLUGIN_ROOT
    / "skills"
    / "literary-translator"
    / "assets"
    / "schemas"
    / "language-smoke-report.schema.json"
)

assert SCRIPT_SRC.is_file(), f"language_smoke_report.py not found at {SCRIPT_SRC}"
assert SCHEMA_SRC.is_file(), f"language-smoke-report.schema.json not found at {SCHEMA_SRC}"


# ---------------------------------------------------------------------------
# Fixture harness
# ---------------------------------------------------------------------------

def make_durable_root(tmp_path):
    """Isolated durable_root: real script copied to {root}/scripts/, real
    schema copied to {root}/schemas/ -- matching the script's own
    self-anchoring (``DURABLE_ROOT = Path(__file__).resolve().parents[1]``,
    ``SCHEMAS_DIR = DURABLE_ROOT / "schemas"``), never assumes cwd ==
    durable_root."""
    root = tmp_path / "durable_root"
    scripts_dir = root / "scripts"
    scripts_dir.mkdir(parents=True)
    shutil.copy2(SCRIPT_SRC, scripts_dir / "language_smoke_report.py")
    schemas_dir = root / "schemas"
    schemas_dir.mkdir()
    shutil.copy2(SCHEMA_SRC, schemas_dir / "language-smoke-report.schema.json")
    (root / "languages").mkdir()
    (root / "runs").mkdir()
    return root


@pytest.fixture
def root(tmp_path):
    return make_durable_root(tmp_path)


def particle_config_payload(particles=(), stopwords=(), has_elision=False, elision_re=None):
    return {
        "PARTICLES": list(particles),
        "STOPWORDS": list(stopwords),
        "has_elision": has_elision,
        "ELISION_RE": elision_re,
    }


def build_manifest(body_texts, frontback_items=None):
    """body_texts: list[str], one per body segment, in intended order_index
    order (seg0, seg1, ...).

    frontback_items: optional list of {"id": str, "decision": str, "text": str}
    dicts. A matching kind="frontback" segment (seg == id) is ALWAYS added to
    segments[] regardless of decision -- this deliberately stresses
    language_smoke_report.py's OWN decision-based filter
    (``frontback_segs = [s for s in segments if kind=="frontback" and
    s.get("seg") in translate_ids]``) rather than merely relying on an
    upstream invariant (manifest.schema.json's cross-reference check) that
    regenerate/omit entries never reach segments[] in the first place.
    """
    blocks = {}
    segments = []
    order = 0
    for i, text in enumerate(body_texts):
        bid = f"body_block_{i}"
        blocks[bid] = {"order_index": order, "plain_text": text}
        segments.append({"seg": f"seg{i}", "kind": "body", "block_ids": [bid]})
        order += 1

    frontback_entries = []
    for item in (frontback_items or []):
        frontback_entries.append({"id": item["id"], "decision": item["decision"]})
        bid = f"fb_block_{item['id']}"
        blocks[bid] = {"order_index": order, "plain_text": item["text"]}
        segments.append({"seg": item["id"], "kind": "frontback", "block_ids": [bid]})
        order += 1

    return {"blocks": blocks, "segments": segments, "frontback": frontback_entries}


def run_smoke(
    root,
    tmp_path,
    manifest,
    particle_config,
    *,
    checked_names=None,
    elision_cases=None,
    particle_cases=None,
    low_name_density_confirmed=False,
    no_names_confirmed=False,
    no_particles_confirmed=False,
):
    unique = uuid.uuid4().hex
    manifest_path = tmp_path / f"manifest_{unique}.json"
    particle_config_path = tmp_path / f"particle_config_{unique}.json"
    report_path = tmp_path / f"report_{unique}.json"

    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    particle_config_path.write_text(json.dumps(particle_config, ensure_ascii=False), encoding="utf-8")

    cmd = [
        sys.executable,
        str(root / "scripts" / "language_smoke_report.py"),
        "--particle-config", str(particle_config_path),
        "--manifest", str(manifest_path),
        "--report-path", str(report_path),
    ]
    if checked_names is not None:
        cmd += ["--checked-names", ",".join(checked_names)]
    if elision_cases is not None:
        elision_path = tmp_path / f"elision_{unique}.json"
        elision_path.write_text(json.dumps(elision_cases, ensure_ascii=False), encoding="utf-8")
        cmd += ["--elision-test-file", str(elision_path)]
    if particle_cases is not None:
        particle_path = tmp_path / f"particle_smoke_{unique}.json"
        particle_path.write_text(json.dumps(particle_cases, ensure_ascii=False), encoding="utf-8")
        cmd += ["--particle-smoke-file", str(particle_path)]
    if low_name_density_confirmed:
        cmd.append("--low-name-density-confirmed")
    if no_names_confirmed:
        cmd.append("--no-names-confirmed")
    if no_particles_confirmed:
        cmd.append("--no-particles-confirmed")

    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.is_file() else None
    return proc, report, {
        "manifest": manifest_path,
        "particle_config": particle_config_path,
        "report": report_path,
    }


def expected_collapse_whitespace(text):
    """Independent reimplementation of the documented normalization rule
    ("collapse all whitespace runs to single spaces... before computing
    source_sample_sha1") -- used ONLY to compute expected hash inputs, never
    imported from the script under test."""
    return re.sub(r"\s+", " ", text).strip()


def sha1_hex(data: bytes) -> str:
    import hashlib
    return hashlib.sha1(data).hexdigest()


NO_PARTICLES_NO_ELISION = particle_config_payload()  # PARTICLES=[] -> needs --no-particles-confirmed


# ---------------------------------------------------------------------------
# The three-hash computation
# ---------------------------------------------------------------------------

def test_particle_config_sha1_is_exact_byte_hash_of_resolved_file(tmp_path, root):
    manifest = build_manifest(["Oskar visited a quiet port."])
    proc, report, paths = run_smoke(
        root, tmp_path, manifest, NO_PARTICLES_NO_ELISION,
        checked_names=["Oskar"],
        low_name_density_confirmed=True,
        no_particles_confirmed=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert report is not None
    expected = sha1_hex(paths["particle_config"].read_bytes())
    assert report["particle_config_sha1"] == expected


def test_particle_config_sha1_changes_when_file_content_changes(tmp_path, root):
    manifest = build_manifest(["Oskar visited a quiet port."])
    config_a = particle_config_payload(stopwords=["Le"])
    config_b = particle_config_payload(stopwords=["La"])  # differs by one stopword
    _, report_a, _ = run_smoke(
        root, tmp_path, manifest, config_a,
        checked_names=["Oskar"], low_name_density_confirmed=True, no_particles_confirmed=True,
    )
    _, report_b, _ = run_smoke(
        root, tmp_path, manifest, config_b,
        checked_names=["Oskar"], low_name_density_confirmed=True, no_particles_confirmed=True,
    )
    assert report_a is not None
    assert report_b is not None
    assert report_a["particle_config_sha1"] != report_b["particle_config_sha1"]


def test_smoke_report_contract_hash_is_sha1_of_scripts_own_bytes(tmp_path, root):
    manifest = build_manifest(["Oskar visited a quiet port."])
    proc, report, _ = run_smoke(
        root, tmp_path, manifest, NO_PARTICLES_NO_ELISION,
        checked_names=["Oskar"], low_name_density_confirmed=True, no_particles_confirmed=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert report is not None
    expected = sha1_hex(SCRIPT_SRC.read_bytes())
    assert report["smoke_report_contract_hash"] == expected
    # Also true of the copy actually executed (byte-identical to the real script).
    assert report["smoke_report_contract_hash"] == sha1_hex(
        (root / "scripts" / "language_smoke_report.py").read_bytes()
    )


def test_source_sample_sha1_normalizes_whitespace_before_hashing(tmp_path, root):
    # Same words, different whitespace runs (double spaces / tabs / newlines)
    # -- must hash IDENTICALLY after normalization.
    text_a = "Anna greeted  Bob softly.   Carol thanked Diana warmly."
    text_b = "Anna greeted\tBob softly.\nCarol   thanked Diana warmly."
    manifest_a = build_manifest([text_a])
    manifest_b = build_manifest([text_b])

    _, report_a, _ = run_smoke(
        root, tmp_path, manifest_a, NO_PARTICLES_NO_ELISION,
        checked_names=["Anna", "Bob", "Carol", "Diana"],
        low_name_density_confirmed=True, no_particles_confirmed=True,
    )
    _, report_b, _ = run_smoke(
        root, tmp_path, manifest_b, NO_PARTICLES_NO_ELISION,
        checked_names=["Anna", "Bob", "Carol", "Diana"],
        low_name_density_confirmed=True, no_particles_confirmed=True,
    )
    assert report_a is not None
    assert report_b is not None
    assert report_a["source_sample_sha1"] == report_b["source_sample_sha1"]
    expected = sha1_hex(expected_collapse_whitespace(text_a).encode("utf-8"))
    assert report_a["source_sample_sha1"] == expected

    # And a genuinely different sample must hash differently -- proves the
    # hash isn't trivially constant.
    manifest_c = build_manifest(["Ethan greeted Fiona softly. George thanked Helen warmly."])
    _, report_c, _ = run_smoke(
        root, tmp_path, manifest_c, NO_PARTICLES_NO_ELISION,
        checked_names=["Ethan", "Fiona", "George", "Helen"],
        low_name_density_confirmed=True, no_particles_confirmed=True,
    )
    assert report_c is not None
    assert report_c["source_sample_sha1"] != report_a["source_sample_sha1"]


# ---------------------------------------------------------------------------
# Stratified sample-selection algorithm
# ---------------------------------------------------------------------------

def test_stratified_selection_dedupes_first_and_middle_for_two_body_segments(tmp_path, root):
    # N=2: first=segs[0], middle=segs[2//2]=segs[1], late=segs[-1]=segs[1]
    # (dup of middle, dropped) -> no "late", no remaining -> no "high_density".
    manifest = build_manifest([
        "quiet halls held no names today.",
        "silent rooms stayed empty tonight.",
    ])
    proc, report, _ = run_smoke(
        root, tmp_path, manifest, NO_PARTICLES_NO_ELISION,
        low_name_density_confirmed=True, no_names_confirmed=True, no_particles_confirmed=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert report is not None
    used = report["source_sample_selection"]["segments_used"]
    assert used == [
        {"segment_id": "seg0", "anchor": "first", "kind": "body"},
        {"segment_id": "seg1", "anchor": "middle", "kind": "body"},
    ]
    assert report["candidate_names_total"] == 0


def test_stratified_selection_picks_first_middle_late_and_highest_density_anchor(tmp_path, root):
    # N=5: first=segs[0], middle=segs[5//2]=segs[2], late=segs[4],
    # remaining={segs[1], segs[3]} -> highest density_score wins as the 4th
    # ("high_density") anchor; the OTHER remaining segment is dropped
    # entirely (never appears in segments_used at all).
    body_texts = [
        "Anna opened the door slowly.",                                    # seg0 -- first
        "the plain text here has almost no capital letters at all "
        "really truly nothing special going on whatsoever.",              # seg1 -- low density, dropped
        "Middle passage continues the story onward.",                     # seg2 -- middle
        "Bertrand Charlotte Desmond Eleanor Frederick gathered together "
        "for a meeting many many words filler filler filler filler "
        "filler filler filler filler filler filler.",                     # seg3 -- high density, wins
        "Zoe closed the final chapter today.",                            # seg4 -- late
    ]
    manifest = build_manifest(body_texts)
    checked = ["Anna", "Middle", "Bertrand Charlotte Desmond Eleanor Frederick", "Zoe"]
    proc, report, _ = run_smoke(
        root, tmp_path, manifest, NO_PARTICLES_NO_ELISION,
        checked_names=checked, low_name_density_confirmed=True, no_particles_confirmed=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert report is not None
    used = report["source_sample_selection"]["segments_used"]
    assert used == [
        {"segment_id": "seg0", "anchor": "first", "kind": "body"},
        {"segment_id": "seg2", "anchor": "middle", "kind": "body"},
        {"segment_id": "seg3", "anchor": "high_density", "kind": "body"},
        {"segment_id": "seg4", "anchor": "late", "kind": "body"},
    ]
    # seg1 (the losing remaining candidate) must never appear at all.
    assert all(s["segment_id"] != "seg1" for s in used)
    assert report["candidate_names_total"] == 4
    assert report["pass"] is True


def test_extract_candidate_names_never_bridges_sentence_boundary_regression(tmp_path, root):
    # Mirrors tests/bootstrap_names.test.py's
    # test_extract_candidates_never_bridges_sentence_boundary, applied to
    # THIS script's own separate "generalized re-implementation" of the same
    # run-building algorithm (see language_smoke_report.py's module docstring).
    manifest = build_manifest(["Fiona. George arrived quietly."])
    proc, report, _ = run_smoke(
        root, tmp_path, manifest, NO_PARTICLES_NO_ELISION,
        checked_names=["Fiona", "George"],
        low_name_density_confirmed=True,
        no_particles_confirmed=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert report is not None
    assert report["candidate_names_total"] == 2
    by_name = {c["name"]: c["found"] for c in report["checked_names"]}
    assert by_name == {"Fiona": True, "George": True}


def test_extract_candidate_names_respects_em_dash_boundary_regression(tmp_path, root):
    # Em-dash is the dominant dialogue-line delimiter in French/Russian/
    # Spanish literary prose -- must be treated exactly like a period/etc.
    # sentence boundary, or "Fiona. -- George arriva." fuses into the bogus
    # candidate "Fiona George".
    manifest = build_manifest(["Fiona. — George arriva."])
    proc, report, _ = run_smoke(
        root, tmp_path, manifest, NO_PARTICLES_NO_ELISION,
        checked_names=["Fiona", "George"],
        low_name_density_confirmed=True,
        no_particles_confirmed=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert report is not None
    assert report["candidate_names_total"] == 2
    by_name = {c["name"]: c["found"] for c in report["checked_names"]}
    assert by_name == {"Fiona": True, "George": True}


def test_extract_candidate_names_particle_branch_respects_boundary_regression(tmp_path, root):
    # The particle-continuation branch (e.g. French "du") must not bridge a
    # sentence terminator sitting before the trailing name, or "parla Fiona
    # du. George arriva." fuses into the bogus candidate "Fiona du George".
    manifest = build_manifest(["parla Fiona du. George arriva."])
    lang = particle_config_payload(particles=["du"])
    proc, report, _ = run_smoke(
        root, tmp_path, manifest, lang,
        checked_names=["Fiona", "George"],
        low_name_density_confirmed=True,
        particle_cases=[{"token": "du", "is_particle": True}],
    )
    assert proc.returncode == 0, proc.stderr
    assert report is not None
    assert report["candidate_names_total"] == 2
    by_name = {c["name"]: c["found"] for c in report["checked_names"]}
    assert by_name == {"Fiona": True, "George": True}


def test_extract_candidate_names_quote_masked_boundary_regression(tmp_path, root):
    # A closing quote sitting between the terminator and the next
    # capitalized token must not mask the boundary, or "'we saw Fiona.'
    # George nodded." fuses into the bogus candidate "Fiona George".
    manifest = build_manifest(["'we saw Fiona.' George nodded."])
    proc, report, _ = run_smoke(
        root, tmp_path, manifest, NO_PARTICLES_NO_ELISION,
        checked_names=["Fiona", "George"],
        low_name_density_confirmed=True,
        no_particles_confirmed=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert report is not None
    assert report["candidate_names_total"] == 2
    by_name = {c["name"]: c["found"] for c in report["checked_names"]}
    assert by_name == {"Fiona": True, "George": True}


def test_extract_candidate_names_bracket_masked_boundary_regression(tmp_path, root):
    # A closing bracket sitting between the terminator and the next
    # capitalized token must not mask the boundary, or "(Fiona.) George
    # arrived." fuses into the bogus candidate "Fiona George".
    manifest = build_manifest(["(Fiona.) George arrived."])
    proc, report, _ = run_smoke(
        root, tmp_path, manifest, NO_PARTICLES_NO_ELISION,
        checked_names=["Fiona", "George"],
        low_name_density_confirmed=True,
        no_particles_confirmed=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert report is not None
    assert report["candidate_names_total"] == 2
    by_name = {c["name"]: c["found"] for c in report["checked_names"]}
    assert by_name == {"Fiona": True, "George": True}


def test_extract_candidate_names_guillemet_masked_boundary_regression(tmp_path, root):
    # An opening guillemet sitting between the terminator and the next
    # capitalized token must not mask the boundary, or "Fiona. « George
    # arriva. »" fuses into the bogus candidate "Fiona George".
    manifest = build_manifest(["Fiona. « George arriva. »"])
    proc, report, _ = run_smoke(
        root, tmp_path, manifest, NO_PARTICLES_NO_ELISION,
        checked_names=["Fiona", "George"],
        low_name_density_confirmed=True,
        no_particles_confirmed=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert report is not None
    assert report["candidate_names_total"] == 2
    by_name = {c["name"]: c["found"] for c in report["checked_names"]}
    assert by_name == {"Fiona": True, "George": True}


def test_extract_candidate_names_nested_wrapper_masked_boundary_regression(tmp_path, root):
    # Two stacked wrappers ")" + "]" mask the terminator before George; the
    # back-scan must skip BOTH to reach the "." behind them.
    manifest = build_manifest(["([Fiona.]) George arrived."])
    proc, report, _ = run_smoke(
        root, tmp_path, manifest, NO_PARTICLES_NO_ELISION,
        checked_names=["Fiona", "George"],
        low_name_density_confirmed=True,
        no_particles_confirmed=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert report is not None
    assert report["candidate_names_total"] == 2
    by_name = {c["name"]: c["found"] for c in report["checked_names"]}
    assert by_name == {"Fiona": True, "George": True}


def test_extract_candidate_names_strips_trailing_apostrophe_regression(tmp_path, root):
    # Issue #82 mirror on THIS script's own generalized extractor, exercised
    # through the real subprocess with the FR elision config active. A trailing
    # apostrophe after a name (e.g. "Fiona’ George") must be STRIPPED by the
    # tokenizer, not absorbed into the token -- so the single fused candidate is
    # the apostrophe-free "Fiona George", never "Fiona’ George". "’" is a
    # WRAPPER (not a TERMINATOR), so with no real sentence boundary present the
    # run still fuses; only the stray apostrophe is gone. Both the straight and
    # curly variants dedupe to the same candidate, so candidate_names_total==1.
    manifest = build_manifest(["Fiona’ George nodded.", "Fiona' George nodded."])
    lang = particle_config_payload(has_elision=True, elision_re=FR_ELISION_RE)
    elision_cases = [
        {"sentence": "Il visita le chateau d'Effiat hier.", "expected_names": ["Effiat"]},
    ]
    proc, report, _ = run_smoke(
        root, tmp_path, manifest, lang,
        checked_names=["Fiona George"],
        low_name_density_confirmed=True,
        no_particles_confirmed=True,
        elision_cases=elision_cases,
    )
    assert proc.returncode == 0, proc.stderr
    assert report is not None
    # The apostrophe never survives into the candidate: the ONLY candidate is
    # the apostrophe-free fused form, so an absorbed "Fiona’ George" (which
    # would leave "Fiona George" not-found) is impossible.
    assert report["candidate_names_total"] == 1
    by_name = {c["name"]: c["found"] for c in report["checked_names"]}
    assert by_name == {"Fiona George": True}
    assert all(c["passed"] for c in report["elision_test_cases"])


def test_sentinel_does_not_fuse_into_candidate_extraction_regression(tmp_path, root):
    # Issue #89: block plain_text carries literal ⟦FNREF_N⟧ / ⟦VERSE_...⟧
    # sentinels (see extract.py.template) that must be stripped before
    # candidate extraction -- otherwise a sentinel fuses into the adjacent
    # name token (e.g. "Bouchard⟦FNREF_5⟧" -> bogus candidate
    # "Bouchard FNREF"), which flips the real checked name to found:false
    # and false-fails the W3 gate.
    manifest = build_manifest(["Bouchard⟦FNREF_5⟧ said nothing."])
    proc, report, _ = run_smoke(
        root, tmp_path, manifest, NO_PARTICLES_NO_ELISION,
        checked_names=["Bouchard"],
        low_name_density_confirmed=True,
        no_particles_confirmed=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert report is not None
    assert report["candidate_names_total"] == 1
    by_name = {c["name"]: c["found"] for c in report["checked_names"]}
    assert by_name == {"Bouchard": True}
    assert report["pass"] is True


def test_sentinel_fused_candidate_is_never_produced_regression(tmp_path, root):
    # Inverse of the above: the bogus fused candidate "Bouchard FNREF" must
    # never be produced once sentinels are stripped, so explicitly checking
    # for that exact string must come back not-found. "Bouchard" (the real,
    # sole candidate) is ALSO checked here so the low-density set-coverage
    # gate is satisfied and the run reaches the found-status stage at all
    # -- checking only the bogus name would leave the real candidate
    # uncovered and now correctly FATALs before a report is even written.
    manifest = build_manifest(["Bouchard⟦FNREF_5⟧ said nothing."])
    proc, report, _ = run_smoke(
        root, tmp_path, manifest, NO_PARTICLES_NO_ELISION,
        checked_names=["Bouchard", "Bouchard FNREF"],
        low_name_density_confirmed=True,
        no_particles_confirmed=True,
    )
    assert proc.returncode == 1
    assert report is not None
    by_name = {c["name"]: c["found"] for c in report["checked_names"]}
    assert by_name["Bouchard"] is True
    assert by_name["Bouchard FNREF"] is False
    assert report["pass"] is False


def test_sentinel_heavy_segment_does_not_win_density_selection_regression(tmp_path, root):
    # Issue #89 (density_score half): density_score() must also strip
    # sentinels before scoring, or a segment stuffed with sentinel markers
    # (each contributing a spurious upper-initial TOKEN_RE match -- the
    # "FNREF" in ⟦FNREF_N⟧ -- with no matching increase in word count) can
    # out-score a segment with genuinely dense real capitalized content,
    # flipping WHICH segment gets selected as the "high_density" sample.
    # N=5 stratified layout mirrors
    # test_stratified_selection_picks_first_middle_late_and_highest_density_anchor:
    # first=seg0, middle=seg2, late=seg4, remaining={seg1, seg3} compete.
    legit = "Bertrand Charlotte Desmond Eleanor Frederick gathered together for a meeting."
    sentinel_heavy = (
        "the plain filler text here has almost no capital letters at all "
        "really truly nothing special whatsoever today"
        + "".join(f"⟦FNREF_{i}⟧" for i in range(1, 21))
    )
    body_texts = [
        "Anna opened the door slowly.",       # seg0 -- first
        legit,                                 # seg1 -- remaining candidate A (legit high density)
        "Middle passage continues onward.",    # seg2 -- middle
        sentinel_heavy,                        # seg3 -- remaining candidate B (sentinel trap)
        "Zoe closed the final chapter today.", # seg4 -- late
    ]
    manifest = build_manifest(body_texts)
    checked = ["Anna", "Bertrand Charlotte Desmond Eleanor Frederick", "Middle", "Zoe"]
    proc, report, _ = run_smoke(
        root, tmp_path, manifest, NO_PARTICLES_NO_ELISION,
        checked_names=checked, low_name_density_confirmed=True, no_particles_confirmed=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert report is not None
    used = report["source_sample_selection"]["segments_used"]
    high_density = [s["segment_id"] for s in used if s["anchor"] == "high_density"]
    assert high_density == ["seg1"]
    assert all(s["segment_id"] != "seg3" for s in used)
    assert report["candidate_names_total"] == 4
    assert report["pass"] is True


def test_sentinel_stripped_before_word_cap_regression(tmp_path, root):
    # Issue #89 follow-up (codex-rescue finding): stripping sentinels only
    # at the candidate-extraction call site happens too LATE relative to
    # cap_words() -- a sentinel occupying a "word" slot in the RAW
    # (unstripped) text consumes part of the SAMPLE_WORD_CAP (750) budget
    # before it's ever stripped, so a legitimate name sitting just past the
    # cap boundary can be silently dropped. One sentinel + 749 ordinary
    # filler words + "Bouchard" as the 751st raw word: capping-before-
    # stripping drops Bouchard entirely (the sentinel occupies a real word
    # slot pre-strip); stripping-before-capping correctly retains it (the
    # sentinel takes no real word budget once it's gone).
    filler = " ".join(f"word{i}" for i in range(749))
    text = f"⟦VERSE_V001_deadbeef⟧ {filler} Bouchard"
    manifest = build_manifest([text])
    proc, report, _ = run_smoke(
        root, tmp_path, manifest, NO_PARTICLES_NO_ELISION,
        checked_names=["Bouchard"],
        low_name_density_confirmed=True,
        no_particles_confirmed=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert report is not None
    assert report["candidate_names_total"] == 1
    by_name = {c["name"]: c["found"] for c in report["checked_names"]}
    assert by_name == {"Bouchard": True}
    assert report["pass"] is True
    # word_count must be exactly 750 (749 filler + Bouchard) -- proves the
    # sentinel itself never occupied a word-cap slot at all.
    assert report["source_sample_selection"]["word_count"] == 750


def test_empty_sample_with_no_body_and_no_frontback_is_fatal(tmp_path, root):
    manifest = build_manifest([])  # no body segments, no frontback entries
    proc, report, _ = run_smoke(root, tmp_path, manifest, NO_PARTICLES_NO_ELISION)
    assert proc.returncode == 2
    assert report is None
    assert "nothing to build a smoke-test sample from" in proc.stderr


# ---------------------------------------------------------------------------
# The fifth "frontback" anchor
# ---------------------------------------------------------------------------

def test_frontback_anchor_absent_when_no_translate_decision_frontback_exists(tmp_path, root):
    manifest = build_manifest(["Oskar visited a quiet port."], frontback_items=None)
    proc, report, _ = run_smoke(
        root, tmp_path, manifest, NO_PARTICLES_NO_ELISION,
        checked_names=["Oskar"], low_name_density_confirmed=True, no_particles_confirmed=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert report is not None
    used = report["source_sample_selection"]["segments_used"]
    assert used == [{"segment_id": "seg0", "anchor": "first", "kind": "body"}]
    assert all(s["kind"] != "frontback" for s in used)


def test_frontback_anchor_included_only_for_translate_decision_and_concatenates_all_of_them(
    tmp_path, root
):
    # Only "translate"-decision frontback entries may ever contribute --
    # "omit"/"regenerate" canaries must be excluded even though a matching
    # segment record physically exists for them in segments[] (stresses the
    # script's OWN decision-based filter, not just an upstream invariant).
    manifest = build_manifest(
        ["Oskar visited a quiet port."],
        frontback_items=[
            {"id": "FRONTBACK:cover", "decision": "omit",
             "text": "Omitcanary should never appear anywhere in candidates."},
            {"id": "FRONTBACK:preface1", "decision": "translate",
             "text": "Helena welcomed the guests warmly."},
            {"id": "FRONTBACK:preface2", "decision": "translate",
             "text": "Gustav offered a toast happily."},
            {"id": "FRONTBACK:toc", "decision": "regenerate",
             "text": "Regencanary must also never appear."},
        ],
    )
    proc, report, _ = run_smoke(
        root, tmp_path, manifest, NO_PARTICLES_NO_ELISION,
        checked_names=["Oskar", "Helena", "Gustav"],
        low_name_density_confirmed=True, no_particles_confirmed=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert report is not None
    used = report["source_sample_selection"]["segments_used"]
    assert used == [
        {"segment_id": "seg0", "anchor": "first", "kind": "body"},
        {"segment_id": "FRONTBACK:preface1", "anchor": "frontback", "kind": "frontback"},
        {"segment_id": "FRONTBACK:preface2", "anchor": "frontback", "kind": "frontback"},
    ]
    # candidate_names_total == 3 (Oskar/Helena/Gustav only) proves the
    # omit/regenerate canary text never reached extraction at all -- if it
    # had leaked in, this count would be 5 and the low-density set-coverage
    # check (checked_names must cover every distinct candidate) would have
    # failed this run with exit code 2 instead.
    assert report["candidate_names_total"] == 3
    assert report["pass"] is True


# ---------------------------------------------------------------------------
# The 10-name floor and its two escape branches
# ---------------------------------------------------------------------------

MANY_NAMES_TEXT = (
    "Alice sat quietly. Bob laughed loudly. Carol left early. Diana stayed late. "
    "Ethan called upon Fiona directly. George arrived quickly. Helen departed slowly. "
    "Irene waved warmly. Jack smiled brightly. Karen nodded once. Leo agreed finally."
)
MANY_NAMES = [
    "Alice", "Bob", "Carol", "Diana", "Ethan", "Fiona",
    "George", "Helen", "Irene", "Jack", "Karen", "Leo",
]

FEW_NAMES_TEXT = "Anna greeted Bob softly. Carol thanked Diana warmly."
FEW_NAMES = ["Anna", "Bob", "Carol", "Diana"]

ZERO_NAMES_TEXTS = [
    "quiet halls held no names today.",
    "silent rooms stayed empty tonight.",
]


def test_default_branch_requires_at_least_ten_checked_names(tmp_path, root):
    manifest = build_manifest([MANY_NAMES_TEXT])
    proc, report, _ = run_smoke(
        root, tmp_path, manifest, NO_PARTICLES_NO_ELISION,
        checked_names=MANY_NAMES[:5],  # only 5, below the 10-name floor
        no_particles_confirmed=True,
    )
    assert proc.returncode == 2
    assert report is None
    assert "at least 10" in proc.stderr


def test_default_branch_succeeds_with_ten_or_more_checked_names_and_marks_flags_false(
    tmp_path, root
):
    manifest = build_manifest([MANY_NAMES_TEXT])
    proc, report, _ = run_smoke(
        root, tmp_path, manifest, NO_PARTICLES_NO_ELISION,
        checked_names=MANY_NAMES,  # all 12
        no_particles_confirmed=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert report is not None
    assert report["candidate_names_total"] == 12
    assert report["low_name_density_confirmed"] is False
    assert report["no_names_confirmed"] is False
    assert report["pass"] is True
    # Single body segment -> first/middle/late dedupe to one anchor.
    assert report["source_sample_selection"]["segments_used"] == [
        {"segment_id": "seg0", "anchor": "first", "kind": "body"}
    ]


def test_low_name_density_branch_requires_confirmation_flag(tmp_path, root):
    manifest = build_manifest([FEW_NAMES_TEXT])
    proc, report, _ = run_smoke(
        root, tmp_path, manifest, NO_PARTICLES_NO_ELISION,
        checked_names=FEW_NAMES,  # 4 names, would satisfy count if flag were set
        no_particles_confirmed=True,
        # low_name_density_confirmed deliberately NOT passed
    )
    assert proc.returncode == 2
    assert report is None
    assert "--low-name-density-confirmed" in proc.stderr


def test_low_name_density_branch_requires_checked_names_count_to_exactly_match_candidates(
    tmp_path, root
):
    manifest = build_manifest([FEW_NAMES_TEXT])  # 4 candidates
    proc, report, _ = run_smoke(
        root, tmp_path, manifest, NO_PARTICLES_NO_ELISION,
        checked_names=FEW_NAMES[:2],  # only 2 of the 4 candidates
        low_name_density_confirmed=True,
        no_particles_confirmed=True,
    )
    assert proc.returncode == 2
    assert report is None
    assert "uncovered" in proc.stderr


def test_low_name_density_rejects_duplicate_entries_that_leave_a_candidate_uncovered(
    tmp_path, root
):
    # {Alice, Bob} candidates, --checked-names Alice,Alice -- pre-fix this
    # satisfied the dedup-blind len(checked_names) == candidate_names_total
    # count check (2 == 2) even though Bob was never checked. Dedup-aware
    # set-coverage must reject it: parse_checked_names collapses the
    # duplicate to a single distinct entry, leaving Bob uncovered.
    manifest = build_manifest(["Alice sat quietly. Bob laughed today."])
    proc, report, _ = run_smoke(
        root, tmp_path, manifest, NO_PARTICLES_NO_ELISION,
        checked_names=["Alice", "Alice"],
        low_name_density_confirmed=True,
        no_particles_confirmed=True,
    )
    assert proc.returncode == 2
    assert report is None
    assert "uncovered" in proc.stderr
    assert "Bob" in proc.stderr


def test_default_branch_rejects_ten_duplicate_entries_of_one_name(tmp_path, root):
    # 12 real candidates (MANY_NAMES_TEXT), but --checked-names supplies the
    # SAME name ten times. Pre-fix, parse_checked_names() didn't dedup, so
    # len(checked_names) == 10 satisfied the >= LOW_NAME_DENSITY_FLOOR check
    # though only one distinct name was ever actually checked. Dedup-aware
    # counting must collapse this to 1 distinct name and refuse to run.
    manifest = build_manifest([MANY_NAMES_TEXT])
    proc, report, _ = run_smoke(
        root, tmp_path, manifest, NO_PARTICLES_NO_ELISION,
        checked_names=["Alice"] * 10,
        no_particles_confirmed=True,
    )
    assert proc.returncode == 2
    assert report is None
    assert "distinct" in proc.stderr


def test_low_name_density_branch_succeeds_when_count_matches(tmp_path, root):
    manifest = build_manifest([FEW_NAMES_TEXT])
    proc, report, _ = run_smoke(
        root, tmp_path, manifest, NO_PARTICLES_NO_ELISION,
        checked_names=FEW_NAMES,  # exactly 4 == candidate_names_total
        low_name_density_confirmed=True,
        no_particles_confirmed=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert report is not None
    assert report["candidate_names_total"] == 4
    assert report["low_name_density_confirmed"] is True
    assert report["no_names_confirmed"] is False
    assert all(c["found"] for c in report["checked_names"])
    assert report["pass"] is True


def test_zero_candidate_branch_requires_low_density_flag_too(tmp_path, root):
    manifest = build_manifest(ZERO_NAMES_TEXTS)
    proc, report, _ = run_smoke(
        root, tmp_path, manifest, NO_PARTICLES_NO_ELISION,
        no_names_confirmed=True,  # missing --low-name-density-confirmed
        no_particles_confirmed=True,
    )
    assert proc.returncode == 2
    assert report is None


def test_zero_candidate_branch_requires_no_names_flag_too(tmp_path, root):
    manifest = build_manifest(ZERO_NAMES_TEXTS)
    proc, report, _ = run_smoke(
        root, tmp_path, manifest, NO_PARTICLES_NO_ELISION,
        low_name_density_confirmed=True,  # missing --no-names-confirmed
        no_particles_confirmed=True,
    )
    assert proc.returncode == 2
    assert report is None


def test_zero_candidate_branch_succeeds_with_both_flags_and_empty_checked_names(tmp_path, root):
    manifest = build_manifest(ZERO_NAMES_TEXTS)
    proc, report, _ = run_smoke(
        root, tmp_path, manifest, NO_PARTICLES_NO_ELISION,
        low_name_density_confirmed=True,
        no_names_confirmed=True,
        no_particles_confirmed=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert report is not None
    assert report["candidate_names_total"] == 0
    assert report["checked_names"] == []
    assert report["low_name_density_confirmed"] is True
    assert report["no_names_confirmed"] is True
    assert report["pass"] is True


def test_no_names_confirmed_rejected_when_candidates_nonzero(tmp_path, root):
    # --no-names-confirmed is reserved for the genuinely zero-candidate case
    # -- must be refused even for a merely-sparse (nonzero, <10) sample.
    manifest = build_manifest([FEW_NAMES_TEXT])  # 4 candidates, nonzero
    proc, report, _ = run_smoke(
        root, tmp_path, manifest, NO_PARTICLES_NO_ELISION,
        no_names_confirmed=True,
        no_particles_confirmed=True,
    )
    assert proc.returncode == 2
    assert report is None
    assert "reserved for the genuinely zero-candidate case" in proc.stderr


# ---------------------------------------------------------------------------
# particle_smoke_cases -- decoupled from name density, keyed ONLY to
# particle_list_size > 0
# ---------------------------------------------------------------------------

def test_particle_smoke_required_even_under_zero_candidate_escape(tmp_path, root):
    # Name branch is fully satisfied (zero-candidate escape, both flags
    # given) -- but particle_list_size > 0 still fatally requires
    # --particle-smoke-file, proving the requirement is NOT gated on the
    # name-density branch at all.
    manifest = build_manifest(ZERO_NAMES_TEXTS)
    lang = particle_config_payload(particles=["de", "von"])
    proc, report, _ = run_smoke(
        root, tmp_path, manifest, lang,
        low_name_density_confirmed=True,
        no_names_confirmed=True,
        # no --particle-smoke-file, no --no-particles-confirmed
    )
    assert proc.returncode == 2
    assert report is None
    assert "decoupled from name density" in proc.stderr


def test_particle_smoke_required_even_under_default_branch_with_many_names(tmp_path, root):
    # Symmetric case: the DEFAULT (>=10 checked names) name branch is fully
    # satisfied, yet the particle-free escape is still independently
    # required when particle_list_size == 0.
    manifest = build_manifest([MANY_NAMES_TEXT])
    lang = particle_config_payload(particles=[])
    proc, report, _ = run_smoke(
        root, tmp_path, manifest, lang,
        checked_names=MANY_NAMES,
        # no --no-particles-confirmed
    )
    assert proc.returncode == 2
    assert report is None
    assert "particle-free language" in proc.stderr


def test_no_particles_confirmed_rejected_when_particle_list_nonempty(tmp_path, root):
    manifest = build_manifest([FEW_NAMES_TEXT])
    lang = particle_config_payload(particles=["de"])
    proc, report, _ = run_smoke(
        root, tmp_path, manifest, lang,
        checked_names=FEW_NAMES, low_name_density_confirmed=True,
        no_particles_confirmed=True,  # misuse: reserved for particle-free languages
    )
    assert proc.returncode == 2
    assert report is None
    assert "reserved for a genuinely particle-free language" in proc.stderr


def test_particle_free_language_succeeds_with_no_particles_confirmed(tmp_path, root):
    manifest = build_manifest([FEW_NAMES_TEXT])
    proc, report, _ = run_smoke(
        root, tmp_path, manifest, NO_PARTICLES_NO_ELISION,
        checked_names=FEW_NAMES, low_name_density_confirmed=True,
        no_particles_confirmed=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert report is not None
    assert report["particle_list_size"] == 0
    assert report["no_particles_confirmed"] is True
    assert report["particle_smoke_cases"] == []
    assert report["pass"] is True


def test_particle_smoke_cases_computed_correctly_when_provided(tmp_path, root):
    manifest = build_manifest([FEW_NAMES_TEXT])
    lang = particle_config_payload(particles=["de", "von"])
    cases = [
        {"token": "De", "is_particle": True},     # case-folds to "de" -> True
        {"token": "VON", "is_particle": True},    # case-folds to "von" -> True
        {"token": "chateau", "is_particle": False},
        {"token": "Anna", "is_particle": False},
    ]
    proc, report, _ = run_smoke(
        root, tmp_path, manifest, lang,
        checked_names=FEW_NAMES, low_name_density_confirmed=True,
        particle_cases=cases,
    )
    assert proc.returncode == 0, proc.stderr
    assert report is not None
    assert report["particle_list_size"] == 2
    assert report["no_particles_confirmed"] is False
    assert report["particle_smoke_cases"] == [
        {"token": "De", "is_particle": True, "passed": True},
        {"token": "VON", "is_particle": True, "passed": True},
        {"token": "chateau", "is_particle": False, "passed": True},
        {"token": "Anna", "is_particle": False, "passed": True},
    ]
    assert report["pass"] is True


# ---------------------------------------------------------------------------
# elision_test_cases -- conditional requirement driven by the in-report
# has_elision field, copied verbatim from the resolved particle_config file
# ---------------------------------------------------------------------------

FR_ELISION_RE = "^([dl])['’](.*)$"  # exactly 2 capture groups


def test_elision_test_file_required_when_has_elision_true(tmp_path, root):
    manifest = build_manifest([FEW_NAMES_TEXT])
    lang = particle_config_payload(has_elision=True, elision_re=FR_ELISION_RE)
    proc, report, _ = run_smoke(
        root, tmp_path, manifest, lang,
        checked_names=FEW_NAMES, low_name_density_confirmed=True,
        no_particles_confirmed=True,
        # elision_cases deliberately omitted
    )
    assert proc.returncode == 2
    assert report is None
    assert "--elision-test-file was not given" in proc.stderr


def test_elision_test_file_rejected_when_has_elision_false(tmp_path, root):
    manifest = build_manifest([FEW_NAMES_TEXT])
    proc, report, _ = run_smoke(
        root, tmp_path, manifest, NO_PARTICLES_NO_ELISION,
        checked_names=FEW_NAMES, low_name_density_confirmed=True,
        no_particles_confirmed=True,
        elision_cases=[{"sentence": "d'Effiat arriva.", "expected_names": ["Effiat"]}],
    )
    assert proc.returncode == 2
    assert report is None
    assert "has_elision is false" in proc.stderr
    assert "--elision-test-file was given" in proc.stderr


def test_has_elision_field_copied_from_resolved_particle_config(tmp_path, root):
    manifest = build_manifest([FEW_NAMES_TEXT])

    _, report_false, _ = run_smoke(
        root, tmp_path, manifest, NO_PARTICLES_NO_ELISION,
        checked_names=FEW_NAMES, low_name_density_confirmed=True,
        no_particles_confirmed=True,
    )
    assert report_false is not None
    assert report_false["has_elision"] is False

    lang_true = particle_config_payload(has_elision=True, elision_re=FR_ELISION_RE)
    _, report_true, _ = run_smoke(
        root, tmp_path, manifest, lang_true,
        checked_names=FEW_NAMES, low_name_density_confirmed=True,
        no_particles_confirmed=True,
        elision_cases=[{"sentence": "d'Effiat arriva.", "expected_names": ["Effiat"]}],
    )
    assert report_true is not None
    assert report_true["has_elision"] is True


def test_elision_test_cases_pass_when_elided_names_are_produced(tmp_path, root):
    manifest = build_manifest(["Oskar visited a quiet port."])
    lang = particle_config_payload(particles=["de", "von"], has_elision=True, elision_re=FR_ELISION_RE)
    elision_cases = [
        {"sentence": "Il visita le chateau d'Effiat hier.", "expected_names": ["Effiat"]},
        {"sentence": "Elle vint de l'Autriche bientot.", "expected_names": ["Autriche"]},
    ]
    particle_cases = [
        {"token": "de", "is_particle": True},
        {"token": "chateau", "is_particle": False},
    ]
    proc, report, _ = run_smoke(
        root, tmp_path, manifest, lang,
        checked_names=["Oskar"], low_name_density_confirmed=True,
        elision_cases=elision_cases, particle_cases=particle_cases,
    )
    assert proc.returncode == 0, proc.stderr
    assert report is not None
    assert len(report["elision_test_cases"]) == 2
    assert all(c["passed"] for c in report["elision_test_cases"])
    assert report["pass"] is True


def test_elision_test_case_marked_failed_when_expected_name_not_produced(tmp_path, root):
    manifest = build_manifest(["Oskar visited a quiet port."])
    lang = particle_config_payload(has_elision=True, elision_re=FR_ELISION_RE)
    elision_cases = [
        {"sentence": "Il visita le chateau d'Effiat hier.", "expected_names": ["WrongName"]},
    ]
    proc, report, _ = run_smoke(
        root, tmp_path, manifest, lang,
        checked_names=["Oskar"], low_name_density_confirmed=True,
        no_particles_confirmed=True,
        elision_cases=elision_cases,
    )
    assert proc.returncode == 1  # report written, pass:false
    assert report is not None
    assert report["elision_test_cases"][0]["passed"] is False
    assert report["pass"] is False


# ---------------------------------------------------------------------------
# Exit-code semantics / overall `pass` combination (0=pass, 1=fail-but-
# written, 2=usage error with no report at all) -- cross-cutting checks
# ---------------------------------------------------------------------------

def test_checked_name_not_found_marks_pass_false_and_exit_code_one(tmp_path, root):
    # Default branch (>=10 distinct checked names, no set-coverage
    # requirement), so a typo'd extra entry can coexist with full coverage
    # of the real candidates and still reach the found-status stage -- on
    # the low-density branch this same typo would now correctly leave a
    # real candidate uncovered and FATAL earlier (see the two
    # set-coverage tests above).
    manifest = build_manifest([MANY_NAMES_TEXT])  # 12 real candidates
    checked = [n if n != "Carol" else "Carolz" for n in MANY_NAMES]  # "Carolz" is a deliberate typo
    proc, report, _ = run_smoke(
        root, tmp_path, manifest, NO_PARTICLES_NO_ELISION,
        checked_names=checked,
        no_particles_confirmed=True,
    )
    assert proc.returncode == 1
    assert report is not None
    by_name = {c["name"]: c["found"] for c in report["checked_names"]}
    assert by_name["Alice"] is True
    assert by_name["Bob"] is True
    assert by_name["Carolz"] is False
    assert by_name["Diana"] is True
    assert report["pass"] is False


def test_particle_smoke_case_mismatch_marks_pass_false_and_exit_code_one(tmp_path, root):
    manifest = build_manifest([FEW_NAMES_TEXT])
    lang = particle_config_payload(particles=["de"])
    cases = [{"token": "de", "is_particle": False}]  # wrong: "de" IS a particle here
    proc, report, _ = run_smoke(
        root, tmp_path, manifest, lang,
        checked_names=FEW_NAMES, low_name_density_confirmed=True,
        particle_cases=cases,
    )
    assert proc.returncode == 1
    assert report is not None
    assert report["particle_smoke_cases"][0]["passed"] is False
    assert report["pass"] is False


# ---------------------------------------------------------------------------
# Schema conformance -- the written report must genuinely satisfy
# language-smoke-report.schema.json's if/then conditionals, not merely the
# script's own internal (also schema-validating) opinion of itself.
# ---------------------------------------------------------------------------

def test_report_matches_json_schema_on_success(tmp_path, root):
    # Exercises BOTH conditional branches at once: has_elision:true requires
    # elision_test_cases (minItems 1); particle_list_size>0 requires
    # particle_smoke_cases (minItems 1); low_name_density_confirmed:true
    # relaxes checked_names' floor from 10 down to minItems 1.
    manifest = build_manifest(["Oskar visited a quiet port."])
    lang = particle_config_payload(particles=["de", "von"], has_elision=True, elision_re=FR_ELISION_RE)
    elision_cases = [
        {"sentence": "Il visita le chateau d'Effiat hier.", "expected_names": ["Effiat"]},
    ]
    particle_cases = [{"token": "de", "is_particle": True}]
    proc, report, _ = run_smoke(
        root, tmp_path, manifest, lang,
        checked_names=["Oskar"], low_name_density_confirmed=True,
        elision_cases=elision_cases, particle_cases=particle_cases,
    )
    assert proc.returncode == 0, proc.stderr
    schema = json.loads(SCHEMA_SRC.read_text(encoding="utf-8"))
    jsonschema.validate(instance=report, schema=schema)


# ---------------------------------------------------------------------------
# Issue #91 -- investigated and NOT fixed: widening ELISION_RE to also split
# a sentence-initial CAPITALIZED elision (e.g. treating "L'Enclos" like
# lowercase "d'Effiat") would collide with a deliberate, documented design
# decision protecting fixed proper-noun spellings such as "D'Artagnan" /
# "L'Aquila" (see assets/languages/README.md's it.json entry) -- those must
# stay fused as a single token, not be split into an article + name. Loaded
# via importlib (module under test lives outside any Python package) to
# call extract_candidate_names() directly with a hand-built lang dict,
# mirroring bootstrap_names.test.py's make_lang() pattern -- self-contained
# so it doesn't depend on fr.json's shipped ELISION_RE staying lowercase-only.
# ---------------------------------------------------------------------------

def _load_language_smoke_report_module():
    spec = importlib.util.spec_from_file_location(
        "language_smoke_report_under_test", SCRIPT_SRC
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_lsr = _load_language_smoke_report_module()


def make_lang_dict(particles=(), stopwords=(), elision_pattern=None, has_elision=None,
                    name_inventory=()):
    """Build the same dict shape ``load_particle_config()`` returns, bypassing
    the JSON file entirely, for a pure algorithm-level test."""
    elision_re = re.compile(elision_pattern) if elision_pattern else None
    if has_elision is None:
        has_elision = elision_re is not None
    return {
        "raw_bytes": b"{}",
        "particles": list(particles),
        "particles_lower": {p.lower() for p in particles},
        "stopwords": set(stopwords),
        "has_elision": has_elision,
        "elision_re": elision_re,
        "name_inventory": frozenset(name_inventory),
    }


def test_extract_candidate_names_keeps_fixed_capitalized_elision_fused_regression():
    # ELISION_RE (FR_ELISION_RE above) matches ONLY a lowercase remnant +
    # apostrophe + capitalized remainder ("d'Effiat", "l'Autriche") -- a
    # sentence-initial or otherwise capitalized elided form ("D'Artagnan")
    # never matches it (no re.IGNORECASE), so the whole raw apostrophe-
    # joined token is appended as-is by the tokenizer's non-elided branch
    # and, already starting with a capital, survives the is_upper_initial()
    # gate intact as ONE fused candidate -- exactly the documented behavior
    # this file's own tokenizer must preserve alongside bootstrap_names.py's.
    lang = make_lang_dict(elision_pattern=FR_ELISION_RE)
    produced = {
        name
        for name, _ in _lsr.extract_candidate_names(
            "D'Artagnan arriva. Puis d'Effiat partit.", lang
        )
    }
    assert "D'Artagnan" in produced
    assert "Artagnan" not in produced
    assert "Effiat" in produced


# ---------------------------------------------------------------------------
# #225 -- offset-safe, mark-inclusive tokenizer. This file's SEPARATE copy of
# TOKEN_RE must stay byte-identical to bootstrap_names.py's (see
# extractor_terminators_drift.test.py::test_token_re_identical_across_both_
# extractors). A pointed/vocalized word is ONE token, and the curated mark
# class is category-M-pure -- mirroring bootstrap_names.test.py's own backstop.
# ---------------------------------------------------------------------------

POINTED_HEBREW = "שָׁלוֹם"
VOCALIZED_ARABIC = "سَلَام"
NFD_RESUME = unicodedata.normalize("NFD", "résumé")

MARK_SUPER_RANGES = (
    (0x0300, 0x036F), (0x1AB0, 0x1ACE), (0x1DC0, 0x1DFF), (0xFE20, 0xFE2F),
    (0x0483, 0x0489), (0x0591, 0x05C7), (0x0610, 0x06ED), (0x0870, 0x08FF),
)


def test_lsr_tokenizer_keeps_pointed_forms_single_token():
    # Pre-#225 each of these shattered into one token per base letter.
    assert len(list(_lsr.TOKEN_RE.finditer(POINTED_HEBREW))) == 1
    assert len(list(_lsr.TOKEN_RE.finditer(VOCALIZED_ARABIC))) == 1
    assert len(list(_lsr.TOKEN_RE.finditer(NFD_RESUME))) == 1


def test_lsr_tokenizer_keeps_arabic_extended_mark_single_token():
    # #225 follow-up (codex Medium) mirror of bootstrap_names.test.py's own
    # regression: U+08F0 ARABIC OPEN FATHATAN (Arabic Extended-A/B, curated
    # sub-ranges pre-fix stopped at U+06ED) must stay fused into its letter's
    # token here too, since this file's TOKEN_RE is byte-identical.
    assert _lsr.TOKEN_RE.findall("ا" + "ࣰ" + "ب") == ["اࣰب"]


def test_lsr_tokenizer_nfc_latin_and_connectors_unchanged():
    assert _lsr.TOKEN_RE.findall("Saint-Simon") == ["Saint-Simon"]
    assert _lsr.TOKEN_RE.findall("aujourd'hui") == ["aujourd'hui"]
    assert _lsr.TOKEN_RE.findall("Fiona’ George") == ["Fiona", "George"]


def test_lsr_mark_class_accepts_only_combining_marks():
    cls = re.compile("[" + _lsr._MARK_CLASS + "]")
    non_marks = sorted(
        (hex(cp), unicodedata.category(chr(cp)))
        for lo, hi in _lsr._MARK_SUBRANGES
        for cp in range(lo, hi + 1)
        if cls.match(chr(cp)) and not unicodedata.category(chr(cp)).startswith("M")
    )
    assert non_marks == [], f"mark class accepts non-M codepoints: {non_marks}"


def test_lsr_mark_class_omits_no_mark_within_super_ranges():
    cls = re.compile("[" + _lsr._MARK_CLASS + "]")
    omitted = sorted(
        hex(cp)
        for lo, hi in MARK_SUPER_RANGES
        for cp in range(lo, hi + 1)
        if unicodedata.category(chr(cp)).startswith("M") and not cls.match(chr(cp))
    )
    assert omitted == [], f"category-M codepoint(s) in a claimed span not covered: {omitted}"


# ---------------------------------------------------------------------------
# #282/#283 -- Hebrew ASCII-punctuation connector equivalence, lsr-side
# mirrors of bootstrap_names.test.py's own tests (this file's TOKEN_RE and
# _fold_token_to_units are independently-copied twins -- see
# extractor_terminators_drift.test.py's drift guard for the pattern-level
# parity proof; the tests below catch a future accidental BEHAVIORAL
# divergence of that drift guard itself). ASCII double-quote (U+0022)
# between two Hebrew letters behaves as gershayim (#282); fold-time, the
# ASCII/Latin twins of the three NAME_CONNECTORS members (plus the ASCII
# quote) split a Hebrew-scoped compound the same way their maqaf/geresh/
# gershayim counterparts do (#283).
# ---------------------------------------------------------------------------
ASCII_QUOTE_ACRONYM_NAME = "מוהרנ" + '"' + "ת"  # R. Nathan of Breslov
ASCII_QUOTE_ACRONYM_TEXT = f"פגשתי את {ASCII_QUOTE_ACRONYM_NAME} אתמול."


def test_lsr_tokenizer_hebrew_ascii_quote_acronym_stays_one_token():
    assert _lsr.TOKEN_RE.findall(ASCII_QUOTE_ACRONYM_NAME) == [ASCII_QUOTE_ACRONYM_NAME]


def test_lsr_tokenizer_ascii_quote_latin_base_with_hebrew_mark_does_not_fuse():
    # codex round-1 adversarial regression, lsr side: a Latin base letter
    # carrying a trailing Hebrew mark must NOT be treated as a Hebrew base
    # letter -- the lookbehind proves the actual base letter, not just the
    # character immediately adjacent to the quote.
    text_1 = "A" + chr(0x05B0) + '"' + "ב"
    text_2 = "A" + chr(0x05B0) + chr(0x05B1) + '"' + "ב"
    assert _lsr.TOKEN_RE.findall(text_1) == ["A" + chr(0x05B0), "ב"]
    assert _lsr.TOKEN_RE.findall(text_2) == ["A" + chr(0x05B0) + chr(0x05B1), "ב"]


def test_lsr_extract_candidate_names_hebrew_ascii_quote_acronym_surfaces_via_inventory():
    # Hebrew has no case distinction, so pass 1 (is_upper_initial) never
    # fires for this text -- only pass 2's inventory route (#204) can
    # surface it, which is exactly what the #282 TOKEN_RE fix unblocks
    # (pre-fix the acronym tokenized into two pieces and the TERMINATORS
    # boundary refusal blocked pass 2 from ever bridging them).
    lang = make_lang_dict(name_inventory=[ASCII_QUOTE_ACRONYM_NAME])
    out = _lsr.extract_candidate_names(ASCII_QUOTE_ACRONYM_TEXT, lang)
    names = {n for n, _mid in out}
    assert ASCII_QUOTE_ACRONYM_NAME in names, f"{ASCII_QUOTE_ACRONYM_NAME!r} missing from {sorted(names)}"
    # must not have split into the two bare halves.
    assert "מוהרנ" not in names
    assert "ת" not in names


ASCII_HYPHEN_NAME = "הבעל" + "-" + "שם" + "-" + "טוב"  # ASCII-hyphen-joined compound
SPACE_JOINED_NAME = "הבעל שם טוב"  # space-joined equivalent


def test_lsr_extract_candidate_names_hebrew_ascii_hyphen_matches_space_joined():
    # #283 repro: TOKEN_RE already fuses the ASCII-hyphen-joined spelling
    # into one token (the hyphen was already a universal connector, no
    # change there); without the #283 fold-time Hebrew-scoped split, that
    # one token's match units would never agree with the three-token
    # space-joined inventory entry.
    lang = make_lang_dict(name_inventory=[SPACE_JOINED_NAME])
    text = f"פגשתי את {ASCII_HYPHEN_NAME} אתמול."
    out = _lsr.extract_candidate_names(text, lang)
    assert ASCII_HYPHEN_NAME in {n for n, _mid in out}


GERSHAYIM_ACRONYM_NAME = "מוהרנ" + "״" + "ת"  # same acronym, gershayim-joined
SPACE_JOINED_ACRONYM_NAME = "מוהרנ" + " " + "ת"  # same acronym, space-joined


@pytest.mark.parametrize(
    "inventory_entry,text_name",
    [
        (GERSHAYIM_ACRONYM_NAME, ASCII_QUOTE_ACRONYM_NAME),
        (SPACE_JOINED_ACRONYM_NAME, ASCII_QUOTE_ACRONYM_NAME),
        (ASCII_QUOTE_ACRONYM_NAME, GERSHAYIM_ACRONYM_NAME),
        (ASCII_QUOTE_ACRONYM_NAME, SPACE_JOINED_ACRONYM_NAME),
    ],
)
def test_lsr_extract_candidate_names_hebrew_ascii_quote_acronym_matches_gershayim_and_space_joined(
    inventory_entry, text_name
):
    # codex-round-2 lock, lsr side: the round-2-submitted split class omitted
    # the ASCII quote itself, so an ASCII-quoted acronym fused by the #282
    # TOKEN_RE fix folded to a DIFFERENT unit sequence than its gershayim/
    # space-joined equivalents -- this test proves all three spellings
    # converge via extract_candidate_names()'s own inventory route (not just
    # _fold_token_to_units() in isolation), in both match directions.
    lang = make_lang_dict(name_inventory=[inventory_entry])
    text = f"פגשתי את {text_name} אתמול."
    out = _lsr.extract_candidate_names(text, lang)
    assert text_name in {n for n, _mid in out}


# ---------------------------------------------------------------------------
# name_inventory coverage census (#284)
#
# The census answers ONE question -- "does this name_inventory form have any
# token-aligned occurrence in the book at all?" -- over the WHOLE manifest,
# and prints the answer. It is deliberately absent from the report (a
# sample-keyed artifact must not carry a whole-book fact), so every assertion
# here is against stdout/stderr or against the census functions themselves.
# ---------------------------------------------------------------------------

BOOTSTRAP_PATH = (
    PLUGIN_ROOT
    / "skills"
    / "literary-translator"
    / "assets"
    / "scripts"
    / "bootstrap_names.py"
)

assert BOOTSTRAP_PATH.is_file(), f"bootstrap_names.py not found at {BOOTSTRAP_PATH}"


def _load_bootstrap_names_module():
    """The PRODUCTION extractor, loaded the same way _lsr is -- used only by
    the elision-parity test, which asserts the two agree rather than
    asserting a hand-written expectation about either. Same guards as this
    file's own module loader: a moved or unloadable path must fail as itself,
    not as an AttributeError inside the parity assertion."""
    spec = importlib.util.spec_from_file_location(
        "bootstrap_names_under_test_from_lsr_tests", BOOTSTRAP_PATH
    )
    assert spec is not None and spec.loader is not None, (
        f"could not load spec for {BOOTSTRAP_PATH}"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def zero_forms(text_pieces, lang):
    return set(lang["name_inventory"]) - _lsr.inventory_forms_seen(list(text_pieces), lang)


BARDITCHEV = "בארדיטשוב"                 # the bare toponym, as the inventory lists it
FROM_BARDITCHEV = "מ" + BARDITCHEV       # the only form the book actually uses (mi- proclitic)


def test_inventory_census_scans_blocks_outside_the_stratified_sample(tmp_path, root):
    # THE test that pins full-manifest scope. With 6 body segments the sample
    # picks seg0/seg1/seg3/seg5 (first/middle/late/high-density); an UNCASED
    # form raises no density score, so seg4 is never selected and the sample
    # never sees it -- candidate_names_total stays 1. The census must still
    # find it. This fails the moment the census is narrowed to the sample.
    body = [f"Anna walked in segment {i}." for i in range(6)]
    body[4] = f"Anna walked here with {BARDITCHEV} nearby."
    manifest = build_manifest(body)
    config = particle_config_payload()
    config["name_inventory"] = [BARDITCHEV]
    proc, report, _ = run_smoke(
        root, tmp_path, manifest, config,
        checked_names=["Anna"], low_name_density_confirmed=True, no_particles_confirmed=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert report is not None
    used = {s["segment_id"] for s in report["source_sample_selection"]["segments_used"]}
    assert "seg4" not in used, f"fixture no longer excludes seg4 from the sample: {used}"
    assert report["candidate_names_total"] == 1  # the sample saw only "Anna"
    assert "inventory_forms_total       = 1" in proc.stdout
    assert "inventory_zero_match_forms  = 0" in proc.stdout


def test_inventory_census_reports_a_bare_form_the_book_only_writes_prefixed(tmp_path, root):
    # #284 itself: Hebrew proclitics fuse, matching is exact-form and
    # token-aligned, so the bare inventory entry can never reach the fused
    # surface token -- today that is silent, and this is the report of it.
    manifest = build_manifest([f"Anna met the rabbi {FROM_BARDITCHEV} today."])
    config = particle_config_payload()
    config["name_inventory"] = [BARDITCHEV]
    proc, _report, _ = run_smoke(
        root, tmp_path, manifest, config,
        checked_names=["Anna"], low_name_density_confirmed=True, no_particles_confirmed=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert "inventory_zero_match_forms  = 1" in proc.stdout
    assert BARDITCHEV in proc.stderr
    assert "NO token-aligned occurrence" in proc.stderr


def test_inventory_census_clears_once_the_attested_surface_form_is_listed(tmp_path, root):
    # The documented remedy (add the exact surface form the book uses) must
    # actually close the finding -- otherwise the WARN sends the operator
    # somewhere that does not work.
    manifest = build_manifest([f"Anna met the rabbi {FROM_BARDITCHEV} today."])
    config = particle_config_payload()
    config["name_inventory"] = [BARDITCHEV, FROM_BARDITCHEV]
    proc, _report, _ = run_smoke(
        root, tmp_path, manifest, config,
        # FROM_BARDITCHEV is now surfaced by the inventory bypass, so the
        # low-density path's set-coverage rule requires it to be checked too.
        checked_names=["Anna", FROM_BARDITCHEV],
        low_name_density_confirmed=True, no_particles_confirmed=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert "inventory_forms_total       = 2" in proc.stdout
    assert "inventory_zero_match_forms  = 1" in proc.stdout   # the bare form still never occurs
    assert FROM_BARDITCHEV not in proc.stderr.split("NO token-aligned occurrence")[-1]


def test_inventory_census_counts_every_terminal_not_the_emission_winner():
    # pass 2 emits AT MOST ONE candidate per position (the longest fresh one),
    # so "a" is never emitted from "a b" -- an emitted-candidates census would
    # report it unmatched. Measured on the live he->en book, three real
    # inventory forms are in exactly this position.
    lang = make_lang_dict(name_inventory=["a", "a b"])
    emitted = {n for n, _mid in _lsr.extract_candidate_names("a b", lang)}
    assert emitted == {"a b"}, emitted
    assert zero_forms(["a b"], lang) == set()


def test_inventory_census_refuses_a_sub_token_match():
    # Token alignment: a bare form inside a longer connector-joined token is
    # NOT an occurrence of that form (the same rule pass 2 enforces).
    lang = make_lang_dict(name_inventory=["משה"])
    assert zero_forms(["פגשתי את משה־לייב אתמול."], lang) == {"משה"}


def test_inventory_census_refuses_a_match_across_a_terminator():
    lang = make_lang_dict(name_inventory=["a b"])
    assert zero_forms(["a. b"], lang) == {"a b"}


def test_inventory_census_reports_a_form_that_tokenizes_to_nothing():
    # name_inventory only requires non-empty strings, so a punctuation-only
    # entry is accepted and then dropped by the trie builder's own `if f`
    # filter. Reporting it as unmatched is the honest verdict; omitting it
    # would hide an entry that can never match anything.
    lang = make_lang_dict(name_inventory=["!!!", "Anna"])
    assert zero_forms(["Anna walked here."], lang) == {"!!!"}


def test_inventory_census_gives_fold_colliding_forms_one_verdict():
    # Two surface forms folding to the same #238/#241 match key share one
    # trie path, so they must share one verdict -- either both seen or both
    # not, never a split that reads as a real difference between them.
    pointed = "בְּרֶסְלֶב"
    unpointed = "ברסלב"
    lang = make_lang_dict(name_inventory=[pointed, unpointed])
    assert _lsr.match_units(pointed) == _lsr.match_units(unpointed)
    assert zero_forms([f"נסע ל {unpointed} בשנת תקפ״ג."], lang) == set()
    assert zero_forms(["nothing relevant here"], lang) == {pointed, unpointed}


def test_inventory_census_names_every_zero_form_not_just_the_first(tmp_path, root):
    # No cap on the list: a truncated one is the silent under-report this
    # census exists to end. The WARN's own rendering of the sorted set is
    # asserted verbatim, so dropping or adding one form fails here.
    missing_a = "אוסטרהא"
    missing_b = "זלאטיפאלי"
    manifest = build_manifest(["Anna walked in a quiet port."])
    config = particle_config_payload()
    config["name_inventory"] = [missing_a, missing_b]
    proc, _report, _ = run_smoke(
        root, tmp_path, manifest, config,
        checked_names=["Anna"], low_name_density_confirmed=True, no_particles_confirmed=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert "inventory_zero_match_forms  = 2" in proc.stdout
    warn = [ln for ln in proc.stderr.splitlines() if "NO token-aligned occurrence" in ln]
    assert len(warn) == 1, proc.stderr
    assert repr(sorted([missing_a, missing_b])) in warn[0], warn[0]


def test_inventory_census_matches_a_connector_joined_token_from_a_space_joined_entry():
    # One SOURCE token contributing several match units -- the whole-token
    # descent has to consume all of them before consulting `None in node`.
    space_joined = "משה לייב"
    maqaf_joined = "משה" + "־" + "לייב"
    lang = make_lang_dict(name_inventory=[space_joined])
    assert zero_forms([f"פגשתי את {maqaf_joined} אתמול."], lang) == set()


def test_inventory_census_counts_a_multi_token_form_across_an_inline_sentinel():
    # ⟦FNREF_N⟧/⟦VERSE_...⟧ sentinels are production input. They are stripped by
    # _inventory_scan_pieces (exactly as the sample path strips them, and as
    # bootstrap_names.py's own same-length mask_sentinels() does), so they must
    # not break a multi-token inventory form that spans one. Driven through
    # _inventory_scan_pieces rather than a hand-built piece list, so the test
    # pins the strip at the seam that actually owns it.
    lang = make_lang_dict(name_inventory=["Marie Claire"])
    manifest = build_manifest(["Marie ⟦FNREF_5⟧ Claire went home."])
    pieces = _lsr._inventory_scan_pieces(manifest)
    assert zero_forms(pieces, lang) == set()


def test_inventory_scan_pieces_takes_every_non_empty_block_whatever_its_kind():
    # The production scan scope is bootstrap_names.iter_manifest_texts(): every
    # non-empty block's plain_text, filtered by NEITHER a block's `type` nor a
    # segment's `kind` nor a frontback `decision`. An omit-decision frontback
    # block is still scanned by the production extractor, so a form living only
    # there must not be reported as never occurring -- while build_source_sample
    # deliberately skips exactly that block.
    manifest = build_manifest(
        ["Anna walked in a quiet port."],
        frontback_items=[{"id": "fb1", "decision": "omit", "text": "Anna met משה כהן."}],
    )
    pieces = _lsr._inventory_scan_pieces(manifest)
    assert len(pieces) == 2
    lang = make_lang_dict(name_inventory=["משה כהן"])
    assert _lsr.inventory_forms_seen(pieces, lang) == {"משה כהן"}


def test_inventory_scan_pieces_never_joins_two_blocks():
    # Pieces stay separate for the same reason build_source_sample's
    # extraction_pieces do: joining non-adjacent text fabricates a match that
    # no per-block production scan could produce.
    manifest = build_manifest(["Anna met משה", "כהן walked home."])
    pieces = _lsr._inventory_scan_pieces(manifest)
    lang = make_lang_dict(name_inventory=["משה כהן"])
    assert _lsr.inventory_forms_seen(pieces, lang) == set()


def test_inventory_census_tokenizes_elision_exactly_like_the_production_extractor():
    # #284 parity: _tokenize() splits on the compiled pattern ALONE, exactly
    # like production. This file used to gate the split on has_elision too, so
    # a has_elision:false + non-null 2-group ELISION_RE config tokenized
    # differently here than in production -- and the census would have
    # reported a form the production extractor finds as never occurring.
    #
    # #116 note: load_particle_config() now REFUSES that config, so it no
    # longer arrives through THIS file's loader -- make_lang_dict below
    # bypasses both loaders on purpose. The property still matters:
    # bootstrap_names.py is deliberately not given the symmetric check (its
    # bytes are cache-key material) and still accepts and tokenizes the shape,
    # so the two tokenizers must keep agreeing on it. The loader refusal is a
    # second, independent layer, not a substitute for this assertion.
    #
    # Asserted against bootstrap_names.py's OWN output, never a hand-written
    # expectation.
    bn = _load_bootstrap_names_module()
    pattern = r"^([dl])['\u2019]([A-Z].*)$"
    text = "Il vit d'Effiat ce matin."
    lang = make_lang_dict(
        name_inventory=["Effiat"], elision_pattern=pattern, has_elision=False
    )
    bn_lang = bn.LanguageConfig(
        path=BOOTSTRAP_PATH,
        particles=frozenset(),
        stopwords=frozenset(),
        elision_re=re.compile(pattern),
        has_elision=False,
        raw_bytes=b"{}",
        name_inventory=frozenset({"Effiat"}),
    )
    produced = {n for n, _mid in bn.extract_candidates(text, bn_lang)}
    assert "Effiat" in produced, produced
    assert zero_forms([text], lang) == set()


def test_inventory_census_is_not_a_gate(tmp_path, root):
    # A curated form absent from THIS book is an operator curation fact, not
    # a language-config failure. Gating it would fail every existing project
    # on upgrade with no migration route.
    manifest = build_manifest(["Anna walked in a quiet port."])
    config = particle_config_payload()
    config["name_inventory"] = ["אוסטרהא"]
    proc, report, _ = run_smoke(
        root, tmp_path, manifest, config,
        checked_names=["Anna"], low_name_density_confirmed=True, no_particles_confirmed=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert report is not None
    assert report["pass"] is True
    assert "inventory_zero_match_forms  = 1" in proc.stdout


def test_inventory_census_adds_no_field_to_the_stored_report(tmp_path, root):
    # The whole design rests on the census NOT being stored: W3 reuses a
    # report while its sample-scoped triple matches, so a whole-book fact
    # stored beside it could be replayed stale. Whole-set assertion, so a
    # future field cannot be added here without this test being updated.
    manifest = build_manifest(["Anna walked in a quiet port."])
    config = particle_config_payload()
    config["name_inventory"] = ["אוסטרהא"]
    _proc, report, _ = run_smoke(
        root, tmp_path, manifest, config,
        checked_names=["Anna"], low_name_density_confirmed=True, no_particles_confirmed=True,
    )
    assert report is not None
    assert set(report) == {
        "candidate_names_total",
        "checked_names",
        "elision_test_cases",
        "has_elision",
        "low_name_density_confirmed",
        "no_names_confirmed",
        "no_particles_confirmed",
        "particle_config_sha1",
        "particle_list_size",
        "particle_smoke_cases",
        "pass",
        "smoke_report_contract_hash",
        "source_sample_selection",
        "source_sample_sha1",
    }


# ---------------------------------------------------------------------------
# #116: has_elision / ELISION_RE is an IFF, and only one half was enforced.
#
# assets/languages/README.md calls ELISION_RE "Required (non-null) iff
# has_elision: true". true+missing was already fatal in both loaders;
# false+pattern was accepted -- and since #284 both tokenizers split on the
# compiled pattern ALONE, so that shape is not half-off but HALF-ON: elisions
# still split while every has_elision-keyed guard switches off, giving a green
# pass:true over names extracted with elision splitting and without the guard
# built to check it. load_particle_config() now refuses it; bootstrap_names.py
# deliberately does not, its bytes being cache-key material. Why, and what
# that residual costs, is priced in the comment at the check itself.
# ---------------------------------------------------------------------------


def _write_particle_config(tmp_path, payload, name="lang.json"):
    path = tmp_path / name
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def test_loader_rejects_has_elision_false_beside_non_null_elision_re(tmp_path, capsys):
    # NEW behaviour. Watched RED before the fix: the loader accepted this and
    # returned a compiled elision_re.
    path = _write_particle_config(
        tmp_path, particle_config_payload(has_elision=False, elision_re=FR_ELISION_RE)
    )
    with pytest.raises(SystemExit) as exc:
        _lsr.load_particle_config(path)
    assert exc.value.code == 2
    err = capsys.readouterr().err
    # Names BOTH keys, so the operator is not left guessing which half to fix.
    assert "has_elision" in err
    assert "ELISION_RE" in err
    # And names the remedy in both directions.
    assert "Set ELISION_RE to null" in err


def test_smoke_run_refuses_the_pairing_and_writes_no_report(tmp_path, root):
    # NEW behaviour, at the GATE rather than at the helper: the W3 smoke test
    # is what an operator actually runs, and a report is what W3 consumes.
    # Watched RED before the fix: it exited 0 and wrote pass:true.
    manifest = build_manifest([FEW_NAMES_TEXT])
    config = particle_config_payload(has_elision=False, elision_re=FR_ELISION_RE)
    proc, report, _ = run_smoke(
        root, tmp_path, manifest, config,
        checked_names=FEW_NAMES, low_name_density_confirmed=True,
        no_particles_confirmed=True,
    )
    assert proc.returncode == 2
    assert report is None
    assert "has_elision is false but" in proc.stderr
    assert "ELISION_RE is non-null" in proc.stderr


# Preservation: green before AND after. Each is mutation-checked against an
# over-catching version of the new branch (see the mutation note).


def test_loader_still_accepts_both_legal_pairings(tmp_path):
    # The iff has two legal sides and neither may become collateral damage.
    false_null = _lsr.load_particle_config(
        _write_particle_config(tmp_path, particle_config_payload(), name="a.json")
    )
    assert false_null["has_elision"] is False
    assert false_null["elision_re"] is None

    true_pattern = _lsr.load_particle_config(
        _write_particle_config(
            tmp_path,
            particle_config_payload(has_elision=True, elision_re=FR_ELISION_RE),
            name="b.json",
        )
    )
    assert true_pattern["has_elision"] is True
    assert true_pattern["elision_re"] is not None
    # Mutation check: a branch keyed on `has_elision is False` alone would
    # reject a.json here; one keyed on `is not None` instead of truthiness
    # leaves both of these green but rejects the empty string below.


def test_loader_keeps_the_existing_message_for_a_wrong_TYPE_elision_re(tmp_path, capsys):
    # The rejecting branch is a sibling elif AFTER the type check. The type
    # error must still win for a non-string, non-null value -- otherwise a
    # typo'd config gets told to "set ELISION_RE to null" when the real fault
    # is its type.
    path = _write_particle_config(
        tmp_path, particle_config_payload(has_elision=False, elision_re=42)
    )
    with pytest.raises(SystemExit):
        _lsr.load_particle_config(path)
    err = capsys.readouterr().err
    assert "ELISION_RE must be a string or null" in err
    assert "has_elision is false but" not in err


def test_loader_still_accepts_an_empty_elision_re_beside_has_elision_false(tmp_path):
    # The branch is gated on TRUTHINESS, matching the compile below it, so it
    # names exactly the configs that get a pattern. "" is inert -- nothing
    # compiles, nothing splits, every obligation is consistently off -- and
    # stays accepted, exactly as before this change. Pins that the check was
    # not widened from "would split" to "is non-null" on the way in.
    lang = _lsr.load_particle_config(
        _write_particle_config(
            tmp_path, particle_config_payload(has_elision=False, elision_re="")
        )
    )
    assert lang["has_elision"] is False
    assert lang["elision_re"] is None


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
