"""tests/txn_glue_plumbing.test.py -- the wiring that lets the driver reach
--kind fixreview at all (#409 track B).

Before this, the two halves could not meet in EITHER direction, and the gap was
not "the connector is missing" but "both endpoints are unreachable":

  * build_codex_job_argv() emitted no --expect-review-token, which codex_job.py
    REQUIRES for that kind and refuses for every other -- so the dispatch could
    never be spelled;
  * _codex_job_outcome() returned a fixed five-key dict, dropping the five
    staged_* fields that are the ONLY pointer to the staged candidates (their
    per-invocation `inv` component is random, there is no deterministic slot,
    and nothing sweeps for them) -- so even a correct dispatch lost its output;
  * no txn_dir was resolved anywhere in production code.

This file covers exactly that plumbing, and only that. The renames belong to
tests/txn_publish.test.py; everything between the two -- which kind a round
dispatches, the pre-derive recovery phase and its lease, minting an intent
from real pre-image state, and the per-round bounds -- belongs to
tests/txn_dispatch_path.test.py.

--fix-mode DEFAULTS TO handoff, and that is asserted here rather than assumed:
the default is what decides whether this release changes behaviour for existing
projects, and it is the one property in this file whose regression would be
silent -- every other test would still pass with the default flipped.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_SRC_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets" / "scripts"
DRIVER_SRC = SCRIPTS_SRC_DIR / "segment_dispatch_driver.py"

assert DRIVER_SRC.is_file(), f"expected the driver at {DRIVER_SRC}"


def _load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, str(path))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


DRIVER = _load_module(DRIVER_SRC, "segment_dispatch_driver_txn_glue")


# ---------------------------------------------------------------------------
# --fix-mode
# ---------------------------------------------------------------------------


def test_fix_mode_defaults_to_handoff():
    """The load-bearing default. `codex` moves who edits the user's text, so
    this release makes that path REACHABLE, not active -- and a silent flip
    would change behaviour for every existing project without a line of their
    config changing."""
    args = DRIVER.build_arg_parser().parse_args(
        ["--durable-root", "/some/root"])
    assert args.fix_mode == DRIVER.FIX_MODE_HANDOFF


def test_fix_mode_accepts_codex():
    args = DRIVER.build_arg_parser().parse_args(
        ["--durable-root", "/some/root", "--fix-mode", "codex"])
    assert args.fix_mode == DRIVER.FIX_MODE_CODEX


def test_fix_mode_rejects_anything_else():
    with pytest.raises(SystemExit):
        DRIVER.build_arg_parser().parse_args(
            ["--durable-root", "/some/root", "--fix-mode", "whatever"])


def test_the_context_carries_the_mode_and_defaults_it_too():
    """Two independent defaults exist -- argparse's and DispatchContext's -- and
    a caller building a context directly (every test in this suite does) would
    otherwise get whichever one happened to be written first."""
    ctx = _mkctx()
    assert ctx.fix_mode == DRIVER.FIX_MODE_HANDOFF
    assert _mkctx(fix_mode=DRIVER.FIX_MODE_CODEX).fix_mode == DRIVER.FIX_MODE_CODEX


def _mkctx(tmp=None, **over):
    root = Path(tmp) if tmp else Path("/some/root")
    dirs = {"durable_root": root, "runs_dir": root / "runs",
            "scripts_dir": SCRIPTS_SRC_DIR}
    kwargs = dict(dirs=dirs, run_id="RUN", translate_cfg={}, companion_path="/c.mjs",
                  durable_root_str=str(root), plugin_root_str=None, node_bin="node",
                  session_id="S")
    kwargs.update(over)
    return DRIVER.DispatchContext(**kwargs)


# ---------------------------------------------------------------------------
# the directories the transaction lives in
# ---------------------------------------------------------------------------


def test_txn_dir_is_scoped_to_the_RUN_not_the_durable_root():
    """Recovery discovers by a FIXED intent path per segment, so two runs
    sharing one txn directory would collide on the same name and one run could
    recover the other's abandoned transaction."""
    ctx = _mkctx()
    assert ctx.txn_dir == Path("/some/root/runs/RUN/txn")
    assert _mkctx(run_id="OTHER").txn_dir == Path("/some/root/runs/OTHER/txn")


def test_segments_dir_is_the_canonical_one():
    assert _mkctx().segments_dir == Path("/some/root/segments")


# ---------------------------------------------------------------------------
# build_codex_job_argv: the review token
# ---------------------------------------------------------------------------


