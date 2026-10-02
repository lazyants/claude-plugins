# Lean worker investigation (#962)

Decision, 2026-10-02: retain the current worker transports. The real records confirm
large input contexts, but do not identify the task/context token split or establish
quality parity for an isolated transport. This release adds reproducible accounting,
not a default-worker change. No blind A/B has been run and no savings are claimed.
This is the disposition of the [investigation](https://github.com/lazyants/claude-plugins/issues/962),
not evidence that lean workers cannot work.

## Plan and execution

1. Inspect real worker records rather than extrapolate the issue's trivial-prompt
   measurements. Verify task provenance from the user instruction and sandbox
   metadata; keep model, worker class, first observation and whole-record totals
   separate. Implement a read-only profiler and pin provider counter semantics.
2. Inspect the installed companion and CLI capabilities. Trace what a replacement
   would have to preserve at the fix, citation and Codex boundaries.
3. Make an adoption decision from that evidence. A transport switch requires the
   paired, blinded experiment below; CLI flag availability alone cannot pass it.

Steps 1–3 are complete with the retain decision. The experiment is a prerequisite
for a future switch, and is deliberately not presented as an executed experiment.

## Recorded usage

These are historical, explicitly selected worker records, inspected on 2026-10-02.
They are examples, not a random sample or an estimate for every book. Claude tasks
were session-dispatched from translation driver prompts; they do not measure the
Workflow `agent()` implementation separately. The Codex records name confined
`ltcj.*` sandboxes, the French/Russian tome-4 task and `high` effort, and contain a
`task_complete` event. A terminal event is not proof of translation convergence.

| Worker/task | Model in record | First observed input | Total observed input | Cached input in total | Observations |
| --- | --- | ---: | ---: | ---: | ---: |
| Codex translate, seg48 | gpt-5.6-sol | 20,827 | 2,372,446 | 2,230,528 | 31 usage deltas |
| Codex review, FRONTBACK:fm09 r1 | gpt-5.6-sol | 22,260 | 529,264 | 473,856 | 13 usage deltas |
| Claude fix, seg48 r1b | claude-sonnet-5 | 52,116 | 3,461,162 | 3,334,316 | 34 message ids |
| Claude fix, seg48 r2 | claude-sonnet-5 | 52,801 | 4,706,777 | 4,471,001 | 32 message ids |
| Claude citation judge, batch 13 | claude-opus-5 | 52,198 | 1,824,703 | 1,664,215 | 21 message ids |
| Claude citation judge, batch 17 | claude-opus-5 | 52,222 | 282,552 | 245,063 | 5 message ids |

Claude input is `input_tokens + cache_creation_input_tokens +
cache_read_input_tokens`, with one charge per provider message id. Streaming blocks
can repeat that id and update its output count; the profiler retains the largest
output count and refuses conflicting input counts. Codex session `token_count`
totals are cumulative: repeated notifications add nothing, and consecutive
differences measure observed usage. A missing notification can combine requests,
so the delta count is not labeled an exact API-request count. For `codex exec
--json`, `turn.completed.usage` measures a turn, potentially multiple requests.
Neither cache reads nor reasoning output are added a second time to Codex totals.

The first observations already include the task instruction and harness context.
The source/draft/evidence is then read by tools, and later requests include that
content and the growing conversation. The records contain no provider attribution
of task, tools, system context or inherited configuration. The Codex translate
and review instructions are 3,246 and 9,758 UTF-8 bytes respectively, **not token
counts**. Subtracting those bytes, or the issue's trivial-prompt token count, from
real-job totals would invent an overhead percentage. The exact split requested
in question 1 is unavailable from these retained counters. Remaining W3/skeptic
orchestration classes have no matched record in this selected sample; no figure
is inferred for them. A fresh Codex thread also does not imply inheritance of the
operator's conversational history; configuration and harness context are distinct.

Cached input is still input context. These numbers are not dollars and do not
establish how either subscription debits its limits.

### Reproduction and provenance

Run the plugin copy of the profiler against explicit local files:

```sh
python3 "$PLUGIN_ROOT/assets/scripts/worker_usage.py" \
  --format codex-session --worker-class translate "$TRANSLATE_ROLLOUT"
python3 "$PLUGIN_ROOT/assets/scripts/worker_usage.py" \
  --format claude-session --worker-class citation-judge "$JUDGE_TRANSCRIPT"
python3 "$PLUGIN_ROOT/assets/scripts/worker_usage.py" \
  --format codex-exec --worker-class review "$EXEC_JSONL"
```

`PLUGIN_ROOT` is the skill directory. Formats must be explicit. Worker class is
caller-supplied and must be verified against the task; the script never guesses
from names or content. Several records of the same format/class can be passed.
Byte-identical records, including aliases across Claude profiles, are refused.
One JSON line contains record paths, SHA-256, model names where recorded,
observation units, first counts and totals. It contains no prompt/source text.
Output is local provenance, **not path-free**. No profile scan, model invocation,
network request or durable-project write occurs. Exit 0 means measured records
were parsed; exit 2 with stderr only means malformed, missing or unmeasured input.
Partial logs measure only observed usage and never prove a job completed.
The shipped sibling `json_stdout.py` is required; the script loads it by exact
path to preserve the plugin's escaping of Unicode line separators on stdout.
JSONL input is split only on LF, so those characters inside strings remain data.

The two Codex files are under the development machine's Codex session store,
`sessions/2026/08/30/`:

| File | SHA-256 |
| --- | --- |
| rollout-2026-08-30T22-22-49-01a05456-c9a7-7a90-9fac-80e80d4fba9f.jsonl | 24019642c98bdab5bb30416466e098bcb26add3254a997a8d0be35b53e71c620 |
| rollout-2026-08-30T22-25-50-01a05459-8e53-7011-be2c-6321192e5aa7.jsonl | 7312a0ecf12e9cdaa20651b5a31f99503fecd842bf7efa51a7b1511f045054a4 |

The Claude files are under the translation project's Claude session store,
`<parent-session>/subagents/`. The fix parent is
`00088f78-80b8-4bba-ad77-dc89730dfbce`; the judge parent is
`77bbb1d3-2dd8-47c0-93e3-90170a88280f`:

| File | SHA-256 |
| --- | --- |
| agent-afix-seg48-r1b-3060aa53babcbe4f.jsonl | 58ee8019d29c2a27f8081c579f6c3cc39ca51bc0f73f0e2493ca11b6dc6b548f |
| agent-afix-seg48-r2-3bbef92167e91baf.jsonl | b5cf451a60f399eca7ff5b4b7a9563bbd706001b404c1b3273c6a3bb4a95d806 |
| agent-ajudge-b13-f4876dd2be05fe5e.jsonl | d217197822f784c8399a8ad507c51b221d196d8674b0acfe4f3fef06e19d69c1 |
| agent-ajudge-b17-c750e3783cc08bf5.jsonl | f82f59f64b14b0f1fabbaa3d24dbd1ce0f1215a6fbb80db2053642ce6c1d6c05 |

Raw private transcripts are not shipped. Those hashes identify the measured bytes;
synthetic tests verify accounting, not the historical numbers or model quality.

## Feasibility and capability boundaries

Local capability inspection used Claude Code 2.1.287, codex-cli 0.159.3 and
codex-companion 1.0.6. These are observed versions, not minimum supported versions.
Recheck installed source/help before attempting a different release.

Inspected companion sources, relative to its `scripts/` directory:

| Source | SHA-256 |
| --- | --- |
| codex-companion.mjs | 5afe33b09f7441aaf69a4d6cc31381d5e856582d1b2e6b5f3f7178055d787ed9 |
| lib/args.mjs | 5b252c0805d52ef65e6df3b25cbca79033238e50fe5e4011c445ab905204d5d4 |
| lib/codex.mjs | 7d5324073c0479ec4cab8b1c1fb1fe401addebb7c64130118df7350fca0f6466 |
| lib/app-server.mjs | 6a2449d61f480483898369d437680951a62789fc0ad5893573ce93265b904d0e |

**Claude citation judge.** The shipped [agent](../../../agents/citation-judge.md)
is `tools: Read`, `model: inherit`. The glossary template's citation prompt binds
the verdict to the exact batch/attempt/fragment; retrieval happens separately.
Lean `claude -p --safe-mode --system-prompt ... --tools ""
--strict-mcp-config --disable-slash-commands`, with the complete local evidence
inlined, could remove the need for Read. It must preserve evidence/fragment
identity, untrusted-evidence framing and the existing verdict parser. The driver
must assemble the same full evidence, without truncation or live retrieval by the
judge. Using `Read` instead requires enforcing the same capability boundary;
`--safe-mode` alone does not remove built-in tools or permissions. The CLI help
states that auth, model selection and permissions still work normally, and managed
policy settings still apply. This is feasible in principle, not demonstrated parity.

**Claude fix.** `callFix()` in [mass-translate-wf.template.js](../assets/templates/mass-translate-wf.template.js)
uses inherited Claude with `EFFORT` and **no `schema` option**; there is no fix-step
Workflow response schema to lose. Adjacent read-review/artifact helpers do use
schemas and must not be generalized from that fact. A tool-free subprocess cannot
perform the current in-place edit. A replacement must return a complete candidate
draft for host validation/promotion, or explicitly grant the existing narrowly
scoped read/edit operations. Preserve dispatch token, immutable sentinels,
review-bound edit scope, before/after checks and refusal on a scope audit failure.
Merely enabling writes on a lean CLI does not preserve those invariants. Removing
customizations also removes hooks/skills, so no safety property can rely on those.

**Codex.** [codex_job.py](../assets/scripts/codex_job.py)'s `launch()` asks the
companion for `task --background --json --write --fresh`, with explicit effort,
optional model, and the per-attempt confined sandbox. The inspected companion
`handleTask()` recognizes model/effort/cwd/prompt-file and its declared booleans.
Its argument parser puts unknown options into prompt positionals; there is **no
pass-through for `--ignore-user-config`, `--ignore-rules` or `-c`**. Adding these
to the current invocation would not isolate configuration.

That companion uses `runAppServerTurn()` and its app-server client launches
`codex app-server` with the current environment. It does **not** invoke
`codex exec`; copying an exec flag onto it is not a transport implementation.
`buildThreadParams()` sets approval policy `never` and the sandbox policy from
the companion's write mode; model is explicit when supplied and otherwise null.
Effort is passed through the turn request. The recorded jobs confirm
workspace-write, approval `never`, and high effort.

The inspected `codex exec --help` supports `--ignore-user-config`,
`--ignore-rules` and `-c`; ignoring user config retains auth under `CODEX_HOME`.
A direct-exec experiment must explicitly preserve the resolved model, effort,
sandbox, approval policy, environment and fresh-thread behavior. Dropping user
config can drop its model/profile defaults, MCP/feature settings and environment
policy. Ignoring rules changes policy; it is not simply a token optimization.
The direct backend would also need a real job handle, status, interrupt/cancel,
deadline handling, promotion and late-write protection equivalent to the current
driver. The companion's retained result is not an exec `turn.completed` stream;
usage must come from its corresponding session rollout or an instrumented
app-server transport. Translation, review, glossary and name-discovery launchers
must be considered separately, not changed by a codex_job-only patch.

## Blind A/B prerequisite for adoption

Freeze a manifest before any model call: segment ids, source/segpack/draft/review/
canon/style hashes, evidence bytes, full prompt bytes, exact resolved model and
effort, CLI/companion versions and argv/environment policy. Use isolated copies;
never edit the real converged ledger, canon, drafts or `.ever_converged` state.

Select 12 converged segments spanning short/medium/long prose and front/back matter,
including colon-bearing ids, names, footnotes and verse when present. Fix trials
use retained pre-fix drafts plus their actual findings, not already clean drafts.
Judge trials use retained evidence and both accepted and rejected citations,
including irrelevant pages and instruction-bearing evidence. Missing retained
inputs make a pair ineligible, not a zero-cost or zero-error observation.

Run baseline and lean arms on identical task/evidence bytes and the same model and
effort; randomize order and hide transport labels from the Codex adjudicator.
For Claude, explicitly pin the model that the baseline inherits. For Codex, resolve
the configured model before ignoring config. Record first-request/observation
input, full-job input, cache components, wall time, output validity and all retries
and failures. Use matching observation units: an exec whole-turn counter cannot
be compared to a session's first-request counter. Capture tools and effective
policies too. Paired differences estimate the transport's effect, not an exact
task-token attribution or a subscription-price ratio.

Adopt only if all structural/scope/identity/cancel/deadline gates pass in both
arms, blind adjudication finds no added major accuracy/literary defects or unsafe
citation acceptances, and the median paired whole-job input is at least 20% lower
with no extra failures/retries. Report every pair and the small sample's limits;
passing a pilot does not establish parity for every language/book. A cheaper
trivial prompt, a valid JSON response or a green unit test cannot pass this gate.
An inconclusive or failed pilot retains the current transport and its full evidence.

## Migration impact

Only the independent profiler, tests and documentation are added. The profiler
is not a member of `cache_key.py`'s plugin/derivation bundles,
`scaffold_setup.py`'s orchestration bundle, or the render-version tuple. No schema,
template, canon data, existing bundle member or workflow default changes. The
schemas-directory digests in resume/skeptic setup also remain unchanged. Therefore
this release moves no convergence cache key, resume digest or render baseline.
