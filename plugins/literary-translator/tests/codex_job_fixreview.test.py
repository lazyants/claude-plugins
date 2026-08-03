"""Tests for --kind fixreview (#409 track B): the MERGED review+fix codex_job.py driver
mode that produces TWO candidate artifacts (a draft AND a review) from ONE codex call,
instead of one.

Companion file to tests/codex_job_driver.test.py (which owns translate/review coverage and
every kind-agnostic mechanism -- sandbox isolation/confinement, the flock lease, hygiene,
launch/poll, the fd-pinned publish-from-sandbox copy, --plugin-root redirection). This file
does NOT re-test any of that: it targets exactly what --kind fixreview adds on top --

  - the two-token CLI contract (--expect-token = draft token, --expect-review-token = review
    token; both required/forbidden the right way depending on --kind);
  - the two-placeholder prompt contract (JOB_OUT / JOB_OUT_REVIEW);
  - canonical_path()'s explicit refusal for this kind (no single canonical path exists);
  - the 4-gate validate-and-stage chain (_validate_fixreview_candidates): draft_ready ->
    validate_draft -> review_ready -> matchedVerdict, in that order, each short-circuiting
    the rest on a rejection;
  - matchedVerdict() executed against the REAL, shipped mass-translate-wf.template.js --
    never a hand-transcription of findingsAuthentic()/matchedVerdict()'s own JS logic (see
    codex_job.py's own module docstring and mass-translate-wf.template.js's "#348 -- one
    copy, not two" comment for why);
  - quarantine-on-rejection (move, never delete, both candidates);
  - staging-not-promotion (no canonical os.replace for this kind, ever) and the stdout
    report (staged_draft_path/staged_review_path/staged_draft_sha256/staged_review_sha256);
  - run()'s fixreview branch skipping safe_adopt()/adopt_pending() entirely.

Like the sibling file, STUB gate scripts (not the real draft_ready.py/validate_draft.py/
review_ready.py) drive the subprocess-level tests for gates 1-3 -- the driver only depends on
the FROZEN candidate-file CLI contract, and real-gate end-to-end coverage lives elsewhere in
this suite. Gate 4 (matchedVerdict) is driven two ways: directly (_matched_verdict() called on
the loaded module) and via a real end-to-end subprocess dispatch through a small JS fake
companion (never the Python FAKE_NODE the sibling file uses for task/status/cancel -- that stub
cannot execute real JS, so reaching gate 4 for real requires a real node interpreter end to end).

WITHOUT node ON PATH, GATE 4 IS NOT EXERCISED AGAINST THE REAL TEMPLATE AT ALL. Both of those
ways need node, so both are @skip_no_node, and every remaining test that touches gate 4
substitutes _matched_verdict_stub. Measured, not assumed: with a PATH carrying only python3 this
file exits 0 with the nine tests NODE_GATED_TESTS names skipped -- a full green that proves
nothing about the gate whose whole design is "never transcribed". (The count of skips is stated,
not the count of passes: a pass total rots on the next added test and would then be one more
number in this file that is confidently wrong.) An earlier version of this docstring claimed gate 4 "MUST
run against the real shipped template, everywhere it is exercised", which was true of the intent
and false of the suite; the skip is the repo-wide convention (see the ~10 sibling files that
skipif on shutil.which("node")) and is kept, but the claim is corrected and
test_the_node_gated_set_is_exactly_this pins the set so it cannot grow silently.
"""

import hashlib
import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets" / "scripts"
TEMPLATES_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets" / "templates"
DRIVER_SRC = SCRIPTS_DIR / "codex_job.py"
TEMPLATE_SRC = TEMPLATES_DIR / "mass-translate-wf.template.js"

assert DRIVER_SRC.is_file(), f"expected the driver at {DRIVER_SRC}"
assert TEMPLATE_SRC.is_file(), f"expected the real template at {TEMPLATE_SRC}"

_spec = importlib.util.spec_from_file_location("codex_job_fixreview_mod", str(DRIVER_SRC))
assert _spec is not None and _spec.loader is not None
codex_job = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(codex_job)

try:
    import fcntl  # noqa: F401
    _HAS_FLOCK = True
except ImportError:  # pragma: no cover - non-POSIX
    _HAS_FLOCK = False

skip_no_flock = pytest.mark.skipif(not _HAS_FLOCK, reason="fcntl.flock unavailable")

_NODE = shutil.which("node")
skip_no_node = pytest.mark.skipif(
    _NODE is None,
    reason="node not on PATH -- gate 4 is NOT exercised against the real template in this run")

#: Every test that cannot run without node, named explicitly.
#:
#: A skip is invisible in a green run: nine tests vanishing takes the suite from
#: 48 to 39 and prints nine dots' worth of 's'. Pinning the set by name means a
#: tenth one cannot join it silently -- someone adding a stubbed gate-4 test and
#: marking it skip_no_node has to come here and say so, which is the moment to
#: ask whether the real-template counterpart exists.
NODE_GATED_TESTS = frozenset({
    "test_matched_verdict_real_template_accepts_clean_review",
    "test_matched_verdict_real_template_accepts_authentic_loc",
    "test_matched_verdict_real_template_rejects_fabricated_loc",
    "test_matched_verdict_real_template_rejects_colonless_holistic_loc",
    "test_matched_verdict_missing_template_reports_error_not_crash",
    "test_matched_verdict_stale_truncate_marker_reports_error",
    "test_e2e_fixreview_stages_valid_pair",
    "test_e2e_fixreview_bad_draft_token_quarantines",
    "test_e2e_fixreview_fabricated_loc_review_quarantines_despite_valid_draft",
})


# --------------------------------------------------------------------------- #
# fixtures / helpers
# --------------------------------------------------------------------------- #
def _chmodx(path: Path):
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def _companion_file(tmp_path):
    c = tmp_path / "codex-companion.mjs"
    c.write_text("//\n", encoding="utf-8")
    return str(c)


_pf_ctr = [0]


def _prompt_file(tmp_path, text):
    _pf_ctr[0] += 1
    p = tmp_path / ("prompt_%d.txt" % _pf_ctr[0])
    p.write_text(text, encoding="utf-8")
    return str(p)


PROMPT_FIXREVIEW = (
    "Write your DRAFT JSON to ⟦JOB_OUT⟧ and your REVIEW JSON to "
    "⟦JOB_OUT_REVIEW⟧. Return DONE.\n"
)
PROMPT_TRANSLATE = "Write your JSON ONLY to ⟦JOB_OUT⟧ and return DONE.\n"


#: The plugin checkout's own root, for tests that need the REAL template.
#:
#: `--plugin-root` is not a test convenience here, it is the only correct way to
#: reach the template from a checkout. Self-anchored resolution is a PRODUCTION
#: path: codex_job.py is copied to <durable_root>/scripts/ at Step 0a and the
#: template is copied flat beside it, so "the directory this script lives in"
#: names both. In the checkout codex_job.py lives in assets/scripts/ while the
#: template lives in assets/templates/, so self-anchoring cannot work and must
#: not be faked -- faking it is exactly what hid the blocker this fixture pair
#: was built around.
CHECKOUT_PLUGIN_ROOT = str(PLUGIN_ROOT / "skills" / "literary-translator")


