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
import os
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

def make_durable_root(tmp_path, name="durable_root"):
    """Builds an isolated durable_root: copies the REAL ledger_merge.py and
    the REAL assets/schemas/*.schema.json files into {root}/scripts/ and
    {root}/schemas/ (so ledger_merge.py's self-anchored SCHEMAS_DIR resolves
    correctly), installs the fake cache_key.py stub alongside it, and
    creates an empty runs/ledger.d/. `name` defaults to the pre-existing
    fixed value -- a caller that needs the root at a specific relative
    nested location (e.g. to exercise a caller-relative --durable-root end
    to end) may pass e.g. name="projects/book".
    """
    root = tmp_path / name
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

def run_merge_from(script_path, *extra_args, timeout=30, cwd=None):
    """`cwd=None` (the default) preserves every pre-existing caller's
    behavior exactly (subprocess.run() with no cwd= inherits the test
    process's own cwd) -- only a caller that needs to control the
    SUBPROCESS's own working directory (e.g. to exercise a caller-relative
    --durable-root end to end) passes one explicitly."""
    return subprocess.run(
        [sys.executable, str(script_path), *extra_args],
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=str(cwd) if cwd is not None else None,
    )


def test_durable_root_flag_absent_orphan_copy_fails_self_anchored(tmp_path):
    """Negative control: an orphan copy invoked WITHOUT --durable-root
    cannot succeed via self-anchoring. Asserts the SPECIFIC reason -- no
    schemas/ dir to even load the ledger schemas from -- not merely that
    some failure occurred: a bare "it failed" cannot distinguish this
    correct refusal from an unrelated crash, so a future defect that broke
    the orphan-copy path for the WRONG reason would pass this test
    silently."""
    orphan_dir = tmp_path / "orphan_location" / "scripts"
    orphan_dir.mkdir(parents=True)
    orphan_script = orphan_dir / "ledger_merge.py"
    shutil.copy2(SCRIPT_SRC, orphan_script)

    proc = run_merge_from(orphan_script)

    assert proc.returncode == 1
    payload = parse_stdout(proc)
    assert payload["success"] is False
    assert "schemas directory not found" in (payload.get("error") or ""), (
        f"expected the orphan copy to fail specifically on its missing "
        f"schemas/ directory; got a different reason: {payload}"
    )


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


# ---------------------------------------------------------------------------
# #608: a GIVEN --plugin-root that does not resolve is a WHOLE-RUN refusal,
# not a per-segment stale-check skip.
#
# Before this, resolve_dirs() validated neither branch, so a mistyped
# --plugin-root reached _compute_stale_segments()'s deliberately non-fatal
# "cache_key.py not found -- skipping stale-check" branch once PER SEGMENT.
# Every converged segment was therefore left unchecked while the merge printed
# its ordinary success line and materialized runs/ledger.json -- a silent false
# green, and precisely the state the flag exists to make impossible. The
# per-segment policy is unchanged; only the whole-run precondition is new.
#
# The BEHAVIOURAL red (that today's code reports success) and the ORDERING red
# (that the refusal precedes any fragment read) are deliberately kept in
# SEPARATE tests. A single test carrying both fixtures would be red on
# unpatched code for the ordering fixture's reason and could never witness the
# false green it exists for.
# ---------------------------------------------------------------------------

