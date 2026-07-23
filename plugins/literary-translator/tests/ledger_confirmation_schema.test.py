"""tests/ledger_confirmation_schema.test.py -- locks the ledger write/merge
confirmation contract from BOTH directions (CONTRACT-1.2.0-reliability.md
sections 1 and 5; PLAN's "NEW ledger_confirmation_schema.test.py" spec):

  (a) ACCEPT-SIDE: drives the REAL scripts_update.py / ledger_merge.py via
      subprocess (write/merge x success/failure) and asserts every captured
      stdout payload validates against BOTH the on-disk STRONG `oneOf`
      schema (`ledger-write-confirmation.schema.json` /
      `ledger-merge-confirmation.schema.json`, unchanged by this build) AND
      the FLAT agent-facing JS literal (`LEDGER_WRITE_SCHEMA` /
      `LEDGER_MERGE_SCHEMA` in mass-translate-wf.template.js) -- structural
      CONTAINMENT, not strict parity: the flat schema is a deliberate
      superset union of both branches (CONTRACT section 1), so this file
      asserts the flat schema ACCEPTS what the real scripts actually emit,
      never that the two are byte-identical.
  (b) REJECT-SIDE: pure `jsonschema.validate()` calls against the on-disk
      STRONG schemas (no subprocess needed -- both files are UNCHANGED by
      this build) proving they reject a branch-crossover
      (`{success:true, error:"x"}`), a missing required field, and an
      unknown field.
  (c) PROPERTY-SET UNION: the flat literal's property set must equal the
      on-disk strong schema's SUCCESS-branch-union-FAILURE-branch property
      set, for both LEDGER_WRITE and LEDGER_MERGE -- derived PROGRAMMATICALLY
      from the on-disk schema files, never hand-typed.
  (d) CONSUME-SITE JS GUARD (CONTRACT section 5): a small Python mirror of
      the load-bearing `ledgerWriteSucceeded`/`ledgerMergeSucceeded` JS
      guard predicates in mass-translate-wf.template.js, FOR TESTABILITY
      ONLY -- the real, load-bearing implementation lives in the JS
      template (owner B). Property-tested against clean success/failure,
      a branch crossover, success-keys-plus-real-failure-evidence, a
      truthful `exit_code: 0` on a success return (#289 -- accepted; it is
      evidence the script SUCCEEDED), an empty-string required string
      field, and a claimed success with a non-empty missing_segments. Plus
      REQUIRED static checks that the SAME key-set literals appear in the
      real JS guard functions near the template's ledger consume sites, that
      every key `hasFailureEvidence()` is called with has a
      `NO_FAILURE_EVIDENCE` row, and that no key-PRESENCE test survives
      anywhere in the template's code outside that one helper (#289).

Volatility note (this file was reconciled against the FINAL landed scripts
after several earlier-drafted CLI guesses -- including an earlier version of
THIS file's own docstring -- turned out stale; the 1.2.0 build's owners were
landing changes concurrently, and the token-check CLI shape was rewritten
more than once): the brief's originally-pinned `--expect-token TOK` guess
(on both scripts) never landed. What Owner C actually shipped, verified
directly against real subprocess runs with production-shaped token pairs:
`ledger_update.py` takes NO new CLI flag at all -- the calling agent folds
an optional `run_token` (a bare RUN_ID string) directly into the SAME
`--payload-file` JSON it already writes for `status`/`rounds`/etc.
`ledger_merge.py` takes a bare `--run-token RUN_ID` CLI flag (not
`--expect-token`, and not the draft-form `<RUN_ID>:<seg>` token -- just the
bare RUN_ID; the script itself reconstructs the draft-form token via its own
`expected_draft_token(run_token, seg)` helper). Both scripts then require:
the on-disk draft's own `dispatch_token` equals `f"{run_token}:{seg}"`
EXACTLY, and `review.json`'s own `dispatch_token` equals that same value
plus a `:r<roundLabel>` SUFFIX (matched by prefix, via a
`review_token_matches()` helper both scripts share byte-for-byte). This
design is internally self-consistent, and is what this file's
"new-behavior" accept-side sub-cases test below. It is GREEN, not
expected-red -- readers should not assume a still-red "blocked on Owner C"
sub-case exists here for the token mechanism itself.

Reconciliation note: an earlier draft of this docstring flagged a live B<->C
interface mismatch here (the JS templates calling a `run_token`/
`--run-token` shape the Python scripts' argparse didn't yet accept). That
gap has since been closed -- `mass-translate-wf.template.js`'s
`recordLedgerPrompt` writes a `run_token` field (this run's bare `RUN_ID`)
into the SAME payload object it already writes for `status`/`rounds`/etc,
and `mergeLedgerPrompt` passes `--run-token RUN_ID` alongside
`--expected-segs`, both matching exactly what `ledger_update.py`/
`ledger_merge.py` accept (confirmed directly against the current template
source). This file's own coverage is still scoped to the two scripts' OWN
CLI contracts, driven directly via subprocess rather than through the JS
templates -- it does not additionally re-verify the template's prompt text
itself (that's `bounded_poll_present.test.py`'s and
`draft_path_convention.test.py`'s territory).

House style: real files only (the real scripts, copied into an isolated
`tmp_path` fixture root so their own `Path(__file__).resolve().parents[1]`
self-anchoring resolves against the fixture, exactly as production
invokes them), never a reimplementation of either script's own logic.
"""
import hashlib
import importlib.util
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import jsonschema
import jsonschema.exceptions
import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
ASSETS_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets"
SCRIPTS_SRC_DIR = ASSETS_DIR / "scripts"
SCHEMAS_SRC_DIR = ASSETS_DIR / "schemas"
TEMPLATES_DIR = ASSETS_DIR / "templates"

LEDGER_UPDATE_SRC = SCRIPTS_SRC_DIR / "ledger_update.py"
LEDGER_MERGE_SRC = SCRIPTS_SRC_DIR / "ledger_merge.py"
MASS_TRANSLATE_PATH = TEMPLATES_DIR / "mass-translate-wf.template.js"

for _p in (LEDGER_UPDATE_SRC, LEDGER_MERGE_SRC, MASS_TRANSLATE_PATH):
    assert _p.is_file(), f"expected plugin asset not found: {_p}"


# ---------------------------------------------------------------------------
# Reuse review_prompt_schema_drift.test.py's hand-rolled JS object-literal
# parser -- never a second, vendored copy (house style, this suite has no
# Node.js hard dependency for this kind of no-execution literal parsing).
# ---------------------------------------------------------------------------

_DRIFT_TEST_PATH = Path(__file__).resolve().parent / "review_prompt_schema_drift.test.py"
assert _DRIFT_TEST_PATH.is_file(), f"expected sibling test file not found: {_DRIFT_TEST_PATH}"

_drift_spec = importlib.util.spec_from_file_location(
    "review_prompt_schema_drift_shared_for_ledger_confirmation_schema", _DRIFT_TEST_PATH
)
_drift = importlib.util.module_from_spec(_drift_spec)
_drift_spec.loader.exec_module(_drift)

parse_js_object_literal = _drift.parse_js_object_literal
_extract_const_object_literal_raw = _drift.extract_const_object_literal


def _from_declaration(mass_translate_source: str, needle: str) -> str:
    """`mass_translate_source` sliced to begin at `needle`'s first occurrence
    in the CODE PROJECTION.

    Every shared JS extractor this file reuses -- the drift module's
    extract_const_object_literal, ledger_update.test.py's _extract_js_function
    and _extract_js_const -- scans BALANCED and comment-aware, but LOCATES its
    starting point with a bare index/search over raw source. A commented-out
    or prose copy of the same declaration therefore wins:

        // historical shape: const FAILURE_EVIDENCE_KEYS = ["error"];

    made all three return the decoy. Handing them a source that already
    starts at the real declaration fixes the location without touching their
    scanning, and without a second vendored copy of any of them. The two
    sibling files still carry the raw-locator weakness in their own uses --
    reported, not fixed here, since they are not this file's to change."""
    code = _js_code_only(mass_translate_source)
    idx = code.find(needle)
    assert idx != -1, (
        f"the template's CODE declares no {needle!r} -- it may exist only "
        "inside a comment or a prompt string, which is exactly what this "
        "projection-anchored lookup exists to refuse"
    )
    return mass_translate_source[idx:]


def extract_const_object_literal(mass_translate_source: str, const_name: str) -> str:
    """Projection-anchored wrapper -- see _from_declaration()."""
    return _extract_const_object_literal_raw(
        _from_declaration(mass_translate_source, f"const {const_name} "), const_name
    )


# ---------------------------------------------------------------------------
# Reuse ledger_update.test.py's verbatim JS extractors for the node harness
# below -- same house rule, never a second vendored copy. That file already
# splices real template functions into a standalone node script; this file
# does the same for the consume-site guards, so the extraction primitives are
# shared rather than reimplemented. Importing it is read-only: its
# module-level work is template reads and extractions, and its own node
# dependency is expressed as a pytest marker, not an import-time failure.
# ---------------------------------------------------------------------------

_LEDGER_UPDATE_TEST_PATH = Path(__file__).resolve().parent / "ledger_update.test.py"
assert _LEDGER_UPDATE_TEST_PATH.is_file(), f"expected sibling test file not found: {_LEDGER_UPDATE_TEST_PATH}"

_ledger_update_spec = importlib.util.spec_from_file_location(
    "ledger_update_shared_for_ledger_confirmation_schema", _LEDGER_UPDATE_TEST_PATH
)
_ledger_update = importlib.util.module_from_spec(_ledger_update_spec)
_ledger_update_spec.loader.exec_module(_ledger_update)

_extract_js_function = _ledger_update._extract_js_function
_extract_js_const = _ledger_update._extract_js_const


# ---------------------------------------------------------------------------
# Fixtures: the on-disk STRONG schemas (unchanged by this build) and the
# FLAT agent-facing JS literals (parsed from the real shipped template).
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def mass_translate_source() -> str:
    return MASS_TRANSLATE_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def ledger_write_confirmation_schema() -> dict:
    path = SCHEMAS_SRC_DIR / "ledger-write-confirmation.schema.json"
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def ledger_merge_confirmation_schema() -> dict:
    path = SCHEMAS_SRC_DIR / "ledger-merge-confirmation.schema.json"
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def ledger_write_schema_flat(mass_translate_source) -> dict:
    return parse_js_object_literal(
        extract_const_object_literal(mass_translate_source, "LEDGER_WRITE_SCHEMA")
    )


@pytest.fixture(scope="module")
def ledger_merge_schema_flat(mass_translate_source) -> dict:
    return parse_js_object_literal(
        extract_const_object_literal(mass_translate_source, "LEDGER_MERGE_SCHEMA")
    )


# ---------------------------------------------------------------------------
# Shared assertion helpers.
# ---------------------------------------------------------------------------

def assert_strong_schema_accepts(oneof_schema: dict, payload: dict, *, label: str) -> None:
    try:
        jsonschema.validate(instance=payload, schema=oneof_schema)
    except jsonschema.exceptions.ValidationError as exc:
        raise AssertionError(
            f"{label}: the on-disk STRONG schema rejected a real script "
            f"payload it must accept: {exc.message}\npayload: {payload}"
        ) from exc


def assert_flat_schema_accepts(flat_schema: dict, payload: dict, *, label: str) -> None:
    """The flat literal IS valid JSON Schema syntax once parsed from JS to a
    Python dict (CONTRACT section 1's literals are plain type:"object"
    schemas) -- so this validates directly with jsonschema, proving the flat
    schema genuinely ACCEPTS the real script's payload shape (the
    "decoupled, not strict-parity" containment check the CONTRACT calls
    for), not merely that it LOOKS similar by inspection."""
    try:
        jsonschema.validate(instance=payload, schema=flat_schema)
    except jsonschema.exceptions.ValidationError as exc:
        raise AssertionError(
            f"{label}: the flat agent-facing schema rejected a real script "
            f"payload it must accept: {exc.message}\npayload: {payload}"
        ) from exc