def _mkjob(tmp_path, kind="fixreview", seg="c001", tok="RUN:c001", review_tok="RUN:c001:r1",
           disp="d1", deadline=100, poll=1, node=None, plugin_root=None):
    seg_dir = tmp_path / "durable" / "segments"
    seg_dir.mkdir(parents=True, exist_ok=True)
    root = tmp_path / "durable"
    companion = _companion_file(tmp_path)
    return codex_job.CodexJob(
        kind=kind, seg=seg, tok=tok, disp=disp, root=str(root), companion=companion,
        prompt_text=PROMPT_FIXREVIEW if kind == "fixreview" else PROMPT_TRANSLATE,
        prompt_file=_prompt_file(tmp_path, PROMPT_FIXREVIEW if kind == "fixreview" else PROMPT_TRANSLATE),
        deadline_sec=deadline, poll_sec=poll, effort="high", node=node or "node",
        review_tok=review_tok if kind == "fixreview" else None,
        plugin_root=plugin_root,
    )


def _seed_fixreview_sandbox(tmp_path, job, draft_content=None, review_content=None,
                            draft_mode="file", review_mode="file"):
    """Mirrors the sibling file's _seed_sandbox(), extended for fixreview's TWO output
    slots. Sets job.sandbox_dir/job.sandbox_attempt/job.sandbox_attempt_review directly
    (no real _setup_sandbox() confinement probe -- irrelevant to these white-box,
    non-dispatching tests), and materializes each slot per its own `mode`
    ("file"/"absent"/"symlink")."""
    sbx = tmp_path / ("sandbox_%s_%s" % (job.seg, job.inv))
    sbx.mkdir(parents=True, exist_ok=True)
    job.sandbox_dir = str(sbx)
    job.sandbox_attempt = str(sbx / "attempt.draft.json")
    job.sandbox_attempt_review = str(sbx / "attempt.review.json")

    def _materialize(path_str, content, mode):
        if mode == "absent":
            return
        if mode == "symlink":
            target = Path(path_str + ".target")
            target.write_text("{}" if content is None else content, encoding="utf-8")
            os.symlink(target, path_str)
            return
        Path(path_str).write_text("{}" if content is None else content, encoding="utf-8")

    _materialize(job.sandbox_attempt, draft_content, draft_mode)
    _materialize(job.sandbox_attempt_review, review_content, review_mode)
    return job.sandbox_attempt, job.sandbox_attempt_review


def _draft_payload(tok, structure_ok=True, quality_ok=True):
    return json.dumps({"dispatch_token": tok, "seg": "c001",
                       "structure_ok": structure_ok, "quality_ok": quality_ok})


def _review_payload(tok, schema_ok=True, clean=True, findings=None):
    return json.dumps({
        "dispatch_token": tok, "schema_ok": schema_ok, "clean": clean, "coverage_ok": True,
        "findings": findings if findings is not None else [], "draft_sha1": "deadbeef",
    })


def _gate_recorder(results):
    """results: gate-name -> returncode (int). Mirrors the sibling file's own helper,
    duplicated per this project's "no shared lib between test files, either" convention."""
    calls = []

    def _gate(args, timeout):
        calls.append(args[0])
        rc = results.get(args[0], 0)
        return SimpleNamespace(returncode=rc, stdout="", stderr="")
    return _gate, calls


def _matched_verdict_stub(result):
    """result: (verdict_dict_or_None, err_str_or_None). Returns a stand-in for
    CodexJob._matched_verdict bound as (self, review_obj, timeout) -> result, plus a
    call-count box so a test can assert whether it was reached at all."""
    calls = {"n": 0}

    def _mv(review_obj, timeout):
        calls["n"] += 1
        return result
    return _mv, calls


# --------------------------------------------------------------------------- #
# CLI / usage
# --------------------------------------------------------------------------- #
def _argv(tmp_path, **over):
    prompt_text = over.pop("prompt_text", PROMPT_FIXREVIEW)
    d = dict(kind="fixreview", companion=_companion_file(tmp_path), cwd=str(tmp_path),
             seg="c001", prompt_file=_prompt_file(tmp_path, prompt_text),
             expect_token="RUN:c001", expect_review_token="RUN:c001:r1",
             disp="d1", deadline_sec="600")
    d.update(over)
    argv = ["--kind", d["kind"], "--companion", d["companion"], "--cwd", d["cwd"],
            "--seg", d["seg"], "--prompt-file", d["prompt_file"],
            "--expect-token", d["expect_token"], "--disp", d["disp"],
            "--deadline-sec", d["deadline_sec"], "--node", "node"]
    if d.get("expect_review_token") is not None:
        argv += ["--expect-review-token", d["expect_review_token"]]
    return argv


def test_kind_fixreview_is_a_valid_argparse_choice():
    p = codex_job._build_parser()
    ns = p.parse_args(["--kind", "fixreview", "--companion", "x", "--cwd", "y",
                       "--seg", "c001", "--prompt-file", "z", "--expect-token", "t",
                       "--expect-review-token", "rt", "--disp", "d1", "--deadline-sec", "5"])
    assert ns.kind == "fixreview"
    assert ns.expect_review_token == "rt"


def test_fixreview_requires_expect_review_token(tmp_path):
    argv = _argv(tmp_path, expect_review_token=None)
    rc = codex_job.main(argv)
    assert rc == 2


def test_fixreview_rejects_whitespace_only_expect_review_token(tmp_path):
    argv = _argv(tmp_path, expect_review_token="   ")
    rc = codex_job.main(argv)
    assert rc == 2


def test_expect_review_token_rejected_for_translate_kind(tmp_path):
    argv = _argv(tmp_path, kind="translate", prompt_text=PROMPT_TRANSLATE)
    # translate has no JOB_OUT_REVIEW placeholder requirement, but DOES still carry
    # --expect-review-token in this argv (from _argv's default) -- must be refused.
    rc = codex_job.main(argv)
    assert rc == 2


def test_fixreview_requires_job_out_review_placeholder(tmp_path):
    argv = _argv(tmp_path, prompt_text="Only ⟦JOB_OUT⟧ here, no second slot.\n")
    rc = codex_job.main(argv)
    assert rc == 2


def test_fixreview_rejects_duplicate_job_out_review_placeholder(tmp_path):
    text = (
        "⟦JOB_OUT⟧ and ⟦JOB_OUT_REVIEW⟧ and again "
        "⟦JOB_OUT_REVIEW⟧.\n"
    )
    argv = _argv(tmp_path, prompt_text=text)
    rc = codex_job.main(argv)
    assert rc == 2


def test_job_out_review_placeholder_rejected_for_translate_kind(tmp_path):
    text = "⟦JOB_OUT⟧ and stray ⟦JOB_OUT_REVIEW⟧ too.\n"
    argv = _argv(tmp_path, kind="translate", expect_review_token=None, prompt_text=text)
    rc = codex_job.main(argv)
    assert rc == 2


# --------------------------------------------------------------------------- #
# canonical_path()
# --------------------------------------------------------------------------- #
def test_canonical_path_raises_for_fixreview():
    with pytest.raises(ValueError):
        codex_job.canonical_path("/fixture/durable_root", "c001", "fixreview")


def test_canonical_path_translate_review_unaffected():
    assert codex_job.canonical_path("/r", "c001", "translate").endswith("c001.draft.json")
    assert codex_job.canonical_path("/r", "c001", "review").endswith("c001.review.json")


