# Orchestration and batching

This file covers how a run actually gets dispatched: the W1–W8 pipeline shape,
why dispatch is a Workflow-tool `pipeline()` call and never named-teammate
`Agent()` fan-out, the exact per-segment loop as it executes inside that
pipeline, how the prompt functions are generated, and the `batch_agent_cap`
preflight estimator that can refuse to start an oversized batch. It is the
orchestration-mechanics counterpart to `references/engine-loop.md` (which
owns the *rules* the loop enforces) and `references/ledger-and-resumability.md`
(which owns the ledger writes and `select_segments.py` classification the
dispatch decision is built on) — read those two alongside this one; this file
does not repeat their content, only cross-references it.

## Why Workflow `pipeline()` dispatch, never named-teammate agents

This is a hard rule, not a style preference. The source project's own real
incident is the reason: **11 named teammates, each nesting its own
schema-less `codex:codex-rescue` review call inside its own turn, silently
wedged for roughly 10 real hours with zero ambient monitoring** — nothing
failed loudly, nothing timed out visibly, the run simply stopped making
progress. The same review step, hoisted to a workflow-level
`agent(..., { schema: REVIEW_SCHEMA })` call instead, worked reliably across
28+ segments.

The reason is stronger than "nested calls are riskier": a raw
`Agent(subagent_type: "codex:codex-rescue")` call is unreliable at returning a
real structured verdict **regardless of who calls it or whether it's
nested** — even a top-level, non-nested call can return the same ambiguous
background-job string instead of a verdict. Only a **Workflow-tool `agent()`
call carrying a `schema` param** has automatic retry-until-valid built into
the harness (it forces a StructuredOutput tool call; a call that returns an
ambiguous string instead of the schema fails validation and the model is
forced to retry). That is why every codex accuracy-bearing call in this
plugin — review and glossary-pass batches — is a workflow-level `agent()`
call with a `schema` param, never a prompt telling some other agent to itself
go call codex-rescue.

Concretely, this means:

- `mass-translate-wf.template.js` and `glossary-pass-wf.template.js` are
  **Workflow scripts**, run via the Workflow tool's own `pipeline()`/`agent()`
  globals — not dispatched as a set of named teammates coordinated over
  `SendMessage`.
- `reviewFixLoop()` (the per-segment review/fix loop, below) is a **plain
  async function called from top-level `pipeline()`**, never a prompt that
  asks a sub-agent to itself invoke `codex:codex-rescue`.
- One segment's dispatch never depends on another segment's teammate having
  finished, polled, or reported idle — `pipeline()`'s own concurrency across
  segments replaces that coordination entirely, with no shared mutable state
  between segments except through the ledger fragment files (see
  `references/ledger-and-resumability.md`).

## The per-run pipeline — W1 through W8

One profile.yml drives one project through eight named workflow stages,
walked in order (full field-by-field detail for each is in `SKILL.md`; this
is the orchestration-level summary of what each stage hands to the next):

- **W1 Scaffold** — fill in every placeholder Step 0/0a already copied into
  place. Gated by `scripts/scaffold_validate.py` before W2 can start: it
  fatally rejects `LT_PLACEHOLDER_UNFILLED` inside any
  `LT_REQUIRED_FILL_BEGIN`/`LT_REQUIRED_FILL_END` marker span, and separately
  rejects `translate_TASK.md`/`review_TASK.md` if the shipped
  `guéridon=refrain-song` trap example survived into a real project.
- **W2 Extract** — run the adapted `extract.py`, producing `manifest.json`;
  its own blocking self-checks (bijection, coverage, spine-order,
  `no_segment_exceeds_max_words`, etc.) must be green before anything
  downstream runs. The final `manifest.json` also passes
  `manifest.schema.json` validation with `jsonschema.Draft202012Validator`
  immediately after extraction.
- **W3 Bootstrap** — style bible by hand/interview, plus the mandatory
  language smoke test, plus the codex glossary-pass (its own, smaller
  Workflow pipeline — see below) that freezes `canon.json`.
- **W3a Segpack generation** — `segpack.py` over every candidate segment in
  `manifest.json`'s `segments[]`, body and translate-decision `FRONTBACK:{id}`
  elements alike, now that canon exists; a missing/invalid segpack for any
  candidate is a FATAL preflight error here, never discovered mid-dispatch
  later.
