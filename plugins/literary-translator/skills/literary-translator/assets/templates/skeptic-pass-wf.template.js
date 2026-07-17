// skeptic-pass-wf.template.js -- literary-translator plugin
//
// GENERATED-ONLY template (RFC #215 Phase 2, structural-risk triage +
// adverse-only skeptic pass). Clones glossary-pass-wf.template.js's own
// dispatch -> bounded-wait -> merge -> disk-independent-verify CONTROL FLOW
// verbatim (never its prompt, never codex_job.py) -- see
// references/canon-and-glossary.md and references/orchestration-and-
// batching.md for the shared mechanism this file implements, and
// glossary-pass-wf.template.js's own header comment for the #87/#88/#90/
// #97 rationale behind that shape.
//
// **OPT-IN + ADVISORY (1.6.0):** unlike the glossary pass, this Workflow
// runs ONLY when a project's profile.yml sets `glossary.skeptic_pass
// .enabled: true`, and its output (skeptic_triage.json) is read by exactly
// one command, the separate advisory `skeptic_report.py` -- no freeze/merge
// reader, and no existing gate (`canon_adjudication_audit.py`), ever opens
// it. The triage schema's `verdict` enum
// (adverse/propose_split/propose_rescope/insufficient_window) has no value
// able to express a confirmation, so nothing this Workflow produces can
// ever land a wrong merge or a phantom split into accepted state through
// the intended path -- see skeptic-triage.schema.json's own description
// and skeptic_ready.py's module docstring for the full safety invariant.
//
// Storage location once instantiated (pinned, mirrors glossary):
//   ${durable_root}/runs/workflows/<run_id>/skeptic-pass-wf.js
//
// Substitution tokens (resolved ONCE by the orchestrating Claude session at
// instantiation time -- there is no templating engine at Workflow-runtime,
// so every token below must already be resolved in the generated file
// before it runs; mirrors glossary-pass-wf.template.js's own token
// discipline exactly):
//   {{SOURCE_LANG}}       -- e.g. "French" -- context only, this pass never
//                            translates or canonicalizes anything.
//   {{DURABLE_ROOT}}      -- the project's durable_root, an absolute path.
//   {{PARTICLE_CONFIG}}   -- profile.yml's source.language.particle_config
//                            LITERAL value (a bare filename), passed
//                            through verbatim to every skeptic_ready.py
//                            --particle-config flag below -- never
//                            reconstructed from source.language.code.
//   {{RUN_ID}}            -- this run's own id (the skeptic pass's OWN
//                            resume domain, distinct from any mass/glossary
//                            RUN_ID -- see skeptic_setup.py). Fresh on a
//                            fresh run, the SAME value again on a resumed
//                            one. Names the run-scoped directory every
//                            fragment/manifest this script touches lives
//                            under.
//   {{BATCH_AGENT_CAP}}   -- engine.batch_agent_cap, the SAME profile field
//                            mass-translate-wf.template.js/glossary-pass-wf
//                            .template.js read, substituted as a BARE
//                            integer. Feeds the same preflight cost cap
//                            those two templates use.
//
// `args` shape this template expects (an array, or a JSON string of one) --
// deliberately carries each window's own resolved block TEXT directly
// (never re-read from manifest.json by this script -- see the PRE-WORKFLOW
// setup note below), the same "args carries the content, the on-disk
// manifest is only for coverage" split glossary-pass-wf.template.js uses
// for its own `candidates`:
//   [ { index: 0, assignments: [
//         { assignment_id, source_form, canonical_target_form, risk_classes,
//           windows_truncated,
//           windows: [ { block, seg, char_start, char_end, text }, ... ] },
//         ... ] },
//     { index: 1, assignments: [...] }, ... ]
// Each `assignments[]` row mirrors one skeptic-assignment.schema.json
// `assignments[]` entry (assignment_id/source_form/canonical_target_form/
// risk_classes/windows_truncated copied verbatim), with each of its
// `windows[]` entries carrying one EXTRA field, `text`, the resolved WHOLE
// block text (`manifest.blocks[window.block].plain_text`) the window's
// `char_start`/`char_end` index into -- resolved once, by whoever builds
// `args` (the orchestrating session, or a small planner script analogous to
// `glossary_batch_plan.py`), never by this script.
//
// Deterministic PRE-WORKFLOW setup (the orchestrating session's own
// skeptic_setup.py call, kind="skeptic" -- run BEFORE the Workflow tool
// ever executes this file, never this script's own job): by the time this
// script runs, ${durable_root}/skeptic/runs/{{RUN_ID}}/ already exists, and
// it already holds, for every batch in `args`, an atomically written
// assignments_{index}.json (that batch's own assignment_id[] array,
// verbatim) plus the aggregate assignments.json (skeptic-assignment
// .schema.json shape -- schema_version/run_id/input_digest/
// producer_input_digest/batch_count/assignments[], carrying every
// assigned entity's own windows_truncated + batch_index). This script
// never creates that directory or those files, and never trusts anything
// BUT them for coverage -- a codex batch call can't pass its own
// self-check by quietly omitting an assigned entity, because the file it
// is checked against was written independently, before the batch was ever
// dispatched.

