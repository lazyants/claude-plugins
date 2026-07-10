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
// where <run_id> is the SAME value substituted below as {{RUN_ID}} -- a
// fresh, sortable identifier on a fresh run, or the identical id a resumed
// run's resumeFromRunId refers back to (see references/ledger-and-
// resumability.md's resume-integrity gate). ${durable_root}/runs/
// .plugin_bundle_hash covers this exact template's own bytes, so a plugin
// update is never silently masked by an old generated script surviving on
// disk.
//
// Generalized from the real, proven historiettes-t3 reference script
// (historiettes-t3/reference/historiettes-mass-translate-wf.reference.js).
// The per-segment engine loop, the schema-validated workflow-level agent()
// discipline, and the self-contained/no-imports constraint are preserved
// exactly from that proven script; the ledger-fragment bookkeeping, the
// review-artifact gate, the batch_agent_cap preflight, and the run-scoped
// dispatch_token freshness discipline are new plugin hardening layered on
// top (see references/gotchas.md item 2 -- careful design, not itself
// proven at scale).
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
//   {{RUN_ID}}                           -- this run's identifier, resolved ONCE by the orchestrating
//                                          session before instantiation via the resume-integrity gate:
//                                          fresh on a new/mismatched-digest run, or the identical value
//                                          reused via resumeFromRunId on a matched-digest resumed run
//                                          (see references/ledger-and-resumability.md). Validated
//                                          upstream against ^[A-Za-z0-9][A-Za-z0-9._-]*$, never
//                                          '.'/'..', never containing a '..' sequence, and always
//                                          colon-free -- this script splices it unguarded into shell
//                                          commands and JSON dispatch_token fields exactly like
//                                          {{DURABLE_ROOT}} above; an unresolved or malformed value
//                                          here is a bug in the instantiation step, never this script's
//                                          job to re-validate.
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
//                                          literal. This one extra token (beyond the six documented
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
// Both draft_path(seg) and review_path(seg) carry one extra top-level
// dispatch_token field beyond their user-facing schema content -- run-scoped
// freshness metadata (draft: "{{RUN_ID}}:{seg}"; review: "{{RUN_ID}}:{seg}:r
// {roundLabel}"), never a path component, and excluded from draft_sha1.py's
// content hash and validate_draft.py's coverage check.

export const meta = {
  name: "literary-translator-mass-translate",
  description: "Per-segment mass-translate pipeline: codex-translate, then a deterministic validate gate, then codex-review (dispatch/wait/read/check), then a Claude fix, looped to convergence, with schema-validated ledger bookkeeping and a batch-size preflight. Instantiated fresh from the plugin's own shipped copy for every run -- never a stale generated copy reused across runs.",
  phases: [
    { title: "Translate", detail: "codex translates each segpack to a draft and self-validates coverage before returning" },
    { title: "ReviewFix", detail: "codex reviews (fire-and-forget, bounded-polled) and Claude fixes, looped until clean and coverage_ok, capped at MAX_FIX_ROUNDS plus one mandatory final confirming review" },
    { title: "Ledger", detail: "schema-validated ledger bookkeeping: per-segment status writes plus the mandatory batch-completeness merge check" },
  ],
};

// ---------------------------------------------------------------------------
// Schema literals -- declared here, above every use, including the pipeline()
// call far below. A schema declared after its first use silently no-ops due
// to temporal-dead-zone semantics in this execution model (see
// references/gotchas.md item 10) -- there is no runtime error to catch it,
// so declaration order here is load-bearing, not a style choice.
//
// Every schema below is a plain top-level `object`, with no top-level
// `oneOf`/`allOf`/`anyOf` -- an agent's `schema` is a tool `input_schema`,
// and a top-level combinator there is REJECTED by the tool-use API outright
// (HTTP 400 on first dispatch), not merely under-enforced (see
// references/gotchas.md's "agent schema is a tool input_schema" item). The
// on-disk schemas some of these mirror (ledger-write-confirmation.schema.json,
// ledger-merge-confirmation.schema.json, review-artifact-check.schema.json)
// stay strong `oneOf` and validate the underlying *scripts'* Python stdout
// at runtime -- they are deliberately NOT the same shape as the flattened
// literals below, which only need to be API-legal. Branch discrimination
// the flat literals can no longer express is instead enforced by the
// exact-key-set JS guards further down this file (ledgerWriteSucceeded,
// ledgerMergeSucceeded, artifactCheckMatched).
// ---------------------------------------------------------------------------

