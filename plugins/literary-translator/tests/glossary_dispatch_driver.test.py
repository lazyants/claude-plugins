#!/usr/bin/env python3
"""The glossary driver's harness, its provenance rule, and its cap.

The harness EXECUTES the shipped template, so the things that can go wrong are not
ordinary logic bugs:

  * it can execute the WRONG COPY -- ${durable_root}/ is writable by the very codex
    jobs this driver dispatches, so a durable copy of the template is
    model-writable JavaScript this process would run;
  * it can MIS-WRAP -- the template's batch-cap preflight ends in a top-level
    `return` ABOVE every prompt builder, so a naive truncate-and-export leaves an
    `Illegal return statement`, and a wrapper that merely "works" today can start
    skipping a startup guard tomorrow;
  * it can enforce the WRONG BOUND -- the template's own preflight counts a
    Workflow's agent calls, which is not what this driver spends.

Each of those is tested here by outcome. Node is required: these builders are
JavaScript, and a green run that executed none of them is a false pass.
"""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = PLUGIN_ROOT / "skills" / "literary-translator"
DRIVER = SKILL_ROOT / "assets" / "scripts" / "glossary_dispatch_driver.py"
JSON_STDOUT = SKILL_ROOT / "assets" / "scripts" / "json_stdout.py"
TEMPLATE = SKILL_ROOT / "assets" / "templates" / "glossary-pass-wf.template.js"

NODE = shutil.which("node")
BATCH = {"index": 0, "candidates": [{"name": "Alpha", "freq": 2}]}


@pytest.fixture
def mod(tmp_path):
    scripts = tmp_path / "durable" / "scripts"
    scripts.mkdir(parents=True)
    target = scripts / "glossary_dispatch_driver.py"
    shutil.copy2(DRIVER, target)
    # json_stdout.py is the driver's one hard sibling dependency: it is loaded
    # by exact path at import time and the driver exits without it, exactly as a
    # deployed copy does. Staging it keeps this fixture a real scripts/ dir.
    shutil.copy2(JSON_STDOUT, target.parent / "json_stdout.py")
    spec = importlib.util.spec_from_file_location(
        f"gdd_h{abs(hash(str(target)))}", target)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def subst(**over):
    base = dict(durable_root="/durable", source_lang="he", target_lang="en",
                research_mode="live", run_id="runX", effort="high",
                citation_content_types="text/html", batch_agent_cap=10 ** 9,
                plugin_root="/plugin", resumed_batch_indices=[])
    base.update(over)
    return base


# ---------------------------------------------------------------------------
# Provenance: which bytes may be executed
# ---------------------------------------------------------------------------

def test_a_missing_plugin_root_is_refused_rather_than_defaulted(mod):
    """No durable fallback exists, deliberately: a fallback would turn a forgotten
    flag into silent execution of the model-writable copy."""
    with pytest.raises(SystemExit) as exc:
        mod.resolve_template(None)
    assert exc.value.code == 2


def test_a_symlinked_template_leaf_is_refused(mod, tmp_path):
    """The no-follow walk's whole job. A symlink at the leaf is how a writable copy
    gets executed while the path still looks like the plugin's own."""
    fake_plugin = tmp_path / "plugin"
    (fake_plugin / "assets" / "templates").mkdir(parents=True)
    elsewhere = tmp_path / "attacker.js"
    elsewhere.write_text("// not the shipped template\n", encoding="utf-8")
    (fake_plugin / "assets" / "templates" / TEMPLATE.name).symlink_to(elsewhere)
    with pytest.raises(SystemExit) as exc:
        mod.resolve_template(str(fake_plugin))
    assert exc.value.code == 2


def test_a_symlinked_ANCESTOR_directory_is_refused(mod, tmp_path):
    """lstat on the leaf cannot see this: every component before it is resolved by
    the kernel, so a genuine regular file at the far end of a symlinked parent
    passes a leaf-only check while the bytes come from somewhere else."""
    fake_plugin = tmp_path / "plugin"
    (fake_plugin / "assets").mkdir(parents=True)
    real_dir = tmp_path / "elsewhere"
    real_dir.mkdir()
    shutil.copy2(TEMPLATE, real_dir / TEMPLATE.name)
    (fake_plugin / "assets" / "templates").symlink_to(real_dir)
    with pytest.raises(SystemExit) as exc:
        mod.resolve_template(str(fake_plugin))
    assert exc.value.code == 2


