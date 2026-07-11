// glossary-pass-wf.template.js -- literary-translator plugin
//
// GENERATED-ONLY template (see references/canon-and-glossary.md and
// references/orchestration-and-batching.md, section "The glossary-pass
// template" -- read those for the full mechanism this file implements).
// Instantiated FRESH from the plugin's current copy every time W3's
// glossary pass runs -- never reused stale across runs, exactly like
// mass-translate-wf.template.js. ${durable_root}/runs/.plugin_bundle_hash
// covers this file specifically, so a plugin update to this template is
// never silently masked by an old generated copy surviving on disk.
//
// Storage location once instantiated (pinned):
//   ${durable_root}/runs/workflows/<run_id>/glossary-pass-wf.js
//
// 1.2.0 (#87 #88 #90 #97): rebuilt on the same dispatch -> bounded-wait ->
// schema-validated-consume discipline translate already used, instead of a
// single schema-validated codex call per batch (see
// references/workflow-schema-validation.md's "shared codex work-call
// pattern"). Each batch's own fragment is now written to a run-scoped path,
// never a shared file, so concurrent batches never race on the same bytes
// (#90); the batch call carries no `agent()` schema at all, so a
// forwarder-detached job can no longer wedge this Workflow (#97); and the
// eventual merge into canon.json is independently re-verified straight off
// disk afterward, never trusted from an agent's own self-report (#88).
//
// Substitution tokens this template documents (resolved ONCE by the
// orchestrating Claude session at instantiation time, before the Workflow
// tool ever executes this file -- there is no templating engine at
// Workflow-runtime, so every token below must already be resolved in the
// generated file before it runs; tests/workflow_template_instantiation
// .test.py greps the instantiated output for a leftover double-curly-brace
// pair and asserts zero matches, running this template's case twice, once
// per research_mode value):
//   {{SOURCE_LANG}}   -- e.g. "French"
//   {{TARGET_LANG}}   -- e.g. "Russian"
//   {{DURABLE_ROOT}}  -- the project's durable_root, an absolute path
//   {{RESEARCH_MODE}} -- profile.yml's glossary.research_mode, "live" or
//                        "offline", resolved once and passed through
//                        literally to canon_validate.py's own
//                        --research-mode flag; this script never parses
//                        YAML itself.
//   {{RUN_ID}}        -- this run's own id, resolved once by the
//                        orchestrating session (fresh on a fresh run, the
//                        SAME value again on a resumed one -- see
//                        references/ledger-and-resumability.md). Names the
//                        run-scoped directory every fragment/manifest this
//                        script touches lives under; stable under
//                        resumeFromRunId.
//   {{BATCH_AGENT_CAP}} -- engine.batch_agent_cap, the SAME profile field
//                        mass-translate-wf.template.js reads, substituted as
//                        a BARE integer (never a quoted string). Feeds the
//                        preflight cost cap below, which refuses to dispatch
//                        a glossary run whose worst-case agent-call estimate
//                        (3*BATCHES.length + 2) would exceed it -- the same
//                        refusal mass-translate-wf.template.js makes for its
//                        own oversized batch (#95).
//
// `args` shape this template expects (an array, or a JSON string of one):
//   [ { index: 0, candidates: [ {name, freq, mid_sentence, multiword,
//       abbrev, n_segments, likely_name}, ... ] },
//     { index: 1, candidates: [...] }, ... ]
// Each candidates[] row is one bootstrap_names.py candidate row, taken
// as-is from name_candidates.json. Batch construction -- curating which
// candidates survive (excluding every source_form already present in the
// CURRENT canon.json's entries{} map AND every non-retried review_queue
// entry, applying the frequency floor, and force-including flagged
// elision-ambiguous pairs) and chunking the survivors into batches -- is the
// orchestrating session's job, performed by scripts/glossary_batch_plan.py
// before it ever calls the Workflow tool, never this script's own job
// (canon.json itself is the citation cache; see
// references/canon-and-glossary.md's "Citation cache" section).
//
// Deterministic PRE-WORKFLOW setup (the orchestrating session's own
// resume_setup.py call, run BEFORE the Workflow tool ever executes this
// file -- never this script's own job, and never redone here): by the time
// this script runs, ${durable_root}/glossary/runs/{{RUN_ID}}/ already
// exists, and it already holds, for every batch in `args`, an atomically
// written manifest_{index}.json (that batch's own candidates[].name list,
// verbatim, as a JSON array of strings) plus one aggregate
// manifest_all.json (the union of every batch's manifest). This script
// never creates that directory or those manifest files, and never trusts
// anything BUT them for coverage -- a codex batch call can't pass its own
// self-check by quietly omitting a candidate, because the manifest it is
// checked against was written independently, before the batch was ever
// dispatched.

