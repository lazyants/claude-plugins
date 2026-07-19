"""tests/skeptic_setup.test.py -- regression-lock suite for
skeptic_setup.py, the skeptic pass's own resume-domain owner + assignment
manifest writer (RFC #215 Phase 2, "skeptic pass", 1.6.0, plan Part B; see
skeptic_setup.py's own module docstring for the full contract).

House style (mirrors tests/resume_integrity.test.py exactly): every fixture
copies the REAL shipped script(s) into an isolated `tmp_path` durable_root
(so each script's own self-anchored `Path(__file__).resolve().parents[1]`
resolves to the fixture root exactly as production does) and invokes it via
a real `subprocess.run`. Nothing here reimplements skeptic_setup.py's own
hashing/validation logic and asserts against that reimplementation.

Owner A1's `suspicion_scan.py` has landed (real functions
`compute_producer_input_digest(canon_bytes, manifest_bytes, resolved_params,
language_config_raw_bytes, script_dir)`, `resolved_scan_params(...)`,
`resolve_citation_block_types(source_format, override)`,
`PRODUCER_CODE_CLOSURE`), so every fixture below copies the REAL shipped
suspicion_scan.py, exactly like every other sibling script.
`skeptic_ready.py`/`skeptic_report.py` (owners A3/A4) and
`skeptic-pass-wf.template.js` (owner A3) are likewise now real, shipped
files -- but this file still copies them in as the SHIPPED bytes (not
placeholders) purely because skeptic_setup.py only ever reads their RAW
BYTES (for its own code-closure hash); it never imports or executes them,
so their content is not otherwise exercised here.
"""
import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
import time
import unicodedata
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
ASSETS_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets"
SCRIPTS_DIR = ASSETS_DIR / "scripts"
SCHEMAS_DIR = ASSETS_DIR / "schemas"

SKEPTIC_SETUP_SRC = SCRIPTS_DIR / "skeptic_setup.py"
SKEPTIC_CONSTANTS_SRC = SCRIPTS_DIR / "skeptic_constants.py"
BOOTSTRAP_NAMES_SRC = SCRIPTS_DIR / "bootstrap_names.py"
CANON_SENSES_SRC = SCRIPTS_DIR / "canon_senses.py"
OCC_INDEX_SRC = SCRIPTS_DIR / "occ_index.py"
EVIDENCE_VERIFY_SRC = SCRIPTS_DIR / "evidence_verify.py"
SUSPICION_SCAN_SRC = SCRIPTS_DIR / "suspicion_scan.py"
SKEPTIC_READY_SRC = SCRIPTS_DIR / "skeptic_ready.py"
SKEPTIC_REPORT_SRC = SCRIPTS_DIR / "skeptic_report.py"
SKEPTIC_TEMPLATE_SRC = ASSETS_DIR / "templates" / "skeptic-pass-wf.template.js"
CACHE_KEY_SRC = SCRIPTS_DIR / "cache_key.py"
SEGPACK_SRC = SCRIPTS_DIR / "segpack.py"
WORKLIST_SCHEMA_SRC = SCHEMAS_DIR / "suspicion-worklist.schema.json"
ASSIGNMENT_SCHEMA_SRC = SCHEMAS_DIR / "skeptic-assignment.schema.json"

for _src in (
    SKEPTIC_SETUP_SRC, SKEPTIC_CONSTANTS_SRC, BOOTSTRAP_NAMES_SRC, CANON_SENSES_SRC,
    OCC_INDEX_SRC, EVIDENCE_VERIFY_SRC, SUSPICION_SCAN_SRC, SKEPTIC_READY_SRC,
    SKEPTIC_REPORT_SRC, SKEPTIC_TEMPLATE_SRC, CACHE_KEY_SRC, SEGPACK_SRC,
    WORKLIST_SCHEMA_SRC, ASSIGNMENT_SCHEMA_SRC,
):
    assert _src.is_file(), f"required fixture source not found: {_src}"

DEFAULT_PARTICLE_CONFIG_BYTES = json.dumps(
    {"PARTICLES": [], "STOPWORDS": [], "has_elision": False, "ELISION_RE": None}
).encode("utf-8")

# The producer's own auto-resolved default for source_format="plain_text"
# (skeptic_constants.CITATION_BLOCK_TYPES_BY_FORMAT["plain_text"], sorted) --
# used as the shared default scan_params below so tests never have to pass
# --citation-block-types explicitly.
DEFAULT_SCAN_PARAMS = {
    "dispersion_threshold": 12,
    "sample_cap": 50,
    "windows_per_entity": 8,
    "near_threshold": 0.15,
    "near_cap": 40,
    "near_pair_budget": 5000,
    "research_mode": "live",
    "source_format": "plain_text",
    "citation_block_types": ["FN", "QUOTE"],
}


# ---------------------------------------------------------------------------
# Generic helpers
# ---------------------------------------------------------------------------


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")


def _load_module(name: str, path: Path, extra_sys_path: Path):
    """Mirrors tests/occ_index.test.py's own loader: `extra_sys_path` is
    temporarily on sys.path so the loaded file's own bare `from X import ...`
    siblings resolve, exactly like a real `python3 <script>.py` invocation.
    Never registers into sys.modules, so repeated calls always re-read the
    file fresh off disk (load-bearing for the mutation tests below, which
    edit a fixture file between two loads)."""
    sys.path.insert(0, str(extra_sys_path))
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None and spec.loader is not None, f"could not load spec for {path}"
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(extra_sys_path))


# Loaded once, module-level: compute_frozen_input_hash is a pure function
# (os/hashlib/Path only, no project-specific state), so the REAL, shipped
# implementation can be reused directly by every fixture below that needs to
# independently compute an EXPECTED canon_sha256/manifest_sha256/
# senses_sha256 -- never a second, hand-rolled reimplementation of the same
# state-tagged hash formula that could silently drift from production.
ss = _load_module("suspicion_scan_for_frozen_hash", SUSPICION_SCAN_SRC, SCRIPTS_DIR)


def make_skeptic_root(tmp_path) -> Path:
    """An isolated durable_root with real copies of every shipped script/
    template skeptic_setup.py's code closure depends on (including owner
    A1's suspicion_scan.py and owners A3/A4's skeptic_ready.py/
    skeptic_report.py/skeptic-pass-wf.template.js -- skeptic_setup.py only
    ever reads the latter three's RAW BYTES for its own closure hash, never
    imports/executes them), the two real schemas, one particle-config
    language file, and trivial canon.json/manifest.json."""
    root = tmp_path / "durable_root"
    scripts_dir = root / "scripts"
    scripts_dir.mkdir(parents=True)

    for src in (SKEPTIC_SETUP_SRC, SKEPTIC_CONSTANTS_SRC, BOOTSTRAP_NAMES_SRC,
                CANON_SENSES_SRC, OCC_INDEX_SRC, EVIDENCE_VERIFY_SRC,
                SUSPICION_SCAN_SRC, SKEPTIC_READY_SRC, SKEPTIC_REPORT_SRC):
        shutil.copy2(src, scripts_dir / src.name)

    schemas_dir = root / "schemas"
    schemas_dir.mkdir(parents=True)
    for src in (WORKLIST_SCHEMA_SRC, ASSIGNMENT_SCHEMA_SRC):
        shutil.copy2(src, schemas_dir / src.name)

    templates_dir = root / "templates"
    templates_dir.mkdir(parents=True)
    shutil.copy2(SKEPTIC_TEMPLATE_SRC, templates_dir / SKEPTIC_TEMPLATE_SRC.name)

    languages_dir = root / "languages"
    languages_dir.mkdir(parents=True)
    (languages_dir / "test.json").write_bytes(DEFAULT_PARTICLE_CONFIG_BYTES)

    write_json(root / "canon.json", {"entries": {}})
    write_json(root / "manifest.json", {"blocks": {}})

    return root


