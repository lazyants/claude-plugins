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
  (d) EXACT-KEY-SET JS GUARD (CONTRACT section 5): a small Python mirror of
      the load-bearing `ledgerWriteSucceeded`/`ledgerMergeSucceeded` JS
      guard predicates in mass-translate-wf.template.js, FOR TESTABILITY
      ONLY -- the real, load-bearing implementation lives in the JS
      template (owner B). Property-tested against clean success/failure,
      a branch crossover, success-keys-plus-a-failure-only-key, an
      empty-string required string field, and a claimed success with a
      non-empty missing_segments. Plus a best-effort, clearly-optional
      static check that the SAME key-set literals appear in the real JS
      guard functions near the template's ledger consume sites.

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
extract_const_object_literal = _drift.extract_const_object_literal


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
# (d) EXACT-KEY-SET JS GUARD (CONTRACT section 5) -- a Python MIRROR of the
#     load-bearing JS predicate, for testability only. The real,
#     load-bearing implementation lives in mass-translate-wf.template.js
#     (ledgerWriteSucceeded/ledgerMergeSucceeded), owned by Owner B.
# ===========================================================================

LEDGER_WRITE_SUCCESS_KEYS = {"success", "status", "fragment_path", "fragment_sha1"}
LEDGER_MERGE_SUCCESS_KEYS = {"success", "ledger_path", "n_segments", "missing_segments", "stale_segments"}
FAILURE_ONLY_KEYS = {"error", "exit_code", "stderr"}


def _is_non_empty_string(v) -> bool:
    return isinstance(v, str) and len(v) > 0


def _is_plain_int(v) -> bool:
    # bool is a subclass of int in Python -- n_segments must be a genuine
    # integer, never True/False sneaking through an `isinstance(v, int)`
    # check (see this repo's own stdlib-JSON-gate hardening notes).
    return isinstance(v, int) and not isinstance(v, bool)


def ledger_write_succeeded(raw) -> bool:
    """Mirrors CONTRACT section 5's documented guard rule for testability --
    the load-bearing implementation lives in the JS template, owned by
    Owner B (ledgerWriteSucceeded() in mass-translate-wf.template.js)."""
    if not isinstance(raw, dict) or raw.get("success") is not True:
        return False
    if FAILURE_ONLY_KEYS & raw.keys():
        return False
    if not set(raw.keys()) <= LEDGER_WRITE_SUCCESS_KEYS:
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
    if FAILURE_ONLY_KEYS & raw.keys():
        return False
    if not set(raw.keys()) <= LEDGER_MERGE_SUCCESS_KEYS:
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
        pytest.param(
            {
                "success": True, "status": "converged", "fragment_path": "/x/seg01.json",
                "fragment_sha1": "a" * 40, "exit_code": 0,
            },
            False, id="success-keys-plus-failure-only-key",
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
            False, id="success-keys-plus-failure-only-key",
        ),
        pytest.param(
            {"success": True, "ledger_path": "/x/ledger.json", "n_segments": 1, "missing_segments": ["seg09"], "stale_segments": []},
            False, id="claimed-success-with-nonempty-missing-segments",
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
# Best-effort, clearly-OPTIONAL static check: the same key-set literals
# appear in the real JS guard functions near the template's ledger consume
# sites. Skips gracefully (does not fail the file) if the guard doesn't
# exist yet under this exact name; when it DOES exist, its key sets are
# asserted to actually match this file's own mirror predicate above, so a
# genuine divergence between the JS guard and this Python mirror is caught.
#
# LEDGER_WRITE_SUCCESS_KEYS/LEDGER_MERGE_SUCCESS_KEYS/FAILURE_ONLY_KEYS are
# declared as ARRAY literals (`const NAME = [...]`), not object literals --
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


def test_optional_js_guard_key_set_literals_match_python_mirror(mass_translate_source):
    try:
        write_keys_literal = extract_const_array_literal(mass_translate_source, "LEDGER_WRITE_SUCCESS_KEYS")
        merge_keys_literal = extract_const_array_literal(mass_translate_source, "LEDGER_MERGE_SUCCESS_KEYS")
        failure_only_literal = extract_const_array_literal(mass_translate_source, "FAILURE_ONLY_KEYS")
    except AssertionError:
        pytest.skip(
            "mass-translate-wf.template.js does not (yet) declare "
            "LEDGER_WRITE_SUCCESS_KEYS/LEDGER_MERGE_SUCCESS_KEYS/"
            "FAILURE_ONLY_KEYS as named consts near its ledger consume "
            "sites -- optional check, not a hard requirement (CONTRACT "
            "section 5's guard may be implemented with inline literals "
            "instead); this does not fail the file."
        )
        return

    js_write_keys = set(parse_js_object_literal(write_keys_literal))
    js_merge_keys = set(parse_js_object_literal(merge_keys_literal))
    js_failure_only_keys = set(parse_js_object_literal(failure_only_literal))

    assert js_write_keys == LEDGER_WRITE_SUCCESS_KEYS, (
        f"JS LEDGER_WRITE_SUCCESS_KEYS {sorted(js_write_keys)} does not match "
        f"this file's own Python mirror {sorted(LEDGER_WRITE_SUCCESS_KEYS)}"
    )
    assert js_merge_keys == LEDGER_MERGE_SUCCESS_KEYS, (
        f"JS LEDGER_MERGE_SUCCESS_KEYS {sorted(js_merge_keys)} does not match "
        f"this file's own Python mirror {sorted(LEDGER_MERGE_SUCCESS_KEYS)}"
    )
    assert js_failure_only_keys == FAILURE_ONLY_KEYS, (
        f"JS FAILURE_ONLY_KEYS {sorted(js_failure_only_keys)} does not match "
        f"this file's own Python mirror {sorted(FAILURE_ONLY_KEYS)}"
    )
