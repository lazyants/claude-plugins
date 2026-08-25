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
import errno
import fcntl
import importlib.util
import io
import json
import contextlib
import os
import shutil
import stat
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

@contextlib.contextmanager
def _segments_dir_fd(segments_dir):
    """mark_ever_converged() takes the segments-directory DESCRIPTOR, not just
    its path, so every link and unlink lands in the directory this run opened
    even if the pathname is retargeted mid-run. The in-process tests below
    have to supply one; run() opens exactly one for the whole apply pass."""
    fd = os.open(str(segments_dir), os.O_RDONLY)
    try:
        yield fd
    finally:
        try:
            os.close(fd)
        except OSError:
            # Best-effort, mirroring run()'s own close. Tests that inject a
            # failing os.close() patch the shared `os` module, so an
            # unprotected close here would raise from the HARNESS and be
            # indistinguishable from the script failing to handle it.
            pass


def call_mark(backfill, seg, segments_dir):
    """mark_ever_converged() under a freshly-opened descriptor."""
    with _segments_dir_fd(segments_dir) as fd:
        return backfill.mark_ever_converged(seg, segments_dir, fd)


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
    # json_stdout.py (#369): every staged script above loads it by exact
    # path from beside itself, so a root without it exits rather than runs.
    shutil.copy2(LEDGER_MERGE_SRC.parent / "json_stdout.py", scripts_dir / "json_stdout.py")
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

# #443. The marker's body carries the writer's own provenance, so "the marker
# this run left is the real thing and not the empty file a torn write would
# have left" is now checkable directly instead of by comparing against ten
# fixed bytes. Parsing it here rather than pinning a byte string on purpose:
# a test that pinned the exact JSON would have to be re-edited for every field
# ever added, and would pin field ORDER, which is not part of the contract.
def assert_marker_written_by(path, writer, seg):
    """The body at `path` is a complete marker published by `writer` for `seg`."""
    raw = path.read_bytes()
    assert raw.endswith(b"\n"), (
        f"a complete marker body ends in a newline; a torn write is exactly "
        f"what this checks for -- got {raw!r}"
    )
    body = json.loads(raw.decode("utf-8"))
    assert body["marker"] == "ever_converged", body
    assert body["v"] == 1, body
    assert body["by"] == writer, (
        f"the marker at {path} claims to have been written by {body['by']!r}, "
        f"not {writer!r} -- #443's whole point is that these are now distinct"
    )
    assert body["seg"] == seg, body
    return body


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
        assert_marker_written_by(p, "backfill_ever_converged", seg)

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

    outcome = call_mark(backfill, seg, segments_dir)

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


@pytest.mark.parametrize("umask_value", [0o022, 0o077], ids=["umask-022", "umask-077"])
def test_sentinel_write_is_byte_identical_to_ledger_update_writer(tmp_path, umask_value):
    """Parametrized over the umask because the two writers reach the mode by
    DIFFERENT syscalls: ledger_update.py's `os.open(..., 0o644)` is masked by
    the kernel, while this script sets the mode on an already-created 0o600
    staging file. A bare fchmod(0o644) therefore agrees with the sibling at
    the default 022 and diverges at 077 -- 0o644 against 0o600 -- so the
    identity this test asserts held only for the umask the suite happened to
    run under."""
    old_umask = os.umask(umask_value)
    try:
        _assert_writers_agree(tmp_path)
    finally:
        os.umask(old_umask)


def _assert_writers_agree(tmp_path):
    writer = _load_module(LEDGER_UPDATE_SRC, "ledger_update_real")
    backfill = _load_module(BACKFILL_SCRIPT_SRC, "backfill_ever_converged_real")

    seg = "segX"
    dir_a = tmp_path / "writer_side"
    dir_a.mkdir()
    dir_b = tmp_path / "backfill_side"
    dir_b.mkdir()

    ok = writer.mark_ever_converged(seg, dir_a)
    assert ok is True, "precondition: the real writer must succeed"
    outcome = call_mark(backfill, seg, dir_b)
    assert outcome == "created", "precondition: the backfill writer must succeed"

    path_a = writer.ever_converged_path(seg, dir_a)
    path_b = backfill.ever_converged_path(seg, dir_b)
    assert path_a.name == path_b.name, (
        "ledger_update.py's own filename convention and backfill_ever_converged.py's "
        "have drifted -- a sentinel this script writes would never be found "
        "by the real reader/writer"
    )

    # #443 INVERTED THIS ASSERTION, and the inversion is the release. It used
    # to require the two writers' bytes to be EQUAL; that equality WAS the
    # defect. Both published `b"converged\n"`, so a marker retrofitted from a
    # ledger row and a marker earned at a real convergence were the same ten
    # bytes, and the only thing that separated them on the project that
    # surfaced the issue was sentinel mtime at microsecond resolution.
    #
    # What must still agree is the FORMAT -- one line of parseable marker JSON
    # from the shared sentinel_body(), so one reader rule covers both writers.
    # What must now DIFFER is `by`. A future edit that makes either writer
    # anonymous, or makes both claim the same name, reintroduces #443 and is
    # caught here.
    body_a = assert_marker_written_by(path_a, "ledger_update", seg)
    body_b = assert_marker_written_by(path_b, "backfill_ever_converged", seg)
    assert body_a["by"] != body_b["by"], (
        "the two writers are indistinguishable on disk again -- that is #443"
    )
    assert path_a.read_bytes() != path_b.read_bytes(), (
        "the bodies are byte-identical, so nothing downstream can tell an "
        "earned marker from a retrofitted one"
    )

    import inspect

    for fn in ("sentinel_body", "write_all"):
        assert inspect.getsource(getattr(writer, fn)) == inspect.getsource(
            getattr(backfill, fn)
        ), (
            f"{fn}() has drifted between the two writers. It is duplicated "
            f"rather than imported for the PLUGIN_BUNDLE_MEMBERS reason "
            f"classify_ever_converged_sentinel()'s docstring gives, so nothing "
            f"but this pin keeps the two bodies parseable by one rule"
        )
    mode_a = path_a.stat().st_mode & 0o777
    mode_b = path_b.stat().st_mode & 0o777
    assert mode_a == mode_b, (
        f"sentinel MODE has drifted: ledger_update.py wrote {oct(mode_a)}, "
        f"backfill_ever_converged.py wrote {oct(mode_b)}"
    )

    # And: never overwritten by either writer, on a second call.
    before_a, before_b = path_a.read_bytes(), path_b.read_bytes()
    assert writer.mark_ever_converged(seg, dir_a) is True
    assert call_mark(backfill, seg, dir_b) == "already_present"
    assert (path_a.read_bytes(), path_b.read_bytes()) == (before_a, before_b), (
        "create-only idempotence covers the BODY too: a second call must not "
        "rewrite provenance over a marker that is already there"
    )


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
        outcome = call_mark(backfill, seg, dir_b)
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
        assert call_mark(backfill, seg, dir_b) == "already_present"


# ---------------------------------------------------------------------------
# [R2/R3 fold, RAW #8] ledger_update.py's OWN mark_ever_converged() must now
# fsync what it publishes -- the durability backfill_ever_converged.py has
# had since 1.20.0 (three sites: :636, :850, :1395) and this writer never
# had. These three drive the REAL ledger_update.py module directly (the same
# _load_module() this section already uses above), never a rebuilt fixture,
# so a fix that only touches the shipped file is what each one pins.
# ---------------------------------------------------------------------------

def test_mark_ever_converged_fsyncs_the_sentinel_and_the_segments_directory(tmp_path):
    """Both halves of the fold's durability contract, pinned in one call:
    the sentinel FILE itself must be fsynced (fsync on a file commits its
    contents, not the directory link that makes it findable) and the
    segments/ DIRECTORY must be fsynced too, so the link survives a crash.
    RED by construction against unfixed code -- measured zero os.fsync
    calls anywhere in ledger_update.py's mark_ever_converged() -- so
    neither inode is ever seen.

    Identifies each descriptor by WHAT IT REFERS TO (dev, inode), never by
    call order or count -- the file's own counting_fsync technique at
    :1678-1691 -- so an fsync the fix adds in either order still gets
    caught, and an unrelated fsync elsewhere in the process cannot make
    this pass vacuously."""
    writer = _load_module(LEDGER_UPDATE_SRC, "ledger_update_fsync_positive")
    segments_dir = tmp_path / "segments"
    segments_dir.mkdir()
    seg = "segFsyncPositive"

    real_fsync = writer.os.fsync
    writer.os.fsync, synced_ids = _fsync_recording_synced_ids(real_fsync)
    try:
        ok = writer.mark_ever_converged(seg, segments_dir)
    finally:
        writer.os.fsync = real_fsync

    assert ok is True, "precondition: the fresh-create path must succeed"

    sentinel_path = writer.ever_converged_path(seg, segments_dir)
    sentinel_id = (os.stat(sentinel_path).st_dev, os.stat(sentinel_path).st_ino)
    dir_id = (os.stat(segments_dir).st_dev, os.stat(segments_dir).st_ino)

    assert sentinel_id in synced_ids, (
        "mark_ever_converged() must fsync the sentinel FILE it just wrote, "
        "not only the converged FRAGMENT it backs (that fsync lives in "
        "write_fragment_atomically(), a different file)"
    )
    assert dir_id in synced_ids, (
        "mark_ever_converged() must fsync the segments/ DIRECTORY too, so "
        "the sentinel's own directory entry -- not merely its contents -- "
        "survives a crash"
    )


def test_the_directory_fsync_follows_the_directory_that_got_the_sentinel_not_the_name(tmp_path):
    """A rename of segments/ DURING the call must not redirect the directory
    fsync onto whatever now answers to that name.

    MR review reproduced the defect this pins: an earlier revision of
    _sync_published_sentinel() resolved `parent` by NAME after the file sync,
    so renaming segments/ aside and moving a replacement into its place
    between the two syncs made the second sync land on the REPLACEMENT while
    the directory that actually received the sentinel was never synced --
    reported as success. The caller then records convergence while the
    dispatch gate reads the sentinel as absent and permits retranslation.

    The assertion is about IDENTITY, not about refusing the rename. Syncing
    the directory that got the create is the CORRECT answer, not merely the
    safe one: that is where the sentinel is, so that is the entry whose
    durability the caller is promising. Pinning a dir-fd before the file
    sync makes both syncs describe one directory by (st_dev, st_ino) rather
    than by a name another process can rebind.

    Interleaves at the only point that matters -- the FIRST fsync, which is
    the sentinel file -- so the rename lands strictly between the two
    syncs, exactly as the review's reproduction did."""
    writer = _load_module(LEDGER_UPDATE_SRC, "ledger_update_dir_rename_race")
    segments_dir = tmp_path / "segments"
    segments_dir.mkdir()
    seg = "segDirRenameRace"

    displaced = tmp_path / "segments.displaced"
    replacement = tmp_path / "segments.replacement"
    replacement.mkdir()

    original_id = (os.stat(segments_dir).st_dev, os.stat(segments_dir).st_ino)
    replacement_id = (os.stat(replacement).st_dev, os.stat(replacement).st_ino)
    assert original_id != replacement_id, (
        "precondition: the two directories must be distinguishable by inode, "
        "or this test cannot tell which one was synced"
    )

    real_fsync = writer.os.fsync
    synced_ids = []
    swapped = []

    def fsync_then_swap(fd):
        st = os.fstat(fd)
        synced_ids.append((st.st_dev, st.st_ino))
        result = real_fsync(fd)
        if not swapped:
            # Strictly between the two syncs: segments/ is moved aside and a
            # different directory takes its name.
            os.rename(segments_dir, displaced)
            os.rename(replacement, segments_dir)
            swapped.append(True)
        return result

    writer.os.fsync = fsync_then_swap
    try:
        ok = writer.mark_ever_converged(seg, segments_dir)
    finally:
        writer.os.fsync = real_fsync

    assert swapped, (
        "precondition: the rename never fired, so this test proved nothing -- "
        "mark_ever_converged() must reach at least one fsync"
    )
    assert ok is False, (
        "CONTRACT, corrected by an MR round: durable is not the same as "
        "FINDABLE. The sentinel really was written and synced into the "
        "directory that received it, but that directory is no longer what "
        "segments/ resolves to, so the dispatch gate reads ABSENT and the "
        "segment is unprotected at the canonical path. Returning True here "
        "would let the caller record convergence over exactly that state -- "
        "which is the retranslation this sentinel exists to prevent. An "
        "earlier revision of this test asserted True and was wrong."
    )
    assert (displaced / f".ever_converged.{seg}").is_file(), (
        "precondition: the sentinel must have landed in the directory that "
        "was later renamed aside, or the race being pinned did not happen"
    )
    assert original_id in synced_ids, (
        "the directory fsync must follow the directory that RECEIVED the "
        "sentinel, held by descriptor -- resolving the parent by name after "
        "the file sync lets a rename redirect it, which is the reproduced "
        "defect this pins"
    )
    assert replacement_id not in synced_ids, (
        "the directory that merely inherited the NAME was synced -- so the "
        "entry the caller just promised to make durable was not, and "
        "mark_ever_converged() reported success anyway"
    )