# --------------------------------------------------------------------------- #
# __init__ wiring
# --------------------------------------------------------------------------- #
def test_fixreview_init_has_no_single_canonical(tmp_path):
    job = _mkjob(tmp_path)
    assert job.canonical is None
    assert job.attempt.endswith(".draft.json")
    assert job.attempt_review.endswith(".review.json")
    assert job.attempt != job.attempt_review
    assert job.quarantine_draft is not None and job.quarantine_draft.endswith(".draft.json")
    assert job.quarantine_review is not None and job.quarantine_review.endswith(".review.json")
    assert job.pending is not None   # unused for fixreview, but present for _preflight_same_device
    assert job.staged is False
    assert job.review_tok == "RUN:c001:r1"


def test_translate_review_init_have_no_attempt_review(tmp_path):
    job = _mkjob(tmp_path, kind="translate", tok="RUN:c001", review_tok=None)
    assert job.attempt_review is None
    assert job.quarantine_draft is None
    assert job.quarantine_review is None
    assert job.canonical is not None
    assert job.review_tok is None


def test_preflight_same_device_passes_for_fixreview(tmp_path):
    job = _mkjob(tmp_path)
    assert job._preflight_same_device() is True


# --------------------------------------------------------------------------- #
# _setup_sandbox / _write_final_prompt
# --------------------------------------------------------------------------- #
def test_setup_sandbox_creates_two_slots(tmp_path):
    job = _mkjob(tmp_path)
    assert job._setup_sandbox() is True
    assert job.sandbox_attempt.endswith("attempt.draft.json")
    assert job.sandbox_attempt_review.endswith("attempt.review.json")
    assert os.path.dirname(job.sandbox_attempt) == os.path.dirname(job.sandbox_attempt_review)


def test_write_final_prompt_substitutes_both_placeholders(tmp_path):
    job = _mkjob(tmp_path)
    assert job._setup_sandbox() is True
    job._write_final_prompt()
    text = Path(job.final_prompt).read_text(encoding="utf-8")
    assert "⟦JOB_OUT⟧" not in text
    assert "⟦JOB_OUT_REVIEW⟧" not in text
    assert job.sandbox_attempt in text
    assert job.sandbox_attempt_review in text


# --------------------------------------------------------------------------- #
# _validate_fixreview_candidates: order, short-circuit, token wiring
# --------------------------------------------------------------------------- #
def test_validate_fixreview_all_pass_stages_both(tmp_path, monkeypatch):
    job = _mkjob(tmp_path)
    _seed_fixreview_sandbox(tmp_path, job)
    gate, calls = _gate_recorder({})
    monkeypatch.setattr(job, "_gate", gate)
    mv, mv_calls = _matched_verdict_stub(({"status": "ok", "rev": {}}, None))
    monkeypatch.setattr(job, "_matched_verdict", mv)

    assert job._validate_fixreview_candidates(lambda: 10) is True
    assert calls == ["draft_ready.py", "validate_draft.py", "review_ready.py"]
    assert mv_calls["n"] == 1
    assert os.path.exists(job.attempt)          # PUBLISHED into staging before gating
    assert os.path.exists(job.attempt_review)


def test_validate_fixreview_draft_ready_rejects_short_circuits(tmp_path, monkeypatch):
    job = _mkjob(tmp_path)
    _seed_fixreview_sandbox(tmp_path, job)
    gate, calls = _gate_recorder({"draft_ready.py": 1})
    monkeypatch.setattr(job, "_gate", gate)
    mv, mv_calls = _matched_verdict_stub(({"status": "ok"}, None))
    monkeypatch.setattr(job, "_matched_verdict", mv)

    assert job._validate_fixreview_candidates(lambda: 10) is False
    assert calls == ["draft_ready.py"]
    assert mv_calls["n"] == 0, "matchedVerdict must never run once an earlier gate rejected"


def test_validate_fixreview_validate_draft_rejects_short_circuits(tmp_path, monkeypatch):
    job = _mkjob(tmp_path)
    _seed_fixreview_sandbox(tmp_path, job)
    gate, calls = _gate_recorder({"draft_ready.py": 0, "validate_draft.py": 1})
    monkeypatch.setattr(job, "_gate", gate)
    mv, mv_calls = _matched_verdict_stub(({"status": "ok"}, None))
    monkeypatch.setattr(job, "_matched_verdict", mv)

    assert job._validate_fixreview_candidates(lambda: 10) is False
    assert calls == ["draft_ready.py", "validate_draft.py"]
    assert mv_calls["n"] == 0


def test_validate_fixreview_review_ready_rejects_short_circuits(tmp_path, monkeypatch):
    job = _mkjob(tmp_path)
    _seed_fixreview_sandbox(tmp_path, job)
    gate, calls = _gate_recorder({"draft_ready.py": 0, "validate_draft.py": 0,
                                  "review_ready.py": 1})
    monkeypatch.setattr(job, "_gate", gate)
    mv, mv_calls = _matched_verdict_stub(({"status": "ok"}, None))
    monkeypatch.setattr(job, "_matched_verdict", mv)

    assert job._validate_fixreview_candidates(lambda: 10) is False
    assert calls == ["draft_ready.py", "validate_draft.py", "review_ready.py"]
    assert mv_calls["n"] == 0, "matchedVerdict is gate 4 -- must never run before gate 3 passes"


def test_validate_fixreview_matched_verdict_blocked_rejects(tmp_path, monkeypatch):
    job = _mkjob(tmp_path)
    _seed_fixreview_sandbox(tmp_path, job)
    gate, calls = _gate_recorder({})
    monkeypatch.setattr(job, "_gate", gate)
    mv, mv_calls = _matched_verdict_stub(
        ({"status": "blocked", "reason": "review-fabricated-loc"}, None))
    monkeypatch.setattr(job, "_matched_verdict", mv)

    assert job._validate_fixreview_candidates(lambda: 10) is False
    assert mv_calls["n"] == 1
    assert "matchedVerdict" in job.error_detail
    assert "review-fabricated-loc" in job.error_detail or "blocked" in job.error_detail


def test_validate_fixreview_matched_verdict_harness_failure_fails_closed(tmp_path, monkeypatch):
    """A harness that could not even PRODUCE a verdict (None) must be treated exactly
    like a rejection -- never as an implicit pass."""
    job = _mkjob(tmp_path)
    _seed_fixreview_sandbox(tmp_path, job)
    gate, calls = _gate_recorder({})
    monkeypatch.setattr(job, "_gate", gate)
    mv, mv_calls = _matched_verdict_stub((None, "node timed out running the matchedVerdict harness"))
    monkeypatch.setattr(job, "_matched_verdict", mv)

    assert job._validate_fixreview_candidates(lambda: 10) is False
    assert "node timed out" in job.error_detail


def test_validate_fixreview_uses_the_two_correct_tokens(tmp_path, monkeypatch):
    """The key two-token correctness property: draft_ready gets the DRAFT token
    (job.tok), review_ready gets the REVIEW token (job.review_tok) -- never swapped,
    never the same value reused."""
    job = _mkjob(tmp_path, tok="RUN:c001", review_tok="RUN:c001:r3")
    _seed_fixreview_sandbox(tmp_path, job)
    captured = {}

    def gate(args, timeout):
        captured[args[0]] = args
        return SimpleNamespace(returncode=0, stdout="", stderr="")
    monkeypatch.setattr(job, "_gate", gate)
    mv, _ = _matched_verdict_stub(({"status": "ok"}, None))
    monkeypatch.setattr(job, "_matched_verdict", mv)

    assert job._validate_fixreview_candidates(lambda: 10) is True
    dr = captured["draft_ready.py"]
    rr = captured["review_ready.py"]
    assert dr[dr.index("--expect-token") + 1] == "RUN:c001"
    assert rr[rr.index("--expect-token") + 1] == "RUN:c001:r3"
    assert dr[dr.index("--candidate-file") + 1] == job.attempt
    assert rr[rr.index("--candidate-file") + 1] == job.attempt_review


