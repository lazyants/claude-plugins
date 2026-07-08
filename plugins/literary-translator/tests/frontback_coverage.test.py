"""tests/frontback_coverage.test.py -- tests for scripts/final_audit.py's
frontback coverage report (SKILL.md's W7 "Frontback coverage report" /
W8 sections; final-audit-summary.schema.json's `frontback_coverage` field).

Scope, per the plugin's own build spec: this mechanism is NEW plugin
hardening, never run at scale -- the real historiettes-t3 project's own PLAN
document stated the intent but the real project actually handled frontback
via a separate, hand-maintained `frontmatter_ru.json` file entirely outside
the ledger/review pipeline (manifest.json's `frontback` key is separate from
`segments`, and the real project's `ledger.json` has zero `FRONTBACK:*`
keys). There is no proven reference implementation to port from here -- this
file is the first real exercise of `build_frontback_coverage()` and its
integration into `final_audit.py`'s `main()`.

What this covers (see final_audit.py's own "Frontback coverage report"
section and SKILL.md's W7 spec):

  1. `build_frontback_coverage()` reads `manifest.json`'s `frontback[]`
     inventory directly and emits exactly one report entry per inventory
     entry, in the SAME order, unconditionally present as an array (empty
     for a `frontback` key that is absent OR an explicit `[]` -- e.g. a
     plain_text source with no frontmatter concept -- never omitted from
     the final summary).
  2. A `translate`-decision entry reports its OWN convergence status,
     cross-referenced from the SAME `select_segments.py` classification
     computed for the whole-project completeness gate (never independently
     re-derived, never re-invoking select_segments.py a second time).
  3. A `regenerate`/`omit`-decision entry is reported by DECISION ALONE --
     `status` is always `null`, even if a classification happens to exist
     under that same id (proving the code branches on `decision`, not on
     "was this id classified").
  4. Non-dict entries inside `frontback[]` are skipped, never crash the
     report.
  5. The whole report is advisory/informational only: varying what a
     frontback entry's cross-referenced status is (including "bad" states
     like `human_escalation`) never flips `hard_failures` or this script's
     own exit code -- only the two HARD checks (coverage, stale-review) do
     that. This is verified both by an unrelated hard failure coexisting
     with clean frontback entries, and by a "bad" frontback classification
     coexisting with an otherwise entirely clean run.

Two test layers:

  * Unit-level (section 1): the real `final_audit.py` is loaded directly via
    `importlib` (its module-level `sys.path.insert(0, SCRIPTS_DIR)` +
    `import validate_draft as vd` / `import bootstrap_names as bn` resolve
    fine against the plugin's real `assets/scripts/` since the file is
    loaded from its real location) and `build_frontback_coverage()` is
    called directly against hand-built `manifest.json` fixtures and
    `classification_by_seg` dicts -- isolating the report's own
    cross-referencing logic from the rest of `main()`'s pipeline.

  * Integration-level (section 2): following this plugin's established
    `make_durable_root` convention (see `select_segments.test.py`,
    `validate_draft.test.py`), an isolated `tmp_path` durable_root is built
    with the REAL `final_audit.py`, `validate_draft.py`,
    `bootstrap_names.py`, `select_segments.py`, and `ledger_merge.py` copied
    in, plus the same small `cache_key.py` stub `select_segments.test.py`
    uses (its own 15-field hashing algorithm has its own dedicated test
    file, `ledger_composite_key.test.py` -- out of scope here). Every
    integration test invokes `python3 {durable_root}/scripts/final_audit.py`
    exactly as it is invoked in production, over a real manifest.json plus
    real ledger fragments/segpacks/drafts, so the ACTUAL end-to-end
    `select_segments.py` -> `final_audit.py` integration is exercised, not
    a mocked stand-in for it.

IMPORTANT -- a genuine contract mismatch this file's tests surface: the real,
shipped `select_segments.py` reports each segment's classification as a
NESTED OBJECT, `{"category": "<one of the six values>", ...extra fields}`
(verified directly -- see `select_segments.test.py`'s own
`test_full_classification_taxonomy_and_report`, e.g.
`classification["seg01_reusable"] == {"category": "reusable"}`). But
`final_audit.py`'s own module docstring ("select_segments.py JSON contract")
documents the OPPOSITE: `"classification": {SEG: CATEGORY, ...}` with CATEGORY
already a bare string. `build_frontback_coverage()` reads
`classification_by_seg.get(fb_id)` with no unwrapping, so against the REAL
`select_segments.py` contract this ships the raw nested object as `status`
for every resolved `translate`-decision entry -- violating both this
script's own documented contract AND `final-audit-summary.schema.json`'s
explicit `"status": {"type": ["string", "null"]}` (with a conditional
requiring `status` be a STRING specifically when `decision == "translate"`).
The dedicated tests below that catch this
(`test_unit_translate_status_is_the_plain_classification_category_string`,
`test_integration_translate_status_is_the_plain_category_string_not_a_raw_object`)
are EXPECTED to fail against the current script -- this is a genuine defect
in `build_frontback_coverage()` (it should extract
`classification_by_seg.get(fb_id, {}).get("category")`, not the whole dict),
not a defect in these tests. Every other test in this file passes against
the current script.
"""
import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
ASSETS_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets"
SCRIPTS_SRC_DIR = ASSETS_DIR / "scripts"
SCHEMAS_SRC = ASSETS_DIR / "schemas"