- **W4 Stress-gate** — run the full per-segment loop, below, on the
  highest-risk segment actually available in this book (longest body segment
  plus whichever of footnotes/verse/front-back-translate are present) before
  trusting the mechanism at batch scale. If the book genuinely has neither
  verse nor footnotes, record that fact in `PLAN.md` or a ledger note and
  stress-test the longest body segment alone.
- **W5 Mass-translate** — the main event: `select_segments.py` classifies
  every candidate segment and emits `SEGS`, the batch-size preflight
  estimates the worst-case agent-call count against `engine.batch_agent_cap`,
  then (if under cap) `mass-translate-wf.template.js`'s `pipeline()` call
  runs the per-segment loop over every ID in `SEGS`. This section and the two
  below it are this file's main subject.
- **W6 Consistency pass** — a lightweight, hand-maintained
  `consistency_issues.md` tracker between batches; never automated, never
  read back in programmatically.
- **W7 Final audit** — `scripts/final_audit.py`'s hard checks plus WARN-only
  advisory checks over every converged segment, plus a whole-project
  completeness gate (one final `select_segments.py` invocation with no
  `--only-segs` restriction). `project_complete: true` only when every
  `manifest.json` segment classifies `reusable`; the frontback coverage report
  is advisory only, and this frontback-through-segment-loop treatment is new
  plugin hardening, not source-proven.
- **W8 Deliver** — report convergence stats, list any `blocked` or
  `non_converged` segments explicitly, and surface W7's per-category
  whole-project completeness counts alongside `project_complete`; assembling
  drafts into one distributable book file is out of scope for v1
  (`output.v1_scope: segment_drafts_and_audit`).

Only W3's glossary-pass and W5's mass-translate are themselves Workflow
`pipeline()` calls; the rest are scripts or hand-driven steps the
orchestrating session runs directly.

## W5: `select_segments.py` preflight, then `pipeline()`

Before `pipeline()` is called, `scripts/select_segments.py` runs and emits
`SEGS` — the exact array of segment IDs this batch will dispatch. By default
that is `not_started ∪ recoverable ∪ stale`, excluding `reusable`,
`human_escalation`, and `blocked_needs_regeneration`; an operator-supplied
`--only-segs` list intersects that set, and also acts as the sole explicit
override that can retry a named `human_escalation` segment by forcing it into
`SEGS` despite its classification. `--only-segs` fatally rejects unrecognized
IDs and fatally rejects an empty emitted `SEGS` unless `--allow-empty` is also
passed. The full classification rules and the six classification categories
are `select_segments.py`'s own subject — see
`references/ledger-and-resumability.md` and `SKILL.md`'s W5 section for the
complete spec. This file only needs the orchestration fact: **the emitted
`SEGS` is the same array both the batch-size estimator below sizes its
estimate against and `pipeline()` dispatches over** — never a separately
hand-typed or re-derived list, and the same array becomes `mergeLedgerPrompt`'s
`--expected-segs` argument at the end of the run.

`mass-translate-wf.template.js` is instantiated **fresh from the plugin's
current copy every run** — never a stale generated copy reused across runs.
`${durable_root}/runs/.plugin_bundle_hash` (computed by Step 0a) covers this
template specifically, so a plugin update is never silently masked by an old
generated script surviving in place.

Bundle membership stays split exactly as follows: `plugin_bundle_hash` gates
cache reuse and covers `validate_draft.py`, `canon_validate.py`,
`cache_key.py`, `draft_sha1.py`, `review_artifact_check.py`,
`ledger_update.py`, plus `mass-translate-wf.template.js` and
`glossary-pass-wf.template.js`; `orchestration_bundle_hash` is diagnostic
only, never part of the composite cache key, and covers exactly
`draft_ready.py`, `ledger_merge.py`, `language_smoke_report.py`, and
`select_segments.py`; `derivation_bundle_hash` covers exactly
`bootstrap_names.py` and `segpack.py` and is the cache-key field that drives
the `blocked_needs_regeneration` treatment.

## Structural properties preserved exactly from the proven reference

`mass-translate-wf.template.js` is generalized from the real, proven
`historiettes-t3/reference/historiettes-mass-translate-wf.reference.js` —
read that file directly for ground truth on structure. These properties are
preserved exactly because they are precisely what made the original reliable:

- **Self-contained, no imports.** The template uses only the Workflow tool's
  provided globals (`agent()`, `pipeline()`, `log()`, `args`) plus `python3`
  shelled out via agent prompts for the deterministic gate — zero
  `import`/`require` statements, matching the proven script exactly. Workflow
  scripts in this execution model can't reliably load external JSON/JS
  modules, so every schema is an **inline literal object**, declared
  **above** the `pipeline()` call — a schema declared after its first use
  silently no-ops due to temporal-dead-zone semantics in this execution
  model (`gotcha_workflow_const_tdz_silent_fail`). This applies to
  `REVIEW_SCHEMA`, `REVIEW_ARTIFACT_SCHEMA`, `LEDGER_WRITE_SCHEMA`, and
  `LEDGER_MERGE_SCHEMA` in `mass-translate-wf.template.js`, and to
  `CANON_BATCH_SCHEMA` in `glossary-pass-wf.template.js`.
- Every `agent()` call carries `phase`/`label` metadata (pure logging,
  non-load-bearing — e.g. `phase: 'Translate'`, `label: 'translate:${seg}'`).
  The file exports a top-level `meta = { name, description, phases }` object.
  Both details are real in the proven reference and kept for parity, but
  neither is load-bearing for correctness.
- The inline schema literals match their shipped schemas exactly:
  `REVIEW_SCHEMA` has `{clean, coverage_ok, findings[{loc, severity, issue,
  suggest}], draft_sha1}` with `additionalProperties:false` and no
  `verse_status`; `REVIEW_ARTIFACT_SCHEMA` is a
  `oneOf` of `{match:true}` or `{match:false, mismatch_detail}`;
  `LEDGER_WRITE_SCHEMA` is a `oneOf` of `{success:true, status,
  fragment_path, fragment_sha1}` or `{success:false, error, exit_code?,
  stderr?}`; `LEDGER_MERGE_SCHEMA` is a `oneOf` of `{success:true,
  ledger_path, n_segments, missing_segments: [], stale_segments}` or
  `{success:false, error, missing_segments?, exit_code?, stderr?}`. Each
  branch is `additionalProperties:false`.

## The exact per-segment loop: translate → readiness-poll → review/fix loop → confirming final review

This is the sequence `pipeline()` runs, once per segment ID in `SEGS`, with
`pipeline()`'s own concurrency handling how many segments run at once. The
call shape is a genuine **two-stage `pipeline()` call**, matching the proven
reference script exactly in structure (source/target-language literals and
paths generalized to profile substitutions):

```js
const results = await pipeline(
  SEGS,
  (seg) => agent(translatePrompt(seg), {
    agentType: 'codex:codex-rescue', effort: 'high', phase: 'Translate', label: `translate:${seg}`,
  }),
  (_translateResult, seg) => reviewFixLoop(seg),
)
```

Stage 1 is the translate call itself; stage 2 is `reviewFixLoop(seg)` — a
plain async function, not another `agent()` call — which runs the
readiness-poll and the review/fix loop described below and returns this
segment's final structured result. `pipeline()` feeds stage 1's per-segment
result into stage 2 alongside `seg`, but `reviewFixLoop` only ever uses `seg`
— stage 1's return value is fire-and-forget by design (the translator prompt
instructs the agent to self-validate coverage before returning; nothing in
stage 2 depends on stage 1's own return string).

1. **Translate.** `agent(translatePrompt(seg), { agentType: 'codex:codex-rescue', effort: 'high' })`
   — fire-and-forget; the translator prompt itself instructs the agent to
   self-validate coverage via the deterministic gate
   (`validate_draft.py`) before returning. This is the one deliberate
   exception to the "codex accuracy calls need a schema" rule (R7 in
   `references/engine-loop.md`): the translate call is intentionally
   schema-less, gated instead by file output plus
   `draft_ready.py`/`validate_draft.py` — see
   `references/false-green-gate.md`.
2. **Readiness poll.** A low-effort wait/poll step (`draft_ready.py` in a
   bash polling loop, `effort: 'low'`) blocks the review loop from starting
   until the async translator has actually delivered a complete file. This
   specifically prevents a Claude fix-agent from ever ending up authoring a
   missing translation, since "codex only translates" would otherwise be
   silently violated the moment a fix step ran against a
   nonexistent/partial draft. On timeout, this branch returns
   `{ seg, converged: false, reason: 'translate-timeout' }` and the loop
   never reaches a review call at all for this segment.