def test_the_real_plugin_tree_resolves(mod):
    assert mod.resolve_template(str(SKILL_ROOT)).name == TEMPLATE.name


# ---------------------------------------------------------------------------
# The wrapper
# ---------------------------------------------------------------------------

def test_substitution_refuses_an_unknown_surviving_token(mod):
    """A template that grows a token must fail loudly. Substituting the nine it
    knows would ship a prompt containing a literal {{TOKEN}}."""
    text = TEMPLATE.read_text(encoding="utf-8") + "\nconst NEW = {{BRAND_NEW}}\n"
    with pytest.raises(SystemExit) as exc:
        mod.render_template_source(text, subst())
    assert exc.value.code == 2


def test_a_moved_export_literal_is_refused(mod):
    with pytest.raises(SystemExit) as exc:
        mod.template_harness_source(
            TEMPLATE.read_text(encoding="utf-8")
            .replace(mod._EXPORT_META_LITERAL, "const renamed = {"), subst())
    assert exc.value.code == 2


def test_a_moved_truncation_marker_is_refused(mod):
    """Mis-truncating silently is the failure this refusal exists to prevent: the
    wrapper would still parse, and would simply stop exporting some builders."""
    with pytest.raises(SystemExit) as exc:
        mod.template_harness_source(
            TEMPLATE.read_text(encoding="utf-8")
            .replace(mod._TRUNCATE_BEFORE_MARKER, "const renamedResults = await x("),
            subst())
    assert exc.value.code == 2


# Real arguments per builder. Deliberately exhaustive over
# TEMPLATE_EXPORTED_FUNCTIONS rather than a sample: a builder the driver declares
# but never exercises here is a builder whose absence would surface at runtime.
_ROW = {"source_form": "Alpha", "basis": "established", "disposition": "accepted",
        "source": "https://dead.test/a"}
_BUILDER_ARGS = {
    "fragmentPath": [0, 0], "manifestPath": [0], "checkBatchCmd": [0, 0],
    "sandboxCheckBatchCmd": ["/private/tmp/ltgd.x/out_0_attempt_0.json", 0],
    "approvedPath": [0, 0], "approveBatchCmd": [0, 0],
    "approvalRecordPath": [0, 0], "recordApprovalCmd": [0, 0],
    "evidenceDir": [0, 0], "evidenceIndexPath": [0, 0],
    "fetchCitationsCmd": [0, 0], "repairFragmentPath": [0, 0],
    "batchDispatchPrompt": [BATCH, 0, None],
    "batchRepairPrompt": [BATCH, 0, [_ROW]],
    "citationJudgePrompt": [BATCH, 0],
    "mergeBatchesCmd": [["/f0.json"], ["/a0.json"]],
    "verifyMergedCmd": [["/f0.json"]],
    "rejectionDetail": ["reply", "OK 0", "FAIL 0"],
    "sentinelVerdict": ["OK 0", "OK 0", "FAIL 0"],
    "rejectedAnywhere": ["reply", "FAIL 0"],
}


@pytest.mark.skipif(NODE is None, reason="node required")
def test_the_wrapper_runs_the_real_template_and_returns_every_builder(mod):
    """Every declared builder is present AND actually invocable with the arguments
    the driver passes it -- not merely `typeof === 'function'`, which a stub would
    also satisfy."""
    missing = set(mod.TEMPLATE_EXPORTED_FUNCTIONS) - set(_BUILDER_ARGS)
    assert not missing, f"this test's argument table is missing {sorted(missing)}"
    calls = [{"key": n, "fn": n, "args": _BUILDER_ARGS[n]}
             for n in mod.TEMPLATE_EXPORTED_FUNCTIONS]
    out = mod.call_template_functions(TEMPLATE, subst(), [BATCH], calls, NODE)
    assert set(out) == set(mod.TEMPLATE_EXPORTED_FUNCTIONS)


@pytest.mark.skipif(NODE is None, reason="node required")
def test_the_templates_own_startup_guards_still_run_for_the_driver(mod):
    """The wrapper is called with the REAL batches array precisely so the
    template's guards are not bypassed. An empty PLUGIN_ROOT is one it throws on;
    if this stopped failing, the wrapper would be skipping the guards."""
    with pytest.raises(SystemExit) as exc:
        mod.call_template_functions(
            TEMPLATE, subst(plugin_root=""), [BATCH],
            [{"key": "f", "fn": "fragmentPath", "args": [0, 0]}], NODE)
    assert exc.value.code == 2


