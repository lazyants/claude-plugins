# Engine loop

This is the single most important reference file in this plugin. It encodes the
**locked, verbatim** engine rules the whole design is built around, generalized only
by replacing "fr"/"ru" with "source language"/"target language." Nothing here is
profile-configurable — read it before any extraction/prompting/reviewing work, per
the pre-read mandate in `SKILL.md`.

The source-proven core is the codex-translate -> deterministic gate ->
codex-review -> Claude-fix loop, proven at ~75-segment scale in the real
reference project (`historiettes-t3/reference/historiettes-mass-translate-wf.reference.js`).
Plugin hardening layered around that core is called out inline where relevant;
do not infer byte-identity for schema fields or control-flow branches explicitly
marked as plugin additions.

## Hard rules index (R1–R7)

Full content lives in the reference doc named next to each rule. This file carries
the full content of **R1** and **R6**; the others are cross-referenced, not repeated,
so there is exactly one place each rule can go stale.

- **R1** Engine-loop role separation — this file, below.
- **R2** False-green gate discipline — `references/false-green-gate.md`.
- **R3** Ledger-based resumability — `references/ledger-and-resumability.md`.
- **R4** Frozen canon discipline, including schema-validated workflow-level
  glossary-pass calls only — `references/canon-and-glossary.md`.
- **R5** Verse policy is configurable, never hardcoded — `references/verse-policy.md`.
- **R6** Word-sense/realia accuracy is first-class — this file, below (a review
  dimension of the engine loop, not a separate subsystem).
- **R7** Workflow-script schema requirement (two explicit categories) —
  `references/workflow-schema-validation.md`.

## R1 — Role separation is a hard rule, never profile-configurable

**codex** is **hardcoded** as both translator and reviewer in every
shipped template (`translate_TASK.template.md`, `review_TASK.template.md`,
`mass-translate-wf.template.js`). No profile field lets a project swap in a
different engine for either role.

