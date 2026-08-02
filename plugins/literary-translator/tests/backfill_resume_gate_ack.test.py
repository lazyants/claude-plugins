#!/usr/bin/env python3
"""#409 Step 3 -- backfill_resume_gate_ack.py, the retrofit that lets a
project which dispatched work before the resume-integrity gate existed get
past select_segments.py's new refusal WITHOUT forging evidence.

The gate itself is tested in tests/resume_gate_skip_detection.test.py,
including the drift pins tying this script's copies of the Step 3 primitives
to select_segments.py's. This file covers the writer's own contract.

The single most important test here is
test_never_writes_an_input_digest: the whole design rests on the retrofit
acknowledging a gap rather than fabricating a proof, and an implementation
that "helpfully" wrote a digest computed from today's inputs would satisfy
every other test in this file while silently re-arming the exact failure the
gate exists to prevent -- a later resume treating a match as authorization to
reuse results that were never validated against those inputs.
"""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
ASSETS_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets"
BACKFILL_ACK_SRC = ASSETS_DIR / "scripts" / "backfill_resume_gate_ack.py"

assert BACKFILL_ACK_SRC.is_file(), f"backfill_resume_gate_ack.py not found at {BACKFILL_ACK_SRC}"

spec = importlib.util.spec_from_file_location("backfill_resume_gate_ack_uut", str(BACKFILL_ACK_SRC))
BACKFILL = importlib.util.module_from_spec(spec)
spec.loader.exec_module(BACKFILL)


def make_root(tmp_path, dispatched=None, digested=(), workflows=()):
    """`dispatched` maps seg -> run_id and produces one tokened draft each."""
    root = tmp_path / "durable_root"
    (root / "segments").mkdir(parents=True)
    (root / "runs").mkdir(parents=True)
    for seg, run_id in (dispatched or {}).items():
        (root / "segments" / f"{seg}.draft.json").write_text(
            json.dumps({"seg": seg, "dispatch_token": f"{run_id}:{seg}"}), encoding="utf-8"
        )
    for run_id in digested:
        (root / "runs" / run_id).mkdir(parents=True, exist_ok=True)
        (root / "runs" / run_id / "input.digest").write_text("deadbeef\n", encoding="utf-8")
    for name in workflows:
        (root / "runs" / "workflows" / name).mkdir(parents=True, exist_ok=True)
    return root


