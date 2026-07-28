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
  description: "Adversarially re-examine structurally-suspicious canon entries (RFC #215 Phase 2) against bounded, whole-block source windows via a fire-and-forget codex-rescue call per batch, writing a run-scoped triage fragment per batch, then one serialized deterministic merge into skeptic_triage.json plus a disk-independent coverage/evidence verify. Opt-in, advisory, adverse-only -- never touches canon.json; every ordinary skeptic-pass failure is non-blocking, EXCEPT a frozen-input hash mismatch (canon.json/manifest.json/canon_senses.json changed since setup), which the orchestrator gates as FATAL/HALT.",
  phases: [
    {
      title: "SkepticPass",
      detail: "codex adversarially examines each batch of assigned entities against their bounded source windows, resolving each to adverse/propose_split/propose_rescope/insufficient_window, and writes its own run-scoped fragment atomically, self-validated (shape + token/coverage + evidence re-auth, dropping a propose_split's unverified referents and coercing a record whose remaining evidence no longer carries its verdict down to insufficient_window) via skeptic_ready.py --validate-fragment -- never a shared file, so concurrent batches never race; each batch is then awaited by a chunked poll of that same check, bounded so no single call approaches the Bash per-call clamp and backed by one authoritative non-polling re-check; the same run's own frozen-input tripwire is also checked directly if no batch ever becomes ready, so a tamper is never missed just because dispatch itself failed",
    },
    {
      title: "Merge",
      detail: "one serialized skeptic_ready.py --merge-fragments call folds every ready batch's fragment into skeptic_triage.json in a fully deterministic order, then a disk-independent skeptic_ready.py --verify-merged call re-checks coverage, multiplicity, token/source_form/window-scoping consistency, every cited evidence record, and (when stamped) a best-effort frozen canon.json/manifest.json/canon_senses.json integrity tripwire, straight off disk, before this run reports merged:true -- a tripwire mismatch is surfaced as a distinct frozen-input-mismatch signal, not an ordinary advisory failure",
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
// #352 -- the batch wait's budget is SPENT ACROSS SEVERAL AGENT CALLS, not
// one. The same defect #348 fixed in mass-translate-wf.template.js, ported
// here; that file's own constants block carries the primary record.
//
// MEASURED, not inferred: the agent's Bash tool clamps a single call at
// 600 000 ms regardless of the timeout the agent asks for. The failing call in
// the W5 P1 gate run asked for `timeout: 3600000` and still came back
// `Exit code 143 / Command timed out after 10m 0s`. So "just raise the
// timeout" does not exist as a fix. This template's own pre-1.16.2 wait --
// `for i in $(seq 1 45); do <check> && exit 0; sleep 20; done; exit 1`, a
// 45 * 20 == 900 s loop inside ONE agent() call -- was therefore killed at
// 600 s and reported as a batch that never became ready, while a complete,
// valid fragment could already be sitting on disk unread.
//
// Chunk i (1-based) polls for whatever is LEFT of WAIT_BOUND_SEC, never a flat
// WAIT_CHUNK_SEC -- so the chunk bounds SUM to WAIT_BOUND_SEC exactly. Flat
// chunks would not SPEND the declared bound, they would silently EXTEND it
// (2 * 480 == 960 s), breaking the one contract WAIT_BOUND_SEC exists to state
// and falsifying every doc that quotes it.
//
// WAIT_BOUND_SEC is 900 s on purpose: the pre-1.16.2 loop's own 45 * 20 s
// product, preserved exactly. This change re-shapes HOW the polling budget is
// spent, never how much of it there is.
// ---------------------------------------------------------------------------
const BASH_CALL_CAP_SEC = 600              // measured hard clamp (see CHANGELOG 1.16.1)
const WAIT_CHUNK_SEC = 480                 // one chunk's own elapsed bound
const WAIT_CHUNK_TOOL_TIMEOUT_MS = 540000  // what the chunk prompt tells the agent to pass
const WAIT_BOUND_SEC = 900                 // this batch's whole polling budget
const WAIT_CHUNKS = Math.ceil(WAIT_BOUND_SEC / WAIT_CHUNK_SEC)   // 2
const WAIT_CALLS = WAIT_CHUNKS + 1         // worst case per wait: chunks + one re-check

// Startup guards, not comments: a future raise of either constant re-creates
// #352 silently otherwise. They throw here, before pipeline() is ever called.
if (WAIT_CHUNK_TOOL_TIMEOUT_MS > BASH_CALL_CAP_SEC * 1000) {
  throw new Error(
    "WAIT_CHUNK_TOOL_TIMEOUT_MS (" + WAIT_CHUNK_TOOL_TIMEOUT_MS + " ms) exceeds the measured " +
    "Bash per-call clamp (" + BASH_CALL_CAP_SEC * 1000 + " ms): the agent would be told to ask " +
    "for a timeout it cannot get, and the chunk bound would stop being the real bound (#352)."
  )
}
if (WAIT_CHUNK_SEC * 1000 >= WAIT_CHUNK_TOOL_TIMEOUT_MS) {
  throw new Error(
    "WAIT_CHUNK_SEC (" + WAIT_CHUNK_SEC + " s) leaves no headroom under " +
    "WAIT_CHUNK_TOOL_TIMEOUT_MS (" + WAIT_CHUNK_TOOL_TIMEOUT_MS + " ms): the poll must reach its " +
    "own elapsed bound and print its marker BEFORE the tool kills the call (#352)."
  )
}

function waitChunkSec(i) {
  return Math.min(WAIT_CHUNK_SEC, WAIT_BOUND_SEC - (i - 1) * WAIT_CHUNK_SEC)
}

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

// codex round 2: relays skeptic_ready.py --check-frozen-inputs's own
// {frozen_input_mismatch, missing[]} line verbatim -- REQUIRED, not
// optional, same "the command's own output always includes this field"
// discipline SKEPTIC_VERIFY_SCHEMA's own comment documents above (a
// schema-valid reply that silently DROPS it would be indistinguishable
// from `undefined === true` -> false downstream, quietly downgrading a
// real frozen-input mismatch to "nothing to report").
const SKEPTIC_FROZEN_CHECK_SCHEMA = {
  type: "object",
  additionalProperties: false,
  required: ["frozen_input_mismatch"],
  properties: {
    frozen_input_mismatch: { type: "boolean" },
    missing: { type: "array", items: { type: "string" } },
  },
}

const BATCHES = Array.isArray(args) ? args : JSON.parse(args)

// ---------------------------------------------------------------------------
// Preflight cost cap -- the same SHAPE glossary-pass-wf.template.js uses (a
// per-batch term times BATCHES.length, plus the fixed merge + verify pair
// == 2), carrying this template's OWN per-batch term: precheck 1 +
// dispatch 1 + wait WAIT_CALLS == 2 + WAIT_CALLS. Not "identical to
// glossary's", which this comment claimed until 1.16.2 and which stopped
// being true when #347 made glossary's own per-batch term conditional on its
// citation review.
//
// It was a flat 3 before 1.16.2, when a wait was ONE agent call. #352 spends
// that wait across WAIT_CHUNKS bounded chunks plus one authoritative
// re-check, so the wait term is WAIT_CALLS and the per-batch total is 5 at
// the constants above. A resumed batch whose fragment already passes
// --validate-fragment pays only the 1 precheck call, strictly cheaper, so
// this stays the true worst-case ceiling for a FRESH run. Refuses the whole
// run, dispatching nothing, if exceeded -- the caller re-plans smaller
// batches and re-runs.
//
// skeptic_setup.py's own step-5 preflight asserts this SAME number
// independently, BEFORE the Workflow ever runs (it writes nothing at all when
// it refuses). The two estimators must move together: leave one behind and
// one of them refuses a batch the other admits.
// ---------------------------------------------------------------------------
const estimatedCalls = (2 + WAIT_CALLS) * BATCHES.length + 2
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
const SENSES_PATH = ROOT + "/canon_senses.json"

// THE single ACCEPT builder for this batch. Every site that asks "is this
// batch's fragment complete and valid?" splices THIS composed command and
// never re-types it: the precheck, dispatch's own self-check, each wait chunk's
// poll, and -- since #352 -- the one authoritative non-polling re-check that
// follows an exhausted wait. A second, hand-rolled copy at any one of those
// sites would let the gate that DECIDES a batch is ready differ from the gate
// codex was told to satisfy, which is the whole reason this is a function.
//
// #243: --canon/--senses-path project the shared ambiguity-competitors
// universe (canon_senses.fold_collision_map) every cited evidence record is
// re-verified against -- see skeptic_ready.py's own module docstring. Both
// inputs feed --validate-fragment the SAME way --verify-merged already needs
// --canon (and, as of #243, --senses-path too, see verifyMergedPrompt()
// below).
//
// NOT read-only, and NOT idempotent -- see batchPrecheckPrompt()'s comment for
// the measured behaviour. Every site that splices this command must therefore
// run it exactly ONCE per decision, never in a loop that keeps calling it after
// it has already succeeded.
function checkCommand(batch) {
  return PY + " " + ROOT + "/scripts/skeptic_ready.py --validate-fragment " + fragmentPath(batch.index) +
    " --particle-config " + PARTICLE_CONFIG +
    " --expect-assignments-file " + assignmentsBatchPath(batch.index) +
    " --canon " + CANON_PATH + " --senses-path " + SENSES_PATH
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
// CURRENT assignment manifest. Unlike glossary's --check-batch, this command
// is not read-only: it also NORMALIZES the fragment in place -- any citation
// that no longer re-verifies is coerced to insufficient_window, and a
// propose_split's unverified referents are dropped from its referents[].
//
// THAT NORMALIZATION IS NOT IDEMPOTENT. Until 1.16.2 this comment claimed it
// was ("always safe, never destructive"); that claim is false, and measured to
// be false. Re-running --validate-fragment on an ALREADY-normalized fragment
// destroys the partial-evidence fact: skeptic_ready.py's _coerce_record()
// recomputes `evidence_coverage.cited` from the referent list it is handed
// (skeptic_ready.py:650), which on the second invocation is the ALREADY-PRUNED
// list -- so a record that honestly said "3 citations offered, 2 verified"
// silently becomes "2 offered, 2 verified", and the fact that a citation was
// ever rejected is gone. Nothing signals the loss: the run's own `coerced`
// count only ever counts VERDICT changes (skeptic_ready.py:740-741), and the
// verdict does not change on that second pass, so it stays 0.
//
// Fixing that non-idempotence is OUT of scope for #352, and it is NOT filed --
// as of 1.16.2 no issue tracks it. What this comment must not do is keep
// asserting the opposite of what the code does.
// The practical consequence for THIS file is the discipline stated on
// checkCommand() above and enforced at the wait site below: every splice of
// the ACCEPT command runs it exactly once per decision, and a READY verdict
// ends the wait immediately rather than spending another chunk on a fragment
// that already validated.
function batchPrecheckPrompt(batch) {
  const checkCmd = checkCommand(batch)
  const lines = []
  lines.push("A prior run of skeptic-pass batch " + batch.index + " may already have written a valid fragment to disk. Check ONCE whether it is already present and valid: run exactly this one bash command (a single invocation, NOT a polling loop):")
  lines.push(checkCmd)
  lines.push("That command is not read-only: it also normalizes the fragment in place -- any citation that fails to re-verify is downgraded to insufficient_window, and a propose_split's unverified referents are dropped from its referent list. It never fabricates a record and never drops a whole record. Run it EXACTLY ONCE and act on that one exit code: it is NOT idempotent, so a second invocation over an already-normalized fragment quietly rewrites that fragment's own record of how many citations were originally offered, and nothing in its output reports having done so.")
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
  lines.push("This command schema-validates your fragment and rejects it outright (naming every offending item) if its shape, its assignment_id/source_form pairing, or its coverage of this batch's assigned entities is wrong -- fix each one named and re-run the command until it prints a line with \"success\": true. A rejected run changes nothing on disk, so retrying after a rejection is safe; STOP at the FIRST \"success\": true and do not run the command again after that, because the successful run is the one that rewrites your fragment and running it a second time over the already-rewritten file loses information (it is not idempotent).")
  lines.push("What that successful run rewrites: it independently re-authenticates every citation you gave, downgrades any that does not check out to insufficient_window, and drops a propose_split's unverified referents from its referent list, writing the result back in place. A \"success\": true result with a nonzero \"coerced\" count just means some of your citations were not verifiable; this is a normal, safe, and expected outcome, never something you need to fix or re-litigate.")
  lines.push("Once you see \"success\": true, return exactly the line: FRAGMENT " + batch.index)
  return lines.join("\n")
}

// WAIT -- Claude, effort:low, no agentType, no schema: ONE CHUNK of a bounded
// poll of the SAME --validate-fragment ACCEPT command DISPATCH's self-check
// already used, against this batch's own fragment. Ported from
// mass-translate-wf.template.js's waitChunkPrompt() (#348); see that function's
// comment for the primary record of the bash grammar's own properties. What is
// this file's own is the ACCEPT gate (checkCommand()) and the absence of a
// fail-fast sentinel: the skeptic dispatch is a direct codex agent() call, not
// a detached codex_job.py driver, so there is no `.codex_failed.*` file for a
// chunk to report and the chunk's only two outcomes are READY and PENDING.
//
// `>/dev/null 2>&1` ON THE IN-LOOP ACCEPT GATE IS LOAD-BEARING, not tidiness.
// skeptic_ready.py --validate-fragment prints one JSON line per invocation, so
// without the redirect the chunk emits one such line per poll iteration and
// "the marker is the last line" would be a claim about the tail of a noisy
// stream. Suppressed, the chunk emits exactly zero or one line and that line is
// the marker. The gate's EXIT STATUS -- the only thing this workflow acts on --
// is unaffected by the redirect.
//
// Marker-plus-`exit 1` rather than distinct exit codes, deliberately: a
// TOOL-KILLED chunk (exit 143, no marker printed) becomes indistinguishable
// from a chunk that merely ran out of budget. That is exactly the safe reading:
// not ready yet, keep polling.
function batchWaitChunkPrompt(batch, chunkIndex) {
  const checkCmd = checkCommand(batch)
  const lines = []
  lines.push("The codex skeptic-pass batch " + batch.index + " is working in the background. This is wait chunk " + chunkIndex + " of " + WAIT_CHUNKS + " -- one bounded slice of this batch's total " + WAIT_BOUND_SEC + "s wait, sized so a single bash call never approaches the " + BASH_CALL_CAP_SEC + "s per-call cap.")
  lines.push("Run EXACTLY ONE bash command, passing a bash tool timeout of " + WAIT_CHUNK_TOOL_TIMEOUT_MS + " ms -- an elapsed-time poll that re-validates this batch's fragment directly:")
  lines.push("end=$((SECONDS + " + waitChunkSec(chunkIndex) + ")); while true; do " + checkCmd + " >/dev/null 2>&1 && exit 0; [ $SECONDS -ge $end ] && break; slp=$((end-SECONDS)); [ $slp -gt 20 ] && slp=20; [ $slp -gt 0 ] && sleep $slp; done; echo LT_CHUNK_BOUND; exit 1")
  lines.push("If that command exits 0 (the fragment validated), return exactly the line: READY " + batch.index)
  lines.push("In every other case -- it printed LT_CHUNK_BOUND, or the call was cut short for any reason at all -- return exactly the line: PENDING " + batch.index)
  lines.push("Do nothing else -- do not touch any files, and do not resolve any entity yourself.")
  return lines.join("\n")
}

// #352 -- THE FIX. After the chunk budget is spent, re-check this batch's
// fragment ONCE, without polling, before declaring the batch not-ready.
//
// Chunking alone would only have moved the wall the poll dies against. The
// hole it leaves open is the one that actually loses work: a codex batch that
// finishes after the last chunk's poll ended has a complete, valid fragment on
// disk that nothing ever reads, and the run reports skeptic-pass-null over it.
//
// Non-polling by construction -- no `end=`, no loop, no sleep. A polling
// re-check would just be one more chunk and could itself hit the cap.
//
// Runs at most ONCE per batch, and only on the path where no chunk returned
// READY -- so the normal path's count of write-capable --validate-fragment
// invocations is unchanged by #352 (see batchPrecheckPrompt()'s comment on why
// that count matters).
function batchWaitRecheckPrompt(batch) {
  const checkCmd = checkCommand(batch)
  const lines = []
  lines.push("The " + WAIT_BOUND_SEC + "s wait budget for the codex skeptic-pass batch " + batch.index + " is spent. Before this batch is declared not-ready, re-check its fragment ONCE -- it may have landed after the last wait chunk's poll ended.")
  lines.push("Run EXACTLY ONE bash command. It does NOT poll and returns immediately:")
  lines.push(checkCmd + " >/dev/null 2>&1")
  lines.push("If that command exits 0 (the fragment validated), return exactly the line: READY " + batch.index)
  lines.push("Otherwise return exactly the line: PENDING " + batch.index)
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
  // #243: --senses-path joins the already-present --canon -- merged
  // verification projects the SAME ambiguity-competitors universe
  // --validate-fragment's per-batch check does, and also re-hashes the
  // sidecar (when the aggregate manifest stamps senses_sha256) as a third
  // H1 tamper tripwire alongside canon.json/manifest.json.
  const cmd = PY + " " + ROOT + "/scripts/skeptic_ready.py --verify-merged " + SKEPTIC_TRIAGE_PATH + " " + AGGREGATE_ASSIGNMENTS_PATH + " --particle-config " + PARTICLE_CONFIG + " --manifest-path " + MANIFEST_PATH + " --canon " + CANON_PATH + " --senses-path " + SENSES_PATH
  lines.push("Run exactly this command and read its one line of JSON output: " + cmd)
  lines.push("Return a structured result with exactly these fields: verified (the command's own verified value), frozen_input_mismatch (the command's own frozen_input_mismatch value, copied verbatim -- it is always present in the command's output), and, only when the command's own output actually includes it, missing (the command's own missing array, copied verbatim). Do not add, omit, or alter any value the command printed.")
  return lines.join("\n")
}

// Frozen-input check -- Claude, effort:low, no agentType, schema:
// SKEPTIC_FROZEN_CHECK_SCHEMA. Disk-independent, TRIAGE-independent: codex
// round 2 found that when every batch's own fragment fails to become ready
// (the notReadyBatches branch below), this pipeline gave up with an
// ordinary advisory `fragment-check-failed` and NEVER called
// verifyMergedPrompt() at all -- so a sidecar tampered sometime after
// skeptic_setup.py stamped this run but before any batch's fragment ever
// validated went completely unreported as the FATAL tamper it is. This is
// the SAME skeptic_ready.py H1 tripwire verifyMergedPrompt() already
// applies (--check-frozen-inputs is a standalone mode built from the exact
// same shared frozen_input_check() Python function --verify-merged calls
// internally), run at THIS decision point too, unconditionally, so no path
// through this pipeline can reach a final verdict without having consulted
// it.
function frozenInputCheckPrompt() {
  const lines = []
  lines.push("Effort: low. Mechanical disk-independent frozen-input tamper check only -- do not judge anything yourself.")
  lines.push("Durable root: " + ROOT + ".")
  const cmd = PY + " " + ROOT + "/scripts/skeptic_ready.py --check-frozen-inputs " + AGGREGATE_ASSIGNMENTS_PATH + " --canon " + CANON_PATH + " --senses-path " + SENSES_PATH + " --manifest-path " + MANIFEST_PATH
  lines.push("Run exactly this command and read its one line of JSON output: " + cmd)
  lines.push("Return a structured result with exactly these fields: frozen_input_mismatch (the command's own frozen_input_mismatch value, copied verbatim -- it is always present in the command's output), and, only when the command's own output actually includes it, missing (the command's own missing array, copied verbatim). Do not add, omit, or alter any value the command printed.")
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

// Line-oriented sentinel verdict (#308), mirrored byte-for-byte across the
// three workflow templates (standalone files, no runtime imports; parity is
// test-pinned). Replaces the #228 whole-string exact match: #228 killed the
// substring false-POSITIVE ("TIMEOUT seg01 (not READY)" passing an indexOf
// check); its whole-string cure then rejected a benign prose-decorated
// success ("...exit 0.\n\nREADY seg03"), mislabeling completed work as a
// timeout (#308). True iff (a) no trimmed non-empty line equals
// failSentinel, AND (b) the LAST trimmed non-empty line equals okSentinel
// exactly. Requiring okSentinel to be the FINAL line (round-2 fix -- an
// earlier "any line" draft accepted a reply that quotes the success form
// while explicitly disavowing it, e.g. "The command failed; quoting the
// requested success form:\nREADY seg01\nThat is not my verdict." -- the
// shipped whole-string check rejects that reply, so "any line" would have
// been a genuine widening of what gets accepted, not just a decoration
// tolerance) tolerates a prose PREAMBLE (the observed real shape) while
// rejecting a sentinel-shaped line the agent's own later prose overrides.
// The failure-sentinel check still scans every line, not just the last, so
// fail-priority on a contradictory reply is unchanged. A reply with no
// non-empty lines is false. This parses only the agent's transport reply;
// nothing else about any call site changes.
function sentinelVerdict(reply, okSentinel, failSentinel) {
  const rawLines = String(reply == null ? "" : reply).split("\n");
  const lines = [];
  for (let i = 0; i < rawLines.length; i++) {
    const line = rawLines[i].trim();
    if (line.length === 0) continue;
    if (failSentinel !== null && line === failSentinel) return false;
    lines.push(line);
  }
  if (lines.length === 0) return false;
  return lines[lines.length - 1] === okSentinel;
}

// Containment guard for the FAIL direction, ported into this template in
// 1.16.2. Its body is pinned byte-identical to the copies in
// mass-translate-wf.template.js and glossary-pass-wf.template.js; only this
// comment is this file's own, because the call site it protects is.
//
// WHY sentinelVerdict() ALONE IS NOT ENOUGH HERE. That function recognises a
// fail sentinel only when the sentinel is ALONE on its LF-delimited line after
// trim(). Anything sharing that line -- a parenthetical, a trailing clause --
// defeats the rejection while a later clean OK line still approves the reply.
// The concrete divergence, measured against the glossary sibling before this
// port: "PENDING 0 (not READY)\nREADY 0" read as READY here and as pending
// there. That sits exactly on the false-GREEN boundary, and this template was
// the permissive side.
//
// It matters MORE here than the single-reply arithmetic suggests. Before
// 1.16.2 one reply per batch was read this way; #352 spends the wait across
// WAIT_CALLS replies, so the same permissive read now gets up to three chances
// per batch to manufacture a READY out of a reply that was reporting the
// opposite.
//
// An empty or non-string failSentinel returns false rather than matching
// everything: "".indexOf("") is 0, so an unguarded containment test would
// reject every reply unconditionally.
function rejectedAnywhere(reply, failSentinel) {
  if (typeof failSentinel !== "string" || failSentinel.length === 0) return false
  return String(reply == null ? "" : reply).indexOf(failSentinel) !== -1
}

// #352 -- the ONE reader of every wait reply at the wait site below, chunk and
// re-check alike. Two verdicts only, because a skeptic wait chunk has only two
// outcomes: this template dispatches codex through a direct agent() call rather
// than a detached codex_job.py driver, so there is no third, driver-failure
// token for a chunk to report (see batchWaitChunkPrompt()).
//
// GUARD ORDER IS THE POINT, and it is the sibling templates' order exactly:
// the PENDING guard runs FIRST and by CONTAINMENT, so a stray PENDING anywhere
// in the reply biases AWAY from READY; only then is READY tested, by WHOLE-LINE
// equality, so a READY can never be manufactured out of a mention. Asymmetric
// on purpose -- the two directions have different costs, and the cheap one is
// polling a little longer.
//
// EVERYTHING ELSE IS "pending", and that default is the load-bearing half. A
// null reply, an unparseable one, or one from a chunk the tool killed mid-call
// is not evidence that the fragment failed -- only that this chunk learned
// nothing. Before 1.16.2 the wait site treated EVERY non-READY reply as
// terminal, so a single ambiguous reply ended the whole wait and lost the
// batch; now it costs one chunk, the remaining chunks still poll, and the
// authoritative re-check still runs at the end.
//
// KNOWN COLLISION, recorded rather than closed, and identical to the one the
// glossary sibling documents: batch indices are bare integers, so one can
// prefix another (1 / 10), and raw containment means "PENDING 10" trips batch
// 1's guard. FALSE-RED ONLY -- READY stays whole-line, so no false green can
// come of it -- and the same exposure already existed for TIMEOUT before
// 1.16.2. Its cost is that batch 1 may abandon its remaining chunk budget
// early; the authoritative re-check still runs, so a fragment that genuinely
// landed is still found. Unreachable in practice: each wait agent's prompt
// names only its own batch.
function waitChunkVerdict(reply, index) {
  if (rejectedAnywhere(reply, "PENDING " + index)) return "pending"
  if (sentinelVerdict(reply, "READY " + index, null)) return "ready"
  return "pending"
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
  // Line-oriented sentinel verdict (#308), replacing the whole-string EXACT
  // match this site used before (content-matching-sentinel-fragility): a
  // failure reply like "ABSENT 0 (fragment missing; not PRESENT)" contains
  // the literal substring "PRESENT" and would falsely resume-skip under a
  // naive `.indexOf(...) !== -1` check -- the earlier whole-string exact
  // match closed that direction, but then rejected a benign prose-decorated
  // PRESENT reply as ABSENT (#308). sentinelVerdict() keeps BOTH directions
  // closed at once: a decorated PRESENT (prose preamble, the sentinel as
  // the reply's own final line) now resume-skips, while a plain ABSENT or a
  // contradictory reply still regenerates (see sentinelVerdict()'s own
  // comment for the exact rule).
  if (sentinelVerdict(precheck, "PRESENT " + batch.index, "ABSENT " + batch.index)) {
    log("batch " + batch.index + ": resume-skip -- existing fragment already passed --validate-fragment, not re-dispatching")
    return { batchIndex: batch.index, fragmentPath: fragmentPath(batch.index), ready: true }
  }

  await agent(batchDispatchPrompt(batch), {
    agentType: "codex:codex-rescue",
    effort: "high",
    phase: "SkepticPass",
    label: "skeptic:dispatch:" + batch.index,
  })

  // #352 -- the wait is CHUNKED across WAIT_CHUNKS agent calls (the Bash tool
  // clamps any single call at BASH_CALL_CAP_SEC, so the pre-1.16.2 single
  // 900 s poll was killed at 600 s and the batch reported not-ready over a
  // fragment that may well have been valid on disk), then backed by ONE
  // authoritative non-polling re-check. The chunk calls keep the EXISTING
  // label `skeptic:wait:<index>` unchanged; only the re-check gets a new one.
  //
  // The break is conditioned on the VERDICT, never on the loop index: with
  // waitChunkVerdict()'s two verdicts, "not pending" is exactly READY, so a
  // READY from ANY chunk -- the first, the last, or one in between -- ends the
  // wait immediately, with no later chunk and no re-check. That is a
  // correctness requirement, not a saved call: each of those would re-run the
  // write-capable, NON-idempotent --validate-fragment over a fragment that has
  // already validated (see batchPrecheckPrompt()'s comment for what that
  // destroys).
  let verdict = "pending"
  for (let chunk = 1; chunk <= WAIT_CHUNKS; chunk++) {
    const chunkReply = await agent(batchWaitChunkPrompt(batch, chunk), {
      effort: "low", phase: "SkepticPass", label: "skeptic:wait:" + batch.index,
    })
    verdict = waitChunkVerdict(chunkReply, batch.index)
    if (verdict !== "pending") break
  }
  // The authoritative re-check, conditioned on the VERDICT the loop ended with
  // rather than on how many chunks ran: it fires only when no chunk ever
  // returned READY, and its own verdict -- not the loop's -- decides the batch.
  if (verdict !== "ready") {
    const recheck = await agent(batchWaitRecheckPrompt(batch), {
      effort: "low", phase: "SkepticPass", label: "skeptic:wait-recheck:" + batch.index,
    })
    verdict = waitChunkVerdict(recheck, batch.index)
  }
  // Every reply at this site -- chunk or re-check -- is read by
  // waitChunkVerdict() and nothing else; this call site deliberately does not
  // re-implement any part of the reading. Both of its guards matter here: the
  // #308 whole-line READY test (inherited from sentinelVerdict(), so a
  // prose-decorated READY with the sentinel as the reply's final line is
  // accepted) and the containment PENDING guard ahead of it (so a glued
  // "PENDING 0 (not READY)" still reads as pending even when a clean READY
  // line follows it).
  if (verdict !== "ready") {
    log("batch " + batch.index + ": fragment never became ready")
    return { batchIndex: batch.index, fragmentPath: fragmentPath(batch.index), ready: false, reason: "skeptic-pass-null" }
  }
  return { batchIndex: batch.index, fragmentPath: fragmentPath(batch.index), ready: true }
}

const batchResults = await pipeline(BATCHES, batchStep)

const readyBatches = batchResults.filter((r) => r && r.ready)
const notReadyBatches = batchResults.filter((r) => !r || !r.ready)

if (notReadyBatches.length > 0) {
  // codex round 2: this branch never reaches verifyMergedPrompt()'s own H1
  // check below (merge+verify is never attempted once any batch failed to
  // become ready) -- run the SAME frozen-input tripwire here explicitly,
  // unconditionally, before deciding this is merely an ordinary advisory
  // outcome.
  const frozenCheck = await agent(frozenInputCheckPrompt(), {
    effort: "low", phase: "SkepticPass", label: "skeptic:frozen-check", schema: SKEPTIC_FROZEN_CHECK_SCHEMA,
  })
  if (frozenCheck && frozenCheck.frozen_input_mismatch === true) {
    const missingDetail = Array.isArray(frozenCheck.missing) ? frozenCheck.missing : null
    log(
      "Skeptic pass: FROZEN-INPUT MISMATCH -- canon.json/manifest.json/canon_senses.json changed since " +
      "skeptic_setup.py stamped this run's hashes, detected before any batch fragment became ready" +
      (missingDetail && missingDetail.length ? " -- " + missingDetail.join(", ") : "") + "."
    )
    return {
      batches: batchResults, merged: false, reason: "frozen-input-mismatch",
      missing: missingDetail, frozenInputMismatch: true,
    }
  }
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
  // manifest.json/canon_senses.json changed since skeptic_setup.py stamped
  // this run) must be surfaced as a DISTINCT reason/flag, never folded into
  // the generic "verify-failed" bucket every other skeptic-pass failure (batch-too-
  // large / fragment-check-failed / an ordinary coverage gap or unverified
  // citation) shares -- SKILL.md's exit-contract gates FATAL/HALT on this
  // signal specifically, keeping every other skeptic-pass failure advisory.
  if (verified && verified.frozen_input_mismatch === true) {
    log(
      "Skeptic pass: FROZEN-INPUT MISMATCH -- canon.json/manifest.json/canon_senses.json changed since " +
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