def test_a_directory_swap_before_the_syncs_cannot_redirect_either_of_them(tmp_path):
    """The SECOND reproduction an MR review landed on the same defect class,
    and the reason the fix became a pin rather than a third narrowing.

    The first round moved the parent open ahead of the file sync; review
    then showed the descriptor was still acquired AFTER the pathname-based
    O_CREAT|O_EXCL, so a swap of segments/ before the syncs still redirected
    both -- reported as success while the sentinel and directory actually
    current at segments/ went unsynced. Narrowing the window twice did not
    close it, because the window WAS the pathname resolution. segments/ is
    now resolved exactly once, before the entry is touched, and every later
    operation is relative to that descriptor.

    This interleaves at os.write(), i.e. after the create and strictly
    before both fsyncs, and swaps in a replacement directory that already
    holds a same-named regular sentinel -- the review's own construction,
    which defeats any check that merely asks "is a regular file present at
    this path". Identity is the only thing that separates them.

    Two-sided: it asserts the ORIGINAL directory and the sentinel this call
    created were synced, AND that neither of the replacement's inodes was.
    An implementation that reopens by name passes the first pair of
    assertions on the decoys and fails the second."""
    writer = _load_module(LEDGER_UPDATE_SRC, "ledger_update_predir_swap")
    segments_dir = tmp_path / "segments"
    segments_dir.mkdir()
    seg = "segPreDirSwap"

    displaced = tmp_path / "segments.displaced"
    replacement = tmp_path / "segments.replacement"
    replacement.mkdir()
    # The decoy carries a same-named REGULAR sentinel, so only identity
    # distinguishes it from the real one.
    (replacement / f".ever_converged.{seg}").write_text("converged\n", encoding="utf-8")

    original_dir_id = (os.stat(segments_dir).st_dev, os.stat(segments_dir).st_ino)
    decoy_dir_id = (os.stat(replacement).st_dev, os.stat(replacement).st_ino)
    decoy_file_id = (
        os.stat(replacement / f".ever_converged.{seg}").st_dev,
        os.stat(replacement / f".ever_converged.{seg}").st_ino,
    )
    assert original_dir_id != decoy_dir_id, (
        "precondition: the two directories must differ by inode, or this "
        "test cannot tell which one was synced"
    )

    real_write = writer.os.write
    real_fsync = writer.os.fsync
    synced_ids = []
    swapped = []

    def write_then_swap(fd, data):
        result = real_write(fd, data)
        if not swapped:
            # After the create, strictly before either fsync.
            os.rename(segments_dir, displaced)
            os.rename(replacement, segments_dir)
            swapped.append(True)
        return result

    def recording_fsync(fd):
        st = os.fstat(fd)
        synced_ids.append((st.st_dev, st.st_ino))
        return real_fsync(fd)

    captured_stderr = io.StringIO()
    real_stderr = writer.sys.stderr
    writer.os.write = write_then_swap
    writer.os.fsync = recording_fsync
    writer.sys.stderr = captured_stderr
    try:
        ok = writer.mark_ever_converged(seg, segments_dir)
    finally:
        writer.os.write = real_write
        writer.os.fsync = real_fsync
        writer.sys.stderr = real_stderr

    assert swapped, (
        "precondition: the swap never fired, so this test proved nothing"
    )
    assert ok is False, (
        "same corrected contract as the sibling test above: the pin makes "
        "both syncs land on the directory that received the sentinel, but "
        "the canonical path now names the decoy, so convergence must be "
        "REFUSED rather than recorded over a segment the dispatch gate will "
        "read as unprotected"
    )

    created = displaced / f".ever_converged.{seg}"
    assert created.is_file(), (
        "precondition: the sentinel this call created must be in the "
        "directory that was renamed aside, or the race did not happen"
    )
    created_id = (os.stat(created).st_dev, os.stat(created).st_ino)

    assert created_id in synced_ids, (
        "the file fsync must land on the sentinel THIS call created, held by "
        "descriptor -- reopening it by name after a directory swap syncs the "
        "decoy instead"
    )
    assert original_dir_id in synced_ids, (
        "the directory fsync must land on the directory that RECEIVED the "
        "sentinel, held by descriptor pinned before the create"
    )
    assert decoy_file_id not in synced_ids, (
        "the decoy sentinel that merely inherited the NAME was synced, so "
        "the entry this call actually created was left undurable while "
        "mark_ever_converged() reported success"
    )
    assert decoy_dir_id not in synced_ids, (
        "the decoy directory that merely inherited the NAME was synced -- "
        "the reproduced defect, one narrowing later"
    )

    # The displacement remedy is a DIFFERENT message from the OS-error one,
    # because an operator acts on it differently: re-running cannot settle a
    # replaced directory. Asserted so the string is reachable rather than
    # dead prose, and so a future edit cannot silently route this arm into
    # the generic "retry once the OS problem is fixed" advice.
    stderr_lower = captured_stderr.getvalue().lower()
    assert "was replaced during this call" in stderr_lower, (
        "the refusal must say the directory was displaced, not merely that "
        "something failed"
    )
    assert "put the intended directory back" in stderr_lower, (
        "the operator needs the remedy for a REPLACED directory, which is "
        "not the retry advice every other failure arm gives"
    )


def test_a_directory_fsync_failure_makes_mark_ever_converged_return_false(tmp_path):
    """Mirrors backfill_ever_converged.py's own
    test_a_directory_fsync_failure_keeps_the_sentinels_and_still_fails_the_run
    (:1605) for the OTHER writer of this artifact. FAIL-CLOSED is the
    deliberate contract decision the plan states: unlike
    write_fragment_atomically()'s best-effort directory sync (`except
    OSError: pass`), a segments/ directory-fsync failure here must refuse
    convergence rather than report success over a directory entry that was
    never made durable -- claim_record.py:565-577 argues the split
    explicitly, for exactly this asymmetry (a crash can lose the entry
    while the fragment it backs survives).

    Asserts the NEGATIVE outcome the fix produces, not merely "some fsync
    happened" -- a test that only checked call count would pass on
    unfixed code too, since unfixed code also returns non-True... except it
    doesn't: unfixed code returns True unconditionally, which is exactly
    what this pins against."""
    writer = _load_module(LEDGER_UPDATE_SRC, "ledger_update_dir_fsync_fail")
    segments_dir = tmp_path / "segments"
    segments_dir.mkdir()
    seg = "segDirFsyncFail"

    real_fsync = writer.os.fsync
    writer.os.fsync = _fsync_failing_only_on_a_directory(real_fsync)
    try:
        ok = writer.mark_ever_converged(seg, segments_dir)
    finally:
        writer.os.fsync = real_fsync

    assert ok is False, (
        "a segments/ directory fsync failure must fail mark_ever_converged() "
        "closed, not report convergence over an undurable directory entry"
    )
    sentinel_path = writer.ever_converged_path(seg, segments_dir)
    assert sentinel_path.is_file(), (
        "the sentinel must NOT be removed on a directory-sync failure: the "
        "create already published the name, so another reader may already "
        "be relying on it -- same reasoning as the sibling writer's own "
        "directory-fsync-failure test"
    )
    assert_marker_written_by(sentinel_path, "ledger_update", seg)


