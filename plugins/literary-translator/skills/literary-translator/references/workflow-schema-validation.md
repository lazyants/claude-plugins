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

## Two categories of schema-validated calls — not one blanket rule

Not every `agent(..., {schema: ...})` call in this plugin exists for the
same reason. Conflating the two categories below is a real mistake this
plan corrected explicitly (an earlier draft's "every codex call needs a
schema" framing implicitly swept ledger-write confirmations in as if they
were codex accuracy calls, which they are not).

### 1. Codex structured-accuracy/canon calls

Review and the canon/glossary-pass batch calls. These **MUST** carry both
`agentType: 'codex:codex-rescue'` **and** a `schema` param, because the
specific failure mode being guarded against is an ambiguous async-job
string standing in for a real codex verdict:

- Review: `agent(reviewPrompt(seg), {agentType:'codex:codex-rescue', schema: REVIEW_SCHEMA})`
- Glossary-pass batch: `agent(glossaryPrompt(batch), {agentType:'codex:codex-rescue', effort:'high', schema: CANON_BATCH_SCHEMA})`

**The translate call is the one deliberate exception even within this
category.** It is intentionally schema-less — gated instead by file output
plus the deterministic checks (`draft_ready.py` for readiness,
`validate_draft.py` for the false-green gate — see
`references/false-green-gate.md`) — exactly matching the proven reference
script's own behavior:

```
agent(translatePrompt(seg), {agentType: 'codex:codex-rescue', effort: 'high'})
```

### 2. Non-codex mechanical schema-confirmation calls

`recordLedgerPrompt`, `mergeLedgerPrompt`, and `verifyReviewArtifactPrompt`.
These use a `schema` param for a **different reason**: verifying that a
shell script's own JSON stdout, plus the required deterministic follow-up
checks, produced a well-formed and complete mechanical confirmation — not
forcing a real verdict out of an ambiguous codex async job. They do **not**
specify `agentType: 'codex:codex-rescue'` at all, and run at `effort: 'low'`
since no judgment is involved:

```
agent(recordLedgerPrompt(seg, fields), {effort:'low', schema: LEDGER_WRITE_SCHEMA})
agent(mergeLedgerPrompt({expectedSegs: SEGS}), {effort:'low', schema: LEDGER_MERGE_SCHEMA})
agent(verifyReviewArtifactPrompt(seg, revObj), {effort:'low', schema: REVIEW_ARTIFACT_SCHEMA})
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
key-set equality check. `verifyReviewArtifactPrompt` is the category-2 call
whose agent task is only to write the expected-file, invoke
`review_artifact_check.py`, and relay that script's printed line verbatim.

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
// (glossary-pass-wf.template.js only:)
const CANON_BATCH_SCHEMA = { /* ... */ };

// ... prompt-builder functions (translatePrompt, reviewPrompt, etc.) ...

// pipeline() is called LAST, after every schema it references above
// is already fully initialized.
pipeline(SEGS, (seg) => reviewFixLoop(seg));
```

`mass-translate-wf.template.js` declares `REVIEW_SCHEMA`,
`REVIEW_ARTIFACT_SCHEMA`, `LEDGER_WRITE_SCHEMA`, and `LEDGER_MERGE_SCHEMA`
above its `pipeline()` call for the same TDZ reason.
`glossary-pass-wf.template.js` declares `CANON_BATCH_SCHEMA` above its own,
separate `pipeline()` call for the identical reason — it is a second,
smaller workflow script, not a shared module, so it repeats its own
declare-above-use discipline independently.

The exact shapes of the four `mass-translate-wf.template.js` schema
literals, matching the shipped `assets/schemas/*.json` files 1:1:

