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
import re
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
    return (
        "async function __workflowMain__(agent, pipeline, log, args) {\n"
        + _DISPATCH_PROMPT_RECORDER
        + body
        + "\n}\n"
    )


# Round 6: a dispatch assertion has to observe that the REAL prompt builder ran
# and produced this exact string. Nothing weaker survives.
#
# Round 5 bound the assertion to the batch's `assignment_id` appearing in the
# prompt, reasoning that only batchDispatchPrompt() embeds it. Measured false:
# `assignment_id` is already sitting in `batch.assignments`, so a bypass reads
# it straight off the batch and passes it as the prompt --
# `agent(batch.assignments.map(a => a.assignment_id).join("\\n"), {label: ...})`
# satisfies the containment check with the guard deleted and no dispatch sent.
#
# Demanding more prompt TEXT (the full stringified assignments, a fixed
# preamble) buys exactly one round each time -- the mistake this loop has now
# made at four successive levels, because the decoy simply copies whatever new
# text is demanded. So the harness stops matching text and records IDENTITY.
# `batchDispatchPrompt` is a hoisted function declaration inside the wrapped
# workflow, so rebinding it here -- before any of the body runs, and without
# touching control flow -- makes every string the real builder produces
# observable.
#
# Round 7 finding (survived independent verification, live mutant in the real
# template): round 6's recorder was a flat, index-agnostic array -- it proved
# a prompt came from the real builder for SOME batch, not for the batch being
# dispatched. `batchDispatchPrompt(batch)` -> `batchDispatchPrompt(BATCHES[0])`
# at the real call site left the whole suite green: batch 1's dispatch replayed
# batch 0's recorded prompt, batch 0's own fragment path, and the flat-array
# `.indexOf` check accepted it because *some* call to the real builder had once
# produced that exact string. Round 8's fix bound identity to `batch.index`
# instead (coerced to string to match the label-derived `idx` string below),
# requiring the prompt recorded for THIS idx to equal what was actually sent.
#
# Round 9 finding (codex, HIGH; survived independent verification against the
# real template, driven through both harness copies): `batch.index` is a
# property read off the very object the call site hands to
# batchDispatchPrompt() -- forgeable the same way the round-7 array was.
# `batchDispatchPrompt({ index: batch.index, assignments: BATCHES[0]
# .assignments })` copies the real, correct index onto a fabricated object
# carrying a DIFFERENT batch's assignments: the real builder still runs (the
# recorder still fires) and records the wrong-content prompt under the
# numerically-correct key, so round 8's guard accepted it -- same index,
# wrong content, right function.
#
# The fix binds to something the call site cannot forge at all: the batch
# OBJECT ITSELF, by reference, never a property copied off it. `BATCHES`
# inside the wrapped workflow below and this harness's own `BATCHES_ARGS`
# (declared further down) are the SAME array with the SAME element
# references -- `BATCHES_ARGS` is passed as `args`, and the real template's
# own `const BATCHES = Array.isArray(args) ? args : JSON.parse(args)` keeps
# that exact reference when args is already an array (which it is here). So
# the dispatch branch below looks up "the batch idx REALLY names" from its
# own BATCHES_ARGS copy -- never from anything the call site passed -- and
# requires the recorded prompt to have come from calling
# batchDispatchPrompt() with THAT EXACT object. A forged literal can copy
# every property it wants; it can never BE that reference.
#
# What this closes: any decoy that calls the real builder (or forwards its
# result) with an object that is not, by reference, this harness's own batch
# object for the claimed index -- whether the object is wholesale fabricated
# (round 9) or genuinely a different batch (round 7). What it does NOT
# close BY ITSELF: in-place mutation of a genuine batch object's own fields
# (e.g. `BATCHES[1].assignments = BATCHES[0].assignments` before calling
# batchDispatchPrompt(BATCHES[1])) -- the reference stays the harness's own,
# so this guard alone would still accept it. That rung is closed separately,
# by removing the ability to mutate at all rather than detecting it after
# the fact -- see the recursive freeze on BATCHES_ARGS below, in
# SKEPTIC_HARNESS_TEMPLATE, for the fix and its own measurement.
_DISPATCH_PROMPT_RECORDER = """
globalThis.__realDispatchPrompts__ = new Map();
{
  const __origBatchDispatchPrompt = batchDispatchPrompt;
  batchDispatchPrompt = function (batch) {
    const built = __origBatchDispatchPrompt(batch);
    globalThis.__realDispatchPrompts__.set(batch, built);
    return built;
  };
}
"""


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
// Round 9 in-place-mutation rung (named, not yet closed, in
// _DISPATCH_PROMPT_RECORDER's header comment above): reference identity
// survives a call site that mutates a genuine batch object's own fields in
// place before making an otherwise-unmodified real call -- e.g.
// `BATCHES[1].assignments = BATCHES[0].assignments` followed by
// `batchDispatchPrompt(BATCHES[1])`. Measured directly: WITHOUT this freeze,
// that exact decoy dispatches batch 1 carrying batch 0's assignment_id and
// the round-9 guard accepts it (rc=0, merged=true; the recorded prompt
// faithfully reflects the mutated, wrong content because the object
// REFERENCE never changed).
//
// Closed here by removing the forgery rather than detecting it, the same
// category change as round 9's own fix: every value reachable from
// BATCHES_ARGS is frozen, RECURSIVELY (own enumerable properties, arrays
// and plain objects alike -- not merely each batch object's own top-level
// fields), before the wrapped workflow ever runs. `Object.freeze()` alone
// is shallow; the recursive walk below is what makes the freeze reach
// `batch.assignments[i].windows[j]` and not just `batch.assignments`
// itself.
//
// Verified SAFE to do, not just convenient: skeptic-pass-wf.template.js
// never writes to a batch, an assignment, or a window anywhere -- grepped
// for every assignment/push/splice/sort/reverse touching `batch`/
// `BATCHES`/`.assignments`/`.windows` (zero hits), and every other
// reference is a read (`batch.index`, `JSON.stringify(batch.assignments,
// ...)`, etc.). Then measured, not just read: this SAME recursive freeze
// was driven through both harness copies' FULL existing test suites --
// every precheck/dispatch/wait/wait-recheck/frozen-check/merge/verify path
// either file's tests exercise against the real template -- at commit
// 1d180e8 (round 8's tip, where this was measured), that suite was 29/29
// passed, nothing threw. That denominator is not this file's current test
// count and will drift further as later rounds add tests to either file --
// re-run `pytest tests/skeptic_pipeline_e2e.test.py tests/skeptic_
// confident_mismerge.test.py -q` for the CURRENT total rather than trusting
// the number above. The freeze mechanism itself was sanity-checked against
// a live artificial mutation (`batch.assignments = []` inside batchStep())
// to confirm it is not silently inert -- that threw, as expected, before
// this measurement was trusted.
//
// RED, against the exact named decoy above (`BATCHES[1].assignments =
// BATCHES[0].assignments`, call site otherwise untouched): with this
// freeze in place, test_e2e_batch_never_ready_short_circuits_before_merge
// fails -- but the evidence is a runtime `TypeError: Cannot assign to read
// only property 'assignments' of object '#<Object>'`, thrown at the
// MUTATION SITE itself (right after `const BATCHES = ...` in the real
// template), never reaching the dispatch guard or any of this file's own
// assertions. That is the JS engine's strict-mode enforcement of the
// freeze, not this harness's own dispatch-identity guard catching
// anything -- a different kind of evidence from every other guard in this
// file, recorded here so the next reader does not mistake one for the
// other.
//
// What this does NOT close: if some future control-flow change needs to
// legitimately write into a batch/assignment/window object, this freeze
// throws on that too and must be revisited together with that change --
// this is a snapshot of a currently-measured invariant (the template is
// read-only over its own `args`), not a permanent guarantee.
(function __freezeBatchesDeep__(o, seen) {
  seen = seen || new Set();
  if (o === null || typeof o !== "object" || seen.has(o)) return o;
  seen.add(o);
  Object.freeze(o);
  Object.getOwnPropertyNames(o).forEach(function (k) { __freezeBatchesDeep__(o[k], seen); });
  return o;
})(BATCHES_ARGS);
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
    // Round-8: same idea as schemaRequired above, for the OTHER half of a
    // schema's own enforcement -- whether it rejects an extra field outright.
    schemaAdditionalProperties: opts.schema ? opts.schema.additionalProperties : null,
    // Round-5 finding F1: a LABEL is a proxy for "this call really did the
    // work its label names", not proof of it -- this mock never inspected
    // promptText, so a decoy that fires a semantically empty agent() call
    // under the right label satisfied every label-only assertion in this
    // file. Recording the real prompt text lets a test bind its assertion
    // to content only the REAL prompt-builder (batchDispatchPrompt() etc.)
    // produces, which a decoy cannot fake without actually doing the work.
    promptText: promptText,
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
    // Round 6: the fragment must not appear for a call that did not send a
    // prompt the REAL builder produced. This mock used to write it on the
    // LABEL alone, so every on-disk assertion in this file was decoy-blind:
    // with the precheck guard deleted and two empty agent() calls carrying
    // "skeptic:dispatch:0"/"skeptic:wait:0", the fragment appeared,
    // run_merge_fragments() found its record and run_verify_merged() agreed --
    // measured. Fixed HERE rather than per-test, so no future test inherits it.
    //
    // Round 7 finding: a flat "was this prompt built by the real function at
    // all" check does not bind the prompt to THIS call's own batch index --
    // a mutant that dispatches every batch with batch 0's prompt (right
    // function, wrong batch) stayed invisible to the whole suite. Round 8
    // bound the prompt to `idx` via `batch.index`, a property read off the
    // call site's own argument.
    //
    // Round 9 finding: that property is exactly as forgeable as the round-7
    // array was -- see _DISPATCH_PROMPT_RECORDER's header comment above for
    // the mutation and the full derivation of this fix. `trustedBatch` is
    // looked up from BATCHES_ARGS (this harness's OWN copy, independent of
    // anything the call site passed), and the recorded prompt must have
    // come from calling batchDispatchPrompt() with THAT EXACT object
    // reference, not merely with something claiming its index.
    const trustedBatch = BATCHES_ARGS.find(function (b) { return String(b.index) === idx; });
    if (!trustedBatch || globalThis.__realDispatchPrompts__.get(trustedBatch) !== promptText) {
      throw new Error(
        "skeptic:dispatch:" + idx + " was called with a prompt that was not built by " +
        "calling the real batchDispatchPrompt() with this harness's own batch object " +
        "for index " + idx + " -- either the prompt was forged outright, or the real " +
        "builder was called with a different object (possibly one that copies this " +
        "index but carries a different batch's content). No fragment is written for " +
        "it: a decoy must not be able to manufacture the artifact a downstream on-disk " +
        "assertion reads. Prompt seen: " + JSON.stringify(promptText.slice(0, 120))
      );
    }
    if (p.dispatchWrite !== undefined) {
      const outPath = fragmentPathFor(idx);
      fs.mkdirSync(path.dirname(outPath), { recursive: true });
      fs.writeFileSync(outPath, JSON.stringify(p.dispatchWrite));
    }
    return "FRAGMENT " + idx;
  }
  // 1.16.2 (#352) -- the wait became WAIT_CHUNKS bounded chunk calls (all under
  // the existing `skeptic:wait:<idx>` label) plus ONE authoritative non-polling
  // re-check under its own `skeptic:wait-recheck:<idx>` label.
  //
  // `recheck` DEFAULTS TO THE SAME REPLY AS `wait`, deliberately: every
  // pre-1.16.2 fixture in this file says "a wait reply shaped like THIS must not
  // make the batch ready", and under a chunked wait that claim is only
  // observable end-to-end when the authoritative re-check answers the same way.
  // Defaulting the re-check to READY would have turned all of them green
  // through the re-check while the property under test was broken.
  if (kind === "wait-recheck") {
    if (p.recheck !== undefined) return p.recheck;
    return (p.wait !== undefined) ? p.wait : ("READY " + idx);
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
        "1": {"precheck": "ABSENT 1", "wait": "PENDING 1"},  # batch 1's fragment never becomes ready
        # (and its re-check answers PENDING too -- the harness defaults
        # `recheck` to the `wait` reply, so this is a genuine end-to-end
        # not-ready, not a chunk that the re-check then rescues)
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

    # Round 10 finding: round 9's identity guard proves batchDispatchPrompt()
    # was CALLED with this harness's own batch-1 object -- it proves nothing
    # about whether that call's RETURN VALUE actually reflects batch 1's own
    # content. A builder that ignores its argument and always reads
    # `BATCHES[0].assignments` internally still gets called with the right
    # reference (the call site is unchanged), so round 9's guard accepts it;
    # every assertion above (labels, merged, notReady) is unaffected too,
    # since none of them inspect prompt CONTENT. Measured directly: with
    # exactly that mutation in the real batchDispatchPrompt(), this whole
    # test -- and the full suite of both harness copies -- passed unchanged.
    # This is the only place in the file that dispatches two REAL batches
    # with distinct assignment_ids through the real template, so it is the
    # only place that can observe a batch's dispatch prompt carrying the
    # WRONG batch's content while still carrying the right index/label.
    dispatch_calls_by_label = {c["label"]: c for c in out["calls"] if c["label"].startswith("skeptic:dispatch:")}
    batch1_prompt = dispatch_calls_by_label["skeptic:dispatch:1"]["promptText"]
    assert aid("Marie") in batch1_prompt, (
        "batch 1's own dispatch prompt does not carry batch 1's real assignment_id -- "
        "the builder may have been called with the right reference (round 9's guard "
        "would still accept that) while still returning the wrong batch's content"
    )
    assert aid("Jean") not in batch1_prompt, (
        "batch 1's dispatch prompt carries batch 0's assignment_id -- the builder "
        "read the wrong batch's assignments despite being called with batch 1's own "
        "object reference"
    )


def test_e2e_frozen_input_mismatch_from_not_ready_batches_real_check(tmp_path):
    """codex round 2's own ask: an EXECUTING template regression where the
    sidecar becomes malformed AFTER stamping but BEFORE fragment
    validation -- asserting frozenInputMismatch:true rather than
    fragment-check-failed. Unlike test_e2e_frozen_input_mismatch_surfaces_
    distinct_signal (which drives the EXISTING verify-merged path via a
    canned PLAN.verify), this batch NEVER becomes ready at all
    (precheck=ABSENT, wait=PENDING, mirroring
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
        "0": {"precheck": "ABSENT 0", "wait": "PENDING 0"},  # never becomes ready
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
    plan = {"0": {"precheck": "ABSENT 0", "wait": "PENDING 0"}}

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
    """The (2 + WAIT_CALLS)*N + 2 preflight formula, which skeptic_setup.py's
    own step-5 preflight must agree with call-for-call -- leave one behind and
    one of them refuses a batch the other admits, after the setup script has
    already written this run's manifests.

    1.16.2 (#352): the per-batch term went 3 -> 5, and NOT because the pass does
    more work. One wait stopped being one agent call: the Bash tool clamps a
    single call at 600 s, so the 900 s wait is now WAIT_CHUNKS bounded chunks
    plus one authoritative non-polling re-check == WAIT_CALLS == 3 calls worst
    case. This charges the worst case, as a preflight must.

    Run ONE below the estimate, so it pins the boundary: a cap comfortably under
    it would keep refusing through any estimator change, including one that
    under-counts -- and under-counting is the dangerous direction here, since it
    admits a run that then blows engine.batch_agent_cap mid-flight."""
    durable_root = str(tmp_path)
    lang_dir = tmp_path / "languages"
    particle_config = write_particle_config(lang_dir)
    run_id = "e2e-run-toobig"
    batches = [
        {"index": 0, "assignments": [make_assignment_for_args("Jean", [])]},
        {"index": 1, "assignments": [make_assignment_for_args("Marie", [])]},
    ]
    # precheck 1 + dispatch 1 + wait (2 chunks + 1 re-check) 3 == 5 per batch,
    # plus the fixed merge + verify pair.
    estimated = 5 * len(batches) + 2  # 12

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
    `ready.indexOf("READY") === -1` check falsely treated a not-ready reply
    that merely contains the literal substring "READY" inside its own
    explanatory prose (e.g. "PENDING 0 (not READY)") as ready -- `indexOf`
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
            "wait": "PENDING 0 (not READY)",
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


def test_e2e_precheck_glued_absent_still_regenerates(tmp_path):
    """Round-4 codex finding (C1) against
    tests/rejected_anywhere_parity.test.py's behavioral gate: that gate
    extracts a `!rejectedAnywhere(...) && sentinelVerdict(...)` expression by
    matching argument TEXT, and never proves the extracted snippet is the one
    that actually controls the live branch. Codex's mutation: alias
    `sentinelVerdict`, park the correctly-guarded expression in an unused
    `const`, and make the real branch call the alias instead -- the
    extraction-based gate still finds one guard, one verdict, sees them
    `&&`-paired, and passes, while the LIVE branch resume-skips a reply it
    must reject.

    This part of the test cannot be fooled by any such decoy, at any level:
    it does not extract or select a snippet at all. It instantiates the REAL
    skeptic-pass-wf.template.js (unmodified control flow) and drives its
    REAL, live `batchStep()` under node via the mocked `agent()` -- whichever
    expression actually executes IS the one under test, because there is
    nothing else it could be.

    Reply shape: the sibling test just above
    (test_e2e_precheck_fail_priority_discriminating_order) already covers
    ABSENT alone on its own LF-delimited line, which sentinelVerdict()'s own
    fail-priority scan catches unguarded. This test covers what that one does
    NOT: ABSENT GLUED to prose by a non-newline character (so the fail-
    priority scan alone would miss it), with a clean trailing PRESENT line --
    the exact shape the rejectedAnywhere() containment guard exists to
    reject. Must dispatch (NOT resume-skip) and still merge once dispatch and
    wait succeed -- mirrors the sibling test's own shape/assertions exactly,
    changing only the precheck reply.

    Round-5 finding F1: even this live-batchStep test was not decoy-proof.
    The mock `agent()` never inspected `promptText`, only `opts.label` -- so a
    mutation that removes the real containment guard (making this precheck
    resume-skip, exactly the defect above) AND inserts two semantically empty
    `agent()` calls carrying the labels "skeptic:dispatch:0"/"skeptic:wait:0"
    satisfied every assertion this test used to make: both labels present,
    `merged: true` from the still-canned "skeptic:verify" mock. No real
    dispatch prompt ever ran and no fragment was ever written. The two blocks
    below close that: the dispatch call's own PROMPT TEXT must carry this
    batch's real assignment_id (something only batchDispatchPrompt(batch)
    itself embeds -- see the template's own `JSON.stringify(batch.assignments,
    ...)` line -- a decoy prompt string cannot contain it without actually
    calling that function), and the merge/verify outcome must be independently
    re-derived by REAL Python straight off disk, never trusted from the
    mock's own canned return value (the same discipline
    test_e2e_full_pipeline_happy_path_real_merge_and_verify uses)."""
    durable_root = str(tmp_path)
    lang_dir = tmp_path / "languages"
    particle_config = write_particle_config(lang_dir)
    lang = bn.load_language_config(particle_config, languages_dir=lang_dir)
    text = "Jean walked home alone."
    block_id, blk = block(text)
    manifest_path = tmp_path / "manifest.json"
    write_json(manifest_path, make_manifest((block_id, blk)))
    jean_evidence = evidence_for("Jean", block_id, "seg01", text, lang)

    run_id = "e2e-run-precheck-glued-absent"
    run_dir = tmp_path / "skeptic" / "runs" / run_id
    write_json(run_dir / "assignments_0.json", [aid("Jean")])
    write_json(run_dir / "assignments.json", make_aggregate_manifest(
        run_id, [make_assignment_for_manifest("Jean", [jean_evidence])],
    ))

    batches = [{"index": 0, "assignments": [make_assignment_for_args("Jean", [window_with_text(jean_evidence, text)])]}]
    dispatch_doc = {"schema_version": 1, "run_id": run_id, "records": [adverse_record("Jean", jean_evidence)]}
    plan = {"0": {
        "precheck": "the chunk was cut short ABSENT 0\nPRESENT 0",
        "dispatchWrite": dispatch_doc, "wait": "READY 0",
    }}

    out = run_skeptic_workflow(
        tmp_path=tmp_path, durable_root=durable_root, particle_config=particle_config,
        run_id=run_id, batch_agent_cap=10_000, batches=batches, plan=plan,
    )
    labels = [c["label"] for c in out["calls"]]
    assert "skeptic:dispatch:0" in labels, (
        "a precheck reply with ABSENT glued to prose (not on its own line) "
        "must still dispatch -- resume-skipping here is the actual defect "
        "the containment guard exists to close"
    )
    assert "skeptic:wait:0" in labels
    assert out["result"]["merged"] is True

    # F1 hardening 1/2: the dispatch call's own prompt content, not merely its
    # label. A label-only decoy call (see the docstring above) cannot make
    # this pass, because it never calls batchDispatchPrompt(batch) and so
    # never embeds this batch's real assignment_id anywhere in its text.
    dispatch_calls = [c for c in out["calls"] if c["label"] == "skeptic:dispatch:0"]
    assert len(dispatch_calls) == 1, f"expected exactly one dispatch call, got {len(dispatch_calls)}"
    assert aid("Jean") in dispatch_calls[0]["promptText"], (
        "the dispatch call's own prompt text does not carry this batch's real "
        "assignment_id -- a decoy that logs the right LABEL without actually "
        "calling batchDispatchPrompt(batch) would satisfy every label-only "
        "assertion above while never sending real work"
    )

    # F1 hardening 2/2: the SIDE EFFECT on disk, verified by REAL Python --
    # never trusting the mock's own canned "skeptic:merge"/"skeptic:verify"
    # return values, exactly as the happy-path test above does.
    triage_path = tmp_path / "skeptic_triage.json"
    merge_result = sr.run_merge_fragments(run_dir, triage_path)
    assert merge_result["records"] == 1
    verify_result = sr.run_verify_merged(
        triage_path, run_dir / "assignments.json", manifest_path, particle_config, languages_dir=lang_dir,
    )
    assert verify_result == {"verified": True, "missing": [], "frozen_input_mismatch": False}


def test_e2e_wait_fail_priority_discriminating_order(tmp_path):
    """Same discriminating-order proof at site B': PENDING before a
    trailing READY line must still be read as not-ready."""
    durable_root = str(tmp_path)
    lang_dir = tmp_path / "languages"
    particle_config = write_particle_config(lang_dir)
    run_id = "e2e-run-wait-discriminating"
    batches = [{"index": 0, "assignments": [make_assignment_for_args("Jean", [])]}]
    plan = {
        "0": {"precheck": "ABSENT 0", "wait": "PENDING 0\nREADY 0"},
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


def test_e2e_wait_glued_pending_with_trailing_ready_still_not_ready(tmp_path):
    """Round-5 finding F4: the precheck site's own gluing defect (see
    test_e2e_precheck_glued_absent_still_regenerates just above) has a
    live-batchStep e2e test.

    Round 8 correction (an outside reviewer caught this): this docstring
    used to claim the WAIT site's identical defect had NO live e2e coverage
    at all until this test. That overstates it -- the round-6 correction
    paragraph just below already measured, and this round re-measured
    independently, that deleting rejectedAnywhere() from waitChunkVerdict()
    entirely fails test_e2e_wait_fail_priority_discriminating_order too, not
    only this test: that pre-existing test already drives the guard live,
    end-to-end, through the real template. What it does NOT cover is the
    GLUED shape: its own PENDING sentinel already sits alone on its own
    LF-delimited line, so a narrower mutant that reads PENDING by
    whole-line equality instead of containment still passes it while
    failing only this test (measured directly; see the round-6 correction
    below for the same discrimination in more detail). That narrower gap --
    not "any coverage at all" -- is what this test actually closes.

    Every existing wait-site e2e test above drives a DIFFERENT shape:
    test_e2e_wait_substring_collision_reports_not_ready is the #227
    substring-inside-prose bug, and test_e2e_wait_fail_priority_discriminating_
    order puts PENDING alone on its own LF-delimited line before a trailing
    READY.

    Round 6 correction (an outside reviewer caught this): an earlier revision
    of this docstring claimed that sibling shape was rejected by
    "sentinelVerdict()'s own fail-priority scan, unguarded" -- reasoning by
    analogy from the precheck site above, where sentinelVerdict()'s
    fail-priority scan genuinely is a second, independent guard (its
    failSentinel there is "ABSENT "+batch.index, non-null). Measured false
    for the wait site: waitChunkVerdict() calls
    sentinelVerdict(reply, "READY "+index, null) with failSentinel literally
    null, and that function's own scan is gated on `failSentinel !== null`,
    so it never fires here. rejectedAnywhere() is the ONLY fail-direction
    guard at this site, and it is the SAME guard this test exercises:
    deleting it from waitChunkVerdict() fails BOTH this test and
    test_e2e_wait_fail_priority_discriminating_order (measured directly).
    What THIS test adds is narrower than "an independently guarded shape" --
    it pins that the guard is CONTAINMENT, not whole-line exact match.
    Replacing rejectedAnywhere() with a scan that only rejects a PENDING
    sentinel occupying its own whole line still passes the sibling test
    (its shape already IS a whole line) but fails this one (a glued PENDING
    never occupies a whole line) -- measured directly. The sibling test
    alone cannot tell those two possible implementations apart; this one
    can, which is the actual, narrower reason it earns its place here.

    Neither of the two prior tests drives a PENDING sentinel GLUED to prose
    by a non-newline character with a trailing clean READY line -- the exact
    shape tests/rejected_anywhere_parity.test.py's own PARITY_REPLY_SHAPES
    calls "glued_pending_space", and the shape rejectedAnywhere()'s
    containment guard inside waitChunkVerdict() exists to reject. That
    parity file's test_all_three_wait_verdicts_agree_on_every_reply_shape
    already covers this reply SHAPE, but only by extracting
    waitChunkVerdict()'s own
    DEFINITION and calling it directly -- exactly the extraction-based
    coverage that let a decoy call-site walk around the precheck guard
    (round-4 codex finding C1) go unnoticed until an e2e test drove the real
    control flow. This test closes the same gap at the wait site: it
    instantiates the REAL template and drives its REAL, live batchStep()
    wait loop under node via the mocked agent(), so whichever expression
    actually decides readiness IS the one under test.

    No promptText/disk hardening is needed here the way F1 needed it for the
    precheck site: removing this guard does not merely let a decoy fake a
    label, it flips the OBSERVABLE decision itself -- a glued PENDING
    misread as ready would proceed straight into the real merge/verify calls
    (out["result"]["merged"] would read True from the still-canned
    "skeptic:verify" mock, and "skeptic:merge"/"skeptic:verify" would appear
    in labels), which the assertions below already catch without needing a
    label-count proxy. (Round 6 -- codex: an earlier revision of this test
    also asserted `not (tmp_path / "skeptic_triage.json").exists()` as a
    supposed extra disk-level guard. Measured vacuous: this test's plan
    carries no `dispatchWrite`, so `run_dir` is never even created on disk
    here, and the only thing in this whole file that ever writes
    `tmp_path/skeptic_triage.json` is the explicit `sr.run_merge_fragments`
    Python call other tests make -- this test never makes it. The mock's own
    "skeptic:merge" branch is canned (`return "MERGED (mock)"`) and never
    touches disk either way, so there is no "writes merge output some other
    way" path for a disk check to catch that the `labels` assertions above
    do not already catch. Removed rather than kept as false insurance.)"""
    durable_root = str(tmp_path)
    lang_dir = tmp_path / "languages"
    particle_config = write_particle_config(lang_dir)
    run_id = "e2e-run-wait-glued-pending"
    batches = [{"index": 0, "assignments": [make_assignment_for_args("Jean", [])]}]
    plan = {
        "0": {
            "precheck": "ABSENT 0",
            "wait": "the chunk was cut short PENDING 0\nREADY 0",
        },
        # This test is about the wait-site containment guard, not the
        # frozen-input check -- canned clean (mirrors the sibling tests
        # above).
        "frozenCheck": {"frozen_input_mismatch": False},
    }

    out = run_skeptic_workflow(
        tmp_path=tmp_path, durable_root=durable_root, particle_config=particle_config,
        run_id=run_id, batch_agent_cap=10_000, batches=batches, plan=plan,
    )
    assert out["result"]["merged"] is False, (
        "a PENDING sentinel glued to prose by a non-newline character, with a "
        "trailing clean READY line, must never be read as ready -- the exact "
        "defect rejectedAnywhere()'s containment guard inside "
        "waitChunkVerdict() exists to close at this site"
    )
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


# Round-8 sweep finding: skeptic's verifyMergedPrompt() is the same shape as
# glossary's glossaryVerifyPrompt() (a disk-independent, "never trusting the
# merge call above's own claim" step), and its own "do not judge the
# comparison yourself" / "Do not add, omit, or alter any value the command
# printed" clauses were equally unpinned. See
# tests/glossary_snapshot_ordering.test.py's
# test_verify_prompt_forbids_judging_or_altering_the_command_result for the
# sibling pin and its own two-strength explanation (presence-only for
# "judge"/"omit"/"alter", structural for "add" via additionalProperties).
SKEPTIC_VERIFY_NO_JUDGE_CLAUSE = "do not judge the comparison yourself"
SKEPTIC_VERIFY_NO_ALTER_CLAUSE = (
    "Do not add, omit, or alter any value the command printed"
)


def test_e2e_verify_prompt_forbids_judging_or_altering_the_command_result(tmp_path):
    """Same property as the glossary sibling test, over the real
    skeptic-pass-wf.template.js. PRESENCE-ONLY for the "judge"/"omit"/"alter"
    half (this mocked harness cannot simulate an LLM being talked into or
    resisting an embedded instruction -- it only proves the sentence is still
    in the rendered prompt). STRUCTURAL for the "add" half: SKEPTIC_VERIFY_
    SCHEMA's own additionalProperties must be false, which is real Workflow-
    engine-enforced regardless of what the prompt says."""
    durable_root = str(tmp_path)
    lang_dir = tmp_path / "languages"
    particle_config = write_particle_config(lang_dir)
    lang = bn.load_language_config(particle_config, languages_dir=lang_dir)
    text = "Jean walked home alone."
    block_id, blk = block(text)
    manifest_path = tmp_path / "manifest.json"
    write_json(manifest_path, make_manifest((block_id, blk)))
    jean_evidence = evidence_for("Jean", block_id, "seg01", text, lang)

    run_id = "e2e-run-verify-prompt-forbids"
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
    assert len(verify_calls) == 1, (
        f"expected exactly one skeptic:verify call, got {len(verify_calls)}"
    )
    verify_prompt = verify_calls[0]["promptText"]

    assert SKEPTIC_VERIFY_NO_JUDGE_CLAUSE in verify_prompt, (
        "the verify prompt must tell the agent not to judge the comparison "
        f"itself; prompt was:\n{verify_prompt}"
    )
    assert SKEPTIC_VERIFY_NO_ALTER_CLAUSE in verify_prompt, (
        "the verify prompt must forbid adding, omitting, or altering any "
        f"value the command printed; prompt was:\n{verify_prompt}"
    )
    assert verify_calls[0]["hasSchema"] is True
    assert verify_calls[0]["schemaAdditionalProperties"] is False, (
        "SKEPTIC_VERIFY_SCHEMA must set additionalProperties: false -- the "
        "code-level enforcement of the 'do not add' half, independent of "
        f"whether the prompt sentence survives; got "
        f"{verify_calls[0]['schemaAdditionalProperties']!r}"
    )


def test_e2e_verify_result_trust_rests_on_shape_alone_not_independent_corroboration(tmp_path):
    """The STRONG form of the property the test above can only pin weakly.
    Same shape as glossary_snapshot_ordering.test.py's sibling test of the
    same name (see that docstring for the full argument) -- over skeptic's
    own isVerifiedResult(), which its own comment calls "IDENTICAL to
    glossary-pass-wf.template.js's own isVerifiedResult()".

    1. SOURCE-STRUCTURAL: isVerifiedResult() is read directly out of the real
       template and asserted to contain no subprocess call, no second
       agent() call, and no reference to skeptic_ready.py -- a pure
       shape/value check over the reply object, nothing else.
    2. BEHAVIOURAL: a mocked "skeptic:verify" reply that never invokes any
       subprocess (this file's own canned `{ verified: true }` default) still
       makes the run report merged:true.
    """
    template_source = SKEPTIC_PASS_TEMPLATE.read_text(encoding="utf-8")
    m = re.search(r"function isVerifiedResult\(v\) \{(.*?)\n\}", template_source, re.DOTALL)
    assert m, (
        "isVerifiedResult() not found in skeptic-pass-wf.template.js -- has "
        "it been renamed or restructured? This test's whole premise is that "
        "function's own body."
    )
    body = m.group(1)
    for marker in ("execFileSync", "spawnSync", "require(", "subprocess", "agent(", "skeptic_ready"):
        assert marker not in body, (
            f"isVerifiedResult() now contains {marker!r} -- it used to be a "
            "pure shape/value check over the reply object with no "
            "independent corroboration of anything; if that changed on "
            "purpose, this assertion needs to be revisited, not silenced. "
            f"Body was:\n{body}"
        )

    durable_root = str(tmp_path)
    lang_dir = tmp_path / "languages"
    particle_config = write_particle_config(lang_dir)
    lang = bn.load_language_config(particle_config, languages_dir=lang_dir)
    text = "Jean walked home alone."
    block_id, blk = block(text)
    manifest_path = tmp_path / "manifest.json"
    write_json(manifest_path, make_manifest((block_id, blk)))
    jean_evidence = evidence_for("Jean", block_id, "seg01", text, lang)

    run_id = "e2e-run-verify-trust-shape-alone"
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
    assert out["result"]["merged"] is True, (
        "a schema-valid, canned verify reply that never invoked the real "
        "skeptic_ready.py --verify-merged command is still trusted -- "
        f"confirms there is no independent corroboration step; result was "
        f"{out['result']}"
    )


# Round-8 sweep finding: PRECHECK and WAIT (chunk and re-check alike) are each
# told "do nothing else" beyond their one read-only check -- unpinned, the
# same shape as the glossary sibling. PRESENCE-ONLY: this mocked agent()
# cannot simulate an LLM doing something extra with its bash tool, so this
# proves the instruction is still WRITTEN, not that it is OBEYED.
SKEPTIC_PRECHECK_NOTHING_ELSE_CLAUSE = (
    "do not create, dispatch, or resolve any entity yourself"
)
SKEPTIC_WAIT_NOTHING_ELSE_CLAUSE = (
    "do not touch any files, and do not resolve any entity yourself"
)


def test_e2e_precheck_and_wait_are_told_to_do_nothing_beyond_their_own_check(tmp_path):
    """No agent() call in any of this plugin's templates carries a tool-
    restriction option (confirmed in the round-8 sweep), so for a precheck or
    wait step that is supposed to be mechanical and read-only, the prompt's
    own "do nothing else" sentence is the only thing standing between "ran
    the one suggested command" and "did whatever else its bash tool allows".

    Two runs: the first (default plan) reaches precheck and the wait chunk;
    the second forces `wait: "PENDING 0"` (the same shape
    test_e2e_not_ready_batches_without_tamper_still_reports_ordinary_failure
    uses) so the chunk budget exhausts and the re-check -- which defaults to
    the SAME reply as the chunk when no `recheck` override is given, see this
    file's own mock agent() comment -- actually fires."""
    durable_root = str(tmp_path)
    lang_dir = tmp_path / "languages"
    particle_config = write_particle_config(lang_dir)
    lang = bn.load_language_config(particle_config, languages_dir=lang_dir)
    text = "Jean walked home alone."
    block_id, blk = block(text)
    manifest_path = tmp_path / "manifest.json"
    write_json(manifest_path, make_manifest((block_id, blk)))
    jean_evidence = evidence_for("Jean", block_id, "seg01", text, lang)

    run_id = "e2e-run-precheck-wait-nothing-else"
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
    precheck_calls = [c for c in out["calls"] if c["label"] == "skeptic:precheck:0"]
    assert len(precheck_calls) == 1
    assert SKEPTIC_PRECHECK_NOTHING_ELSE_CLAUSE in precheck_calls[0]["promptText"], (
        "the precheck prompt must forbid the agent from doing anything "
        f"beyond its one read-only check; prompt was:\n{precheck_calls[0]['promptText']}"
    )
    wait_calls = [c for c in out["calls"] if c["label"] == "skeptic:wait:0"]
    assert wait_calls, "expected at least one skeptic:wait:0 call"
    for c in wait_calls:
        assert SKEPTIC_WAIT_NOTHING_ELSE_CLAUSE in c["promptText"], (
            "every wait chunk prompt must forbid the agent from touching "
            f"files or resolving entities itself; prompt was:\n{c['promptText']}"
        )

    # The re-check only fires once every wait chunk stays PENDING, which is
    # also the branch that runs skeptic_ready.py --check-frozen-inputs for
    # REAL (see this file's mock agent() comment on "skeptic:frozen-check"):
    # the scripts must actually be staged under durable_root for that
    # subprocess call to find them.
    stage_skeptic_ready_scripts(tmp_path)
    canon_path = tmp_path / "canon.json"
    canon_path.write_text(json.dumps({"entries": {}}), encoding="utf-8")
    senses_path = tmp_path / "canon_senses.json"
    senses_path.write_text(
        json.dumps({"schema_version": 1, "entries_by_source_form": {}}), encoding="utf-8"
    )
    recheck_run_id = "e2e-run-precheck-wait-nothing-else-recheck"
    recheck_run_dir = tmp_path / "skeptic" / "runs" / recheck_run_id
    write_json(recheck_run_dir / "assignments_0.json", [aid("Jean")])
    write_json(recheck_run_dir / "assignments.json", {
        **make_aggregate_manifest(recheck_run_id, [make_assignment_for_manifest("Jean", [])]),
        "canon_sha256": suspicion_scan.compute_frozen_input_hash(canon_path),
        "manifest_sha256": suspicion_scan.compute_frozen_input_hash(manifest_path),
        "senses_sha256": suspicion_scan.compute_frozen_input_hash(senses_path),
    })
    recheck_batches = [{"index": 0, "assignments": [make_assignment_for_args("Jean", [])]}]
    recheck_plan = {"0": {"precheck": "ABSENT 0", "wait": "PENDING 0"}}
    recheck_out = run_skeptic_workflow(
        tmp_path=tmp_path, durable_root=durable_root, particle_config=particle_config,
        run_id=recheck_run_id, batch_agent_cap=10_000, batches=recheck_batches, plan=recheck_plan,
    )
    recheck_calls = [c for c in recheck_out["calls"] if c["label"] == "skeptic:wait-recheck:0"]
    assert recheck_calls, (
        "expected the wait re-check to fire when every chunk stays PENDING; "
        f"calls were {[c['label'] for c in recheck_out['calls']]}"
    )
    for c in recheck_calls:
        assert SKEPTIC_WAIT_NOTHING_ELSE_CLAUSE in c["promptText"], (
            "the wait re-check prompt must forbid the agent from touching "
            f"files or resolving entities itself; prompt was:\n{c['promptText']}"
        )


# Round-8 sweep item 2 (own-ranked list): the dispatch prompt's own STOP
# instruction over skeptic_ready.py --validate-fragment's self-check loop.
#
# WHY IT IS STILL PINNED, AND WHY ITS SEVERITY ARGUMENT NO LONGER HOLDS.
# skeptic_ready.py --validate-fragment is NOT read-only by the script's own
# design: a successful run REWRITES the fragment in place (dropping unverified
# propose_split referents, coercing a record to insufficient_window). When this
# pin was written that rewrite was also NON-IDEMPOTENT, so a second invocation
# over an already-normalized fragment silently corrupted the fragment's own
# record of how many citations were originally offered, and this one prompt
# sentence was the only thing standing between the dispatch agent's self-check
# retry loop and that corruption. #368 removed the corruption itself:
# `evidence_coverage.cited` is now monotone, so re-running the command
# reproduces the previous values rather than recounting a pruned list. The
# sentence stays pinned as ECONOMY -- an extra invocation spends an agent call
# from the run's bounded budget -- not as a data-integrity defence. The
# companion clause that stated the old rationale ("it is not idempotent") was
# deleted with the defect rather than re-pointed at the new one: pinning prose
# that is no longer load-bearing would only duplicate this pin.
#
# PRESENCE-ONLY, and there is no stronger form available here. The
# glossary/skeptic VERIFY pins above can go structural because the
# ORCHESTRATING JS makes the trust decision (isVerifiedResult() et al.), so
# this harness can observe it. Here the decision being guarded is "how many
# times does the DISPATCHED CODEX SUBPROCESS itself invoke this command inside
# its own self-check retry loop" -- entirely internal to that subprocess,
# never surfaced to the orchestrating JS this harness can drive or observe.
# Nothing in this file's mock agent() simulates codex's own bash-tool retry
# loop (it returns a canned FRAGMENT/dispatchWrite reply directly), so no
# structural assertion here could prove compliance even in principle. That is
# a limitation of what this harness can prove, not a claim that the property
# is unimportant -- it is this round's single highest-severity finding by the
# ranking already reported.
SKEPTIC_DISPATCH_STOP_AT_FIRST_SUCCESS_CLAUSE = (
    "STOP at the FIRST \"success\": true and do not run the command again "
    "after that"
)


def test_e2e_dispatch_prompt_forbids_rerunning_the_self_check_after_success(tmp_path):
    """See the module-level comment just above this test for what this pin is
    for since #368 (an agent-budget instruction, no longer a data-integrity
    defence) and for why it cannot be made behavioural with this harness."""
    durable_root = str(tmp_path)
    lang_dir = tmp_path / "languages"
    particle_config = write_particle_config(lang_dir)
    lang = bn.load_language_config(particle_config, languages_dir=lang_dir)
    text = "Jean walked home alone."
    block_id, blk = block(text)
    manifest_path = tmp_path / "manifest.json"
    write_json(manifest_path, make_manifest((block_id, blk)))
    jean_evidence = evidence_for("Jean", block_id, "seg01", text, lang)

    run_id = "e2e-run-dispatch-stop-at-first-success"
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
    dispatch_calls = [c for c in out["calls"] if c["label"] == "skeptic:dispatch:0"]
    assert len(dispatch_calls) == 1, (
        f"expected exactly one skeptic:dispatch:0 call, got {len(dispatch_calls)}"
    )
    dispatch_prompt = dispatch_calls[0]["promptText"]

    assert SKEPTIC_DISPATCH_STOP_AT_FIRST_SUCCESS_CLAUSE in dispatch_prompt, (
        "the dispatch prompt must tell codex to stop re-running the self-"
        "check once it has already succeeded once; prompt was:\n"
        f"{dispatch_prompt}"
    )

    # #109 -- the background routing control, asserted on the prompt this run
    # EMITTED rather than on the source that builds it.
    # tests/bounded_poll_present.test.py pins this line by shape, in the
    # template SOURCE, for every codex dispatch this plugin ships -- a claim
    # about text. This one closes the gap to the wire, so a refactor keeping
    # the push but no longer RENDERING it first fails here. The dispatch prompt
    # is already captured above, so this rides an existing run rather than
    # paying for another.
    first_line = dispatch_prompt.split("\n")[0]
    assert first_line == "--background", (
        "the codex dispatch prompt's FIRST rendered line must be the bare "
        "routing control --background, so the codex:codex-rescue forwarder is "
        "given an explicit choice instead of picking foreground by its own "
        "heuristic and running the codex turn inside its single Bash call "
        "(#109). First line was instead: " + repr(first_line)
    )


# ---------------------------------------------------------------------------
# Round 6 -- parity pin for the dispatch-write identity guard.
#
# This file's own mock agent() (see _DISPATCH_PROMPT_RECORDER and the
# "dispatch" branch's own comment above) and tests/skeptic_confident_mismerge
# .test.py's INDEPENDENT copy of the same mock both got the identity-recording
# fix this round: a "dispatch" call only writes a fragment for a promptText
# that the REAL batchDispatchPrompt() actually produced. The fix landed in
# both copies by hand, one at a time -- which is exactly how today's earlier
# defect happened: round 5 hardened this file's own copy against a decoy and
# left the sibling copy unguarded, and nothing caught it until a human audit.
# Nothing else in this suite would have caught THAT: each file's own tests
# pass against its own copy, so a guard present in one and missing from the
# other is invisible to both.
#
# What this test DOES establish: the two copies, as they exist RIGHT NOW,
# agree on the guard's behaviour for a forged dispatch input and for a real
# one. What it does NOT establish: that a single shared implementation
# exists (there still are two, hand-maintained), or that some future THIRD
# copy would be caught by anything (nothing generalizes this pairwise pin to
# an unknown copy count). Extracting one shared harness module that both
# files import would remove the drift vector at its source instead of
# detecting it after the fact, and is the stronger fix. That extraction was
# considered this round and deferred -- not because the reasoning is wrong,
# but because this is round six of a loop where every round's fix has
# contained the next round's defect, and a structural refactor of a harness
# both e2e files depend on deserves its own review round rather than riding
# along at the end of this one. It has NOT been filed anywhere; this
# paragraph is the only record of the decision until someone acts on it.
#
# BEHAVIOURAL, not textual, per tests/rejected_anywhere_parity.test.py's own
# round-2 lesson (recorded in that file's docstring): a prior version of that
# guard's parity check asserted two copies were byte-identical, which proved
# nothing about whether either copy's CALL SITE actually used it. So this
# test does not diff source text at all. It drives one shared, hand-built
# fixture -- deliberately NOT the real skeptic-pass-wf.template.js, so this
# test exercises exactly the dispatch-write guard and nothing else -- through
# each file's own, real, unmodified build_skeptic_harness()/mock agent(),
# with one genuine dispatch (must be accepted) and one forged one that reads
# real assignment_ids straight off the batch without ever calling the real
# batchDispatchPrompt() (the same "bare assignment ids" shape
# probe_battery.py's own decoy battery used against this round's Task 1 fix;
# see the identity-recording rationale above _DISPATCH_PROMPT_RECORDER).
#
# Proved red/green before trusting it: with the guard's `throw` block deleted
# from one copy's dispatch branch (mutant asserted live), this test fails for
# that copy and passes for the other; restoring the block, both pass again.
# ---------------------------------------------------------------------------

_MISMERGE_TEST_FILE = Path(__file__).resolve().parent / "skeptic_confident_mismerge.test.py"
assert _MISMERGE_TEST_FILE.is_file(), (
    f"sibling harness copy not found at {_MISMERGE_TEST_FILE} -- this parity "
    "pin has nothing to compare against"
)


def _load_sibling_mismerge_module_for_dispatch_guard_parity():
    # Dynamic load, not a normal import: this project's test files are each
    # self-contained (see this file's own header comment on the convention),
    # and the whole point of this pin is to exercise the SIBLING FILE'S OWN
    # shipped build_skeptic_harness(), never a reimplementation of it -- a
    # third, hand-copied version of the harness logic here would just be a
    # third thing that can drift.
    return _load_module(
        "skeptic_confident_mismerge_for_dispatch_guard_parity_pin", _MISMERGE_TEST_FILE, SCRIPTS_DIR,
    )


# A minimal, self-contained workflow body. Deliberately NOT the real
# skeptic-pass-wf.template.js -- it needs no precheck/wait control flow at
# all, only a hoisted batchDispatchPrompt() (so _DISPATCH_PROMPT_RECORDER's
# rebind has something to rebind) and two "skeptic:dispatch:0" calls.
_DISPATCH_GUARD_PARITY_FIXTURE = r"""
export const meta = { version: "dispatch-guard-parity-pin-fixture" };

function batchDispatchPrompt(batch) {
  return "REAL PROMPT for " + batch.assignments.map(a => a.assignment_id).join(",");
}

const batch = args[0];
const realReply = await agent(batchDispatchPrompt(batch), {
  phase: "SkepticPass", label: "skeptic:dispatch:0",
});

let forgedRejected = false;
let forgedErrorMessage = null;
try {
  await agent(
    "FORGED " + batch.assignments.map(a => a.assignment_id).join(","),
    { phase: "SkepticPass", label: "skeptic:dispatch:0" },
  );
} catch (err) {
  forgedRejected = true;
  forgedErrorMessage = String(err && err.message || err);
}

return { realReply: realReply, forgedRejected: forgedRejected, forgedErrorMessage: forgedErrorMessage };
"""


def _run_dispatch_guard_parity_fixture(build_harness_fn, tmp_path: Path, label: str) -> dict:
    """Drive _DISPATCH_GUARD_PARITY_FIXTURE through ONE file's own
    build_skeptic_harness() -- the same fixture body and batch for both
    files, so this is one input driven through two real harnesses, not two
    separately-reasoned-about scenarios."""
    assert NODE is not None, "node executable not found on PATH"
    batches = [{"index": 0, "assignments": [{
        "assignment_id": aid("Jean"), "source_form": "Jean", "canonical_target_form": "Jean",
        "risk_classes": ["high_dispersion"], "windows_truncated": False, "windows": [],
    }]}]
    harness_text = build_harness_fn(_DISPATCH_GUARD_PARITY_FIXTURE, batches, {}, str(tmp_path), "parity-pin-run")
    harness_path = tmp_path / f"dispatch_guard_parity_{label}.js"
    harness_path.write_text(harness_text, encoding="utf-8")
    proc = subprocess.run([NODE, str(harness_path)], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, (
        f"{label}'s dispatch-guard parity harness process itself failed (exit "
        f"{proc.returncode}) -- this means the REAL dispatch call was rejected "
        f"too (the fixture only catches the FORGED one in its own try/catch), "
        f"not just the forged one:\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
    )
    return json.loads(proc.stdout)["result"]


def test_dispatch_write_guard_agrees_between_the_two_harness_copies(tmp_path):
    """The parity pin itself. See the section comment above for what this
    does and does not establish, and why the stronger fix (one shared
    harness module) is deferred rather than done here."""
    results = {
        "skeptic_pipeline_e2e.test.py": _run_dispatch_guard_parity_fixture(
            build_skeptic_harness, tmp_path, "e2e",
        ),
        "skeptic_confident_mismerge.test.py": _run_dispatch_guard_parity_fixture(
            _load_sibling_mismerge_module_for_dispatch_guard_parity().build_skeptic_harness,
            tmp_path, "mismerge",
        ),
    }
    for label, result in results.items():
        assert result["forgedRejected"] is True, (
            f"{label}'s dispatch-write identity guard ACCEPTED a forged prompt "
            f"that batchDispatchPrompt() never produced -- the two copies have "
            f"drifted, and this is the weaker one. Result: {result}"
        )
        assert result["realReply"] == "FRAGMENT 0", (
            f"{label}'s dispatch-write identity guard REJECTED a real, "
            f"faithfully-produced prompt -- over-broad, not just under-broad. "
            f"Result: {result}"
        )


# Round 8: the parity pin above drives a SINGLE batch, so it cannot see the
# round-7 finding -- a decoy that dispatches under one batch's label while
# replaying a DIFFERENT batch's real, builder-produced prompt (right
# function, wrong index). Both copies' original recorders were a flat
# array/`.indexOf()`, which only proves the builder produced a string for
# SOME batch, never for the one being dispatched; the single-batch fixture
# above has no second batch to replay FROM, so it stayed green through that
# exact gap in both copies at once. This second fixture adds the missing
# case.
_DISPATCH_GUARD_CROSS_BATCH_REPLAY_FIXTURE = r"""
export const meta = { version: "dispatch-guard-cross-batch-replay-parity-pin-fixture" };

function batchDispatchPrompt(batch) {
  return "REAL PROMPT for " + batch.assignments.map(a => a.assignment_id).join(",");
}

const batch0 = args[0];
const batch1 = args[1];

const batch0Reply = await agent(batchDispatchPrompt(batch0), {
  phase: "SkepticPass", label: "skeptic:dispatch:0",
});

let replayRejected = false;
let replayErrorMessage = null;
let replayReply = null;
try {
  // The decoy: the REAL builder, called for batch 0, forwarded under
  // batch 1's own dispatch label -- a prompt the builder genuinely
  // produced, just not for the batch this call claims to be.
  replayReply = await agent(batchDispatchPrompt(batch0), {
    phase: "SkepticPass", label: "skeptic:dispatch:1",
  });
} catch (err) {
  replayRejected = true;
  replayErrorMessage = String(err && err.message || err);
}

return {
  batch0Reply: batch0Reply,
  replayRejected: replayRejected,
  replayErrorMessage: replayErrorMessage,
  replayReply: replayReply,
};
"""


def _run_dispatch_guard_cross_batch_replay_fixture(build_harness_fn, tmp_path: Path, label: str) -> dict:
    """Same one-input-two-harnesses discipline as
    _run_dispatch_guard_parity_fixture, for the cross-batch-replay shape."""
    assert NODE is not None, "node executable not found on PATH"
    batches = [
        {"index": 0, "assignments": [{
            "assignment_id": aid("Jean"), "source_form": "Jean", "canonical_target_form": "Jean",
            "risk_classes": ["high_dispersion"], "windows_truncated": False, "windows": [],
        }]},
        {"index": 1, "assignments": [{
            "assignment_id": aid("Marie"), "source_form": "Marie", "canonical_target_form": "Marie",
            "risk_classes": ["high_dispersion"], "windows_truncated": False, "windows": [],
        }]},
    ]
    harness_text = build_harness_fn(
        _DISPATCH_GUARD_CROSS_BATCH_REPLAY_FIXTURE, batches, {}, str(tmp_path), "cross-batch-replay-pin-run",
    )
    harness_path = tmp_path / f"dispatch_guard_cross_batch_replay_{label}.js"
    harness_path.write_text(harness_text, encoding="utf-8")
    proc = subprocess.run([NODE, str(harness_path)], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, (
        f"{label}'s cross-batch-replay parity harness process itself failed (exit "
        f"{proc.returncode}) -- this means the LEGITIMATE batch-0 dispatch was "
        f"rejected too, not just the replay:\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
    )
    return json.loads(proc.stdout)["result"]


def test_dispatch_write_guard_rejects_cross_batch_replay_in_both_harness_copies(tmp_path):
    """Round 8: closes the parity pin's own blind spot. The pin above
    (test_dispatch_write_guard_agrees_between_the_two_harness_copies) drives
    only a single batch, so it cannot see a decoy that dispatches under one
    batch's label while replaying a DIFFERENT batch's real, builder-produced
    prompt -- exactly the defect round 7 found in this file's own
    identity-recording fix (see _DISPATCH_PROMPT_RECORDER's header comment).
    Measured directly, before porting the fix to the sibling copy: this
    file's own recorder, Map-keyed by batch index, rejects the replay; the
    sibling's still-flat-array recorder accepted the SAME replay
    (`.indexOf()` only proves the builder produced this string for SOME
    batch, not for batch 1 specifically). This test pins that both copies
    now reject it, closing the gap the single-batch pin above cannot see."""
    results = {
        "skeptic_pipeline_e2e.test.py": _run_dispatch_guard_cross_batch_replay_fixture(
            build_skeptic_harness, tmp_path, "e2e",
        ),
        "skeptic_confident_mismerge.test.py": _run_dispatch_guard_cross_batch_replay_fixture(
            _load_sibling_mismerge_module_for_dispatch_guard_parity().build_skeptic_harness,
            tmp_path, "mismerge",
        ),
    }
    for label, result in results.items():
        assert result["batch0Reply"] == "FRAGMENT 0", (
            f"{label}'s dispatch-write identity guard rejected batch 0's own "
            f"legitimate dispatch -- over-broad, not just under-broad. Result: {result}"
        )
        assert result["replayRejected"] is True, (
            f"{label}'s dispatch-write identity guard ACCEPTED a prompt the real "
            f"batchDispatchPrompt() built for a DIFFERENT batch (batch 0), replayed "
            f"under batch 1's own dispatch label -- right function, wrong index. "
            f"Result: {result}"
        )


# Round 9 (codex, HIGH): the round-8 fix above closed WRONG-INDEX replay, and
# stayed blind to SAME-INDEX, wrong-CONTENT forgery -- a fabricated object
# that copies the real batch's own `.index` but carries a DIFFERENT batch's
# `.assignments`. `batchDispatchPrompt()` is still the real function, still
# genuinely called, still genuinely producing a prompt from whatever object
# it is handed; what is forged is the object, not the call. codex applied
# this exact mutation to the real template (`batchDispatchPrompt(batch)` ->
# `batchDispatchPrompt({ index: batch.index, assignments: BATCHES[0]
# .assignments })`), built both real harness copies, and ran each under node
# directly: rc=0, merged=true for both, with the round-8 different-index
# replay still correctly rejected as a control (the guards were live, just
# blind to this shape). See _DISPATCH_PROMPT_RECORDER's header comment above
# for the object-identity fix this drove.
_DISPATCH_GUARD_SAME_INDEX_WRONG_CONTENT_FIXTURE = r"""
export const meta = { version: "dispatch-guard-same-index-wrong-content-parity-pin-fixture" };

function batchDispatchPrompt(batch) {
  return "REAL PROMPT for " + batch.assignments.map(a => a.assignment_id).join(",");
}

const batch0 = args[0];
const batch1 = args[1];

const batch0Reply = await agent(batchDispatchPrompt(batch0), {
  phase: "SkepticPass", label: "skeptic:dispatch:0",
});

let forgeryRejected = false;
let forgeryErrorMessage = null;
let forgeryReply = null;
try {
  // The decoy: a FABRICATED object literal copying batch 1's own real
  // index but carrying batch 0's real assignments -- codex's exact
  // finding. The call to batchDispatchPrompt() is genuine; the object it
  // is handed is not.
  forgeryReply = await agent(
    batchDispatchPrompt({ index: batch1.index, assignments: batch0.assignments }),
    { phase: "SkepticPass", label: "skeptic:dispatch:1" },
  );
} catch (err) {
  forgeryRejected = true;
  forgeryErrorMessage = String(err && err.message || err);
}

return {
  batch0Reply: batch0Reply,
  forgeryRejected: forgeryRejected,
  forgeryErrorMessage: forgeryErrorMessage,
  forgeryReply: forgeryReply,
};
"""


def _run_dispatch_guard_same_index_wrong_content_fixture(build_harness_fn, tmp_path: Path, label: str) -> dict:
    """Same one-input-two-harnesses discipline as the cross-batch-replay
    fixture above, for the same-index-wrong-content shape."""
    assert NODE is not None, "node executable not found on PATH"
    batches = [
        {"index": 0, "assignments": [{
            "assignment_id": aid("Jean"), "source_form": "Jean", "canonical_target_form": "Jean",
            "risk_classes": ["high_dispersion"], "windows_truncated": False, "windows": [],
        }]},
        {"index": 1, "assignments": [{
            "assignment_id": aid("Marie"), "source_form": "Marie", "canonical_target_form": "Marie",
            "risk_classes": ["high_dispersion"], "windows_truncated": False, "windows": [],
        }]},
    ]
    harness_text = build_harness_fn(
        _DISPATCH_GUARD_SAME_INDEX_WRONG_CONTENT_FIXTURE, batches, {}, str(tmp_path),
        "same-index-wrong-content-pin-run",
    )
    harness_path = tmp_path / f"dispatch_guard_same_index_wrong_content_{label}.js"
    harness_path.write_text(harness_text, encoding="utf-8")
    proc = subprocess.run([NODE, str(harness_path)], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, (
        f"{label}'s same-index-wrong-content parity harness process itself failed "
        f"(exit {proc.returncode}) -- this means the LEGITIMATE batch-0 dispatch was "
        f"rejected too, not just the forgery:\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
    )
    return json.loads(proc.stdout)["result"]


def test_dispatch_write_guard_rejects_same_index_wrong_content_forgery_in_both_harness_copies(tmp_path):
    """Round 9 (codex, HIGH). Pins the fix in _DISPATCH_PROMPT_RECORDER's
    header comment: a decoy that fabricates an object copying a real batch's
    own `.index` but carrying a DIFFERENT batch's `.assignments`, then calls
    the REAL batchDispatchPrompt() with it, must be rejected in both harness
    copies -- same index, wrong content, right function is not a lesser
    attack than wrong index; it defeated the round-8 index-keyed guard
    outright (measured directly, both copies, rc=0/merged=true, before this
    fix)."""
    results = {
        "skeptic_pipeline_e2e.test.py": _run_dispatch_guard_same_index_wrong_content_fixture(
            build_skeptic_harness, tmp_path, "e2e",
        ),
        "skeptic_confident_mismerge.test.py": _run_dispatch_guard_same_index_wrong_content_fixture(
            _load_sibling_mismerge_module_for_dispatch_guard_parity().build_skeptic_harness,
            tmp_path, "mismerge",
        ),
    }
    for label, result in results.items():
        assert result["batch0Reply"] == "FRAGMENT 0", (
            f"{label}'s dispatch-write identity guard rejected batch 0's own "
            f"legitimate dispatch -- over-broad, not just under-broad. Result: {result}"
        )
        assert result["forgeryRejected"] is True, (
            f"{label}'s dispatch-write identity guard ACCEPTED a fabricated object "
            f"that copies batch 1's own index but carries batch 0's assignments, "
            f"passed to the REAL batchDispatchPrompt() -- same index, wrong content, "
            f"right function. Result: {result}"
        )
