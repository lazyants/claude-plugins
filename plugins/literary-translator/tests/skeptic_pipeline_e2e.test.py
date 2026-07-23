"""tests/skeptic_pipeline_e2e.test.py -- executes the INSTANTIATED
``skeptic-pass-wf.template.js`` (RFC #215 Phase 2) with a fake ``agent()``,
mirroring ``tests/batch_size_estimator.test.py``'s own glossary-harness
pattern (``_wrap_for_execution``, the ``PLAN``-keyed mock, the
``pipeline()``/``log()`` shims). This is the REAL acceptance chain, no live
codex: the fake ``agent()``'s "dispatch" step writes a real, schema-shaped
triage fragment straight to disk (simulating what codex would write), and
the JS control flow's own "skeptic:merge"/"skeptic:verify" calls are
CANNED-mocked (never trusted, same discipline the glossary harness uses for
"glossary:merge"/"glossary:verify") -- so every real assertion in this file
comes from a SEPARATE, explicit call into the REAL Python
``skeptic_ready.run_merge_fragments``/``run_verify_merged`` (and, where
noted, ``run_validate_fragment``) on the actual fragment files the mock
left on disk, never from the mock's own disk-untouched return value.

Fixtures mirror ``tests/skeptic_ready.test.py``'s own helpers (duplicated
here, not imported -- this project's test files are each self-contained,
see ``pytest.ini``'s own comment on this convention).
"""
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
ASSETS_DIR = PLUGIN_ROOT / "skills" / "literary-translator" / "assets"
SCRIPTS_DIR = ASSETS_DIR / "scripts"
TEMPLATES_DIR = ASSETS_DIR / "templates"
SKEPTIC_PASS_TEMPLATE = TEMPLATES_DIR / "skeptic-pass-wf.template.js"
SKEPTIC_READY_SCRIPT = SCRIPTS_DIR / "skeptic_ready.py"
OCC_INDEX_SCRIPT = SCRIPTS_DIR / "occ_index.py"
BOOTSTRAP_NAMES_SCRIPT = SCRIPTS_DIR / "bootstrap_names.py"
# codex round 2: the "skeptic:frozen-check" real-subprocess harness branch
# needs skeptic_ready.py's own FULL import closure staged under
# ${durable_root}/scripts/ (never just skeptic_ready.py alone) -- every
# other test in this file never actually executes a real subprocess against
# ROOT/scripts/*, so this closure was never needed here before.
SKEPTIC_READY_DEPS = (
    "skeptic_ready.py", "skeptic_constants.py", "bootstrap_names.py",
    "evidence_verify.py", "canon_senses.py", "occ_index.py", "suspicion_scan.py",
)


def stage_skeptic_ready_scripts(durable_root: Path) -> None:
    scripts_dir = durable_root / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    for name in SKEPTIC_READY_DEPS:
        shutil.copy2(SCRIPTS_DIR / name, scripts_dir / name)

assert SKEPTIC_PASS_TEMPLATE.is_file(), f"skeptic-pass-wf.template.js not found at {SKEPTIC_PASS_TEMPLATE}"
assert SKEPTIC_READY_SCRIPT.is_file(), f"skeptic_ready.py not found at {SKEPTIC_READY_SCRIPT}"

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(
    NODE is None,
    reason="node not found on PATH; this test needs Node.js to actually execute "
    "the skeptic-pass workflow template's real control flow (no hard Node.js "
    "dependency for this plugin otherwise)",
)


def _load_module(name: str, path: Path, extra_sys_path: Path):
    sys.path.insert(0, str(extra_sys_path))
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None and spec.loader is not None, f"could not load spec for {path}"
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(extra_sys_path))


bn = _load_module("bootstrap_names_for_skeptic_e2e_test", BOOTSTRAP_NAMES_SCRIPT, SCRIPTS_DIR)
occ = _load_module("occ_index_for_skeptic_e2e_test", OCC_INDEX_SCRIPT, SCRIPTS_DIR)
sr = _load_module("skeptic_ready_for_skeptic_e2e_test", SKEPTIC_READY_SCRIPT, SCRIPTS_DIR)
# compute_frozen_input_hash() is deliberately NOT imported into
# skeptic_ready.py's production code any more (round 8) -- it stays
# test-only fixture-stamping sugar, so this suite imports it straight from
# suspicion_scan.py, where it is actually defined. `sr = _load_module(...)`
# above already triggered a real `import suspicion_scan` as a side effect
# of skeptic_ready.py's own top-level `from suspicion_scan import (...)`,
# so this is the SAME cached module object, not a second independent load
# (mirrors tests/skeptic_ready.test.py's own identical fix).
suspicion_scan = sys.modules["suspicion_scan"]


# ---------------------------------------------------------------------------
# Fixture helpers (mirror tests/skeptic_ready.test.py's own)
# ---------------------------------------------------------------------------

def write_particle_config(languages_dir: Path, filename: str = "test.json", *,
                           particles=(), stopwords=(), has_elision=False,
                           elision_re=None, name_inventory=None) -> str:
    languages_dir.mkdir(parents=True, exist_ok=True)
    doc = {
        "PARTICLES": list(particles),
        "STOPWORDS": list(stopwords),
        "has_elision": has_elision,
        "ELISION_RE": elision_re,
    }
    if name_inventory is not None:
        doc["name_inventory"] = list(name_inventory)
    (languages_dir / filename).write_text(json.dumps(doc), encoding="utf-8")
    return filename


def block(text, seg="seg01", block_id="PARA:seg01:0001"):
    return block_id, {"seg": seg, "plain_text": text}


def make_manifest(*blocks_kv) -> dict:
    return {"blocks": dict(blocks_kv)}


def evidence_for(source_form, block_id, seg, text, lang, index=0) -> dict:
    records = occ.build_occurrence_records(source_form, block_id, seg, text, lang)
    assert records, f"no production occurrence of {source_form!r} in block {block_id!r} under this lang config"
    rec = records[index]
    return {
        "block": rec["block"], "seg": rec["seg"],
        "char_start": rec["char_start"], "char_end": rec["char_end"],
        "context_start": rec["context_start"], "context_end": rec["context_end"],
        "sha256": rec["context_sha256"],
    }


def aid(source_form: str) -> str:
    return sr.compute_assignment_id(source_form)


def write_json(path: Path, doc) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")


def adverse_record(source_form, evidence, assignment_id=None, rationale="contradicts identity"):
    return {
        "assignment_id": assignment_id or aid(source_form),
        "source_form": source_form, "verdict": "adverse",
        "rationale": rationale, "evidence": evidence,
    }


def insufficient_record(source_form, assignment_id=None, rationale="not enough context"):
    return {
        "assignment_id": assignment_id or aid(source_form),
        "source_form": source_form, "verdict": "insufficient_window", "rationale": rationale,
    }


def window_for(evidence: dict) -> dict:
    return {
        "block": evidence["block"], "seg": evidence["seg"],
        "char_start": evidence["char_start"], "char_end": evidence["char_end"],
    }


def window_with_text(evidence: dict, text: str) -> dict:
    """The `args`-only window shape this template's dispatch prompt embeds
    -- window_for()'s shape plus the resolved WHOLE block `text` (see the
    template's own header comment's `args` shape note). Never written to
    any schema-validated file -- assignments.json's own windows[] stays
    additionalProperties:false with no `text` property."""
    w = window_for(evidence)
    w["text"] = text
    return w


