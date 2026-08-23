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
//   {{EFFORT}}                           -- engine.effort (enum: low/medium/high/xhigh), substituted
//                                          as a plain quoted string, same style as {{SOURCE_LANG}}
//                                          above. Drives BOTH carriers for every codex/fix pass in
//                                          this file from the SAME value: the --effort flag on the
//                                          two detached codex_job.py launches below (translate/
//                                          review) and the Claude fix step's own agent() effort
//                                          option -- never one hard-coded while the other reads
//                                          this token (see references/ledger-and-resumability.md's
//                                          dual-injection rule).
//   {{MODEL}}                            -- engine.model, or an EMPTY STRING when unset. Substituted
//                                          as a plain quoted string. Threads ONLY to the two detached
//                                          codex_job.py launches below (translate/review), as an
//                                          optional single-quoted --model argument omitted entirely
//                                          from the launch command when this value is empty -- never
//                                          threaded to the Claude fix step (a codex model id is not
//                                          meaningful there).
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
//   {{MAX_CODEX_JOBS_PER_BATCH}}          -- engine.max_codex_jobs_per_batch, substituted as a BARE
//                                          integer literal, same style as {{BATCH_AGENT_CAP}} above.
//                                          #409 stage 0: a SECOND, independent preflight cap sized
//                                          against real codex dispatches rather than Workflow
//                                          agent() calls (see the "max_codex_jobs_per_batch
//                                          preflight" block below). Unlike {{BATCH_AGENT_CAP}},
//                                          engine.max_codex_jobs_per_batch is OPTIONAL in
//                                          profile.schema.json -- when absent, the orchestrating
//                                          session substitutes the schema field's own documented
//                                          "default" (400) here, mirroring how {{MODEL}} falls back
//                                          to its own documented empty-string default when
//                                          engine.model is unset.
//   {{CODEX_COMPANION_PATH_JSON}}         -- resolved codex-companion.mjs path, substituted as a
//                                          strict json.dumps JS STRING LITERAL (i.e. WITH its own
//                                          surrounding quotes -- the token sits OUTSIDE quotes in
//                                          `const COMPANION = {{CODEX_COMPANION_PATH_JSON}};`, unlike
//                                          the plain-string tokens above). The orchestrating session
//                                          resolves it once via resolve_codex_companion.py and
//                                          json.dumps's the raw companion_path; that resolver rejects
//                                          any path containing a single quote / control char /
//                                          newline, so the resulting COMPANION value is always safe
//                                          to splice into the driver launch below as a SINGLE-QUOTED
//                                          bash argument (space/unicode paths included).
//   {{PLUGIN_ROOT}}                       -- #412: the plugin's own install root (NEVER
//                                          {{DURABLE_ROOT}}/scripts/, the Step-0a COPY the codex
//                                          process this driver launches can write to), or an EMPTY
//                                          STRING when this dispatch does not opt into the redirect.
//                                          Same substitution shape as {{CODEX_COMPANION_PATH_JSON}}
//                                          immediately above (a strict json.dumps JS STRING LITERAL,
//                                          WITH its own surrounding quotes, sitting OUTSIDE quotes in
//                                          `const PLUGIN_ROOT = {{PLUGIN_ROOT}};`) and the SAME safety
//                                          contract: the orchestrating session is responsible for a
//                                          value that is always safe to splice as a SINGLE-QUOTED bash
//                                          argument (no single quote / control char / newline). Threads
//                                          to the two detached codex_job.py launches below
//                                          (translate/review) as an optional single-quoted
//                                          --plugin-root argument, omitted entirely from the launch
//                                          command when this value is empty (same conditional-omission
//                                          shape as {{MODEL}}/MODEL_ARG above) -- codex_job.py's own
//                                          _trusted_scripts_dir() then falls back to its pre-#412
//                                          default unchanged. Never threaded to the Claude fix step
//                                          (it launches no codex_job.py of its own).
//
// W5 dispatch model (#198 -- codex is no longer fire-and-forget from a
// Workflow agent turn): every codex translate/review is launched by the
// DETACHED (nohup) codex_job.py driver, never by a codex-agentType agent()
// call. A plain-Claude DISPATCHER agent (translateDrivePrompt/
// reviewDrivePrompt -- no agentType, effort low) generates a per-dispatch
// nonce DISP, writes the codex task text (translatePrompt/
// reviewDispatchPrompt, each carrying EXACTLY ONE ⟦JOB_OUT⟧ output
// placeholder) to a fresh <root>/segments/.codex_task.<kind>.<seg>.<DISP>
// file, launches codex_job.py DETACHED so it OUTLIVES the dispatcher's turn
// (nohup, </dev/null >/dev/null 2>&1 & -- NO setsid, NO external `timeout`
// binary), and returns exactly `DISPATCHED <seg> <DISP>`. codex writes an
// ISOLATED attempt file (the driver substitutes ⟦JOB_OUT⟧ with it) and the
// driver validate-before-promotes it -- under a per-seg flock -- to the
// canonical segments/<seg>.{draft,review}.json: codex writes disk, its own
// return line is NEVER the verdict. The Workflow's OWN wait poll
// (waitPrompt/reviewWaitPrompt) is the AUTHORITATIVE independent gate -- a
// POLLING-BUDGET loop (budget = CODEX_DEADLINE_SEC + CODEX_FINALIZE_BUDGET_SEC
// + CODEX_WAIT_GRACE_SEC = 3450 s, spent since 1.16.1 across WAIT_CHUNKS = 8
// bounded agent calls rather than inside one, because the agent Bash tool
// clamps a single call at a measured 600 s regardless of the timeout requested
// (#348); NO `timeout` binary)
// whose ACCEPT is a FULL re-validation of the CURRENT canonical (translate:
// draft_ready.py --expect-token AND validate_draft.py; review: review_ready
// .py --expect-token), never a trust of any driver-written file.
// A failed or exhausted poll is followed by ONE non-polling authoritative
// re-check before any timeout is declared, so a run that finished while the
// poll was between chunks is not reported as a timeout. Its
// optional fail-fast is a pure presence check on the DISP-named sentinel
// segments/.codex_failed.<seg>.<DISP> (the driver writes it only when it did
// NOT promote), evaluated ONLY AFTER the ACCEPT gate did not pass this
// iteration -- so a valid canonical always wins over any sentinel, and an
// empty DISP (unparseable dispatcher return) simply disables fail-fast and
// polls to the bound (safe degradation). CODEX_DEADLINE_SEC=2700 (the
// 45-min poll window -- covers the pilot's longest segment; tunable),
// CODEX_FINALIZE_BUDGET_SEC=150, FINALIZE_TAIL=10, PER_CALL_CAP=90,
// CODEX_WAIT_GRACE_SEC=600 mirror codex_job.py's own constants.
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
    { title: "Translate", detail: "the codex_job.py driver (launched detached) translates each segpack to an isolated attempt, validates it, and atomically promotes it to the canonical draft" },
    { title: "ReviewFix", detail: "the codex_job.py driver reviews (launched detached, validate-before-promote, bounded-polled) and Claude fixes, looped until clean and coverage_ok, capped at MAX_FIX_ROUNDS plus one mandatory final confirming review" },
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
// consume-site JS guards further down this file (ledgerWriteSucceeded,
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
      description: "Issues the reviewer wants fixed. May remain non-empty even when clean is true (residual low/cosmetic items the reviewer chose not to fix-round); clean is judged solely on whether any finding requires a fix round.",
      items: {
        type: "object",
        additionalProperties: false,
        required: ["loc", "severity", "issue", "suggest"],
        properties: {
          loc: {
            type: "string",
            description: "Location the finding applies to. ALWAYS colon-delimited, never a bare or holistic token: a block id (e.g. PARA:seg01:0001), FN:{n} for a footnote, VERSE:{vid} for a verse, or NOTE:{n} for one entry of the draft's own notes[] array. NOTE:{n} is a 0-based INDEX into notes[]; FN:{n} is the footnote's own NUMBER.",
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
// match:true-implies-no-mismatch-evidence for this literal's return --
// judged on the field's VALUE, not its presence, for the same reason the
// ledger guards are (#289): declaring the field here advertises it as
// fillable on a match, and agents fill what a schema advertises.
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
// success:true return that also carries real failure EVIDENCE. Note the
// cost of the union, and why that guard judges values rather than keys
// (#289): declaring error/exit_code/stderr here ADVERTISES them as fillable
// on a success return, and agents do fill them -- `exit_code: 0` on a
// perfectly good write is a routine, truthful return, not a red flag.
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

// #131 facet A -- flat agent-facing literal for draftPresentAndValid's probe
// call below. Declared here with the other schema literals per the same TDZ
// rule the block comment above documents: the probe fires from inside
// runRound, which is defined (and, more importantly, called via pipeline())
// well after this point in the file, but the const itself must still sit
// above every use textually.
const DRAFT_PROBE_SCHEMA = {
  type: "object",
  additionalProperties: false,
  required: ["present"],
  properties: {
    present: { type: "boolean" },
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
const MAX_CODEX_JOBS_PER_BATCH = {{MAX_CODEX_JOBS_PER_BATCH}};
// #197 -- engine.effort/engine.model. EFFORT drives both the codex_job.py
// --effort flag (translate/review launches below) and the Claude fix step's
// agent() effort option, always from this one value. MODEL is the empty
// string when engine.model is unset (see the header token doc above); it
// threads only to the two codex_job.py launches, never to the fix step.
const EFFORT = "{{EFFORT}}";
const MODEL = "{{MODEL}}";

// #197 -- defense-in-depth: EFFORT and MODEL are substituted from profile.yml
// (schema-validated at Step 0) but are re-checked HERE, before either reaches
// the codex_job.py dispatch SHELL command built in translateDrivePrompt/
// reviewDrivePrompt below -- EFFORT is spliced UNQUOTED, MODEL single-quoted.
// Mirrors the SEG_ID_RE / parseDisp guards below: a poisoned or hand-edited
// profile.yml (or a resume that skips Step 0's schema validation) fails LOUDLY
// here instead of silently reaching a shell splice. Allowlists kept identical
// to profile.schema.json's engine.effort enum and engine.model pattern.
const EFFORT_RE = /^(low|medium|high|xhigh)$/;
if (!EFFORT_RE.test(EFFORT)) {
  throw new Error("Unsafe engine.effort " + JSON.stringify(EFFORT) + ": must be one of low|medium|high|xhigh");
}
const MODEL_RE = /^[A-Za-z0-9][A-Za-z0-9._-]*$/;
if (MODEL !== "" && !MODEL_RE.test(MODEL)) {
  throw new Error("Unsafe engine.model " + JSON.stringify(MODEL) + ": must match ^[A-Za-z0-9][A-Za-z0-9._-]*$ (or be empty when unset)");
}
// MODEL_ARG single-quoted, appended only when truthy; a bare --model flag with
// no value is never emitted. Hoisted here (identical in the translate and
// review codex_job.py launches below; depends only on MODEL).
const MODEL_ARG = MODEL ? " --model '" + MODEL + "'" : "";
const VERSE_POLICY_INSTRUCTION_BLOCK = "{{VERSE_POLICY_INSTRUCTION_BLOCK}}";

// #198 -- resolved codex-companion.mjs path. Substituted as a strict
// json.dumps JS STRING LITERAL (WITH its own quotes -- the token sits
// outside quotes here, unlike the plain-string tokens above); see the header
// comment's {{CODEX_COMPANION_PATH_JSON}} entry. Spliced into every driver
// launch below as a SINGLE-QUOTED bash argument (resolve_codex_companion.py
// rejects a path with a quote/control/newline, so single-quoting is safe for
// space/unicode paths and injection-proof).
const COMPANION = {{CODEX_COMPANION_PATH_JSON}};

// #412 -- the plugin's own install root (see the header comment's
// {{PLUGIN_ROOT}} entry), or "" when this dispatch does not opt into the
// redirect. Same JS-string-literal substitution shape as COMPANION above,
// but unlike COMPANION there is no dedicated resolver script
// (resolve_codex_companion.py's own counterpart) to lean on, so this file
// re-checks it itself -- mirroring EFFORT_RE/MODEL_RE above -- before it
// ever reaches the codex_job.py dispatch SHELL command: empty is valid (the
// redirect opt-out), a non-empty value must contain no single quote or
// control character -- the exact class that would break out of the
// SINGLE-QUOTED bash splice below.
const PLUGIN_ROOT = {{PLUGIN_ROOT}};
const PLUGIN_ROOT_UNSAFE_RE = /['\x00-\x1f\x7f]/;
if (PLUGIN_ROOT !== "" && PLUGIN_ROOT_UNSAFE_RE.test(PLUGIN_ROOT)) {
  // #412: this message deliberately names the concept ("plugin_root"), never
  // the literal double-brace token spelling -- writing the token's own
  // syntax into a runtime string here would make it a SECOND substitution
  // site, silently corrupted by the very instantiation step this check
  // exists to guard against (see EFFORT_RE/MODEL_RE above, which name
  // "engine.effort"/"engine.model" for the identical reason).
  throw new Error("Unsafe plugin_root value " + JSON.stringify(PLUGIN_ROOT) + ": must not contain a single quote or control character");
}
// PLUGIN_ROOT_ARG single-quoted, appended only when truthy -- same
// conditional-omission shape as MODEL_ARG above; a bare --plugin-root flag
// with no value is never emitted (codex_job.py's own argparse default,
// None, is reserved for "flag omitted entirely", not an empty string).
const PLUGIN_ROOT_ARG = PLUGIN_ROOT ? " --plugin-root '" + PLUGIN_ROOT + "'" : "";

// #198 -- driver/poll timing constants, mirroring codex_job.py's own
// constants (documented in the header comment's W5 dispatch model). Only the
// three that make up the wait bound (DEADLINE + FINALIZE_BUDGET + WAIT_GRACE)
// are used by this template's wait polls; FINALIZE_TAIL / PER_CALL_CAP are
// declared for parity/documentation with the driver's own internal budgets.
const CODEX_DEADLINE_SEC = 2700;        // 45-min poll window (tunable)
const CODEX_FINALIZE_BUDGET_SEC = 150;
const FINALIZE_TAIL = 10;
const PER_CALL_CAP = 90;
const CODEX_WAIT_GRACE_SEC = 600;
// The wait poll's POLLING BUDGET -- not an elapsed-time outer bound, and the
// distinction is load-bearing: the driver's deadline plus its finalize budget
// plus a grace margin, so the Workflow poll never gives up before the driver
// can promote/finalize. = 2700 + 150 + 600 = 3450 s. Since 1.16.1 it is spent
// across WAIT_CHUNKS bounded calls, so total WALL-CLOCK for a wait is this
// budget plus per-call overhead plus the final authoritative re-check -- the
// budget bounds how long the workflow POLLS, never how long the wait TAKES.
const WAIT_BOUND_SEC = CODEX_DEADLINE_SEC + CODEX_FINALIZE_BUDGET_SEC + CODEX_WAIT_GRACE_SEC;

// ---------------------------------------------------------------------------
// #348 -- the wait bound above is SPENT ACROSS SEVERAL AGENT CALLS, not one.
//
// MEASURED, not inferred: the agent's Bash tool clamps a single call at
// 600 000 ms regardless of the timeout the agent passes. The failing call in
// the P1 gate run asked for `timeout: 3600000` and still came back
// `Exit code 143 / Command timed out after 10m 0s`. So "just raise the
// timeout" is not available, and a one-call poll of WAIT_BOUND_SEC (3450 s)
// was killed at 600 s every time -- reported as translate-timeout /
// review-timeout while a clean canonical artifact sat unread on disk.
//
// Chunk i (1-based) polls for whatever is LEFT of WAIT_BOUND_SEC, never a flat
// WAIT_CHUNK_SEC -- so the chunk bounds SUM to WAIT_BOUND_SEC exactly. Flat
// chunks would not SPEND the declared bound, they would silently EXTEND it
// (8 * 480 = 3840 s), breaking the one contract WAIT_BOUND_SEC exists to
// state and falsifying every doc that quotes it.
//
// NOT PER_CALL_CAP. #348 suggested making the declared-but-unused
// PER_CALL_CAP = 90 load-bearing here; deliberately declined. PER_CALL_CAP
// mirrors codex_job.py:65's ceiling for THE DRIVER'S OWN SUBPROCESSES ("hard
// ceiling for ANY single subprocess") -- a different quantity from this
// template's per-agent-call poll bound, and conflating them would make one
// constant answer two unrelated questions. WAIT_CHUNK_SEC is the new
// load-bearing chunk bound; do not "restore" the conflation.
const BASH_CALL_CAP_SEC = 600;              // measured hard clamp (see CHANGELOG 1.16.1)
const WAIT_CHUNK_SEC = 480;                 // one chunk's own elapsed bound
const WAIT_CHUNK_TOOL_TIMEOUT_MS = 540000;  // what the chunk prompt tells the agent to pass
const WAIT_CHUNKS = Math.ceil(WAIT_BOUND_SEC / WAIT_CHUNK_SEC);   // 8
const WAIT_CALLS = WAIT_CHUNKS + 1;         // worst case per wait: chunks + one re-check

// Startup guards, not comments: a future raise of either constant re-creates
// #348 silently otherwise. They throw here, before pipeline() is ever called.
if (WAIT_CHUNK_TOOL_TIMEOUT_MS > BASH_CALL_CAP_SEC * 1000) {
  throw new Error(
    "WAIT_CHUNK_TOOL_TIMEOUT_MS (" + WAIT_CHUNK_TOOL_TIMEOUT_MS + " ms) exceeds the measured " +
    "Bash per-call clamp (" + BASH_CALL_CAP_SEC * 1000 + " ms): the agent would be told to ask " +
    "for a timeout it cannot get, and the chunk bound would stop being the real bound (#348)."
  );
}
if (WAIT_CHUNK_SEC * 1000 >= WAIT_CHUNK_TOOL_TIMEOUT_MS) {
  throw new Error(
    "WAIT_CHUNK_SEC (" + WAIT_CHUNK_SEC + " s) leaves no headroom under " +
    "WAIT_CHUNK_TOOL_TIMEOUT_MS (" + WAIT_CHUNK_TOOL_TIMEOUT_MS + " ms): the poll must reach its " +
    "own elapsed bound and print its marker BEFORE the tool kills the call (#348)."
  );
}

function waitChunkSec(i) {
  return Math.min(WAIT_CHUNK_SEC, WAIT_BOUND_SEC - (i - 1) * WAIT_CHUNK_SEC);
}

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
// #198 SEGS uniqueness guard (BLOCKER r10). The supported-model coherence
// proof for the wait poll's ACCEPT gate (see the header comment's W5
// dispatch model + references/orchestration-and-batching.md) rests on
// segments/<seg>.draft.json having a SINGLE writer (its own driver + its own
// sequential fixer). Nothing upstream enforces unique ids -- the manifest
// schema does not require uniqueness and select_segments.py appends every id
// with no dedup -- and the SEG_ID_RE loop above checks only SYNTAX, so a
// DUPLICATE id would make pipeline() run two branches for the same seg whose
// fixers rewrite the SAME canonical draft concurrently, breaking the
// single-writer premise with NO disobedient codex. A duplicate id is ALWAYS
// malformed anyway (two segments collide on one draft path). This is the
// authoritative gate: SEGS = args and this template ALWAYS validates SEGS
// before dispatch, so it catches both manifest-derived and args-passed
// duplicates. Kept template-only (declining a select_segments.py dedup)
// precisely so it flips only the already-flipping plugin_bundle_hash, never
// the orchestration_bundle_hash's resume-gating.
const seen = new Set();
for (const s of SEGS) {
  if (seen.has(s)) throw new Error("duplicate segment id " + JSON.stringify(s) + " in dispatch list — segment ids must be unique (they name canonical segments/<seg>.draft.json paths)");
  seen.add(s);
}

// ---------------------------------------------------------------------------
// #133 finding-loc authenticity gate. A schema-valid review verdict can
// still carry a fabricated finding if the reviewer agent died mid-judgment
// after it had already obtained a real draft_sha1/dispatch_token but before
// it ever inspected the actual draft content -- what it leaves behind is a
// clean-looking verdict whose finding(s) reference an abstract sentinel
// (TASK/PROCESS/SYSTEM/RUN) rather than any real content location. A
// conforming loc is ALWAYS a colon-delimited structural reference: a block
// id ("{btype}:{seg}:{ord}", e.g. PARA:seg01:0001, or the shorter HEAD:seg01
// shape some adapters emit -- btype is deliberately NOT a fixed enum, see
// manifest.schema.json; adapters may emit their own block types, so only
// the ":" shape is invariant across all of them), FN:n, VERSE:vid, or --
// #539 -- NOTE:n for one entry of the draft's own notes[] array. The
// named infra sentinels are bare, colonless tokens -- that is the one true
// invariant this gate can lean on without hardcoding a block-type allowlist
// (which would over-reject a legitimate custom adapter's own block types)
// or a segpack-membership check (which would over-reject a healthy
// reviewer's slightly-off but
// genuine content ref). NOTE WHAT THIS GATE ACTUALLY TESTS, since its name
// and its reason string both overstate it: it tests the SHAPE of a loc
// (colon-delimited vs bare token), never whether the loc resolves against
// this draft and never whether the finding is true. A colon-bearing loc
// naming a block that does not exist passes it. #539 is what the gap cost
// while notes[] had no conforming spelling: a reviewer with a TRUE finding
// about a note had to invent a colonless loc, and .every() then discarded
// its valid block findings alongside the invention. Deliberately no figures
// here -- the measured population is in the 1.39.0 CHANGELOG entry, which is
// where a number belongs: it goes stale, it is not actionable at this line,
// and restating it in several places is how the four copies drifted apart
// while this fix was being reviewed. Residual
// false-block: a healthy reviewer emitting a colonless holistic loc (e.g.
// "overall") would also be caught here -- deviates from the shipped
// block_id|FN:n|VERSE:vid|NOTE:n contract, but the
// failure direction stays safe: findingsAuthentic() feeding into
// getVerifiedReview below routes a non-authentic verdict to
// blocked/review-fabricated-loc, which #131's blanket blocked-branch
// ledger-skip already makes recoverable (re-reviewed next run), never a
// terminal escalation.
const AUTHENTIC_LOC_RE = /^[^\s:]+:.+$/;
function findingsAuthentic(rev) {
  if (!rev || !Array.isArray(rev.findings)) return true; // clean/empty verdict -> authentic
  return rev.findings.every((f) => f && typeof f.loc === "string" && AUTHENTIC_LOC_RE.test(f.loc));
}

// #133 -- shared "artifact matched -> authenticity gate -> ok" step, used
// by both the first attempt and the shared retry in getVerifiedReview
// below (DRY: keeps those two copies of the exact same check from
// silently drifting apart from each other over time).
function matchedVerdict(rev) {
  if (!findingsAuthentic(rev)) return { status: "blocked", reason: "review-fabricated-loc" };
  return { status: "ok", rev: rev };
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
// Consume-site JS guards (CONTRACT §5). The flat schemas above no longer
// discriminate success/failure shape the way the old oneOf branches did --
// the tool-use API cannot enforce a top-level combinator, so a returned
// object that claims success:true/match:true while ALSO carrying real
// EVIDENCE of failure (a non-empty error/stderr/mismatch_detail, a non-zero
// exit_code), or that is missing a required success-branch field, or that
// carries a key neither branch of its contract ever declared, must be
// treated as a failed call here -- never trusted, never routed down the
// success path. The on-disk strong schemas plus each script's own runtime
// self-validation are the second layer behind these guards, not a
// substitute for them.
//
// #289 -- all three guards used to reject on the mere PRESENCE of an
// optional failure-branch field. But a flat union advertises every one of
// those fields as fillable on EVERY call (that is what flattening costs),
// and agents fill what a schema advertises: a truthful relay of a
// successful ledger_update.py run volunteers `exit_code: 0` -- proof the
// script SUCCEEDED -- and the presence test read that proof as proof of
// failure, failing segments whose fragments were already correct on disk.
// Whether a given agent volunteers the field is model discretion, so the
// verdict was non-deterministic across identical prompts.
//
// Because that was the THIRD site of one defect class, the judgement is no
// longer re-implemented per guard: NO_FAILURE_EVIDENCE below is the single
// table saying what each optional field looks like when it carries NO
// evidence, and hasFailureEvidence() is the single place that consults it.
// Adding a fourth flat schema means adding a table row and an evidence-key
// list -- not writing another predicate. The `k in raw` presence idiom now
// appears exactly ONCE in this file, inside that helper, and
// tests/ledger_confirmation_schema.test.py fails the build if it
// reappears at any other site.
// ---------------------------------------------------------------------------

function isNonEmptyString(v) {
  return typeof v === "string" && v.length > 0;
}

// Deliberately NOT the negation of isNonEmptyString: a non-string is neither
// a non-empty string nor an empty one. The NO_FAILURE_EVIDENCE table leans
// on that asymmetry -- a wrong-typed error/stderr/mismatch_detail is
// unreadable evidence and must fail closed exactly as the old presence-only
// check did.
function isEmptyString(v) {
  return typeof v === "string" && v.length === 0;
}

// `0` is the ONLY exit status that testifies to success. `"0"`, `false` and
// `null` are all `!== 0`, so they fail closed like any other wrong type.
function isZeroExitCode(v) {
  return v === 0;
}

function hasOnlyKeys(obj, allowedKeys) {
  return Object.keys(obj).every((k) => allowedKeys.indexOf(k) !== -1);
}

const LEDGER_WRITE_SUCCESS_KEYS = ["success", "status", "fragment_path", "fragment_sha1"];
const LEDGER_MERGE_SUCCESS_KEYS = ["success", "ledger_path", "n_segments", "missing_segments", "stale_segments"];
const REVIEW_ARTIFACT_SUCCESS_KEYS = ["match"];
// The optional fields each flat schema declares for its FAILURE branch.
// Named for where failure evidence may APPEAR, not "failure-only" (#289):
// the flat schemas make them fillable on a success return too, and only
// their VALUE says which branch a return really is.
const FAILURE_EVIDENCE_KEYS = ["error", "exit_code", "stderr"];
const REVIEW_ARTIFACT_EVIDENCE_KEYS = ["mismatch_detail"];
// Every key the corresponding flat schema declares. hasOnlyKeys() is checked
// against these rather than the SUCCESS keys alone, so a benign,
// already-value-checked `exit_code: 0` is not re-rejected as an unexpected
// key -- that second rejection was the same #289 defect wearing a different
// hat. A key NEITHER branch declares (a merge field on a write return, an
// invented field) is still fatal, which is the work this check exists to do:
// the tool-use API's own additionalProperties:false is the second layer
// behind it, not a reason to drop it.
const LEDGER_WRITE_ALLOWED_KEYS = LEDGER_WRITE_SUCCESS_KEYS.concat(FAILURE_EVIDENCE_KEYS);
const LEDGER_MERGE_ALLOWED_KEYS = LEDGER_MERGE_SUCCESS_KEYS.concat(FAILURE_EVIDENCE_KEYS);
const REVIEW_ARTIFACT_ALLOWED_KEYS = REVIEW_ARTIFACT_SUCCESS_KEYS.concat(REVIEW_ARTIFACT_EVIDENCE_KEYS);

// The single table of "what does this optional field look like when it
// carries NO evidence of failure?". A text field testifies to nothing only
// when it is exactly the empty string -- never by its CONTENT, because
// judging whether "none"/"n/a"/"no mismatch" means "fine" is natural-language
// interpretation, which does not belong in a gate. Anything else, including
// a wrong-typed value, is evidence.
const NO_FAILURE_EVIDENCE = { error: isEmptyString, stderr: isEmptyString, mismatch_detail: isEmptyString, exit_code: isZeroExitCode };

// The one place this file tests a declared optional field for failure
// evidence. An absent field testifies to nothing; a present one testifies
// to failure unless the table's benign-value predicate accepts it. A field
// with no table entry is unclassifiable and counts as evidence -- fail
// closed rather than throw or wave it through.
function hasFailureEvidence(raw, evidenceKeys) {
  return evidenceKeys.some((k) => {
    if (!(k in raw)) return false;
    const benign = NO_FAILURE_EVIDENCE[k];
    return typeof benign !== "function" || !benign(raw[k]);
  });
}

function ledgerWriteSucceeded(raw) {
  if (!raw || raw.success !== true) return false;
  if (hasFailureEvidence(raw, FAILURE_EVIDENCE_KEYS)) return false;
  if (!hasOnlyKeys(raw, LEDGER_WRITE_ALLOWED_KEYS)) return false;
  return isNonEmptyString(raw.status) && isNonEmptyString(raw.fragment_path) && isNonEmptyString(raw.fragment_sha1);
}

function ledgerMergeSucceeded(raw) {
  if (!raw || raw.success !== true) return false;
  if (hasFailureEvidence(raw, FAILURE_EVIDENCE_KEYS)) return false;
  if (!hasOnlyKeys(raw, LEDGER_MERGE_ALLOWED_KEYS)) return false;
  return (
    isNonEmptyString(raw.ledger_path) &&
    Number.isInteger(raw.n_segments) &&
    Array.isArray(raw.missing_segments) && raw.missing_segments.length === 0 &&
    Array.isArray(raw.stale_segments)
  );
}

// #289 third site. review_artifact_check.py's own emit_match() prints a bare
// {"match": true} and NEVER a mismatch_detail alongside it, so any
// mismatch_detail on a match:true return was added by the relaying agent --
// exactly how exit_code got onto the ledger returns. This guard also gains
// the allowed-key check its two siblings always had; an undeclared key used
// to sail through as a match.
function artifactCheckMatched(art) {
  if (!art || art.match !== true) return false;
  if (hasFailureEvidence(art, REVIEW_ARTIFACT_EVIDENCE_KEYS)) return false;
  return hasOnlyKeys(art, REVIEW_ARTIFACT_ALLOWED_KEYS);
}

// ---------------------------------------------------------------------------
// #198 DISP capture (HIGH-3). A DISPATCHER agent (translateDrivePrompt/
// reviewDrivePrompt) returns exactly `DISPATCHED <seg> <DISP>`; the captured
// DISP is later interpolated into the wait command's shell path
// (segments/.codex_failed.<seg>.<disp>), so it MUST be validated in JS
// BEFORE prompt construction. parseDisp matches the WHOLE trimmed return
// against an ANCHORED EXACT grammar `^DISPATCHED <seg> ([0-9A-Fa-f][0-9A-Fa-f-]*)$`
// where <seg> is this known-safe seg literal (regex-escaped) and the capture
// is restricted to the DISP generator alphabet (uuidgen hex+hyphens, or the
// $RANDOM digit fallback). On ANY mismatch -- extra text, wrong seg, a char
// outside [0-9A-Fa-f-], multi-line, or a non-string return -- disp is "" (so
// no unsafe char can reach the wait bash; an empty DISP merely disables
// fail-fast -- safe degradation). "$" here has no /m flag, so it matches only
// end-of-input, NOT before a trailing newline -- a multi-line return cannot
// sneak past the anchor.
function escapeRegExp(s) {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}
function parseDisp(raw, seg) {
  if (typeof raw !== "string") return "";
  const re = new RegExp("^DISPATCHED " + escapeRegExp(seg) + " ([0-9A-Fa-f][0-9A-Fa-f-]*)$");
  const m = re.exec(raw.trim());
  return m ? m[1] : "";
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

// CONTAINMENT GUARD -- the fail-priority backstop for the two WAIT call sites
// in this file. True iff the raw reply contains failSentinel ANYWHERE, at any
// offset, on any line, adjacent to anything.
//
// The FUNCTION BODY below is byte-identical to glossary-pass-wf.template.js's
// copy, deliberately: two divergent copies of a security guard are worse than
// one, for the same reason sentinelVerdict() is mirrored. Do not reformat it.
// This COMMENT is necessarily this file's own -- the glossary copy's comment
// describes glossary's call sites, counts and recovery paths, none of which
// are true here.
//
// EVERY COUNT BELOW NAMES ITS SHAPE AND ITS SET, and so must any count added
// later. A bare "15 of 16" is not checkable: the numbers move with the reply
// shape AND with which glue table was counted, and this codebase deliberately
// uses two different tables. Concretely, a reader comparing this file's counts
// against the suite that pins them will otherwise see a contradiction:
//   GLUE_CHARS (16 items) in tests/glossary_citation_review.test.py -- the set
//     every count in THIS comment and at this file's call sites is measured
//     over. Adds U+001D, U+001E, U+001F.
//   ALL_GLUES (15 items) in tests/mass_translate_sentinel_containment.test.py
//     -- a different set, partitioned by trim-strippability, which the
//     mass-translate suite measures and which reports "14 of 15" for this same
//     property. Adds a HYPHEN and a QUOTE; omits U+001D/U+001E/U+001F.
// 13 characters are common to both. Both numbers are correct over their own
// population; neither is a bug, and the two sets are NOT to be unified --
// A2's partition carries information a flat list does not.
//
// Why this exists. sentinelVerdict() splits on "\n" and compares whole trimmed
// lines, so its fail-priority scan only sees a fail sentinel that an LF put on
// a line of its own. Measured over GLUE_CHARS (16 items,
// tests/glossary_citation_review.test.py), shape: the fail sentinel SHARING ITS
// LINE with prose -- a reply of prose + GLUE + "TIMEOUT <seg>" + "\n" +
// "READY <seg>" -- 15 of 16 glue characters defeat that scan at BOTH wait
// sites, so 30 of 32 site/character pairs, and the segment FALSELY PROCEEDS as
// ready. LF alone blocks. The 15
// are not exotic: PLAIN SPACE, TAB, a lone CR, VT, FF, U+001C, U+001D, U+001E,
// U+001F, NBSP, U+0085, U+2028, U+2029, ZWSP -- and the ordinary letter "x".
// This is not a line-separator problem: `split("\n")` breaks on LF and nothing
// else, so ANY character between prose and the sentinel keeps them on one line
// and defeats whole-line equality. That alphabet is unbounded, so widening the
// split is whack-a-mole; containment is closed under all of it at once because
// it never asks where the sentinel sits.
//
// Why this file matters at least as much as glossary's. The translate-wait
// site's own comment calls it "the worst of the five sites (#228)": a false
// pass there sends the ENTIRE review/fix cycle over a draft that never
// finished translating, and nothing on that path is recorded as recoverable,
// so the "we'll pick it back up next run" net never fires.
//
// THE COST, which differs per site and must not be flattened:
//   translate wait -- a false RED returns reason:"translate-timeout" WITHOUT a
//     terminal ledger write. translateStage's in_progress fragment stays the
//     durable record and select_segments.py's "non-terminal -> recoverable"
//     rule auto-redispatches the segment next run. Bounded and automatic.
//   review wait -- a false RED returns status:"blocked", reason:"review-timeout"
//     for that segment. Also the fail-safe direction, but it ends this run's
//     work on the segment rather than retrying inside it.
// Either false RED costs one segment's rework; a false GREEN corrupts the draft
// the rest of the pipeline treats as finished. The guard buys that asymmetry
// deliberately; it is not free.
//
// An empty or non-string failSentinel returns false rather than matching
// everything: "".indexOf("") is 0, so an unguarded containment test would
// reject every reply unconditionally.
//
// This guard is NOT used at runRound's fix site. That call has no fail
// sentinel at all -- "DRAFT_MISSING <seg>" is its OK sentinel -- so a
// fail-sentinel guard would be a no-op there by construction. Its gap runs the
// opposite way and is closed by mentionedAnywhere() below instead.
function rejectedAnywhere(reply, failSentinel) {
  if (typeof failSentinel !== "string" || failSentinel.length === 0) return false
  return String(reply == null ? "" : reply).indexOf(failSentinel) !== -1
}

// Containment in the OK-SENTINEL direction -- the counterpart to
// rejectedAnywhere() above, and deliberately NOT that same function. That one
// takes a FAIL sentinel, so a hit biases toward REJECTING. This one takes a
// sentinel whose presence a caller is trying not to MISS, so a hit biases
// toward acting on it. Identical containment test, opposite consequence, which
// is why it carries its own name instead of reusing one that would be false at
// the call site.
//
// "mentioned" rather than "reported" on purpose: this test cannot tell a
// genuine report from a passing textual mention, and its one caller has
// accepted that collision knowingly (see runRound below). A name promising
// "reported" would be false in exactly the case that matters.
//
// Delegates to rejectedAnywhere() so the containment semantics -- including
// the empty/non-string sentinel guard that stops "".indexOf("") === 0 from
// matching every reply -- live in one place. That function's body is pinned
// byte-identical across the workflow templates; this wrapper does not move it.
function mentionedAnywhere(reply, sentinel) {
  return rejectedAnywhere(reply, sentinel)
}

// #348 -- THE SINGLE PARSE SITE for both chunked wait loops. One copy, not two,
// for the same reason matchedVerdict() is one copy: two divergent readings of a
// false-green boundary are worse than one.
//
// Grammar: a chunk agent returns exactly one of
//   READY <seg>   -- the canonical ACCEPT gate exited 0
//   FAILED <seg>  -- the driver's DISP-named fail sentinel appeared
//   PENDING <seg> -- the chunk spent its own elapsed bound, or was cut short
//
// PENDING, not NOTREADY: it keeps the option open to guard the READY direction
// with containment later, and it removes a reader-facing trap where two of the
// three sentinels differ only by a prefix.
//
// ORDER IS LOAD-BEARING. Both containment guards run BEFORE the exact-line
// READY test, so every #228/#308 property is preserved unchanged: a fail
// sentinel glued behind ANY character still rejects (rejectedAnywhere is raw
// indexOf and never asks where the sentinel sits), while READY stays whole-line
// equality via sentinelVerdict, so a sentinel-shaped line the agent's own LATER
// prose overrides is still not a success. That qualifier is exact rather than a
// hedge (#371): sentinelVerdict reads the LAST trimmed non-empty line, so a
// disavowal that PRECEDES a bare sentinel on the final line is the prose
// preamble #308 tolerates by design, and it passes unless one of the
// containment guards above catches it first.
//
// The fail-safe direction is the default: an unparseable reply, a null return,
// or a tool error is PENDING -- never READY. At worst that costs one more chunk
// of waiting, bounded by WAIT_CHUNKS, and the post-exhaustion re-check still
// runs afterwards.
//
// KNOWN COLLISION, inherited and recorded rather than closed. SEG_ID_RE permits
// one id to prefix another (seg1 / seg10) and these two guards are raw
// containment, so "FAILED seg10" matches seg1's FAILED guard. This is
// FALSE-RED ONLY -- READY is whole-line equality, so it can never manufacture a
// false green -- and the same exposure already exists for TIMEOUT today. Its
// new cost is that seg1 could abandon its remaining chunk budget early; the
// authoritative re-check below still runs, so a genuinely-landed artifact is
// still found. Unreachable in practice: a wait agent's prompt names only its
// own seg. tests/wait_chunking.test.py pins the behaviour so it stays recorded.
function waitChunkVerdict(reply, seg) {
  if (rejectedAnywhere(reply, "FAILED " + seg)) return "failed";
  // rejectedAnywhere() for PENDING too, and deliberately so, though PENDING is
  // not strictly a FAILURE sentinel: the 1.16.1 review proposed mentionedAnywhere()
  // here on naming grounds -- the two share one body, so it is behaviour-identical --
  // and it was NOT taken. tests/bounded_poll_present.test.py's
  // test_wait_chunk_verdict_runs_both_guards_before_the_whole_line_ready_test
  // pins BOTH guards by helper NAME and additionally checks they read the same
  // reply variable and sit positionally before the whole-line READY test. Buying
  // a naming nicety by widening that regex would trade a real structural guard on
  // a false-green boundary for cosmetics. What PENDING means here is "a hit biases
  // AWAY from READY", which is the same direction rejectedAnywhere() is named for.
  if (rejectedAnywhere(reply, "PENDING " + seg)) return "pending";
  if (sentinelVerdict(reply, "READY " + seg, null)) return "ready";
  return "pending";
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
  lines.push("Effort: " + EFFORT + ". Literary translation of segment " + seg + " (" + SOURCE_LANG + " to " + TARGET_LANG + ").");
  lines.push("Read in order: " + ROOT + "/translate_TASK.md ; " + ROOT + "/style_bible.md (in full, especially the word-sense/realia traps section) ; " + ROOT + "/segments/segpack_" + seg + ".json (source text plus the frozen name/realia canon for this segment).");
  lines.push("Verse policy for this project: " + VERSE_POLICY_INSTRUCTION_BLOCK);
  lines.push("Translate every block, footnote, and verse this segpack contains. Copy every placeholder sentinel (e.g. ⟦FNREF_N⟧, ⟦VERSE_...⟧) byte for byte, in its correct position in the sentence -- never translate, drop, or reword a sentinel itself. Any embedded third-language text (Latin, an older form of the source language, or similar) gets an in-text gloss, never a notes-only translation. The segpack's canon_map gives each already-canonized name's frozen canonical target form (source form -> target form): render each such name using that target form's stem/spelling, declined as the target grammar requires -- a correctly inflected form of the canonical stem is correct, never a verbatim copy of the citation form where grammar needs another case. For a new_names entry not yet in canon_map, choose a reasoned rendering and flag it in notes as NEW:. The segpack's split_names lists any source form adjudicated as a HOMONYM SPLIT -- one spelling denoting two or more distinct referents, each sense given with a disambiguator. A split form has no frozen target form and never will (that is what a homonym cannot have), so it appears in neither canon_map nor new_names: decide per occurrence which sense the passage carries, render accordingly, and flag your rendering in notes as NEW: naming the sense you chose.");
  lines.push("Write your draft exactly per translate_TASK.md's own schema to the SINGLE output path ⟦JOB_OUT⟧ (an isolated attempt path this run supplies) -- and add one extra top-level field beyond that schema: dispatch_token, with exactly this literal string value: " + JSON.stringify(dispatchToken) + ". This is run-scoping freshness metadata, not part of the translation itself.");
  lines.push("That output path SUPERSEDES " + ROOT + "/translate_TASK.md for the write destination: write your draft ONLY to that path, even if translate_TASK.md tells you to write " + ROOT + "/segments/" + seg + ".draft.json or another segments/ path -- never write the canonical " + ROOT + "/segments/" + seg + ".draft.json yourself, and create no other file under " + ROOT + "/segments/. That single output path is the only segments-area file you may write; a downstream deterministic gate validates it before it is promoted to the canonical draft, so you do not run any coverage check yourself.");
  lines.push("Return exactly the line: DONE " + seg);
  return lines.join("\n");
}

// #198 -- the plain-Claude DISPATCHER for the translate job (no agentType,
// effort low). It NEVER translates: it generates a per-dispatch DISP nonce,
// writes translatePrompt(seg)'s codex task text (carrying its ONE ⟦JOB_OUT⟧
// placeholder) to a fresh task-file, launches codex_job.py DETACHED (nohup,
// </dev/null >/dev/null 2>&1 & -- NO setsid, NO external `timeout` binary) so
// the driver OUTLIVES this agent's turn, and returns exactly
// `DISPATCHED <seg> <DISP>` (it does NOT poll the driver -- the Workflow wait
// poll is the durable watcher). The codex task text is embedded via a QUOTED
// heredoc (<<'LT_CODEX_TASK_EOF') so no $/`/quote inside it is expanded;
// COMPANION is single-quoted (resolver-guaranteed quote/control/newline-free);
// DISP is validated in JS on return (parseDisp) before it ever reaches the
// wait bash. The codex_job.py CLI is exactly the frozen CONTRACT surface (the
// bash is kept inline here, deliberately symmetric with reviewDrivePrompt, so
// the per-function regression greps in bounded_poll_present.test.py see it).
function translateDrivePrompt(seg) {
  const taskFile = ROOT + "/segments/.codex_task.translate." + seg;
  const expectToken = RUN_ID + ":" + seg;
  const codexTask = translatePrompt(seg);
  const cmd =
    "DISP=$(uuidgen 2>/dev/null || echo $RANDOM$RANDOM$RANDOM); " +
    "TASKFILE=\"" + taskFile + ".$DISP\"; " +
    "cat > \"$TASKFILE\" <<'LT_CODEX_TASK_EOF'\n" +
    codexTask + "\n" +
    "LT_CODEX_TASK_EOF\n" +
    "nohup " + PY + " " + ROOT + "/scripts/codex_job.py --kind translate --companion '" + COMPANION + "' --cwd " + ROOT + " --seg " + seg + " --prompt-file \"$TASKFILE\" --expect-token " + expectToken + " --run-id " + RUN_ID + " --disp \"$DISP\" --deadline-sec " + CODEX_DEADLINE_SEC + " --effort " + EFFORT + MODEL_ARG + PLUGIN_ROOT_ARG + " </dev/null >/dev/null 2>&1 &\n" +
    "echo \"DISPATCHED " + seg + " $DISP\"";
  const lines = [];
  lines.push("Effort: low. You are DISPATCHING a background codex translation job for segment " + seg + " -- you do NOT translate anything yourself, and you do NOT wait for the job to finish.");
  lines.push("Run EXACTLY ONE bash command -- this entire block, verbatim:");
  lines.push(cmd);
  lines.push("Then return EXACTLY the single line that command echoed: the word DISPATCHED, then " + seg + ", then the generated DISP value. Do not poll the job, do not read any file, and add no other text.");
  return lines.join("\n");
}

// The codex REVIEW task text (#97 #88 #87-artifact restructure;
// reviewDispatchPrompt/reviewWaitPrompt/readReviewPrompt/callArtifactCheck
// replace the old, single schema-validated reviewPrompt/callReview). #198:
// this is no longer dispatched fire-and-forget from a Workflow agent turn --
// reviewDrivePrompt writes this text verbatim into the codex_job.py driver's
// task-file, the codex reviewer writes its verdict to the isolated ⟦JOB_OUT⟧
// attempt path, and the driver validate-before-promotes it to the canonical
// segments/{seg}.review.json under a per-seg flock. codex writes disk, its
// own return line is NEVER the verdict -- only reviewWaitPrompt's bounded
// poll of the promoted canonical below confirms readiness.
//
// Self-contained: this prompt carries the FULL review.schema.json field
// contract inline and explicitly supersedes review_TASK.md for that
// contract -- a resumed project's review_TASK.md may predate this change
// and must never be trusted over the fields spelled out here.
function reviewDispatchPrompt(seg, roundLabel) {
  const dispatchToken = RUN_ID + ":" + seg + ":r" + roundLabel;
  const draftToken = RUN_ID + ":" + seg;
  const lines = [];
  lines.push("Effort: " + EFFORT + ". Single reviewer covering both accuracy and literary quality for segment " + seg + " (" + SOURCE_LANG + " to " + TARGET_LANG + "), round " + roundLabel + ".");
  lines.push("This prompt is self-contained and supersedes " + ROOT + "/review_TASK.md for the field contract below. Read review_TASK.md for narrative guidance only -- it may predate this instruction, and its own field list must never override the fields spelled out here.");
  lines.push("First run the deterministic gate: " + PY + " " + ROOT + "/scripts/validate_draft.py " + seg + " -- remember whether it printed OK or FAIL, and any defects it named.");
  lines.push("Before reading the draft, compute its current sha1 by running: " + PY + " " + ROOT + "/scripts/draft_sha1.py " + seg + " -- this becomes your draft_sha1 value below, and it must be computed BEFORE you read the draft file itself.");
  lines.push("Then read: " + ROOT + "/review_TASK.md ; " + ROOT + "/style_bible.md ; " + ROOT + "/segments/segpack_" + seg + ".json ; " + ROOT + "/segments/" + seg + ".draft.json.");
  lines.push("As soon as you read the draft, check its own dispatch_token field: it must equal exactly this literal string: " + JSON.stringify(draftToken) + ". If it does not match exactly, STOP here -- this draft belongs to a different, stale run. Do not review it, write no review output at all, and return exactly the line: DRAFT_TOKEN_MISMATCH " + seg + " instead of the REVIEWED line below.");
  lines.push("Verse policy for this project: " + VERSE_POLICY_INSTRUCTION_BLOCK);
  // #546 -- DELIVERY vs STORAGE. The reviewer is handed the draft's fields and
  // never the page the assembler builds from them, so "the reader is left with
  // nothing" is a claim it cannot check and cannot be argued out of: each round's
  // fix moves the meaning somewhere the next round also cannot see, and the loop
  // alternates between two objections that cannot both be satisfied in one line.
  // (The measured incident behind it is in the 1.35.0 CHANGELOG entry, which is
  // where a number belongs -- it goes stale, and it is not actionable here.)
  // Scoped to THIS function on purpose -- the same text in translatePrompt/fixPrompt would read
  // as permission to skimp on `rendered` because the gloss carries the meaning,
  // which is the opposite defect. The positive half is scoped to the shipped
  // Obsidian renderer (render_obsidian.py's _render_verse_block/_render_verse_inline);
  // a `custom` adapter renders per project and is deliberately NOT claimed here.
  lines.push("Delivery vs storage: `rendered` and `literal_gloss` are two fields of ONE verse entry, and this plugin's shipped Obsidian output always prints a verse's literal gloss with the verse it belongs to -- as the verse body itself when it is the only rendering, and beneath the verse block (or inline beside an embedded verse) when it accompanies `rendered`. You are given the draft, never the assembled output, so a finding may not assert -- from the draft alone -- that the reader is left without a meaning for a verse whose own `literal_gloss` supplies it. A verse whose `literal_gloss` does NOT supply that meaning is unaffected: report it normally.");
  lines.push("Check the draft against the source for: full accuracy (no omissions or distortions), word-sense and realia fidelity for the source era and context -- ask explicitly whether each notable word means what it meant in that period and context, not what it means today -- name/canon fidelity, placeholder sentinel fidelity, verse per the policy above, and literary quality (register, idiom, natural seams, rhythm).");
  lines.push("Canon-name fidelity specifically: the segpack's canon_map gives each already-canonized name's frozen canonical target form. Flag a canon name ONLY if the draft renders a different name, a different transliteration of the canonical stem, leaves a canonical name untranslated, or swaps an epithet for a real surname -- a correctly inflected/declined form of the canonical stem is CORRECT and must NOT be flagged.");
  lines.push("A canon_map target form is authoritative as given. Never flag a canon name merely because its frozen canonical target form is lexically unrelated to the SOURCE form -- for a sense-translated speaking name (basis:\"sense_translated\") that is expected and correct. The deviation triggers above still apply. Correctness of the frozen canon decision itself is out of scope for this review -- a suspected error is reopened via the glossary/adjudication route, never flagged here.");
  // #529 -- AUTHORITY DIRECTION. The two lines above tell the reviewer that a
  // canon_map target form is authoritative; nothing told it that the draft's OWN
  // names[] entries and NEW: notes are not. Both artifacts sit in its context with
  // nothing separating them by status, so it can enforce the draft's own unratified
  // proposal against that same draft and file a canon finding for a form that exists
  // nowhere -- measured twice, once applied and once used to REVERT a correct change.
  // Scoped to THIS function by ROLE, not by symmetry. 1.37.0 (#532) gave the FIX
  // turn its own apply-side rule -- a canon claim whose form resolves in neither
  // the segment's canon_map nor canon.json is refused -- so this text is the
  // RAISE-side half of the same property, in the vocabulary of the only turn that
  // files a finding. The fixer never raises one, and repeating a raise-side rule
  // in its prompt would read as licence to skip a finding rather than substantiate
  // it. The two halves are deliberately independent: a finding refused at the fix
  // turn still costs a round, and its verdict still stands in review.json, so the
  // unit does not converge until the round advances, an operator rejection lands
  // (#461/#527) or the cap fires. It does NOT reach the next REVIEWER -- this
  // function's read list is review_TASK.md, style_bible.md, the segpack and the
  // draft, and nothing puts a prior review in front of it (corrected in 1.63.0,
  // #526; the 1.40.0 CHANGELOG entry still carries the superseded sentence as the
  // record of what that release claimed).
  // The prohibition binds only a finding that PRESCRIBES a target form. A blanket
  // "no canon_map entry means do not raise" would suppress findings that are
  // authorized today and grounded in the source, not in a canon: segpack.py admits a
  // canonized name to canon_names while deliberately omitting it from canon_map when
  // its canonical_target_form is empty, and the strong-name detector can drop a
  // canonized name from the segpack altogether.
  lines.push("Authority direction. The segpack's canon_map is the only frozen canon you are given -- canon.json is not in your read list, and canon_names may name a person whose canon_map entry is deliberately absent. The draft's own names[] entries and any note prefixed NEW: were written by the translator in the same turn as the prose you are reviewing: they are unratified proposals, never a standard, and the artifact under review is never the authority it is reviewed against. Cite them as context if it helps, never as the rule a rendering violates. So a finding that prescribes a particular canonical target form -- demanding the prose be changed to it, restored to it, or reverted to it -- must quote, in its own issue text, the canon_map entry (source form -> target form) it rests on; if that form has no canon_map entry, there is no frozen canon at that name and you may not assert one. Findings grounded in the source rather than in a canonical target form are untouched -- an omitted name, a canonical name left untranslated, a name rendered as a different person are all reported exactly as before. One case is neither canon nor an unratified proposal: the segpack's split_names lists source forms adjudicated as HOMONYM SPLITS, each sense carrying a disambiguator. A split form is absent from canon_map and from canon.json BY DESIGN, so the rule above still binds -- you may not prescribe a canonical target form at a split name, and you must not report it as an uncanonized or new name either, because its adjudication already happened. What you may report, quoting the disambiguator it rests on, is the draft carrying the WRONG SENSE for this passage; that is a source-grounded finding like any other.");
  // #526 -- BOOK-SCOPED RULES. The style contract carries rules whose predicate
  // spans the whole book (gloss at its FIRST occurrence only; identify on FIRST
  // mention; the Common Era equivalent at its FIRST mention; the original-script
  // parenthetical on FIRST mention -- style_bible.template.md ships the last of
  // those as a REQUIRED FILL, so a project authors one by construction). This
  // reviewer holds ONE segment, so it cannot see where a term first occurs and
  // re-derives the same obligation at every later occurrence: measured on a live
  // book, 5 distinct false findings over 2 rounds, all demanding one gloss whose
  // first occurrence was already glossed six segments earlier -- a count that
  // grows with book length rather than being a one-off.
  //
  // 1.37.0 (#532) already gave the FIX turn the apply-side half, and says so in
  // its own words: a book-scoped rule turns on something this segment does not
  // contain, so settle it against the earlier segments' drafts or refuse -- "this
  // is the class the reviewer cannot evaluate at all, having seen one segment".
  // The plugin has been telling the fixer that while never telling the reviewer.
  // This is the RAISE-side half, scoped to THIS function by ROLE exactly as #529
  // is: the same text in translatePrompt would read as licence to skip
  // first-mention treatment altogether (the mirror defect), and in fixPrompt it
  // would contradict that branch, which sends the fixer to GO AND SETTLE the fact
  // rather than to leave it alone.
  //
  // The in-segment carve-back is one-directional, and that asymmetry is the whole
  // of it: two occurrences of a term in this segment settle that the SECOND is not
  // the book's first -- that fact needs no segment the reviewer cannot see -- while
  // saying anything about the FIRST of the two still needs the whole book. So the
  // remove direction is restored there and the add direction is not.
  //
  // The second disjunct is anchored IN THAT FILE, and that is not padding. The
  // reviewer's read list also holds the draft, whose notes[] are written by the
  // translator in the same turn as the prose under review and are routinely
  // phrased as first-mention records; an unanchored "a written note" would let one
  // of those count as the evidence, which is exactly the authority #529 spent a
  // release taking away ("the artifact under review is never the authority it is
  // reviewed against"). The segpack's source text can carry editorial-note-shaped
  // prose for the same reason.
  //
  // The carve-back binds in BOTH directions on purpose. A recorded first
  // occurrence does not only license the addition finding at the recorded place --
  // it equally proves that every OTHER occurrence is not the first, so the
  // redundant-repeat finding is evaluable there. Binding it to "the recorded place
  // is in this segment" would suppress the removal half at every later segment
  // even where the contract has already settled it, and a clean verdict would then
  // converge over a contract violation.
  lines.push("Book-scoped rules. style_bible.md carries rules whose predicate spans the WHOLE book -- gloss a realia at its FIRST occurrence only, identify a person on FIRST mention, give a source-calendar year its Common Era equivalent at its FIRST mention, render an original-script name in parentheses on FIRST mention. You hold ONE segment, and a term's first occurrence in the book is normally in a segment you will never see, so a finding may not rest on the assertion that an occurrence in this segment is, or is not, the book's first. Concretely: do not demand that a first-mention treatment -- a gloss, a parenthetical original, an identification, an era equivalent -- be ADDED here, and do not demand that a treatment already present be REMOVED here as a redundant repeat. Both directions are unevaluable from your inputs, and the remove direction deletes correct text rather than merely costing a round. The one place the evidence IS in your hands: where style_bible.md itself records where a term first occurs -- its motif table's first-occurrence column, or a written note IN THAT FILE naming the block that holds the first mention -- that record settles the question in BOTH directions, and you report normally: a missing first-mention treatment at the recorded place when that place is in this segment, and a redundant repeat at any occurrence the record puts elsewhere. A second place the evidence is in your hands is THIS SEGMENT ITSELF: where an occurrence here is preceded by another occurrence of the same term in this same segment, the later one is provably not the book's first whatever lies in the segments you cannot see, so a redundant repeat there is reported normally. That reasoning runs in the REMOVE direction only -- an earlier occurrence here proves a later one is not the first, and proves nothing about whether that earlier one is. Everything else about such a rule stays in scope: where the treatment IS present, whether it is correctly FORMED -- the right script, the transliteration system style_bible.md names, correct era arithmetic -- is fully evaluable here, and a finding grounded in the source rather than in a whole-book predicate is untouched.");
  // #539 -- the loc VOCABULARY, stated here because this prompt is the site
  // that binds: :1008 above declares it self-contained and says
  // review_TASK.md's own field list must never override it. Before #539 this
  // function named only VERSE:{vid}, and the draft's own notes[] had no
  // conforming spelling anywhere, so a reviewer with a true finding about a
  // note invented one ("notes[14]", "NOTES") -- colonless, refused by
  // findingsAuthentic() above, and .every() then discarded that review's
  // valid block findings with it. The 0-based/NUMBER contrast is spelled out
  // rather than left to inference: NOTE:n and FN:n look alike, nothing
  // downstream resolves an index, and a one-based reading aims the fix turn
  // at the wrong note.
  lines.push("Finding loc contract: every finding's loc must be COLON-DELIMITED. A bare, holistic token (\"overall\", \"NOTES\", \"TASK\") is refused outright and discards this entire review, valid findings included -- so never emit one. The forms are: a block id (e.g. PARA:seg01:0001, or the shorter HEAD:seg01 some adapters emit); FN:n for a footnote; VERSE:vid for a verse; NOTE:n for one entry of this draft's own notes[] array. NOTE:n is a 0-based INDEX into notes[] -- the first note is NOTE:0 -- whereas FN:n is the footnote's own NUMBER, not an index.");
  lines.push("Build a JSON object with exactly these five fields: clean (true only if there are no findings that require a fix round), coverage_ok (true only if the deterministic gate above printed OK), findings (an array of objects with loc/severity/issue/suggest -- every loc per the loc contract stated just above), draft_sha1 (the value you computed before reading the draft, above), and dispatch_token (exactly this literal string: " + JSON.stringify(dispatchToken) + ").");
  lines.push("Write that exact object as JSON to the SINGLE output path ⟦JOB_OUT⟧ (an isolated attempt path this run supplies) and nothing else. That output path SUPERSEDES " + ROOT + "/review_TASK.md for the write destination: write your verdict ONLY to that path, even if review_TASK.md names " + ROOT + "/segments/" + seg + ".review.json or another segments/ path -- never write the canonical " + ROOT + "/segments/" + seg + ".review.json yourself, and create no other file under " + ROOT + "/segments/. That single output path is the only segments-area file you may write; the driver validates it and atomically promotes it to the canonical review artifact.");
  lines.push("Return exactly the line: REVIEWED " + seg);
  return lines.join("\n");
}

// #198 -- the plain-Claude DISPATCHER for the review job. Symmetric to
// translateDrivePrompt (no agentType, effort low; --kind review;
// --expect-token RUN_ID:seg:r<label>; task-file .codex_task.review.<seg>.
// <DISP>); writes reviewDispatchPrompt(seg, roundLabel)'s codex task text
// (its ONE ⟦JOB_OUT⟧ placeholder intact) and returns `DISPATCHED <seg>
// <DISP>`. The bash is kept inline (symmetric with translateDrivePrompt) so
// the per-function regression greps in bounded_poll_present.test.py see it.
function reviewDrivePrompt(seg, roundLabel) {
  const taskFile = ROOT + "/segments/.codex_task.review." + seg;
  const expectToken = RUN_ID + ":" + seg + ":r" + roundLabel;
  const codexTask = reviewDispatchPrompt(seg, roundLabel);
  const cmd =
    "DISP=$(uuidgen 2>/dev/null || echo $RANDOM$RANDOM$RANDOM); " +
    "TASKFILE=\"" + taskFile + ".$DISP\"; " +
    "cat > \"$TASKFILE\" <<'LT_CODEX_TASK_EOF'\n" +
    codexTask + "\n" +
    "LT_CODEX_TASK_EOF\n" +
    "nohup " + PY + " " + ROOT + "/scripts/codex_job.py --kind review --companion '" + COMPANION + "' --cwd " + ROOT + " --seg " + seg + " --prompt-file \"$TASKFILE\" --expect-token " + expectToken + " --run-id " + RUN_ID + " --disp \"$DISP\" --deadline-sec " + CODEX_DEADLINE_SEC + " --effort " + EFFORT + MODEL_ARG + PLUGIN_ROOT_ARG + " </dev/null >/dev/null 2>&1 &\n" +
    "echo \"DISPATCHED " + seg + " $DISP\"";
  const lines = [];
  lines.push("Effort: low. You are DISPATCHING a background codex review job for segment " + seg + " (round " + roundLabel + ") -- you do NOT review anything yourself, and you do NOT wait for the job to finish.");
  lines.push("Run EXACTLY ONE bash command -- this entire block, verbatim:");
  lines.push(cmd);
  lines.push("Then return EXACTLY the single line that command echoed: the word DISPATCHED, then " + seg + ", then the generated DISP value. Do not poll the job, do not read any file, and add no other text.");
  return lines.join("\n");
}

// ---------------------------------------------------------------------------
// #348 -- the two wait sites' ACCEPT gates, COMPOSED ONCE AND SHARED.
//
// Each returns the exact canonical-validation command for its kind. The chunk
// poll and the post-exhaustion re-check both splice the SAME string, so the
// re-check can never drift into a weaker gate than the poll it backs up --
// that drift would be a false GREEN (accepting an artifact the poll would have
// rejected), the one direction this pipeline cannot recover from. Same idiom
// and same reason as glossary's shared --check-batch command.
//
// translate ACCEPT = draft_ready.py --expect-token (token + delivery, so an
// old-run straggler's stale-token draft is never accepted) AND
// validate_draft.py (the six quality checks, so a structurally-complete but
// content-defective draft is REJECTED). review ACCEPT = review_ready.py
// --expect-token (full schema + draft_sha1 freshness + this round's
// dispatch_token). No external `timeout` binary in either.
// ---------------------------------------------------------------------------
function translateAcceptCmd(seg) {
  const dispatchToken = RUN_ID + ":" + seg;
  return PY + " " + ROOT + "/scripts/draft_ready.py " + seg + " --expect-token " + dispatchToken +
    " && " + PY + " " + ROOT + "/scripts/validate_draft.py " + seg;
}
function reviewAcceptCmd(seg, roundLabel) {
  const dispatchToken = RUN_ID + ":" + seg + ":r" + roundLabel;
  return PY + " " + ROOT + "/scripts/review_ready.py " + seg + " --expect-token " + dispatchToken;
}

// #348 -- ONE chunk of a chunked wait. Shared by both wait sites so the two
// deliberately-parallel polls cannot drift apart in shape; only the ACCEPT
// gate, the fail-fast sentinel's seg and the prose differ.
//
// The bash keeps #198's proven grammar exactly: ACCEPT gate first (a valid
// canonical always wins over any sentinel), optional fail-fast evaluated ONLY
// AFTER the gate did not pass this iteration, gate -> deadline-break ->
// clamped sleep, and NO separate post-loop gate inside the command, so exactly
// one gate straddles this chunk's deadline. What is new is the elapsed bound
// (this chunk's slice, not the whole run's) and the terminal markers.
//
// `>/dev/null 2>&1` ON THE IN-LOOP ACCEPT GATE IS LOAD-BEARING, not tidiness.
// Without it the gate prints one `{"ready": false, ...}` line per iteration
// (~30 measured in the #348 transcript), so "the marker is the last line"
// would be a claim about the tail of a noisy stream. Suppressed, the chunk
// emits exactly zero or one line and that line is the marker. The gate's EXIT
// STATUS -- the only thing this workflow acts on -- is unaffected by the
// redirect.
//
// Marker-plus-`exit 1` rather than distinct exit codes, deliberately: it keeps
// #198's `&& exit 0` / `exit 1` grammar intact, and -- the point -- a
// TOOL-KILLED chunk (exit 143, no marker printed) becomes indistinguishable
// from a chunk that merely ran out of budget. That is exactly the safe
// reading: not ready yet, keep polling.
function waitChunkPrompt(seg, acceptCmd, disp, chunkIndex, whatPhrase, dontClause) {
  const failFast = disp
    ? " [ -f \"" + ROOT + "/segments/.codex_failed." + seg + "." + disp + "\" ] && { echo LT_FAIL_SENTINEL; exit 1; };"
    : "";
  const lines = [];
  lines.push("The codex " + whatPhrase + " is running in a DETACHED background job (launched by codex_job.py). This is wait chunk " + chunkIndex + " of " + WAIT_CHUNKS + " -- one bounded slice of this segment's total " + WAIT_BOUND_SEC + "s wait, sized so a single bash call never approaches the " + BASH_CALL_CAP_SEC + "s per-call cap.");
  lines.push("Run EXACTLY ONE bash command, passing a bash tool timeout of " + WAIT_CHUNK_TOOL_TIMEOUT_MS + " ms -- an elapsed-time poll that re-validates the canonical artifact directly:");
  lines.push("end=$((SECONDS + " + waitChunkSec(chunkIndex) + ")); while true; do " + acceptCmd + " >/dev/null 2>&1 && exit 0;" + failFast + " [ $SECONDS -ge $end ] && break; slp=$((end-SECONDS)); [ $slp -gt 20 ] && slp=20; [ $slp -gt 0 ] && sleep $slp; done; echo LT_CHUNK_BOUND; exit 1");
  lines.push("If that command exits 0 (the canonical artifact validated), return exactly the line: READY " + seg);
  lines.push("If it printed LT_FAIL_SENTINEL, return exactly the line: FAILED " + seg);
  lines.push("In every other case -- it printed LT_CHUNK_BOUND, or the call was cut short for any reason at all -- return exactly the line: PENDING " + seg);
  lines.push("Do nothing else -- do not touch any files, and " + dontClause + ".");
  return lines.join("\n");
}

// #348 -- THE FIX. After the chunk budget is spent (or a chunk reported the
// driver's fail sentinel), re-check the canonical artifact ONCE, without
// polling, before declaring a timeout.
//
// This is the defect #348 actually reports. Chunking alone would have turned
// the one observed 610 s failure into a success by accident, while leaving the
// real hole open: a job that finishes after the last poll ended has a complete,
// valid artifact on disk that nothing ever reads. The frozen reproduction is a
// clean segments/<seg>.review.json sitting beside a ledger saying in_progress.
//
// Running it on the FAILED path too is deliberate, not sloppy: the fail
// sentinel means the DRIVER did not promote, and this file's own rule is that
// a valid canonical always wins over any sentinel. The canonical gate is the
// authority on whether one landed anyway.
//
// Non-polling by construction -- no `end=`, no loop, no sleep. A polling
// re-check would just be one more chunk and could itself hit the cap.
function waitRecheckPromptFor(seg, acceptCmd, whatPhrase, dontClause) {
  const lines = [];
  lines.push("The " + WAIT_BOUND_SEC + "s wait budget for the codex " + whatPhrase + " is spent, or its job reported failure. Before this is declared a timeout, re-check the canonical artifact ONCE -- it may have landed after the last wait chunk's poll ended.");
  lines.push("Run EXACTLY ONE bash command. It does NOT poll and returns immediately:");
  lines.push(acceptCmd + " >/dev/null 2>&1");
  lines.push("If that command exits 0 (the canonical artifact validated), return exactly the line: READY " + seg);
  lines.push("Otherwise return exactly the line: PENDING " + seg);
  lines.push("Do nothing else -- do not touch any files, and " + dontClause + ".");
  return lines.join("\n");
}

// #198 -- the Workflow's AUTHORITATIVE independent wait gate for the review
// dispatch above. The codex reviewer runs in a DETACHED codex_job.py job;
// this poll re-validates the CANONICAL review artifact directly (never
// trusts any driver-written file). ACCEPT = review_ready.py <seg>
// --expect-token <tok> exit 0 (full schema + draft_sha1 freshness + this
// round's dispatch_token).
//
// Since 1.16.1 (#348) this builds ONE CHUNK of the chunked wait, not the whole
// poll: chunkIndex selects the slice, and the bash grammar, the chunk bound and
// the fail-fast semantics all live in waitChunkPrompt() -- see its comment
// rather than duplicating them here, which is how this header came to describe
// a single full-bound poll that no longer exists. The chunk loop and the ONE
// non-polling authoritative re-check that follows an exhausted or failed poll
// belong to the CALL SITE (getVerifiedReview/reviewFixLoop); an exhausted chunk
// is not a timeout on its own. What is genuinely this wrapper's own is the
// ACCEPT gate named above; roundLabel derives the token internally
// (RUN_ID:seg:r<label>).
function reviewWaitPrompt(seg, roundLabel, disp, chunkIndex) {
  return waitChunkPrompt(seg, reviewAcceptCmd(seg, roundLabel), disp, chunkIndex,
    "reviewer for segment " + seg + " (round " + roundLabel + ")",
    "do not review anything yourself");
}

// #348 -- the post-exhaustion authoritative re-check for the review wait. See
// waitRecheckPrompt(); this wrapper exists so the review site reads like its
// translate twin and so the shared reviewAcceptCmd() is spliced, never retyped.
function reviewWaitRecheckPrompt(seg, roundLabel) {
  return waitRecheckPromptFor(seg, reviewAcceptCmd(seg, roundLabel),
    "review of segment " + seg + " (round " + roundLabel + ")",
    "do not review anything yourself");
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

// #198 -- the Workflow's AUTHORITATIVE independent wait gate for the
// translate dispatch above. The codex translator runs in a DETACHED
// codex_job.py job; this poll re-validates the CANONICAL draft directly
// (never trusts any driver-written file). ACCEPT = draft_ready.py <seg>
// --expect-token <tok> exit 0 (token + delivery -- so an old-run straggler
// translator's draft with a stale token is never accepted) AND
// validate_draft.py <seg> prints OK (the six quality checks -- so a
// structurally-complete but content-defective draft is REJECTED).
//
// Since 1.16.1 (#348) this builds ONE CHUNK of the chunked wait, not the whole
// poll: chunkIndex selects the slice, and the bash grammar, the chunk bound and
// the fail-fast semantics live in waitChunkPrompt(). The chunk loop and the ONE
// non-polling authoritative re-check that follows an exhausted or failed poll
// belong to the CALL SITE, so an exhausted chunk is not a timeout on its own.
// The ACCEPT gate above is what is genuinely this wrapper's own.
function waitPrompt(seg, disp, chunkIndex) {
  return waitChunkPrompt(seg, translateAcceptCmd(seg), disp, chunkIndex,
    "translator for segment " + seg,
    "do not translate anything yourself, and do not read the draft");
}

// #348 -- the post-exhaustion authoritative re-check for the translate wait.
function waitRecheckPrompt(seg) {
  return waitRecheckPromptFor(seg, translateAcceptCmd(seg),
    "translation of segment " + seg,
    "do not translate anything yourself, and do not read the draft");
}

// 1.3.6 (#132 option b): the fixer now reads its findings from the
// AUTHORITATIVE on-disk review_path(seg) file, not from a spliced in-memory
// JSON object -- closes a gap where a read-agent transcription slip
// (issue/suggest text differing from what is on disk, while loc/severity
// still match) would previously pass review_artifact_check.py's narrowed
// {loc,severity} compare and then have the fixer apply the WRONG free-text
// instruction from the in-memory copy. review_ready.py already
// token-validated this exact file fresh THIS round before the fix call was
// ever dispatched (dispatch_token = <RUN_ID>:<seg>:r<roundLabel>), and the
// canonical review artifact is not rewritten again until the NEXT round's
// review job promotes a fresh one (the codex_job.py driver's atomic
// os.replace) -- long after this fix call returns -- so this read is fresh
// and race-free, never a stale or mid-write artifact.
//
// Deliberate, documented 3-argument signature (kept from the proven
// reference's own 2-argument fixPrompt(seg, round) shape, extended once --
// see references/gotchas.md item 5 and references/engine-loop.md's R1):
// revObj is still passed through (the SAME schema-validated object
// readReviewPrompt already returned this round -- used elsewhere for the
// clean/coverage_ok convergence decision in runRound and
// review_artifact_check.py's loc/severity/count binding), but fixPrompt
// itself no longer splices it into the prompt as the findings source.
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
//
// #409 Step 3: this prompt does NOT ask the fixer to run validate_draft.py
// and certify its own output. An earlier revision did (via the line just
// below the draft-rewrite instruction), but that self-report was dead text:
// runRound's own handling of callFix's return value (`fx`) only ever scans
// it for the literal DRAFT_MISSING sentinel -- "confirm it prints OK" was
// never parsed, checked, or acted on by anything downstream, so it read as
// an assurance this pipeline provides while providing none. A deterministic
// gate must not be executed by the party it is checking -- and the fixer
// self-certifying its own edit is exactly that.
//
// This is NOT closed by translateAcceptCmd()'s validate_draft.py splice
// (:1020) -- that gate is the TRANSLATE wait's own ACCEPT command
// (waitPrompt/waitChunkPrompt), fired exactly once, before this fix step
// (and the whole round loop) ever runs; it is never invoked again after a
// fix. Verified directly: reviewAcceptCmd() (the ACCEPT command every round
// after a fix actually waits on, via getVerifiedReview/reviewWaitPrompt)
// calls ONLY review_ready.py, and review_ready.py's own docstring lists
// its exact three checks -- review.schema.json validity, draft_sha1
// freshness, dispatch_token match -- none of which is validate_draft.py.
// The only thing that currently determines a post-fix draft's coverage_ok
// is reviewDispatchPrompt's own instruction to the NEXT round's REVIEWER
// ("First run the deterministic gate: validate_draft.py ... remember
// whether it printed OK or FAIL") -- still a self-report, just by a
// different party (a fresh reviewer turn, not the fixer that made the
// edit) than the one this comment removes. Deleting the fixer's own
// self-check is correct regardless (it was unparsed dead text either way);
// it is not, by itself, a claim that the post-fix draft is independently,
// deterministically re-validated anywhere in this file today.
function fixPrompt(seg, round, revObj) {
  const lines = [];
  lines.push("Effort: " + EFFORT + ". You are the Claude editor applying review findings to segment " + seg + ", round " + round + ".");
  lines.push("Read " + ROOT + "/segments/" + seg + ".review.json -- this is the AUTHORITATIVE source of the reviewer's findings for this round. review_ready.py already confirmed, before this fix call was ever dispatched, that this exact file is fresh (its dispatch_token matches this run and round) -- so this read is race-free. Its findings[] entries are the reviewer's RECOMMENDATIONS, not orders: the reviewer is one codex turn that saw this segment alone, and a finding's issue and suggest are unconstrained prose that nothing has checked against the source. Apply an entry you can substantiate; refuse one you cannot.");
  lines.push("Important: only codex translates. If the draft is missing or is not actually ready -- check by running " + PY + " " + ROOT + "/scripts/draft_ready.py " + seg + " -- do not translate it yourself: return exactly the line DRAFT_MISSING " + seg + " and write nothing.");
  lines.push("Otherwise, read " + ROOT + "/segments/" + seg + ".draft.json, " + ROOT + "/segments/segpack_" + seg + ".json and " + ROOT + "/style_bible.md, and work through the findings in " + ROOT + "/segments/" + seg + ".review.json one at a time. Never touch a placeholder sentinel (e.g. ⟦FNREF_...⟧, ⟦VERSE_...⟧) -- copy each one byte for byte in place. Keep the verse policy: " + VERSE_POLICY_INSTRUCTION_BLOCK);
  lines.push("Substantiate a finding against the source BEFORE you change anything, using the evidence its loc points at -- all of it is already on disk. A body block: that block's own plain_text (or source_html) in the segpack. FN:n -- the segpack's footnotes[] entry with that n, field source_text. VERSE:vid -- the entry with that vid under verse.store in " + ROOT + "/manifest.json, field plain_text (and source_html where present); the segpack's verses[] carries placement only and no verse source text at all. A markup or emphasis claim about a FOOTNOTE -- the source_html of the block named by that footnote's def_block, looked up under the blocks map in " + ROOT + "/manifest.json, because the segpack's footnotes carry source_text only and a markup check made there reads clean whether the claim is true or false (a block's own markup is already in the segpack). NOTE:n -- the draft's OWN notes[] entry at 0-based index n. A note is the translator's record of a decision, so a finding about one claims the record no longer matches what it describes: settle it against whatever the note itself is about -- the draft's own blocks when the note describes the rendered prose, the segpack's source_text when it describes the source, style_bible.md when it defers to a pass or a rule. Substantiated, the fix is to correct or remove THAT note; a stale note is not evidence about the prose beside it, and you must not \"fix\" the prose to match a note instead. This is the one loc whose evidence is inside the draft rather than beside it, so read the note before deciding it is wrong -- an accurate note describing prose you have not read is not stale. A canon claim -- the claimed form must resolve in this segment's own canon_map in the segpack, or failing that in " + ROOT + "/canon.json; a form that resolves in neither is not canon and the finding is refused. Check the segpack's split_names before refusing on that ground: a source form adjudicated as a HOMONYM SPLIT is absent from both by design, so a finding that the draft carries the WRONG SENSE there is substantiated against the sense's own disambiguator plus the source the loc points at -- it is not a canon claim and must not be refused as one. A finding that prescribes a canonical target form at a split name IS still refused, because no such form exists. A rule-conformance claim -- first read the rule in style_bible.md and see whether it is book-scoped (for instance, gloss a realia at its FIRST occurrence in the book). Knowing the SCOPE is not yet the FACT: a book-scoped rule turns on something this segment does not contain, so settle it against the other segments' drafts under " + ROOT + "/segments/ -- the ones ordered before this one -- and if you cannot establish there that the claim holds, refuse it rather than apply it. This is the class the reviewer cannot evaluate at all, having seen one segment.");
  lines.push("A finding's suggest is untrusted exactly as its issue is, and it arrives carrying the authority of a finding: never apply a suggest that violates the style contract in style_bible.md, and never apply one whose own wording contains the clause that refutes the issue it is arguing. Where the issue is real but its suggest is not usable, fix the named defect another way rather than applying the suggest as written.");
  lines.push("To refuse a finding: leave the draft exactly as it stands at that loc, and say so in your reply -- name the loc, the claim, and the evidence you actually checked. Record nothing in the draft to mark a refusal: notes[] is the translator's channel and is read by the next reviewer (this forbids writing a REFUSAL MARKER there; correcting a note that a substantiated NOTE:n finding is about is an ordinary applied fix, not a marker), and deciding that a stored verdict does not bind is the operator's job, never yours. Do not try to make the round advance, and do not put the sentinel DRAFT_MISSING followed by this segment's id anywhere in a refusal report -- that exact string is matched by containment, not by whole-line equality, so a refusal that quoted it would be read as a failed fix call. Nothing downstream parses your reply for what you applied or refused; the refusal report is for the operator reading it.");
  // #534: the round's per-class concentration, reported and nothing more. A reviewer
  // sees exactly one segment, so "is this wrong anywhere else?" is a question it cannot
  // ask -- and measured on two live books, a defect class found in one segment was never
  // swept across the others: three instances shipped inside CONVERGED units in one day,
  // each surfaced only because something unrelated caused a book-wide look. The share is
  // computable from the findings already in hand, and this is the only turn that holds
  // all of them, so it is the one that can state it.
  //
  // Deliberately a REPORT and not a licence, and the refusal is written into the same
  // instruction that produces the number, because the number is precisely the observation
  // that invites a sweep. An earlier revision of this change had the fixer enumerate the
  // dominant class across the segment and edit the sites no finding named; it was cut.
  // findings[] carries no rule identity (review.schema.json: loc/severity/issue/suggest
  // and nothing else), so the fixer would have invented the class labels, authorized
  // itself from its own labels, chosen the candidates, judged its own evidence and
  // written the edits -- with nothing that can read content between the decision and the
  // draft, in a step that already exists because a reviewer's findings are unchecked
  // prose (#532). The issue's own measurements price that failure: 109 occurrences of
  // strings the book italicises somewhere, ~10 of them defects; 98 sites of a quotation
  // class, 86 already correct and 66 of those compliant with a DIFFERENT rule of the same
  // contract. A uniform sweep fails worst exactly where the book is most consistent.
  // Enumerating a class therefore stays the operator's (SKILL.md, W6) -- it reaches
  // segments this call was not given, and acting on a converged unit stales it.
  lines.push("Before you finish, report the SHAPE of this round: group the findings you were given by the rule in " + ROOT + "/style_bible.md each one instantiates, and give the counts as <rule>: N of M findings this round. A finding that is a one-off slip at its own loc instantiates no rule, and saying so is a real answer, not a gap. Put this on the lines above the FIXED line, beside any refusal report and under the same DRAFT_MISSING prohibition stated above; it is for the operator, and nothing downstream parses it.");
  lines.push("That report is a report and not a licence: it changes nothing about which loci you edit. Apply and refuse exactly as stated above, and never edit a site no finding named -- not even one that plainly instantiates the same rule as a finding you just applied. A class that dominates a round is the operator's to enumerate and adjudicate site by site, never yours to sweep: it reaches segments this call was not given, and a converged unit goes stale the moment it is touched. Record none of this in the draft either -- notes[] is the translator's channel.");
  lines.push("Never change the set of block, footnote, or verse keys -- they must stay exactly 1:1 with the segpack.");
  lines.push("The draft also carries a dispatch_token top-level field -- copy its existing value byte for byte into your rewritten draft, unchanged; never invent, drop, or recompute it.");
  lines.push("Rewrite " + ROOT + "/segments/" + seg + ".draft.json with your fixes. If you substantiated nothing and refused every finding, leave that file exactly as you found it.");
  lines.push("End your reply with the line: FIXED " + seg + " r" + round + ". Put any refusal report on the lines above that line.");
  return lines.join("\n");
}

// #131 facet A helper -- fires ONLY from runRound's fix-call branch, on the
// terminal path taken when callFix comes back falsy/DRAFT_MISSING, to tell
// apart a genuinely absent draft from a transient fix-call failure (agent
// died / output-token ceiling / classifier block) on an otherwise present,
// valid draft. This terminal path is strictly SHORTER than the worst-case
// full-MAXFIX-rounds-then-final path the batch_agent_cap estimator sizes
// against (see the one-line note above estimatedCalls further down), so
// this extra call never affects the preflight bound.
function draftProbePrompt(seg) {
  const lines = [];
  lines.push("Effort: low. Mechanical probe only -- do not translate, fix, or judge anything.");
  lines.push("Segment: " + seg + ". Durable root: " + ROOT + ".");
  lines.push("Run: " + PY + " " + ROOT + "/scripts/draft_ready.py " + seg + " -- note whether it exits 0 (ready) or not.");
  lines.push("Then run: " + PY + " " + ROOT + "/scripts/validate_draft.py " + seg + " -- note whether it prints OK or FAIL.");
  lines.push("Return present: true only if BOTH commands above succeeded (draft_ready.py exited 0 AND validate_draft.py printed OK); otherwise return present: false.");
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
// final confirming one. revObj is spliced in directly to build the
// --expected-file below (1.3.6/#132 option b: fixPrompt no longer uses this
// splice mechanism itself -- it reads review_path(seg) from disk instead;
// see that function's own comment); the script, not the agent, does the
// actual comparison against the on-disk review_path(seg) -- see
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

// #198 -- dispatch the DETACHED codex review job via the plain-Claude
// reviewDrivePrompt (no agentType, effort low), then parse the DISP nonce
// off the anchored `DISPATCHED <seg> <DISP>` return with parseDisp (disp=""
// on any mismatch -> fail-fast disabled, safe). Returns the captured disp
// for getVerifiedReview to thread into reviewWaitPrompt.
async function callReviewDispatch(seg, roundLabel) {
  const label = "review-dispatch:" + seg + ":r" + roundLabel;
  const raw = await agent(reviewDrivePrompt(seg, roundLabel), {
    effort: "low", phase: "ReviewFix", label: label,
  });
  return parseDisp(raw, seg);
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
    effort: EFFORT, phase: "ReviewFix", label: label,
  });
}

// #131 facet A -- see draftProbePrompt's own comment above for the full
// rationale. Label frozen as "draft-probe:" + seg (CONTRACT §4). Returns
// true (draft present and valid), false (the probe genuinely ran and
// confirmed the draft is absent/invalid), or null (the probe call ITSELF
// failed -- agent death / output-token ceiling / classifier block, the
// SAME transient modes this whole facet exists to disambiguate for the fix
// call). A null return is inconclusive, never treated as proof of absence
// -- the caller must route it the same recoverable way as true, or a
// correlated outage on both the fix call and the probe call would defeat
// facet A entirely by falling through to a terminal draft-missing write.
async function draftPresentAndValid(seg) {
  const label = "draft-probe:" + seg;
  const raw = await agent(draftProbePrompt(seg), {
    effort: "low", phase: "ReviewFix", label: label, schema: DRAFT_PROBE_SCHEMA,
  });
  if (!raw) return null;
  return raw.present === true;
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
// and references/false-green-gate.md. The dispatch is a plain-Claude DRIVE
// of the DETACHED codex_job.py review job (translateStage's own #198 pattern
// -- codex writes disk, its return is not the verdict); this function never
// trusts the dispatcher's return except to capture DISP, only the on-disk
// canonical artifact the bounded poll below re-validates. An EXHAUSTED wait no
// longer ends the point immediately: since #348 one non-polling authoritative
// re-check runs first, and only if that also fails is the point blocked as
// review-timeout -- so a run that finished while the poll was between chunks is
// not reported as a timeout. The TIMEOUT sentinel itself is gone from these two
// sites; the round-5 comment sweep named waitPrompt/reviewWaitPrompt and missed
// this one. No read/check is attempted against an artifact that may still be
// mid-write.
//
// After a successful wait, ONE shared retry budget covers the read and
// the check together: a null read OR a match:false check retries the
// (read THEN check) pair once, fresh; still failing afterward ->
// blocked/review-null (the retry's own read came back null) or
// blocked/review-artifact-mismatch (the retry's read succeeded but still
// didn't match) -- never two independent read-retry/check-retry budgets.
// Call budget for one review point: dispatch(1) + wait(WAIT_CALLS) + read(1) +
// check(1) + [retry: read(1) + check(1)] = 5 + WAIT_CALLS calls, worst case.
// It was a flat 6 before 1.16.1, when a wait was one call; the ladder at the
// bottom of this file carries the generalised arithmetic and reduces to the
// old 6 when WAIT_CALLS = 1.
async function getVerifiedReview(seg, roundLabel) {
  const disp = await callReviewDispatch(seg, roundLabel);

  // #348 -- the wait is CHUNKED across WAIT_CHUNKS agent calls (the Bash tool
  // clamps any single call at BASH_CALL_CAP_SEC), then backed by ONE
  // authoritative non-polling re-check. Chunk calls keep the EXISTING label
  // `review-wait:<seg>:r<round>` unchanged; only the re-check gets a new one.
  // Written inline here rather than factored into a helper so this site stays
  // the deliberate twin of reviewFixLoop's translate wait -- the parsing is
  // shared (waitChunkVerdict), which is where divergence would actually hurt.
  const waitLabel = "review-wait:" + seg + ":r" + roundLabel;
  let verdict = "pending";
  for (let chunk = 1; chunk <= WAIT_CHUNKS; chunk++) {
    const chunkReply = await agent(reviewWaitPrompt(seg, roundLabel, disp, chunk), {
      effort: "low", phase: "ReviewFix", label: waitLabel,
    });
    verdict = waitChunkVerdict(chunkReply, seg);
    if (verdict !== "pending") break;
  }
  // #348 -- the authoritative re-check. Runs whenever the chunk loop did not
  // end READY: budget exhausted, OR a chunk reported the driver's fail
  // sentinel. See waitRecheckPromptFor() for why the FAILED path re-checks too.
  if (verdict !== "ready") {
    const recheck = await agent(reviewWaitRecheckPrompt(seg, roundLabel), {
      effort: "low", phase: "ReviewFix",
      label: "review-wait-recheck:" + seg + ":r" + roundLabel,
    });
    verdict = waitChunkVerdict(recheck, seg);
  }
  // Every reply at this site -- chunk or re-check -- is parsed by
  // waitChunkVerdict() and nothing else, which is where #228's and #308's
  // properties now live as one enforced order (containment guards first, then
  // whole-line READY). See that function's comment; this call site deliberately
  // does not re-implement any part of the reading.
  //
  // The reason string is unchanged on purpose: select_segments.py's
  // "non-terminal -> recoverable" rule and every recovery doc key off
  // "review-timeout". A false RED here still blocks the segment for this run --
  // the fail-safe direction, but not an in-run retry.
  if (verdict !== "ready") {
    return { status: "blocked", reason: "review-timeout" };
  }

  const first = await readAndCheck(seg, roundLabel, false);
  if (artifactCheckMatched(first.art)) return matchedVerdict(first.rev);

  const retry = await readAndCheck(seg, roundLabel, true);
  if (!retry.rev) return { status: "blocked", reason: "review-null" };
  if (artifactCheckMatched(retry.art)) return matchedVerdict(retry.rev);

  return { status: "blocked", reason: "review-artifact-mismatch" };
}

// One review/fix round. isFinal marks the mandatory confirming review after
// the round cap -- on that round a not-clean verdict ends the segment as
// non_converged/cap (handled by the caller), never dispatches a fix.
async function runRound(seg, round, isFinal) {
  const roundLabel = isFinal ? "final" : String(round);

  const verified = await getVerifiedReview(seg, roundLabel);
  if (verified.status === "blocked") {
    // #131 facet B: every getVerifiedReview blocked reason (review-timeout,
    // review-null, review-artifact-mismatch, and -- #133 -- review-
    // fabricated-loc) is transient/infra, never genuine content
    // non-convergence: a codex reviewer that died mid-dispatch, a review
    // artifact that never landed or never matched on either attempt, or a
    // schema-valid verdict caught referencing a phantom finding. Do NOT
    // record a terminal ledger write here -- the in_progress fragment
    // translateStage already wrote stays the durable record, and
    // select_segments.py's own "any non-terminal/unrecognized status ->
    // recoverable" rule (references/ledger-and-resumability.md) picks the
    // segment back up and auto-redispatches it on the next run.
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
  // Line-oriented match via sentinelVerdict (#308) against the lone failure
  // sentinel (okSentinel is null -- there is no success sentinel to require
  // as the final line here, only a failure sentinel to scan for on any
  // line), never a whole-string exact compare or a substring check
  // (content-matching-sentinel-fragility, #228) -- fixPrompt instructs the
  // agent to return exactly "DRAFT_MISSING <seg>", and a genuine fix reply
  // that merely mentions that literal substring in its own prose must not
  // collide with it, while a benign prose-decorated DRAFT_MISSING must still
  // be recognized (#308's direction) instead of silently read as an ordinary
  // review round. The `!fx ||` falsy branch is KEPT deliberately and is NOT
  // redundant with the sentinelVerdict check: the runtime treats a falsy fx
  // (agent death / output-token ceiling / classifier block -- #131 facet A)
  // and a genuine DRAFT_MISSING alike as inconclusive, both routed through
  // the draftPresentAndValid probe below, whose own contract says null means
  // inconclusive, never absent (see its comment above). Dropping `!fx` would
  // let a dead fix call silently read as an ordinary review round instead of
  // probing for what actually happened.
  //
  // #228 DELIBERATELY REVERSED HERE -- read this before "simplifying" it back.
  // That fix built this site on whole-trimmed-line equality precisely so a fix
  // reply that DISCUSSES "DRAFT_MISSING <seg>" in its prose could not be
  // mistaken for a report of one, and that protection did work: measured, a
  // prose mention did not fire it. It was reversed anyway, on measurement.
  //
  // What the whole-line rule cost. "DRAFT_MISSING <seg>" is the OK sentinel at
  // this call, so gluing does not fake a pass -- it makes a REAL report go
  // UNRECOGNIZED, falling through to `terminal: false` and silently continuing
  // as an ordinary review round over a draft the fix agent just said was
  // missing. Measured over GLUE_CHARS (16 items,
  // tests/glossary_citation_review.test.py -- NOT the 15-item ALL_GLUES the
  // mass-translate suite uses; see rejectedAnywhere()'s comment for why both
  // exist), in the two shapes a reply can take. The count is SHAPE-DEPENDENT as
  // well as set-dependent, which is easy to get wrong:
  //   prose on the SAME line as the sentinel ("prose<GLUE>DRAFT_MISSING <seg>")
  //     -- 15 of 16 missed. trim() only reaches a line's two ends, so it never
  //     gets a chance at glue sitting between the prose and the sentinel.
  //   sentinel ALONE on its line ("prose\n<GLUE>DRAFT_MISSING <seg>")
  //     -- 7 of 16 missed. Here trim() does reach the glue and strips 9 of the
  //     16; the 7 survivors are U+001C, U+001D, U+001E, U+001F, U+0085, U+200B
  //     and the ordinary letter "x". Note U+0085 NEL is NOT trim()-strippable
  //     in JS while U+2028 and U+2029 ARE -- the strippable set is not "the
  //     characters that look like whitespace", so do not reason about it by eye.
  //
  // What the reversal costs. mentionedAnywhere() cannot tell a report from a
  // mention, so a fix reply that merely discusses the sentinel now lands here
  // too. That is the false-RED direction and it is bounded:
  // draftPresentAndValid() probes, finds the draft present, and returns
  // reason:"fix-call-failed" with NO terminal ledger write, so the in_progress
  // fragment stays the durable record and the segment auto-redispatches next
  // run. The whole-line rule's failure, by contrast, was silent and left the
  // pipeline treating a missing draft as reviewable. One wasted segment re-run
  // beats that, which is the trade the wait sites above already made.
  //
  // Containment SUBSUMES the old check -- any reply whose last trimmed line
  // equals the sentinel also contains it -- so sentinelVerdict() is not merely
  // redundant here, it is strictly narrower, and calling both would add nothing.
  // The `!fx ||` falsy branch above is still not redundant, for its own reason.
  if (!fx || mentionedAnywhere(fx, "DRAFT_MISSING " + seg)) {
    // #131 facet A: a falsy/DRAFT_MISSING return conflates (a) a genuine
    // missing draft with (b) a hard API/output-token-ceiling error and (c) a
    // classifier block -- both (b) and (c) also yield a falsy fx even though
    // the draft itself is present and fine. Probe before concluding which
    // one this is.
    const present = await draftPresentAndValid(seg);
    if (present !== false) {
      // present === true (draft present and valid) OR present === null
      // (the probe call itself failed -- inconclusive, NOT proof of
      // absence -- see draftPresentAndValid's own comment) -- both are
      // transient: skip the ledger write; the in_progress fragment
      // classifies recoverable and auto-redispatches next run, same as
      // facets B/C above. Reuses the "fix-call-failed" reason rather than
      // adding a new one for the probe-failed sub-case.
      return { terminal: true, value: { seg: seg, converged: false, reason: "fix-call-failed", rounds: round } };
    }
    // present === false: the probe genuinely ran and confirmed the draft is
    // absent/invalid after a translate that reported READY -- a real
    // anomaly worth human attention -- keep this path terminal
    // (blocked/draft-missing -> human_escalation), unchanged from before.
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

  // #198 -- the DISP captured by translateStage's dispatcher (or "" if its
  // return was unparseable -- safe degradation, fail-fast simply disabled).
  const disp = stage1Result && typeof stage1Result.disp === "string" ? stage1Result.disp : "";
  // #348 -- the deliberate twin of getVerifiedReview's chunked wait: the same
  // WAIT_CHUNKS bounded chunks under the same existing label, then ONE
  // authoritative non-polling re-check. Kept inline here rather than factored
  // out so both wait sites still read as this file's two parallel polls; the
  // PARSING is shared (waitChunkVerdict), which is where divergence would
  // actually cost something.
  //
  // This is the worse of the two sites to get wrong in EITHER direction. A
  // false GREEN sends the entire review/fix cycle over a draft that never
  // finished translating, and nothing on that path is recorded as recoverable,
  // so the "we'll pick it back up next run" net never fires. A false RED --
  // which is what #348 was, ~30 times per killed poll -- discards a finished
  // translation and re-runs a 45-minute codex job. The chunk loop addresses
  // neither by itself; the re-check below is what closes the false RED.
  let verdict = "pending";
  for (let chunk = 1; chunk <= WAIT_CHUNKS; chunk++) {
    const chunkReply = await agent(waitPrompt(seg, disp, chunk), {
      effort: "low", phase: "ReviewFix", label: "wait:" + seg,
    });
    verdict = waitChunkVerdict(chunkReply, seg);
    if (verdict !== "pending") break;
  }
  if (verdict !== "ready") {
    const recheck = await agent(waitRecheckPrompt(seg), {
      effort: "low", phase: "ReviewFix", label: "wait-recheck:" + seg,
    });
    verdict = waitChunkVerdict(recheck, seg);
  }
  // Every reply at this site -- chunk or re-check -- is parsed by
  // waitChunkVerdict() and nothing else; see that function's comment for the
  // #228/#308 properties and the enforced guard order. This call site
  // deliberately does not re-implement any part of the reading.
  if (verdict !== "ready") {
    // #131 facet C: a translate-timeout is transient/mechanical (the codex
    // translator agent died, hit an infra hiccup, or is simply still
    // running past the bounded poll) -- not genuine content
    // non-convergence. Do NOT record a terminal ledger write here: the
    // in_progress fragment translateStage already wrote stays the durable
    // record, and select_segments.py's own "any non-terminal/unrecognized
    // status -> recoverable" rule (references/ledger-and-resumability.md)
    // picks the segment back up and auto-redispatches it on the next run.
    return { seg: seg, converged: false, reason: "translate-timeout" };
  }

  for (let round = 1; round <= MAXFIX; round++) {
    const r = await runRound(seg, round, false);
    if (r.terminal) return r.value;
    // #532: the count is the REVIEW's findings[] length, not a count of what
    // the fixer applied -- the only thing parsed out of the fix reply is the
    // DRAFT_MISSING <seg> sentinel, never an applied/refused outcome -- and
    // since the fixer may now refuse a finding it cannot substantiate,
    // "N findings fixed" would be false on any round that refused one.
    // Report what is actually known here: the round was dispatched and is
    // being re-reviewed.
    log(seg + ": round " + round + " -- fix turn completed over " + r.findingsCount + " findings, re-reviewing");
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
// otherwise leave zero durable record), then the plain-Claude DRIVE of the
// DETACHED codex translate job (#198 -- translateDrivePrompt launches
// codex_job.py detached and returns immediately; codex writes disk, its
// return is not the verdict). The translate job stays schema-less and is
// gated instead by the wait poll's own draft_ready.py + validate_draft.py
// re-validation of the promoted canonical (see references/false-green-gate.md).
// Returns { disp } -- the DISP captured off the dispatcher's anchored return
// (or "" on any mismatch), threaded into reviewFixLoop's own wait poll.
async function translateStage(seg) {
  const rec = await recordLedgerCall(seg, { status: "in_progress" }, "ledger:in_progress:" + seg);
  if (!rec.ok) return { ledgerFailed: true, result: rec.failResult };

  const raw = await agent(translateDrivePrompt(seg), {
    effort: "low", phase: "Translate", label: "translate:" + seg,
  });
  return { disp: parseDisp(raw, seg) };
}

// ---------------------------------------------------------------------------
// batch_agent_cap preflight -- see references/orchestration-and-batching.md's
// "batch_agent_cap" section for the full derivation. This estimator is new
// plugin hardening, not itself source-proven (the real reference script has
// no such check anywhere). Must run, and must be able to return, BEFORE
// pipeline() is ever called below.
//
// Per segment, with #348's chunked waits. A WAIT is no longer one call: worst
// case it is WAIT_CALLS = WAIT_CHUNKS chunks + 1 authoritative re-check.
//   2 fixed non-wait calls (in_progress ledger write, translate dispatch)
// + 1 translate wait                      -> WAIT_CALLS
// + MAXFIX normal rounds, each = a review point's 6-call worst case
//   (of which ONE is the review wait -> WAIT_CALLS) + 1 fix call
//                                          -> MAXFIX * (5 + WAIT_CALLS + 1)
// + 1 mandatory final review point (6 calls, no fix dispatched)
//                                          -> 5 + WAIT_CALLS
// + 1 terminal ledger write
// = 2 + WAIT_CALLS + MAXFIX*(6 + WAIT_CALLS) + 5 + WAIT_CALLS + 1
// = 8 + 2*WAIT_CALLS + MAXFIX*(6 + WAIT_CALLS).
// Batch-level: 1 (the final merge-ledger completeness check; there is no
// batch pre-clean call).
//
// PROVABLY A GENERALISATION, not a rewrite: substitute WAIT_CALLS = 1 and this
// reduces to 8 + 2 + MAXFIX*7 = 10 + 7*MAXFIX, the pre-#348 formula verbatim.
//
// OPERATIONAL CONSEQUENCE, stated rather than hidden: at WAIT_CALLS = 9 and
// MAXFIX = 4 a segment budgets 86 calls, up from 38. At the shipped
// engine.batch_agent_cap: 3500 a normal batch therefore drops from 92 segments
// to 40 -- both re-derived here rather than quoted: 1 + 92*38 = 3497 and
// 1 + 40*86 = 3441, with 93 and 41 the first values to exceed the cap.
// This said "~78 segments" until round 5. That number is a batch SIZE, not a
// ceiling: it is the ~78-segment repro in
// references/orchestration-and-batching.md's note on 1.3.5 raising this cap
// from 1000, where the whole point is that 1 + 78*38 = 2965 fitted under 3500
// WITH HEADROOM. Quoting it here turned a repro size into a capacity and then
// compared it against a real ceiling.
// profile.example.yml states the post-#348 figure (40, and 26 at cap 1000);
// neither it nor references/orchestration-and-batching.md ever carried a
// before/after capacity pair, so the claim that they "carry the same
// arithmetic" was describing agreement that did not exist.
//
// #131's draftPresentAndValid probe does NOT change this formula: it fires
// only from runRound's fix-call-failed terminal branch, which ENDS the
// segment right there -- strictly shorter than the worst-case path this
// formula already sizes against (a full MAXFIX rounds then the final
// review), so the ceiling this preflight enforces stays sound.
// ---------------------------------------------------------------------------
const estimatedCalls = 1 + SEGS.length * (8 + 2 * WAIT_CALLS + MAXFIX * (6 + WAIT_CALLS));
if (estimatedCalls > BATCH_AGENT_CAP) {
  log(
    "Batch too large: estimatedCalls=" + estimatedCalls +
    " exceeds engine.batch_agent_cap=" + BATCH_AGENT_CAP +
    " for " + SEGS.length + " segment(s) at max_fix_rounds=" + MAXFIX + "."
  );
  return { converged: [], failed: [], reason: "batch-too-large", estimatedCalls: estimatedCalls, cap: BATCH_AGENT_CAP };
}

// ---------------------------------------------------------------------------
// max_codex_jobs_per_batch preflight (#409 stage 0, issue #402) -- a SECOND,
// INDEPENDENT preflight, run in addition to the batch_agent_cap estimator
// directly above (batch_agent_cap is not removed or replaced -- it still
// governs this same template's own agent()-call budget, and it remains the
// shared knob for the glossary-pass and skeptic-pass preflights elsewhere).
// That estimator sizes Workflow agent() calls, a proxy that does not name
// the resource an operator actually spends money/time on. This gate sizes
// the real thing directly: codex dispatches (one detached codex_job.py
// launch per translate and per review -- NOT per fix; see CODEX_JOBS_PER_SEG
// below and the comment above it for why). Must run, and must
// be able to return, BEFORE pipeline() is ever called below -- same
// placement contract as the estimator directly above, so a refusal from
// EITHER gate stays "before any work" rather than mid-batch. Issue #402's
// complaint was that a preflight refusal did not name its own culprit
// clearly enough for an operator to act on -- the message below names the
// knob, the computed need, the effective limit, and the segment count.
//
// Deliberately placed AFTER the batch_agent_cap check above rather than
// before it: both gates return before any dispatch, so either ordering
// satisfies "refuses before any work" identically, and this ordering keeps
// this file's own top-level `const estimatedCalls` marker (see
// tests/draft_path_convention.test.py's `_JS_CUT_MARKER`, which slices the
// raw template at that exact string to extract only its function
// declarations) as the FIRST top-level `return`-bearing statement in the
// file -- unchanged from before this gate existed.
//
// Per segment, worst case (every round non-clean, so every fix round
// actually fires):
//   1 translate job
// + (MAXFIX + 1) review jobs      -- one per normal round, plus the one
//                                    mandatory final confirming review
// = MAXFIX + 2 codex jobs per segment.
//
// The MAXFIX fix rounds are deliberately NOT counted here. callFix() is a
// plain Workflow agent() call -- the CLAUDE fix step -- and never launches
// codex_job.py. This file has exactly two launch sites, the dispatch shells
// built in translateDrivePrompt and reviewDrivePrompt; nothing else spawns a
// driver. A review round cannot re-dispatch either: its retry path
// (readAndCheck) re-reads the artifact codex already wrote rather than
// starting a second job.
//
// Counting the fix calls made this gate measure a different resource from the
// one its name, this comment, and the operator-facing refusal all describe --
// it over-counted by MAXFIX per segment and refused batches that were in fact
// within engine.max_codex_jobs_per_batch. Concretely, at MAXFIX=4 a
// 41-segment batch launches 246 codex jobs but was computed as 410 and
// rejected against the default cap of 400.
// ---------------------------------------------------------------------------
const CODEX_JOBS_PER_SEG = MAXFIX + 2;
const estimatedCodexJobs = SEGS.length * CODEX_JOBS_PER_SEG;
if (estimatedCodexJobs > MAX_CODEX_JOBS_PER_BATCH) {
  // Deliberately says "the effective ... limit", never "engine.max_codex_
  // jobs_per_batch=N" as if N were necessarily something the operator
  // wrote. This value is substituted at instantiation time either way
  // (profile.yml's own value, or the schema's documented default when the
  // profile omits the key) -- the two cases are byte-identical here, so the
  // message must stay true under BOTH without claiming which one applied.
  // "Raise it in profile.yml" is the correct next step regardless: it works
  // whether the operator is editing an existing value or adding the key
  // for the first time.
  log(
    "Batch too large: this batch needs estimatedCodexJobs=" + estimatedCodexJobs +
    " for " + SEGS.length + " segment(s) at max_fix_rounds=" + MAXFIX +
    ", over the effective engine.max_codex_jobs_per_batch limit of " + MAX_CODEX_JOBS_PER_BATCH +
    ". Raise it in profile.yml under engine: to allow a larger batch."
  );
  return {
    converged: [], failed: [], reason: "batch-too-large-codex-jobs",
    estimatedCodexJobs: estimatedCodexJobs, codexJobsCap: MAX_CODEX_JOBS_PER_BATCH,
  };
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
