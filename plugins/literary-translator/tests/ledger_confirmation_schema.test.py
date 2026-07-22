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
    with pytest.raises(AssertionError, match=f"const {const_name} ="):
        assert_js_guard_key_sets_match_python_mirror(mutated)


# ---------------------------------------------------------------------------
# #289 CLASS LOCK -- the presence-test idiom must stay deleted.
#
# #289 was found three times in one file (ledgerWriteSucceeded,
# ledgerMergeSucceeded, artifactCheckMatched): a key-PRESENCE test standing
# in for a failure-EVIDENCE test on a field the flat schema itself
# advertises as fillable. Patching the three sites is rung 3; this lock is
# rung 4.
#
# An earlier form of this lock enumerated the SPELLINGS a presence test can
# be written in: it derived every optional field from the flat literals and
# searched the template for `"<field>" in <obj>`. That is a blacklist, and it
# did not actually cover #289 -- two of the three sites tested a LOOP
# VARIABLE (`FAILURE_ONLY_KEYS.some((k) => k in raw)`), never a quoted field
# name, so the lock would have missed the very defect it exists to lock out.
# Only artifactCheckMatched's `!("mismatch_detail" in art)` matched it.
#
# So the lock is stated as its INVERSE instead: the JS `in` operator may
# appear in this template's CODE only inside hasFailureEvidence(), the single
# helper that owns the judgement. Every spelling -- quoted literal, loop
# variable, computed key -- and every site, including a fourth consume-site
# guard that does not exist yet, is then a failure by construction rather
# than by enumeration.
#
# Two consequences, both deliberate:
#   * A `for (const k in obj)` would trip this lock too, even though it is
#     not a presence test. The template's idiom is Object.keys() (see
#     hasOnlyKeys()), so nothing is given up today; a genuine future need has
#     to amend this lock in the same commit and say why, which is exactly the
#     review this defect class earned. A looser regex that tried to tell the
#     two apart would be back to enumerating spellings.
#   * hasFailureEvidence() is the ONE exemption, and it cannot be widened by
#     moving a presence test INTO the helper and then not calling it:
#     test_every_consume_site_guard_routes_through_the_shared_evidence_helper
#     below pins that all three guards still consult it.
#
# The scan runs over CODE only. This template is ~1300 lines of which most is
# comment and agent-prompt prose ("in place", "in full", "in binary mode"),
# so a search for the word over the raw source would be meaningless.
# _js_code_only() blanks comments, string/template literals and regex
# literals to spaces, preserving offsets so reported line numbers stay honest.
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
    can close one -- `)`, `]`, `}`, an identifier, a number -- is read as
    division, unless that identifier is one of the keywords above.

    The current template contains no division at all (all five of its `/`
    literals are regexes); the division branch exists so that introducing one
    cannot silently make the scanner swallow code up to the next `/`. Where
    the two readings are genuinely ambiguous the tie goes to division, which
    consumes one character, over a regex, which could consume a line."""
    j = offset - 1
    while j >= 0 and source[j] in " \t\r\n":
        j -= 1
    if j < 0:
        return True
    c = source[j]
    if c in ")]}":
        return False
    if c.isalnum() or c in "_$":
        k = j
        while k >= 0 and (source[k].isalnum() or source[k] in "_$"):
            k -= 1
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
        f"{source[start:start + 60]!r}. If that `/` is actually a division, "
        "_regex_may_start_at() misclassified it and needs the new context."
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
    character whether it is code, and blank everything that is not."""
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
    """(start, end) offsets of `function <name>(...) { ... }` -- same
    column-0 `\\n}` terminator convention as the sibling guard test below."""
    start = mass_translate_source.find(f"function {name}(")
    assert start != -1, (
        f"the template no longer declares function {name}() -- the #289 class "
        "lock is anchored on it. If the helper was renamed or removed, "
        "re-anchor the lock deliberately rather than letting it disappear."
    )
    return start, mass_translate_source.index("\n}", start)


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


def test_no_key_presence_test_exists_outside_the_shared_evidence_helper(mass_translate_source):
    assert_in_operator_confined_to_evidence_helper(mass_translate_source)


# A consume-site guard is the natural home for a reintroduction, so every
# mutant below is spliced into ledgerWriteSucceeded() -- code context,
# outside hasFailureEvidence(). The shipped template is never modified.
_GUARD_BODY_ANCHOR = "function ledgerWriteSucceeded(raw) {\n"


@pytest.mark.parametrize(
    "reintroduced",
    [
        # The shape #289 ACTUALLY had at two of its three sites, and the one
        # the previous enumerate-the-spellings form of this lock missed.
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
    ],
)
def test_presence_test_lock_catches_a_reintroduction_in_any_spelling(
    mass_translate_source, reintroduced,
):
    assert _GUARD_BODY_ANCHOR in mass_translate_source, (
        "ledgerWriteSucceeded() no longer opens with the anchored signature "
        "these RED-proof mutants splice into -- re-anchor them, do not delete them"
    )
    mutated = mass_translate_source.replace(
        _GUARD_BODY_ANCHOR, _GUARD_BODY_ANCHOR + reintroduced + "\n", 1
    )
    with pytest.raises(AssertionError, match="#289 class regression"):
        assert_in_operator_confined_to_evidence_helper(mutated)


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
    ],
)
def test_presence_test_lock_ignores_the_idiom_inside_a_comment_or_a_prompt_string(
    mass_translate_source, carrier,
):
    """The other half of the RED proof: the lock reads CODE, not the file. A
    check that fired on the raw source would be unusable against a template
    this comment- and prose-heavy, and would tempt the next author to loosen
    it back into a spelling blacklist."""
    mutated = mass_translate_source.replace(
        _GUARD_BODY_ANCHOR, _GUARD_BODY_ANCHOR + carrier + "\n", 1
    )
    assert_in_operator_confined_to_evidence_helper(mutated)