def test_a_failed_sentinel_sync_does_not_launder_into_true_on_retry(tmp_path):
    """THE laundering test codex round 2's admitted MAJOR demanded (see the
    plan's [R3] section). `mark_ever_converged()`'s `O_CREAT|O_EXCL` hits
    FileExistsError on a retry, classifies the entry SENTINEL_PRESENT, and
    the already-present branch must NOT return True having synced nothing --
    that would record convergence over a sentinel whose directory entry was
    never made durable, laundering the first call's failed sync into a green
    result. Exactly the shape backfill_ever_converged.py:971-1000 already
    documents and tests at :1644-1709 for its own retry path.

    RED against a fix that syncs only the fresh-create branch: the second
    call below would return True at the FileExistsError shortcut without
    ever touching os.fsync again."""
    writer = _load_module(LEDGER_UPDATE_SRC, "ledger_update_launder")
    segments_dir = tmp_path / "segments"
    segments_dir.mkdir()
    seg = "segLaunder"

    real_fsync = writer.os.fsync
    writer.os.fsync = _fsync_failing_only_on_a_directory(real_fsync)
    try:
        first = writer.mark_ever_converged(seg, segments_dir)
        assert first is False, (
            "precondition: the first call must fail on the directory sync"
        )
        second = writer.mark_ever_converged(seg, segments_dir)
        assert second is False, (
            "the retry hits FileExistsError, classifies SENTINEL_PRESENT, "
            "and must not launder the first call's failed sync into True "
            "through the already-present shortcut"
        )
    finally:
        writer.os.fsync = real_fsync

    sentinel_path = writer.ever_converged_path(seg, segments_dir)
    sentinel_id = (os.stat(sentinel_path).st_dev, os.stat(sentinel_path).st_ino)
    dir_id = (os.stat(segments_dir).st_dev, os.stat(segments_dir).st_ino)

    writer.os.fsync, synced_ids = _fsync_recording_synced_ids(real_fsync)
    try:
        third = writer.mark_ever_converged(seg, segments_dir)
    finally:
        writer.os.fsync = real_fsync

    assert third is True, (
        "once the directory fsync actually works, the already-present path "
        "must succeed -- this call is what finally makes the entry durable"
    )
    assert sentinel_id in synced_ids and dir_id in synced_ids, (
        "the third call must actually SYNC, not merely return True -- a "
        "green retry that skips the work is invisible from the return "
        "value alone, which is the whole laundering shape this test pins"
    )


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

    assert proc.returncode == 1, (
        f"an entry this script cannot verify and cannot repair leaves a "
        f"segment unprotected, so the run must not exit 0; stderr={proc.stderr!r}"
    )
    payload = parse_stdout(proc)
    assert payload["success"] is False
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

    assert proc.returncode == 1, f"stderr={proc.stderr!r}"
    payload = parse_stdout(proc)
    assert payload["success"] is False
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
    """Pre-fix, an OSError from mark_ever_converged()'s os.write() was
    OUTSIDE the try/except producing this function's documented
    string-outcome contract -- it propagated as an uncaught exception
    instead, on exactly the failure that contract exists to cover. The
    caller (run(), under --apply) has no handling for an exception here;
    only for the three documented outcomes.

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
        outcome = call_mark(backfill, seg, segments_dir)
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

    # Nothing may appear at the public name. This assertion used to say the
    # OPPOSITE, on the premise that residue there was harmless "because every
    # consumer only calls .exists()". That premise died twice over: this
    # release replaced those `.exists()` reads with
    # classify_ever_converged_sentinel(), and -- the reason that matters --
    # residue launders a retry. It reads as SENTINEL_PRESENT, so the next run
    # reports the segment already protected and never completes the fsync the
    # first run failed to. Run 1 red, run 2 green, durability never
    # established by either.
    #
    # The first repair made the failing path unlink the name it had created,
    # which was a BLOCKER of its own (it could delete a sentinel another
    # writer had since installed). Staging is what makes this assertion hold
    # WITHOUT any unlink of the public name: the write that fails here goes
    # to the temp file, so the public name was never created in the first
    # place. Hence `not path.exists()` and no stray staging file either --
    # the second half is what would go red if cleanup regressed.
    path = backfill.ever_converged_path(seg, segments_dir)
    assert not path.exists(), (
        "a failed write must leave NOTHING at the public sentinel name: the "
        "bytes are staged elsewhere and the name is only published, by "
        "os.link(), once they are durable"
    )
    strays = [p.name for p in segments_dir.iterdir()]
    assert strays == [], (
        f"the staging file must be cleaned up on a failed write, or the next "
        f"run's segments/ fills with debris; found {strays}"
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
        outcome = call_mark(backfill, seg, segments_dir)
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
    # json_stdout.py (#369): every staged script above loads it by exact
    # path from beside itself, so a root without it exits rather than runs.
    shutil.copy2(LEDGER_MERGE_SRC.parent / "json_stdout.py", plugin_scripts_dir / "json_stdout.py")
    (plugin_scripts_dir / "cache_key.py").write_text(FAKE_CACHE_KEY_PY, encoding="utf-8")
    return plugin_root


def test_durable_root_flag_absent_orphan_copy_fails_self_anchored(tmp_path):
    """Negative control: an orphan copy invoked WITHOUT --durable-root has
    no ledger_merge.py sibling to find -- cannot succeed via self-anchoring."""
    orphan_dir = tmp_path / "orphan_location" / "scripts"
    orphan_dir.mkdir(parents=True)
    orphan_script = orphan_dir / "backfill_ever_converged.py"
    shutil.copy2(BACKFILL_SCRIPT_SRC, orphan_script)
    # json_stdout.py (#369): this fixture stages ONE script on purpose, and
    # the property under test needs it to START -- without its sibling it
    # would exit on the missing helper instead, which is a different test.
    shutil.copy2(BACKFILL_SCRIPT_SRC.parent / "json_stdout.py", orphan_script.parent / "json_stdout.py")

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
    # json_stdout.py (#369): this fixture stages ONE script on purpose, and
    # the property under test needs it to START -- without its sibling it
    # would exit on the missing helper instead, which is a different test.
    shutil.copy2(BACKFILL_SCRIPT_SRC.parent / "json_stdout.py", orphan_script.parent / "json_stdout.py")

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


def test_a_failed_create_does_not_launder_into_a_successful_retry(tmp_path):
    """The failure mode that makes an honest red worse than useless.

    A post-create error (write, fsync, close, directory sync) happens after
    O_CREAT|O_EXCL has already published the name. If that file is left
    behind, the NEXT attempt classifies it SENTINEL_PRESENT and returns
    `already_present` -- a success -- without ever completing the fsync the
    first attempt failed. Run 1 reports failure, run 2 reports protection, and
    no run ever established durability. The operator sees the second result.
    """
    backfill = _load_module(BACKFILL_SCRIPT_SRC, "backfill_under_test_no_laundering")
    segments_dir = tmp_path / "segments"
    segments_dir.mkdir()
    seg = "segLaunder"

    real_write = backfill.os.write

    def failing_write(fd, data):
        raise OSError(28, "No space left on device")

    backfill.os.write = failing_write
    try:
        first = call_mark(backfill, seg, segments_dir)
    finally:
        backfill.os.write = real_write

    assert first.startswith("error: "), first

    # The retry must do real work, not inherit a half-made marker.
    second = call_mark(backfill, seg, segments_dir)
    assert second == "created", (
        f"the retry must genuinely create the sentinel, not report "
        f"`already_present` off the residue of the failed attempt; got {second!r}"
    )

    # And the marker it left is the real thing, not the empty file the failed
    # write would have left -- which is what makes this test able to tell a
    # true retry from a laundered one.
    path = backfill.ever_converged_path(seg, segments_dir)
    assert_marker_written_by(path, "backfill_ever_converged", seg)


def test_a_failed_create_never_destroys_another_writers_sentinel(tmp_path):
    """The interleaving that made the FIRST cleanup attempt a BLOCKER.

    That version created the public name with O_CREAT|O_EXCL and unlinked it
    again on failure, reasoning that EXCL proved the call owned the file. EXCL
    proves only that this call installed the entry at open time -- it does not
    reserve the pathname. Between the failed write and the unlink, another
    actor can remove the incomplete inode and install a real, fully-synced
    sentinel, and the unlink then deletes THAT: a cleanup that destroys
    protection somebody else established, which is strictly worse than the
    residue it was cleaning up.

    The failing write here performs exactly that substitution, so the test
    reproduces the race deterministically rather than hoping to hit it."""
    backfill = _load_module(BACKFILL_SCRIPT_SRC, "backfill_under_test_no_destroy")
    segments_dir = tmp_path / "segments"
    segments_dir.mkdir()
    seg = "segRace"
    path = backfill.ever_converged_path(seg, segments_dir)
    other_writer_content = b"converged\n"

    real_write = backfill.os.write

    def failing_write_that_lets_another_writer_in(fd, data):
        # Stand in for the concurrent actor: whatever this call staged, a real
        # sentinel is now at the public name.
        if not path.exists():
            path.write_bytes(other_writer_content)
        raise OSError(28, "No space left on device")

    backfill.os.write = failing_write_that_lets_another_writer_in
    try:
        outcome = call_mark(backfill, seg, segments_dir)
    finally:
        backfill.os.write = real_write

    assert outcome.startswith("error: "), outcome
    assert path.exists(), (
        "the other writer's sentinel was destroyed by this call's cleanup -- "
        "a failed create must never remove a marker it did not create"
    )
    assert path.read_bytes() == other_writer_content

    # No staging file may be left lying around in segments/ either.
    strays = [p.name for p in segments_dir.iterdir() if "staging" in p.name]
    assert strays == [], f"staging files left behind: {strays}"


@pytest.mark.parametrize(
    "where",
    ["segment_id", "status"],
    ids=["surrogate-in-segment-id", "surrogate-in-status"],
)
def test_a_lone_surrogate_still_produces_a_json_payload_not_a_traceback(tmp_path, where):
    """A ledger is JSON, and JSON can carry a lone surrogate.

    `json.dumps(..., ensure_ascii=False)` keeps it verbatim and the PRINT then
    raises UnicodeEncodeError -- from outside every handler in this script, so
    the process dies with a traceback and emits no JSON at all, exactly where
    the output contract promises a failure payload. Both carriers matter: a
    surrogate segment id is caught by validation but is then interpolated into
    the fatal payload, and a surrogate STATUS travels through `not_evaluated`
    to the success payload, which validation never touches."""
    root = setup_mixed_project(tmp_path)
    prime_materialized_ledger(root)

    ledger = root / "runs" / "ledger.json"
    doc = json.loads(ledger.read_text())
    if where == "segment_id":
        doc["segments"]["\ud800"] = {"status": "in_progress"}
    else:
        doc["segments"]["seg_surrogate_status"] = {"status": "\ud800"}
    ledger.write_text(json.dumps(doc, ensure_ascii=True))

    proc = run_backfill(root)

    # The message names the SYMPTOM and shows stderr, deliberately without
    # naming a cause. An earlier version asserted the cause ("the run died
    # before emitting its payload"), which cost three rounds of repairing
    # already-correct guards: the script had stopped importing entirely, and
    # a non-importable module produces this identical empty stdout. Read the
    # stderr below before believing any theory about why.
    assert proc.stdout.strip(), (
        f"no JSON on stdout. Could be the surrogate this test is about, or "
        f"anything that stops the script reaching its final print at all -- "
        f"a SyntaxError included. stderr tail: {proc.stderr[-800:]!r}"
    )
    payload = json.loads(proc.stdout)
    assert isinstance(payload, dict) and "success" in payload
    assert "UnicodeEncodeError" not in proc.stderr
    assert "Traceback" not in proc.stderr


def test_an_unsafe_segment_id_is_refused_even_when_it_is_not_converged(tmp_path):
    """Segment ids are validated before the status branch, not inside it.

    `not_evaluated` gave non-converged records their first route to stdout.
    Validation sat on the converged branch only, so an id like `../unsafe`
    -- rejected outright when it was converged -- travelled straight out
    through the new list, with `success: true` alongside it."""
    root = setup_mixed_project(tmp_path)
    write_fragment(root, "seg_in_progress", in_progress_fragment())
    prime_materialized_ledger(root)

    ledger = root / "runs" / "ledger.json"
    doc = json.loads(ledger.read_text())
    doc["segments"]["../unsafe"] = {"status": "in_progress"}
    ledger.write_text(json.dumps(doc))

    proc = run_backfill(root)

    assert proc.returncode != 0, (
        f"an unsafe segment id must be refused wherever it appears, not "
        f"reported; got rc={proc.returncode} stdout={proc.stdout[:400]!r}"
    )
    payload = json.loads(proc.stdout)
    # Assert the SPECIFIC refusal, not merely that something failed. Without
    # this, any unrelated fatal -- an unreadable ledger, a merge error --
    # satisfies rc != 0, and the absence check below passes vacuously because
    # a fatal payload has no `not_evaluated` key for `../unsafe` to be in.
    assert payload.get("success") is False
    assert "unsafe segment id" in payload.get("error", ""), (
        f"the run must fail BECAUSE of the id, not for some unrelated "
        f"reason that happens to also be fatal; got {payload!r}"
    )
    assert "../unsafe" in payload.get("error", "")
    assert "../unsafe" not in json.dumps(payload.get("not_evaluated", []))


def test_a_staging_fsync_failure_never_publishes_the_public_name(tmp_path):
    """The durability barrier sits BEFORE publication, so the public name must
    not exist AT THE MOMENT the fsync runs.

    The assertion is inside the callback deliberately, and the first version of
    this test is why. It checked only the post-state -- error returned, public
    name absent, directory empty -- and review showed all three also hold on
    the OLD public-name-first implementation, whose failure path unlinked what
    it had published. Both orderings converge on the same wreckage, so a test
    that inspects the aftermath cannot tell them apart, and this one passed
    against the very implementation it was written to forbid. Ordering is only
    observable from inside the window."""
    backfill = _load_module(BACKFILL_SCRIPT_SRC, "backfill_under_test_fsync_failure")
    segments_dir = tmp_path / "segments"
    segments_dir.mkdir()
    seg = "segFsyncFail"
    path = backfill.ever_converged_path(seg, segments_dir)

    real_fsync = backfill.os.fsync
    observed = {}

    def failing_fsync(fd):
        # THE assertion of this test: at the durability barrier, the sentinel
        # name must still be unpublished. Recorded rather than asserted here
        # so a failure surfaces as this test's own message and not as an
        # exception escaping through the code under test.
        observed["public_name_existed_at_fsync"] = os.path.lexists(str(path))
        raise OSError(5, "Input/output error")  # EIO

    backfill.os.fsync = failing_fsync
    try:
        outcome = call_mark(backfill, seg, segments_dir)
    finally:
        backfill.os.fsync = real_fsync

    assert observed.get("public_name_existed_at_fsync") is False, (
        "the sentinel name was ALREADY published when the durability barrier "
        "ran, so a crash there leaves bytes nothing has synced reachable "
        "under the name readers consult -- stage, fsync, THEN link"
    )
    assert outcome.startswith("error: "), (
        f"an fsync-time OSError must produce the documented string outcome, "
        f"not an uncaught exception; got {outcome!r}"
    )
    assert "Input/output error" in outcome

    assert not path.exists(), (
        "bytes that were never fsynced must not be reachable under the "
        "sentinel name -- that is the entire point of staging before linking"
    )
    assert list(segments_dir.iterdir()) == [], (
        "the staging file must be cleaned up when the fsync fails"
    )


def _apply_run(backfill, root):
    """Drive run() IN-PROCESS under --apply, so os.fsync can be patched.

    run_backfill() shells out, which is the right harness for output-contract
    tests but cannot reach a syscall seam inside the child."""
    # No --plugin-root: that flag names a PLUGIN root (assets/scripts/...),
    # while the fixture is a DURABLE root. Left unset, ledger_merge.py
    # resolves module-relative to the real plugin copy, which is what the
    # subprocess harness effectively uses too.
    args = backfill.build_arg_parser().parse_args(
        ["--durable-root", str(root), "--apply"]
    )
    return backfill.run(args, backfill.resolve_dirs(str(root), None))


def _fsync_recording_synced_ids(real_fsync):
    """A pass-through os.fsync that records WHICH entries were synced, by
    (st_dev, st_ino) rather than by path -- the same identity-not-name test
    the assertions using it make. Returns (fsync, synced_ids); the set is
    live, so read it after the call under test. Sibling of
    _fsync_failing_only_on_a_directory() below, and factored out for the
    same reason: two tests were carrying byte-identical copies of it."""
    synced_ids = set()

    def recording(fd):
        st = os.fstat(fd)
        synced_ids.add((st.st_dev, st.st_ino))
        return real_fsync(fd)

    return recording, synced_ids


def _fsync_failing_only_on_a_directory(real_fsync):
    """Distinguish the directory seam by WHAT THE FD REFERS TO, never by call
    order or call count -- an ordering assumption stops testing this seam the
    moment another fsync is added ahead of it, silently."""
    def failing(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError(5, "Input/output error")  # EIO
        return real_fsync(fd)
    return failing


def test_a_directory_fsync_failure_keeps_the_sentinels_and_still_fails_the_run(tmp_path):
    """The one failure that deliberately leaves published names in place.

    Past the link a name may already be another reader's protection, so
    removing it is the destruction staging exists to prevent. The honest
    report is "they exist, their durability is unproven" -- which must still
    FAIL the run, so it cannot claim protection it cannot vouch for. Both
    halves are asserted because they pull in opposite directions and a
    plausible fix for either breaks the other."""
    backfill = _load_module(BACKFILL_SCRIPT_SRC, "backfill_under_test_dir_fsync")
    root = setup_mixed_project(tmp_path)
    prime_materialized_ledger(root)
    segments_dir = root / "segments"

    real_fsync = backfill.os.fsync
    backfill.os.fsync = _fsync_failing_only_on_a_directory(real_fsync)
    try:
        result = _apply_run(backfill, root)
    finally:
        backfill.os.fsync = real_fsync

    assert result["created"], "the test must have actually created sentinels"
    assert result["success"] is False, (
        "an unsynced segments directory means the entries this run published "
        "may not survive a crash, so the run cannot report success"
    )
    assert "could not be synced" in (result["directory_sync_error"] or "")

    for seg in result["created"]:
        path = backfill.ever_converged_path(seg, segments_dir)
        assert path.is_file(), (
            f"{seg}'s sentinel must NOT be removed: the link already "
            f"published the name, so another reader may be relying on it"
        )
        assert_marker_written_by(path, "backfill_ever_converged", seg)
    strays = [q.name for q in segments_dir.iterdir() if "staging" in q.name]
    assert strays == [], f"staging files left behind: {strays}"


def test_a_failed_directory_sync_does_not_launder_into_a_green_retry(tmp_path):
    """The retry must actually re-sync, not skip the work via
    `already_sentineled`.

    This is the defect review found in the per-segment version of the fsync.
    Run 1 published the sentinel and failed its directory fsync, reporting the
    segment in `failed_to_create`. Run 2's pre-write scan then classified that
    same published file as SENTINEL_PRESENT, filed it under
    `already_sentineled`, never called the writer for it, and so never reached
    the fsync that had failed -- returning `success: true` having established
    nothing. Red then green, durability proven by neither: the exact
    laundering shape this release exists to remove, one layer up from the
    residue version of it.

    The assertion that matters is therefore NOT "run 2 is green" but "run 2
    actually fsynced the directory". A retry that goes green by skipping the
    work looks identical from the outside, which is why the first version of
    this fix shipped with the hole."""
    backfill = _load_module(BACKFILL_SCRIPT_SRC, "backfill_under_test_sync_retry")
    root = setup_mixed_project(tmp_path)
    prime_materialized_ledger(root)

    real_fsync = backfill.os.fsync
    backfill.os.fsync = _fsync_failing_only_on_a_directory(real_fsync)
    try:
        first = _apply_run(backfill, root)
    finally:
        backfill.os.fsync = real_fsync

    assert first["created"] and first["success"] is False, (
        "run 1 must have published sentinels and failed on the directory sync"
    )

    dir_syncs = []

    # Identify the directory by INODE, not merely by "is a directory". A
    # bare S_ISDIR count is satisfied by any directory fsync at all, so an
    # unrelated one elsewhere in the process would make this test green
    # without the segments directory ever being synced -- the assertion
    # would then be about the wrong file.
    segments_id = (os.stat(root / "segments").st_dev,
                   os.stat(root / "segments").st_ino)

    def counting_fsync(fd):
        st = os.fstat(fd)
        if stat.S_ISDIR(st.st_mode) and (st.st_dev, st.st_ino) == segments_id:
            dir_syncs.append(fd)
        return real_fsync(fd)

    backfill.os.fsync = counting_fsync
    try:
        second = _apply_run(backfill, root)
    finally:
        backfill.os.fsync = real_fsync

    assert second["created"] == [], (
        "run 2 should find the sentinels already published -- that is the "
        "very condition under which the old code skipped the sync"
    )
    assert dir_syncs, (
        "run 2 created nothing, so it never called the writer -- and it did "
        "NOT fsync the segments directory either. Run 1's unsynced entries "
        "are still unsynced while the run reports success. That is the "
        "laundering, and it is invisible from the payload alone"
    )
    assert second["success"] is True
    assert second["directory_sync_error"] is None


def test_a_segments_dir_replaced_mid_run_fails_instead_of_reporting_created(tmp_path):
    """Holding one descriptor makes the run self-consistent, NOT correct.

    Every link, unlink and fsync goes to the directory `dir_fd` names. The
    readers -- select_segments.py, final_audit.py -- resolve
    `{durable_root}/segments` by PATHNAME, every time. Replace that pathname
    mid-run and the two stop being the same directory: the sentinels land
    where the descriptor points, and nobody who looks the path up will ever
    see them.

    Review found the previous version of this claiming the case "fails closed
    with ENOENT". It does not -- and since staging became descriptor-relative
    it cannot: every write in the sequence lands in the directory `dir_fd`
    names, so no retarget can separate the staging file from the link that
    publishes it. The link SUCCEEDS in the old directory and the run reports
    `created` for a name absent from the directory the dispatch gate reads.
    That is a false success -- the worst shape this script has, because the
    operator's next action is to dispatch.

    A single process cannot make a pathname stable against a concurrent
    renamer, so the fix reports rather than repairs. This test pins the
    reporting: the run must FAIL, and it must not claim the sentinels are
    where a reader would look."""
    backfill = _load_module(BACKFILL_SCRIPT_SRC, "backfill_under_test_retarget")
    root = setup_mixed_project(tmp_path)
    prime_materialized_ledger(root)
    segments_dir = root / "segments"

    real_link = backfill.os.link
    swapped = []

    def link_then_retarget(src, dst, **kwargs):
        # Publish into the ORIGINAL directory, exactly as the real sequence
        # does, and only then move that directory out from under the
        # pathname -- the interleaving that survives the descriptor binding.
        result = real_link(src, dst, **kwargs)
        if not swapped:
            aside = tmp_path / "segments_aside"
            os.rename(str(segments_dir), str(aside))
            os.mkdir(str(segments_dir))
            swapped.append(aside)
        return result

    backfill.os.link = link_then_retarget
    try:
        result = _apply_run(backfill, root)
    finally:
        backfill.os.link = real_link

    assert swapped, "the test never performed the retarget it exists to model"

    # THE causal assertion, and deliberately first. `success is False` is NOT
    # sufficient evidence here and asserting it alone would be near-vacuous:
    # segments processed AFTER the swap stage into the new directory while
    # the link resolves through the old descriptor, so they fail ENOENT on
    # their own and redden the run without the identity check existing at
    # all. Measured against a mutant with the check removed: `success` was
    # already False, and only these two assertions went red.
    assert result["segments_dir_replaced"] is not None, (
        "the run must detect that the directory it wrote to is no longer the "
        "one its path names -- per-segment ENOENT on the segments that came "
        "after the swap is a side effect, not the detection"
    )
    # Asserted on the FIELD, not on words in a sentence. Review pointed out
    # that a free-text string is not a contract a caller can depend on, which
    # is why displacement got its own key instead of a phrase inside
    # `directory_sync_error`.
    assert result["directory_sync_error"] is None, (
        f"the fsync itself succeeded here, so this must be reported as "
        f"displacement and nothing else; got "
        f"{result['directory_sync_error']!r}"
    )
    assert result["success"] is False

    # And the substantive claim: the sentinel really is invisible under the
    # path readers use. If this ever stops holding, the failure above has
    # become over-strict rather than protective.
    for seg in result["created"]:
        assert not backfill.ever_converged_path(seg, segments_dir).exists()
        assert (swapped[0] / f".ever_converged.{seg}").is_file()


def test_a_retarget_during_the_census_is_caught_too_not_only_during_the_writes(tmp_path):
    """The census is the part that DECIDES; covering only the writes is
    useless if what got fooled was the decision.

    The earlier version of the identity check opened its descriptor AFTER the
    sentinel census, so this interleaving sailed through: directory A holds a
    real sentinel, the census reads A and files that segment under
    `already_sentineled`, A is renamed aside and an empty B takes the
    pathname, the descriptor opens B, the segment is not in
    `missing_sentinels` so B never receives a marker -- and the final
    comparison finds the descriptor and the pathname agreeing perfectly,
    because both now name B. `success: true`, and the dispatch gate reads B
    and sees nothing.

    Note what makes it nastier than the write-window case: this run writes
    NOTHING, so no per-segment error appears anywhere to redden it. The only
    thing standing between it and a false green is the descriptor predating
    the census."""
    backfill = _load_module(BACKFILL_SCRIPT_SRC, "backfill_under_test_census_retarget")
    root = setup_mixed_project(tmp_path)
    prime_materialized_ledger(root)
    segments_dir = root / "segments"

    # Give every converged segment a real sentinel first, so the census puts
    # them all in `already_sentineled` and the run has no writes to do.
    first = _apply_run(backfill, root)
    assert first["success"] is True and first["created"], (
        "setup must actually raise the sentinels the census will later read"
    )
    # `missing_sentinels` is the CENSUS result, so it is non-empty on a run
    # that had work to do -- it names what this run went on to create. The
    # precondition that matters is the state AFTER: a second census must find
    # nothing missing, which is what makes the run under test a pure
    # already_sentineled read with no writes.
    baseline = _apply_run(backfill, root)
    assert baseline["missing_sentinels"] == [] and baseline["created"] == [], (
        f"the project must be fully protected before the census test runs; "
        f"missing={baseline['missing_sentinels']}"
    )

    # Derived from ever_converged_segs, NOT from already_sentineled. The
    # census calls the classifier once per CONVERGED segment; the two counts
    # coincide only while every converged segment happens to hold a regular
    # sentinel. An ambiguous entry, or any fixture change, would desync them
    # and make the swap land mid-census -- turning this into the different,
    # self-reddening write case and giving a red for the wrong reason.
    census_size = len(baseline["ever_converged_segs"])
    assert census_size, "the fixture must have segments for the census to read"
    assert baseline["ambiguous_sentinels"] == [], (
        "an ambiguous entry would desync the classifier call count from "
        "ever_converged_segs and break this test's swap timing"
    )

    real_classify = backfill.classify_ever_converged_sentinel
    swapped = []
    calls = []

    # `**kwargs` rather than a fixed signature: the census passes `dir_fd`
    # and the writer's EEXIST re-read passes it too, while a mutant that
    # reverts either call site passes neither. Forwarding whatever arrives
    # keeps this test valid under both, which is what makes it usable as the
    # mutation harness it doubles as.
    def classify_then_retarget(path, **kwargs):
        state = real_classify(path, **kwargs)
        calls.append(path)
        # Swap only once the census has read EVERY segment. Swapping earlier
        # sends the remaining lookups to the new empty directory, which puts
        # those segments in `missing_sentinels` and makes the run attempt
        # writes -- a different (and self-reddening) case. The one being
        # modelled here is the census completing normally against A and the
        # pathname moving before anything else happens, so the run has no
        # work to do and nothing else can go red.
        if len(calls) == census_size and not swapped:
            aside = tmp_path / "segments_aside_census"
            os.rename(str(segments_dir), str(aside))
            os.mkdir(str(segments_dir))
            swapped.append(aside)
        return state

    # setattr, not attribute assignment: the module object is dynamically
    # loaded, so a static checker cannot see this name on it.
    setattr(backfill, "classify_ever_converged_sentinel", classify_then_retarget)
    try:
        result = _apply_run(backfill, root)
    finally:
        setattr(backfill, "classify_ever_converged_sentinel", real_classify)

    assert swapped, "the test never performed the retarget it exists to model"
    assert result["created"] == [] and not result["failed_to_create"], (
        "this run must write nothing -- that is what makes the case "
        "dangerous, and what makes every other signal stay green"
    )
    assert result["segments_dir_replaced"] is not None, (
        "a census read from a directory the path no longer names cannot "
        "support a claim that anything is protected"
    )
    assert result["directory_sync_error"] is None
    assert result["success"] is False

    # The substance: what the census called protected is not visible.
    for seg in result["already_sentineled"]:
        assert not backfill.ever_converged_path(seg, segments_dir).exists()


def test_a_census_reads_the_descriptor_not_a_pathname_repointed_and_restored(tmp_path):
    """The swapped-away-AND-BACK interleaving, which the identity check
    cannot see and never could.

    `check_segments_dir_identity()` samples ONCE, at the end. A `segments/`
    symlink re-pointed at B for the length of the census and restored to A
    before that sample makes the sample compare A to A and agree -- so for as
    long as the census resolved `{segments_dir}/.ever_converged.<seg>` by
    PATHNAME, it read B's entries and reported them as A's protection.
    Reproduced on the descriptor-holding code: `success: true`,
    `already_sentineled` naming segments, `segments_dir_replaced: null`, and A
    without the sentinels. A run that says "already protected" is the one an
    operator acts on by dispatching, so this is the false green that costs a
    finished translation.

    The fix is not another check. A second identity sample is defeated by the
    same swap, and a locking protocol is not needed either: the run already
    HOLDS the directory open, so classifying relative to that descriptor
    leaves the swap no pathname to act on.

    Note what is deliberately asserted NEGATIVE: `segments_dir_replaced` must
    stay None. This interleaving is invisible to the identity check by
    construction, so a non-None there would mean the assertions above are
    being satisfied by the wrong mechanism -- the same reasoning that made
    `success is False` an inadmissible causal assertion in the two retarget
    tests above."""
    backfill = _load_module(BACKFILL_SCRIPT_SRC, "backfill_under_test_census_symlink")
    root = setup_mixed_project(tmp_path)
    prime_materialized_ledger(root)

    # segments -> segments_a, the REAL directory (it already holds the
    # fixture's one pre-existing sentinel); segments_b is the decoy the
    # pathname is briefly re-pointed at. A symlinked `segments/` is an
    # explicitly supported project shape -- that is why the open deliberately
    # omits O_NOFOLLOW -- so this is a supported project, not a broken one.
    dir_a = root / "segments_a"
    dir_b = root / "segments_b"
    os.rename(str(root / "segments"), str(dir_a))
    os.mkdir(str(dir_b))
    os.symlink("segments_a", str(root / "segments"))

    # B holds a real sentinel for EVERY ever-converged segment, including the
    # two A lacks. Under a pathname census that empties `missing_sentinels`
    # entirely, so the run writes nothing and no per-segment error exists to
    # redden it -- the false green in its purest form, exactly as reported.
    for seg in EVER_CONVERGED:
        (dir_b / f".ever_converged.{seg}").write_bytes(b"converged\n")

    real_classify = backfill.classify_ever_converged_sentinel
    census_calls = []
    repointed = []
    restored = []

    def classify_then_swap(path, **kwargs):
        # Re-point BEFORE the first lookup, so the entire census runs while
        # the pathname names B.
        if not repointed:
            os.unlink(str(root / "segments"))
            os.symlink("segments_b", str(root / "segments"))
            repointed.append(True)
        state = real_classify(path, **kwargs)
        census_calls.append(path)
        # ...and restore the moment the census ends, before anything else
        # runs, so the end-of-run identity sample finds A where it started.
        # That restoration is the whole point: it is what makes the false
        # green reachable, and what a single identity sample cannot see.
        if len(census_calls) == len(EVER_CONVERGED) and not restored:
            os.unlink(str(root / "segments"))
            os.symlink("segments_a", str(root / "segments"))
            restored.append(True)
        return state

    setattr(backfill, "classify_ever_converged_sentinel", classify_then_swap)
    try:
        result = _apply_run(backfill, root)
    finally:
        setattr(backfill, "classify_ever_converged_sentinel", real_classify)

    assert repointed and restored, (
        "the test never performed the swap-and-restore it exists to model"
    )
    assert os.readlink(str(root / "segments")) == "segments_a", (
        "the pathname must name A again by the end -- that is what makes the "
        "identity sample agree, and the false green reachable"
    )

    # THE causal assertion. Neither segment whose only sentinel lives in B
    # may be reported protected; only the one really present in A may be.
    assert result["already_sentineled"] == ["seg_conv_presentinel"], (
        f"only a sentinel that is really in the directory the descriptor "
        f"names may be counted as protection; got "
        f"{result['already_sentineled']} -- anything more means the census "
        f"read the decoy through the re-pointed pathname"
    )
    assert result["missing_sentinels"] == MISSING_BEFORE_APPLY, (
        f"the two segments A lacks must still be missing; got "
        f"{result['missing_sentinels']}"
    )
    assert sorted(result["created"]) == MISSING_BEFORE_APPLY, (
        f"and they must actually get their sentinels; got {result['created']}"
    )
    # NOT caught by the identity check -- see the docstring.
    assert result["segments_dir_replaced"] is None, (
        f"the pathname names A at the end, so the identity sample must agree: "
        f"this case is closed by the census, not by that check; got "
        f"{result['segments_dir_replaced']!r}"
    )
    assert result["directory_sync_error"] is None
    assert result["ambiguous_sentinels"] == []
    assert result["success"] is True

    # The substance, in both directions: A really is protected now, and the
    # decoy directory holds exactly what the test put there and nothing else.
    for seg in EVER_CONVERGED:
        assert (dir_a / f".ever_converged.{seg}").is_file(), (
            f"{seg} must be protected in the directory the descriptor names, "
            f"which is the one readers reach through the restored pathname"
        )
    assert sorted(p.name for p in dir_b.iterdir()) == sorted(
        f".ever_converged.{seg}" for seg in EVER_CONVERGED
    ), "nothing this run did may have reached the decoy directory"


def test_a_dry_run_also_refuses_when_the_segments_dir_was_replaced(tmp_path):
    """The dry run is the mode an operator acts on, so it needs the check too.

    SKILL.md's upgrade note tells them a dry run's `missing_sentinels` decides
    whether backfilling is needed at all. Before this, the descriptor was
    opened only under `--apply`, so a dry census could read a directory the
    path no longer names, come back with nothing missing, and talk the
    operator out of running `--apply` at all -- straight into a W5 dispatch
    against unprotected work. A false clean here is worse than a false clean
    under `--apply`, because nothing downstream ever revisits it."""
    backfill = _load_module(BACKFILL_SCRIPT_SRC, "backfill_under_test_dry_retarget")
    root = setup_mixed_project(tmp_path)
    prime_materialized_ledger(root)
    segments_dir = root / "segments"

    args = backfill.build_arg_parser().parse_args(["--durable-root", str(root)])
    dirs = backfill.resolve_dirs(str(root), None)

    real_classify = backfill.classify_ever_converged_sentinel
    swapped = []

    def classify_then_retarget(path, **kwargs):
        state = real_classify(path, **kwargs)
        if not swapped:
            aside = tmp_path / "segments_aside_dry"
            os.rename(str(segments_dir), str(aside))
            os.mkdir(str(segments_dir))
            swapped.append(aside)
        return state

    setattr(backfill, "classify_ever_converged_sentinel", classify_then_retarget)
    try:
        result = backfill.run(args, dirs)
    finally:
        setattr(backfill, "classify_ever_converged_sentinel", real_classify)

    assert swapped, "the test never performed the retarget it exists to model"
    assert result["applied"] is False, "this must be the DRY path"
    assert result["segments_dir_replaced"] is not None, (
        "a dry run whose census read a displaced directory must not report a "
        "clean result -- its missing_sentinels is what the operator acts on"
    )
    assert result["success"] is False


@pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="root ignores the permission bits this test depends on",
)
def test_a_census_that_could_not_read_anything_is_not_a_clean_dry_run(tmp_path):
    """The false CLEAN, and the reason `ambiguous_sentinels` now fails the run.

    `chmod 444 segments` -- readable, not searchable -- needs no attacker: a
    restore tool, a bad `cp -r`, or a mount option produces it. Every lstat
    underneath then fails EACCES, so EVERY segment lands in AMBIGUOUS and
    `missing_sentinels` comes back EMPTY. SKILL.md's upgrade note tells the
    operator that a dry run with an empty `missing_sentinels` means the
    project needs no backfilling, and the five-field checklist that would
    have caught this is explicitly gated on "After --apply" -- which the
    operator following that instruction never reaches. So a census that
    established NOTHING was indistinguishable, at `success`/
    `missing_sentinels`/exit-code level, from a healthy project, and the
    next step after reading it is a W5 dispatch over unprotected work.

    Fails on the pre-fix code at the returncode assertion: it exited 0."""
    root = setup_mixed_project(tmp_path)
    prime_materialized_ledger(root)
    segments_dir = root / "segments"

    os.chmod(segments_dir, 0o444)
    try:
        proc = run_backfill(root)
    finally:
        os.chmod(segments_dir, 0o755)

    payload = parse_stdout(proc)
    # Precondition: this must be the wholesale-ambiguous shape, not some other
    # failure. Without it a chmod that stopped working would leave the
    # returncode assertion below passing for an unrelated reason.
    assert payload["applied"] is False, "this is the DRY path the note names"
    assert [e["seg"] for e in payload["ambiguous_sentinels"]] == sorted(
        payload["ever_converged_segs"]
    ), payload
    assert payload["missing_sentinels"] == [] and payload["already_sentineled"] == [], (
        "the census established nothing -- which is exactly why the empty "
        "missing_sentinels below must not read as 'nothing to do'"
    )

    # The ONLY thing that may be reddening this run is the ambiguous census.
    # Without these, a future change that made some other key fail here would
    # keep the test green while the guard it exists for had been removed.
    assert payload["directory_sync_error"] is None, payload
    assert payload["segments_dir_replaced"] is None, payload
    assert payload["failed_to_create"] == [], payload

    assert proc.returncode == 1, (
        f"a run that verified no segment at all must not exit 0; "
        f"stderr={proc.stderr!r}"
    )
    assert payload["success"] is False


def test_a_segments_path_that_is_a_regular_file_is_refused_at_the_open(tmp_path):
    """A bare `O_RDONLY` open succeeds on a REGULAR FILE, `os.fsync` on it
    succeeds, and `check_segments_dir_identity()` then compares that file
    against itself and agrees -- so every structural check in the script
    agreed with every other one while every sentinel lookup underneath them
    returned ENOTDIR. `O_DIRECTORY` is what makes the open refuse.

    Asserted on the FATAL shape, not merely on `success is False`: without
    `O_DIRECTORY` the run still fails now (every lookup is ambiguous, and
    ambiguous fails the run), so `success` alone cannot tell the two apart.
    A fatal payload carries `error` and has no census buckets at all."""
    root = setup_mixed_project(tmp_path)
    prime_materialized_ledger(root)
    segments_dir = root / "segments"

    shutil.rmtree(segments_dir)
    segments_dir.write_text("not a directory\n", encoding="utf-8")

    proc = run_backfill(root)

    assert proc.returncode == 1, f"stderr={proc.stderr!r}"
    payload = parse_stdout(proc)
    assert payload["success"] is False
    assert "ambiguous_sentinels" not in payload, (
        "this must abort at the open, before any census runs -- a run that "
        "reaches the census has already accepted a file as its directory"
    )
    assert "Not a directory" in payload["error"], payload


def test_the_staging_file_is_created_relative_to_the_descriptor_not_the_path(tmp_path):
    """`tempfile.mkstemp(dir=str(segments_dir))` was the one mutating call in
    the write path still resolving `segments/` by PATHNAME. With the symlink
    re-pointed A->B after the descriptor is open, staging landed in B while
    the link and the cleanup both operated on A: the run failed closed, which
    is correct, and left a file in B that THIS INVOCATION could never remove,
    since `_cleanup_staging()` unlinks relative to the descriptor -- a
    file-creation primitive in a directory of the retargeter's choosing.

    The identity check is not a defence against that: it compares directory
    IDENTITY and never the entries under either directory, so it fires on this
    interleaving (asserted below) and still cannot see, name, or remove what
    was left in B.

    The causal assertion is therefore that B is EMPTY. `success is False`
    holds either way, so it proves nothing here."""
    backfill = _load_module(BACKFILL_SCRIPT_SRC, "backfill_under_test_staging_fd")
    root = setup_mixed_project(tmp_path)
    prime_materialized_ledger(root)

    # segments -> segments_a, with an empty segments_b standing by.
    dir_a = root / "segments_a"
    dir_b = root / "segments_b"
    os.rename(str(root / "segments"), str(dir_a))
    os.mkdir(str(dir_b))
    os.symlink("segments_a", str(root / "segments"))

    real_classify = backfill.classify_ever_converged_sentinel
    census_calls = []
    repointed = []

    def classify_then_repoint(path, **kwargs):
        state = real_classify(path, **kwargs)
        census_calls.append(path)
        # Only once the census has finished, so the writes -- and the staging
        # they need -- are what runs against the re-pointed path. Re-pointing
        # mid-census would change which segments the run tries to write and
        # test a different thing.
        if len(census_calls) == len(EVER_CONVERGED) and not repointed:
            os.unlink(str(root / "segments"))
            os.symlink("segments_b", str(root / "segments"))
            repointed.append(True)
        return state

    setattr(backfill, "classify_ever_converged_sentinel", classify_then_repoint)
    try:
        result = _apply_run(backfill, root)
    finally:
        setattr(backfill, "classify_ever_converged_sentinel", real_classify)

    assert repointed, "the test never performed the re-point it exists to model"
    assert list(dir_b.iterdir()) == [], (
        "nothing this run did may reach the directory the path was re-pointed "
        "at -- a staging file left there is unreachable by the cleanup, which "
        "unlinks relative to the descriptor"
    )
    # And the work really did happen in the directory the descriptor names.
    assert result["created"], "the run must have had writes to do"
    for seg in result["created"]:
        assert (dir_a / f".ever_converged.{seg}").is_file()
    assert result["segments_dir_replaced"] is not None
    assert result["success"] is False


def test_the_umask_is_read_once_per_run_not_once_per_segment(tmp_path):
    """Reading the umask requires SETTING it, and that window is process-wide.

    This module documents in-process callers and every test here is one, so a
    concurrent thread creating a file inside the window gets it mode-0666. The
    window cannot be removed (there is no getumask), but opening it once per
    RUN instead of once per SEGMENT is free. Each read is two os.umask calls
    (set to 0, restore)."""
    backfill = _load_module(BACKFILL_SCRIPT_SRC, "backfill_under_test_umask_count")
    root = setup_mixed_project(tmp_path)
    prime_materialized_ledger(root)

    real_umask = os.umask
    calls = []

    def counting_umask(mask):
        calls.append(mask)
        return real_umask(mask)

    backfill.os.umask = counting_umask
    try:
        result = _apply_run(backfill, root)
    finally:
        backfill.os.umask = real_umask

    assert len(result["created"]) >= 2, (
        f"the fixture must write more than one sentinel, or 'once per run' "
        f"and 'once per segment' are the same number; created="
        f"{result['created']}"
    )
    assert len(calls) == 2, (
        f"exactly one umask read for the whole apply pass; got {len(calls)} "
        f"os.umask calls ({calls})"
    )
    assert result["success"] is True


def test_an_apply_run_with_nothing_to_create_opens_no_umask_window_at_all(tmp_path):
    """The regression the hoist introduced, and the run an operator repeats
    most often: `--apply` over an already fully protected project.

    Before the hoist, zero missing sentinels meant zero calls to the writer
    and therefore zero umask windows. Hoisting the read to the top of the
    apply block opened one on every such run -- fewer windows than the
    per-segment version for a run with work to do, and strictly MORE than
    before for the run that has none. The test above cannot see it: it
    requires at least two creations by construction.

    Fails on the unconditional hoist with 2 os.umask calls instead of 0."""
    backfill = _load_module(BACKFILL_SCRIPT_SRC, "backfill_under_test_umask_noop")
    root = setup_mixed_project(tmp_path)
    prime_materialized_ledger(root)

    first = _apply_run(backfill, root)
    assert first["created"], "setup must raise the sentinels the no-op run will find"

    real_umask = os.umask
    calls = []

    def counting_umask(mask):
        calls.append(mask)
        return real_umask(mask)

    backfill.os.umask = counting_umask
    try:
        second = _apply_run(backfill, root)
    finally:
        backfill.os.umask = real_umask

    assert second["missing_sentinels"] == [] and second["created"] == [], (
        f"the precondition is a run with NOTHING to create; got "
        f"missing={second['missing_sentinels']} created={second['created']}"
    )
    assert calls == [], (
        f"a run that creates nothing must not widen the process-wide umask "
        f"even once; got {len(calls)} os.umask calls ({calls})"
    )
    assert second["success"] is True


def test_an_apply_run_never_labels_a_raced_segment_as_a_dry_run(tmp_path, capsys):
    """A segment missing at census time and created by SOMETHING ELSE before
    this run reaches it is reported "already_present" by the writer and lands
    in no bucket: it stays in `missing_sentinels`, and appears in neither
    `created` nor `failed_to_create`. The per-segment summary line then fell
    through to a label hardcoded "(dry run)" -- printed during an --apply run,
    on the one line an operator reads per segment.

    Fails pre-fix on the label assertion; the payload assertions hold in both
    versions and are here to prove the race really happened."""
    backfill = _load_module(BACKFILL_SCRIPT_SRC, "backfill_under_test_race_label")
    root = setup_mixed_project(tmp_path)
    prime_materialized_ledger(root)

    real_classify = backfill.classify_ever_converged_sentinel
    census_calls = []
    raced = []

    def classify_then_plant(path, **kwargs):
        state = real_classify(path, **kwargs)
        census_calls.append(path)
        # After the census, plant a real sentinel for a segment it just
        # classified ABSENT. The writer's link() then gets EEXIST, re-reads
        # the entry, finds a regular file, and returns "already_present".
        if len(census_calls) == len(EVER_CONVERGED) and not raced:
            for candidate in census_calls:
                if not candidate.exists():
                    candidate.write_bytes(b"converged\n")
                    raced.append(candidate.name.split(".ever_converged.")[-1])
                    break
        return state

    setattr(backfill, "classify_ever_converged_sentinel", classify_then_plant)
    try:
        rc = backfill.main(["--durable-root", str(root), "--apply"])
    finally:
        setattr(backfill, "classify_ever_converged_sentinel", real_classify)

    assert raced, "the test never planted the sentinel it exists to model"
    seg = raced[0]
    captured = capsys.readouterr()
    payload = json.loads(captured.out.strip().splitlines()[-1])

    assert seg in payload["missing_sentinels"], payload
    assert seg not in payload["created"], (
        f"{seg} was created by the plant, not by this run"
    )
    assert seg not in {e["seg"] for e in payload["failed_to_create"]}, payload

    line = [ln for ln in captured.err.splitlines() if ln.strip().startswith(f"- {seg}:")]
    assert len(line) == 1, captured.err
    # The EXACT label, not merely the absence of the word "dry". Asserting an
    # absence would stay green for any other wrong label, including a
    # differently-cased one.
    assert line[0].strip() == (
        f"- {seg}: missing at census -- created by something else during this run"
    ), f"got {line[0]!r}"
    assert rc == 0, captured.err


def test_the_eexist_re_read_asks_the_descriptor_not_the_repointed_pathname(tmp_path):
    """The same false green one call later, in the write path's last
    pathname lookup.

    `link()` publishes through `dir_fd`, so an EEXIST it raises is a
    collision in the directory the DESCRIPTOR names. Re-reading that name by
    pathname asks a DIFFERENT directory once `segments/` has been re-pointed
    -- and that answer decides between "already_present", which
    `mark_ever_converged()` means as "protected, nothing to do", and an
    error. The file used to disclose this lookup as read-only and therefore
    harmless; it cannot place, publish or strand a file, but a read that
    manufactures "protected" out of another directory's file is the exact
    failure this release exists to remove.

    The interleaving, and it is the narrow one: the census finds the name
    ABSENT in A; something plants a DANGLING SYMLINK there before the write
    reaches it -- the entry the predicate exists to refuse, since O_EXCL and
    link() both take EEXIST from one -- and the pathname is re-pointed at a B
    holding a real regular file under that same name. Read by pathname, B
    answers PRESENT and the segment is counted protected while A holds a
    dangling link that no reader will ever resolve. Read through the
    descriptor, A answers AMBIGUOUS and the run refuses.

    The pathname is restored before the run ends, for the same reason as in
    the census test: it keeps `check_segments_dir_identity()` out of the
    verdict, so what is asserted below is this lookup and nothing else."""
    backfill = _load_module(BACKFILL_SCRIPT_SRC, "backfill_under_test_eexist_fd")
    root = setup_mixed_project(tmp_path)
    prime_materialized_ledger(root)

    dir_a = root / "segments_a"
    dir_b = root / "segments_b"
    os.rename(str(root / "segments"), str(dir_a))
    os.mkdir(str(dir_b))
    os.symlink("segments_a", str(root / "segments"))

    # The victim: a segment the census will find genuinely absent in A.
    seg = MISSING_BEFORE_APPLY[0]
    marker = f".ever_converged.{seg}"
    (dir_b / marker).write_bytes(b"converged\n")

    real_classify = backfill.classify_ever_converged_sentinel
    census_calls = []
    planted = []

    def classify_then_plant_and_repoint(path, **kwargs):
        state = real_classify(path, **kwargs)
        census_calls.append(path)
        # After the census, so the segment is already in `missing_sentinels`
        # and the writer will try to create it.
        if len(census_calls) == len(EVER_CONVERGED) and not planted:
            os.symlink("no-such-target", str(dir_a / marker))
            os.unlink(str(root / "segments"))
            os.symlink("segments_b", str(root / "segments"))
            planted.append(True)
        return state

    real_sync = backfill.sync_segments_dir
    restored = []

    def restore_then_sync(fd):
        # The directory fsync is the first thing after the write loop, so
        # this restores the pathname once every link has been attempted and
        # before the identity sample runs.
        if not restored:
            os.unlink(str(root / "segments"))
            os.symlink("segments_a", str(root / "segments"))
            restored.append(True)
        return real_sync(fd)

    setattr(backfill, "classify_ever_converged_sentinel", classify_then_plant_and_repoint)
    setattr(backfill, "sync_segments_dir", restore_then_sync)
    try:
        result = _apply_run(backfill, root)
    finally:
        setattr(backfill, "classify_ever_converged_sentinel", real_classify)
        setattr(backfill, "sync_segments_dir", real_sync)

    assert planted and restored, (
        "the test never performed the plant-and-repoint it exists to model"
    )
    assert stat.S_ISLNK(os.lstat(str(dir_a / marker)).st_mode), (
        "the planted entry must still be the dangling link -- the writer must "
        "never delete or replace an entry it did not create"
    )

    # THE causal assertion: the segment is REFUSED, not laundered into
    # "already_present" by a regular file sitting in another directory.
    failed = {entry["seg"]: entry["error"] for entry in result["failed_to_create"]}
    assert seg in failed, (
        f"the entry that raised EEXIST is a dangling symlink in the directory "
        f"the descriptor names, so this segment's protection is UNPROVEN and "
        f"the run must say so; got failed_to_create={result['failed_to_create']} "
        f"created={result['created']}"
    )
    assert "refusing to treat this as an existing sentinel" in failed[seg], (
        f"got {failed[seg]!r}"
    )
    assert seg not in result["created"]
    # Not caught by the identity check, by construction -- see the docstring.
    assert result["segments_dir_replaced"] is None, (
        f"the pathname names A again by the end, so the identity sample must "
        f"agree; got {result['segments_dir_replaced']!r}"
    )
    assert result["directory_sync_error"] is None
    assert result["success"] is False

    # The other missing segment was unaffected and really did get protected.
    other = MISSING_BEFORE_APPLY[1]
    assert result["created"] == [other], result["created"]
    assert (dir_a / f".ever_converged.{other}").is_file()
    # And nothing this run did reached the decoy directory.
    assert sorted(p.name for p in dir_b.iterdir()) == [marker], (
        "no staging file, no link, nothing may have landed in the directory "
        "the pathname was briefly re-pointed at"
    )


def test_a_dry_run_treats_an_invalid_utf8_ledger_as_missing_not_as_a_crash(tmp_path):
    """`UnicodeDecodeError` is a `ValueError`, so neither `OSError` nor
    `json.JSONDecodeError` caught it and it escaped `read_existing_ledger()`
    -- whose own docstring promises it never raises and treats "missing,
    unreadable, not valid JSON" identically as "nothing usable yet".

    The operator-visible difference: instead of the refusal that names
    `--allow-merge` as the next step, they got main()'s defensive catch-all
    ("unexpected error: 'utf-8' codec can't decode byte ..."), the payload
    shape reserved for a bug in this script. Fail-closed, wrong message.

    The pre-existing corrupt-ledger test could not catch this: it writes
    invalid JSON, which is a different exception on a different line."""
    root = setup_mixed_project(tmp_path)
    ledger = root / "runs" / "ledger.json"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    # Valid JSON structurally, but the bytes are not UTF-8.
    ledger.write_bytes(b'{"segments": {"seg\xed\xa0\x80": {}}}')

    proc = run_backfill(root)

    assert proc.returncode == 1, f"stderr={proc.stderr!r}"
    payload = parse_stdout(proc)
    assert payload["success"] is False
    assert "unexpected error" not in payload["error"], (
        f"a malformed input file must not be reported as an internal bug; "
        f"got {payload['error']!r}"
    )
    assert "--allow-merge" in payload["error"], (
        f"an unusable existing ledger must produce the refusal that names the "
        f"next step, exactly as a missing or corrupt-JSON one does; got "
        f"{payload['error']!r}"
    )


def test_read_json_reports_invalid_utf8_as_a_fatal_not_an_unexpected_error(tmp_path):
    """The other reader. `read_json()` is only ever called on a ledger this
    script just merged, so a fatal is right -- but it must be THIS script's
    fatal, with a message naming the file, not the catch-all that says the
    script itself misbehaved."""
    backfill = _load_module(BACKFILL_SCRIPT_SRC, "backfill_under_test_read_json_utf8")
    bad = tmp_path / "ledger.json"
    bad.write_bytes(b'{"segments": {"a\xed\xa0\x80": {}}}')

    with pytest.raises(backfill.FatalError) as excinfo:
        backfill.read_json(bad, "materialized ledger.json")

    payload = json.loads(str(excinfo.value))
    assert payload["success"] is False
    assert "not valid UTF-8" in payload["error"], payload
    assert str(bad) in payload["error"], payload


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))


# ---------------------------------------------------------------------------
# #443. The marker carries its own justification now, and this script is the
# only thing that reads it. These pin BOTH halves: what the writer records,
# and that the reader it feeds decides nothing.
# ---------------------------------------------------------------------------

def test_the_backfill_writer_records_the_ledger_row_it_retrofitted_from(tmp_path):
    """What THIS script can honestly claim is weaker than what
    ledger_update.py claims, and the asymmetry is the whole point: a backfill
    never observed a convergence, so it records no run token and no round
    label -- only the ledger row that put the segment in scope.

    Fails on the pre-#443 writer at the json.loads(): the body was the ten
    bytes `converged\n`, identical to the one a real convergence wrote, which
    is the defect #443 names."""
    root = setup_mixed_project(tmp_path)

    proc = run_backfill(root, "--apply")

    assert proc.returncode == 0, f"stderr={proc.stderr!r}"
    body = assert_marker_written_by(
        sentinel_path(root, "seg_conv_match"), "backfill_ever_converged", "seg_conv_match"
    )
    assert body["ledger_status"] == "converged", body
    assert body["ledger_source"] in ("existing", "freshly_merged"), body
    assert body["reviewed_draft_sha1"] == "0" * 40, (
        f"the row's own reviewed_draft_sha1 must be carried across, not "
        f"invented: {body!r}"
    )
    assert "run_token" not in body and "round" not in body, (
        f"a backfill has neither, and recording one would be exactly the "
        f"empty assertion this issue is about: {body!r}"
    )


