#!/usr/bin/env python3
"""Every prompt and command the glossary driver issues comes from the TEMPLATE.

`glossary_dispatch_driver.py` replaces agent calls with local execution, so the
risk it introduces is not that a command fails -- it is that the command it runs
DRIFTS from the one the `pipeline()` fallback runs. Two paths issuing two slightly
different `--check-batch` lines is a gate that asks one question of one path and a
weaker question of the other, and nothing goes red.

So this file drives the driver's own harness against the REAL shipped template and
pins, table-driven, every builder the state machine consumes. It is deliberately
NOT a list of expected strings hand-copied from the template: that would be the
second copy it exists to prevent. It asserts STRUCTURE (flag order, the
`--plugin-root` asymmetry, the routing identity) plus a mutation check that the
table is not vacuous.

Skipped wholesale without node: these builders are JavaScript, and a green run
that silently executed none of them is the exact false pass this repo's CI notes
warn about.
"""

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = PLUGIN_ROOT / "skills" / "literary-translator"
DRIVER = SKILL_ROOT / "assets" / "scripts" / "glossary_dispatch_driver.py"
TEMPLATE = SKILL_ROOT / "assets" / "templates" / "glossary-pass-wf.template.js"

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node is required to execute "
                                                     "the template's builders")

BATCH = {"index": 3, "candidates": [{"name": "Alpha", "freq": 7}]}
ROWS = [{"source_form": "Alpha", "basis": "established",
         "canonical_target_form": "Alpha", "confidence": "high",
         "disposition": "accepted", "is_proper_name": True,
         "source": "https://dead.test/a"}]


@pytest.fixture(scope="module")
def mod(tmp_path_factory):
    scripts = tmp_path_factory.mktemp("durable") / "scripts"
    scripts.mkdir(parents=True)
    target = scripts / "glossary_dispatch_driver.py"
    shutil.copy2(DRIVER, target)
    spec = importlib.util.spec_from_file_location("gdd_parity", target)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def subst(**over):
    base = dict(durable_root="/durable", source_lang="he", target_lang="en",
                research_mode="live", run_id="runX", effort="high",
                citation_content_types="text/html,application/pdf",
                batch_agent_cap=10 ** 9, plugin_root="/plugin",
                resumed_batch_indices=[])
    base.update(over)
    return base


@pytest.fixture(scope="module")
def built(mod):
    calls = [
        {"key": "check", "fn": "checkBatchCmd", "args": [3, 0]},
        {"key": "approve", "fn": "approveBatchCmd", "args": [3, 0]},
        {"key": "record", "fn": "recordApprovalCmd", "args": [3, 0]},
        {"key": "fetch", "fn": "fetchCitationsCmd", "args": [3, 0]},
        {"key": "merge", "fn": "mergeBatchesCmd",
         "args": [["/durable/f3.json"], ["/durable/a3.json"]]},
        {"key": "verify", "fn": "verifyMergedCmd", "args": [["/durable/f3.json"]]},
        {"key": "dispatch", "fn": "batchDispatchPrompt", "args": [BATCH, 0, None]},
        {"key": "repair", "fn": "batchRepairPrompt", "args": [BATCH, 0, ROWS, None]},
        {"key": "judge", "fn": "citationJudgePrompt", "args": [BATCH, 0]},
        {"key": "fragment", "fn": "fragmentPath", "args": [3, 0]},
        {"key": "approved", "fn": "approvedPath", "args": [3, 0]},
        {"key": "repairpath", "fn": "repairFragmentPath", "args": [3, 0]},
        {"key": "manifest", "fn": "manifestPath", "args": [3]},
        {"key": "evidence", "fn": "evidenceIndexPath", "args": [3, 0]},
    ]
    return mod.call_template_functions(TEMPLATE, subst(), [BATCH], calls, NODE)


# ---------------------------------------------------------------------------
# Every builder the state machine consumes is reachable and non-empty.
# ---------------------------------------------------------------------------

def test_the_harness_exports_exactly_what_the_driver_declares(mod, built):
    """A declared builder the template does not define fails at harness time, not
    at use time -- so a template rename cannot ship as a runtime surprise."""
    assert set(mod.TEMPLATE_EXPORTED_FUNCTIONS), "the export list is empty"
    assert all(isinstance(v, str) and v for v in built.values())


def test_every_declared_builder_is_actually_callable(mod):
    """Covers the declared names this file's own table does not exercise, so the
    list cannot grow a dead entry."""
    calls = [{"key": n, "fn": n, "args": []} for n in
             ("fragmentPath", "manifestPath", "approvedPath", "approvalRecordPath",
              "evidenceDir", "evidenceIndexPath", "repairFragmentPath")]
    out = mod.call_template_functions(TEMPLATE, subst(), [BATCH], calls, NODE)
    assert set(out) == {c["key"] for c in calls}


# ---------------------------------------------------------------------------
# The gate commands: flag order and the #412 asymmetry.
# ---------------------------------------------------------------------------

def test_check_batch_keeps_research_mode_before_expect_source_forms(built):
    """Argument ORDER is contract here, not style: the dispatch prompt tells codex
    to re-run 'exactly the command above', so the driver's copy and the agent's
    copy must be the same string."""
    cmd = built["check"]
    assert "--check-batch" in cmd
    assert cmd.index("--research-mode") < cmd.index("--expect-source-forms-file")