FINAL_AUDIT_SRC = SCRIPTS_SRC_DIR / "final_audit.py"
VALIDATE_DRAFT_SRC = SCRIPTS_SRC_DIR / "validate_draft.py"
BOOTSTRAP_NAMES_SRC = SCRIPTS_SRC_DIR / "bootstrap_names.py"
SELECT_SEGMENTS_SRC = SCRIPTS_SRC_DIR / "select_segments.py"
LEDGER_MERGE_SRC = SCRIPTS_SRC_DIR / "ledger_merge.py"

for _p in (
    FINAL_AUDIT_SRC,
    VALIDATE_DRAFT_SRC,
    BOOTSTRAP_NAMES_SRC,
    SELECT_SEGMENTS_SRC,
    LEDGER_MERGE_SRC,
    SCHEMAS_SRC,
):
    assert _p.exists(), f"required fixture source not found: {_p}"


# ---------------------------------------------------------------------------
# Section 0: shared fixture building blocks
# ---------------------------------------------------------------------------

# A full, schema-shaped 15-field cache_key dict -- every field derived from
# `seed` so two different seeds mismatch in every field simultaneously.
CACHE_KEY_FIELDS = [
    "input_sha1",
    "style_contract_hash",
    "used_terms_hash",
    "pipeline_version",
    "schema_hash",
    "prompt_hash",
    "agent_config_hash",
    "profile_semantics_hash",
    "particle_config_hash",
    "source_extraction_hash",
    "source_input_hash",
    "derivation_bundle_hash",
    "verse_map_hash",
    "note_map_hash",
    "plugin_bundle_hash",
]


def make_cache_key(seed):
    return {field: f"{field}-{seed}" for field in CACHE_KEY_FIELDS}


# Fixture stand-in for the real cache_key.py -- same `--seg <id>` -> JSON
# stdout interface, sourced from a test-controlled lookup file instead of
# real profile.yml/canon.json/segpack machinery. Same stub
# select_segments.test.py/ledger_merge.test.py use: this script's own
# hashing algorithm has its own dedicated test file
# (ledger_composite_key.test.py), out of scope for this one.
FAKE_CACHE_KEY_PY = """#!/usr/bin/env python3
import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DURABLE_ROOT = HERE.parent
KEYS_PATH = DURABLE_ROOT / "test_fixture_cache_keys.json"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seg")
    parser.add_argument("--field")
    args = parser.parse_args()
    if not args.seg:
        sys.stderr.write("fake cache_key.py: test stub requires --seg\\n")
        return 1
    data = json.loads(KEYS_PATH.read_text(encoding="utf-8"))
    if args.seg not in data:
        sys.stderr.write(f"fake cache_key.py: no fixture key for {args.seg}\\n")
        return 1
    print(json.dumps(data[args.seg]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
"""