// Matches review.schema.json's four verdict fields exactly. No verse_status
// field: verse-specific issues surface as ordinary findings[] entries (loc:
// "VERSE:{vid}"); verse COVERAGE is exclusively validate_draft.py's job,
// never review judgment. draft_sha1 is a deliberate plugin addition over
// the proven reference's own schema -- the reviewer computes it itself,
// before reading the draft, via draft_sha1.py (hash-first-then-read). This
// is an intentional four-field PROJECTION of the five-field on-disk
// review.schema.json (which also carries dispatch_token, run-scoped
// freshness metadata never part of the verdict) -- readReviewPrompt below
// reads the five-field file but returns only these four.
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

// Flat agent-facing literal (CONTRACT §1) -- deliberately NOT the same
// shape as review-artifact-check.schema.json on disk, which stays a strong
// oneOf and validates review_artifact_check.py's own stdout at the script
// level. artifactCheckMatched() below is what actually enforces
// match:true-implies-no-mismatch_detail for this literal's return.
const REVIEW_ARTIFACT_SCHEMA = {
  type: "object",
  additionalProperties: false,
  required: ["match"],
  properties: {
    match: { type: "boolean" },
    mismatch_detail: { type: "string" },
  },
};

// Flat agent-facing literal (CONTRACT §1) -- a union of the on-disk
// ledger-write-confirmation.schema.json's two branches' fields, all
// optional except success. Deliberately NOT the same shape as the on-disk
// schema, which stays a strong oneOf and validates ledger_update.py's own
// stdout at the script level. ledgerWriteSucceeded() below is what
// actually enforces the success-branch field set and rejects a
// success:true return that also carries a failure-only key.
const LEDGER_WRITE_SCHEMA = {
  type: "object",
  additionalProperties: false,
  required: ["success"],
  properties: {
    success: { type: "boolean" },
    status: { type: "string" },
    fragment_path: { type: "string" },
    fragment_sha1: { type: "string" },
    error: { type: "string" },
    exit_code: { type: "integer" },
    stderr: { type: "string" },
  },
};

// Flat agent-facing literal (CONTRACT §1) -- same union treatment as
// LEDGER_WRITE_SCHEMA above. missing_segments uses the RELAXED union shape
// {type:"array", items:{type:"string"}} (no maxItems) so the same literal
// can carry either branch's missing_segments; ledgerMergeSucceeded() below
// is what actually enforces the success branch's missing_segments.length
// === 0 requirement (the maxItems:0 the old success-branch literal used to
// express directly).
const LEDGER_MERGE_SCHEMA = {
  type: "object",
  additionalProperties: false,
  required: ["success"],
  properties: {
    success: { type: "boolean" },
    ledger_path: { type: "string" },
    n_segments: { type: "integer" },
    missing_segments: { type: "array", items: { type: "string" } },
    stale_segments: { type: "array", items: { type: "string" } },
    error: { type: "string" },
    exit_code: { type: "integer" },
    stderr: { type: "string" },
  },
};

// ---------------------------------------------------------------------------
// Constants substituted once at instantiation time (see the header comment
// above for the full token list and the JSON-escaping contract on the verse
// policy token specifically).
// ---------------------------------------------------------------------------

const PY = "python3";
const ROOT = "{{DURABLE_ROOT}}";
const RUN_ID = "{{RUN_ID}}";
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
// into shell command strings below (translatePrompt/reviewDispatchPrompt/
// reviewWaitPrompt/readReviewPrompt/fixPrompt/waitPrompt/recordLedgerPrompt/
// mergeLedgerPrompt), including bash for-loops in waitPrompt and
// reviewWaitPrompt -- an unsafe id ('../', '/', shell metacharacters) would
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
// Exact-key-set JS guards (CONTRACT §5). The flat schemas above no longer
// discriminate success/failure shape the way the old oneOf branches did --
// the tool-use API cannot enforce a top-level combinator, so a returned
// object that claims success:true/match:true while ALSO carrying a
// failure-only key (error/exit_code/stderr/mismatch_detail), or that is
// missing a required success-branch field, must be treated as a failed
// call here -- never trusted, never routed down the success path. The
// on-disk strong schemas plus each script's own runtime self-validation
// are the second layer behind these guards, not a substitute for them.
// ---------------------------------------------------------------------------

