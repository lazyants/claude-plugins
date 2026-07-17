"""tests/audit_unchanged_regression.test.py -- RFC #215 Phase 2 regression
proving `canon_adjudication_audit.py` (the persisted, machine-checkable
rollout gate; see its own module docstring) is BYTE-FOR-BYTE UNCHANGED by
the skeptic pass's presence: it neither reads nor is in any way perturbed
by `skeptic_triage.json` sitting in the durable root (contract A4 / plan
Part C: "the existing canon_adjudication_audit.py gate is unchanged
byte-for-byte -- no triage accounting inside it; no change to
blocking_count/gate_passed/exit").

This is a REGRESSION test, not a unit test of skeptic_report.py itself
(see skeptic_report.test.py for that): it runs the REAL, unmodified
canon_adjudication_audit.py CLI (staged exactly as
tests/canon_adjudication_audit_homonym_split.test.py stages it, via
tests/_senses_fixture.py::stage_consumer()) against the SAME category-5
fixture (a genuine 2-sense "Jean" split, missing verdict -> blocks) TWICE:
once with no skeptic_triage.json in the durable root, once with a
schema-valid, non-empty one sitting right next to canon.json/manifest.json/
canon_senses.json -- and asserts the summary JSON, the raw stdout bytes,
and the exit code are all IDENTICAL between the two runs.

NAMED MUTATION this guards against: any future edit to
canon_adjudication_audit.py that starts importing skeptic_constants,
reading skeptic_triage.json, or folding a triage-derived signal into
blocking_count/gate_passed (e.g. "escalate the gate when an adverse
skeptic finding exists") would make this test go from green to red the
moment that coupling is introduced -- proving the audit gate really is
unchanged by the skeptic pass's presence, not merely unedited by us today.
"""
import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_SRC_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets" / "scripts"
LANGUAGES_SRC_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets" / "languages"

SCRIPT_SRC = SCRIPTS_SRC_DIR / "canon_adjudication_audit.py"
assert SCRIPT_SRC.is_file(), f"canon_adjudication_audit.py not found at {SCRIPT_SRC}"
assert (LANGUAGES_SRC_DIR / "fr.json").is_file(), f"fr.json not found under {LANGUAGES_SRC_DIR}"

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))
from _senses_fixture import stage_consumer  # noqa: E402

# canon_adjudication_audit.py's own extra dependency closure beyond
# canon_senses.py (which stage_consumer() already brings) -- mirrors
# tests/canon_adjudication_audit_homonym_split.test.py's own
# _EXTRA_DEP_SCRIPTS exactly (same consumer script, same deps).
_EXTRA_DEP_SCRIPTS = ("bootstrap_names.py", "occ_index.py", "evidence_verify.py")
for _dep in _EXTRA_DEP_SCRIPTS:
    assert (SCRIPTS_SRC_DIR / _dep).is_file(), f"{_dep} not found at {SCRIPTS_SRC_DIR / _dep}"


def _load_module(name: str, path: Path, extra_sys_path: Path):
    """Used ONLY to compute real production spans for fixture construction
    (never to exercise canon_adjudication_audit.py itself -- that always
    happens via the real CLI subprocess, see run_audit() below)."""
    sys.path.insert(0, str(extra_sys_path))
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(extra_sys_path))


bn = _load_module("bn_for_audit_unchanged_test", SCRIPTS_SRC_DIR / "bootstrap_names.py", SCRIPTS_SRC_DIR)
oi = _load_module("oi_for_audit_unchanged_test", SCRIPTS_SRC_DIR / "occ_index.py", SCRIPTS_SRC_DIR)

FR_LANG = bn.load_language_config("fr.json", languages_dir=LANGUAGES_SRC_DIR)


