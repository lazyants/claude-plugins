# Workflow schema validation

This is the full content of **R7 — Workflow-script schema requirement**
(see `SKILL.md`'s R1–R7 index). It covers three things that are all facets
of the same underlying constraint: every `codex:codex-rescue` call that
needs a guaranteed structured verdict must be dispatched as a **Workflow-tool
`agent()` call carrying a `schema` param**, never as a bare `Agent()` call and
never nested inside another agent's own turn.

## Why: a bare `Agent()` call to codex-rescue is unreliable

This is the single most load-bearing lesson behind this plugin's entire
Workflow-script design, and it is empirical, not theoretical: on the real
source project, **11 named teammates each nesting their own schema-less
`codex:codex-rescue` review call silently wedged for ~10 real hours with
zero ambient monitoring**, while the **same review step hoisted to a
workflow-level `agent(..., {schema: REVIEW_SCHEMA})` call worked reliably
across 28+ segments**.

The mechanism, precisely stated:

- A raw `Agent(subagent_type: "codex:codex-rescue")` call — top-level or
  nested, it makes no difference — can return an **ambiguous background-job
  string** instead of a real structured verdict. Nothing forces it to
  actually complete the judgment call and hand back a usable result; it can
  report that a task was started and leave it there.
- Only a **Workflow-tool `agent()` call carrying a `schema` param** has
  automatic **retry-until-valid** built into the harness: it forces a
  StructuredOutput tool call, an ambiguous string fails validation against
  the schema, and the model is forced to retry until it actually produces a
  conforming object.
- This is **not merely a defense against the specific nested-dispatch
  incident** that first surfaced the problem — it is a general property of
  how codex-rescue calls behave in this environment, regardless of who calls
  them or how. Schema validation is what makes the verdict real, not
  nesting depth.

The concrete rule this produces for every workflow template in this plugin:

- `reviewFixLoop()` (the per-segment translate → review → fix loop, see
  `references/engine-loop.md`) is a **plain async function called from the
  top-level `pipeline()`** — never a prompt that tells a sub-agent to itself
  go call `codex:codex-rescue`. The orchestration logic lives in the
  Workflow script's own JS control flow, not delegated into an agent's own
  turn.
- Every codex-rescue call the loop makes — translate, review, the
  glossary-pass batch call — is dispatched via the Workflow tool's `agent()`
  function, at the top level, from that JS.

## The shared codex work-call pattern (1.2.0): dispatch → bounded wait → schema-validated consume

Earlier revisions had review and the glossary-pass batch call codex directly
with a `schema` param, betting on the harness's retry-until-valid to force a
real verdict out of an async job. That bet had a real gap (#97): a
forwarder-detached codex job can still hang the workflow indefinitely on that
one `await` — a `schema` param does nothing to *bound how long* the call sits
unresolved, it only shapes what comes back once it does. Only translate's
original fire-and-forget-plus-poll shape was ever actually bounded. 1.2.0
generalizes that proven shape to review and the glossary batch instead of
inventing a second discipline for them:

1. **DISPATCH** — codex (`agentType:'codex:codex-rescue'`, pinned),
   **schema-less**, fire-and-forget. The agent does the work and writes its
   result **atomically** to a `{{RUN_ID}}`-scoped path carrying a
   `dispatch_token`, self-validates its own shape via a shipped script, and
   returns a short sentinel string (`REVIEWED {seg}`, `FRAGMENT {index}`,
   `DONE {seg}`). The Workflow JS never depends on this call's own return
   value — see `references/orchestration-and-batching.md` and
   `references/ledger-and-resumability.md` for the `{{RUN_ID}}`/token
   mechanics this closes (#90, the resume-integrity gate).
2. **WAIT** — Claude, `effort:'low'`, **no** `agentType`, **no** `schema`: a
   **bounded** bash polling loop (the same shape `waitPrompt`/
   `draft_ready.py` already used for translate) against a readiness script
   that fully validates the on-disk artifact, including its run-scoped
   token. Returns `READY`/`TIMEOUT` as plain text.
3. **CONSUME** — Claude, schema-validated: reads the artifact and returns it
   (`readReviewPrompt` → `REVIEW_SCHEMA`), then a **separate**
   schema-validated call runs the deterministic cross-check script against
   the workflow-held return (`verifyReviewArtifactPrompt` → flat
   `REVIEW_ARTIFACT_SCHEMA`; glossary's disk-verify call → flat
   `CANON_VERIFY_SCHEMA`).

This bounds the **forwarder-hollow-return** and **detached-job-hang** failure
modes for every codex work-call, not just translate. It does **not** bound a
*synchronous* codex block-and-hang on the DISPATCH `await` itself — translate
already carried that residual, and review/glossary batch now carry it too;
see `references/gotchas.md`.

`fixPrompt` is the one deliberate exception to the whole pattern: it stays a
plain Claude call (no `agentType`, no `schema`, no dispatch/wait/consume
split) — it can't forward-detach (it isn't codex), and the poll pattern has
nothing to bound since a fix call doesn't make a blocking codex `await` in
the first place.

## What carries a `schema` param now, and why

Not every `agent()` call in this design carries a `schema` param, and
conflating "codex work-call" with "schema-validated call" is a mistake this
1.2.0 revision corrected explicitly — the two are now largely disjoint (an
earlier draft's "every codex call needs a schema" framing is exactly what
produced the #87 top-level-`oneOf`/`array` schemas that made review and the
glossary batch call fail outright — see below).

### DISPATCH calls — codex, schema-less, by design

`translatePrompt`, `reviewDispatchPrompt`, `batchDispatchPrompt` (glossary).
All three are `agentType:'codex:codex-rescue'`, fire-and-forget, and carry
**no** `schema` param — a schema on a call whose return the workflow never
reads only slows down a job that's already bounded by the WAIT step's own
poll timeout. Structured verdicts come from the on-disk artifact these calls
write, read back by a later CONSUME call instead:

```
agent(translatePrompt(seg), {agentType: 'codex:codex-rescue', effort: 'high'})
agent(reviewDispatchPrompt(seg, round), {agentType: 'codex:codex-rescue', effort: 'high'})
agent(batchDispatchPrompt(batch), {agentType: 'codex:codex-rescue', effort: 'high'})
```

### WAIT calls — Claude, schema-less, bounded poll

`waitPrompt` (translate), `reviewWaitPrompt`, `batchWaitPrompt` (glossary).
Plain Claude, `effort:'low'`, no `agentType`, no `schema` — a bounded
`for i in $(seq 1 45)`-shaped bash polling loop against a readiness script
(`draft_ready.py --expect-token`, `review_ready.py --expect-token`,
`canon_validate.py --check-batch`), returning `READY`/`TIMEOUT` as plain
text.

### CONSUME calls — Claude, schema-validated

`readReviewPrompt` (`schema: REVIEW_SCHEMA`), `verifyReviewArtifactPrompt`
(`schema: REVIEW_ARTIFACT_SCHEMA`, now flat — see below), glossary's
disk-verify call (`schema: CANON_VERIFY_SCHEMA`, new). These exist to force
a real structured object out of a call whose entire job is "read this file
and relay it" or "run this script and relay its printed line" — the same
retry-until-valid guarantee this file's opening section describes, now
applied to a Claude call reading a codex-written artifact rather than to the
codex call itself:

```
agent(readReviewPrompt(seg), {effort:'low', schema: REVIEW_SCHEMA})
agent(verifyReviewArtifactPrompt(seg, revObj), {effort:'low', schema: REVIEW_ARTIFACT_SCHEMA})
agent(glossaryVerifyPrompt(fragments), {effort:'low', schema: CANON_VERIFY_SCHEMA})
```

### Non-codex mechanical schema-confirmation calls (unchanged in kind)

`recordLedgerPrompt`, `mergeLedgerPrompt`. Still schema-validated
(`LEDGER_WRITE_SCHEMA`, `LEDGER_MERGE_SCHEMA` — both now flat, see below),
still not `agentType:'codex:codex-rescue'`, still `effort:'low'`, for the
same reason as before: verifying that a shell script's own JSON stdout,
plus the required deterministic follow-up checks, produced a well-formed and
complete mechanical confirmation — not forcing a real verdict out of an
ambiguous codex async job:

```
agent(recordLedgerPrompt(seg, fields), {effort:'low', schema: LEDGER_WRITE_SCHEMA})
agent(mergeLedgerPrompt({expectedSegs: SEGS}), {effort:'low', schema: LEDGER_MERGE_SCHEMA})
```

`recordLedgerPrompt` must independently re-read the fragment file
`ledger_update.py` claimed to write, recompute its sha1, and compare that to
`fragment_sha1` before returning success. After a `success:true` return, the
Workflow JS itself also checks that the returned `fragment_path`'s segment
component matches `seg` and that returned `status` matches `fields.status`;
a mismatch is handled as `reason:'ledger-write-mismatch'`, never retried
through the same channel.

`mergeLedgerPrompt({expectedSegs: SEGS})` uses the exact `SEGS` array emitted
by `select_segments.py`, then independently re-reads `ledger.json` and
verifies a completeness/subset condition: every expected ID must be present,
while extra keys from previous batches are allowed. It is never an exact
key-set equality check.

### Calls with neither `agentType` nor `schema`

`fixPrompt` (unchanged — see above). The glossary final-merge call
(`canon_validate.py --merge-batches`) — Claude, `effort:'low'`, no
`agentType`, no `schema`: its job is to shell out and its own exit code is
the signal; nothing downstream reads a structured return from this call,
only from the disk-verify call that follows it. See
`references/canon-and-glossary.md` for the full glossary-pass flow.

## #87: an `agent()` schema is a tool `input_schema` — plain top-level object only

The Workflow tool's `agent(..., {schema})` param becomes a tool-use API
`input_schema`. The API requires that to be a top-level `type:"object"` — it
does **not** accept a top-level `oneOf`/`allOf`/`anyOf`, and would not
enforce an `if`/`then` discriminator even if it did. Before 1.2.0, three
literals violated this directly: `CANON_BATCH_SCHEMA` was a top-level
`array` (blocking every glossary dispatch with an HTTP 400), and
`REVIEW_ARTIFACT_SCHEMA`/`LEDGER_WRITE_SCHEMA`/`LEDGER_MERGE_SCHEMA` were
each a top-level `oneOf` (blocking mass-translate). This is `#87`, and the
fix has two parts that must not be conflated:

1. **Flatten every agent-facing literal** to `type:"object"`, no top-level
   combinator, `additionalProperties:false`, with a *relaxed union* of the
   fields from every branch the old `oneOf` used to discriminate, each field
   individually optional except a single always-required discriminator
   (`success`/`match`/`verified`).
2. **Branch discrimination does not disappear — it moves.** The on-disk
   schemas (`ledger-write-confirmation.schema.json`,
   `ledger-merge-confirmation.schema.json`, `review-artifact-check.schema.json`)
   **stay strong `oneOf`** and validate the *script's* real output at
   runtime (`ledger_merge.py` self-validates its own stdout, for instance).
   The **exact-key-set JS guard**, at every flat-schema consume site, is what
   re-establishes discrimination on the *agent-relayed* object — see
   `references/ledger-and-resumability.md` for the guard field sets. A flat
   schema without its paired JS guard is not safe to trust on its own: it
   would accept `{success:true, error:"x"}` as a success just as readily as
   a genuine one.

`CANON_BATCH_SCHEMA` itself is **deleted**, not flattened — the glossary
batch dispatch is schema-less fire-and-forget now (see above), so there is
no agent-facing literal for it at all; the on-disk `canon-batch.schema.json`
stays an `array` and is validated only by `canon_validate.py --check-batch`,
never by an `agent()` call.

## The TDZ gotcha: declare schema literals ABOVE the `pipeline()` call

Workflow scripts in this plugin are **self-contained, with no imports** —
they use only the Workflow tool's provided globals (`agent()`, `pipeline()`,
`log()`, `args`) plus `python3` shelled out via agent prompts for the
deterministic checks. This is a hard structural constraint, not a style
preference: Workflow scripts in this execution model can't reliably load
external JSON/JS modules, so every JSON Schema a template uses must be an
**inline literal object in the script itself**.

That literal object **must be declared above the `pipeline()` call that
references it**. A schema declared after its first use silently no-ops due
to temporal-dead-zone (TDZ) semantics in this execution model — this is
`gotcha_workflow_const_tdz_silent_fail`: the schema binding exists but is not
yet initialized at the point `pipeline()` actually runs, so the validation
that should be forcing retry-until-valid quietly never fires, and there is
no error to point at the cause. Declaration order in the file is load-bearing.

Concretely, every workflow template in this plugin has this shape:

```js
// Schema literals FIRST — every one of these must be fully declared
// before pipeline() is ever called below.
const REVIEW_SCHEMA = { /* ... */ };
const REVIEW_ARTIFACT_SCHEMA = { /* ... */ };
const LEDGER_WRITE_SCHEMA = { /* ... */ };
const LEDGER_MERGE_SCHEMA = { /* ... */ };

// ... prompt-builder functions (translatePrompt, reviewDispatchPrompt, etc.) ...

// pipeline() is called LAST, after every schema it references above
// is already fully initialized.
pipeline(SEGS, (seg) => reviewFixLoop(seg));
```

`mass-translate-wf.template.js` declares `REVIEW_SCHEMA`,
`REVIEW_ARTIFACT_SCHEMA`, `LEDGER_WRITE_SCHEMA`, and `LEDGER_MERGE_SCHEMA`
above its `pipeline()` call for the same TDZ reason.
`glossary-pass-wf.template.js` declares its own `CANON_VERIFY_SCHEMA` above
its own, separate `pipeline()` call for the identical reason — it is a
second, smaller workflow script, not a shared module, so it repeats its own
declare-above-use discipline independently. `CANON_BATCH_SCHEMA` is gone
(see the #87 section above) — the glossary batch dispatch carries no schema
at all now.

The exact shapes of the schema literals still declared as `agent()`
`schema` params, matching the shipped `assets/schemas/*.json` files' field
sets (not their combinator structure — see the #87 section above for why
these are flat while the on-disk files stay strong `oneOf`):

- **`REVIEW_SCHEMA`** — unchanged, matches `review.schema.json`'s four
  verdict fields projected out of the five-field on-disk shape (which now
  also carries `dispatch_token` — see
  `references/ledger-and-resumability.md`): `{clean: boolean, coverage_ok:
  boolean, findings: [{loc, severity, issue, suggest}], draft_sha1: string}`,
  all fields required, `additionalProperties: false`. No `verse_status`
  field, no `dispatch_token` field — verse issues surface as ordinary
  `findings[]` entries (`loc: "VERSE:{vid}"`); verse coverage is exclusively
  the deterministic validator's job, never review judgment. `draft_sha1` is
  a deliberate plugin addition over the real reference schema: the reviewer
  computes it itself, before reading the draft, by shelling out to
  `python3 {{DURABLE_ROOT}}/scripts/draft_sha1.py <seg>`.
- **`REVIEW_ARTIFACT_SCHEMA`** — now flat: `{type:"object",
  additionalProperties:false, required:["match"], properties:{match:
  {type:"boolean"}, mismatch_detail:{type:"string"}}}`. The on-disk
  `review-artifact-check.schema.json` stays the strong `oneOf` of
  `{match:true}` / `{match:false, mismatch_detail}` — `review_artifact_check.py`
  itself still emits one of those two exact shapes; the flat literal is only
  what the *agent* is allowed to relay back into the Workflow.
- **`LEDGER_WRITE_SCHEMA`** — now flat, a relaxed union of both former
  branches: `{type:"object", additionalProperties:false, required:["success"],
  properties:{success:{type:"boolean"}, status:{type:"string"},
  fragment_path:{type:"string"}, fragment_sha1:{type:"string"},
  error:{type:"string"}, exit_code:{type:"integer"}, stderr:{type:"string"}}}`.
  The on-disk `ledger-write-confirmation.schema.json` stays the strong
  `oneOf` — `ledger_update.py` itself still only ever emits a genuine
  success or a genuine failure shape, never a mix.
- **`LEDGER_MERGE_SCHEMA`** — now flat: `{type:"object",
  additionalProperties:false, required:["success"],
  properties:{success:{type:"boolean"}, ledger_path:{type:"string"},
  n_segments:{type:"integer"}, missing_segments:{type:"array",
  items:{type:"string"}}, stale_segments:{type:"array",
  items:{type:"string"}}, error:{type:"string"}, exit_code:{type:"integer"},
  stderr:{type:"string"}}}`. `missing_segments` is the deliberately relaxed
  union (no `maxItems`, unlike the on-disk success branch's `{type:"array",
  maxItems:0}`) — the flat literal can't express "empty on success, present
  on failure" without a combinator, so the JS guard (see
  `references/ledger-and-resumability.md`) is what actually enforces
  emptiness on the success path. The on-disk `ledger-merge-confirmation.schema.json`
  stays the strong `oneOf`.
- **`CANON_VERIFY_SCHEMA`** — new, glossary-only, flat: `{type:"object",
  additionalProperties:false, required:["verified"], properties:{verified:
  {type:"boolean"}, missing:{type:"array", items:{type:"string"}}}}`. Relays
  `canon_validate.py --verify-merged`'s own `{verified, missing[]}` line —
  see `references/canon-and-glossary.md`.

None of the four flat literals above are safe to trust without their paired
exact-key-set JS guard at the consume site (a flat schema alone would accept
`{success:true, error:"x"}` as a success) — the guard field sets are
`references/ledger-and-resumability.md`'s subject.

`tests/agent_schema_top_level_object.test.py` asserts every remaining agent
`schema:` const (`REVIEW_SCHEMA`, flat `REVIEW_ARTIFACT_SCHEMA`, flat
`LEDGER_WRITE_SCHEMA`/`LEDGER_MERGE_SCHEMA`, `CANON_VERIFY_SCHEMA`) is
top-level `type:"object"` with no top-level combinator — the direct
regression lock for #87.

`tests/schema_literal_drift.test.py` locks the surviving inline workflow
schema literals against their matching `assets/schemas/*.json` files, but no
longer as strict equality across the board: `REVIEW_SCHEMA` is asserted as
an intentional 4-field **projection** of the 5-field on-disk
`review.schema.json` (which now also requires `dispatch_token`), while the
flat `LEDGER_WRITE_SCHEMA`/`LEDGER_MERGE_SCHEMA` literals are decoupled from
strict on-disk parity and instead asserted (a) API-legal (top-level object,
no combinator) and (b) that they accept the real scripts' actual success and
failure outputs. `CANON_BATCH_SCHEMA` is gone from this test entirely — its
coverage moved to the `--check-batch` CLI tests in
`references/canon-and-glossary.md`.

`tests/workflow_template_instantiation.test.py` instantiates both templates
against a fixture profile (substituting a stable fixture `{{RUN_ID}}`) and
greps the output for a literal `{{`, asserting zero matches — no
substitution token left unresolved, which would otherwise be an easy way for
a declare-order refactor to silently break instantiation without touching
the TDZ ordering at all.

## The review-artifact gate: `review-artifact-check.schema.json` / `scripts/review_artifact_check.py`

This is the mechanism that stops later ledger/audit-trail consumers from ever
trusting a `review.json` on disk that doesn't actually match the structured
verdict the workflow itself holds. As of 1.2.0 it is the **CONSUME**-side
half of the shared codex work-call pattern (above): `reviewDispatchPrompt`
writes `review.json` to disk during DISPATCH; `readReviewPrompt` and
`verifyReviewArtifactPrompt` are two **separate** CONSUME calls that run only after
`reviewWaitPrompt`'s poll reports `READY`.

### The gap it closes

`readReviewPrompt` reads `review_path(seg) = segments/{seg}.review.json`
from disk and returns a schema-validated `REVIEW_SCHEMA` object to the JS —
a genuine disk read, not a fresh judgment call. Nothing about that read
alone proves the file it just read is the current run's own artifact rather
than a leftover from an earlier run or round: `review.json` is an unscoped,
overwritable path, and a stale file sitting there is exactly the class of
bug the resume-integrity work below exists to close. The review-artifact
gate's `verifyReviewArtifactPrompt` step exists to make that trustworthy
deterministically, never on an LLM's own say-so.

### What the gate protects today

**1.3.6 (#132 option b):** `fixPrompt(seg, round, revObj)` no longer splices
`revObj` into its own prompt as the findings source — it instructs the
fixer to READ `review_path(seg)` itself and apply every entry in its
on-disk `findings[]` array. `review_ready.py` already token-validated this
exact file fresh THIS round before the fix call was ever dispatched, and it
is not rewritten again until the NEXT round's review dispatch, so this read
is fresh and race-free. This is what closes the fix-step-input question:
the fixer's own disk read, not this gate. The narrowed `{loc, severity}`
compare below no longer needs to bind the free-text `issue`/`suggest`
bodies for that same reason — the fixer never consumes the CONSUME agent's
transcribed copy of them at all, so a transcription slip there can no
longer reach the fixer, regardless of what this gate's own compare does or
doesn't catch. This gate still protects `scripts/ledger_update.py`'s
`reviewed_draft_sha1`/`dispatch_token` binding check at the convergence
write (see `references/ledger-and-resumability.md`) and any later
audit-trail inspection — both of which still read `review_path(seg)` from
disk, at a point in time *after* this gate has already run.

### The mechanism, step by step

Right after `reviewWaitPrompt` reports `READY`, the JS runs the two CONSUME
calls in sequence:

```
const revObj = await agent(readReviewPrompt(seg), {effort:'low', schema: REVIEW_SCHEMA});
// on revObj === null, see "Null-review and the shared retry budget" below
const artifactResult = await agent(verifyReviewArtifactPrompt(seg, revObj), {effort:'low', schema: REVIEW_ARTIFACT_SCHEMA});
```

`verifyReviewArtifactPrompt`'s prompt instructs the calling agent to do exactly three
things, and nothing more:

1. Write `revObj`'s own canonical-JSON text — already fully formed by the
   JS, no hashing/formatting logic needed since the JS has no imports — to a
   scratch `--expected-file <path>`. This is the same "never shell-embed a
   JSON blob, write it to a file instead" discipline `ledger_update.py`'s
   own `--payload-file` convention already uses.
2. Invoke `python3 {{DURABLE_ROOT}}/scripts/review_artifact_check.py <seg>
   --expected-file <path>`.
3. Relay that script's own printed `{match:true}` / `{match:false,
   mismatch_detail}` line **verbatim** as its schema-validated return — the
   agent never performs the comparison itself.

The **script** (never the agent) does the actual work: it reads the
canonical `review_path(seg)` from disk, **projects both** the on-disk object
and the `--expected-file`'s contents down to exactly the four verdict fields
`{clean, coverage_ok, findings, draft_sha1}` — dropping `dispatch_token` from
whichever side carries it, since `review.json` on disk now carries
`dispatch_token` while `revObj`/`--expected-file` never do (`REVIEW_SCHEMA`
is a deliberate 4-field projection, not the on-disk file's full shape).
**1.3.6 addition (#132):** each `findings[]` element is further projected
down to `{loc, severity}` on both sides, dropping the free-text
`issue`/`suggest` bodies — by the time this script runs, `review_ready.py`
has already guaranteed the on-disk artifact is schema-valid,
`draft_sha1`-fresh, and `dispatch_token`-matched, so byte-comparing
free-text prose can only false-block a valid review over an immaterial
transcription slip, never catch a decision-relevant divergence the
retained `loc`/`severity`/array-length binding wouldn't already catch. The
script then canonicalizes both projections via sorted-key JSON
serialization, and does a byte-for-byte comparison of the two canonical
forms. It prints `{match:true}` on an exact match, or `{match:false,
mismatch_detail:"<the first differing key/value pair, named>"}` otherwise. The agent-facing `REVIEW_ARTIFACT_SCHEMA`
is flat (see the #87 section above); the script's own printed line still only
ever takes one of the two shapes the on-disk `review-artifact-check.schema.json`
strong `oneOf` requires:

```json
{"oneOf": [
  {"match": true},
  {"match": false, "mismatch_detail": "<string>"}
]}
```

(each branch `additionalProperties: false`, `match` required in both.)

### Null-review and the shared retry budget

`getVerifiedReview(seg, round)` runs: DISPATCH → WAIT (`TIMEOUT` → `blocked
review-timeout`, no retry) → **one shared retry budget** covering both
CONSUME calls together: `read → check`; if the read comes back `null` **or**
the check reports `match:false`, retry the *same* `(read, check)` pair
**once** — never retrying read and check independently of each other. Still
failing after the retry → `blocked review-null` (persistent null read) or
`blocked review-artifact-mismatch` (persistent mismatch), whichever
triggered it. This caps one review point's worst case at
`dispatch + wait + 2×(read + check) = 6` calls — see
`references/orchestration-and-batching.md`'s estimator section for how this
feeds `estimatedCalls`.

This is a deliberate change from the pre-1.2.0 shape, which retried the
*entire dispatch* on a mismatch. Retrying only the CONSUME pair is correct
now because DISPATCH already wrote an atomically-complete, token-scoped
artifact by the time WAIT reports `READY` — there is nothing left for a
fresh dispatch to fix that a fresh read/check pair wouldn't also fix, and
re-dispatching would burn a second codex call for no reason.

On `match:true`, the fix step dispatches:
`agent(fixPrompt(seg, round, revObj), {effort:'high'})`.

### The residual risk this gate cannot fully close, and why

The **comparison** itself — `review_artifact_check.py` diffing two
already-on-disk-or-freshly-written files — is genuinely deterministic. But
one of its two inputs, the `--expected-file`, is still **written by an LLM
agent** inside its own turn, not by the JS or any deterministic script (the
Workflow JS has no direct filesystem access at all — every write in this
design is either an agent-turn action or a `python3` script shelled out from
one, never invoked by the JS directly). If that agent ever wrote something
other than `revObj`'s own exact text — a transcription slip, a stale
substitution — the deterministic script would still "correctly" compare
`review_path(seg)` against a **wrong** expected-file, and the whole point of
the gate is to catch exactly the kind of drift a wrong expected-file would
then hide.

This residual is now confined to the narrower purpose described above (the
ledger-binding/audit-trail question), never to a wrong-findings-reach-the-
fix-step question — that question is closed by `fixPrompt` reading
`review_path(seg)` itself (1.3.6/#132 option b), a fresh, token-validated
disk read the fixer performs independently of whatever this gate's own
`--expected-file` comparison concluded.

For the later ledger/audit-trail purpose only, a second, rarer compound case
is explicitly **not** covered by any fixture: `review_path(seg)` and
`--expected-file` **both** independently stale, and happening to
byte-for-byte agree. This is an accepted residual risk, not one any test in
this plugin claims to close — no fixture can exercise it directly, since the
failure requires a specific wrong choice by an LLM agent, not a script bug,
and this plugin's test coverage stops at the deterministic-script boundary.
It is no longer a fix-step risk: `fixPrompt` reads the on-disk
`review_path(seg)` itself for its findings (1.3.6/#132 option b), a fresh
read independent of this gate's own comparison.
The **stale-run** case (a straggler artifact from an OLD run, not just an
old round) is a distinct, separately-closed problem — see the
`dispatch_token`/resume-integrity mechanics in
`references/ledger-and-resumability.md`.

### Tests

`tests/review_artifact_check.test.py` covers, at minimum:

- A fixture `review_path(seg)` (with `dispatch_token`) plus a 4-field
  expected-file (no `dispatch_token`) asserts `{match:true}` — the
  field-projection positive case ([cx4#1]).
- A fixture with one differing verdict field (e.g. `coverage_ok` flipped)
  asserts `{match:false}`, naming that exact field in `mismatch_detail`.
- A missing `review_path(seg)` asserts a named, non-zero-exit error, never a
  raw traceback.
- A fixture where `--expected-file` is populated with a **stale**,
  previously-written `review_path(seg)` snapshot (simulating the verifying
  agent writing the wrong content) while the **current** `review_path(seg)`
  holds a genuinely different, later review — asserts `{match:false}`,
  proving the comparison mechanism has no blind spot that would let a
  wrongly-populated expected-file slip through as a false match.
- The workflow-level shared-retry/blocked case: a forced `match:false` on
  both the original and retried `(read, check)` pair ends the segment as
  `blocked` with reason `review-artifact-mismatch`.
- 1.3.6 (#132): two verdicts identical in `clean`/`coverage_ok`/`draft_sha1`
  and in every finding's `loc`+`severity`, differing ONLY in a finding's
  free-text `issue`/`suggest` text, assert `{match:true}` — the narrowed
  per-finding projection's positive case. A difference in `loc`, `severity`,
  or the findings array's own length still asserts `{match:false}` — the
  structural binding that protects the fixer is unchanged.

`review_artifact_check.py` itself is dependency-free (stdlib `json` only —
no `requirements.txt` entry, no dependency preflight needed).