3. **Review/fix loop**, up to `engine.max_fix_rounds` rounds of review → fix
   → re-review, exiting early the moment a review reports
   `clean && coverage_ok`:
   - **Review call** — schema-validated, workflow-level,
     `agentType: 'codex:codex-rescue'`, `effort: 'high'`, schema
     `REVIEW_SCHEMA` (see `references/workflow-schema-validation.md` for the
     exact shape).
   - **Null-review handling** (a deliberate plan enhancement, absent from
     the proven reference, which blocks on the first null with no retry):
     if the review call returns null, retry that SAME call once, fresh; if
     the retry also returns null, exit immediately as `blocked` with reason
     `review-null` rather than consuming a fix round.
   - **Review-artifact gate**, right after a non-null verdict:
     `agent(verifyReviewArtifactPrompt(seg, revObj), { effort: 'low', schema: REVIEW_ARTIFACT_SCHEMA })`
     verifies the on-disk `review_path(seg)` byte-matches the `revObj` the
     review call actually returned, via
     `scripts/review_artifact_check.py`'s deterministic comparison. On
     `match: false`, retry the original review call once, fresh, and re-run
     the same check against the retry's write; if it still mismatches, exit
     as `blocked` with reason `review-artifact-mismatch`. Full mechanics and
     what this gate does and doesn't protect (it no longer gates the fix
     step's own input, only the ledger-binding/audit-trail question) are in
     `references/engine-loop.md` and `references/ledger-and-resumability.md`.
   - **Fix call**, only on `match: true`:
     `agent(fixPrompt(seg, round, revObj), { effort: 'high' })` — no
     `agentType` field, keeping it on plain Claude. `fixPrompt` receives
     `revObj` directly (the same schema-validated object the review call
     already returned this round, still in memory) rather than reading
     `review_path(seg)` back off disk — a deliberate, documented departure
     from the proven reference's 2-argument `fixPrompt(seg, round)` shape.
     See `references/engine-loop.md` R1 for the full reasoning.
4. **Confirming final review.** Always one final confirming review after the
   round cap, even if the loop exited because of the cap rather than
   convergence — a fix that goes unverified is the single most common source
   of a silently-broken "done" segment. This final review carries its own
   review-artifact-gate call too (the gate fires after every non-null
   verdict, including the final confirming one).
5. **Result.** Ordinary translate/review non-convergence returns a structured
   `{ seg, converged: false, reason, rounds, lastFindings }` object — never
   throws, never silently marks done. `reason` is one of
   `translate-timeout`, `review-null`, `draft-missing`,
   `review-artifact-mismatch`, or `cap` (non-converged after the final
   confirming review). Ledger-write failures are surfaced through the Workflow
   result instead of being written back through the same ledger channel:
   `success:false` from `recordLedgerPrompt` returns
   `{ seg, converged: false, reason: 'ledger-write-failed', detail: <error> }`,
   while the JS-side fragment/status payload-intent mismatch returns
   `reason: 'ledger-write-mismatch'`.

No sub-chunking exists anywhere in this loop in v1 — `mass-translate-wf.template.js`
operates only on whole `seg` items; a segment whose `word_count` exceeds
`max_segment_words` is caught at W2 extraction, never here (see `SKILL.md`
W4).

## Prompt functions — generated from the profile at instantiation time

`mass-translate-wf.template.js` defines seven prompt functions:
`translatePrompt`, `reviewPrompt`, `waitPrompt`, `fixPrompt`,
`recordLedgerPrompt`, `mergeLedgerPrompt`, `verifyReviewArtifactPrompt`.
`glossary-pass-wf.template.js` defines its own, smaller set including
`glossaryPrompt`.

**There is no templating engine at Workflow-runtime.** Every prompt function
is plain JavaScript string interpolation against constants the orchestrating
session substitutes once, at the moment it instantiates the template file
from the plugin's shipped copy — before the Workflow tool ever executes it.
The template documents its own substitution tokens explicitly:
`{{SOURCE_LANG}}`, `{{TARGET_LANG}}`, `{{DURABLE_ROOT}}`,
`{{VERSE_POLICY_INSTRUCTION_BLOCK}}`, `{{MAX_FIX_ROUNDS}}`, and (glossary-pass
template only) `{{RESEARCH_MODE}}`. `{{VERSE_POLICY_INSTRUCTION_BLOCK}}` in
particular is read fresh from the CURRENT `profile.yml` every time a run is
scaffolded — never spliced into `translate_TASK.md`/`review_TASK.md`
directly — which is what keeps it staleness-immune when `verse_policy.mode`
changes between runs (see `references/verse-policy.md`).