function isNonEmptyString(v) {
  return typeof v === "string" && v.length > 0;
}

function hasOnlyKeys(obj, allowedKeys) {
  return Object.keys(obj).every((k) => allowedKeys.indexOf(k) !== -1);
}

const LEDGER_WRITE_SUCCESS_KEYS = ["success", "status", "fragment_path", "fragment_sha1"];
const LEDGER_MERGE_SUCCESS_KEYS = ["success", "ledger_path", "n_segments", "missing_segments", "stale_segments"];
const FAILURE_ONLY_KEYS = ["error", "exit_code", "stderr"];

function ledgerWriteSucceeded(raw) {
  if (!raw || raw.success !== true) return false;
  if (FAILURE_ONLY_KEYS.some((k) => k in raw)) return false;
  if (!hasOnlyKeys(raw, LEDGER_WRITE_SUCCESS_KEYS)) return false;
  return isNonEmptyString(raw.status) && isNonEmptyString(raw.fragment_path) && isNonEmptyString(raw.fragment_sha1);
}

function ledgerMergeSucceeded(raw) {
  if (!raw || raw.success !== true) return false;
  if (FAILURE_ONLY_KEYS.some((k) => k in raw)) return false;
  if (!hasOnlyKeys(raw, LEDGER_MERGE_SUCCESS_KEYS)) return false;
  return (
    isNonEmptyString(raw.ledger_path) &&
    Number.isInteger(raw.n_segments) &&
    Array.isArray(raw.missing_segments) && raw.missing_segments.length === 0 &&
    Array.isArray(raw.stale_segments)
  );
}

function artifactCheckMatched(art) {
  return !!art && art.match === true && !("mismatch_detail" in art);
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
  const dispatchToken = RUN_ID + ":" + seg;
  const lines = [];
  lines.push("Effort: high. Literary translation of segment " + seg + " (" + SOURCE_LANG + " to " + TARGET_LANG + ").");
  lines.push("Read in order: " + ROOT + "/translate_TASK.md ; " + ROOT + "/style_bible.md (in full, especially the word-sense/realia traps section) ; " + ROOT + "/segments/segpack_" + seg + ".json (source text plus the frozen name/realia canon for this segment).");
  lines.push("Verse policy for this project: " + VERSE_POLICY_INSTRUCTION_BLOCK);
  lines.push("Translate every block, footnote, and verse this segpack contains. Copy every placeholder sentinel (e.g. ⟦FNREF_N⟧, ⟦VERSE_...⟧) byte for byte, in its correct position in the sentence -- never translate, drop, or reword a sentinel itself. Any embedded third-language text (Latin, an older form of the source language, or similar) gets an in-text gloss, never a notes-only translation. Use canon_names forms verbatim; for a new_names entry not yet in the canon, choose a reasoned rendering and flag it in notes as NEW:.");
  lines.push("Write your draft exactly per translate_TASK.md's own schema to: " + ROOT + "/segments/" + seg + ".draft.json -- and add one extra top-level field beyond that schema: dispatch_token, with exactly this literal string value: " + JSON.stringify(dispatchToken) + ". This is run-scoping freshness metadata, not part of the translation itself.");
  lines.push("Then self-check coverage: run " + PY + " " + ROOT + "/scripts/validate_draft.py " + seg + ". If it prints FAIL, fix the draft and rewrite the file, and repeat until it prints OK.");
  lines.push("Return exactly the line: DONE " + seg);
  return lines.join("\n");
}

