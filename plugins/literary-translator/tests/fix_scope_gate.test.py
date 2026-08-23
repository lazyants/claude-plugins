"""#607 -- the fix-scope gate as WIRED, driven through the shipped template.

`fix_scope_audit.test.py` covers the checker. This file covers the four
things only the workflow can be wrong about, and it drives the REAL
`mass-translate-wf.template.js` under Node with a scripted mock `agent()`
rather than reasoning about the source:

  1. an audit mismatch ends the segment with exactly one terminal
     `blocked`/`fix-scope-violation` ledger write;
  2. the audit runs BEFORE `fx` is inspected, so a turn cannot mutate the
     durable copies and then leave through the falsy/`DRAFT_MISSING` door
     unaudited -- the second BLOCKER of this issue's plan review;
  3. one failed relay plus a good retry CONTINUES, while two failures end the
     segment as `fix-scope-unverified` rather than proceeding over a surface
     the pipeline could not verify;
  4. an empty plugin root refuses the batch before any agent call.

The instantiate/wrap/run harness is imported from
`batch_size_estimator.test.py` via `importlib.util.spec_from_file_location`
-- the same mechanism `agent_schema_top_level_object.test.py` uses, and for
the same reason: a second vendored copy of the substitution contract would
drift from the first, and a harness that instantiated the template
differently from the one every call-count assertion uses would be testing a
different file than it claims to.
"""

import importlib.util
from pathlib import Path

import pytest

_BSE_PATH = Path(__file__).resolve().parent / "batch_size_estimator.test.py"
_spec = importlib.util.spec_from_file_location("_bse_harness", _BSE_PATH)
_bse = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_bse)

run_workflow = _bse.run_workflow
review_obj = _bse.review_obj
match_true = _bse.match_true
bucket_calls_by_segment = _bse.bucket_calls_by_segment
pytestmark = _bse.NODE_REQUIRED if hasattr(_bse, "NODE_REQUIRED") else pytest.mark.skipif(
    _bse.NODE is None, reason="node not found on PATH; this file executes the real template"
)

SEG = "seg01"
CLEAN = {"ok": True, "n_checked": 79, "n_expected": 79}
MISMATCH = {
    "ok": False,
    "verdict": "mismatch",
    "n_checked": 79,
    "n_expected": 79,
    "differing": ["scripts/validate_draft.py"],
    "missing": [],
    "irregular": [],
    "extra": [],
    "marker_mismatch": [],
}


def one_round_plan(*, fix_reply=f"FIXED {SEG} r1", fix_scopes=None, present=None):
    """A segment whose first review point succeeds on the happy path and whose
    verdict is non-clean, so exactly one fix dispatches on round 1."""
    plan = {
        "wait": f"READY {SEG}",
        "reviewWaits": [f"READY {SEG}"],
        "reviews": [review_obj(clean=False)],
        "artifactChecks": [match_true()],
        "fixes": [fix_reply],
    }
    if fix_scopes is not None:
        plan["fixScopes"] = fix_scopes
    if present is not None:
        plan["present"] = present
    return {SEG: plan}


def drive(tmp_path, plan, max_fix_rounds=2):
    return run_workflow(
        tmp_path=tmp_path,
        max_fix_rounds=max_fix_rounds,
        batch_agent_cap=10_000,
        segs=[SEG],
        plan=plan,
    )


def labels_of(out):
    return [c["label"] for c in out["calls"]]


def test_mismatch_blocks_the_segment_once(tmp_path):
    out = drive(tmp_path, one_round_plan(fix_scopes=[MISMATCH]))
    result = out["result"]
    assert result["converged"] == []
    assert len(result["failed"]) == 1
    failed = result["failed"][0]
    assert failed["seg"] == SEG
    assert failed["converged"] is False
    assert failed["reason"] == "fix-scope-violation"
    assert failed["rounds"] == 1

    # Exactly one terminal ledger write, and it is the violation one -- a
    # second write, or a write under any other reason, would leave the
    # operator two records of one event.
    ledger = [lb for lb in labels_of(out) if lb.startswith("ledger:")]
    assert ledger == [f"ledger:in_progress:{SEG}", f"ledger:blocked:fix-scope-violation:{SEG}"], ledger

    # The segment stops at round 1: no round-2 review point is dispatched.
    assert not any(lb.startswith(f"review-dispatch:{SEG}:r2") for lb in labels_of(out))