export const meta = {
  name: "literary-translator-glossary-pass",
  description: "Batch candidate proper names/realia (bootstrap_names.py output, already filtered against the current canon.json) through a fire-and-forget codex-rescue call each, writing a run-scoped fragment per batch, then one serialized merge into canon.json plus a disk-independent verify.",
  phases: [
    {
      title: "GlossaryPass",
      detail: "codex resolves each batch of candidates into a canon-batch.schema.json-shaped array and writes it, atomically, to its own run-scoped fragment file, self-validated shape-and-coverage via canon_validate.py --check-batch -- never a shared file, so concurrent batches never race",
    },
    {
      title: "Merge",
      detail: "one serialized canon_validate.py --merge-batches call folds every ready batch's fragment into canon.json in index order, then a disk-independent canon_validate.py --verify-merged call re-checks the result straight off disk before this run reports merged:true",
    },
  ],
}

const ROOT = "{{DURABLE_ROOT}}"
const PY = "python3"
const SOURCE_LANG = "{{SOURCE_LANG}}"
const TARGET_LANG = "{{TARGET_LANG}}"
const RESEARCH_MODE = "{{RESEARCH_MODE}}"
const RUN_ID = "{{RUN_ID}}"
const RUN_DIR = ROOT + "/glossary/runs/" + RUN_ID
const BATCH_AGENT_CAP = {{BATCH_AGENT_CAP}}

// ---------------------------------------------------------------------------
// Schema literal -- declared ABOVE the pipeline() call at the bottom of this
// file. A schema declared after its first use silently no-ops due to
// temporal-dead-zone semantics in this execution model (see
// references/workflow-schema-validation.md's TDZ gotcha,
// gotcha_workflow_const_tdz_silent_fail) -- declaration order in this file
// is load-bearing. This is the ONE inline schema literal
// glossary-pass-wf.template.js owns (the other four -- REVIEW_SCHEMA,
// REVIEW_ARTIFACT_SCHEMA, LEDGER_WRITE_SCHEMA, LEDGER_MERGE_SCHEMA -- belong
// to mass-translate-wf.template.js instead). CANON_BATCH_SCHEMA is GONE
// (#87): the batch dispatch call below is schema-less fire-and-forget, so
// there is no agent-facing literal for it at all any more; the on-disk
// canon-batch.schema.json stays an array and is validated only by
// canon_validate.py --check-batch, never by an agent() call. Flat, no
// top-level combinator, matching the shipped CANON_VERIFY_SCHEMA that
// relays canon_validate.py --verify-merged's own {verified, missing[]}
// line -- see references/workflow-schema-validation.md's #87 section for
// why this must be a plain type:"object" (the tool-use API's own
// input_schema requirement).
// ---------------------------------------------------------------------------

const CANON_VERIFY_SCHEMA = {
  type: "object",
  additionalProperties: false,
  required: ["verified"],
  properties: {
    verified: { type: "boolean" },
    missing: { type: "array", items: { type: "string" } },
  },
}

const BATCHES = Array.isArray(args) ? args : JSON.parse(args)

// ---------------------------------------------------------------------------
// Preflight cost cap (#95). Worst-case agent-call count for a FRESH run:
// per batch, precheck + dispatch + wait == 3 (a resumed batch whose fragment
// already passes --check-batch pays only the 1 precheck call, strictly
// cheaper, so this is the true ceiling), plus the fixed merge + verify pair
// == 2. So estimatedCalls = 3*BATCHES.length + 2. If that exceeds
// engine.batch_agent_cap, refuse the whole run WITHOUT dispatching anything,
// the same refusal shape mass-translate-wf.template.js emits for its own
// oversized batch -- the caller re-plans smaller batches
// (glossary_batch_plan.py's --batch-size) and re-runs. Counted in BATCHES,
// never candidates-per-batch, so a co-located elision pair nudging one batch
// slightly over its nominal size never trips this. Placed before the
// index-guard loop below on purpose: a refused run dispatches nothing, so
// there is no unsafe index to guard against yet.
// ---------------------------------------------------------------------------
const estimatedCalls = 3 * BATCHES.length + 2
if (estimatedCalls > BATCH_AGENT_CAP) {
  log(
    "Batch too large: estimatedCalls=" + estimatedCalls +
    " exceeds engine.batch_agent_cap=" + BATCH_AGENT_CAP +
    " for " + BATCHES.length + " glossary batch(es)."
  )
  return { merged: false, reason: "batch-too-large", estimatedCalls: estimatedCalls, cap: BATCH_AGENT_CAP }
}