def test_plugin_root_that_does_not_resolve_refuses(tmp_path):
    """The REFUSE direction, behavioural. A converged fragment whose stored
    key MATCHES what the fixture stub reports -- so with a WORKING checker
    this merge succeeds and reports no staleness, and the only thing under
    test is the unresolvable root itself.

    Pre-fix this exits 0 with success=True and writes runs/ledger.json, having
    skipped the stale-check for every segment. That false green is this test's
    red."""
    root = make_durable_root(tmp_path)
    key = make_cache_key("stored")
    write_fixture_cache_keys(root, {"seg01": key})
    write_fragment(root, "seg01", converged_fragment(key))
    missing_plugin_root = tmp_path / "nonexistent_plugin_install"

    proc = run_merge(root, "--plugin-root", str(missing_plugin_root))

    assert proc.returncode == 1, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    payload = parse_stdout(proc)
    assert payload["success"] is False
    error = payload["error"]
    assert "--plugin-root" in error, error
    assert str(missing_plugin_root) in error, (
        f"the error must name the root the operator actually passed: {error}"
    )
    assert str(missing_plugin_root / "assets" / "scripts") in error, (
        f"the error must name the RESOLVED path it looked for: {error}"
    )
    assert not (root / "runs" / "ledger.json").exists(), (
        "the merge must refuse without materializing a ledger -- a ledger "
        "written here is the false green this refusal exists to prevent"
    )


def test_plugin_root_refusal_precedes_any_fragment_read(tmp_path):
    """The ORDERING pin, and the reason the test above is not enough: a guard
    placed ANYWHERE before the ledger write leaves runs/ledger.json absent, so
    the absent file cannot distinguish "refused first" from "refused after
    reading every fragment and running every checker".

    The tripwire is a second fragment file holding invalid JSON.
    _read_fragments() must parse EVERY fragment before it returns, so the
    tripwire raises `invalid JSON in fragment ...` wherever its name happens to
    sort -- the name below is arbitrary. So the assertion is on WHICH error
    comes back: a guard at or below _read_fragments() yields the fragment
    error; only a guard above it yields the --plugin-root error.

    Its red on unpatched code is therefore the FRAGMENT error, not the false
    green -- a different red from the behavioural test above, by design."""
    root = make_durable_root(tmp_path)
    key = make_cache_key("stored")
    write_fixture_cache_keys(root, {"seg01": key})
    write_fragment(root, "seg01", converged_fragment(key))
    tripwire = root / "runs" / "ledger.d" / "000_tripwire.json"
    tripwire.write_text("{ this is not json", encoding="utf-8")
    missing_plugin_root = tmp_path / "nonexistent_plugin_install"

    proc = run_merge(root, "--plugin-root", str(missing_plugin_root))

    assert proc.returncode == 1, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    payload = parse_stdout(proc)
    assert payload["success"] is False
    error = payload["error"]
    assert "--plugin-root" in error, (
        "the --plugin-root refusal must come back, NOT the tripwire fragment's "
        f"parse error -- getting the fragment error means the guard runs at or "
        f"below _read_fragments(): {error}"
    )
    assert "invalid JSON in fragment" not in error, (
        f"a fragment was read before the refusal: {error}"
    )


def test_plugin_root_empty_string_refuses_even_when_cwd_has_assets_scripts(tmp_path):
    """The empty/whitespace leg, which a bare is_dir() check would let
    through: Path("").resolve() is the CURRENT WORKING DIRECTORY, and
    run_merge() runs with cwd=root. Planting assets/scripts/cache_key.py under
    that cwd makes the naive check PASS and silently run the cwd copy.

    That planted copy is the FAKE stub, which reports the fixture's `current`
    key -- deliberately mismatching the fragment's stored key -- so pre-fix
    this merge exits 0, succeeds, and reports seg01 stale off a checker the
    operator never pointed at. The refusal is what this test pins."""
    root = make_durable_root(tmp_path)
    stored_key = make_cache_key("stored")
    current_key = make_cache_key("current")
    write_fixture_cache_keys(root, {"seg01": current_key})
    write_fragment(root, "seg01", converged_fragment(stored_key))
    cwd_scripts = root / "assets" / "scripts"
    cwd_scripts.mkdir(parents=True)
    (cwd_scripts / "cache_key.py").write_text(FAKE_CACHE_KEY_PY, encoding="utf-8")

    proc = run_merge(root, "--plugin-root", "")

    assert proc.returncode == 1, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    payload = parse_stdout(proc)
    assert payload["success"] is False
    assert "empty/whitespace-only" in payload["error"], payload["error"]
    assert not (root / "runs" / "ledger.json").exists(), (
        "an empty --plugin-root must refuse without materializing a ledger"
    )