def test_mismatch_note_names_files_and_not_their_contents(tmp_path):
    """The ledger is read by an operator and by select_segments.py. Naming the
    offending paths is the actionable part; pasting a possibly-tampered
    file's bytes into a durable record is not."""
    out = drive(tmp_path, one_round_plan(fix_scopes=[MISMATCH]))
    note = [c for c in out["calls"] if c["label"].startswith("ledger:blocked:fix-scope-violation")][0]
    rendered = note.get("prompt", "")
    assert "scripts/validate_draft.py" in rendered
    assert "not by itself proof of tampering" in rendered.lower()
    assert "Step 0a" in rendered


@pytest.mark.parametrize("fix_reply,case", [
    (f"DRAFT_MISSING {SEG}", "bare sentinel"),
    (None, "falsy fix return"),
    (f"prose about DRAFT_MISSING {SEG} in passing", "sentinel mentioned in prose"),
])
def test_audit_runs_before_fx_is_inspected(tmp_path, fix_reply, case):
    """The plan review's second BLOCKER. Every one of these replies exits
    through runRound's falsy/DRAFT_MISSING branch. If the audit sat after
    that branch, a turn could mutate the durable copies and then return any
    of them to leave unaudited -- and the NEXT batch would read the changed
    tree as simply how things are."""
    out = drive(tmp_path, one_round_plan(fix_reply=fix_reply, fix_scopes=[MISMATCH], present=True))
    assert any(lb.startswith(f"fix-scope:{SEG}:r1") for lb in labels_of(out)), (
        f"{case}: the audit must fire before the DRAFT_MISSING branch is taken"
    )
    assert out["result"]["failed"][0]["reason"] == "fix-scope-violation", (
        f"{case}: the violation must win over the fix-call-failed path -- the "
        "audit is the earlier and the more serious verdict"
    )
    # The probe belongs to the branch the audit pre-empts; it must not run.
    assert not any(lb.startswith("draft-probe:") for lb in labels_of(out)), case


def test_one_relay_failure_retries_and_continues(tmp_path):
    """A single dead relay is infra flakiness. It must cost one retry, not a
    segment: the round carries on exactly as it would have."""
    plan = one_round_plan(fix_scopes=[None, CLEAN])
    plan[SEG]["reviewWaits"] = [f"READY {SEG}", f"READY {SEG}"]
    plan[SEG]["reviews"] = [review_obj(clean=False), review_obj(clean=True)]
    plan[SEG]["artifactChecks"] = [match_true(), match_true()]
    out = drive(tmp_path, plan)
    scope_calls = [lb for lb in labels_of(out) if lb.startswith("fix-scope:")]
    assert scope_calls == [f"fix-scope:{SEG}:r1", f"fix-scope:{SEG}:r1:retry"], scope_calls
    assert [r["seg"] for r in out["result"]["converged"]] == [SEG]
    assert out["result"]["failed"] == []


def test_two_relay_failures_block_rather_than_proceed(tmp_path):
    """Fail-closed. Leaving the segment recoverable would let the next batch
    run over exactly the surface this gate could not verify -- and the price
    is stated rather than hidden: clearing this costs the segment a
    re-translation, which is what a safety halt is for."""
    out = drive(tmp_path, one_round_plan(fix_scopes=[None, None]))
    failed = out["result"]["failed"][0]
    assert failed["reason"] == "fix-scope-unverified"
    assert failed["converged"] is False
    ledger = [lb for lb in labels_of(out) if lb.startswith("ledger:")]
    assert f"ledger:blocked:fix-scope-unverified:{SEG}" in ledger
    assert not any(lb.startswith("draft-probe:") for lb in labels_of(out))


