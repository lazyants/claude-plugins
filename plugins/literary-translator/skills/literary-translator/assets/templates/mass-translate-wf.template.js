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
// (waitPrompt/reviewWaitPrompt) is the AUTHORITATIVE independent gate -- an
// elapsed-time loop (bound = CODEX_DEADLINE_SEC + CODEX_FINALIZE_BUDGET_SEC
// + CODEX_WAIT_GRACE_SEC = 3450 s, gate-then-deadline-break with NO separate
// post-loop gate, plus one final finite gate check; NO `timeout` binary)
// whose ACCEPT is a FULL re-validation of the CURRENT canonical (translate:
// draft_ready.py --expect-token AND validate_draft.py; review: review_ready
// .py --expect-token), never a trust of any driver-written file. Its
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
// The wait poll's elapsed-time outer bound: the driver's deadline plus its
// finalize budget plus a grace margin, so the Workflow poll never gives up
// before the driver can promote/finalize. = 2700 + 150 + 600 = 3450 s.
const WAIT_BOUND_SEC = CODEX_DEADLINE_SEC + CODEX_FINALIZE_BUDGET_SEC + CODEX_WAIT_GRACE_SEC;

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
// genuine loc is ALWAYS a colon-delimited structural reference: a block id
// ("{btype}:{seg}:{ord}", e.g. PARA:seg01:0001, or the shorter HEAD:seg01
// shape some adapters emit -- btype is deliberately NOT a fixed enum, see
// manifest.schema.json; adapters may emit their own block types, so only
// the ":" shape is invariant across all of them), FN:n, or VERSE:vid. The
// named infra sentinels are bare, colonless tokens -- that is the one true
// invariant this gate can lean on without hardcoding a block-type allowlist
// (which would over-reject a legitimate custom adapter's own block types)
// or a segpack-membership check (which would over-reject a healthy
// reviewer's slightly-off but
// genuine content ref). Residual false-block: a healthy reviewer emitting a
// colonless holistic loc (e.g. "overall") would also be caught here --
// deviates from the shipped block_id|FN:n|VERSE:vid contract, but the
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
  lines.push("Translate every block, footnote, and verse this segpack contains. Copy every placeholder sentinel (e.g. ⟦FNREF_N⟧, ⟦VERSE_...⟧) byte for byte, in its correct position in the sentence -- never translate, drop, or reword a sentinel itself. Any embedded third-language text (Latin, an older form of the source language, or similar) gets an in-text gloss, never a notes-only translation. The segpack's canon_map gives each already-canonized name's frozen canonical target form (source form -> target form): render each such name using that target form's stem/spelling, declined as the target grammar requires -- a correctly inflected form of the canonical stem is correct, never a verbatim copy of the citation form where grammar needs another case. For a new_names entry not yet in canon_map, choose a reasoned rendering and flag it in notes as NEW:.");
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
    "nohup " + PY + " " + ROOT + "/scripts/codex_job.py --kind translate --companion '" + COMPANION + "' --cwd " + ROOT + " --seg " + seg + " --prompt-file \"$TASKFILE\" --expect-token " + expectToken + " --disp \"$DISP\" --deadline-sec " + CODEX_DEADLINE_SEC + " --effort " + EFFORT + MODEL_ARG + " </dev/null >/dev/null 2>&1 &\n" +
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
  lines.push("Check the draft against the source for: full accuracy (no omissions or distortions), word-sense and realia fidelity for the source era and context -- ask explicitly whether each notable word means what it meant in that period and context, not what it means today -- name/canon fidelity, placeholder sentinel fidelity, verse per the policy above, and literary quality (register, idiom, natural seams, rhythm).");
  lines.push("Canon-name fidelity specifically: the segpack's canon_map gives each already-canonized name's frozen canonical target form. Flag a canon name ONLY if the draft renders a different name, a different transliteration of the canonical stem, leaves a canonical name untranslated, or swaps an epithet for a real surname -- a correctly inflected/declined form of the canonical stem is CORRECT and must NOT be flagged.");
  lines.push("A canon_map target form is authoritative as given. Never flag a canon name merely because its frozen canonical target form is lexically unrelated to the SOURCE form -- for a sense-translated speaking name (basis:\"sense_translated\") that is expected and correct. The deviation triggers above still apply. Correctness of the frozen canon decision itself is out of scope for this review -- a suspected error is reopened via the glossary/adjudication route, never flagged here.");
  lines.push("Build a JSON object with exactly these five fields: clean (true only if there are no findings that require a fix round), coverage_ok (true only if the deterministic gate above printed OK), findings (an array of objects with loc/severity/issue/suggest -- use a loc like \"VERSE:{vid}\" for a verse-specific finding), draft_sha1 (the value you computed before reading the draft, above), and dispatch_token (exactly this literal string: " + JSON.stringify(dispatchToken) + ").");
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
    "nohup " + PY + " " + ROOT + "/scripts/codex_job.py --kind review --companion '" + COMPANION + "' --cwd " + ROOT + " --seg " + seg + " --prompt-file \"$TASKFILE\" --expect-token " + expectToken + " --disp \"$DISP\" --deadline-sec " + CODEX_DEADLINE_SEC + " --effort " + EFFORT + MODEL_ARG + " </dev/null >/dev/null 2>&1 &\n" +
    "echo \"DISPATCHED " + seg + " $DISP\"";
  const lines = [];
  lines.push("Effort: low. You are DISPATCHING a background codex review job for segment " + seg + " (round " + roundLabel + ") -- you do NOT review anything yourself, and you do NOT wait for the job to finish.");
  lines.push("Run EXACTLY ONE bash command -- this entire block, verbatim:");
  lines.push(cmd);
  lines.push("Then return EXACTLY the single line that command echoed: the word DISPATCHED, then " + seg + ", then the generated DISP value. Do not poll the job, do not read any file, and add no other text.");
  return lines.join("\n");
}

