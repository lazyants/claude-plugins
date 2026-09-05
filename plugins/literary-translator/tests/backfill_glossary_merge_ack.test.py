#!/usr/bin/env python3
"""#820 -- backfill_glossary_merge_ack.py, the retrofit that lets a project
whose W3 glossary pass merged BEFORE `canon_validate.py --merge-batches`
started writing a durable `glossary/runs/<RUN_ID>/merged.json` marker get
past the new W5 admission gate WITHOUT forging evidence of a verified merge.

The gate itself (refusing dispatch while a run directory has no
`merged.json`) is tested in tests/select_segments_glossary_gate.test.py.
This file covers the writer's own contract: structural completeness (every
`manifest_<index>.json` has a matching `out_<index>_attempt_0.json`), the
dry-run/`--apply` split, never overwriting an existing marker, refusing an
unsafe RUN_ID-shaped directory name, and the exact pinned marker shape.

## The single most important test here

`test_never_claims_a_verified_merge`: the whole design rests on the retrofit
acknowledging structural completeness, never claiming the merge itself was
checked. An implementation that quietly upgraded `source` to `"merge"`, or
wrote a `note` implying verification, would satisfy every other assertion in
this file while silently letting a later reader trust a marker it should
not.

## Fixture strategy

Every test drives the REAL, shipped `backfill_glossary_merge_ack.py` as a
subprocess against an isolated fixture tree, staged into
`tmp_path/durable_root/scripts/` alongside its `json_stdout.py` sibling --
the same staging convention `tests/select_segments_glossary_gate.test.py`
uses for `select_segments.py`, so `Path(__file__).resolve().parent`
self-anchors exactly like a real Step 0a deployment. `--durable-root` is
passed explicitly in most tests (matching `tests/backfill_resume_gate_ack
.test.py`'s own `run_backfill()` helper); one test drives the script with no
flags at all to prove the self-anchored default still resolves correctly.
"""

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
ASSETS_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets"
SCRIPTS_SRC_DIR = ASSETS_DIR / "scripts"

BACKFILL_SRC = SCRIPTS_SRC_DIR / "backfill_glossary_merge_ack.py"
JSON_STDOUT_SRC = SCRIPTS_SRC_DIR / "json_stdout.py"
GLOSSARY_DRIVER_SRC = SCRIPTS_SRC_DIR / "glossary_dispatch_driver.py"
RESUME_SETUP_SRC = SCRIPTS_SRC_DIR / "resume_setup.py"
CANON_VALIDATE_SRC = SCRIPTS_SRC_DIR / "canon_validate.py"

for _src in (BACKFILL_SRC, JSON_STDOUT_SRC, GLOSSARY_DRIVER_SRC, RESUME_SETUP_SRC, CANON_VALIDATE_SRC):
    assert _src.is_file(), f"expected script not found: {_src}"


def _load_module(name: str, path: Path, extra_sys_path: "Path | None" = None):
    """Mirrors tests/canon_validate_recollapse.test.py's own loader --
    `canon_validate.py`'s `from canon_senses import ...` only resolves via
    sys.path[0] under a real `python3 canon_validate.py` invocation, so its
    own scripts/ directory must be inserted onto sys.path around the
    in-process load. `backfill_glossary_merge_ack.py` imports no sibling of
    its own, so its own load passes `extra_sys_path=None` and skips this
    entirely."""
    if extra_sys_path is not None:
        sys.path.insert(0, str(extra_sys_path))
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None and spec.loader is not None, f"could not load spec for {path}"
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        if extra_sys_path is not None:
            sys.path.remove(str(extra_sys_path))


# #876: both loaded in-process (not subprocessed) solely to read each
# module's own GLOSSARY_DISPATCH_MODEL_UNRECORDED copy, so the anti-drift
# pin below can never silently drift from what either module actually ships.
_CANON_VALIDATE_MODULE = _load_module(
    "canon_validate_backfill_glossary_merge_ack_under_test", CANON_VALIDATE_SRC, SCRIPTS_SRC_DIR
)
_BACKFILL_MODULE = _load_module(
    "backfill_glossary_merge_ack_under_test", BACKFILL_SRC
)
GLOSSARY_DISPATCH_MODEL_UNRECORDED = _CANON_VALIDATE_MODULE.GLOSSARY_DISPATCH_MODEL_UNRECORDED