export const meta = {
  name: "literary-translator-skeptic-pass",
  description: "Adversarially re-examine structurally-suspicious canon entries (RFC #215 Phase 2) against bounded, whole-block source windows via a fire-and-forget codex-rescue call per batch, writing a run-scoped triage fragment per batch, then one serialized deterministic merge into skeptic_triage.json plus a disk-independent coverage/evidence verify. Opt-in, advisory, adverse-only -- never touches canon.json; every ordinary skeptic-pass failure is non-blocking, EXCEPT a frozen-input hash mismatch (canon.json/manifest.json changed since setup), which the orchestrator gates as FATAL/HALT.",
  phases: [
    {
      title: "SkepticPass",
      detail: "codex adversarially examines each batch of assigned entities against their bounded source windows, resolving each to adverse/propose_split/propose_rescope/insufficient_window, and writes its own run-scoped fragment atomically, self-validated (shape + token/coverage + evidence re-auth, with any unverifiable citation silently coerced to insufficient_window) via skeptic_ready.py --validate-fragment -- never a shared file, so concurrent batches never race",
    },
    {
      title: "Merge",
      detail: "one serialized skeptic_ready.py --merge-fragments call folds every ready batch's fragment into skeptic_triage.json in a fully deterministic order, then a disk-independent skeptic_ready.py --verify-merged call re-checks coverage, multiplicity, token/source_form/window-scoping consistency, every cited evidence record, and (when stamped) a best-effort frozen canon.json/manifest.json integrity tripwire, straight off disk, before this run reports merged:true -- a tripwire mismatch is surfaced as a distinct frozen-input-mismatch signal, not an ordinary advisory failure",
    },
  ],
}

const ROOT = "{{DURABLE_ROOT}}"
const PY = "python3"
const SOURCE_LANG = "{{SOURCE_LANG}}"
const PARTICLE_CONFIG = "{{PARTICLE_CONFIG}}"
const RUN_ID = "{{RUN_ID}}"
const RUN_DIR = ROOT + "/skeptic/runs/" + RUN_ID
const BATCH_AGENT_CAP = {{BATCH_AGENT_CAP}}

// ---------------------------------------------------------------------------
// Schema literal -- declared ABOVE the pipeline() call at the bottom of this
// file (temporal-dead-zone discipline, see glossary-pass-wf.template.js's
// own comment on this). Relays skeptic_ready.py --verify-merged's own
// {verified, missing[]} line verbatim -- the SAME flat shape
// CANON_VERIFY_SCHEMA uses for the analogous glossary call, for the same
// tool-use-API "top-level object, no combinator" reason -- PLUS this
// template's own addition, frozen_input_mismatch (P1 fix, review-bot #227):
// relayed straight from skeptic_ready.py's own output field so the
// FATAL/HALT signal below can be driven off the command's own distinct
// verdict, never re-derived by scanning missing[] text. REQUIRED, not
// optional (codex round-4 fix): the command's own output ALWAYS includes
// this field (skeptic_ready.py's run_verify_merged returns it
// unconditionally), so a schema-valid relay reply that silently DROPS it
// would be indistinguishable from `undefined === true` -> false below,
// quietly downgrading a real frozen-input mismatch to the generic advisory
// verify-failed bucket -- marking it required forces the Workflow
// harness's retry-until-valid to reject that omission outright, the same
// "flat schema needs its own required-field discipline" class
// references/workflow-schema-validation.md warns about (mirrors why
// isVerifiedResult() below guards `missing` explicitly rather than trusting
// the schema alone).
// ---------------------------------------------------------------------------