def make_assignment_for_manifest(source_form, evidences, risk_classes=("high_dispersion",),
                                  batch_index=0, windows_truncated=False):
    """skeptic-assignment.schema.json's own assignments[] item shape --
    windows[] carries ONLY block/seg/char_start/char_end."""
    return {
        "assignment_id": aid(source_form), "source_form": source_form,
        "canonical_target_form": source_form, "risk_classes": list(risk_classes),
        "windows": [window_for(e) for e in evidences],
        "windows_truncated": windows_truncated, "batch_index": batch_index,
    }


def make_assignment_for_args(source_form, windows_with_text_list, risk_classes=("high_dispersion",),
                              windows_truncated=False):
    """The `args` shape this template's dispatch prompt reads -- same
    fields as an aggregate assignment (minus batch_index, which is implicit
    in which BATCHES[] entry it lives under), with each window carrying the
    extra `text` field."""
    return {
        "assignment_id": aid(source_form), "source_form": source_form,
        "canonical_target_form": source_form, "risk_classes": list(risk_classes),
        "windows_truncated": windows_truncated, "windows": windows_with_text_list,
    }


def make_aggregate_manifest(run_id, assignments, batch_count=1) -> dict:
    return {
        "schema_version": 1, "run_id": run_id,
        "input_digest": "0" * 64, "producer_input_digest": "1" * 64,
        "batch_count": batch_count, "assignments": assignments,
    }


# ---------------------------------------------------------------------------
# JS instantiation + fake-agent harness (mirrors batch_size_estimator
# .test.py's own GLOSSARY_HARNESS_TEMPLATE / _wrap_for_execution exactly,
# adapted for this template's labels and the `text`-carrying `args` shape).
# ---------------------------------------------------------------------------

def instantiate_skeptic_pass(*, durable_root: str, source_lang: str, particle_config: str,
                              run_id: str, batch_agent_cap: int) -> str:
    text = SKEPTIC_PASS_TEMPLATE.read_text(encoding="utf-8")
    text = text.replace("{{DURABLE_ROOT}}", durable_root)
    text = text.replace("{{SOURCE_LANG}}", source_lang)
    text = text.replace("{{PARTICLE_CONFIG}}", particle_config)
    text = text.replace("{{RUN_ID}}", run_id)
    text = text.replace("{{BATCH_AGENT_CAP}}", str(int(batch_agent_cap)))
    assert "{{" not in text, (
        "skeptic-pass fixture instantiation left an unresolved token -- fix the fixture, not the assertion"
    )
    return text


def _wrap_for_execution(js_source: str) -> str:
    """Identical to batch_size_estimator.test.py's own helper: the raw
    template is not valid standalone JS (it both `export`s `meta` and
    `return`s at its own top level)."""
    assert js_source.count("export const meta") == 1, (
        "expected exactly one 'export const meta' declaration to strip -- "
        "the template's export contract may have changed"
    )
    body = js_source.replace("export const meta", "const meta", 1)
    return "async function __workflowMain__(agent, pipeline, log, args) {\n" + body + "\n}\n"


# The mock `agent()`'s "dispatch" branch WRITES a real fragment file to
# disk (via `p.dispatchWrite`), simulating what codex would actually
# produce -- this is what makes the subsequent, separate real-Python merge/
# verify calls meaningful instead of vacuous. "skeptic:merge"/
# "skeptic:verify" are CANNED-mocked here (never trusted by this file's own
# assertions as a stand-in for the REAL disk verification -- every test
# that cares about that calls sr.run_merge_fragments/run_verify_merged
# directly) -- mirrors the shipped glossary harness's own "glossary:merge"/
# "glossary:verify" mock exactly. "skeptic:verify"'s own canned result is
# optionally overridable via a top-level `PLAN.verify` object (absent ->
# the same `{verified: true}` default every other test relies on) -- this
# lets a test drive a SPECIFIC verify-mode result (e.g. a schema-shaped
# `{verified: false, frozen_input_mismatch: true, ...}`) through the REAL
# JS control flow to check that control flow's own handling of it (P1 fix,
# review-bot #227's frozen-input-mismatch propagation), without needing a
# real skeptic_ready.py --verify-merged failure to produce it.
SKEPTIC_HARNESS_TEMPLATE = r"""
'use strict';
const fs = require('fs');
const path = require('path');

__WRAPPED_SOURCE__

const PLAN = __PLAN_JSON__;
const BATCHES_ARGS = __BATCHES_JSON__;
const ROOT = __ROOT_JSON__;
const RUN_ID = __RUN_ID_JSON__;
const RUN_DIR = ROOT + "/skeptic/runs/" + RUN_ID;
const callsLog = [];
const logLines = [];
let pipelineCalled = false;

function fragmentPathFor(idx) { return RUN_DIR + "/triage_" + idx + ".json"; }

async function agent(promptText, opts) {
  opts = opts || {};
  const label = opts.label || "";
  callsLog.push({
    label: label,
    phase: opts.phase || null,
    effort: opts.effort || null,
    agentType: opts.agentType || null,
    hasSchema: !!opts.schema,
    // Captures the ACTUAL schema literal's own `required` array at the
    // moment of each call -- lets a test assert directly on the real
    // template's schema declaration (e.g. that SKEPTIC_VERIFY_SCHEMA
    // requires `frozen_input_mismatch`, codex round-4 fix) without needing
    // this synthetic mock to simulate the real Workflow engine's own
    // schema-validation/retry-until-valid enforcement, which this harness
    // was never built to model.
    schemaRequired: (opts.schema && opts.schema.required) || null,
  });

  if (label === "skeptic:merge") return "MERGED (mock)";
  if (label === "skeptic:verify") return (PLAN.verify !== undefined) ? PLAN.verify : { verified: true };
  if (label === "skeptic:frozen-check") {
    if (PLAN.frozenCheck !== undefined) return PLAN.frozenCheck;
    // codex round 2: deliberately NOT canned, unlike skeptic:merge/
    // skeptic:verify above -- this is the exact "an EXECUTING template
    // regression" codex asked for: the REAL skeptic_ready.py
    // --check-frozen-inputs subprocess, run against REAL on-disk files
    // (never a mock), proving the JS's own frozen-input branch (below,
    // guarding the notReadyBatches return) reacts correctly to what
    // production would actually compute -- not a synthetic stand-in.
    const cp = require('child_process');
    const cmdArgs = [
      ROOT + "/scripts/skeptic_ready.py", "--check-frozen-inputs", RUN_DIR + "/assignments.json",
      "--canon", ROOT + "/canon.json", "--senses-path", ROOT + "/canon_senses.json",
      "--manifest-path", ROOT + "/manifest.json",
    ];
    let out;
    try {
      out = cp.execFileSync("python3", cmdArgs, { encoding: "utf8" });
    } catch (err) {
      // --check-frozen-inputs exits 1 when frozen_input_mismatch is true --
      // still a valid JSON line on stdout, never a harness failure.
      out = err.stdout;
    }
    return JSON.parse(out);
  }

  const parts = label.split(":");
  const kind = parts[1];
  const idx = parts[parts.length - 1];
  const p = PLAN[idx] || {};
  if (kind === "precheck") return (p.precheck !== undefined) ? p.precheck : ("ABSENT " + idx);
  if (kind === "dispatch") {
    if (p.dispatchWrite !== undefined) {
      const outPath = fragmentPathFor(idx);
      fs.mkdirSync(path.dirname(outPath), { recursive: true });
      fs.writeFileSync(outPath, JSON.stringify(p.dispatchWrite));
    }
    return "FRAGMENT " + idx;
  }
  if (kind === "wait") return (p.wait !== undefined) ? p.wait : ("READY " + idx);
  throw new Error("skeptic mock agent(): unrecognized label " + label);
}

async function pipeline(items, stage) {
  pipelineCalled = true;
  const out = [];
  for (const item of items) {
    out.push(await stage(item));
  }
  return out;
}

function log(msg) { logLines.push(String(msg)); }

(async () => {
  try {
    const result = await __workflowMain__(agent, pipeline, log, BATCHES_ARGS);
    process.stdout.write(JSON.stringify({
      result: result,
      calls: callsLog,
      log: logLines,
      pipelineCalled: pipelineCalled,
    }));
  } catch (err) {
    process.stderr.write("HARNESS_ERROR: " + (err && err.stack || String(err)) + "\n");
    process.exit(1);
  }
})();
"""


