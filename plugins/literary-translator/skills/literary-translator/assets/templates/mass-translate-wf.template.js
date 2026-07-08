// mass-translate-wf.template.js
//
// GENERATED-ONLY. This file is instantiated fresh from the plugin's own
// shipped copy at the start of every W5 mass-translate run -- it is never
// reused across runs, and never hand-edited in place despite the
// ".template" suffix on the shipped filename. The instantiated, fully
// substituted copy is written to:
//
//   ${durable_root}/runs/workflows/<run_id>/mass-translate-wf.js
//
// where <run_id> is a fresh, sortable identifier (an ISO-8601-ish timestamp
// works) generated once per invocation -- the same id a later resumed run's
// resumeFromRunId refers back to. ${durable_root}/runs/.plugin_bundle_hash
// covers this exact template's own bytes, so a plugin update is never
// silently masked by an old generated script surviving on disk.
//
// Generalized from the real, proven historiettes-t3 reference script
// (historiettes-t3/reference/historiettes-mass-translate-wf.reference.js).
// The per-segment engine loop, the schema-validated workflow-level agent()
// discipline, and the self-contained/no-imports constraint are preserved
// exactly from that proven script; the ledger-fragment bookkeeping, the
// review-artifact gate, and the batch_agent_cap preflight are new plugin
// hardening layered on top (see references/gotchas.md item 2 -- careful
// design, not itself proven at scale).
//
// Self-contained by design: only the Workflow tool's own globals are used
// (agent(), pipeline(), log(), args) plus python3 shelled out via agent
// prompts for every deterministic check. No import/require anywhere.
//
// Substitution tokens (resolved ONCE by the orchestrating session at
// instantiation time, before the Workflow tool ever executes this script --
// there is no templating engine at Workflow runtime, so a leftover
// unresolved substitution token in the generated script is a hard bug in
// the instantiation step, never a cosmetic one):
//
//   {{DURABLE_ROOT}}                     -- absolute path to the project's durable root
//   {{SOURCE_LANG}}                      -- source.language.code, e.g. "fr"
//   {{TARGET_LANG}}                      -- target.language.code, e.g. "ru"
//   {{MAX_FIX_ROUNDS}}                   -- engine.max_fix_rounds, substituted as a BARE integer literal
//   {{VERSE_POLICY_INSTRUCTION_BLOCK}}   -- resolved verse-policy instruction text, read fresh from
//                                          the CURRENT profile.yml every time a run is scaffolded --
//                                          never spliced into translate_TASK.md/review_TASK.md
//                                          directly. The instantiation step must substitute this
//                                          token with a JSON-string-escaped form of the resolved
//                                          text (e.g. via JSON.stringify, then stripping the outer
//                                          quotes this token already sits inside below), so any
//                                          quote or newline in the resolved instruction text stays
//                                          a valid JS string body -- never a raw, unescaped splice.
//   {{BATCH_AGENT_CAP}}                  -- engine.batch_agent_cap, substituted as a BARE integer
//                                          literal. This one extra token (beyond the five documented
//                                          in references/orchestration-and-batching.md's "prompt
//                                          functions" section) exists because the batch_agent_cap
//                                          preflight estimator below needs this value and this
//                                          script has no filesystem access with which to read
//                                          profile.yml itself.
//
// Storage/path conventions this file must follow exactly (load-bearing --
// see references/ledger-and-resumability.md and references/gotchas.md item 3):
//   draft_path(seg)   = segments/{seg}.draft.json      (no target-language suffix)
//   review_path(seg)  = segments/{seg}.review.json     (no target-language suffix)
//   segpack_path(seg) = segments/segpack_{seg}.json

export const meta = {
  name: "literary-translator-mass-translate",
  description: "Per-segment mass-translate pipeline: codex-translate, then a deterministic validate gate, then codex-review, then a Claude fix, looped to convergence, with schema-validated ledger bookkeeping and a batch-size preflight. Instantiated fresh from the plugin's own shipped copy for every run -- never a stale generated copy reused across runs.",
  phases: [
    { title: "Translate", detail: "codex translates each segpack to a draft and self-validates coverage before returning" },
    { title: "ReviewFix", detail: "codex reviews and Claude fixes, looped until clean and coverage_ok, capped at MAX_FIX_ROUNDS plus one mandatory final confirming review" },
    { title: "Ledger", detail: "schema-validated ledger bookkeeping: per-segment status writes plus the mandatory batch-completeness merge check" },
  ],
};

