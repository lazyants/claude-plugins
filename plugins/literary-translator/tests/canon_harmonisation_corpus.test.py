"""tests/canon_harmonisation_corpus.test.py -- scripts/canon_harmonisation.py's
`--build-corpus` mode, the corpus-gathering step that precedes `--check`
(#823). See that script's own `build_corpus()` docstring and
`canon-harmonisation-corpus.schema.json` for the full contract this file
asserts against; see tests/canon_harmonisation.test.py for `--check`/
`--report` themselves, which this file deliberately does not re-cover.

## Why a corpus at all

`--check` validates a proposal against exactly what a dispatched pass was
SHOWN, never against whatever live files happen to say when the check runs
later -- name_candidates.json left behind by an earlier run looks identical
to a freshly built one, and a draft that converged after runs/ledger.json
was last materialized is invisible to a reader of that file. `--build-corpus`
is the step that turns "what the session saw" into a byte-comparable
artifact: it reads canon.json's entries{}, every converged segment's draft
names[] rows (subject to a stale-review exclusion), and (conditionally)
name_candidates.json, and writes them into one corpus file whose own sha256
`--check --expect-corpus-sha256` later re-verifies.

## Fixture strategy

Every test builds an isolated `durable_root` on disk, mirroring
tests/canon_harmonisation.test.py's own `make_durable_root` pattern (copied
here, not imported -- each test file owns its fixtures). Staged under
`{root}/scripts/`: canon_harmonisation.py itself, json_stdout.py (its own
dynamic dependency), ledger_merge.py (`_read_fragments`, the fail-closed
enumeration `--build-corpus` reuses), final_audit.py (`draft_content_sha1`,
`_name_entry_forms`), and final_audit.py's OWN bare-import dependencies,
bootstrap_names.py and validate_draft.py -- final_audit.py imports both at
module level and exits 2 if either is missing, so omitting them would break
every test in this file at the "no stdout" line, not just the ones that
exercise that path. Every `*.schema.json` sibling is staged too:
`_build_schema_registry()` globs the whole schemas/ directory, not only the
one schema build_corpus() validates its own output against.

Every `reviewed_draft_sha1` a fixture writes is computed by loading the REAL
final_audit.py from the fixture root and calling its own
`draft_content_sha1()` -- never a reimplementation of the canonical-JSON-
minus-dispatch_token algorithm, which would silently drift from what
build_corpus() itself compares against.

Invoked via `sys.executable`, an explicit `timeout=`, `capture_output=True,
text=True` -- the same subprocess convention every sibling suite in this
directory uses.
"""
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import jsonschema
import pytest
from referencing import Registry, Resource

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_SRC_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets" / "scripts"
SCHEMAS_SRC_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets" / "schemas"

SCRIPT_SRC = SCRIPTS_SRC_DIR / "canon_harmonisation.py"
CORPUS_SCHEMA_SRC = SCHEMAS_SRC_DIR / "canon-harmonisation-corpus.schema.json"
assert SCRIPT_SRC.is_file(), f"canon_harmonisation.py not found at {SCRIPT_SRC}"
assert CORPUS_SCHEMA_SRC.is_file(), f"canon-harmonisation-corpus.schema.json not found at {CORPUS_SCHEMA_SRC}"

# Every sibling build_corpus() loads by exact path (canon_harmonisation.py's
# own _load_sibling / the dynamic json_stdout.py load), plus final_audit.py's
# own bare-import dependencies it pulls in at module level.
DEPENDENCY_SCRIPTS = (
    "canon_harmonisation.py",
    "json_stdout.py",
    "ledger_merge.py",
    "final_audit.py",
    "bootstrap_names.py",
    "validate_draft.py",
)


# ---------------------------------------------------------------------------
# Fixture construction
# ---------------------------------------------------------------------------


def make_corpus_durable_root(root: Path) -> Path:
    """The ordinary fixture: DEPENDENCY_SCRIPTS staged under {root}/scripts/,
    every *.schema.json sibling staged under {root}/schemas/. No canon.json
    yet -- each test writes its own via write_canon()."""
    scripts_dir = root / "scripts"
    schemas_dir = root / "schemas"
    scripts_dir.mkdir(parents=True)
    schemas_dir.mkdir(parents=True)
    for name in DEPENDENCY_SCRIPTS:
        shutil.copy2(SCRIPTS_SRC_DIR / name, scripts_dir / name)
    for schema_file in sorted(SCHEMAS_SRC_DIR.glob("*.schema.json")):
        shutil.copy2(schema_file, schemas_dir / schema_file.name)
    return root


