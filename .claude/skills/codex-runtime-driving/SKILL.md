---
name: codex-runtime-driving
description: Mechanics for reliably driving the codex-companion runtime and recovering its verdict — use when running codex:codex-rescue or codex-companion.mjs (task/adversarial-review/codex exec, foreground, --background, or via the rescue Agent), polling or reading its job state JSON / result, diagnosing a backgrounded or lost verdict, a dead/hung/killed worker, "No job found", a fabricated wait-state, arg-misparse, env/PATH/broker/TMPDIR/moderation/usage-limit breakage, driving long or parallel background jobs safely, confining what a job may WRITE or reasoning about its sandbox/workspace-root boundary (a `--cwd` inside a repo does not move it), sizing a prompt or a context budget against the model's real usable window, or benchmarking codex model×effort on a slice before committing a big job.
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

**`kill -0 <pid>` is only as good as the pid, and `pgrep -f` is the wrong way to get one** — for a job
launched directly as `codex exec ... > out 2> err` there is no state JSON to read `.pid` from, and
`pgrep -f codex` matches the session's own zsh wrapper, whose command line contains `export
CODEX_COMPANION_*` env assignments. Grepping the full command line therefore returns a shell that exits on
its own schedule while the real worker runs on. **Identify the process by who holds the output file open:
`lsof -t <job>.err`, keeping the pid whose `ps -p <pid> -o comm=` ends in `bin/codex`.** Measured: two
successive monitors reported "PROCESS EXITED" for a job that was alive and writing — a false completion
that reads exactly like a finished run and invites acting on an absent verdict. Corollary for any
`until ! kill -0 $PID` waiter: verify the pid identifies the worker BEFORE arming the loop, because a
waiter on the wrong pid returns promptly and looks like success.

**Having the right pid does not mean you can read liveness off it — two follow-on traps, both measured
2026-08-07 and both of which produced a confident "the job is stalled/dead" that was false.**
(1) **The holder's `etime` is NOT the job's age.** A node+codex launcher/worker pair persists and is
REUSED across runs, so a job launched two minutes ago is held by processes reporting hours of elapsed
time. Reading that as "these are stale processes, my launch never took" inverts the diagnosis. Age
tells you nothing; only the output file's growth does. (2) **Sample growth over ≥20 s, never a few
seconds.** `stat -f%z` twice around an 8-second window caught a pause between writes and read as a
stall, on a job that was in fact emitting ~5 KB/s. A dead job and a thinking one look identical over a
short window — and a KILLED launch leaves a partial `.err` that is indistinguishable by size or content
from a running one. Related: a `nohup … &` launch inside a FOREGROUND Bash tool call did not survive
that call's 2-minute timeout (observed: SIGTERM to the call, no worker holding the file afterwards) —
launch long runs with the Bash tool's own `run_in_background` instead.

Read the reference file that matches the task:

- **`references/codex-companion-runtime.md`** — read when driving a codex-rescue / `codex-companion.mjs`
  review, monitoring its state JSON or verdict, or recovering from any failure: a backgrounded/lost verdict,
  a dead or hung or harness-killed worker, "No job found", a fabricated wait-state, an arg-misparse launch
  400, an env/PATH/broker/TMPDIR/moderation breakage, or driving long/parallel `--background` jobs safely.
  **Also read it before trying to CONFINE a job's writes:** `--cwd` does not set the sandbox boundary —
  the workspace root is the enclosing repo's toplevel, so a per-launch dir under a durable root inside a
  repo still hands the job `--write` over the whole repo; and job records are keyed by that resolved root,
  so `status`/`cancel` must reuse the launch-time cwd or report "No job found" for a live job.
  Covers the reliable direct-drive patterns, the fastest `jq -r '.result.rawOutput'` verdict recovery,
  stall/hang thresholds, and the `/security-review` working-tree diff caveats.
- **`references/prompt-sizing.md`** — read BEFORE dispatching a job whose prompt is large and whose payload
  is non-Latin (vocalized Hebrew, Arabic with harakat, Devanagari, heavy CJK) or mostly JSON, **and before
  any decision that rests on how much context window is available — a size gate, a chunk size, a
  fits/does-not-fit acceptance claim — whatever the script.** Covers budgeting per SCRIPT rather than per
  byte, and how to measure a prompt's real size. Three silent traps invert the obvious method: `chars/4` is
  a Latin-prose token ratio that under-counts a diacritized script badly enough to overflow a window the
  estimate said it fit; `wc -c` counts BYTES, not characters; and `context_window` is not the budget —
  `effective_context_window_percent` and the per-call `base_instructions` preamble on the same model record
  both subtract from it silently, and no tokenizer field exists to settle which encoding to count with.
- **`references/model-effort-bakeoff.md`** — read when benchmarking codex model×effort on a representative
  slice before committing a full translation/quality job (drive N isolated arms via the CLI, blind-adjudicate).
  Carries the durable finding: **an n=1 bake-off measures noise.** This one's "`high` won / `xhigh`
  over-reaches" conclusion did NOT survive a pre-registered replication (3 replicates per cell, blind
  cross-model judge, counterbalanced) — the tiers came out **indistinguishable on fidelity**, the only
  robust difference being **`xhigh` ≈2× slower**. So: choose the tier from standing policy (fidelity-risk
  translation pins `xhigh`) and measured latency, not from a tier-winner claim; and put accuracy
  constraints in the PROMPT, not in a higher effort tier.
