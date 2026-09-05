"""tests/glossary_merge_marker.test.py -- #820: the durable glossary-merge
marker `canon_validate.py --merge-batches` writes on a SUCCESSFUL merge, and
the SEAM proving `select_segments.py`'s W5 admission gate
(`check_glossary_runs_merged()`) genuinely reads what this writer produces.

Two review rounds established that the W5 admission gate cannot INFER
merge state from any mutable project input (`name_candidates.json`,
per-batch adjudication fragments): a name can be frozen, adjudicated, AND
removed from a regenerated candidate list before ever reaching canon.json,
invisible to any re-derivation. The fix is to STOP inferring and START
recording: `canon_validate.py --glossary-merge-marker PATH`, given
alongside `--merge-batches`, atomically writes
`{"schema": "glossary-run-merged/1", "run_id", "merged_at", "batches",
"source": "merge", "dispatch_model"}` to PATH -- ONLY once the merge has landed on
disk (after `_stamp_write_verify()`'s own write + fresh re-read) -- and the
gate reads it back with no re-derivation at all.

## Scope

This file owns:
  1. The marker's exact pinned shape on a successful merge (schema/run_id/
     merged_at format/batches/source/dispatch_model), across one and
     several fragments.
  2. No `--glossary-merge-marker` flag given -> no marker written, ordinary
     merge success otherwise unaffected.
  3. An unwritable marker path -> the WHOLE merge call reports failure
     (canon.json is still committed underneath -- re-running
     --merge-batches over the same fragments is the documented recovery,
     since #291 already makes a fully-merged re-submission a no-op) rather
     than a silently marker-less success.
  4. An unsafe run id (derived from the marker path's own parent directory
     name) -> refused before any write is attempted.
  5. `--glossary-merge-marker` rejected outside `--merge-batches` (the
     table-driven CLI guard `canon_validate.py` already uses for every
     other mode-scoped flag).
  6. THE SEAM (case 7 below): the REAL `canon_validate.py --merge-batches
     --glossary-merge-marker` writes a marker under a REAL project fixture,
     and the REAL `select_segments.py` admits (or, marker-absent, refuses
     with reason "glossary-run-unmerged") reading that exact file -- no
     stub on either side. This is the one test that would catch a
     disagreement between this writer and the reader another engineer is
     implementing in parallel; if the reader is not finished yet, this
     case is expected to fail red rather than being weakened.

## Fixture strategy

Follows this plugin's established subprocess-isolation convention: every
test drives the REAL, shipped `canon_validate.py` (cases 1-6) or the REAL
`canon_validate.py` AND `select_segments.py` together (case 7) against an
isolated fixture root, via `tests/_senses_fixture.py`'s sanctioned
`stage_consumer()` -- never a hand-built stand-in for either script.
"""
import importlib.util
import json
import re
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
SCRIPTS_SRC_DIR = ASSETS_DIR / "scripts"
SCHEMAS_SRC_DIR = ASSETS_DIR / "schemas"

CANON_VALIDATE_SRC = SCRIPTS_SRC_DIR / "canon_validate.py"
SELECT_SEGMENTS_SRC = SCRIPTS_SRC_DIR / "select_segments.py"
LEDGER_MERGE_SRC = SCRIPTS_SRC_DIR / "ledger_merge.py"
DRAFT_READY_SRC = SCRIPTS_SRC_DIR / "draft_ready.py"
VALIDATE_DRAFT_SRC = SCRIPTS_SRC_DIR / "validate_draft.py"

for _src in (
    CANON_VALIDATE_SRC, SELECT_SEGMENTS_SRC, LEDGER_MERGE_SRC, DRAFT_READY_SRC, VALIDATE_DRAFT_SRC,
):
    assert _src.is_file(), f"expected script not found: {_src}"
assert SCHEMAS_SRC_DIR.is_dir(), f"schemas dir not found at {SCHEMAS_SRC_DIR}"

# ISO-8601 UTC, seconds precision, 'Z' suffix -- the same format
# ledger_update.py's/select_segments.py's own now_iso8601() copies use,
# which is what _merge_marker_now_iso8601() in canon_validate.py mirrors.
MERGED_AT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

GLOSSARY_RUN_MERGED_SCHEMA = "glossary-run-merged/1"


