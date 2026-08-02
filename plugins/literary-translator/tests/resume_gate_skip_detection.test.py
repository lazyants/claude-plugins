#!/usr/bin/env python3
"""#409 Step 3 -- select_segments.py refuses when a prior RUN_ID dispatched
work into a project WITHOUT the resume-integrity gate.

## Which of these tests are PROOFS and which are BOUNDS

This distinction is load-bearing and must not be lost in a later edit.

PROOFS -- these go RED if the check is deleted or broken, and they are the
only tests that may ever be cited as evidence the check works:

  * test_refuses_when_a_dispatched_run_id_has_no_input_digest
  * test_acknowledged_run_stops_blocking_but_a_new_skip_still_fires
  * test_frontback_seg_id_attributes_to_the_right_run_id
  * test_workflow_dir_alone_is_enough_to_refuse
  * test_the_retrofit_can_clear_every_refusal_the_gate_can_raise

FALSE-POSITIVE BOUNDS -- these assert the check does NOT fire on a healthy
project. Every one of them STAYS GREEN if the check is deleted entirely,
because a deleted check also does not fire. They bound the damage; they
prove nothing about detection, and citing them as evidence the gate works
would be exactly the mistake this file exists to make impossible:

  * test_allows_once_that_run_id_has_its_digest
  * test_does_not_fire_on_a_project_with_no_dispatched_drafts
  * test_compliant_and_busy_project_passes_and_reports_its_run_ids
  * test_classify_only_reports_the_evidence_without_refusing

The exact-list assertions on `runs_missing_digest` (never truthiness) and
the `drafts_scanned` assertions are what make the PROOFS non-vacuous. A scan
that silently matched nothing -- wrong glob, wrong directory, a fixture that
forgot the dispatch_token -- produces an empty list and would otherwise pass
a "did it refuse?" assertion for entirely the wrong reason.

## The three real on-disk states these fixtures mirror

Measured on the live projects that motivated the check, so the fixtures are
characterizations of real shapes and not invented ones:

  gate skipped       -- four hand-labelled run ids, 80 tokened drafts, zero
                        input.digest files.
  compliant and busy -- three dispatching run ids, every one with its
                        digest, PLUS further digests for runs that resolved
                        but dispatched nothing yet. This is the state a real
                        working project sits in, and the one a broken scan
                        would most plausibly break.
  first run ever     -- a manifest and no tokened draft at all.
"""

import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
ASSETS_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets"
SELECT_SCRIPT_SRC = ASSETS_DIR / "scripts" / "select_segments.py"
LEDGER_MERGE_SRC = ASSETS_DIR / "scripts" / "ledger_merge.py"
BACKFILL_ACK_SRC = ASSETS_DIR / "scripts" / "backfill_resume_gate_ack.py"
SCHEMAS_SRC = ASSETS_DIR / "schemas"

assert SELECT_SCRIPT_SRC.is_file(), f"select_segments.py not found at {SELECT_SCRIPT_SRC}"
assert LEDGER_MERGE_SRC.is_file(), f"ledger_merge.py not found at {LEDGER_MERGE_SRC}"
assert BACKFILL_ACK_SRC.is_file(), f"backfill_resume_gate_ack.py not found at {BACKFILL_ACK_SRC}"
assert SCHEMAS_SRC.is_dir(), f"schemas dir not found at {SCHEMAS_SRC}"

# Minimal stand-in for cache_key.py. None of these fixtures classify a
# converged segment (every segment is `recoverable` or `not_started`, both of
# which return before classify_converged_segment ever shells out), so this
# only has to exist and print a JSON object if it is ever reached.
FAKE_CACHE_KEY_PY = """#!/usr/bin/env python3
import argparse
import json
import sys

p = argparse.ArgumentParser()
p.add_argument("--seg", required=True)
p.add_argument("--durable-root", default=None)
a = p.parse_args()
print(json.dumps({"seg": a.seg}))
sys.exit(0)
"""


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SELECT = _load("select_segments_under_test", SELECT_SCRIPT_SRC)
BACKFILL_ACK = _load("backfill_resume_gate_ack_under_test", BACKFILL_ACK_SRC)