def sha1_of_bytes(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def draft_content_sha1_of(draft_obj: dict) -> str:
    """Mirrors draft_content_sha1() in BOTH ledger_update.py and
    ledger_merge.py (byte-identical duplicates of each other, per this
    project's "no shared lib between self-contained scripts" convention) --
    used ONLY to build fixture review.json's draft_sha1 field to a value
    that will genuinely match what the real scripts compute; never used as
    a substitute for actually running either real script."""
    projected = {k: v for k, v in draft_obj.items() if k != "dispatch_token"}
    canonical = json.dumps(
        projected, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return sha1_of_bytes(canonical)


FULL_CACHE_KEY = {
    "input_sha1": "a1", "style_contract_hash": "b2", "used_terms_hash": "c3",
    "pipeline_version": "v1", "schema_hash": "d4", "prompt_hash": "e5",
    "agent_config_hash": "f6", "profile_semantics_hash": "g7",
    "particle_config_hash": "h8", "source_extraction_hash": "i9",
    "source_input_hash": "j10", "derivation_bundle_hash": "k11",
    "verse_map_hash": "l12", "note_map_hash": "m13", "plugin_bundle_hash": "n14",
}


# ---------------------------------------------------------------------------
# Isolated durable_root fixture builders -- copy the REAL script(s) into
# {root}/scripts/ so self-anchoring resolves against the fixture, exactly
# as production invokes them (house style; mirrors ledger_update.test.py /
# ledger_merge.test.py / draft_path_convention.test.py).
# ---------------------------------------------------------------------------

def make_ledger_update_root(tmp_path) -> Path:
    root = tmp_path / "durable_root"
    scripts_dir = root / "scripts"
    schemas_dir = root / "schemas"
    scripts_dir.mkdir(parents=True)
    schemas_dir.mkdir(parents=True)
    shutil.copy2(LEDGER_UPDATE_SRC, scripts_dir / "ledger_update.py")
    shutil.copy2(
        SCHEMAS_SRC_DIR / "ledger-record-base.schema.json",
        schemas_dir / "ledger-record-base.schema.json",
    )
    shutil.copy2(
        SCHEMAS_SRC_DIR / "ledger-fragment.schema.json",
        schemas_dir / "ledger-fragment.schema.json",
    )
    (root / "segments").mkdir()
    (root / "runs").mkdir()
    return root


def make_ledger_merge_root(tmp_path) -> Path:
    root = tmp_path / "durable_root"
    scripts_dir = root / "scripts"
    scripts_dir.mkdir(parents=True)
    shutil.copy2(LEDGER_MERGE_SRC, scripts_dir / "ledger_merge.py")
    # ledger_merge.py's own _build_schema_registry() globs every
    # *.schema.json under SCHEMAS_DIR (needed for ledger.schema.json's own
    # $ref to ledger-record-base.schema.json) -- copy the whole real dir,
    # mirroring ledger_merge.test.py's own fixture convention.
    shutil.copytree(SCHEMAS_SRC_DIR, root / "schemas")
    (root / "segments").mkdir()
    (root / "runs" / "ledger.d").mkdir(parents=True)
    return root


def write_payload(root: Path, name: str, payload: dict) -> Path:
    path = root / "runs" / f".ledger_update_payload.{name}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def run_ledger_update(root: Path, seg: str, payload_path: Path):
    cmd = [
        sys.executable, str(root / "scripts" / "ledger_update.py"), seg,
        "--payload-file", str(payload_path),
    ]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=30)


def run_ledger_merge(root: Path, *extra_args, timeout=30):
    return subprocess.run(
        [sys.executable, str(root / "scripts" / "ledger_merge.py"), *extra_args],
        capture_output=True, text=True, timeout=timeout, cwd=str(root),
    )


def parse_single_json_line(proc) -> dict:
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    assert len(lines) == 1, (
        f"expected exactly one JSON line on stdout, got {len(lines)}:\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    return json.loads(lines[0])


def write_convergence_fixture(root: Path, seg: str, *, draft_token=None, review_token=None) -> str:
    """Writes a minimal, schema-consistent draft+review+segpack triple at
    the canonical paths for a convergence write/merge. Returns the
    correctly-computed draft_sha1 (the value review.json's own draft_sha1
    field is set to), so a genuine converged write/merge is possible.
    `draft_token`/`review_token`, when given, are written as the draft's/
    review's own `dispatch_token` field (CONTRACT section 2's new field) --
    omitted entirely for the pre-token, currently-still-supported call
    shape."""
    segments_dir = root / "segments"
    draft = {"seg": seg, "blocks": {"p1": "hi"}}
    if draft_token is not None:
        draft["dispatch_token"] = draft_token
    draft_sha1 = draft_content_sha1_of(draft)
    (segments_dir / f"{seg}.draft.json").write_text(json.dumps(draft), encoding="utf-8")
    (segments_dir / f"segpack_{seg}.json").write_text(
        json.dumps({"blocks": [{"id": "p1"}], "footnotes": [], "verses": []}), encoding="utf-8",
    )
    review = {"draft_sha1": draft_sha1}
    if review_token is not None:
        review["dispatch_token"] = review_token
    (segments_dir / f"{seg}.review.json").write_text(json.dumps(review), encoding="utf-8")
    return draft_sha1


def write_fragment(root: Path, seg: str, record: dict) -> Path:
    frag_path = root / "runs" / "ledger.d" / f"{seg}.json"
    frag_path.write_text(json.dumps(record, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    return frag_path


# ===========================================================================
# (a) ACCEPT-SIDE -- ledger_update.py, pre-token call shape (still
#     currently supported: --expect-token is optional and backward
#     compatible when omitted).
# ===========================================================================

def test_ledger_update_converged_success_validates_against_both_schemas(
    tmp_path, ledger_write_confirmation_schema, ledger_write_schema_flat,
):
    root = make_ledger_update_root(tmp_path)
    seg = "seg01"
    write_convergence_fixture(root, seg)
    payload_path = write_payload(root, "p1", {"status": "converged", "rounds": 1, "cache_key": FULL_CACHE_KEY})

    proc = run_ledger_update(root, seg, payload_path)
    assert proc.returncode == 0, f"expected convergence to succeed:\n{proc.stdout}\n{proc.stderr}"
    payload = parse_single_json_line(proc)
    assert payload["success"] is True

    assert_strong_schema_accepts(ledger_write_confirmation_schema, payload, label="ledger_update.py converged SUCCESS")
    assert_flat_schema_accepts(ledger_write_schema_flat, payload, label="ledger_update.py converged SUCCESS")


def test_ledger_update_converged_failure_missing_draft_validates_against_both_schemas(
    tmp_path, ledger_write_confirmation_schema, ledger_write_schema_flat,
):
    root = make_ledger_update_root(tmp_path)
    seg = "seg02"
    segments_dir = root / "segments"
    # segpack + review present, but the draft itself is deliberately absent.
    (segments_dir / f"segpack_{seg}.json").write_text(
        json.dumps({"blocks": [{"id": "p1"}], "footnotes": [], "verses": []}), encoding="utf-8",
    )
    (segments_dir / f"{seg}.review.json").write_text(
        json.dumps({"draft_sha1": "0" * 40}), encoding="utf-8",
    )
    payload_path = write_payload(root, "p2", {"status": "converged", "rounds": 1, "cache_key": FULL_CACHE_KEY})

    proc = run_ledger_update(root, seg, payload_path)
    assert proc.returncode != 0
    payload = parse_single_json_line(proc)
    assert payload["success"] is False
    assert "draft not found" in payload["error"]

    assert_strong_schema_accepts(ledger_write_confirmation_schema, payload, label="ledger_update.py converged FAILURE (missing draft)")
    assert_flat_schema_accepts(ledger_write_schema_flat, payload, label="ledger_update.py converged FAILURE (missing draft)")


# ===========================================================================
# (a) ACCEPT-SIDE -- ledger_update.py, run_token-in-payload call shape (see
#     module docstring's "Volatility note": verified GREEN against real
#     production-shaped token pairs, not merely read from source).
# ===========================================================================

def test_ledger_update_run_token_matching_draft_and_review_succeeds(
    tmp_path, ledger_write_confirmation_schema, ledger_write_schema_flat,
):
    root = make_ledger_update_root(tmp_path)
    seg = "seg03"
    run_id = "20260710T000000Z"
    draft_token = f"{run_id}:{seg}"
    review_token = f"{run_id}:{seg}:rfinal"
    write_convergence_fixture(root, seg, draft_token=draft_token, review_token=review_token)
    payload_path = write_payload(
        root, "p3",
        {"status": "converged", "rounds": 1, "cache_key": FULL_CACHE_KEY, "run_token": run_id},
    )

    proc = run_ledger_update(root, seg, payload_path)
    assert proc.returncode == 0, (
        f"a draft whose dispatch_token EXACTLY equals '<run_token>:<seg>', paired "
        f"with a review whose own dispatch_token carries that same value "
        f"plus a ':r<roundLabel>' suffix, must be accepted as a genuine "
        f"same-run pair:\n{proc.stdout}\n{proc.stderr}"
    )
    payload = parse_single_json_line(proc)
    assert payload["success"] is True

    assert_strong_schema_accepts(ledger_write_confirmation_schema, payload, label="ledger_update.py run_token SUCCESS")
    assert_flat_schema_accepts(ledger_write_schema_flat, payload, label="ledger_update.py run_token SUCCESS")


def test_ledger_update_run_token_mismatch_refused_for_token_reason(
    tmp_path, ledger_write_confirmation_schema, ledger_write_schema_flat,
):
    """A draft+review pair that is internally consistent with EACH OTHER
    (same run/seg) but not with the CURRENT run's own payload `run_token` (a
    stale/straggler pair from a different run) must be refused -- and for
    the genuine token-mismatch reason, never confused with an unrelated
    failure mode."""
    root = make_ledger_update_root(tmp_path)
    seg = "seg04"
    stale_run_id = "20260101T000000Z"
    current_run_id = "20260710T000000Z"
    draft_token = f"{stale_run_id}:{seg}"
    review_token = f"{stale_run_id}:{seg}:rfinal"
    write_convergence_fixture(root, seg, draft_token=draft_token, review_token=review_token)
    payload_path = write_payload(
        root, "p4",
        {"status": "converged", "rounds": 1, "cache_key": FULL_CACHE_KEY, "run_token": current_run_id},
    )

    proc = run_ledger_update(root, seg, payload_path)
    assert proc.returncode != 0
    payload = parse_single_json_line(proc)
    assert payload["success"] is False
    assert "dispatch_token" in payload["error"], (
        f"must be refused for a genuine dispatch_token mismatch, got: {payload['error']!r}"
    )
    assert "Malformed payload" not in payload["error"]

    assert_strong_schema_accepts(ledger_write_confirmation_schema, payload, label="ledger_update.py run_token MISMATCH")
    assert_flat_schema_accepts(ledger_write_schema_flat, payload, label="ledger_update.py run_token MISMATCH")


# ===========================================================================
# (a) ACCEPT-SIDE -- ledger_merge.py, pre-token call shape.
# ===========================================================================

def test_ledger_merge_success_no_missing_no_stale_validates_against_both_schemas(
    tmp_path, ledger_merge_confirmation_schema, ledger_merge_schema_flat,
):
    root = make_ledger_merge_root(tmp_path)
    write_fragment(root, "seg01", {"timestamp": "2026-01-01T00:00:00Z", "status": "pending"})
    write_fragment(root, "seg02", {"timestamp": "2026-01-01T00:00:00Z", "status": "in_progress"})

    proc = run_ledger_merge(root, "--expected-segs", "seg01,seg02", "--skip-stale-check")
    assert proc.returncode == 0, f"{proc.stdout}\n{proc.stderr}"
    payload = parse_single_json_line(proc)
    assert payload["success"] is True
    assert payload["missing_segments"] == []

    assert_strong_schema_accepts(ledger_merge_confirmation_schema, payload, label="ledger_merge.py SUCCESS")
    assert_flat_schema_accepts(ledger_merge_schema_flat, payload, label="ledger_merge.py SUCCESS")


def test_ledger_merge_failure_missing_expected_segment_validates_against_both_schemas(
    tmp_path, ledger_merge_confirmation_schema, ledger_merge_schema_flat,
):
    root = make_ledger_merge_root(tmp_path)
    write_fragment(root, "seg01", {"timestamp": "2026-01-01T00:00:00Z", "status": "pending"})
    # seg02 is expected but has no fragment at all.

    proc = run_ledger_merge(root, "--expected-segs", "seg01,seg02", "--skip-stale-check")
    assert proc.returncode != 0
    payload = parse_single_json_line(proc)
    assert payload["success"] is False
    assert payload["missing_segments"] == ["seg02"]

    assert_strong_schema_accepts(ledger_merge_confirmation_schema, payload, label="ledger_merge.py FAILURE (missing segment)")
    assert_flat_schema_accepts(ledger_merge_schema_flat, payload, label="ledger_merge.py FAILURE (missing segment)")


# ===========================================================================
# (a) ACCEPT-SIDE -- ledger_merge.py, --run-token call shape (see module
#     docstring's "Volatility note").
# ===========================================================================

def _write_converged_merge_fixture(root: Path, seg: str, *, draft_token, review_token) -> dict:
    draft_sha1 = write_convergence_fixture(root, seg, draft_token=draft_token, review_token=review_token)
    fragment = {
        "timestamp": "2026-01-01T00:00:00Z",
        "status": "converged",
        "rounds": 1,
        "cache_key": FULL_CACHE_KEY,
        "n_blocks": 1,
        "n_footnotes": 0,
        "n_verses": 0,
        "reviewed_draft_sha1": draft_sha1,
    }
    write_fragment(root, seg, fragment)
    return fragment


def test_ledger_merge_run_token_matching_converged_segment_succeeds(
    tmp_path, ledger_merge_confirmation_schema, ledger_merge_schema_flat,
):
    root = make_ledger_merge_root(tmp_path)
    seg = "seg05"
    run_id = "20260710T000000Z"
    draft_token = f"{run_id}:{seg}"
    review_token = f"{run_id}:{seg}:rfinal"
    _write_converged_merge_fixture(root, seg, draft_token=draft_token, review_token=review_token)

    proc = run_ledger_merge(root, "--expected-segs", seg, "--run-token", run_id, "--skip-stale-check")
    assert proc.returncode == 0, (
        f"a converged segment whose on-disk draft+review dispatch_token both "
        f"trace to the current run's own --run-token must be accepted:\n"
        f"{proc.stdout}\n{proc.stderr}"
    )
    payload = parse_single_json_line(proc)
    assert payload["success"] is True

    assert_strong_schema_accepts(ledger_merge_confirmation_schema, payload, label="ledger_merge.py --run-token SUCCESS")
    assert_flat_schema_accepts(ledger_merge_schema_flat, payload, label="ledger_merge.py --run-token SUCCESS")


def test_ledger_merge_run_token_mismatch_on_converged_segment_refused(
    tmp_path, ledger_merge_confirmation_schema, ledger_merge_schema_flat,
):
    """Closes the race the batch-final merge check exists for (CONTRACT
    section 4/6): a converged segment whose draft+review pair is internally
    consistent but belongs to a DIFFERENT (stale) run than the current
    --run-token must never be folded into a success -- and per CONTRACT
    section 5's guard discipline, this must show up as a genuine failure
    (never silently dropped from missing_segments only, never a bare crash)."""
    root = make_ledger_merge_root(tmp_path)
    seg = "seg06"
    stale_run_id = "20260101T000000Z"
    current_run_id = "20260710T000000Z"
    draft_token = f"{stale_run_id}:{seg}"
    review_token = f"{stale_run_id}:{seg}:rfinal"
    _write_converged_merge_fixture(root, seg, draft_token=draft_token, review_token=review_token)

    proc = run_ledger_merge(
        root, "--expected-segs", seg, "--run-token", current_run_id, "--skip-stale-check",
    )
    assert proc.returncode != 0
    payload = parse_single_json_line(proc)
    assert payload["success"] is False
    assert "dispatch_token" in payload["error"], (
        f"must be refused for a genuine dispatch_token mismatch, got: {payload['error']!r}"
    )

    assert_strong_schema_accepts(ledger_merge_confirmation_schema, payload, label="ledger_merge.py --run-token MISMATCH")
    assert_flat_schema_accepts(ledger_merge_schema_flat, payload, label="ledger_merge.py --run-token MISMATCH")


# ===========================================================================
# (b) REJECT-SIDE -- the on-disk STRONG oneOf schemas reject a
#     branch-crossover, a missing required field, and an unknown field.
#     Pure jsonschema.validate() calls, no subprocess needed -- both files
#     are UNCHANGED by this build.
# ===========================================================================

@pytest.mark.parametrize(
    "instance",
    [
        pytest.param({"success": True, "error": "x"}, id="crossover-success-with-error"),
        pytest.param({"success": True, "status": "converged"}, id="success-missing-fragment-fields"),
        pytest.param(
            {"success": True, "status": "converged", "fragment_path": "p", "fragment_sha1": "s", "bogus": 1},
            id="success-plus-unknown-field",
        ),
        pytest.param({"success": False}, id="failure-missing-error"),
    ],
)
def test_ledger_write_confirmation_strong_schema_rejects(instance, ledger_write_confirmation_schema):
    with pytest.raises(jsonschema.exceptions.ValidationError):
        jsonschema.validate(instance=instance, schema=ledger_write_confirmation_schema)


@pytest.mark.parametrize(
    "instance",
    [
        pytest.param({"success": True, "error": "x"}, id="crossover-success-with-error"),
        pytest.param(
            {"success": True, "ledger_path": "p", "n_segments": 1, "missing_segments": ["seg09"], "stale_segments": []},
            id="success-with-nonempty-missing-segments",
        ),
        pytest.param(
            {"success": True, "ledger_path": "p", "n_segments": 1, "missing_segments": [], "stale_segments": [], "bogus": 1},
            id="success-plus-unknown-field",
        ),
        pytest.param({"success": False}, id="failure-missing-error"),
    ],
)
def test_ledger_merge_confirmation_strong_schema_rejects(instance, ledger_merge_confirmation_schema):
    with pytest.raises(jsonschema.exceptions.ValidationError):
        jsonschema.validate(instance=instance, schema=ledger_merge_confirmation_schema)


# ===========================================================================
# (c) PROPERTY-SET UNION -- the flat literal's property set equals the
#     on-disk strong schema's SUCCESS-union-FAILURE branch property set,
#     derived programmatically, never hand-typed.
# ===========================================================================

def union_of_oneof_branch_properties(oneof_schema: dict) -> set:
    result: set = set()
    for branch in oneof_schema["oneOf"]:
        result |= set(branch["properties"].keys())
    return result


def assert_flat_properties_equal_branch_union(flat_schema: dict, oneof_schema: dict, *, label: str) -> None:
    flat_props = set(flat_schema["properties"].keys())
    union_props = union_of_oneof_branch_properties(oneof_schema)
    assert flat_props == union_props, (
        f"{label}: flat literal's property set must equal the on-disk "
        f"strong schema's SUCCESS-union-FAILURE branch property set.\n"
        f"  flat:  {sorted(flat_props)}\n"
        f"  union: {sorted(union_props)}"
    )


def test_ledger_write_flat_properties_equal_strong_branch_union(
    ledger_write_schema_flat, ledger_write_confirmation_schema,
):
    assert_flat_properties_equal_branch_union(
        ledger_write_schema_flat, ledger_write_confirmation_schema, label="LEDGER_WRITE_SCHEMA",
    )


def test_ledger_merge_flat_properties_equal_strong_branch_union(
    ledger_merge_schema_flat, ledger_merge_confirmation_schema,
):
    assert_flat_properties_equal_branch_union(
        ledger_merge_schema_flat, ledger_merge_confirmation_schema, label="LEDGER_MERGE_SCHEMA",
    )


def test_property_set_union_helper_catches_a_dropped_flat_property(ledger_write_confirmation_schema, ledger_write_schema_flat):
    """Regression-catcher: the union-equality helper above is not vacuously
    true -- dropping a real flat property must be caught."""
    mutated_flat = json.loads(json.dumps(ledger_write_schema_flat))
    del mutated_flat["properties"]["stderr"]
    with pytest.raises(AssertionError):
        assert_flat_properties_equal_branch_union(
            mutated_flat, ledger_write_confirmation_schema, label="mutated LEDGER_WRITE_SCHEMA (dropped stderr)",
        )


# ===========================================================================
# (d) CONSUME-SITE JS GUARD (CONTRACT section 5) -- a Python MIRROR of the
#     load-bearing JS predicate, for testability only. The real,
#     load-bearing implementation lives in mass-translate-wf.template.js
#     (ledgerWriteSucceeded/ledgerMergeSucceeded), owned by Owner B.
#
# #289: the guard judges failure EVIDENCE, never failure-key PRESENCE. Both
# flat literals declare error/exit_code/stderr as fillable on every call, so
# an agent honestly relaying a successful script run may volunteer
# `exit_code: 0` -- proof of SUCCESS. Rejecting on presence turned that proof
# into a reported failure for segments already correctly written to disk.
# ===========================================================================

LEDGER_WRITE_SUCCESS_KEYS = {"success", "status", "fragment_path", "fragment_sha1"}
LEDGER_MERGE_SUCCESS_KEYS = {"success", "ledger_path", "n_segments", "missing_segments", "stale_segments"}
# Where failure evidence may APPEAR -- not keys that appear only on failure.
FAILURE_EVIDENCE_KEYS = {"error", "exit_code", "stderr"}
# Every key the corresponding flat literal declares. A benign, already
# value-checked evidence field must not be re-rejected as an unexpected key;
# a key neither branch declares still is.
LEDGER_WRITE_ALLOWED_KEYS = LEDGER_WRITE_SUCCESS_KEYS | FAILURE_EVIDENCE_KEYS
LEDGER_MERGE_ALLOWED_KEYS = LEDGER_MERGE_SUCCESS_KEYS | FAILURE_EVIDENCE_KEYS


def _is_non_empty_string(v) -> bool:
    return isinstance(v, str) and len(v) > 0


def _is_empty_string(v) -> bool:
    # Deliberately NOT the negation of _is_non_empty_string: a non-string is
    # neither. _has_failure_evidence leans on that asymmetry so a wrong-typed
    # error/stderr is treated as unreadable evidence and fails closed.
    return isinstance(v, str) and len(v) == 0


def _is_plain_int(v) -> bool:
    # bool is a subclass of int in Python -- n_segments must be a genuine
    # integer, never True/False sneaking through an `isinstance(v, int)`
    # check (see this repo's own stdlib-JSON-gate hardening notes).
    return isinstance(v, int) and not isinstance(v, bool)


def _has_failure_evidence(raw) -> bool:
    """True when a declared evidence field is filled with something that
    actually testifies to a failure. `exit_code: 0` testifies to success;
    `True` is not 0 here because bool is excluded via _is_plain_int, matching
    the JS side where `false !== 0`."""
    if "error" in raw and not _is_empty_string(raw["error"]):
        return True
    if "stderr" in raw and not _is_empty_string(raw["stderr"]):
        return True
    if "exit_code" in raw and not (_is_plain_int(raw["exit_code"]) and raw["exit_code"] == 0):
        return True
    return False


def ledger_write_succeeded(raw) -> bool:
    """Mirrors CONTRACT section 5's documented guard rule for testability --
    the load-bearing implementation lives in the JS template, owned by
    Owner B (ledgerWriteSucceeded() in mass-translate-wf.template.js)."""
    if not isinstance(raw, dict) or raw.get("success") is not True:
        return False
    if _has_failure_evidence(raw):
        return False
    if not set(raw.keys()) <= LEDGER_WRITE_ALLOWED_KEYS:
        return False
    return (
        _is_non_empty_string(raw.get("status"))
        and _is_non_empty_string(raw.get("fragment_path"))
        and _is_non_empty_string(raw.get("fragment_sha1"))
    )


def ledger_merge_succeeded(raw) -> bool:
    """Mirrors CONTRACT section 5's documented guard rule for testability --
    the load-bearing implementation lives in the JS template, owned by
    Owner B (ledgerMergeSucceeded() in mass-translate-wf.template.js)."""
    if not isinstance(raw, dict) or raw.get("success") is not True:
        return False
    if _has_failure_evidence(raw):
        return False
    if not set(raw.keys()) <= LEDGER_MERGE_ALLOWED_KEYS:
        return False
    missing = raw.get("missing_segments")
    stale = raw.get("stale_segments")
    return (
        _is_non_empty_string(raw.get("ledger_path"))
        and _is_plain_int(raw.get("n_segments"))
        and isinstance(missing, list) and len(missing) == 0
        and isinstance(stale, list)
    )


@pytest.mark.parametrize(
    "raw,expected",
    [
        pytest.param(
            {"success": True, "status": "converged", "fragment_path": "/x/seg01.json", "fragment_sha1": "a" * 40},
            True, id="clean-success",
        ),
        pytest.param({"success": False, "error": "boom"}, False, id="clean-failure"),
        pytest.param({"success": True, "error": "x"}, False, id="crossover-success-with-error"),
        # #289: a truthful exit_code:0 riding along on an otherwise perfect
        # success return is evidence the script SUCCEEDED -- accept it. The
        # flat literal advertises the field, so agents volunteer it.
        pytest.param(
            {
                "success": True, "status": "converged", "fragment_path": "/x/seg01.json",
                "fragment_sha1": "a" * 40, "exit_code": 0,
            },
            True, id="success-keys-plus-truthful-exit-code-zero",
        ),
        pytest.param(
            {
                "success": True, "status": "converged", "fragment_path": "/x/seg01.json",
                "fragment_sha1": "a" * 40, "exit_code": 3,
            },
            False, id="success-keys-plus-nonzero-exit-code",
        ),
        pytest.param(
            {
                "success": True, "status": "converged", "fragment_path": "/x/seg01.json",
                "fragment_sha1": "a" * 40, "stderr": "Traceback (most recent call last):",
            },
            False, id="success-keys-plus-nonempty-stderr",
        ),
        pytest.param(
            {
                "success": True, "status": "converged", "fragment_path": "/x/seg01.json",
                "fragment_sha1": "a" * 40, "exit_code": "0",
            },
            False, id="wrong-typed-exit-code-is-unreadable-evidence",
        ),
        pytest.param(
            {
                "success": True, "status": "converged", "fragment_path": "/x/seg01.json",
                "fragment_sha1": "a" * 40, "exit_code": 0, "ledger_path": "/x/l.json",
            },
            False, id="undeclared-key-still-rejected-alongside-exit-code-zero",
        ),
        pytest.param(
            {"success": True, "status": "", "fragment_path": "/x/seg01.json", "fragment_sha1": "a" * 40},
            False, id="empty-string-status",
        ),
        pytest.param(
            {"success": True, "status": "converged", "fragment_path": "", "fragment_sha1": "a" * 40},
            False, id="empty-string-fragment-path",
        ),
    ],
)
def test_ledger_write_succeeded_mirror_predicate(raw, expected):
    assert ledger_write_succeeded(raw) is expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        pytest.param(
            {"success": True, "ledger_path": "/x/ledger.json", "n_segments": 3, "missing_segments": [], "stale_segments": []},
            True, id="clean-success",
        ),
        pytest.param({"success": False, "error": "boom", "missing_segments": ["seg09"]}, False, id="clean-failure"),
        pytest.param({"success": True, "error": "x"}, False, id="crossover-success-with-error"),
        pytest.param(
            {
                "success": True, "ledger_path": "/x/ledger.json", "n_segments": 3,
                "missing_segments": [], "stale_segments": [], "stderr": "warn",
            },
            False, id="success-keys-plus-nonempty-stderr",
        ),
        # #289, merge side.
        pytest.param(
            {
                "success": True, "ledger_path": "/x/ledger.json", "n_segments": 3,
                "missing_segments": [], "stale_segments": [], "exit_code": 0,
            },
            True, id="success-keys-plus-truthful-exit-code-zero",
        ),
        pytest.param(
            {
                "success": True, "ledger_path": "/x/ledger.json", "n_segments": 3,
                "missing_segments": [], "stale_segments": [], "exit_code": 2,
            },
            False, id="success-keys-plus-nonzero-exit-code",
        ),
        pytest.param(
            {"success": True, "ledger_path": "/x/ledger.json", "n_segments": 1, "missing_segments": ["seg09"], "stale_segments": []},
            False, id="claimed-success-with-nonempty-missing-segments",
        ),
        pytest.param(
            {
                "success": True, "ledger_path": "/x/ledger.json", "n_segments": 1,
                "missing_segments": ["seg09"], "stale_segments": [], "exit_code": 0,
            },
            False, id="exit-code-zero-never-excuses-an-incomplete-batch",
        ),
        pytest.param(
            {"success": True, "ledger_path": "/x/ledger.json", "n_segments": True, "missing_segments": [], "stale_segments": []},
            False, id="bool-is-not-a-plain-int-n-segments",
        ),
    ],
)
def test_ledger_merge_succeeded_mirror_predicate(raw, expected):
    assert ledger_merge_succeeded(raw) is expected


# ---------------------------------------------------------------------------
# REQUIRED bridge between the real JS guard and the Python mirror above: the
# same key-set literals must exist in the template under these exact names,
# and must match this file's own mirror predicate key sets.
#
# This check used to pytest.skip() when a const was missing, on the rationale
# that "CONTRACT section 5's guard may be implemented with inline literals
# instead". That hedge is stale:
# test_every_consume_site_guard_routes_through_the_shared_evidence_helper
# below now REQUIRES every consume-site guard to route through
# hasFailureEvidence(), so the inline-literal alternative it allowed for no
# longer exists.
#
# Skipping was also the wrong failure mode. mass_translate_driver_smoke.
# test.py is skipif(NODE is None) for the whole file and ledger_update.
# test.py's harness gates on node as well, so the Python mirror predicates
# above are the NODE-FREE layer -- on a machine without node they are the
# only thing testing this guard's logic at all. A bridge that skips rather
# than fails lets such a machine run the suite fully green while the shipped
# JS has drifted arbitrarily from the mirror that is supposedly testing it.
# A missing or renamed const is therefore a failure, not a skip.
#
# LEDGER_WRITE_SUCCESS_KEYS/LEDGER_MERGE_SUCCESS_KEYS/FAILURE_EVIDENCE_KEYS
# are declared as ARRAY literals (`const NAME = [...]`), not object literals --
# the imported extract_const_object_literal/_find_balanced_brace_span pair
# is scoped to `{...}` object literals only (it asserts the character right
# after `=` is `{`), so it cannot extract these. A small local
# bracket-balanced counterpart (same string/comment-aware technique as
# _find_balanced_brace_span, just counting `[`/`]` instead of `{`/`}`)
# extracts the array TEXT; the array is then still parsed with the SAME
# imported parse_js_object_literal (its underlying _Parser dispatches on a
# leading `[` to _parse_array() -- it is not actually object-literal-only,
# despite the function's name), so no second parser is introduced.
# ---------------------------------------------------------------------------

def _find_balanced_bracket_span(source: str, start: int) -> int:
    assert source[start] == "[", f"expected '[' at offset {start}, found {source[start]!r}"
    depth = 0
    i = start
    n = len(source)
    while i < n:
        c = source[i]
        if c == '"':
            i += 1
            while i < n and source[i] != '"':
                if source[i] == "\\":
                    i += 1
                i += 1
            i += 1  # skip the closing quote
            continue
        if c == "/" and i + 1 < n and source[i + 1] == "/":
            newline = source.find("\n", i)
            i = newline if newline != -1 else n
            continue
        if c == "[":
            depth += 1
        elif c == "]":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    raise AssertionError(f"unbalanced brackets starting at offset {start}")


def extract_const_array_literal(source: str, const_name: str) -> str:
    # Projection-anchored for the same reason as extract_const_object_literal
    # above: a commented-out `const NAME = [...]` used to win this search.
    source = _from_declaration(source, f"const {const_name} ")
    m = re.search(r"const\s+" + re.escape(const_name) + r"\s*=\s*", source)
    if not m:
        raise AssertionError(f"expected 'const {const_name} = [ ... ];' declaration, found none")
    start = m.end()
    if start >= len(source) or source[start] != "[":
        raise AssertionError(
            f"const {const_name} is not assigned an array literal (expected "
            f"'[' immediately after '=')"
        )
    end = _find_balanced_bracket_span(source, start)
    return source[start:end]


def assert_js_guard_key_sets_match_python_mirror(mass_translate_source: str) -> None:
    """Factored out so the RED-proof test below can run the bridge against a
    MUTATED copy of the template source -- same shape as the other
    assert_*() helpers in this file.

    extract_const_array_literal() raises AssertionError naming the missing
    const, which is exactly the failure wanted here -- no try/except."""
    write_keys_literal = extract_const_array_literal(mass_translate_source, "LEDGER_WRITE_SUCCESS_KEYS")
    merge_keys_literal = extract_const_array_literal(mass_translate_source, "LEDGER_MERGE_SUCCESS_KEYS")
    failure_evidence_literal = extract_const_array_literal(mass_translate_source, "FAILURE_EVIDENCE_KEYS")

    js_write_keys = set(parse_js_object_literal(write_keys_literal))
    js_merge_keys = set(parse_js_object_literal(merge_keys_literal))
    js_failure_evidence_keys = set(parse_js_object_literal(failure_evidence_literal))

    assert js_write_keys == LEDGER_WRITE_SUCCESS_KEYS, (
        f"JS LEDGER_WRITE_SUCCESS_KEYS {sorted(js_write_keys)} does not match "
        f"this file's own Python mirror {sorted(LEDGER_WRITE_SUCCESS_KEYS)}"
    )
    assert js_merge_keys == LEDGER_MERGE_SUCCESS_KEYS, (
        f"JS LEDGER_MERGE_SUCCESS_KEYS {sorted(js_merge_keys)} does not match "
        f"this file's own Python mirror {sorted(LEDGER_MERGE_SUCCESS_KEYS)}"
    )
    assert js_failure_evidence_keys == FAILURE_EVIDENCE_KEYS, (
        f"JS FAILURE_EVIDENCE_KEYS {sorted(js_failure_evidence_keys)} does not "
        f"match this file's own Python mirror {sorted(FAILURE_EVIDENCE_KEYS)}"
    )


def test_js_guard_key_set_literals_match_python_mirror(mass_translate_source):
    assert_js_guard_key_sets_match_python_mirror(mass_translate_source)


@pytest.mark.parametrize(
    "const_name",
    ["LEDGER_WRITE_SUCCESS_KEYS", "LEDGER_MERGE_SUCCESS_KEYS", "FAILURE_EVIDENCE_KEYS"],
)
def test_missing_key_set_const_fails_the_bridge_rather_than_skipping_it(
    mass_translate_source, const_name,
):
    """RED proof that the bridge above no longer has an escape hatch. A
    reintroduced pytest.skip() raises Skipped, not AssertionError, so this
    test fails if the hatch comes back -- which matters because on a machine
    without node these mirror predicates are the only layer testing the
    guard's logic at all."""
    mutated = mass_translate_source.replace(
        f"const {const_name} =", f"const {const_name}_RENAMED_BY_TEST =", 1
    )
    assert mutated != mass_translate_source, f"could not rename const {const_name}"
    # The message now comes from the projection-anchored lookup rather than
    # from the array extractor, so match on the const NAME rather than on the
    # `= [ ... ];` phrasing -- what must be pinned is that the failure names
    # the const that went missing, not which layer noticed.
    with pytest.raises(AssertionError, match=f"const {const_name}"):
        assert_js_guard_key_sets_match_python_mirror(mutated)


# ---------------------------------------------------------------------------
# #289 CLASS LOCK -- a consume-site guard must judge failure EVIDENCE, never
# key PRESENCE.
#
# #289 was found three times in one file (ledgerWriteSucceeded,
# ledgerMergeSucceeded, artifactCheckMatched): a key-PRESENCE test standing
# in for a failure-EVIDENCE test on a field the flat schema itself advertises
# as fillable. Patching the three sites is rung 3; this is rung 4.
#
# TWO STATIC ATTEMPTS AT RUNG 4 FAILED, AND THE REASON IS THE SAME BOTH TIMES.
# The first enumerated the SPELLINGS a presence test can take, searching for
# `"<field>" in <obj>`; it missed #289 itself, because two of the three sites
# tested a LOOP VARIABLE (`FAILURE_ONLY_KEYS.some((k) => k in raw)`). The
# second inverted that into "the `in` operator may appear only inside
# hasFailureEvidence()", which needed a JS lexer to tell code from the
# template's very large prose layer -- and the lexer's own division-vs-regex
# heuristic could be tricked into blanking real code:
#
#     let z = "x" /FAILURE_EVIDENCE_KEYS.some(k => k in raw)/ 1;
#
# reads as a regex literal to that heuristic, so a fully present presence
# test disappeared and the lock passed. The lexer existed to avoid false
# REDs, and it bought them by creating false GREENs. For a safety lock that
# is the wrong trade in the wrong direction: an over-firing lock is loud and
# gets fixed, an under-firing one is silent exactly when it matters.
#
# Neither could see the other shape a static check cannot reach -- a guard
# that calls hasFailureEvidence() and DISCARDS the verdict. Textually it
# routes through the helper; behaviourally it judges nothing.
#
# So rung 4 is no longer a reader of the template. It is a pair:
#
#   1. A dumb raw-source TRIPWIRE (no lexing): the functions that consult
#      hasOnlyKeys() must be exactly the roster in CONSUME_SITE_GUARDS. It
#      cannot tell a call from a mention in a comment, and that is accepted
#      -- a mention produces a false RED, which is loud and is fixed by
#      amending the roster on purpose.
#   2. BEHAVIOURAL tests driven by that roster, which EXECUTE each guard on
#      returns it must accept and returns it must reject. A presence test
#      fails the accept side in any spelling, obfuscated or not; a discarded
#      verdict fails the reject side. Nothing is being read, so nothing can
#      be read wrongly.
#
# Adding a consume site therefore cannot be done silently: the tripwire fires
# until the roster names it, and naming it requires stating its accepted and
# rejected returns, which is what makes it tested.
# ---------------------------------------------------------------------------

FLAT_AGENT_FACING_LITERALS = (
    "LEDGER_WRITE_SCHEMA",
    "LEDGER_MERGE_SCHEMA",
    "REVIEW_ARTIFACT_SCHEMA",
    "REVIEW_SCHEMA",
    "DRAFT_PROBE_SCHEMA",
)


def _optional_fields_of(mass_translate_source: str, const_name: str) -> set:
    """A flat literal's declared-but-not-required properties -- exactly the
    fields an agent may volunteer without violating the schema, and so
    exactly the fields a presence test can be fooled by."""
    literal = parse_js_object_literal(
        extract_const_object_literal(mass_translate_source, const_name)
    )
    return set(literal.get("properties", {})) - set(literal.get("required", []))


def _line_of(source: str, offset: int) -> int:
    return source.count("\n", 0, offset) + 1


def _line_text_at(source: str, offset: int) -> str:
    # Sliced between real "\n" boundaries rather than via str.splitlines(),
    # which also splits on U+2028/U+0085 and would then disagree with
    # _line_of()'s "\n"-only count on any template that ever carries one.
    start = source.rfind("\n", 0, offset) + 1
    end = source.find("\n", offset)
    return source[start:end if end != -1 else len(source)].strip()


# Keywords after which a `/` opens a regex literal rather than dividing --
# they all end in a position where an EXPRESSION may begin.
_REGEX_MAY_FOLLOW_KEYWORDS = frozenset({
    "await", "case", "delete", "do", "else", "in", "instanceof", "new", "of",
    "return", "typeof", "void", "yield",
})


def _regex_may_start_at(source: str, offset: int) -> bool:
    """The standard JS `/`-is-a-regex-not-a-division disambiguation: a regex
    literal may begin only where an expression may begin, so anything that
    can close one is read as division, unless it is a keyword after which an
    expression may follow.

    What closes an expression, and why each entry is here: `)`, `]`, `}`; an
    identifier or number; a closing string quote or template backtick (in
    code context an opening one is impossible -- the scanner consumes a
    literal whole, so a quote reached here is always the closing one); and a
    postfix `++`/`--`, distinguished from binary `+`/`-` by the doubled
    character. Every one of those was a demonstrated false-GREEN before it
    was listed: `let z = "x" /KEYS.some(k => k in raw)/ 1;` read as a regex
    literal and blanked a live presence test out of the projection.

    Where the two readings are genuinely ambiguous the tie goes to division,
    which consumes one character, over a regex, which could consume a line."""
    j = offset - 1
    while j >= 0 and source[j] in " \t\r\n":
        j -= 1
    if j < 0:
        return True
    c = source[j]
    if c in ")]}\"'`":
        return False
    if c in "+-" and j > 0 and source[j - 1] == c:
        return False  # postfix ++/--, not binary +/-
    if c.isalnum() or c in "_$":
        k = j
        while k >= 0 and (source[k].isalnum() or source[k] in "_$"):
            k -= 1
        # A reserved word is a legal PROPERTY NAME, and `obj.new` is an
        # ordinary member expression that ends an expression -- so a `/`
        # after it divides. Verified against real node: with `foo = {new: 8}`,
        # `foo.new / 2 / 2` evaluates to 2. Without this check the keyword
        # list below fired on the property name and read the division as a
        # regex start, blanking a live presence test out of the projection
        # for 9 of the 10 reserved words in it.
        dot = k
        while dot >= 0 and source[dot] in " \t\r\n":
            dot -= 1
        if dot >= 0 and source[dot] == ".":
            return False
        return source[k + 1:j + 1] in _REGEX_MAY_FOLLOW_KEYWORDS
    return True


def _quoted_literal_end(source: str, start: int) -> int:
    """Offset just past the closing quote of the `'`/`"` string at `start`. A
    backslash escapes the next character, including a line continuation's
    newline; a bare newline means the literal never closed."""
    quote = source[start]
    j = start + 1
    n = len(source)
    while j < n:
        c = source[j]
        if c == "\\":
            j += 2
            continue
        if c == quote:
            return j + 1
        if c == "\n":
            break
        j += 1
    raise AssertionError(
        f"unterminated {quote} string literal at line {_line_of(source, start)}: "
        f"{source[start:start + 60]!r}"
    )


def _template_chunk_end(source: str, start: int) -> tuple:
    """Scan a run of template-literal TEXT from `start` to whichever comes
    first, and report which: `(offset just past the closing backtick, False)`,
    or `(offset of the `$` opening a `${...}` substitution, True)`. The
    substitution's body is CODE again, so the caller resumes normal scanning
    there and comes back here once the matching `}` closes it -- the template
    really does interpolate (`Unsafe segment id ${JSON.stringify(s)}`), so
    this cannot be simplified into "blank backtick to backtick"."""
    j = start
    n = len(source)
    while j < n:
        c = source[j]
        if c == "\\":
            j += 2
            continue
        if c == "$" and j + 1 < n and source[j + 1] == "{":
            return j, True
        if c == "`":
            return j + 1, False
        j += 1
    raise AssertionError(
        f"unterminated template literal at line {_line_of(source, start)}: "
        f"{source[start:start + 60]!r}"
    )


def _regex_literal_end(source: str, start: int) -> int:
    """Offset just past the closing `/` (plus flags) of the regex literal at
    `start`. `[...]` character classes may contain an unescaped `/`."""
    j = start + 1
    n = len(source)
    in_class = False
    while j < n:
        c = source[j]
        if c == "\\":
            j += 2
            continue
        if c == "\n":
            break
        if c == "[":
            in_class = True
        elif c == "]":
            in_class = False
        elif c == "/" and not in_class:
            j += 1
            while j < n and source[j].isalpha():  # trailing flags
                j += 1
            return j
        j += 1
    raise AssertionError(
        f"unterminated regex literal at line {_line_of(source, start)}: "
        f"{source[start:start + 60]!r}. A `/` that opens no regex is usually a "
        "DIVISION that _regex_may_start_at() mistook for a regex start -- add "
        "whatever closes the expression to its division list. This fails loudly "
        "rather than blanking the rest of the line, which is the safe direction."
    )


def _js_code_only(source: str) -> str:
    """`source` with every comment, string literal, template literal and
    regex literal blanked to spaces -- newlines and every offset preserved,
    so a match found in the result indexes straight back into the real file.

    Deliberately NOT a second copy of review_prompt_schema_drift.test.py's
    literal parser imported at the top of this file: that one is scoped to
    the schema literals' restricted grammar (its token regex accepts only
    `{}[]:,` punctuation and raises on anything else), so it cannot walk a
    whole template. This is the complementary, cruder job -- decide per
    character whether it is code, and blank everything that is not.

    Pinned by test_js_code_only_shapes below, which is the direct test table
    this had no equivalent of when it first shipped."""
    out = list(source)
    n = len(source)

    def blank(start: int, stop: int) -> None:
        for j in range(start, stop):
            if out[j] != "\n":
                out[j] = " "

    def enter_template_text(start: int) -> int:
        """Blank template-literal text from `start`, returning where code
        resumes -- either past the closing backtick or past the `${` whose
        body is code again (in which case the substitution is pushed)."""
        nonlocal depth
        end, is_substitution = _template_chunk_end(source, start)
        if not is_substitution:
            blank(start, end)
            return end
        blank(start, end + 2)
        substitutions.append(depth)
        depth += 1
        return end + 2

    depth = 0            # brace nesting, counted over CODE only
    substitutions = []   # brace depth at which each open ${...} started
    i = 0
    while i < n:
        c = source[i]
        if c == "/" and source.startswith("//", i):
            end = source.find("\n", i)
            end = n if end == -1 else end
        elif c == "/" and source.startswith("/*", i):
            end = source.find("*/", i + 2)
            if end == -1:
                raise AssertionError(
                    f"unterminated block comment at line {_line_of(source, i)}"
                )
            end += 2
        elif c == "/" and _regex_may_start_at(source, i):
            end = _regex_literal_end(source, i)
        elif c in "\"'":
            end = _quoted_literal_end(source, i)
        elif c == "`":
            blank(i, i + 1)
            i = enter_template_text(i + 1)
            continue
        elif c == "{":
            depth += 1
            i += 1
            continue
        elif c == "}":
            depth -= 1
            if substitutions and depth == substitutions[-1]:
                substitutions.pop()
                # Blank the substitution-closing `}` too: its opening `${` was
                # already blanked, so leaving this brace would put an
                # unbalanced close into the projection that every depth-counting
                # consumer (_call_argument_texts, _function_spans) would cross
                # at depth 0 early. The substitution's INNER code is untouched
                # and stays correctly positioned; only this one brace is blanked
                # (ordinary code-block `}` must survive for depth counting).
                blank(i, i + 1)
                i = enter_template_text(i + 1)  # back into the literal's text
            else:
                i += 1
            continue
        else:
            i += 1
            continue
        blank(i, end)
        i = end
    return "".join(out)


def _in_operator_sites(mass_translate_source: str) -> list:
    """(offset, line number, source line) for every JS `in` operator in the
    template's code."""
    code = _js_code_only(mass_translate_source)
    return [
        (m.start(), _line_of(mass_translate_source, m.start()),
         _line_text_at(mass_translate_source, m.start()))
        for m in re.finditer(r"\bin\b", code)
    ]


def _function_body_span(mass_translate_source: str, name: str) -> tuple:
    """(start, end) offsets of `function <name>(...) { ... }`, terminated at
    the first column-0 `}` in the CODE PROJECTION -- never in raw source,
    where an unindented `}` inside prompt text would truncate the body."""
    code = _js_code_only(mass_translate_source)
    start = code.find(f"function {name}(")
    assert start != -1, (
        f"the template no longer declares function {name}() -- the #289 class "
        "lock is anchored on it. If the helper was renamed or removed, "
        "re-anchor the lock deliberately rather than letting it disappear."
    )
    return start, code.index("\n}", start)


def assert_in_operator_confined_to_evidence_helper(mass_translate_source: str) -> None:
    """The #289 class lock itself, factored out so the RED-proof tests below
    can run it against a MUTATED copy of the template source without ever
    touching the shipped file."""
    sites = _in_operator_sites(mass_translate_source)
    helper_start, helper_end = _function_body_span(mass_translate_source, "hasFailureEvidence")

    # Non-vacuity first: a scanner that blanked too much -- a misclassified
    # division swallowing code up to the next `/`, say -- would find nothing
    # anywhere and let the real assertion below pass for the wrong reason.
    # hasFailureEvidence()'s own `k in raw` is the positive control.
    assert [ln for off, ln, _ in sites if helper_start <= off < helper_end], (
        "no `in` operator was found inside hasFailureEvidence() -- either the "
        "helper stopped using one (and this lock is now measuring nothing) or "
        f"_js_code_only() is blanking real code. Sites found: {sites}"
    )

    offenders = [
        (ln, text) for off, ln, text in sites
        if not (helper_start <= off < helper_end)
    ]
    assert not offenders, (
        "#289 class regression -- the JS `in` operator appears outside "
        "hasFailureEvidence(). A key-PRESENCE test cannot stand in for a "
        "failure-EVIDENCE test here: the flat schemas advertise their optional "
        "fields as fillable on a SUCCESS return, so an honest agent volunteers "
        "them (`exit_code: 0`) and a presence test fails good work "
        "non-deterministically. Route the field through hasFailureEvidence()/"
        "NO_FAILURE_EVIDENCE instead -- a table row, not a fourth predicate. A "
        "deliberate non-presence use (a `for...in` loop) trips this lock too, "
        "by design: amend the lock in the same commit and say why. Offenders "
        f"(line, source): {offenders}"
    )


@pytest.mark.parametrize(
    "snippet,in_survives",
    [
        # --- blanked: `in` here is prose or pattern text, never an operator
        pytest.param("/* in a block comment */\nlet a = 1;", False, id="block-comment"),
        pytest.param("// in a line comment\nlet a = 1;", False, id="line-comment"),
        pytest.param('let s = "a string saying in";', False, id="string-literal"),
        pytest.param("let s = `template text saying in`;", False, id="template-text"),
        pytest.param("let s = `head ${x} tail saying in`;", False,
                     id="template-text-resuming-after-a-substitution"),
        pytest.param("let s = `it's in text`;", False,
                     id="apostrophe-inside-template-text"),
        pytest.param("const re = /^(in|out)$/;", False, id="in-inside-a-regex-literal"),
        # --- survives: a real `in` operator, however it is reached
        pytest.param("let s = `${k in raw}`;", True,
                     id="in-inside-a-substitution-body"),
        pytest.param("for (const k in raw) { }", True, id="for-in-loop"),
        pytest.param("const q = raw.n / 2;\nif (k in raw) { }", True,
                     id="presence-test-after-a-real-division"),
        # The three false-GREENs the division heuristic used to have: a `/`
        # after a closing quote, a closing backtick, or a postfix increment
        # was read as opening a regex literal, blanking everything to the
        # next `/` -- including a live presence test.
        pytest.param('let z = "x" /k in raw/ 1;', True, id="fake-regex-after-string-quote"),
        pytest.param("let z = `x` /k in raw/ 1;", True, id="fake-regex-after-template-backtick"),
        pytest.param("let w = 1; w++ /k in raw/ 1;", True, id="fake-regex-after-postfix-increment"),
        # A reserved word is a legal property name; `obj.new` ends an
        # expression, so the `/` after it divides. 9 of the 10 words in
        # _REGEX_MAY_FOLLOW_KEYWORDS blanked a live presence test here.
        pytest.param("let z = foo.new /k in raw/ 1;", True,
                     id="fake-regex-after-reserved-word-property"),
        pytest.param("let z = foo.typeof /k in raw/ 1;", True,
                     id="fake-regex-after-reserved-word-property-typeof"),
        # ...but the keyword path itself must still work when the word is NOT
        # a property name, or the fix above would have traded one hole for
        # another: this really is a regex literal and must blank.
        pytest.param("return /a in b/.test(x);", False,
                     id="real-regex-after-a-genuine-keyword"),
        pytest.param("let r = typeof /a in b/;", False,
                     id="real-regex-after-typeof"),
    ],
)
def test_js_code_only_shapes(snippet, in_survives):
    """The direct test table _js_code_only() shipped without. It was written
    to fit this one template, which is exactly the argument
    _object_literal_key_names() makes for having its own table -- the same
    argument applies here, and with more at stake: every static lock in this
    file reads the projection this produces, so a shape it blanks wrongly is
    a silent false GREEN in all of them at once.

    Offsets and newlines must survive too, or the line numbers every failure
    message reports would be lies."""
    code = _js_code_only(snippet)
    assert len(code) == len(snippet), "offsets were not preserved"
    assert code.count("\n") == snippet.count("\n"), "newlines were not preserved"
    assert bool(re.search(r"\bin\b", code)) is in_survives, (
        f"projection of {snippet!r} was {code!r}"
    )


def test_js_code_only_blanks_a_template_substitution_closing_brace():
    """A template substitution's opening `${` is blanked, but its matching
    `}` used to survive into the code projection. That left every consumer
    that depth-counts over the projection -- _call_argument_texts and
    _function_spans -- one unbalanced close brace that crosses depth 0 early
    (so a call's argument split closes at the stray `}` before reaching a
    later argument). Blank the substitution close too: the substitution's
    INNER code still survives at its real offset, the projection is
    brace-balanced, and every offset is preserved (or the line numbers every
    failure reports would lie)."""
    src = "const raw = await agent(`seg ${seg} now`, { schema: S });"
    code = _js_code_only(src)
    assert len(code) == len(src), "offsets were not preserved"
    # The substitution's inner code survives, still at its real offset.
    inner = src.index("seg}")
    assert code[inner:inner + 3] == "seg", (
        f"substitution inner code did not survive: {code!r}"
    )
    # Brace projection is balanced AND never crosses depth 0 early -- exactly
    # what _call_argument_texts / _function_spans depth-count on.
    depth = lowest = 0
    for ch in code:
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            lowest = min(lowest, depth)
    assert depth == 0, f"unbalanced braces in projection (net {depth}): {code!r}"
    assert lowest == 0, (
        f"projection crossed depth 0 early -- a stray substitution close brace "
        f"survived (lowest {lowest}): {code!r}"
    )


@pytest.mark.parametrize(
    "const_name,decoy,real_marker",
    [
        pytest.param(
            "FAILURE_EVIDENCE_KEYS",
            '// historical shape: const FAILURE_EVIDENCE_KEYS = ["error"];',
            "exit_code", id="array-literal",
        ),
        pytest.param(
            "NO_FAILURE_EVIDENCE",
            "// old: const NO_FAILURE_EVIDENCE = { error: isEmptyString };",
            "exit_code", id="object-literal",
        ),
    ],
)
def test_extractors_ignore_a_commented_out_declaration(
    mass_translate_source, const_name, decoy, real_marker,
):
    """Every shared JS extractor scans balanced and comment-aware but used to
    LOCATE with a bare raw index, so a commented-out declaration of the same
    name won and the checks built on it silently read the decoy. Pinned for
    both literal shapes."""
    anchor = f"const {const_name} "
    assert anchor in mass_translate_source, f"anchor {anchor!r} has moved"
    mutated = mass_translate_source.replace(anchor, decoy + "\n" + anchor, 1)
    assert mutated != mass_translate_source, "the decoy mutation did not apply"

    if const_name == "NO_FAILURE_EVIDENCE":
        extracted = extract_const_object_literal(mutated, const_name)
    else:
        extracted = extract_const_array_literal(mutated, const_name)
    assert real_marker in extracted, (
        f"extraction of {const_name} returned the commented-out decoy rather "
        f"than the real declaration: {extracted!r}"
    )
    # And the harness's own statement extractor, which the behavioural layer
    # splices into the node script it executes.
    assert real_marker in _extract_const_statement(mutated, const_name)


def test_flat_literals_still_declare_the_fields_289_fired_on_as_optional(mass_translate_source):
    """The lock is spelling-agnostic, but the reason the class exists at all
    is that these fields are OPTIONAL in the flat agent-facing schemas --
    declared, so an agent may volunteer one, yet not required, so its mere
    presence says nothing about which branch the return is. Derived from the
    literals themselves, never hand-typed, so a NEW flat schema is covered the
    day it lands. If a future build made one of them required, presence would
    become a legitimate test for it and the lock's rationale would need
    re-reading rather than silently continuing to hold."""
    optional_fields = set()
    for const_name in FLAT_AGENT_FACING_LITERALS:
        optional_fields |= _optional_fields_of(mass_translate_source, const_name)
    assert {"exit_code", "mismatch_detail"} <= optional_fields, (
        "the two fields #289 actually fired on must be among the flat "
        f"literals' derived optional fields, got {sorted(optional_fields)}"
    )


# Top-level declaration forms _function_spans() can find and delimit. The
# arrow-const form is not used by the template today; it is recognised anyway
# so that introducing one does not silently drop the function out of the
# routing lock's view.
_DECLARATION_FORMS = (
    r"^(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(",
    r"^const\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?\([^)]*\)\s*=>\s*\{",
)

# Statement keywords whose `keyword (...) {` line looks exactly like an
# object-literal method declaration. Excluded by name, not by heuristic.
_STATEMENT_KEYWORDS = frozenset({
    "catch", "do", "else", "for", "function", "if", "return", "switch",
    "try", "while", "with",
})


def _function_spans(mass_translate_source: str) -> dict:
    """{name: (start, end)} for every top-level declaration, over the CODE
    PROJECTION at BOTH ends. The terminator is looked up in the projection
    rather than in raw source specifically so that an unindented `}` sitting
    inside prompt text or a comment cannot end a span early and hide the rest
    of a function body from every check built on this."""
    code = _js_code_only(mass_translate_source)
    spans = {}
    for pattern in _DECLARATION_FORMS:
        for m in re.finditer(pattern, code, re.M):
            spans[m.group(1)] = (m.start(), code.index("\n}", m.start()))
    return spans


def _unspannable_declaration_sites(mass_translate_source: str) -> list:
    """Object-literal method declarations (`const probes = { name(x) { ... } }`)
    -- a function this file's span machinery cannot delimit, and therefore a
    consume site the routing lock below would not see.

    Reported so it can FAIL LOUDLY rather than be silently skipped. Spanning
    the idiom correctly is more machinery than a currently-unused form
    deserves; noticing that it arrived is nearly free, and converts a silent
    blind spot into an instruction to extend _DECLARATION_FORMS in the same
    commit."""
    code = _js_code_only(mass_translate_source)
    return [
        (m.group(1), _line_of(mass_translate_source, m.start()))
        for m in re.finditer(r"^[ \t]+([A-Za-z_$][\w$]*)\s*\([^()]*\)\s*\{[ \t]*$",
                             code, re.M)
        if m.group(1) not in _STATEMENT_KEYWORDS
    ]


def _hasonlykeys_callers(mass_translate_source: str) -> dict:
    """{function name: declaration line} for every function whose body calls
    hasOnlyKeys() -- read off the code projection, so a mention in a comment
    or in prompt text is not counted.

    This is the CONVENTION signal for "consume site": a function is one if it
    performs the allowed-key discipline. Its limit -- a guard that skips the
    convention entirely is invisible to it -- is why the structural signal
    below exists alongside it, not instead of it."""
    code = _js_code_only(mass_translate_source)
    return {
        name: _line_of(mass_translate_source, start)
        for name, (start, end) in _function_spans(mass_translate_source).items()
        if name != "hasOnlyKeys" and "hasOnlyKeys(" in code[start:end]
    }


def _evidence_carrying_schemas(mass_translate_source: str) -> set:
    """Flat schema const names whose OPTIONAL properties include at least one
    failure-evidence key -- i.e. an agent could volunteer an evidence field on
    a success return, which is exactly the #289 hazard. A dispatch validated
    by such a schema produces a value that MUST be judged for failure
    evidence. Schemas with no optional evidence field (DRAFT_PROBE_SCHEMA's
    lone required `present`, REVIEW_SCHEMA's all-required set) carry no such
    hazard and are correctly excluded."""
    evidence_keys = set()
    # _evidence_key_consts_passed_to_helper already excludes the helper's own
    # declaration and rejects inline-literal argument shapes -- reuse it
    # rather than re-deriving the call-site scan (and its `function ` guard)
    # a second time here.
    for const_name in _evidence_key_consts_passed_to_helper(mass_translate_source):
        evidence_keys |= set(parse_js_object_literal(
            extract_const_array_literal(mass_translate_source, const_name)
        ))
    assert evidence_keys, "no evidence keys resolved from hasFailureEvidence() call sites"
    carrying = set()
    for const_name in FLAT_AGENT_FACING_LITERALS:
        if _optional_fields_of(mass_translate_source, const_name) & evidence_keys:
            carrying.add(const_name)
    return carrying


def _call_argument_texts(code: str, open_paren: int) -> list:
    """The comma-separated argument texts of the call whose `(` is at
    `open_paren`, split at depth 1 only. Runs on the CODE PROJECTION, where
    strings and comments are already blanked, so a comma inside either cannot
    split an argument."""
    depth = 0
    args = []
    start = open_paren + 1
    i = open_paren
    n = len(code)
    while i < n:
        c = code[i]
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
            if depth == 0:
                args.append(code[start:i])
                return args
        elif c == "," and depth == 1:
            args.append(code[start:i])
            start = i + 1
        i += 1
    raise AssertionError(f"unbalanced call parentheses at offset {open_paren}")


def _agent_dispatch_consume_sites(mass_translate_source: str) -> dict:
    """{function name: line} for every locally-defined function that is handed,
    AS ITS WHOLE ARGUMENT, a variable bound in the same scope to an
    `await agent(..., { schema: <evidence-carrying> })` dispatch.

    This is the STRUCTURAL signal for "consume site", and it does not depend
    on the guard following any convention: a function is one by virtue of
    WHAT IT RECEIVES -- an untrusted, evidence-carrying agent return -- not by
    which helper it happens to call. A guard reimplementing #289's shape
    without hasOnlyKeys (gating on `.success` and rejecting via
    `!== undefined`) is on the roster the moment it is fed such a dispatch,
    and the behavioural tests then execute it.

    Precisely scoped to stay decidable and false-positive-free, verified
    against the real template:
      * `schema:` gates it. `parseDisp(raw)` is handed the translate-drive
        return, but that dispatch carries NO schema (it returns the bare
        `DISPATCHED <seg> <disp>` string), so parseDisp -- a parser, not a
        success guard -- is not mistaken for one.
      * evidence-carrying gates it further. The draft-probe dispatch has a
        schema but no optional evidence field, so its inline `raw.present`
        check is not dragged in.
      * WHOLE-argument gates it. `endsWithSegJson(raw.fragment_path, seg)`
        receives a field of a validated return, a post-validation use, not
        the untrusted object -- so it is not a consume site.

    What it deliberately does NOT reach, and why this is a signal beside the
    convention one rather than a replacement for it: a return that flows to
    its guard INDIRECTLY -- `artifactCheckMatched(first.art)`, where the value
    passes agent() -> callArtifactCheck's return -> readAndCheck's `{art}`
    object field -> `first.art`. Following that needs interprocedural,
    field-sensitive taint analysis; approximating it cheaply would reintroduce
    the silent-blind-spot risk this release exists to remove. artifactCheckMatched
    is caught today by the convention signal, so the union has no live gap;
    the residual is a hypothetical future guard that is BOTH indirect-fed AND
    convention-skipping. Stated in assert_every_consume_site_guard_routes_
    through_helper()'s docstring.

    Binding keyword: `const` ONLY, by design -- not merely conservatively. This
    is a second, sibling residual. const-immutability is exactly what makes the
    whole-span `GUARD(name)` scan sound: a const-bound name provably still holds
    the dispatch's evidence-carrying return at every call site in the enclosing
    function, so any `GUARD(name)` genuinely received it. A `let`/`var`-bound
    name can be REASSIGNED to a derived value before a downstream `GUARD(name)`,
    so binding on `let`/`var` here would FALSELY roster a benign guard that never
    saw the untrusted return. Supporting them soundly needs reassignment-
    tracking, not warranted for a shape no live dispatch uses -- all real
    dispatch sites bind with `const`. So a `let`/`var`-bound dispatch feeding a
    convention-skipping, presence-test-free guard is a known, narrow residual, a
    sibling of the indirect-fed one above. Pinned by
    test_structural_signal_is_const_only_by_design."""
    code = _js_code_only(mass_translate_source)
    evidence_schemas = _evidence_carrying_schemas(mass_translate_source)
    spans = _function_spans(mass_translate_source)

    def enclosing(offset: int) -> tuple:
        best = None
        for name, (start, end) in spans.items():
            if start <= offset < end and (best is None or start > spans[best][0]):
                best = name
        # A dispatch outside every function span is module top-level (the
        # final mergeLedger call); its guard call is at top level too.
        return (best, spans[best]) if best is not None else (None, (0, len(code)))

    consume_sites = {}
    for m in re.finditer(r"const\s+([A-Za-z_$][\w$]*)\s*=\s*await\s+agent\s*\(", code):
        # The regex ends in `agent\s*\(`, so m.end()-1 is already the `(`; the
        # `(` here is the call's open paren. Binds on `const` ONLY, by design --
        # see this function's docstring: const-immutability makes the whole-span
        # GUARD(name) scan sound; let/var would need reassignment-tracking.
        args = _call_argument_texts(code, m.end() - 1)
        options = args[1] if len(args) >= 2 else ""
        schema = re.search(r"\bschema:\s*([A-Za-z_$][\w$]*)", options)
        if not schema or schema.group(1) not in evidence_schemas:
            continue
        var = m.group(1)
        _name, (start, end) = enclosing(m.start())
        # Whole-var argument only: `GUARD(var)`, optionally negated as
        # `!GUARD(var)`. `GUARD(var.field)` is excluded by the `\)` anchor.
        for call in re.finditer(r"([A-Za-z_$][\w$]*)\s*\(\s*" + re.escape(var) + r"\s*\)",
                                code[start:end]):
            fn = call.group(1)
            if fn in spans and fn != "agent":
                consume_sites.setdefault(fn, _line_of(mass_translate_source, start + call.start()))
    return consume_sites


def _derive_consume_sites(mass_translate_source: str) -> dict:
    """The roster the tripwire pins: the UNION of the convention signal
    (hasOnlyKeys callers) and the structural signal (functions fed an
    evidence-carrying agent dispatch). A function found by EITHER is a consume
    site; to escape the roster a guard must now evade BOTH -- skip the
    allowed-key discipline AND be fed only through indirection."""
    sites = dict(_hasonlykeys_callers(mass_translate_source))
    for name, line in _agent_dispatch_consume_sites(mass_translate_source).items():
        sites.setdefault(name, line)
    return sites


def assert_every_consume_site_guard_routes_through_helper(mass_translate_source: str) -> None:
    """The `in`-lock above is satisfiable by DELETING a check outright -- a
    guard with no evidence test contains no `in` to find. This is the other
    half of that pair: every consume site must still consult the shared
    judgement. Neither half subsumes the other, and neither is redundant.

    TWO blind spots, not one. Both are stated here because an earlier version
    of this docstring claimed there was only the first, and a reviewer found
    the second:

      1. A guard that CALLS hasFailureEvidence() and discards the verdict
         routes through the helper textually while judging nothing. Both
         static halves miss it. Detecting it statically means data flow, and
         approximating it by pattern (`if (hasFailureEvidence(`) is spelling
         enumeration, which is the exact failure this lock has already had
         twice. It is covered by EXECUTION instead --
         test_rostered_guard_rejects_every_evidence_bearing_return is the
         only check here that catches it, and it is node-gated.

      2. A guard that never calls hasOnlyKeys() is invisible to THIS lock (it
         iterates hasOnlyKeys callers) and to the `in`-lock if it also has no
         presence test. It is NOT, however, invisible to the roster tripwire:
         the tripwire derives its roster from the UNION of the convention
         signal (hasOnlyKeys callers) and a STRUCTURAL signal
         (_agent_dispatch_consume_sites -- functions handed an
         evidence-carrying agent() return, decided at the dispatch site, not
         by which helper the function calls). A guard reimplementing #289 with
         `.success` + `!== undefined` and no hasOnlyKeys is rostered the
         moment it is fed such a dispatch, and the behavioural tests then
         execute it and watch it reject a benign `exit_code: 0`. That closes
         the shape a reviewer demonstrated here.

    The residual, stated exactly rather than softened -- this is the release's
    subject. The structural signal reaches only DIRECT dispatch feeds
    (`const raw = await agent(..., {schema}); guard(raw)`). A return that
    reaches its guard through INDIRECTION -- agent() -> a wrapper's return ->
    an object field -> a property access, as artifactCheckMatched(first.art)
    does -- is not followed, because doing so soundly needs interprocedural,
    field-sensitive taint analysis, and approximating it cheaply is exactly
    the silent-blind-spot trade this release removed. So the uncovered set is
    precisely: a FUTURE guard that is BOTH indirect-fed AND convention-skipping
    AND presence-test-free. The live template has no such guard --
    artifactCheckMatched is indirect-fed but uses hasOnlyKeys, so the
    convention signal covers it. Verified by constructing the escape:
    test_apparatus_residual_is_exactly_indirect_plus_convention_skipping."""
    unspannable = _unspannable_declaration_sites(mass_translate_source)
    assert not unspannable, (
        "the template now declares a function in an object-method form this "
        "file cannot span, so the routing lock below cannot see whether it is "
        "a consume site. Add the form to _DECLARATION_FORMS in this same "
        f"commit. Sites (name, line): {unspannable}"
    )

    spans = _function_spans(mass_translate_source)
    code = _js_code_only(mass_translate_source)
    for guard in ("ledgerWriteSucceeded", "ledgerMergeSucceeded", "artifactCheckMatched"):
        assert guard in spans, (
            f"{guard}() is no longer declared in the template -- if a "
            "consume-site guard was renamed or removed, re-anchor this floor "
            "deliberately rather than dropping the name."
        )
        start, end = spans[guard]
        assert "hasFailureEvidence(" in code[start:end], (
            f"{guard}() no longer calls hasFailureEvidence() -- the #289 "
            "class lock above would then pass vacuously, with the guard "
            "having no failure-evidence check at all."
        )

    unjudged = sorted(
        name for name, (start, end) in spans.items()
        if name != "hasOnlyKeys"
        and "hasOnlyKeys(" in code[start:end]
        and "hasFailureEvidence(" not in code[start:end]
    )
    assert not unjudged, (
        "unjudged consume site -- these functions check an agent return's "
        "allowed KEY SET but never its failure EVIDENCE, which is #289's "
        "shape exactly: a return carrying `exit_code: 3` or a non-empty "
        "stderr would be accepted as success. Route them through "
        f"hasFailureEvidence(): {unjudged}"
    )


def test_every_consume_site_guard_routes_through_the_shared_evidence_helper(mass_translate_source):
    assert_every_consume_site_guard_routes_through_helper(mass_translate_source)


def test_no_key_presence_test_exists_outside_the_shared_evidence_helper(mass_translate_source):
    assert_in_operator_confined_to_evidence_helper(mass_translate_source)


# Every mutant below is spliced into ledgerWriteSucceeded() -- code context,
# outside hasFailureEvidence(). The shipped template is never modified.
_GUARD_BODY_ANCHOR = "function ledgerWriteSucceeded(raw) {\n"


def _splice_into_guard(mass_translate_source: str, line: str) -> str:
    """Insert `line` at the top of ledgerWriteSucceeded()'s body, asserting
    the anchor still exists. Every RED proof in this file goes through here
    so none of them can go vacuous by silently not applying."""
    assert _GUARD_BODY_ANCHOR in mass_translate_source, (
        "ledgerWriteSucceeded() no longer opens with the anchored signature "
        "the RED proofs splice into -- re-anchor them, do not delete them"
    )
    mutated = mass_translate_source.replace(
        _GUARD_BODY_ANCHOR, _GUARD_BODY_ANCHOR + line + "\n", 1
    )
    assert mutated != mass_translate_source, "the splice did not apply"
    return mutated


@pytest.mark.parametrize(
    "reintroduced",
    [
        # The shape #289 ACTUALLY had at two of its three sites, and the one
        # the first enumerate-the-spellings form of this lock missed.
        pytest.param(
            "  if (FAILURE_EVIDENCE_KEYS.some((k) => k in raw)) return false;",
            id="loop-variable",
        ),
        pytest.param('  if ("exit_code" in raw) return false;', id="quoted-literal"),
        # #289's third site, the one spelling the old lock did catch -- kept
        # so broadening the check cannot quietly trade one spelling for the other.
        pytest.param('  if (!("mismatch_detail" in raw)) return false;', id="negated-quoted-literal"),
        pytest.param(
            '  const probe = "exit_code";\n  if (probe in raw) return false;',
            id="computed-key",
        ),
        # The demonstrated false-GREEN: a presence test that the
        # division-vs-regex disambiguator used to blank away as a fake regex
        # literal, once after a closing string quote, once after a closing
        # template backtick, once after a postfix increment.
        pytest.param(
            '  let z = "x" /FAILURE_EVIDENCE_KEYS.some(k => k in raw)/ 1;',
            id="fake-regex-after-string-quote",
        ),
        pytest.param(
            "  let z = `x` /FAILURE_EVIDENCE_KEYS.some(k => k in raw)/ 1;",
            id="fake-regex-after-template-backtick",
        ),
        pytest.param(
            "  let w = 1; w++ /FAILURE_EVIDENCE_KEYS.some(k => k in raw)/ 1;",
            id="fake-regex-after-postfix-increment",
        ),
        pytest.param(
            "  if (foo.new /FAILURE_EVIDENCE_KEYS.some(k => k in raw)/ 1) return false;",
            id="fake-regex-after-reserved-word-property",
        ),
    ],
)
def test_presence_test_lock_catches_a_reintroduction_in_any_spelling(
    mass_translate_source, reintroduced,
):
    with pytest.raises(AssertionError, match="#289 class regression"):
        assert_in_operator_confined_to_evidence_helper(
            _splice_into_guard(mass_translate_source, reintroduced)
        )


@pytest.mark.parametrize(
    "carrier",
    [
        pytest.param(
            '  // a later note recalling that `"exit_code" in raw` was the #289 bug',
            id="line-comment",
        ),
        pytest.param(
            '  lines.push("Say whether exit_code in raw was set on the return.");',
            id="prompt-string",
        ),
        pytest.param(
            "  const re = /^(in|out)$/;",
            id="in-inside-a-regex-literal",
        ),
        pytest.param(
            "  const msg = `checked ${raw.status} in place`;",
            id="in-inside-template-text",
        ),
    ],
)
def test_presence_test_lock_ignores_the_idiom_inside_a_comment_or_a_prompt_string(
    mass_translate_source, carrier,
):
    """The other half of the RED proof: the lock reads CODE, not the file. A
    check that fired on the raw source would be unusable against a template
    this comment- and prose-heavy (49 `in` occurrences in raw source, 1 in
    the projection), and would tempt the next author to loosen it back into a
    spelling blacklist. The splice is asserted to have landed, so a moved
    anchor fails this loudly instead of passing it vacuously."""
    assert_in_operator_confined_to_evidence_helper(
        _splice_into_guard(mass_translate_source, carrier)
    )


@pytest.mark.parametrize(
    "declaration",
    [
        pytest.param(
            "function draftProbeMatched(probe) {\n"
            "  if (!probe || probe.ok !== true) return false;\n"
            "  return hasOnlyKeys(probe, REVIEW_ARTIFACT_ALLOWED_KEYS);\n"
            "}\n",
            id="function-declaration",
        ),
        # The arrow-const form the span machinery used to miss entirely: it
        # matched only `function NAME(`, so a guard written this way was
        # invisible to the routing lock AND contained no `in` for the lock
        # above to find. Both halves missed it; now the span forms cover it.
        pytest.param(
            "const draftProbeMatched = (probe) => {\n"
            "  if (!probe || probe.ok !== true) return false;\n"
            "  return hasOnlyKeys(probe, REVIEW_ARTIFACT_ALLOWED_KEYS);\n"
            "};\n",
            id="arrow-const-declaration",
        ),
    ],
)
def test_routing_lock_catches_an_unjudged_consume_site(mass_translate_source, declaration):
    """RED proof: a fourth guard that checks its allowed KEY SET but never
    failure EVIDENCE -- #289's shape, at a site no name list knows about."""
    unjudged = mass_translate_source.replace(
        "function artifactCheckMatched(art) {",
        declaration + "\nfunction artifactCheckMatched(art) {",
        1,
    )
    assert unjudged != mass_translate_source, "the unjudged-guard mutation did not apply"
    with pytest.raises(AssertionError, match="unjudged consume site"):
        assert_every_consume_site_guard_routes_through_helper(unjudged)


def test_routing_lock_refuses_an_unspannable_declaration_form(mass_translate_source):
    """The object-literal method form is NOT spanned. Rather than let such a
    guard slip past the routing lock unnoticed, its arrival is a loud failure
    telling the author to extend _DECLARATION_FORMS in the same commit."""
    method_form = mass_translate_source.replace(
        "function artifactCheckMatched(art) {",
        "const probes = {\n"
        "  draftProbeMatched(probe) {\n"
        "    return hasOnlyKeys(probe, REVIEW_ARTIFACT_ALLOWED_KEYS);\n"
        "  },\n"
        "};\n"
        "\n"
        "function artifactCheckMatched(art) {",
        1,
    )
    assert method_form != mass_translate_source, "the object-method mutation did not apply"
    with pytest.raises(AssertionError, match="object-method form this file cannot span"):
        assert_every_consume_site_guard_routes_through_helper(method_form)


def assert_consume_site_list_is_exhaustive(mass_translate_source: str) -> None:
    """The tripwire. CONSUME_SITE_GUARDS must name exactly the functions the
    two signals derive -- convention (hasOnlyKeys callers) UNION structural
    (fed an evidence-carrying agent dispatch) -- no more, no fewer."""
    found = set(_derive_consume_sites(mass_translate_source))
    declared = set(CONSUME_SITE_GUARDS)
    assert found == declared, (
        "consume-site roster drift -- CONSUME_SITE_GUARDS must name exactly "
        "the functions derived as consume sites (they call hasOnlyKeys(), OR "
        "they are handed an evidence-carrying agent() return), because that "
        "roster is what drives the behavioural tests below. Undeclared (a "
        f"consume site nothing is executing): {sorted(found - declared)}. "
        f"Declared but not found (stale entry, or renamed): {sorted(declared - found)}. "
        "Amend the roster in the same commit as the guard, and give the new "
        "entry its accepted/rejected cases -- that is what makes it testable."
    )


def test_consume_site_roster_is_exhaustive(mass_translate_source):
    assert_consume_site_list_is_exhaustive(mass_translate_source)


# The lead/reviewer's demonstrated escape: a guard that reimplements #289's
# shape (gate on `.success`, reject evidence via `!== undefined`) with NO
# hasOnlyKeys and NO `in`, so it is invisible to the convention roster, the
# routing lock and the `in`-lock alike. Fed a real evidence-carrying dispatch.
_FIXUP_GUARD_DEF = (
    "function fixupApplied(raw) {\n"
    "  if (!raw || raw.success !== true) return false;\n"
    "  if (raw.exit_code !== undefined) return false;\n"
    "  return typeof raw.status === \"string\" && raw.status.length > 0;\n"
    "}\n\n"
)
_FIXUP_FEED_ANCHOR = "  if (!ledgerWriteSucceeded(raw)) {\n"
_FIXUP_FEED = "  if (!fixupApplied(raw)) { return { ok: false, failResult: null }; }\n"


def _splice_unrostered_agent_fed_guard(mass_translate_source: str) -> str:
    """Add fixupApplied, fed the LEDGER_WRITE dispatch's `raw`, without adding
    it to CONSUME_SITE_GUARDS. The shipped template is never modified."""
    assert "function ledgerWriteSucceeded(raw) {" in mass_translate_source
    assert _FIXUP_FEED_ANCHOR in mass_translate_source
    out = mass_translate_source.replace(
        "function ledgerWriteSucceeded(raw) {",
        _FIXUP_GUARD_DEF + "function ledgerWriteSucceeded(raw) {", 1,
    ).replace(_FIXUP_FEED_ANCHOR, _FIXUP_FEED + _FIXUP_FEED_ANCHOR, 1)
    assert out != mass_translate_source, "the fixupApplied splice did not apply"
    return out


def test_structural_signal_catches_an_agent_fed_guard_the_convention_signal_misses(
    mass_translate_source,
):
    """RED/GREEN for the structural half of the roster. fixupApplied is fed a
    real evidence-carrying agent() dispatch but calls no hasOnlyKeys, so:

      * the CONVENTION signal alone does NOT see it -- this is the gap the
        reviewer demonstrated, pinned here so a regression that drops the
        structural signal is caught;
      * the STRUCTURAL signal DOES see it, so the union tripwire fires;
      * the two purely-static locks miss it too (no hasOnlyKeys, no `in`),
        proving the tripwire is the load-bearing check for this shape.
    """
    mutated = _splice_unrostered_agent_fed_guard(mass_translate_source)

    # The gap: convention signal is blind to it.
    assert "fixupApplied" not in _hasonlykeys_callers(mutated), (
        "fixupApplied unexpectedly calls hasOnlyKeys -- this RED proof needs a "
        "guard the CONVENTION signal cannot see, or it proves nothing new"
    )
    # The fix: structural signal sees it, so the union tripwire fires.
    assert "fixupApplied" in _agent_dispatch_consume_sites(mutated), (
        "the structural signal did not identify fixupApplied as fed an "
        "evidence-carrying agent dispatch -- the whole point of this layer"
    )
    with pytest.raises(AssertionError, match="consume-site roster drift"):
        assert_consume_site_list_is_exhaustive(mutated)

    # And the two static locks genuinely miss it, so nothing else would have.
    assert_every_consume_site_guard_routes_through_helper(mutated)  # no raise
    assert_in_operator_confined_to_evidence_helper(mutated)         # no raise


# FIX 1 regression fixture: a dispatch whose FIRST (positional) prompt argument
# is an inline template literal carrying a `${...}` substitution. The
# substitution's closing `}` used to survive the code projection unbalanced, so
# _call_argument_texts closed the call at that stray brace and never reached the
# `schema:` option in arg1 -- the structural signal silently dropped the
# dispatch. templateFedGuard is fed the dispatch's `raw`, skips hasOnlyKeys, and
# is NOT added to CONSUME_SITE_GUARDS.
_TEMPLATE_FED_SPLICE = (
    "async function templateGuardConsumer(seg) {\n"
    "  const rawTpl = await agent(`seg ${seg} now`, { schema: LEDGER_WRITE_SCHEMA });\n"
    "  if (!templateFedGuard(rawTpl)) { return { ok: false }; }\n"
    "  return { ok: true };\n"
    "}\n\n"
    "function templateFedGuard(raw) {\n"
    "  if (!raw || raw.success !== true) return false;\n"
    "  if (raw.exit_code !== undefined) return false;\n"
    "  return typeof raw.status === \"string\";\n"
    "}\n\n"
)


def _splice_template_fed_guard(mass_translate_source: str) -> str:
    """Add templateGuardConsumer + templateFedGuard, the latter fed a dispatch
    whose positional prompt is an inline template literal, without rostering
    it. The shipped template is never modified."""
    assert "function ledgerWriteSucceeded(raw) {" in mass_translate_source
    out = mass_translate_source.replace(
        "function ledgerWriteSucceeded(raw) {",
        _TEMPLATE_FED_SPLICE + "function ledgerWriteSucceeded(raw) {", 1,
    )
    assert out != mass_translate_source, "the template-fed splice did not apply"
    return out


def test_structural_signal_survives_a_template_literal_first_argument(mass_translate_source):
    """RED/GREEN for FIX 1's structural consequence. A dispatch whose
    positional prompt argument is an inline template literal with a `${...}`
    substitution: the substitution's closing `}` used to survive the code
    projection unbalanced, so _call_argument_texts closed the call early and
    the structural signal never saw the `schema:` in arg1 -- the guard fed
    that dispatch slipped the roster silently. With the substitution close
    blanked the projection is brace-balanced, the argument split reaches the
    schema, and the tripwire fires on the unrostered guard.

    Mirrors test_structural_signal_catches_an_agent_fed_guard_the_convention_
    signal_misses, differing only in the dispatch's argument shape."""
    mutated = _splice_template_fed_guard(mass_translate_source)

    # Convention signal is blind to it (no hasOnlyKeys), so only the structural
    # signal can catch this shape -- exactly as the tripwire's union intends.
    assert "templateFedGuard" not in _hasonlykeys_callers(mutated), (
        "templateFedGuard unexpectedly calls hasOnlyKeys -- this RED proof needs "
        "a guard the CONVENTION signal cannot see, or it proves nothing new"
    )
    # The fix: the substitution `}` no longer truncates the argument split, so
    # the structural signal identifies the dispatch and its guard.
    assert "templateFedGuard" in _agent_dispatch_consume_sites(mutated), (
        "the structural signal missed a dispatch whose first argument is an "
        "inline template literal -- the substitution's `}` truncated the "
        "argument split before the schema: option (FIX 1)"
    )
    with pytest.raises(AssertionError, match="consume-site roster drift"):
        assert_consume_site_list_is_exhaustive(mutated)


# Const-only-boundary fixture: a dispatch bound with `let` (e.g. a retry loop
# that reassigns) rather than `const`. The structural signal binds on `const`
# ALONE by design (const-immutability is what makes the whole-span GUARD(name)
# scan sound; a let/var name may be reassigned before a downstream GUARD(name)),
# so a let/var-bound dispatch is outside it -- a known, narrow residual.
# letFedGuard is fed such a dispatch, skips hasOnlyKeys, and is NOT rostered.
# The positional prompt is a plain string, so this fixture exercises the binding
# keyword ALONE (independent of FIX 1's template-substitution brace).
_LET_FED_SPLICE = (
    "async function letGuardConsumer(seg) {\n"
    "  let rawLet = await agent(\"probe prompt\", { schema: LEDGER_WRITE_SCHEMA });\n"
    "  if (!letFedGuard(rawLet)) { return { ok: false }; }\n"
    "  return { ok: true };\n"
    "}\n\n"
    "function letFedGuard(raw) {\n"
    "  if (!raw || raw.success !== true) return false;\n"
    "  if (raw.exit_code !== undefined) return false;\n"
    "  return typeof raw.status === \"string\";\n"
    "}\n\n"
)


def _splice_let_fed_guard(mass_translate_source: str) -> str:
    """Add letGuardConsumer + letFedGuard, the latter fed a `let`-bound
    dispatch, without rostering it. The shipped template is never modified."""
    assert "function ledgerWriteSucceeded(raw) {" in mass_translate_source
    out = mass_translate_source.replace(
        "function ledgerWriteSucceeded(raw) {",
        _LET_FED_SPLICE + "function ledgerWriteSucceeded(raw) {", 1,
    )
    assert out != mass_translate_source, "the let-fed splice did not apply"
    return out


def test_structural_signal_is_const_only_by_design(mass_translate_source):
    """The const-only boundary, pinned rather than asserted. The structural
    signal binds on `const` ALONE, by design -- not merely conservatively.
    const-immutability is exactly what makes the whole-span `GUARD(name)` scan
    sound: a const-bound name provably still holds the dispatch's
    evidence-carrying return at every call site in the enclosing function, so
    any `GUARD(name)` genuinely received it. A `let`/`var`-bound name can be
    REASSIGNED to a derived value before a downstream `GUARD(name)`, so binding
    on `let`/`var` would falsely roster a benign guard that never saw the
    untrusted return. Supporting them soundly needs reassignment-tracking, not
    warranted for a shape no live dispatch uses -- all real dispatch sites bind
    with `const`.

    So a `let`/`var`-bound dispatch feeding a convention-skipping,
    presence-test-free guard is a known, narrow residual -- a sibling of the
    indirect-fed one pinned by
    test_apparatus_residual_is_exactly_indirect_plus_convention_skipping.
    Construct that shape and show every layer here passes it through, so the
    docstring's const-only residual is real and complete, not a hedge.

    (This is the inverse of a RED/GREEN fix test: widening the binding regex to
    (?:const|let|var) is deliberately OUT of scope, and this pins it out.)"""
    mutated = _splice_let_fed_guard(mass_translate_source)

    # The let-bound guard genuinely skips the convention signal too (no
    # hasOnlyKeys), so if the structural signal DID bind on `let` this benign
    # guard would be rostered -- exactly the false positive const-only avoids.
    assert "letFedGuard" not in _hasonlykeys_callers(mutated), (
        "letFedGuard unexpectedly calls hasOnlyKeys -- this boundary proof needs "
        "a guard the CONVENTION signal cannot see, or it proves nothing"
    )
    # The const-only boundary itself: a `let`-bound dispatch is outside the
    # structural signal by design, so its guard is NOT rostered.
    assert "letFedGuard" not in _agent_dispatch_consume_sites(mutated), (
        "a `let`-bound agent dispatch was rostered by the structural signal -- "
        "the binding regex must be const-only by design (const-immutability is "
        "what makes the whole-span GUARD(name) scan sound; supporting let/var "
        "soundly would need reassignment-tracking)"
    )
    # -- so the guard escapes every layer here, exactly as the residual does.
    assert_consume_site_list_is_exhaustive(mutated)
    assert_every_consume_site_guard_routes_through_helper(mutated)
    assert_in_operator_confined_to_evidence_helper(mutated)


def test_apparatus_residual_is_exactly_indirect_plus_convention_skipping(mass_translate_source):
    """The honest boundary, verified rather than asserted. A guard fed its
    evidence-carrying return through INDIRECTION (a wrapper's return, not a
    direct `const x = await agent(...)` binding) and calling no hasOnlyKeys is
    the one shape NO layer here catches. Construct it and show every check
    passes -- so the docstring's residual is real and complete, not a hedge.

    The construction mirrors the live artifactCheckMatched chain: a wrapper
    returns the agent value, the guard is fed the wrapper's result."""
    residual = mass_translate_source.replace(
        "function ledgerWriteSucceeded(raw) {",
        # A guard reached only indirectly, gating on .success with no
        # hasOnlyKeys and no `in` -- the union's acknowledged blind spot.
        "async function wrapProbe(seg) {\n"
        "  return await agent(draftProbePrompt(seg), { effort: \"low\", schema: LEDGER_WRITE_SCHEMA });\n"
        "}\n\n"
        "function probeApplied(p) {\n"
        "  if (!p || p.success !== true) return false;\n"
        "  if (p.exit_code !== undefined) return false;\n"
        "  return true;\n"
        "}\n\n"
        "async function useProbe(seg) {\n"
        "  const wrapped = await wrapProbe(seg);\n"
        "  return probeApplied(wrapped);\n"
        "}\n\n"
        "function ledgerWriteSucceeded(raw) {", 1,
    )
    assert residual != mass_translate_source, "the residual construction did not apply"

    # probeApplied is fed `wrapped`, which is bound to a LOCAL FUNCTION call
    # (wrapProbe), not directly to `await agent(...)`. The structural signal,
    # which follows only direct dispatch bindings, does not reach it --
    assert "probeApplied" not in _agent_dispatch_consume_sites(residual)
    assert "probeApplied" not in _hasonlykeys_callers(residual)
    # -- so every check here passes. This is the residual, named and shown.
    assert_consume_site_list_is_exhaustive(residual)
    assert_every_consume_site_guard_routes_through_helper(residual)
    assert_in_operator_confined_to_evidence_helper(residual)


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(
            lambda s: s.replace(
                "function artifactCheckMatched(art) {",
                "function draftProbeMatched(probe) {\n"
                "  if (!probe || probe.ok !== true) return false;\n"
                "  return hasOnlyKeys(probe, REVIEW_ARTIFACT_ALLOWED_KEYS);\n"
                "}\n"
                "\n"
                "function artifactCheckMatched(art) {",
                1,
            ),
            id="undeclared-fourth-consume-site",
        ),
        pytest.param(
            lambda s: s.replace("function artifactCheckMatched(art) {",
                                "function artifactCheckRenamed(art) {", 1),
            id="declared-guard-renamed-away",
        ),
    ],
)
def test_roster_tripwire_catches_drift_in_either_direction(mass_translate_source, mutate):
    mutated = mutate(mass_translate_source)
    assert mutated != mass_translate_source, "the mutation did not apply"
    with pytest.raises(AssertionError, match="consume-site roster drift"):
        assert_consume_site_list_is_exhaustive(mutated)


