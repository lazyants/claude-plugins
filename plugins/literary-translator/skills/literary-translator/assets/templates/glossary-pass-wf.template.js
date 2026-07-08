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
//
// `args` shape this template expects (an array, or a JSON string of one):
//   [ { index: 0, candidates: [ {name, freq, mid_sentence, multiword,
//       abbrev, n_segments, likely_name}, ... ] },
//     { index: 1, candidates: [...] }, ... ]
// Each candidates[] row is one bootstrap_names.py candidate row, taken
// as-is from name_candidates.json. Batch construction -- chunking
// candidates into batches AND excluding every source_form already present
// in the CURRENT canon.json's entries{} map -- is the orchestrating
// session's job before it ever calls the Workflow tool, never this
// script's own job (canon.json itself is the citation cache; see
// references/canon-and-glossary.md's "Citation cache" section).

export const meta = {
  name: "literary-translator-glossary-pass",
  description: "Batch candidate proper names/realia (bootstrap_names.py output, already filtered against the current canon.json) through a schema-validated codex-rescue call each, freezing canon.json via canon_validate.py inside the same turn.",
  phases: [
    {
      title: "GlossaryPass",
      detail: "codex resolves each batch of candidates into a canon-batch.schema.json-shaped array, writes it to glossary/out_{index}.json, merges it into canon.json via canon_validate.py, then returns the same validated array",
    },
  ],
}

const ROOT = "{{DURABLE_ROOT}}"
const PY = "python3"
const SOURCE_LANG = "{{SOURCE_LANG}}"
const TARGET_LANG = "{{TARGET_LANG}}"
const RESEARCH_MODE = "{{RESEARCH_MODE}}"

// ---------------------------------------------------------------------------
// Schema literal -- declared ABOVE the pipeline() call at the bottom of this
// file. A schema declared after its first use silently no-ops due to
// temporal-dead-zone semantics in this execution model (see
// references/workflow-schema-validation.md's TDZ gotcha,
// gotcha_workflow_const_tdz_silent_fail) -- declaration order in this file
// is load-bearing. This is the ONE inline schema literal
// glossary-pass-wf.template.js owns (the other four --  REVIEW_SCHEMA,
// REVIEW_ARTIFACT_SCHEMA, LEDGER_WRITE_SCHEMA, LEDGER_MERGE_SCHEMA -- belong
// to mass-translate-wf.template.js instead).
// ---------------------------------------------------------------------------

const CANON_BATCH_ACCEPTED_SHAPE = {
  type: "object",
  additionalProperties: false,
  required: ["source_form", "is_proper_name", "disposition", "canonical_target_form", "basis", "confidence"],
  properties: {
    source_form: { type: "string", minLength: 1, description: "The candidate's own surface form, copied verbatim from the candidates[] row handed to this batch." },
    is_proper_name: { type: "boolean", description: "false = not actually a proper name (a common word, an interjection, a bare title, a capitalization artifact). Such a candidate still goes through disposition:review_queue, never disposition:accepted." },
    disposition: { const: "accepted" },
    canonical_target_form: { type: "string", minLength: 1, description: "The resolved target-language form." },
    basis: { type: "string", enum: ["established", "transliterated", "title", "not_a_name"] },
    source: { type: "string", description: "Reference URL. Required, and must be a non-empty URI, when basis is established." },
    confidence: { type: "string", enum: ["high", "medium", "low"] },
    note: { type: "string" },
    category: { type: "string", description: "Optional, OPEN-vocabulary per-project entity category (e.g. person, place, work, group) -- see canon-entry.schema.json's own 'category' field. Absent/blank is valid; consumers treat it as 'other'." },
  },
  if: {
    properties: { basis: { const: "established" } },
  },
  then: {
    required: ["source"],
    properties: {
      source: { type: "string", format: "uri", minLength: 1 },
    },
  },
}

const CANON_BATCH_QUEUED_SHAPE = {
  type: "object",
  additionalProperties: false,
  required: ["source_form", "is_proper_name", "disposition", "note"],
  properties: {
    source_form: { type: "string", minLength: 1 },
    is_proper_name: { type: "boolean" },
    disposition: { const: "review_queue" },
    canonical_target_form: { type: "string" },
    basis: { type: "string", enum: ["established", "transliterated", "title", "not_a_name"] },
    source: { type: "string" },
    confidence: { type: "string", enum: ["high", "medium", "low"] },
    note: { type: "string", minLength: 1, description: "Required: why this candidate is queued rather than resolved (disputed transcription, several historical people sharing one surname, not enough context in this batch, a non-name candidate, or, under offline research_mode, the literal prefix SOURCE_UNAVAILABLE:)." },
  },
}

const CANON_BATCH_SCHEMA = {
  type: "array",
  description: "canon-batch.schema.json's own real return contract -- a discriminated union over each item's own disposition field, never a bare array of canon-entry.schema.json shapes.",
  items: { oneOf: [CANON_BATCH_ACCEPTED_SHAPE, CANON_BATCH_QUEUED_SHAPE] },
}

const BATCHES = Array.isArray(args) ? args : JSON.parse(args)