# ---------------------------------------------------------------------------
# Staging -- mirrors tests/select_segments_glossary_gate.test.py's
# `_stage_scripts()`: real shipped files copied into an isolated
# `{root}/scripts/` so the script's own self-anchoring resolves against the
# fixture exactly as Step 0a's copy pass does in production.
# ---------------------------------------------------------------------------

def stage(tmp_path, name="durable_root") -> Path:
    root = tmp_path / name
    scripts_dir = root / "scripts"
    scripts_dir.mkdir(parents=True)
    shutil.copy2(BACKFILL_SRC, scripts_dir / "backfill_glossary_merge_ack.py")
    shutil.copy2(JSON_STDOUT_SRC, scripts_dir / "json_stdout.py")
    return root


def run_backfill(root, *extra_args, use_durable_root_flag=True, timeout=60):
    script = root / "scripts" / "backfill_glossary_merge_ack.py"
    args = [sys.executable, str(script)]
    if use_durable_root_flag:
        args += ["--durable-root", str(root)]
    args += list(extra_args)
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout)


def parse_stdout(proc):
    assert proc.stdout.strip(), f"expected one JSON line on stdout, got none. stderr:\n{proc.stderr}"
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    assert len(lines) == 1, f"expected exactly one JSON line, got:\n{proc.stdout}"
    return json.loads(lines[0])


def snapshot(root):
    """Every path under `root` plus each file's bytes -- for proving a dry
    run (or a refusal) wrote absolutely nothing, not merely that it wrote no
    marker."""
    out = {}
    for p in sorted(root.rglob("*")):
        out[str(p.relative_to(root))] = p.read_bytes() if p.is_file() else None
    return out


# ---------------------------------------------------------------------------
# Glossary-run fixture builders -- mirror the real RUN_DIR layout
# glossary-pass-wf.template.js writes: {durable_root}/glossary/runs/<RUN_ID>/
# manifest_<index>.json + out_<index>_attempt_<n>.json + manifest_all.json.
# ---------------------------------------------------------------------------

def run_dir(root: Path, run_id: str) -> Path:
    d = root / "glossary" / "runs" / run_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def write_manifest(root: Path, run_id: str, index: int, names=("Fiona",)) -> None:
    d = run_dir(root, run_id)
    (d / f"manifest_{index}.json").write_text(
        json.dumps({"index": index, "candidates": [{"name": n} for n in names]}),
        encoding="utf-8",
    )


def write_fragment(root: Path, run_id: str, index: int, attempt: int = 0) -> None:
    d = run_dir(root, run_id)
    (d / f"out_{index}_attempt_{attempt}.json").write_text(
        json.dumps({"index": index, "entries": []}), encoding="utf-8"
    )


def make_complete_run(root: Path, run_id: str, indices=(0,)) -> None:
    """A run whose every manifest_<index>.json has a matching
    out_<index>_attempt_0.json -- structurally complete, eligible for
    acknowledgement."""
    for idx in indices:
        write_manifest(root, run_id, idx)
        write_fragment(root, run_id, idx, attempt=0)
    (run_dir(root, run_id) / "manifest_all.json").write_text(
        json.dumps({"candidates": []}), encoding="utf-8"
    )


def make_incomplete_run(root: Path, run_id: str, complete_indices=(0,), missing_indices=(1,)) -> None:
    """A run with manifests for both `complete_indices` and
    `missing_indices`, but attempt-0 fragments ONLY for `complete_indices` --
    the shape structural completeness must refuse."""
    for idx in complete_indices:
        write_manifest(root, run_id, idx)
        write_fragment(root, run_id, idx, attempt=0)
    for idx in missing_indices:
        write_manifest(root, run_id, idx)


def existing_marker(root: Path, run_id: str, doc: dict) -> Path:
    path = run_dir(root, run_id) / "merged.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


# ===========================================================================
# The contract that matters most.
# ===========================================================================