// The dispatch half of the review restructure (#97 #88 #87-artifact --
// reviewDispatchPrompt/reviewWaitPrompt/readReviewPrompt/callArtifactCheck
// replace the old, single schema-validated reviewPrompt/callReview).
// Schema-less, fire-and-forget, matching translateStage's own discipline:
// the codex reviewer writes segments/{seg}.review.json atomically and
// self-validates its own shape before returning -- this script never
// trusts the agent's return value here, only the on-disk artifact
// reviewWaitPrompt's bounded poll below confirms.
//
// Self-contained: this prompt carries the FULL review.schema.json field
// contract inline and explicitly supersedes review_TASK.md for that
// contract -- a resumed project's review_TASK.md may predate this change
// and must never be trusted over the fields spelled out here.
function reviewDispatchPrompt(seg, roundLabel) {
  const dispatchToken = RUN_ID + ":" + seg + ":r" + roundLabel;
  const draftToken = RUN_ID + ":" + seg;
  const lines = [];
  lines.push("Effort: high. Single reviewer covering both accuracy and literary quality for segment " + seg + " (" + SOURCE_LANG + " to " + TARGET_LANG + "), round " + roundLabel + ".");
  lines.push("This prompt is self-contained and supersedes " + ROOT + "/review_TASK.md for the field contract below. Read review_TASK.md for narrative guidance only -- it may predate this instruction, and its own field list must never override the fields spelled out here.");
  lines.push("First run the deterministic gate: " + PY + " " + ROOT + "/scripts/validate_draft.py " + seg + " -- remember whether it printed OK or FAIL, and any defects it named.");
  lines.push("Before reading the draft, compute its current sha1 by running: " + PY + " " + ROOT + "/scripts/draft_sha1.py " + seg + " -- this becomes your draft_sha1 value below, and it must be computed BEFORE you read the draft file itself.");
  lines.push("Then read: " + ROOT + "/review_TASK.md ; " + ROOT + "/style_bible.md ; " + ROOT + "/segments/segpack_" + seg + ".json ; " + ROOT + "/segments/" + seg + ".draft.json.");
  lines.push("As soon as you read the draft, check its own dispatch_token field: it must equal exactly this literal string: " + JSON.stringify(draftToken) + ". If it does not match exactly, STOP here -- this draft belongs to a different, stale run. Do not review it, do not write " + ROOT + "/segments/" + seg + ".review.json at all, and return exactly the line: DRAFT_TOKEN_MISMATCH " + seg + " instead of the REVIEWED line below.");
  lines.push("Verse policy for this project: " + VERSE_POLICY_INSTRUCTION_BLOCK);
  lines.push("Check the draft against the source for: full accuracy (no omissions or distortions), word-sense and realia fidelity for the source era and context -- ask explicitly whether each notable word means what it meant in that period and context, not what it means today -- name/canon fidelity, placeholder sentinel fidelity, verse per the policy above, and literary quality (register, idiom, natural seams, rhythm).");
  lines.push("Build a JSON object with exactly these five fields: clean (true only if there are no findings that require a fix round), coverage_ok (true only if the deterministic gate above printed OK), findings (an array of objects with loc/severity/issue/suggest -- use a loc like \"VERSE:{vid}\" for a verse-specific finding), draft_sha1 (the value you computed before reading the draft, above), and dispatch_token (exactly this literal string: " + JSON.stringify(dispatchToken) + ").");
  lines.push("Write that exact object to a fresh temp file in the same directory (e.g. via python3, json.dump to " + ROOT + "/segments/." + seg + ".review.json.tmp.<a unique suffix, e.g. your own process id>), then atomically rename/replace it (e.g. os.replace) into place at: " + ROOT + "/segments/" + seg + ".review.json -- never write that destination path directly, so a concurrent reader never observes a half-written file.");
  lines.push("Return exactly the line: REVIEWED " + seg);
  return lines.join("\n");
}

// Bounded poll for the review dispatch above -- review_ready.py fully
// validates the on-disk review.json (full schema, draft_sha1 freshness,
// AND the dispatch_token this round's dispatch just wrote) before
// reporting ready, so a stale or still-mid-write artifact never passes.
// Same 45x20s/~15min bound as translate's own waitPrompt below.
function reviewWaitPrompt(seg, dispatchToken) {
  const lines = [];
  lines.push("The codex reviewer for segment " + seg + " is working in the background. Wait for it to finish: run exactly one bash command, a polling loop:");
  lines.push("for i in $(seq 1 45); do " + PY + " " + ROOT + "/scripts/review_ready.py " + seg + " --expect-token " + dispatchToken + " && exit 0; sleep 20; done; exit 1");
  lines.push("If that command exits successfully (review_ready.py reported ready), return exactly the line: READY " + seg);
  lines.push("Otherwise, after the timeout (about 15 minutes), return exactly the line: TIMEOUT " + seg);
  lines.push("Do nothing else -- do not touch any files, and do not review anything yourself.");
  return lines.join("\n");
}