// ---------------------------------------------------------------------------
// Schema literals -- declared here, above every use, including the pipeline()
// call far below. A schema declared after its first use silently no-ops due
// to temporal-dead-zone semantics in this execution model (see
// references/gotchas.md item 10) -- there is no runtime error to catch it,
// so declaration order here is load-bearing, not a style choice.
// ---------------------------------------------------------------------------

// Matches review.schema.json exactly. No verse_status field: verse-specific
// issues surface as ordinary findings[] entries (loc: "VERSE:{vid}"); verse
// COVERAGE is exclusively validate_draft.py's job, never review judgment.
// draft_sha1 is a deliberate plugin addition over the proven reference's own
// schema -- the reviewer computes it itself, before reading the draft, via
// draft_sha1.py (hash-first-then-read).
const REVIEW_SCHEMA = {
  type: "object",
  additionalProperties: false,
  required: ["clean", "coverage_ok", "findings", "draft_sha1"],
  properties: {
    clean: {
      type: "boolean",
      description: "True only if the reviewer found no findings that require a fix round.",
    },
    coverage_ok: {
      type: "boolean",
      description: "True only if the deterministic validate_draft.py gate printed OK for this draft.",
    },
    findings: {
      type: "array",
      description: "Issues the reviewer wants fixed. Empty when clean is true.",
      items: {
        type: "object",
        additionalProperties: false,
        required: ["loc", "severity", "issue", "suggest"],
        properties: {
          loc: {
            type: "string",
            description: "Location the finding applies to, e.g. a block/footnote id, or VERSE:{vid} for a verse-specific issue.",
          },
          severity: { type: "string" },
          issue: { type: "string" },
          suggest: { type: "string" },
        },
      },
    },
    draft_sha1: {
      type: "string",
      description: "The reviewer's own sha1 of the draft, computed via draft_sha1.py BEFORE reading the draft file.",
    },
  },
};

// Matches review-artifact-check.schema.json exactly.
const REVIEW_ARTIFACT_SCHEMA = {
  oneOf: [
    {
      type: "object",
      additionalProperties: false,
      required: ["match"],
      properties: {
        match: { const: true },
      },
    },
    {
      type: "object",
      additionalProperties: false,
      required: ["match", "mismatch_detail"],
      properties: {
        match: { const: false },
        mismatch_detail: { type: "string" },
      },
    },
  ],
};

// Matches ledger-write-confirmation.schema.json exactly. The two branches
// are deliberately not the same shape -- a failure never claims a
// fragment_path/fragment_sha1 that was never written.
const LEDGER_WRITE_SCHEMA = {
  oneOf: [
    {
      type: "object",
      additionalProperties: false,
      required: ["success", "status", "fragment_path", "fragment_sha1"],
      properties: {
        success: { const: true },
        status: { type: "string" },
        fragment_path: { type: "string" },
        fragment_sha1: { type: "string" },
      },
    },
    {
      type: "object",
      additionalProperties: false,
      required: ["success", "error"],
      properties: {
        success: { const: false },
        error: { type: "string" },
        exit_code: { type: "integer" },
        stderr: { type: "string" },
      },
    },
  ],
};

// Matches ledger-merge-confirmation.schema.json exactly.
const LEDGER_MERGE_SCHEMA = {
  oneOf: [
    {
      type: "object",
      additionalProperties: false,
      required: ["success", "ledger_path", "n_segments", "missing_segments", "stale_segments"],
      properties: {
        success: { const: true },
        ledger_path: { type: "string" },
        n_segments: { type: "integer" },
        missing_segments: { type: "array", maxItems: 0 },
        stale_segments: { type: "array", items: { type: "string" } },
      },
    },
    {
      type: "object",
      additionalProperties: false,
      required: ["success", "error"],
      properties: {
        success: { const: false },
        error: { type: "string" },
        missing_segments: { type: "array", items: { type: "string" } },
        exit_code: { type: "integer" },
        stderr: { type: "string" },
      },
    },
  ],
};