def test_the_census_reports_who_published_each_already_present_marker(tmp_path):
    """The report #443 exists to make possible. Before it, an operator asking
    "was this marker earned or asserted?" had nothing on disk to read and fell
    back to separating the two by sentinel MTIME at microsecond resolution.

    Every value in the map is exercised by a marker the test actually put
    there, including the legacy body every live project carries today."""
    root = setup_mixed_project(tmp_path)
    prime_materialized_ledger(root)
    writer = _load_module(LEDGER_UPDATE_SRC, "ledger_update_attrib")

    # seg_conv_presentinel already carries PRESENTINEL_CONTENT -> unattributed.
    # Give the other two markers a real writer each, so nothing is created by
    # this run and the census reports on three pre-existing markers.
    writer.mark_ever_converged("seg_conv_match", root / "segments", {"run_token": "R1"})
    call_mark(
        _load_module(BACKFILL_SCRIPT_SRC, "backfill_attrib"),
        "seg_conv_mismatch",
        root / "segments",
    )

    payload = parse_stdout(run_backfill(root))

    assert payload["already_sentineled"] == EVER_CONVERGED
    assert payload["sentinel_attribution"] == {
        "seg_conv_match": "ledger_update",
        "seg_conv_mismatch": "backfill_ever_converged",
        "seg_conv_presentinel": "unattributed",
    }, payload["sentinel_attribution"]