const SKEPTIC_VERIFY_SCHEMA = {
  type: "object",
  additionalProperties: false,
  required: ["verified", "frozen_input_mismatch"],
  properties: {
    verified: { type: "boolean" },
    missing: { type: "array", items: { type: "string" } },
    frozen_input_mismatch: { type: "boolean" },
  },
}

const BATCHES = Array.isArray(args) ? args : JSON.parse(args)

// ---------------------------------------------------------------------------
// Preflight cost cap -- IDENTICAL formula to glossary-pass-wf.template.js's
// own (per batch: precheck + dispatch + wait == 3; plus the fixed
// merge + verify pair == 2), for the same reason: a resumed batch whose
// fragment already passes --validate-fragment pays only the 1 precheck
// call, strictly cheaper, so 3*BATCHES.length + 2 is the true worst-case
// ceiling for a FRESH run. Refuses the whole run, dispatching nothing, if
// exceeded -- the caller re-plans smaller batches and re-runs.
// ---------------------------------------------------------------------------
const estimatedCalls = 3 * BATCHES.length + 2
if (estimatedCalls > BATCH_AGENT_CAP) {
  log(
    "Batch too large: estimatedCalls=" + estimatedCalls +
    " exceeds engine.batch_agent_cap=" + BATCH_AGENT_CAP +
    " for " + BATCHES.length + " skeptic-pass batch(es)."
  )
  return { merged: false, reason: "batch-too-large", estimatedCalls: estimatedCalls, cap: BATCH_AGENT_CAP }
}

// ---------------------------------------------------------------------------
// Defense-in-depth batch-index guard -- mirrors glossary-pass-wf
// .template.js's own SEG_ID_RE-adjacent discipline exactly: every batch's
// index is spliced, unquoted, into shell command strings and file paths
// below, so an unsafe or duplicate index must throw here, before anything
// is dispatched against it.
// ---------------------------------------------------------------------------
const seenBatchIndices = new Set()
for (let i = 0; i < BATCHES.length; i++) {
  const idx = BATCHES[i] && BATCHES[i].index
  if (typeof idx !== "number" || !Number.isInteger(idx) || idx < 0) {
    throw new Error("Unsafe batch index " + JSON.stringify(idx) + " at position " + i + ": must be a non-negative integer")
  }
  if (seenBatchIndices.has(idx)) {
    throw new Error("Duplicate batch index " + idx + " at position " + i)
  }
  seenBatchIndices.add(idx)
}

// ---------------------------------------------------------------------------
// Run-scoped path helpers. RUN_DIR, and every assignments*.json inside it,
// already exist by the time this script runs -- see the header comment's
// "Deterministic PRE-WORKFLOW setup" section. This script only ever reads
// those manifests (for the --expect-assignments-file / --verify-merged
// coverage checks) and writes/reads its own triage_{index}.json fragments;
// it never creates RUN_DIR itself, and it never reads manifest.json's block
// text directly -- that already arrived via `args` (see the header
// comment's `args` shape note).
// ---------------------------------------------------------------------------
function fragmentPath(index) {
  return RUN_DIR + "/triage_" + index + ".json"
}
function assignmentsBatchPath(index) {
  // Mirrors skeptic_constants.SKEPTIC_FRAGMENT_PREFIX's sibling convention:
  // this batch's own assignment_id[] array, written by skeptic_setup.py
  // BEFORE dispatch, exactly like glossary's manifest_{index}.json.
  return RUN_DIR + "/assignments_" + index + ".json"
}
const AGGREGATE_ASSIGNMENTS_PATH = RUN_DIR + "/assignments.json"
const SKEPTIC_TRIAGE_PATH = ROOT + "/skeptic_triage.json"
const MANIFEST_PATH = ROOT + "/manifest.json"
const CANON_PATH = ROOT + "/canon.json"

function checkCommand(batch) {
  return PY + " " + ROOT + "/scripts/skeptic_ready.py --validate-fragment " + fragmentPath(batch.index) +
    " --particle-config " + PARTICLE_CONFIG +
    " --expect-assignments-file " + assignmentsBatchPath(batch.index)
}

// ---------------------------------------------------------------------------
// Prompt-builder functions. Plain string concatenation throughout, never a
// backtick template literal -- same reason as mass-translate-wf.template.js
// / glossary-pass-wf.template.js (natural-language prose below routinely
// needs literal quotes).
// ---------------------------------------------------------------------------