// ---------------------------------------------------------------------------
// Constants substituted once at instantiation time (see the header comment
// above for the full token list and the JSON-escaping contract on the verse
// policy token specifically).
// ---------------------------------------------------------------------------

const PY = "python3";
const ROOT = "{{DURABLE_ROOT}}";
const SOURCE_LANG = "{{SOURCE_LANG}}";
const TARGET_LANG = "{{TARGET_LANG}}";
const MAXFIX = {{MAX_FIX_ROUNDS}};
const BATCH_AGENT_CAP = {{BATCH_AGENT_CAP}};
const VERSE_POLICY_INSTRUCTION_BLOCK = "{{VERSE_POLICY_INSTRUCTION_BLOCK}}";

// SEGS is this run's dispatch list -- the exact array select_segments.py
// emitted (SEGS = not_started union recoverable union stale, minus reusable/
// human_escalation/blocked_needs_regeneration), passed through as this
// Workflow's own args. Never separately hand-typed or re-derived here.
const SEGS = Array.isArray(args) ? args : JSON.parse(args);

// ---------------------------------------------------------------------------
// Defense-in-depth segment id guard. Every id in SEGS is spliced, unquoted,
// into shell command strings below (translatePrompt/reviewPrompt/fixPrompt/
// waitPrompt/recordLedgerPrompt/mergeLedgerPrompt), including a bash for-loop
// in waitPrompt -- an unsafe id ('../', '/', shell metacharacters) would
// otherwise escape the durable root or inject arbitrary shell commands.
// select_segments.py already validates every id it emits against this same
// allowlist BEFORE it ever reaches this script's args, so this check should
// never fire in production; it exists solely so a poisoned/hand-edited SEGS
// input fails loudly here rather than silently reaching a shell command.
// Kept identical to select_segments.py's and review_artifact_check.py's own
// validate_seg() allowlist. In JS, "$" (no /m flag) matches only
// end-of-input (NOT before a trailing newline), so /^...$/ is safe here --
// do NOT add the /m flag.
// ---------------------------------------------------------------------------
const SEG_ID_RE = /^(?:FRONTBACK:)?[A-Za-z0-9_]+$/;
for (let i = 0; i < SEGS.length; i++) {
  const s = SEGS[i];
  if (typeof s !== "string" || !SEG_ID_RE.test(s)) {
    throw new Error(`Unsafe segment id ${JSON.stringify(s)}: must match (FRONTBACK:)?[A-Za-z0-9_]+`);
  }
}

// ---------------------------------------------------------------------------
// Small helper: does fragmentPath end with exactly "{seg}.json"? A plain
// substring check (indexOf) would wrongly match seg1 against a fragment
// path for seg10 -- this checks the true path suffix instead.
// ---------------------------------------------------------------------------
function endsWithSegJson(fragmentPath, seg) {
  const want = seg + ".json";
  if (typeof fragmentPath !== "string" || fragmentPath.length < want.length) return false;
  return fragmentPath.slice(fragmentPath.length - want.length) === want;
}

// ---------------------------------------------------------------------------
// Prompt-builder functions. All plain JavaScript string interpolation
// against the constants above -- there is no templating engine at Workflow
// runtime, so every one of these is built with ordinary string
// concatenation, never a backtick template literal (natural-language prose
// below routinely needs literal quotes and would otherwise risk an
// unescaped backtick terminating the literal early).
// ---------------------------------------------------------------------------