def _argv(**over):
    kwargs = dict(kind="review", seg="seg01", companion_path="/c.mjs",
                  durable_root=Path("/some/root"), prompt_file=Path("/tmp/p.txt"),
                  expect_token="RUN:seg01:r1", disp="d1", deadline_sec=600,
                  effort="high", model="", plugin_root_str=None)
    kwargs.update(over)
    return DRIVER.build_codex_job_argv(**kwargs)


@pytest.mark.parametrize("kind", ["translate", "review"])
def test_no_review_token_is_emitted_for_the_other_kinds(kind):
    """codex_job.py REFUSES the flag for every kind but fixreview, so emitting
    it would turn a working dispatch into a rejected one."""
    argv = _argv(kind=kind)
    assert "--expect-review-token" not in argv


def test_fixreview_emits_the_review_token_right_after_nothing_else_changes():
    argv = _argv(kind="fixreview", expect_token="RUN:seg01",
                 expect_review_token="RUN:seg01:r1")
    assert "--expect-review-token" in argv
    assert argv[argv.index("--expect-review-token") + 1] == "RUN:seg01:r1"
    # the draft token stays in --expect-token; the two are NOT interchangeable
    assert argv[argv.index("--expect-token") + 1] == "RUN:seg01"


def test_fixreview_without_a_review_token_refuses_instead_of_launching():
    """Refusing here rather than letting codex_job.py reject it costs one
    process and one confusing error message less, and names the real mistake."""
    with pytest.raises(DRIVER.DriverError):
        _argv(kind="fixreview", expect_token="RUN:seg01", expect_review_token=None)


@pytest.mark.parametrize("kind", ["translate", "review"])
def test_a_review_token_supplied_for_the_wrong_kind_refuses(kind):
    with pytest.raises(DRIVER.DriverError):
        _argv(kind=kind, expect_review_token="RUN:seg01:r1")


# ---------------------------------------------------------------------------
# _codex_job_outcome: the staged fields
# ---------------------------------------------------------------------------


def _outcome(line: dict) -> dict:
    return DRIVER._codex_job_outcome({"stdout": json.dumps(line) + "\n"})


STAGED_LINE = {
    "ok": True, "reason": "staged", "error_detail": None,
    "job_status": "completed", "adopted": False,
    "staged": True,
    "staged_draft_path": "/some/root/segments/.att.seg01.abcd.draft.json",
    "staged_review_path": "/some/root/segments/.att.seg01.abcd.review.json",
    "staged_draft_sha256": "d" * 64,
    "staged_review_sha256": "r" * 64,
}


@pytest.mark.parametrize("field", [
    "staged", "staged_draft_path", "staged_review_path",
    "staged_draft_sha256", "staged_review_sha256",
])
def test_every_staged_field_survives_the_parse(field):
    """Each named separately, so a partial regression cannot hide behind a
    single dict comparison that someone later relaxes."""
    assert _outcome(STAGED_LINE)[field] == STAGED_LINE[field]


def test_the_staged_paths_are_copied_verbatim_never_re_derived():
    """The `inv` component is os.urandom -- it cannot be recomputed from
    anything the driver knows, so a re-derived path would name a file that does
    not exist."""
    out = _outcome(STAGED_LINE)
    assert out["staged_draft_path"] == STAGED_LINE["staged_draft_path"]
    assert "abcd" in out["staged_draft_path"]


def test_the_other_kinds_gain_no_staged_keys():
    """translate/review report no staged_* fields at all, and absent must stay
    absent -- a None-valued key reads downstream as 'staged, value unknown'."""
    out = _outcome({"ok": True, "reason": "promoted", "error_detail": None,
                    "job_status": "completed", "adopted": False})
    for field in ("staged", "staged_draft_path", "staged_draft_sha256"):
        assert field not in out


def test_a_refused_fixreview_still_reports_its_absent_shaped_fields():
    out = _outcome({"ok": False, "reason": "validate-failed", "error_detail": "gate 4",
                    "job_status": "completed", "adopted": False, "staged": False,
                    "staged_draft_path": None, "staged_review_path": None,
                    "staged_draft_sha256": None, "staged_review_sha256": None})
    assert out["ok"] is False
    assert out["staged"] is False
    assert out["staged_draft_path"] is None


def test_unparseable_stdout_still_falls_back_without_inventing_staged_fields():
    out = DRIVER._codex_job_outcome({"stdout": "not json", "stderr": "boom"})
    assert out["ok"] is False
    assert out["reason"] == "driver-no-parseable-stdout"
    assert "staged" not in out


if __name__ == "__main__":
    import sys

    sys.exit(pytest.main([__file__, "-v"]))
