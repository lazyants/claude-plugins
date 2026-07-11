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
  `LT_REQUIRED_FILL_BEGIN`/`LT_REQUIRED_FILL_END` marker span; separately
  rejects any of the six copied files still carrying an unfilled inline
  bracket placeholder (`[SOURCE LANGUAGE]`, etc.); and separately rejects
  `translate_TASK.md`/`review_TASK.md` if the shipped era/domain trap
  example survived into a real project (an exact-substring match plus a
  co-occurrence check that also catches a mangled or partially-deleted
  survivor).
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
`ledger_update.py`, `review_ready.py`, `resume_setup.py`, plus
`mass-translate-wf.template.js` and `glossary-pass-wf.template.js`.
`review_ready.py` and `resume_setup.py` (both new in 1.2.0) are correctness-
determining in the same sense as `review_artifact_check.py`/`ledger_update.py`
— a bug in either could certify a stale or wrongly-scoped artifact as safe to
consume, or wrongly permit/refuse a resume — so both are gating members, not
diagnostic-only, unlike their sibling readiness/merge scripts below.
`orchestration_bundle_hash` is diagnostic only, never part of the composite
cache key, and covers exactly `draft_ready.py`, `ledger_merge.py`,
`language_smoke_report.py`, and `select_segments.py`; `derivation_bundle_hash`
covers exactly `bootstrap_names.py` and `segpack.py` and is the cache-key
field that drives the `blocked_needs_regeneration` treatment. See
`references/ledger-and-resumability.md` for the full three-bundle-hash
membership table (the authoritative restatement site) and the
resume-integrity digest that reads both `plugin_bundle_hash` and
`orchestration_bundle_hash` as version inputs.

## Structural properties preserved exactly from the proven reference

`mass-translate-wf.template.js` is generalized from the real, proven
`historiettes-t3/reference/historiettes-mass-translate-wf.reference.js`.
These properties are preserved exactly because they are precisely what
made the original reliable:

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
  `CANON_VERIFY_SCHEMA` in `glossary-pass-wf.template.js`.
- Every `agent()` call carries `phase`/`label` metadata (pure logging,
  non-load-bearing — e.g. `phase: 'Translate'`, `label: 'translate:${seg}'`).
  The file exports a top-level `meta = { name, description, phases }` object.
  Both details are real in the proven reference and kept for parity, but
  neither is load-bearing for correctness.
- **Every agent-facing schema literal is top-level `type:"object"`, with no
  top-level `oneOf`/`allOf`/`anyOf`** (the `#87` fix — see
  `references/workflow-schema-validation.md` for the full flat shapes,
  the reasoning, and the exact-key-set JS guards that re-establish branch
  discrimination on the *agent-relayed* object). Do not restate the exact
  field lists here — that reference file is the single authoritative site;
  this file only needs the orchestration-level fact that they are flat.

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
   — fire-and-forget, the DISPATCH half of the shared codex work-call
   pattern (`references/workflow-schema-validation.md`); the translator
   prompt writes `draft_path(seg) = segments/{seg}.draft.json` carrying a
   `dispatch_token = <RUN_ID>:<seg>` metadata field and instructs the agent
   to self-validate coverage via the deterministic gate
   (`validate_draft.py`) before returning. This is the one deliberate
   exception to the "codex accuracy calls need a schema" framing (R7 in
   `references/engine-loop.md`): the translate call is intentionally
   schema-less, gated instead by file output plus
   `draft_ready.py`/`validate_draft.py` — see
   `references/false-green-gate.md`.
2. **Readiness poll.** A low-effort wait/poll step (`draft_ready.py
   --expect-token <RUN_ID:seg>` in a bounded bash polling loop, `effort:
   'low'`) blocks the review loop from starting until the async translator
   has actually delivered a complete, current-run-tokened file. This
   specifically prevents a Claude fix-agent from ever ending up authoring a
   missing translation, since "codex only translates" would otherwise be
   silently violated the moment a fix step ran against a
   nonexistent/partial/stale-run draft. On timeout, this branch returns
   `{ seg, converged: false, reason: 'translate-timeout' }` and the loop
   never reaches a review call at all for this segment.