# ---------------------------------------------------------------------------
# BEHAVIOURAL half -- each rostered guard is EXECUTED, on the real extracted
# JS, against the returns it must accept and the returns it must reject.
#
# This is what actually locks #289's class, and it replaced a static scanner
# that tried to do the same job by reading the template's text. Two shapes
# defeated the scanner and are caught here by construction, because nothing
# is being read -- the guard is run:
#
#   * A presence test in ANY spelling, however it is written or obfuscated:
#     it rejects a benign `exit_code: 0`, so an "accepted" case fails.
#   * A guard that calls hasFailureEvidence() and THROWS THE RESULT AWAY:
#     textually it routes through the helper, so every static check passes,
#     but it accepts `exit_code: 3`, so a "rejected" case fails.
#
# Note which side catches which -- the accepted cases alone would miss the
# discarded-return shape entirely, and the rejected cases alone would miss
# #289 itself. Both halves are load-bearing; neither is decoration.
#
# HONEST COST, stated rather than papered over: this half is node-gated. On a
# machine without node it does not run, and the only surviving cover is the
# roster tripwire plus the static table-coverage check below -- neither of
# which executes anything. That is already true of every other behavioural
# test of this template (mass_translate_driver_smoke.test.py is skipif'd for
# the whole file), so it is not a regression, but it does mean a node-free
# CI leg cannot be read as "the guards are correct".
# ---------------------------------------------------------------------------