- **`REVIEW_SCHEMA`** — matches `review.schema.json`: `{clean: boolean,
  coverage_ok: boolean, findings: [{loc, severity, issue, suggest}],
  draft_sha1: string}`, all fields required, `additionalProperties: false`.
  No `verse_status` field — verse issues surface as ordinary `findings[]`
  entries (`loc: "VERSE:{vid}"`); verse coverage is exclusively the
  deterministic validator's job, never review judgment. `draft_sha1` is a
  deliberate plugin addition over the real reference schema: the reviewer
  computes it itself, before reading the draft, by shelling out to
  `python3 {{DURABLE_ROOT}}/scripts/draft_sha1.py <seg>`.
- **`REVIEW_ARTIFACT_SCHEMA`** — matches `review-artifact-check.schema.json`:
  `{oneOf: [{match: true} (required), {match: false, mismatch_detail}
  (required)]}`, each branch `additionalProperties: false`.
- **`LEDGER_WRITE_SCHEMA`** — matches `ledger-write-confirmation.schema.json`:
  `{oneOf: [{success: true, status, fragment_path, fragment_sha1} (all
  required), {success: false, error} (required, +optional
  exit_code/stderr)]}`, each branch `additionalProperties: false`.
- **`LEDGER_MERGE_SCHEMA`** — matches `ledger-merge-confirmation.schema.json`:
  `{oneOf: [{success: true, ledger_path, n_segments, missing_segments,
  stale_segments} (all required), {success: false, error} (required,
  +optional missing_segments/exit_code/stderr)]}`, each branch
  `additionalProperties: false`.

`tests/schema_literal_drift.test.py` locks all five inline workflow schema
literals in against their matching `assets/schemas/*.json` files:
`REVIEW_SCHEMA`, `LEDGER_WRITE_SCHEMA`, `CANON_BATCH_SCHEMA`,
`LEDGER_MERGE_SCHEMA`, and `REVIEW_ARTIFACT_SCHEMA`. `CANON_BATCH_SCHEMA`
is compared directly against `canon-batch.schema.json`, not an ad hoc wrapper
around a different canon schema.

`tests/workflow_template_instantiation.test.py` instantiates both templates
against a fixture profile and greps the output for a literal `{{`, asserting
zero matches — no substitution token left unresolved, which would otherwise
be an easy way for a declare-order refactor to silently break instantiation
without touching the TDZ ordering at all.

## The review-artifact gate: `review-artifact-check.schema.json` / `scripts/review_artifact_check.py`

This is the mechanism that stops later ledger/audit-trail consumers from ever
trusting a `review.json` on disk that doesn't actually match the structured
verdict the review call returned.

### The gap it closes

