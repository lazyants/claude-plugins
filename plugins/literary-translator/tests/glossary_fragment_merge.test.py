"""tests/glossary_fragment_merge.test.py -- regression coverage for
scripts/canon_validate.py's `--check-batch` and `--merge-batches` modes,
per CONTRACT-1.2.0-reliability.md §3 (issue #90: "concurrent glossary
batches race on shared canon.json -> silent lost updates").

The 1.2.0 glossary-pass Workflow dispatches ONE codex batch per
`glossary/runs/{{RUN_ID}}/out_{index}.json` fragment, self-checks each
fragment in isolation (`--check-batch`), then merges ALL fragments in ONE
process via `--merge-batches P1 P2 ...` -- threading
`acc = _merge_batch(acc, frag)` across fragments in argv order, stamping
generation_hashes once, and (per the CONTRACT's own reordering fix) running
whole-file Pass 2 on the in-memory accumulator BEFORE the atomic write, not
after. This file locks down:

  1. `--merge-batches` over disjoint fragments (clean union), identical
     overlap (idempotent no-op), and a genuine collision (fatal, existing
     `_merge_batch` collision-detection logic, now reachable through the
     new multi-fragment CLI).
  2. THE #90 regression itself: a source_form ACCEPTED in a LOWER-index
     fragment must not be RE-ADDED to review_queue by a HIGHER-index
     fragment that (independently, e.g. a racing codex batch) still queues
     the same source_form -- the CONTRACT's fix guards `_merge_batch`'s
     review_queue-append branch with `if source_form in entries: continue`.
  3. Idempotent re-merge of an unchanged fragment set.
  4. `--check-batch PATH [--expect-source-forms-file M.json]`: single-
     fragment Pass-1 + offline backstop, NO write, plus exact manifest
     coverage (missing candidate AND extra candidate both rejected -- this
     is where `canon_batch_inline_shape_drift.test.py`'s retired coverage
     partially moved to, per the build plan's "RETIRE" note).
  5. The two "canon.json must stay untouched" invariants CONTRACT §3 calls
     out explicitly for `--merge-batches`: (a) a Pass-1 failure on any
     fragment, and (b) -- the actual regression the reordering fix closes
     -- a Pass-2 (whole-file) failure on the in-memory accumulator, proven
     with a pre-existing on-disk canon.json that Pass-1 (which only
     re-validates the NEW batch items) can never catch.
  6. `--research-mode` stays required for both new modes.

Follows this plugin's established self-anchoring test convention
(`canon_format_validation.test.py`'s own `make_durable_root` pattern): every
test copies the REAL `canon_validate.py` and the REAL
`assets/schemas/canon-{entry,batch,file}.schema.json` files into an isolated
`tmp_path` fixture root, plus a fixture stand-in for `cache_key.py` (only
ever shelled out to as `cache_key.py --field <name>` for
`generation_hashes` stamping -- the real 15-field hashing algorithm has its
own dedicated test file, `ledger_composite_key.test.py`), and invokes the
script exactly as production does.

STATUS (owner-build coordination): at the time this file was written,
`--check-batch`/`--merge-batches` do not exist yet on
`scripts/canon_validate.py` -- every test below is written to
CONTRACT-1.2.0-reliability.md §3's documented shape, not to what is
currently on disk. Tests that need the not-yet-landed flags fail with an
argparse "unrecognized arguments" error until Owner C lands them; that is
expected, not a bug in this file.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))
from _senses_fixture import stage_consumer  # noqa: E402

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
ASSETS_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets"
SCRIPT_SRC = ASSETS_DIR / "scripts" / "canon_validate.py"
SCHEMAS_SRC = ASSETS_DIR / "schemas"

CANON_SCHEMA_FILES = (
    "canon-entry.schema.json",
    "canon-batch.schema.json",
    "canon-file.schema.json",
)

assert SCRIPT_SRC.is_file(), f"canon_validate.py not found at {SCRIPT_SRC}"
for _name in CANON_SCHEMA_FILES:
    assert (SCHEMAS_SRC / _name).is_file(), f"{_name} not found under {SCHEMAS_SRC}"


# A fixture stand-in for the real cache_key.py -- canon_validate.py's
# `--merge-batches` mode only ever shells out to it as `cache_key.py --field
# <name>` (never `--seg`), so the stub only needs to support that one
# interface: print a deterministic, non-empty string per requested field.
FAKE_CACHE_KEY_PY = """#!/usr/bin/env python3
import argparse
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--field")
    parser.add_argument("--seg", default=None)
    args = parser.parse_args()
    if not args.field:
        sys.stderr.write("fake cache_key.py: test stub requires --field\\n")
        return 1
    print(f"fixture-{args.field}-hash")
    return 0