def test_never_claims_a_verified_merge(tmp_path):
    root = stage(tmp_path)
    make_complete_run(root, "OLDRUN", indices=(0, 1))

    proc = run_backfill(root, "--apply")

    assert proc.returncode == 0, proc.stdout
    marker = run_dir(root, "OLDRUN") / "merged.json"
    assert marker.is_file()
    body = json.loads(marker.read_text(encoding="utf-8"))
    assert body["source"] == "backfill-ack", (
        "the retrofit must never claim it is a real canon_validate.py merge "
        f"record -- got source={body.get('source')!r}"
    )
    assert body["source"] != "merge"
    assert "not a verified merge" in body["note"] or "not verified" in body["note"].lower()


# ===========================================================================
# Dry run / apply, and the exact pinned marker shape.
# ===========================================================================


def test_dry_run_writes_absolutely_nothing(tmp_path):
    root = stage(tmp_path)
    make_complete_run(root, "OLDRUN", indices=(0,))
    before = snapshot(root)

    proc = run_backfill(root)

    assert proc.returncode == 0, proc.stdout
    payload = parse_stdout(proc)
    assert payload["applied"] is False
    assert payload["needs_ack"] == ["OLDRUN"]
    assert payload["created"] == []
    assert snapshot(root) == before, "a dry run must make ZERO filesystem writes"


def test_apply_writes_the_exact_pinned_marker_shape(tmp_path):
    root = stage(tmp_path)
    make_complete_run(root, "OLDRUN", indices=(0, 2, 5))

    payload = parse_stdout(run_backfill(root, "--apply"))

    assert payload["created"] == ["OLDRUN"]
    marker = run_dir(root, "OLDRUN") / "merged.json"
    body = json.loads(marker.read_text(encoding="utf-8"))
    assert set(body.keys()) == {
        "schema", "run_id", "merged_at", "batches", "source", "note", "dispatch_model",
    }
    assert body["schema"] == "glossary-run-merged/1"
    assert body["run_id"] == "OLDRUN"
    assert body["merged_at"].endswith("Z")
    assert body["batches"] == [0, 2, 5], "batches must be the ascending int indices acknowledged"
    assert body["source"] == "backfill-ack"
    assert isinstance(body["note"], str) and body["note"]
    # #876: identical on both writers -- see test_dispatch_model_constant_
    # matches_canon_validates_own_copy below for the anti-drift pin.
    assert body["dispatch_model"] == GLOSSARY_DISPATCH_MODEL_UNRECORDED


def test_dispatch_model_constant_matches_canon_validates_own_copy():
    """#876: this file restates canon_validate.py's GLOSSARY_DISPATCH_MODEL_
    UNRECORDED rather than importing it (the established "no shared lib
    between self-contained scripts" convention _RUN_ID_RE already follows
    in both files). A restatement with no drift check is exactly the defect
    class #876 is about -- two copies of a claim that silently disagree --
    so this loads BOTH real modules in-process and pins them equal."""
    assert _BACKFILL_MODULE._GLOSSARY_DISPATCH_MODEL_UNRECORDED == GLOSSARY_DISPATCH_MODEL_UNRECORDED


def test_apply_is_idempotent_and_never_rewrites(tmp_path):
    root = stage(tmp_path)
    make_complete_run(root, "OLDRUN", indices=(0,))
    first = parse_stdout(run_backfill(root, "--apply"))
    assert first["created"] == ["OLDRUN"]
    marker = run_dir(root, "OLDRUN") / "merged.json"
    body_after_first = marker.read_bytes()

    second = parse_stdout(run_backfill(root, "--apply", "--allow-empty"))

    assert second["created"] == []
    assert second["already_marked"] == ["OLDRUN"]
    assert marker.read_bytes() == body_after_first, "an existing marker must never be rewritten"


# ===========================================================================
# Structural completeness -- the honesty check.
# ===========================================================================