def test_validate_fixreview_review_publish_failure_before_any_gate(tmp_path, monkeypatch):
    job = _mkjob(tmp_path)
    _seed_fixreview_sandbox(tmp_path, job, review_mode="absent")
    gate, calls = _gate_recorder({})
    monkeypatch.setattr(job, "_gate", gate)

    assert job._validate_fixreview_candidates(lambda: 10) is False
    assert calls == []
    assert os.path.exists(job.attempt)           # draft WAS published
    assert not os.path.exists(job.attempt_review)  # review never was


def test_validate_fixreview_draft_symlink_refused(tmp_path, monkeypatch):
    job = _mkjob(tmp_path)
    _seed_fixreview_sandbox(tmp_path, job, draft_mode="symlink")
    gate, calls = _gate_recorder({})
    monkeypatch.setattr(job, "_gate", gate)

    assert job._validate_fixreview_candidates(lambda: 10) is False
    assert calls == []
    assert not os.path.exists(job.attempt)


# --------------------------------------------------------------------------- #
# quarantine
# --------------------------------------------------------------------------- #
def test_quarantine_moves_both_existing_candidates(tmp_path):
    job = _mkjob(tmp_path)
    Path(job.attempt).write_text('{"marker":"draft"}', encoding="utf-8")
    Path(job.attempt_review).write_text('{"marker":"review"}', encoding="utf-8")
    job._quarantine_fixreview_candidates()
    assert not os.path.exists(job.attempt)
    assert not os.path.exists(job.attempt_review)
    assert json.loads(Path(job.quarantine_draft).read_text())["marker"] == "draft"
    assert json.loads(Path(job.quarantine_review).read_text())["marker"] == "review"


def test_quarantine_handles_partial_publish(tmp_path):
    job = _mkjob(tmp_path)
    Path(job.attempt).write_text("{}", encoding="utf-8")
    job._quarantine_fixreview_candidates()
    assert os.path.exists(job.quarantine_draft)
    assert not os.path.exists(job.quarantine_review)


def test_quarantine_is_noop_when_nothing_published(tmp_path):
    job = _mkjob(tmp_path)
    job._quarantine_fixreview_candidates()   # must not raise
    assert not os.path.exists(job.quarantine_draft)
    assert not os.path.exists(job.quarantine_review)


def test_a_candidate_quarantine_FAILED_to_move_is_not_deleted_by_finalize(tmp_path, monkeypatch):
    """The whole point of quarantining instead of deleting, and the one path
    that used to defeat it.

    finalize() removes both candidates whenever staged is False, reasoning that
    they were either already moved out or never created. A swallowed OSError is
    a third case: the file is still at self.attempt, and the unconditional
    delete then destroys exactly the evidence -- a fabricated-loc review
    together with the REAL draft edit that accompanied it, which for this kind
    is the user's own text.

    Both slots fail here, so neither survives by accident of the other."""
    job = _mkjob(tmp_path)
    Path(job.attempt).write_text('{"marker":"draft"}', encoding="utf-8")
    Path(job.attempt_review).write_text('{"marker":"review"}', encoding="utf-8")

    def refuse(src, dst):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(codex_job.os, "replace", refuse)
    job.quarantine_stuck = job._quarantine_fixreview_candidates()
    monkeypatch.undo()

    assert job.quarantine_stuck == {"draft", "review"}
    assert not os.path.exists(job.quarantine_draft), "the move genuinely failed"

    job.finalize()

    assert json.loads(Path(job.attempt).read_text())["marker"] == "draft", (
        "an unmovable candidate must be LEFT BEHIND, never deleted"
    )
    assert json.loads(Path(job.attempt_review).read_text())["marker"] == "review"


def test_a_successful_quarantine_still_lets_finalize_clean_up(tmp_path):
    """The other direction, so the fix above cannot be satisfied by simply
    never deleting anything: when the move worked there is nothing left at the
    .att paths, and finalize's removal stays a harmless no-op."""
    job = _mkjob(tmp_path)
    Path(job.attempt).write_text('{"marker":"draft"}', encoding="utf-8")
    Path(job.attempt_review).write_text('{"marker":"review"}', encoding="utf-8")
    job.quarantine_stuck = job._quarantine_fixreview_candidates()
    assert job.quarantine_stuck == set()

    job.finalize()

    assert not os.path.exists(job.attempt)
    assert json.loads(Path(job.quarantine_draft).read_text())["marker"] == "draft"
    assert json.loads(Path(job.quarantine_review).read_text())["marker"] == "review"


# --------------------------------------------------------------------------- #
# matchedVerdict() against the REAL, shipped template -- never a stub. This is the
# gate that must NEVER be hand-transcribed; these tests prove the harness genuinely
# executes the template's own JS, not a Python reading of the same rule.
# --------------------------------------------------------------------------- #
@skip_no_node
def test_matched_verdict_real_template_accepts_clean_review(tmp_path):
    job = _mkjob(tmp_path, node=_NODE, plugin_root=CHECKOUT_PLUGIN_ROOT)
    verdict, err = job._matched_verdict(
        {"clean": True, "coverage_ok": True, "findings": [], "draft_sha1": "deadbeef"}, 30)
    assert err is None, err
    assert verdict == {"status": "ok", "rev": {"clean": True, "coverage_ok": True,
                                               "findings": [], "draft_sha1": "deadbeef"}}


@skip_no_node
def test_matched_verdict_real_template_accepts_authentic_loc(tmp_path):
    job = _mkjob(tmp_path, node=_NODE, plugin_root=CHECKOUT_PLUGIN_ROOT)
    rev = {"clean": False, "coverage_ok": True, "draft_sha1": "deadbeef", "findings": [
        {"loc": "PARA:seg01:0001", "severity": "low", "issue": "x", "suggest": "y"},
    ]}
    verdict, err = job._matched_verdict(rev, 30)
    assert err is None, err
    assert verdict["status"] == "ok"


@skip_no_node
def test_matched_verdict_real_template_rejects_fabricated_loc(tmp_path):
    """The exact RED-BEFORE-GREEN property this gate exists for: a colonless,
    infra-sentinel-shaped loc (e.g. "TASK") fails mass-translate-wf.template.js's own
    AUTHENTIC_LOC_RE and must be BLOCKED, not silently accepted."""
    job = _mkjob(tmp_path, node=_NODE, plugin_root=CHECKOUT_PLUGIN_ROOT)
    rev = {"clean": False, "coverage_ok": True, "draft_sha1": "deadbeef", "findings": [
        {"loc": "TASK", "severity": "low", "issue": "x", "suggest": "y"},
    ]}
    verdict, err = job._matched_verdict(rev, 30)
    assert err is None, err
    assert verdict == {"status": "blocked", "reason": "review-fabricated-loc"}


@skip_no_node
def test_matched_verdict_real_template_rejects_colonless_holistic_loc(tmp_path):
    job = _mkjob(tmp_path, node=_NODE, plugin_root=CHECKOUT_PLUGIN_ROOT)
    rev = {"clean": False, "coverage_ok": True, "draft_sha1": "deadbeef", "findings": [
        {"loc": "overall", "severity": "low", "issue": "x", "suggest": "y"},
    ]}
    verdict, err = job._matched_verdict(rev, 30)
    assert err is None, err
    assert verdict["status"] == "blocked"


