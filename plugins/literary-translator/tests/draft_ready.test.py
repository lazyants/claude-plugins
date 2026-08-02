"""tests/draft_ready.test.py -- regression-lock suite for
scripts/draft_ready.py, the delivery/readiness probe for
segments/{seg}.draft.json.

No dedicated test file existed for this script before -- it was previously
exercised only indirectly (draft_path_convention.test.py, seg_safety_*.test.py,
codex_job_driver.test.py, etc.), none of which cover its OWN CLI surface in
isolation. This file follows the plugin's established subprocess-fixture
convention (validate_draft.test.py's own `make_durable_root` pattern): copy
the REAL draft_ready.py into an isolated `tmp_path/.../scripts/` root and
invoke it exactly as production does, so its `Path(__file__)`-based
self-anchoring resolves against the fixture.

Primary focus: the #412 prerequisite -- an optional `--durable-root PATH`
that governs DATA only (segments/), following the SAME convention
`select_segments.py`/`ledger_merge.py`/`resume_setup.py`/`review_ready.py`
already established (see references/gotchas.md §4), but WITHOUT a companion
`--plugin-root`: this script is a LEAF, it shells out to nothing at all, so
there is no sibling-script resolution concern and nothing to forward.

Every #412 test below uses an ORPHAN-COPY fixture -- the script file itself
sits somewhere with no `segments/` co-located at all -- so co-location can
never be what makes a redirect test pass; only the flag itself can.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
ASSETS_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets"
SCRIPT_SRC = ASSETS_DIR / "scripts" / "draft_ready.py"

assert SCRIPT_SRC.is_file(), f"draft_ready.py not found at {SCRIPT_SRC}"


# ---------------------------------------------------------------------------
# Fixture harness
# ---------------------------------------------------------------------------

def make_durable_root(tmp_path, name="durable_root"):
    """Builds an isolated durable_root: copies the REAL draft_ready.py into
    {root}/scripts/ (so its self-anchoring resolves to THIS temp root) and
    creates segments/."""
    root = tmp_path / name
    scripts_dir = root / "scripts"
    scripts_dir.mkdir(parents=True)
    shutil.copy2(SCRIPT_SRC, scripts_dir / "draft_ready.py")
    (root / "segments").mkdir()
    return root


def write_segment(root, seg, segpack, draft):
    segments_dir = root / "segments"
    (segments_dir / f"segpack_{seg}.json").write_text(
        json.dumps(segpack, ensure_ascii=False), encoding="utf-8"
    )
    (segments_dir / f"{seg}.draft.json").write_text(
        json.dumps(draft, ensure_ascii=False), encoding="utf-8"
    )


def clean_segpack():
    """Minimal segpack.schema.json-shaped fixture: one block, one footnote,
    one verse -- just enough for draft_ready.py's own key-set comparison."""
    return {
        "blocks": [{"id": "p1"}],
        "footnotes": [{"n": 1}],
        "verses": [{"vid": "vA"}],
    }


def clean_draft(seg="seg01"):
    """A draft whose block/footnote/verse KEY SETS exactly match
    clean_segpack()'s -- the minimum draft_ready.py's readiness comparison
    requires."""
    return {
        "seg": seg,
        "blocks": {"p1": "translated text"},
        "footnotes": {"1": "translated note"},
        "verses": {"vA": {}},
        "names": [],
        "notes": [],
    }