if __name__ == "__main__":
    sys.exit(main())
"""


# ---------------------------------------------------------------------------
# Fixture harness
# ---------------------------------------------------------------------------


def make_durable_root(tmp_path):
    """Builds an isolated durable_root: copies the REAL canon_validate.py
    into {root}/scripts/ (so its self-anchored SCHEMAS_DIR/DEFAULT_CANON_PATH
    resolve against THIS fixture, never this repo's real assets tree),
    installs the fake cache_key.py stub alongside it, and copies the REAL
    three canon-*.schema.json files into {root}/schemas/. Also stages the
    REAL canon_senses.py (canon_validate.py's sibling import, RFC #215 1d)
    and canon-senses.schema.json via the sanctioned tests/_senses_fixture.py
    helper, so `from canon_senses import ...` resolves inside this isolated
    fixture too."""
    root = tmp_path / "durable_root"
    stage_consumer(root, "canon_validate.py")
    scripts_dir = root / "scripts"
    (scripts_dir / "cache_key.py").write_text(FAKE_CACHE_KEY_PY, encoding="utf-8")

    schemas_dir = root / "schemas"
    for name in CANON_SCHEMA_FILES:
        shutil.copy2(SCHEMAS_SRC / name, schemas_dir / name)

    return root


def write_canon(root, doc):
    canon_path = root / "canon.json"
    canon_path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return canon_path


def write_fragment(root, items, name="fragment.json"):
    frag_path = root / name
    frag_path.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
    return frag_path


def write_manifest(root, source_forms, name="manifest.json"):
    manifest_path = root / name
    manifest_path.write_text(
        json.dumps(list(source_forms), ensure_ascii=False), encoding="utf-8"
    )
    return manifest_path


def run_check_batch(root, research_mode, fragment_path, manifest_path=None, timeout=30):
    cmd = [
        sys.executable,
        str(root / "scripts" / "canon_validate.py"),
        "--research-mode",
        research_mode,
        "--check-batch",
        str(fragment_path),
    ]
    if manifest_path is not None:
        cmd += ["--expect-source-forms-file", str(manifest_path)]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=str(root))


def run_merge_batches(root, research_mode, fragment_paths, timeout=30):
    cmd = [
        sys.executable,
        str(root / "scripts" / "canon_validate.py"),
        "--research-mode",
        research_mode,
        "--merge-batches",
        *[str(p) for p in fragment_paths],
    ]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=str(root))


def parse_stdout(proc):
    assert proc.stdout.strip(), (
        f"expected one JSON line on stdout, got none. stderr:\n{proc.stderr}"
    )
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    assert len(lines) == 1, f"expected exactly one JSON line, got:\n{proc.stdout}"
    return json.loads(lines[0])


def canon_entry(
    source_form,
    canonical_target_form="Placeholder Target",
    basis="transliterated",
    confidence="high",
    source=None,
    is_proper_name=True,
    note=None,
    category=None,
):
    """A canon-entry.schema.json-shaped ACCEPTED entry (entries{} value)."""
    entry = {
        "source_form": source_form,
        "is_proper_name": is_proper_name,
        "canonical_target_form": canonical_target_form,
        "basis": basis,
        "confidence": confidence,
    }
    if source is not None:
        entry["source"] = source
    if note is not None:
        entry["note"] = note
    if category is not None:
        entry["category"] = category
    return entry


def accepted_batch_item(source_form, **kwargs):
    """A canon-batch.schema.json ACCEPTED-branch item -- canon_entry() plus
    the 'disposition' discriminator MERGE mode routes on."""
    item = canon_entry(source_form, **kwargs)
    item["disposition"] = "accepted"
    return item


def queued_batch_item(source_form, note="needs review", is_proper_name=True, **extra):
    """A canon-batch.schema.json QUEUED-branch item -- only source_form,
    is_proper_name, disposition, and note are required."""
    item = {
        "source_form": source_form,
        "is_proper_name": is_proper_name,
        "disposition": "review_queue",
        "note": note,
    }
    item.update(extra)
    return item


def canon_file_doc(
    entries: dict | None = None,
    review_queue: list | None = None,
    generation_hashes: str | dict | None = "default",
):
    doc = {
        "entries": entries if entries is not None else {},
        "review_queue": review_queue if review_queue is not None else [],
    }
    if generation_hashes == "default":
        doc["generation_hashes"] = {
            "particle_config_hash": "abc123",
            "derivation_bundle_hash": "def456",
        }
    elif generation_hashes is not None:
        doc["generation_hashes"] = generation_hashes
    return doc


VALID_URI = "https://example.org/wiki/established_name"


# ===========================================================================
# 1. --merge-batches: disjoint / identical-overlap / genuine-collision.
# ===========================================================================


def test_merge_batches_disjoint_fragments_merge_cleanly(tmp_path):
    root = make_durable_root(tmp_path)
    frag1 = write_fragment(
        root,
        [accepted_batch_item("Guerin", canonical_target_form="Gerin", basis="transliterated", confidence="medium")],
        name="out_0.json",
    )
    frag2 = write_fragment(
        root,
        [queued_batch_item("Provence", note="disputed rendering")],
        name="out_1.json",
    )

    proc = run_merge_batches(root, "live", [frag1, frag2])
    assert proc.returncode == 0, proc.stdout + proc.stderr
    payload = parse_stdout(proc)
    assert payload["success"] is True
    assert payload["mode"] == "merge_batches"
    assert payload["entries_count"] == 1
    assert payload["review_queue_count"] == 1

    on_disk = json.loads((root / "canon.json").read_text(encoding="utf-8"))
    assert "Guerin" in on_disk["entries"]
    assert on_disk["review_queue"][0]["source_form"] == "Provence"


def test_merge_batches_identical_overlap_across_fragments_is_idempotent_no_op(tmp_path):
    root = make_durable_root(tmp_path)
    shared_fields = dict(
        canonical_target_form="Sun King", basis="transliterated", confidence="high"
    )
    frag1 = write_fragment(
        root, [accepted_batch_item("Roi Soleil", **shared_fields)], name="out_0.json"
    )
    frag2 = write_fragment(
        root, [accepted_batch_item("Roi Soleil", **shared_fields)], name="out_1.json"
    )

    proc = run_merge_batches(root, "live", [frag1, frag2])
    assert proc.returncode == 0, proc.stdout + proc.stderr
    payload = parse_stdout(proc)
    assert payload["success"] is True
    assert payload["entries_count"] == 1
    assert payload["review_queue_count"] == 0


def test_merge_batches_conflicting_overlap_is_fatal_collision_and_canon_untouched(tmp_path):
    root = make_durable_root(tmp_path)
    canon_path = write_canon(
        root,
        canon_file_doc(
            entries={
                "Zurich": canon_entry(
                    "Zurich", canonical_target_form="Zurich", basis="transliterated", confidence="high"
                )
            }
        ),
    )
    before_bytes = canon_path.read_bytes()

    frag1 = write_fragment(
        root,
        [accepted_batch_item("Roi Soleil", canonical_target_form="Sun King", basis="transliterated", confidence="high")],
        name="out_0.json",
    )
    # Same source_form, DIFFERENT resolution -- a genuine collision.
    frag2 = write_fragment(
        root,
        [accepted_batch_item("Roi Soleil", canonical_target_form="The Sun King", basis="transliterated", confidence="high")],
        name="out_1.json",
    )

    proc = run_merge_batches(root, "live", [frag1, frag2])
    assert proc.returncode == 1, proc.stdout + proc.stderr
    payload = parse_stdout(proc)
    assert payload["success"] is False
    assert "Roi Soleil" in payload["error"]
    assert canon_path.read_bytes() == before_bytes, (
        "a fatal collision must never write canon.json -- pre-existing "
        "content must survive byte-for-byte"
    )


# ===========================================================================
# 2. THE #90 regression itself.
# ===========================================================================


def test_merge_batches_lower_index_accepted_survives_higher_index_queued_same_source_form(tmp_path):
    """PARITY/CHARACTERIZATION coverage, not a new regression test: issue
    #102's `_merge_batch` guard (`if source_form in entries: continue` on
    the review_queue-append branch) is ALREADY landed on this file's
    current code -- this test locks down that already-fixed behavior, it
    does not test new hardening. (The remaining #102 hardening gap -- a
    hand-corrupted canon.json that never went through `_merge_batch` at all
    can still land the same source_form in both entries{} and
    review_queue[] -- is covered separately by
    `_assert_no_entries_review_queue_overlap`'s tests in
    canon_format_validation.test.py and merged_disk_verify.test.py.)

    The single most important regression case in this file. A
    source_form is ACCEPTED in a LOWER-index fragment (P1, merged first)
    and independently QUEUED for the SAME source_form in a HIGHER-index
    fragment (P2, merged second) -- e.g. two concurrent glossary batches
    that both happened to consider the same candidate and disagreed on
    whether it was resolved yet. Threaded via
    `acc = _merge_batch(acc, frag)` in argv order (P1 then P2), the
    CONTRACT's fix ('_merge_batch' guards the review_queue-append branch
    with `if source_form in entries: continue`) means P2's queued claim
    must be silently dropped, NOT appended alongside the already-accepted
    entry."""
    root = make_durable_root(tmp_path)
    accepted_item = accepted_batch_item(
        "Duc de Guise", canonical_target_form="Duke of Guise", basis="transliterated", confidence="high"
    )
    frag_p1 = write_fragment(root, [accepted_item], name="out_0.json")
    frag_p2 = write_fragment(
        root, [queued_batch_item("Duc de Guise", note="needs a human call")], name="out_1.json"
    )

    proc = run_merge_batches(root, "live", [frag_p1, frag_p2])
    assert proc.returncode == 0, proc.stdout + proc.stderr
    payload = parse_stdout(proc)
    assert payload["success"] is True
    assert payload["entries_count"] == 1
    assert payload["review_queue_count"] == 0

    on_disk = json.loads((root / "canon.json").read_text(encoding="utf-8"))
    assert on_disk["entries"]["Duc de Guise"]["canonical_target_form"] == "Duke of Guise"
    assert all(q.get("source_form") != "Duc de Guise" for q in on_disk["review_queue"]), (
        "#90 regression: a source_form ACCEPTED in a lower-index fragment "
        "must not ALSO be re-added to review_queue by a higher-index "
        "fragment's queued claim for the same source_form"
    )


def test_merge_batches_rerun_with_unchanged_fragments_is_idempotent(tmp_path):
    root = make_durable_root(tmp_path)
    frag1 = write_fragment(
        root,
        [accepted_batch_item("Guerin", canonical_target_form="Gerin", basis="transliterated", confidence="medium")],
        name="out_0.json",
    )
    frag2 = write_fragment(
        root, [queued_batch_item("Provence", note="disputed rendering")], name="out_1.json"
    )

    first = run_merge_batches(root, "live", [frag1, frag2])
    assert first.returncode == 0, first.stdout + first.stderr
    first_payload = parse_stdout(first)
    assert first_payload["success"] is True

    # Re-run with the EXACT same, unchanged fragment set.
    second = run_merge_batches(root, "live", [frag1, frag2])
    assert second.returncode == 0, second.stdout + second.stderr
    second_payload = parse_stdout(second)
    assert second_payload["success"] is True
    assert second_payload["entries_count"] == first_payload["entries_count"]
    assert second_payload["review_queue_count"] == first_payload["review_queue_count"]


# ===========================================================================
# 3. --check-batch: single-fragment Pass-1 + offline backstop, NO write.
# ===========================================================================


def test_check_batch_accepts_schema_valid_fragment_and_writes_nothing(tmp_path):
    root = make_durable_root(tmp_path)
    frag = write_fragment(
        root,
        [
            accepted_batch_item("Guerin", canonical_target_form="Gerin", basis="transliterated", confidence="medium"),
            queued_batch_item("Provence", note="disputed rendering"),
        ],
    )
    proc = run_check_batch(root, "live", frag)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    payload = parse_stdout(proc)
    assert payload["success"] is True
    assert payload["mode"] == "check_batch"
    assert payload["source_forms"] == 2
    assert not (root / "canon.json").exists()


@pytest.mark.parametrize("research_mode, expect_success", [("live", True), ("offline", False)])
def test_check_batch_offline_backstop_gates_established_claim(tmp_path, research_mode, expect_success):
    root = make_durable_root(tmp_path)
    frag = write_fragment(
        root,
        [
            accepted_batch_item(
                "Roi Soleil",
                canonical_target_form="Sun King",
                basis="established",
                confidence="high",
                source=VALID_URI,
            )
        ],
    )
    proc = run_check_batch(root, research_mode, frag)
    payload = parse_stdout(proc)
    assert payload["success"] is expect_success
    if expect_success:
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert payload["mode"] == "check_batch"
    else:
        assert proc.returncode == 1, proc.stdout + proc.stderr
        assert "Roi Soleil" in payload["error"]
    assert not (root / "canon.json").exists()


def test_check_batch_rejects_schema_invalid_item_naming_offender(tmp_path):
    root = make_durable_root(tmp_path)
    canon_path = write_canon(root, canon_file_doc())
    before_bytes = canon_path.read_bytes()

    frag = write_fragment(
        root,
        # Missing canonical_target_form/basis/confidence -- Pass-1 failure.
        [{"source_form": "Rome", "is_proper_name": True, "disposition": "accepted"}],
    )
    proc = run_check_batch(root, "live", frag)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    payload = parse_stdout(proc)
    assert payload["success"] is False
    assert "Rome" in payload["error"]
    assert canon_path.read_bytes() == before_bytes


def test_check_batch_manifest_exact_coverage_accepts(tmp_path):
    root = make_durable_root(tmp_path)
    frag = write_fragment(
        root,
        [
            accepted_batch_item("Guerin", canonical_target_form="Gerin", basis="transliterated", confidence="medium"),
            queued_batch_item("Provence", note="disputed"),
        ],
    )
    manifest = write_manifest(root, ["Guerin", "Provence"])
    proc = run_check_batch(root, "live", frag, manifest_path=manifest)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    payload = parse_stdout(proc)
    assert payload["success"] is True
    assert payload["source_forms"] == 2


def test_check_batch_manifest_subset_fragment_missing_coverage_is_rejected(tmp_path):
    # The retired canon_batch_inline_shape_drift.test.py's coverage-omission
    # case moves here: a fragment that is a STRICT SUBSET of the manifest
    # (never addresses one of the manifest's candidates) must be rejected.
    root = make_durable_root(tmp_path)
    frag = write_fragment(
        root,
        [accepted_batch_item("Guerin", canonical_target_form="Gerin", basis="transliterated", confidence="medium")],
    )
    manifest = write_manifest(root, ["Guerin", "Provence"])
    proc = run_check_batch(root, "live", frag, manifest_path=manifest)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    payload = parse_stdout(proc)
    assert payload["success"] is False
    assert "Provence" in payload["error"]
    assert not (root / "canon.json").exists()


def test_check_batch_manifest_extra_source_form_not_in_manifest_is_rejected(tmp_path):
    root = make_durable_root(tmp_path)
    frag = write_fragment(
        root,
        [
            accepted_batch_item("Guerin", canonical_target_form="Gerin", basis="transliterated", confidence="medium"),
            accepted_batch_item("Zurich", canonical_target_form="Zurich", basis="transliterated", confidence="high"),
        ],
    )
    manifest = write_manifest(root, ["Guerin"])
    proc = run_check_batch(root, "live", frag, manifest_path=manifest)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    payload = parse_stdout(proc)
    assert payload["success"] is False
    assert "Zurich" in payload["error"]
    assert not (root / "canon.json").exists()


def test_check_batch_rejects_truncated_invalid_json_fragment(tmp_path):
    """#101 resume-skip safety, Python half: a fragment that is not valid
    JSON at all (e.g. a write truncated mid-flight -- the atomic dot-temp +
    rename discipline in the dispatch prompt exists precisely to avoid ever
    exposing such a file at out_{index}.json, but a corrupt fragment that
    does slip through must still be REJECTED, never trusted) is rejected by
    `--check-batch` with a clean non-zero exit and no canon.json write. This
    is what makes the glossary-pass-wf.template.js resume-skip precheck safe:
    a corrupt pre-existing fragment fails the same `--check-batch` command,
    so the template falls THROUGH to a fresh dispatch (the template-control-
    flow half of this property lives in the node-harness test file,
    batch_size_estimator.test.py). `_read_json_file` surfaces the
    JSONDecodeError as a CanonValidationError before any schema check runs."""
    root = make_durable_root(tmp_path)
    frag = root / "out_0.json"
    # A JSON array truncated mid-object -- json.loads raises JSONDecodeError.
    frag.write_text('[{"source_form": "Guerin", "disposition": "accep', encoding="utf-8")
    proc = run_check_batch(root, "live", frag)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    payload = parse_stdout(proc)
    assert payload["success"] is False
    assert "not valid JSON" in payload["error"], payload
    assert not (root / "canon.json").exists()


# ===========================================================================
# 4. --research-mode stays required for every new mode.
# ===========================================================================


def test_check_batch_requires_research_mode(tmp_path):
    root = make_durable_root(tmp_path)
    frag = write_fragment(
        root,
        [accepted_batch_item("Guerin", canonical_target_form="Gerin", basis="transliterated", confidence="medium")],
    )
    cmd = [sys.executable, str(root / "scripts" / "canon_validate.py"), "--check-batch", str(frag)]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30, cwd=str(root))
    assert proc.returncode == 2, proc.stdout + proc.stderr


def test_merge_batches_requires_research_mode(tmp_path):
    root = make_durable_root(tmp_path)
    frag = write_fragment(
        root,
        [accepted_batch_item("Guerin", canonical_target_form="Gerin", basis="transliterated", confidence="medium")],
    )
    cmd = [sys.executable, str(root / "scripts" / "canon_validate.py"), "--merge-batches", str(frag)]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30, cwd=str(root))
    assert proc.returncode == 2, proc.stdout + proc.stderr


def test_merge_batches_rejects_expect_source_forms_file(tmp_path):
    # --merge-batches does not enforce source-form coverage (that is --check-batch
    # per fragment + --verify-merged for the merged set). Passing
    # --expect-source-forms-file with --merge-batches must FAIL LOUD (exit 2), not
    # silently drop the flag and imply coverage was verified.
    root = make_durable_root(tmp_path)
    frag = write_fragment(
        root,
        [accepted_batch_item("Guerin", canonical_target_form="Gerin", basis="transliterated", confidence="medium")],
    )
    manifest = root / "manifest.json"
    manifest.write_text(json.dumps(["Guerin"]), encoding="utf-8")
    cmd = [
        sys.executable, str(root / "scripts" / "canon_validate.py"),
        "--merge-batches", str(frag),
        "--research-mode", "live",
        "--expect-source-forms-file", str(manifest),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30, cwd=str(root))
    assert proc.returncode == 2, proc.stdout + proc.stderr
    # 1.15.0: the refusal moved into canon_validate.py's MODE_SPECS table, so
    # the sentence frame is now uniform across every refusing mode. The REASON
    # is unchanged and still the point of this assertion -- an operator must
    # be told where coverage IS enforced, not merely that the flag was
    # refused.
    assert "--merge-batches does not accept --expect-source-forms-file" in proc.stderr, proc.stderr
    assert "coverage is enforced by --check-batch per fragment" in proc.stderr, proc.stderr


# ===========================================================================
# 5. canon.json-untouched invariants -- CONTRACT §3's reordering fix.
# ===========================================================================


def test_merge_batches_pass1_failure_on_any_fragment_leaves_canon_untouched(tmp_path):
    root = make_durable_root(tmp_path)
    canon_path = write_canon(
        root,
        canon_file_doc(
            entries={
                "Zurich": canon_entry(
                    "Zurich", canonical_target_form="Zurich", basis="transliterated", confidence="high"
                )
            }
        ),
    )
    before_bytes = canon_path.read_bytes()

    frag_good = write_fragment(
        root,
        [accepted_batch_item("Guerin", canonical_target_form="Gerin", basis="transliterated", confidence="medium")],
        name="out_0.json",
    )
    frag_bad = write_fragment(
        root,
        [{"source_form": "Rome", "is_proper_name": True, "disposition": "accepted"}],
        name="out_1.json",
    )

    proc = run_merge_batches(root, "live", [frag_good, frag_bad])
    assert proc.returncode == 1, proc.stdout + proc.stderr
    payload = parse_stdout(proc)
    assert payload["success"] is False
    assert "Rome" in payload["error"]
    assert canon_path.read_bytes() == before_bytes, (
        "a Pass-1 failure on ANY fragment must abort before any write -- "
        "all fragments are validated before threading the merge"
    )


def test_merge_batches_pass2_failure_on_stale_malformed_existing_entry_leaves_canon_untouched(tmp_path):
    """The actual regression the CONTRACT's reordering fix closes.

    Seed canon.json with an existing entries{} value that is ALREADY
    malformed in a way Pass-1 (per-batch-item validation of the NEW
    fragment only) can never catch -- `_merge_batch`'s own docstring is
    explicit that "an already-frozen canon.json is not retroactively
    re-litigated". The one NEW batch item here is itself perfectly
    schema-valid, so the only thing that can catch the stale malformed
    entry is Pass 2's whole-file re-validation of the merged accumulator
    (canon-file.schema.json's entries{} additionalProperties $ref's
    canon-entry.schema.json, whose 'basis' enum does not include
    "mythical").

    Before the CONTRACT's fix, canon_validate.py wrote the merged doc to
    disk BEFORE running Pass 2, so a Pass-2 failure here would already have
    corrupted canon.json on disk (fresh generation_hashes + the new entry,
    still carrying the stale malformed one) even though the overall merge
    reported failure. The fix moves Pass 2 (on the in-memory accumulator)
    before the write, so this must leave canon.json byte-for-byte
    UNCHANGED.
    """
    root = make_durable_root(tmp_path)
    canon_path = write_canon(
        root,
        canon_file_doc(
            entries={
                "Atlantis": {
                    "source_form": "Atlantis",
                    "is_proper_name": True,
                    "canonical_target_form": "Atlantis",
                    # Not a member of canon-entry.schema.json's basis enum
                    # (established/transliterated/title/sense_translated/
                    # not_a_name) -- Pass 1 never sees this pre-existing
                    # entry, only Pass 2 (whole-file) does.
                    "basis": "mythical",
                    "confidence": "high",
                }
            }
        ),
    )
    before_bytes = canon_path.read_bytes()

    frag = write_fragment(
        root,
        [accepted_batch_item("Guerin", canonical_target_form="Gerin", basis="transliterated", confidence="medium")],
    )

    proc = run_merge_batches(root, "live", [frag])
    assert proc.returncode == 1, proc.stdout + proc.stderr
    payload = parse_stdout(proc)
    assert payload["success"] is False
    assert "Atlantis" in payload["error"] or "basis" in payload["error"]
    assert canon_path.read_bytes() == before_bytes, (
        "canon.json must remain byte-for-byte unchanged when Pass 2 fails "
        "-- a write before Pass 2 would corrupt it even on overall failure"
    )


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