def test_attribution_moves_no_bucket_no_count_and_not_the_exit_status(tmp_path):
    """THE PIN THAT KEEPS #443 SAFE. Provenance is a DIAGNOSTIC: an
    unattributed marker must protect its segment exactly as much as an
    attributed one, or every marker written before this change -- all of them,
    on every project -- becomes unprotected the day it ships.

    Compares a run over three provenance-free markers against the same run
    over the same project with real provenance in place, and requires every
    decision-bearing field to be identical."""
    def run_over(root):
        payload = parse_stdout(run_backfill(root))
        return {k: payload[k] for k in (
            "success", "already_sentineled", "missing_sentinels",
            "ambiguous_sentinels", "counts", "not_evaluated",
        )}

    legacy_root = setup_mixed_project(tmp_path / "legacy")
    prime_materialized_ledger(legacy_root)
    for seg in ("seg_conv_match", "seg_conv_mismatch"):
        sentinel_path(legacy_root, seg).write_bytes(b"converged\n")

    attributed_root = setup_mixed_project(tmp_path / "attributed")
    prime_materialized_ledger(attributed_root)
    writer = _load_module(LEDGER_UPDATE_SRC, "ledger_update_parity")
    for seg in ("seg_conv_match", "seg_conv_mismatch"):
        writer.mark_ever_converged(seg, attributed_root / "segments", {"run_token": "R1"})

    assert run_over(legacy_root) == run_over(attributed_root), (
        "a marker's provenance changed a bucket, a count or the run's verdict "
        "-- #443 must not be able to reclassify anything"
    )


