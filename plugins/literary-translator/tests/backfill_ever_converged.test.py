"""tests/backfill_ever_converged.test.py -- tests for
scripts/backfill_ever_converged.py.

See that script's own module docstring for the full spec. This file
exercises exactly what that spec makes it responsible for:

  1. "Ever converged" determination from the MERGED ledger (shelling out to
     the real ledger_merge.py, never a re-implemented merge): a segment
     whose materialized status is `converged` OR `stale` counts -- both mean
     "this fragment's own on-disk status is converged" (ledger_merge.py only
     ever computes `stale` for a fragment that WAS `converged`). A fragment
     that never converged (`in_progress`/`blocked`/`non_converged`) must
     never be picked up.
  2. DRY RUN is the default: writes not one sentinel file, reports the
     correct sorted id lists and counts.
  3. `--apply`: creates exactly the missing sentinels, and running it again
     is idempotent (nothing new, nothing re-created).
  4. An existing sentinel -- even one with unexpected content -- is NEVER
     overwritten or deleted.
  5. `--allow-empty`: without it, zero ever-converged segments is FATAL;
     with it, reported normally.
  6. The sentinel this script writes is byte-identical (filename, content,
     mode) to what ledger_update.py's own `mark_ever_converged()` writes --
     a drift test, not a second source of truth (this project's "no shared
     lib between self-contained scripts" convention; the same technique
     select_segments.test.py already uses for `ever_converged_path()`).
  7. `--durable-root`/`--plugin-root`: the identical independent-root-override
     contract select_segments.py/ledger_merge.py already carry, including
     the --plugin-root tampered-sibling-bypass security property.

Following this plugin's established test convention (`ledger_merge.test.py`'s
`make_durable_root` pattern): every test copies the REAL
`backfill_ever_converged.py` and `ledger_merge.py` plus the REAL
`assets/schemas/*.schema.json` files into an isolated `tmp_path` fixture
root and invokes `python3 {durable_root}/scripts/backfill_ever_converged.py
[flags]` exactly as it is invoked in production. `cache_key.py` is stubbed
with the same small fixture script `ledger_merge.test.py`/
`select_segments.test.py` use.
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
ASSETS_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets"
BACKFILL_SCRIPT_SRC = ASSETS_DIR / "scripts" / "backfill_ever_converged.py"
LEDGER_MERGE_SRC = ASSETS_DIR / "scripts" / "ledger_merge.py"
LEDGER_UPDATE_SRC = ASSETS_DIR / "scripts" / "ledger_update.py"
SCHEMAS_SRC = ASSETS_DIR / "schemas"

assert BACKFILL_SCRIPT_SRC.is_file(), f"backfill_ever_converged.py not found at {BACKFILL_SCRIPT_SRC}"
assert LEDGER_MERGE_SRC.is_file(), f"ledger_merge.py not found at {LEDGER_MERGE_SRC}"
assert LEDGER_UPDATE_SRC.is_file(), f"ledger_update.py not found at {LEDGER_UPDATE_SRC}"
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

# Verbatim copy of the fixture stand-in for cache_key.py that
# select_segments.test.py/ledger_merge.test.py already use: same `--seg <id>`
# -> JSON object stdout interface, sourced from a test-controlled
# test_fixture_cache_keys.json instead of real profile.yml/canon.json/segpack
# machinery.
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
    """Builds an isolated durable_root: copies the REAL
    backfill_ever_converged.py and ledger_merge.py plus the REAL
    assets/schemas/*.schema.json files into {root}/scripts/ and
    {root}/schemas/, installs the fake cache_key.py stub alongside them, and
    creates empty runs/ledger.d/ and segments/ directories."""
    root = tmp_path / name
    scripts_dir = root / "scripts"
    scripts_dir.mkdir(parents=True)
    shutil.copy2(BACKFILL_SCRIPT_SRC, scripts_dir / "backfill_ever_converged.py")
    shutil.copy2(LEDGER_MERGE_SRC, scripts_dir / "ledger_merge.py")
    (scripts_dir / "cache_key.py").write_text(FAKE_CACHE_KEY_PY, encoding="utf-8")

    schemas_dir = root / "schemas"
    shutil.copytree(SCHEMAS_SRC, schemas_dir)

    (root / "runs" / "ledger.d").mkdir(parents=True)
    (root / "segments").mkdir()
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


def make_cache_key(seed):
    return {field: f"{field}-{seed}" for field in CACHE_KEY_FIELDS}


def converged_fragment(cache_key, reviewed_draft_sha1, rounds=1):
    return {
        "timestamp": "2026-01-01T00:00:00Z",
        "status": "converged",
        "rounds": rounds,
        "cache_key": cache_key,
        "n_blocks": 3,
        "n_footnotes": 1,
        "n_verses": 0,
        "reviewed_draft_sha1": reviewed_draft_sha1,
    }


def in_progress_fragment():
    return {"timestamp": "2026-01-01T00:00:00Z", "status": "in_progress"}


def blocked_fragment(reason="review-null"):
    return {"timestamp": "2026-01-01T00:00:00Z", "status": "blocked", "reason": reason}


def non_converged_fragment(reason="cap", rounds=4):
    return {
        "timestamp": "2026-01-01T00:00:00Z",
        "status": "non_converged",
        "reason": reason,
        "rounds": rounds,
    }


def sentinel_path(root, seg):
    return root / "segments" / f".ever_converged.{seg}"


def sentinel_files(root):
    return sorted(p.name for p in (root / "segments").glob(".ever_converged.*"))


def run_backfill(root, *extra_args, timeout=30):
    return subprocess.run(
        [sys.executable, str(root / "scripts" / "backfill_ever_converged.py"), *extra_args],
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=str(root),
    )


def run_backfill_from(script_path, *extra_args, timeout=30):
    return subprocess.run(
        [sys.executable, str(script_path), *extra_args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def parse_stdout(proc):
    assert proc.stdout.strip(), f"expected one JSON line on stdout, got none. stderr:\n{proc.stderr}"
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    assert len(lines) == 1, f"expected exactly one JSON line, got:\n{proc.stdout}"
    return json.loads(lines[0])


# ---------------------------------------------------------------------------
# The mixed-population fixture: two never-sentineled ever-converged segments
# (one plain `converged`, one that has gone `stale` since -- both must count),
# one ever-converged segment that ALREADY has a sentinel (with distinctive
# non-canonical content, to prove non-overwrite, not merely non-recreation),
# and three segments that never converged at all (must never be picked up).
# ---------------------------------------------------------------------------

PRESENTINEL_CONTENT = b"LEGACY-SENTINEL-DO-NOT-TOUCH"


def build_mixed_project(root):
    current_key = make_cache_key("current")
    fixture_keys = {}

    # seg_conv_match: converged, cache key still matches -> stays `converged`
    # in the materialized ledger. No sentinel yet -> missing.
    fixture_keys["seg_conv_match"] = current_key
    write_fragment(root, "seg_conv_match", converged_fragment(dict(current_key), "0" * 40))

    # seg_conv_mismatch: converged, but the cache key has since drifted ->
    # ledger_merge.py flips it to `stale` in the MATERIALIZED view only (the
    # on-disk fragment's own status stays `converged`). Must STILL count as
    # ever-converged. No sentinel yet -> missing.
    fixture_keys["seg_conv_mismatch"] = current_key
    stored_mismatch = dict(current_key)
    stored_mismatch["style_contract_hash"] = "style_contract_hash-OLD"
    write_fragment(root, "seg_conv_mismatch", converged_fragment(stored_mismatch, "1" * 40))

    # seg_conv_presentinel: converged, cache key matches, and ALREADY has a
    # sentinel raised (with non-canonical content, so a test can prove it was
    # never touched, not merely that a file with the right name exists).
    fixture_keys["seg_conv_presentinel"] = current_key
    write_fragment(root, "seg_conv_presentinel", converged_fragment(dict(current_key), "2" * 40))
    sentinel_path(root, "seg_conv_presentinel").write_bytes(PRESENTINEL_CONTENT)

    # Never converged -- must never be picked up.
    write_fragment(root, "seg_in_progress", in_progress_fragment())
    write_fragment(root, "seg_blocked", blocked_fragment())
    write_fragment(root, "seg_non_converged", non_converged_fragment())

    write_fixture_cache_keys(root, fixture_keys)


EVER_CONVERGED = sorted(["seg_conv_match", "seg_conv_mismatch", "seg_conv_presentinel"])
MISSING_BEFORE_APPLY = sorted(["seg_conv_match", "seg_conv_mismatch"])


def setup_mixed_project(tmp_path):
    root = make_durable_root(tmp_path)
    build_mixed_project(root)
    return root


# ---------------------------------------------------------------------------
# 0. Dry run performs ZERO filesystem modifications anywhere under the
# durable root -- NOT merely "no sentinel file". A prior version of this
# script always shelled out to ledger_merge.py, which ATOMICALLY WRITES
# runs/ledger.json as a side effect even when called bare -- so a "dry run"
# was silently mutating the live project directory. Two other sessions can
# be working in the same project tree at the same time; a rewrite of
# runs/ledger.json during their run is exactly the kind of collision this
# plugin's own conventions exist to prevent. The fix: a dry run reads an
# EXISTING materialized runs/ledger.json directly (no subprocess) when one
# is present; only re-materializes via ledger_merge.py at all when
# explicitly authorized (--allow-merge, or implicitly under --apply, where
# the merge is a legitimate part of doing the work).
# ---------------------------------------------------------------------------

def prime_materialized_ledger(root):
    """Materializes runs/ledger.json via the REAL ledger_merge.py, standing
    in for "a project that has already run select_segments.py/
    final_audit.py at least once" -- the common case this script's fast
    read-only path exists for. Deliberately NOT done via
    backfill_ever_converged.py itself, so priming is independent of the
    code under test."""
    proc = subprocess.run(
        [sys.executable, str(root / "scripts" / "ledger_merge.py")],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=str(root),
    )
    assert proc.returncode == 0, f"fixture setup: ledger_merge.py failed: {proc.stderr}"
    return root / "runs" / "ledger.json"


def _snapshot_tree(root):
    """{relative_path: (mtime_ns, size)} for every FILE under root -- used to
    prove a run made literally zero filesystem modifications, not merely
    that no sentinel appeared (the weaker claim that let the original defect
    through review)."""
    snap = {}
    for p in root.rglob("*"):
        if p.is_file():
            st = p.stat()
            snap[str(p.relative_to(root))] = (st.st_mtime_ns, st.st_size)
    return snap


def _tree_diff(before, after):
    added = sorted(set(after) - set(before))
    removed = sorted(set(before) - set(after))
    changed = sorted(k for k in (set(before) & set(after)) if before[k] != after[k])
    return {"added": added, "removed": removed, "changed": changed}


def test_dry_run_with_existing_ledger_makes_zero_filesystem_modifications(tmp_path):
    """The core safety property, with a pre-existing materialized ledger --
    the common case (most real projects already have one)."""
    root = setup_mixed_project(tmp_path)
    prime_materialized_ledger(root)

    before = _snapshot_tree(root)
    proc = run_backfill(root)
    after = _snapshot_tree(root)

    assert proc.returncode == 0, f"stderr={proc.stderr!r}"
    payload = parse_stdout(proc)
    assert payload["ledger_source"] == "existing", (
        "must use the pre-existing ledger.json directly, not re-merge"
    )
    assert payload["ever_converged_segs"] == EVER_CONVERGED

    diff = _tree_diff(before, after)
    assert diff == {"added": [], "removed": [], "changed": []}, (
        f"a dry run must make ZERO filesystem modifications anywhere under "
        f"the durable root when a materialized ledger.json already exists: {diff}"
    )


def test_dry_run_without_existing_ledger_refuses_rather_than_silently_merging(tmp_path):
    root = setup_mixed_project(tmp_path)
    assert not (root / "runs" / "ledger.json").exists(), "fixture precondition"

    before = _snapshot_tree(root)
    proc = run_backfill(root)
    after = _snapshot_tree(root)

    assert proc.returncode != 0, (
        "with no materialized ledger and no explicit opt-in, a dry run must "
        "refuse rather than silently invoke ledger_merge.py (which writes)\n"
        f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
    payload = parse_stdout(proc)
    assert payload["success"] is False
    assert "--allow-merge" in payload["error"]
    assert _tree_diff(before, after) == {"added": [], "removed": [], "changed": []}, (
        "a refusal must not have written anything either"
    )


def test_dry_run_without_existing_ledger_succeeds_with_allow_merge(tmp_path):
    root = setup_mixed_project(tmp_path)

    proc = run_backfill(root, "--allow-merge")

    assert proc.returncode == 0, f"stderr={proc.stderr!r}"
    payload = parse_stdout(proc)
    assert payload["ledger_source"] == "freshly_merged"
    assert payload["ever_converged_segs"] == EVER_CONVERGED
    assert (root / "runs" / "ledger.json").is_file(), (
        "--allow-merge explicitly authorizes exactly this one write"
    )
    assert payload["applied"] is False
    assert payload["created"] == []
    assert sentinel_files(root) == [".ever_converged.seg_conv_presentinel"], (
        "--allow-merge authorizes the ledger write, never a sentinel write"
    )


def test_apply_always_freshly_remerges_even_with_an_existing_ledger(tmp_path):
    """--apply never takes the fast existing-ledger.json path -- it always
    re-materializes immediately before writing any sentinel, so it can never
    act on a stale view."""
    root = setup_mixed_project(tmp_path)
    prime_materialized_ledger(root)

    proc = run_backfill(root, "--apply")

    assert proc.returncode == 0, f"stderr={proc.stderr!r}"
    payload = parse_stdout(proc)
    assert payload["ledger_source"] == "freshly_merged"


def test_dry_run_treats_a_corrupt_existing_ledger_as_missing(tmp_path):
    root = setup_mixed_project(tmp_path)
    (root / "runs").mkdir(exist_ok=True)
    (root / "runs" / "ledger.json").write_text("{not valid json", encoding="utf-8")

    proc = run_backfill(root)
    assert proc.returncode != 0, "a corrupt existing ledger must not be silently trusted"
    payload = parse_stdout(proc)
    assert payload["success"] is False
    assert "--allow-merge" in payload["error"]

    proc2 = run_backfill(root, "--allow-merge")
    assert proc2.returncode == 0, f"stderr={proc2.stderr!r}"
    payload2 = parse_stdout(proc2)
    assert payload2["ledger_source"] == "freshly_merged"
    assert payload2["ever_converged_segs"] == EVER_CONVERGED


# ---------------------------------------------------------------------------
# 1. Dry run (the default): writes NOTHING, reports the correct ids.
# ---------------------------------------------------------------------------

def test_dry_run_writes_nothing_and_reports_correct_ids(tmp_path):
    root = setup_mixed_project(tmp_path)
    prime_materialized_ledger(root)  # the common case: a ledger.json already exists
    before = sentinel_files(root)

    proc = run_backfill(root)

    assert proc.returncode == 0, f"stderr={proc.stderr!r}"
    payload = parse_stdout(proc)
    assert payload["success"] is True
    assert payload["applied"] is False

    assert payload["ever_converged_segs"] == EVER_CONVERGED, (
        "must include the currently-converged AND the gone-stale segment, "
        "and exclude every never-converged one"
    )
    assert payload["already_sentineled"] == ["seg_conv_presentinel"]
    assert payload["missing_sentinels"] == MISSING_BEFORE_APPLY
    assert payload["created"] == []
    assert payload["failed_to_create"] == []
    assert payload["counts"] == {
        "ever_converged": 3,
        "already_sentineled": 1,
        "missing_sentinels": 2,
        # 1.19.1: the third bucket -- a sentinel path that is neither absent
        # nor a regular file. Asserted as an exact dict on purpose: a bucket
        # silently disappearing from this report is how a segment stops being
        # counted anywhere at all.
        "ambiguous_sentinels": 0,
        # 1.20.0: the segments this script did not consider at all. It is not
        # an error bucket -- it is the script declining to imply that
        # `success: true` means every segment that ever converged is now
        # protected. It cannot know that: a segment that converged and was
        # later replaced no longer records the convergence anywhere.
        "not_evaluated": 3,
        "created": 0,
        "failed_to_create": 0,
    }
    # Every non-converged segment appears with the status that excluded it.
    # `seg_in_progress` is precisely the dangerous shape: on a project that
    # converged before sentinels existed, a segment in this state may have
    # converged and been replaced, and nothing distinguishes it from one that
    # never converged. It must be NAMED rather than silently dropped.
    assert payload["not_evaluated"] == [
        {"seg": "seg_blocked", "status": "blocked"},
        {"seg": "seg_in_progress", "status": "in_progress"},
        {"seg": "seg_non_converged", "status": "non_converged"},
    ]

    after = sentinel_files(root)
    assert after == before, "dry run must not create (or touch) any sentinel file"
    assert sentinel_path(root, "seg_conv_presentinel").read_bytes() == PRESENTINEL_CONTENT, (
        "dry run must not touch the pre-existing sentinel's content either"
    )


def test_dry_run_zero_ever_converged_still_writes_nothing(tmp_path):
    """A project with only never-converged fragments: --allow-empty required,
    and still no sentinel is written even once permitted."""
    root = make_durable_root(tmp_path)
    write_fragment(root, "seg_in_progress", in_progress_fragment())
    prime_materialized_ledger(root)

    proc = run_backfill(root, "--allow-empty")

    assert proc.returncode == 0, f"stderr={proc.stderr!r}"
    payload = parse_stdout(proc)
    assert payload["ever_converged_segs"] == []
    assert sentinel_files(root) == []


# ---------------------------------------------------------------------------
# 2 & 3. --apply creates exactly the missing sentinels, and is idempotent.
# ---------------------------------------------------------------------------

def test_apply_creates_exactly_the_missing_sentinels(tmp_path):
    root = setup_mixed_project(tmp_path)

    proc = run_backfill(root, "--apply")

    assert proc.returncode == 0, f"stderr={proc.stderr!r}"
    payload = parse_stdout(proc)
    assert payload["applied"] is True
    assert payload["created"] == MISSING_BEFORE_APPLY
    assert payload["already_sentineled"] == ["seg_conv_presentinel"]
    assert payload["missing_sentinels"] == MISSING_BEFORE_APPLY, (
        "missing_sentinels reports what was missing AT THE START of this "
        "run, regardless of what --apply then did about it"
    )
    assert payload["failed_to_create"] == []

    for seg in MISSING_BEFORE_APPLY:
        p = sentinel_path(root, seg)
        assert p.is_file(), f"sentinel for {seg} was not created"
        assert p.read_bytes() == b"converged\n"

    assert sentinel_files(root) == [
        ".ever_converged.seg_conv_match",
        ".ever_converged.seg_conv_mismatch",
        ".ever_converged.seg_conv_presentinel",
    ]


def test_apply_is_idempotent_on_a_second_run(tmp_path):
    root = setup_mixed_project(tmp_path)
    first = run_backfill(root, "--apply")
    assert first.returncode == 0, f"stderr={first.stderr!r}"

    files_before = sentinel_files(root)

    second = run_backfill(root, "--apply")

    assert second.returncode == 0, f"stderr={second.stderr!r}"
    payload = parse_stdout(second)
    assert payload["ever_converged_segs"] == EVER_CONVERGED
    assert sorted(payload["already_sentineled"]) == EVER_CONVERGED, (
        "everything must now report as already-sentineled"
    )
    assert payload["missing_sentinels"] == []
    assert payload["created"] == [], "nothing new should be (re-)created on a repeat run"
    assert payload["failed_to_create"] == []

    assert sentinel_files(root) == files_before, "no new/removed sentinel files on the repeat run"


# ---------------------------------------------------------------------------
# 4. An existing sentinel is never overwritten or deleted.
# ---------------------------------------------------------------------------

def test_existing_sentinel_is_never_overwritten_or_deleted(tmp_path):
    """CLI-level: run()'s own missing_sentinels pre-filter (computed from a
    classify_ever_converged_sentinel() snapshot BEFORE any write) already keeps an already-sentineled
    segment out of the create loop entirely, so this proves the end-to-end
    report/behavior is correct. It does NOT by itself exercise
    mark_ever_converged()'s own O_EXCL protection, since that pre-filter
    makes the write call unreachable for this segment in a normal run --
    see test_mark_ever_converged_never_overwrites_an_existing_sentinel below
    for the direct, pre-filter-independent proof of that guarantee (the two
    together close the gap; a mutation dropping O_EXCL alone was verified to
    slip past THIS test but not the direct one)."""
    root = setup_mixed_project(tmp_path)

    proc = run_backfill(root, "--apply")
    assert proc.returncode == 0, f"stderr={proc.stderr!r}"
    payload = parse_stdout(proc)

    assert "seg_conv_presentinel" not in payload["created"], (
        "a pre-existing sentinel must never be reported as newly created"
    )
    assert sentinel_path(root, "seg_conv_presentinel").read_bytes() == PRESENTINEL_CONTENT, (
        "the pre-existing sentinel's CONTENT must be untouched"
    )
    assert sentinel_path(root, "seg_conv_presentinel").is_file(), "must not be deleted either"


def test_mark_ever_converged_never_overwrites_an_existing_sentinel(tmp_path):
    """Direct, pre-filter-independent proof of the O_EXCL guarantee itself:
    calls backfill_ever_converged.py's own mark_ever_converged() against a
    segments_dir that ALREADY holds a sentinel with distinctive non-canonical
    content, bypassing run()'s missing_sentinels pre-filter entirely (which
    would otherwise make this exact call unreachable through the CLI --
    e.g. a race where a sentinel is raised by something else between this
    script's already_sentineled snapshot and its create loop). Confirmed by
    mutation: dropping O_EXCL from the real implementation makes this test
    fail while leaving test_existing_sentinel_is_never_overwritten_or_deleted
    above green."""
    backfill = _load_module(BACKFILL_SCRIPT_SRC, "backfill_ever_converged_direct_excl_check")

    segments_dir = tmp_path / "segments"
    segments_dir.mkdir()
    seg = "segRace"
    presentinel = segments_dir / f".ever_converged.{seg}"
    presentinel.write_bytes(PRESENTINEL_CONTENT)

    outcome = backfill.mark_ever_converged(seg, segments_dir)

    assert outcome == "already_present"
    assert presentinel.read_bytes() == PRESENTINEL_CONTENT, (
        "must not overwrite an existing sentinel even when this exact "
        "function is called directly against a segment already sentineled"
    )


# ---------------------------------------------------------------------------
# 5. --allow-empty: zero ever-converged segments is FATAL without it.
# ---------------------------------------------------------------------------

def test_zero_ever_converged_is_fatal_without_allow_empty(tmp_path):
    root = make_durable_root(tmp_path)
    # runs/ledger.d/ is empty (no fragments at all) -- the emptiest possible
    # project: not even a not_started/blocked entry.
    prime_materialized_ledger(root)  # isolate this test to the --allow-empty check

    proc = run_backfill(root)

    assert proc.returncode != 0, (
        f"a ledger with zero ever-converged segments must refuse by default\n"
        f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
    payload = parse_stdout(proc)
    assert payload["success"] is False
    assert "--allow-empty" in payload["error"]
    assert "ZERO" in payload["error"]


def test_zero_ever_converged_succeeds_with_allow_empty(tmp_path):
    root = make_durable_root(tmp_path)
    prime_materialized_ledger(root)  # isolate this test to the --allow-empty check

    proc = run_backfill(root, "--allow-empty")

    assert proc.returncode == 0, f"stderr={proc.stderr!r}"
    payload = parse_stdout(proc)
    assert payload["success"] is True
    assert payload["ever_converged_segs"] == []
    assert payload["counts"]["ever_converged"] == 0


def test_zero_ever_converged_with_apply_and_allow_empty_creates_nothing(tmp_path):
    root = make_durable_root(tmp_path)

    proc = run_backfill(root, "--allow-empty", "--apply")

    assert proc.returncode == 0, f"stderr={proc.stderr!r}"
    payload = parse_stdout(proc)
    assert payload["created"] == []
    assert sentinel_files(root) == []


def test_non_converged_statuses_are_never_picked_up(tmp_path):
    """A project with only in_progress/blocked/non_converged fragments (no
    plain not_started even needed -- absence of a fragment is not this
    script's concern at all, unlike select_segments.py's candidate list)
    must report zero ever-converged segments."""
    root = make_durable_root(tmp_path)
    write_fragment(root, "seg_in_progress", in_progress_fragment())
    write_fragment(root, "seg_blocked", blocked_fragment())
    write_fragment(root, "seg_non_converged", non_converged_fragment())
    prime_materialized_ledger(root)

    proc = run_backfill(root, "--allow-empty")

    assert proc.returncode == 0, f"stderr={proc.stderr!r}"
    payload = parse_stdout(proc)
    assert payload["ever_converged_segs"] == []


# ---------------------------------------------------------------------------
# 6. Byte-identity against ledger_update.py's own mark_ever_converged() --
# the drift test. Verbatim technique from select_segments.test.py's own
# test_sentinel_filename_matches_the_writer_in_ledger_update, extended to
# also compare CONTENT and MODE (not just the filename convention).
# ---------------------------------------------------------------------------

def _load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, str(path))
    assert spec is not None and spec.loader is not None, f"cannot load {path}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_sentinel_write_is_byte_identical_to_ledger_update_writer(tmp_path):
    writer = _load_module(LEDGER_UPDATE_SRC, "ledger_update_real")
    backfill = _load_module(BACKFILL_SCRIPT_SRC, "backfill_ever_converged_real")

    seg = "segX"
    dir_a = tmp_path / "writer_side"
    dir_a.mkdir()
    dir_b = tmp_path / "backfill_side"
    dir_b.mkdir()

    ok = writer.mark_ever_converged(seg, dir_a)
    assert ok is True, "precondition: the real writer must succeed"
    outcome = backfill.mark_ever_converged(seg, dir_b)
    assert outcome == "created", "precondition: the backfill writer must succeed"

    path_a = writer.ever_converged_path(seg, dir_a)
    path_b = backfill.ever_converged_path(seg, dir_b)
    assert path_a.name == path_b.name, (
        "ledger_update.py's own filename convention and backfill_ever_converged.py's "
        "have drifted -- a sentinel this script writes would never be found "
        "by the real reader/writer"
    )

    assert path_a.read_bytes() == path_b.read_bytes(), (
        "sentinel CONTENT has drifted between ledger_update.py's own writer "
        "and backfill_ever_converged.py's"
    )
    mode_a = path_a.stat().st_mode & 0o777
    mode_b = path_b.stat().st_mode & 0o777
    assert mode_a == mode_b, (
        f"sentinel MODE has drifted: ledger_update.py wrote {oct(mode_a)}, "
        f"backfill_ever_converged.py wrote {oct(mode_b)}"
    )

    # And: never overwritten by either writer, on a second call.
    assert writer.mark_ever_converged(seg, dir_a) is True
    assert backfill.mark_ever_converged(seg, dir_b) == "already_present"
    assert path_a.read_bytes() == path_b.read_bytes()


def test_both_writers_refuse_a_non_regular_entry_at_the_sentinel_path(tmp_path):
    """1.19.1: extends the drift pin above to the FileExistsError branch,
    which is where the two writers could silently disagree while everything
    tested above stayed green -- both write identical bytes on the happy
    path, and that is all the byte-identity test exercises.

    `os.open(O_CREAT|O_EXCL)` raises EEXIST for ANY existing entry, so a
    dangling symlink and a directory both reach it. Neither is a sentinel
    either writer wrote, and reporting them as marked/already_present claims
    a protection nothing verified.

    The two copies' outcome SHAPES differ on purpose (bool vs string, see
    backfill's own mark_ever_converged docstring), so this pins the shared
    decision -- refuse or accept -- rather than an identical return value.

    Fails on the unfixed code at the first `is False` / `startswith("error:")`
    assertion of each pair: pre-fix both writers report success."""
    writer = _load_module(LEDGER_UPDATE_SRC, "ledger_update_nonregular")
    backfill = _load_module(BACKFILL_SCRIPT_SRC, "backfill_nonregular")

    dir_a = tmp_path / "writer_side"
    dir_a.mkdir()
    dir_b = tmp_path / "backfill_side"
    dir_b.mkdir()

    for seg, make_entry in (
        ("segLink", lambda p: p.symlink_to(p.parent / "no-such-target")),
        ("segDir", lambda p: p.mkdir()),
    ):
        make_entry(writer.ever_converged_path(seg, dir_a))
        make_entry(backfill.ever_converged_path(seg, dir_b))

        assert writer.mark_ever_converged(seg, dir_a) is False, (
            f"{seg}: ledger_update.py accepted a non-regular entry as proof "
            f"of prior marking"
        )
        outcome = backfill.mark_ever_converged(seg, dir_b)
        assert outcome.startswith("error:"), (
            f"{seg}: backfill_ever_converged.py reported {outcome!r} for a "
            f"non-regular entry -- 'already_present' means 'protected, "
            f"nothing to do', which is false here"
        )

    # FALSE-POSITIVE BOUND, in the same test because it is the same branch:
    # a genuine regular sentinel must still be accepted idempotently by both.
    for seg in ("segOK",):
        writer.ever_converged_path(seg, dir_a).write_bytes(b"converged\n")
        backfill.ever_converged_path(seg, dir_b).write_bytes(b"converged\n")
        assert writer.mark_ever_converged(seg, dir_a) is True
        assert backfill.mark_ever_converged(seg, dir_b) == "already_present"


def test_a_dangling_symlink_is_bucketed_ambiguous_never_missing(tmp_path):
    """CLI level. Pre-fix, `.exists()` followed the dangling link and put the
    segment in `missing_sentinels`; --apply then called the writer, whose
    O_CREAT|O_EXCL got EEXIST from that same link and returned
    "already_present", so the run exited 0 having protected nothing and said
    nothing. A repair tool reporting a repair it did not make is the worst
    shape available here.

    Fails on the unfixed code at `payload["ambiguous_sentinels"]` (KeyError:
    the bucket does not exist) and, if that key is stubbed in, at the
    `not in payload["missing_sentinels"]` assertion."""
    root = setup_mixed_project(tmp_path)
    seg = EVER_CONVERGED[0]
    link = sentinel_path(root, seg)
    link.symlink_to(root / "segments" / "no-such-target")

    proc = run_backfill(root, "--apply")

    assert proc.returncode == 0, f"stderr={proc.stderr!r}"
    payload = parse_stdout(proc)
    assert payload["ambiguous_sentinels"] == [
        {"seg": seg, "detail": "the entry is a symbolic link, not a regular file"}
    ], payload
    assert seg not in payload["missing_sentinels"], (
        "the entry is NOT missing -- calling it missing is what let the "
        "backfill report a sentinel it never wrote"
    )
    assert seg not in payload["already_sentineled"], (
        "and it is not protected either; those are the only two buckets that "
        "existed before, and it belongs to neither"
    )
    assert seg not in payload["created"]
    assert link.is_symlink(), (
        "the entry must survive untouched -- this script never replaces an "
        "entry it did not write, and the operator needs it to diagnose"
    )
    assert "AMBIGUOUS" in proc.stderr and seg in proc.stderr, proc.stderr


def test_a_directory_at_the_sentinel_path_is_not_counted_as_protected(tmp_path):
    """The other half, and the one that fails LOUDEST pre-fix: `.exists()` is
    True for a directory, so the pre-fix scan reported the segment as
    `already_sentineled` -- a repair tool asserting protection that is not
    there, which is exactly the state an operator runs this script to rule
    out.

    Fails on the unfixed code at `seg not in payload["already_sentineled"]`."""
    root = setup_mixed_project(tmp_path)
    seg = EVER_CONVERGED[0]
    sentinel_path(root, seg).mkdir()

    proc = run_backfill(root, "--apply")

    assert proc.returncode == 0, f"stderr={proc.stderr!r}"
    payload = parse_stdout(proc)
    assert seg not in payload["already_sentineled"], (
        "a directory is not a sentinel; counting it as one claims a "
        "protection that does not exist"
    )
    assert [e["seg"] for e in payload["ambiguous_sentinels"]] == [seg], payload
    assert payload["counts"]["ambiguous_sentinels"] == 1
    assert sentinel_path(root, seg).is_dir(), "left untouched for the operator"


def test_a_healthy_project_reports_no_ambiguous_sentinels(tmp_path):
    """FALSE-POSITIVE BOUND for the two tests above: the new bucket must stay
    empty on every normal run, and the two original buckets must still add up.
    A predicate that over-blocks would strand a real backfill.

    Green before and after the fix by design (modulo the new key)."""
    root = setup_mixed_project(tmp_path)

    proc = run_backfill(root, "--apply")

    assert proc.returncode == 0, f"stderr={proc.stderr!r}"
    payload = parse_stdout(proc)
    assert payload["ambiguous_sentinels"] == []
    assert payload["counts"]["ambiguous_sentinels"] == 0
    assert sorted(payload["already_sentineled"] + payload["missing_sentinels"]) == EVER_CONVERGED, (
        "with nothing ambiguous, the two original buckets must still "
        "partition the ever-converged set exactly as before"
    )
    assert "AMBIGUOUS" not in proc.stderr


# ---------------------------------------------------------------------------
# 6b. The OSError-escape defect the review bot found: os.write()/os.close()
# had no `except OSError` at all, so a write or close failure propagated as
# an uncaught exception instead of this function's documented "created" /
# "already_present" / "error: ..." string outcome. Adapted from
# ledger_update.py's own sibling fix (commit 5416d60) for THIS function's
# different contract -- a returned string, not a bool plus a stderr print --
# so these assert on the return value alone, not on captured stderr text.
# ---------------------------------------------------------------------------

def test_write_failure_after_sentinel_create_is_reported_as_a_clean_error_string(tmp_path):
    """mark_ever_converged()'s O_CREAT|O_EXCL open() publishes the sentinel's
    NAME in segments/ before the single os.write() that fills it in ever
    runs. Pre-fix, an OSError from that write was OUTSIDE the try/except
    producing this function's documented string-outcome contract -- it
    propagated as an uncaught exception instead, on exactly the failure that
    contract exists to cover. The caller (run(), under --apply) has no
    handling for an exception here; only for the three documented outcomes.

    Genuinely only reachable in-process, matching ledger_update.test.py's
    own reasoning for its sibling test: there is no portable, reliable way
    to make a real os.write() to a freshly os.open()'d fd fail on a normal
    filesystem without root/fuse/quota machinery, so a direct in-process
    call plus a narrow os.write() patch is the faithful way to reach this
    seam."""
    backfill = _load_module(BACKFILL_SCRIPT_SRC, "backfill_under_test_write_failure")
    segments_dir = tmp_path / "segments"
    segments_dir.mkdir()
    seg = "segWriteFail"

    real_write = backfill.os.write

    def failing_write(fd, data):
        raise OSError(28, "No space left on device")  # ENOSPC

    backfill.os.write = failing_write
    try:
        outcome = backfill.mark_ever_converged(seg, segments_dir)
    finally:
        backfill.os.write = real_write

    assert outcome not in ("created", "already_present"), (
        "a write-time OSError must not be reported as a success"
    )
    assert outcome.startswith("error: "), (
        f"a write-time OSError must produce the SAME 'error: ...' string an "
        f"open-time OSError already does -- not an uncaught exception "
        f"propagating past this function's own documented contract; got {outcome!r}"
    )
    assert "No space left on device" in outcome

    # The write failure must not also leak the file descriptor: the fd is
    # closed as a best-effort cleanup before the error string is returned.
    path = backfill.ever_converged_path(seg, segments_dir)
    assert path.exists(), (
        "O_CREAT|O_EXCL already published the sentinel's name before the "
        "write failed -- that is documented as harmless (every consumer "
        "only calls .exists()), so the name must still be there"
    )


def test_close_failure_after_successful_write_is_reported_as_a_clean_error_string(tmp_path):
    """Sibling of the write-failure test above, for the OTHER OS call this
    function makes after a successful write: os.close(). Some filesystems
    (notably NFS) defer reporting a write error until close() specifically,
    so this is not a redundant echo of the write-failure case -- it is the
    one place a write can appear to have succeeded and still turn out to
    have failed."""
    backfill = _load_module(BACKFILL_SCRIPT_SRC, "backfill_under_test_close_failure")
    segments_dir = tmp_path / "segments"
    segments_dir.mkdir()
    seg = "segCloseFail"

    real_close = backfill.os.close

    def failing_close(fd):
        raise OSError(5, "Input/output error")  # EIO, as e.g. NFS may defer

    backfill.os.close = failing_close
    try:
        outcome = backfill.mark_ever_converged(seg, segments_dir)
    finally:
        backfill.os.close = real_close

    assert outcome not in ("created", "already_present"), (
        "a close-time OSError must not be reported as a success"
    )
    assert outcome.startswith("error: "), (
        f"a close-time OSError must produce the SAME 'error: ...' string an "
        f"open- or write-time OSError already does -- not an uncaught "
        f"exception; got {outcome!r}"
    )
    assert "Input/output error" in outcome


# ---------------------------------------------------------------------------
# 7. --durable-root / --plugin-root: the identical independent-root-override
# contract select_segments.py/ledger_merge.py already carry. Adapted
# verbatim from select_segments.test.py's own battery of six tests for the
# same contract.
# ---------------------------------------------------------------------------

_TAMPERED_LEDGER_MERGE_SRC = (
    "#!/usr/bin/env python3\n"
    "import sys\n"
    "sys.stderr.write('TAMPERED_LEDGER_MERGE_MUST_NEVER_RUN')\n"
    "sys.exit(97)\n"
)


def poison_durable_root_ledger_merge(root):
    (root / "scripts" / "ledger_merge.py").write_text(
        _TAMPERED_LEDGER_MERGE_SRC, encoding="utf-8"
    )


def make_trusted_plugin_root(tmp_path, name="trusted_plugin_install"):
    plugin_root = tmp_path / name
    plugin_scripts_dir = plugin_root / "assets" / "scripts"
    plugin_scripts_dir.mkdir(parents=True)
    shutil.copy2(LEDGER_MERGE_SRC, plugin_scripts_dir / "ledger_merge.py")
    (plugin_scripts_dir / "cache_key.py").write_text(FAKE_CACHE_KEY_PY, encoding="utf-8")
    return plugin_root


def test_durable_root_flag_absent_orphan_copy_fails_self_anchored(tmp_path):
    """Negative control: an orphan copy invoked WITHOUT --durable-root has
    no ledger_merge.py sibling to find -- cannot succeed via self-anchoring."""
    orphan_dir = tmp_path / "orphan_location" / "scripts"
    orphan_dir.mkdir(parents=True)
    orphan_script = orphan_dir / "backfill_ever_converged.py"
    shutil.copy2(BACKFILL_SCRIPT_SRC, orphan_script)

    proc = run_backfill_from(orphan_script, "--allow-empty")

    assert proc.returncode == 1
    payload = parse_stdout(proc)
    assert payload["success"] is False
    assert "ledger_merge.py" in payload["error"]


def test_durable_root_flag_omitted_preserves_todays_behavior(tmp_path):
    root = setup_mixed_project(tmp_path)
    prime_materialized_ledger(root)

    proc = run_backfill(root)

    assert proc.returncode == 0
    payload = parse_stdout(proc)
    assert payload["success"] is True
    assert payload["ever_converged_segs"] == EVER_CONVERGED


def test_plugin_root_flag_bypasses_a_tampered_durable_root_sibling(tmp_path):
    """The core security property: this script runs from its own in-place
    durable-root copy whose SIBLING ledger_merge.py has been POISONED.
    --plugin-root pointing at a separate, untampered location must make it
    use THAT ledger_merge.py instead. --allow-merge (rather than priming a
    ledger.json first) is deliberate: this test must force a REAL merge
    subprocess call, or it would never touch either ledger_merge.py copy at
    all and prove nothing about which one ran."""
    root = setup_mixed_project(tmp_path)
    poison_durable_root_ledger_merge(root)
    plugin_root = make_trusted_plugin_root(tmp_path)

    proc = run_backfill(root, "--plugin-root", str(plugin_root), "--allow-merge")

    assert proc.returncode == 0, (
        f"--plugin-root pointing at the REAL ledger_merge.py must succeed "
        f"even though durable_root's own copy is poisoned:\n"
        f"rc={proc.returncode}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    assert "TAMPERED" not in proc.stdout and "TAMPERED" not in proc.stderr
    payload = parse_stdout(proc)
    assert payload["success"] is True
    assert payload["ever_converged_segs"] == EVER_CONVERGED


def test_plugin_root_flag_absent_uses_the_poisoned_durable_root_sibling(tmp_path):
    """Negative control, and backward-compat proof in one: the SAME
    poisoned durable-root ledger_merge.py, invoked WITHOUT --plugin-root,
    genuinely runs and fails -- proving the positive test's success above is
    attributable to --plugin-root specifically. --allow-merge forces the
    same real merge attempt the positive test makes, so this is a true
    negative control on the identical code path."""
    root = setup_mixed_project(tmp_path)
    poison_durable_root_ledger_merge(root)

    proc = run_backfill(root, "--allow-merge")  # no --plugin-root

    assert proc.returncode == 1
    payload = parse_stdout(proc)
    assert payload["success"] is False
    assert "TAMPERED_LEDGER_MERGE_MUST_NEVER_RUN" in payload["error"]


def test_durable_root_and_plugin_root_are_independently_resolved(tmp_path):
    """--durable-root points at a DATA-only fixture with NO scripts/
    directory at all; --plugin-root points at a SEPARATE, scripts-only
    fixture with no data of its own. Success proves the two concerns are
    genuinely resolved independently, never conflated into one root."""
    data_root = tmp_path / "data_only"
    data_root.mkdir()
    (data_root / "runs" / "ledger.d").mkdir(parents=True)
    (data_root / "segments").mkdir()
    shutil.copytree(SCHEMAS_SRC, data_root / "schemas")
    assert not (data_root / "scripts").exists(), "fixture bug: data_root must have NO scripts/ dir"

    current_key = make_cache_key("current")
    write_fixture_cache_keys(data_root, {"segOnly": current_key})
    write_fragment(data_root, "segOnly", converged_fragment(dict(current_key), "0" * 40))

    plugin_root = make_trusted_plugin_root(tmp_path, name="plugin_only")

    orphan_dir = tmp_path / "orphan_location" / "scripts"
    orphan_dir.mkdir(parents=True)
    orphan_script = orphan_dir / "backfill_ever_converged.py"
    shutil.copy2(BACKFILL_SCRIPT_SRC, orphan_script)

    proc = run_backfill_from(
        orphan_script,
        "--durable-root", str(data_root),
        "--plugin-root", str(plugin_root),
        "--allow-merge",  # data_root has no pre-existing ledger.json to read
    )

    assert proc.returncode == 0, (
        f"durable-root (data) and plugin-root (siblings) must resolve "
        f"independently -- got rc={proc.returncode}\nstdout:\n{proc.stdout}\n"
        f"stderr:\n{proc.stderr}"
    )
    payload = parse_stdout(proc)
    assert payload["success"] is True
    assert payload["ever_converged_segs"] == ["segOnly"]
    assert payload["durable_root"] == str(data_root)
    # ledger_merge.py's own materialized ledger.json must land under the
    # DATA root, never under plugin_root.
    assert (data_root / "runs" / "ledger.json").is_file()
    assert not (plugin_root / "runs").exists()
    # And the sentinel itself must land under the DATA root's segments/.
    assert (data_root / "segments" / ".ever_converged.segOnly").exists() is False, (
        "dry run by default -- still writes nothing even with both roots given"
    )


def test_plugin_root_flag_omitted_preserves_todays_behavior(tmp_path):
    root = setup_mixed_project(tmp_path)
    prime_materialized_ledger(root)

    proc = run_backfill(root, "--durable-root", str(root))

    assert proc.returncode == 0
    payload = parse_stdout(proc)
    assert payload["success"] is True
    assert payload["ever_converged_segs"] == EVER_CONVERGED


# ---------------------------------------------------------------------------
# 8. Doubled-path fix. run_ledger_merge() runs the sibling ledger_merge.py
# subprocess with `cwd` set to the ALREADY-RESOLVED durable_root, but used
# to forward the RAW (possibly relative) --durable-root string as that
# sibling's own --durable-root -- which the sibling's own resolve_dirs()
# resolves a SECOND time against its cwd (the already-resolved value). The
# identical shape was independently confirmed (and fixed) in
# resume_setup.py, segment_dispatch_driver.py, and select_segments.py; this
# script had its own copy of `_root_forward_args()` (a distinct local
# function, not an import), which is why the select_segments.py fix did not
# reach it. Every test above passes an absolute path for both flags, so
# none of them would have caught this.
# ---------------------------------------------------------------------------


def test_relative_durable_root_is_not_doubled_end_to_end(tmp_path):
    """PROOF, end to end against the REAL ledger_merge.py: invoked with a
    genuinely RELATIVE --durable-root, from a cwd that is its own PARENT
    directory, forced through --apply (never the fast dry-run path that
    reads an existing runs/ledger.json directly with NO subprocess at all
    -- see resolve_ledger_segments()). Pre-fix, the raw 'durable_root'
    string was forwarded to ledger_merge.py, whose subprocess cwd is
    already {tmp_path}/durable_root -- so its own
    Path('durable_root').resolve() landed on
    {tmp_path}/durable_root/durable_root, which has no schemas/manifest,
    and ledger_merge.py failed outright (confirmed against this exact
    fixture at the parent commit)."""
    root = make_durable_root(tmp_path)

    proc = subprocess.run(
        [
            sys.executable,
            str(root / "scripts" / "backfill_ever_converged.py"),
            "--durable-root",
            "durable_root",
            "--apply",
            "--allow-empty",
        ],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=str(tmp_path),
    )

    assert proc.returncode == 0, (
        f"a relative --durable-root must resolve to the SAME tree as the "
        f"equivalent absolute one -- got rc={proc.returncode}\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    payload = parse_stdout(proc)
    assert payload["success"] is True
    assert payload["durable_root"] == str(root.resolve())
    assert payload["ledger_source"] == "freshly_merged", (
        "this test must force the real subprocess path, or it proves nothing"
    )
    assert (root / "runs" / "ledger.json").is_file(), (
        "ledger_merge.py must have materialized the ledger in the SAME "
        "tree backfill_ever_converged.py itself resolved to, not one level "
        "deeper"
    )


def test_root_forward_args_never_forwards_a_relative_durable_root(tmp_path, monkeypatch):
    """Unit-level companion, pinning _root_forward_args() directly: it must
    forward the RESOLVED durable_root, never the raw (possibly relative)
    CLI string."""
    module = _load_module(BACKFILL_SCRIPT_SRC, "backfill_ever_converged_root_forward_test")
    monkeypatch.chdir(tmp_path)
    dirs = module.resolve_dirs("some/relative/root", None)

    args = module._root_forward_args(dirs, "some/relative/root", None)

    expected = str((tmp_path / "some" / "relative" / "root").resolve())
    assert args == ["--durable-root", expected], (
        f"the forwarded value must equal the RESOLVED root exactly once, not "
        f"the raw relative string (which the sibling would resolve a SECOND "
        f"time against its own already-resolved cwd). got {args!r}, expected "
        f"['--durable-root', {expected!r}]"
    )


def test_root_forward_args_never_forwards_a_relative_plugin_root(tmp_path, monkeypatch):
    """The --plugin-root half of the same fix: a relative override must be
    resolved against THIS script's own cwd (the same base resolve_dirs()
    already used for its own sibling lookup) BEFORE forwarding -- never
    passed through raw for the child to resolve against ITS OWN, different
    cwd."""
    module = _load_module(BACKFILL_SCRIPT_SRC, "backfill_ever_converged_root_forward_test2")
    (tmp_path / "plugin_dir" / "assets" / "scripts").mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    dirs = module.resolve_dirs(None, "plugin_dir")

    args = module._root_forward_args(dirs, None, "plugin_dir")

    assert args[0:2] == ["--durable-root", str(dirs["durable_root"])]
    expected_plugin_root = str((tmp_path / "plugin_dir").resolve())
    assert args[2:4] == ["--plugin-root", expected_plugin_root]
    assert "plugin_dir" != args[3], "must be resolved, not the raw fragment"


# ---------------------------------------------------------------------------
# A run that protected NOTHING must not report success.
#
# This script's entire operational role is to be run before a W5 dispatch so
# an operator can conclude the #409 protection is up; SKILL.md's upgrade note
# tells them to run it and check the result. It used to hardcode
# `"success": True` and `return 0` regardless of `failed_to_create`, surfacing
# per-segment failures only in a stderr warning and a JSON array nobody is
# obliged to read. So the run where every create failed was indistinguishable
# by exit code from the run where every create succeeded -- and the operator
# dispatches over converged work believing it is protected.
#
# Reaching `failed_to_create` deterministically also required fixing the open
# path: only FileExistsError was caught, so a plain EACCES escaped to the
# top-level handler and aborted the whole run instead of being reported for
# that segment.
# ---------------------------------------------------------------------------

@pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses directory permissions")
def test_failed_creation_reports_failure_in_both_json_and_exit_code(tmp_path):
    root = setup_mixed_project(tmp_path)
    prime_materialized_ledger(root)

    segments_dir = root / "segments"
    original_mode = segments_dir.stat().st_mode
    segments_dir.chmod(0o555)  # readable/traversable, not writable
    try:
        proc = run_backfill(root, "--apply")
    finally:
        segments_dir.chmod(original_mode)

    payload = parse_stdout(proc)

    # The run completed and reported per segment -- it did not abort at the
    # first failure, which is what makes the report trustworthy at all.
    assert payload["created"] == []
    assert [entry["seg"] for entry in payload["failed_to_create"]] == MISSING_BEFORE_APPLY
    assert payload["counts"]["failed_to_create"] == len(MISSING_BEFORE_APPLY)

    # ...and both channels say so. The exit code is the one an operator's
    # `&&` actually reads.
    assert payload["success"] is False, (
        "a run that created no sentinel it set out to create must not report "
        "success -- that is the reading which authorizes the dispatch"
    )
    assert proc.returncode != 0, (
        f"exit code must track success; got {proc.returncode} with "
        f"failed_to_create={payload['failed_to_create']!r}"
    )

    # The sentinel genuinely is not there. Without this the test would pass
    # against a script that reported failure but had written the file anyway.
    for seg in MISSING_BEFORE_APPLY:
        assert not sentinel_path(root, seg).exists()


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