@skip_no_node
def test_matched_verdict_missing_template_reports_error_not_crash(tmp_path):
    empty_plugin_root = tmp_path / "no_templates_here"
    (empty_plugin_root / "assets" / "templates").mkdir(parents=True)
    (empty_plugin_root / "assets" / "scripts").mkdir(parents=True)
    job = _mkjob(tmp_path, node=_NODE, plugin_root=CHECKOUT_PLUGIN_ROOT)
    job.plugin_root = str(empty_plugin_root)
    verdict, err = job._matched_verdict({"clean": True, "coverage_ok": True, "findings": [],
                                         "draft_sha1": "x"}, 30)
    assert verdict is None
    assert err is not None and "could not read" in err


@skip_no_node
def test_matched_verdict_stale_truncate_marker_reports_error(tmp_path):
    fake_root = tmp_path / "fake_plugin"
    (fake_root / "assets" / "templates").mkdir(parents=True)
    (fake_root / "assets" / "scripts").mkdir(parents=True)
    (fake_root / "assets" / "templates" / "mass-translate-wf.template.js").write_text(
        "export function matchedVerdict(rev) { return { status: 'ok', rev }; }\n",
        encoding="utf-8",
    )
    job = _mkjob(tmp_path, node=_NODE, plugin_root=CHECKOUT_PLUGIN_ROOT)
    job.plugin_root = str(fake_root)
    verdict, err = job._matched_verdict({"clean": True, "coverage_ok": True, "findings": [],
                                         "draft_sha1": "x"}, 30)
    assert verdict is None
    assert err is not None and "truncation marker" in err


def test_matched_verdict_uses_plugin_root_redirect_when_given(tmp_path):
    """--plugin-root redirects the TEMPLATE lookup exactly like it redirects gate
    EXECUTABLES (_trusted_scripts_dir) -- same trust boundary, same fallback rule."""
    job = _mkjob(tmp_path)
    job.plugin_root = "/fixture/plugin_root"
    assert job._trusted_template_path() == os.path.join(
        "/fixture/plugin_root", "assets", "templates", "mass-translate-wf.template.js")


def test_the_self_anchored_template_path_is_where_the_SCAFFOLDER_actually_puts_it(tmp_path):
    """The fallback must name a path Step 0a really creates, and this asserts it
    against the scaffolder's own rule rather than against a hand-built tree.

    scaffold_setup.py:217 places EVERY bundle member -- the .py gates and the
    .template.js templates alike -- at ${durable_root}/scripts/<name>, and its
    docstring says so in as many words: "there is no scripts/templates/ subdir".
    mass-translate-wf.template.js is a PLUGIN_BUNDLE_MEMBER, so in a shipped
    deployment it sits flat in scripts/ beside draft_ready.py.

    The previous version of this test asserted dirname(SCRIPTS_DIR)/templates/,
    a directory nothing ever creates -- so with --plugin-root omitted (which is
    documented as optional) gate 4 could never pass, every fixreview dispatch
    would quarantine both candidates and exit 1, and this test pinned that as
    correct. Written to fail against that behaviour before the fix."""
    ck_spec = importlib.util.spec_from_file_location(
        "cache_key_for_template_placement", str(SCRIPTS_DIR / "cache_key.py"))
    assert ck_spec is not None and ck_spec.loader is not None
    cache_key = importlib.util.module_from_spec(ck_spec)
    ck_spec.loader.exec_module(cache_key)
    assert "mass-translate-wf.template.js" in cache_key.PLUGIN_BUNDLE_MEMBERS, (
        "premise of this test: the template is a bundle member, so the scaffolder "
        "places it the same way it places every other member"
    )

    job = _mkjob(tmp_path)
    job.plugin_root = None
    assert job._trusted_template_path() == os.path.join(
        codex_job.SCRIPTS_DIR, "mass-translate-wf.template.js"), (
        "the self-anchored template path must sit flat in scripts/, beside the .py "
        "gates, because that is where scaffold_setup.py:217 puts every bundle member"
    )


# --------------------------------------------------------------------------- #
# run() orchestration: staging, quarantine, skip-safe_adopt/adopt_pending, finalize
# --------------------------------------------------------------------------- #
def test_run_fixreview_stages_on_full_pass(tmp_path, monkeypatch):
    job = _mkjob(tmp_path, deadline=100)
    monkeypatch.setattr(job, "hygiene", lambda: None)

    def fake_launch():
        job.jobId = "J"
        Path(job.sandbox_attempt).write_text(_draft_payload(job.tok), encoding="utf-8")
        Path(job.sandbox_attempt_review).write_text(_review_payload(job.review_tok), encoding="utf-8")
        return True
    monkeypatch.setattr(job, "launch", fake_launch)
    monkeypatch.setattr(job, "poll", lambda: setattr(job, "job_status", "completed"))
    gate, calls = _gate_recorder({})
    monkeypatch.setattr(job, "_gate", gate)
    mv, _ = _matched_verdict_stub(({"status": "ok"}, None))
    monkeypatch.setattr(job, "_matched_verdict", mv)

    rc = job.run()
    assert rc == 0
    assert job.staged is True
    assert job.promoted is False
    assert job.adopted is False
    assert job.reason == "staged"
    assert os.path.exists(job.attempt)            # STILL there -- staged, never removed
    assert os.path.exists(job.attempt_review)
    assert not os.path.exists(job.fail_sentinel)


def test_run_fixreview_never_calls_safe_adopt_or_adopt_pending(tmp_path, monkeypatch):
    job = _mkjob(tmp_path, deadline=100)
    monkeypatch.setattr(job, "hygiene", lambda: None)

    def boom():
        raise AssertionError("safe_adopt/adopt_pending must never run for --kind fixreview")
    monkeypatch.setattr(job, "safe_adopt", boom)
    monkeypatch.setattr(job, "adopt_pending", boom)

    def fake_launch():
        job.jobId = "J"
        Path(job.sandbox_attempt).write_text(_draft_payload(job.tok), encoding="utf-8")
        Path(job.sandbox_attempt_review).write_text(_review_payload(job.review_tok), encoding="utf-8")
        return True
    monkeypatch.setattr(job, "launch", fake_launch)
    monkeypatch.setattr(job, "poll", lambda: setattr(job, "job_status", "completed"))
    gate, calls = _gate_recorder({})
    monkeypatch.setattr(job, "_gate", gate)
    mv, _ = _matched_verdict_stub(({"status": "ok"}, None))
    monkeypatch.setattr(job, "_matched_verdict", mv)

    rc = job.run()
    assert rc == 0   # would have raised above if either were reached


def test_run_fixreview_quarantines_on_gate_rejection(tmp_path, monkeypatch):
    """End-to-end through run() (not a monkeypatched _validate_fixreview_candidates):
    a REAL rejecting gate must lead to a REAL quarantine, canonical (N/A for this
    kind) untouched, staged=False, fail sentinel written."""
    job = _mkjob(tmp_path, deadline=100)
    monkeypatch.setattr(job, "hygiene", lambda: None)

    def fake_launch():
        job.jobId = "J"
        Path(job.sandbox_attempt).write_text(_draft_payload(job.tok), encoding="utf-8")
        Path(job.sandbox_attempt_review).write_text(_review_payload(job.review_tok), encoding="utf-8")
        return True
    monkeypatch.setattr(job, "launch", fake_launch)
    monkeypatch.setattr(job, "poll", lambda: setattr(job, "job_status", "completed"))
    # review_ready rejects (e.g. a stale/wrong review token) -- gates 1-2 pass first.
    gate, calls = _gate_recorder({"review_ready.py": 1})
    monkeypatch.setattr(job, "_gate", gate)
    mv, mv_calls = _matched_verdict_stub(({"status": "ok"}, None))
    monkeypatch.setattr(job, "_matched_verdict", mv)

    rc = job.run()
    assert rc == 1
    assert job.staged is False
    assert job.reason == "validate-failed"
    assert mv_calls["n"] == 0, "matchedVerdict must never run once review_ready rejected"
    assert not os.path.exists(job.attempt)               # quarantined, not left at the staging slot
    assert not os.path.exists(job.attempt_review)
    assert os.path.exists(job.quarantine_draft)           # but genuinely preserved, not deleted
    assert os.path.exists(job.quarantine_review)
    assert os.path.exists(job.fail_sentinel)


