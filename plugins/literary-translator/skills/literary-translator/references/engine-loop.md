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

`codex:codex-rescue` is **hardcoded** as both translator and reviewer in every
shipped template (`translate_TASK.template.md`, `review_TASK.template.md`,
`mass-translate-wf.template.js`). No profile field lets a project swap in a
different engine for either role.

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
  agent(fixPrompt(seg, round, revObj), {effort: 'high'})
  ```

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
  deviation. `revObj` is the third argument: the SAME schema-validated object
  `reviewPrompt` already returned this round, still in the JS's own in-memory
  state, never re-read off disk. The fix agent's prompt-generation logic embeds
  `revObj`'s literal canonical-JSON text directly (the identical deterministic
  splice mechanism `verifyReviewArtifactPrompt` uses — see
  `references/ledger-and-resumability.md` and
  `references/workflow-schema-validation.md` for the review-artifact gate this
  interacts with) rather than instructing the agent to re-read
  `review_path(seg) = segments/{seg}.review.json` from disk. This closes the
  review-artifact gate's residual risk structurally for the fix step
  specifically: there is no longer a second on-disk artifact for the fix step's
  own input to independently drift against. `review_path(seg)` still stays on
  disk, unchanged, for audit/back-compat and for `ledger_update.py`'s
  `reviewed_draft_sha1` binding check and later audit-trail inspection — those
  consumers still read it — but the fix step's own correctness no longer depends
  on it.

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

1. `agent(translatePrompt(seg), {agentType: 'codex:codex-rescue', effort:'high'})`
   — fire-and-forget; the translator prompt itself instructs the agent to
   self-validate coverage via the deterministic gate (`validate_draft.py`) before
   returning. This is the one deliberate exception to R7's "codex accuracy calls
   need a schema" rule: the translate call is intentionally schema-less, gated
   instead by file output plus `draft_ready.py`/`validate_draft.py` — see
   `references/false-green-gate.md` and `references/workflow-schema-validation.md`.
2. A **low-effort wait/poll step** (`draft_ready.py` in a bash polling loop, called
   at `effort: 'low'`) blocks the review loop from starting until the async
   translator has actually delivered a complete file. This specifically prevents
   a Claude fix-agent from ever ending up authoring a missing translation, since
   "codex only translates" would otherwise be silently violated the moment a fix
   step ran against a nonexistent/partial draft.
3. Up to `engine.max_fix_rounds` rounds of **review (schema-validated,
   `agentType: 'codex:codex-rescue'`, `effort: 'high'`) → Claude fix
   (`effort: 'high'`, no `agentType`) → re-review**, exiting early the moment a
   review reports `clean && coverage_ok`. Each round's review call carries
   `REVIEW_SCHEMA`, matching `review.schema.json` 1:1:
   `{clean: boolean, coverage_ok: boolean, findings: [{loc, severity, issue,
   suggest}], draft_sha1: string}`, all fields required,
   `additionalProperties: false`, and **no `verse_status` field**. `draft_sha1`
   is a deliberate plugin addition on top of the proven shape: the reviewer
   computes it **before** reading the draft by shelling out
   `python3 {{DURABLE_ROOT}}/scripts/draft_sha1.py <seg>` — never raw
   `sha1sum`. See `references/workflow-schema-validation.md` for the full
   shipped schema.
   - **Null-review handling** (a deliberate plan enhancement, confirmed absent
     from the reference script itself): if a schema-validated review call still
     returns null, retry that SAME review call once, fresh. If the retry also
     returns null, the segment exits immediately as `blocked` rather than
     consuming a fix round on a segment with no real verdict to act on. The real
     reference script has no such retry — it marks `converged: false, reason:
     'review-null'` on the first null. See `references/ledger-and-resumability.md`
     for the full ledger-status treatment.
   - **Review-artifact gate**, after every non-null review verdict (including the
     mandatory final confirming review), before any fix or terminal ledger
     decision: `agent(verifyReviewArtifactPrompt(seg, revObj), {effort:'low',
     schema: REVIEW_ARTIFACT_SCHEMA})`. The prompt embeds `revObj`'s canonical
     JSON text and instructs the agent to write it verbatim to a scratch
     `--expected-file <path>`, invoke
     `python3 {{DURABLE_ROOT}}/scripts/review_artifact_check.py <seg>
     --expected-file <path>`, and relay the script's own printed
     `{match:true}`/`{match:false, mismatch_detail}` line. The script, not the
     agent, canonicalizes `review_path(seg)` and the expected file with
     sorted-key JSON and byte-compares those canonical forms. On `match:false`,
     retry the original review call once, then `blocked` with reason
     `review-artifact-mismatch` if it still mismatches. Known residual: the
     `--expected-file` is still written by an LLM agent, so this gate's
     deterministic comparison can compare against the wrong expected file if that
     write is wrong. Since `fixPrompt` now works from in-memory `revObj` (see R1
     above), that residual is confined to `ledger_update.py`'s later
     `reviewed_draft_sha1` binding check and audit-trail inspection, not the fix
     step's own input. Full mechanics:
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

**Every ACCURACY-BEARING agent call in this loop uses `effort: 'high'`** —
translate, review, fix, and glossary-pass batches. No such agent inherits a
session-level xhigh/ultracode effort. This is both a literal requirement and the
fix for a known `max_tokens` wedge on synthesis-heavy agent configs.

The purely mechanical, no-judgment calls are deliberately `effort: 'low'` — this
is **not** an oversight to be "fixed" to high:
- the translator-readiness poll (`waitPrompt`/`draft_ready.py` wait loop),
- the review-artifact gate (`verifyReviewArtifactPrompt`/
  `review_artifact_check.py`),
- the ledger-bookkeeping call (`recordLedgerPrompt`, see
  `references/ledger-and-resumability.md`).

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
trap be entered before the run starts.

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

`blocked` is a **separate, distinct failure mode** reached only via review-null
(after one retry), draft-missing, or review-artifact-mismatch (after one retry)
paths — never via cap-exhaustion.

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