`reviewPrompt` returns a schema-validated `REVIEW_SCHEMA` object to the JS,
but it **also** writes that same content to disk, at the canonical
`review_path(seg) = segments/{seg}.review.json` (see
`references/ledger-and-resumability.md`'s canonical path invariants), as a
side effect. Nothing about the schema-validated return itself guarantees
that the file on disk matches the object the JS actually received — a stale
or divergent file could otherwise sit there unnoticed. The review-artifact
gate exists to catch exactly that divergence, deterministically, rather than
trusting the write blindly.

### What the gate protects today (its purpose changed at round 60)

Earlier in this plugin's design, the gate's result fed directly into what
the fix step saw. That is **no longer true**. `fixPrompt(seg, round,
revObj)` now receives the review object (`revObj`) **directly**, spliced
into its own prompt text via the JS's deterministic serialization — the
same substitution mechanism `{{DURABLE_ROOT}}` uses elsewhere — and never
reads `review_path(seg)` from disk at all. So the gate no longer protects
the fix step's own input.

What it **still** protects: `scripts/ledger_update.py`'s
`reviewed_draft_sha1` binding check, and any later audit-trail inspection —
both of which still read `review_path(seg)` from disk, at a point in time
*after* this gate has already run. The gate's job is now "prove the on-disk
artifact these later consumers will read is trustworthy," not "prove the fix
step's own input is trustworthy" — a narrower, but still real, purpose.

### The mechanism, step by step

Right after a **non-null** `REVIEW_SCHEMA` verdict comes back, the JS
dispatches a schema-validated, mechanical (category 2, above) call:

```
agent(verifyReviewArtifactPrompt(seg, revObj), {effort:'low', schema: REVIEW_ARTIFACT_SCHEMA})
```

The prompt instructs the calling agent to do exactly three things, and
nothing more:

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
canonical `review_path(seg)` from disk, canonicalizes both it and the
`--expected-file`'s contents via sorted-key JSON serialization, and does a
byte-for-byte comparison of the two canonical forms — a diff that cannot be
misjudged. It prints `{match:true}` on an exact match, or `{match:false,
mismatch_detail:"<the first differing key/value pair, named>"}` otherwise,
matching `review-artifact-check.schema.json` exactly:

```json
{"oneOf": [
  {"match": true},
  {"match": false, "mismatch_detail": "<string>"}
]}
```

(each branch `additionalProperties: false`, `match` required in both.)

On `match:false`: retry the **original** review call once, fresh, then
re-run the same script against the retry's own write. If it **still**
reports `match:false`, the segment is `blocked` with reason
`review-artifact-mismatch` — the identical retry-once-then-blocked shape the
null-review path already uses elsewhere in the loop, just gating a
different failure mode.

On `match:true`, the fix step dispatches:
`agent(fixPrompt(seg, round, revObj), {effort:'high'})`.

### The residual risk this gate cannot fully close, and why

The **comparison** itself — `review_artifact_check.py` diffing two
already-on-disk files — is genuinely deterministic. But one of its two
inputs, the `--expected-file`, is still **written by an LLM agent** inside
its own turn, not by the JS or any deterministic script (the Workflow JS has
no direct filesystem access at all — every write in this design is either
an agent-turn action or a `python3` script shelled out from one, never
invoked by the JS directly). If that agent ever wrote something other than
`revObj`'s own exact text — a transcription slip, a stale substitution — the
deterministic script would still "correctly" compare `review_path(seg)`
against a **wrong** expected-file, and the whole point of the gate is to
catch exactly the kind of drift a wrong expected-file would then hide.

This residual is now confined to the narrower purpose described above (the
ledger-binding/audit-trail question), never to a wrong-findings-reach-the-
fix-step question — that question is structurally closed by `fixPrompt`
receiving `revObj` as an in-memory value directly, with no second on-disk
artifact for it to independently drift against.

For the later ledger/audit-trail purpose only, a second, rarer compound case
is explicitly **not** covered by any fixture: `review_path(seg)` and
`--expected-file` **both** independently stale, and happening to
byte-for-byte agree. This is an accepted residual risk, not one any test in
this plugin claims to close — no fixture can exercise it directly, since the
failure requires a specific wrong choice by an LLM agent, not a script bug,
and this plan's test coverage stops at the deterministic-script boundary.
It is no longer a fix-step risk: `fixPrompt` receives `revObj` as an
in-memory value and never reads either on-disk artifact for its findings.

### Tests

`tests/review_artifact_check.test.py` covers, at minimum:

- A fixture `review_path(seg)` plus a byte-identical expected-file asserts
  `{match:true}`.
- A fixture with one differing field (e.g. `coverage_ok` flipped) asserts
  `{match:false}`, naming that exact field in `mismatch_detail`.
- A missing `review_path(seg)` asserts a named, non-zero-exit error, never a
  raw traceback.
- A fixture where `--expected-file` is populated with a **stale**,
  previously-written `review_path(seg)` snapshot (simulating the verifying
  agent writing the wrong content) while the **current** `review_path(seg)`
  holds a genuinely different, later review — asserts `{match:false}`,
  proving the comparison mechanism has no blind spot that would let a
  wrongly-populated expected-file slip through as a false match.
- The pre-existing workflow-level retry/blocked case: a forced `match:false`
  on both the original and retried check ends the segment as `blocked` with
  reason `review-artifact-mismatch`.

`review_artifact_check.py` itself is dependency-free (stdlib `json` only —
no `requirements.txt` entry, no dependency preflight needed).