3. **Review/fix loop**, up to `engine.max_fix_rounds` rounds of review → fix
   → re-review, exiting early the moment a review reports
   `clean && coverage_ok`. Each round's review point is itself the shared
   DISPATCH → WAIT → CONSUME pattern, not one call:
   - **`reviewDispatchPrompt`** — codex, schema-less, fire-and-forget:
     computes `draft_sha1` hash-first, reviews, atomically writes
     `review_path(seg) = segments/{seg}.review.json` carrying
     `dispatch_token = <RUN_ID>:<seg>:r<roundLabel>` (`roundLabel` = the
     round number or `final`), prints `REVIEWED {seg}`.
   - **`reviewWaitPrompt`** — Claude, `effort:'low'`, bounded poll of
     `review_ready.py {seg} --expect-token <RUN_ID:seg:rN>` (`for i in
     $(seq 1 45)` shape, matching translate's own poll). `TIMEOUT` → exit
     immediately as `blocked review-timeout`, no retry.
   - **`readReviewPrompt` + `verifyReviewArtifactPrompt`** — the two CONSUME calls,
     schema-validated (`REVIEW_SCHEMA`, flat `REVIEW_ARTIFACT_SCHEMA`),
     covered under **one shared retry budget**: read → check; on a `null`
     read OR a `match:false` check, retry the SAME `(read, check)` pair
     once, fresh; still failing → `blocked review-null` or `blocked
     review-artifact-mismatch`, whichever triggered it. Full mechanics —
     including why this replaces the old "retry the whole dispatch"
     shape — are in `references/workflow-schema-validation.md` and
     `references/ledger-and-resumability.md`.
   - **Fix call**, only on `match: true`:
     `agent(fixPrompt(seg, round, revObj), { effort: 'high' })` — no
     `agentType` field, keeping it on plain Claude. `fixPrompt` receives
     `revObj` directly (the same schema-validated object `readReviewPrompt`
     already returned this round, still in memory) rather than reading
     `review_path(seg)` back off disk — a deliberate, documented departure
     from the proven reference's 2-argument `fixPrompt(seg, round)` shape.
     See `references/engine-loop.md` R1 for the full reasoning.
4. **Confirming final review.** Always one final confirming review after the
   round cap, even if the loop exited because of the cap rather than
   convergence — a fix that goes unverified is the single most common source
   of a silently-broken "done" segment. This final review point runs the
   identical dispatch → wait → read → check sequence (`roundLabel: 'final'`
   in its `dispatch_token`).
5. **Result.** Ordinary translate/review non-convergence returns a structured
   `{ seg, converged: false, reason, rounds, lastFindings }` object — never
   throws, never silently marks done. `reason` is one of
   `translate-timeout`, `review-timeout`, `review-null`, `draft-missing`,
   `review-artifact-mismatch`, or `cap` (non-converged after the final
   confirming review). Ledger-write failures are surfaced through the Workflow
   result instead of being written back through the same ledger channel:
   `success:false` from `recordLedgerPrompt` returns
   `{ seg, converged: false, reason: 'ledger-write-failed', detail: <error> }`,
   while the JS-side fragment/status payload-intent mismatch returns
   `reason: 'ledger-write-mismatch'`. A `dispatch_token`/sha mismatch at the
   convergence ledger write (see `references/ledger-and-resumability.md`'s
   commit-gate chain) also surfaces as `reason: 'ledger-write-failed'` —
   never recorded `converged`.

No sub-chunking exists anywhere in this loop in v1 — `mass-translate-wf.template.js`
operates only on whole `seg` items; a segment whose `word_count` exceeds
`max_segment_words` is caught at W2 extraction, never here (see `SKILL.md`
W4).

## Prompt functions — generated from the profile at instantiation time

`mass-translate-wf.template.js` defines nine prompt functions:
`translatePrompt`, `waitPrompt`, `reviewDispatchPrompt`,
`reviewWaitPrompt`, `readReviewPrompt`, `verifyReviewArtifactPrompt`, `fixPrompt`,
`recordLedgerPrompt`, `mergeLedgerPrompt`. (`reviewPrompt` — the old,
single, schema-validated review call — no longer exists; the review point
is now four functions, one per DISPATCH/WAIT/CONSUME×2 step, per
`references/workflow-schema-validation.md`. `verifyReviewArtifactPrompt`
keeps its pre-1.2.0 name but is now dispatched as a separate call after
`readReviewPrompt` returns, rather than immediately after the old single
`reviewPrompt` call.) `glossary-pass-wf.template.js` defines its own,
smaller set: `batchDispatchPrompt`, `batchWaitPrompt`, a final-merge prompt,
and `glossaryVerifyPrompt` (`CANON_VERIFY_SCHEMA`) — see
`references/canon-and-glossary.md`.

**There is no templating engine at Workflow-runtime.** Every prompt function
is plain JavaScript string interpolation against constants the orchestrating
session substitutes once, at the moment it instantiates the template file
from the plugin's shipped copy — before the Workflow tool ever executes it.
The template documents its own substitution tokens explicitly:
`{{SOURCE_LANG}}`, `{{TARGET_LANG}}`, `{{DURABLE_ROOT}}`,
`{{VERSE_POLICY_INSTRUCTION_BLOCK}}`, `{{MAX_FIX_ROUNDS}}`,
`{{BATCH_AGENT_CAP}}` (both templates' preflight cost caps — the glossary-pass
template's use is new in 1.3.5), `{{RUN_ID}}`
(new in 1.2.0, both templates — see below), and (glossary-pass template
only) `{{RESEARCH_MODE}}`. `{{VERSE_POLICY_INSTRUCTION_BLOCK}}` in
particular is read fresh from the CURRENT `profile.yml` every time a run is
scaffolded — never spliced into `translate_TASK.md`/`review_TASK.md`
directly — which is what keeps it staleness-immune when `verse_policy.mode`
changes between runs (see `references/verse-policy.md`).

Because substitution happens once at instantiation time and never again at
runtime, a leftover `{{...}}` token in the generated script is a hard bug,
not a cosmetic one — it means a substitution the instantiation step should
have performed didn't happen. `tests/workflow_template_instantiation.test.py`
instantiates both templates against a fixture profile (substituting a stable
fixture value for `{{RUN_ID}}` too) and greps the output for the literal
substring `{{`, asserting zero matches; the glossary-pass case runs twice,
once per `research_mode` value, to prove `{{RESEARCH_MODE}}` resolves
correctly in both directions.

### `{{RUN_ID}}` derivation — a resolve-once, resume-stable contract

**Corrected from the pre-1.2.0 wording, which said "a fresh id per
invocation" — that was true only for a brand-new run.** The orchestrating
session resolves `{{RUN_ID}}` exactly **once**, at instantiation time, as:

```
effectiveRunId = resumeFromRunId  (on a resume whose input_digest MATCHES — see below)
             else a fresh, sortable id
```

The fresh-id case uses a **colon-free** timestamp form, `YYYYMMDDTHHMMSSZ` —
a raw ISO-8601 string with `:` is intentionally rejected, since `:` is
unsafe in some path contexts this ID ends up embedded in. Either way, the
value is validated against a **hardened, path-safe allowlist**:
`^[A-Za-z0-9][A-Za-z0-9._-]*$`, and the whole value must not be `.` or `..`,
and must not contain a `..` substring anywhere (rejecting directory-escape
and dot-segment-collapse tricks). The identical value both names the run
directory (`${durable_root}/runs/workflows/<RUN_ID>/`,
`${durable_root}/glossary/runs/<RUN_ID>/`) and substitutes `{{RUN_ID}}`
inside the instantiated template — so a fresh instantiation and a resumed
one that reuses the same `RUN_ID` produce byte-identical
tokens/paths throughout. `runs/workflows/` is created by Step 0a. The full
path is logged in W8's status output.

**Whether to resume at all is a separate decision from the `RUN_ID` value
itself** — gated by the resume-integrity digest below, never by "a
`resumeFromRunId` was supplied" alone.

### The resume-integrity gate and its digest inputs

Embedding `{{RUN_ID}}` in dispatch prompts closes staleness for the
artifacts that carry it (`draft.json`, `review.json`, glossary fragments —
see `references/ledger-and-resumability.md`), but it does **not** by itself
decide whether resuming is *safe*: `readReviewPrompt`, `verifyReviewArtifactPrompt`,
`fixPrompt`, and the ledger calls never carry `RUN_ID` in their own prompts,
so a fresh `RUN_ID` alone would still let a resumed run replay their cached
results against inputs that changed underneath them. The orchestrating
session closes this at a single pre-workflow choke point instead: before
ever calling `pipeline()`, it computes an `input_digest` and
**create-or-compares** it against `runs/<RUN_ID>/input.digest`.

```
input_digest = sha256(canonical_json({
  kind: "mass" | "glossary",
  args: <the full ordered args this invocation was given>,
  subst: {research_mode, verse_policy, source_lang, target_lang,
          max_fix_rounds, batch_agent_cap},   // resolved profile substitutions
  domain: mass: {seg: <cache_key.py's 15-field composite per seg>}
        | glossary: {glossary_rule, canon_hash},
  version: {plugin_bundle_hash: <runs/.plugin_bundle_hash>,
            orchestration_bundle_hash: <runs/.orchestration_bundle_hash>,
            schemas: <sha of the schemas/ dir>},
}))
```

**MATCH** the prior run's own recorded digest → resume with
`resumeFromRunId` — every digest input is byte-identical, so every cached
result (including the four unscoped-prompt calls above) is provably still
valid. **MISMATCH, or no prior digest** → launch a **fresh run**, a fresh
`RUN_ID`, and explicitly **no** `resumeFromRunId` — reuse nothing. This is
the general principle the digest closes: cover *every* input that can change
a cached agent output, not just the ones a naive "did the source text
change" check would think to cover — a `research_mode: live→offline` flip,
for instance, changes agent policy and `--check-batch` validity without
changing a single hashed content byte, which is exactly why `subst` is a
first-class digest input alongside `args`/`domain`/`version`, not folded
into one of them. `resume_setup.py` (new script, `assets/scripts/`)
implements this: given the run kind, `args`, resolved substitutions, and the
per-seg cache keys / glossary candidates, it computes `input_digest`,
create-or-compares `runs/<RUN_ID>/input.digest`, creates the run
directory/directories, and (glossary only) atomically writes the manifest
files below — aborting (nonzero exit) before any dispatch on any failure.
It emits the resolved `effectiveRunId` and `resume: true|false` as one JSON
line. See `references/ledger-and-resumability.md` for the
per-artifact-consumption token/sha commit-gate chain this digest gate
complements (the digest decides *whether* to resume; the commit-gate chain
polices every individual artifact even when resuming is in principle safe).

## `batch_agent_cap` — the worst-case preflight estimator

Before `pipeline()` is ever called, the workflow template computes a
worst-case estimate of how many total `agent()` calls this batch could make,
and refuses to start if that estimate exceeds `engine.batch_agent_cap`
(`profile.yml`'s `engine.batch_agent_cap: 3500` in the shipped example — see
`assets/profile.example.yml`; 1.3.5 raised this default from 1000, which the
`1 + N*38`-at-`max_fix_rounds:4` formula below made refuse any mass batch over
26 segments — 3500 admits the issue's ~78-segment repro, `1 + 78*38 = 2965`,
with headroom). **This estimator is new plugin hardening, not
itself source-proven** — the real reference script has no such check
anywhere; it simply pipelines whatever `SEGS` it's given. Treat it with the
same "carefully designed, unproven at scale" confidence
`references/ledger-and-resumability.md` already applies to the ledger
subsystem, pending a first real pilot run.

The formula was re-derived for 1.2.0's DISPATCH/WAIT/CONSUME review shape
and the removal of the batch-level pre-clean step (see the resume-integrity
gate above, which makes a pre-clean unnecessary — `{{RUN_ID}}` scoping is
what used to need a clean-slate wipe). It still comes from enumerating every
mutually-exclusive per-segment branch and taking the true worst case, not
from padding a flat guess:

- **A review point, worst case, is exactly 6 calls**: `reviewDispatchPrompt`
  (1) + `reviewWaitPrompt` (1) + the CONSUME pair under its **one shared
  retry budget** — `readReviewPrompt` + `verifyReviewArtifactPrompt` run once, then
  (worst case) the identical pair retried once more = 4 — for
  `1 + 1 + 2×(1 + 1) = 6`. This is a single number now, not a set of
  mutually-exclusive terminating sub-cases: `reviewWaitPrompt` timing out is
  the only way a review point resolves in fewer than 6 calls, and that
  terminates the segment immediately (see below), so it is never the
  binding case for the worst-case estimate.
- **Every segment, unconditionally, before any review point is even
  reached:** 1 `in_progress` ledger write + 1 translate DISPATCH + 1
  translate WAIT = **3 fixed calls**.
- **A NORMAL round** (one that neither converges nor terminates the loop):
  one review point (6) + one fix call (1) = **7 calls**.
- **The final confirming review** (always runs, even after the round cap):
  one review point = **6 calls**, no fix call attached to it.
- **+1 terminal ledger write**, whichever terminal status fires
  (`converged`/`non_converged`/`blocked`).

Per-segment worst case, across every branch (converged-at-cap,
non-converged-at-cap, and blocked-on-the-final-round-before-cap — a
`review-timeout`/`review-null`/`review-artifact-mismatch`/`draft-missing`
block always terminates via a *shorter* path than running every round to
cap, so it is never the binding case):

```
perSegment = 3 (fixed) + 7 * maxFixRounds (normal rounds) + 6 (final review) + 1 (terminal ledger)
           = 10 + 7 * maxFixRounds
```

```
estimatedCalls = 1 + SEGS.length * (10 + 7 * maxFixRounds)
```

The leading `+1` is the one mandatory, **batch-level** (not per-segment)
`mergeLedgerPrompt` call every batch makes exactly once before returning —
unchanged in kind from before, just no longer accompanied by a pre-clean
call (removed; see the resume-integrity gate above).
`tests/batch_size_estimator.test.py`'s mock harness forces a mid-loop
shared-retry (read/check → retry → fix, one full max round) and asserts
**exact** equality to this formula, not `≤`.

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
membership list. **1.3.5:** W3's glossary-pass template reads this SAME
`engine.batch_agent_cap` field, with its own smaller worst-case formula
(`estimatedCalls = 3 * BATCHES.length + 2`) and the same refusal shape — see
the glossary-pass template section below.

## The glossary-pass template — a second, smaller `pipeline()` call

`glossary-pass-wf.template.js` runs once during W3, bootstrap, before the
mass-translate template ever runs at W5. **Labeled explicitly: new
hardening, not itself source-proven.** The real project ran its glossary
pass as ad hoc `glossary/TASK.md` plus codex batches producing
`glossary/out_*.json` — not a schema-validated Workflow script. This
template applies the proven review-loop *mechanics* (fire-and-forget
dispatch, bounded disk-poll, disk-is-truth) to a new context by analogy —
sound engineering, but not "this exact script ran on a real project." A
first real project should pilot this template on one small batch and
manually verify its `canon.json` merge output before treating it as fully
load-bearing, the same stress-gate discipline W4 applies to translation.

**1.2.0 restructure (#87, #88, #90, #97).** The pre-1.2.0 shape had a single
schema-validated codex `agent()` call per batch, banking the codex return
directly into `canon.json` with no independent disk check (#88) and racing
concurrent batches against one shared `canon.json` (#90) — on top of the
`CANON_BATCH_SCHEMA` top-level-`array` shape that made every dispatch fail
outright (#87). 1.2.0 brings the glossary batch into the same shared
DISPATCH → WAIT → CONSUME pattern review already uses, plus one serialized
final merge to close the concurrent-write race:

**Deterministic PRE-WORKFLOW setup**, run by the orchestrating session
*before* `pipeline()` is ever called — not itself an unbounded Workflow
step, and independent of the codex batch calls, so a batch can't pass
coverage by omitting names from a manifest it also controls:

1. Resolve `effectiveRunId` via the resume-integrity gate above.
2. Create + validate `glossary/runs/<RUN_ID>/`.
3. Atomically serialize each batch's exact `args.candidates[].name` list to
   `glossary/runs/<RUN_ID>/manifest_{index}.json`, plus an aggregate
   `glossary/runs/<RUN_ID>/manifest_all.json` (the union of every batch).
4. Abort (nonzero, no dispatch at all) if writing/reconciling any manifest
   fails.

`resume_setup.py` implements steps 1–4 (see above); `SKILL.md`'s Step 0a
scaffold pre-creates `glossary/runs/` itself.

**Per-batch (DISPATCH → WAIT):**

```js
pipeline(batches, (batch) => batchDispatchWaitLoop(batch))
```

- `batchPrecheckPrompt(batch)` — Claude, `effort:'low'`, no `agentType`, no
  schema, **run FIRST (resume-skip, 1.3.5 #101)**: a single-shot, read-only
  run of the same `--check-batch` invocation `batchWaitPrompt` polls. If a
  prior interrupted run of this SAME `{{RUN_ID}}` already left a valid
  `out_{index}.json` fragment on disk, the precheck returns `PRESENT` and the
  batch skips its codex dispatch + wait entirely; any non-`PRESENT` answer (a
  missing, malformed, or wrong-coverage fragment, or a failed precheck) falls
  THROUGH to the normal dispatch + wait, so a bad fragment is never wrongly
  trusted. Safe because any plugin update flips `plugin_bundle_hash` (this
  template is itself a `PLUGIN_BUNDLE_MEMBERS` entry) → a fresh `RUN_ID` with
  no old fragments on disk, so a fragment that still passes `--check-batch`
  against the CURRENT manifest is genuinely current, never stale.
- `batchDispatchPrompt(batch)` — codex, `agentType:'codex:codex-rescue'`,
  `effort:'high'`, **schema-less**, fire-and-forget: writes the run-scoped
  fragment `glossary/runs/{{RUN_ID}}/out_{index}.json` **atomically**,
  self-validates it via `canon_validate.py --check-batch <frag>
  --research-mode X --expect-source-forms-file
  glossary/runs/{{RUN_ID}}/manifest_{index}.json` (shape **and** exact
  coverage against the trusted manifest — no write), and prints
  `FRAGMENT {index}`.
- `batchWaitPrompt(batch)` — Claude, `effort:'low'`, bounded poll of the
  same `--check-batch` invocation, returning `READY`/`TIMEOUT`.

Fragment paths are run-scoped (`{{RUN_ID}}` in the path itself), so — unlike
the pre-1.2.0 design — **no pre-clean call is needed**: a stale fragment
from a prior run simply sits at a different, unreferenced path.

**After every fragment is `READY`, two final calls, never per-batch:**

1. **Final merge** — Claude, `effort:'low'`, **no** `agentType`, **no**
   `schema`: runs `canon_validate.py --merge-batches <frag1> <frag2> …
   --research-mode X` — the single serialized writer that closes #90 (see
   `references/canon-and-glossary.md` for the merge algorithm).
2. **Disk-verify** — Claude, `effort:'low'`, no `agentType`,
   `schema: CANON_VERIFY_SCHEMA` (flat, new — see
   `references/workflow-schema-validation.md`) + its own exact-key-set JS
   guard: runs `canon_validate.py --verify-merged --batch <frag1> <frag2> …
   --research-mode X --expect-source-forms-file
   glossary/runs/{{RUN_ID}}/manifest_all.json`, a **disk-independent**
   re-check that every fragment's items actually landed in `canon.json`
   correctly, closing #88 (the pre-1.2.0 design banked the codex return with
   no disk verification at all). `merged: true` is returned only after
   `--verify-merged` passes AND the JS guard confirms `verified:true` with
   an empty `missing[]`.

`CANON_BATCH_SCHEMA` and the "pilot one batch" special-case prose are both
gone — deleted, not flattened, since the glossary batch dispatch carries no
agent-facing schema at all now (see
`references/workflow-schema-validation.md`'s `#87` section).

The merged output is not accepted on trust at any step: `--check-batch`
validates one fragment (Pass-1 + offline backstop) before it's ever trusted
as `READY`; `--merge-batches` re-validates every fragment again before
threading them into `canon.json` (dedup + collision checking, routed by each
item's own `disposition` field — `entries{}` for accepted, `review_queue`
for queued — no `canon_hash` field exists anywhere) and re-reads the whole
written file against `canon-file.schema.json`, including required
`generation_hashes.particle_config_hash` and `.derivation_bundle_hash`;
`--verify-merged` then independently re-derives, from a **fresh** disk read,
that every item actually landed. `--research-mode live|offline` is required
on every mode, never defaulted; `offline` fatally rejects any merged
`basis:"established"` entry. Batch construction (`glossary_batch_plan.py`, see
the 1.3.5 subsection below) has already excluded every `source_form` present
in the current `canon.json`'s `entries{}` AND every non-retried `review_queue`
entry before any of this runs.

**1.3.5 — batch construction, cost cap, resume-skip (#101/#95/#91).** Two
things now run before this template is even instantiated, plus one new step
inside it:

- **`scripts/glossary_batch_plan.py` builds `args`/`batches`** (once, before
  `resume_setup.py`). It reads `name_candidates.json` + the current
  `canon.json` and: (1) excludes every candidate already resolved — an
  `entries{}` key OR a non-retried `review_queue[].source_form` (the #101
  filter, now in code, not prose; `--retry` is the explicit human re-research
  path); (2) curates the survivors by `likely_name` and `--min-candidate-freq`
  (the profile's optional `glossary.min_candidate_freq`, else 2); (3)
  force-includes any `elision_ambiguous` row and its `elision_stripped_form`
  target for adjudication (#91), co-locating the pair in one batch. On the
  all-resolved case it prints `{"no_new_candidates": true, "batches": []}` and
  the orchestrating session skips `resume_setup.py` and this Workflow entirely
  — see `references/canon-and-glossary.md`'s Citation-cache section.
- **Preflight cost cap** (mirroring W5's estimator): right after
  `const BATCHES = ...`, before dispatching anything, the template computes
  `estimatedCalls = 3 * BATCHES.length + 2` (per batch: precheck + dispatch +
  wait; plus the fixed final merge + verify pair) and refuses the whole run
  with `{merged: false, reason: "batch-too-large", estimatedCalls, cap}` if it
  exceeds `engine.batch_agent_cap` — the SAME field W5 reads, spliced in as
  the bare-integer `{{BATCH_AGENT_CAP}}` token. The count is over BATCHES,
  never candidates-per-batch, so a co-located elision pair nudging one batch a
  candidate or two over its nominal `--batch-size` never trips it. A refused
  run re-plans smaller batches (`glossary_batch_plan.py --batch-size`).
- **Resume-skip precheck** — the `batchPrecheckPrompt` bullet above; a valid
  pre-existing fragment for this `{{RUN_ID}}` is trusted and its dispatch +
  wait skipped, so a resumed run never re-pays the codex dispatch for a batch
  already done.

## Ledger writes stay orchestration-adjacent, not orchestration-owned

Every per-segment ledger-fragment write goes through the schema-validated,
low-effort `agent(recordLedgerPrompt(seg, fields), {effort:'low', schema:
LEDGER_WRITE_SCHEMA})` (flat now — see
`references/workflow-schema-validation.md`); no fragment write happens any
other way. It is called from five distinct points inside the per-segment
loop above (before translate dispatch, on wait timeout, and for each of the
three JS-decided terminal outcomes). Immediately after any `success:true`
ledger-write return, the Workflow JS itself compares the returned
`fragment_path`'s segment-ID component against `seg` and the returned
`status` against `fields.status`; a mismatch is handled as
`reason:'ledger-write-mismatch'`, never retried through the same
ledger-write channel. The **converged**-status write additionally carries
the token/sha commit-gate precondition — see
`references/ledger-and-resumability.md`'s commit-gate chain. `mergeLedgerPrompt`
is called once at the end of the whole batch as
`agent(mergeLedgerPrompt({expectedSegs: SEGS}), {effort:'low', schema:
LEDGER_MERGE_SCHEMA})` (also flat now), using the same `SEGS` array
`select_segments.py` emitted, and the batch is not complete until that
mandatory completeness check — now including its own per-segment
token/sha re-check, the last link in the commit-gate chain — passes. The
schemas, exact payload shapes, and why `pipeline()`'s per-segment
concurrency rules out a single shared read-modify-write of one `ledger.json`
are `references/ledger-and-resumability.md`'s subject in full.