def test_structurally_incomplete_run_is_refused_and_left_untouched(tmp_path):
    root = stage(tmp_path)
    make_incomplete_run(root, "PARTIAL", complete_indices=(0,), missing_indices=(1,))
    before = snapshot(root)

    proc = run_backfill(root, "--apply")

    assert proc.returncode != 0, "a refused run must exit non-zero"
    payload = parse_stdout(proc)
    assert payload["success"] is False
    assert payload["created"] == []
    assert payload["needs_ack"] == []
    assert len(payload["refused"]) == 1
    assert payload["refused"][0]["run_id"] == "PARTIAL"
    assert "1" in payload["refused"][0]["reason"]  # names the missing index
    assert not (run_dir(root, "PARTIAL") / "merged.json").exists(), (
        "an incomplete run must never be acknowledged -- that is exactly "
        "the defect #820's gate exists to prevent"
    )
    assert snapshot(root) == before, "a refused run must never be written to"


def test_run_with_no_manifests_at_all_is_refused(tmp_path):
    root = stage(tmp_path)
    d = run_dir(root, "EMPTYRUN")
    (d / "manifest_all.json").write_text(json.dumps({"candidates": []}), encoding="utf-8")

    proc = run_backfill(root, "--apply")

    assert proc.returncode != 0
    payload = parse_stdout(proc)
    assert payload["refused"][0]["run_id"] == "EMPTYRUN"
    assert "no manifest_<index>.json" in payload["refused"][0]["reason"]
    assert not (d / "merged.json").exists()


def test_batches_field_reflects_only_the_indices_actually_present(tmp_path):
    """Gaps between indices are fine -- completeness is per-manifest, not a
    contiguous range."""
    root = stage(tmp_path)
    make_complete_run(root, "SPARSE", indices=(0, 3))

    payload = parse_stdout(run_backfill(root, "--apply"))

    assert payload["batches_by_run_id"]["SPARSE"] == [0, 3]


# ===========================================================================
# Never overwrite an existing marker -- of ANY source, valid or foreign.
# ===========================================================================


def test_existing_merge_source_marker_is_never_downgraded(tmp_path):
    root = stage(tmp_path)
    make_complete_run(root, "REALMERGE", indices=(0,))
    existing_marker(root, "REALMERGE", {
        "schema": "glossary-run-merged/1", "run_id": "REALMERGE",
        "merged_at": "2026-01-01T00:00:00Z", "batches": [0], "source": "merge",
    })
    before_bytes = (run_dir(root, "REALMERGE") / "merged.json").read_bytes()

    payload = parse_stdout(run_backfill(root, "--apply", "--allow-empty"))

    assert payload["already_marked"] == ["REALMERGE"]
    assert payload["created"] == []
    assert (run_dir(root, "REALMERGE") / "merged.json").read_bytes() == before_bytes, (
        "a real merge record must never be rewritten, let alone downgraded"
    )


def test_existing_unreadable_marker_is_refused_not_overwritten(tmp_path):
    """A corrupt/foreign merged.json is ambiguous, not absent -- this must
    fail closed exactly like the two prior backfill scripts' AMBIGUOUS
    handling: an entry this script cannot make sense of is never silently
    replaced."""
    root = stage(tmp_path)
    make_complete_run(root, "CORRUPT", indices=(0,))
    marker = run_dir(root, "CORRUPT") / "merged.json"
    marker.write_text("not valid json {{{", encoding="utf-8")

    proc = run_backfill(root, "--apply")

    assert proc.returncode != 0
    payload = parse_stdout(proc)
    assert payload["refused"][0]["run_id"] == "CORRUPT"
    assert marker.read_text(encoding="utf-8") == "not valid json {{{"


# ===========================================================================
# RUN_ID safety.
# ===========================================================================


def test_unsafe_directory_name_is_refused_loudly(tmp_path):
    root = stage(tmp_path)
    unsafe = root / "glossary" / "runs" / "a..b"
    unsafe.mkdir(parents=True)
    (unsafe / "manifest_0.json").write_text(json.dumps({"index": 0}), encoding="utf-8")

    proc = run_backfill(root, "--apply")

    assert proc.returncode != 0
    payload = parse_stdout(proc)
    assert payload["success"] is False
    assert "unsafe" in payload["error"]
    assert "a..b" in payload["error"]