// PRECHECK -- Claude, effort:low, no agentType, no schema. Resume-skip: a
// prior, possibly-interrupted run of this SAME {{RUN_ID}} may have already
// written a fragment that still passes --validate-fragment against the
// CURRENT assignment manifest. Unlike glossary's --check-batch, this
// command is not strictly read-only -- it also NORMALIZES the fragment in
// place (any citation that no longer re-verifies is silently coerced to
// insufficient_window) -- but that coercion is idempotent, so running it
// again here is always safe, never destructive, and never changes an
// already-safe fragment's meaning.
function batchPrecheckPrompt(batch) {
  const checkCmd = checkCommand(batch)
  const lines = []
  lines.push("A prior run of skeptic-pass batch " + batch.index + " may already have written a valid fragment to disk. Check ONCE whether it is already present and valid: run exactly this one bash command (a single invocation, NOT a polling loop):")
  lines.push(checkCmd)
  lines.push("This command also normalizes the fragment in place (any citation that fails to re-verify is silently downgraded to insufficient_window) -- safe to run even if it was already run before; it never fabricates or drops a whole record, only downgrades an unverifiable citation's own verdict.")
  lines.push("If that command exits successfully (exit code 0), the fragment is already complete and valid -- return exactly the line: PRESENT " + batch.index)
  lines.push("If it exits non-zero for ANY reason (the file is missing, is not valid JSON, or fails its shape/token/coverage checks), return exactly the line: ABSENT " + batch.index)
  lines.push("Do nothing else -- do not create, dispatch, or resolve any entity yourself; this is purely a read-only-in-intent presence check.")
  return lines.join("\n")
}