# Minimal but complete profile.yml -- exactly the fields
# validate_draft.py's ProfileConfig requires (verse_policy.mode,
# footnotes.apparatus_policy, validation.untranslated_sentinel).
# verse_policy.mode=skip + apparatus_policy=translate_all keeps every
# segment's own coverage check trivially satisfiable with zero
# blocks/footnotes/verses. Deliberately NO source.language.particle_config --
# final_audit.py's main() catches the resulting KeyError and just skips the
# foreign-remainder WARN check (see its own try/except), which is
# irrelevant to this file's frontback-coverage scope.
DEFAULT_PROFILE = {
    "verse_policy": {"mode": "skip"},
    "footnotes": {"apparatus_policy": "translate_all"},
    "validation": {"untranslated_sentinel": "[TODO-UNTRANSLATED]"},
}


def make_durable_root(tmp_path):
    """Builds an isolated durable_root: copies the REAL final_audit.py,
    validate_draft.py, bootstrap_names.py, select_segments.py, and
    ledger_merge.py into {root}/scripts/, installs the fake cache_key.py
    stub alongside them, copies the REAL assets/schemas/*.schema.json files,
    writes profile.yml + the `.literary-translator-root.json` ownership
    marker (validate_draft.py's own `load_profile()` resolution mechanism),
    and creates empty runs/ledger.d/ and segments/ directories.
    """
    root = tmp_path / "durable_root"
    scripts_dir = root / "scripts"
    scripts_dir.mkdir(parents=True)
    shutil.copy2(FINAL_AUDIT_SRC, scripts_dir / "final_audit.py")
    shutil.copy2(VALIDATE_DRAFT_SRC, scripts_dir / "validate_draft.py")
    shutil.copy2(BOOTSTRAP_NAMES_SRC, scripts_dir / "bootstrap_names.py")
    shutil.copy2(SELECT_SEGMENTS_SRC, scripts_dir / "select_segments.py")
    shutil.copy2(LEDGER_MERGE_SRC, scripts_dir / "ledger_merge.py")
    (scripts_dir / "cache_key.py").write_text(FAKE_CACHE_KEY_PY, encoding="utf-8")

    schemas_dir = root / "schemas"
    shutil.copytree(SCHEMAS_SRC, schemas_dir)

    (root / "runs" / "ledger.d").mkdir(parents=True)
    (root / "segments").mkdir()

    profile_path = root / "profile.yml"
    profile_path.write_text(yaml.safe_dump(DEFAULT_PROFILE, sort_keys=False), encoding="utf-8")
    marker = {"owner_profile_path": str(profile_path)}
    (root / ".literary-translator-root.json").write_text(json.dumps(marker), encoding="utf-8")

    return root


def write_manifest(root, segments, frontback=None):
    """Writes manifest.json. `frontback` is omitted entirely from the JSON
    object when None (distinct from an explicit empty list -- both are
    exercised as separate test cases below)."""
    manifest = {"segments": [{"seg": s} for s in segments]}
    if frontback is not None:
        manifest["frontback"] = frontback
    (root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
    )


def write_fixture_cache_keys(root, mapping):
    (root / "test_fixture_cache_keys.json").write_text(
        json.dumps(mapping, ensure_ascii=False), encoding="utf-8"
    )