# ---------------------------------------------------------------------------
# A RELATIVE --durable-root must be resolved exactly ONCE (LT-409 post-review
# fix, third instance of this shape -- resume_setup.py and select_segments.py
# each had the identical bug in their own forward to cache_key.py).
# resolve_dirs() already resolves it correctly against ledger_merge.py's OWN
# cwd -- but _compute_stale_segments() then runs the cache_key.py subprocess
# with cwd SET TO that already-resolved root while (pre-fix) forwarding the
# ORIGINAL, still-relative string as the subprocess's own --durable-root.
# cache_key.py resolves ITS --durable-root against ITS OWN cwd (the
# already-resolved root) -- joining the relative fragment onto the root a
# second time. Silent either way: the parent's own success/failure comes
# from the child's stdout JSON and exit code, and a wrong-tree read that
# still produces SOME JSON object looks exactly like a genuine one.
# ---------------------------------------------------------------------------

PATH_PROBE_CACHE_KEY_PY = """#!/usr/bin/env python3
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
        resolved = Path(args.durable_root).resolve()
    else:
        resolved = Path(__file__).resolve().parent.parent
    # Record what THIS invocation resolved --durable-root to. __file__ is
    # this stub's own FIXED on-disk location, unaffected by any doubling bug
    # in the --durable-root VALUE it receives, so the probe file's own path
    # is trustworthy regardless of what is under test.
    probe_path = Path(__file__).resolve().parent.parent / "cache_key_probe.jsonl"
    with open(probe_path, "a", encoding="utf-8") as f:
        f.write(json.dumps({"seg": args.seg, "resolved_durable_root": str(resolved)}) + "\\n")
    if not args.seg:
        sys.stderr.write("path-probe cache_key.py: test stub requires --seg\\n")
        return 1
    # Always succeeds with a real-shaped (if fake) object, regardless of what
    # it resolved -- decouples "did ledger_merge.py notice a problem" from
    # "did cache_key.py read the RIGHT tree", so a doubled-path defect is
    # caught by a direct path comparison even in a build where it happens not
    # to crash (e.g. the doubled directory exists for an unrelated reason) --
    # the more dangerous, silent failure mode.
    print(json.dumps({"probe": True, "seg": args.seg}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
"""


def test_relative_durable_root_is_not_double_resolved_for_cache_key_subprocess(tmp_path):
    """Caller runs from an outer cwd (e.g. `cd /repo`) with a RELATIVE
    --durable-root (e.g. `projects/book`) -- the exact shape every real
    caller of this script COULD use, even though every other test in this
    file happens to pass an absolute one."""
    outer = tmp_path  # stands in for the caller's own cwd, e.g. "/repo"
    root = make_durable_root(outer, name="projects/book")
    (root / "scripts" / "cache_key.py").write_text(PATH_PROBE_CACHE_KEY_PY, encoding="utf-8")
    write_fragment(root, "seg01", converged_fragment(make_cache_key("s1")))
    probe_path = root / "cache_key_probe.jsonl"
    assert not probe_path.exists()  # fixture sanity: nothing recorded yet

    proc = run_merge_from(
        root / "scripts" / "ledger_merge.py",
        "--durable-root", "projects/book",  # RELATIVE, relative to `outer`
        cwd=outer,
    )

    assert probe_path.is_file(), (
        f"the probe stub never ran -- ledger_merge.py must have failed "
        f"before even shelling out to cache_key.py: rc={proc.returncode}\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    probe_lines = [
        json.loads(ln) for ln in probe_path.read_text(encoding="utf-8").splitlines() if ln.strip()
    ]
    assert probe_lines, "probe file exists but recorded no invocations"
    for entry in probe_lines:
        assert entry["resolved_durable_root"] == str(root), (
            f"cache_key.py's own --durable-root resolution must land on the "
            f"SAME root ledger_merge.py itself resolved ({root}) -- got "
            f"{entry['resolved_durable_root']!r} for seg {entry['seg']!r}. A "
            f"doubled path here (the relative fragment 'projects/book' "
            f"joined onto root a second time) means the raw relative "
            f"string was forwarded verbatim into a subprocess whose cwd is "
            f"already that resolved root."
        )

    # The probe stub never fails, so ledger_merge.py itself must have
    # reported success -- proving the wrong-tree read (pre-fix) would have
    # been entirely SILENT: a caller reading only {"success": true, ...}
    # would never learn its stale-check ran against the wrong directory.
    assert proc.returncode == 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    payload = parse_stdout(proc)
    assert payload["success"] is True