// ---------------------------------------------------------------------------
// Defense-in-depth batch-index guard. Every batch's index is spliced,
// unquoted, into shell command strings and file paths below
// (batchDispatchPrompt/batchWaitPrompt, and the final merge/verify
// commands) -- an unsafe or duplicate index would otherwise collide two
// batches' fragment paths onto the same file, or escape into an injected
// shell command. Checked BEFORE any write or dispatch: a bad/duplicate
// index throws here, so nothing is ever dispatched against it. Mirrors
// mass-translate-wf.template.js's own SEG_ID_RE guard discipline exactly.
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
// Run-scoped path helpers. RUN_DIR, and every manifest inside it, already
// exist by the time this script runs -- see the header comment's
// "Deterministic PRE-WORKFLOW setup" section. This script only ever reads
// the manifests and writes/reads its own out_{index}.json fragments; it
// never creates RUN_DIR itself.
// ---------------------------------------------------------------------------
function fragmentPath(index) {
  return RUN_DIR + "/out_" + index + ".json"
}
function manifestPath(index) {
  return RUN_DIR + "/manifest_" + index + ".json"
}
const MANIFEST_ALL_PATH = RUN_DIR + "/manifest_all.json"

// ---------------------------------------------------------------------------
// Prompt-builder functions. Plain string concatenation throughout, never a
// backtick template literal -- see mass-translate-wf.template.js's own
// header comment for why (natural-language prose below routinely needs
// literal quotes).
// ---------------------------------------------------------------------------

// PRECHECK -- Claude, effort:low, no agentType, no schema. Resume-skip
// (#101): a prior, possibly-interrupted run of this SAME {{RUN_ID}} may have
// already written a valid out_{index}.json fragment. Because any plugin
// update flips plugin_bundle_hash (this template is itself a
// PLUGIN_BUNDLE_MEMBERS entry) and so forces a fresh run_id with no old
// fragments on disk, ANY fragment that still passes --check-batch against
// the CURRENT manifest is genuinely current, never stale -- so it can be
// trusted and the (expensive) codex dispatch skipped. A single-shot run of
// the SAME --check-batch command batchWaitPrompt polls; any failure at all
// (missing file, malformed JSON, wrong coverage, offline backstop) makes
// this return ABSENT, so the batch falls THROUGH to a normal dispatch +
// wait and a bad or absent fragment is never wrongly trusted.
function batchPrecheckPrompt(batch) {
  const outPath = fragmentPath(batch.index)
  const manifestFile = manifestPath(batch.index)
  const checkCmd = PY + " " + ROOT + "/scripts/canon_validate.py --check-batch " + outPath + " --research-mode " + RESEARCH_MODE + " --expect-source-forms-file " + manifestFile
  const lines = []
  lines.push("A prior run of glossary-pass batch " + batch.index + " may already have written a valid fragment to disk. Check ONCE, read-only, whether it is already present and valid: run exactly this one bash command (a single invocation, NOT a polling loop):")
  lines.push(checkCmd)
  lines.push("If that command exits successfully (exit code 0), the fragment is already complete and valid -- return exactly the line: PRESENT " + batch.index)
  lines.push("If it exits non-zero for ANY reason (the file is missing, is not valid JSON, or fails its shape/offline/coverage checks), return exactly the line: ABSENT " + batch.index)
  lines.push("Do nothing else -- do not create, modify, dispatch, or resolve any candidates yourself; this is purely a read-only presence check.")
  return lines.join("\n")
}