def make_durable_root(tmp_path):
    """An isolated durable_root holding the REAL select_segments.py and
    ledger_merge.py plus the REAL schemas -- same shape as
    select_segments.test.py's own `make_durable_root()`."""
    root = tmp_path / "durable_root"
    scripts_dir = root / "scripts"
    scripts_dir.mkdir(parents=True)
    shutil.copy2(SELECT_SCRIPT_SRC, scripts_dir / "select_segments.py")
    shutil.copy2(LEDGER_MERGE_SRC, scripts_dir / "ledger_merge.py")
    shutil.copy2(BACKFILL_ACK_SRC, scripts_dir / "backfill_resume_gate_ack.py")
    (scripts_dir / "cache_key.py").write_text(FAKE_CACHE_KEY_PY, encoding="utf-8")

    shutil.copytree(SCHEMAS_SRC, root / "schemas")
    (root / "runs" / "ledger.d").mkdir(parents=True)
    (root / "segments").mkdir()
    return root


def write_manifest(root, seg_ids):
    (root / "manifest.json").write_text(
        json.dumps({"segments": [{"seg": s} for s in seg_ids]}, ensure_ascii=False),
        encoding="utf-8",
    )


def write_in_progress_fragment(root, seg):
    """`in_progress` classifies as `recoverable`, which is dispatch-eligible
    and needs no cache key -- so every fixture below gets past the empty-SEGS
    refusal and reaches the Step 3 gate without a converged-segment setup."""
    (root / "runs" / "ledger.d" / f"{seg}.json").write_text(
        json.dumps({"timestamp": "2026-08-01T00:00:00Z", "status": "in_progress"}),
        encoding="utf-8",
    )


def write_draft(root, seg, dispatch_token=None):
    """A draft at select_segments.py's own canonical `draft_path()` location.
    `dispatch_token=None` writes a draft WITHOUT the field -- the real,
    schema-permitted shape (draft.schema.json lists dispatch_token in
    properties but not in required)."""
    content = {"seg": seg, "blocks": [], "footnotes": [], "verses": [], "names": [], "notes": []}
    if dispatch_token is not None:
        content["dispatch_token"] = dispatch_token
    raw = json.dumps(content, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    (root / "segments" / f"{seg}.draft.json").write_bytes(raw)
    return hashlib.sha1(raw).hexdigest()


def write_digest(root, run_id, value="deadbeef"):
    """resume_setup.py's own artifact: runs/<RUN_ID>/input.digest."""
    run_dir = root / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "input.digest").write_text(value + "\n", encoding="utf-8")


def write_ack(root, run_id):
    """What backfill_resume_gate_ack.py --apply leaves behind. Written here
    through the REAL script's own writer, never a hand-rolled copy, so a
    change to its filename or semantics cannot leave this fixture asserting
    against a marker the shipped code no longer writes."""
    outcome = BACKFILL_ACK.write_ack(run_id, root / "runs", ["seg_whatever"])
    assert outcome == "created", outcome