def test_relative_plugin_root_resolves_against_the_invokers_cwd_not_the_childs(tmp_path):
    """`--plugin-root` cannot suffer ledger_merge.py's OWN doubled-path bug
    (cache_key.py "does not accept --plugin-root at all" -- module docstring
    -- so the raw string is never forwarded to it as an argument); this
    checks the DISTINCT defect the select_segments.py lane found on the same
    flag elsewhere: resolve_dirs() resolves --plugin-root ITSELF, in THIS
    process, against the invoker's own cwd -- `cache_key_script =
    Path(plugin_root_str).resolve() / "assets" / "scripts" / "cache_key.py"`
    (line ~162) -- and that resolved absolute Path object is what actually
    gets used to invoke the subprocess (`subprocess.run([sys.executable,
    str(cache_key_script), ...])`), never re-derived by the child. So a
    relative --plugin-root, with no --durable-root of its own, must resolve
    against THIS process's cwd (the invoker's) and reach the untampered
    stub at that location.

    Same poisoned-vs-trusted design as
    test_plugin_root_flag_bypasses_a_tampered_durable_root_sibling above (a
    RELATIVE echo of it, not a new mechanism) -- deliberately NOT "does the
    call merely succeed", since a wrong-tree read that happens to crash
    would look identical to a correct read that reports "not stale": the
    durable-root sibling is POISONED to always claim a match (the dangerous
    false-negative direction), so only the genuinely-mismatching TRUSTED
    sibling running produces the observed `stale_segments == ["seg01"]` --
    an ambiguous "it didn't crash" is never enough here."""
    outer = tmp_path  # stands in for the caller's own cwd
    root = make_durable_root(outer, name="projects/book")
    stored_key = make_cache_key("stored")
    current_key = make_cache_key("current")  # deliberately mismatches
    write_fixture_cache_keys(root, {"seg01": current_key})
    write_fragment(root, "seg01", converged_fragment(stored_key))
    (root / "scripts" / "cache_key.py").write_text(
        tampered_cache_key_py_src(stored_key), encoding="utf-8"
    )

    make_trusted_plugin_root(outer)  # writes {outer}/trusted_plugin_install/assets/scripts/cache_key.py

    proc = run_merge_from(
        root / "scripts" / "ledger_merge.py",
        "--plugin-root", "trusted_plugin_install",  # RELATIVE, relative to `outer`
        cwd=outer,
    )

    assert proc.returncode == 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    payload = parse_stdout(proc)
    assert payload["success"] is True
    assert payload["stale_segments"] == ["seg01"], (
        "the relative --plugin-root must resolve, against the INVOKER's own "
        "cwd, to the trusted sibling (which genuinely recomputes and reports "
        "the real mismatch) -- a poisoned durable-root copy running instead "
        f"would report NO staleness: {payload}"
    )


# ---------------------------------------------------------------------------
# 4. #463 -- a merge never reports "empty" for a directory it could not read,
#    and never publishes an empty ledger over a populated one.
#
#    Two independent guards, tested independently. _read_fragments()' errno
#    split makes an unreadable ledger.d LOUD (two tests below fire with no
#    outgoing ledger at all, where the many-to-zero guard cannot reach).
#    merge()'s many-to-zero refusal constrains the OUTCOME whatever the cause,
#    including causes not enumerated here.
#
#    The refusal tests are the red-before-green witnesses: each was watched
#    failing against the unfixed script. The rest are controls -- green before
#    AND after -- and exist to pin that neither guard over-catches.
# ---------------------------------------------------------------------------