def run_draft_ready(root, seg, *extra_args, timeout=30):
    return subprocess.run(
        [sys.executable, str(root / "scripts" / "draft_ready.py"), seg, *extra_args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def run_draft_ready_from(script_path, seg, *extra_args, timeout=30):
    return subprocess.run(
        [sys.executable, str(script_path), seg, *extra_args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


# ---------------------------------------------------------------------------
# Sanity: the harness itself is sound.
# ---------------------------------------------------------------------------

def test_clean_baseline_is_ready(tmp_path):
    root = make_durable_root(tmp_path)
    write_segment(root, "seg01", clean_segpack(), clean_draft())

    result = run_draft_ready(root, "seg01")

    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert "[seg01] READY" in result.stdout


def test_missing_segpack_is_not_ready(tmp_path):
    root = make_durable_root(tmp_path)
    (root / "segments" / "seg01.draft.json").write_text(
        json.dumps(clean_draft(), ensure_ascii=False), encoding="utf-8"
    )
    # No segpack written.

    result = run_draft_ready(root, "seg01")

    assert result.returncode == 1
    assert "segpack missing" in result.stdout


# ---------------------------------------------------------------------------
# #412 prerequisite -- --durable-root PATH. Governs DATA (segments/) only;
# this script has no sibling to resolve, so there is no --plugin-root and
# nothing to forward (unlike select_segments.py/ledger_merge.py/
# resume_setup.py/review_ready.py, each of which shells out to at least one
# sibling script).
# ---------------------------------------------------------------------------

def test_durable_root_flag_omitted_preserves_todays_behavior(tmp_path):
    """Backward compatibility: the ordinary in-place fixture, invoked with no
    --durable-root at all, behaves exactly as before."""
    root = make_durable_root(tmp_path)
    write_segment(root, "seg01", clean_segpack(), clean_draft())

    result = run_draft_ready(root, "seg01")

    assert result.returncode == 0
    assert "[seg01] READY" in result.stdout


def test_durable_root_flag_omitted_is_byte_identical_to_explicit_self_root(tmp_path):
    """An explicit --durable-root pointing at the SAME root the script would
    have self-anchored to anyway must produce byte-identical stdout -- proof
    the flag changes nothing when it names today's own location."""
    root = make_durable_root(tmp_path)
    write_segment(root, "seg01", clean_segpack(), clean_draft())

    without = run_draft_ready(root, "seg01")
    with_flag = run_draft_ready(root, "seg01", "--durable-root", str(root))

    assert without.returncode == with_flag.returncode == 0
    assert without.stdout == with_flag.stdout


def test_durable_root_flag_redirects_data_reads_orphan_copy(tmp_path):
    """The core property, proven via an ORPHAN COPY: the script file sits at
    a location with NO segments/ co-located at all, so success is possible
    ONLY if --durable-root actually redirected the data read -- co-location
    cannot be what made this pass."""
    data_root = make_durable_root(tmp_path, name="data_only")
    write_segment(data_root, "seg01", clean_segpack(), clean_draft())

    orphan_dir = tmp_path / "orphan_location" / "scripts"
    orphan_dir.mkdir(parents=True)
    orphan_script = orphan_dir / "draft_ready.py"
    shutil.copy2(SCRIPT_SRC, orphan_script)
    assert not (orphan_dir.parent / "segments").exists(), (
        "fixture bug: the orphan location must have NO co-located segments/"
    )

    result = run_draft_ready_from(orphan_script, "seg01", "--durable-root", str(data_root))

    assert result.returncode == 0, (
        f"--durable-root must redirect data reads to {data_root} even though "
        f"the script itself has no co-located segments/ at all:\n"
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert "[seg01] READY" in result.stdout


def test_durable_root_flag_absent_orphan_copy_fails_self_anchored(tmp_path):
    """Negative control, and proof the positive test above is attributable
    to --durable-root specifically: the SAME orphan copy, invoked WITHOUT
    the flag, cannot succeed via self-anchoring (no segments/ next to it at
    all)."""
    data_root = make_durable_root(tmp_path, name="data_only")
    write_segment(data_root, "seg01", clean_segpack(), clean_draft())

    orphan_dir = tmp_path / "orphan_location2" / "scripts"
    orphan_dir.mkdir(parents=True)
    orphan_script = orphan_dir / "draft_ready.py"
    shutil.copy2(SCRIPT_SRC, orphan_script)

    result = run_draft_ready_from(orphan_script, "seg01")  # no --durable-root

    assert result.returncode == 1
    assert "absent/empty" in result.stdout


def test_durable_root_flag_redirects_the_not_ready_diagnosis_too(tmp_path):
    """The redirect applies to every code path, not just the happy one: a
    "segpack missing" diagnosis, read via --durable-root from an orphan
    copy, must name the REDIRECTED path (proving the data root really
    moved), not the orphan location's own (nonexistent) segments/."""
    data_root = make_durable_root(tmp_path, name="data_only")
    (data_root / "segments" / "seg01.draft.json").write_text(
        json.dumps(clean_draft(), ensure_ascii=False), encoding="utf-8"
    )
    # No segpack at the data root.

    orphan_dir = tmp_path / "orphan_location3" / "scripts"
    orphan_dir.mkdir(parents=True)
    orphan_script = orphan_dir / "draft_ready.py"
    shutil.copy2(SCRIPT_SRC, orphan_script)

    result = run_draft_ready_from(orphan_script, "seg01", "--durable-root", str(data_root))

    assert result.returncode == 1
    assert "segpack missing" in result.stdout
    assert str(data_root / "segments" / "segpack_seg01.json") in result.stdout, (
        f"the reported path must be under the REDIRECTED data root, not the "
        f"orphan script's own location:\n{result.stdout}"
    )


def test_durable_root_flag_combines_with_candidate_file(tmp_path):
    """--durable-root (governs the SEGPACK's canonical location, plus the
    draft's canonical fallback) and --candidate-file (overrides ONLY where
    the draft is read from) are independent -- both apply together from an
    orphan copy."""
    data_root = make_durable_root(tmp_path, name="data_only")
    (data_root / "segments" / "segpack_seg01.json").write_text(
        json.dumps(clean_segpack(), ensure_ascii=False), encoding="utf-8"
    )
    candidate = data_root / "segments" / ".att.seg01.1.draft.json"
    candidate.write_text(json.dumps(clean_draft(), ensure_ascii=False), encoding="utf-8")
    # No canonical seg01.draft.json at all -- proves --candidate-file, not
    # the canonical path, is what gets read.

    orphan_dir = tmp_path / "orphan_location4" / "scripts"
    orphan_dir.mkdir(parents=True)
    orphan_script = orphan_dir / "draft_ready.py"
    shutil.copy2(SCRIPT_SRC, orphan_script)

    result = run_draft_ready_from(
        orphan_script, "seg01",
        "--durable-root", str(data_root),
        "--candidate-file", str(candidate),
    )

    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert "[seg01] READY" in result.stdout


# ---------------------------------------------------------------------------
# --expect-token TOK -- 1.2.0 addition, closes the stale/straggler-draft-
# from-a-different-run gap (module docstring). Previously untested anywhere:
# this file never passed the flag at all, and codex_job_driver.test.py's own
# --expect-token uses go through a FAKE draft_ready.py stub, never the real
# script. Confirmed by mutation: `if token != args.expect_token:` -> `if
# False:` in main() survives the whole battery. The fixtures below reuse
# clean_segpack()/clean_draft() UNCHANGED -- the exact pair
# test_clean_baseline_is_ready above already proves passes every OTHER gate
# (schema shape, seg==seg, key sets) -- so --expect-token is the ONLY
# variable between this section's tests and that proven-clean baseline.
# ---------------------------------------------------------------------------


def test_expect_token_mismatch_is_not_ready(tmp_path):
    """PROOF. clean_draft() carries no dispatch_token field at all, so
    draft.get("dispatch_token") is None -- which cannot equal any
    --expect-token value. Isolates the token check as the ONLY thing
    deciding: every other gate is satisfied identically to
    test_clean_baseline_is_ready's own proven-passing fixture."""
    root = make_durable_root(tmp_path)
    write_segment(root, "seg01", clean_segpack(), clean_draft())

    result = run_draft_ready(root, "seg01", "--expect-token", "RUN1:seg01")

    assert result.returncode == 1, (
        f"a draft with no dispatch_token cannot satisfy --expect-token -- "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert "dispatch_token mismatch" in result.stdout
    assert "RUN1:seg01" in result.stdout


def test_expect_token_match_is_ready(tmp_path):
    """Pairing for the PROOF above: the SAME clean fixture, draft's
    dispatch_token now set to EXACTLY the value --expect-token names.
    Without this, the mismatch test alone could not distinguish "the check
    fires on any given token" from "the check always refuses" -- a mutation
    to `if True:` would pass the mismatch test just as wrongly as `if
    False:` passes it today."""
    root = make_durable_root(tmp_path)
    draft = clean_draft()
    draft["dispatch_token"] = "RUN1:seg01"
    write_segment(root, "seg01", clean_segpack(), draft)

    result = run_draft_ready(root, "seg01", "--expect-token", "RUN1:seg01")

    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert "[seg01] READY" in result.stdout


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