def assert_every_consume_site_guard_routes_through_helper(mass_translate_source: str) -> None:
    """The lock above is satisfiable by simply DELETING a check, which would
    trade a false-reject for a false-green. Pin the other side, from two
    directions: the three guards that exist today must still consult the
    shared judgement (a named floor, which also notices if one of them is
    deleted outright), AND -- since that enumeration cannot see a fourth
    guard -- so must any OTHER function that consults hasOnlyKeys().

    hasOnlyKeys() is the structural marker, not a naming convention: its only
    argument is an object an agent returned, and every caller is therefore a
    consume site that owes the same evidence judgement. A fourth guard that
    remembered its allowed-key check but forgot its evidence check is exactly
    the shape #289 had, and is invisible to a hand-kept list of three names.
    """
    named_guards = ("ledgerWriteSucceeded", "ledgerMergeSucceeded", "artifactCheckMatched")
    spans = _function_spans(mass_translate_source)
    for guard in named_guards:
        assert guard in spans, (
            f"{guard}() is no longer declared in the template -- if a "
            "consume-site guard was renamed or removed, re-anchor this floor "
            "deliberately rather than dropping the name."
        )
        start, end = spans[guard]
        assert "hasFailureEvidence(" in mass_translate_source[start:end], (
            f"{guard}() no longer calls hasFailureEvidence() -- the #289 "
            "class lock above would then pass vacuously, with the guard "
            "having no failure-evidence check at all."
        )

    unjudged = sorted(
        name for name, (start, end) in spans.items()
        if name != "hasOnlyKeys"
        and "hasOnlyKeys(" in mass_translate_source[start:end]
        and "hasFailureEvidence(" not in mass_translate_source[start:end]
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


@pytest.mark.parametrize(
    "mutate,expected_message",
    [
        # The named floor: an existing guard drops its evidence check.
        pytest.param(
            lambda s: s.replace(
                "  if (hasFailureEvidence(art, REVIEW_ARTIFACT_EVIDENCE_KEYS)) return false;\n",
                "",
                1,
            ),
            "artifactCheckMatched.. no longer calls hasFailureEvidence",
            id="named-guard-drops-its-check",
        ),
        # The derived half: a FOURTH guard the enumeration cannot see, which
        # remembered its allowed-key check and forgot its evidence check.
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
            "unjudged consume site",
            id="fourth-guard-skips-the-evidence-check",
        ),
    ],
)
def test_guard_routing_lock_catches_an_unjudged_consume_site(
    mass_translate_source, mutate, expected_message,
):
    mutated = mutate(mass_translate_source)
    assert mutated != mass_translate_source, (
        "the mutation did not apply -- its anchor text has moved, so this RED "
        "proof would pass without ever exercising the lock"
    )
    with pytest.raises(AssertionError, match=expected_message):
        assert_every_consume_site_guard_routes_through_helper(mutated)


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
            end = _quoted_literal_end(literal_text, i)
            token = literal_text[i + 1:end - 1]
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


def _function_spans(mass_translate_source: str) -> dict:
    """{name: (start, end)} for every top-level `function name(...)` in the
    template -- same column-0 `\\n}` terminator convention as
    _function_body_span(). Declarations are matched against the CODE
    projection so a `// function foo(` in a comment cannot invent one."""
    code = _js_code_only(mass_translate_source)
    spans = {}
    for m in re.finditer(r"^(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(", code, re.M):
        spans[m.group(1)] = (m.start(), mass_translate_source.index("\n}", m.start()))
    return spans


def _call_argument_texts(code: str, open_paren: int) -> list:
    """The comma-separated argument texts of the call whose `(` is at
    `open_paren`. Runs on the CODE projection, where string literals are
    already blanked, so a comma inside a string cannot split an argument."""
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


def _evidence_key_consts_passed_to_helper(mass_translate_source: str) -> set:
    """The const NAMES the template actually passes as hasFailureEvidence()'s
    `evidenceKeys` argument, read off the call sites."""
    code = _js_code_only(mass_translate_source)
    names = set()
    for m in re.finditer(r"\bhasFailureEvidence\s*\(", code):
        if code[:m.start()].rstrip().endswith("function"):
            continue  # the declaration itself
        args = _call_argument_texts(code, m.end() - 1)
        assert len(args) == 2, (
            f"hasFailureEvidence() called with {len(args)} arguments at line "
            f"{_line_of(mass_translate_source, m.start())}, expected 2"
        )
        evidence_arg = args[1].strip()
        assert re.fullmatch(r"[A-Za-z_$][\w$]*", evidence_arg), (
            "hasFailureEvidence()'s evidenceKeys argument at line "
            f"{_line_of(mass_translate_source, m.start())} is {evidence_arg!r}, "
            "not a bare const name. Pass a named const -- an inline array "
            "literal is invisible to the NO_FAILURE_EVIDENCE coverage check "
            "below, which is the whole point of that check."
        )
        names.add(evidence_arg)
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
    mutated = mass_translate_source.replace(
        'const FAILURE_EVIDENCE_KEYS = ["error", "exit_code", "stderr"];',
        'const FAILURE_EVIDENCE_KEYS = ["error", "exit_code", "stderr", "timed_out"];',
        1,
    ).replace(
        "const NO_FAILURE_EVIDENCE = { error: isEmptyString,",
        "const NO_FAILURE_EVIDENCE = { timed_out: isEmptyString, error: isEmptyString,",
        1,
    )
    assert "timed_out: isEmptyString" in mutated, "the table mutation did not apply"
    assert_every_evidence_key_has_a_table_row(mutated)