// Mechanical read only, once reviewWaitPrompt confirms the on-disk artifact
// is ready. review.json carries five fields on disk (the four verdict
// fields plus dispatch_token); this prompt returns only the four verdict
// fields, matching REVIEW_SCHEMA exactly -- dispatch_token is run-scoping
// metadata, never part of the returned verdict.
function readReviewPrompt(seg) {
  const lines = [];
  lines.push("Effort: low. Mechanical read only -- do not judge or second-guess the reviewer's verdict.");
  lines.push("Segment: " + seg + ". Durable root: " + ROOT + ".");
  lines.push("Read: " + ROOT + "/segments/" + seg + ".review.json");
  lines.push("That file has five top-level fields: clean, coverage_ok, findings, draft_sha1, and dispatch_token. Return a structured result with exactly the first four -- clean, coverage_ok, findings, draft_sha1 -- verbatim from the file. Omit dispatch_token from your return; it is internal run-scoping metadata, not part of the verdict.");
  return lines.join("\n");
}

// Bounded poll for the translate dispatch above -- draft_ready.py's
// --expect-token requires the on-disk draft's dispatch_token to equal this
// run's own token before reporting READY, so an old-run straggler
// translator's draft (old token) is never accepted here (references/
// ledger-and-resumability.md's resume-integrity gate, commit-gate point (i)).
function waitPrompt(seg) {
  const dispatchToken = RUN_ID + ":" + seg;
  const lines = [];
  lines.push("The codex translator for segment " + seg + " is working in the background. Wait for it to finish: run exactly one bash command, a polling loop:");
  lines.push("for i in $(seq 1 45); do " + PY + " " + ROOT + "/scripts/draft_ready.py " + seg + " --expect-token " + dispatchToken + " && exit 0; sleep 20; done; exit 1");
  lines.push("If that command exits successfully (draft_ready.py printed READY), return exactly the line: READY " + seg);
  lines.push("Otherwise, after the timeout (about 15 minutes), return exactly the line: TIMEOUT " + seg);
  lines.push("Do nothing else -- do not touch any files, and do not translate anything yourself.");
  return lines.join("\n");
}

// Deliberate, documented 3-argument departure from the proven reference's
// 2-argument fixPrompt(seg, round) shape -- see references/gotchas.md item 5
// and references/engine-loop.md's R1. revObj is the SAME schema-validated
// object readReviewPrompt already returned this round, still in this
// script's own in-memory state, spliced in directly rather than re-read off
// review_path(seg). Do not revert this to a 2-argument shape.
//
// The call/dispatch shape here is deliberately UNCHANGED by the #97
// restructure (a plain, unbounded, schema-less Claude await, no agentType --
// see references/engine-loop.md's "The FIX call is NOT restructured" note):
// a forward-detached job can't happen on a Claude call, and a sha-changed
// readiness gate would false-time-out a no-op fix. The one content addition
// below (preserve dispatch_token) is load-bearing, not an architecture
// change: this prompt tells the agent to REWRITE the entire draft.json, and
// without this line it would have no way to know a dispatch_token field
// even exists, silently dropping it on every fixed segment's first round --
// which would then always fail ledger_update.py's convergence-time
// dispatch_token check (references/ledger-and-resumability.md).
function fixPrompt(seg, round, revObj) {
  const revObjJSON = JSON.stringify(revObj);
  const lines = [];
  lines.push("Effort: high. You are the Claude editor applying review findings to segment " + seg + ", round " + round + ".");
  lines.push("The reviewer's structured verdict for this round, to apply in full, is exactly this JSON object -- use only this, do not re-read " + ROOT + "/segments/" + seg + ".review.json for findings:");
  lines.push(revObjJSON);
  lines.push("Important: only codex translates. If the draft is missing or is not actually ready -- check by running " + PY + " " + ROOT + "/scripts/draft_ready.py " + seg + " -- do not translate it yourself: return exactly the line DRAFT_MISSING " + seg + " and write nothing.");
  lines.push("Otherwise, read " + ROOT + "/segments/" + seg + ".draft.json and " + ROOT + "/segments/segpack_" + seg + ".json, and carefully apply every finding above to the draft. Never touch a placeholder sentinel (e.g. ⟦FNREF_...⟧, ⟦VERSE_...⟧) -- copy each one byte for byte in place. Keep the verse policy: " + VERSE_POLICY_INSTRUCTION_BLOCK);
  lines.push("Never change the set of block, footnote, or verse keys -- they must stay exactly 1:1 with the segpack.");
  lines.push("The draft also carries a dispatch_token top-level field -- copy its existing value byte for byte into your rewritten draft, unchanged; never invent, drop, or recompute it.");
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
// via cache_key.py and fold it into the payload it writes, alongside a
// run_token field carrying this run's bare RUN_ID -- ledger_update.py uses
// run_token to refuse recording convergence when the on-disk draft or
// review artifact's own dispatch_token belongs to a different (stale) run.
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
    lines.push("Also add a run_token field to the payload object with exactly this literal string value: " + JSON.stringify(RUN_ID) + " -- this run's identifier. ledger_update.py uses it to refuse recording convergence if the on-disk draft's or review.json's own dispatch_token belongs to a stale, different run.");
  }
  lines.push("Write the resulting payload object, and nothing else, to a fresh scratch file at " + ROOT + "/runs/.ledger_update_payload." + seg + ".<a unique suffix, e.g. your own process id> -- never reuse an existing scratch file.");
  lines.push("Then run: " + PY + " " + ROOT + "/scripts/ledger_update.py " + seg + " --payload-file <the scratch file path you just wrote>");
  lines.push("Capture that command's single printed JSON line.");
  lines.push("If it reports success: true, re-read the fragment file at the fragment_path it reported, independently compute the sha1 of that file's raw bytes (e.g. with Python's hashlib, reading the file in binary mode), and confirm it matches the fragment_sha1 the command reported. Do not trust the command's own fragment_sha1 claim without this independent check.");
  lines.push("Return exactly one structured result matching the required schema: on a verified success, success: true plus the status/fragment_path/fragment_sha1 the command reported; on any failure, or if the independent sha1 check does not match, success: false plus an error string describing what went wrong.");
  return lines.join("\n");
}