@pytest.mark.skipif(NODE is None, reason="node required")
def test_a_duplicate_batch_index_is_refused_by_the_templates_own_check(mod):
    dupes = [{"index": 0, "candidates": []}, {"index": 0, "candidates": []}]
    with pytest.raises(SystemExit) as exc:
        mod.call_template_functions(
            TEMPLATE, subst(), dupes,
            [{"key": "f", "fn": "fragmentPath", "args": [0, 0]}], NODE)
    assert exc.value.code == 2


@pytest.mark.skipif(NODE is None, reason="node required")
def test_the_batch_cap_preflight_is_detected_structurally(mod):
    """With a small cap the template returns its preflight object INSTEAD of the
    builder set. The driver must notice that the builders are missing -- never
    match on the reason string, which would make it a reader of the template's
    failure vocabulary."""
    with pytest.raises(SystemExit) as exc:
        mod.call_template_functions(
            TEMPLATE, subst(batch_agent_cap=1), [BATCH],
            [{"key": "f", "fn": "fragmentPath", "args": [0, 0]}], NODE)
    assert exc.value.code == 1


# ---------------------------------------------------------------------------
# The cap the driver actually enforces
# ---------------------------------------------------------------------------

def test_the_local_bound_is_judges_not_the_workflow_estimate(mod):
    """7 batches x 3 attempts = 21 judges. The template's Workflow estimate for the
    same run is 16*7+2 = 114, so enforcing that would refuse a run this driver can
    comfortably afford."""
    assert mod.enforce_local_cap(7, 2, 100, "live") == 21


def test_the_local_bound_still_refuses_when_genuinely_exceeded(mod):
    with pytest.raises(SystemExit) as exc:
        mod.enforce_local_cap(50, 2, 100, "live")
    assert exc.value.code == 1


def test_an_offline_run_is_charged_for_no_judges_at_all(mod):
    """Outside `live` the batch reaches `ready` without a judge ever being
    rendered, so charging the live worst case refuses a run whose reachable path
    issues zero agent calls -- in the one mode chosen to need no network."""
    # "cached" is deliberately NOT a research mode the schema or the argparse
    # choices admit: the bound must be "not live", not "== offline", so that a
    # third mode added later cannot silently inherit the live judge charge.
    for mode in ("offline", "cached"):
        assert mod.enforce_local_cap(34, 2, 100, mode) == 0


def test_an_offline_run_still_has_a_codex_job_ceiling(mod):
    """Zero JUDGES is not zero WORK. Offline still dispatches one codex job per
    batch, and the template's preflight -- which the driver deliberately loads
    past -- was the only thing bounding that before. Bounding judges alone left
    an offline run able to enqueue any number of jobs at all."""
    assert mod.worst_case_codex_jobs(34, 2, "offline") == 34
    assert mod.enforce_local_cap(34, 2, 100, "offline") == 0
    with pytest.raises(SystemExit) as exc:
        mod.enforce_local_cap(5000, 2, 100, "offline")
    assert exc.value.code == 1


def test_the_codex_job_ceiling_counts_the_repair_launch_too(mod):
    """A live rung can launch TWO jobs, not one: the whole-batch dispatch and,
    when a citation does not retrieve, the per-row repair into the reserved next
    rung. A ceiling that counted only dispatches would admit twice the work it
    thought it was admitting."""
    assert mod.worst_case_codex_jobs(7, 2, "live") == 42
    with pytest.raises(SystemExit) as exc:
        mod.enforce_local_cap(7, 2, 41, "live")
    assert exc.value.code == 1
    assert mod.enforce_local_cap(7, 2, 42, "live") == 21


# ---------------------------------------------------------------------------
# Shared-command execution
# ---------------------------------------------------------------------------

def test_template_commands_run_without_a_shell(mod, tmp_path):
    """The template's strings are POSIX-quoted for bash because an agent's only
    executor is bash. The driver has a real argv, so shlex parses the quoting and
    nothing reaches a shell -- a metacharacter in a spliced value must be inert."""
    marker = tmp_path / "shell-ran"
    code, out, _err = mod.run_template_cmd(
        f"{sys.executable} -c 'import sys;print(sys.argv[1])' "
        f"';touch {marker};'", timeout=60)
    assert code == 0
    assert out.strip() == f";touch {marker};"
    assert not marker.exists(), "a shell interpreted the argument"