def test_run_fixreview_budget_exhausted_completed_neither_staged_nor_deferred(tmp_path, monkeypatch):
    """Mirrors the sibling file's test_run_refuses_promote_when_budget_exhausted /
    test_run_defers_completed_attempt_when_budget_exhausted, but for fixreview there is
    NO #213 defer/adopt_pending counterpart -- a completed-but-unvalidated fixreview
    attempt is simply discarded, not deferred (see run()'s own fixreview comment)."""
    job = _mkjob(tmp_path, deadline=5)
    monkeypatch.setattr(job, "hygiene", lambda: None)

    def fake_launch():
        job.jobId = "J"
        Path(job.sandbox_attempt).write_text(_draft_payload(job.tok), encoding="utf-8")
        Path(job.sandbox_attempt_review).write_text(_review_payload(job.review_tok), encoding="utf-8")
        return True
    monkeypatch.setattr(job, "launch", fake_launch)
    monkeypatch.setattr(job, "poll", lambda: setattr(job, "job_status", "completed"))
    monkeypatch.setattr(job, "abs_remaining", lambda: 2.0)   # exhausted vs. FINALIZE_TAIL
    validated = {"called": False}
    monkeypatch.setattr(job, "_validate_fixreview_candidates",
                        lambda timeout_fn: validated.__setitem__("called", True) or True)

    rc = job.run()
    assert rc == 1
    assert validated["called"] is False   # refused to even BEGIN validation
    assert job.staged is False
    assert job.reason == "job-completed"
    assert not os.path.exists(job.attempt)          # never published (sandbox never read)
    assert not os.path.exists(job.attempt_review)
    assert not os.path.exists(job.quarantine_draft)  # nothing to quarantine either
    assert os.path.exists(job.fail_sentinel)


def test_run_refuses_dispatch_on_device_mismatch_fixreview(tmp_path, monkeypatch):
    job = _mkjob(tmp_path)
    monkeypatch.setattr(job, "_preflight_same_device", lambda: False)
    rc = job.run()
    assert rc == 1
    assert job.reason == "device-mismatch"
    assert job.staged is False
    assert os.path.exists(job.fail_sentinel)


# --------------------------------------------------------------------------- #
# finalize(): stdout line fields, staged vs not-staged
# --------------------------------------------------------------------------- #
def _run_line(proc_or_job_run_stdout):
    return json.loads(proc_or_job_run_stdout.strip().splitlines()[-1])


def test_finalize_stdout_reports_staged_paths_and_matching_digests(tmp_path, monkeypatch, capsys):
    job = _mkjob(tmp_path, deadline=100)
    monkeypatch.setattr(job, "hygiene", lambda: None)

    def fake_launch():
        job.jobId = "J"
        Path(job.sandbox_attempt).write_text(_draft_payload(job.tok), encoding="utf-8")
        Path(job.sandbox_attempt_review).write_text(_review_payload(job.review_tok), encoding="utf-8")
        return True
    monkeypatch.setattr(job, "launch", fake_launch)
    monkeypatch.setattr(job, "poll", lambda: setattr(job, "job_status", "completed"))
    gate, calls = _gate_recorder({})
    monkeypatch.setattr(job, "_gate", gate)
    mv, _ = _matched_verdict_stub(({"status": "ok"}, None))
    monkeypatch.setattr(job, "_matched_verdict", mv)

    rc = job.run()
    assert rc == 0
    out = capsys.readouterr().out
    line = _run_line(out)
    assert line["staged"] is True
    assert line["staged_draft_path"] == job.attempt
    assert line["staged_review_path"] == job.attempt_review
    assert line["staged_draft_sha256"] == hashlib.sha256(Path(job.attempt).read_bytes()).hexdigest()
    assert line["staged_review_sha256"] == hashlib.sha256(Path(job.attempt_review).read_bytes()).hexdigest()


def test_the_reported_digest_is_the_VALIDATED_one_not_a_later_re_read(tmp_path, monkeypatch, capsys):
    """The digest the transaction layer binds its intent to must describe the
    bytes the four gates checked.

    Re-hashing the path in finalize -- which is what this used to do -- reports a
    digest for whatever is there at report time. A rewrite landing after gate 4
    would then be handed on under a digest that makes it look validated, and
    publish_txn's own staged-source confirm would happily agree, because both
    sides would be describing the same unvalidated bytes.

    Here the staged draft is rewritten after validation. The run must refuse to
    report a staging at all rather than report the new bytes, or the old digest
    against the new file."""
    job = _mkjob(tmp_path, deadline=100)
    monkeypatch.setattr(job, "hygiene", lambda: None)
    monkeypatch.setattr(job, "_preflight_same_device", lambda: True)
    monkeypatch.setattr(job, "safe_adopt", lambda: False)

    def fake_launch():
        _seed_fixreview_sandbox(tmp_path, job)
        Path(job.sandbox_attempt).write_text(_draft_payload(job.tok), encoding="utf-8")
        Path(job.sandbox_attempt_review).write_text(_review_payload(job.review_tok), encoding="utf-8")
        return True
    monkeypatch.setattr(job, "launch", fake_launch)
    monkeypatch.setattr(job, "poll", lambda: setattr(job, "job_status", "completed"))
    gate, _ = _gate_recorder({})
    monkeypatch.setattr(job, "_gate", gate)
    mv, _ = _matched_verdict_stub(({"status": "ok"}, None))

    def validate_then_tamper(review_obj, timeout):
        result = mv(review_obj, timeout)
        # Lands after every gate has passed, before finalize reports.
        Path(job.attempt).write_text('{"rewritten":"after the gates"}', encoding="utf-8")
        return result
    monkeypatch.setattr(job, "_matched_verdict", validate_then_tamper)

    job.run()
    line = _run_line(capsys.readouterr().out)

    assert line["staged"] is False, "a post-validation rewrite must not be reported as staged"
    assert line["staged_draft_sha256"] is None
    assert line["staged_draft_path"] is None
    assert line["reason"] == "staged-digest-drifted"
    assert line["ok"] is False


def test_a_staging_with_no_captured_digest_is_refused_not_reported_as_null(tmp_path, capsys):
    """Fail CLOSED when there is nothing to compare.

    A digest is captured only by _publish_from_sandbox, which is the step that
    verifies the bytes landed. If self.staged is somehow true without one, the
    honest answer is that this run cannot vouch for the bytes -- not to report
    the staging with a null digest, which downstream reads as "staged, digest
    unknown" and is exactly what write_txn_intent would then record.

    Mutation is why this test exists: turning the missing-digest branch into a
    `continue` left the whole suite green, so nothing held the fail-closed half
    of a guard whose other half was covered."""
    job = _mkjob(tmp_path)
    Path(job.attempt).write_text('{"marker":"draft"}', encoding="utf-8")
    Path(job.attempt_review).write_text('{"marker":"review"}', encoding="utf-8")
    job.staged = True
    assert job.published_digests == {}, "premise: nothing verified these bytes"

    job.finalize()
    line = _run_line(capsys.readouterr().out)

    assert line["staged"] is False
    assert line["staged_draft_sha256"] is None
    assert line["staged_draft_path"] is None
    assert line["reason"] == "staged-digest-drifted"