def compute_expected_producer_digest(root: Path, scan_params: dict,
                                      particle_config_filename: str = "test.json",
                                      citation_override=None) -> str:
    """Computes the digest a real re-run of suspicion_scan.py would stamp,
    by loading the fixture's OWN on-disk suspicion_scan.py/bootstrap_names.py
    fresh (never cached) -- so a test that mutates a fixture file between two
    calls gets a genuinely fresh recomputation, never a stale in-process
    value. Calls the REAL suspicion_scan.py's own resolved_scan_params()/
    resolve_citation_block_types()/compute_producer_input_digest() -- never
    a reimplementation -- so this helper and skeptic_setup.py's own
    recomputation are guaranteed to use the identical algorithm. `citation_override`
    defaults to `None` (adapter-default resolution, every existing caller's
    behavior); pass `()` to stamp a worklist assuming an EXPLICIT EMPTY
    override instead (Fix L11's own test)."""
    scripts_dir = root / "scripts"
    languages_dir = root / "languages"
    ss = _load_module("suspicion_scan_for_test", scripts_dir / "suspicion_scan.py", scripts_dir)
    bn = _load_module("bootstrap_names_for_test", scripts_dir / "bootstrap_names.py", scripts_dir)
    lang = bn.load_language_config(particle_config_filename, languages_dir)
    canon_path = root / "canon.json"
    canon_bytes = canon_path.read_bytes() if canon_path.is_file() else b""
    manifest_bytes = (root / "manifest.json").read_bytes()
    # #243: canon_senses.json's own raw bytes, tolerant-read exactly like
    # canon_bytes -- make_skeptic_root() never writes a sidecar by default,
    # so this is b"" (absent) unless a test writes one itself before calling
    # write_worklist()/this helper.
    senses_path = root / "canon_senses.json"
    senses_bytes = senses_path.read_bytes() if senses_path.is_file() else b""

    resolved_citation_types = ss.resolve_citation_block_types(scan_params["source_format"], citation_override)
    resolved_params = ss.resolved_scan_params(
        dispersion_threshold=scan_params["dispersion_threshold"],
        sample_cap=scan_params["sample_cap"],
        windows_per_entity=scan_params["windows_per_entity"],
        near_threshold=scan_params["near_threshold"],
        near_cap=scan_params["near_cap"],
        near_pair_budget=scan_params["near_pair_budget"],
        research_mode=scan_params["research_mode"],
        source_format=scan_params["source_format"],
        resolved_citation_types=resolved_citation_types,
    )
    return ss.compute_producer_input_digest(
        canon_bytes, manifest_bytes, senses_bytes, resolved_params, lang.raw_bytes, scripts_dir,
    )


def make_occurrence_ref(block, seg, char_start, char_end, origin="block", vid=None):
    ref = {"block": block, "seg": seg, "char_start": char_start, "char_end": char_end, "origin": origin}
    if vid is not None:
        ref["vid"] = vid
    return ref


def make_worklist_entry(source_form, canonical_target_form=None,
                         risk_classes=("singleton",), occurrence_refs=()):
    return {
        "source_form": source_form,
        "canonical_target_form": canonical_target_form or source_form,
        "risk_classes": list(risk_classes),
        "occurrence_refs": list(occurrence_refs),
    }


def write_worklist(root: Path, entries, scan_params=None, particle_config_filename="test.json"):
    sp = scan_params if scan_params is not None else DEFAULT_SCAN_PARAMS
    digest = compute_expected_producer_digest(root, sp, particle_config_filename)
    doc = {"schema_version": 1, "producer_input_digest": digest, "entries": entries}
    write_json(root / "suspicion_worklist.json", doc)
    return doc


def run_skeptic_setup(root: Path, scan_params=None, *, particle_config="test.json",
                       entities_per_batch=5, batch_agent_cap=100,
                       resume_from_run_id=None, canon=None, manifest=None,
                       worklist=None, languages_dir=None, timeout=30,
                       citation_block_types=None, source_lang="fr"):
    sp = scan_params if scan_params is not None else DEFAULT_SCAN_PARAMS
    cmd = [
        sys.executable, str(root / "scripts" / "skeptic_setup.py"),
        "--particle-config", particle_config,
        "--research-mode", sp["research_mode"],
        "--source-format", sp["source_format"],
        "--dispersion-threshold", str(sp["dispersion_threshold"]),
        "--sample-cap", str(sp["sample_cap"]),
        "--windows-per-entity", str(sp["windows_per_entity"]),
        "--near-threshold", str(sp["near_threshold"]),
        "--near-cap", str(sp["near_cap"]),
        "--near-pair-budget", str(sp["near_pair_budget"]),
        "--entities-per-batch", str(entities_per_batch),
        "--batch-agent-cap", str(batch_agent_cap),
        # Fix M8 -- the skeptic prompt's own interpolated {{SOURCE_LANG}}
        # token, folded into config_values/the skeptic input_digest (see
        # skeptic_setup.py's own module docstring / build_arg_parser()). No
        # --target-lang: the shipped template has no {{TARGET_LANG}} token.
        "--source-lang", source_lang,
    ]
    if resume_from_run_id is not None:
        cmd += ["--resume-from-run-id", resume_from_run_id]
    if canon is not None:
        cmd += ["--canon", str(canon)]
    if manifest is not None:
        cmd += ["--manifest", str(manifest)]
    if worklist is not None:
        cmd += ["--worklist", str(worklist)]
    if languages_dir is not None:
        cmd += ["--languages-dir", str(languages_dir)]
    # `None` -> flag omitted entirely (adapter default resolution); a list
    # (INCLUDING the empty list) -> flag passed, `[]` being the explicit
    # empty override this file's own L11 test targets.
    if citation_block_types is not None:
        cmd += ["--citation-block-types", *citation_block_types]

    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=str(root), timeout=timeout)
    parsed = None
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    if len(lines) == 1:
        try:
            parsed = json.loads(lines[0])
        except json.JSONDecodeError:
            parsed = None
    return proc, parsed


def existing_skeptic_run_dirs(root: Path):
    runs_dir = root / "skeptic" / "runs"
    if not runs_dir.is_dir():
        return []
    return sorted(p.name for p in runs_dir.iterdir())


# ===========================================================================
# Validate-before-any-write discipline
# ===========================================================================


def test_worklist_invalid_json_aborts_before_any_write(tmp_path):
    root = make_skeptic_root(tmp_path)
    (root / "suspicion_worklist.json").write_text("{not valid json", encoding="utf-8")

    proc, parsed = run_skeptic_setup(root)

    assert proc.returncode == 1
    assert parsed is not None and parsed["success"] is False
    assert existing_skeptic_run_dirs(root) == [], (
        "MUTATION CAUGHT: if JSON parsing happened AFTER creating skeptic/runs/, "
        "a run directory would exist even though the worklist never parsed"
    )


def test_worklist_schema_invalid_aborts_before_any_write(tmp_path):
    root = make_skeptic_root(tmp_path)
    # Missing the required 'producer_input_digest' field -- schema-invalid.
    write_json(root / "suspicion_worklist.json", {"schema_version": 1, "entries": []})

    proc, parsed = run_skeptic_setup(root)

    assert proc.returncode == 1
    assert parsed is not None and parsed["success"] is False
    assert "schema" in parsed["error"].lower()
    assert existing_skeptic_run_dirs(root) == [], (
        "MUTATION CAUGHT: if schema validation ran AFTER resolving a RUN_ID/"
        "creating its directory, a run directory would exist despite the "
        "schema-invalid worklist"
    )


def test_stale_producer_input_digest_rejected_fail_closed(tmp_path):
    root = make_skeptic_root(tmp_path)
    entry = make_worklist_entry("Jean", occurrence_refs=[make_occurrence_ref("b1", "seg01", 0, 4)])
    write_worklist(root, [entry])

    # Perturb canon.json AFTER the worklist was stamped -- the worklist's own
    # claim about which canon.json state it scanned is now stale.
    write_json(root / "canon.json", {"entries": {"changed": True}})

    proc, parsed = run_skeptic_setup(root)

    assert proc.returncode == 1
    assert parsed is not None and parsed["success"] is False
    assert "stale" in parsed["error"].lower()
    assert existing_skeptic_run_dirs(root) == [], (
        "MUTATION CAUGHT: if freshness were checked AFTER a run directory was "
        "created, a run directory would exist despite the stale reject -- "
        "fail-closed means NOTHING is written for a rejected worklist"
    )