def run_harmonisation(root: Path, *args, timeout=30):
    return subprocess.run(
        [sys.executable, str(root / "scripts" / "canon_harmonisation.py"), *args],
        capture_output=True, text=True, timeout=timeout,
    )


def parse_stdout(proc) -> dict:
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    assert len(lines) == 1, (
        f"expected exactly one stdout JSON line, got {len(lines)}:\n{proc.stdout}\n"
        f"STDERR:\n{proc.stderr}"
    )
    return json.loads(lines[0])


def assert_no_stdout(proc) -> None:
    assert proc.stdout == "", f"expected no stdout at all, got:\n{proc.stdout!r}"


def canon_entry(source_form, target, is_proper_name=True, basis="transliterated"):
    """A minimal canon.json entries{} record -- build_corpus() only ever
    reads canonical_target_form off it. See
    tests/canon_harmonisation.test.py's own canon_entry() for the model."""
    return {
        "source_form": source_form,
        "canonical_target_form": target,
        "is_proper_name": is_proper_name,
        "basis": basis,
        "confidence": "high",
    }


def write_canon(root: Path, entries: list) -> bytes:
    """Writes {root}/canon.json from a list of canon_entry() dicts and
    returns the exact bytes written, so a test can compute the expected
    canon_sha256 without re-deriving canon.json's own serialisation."""
    keyed = {e["source_form"]: e for e in entries}
    doc = {
        "entries": keyed,
        "review_queue": [],
        "generation_hashes": {"particle_config_hash": "test-hash", "derivation_bundle_hash": "test-hash"},
    }
    raw = json.dumps(doc, ensure_ascii=False).encode("utf-8")
    (root / "canon.json").write_bytes(raw)
    return raw