def test_finalize_stdout_fields_absent_shaped_when_not_staged(tmp_path, monkeypatch, capsys):
    job = _mkjob(tmp_path)
    monkeypatch.setattr(job, "_preflight_same_device", lambda: False)
    rc = job.run()
    assert rc == 1
    out = capsys.readouterr().out
    line = _run_line(out)
    assert line["staged"] is False
    assert line["staged_draft_path"] is None
    assert line["staged_review_path"] is None
    assert line["staged_draft_sha256"] is None
    assert line["staged_review_sha256"] is None


def test_finalize_does_not_add_fixreview_fields_for_translate(tmp_path, monkeypatch, capsys):
    job = _mkjob(tmp_path, kind="translate", tok="RUN:c001", review_tok=None)
    monkeypatch.setattr(job, "_preflight_same_device", lambda: False)
    job.run()
    out = capsys.readouterr().out
    line = _run_line(out)
    assert "staged" not in line
    assert "staged_draft_path" not in line


# --------------------------------------------------------------------------- #
# Full subprocess dispatch, real node end to end (gates 1-3 stubbed, gate 4 REAL --
# see this file's own module docstring for why gate 4 specifically is never stubbed).
# --------------------------------------------------------------------------- #
STUB_DRAFT_READY = '''#!/usr/bin/env python3
import argparse, json, os, sys
p = argparse.ArgumentParser()
p.add_argument("seg")
p.add_argument("--expect-token", dest="tok", default=None)
p.add_argument("--candidate-file", dest="cf", default=None)
p.add_argument("--durable-root", dest="dr", default=None)
a = p.parse_args()
root = os.path.abspath(a.dr) if a.dr else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
path = a.cf if a.cf else os.path.join(root, "segments", a.seg + ".draft.json")
try:
    d = json.load(open(path, encoding="utf-8"))
except Exception as e:
    print("not ready: %s" % e); sys.exit(1)
if not isinstance(d, dict) or not d.get("structure_ok"):
    print("not ready: structure"); sys.exit(1)
if a.tok is not None and d.get("dispatch_token") != a.tok:
    print("not ready: token"); sys.exit(1)
print("[%s] READY" % a.seg); sys.exit(0)
'''

STUB_VALIDATE_DRAFT = '''#!/usr/bin/env python3
import argparse, json, os, sys
p = argparse.ArgumentParser()
p.add_argument("seg")
p.add_argument("--candidate-file", dest="cf", default=None)
p.add_argument("--durable-root", dest="dr", default=None)
a = p.parse_args()
root = os.path.abspath(a.dr) if a.dr else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
path = a.cf if a.cf else os.path.join(root, "segments", a.seg + ".draft.json")
try:
    d = json.load(open(path, encoding="utf-8"))
except Exception as e:
    print("FAIL: %s" % e); sys.exit(1)
if not isinstance(d, dict) or not d.get("quality_ok"):
    print("[%s] FAIL (quality)" % a.seg); sys.exit(1)
print("[%s] OK" % a.seg); sys.exit(0)
'''

STUB_REVIEW_READY = '''#!/usr/bin/env python3
import argparse, json, os, sys
p = argparse.ArgumentParser()
p.add_argument("seg")
p.add_argument("--expect-token", dest="tok", default=None)
p.add_argument("--candidate-file", dest="cf", default=None)
p.add_argument("--durable-root", dest="dr", default=None)
a = p.parse_args()
root = os.path.abspath(a.dr) if a.dr else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
path = a.cf if a.cf else os.path.join(root, "segments", a.seg + ".review.json")
try:
    d = json.load(open(path, encoding="utf-8"))
except Exception as e:
    print(json.dumps({"ready": False, "reason": str(e)})); sys.exit(1)
ok = isinstance(d, dict) and d.get("schema_ok") and (a.tok is None or d.get("dispatch_token") == a.tok)
print(json.dumps({"ready": bool(ok)})); sys.exit(0 if ok else 1)
'''

# A real node .mjs fake companion -- NOT the sibling file's Python FAKE_NODE, which
# cannot execute real JS and so can never let a dispatch reach gate 4 for real. Reads
# controlling state from CJ_STATE (same env-var convention as the sibling file).
FAKE_COMPANION_MJS = r'''
import { readFileSync, writeFileSync, existsSync } from "node:fs";

const state = JSON.parse(readFileSync(process.env.CJ_STATE, "utf-8"));
// node's own process.argv is [nodeExe, scriptPath, ...userArgs] -- slice(2) already
// strips BOTH, unlike the sibling file's Python FAKE_NODE (which simulates the node
// executable itself, so its argv still carries the companion path as an extra leading
// element). argv[0] here IS the subcommand ("task"/"status"/"cancel").
const argv = process.argv.slice(2);
const sub = argv[0] || "";
const rest = argv.slice(1);

function opt(name) {
  const i = rest.indexOf(name);
  return i >= 0 && i + 1 < rest.length ? rest[i + 1] : null;
}
const cwd = opt("--cwd");
function jobCwdMarker(jid) { return process.env.CJ_STATE + ".jobcwd." + jid; }

if (sub === "task") {
  const promptFile = opt("--prompt-file");
  let text = "";
  if (promptFile && existsSync(promptFile)) text = readFileSync(promptFile, "utf-8");
  const draftMatch = text.match(/(\S+\/attempt\.draft\.json)/);
  const reviewMatch = text.match(/(\S+\/attempt\.review\.json)/);
  if (draftMatch && state.draft_payload !== undefined) {
    writeFileSync(draftMatch[1], JSON.stringify(state.draft_payload));
  }
  if (reviewMatch && state.review_payload !== undefined) {
    writeFileSync(reviewMatch[1], JSON.stringify(state.review_payload));
  }
  const jid = state.jobId || "job-1";
  writeFileSync(jobCwdMarker(jid), cwd || "");
  process.stdout.write(JSON.stringify({ jobId: jid, status: "queued" }));
  process.exit(0);
}

if (sub === "status") {
  const jid = rest.find((t) => !t.startsWith("--"));
  let launchedCwd = null;
  try { launchedCwd = readFileSync(jobCwdMarker(jid), "utf-8"); } catch (e) {}
  if (launchedCwd !== null && launchedCwd !== cwd) {
    process.stderr.write("No job found for \"" + jid + "\".\n");
    process.exit(1);
  }
  process.stdout.write(JSON.stringify({ job: { status: "completed", workspaceRoot: cwd } }));
  process.exit(0);
}

if (sub === "cancel") {
  process.stdout.write(JSON.stringify({}));
  process.exit(0);
}
process.exit(0);
'''