def _load_module(name: str, path: Path, extra_sys_path: Path):
    """Mirrors tests/canon_validate_recollapse.test.py's own loader exactly
    -- canon_validate.py's `from canon_senses import ...` only resolves via
    sys.path[0] under a real `python3 canon_validate.py` invocation, so its
    own scripts/ directory must be inserted onto sys.path around the
    in-process load."""
    sys.path.insert(0, str(extra_sys_path))
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None and spec.loader is not None, f"could not load spec for {path}"
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(extra_sys_path))


# #876: loaded in-process (not subprocessed) solely to read the frozen
# GLOSSARY_DISPATCH_MODEL_UNRECORDED constant off the REAL module, so this
# suite's pinned-shape assertion can never silently drift from what
# canon_validate.py actually ships.
_CANON_VALIDATE_MODULE = _load_module(
    "canon_validate_glossary_merge_marker_under_test", CANON_VALIDATE_SRC, SCRIPTS_SRC_DIR
)
GLOSSARY_DISPATCH_MODEL_UNRECORDED = _CANON_VALIDATE_MODULE.GLOSSARY_DISPATCH_MODEL_UNRECORDED


# ---------------------------------------------------------------------------
# A combined cache_key.py stub: canon_validate.py's STAMPING modes shell out
# to it as `--field <name>` (no --seg); select_segments.py's segment
# classification would shell out to it as `--seg <id>` (unused here -- every
# fixture below carries exactly one not_started segment, and
# classify_segment() never reaches cache_key.py for that state), but
# resolve_dirs() always computes its path and checks `.is_file()`
# unconditionally, so the file must exist regardless.
# ---------------------------------------------------------------------------