def hex64(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def real_spans(source_form, block_text):
    return oi.production_occurrences(source_form, block_text, FR_LANG)


def make_sense(sense_id, disambiguator, block_text, char_start, char_end):
    context_start, context_end = 0, len(block_text)
    sha = hex64(block_text[context_start:context_end])
    return {
        "sense_id": sense_id, "disambiguator": disambiguator, "index_scope": "narrative",
        "evidence": {
            "block": "b1", "seg": None, "char_start": char_start, "char_end": char_end,
            "context_start": context_start, "context_end": context_end, "sha256": sha,
        },
    }


def two_jean_senses(block_text="Jean parla a Jean encore une fois."):
    """Same shape as canon_adjudication_audit_homonym_split.test.py's own
    two_jean_senses() -- a genuinely-valid 2-sense split, no verdict yet,
    so category 5 (homonym_split) fires as a missing_verdict blocker."""
    spans = real_spans("Jean", block_text)
    assert len(spans) == 2, f"expected 2 'Jean' occurrences, got {spans}"
    (s0, e0), (s1, e1) = spans
    senses = [
        make_sense("s1", "the baker", block_text, s0, e0),
        make_sense("s2", "the soldier", block_text, s1, e1),
    ]
    return block_text, senses


def write_manifest(root, blocks):
    (root / "manifest.json").write_text(json.dumps({"blocks": blocks}, ensure_ascii=False), encoding="utf-8")


def write_senses(root, entries_by_source_form):
    doc = {"schema_version": 1, "entries_by_source_form": entries_by_source_form}
    (root / "canon_senses.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")


def write_canon(root, entries):
    keyed = {e["source_form"]: e for e in entries}
    doc = {
        "entries": keyed, "review_queue": [],
        "generation_hashes": {"particle_config_hash": "test-hash", "derivation_bundle_hash": "test-hash"},
    }
    (root / "canon.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")


def entry(source_form, target, is_proper_name=True, basis="transliterated"):
    return {
        "source_form": source_form, "canonical_target_form": target,
        "is_proper_name": is_proper_name, "basis": basis,
    }


def write_skeptic_triage(root, run_id="regression-test-run"):
    """A schema-valid, NON-EMPTY skeptic_triage.json sitting directly in the
    durable root (its shipped default location, {durable_root}/
    skeptic_triage.json, per skeptic_constants.SKEPTIC_TRIAGE_FILENAME) --
    the ONE thing that differs between the two durable roots this test
    compares."""
    doc = {
        "schema_version": 1,
        "run_id": run_id,
        "records": [
            {
                "assignment_id": hex64("assignment:Jean"),
                "source_form": "Jean",
                "verdict": "adverse",
                "rationale": "a contradicting sentence was found",
                "evidence": {
                    "block": "b1", "seg": None, "char_start": 0, "char_end": 4,
                    "context_start": 0, "context_end": 10, "sha256": hex64("evidence-context"),
                },
                "evidence_coverage": {"cited": 1, "verified": 1},
            },
        ],
    }
    (root / "skeptic_triage.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")


def make_durable_root(root):
    stage_consumer(root, "canon_adjudication_audit.py")
    scripts_dir = root / "scripts"
    for dep in _EXTRA_DEP_SCRIPTS:
        shutil.copy2(SCRIPTS_SRC_DIR / dep, scripts_dir / dep)
    shutil.copytree(LANGUAGES_SRC_DIR, root / "languages")
    return root


def run_audit(root, *args, timeout=30):
    return subprocess.run(
        [sys.executable, str(root / "scripts" / "canon_adjudication_audit.py"), *args],
        capture_output=True, text=True, timeout=timeout,
    )


def parse_stdout(proc):
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    assert len(lines) == 1, f"expected exactly one stdout JSON line, got {len(lines)}:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
    return json.loads(lines[0])


def _without_generated_at(summary):
    """Every OTHER field compared exactly. `generated_at` is a wall-clock
    timestamp the audit stamps on every run regardless of skeptic_triage.json
    (two consecutive runs of the UNMODIFIED script a millisecond apart would
    already differ here) -- excluding only this one field, and asserting
    everything else matches exactly, is a STRONGER proof than a same-root
    identity check would be if it silently also tolerated some genuine
    skeptic-shaped drift; see canon-adjudication-audit-summary.schema.json
    for `generated_at`'s own type."""
    return {k: v for k, v in summary.items() if k != "generated_at"}


# ===========================================================================
# Both runs below share the SAME durable root -- adding skeptic_triage.json
# BETWEEN the two --check invocations, never a second separately-built root
# -- so canon_path/senses_path/adjudications_path/warnings (which embed the
# durable root's own filesystem path) are trivially identical strings in
# both runs, and the only thing that can legitimately differ at all is
# `generated_at` (stripped above) or a genuine skeptic-triage-induced change
# (which is exactly what this regression is watching for).
# ===========================================================================


def test_audit_summary_and_exit_code_identical_with_and_without_skeptic_triage(tmp_path):
    root = make_durable_root(tmp_path / "durable_root")

    block_text, senses = two_jean_senses()
    write_manifest(root, {"b1": {"seg": None, "plain_text": block_text}})
    write_senses(root, {"Jean": {"senses": senses}})
    write_canon(root, [entry("Marie", "Marie")])  # unrelated entry, no collapse

    assert not (root / "skeptic_triage.json").exists()
    proc_before = run_audit(root, "--check", "--particle-config", "fr.json")
    summary_before = parse_stdout(proc_before)

    # Sanity: this fixture genuinely blocks (a real, un-adjudicated homonym_split),
    # so the identity asserted below is not vacuously true of two already-empty/green runs.
    assert summary_before["totals"]["by_kind"]["homonym_split"] == 1
    assert summary_before["blocking_count"] == 1
    assert summary_before["gate_passed"] is False
    assert proc_before.returncode == 1

    write_skeptic_triage(root)
    assert (root / "skeptic_triage.json").is_file()
    proc_after = run_audit(root, "--check", "--particle-config", "fr.json")
    summary_after = parse_stdout(proc_after)

    assert proc_after.returncode == proc_before.returncode
    assert _without_generated_at(summary_after) == _without_generated_at(summary_before), (
        "canon_adjudication_audit.py's summary must be unaffected (aside from its own "
        "generated_at timestamp) by skeptic_triage.json's presence in the durable root"
    )


def test_audit_summary_and_exit_code_identical_on_a_clean_gate_too(tmp_path):
    """Companion case: the SAME identity holds on the ordinary green path
    (no split, no blockers at all) -- skeptic_triage.json's presence must
    not perturb a clean --check run either."""
    root = make_durable_root(tmp_path / "durable_root")
    write_canon(root, [entry("Marie", "Marie")])

    proc_before = run_audit(root, "--check")
    summary_before = parse_stdout(proc_before)
    assert summary_before["blocking_count"] == 0
    assert summary_before["gate_passed"] is True
    assert proc_before.returncode == 0

    write_skeptic_triage(root)
    proc_after = run_audit(root, "--check")
    summary_after = parse_stdout(proc_after)

    assert proc_after.returncode == proc_before.returncode
    assert _without_generated_at(summary_after) == _without_generated_at(summary_before)
