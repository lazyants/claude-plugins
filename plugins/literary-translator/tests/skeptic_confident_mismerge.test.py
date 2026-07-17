"""tests/skeptic_confident_mismerge.test.py -- RFC #215 Phase-2 ACCEPTANCE:
both motivating failure shapes the plan's "Design" section names explicitly
(plan §"Context"), driven through the REAL skeptic pass chain (the
instantiated ``skeptic-pass-wf.template.js`` with a fake ``agent()``, then
REAL Python ``skeptic_ready.py --validate-fragment``/``--merge-fragments``/
``--verify-merged`` on the actual fragment files) -- never a canned mock
return.

``suspicion_scan.py`` (A1, dispersion/merge_participant DETECTION) and
``skeptic_setup.py`` (A2, the real resume-domain assignment builder) are
separate owners' deliverables. This file's job (A3's own ownership, per the
build contract) is the OTHER half of the acceptance claim: PROVE that once
such an entity IS assigned to the skeptic pass (with the risk_classes a real
scan would have attached), the skeptic chain this file owns -- adversarial
dispatch, schema+evidence-adapter validation, deterministic merge, disk-
independent verify -- resolves it to the correct terminal verdict with
byte-verified evidence. Assignment/aggregate-manifest fixtures below are
therefore hand-built (conforming to skeptic-assignment.schema.json) rather
than produced by a live suspicion_scan.py/skeptic_setup.py run -- exactly
the "construct minimal synthetic assignment fixtures to unblock" contract
allowance for a downstream owner not yet landed.

Case (b)'s reachability is PINNED explicitly (round-3 blocker 3 of the
approved plan), since a real scan's ``high_dispersion`` class defaults to a
12-distinct-seg threshold and the RFC's own motivating form is Hebrew
(caseless): the fixture (i) puts the Hebrew form in
``LanguageConfig.name_inventory`` so the caseless inventory matcher runs at
all (Hebrew letters are Unicode category ``Lo``, not ``Lu`` --
``is_upper_initial()``'s default capitalized-run algorithm cannot see this
form), (ii) places the three occurrences in THREE DISTINCT ``seg`` ids
(asserted by an explicit guard below -- a vacuous trigger would prove
nothing), and (iii) assigns ``risk_classes=["high_dispersion"]`` to record
that a real scan run with ``--dispersion-threshold 3``/
``--windows-per-entity >= 3`` would have genuinely fired for this entity.

Fixtures mirror tests/skeptic_ready.test.py's own helpers (duplicated here,
not imported -- see pytest.ini's own comment on this project's
self-contained-test-file convention).
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


bn = _load_module("bootstrap_names_for_mismerge_test", BOOTSTRAP_NAMES_SCRIPT, SCRIPTS_DIR)
occ = _load_module("occ_index_for_mismerge_test", OCC_INDEX_SCRIPT, SCRIPTS_DIR)
sr = _load_module("skeptic_ready_for_mismerge_test", SKEPTIC_READY_SCRIPT, SCRIPTS_DIR)


# ---------------------------------------------------------------------------
# Fixture helpers (mirror tests/skeptic_ready.test.py / skeptic_pipeline_e2e
# .test.py's own)
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


def window_for(evidence: dict) -> dict:
    return {
        "block": evidence["block"], "seg": evidence["seg"],
        "char_start": evidence["char_start"], "char_end": evidence["char_end"],
    }


def window_with_text(evidence: dict, text: str) -> dict:
    w = window_for(evidence)
    w["text"] = text
    return w


def make_assignment_for_manifest(source_form, evidences, risk_classes=("high_dispersion",),
                                  batch_index=0, windows_truncated=False):
    return {
        "assignment_id": aid(source_form), "source_form": source_form,
        "canonical_target_form": source_form, "risk_classes": list(risk_classes),
        "windows": [window_for(e) for e in evidences],
        "windows_truncated": windows_truncated, "batch_index": batch_index,
    }


def make_assignment_for_args(source_form, windows_with_text_list, risk_classes=("high_dispersion",),
                              windows_truncated=False):
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


def instantiate_skeptic_pass(*, durable_root: str, source_lang: str, particle_config: str,
                              run_id: str, batch_agent_cap: int) -> str:
    text = SKEPTIC_PASS_TEMPLATE.read_text(encoding="utf-8")
    text = text.replace("{{DURABLE_ROOT}}", durable_root)
    text = text.replace("{{SOURCE_LANG}}", source_lang)
    text = text.replace("{{PARTICLE_CONFIG}}", particle_config)
    text = text.replace("{{RUN_ID}}", run_id)
    text = text.replace("{{BATCH_AGENT_CAP}}", str(int(batch_agent_cap)))
    assert "{{" not in text, "fixture instantiation left an unresolved token -- fix the fixture, not the assertion"
    return text


def _wrap_for_execution(js_source: str) -> str:
    assert js_source.count("export const meta") == 1
    body = js_source.replace("export const meta", "const meta", 1)
    return "async function __workflowMain__(agent, pipeline, log, args) {\n" + body + "\n}\n"


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
    label: label, phase: opts.phase || null, effort: opts.effort || null,
    agentType: opts.agentType || null, hasSchema: !!opts.schema,
  });

  if (label === "skeptic:merge") return "MERGED (mock)";
  if (label === "skeptic:verify") return { verified: true };

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
  for (const item of items) { out.push(await stage(item)); }
  return out;
}

function log(msg) { logLines.push(String(msg)); }

(async () => {
  try {
    const result = await __workflowMain__(agent, pipeline, log, BATCHES_ARGS);
    process.stdout.write(JSON.stringify({
      result: result, calls: callsLog, log: logLines, pipelineCalled: pipelineCalled,
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
# Case (a): confident over-merge -> adverse
# ---------------------------------------------------------------------------

def test_confident_overmerge_reaches_adverse_verdict(tmp_path):
    """A hand-seeded confident over-merge: canon.json (a real scan's own
    input, not read by this test) would map BOTH "Jean" and "Jehan" -- two
    distinct source_forms -- to the SAME canonical_target_form, both at
    confidence:"high", tripping suspicion_scan.py's own merge_participant
    risk class (A1's ownership). This test proves the OTHER half: once
    "Jean" is assigned to the skeptic pass with risk_classes=
    ["merge_participant"], a genuinely contradicting source-text window
    drives the REAL chain -- dispatch -> validate-fragment (real,
    evidence re-authenticated) -> merge-fragments (real) -> verify-merged
    (real) -- to `adverse` with byte-verified evidence."""
    durable_root = str(tmp_path)
    lang_dir = tmp_path / "languages"
    particle_config = write_particle_config(lang_dir)
    lang = bn.load_language_config(particle_config, languages_dir=lang_dir)

    text = "Jean was seen in Paris that same morning, while Jehan was already fighting far away in Rome."
    block_id, blk = block(text)
    manifest_path = tmp_path / "manifest.json"
    write_json(manifest_path, make_manifest((block_id, blk)))
    jean_evidence = evidence_for("Jean", block_id, "seg01", text, lang)

    run_id = "acceptance-overmerge"
    run_dir = tmp_path / "skeptic" / "runs" / run_id
    write_json(run_dir / "assignments_0.json", [aid("Jean")])
    write_json(run_dir / "assignments.json", make_aggregate_manifest(run_id, [
        make_assignment_for_manifest("Jean", [jean_evidence], risk_classes=("merge_participant",)),
    ]))

    batches = [{"index": 0, "assignments": [
        make_assignment_for_args("Jean", [window_with_text(jean_evidence, text)], risk_classes=("merge_participant",)),
    ]}]
    dispatch_doc = {
        "schema_version": 1, "run_id": run_id,
        "records": [adverse_record(
            "Jean", jean_evidence,
            rationale="Jean and Jehan are placed in different cities at the same time -- cannot be one person",
        )],
    }
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
    assert validate_result["success"] is True
    assert validate_result["coerced"] == 0  # the citation genuinely byte-verifies

    triage_path = tmp_path / "skeptic_triage.json"
    sr.run_merge_fragments(run_dir, triage_path)
    result = sr.run_verify_merged(
        triage_path, run_dir / "assignments.json", manifest_path, particle_config, languages_dir=lang_dir,
    )
    assert result == {"verified": True, "missing": [], "frozen_input_mismatch": False}

    merged = json.loads(triage_path.read_text(encoding="utf-8"))
    rec = merged["records"][0]
    assert rec["verdict"] == "adverse"
    assert rec["evidence_coverage"] == {"cited": 1, "verified": 1}


# ---------------------------------------------------------------------------
# Case (b): one spelling, three people -> propose_split (reachability pinned)
# ---------------------------------------------------------------------------

def test_homonym_one_spelling_three_people_reaches_propose_split(tmp_path):
    """The RFC's motivating case: one Hebrew spelling denoting three
    distinct people (a nursing mother / a grandchild / an unrelated
    traveler). Reachability pinned per the approved plan's round-3
    blocker 3 -- see this module's own docstring for the three pinning
    steps. Drives the real chain to `propose_split` with 3 referents, each
    carrying byte-verified evidence (the Phase-1 senses[] minItems:2
    shape, canon-senses.schema.json:11-26)."""
    durable_root = str(tmp_path)
    lang_dir = tmp_path / "languages"
    hebrew_form = "פייגע"
    particle_config = write_particle_config(lang_dir, name_inventory=[hebrew_form])
    lang = bn.load_language_config(particle_config, languages_dir=lang_dir)
    assert hebrew_form in lang.name_inventory, "reachability step (i): the form must be in name_inventory"

    text_mother = f"{hebrew_form} nursed her infant son by the window every morning."
    text_grandchild = f"The old woman called for {hebrew_form}, her late daughter's own child, to fetch water."
    text_stranger = f"A traveler named {hebrew_form} passed through the village and was never seen again."

    block_mother_id, block_mother = block(text_mother, seg="seg01", block_id="PARA:seg01:0001")
    block_grandchild_id, block_grandchild = block(text_grandchild, seg="seg05", block_id="PARA:seg05:0001")
    block_stranger_id, block_stranger = block(text_stranger, seg="seg09", block_id="PARA:seg09:0001")
    manifest_path = tmp_path / "manifest.json"
    write_json(manifest_path, make_manifest(
        (block_mother_id, block_mother), (block_grandchild_id, block_grandchild), (block_stranger_id, block_stranger),
    ))

    ev_mother = evidence_for(hebrew_form, block_mother_id, "seg01", text_mother, lang)
    ev_grandchild = evidence_for(hebrew_form, block_grandchild_id, "seg05", text_grandchild, lang)
    ev_stranger = evidence_for(hebrew_form, block_stranger_id, "seg09", text_stranger, lang)

    # Reachability step (ii): the 3 occurrences must resolve to 3 DISTINCT
    # segs -- otherwise a --dispersion-threshold 3 trigger would be vacuous.
    occ_segs = {ev_mother["seg"], ev_grandchild["seg"], ev_stranger["seg"]}
    assert len(occ_segs) == 3, "reachability guard failed: occurrences did not resolve to 3 distinct segs"

    run_id = "acceptance-homonym-split"
    run_dir = tmp_path / "skeptic" / "runs" / run_id
    write_json(run_dir / "assignments_0.json", [aid(hebrew_form)])
    # Reachability step (iii): risk_classes records that a real scan run
    # with --dispersion-threshold 3 / --windows-per-entity>=3 would have
    # genuinely fired for this entity (3 distinct segs >= threshold 3).
    write_json(run_dir / "assignments.json", make_aggregate_manifest(run_id, [
        make_assignment_for_manifest(
            hebrew_form, [ev_mother, ev_grandchild, ev_stranger], risk_classes=("high_dispersion",),
        ),
    ]))

    batches = [{"index": 0, "assignments": [
        make_assignment_for_args(hebrew_form, [
            window_with_text(ev_mother, text_mother),
            window_with_text(ev_grandchild, text_grandchild),
            window_with_text(ev_stranger, text_stranger),
        ], risk_classes=("high_dispersion",)),
    ]}]
    dispatch_doc = {
        "schema_version": 1, "run_id": run_id,
        "records": [{
            "assignment_id": aid(hebrew_form), "source_form": hebrew_form,
            "verdict": "propose_split",
            "rationale": (
                "three incompatible referents share this one spelling: a nursing mother, "
                "an elderly woman's grandchild, and an unrelated traveler"
            ),
            "referents": [
                {"disambiguator": "the nursing mother", "evidence": ev_mother},
                {"disambiguator": "the grandchild sent for water", "evidence": ev_grandchild},
                {"disambiguator": "the unrelated traveler", "evidence": ev_stranger},
            ],
        }],
    }
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
    assert validate_result["success"] is True
    assert validate_result["coerced"] == 0  # all 3 referents genuinely byte-verify

    triage_path = tmp_path / "skeptic_triage.json"
    sr.run_merge_fragments(run_dir, triage_path)
    result = sr.run_verify_merged(
        triage_path, run_dir / "assignments.json", manifest_path, particle_config, languages_dir=lang_dir,
    )
    assert result == {"verified": True, "missing": [], "frozen_input_mismatch": False}

    merged = json.loads(triage_path.read_text(encoding="utf-8"))
    rec = merged["records"][0]
    assert rec["verdict"] == "propose_split"
    assert len(rec["referents"]) == 3
    assert rec["evidence_coverage"] == {"cited": 3, "verified": 3}
    assert {r["disambiguator"] for r in rec["referents"]} == {
        "the nursing mother", "the grandchild sent for water", "the unrelated traveler",
    }