def canon_sha256_of(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def write_draft(root: Path, seg: str, names: list) -> Path:
    """Writes {root}/segments/{seg}.draft.json -- the shape build_corpus()
    reads names[] rows out of. Deliberately just {"names": [...]}: neither
    build_corpus() nor draft_content_sha1() schema-validates a draft, they
    only need it to be a JSON object."""
    path = root / "segments" / f"{seg}.draft.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"names": names}, ensure_ascii=False), encoding="utf-8")
    return path


def draft_content_sha1_of(root: Path, draft_path: Path) -> str:
    """Computes the SAME sha1 build_corpus() will compare a fragment's
    reviewed_draft_sha1 against, by loading the REAL final_audit.py staged
    at {root}/scripts/final_audit.py and calling its own
    draft_content_sha1() -- never a reimplementation of the canonical-JSON-
    minus-dispatch_token algorithm."""
    spec = importlib.util.spec_from_file_location(
        "final_audit_fixture_helper", root / "scripts" / "final_audit.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.draft_content_sha1(draft_path)


def write_fragment(root: Path, seg: str, status="converged", reviewed_draft_sha1=None) -> Path:
    """Writes {root}/runs/ledger.d/{seg}.json -- the shape
    ledger_merge._read_fragments() reads. `reviewed_draft_sha1` is omitted
    from the record entirely when None, for the "no reviewed_draft_sha1 at
    all" exclusion case."""
    path = root / "runs" / "ledger.d" / f"{seg}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {"status": status}
    if reviewed_draft_sha1 is not None:
        record["reviewed_draft_sha1"] = reviewed_draft_sha1
    path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    return path


def stage_converged_segment(root: Path, seg: str, names: list) -> str:
    """Writes a draft plus a converged ledger fragment whose
    reviewed_draft_sha1 matches the draft's CURRENT content -- the only
    shape build_corpus() accepts as contributing draft observations.
    Returns the sha1, in case a caller wants to build a stale variant that
    shares the same draft file."""
    draft_path = write_draft(root, seg, names)
    sha1 = draft_content_sha1_of(root, draft_path)
    write_fragment(root, seg, status="converged", reviewed_draft_sha1=sha1)
    return sha1


def write_candidates(root: Path, rows: list) -> Path:
    path = root / "name_candidates.json"
    path.write_text(json.dumps({"candidates": rows}, ensure_ascii=False), encoding="utf-8")
    return path


def _corpus_schema_errors(doc: dict, schemas_dir: Path) -> list:
    """Mirrors canon_harmonisation.py's own _build_schema_registry() /
    _schema_errors(): every *.schema.json under schemas_dir registered by
    its own $id, then validated against canon-harmonisation-corpus.schema.json
    through THAT registry -- never jsonschema.validate() against the bare
    schema dict, which would silently ignore an unresolved $ref."""
    resources = []
    for schema_file in sorted(schemas_dir.glob("*.schema.json")):
        contents = json.loads(schema_file.read_text(encoding="utf-8"))
        schema_id = contents.get("$id", schema_file.name)
        resources.append((schema_id, Resource.from_contents(contents)))
    registry = Registry().with_resources(resources)
    schema = json.loads(
        (schemas_dir / "canon-harmonisation-corpus.schema.json").read_text(encoding="utf-8")
    )
    validator = jsonschema.Draft202012Validator(schema, registry=registry)
    return sorted(validator.iter_errors(doc), key=lambda e: [str(p) for p in e.path])


# ===========================================================================
# Happy path
# ===========================================================================


def test_build_corpus_happy_path_gathers_all_three_corpora(tmp_path):
    """One canon entry, one converged segment with a readable names[] row,
    one candidate row -- the stdout summary's counts must match what was
    fed in, and the corpus file's own sha256 must match what the summary
    reports (never a value drifted from what was actually written)."""
    root = make_corpus_durable_root(tmp_path / "durable_root")
    raw_canon = write_canon(root, [canon_entry("CanonSource", "CanonTarget")])
    csha = canon_sha256_of(raw_canon)
    stage_converged_segment(root, "seg01", [
        {"source_form": "DraftSource", "target_form": "DraftTarget"},
    ])
    write_candidates(root, [{"name": "CandidateName", "freq": 3}])

    proc = run_harmonisation(root, "--build-corpus", "--candidates-source", "bootstrap")
    assert proc.returncode == 0, proc.stderr
    summary = parse_stdout(proc)

    assert summary["canon_observations"] == 1
    assert summary["draft_observations"] == 1
    assert summary["candidate_observations"] == 1
    assert summary["converged_segments"] == 1
    assert summary["canon_sha256"] == csha
    assert summary["should_dispatch"] is True

    corpus_path = Path(summary["corpus_path"])
    written = corpus_path.read_bytes()
    assert hashlib.sha256(written).hexdigest() == summary["corpus_sha256"], (
        "the reported corpus_sha256 must be the sha256 of the bytes actually written"
    )


# ===========================================================================
# FAIL-CLOSED enumeration: an unreadable, POPULATED ledger.d must never read
# as an empty one.
# ===========================================================================


def test_build_corpus_unreadable_populated_ledger_d_is_fatal_not_empty(tmp_path):
    """The defect that matters most here: is_dir() answers False on a
    suppressed OSError and glob() returns an empty iterator for a directory
    it cannot read, so a populated-but-unreadable runs/ledger.d would look
    exactly like a genuinely empty one to code built on those two calls --
    a corpus that GATHERED nothing reading like one that FOUND nothing.
    ledger_merge._read_fragments() answers with one iterdir() inside one
    try instead, which turns a permission error into a REFUSAL. Must exit
    2, print NO stdout, print no raw Traceback, and name the directory."""
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        pytest.skip("chmod 0o000 is not enforced for root")
    root = make_corpus_durable_root(tmp_path / "durable_root")
    write_canon(root, [canon_entry("CanonSource", "CanonTarget")])
    write_fragment(root, "seg01", status="converged")  # content irrelevant: iterdir() itself must fail
    ledger_d = root / "runs" / "ledger.d"
    original_mode = ledger_d.stat().st_mode
    ledger_d.chmod(0o000)
    try:
        proc = run_harmonisation(root, "--build-corpus", "--candidates-source", "disabled")
    finally:
        ledger_d.chmod(original_mode)

    assert proc.returncode == 2, proc.stdout
    assert_no_stdout(proc)
    assert "Traceback" not in proc.stderr, (
        f"a permission fault must surface as this script's named fatal, not a raw "
        f"traceback:\n{proc.stderr}"
    )
    assert str(ledger_d) in proc.stderr
    # And nothing was written: a fatal that still left a corpus file behind
    # would leave a short corpus on disk for a later run to pick up.
    assert list((root / "harmonisation").glob("corpus_*.json")) == [], (
        "a failed gather must publish no corpus file"
    )


# ===========================================================================
# Stale review: reviewed_draft_sha1 must still match the draft's CURRENT
# content, in every shape that comparison can fail.
# ===========================================================================


def test_build_corpus_stale_review_exclusions_three_shapes(tmp_path):
    """A draft edited after its review carries names no reviewer ever saw,
    so status == "converged" alone must not be sufficient to let a fragment
    contribute rows. Three ways that comparison fails, all excluded and all
    counted in drafts_excluded_stale_review rather than silently dropped:
    (a) reviewed_draft_sha1 present but stale (does not match the draft's
    current content), (b) reviewed_draft_sha1 missing from the fragment
    entirely, (c) reviewed_draft_sha1 present but the draft file itself is
    gone. converged_segments must stay 0: none of the three ever reaches
    the point where a fragment is counted as contributing."""
    root = make_corpus_durable_root(tmp_path / "durable_root")
    write_canon(root, [])

    write_draft(root, "seg_mismatch", [{"source_form": "A", "target_form": "A-target"}])
    write_fragment(root, "seg_mismatch", status="converged", reviewed_draft_sha1="0" * 40)

    write_draft(root, "seg_no_sha", [{"source_form": "B", "target_form": "B-target"}])
    write_fragment(root, "seg_no_sha", status="converged")  # no reviewed_draft_sha1 at all

    write_fragment(root, "seg_missing_draft", status="converged", reviewed_draft_sha1="1" * 40)
    assert not (root / "segments" / "seg_missing_draft.draft.json").exists()

    proc = run_harmonisation(root, "--build-corpus", "--candidates-source", "disabled")
    assert proc.returncode == 0, proc.stderr
    summary = parse_stdout(proc)
    assert summary["drafts_excluded_stale_review"] == 3
    assert summary["converged_segments"] == 0
    assert summary["draft_observations"] == 0


# ===========================================================================
# names[] field conventions -- both read, an unmatched row counted not
# silently dropped.
# ===========================================================================


def test_build_corpus_both_names_field_conventions_and_skipped_row(tmp_path):
    """final_audit._name_entry_forms() accepts either target_form or
    canonical_target_form as the target field -- both must actually
    contribute a draft observation. A row matching neither convention (no
    readable target at all) must increment draft_rows_skipped rather than
    vanish without a trace."""
    root = make_corpus_durable_root(tmp_path / "durable_root")
    write_canon(root, [])
    stage_converged_segment(root, "seg01", [
        {"source_form": "X1", "target_form": "T1"},
        {"source_form": "X2", "canonical_target_form": "T2"},
        {"source_form": "X3"},  # neither convention -- no readable target
    ])

    proc = run_harmonisation(root, "--build-corpus", "--candidates-source", "disabled")
    assert proc.returncode == 0, proc.stderr
    summary = parse_stdout(proc)
    assert summary["draft_observations"] == 2
    assert summary["draft_rows_skipped"] == 1

    corpus_doc = json.loads(Path(summary["corpus_path"]).read_text(encoding="utf-8"))
    draft_rows = {
        o["source_form"]: o["target_form"]
        for o in corpus_doc["observations"] if o["corpus"] == "draft"
    }
    assert draft_rows == {"X1": "T1", "X2": "T2"}


# ===========================================================================
# --candidates-source disabled vs bootstrap
# ===========================================================================


def test_build_corpus_candidates_disabled_ignores_populated_stale_file(tmp_path):
    """On the glossary.enabled:false branch the bootstrap never ran and
    deletes nothing, so a stale name_candidates.json can sit there looking
    present. --candidates-source disabled must produce ZERO candidate
    observations even though a populated file is on disk -- the file must
    never be opened at all on this branch."""
    root = make_corpus_durable_root(tmp_path / "durable_root")
    write_canon(root, [canon_entry("CanonSource", "CanonTarget")])
    write_candidates(root, [{"name": "Stale1", "freq": 5}, {"name": "Stale2", "freq": 2}])

    proc = run_harmonisation(root, "--build-corpus", "--candidates-source", "disabled")
    assert proc.returncode == 0, proc.stderr
    summary = parse_stdout(proc)
    assert summary["candidate_observations"] == 0
    assert summary["candidates_source"] == "disabled"

    corpus_doc = json.loads(Path(summary["corpus_path"]).read_text(encoding="utf-8"))
    assert corpus_doc["candidates_source"] == "disabled"
    assert not any(o["corpus"] == "candidate" for o in corpus_doc["observations"])


def test_build_corpus_candidates_bootstrap_empty_list_distinct_from_disabled(tmp_path):
    """A freshly-built empty corpus (bootstrap ran, the extractor found
    nothing) is a DIFFERENT fact from a corpus that was never gathered
    (disabled) -- both report candidate_observations: 0, so the two must
    stay distinguishable through candidates_source alone."""
    root = make_corpus_durable_root(tmp_path / "durable_root")
    write_canon(root, [canon_entry("CanonSource", "CanonTarget")])
    write_candidates(root, [])

    proc = run_harmonisation(root, "--build-corpus", "--candidates-source", "bootstrap")
    assert proc.returncode == 0, proc.stderr
    summary = parse_stdout(proc)
    assert summary["candidate_observations"] == 0
    assert summary["candidates_source"] == "bootstrap"

    corpus_doc = json.loads(Path(summary["corpus_path"]).read_text(encoding="utf-8"))
    assert corpus_doc["candidates_source"] == "bootstrap"


# ===========================================================================
# should_dispatch: >=1 canon observation, OR >=2 draft observations with 0
# canon; false for exactly 1 draft observation and 0 canon.
# ===========================================================================


def test_build_corpus_should_dispatch_true_with_one_canon_observation(tmp_path):
    root = make_corpus_durable_root(tmp_path / "durable_root")
    write_canon(root, [canon_entry("CanonSource", "CanonTarget")])

    proc = run_harmonisation(root, "--build-corpus", "--candidates-source", "disabled")
    assert proc.returncode == 0, proc.stderr
    summary = parse_stdout(proc)
    assert summary["canon_observations"] == 1
    assert summary["draft_observations"] == 0
    assert summary["should_dispatch"] is True


def test_build_corpus_should_dispatch_true_with_two_draft_observations_and_no_canon(tmp_path):
    root = make_corpus_durable_root(tmp_path / "durable_root")
    write_canon(root, [])
    stage_converged_segment(root, "seg01", [{"source_form": "A", "target_form": "A-target"}])
    stage_converged_segment(root, "seg02", [{"source_form": "B", "target_form": "B-target"}])

    proc = run_harmonisation(root, "--build-corpus", "--candidates-source", "disabled")
    assert proc.returncode == 0, proc.stderr
    summary = parse_stdout(proc)
    assert summary["canon_observations"] == 0
    assert summary["draft_observations"] == 2
    assert summary["should_dispatch"] is True


def test_build_corpus_should_dispatch_false_with_one_draft_observation_and_no_canon(tmp_path):
    root = make_corpus_durable_root(tmp_path / "durable_root")
    write_canon(root, [])
    stage_converged_segment(root, "seg01", [{"source_form": "A", "target_form": "A-target"}])

    proc = run_harmonisation(root, "--build-corpus", "--candidates-source", "disabled")
    assert proc.returncode == 0, proc.stderr
    summary = parse_stdout(proc)
    assert summary["canon_observations"] == 0
    assert summary["draft_observations"] == 1
    assert summary["should_dispatch"] is False


# ===========================================================================
# Written corpus file validates against the real schema.
# ===========================================================================


def test_build_corpus_written_file_validates_against_corpus_schema(tmp_path):
    root = make_corpus_durable_root(tmp_path / "durable_root")
    write_canon(root, [canon_entry("CanonSource", "CanonTarget")])
    stage_converged_segment(root, "seg01", [
        {"source_form": "DraftSource", "target_form": "DraftTarget"},
    ])
    write_candidates(root, [{"name": "CandidateName", "freq": 3}])

    proc = run_harmonisation(root, "--build-corpus", "--candidates-source", "bootstrap")
    assert proc.returncode == 0, proc.stderr
    summary = parse_stdout(proc)

    corpus_doc = json.loads(Path(summary["corpus_path"]).read_text(encoding="utf-8"))
    errors = _corpus_schema_errors(corpus_doc, root / "schemas")
    assert errors == [], f"written corpus failed its own schema: {[e.message for e in errors]}"


# ===========================================================================
# Usage errors
# ===========================================================================


def test_usage_error_build_corpus_without_candidates_source(tmp_path):
    root = make_corpus_durable_root(tmp_path / "durable_root")
    proc = run_harmonisation(root, "--build-corpus")
    assert proc.returncode == 2, proc.stdout
    assert_no_stdout(proc)


def test_usage_error_build_corpus_combined_with_check(tmp_path):
    root = make_corpus_durable_root(tmp_path / "durable_root")
    proc = run_harmonisation(root, "--check", "dummy.json", "--build-corpus")
    assert proc.returncode == 2, proc.stdout
    assert_no_stdout(proc)


def test_usage_error_candidates_source_without_build_corpus(tmp_path):
    root = make_corpus_durable_root(tmp_path / "durable_root")
    proc = run_harmonisation(root, "--report", "--candidates-source", "bootstrap")
    assert proc.returncode == 2, proc.stdout
    assert_no_stdout(proc)


# ===========================================================================
# Cross-corpus discrepancy: same source_form, different targets -> TWO
# observations, never collapsed to one.
# ===========================================================================


def test_build_corpus_same_source_form_different_targets_yields_two_observations(tmp_path):
    """Keying by source form alone would erase the exact cross-corpus
    discrepancy this feature exists to surface: a canon entry and a
    converged draft row for the SAME source_form under DIFFERENT targets
    must produce two distinct observations, one per corpus, not one
    collapsed row that hides the disagreement."""
    root = make_corpus_durable_root(tmp_path / "durable_root")
    write_canon(root, [canon_entry("SharedName", "TargetFromCanon")])
    stage_converged_segment(root, "seg01", [
        {"source_form": "SharedName", "target_form": "TargetFromDraft"},
    ])

    proc = run_harmonisation(root, "--build-corpus", "--candidates-source", "disabled")
    assert proc.returncode == 0, proc.stderr
    summary = parse_stdout(proc)
    assert summary["canon_observations"] == 1
    assert summary["draft_observations"] == 1

    corpus_doc = json.loads(Path(summary["corpus_path"]).read_text(encoding="utf-8"))
    matches = [o for o in corpus_doc["observations"] if o["source_form"] == "SharedName"]
    assert len(matches) == 2, (
        f"expected two distinct observations for the same source_form under two "
        f"different targets, got {len(matches)}: {matches}"
    )
    by_corpus = {o["corpus"]: o["target_form"] for o in matches}
    assert by_corpus == {"canon": "TargetFromCanon", "draft": "TargetFromDraft"}


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))