NODE = shutil.which("node")
requires_node = pytest.mark.skipif(NODE is None, reason="node is not installed")

# The roster the tripwire above pins, and the behavioural cases below drive.
# Adding a consume site means adding an entry here -- which means stating,
# in the same commit, what it must accept and what it must reject.
CONSUME_SITE_GUARDS = {
    "ledgerWriteSucceeded": {
        "accepted": {
            "clean-success": {
                "success": True, "status": "converged",
                "fragment_path": "/x/seg01.json", "fragment_sha1": "a" * 40,
            },
            # #289 itself: the truthful exit_code a presence test rejected.
            "truthful-exit-code-zero": {
                "success": True, "status": "converged",
                "fragment_path": "/x/seg01.json", "fragment_sha1": "a" * 40, "exit_code": 0,
            },
        },
        "rejected": {
            "nonzero-exit-code": {
                "success": True, "status": "converged",
                "fragment_path": "/x/seg01.json", "fragment_sha1": "a" * 40, "exit_code": 3,
            },
            "wrong-typed-exit-code": {
                "success": True, "status": "converged",
                "fragment_path": "/x/seg01.json", "fragment_sha1": "a" * 40, "exit_code": "0",
            },
            "nonempty-stderr": {
                "success": True, "status": "converged",
                "fragment_path": "/x/seg01.json", "fragment_sha1": "a" * 40,
                "stderr": "Traceback (most recent call last):",
            },
            "nonempty-error": {
                "success": True, "status": "converged",
                "fragment_path": "/x/seg01.json", "fragment_sha1": "a" * 40,
                "error": "runs/ledger.d is not writable",
            },
        },
    },
    "ledgerMergeSucceeded": {
        "accepted": {
            "clean-success": {
                "success": True, "ledger_path": "/x/l.json", "n_segments": 3,
                "missing_segments": [], "stale_segments": [],
            },
            "truthful-exit-code-zero": {
                "success": True, "ledger_path": "/x/l.json", "n_segments": 3,
                "missing_segments": [], "stale_segments": [], "exit_code": 0,
            },
        },
        "rejected": {
            "nonzero-exit-code": {
                "success": True, "ledger_path": "/x/l.json", "n_segments": 3,
                "missing_segments": [], "stale_segments": [], "exit_code": 2,
            },
            "nonempty-stderr": {
                "success": True, "ledger_path": "/x/l.json", "n_segments": 3,
                "missing_segments": [], "stale_segments": [], "stderr": "cache_key.py died",
            },
            "incomplete-batch-despite-exit-code-zero": {
                "success": True, "ledger_path": "/x/l.json", "n_segments": 1,
                "missing_segments": ["seg09"], "stale_segments": [], "exit_code": 0,
            },
        },
    },
    "artifactCheckMatched": {
        "accepted": {
            "bare-match": {"match": True},
            # #289's third site: review_artifact_check.py never emits
            # mismatch_detail on a match, so an empty one is agent-added and
            # benign. The pre-fix guard escalated it on presence alone.
            "benign-empty-mismatch-detail": {"match": True, "mismatch_detail": ""},
        },
        "rejected": {
            "real-mismatch-detail": {"match": True, "mismatch_detail": "verse count differs"},
            "match-false": {"match": False},
            "undeclared-key": {"match": True, "exit_code": 0},
        },
    },
}