HOSTILE_BODIES = [
    ("legacy ten bytes", b"converged\n", "unattributed"),
    ("empty file", b"", "unattributed"),
    ("torn JSON", b'{"marker":"ever_conv', "unattributed"),
    ("invalid UTF-8", b"\xff\xfe\x00", "unattributed"),
    ("JSON scalar", b'"converged"\n', "unattributed"),
    ("JSON list", b'["ever_converged"]\n', "unattributed"),
    ("object, wrong marker", b'{"marker":"resume_gate_ack","by":"x"}\n', "unattributed"),
    ("object, no by", b'{"marker":"ever_converged","v":1}\n', "unattributed"),
    ("object, non-string by", b'{"marker":"ever_converged","by":7}\n', "unattributed"),
    ("object, empty by", b'{"marker":"ever_converged","by":""}\n', "unattributed"),
]


@pytest.mark.parametrize("label,raw,expected", HOSTILE_BODIES,
                         ids=[case[0] for case in HOSTILE_BODIES])
def test_the_body_reader_answers_rather_than_raising(tmp_path, label, raw, expected):
    """The marker's body is the one thing at this path an operator or an
    unrelated tool can author, and this reader is the plugin's only consumer
    of it. Every malformed shape must produce an ANSWER -- never an exception,
    never a halt -- because a diagnostic that can crash the census would make
    a repair tool refuse to run over exactly the projects that need it."""
    backfill = _load_module(BACKFILL_SCRIPT_SRC, f"backfill_hostile_{abs(hash(label))}")
    segments_dir = tmp_path / "segments"
    segments_dir.mkdir()
    path = segments_dir / ".ever_converged.segH"
    path.write_bytes(raw)

    assert backfill.read_sentinel_attribution(path) == expected, label


def test_the_body_reader_stops_at_its_cap_and_reports_an_unreadable_entry(tmp_path):
    """Two ends of the same contract.

    The CAP: a body is well under 300 bytes, and the reader must not let a
    file at this path decide how much it reads. A megabyte of JSON that would
    parse perfectly is truncated and therefore unattributed -- the safe
    answer, since the reader vouches for nothing it did not fully read.

    UNREADABLE is kept separate from unattributed on purpose: "I could not
    look" and "I looked and found no provenance" are different facts for an
    operator, and folding them is how a diagnostic starts lying."""
    backfill = _load_module(BACKFILL_SCRIPT_SRC, "backfill_cap")
    segments_dir = tmp_path / "segments"
    segments_dir.mkdir()

    huge = segments_dir / ".ever_converged.segBig"
    padding = "x" * (backfill.SENTINEL_BODY_READ_CAP * 4)
    huge.write_text(
        json.dumps({"marker": "ever_converged", "by": "ledger_update", "pad": padding}),
        encoding="utf-8",
    )
    assert backfill.read_sentinel_attribution(huge) == "unattributed"

    missing = segments_dir / ".ever_converged.segGone"
    assert backfill.read_sentinel_attribution(missing) == "unreadable"

    a_dir = segments_dir / ".ever_converged.segDir"
    a_dir.mkdir()
    assert backfill.read_sentinel_attribution(a_dir) in ("unreadable", "unattributed")


