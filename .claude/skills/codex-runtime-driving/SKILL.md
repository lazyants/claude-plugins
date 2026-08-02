---
name: codex-runtime-driving
description: Mechanics for reliably driving the codex-companion runtime and recovering its verdict — use when running codex:codex-rescue or codex-companion.mjs (task/adversarial-review/codex exec, foreground, --background, or via the rescue Agent), polling or reading its job state JSON / result, diagnosing a backgrounded or lost verdict, a dead/hung/killed worker, "No job found", a fabricated wait-state, arg-misparse, env/PATH/broker/TMPDIR/moderation/usage-limit breakage, driving long or parallel background jobs safely, or benchmarking codex model×effort on a slice before committing a big job.
---

# Driving the Codex runtime

The `codex:codex-rescue` Agent / `codex-companion.mjs` runtime frequently backgrounds a review and
loses its verdict, hangs, dies with its launcher, or files its job where you don't expect. This skill is
the plumbing for getting a reliable, retrievable verdict out of it and diagnosing when it goes wrong — it
is NOT about deciding when to reach for Codex vs. a parallel-review Workflow (a separate standing guardrail).

Two habits carry most of it: **drive the runtime directly** (`--prompt-file` + read the job STATE JSON, or
a foreground `task` inside a `run_in_background` Bash) rather than trusting the forwarder's return message,
and **judge liveness by log-mtime + `kill -0 <pid>`, never by a `status` that keeps lying `running`** after
the worker died. `<CC>` below is the resolved `codex-companion.mjs`; the version bumps, so never hardcode it.

Read the reference file that matches the task:

- **`references/codex-companion-runtime.md`** — read when driving a codex-rescue / `codex-companion.mjs`
  review, monitoring its state JSON or verdict, or recovering from any failure: a backgrounded/lost verdict,
  a dead or hung or harness-killed worker, "No job found", a fabricated wait-state, an arg-misparse launch
  400, an env/PATH/broker/TMPDIR/moderation breakage, or driving long/parallel `--background` jobs safely.
  Covers the reliable direct-drive patterns, the fastest `jq -r '.result.rawOutput'` verdict recovery,
  stall/hang thresholds, and the `/security-review` working-tree diff caveats.
- **`references/prompt-sizing.md`** — read BEFORE dispatching a job whose prompt is large and whose payload
  is non-Latin (vocalized Hebrew, Arabic with harakat, Devanagari, heavy CJK) or mostly JSON. Two silent
  traps: `chars/4` is a Latin-prose token ratio that badly under-counts a diacritized script (a 611K-char
  Hebrew prompt estimated at "~153K tokens, fits" overflowed a 272K window), and `wc -c` counts BYTES not
  characters (same prompt: 379K by `wc -c` vs 273K real, making 3.5% growth look like 40%). Budget per
  SCRIPT, not per byte; size with `python3 -c "print(len(open(p).read()))"`.
- **`references/model-effort-bakeoff.md`** — read when benchmarking codex model×effort on a representative
  slice before committing a full translation/quality job (drive N isolated arms via the CLI, blind-adjudicate).
  Carries the durable finding: **an n=1 bake-off measures noise.** This one's "`high` won / `xhigh`
  over-reaches" conclusion did NOT survive a pre-registered replication (3 replicates per cell, blind
  cross-model judge, counterbalanced) — the tiers came out **indistinguishable on fidelity**, the only
  robust difference being **`xhigh` ≈2× slower**. So: choose the tier from standing policy (fidelity-risk
  translation pins `xhigh`) and measured latency, not from a tier-winner claim; and put accuracy
  constraints in the PROMPT, not in a higher effort tier.