# ===========================================================================
# Malformed gathered input is a REFUSAL, never a quiet "found nothing"
# ===========================================================================


def test_build_corpus_fragment_without_a_usable_status_is_fatal(tmp_path):
    """A ledger fragment this script cannot read is corruption, not a segment
    that has not converged. Skipping it silently is the unreadable-ledger.d
    failure one level down: the corpus comes back short with every counter at
    zero and nothing anywhere saying so."""
    root = make_corpus_durable_root(tmp_path / "durable_root")
    write_canon(root, [canon_entry("CanonSource", "CanonTarget")])
    ledger_d = root / "runs" / "ledger.d"
    ledger_d.mkdir(parents=True, exist_ok=True)
    (ledger_d / "seg01.json").write_text(
        json.dumps({"seg": "seg01"}), encoding="utf-8")  # no status at all

    proc = run_harmonisation(root, "--build-corpus", "--candidates-source", "disabled")
    assert proc.returncode == 2, proc.stdout
    assert_no_stdout(proc)
    assert "Traceback" not in proc.stderr
    assert "no usable status" in proc.stderr


def test_build_corpus_converged_draft_without_a_names_array_is_fatal(tmp_path):
    """draft.schema.json REQUIRES names[] as an array. Reading a missing one
    as an empty list would report a corrupt draft as a segment that genuinely
    contributed no names -- the same substitution, in the one place the union
    input comes from."""
    root = make_corpus_durable_root(tmp_path / "durable_root")
    write_canon(root, [canon_entry("CanonSource", "CanonTarget")])
    draft_path = root / "segments" / "seg01.draft.json"
    draft_path.parent.mkdir(parents=True, exist_ok=True)
    draft_path.write_text(json.dumps(
        {"seg": "seg01", "blocks": [], "footnotes": [], "verses": [], "notes": []},
        ensure_ascii=False), encoding="utf-8")
    write_fragment(root, "seg01", status="converged",
                   reviewed_draft_sha1=draft_content_sha1_of(root, draft_path))

    proc = run_harmonisation(root, "--build-corpus", "--candidates-source", "disabled")
    assert proc.returncode == 2, proc.stdout
    assert_no_stdout(proc)
    assert "Traceback" not in proc.stderr
    assert "no names[] array" in proc.stderr