def test_the_body_reader_is_strict_about_the_shape_it_will_vouch_for(tmp_path):
    """The body is UNTRUSTED: anyone who can write the marker can write any
    `by` they like. So attribution is granted only to a body that matches what
    sentinel_body() actually emits, and the answer comes from a CLOSED set --
    echoing an arbitrary `by` back would put a value outside this script's own
    documented output contract into its JSON and dress a foreign file up as
    provenance.

    The `expected_seg` arm is the one with teeth beyond tidiness: a marker
    copied from another segment is real, well-formed, plugin-written
    provenance -- for a DIFFERENT segment -- and reporting it as this one's is
    precisely the empty authority #443 is about."""
    backfill = _load_module(BACKFILL_SCRIPT_SRC, "backfill_strict")
    segments_dir = tmp_path / "segments"
    segments_dir.mkdir()
    path = segments_dir / ".ever_converged.segS"

    def attribution(body, **kw):
        path.write_text(json.dumps(body), encoding="utf-8")
        return backfill.read_sentinel_attribution(path, **kw)

    good = {"marker": "ever_converged", "v": 1, "by": "ledger_update", "seg": "segS"}
    assert attribution(good) == "ledger_update"
    assert attribution(good, expected_seg="segS") == "ledger_update"

    assert attribution({**good, "by": "some_other_tool"}) == "unattributed"
    assert attribution({**good, "v": 2}) == "unattributed"
    assert attribution({k: v for k, v in good.items() if k != "v"}) == "unattributed"
    assert attribution({k: v for k, v in good.items() if k != "seg"}) == "unattributed"
    assert attribution({**good, "seg": ""}) == "unattributed"
    assert attribution({**good, "seg": 7}) == "unattributed"
    assert attribution(good, expected_seg="a_different_segment") == "unattributed"


def test_the_body_reader_answers_even_when_json_raises_a_non_value_error(tmp_path):
    """`json.loads()` raises RecursionError -- which is NOT a ValueError -- on
    deeply nested input, and the reader's contract is that it never raises.

    Reproduced deterministically by making the parse raise, rather than by
    nesting brackets: measured, a body deep enough to trip CPython's own
    recursion does NOT fit under the 4096-byte cap on this project's 3.14.7,
    where the C scanner does not recurse in Python frames -- so a nesting
    fixture would pass here whether or not the arm exists, on the very
    interpreter CI runs. This plugin's floor is 3.10, where it does raise."""
    backfill = _load_module(BACKFILL_SCRIPT_SRC, "backfill_recursion")
    segments_dir = tmp_path / "segments"
    segments_dir.mkdir()
    path = segments_dir / ".ever_converged.segR"
    path.write_bytes(b'{"marker":"ever_converged"}\n')

    real_loads = backfill.json.loads

    def raising_loads(*args, **kwargs):
        raise RecursionError("maximum recursion depth exceeded")

    backfill.json.loads = raising_loads
    try:
        assert backfill.read_sentinel_attribution(path) == "unattributed"
    finally:
        backfill.json.loads = real_loads


def test_the_body_reader_is_safe_on_both_branches_a_symlink_and_a_fifo(tmp_path):
    """The census reads relative to the run's directory descriptor; every test
    above exercises the PATHNAME branch, which nothing in production uses. Both
    are pinned here, over the two entries that make the open flags load-bearing.

    A symlink at the marker name must not be followed -- an entry the writers
    never publish, and following one reads a file outside `segments/` and
    attributes it to this segment. A FIFO must not BLOCK: without O_NONBLOCK
    the open waits forever for a writer, and the repair tool hangs on exactly
    the project that needs it. Neither is reachable from the census today,
    because classify_ever_converged_sentinel() has already rejected both as
    AMBIGUOUS -- but that ordering is the caller's, not this function's, and a
    reader whose safety depends on its caller having checked first is one
    refactor from being unsafe."""
    backfill = _load_module(BACKFILL_SCRIPT_SRC, "backfill_branches")
    segments_dir = tmp_path / "segments"
    segments_dir.mkdir()
    dir_fd = os.open(str(segments_dir), os.O_RDONLY | os.O_DIRECTORY)
    try:
        target = tmp_path / "elsewhere.json"
        target.write_text(
            json.dumps({"marker": "ever_converged", "v": 1,
                        "by": "ledger_update", "seg": "segL"}),
            encoding="utf-8",
        )
        link = segments_dir / ".ever_converged.segL"
        link.symlink_to(target)
        assert backfill.read_sentinel_attribution(link) == "unreadable"
        assert backfill.read_sentinel_attribution(link, dir_fd=dir_fd) == "unreadable"

        fifo = segments_dir / ".ever_converged.segF"
        os.mkfifo(fifo)
        # No reader/writer is ever attached: without O_NONBLOCK these two
        # calls never return, so the test hanging IS the failure signal.
        assert backfill.read_sentinel_attribution(fifo) in ("unreadable", "unattributed")
        assert backfill.read_sentinel_attribution(
            fifo, dir_fd=dir_fd
        ) in ("unreadable", "unattributed")

        real = segments_dir / ".ever_converged.segOK"
        real.write_text(
            json.dumps({"marker": "ever_converged", "v": 1,
                        "by": "backfill_ever_converged", "seg": "segOK"}),
            encoding="utf-8",
        )
        assert backfill.read_sentinel_attribution(
            real, dir_fd=dir_fd, expected_seg="segOK"
        ) == "backfill_ever_converged", (
            "the descriptor branch is the one production uses; a positive "
            "control keeps the two assertions above from passing because the "
            "branch refuses everything"
        )
    finally:
        os.close(dir_fd)


def test_an_oversized_body_is_refused_rather_than_judged_on_its_prefix(tmp_path):
    """The cap alone was not an overflow check. Reading exactly the maximum
    cannot tell a body that ENDS at the limit from one that runs past it, so a
    file whose first bytes parse as a valid attributed marker -- with anything
    at all after them -- was reported as a known writer's.

    The fixture is deliberately the hostile shape rather than a truncated
    string: valid, complete, attributed JSON that fits inside the maximum,
    followed by trailing bytes. On the pre-fix reader this returned
    `ledger_update`."""
    backfill = _load_module(BACKFILL_SCRIPT_SRC, "backfill_overflow")
    segments_dir = tmp_path / "segments"
    segments_dir.mkdir()
    path = segments_dir / ".ever_converged.segO"

    body = json.dumps({"marker": "ever_converged", "v": 1,
                       "by": "ledger_update", "seg": "segO"}).encode("utf-8")
    padding = b" " * (backfill.SENTINEL_BODY_MAX_BYTES - len(body))
    assert len(body + padding) == backfill.SENTINEL_BODY_MAX_BYTES

    path.write_bytes(body + padding)
    assert backfill.read_sentinel_attribution(path) == "ledger_update", (
        "a body exactly AT the maximum must still be read -- the check is an "
        "overflow detector, not an off-by-one that rejects the largest legal "
        "marker"
    )

    path.write_bytes(body + padding + b"X")
    assert backfill.read_sentinel_attribution(path) == "unattributed"

    # The case the length check exists for, and the one a trailing-garbage
    # fixture does NOT reach: a body that is one byte over the maximum and is
    # still perfectly valid attributed JSON. The parse cannot refuse it -- only
    # the length can -- so without the explicit overflow test the reader
    # attributes a body no writer can emit. Built by padding INSIDE a JSON
    # string so the result stays parseable at exactly CAP+1 bytes.
    over = backfill.SENTINEL_BODY_MAX_BYTES + 1
    stub = json.dumps({"marker": "ever_converged", "v": 1, "by": "ledger_update",
                       "seg": "segO", "pad": ""}).encode("utf-8")
    exact = json.dumps({"marker": "ever_converged", "v": 1, "by": "ledger_update",
                        "seg": "segO", "pad": "p" * (over - len(stub))}).encode("utf-8")
    assert len(exact) == over, len(exact)
    assert json.loads(exact.decode("utf-8"))["by"] == "ledger_update", (
        "precondition: the oversized body must be VALID JSON, or the parse "
        "refuses it and the length check is never the thing under test"
    )
    path.write_bytes(exact)
    assert backfill.read_sentinel_attribution(path) == "unattributed", (
        "a body one byte past what any writer publishes was attributed to a "
        "known writer on the strength of parsing cleanly"
    )


VERSION_CASES = [
    ("a real integer", 1, "ledger_update"),
    # bool is a SUBCLASS of int and True == 1, so an equality-only check
    # accepts this; neither writer can emit it.
    ("JSON true", True, "unattributed"),
    ("a float that compares equal", 1.0, "unattributed"),
    ("the version as a string", "1", "unattributed"),
    ("a future version", 2, "unattributed"),
]


@pytest.mark.parametrize("label,version,expected", VERSION_CASES,
                         ids=[case[0] for case in VERSION_CASES])
def test_the_version_must_be_the_integer_the_writers_emit(tmp_path, label, version, expected):
    """The strict-shape check pins what sentinel_body() ACTUALLY emits, and
    Python's numeric equality is looser than that: `True == 1` and `1.0 == 1`
    both hold. A foreign body carrying either was attributed to a known
    writer."""
    backfill = _load_module(BACKFILL_SCRIPT_SRC, f"backfill_v_{abs(hash(label))}")
    segments_dir = tmp_path / "segments"
    segments_dir.mkdir()
    path = segments_dir / ".ever_converged.segV"
    path.write_text(
        json.dumps({"marker": "ever_converged", "v": version,
                    "by": "ledger_update", "seg": "segV"}),
        encoding="utf-8",
    )
    assert backfill.read_sentinel_attribution(path) == expected, label


def test_a_writer_can_never_publish_a_body_its_own_reader_would_refuse(tmp_path):
    """THE WRITER/READER ROUND TRIP, and the seam #443 could have failed at
    silently. `run_token` is a free-form string with no length constraint
    anywhere in the payload schema, and it is copied into the marker -- so an
    unbounded evidence field produced a marker that was GENUINELY EARNED and
    read back `unattributed`, defeating the feature on exactly the markers it
    exists for. No hostile actor is needed; a long run id suffices.

    sentinel_body() answers by dropping the evidence rather than truncating
    it: a truncated JSON body is not shorter evidence, it is unparseable
    evidence. The marker still says who wrote it, which is the half worth
    keeping. Both writers are checked, because the bound lives in the shared
    function and a divergence there is exactly what the source pin exists for."""
    writer = _load_module(LEDGER_UPDATE_SRC, "ledger_update_roundtrip")
    backfill = _load_module(BACKFILL_SCRIPT_SRC, "backfill_roundtrip")
    segments_dir = tmp_path / "segments"
    segments_dir.mkdir()

    huge = "R" * (backfill.SENTINEL_BODY_MAX_BYTES * 3)
    for module, name, seg in (
        (writer, "ledger_update", "segRT1"),
        (backfill, "backfill_ever_converged", "segRT2"),
    ):
        body = module.sentinel_body(seg, name, {
            "run_token": huge, "reviewed_draft_sha1": huge, "ledger_status": huge,
        })
        assert len(body) <= backfill.SENTINEL_BODY_MAX_BYTES, (
            f"{name} published {len(body)} bytes, past the maximum its own "
            f"reader accepts"
        )
        path = segments_dir / f".ever_converged.{seg}"
        path.write_bytes(body)
        assert backfill.read_sentinel_attribution(
            path, expected_seg=seg
        ) == name, (
            "an earned marker read back as unattributed -- the writer emitted "
            "a body its reader refuses, which is #443 defeated on the markers "
            "it exists for"
        )
        assert "run_token" not in json.loads(body.decode("utf-8")), (
            "the oversized evidence must be DROPPED, not truncated into "
            "something unparseable"
        )


def test_the_body_read_survives_a_short_os_read(tmp_path):
    """A single `os.read()` on a regular file normally returns the whole
    request, but it is NOT guaranteed to -- an interruption or a filesystem
    implementation may hand back a short count. A short read that happened to
    land on a complete JSON object is indistinguishable from EOF, so an
    OVERSIZED body would be judged on its prefix and attributed to a known
    writer: exactly what the read-one-past-the-maximum check exists to stop.

    Forced deterministically by chunking `os.read`, because no ordinary file
    on this host reproduces it -- a real-file fixture would pass whether or
    not the loop exists, on the very interpreter and filesystem CI runs.

    Two arms, and the second is the one with teeth. An in-bounds body split
    across chunks must still be read whole; an over-long body must still be
    caught even though its first chunk parses cleanly on its own."""
    backfill = _load_module(BACKFILL_SCRIPT_SRC, "backfill_shortread")
    segments_dir = tmp_path / "segments"
    segments_dir.mkdir()
    path = segments_dir / ".ever_converged.segSR"

    real_read = backfill.os.read

    def chunked_read(fd, count, _chunk=[64]):
        return real_read(fd, min(count, _chunk[0]))

    good = json.dumps({"marker": "ever_converged", "v": 1,
                       "by": "ledger_update", "seg": "segSR",
                       "pad": "p" * 500}).encode("utf-8")
    assert len(good) > 64, "precondition: the body must span several chunks"
    path.write_bytes(good)

    backfill.os.read = chunked_read
    try:
        assert backfill.read_sentinel_attribution(path) == "ledger_update", (
            "an in-bounds body split across short reads must still be read "
            "whole -- a single os.read() would have parsed 64 bytes and "
            "reported it unattributed"
        )

        stub = json.dumps({"marker": "ever_converged", "v": 1,
                           "by": "ledger_update", "seg": "segSR",
                           "pad": ""}).encode("utf-8")
        over = backfill.SENTINEL_BODY_MAX_BYTES + 1
        oversized = json.dumps({"marker": "ever_converged", "v": 1,
                                "by": "ledger_update", "seg": "segSR",
                                "pad": "p" * (over - len(stub))}).encode("utf-8")
        assert len(oversized) == over
        path.write_bytes(oversized)
        assert backfill.read_sentinel_attribution(path) == "unattributed", (
            "a body past the maximum was attributed because the read stopped "
            "short of proving it was over -- the overflow check is only as "
            "good as the read that feeds it"
        )
    finally:
        backfill.os.read = real_read