Because substitution happens once at instantiation time and never again at
runtime, a leftover `{{...}}` token in the generated script is a hard bug,
not a cosmetic one — it means a substitution the instantiation step should
have performed didn't happen. `tests/workflow_template_instantiation.test.py`
instantiates both templates against a fixture profile and greps the output
for the literal substring `{{`, asserting zero matches; the glossary-pass
case runs twice, once per `research_mode` value, to prove
`{{RESEARCH_MODE}}` resolves correctly in both directions.

**Storage location (pinned):** the instantiated script is written to
`${durable_root}/runs/workflows/<run_id>/mass-translate-wf.js` (and
`glossary-pass-wf.js` for the glossary pass), where `run_id` is a fresh,
sortable identifier (an ISO-8601-ish timestamp works) generated once per
invocation — the same ID a later resumed run's `resumeFromRunId` would refer
back to. `runs/workflows/` is created by Step 0a. The full path is logged in
W8's status output.

## `batch_agent_cap` — the worst-case preflight estimator

Before `pipeline()` is ever called, the workflow template computes a
worst-case estimate of how many total `agent()` calls this batch could make,
and refuses to start if that estimate exceeds `engine.batch_agent_cap`
(`profile.yml`'s `engine.batch_agent_cap: 1000` in the shipped example — see
`assets/profile.example.yml`). **This estimator is new plugin hardening, not
itself source-proven** — the real reference script has no such check
anywhere; it simply pipelines whatever `SEGS` it's given. Treat it with the
same "carefully designed, unproven at scale" confidence
`references/ledger-and-resumability.md` already applies to the ledger
subsystem, pending a first real pilot run.

The formula comes from enumerating every mutually-exclusive per-segment
branch and taking the true worst case, not from padding a flat guess:

- **Every segment, unconditionally:** 1 `in_progress` ledger write + 1
  translate call + 1 wait/poll call = **3 fixed calls**, before any branch is
  even reached.
- **Timeout branch:** no review call ever happens on this path; +1 ledger
  write. Branch total: `3 + 1 = 4`.
- **Blocked branch:** up to `max_fix_rounds - 1` completed NORMAL rounds,
  each **3 calls** (review + artifact-check + fix — a normal round's review
  is non-null by definition, since a null review diverts into the
  terminating sub-case below instead), then a terminating round whose call
  count depends on which of three mutually exclusive sub-cases fires:
  - `review-null` (after one retry): 2 calls — neither the original nor the
    retry triggers the artifact-check, since that gate only fires after a
    non-null verdict.
  - `draft-missing`: 3 calls — a non-null review + its artifact-check
    (matching) + a fix call that hits `DRAFT_MISSING`.
  - `review-artifact-mismatch`: 4 calls — a non-null review + its
    artifact-check reporting `match: false` + a fresh retry of the original
    review call + the retry's own artifact-check, which STILL reports
    `match: false`. No fix call ever dispatches on this path. This is the
    largest of the three terminating sub-cases.
  Terminating-round maximum: 4 calls. Branch total:
  `3 + 3*(max_fix_rounds - 1) + 4 + 1 = 3*max_fix_rounds + 5`.