@pytest.mark.parametrize("row,needle", [
    ("not an object", "not an object"),
    ({"freq": 3}, "no usable name"),
    ({"name": "Weinberg"}, "no usable freq"),
    ({"name": "Weinberg", "freq": 0}, "no usable freq"),
])
def test_build_corpus_malformed_candidate_row_is_fatal(tmp_path, row, needle):
    """bootstrap_names.py writes every one of these rows itself, so a
    malformed one is a corrupt file rather than a row this script may decline.
    Dropping it silently removes exactly the union rows the feature exists to
    expose, and reports success while doing it."""
    root = make_corpus_durable_root(tmp_path / "durable_root")
    write_canon(root, [canon_entry("CanonSource", "CanonTarget")])
    write_candidates(root, [row])

    proc = run_harmonisation(root, "--build-corpus", "--candidates-source", "bootstrap")
    assert proc.returncode == 2, proc.stdout
    assert_no_stdout(proc)
    assert "Traceback" not in proc.stderr
    assert needle in proc.stderr


def test_build_corpus_refuses_to_overwrite_an_existing_out_path(tmp_path):
    """A corpus file is per-attempt and the digest the session keeps refers to
    THAT one, so silently replacing it would leave a kept digest pointing at
    bytes that are gone."""
    root = make_corpus_durable_root(tmp_path / "durable_root")
    write_canon(root, [canon_entry("CanonSource", "CanonTarget")])
    out = root / "harmonisation" / "corpus_fixed.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("{}", encoding="utf-8")

    proc = run_harmonisation(root, "--build-corpus", "--candidates-source", "disabled",
                             "--out", str(out))
    assert proc.returncode == 2, proc.stdout
    assert_no_stdout(proc)
    assert "never overwritten" in proc.stderr
    assert out.read_text(encoding="utf-8") == "{}", "the existing file must be untouched"