def _build_e2e_root(tmp_path):
    root = tmp_path / "durable"
    (root / "segments").mkdir(parents=True)
    scripts = root / "scripts"
    scripts.mkdir()
    (scripts / "codex_job.py").write_text(DRIVER_SRC.read_text(encoding="utf-8"), encoding="utf-8")
    (scripts / "draft_ready.py").write_text(STUB_DRAFT_READY, encoding="utf-8")
    (scripts / "validate_draft.py").write_text(STUB_VALIDATE_DRAFT, encoding="utf-8")
    (scripts / "review_ready.py").write_text(STUB_REVIEW_READY, encoding="utf-8")
    # No --plugin-root in these e2e tests -> _trusted_template_path() self-anchors
    # to SCRIPTS_DIR/<name>, FLAT beside the .py gates, because that is where
    # scaffold_setup.py:217 places every bundle member and the template is one.
    # The REAL template (never a stub -- see this file's own module docstring)
    # must sit there for gate 4 to be reachable at all.
    #
    # This fixture used to build root/templates/ instead, a directory Step 0a
    # never creates. That did not merely diverge from production, it CONCEALED a
    # blocker: gate 4 could not pass in any real deployment, and these tests
    # passed anyway because they hand-built the layout the bug expected. A
    # fixture describing a state the scaffolder cannot produce is not evidence
    # about a state it can.
    (scripts / "mass-translate-wf.template.js").write_text(
        TEMPLATE_SRC.read_text(encoding="utf-8"), encoding="utf-8")
    companion = root / "fake-companion.mjs"
    companion.write_text(FAKE_COMPANION_MJS, encoding="utf-8")
    return root, str(companion)


def _spawn_fixreview(root, companion, seg, draft_tok, review_tok, disp, state, deadline=8,
                     poll=1, node=None):
    seg_dir = root / "segments"
    state_file = root / ("state.%s.json" % disp)
    state_file.write_text(json.dumps(state), encoding="utf-8")
    prompt = seg_dir / (".codex_task.fixreview.%s.%s" % (seg, disp))
    prompt.write_text(PROMPT_FIXREVIEW, encoding="utf-8")
    argv = [
        sys.executable, str(root / "scripts" / "codex_job.py"),
        "--kind", "fixreview", "--companion", companion, "--cwd", str(root), "--seg", seg,
        "--prompt-file", str(prompt), "--expect-token", draft_tok,
        "--expect-review-token", review_tok, "--disp", disp,
        "--deadline-sec", str(deadline), "--poll-sec", str(poll),
        "--node", node or _NODE or "node",
    ]
    env = dict(os.environ, CJ_STATE=str(state_file))
    return subprocess.run(argv, capture_output=True, text=True, timeout=120, cwd=str(root), env=env)


@skip_no_node
def test_e2e_fixreview_stages_valid_pair(tmp_path):
    root, companion = _build_e2e_root(tmp_path)
    seg, draft_tok, review_tok = "c001", "RUN:c001", "RUN:c001:r1"
    state = {
        "draft_payload": {"dispatch_token": draft_tok, "structure_ok": True, "quality_ok": True},
        "review_payload": {"dispatch_token": review_tok, "schema_ok": True, "clean": True,
                           "coverage_ok": True, "findings": [], "draft_sha1": "deadbeef"},
    }
    proc = _spawn_fixreview(root, companion, seg, draft_tok, review_tok, "D1", state)
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    line = json.loads(proc.stdout.strip().splitlines()[-1])
    assert line["ok"] is True
    assert line["staged"] is True
    assert line["reason"] == "staged"
    assert Path(line["staged_draft_path"]).is_file()
    assert Path(line["staged_review_path"]).is_file()
    assert line["staged_draft_sha256"] == hashlib.sha256(
        Path(line["staged_draft_path"]).read_bytes()).hexdigest()
    # canonical files must never exist -- this driver never promotes for this kind.
    assert not (root / "segments" / f"{seg}.draft.json").exists()
    assert not (root / "segments" / f"{seg}.review.json").exists()


@skip_no_node
def test_e2e_fixreview_bad_draft_token_quarantines(tmp_path):
    root, companion = _build_e2e_root(tmp_path)
    seg, draft_tok, review_tok = "c001", "RUN:c001", "RUN:c001:r1"
    state = {
        "draft_payload": {"dispatch_token": "WRONG_TOKEN", "structure_ok": True, "quality_ok": True},
        "review_payload": {"dispatch_token": review_tok, "schema_ok": True, "clean": True,
                           "coverage_ok": True, "findings": [], "draft_sha1": "deadbeef"},
    }
    proc = _spawn_fixreview(root, companion, seg, draft_tok, review_tok, "D2", state)
    assert proc.returncode == 1, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    line = json.loads(proc.stdout.strip().splitlines()[-1])
    assert line["ok"] is False
    assert line["staged"] is False
    assert line["reason"] == "validate-failed"
    quarantined = list((root / "segments").glob(".codex_quarantine.%s.*.draft.json" % seg))
    assert len(quarantined) == 1, "the bad-token draft must be QUARANTINED, not silently dropped"


@skip_no_node
def test_e2e_fixreview_fabricated_loc_review_quarantines_despite_valid_draft(tmp_path):
    """The genuine end-to-end proof this whole 4th gate exists for: gates 1-3 all PASS
    (valid draft, valid review schema/tokens per the stubs) but the review's own
    findings carry a fabricated (colonless, infra-sentinel-shaped) loc -- the REAL
    matchedVerdict(), executed against the shipped template through a real subprocess
    dispatch, must still refuse to stage."""
    root, companion = _build_e2e_root(tmp_path)
    seg, draft_tok, review_tok = "c001", "RUN:c001", "RUN:c001:r1"
    state = {
        "draft_payload": {"dispatch_token": draft_tok, "structure_ok": True, "quality_ok": True},
        "review_payload": {"dispatch_token": review_tok, "schema_ok": True, "clean": False,
                           "coverage_ok": True, "draft_sha1": "deadbeef", "findings": [
                               {"loc": "TASK", "severity": "high", "issue": "x", "suggest": "y"},
                           ]},
    }
    proc = _spawn_fixreview(root, companion, seg, draft_tok, review_tok, "D3", state)
    assert proc.returncode == 1, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    line = json.loads(proc.stdout.strip().splitlines()[-1])
    assert line["ok"] is False
    assert line["staged"] is False
    assert line["reason"] == "validate-failed"
    assert "matchedVerdict" in (line.get("error_detail") or "")
    quarantined_draft = list((root / "segments").glob(".codex_quarantine.%s.*.draft.json" % seg))
    quarantined_review = list((root / "segments").glob(".codex_quarantine.%s.*.review.json" % seg))
    assert len(quarantined_draft) == 1, "the draft (already edited by the fixreview turn) must be preserved"
    assert len(quarantined_review) == 1


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))


def test_the_node_gated_set_is_exactly_this():
    """Always runs, with or without node, so the coverage this file loses on a
    node-less machine is stated rather than merely skipped.

    Derived from the decorated functions themselves -- reading the markers off
    the module -- not from a hand-kept list compared against another hand-kept
    list, which would agree with itself while both drifted from the code."""
    import inspect
    import sys as _sys

    module = _sys.modules[__name__]
    marked = set()
    for name, obj in inspect.getmembers(module, inspect.isfunction):
        if not name.startswith("test_"):
            continue
        for mark in getattr(obj, "pytestmark", []):
            if mark.name == "skipif" and "node not on PATH" in str(mark.kwargs.get("reason", "")):
                marked.add(name)

    assert marked == set(NODE_GATED_TESTS), (
        "the node-gated set changed. Added tests: %s. Removed: %s. If a new gate-4 "
        "test is stubbed, say so here and check that a real-template counterpart "
        "exists -- a skip is invisible in a green run."
        % (sorted(marked - set(NODE_GATED_TESTS)), sorted(set(NODE_GATED_TESTS) - marked))
    )
