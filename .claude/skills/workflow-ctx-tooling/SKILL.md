---
name: workflow-ctx-tooling
description: Traps when authoring or debugging a Workflow script or using the context-mode (ctx) tools — ctx_execute_file/ctx_execute confinement to the project root, the Workflow background-task .output wrapper shape, apostrophes in single-quoted prompt prose breaking the strict pre-run parse, and multi-agent Workflow output you must not trust at face value (schema agents failing to null/placeholder, spread-makes-null-truthy, and investigators reading a stale copy). Read when a Workflow is rejected before agents run, a ctx tool refuses a path, an agent slot comes back empty/garbage, or you are about to bank a Workflow's recommendation.
---

# Workflow + context-mode (ctx) tooling traps

## ctx tools are confined to the project root

`mcp__plugin_context-mode_context-mode__ctx_execute_file` and `ctx_execute` refuse any path
outside the project root (`/Users/moi/lazy-ants/development/claude-plugins`). The error reads:

```
File access blocked … resolves outside the project root … context-mode confines ctx_execute_file to the workspace (issue #852).
```

So they **cannot** read the session scratchpad or task-output dirs (both under
`/private/tmp/claude-501/...`). For anything outside the repo, use `Bash` (python/node) or the
native `Read` tool — the native `Read` tool has **no** such restriction.

## Workflow background-task `.output` file shape

A background Workflow's `<task-id>.output` is a JSON **dict**, not the array you returned. Keys:
`summary, agentCount, logs, result, workflowProgress, totalTokens, totalToolCalls`. The array your
script `return`ed sits under **`result`**, not the top level — parse `json.load(...)['result']`.
Failed/skipped agents that were `.filter(Boolean)`'d out simply don't appear in it.

## The script is parsed as strict JS BEFORE anything runs — apostrophes break it

The `Workflow` tool parses the script as plain JavaScript, strictly, before dispatching any agent.
When prompt prose lives in **single-quoted** strings (especially inside an
`[ '...', '...' ].join('\n')` array), any **apostrophe / contraction** — `branch's`, `doesn't`,
`it's`, `can't`, `the instance's keys` — is read as the closing quote and the rest of the line
becomes stray tokens. The whole Workflow is rejected; no agents dispatch; the round-trip is wasted;
the caret points at the apostrophe, not a real bug:

```
Invalid workflow script: Script parse error: Unexpected token (99:83)
```

Fixes, in order of preference:

- **Reword to the possessive-free / contraction-free form** in single-quoted strings ("the branch
  false-valued then.properties", "the instance keys", "does not", "cannot", "it is"). Cheapest fix,
  reads fine to an agent.
- If you genuinely need an apostrophe, use a **double-quoted** string for that element (JS allows
  `"the branch's keys"`), or a **template literal** — but template literals bring their own trap: a
  literal backtick or `${` in prose breaks THEM. Possessive-free single-quoted prose is the safest
  default for large prompt blocks.
- Before launching a big Workflow, **eyeball every single-quoted prose string for `'s`, `n't`,
  `'re`, `'ll`, `'ve`** — a 5-second scan that saves a full failed dispatch. Prose destined for a
  strict parser needs a byte-level once-over, not just a read-through (same discipline as scanning
  for stray invisible Unicode in prompt/plan text).

This is orthogonal to the confinement trap above: that one is about what a Workflow can READ; this
one is about the script failing to PARSE at all.

## Don't trust a multi-agent Workflow's output at face value

Two independent failure modes each produce CONFIDENT output that poisons the result if banked
unverified.

### A — a schema-forced agent fails to null OR a truthy placeholder (authoring-time)

A `parallel()`/`pipeline()`/`agent(prompt, {schema})` given a `schema` can:

- hit `StructuredOutput retry cap (5) exceeded` and resolve to `null`; or
- do real work, botch the final structured emit, and return a valid-SHAPED **placeholder** like
  `{"answer":"test","numbers":[],...}` — which is truthy, so `results.filter(Boolean)` keeps it and
  synthesis ingests "test" as a real result.

**Worse:** the wrapper `agent(...).then(r => ({ ...r, key }))` makes even a genuine `null` return
into a truthy `{ key }`, so `.filter(Boolean)` can NEVER drop a failed agent when you spread its
return into an object.

Fix pattern:

- Never spread an agent return into an always-truthy object BEFORE filtering. Filter/validate the
  raw return first.
- Add an explicit garbage-detector, not just `Boolean`, e.g.
  `isGarbage = r => !r || !r.answer || String(r.answer).trim().length < 40`.
- Single-retry backstop per garbage/missing result (re-run with a "return REAL analysis, not
  placeholders" nudge), then drop-and-log if still garbage — so one flaky schema agent can't
  silently blank a phase.
- Keep the schema simple, or budget for backfilling dropped slots by hand.

### B — investigators analyze a STALE copy and confidently recommend the wrong fix (consuming-time)

When a run/working dir holds two copies of an artifact (e.g. the real promoted deposit AND a
leftover staging copy of an older, rejected version), agents left to find "the file" themselves can
analyze the STALE one and return a detailed, numeric, entirely-confident recommendation that targets
a defect existing ONLY in the stale copy. Following it weakens correct code to fix a phantom.

Fix pattern:

- Before acting on a Workflow's recommendation, check WHICH artifact each agent actually read (grep
  the agent logs / the recommendation's cited paths), and **re-run the central claim against the
  CANONICAL target yourself**.
- When a dir contains staging + shipped copies, name the canonical one explicitly in every agent
  prompt and tell them NOT to use siblings — ambiguity defaults to the wrong file.
- A RIGHT-sounding recommendation computed against a WRONG source is not a real finding — the twin
  of "a wrong PATH is not a refutation". Spot-verify every claim (and file:line) yourself.