# ===========================================================================
# The corpus file's SERIALISATION, pinned as literal bytes
# ===========================================================================

# One empty-observation corpus as the exact UTF-8 bytes build_corpus would
# write for it, plus their sha256. Not a re-derivation: key order, the
# two-space indent, ensure_ascii and the trailing newline all change these
# bytes, and the single-file anchor deliberately has no canonical serialiser
# to appeal to -- --check compares a digest over whatever bytes are on disk.
# So the shape has to be pinned somewhere, and a vector computed by the same
# json.dumps call it is meant to guard would pin nothing.
EMPTY_CORPUS_BYTES = (
    b'{\n'
    b'  "candidates_source": "disabled",\n'
    b'  "canon_sha256": "' + b"0" * 64 + b'",\n'
    b'  "converged_segments": 0,\n'
    b'  "draft_rows_skipped": 0,\n'
    b'  "drafts_excluded_stale_review": 0,\n'
    b'  "generated_at": "2026-01-01T00:00:00+00:00",\n'
    b'  "observations": [],\n'
    b'  "schema_version": 1\n'
    b'}\n'
)
EMPTY_CORPUS_SHA256 = (
    "9fed678f7c2a676d750684299c1ad73c15f37157e32683cbf1d66892a1e0ca43"
)


def test_empty_corpus_literal_byte_vector():
    """The vector itself: these exact bytes hash to this exact digest. If the
    pair ever disagrees, the test file has been edited without recomputing,
    and every claim below it is worthless."""
    assert hashlib.sha256(EMPTY_CORPUS_BYTES).hexdigest() == EMPTY_CORPUS_SHA256
    assert len(EMPTY_CORPUS_BYTES) == 307


