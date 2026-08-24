# Orchestration and batching

This file covers how a run actually gets dispatched: the W1–W8 pipeline shape,
why dispatch is a sanctioned launcher — W5's default
`segment_dispatch_driver.py` (#516) or the retained `pipeline()` fallback this
file otherwise describes — and never named-teammate `Agent()` fan-out, the
exact per-segment loop as it executes inside that pipeline, how the prompt
functions are generated, and the `batch_agent_cap`
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
- **W2 Extract** — for `gutenberg_epub`/`plain_text`, run the adapted
  `extract.py`; for `custom`, run the co-designed
  `scripts/custom_extractors/<value>` extractor instead (`extract.py` there
  is only Step 0a's unadapted template copy, never run — see
  `source-format-adapters/custom.md`). Either way, extraction produces
  `manifest.json`, and the extractor's own blocking self-checks (bijection,
  coverage, spine-order, `no_segment_exceeds_max_words`, etc., or a
  documented equivalent for `custom`) must be green before anything
  downstream runs. The final `manifest.json` also passes
  `manifest.schema.json` validation with `jsonschema.Draft202012Validator`
  immediately after extraction, then the managed `validate_extraction.py`
  gate (schema validation + independent manifest-derivable re-derivation for
  every format; the self-check region-hash pin only for `gutenberg_epub`/
  `plain_text` — see `false-green-gate.md`).