def test_final_round_dispatches_no_audit(tmp_path):
    """`isFinal` returns before `callFix`, so the mandatory confirming round
    never dispatches a fix and has nothing to audit. An audit there would be
    a call the estimator does not budget."""
    plan = one_round_plan(fix_scopes=[CLEAN])
    plan[SEG]["reviewWaits"] = [f"READY {SEG}", f"READY {SEG}"]
    plan[SEG]["reviews"] = [review_obj(clean=False), review_obj(clean=False)]
    plan[SEG]["artifactChecks"] = [match_true(), match_true()]
    out = drive(tmp_path, plan, max_fix_rounds=1)
    scope_calls = [lb for lb in labels_of(out) if lb.startswith("fix-scope:")]
    assert scope_calls == [f"fix-scope:{SEG}:r1"], scope_calls
    assert not any(lb.endswith(":rfinal") for lb in scope_calls)


def test_clean_verdict_never_reaches_the_audit(tmp_path):
    """No fix dispatched, nothing to audit -- the gate must not tax a segment
    that converged on its first review."""
    plan = {SEG: {
        "wait": f"READY {SEG}",
        "reviewWaits": [f"READY {SEG}"],
        "reviews": [review_obj(clean=True)],
        "artifactChecks": [match_true()],
        "fixes": [],
    }}
    out = drive(tmp_path, plan)
    assert [r["seg"] for r in out["result"]["converged"]] == [SEG]
    assert not any(lb.startswith("fix-scope:") for lb in labels_of(out))


@pytest.mark.parametrize("clean,case", [
    ({"ok": True, "n_checked": 0, "n_expected": 0}, "checked nothing, and said so consistently"),
    ({"ok": True, "n_checked": 0, "n_expected": 79}, "walk covered nothing"),
    ({"ok": True, "n_checked": 12, "n_expected": 79}, "walk truncated part-way"),
])
def test_a_clean_verdict_that_compared_nothing_is_not_a_pass(tmp_path, clean, case):
    """`ok` alone was the false GREEN: a walk that runs zero times prints
    exactly like one that covered everything, and the relay between the script
    and this workflow is a model turn. The script reports what it checked AND
    what it was supposed to check; a clean verdict is only honoured when those
    agree and are non-zero.

    This does not stop a relay that fabricates BOTH numbers, which is why
    SKILL.md discloses that residual instead of implying this closes it."""
    out = drive(tmp_path, one_round_plan(fix_scopes=[clean]))
    failed = out["result"]["failed"][0]
    assert failed["reason"] == "fix-scope-unverified", case
    assert out["result"]["batchComplete"] is False, case


def test_a_halt_is_reported_at_batch_level_even_when_the_ledger_write_fails(tmp_path):
    """The blocked fragment is written by `ledger_update.py` FROM THE DURABLE
    TREE the audit has just reported as diverging -- so the write can fail for
    exactly the reason the halt fired, and `recordLedgerCall`'s failure path
    returns without the promised fragment. The surviving `in_progress`
    fragment then classifies as recoverable and the next batch redispatches.

    The batch-level record does not live in that tree. It cannot make the
    durable record bulletproof; what it guarantees is that the batch cannot
    END LOOKING CLEAN when a halt fired."""
    plan = one_round_plan(fix_scopes=[MISMATCH])
    plan[SEG]["ledgerWrites"] = {"blocked": None}
    out = drive(tmp_path, plan)
    result = out["result"]
    assert result["batchComplete"] is False
    assert result["reason"] == "fix-scope-halt" or result.get("fixScopeHalts")
    halts = result["fixScopeHalts"]
    assert [h["seg"] for h in halts] == [SEG]
    assert halts[0]["reason"] == "fix-scope-violation"
    assert any("FIX-SCOPE HALT" in line for line in out["log"]), out["log"]


def test_a_successful_halt_still_marks_the_batch_incomplete(tmp_path):
    out = drive(tmp_path, one_round_plan(fix_scopes=[MISMATCH]))
    result = out["result"]
    assert result["batchComplete"] is False
    assert result["reason"] == "fix-scope-halt"
    assert result["fixScopeHalts"][0]["ledgerRecorded"] is True