def _extract_function_source(mass_translate_source: str, signature_prefix: str) -> str:
    """ledger_update.test.py's brace-balanced function extractor, located via
    the code projection -- see _from_declaration(). Its own scanning is
    reused verbatim; only where it starts changes."""
    return _extract_js_function(
        _from_declaration(mass_translate_source, signature_prefix), signature_prefix
    )


def _extract_const_statement(mass_translate_source: str, const_name: str) -> str:
    """The same treatment for the single-line `const NAME = ...;` extractor.
    That one terminates on the first raw `;`, so it was doubly exposed: a
    decoy comment could both start it in the wrong place and end it there."""
    return _extract_js_const(
        _from_declaration(mass_translate_source, f"const {const_name} "), const_name
    )


def _guard_harness_preamble(mass_translate_source: str) -> str:
    """Every declaration a rostered guard needs, spliced verbatim from the
    real template in SOURCE order -- which is what keeps the dependency chain
    valid: the `*_ALLOWED_KEYS` consts `.concat()` their inputs at
    declaration time, and NO_FAILURE_EVIDENCE references the benign-value
    predicates by identifier. The `*_KEYS` consts are collected by pattern
    rather than named, so a fourth schema's own key sets come along without
    editing this."""
    parts = [
        _extract_function_source(mass_translate_source, "function isNonEmptyString("),
        _extract_function_source(mass_translate_source, "function isEmptyString("),
        _extract_function_source(mass_translate_source, "function isZeroExitCode("),
        _extract_function_source(mass_translate_source, "function hasOnlyKeys("),
    ]
    # Collected from the CODE PROJECTION, so a `const FOO_KEYS = ...` sitting
    # in a comment cannot add a name whose real declaration does not exist.
    key_consts = re.findall(r"^const ([A-Za-z_$][\w$]*_KEYS)\s*=",
                            _js_code_only(mass_translate_source), re.M)
    assert key_consts, "no `const *_KEYS` declarations found in the template"
    parts += [_extract_const_statement(mass_translate_source, name) for name in key_consts]
    parts.append(_extract_const_statement(mass_translate_source, "NO_FAILURE_EVIDENCE"))
    parts.append(_extract_function_source(mass_translate_source, "function hasFailureEvidence("))
    return "\n".join(parts)