// DISPATCH -- codex, schema-less, fire-and-forget (see
// references/workflow-schema-validation.md's "shared codex work-call
// pattern"). Writes this batch's fragment ATOMICALLY to its own run-scoped
// path and self-validates shape + exact candidate coverage via
// canon_validate.py --check-batch, against the manifest the pre-workflow
// setup already wrote -- never against a shell-embedded candidate list this
// agent could quietly omit from. The Workflow JS never depends on this
// call's own return value; only the WAIT step's disk-backed poll below does.
function batchDispatchPrompt(batch) {
  const candidatesJson = JSON.stringify(batch.candidates, null, 1)
  const outPath = fragmentPath(batch.index)
  const manifestFile = manifestPath(batch.index)
  const lines = []
  lines.push("Effort: high. Canon-and-glossary pass (codex-glossary-pass) for a " + SOURCE_LANG + " -> " + TARGET_LANG + " literary translation project, batch " + batch.index + ".")
  lines.push("Read in full, in this order: " + ROOT + "/glossary_TASK.md (the canonicalization rules and the exact per-item output contract) and " + ROOT + "/canon.json (the entries already frozen there). Never re-decide or override any source_form already present in canon.json's own entries{} -- this batch resolves only the new candidates listed below, which were already filtered against the current canon.json before you were dispatched.")
  lines.push("research_mode = " + RESEARCH_MODE + ". If it is \"offline\": basis:\"established\" is forbidden outright for every candidate in this batch, with no exception -- use basis:\"transliterated\" when the fixed practical-transcription rule in style_bible.md (section C-translit) is enough on its own, or set disposition:\"review_queue\" instead, with a note that starts with the literal prefix \"SOURCE_UNAVAILABLE:\". If it is \"live\": basis:\"established\" is allowed, but only together with a real, citable reference source URL -- never a fabricated one.")
  lines.push("This batch's candidates -- deterministically extracted by bootstrap_names.py, never yet decided by any LLM (name = the surface form as it appears in the source text; freq/n_segments = how often and how widely it recurs; likely_name/multiword/mid_sentence/abbrev = this script's own recall-oriented heuristics, not a verdict; elision_ambiguous/elision_stripped_form = present only on some rows, flagging a possible article-elision ambiguity resolved by the adjudication rule below):")
  lines.push(candidatesJson)
  lines.push("For EVERY candidate above, in the SAME order, decide exactly one canon-batch item:")
  lines.push("- source_form: the candidate's own \"name\" field, copied verbatim.")
  lines.push("- is_proper_name: false when the candidate is not actually a proper name at all (a frequent common word, an interjection, a bare title, or a sentence-initial capitalization artifact) -- such a candidate always gets disposition:\"review_queue\" too, never disposition:\"accepted\".")
  lines.push("- disposition: \"accepted\" once you have a confident resolution; \"review_queue\" whenever it still needs a human's later attention -- a disputed transcription, several different historical people sharing one surname, not enough context in this batch alone, a non-name candidate as above, or the offline SOURCE_UNAVAILABLE case above.")
  lines.push("- When disposition is \"accepted\": canonical_target_form, basis (\"established\" | \"transliterated\" | \"title\" | \"not_a_name\"), and confidence (\"high\" | \"medium\" | \"low\") are all required; when basis is \"established\", source is also required and must be a real, non-empty reference URL, never left empty and never invented.")
  lines.push("- When disposition is \"review_queue\": note is required and must explain, briefly, why the candidate is queued rather than resolved.")
  lines.push("- A title phrase (an honorific plus a bare surname or role -- for instance a form meaning \"Monsieur the Prince\" or \"the Queen Mother\") gets basis:\"title\", with canonical_target_form holding the unpacked target-language phrase; if the underlying surname is ALSO present as its own separate candidate in this same batch, resolve that one on its own merits instead of folding it into the title entry.")
  lines.push("- ELISION AMBIGUITY: when a candidate row carries elision_ambiguous:true, it is a capitalized, sentence-initial form that MIGHT merely be an article-elision of another name rather than a distinct name of its own (its elision_stripped_form field names that other form -- e.g. \"L'Enclos\", whose elision_stripped_form is \"Enclos\"). Do NOT silently accept such a row as a standalone proper name: unless you can positively confirm from context that it genuinely IS its own distinct entity, set disposition:\"review_queue\" with a note that names its elision_stripped_form, so a human can decide whether the two forms are the same entity. Only when you are confident it is a separate name may you resolve it as accepted.")
  lines.push("- NICKNAMES, EPITHETS, AND ALIASES: only true orthographic spelling variants of the same surface name (for instance \"Sarrasin\" and \"Sarrazin\") may ever share one canonical_target_form. A salon nickname, epithet, sobriquet, or alias is its OWN surface form -- resolve its own canonical_target_form under the basis rules above, on its own merits (usually basis:\"transliterated\", or basis:\"established\" if a genuinely established form exists for the nickname itself), and NEVER give it the referent's real-name canonical_target_form, no matter how well-known the identity link is. If it cannot be resolved as its own form, set disposition:\"review_queue\" with a note instead of fabricating a basis -- record any known identity link only in that note, never by collapsing the two forms together.")
  lines.push("Write this exact JSON array, in this exact order, to " + outPath + " ATOMICALLY: write it first to a fresh temp file in the SAME directory (for example a dot-prefixed name alongside the target, holding your own process id), then rename that temp file into place at exactly " + outPath + " -- so a partially-written file is never visible at that path. A plain JSON array of objects, no markdown code fence, no comment, nothing else in the file.")
  lines.push("Then self-check by running this command and reading its one line of JSON output: " + PY + " " + ROOT + "/scripts/canon_validate.py --check-batch " + outPath + " --research-mode " + RESEARCH_MODE + " --expect-source-forms-file " + manifestFile)
  lines.push("This command checks only this fragment's own shape, the offline backstop, and its EXACT candidate coverage against the manifest file above -- it does NOT merge into canon.json; a separate, later, serialized step folds every batch's confirmed-ready fragment into canon.json only once every batch here is done. If it prints a line with \"success\": false, it names every offending item -- fix each one in your own array (reassign basis/disposition/note as the rules above require; never weaken the offline backstop, never fabricate a source URL to make the check pass, never drop or add a candidate), rewrite " + outPath + " the same atomic way, and re-run the command. Repeat until it prints a line with \"success\": true. This self-check command supersedes any older self-check prose you may find in glossary_TASK.md from a prior plugin version -- always run exactly the command above, never --batch.")
  lines.push("Once you have that success line, return exactly the line: FRAGMENT " + batch.index)
  return lines.join("\n")
}

