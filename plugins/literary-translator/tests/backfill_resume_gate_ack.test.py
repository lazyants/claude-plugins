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

## Three later fixes, and the tests that pin each

  * **Physical-path safety.** `write_ack()` used to resolve `runs_dir /
    run_id` as a plain path string, which transparently follows a symlink
    planted at either component -- confirmed to write the marker OUTSIDE
    the durable root before this fix existed. Now anchored via
    `os.O_NOFOLLOW`-protected directory file descriptors, one component at
    a time. See test_write_ack_refuses_a_symlinked_run_directory and
    test_write_ack_refuses_a_symlinked_runs_parent.
  * **Atomicity.** The marker used to become visible (`O_CREAT | O_EXCL`)
    BEFORE the single `os.write()` call that filled it in, with no error
    handling around that call at all -- confirmed pre-fix to leave a
    permanent 0-byte marker (and an uncaught crash) on a simulated write
    failure, un-fixable by any retry because the next run sees
    "already_present" and moves on. Now published via write-temp-fsync-then-
    `os.link()`, so a failure anywhere in the write step leaves nothing at
    the final name. See
    test_a_write_failure_never_leaves_a_partial_marker_and_is_retryable.
  * **Audit accuracy.** The marker's own `note` field used to say "This run
    dispatched work" unconditionally, even for a run id whose only evidence
    is a `runs/workflows/<RUN_ID>/` directory with zero drafts pointing at
    it (a legitimate, documented shape -- see
    test_a_workflow_only_run_is_acknowledged_with_an_empty_seg_list, which
    already proved `dispatched_segs` is legitimately `[]` for that shape but
    never checked what `note` said about it). The note now branches on
    whether anything was actually dispatched.
"""

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
ASSETS_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets"
BACKFILL_ACK_SRC = ASSETS_DIR / "scripts" / "backfill_resume_gate_ack.py"
CODEX_JOB_SRC = ASSETS_DIR / "scripts" / "codex_job.py"

assert BACKFILL_ACK_SRC.is_file(), f"backfill_resume_gate_ack.py not found at {BACKFILL_ACK_SRC}"

spec = importlib.util.spec_from_file_location("backfill_resume_gate_ack_uut", str(BACKFILL_ACK_SRC))
BACKFILL = importlib.util.module_from_spec(spec)
spec.loader.exec_module(BACKFILL)

assert CODEX_JOB_SRC.is_file(), f"codex_job.py not found at {CODEX_JOB_SRC}"

_cj_spec = importlib.util.spec_from_file_location("codex_job_uut", str(CODEX_JOB_SRC))
CODEX_JOB = importlib.util.module_from_spec(_cj_spec)
_cj_spec.loader.exec_module(CODEX_JOB)


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
    assert "dispatched work" in body["note"], (
        "the positive control for the audit-accuracy fix: a run id that "
        "genuinely dispatched something must still say so"
    )


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
    # Audit-accuracy fix: dispatched_segs == [] must not be paired with a
    # note that still claims dispatch happened. This is the exact defect
    # codex found -- this test's OWN fixture already proved the shape is
    # legitimate, but never checked what the durable record SAID about it.
    assert "dispatched work" not in body["note"], (
        f"a workflow-dir-only run id never dispatched anything -- the note "
        f"must not claim it did. note={body['note']!r}"
    )
    assert "instantiated" in body["note"]


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


# ===========================================================================
# Physical-path safety -- validate_run_id() constrains the STRING SHAPE of a
# run id; it says nothing about what actually sits on disk at that name. A
# validated, safely-shaped run id can still have a symlink planted at its
# directory, and the pre-fix write_ack() followed it. write_ack() is a pure
# function of its own arguments, so these call it directly rather than
# through the CLI/scan machinery -- the symlink is set up by hand at exactly
# the level write_ack() touches.
# ===========================================================================


def test_write_ack_refuses_a_symlinked_run_directory(tmp_path):
    """Confirmed pre-fix (this exact fixture, against the parent commit's
    copy of the script): write_ack() wrote the marker INSIDE the external
    symlink target, outside the durable root entirely. Post-fix,
    os.O_NOFOLLOW-anchored directory descriptors refuse to follow it."""
    runs = tmp_path / "runs"
    runs.mkdir()
    external = tmp_path / "external_target"
    external.mkdir()
    (runs / "OLDRUN").symlink_to(external, target_is_directory=True)

    outcome = BACKFILL.write_ack("OLDRUN", runs, ["seg01"], evidence=["drafts"])

    assert outcome.startswith("error:"), (
        f"a symlinked run directory must be refused, not silently followed: {outcome}"
    )
    assert not (external / ".resume_gate_ack").exists(), (
        "the marker must never be written through the symlink into the "
        "external target -- this is the actual data-escape the fix closes"
    )
    assert not (runs / "OLDRUN" / ".resume_gate_ack").exists()


def test_write_ack_refuses_a_symlinked_runs_parent(tmp_path):
    """The second shape the same defect covers: runs/ ITSELF is the
    symlink, not just the per-run_id directory under it."""
    external = tmp_path / "external_target"
    external.mkdir()
    runs = tmp_path / "runs"
    runs.symlink_to(external, target_is_directory=True)

    outcome = BACKFILL.write_ack("OLDRUN", runs, ["seg01"], evidence=["drafts"])

    assert outcome.startswith("error:"), (
        f"a symlinked runs/ parent must be refused, not silently followed: {outcome}"
    )
    assert not (external / "OLDRUN").exists(), (
        "nothing must be created inside the external target at all"
    )


# ===========================================================================
# Atomicity -- the marker's final name must never be visible in a partial
# state. Simulated by monkeypatching os.write (which write_ack() calls via
# its own module-level `os` reference, so patching BACKFILL.os.write reaches
# it) to fail exactly once, the way an interrupted/ENOSPC write would.
# ===========================================================================


def test_a_write_failure_never_leaves_a_partial_marker_and_is_retryable(tmp_path, monkeypatch):
    """Confirmed pre-fix (this exact fixture): the failure propagated as an
    UNCAUGHT OSError out of write_ack() -- but not before O_CREAT|O_EXCL had
    already made a 0-byte file visible at the final name. A retry, once the
    transient failure clears, saw "already_present" and left the 0-byte
    marker corrupt forever -- select_segments.py's own gate would have
    trusted that empty file as a valid acknowledgement permanently, with no
    automated way to recover short of a human deleting it by hand.

    Post-fix: the failure is caught and reported as an "error: ..." string,
    nothing is visible at the final name afterward, and an immediate retry
    (once os.write works again) succeeds cleanly with the full correct
    body -- proving this is actually retryable, not merely "does not
    crash"."""
    root = make_root(tmp_path, dispatched={"seg01": "OLDRUN"})
    marker = root / "runs" / "OLDRUN" / ".resume_gate_ack"

    def failing_write(fd, data):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(BACKFILL.os, "write", failing_write)
    outcome = BACKFILL.write_ack("OLDRUN", root / "runs", ["seg01"], evidence=["drafts"])
    monkeypatch.undo()

    assert outcome.startswith("error:"), (
        f"a write failure must be reported, not silently swallowed: {outcome}"
    )
    assert not marker.exists(), (
        "a failed write must never leave anything at the final marker name "
        "-- both select_segments.py and this script's own run() trust a "
        f"bare .exists() there. marker exists with content: "
        f"{marker.read_bytes() if marker.exists() else None!r}"
    )
    leftovers = list((root / "runs" / "OLDRUN").glob(".resume_gate_ack.tmp.*"))
    assert leftovers == [], f"temp scratch file(s) left behind: {leftovers}"

    retry_outcome = BACKFILL.write_ack("OLDRUN", root / "runs", ["seg01"], evidence=["drafts"])
    assert retry_outcome == "created", (
        f"the failure must be RETRYABLE without manual intervention, not "
        f"just non-crashing: {retry_outcome}"
    )
    assert json.loads(marker.read_text(encoding="utf-8"))["run_id"] == "OLDRUN"