For the W5 mass-translate loop the codex translator/reviewer is **launched by the
shipped `codex_job.py` driver** (`assets/scripts/codex_job.py`), NOT by the
`codex:codex-rescue` subagent forwarder. A plain-Claude drive agent launches the
driver DETACHED (`nohup`); the driver runs `codex-companion.mjs task --background`
to a terminal state and, on success, **validates the isolated attempt artifact and
only then atomically promotes** it to the canonical path (#198 — the forwarder
backgrounded the job and returned a stub, so no artifact was ever written and every
segment timed out). codex is still the sole translator/reviewer; Claude only
drives/polls/fixes, never translates. (The glossary pass (R4) is the one codex work
path that KEEPS the direct `codex:codex-rescue` call — it is out of #198's scope.)

- **The translator ONLY translates.** It never reviews its own output beyond
  running the deterministic gate (`validate_draft.py`, see
  `references/false-green-gate.md`) and self-correcting coverage defects it finds
  that way.
- **The reviewer (same subagent type) ONLY reviews.** A single reviewer covers
  BOTH accuracy (omissions, distortions, canon/name fidelity, placeholder
  fidelity, word-sense/realia) AND literary quality (register, idiom, seams,
  rhythm) in ONE pass — never two separate reviewers.
- **Claude (the orchestrating session) ONLY applies fixes.** The exact call shape
  that mechanically enforces this, pinned down once and referenced everywhere
  else this step is mentioned:

  ```
  agent(fixPrompt(seg, round, revObj), {effort: EFFORT})
  ```

  (`EFFORT` is this project's own `engine.effort` value — a configurable
  enum, never hardcoded — substituted once at template-instantiation time
  alongside every other codex/fix effort carrier in this loop; see
  "Effort discipline" below and `references/ledger-and-resumability.md`'s
  dual-injection rule.)

  Explicitly **no `agentType` field.** Omitting `agentType` is what keeps this
  call on the plain Claude/main model rather than codex — the same mechanism (a
  field simply absent) that governs every other plain-Claude call in this
  design; there is no separate "non-codex" flag to set, only the absence of the
  codex one.

  The source-supported invariant from the proven reference is the shape, not a
  copied prompt body: its own `fixPrompt` takes only `(seg, round)`; findings are
  read from the canonical `review_path(seg) = segments/{seg}.review.json` inside
  the fix agent's own prompt text, never passed as a JS argument; and the fix
  result path includes a `DRAFT_MISSING` branch. Do not reintroduce a JS
  `findings` parameter when porting.

  This plugin's `fixPrompt(seg, round, revObj)` is a **deliberate, documented
  departure** from that proven 2-argument shape — the same class of addition as
  the null-review retry-once behavior below, never a silent, unexplained
  deviation. `revObj` is still the third argument (used elsewhere for the
  clean/coverage_ok convergence decision and the review-artifact gate's own
  `--expected-file` content), but **1.3.6 (#132 option b)**: the fix agent's
  prompt no longer splices `revObj`'s findings into its own text at all — it
  instead instructs the agent to READ the canonical
  `review_path(seg) = segments/{seg}.review.json` from disk itself and apply
  every entry in its on-disk `findings[]` array. `review_ready.py` already
  token-validated this exact file fresh THIS round before the fix call was
  ever dispatched. In the SUPPORTED single-orchestrator obedient model the
  review for `<seg>` round R is written once by its own detached
  `codex_job.py --kind review` driver — not concurrently with the fixer's read —
  so this read is race-free. OUTSIDE that model the guarantee is narrower:
  `review_ready.py` reads `review.json` in-memory ONCE and checks only
  schema + expected-token + current-`draft_sha1` (NOT content quality or writer
  identity, unlike translate's `validate_draft`), so a schema-valid
  same-token/current-hash FORGERY that is STABLE through acceptance can PASS (the
  review-path disobedient-writer residual); a mutation after `review_ready`'s read
  BEGINS and before it returns READY — or between READY and any later canonical
  re-read to consume the verdict — is the mid-gate TOCTOU residual, and a rewrite
  after the verdict is consumed is the post-accept residual (all §6, out of scope).
  This closes the review-artifact gate's residual risk for the fix step by a DIFFERENT
  mechanism than the pre-1.3.6 design intended: not because there is no
  second on-disk artifact to drift against (there is — `revObj`'s own
  transcribed copy, still used for the gate's `--expected-file` and the
  convergence decision), but because the fix step no longer CONSUMES that
  transcribed copy for its findings at all — a transcription slip in it can
  no longer reach the fixer, regardless of what the gate's own compare does
  or doesn't catch. `review_path(seg)` stays on disk, unchanged, for
  audit/back-compat and for `ledger_update.py`'s
  `reviewed_draft_sha1`/`dispatch_token` binding check and later audit-trail
  inspection — those consumers still read it — and now the fix step reads it
  too, independently.

  The fix prompt's job is **constrained to editing an EXISTING
  `draft_path(seg) = segments/{seg}.draft.json` file** that codex already
  produced and that has already passed `draft_ready.py`. It never originates new
  translated content from source text, never authors a first-draft translation,
  and never overrides a reviewer's accuracy verdict with its own literary
  judgment without a documented reason. (`draft-missing` is a distinct ledger
  status that exists specifically for the case where a fix step would otherwise
  be tempted to author a missing translation — see
  `references/ledger-and-resumability.md`.)

### The per-segment cycle

Translate → readiness poll → review/fix loop → confirming final review, in that
order, preserved from the proven reference; plugin additions are labeled in the
steps below:

1. A plain-Claude **drive agent** (`effort: 'low'`, no `agentType`) launches the
   shipped `codex_job.py --kind translate` driver DETACHED (`nohup`) and returns
   `DISPATCHED <seg> <DISP>` — it does NOT itself translate. The driver runs codex
   `task --background` (with `--effort <engine.effort>` as a real CLI flag), and on a completed
   job **validates the isolated attempt** (`draft_ready.py`/`validate_draft.py` on
   the `--candidate-file` attempt) and only then **atomically promotes** it to
   `draft_path(seg)` carrying a run-scoped `dispatch_token`. This is the one
   deliberate exception to R7's "codex accuracy calls need a schema" framing: the
   translate work is intentionally schema-less, gated instead by file output plus
   the Workflow's own on-disk ACCEPT gate re-running `draft_ready.py`/
   `validate_draft.py` on the CURRENT canonical — see
   `references/false-green-gate.md` and `references/workflow-schema-validation.md`.
   (Before #198 this was a direct `agent({agentType:'codex:codex-rescue'})`
   fire-and-forget call; the forwarder backgrounded codex and returned a stub, so
   the artifact was never written — the driver now owns the launch deterministically.)
2. A **low-effort wait/poll step** (`draft_ready.py --expect-token` in a
   bounded bash polling loop, called at `effort: 'low'`) blocks the review
   loop from starting until the async translator has actually delivered a
   complete, current-run-tokened file. This specifically prevents a Claude
   fix-agent from ever ending up authoring a missing translation, since
   "codex only translates" would otherwise be silently violated the moment a
   fix step ran against a nonexistent/partial/stale-run draft.
3. Up to `engine.max_fix_rounds` rounds of **review point (detached-driver dispatch
   → bounded wait → schema-validated consume; the DISPATCH half is now the
   `codex_job.py --kind review` driver launched by a plain-Claude drive agent, NOT
   an `agentType: 'codex:codex-rescue'` call — codex still reviews, launched via the
   driver at `--effort <engine.effort>`) → Claude fix (`effort: <engine.effort>`, no `agentType`) →
   re-review**, exiting early the moment a review reports `clean && coverage_ok`.
   Each round's review point runs four functions in sequence —
   `reviewDispatchPrompt` (the drive agent that launches the detached
   `codex_job.py --kind review` driver, schema-less; the driver validates the
   isolated review attempt via `review_ready.py --candidate-file` before atomically
   promoting `review_path(seg)` with `dispatch_token`) → `reviewWaitPrompt` (Claude,
   bounded poll) → `readReviewPrompt` (Claude, `schema: REVIEW_SCHEMA`) →
   `verifyReviewArtifactPrompt` (Claude, flat `schema: REVIEW_ARTIFACT_SCHEMA`) — see
   `references/orchestration-and-batching.md` for the exact call shapes.
   `REVIEW_SCHEMA` matches `review.schema.json`'s four verdict fields:
   `{clean: boolean, coverage_ok: boolean, findings: [{loc, severity, issue,
   suggest}], draft_sha1: string}`, all fields required,
   `additionalProperties: false`, and **no `verse_status` field**. `draft_sha1`
   is a deliberate plugin addition on top of the proven shape: the reviewer
   computes it **before** reading the draft by shelling out
   `python3 {{DURABLE_ROOT}}/scripts/draft_sha1.py <seg>` — never raw
   `sha1sum`. See `references/workflow-schema-validation.md` for the full
   shipped schema.
   - **Timeout and shared-retry handling** (1.2.0): `reviewWaitPrompt`
     timing out exits immediately as `blocked review-timeout`, no retry — a
     genuine failure to even get a dispatched review to complete. Once
     `READY`, the CONSUME pair (`readReviewPrompt` + `verifyReviewArtifactPrompt`)
     shares **one retry budget**: a null read OR a `match:false` check
     retries the SAME `(read, check)` pair once, fresh; still failing →
     `blocked review-null` or `blocked review-artifact-mismatch`,
     whichever triggered it. This replaces the pre-1.2.0 shape (retrying
     the whole review dispatch on a mismatch) — the DISPATCH call already
     wrote a complete, token-scoped artifact by the time `READY` fires, so
     re-dispatching a fresh codex review on a mismatch would burn a call
     fixing nothing a fresh read/check pair wouldn't also fix. See
     `references/ledger-and-resumability.md` for the full ledger-status
     treatment and `references/workflow-schema-validation.md` for the
     retry-budget mechanics.
   - **Review-artifact gate**, the `verifyReviewArtifactPrompt` half of the CONSUME
     pair, after every non-null review read (including the mandatory final
     confirming review), before any fix or terminal ledger decision: the
     prompt embeds `revObj`'s canonical JSON text (the object
     `readReviewPrompt` just returned) and instructs the agent to write it
     verbatim to a scratch `--expected-file <path>`, invoke
     `python3 {{DURABLE_ROOT}}/scripts/review_artifact_check.py <seg>
     --expected-file <path>`, and relay the script's own printed
     `{match:true}`/`{match:false, mismatch_detail}` line. The script, not the
     agent, projects both `review_path(seg)` (which now also carries
     `dispatch_token`) and the expected file down to the four verdict
     fields — and (1.3.6, #132) each `findings[]` element further down to
     `{loc, severity}`, dropping the free-text `issue`/`suggest` bodies so a
     transcription slip in prose can't false-block an already-validated
     review — canonicalizes both projections with sorted-key JSON, and
     byte-compares those canonical forms. On `match:false`, the shared
     retry budget above
     fires; still mismatching after the retry → `blocked` with reason
     `review-artifact-mismatch`. Known residual: the `--expected-file` is
     still written by an LLM agent, so this gate's deterministic comparison
     can compare against the wrong expected file if that write is wrong.
     Since `fixPrompt` now reads `review_path(seg)` itself (1.3.6/#132
     option b, see R1 above), that residual is confined to
     `ledger_update.py`'s later `reviewed_draft_sha1`/`dispatch_token`
     binding check and audit-trail inspection, not the fix step's own input
     — the fixer's own disk read closes that question, independent of
     whatever this gate's own comparison concludes. Full mechanics:
     `references/ledger-and-resumability.md`.
4. **Always one final confirming review after the cap**, even if the loop exited
   because of the cap rather than convergence — a fix that goes unverified is
   the single most common source of a silently-broken "done" segment.
5. Non-convergence returns a structured `{seg, converged: false, reason, rounds,
   lastFindings}` result rather than throwing or silently marking done — the
   orchestrating session is responsible for surfacing every `non_converged`/
   `blocked` segment, never silently shipping a partial book.

### No sub-chunking in v1

`mass-translate-wf.template.js` operates only on whole `seg` items. v1 has no
`chunk_id`/`owned_ids[]`/`context_only_ids[]` contract, no chunk segpack naming,
no chunk-readiness polling, no merge-validation path in `validate_draft.py`, and
no per-chunk ledger status. `draft.schema.json` carries no chunk fields. A
segment too large to translate as one unit is handled by the `max_segment_words`
fatal extraction preflight, not by an under-specified fan-out.

### Effort discipline

**Every ACCURACY-BEARING codex work-call in this loop is driven by
`engine.effort`** — a configurable enum (`low`/`medium`/`high`/`xhigh`,
default `high`; excludes `none`/`minimal` as nonsensical for accuracy work,
and excludes `max`, which codex-companion's own `--effort` flag rejects
outright). For W5 translate and review this is `--effort <engine.effort>`
passed to codex as a REAL CLI flag on the `codex_job.py` driver's `task`
launch (the plain-Claude drive agent that launches the detached driver is
itself `effort: 'low'` — it only dispatches, it does not translate/review).
`fixPrompt` (plain Claude) and the glossary-pass `batchDispatchPrompt` (still
`agentType: 'codex:codex-rescue'`) keep `effort: <engine.effort>` as their
own agent option — all three carriers read the SAME resolved value from one
profile knob, burned in at a single template instantiation, never
independently pinned (see `references/ledger-and-resumability.md`'s
dual-injection rule). No such call inherits a session-level xhigh/ultracode
effort. This is both a literal, profile-driven requirement and the fix for
a known `max_tokens` wedge on synthesis-heavy agent configs. An optional
`engine.model` threads to the two `codex_job.py` driver launches
(translate/review) only — never to the glossary pass or the fix step,
where a codex model id is not meaningful (see `assets/profile.example.yml`).

The purely mechanical, no-judgment calls are deliberately `effort: 'low'` — this
is **not** an oversight to be "fixed" to high:
- every W5 translate/review **drive/dispatch** step (the plain-Claude agent that
  writes the codex task-file and launches the detached `codex_job.py` driver, then
  returns `DISPATCHED <seg> <DISP>`) — the accuracy-bearing effort lives on codex's
  own `--effort <engine.effort>` flag, not on this dispatcher,
- every WAIT/readiness poll (`waitPrompt`/`draft_ready.py`,
  `reviewWaitPrompt`/`review_ready.py`, `batchWaitPrompt`/
  `canon_validate.py --check-batch`),
- every CONSUME call (`readReviewPrompt`, the review-artifact gate —
  `verifyReviewArtifactPrompt`/`review_artifact_check.py` — and the glossary
  disk-verify call),
- the ledger-bookkeeping calls (`recordLedgerPrompt`/`mergeLedgerPrompt`,
  see `references/ledger-and-resumability.md`).

## R6 — Word-sense/realia accuracy is first-class

Word-sense/realia accuracy is an explicitly named review dimension, not folded
into generic "accuracy." The reviewer prompt must explicitly ask **"does this
word mean what it meant in period/context, not what it means today."** The style
bible must carry a "traps" section (`E-traps` in the source project) for known
false-friend words discovered during the run.

`translate_TASK.template.md` and `review_TASK.template.md` both carry this
placeholder callout:

```
<!-- ERA/DOMAIN TRAP EXAMPLE — replace with this book's own discovered traps, e.g.
guéridon=refrain-song not side-table for a 17th-c. French memoir -->
```

so a future project doesn't ship the old book's trap by copy-paste accident.

This callout is **deliberately NOT** one of `style_bible.md`/`PLAN.md`'s
`LT_REQUIRED_FILL_BEGIN`/`END`-gated MUST-fill sections. This is intentional, not
an oversight: unlike the style bible's voice/register description or `PLAN.md`'s
project notes — both fully knowable at scaffold time, before any translation
happens — traps are discovered *during* the run. There is nothing real to fill
in yet at project-scaffold time, so gating extraction on this callout being
replaced would block every fresh project on content that doesn't exist yet.

The genuine risk this callout exists to prevent is narrower and different in
kind: not "was a new trap ever added" but "did THIS SPECIFIC book's own example
— `guéridon=refrain-song` — survive a copy-paste into an unrelated project."
`scripts/scaffold_validate.py` is extended with a **separate, marker-free check**
(no `LT_REQUIRED_FILL` span involved, since nothing is required to be filled in):
it FATALLY rejects `translate_TASK.md`/`review_TASK.md` if the literal substring
`guéridon=refrain-song` still appears anywhere — never a requirement that a new
trap be entered before the run starts. A second, complementary check catches
survivors the exact substring alone would miss — a separator-mangled pairing
(`guéridon = refrain-song`) or the pairing deleted while its explanatory
sentence survives — by checking, scoped to the callout's own HTML comment,
whether the label and the content word `guéridon` still co-occur there.

## Foreign-language insertions

Foreign-language insertions (Latin, Old French, or any embedded third language)
get an **in-text gloss, never a notes-only translation**. The source project hit
this as a real reviewer-caught defect (translation hidden only in `notes[]`,
never visible to the reader). Kept as a hard rule, generalized to "any embedded
third-language text," not just Latin/Old French.

## Non-convergence is a status, not a silent failure

One rule, stated identically everywhere it's discussed. After the round cap
(`engine.max_fix_rounds`) is reached and the mandatory final confirming review is
still not clean, the segment's status is simply `non_converged` — **full stop,
no further automated review/adjudication pass.**

`blocked` is a **separate, distinct failure mode** reached only via
review-timeout, review-null (after one retry), draft-missing, or
review-artifact-mismatch (after one retry) paths — never via cap-exhaustion.

Resolving a `non_converged` segment (a fresh look, a manual fix, a re-run with
adjusted style-bible guidance, etc.) is out of scope for the automated workflow
template; it's a human-escalation item, exactly like `blocked`.

**Re-entry into automated dispatch is NOT automatic** for either status — the
classification logic in `select_segments.py` only re-tests cache-key staleness
for `converged` segments (converged → `stale` on a hash mismatch); it never
re-tests `non_converged`/`blocked` segments the same way, so even a style-bible
edit would not organically flip a `human_escalation` segment back into `SEGS`.
The one, explicit, auditable path back in is:

```
select_segments.py --only-segs <id,...>
```

naming the resolved segment. This forces the named segment into `SEGS`
regardless of its `human_escalation` classification, and its re-dispatch through
the normal `in_progress` call site naturally overwrites its stale terminal
fragment — no separate reset/clear script is needed. See
`references/ledger-and-resumability.md` for the full ledger mechanics.

**v1 delivery must refuse to mark the audit package
(`output.v1_scope: segment_drafts_and_audit`) complete while ANY item remains
`non_converged` OR `blocked`** — both are "not done," not just `blocked`; a
future assembly script must also hard-fail on either status when it exists.