function translatePrompt(seg) {
  const lines = [];
  lines.push("Effort: high. Literary translation of segment " + seg + " (" + SOURCE_LANG + " to " + TARGET_LANG + ").");
  lines.push("Read in order: " + ROOT + "/translate_TASK.md ; " + ROOT + "/style_bible.md (in full, especially the word-sense/realia traps section) ; " + ROOT + "/segments/segpack_" + seg + ".json (source text plus the frozen name/realia canon for this segment).");
  lines.push("Verse policy for this project: " + VERSE_POLICY_INSTRUCTION_BLOCK);
  lines.push("Translate every block, footnote, and verse this segpack contains. Copy every placeholder sentinel (e.g. ⟦FNREF_N⟧, ⟦VERSE_...⟧) byte for byte, in its correct position in the sentence -- never translate, drop, or reword a sentinel itself. Any embedded third-language text (Latin, an older form of the source language, or similar) gets an in-text gloss, never a notes-only translation. Use canon_names forms verbatim; for a new_names entry not yet in the canon, choose a reasoned rendering and flag it in notes as NEW:.");
  lines.push("Write your draft exactly per translate_TASK.md's own schema to: " + ROOT + "/segments/" + seg + ".draft.json");
  lines.push("Then self-check coverage: run " + PY + " " + ROOT + "/scripts/validate_draft.py " + seg + ". If it prints FAIL, fix the draft and rewrite the file, and repeat until it prints OK.");
  lines.push("Return exactly the line: DONE " + seg);
  return lines.join("\n");
}

function reviewPrompt(seg) {
  const lines = [];
  lines.push("Effort: high. Single reviewer covering both accuracy and literary quality for segment " + seg + " (" + SOURCE_LANG + " to " + TARGET_LANG + ").");
  lines.push("First run the deterministic gate: " + PY + " " + ROOT + "/scripts/validate_draft.py " + seg + " -- remember whether it printed OK or FAIL, and any defects it named.");
  lines.push("Before reading the draft, compute its current sha1 by running: " + PY + " " + ROOT + "/scripts/draft_sha1.py " + seg + " -- this becomes your draft_sha1 return value below, and it must be computed BEFORE you read the draft file itself.");
  lines.push("Then read: " + ROOT + "/review_TASK.md ; " + ROOT + "/style_bible.md ; " + ROOT + "/segments/segpack_" + seg + ".json ; " + ROOT + "/segments/" + seg + ".draft.json.");
  lines.push("Verse policy for this project: " + VERSE_POLICY_INSTRUCTION_BLOCK);
  lines.push("Check the draft against the source for: full accuracy (no omissions or distortions), word-sense and realia fidelity for the source era and context -- ask explicitly whether each notable word means what it meant in that period and context, not what it means today -- name/canon fidelity, placeholder sentinel fidelity, verse per the policy above, and literary quality (register, idiom, natural seams, rhythm).");
  lines.push("Return a structured result with exactly these fields: clean (true only if there are no findings that require a fix round), coverage_ok (true only if the deterministic gate above printed OK), findings (an array of objects with loc/severity/issue/suggest -- use a loc like \"VERSE:{vid}\" for a verse-specific finding), and draft_sha1 (the value you computed before reading the draft, above).");
  lines.push("Also write that exact same JSON object to: " + ROOT + "/segments/" + seg + ".review.json");
  return lines.join("\n");
}

function waitPrompt(seg) {
  const lines = [];
  lines.push("The codex translator for segment " + seg + " is working in the background. Wait for it to finish: run exactly one bash command, a polling loop:");
  lines.push("for i in $(seq 1 45); do " + PY + " " + ROOT + "/scripts/draft_ready.py " + seg + " && exit 0; sleep 20; done; exit 1");
  lines.push("If that command exits successfully (draft_ready.py printed READY), return exactly the line: READY " + seg);
  lines.push("Otherwise, after the timeout (about 15 minutes), return exactly the line: TIMEOUT " + seg);
  lines.push("Do nothing else -- do not touch any files, and do not translate anything yourself.");
  return lines.join("\n");
}