- **Converged / non-converged-at-cap branch:** the full `max_fix_rounds`
  rounds of review + artifact-check + fix (3 calls per round, no early clean
  exit), then one final confirming review plus its own artifact-check (2
  calls — the gate fires after every non-null verdict, including the final
  confirming one), then 1 ledger write. Branch total:
  `3 + 3*max_fix_rounds + 2 + 1 = 3*max_fix_rounds + 6` — **this is the true
  per-segment maximum across all branches** (the blocked branch's own worst
  sub-case is always exactly one call lower, since whichever way a blocked
  path terminates it substitutes for, rather than adds to, the converged
  branch's final confirming-review-plus-artifact-check pair).

```
estimatedCalls = 1 + SEGS.length * (6 + 3 * maxFixRounds)
```

The leading `+1` is the one mandatory, batch-level (not per-segment)
`mergeLedgerPrompt` call every batch makes exactly once before returning.

If `estimatedCalls > engine.batch_agent_cap`: `log()` the estimate and the
segment count, then return immediately with

```js
{ converged: [], failed: [], reason: 'batch-too-large', estimatedCalls, cap: engine.batch_agent_cap }
```

`pipeline()` never runs in this case. **Splitting an oversized batch into
smaller ones is the operator's decision in v1, not automated** — nothing in
this plugin auto-shrinks `SEGS` or auto-paginates a run; the operator either
lowers the batch via `select_segments.py --only-segs <comma-list>` (see
`references/ledger-and-resumability.md`) or raises `engine.batch_agent_cap`
in `profile.yml` if that ceiling was simply set too conservatively for this
project.

`engine.batch_agent_cap` is a pure orchestration/scheduling knob with no
effect on translation output — it is deliberately excluded from
`agent_config_hash` (only `effort`/`max_fix_rounds` are hashed), so changing
the cap alone never re-invalidates an already-converged segment's cache key.
See `references/ledger-and-resumability.md` for the full cache-key
membership list.

## The glossary-pass template — a second, smaller `pipeline()` call

`glossary-pass-wf.template.js` runs once during W3, bootstrap, before the
mass-translate template ever runs at W5. **Labeled explicitly: new
hardening, not itself source-proven.** The real project ran its glossary
pass as ad hoc `glossary/TASK.md` plus codex batches producing
`glossary/out_*.json` — not a schema-validated Workflow script. This
template applies the proven review-loop *mechanics* (schema-validated,
workflow-level `agent()` calls) to a new context by analogy — sound
engineering, but not "this exact script ran on a real project." A first real
project should pilot this template on one small batch and manually verify
its `canon.json` merge output before treating it as fully load-bearing, the
same stress-gate discipline W4 applies to translation.

Structurally, it is a single-stage `pipeline()` call over batches of
candidate name/term forms, not per-segment IDs:

```js
pipeline(batches, (batch) => agent(glossaryPrompt(batch), {
  agentType: 'codex:codex-rescue', effort: 'high', schema: CANON_BATCH_SCHEMA,
}))
```

Every batch call is workflow-level and schema-validated, never raw or
nested — the same discipline as the review call above, for the same reason.
`CANON_BATCH_SCHEMA` is declared above the `pipeline()` call, for the
identical temporal-dead-zone reason as `mass-translate-wf.template.js`'s own
schemas.

The batch output is not just accepted on trust: each validated array result is
merged into `canon.json` with dedup + collision checking and then routed by
`disposition` (`entries{}` for accepted items, `review_queue` for queued
items), and no `canon_hash` field exists anywhere.
`scripts/canon_validate.py` re-validates every merged item against
`canon-batch.schema.json` with `format_checker=jsonschema.FormatChecker()`,
then re-reads the whole written `canon.json` and validates it against
`canon-file.schema.json`, including required
`generation_hashes.particle_config_hash` and `.derivation_bundle_hash`.
`canon_validate.py --research-mode live|offline` is required, never defaulted;
`offline` fatally rejects any merged `basis:"established"` entry. Batch
construction also excludes every `source_form` already present in the current
`canon.json`'s `entries{}`.

## Ledger writes stay orchestration-adjacent, not orchestration-owned

Every per-segment ledger-fragment write goes through the schema-validated,
low-effort `agent(recordLedgerPrompt(seg, fields), {effort:'low', schema:
LEDGER_WRITE_SCHEMA})`; no fragment write happens any other way. It is called
from five distinct points inside the per-segment loop above (before translate
dispatch, on wait timeout, and for each of the three JS-decided terminal
outcomes). Immediately after any `success:true` ledger-write return, the
Workflow JS itself compares the returned `fragment_path`'s segment-ID component
against `seg` and the returned `status` against `fields.status`; a mismatch is
handled as `reason:'ledger-write-mismatch'`, never retried through the same
ledger-write channel. `mergeLedgerPrompt` is called once at the end of the
whole batch as `agent(mergeLedgerPrompt({expectedSegs: SEGS}), {effort:'low',
schema: LEDGER_MERGE_SCHEMA})`, using the same `SEGS` array
`select_segments.py` emitted, and the batch is not complete until that
mandatory completeness check passes. The schemas, exact payload shapes, and why
`pipeline()`'s per-segment
concurrency rules out a single shared read-modify-write of one `ledger.json`
are `references/ledger-and-resumability.md`'s subject in full.