# ---------------------------------------------------------------------------
# #621 -- the project lease. `--apply` must not be able to raise a sentinel
# inside a driver's census-to-dispatch window. The exclusion is
# runs/.driver.lock, the same lease segment_dispatch_driver.py holds across
# its WHOLE run (acquired before its select_segments.py Step 1 call, released
# only after the dispatch loop), so a backfill that cannot start while it is
# held cannot land a marker in the middle.
#
# A note on why the lifetime tests below run IN-PROCESS. flock is scoped per
# OPEN FILE DESCRIPTION, not per process, so a second independent os.open() of
# the same path contends for real against a lease this very process holds and
# can never self-deadlock -- select_segments.py:_independent_lock_attempt()
# documents the same property, measured (BlockingIOError, errno 35). That is
# what lets a test assert "held right now, at this instant of the run". The
# subprocess harness cannot: process exit releases a kernel flock whether or
# not release_project_lease() was ever called, so a subprocess "the lock is
# free afterwards" assertion is vacuous as a release test.
# ---------------------------------------------------------------------------


def _lock_path(root):
    return root / "runs" / ".driver.lock"


@contextlib.contextmanager
def _external_lease_held(root):
    """Holds runs/.driver.lock exactly as segment_dispatch_driver.py does --
    plain O_CREAT|O_RDWR then LOCK_EX|LOCK_NB -- for the duration of the
    block. Stands in for a driver run being in flight."""
    path = _lock_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield path
    finally:
        os.close(fd)


def _independent_acquire_succeeds(root):
    """True iff a genuinely independent open of runs/.driver.lock can take
    LOCK_EX|LOCK_NB right now. Never leaves a lease held.

    Only the CONTENTION errnos count as "no, something holds it". Mapping an
    arbitrary OSError to False would manufacture exactly the evidence this
    helper exists to gather -- an ENOLCK or EMFILE would read as proof that
    the lease is held, and the lifetime assertion below would pass without
    ever having observed a lease. Same discrimination the production probe
    makes, and the same one select_segments.py measured the cost of omitting.
    """
    path = _lock_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_CREAT | os.O_RDWR, 0o600)
    # try/except/else/finally, the same shape the production probe uses for
    # this identical primitive -- all three exits still pass through `finally`,
    # and the helper stops reading as a different idiom for the same operation.
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        if exc.errno in (errno.EAGAIN, errno.EWOULDBLOCK, errno.EACCES):
            return False
        raise AssertionError(
            f"the lease probe could not be RUN ({exc}) -- this test "
            f"proves nothing, and reporting it as 'held' would be the "
            f"false green it exists to catch"
        ) from exc
    else:
        fcntl.flock(fd, fcntl.LOCK_UN)
        return True
    finally:
        os.close(fd)


def test_apply_refuses_while_the_project_lease_is_held_and_writes_nothing(tmp_path):
    """The refusal itself, and -- the half that actually matters -- that it
    happens BEFORE any sentinel is written. An implementation that acquired
    the lease after the census and the create loop would still print this
    refusal while having already done the damage."""
    root = setup_mixed_project(tmp_path)
    prime_materialized_ledger(root)

    with _external_lease_held(root) as lock_path:
        # The WHOLE tree, not just the sentinel names. Asserting only on
        # sentinels left a third mutation alive (named by review): move the
        # acquire out of the wrapper to just before the sentinel loop, and
        # this test still passes while an --apply run re-materializes
        # runs/ledger.json inside a driver's lease -- contradicting this
        # test's own name, and the docstring's "holds it for this run's
        # lifetime". `.driver.lock` itself does not show up as a change, and
        # the reason is "present in both snapshots", not an exclusion rule
        # this helper implements: the fixture creates it BEFORE the first
        # snapshot, and a refused run leaves it byte-identical (an O_CREAT
        # over an existing file moves neither mtime nor size). It is the
        # lease being HELD, not a write by the run under test.
        before = _snapshot_tree(root)
        proc = run_backfill(root, "--apply")
        after = _snapshot_tree(root)

    assert proc.returncode != 0, (
        "--apply must refuse while another project-lease participant holds "
        f"runs/.driver.lock\nstdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
    payload = parse_stdout(proc)
    assert payload["success"] is False
    assert str(lock_path) in payload["error"], (
        "the refusal must name the path an operator has to go look at, not "
        f"merely say something is locked: {payload['error']!r}"
    )
    diff = _tree_diff(before, after)
    assert diff == {"added": [], "removed": [], "changed": []}, (
        "a refused run must have written NOTHING anywhere under the durable "
        "root -- not a sentinel, and not a re-materialized runs/ledger.json. "
        "The lease has to be taken at the TOP of the run, not around the "
        f"create loop: {diff}"
    )


def test_the_lease_is_held_across_every_sentinel_write_and_released_after(tmp_path):
    """THE LIFETIME TEST, and the reason the other three are not enough.

    Codex named the mutation that survives all of them: acquire the lease,
    stamp the holder JSON, then release it immediately before delegating to
    _run_holding_lease(). The refusal test still refuses (an external holder
    still blocks the acquire), the dry-run carve-out is untouched, the holder
    body is still written -- and the window #621 is about is wide open again,
    because a driver can take the lease after that premature release, census
    an absent sentinel, and have this run write it before dispatch.

    So the assertion is not "a lease was taken once". It is that at the
    instant of EVERY sentinel write, an independent LOCK_EX|LOCK_NB against
    runs/.driver.lock is REFUSED -- i.e. the lease is genuinely held right
    then -- and that it is genuinely released once run() returns.

    `writes` is asserted non-zero: a wrapper that never fires prints exactly
    what a passing one prints."""
    backfill = _load_module(BACKFILL_SCRIPT_SRC, "backfill_under_test_lease_lifetime")
    root = setup_mixed_project(tmp_path)

    real_mark = backfill.mark_ever_converged
    observations = []

    def mark_observing_the_lease(*a, **kw):
        observations.append(_independent_acquire_succeeds(root))
        return real_mark(*a, **kw)

    backfill.mark_ever_converged = mark_observing_the_lease
    try:
        result = _apply_run(backfill, root)
    finally:
        backfill.mark_ever_converged = real_mark

    assert result["created"], "the fixture must actually create sentinels"
    assert len(observations) >= 1, (
        "the wrapper never fired -- this test proved nothing, and a "
        "zero-iteration probe is indistinguishable from a passing one"
    )
    assert not any(observations), (
        f"the project lease must be HELD at every sentinel write; an "
        f"independent LOCK_EX|LOCK_NB succeeded on "
        f"{observations.count(True)} of {len(observations)} writes, which "
        f"means the lease was released (or never taken) while this run was "
        f"still writing markers a driver's census could race"
    )
    assert _independent_acquire_succeeds(root), (
        "run() must RELEASE the lease when it returns -- this module has "
        "in-process callers by design, and a leaked descriptor would hold "
        "the project lease for the life of the interpreter"
    )


def test_a_dry_run_is_not_refused_by_a_held_lease_and_still_writes_nothing(tmp_path):
    """The carve-out, pinned so that widening or narrowing it later is a
    visible edit rather than a silent one. A dry run creates no sentinel, so
    it cannot cause #621's interleaving at all; locking it would create
    runs/.driver.lock, which is itself a filesystem modification and would
    break the write-nothing guarantee asserted above."""
    root = setup_mixed_project(tmp_path)
    prime_materialized_ledger(root)

    with _external_lease_held(root):
        before = _snapshot_tree(root)
        proc = run_backfill(root)
        after = _snapshot_tree(root)

    assert proc.returncode == 0, (
        "a dry run must not be refused by a held project lease\n"
        f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
    payload = parse_stdout(proc)
    assert payload["success"] is True
    assert payload["ever_converged_segs"] == EVER_CONVERGED
    assert _tree_diff(before, after) == {"added": [], "removed": [], "changed": []}, (
        "a dry run must still make ZERO filesystem modifications -- taking a "
        "lease here would create runs/.driver.lock and break exactly that"
    )


def test_the_lease_file_names_this_script_as_its_holder(tmp_path):
    """The only channel that tells a human WHICH participant holds the lease.
    segment_dispatch_driver.py's own refusal says "another driver" and is not
    edited here (it is a PLUGIN_BUNDLE_MEMBERS entry, so touching it would
    move plugin_bundle_hash), and select_segments.py's --from-stalled lease
    stamps no body at all."""
    root = setup_mixed_project(tmp_path)

    proc = run_backfill(root, "--apply")

    assert proc.returncode == 0, f"stderr={proc.stderr!r}"
    body = _lock_path(root).read_text(encoding="utf-8").strip()
    assert body, "a successful --apply must leave a diagnostic body behind"
    record = json.loads(body)
    assert record["holder"] == "backfill_ever_converged.py", (
        f"the lease body must name its holder so a refusal elsewhere can be "
        f"diagnosed without guessing: {record!r}"
    )
    assert isinstance(record["pid"], int), (
        f"the body must carry an integer pid, the driver's own shape: {record!r}"
    )


def test_a_filesystem_that_cannot_flock_at_all_warns_instead_of_claiming_a_holder(
    tmp_path, capsys
):
    """A FAILED acquire is not a HELD lease, and the difference decides
    whether the one-time migration can run at all.

    Caught by review after the self-test had already been fixed for the
    identical confusion -- the same misreading shipped twice in one function.
    With ENOLCK injected on the FIRST flock, the earlier version exited 1
    saying "another project-lease participant already holds" it, on a mount
    with no driver anywhere near it, permanently blocking the migration on
    exactly the filesystems the degraded-mode policy exists to keep working.

    ENOLCK, not a generic OSError: the point is the errno classification, and
    a test that injected EAGAIN would assert the refusal path instead. The
    contention direction stays covered by the refusal test above, so the two
    errnos are pinned against each other rather than one being trusted."""
    backfill = _load_module(BACKFILL_SCRIPT_SRC, "backfill_under_test_no_flock")
    root = setup_mixed_project(tmp_path)

    real_flock = backfill.fcntl.flock

    def flock_unsupported(fd, op):
        raise OSError(errno.ENOLCK, "No locks available")

    backfill.fcntl.flock = flock_unsupported
    try:
        result = _apply_run(backfill, root)
    finally:
        backfill.fcntl.flock = real_flock

    assert result["success"] is True, (
        "a filesystem that cannot lock must not block the migration -- the "
        f"documented policy is warn-and-proceed: {result!r}"
    )
    assert result["created"], (
        "the run must still have done its actual work; a degraded lease is "
        "not a reason to protect nothing"
    )

    err = capsys.readouterr().err
    assert "flock could not be performed" in err, (
        f"the operator must be told no lease is held, in the run they are "
        f"watching: {err!r}"
    )
    assert "No locks available" in err, (
        "the warning must carry the errno text, or the operator cannot tell "
        "an unlockable mount from any other degraded state"
    )
    assert "already holds" not in err, (
        "the old bug: an unlockable filesystem reported as another "
        "participant holding the lease, which is a holder that does not exist"
    )


def test_a_mistyped_durable_root_is_named_and_nothing_is_created(tmp_path):
    """A regression the LEASE introduced, caught by review after it merged.

    Taking the lease moved to the top of the run, ahead of everything that
    used to touch the filesystem first, which made its
    `mkdir(parents=True, exist_ok=True)` the first thing a mistyped
    --durable-root reached. Measured on the pre-lease script and the lease
    version with identical argv: the old one wrote NOTHING and said "No such
    file or directory: <root>"; the new one materialized the entire missing
    path and then failed one step later with "schemas directory not found".

    Both halves are asserted because only together do they describe the harm.
    The litter is the smaller one. The diagnostic is the real cost: "missing
    schemas" reads as an INCOMPLETE project rather than a nonexistent one,
    and by the time the operator reads it the path exists on disk, so going
    to look at it corroborates the wrong reading.

    A deep path, not a single missing segment: `parents=True` means one
    mistyped segment materializes an arbitrarily deep tree, and a one-level
    fixture would pass against a `mkdir(parents=False)` that still creates
    the wrong directory."""
    missing = tmp_path / "typo" / "deep" / "durable_root"
    real = make_durable_root(tmp_path, name="real_root")

    proc = run_backfill_from(
        real / "scripts" / "backfill_ever_converged.py",
        "--durable-root", str(missing), "--apply",
    )

    assert proc.returncode != 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    payload = parse_stdout(proc)
    assert payload["success"] is False
    assert str(missing) in payload["error"], (
        f"the error must NAME the path that is not there -- that sentence is "
        f"the whole diagnosis of a typo: {payload['error']!r}"
    )
    assert "does not exist" in payload["error"], (
        f"and must say the root is ABSENT, not that something inside it is "
        f"missing, which reads as an incomplete project: {payload['error']!r}"
    )

    assert not (tmp_path / "typo").exists(), (
        "a mistyped --durable-root must not materialize its own path; "
        "`mkdir(parents=True)` at the top of the run would create the whole "
        "missing tree before anything validated it"
    )
