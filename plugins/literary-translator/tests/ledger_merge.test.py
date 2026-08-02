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
# of real profile.yml/canon.json/segpack machinery. Accepts an OPTIONAL
# --durable-root (LT-409), mirroring the real script's own contract: when
# given, it locates test_fixture_cache_keys.json under THAT root instead of
# its own self-anchored location -- so a test can prove ledger_merge.py
# actually forwards the flag, not merely that it tolerates an unknown arg.
FAKE_CACHE_KEY_PY = """#!/usr/bin/env python3
import argparse
import json
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seg")
    parser.add_argument("--field")
    parser.add_argument("--durable-root", default=None)
    args = parser.parse_args()
    if args.durable_root:
        durable_root = Path(args.durable_root).resolve()
    else:
        durable_root = Path(__file__).resolve().parent.parent
    keys_path = durable_root / "test_fixture_cache_keys.json"
    if not args.seg:
        sys.stderr.write("fake cache_key.py: test stub requires --seg\\n")
        return 1
    data = json.loads(keys_path.read_text(encoding="utf-8"))
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


# ---------------------------------------------------------------------------
# --durable-root PATH (LT-409, post-review correction): an explicit,
# caller-supplied DATA root (schemas/segments/runs) -- REPLACES self-
# anchoring for data when given. Deliberately does NOT redirect where the
# cache_key.py sibling script is found -- that is --plugin-root's own,
# independent concern (see the dedicated section below). Byte-identical to
# today's self-anchored behavior for both when both flags are omitted.
# ---------------------------------------------------------------------------

def run_merge_from(script_path, *extra_args, timeout=30):
    return subprocess.run(
        [sys.executable, str(script_path), *extra_args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def test_durable_root_flag_absent_orphan_copy_fails_self_anchored(tmp_path):
    """Negative control: an orphan copy invoked WITHOUT --durable-root
    cannot succeed via self-anchoring (no schemas/ dir to even load the
    ledger schemas from)."""
    orphan_dir = tmp_path / "orphan_location" / "scripts"
    orphan_dir.mkdir(parents=True)
    orphan_script = orphan_dir / "ledger_merge.py"
    shutil.copy2(SCRIPT_SRC, orphan_script)

    proc = run_merge_from(orphan_script)

    assert proc.returncode == 1
    payload = parse_stdout(proc)
    assert payload["success"] is False


def test_durable_root_flag_omitted_preserves_todays_behavior(tmp_path):
    """Backward compatibility: the ordinary in-place fixture, invoked with
    no --durable-root/--plugin-root at all, behaves exactly as before."""
    root = make_durable_root(tmp_path)
    write_fragment(root, "seg01", pending_fragment())

    proc = run_merge(root)

    assert proc.returncode == 0
    payload = parse_stdout(proc)
    assert payload["success"] is True
    assert payload["n_segments"] == 1


# ---------------------------------------------------------------------------
# --plugin-root PATH (LT-409, post-review correction): the SECURITY property
# this flag exists for. ${durable_root}/scripts/ is a Step-0a copy that the
# codex process can write to (codex_job.py grants --write over the whole
# durable root), so a sibling script resolved FROM durable_root could be a
# tampered copy validating itself. --plugin-root is a SEPARATE, orthogonal
# input that must NEVER be derived from --durable-root.
# ---------------------------------------------------------------------------

def tampered_cache_key_py_src(fake_matching_key: dict) -> str:
    """A fake cache_key.py that ALWAYS echoes back `fake_matching_key`
    regardless of --seg/--durable-root, simulating a codex-tampered copy
    designed to make every segment look converged (its output always equals
    whatever the fragment's own STORED cache_key is) rather than genuinely
    recomputing anything."""
    return (
        "#!/usr/bin/env python3\n"
        "import json\n"
        "import sys\n"
        f"print(json.dumps({fake_matching_key!r}))\n"
        "sys.exit(0)\n"
    )


def make_trusted_plugin_root(tmp_path, name="trusted_plugin_install"):
    """A SEPARATE physical location holding the REAL (test-stub)
    cache_key.py at the {plugin_root}/assets/scripts/ layout SKILL.md
    documents for the plugin-anchored scripts."""
    plugin_root = tmp_path / name
    plugin_scripts_dir = plugin_root / "assets" / "scripts"
    plugin_scripts_dir.mkdir(parents=True)
    (plugin_scripts_dir / "cache_key.py").write_text(FAKE_CACHE_KEY_PY, encoding="utf-8")
    return plugin_root


def test_plugin_root_flag_bypasses_a_tampered_durable_root_sibling(tmp_path):
    """The core property: ledger_merge.py runs from its OWN in-place
    durable-root copy (production's normal invocation shape) whose SIBLING
    cache_key.py has been TAMPERED to always echo the fragment's own
    STORED cache_key back (i.e. always "not stale", regardless of what
    genuinely changed). --plugin-root pointing at a separate, untampered
    location must make it use THAT cache_key.py instead -- the genuinely
    mismatching current_key must be detected as stale, proving the
    poisoned durable-root sibling was never consulted."""
    root = make_durable_root(tmp_path)
    stored_key = make_cache_key("stored")
    current_key = make_cache_key("current")  # deliberately mismatches
    write_fixture_cache_keys(root, {"seg01": current_key})
    write_fragment(root, "seg01", converged_fragment(stored_key))
    (root / "scripts" / "cache_key.py").write_text(
        tampered_cache_key_py_src(stored_key), encoding="utf-8"
    )

    plugin_root = make_trusted_plugin_root(tmp_path)

    proc = run_merge(root, "--plugin-root", str(plugin_root))

    assert proc.returncode == 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    payload = parse_stdout(proc)
    assert payload["success"] is True
    assert payload["stale_segments"] == ["seg01"], (
        "the trusted plugin-root cache_key.py must have run (reporting the "
        f"genuine mismatch) -- a poisoned durable-root copy running instead "
        f"would report NO staleness: {payload}"
    )


def test_plugin_root_flag_absent_uses_the_poisoned_durable_root_sibling(tmp_path):
    """Negative control, and backward-compat proof in one: the SAME
    poisoned durable-root cache_key.py, invoked WITHOUT --plugin-root, is
    exactly what today's self-anchored lookup finds -- unchanged. It
    genuinely runs and always reports "not stale", proving the positive
    test's detection above is attributable to --plugin-root specifically."""
    root = make_durable_root(tmp_path)
    stored_key = make_cache_key("stored")
    current_key = make_cache_key("current")
    write_fixture_cache_keys(root, {"seg01": current_key})
    write_fragment(root, "seg01", converged_fragment(stored_key))
    (root / "scripts" / "cache_key.py").write_text(
        tampered_cache_key_py_src(stored_key), encoding="utf-8"
    )

    proc = run_merge(root)  # no --plugin-root

    assert proc.returncode == 0
    payload = parse_stdout(proc)
    assert payload["success"] is True
    assert payload["stale_segments"] == [], (
        f"without --plugin-root, the poisoned copy runs and always reports "
        f"'not stale' -- got: {payload}"
    )


def test_durable_root_and_plugin_root_are_independently_resolved(tmp_path):
    """Orthogonality, end to end, from a fully orphan copy: --durable-root
    points at a DATA-only fixture with NO scripts/ directory AT ALL,
    --plugin-root points at a SEPARATE, scripts-only fixture with no data
    of its own. Success proves the two concerns are genuinely resolved
    independently, never conflated into one root."""
    data_root = tmp_path / "data_only"
    data_root.mkdir()
    schemas_dir = data_root / "schemas"
    shutil.copytree(SCHEMAS_SRC, schemas_dir)
    (data_root / "runs" / "ledger.d").mkdir(parents=True)
    current_key = make_cache_key("current")
    write_fixture_cache_keys(data_root, {"seg01": current_key})
    write_fragment(data_root, "seg01", converged_fragment(dict(current_key)))
    assert not (data_root / "scripts").exists(), (
        "fixture bug: data_root must have NO scripts/ dir at all"
    )

    plugin_root = make_trusted_plugin_root(tmp_path, name="plugin_only")

    orphan_dir = tmp_path / "orphan_location" / "scripts"
    orphan_dir.mkdir(parents=True)
    orphan_script = orphan_dir / "ledger_merge.py"
    shutil.copy2(SCRIPT_SRC, orphan_script)

    proc = run_merge_from(
        orphan_script,
        "--durable-root", str(data_root),
        "--plugin-root", str(plugin_root),
    )

    assert proc.returncode == 0, (
        f"durable-root (data) and plugin-root (sibling) must resolve "
        f"independently -- got rc={proc.returncode}\nstdout:\n{proc.stdout}\n"
        f"stderr:\n{proc.stderr}"
    )
    payload = parse_stdout(proc)
    assert payload["success"] is True
    assert payload["n_segments"] == 1
    assert payload["stale_segments"] == []  # matching key -> not stale
    assert (data_root / "runs" / "ledger.json").is_file()
    assert not (plugin_root / "runs").exists()


def test_plugin_root_flag_omitted_preserves_todays_behavior(tmp_path):
    """Backward compatibility for the split itself: --durable-root alone
    (no --plugin-root) still resolves the sibling self-anchored, exactly as
    before the split -- an in-place fixture with an UNTAMPERED cache_key.py
    still succeeds via --durable-root alone."""
    root = make_durable_root(tmp_path)
    write_fragment(root, "seg01", pending_fragment())

    proc = run_merge(root, "--durable-root", str(root))

    assert proc.returncode == 0
    payload = parse_stdout(proc)
    assert payload["success"] is True
    assert payload["n_segments"] == 1


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
