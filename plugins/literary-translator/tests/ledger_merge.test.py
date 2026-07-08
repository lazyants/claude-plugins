"""tests/ledger_merge.test.py -- tests for scripts/ledger_merge.py.

See references/ledger-and-resumability.md, section "`mergeLedgerPrompt` /
`ledger_merge.py` -- completeness verification" for the authoritative spec
this script implements; this file exercises exactly the three things that
spec makes ledger_merge.py responsible for:

  1. Materializing `runs/ledger.d/*.json` fragments into the single
     `ledger.json`'s `{"segments": {...}}` shape (`ledger.schema.json`),
     regardless of which per-segment status each fragment carries.
  2. Computing `stale` ITSELF: for every fragment whose on-disk `status` is
     `converged`, shelling out to `cache_key.py --seg <id>` and comparing the
     result field-by-field against the fragment's own stored `cache_key`. A
     mismatch flips that segment's status to `stale` in the MATERIALIZED
     `ledger.json` only -- the on-disk fragment file itself is never
     rewritten (asserted directly: the fragment bytes/status on disk are
     re-read after every merge and must be byte-for-byte what this test
     wrote, `converged`, never `stale`).
  3. The completeness check: triggered by either `--expected-from-manifest`
     or `--expected-segs` (and it is a SUBSET/completeness check, never exact
     key-set equality -- extra fragments from prior batches are allowed);
     skipped entirely (trivially empty `missing_segments`, no failure) when
     neither flag is passed, even if the on-disk fragment set is obviously
     incomplete relative to some hypothetical full project.

Following this plugin's established convention for scripts that self-anchor
their durable_root via `Path(__file__).resolve().parents[1]`
(`validate_draft.test.py`'s `make_durable_root` pattern): every test copies
the REAL `ledger_merge.py` and the REAL `assets/schemas/*.schema.json` files
into an isolated `tmp_path` fixture root and invokes it exactly as it is
invoked in production -- `python3 {durable_root}/scripts/ledger_merge.py
[flags]` -- so its self-anchoring resolves against the fixture, never this
repo's real assets tree.

`cache_key.py` itself is stubbed out with a small fixture script that reads
a test-controlled `test_fixture_cache_keys.json` mapping `{seg: <15-field
cache_key dict>}` and prints the requested segment's entry verbatim. This
keeps the test scoped to ledger_merge.py's OWN comparison/materialization
logic (the real cache_key.py's 15-field hashing algorithm has its own
dedicated test file, `ledger_composite_key.test.py`) while still exercising
the real subprocess call path (`subprocess.run([sys.executable,
str(CACHE_KEY_SCRIPT), "--seg", seg], ...)`) with a script that behaves like
the real one at the only interface ledger_merge.py actually depends on:
`--seg <id>` prints a JSON object to stdout.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
ASSETS_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets"
SCRIPT_SRC = ASSETS_DIR / "scripts" / "ledger_merge.py"
SCHEMAS_SRC = ASSETS_DIR / "schemas"

assert SCRIPT_SRC.is_file(), f"ledger_merge.py not found at {SCRIPT_SRC}"
assert SCHEMAS_SRC.is_dir(), f"schemas dir not found at {SCHEMAS_SRC}"

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

# A fixture stand-in for the real cache_key.py -- same `--seg <id>` -> JSON
# object stdout interface, sourced from a test-controlled lookup file instead
# of real profile.yml/canon.json/segpack machinery.
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


# ---------------------------------------------------------------------------
# Fixture harness
# ---------------------------------------------------------------------------

def make_durable_root(tmp_path):
    """Builds an isolated durable_root: copies the REAL ledger_merge.py and
    the REAL assets/schemas/*.schema.json files into {root}/scripts/ and
    {root}/schemas/ (so ledger_merge.py's self-anchored SCHEMAS_DIR resolves
    correctly), installs the fake cache_key.py stub alongside it, and
    creates an empty runs/ledger.d/.
    """
    root = tmp_path / "durable_root"
    scripts_dir = root / "scripts"
    scripts_dir.mkdir(parents=True)
    shutil.copy2(SCRIPT_SRC, scripts_dir / "ledger_merge.py")
    (scripts_dir / "cache_key.py").write_text(FAKE_CACHE_KEY_PY, encoding="utf-8")

    schemas_dir = root / "schemas"
    shutil.copytree(SCHEMAS_SRC, schemas_dir)

    (root / "runs" / "ledger.d").mkdir(parents=True)
    return root


def write_fragment(root, seg, record):
    frag_path = root / "runs" / "ledger.d" / f"{seg}.json"
    frag_path.write_text(
        json.dumps(record, ensure_ascii=False, sort_keys=True), encoding="utf-8"
    )
    return frag_path


def write_fixture_cache_keys(root, mapping):
    (root / "test_fixture_cache_keys.json").write_text(
        json.dumps(mapping, ensure_ascii=False), encoding="utf-8"
    )


def run_merge(root, *extra_args, timeout=30):
    return subprocess.run(
        [sys.executable, str(root / "scripts" / "ledger_merge.py"), *extra_args],
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=str(root),
    )


def parse_stdout(proc):
    assert proc.stdout.strip(), f"expected one JSON line on stdout, got none. stderr:\n{proc.stderr}"
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    assert len(lines) == 1, f"expected exactly one JSON line, got:\n{proc.stdout}"
    return json.loads(lines[0])


def make_cache_key(seed):
    """A full, schema-valid 15-field cache_key dict. Every field's value is
    derived from `seed` so two different seeds are guaranteed to produce a
    field-by-field mismatch in every one of the 15 fields simultaneously.
    """
    return {field: f"{field}-{seed}" for field in CACHE_KEY_FIELDS}


def converged_fragment(cache_key, rounds=1):
    return {
        "timestamp": "2026-01-01T00:00:00Z",
        "status": "converged",
        "rounds": rounds,
        "cache_key": cache_key,
        "n_blocks": 3,
        "n_footnotes": 1,
        "n_verses": 0,
        "reviewed_draft_sha1": "d" * 40,
    }


def pending_fragment():
    return {"timestamp": "2026-01-01T00:00:00Z", "status": "pending"}


def in_progress_fragment():
    return {"timestamp": "2026-01-01T00:00:00Z", "status": "in_progress"}


def non_converged_fragment(reason="cap", rounds=4):
    return {
        "timestamp": "2026-01-01T00:00:00Z",
        "status": "non_converged",
        "reason": reason,
        "rounds": rounds,
    }


def blocked_fragment(reason="review-null"):
    return {
        "timestamp": "2026-01-01T00:00:00Z",
        "status": "blocked",
        "reason": reason,
    }


# ---------------------------------------------------------------------------
# 1. Materializing fragments into ledger.schema.json's segments{} shape
# ---------------------------------------------------------------------------

def test_materializes_all_fragment_statuses_into_segments_shape(tmp_path):
    root = make_durable_root(tmp_path)
    key_a = make_cache_key("A")
    write_fixture_cache_keys(root, {"seg01": key_a})

    write_fragment(root, "seg01", converged_fragment(key_a))
    write_fragment(root, "seg02", pending_fragment())
    write_fragment(root, "seg03", in_progress_fragment())
    write_fragment(root, "seg04", non_converged_fragment())
    write_fragment(root, "seg05", blocked_fragment())

    proc = run_merge(root)
    assert proc.returncode == 0, proc.stderr
    payload = parse_stdout(proc)
    assert payload["success"] is True
    assert payload["n_segments"] == 5
    assert payload["missing_segments"] == []
    assert payload["stale_segments"] == []  # seg01's cache_key matches -> not stale

    ledger_path = root / "runs" / "ledger.json"
    assert ledger_path.is_file()
    assert payload["ledger_path"] == str(ledger_path)
    doc = json.loads(ledger_path.read_text(encoding="utf-8"))
    assert set(doc["segments"].keys()) == {"seg01", "seg02", "seg03", "seg04", "seg05"}
    assert doc["segments"]["seg01"]["status"] == "converged"
    assert doc["segments"]["seg02"]["status"] == "pending"
    assert doc["segments"]["seg03"]["status"] == "in_progress"
    assert doc["segments"]["seg04"]["status"] == "non_converged"
    assert doc["segments"]["seg04"]["reason"] == "cap"
    assert doc["segments"]["seg05"]["status"] == "blocked"


def test_materializes_empty_ledger_when_no_fragments_exist(tmp_path):
    root = make_durable_root(tmp_path)
    write_fixture_cache_keys(root, {})

    proc = run_merge(root)
    assert proc.returncode == 0, proc.stderr
    payload = parse_stdout(proc)
    assert payload["success"] is True
    assert payload["n_segments"] == 0
    assert payload["stale_segments"] == []

    doc = json.loads((root / "runs" / "ledger.json").read_text(encoding="utf-8"))
    assert doc == {"segments": {}}


# ---------------------------------------------------------------------------
# 2. Stale computation via cache_key.py --seg <id>, materialized-only,
#    fragment never rewritten
# ---------------------------------------------------------------------------

def test_cache_key_mismatch_flips_status_to_stale_in_materialized_output_only(tmp_path):
    root = make_durable_root(tmp_path)
    stored_key = make_cache_key("stored")
    current_key = make_cache_key("current")  # every field differs from stored_key
    write_fixture_cache_keys(root, {"seg01": current_key})

    frag_path = write_fragment(root, "seg01", converged_fragment(stored_key, rounds=2))
    fragment_bytes_before = frag_path.read_bytes()

    proc = run_merge(root)
    assert proc.returncode == 0, proc.stderr
    payload = parse_stdout(proc)
    assert payload["success"] is True
    assert payload["stale_segments"] == ["seg01"]

    # Materialized ledger.json: status flipped to 'stale'.
    doc = json.loads((root / "runs" / "ledger.json").read_text(encoding="utf-8"))
    assert doc["segments"]["seg01"]["status"] == "stale"
    # The rest of the record's fields survive untouched (the OLD/stored
    # cache_key, not the freshly recomputed mismatching one).
    assert doc["segments"]["seg01"]["cache_key"] == stored_key
    assert doc["segments"]["seg01"]["rounds"] == 2

    # The on-disk fragment itself is NEVER rewritten: still 'converged',
    # still carries the original stored cache_key, byte-identical to what
    # this test wrote before running the merge.
    assert frag_path.read_bytes() == fragment_bytes_before
    frag_doc = json.loads(frag_path.read_text(encoding="utf-8"))
    assert frag_doc["status"] == "converged"
    assert frag_doc["cache_key"] == stored_key


def test_cache_key_match_leaves_converged_status_unchanged(tmp_path):
    root = make_durable_root(tmp_path)
    key = make_cache_key("same")
    write_fixture_cache_keys(root, {"seg01": key})
    write_fragment(root, "seg01", converged_fragment(key))

    proc = run_merge(root)
    assert proc.returncode == 0, proc.stderr
    payload = parse_stdout(proc)
    assert payload["stale_segments"] == []

    doc = json.loads((root / "runs" / "ledger.json").read_text(encoding="utf-8"))
    assert doc["segments"]["seg01"]["status"] == "converged"


def test_partial_cache_key_mismatch_in_a_single_field_still_flags_stale(tmp_path):
    # A mismatch in just ONE of the 15 fields must still flip the segment to
    # stale -- the comparison is per-field, not a whole-object identity check
    # that could be fooled by dict key ordering or similar.
    root = make_durable_root(tmp_path)
    stored_key = make_cache_key("X")
    current_key = dict(stored_key)
    current_key["verse_map_hash"] = "verse_map_hash-DIFFERENT"
    write_fixture_cache_keys(root, {"seg01": current_key})
    write_fragment(root, "seg01", converged_fragment(stored_key))

    proc = run_merge(root)
    payload = parse_stdout(proc)
    assert payload["stale_segments"] == ["seg01"]


def test_stale_check_only_applies_to_converged_fragments(tmp_path):
    # Non-converged statuses (which never carry a cache_key per
    # ledger-fragment.schema.json) must never be recomputed/misclassified as
    # stale, regardless of what the fixture cache_key stub would return.
    root = make_durable_root(tmp_path)
    write_fixture_cache_keys(root, {})  # no entries at all -- would error if queried

    write_fragment(root, "seg01", pending_fragment())
    write_fragment(root, "seg02", in_progress_fragment())
    write_fragment(root, "seg03", non_converged_fragment())
    write_fragment(root, "seg04", blocked_fragment())

    proc = run_merge(root)
    assert proc.returncode == 0, proc.stderr
    payload = parse_stdout(proc)
    assert payload["success"] is True
    assert payload["stale_segments"] == []

    doc = json.loads((root / "runs" / "ledger.json").read_text(encoding="utf-8"))
    for seg in ("seg01", "seg02", "seg03", "seg04"):
        assert doc["segments"][seg]["status"] != "stale"


def test_skip_stale_check_flag_suppresses_recomputation(tmp_path):
    root = make_durable_root(tmp_path)
    stored_key = make_cache_key("stored")
    current_key = make_cache_key("current")
    write_fixture_cache_keys(root, {"seg01": current_key})
    write_fragment(root, "seg01", converged_fragment(stored_key))

    proc = run_merge(root, "--skip-stale-check")
    assert proc.returncode == 0, proc.stderr
    payload = parse_stdout(proc)
    assert payload["stale_segments"] == []

    doc = json.loads((root / "runs" / "ledger.json").read_text(encoding="utf-8"))
    assert doc["segments"]["seg01"]["status"] == "converged"


# ---------------------------------------------------------------------------
# 3. Completeness check: --expected-segs / --expected-from-manifest detect a
#    genuinely missing fragment; skipped entirely with neither flag.
# ---------------------------------------------------------------------------

def test_no_expected_flag_skips_completeness_check_even_when_incomplete(tmp_path):
    # Only two fragments exist on disk; a "complete" project would obviously
    # have more. With neither --expected-from-manifest nor --expected-segs,
    # the completeness check must be skipped entirely -- this must still
    # succeed, not fail on some implicit notion of completeness.
    root = make_durable_root(tmp_path)
    write_fixture_cache_keys(root, {})
    write_fragment(root, "seg01", pending_fragment())
    write_fragment(root, "seg02", in_progress_fragment())

    proc = run_merge(root)
    assert proc.returncode == 0, proc.stderr
    payload = parse_stdout(proc)
    assert payload["success"] is True
    assert payload["missing_segments"] == []
    assert payload["n_segments"] == 2


def test_expected_segs_detects_genuinely_missing_fragment(tmp_path):
    root = make_durable_root(tmp_path)
    write_fixture_cache_keys(root, {})
    write_fragment(root, "seg01", pending_fragment())
    write_fragment(root, "seg02", pending_fragment())
    # seg03 is expected but has no fragment at all.

    ledger_path = root / "runs" / "ledger.json"
    proc = run_merge(root, "--expected-segs", "seg01,seg02,seg03")
    assert proc.returncode == 1
    payload = parse_stdout(proc)
    assert payload["success"] is False
    assert payload["missing_segments"] == ["seg03"]
    assert "seg03" in payload["error"]
    # A failed completeness check must never materialize ledger.json.
    assert not ledger_path.exists()


def test_expected_segs_success_allows_subset_not_exact_equality(tmp_path):
    # ledger.json legitimately accumulates fragments across every batch ever
    # run -- extra fragments beyond the currently expected partial-batch list
    # must NOT cause a failure. This is a completeness/subset check, never
    # exact key-set equality.
    root = make_durable_root(tmp_path)
    write_fixture_cache_keys(root, {})
    write_fragment(root, "seg01", pending_fragment())
    write_fragment(root, "seg02", pending_fragment())
    write_fragment(root, "seg03", pending_fragment())  # from a prior batch

    proc = run_merge(root, "--expected-segs", "seg01,seg02")
    assert proc.returncode == 0, proc.stderr
    payload = parse_stdout(proc)
    assert payload["success"] is True
    assert payload["missing_segments"] == []
    # All three fragments materialize, including the one not in this batch's
    # expected list.
    assert payload["n_segments"] == 3
    doc = json.loads((root / "runs" / "ledger.json").read_text(encoding="utf-8"))
    assert set(doc["segments"].keys()) == {"seg01", "seg02", "seg03"}


def test_expected_from_manifest_detects_genuinely_missing_fragment(tmp_path):
    root = make_durable_root(tmp_path)
    write_fixture_cache_keys(root, {})
    write_fragment(root, "seg01", pending_fragment())
    # seg02 is listed in the manifest but has no fragment.

    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps({"segments": [{"seg": "seg01"}, {"seg": "seg02"}]}),
        encoding="utf-8",
    )

    proc = run_merge(root, "--expected-from-manifest", str(manifest_path))
    assert proc.returncode == 1
    payload = parse_stdout(proc)
    assert payload["success"] is False
    assert payload["missing_segments"] == ["seg02"]
    assert not (root / "runs" / "ledger.json").exists()


def test_expected_from_manifest_success_when_every_segment_has_a_fragment(tmp_path):
    root = make_durable_root(tmp_path)
    key = make_cache_key("A")
    write_fixture_cache_keys(root, {"seg01": key})
    write_fragment(root, "seg01", converged_fragment(key))
    write_fragment(root, "seg02", pending_fragment())

    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps({"segments": [{"seg": "seg01"}, {"seg": "seg02"}]}),
        encoding="utf-8",
    )

    proc = run_merge(root, "--expected-from-manifest", str(manifest_path))
    assert proc.returncode == 0, proc.stderr
    payload = parse_stdout(proc)
    assert payload["success"] is True
    assert payload["missing_segments"] == []
    assert payload["n_segments"] == 2


def test_expected_segs_and_stale_check_compose_in_one_run(tmp_path):
    # The completeness check and the stale-computation are independent
    # concerns that must both run correctly in the same invocation: seg02 is
    # present but stale (cache_key mismatch), seg03 is entirely missing.
    root = make_durable_root(tmp_path)
    stored_key = make_cache_key("stored")
    current_key = make_cache_key("current")
    write_fixture_cache_keys(root, {"seg02": current_key})
    write_fragment(root, "seg01", pending_fragment())
    write_fragment(root, "seg02", converged_fragment(stored_key))

    proc = run_merge(root, "--expected-segs", "seg01,seg02,seg03")
    assert proc.returncode == 1
    payload = parse_stdout(proc)
    assert payload["success"] is False
    assert payload["missing_segments"] == ["seg03"]
    # A failed completeness check short-circuits before the write -- no
    # ledger.json should exist regardless of what the stale-check would have
    # found.
    assert not (root / "runs" / "ledger.json").exists()


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