// DISPATCH -- codex, schema-less, fire-and-forget (see
// references/workflow-schema-validation.md's "shared codex work-call
// pattern"). Writes this batch's fragment ATOMICALLY to its own run-scoped
// path and self-validates via skeptic_ready.py --validate-fragment, against
// the assignment manifest the pre-workflow setup already wrote -- never
// against a shell-embedded assignment list this agent could quietly omit
// from. The Workflow JS never depends on this call's own return value; only
// the WAIT step's disk-backed poll below does.
function batchDispatchPrompt(batch) {
  const outPath = fragmentPath(batch.index)
  const checkCmd = checkCommand(batch)
  const lines = []
  lines.push("Effort: high. Skeptic pass (codex-skeptic-pass, RFC #215 Phase 2) for a " + SOURCE_LANG + " literary translation project's canon, batch " + batch.index + ".")
  lines.push("You are the ADVERSARY, not the author. Every entity below was already accepted into this project's canon.json by an EARLIER, blind (source-text-unaware) pass. Your ONLY job here is to try to find a concrete reason that earlier acceptance was wrong, using ONLY the actual source-text windows given below for each entity. You may NEVER confirm an entity is correctly identified -- there is no verdict available to you that means \"confirmed\"; your output schema accepts only adverse, propose_split, propose_rescope, or insufficient_window. When in genuine doubt, insufficient_window is always the safe, correct answer -- never strain for a split or an adverse finding you cannot back with an exact, real quote.")
  lines.push("Verdict rules, applied independently to EACH entity below:")
  lines.push("- propose_split: this ONE source_form's occurrences actually denote 2 OR MORE distinct referents conflated under one canon entry (the RFC's motivating case: one spelling shared by several different people). Requires: FIRST enumerate the distinct referents you believe are conflated; THEN, for EACH one (at least 2 -- fewer than 2 is not a usable split), cite one piece of evidence (see the citation format below) -- a real quote from the windows given for THIS entity that supports that specific referent. A referent whose evidence you cannot pin to an exact quote from the windows below is not usable -- omit it rather than fabricate one; dropping below 2 usable referents automatically and safely downgrades your whole claim to insufficient_window, which is a fine outcome, never something you need to force past.")
  lines.push("- adverse: the windows below show a SPECIFIC, concrete sentence that contradicts this entity's current canon identity or an existing merge (two named individuals doing incompatible things in the same passage, an impossible timeline, an explicit textual statement that two forms are different people, etc.). Requires ONE cited piece of evidence -- the exact contradicting quote.")
  lines.push("- propose_rescope: the windows show this entry should not be scoped as a person/identity entry at all (for instance, every occurrence is a citation or allusion, never an active narrative participant). Requires the SAME one-citation evidence shape as adverse.")
  lines.push("- insufficient_window: you found nothing definite either way, or windows_truncated is true for this entity (you were not shown every occurrence, so a confident negative or positive claim is not safe). This is the DEFAULT whenever you are not sure.")
  lines.push("Evidence citation format -- every citation (adverse/propose_rescope's single one, or each propose_split referent's own) is an object { block, seg, char_start, char_end, context_start, context_end, sha256 }: block = the window's own block id (copied verbatim from the entity's windows below); seg = that window's own seg value (copied verbatim, including a literal null); char_start/char_end = the exact character offsets of your quoted span WITHIN that block's full text given below (0-indexed, half-open -- text[char_start:char_end] must equal your quote exactly); context_start = 0 and context_end = that block's full text length in characters (the context window is always the block's ENTIRE text, never a narrower slice); sha256 = the sha256 hex digest of the UTF-8 bytes of that block's entire text. Compute char_start/char_end/sha256 as precisely as you can from the exact text given -- a citation that does not check out byte-for-byte against the real text is automatically and safely downgraded to insufficient_window (never dangerous, only wasted effort), so precision helps but is never a hard requirement you must re-litigate.")
  lines.push("Entities assigned to this batch (each already flagged by a deterministic, confidence-independent structural scan -- risk_classes names WHY it was flagged, never a verdict; windows are this entity's own bounded set of whole-block source excerpts, capped per entity, with windows_truncated indicating whether some were omitted):")
  lines.push(JSON.stringify(batch.assignments, null, 1))
  lines.push("Write this exact JSON object, to " + outPath + " ATOMICALLY: write it first to a fresh temp file in the SAME directory (for example a dot-prefixed name alongside the target, holding your own process id), then rename that temp file into place at exactly " + outPath + " -- so a partially-written file is never visible at that path. Shape: {\"schema_version\": 1, \"run_id\": \"" + RUN_ID + "\", \"records\": [ ... ]}, with EXACTLY one record per entity listed above, in the SAME order, each shaped { assignment_id (copied verbatim from that entity), source_form (copied verbatim), verdict, rationale (a short human-readable reason), and evidence/referents exactly as the verdict rules above require }. A plain JSON object, no markdown code fence, no comment, nothing else in the file.")
  lines.push("Then self-check by running this command and reading its one line of JSON output: " + checkCmd)
  lines.push("This command schema-validates your fragment and rejects it outright (naming every offending item) if its shape, its assignment_id/source_form pairing, or its coverage of this batch's assigned entities is wrong -- fix each one named and re-run the command until it prints a line with \"success\": true. It ALSO independently re-authenticates every citation you gave and silently downgrades any that does not check out to insufficient_window, rewriting your fragment in place -- a \"success\": true result with a nonzero \"coerced\" count just means some of your citations were not verifiable; this is a normal, safe, and expected outcome, never something you need to fix or re-litigate.")
  lines.push("Once you see \"success\": true, return exactly the line: FRAGMENT " + batch.index)
  return lines.join("\n")
}

// WAIT -- Claude, effort:low, no agentType, no schema: a bounded poll of the
// SAME --validate-fragment command DISPATCH's self-check already used,
// against this batch's own fragment (the translate/review wait steps'
// shape -- see mass-translate-wf.template.js's waitPrompt).
function batchWaitPrompt(batch) {
  const checkCmd = checkCommand(batch)
  const lines = []
  lines.push("The codex skeptic-pass batch " + batch.index + " is working in the background. Wait for it to finish: run exactly one bash command, a polling loop:")
  lines.push("for i in $(seq 1 45); do " + checkCmd + " && exit 0; sleep 20; done; exit 1")
  lines.push("If that command exits successfully, return exactly the line: READY " + batch.index)
  lines.push("Otherwise, after the timeout (about 15 minutes), return exactly the line: TIMEOUT " + batch.index)
  lines.push("Do nothing else -- do not touch any files, and do not resolve any entity yourself.")
  return lines.join("\n")
}