// --run-token RUN_ID (new CLI flag, alongside the existing --expected-segs)
// is this function's own field to document for ledger_merge.py: before
// reporting success/batchComplete, the script re-asserts for EACH expected
// converged segment that its on-disk draft's and review.json's own
// dispatch_token both equal this run's token, and that the draft's current
// sha1 still matches the ledger-recorded draft_sha1 -- closing the window
// between a per-segment convergence write and this batch-final check
// (references/ledger-and-resumability.md).
function mergeLedgerPrompt(segs) {
  const segsCsv = segs.join(",");
  const lines = [];
  lines.push("Effort: low. Mechanical ledger completeness check only -- no translation or review judgment.");
  lines.push("Durable root: " + ROOT + ".");
  lines.push("Run: " + PY + " " + ROOT + "/scripts/ledger_merge.py --expected-segs " + segsCsv + " --run-token " + RUN_ID);
  lines.push("Capture that command's single printed JSON line.");
  lines.push("Independently re-read " + ROOT + "/runs/ledger.json and confirm every one of these segment ids has a matching key: " + segsCsv + ". This is a completeness/subset check only: ledger.json may also contain extra keys left over from earlier batches, and that is expected, never a failure by itself. Only a listed segment id with no matching key at all is a failure.");
  lines.push("Return exactly one structured result matching the required schema: on a verified success, success: true plus the ledger_path/n_segments/missing_segments/stale_segments the command reported; on any failure, or if your own independent check disagrees with the command's claim, success: false plus an error string.");
  return lines.join("\n");
}