// WAIT -- Claude, effort:low, no agentType, no schema: a bounded poll of the
// SAME --check-batch command DISPATCH's self-check already used, against
// this batch's own fragment (the translate/review wait steps' shape --
// see mass-translate-wf.template.js's waitPrompt).
function batchWaitPrompt(batch) {
  const outPath = fragmentPath(batch.index)
  const manifestFile = manifestPath(batch.index)
  const checkCmd = PY + " " + ROOT + "/scripts/canon_validate.py --check-batch " + outPath + " --research-mode " + RESEARCH_MODE + " --expect-source-forms-file " + manifestFile
  const lines = []
  lines.push("The codex glossary-pass batch " + batch.index + " is working in the background. Wait for it to finish: run exactly one bash command, a polling loop:")
  lines.push("for i in $(seq 1 45); do " + checkCmd + " && exit 0; sleep 20; done; exit 1")
  lines.push("If that command exits successfully, return exactly the line: READY " + batch.index)
  lines.push("Otherwise, after the timeout (about 15 minutes), return exactly the line: TIMEOUT " + batch.index)
  lines.push("Do nothing else -- do not touch any files, and do not resolve any candidates yourself.")
  return lines.join("\n")
}

// Merge -- Claude, effort:low, no agentType, no schema: this call's own
// return is never trusted (see references/workflow-schema-validation.md);
// only the disk-independent glossaryVerifyPrompt() call below gates
// merged:true. fragments must already be every ready batch's fragmentPath,
// in ascending batch-index order (see the pipeline stage below) -- that
// order is threaded straight into canon_validate.py's own
// _merge_batch(acc, frag) chaining.
function mergeBatchesPrompt(fragments) {
  const lines = []
  lines.push("Effort: low. Mechanical glossary batch-merge only -- no canonicalization judgment.")
  lines.push("Durable root: " + ROOT + ".")
  const cmdParts = [PY, ROOT + "/scripts/canon_validate.py", "--merge-batches"]
  for (let i = 0; i < fragments.length; i++) cmdParts.push(fragments[i])
  cmdParts.push("--research-mode", RESEARCH_MODE)
  lines.push("Run exactly this command and capture its single printed JSON line: " + cmdParts.join(" "))
  lines.push("Return that printed line's content, as text, in your own response. Do not judge or re-decide anything yourself -- a separate, disk-independent step verifies this merge afterward and is what this run actually trusts.")
  return lines.join("\n")
}