// Deliberate, documented 3-argument departure from the proven reference's
// 2-argument fixPrompt(seg, round) shape -- see references/gotchas.md item 5
// and references/engine-loop.md's R1. revObj is the SAME schema-validated
// object reviewPrompt already returned this round, still in this script's
// own in-memory state, spliced in directly rather than re-read off
// review_path(seg). Do not revert this to a 2-argument shape.
function fixPrompt(seg, round, revObj) {
  const revObjJSON = JSON.stringify(revObj);
  const lines = [];
  lines.push("Effort: high. You are the Claude editor applying review findings to segment " + seg + ", round " + round + ".");
  lines.push("The reviewer's structured verdict for this round, to apply in full, is exactly this JSON object -- use only this, do not re-read " + ROOT + "/segments/" + seg + ".review.json for findings:");
  lines.push(revObjJSON);
  lines.push("Important: only codex translates. If the draft is missing or is not actually ready -- check by running " + PY + " " + ROOT + "/scripts/draft_ready.py " + seg + " -- do not translate it yourself: return exactly the line DRAFT_MISSING " + seg + " and write nothing.");
  lines.push("Otherwise, read " + ROOT + "/segments/" + seg + ".draft.json and " + ROOT + "/segments/segpack_" + seg + ".json, and carefully apply every finding above to the draft. Never touch a placeholder sentinel (e.g. ⟦FNREF_...⟧, ⟦VERSE_...⟧) -- copy each one byte for byte in place. Keep the verse policy: " + VERSE_POLICY_INSTRUCTION_BLOCK);
  lines.push("Never change the set of block, footnote, or verse keys -- they must stay exactly 1:1 with the segpack.");
  lines.push("Rewrite " + ROOT + "/segments/" + seg + ".draft.json with your fixes. Then run " + PY + " " + ROOT + "/scripts/validate_draft.py " + seg + " and confirm it prints OK -- if your own edit broke coverage or a placeholder, repair it and rewrite the file again until it prints OK.");
  lines.push("Return exactly the line: FIXED " + seg + " r" + round);
  return lines.join("\n");
}

// One schema-validated call shape covers all five ledger-write call sites
// (see references/ledger-and-resumability.md). fields may carry status
// (required), reason, rounds (a bare integer), note -- and, ONLY for the
// converged call site, a needsCacheKey marker (a JS-side-only signal to
// this prompt builder, never itself a real ledger_update.py payload field)
// instructing the agent to compute the current 15-field cache_key itself
// via cache_key.py and fold it into the payload it writes.
function recordLedgerPrompt(seg, fields) {
  const knownFields = {};
  if (fields.status !== undefined) knownFields.status = fields.status;
  if (fields.reason !== undefined) knownFields.reason = fields.reason;
  if (fields.rounds !== undefined) knownFields.rounds = fields.rounds;
  if (fields.note !== undefined) knownFields.note = fields.note;
  const knownFieldsJSON = JSON.stringify(knownFields);

  const lines = [];
  lines.push("Effort: low. Mechanical ledger bookkeeping only -- no translation or review judgment.");
  lines.push("Segment: " + seg + ". Durable root: " + ROOT + ".");
  lines.push("Start building a JSON payload object from exactly these fields: " + knownFieldsJSON + ".");
  if (fields.needsCacheKey) {
    lines.push("This is a convergence write. Before writing the payload file, run: " + PY + " " + ROOT + "/scripts/cache_key.py --seg " + seg);
    lines.push("Take that command's full printed JSON object verbatim and add it to the payload object as its cache_key field, unmodified.");
  }
  lines.push("Write the resulting payload object, and nothing else, to a fresh scratch file at " + ROOT + "/runs/.ledger_update_payload." + seg + ".<a unique suffix, e.g. your own process id> -- never reuse an existing scratch file.");
  lines.push("Then run: " + PY + " " + ROOT + "/scripts/ledger_update.py " + seg + " --payload-file <the scratch file path you just wrote>");
  lines.push("Capture that command's single printed JSON line.");
  lines.push("If it reports success: true, re-read the fragment file at the fragment_path it reported, independently compute the sha1 of that file's raw bytes (e.g. with Python's hashlib, reading the file in binary mode), and confirm it matches the fragment_sha1 the command reported. Do not trust the command's own fragment_sha1 claim without this independent check.");
  lines.push("Return exactly one structured result matching the required schema: on a verified success, success: true plus the status/fragment_path/fragment_sha1 the command reported; on any failure, or if the independent sha1 check does not match, success: false plus an error string describing what went wrong.");
  return lines.join("\n");
}