def run_select(root, *extra_args, timeout=60):
    return subprocess.run(
        [sys.executable, str(root / "scripts" / "select_segments.py"), *extra_args],
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


def skipped_gate_project(tmp_path, run_id="HANDLABEL20260801"):
    """The tome1 shape, minimized: one eligible segment whose draft carries a
    dispatch_token, and NO runs/<run_id>/ directory at all."""
    root = make_durable_root(tmp_path)
    write_manifest(root, ["seg01"])
    write_in_progress_fragment(root, "seg01")
    write_draft(root, "seg01", dispatch_token=f"{run_id}:seg01")
    return root


# ===========================================================================
# PROOF 1 -- the load-bearing negative. Goes RED if the check is deleted.
# ===========================================================================


def test_refuses_when_a_dispatched_run_id_has_no_input_digest(tmp_path):
    """A run id that demonstrably dispatched work (its token is on a draft)
    and has no input.digest must refuse the whole invocation.

    The `== ["HANDLABEL20260801"]` assertion is deliberate and must never be
    relaxed to a truthiness or membership check: a refusal that fired for
    some OTHER reason, or a scan that matched zero drafts and reported an
    empty list, would both satisfy a weaker assertion while proving nothing.
    """
    root = skipped_gate_project(tmp_path)

    proc = run_select(root)

    assert proc.returncode != 0, f"expected a refusal. stdout={proc.stdout!r}"
    payload = parse_stdout(proc)
    assert payload["success"] is False
    assert payload["runs_missing_digest"] == ["HANDLABEL20260801"], payload
    assert payload["drafts_scanned"] == 1, (
        "the scan must have actually READ the draft -- a zero here means the "
        "refusal above fired without evidence"
    )
    assert "HANDLABEL20260801" in payload["error"]
    assert "backfill_resume_gate_ack.py" in payload["error"], (
        "the refusal must name the remedy; there is deliberately no flag"
    )


# ===========================================================================
# PROOF 2 -- acknowledgement is per-RUN_ID and does NOT disable the gate.
# Without this test, "acknowledged one run" and "switched the gate off" are
# indistinguishable. Goes RED if the ack is ever widened to project scope.
# ===========================================================================


def test_acknowledged_run_stops_blocking_but_a_new_skip_still_fires(tmp_path):
    root = make_durable_root(tmp_path)
    write_manifest(root, ["seg01", "seg02"])
    write_in_progress_fragment(root, "seg01")
    write_in_progress_fragment(root, "seg02")
    write_draft(root, "seg01", dispatch_token="OLDRUN20260701:seg01")
    write_draft(root, "seg02", dispatch_token="NEWRUN20260801:seg02")
    # Neither has a digest; only the OLD one is acknowledged.
    write_ack(root, "OLDRUN20260701")

    proc = run_select(root)

    assert proc.returncode != 0, f"the un-acknowledged run must still refuse. stdout={proc.stdout!r}"
    payload = parse_stdout(proc)
    assert payload["runs_missing_digest"] == ["NEWRUN20260801"], (
        "acknowledging one run id must clear exactly that run id and nothing "
        f"else -- a project-level off-switch would have emptied this list. {payload}"
    )


# ===========================================================================
# PROOF 3 -- a FRONTBACK seg id must not corrupt run-id attribution.
# The token is `<RUN_ID>:<seg>` and a seg id may itself contain a colon, so a
# right-split implementation reads the wrong half. Goes RED on that mutation.
# ===========================================================================


def test_frontback_seg_id_attributes_to_the_right_run_id(tmp_path):
    root = make_durable_root(tmp_path)
    write_manifest(root, ["FRONTBACK:fm04"])
    write_in_progress_fragment(root, "FRONTBACK:fm04")
    write_draft(root, "FRONTBACK:fm04", dispatch_token="RUNX20260801:FRONTBACK:fm04")

    proc = run_select(root)

    assert proc.returncode != 0
    payload = parse_stdout(proc)
    assert payload["runs_missing_digest"] == ["RUNX20260801"], (
        "the run id must come from the FIRST colon; a right-split would "
        f"report 'RUNX20260801:FRONTBACK'. {payload}"
    )


def test_draft_run_id_parses_the_shapes_that_actually_occur():
    """Unit-level companion to PROOF 3, pinning the parser directly."""
    assert SELECT.draft_run_id("20260801T090001Z:seg01") == "20260801T090001Z"
    assert SELECT.draft_run_id("w5-batch1-20260801T0500:FRONTBACK:fm04") == "w5-batch1-20260801T0500"
    for bad in ("nocolon", "", ":seg01", "run:", None, 123, []):
        assert SELECT.draft_run_id(bad) is None, bad


# ===========================================================================
# FALSE-POSITIVE BOUNDS -- all four STAY GREEN if the check is deleted.
# They bound damage. They are not evidence the check works. See this file's
# own module docstring.
# ===========================================================================


def test_allows_once_that_run_id_has_its_digest(tmp_path):
    """The pairing for PROOF 1: the SAME fixture plus exactly one file. This
    pins the refusal to the digest's absence specifically rather than to some
    other property of the fixture."""
    root = skipped_gate_project(tmp_path)
    write_digest(root, "HANDLABEL20260801")

    proc = run_select(root)

    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    payload = parse_stdout(proc)
    assert payload["runs_missing_digest"] == []
    assert payload["dispatching_run_ids"] == ["HANDLABEL20260801"]
    assert payload["drafts_scanned"] == 1


def test_does_not_fire_on_a_project_with_no_dispatched_drafts(tmp_path):
    """First run ever: a manifest, no drafts, no runs/. Absence of digests is
    not a skipped gate here, and the set difference gives that for free
    rather than through a special case."""
    root = make_durable_root(tmp_path)
    write_manifest(root, ["seg01"])

    proc = run_select(root)

    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    payload = parse_stdout(proc)
    assert payload["runs_missing_digest"] == []
    assert payload["dispatching_run_ids"] == []
    assert payload["drafts_scanned"] == 0


def test_untokened_draft_is_unattributable_and_never_refuses(tmp_path):
    """draft.schema.json permits a draft with no dispatch_token. Such a draft
    contributes nothing, and the direction of that hole is one-way: a false
    NEGATIVE (a skip we fail to see), never a false positive."""
    root = make_durable_root(tmp_path)
    write_manifest(root, ["seg01"])
    write_in_progress_fragment(root, "seg01")
    write_draft(root, "seg01", dispatch_token=None)

    proc = run_select(root)

    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    payload = parse_stdout(proc)
    assert payload["runs_missing_digest"] == []
    assert payload["drafts_scanned"] == 1, "the draft must have been read"
    assert payload["drafts_untokened"] == 1, (
        "and must be reported as unattributable rather than silently ignored"
    )


def test_workflow_dir_alone_is_enough_to_refuse(tmp_path):
    """PROOF -- the second evidence half. A run whose drafts were all later
    re-dispatched under another run id survives ONLY as a
    runs/workflows/<RUN_ID>/ directory, because a draft holds just its most
    recent token. Measured on the project that motivated this check: the
    draft scan sees four run ids, the workflow directories show six.

    Goes RED if the workflow half is dropped from the union."""
    root = make_durable_root(tmp_path)
    write_manifest(root, ["seg01"])
    write_in_progress_fragment(root, "seg01")
    # seg01's draft was re-dispatched under a LATER, gate-compliant run, so
    # nothing in segments/ points at OVERWRITTEN_RUN any more.
    write_draft(root, "seg01", dispatch_token="LATERRUN20260801:seg01")
    write_digest(root, "LATERRUN20260801")
    (root / "runs" / "workflows" / "OVERWRITTEN_RUN").mkdir(parents=True)

    proc = run_select(root)

    assert proc.returncode != 0, f"stdout={proc.stdout!r}"
    payload = parse_stdout(proc)
    assert payload["runs_missing_digest"] == ["OVERWRITTEN_RUN"], payload
    assert payload["dispatching_run_ids"] == ["LATERRUN20260801"], (
        "the draft scan alone genuinely cannot see it -- that is the point"
    )
    assert payload["run_id_evidence"]["OVERWRITTEN_RUN"] == ["workflow_dir"]


def test_a_run_with_both_evidence_halves_is_reported_as_both(tmp_path):
    root = skipped_gate_project(tmp_path, run_id="BOTHRUN20260801")
    (root / "runs" / "workflows" / "BOTHRUN20260801").mkdir(parents=True)

    payload = parse_stdout(run_select(root))

    assert payload["runs_missing_digest"] == ["BOTHRUN20260801"]
    assert payload["run_id_evidence"]["BOTHRUN20260801"] == ["drafts", "workflow_dir"]


def test_compliant_and_busy_project_passes_and_reports_its_run_ids(tmp_path):
    """The state a REAL working project sits in, and the one a broken scan
    would most plausibly break -- characterized from ssk-he-en's vol2/run:
    several segments dispatched across THREE run ids, every one carrying its
    digest, plus further digests for runs that resolved but dispatched
    nothing yet (a glossary run and a mass run that produced no draft).

    A first-run-ever fixture cannot stand in for this: there, the scan is
    empty and the difference is trivially empty, so a scan that reads no
    drafts at all passes identically. Here the scan must find exactly three
    run ids across five drafts and still refuse nothing."""
    root = make_durable_root(tmp_path)
    segs = ["seg01", "seg02", "seg03", "seg04", "seg05"]
    write_manifest(root, segs)
    for seg in segs:
        write_in_progress_fragment(root, seg)
    dispatched = {
        "seg01": "20260801T090001Z",
        "seg02": "20260801T090001Z",
        "seg03": "20260801T124257Z",
        "seg04": "20260801T132418Z",
        "seg05": "20260801T132418Z",
    }
    for seg, run_id in dispatched.items():
        write_draft(root, seg, dispatch_token=f"{run_id}:{seg}")
    for run_id in sorted(set(dispatched.values())):
        write_digest(root, run_id)
    # Resolved but never dispatched -- a digest with no draft pointing at it
    # is the normal, benign case and must not be mistaken for anything.
    write_digest(root, "20260714T210207Z")
    write_digest(root, "20260801T001211Z")
    # ...and every one of those runs also left an instantiated workflow
    # directory, exactly as the real compliant project does (six dirs, six
    # digests). This is the false-positive bound for the WORKFLOW half: it
    # must contribute nothing here, or the union would refuse the one live
    # project that has done everything right.
    for run_id in (
        "20260714T210207Z",
        "20260801T001211Z",
        "20260801T090001Z",
        "20260801T124257Z",
        "20260801T132418Z",
    ):
        (root / "runs" / "workflows" / run_id).mkdir(parents=True)

    proc = run_select(root)

    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    payload = parse_stdout(proc)
    assert payload["runs_missing_digest"] == []
    assert payload["dispatching_run_ids"] == [
        "20260801T090001Z",
        "20260801T124257Z",
        "20260801T132418Z",
    ], payload
    assert payload["drafts_scanned"] == 5, (
        "all five drafts must actually have been read -- this is the "
        "assertion a silently-empty scan fails"
    )
    assert payload["drafts_untokened"] == 0


def test_classify_only_reports_the_evidence_without_refusing(tmp_path):
    """--classify-only must stay a pure read (final_audit.py's completeness
    gate calls it), yet must still REPORT the evidence. Reporting an empty
    list there would make a skipped-gate project indistinguishable from a
    clean one to every --classify-only consumer."""
    root = skipped_gate_project(tmp_path)

    proc = run_select(root, "--classify-only")

    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    payload = parse_stdout(proc)
    assert payload["authorizes_dispatch"] is False
    assert payload["runs_missing_digest"] == ["HANDLABEL20260801"], (
        "the evidence must be reported even where it is not acted on"
    )


# ===========================================================================
# Drift pins -- the Step 3 primitives are duplicated between the READER
# (select_segments.py) and the WRITER (backfill_resume_gate_ack.py) per this
# project's "no shared lib between self-contained scripts" convention. These
# are drift checks, not a second source of truth.
# ===========================================================================


def test_the_retrofit_can_clear_every_refusal_the_gate_can_raise(tmp_path):
    """PROOF, and the one that guards the seam between the two scripts.

    The gate blocks on the UNION of draft-derived and workflow-derived run
    ids. If the retrofit only ever acknowledged the draft-derived half, an
    operator would face a refusal with no way to clear it -- a bricked
    project, and neither script's own tests would notice, because each side
    would be internally consistent. So this drives the round trip end to end
    against a project carrying BOTH shapes: refuse -> backfill --apply ->
    pass."""
    root = make_durable_root(tmp_path)
    write_manifest(root, ["seg01"])
    write_in_progress_fragment(root, "seg01")
    write_draft(root, "seg01", dispatch_token="DRAFTRUN20260801:seg01")
    (root / "runs" / "workflows" / "WFONLYRUN20260801").mkdir(parents=True)

    before = parse_stdout(run_select(root))
    assert before["runs_missing_digest"] == ["DRAFTRUN20260801", "WFONLYRUN20260801"]

    proc = subprocess.run(
        [
            sys.executable,
            str(root / "scripts" / "backfill_resume_gate_ack.py"),
            "--durable-root",
            str(root),
            "--apply",
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    acked = json.loads([ln for ln in proc.stdout.splitlines() if ln.strip()][0])
    assert acked["created"] == ["DRAFTRUN20260801", "WFONLYRUN20260801"], (
        "the retrofit must be able to acknowledge BOTH evidence shapes"
    )

    after = run_select(root)
    assert after.returncode == 0, f"stdout={after.stdout!r} stderr={after.stderr!r}"
    payload = parse_stdout(after)
    assert payload["runs_missing_digest"] == []
    assert payload["runs_acknowledged_pre_gate"] == [
        "DRAFTRUN20260801",
        "WFONLYRUN20260801",
    ]


@pytest.mark.parametrize(
    "token",
    [
        "20260801T090001Z:seg01",
        "w5-batch1-20260801T0500:FRONTBACK:fm04",
        "nocolon",
        "",
        ":seg01",
        "run:",
    ],
)
def test_both_copies_of_draft_run_id_agree(token):
    assert SELECT.draft_run_id(token) == BACKFILL_ACK.draft_run_id(token), token


def test_both_copies_of_the_marker_paths_agree(tmp_path):
    runs = tmp_path / "runs"
    assert SELECT.input_digest_path("R1", runs) == BACKFILL_ACK.input_digest_path("R1", runs)
    assert SELECT.resume_gate_ack_path("R1", runs) == BACKFILL_ACK.resume_gate_ack_path("R1", runs)
    assert SELECT.resume_gate_ack_path("R1", runs).name == ".resume_gate_ack"


def test_the_reader_and_writer_agree_on_a_real_marker(tmp_path):
    """End-to-end pin: what the writer actually creates is what the reader
    actually looks for. A filename or directory-layout drift between the two
    would leave the gate refusing forever with no way to acknowledge."""
    runs = tmp_path / "runs"
    assert BACKFILL_ACK.write_ack("RUN1", runs, ["seg01"]) == "created"
    assert SELECT.resume_gate_ack_path("RUN1", runs).exists()
    assert BACKFILL_ACK.write_ack("RUN1", runs, ["seg01"]) == "already_present", (
        "the writer must be idempotent and never overwrite an existing marker"
    )