def run_guard(tmp_path, mass_translate_source: str, guard: str, raws: list) -> list:
    """Run the REAL extracted `guard` under node over `raws`, returning its
    verdicts. Nothing is reimplemented in Python -- a mirror predicate could
    agree with a broken guard, which is the blind spot this exists to avoid."""
    signature = f"function {guard}("
    assert signature in mass_translate_source, f"template declares no {guard}()"
    script = tmp_path / f"{guard}_probe.js"
    script.write_text(
        _guard_harness_preamble(mass_translate_source) + "\n"
        + _extract_function_source(mass_translate_source, signature) + "\n"
        f"console.log(JSON.stringify(JSON.parse(process.argv[2]).map({guard})));\n",
        encoding="utf-8",
    )
    proc = subprocess.run(
        [NODE, str(script), json.dumps(raws)],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, f"{guard} harness failed: {proc.stderr}"
    verdicts = json.loads(proc.stdout)
    assert len(verdicts) == len(raws), f"{guard} returned {len(verdicts)} verdicts for {len(raws)} inputs"
    return verdicts


@pytest.mark.parametrize("guard", sorted(CONSUME_SITE_GUARDS))
@requires_node
def test_rostered_guard_accepts_every_benign_return(tmp_path, mass_translate_source, guard):
    """A presence test in ANY spelling fails here: it rejects a return whose
    only sin is carrying a truthful, benign value in a declared optional
    field. This is #289 stated as behaviour rather than as text."""
    cases = CONSUME_SITE_GUARDS[guard]["accepted"]
    verdicts = run_guard(tmp_path, mass_translate_source, guard, list(cases.values()))
    wrong = [label for label, ok in zip(cases, verdicts) if ok is not True]
    assert not wrong, (
        f"#289 regression -- {guard}() REJECTED a benign return. A declared "
        "optional field carrying a truthful benign value (`exit_code: 0`, an "
        "empty mismatch_detail) is proof of success, not of failure; rejecting "
        "it fails work that was already correct, non-deterministically, "
        f"depending on whether the agent volunteered the field. Cases: {wrong}"
    )


@pytest.mark.parametrize("guard", sorted(CONSUME_SITE_GUARDS))
@requires_node
def test_rostered_guard_rejects_every_evidence_bearing_return(tmp_path, mass_translate_source, guard):
    """The anti-false-green half, and the one that catches a guard which
    calls hasFailureEvidence() and discards the result -- textually perfect,
    behaviourally inert."""
    cases = CONSUME_SITE_GUARDS[guard]["rejected"]
    verdicts = run_guard(tmp_path, mass_translate_source, guard, list(cases.values()))
    wrong = [label for label, ok in zip(cases, verdicts) if ok is not False]
    assert not wrong, (
        f"false green -- {guard}() ACCEPTED a return carrying real evidence of "
        "failure. Check that the guard does not merely CALL hasFailureEvidence() "
        "but acts on what it returns, and that its allowed-key check still "
        f"runs. Cases: {wrong}"
    )


@pytest.mark.parametrize(
    "guard,mutate,broken_side",
    [
        # Bypass 1's payload: the #289 presence test, reintroduced. The
        # deleted scanner could be blinded to this by a fake regex literal;
        # execution cannot be.
        pytest.param(
            "ledgerWriteSucceeded",
            lambda s: s.replace(
                "  if (hasFailureEvidence(raw, FAILURE_EVIDENCE_KEYS)) return false;",
                "  if (FAILURE_EVIDENCE_KEYS.some((k) => k in raw)) return false;",
                1,
            ),
            "accepted",
            id="presence-test-reintroduced",
        ),
        # Bypass 2's payload: calls the helper, throws the result away.
        pytest.param(
            "ledgerWriteSucceeded",
            lambda s: s.replace(
                "  if (hasFailureEvidence(raw, FAILURE_EVIDENCE_KEYS)) return false;",
                "  hasFailureEvidence(raw, FAILURE_EVIDENCE_KEYS);",
                1,
            ),
            "rejected",
            id="evidence-verdict-discarded",
        ),
        pytest.param(
            "artifactCheckMatched",
            lambda s: s.replace(
                "  if (hasFailureEvidence(art, REVIEW_ARTIFACT_EVIDENCE_KEYS)) return false;",
                '  if (REVIEW_ARTIFACT_EVIDENCE_KEYS.some((k) => k in art)) return false;',
                1,
            ),
            "accepted",
            id="presence-test-at-the-third-site",
        ),
    ],
)
@requires_node
def test_behavioural_lock_catches_both_static_bypasses(
    tmp_path, mass_translate_source, guard, mutate, broken_side,
):
    """RED proof, kept permanently, for the two shapes that defeated the
    static scanner this replaced. Each mutant is spliced into a COPY of the
    template source; the shipped file is never touched.

    `broken_side` records WHICH half catches it, so a future edit that
    quietly drops one half cannot claim the other still covers the class."""
    mutated = mutate(mass_translate_source)
    assert mutated != mass_translate_source, (
        "the mutation did not apply -- its anchor has moved, so this RED proof "
        "would pass without exercising anything"
    )
    cases = CONSUME_SITE_GUARDS[guard][broken_side]
    verdicts = run_guard(tmp_path, mutated, guard, list(cases.values()))
    expected = True if broken_side == "accepted" else False
    assert any(v is not expected for v in verdicts), (
        f"the {broken_side} half did not catch this mutant of {guard}() -- it "
        f"returned {verdicts} where an honest guard returns all {expected!r}"
    )


@requires_node
def test_guard_harness_reproduces_the_honest_verdicts(tmp_path, mass_translate_source):
    """Non-vacuity control for the harness itself: a harness that silently
    failed to splice the guard, or that always returned the same verdict,
    would make both behavioural tests above meaningless. The unmutated guard
    must produce BOTH verdicts."""
    write = CONSUME_SITE_GUARDS["ledgerWriteSucceeded"]
    verdicts = run_guard(
        tmp_path, mass_translate_source, "ledgerWriteSucceeded",
        list(write["accepted"].values()) + list(write["rejected"].values()),
    )
    assert set(verdicts) == {True, False}, (
        f"the harness returned only {set(verdicts)} -- it is not discriminating"
    )


# ---------------------------------------------------------------------------
# #289 TABLE COVERAGE -- every key hasFailureEvidence() is asked about must
# have a NO_FAILURE_EVIDENCE row.
#
# hasFailureEvidence() reads `const benign = NO_FAILURE_EVIDENCE[k]` and
# treats a missing row as unclassifiable, counting the field as evidence.
# Failing CLOSED is the right direction -- but it fails SILENTLY. An evidence
# key with no row makes its guard reject unconditionally: every present value
# is evidence, so a perfectly good return is reported as a failure, with
# nothing in the build to say why. That is the #289 outcome reproduced by the
# machinery built to prevent it, and it would surface only in a live run --
# which is precisely what #289 cost.
#
# The set of evidence keys is derived from hasFailureEvidence()'s own CALL
# SITES, not from a list kept here: whatever second argument the template
# actually passes is what the table must cover. A fifth flat schema that
# declares `const DRAFT_PROBE_EVIDENCE_KEYS = ["timed_out"]` and passes it in
# therefore fails the build until NO_FAILURE_EVIDENCE declares a `timed_out`
# row -- the guarantee the 1.15.0 CHANGELOG claims, which until now nothing
# actually enforced.
# ---------------------------------------------------------------------------

def _object_literal_key_names(literal_text: str) -> list:
    """The keys declared at the TOP level of a `{...}` object-literal text.

    NO_FAILURE_EVIDENCE's values are bare function identifiers
    (`error: isEmptyString`), not JSON values, so the imported
    parse_js_object_literal cannot read it -- its grammar is the schema
    literals' JSON-shaped subset and it raises "unexpected bare identifier as
    a value". Only the KEYS are wanted here, and they are read structurally
    rather than by regex: at depth 1, an identifier or quoted string that
    both FOLLOWS a `{`/`,` and is FOLLOWED by `:` is a key. Requiring both
    sides rules out an identifier in value position (a `cond ? a : b` value
    would otherwise contribute `a`), and anything nested sits at depth >= 2,
    so a table that grows a nested value cannot smuggle a key in. The
    preceding token is tracked forward rather than scanned backward, so a
    comment between `{`/`,` and a key does not hide it."""
    if not literal_text.startswith("{"):
        raise AssertionError(
            f"expected an object literal starting with '{{', got {literal_text[:40]!r}"
        )
    keys = []
    depth = 0
    prev = ""  # last significant character, comments and whitespace skipped
    i = 0
    n = len(literal_text)
    while i < n:
        c = literal_text[i]
        if c == "/" and literal_text.startswith("//", i):
            end = literal_text.find("\n", i)
            i = n if end == -1 else end
            continue
        if c in " \t\r\n":
            i += 1
            continue
        if c in "{[":
            depth += 1
            prev = c
            i += 1
            continue
        if c in "}]":
            depth -= 1
            prev = c
            i += 1
            continue
        if c in "\"'":
            # A quoted key. Scanned inline rather than via a shared JS string
            # helper: this walks one small object-literal text, not a whole
            # template, and the general lexer that used to provide that helper
            # was deleted for being unsafe at template scale.
            end = i + 1
            while end < n and literal_text[end] != c:
                end += 2 if literal_text[end] == "\\" else 1
            if end >= n:
                raise AssertionError(
                    f"unterminated {c} key literal in {literal_text[:60]!r}"
                )
            token = literal_text[i + 1:end]
            end += 1
        elif c.isalpha() or c in "_$":
            end = i
            while end < n and (literal_text[end].isalnum() or literal_text[end] in "_$"):
                end += 1
            token = literal_text[i:end]
        else:
            prev = c
            i += 1
            continue
        after = end
        while after < n and literal_text[after] in " \t\r\n":
            after += 1
        if depth == 1 and prev in "{," and after < n and literal_text[after] == ":":
            keys.append(token)
        prev = "token"  # never in "{,", so a value cannot look like a key
        i = end
    return keys


def _evidence_key_consts_passed_to_helper(mass_translate_source: str) -> set:
    """The const NAMES the template passes as hasFailureEvidence()'s
    `evidenceKeys` argument, read off the RAW source with a plain regex --
    no lexing, and no function-span extraction.

    Both of this scan's failure modes are loud and safe. A `hasFailureEvidence(
    raw, FOO_KEYS)` mentioned only in a comment yields a name that has no
    `const FOO_KEYS` array, and extraction fails naming it. A call site whose
    second argument is an inline array literal simply does not match, so it
    is reported as an unmatched call site below rather than silently skipped.
    The previous version read a lexed projection, which could be blinded into
    dropping a real call site altogether -- a false GREEN.

    A bare `hasFailureEvidence()` with no argument at all is prose, not a
    call -- the template's own comments refer to the helper that way -- so
    only mentions carrying at least one identifier argument are considered."""
    call = re.compile(
        r"\bhasFailureEvidence\s*\(\s*[A-Za-z_$][\w$]*\s*,\s*([A-Za-z_$][\w$]*)\s*\)"
    )
    names = set()
    unmatched = []
    for m in re.finditer(r"\bhasFailureEvidence\s*\(\s*[A-Za-z_$][\w$]*",
                         mass_translate_source):
        if mass_translate_source[:m.start()].rstrip().endswith("function"):
            continue  # the helper's own declaration, not a call of it
        full = call.match(mass_translate_source, m.start())
        if full:
            names.add(full.group(1))
        else:
            unmatched.append(_line_of(mass_translate_source, m.start()))
    assert not unmatched, (
        "hasFailureEvidence() is called at line(s) "
        f"{unmatched} in a shape this check cannot read -- most likely an "
        "inline array literal as the evidenceKeys argument. Pass a NAMED "
        "const instead: an inline literal has no declaration for the "
        "NO_FAILURE_EVIDENCE coverage check to follow, which is the whole "
        "point of that check."
    )
    return names


def assert_every_evidence_key_has_a_table_row(mass_translate_source: str) -> None:
    """The #289 table-coverage lock, factored out so the RED-proof tests
    below can run it against a MUTATED copy of the template source."""
    table_keys = set(_object_literal_key_names(
        extract_const_object_literal(mass_translate_source, "NO_FAILURE_EVIDENCE")
    ))
    assert table_keys, (
        "no keys were read out of NO_FAILURE_EVIDENCE -- the table is empty, "
        "or _object_literal_key_names() no longer understands its shape. "
        "Either way this lock would pass vacuously."
    )

    const_names = _evidence_key_consts_passed_to_helper(mass_translate_source)
    assert const_names, (
        "no hasFailureEvidence() call sites were found, so no evidence keys "
        "could be derived -- this lock would pass vacuously."
    )

    uncovered = {}
    for const_name in sorted(const_names):
        keys = set(parse_js_object_literal(
            extract_const_array_literal(mass_translate_source, const_name)
        ))
        assert keys, f"{const_name} is empty -- an evidence key set with no keys"
        if keys - table_keys:
            uncovered[const_name] = sorted(keys - table_keys)
    assert not uncovered, (
        "#289 silent-rejection gap -- hasFailureEvidence() is called with "
        "evidence keys that NO_FAILURE_EVIDENCE has no row for. A missing row "
        "means `benign === undefined`, so EVERY present value of that field "
        "counts as failure evidence and the guard rejects unconditionally: "
        "correct work reported as failed, with nothing in the build to say "
        "why, discoverable only in a live run. Add a benign-value predicate "
        f"row for each. Table has {sorted(table_keys)}; uncovered: {uncovered}"
    )


def test_every_evidence_key_has_a_no_failure_evidence_row(mass_translate_source):
    assert_every_evidence_key_has_a_table_row(mass_translate_source)


@pytest.mark.parametrize(
    "literal_text,expected",
    [
        pytest.param("{ error: isEmptyString, exit_code: isZeroExitCode }",
                     ["error", "exit_code"], id="bare-identifier-values"),
        pytest.param('{ "error": isEmptyString, exit_code: isZeroExitCode }',
                     ["error", "exit_code"], id="quoted-key"),
        pytest.param("{\n  // a note\n  error: isEmptyString,\n  stderr: isEmptyString,\n}",
                     ["error", "stderr"], id="comment-between-keys"),
        pytest.param("{ a: { nested: x }, b: y }", ["a", "b"], id="nested-object-key-excluded"),
        pytest.param("{ a: cond ? p : q, b: y }", ["a", "b"], id="ternary-value-not-a-key"),
        pytest.param("{ a: [1, 2], b: y }", ["a", "b"], id="array-value"),
        pytest.param("{ }", [], id="empty"),
    ],
)
def test_object_literal_key_reader_shapes(literal_text, expected):
    """The whole table-coverage lock rests on this reader: an over-reading
    one invents rows that do not exist (false green, the failure mode that
    matters), an under-reading one invents uncovered keys. Both halves are
    pinned here against hand-written shapes rather than only against the
    real table, which the reader and the lock were written to fit."""
    assert _object_literal_key_names(literal_text) == expected


def test_no_failure_evidence_table_keys_are_read_correctly(mass_translate_source):
    """Non-vacuity control for the extraction the lock above depends on: a
    key-reader that silently returned everything, or nothing, would make the
    coverage assertion meaningless in either direction."""
    keys = _object_literal_key_names(
        extract_const_object_literal(mass_translate_source, "NO_FAILURE_EVIDENCE")
    )
    assert len(keys) == len(set(keys)), f"duplicate keys read from the table: {keys}"
    # The values are bare identifiers; none of them may be read as a key.
    assert not {"isEmptyString", "isZeroExitCode"} & set(keys), (
        f"a benign-value PREDICATE was read as a table KEY: {keys}"
    )


@pytest.mark.parametrize(
    "mutate,label",
    [
        pytest.param(
            lambda s: s.replace(
                'const FAILURE_EVIDENCE_KEYS = ["error", "exit_code", "stderr"];',
                'const FAILURE_EVIDENCE_KEYS = ["error", "exit_code", "stderr", "timed_out"];',
                1,
            ),
            "new key on an existing evidence const",
            id="key-added-to-existing-const",
        ),
        pytest.param(
            # The CHANGELOG's actual claim: a FOURTH flat schema arrives with
            # its own evidence const and its own guard, and declares no row.
            lambda s: s.replace(
                "function artifactCheckMatched(art) {",
                'const DRAFT_PROBE_EVIDENCE_KEYS = ["timed_out"];\n'
                "function draftProbeMatched(probe) {\n"
                "  if (hasFailureEvidence(probe, DRAFT_PROBE_EVIDENCE_KEYS)) return false;\n"
                "  return hasOnlyKeys(probe, DRAFT_PROBE_EVIDENCE_KEYS);\n"
                "}\n"
                "\n"
                "function artifactCheckMatched(art) {",
                1,
            ),
            "fourth flat schema with its own evidence const and guard",
            id="fourth-schema-guard",
        ),
    ],
)
def test_table_coverage_catches_an_evidence_key_with_no_row(
    mass_translate_source, mutate, label,
):
    mutated = mutate(mass_translate_source)
    assert mutated != mass_translate_source, (
        f"the {label!r} mutation did not apply -- its anchor text has moved, "
        "so this RED proof would pass without ever exercising the lock"
    )
    with pytest.raises(AssertionError, match="#289 silent-rejection gap"):
        assert_every_evidence_key_has_a_table_row(mutated)


def test_table_coverage_accepts_a_new_evidence_key_that_declares_its_row(mass_translate_source):
    """The other side of the RED proof: the lock must not be satisfiable only
    by never growing. Adding the key AND its benign-value row passes."""
    # Each replace is asserted separately. Chained, only the second was
    # checked, so a moved anchor on the FIRST one left this passing against
    # an unmutated key list -- the vacuous shape this test exists to rule out.
    with_key = mass_translate_source.replace(
        'const FAILURE_EVIDENCE_KEYS = ["error", "exit_code", "stderr"];',
        'const FAILURE_EVIDENCE_KEYS = ["error", "exit_code", "stderr", "timed_out"];',
        1,
    )
    assert with_key != mass_translate_source, "the evidence-key mutation did not apply"
    mutated = with_key.replace(
        "const NO_FAILURE_EVIDENCE = { error: isEmptyString,",
        "const NO_FAILURE_EVIDENCE = { timed_out: isEmptyString, error: isEmptyString,",
        1,
    )
    assert mutated != with_key, "the table-row mutation did not apply"
    assert_every_evidence_key_has_a_table_row(mutated)


# ---------------------------------------------------------------------------
# #289 ALLOWED-KEY PARITY -- each guard's allowed-key list must equal the
# properties its flat literal declares.
#
# hasOnlyKeys(raw, X_ALLOWED_KEYS) rejects any key the list omits. Add a
# property to a flat literal without adding it to the key list and an agent
# that honestly fills the field it was just promised gets its return REJECTED
# -- a false reject, silent, surfacing only as failed work in a live run.
# That is #289's shape and #289's exact cost, reached from the other side.
#
# The reverse gap is real too, just cheaper: a key allowed but never declared
# waves through a field the schema does not advertise, which is the
# undeclared-key rejection those lists exist to perform.
# ---------------------------------------------------------------------------

ALLOWED_KEYS_TO_FLAT_LITERAL = {
    "LEDGER_WRITE_ALLOWED_KEYS": "LEDGER_WRITE_SCHEMA",
    "LEDGER_MERGE_ALLOWED_KEYS": "LEDGER_MERGE_SCHEMA",
    "REVIEW_ARTIFACT_ALLOWED_KEYS": "REVIEW_ARTIFACT_SCHEMA",
}


def _resolve_allowed_keys(mass_translate_source: str, const_name: str) -> set:
    """`const X_ALLOWED_KEYS = A.concat(B);` -> the union of A's and B's keys.

    The operand names are read out of the concat EXPRESSION rather than
    hand-paired here, so a list that grows a third operand is followed
    automatically."""
    code = _js_code_only(mass_translate_source)
    m = re.search(r"const\s+" + re.escape(const_name) + r"\s*=\s*([^;]+);", code)
    assert m, f"the template declares no `const {const_name} = ...;`"
    operands = [w for w in re.findall(r"[A-Za-z_$][\w$]*", m.group(1)) if w != "concat"]
    assert operands, f"{const_name} names no key-set operands: {m.group(1)!r}"
    keys = set()
    for operand in operands:
        keys |= set(parse_js_object_literal(
            extract_const_array_literal(mass_translate_source, operand)
        ))
    return keys


def assert_allowed_keys_match_flat_literal_properties(mass_translate_source: str) -> None:
    mismatches = {}
    for allowed_const, literal_const in ALLOWED_KEYS_TO_FLAT_LITERAL.items():
        declared = set(parse_js_object_literal(
            extract_const_object_literal(mass_translate_source, literal_const)
        ).get("properties", {}))
        allowed = _resolve_allowed_keys(mass_translate_source, allowed_const)
        assert declared, f"{literal_const} declares no properties"
        if allowed != declared:
            mismatches[allowed_const] = {
                "declared by the schema but NOT allowed (honest returns get rejected)":
                    sorted(declared - allowed),
                "allowed but NOT declared by the schema": sorted(allowed - declared),
            }
    assert not mismatches, (
        "allowed-key drift -- a guard's hasOnlyKeys() list no longer matches "
        "the properties its flat literal advertises to the agent. A property "
        "the schema declares but the list omits is the worse direction: the "
        "agent is invited to fill the field and then REJECTED for filling it, "
        "silently, exactly as in #289. Keep the two in step in the same "
        f"commit. {mismatches}"
    )


def test_every_allowed_key_set_matches_its_flat_literal_properties(mass_translate_source):
    assert_allowed_keys_match_flat_literal_properties(mass_translate_source)


@pytest.mark.parametrize(
    "old,new,label",
    [
        pytest.param(
            "    fragment_sha1: { type: \"string\" },\n",
            "    fragment_sha1: { type: \"string\" },\n    duration_ms: { type: \"integer\" },\n",
            "a property added to the flat literal but not to the key list",
            id="schema-grew-key-list-did-not",
        ),
        pytest.param(
            'const LEDGER_WRITE_SUCCESS_KEYS = ["success", "status", "fragment_path", "fragment_sha1"];',
            'const LEDGER_WRITE_SUCCESS_KEYS = ["success", "status", "fragment_path"];',
            "a key dropped from the list but still declared by the schema",
            id="key-list-shrank-schema-did-not",
        ),
    ],
)
def test_allowed_key_parity_catches_drift_in_either_direction(
    mass_translate_source, old, new, label,
):
    assert old in mass_translate_source, f"anchor for {label!r} has moved: {old!r}"
    mutated = mass_translate_source.replace(old, new, 1)
    assert mutated != mass_translate_source, f"the {label!r} mutation did not apply"
    with pytest.raises(AssertionError, match="allowed-key drift"):
        assert_allowed_keys_match_flat_literal_properties(mutated)