# ===========================================================================
# Success path: schema-valid manifests written BEFORE any dispatch
# ===========================================================================


def test_success_writes_schema_valid_aggregate_and_batch_manifests(tmp_path):
    root = make_skeptic_root(tmp_path)
    entries = [
        make_worklist_entry("Jean", risk_classes=["singleton"],
                             occurrence_refs=[make_occurrence_ref("b1", "seg01", 0, 4)]),
        make_worklist_entry("Paul", risk_classes=["high_dispersion"],
                             occurrence_refs=[make_occurrence_ref("b2", "seg02", 0, 4)]),
    ]
    write_worklist(root, entries)

    proc, parsed = run_skeptic_setup(root, entities_per_batch=1)

    assert proc.returncode == 0, f"stdout={proc.stdout}\nstderr={proc.stderr}"
    assert parsed["success"] is True
    assert parsed["batch_count"] == 2
    assert parsed["assignment_count"] == 2

    run_dir = Path(parsed["run_dir"])
    assert (run_dir / "input.digest").is_file()

    aggregate_path = run_dir / "assignments.json"
    aggregate = json.loads(aggregate_path.read_text(encoding="utf-8"))
    assert len(aggregate["assignments"]) == 2
    assert aggregate["batch_count"] == 2
    assert aggregate["input_digest"] == parsed["input_digest"]
    assert aggregate["producer_input_digest"] == parsed["producer_input_digest"]

    # Fix H1 (writer half): the frozen canon/manifest inputs' own
    # state-tagged hash (compute_frozen_input_hash, codex round 2) are
    # stamped so --verify-merged (A3) can re-hash the on-disk files and
    # detect a post-setup tamper.
    assert aggregate["canon_sha256"] == ss.compute_frozen_input_hash(root / "canon.json"), (
        "MUTATION CAUGHT: the aggregate manifest must stamp canon_sha256 == "
        "compute_frozen_input_hash() of the ACTUAL canon.json this run was set up against"
    )
    assert aggregate["manifest_sha256"] == ss.compute_frozen_input_hash(root / "manifest.json"), (
        "MUTATION CAUGHT: the aggregate manifest must stamp manifest_sha256 == "
        "compute_frozen_input_hash() of the ACTUAL manifest.json this run was set up against"
    )

    # Independent re-validation against the REAL schema file -- never trust
    # skeptic_setup.py's own internal self-check alone.
    import jsonschema
    schema = json.loads(ASSIGNMENT_SCHEMA_SRC.read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator(schema).validate(aggregate)

    # Per-batch fragments: bare JSON arrays of that batch's own
    # assignment_id strings, filename "assignments_{index}.json" -- mirrors
    # resume_setup.py's manifest_{index}.json convention exactly, and is
    # what skeptic-pass-wf.template.js's assignmentsBatchPath()/
    # skeptic_ready.py --expect-assignments-file actually consume (NOT the
    # full schema-envelope shape).
    batch_files = sorted(run_dir.glob("assignments_*.json"))
    assert [p.name for p in batch_files] == ["assignments_0.json", "assignments_1.json"], (
        "MUTATION CAUGHT: with entities_per_batch=1 and 2 assigned entities, "
        "chunking must produce exactly two per-batch fragment files -- a "
        "batching bug (e.g. always writing batch 0) would leave this list short"
    )
    batch0_ids = json.loads(batch_files[0].read_text(encoding="utf-8"))
    batch1_ids = json.loads(batch_files[1].read_text(encoding="utf-8"))
    assert isinstance(batch0_ids, list) and len(batch0_ids) == 1
    assert isinstance(batch1_ids, list) and len(batch1_ids) == 1
    aggregate_ids_by_form = {a["source_form"]: a["assignment_id"] for a in aggregate["assignments"]}
    assert {batch0_ids[0], batch1_ids[0]} == set(aggregate_ids_by_form.values())


def test_aggregate_stamps_senses_sha256_of_actual_sidecar_bytes(tmp_path):
    """#243 H1 (writer half): canon_senses.json joined canon.json/
    manifest.json as a THIRD frozen input this script must stamp, so
    --verify-merged can re-hash it and detect a post-setup tamper the same
    way it already does for canon_sha256/manifest_sha256."""
    root = make_skeptic_root(tmp_path)
    (root / "canon_senses.json").write_text(
        json.dumps({"schema_version": 1, "entries_by_source_form": {}}), encoding="utf-8"
    )
    entry = make_worklist_entry("Jean", occurrence_refs=[make_occurrence_ref("b1", "seg01", 0, 4)])
    write_worklist(root, [entry])

    proc, parsed = run_skeptic_setup(root)

    assert proc.returncode == 0, f"stdout={proc.stdout}\nstderr={proc.stderr}"
    run_dir = Path(parsed["run_dir"])
    aggregate = json.loads((run_dir / "assignments.json").read_text(encoding="utf-8"))
    assert aggregate["senses_sha256"] == ss.compute_frozen_input_hash(root / "canon_senses.json"), (
        "MUTATION CAUGHT: the aggregate manifest must stamp senses_sha256 == "
        "compute_frozen_input_hash() of the ACTUAL canon_senses.json this run was set up against"
    )


def test_aggregate_stamps_senses_sha256_of_absent_state_when_sidecar_absent(tmp_path):
    """The default fixture (make_skeptic_root) never writes canon_senses.json
    -- senses_sha256 must still be present, stamping the "absent"-state hash
    (compute_frozen_input_hash's own state tag, codex round 2 -- NOT bare
    sha256(b"") any more, which would collide with a regular-but-empty file
    or a directory), never omitted or null, so an aggregate manifest is
    always comparable byte-for-byte against a fresh re-hash regardless of
    whether the sidecar exists."""
    root = make_skeptic_root(tmp_path)
    assert not (root / "canon_senses.json").is_file()
    entry = make_worklist_entry("Jean", occurrence_refs=[make_occurrence_ref("b1", "seg01", 0, 4)])
    write_worklist(root, [entry])

    proc, parsed = run_skeptic_setup(root)

    assert proc.returncode == 0, f"stdout={proc.stdout}\nstderr={proc.stderr}"
    run_dir = Path(parsed["run_dir"])
    aggregate = json.loads((run_dir / "assignments.json").read_text(encoding="utf-8"))
    assert aggregate["senses_sha256"] == ss.compute_frozen_input_hash(root / "canon_senses.json")
    # Mutation: reverting to bare sha256(b"") would make this equal a
    # regular, genuinely-empty file's hash too -- pin the DISTINCTION
    # directly, not just "some hash was stamped".
    (root / "canon_senses.json").write_bytes(b"")
    assert aggregate["senses_sha256"] != hashlib.sha256(b"").hexdigest()


def test_h1_stamp_snapshots_derivation_time_state_not_a_later_reread(tmp_path):
    """codex round 3 BLOCKER: skeptic_setup.py reads canon/manifest/senses
    ONCE early (driving the freshness check + assignments this run
    builds), but the H1 stamp was previously computed by RE-READING the
    paths later, right before writing assignments.json. A mutation landing
    in that window was silently ADOPTED as "the true state" instead of
    being caught -- the published stamp would describe the MUTATED file
    while the worklist-freshness check and the assignments this run just
    built both still describe the ORIGINAL one.

    Injects a mutation at the exact boundary via a monkeypatch on
    `_schemas_dir_hash()` -- called well after the derivation reads (top of
    `run()`) and well before the aggregate dict (with its H1 stamps) is
    built, the same real ordering this bug lived in -- and asserts the
    STAMP reflects the ORIGINAL snapshot, never the mutated one. This is
    NOT "no mutation is ever missed": the whole point of H1 is that the
    NEXT --verify-merged/--check-frozen-inputs re-read catches this by
    comparing the (correctly-original) stamp against what's ACTUALLY on
    disk now (mutated) -- a re-read stamp would instead make the stamp
    agree with the mutation, and nothing downstream would ever see a
    mismatch at all."""
    root = make_skeptic_root(tmp_path)
    mod = _load_module(
        "skeptic_setup_for_snapshot_race_test", root / "scripts" / "skeptic_setup.py", root / "scripts"
    )
    senses_path = root / "canon_senses.json"
    original_senses_bytes = json.dumps(
        {"schema_version": 1, "entries_by_source_form": {}}
    ).encode("utf-8")
    senses_path.write_bytes(original_senses_bytes)
    original_senses_hash = mod.compute_frozen_input_hash_from_state("regular", original_senses_bytes)

    entry = make_worklist_entry("Jean", occurrence_refs=[make_occurrence_ref("b1", "seg01", 0, 4)])
    write_worklist(root, [entry])

    mutated_senses_bytes = json.dumps(
        {"schema_version": 1, "entries_by_source_form": {"Injected": {"senses": []}}}
    ).encode("utf-8")

    real_schemas_dir_hash = mod._schemas_dir_hash

    def _mutate_senses_then_hash_schemas():
        # Simulates a skeptic-agent tamper (or any other mid-run mutation)
        # landing in the window between this run's derivation reads and its
        # H1 stamp -- _schemas_dir_hash() is a real, unrelated call this
        # function makes at exactly that point, never touching canon/
        # manifest/senses itself, which is what makes it a clean injection
        # site rather than interfering with anything else in the flow.
        senses_path.write_bytes(mutated_senses_bytes)
        return real_schemas_dir_hash()

    mod._schemas_dir_hash = _mutate_senses_then_hash_schemas

    sp = DEFAULT_SCAN_PARAMS
    argv = [
        "--particle-config", "test.json",
        "--research-mode", sp["research_mode"],
        "--source-format", sp["source_format"],
        "--dispersion-threshold", str(sp["dispersion_threshold"]),
        "--sample-cap", str(sp["sample_cap"]),
        "--windows-per-entity", str(sp["windows_per_entity"]),
        "--near-threshold", str(sp["near_threshold"]),
        "--near-cap", str(sp["near_cap"]),
        "--near-pair-budget", str(sp["near_pair_budget"]),
        "--entities-per-batch", "5", "--batch-agent-cap", "100",
        "--source-lang", "fr",
    ]
    args = mod.build_arg_parser().parse_args(argv)
    result = mod.run(args)

    run_dir = Path(result["run_dir"])
    aggregate = json.loads((run_dir / "assignments.json").read_text(encoding="utf-8"))
    # Mutation this test guards: a stamp computed by RE-READING
    # canon_senses.json at stamp time (the pre-fix shape) would hash the
    # MUTATED bytes instead, making this assertion fail.
    assert aggregate["senses_sha256"] == original_senses_hash, (
        "MUTATION CAUGHT: the H1 stamp must describe the snapshot this run "
        "actually derived its worklist-freshness check and assignments "
        "from -- re-reading canon_senses.json fresh at stamp time instead "
        "laundered the mid-run mutation into the published stamp"
    )
    # The on-disk file genuinely IS mutated now -- confirms the NEXT
    # --verify-merged/--check-frozen-inputs re-read will correctly catch
    # this as a tamper (stamped-original != actually-on-disk-mutated),
    # which is the property this whole test protects.
    assert senses_path.read_bytes() == mutated_senses_bytes


def test_windows_capped_truncated_flag_and_verse_embedded_excluded(tmp_path):
    root = make_skeptic_root(tmp_path)
    block_refs = [make_occurrence_ref(f"b{i}", f"seg{i:02d}", 0, 4) for i in range(10)]
    verse_ref = make_occurrence_ref("b_carrier", "seg99", 0, 4, origin="verse_embedded", vid="v1")
    entry = make_worklist_entry("Jean", occurrence_refs=block_refs + [verse_ref])
    write_worklist(root, [entry])  # DEFAULT_SCAN_PARAMS windows_per_entity == 8

    proc, parsed = run_skeptic_setup(root)

    assert proc.returncode == 0, f"stdout={proc.stdout}\nstderr={proc.stderr}"
    run_dir = Path(parsed["run_dir"])
    aggregate = json.loads((run_dir / "assignments.json").read_text(encoding="utf-8"))
    (assignment,) = aggregate["assignments"]

    assert len(assignment["windows"]) == 8, (
        "MUTATION CAUGHT: capping over ALL occurrence_refs (including the "
        "origin='verse_embedded' one) instead of only origin='block' refs "
        "would leak a non-citable window into the capped set / change the count"
    )
    assert assignment["windows_truncated"] is True
    assert all(w["block"] != "b_carrier" for w in assignment["windows"]), (
        "MUTATION CAUGHT: an origin='verse_embedded' occurrence_ref must NEVER "
        "become a citable window -- evidence_verify can only authenticate "
        "against manifest.blocks{}, never verse.store"
    )


def test_entity_with_few_refs_not_marked_truncated(tmp_path):
    root = make_skeptic_root(tmp_path)
    entry = make_worklist_entry("Paul", occurrence_refs=[make_occurrence_ref("b1", "seg01", 0, 4)])
    write_worklist(root, [entry])

    proc, parsed = run_skeptic_setup(root)

    assert proc.returncode == 0, f"stdout={proc.stdout}\nstderr={proc.stderr}"
    run_dir = Path(parsed["run_dir"])
    aggregate = json.loads((run_dir / "assignments.json").read_text(encoding="utf-8"))
    (assignment,) = aggregate["assignments"]
    assert len(assignment["windows"]) == 1
    assert assignment["windows_truncated"] is False


def test_verse_embedded_ref_present_marks_windows_truncated(tmp_path):
    """Fix M5: windows_truncated must be True whenever the entity has ANY
    origin='verse_embedded' occurrence_ref, even when every origin='block'
    ref fits inside windows_per_entity uncapped -- those verse refs are
    label-only (never citable; evidence_verify can only authenticate
    against manifest.blocks{}, never verse.store) and are silently DROPPED
    from windows, so their presence alone means the skeptic's view is
    incomplete. Pre-fix, windows_truncated only checked
    len(block_refs) > windows_per_entity, so this case wrongly read False
    -- letting the skeptic conclude "full coverage" on the false premise
    that every occurrence of this entity is a citable block, and possibly
    return propose_rescope on that false premise."""
    root = make_skeptic_root(tmp_path)
    block_ref = make_occurrence_ref("b1", "seg01", 0, 4)
    verse_ref = make_occurrence_ref("b_carrier", "seg99", 0, 4, origin="verse_embedded", vid="v1")
    entry = make_worklist_entry("Jean", occurrence_refs=[block_ref, verse_ref])
    write_worklist(root, [entry])  # DEFAULT_SCAN_PARAMS windows_per_entity == 8, well above 1

    proc, parsed = run_skeptic_setup(root)

    assert proc.returncode == 0, f"stdout={proc.stdout}\nstderr={proc.stderr}"
    run_dir = Path(parsed["run_dir"])
    aggregate = json.loads((run_dir / "assignments.json").read_text(encoding="utf-8"))
    (assignment,) = aggregate["assignments"]
    assert len(assignment["windows"]) == 1
    assert assignment["windows_truncated"] is True, (
        "MUTATION CAUGHT: an omitted origin='verse_embedded' occurrence_ref "
        "must force windows_truncated=True even when the block refs alone "
        "did not exceed windows_per_entity"
    )


def test_assignment_id_is_sha256_hex_of_nfc_source_form(tmp_path):
    root = make_skeptic_root(tmp_path)
    entry = make_worklist_entry("Jean", occurrence_refs=[make_occurrence_ref("b1", "seg01", 0, 4)])
    write_worklist(root, [entry])

    proc, parsed = run_skeptic_setup(root)

    assert proc.returncode == 0, f"stdout={proc.stdout}\nstderr={proc.stderr}"
    run_dir = Path(parsed["run_dir"])
    aggregate = json.loads((run_dir / "assignments.json").read_text(encoding="utf-8"))
    (assignment,) = aggregate["assignments"]
    expected_id = hashlib.sha256(unicodedata.normalize("NFC", "Jean").encode("utf-8")).hexdigest()
    assert assignment["assignment_id"] == expected_id, (
        "MUTATION CAUGHT: hashing the raw (non-NFC-normalized) source_form "
        "would diverge from this expectation for any input not already in NFC"
    )


# ===========================================================================
# batch_agent_cap: honest chunking + over-cap refusal (glossary-pass-wf
# .template.js:144-166's own formula)
# ===========================================================================


def test_batch_agent_cap_chunking_shape(tmp_path):
    root = make_skeptic_root(tmp_path)
    entries = [
        make_worklist_entry(f"Name{i}", occurrence_refs=[make_occurrence_ref(f"b{i}", f"seg{i:02d}", 0, 4)])
        for i in range(12)
    ]
    write_worklist(root, entries)

    # 12 entities / 5 per batch -> ceil(12/5) == 3 batches -> estimatedCalls
    # == 3*3+2 == 11.
    proc, parsed = run_skeptic_setup(root, entities_per_batch=5, batch_agent_cap=11)

    assert proc.returncode == 0, f"stdout={proc.stdout}\nstderr={proc.stderr}"
    assert parsed["batch_count"] == 3
    run_dir = Path(parsed["run_dir"])
    sizes = [
        len(json.loads((run_dir / f"assignments_{i}.json").read_text(encoding="utf-8")))
        for i in range(3)
    ]
    assert sizes == [5, 5, 2], (
        "MUTATION CAUGHT: an off-by-one in the `i // entities_per_batch` "
        "chunking formula would split 12 entities into a different shape "
        "than [5, 5, 2]"
    )


def test_batch_agent_cap_over_cap_refuses_whole_run_writes_nothing(tmp_path):
    root = make_skeptic_root(tmp_path)
    entries = [
        make_worklist_entry(f"Name{i}", occurrence_refs=[make_occurrence_ref(f"b{i}", f"seg{i:02d}", 0, 4)])
        for i in range(12)
    ]
    write_worklist(root, entries)

    # Same 12/5 -> 3 batches -> estimatedCalls == 11; demand cap=10 (< 11).
    proc, parsed = run_skeptic_setup(root, entities_per_batch=5, batch_agent_cap=10)

    assert proc.returncode == 1
    assert parsed is not None and parsed["success"] is False
    assert "batch" in parsed["error"].lower()
    assert existing_skeptic_run_dirs(root) == [], (
        "MUTATION CAUGHT: refusing to dispatch must refuse the WHOLE run -- "
        "if the cap check ran AFTER writing assignments.json, a run "
        "directory (with manifests a downstream template could wrongly "
        "trust) would exist despite the refusal"
    )


# ===========================================================================
# Resume-domain digest gate: identical rerun resumes; ANY closure-member
# byte change forces a fresh RUN_ID (round-3/round-4 blockers)
# ===========================================================================


def test_identical_rerun_resumes_same_run_id(tmp_path):
    root = make_skeptic_root(tmp_path)
    entry = make_worklist_entry("Jean", occurrence_refs=[make_occurrence_ref("b1", "seg01", 0, 4)])
    write_worklist(root, [entry])

    proc1, parsed1 = run_skeptic_setup(root)
    assert proc1.returncode == 0, f"stdout={proc1.stdout}\nstderr={proc1.stderr}"
    run_id_1 = parsed1["effectiveRunId"]
    assert parsed1["resume"] is False

    proc2, parsed2 = run_skeptic_setup(root, resume_from_run_id=run_id_1)
    assert proc2.returncode == 0, f"stdout={proc2.stdout}\nstderr={proc2.stderr}"
    assert parsed2["resume"] is True
    assert parsed2["effectiveRunId"] == run_id_1
    assert parsed2["input_digest"] == parsed1["input_digest"]


def test_skeptic_input_digest_frames_members_no_boundary_collision(tmp_path):
    """Fix L10: compute_skeptic_input_digest() must separate every hashed
    member with a framing byte (mirrors suspicion_scan.compute_producer_input_digest()'s
    own NUL-separator framing exactly) -- otherwise two adjacent
    variable-length members hash identically across a boundary shift, e.g.
    canon_bytes=b"A"+manifest_bytes=b"BC" vs canon_bytes=b"AB"+manifest_bytes=b"C"
    both concatenate to b"ABC" with no separator. Calls
    compute_skeptic_input_digest() directly (a pure function, no
    subprocess) against a real script_dir fixture (so the closure-file read
    loop inside it succeeds) with every OTHER argument held byte-identical
    across the two calls -- only the canon/manifest boundary shifts."""
    root = make_skeptic_root(tmp_path)
    mod = _load_module(
        "skeptic_setup_for_digest_framing_test", root / "scripts" / "skeptic_setup.py", root / "scripts"
    )
    scripts_dir = root / "scripts"
    common = dict(
        worklist_bytes=b"{}",
        assignments=[],
        config_values={},
        language_config_raw_bytes=b"",
        schemas_dir_hash_hex="deadbeef",
        script_dir=scripts_dir,
        template_bytes=b"",
    )

    digest_split_1 = mod.compute_skeptic_input_digest(canon_bytes=b"A", manifest_bytes=b"BC", **common)
    digest_split_2 = mod.compute_skeptic_input_digest(canon_bytes=b"AB", manifest_bytes=b"C", **common)

    assert digest_split_1 != digest_split_2, (
        "MUTATION CAUGHT: with no separator between hashed members, "
        "canon_bytes=b'A'+manifest_bytes=b'BC' and canon_bytes=b'AB'+"
        "manifest_bytes=b'C' both concatenate to b'ABC' and hash "
        "identically -- a real collision the plan's closure guarantee "
        "forbids"
    )


def test_fresh_run_id_is_collision_free_without_sleeping(tmp_path):
    """Hardening: fresh_run_id() must be collision-free ON ITS OWN
    (microsecond timestamp + random hex suffix), not merely "usually fine,
    retry with a time.sleep(1) if not" -- resolve_skeptic_run()'s prior
    sleep-based collision recovery was nondeterministic-timing code smell,
    and two fresh runs launched back-to-back easily land in the same
    wall-clock SECOND. Calls fresh_run_id() directly (a pure-function
    property, no subprocess) in a tight loop that comfortably completes
    within a single wall-clock second -- fast enough that a 1-second-
    resolution timestamp would collide repeatedly."""
    root = make_skeptic_root(tmp_path)
    mod = _load_module(
        "skeptic_setup_for_fresh_id_test", root / "scripts" / "skeptic_setup.py", root / "scripts"
    )

    ids = [mod.fresh_run_id() for _ in range(500)]

    assert len(set(ids)) == len(ids), (
        "MUTATION CAUGHT: reverting fresh_run_id() to the bare "
        "'%Y%m%dT%H%M%SZ' 1-second-resolution timestamp (no microseconds, "
        "no random suffix) would produce many duplicate ids across this "
        "500-call tight loop"
    )
    for rid in ids:
        assert mod.RUN_ID_RE.fullmatch(rid), f"{rid!r} does not match RUN_ID_RE"
        assert mod.validate_run_id(rid) is None, f"{rid!r} failed validate_run_id"


def test_two_immediate_fresh_runs_get_distinct_run_ids_fast(tmp_path):
    """End-to-end companion to the property test above: two consecutive
    fresh (no --resume-from-run-id) skeptic_setup.py invocations against the
    SAME durable root, launched back-to-back, must both succeed with
    DISTINCT effectiveRunId values and complete quickly -- no
    time.sleep(1)-driven collision recovery anywhere in the round trip."""
    root = make_skeptic_root(tmp_path)
    entry = make_worklist_entry("Jean", occurrence_refs=[make_occurrence_ref("b1", "seg01", 0, 4)])
    write_worklist(root, [entry])

    start = time.perf_counter()
    proc1, parsed1 = run_skeptic_setup(root)
    proc2, parsed2 = run_skeptic_setup(root)
    elapsed = time.perf_counter() - start

    assert proc1.returncode == 0, f"stdout={proc1.stdout}\nstderr={proc1.stderr}"
    assert proc2.returncode == 0, f"stdout={proc2.stdout}\nstderr={proc2.stderr}"
    assert parsed1["resume"] is False
    assert parsed2["resume"] is False
    assert parsed1["effectiveRunId"] != parsed2["effectiveRunId"], (
        "two immediate fresh runs against the same durable root must get "
        "distinct RUN_IDs"
    )
    assert elapsed < 1.0, (
        f"two back-to-back skeptic_setup.py invocations took {elapsed:.2f}s -- "
        "a sleep(1)-based collision-recovery path would push this over 1s "
        "whenever both land in the same wall-clock second, which two "
        "back-to-back subprocess launches routinely do"
    )


def test_particle_config_one_byte_edit_forces_new_run_id(tmp_path):
    """Round-4 blocker 1: a same-named particle-config file's own bytes must
    be in the digest closure (via LanguageConfig.raw_bytes) -- a one-byte
    edit must be indistinguishable from any other invalidating change."""
    root = make_skeptic_root(tmp_path)
    entry = make_worklist_entry("Jean", occurrence_refs=[make_occurrence_ref("b1", "seg01", 0, 4)])
    write_worklist(root, [entry])

    proc1, parsed1 = run_skeptic_setup(root)
    assert proc1.returncode == 0, f"stdout={proc1.stdout}\nstderr={proc1.stderr}"
    run_id_1 = parsed1["effectiveRunId"]

    pc_path = root / "languages" / "test.json"
    pc_path.write_bytes(pc_path.read_bytes() + b" ")

    # A real re-run of suspicion_scan.py (the W-step always re-produces the
    # worklist before skeptic_setup.py runs) would re-stamp the worklist with
    # a digest over the NEW particle-config bytes -- do that here too, so the
    # mismatch this test targets is the RUN_ID property, not a masking
    # "stale worklist" rejection.
    write_worklist(root, [entry])

    proc2, parsed2 = run_skeptic_setup(root, resume_from_run_id=run_id_1)
    assert proc2.returncode == 0, f"stdout={proc2.stdout}\nstderr={proc2.stderr}"
    assert parsed2["resume"] is False, (
        "MUTATION CAUGHT: if LanguageConfig.raw_bytes were left out of the "
        "skeptic input_digest, this one-byte particle-config edit would be "
        "invisible and the run would wrongly RESUME run_id_1"
    )
    assert parsed2["effectiveRunId"] != run_id_1


def test_senses_sidecar_edit_forces_new_run_id(tmp_path):
    """#243: canon_senses.json's own raw bytes joined the producer/skeptic
    digest closures -- a curator editing the sidecar (adding a split-only
    form, say) with canon/manifest/particle-config/params all held constant
    must be indistinguishable from any other invalidating change, exactly
    like the particle-config one-byte-edit case above."""
    root = make_skeptic_root(tmp_path)
    (root / "canon_senses.json").write_text(
        json.dumps({"schema_version": 1, "entries_by_source_form": {}}), encoding="utf-8"
    )
    entry = make_worklist_entry("Jean", occurrence_refs=[make_occurrence_ref("b1", "seg01", 0, 4)])
    write_worklist(root, [entry])

    proc1, parsed1 = run_skeptic_setup(root)
    assert proc1.returncode == 0, f"stdout={proc1.stdout}\nstderr={proc1.stderr}"
    run_id_1 = parsed1["effectiveRunId"]

    senses_path = root / "canon_senses.json"
    senses_path.write_bytes(senses_path.read_bytes() + b" ")

    # A real re-run of suspicion_scan.py would re-stamp the worklist with a
    # digest over the NEW sidecar bytes -- do that here too, so the mismatch
    # this test targets is the RUN_ID property, not a masking "stale
    # worklist" rejection (see test_stale_producer_input_digest_rejected...
    # for that half).
    write_worklist(root, [entry])

    proc2, parsed2 = run_skeptic_setup(root, resume_from_run_id=run_id_1)
    assert proc2.returncode == 0, f"stdout={proc2.stdout}\nstderr={proc2.stderr}"
    assert parsed2["resume"] is False, (
        "MUTATION CAUGHT: if senses_bytes were left out of the producer/"
        "skeptic input digests, this one-byte canon_senses.json edit would "
        "be invisible and the run would wrongly RESUME run_id_1"
    )
    assert parsed2["effectiveRunId"] != run_id_1


def test_stale_worklist_rejected_when_senses_sidecar_changes_after_stamping(tmp_path):
    """The freshness half of the same guarantee: editing canon_senses.json
    AFTER a worklist was stamped (never re-running suspicion_scan.py) must
    be rejected fail-closed, exactly like editing canon.json after stamping
    already is (test_stale_producer_input_digest_rejected_fail_closed)."""
    root = make_skeptic_root(tmp_path)
    entry = make_worklist_entry("Jean", occurrence_refs=[make_occurrence_ref("b1", "seg01", 0, 4)])
    write_worklist(root, [entry])  # stamped against an ABSENT canon_senses.json (senses_bytes == b"")

    (root / "canon_senses.json").write_text(
        json.dumps({"schema_version": 1, "entries_by_source_form": {}}), encoding="utf-8"
    )

    proc, parsed = run_skeptic_setup(root)

    assert proc.returncode == 1
    assert parsed is not None and parsed["success"] is False
    assert "stale" in parsed["error"].lower(), (
        "MUTATION CAUGHT: if senses_bytes were left out of the recomputed "
        "producer_input_digest, a canon_senses.json edit made AFTER the "
        "worklist was stamped would go undetected and this run would "
        "wrongly succeed against a stale competitors universe"
    )
    assert existing_skeptic_run_dirs(root) == []


def test_source_lang_change_forces_new_run_id(tmp_path):
    """Fix M8: the skeptic prompt (skeptic-pass-wf.template.js, owner A3)
    interpolates a {{SOURCE_LANG}} token, but config_values (folded into
    compute_skeptic_input_digest) previously hashed only scan params/batch
    values/particle-config FILENAME -- never this token. A project that
    changes source language while canon/manifest/particle-config-bytes stay
    constant would wrongly RESUME fragments generated under the OLD
    language context. --source-lang must now be part of that closure --
    two setups identical except --source-lang must get DISTINCT
    input_digests (and RUN_IDs). Confirmed with A3 (the template's own
    owner) that there is no {{TARGET_LANG}} token at all, so --target-lang
    is deliberately NOT part of this closure (see skeptic_setup.py's own
    module docstring)."""
    root = make_skeptic_root(tmp_path)
    entry = make_worklist_entry("Jean", occurrence_refs=[make_occurrence_ref("b1", "seg01", 0, 4)])
    write_worklist(root, [entry])

    proc1, parsed1 = run_skeptic_setup(root, source_lang="fr")
    assert proc1.returncode == 0, f"stdout={proc1.stdout}\nstderr={proc1.stderr}"
    run_id_1 = parsed1["effectiveRunId"]

    proc2, parsed2 = run_skeptic_setup(root, source_lang="it", resume_from_run_id=run_id_1)
    assert proc2.returncode == 0, f"stdout={proc2.stdout}\nstderr={proc2.stderr}"
    assert parsed2["resume"] is False, (
        "MUTATION CAUGHT: if the language tokens were left out of the "
        "skeptic input_digest, this --source-lang-only change would be "
        "invisible and the run would wrongly RESUME run_id_1"
    )
    assert parsed2["effectiveRunId"] != run_id_1
    assert parsed2["input_digest"] != parsed1["input_digest"]


def test_skeptic_constants_byte_edit_forces_new_run_id(tmp_path):
    """Round-3 blocker 1: skeptic_constants.py (the authoritative-defaults
    module) must be in the closure it governs."""
    root = make_skeptic_root(tmp_path)
    entry = make_worklist_entry("Jean", occurrence_refs=[make_occurrence_ref("b1", "seg01", 0, 4)])
    write_worklist(root, [entry])

    proc1, parsed1 = run_skeptic_setup(root)
    assert proc1.returncode == 0, f"stdout={proc1.stdout}\nstderr={proc1.stderr}"
    run_id_1 = parsed1["effectiveRunId"]

    constants_path = root / "scripts" / "skeptic_constants.py"
    constants_path.write_bytes(constants_path.read_bytes() + b"\n# one-byte-class edit\n")

    write_worklist(root, [entry])  # re-stamped, as a real re-run would do

    proc2, parsed2 = run_skeptic_setup(root, resume_from_run_id=run_id_1)
    assert proc2.returncode == 0, f"stdout={proc2.stdout}\nstderr={proc2.stderr}"
    assert parsed2["resume"] is False, (
        "MUTATION CAUGHT: if skeptic_constants.py were left out of the "
        "skeptic code closure, this byte edit would be invisible and the "
        "run would wrongly RESUME run_id_1"
    )
    assert parsed2["effectiveRunId"] != run_id_1


def test_citation_block_types_zero_arg_flows_through_as_explicit_empty_override(tmp_path):
    """Fix L11: --citation-block-types must accept nargs="*" (zero-or-more),
    same fix A1 applies to suspicion_scan.py's own identical flag, so an
    EXPLICIT EMPTY override (disabling class 5's all_citation fail-safe
    entirely, as opposed to omitting the flag and falling back to the
    source-format adapter default) is expressible at all. Pre-fix,
    nargs="+" rejects a zero-arg flag outright at the argparse level -- and
    even were it accepted, `args.citation_block_types` truthiness (`[]` is
    falsy) would silently coerce it back to `None` (the adapter default),
    never reaching resolve_citation_block_types() as an explicit override.
    Observable only via the recomputed producer_input_digest match/mismatch
    (citation_block_types otherwise has no other effect on this script's own
    output)."""
    root = make_skeptic_root(tmp_path)
    entry = make_worklist_entry("Jean", occurrence_refs=[make_occurrence_ref("b1", "seg01", 0, 4)])

    # Stamp a worklist whose producer_input_digest assumes an EXPLICIT EMPTY
    # citation_block_types override (`()`), not DEFAULT_SCAN_PARAMS's own
    # plain_text adapter default (`("FN", "QUOTE")`).
    digest = compute_expected_producer_digest(root, DEFAULT_SCAN_PARAMS, citation_override=())
    write_json(root / "suspicion_worklist.json", {
        "schema_version": 1, "producer_input_digest": digest, "entries": [entry],
    })

    proc, parsed = run_skeptic_setup(root, citation_block_types=[])

    assert proc.returncode == 0, f"stdout={proc.stdout}\nstderr={proc.stderr}"
    assert parsed is not None and parsed["success"] is True, (
        "MUTATION CAUGHT: a zero-arg --citation-block-types must recompute "
        "the producer_input_digest against an EXPLICIT EMPTY override, "
        "matching a worklist stamped the same way -- if it were silently "
        "coerced back to the adapter default (None), this run would reject "
        "the worklist as STALE"
    )


def test_resume_from_run_id_rejects_path_traversal(tmp_path):
    root = make_skeptic_root(tmp_path)
    entry = make_worklist_entry("Jean", occurrence_refs=[make_occurrence_ref("b1", "seg01", 0, 4)])
    write_worklist(root, [entry])

    proc, parsed = run_skeptic_setup(root, resume_from_run_id="../../etc")

    assert proc.returncode == 1
    assert parsed is not None and parsed["success"] is False


def test_resume_run_dir_symlink_escapes_durable_root_rejected(tmp_path):
    """Fix M7: validate_run_id() only does LEXICAL validation (regex, no
    '..') -- it can never catch a pre-planted SYMLINK sitting at a
    lexically-valid run id. If ${root}/skeptic/runs/<id> is a symlink to an
    external directory that also carries a matching input.digest, the
    resume-match condition is satisfied and (pre-fix) setup proceeds to
    write assignments_*.json/assignments.json THROUGH the symlink, straight
    outside the durable root."""
    root = make_skeptic_root(tmp_path)
    entry = make_worklist_entry("Jean", occurrence_refs=[make_occurrence_ref("b1", "seg01", 0, 4)])
    write_worklist(root, [entry])

    # A real fresh run first, purely to learn the deterministic input_digest
    # this exact fixture computes (a pure function of canon/manifest/
    # worklist/config -- independent of run_id): never reusing its real
    # run_dir for the attack below.
    proc0, parsed0 = run_skeptic_setup(root)
    assert proc0.returncode == 0, f"stdout={proc0.stdout}\nstderr={proc0.stderr}"
    matching_digest = parsed0["input_digest"]

    # Plant a symlink at a LEXICALLY-VALID run id pointing OUTSIDE the
    # durable root, with a matching input.digest.
    outside_dir = tmp_path / "outside_target"
    outside_dir.mkdir()
    (outside_dir / "input.digest").write_text(matching_digest + "\n", encoding="utf-8")
    evil_run_id = "evil-run"
    (root / "skeptic" / "runs" / evil_run_id).symlink_to(outside_dir, target_is_directory=True)

    proc, parsed = run_skeptic_setup(root, resume_from_run_id=evil_run_id)

    assert proc.returncode == 1
    assert parsed is not None and parsed["success"] is False
    assert not (outside_dir / "assignments.json").exists(), (
        "MUTATION CAUGHT: a symlinked run_dir pointing outside the durable "
        "root must never be written into -- if setup wrote assignments.json "
        "here, the escape succeeded"
    )


# ===========================================================================
# Segment cache_key (15-field composite) untouched by landing the skeptic
# scripts/schemas -- proves "no re-translate" directly against the REAL,
# unmodified cache_key.py.
# ===========================================================================

MINIMAL_PROFILE_YAML = """
project:
  pipeline_version: "1.6.0-test"
engine:
  effort: "high"
  max_fix_rounds: 3
source:
  language:
    code: "fr"
    particle_config: "test.json"
  format: "plain_text"
  path: "book.txt"
  adapter_config:
    plain_text: {}
target:
  language:
    code: "en"
verse_policy:
  mode: "skip"
  threshold_lines: 4
footnotes:
  apparatus_policy: "renumber"
validation:
  untranslated_sentinel: "UNTRANSLATED"
"""

STYLE_BIBLE_MD = (
    "intro\n<!-- STYLE_CONTRACT_BEGIN -->\nsome contract text\n<!-- STYLE_CONTRACT_END -->\ntail\n"
)


def make_cache_key_fixture_root(tmp_path) -> Path:
    """A minimal but complete cache_key.py-computable project -- independent
    of make_skeptic_root() above (that fixture has no profile.yml/segments/
    at all; cache_key.py needs a real project shape)."""
    root = tmp_path / "cache_key_root"
    scripts_dir = root / "scripts"
    scripts_dir.mkdir(parents=True)
    shutil.copy2(CACHE_KEY_SRC, scripts_dir / "cache_key.py")
    # DERIVATION_BUNDLE_MEMBERS -- compute_derivation_bundle_hash() reads
    # these two files' own bytes directly (never a marker), unlike
    # plugin_bundle_hash which only reads a static marker file.
    shutil.copy2(BOOTSTRAP_NAMES_SRC, scripts_dir / "bootstrap_names.py")
    shutil.copy2(SEGPACK_SRC, scripts_dir / "segpack.py")

    write_json(root / ".literary-translator-root.json", {"owner_profile_path": "profile.yml"})
    (root / "profile.yml").write_text(MINIMAL_PROFILE_YAML, encoding="utf-8")
    (root / "style_bible.md").write_text(STYLE_BIBLE_MD, encoding="utf-8")
    (root / "translate_TASK.md").write_text("translate prompt\n", encoding="utf-8")
    (root / "review_TASK.md").write_text("review prompt\n", encoding="utf-8")
    (root / "extract.py").write_text("# dummy extractor\n", encoding="utf-8")

    schemas_dir = root / "schemas"
    schemas_dir.mkdir(parents=True)
    for name in ("draft.schema.json", "review.schema.json", "segpack.schema.json"):
        write_json(schemas_dir / name, {"type": "object", "title": name})

    languages_dir = root / "languages"
    languages_dir.mkdir(parents=True)
    (languages_dir / "test.json").write_bytes(DEFAULT_PARTICLE_CONFIG_BYTES)

    (root / "book.txt").write_text("Jean met Paul.\n", encoding="utf-8")
    write_json(root / "manifest.json", {"source_inputs": ["book.txt"]})
    write_json(root / "canon.json", {"entries": {}})

    segments_dir = root / "segments"
    segments_dir.mkdir(parents=True)
    write_json(segments_dir / "segpack_seg01.json", {
        "blocks": [{"order_index": 0, "plain_text": "Jean met Paul."}],
        "footnotes": [], "verses": [],
    })

    runs_dir = root / "runs"
    runs_dir.mkdir(parents=True)
    (runs_dir / ".plugin_bundle_hash").write_text("pbh-fixture-v1", encoding="utf-8")
    (runs_dir / ".orchestration_bundle_hash").write_text("obh-fixture-v1", encoding="utf-8")

    return root


def compute_real_cache_key(root: Path, seg="seg01", timeout=30):
    proc = subprocess.run(
        [sys.executable, str(root / "scripts" / "cache_key.py"), "--seg", seg],
        capture_output=True, text=True, cwd=str(root), timeout=timeout,
    )
    assert proc.returncode == 0, f"cache_key.py failed: rc={proc.returncode}\nstdout={proc.stdout}\nstderr={proc.stderr}"
    return json.loads(proc.stdout)


def test_landing_skeptic_scripts_leaves_segment_cache_key_byte_identical(tmp_path):
    """Regression-lock (plan Part B / Decisions): none of the new skeptic
    scripts may enter PLUGIN_BUNDLE_MEMBERS/DERIVATION_BUNDLE_MEMBERS, and
    compute_schema_hash() only ever hashes draft/review/segpack.schema.json
    by NAME -- never the whole schemas/ directory -- so new *.schema.json
    files are cache-key-safe. Proven directly against the REAL, unmodified
    cache_key.py: the full 15-field composite for one segment must be
    byte-identical whether or not the new skeptic scripts/schemas are
    present on disk alongside it.
    """
    root = make_cache_key_fixture_root(tmp_path)
    before = compute_real_cache_key(root)

    # MUTATION this catches: if any of these were wrongly added to
    # PLUGIN_BUNDLE_MEMBERS/DERIVATION_BUNDLE_MEMBERS, or compute_schema_hash()
    # hashed the whole schemas/ dir instead of the three named files, `after`
    # would diverge from `before`.
    shutil.copy2(SKEPTIC_SETUP_SRC, root / "scripts" / "skeptic_setup.py")
    shutil.copy2(SKEPTIC_CONSTANTS_SRC, root / "scripts" / "skeptic_constants.py")
    shutil.copy2(WORKLIST_SCHEMA_SRC, root / "schemas" / "suspicion-worklist.schema.json")
    shutil.copy2(ASSIGNMENT_SCHEMA_SRC, root / "schemas" / "skeptic-assignment.schema.json")

    after = compute_real_cache_key(root)

    assert after == before, (
        "landing the skeptic scripts/schemas changed the segment cache_key -- "
        f"a converged segment would wrongly re-translate.\nbefore={json.dumps(before, indent=2)}"
        f"\nafter={json.dumps(after, indent=2)}"
    )


def test_new_skeptic_scripts_absent_from_cache_key_bundle_tuples(tmp_path):
    """Static regression-lock directly against cache_key.py's own source
    (never edited by this owner): none of the five new skeptic scripts may
    ever be listed in PLUGIN_BUNDLE_MEMBERS or DERIVATION_BUNDLE_MEMBERS --
    either would silently force a re-translate of every converged
    mass/glossary segment the next time Step 0a stamps a fresh
    plugin_bundle_hash (this is the complement to the end-to-end test
    above, which the static marker-file read in compute_plugin_bundle_hash()
    cannot itself exercise)."""
    ck = _load_module("cache_key_bundle_check", CACHE_KEY_SRC, SCRIPTS_DIR)
    new_skeptic_files = {
        "suspicion_scan.py", "skeptic_setup.py", "skeptic_ready.py",
        "skeptic_report.py", "skeptic_constants.py",
    }
    assert not (new_skeptic_files & set(ck.PLUGIN_BUNDLE_MEMBERS)), (
        f"found new skeptic script(s) in PLUGIN_BUNDLE_MEMBERS: "
        f"{new_skeptic_files & set(ck.PLUGIN_BUNDLE_MEMBERS)}"
    )
    assert not (new_skeptic_files & set(ck.DERIVATION_BUNDLE_MEMBERS)), (
        f"found new skeptic script(s) in DERIVATION_BUNDLE_MEMBERS: "
        f"{new_skeptic_files & set(ck.DERIVATION_BUNDLE_MEMBERS)}"
    )


# ===========================================================================
# Cross-language (Python <-> JS) filename-convention drift guard
# ===========================================================================


def test_assignment_batch_prefix_matches_template_js_convention():
    """skeptic_setup.py's own ASSIGNMENT_BATCH_PREFIX ("assignments_") and
    the SHIPPED skeptic-pass-wf.template.js's assignmentsBatchPath() helper
    must agree on the per-batch fragment filename prefix -- the two can
    never share a Python constant (the template is JS), so this reads both
    real, on-disk files and asserts the literal prefix string appears in
    the template, guarding the actual drift risk directly rather than via
    a relocation that wouldn't reach the JS side anyway."""
    mod = _load_module("skeptic_setup_for_prefix_test", SKEPTIC_SETUP_SRC, SCRIPTS_DIR)
    template_text = SKEPTIC_TEMPLATE_SRC.read_text(encoding="utf-8")
    assert mod.ASSIGNMENT_BATCH_PREFIX in template_text, (
        f"skeptic-pass-wf.template.js does not contain the literal prefix "
        f"{mod.ASSIGNMENT_BATCH_PREFIX!r} -- skeptic_setup.py's own "
        "ASSIGNMENT_BATCH_PREFIX has drifted from the template's "
        "assignmentsBatchPath() convention"
    )


def test_template_always_passes_canon_and_senses_path_flags_unconditionally():
    """#243 (codex review): the template's checkCommand() (batch precheck/
    dispatch-self-check/wait-poll) and verifyMergedPrompt() must BOTH pass
    --canon/--senses-path UNCONDITIONALLY -- deliberately, even for a
    project with no canon_senses.json yet -- because skeptic_ready.py's own
    --senses-path is UNCONDITIONALLY absence-tolerant (see
    _resolve_competitors' own docstring), not gated on whether the flag was
    explicitly given. This is a regression lock on the DESIGN DECISION
    itself: if a future edit made the template conditionally OMIT the flag
    when the file doesn't exist (the other fix codex offered), it would also
    need to revert skeptic_ready.py's own tolerance back to the
    explicit-path-must-exist convention -- the two must move together, never
    drift independently."""
    template_text = SKEPTIC_TEMPLATE_SRC.read_text(encoding="utf-8")
    check_command_src = template_text[
        template_text.index("function checkCommand"):template_text.index("function batchPrecheckPrompt")
    ]
    verify_prompt_src = template_text[
        template_text.index("function verifyMergedPrompt"):
    ]
    for label, src in (("checkCommand", check_command_src), ("verifyMergedPrompt", verify_prompt_src)):
        assert "--canon" in src and "CANON_PATH" in src, f"{label} must pass --canon"
        assert "--senses-path" in src and "SENSES_PATH" in src, f"{label} must pass --senses-path"