def test_dot_prefixed_staging_entries_are_not_treated_as_run_dirs(tmp_path):
    """A name that fails even the base RUN_ID character class (dot-prefixed,
    matching the shape a driver's own staging files use) is not run-id
    shaped at all and must be silently skipped, never fatally refused."""
    root = stage(tmp_path)
    make_complete_run(root, "REALRUN", indices=(0,))
    stray = root / "glossary" / "runs" / ".stray_staging_dir"
    stray.mkdir(parents=True)

    payload = parse_stdout(run_backfill(root, "--apply"))

    assert payload["success"] is True
    assert "REALRUN" in payload["created"]
    assert payload["runs_scanned"] == ["REALRUN"]


# ===========================================================================
# Guards / counts, including the zero-runs case.
# ===========================================================================


def test_zero_runs_fatals_without_allow_empty(tmp_path):
    root = stage(tmp_path)

    proc = run_backfill(root, "--apply")

    assert proc.returncode != 0
    payload = parse_stdout(proc)
    assert payload["success"] is False
    assert "--allow-empty" in payload["error"]
    assert payload["runs_scanned"] == []


def test_zero_runs_reports_cleanly_with_allow_empty(tmp_path):
    root = stage(tmp_path)

    payload = parse_stdout(run_backfill(root, "--allow-empty"))

    assert payload["success"] is True
    assert payload["runs_scanned"] == []
    assert payload["counts"] == {
        "runs_scanned": 0, "already_marked": 0, "needs_ack": 0,
        "created": 0, "refused": 0,
    }


def test_counts_are_correct_across_every_bucket(tmp_path):
    root = stage(tmp_path)
    make_complete_run(root, "TO_ACK", indices=(0,))
    make_incomplete_run(root, "BAD", complete_indices=(0,), missing_indices=(1,))
    make_complete_run(root, "ALREADY", indices=(0,))
    existing_marker(root, "ALREADY", {
        "schema": "glossary-run-merged/1", "run_id": "ALREADY",
        "merged_at": "2026-01-01T00:00:00Z", "batches": [0], "source": "merge",
    })

    payload = parse_stdout(run_backfill(root, "--apply"))

    assert payload["counts"] == {
        "runs_scanned": 3, "already_marked": 1, "needs_ack": 1,
        "created": 1, "refused": 1,
    }
    assert payload["success"] is False, "a non-empty refused bucket must fail the run"


# ===========================================================================
# CLI parity / self-anchoring.
# ===========================================================================


def test_plugin_root_is_accepted_and_reported_but_resolves_nothing(tmp_path):
    root = stage(tmp_path)
    make_complete_run(root, "OLDRUN", indices=(0,))
    fake_plugin_root = tmp_path / "plugin_root_unused"
    fake_plugin_root.mkdir()

    payload = parse_stdout(run_backfill(root, "--allow-empty", "--plugin-root", str(fake_plugin_root)))

    assert payload["plugin_root"] == str(fake_plugin_root.resolve())


def test_self_anchored_invocation_needs_no_durable_root_flag(tmp_path):
    root = stage(tmp_path)
    make_complete_run(root, "OLDRUN", indices=(0,))

    payload = parse_stdout(run_backfill(root, "--apply", use_durable_root_flag=False))

    assert payload["durable_root"] == str(root.resolve())
    assert payload["created"] == ["OLDRUN"]


# ===========================================================================
# Drift pin -- this script's validate_run_id()/RUN_ID_RE must stay identical
# to resume_setup.py's, which OWNS the contract (glossary_dispatch_driver.py
# duplicates the same copy and is checked against it by
# tests/run_id_pattern_drift.test.py; this pins the third copy in).
# ===========================================================================


def _extract_run_id_re_pattern(src_text: str) -> str:
    import re as _re
    m = _re.search(r'RUN_ID_RE\s*=\s*re\.compile\(r"([^"]+)"\)', src_text)
    assert m, "could not find a RUN_ID_RE assignment"
    return m.group(1)


def test_run_id_re_matches_resume_setup_py():
    ours = _extract_run_id_re_pattern(BACKFILL_SRC.read_text(encoding="utf-8"))
    theirs = _extract_run_id_re_pattern(RESUME_SETUP_SRC.read_text(encoding="utf-8"))
    assert ours == theirs, (
        f"this script's _RUN_ID_RE ({ours!r}) has drifted from "
        f"resume_setup.py's own RUN_ID_RE ({theirs!r}), the contract owner"
    )


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