FAKE_CACHE_KEY_PY = """#!/usr/bin/env python3
import argparse
import json
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seg", default=None)
    parser.add_argument("--field", default=None)
    parser.add_argument("--durable-root", default=None)
    args = parser.parse_args()
    if args.field and not args.seg:
        print(f"fixture-{args.field}-hash")
        return 0
    if not args.seg:
        sys.stderr.write("fake cache_key.py: test stub requires --seg or --field\\n")
        return 1
    durable_root = Path(args.durable_root).resolve() if args.durable_root else Path(__file__).resolve().parent.parent
    keys_path = durable_root / "test_fixture_cache_keys.json"
    if not keys_path.is_file():
        sys.stderr.write(f"fake cache_key.py: no fixture keys file at {keys_path}\\n")
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


CANON_SCHEMA_FILES = (
    "canon-entry.schema.json",
    "canon-batch.schema.json",
    "canon-file.schema.json",
)


def make_canon_only_root(tmp_path):
    """Isolated root carrying ONLY canon_validate.py (+ its canon_senses.py
    sibling + every schema file it validates against) -- cases 1-6, which
    never invoke select_segments.py, follow
    tests/glossary_fragment_merge.test.py's own `make_durable_root` shape
    exactly."""
    root = tmp_path / "durable_root"
    stage_consumer(root, "canon_validate.py")
    (root / "scripts" / "cache_key.py").write_text(FAKE_CACHE_KEY_PY, encoding="utf-8")
    schemas_dir = root / "schemas"
    for name in CANON_SCHEMA_FILES:
        shutil.copy2(SCHEMAS_SRC_DIR / name, schemas_dir / name)
    return root


def make_full_project(tmp_path):
    """Isolated root carrying BOTH canon_validate.py and select_segments.py
    (plus every sibling select_segments.py's resolve_dirs() unconditionally
    checks for), self-anchored to the SAME root -- so a marker
    canon_validate.py writes under `{root}/glossary/runs/...` is exactly
    what select_segments.py, run from the same root, reads back. Mirrors
    tests/select_segments_glossary_gate.test.py's own `make_full_project`
    fixture (one not_started 'seg01' segment, empty runs/ledger.d/ and
    segments/, glossary enabled at research_mode offline)."""
    root = tmp_path / "durable_root"
    scripts_dir = root / "scripts"
    schemas_dir = root / "schemas"
    shutil.copytree(SCHEMAS_SRC_DIR, schemas_dir, dirs_exist_ok=True)
    scripts_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(SELECT_SEGMENTS_SRC, scripts_dir / "select_segments.py")
    shutil.copy2(LEDGER_MERGE_SRC, scripts_dir / "ledger_merge.py")
    shutil.copy2(DRAFT_READY_SRC, scripts_dir / "draft_ready.py")
    shutil.copy2(VALIDATE_DRAFT_SRC, scripts_dir / "validate_draft.py")
    (scripts_dir / "cache_key.py").write_text(FAKE_CACHE_KEY_PY, encoding="utf-8")
    stage_consumer(root, "glossary_batch_plan.py")
    stage_consumer(root, "canon_validate.py")

    (root / "runs" / "ledger.d").mkdir(parents=True)
    (root / "segments").mkdir()
    (root / "manifest.json").write_text(
        json.dumps({"segments": [{"seg": "seg01"}]}, ensure_ascii=False), encoding="utf-8"
    )
    (root / "canon.json").write_text(
        json.dumps({"entries": {}}, ensure_ascii=False), encoding="utf-8"
    )
    (root / "name_candidates.json").write_text(
        json.dumps({"candidates": []}, ensure_ascii=False), encoding="utf-8"
    )
    profile_path = root / "profile.yml"
    profile_path.write_text(
        "glossary:\n  enabled: true\n  research_mode: offline\n", encoding="utf-8"
    )
    (root / ".literary-translator-root.json").write_text(
        json.dumps({"owner_profile_path": str(profile_path)}), encoding="utf-8"
    )
    return root


def write_fragment(root, items, name="fragment.json"):
    frag_path = root / name
    frag_path.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
    return frag_path


def accepted_batch_item(source_form, canonical_target_form="Placeholder", basis="transliterated", confidence="high"):
    return {
        "source_form": source_form,
        "is_proper_name": True,
        "disposition": "accepted",
        "canonical_target_form": canonical_target_form,
        "basis": basis,
        "confidence": confidence,
    }


def run_merge_batches(root, fragment_paths, research_mode="offline", marker_path=None, timeout=30):
    cmd = [
        sys.executable,
        str(root / "scripts" / "canon_validate.py"),
        "--research-mode",
        research_mode,
        "--merge-batches",
        *[str(p) for p in fragment_paths],
        "--allow-durable-sibling",
    ]
    if marker_path is not None:
        cmd += ["--glossary-merge-marker", str(marker_path)]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=str(root))


def run_select_segments(root, timeout=30):
    return subprocess.run(
        [sys.executable, str(root / "scripts" / "select_segments.py")],
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


# ===========================================================================
# 1. Marker written on a successful merge, exact pinned shape (one fragment).
# ===========================================================================


def test_1_marker_written_with_pinned_shape_on_successful_merge(tmp_path):
    root = make_canon_only_root(tmp_path)
    run_dir = root / "glossary" / "runs" / "R1"
    run_dir.mkdir(parents=True)
    frag = write_fragment(root, [accepted_batch_item("Fiona")], name="out_0.json")
    marker_path = run_dir / "merged.json"

    proc = run_merge_batches(root, [frag], marker_path=marker_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    payload = parse_stdout(proc)
    assert payload["success"] is True

    assert marker_path.is_file(), "expected a marker at the given --glossary-merge-marker path"
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    assert set(marker.keys()) == {
        "schema", "run_id", "merged_at", "batches", "source", "dispatch_model",
    }, marker
    assert marker["schema"] == GLOSSARY_RUN_MERGED_SCHEMA
    assert marker["run_id"] == "R1", "run_id must be derived from the marker path's own parent directory name"
    assert MERGED_AT_RE.match(marker["merged_at"]), marker["merged_at"]
    assert marker["batches"] == [0]
    assert marker["source"] == "merge"
    # #876: pinned against canon_validate.py's own constant, loaded
    # in-process at :117; test_1b below pins the sentence itself.
    assert marker["dispatch_model"] == GLOSSARY_DISPATCH_MODEL_UNRECORDED


# ===========================================================================
# 1b. #876: the LITERAL statement the marker makes. Every other assertion in
#     this file compares the emitted object against the production constant,
#     so a SYNCHRONISED edit to both copies of it passes all of them -- and
#     the whole point of this key is the sentence it carries. This one pins
#     that sentence itself, so weakening it has to be a deliberate act with
#     a changelog entry, not a quiet edit two assertions agree with.
# ===========================================================================


def test_1b_dispatch_model_is_the_shipped_statement():
    assert GLOSSARY_DISPATCH_MODEL_UNRECORDED == {
        "recorded": False,
        "reason": (
            "this pipeline dispatches the glossary pass with no model argument and "
            "records no model anywhere in the run, so the model that produced these "
            "rows is not recorded here -- absent by design, not merely missing"
        ),
    }


# ===========================================================================
# 2. Several fragments -> batches is the ascending, 0-based position of each
#    fragment in the given --merge-batches order (matching what was
#    actually merged in THIS call).
# ===========================================================================


def test_2_batches_reflects_the_fragments_actually_merged_in_this_call(tmp_path):
    root = make_canon_only_root(tmp_path)
    run_dir = root / "glossary" / "runs" / "R2"
    run_dir.mkdir(parents=True)
    frag0 = write_fragment(root, [accepted_batch_item("Alpha")], name="out_0.json")
    frag1 = write_fragment(root, [accepted_batch_item("Beta")], name="out_1.json")
    frag2 = write_fragment(root, [accepted_batch_item("Gamma")], name="out_2.json")
    marker_path = run_dir / "merged.json"

    proc = run_merge_batches(root, [frag0, frag1, frag2], marker_path=marker_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    payload = parse_stdout(proc)
    assert payload["success"] is True
    assert payload["fragments_merged"] == 3

    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    assert marker["batches"] == [0, 1, 2]


# ===========================================================================
# 3. No --glossary-merge-marker given -> ordinary merge, no marker anywhere.
# ===========================================================================


def test_3_no_flag_means_no_marker_written(tmp_path):
    root = make_canon_only_root(tmp_path)
    run_dir = root / "glossary" / "runs" / "R3"
    run_dir.mkdir(parents=True)
    frag = write_fragment(root, [accepted_batch_item("Delta")], name="out_0.json")

    proc = run_merge_batches(root, [frag])
    assert proc.returncode == 0, proc.stdout + proc.stderr
    payload = parse_stdout(proc)
    assert payload["success"] is True

    assert not (run_dir / "merged.json").is_file()
    assert list(run_dir.iterdir()) == [], "no file at all should appear under the run dir"

    on_disk = json.loads((root / "canon.json").read_text(encoding="utf-8"))
    assert "Delta" in on_disk["entries"], "the merge itself must be unaffected by the flag's absence"


# ===========================================================================
# 4. An unwritable marker path -> the WHOLE merge call fails loudly, even
#    though canon.json underneath was already committed by
#    _stamp_write_verify() before the marker write was attempted.
# ===========================================================================


def test_4_unwritable_marker_path_fails_the_merge_loudly(tmp_path):
    root = make_canon_only_root(tmp_path)
    (root / "glossary" / "runs").mkdir(parents=True)
    # R4 exists as a FILE, not a directory -- so `{run_dir}/merged.json`'s
    # own parent can never be created/entered. A genuine, portable write
    # failure that needs no chmod/permission trickery.
    run_dir_as_file = root / "glossary" / "runs" / "R4"
    run_dir_as_file.write_text("not a directory", encoding="utf-8")
    marker_path = run_dir_as_file / "merged.json"
    frag = write_fragment(root, [accepted_batch_item("Epsilon")], name="out_0.json")

    proc = run_merge_batches(root, [frag], marker_path=marker_path)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    payload = parse_stdout(proc)
    assert payload["success"] is False
    assert "glossary merge marker" in payload["error"], payload

    # Not silently marker-less: the CALL as a whole reports failure. The
    # underlying merge is still idempotent-retryable once the path is
    # fixed (#291: re-merging already-merged content is a no-op), which is
    # exactly why canon.json itself was allowed to already hold the entry.
    on_disk = json.loads((root / "canon.json").read_text(encoding="utf-8"))
    assert "Epsilon" in on_disk["entries"], (
        "canon.json is written by _stamp_write_verify() BEFORE the marker "
        "is attempted, so the merge content itself is unaffected by a "
        "downstream marker failure"
    )
    assert run_dir_as_file.is_file() and run_dir_as_file.read_text(encoding="utf-8") == "not a directory"


# ===========================================================================
# 5. An unsafe run id (from the marker path's parent directory name) is
#    refused before any write is attempted.
# ===========================================================================


@pytest.mark.parametrize("unsafe_run_id", ["..", "bad:id", "has/slash", ""])
def test_5_unsafe_run_id_is_refused(tmp_path, unsafe_run_id):
    root = make_canon_only_root(tmp_path)
    frag = write_fragment(root, [accepted_batch_item("Zeta")], name="out_0.json")
    # "" and "has/slash" can never reach validate_run_id() as
    # `Path(marker).parent.name` via a real filesystem path: pathlib's `/`
    # operator drops an EMPTY component outright, and a slash-bearing
    # string is not one path component at all -- it SPLITS into several
    # real ones (verified: `Path("a") / "" == Path("a")` and
    # `Path("a") / "has/slash" == Path("a/has/slash")`, whose OWN final
    # component is the safe name "slash", not the unsafe string). "." was
    # tried too and dropped from this list for the identical reason
    # (`Path("a") / "." == Path("a")`) -- only ".." and "bad:id" survive as
    # one literal directory-name string a real caller could produce.
    if unsafe_run_id in ("", "has/slash"):
        pytest.skip("not expressible as a single path-component run id via pathlib's / operator")
    marker_path = root / "glossary" / "runs" / unsafe_run_id / "merged.json"

    proc = run_merge_batches(root, [frag], marker_path=marker_path)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    payload = parse_stdout(proc)
    assert payload["success"] is False
    assert "run id" in payload["error"], payload
    assert not marker_path.is_file()


# ===========================================================================
# 6. --glossary-merge-marker is refused outside --merge-batches.
# ===========================================================================


def test_6_flag_refused_outside_merge_batches(tmp_path):
    root = make_canon_only_root(tmp_path)
    proc = subprocess.run(
        [
            sys.executable,
            str(root / "scripts" / "canon_validate.py"),
            "--research-mode",
            "offline",
            "--init",
            "--allow-durable-sibling",
            "--glossary-merge-marker",
            str(root / "glossary" / "runs" / "R6" / "merged.json"),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=str(root),
    )
    assert proc.returncode != 0
    assert "--glossary-merge-marker" in proc.stderr


# ===========================================================================
# 7. THE SEAM: the REAL canon_validate.py --merge-batches
#    --glossary-merge-marker output feeds the REAL select_segments.py W5
#    admission gate directly -- no stub on either side.
# ===========================================================================


def test_7_seam_real_marker_admits_real_select_segments_gate(tmp_path):
    root = make_full_project(tmp_path)
    run_dir = root / "glossary" / "runs" / "R7"
    run_dir.mkdir(parents=True)
    frag = write_fragment(root, [accepted_batch_item("Eta")], name="out_0.json")
    marker_path = run_dir / "merged.json"

    merge_proc = run_merge_batches(root, [frag], marker_path=marker_path)
    assert merge_proc.returncode == 0, merge_proc.stdout + merge_proc.stderr
    assert marker_path.is_file()

    select_proc = run_select_segments(root)
    assert select_proc.returncode == 0, (
        f"expected the real select_segments.py to ADMIT reading the real "
        f"marker canon_validate.py just wrote.\n"
        f"select_segments stdout={select_proc.stdout!r}\n"
        f"select_segments stderr={select_proc.stderr!r}"
    )
    payload = parse_stdout(select_proc)
    assert payload["success"] is True, payload
    assert payload["segs"] == ["seg01"], payload


def test_7b_seam_absent_marker_refuses_the_real_gate(tmp_path):
    """Companion to 7: proves the marker is genuinely load-bearing for
    admission (not merely coincidentally present) -- the same run
    directory, real select_segments.py, but WITHOUT ever running the merge
    (so no merged.json), must refuse with reason "glossary-run-unmerged"."""
    root = make_full_project(tmp_path)
    run_dir = root / "glossary" / "runs" / "R7b"
    run_dir.mkdir(parents=True)

    select_proc = run_select_segments(root)
    assert select_proc.returncode == 1, select_proc.stdout + select_proc.stderr
    payload = parse_stdout(select_proc)
    assert payload["success"] is False, payload
    assert payload["reason"] == "glossary-run-unmerged", payload


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