// Verify -- Claude, effort:low, no agentType, schema: CANON_VERIFY_SCHEMA.
// Disk-independent: canon_validate.py --verify-merged fresh-reads
// canon.json plus every listed fragment itself, never trusting the merge
// call above's own claim (#88). fragments must be the SAME ready-batch
// fragment paths, in the same order, that mergeBatchesPrompt() was given.
function glossaryVerifyPrompt(fragments) {
  const lines = []
  lines.push("Effort: low. Mechanical disk-independent merge verification only -- do not judge the comparison yourself.")
  lines.push("Durable root: " + ROOT + ".")
  const cmdParts = [PY, ROOT + "/scripts/canon_validate.py", "--verify-merged"]
  for (let i = 0; i < fragments.length; i++) { cmdParts.push("--batch", fragments[i]) }
  cmdParts.push("--research-mode", RESEARCH_MODE, "--expect-source-forms-file", MANIFEST_ALL_PATH)
  lines.push("Run exactly this command and read its one line of JSON output: " + cmdParts.join(" "))
  lines.push("Return a structured result with exactly these fields: verified (the command's own verified value), and, only when the command's own output actually includes it, missing (the command's own missing array, copied verbatim). Do not add, omit, or alter any value the command printed.")
  return lines.join("\n")
}

// Exact-key-set JS guard for CANON_VERIFY_SCHEMA's flat literal (see
// references/ledger-and-resumability.md's guard-field-set discipline): a
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
// collide on shared bytes the way a single shared canon.json used to (#90).
// The dispatch call's own return is never read -- only the wait step's
// disk-backed poll decides whether this batch's fragment is ready.
// ---------------------------------------------------------------------------
async function batchStep(batch) {
  // Resume-skip precheck (#101): if this batch's fragment already exists and
  // passes --check-batch, trust it and skip the codex dispatch + wait. Any
  // non-PRESENT answer -- including a null/failed precheck, or a corrupt or
  // missing fragment (both of which the precheck reports as ABSENT) -- falls
  // through to a full dispatch, so a bad fragment is never wrongly skipped.
  const precheck = await agent(batchPrecheckPrompt(batch), {
    effort: "low", phase: "GlossaryPass", label: "glossary:precheck:" + batch.index,
  })
  if (precheck && precheck.indexOf("PRESENT") !== -1) {
    log("batch " + batch.index + ": resume-skip -- existing fragment already passed --check-batch, not re-dispatching")
    return { batchIndex: batch.index, fragmentPath: fragmentPath(batch.index), ready: true }
  }

  await agent(batchDispatchPrompt(batch), {
    agentType: "codex:codex-rescue",
    effort: "high",
    phase: "GlossaryPass",
    label: "glossary:dispatch:" + batch.index,
  })

  const ready = await agent(batchWaitPrompt(batch), {
    effort: "low", phase: "GlossaryPass", label: "glossary:wait:" + batch.index,
  })
  if (!ready || ready.indexOf("READY") === -1) {
    log("batch " + batch.index + ": fragment never became ready")
    return { batchIndex: batch.index, fragmentPath: fragmentPath(batch.index), ready: false, reason: "glossary-pass-null" }
  }
  return { batchIndex: batch.index, fragmentPath: fragmentPath(batch.index), ready: true }
}

const batchResults = await pipeline(BATCHES, batchStep)

const readyBatches = batchResults
  .filter((r) => r && r.ready)
  .sort((a, b) => a.batchIndex - b.batchIndex)
const notReadyBatches = batchResults.filter((r) => !r || !r.ready)

if (notReadyBatches.length > 0) {
  log("Glossary pass: " + notReadyBatches.length + "/" + BATCHES.length + " batch(es) never produced a ready fragment; the merge is not attempted.")
  return {
    batches: batchResults, merged: false, reason: "fragment-check-failed",
    notReady: notReadyBatches.map((r) => (r ? r.batchIndex : null)),
  }
}

const fragments = readyBatches.map((r) => r.fragmentPath)

// ONE serialized merge call (never concurrent with itself, and never run
// until every batch's own fragment has independently passed --check-batch
// above) -- this is the fix for #90's shared-canon.json race.
await agent(mergeBatchesPrompt(fragments), {
  effort: "low", phase: "Merge", label: "glossary:merge",
})

const verified = await agent(glossaryVerifyPrompt(fragments), {
  effort: "low", phase: "Merge", label: "glossary:verify", schema: CANON_VERIFY_SCHEMA,
})

if (!isVerifiedResult(verified)) {
  const missingDetail = verified && Array.isArray(verified.missing) ? verified.missing : null
  log("Glossary pass: post-merge disk verification failed" + (missingDetail && missingDetail.length ? " -- missing: " + missingDetail.join(", ") : "") + ".")
  return { batches: batchResults, merged: false, reason: "verify-failed", missing: missingDetail }
}

log("DONE: " + fragments.length + "/" + BATCHES.length + " batch fragment(s) merged into canon.json (verified).")
return { batches: batchResults, merged: true }