def test_run_id_is_allowlisted_not_denylisted(mod):
    assert mod.validate_run_id("run_2026-08-31.v2") is None
    for bad in ("", "../escape", "run;rm -rf /", "run id", "run\nid", "/abs"):
        assert mod.validate_run_id(bad) is not None, f"{bad!r} must be refused"


# ---------------------------------------------------------------------------
# The failure REASON of a template command (#851)
#
# Every script this driver runs reports its verdict on STDOUT as one JSON line and
# never on stderr, which carries only what is not a verdict (an import guard,
# argparse misuse, a traceback). The driver logged `err` alone, so a real refusal
# -- canon_validate.py exiting 1 because --approve-to would overwrite a differing
# snapshot -- reached the operator as a bare colon with nothing after it. These
# are outcome tests: they read the line the operator actually sees.
# ---------------------------------------------------------------------------

STDOUT_REFUSAL = (
    '{"success": false, "error": "SENTINEL-REASON: --approve-to refuses to '
    'overwrite the approved snapshot already at /x: its bytes differ from the '
    'fragment just validated"}'
)


class _StubCtx:
    """Only what prepare_and_hand_back reads before it reaches a failure branch."""

    def __init__(self, tmp_path):
        self._paths = tmp_path

    def build(self, calls):
        out = {}
        for call in calls:
            key = call["key"]
            if key in ("approve", "fetch"):
                out[key] = f"{key}-command"
            else:
                out[key] = str(self._paths / key)
        return out


def _drive_approve(mod, tmp_path, monkeypatch, results):
    """Runs prepare_and_hand_back with run_template_cmd replaced by `results`,
    a list of (code, out, err) consumed in call order."""
    calls = iter(results)
    monkeypatch.setattr(mod, "run_template_cmd",
                        lambda cmd, *, timeout: next(calls))
    return mod.prepare_and_hand_back(
        _StubCtx(tmp_path), dict(BATCH), 0, tmp_path / "fragment.json")


def test_a_snapshot_failure_logs_the_reason_the_script_actually_printed(
        mod, tmp_path, monkeypatch, capsys):
    """THE #851 REGRESSION. canon_validate.py exits 1 with its reason on stdout and
    stderr empty; before the fix this logged an empty string after the colon."""
    result = _drive_approve(mod, tmp_path, monkeypatch,
                            [(1, STDOUT_REFUSAL, "")])
    assert result["reason"] == "approve-failed"
    logged = capsys.readouterr().err
    assert "could not snapshot attempt 0" in logged
    assert "SENTINEL-REASON" in logged, (
        "the reason reached the operator as an empty line: " + repr(logged))


def test_a_citation_fetch_failure_logs_the_reason_too(
        mod, tmp_path, monkeypatch, capsys):
    """fetch_citation.py has the same shape -- zero stderr writes, verdict on
    stdout -- so the fetch branch had the identical defect."""
    result = _drive_approve(mod, tmp_path, monkeypatch,
                            [(0, "", ""), (1, STDOUT_REFUSAL, "")])
    assert result["reason"] == "fetch-failed"
    logged = capsys.readouterr().err
    assert "citation fetch failed for attempt 0" in logged
    assert "SENTINEL-REASON" in logged


def test_stderr_still_wins_when_the_command_actually_wrote_some(
        mod, tmp_path, monkeypatch, capsys):
    """NO REGRESSION on the path that already worked. run_template_cmd synthesises
    stderr for a timeout and an OSError, and argparse misuse writes it; stdout is
    a FALLBACK, never a replacement."""
    _drive_approve(mod, tmp_path, monkeypatch,
                   [(124, "ignored-stdout", "timed out after 600s")])
    logged = capsys.readouterr().err
    assert "timed out after 600s" in logged
    assert "ignored-stdout" not in logged