// Right after every review verdict readReviewPrompt returns, including the
// final confirming one. revObj is spliced in directly (same mechanism
// fixPrompt uses); the script, not the agent, does the actual comparison
// against the on-disk review_path(seg) -- see
// references/workflow-schema-validation.md. review_artifact_check.py
// projects BOTH sides down to exactly {clean, coverage_ok, findings,
// draft_sha1} before comparing, so a disk file that also carries
// dispatch_token (five fields) still matches this four-field expected
// object.
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
// with the mandatory JS-side payload-intent verification: after
// ledgerWriteSucceeded() accepts the return, this script itself (not a new
// prompt) confirms the returned fragment_path's segment component matches
// seg and the returned status matches fields.status. A mismatch is treated
// the same as a failed write, never retried through the same ledger-write
// channel.
// ---------------------------------------------------------------------------
async function recordLedgerCall(seg, fields, label) {
  const raw = await agent(recordLedgerPrompt(seg, fields), {
    effort: "low", phase: "Ledger", label: label, schema: LEDGER_WRITE_SCHEMA,
  });

  if (!ledgerWriteSucceeded(raw)) {
    const detail = raw && typeof raw.error === "string" ? raw.error : "ledger_update.py write did not report success";
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

async function callReviewDispatch(seg, roundLabel) {
  const label = "review-dispatch:" + seg + ":r" + roundLabel;
  return await agent(reviewDispatchPrompt(seg, roundLabel), {
    agentType: "codex:codex-rescue", effort: "high", phase: "ReviewFix", label: label,
  });
}

async function callReadReview(seg, roundLabel, isRetry) {
  const label = "review-read:" + seg + ":r" + roundLabel + (isRetry ? ":retry" : "");
  return await agent(readReviewPrompt(seg), {
    effort: "low", phase: "ReviewFix", label: label, schema: REVIEW_SCHEMA,
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

// The read+check pair getVerifiedReview below retries as ONE shared unit
// (never independently) -- see getVerifiedReview's own comment for the
// full retry-budget rationale.
async function readAndCheck(seg, roundLabel, isRetry) {
  const rev = await callReadReview(seg, roundLabel, isRetry);
  if (!rev) return { rev: null, art: null };
  const art = await callArtifactCheck(seg, rev, roundLabel, isRetry);
  return { rev: rev, art: art };
}

// Runs one review point -- dispatch, bounded wait, read, artifact-check --
// through to a verified verdict, per references/workflow-schema-validation.md
// and references/false-green-gate.md. The dispatch is a schema-less,
// fire-and-forget codex call (translateStage's own pattern); this function
// never trusts its return value, only the on-disk artifact the bounded
// poll below confirms. TIMEOUT ends the point immediately as
// blocked/review-timeout -- no read/check is attempted against a draft
// that may still be mid-write.
//
// After a successful wait, ONE shared retry budget covers the read and
// the check together: a null read OR a match:false check retries the
// (read THEN check) pair once, fresh; still failing afterward ->
// blocked/review-null (the retry's own read came back null) or
// blocked/review-artifact-mismatch (the retry's read succeeded but still
// didn't match) -- never two independent read-retry/check-retry budgets.
// Call budget for one review point: dispatch(1) + wait(1) + read(1) +
// check(1) + [retry: read(1) + check(1)] = 6 calls, worst case.
async function getVerifiedReview(seg, roundLabel) {
  const dispatchToken = RUN_ID + ":" + seg + ":r" + roundLabel;

  await callReviewDispatch(seg, roundLabel);

  const waitLabel = "review-wait:" + seg + ":r" + roundLabel;
  const ready = await agent(reviewWaitPrompt(seg, dispatchToken), {
    effort: "low", phase: "ReviewFix", label: waitLabel,
  });
  if (!ready || ready.indexOf("READY") === -1) {
    return { status: "blocked", reason: "review-timeout" };
  }

  const first = await readAndCheck(seg, roundLabel, false);
  if (artifactCheckMatched(first.art)) return { status: "ok", rev: first.rev };

  const retry = await readAndCheck(seg, roundLabel, true);
  if (!retry.rev) return { status: "blocked", reason: "review-null" };
  if (artifactCheckMatched(retry.art)) return { status: "ok", rev: retry.rev };

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
//
// Per segment: 3 fixed calls (in_progress ledger write, translate dispatch,
// translate wait) + up to MAXFIX normal rounds at 7 calls each (a review
// point's 6-call worst case plus 1 fix call) + 1 mandatory final review
// point (6 calls, no fix dispatched) + 1 terminal ledger write
// = 3 + 7*MAXFIX + 6 + 1 = 10 + 7*MAXFIX. Batch-level: 1 (the final
// merge-ledger completeness check; there is no batch pre-clean call).
// ---------------------------------------------------------------------------
const estimatedCalls = 1 + SEGS.length * (10 + 7 * MAXFIX);
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

if (!ledgerMergeSucceeded(mergeResult)) {
  const detail = mergeResult && typeof mergeResult.error === "string" ? mergeResult.error : "ledger_merge.py completeness check did not report success";
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