requires_non_root = pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="mode bits do not deny root, so chmod 000 cannot make a path unreadable",
)


def write_ledger_json(root, segments):
    """Writes an outgoing runs/ledger.json directly, as a prior merge would
    have left it. Returns its path."""
    path = root / "runs" / "ledger.json"
    path.write_text(
        json.dumps({"segments": segments}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def test_empty_fragment_dir_refuses_to_erase_a_populated_ledger(tmp_path):
    """RED witness. The many-to-zero guard in its simplest form: ledger.d is
    genuinely readable and genuinely empty, but ledger.json holds real
    segments. Nothing in this plugin deletes a fragment, so this transition
    cannot come from a legitimate merge -- it must not publish."""
    root = make_durable_root(tmp_path)
    write_fixture_cache_keys(root, {})
    ledger_path = write_ledger_json(
        root, {"seg01": {"status": "converged"}, "seg02": {"status": "pending"}}
    )
    before = ledger_path.read_bytes()

    proc = run_merge(root)

    assert proc.returncode == 1, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    payload = parse_stdout(proc)
    assert payload["success"] is False
    assert "2" in payload["error"], payload["error"]
    assert "ledger.json" in payload["error"], payload["error"]
    assert ledger_path.read_bytes() == before, (
        "the refusal must happen BEFORE the atomic replace -- the outgoing "
        "ledger has to be byte-for-byte untouched"
    )


@requires_non_root
def test_unreadable_populated_fragment_dir_refuses_and_leaves_the_ledger_intact(tmp_path):
    """RED witness, and the bug #463 actually reports. ledger.d holds a real
    fragment but cannot be read. Measured on the interpreter this ships
    against: at mode 0o000 `is_dir()` still answers True and `glob("*.json")`
    still answers [], so the old code saw a populated directory as empty and
    published {} over live state."""
    root = make_durable_root(tmp_path)
    key_a = make_cache_key("A")
    write_fixture_cache_keys(root, {"seg01": key_a})
    write_fragment(root, "seg01", converged_fragment(key_a))
    ledger_path = write_ledger_json(root, {"seg01": {"status": "converged"}})
    before = ledger_path.read_bytes()

    ledger_d = root / "runs" / "ledger.d"
    os.chmod(ledger_d, 0o000)
    try:
        proc = run_merge(root)
    finally:
        os.chmod(ledger_d, 0o700)

    assert proc.returncode == 1, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    payload = parse_stdout(proc)
    assert payload["success"] is False
    assert ledger_path.read_bytes() == before


@requires_non_root
def test_unreadable_fragment_dir_refuses_even_with_no_outgoing_ledger(tmp_path):
    """RED witness for the errno split ALONE. There is no ledger.json here, so
    the many-to-zero guard cannot fire and the refusal can only come from
    _read_fragments(). This is what keeps the two guards independent: the
    split reports the failure at the layer that saw it, whether or not
    anything downstream would have caught the outcome."""
    root = make_durable_root(tmp_path)
    write_fixture_cache_keys(root, {})
    assert not (root / "runs" / "ledger.json").exists()

    ledger_d = root / "runs" / "ledger.d"
    os.chmod(ledger_d, 0o000)
    try:
        proc = run_merge(root)
    finally:
        os.chmod(ledger_d, 0o700)

    assert proc.returncode == 1, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    payload = parse_stdout(proc)
    assert payload["success"] is False
    assert "could not be listed" in payload["error"], payload["error"]
    assert not (root / "runs" / "ledger.json").exists(), (
        "a refusal must not leave a materialized ledger behind"
    )


@requires_non_root
def test_unsearchable_parent_refuses_rather_than_reading_as_absent(tmp_path):
    """Control, and deliberately NOT labelled a witness: measured GREEN against
    the unfixed script too. It covers the is_dir() leg of the swallow -- an
    unsearchable PARENT makes the stat of ledger.d fail with EACCES, so
    `is_dir()` answered False and the old code read that as the documented
    'not written yet'. WHY it was already green is the part worth keeping: the
    same unsearchable runs/ also defeats _atomic_write_json(), so that leg
    could never produce a SILENT empty publish, only a noisier failure one
    step later. Which is why the docstring of _read_fragments() names the glob
    leg, not this one, as the swallow that actually reaches production.
    iterdir() now raises it at the layer that saw it, and this test pins the
    outcome property that holds either way."""
    root = make_durable_root(tmp_path)
    write_fixture_cache_keys(root, {})
    runs_dir = root / "runs"

    os.chmod(runs_dir, 0o000)
    try:
        proc = run_merge(root)
    finally:
        os.chmod(runs_dir, 0o700)

    assert proc.returncode == 1, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    payload = parse_stdout(proc)
    assert payload["success"] is False
    assert not (root / "runs" / "ledger.json").exists(), (
        "no ledger may be materialized when the run directory could not even "
        "be searched"
    )


@requires_non_root
def test_unreadable_outgoing_ledger_refuses_when_the_merge_is_empty(tmp_path):
    """RED witness. The single-fault form of a case a plan review constructed:
    ledger.d is legitimately empty, but the existing ledger.json cannot be
    read, so whether segments are about to be erased is UNKNOWN. Ignoring that
    read failure would leave the identical swallow one file over --
    could-not-look is not nobody-is-there here too."""
    root = make_durable_root(tmp_path)
    write_fixture_cache_keys(root, {})
    ledger_path = write_ledger_json(root, {"seg01": {"status": "converged"}})
    before = ledger_path.read_bytes()

    os.chmod(ledger_path, 0o000)
    try:
        proc = run_merge(root)
    finally:
        os.chmod(ledger_path, 0o600)

    assert proc.returncode == 1, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    payload = parse_stdout(proc)
    assert payload["success"] is False
    assert ledger_path.read_bytes() == before


def test_unparseable_outgoing_ledger_refuses_when_the_merge_is_empty(tmp_path):
    """RED witness. Same principle as the unreadable case: a ledger.json that
    does not parse cannot be shown to be empty, so an empty merge must not
    replace it."""
    root = make_durable_root(tmp_path)
    write_fixture_cache_keys(root, {})
    ledger_path = root / "runs" / "ledger.json"
    ledger_path.write_text("{ this is not json", encoding="utf-8")
    before = ledger_path.read_bytes()

    proc = run_merge(root)

    assert proc.returncode == 1, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    payload = parse_stdout(proc)
    assert payload["success"] is False
    assert ledger_path.read_bytes() == before


def test_a_populated_merge_still_overwrites_a_populated_ledger(tmp_path):
    """Control, green before and after. The guard fires only on the
    many-to-zero transition; an ordinary re-materialization is untouched."""
    root = make_durable_root(tmp_path)
    key_a = make_cache_key("A")
    write_fixture_cache_keys(root, {"seg01": key_a})
    write_fragment(root, "seg01", converged_fragment(key_a))
    write_ledger_json(root, {"seg99": {"status": "pending"}})

    proc = run_merge(root)

    assert proc.returncode == 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    payload = parse_stdout(proc)
    assert payload["success"] is True
    doc = json.loads((root / "runs" / "ledger.json").read_text(encoding="utf-8"))
    assert set(doc["segments"].keys()) == {"seg01"}


@requires_non_root
def test_an_unreadable_outgoing_ledger_never_blocks_a_merge_that_has_fragments(tmp_path):
    """Control, green before and after -- and the reason the outgoing-ledger
    read cannot over-catch. That read happens ONLY when the merge produced
    zero segments, so a corrupt or unreadable ledger.json is irrelevant to a
    merge that has real fragments to publish."""
    root = make_durable_root(tmp_path)
    key_a = make_cache_key("A")
    write_fixture_cache_keys(root, {"seg01": key_a})
    write_fragment(root, "seg01", converged_fragment(key_a))
    ledger_path = write_ledger_json(root, {"seg99": {"status": "pending"}})

    os.chmod(ledger_path, 0o000)
    try:
        proc = run_merge(root)
    finally:
        os.chmod(ledger_path, 0o600)

    assert proc.returncode == 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    assert parse_stdout(proc)["success"] is True


def test_deleting_the_ledger_is_the_escape_hatch_for_a_deliberate_reset(tmp_path):
    """Control. A genuine reset is an operator act and has to say so -- by
    deleting runs/ledger.json. No flag exists for it, deliberately: a flag
    would let the accident through as well."""
    root = make_durable_root(tmp_path)
    write_fixture_cache_keys(root, {})
    ledger_path = write_ledger_json(root, {"seg01": {"status": "converged"}})

    refused = run_merge(root)
    assert refused.returncode == 1, refused.stdout

    ledger_path.unlink()
    proc = run_merge(root)

    assert proc.returncode == 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    payload = parse_stdout(proc)
    assert payload["success"] is True
    assert payload["n_segments"] == 0
    doc = json.loads(ledger_path.read_text(encoding="utf-8"))
    assert doc == {"segments": {}}


def test_an_already_empty_outgoing_ledger_is_republished_not_refused(tmp_path):
    """Control. The guard is many-to-ZERO, not any-to-zero: an outgoing ledger
    that is ALREADY empty has nothing to lose, so a repeat merge of an empty
    project must keep succeeding -- that is the steady state of a project
    scaffolded but not yet translated."""
    root = make_durable_root(tmp_path)
    write_fixture_cache_keys(root, {})
    write_ledger_json(root, {})

    proc = run_merge(root)

    assert proc.returncode == 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    assert parse_stdout(proc)["success"] is True


def test_a_ledger_d_that_is_a_plain_file_still_reads_as_not_written_yet(tmp_path):
    """Control. ENOTDIR stays on the DEFINITIVE side of the split, exactly as
    the old `if not ledger_d.is_dir()` had it -- dropping the explicit S_ISDIR
    check loses nothing, because iterdir() raises NotADirectoryError for it."""
    root = make_durable_root(tmp_path)
    write_fixture_cache_keys(root, {})
    ledger_d = root / "runs" / "ledger.d"
    ledger_d.rmdir()
    ledger_d.write_text("not a directory", encoding="utf-8")

    proc = run_merge(root)

    assert proc.returncode == 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    payload = parse_stdout(proc)
    assert payload["success"] is True
    assert payload["n_segments"] == 0


def test_non_json_names_in_the_fragment_dir_are_ignored_exactly_as_glob_did(tmp_path):
    """Control for the construct swap. The suffix filter has to select what
    glob("*.json") selected: ledger_update.py stages `{seg}.json.tmp.{pid}`
    and publishes `{seg}.json`, so a staged temp file left behind by an
    interrupted write must not be picked up as a fragment."""
    root = make_durable_root(tmp_path)
    key_a = make_cache_key("A")
    write_fixture_cache_keys(root, {"seg01": key_a})
    write_fragment(root, "seg01", converged_fragment(key_a))
    ledger_d = root / "runs" / "ledger.d"
    (ledger_d / "seg02.json.tmp.4242").write_text('{"status": "pending"}', encoding="utf-8")
    (ledger_d / "README").write_text("notes", encoding="utf-8")

    proc = run_merge(root)

    assert proc.returncode == 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    payload = parse_stdout(proc)
    assert payload["n_segments"] == 1
    doc = json.loads((root / "runs" / "ledger.json").read_text(encoding="utf-8"))
    assert set(doc["segments"].keys()) == {"seg01"}


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