- **W3 Bootstrap** — style bible by hand/interview, plus the mandatory
  language smoke test, plus the codex glossary-pass (its own, smaller
  Workflow pipeline — see below) that freezes `canon.json` — or, when
  `glossary.enabled: false` (#727), an empty-but-stamped `canon.json`
  bootstrapped via `canon_validate.py --init` instead of the glossary pass,
  with the smoke test still mandatory either way.
- **Mandatory homonym-split evidence gate** — runs after W3's THREE
  rejoining branches (the codex glossary-pass, the `no_new_candidates` skip,
  and the `glossary.enabled: false` disabled branch alike),
  strictly before W3a: `scripts/canon_adjudication_audit.py --check
  --particle-config <literal value> --advisory` against the resolved
  `canon_senses.json` sidecar (default path, never overridden here). Not the
  same invocation as SKILL.md's opt-in categories-1-4 gate documented for
  Deliver (W7/W8) — this call always runs, and even under `--advisory` it
  still HALTS before W3a on any unverified, stale, or collapsed split when
  `canon_senses.json` is non-empty, and on a category-1 **surface-variant**
  duplicate `source_form` whether or not the project opted into that gate
  (#244); a project with an absent/empty sidecar passes through as a no-op
  **for the split checks**, reported as `homonym_split: NOT ENUMERATED`
  rather than a bare `0` — the category-1 surface-variant halt above is
  computed from `canon.json` and applies to such a project too. See
  SKILL.md for the exact command and
  `canon-and-glossary.md`/`canon_adjudication_audit.py` for the
  evidence-verification mechanics.
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
- **W5 Mass-translate** — the main event. On W5's DEFAULT launcher since
  #516, `segment_dispatch_driver.py` runs `select_segments.py` itself and
  drives the same per-segment loop locally, with no agent-call budget to
  estimate. What follows is the retained FALLBACK path: `select_segments.py`
  classifies
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
  `--only-segs` restriction). `project_complete: true` when every
  `manifest.json` segment classifies `reusable`, MINUS two named carve-outs
  for segments that already converged and only look stale: the #491
  machinery-only one (`stale_previously_converged`, always on) and, when
  `profile.yml` declares `validation.admit_contract_only_stale`, #533's
  contract-only one (`stale_contract_admitted`, which names its segments).
  Every other `stale` segment blocks exactly as before; the frontback coverage report
  is advisory only, and this frontback-through-segment-loop treatment is new
  plugin hardening, not source-proven.
- **W8 Deliver** — report convergence stats, list any `blocked` or
  `non_converged` segments explicitly, and surface W7's per-category
  whole-project completeness counts alongside `project_complete`; assembling
  drafts into one distributable book file is out of scope for v1
  (`output.v1_scope: segment_drafts_and_audit`).

Only W3's glossary-pass and W5's mass-translate are themselves Workflow
`pipeline()` calls; the rest are scripts or hand-driven steps the
orchestrating session runs directly. For W5 that describes its FALLBACK
launcher: since #516 its default is the local `segment_dispatch_driver.py`,
which runs the same per-segment loop with no Workflow tool involved (SKILL.md,
"Default dispatch path").

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
`${durable_root}/runs/.plugin_bundle_hash` (computed once, at Step 0a) covers
this template specifically, but only as of that moment: the marker
characterizes the DURABLE copies under `${durable_root}/scripts/` and is
never recomputed, so by itself it cannot detect a plugin update landing
between Step 0a and a later batch. SKILL.md's W5 rule (#396) — run
`scaffold_setup.py --verify` immediately before EACH live-tree read or
execution of a bundle member — is what closes that gap, comparing the durable
copies against the live plugin tree on demand. Each use, never once per
session: the install is shared, so a verdict is evidence about the tree as it
was when the check ran and does not stay true for a later instantiation.
Residual: the window is not closed, only narrowed to the gap between a verify
and the use it guards; an update landing inside that gap is still masked. Prose enforcement is a weaker guarantee than a
code gate, and it was chosen for a maintenance reason rather than an
impossibility one: the check has to fire ahead of MANY entry points — every
script that redirects a bundle member's resolution with `--plugin-root` —
and most of those are themselves bundle members, so wiring the call into them
would move the very hash the check protects and re-stale every converged
segment (#482). Not all of them: `final_audit.py` is excluded from both
bundles and could host the call at no hash cost. But a per-entry-point call
is an enumeration that grows with every new `--plugin-root` consumer, and
there is no single host all of them pass through. One rule stated over the
class is the smaller thing to maintain and the harder thing to leave
incomplete.

Bundle membership stays split three ways. `plugin_bundle_hash` gates cache
reuse and covers every entry of `cache_key.py`'s own `PLUGIN_BUNDLE_MEMBERS`
tuple — scripts plus the two workflow templates. **Read that tuple; this page
deliberately no longer restates it.** The list used to live here and drifted
twice: 1.4.7's "ten scripts + two templates" went stale the moment
`canon_senses.py` and `fetch_citation.py` joined, and the replacement went
stale again the moment `claim_record.py` (#438) and `reject_review.py` (#461)
did. A restatement with no test behind it always loses that race.
`review_ready.py` and `resume_setup.py` (both new in 1.2.0) are correctness-
determining in the same sense as `review_artifact_check.py`/`ledger_update.py`
— a bug in either could certify a stale or wrongly-scoped artifact as safe to
consume, or wrongly permit/refuse a resume — so both are gating members, not
diagnostic-only, unlike their sibling readiness/merge scripts below.
`glossary_batch_plan.py` (new in 1.3.5) and `codex_job.py` (the W5
translate/review driver, new in 1.4.7) are gating members too, for the same
reason — the former shapes glossary content, the latter
validates-before-promotes each draft/review artifact.
`orchestration_bundle_hash` is non-gating for convergence — never part of
the composite cache key — but gating for resume, folded into the
resume-integrity digest (see below); it covers exactly `scaffold_setup.py`'s
`ORCHESTRATION_BUNDLE_MEMBERS` tuple, which is restated once, in
`references/ledger-and-resumability.md`, and nowhere else;
`derivation_bundle_hash`
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
  SEGS,                                       // deduplicated: a duplicate seg id HARD-THROWS before pipeline()
  (seg) => agent(translateDrivePrompt(seg), {
    effort: 'low', phase: 'Translate', label: `translate:${seg}`,  // launches codex_job.py --kind translate DETACHED; returns "DISPATCHED <seg> <DISP>"
  }),
  (dispatchResult, seg) => reviewFixLoop(seg, dispatchResult),
)
```

Stage 1 is the translate DISPATCH — a plain-Claude drive agent (no `agentType`,
`effort: 'low'`) that writes the codex task-file and launches the shipped
`codex_job.py --kind translate` driver DETACHED (`nohup`), returning
`DISPATCHED <seg> <DISP>` (#198; codex itself runs at `--effort <engine.effort>` via the
driver, NOT as an agent effort option). Stage 2 is `reviewFixLoop(seg,
dispatchResult)` — a plain async function, not another `agent()` call — which
parses the drive return for the per-dispatch `DISP` nonce (used to key the
translate wait's fail-fast sentinel), then runs the readiness-poll and the
review/fix loop below and returns this segment's final structured result. The
drive agent does NOT itself translate; codex's actual output reaches the
canonical `draft_path(seg)` only after the driver validates the isolated attempt
and atomically promotes it.

1. **Translate.** `agent(translateDrivePrompt(seg), { effort: 'low' })` (no
   `agentType`) — the DISPATCH half of the shared codex work-call pattern
   (`references/workflow-schema-validation.md`), now the plain-Claude drive
   agent that launches the detached `codex_job.py --kind translate` driver and
   returns `DISPATCHED <seg> <DISP>`. codex (at `--effort <engine.effort>`) writes its
   attempt carrying a `dispatch_token = <RUN_ID>:<seg>` metadata field; the
   driver **validates the isolated attempt** (`draft_ready.py`/`validate_draft.py`
   on the `--candidate-file`) and only then atomically promotes it to
   `draft_path(seg) = segments/{seg}.draft.json`. This is the one deliberate
   exception to the "codex accuracy calls need a schema" framing (R7 in
   `references/engine-loop.md`): the translate work is intentionally
   schema-less, gated instead by file output plus the Workflow's own on-disk
   `draft_ready.py`/`validate_draft.py` ACCEPT gate on the current canonical —
   see `references/false-green-gate.md`. (Before #198 this was a direct
   `agentType: 'codex:codex-rescue'` fire-and-forget call whose forwarder
   backgrounded codex and returned a stub — the driver owns the launch now.)
2. **Readiness poll.** A low-effort wait/poll step (`effort: 'low'`) blocks the
   review loop from starting until the driver has delivered a complete,
   current-run-tokened draft. Its ACCEPT runs the FULL canonical gate directly on
   `draft_path(seg)` — `draft_ready.py <seg> --expect-token <RUN_ID:seg>` AND
   `validate_draft.py <seg>` — in a bounded bash loop (no external `timeout`
   binary), and its **fail-fast** is a presence check on the DISP-named sentinel
   `[ -f segments/.codex_failed.<seg>.<DISP> ]` (keyed on the `DISP` captured from
   stage 1's `DISPATCHED` return), evaluated only AFTER ACCEPT did not pass this
   iteration — so a genuine driver failure short-circuits the poll instead of
   waiting out the whole bound. This specifically prevents a Claude fix-agent from
   ever ending up authoring a missing translation, since "codex only translates"
   would otherwise be silently violated the moment a fix step ran against a
   nonexistent/partial/stale-run draft. **1.16.1 (#348):** this wait is no longer
   ONE `agent()` call. The Bash tool clamps any single call at
   `BASH_CALL_CAP_SEC = 600 s` regardless of the timeout the agent asks for, so
   the 3450 s bound is SPENT across up to `WAIT_CHUNKS = 8` chunk calls
   (`WAIT_CHUNK_SEC = 480 s` each, chunk 8 shortened to the 90 s that remain, so
   the chunks sum to exactly 3450), each returning `READY <seg>`,
   `FAILED <seg>` or `PENDING <seg>`. Whenever the chunk loop ends anything
   other than READY — budget exhausted OR a chunk reported the driver's fail
   sentinel — ONE authoritative, non-polling re-check of the canonical draft
   runs before a timeout is declared, because a job that finishes after the last
   chunk's poll ended leaves a valid draft that nothing would otherwise read.
   Only if that re-check also fails does this branch return
   `{ seg, converged: false, reason: 'translate-timeout', detail }` (#400,
   see item 5 below), and the loop never reaches a review call at all for
   this segment. The reason string is
   deliberately unchanged: `select_segments.py`'s "non-terminal → recoverable"
   rule and every recovery doc key off it. **1.16.0:** this wait is
   containment-guarded — a reply carrying the fail sentinel (`FAILED <seg>`
   since 1.16.1, `TIMEOUT <seg>` before it) anywhere in it leaves the READY path
   even when glued to prose, which whole-line matching alone would have skipped,
   proceeding as ready on a reply that said it had failed. `translate-timeout`
   is deliberately non-terminal, so a false RED here is the cheapest of the
   guarded sites: `select_segments.py` picks the segment back up and
   auto-redispatches it on the next run.
3. **Review/fix loop**, up to `engine.max_fix_rounds` rounds of review → fix
   → re-review, exiting early the moment a review reports
   `clean && coverage_ok`. Each round's review point is itself the shared
   DISPATCH → WAIT → CONSUME pattern, not one call:
   - **Review DISPATCH (`reviewDrivePrompt`)** — the plain-Claude drive agent
     (`effort: 'low'`, no `agentType`) that launches the detached
     `codex_job.py --kind review` driver and returns `DISPATCHED <seg> <DISP>`.
     codex computes `draft_sha1` hash-first, reviews, and its attempt carries
     `dispatch_token = <RUN_ID>:<seg>:r<roundLabel>` (`roundLabel` = the round
     number or `final`); the driver validates it (`review_ready.py
     --candidate-file`) before atomically promoting `review_path(seg) =
     segments/{seg}.review.json`.
   - **`reviewWaitPrompt`** — Claude, `effort:'low'`, bounded poll whose ACCEPT
     runs `review_ready.py {seg} --expect-token <RUN_ID:seg:rN>` on the canonical
     and whose fail-fast is the DISP-named sentinel `[ -f
     segments/.codex_failed.<seg>.<DISP> ]` (no external `timeout` binary).
     **1.16.1 (#348):** chunked exactly like the translate wait above — up to
     `WAIT_CHUNKS = 8` chunk calls spending the same 3450 s bound, each returning
     `READY`/`FAILED`/`PENDING <seg>`, then ONE authoritative non-polling
     re-check (`reviewWaitRecheckPrompt`) of the canonical review artifact,
     running on the fail-fast path too. Budget exhausted or fail-fast, with that
     re-check also failing → `blocked review-timeout`. The review JOB is never
     re-dispatched; the re-check is a second look at disk, not a retry.
     **1.16.0:** containment-guarded — a reply carrying the fail sentinel
     (`FAILED <seg>` since 1.16.1, `TIMEOUT <seg>` before it) anywhere in it
     leaves the READY path, even glued to prose, where whole-line matching
     alone would have skipped it and proceeded as ready (see the glossary-pass
     template section below, and `references/canon-and-glossary.md`). Because
     the segment is not re-reviewed here, a false RED costs it for the run.
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
     `agent(fixPrompt(seg, round, revObj), { effort: EFFORT })` — no
     `agentType` field, keeping it on plain Claude. Since 1.3.6/#132 option b
     `fixPrompt` instructs the fixer to READ `review_path(seg)` back off disk
     and work through its on-disk `findings[]` array — applying what it can
     substantiate against the source and refusing what it cannot (#532);
     `revObj` (the same
     schema-validated object `readReviewPrompt` returned this round, still in
     memory) remains its third argument for the convergence decision and the
     review-artifact gate's `--expected-file`, but is no longer the findings
     source — a deliberate, documented departure from the proven reference's
     2-argument `fixPrompt(seg, round)` shape. See `references/engine-loop.md`
     R1 for the full reasoning.
4. **Confirming final review.** Always one final confirming review after the
   round cap, even if the loop exited because of the cap rather than
   convergence — a fix that goes unverified is the single most common source
   of a silently-broken "done" segment. This final review point runs the
   identical dispatch → wait → read → check sequence (`roundLabel: 'final'`
   in its `dispatch_token`).
5. **Result.** Ordinary translate/review non-convergence returns a structured
   `{ seg, converged: false, reason, rounds, lastFindings, detail }` object —
   never throws, never silently marks done; `detail` is optional (#400, see
   below). `reason` is one of
   `translate-timeout`, `review-timeout`, `review-null`,
   `review-artifact-mismatch`, `review-fabricated-loc` (1.3.6/#133 — a
   schema-valid, artifact-matched verdict whose finding carries a bare,
   colonless `loc` instead of a colon-delimited content location. The check
   is a SHAPE test, not a proof of fabrication: it never resolves the loc
   against the draft, so this reason names what the loc looked like, never
   what the reviewer knew),
   `fix-call-failed` (1.3.6/#131 facet A — the fix call came back falsy/
   `DRAFT_MISSING` but the `draftPresentAndValid` probe confirmed the draft
   is present-and-valid, or the probe call itself failed inconclusively;
   **1.16.0** also reaches here when a fix reply merely MENTIONS
   `DRAFT_MISSING <seg>` without reporting one, since that site is now keyed
   on containment via `mentionedAnywhere()` and cannot tell the two apart —
   the accepted, non-terminal cost of no longer missing a real report),
   `draft-missing`, or `cap` (non-converged after the final confirming
   review).
   **1.3.6 (#131):** every reason above EXCEPT `draft-missing` and
   `cap` is now recoverable rather than terminal — no ledger write happens
   for them at all (see `references/ledger-and-resumability.md`'s
   `recordLedgerPrompt` call-sites section), so the segment's `in_progress`
   fragment stays the durable record and `select_segments.py`
   auto-redispatches it next run. Ledger-write failures are surfaced through the Workflow
   result instead of being written back through the same ledger channel:
   `success:false` from `recordLedgerPrompt` returns
   `{ seg, converged: false, reason: 'ledger-write-failed', detail: <error> }`,
   while the JS-side fragment/status payload-intent mismatch returns
   `reason: 'ledger-write-mismatch'`.

   **A terminal reason from OUTSIDE this list (#398).** Everything enumerated
   above is a value of the Workflow template's OWN returned `reason` field, and
   the 1.3.6 recoverability rule applies to those and only those.
   `translate-rejected` is a different kind of thing: a LEDGER reason, written
   by `codex_job.py` rather than by the template, when `validate_draft.py` ran
   against a translate candidate and rejected it with exit 1 -- its content
   verdict. Because it is written by the child both dispatch paths launch, it
   is the one terminal ledger write the Workflow path and
   `segment_dispatch_driver.py` both inherit without either being changed, and
   it is what stops a permanently-rejected segment from being paid for again on
   every subsequent run. It is terminal; the 1.3.6 sentence above does not
   reach it. Exit 2, a gate that could not run, and every `draft_ready.py`
   rejection stay recoverable. See `references/ledger-and-resumability.md`'s
   call-sites section. A `dispatch_token`/sha mismatch at the
   convergence ledger write (see `references/ledger-and-resumability.md`'s
   commit-gate chain) also surfaces as `reason: 'ledger-write-failed'` —
   never recorded `converged`.

   **`detail` (#400).** The runtime hands this script no error text when a
   subagent dies on a terminal API error — `agent()` just returns `null` —
   so `reason` can name a STAGE but never a CAUSE. On a failed-call reason
   (`translate-timeout`, `review-timeout`, `review-null`,
   `review-artifact-mismatch`, `fix-call-failed`, `ledger-write-failed`,
   `ledger-write-mismatch`, `ledger-merge-failed`) `detail` describes what
   the call actually returned, capped and collapsed to one line by
   `flattenDetail()` (`DETAIL_CAP = 160`) — the chokepoint every
   MODEL-AUTHORED or otherwise dynamic detail runs through: the agent reply
   via `replyDetail()`, the artifact check's `mismatch_detail`, the relayed
   ledger/merge `error`, and the `ledger-write-mismatch` string;
   `sourcedDetail()` re-flattens so a source label counts against the same
   budget rather than being appended past it. (The fixed fallback constants
   — `replyDetail()`'s own three, `FABRICATED_LOC_DETAIL`,
   `PROBE_NULL_DETAIL`, and the two ledger/merge "did not report success"
   strings — are short, single-line by construction and never reach
   `flattenDetail()`.) Two reasons read differently:
   `review-fabricated-loc`'s `detail` is the fixed `FABRICATED_LOC_DETAIL`
   constant naming the shape defect, never a returned reply, and
   `draft-missing`/`cap` carry no `detail` at all. A `source:` prefix
   (`review dispatch`, `translate dispatch`, `draft probe`, `fix call`) is
   added only where one `reason` can be produced by two different failing
   calls that it alone cannot distinguish; elsewhere `detail` is
   deliberately unlabelled, so an outage that kills calls at several stages
   does not needlessly fragment into a private string per stage (a labelled
   outage, e.g. both dispatchers dying at once, still buckets separately by
   design — `translate dispatch:` and `review dispatch:` are different
   strings). The tally below reports every bucket of two or more, never a
   single winner, so a runner-up bucket is never dropped. `waitDetail` rides
   alongside `detail` only on the two timeout reasons, and only when a
   dispatch-sourced `detail` displaced the wait reply — the proximate wait
   text is then kept rather than discarded.

   The batch summary (after the "Translate/review pass done" log line, once
   `pipeline()` returns) tallies these `detail` strings across every failed
   segment: any value repeated on two or more rows is logged and returned
   as `failureDetailTally`, an array of `{ detail, count }` ordered by
   count descending then `detail` ascending, on both the `batchComplete:
   true` return and the `ledger-merge-failed` return.

No sub-chunking exists anywhere in this loop in v1 — `mass-translate-wf.template.js`
operates only on whole `seg` items; a segment whose `word_count` exceeds
`max_segment_words` is caught at W2 extraction, never here (see `SKILL.md`
W4).

## Prompt functions — generated from the profile at instantiation time

`mass-translate-wf.template.js` defines sixteen prompt functions:
`translatePrompt`, `translateDrivePrompt`, `waitPrompt`, `waitRecheckPrompt`,
`reviewDispatchPrompt`, `reviewDrivePrompt`, `reviewWaitPrompt`,
`reviewWaitRecheckPrompt`, `waitChunkPrompt`, `waitRecheckPromptFor`,
`readReviewPrompt`, `verifyReviewArtifactPrompt`, `fixPrompt`,
`draftProbePrompt`, `recordLedgerPrompt`, `mergeLedgerPrompt`. Four of those
are 1.16.1/#348 additions, all wait-side: `waitChunkPrompt` and
`waitRecheckPromptFor` are the shared builders both wait sites splice, while
`waitRecheckPrompt` and `reviewWaitRecheckPrompt` are the per-site
authoritative re-checks. `waitPrompt`/`reviewWaitPrompt` keep their names but
now build ONE chunk each, taking a `chunkIndex`. The #198 drive
prompts `translateDrivePrompt`/`reviewDrivePrompt` are the plain-Claude
dispatchers that launch the detached `codex_job.py` driver (returning
`DISPATCHED <seg> <DISP>`); `translatePrompt`/`reviewDispatchPrompt` remain,
now supplying the codex TASK body text that the drive prompts embed into the
driver's task-file. (`reviewPrompt` — the old, single, schema-validated review
call — no longer exists; the review point is now the DISPATCH/WAIT/CONSUME×2
sequence, per `references/workflow-schema-validation.md`.
`verifyReviewArtifactPrompt` keeps its pre-1.2.0 name but is now dispatched as
a separate call after `readReviewPrompt` returns, rather than immediately after
the old single `reviewPrompt` call.) `glossary-pass-wf.template.js` defines its own,
smaller set of eight: `batchPrecheckPrompt`, `batchDispatchPrompt`,
`batchWaitChunkPrompt` and `batchWaitRecheckPrompt` (**1.16.2**, the pair that
replaced the single `batchWaitPrompt`), `citationPreparePrompt` and
`citationJudgePrompt` (1.16.1,
`live` only — the pair that replaced 1.16.0's single `citationReviewPrompt`),
`mergeBatchesPrompt`, and `glossaryVerifyPrompt` (`CANON_VERIFY_SCHEMA`) — see
`references/canon-and-glossary.md`. Since **1.16.2** its wait IS the shape
W5's two waits use — chunks plus one authoritative non-polling re-check, over
an elapsed-time loop — where before it was the odd one out; the sentinel sets
and chunk counts still differ. See
`references/workflow-schema-validation.md`'s WAIT section.

**There is no templating engine at Workflow-runtime.** Every prompt function
is plain JavaScript string interpolation against constants the orchestrating
session substitutes once, at the moment it instantiates the template file
from the plugin's shipped copy — before the Workflow tool ever executes it.
The template documents its own substitution tokens explicitly:
`{{SOURCE_LANG}}`, `{{TARGET_LANG}}`, `{{DURABLE_ROOT}}`,
`{{VERSE_POLICY_INSTRUCTION_BLOCK}}`, `{{MAX_FIX_ROUNDS}}`,
`{{BATCH_AGENT_CAP}}` (both templates' preflight cost caps — the glossary-pass
template's use is new in 1.3.5), `{{RUN_ID}}`
(new in 1.2.0, both templates — see below), `{{EFFORT}}` (#197, both
templates — `engine.effort`'s enum value, substituted as a plain quoted
string; drives every codex/fix effort carrier in the instantiated template
from this one value, see `references/ledger-and-resumability.md`'s
dual-injection rule), `{{MODEL}}` (#197, mass-translate template only —
`engine.model`, or an empty string when unset; threads only to the two
`codex_job.py` driver launches, never to the glossary pass or the fix
step), `{{CODEX_COMPANION_PATH_JSON}}`
(new in 1.4.7, mass-translate template only — the `json.dumps`-encoded absolute
`codex-companion.mjs` path, resolved once at instantiation by
`resolve_codex_companion.py` and handed to `codex_job.py --companion`;
JSON-encoded so a path with a space or non-ASCII character stays a safe JS/bash
literal), and (glossary-pass template only) `{{RESEARCH_MODE}}`. `{{VERSE_POLICY_INSTRUCTION_BLOCK}}` in
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
directory `resume_setup.py` itself owns (`${durable_root}/runs/<RUN_ID>/`,
written unconditionally for every `kind`, with
`${durable_root}/glossary/runs/<RUN_ID>/` created as an ADDITIONAL
directory — never a substitute — when `kind="glossary"`) and substitutes
`{{RUN_ID}}` inside the instantiated template — so a fresh instantiation
and a resumed one that reuses the same `RUN_ID` produce byte-identical
tokens/paths throughout.
(`${durable_root}/runs/workflows/<RUN_ID>/` is a SEPARATE directory —
confirmed distinct from the above: `resume_setup.py`'s own source contains
no mention of "workflows" anywhere, and `write_run_dir()` creates
`runs/<RUN_ID>/` directly under `runs/`, never nested under a `workflows/`
subdirectory. `runs/workflows/` is part of Step 0a's created skeleton;
exactly what gets written under `runs/workflows/<RUN_ID>/` and by whom is
NOT re-derived here — do not assume it without checking the current
Step 0a/driver source.) The full path of resume_setup.py's own run
directory is logged in W8's status output.

**Whether to resume at all is a separate decision from the `RUN_ID` value
itself** — gated by the resume-integrity digest below, never by "a
`resumeFromRunId` was supplied" alone. One MATCH case deliberately reuses the
`RUN_ID` while passing no `resumeFromRunId` at all; see the exception under
that gate.

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
  args: mass: {}  // LT-409: PINNED — see below, never the invocation's own args
      | glossary: <the full ordered args this invocation was given>,
  subst: {research_mode, verse_policy, source_lang, target_lang,
          max_fix_rounds, batch_agent_cap, max_codex_jobs_per_batch, effort,
          citation_content_types},   // resolved profile substitutions
                                     // (#197: effort added; #347: citation_content_types added)
  domain: mass: {seg: <cache_key.py's 15-field composite per seg>}
                // LT-409: seg ids come from manifest.json's own segments[]
                // (the FULL candidate set), never from a caller-supplied list
        | glossary: {glossary_rule, canon_hash},
  version: {plugin_bundle_hash: <runs/.plugin_bundle_hash>,
            orchestration_bundle_hash: <runs/.orchestration_bundle_hash>,
            schemas: <sha of the schemas/ dir>},
}))
```

**LT-409, `args` for `kind="mass"`:** pinned to the literal empty object
`{}`, not the invocation's own CLI-scoping args (`select_segments.py`'s
`--only-segs`/`--allow-retranslate-converged`/`--allow-empty`). Those
flags govern Step 1's OWN gating, already enforced before this digest is
ever computed — they do not change what any already-promoted per-segment
artifact MEANS, so hashing them here would gate resume on a value that
narrows every time the operator paces a batch with `--only-segs`, for the
identical reason `domain`'s own seg-id source was fixed (next paragraph).
`kind="glossary"` is unaffected — `args` keeps its pre-existing meaning
there.

**LT-409, `domain` for `kind="mass"`:** the seg-id set comes from
`manifest.json`'s own `segments[]` array — the full candidate set, which
does NOT shrink as segments converge — never from the Workflow's own
emitted `SEGS` (`select_segments.py`'s eligible list, which EXCLUDES
`reusable` segments and therefore shrinks by one entry every single
convergence). Hashing the shrinking list forced a fresh, non-resuming
`RUN_ID` on every convergence, discarding in-flight fix work each time —
the domain now stays stable across exactly that case, while still
changing, correctly, when the manifest itself changes or any segment's own
cache_key does.

**`plugin_root` (#412) is accepted by `resume_setup.py` as a separate
top-level payload field, never as a `subst` member or any other digest
input** — it names a filesystem location, not a profile-derived semantic
value, and `plugin_bundle_hash` (in `version`, above) already covers "did
the plugin's own content change" without making the digest non-portable
across operators' checkouts.

**MATCH** the prior run's own recorded digest → normally resume with
`resumeFromRunId` — every digest input is byte-identical, so every cached
result (including the four unscoped-prompt calls above) is provably still
valid, **except for the `glossary-pass-null` failure below, where the
Workflow must be invoked WITHOUT `resumeFromRunId`**. **MISMATCH, or no
prior digest** → launch a **fresh run**, a fresh `RUN_ID`, and explicitly
**no** `resumeFromRunId` — reuse nothing.

**Exception — a MATCH whose cached result is a non-answer (#404).** The
digest reasons about INPUTS; it cannot see that a cached result records a
batch that never became ready. When the previous glossary pass returned
`merged: false`, `reason: "fragment-check-failed"` with any batch at
`reason: "glossary-pass-null"`, resuming replays that batch's cached precheck
and wait replies instead of calling anything, so the identical verdict comes
back — which reads as "it failed again" rather than "it never ran". Recover
by offering the prior `RUN_ID` to `resume_setup.py` in `resume_from_run_ids`
exactly as usual — the failed pass merged nothing, so the digest normally
still matches, and `resume_setup.py`'s own `resume: true` answer is the
authority — but invoke the Workflow WITHOUT `resumeFromRunId`. That re-runs
each batch's precheck against what is actually on disk: a valid attempt-0
fragment is resume-skipped, a missing or invalid one is dispatched again.
Before re-invoking, confirm the previous run's codex jobs are terminal — a
dispatch job outlives the `agent()` call that awaited it, so a late one can
still rewrite its attempt path after `write_run_dir()`'s wipe.

That two-branch rule is the general principle the digest closes: cover
*every* input that can change a cached agent output, not just the ones a
naive "did the source text change" check would think to cover — a
`research_mode: live→offline` flip,
for instance, changes agent policy and `--check-batch` validity without
changing a single hashed content byte, which is exactly why `subst` is a
first-class digest input alongside `args`/`domain`/`version`, not folded
into one of them. **#197:** `subst` gains `effort` for the identical
reason — an `engine.effort` tier change (e.g. `high`→`xhigh`) changes what
the codex/fix calls actually do without changing any hashed content byte.
The mass path already sees an `engine.effort` (and `engine.model`) change
per-segment via `agent_config_hash` (see
`references/ledger-and-resumability.md`), but the glossary pass has no
per-segment cache key at all, so `subst` is the only place its own
resume-integrity digest can see an effort change. `model` is deliberately
**not** added to `subst` — the glossary pass has no model knob, so folding
it in here would encode a false dependency. `resume_setup.py` (new script, `assets/scripts/`)
implements this: given the run kind, resolved substitutions, and (glossary
only) candidates, it computes `input_digest` — deriving the mass-kind
domain itself from `manifest.json` (never trusting a caller-supplied seg
list, LT-409) and each segment's cache_key.py composite fresh — then
create-or-compares it against every candidate in `resume_from_run_ids`'
own `runs/<candidate>/input.digest`, in order, returning the first match.
It creates the run directory/directories, and (glossary only) atomically
writes the manifest
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
`assets/profile.example.yml`). 1.3.5 raised this default from 1000, which the
then-current `1 + N*38`-at-`max_fix_rounds:4` formula made refuse any mass batch
over 26 segments; 3500 admitted the issue's ~78-segment repro,
`1 + 78*38 = 2965`, with headroom. **1.16.1 (#348) more than doubled the
per-segment cost:** a WAIT is now up to 9 calls rather than 1, so the formula
below yielded `1 + N*86` at `max_fix_rounds:4`, and the SAME 3500 cap admitted
at most **40 segments** (`1 + 40*86 = 3441`; 41 segments would need 3527 and the
run refuses to start). A 40-segment book batch therefore sat just under that
ceiling, where before #348 the same batch carried roughly 2000 calls of margin
(`1 + 40*38 = 1521`). **1.68.0 (#607) added two more calls to every continuing
fix round** — the fix-scope audit and the one retry a continuing round may
spend on it — so the per-segment term is now **94**, and the figures above are
history rather than current arithmetic. At 94 the same 3500 cap admits **37**
(`1 + 37*94 = 3479`; 38 would need 3573), and the cap actually shipped today,
`10000`, admits **106** (`1 + 106*94 = 9965`; 107 would need 10059), down from
116 at 86 calls. Whether to raise the cap for a given project is the
operator's call, not this plugin's. **This estimator is new plugin hardening, not
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

**1.16.1 (#348) — a WAIT is no longer one call.** The Bash tool clamps any
single call at 600 s, so each wait is spent as up to `WAIT_CHUNKS = 8` bounded
chunk calls followed by ONE authoritative non-polling re-check:
`WAIT_CALLS = WAIT_CHUNKS + 1 = 9`, worst case. Every "+1 WAIT" below is really
"+`WAIT_CALLS`". Substituting `WAIT_CALLS = 1` recovers the pre-#348 arithmetic
verbatim, so this is a generalisation of the derivation below, not a rewrite of
it.

- **A review point, worst case, is exactly `5 + WAIT_CALLS` calls** (6 before
  #348, 14 now): the review DISPATCH drive agent
  `reviewDrivePrompt` (1 — it launches the detached
  `codex_job.py --kind review` driver; #198 replaces the old
  `reviewDispatchPrompt` codex `agent()` call 1:1, so the count is unchanged) +
  the review WAIT (`WAIT_CALLS`) + the CONSUME pair under its **one shared retry
  budget** — `readReviewPrompt` + `verifyReviewArtifactPrompt` run once, then
  (worst case) the identical pair retried once more = 4 — for
  `1 + WAIT_CALLS + 2×(1 + 1)`. This is a single number, not a set of
  mutually-exclusive terminating sub-cases: the review wait failing is
  the only way a review point resolves in fewer calls, and that
  terminates the segment immediately (see below), so it is never the
  binding case for the worst-case estimate.
- **Every segment, unconditionally, before any review point is even
  reached:** 1 `in_progress` ledger write + 1 translate DISPATCH + 1
  translate WAIT = **`2 + WAIT_CALLS` fixed calls** (3 before #348, 11 now).
- **A NORMAL round** (one that neither converges nor terminates the loop):
  one review point (`5 + WAIT_CALLS`) + one fix call (1) + #607's fix-scope
  audit (up to 2 — a round that CONTINUES may spend a failed first relay and
  its successful retry) = **`8 + WAIT_CALLS` calls** (`6 + WAIT_CALLS` before
  #607).
- **The final confirming review** (always runs, even after the round cap):
  one review point = **`5 + WAIT_CALLS` calls**, no fix call attached to it.
- **+1 terminal ledger write**, whichever terminal status fires
  (`converged`/`non_converged`/`blocked`).

Per-segment worst case, across every branch (converged-at-cap,
non-converged-at-cap, and blocked-on-the-final-round-before-cap — a
`review-timeout`/`review-null`/`review-artifact-mismatch`/`review-fabricated-loc`/
`draft-missing`/`fix-call-failed` block always terminates via a *shorter*
path than running every round to cap, so it is never the binding case;
1.3.6/#131 additionally removes the terminal ledger write for every one of
those reasons except `draft-missing`, which only shortens those paths
further and does not change which branch is binding):

```
perSegment = (2 + WAIT_CALLS)                    (fixed)
           + maxFixRounds * (8 + WAIT_CALLS)     (normal rounds)
           + (5 + WAIT_CALLS)                    (final review, no fix and
                                                  therefore no audit)
           + 1                                   (terminal ledger)
           = 8 + 2*WAIT_CALLS + maxFixRounds * (8 + WAIT_CALLS)
           = 94 at the shipped WAIT_CALLS = 9, maxFixRounds = 4
             (86 before #607; 10 + 7*maxFixRounds = 38 before #348)

  The normal-round term is 8, not 6: 5 review-point calls + 1 fix + TWO
  fix-scope audit calls. Two, because a round that CONTINUES may spend a
  failed first relay and its successful retry; budgeting one would be
  exceeded by an ordinary recovered round.
```

```
estimatedCalls = 1 + SEGS.length * (8 + 2*WAIT_CALLS + maxFixRounds * (8 + WAIT_CALLS))
               = 1 + SEGS.length * 94  at WAIT_CALLS = 9, max_fix_rounds: 4
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
`agent_config_hash` (only `effort`/`max_fix_rounds`/`model` are hashed), so changing
the cap alone never re-invalidates an already-converged segment's cache key.
See `references/ledger-and-resumability.md` for the full cache-key
membership list. **1.3.5:** W3's glossary-pass template reads this SAME
`engine.batch_agent_cap` field, with its own smaller worst-case formula and
the same refusal shape. **1.16.0:** that formula is now MODE-DEPENDENT — an
`offline` run kept the historical `3 * BATCHES.length + 2` unchanged, while
a `live` run additionally pays for the citation-review retry ladder. See the
glossary-pass template section below for both branches. **1.16.2:** the
offline branch no longer matches that historical figure, and deliberately so —
it is now `5 * BATCHES.length + 2`. Holding offline byte-identical to `3N + 2`
was only ever possible because the extra 1.16.0/1.16.1 cost sat entirely in
the live-only citation ladder; the chunked wait (#352) is mode-INDEPENDENT, so
**an `offline` project whose cap was tuned to the old formula can now be
refused at preflight too**, not just a live one — the first time offline
projects are exposed to this class of change.

**The principle the old promise rested on is upheld, not abandoned**, and the
distinction is the whole justification. Through 1.16.1 the offline REAL COST
did not change, so charging offline for the live ladder would have been a
FALSE refusal — and a preflight that refuses runs it should permit is a worse
failure than one that is slightly loose. In 1.16.2 the wait can genuinely cost
`WAIT_CALLS` in BOTH modes, so `5N + 2` is offline's true CEILING — the same
kind of quantity `3N + 2` always was, not a claim about what a run spends —
and a refusal against it is a correct refusal. What the mode-awareness still prevents is the
thing that would break the principle: a MODE-BLIND estimate charging an
offline project the live `19N + 2` for a ladder it can never execute.

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

**1.4.0 — glossary staleness preflight, runs BEFORE this whole PRE-WORKFLOW
setup.** After `glossary_batch_plan.py` returns non-empty batches and
strictly before `resume_setup.py` (kind `glossary`) runs, the orchestrating
session invokes a separate, standalone script —
`glossary_preflight.py` (`{{PLUGIN_ROOT}}/assets/scripts/`, never copied to
`durable_root`) — comparing the durable `schemas/canon-*.schema.json` and
`glossary_TASK.md` against the plugin's own shipped copies, halting
(non-zero exit, no dispatch at all) on any mismatch. See
`references/canon-and-glossary.md` and `SKILL.md`'s W3 section for the CLI
contract and remediation. This is deliberately a **plain script, not an
`agent()` call**, so it is never resume-cached against the `input_digest`
below, and it does **not** perturb the `estimatedCalls` cost formula further
down this section or add a `{{BATCH_AGENT_CAP}}`-style template token — a
future reader should not "fix" that estimator to `+3` for this step; the gate
makes no `agent()` call at all. (That formula is stated once, in the
**Preflight cost cap** bullet below, and deliberately not restated here — it
is mode-dependent since 1.16.0 and should never need applying in two
places.)

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
pipeline(BATCHES, batchStep)
```

- `batchPrecheckPrompt(batch)` — Claude, `effort:'low'`, no `agentType`, no
  schema, **run FIRST (resume-skip, 1.3.5 #101)**: a single-shot, read-only
  run of the same `--check-batch` invocation `batchWaitChunkPrompt` polls. If a
  prior interrupted run of this SAME `{{RUN_ID}}` already left a valid
  `out_{index}_attempt_0.json` fragment on disk (the precheck is hard-wired to
  attempt 0 — `checkBatchCmd(batch.index, 0)`), the precheck returns `PRESENT`
  and the batch skips its codex dispatch + wait entirely — but NOT, since
  1.16.0, the `live`-mode citation review below, which a resumed batch still
  pays exactly like a fresh one; any non-`PRESENT` answer (a
  missing, malformed, or wrong-coverage fragment, or a failed precheck) falls
  THROUGH to the normal dispatch + wait, so a bad fragment is never wrongly
  trusted. Safe because any plugin update flips `plugin_bundle_hash` (this
  template is itself a `PLUGIN_BUNDLE_MEMBERS` entry) → a fresh `RUN_ID` with
  no old fragments on disk, so a fragment that still passes `--check-batch`
  against the CURRENT manifest is genuinely current, never stale.
- `batchDispatchPrompt(batch, attempt, rejectionReason)` — codex,
  `agentType:'codex:codex-rescue'`,
  `effort: EFFORT` (`engine.effort`, #197), **schema-less**, fire-and-forget: writes the run-scoped
  fragment `glossary/runs/{{RUN_ID}}/out_{index}_attempt_{n}.json`
  **atomically**,
  self-validates it via `canon_validate.py --check-batch <frag>
  --research-mode X --expect-source-forms-file
  glossary/runs/{{RUN_ID}}/manifest_{index}.json` (shape **and** exact
  coverage against the trusted manifest — no write), and prints
  `FRAGMENT {index}`. **1.16.0:** the path is attempt-scoped and
  `rejectionReason` carries the citation reviewer's own findings into every
  attempt after the first.
- `batchWaitChunkPrompt(batch, attempt, chunkIndex)` and
  `batchWaitRecheckPrompt(batch, attempt)` — Claude, `effort:'low'`, together
  replacing 1.16.1's single `batchWaitPrompt()`: one CHUNK of a bounded poll
  of the same `--check-batch` invocation, and the post-exhaustion re-check,
  both returning
  `READY`/`PENDING`. **1.16.2 (#352):** the 900 s budget is spent across
  bounded chunks plus one authoritative non-polling re-check rather than in a
  single call that the measured 600 000 ms Bash clamp would kill; a wait
  therefore costs **up to** `chunks + 1` = 3 agent calls rather than exactly 1
  — a `READY` in any chunk ends the loop and suppresses the re-check, so 3 is
  the ceiling the estimator uses, not the runtime cost — and `TIMEOUT` is no
  longer a sentinel — a timeout is what the call site concludes when the
  re-check also answers `PENDING`. See `references/canon-and-glossary.md`'s
  **The chunked wait** for the full contract.
- `citationPreparePrompt(batch, attempt)` (**1.16.1**, replacing 1.16.0's single
  `citationReviewPrompt`) — Claude, `effort:'low'`, `live` only; returns
  `EVIDENCE_READY`/`EVIDENCE_FAILED <index> ATTEMPT <n>`. It opens by re-running
  the fragment's own `--check-batch` validation with `--approve-to`, which
  snapshots the exact bytes that invocation just validated to a create-once,
  attempt-scoped `approved_{index}_attempt_{n}.json` — one read, so nothing can
  change between validating and copying. It then runs `fetch_citation.py` over
  **that snapshot** and reads only the single locally-generated metadata line the
  script prints. It never reads a retrieved body.
- `citationJudgePrompt(batch, attempt)` (**1.16.1**) — Claude, `effort:'high'`,
  `agentType:'literary-translator:citation-judge'` since **#353**, no schema,
  `live` only; returns `CITATIONS_OK`/`CITATIONS_REJECTED <index> ATTEMPT <n>`. It gates whether
  the batch counts as ready at all. It audits the approved snapshot and the
  fetched evidence bodies, and performs no retrieval of its own — it is given no
  retrieval instruction and **no fragment path**. Stated narrowly, because an
  earlier draft of this bullet overclaimed it: the judge *does* receive URLs.
  `index.json`'s `source` field is the cited URL itself, the judge is asked to
  name the offending source in its verdict, and a fetched body can contain any
  URL at all. What the split removes is the *reason* to fetch and the
  *provenance* of every byte it judges — not URLs — which is why its prompt
  marks `source` and `source_form` UNTRUSTED explicitly rather than relying on
  their absence. The tool went separately, in #353: that `agentType` resolves
  to a plugin agent whose frontmatter grants `tools: Read` and nothing else, so
  an agent that could once run a command while reading attacker-authored bodies
  now holds no tool that can open a connection or run one.
  The approval binds bytes rather than a path.
  **This pair costs one MORE `agent()` call per attempt than 1.16.0's single
  reviewer** — that is the whole reason the live ladder moved from
  `1 + 3*(MAX_CITATION_RETRIES+1)` to `1 + 4*(MAX_CITATION_RETRIES+1)`; see the
  batch_agent_cap section above. **1.16.2 (#352)** then moved it again, to
  `1 + (3 + WAIT_CALLS)*(MAX_CITATION_RETRIES+1)` — **19** at the shipped
  `WAIT_CALLS = 3` — for an unrelated reason: not a new review step, but one
  wait becoming worth up to `WAIT_CALLS` agent calls instead of exactly 1. See
  `references/canon-and-glossary.md`'s **Pre-merge citation review**.

**All four of these verdicts are containment-guarded** — the precheck, the wait,
and (1.16.1) both halves of the citation pair — as are mass-translate's
`waitChunkVerdict()` and its `DRAFT_MISSING` fix check, **six sites over the two
templates**. The total is unchanged from 1.16.0 but its composition is not: the
glossary side went from three to four when the citation reviewer split in two,
and the mass-translate side went from three to two when 1.16.1 (#348) collapsed
the two separate wait verdicts into the single `waitChunkVerdict()` parse site
that now serves both chunked wait loops. Counting *call sites in the templates*
rather than *waits in the pipeline* is what makes those two movements cancel.
**1.16.2 (#352)** chunks the glossary and skeptic waits the same way, and the
count survives that only because a chunked wait must keep ONE parse site for
its chunks AND its post-exhaustion re-check — the reason #348 gave originally:
a re-check parsed somewhere else could silently drift into a weaker gate than
the poll it backs up. A port that gave the re-check its own verdict parser
would both break that guarantee and make this total wrong.
Each short-circuits when the sentinel is found anywhere in the
reply as a substring, before `sentinelVerdict()` is consulted.
`sentinelVerdict()` alone matches whole LINES, so a sentinel sharing its line
with anything `trim()` does not strip was skipped.

Five of the six take a FAILURE sentinel via `rejectedAnywhere()`, where a hit
biases toward REJECTING and the guard only ever adds rejections.
`waitChunkVerdict()` is the one site that runs it TWICE — once for `FAILED` and
once for `PENDING`. `PENDING` is not strictly a failure sentinel, but it biases
in the same direction the helper is named for: away from `READY`. Spelling that
second guard `mentionedAnywhere()` was proposed in the 1.16.1 review and
deliberately not taken; the two helpers share one body, so it would have been
behaviour-identical, but `bounded_poll_present.test.py` pins both guards by
helper NAME, and widening that regex would trade a structural guard on a
false-green boundary for a naming nicety. The
`DRAFT_MISSING` fix site is the exception and runs the same containment test in
the opposite direction, through `mentionedAnywhere()`: there the sentinel is the
OK one, so gluing hid a genuine missing-draft report and the loop silently
carried on reviewing an absent draft.

A false hit recovers in-run DETERMINISTICALLY at exactly ONE of the six: the
precheck, which falls through to the dispatch it would have run anyway —
correct whatever made it report `ABSENT`. Of the other five, only the citation
review gets a further attempt inside the run — its ladder's — and the remaining
four cost a later run; since the trigger is the reply's phrasing rather than the
data, either retry is a re-roll rather than a fix. The **citation review is
not** among the DETERMINISTIC recoverers, despite its retry ladder: the ladder
regenerates the fragment while the reviewer's wording is what tripped the guard,
so a regenerated attempt merges only if its fresh reply happens not to re-trip
the guard, and every attempt can burn on the same narration, ending the run
`citation-review-exhausted` with nothing merged (a genuine rejection names each
offending item, its `source` URL and the check it failed; a `lastRejection` that
names none, or reads as an approval, is the guard misfiring and a review-prompt
defect to report rather than re-run).
The glossary wait ends the batch and with it the whole
pass (`reason:"glossary-pass-null"`), mass-translate's review wait blocks that
segment (`reason:"review-timeout"`), its translate wait returns the non-terminal
`reason:"translate-timeout"`, and the fix site returns the equally non-terminal
`reason:"fix-call-failed"`; `select_segments.py` auto-redispatches the last two
next run. Full statement of the rule, the measured glue counts with their
shapes and sets, the two false REDs and the per-site cost of a false reject:
`references/canon-and-glossary.md`'s **Pre-merge citation review**.

Fragment paths are run-scoped (`{{RUN_ID}}` in the path itself), so a stale
fragment from a run with a DIFFERENT `RUN_ID` sits at a different,
unreferenced path — but that alone was never enough, and this is where the
pre-1.2.0 pre-clean's job actually went. A digest-match resume deliberately
reuses the SAME `RUN_ID`, so a prior run's fragments sit at exactly the paths
this run will poll, and `--check-batch` has no mtime, token or freshness
notion to notice. **1.16.0:** `resume_setup.py`'s `write_run_dir()` therefore
wipes stale fragments before the run starts, conditioned on the resume flag —
a fresh run wipes ALL `out_*` and `approved_*` attempts including attempt 0
(an orphaned `glossary/runs/<RUN_ID>` directory can outlive its identity
directory, which is all fresh-ID uniqueness checks), a resume wipes `n >= 1`
plus every snapshot and keeps attempt 0, which the resume-skip optimisation
depends on wholly and which is citation-reviewed either way.

**After every fragment is `READY`, two final calls, never per-batch:**

1. **Final merge** — Claude, `effort:'low'`, **no** `agentType`, **no**
   `schema`: runs `canon_validate.py --merge-batches <frag1> <frag2> …
   --research-mode X --plugin-root {{PLUGIN_ROOT}}` — plus, under `live` only,
   `--citations-reviewed` (**#505**: the writer refuses to freeze a
   `basis:"established"` citation nobody attested a review for, and under
   `live` every batch reaching this call has passed a `CITATIONS_OK` verdict;
   under `offline` no review runs and none is needed, so the flag is correctly
   absent) — the single serialized writer that closes #90 (see
   `references/canon-and-glossary.md` for the merge algorithm). **1.16.0:**
   under `live` those `<frag>` paths are each batch's approved SNAPSHOT
   (`approved_{index}_attempt_{n}.json`), not the mutable attempt fragment, so
   within one run what merges is byte-identical to what the citation reviewer
   audited — on preconditions stated once in `references/canon-and-glossary.md`,
   "What the approved snapshot guarantees, and the preconditions it rests on",
   and deliberately not re-derived here. Under `offline` no reviewer runs and no
   snapshot exists, so they are the attempt paths, an explicit branch rather
   than a global rename. The disk-verify below re-checks the same paths the
   merge was handed.
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
  `estimatedCalls = perBatchCalls * BATCHES.length + 2` (the `+ 2` is the
  fixed final merge + verify pair) and refuses the whole run with
  `{merged: false, reason: "batch-too-large", estimatedCalls, cap}` if it
  exceeds `engine.batch_agent_cap` — the SAME field W5 reads, spliced in as
  the bare-integer `{{BATCH_AGENT_CAP}}` token. **1.16.0: `perBatchCalls` is
  MODE-DEPENDENT**, because the citation-review retry ladder exists only
  under `live`. **1.16.2: it is also parameterized by `WAIT_CALLS`**, since
  one wait is no longer one agent call:

  ```
  live    -- perBatchCalls = 1 + (3 + WAIT_CALLS) * (MAX_CITATION_RETRIES + 1)
             1 precheck, then dispatch + wait + citation prepare + judge per
             attempt, with attempts == MAX_CITATION_RETRIES + 1 in the worst
             case (every review rejects until the ladder is exhausted)
  offline -- perBatchCalls = 1 + (1 + WAIT_CALLS) == 2 + WAIT_CALLS
             precheck + dispatch + wait
  ```

  At the shipped `WAIT_CALLS = 3` and `MAX_CITATION_RETRIES = 2` that is
  **`19 * BATCHES.length + 2` live** and **`5 * BATCHES.length + 2`
  offline**, so a `batch_agent_cap` of 3500 admits ~184 live batches or ~699
  offline ones. The parameterized form is **provably a generalisation rather
  than a rewrite**: substituting `WAIT_CALLS = 1` collapses the two branches
  to `1 + 4*(MAX_CITATION_RETRIES+1) == 13` and to `3`, which are exactly the
  1.16.1 formulas.

  **The offline branch stays MODE-AWARE, but it is no longer the historical
  `3 * BATCHES.length + 2`.** Under `offline`, `canon_validate.py` makes
  `basis:"established"` fatal, so there is provably no citation to review —
  the stage is not skipped for speed, it has nothing to act on.
  `CITATION_REVIEW_ENABLED` is therefore false, which also removes the only
  thing that can REJECT an attempt, so the ladder can never advance past
  attempt 0 and there is exactly one dispatch + wait. Keeping the estimate
  mode-aware still matters for the same reason it always did — a mode-blind
  estimate would charge every offline project for a ladder it cannot execute.
  What changed in 1.16.2 is the wait alone, `3N + 2 → 5N + 2`, and that is a
  real increase in the CEILING in BOTH modes rather than a bookkeeping one: unlike the
  1.16.0/1.16.1 moves, it is not confined to the live ladder, so an offline
  project whose cap was tuned to the old formula will now be refused.

  This is a worst-case CEILING, not a typical-run estimate, and 1.16.2 widens
  the gap between the two — a wait that finds its fragment on the FIRST chunk
  spends 1 call, not `WAIT_CALLS`, and only a wait that exhausts every chunk
  and still needs the re-check spends all 3. Under `live` a batch approved on
  attempt 0 costs `2 + WAIT_CALLS + 2` = 7 calls, not the full ladder, and an
  attempt whose prepare fails short-circuits before the judge for
  `2 + WAIT_CALLS` rather than `3 + WAIT_CALLS`. A resumed batch whose
  fragment already passes `--check-batch` skips its attempt-0 dispatch + wait
  and so comes in strictly under the ceiling — but it does NOT skip the
  citation review, which is why that saving is `1 + WAIT_CALLS` calls and not
  `3 + WAIT_CALLS`. The count is over BATCHES, never candidates-per-batch, so
  a co-located elision pair nudging one batch a candidate or two over its
  nominal `--batch-size` never trips it. A refused run re-plans smaller
  batches (`glossary_batch_plan.py --batch-size`).
- **Resume-skip precheck** — the `batchPrecheckPrompt` bullet above; a valid
  pre-existing fragment for this `{{RUN_ID}}` is trusted and its dispatch +
  wait skipped, so a resumed run never re-pays the codex dispatch for a batch
  already done.

## Skeptic pass dispatch (opt-in, RFC #215 Phase 2)

When `glossary.skeptic_pass.enabled` is set, the W-step sequence gains one additional, self-contained leg after the glossary merge: `suspicion_scan.py` (regenerated every enabled run -- never trusts a stale worklist) -> `skeptic_setup.py` (its own `kind="skeptic"` resume domain, deliberately NOT folded into `resume_setup.py`'s `mass`/`glossary` kinds and NOT a `PLUGIN_BUNDLE_MEMBERS`/`ORCHESTRATION_BUNDLE_MEMBERS` entry) -> `skeptic-pass-wf.template.js` (clones the glossary template's dispatch/poll/merge/verify control flow, including a `batch_agent_cap` preflight of the same SHAPE -- a per-batch term times `BATCHES.length` plus the fixed merge + verify pair -- though no longer the same NUMBER: this template's per-batch term is an unconditional `precheck 1 + dispatch 1 + wait WAIT_CALLS == 2 + WAIT_CALLS`, i.e. `5 * BATCHES.length + 2` at the shipped constants, where glossary's became conditional on its citation review in 1.16.1. **1.16.2 (#352)** moved this term from a flat 3, and since it gates an opt-in advisory pass, that is an OPERATOR-VISIBLE refusal threshold moving under an existing `batch_agent_cap`. Note `skeptic_setup.py`'s step-5 preflight asserts the SAME number independently, before the Workflow ever runs, and the two estimators must move together -- leave one behind and one of them refuses a batch the other admits) -> `skeptic_report.py`, run last and purely for a human to read. Because this whole leg sits behind an opt-in flag and writes only its own new files, a project that never enables it sees zero change to dispatch shape, batching, or cache-key behavior. `skeptic_report.py` itself is never dispatched as part of any batch -- it takes no part in convergence, retries, or the ledger; it is a single, synchronous, read-only render of whatever `skeptic_triage.json` the pass produced.

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
