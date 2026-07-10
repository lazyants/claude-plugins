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
    (>=10 checked names), the low-name-density branch
    (--low-name-density-confirmed + checked_names count == candidates), and
    the zero-candidate branch (--no-names-confirmed, requiring BOTH flags).
  - particle_smoke_cases's requirement DECOUPLED from name density --
    keyed only to particle_list_size > 0, with its own
    --no-particles-confirmed escape for a genuinely particle-free language.
  - elision_test_cases's conditional requirement, driven by the in-report
    has_elision field copied verbatim from the resolved particle_config file.
"""
import json
import re
import shutil
import subprocess
import sys
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
    # had leaked in, this count would be 5 and the low-density completeness
    # check (len(checked_names) == candidate_names_total) would have failed
    # this run with exit code 2 instead.
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
    assert "EXACTLY" in proc.stderr


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
    manifest = build_manifest([FEW_NAMES_TEXT])  # real candidates: Anna, Bob, Carol, Diana
    checked = ["Anna", "Bob", "Carolz", "Diana"]  # "Carolz" is a deliberate typo
    proc, report, _ = run_smoke(
        root, tmp_path, manifest, NO_PARTICLES_NO_ELISION,
        checked_names=checked, low_name_density_confirmed=True,
        no_particles_confirmed=True,
    )
    assert proc.returncode == 1
    assert report is not None
    by_name = {c["name"]: c["found"] for c in report["checked_names"]}
    assert by_name["Anna"] is True
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


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