def test_build_corpus_serialisation_matches_the_pinned_shape(tmp_path):
    """The bytes build_corpus actually writes, re-serialised from the document
    it wrote with the SAME options the vector encodes, must be byte-identical
    to what is on disk. This is what ties the live writer to the vector above:
    a change to indent, sort_keys, ensure_ascii or the trailing newline moves
    the file's digest, and the digest is the whole anchor."""
    root = make_corpus_durable_root(tmp_path / "durable_root")
    write_canon(root, [canon_entry("CanonSource", "CanonTarget")])
    proc = run_harmonisation(root, "--build-corpus", "--candidates-source", "disabled")
    assert proc.returncode == 0, proc.stderr
    summary = parse_stdout(proc)

    raw = Path(summary["corpus_path"]).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == summary["corpus_sha256"]

    doc = json.loads(raw)
    expected = json.dumps(
        doc, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    assert raw == expected, (
        "the corpus file's serialisation no longer matches the pinned shape "
        "(sorted keys, two-space indent, ensure_ascii=False, trailing newline)"
    )

    # And the vector's own shape is the one the writer produces: same options,
    # same result, for a document with no observations.
    vector_doc = json.loads(EMPTY_CORPUS_BYTES)
    assert json.dumps(
        vector_doc, ensure_ascii=False, indent=2, sort_keys=True
    ).encode("utf-8") + b"\n" == EMPTY_CORPUS_BYTES
    assert set(vector_doc) == set(doc), (
        "the pinned vector and the live writer disagree about which top-level "
        "fields a corpus file carries"
    )


# A SECOND vector, non-ASCII this time. The empty one above is ASCII-only, so
# it is byte-identical under ensure_ascii=False and ensure_ascii=True: a
# regression that started escaping every non-Latin source form would keep it
# green while moving the digest of every real corpus this plugin builds, and
# the digest is the whole anchor. Built with chr()-free literal text because
# the forms themselves are the subject here, not any boundary character.
NON_ASCII_CORPUS_SHA256 = (
    "4c94385884a0ac4a786653111483505d1e43b0568eb3aa1af296f8e33ee3d016"
)


def _non_ascii_corpus_doc():
    return {
        "canon_sha256": "0" * 64,
        "candidates_source": "bootstrap",
        "converged_segments": 1,
        "draft_rows_skipped": 0,
        "drafts_excluded_stale_review": 0,
        "generated_at": "2026-01-01T00:00:00+00:00",
        "observations": [
            {"corpus": "canon", "source_form": "בָאבְרִינִצֶער",
             "target_form": "Mordechai Babrinitzer"},
            {"corpus": "draft", "source_form": "בָאבְרִינִצֶער",
             "target_form": "Mordechai Bobrinitzer", "n_segments": 2},
        ],
        "schema_version": 1,
    }


def test_non_ascii_corpus_byte_vector_pins_ensure_ascii():
    """The vector, and the property the ASCII one cannot carry: serialising
    the same document with ensure_ascii=True must NOT reach this digest. That
    is what makes this pair a guard on the writer's option rather than a
    restatement of it."""
    doc = _non_ascii_corpus_doc()
    raw = json.dumps(
        doc, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    assert hashlib.sha256(raw).hexdigest() == NON_ASCII_CORPUS_SHA256
    assert len(raw) == 605

    escaped = json.dumps(
        doc, ensure_ascii=True, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    assert escaped != raw
    assert hashlib.sha256(escaped).hexdigest() != NON_ASCII_CORPUS_SHA256


def test_build_corpus_writes_non_ascii_forms_unescaped(tmp_path):
    """The LIVE writer over non-Latin data: the bytes on disk must carry the
    source form itself, not a \\uXXXX escape of it, and their digest must be
    the one the run reported. A corpus of Hebrew or Yiddish names is the
    normal case for this plugin, so an ASCII-only fixture tests the writer on
    data it will almost never see."""
    root = make_corpus_durable_root(tmp_path / "durable_root")
    source_form = "בָאבְרִינִצֶער"
    write_canon(root, [canon_entry(source_form, "Mordechai Babrinitzer")])

    proc = run_harmonisation(root, "--build-corpus", "--candidates-source", "disabled")
    assert proc.returncode == 0, proc.stderr
    summary = parse_stdout(proc)

    raw = Path(summary["corpus_path"]).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == summary["corpus_sha256"]
    assert source_form.encode("utf-8") in raw, (
        "the corpus file must carry the source form itself; an escaped copy "
        "parses back the same but moves the digest of every real corpus"
    )
    assert b"\\u05d1" not in raw


def test_build_corpus_refuses_a_dangling_symlink_at_the_out_path(tmp_path):
    """The reason create-once is os.link rather than exists()-then-replace.
    Path.exists() FOLLOWS symlinks, so a dangling one answers False: a check
    would pass and os.replace would then write through the link's name. link()
    fails with EEXIST on any existing name, symlink included, and does it
    atomically -- there is no window between the check and the act."""
    root = make_corpus_durable_root(tmp_path / "durable_root")
    write_canon(root, [canon_entry("CanonSource", "CanonTarget")])
    out = root / "harmonisation" / "corpus_fixed.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.symlink_to(root / "nothing_here.json")
    assert not out.exists(), "fixture sanity: a dangling symlink must answer False"
    assert out.is_symlink()

    proc = run_harmonisation(root, "--build-corpus", "--candidates-source", "disabled",
                             "--out", str(out))
    assert proc.returncode == 2, proc.stdout
    assert_no_stdout(proc)
    assert "never overwritten" in proc.stderr
    assert out.is_symlink(), "the link itself must be untouched"
    assert not (root / "nothing_here.json").exists(), "nothing may be written through it"