function mergeLedgerPrompt(segs) {
  const segsCsv = segs.join(",");
  const lines = [];
  lines.push("Effort: low. Mechanical ledger completeness check only -- no translation or review judgment.");
  lines.push("Durable root: " + ROOT + ".");
  lines.push("Run: " + PY + " " + ROOT + "/scripts/ledger_merge.py --expected-segs " + segsCsv);
  lines.push("Capture that command's single printed JSON line.");
  lines.push("Independently re-read " + ROOT + "/runs/ledger.json and confirm every one of these segment ids has a matching key: " + segsCsv + ". This is a completeness/subset check only: ledger.json may also contain extra keys left over from earlier batches, and that is expected, never a failure by itself. Only a listed segment id with no matching key at all is a failure.");
  lines.push("Return exactly one structured result matching the required schema: on a verified success, success: true plus the ledger_path/n_segments/missing_segments/stale_segments the command reported; on any failure, or if your own independent check disagrees with the command's claim, success: false plus an error string.");
  return lines.join("\n");
}

// Right after every non-null review verdict, including the final confirming
// one. revObj is spliced in directly (same mechanism fixPrompt uses); the
// script, not the agent, does the actual byte-for-byte comparison against
// the on-disk review_path(seg) -- see references/workflow-schema-validation.md.
function verifyReviewArtifactPrompt(seg, revObj) {
  const revObjJSON = JSON.stringify(revObj);
  const lines = [];
  lines.push("Effort: low. Mechanical artifact verification only -- do not judge the comparison yourself.");
  lines.push("Segment: " + seg + ". Durable root: " + ROOT + ".");
  lines.push("Write exactly this JSON object, byte for byte, and nothing else, to a fresh scratch file at " + ROOT + "/runs/.review_artifact_expected." + seg + ".<a unique suffix, e.g. your own process id>:");
  lines.push(revObjJSON);
  lines.push("Then run: " + PY + " " + ROOT + "/scripts/review_artifact_check.py " + seg + " --expected-file <the scratch file path you just wrote>");
  lines.push("Relay that command's single printed JSON line verbatim as your own structured result. The script already did the comparison -- do not re-judge it.");
  return lines.join("\n");
}

// ---------------------------------------------------------------------------
// recordLedgerCall -- wraps the schema-validated recordLedgerPrompt call
// with the mandatory JS-side payload-intent verification: after a
// success:true return, this script itself (not a new prompt) confirms the
// returned fragment_path's segment component matches seg and the returned
// status matches fields.status. A mismatch is treated the same as
// success:false, never retried through the same ledger-write channel.
// ---------------------------------------------------------------------------
async function recordLedgerCall(seg, fields, label) {
  const raw = await agent(recordLedgerPrompt(seg, fields), {
    effort: "low", phase: "Ledger", label: label, schema: LEDGER_WRITE_SCHEMA,
  });

  if (!raw || raw.success !== true) {
    const detail = raw && raw.error ? raw.error : "ledger_update.py write did not report success";
    return {
      ok: false,
      failResult: { seg: seg, converged: false, reason: "ledger-write-failed", detail: detail },
    };
  }

  const segMatches = endsWithSegJson(raw.fragment_path, seg);
  const statusMatches = raw.status === fields.status;
  if (!segMatches || !statusMatches) {
    return {
      ok: false,
      failResult: {
        seg: seg, converged: false, reason: "ledger-write-mismatch",
        detail: "fragment_path=" + raw.fragment_path + " status=" + raw.status + " but expected seg=" + seg + " status=" + fields.status,
      },
    };
  }

  return { ok: true, raw: raw };
}

// ---------------------------------------------------------------------------
// Per-round call helpers.
// ---------------------------------------------------------------------------

async function callReview(seg, roundLabel, isRetry) {
  const label = "review:" + seg + ":r" + roundLabel + (isRetry ? ":retry" : "");
  return await agent(reviewPrompt(seg), {
    agentType: "codex:codex-rescue", effort: "high", phase: "ReviewFix", label: label, schema: REVIEW_SCHEMA,
  });
}

async function callArtifactCheck(seg, revObj, roundLabel, isRetry) {
  const label = "artifact-check:" + seg + ":r" + roundLabel + (isRetry ? ":retry" : "");
  const art = await agent(verifyReviewArtifactPrompt(seg, revObj), {
    effort: "low", phase: "ReviewFix", label: label, schema: REVIEW_ARTIFACT_SCHEMA,
  });
  if (!art) return { match: false, mismatch_detail: "artifact-check agent call returned no result" };
  return art;
}

