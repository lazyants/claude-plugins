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
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = PLUGIN_ROOT / "skills" / "literary-translator"
DRIVER = SKILL_ROOT / "assets" / "scripts" / "glossary_dispatch_driver.py"
TEMPLATE = SKILL_ROOT / "assets" / "templates" / "glossary-pass-wf.template.js"

NODE = shutil.which("node")
BATCH = {"index": 0, "candidates": [{"name": "Alpha", "freq": 2}]}


@pytest.fixture
def mod(tmp_path):
    scripts = tmp_path / "durable" / "scripts"
    scripts.mkdir(parents=True)
    target = scripts / "glossary_dispatch_driver.py"
    shutil.copy2(DRIVER, target)
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
    "approvedPath": [0, 0], "approveBatchCmd": [0, 0],
    "approvalRecordPath": [0, 0], "recordApprovalCmd": [0, 0],
    "evidenceDir": [0, 0], "evidenceIndexPath": [0, 0],
    "fetchCitationsCmd": [0, 0], "repairFragmentPath": [0, 0],
    "batchDispatchPrompt": [BATCH, 0, None],
    "batchRepairPrompt": [BATCH, 0, [_ROW], None],
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
    for mode in ("offline", "cached"):
        assert mod.enforce_local_cap(34, 2, 100, mode) == 0


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


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