// Merge -- Claude, effort:low, no agentType, no schema: this call's own
// return is never trusted (see references/workflow-schema-validation.md);
// only the disk-independent skepticVerifyPrompt() call below gates
// merged:true. Reads every ready batch's fragment straight out of RUN_DIR --
// no fragment list needs threading through, unlike glossary's
// --merge-batches (skeptic_ready.py --merge-fragments takes the run dir
// itself and globs it).
function mergeFragmentsPrompt() {
  const lines = []
  lines.push("Effort: low. Mechanical skeptic-triage merge only -- no judgment.")
  lines.push("Durable root: " + ROOT + ".")
  const cmd = PY + " " + ROOT + "/scripts/skeptic_ready.py --merge-fragments " + RUN_DIR
  lines.push("Run exactly this command and capture its single printed JSON line: " + cmd)
  lines.push("Return that printed line's content, as text, in your own response. Do not judge or re-decide anything yourself -- a separate, disk-independent step verifies this merge afterward and is what this run actually trusts.")
  return lines.join("\n")
}

// Verify -- Claude, effort:low, no agentType, schema: SKEPTIC_VERIFY_SCHEMA.
// Disk-independent: skeptic_ready.py --verify-merged fresh-reads
// skeptic_triage.json plus the aggregate assignment manifest itself, never
// trusting the merge call above's own claim (mirrors glossary's #88 fix).
// At the SAME rigor as the per-batch self-check above (--validate-fragment)
// -- token/source_form/window-scoping re-checks, exact-one-record-per-
// assignment multiplicity, run_id binding -- plus two merge-only checks:
// --manifest-path (explicit, matching --canon below, rather than relying on
// skeptic_ready.py's own default) and --canon feed the frozen-input
// integrity tripwire half of the H1 mitigation (BEST-EFFORT only -- see
// skeptic_ready.py's own docstring for why it cannot be sound against an
// adversarial agent): whenever skeptic_setup.py stamped canon_sha256/
// manifest_sha256 into the aggregate manifest, this call re-hashes the
// on-disk files and flags a mismatch if either changed since setup. A
// mismatch is surfaced via the command's own DISTINCT `frozen_input_mismatch`
// field (never inferred from `missing[]` text) and propagated below as this
// Workflow's own `frozenInputMismatch`/`reason: "frozen-input-mismatch"` --
// this is the signal SKILL.md's exit-contract gates FATAL/HALT on, unlike
// every other skeptic-pass failure here, which stays advisory.
function verifyMergedPrompt() {
  const lines = []
  lines.push("Effort: low. Mechanical disk-independent merge verification only -- do not judge the comparison yourself.")
  lines.push("Durable root: " + ROOT + ".")
  const cmd = PY + " " + ROOT + "/scripts/skeptic_ready.py --verify-merged " + SKEPTIC_TRIAGE_PATH + " " + AGGREGATE_ASSIGNMENTS_PATH + " --particle-config " + PARTICLE_CONFIG + " --manifest-path " + MANIFEST_PATH + " --canon " + CANON_PATH
  lines.push("Run exactly this command and read its one line of JSON output: " + cmd)
  lines.push("Return a structured result with exactly these fields: verified (the command's own verified value), frozen_input_mismatch (the command's own frozen_input_mismatch value, copied verbatim -- it is always present in the command's output), and, only when the command's own output actually includes it, missing (the command's own missing array, copied verbatim). Do not add, omit, or alter any value the command printed.")
  return lines.join("\n")
}

// Exact-key-set JS guard for SKEPTIC_VERIFY_SCHEMA's flat literal (see
// references/ledger-and-resumability.md's guard-field-set discipline) --
// IDENTICAL to glossary-pass-wf.template.js's own isVerifiedResult(): a
// flat schema alone would accept a hollow or crossover object as readily as
// a genuine one. Accepted only when verified===true AND missing is either
// absent or a genuinely empty array.
function isVerifiedResult(v) {
  if (!v || v.verified !== true) return false
  if (Object.prototype.hasOwnProperty.call(v, "missing")) {
    return Array.isArray(v.missing) && v.missing.length === 0
  }
  return true
}