def build_skeptic_harness(js_source: str, batches: list, plan: dict, root: str, run_id: str) -> str:
    wrapped = _wrap_for_execution(js_source)
    text = SKEPTIC_HARNESS_TEMPLATE.replace("__WRAPPED_SOURCE__", wrapped)
    text = text.replace("__PLAN_JSON__", json.dumps(plan))
    text = text.replace("__BATCHES_JSON__", json.dumps(batches))
    text = text.replace("__ROOT_JSON__", json.dumps(root))
    text = text.replace("__RUN_ID_JSON__", json.dumps(run_id))
    return text


def run_skeptic_workflow(*, tmp_path: Path, durable_root: str, particle_config: str, run_id: str,
                          batch_agent_cap: int, batches: list, plan: dict,
                          source_lang: str = "French", timeout: int = 30) -> dict:
    assert NODE is not None, "node executable not found on PATH -- required to run this test file"
    js_source = instantiate_skeptic_pass(
        durable_root=durable_root, source_lang=source_lang, particle_config=particle_config,
        run_id=run_id, batch_agent_cap=batch_agent_cap,
    )
    harness_text = build_skeptic_harness(js_source, batches, plan, durable_root, run_id)
    harness_path = tmp_path / "skeptic_harness.js"
    harness_path.write_text(harness_text, encoding="utf-8")

    proc = subprocess.run([NODE, str(harness_path)], capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise AssertionError(
            f"skeptic harness execution failed (exit {proc.returncode}):\n"
            f"STDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
        )
    return json.loads(proc.stdout)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_e2e_full_pipeline_happy_path_real_merge_and_verify(tmp_path):
    """The real acceptance chain: dispatch->wait control flow (asserted from
    the JS harness) plus REAL Python merge+verify (asserted independently,
    never from the mock's own canned "skeptic:verify" return)."""
    durable_root = str(tmp_path)
    lang_dir = tmp_path / "languages"
    particle_config = write_particle_config(lang_dir)
    lang = bn.load_language_config(particle_config, languages_dir=lang_dir)

    text = "Jean met Paul at the market. Jean disappeared soon after."
    block_id, blk = block(text)
    manifest_path = tmp_path / "manifest.json"
    write_json(manifest_path, make_manifest((block_id, blk)))
    jean_evidence = evidence_for("Jean", block_id, "seg01", text, lang)

    run_id = "e2e-run-happy"
    run_dir = tmp_path / "skeptic" / "runs" / run_id
    write_json(run_dir / "assignments_0.json", [aid("Jean")])
    write_json(run_dir / "assignments.json", make_aggregate_manifest(
        run_id, [make_assignment_for_manifest("Jean", [jean_evidence])],
    ))

    batches = [{"index": 0, "assignments": [make_assignment_for_args("Jean", [window_with_text(jean_evidence, text)])]}]
    dispatch_doc = {"schema_version": 1, "run_id": run_id, "records": [adverse_record("Jean", jean_evidence)]}
    plan = {"0": {"precheck": "ABSENT 0", "dispatchWrite": dispatch_doc, "wait": "READY 0"}}

    out = run_skeptic_workflow(
        tmp_path=tmp_path, durable_root=durable_root, particle_config=particle_config,
        run_id=run_id, batch_agent_cap=10_000, batches=batches, plan=plan,
    )

    assert out["pipelineCalled"] is True
    assert out["result"]["merged"] is True
    labels = [c["label"] for c in out["calls"]]
    assert labels == [
        "skeptic:precheck:0", "skeptic:dispatch:0", "skeptic:wait:0",
        "skeptic:merge", "skeptic:verify",
    ]

    # MUTATION this guards: if the assertions above were the whole test,
    # a template that simply always returned merged:true (never actually
    # dispatching a real merge) would still pass -- the REAL Python calls
    # below are what actually prove the fragment on disk is genuine.
    triage_path = tmp_path / "skeptic_triage.json"
    merge_result = sr.run_merge_fragments(run_dir, triage_path)
    assert merge_result["records"] == 1

    verify_result = sr.run_verify_merged(
        triage_path, run_dir / "assignments.json", manifest_path, particle_config, languages_dir=lang_dir,
    )
    assert verify_result == {"verified": True, "missing": [], "frozen_input_mismatch": False}

    merged = json.loads(triage_path.read_text(encoding="utf-8"))
    assert merged["records"][0]["source_form"] == "Jean"
    assert merged["records"][0]["verdict"] == "adverse"


def test_e2e_coverage_gap_verify_merged_fails(tmp_path):
    """MUTATION this guards: if --verify-merged computed coverage from a
    batch's own claimed content instead of the independently pre-written
    aggregate assignments.json, a fragment that silently dropped an
    assigned entity (Marie, below) would never be caught."""
    durable_root = str(tmp_path)
    lang_dir = tmp_path / "languages"
    particle_config = write_particle_config(lang_dir)
    lang = bn.load_language_config(particle_config, languages_dir=lang_dir)

    text = "Jean met Marie at the market."
    block_id, blk = block(text)
    manifest_path = tmp_path / "manifest.json"
    write_json(manifest_path, make_manifest((block_id, blk)))
    jean_evidence = evidence_for("Jean", block_id, "seg01", text, lang)
    marie_evidence = evidence_for("Marie", block_id, "seg01", text, lang)

    run_id = "e2e-run-gap"
    run_dir = tmp_path / "skeptic" / "runs" / run_id
    write_json(run_dir / "assignments_0.json", [aid("Jean"), aid("Marie")])
    write_json(run_dir / "assignments.json", make_aggregate_manifest(run_id, [
        make_assignment_for_manifest("Jean", [jean_evidence]),
        make_assignment_for_manifest("Marie", [marie_evidence]),
    ]))

    batches = [{"index": 0, "assignments": [
        make_assignment_for_args("Jean", [window_with_text(jean_evidence, text)]),
        make_assignment_for_args("Marie", [window_with_text(marie_evidence, text)]),
    ]}]
    # codex "forgets" Marie -- only Jean's record makes it into the fragment.
    dispatch_doc = {"schema_version": 1, "run_id": run_id, "records": [adverse_record("Jean", jean_evidence)]}
    plan = {"0": {"precheck": "ABSENT 0", "dispatchWrite": dispatch_doc, "wait": "READY 0"}}

    out = run_skeptic_workflow(
        tmp_path=tmp_path, durable_root=durable_root, particle_config=particle_config,
        run_id=run_id, batch_agent_cap=10_000, batches=batches, plan=plan,
    )
    # The JS control flow itself trusted the CANNED mock verify -- this is
    # exactly why the real, independent check below is the one that matters.
    assert out["result"]["merged"] is True

    triage_path = tmp_path / "skeptic_triage.json"
    sr.run_merge_fragments(run_dir, triage_path)
    result = sr.run_verify_merged(
        triage_path, run_dir / "assignments.json", manifest_path, particle_config, languages_dir=lang_dir,
    )
    assert result["verified"] is False
    assert any("coverage gap" in m for m in result["missing"])


def test_e2e_batch_never_ready_short_circuits_before_merge(tmp_path):
    """Partial coverage across batches: one batch's fragment never becomes
    READY -- the control flow must stop before merge is even attempted,
    never silently merge what IS ready and paper over the rest."""
    durable_root = str(tmp_path)
    lang_dir = tmp_path / "languages"
    particle_config = write_particle_config(lang_dir)
    lang = bn.load_language_config(particle_config, languages_dir=lang_dir)
    text = "Jean met Marie at the market."
    block_id, blk = block(text)
    manifest_path = tmp_path / "manifest.json"
    write_json(manifest_path, make_manifest((block_id, blk)))
    jean_evidence = evidence_for("Jean", block_id, "seg01", text, lang)
    marie_evidence = evidence_for("Marie", block_id, "seg01", text, lang)

    run_id = "e2e-run-partial"
    run_dir = tmp_path / "skeptic" / "runs" / run_id
    write_json(run_dir / "assignments_0.json", [aid("Jean")])
    write_json(run_dir / "assignments_1.json", [aid("Marie")])
    write_json(run_dir / "assignments.json", make_aggregate_manifest(run_id, [
        make_assignment_for_manifest("Jean", [jean_evidence], batch_index=0),
        make_assignment_for_manifest("Marie", [marie_evidence], batch_index=1),
    ], batch_count=2))

    batches = [
        {"index": 0, "assignments": [make_assignment_for_args("Jean", [window_with_text(jean_evidence, text)])]},
        {"index": 1, "assignments": [make_assignment_for_args("Marie", [window_with_text(marie_evidence, text)])]},
    ]
    dispatch_doc_0 = {"schema_version": 1, "run_id": run_id, "records": [adverse_record("Jean", jean_evidence)]}
    plan = {
        "0": {"precheck": "ABSENT 0", "dispatchWrite": dispatch_doc_0, "wait": "READY 0"},
        "1": {"precheck": "ABSENT 1", "wait": "TIMEOUT 1"},  # batch 1's fragment never becomes ready
        # This test is about the not-ready-batches short-circuit itself, not
        # the frozen-input check -- canned clean, mirrors skeptic:verify's
        # own optional-override convention.
        "frozenCheck": {"frozen_input_mismatch": False},
    }

    out = run_skeptic_workflow(
        tmp_path=tmp_path, durable_root=durable_root, particle_config=particle_config,
        run_id=run_id, batch_agent_cap=10_000, batches=batches, plan=plan,
    )
    assert out["result"]["merged"] is False
    assert out["result"]["reason"] == "fragment-check-failed"
    assert out["result"]["notReady"] == [1]
    labels = [c["label"] for c in out["calls"]]
    assert "skeptic:merge" not in labels
    assert "skeptic:verify" not in labels


def test_e2e_frozen_input_mismatch_from_not_ready_batches_real_check(tmp_path):
    """codex round 2's own ask: an EXECUTING template regression where the
    sidecar becomes malformed AFTER stamping but BEFORE fragment
    validation -- asserting frozenInputMismatch:true rather than
    fragment-check-failed. Unlike test_e2e_frozen_input_mismatch_surfaces_
    distinct_signal (which drives the EXISTING verify-merged path via a
    canned PLAN.verify), this batch NEVER becomes ready at all
    (precheck=ABSENT, wait=TIMEOUT, mirroring
    test_e2e_batch_never_ready_short_circuits_before_merge exactly) -- the
    pre-fix pipeline would reach `fragment-check-failed` here and never
    even attempt merge+verify, so the H1 tripwire there would never fire.
    The "skeptic:frozen-check" mock label is DELIBERATELY NOT canned (see
    the harness's own agent() implementation) -- it runs the REAL
    skeptic_ready.py --check-frozen-inputs subprocess against REAL,
    genuinely-tampered on-disk files, proving both the JS's own branch
    logic AND the Python CLI's real answer, not a synthetic stand-in for
    either."""
    stage_skeptic_ready_scripts(tmp_path)
    durable_root = str(tmp_path)
    lang_dir = tmp_path / "languages"
    particle_config = write_particle_config(lang_dir)
    text = "Jean walked home alone."
    block_id, blk = block(text)
    manifest_path = tmp_path / "manifest.json"
    write_json(manifest_path, make_manifest((block_id, blk)))

    canon_path = tmp_path / "canon.json"
    canon_path.write_text(json.dumps({"entries": {}}), encoding="utf-8")
    senses_path = tmp_path / "canon_senses.json"
    senses_path.write_text(
        json.dumps({"schema_version": 1, "entries_by_source_form": {}}), encoding="utf-8"
    )

    run_id = "e2e-run-frozen-not-ready"
    run_dir = tmp_path / "skeptic" / "runs" / run_id
    write_json(run_dir / "assignments_0.json", [aid("Jean")])
    # Stamped BEFORE the tamper below -- the aggregate manifest records the
    # frozen inputs' state AT SETUP TIME, exactly as skeptic_setup.py would.
    write_json(run_dir / "assignments.json", {
        **make_aggregate_manifest(run_id, [make_assignment_for_manifest("Jean", [])]),
        "canon_sha256": suspicion_scan.compute_frozen_input_hash(canon_path),
        "manifest_sha256": suspicion_scan.compute_frozen_input_hash(manifest_path),
        "senses_sha256": suspicion_scan.compute_frozen_input_hash(senses_path),
    })

    # Tamper: overwrite canon_senses.json with SCHEMA-INVALID content
    # (codex's own "becomes malformed" framing) AFTER stamping, BEFORE this
    # run's fragment ever validates.
    senses_path.write_text(json.dumps({
        "schema_version": 1,
        "entries_by_source_form": {"Injected": {"senses": [
            {"sense_id": "s1", "disambiguator": "only one", "index_scope": "narrative",
             "evidence": {"block": "b1", "seg": "seg01", "char_start": 0, "char_end": 4,
                          "context_start": 0, "context_end": 20, "sha256": "a" * 64}},
        ]}},
    }), encoding="utf-8")

    batches = [{"index": 0, "assignments": [make_assignment_for_args("Jean", [])]}]
    plan = {
        "0": {"precheck": "ABSENT 0", "wait": "TIMEOUT 0"},  # never becomes ready
    }

    out = run_skeptic_workflow(
        tmp_path=tmp_path, durable_root=durable_root, particle_config=particle_config,
        run_id=run_id, batch_agent_cap=10_000, batches=batches, plan=plan,
    )
    # Mutation: if the notReadyBatches branch never called
    # frozenInputCheckPrompt() at all (the pre-fix shape), this would read
    # merged:false, reason:"fragment-check-failed" instead -- the exact
    # silent-downgrade codex round 2 found.
    assert out["result"]["merged"] is False
    assert out["result"]["reason"] == "frozen-input-mismatch"
    assert out["result"]["frozenInputMismatch"] is True
    labels = [c["label"] for c in out["calls"]]
    assert "skeptic:frozen-check" in labels
    assert "skeptic:merge" not in labels
    assert "skeptic:verify" not in labels


def test_e2e_not_ready_batches_without_tamper_still_reports_ordinary_failure(tmp_path):
    """Positive control for the fix above, mirrored on the REAL (not
    canned) --check-frozen-inputs path: when nothing was actually tampered,
    the notReadyBatches branch must still report the ordinary
    "fragment-check-failed" outcome, never the fatal one -- the new check
    must not turn every merely-slow/never-finished batch into a false
    FATAL HALT."""
    stage_skeptic_ready_scripts(tmp_path)
    durable_root = str(tmp_path)
    lang_dir = tmp_path / "languages"
    particle_config = write_particle_config(lang_dir)
    text = "Jean walked home alone."
    block_id, blk = block(text)
    manifest_path = tmp_path / "manifest.json"
    write_json(manifest_path, make_manifest((block_id, blk)))

    canon_path = tmp_path / "canon.json"
    canon_path.write_text(json.dumps({"entries": {}}), encoding="utf-8")
    senses_path = tmp_path / "canon_senses.json"
    senses_path.write_text(
        json.dumps({"schema_version": 1, "entries_by_source_form": {}}), encoding="utf-8"
    )

    run_id = "e2e-run-not-ready-clean"
    run_dir = tmp_path / "skeptic" / "runs" / run_id
    write_json(run_dir / "assignments_0.json", [aid("Jean")])
    write_json(run_dir / "assignments.json", {
        **make_aggregate_manifest(run_id, [make_assignment_for_manifest("Jean", [])]),
        "canon_sha256": suspicion_scan.compute_frozen_input_hash(canon_path),
        "manifest_sha256": suspicion_scan.compute_frozen_input_hash(manifest_path),
        "senses_sha256": suspicion_scan.compute_frozen_input_hash(senses_path),
    })
    # No tamper this time.

    batches = [{"index": 0, "assignments": [make_assignment_for_args("Jean", [])]}]
    plan = {"0": {"precheck": "ABSENT 0", "wait": "TIMEOUT 0"}}

    out = run_skeptic_workflow(
        tmp_path=tmp_path, durable_root=durable_root, particle_config=particle_config,
        run_id=run_id, batch_agent_cap=10_000, batches=batches, plan=plan,
    )
    assert out["result"]["merged"] is False
    assert out["result"]["reason"] == "fragment-check-failed"
    assert "frozenInputMismatch" not in out["result"]
    labels = [c["label"] for c in out["calls"]]
    assert "skeptic:frozen-check" in labels


def test_e2e_escalates_to_insufficient_window_when_windows_truncated(tmp_path):
    durable_root = str(tmp_path)
    lang_dir = tmp_path / "languages"
    particle_config = write_particle_config(lang_dir)
    lang = bn.load_language_config(particle_config, languages_dir=lang_dir)
    text = "Jean walked home alone."
    block_id, blk = block(text)
    manifest_path = tmp_path / "manifest.json"
    write_json(manifest_path, make_manifest((block_id, blk)))
    jean_evidence = evidence_for("Jean", block_id, "seg01", text, lang)

    run_id = "e2e-run-truncated"
    run_dir = tmp_path / "skeptic" / "runs" / run_id
    write_json(run_dir / "assignments_0.json", [aid("Jean")])
    write_json(run_dir / "assignments.json", make_aggregate_manifest(run_id, [
        make_assignment_for_manifest("Jean", [jean_evidence], windows_truncated=True),
    ]))

    batches = [{"index": 0, "assignments": [
        make_assignment_for_args("Jean", [window_with_text(jean_evidence, text)], windows_truncated=True),
    ]}]
    dispatch_doc = {
        "schema_version": 1, "run_id": run_id,
        "records": [insufficient_record("Jean", rationale="windows truncated -- cannot be confident")],
    }
    plan = {"0": {"precheck": "ABSENT 0", "dispatchWrite": dispatch_doc, "wait": "READY 0"}}

    out = run_skeptic_workflow(
        tmp_path=tmp_path, durable_root=durable_root, particle_config=particle_config,
        run_id=run_id, batch_agent_cap=10_000, batches=batches, plan=plan,
    )
    assert out["result"]["merged"] is True

    triage_path = tmp_path / "skeptic_triage.json"
    sr.run_merge_fragments(run_dir, triage_path)
    result = sr.run_verify_merged(
        triage_path, run_dir / "assignments.json", manifest_path, particle_config, languages_dir=lang_dir,
    )
    assert result == {"verified": True, "missing": [], "frozen_input_mismatch": False}
    merged = json.loads(triage_path.read_text(encoding="utf-8"))
    assert merged["records"][0]["verdict"] == "insufficient_window"


def test_e2e_embedded_verse_citation_coerced_to_insufficient_window(tmp_path):
    """MUTATION this guards: an evidence citation whose `block` id is a
    verse-placeholder id (never a real manifest.blocks{} key -- exactly
    what an embedded-verse node's own citation would look like, since
    evidence_verify.py authenticates only against blocks{}, never
    verse.store[]) must be coerced to insufficient_window by the real
    --validate-fragment step (run here exactly as the WAIT step's poll
    would in production), and the merged+verified chain must still
    complete cleanly on the resulting, now-safe record."""
    durable_root = str(tmp_path)
    lang_dir = tmp_path / "languages"
    particle_config = write_particle_config(lang_dir)
    lang = bn.load_language_config(particle_config, languages_dir=lang_dir)
    text = "Jean recited a verse and then walked home."
    block_id, blk = block(text)
    manifest_path = tmp_path / "manifest.json"
    write_json(manifest_path, make_manifest((block_id, blk)))
    jean_evidence = evidence_for("Jean", block_id, "seg01", text, lang)
    verse_evidence = dict(jean_evidence)
    verse_evidence["block"] = "VERSE_NODE:0001"  # not a manifest.blocks{} key

    run_id = "e2e-run-verse"
    run_dir = tmp_path / "skeptic" / "runs" / run_id
    write_json(run_dir / "assignments_0.json", [aid("Jean")])
    write_json(run_dir / "assignments.json", make_aggregate_manifest(
        run_id, [make_assignment_for_manifest("Jean", [jean_evidence])],
    ))

    batches = [{"index": 0, "assignments": [make_assignment_for_args("Jean", [window_with_text(jean_evidence, text)])]}]
    dispatch_doc = {"schema_version": 1, "run_id": run_id, "records": [adverse_record("Jean", verse_evidence)]}
    plan = {"0": {"precheck": "ABSENT 0", "dispatchWrite": dispatch_doc, "wait": "READY 0"}}

    out = run_skeptic_workflow(
        tmp_path=tmp_path, durable_root=durable_root, particle_config=particle_config,
        run_id=run_id, batch_agent_cap=10_000, batches=batches, plan=plan,
    )
    assert out["result"]["merged"] is True

    frag_path = run_dir / "triage_0.json"
    validate_result = sr.run_validate_fragment(
        frag_path, manifest_path, particle_config, languages_dir=lang_dir,
        expect_assignments_file=run_dir / "assignments_0.json",
    )
    assert validate_result["coerced"] == 1

    triage_path = tmp_path / "skeptic_triage.json"
    sr.run_merge_fragments(run_dir, triage_path)
    result = sr.run_verify_merged(
        triage_path, run_dir / "assignments.json", manifest_path, particle_config, languages_dir=lang_dir,
    )
    assert result == {"verified": True, "missing": [], "frozen_input_mismatch": False}
    merged = json.loads(triage_path.read_text(encoding="utf-8"))
    assert merged["records"][0]["verdict"] == "insufficient_window"


def test_e2e_preflight_batch_too_large_dispatches_nothing(tmp_path):
    """Same 3*N+2 preflight formula as glossary -- locks the shared shape
    so a future edit to one template's estimator can't silently drift from
    the other's."""
    durable_root = str(tmp_path)
    lang_dir = tmp_path / "languages"
    particle_config = write_particle_config(lang_dir)
    run_id = "e2e-run-toobig"
    batches = [
        {"index": 0, "assignments": [make_assignment_for_args("Jean", [])]},
        {"index": 1, "assignments": [make_assignment_for_args("Marie", [])]},
    ]
    estimated = 3 * len(batches) + 2  # 8

    out = run_skeptic_workflow(
        tmp_path=tmp_path, durable_root=durable_root, particle_config=particle_config,
        run_id=run_id, batch_agent_cap=estimated - 1, batches=batches, plan={},
    )
    assert out["pipelineCalled"] is False
    assert out["calls"] == []
    assert out["result"] == {
        "merged": False, "reason": "batch-too-large",
        "estimatedCalls": estimated, "cap": estimated - 1,
    }


# ---------------------------------------------------------------------------
# review-bot #227 P1 fixes: exact-match sentinels (content-matching-
# sentinel-fragility class) + a distinct frozen-input-mismatch signal.
# ---------------------------------------------------------------------------

def test_e2e_precheck_substring_collision_does_not_falsely_resume_skip(tmp_path):
    """RED before the P1 sentinel-exact-match fix: the OLD
    `precheck.indexOf("PRESENT") !== -1` check falsely matched a FAILURE
    reply that merely contains the literal substring "PRESENT" inside its
    own explanatory prose (e.g. "ABSENT 0 (fragment missing; not
    PRESENT)"), resume-skipping WITHOUT dispatching -- so a recoverable
    missing/corrupt fragment was silently never repaired on resume."""
    durable_root = str(tmp_path)
    lang_dir = tmp_path / "languages"
    particle_config = write_particle_config(lang_dir)
    lang = bn.load_language_config(particle_config, languages_dir=lang_dir)
    text = "Jean walked home alone."
    block_id, blk = block(text)
    manifest_path = tmp_path / "manifest.json"
    write_json(manifest_path, make_manifest((block_id, blk)))
    jean_evidence = evidence_for("Jean", block_id, "seg01", text, lang)

    run_id = "e2e-run-precheck-collision"
    run_dir = tmp_path / "skeptic" / "runs" / run_id
    write_json(run_dir / "assignments_0.json", [aid("Jean")])
    write_json(run_dir / "assignments.json", make_aggregate_manifest(
        run_id, [make_assignment_for_manifest("Jean", [jean_evidence])],
    ))

    batches = [{"index": 0, "assignments": [make_assignment_for_args("Jean", [window_with_text(jean_evidence, text)])]}]
    dispatch_doc = {"schema_version": 1, "run_id": run_id, "records": [adverse_record("Jean", jean_evidence)]}
    plan = {"0": {
        "precheck": "ABSENT 0 (fragment missing; not PRESENT)",
        "dispatchWrite": dispatch_doc, "wait": "READY 0",
    }}

    out = run_skeptic_workflow(
        tmp_path=tmp_path, durable_root=durable_root, particle_config=particle_config,
        run_id=run_id, batch_agent_cap=10_000, batches=batches, plan=plan,
    )
    labels = [c["label"] for c in out["calls"]]
    # A substring-collision bug would resume-skip straight from precheck to
    # merge/verify, never calling dispatch/wait at all.
    assert "skeptic:dispatch:0" in labels
    assert "skeptic:wait:0" in labels
    assert out["result"]["merged"] is True


def test_e2e_wait_substring_collision_reports_not_ready(tmp_path):
    """RED before the P1 sentinel-exact-match fix: the OLD
    `ready.indexOf("READY") === -1` check falsely treated a TIMEOUT reply
    that merely contains the literal substring "READY" inside its own
    explanatory prose (e.g. "TIMEOUT 0 (not READY)") as ready -- `indexOf`
    finds "READY" so the negated `=== -1` check was false, leaving the
    batch wrongly marked ready:true."""
    durable_root = str(tmp_path)
    lang_dir = tmp_path / "languages"
    particle_config = write_particle_config(lang_dir)
    lang = bn.load_language_config(particle_config, languages_dir=lang_dir)
    text = "Jean walked home alone."
    block_id, blk = block(text)
    manifest_path = tmp_path / "manifest.json"
    write_json(manifest_path, make_manifest((block_id, blk)))
    jean_evidence = evidence_for("Jean", block_id, "seg01", text, lang)

    run_id = "e2e-run-wait-collision"
    run_dir = tmp_path / "skeptic" / "runs" / run_id
    write_json(run_dir / "assignments_0.json", [aid("Jean")])
    write_json(run_dir / "assignments.json", make_aggregate_manifest(
        run_id, [make_assignment_for_manifest("Jean", [jean_evidence])],
    ))

    batches = [{"index": 0, "assignments": [make_assignment_for_args("Jean", [window_with_text(jean_evidence, text)])]}]
    dispatch_doc = {"schema_version": 1, "run_id": run_id, "records": [adverse_record("Jean", jean_evidence)]}
    plan = {
        "0": {
            "precheck": "ABSENT 0", "dispatchWrite": dispatch_doc,
            "wait": "TIMEOUT 0 (not READY)",
        },
        # This test is about the sentinel substring-collision fix, not the
        # frozen-input check -- canned clean.
        "frozenCheck": {"frozen_input_mismatch": False},
    }

    out = run_skeptic_workflow(
        tmp_path=tmp_path, durable_root=durable_root, particle_config=particle_config,
        run_id=run_id, batch_agent_cap=10_000, batches=batches, plan=plan,
    )
    assert out["result"]["merged"] is False
    assert out["result"]["reason"] == "fragment-check-failed"
    assert out["result"]["notReady"] == [0]
    labels = [c["label"] for c in out["calls"]]
    assert "skeptic:merge" not in labels
    assert "skeptic:verify" not in labels


# ---------------------------------------------------------------------------
# #308 P1 fixes: line-oriented sentinel verdicts (sentinelVerdict()) at
# skeptic-pass-wf.template.js's two sentinel sites -- A' (batch precheck)
# and B' (batch wait). The #227 fix above (mirroring #228) killed the
# substring false-POSITIVE; #308 is the false-NEGATIVE dual that whole-
# string cure introduced -- a benign prose-decorated sentinel misclassified
# as absent/timed-out. None of these tests need a real on-disk fragment:
# the mock's precheck/dispatch/wait branches never touch disk themselves
# (only an explicit ``dispatchWrite`` and the canned merge/verify results
# do), so an empty-windows assignment is enough, mirroring
# ``test_e2e_preflight_batch_too_large_dispatches_nothing``'s own fixture.
# ---------------------------------------------------------------------------

def test_e2e_precheck_decorated_present_still_resume_skips(tmp_path):
    """Site A' accept: a genuine PRESENT reply decorated with a prose
    preamble (the observed real #308 shape) must still resume-skip, not
    fall through to a full dispatch."""
    durable_root = str(tmp_path)
    lang_dir = tmp_path / "languages"
    particle_config = write_particle_config(lang_dir)
    run_id = "e2e-run-precheck-decorated"
    batches = [{"index": 0, "assignments": [make_assignment_for_args("Jean", [])]}]
    plan = {"0": {
        "precheck": "The precheck command exited 0, confirming the existing fragment is already valid.\n\nPRESENT 0",
    }}

    out = run_skeptic_workflow(
        tmp_path=tmp_path, durable_root=durable_root, particle_config=particle_config,
        run_id=run_id, batch_agent_cap=10_000, batches=batches, plan=plan,
    )
    labels = [c["label"] for c in out["calls"]]
    assert "skeptic:dispatch:0" not in labels
    assert "skeptic:wait:0" not in labels
    assert out["result"]["merged"] is True


def test_e2e_wait_decorated_ready_is_accepted_not_timeout(tmp_path):
    """Site B' accept: a genuine READY reply decorated with a prose
    preamble (the exact #308 evidence reply, journal-verbatim) must be
    accepted, not misclassified as a timeout."""
    durable_root = str(tmp_path)
    lang_dir = tmp_path / "languages"
    particle_config = write_particle_config(lang_dir)
    run_id = "e2e-run-wait-decorated"
    batches = [{"index": 0, "assignments": [make_assignment_for_args("Jean", [])]}]
    plan = {
        "0": {
            "precheck": "ABSENT 0",
            "wait": "The poll confirmed the review artifact is ready (exit 0).\n\nREADY 0",
        },
        # Not reached on the fix (notReadyBatches stays empty), but pins a
        # clean, deterministic canned answer rather than falling through to
        # the REAL "skeptic:frozen-check" subprocess branch (which needs
        # staged scripts) should a future regression misclassify this
        # reply as not-ready -- mirrors the fail-priority test below.
        "frozenCheck": {"frozen_input_mismatch": False},
    }

    out = run_skeptic_workflow(
        tmp_path=tmp_path, durable_root=durable_root, particle_config=particle_config,
        run_id=run_id, batch_agent_cap=10_000, batches=batches, plan=plan,
    )
    assert out["result"]["merged"] is True
    labels = [c["label"] for c in out["calls"]]
    assert "skeptic:merge" in labels
    assert "skeptic:verify" in labels


def test_e2e_precheck_fail_priority_discriminating_order(tmp_path):
    """Fail-priority, discriminating order (PLAN-308 sec3 item 3's round-3
    codex finding): ABSENT before a trailing PRESENT line must still
    regenerate -- proves the fail-sentinel scan runs over every line, not
    just the last one (a last-line-only reader would wrongly accept this,
    since PRESENT is the reply's own final line)."""
    durable_root = str(tmp_path)
    lang_dir = tmp_path / "languages"
    particle_config = write_particle_config(lang_dir)
    run_id = "e2e-run-precheck-discriminating"
    batches = [{"index": 0, "assignments": [make_assignment_for_args("Jean", [])]}]
    plan = {"0": {"precheck": "ABSENT 0\nPRESENT 0"}}

    out = run_skeptic_workflow(
        tmp_path=tmp_path, durable_root=durable_root, particle_config=particle_config,
        run_id=run_id, batch_agent_cap=10_000, batches=batches, plan=plan,
    )
    labels = [c["label"] for c in out["calls"]]
    assert "skeptic:dispatch:0" in labels
    assert "skeptic:wait:0" in labels
    assert out["result"]["merged"] is True


def test_e2e_wait_fail_priority_discriminating_order(tmp_path):
    """Same discriminating-order proof at site B': TIMEOUT before a
    trailing READY line must still time out."""
    durable_root = str(tmp_path)
    lang_dir = tmp_path / "languages"
    particle_config = write_particle_config(lang_dir)
    run_id = "e2e-run-wait-discriminating"
    batches = [{"index": 0, "assignments": [make_assignment_for_args("Jean", [])]}]
    plan = {
        "0": {"precheck": "ABSENT 0", "wait": "TIMEOUT 0\nREADY 0"},
        # This test is about the sentinel fail-priority fix, not the
        # frozen-input check -- canned clean (mirrors the #227 collision
        # test above).
        "frozenCheck": {"frozen_input_mismatch": False},
    }

    out = run_skeptic_workflow(
        tmp_path=tmp_path, durable_root=durable_root, particle_config=particle_config,
        run_id=run_id, batch_agent_cap=10_000, batches=batches, plan=plan,
    )
    assert out["result"]["merged"] is False
    assert out["result"]["reason"] == "fragment-check-failed"
    assert out["result"]["notReady"] == [0]
    labels = [c["label"] for c in out["calls"]]
    assert "skeptic:merge" not in labels
    assert "skeptic:verify" not in labels


def test_e2e_precheck_non_terminal_quoted_present_still_regenerates(tmp_path):
    """5a non-terminal quoted-success regression (required, not optional):
    a reply that quotes the PRESENT sentinel on a non-final line, then
    disavows it in later prose, must NOT resume-skip -- the sentinel must
    be the reply's own final non-empty line, not merely present anywhere."""
    durable_root = str(tmp_path)
    lang_dir = tmp_path / "languages"
    particle_config = write_particle_config(lang_dir)
    run_id = "e2e-run-precheck-quoted"
    batches = [{"index": 0, "assignments": [make_assignment_for_args("Jean", [])]}]
    plan = {"0": {
        "precheck": "The command failed; quoting the requested success form:\nPRESENT 0\nThat is not my verdict.",
    }}

    out = run_skeptic_workflow(
        tmp_path=tmp_path, durable_root=durable_root, particle_config=particle_config,
        run_id=run_id, batch_agent_cap=10_000, batches=batches, plan=plan,
    )
    labels = [c["label"] for c in out["calls"]]
    assert "skeptic:dispatch:0" in labels
    assert "skeptic:wait:0" in labels
    assert out["result"]["merged"] is True


def test_e2e_wait_non_terminal_quoted_ready_still_times_out(tmp_path):
    """5a non-terminal quoted-success regression at site B' (codex's own
    counter-example, reused verbatim): a reply that quotes READY on a
    non-final line, then disavows it, must still report a timeout."""
    durable_root = str(tmp_path)
    lang_dir = tmp_path / "languages"
    particle_config = write_particle_config(lang_dir)
    run_id = "e2e-run-wait-quoted"
    batches = [{"index": 0, "assignments": [make_assignment_for_args("Jean", [])]}]
    plan = {
        "0": {
            "precheck": "ABSENT 0",
            "wait": "The command failed; quoting the requested success form:\nREADY 0\nThat is not my verdict.",
        },
        "frozenCheck": {"frozen_input_mismatch": False},
    }

    out = run_skeptic_workflow(
        tmp_path=tmp_path, durable_root=durable_root, particle_config=particle_config,
        run_id=run_id, batch_agent_cap=10_000, batches=batches, plan=plan,
    )
    assert out["result"]["merged"] is False
    assert out["result"]["reason"] == "fragment-check-failed"
    assert out["result"]["notReady"] == [0]


def test_e2e_frozen_input_mismatch_surfaces_distinct_signal(tmp_path):
    """RED before the P1 fix (review-bot #227): when skeptic_ready.py
    --verify-merged reports frozen_input_mismatch (a canon.json/
    manifest.json hash mismatch since setup), the Workflow's own JS control
    flow must surface a DISTINCT signal -- reason: "frozen-input-mismatch"
    and frozenInputMismatch: true -- never the generic "verify-failed"
    every ordinary skeptic-pass failure shares, so SKILL.md's exit-contract
    can gate this one case FATAL/HALT while everything else stays
    advisory. Drives the REAL JS control flow (not just the Python
    function) via a canned `PLAN.verify` result, since the JS-side
    propagation is what's under test here."""
    durable_root = str(tmp_path)
    lang_dir = tmp_path / "languages"
    particle_config = write_particle_config(lang_dir)
    lang = bn.load_language_config(particle_config, languages_dir=lang_dir)
    text = "Jean walked home alone."
    block_id, blk = block(text)
    manifest_path = tmp_path / "manifest.json"
    write_json(manifest_path, make_manifest((block_id, blk)))
    jean_evidence = evidence_for("Jean", block_id, "seg01", text, lang)

    run_id = "e2e-run-frozen-mismatch"
    run_dir = tmp_path / "skeptic" / "runs" / run_id
    write_json(run_dir / "assignments_0.json", [aid("Jean")])
    write_json(run_dir / "assignments.json", make_aggregate_manifest(
        run_id, [make_assignment_for_manifest("Jean", [jean_evidence])],
    ))

    batches = [{"index": 0, "assignments": [make_assignment_for_args("Jean", [window_with_text(jean_evidence, text)])]}]
    dispatch_doc = {"schema_version": 1, "run_id": run_id, "records": [adverse_record("Jean", jean_evidence)]}
    plan = {
        "0": {"precheck": "ABSENT 0", "dispatchWrite": dispatch_doc, "wait": "READY 0"},
        "verify": {
            "verified": False,
            "missing": ["canon.json at /tmp/canon.json has changed since skeptic_setup.py "
                        "stamped this run (sha256 aaa != stamped bbb) -- possible tamper of "
                        "the frozen input, HALTING"],
            "frozen_input_mismatch": True,
        },
    }

    out = run_skeptic_workflow(
        tmp_path=tmp_path, durable_root=durable_root, particle_config=particle_config,
        run_id=run_id, batch_agent_cap=10_000, batches=batches, plan=plan,
    )
    assert out["result"]["merged"] is False
    assert out["result"]["reason"] == "frozen-input-mismatch"
    assert out["result"]["frozenInputMismatch"] is True


def test_e2e_ordinary_verify_failure_keeps_generic_reason(tmp_path):
    """Positive control for the fix above: an ORDINARY verify failure
    (frozen_input_mismatch absent/false) must keep the existing generic
    "verify-failed" reason and must NOT set frozenInputMismatch -- only a
    genuine frozen-input hash mismatch gets the distinct signal."""
    durable_root = str(tmp_path)
    lang_dir = tmp_path / "languages"
    particle_config = write_particle_config(lang_dir)
    lang = bn.load_language_config(particle_config, languages_dir=lang_dir)
    text = "Jean walked home alone."
    block_id, blk = block(text)
    manifest_path = tmp_path / "manifest.json"
    write_json(manifest_path, make_manifest((block_id, blk)))
    jean_evidence = evidence_for("Jean", block_id, "seg01", text, lang)

    run_id = "e2e-run-ordinary-verify-fail"
    run_dir = tmp_path / "skeptic" / "runs" / run_id
    write_json(run_dir / "assignments_0.json", [aid("Jean")])
    write_json(run_dir / "assignments.json", make_aggregate_manifest(
        run_id, [make_assignment_for_manifest("Jean", [jean_evidence])],
    ))

    batches = [{"index": 0, "assignments": [make_assignment_for_args("Jean", [window_with_text(jean_evidence, text)])]}]
    dispatch_doc = {"schema_version": 1, "run_id": run_id, "records": [adverse_record("Jean", jean_evidence)]}
    plan = {
        "0": {"precheck": "ABSENT 0", "dispatchWrite": dispatch_doc, "wait": "READY 0"},
        # frozen_input_mismatch explicitly False -- a real schema-conformant
        # relay always includes it now that SKEPTIC_VERIFY_SCHEMA.required
        # covers it (codex round-4 fix); omitting it here would no longer
        # represent a genuine possible relay reply.
        "verify": {
            "verified": False, "missing": ["assignment X has no triage record (coverage gap)"],
            "frozen_input_mismatch": False,
        },
    }

    out = run_skeptic_workflow(
        tmp_path=tmp_path, durable_root=durable_root, particle_config=particle_config,
        run_id=run_id, batch_agent_cap=10_000, batches=batches, plan=plan,
    )
    assert out["result"]["merged"] is False
    assert out["result"]["reason"] == "verify-failed"
    assert "frozenInputMismatch" not in out["result"]


def test_e2e_verify_schema_requires_frozen_input_mismatch(tmp_path):
    """RED before the codex round-4 fix: SKEPTIC_VERIFY_SCHEMA marked
    `frozen_input_mismatch` OPTIONAL (only in `properties`, not
    `required`), so a schema-VALID relay reply could still silently DROP
    the field. Since skeptic_ready.py's run_verify_merged ALWAYS returns
    the field, a faithful relay can always include it -- so marking it
    required (forcing the real Workflow engine's retry-until-valid to
    reject an omission) is safe and closes the "relay drops the one field
    that gates FATAL/HALT" gap. This test can't simulate that real
    engine's retry loop (the mock agent() here never performs schema
    validation, mirroring every other schema-carrying call in this file),
    so it asserts directly on the ACTUAL schema literal the "skeptic:verify"
    call is given -- the real, load-bearing artifact the fix touches."""
    durable_root = str(tmp_path)
    lang_dir = tmp_path / "languages"
    particle_config = write_particle_config(lang_dir)
    lang = bn.load_language_config(particle_config, languages_dir=lang_dir)
    text = "Jean walked home alone."
    block_id, blk = block(text)
    manifest_path = tmp_path / "manifest.json"
    write_json(manifest_path, make_manifest((block_id, blk)))
    jean_evidence = evidence_for("Jean", block_id, "seg01", text, lang)

    run_id = "e2e-run-verify-schema-required"
    run_dir = tmp_path / "skeptic" / "runs" / run_id
    write_json(run_dir / "assignments_0.json", [aid("Jean")])
    write_json(run_dir / "assignments.json", make_aggregate_manifest(
        run_id, [make_assignment_for_manifest("Jean", [jean_evidence])],
    ))

    batches = [{"index": 0, "assignments": [make_assignment_for_args("Jean", [window_with_text(jean_evidence, text)])]}]
    dispatch_doc = {"schema_version": 1, "run_id": run_id, "records": [adverse_record("Jean", jean_evidence)]}
    plan = {"0": {"precheck": "ABSENT 0", "dispatchWrite": dispatch_doc, "wait": "READY 0"}}

    out = run_skeptic_workflow(
        tmp_path=tmp_path, durable_root=durable_root, particle_config=particle_config,
        run_id=run_id, batch_agent_cap=10_000, batches=batches, plan=plan,
    )
    verify_calls = [c for c in out["calls"] if c["label"] == "skeptic:verify"]
    assert len(verify_calls) == 1
    assert verify_calls[0]["hasSchema"] is True
    assert verify_calls[0]["schemaRequired"] is not None
    assert "verified" in verify_calls[0]["schemaRequired"]
    assert "frozen_input_mismatch" in verify_calls[0]["schemaRequired"]
