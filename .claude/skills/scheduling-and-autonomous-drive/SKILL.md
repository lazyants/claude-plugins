---
name: scheduling-and-autonomous-drive
description: Choosing and configuring timers/reminders (CronCreate vs ScheduleWakeup, one-shot vs recurring, resolving a user-quoted retry clock-time against the real `date`) and holding the right posture during autonomous execution — running a `/loop` or `/goal` drive, handling a stop-hook wake or a resume boundary (scheduled-wakeup or compaction) that desyncs plan-mode state, filling async waits with non-racing parallel prep, and checking for a safe disjoint-partition split before accepting a long serial batch wait.
---

# Scheduling and autonomous-drive posture

## Timer tool choice: CronCreate vs ScheduleWakeup

For an interactive-session "wake me at/in X" request, pick by wait length and context:

- **`ScheduleWakeup`** is scoped to `/loop` dynamic-mode pacing and is **clamped to `[60, 3600]` seconds (max 1 hour)**. It is not available outside a `/loop` context. Unusable for any multi-hour "remind me" ask.
- **`CronCreate`** is the right tool for a plain interactive-session "fire once at time X" ask:
  - Standard 5-field cron in **LOCAL time**.
  - `recurring: false` to auto-delete after firing once.
  - It is **session-only** (in-memory; dies if the session ends). Mention this to the user when the wait is long enough that a session restart is plausible.
  - When the user gave a **specific** target, write the fire-time as an exact computed cron pin (`"2 6 11 7 *"` style), not an approximation. The tool's own "avoid :00/:30" guidance is for approximate/recurring requests, NOT a user-stated precise duration.

### Resolve a quoted retry clock-time against real `date` first

When a user echoes a clock-time that came from an older error message (e.g. a codex/rate-limit "try again at 4:30"), do not take the quoted time at face value:

- A rate-limit error's quoted retry-time drifts as real time passes, and the user may be glancing at a stale instance of it.
- A bare `"4:30"`-shaped user message may mean a **duration from now** ("target is in 4.5 hours"), not that literal clock value.
- Get the actual current time first: `date "+%Y-%m-%d %H:%M:%S %Z"` (plain Bash in the main loop — Workflow scripts can't call `Date.now()`/`new Date()`). Then compute the real target clock-time from whichever the user meant. Don't guess.

## Plan-mode state desyncs across resume boundaries

A resume boundary — a `ScheduleWakeup`-triggered turn OR a `/compact` — re-enters the conversation as a fresh top-level invocation, and that boundary does NOT preserve plan-mode's tool-gating state the way a normal multi-tool-call turn does. Two failure shapes:

**Silent auto-EXIT (scheduled-wakeup case).** A wakeup firing mid-review can open the next turn with an unprompted `## Exited Plan Mode` — even though `ExitPlanMode` was never called, no plan was approved, and the review loop is still mid-flight. No error, no explanation.
- Do NOT treat "plan mode exited" as a signal that review/approval happened — it didn't. The codex-hardening loop still governs: keep revising and re-reviewing until the reviewer comes back clean, regardless of what plan-mode's UI state says.
- Do NOT try to force a return to plan mode mid-loop (re-calling `EnterPlanMode`). Finish the review loop on its merits and proceed once it's genuinely clean. `ExitPlanMode` may no longer be meaningful/callable at that point — that's fine; it was never the real gate. The reviewer coming back clean is.
- If a task genuinely needs plan-mode's edit-BLOCKING guarantee to survive a long wait (not just the review-and-revise ritual), don't rely on `ScheduleWakeup` to bridge it: either poll inline within the same turn (accept the cost), or accept the guarantee may lapse and re-verify nothing was edited out from under the plan before resuming.

**Stale re-INJECTED banner (compaction case).** After `/compact` mid-autonomous-execution, the next turn can open with "Plan mode is active … you MUST NOT make any edits," pointing at an already-executed plan file and contradicting a standing autonomous `/goal`. Tells that it's spurious:
- (a) it names a stale/wrong plan file (the original, long-executed one, not the live plan);
- (b) moments before compaction the session was demonstrably EXECUTING (editing files, launching jobs — impossible under real plan mode);
- (c) it contradicts an explicit later user directive.

Resolution: don't bulldoze the "MUST NOT edit" by editing anyway. Clear it via the sanctioned `ExitPlanMode` — first write the real continuation plan INTO the banner's named file so the approval shows the right content — then resume. Root cause is the same as the wakeup case: trust the actual work state plus the standing user directive over the banner.

## Autonomous stop-hook: keep every turn productive, never "hold"

Under an autonomous `/goal …` (finish to the end without asking) combined with a Stop-hook that re-fires until all requirements are met: a stop-hook gates on whether the DELIVERABLE is complete, not on whether a background task is pending. So ending a turn with "holding for background task X to resume me" reads as **stopping prematurely** — it trips the hook and draws a "stuck?" from the user, even when the blocker is real.

When genuinely blocked on async work (a long codex/background run, a monitor that will resume you), do NOT end the turn on "holding." Instead:

- Maximize productive **non-racing parallel prep** each turn: build + unit-test the downstream tooling, dry-run the next stage on PARTIAL data (a single-item smoke, a partial index build validates the whole chain before the full run finishes), file follow-ups, write the durable verification record.
- Keep a real background monitor pending so the harness resumes you on completion — that IS forward motion.
- When there truly is nothing non-racing left, say so **concretely**: what is running, what it's blocked on, rough ETA — not a bare "holding." Concrete-blocked ≠ stopped.

## Check for a safe parallel split BEFORE accepting a long serial wait

Before accepting a long SERIAL wait on a batch job (and recommending the user just let it finish), check for a SAFE disjoint-partition parallelization. It's usually available and needs **zero new correctness-critical code** when both hold:

1. **Each item is independent** (per-item input → per-item output file), and
2. **An existing, tested aggregate/replay path** already assembles cached per-item outputs with no model calls (e.g. a `--dry <fixture>` mode that reads each cached per-item output and does the full parse + validate + aggregate). The correctness-critical step (validation) stays unchanged, tested code.

When both hold, parallelize by **disjoint partition** (no cross-process contention):
- Kill the serial run — its finished items are already cached on disk.
- Launch N shards, each owning a disjoint slice. Match the proven concurrency (e.g. the same pool size an earlier round already ran safely); don't exceed what's been shown safe on a must-not-fail run.
- Aggregate via the EXISTING dry/replay path.

**Killing the serial run is safe IF the driver has per-attempt isolation:** each attempt runs in its own throwaway dir with atomic promote only on `completed`. Then a killed job's zombie can only write into its abandoned dir, never canonical output; and an in-flight item is **adopted, not re-run** if the driver binds restart-adoption to the input hash.

**Zero-edit lock parameterization** — to give each shard its own single-writer lock without touching a correctness-critical source file, inject the module global before calling main:

```
python3 -c "import mod as m; m.LEDGER_LOCK='…shardN.lock'; m.main([...])"
```

A function that reads a module-global at call time picks up the override. Beats editing a must-not-fail file mid-flight just to add a `--lock` arg.

**The tell to catch yourself on:** citing "must-not-fail → don't gamble the healthy run" to justify NOT CHECKING whether the wait was avoidable. The safety framing belongs only to the NAIVE approach (resharding that re-runs completed items, or bolting new code onto a must-not-fail script) — it must not become an excuse to skip the investigation. A long serial wait on an independent-per-item job is a signal to investigate a disjoint-partition split FIRST, then decide.