// ---------------------------------------------------------------------------
// Per-batch dispatch -> wait sequence. pipeline() runs these concurrently;
// each batch writes only its own fragment file, so concurrent batches never
// collide on shared bytes (mirrors glossary's own #90 fix).
// ---------------------------------------------------------------------------
async function batchStep(batch) {
  const precheck = await agent(batchPrecheckPrompt(batch), {
    effort: "low", phase: "SkepticPass", label: "skeptic:precheck:" + batch.index,
  })
  // EXACT match, never substring (content-matching-sentinel-fragility): a
  // failure reply like "ABSENT 0 (fragment missing; not PRESENT)" contains
  // the literal substring "PRESENT" and would falsely resume-skip under a
  // `.indexOf(...) !== -1` check -- the sentinel and this check must agree
  // on the WHOLE trimmed line, batch index included (batchPrecheckPrompt
  // instructs the agent to return exactly "PRESENT <index>"/"ABSENT
  // <index>").
  if (String(precheck).trim() === "PRESENT " + batch.index) {
    log("batch " + batch.index + ": resume-skip -- existing fragment already passed --validate-fragment, not re-dispatching")
    return { batchIndex: batch.index, fragmentPath: fragmentPath(batch.index), ready: true }
  }

  await agent(batchDispatchPrompt(batch), {
    agentType: "codex:codex-rescue",
    effort: "high",
    phase: "SkepticPass",
    label: "skeptic:dispatch:" + batch.index,
  })

  const ready = await agent(batchWaitPrompt(batch), {
    effort: "low", phase: "SkepticPass", label: "skeptic:wait:" + batch.index,
  })
  // Same EXACT-match discipline as the precheck above: a timeout reply like
  // "TIMEOUT 0 (not READY)" contains the literal substring "READY" and
  // would falsely pass a `.indexOf("READY") === -1` check (batchWaitPrompt
  // instructs the agent to return exactly "READY <index>"/"TIMEOUT
  // <index>").
  if (String(ready).trim() !== "READY " + batch.index) {
    log("batch " + batch.index + ": fragment never became ready")
    return { batchIndex: batch.index, fragmentPath: fragmentPath(batch.index), ready: false, reason: "skeptic-pass-null" }
  }
  return { batchIndex: batch.index, fragmentPath: fragmentPath(batch.index), ready: true }
}

const batchResults = await pipeline(BATCHES, batchStep)

const readyBatches = batchResults.filter((r) => r && r.ready)
const notReadyBatches = batchResults.filter((r) => !r || !r.ready)

if (notReadyBatches.length > 0) {
  log("Skeptic pass: " + notReadyBatches.length + "/" + BATCHES.length + " batch(es) never produced a ready fragment; the merge is not attempted.")
  return {
    batches: batchResults, merged: false, reason: "fragment-check-failed",
    notReady: notReadyBatches.map((r) => (r ? r.batchIndex : null)),
  }
}

// ONE serialized merge call (never concurrent with itself, and never run
// until every batch's own fragment has independently passed
// --validate-fragment above) -- the skeptic-pass analogue of glossary's #90
// fix.
await agent(mergeFragmentsPrompt(), {
  effort: "low", phase: "Merge", label: "skeptic:merge",
})

const verified = await agent(verifyMergedPrompt(), {
  effort: "low", phase: "Merge", label: "skeptic:verify", schema: SKEPTIC_VERIFY_SCHEMA,
})

if (!isVerifiedResult(verified)) {
  const missingDetail = verified && Array.isArray(verified.missing) ? verified.missing : null
  // P1 fix (review-bot #227): a frozen-input hash mismatch (canon.json/
  // manifest.json changed since skeptic_setup.py stamped this run) must be
  // surfaced as a DISTINCT reason/flag, never folded into the generic
  // "verify-failed" bucket every other skeptic-pass failure (batch-too-
  // large / fragment-check-failed / an ordinary coverage gap or unverified
  // citation) shares -- SKILL.md's exit-contract gates FATAL/HALT on this
  // signal specifically, keeping every other skeptic-pass failure advisory.
  if (verified && verified.frozen_input_mismatch === true) {
    log(
      "Skeptic pass: FROZEN-INPUT MISMATCH -- canon.json/manifest.json changed since " +
      "skeptic_setup.py stamped this run's hashes" +
      (missingDetail && missingDetail.length ? " -- " + missingDetail.join(", ") : "") + "."
    )
    return {
      batches: batchResults, merged: false, reason: "frozen-input-mismatch",
      missing: missingDetail, frozenInputMismatch: true,
    }
  }
  log("Skeptic pass: post-merge disk verification failed" + (missingDetail && missingDetail.length ? " -- missing: " + missingDetail.join(", ") : "") + ".")
  return { batches: batchResults, merged: false, reason: "verify-failed", missing: missingDetail }
}

log("DONE: " + readyBatches.length + "/" + BATCHES.length + " batch fragment(s) merged into skeptic_triage.json (verified).")
return { batches: batchResults, merged: true }