function glossaryPrompt(batch) {
  const candidatesJson = JSON.stringify(batch.candidates, null, 1)
  const outPath = `${ROOT}/glossary/out_${batch.index}.json`
  return `Effort: high. Canon-and-glossary pass (codex-glossary-pass) for a ${SOURCE_LANG} -> ${TARGET_LANG} literary translation project, batch ${batch.index}.

Read in full, in this order: ${ROOT}/glossary_TASK.md (the canonicalization rules and the exact per-item output contract) and ${ROOT}/canon.json (the entries already frozen there). Never re-decide or override any source_form already present in canon.json's own entries{} -- this batch resolves only the new candidates listed below, which were already filtered against the current canon.json before you were dispatched.

research_mode = ${RESEARCH_MODE}. If it is "offline": basis:"established" is forbidden outright for every candidate in this batch, with no exception -- use basis:"transliterated" when the fixed practical-transcription rule in style_bible.md (section C-translit) is enough on its own, or set disposition:"review_queue" instead, with a note that starts with the literal prefix "SOURCE_UNAVAILABLE:". If it is "live": basis:"established" is allowed, but only together with a real, citable reference source URL -- never a fabricated one.

This batch's candidates -- deterministically extracted by bootstrap_names.py, never yet decided by any LLM (name = the surface form as it appears in the source text; freq/n_segments = how often and how widely it recurs; likely_name/multiword/mid_sentence/abbrev = this script's own recall-oriented heuristics, not a verdict):
${candidatesJson}

For EVERY candidate above, in the SAME order, decide exactly one canon-batch item:
- source_form: the candidate's own "name" field, copied verbatim.
- is_proper_name: false when the candidate is not actually a proper name at all (a frequent common word, an interjection, a bare title, or a sentence-initial capitalization artifact) -- such a candidate always gets disposition:"review_queue" too, never disposition:"accepted".
- disposition: "accepted" once you have a confident resolution; "review_queue" whenever it still needs a human's later attention -- a disputed transcription, several different historical people sharing one surname, not enough context in this batch alone, a non-name candidate as above, or the offline SOURCE_UNAVAILABLE case above.
- When disposition is "accepted": canonical_target_form, basis ("established" | "transliterated" | "title" | "not_a_name"), and confidence ("high" | "medium" | "low") are all required; when basis is "established", source is also required and must be a real, non-empty reference URL, never left empty and never invented.
- When disposition is "review_queue": note is required and must explain, briefly, why the candidate is queued rather than resolved.
- A title phrase (an honorific plus a bare surname or role -- for instance a form meaning "Monsieur the Prince" or "the Queen Mother") gets basis:"title", with canonical_target_form holding the unpacked target-language phrase; if the underlying surname is ALSO present as its own separate candidate in this same batch, resolve that one on its own merits instead of folding it into the title entry.

Write this exact JSON array, in this exact order, to ${outPath} -- a plain JSON array of objects, no markdown code fence, no comment, nothing else in the file. Then run this command and read its one line of JSON output: ${PY} ${ROOT}/scripts/canon_validate.py --research-mode ${RESEARCH_MODE} --batch ${outPath}

If it prints a line with "success": false, it names every offending item -- fix each one in your own array (reassign basis/disposition/note as the rules above require; never weaken the offline backstop, never fabricate a source URL to make the check pass), rewrite ${outPath}, and re-run canon_validate.py. Repeat until it prints a line with "success": true -- only then does canon.json actually hold this batch's decisions. Only once you have that success line should you return your final answer: the same validated array you just wrote, in the same order as the candidates you were given, matching canon-batch.schema.json exactly.`
}

async function glossaryBatchStep(batch) {
  const batchResult = await agent(glossaryPrompt(batch), {
    agentType: "codex:codex-rescue",
    effort: "high",
    phase: "GlossaryPass",
    label: `glossary:batch${batch.index}`,
    schema: CANON_BATCH_SCHEMA,
  })
  if (!batchResult) {
    return { batchIndex: batch.index, merged: false, reason: "glossary-pass-null", batchSize: batch.candidates.length }
  }
  const accepted = batchResult.filter((item) => item.disposition === "accepted").length
  const queued = batchResult.filter((item) => item.disposition === "review_queue").length
  log(`batch ${batch.index}: ${batchResult.length} candidates resolved (${accepted} accepted, ${queued} queued) and merged into canon.json`)
  return { batchIndex: batch.index, merged: true, batchSize: batch.candidates.length, accepted, queued }
}

// Note on concurrency: pipeline() may run these batch calls concurrently,
// and every batch's own canon_validate.py invocation reads-then-writes the
// SAME shared canon.json (unlike mass-translate-wf.template.js's
// per-segment ledger fragments, which each own a separate file and so never
// collide). This template is new plugin hardening, not itself
// source-proven (see references/canon-and-glossary.md) -- a first real
// project should pilot it on one small batch at a time and manually verify
// the canon.json merge output before trusting a large, fully concurrent
// glossary-pass run.

const results = await pipeline(BATCHES, (batch) => glossaryBatchStep(batch))

const merged = results.filter((r) => r && r.merged)
const failed = results.filter((r) => !r || !r.merged)
log(`DONE: ${merged.length}/${BATCHES.length} batches merged into canon.json; ${failed.length} need attention`)
return { merged, failed }