# ===========================================================================
# #428 -- this script's own copy of `scan_dispatching_run_ids()` must ignore
# `codex_job.py`'s private per-segment slot files. Its loop globs
# `segments/*.draft.json`, and `pathlib.Path.glob` matches DOT-PREFIXED
# names, so `.att.<seg>.<INV>.draft.json` and `.att_pending.<seg>.draft.json`
# were both read as canonical drafts and attributed by their own
# `dispatch_token`.
#
# The reader's identical copy is covered in tests/resume_gate_skip_detection
# .test.py, including a drift pin over both. This test exists so the WRITER
# cannot regress alone: each script owns its own copy, and a fix landing in
# only one of them is exactly the divergence that let this defect sit in
# both (the reader had already been rewritten from `glob` to `iterdir`
# without either copy changing what it admitted).
# ===========================================================================


def test_slot_files_are_not_counted_or_attributed_as_drafts(tmp_path):
    """One real draft plus BOTH slot files for the same seg, all carrying
    the SAME valid token. Pre-fix: `drafts_scanned=3` and the seg listed
    three times under one run id.

    Slot names are built by constructing the REAL producer rather than typed
    here, so a rename of either slot moves this fixture with it."""
    root = make_root(tmp_path, dispatched={"seg01": "RUN20260804T090001Z"})
    job = CODEX_JOB.CodexJob(
        kind="translate",
        seg="seg01",
        tok="RUN20260804T090001Z:seg01",
        disp="d1",
        root=str(root),
        companion=str(root / "companion.mjs"),
        prompt_text="p",
        prompt_file=str(root / "prompt.txt"),
        deadline_sec=100,
        poll_sec=1,
        effort="high",
        node="node",
    )
    slot_doc = json.dumps({"seg": "seg01", "dispatch_token": "RUN20260804T090001Z:seg01"})
    for slot in (Path(job.attempt), Path(job.pending)):
        assert slot.name.startswith("."), f"fixture precondition: {slot.name}"
        assert slot.name.endswith(".draft.json"), (
            "fixture precondition: the slot must collide with the globbed "
            f"suffix, otherwise this test proves nothing. {slot.name}"
        )
        slot.write_text(slot_doc, encoding="utf-8")

    scan = BACKFILL.scan_dispatching_run_ids(root / "segments")

    assert scan == {
        "by_run_id": {"RUN20260804T090001Z": ["seg01"]},
        "drafts_scanned": 1,
        "drafts_untokened": 0,
    }, scan