def test_a_long_stdout_verdict_is_truncated_at_the_end_that_keeps_the_reason(
        mod, tmp_path, monkeypatch, capsys):
    """A tail-slice of stdout drops exactly the field worth reading. The JSON line
    puts "error" FIRST and a redundant "offending" array last, and a real
    multi-row schema failure runs to thousands of bytes -- so `(err or out)[-400:]`
    would log the tail of `offending` and no reason at all."""
    payload = ('{"success": false, "error": "SENTINEL-REASON", "offending": ['
               + ", ".join('"filler row %d"' % i for i in range(200)) + ']}')
    assert len(payload) > 2000, "the fixture must exceed the truncation window"
    _drive_approve(mod, tmp_path, monkeypatch, [(1, payload, "")])
    logged = capsys.readouterr().err
    assert "SENTINEL-REASON" in logged
    assert "filler row 199" not in logged, "the line was sliced from the wrong end"


def test_the_approval_record_failure_persists_the_reason_into_the_state_record(
        mod, tmp_path, monkeypatch):
    """The THIRD site, and the only one whose reason is PERSISTED rather than
    logged: when the review approved but the bookkeeping write failed, `detail`
    got the same empty string, so the state record an operator reads afterwards
    said the batch failed and would not say why.

    Driven through record_verdicts' real control flow -- the nonce, the re-hashed
    snapshot digest and the sentinel read all have to pass before the approval
    record is even attempted, so an assertion here cannot be reached by accident."""
    vdir = tmp_path / "verdicts"
    vdir.mkdir()
    snapshot = tmp_path / "approved_0_attempt_0.json"
    snapshot.write_text("[]")

    class _Ctx:
        verdict_dir = vdir

        def build(self, calls):
            out = {}
            for call in calls:
                fn = call["fn"]
                if fn == "approvedPath":
                    out[call["key"]] = str(snapshot)
                elif fn == "rejectedAnywhere":
                    out[call["key"]] = False       # no containment guard tripped
                elif fn == "sentinelVerdict":
                    out[call["key"]] = True        # the judge APPROVED
                elif fn == "rejectionDetail":
                    out[call["key"]] = ""
                elif fn == "recordApprovalCmd":
                    out[call["key"]] = "record-approval-command"
                else:
                    out[call["key"]] = str(tmp_path / call["key"])
            return out

    state = {"batches": {"0": {
        "status": "awaiting_judge", "attempt": 0,
        "pending": {"nonce": "NONCE", "snapshot_sha256": mod._sha256_file(snapshot),
                    "ok_sentinel": "CITATIONS_OK 0 ATTEMPT 0",
                    "fail_sentinel": "CITATIONS_REJECTED 0 ATTEMPT 0"}}}}
    verdicts = vdir / "verdicts.json"
    verdicts.write_text(json.dumps(
        [{"batch": 0, "attempt": 0, "nonce": "NONCE",
          "reply": "CITATIONS_OK 0 ATTEMPT 0"}]))

    monkeypatch.setattr(mod, "run_template_cmd",
                        lambda cmd, *, timeout: (1, STDOUT_REFUSAL, ""))
    result = mod.record_verdicts(_Ctx(), verdicts, state)

    st = state["batches"]["0"]
    assert st["status"] == "failed"
    assert st["reason"] == "approval-record-write-failed"
    assert "SENTINEL-REASON" in st["detail"], (
        "the persisted reason was empty: " + repr(st.get("detail")))
    assert result["recorded"][0]["approvalRecorded"] is False


def test_canon_validate_really_does_write_its_refusal_to_stdout_only(tmp_path):
    """CHARACTERISATION, not a regression test -- it passes before and after the
    fix. It pins the premise the stdout fallback exists for -- a refusal, ANY
    refusal, is reported on stdout: this invocation exits 1 on per-item schema
    validation, before `--approve-to` is ever consulted, and the reason still
    arrives on stdout with stderr empty. If canon_validate.py ever moved its
    verdict to stderr, the fallback would quietly become dead code and this test
    would say so rather than leaving it to be re-derived by hand."""
    canon = tmp_path / "canon.json"
    fragment = tmp_path / "fragment.json"
    fragment.write_text(json.dumps(
        [{"source_form": "name0", "canonical_target_form": "N0",
          "basis": "established"}]))
    proc = subprocess.run(
        [sys.executable, str(SKILL_ROOT / "assets" / "scripts" / "canon_validate.py"),
         "--canon", str(canon), "--research-mode", "live",
         "--check-batch", str(fragment),
         "--approve-to", str(tmp_path / "approved.json")],
        capture_output=True, text=True, timeout=120)
    assert proc.returncode == 1, proc.stderr
    assert proc.stderr == "", "canon_validate.py now writes stderr; see #851"
    assert '"error"' in proc.stdout


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
