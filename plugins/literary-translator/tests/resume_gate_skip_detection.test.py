#!/usr/bin/env python3
"""#409 Step 3 -- select_segments.py refuses when a prior RUN_ID dispatched
work into a project WITHOUT the resume-integrity gate.

## Which of these tests are PROOFS, BOUNDS, ACCEPTED GAPS, or PINS

This distinction is load-bearing and must not be lost in a later edit. It was
also caught DRIFTING once already: a mutation at
`select_segments.py`'s `if runs_missing_digest and authorizes_dispatch:`
(flipped to `if False:`) showed `test_the_retrofit_can_clear_every_refusal_
the_gate_can_raise` staying GREEN while listed as a PROOF, and six further
tests belonging to no listed category at all. Re-measured directly against
that mutation before writing the corrected lists below -- every RED/GREEN
verdict stated here is the actual pytest outcome under it, not a
recollection. Every test in this file now appears in EXACTLY ONE of the four
categories; "not in the BOUNDS list" must never again be read as "therefore
a PROOF".

PROOFS -- these go RED under that exact mutation, and they are the ONLY
tests that may ever be cited as evidence the Step 3 refusal itself works:

  * test_refuses_when_a_dispatched_run_id_has_no_input_digest
  * test_acknowledged_run_stops_blocking_but_a_new_skip_still_fires
  * test_frontback_seg_id_attributes_to_the_right_run_id
  * test_workflow_dir_alone_is_enough_to_refuse

FALSE-POSITIVE BOUNDS -- these assert the check does NOT fire on a healthy
project, and STAY GREEN under the same mutation, because a deleted check
also does not fire. They bound the damage; they prove nothing about
detection, and citing them as evidence the gate works would be exactly the
mistake this file exists to make impossible:

  * test_allows_once_that_run_id_has_its_digest
  * test_does_not_fire_on_a_project_with_no_dispatched_drafts
  * test_compliant_and_busy_project_passes_and_reports_its_run_ids
  * test_classify_only_reports_the_evidence_without_refusing

The exact-list assertions on `runs_missing_digest` (never truthiness) and
the `drafts_scanned` assertions are what make the PROOFS non-vacuous. A scan
that silently matched nothing -- wrong glob, wrong directory, a fixture that
forgot the dispatch_token -- produces an empty list and would otherwise pass
a "did it refuse?" assertion for entirely the wrong reason.

ACCEPTED GAP -- a confirmed false NEGATIVE, pinned as deliberately accepted
rather than silently assumed correct. STAYS GREEN under the mutation (same
observable behavior as a BOUND), but licenses the OPPOSITE conclusion: not
"the check is healthy here", but "detection provably stops here, on
purpose, and the test says so in its own name and docstring rather than
leaving that discovery to whoever deletes the check next":

  * test_untokened_draft_is_unattributable_and_never_refuses --
    `draft.schema.json` permits a draft with no `dispatch_token` at all;
    such a draft is unattributable and contributes nothing to either scan.
    RECLASSIFIED here (was previously unlisted): its own docstring already
    said "a false NEGATIVE", which is this category's exact definition, not
    a BOUND's.
  * test_driver_run_fully_overwritten_and_never_instantiated_is_undetectable
    -- the two one-way holes `scan_dispatching_run_ids()`'s own "KNOWN HOLE"
    paragraph and `scan_workflow_run_ids()`'s own docstring each document
    separately COMPOSE when a driver-dispatched run's every draft is later
    overwritten: no artifact survives in either scan, so the union sees
    nothing and the gate passes silently. Confirmed by the test's own
    before/after asymmetry -- the SAME run id refuses while its draft still
    exists and passes silently the instant that draft is overwritten.

COMPONENT & CONSISTENCY PINS -- test a NARROWER property than the Step 3
refusal itself (a parser, one evidence-attribution detail, a round trip
between this script and a sibling, or two independent copies of the same
logic agreeing) in isolation from whether the refusal fires at all. ALL
STAY GREEN under the same mutation -- that is expected and proves nothing
about the refusal either way, in either direction: a passing pin is not
evidence the refusal works, and a refusal deleted out from under a passing
pin does not make the pin wrong. What licenses citing one is narrower and
different: if a PIN itself goes red, that is evidence of a defect in the
specific narrower thing it names, independent of the refusal's own health.

  * test_draft_run_id_parses_the_shapes_that_actually_occur -- unit-level,
    no subprocess at all; companion to the frontback PROOF above, pinning
    the parser it depends on directly.
  * test_a_run_with_both_evidence_halves_is_reported_as_both -- asserts
    `run_id_evidence` attribution only; never asserts on `returncode`, so it
    cannot be a PROOF regardless of what it sets up.
  * test_the_retrofit_can_clear_every_refusal_the_gate_can_raise --
    RECLASSIFIED here (previously mislabeled a PROOF; the mutation above is
    what caught it). It drives `backfill_resume_gate_ack.py --apply`
    against BOTH evidence shapes and asserts the evidence fields end up
    correct -- a real property (the round trip must actually clear
    everything the union can raise), but one that never once asserts the
    Step 3 refusal itself fired, so it cannot detect that refusal's
    deletion.
  * test_both_copies_of_draft_run_id_agree -- drift pin, `select_segments.py`
    vs. `backfill_resume_gate_ack.py`'s own `draft_run_id()`.
  * test_both_copies_of_validate_run_id_agree -- drift pin, the same two
    scripts' `validate_run_id()` (the #409 security fix below).
  * test_both_copies_of_the_marker_paths_agree -- drift pin, both scripts'
    `input_digest_path()`/`resume_gate_ack_path()`.
  * test_the_reader_and_writer_agree_on_a_real_marker -- end-to-end pin: what
    `backfill_resume_gate_ack.py` actually creates is what `select_segments.py`
    actually looks for.

## Security fix -- unsafe RUN_ID validation

`select_segments.py` built `runs/<RUN_ID>/input.digest` and
`.resume_gate_ack` paths straight from a draft's `dispatch_token` with no
shape check, while its sibling `backfill_resume_gate_ack.py` already
validated the identical value and refused. This sub-taxonomy is mutated and
measured separately from the Step 3 gate above -- a different `if` (`if
unsafe_run_ids and authorizes_dispatch:`), re-verified the same way
(flipped to `if False:`) before these lists were written:

PROOFS (go RED under that mutation, and license citing this check itself as
working -- nothing broader):

  * test_refuses_when_a_traversing_run_id_is_present
  * test_refuses_on_an_absolute_path_run_id_and_never_reads_outside_runs_dir
    -- the sharpest shape: reproduces the actual GATE BYPASS (a traversing/
    absolute run id resolving onto an unrelated existing `input.digest`
    reads as "already gated"), confirmed pre-fix to return exit 0.
  * test_unsafe_run_id_refusal_does_not_recommend_the_unclearable_remedy --
    proves the DESIGN decision, not just the mechanism: the refusal must
    not recommend `backfill_resume_gate_ack.py`, which validates the
    identical shape and would refuse the same id (the "unclearable wedge"
    the union-of-evidence design exists to prevent, one layer deeper).

BOUNDS (stay green under that same mutation -- deleting the check just means
`unsafe_run_ids` is never populated, so an emptiness assertion still holds
vacuously; they prove no false positive, nothing about detection):

  * test_classify_only_reports_unsafe_run_ids_without_refusing
  * test_frontback_seg_full_flow_is_not_flagged_unsafe -- the positive
    control: the shape "least likely to appear in a hand-built fixture"
    (draft_run_id()'s own docstring) must still pass end to end.
  * the `unsafe_run_ids == {}` assertions added to the pre-existing
    false-positive bounds above.

`test_both_copies_of_validate_run_id_agree` also belongs to this fix, but is
a drift PIN rather than a PROOF or BOUND of it -- listed under COMPONENT &
CONSISTENCY PINS above, not repeated here.

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
    assert payload["unsafe_run_ids"] == {}, (
        "a well-formed run id must never land in the new unsafe bucket"
    )


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
    assert payload["unsafe_run_ids"] == {}


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


def test_driver_run_fully_overwritten_and_never_instantiated_is_undetectable(tmp_path):
    """ACCEPTED GAP, pinned deliberately -- not a proof the check works, the
    opposite: this is the one combination where BOTH evidence halves miss at
    once, and it is not fixable from current disk state alone.

    OLD_DRIVER_RUN dispatches seg01 via the driver (which never creates a
    runs/workflows/ directory -- see scan_workflow_run_ids()'s own
    docstring) and is never gated. Before anyone notices, seg01 gets
    redispatched under NEW_COMPLIANT_RUN, which DOES have its digest and
    overwrites seg01's draft token (a draft holds only its most recent
    token). OLD_DRIVER_RUN now has zero surviving evidence anywhere on
    disk: not in the draft scan (overwritten), not in the workflow scan (a
    driver run never wrote one to begin with). select_segments.py cannot
    refuse what it cannot see.

    First confirmed empirically against this exact fixture before either
    line of documentation above was written: step 1 (OLD_DRIVER_RUN's draft
    still present) refuses correctly (`runs_missing_digest ==
    ["OLD_DRIVER_RUN"]`); step 2 (after the overwrite) passes silently. That
    asymmetry -- not this test failing -- is what proves the gap is real."""
    root = make_durable_root(tmp_path)
    write_manifest(root, ["seg01"])
    write_in_progress_fragment(root, "seg01")
    write_draft(root, "seg01", dispatch_token="OLD_DRIVER_RUN:seg01")

    before = parse_stdout(run_select(root))
    assert before["runs_missing_digest"] == ["OLD_DRIVER_RUN"], (
        "sanity check: while the draft still points at it, the gate must "
        f"still catch it -- otherwise this test proves nothing. {before}"
    )

    write_digest(root, "NEW_COMPLIANT_RUN")
    write_draft(root, "seg01", dispatch_token="NEW_COMPLIANT_RUN:seg01")

    proc = run_select(root)

    assert proc.returncode == 0, (
        "documenting the accepted gap: OLD_DRIVER_RUN is now undetectable, "
        f"so the run passes. stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
    payload = parse_stdout(proc)
    assert payload["runs_missing_digest"] == [], payload
    assert payload["dispatching_run_ids"] == ["NEW_COMPLIANT_RUN"], (
        "OLD_DRIVER_RUN has genuinely vanished from all evidence -- this is "
        f"the gap itself, not a scan malfunction. {payload}"
    )
    assert payload["workflow_run_ids"] == [], (
        "a driver run never had a workflow directory to leave a trace in "
        f"even before the overwrite. {payload}"
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
    # Audit-accuracy fix: OVERWRITTEN_RUN itself never dispatched anything
    # (its only evidence is the workflow directory) -- the summary sentence
    # must not blanket-claim every listed id "dispatched work".
    assert (
        "show evidence of having dispatched work and/or had a Workflow "
        "template instantiated" in payload["error"]
    ), payload["error"]
    assert "OVERWRITTEN_RUN (0 draft(s), evidence: workflow_dir)" in payload["error"]


def test_a_run_with_both_evidence_halves_is_reported_as_both(tmp_path):
    """COMPONENT & CONSISTENCY PIN, not a PROOF: asserts `run_id_evidence`
    attribution only, and never checks `proc.returncode` -- so it cannot
    detect the Step 3 refusal being deleted, only that the union correctly
    tags a doubly-evidenced run id with BOTH sources rather than just one."""
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
    assert payload["unsafe_run_ids"] == {}, (
        "none of this real project's own run ids may be flagged unsafe"
    )


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
# Security fix -- unsafe RUN_ID validation. select_segments.py built
# runs/<RUN_ID>/ paths straight from an unvalidated draft dispatch_token;
# backfill_resume_gate_ack.py already validated the identical value. See this
# file's own module docstring "Security fix" section for the PROOF/BOUND
# breakdown.
# ===========================================================================


def test_refuses_when_a_traversing_run_id_is_present(tmp_path):
    """PROOF. A `../`-shaped run id (from a token like
    '../../../../tmp/pwned:seg01') must never be turned into a filesystem
    lookup -- refused as unsafe instead, and never folded into
    `runs_missing_digest` (which WOULD have recommended
    backfill_resume_gate_ack.py, a remedy that cannot clear this)."""
    root = make_durable_root(tmp_path)
    write_manifest(root, ["seg01"])
    write_in_progress_fragment(root, "seg01")
    bad_run_id = "../../../../tmp/pwned"
    write_draft(root, "seg01", dispatch_token=f"{bad_run_id}:seg01")

    proc = run_select(root)

    assert proc.returncode != 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    payload = parse_stdout(proc)
    assert payload["success"] is False
    assert bad_run_id in payload["unsafe_run_ids"], payload
    assert payload["runs_missing_digest"] == [], (
        "an unsafe run id must be caught BEFORE it ever reaches the "
        f"missing-digest bucket, not folded into it. {payload}"
    )
    assert bad_run_id in payload["error"]


def test_refuses_on_an_absolute_path_run_id_and_never_reads_outside_runs_dir(tmp_path):
    """PROOF -- the sharpest shape of the bug, and a direct reproduction of
    its GATE-BYPASS consequence. `pathlib` silently DISCARDS `runs_dir` on
    an absolute right-hand side: `Path(runs_dir) / '/abs/path' ==
    Path('/abs/path')`. This plants a decoy `input.digest` at exactly the
    absolute address an unvalidated run id resolves to. Confirmed against
    the actual pre-fix code at this branch's parent commit
    (959a26a/plugins/literary-translator/.../select_segments.py) that this
    identical fixture returns exit 0 / `success: true` -- the id reads as
    'already gated' via the decoy, silently passing the check it should
    have refused."""
    root = make_durable_root(tmp_path)
    escape_target = tmp_path / "escaped_elsewhere"
    escape_target.mkdir()
    assert (root / "runs") / str(escape_target) == escape_target, (
        "pathlib's absolute-join behavior changed -- this fixture's premise is stale"
    )
    (escape_target / "input.digest").write_text("decoy\n", encoding="utf-8")

    write_manifest(root, ["seg01"])
    write_in_progress_fragment(root, "seg01")
    write_draft(root, "seg01", dispatch_token=f"{escape_target}:seg01")

    proc = run_select(root)

    assert proc.returncode != 0, (
        "an absolute-path run id must never be read as 'gated' via the decoy "
        f"digest planted at {escape_target}. stdout={proc.stdout!r}"
    )
    payload = parse_stdout(proc)
    assert payload["success"] is False
    assert str(escape_target) in payload["unsafe_run_ids"], payload
    assert payload["runs_missing_digest"] == [], payload


def test_unsafe_run_id_refusal_does_not_recommend_the_unclearable_remedy(tmp_path):
    """PROOF for the design decision, not just the mechanism: the refusal
    for an unsafe run id must NOT point at backfill_resume_gate_ack.py,
    because that script validates the IDENTICAL shape and would refuse the
    very same id -- confirmed directly against the real
    BACKFILL_ACK.validate_run_id() below, not merely asserted. Recommending
    it anyway would reproduce the unclearable wedge the union-of-evidence
    design exists to avoid, one layer deeper: refuse -> --apply -> refuse
    again, through neither script."""
    root = make_durable_root(tmp_path)
    write_manifest(root, ["seg01"])
    write_in_progress_fragment(root, "seg01")
    bad_run_id = "has a space"  # merely malformed, not necessarily malicious
    write_draft(root, "seg01", dispatch_token=f"{bad_run_id}:seg01")

    assert BACKFILL_ACK.validate_run_id(bad_run_id) is not None, (
        "fixture premise: backfill_resume_gate_ack.py must ALSO reject this "
        "id, or this test proves nothing about the wedge"
    )

    proc = run_select(root)

    assert proc.returncode != 0
    payload = parse_stdout(proc)
    assert bad_run_id in payload["unsafe_run_ids"], payload
    # The message MAY still name backfill_resume_gate_ack.py (explaining why
    # it won't help is useful), but must not RECOMMEND running it the way
    # the runs_missing_digest refusal recommends its own remedy (that exact
    # "backfill_resume_gate_ack.py --apply" phrasing).
    assert "backfill_resume_gate_ack.py --apply" not in payload["error"], (
        "the message must not tell the operator to run the remedy that will "
        "also refuse this same id"
    )
    assert "would not help" in payload["error"], (
        "the message must say explicitly that the sanctioned backfill tool "
        "cannot clear this refusal"
    )
    assert "by hand" in payload["error"], (
        "the message must name the ONLY remedy that actually exists"
    )


def test_classify_only_reports_unsafe_run_ids_without_refusing(tmp_path):
    """BOUND. --classify-only must stay a pure read (final_audit.py's
    completeness gate calls it) even when an unsafe run id is present --
    mirrors test_classify_only_reports_the_evidence_without_refusing for the
    new unsafe_run_ids bucket specifically."""
    root = make_durable_root(tmp_path)
    write_manifest(root, ["seg01"])
    write_in_progress_fragment(root, "seg01")
    bad_run_id = "../escape"
    write_draft(root, "seg01", dispatch_token=f"{bad_run_id}:seg01")

    proc = run_select(root, "--classify-only")

    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    payload = parse_stdout(proc)
    assert payload["authorizes_dispatch"] is False
    assert bad_run_id in payload["unsafe_run_ids"], (
        "the evidence must be reported even where it is not acted on"
    )


def test_frontback_seg_full_flow_is_not_flagged_unsafe(tmp_path):
    """BOUND -- the positive control the module docstring calls out by name.
    draft_run_id() splits on the FIRST colon only, precisely so a
    FRONTBACK:{id} SEG shape (itself containing a colon) is not mistaken for
    part of the RUN_ID. A naive validation bolted on top could still break
    this if it validated the wrong half or the raw token instead of the
    already-split run id. Drives the full flow with the digest present, and
    asserts both that SEGS still includes the segment and that
    unsafe_run_ids stays empty."""
    root = make_durable_root(tmp_path)
    write_manifest(root, ["FRONTBACK:fm04"])
    write_in_progress_fragment(root, "FRONTBACK:fm04")
    write_draft(
        root, "FRONTBACK:fm04", dispatch_token="w5-batch1-20260801T0500:FRONTBACK:fm04"
    )
    write_digest(root, "w5-batch1-20260801T0500")

    proc = run_select(root)

    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    payload = parse_stdout(proc)
    assert payload["unsafe_run_ids"] == {}, payload
    assert payload["runs_missing_digest"] == []
    assert "FRONTBACK:fm04" in payload["segs"]


# ===========================================================================
# COMPONENT & CONSISTENCY PINS (continued) -- the Step 3 primitives are
# duplicated between the READER (select_segments.py) and the WRITER
# (backfill_resume_gate_ack.py) per this project's "no shared lib between
# self-contained scripts" convention. These are drift checks, not a second
# source of truth, and not proofs of the refusal itself -- see this file's
# own module docstring for the category's license.
# ===========================================================================


def test_the_retrofit_can_clear_every_refusal_the_gate_can_raise(tmp_path):
    """COMPONENT & CONSISTENCY PIN -- the seam between the two scripts, NOT
    a PROOF of the Step 3 refusal itself (mislabeled as one until a mutation
    at the refusal's own `if` caught it staying green; see this file's own
    module docstring for that history). Never asserts the "before" state
    actually refused -- only that the EVIDENCE fields are correct and that
    the round trip clears them.

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
        # Security-fix regression coverage: draft_run_id() is a PURE
        # syntactic split (see its own docstring) and stays that way after
        # the fix -- it must return the SAME (still unvalidated) substring
        # in both copies even for a traversing or absolute-path token.
        # Judging that substring unsafe is validate_run_id()'s job, called
        # separately downstream; see this test's own docstring below for why
        # that keeps this exact assertion the right one.
        "../../../../tmp/pwned:seg01",
        "/etc:seg01",
    ],
)
def test_both_copies_of_draft_run_id_agree(token):
    """Still the right assertion after the #409 security fix, unchanged in
    kind. draft_run_id() is a PURE syntactic split -- its docstring is
    explicit that a naive rsplit/split(':')[-2] gets FRONTBACK segments
    wrong, which is the whole reason it exists as its own function -- and it
    is deliberately never the place a run id is judged safe or unsafe. That
    judgment is validate_run_id()'s job, called separately in run() AFTER
    extraction, mirroring exactly how backfill_resume_gate_ack.py's own
    run() already calls its draft_run_id() then its validate_run_id() as two
    separate steps. So this test correctly continues to assert that both
    copies extract the IDENTICAL substring -- including for the
    traversing/absolute-path cases added above, which extract cleanly to
    '../../../../tmp/pwned' and '/etc' in BOTH files, unsafe as those values
    are. test_both_copies_of_validate_run_id_agree below is the
    complementary drift pin for the validation step: it proves both copies
    REJECT those same two extracted values identically, which is what
    actually closes the vulnerability."""
    assert SELECT.draft_run_id(token) == BACKFILL_ACK.draft_run_id(token), token


@pytest.mark.parametrize(
    "run_id",
    [
        "20260801T090001Z",
        "w5-batch1-20260801T0500",
        "RUNX20260801",
        "../../../../tmp/pwned",
        "/etc",
        "..",
        ".",
        "",
        "a..b",
        "run:id",
        "has a space",
        None,
        123,
    ],
)
def test_both_copies_of_validate_run_id_agree(run_id):
    """Security-fix drift pin, the complement to
    test_both_copies_of_draft_run_id_agree above. select_segments.py's
    validate_run_id() must accept and reject the IDENTICAL set of run ids as
    backfill_resume_gate_ack.py's own copy -- a run id one script accepted
    and the other rejected would either reopen the gate bypass (this script
    accepts what the other would refuse to acknowledge, so nothing here
    would ever CLEAR it either) or reintroduce the unclearable wedge (this
    script rejects an id the other could actually acknowledge)."""
    assert (SELECT.validate_run_id(run_id) is None) == (
        BACKFILL_ACK.validate_run_id(run_id) is None
    ), run_id


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