async function callFix(seg, round, revObj) {
  const label = "fix:" + seg + ":r" + round;
  return await agent(fixPrompt(seg, round, revObj), {
    effort: "high", phase: "ReviewFix", label: label,
  });
}

// Runs one review verdict through to a verified, artifact-gate-confirmed
// result, handling both retry-once-then-blocked paths documented in
// references/engine-loop.md and references/workflow-schema-validation.md:
//   - a null review is retried once, fresh; still null -> blocked review-null.
//   - a match:false artifact check retries the ORIGINAL review call once,
//     fresh, then re-checks the retry's own write; still mismatched ->
//     blocked review-artifact-mismatch. If that retry review itself comes
//     back null, this is treated the same as the review-null path above --
//     there is no usable verdict to act on either way.
async function getVerifiedReview(seg, roundLabel) {
  let rev = await callReview(seg, roundLabel, false);
  if (!rev) {
    rev = await callReview(seg, roundLabel, true);
    if (!rev) return { status: "blocked", reason: "review-null" };
  }

  let art = await callArtifactCheck(seg, rev, roundLabel, false);
  if (art.match === true) return { status: "ok", rev: rev };

  const rev2 = await callReview(seg, roundLabel, true);
  if (!rev2) return { status: "blocked", reason: "review-null" };
  const art2 = await callArtifactCheck(seg, rev2, roundLabel, true);
  if (art2.match === true) return { status: "ok", rev: rev2 };

  return { status: "blocked", reason: "review-artifact-mismatch" };
}

// One review/fix round. isFinal marks the mandatory confirming review after
// the round cap -- on that round a not-clean verdict ends the segment as
// non_converged/cap (handled by the caller), never dispatches a fix.
async function runRound(seg, round, isFinal) {
  const roundLabel = isFinal ? "final" : String(round);

  const verified = await getVerifiedReview(seg, roundLabel);
  if (verified.status === "blocked") {
    const rec = await recordLedgerCall(
      seg, { status: "blocked", reason: verified.reason },
      "ledger:blocked:" + verified.reason + ":" + seg,
    );
    if (!rec.ok) return { terminal: true, value: rec.failResult };
    return { terminal: true, value: { seg: seg, converged: false, reason: verified.reason, rounds: round } };
  }

  const rev = verified.rev;
  if (rev.clean && rev.coverage_ok) {
    const rec = await recordLedgerCall(
      seg, { status: "converged", rounds: round, needsCacheKey: true },
      "ledger:converged:" + seg,
    );
    if (!rec.ok) return { terminal: true, value: rec.failResult };
    return { terminal: true, value: { seg: seg, converged: true, rounds: round } };
  }

  if (isFinal) {
    return { terminal: false, capReached: true, lastFindings: rev.findings };
  }

  const fx = await callFix(seg, round, rev);
  if (!fx || fx.indexOf("DRAFT_MISSING") !== -1) {
    const rec = await recordLedgerCall(
      seg, { status: "blocked", reason: "draft-missing" },
      "ledger:blocked:draft-missing:" + seg,
    );
    if (!rec.ok) return { terminal: true, value: rec.failResult };
    return { terminal: true, value: { seg: seg, converged: false, reason: "draft-missing", rounds: round } };
  }

  return { terminal: false, findingsCount: rev.findings.length };
}