def run_backfill(root, *extra_args, timeout=60):
    return subprocess.run(
        [sys.executable, str(BACKFILL_ACK_SRC), "--durable-root", str(root), *extra_args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def parse_stdout(proc):
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    assert len(lines) == 1, f"expected exactly one JSON line, got:\n{proc.stdout}"
    return json.loads(lines[0])


def snapshot(root):
    """Every path under `root` plus each file's bytes -- for proving a dry
    run wrote absolutely nothing, not merely that it wrote no marker."""
    out = {}
    for p in sorted(root.rglob("*")):
        out[str(p.relative_to(root))] = p.read_bytes() if p.is_file() else None
    return out


# ===========================================================================
# The contract that matters most.
# ===========================================================================


def test_never_writes_an_input_digest(tmp_path):
    """Acknowledging a gap must never look like closing it. If this ever
    fails, the retrofit has started forging the very proof the gate checks
    for, and every other test here would still pass."""
    root = make_root(tmp_path, dispatched={"seg01": "OLDRUN"})

    proc = run_backfill(root, "--apply")

    assert proc.returncode == 0, proc.stdout
    assert not (root / "runs" / "OLDRUN" / "input.digest").exists(), (
        "the retrofit fabricated an input.digest -- this re-arms the exact "
        "unsafe-resume the gate exists to prevent"
    )
    assert (root / "runs" / "OLDRUN" / ".resume_gate_ack").is_file()
    body = json.loads((root / "runs" / "OLDRUN" / ".resume_gate_ack").read_text(encoding="utf-8"))
    assert body["gate_ran"] is False, (
        "the marker must state explicitly that the gate did NOT run, so it "
        "cannot later be misread as evidence that it did"
    )
    assert body["run_id"] == "OLDRUN"
    assert body["acknowledged_at"].endswith("Z")
    assert body["dispatched_segs"] == ["seg01"]


# ===========================================================================
# Dry run / apply.
# ===========================================================================


def test_dry_run_writes_absolutely_nothing(tmp_path):
    root = make_root(tmp_path, dispatched={"seg01": "OLDRUN"})
    before = snapshot(root)

    proc = run_backfill(root)

    assert proc.returncode == 0, proc.stdout
    payload = parse_stdout(proc)
    assert payload["applied"] is False
    assert payload["needs_ack"] == ["OLDRUN"]
    assert payload["created"] == []
    assert snapshot(root) == before, "a dry run must make ZERO filesystem writes"


def test_apply_acknowledges_exactly_the_ungated_runs(tmp_path):
    root = make_root(
        tmp_path,
        dispatched={"seg01": "OLDRUN", "seg02": "GOODRUN"},
        digested=["GOODRUN"],
    )

    payload = parse_stdout(run_backfill(root, "--apply"))

    assert payload["created"] == ["OLDRUN"]
    assert payload["gated_run_ids"] == ["GOODRUN"]
    assert not (root / "runs" / "GOODRUN" / ".resume_gate_ack").exists(), (
        "a run whose gate demonstrably ran must never be acknowledged -- an "
        "acknowledgement there would be a false record of a gap"
    )


def test_apply_is_idempotent(tmp_path):
    root = make_root(tmp_path, dispatched={"seg01": "OLDRUN"})
    first = parse_stdout(run_backfill(root, "--apply"))
    assert first["created"] == ["OLDRUN"]
    marker = root / "runs" / "OLDRUN" / ".resume_gate_ack"
    body_after_first = marker.read_bytes()

    second = run_backfill(root, "--apply")

    payload = parse_stdout(second)
    assert payload["success"] is False, (
        "a second run has nothing left to acknowledge, which the --allow-empty "
        "guard must surface rather than report as a silent success"
    )
    assert marker.read_bytes() == body_after_first, "the existing marker must never be rewritten"

    third = parse_stdout(run_backfill(root, "--apply", "--allow-empty"))
    assert third["already_acknowledged"] == ["OLDRUN"]
    assert third["created"] == []


# ===========================================================================
# Guards.
# ===========================================================================


def test_zero_to_acknowledge_fatals_without_allow_empty(tmp_path):
    """An already-compliant project and a scan that matched nothing emit
    nearly identical reports, differing only in a count nobody watches."""
    root = make_root(tmp_path, dispatched={"seg01": "GOODRUN"}, digested=["GOODRUN"])

    proc = run_backfill(root, "--apply")

    assert proc.returncode != 0
    payload = parse_stdout(proc)
    assert payload["success"] is False
    assert "--allow-empty" in payload["error"]
    assert payload["drafts_scanned"] == 1, (
        "the refusal must report what the scan actually saw, so a wrong "
        "--durable-root is distinguishable from a compliant project"
    )


def test_empty_project_reports_its_zero_scan(tmp_path):
    root = make_root(tmp_path)

    payload = parse_stdout(run_backfill(root, "--allow-empty"))

    assert payload["dispatching_run_ids"] == []
    assert payload["drafts_scanned"] == 0


def test_only_runs_fatals_on_a_run_id_no_draft_reports(tmp_path):
    root = make_root(tmp_path, dispatched={"seg01": "OLDRUN"})

    proc = run_backfill(root, "--apply", "--only-runs", "OLDRUN,TYPO_RUN")

    assert proc.returncode != 0
    payload = parse_stdout(proc)
    assert "TYPO_RUN" in payload["error"]
    assert not (root / "runs" / "OLDRUN" / ".resume_gate_ack").exists(), (
        "the refusal must happen BEFORE any write, so a typo cannot half-apply"
    )


def test_only_runs_narrows_to_the_named_run(tmp_path):
    root = make_root(tmp_path, dispatched={"seg01": "RUN_A", "seg02": "RUN_B"})

    payload = parse_stdout(run_backfill(root, "--apply", "--only-runs", "RUN_A"))

    assert payload["created"] == ["RUN_A"]
    assert not (root / "runs" / "RUN_B" / ".resume_gate_ack").exists()


def test_workflow_dirs_are_part_of_the_work_list_not_merely_reported(tmp_path):
    """The retrofit must acknowledge BOTH evidence halves, because the gate
    blocks on their union. A workflow-directory-only run id is the shape the
    draft scan structurally cannot see -- a draft holds only its most recent
    dispatch token -- and if it were merely *reported* here rather than
    acknowledged, the gate would refuse forever with no way to clear it.
    tests/resume_gate_skip_detection.test.py drives that round trip end to
    end; this pins the writer's own half of it."""
    root = make_root(
        tmp_path,
        dispatched={"seg01": "RUN_A"},
        workflows=["RUN_A", "RUN_NEVER_DISPATCHED"],
    )

    payload = parse_stdout(run_backfill(root))

    assert payload["needs_ack"] == ["RUN_A", "RUN_NEVER_DISPATCHED"], (
        "both evidence halves must drive the work list"
    )
    assert payload["workflow_run_ids"] == ["RUN_A", "RUN_NEVER_DISPATCHED"]
    assert payload["run_id_evidence"] == {
        "RUN_A": ["drafts", "workflow_dir"],
        "RUN_NEVER_DISPATCHED": ["workflow_dir"],
    }


def test_a_workflow_only_run_is_acknowledged_with_an_empty_seg_list(tmp_path):
    """A workflow-dir-only run id has no drafts pointing at it by definition,
    so its marker records an empty `dispatched_segs`. That is the expected
    shape, not a missing value -- the marker's own `evidence` field is what
    says why the run was in scope."""
    root = make_root(tmp_path, dispatched={"seg01": "RUN_A"}, workflows=["WF_ONLY"])

    payload = parse_stdout(run_backfill(root, "--apply"))

    assert "WF_ONLY" in payload["created"]
    body = json.loads((root / "runs" / "WF_ONLY" / ".resume_gate_ack").read_text(encoding="utf-8"))
    assert body["dispatched_segs"] == []
    assert body["evidence"] == ["workflow_dir"]
    assert body["gate_ran"] is False


def test_unsafe_run_id_in_a_draft_token_is_refused(tmp_path):
    """The run id becomes a directory name, so it is re-validated here rather
    than trusted transitively from whatever wrote the draft."""
    root = make_root(tmp_path)
    (root / "segments" / "seg01.draft.json").write_text(
        json.dumps({"seg": "seg01", "dispatch_token": "../escape:seg01"}), encoding="utf-8"
    )

    proc = run_backfill(root, "--apply")

    assert proc.returncode != 0
    payload = parse_stdout(proc)
    assert payload["success"] is False
    assert "unsafe RUN_ID" in payload["error"]
    assert not (root / "runs" / ".." / "escape").exists()