def write_fragment(root, seg, record):
    frag_path = root / "runs" / "ledger.d" / f"{seg}.json"
    frag_path.write_text(json.dumps(record, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    return frag_path


def converged_fragment(cache_key, reviewed_draft_sha1, rounds=1):
    return {
        "timestamp": "2026-01-01T00:00:00Z",
        "status": "converged",
        "rounds": rounds,
        "cache_key": cache_key,
        "n_blocks": 0,
        "n_footnotes": 0,
        "n_verses": 0,
        "reviewed_draft_sha1": reviewed_draft_sha1,
    }


def blocked_fragment(reason="review-null"):
    return {"timestamp": "2026-01-01T00:00:00Z", "status": "blocked", "reason": reason}


def write_minimal_segment(root, seg):
    """Writes a trivially-clean segpack_{seg}.json ({}) and {seg}.draft.json
    (zero blocks/footnotes/verses/names/notes) -- passes validate_draft.py's
    coverage check unconditionally (empty key sets are trivially 1:1), which
    keeps a converged fixture segment's OWN hard-check status ("clean")
    fully decoupled from whatever this test is actually trying to exercise
    about the frontback coverage report. Returns the exact sha1 hex digest
    of the draft bytes written, for use as a ledger fragment's
    `reviewed_draft_sha1`.
    """
    segments_dir = root / "segments"
    (segments_dir / f"segpack_{seg}.json").write_text("{}", encoding="utf-8")
    draft_obj = {
        "seg": seg,
        "blocks": {},
        "footnotes": {},
        "verses": {},
        "names": [],
        "notes": [],
    }
    draft_bytes = json.dumps(draft_obj, ensure_ascii=False, sort_keys=True).encode("utf-8")
    (segments_dir / f"{seg}.draft.json").write_bytes(draft_bytes)
    return hashlib.sha1(draft_bytes).hexdigest()


def run_final_audit(root, timeout=60):
    return subprocess.run(
        [sys.executable, str(root / "scripts" / "final_audit.py")],
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=str(root),
    )


def parse_stdout(proc):
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    assert len(lines) == 1, f"expected exactly one JSON line on stdout, got:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    return json.loads(lines[0])


# ---------------------------------------------------------------------------
# Section 1: unit-level tests of build_frontback_coverage() itself, loaded
# directly from the real final_audit.py via importlib (matching this
# plugin's established pattern for standalone, non-package scripts -- see
# bootstrap_names.test.py). MANIFEST_PATH is monkeypatched per test; every
# other module-level constant (SCRIPTS_DIR/DURABLE_ROOT/etc, and the
# `import validate_draft as vd` / `import bootstrap_names as bn` sibling
# imports final_audit.py performs at its own module scope) resolves against
# the real assets/scripts directory, which is harmless here since
# build_frontback_coverage() never touches any of them.
# ---------------------------------------------------------------------------

def _load_final_audit_module():
    spec = importlib.util.spec_from_file_location("final_audit_under_test", FINAL_AUDIT_SRC)
    assert spec is not None and spec.loader is not None, f"could not load spec for {FINAL_AUDIT_SRC}"
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


FA = _load_final_audit_module()


def _set_manifest(tmp_path, monkeypatch, manifest_obj):
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest_obj, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(FA, "MANIFEST_PATH", manifest_path)
    return manifest_path


def test_unit_frontback_key_absent_yields_empty_list(tmp_path, monkeypatch):
    _set_manifest(tmp_path, monkeypatch, {"segments": []})
    assert FA.build_frontback_coverage({}) == []


def test_unit_frontback_explicit_empty_array_yields_empty_list(tmp_path, monkeypatch):
    _set_manifest(tmp_path, monkeypatch, {"segments": [], "frontback": []})
    assert FA.build_frontback_coverage({}) == []


def test_unit_regenerate_and_omit_report_by_decision_alone(tmp_path, monkeypatch):
    """regenerate/omit entries are reported by decision ALONE -- status is
    always null, even when a classification happens to exist under that
    same id (proving the branch is on `decision`, never on presence in
    classification_by_seg)."""
    _set_manifest(
        tmp_path,
        monkeypatch,
        {
            "segments": [],
            "frontback": [
                {"id": "FRONTBACK:back_regen", "decision": "regenerate"},
                {"id": "FRONTBACK:back_omit", "decision": "omit"},
            ],
        },
    )
    # Deliberately present under both ids -- must be ignored.
    classification_by_seg = {
        "FRONTBACK:back_regen": {"category": "reusable"},
        "FRONTBACK:back_omit": {"category": "stale", "stale_reason": ["draft_sha1_mismatch"]},
    }
    result = FA.build_frontback_coverage(classification_by_seg)
    assert result == [
        {"id": "FRONTBACK:back_regen", "decision": "regenerate", "status": None},
        {"id": "FRONTBACK:back_omit", "decision": "omit", "status": None},
    ]


def test_unit_translate_id_missing_from_classification_yields_null_status(tmp_path, monkeypatch):
    """A translate-decision id that select_segments.py never classified at
    all (e.g. a bookkeeping mistake -- the id was never added to
    manifest.json's own segments[]) resolves gracefully to null, not a
    KeyError."""
    _set_manifest(
        tmp_path,
        monkeypatch,
        {"segments": [], "frontback": [{"id": "FRONTBACK:never_dispatched", "decision": "translate"}]},
    )
    result = FA.build_frontback_coverage({})
    assert result == [{"id": "FRONTBACK:never_dispatched", "decision": "translate", "status": None}]


def test_unit_order_and_multiple_entries_preserved(tmp_path, monkeypatch):
    _set_manifest(
        tmp_path,
        monkeypatch,
        {
            "segments": [],
            "frontback": [
                {"id": "A", "decision": "translate"},
                {"id": "B", "decision": "regenerate"},
                {"id": "C", "decision": "omit"},
                {"id": "D", "decision": "translate"},
            ],
        },
    )
    classification_by_seg = {
        "A": {"category": "stale", "stale_reason": ["cache_key_mismatch"]},
        "D": {"category": "not_started"},
    }
    result = FA.build_frontback_coverage(classification_by_seg)
    assert [item["id"] for item in result] == ["A", "B", "C", "D"]
    assert result[1] == {"id": "B", "decision": "regenerate", "status": None}
    assert result[2] == {"id": "C", "decision": "omit", "status": None}
    # Loose check only (passes regardless of the dict-vs-string defect
    # documented at module top): a translate entry with a matching
    # classification is genuinely cross-referenced, not defaulted to null.
    assert result[0]["status"] is not None
    assert result[3]["status"] is not None


def test_unit_non_dict_frontback_entries_are_skipped(tmp_path, monkeypatch):
    _set_manifest(
        tmp_path,
        monkeypatch,
        {
            "segments": [],
            "frontback": ["not-a-dict", {"id": "ok", "decision": "omit"}, 42, None, ["x"]],
        },
    )
    result = FA.build_frontback_coverage({})
    assert result == [{"id": "ok", "decision": "omit", "status": None}]


def test_unit_unrecognized_decision_value_never_triggers_lookup(tmp_path, monkeypatch):
    """Only the literal string "translate" ever triggers a
    classification_by_seg lookup -- any other decision value (including one
    outside the translate/regenerate/omit enum) is treated exactly like
    regenerate/omit: status forced to null."""
    _set_manifest(
        tmp_path,
        monkeypatch,
        {"segments": [], "frontback": [{"id": "X", "decision": "some-other-value"}]},
    )
    classification_by_seg = {"X": {"category": "reusable"}}
    result = FA.build_frontback_coverage(classification_by_seg)
    assert result == [{"id": "X", "decision": "some-other-value", "status": None}]


def test_unit_manifest_missing_is_fatal(tmp_path, monkeypatch):
    monkeypatch.setattr(FA, "MANIFEST_PATH", tmp_path / "does_not_exist.json")
    with pytest.raises(SystemExit) as exc_info:
        FA.build_frontback_coverage({})
    assert exc_info.value.code == 2


def test_unit_manifest_not_an_object_is_fatal(tmp_path, monkeypatch):
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    monkeypatch.setattr(FA, "MANIFEST_PATH", manifest_path)
    with pytest.raises(SystemExit) as exc_info:
        FA.build_frontback_coverage({})
    assert exc_info.value.code == 2


def test_unit_translate_status_is_the_plain_classification_category_string(tmp_path, monkeypatch):
    """Per SKILL.md's W7 spec ("a translate-decision element reports its OWN
    convergence status") and final-audit-summary.schema.json's
    `frontback_coverage[].status` field (typed `["string", "null"]`, with a
    conditional that specifically requires a STRING when decision ==
    "translate"), the reported status for a resolved translate-decision
    entry must be the plain six-value category label (e.g. "reusable"),
    matching the SAME label vocabulary `completeness_counts` uses elsewhere
    in this exact same JSON summary.

    The real, shipped select_segments.py reports a segment's classification
    as {"category": "<label>", ...}, never a bare string (verified directly
    -- see select_segments.test.py's own
    test_full_classification_taxonomy_and_report). That is the exact shape
    final_audit.py's own run_completeness_gate() passes straight through as
    classification_by_seg.

    EXPECTED TO FAIL against the current final_audit.py: build_frontback_coverage()
    does `status = classification_by_seg.get(fb_id)` with no unwrapping, so
    against real production data `status` ends up as the raw
    {"category": "reusable"} object, not the string "reusable" -- a genuine
    defect (documented at this file's own module docstring), not a defect in
    this test.
    """
    _set_manifest(
        tmp_path,
        monkeypatch,
        {"segments": [], "frontback": [{"id": "FRONTBACK:front01", "decision": "translate"}]},
    )
    classification_by_seg = {"FRONTBACK:front01": {"category": "reusable"}}
    result = FA.build_frontback_coverage(classification_by_seg)
    assert result == [{"id": "FRONTBACK:front01", "decision": "translate", "status": "reusable"}]


# ---------------------------------------------------------------------------
# Section 2: integration-level tests -- the real final_audit.py subprocess,
# against a real select_segments.py/ledger_merge.py/validate_draft.py/
# bootstrap_names.py pipeline over an isolated durable_root fixture.
# ---------------------------------------------------------------------------

FRONT_OK = "FRONTBACK:front_ok"
BODY_NOT_STARTED = "seg_body_never_touched"
BROKEN_REVIEW = "seg_broken_stale_review"
REGEN_ID = "FRONTBACK:back_regen"
OMIT_ID = "FRONTBACK:back_omit"


def setup_mixed_scenario(tmp_path):
    """One manifest exercising all three frontback decisions plus an
    UNRELATED hard failure (BROKEN_REVIEW, a plain body segment never
    mentioned in frontback[] at all):

      - FRONT_OK: a translate-decision frontback element that is ALSO a
        first-class manifest segments[] member (per SKILL.md's own spec --
        "A translate-decision element gets its OWN entry in
        manifest.json's segments[]"), converged with a matching cache key
        and matching draft sha1 -> select_segments.py classifies it
        "reusable"; its own segpack/draft are clean -> zero hard-check
        failures.
      - BODY_NOT_STARTED: an ordinary body segment, no ledger fragment at
        all -> "not_started". Not referenced by frontback[] at all.
      - BROKEN_REVIEW: an ordinary body segment, converged, with a matching
        cache key but a WRONG recorded reviewed_draft_sha1 -> triggers
        final_audit.py's own hard_check_stale_review (1 failure) AND
        select_segments.py's independent classification of the exact same
        underlying fact as "stale"/draft_sha1_mismatch. Not referenced by
        frontback[] at all -- proves the frontback report is fully
        decoupled from WHY a hard failure fired elsewhere.
      - REGEN_ID / OMIT_ID: frontback-only ids (never in segments[] at all,
        matching the real construction invariant: regenerate/omit-decision
        elements do NOT join segments[]).
    """
    root = make_durable_root(tmp_path)
    write_manifest(
        root,
        [FRONT_OK, BODY_NOT_STARTED, BROKEN_REVIEW],
        frontback=[
            {"id": FRONT_OK, "decision": "translate"},
            {"id": REGEN_ID, "decision": "regenerate"},
            {"id": OMIT_ID, "decision": "omit"},
        ],
    )

    current_key = make_cache_key("current")
    fixture_keys = {}

    sha1_front_ok = write_minimal_segment(root, FRONT_OK)
    fixture_keys[FRONT_OK] = current_key
    write_fragment(root, FRONT_OK, converged_fragment(dict(current_key), sha1_front_ok))

    sha1_broken = write_minimal_segment(root, BROKEN_REVIEW)
    fixture_keys[BROKEN_REVIEW] = current_key
    write_fragment(
        root,
        BROKEN_REVIEW,
        # cache key matches current exactly (isolates the draft-sha1 gate);
        # reviewed_draft_sha1 is a deliberately wrong 40-hex-digit stand-in.
        converged_fragment(dict(current_key), "0" * 40),
    )
    assert sha1_broken != "0" * 40  # sanity: the mismatch is real, not accidental

    # BODY_NOT_STARTED: no fragment at all, no segpack/draft needed (never
    # converged, so final_audit.py's hard checks never look at it).

    write_fixture_cache_keys(root, fixture_keys)
    return root


def test_integration_frontback_coverage_structure_and_order(tmp_path):
    root = setup_mixed_scenario(tmp_path)
    proc = run_final_audit(root)
    payload = parse_stdout(proc)

    assert "frontback_coverage" in payload
    coverage = payload["frontback_coverage"]
    assert [item["id"] for item in coverage] == [FRONT_OK, REGEN_ID, OMIT_ID]
    assert [item["decision"] for item in coverage] == ["translate", "regenerate", "omit"]

    # regenerate/omit: reported by decision alone.
    assert coverage[1] == {"id": REGEN_ID, "decision": "regenerate", "status": None}
    assert coverage[2] == {"id": OMIT_ID, "decision": "omit", "status": None}

    # translate: genuinely cross-referenced (not defaulted to null) -- loose
    # check, passes regardless of the dict-vs-string defect documented at
    # this file's module docstring.
    assert coverage[0]["status"] is not None

    # Human-readable stderr report mirrors the structured summary.
    assert "FRONTBACK COVERAGE (3 entries):" in proc.stderr
    assert f"- {REGEN_ID} decision=regenerate status=None" in proc.stderr
    assert f"- {OMIT_ID} decision=omit status=None" in proc.stderr


def test_integration_translate_status_is_the_plain_category_string_not_a_raw_object(tmp_path):
    """Full end-to-end equivalent of the unit-level bug-catching test above:
    against a REAL select_segments.py run (not a hand-built dict), the
    resolved translate-decision status should be the plain string
    "reusable" per final-audit-summary.schema.json. EXPECTED TO FAIL against
    the current final_audit.py -- see this file's module docstring."""
    root = setup_mixed_scenario(tmp_path)
    proc = run_final_audit(root)
    payload = parse_stdout(proc)
    front_entry = next(item for item in payload["frontback_coverage"] if item["id"] == FRONT_OK)
    assert front_entry["status"] == "reusable"


def test_integration_frontback_report_never_gates_exit_code_on_unrelated_hard_failure(tmp_path):
    """hard_failures/exit code are gated purely by the two hard checks
    (coverage, stale-review) -- BROKEN_REVIEW's stale-review defect is
    entirely unrelated to any frontback id, yet still fully accounts for
    the whole-run failure; the frontback report itself contributes nothing
    to hard_failures regardless of what it shows."""
    root = setup_mixed_scenario(tmp_path)
    proc = run_final_audit(root)
    assert proc.returncode == 1, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    payload = parse_stdout(proc)

    assert payload["coverage_failures"] == 0
    assert payload["stale_review_failures"] == 1
    assert payload["hard_failures"] == 1
    # Rollup invariant, procedurally enforced by final_audit.py itself.
    assert payload["hard_failures"] == payload["coverage_failures"] + payload["stale_review_failures"]

    # The frontback report is present and untouched by the unrelated
    # failure -- three entries, same shape as the structural test above.
    assert len(payload["frontback_coverage"]) == 3
    assert payload["frontback_coverage"][1]["status"] is None
    assert payload["frontback_coverage"][2]["status"] is None


def test_integration_frontback_report_never_gates_exit_code_on_its_own_bad_status(tmp_path):
    """The inverse direction of the advisory-only guarantee: a
    translate-decision frontback element that is ITSELF unresolved
    (human_escalation, via a blocked ledger fragment) must not gate the
    run -- with every other check clean, hard_failures/exit code stay 0."""
    root = make_durable_root(tmp_path)
    seg = "FRONTBACK:front_blocked"
    filler = "seg_filler_not_started"
    # `filler` (not_started, no fragment at all) exists purely so
    # select_segments.py's own emitted SEGS is non-empty -- a manifest whose
    # ONLY segment is human_escalation-classified would otherwise make
    # select_segments.py itself FATAL ("emitted SEGS is empty -- refusing
    # to no-op silently"), an unrelated whole-project-completeness-gate
    # concern out of this file's frontback-coverage scope (see
    # final_audit.test.py for that gate's own dedicated tests).
    write_manifest(root, [seg, filler], frontback=[{"id": seg, "decision": "translate"}])
    write_fragment(root, seg, blocked_fragment(reason="review-null"))
    # A `blocked` fragment's own status != "converged", so final_audit.py's
    # load_converged_fragments() excludes it entirely from both hard checks
    # -- no segpack/draft/cache-key fixture needed for it at all.
    write_fixture_cache_keys(root, {})

    proc = run_final_audit(root)
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    payload = parse_stdout(proc)
    assert payload["coverage_failures"] == 0
    assert payload["stale_review_failures"] == 0
    assert payload["hard_failures"] == 0

    assert payload["frontback_coverage"] == [
        {
            "id": seg,
            "decision": "translate",
            # Loose check only (see module docstring's documented defect):
            # a human_escalation classification is genuinely non-null.
            "status": payload["frontback_coverage"][0]["status"],
        }
    ]
    assert payload["frontback_coverage"][0]["status"] is not None
    # completeness_counts (a SEPARATE, already-informational gate) does
    # reflect the escalation -- but that is project_complete's job, never
    # hard_failures'/the exit code's.
    assert payload["completeness_counts"]["human_escalation"] == 1
    assert payload["project_complete"] is False


def test_integration_frontback_key_absent_yields_empty_array_unconditionally(tmp_path):
    root = make_durable_root(tmp_path)
    seg = "seg_only"
    filler = "seg_filler_not_started"
    # `filler` keeps select_segments.py's own default (no --allow-empty) run
    # from emitting an empty SEGS list, which would otherwise FATAL it --
    # see the comment in test_integration_frontback_report_never_gates_exit_code_on_its_own_bad_status.
    write_manifest(root, [seg, filler])  # no frontback key at all
    key = make_cache_key("only")
    sha1 = write_minimal_segment(root, seg)
    write_fragment(root, seg, converged_fragment(dict(key), sha1))
    write_fixture_cache_keys(root, {seg: key})

    proc = run_final_audit(root)
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    payload = parse_stdout(proc)
    assert "frontback_coverage" in payload
    assert payload["frontback_coverage"] == []
    assert "FRONTBACK COVERAGE (0 entries):" in proc.stderr


def test_integration_frontback_explicit_empty_array_yields_empty_array(tmp_path):
    root = make_durable_root(tmp_path)
    seg = "seg_only"
    filler = "seg_filler_not_started"
    write_manifest(root, [seg, filler], frontback=[])
    key = make_cache_key("only")
    sha1 = write_minimal_segment(root, seg)
    write_fragment(root, seg, converged_fragment(dict(key), sha1))
    write_fixture_cache_keys(root, {seg: key})

    proc = run_final_audit(root)
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    payload = parse_stdout(proc)
    assert payload["frontback_coverage"] == []


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