// The per-segment translate -> readiness-poll -> review/fix loop ->
// confirming final review sequence. Called from pipeline() as this run's
// second stage, fed stage 1's own result and seg (see translateStage below
// and references/orchestration-and-batching.md's two-stage pipeline() shape).
async function reviewFixLoop(stage1Result, seg) {
  if (stage1Result && stage1Result.ledgerFailed) return stage1Result.result;

  const ready = await agent(waitPrompt(seg), { effort: "low", phase: "ReviewFix", label: "wait:" + seg });
  if (!ready || ready.indexOf("READY") === -1) {
    const rec = await recordLedgerCall(
      seg, { status: "non_converged", reason: "translate-timeout" },
      "ledger:timeout:" + seg,
    );
    if (!rec.ok) return rec.failResult;
    return { seg: seg, converged: false, reason: "translate-timeout" };
  }

  for (let round = 1; round <= MAXFIX; round++) {
    const r = await runRound(seg, round, false);
    if (r.terminal) return r.value;
    log(seg + ": round " + round + " -- " + r.findingsCount + " findings fixed, re-reviewing");
  }

  const finalRound = await runRound(seg, MAXFIX + 1, true);
  if (finalRound.terminal) return finalRound.value;

  const rec = await recordLedgerCall(
    seg, { status: "non_converged", reason: "cap", rounds: MAXFIX + 1 },
    "ledger:cap:" + seg,
  );
  if (!rec.ok) return rec.failResult;
  return {
    seg: seg, converged: false, reason: "cap", rounds: MAXFIX + 1,
    lastFindings: finalRound.lastFindings || null,
  };
}

// Stage 1 of the pipeline: the in_progress ledger write (closing the gap
// where an interruption between dispatch and any terminal write would
// otherwise leave zero durable record), then the fire-and-forget codex
// translate call itself. This is the one deliberate exception to "every
// codex accuracy call needs a schema" -- the translate call is intentionally
// schema-less, gated instead by file output plus draft_ready.py/
// validate_draft.py (see references/false-green-gate.md).
async function translateStage(seg) {
  const rec = await recordLedgerCall(seg, { status: "in_progress" }, "ledger:in_progress:" + seg);
  if (!rec.ok) return { ledgerFailed: true, result: rec.failResult };

  return await agent(translatePrompt(seg), {
    agentType: "codex:codex-rescue", effort: "high", phase: "Translate", label: "translate:" + seg,
  });
}

// ---------------------------------------------------------------------------
// batch_agent_cap preflight -- see references/orchestration-and-batching.md's
// "batch_agent_cap" section for the full derivation. This estimator is new
// plugin hardening, not itself source-proven (the real reference script has
// no such check anywhere). Must run, and must be able to return, BEFORE
// pipeline() is ever called below.
// ---------------------------------------------------------------------------
const estimatedCalls = 1 + SEGS.length * (6 + 3 * MAXFIX);
if (estimatedCalls > BATCH_AGENT_CAP) {
  log(
    "Batch too large: estimatedCalls=" + estimatedCalls +
    " exceeds engine.batch_agent_cap=" + BATCH_AGENT_CAP +
    " for " + SEGS.length + " segment(s) at max_fix_rounds=" + MAXFIX + "."
  );
  return { converged: [], failed: [], reason: "batch-too-large", estimatedCalls: estimatedCalls, cap: BATCH_AGENT_CAP };
}

const results = await pipeline(SEGS, translateStage, reviewFixLoop);

const converged = [];
const failed = [];
for (let i = 0; i < results.length; i++) {
  const r = results[i];
  if (r && r.converged) converged.push(r);
  else failed.push(r);
}
log("Translate/review pass done: " + converged.length + "/" + SEGS.length + " converged, " + failed.length + " need attention.");

// Mandatory, blocking, batch-final completeness check -- a batch is not
// complete until this passes (see references/ledger-and-resumability.md's
// "mergeLedgerPrompt / ledger_merge.py" section). Never written through the
// per-segment ledger channel it exists to independently verify.
const mergeResult = await agent(mergeLedgerPrompt(SEGS), {
  effort: "low", phase: "Ledger", label: "merge-ledger", schema: LEDGER_MERGE_SCHEMA,
});

if (!mergeResult || mergeResult.success !== true) {
  const detail = mergeResult && mergeResult.error ? mergeResult.error : "ledger_merge.py completeness check did not report success";
  log("Ledger merge/completeness check failed: " + detail);
  return {
    converged: converged, failed: failed,
    batchComplete: false, reason: "ledger-merge-failed", detail: detail,
  };
}

return {
  converged: converged, failed: failed, batchComplete: true,
  ledgerPath: mergeResult.ledger_path, staleSegments: mergeResult.stale_segments,
};