// #198 -- the Workflow's AUTHORITATIVE independent wait gate for the review
// dispatch above. The codex reviewer runs in a DETACHED codex_job.py job;
// this poll re-validates the CANONICAL review artifact directly (never
// trusts any driver-written file). ACCEPT = review_ready.py <seg>
// --expect-token <tok> exit 0 (full schema + draft_sha1 freshness + this
// round's dispatch_token). Elapsed-time loop, gate-then-deadline-break, NO
// separate post-loop gate (so exactly ONE gate can straddle the deadline).
// The optional fail-fast (only when disp is non-empty) is a pure presence
// check on the DISP-named sentinel the driver writes when it did NOT
// promote, evaluated ONLY AFTER the ACCEPT gate did not pass this iteration
// -- a valid canonical always wins over any sentinel; an empty disp disables
// fail-fast and simply polls to the bound (safe degradation). No external
// `timeout` binary anywhere. roundLabel derives the token internally
// (RUN_ID:seg:r<label>).
function reviewWaitPrompt(seg, roundLabel, disp) {
  const dispatchToken = RUN_ID + ":" + seg + ":r" + roundLabel;
  const failFast = disp
    ? " [ -f \"" + ROOT + "/segments/.codex_failed." + seg + "." + disp + "\" ] && exit 1;"
    : "";
  const lines = [];
  lines.push("The codex reviewer for segment " + seg + " (round " + roundLabel + ") is running in a DETACHED background job (launched by codex_job.py). Wait for it by running EXACTLY ONE bash command -- an elapsed-time poll that re-validates the canonical review artifact directly:");
  lines.push("end=$((SECONDS + " + WAIT_BOUND_SEC + ")); while true; do " + PY + " " + ROOT + "/scripts/review_ready.py " + seg + " --expect-token " + dispatchToken + " && exit 0;" + failFast + " [ $SECONDS -ge $end ] && break; slp=$((end-SECONDS)); [ $slp -gt 20 ] && slp=20; [ $slp -gt 0 ] && sleep $slp; done; exit 1");
  lines.push("If that command exits 0 (review_ready.py confirmed the canonical review artifact for this run and round), return exactly the line: READY " + seg);
  lines.push("Otherwise (the elapsed-time bound was reached, or the job's fail sentinel appeared), return exactly the line: TIMEOUT " + seg);
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

// #198 -- the Workflow's AUTHORITATIVE independent wait gate for the
// translate dispatch above. The codex translator runs in a DETACHED
// codex_job.py job; this poll re-validates the CANONICAL draft directly
// (never trusts any driver-written file). ACCEPT = draft_ready.py <seg>
// --expect-token <tok> exit 0 (token + delivery -- so an old-run straggler
// translator's draft with a stale token is never accepted) AND
// validate_draft.py <seg> prints OK (the six quality checks -- so a
// structurally-complete but content-defective draft is REJECTED). Elapsed-
// time loop, gate-then-deadline-break, NO separate post-loop gate. The
// optional fail-fast (only when disp is non-empty) is a pure presence check
// on the DISP-named sentinel, evaluated ONLY AFTER the ACCEPT gate did not
// pass this iteration (a valid canonical always wins; an empty disp disables
// it -- safe degradation). No external `timeout` binary.
function waitPrompt(seg, disp) {
  const dispatchToken = RUN_ID + ":" + seg;
  const failFast = disp
    ? " [ -f \"" + ROOT + "/segments/.codex_failed." + seg + "." + disp + "\" ] && exit 1;"
    : "";
  const lines = [];
  lines.push("The codex translator for segment " + seg + " is running in a DETACHED background job (launched by codex_job.py). Wait for it by running EXACTLY ONE bash command -- an elapsed-time poll that re-validates the canonical draft directly:");
  lines.push("end=$((SECONDS + " + WAIT_BOUND_SEC + ")); while true; do " + PY + " " + ROOT + "/scripts/draft_ready.py " + seg + " --expect-token " + dispatchToken + " && " + PY + " " + ROOT + "/scripts/validate_draft.py " + seg + " && exit 0;" + failFast + " [ $SECONDS -ge $end ] && break; slp=$((end-SECONDS)); [ $slp -gt 20 ] && slp=20; [ $slp -gt 0 ] && sleep $slp; done; exit 1");
  lines.push("If that command exits 0 (the canonical draft passed both draft_ready.py --expect-token and validate_draft.py), return exactly the line: READY " + seg);
  lines.push("Otherwise (the elapsed-time bound was reached, or the job's fail sentinel appeared), return exactly the line: TIMEOUT " + seg);
  lines.push("Do nothing else -- do not touch any files, do not translate anything yourself, and do not read the draft.");
  return lines.join("\n");
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
function fixPrompt(seg, round, revObj) {
  const lines = [];
  lines.push("Effort: " + EFFORT + ". You are the Claude editor applying review findings to segment " + seg + ", round " + round + ".");
  lines.push("Read " + ROOT + "/segments/" + seg + ".review.json -- this is the AUTHORITATIVE source of the reviewer's findings for this round. review_ready.py already confirmed, before this fix call was ever dispatched, that this exact file is fresh (its dispatch_token matches this run and round) -- so this read is race-free. Apply every entry in its findings[] array, in full, to the draft.");
  lines.push("Important: only codex translates. If the draft is missing or is not actually ready -- check by running " + PY + " " + ROOT + "/scripts/draft_ready.py " + seg + " -- do not translate it yourself: return exactly the line DRAFT_MISSING " + seg + " and write nothing.");
  lines.push("Otherwise, read " + ROOT + "/segments/" + seg + ".draft.json and " + ROOT + "/segments/segpack_" + seg + ".json, and carefully apply every finding from " + ROOT + "/segments/" + seg + ".review.json to the draft. Never touch a placeholder sentinel (e.g. ⟦FNREF_...⟧, ⟦VERSE_...⟧) -- copy each one byte for byte in place. Keep the verse policy: " + VERSE_POLICY_INSTRUCTION_BLOCK);
  lines.push("Never change the set of block, footnote, or verse keys -- they must stay exactly 1:1 with the segpack.");
  lines.push("The draft also carries a dispatch_token top-level field -- copy its existing value byte for byte into your rewritten draft, unchanged; never invent, drop, or recompute it.");
  lines.push("Rewrite " + ROOT + "/segments/" + seg + ".draft.json with your fixes. Then run " + PY + " " + ROOT + "/scripts/validate_draft.py " + seg + " and confirm it prints OK -- if your own edit broke coverage or a placeholder, repair it and rewrite the file again until it prints OK.");
  lines.push("Return exactly the line: FIXED " + seg + " r" + round);
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
// canonical artifact the bounded poll below re-validates. TIMEOUT ends the
// point immediately as blocked/review-timeout -- no read/check is attempted
// against an artifact that may still be mid-write.
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
  const disp = await callReviewDispatch(seg, roundLabel);

  const waitLabel = "review-wait:" + seg + ":r" + roundLabel;
  const ready = await agent(reviewWaitPrompt(seg, roundLabel, disp), {
    effort: "low", phase: "ReviewFix", label: waitLabel,
  });
  // Line-oriented match via sentinelVerdict (#308), never a whole-string
  // exact compare: the reply's LAST trimmed non-empty line must equal
  // "READY <seg>" exactly, and no line anywhere may equal "TIMEOUT <seg>".
  // This keeps BOTH directions closed. #228's direction: a timeout reply
  // like "TIMEOUT seg01 (not READY)" contains the literal substring "READY"
  // but its one line matches neither sentinel exactly, so it still blocks
  // (never a `.indexOf("READY") === -1` substring check). #308's direction:
  // a benign prose-decorated success ("The poll confirmed the review
  // artifact is ready (exit 0).\n\nREADY seg03") no longer misses the OLD
  // whole-string `String(x).trim() !== "READY " + seg` check just because
  // of the preamble -- reviewWaitPrompt still instructs the agent to return
  // exactly "READY <seg>"/"TIMEOUT <seg>", this only tolerates decoration
  // around it.
  if (!sentinelVerdict(ready, "READY " + seg, "TIMEOUT " + seg)) {
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
  if (!fx || sentinelVerdict(fx, "DRAFT_MISSING " + seg, null)) {
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
  const ready = await agent(waitPrompt(seg, disp), { effort: "low", phase: "ReviewFix", label: "wait:" + seg });
  // Line-oriented match via sentinelVerdict (#308), never a whole-string
  // exact compare: the reply's LAST trimmed non-empty line must equal
  // "READY <seg>" exactly, and no line anywhere may equal "TIMEOUT <seg>".
  // This keeps BOTH directions closed. #228's direction: a timeout reply
  // like "TIMEOUT seg01 (not READY)" contains the literal substring "READY"
  // but its one line matches neither sentinel exactly, so it still blocks
  // (never a `.indexOf("READY") === -1` substring check) -- this is the
  // worst of the five sites (#228): a false pass here sends the ENTIRE
  // review/fix cycle over a draft that never actually finished translating,
  // and the "we'll pick it back up next run" safety net never fires because
  // nothing here is recorded as recoverable. #308's direction: a benign
  // prose-decorated success no longer misses the OLD whole-string check
  // just because of a preamble -- waitPrompt still instructs the agent to
  // return exactly "READY <seg>"/"TIMEOUT <seg>", this only tolerates
  // decoration around it.
  if (!sentinelVerdict(ready, "READY " + seg, "TIMEOUT " + seg)) {
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
// Per segment: 3 fixed calls (in_progress ledger write, translate dispatch,
// translate wait) + up to MAXFIX normal rounds at 7 calls each (a review
// point's 6-call worst case plus 1 fix call) + 1 mandatory final review
// point (6 calls, no fix dispatched) + 1 terminal ledger write
// = 3 + 7*MAXFIX + 6 + 1 = 10 + 7*MAXFIX. Batch-level: 1 (the final
// merge-ledger completeness check; there is no batch pre-clean call).
//
// #131's draftPresentAndValid probe does NOT change this formula: it fires
// only from runRound's fix-call-failed terminal branch, which ENDS the
// segment right there -- strictly shorter than the worst-case path this
// formula already sizes against (a full MAXFIX rounds then the final
// review), so the ceiling this preflight enforces stays sound.
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