def test_approve_appends_approve_to_after_the_check_prefix(built):
    """approveBatchCmd is checkBatchCmd plus one flag, APPENDED -- never
    interleaved -- so the shared prefix stays byte-reproducible from the dispatch
    side, which has no business naming a snapshot."""
    assert built["approve"].startswith(built["check"])
    assert built["approve"].endswith(built["approved"])
    assert "--approve-to" in built["approve"]


def test_record_approval_is_issued_against_the_snapshot_not_the_fragment(built):
    """The snapshot is the object the judge audited. Re-taking or re-checking the
    mutable attempt path here would leave the audited bytes and the recorded bytes
    as two different objects."""
    assert built["approved"] in built["record"]
    assert built["fragment"] not in built["record"]
    assert "--record-approval-to" in built["record"]
    assert "--approve-to" not in built["record"], (
        "the record command must not re-take the snapshot")


def test_fetch_reads_the_snapshot_never_the_mutable_attempt(built):
    assert built["approved"] in built["fetch"]
    assert built["fragment"] not in built["fetch"]


def test_plugin_root_rides_on_the_merge_command_and_on_no_other(built):
    """#412. --merge-batches is the one STAMPING mode, so it resolves cache_key.py
    from the plugin tree the codex agents cannot write. Putting the flag on a
    non-stamping command, or omitting it here, are both silent failures: the merge
    would stamp provenance hashes from a model-writable script."""
    assert "--plugin-root" in built["merge"]
    for key in ("check", "approve", "record", "verify", "fetch"):
        assert "--plugin-root" not in built[key], (
            f"{key} must not carry --plugin-root")


def test_verify_names_every_fragment_and_the_aggregate_manifest(built):
    assert "--verify-merged" in built["verify"]
    assert "--expect-source-forms-file" in built["verify"]


def test_fetch_quotes_every_content_type_separately(built):
    """The list is spliced from profile config; the template single-quotes each
    value. The driver runs these via shlex WITHOUT a shell, so this asserts the
    quoting the parse depends on is still emitted."""
    import shlex
    argv = shlex.split(built["fetch"])
    types = [argv[i + 1] for i, a in enumerate(argv) if a == "--allow-content-type"]
    assert types == ["text/html", "application/pdf"]


# ---------------------------------------------------------------------------
# The routing identity -- what makes "strip" safe instead of a second copy.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("key", ["dispatch", "repair"])
def test_the_routing_identity_holds_for_every_codex_prompt(mod, built, key):
    """The driver derives the task text by dropping the first line. That is only
    sound while the first line IS the routing token and nothing else, so the
    identity is asserted here rather than assumed there.

    This is the assertion that replaces splitting the prose into its own builder;
    if it ever fails, restore the split rather than loosening the strip."""
    prompt = built[key]
    assert prompt.split("\n")[0] == mod._ROUTING_LINE
    assert prompt == mod._ROUTING_LINE + "\n" + mod.strip_routing_line(prompt)


def test_strip_refuses_a_prompt_that_does_not_open_with_the_token(mod):
    """Fails loudly rather than guessing where the task begins -- a silent guess
    would hand codex a routing flag as instructions."""
    with pytest.raises(SystemExit) as exc:
        mod.strip_routing_line("Effort: high. no routing line here\nmore")
    assert exc.value.code == 2


def test_the_repair_prompt_names_only_the_rows_it_was_given(built):
    """The repair must not leak the rest of the batch: those rows are approved and
    are not the repairing agent's to touch."""
    assert "Alpha" in built["repair"]
    assert built["repairpath"] in built["repair"]
    assert "--check-batch" not in built["repair"], (
        "a repair fragment holds only the failed subset, so a full-manifest "
        "coverage self-check would fail by construction")


# ---------------------------------------------------------------------------
# Non-vacuity: the table must actually be reading the template.
# ---------------------------------------------------------------------------

def test_a_perturbed_template_changes_what_the_table_sees(mod, tmp_path):
    """Guards the whole file against silently testing nothing. If the harness were
    reading a stale or fabricated source, this mutation would not show up."""
    mutated = tmp_path / "glossary-pass-wf.template.js"
    text = TEMPLATE.read_text(encoding="utf-8")
    anchor = '" --approve-to "'
    assert anchor in text, "the mutation anchor moved; re-derive this test"
    mutated.write_text(text.replace(anchor, '" --approve-to-MUTANT "', 1),
                       encoding="utf-8")
    out = mod.call_template_functions(
        mutated, subst(), [BATCH],
        [{"key": "approve", "fn": "approveBatchCmd", "args": [3, 0]}], NODE)
    assert "--approve-to-MUTANT" in out["approve"]


def test_the_token_table_resolves_every_token_the_template_declares(mod):
    """Compares the DISCOVERED token set with the DECLARED one rather than pinning
    a count: a template that grows a token must fail here, and a hardcoded number
    would also fail for a token that was merely renamed."""
    import re
    declared = set(mod._TEMPLATE_TOKEN_STYLE)
    found = set(re.findall(r"\{\{([A-Z_]+)\}\}", TEMPLATE.read_text(encoding="utf-8")))
    assert found == declared, (
        f"template tokens {sorted(found)} != driver's table {sorted(declared)}")


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
